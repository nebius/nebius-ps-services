from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from nebius_cxcli.destroy_cloud import DestroyCloud


def resource(identifier, parent, **kwargs):
    return {"metadata": {"id": identifier, "parent_id": parent}, **kwargs}


def attachment(identifier):
    return {
        "filesystems": [{"existing_filesystem": {"id": identifier}, "mount_tag": "arbitrary-tag"}]
    }


class InventoryCloud(DestroyCloud):
    def __init__(self):
        self.project_id = "project"
        self.calls = []
        self.cluster = resource("cluster", "project")
        self.groups = [resource("group", "cluster", spec={"template": attachment("fs")})]
        self.instances = [resource("worker", "project", spec=attachment("fs"))]
        self.instances[0]["metadata"]["labels"] = {
            "mk8s-cluster-id": "cluster",
            "mk8s-node-group-id": "group",
        }
        self.filesystem = resource(
            "fs", "project", spec={}, status={"read_write_attachments": ["worker"]}
        )
        self.other_groups = []
        self.disks = []
        self.gpus = {}

    def get(self, kind, identifier, **kwargs):
        self.calls.append(("get", kind, identifier))
        if kind == "disk":
            return copy.deepcopy(
                next((d for d in self.disks if d["metadata"]["id"] == identifier), None)
            )
        if kind == "gpu_cluster":
            return copy.deepcopy(self.gpus[identifier])
        return copy.deepcopy(self.cluster if kind == "cluster" else self.filesystem)

    def list(self, kind, parent):
        self.calls.append(("list", kind, parent))
        if kind == "disk":
            return copy.deepcopy(self.disks)
        if kind == "instance":
            return copy.deepcopy(self.instances)
        if kind == "node_group":
            return copy.deepcopy(self.groups if parent == "cluster" else self.other_groups)
        return [self.cluster, resource("other", "project")]


def test_inventory_includes_zero_node_templates_arbitrary_tags_and_actual_workers():
    cloud = InventoryCloud()
    cloud.groups.append(resource("zero", "cluster", spec={"template": attachment("zero-fs")}))
    cloud.instances[0]["spec"]["filesystems"].append({"existing_filesystem": {"id": "worker-fs"}})
    original = cloud.get

    def get(kind, identifier, **kwargs):
        if kind == "filesystem":
            return resource(identifier, "project", spec={}, status={})
        return original(kind, identifier, **kwargs)

    cloud.get = get
    inventory = cloud.inventory("cluster", delete_sfs=False)
    assert inventory["filesystem_ids"] == ["fs", "worker-fs", "zero-fs"]
    assert inventory["node_group_ids"] == ["group", "zero"]


def test_thousand_workers_use_one_bulk_instance_list():
    cloud = InventoryCloud()
    worker = cloud.instances[0]
    cloud.instances = [copy.deepcopy(worker) for _ in range(1000)]
    for i, instance in enumerate(cloud.instances):
        instance["metadata"]["id"] = f"worker-{i}"
    inventory = cloud.inventory("cluster", delete_sfs=False)
    assert len(inventory["worker_ids"]) == 1000
    assert cloud.calls.count(("list", "instance", "project")) == 1
    assert not any(call[1] == "instance" and call[0] == "get" for call in cloud.calls)


@pytest.mark.parametrize(
    "change",
    [
        "foreign-project",
        "wrong-group",
        "missing-label",
        "template-shared",
        "stopped-instance-shared",
        "unknown-attachment",
        "protected",
    ],
)
def test_unproven_or_shared_storage_blocks_preflight(change):
    cloud = InventoryCloud()
    if change == "foreign-project":
        cloud.cluster["metadata"]["parent_id"] = "other-project"
    elif change == "wrong-group":
        cloud.instances[0]["metadata"]["labels"]["mk8s-node-group-id"] = "other-group"
    elif change == "missing-label":
        cloud.instances[0]["metadata"]["labels"].pop("mk8s-cluster-id")
    elif change == "template-shared":
        cloud.other_groups = [resource("other-group", "other", spec={"template": attachment("fs")})]
    elif change == "stopped-instance-shared":
        cloud.instances.append(
            resource("stopped", "project", spec=attachment("fs"), status={"state": "STOPPED"})
        )
    elif change == "unknown-attachment":
        cloud.filesystem["status"]["read_only_attachments"] = ["unidentified"]
    else:
        cloud.filesystem["spec"]["forbid_deletion"] = True
    with pytest.raises(RuntimeError):
        cloud.inventory("cluster", delete_sfs=True)


def test_default_preserves_shared_protected_filesystem_and_allows_worker_turnover():
    cloud = InventoryCloud()
    frozen = cloud.inventory("cluster", delete_sfs=False)
    cloud.filesystem["spec"]["forbid_deletion"] = True
    cloud.filesystem["status"]["read_write_attachments"].append("other-instance")
    cloud.instances[0]["metadata"]["id"] = "replacement-worker"
    cloud.verify_scope(frozen, delete_sfs=False)


def test_pagination_completeness_and_repeated_token_rejection():
    cloud = object.__new__(DestroyCloud)
    tokens = []

    def list_request(request, **kwargs):
        tokens.append(request.page_token)
        result = {"items": [resource("a" if not request.page_token else "b", "project")]}
        if not request.page_token:
            result["next_page_token"] = "next"
        return SimpleNamespace(wait=lambda: result)

    cloud.clients = {"instance": SimpleNamespace(list=list_request)}
    assert [row["metadata"]["id"] for row in cloud.list("instance", "project")] == ["a", "b"]
    assert tokens == ["", "next"]
    cloud.clients["instance"].list = lambda *a, **kw: SimpleNamespace(
        wait=lambda: {"next_page_token": "same"}
    )
    with pytest.raises(RuntimeError, match="pagination"):
        cloud.list("instance", "project")


@pytest.mark.parametrize(
    "kind,request_type",
    [
        ("cluster", "nebius.mk8s.v1.DeleteClusterRequest"),
        ("filesystem", "nebius.compute.v1.DeleteFilesystemRequest"),
        ("disk", "nebius.compute.v1.DeleteDiskRequest"),
        ("gpu_cluster", "nebius.compute.v1.DeleteGpuClusterRequest"),
    ],
)
def test_recovery_uses_real_sdk_direct_operation_messages(kind, request_type):
    from nebius.api.nebius.common.v1 import ListOperationsResponse, Operation
    from nebius.base.protos.json_format import message_to_dict

    from nebius_cxcli.nebius_api_helpers import sdk_parse_message

    cloud = object.__new__(DestroyCloud)
    operation = sdk_parse_message(
        Operation,
        {
            "id": "op",
            "resource_id": "cluster",
            "request_headers": {"x-idempotency-key": {"values": ["approved-key"]}},
            "request": {
                "@type": f"type.googleapis.com/{request_type}",
                "id": "cluster",
            },
        },
    )
    response = ListOperationsResponse(operations=[operation])
    client = SimpleNamespace(list=lambda *a, **kw: SimpleNamespace(wait=lambda: response))
    cloud.clients = {kind: SimpleNamespace(operation_service=lambda: client)}
    assert message_to_dict(operation)["resourceId"] == "cluster"
    assert cloud.recover_operation(kind, "cluster", "approved-key") == "op"
    with pytest.raises(RuntimeError, match="acceptance is unknown"):
        cloud.recover_operation(kind, "cluster", "different-key")


def test_absence_checks_frozen_worker_ids_even_after_label_loss():
    cloud = object.__new__(DestroyCloud)
    cloud.project_id = "project"
    cloud.get = lambda *a, **kw: None
    cloud.list = lambda *a: [resource("original-worker", "project", spec={})]
    with pytest.raises(RuntimeError, match="worker remains"):
        cloud.verify_cluster_absent(
            {
                "cluster_id": "cluster",
                "node_group_ids": ["group"],
                "worker_ids": ["original-worker"],
            }
        )


def test_vm_nfs_uses_exact_cloud_instance_disks_and_allocations_without_export_probe():
    cloud = InventoryCloud()
    cloud.instances = [
        resource(
            "nfs",
            "project",
            spec={
                "secondary_disks": [
                    {
                        "existing_disk": {"id": "disk"},
                        "device_id": "data",
                        "attach_mode": "READ_WRITE",
                    }
                ]
            },
            status={
                "disk_attachments": [{"id": "disk"}],
                "network_interfaces": [
                    {"ip_address": {"address": "10.0.0.8", "allocation_id": "allocation"}}
                ],
            },
        )
    ]
    calls = []

    def get(kind, identifier):
        calls.append((kind, identifier))
        return cloud.instances[0] if kind == "instance" else resource(identifier, "project")

    cloud.get = get
    frozen = cloud.discover_vm_nfs({"enabled": True, "server": "10.0.0.8", "path": "/export/home"})
    assert frozen["resources"] == {
        "instance": ["nfs"],
        "disk": ["disk"],
        "allocation": ["allocation"],
    }
    assert calls == [("instance", "nfs"), ("disk", "disk"), ("allocation", "allocation")]
    cloud.instances[0]["status"]["disk_attachments"] = []
    with pytest.raises(RuntimeError, match="disk identity"):
        cloud.verify_vm_nfs(frozen)


@pytest.mark.parametrize("kind", ["cluster", "filesystem", "disk", "gpu_cluster"])
def test_delete_constructs_actual_sdk_request_with_durable_idempotency_header(kind, monkeypatch):
    from nebius.aio.idempotency import ensure_key_in_metadata
    from nebius.aio.request import Request
    from nebius.api.nebius.compute.v1 import (
        DiskServiceClient,
        FilesystemServiceClient,
        GpuClusterServiceClient,
    )
    from nebius.api.nebius.mk8s.v1 import ClusterServiceClient

    observed = []

    def wait(request):
        # Stop at the network boundary, after the real generated client and
        # Request constructor have checked all arguments.
        assert request._input.id == "resource-id"
        assert request._retries == 0
        ensure_key_in_metadata(request._input_metadata)
        assert request._input_metadata["x-idempotency-key"] == ["persisted-key"]
        observed.append(request)
        return SimpleNamespace(id="operation-id", resource_id="resource-id")

    monkeypatch.setattr(Request, "wait", wait)
    cloud = object.__new__(DestroyCloud)
    cloud.assert_held = lambda: None
    client = {
        "cluster": ClusterServiceClient,
        "filesystem": FilesystemServiceClient,
        "disk": DiskServiceClient,
        "gpu_cluster": GpuClusterServiceClient,
    }[kind]
    cloud.clients = {kind: client(object())}
    assert cloud.submit_delete(kind, "resource-id", "persisted-key") == "operation-id"
    assert len(observed) == 1
