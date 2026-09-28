from __future__ import annotations

import copy

import pytest

from destroy_fakes import Cloud, receipt
from nebius_cxcli.destroy import receipt_confirmation, run_destroy
from nebius_cxcli.destroy_cloud import TerminalDestroyOperationError
from nebius_cxcli.destroy_resources import (
    CSI_CLUSTER_LABEL,
    check_remaining_references,
    pvc_identity,
    verify_pvc_postconditions,
)
from test_destroy_inventory import InventoryCloud, resource
from test_destroy_storage import change, plan


def disk(identifier="disk-a", project="project", cluster="cluster", worker=None):
    result = resource(identifier, project, spec={"size_gibibytes": 30}, status={})
    result["metadata"]["labels"] = {
        CSI_CLUSTER_LABEL: cluster,
        "CSIVolumeName": f"pvc-{identifier}",
        "kubernetes.io/created-for/pv/name": f"pvc-{identifier}",
        "kubernetes.io/created-for/pvc/name": "metrics",
        "kubernetes.io/created-for/pvc/namespace": "monitoring",
    }
    if worker:
        result["status"]["read_write_attachment"] = worker
    return result


def populated():
    cloud = InventoryCloud()
    cloud.disks = [disk(worker="worker"), disk("disk-b")]
    cloud.groups[0]["spec"]["template"]["gpu_cluster"] = {"id": "gpu"}
    cloud.instances[0]["spec"]["gpu_cluster"] = {"id": "gpu"}
    cloud.instances[0]["spec"]["secondary_disks"] = [{"existing_disk": {"id": "disk-a"}}]
    cloud.gpus["gpu"] = resource("gpu", "project", spec={}, status={"instances": ["worker"]})
    return cloud


def test_ethernet_worker_empty_gpu_cluster_does_not_block_storage_inventory():
    cloud = InventoryCloud()
    cloud.instances[0]["spec"]["gpu_cluster"] = {}
    cloud.groups[0]["spec"]["template"]["gpu_cluster"] = {}
    value = cloud.inventory("cluster", delete_sfs=False)
    assert value["gpu_cluster_ids"] == []


@pytest.mark.parametrize("value", [{"id": ""}, {"id": None}, {"unknown": "gpu"}, [], "gpu"])
def test_malformed_nonempty_gpu_cluster_still_rejected(value):
    from nebius_cxcli.destroy_resources import gpu_id

    with pytest.raises(RuntimeError, match="incomplete"):
        gpu_id({"gpu_cluster": value})


def test_inventory_discovers_attached_detached_and_zero_node_gpu_resources():
    cloud = populated()
    cloud.groups.append(
        resource("zero", "cluster", spec={"template": {"gpu_cluster": {"id": "zero-gpu"}}})
    )
    cloud.gpus["zero-gpu"] = resource("zero-gpu", "project", spec={}, status={})
    cloud.gpus["owned-unused"] = resource("owned-unused", "project", spec={}, status={})
    value = cloud.inventory("cluster", delete_sfs=False, managed_gpu_ids=["owned-unused"])
    assert value["pvc_disk_ids"] == ["disk-a", "disk-b"]
    assert value["gpu_cluster_ids"] == ["gpu", "owned-unused", "zero-gpu"]
    assert value["pvc_disks"]["disk-a"]["size_bytes"] == 30 * 1024**3
    assert cloud.calls.count(("list", "instance", "project")) == 1
    assert cloud.calls.count(("list", "disk", "project")) == 1
    assert cloud.calls.count(("list", "cluster", "project")) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "disk-project",
        "disk-label",
        "disk-pv",
        "disk-protected",
        "disk-lock",
        "disk-attachment",
        "disk-stopped-ref",
        "gpu-member",
        "gpu-stopped-ref",
        "gpu-template",
        "gpu-project",
        "unknown-secondary",
    ],
)
def test_unsafe_dependent_resources_block_inventory(fault):
    cloud = populated()
    if fault == "disk-project":
        cloud.disks[0]["metadata"]["parent_id"] = "other"
    elif fault == "disk-label":
        del cloud.disks[0]["metadata"]["labels"]["kubernetes.io/created-for/pvc/namespace"]
    elif fault == "disk-pv":
        cloud.disks[0]["metadata"]["labels"]["CSIVolumeName"] = "different"
    elif fault == "disk-protected":
        cloud.disks[0]["spec"]["forbid_deletion"] = True
    elif fault == "disk-lock":
        cloud.disks[0]["status"]["lock_state"] = {"snapshots": ["snapshot"]}
    elif fault == "disk-attachment":
        cloud.disks[0]["status"]["read_only_attachments"] = ["outsider"]
    elif fault == "disk-stopped-ref":
        cloud.instances.append(
            resource(
                "stopped",
                "project",
                spec={"secondary_disks": [{"existing_disk": {"id": "disk-a"}}]},
            )
        )
    elif fault == "gpu-member":
        cloud.gpus["gpu"]["status"]["instances"].append("outsider")
    elif fault == "gpu-stopped-ref":
        cloud.instances.append(resource("stopped", "project", spec={"gpu_cluster": {"id": "gpu"}}))
    elif fault == "gpu-template":
        cloud.other_groups.append(
            resource("foreign", "other", spec={"template": {"gpu_cluster": {"id": "gpu"}}})
        )
    elif fault == "gpu-project":
        cloud.gpus["gpu"]["metadata"]["parent_id"] = "other"
    else:
        cloud.disks[0]["metadata"]["labels"] = {}
    with pytest.raises(RuntimeError):
        cloud.inventory("cluster", delete_sfs=False)


def test_preserve_disks_allows_protection_and_excludes_boot_and_managed_disks():
    cloud = populated()
    cloud.disks[0]["spec"]["forbid_deletion"] = True
    cloud.disks.extend([disk("boot"), disk("managed")])
    cloud.instances[0]["spec"]["boot_disk"] = {"existing_disk": {"id": "boot"}}
    cloud.disks[-1]["status"]["managed_by"] = "worker"
    value = cloud.inventory("cluster", delete_sfs=False, preserve_pvc_disks=True)
    assert value["pvc_disk_ids"] == ["disk-a", "disk-b"]


@pytest.mark.parametrize("field", ["read_write_attachment", "read_only_attachments"])
def test_unknown_disk_attachment_status_cannot_escape_discovery(field):
    cloud = populated()
    cloud.instances[0]["spec"].pop("secondary_disks")
    cloud.disks[0]["metadata"]["labels"] = {}
    cloud.disks[0]["status"] = {field: "worker" if field == "read_write_attachment" else ["worker"]}
    with pytest.raises(RuntimeError, match="unproven PVC ownership"):
        cloud.inventory("cluster", delete_sfs=False)
    value = cloud.inventory("cluster", delete_sfs=False, preserve_pvc_disks=True)
    assert value["unclassified_disk_ids"] == ["disk-a"]


def test_scope_allows_worker_turnover_and_disk_absence_but_no_new_disks():
    cloud = populated()
    frozen = cloud.inventory("cluster", delete_sfs=False)
    cloud.instances[0]["metadata"]["id"] = "replacement"
    cloud.gpus["gpu"]["status"]["instances"] = ["replacement"]
    cloud.disks[0]["status"]["read_write_attachment"] = "replacement"
    cloud.disks.pop()
    cloud.verify_scope(frozen, delete_sfs=False)
    cloud.disks.append(disk("new"))
    with pytest.raises(RuntimeError, match="inventory changed"):
        cloud.verify_scope(frozen, delete_sfs=False)


def test_excluded_boot_and_managed_disks_are_not_reported_as_unapproved_pvcs():
    cloud = populated()
    cloud.disks.extend([disk("boot"), disk("managed")])
    cloud.instances.append(
        resource("outside", "project", spec={"boot_disk": {"existing_disk": {"id": "boot"}}})
    )
    cloud.disks[-1]["status"]["managed_by"] = "worker"
    frozen = cloud.inventory("cluster", delete_sfs=False)
    assert frozen["excluded_disk_ids"] == ["boot", "managed"]
    cloud.disks = [d for d in cloud.disks if d["metadata"]["id"] in frozen["excluded_disk_ids"]]
    verify_pvc_postconditions(cloud, frozen, False)


@pytest.mark.parametrize("changed", ["boot", "managed", "labels"])
def test_missing_candidate_requires_actual_absence_before_cluster_delete(changed):
    cloud = populated()
    frozen = cloud.inventory("cluster", delete_sfs=False)
    if changed == "boot":
        cloud.instances[0]["spec"]["boot_disk"] = {"existing_disk": {"id": "disk-b"}}
    elif changed == "managed":
        cloud.disks[1]["status"]["managed_by"] = "worker"
    else:
        cloud.disks[1]["metadata"]["labels"] = {}
    with pytest.raises(RuntimeError, match="changed classification"):
        cloud.verify_scope(frozen, delete_sfs=False)


@pytest.mark.parametrize("kind", ["gpu", "disk"])
def test_remaining_configuration_reference_blocks_cleanup(kind):
    value = populated().inventory("cluster", delete_sfs=False)
    with pytest.raises(RuntimeError, match="Remaining configuration"):
        check_remaining_references(
            {"other": {"id": "gpu" if kind == "gpu" else "disk-a"}}, value, False
        )


def execution_fixture(count=2, preserve=False, delete_sfs=False):
    cloud = Cloud()
    inventory = copy.deepcopy(receipt().approved["inventory"])
    inventory["gpu_cluster_ids"] = ["gpu_cluster-a"]
    inventory["group_gpu_clusters"] = {"group-a": "gpu_cluster-a"}
    cloud.data["gpu_cluster-a"] = resource("gpu_cluster-a", "project-a", spec={}, status={})
    for i in range(count):
        identifier = f"disk-{i:04d}"
        value = disk(identifier, "project-a", "mk8scluster-a")
        cloud.data[identifier] = value
        inventory["pvc_disks"][identifier] = pvc_identity(value, "project-a", "mk8scluster-a")
    inventory["pvc_disk_ids"] = sorted(inventory["pvc_disks"])
    cloud.present.update(cloud.data)
    cloud.inventory = lambda *a, **kw: copy.deepcopy(inventory)
    value = receipt(inventory=inventory, preserve_pvc_disks=preserve, delete_sfs=delete_sfs)
    return cloud, value


def execute(cloud, value, saved, **overrides):
    args = dict(
        receipt=value,
        cloud=cloud,
        save=saved.append,
        approval_mode="interactive",
        confirmation=receipt_confirmation(value),
        reconcile=lambda: cloud.events.append("reconcile"),
        publish=lambda: cloud.events.append("publish"),
        clear_baseline=lambda: None,
    )
    args.update(overrides)
    return run_destroy(**args)


@pytest.mark.parametrize("preserve", [True, False])
@pytest.mark.parametrize("delete_sfs", [True, False])
@pytest.mark.parametrize("ownership", ["managed", "onboarded"])
def test_default_sdk_cleanup_and_independent_dispositions(preserve, delete_sfs, ownership):
    cloud, value = execution_fixture(preserve=preserve, delete_sfs=delete_sfs)
    value = receipt(
        **{k: v for k, v in value.approved.items() if k not in {"schema", "ownership"}},
        ownership=ownership,
    )
    result = execute(cloud, value, [])
    assert result.status == "complete"
    kinds = [kind for kind, *_ in cloud.calls]
    assert kinds == [
        "cluster",
        "gpu_cluster",
        *([] if preserve else ["disk", "disk"]),
        *(["filesystem"] if delete_sfs else []),
    ]
    assert ("disk-0000" in cloud.present) == preserve
    assert ("filesystem-a" in cloud.present) != delete_sfs
    assert "gpu_clusters_absent" in result.checkpoints


def test_thousand_disk_queue_is_bounded_and_receipt_writes_precede_effects(monkeypatch):
    cloud, value = execution_fixture(count=1000)
    monkeypatch.setattr("nebius_cxcli.destroy.time.sleep", lambda _: None)

    class LatestReceipt(list):
        def append(self, value):
            self[:] = [value]

    saved, inflight = LatestReceipt(), set()
    maximum = 0
    submit, poll = cloud.submit_delete, cloud.poll_delete_once

    def track_submit(kind, identifier, key):
        nonlocal maximum
        assert saved[-1].requests[f"{kind}:{identifier}"]["key"] == key
        if kind == "disk":
            inflight.add(identifier)
            maximum = max(maximum, len(inflight))
        return submit(kind, identifier, key)

    def track_poll(kind, identifier, operation):
        assert saved[-1].requests[f"{kind}:{identifier}"]["operation_id"] == operation
        inflight.remove(identifier)
        return poll(kind, identifier, operation)

    cloud.submit_delete, cloud.poll_delete_once = track_submit, track_poll
    assert execute(cloud, value, saved).status == "complete"
    assert maximum == 8 and not inflight
    assert len([c for c in cloud.calls if c[0] == "cluster"]) == 1


@pytest.mark.parametrize("kind", ["gpu_cluster", "disk"])
@pytest.mark.parametrize("failure", ["unknown-acceptance", "poll", "terminal"])
def test_resource_failures_resume_without_blind_replay(kind, failure):
    cloud, value = execution_fixture()
    saved = []
    submit, poll, once = cloud.submit_delete, cloud.poll_delete, cloud.poll_delete_once

    def failing_submit(k, identifier, key):
        op = submit(k, identifier, key)
        if k == kind and failure == "unknown-acceptance":
            raise TimeoutError("lost acceptance")
        return op

    def failing_poll(k, identifier, op):
        if k == kind:
            if failure == "terminal":
                raise TerminalDestroyOperationError("failed")
            if failure == "poll":
                raise TimeoutError("poll")
        return poll(k, identifier, op)

    cloud.submit_delete, cloud.poll_delete = failing_submit, failing_poll
    cloud.poll_delete_once = lambda *a: failing_poll(*a) or True
    with pytest.raises((TimeoutError, TerminalDestroyOperationError)):
        execute(cloud, value, saved)
    cloud.submit_delete, cloud.poll_delete, cloud.poll_delete_once = submit, poll, once
    result = execute(cloud, saved[-1], saved)
    assert result.status == "complete"
    ids = [identifier for k, identifier, _ in cloud.calls if k == kind]
    assert len(ids) == len(set(ids)) + (failure == "terminal")
    assert cloud.events.count("submit:cluster") == 1


@pytest.mark.parametrize("changed", ["boot", "managed", "labels"])
def test_terminal_cluster_retry_rechecks_missing_pvc_classification(changed):
    cloud, value = execution_fixture()
    saved = []

    def failed(*args):
        raise TerminalDestroyOperationError("failed cluster deletion")

    cloud.poll_delete = failed
    with pytest.raises(TerminalDestroyOperationError):
        execute(cloud, value, saved)
    live = copy.deepcopy(value.approved["inventory"])
    identifier = live["pvc_disk_ids"].pop()
    del live["pvc_disks"][identifier]
    if changed in {"boot", "managed"}:
        live["excluded_disk_ids"].append(identifier)
    else:
        cloud.data[identifier]["metadata"]["labels"] = {}
    cloud.inventory = lambda *args, **kwargs: live
    with pytest.raises(RuntimeError, match="changed classification"):
        execute(cloud, saved[-1], saved)
    assert len(cloud.calls) == 1 and cloud.calls[0][0] == "cluster"


def test_gpu_checkpoint_is_revalidated_before_publication():
    cloud, value = execution_fixture()
    saved = []

    def fail():
        raise RuntimeError("reconcile failed")

    with pytest.raises(RuntimeError, match="reconcile failed"):
        execute(cloud, value, saved, reconcile=fail)
    cloud.present.add("gpu_cluster-a")
    with pytest.raises(RuntimeError, match="GPU cluster is not absent"):
        execute(cloud, saved[-1], saved)
    assert "publish" not in cloud.events


def test_gpu_references_are_rechecked_after_waiting_for_another_gpu():
    cloud, value = execution_fixture()
    inventory = copy.deepcopy(value.approved["inventory"])
    inventory["gpu_cluster_ids"].append("gpu_cluster-b")
    cloud.data["gpu_cluster-b"] = resource("gpu_cluster-b", "project-a", spec={}, status={})
    cloud.present.add("gpu_cluster-b")
    value = receipt(inventory=inventory)
    original = cloud.list

    def listed(kind, parent):
        if kind == "cluster" and "gpu_cluster-a" not in cloud.present:
            return [resource("other-cluster", "project-a")]
        if kind == "node_group" and parent == "other-cluster":
            return [
                resource(
                    "other-group",
                    parent,
                    spec={"template": {"gpu_cluster": {"id": "gpu_cluster-b"}}},
                )
            ]
        return original(kind, parent)

    cloud.list = listed
    with pytest.raises(RuntimeError, match="another cluster template"):
        execute(cloud, value, [])
    assert not any(c[1] == "gpu_cluster-b" for c in cloud.calls)


def test_unapproved_late_disk_requires_separate_resolution():
    cloud, value = execution_fixture()
    saved = []
    cloud.data["disk-new"] = disk("disk-new", "project-a", "mk8scluster-a")
    cloud.present.add("disk-new")
    with pytest.raises(RuntimeError, match="separate cleanup.*disk-new"):
        execute(cloud, value, saved)
    assert not any(c[1] == "disk-new" for c in cloud.calls)
    cloud.present.remove("disk-new")
    assert execute(cloud, saved[-1], saved).status == "complete"


def test_gpu_is_sdk_owned_and_terraform_may_only_reconcile_absence():
    from nebius_cxcli.destroy_generation import freeze_terraform, validate_reconciliation

    value = plan()
    gpu = change(
        "module.cluster_a.nebius_compute_v1_gpu_cluster.this",
        "nebius_compute_v1_gpu_cluster",
        "gpu",
    )
    value["resource_changes"].append(gpu)
    inventory = {**receipt().approved["inventory"], "gpu_cluster_ids": ["gpu"]}
    frozen = freeze_terraform(
        value,
        inventory=inventory,
        delete_sfs=False,
        module_names=["cluster_a"],
        managed_cluster=True,
    )
    assert frozen["deletes"][gpu["address"]]["sdk"] is True
    post = copy.deepcopy(value)
    cluster = post["resource_changes"].pop(0)
    with pytest.raises(RuntimeError, match="ancillary"):
        validate_reconciliation(post, frozen)
    removed_gpu = post["resource_changes"].pop()
    post["resource_drift"] = [cluster, removed_gpu]
    validate_reconciliation(post, frozen)
    inventory["gpu_cluster_ids"] = ["other"]
    with pytest.raises(RuntimeError, match="unapproved cloud identity"):
        freeze_terraform(
            value,
            inventory=inventory,
            delete_sfs=False,
            module_names=["cluster_a"],
            managed_cluster=True,
        )


def test_ancillary_root_iam_requires_exact_renderer_owned_instance():
    from nebius_cxcli.destroy_generation import freeze_terraform

    value = plan()
    kind = "nebius_iam_v1_group"
    address = f'{kind}.cluster_a_soperator_observability["unrelated"]'
    value["resource_changes"].append(change(address, kind, "unrelated-group"))
    kwargs = dict(
        inventory=receipt().approved["inventory"],
        delete_sfs=False,
        module_names=["cluster_a"],
        managed_cluster=True,
    )
    with pytest.raises(RuntimeError, match="unrelated managed resource"):
        freeze_terraform(
            value,
            **kwargs,
            ancillary_addresses=frozenset({f'{kind}.cluster_a_soperator_observability["worker"]'}),
        )
    frozen = freeze_terraform(value, **kwargs, ancillary_addresses=frozenset({address}))
    assert frozen["deletes"][address]["id"] == "unrelated-group"
