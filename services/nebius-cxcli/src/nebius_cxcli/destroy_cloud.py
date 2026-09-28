"""Cloud-only inventory and bounded SDK operations for whole-cluster retirement."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .nebius_api_helpers import bounded_nebius_request_kwargs, sdk_message_to_mapping
from .sdk_auth import init_nebius_sdk


class TerminalDestroyOperationError(RuntimeError):
    """The provider conclusively rejected or failed a recorded operation."""


def metadata(value: Mapping[str, Any]) -> Mapping[str, Any]:
    result = value.get("metadata")
    if not isinstance(result, Mapping) or not result.get("id"):
        raise RuntimeError("Destroy cloud resource identity is incomplete")
    return result


def filesystem_ids(value: Mapping[str, Any]) -> set[str]:
    attachments = value.get("filesystems", [])
    if not isinstance(attachments, list):
        raise RuntimeError("Destroy filesystem attachment inventory is incomplete")
    result = set()
    for attachment in attachments:
        filesystem = attachment.get("existing_filesystem", {})
        identifier = filesystem.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise RuntimeError("Destroy filesystem attachment has no immutable ID")
        result.add(identifier)
    return result


def worker_group(
    instance: Mapping[str, Any], project_id: str, cluster_id: str, groups: set[str]
) -> str | None:
    identity = metadata(instance)
    labels = identity.get("labels", {})
    if not isinstance(labels, Mapping):
        raise RuntimeError("Destroy instance labels are malformed")
    cluster = labels.get("mk8s-cluster-id")
    group = labels.get("mk8s-node-group-id")
    if cluster != cluster_id and group not in groups:
        return None
    if identity.get("parent_id") != project_id or cluster != cluster_id or group not in groups:
        raise RuntimeError("Destroy worker project/cluster/node-group ownership is ambiguous")
    return str(group)


class DestroyCloud:
    def __init__(
        self,
        project_id: str,
        *,
        sdk: Any = None,
        assert_held: Callable[[], object] = lambda: None,
        progress: Callable[[str], object] = lambda _message: None,
    ) -> None:
        from nebius.api.nebius.compute.v1 import (
            DiskServiceClient,
            FilesystemServiceClient,
            GpuClusterServiceClient,
            InstanceServiceClient,
        )
        from nebius.api.nebius.mk8s.v1 import ClusterServiceClient, NodeGroupServiceClient
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient

        self.project_id = project_id
        self.sdk = (
            sdk
            if sdk is not None
            else init_nebius_sdk(parent_id=project_id, context="MK8s destroy")
        )
        self.clients: dict[str, Any] = {
            "cluster": ClusterServiceClient(self.sdk),
            "node_group": NodeGroupServiceClient(self.sdk),
            "filesystem": FilesystemServiceClient(self.sdk),
            "instance": InstanceServiceClient(self.sdk),
            "disk": DiskServiceClient(self.sdk),
            "gpu_cluster": GpuClusterServiceClient(self.sdk),
            "allocation": AllocationServiceClient(self.sdk),
        }
        self.assert_held, self.progress = assert_held, progress

    def close(self) -> None:
        self.sdk.sync_close()

    def request(self, kind: str, method: str, **fields: Any) -> Any:
        from nebius.api.nebius.compute import v1 as compute
        from nebius.api.nebius.mk8s import v1 as mk8s
        from nebius.api.nebius.vpc import v1 as vpc

        namespace = (
            mk8s if kind in {"cluster", "node_group"} else vpc if kind == "allocation" else compute
        )
        name = {
            "cluster": "Cluster",
            "node_group": "NodeGroup",
            "filesystem": "Filesystem",
            "instance": "Instance",
            "disk": "Disk",
            "gpu_cluster": "GpuCluster",
            "allocation": "Allocation",
        }[kind]
        request_type = getattr(
            namespace, method.title() + (name + "s" if method == "list" else name) + "Request"
        )
        return request_type(**fields)

    def get(self, kind: str, identifier: str, *, absent_ok: bool = False) -> dict[str, Any] | None:
        try:
            response = (
                self.clients[kind]
                .get(self.request(kind, "get", id=identifier), **bounded_nebius_request_kwargs())
                .wait()
            )
        except Exception as exc:
            code = getattr(getattr(exc, "status", None), "code", None)
            if absent_ok and getattr(code, "name", "") == "NOT_FOUND":
                return None
            raise
        result = dict(sdk_message_to_mapping(response))
        if metadata(result)["id"] != identifier:
            raise RuntimeError("Destroy cloud response returned a different resource ID")
        return result

    def list(self, kind: str, parent_id: str) -> list[dict[str, Any]]:
        token = ""
        seen: set[str] = set()
        identities: set[str] = set()
        items: list[dict[str, Any]] = []
        while True:
            response = (
                self.clients[kind]
                .list(
                    self.request(kind, "list", parent_id=parent_id, page_token=token),
                    **bounded_nebius_request_kwargs(),
                )
                .wait()
            )
            payload = sdk_message_to_mapping(response)
            batch = payload.get("items", [])
            if not isinstance(batch, list):
                raise RuntimeError("Destroy cloud list is incomplete")
            for value in batch:
                identifier = str(metadata(value)["id"])
                if identifier in identities or metadata(value).get("parent_id") != parent_id:
                    raise RuntimeError("Destroy cloud list has duplicate or foreign identities")
                identities.add(identifier)
                items.append(dict(value))
            token = str(payload.get("next_page_token") or "")
            if not token:
                return items
            if token in seen:
                raise RuntimeError("Destroy cloud pagination did not advance")
            seen.add(token)

    def inventory(
        self,
        cluster_id: str,
        *,
        delete_sfs: bool,
        preserve_pvc_disks: bool = False,
        managed_gpu_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        from .destroy_resources import discover

        cluster = self.get("cluster", cluster_id)
        assert cluster is not None
        if metadata(cluster).get("parent_id") != self.project_id:
            raise RuntimeError("Destroy cluster belongs to a different project")
        groups = self.list("node_group", cluster_id)
        group_ids = {str(metadata(group)["id"]) for group in groups}
        instances = self.list("instance", self.project_id)
        workers: dict[str, str] = {}
        filesystems: set[str] = set()
        group_filesystems: dict[str, list[str]] = {}
        for group in groups:
            spec = group.get("spec")
            if not isinstance(spec, Mapping) or not isinstance(spec.get("template"), Mapping):
                raise RuntimeError("Destroy node-group template inventory is incomplete")
            ids = filesystem_ids(spec["template"])
            group_filesystems[str(metadata(group)["id"])] = sorted(ids)
            filesystems.update(ids)
        for instance in instances:
            group_id = worker_group(instance, self.project_id, cluster_id, group_ids)
            if group_id is not None:
                workers[str(metadata(instance)["id"])] = group_id
                if not isinstance(instance.get("spec"), Mapping):
                    raise RuntimeError("Destroy worker attachment specification is unavailable")
                filesystems.update(filesystem_ids(instance["spec"]))
        if delete_sfs:
            self.check_exclusive(filesystems, cluster_id, set(workers), instances=instances)
        for identifier in sorted(filesystems):
            self.check_filesystem(identifier, delete=delete_sfs)
        return {
            "cluster_id": cluster_id,
            "project_id": self.project_id,
            "node_group_ids": sorted(group_ids),
            "group_filesystems": group_filesystems,
            "filesystem_ids": sorted(filesystems),
            "worker_ids": sorted(workers),
            **discover(
                self,
                cluster_id,
                groups,
                instances,
                set(workers),
                preserve_pvc_disks=preserve_pvc_disks,
                managed_gpu_ids=managed_gpu_ids,
            ),
        }

    def check_filesystem(self, identifier: str, *, delete: bool, detached: bool = False) -> None:
        filesystem = self.get("filesystem", identifier)
        assert filesystem is not None
        if metadata(filesystem).get("parent_id") != self.project_id:
            raise RuntimeError("Destroy filesystem belongs to a different project")
        if not isinstance(filesystem.get("spec"), Mapping):
            raise RuntimeError("Destroy filesystem protection specification is unavailable")
        if delete and filesystem["spec"].get("forbid_deletion", False):
            raise RuntimeError(
                f"SFS {identifier} has deletion protection; no deletion was authorized"
            )
        status = filesystem.get("status")
        if not isinstance(status, Mapping):
            raise RuntimeError("Destroy filesystem attachment status is unavailable")
        if detached and any(
            status.get(field) for field in ("read_write_attachments", "read_only_attachments")
        ):
            raise RuntimeError(
                f"SFS {identifier} still has attachments; storage deletion is blocked"
            )

    def check_exclusive(
        self,
        ids: set[str],
        cluster_id: str,
        workers: set[str],
        *,
        instances: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        if not ids:
            return
        for instance in (
            instances if instances is not None else self.list("instance", self.project_id)
        ):
            if not isinstance(instance.get("spec"), Mapping):
                raise RuntimeError("SFS instance reference inventory is incomplete")
            if str(metadata(instance)["id"]) not in workers and ids & filesystem_ids(
                instance.get("spec", {})
            ):
                raise RuntimeError("SFS is referenced by an instance outside the selected cluster")
        for cluster in self.list("cluster", self.project_id):
            other_id = str(metadata(cluster)["id"])
            if other_id == cluster_id:
                continue
            for group in self.list("node_group", other_id):
                spec = group.get("spec")
                if not isinstance(spec, Mapping) or not isinstance(spec.get("template"), Mapping):
                    raise RuntimeError("SFS node-group reference inventory is incomplete")
                if ids & filesystem_ids(spec["template"]):
                    raise RuntimeError("SFS is referenced by another cluster node-group template")
        for identifier in sorted(ids):
            filesystem = self.get("filesystem", identifier)
            assert filesystem is not None
            status = filesystem.get("status")
            if not isinstance(status, Mapping):
                raise RuntimeError("SFS attachment evidence is unavailable")
            attachments = set(status.get("read_write_attachments", [])) | set(
                status.get("read_only_attachments", [])
            )
            if attachments - workers:
                raise RuntimeError(f"SFS {identifier} is shared or has unknown attachments")

    def verify_scope(
        self,
        frozen: Mapping[str, Any],
        *,
        delete_sfs: bool,
        preserve_pvc_disks: bool = False,
    ) -> None:
        from .destroy_resources import verify_pvc_scope

        current = self.inventory(
            str(frozen["cluster_id"]),
            delete_sfs=delete_sfs,
            preserve_pvc_disks=preserve_pvc_disks,
            managed_gpu_ids=frozen["managed_gpu_cluster_ids"],
        )
        fields = (
            "cluster_id",
            "project_id",
            "node_group_ids",
            "group_filesystems",
            "filesystem_ids",
            "gpu_cluster_ids",
            "group_gpu_clusters",
        )
        if any(current[field] != frozen[field] for field in fields):
            raise RuntimeError(
                "Destroy cloud inventory changed; approved group/storage scope cannot expand"
            )
        verify_pvc_scope(self, frozen, current, preserve_pvc_disks)

    def poll_delete_once(self, kind: str, identifier: str, operation_id: str) -> bool:
        from nebius.api.nebius.common.v1 import GetOperationRequest

        self.assert_held()
        operation = (
            self.clients[kind]
            .operation_service()
            .get(GetOperationRequest(id=operation_id), **bounded_nebius_request_kwargs())
            .wait()
        )
        if operation.id != operation_id or operation.resource_id != identifier:
            raise RuntimeError("Destroy operation does not match its approved resource")
        if operation.done() and not operation.successful():
            raise TerminalDestroyOperationError(
                "Destroy provider operation failed; rerun the same command to review and confirm another attempt"
            )
        return operation.done()

    def submit_delete(self, kind: str, identifier: str, key: str) -> str:
        self.assert_held()
        operation = (
            self.clients[kind]
            .delete(
                self.request(kind, "delete", id=identifier),
                metadata=[("x-idempotency-key", key)],
                **bounded_nebius_request_kwargs(),
            )
            .wait()
        )
        if not operation.id or operation.resource_id != identifier:
            raise RuntimeError("Destroy provider operation identity is incomplete or mismatched")
        return str(operation.id)

    def poll_delete(
        self, kind: str, identifier: str, operation_id: str, *, timeout: float = 3600
    ) -> None:
        deadline = time.monotonic() + timeout
        while True:
            if self.poll_delete_once(kind, identifier, operation_id):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Destroy operation is still running; rerun the same command to resume"
                )
            label = "GPU cluster" if kind == "gpu_cluster" else kind
            self.progress(f"Waiting for {label} deletion: {identifier}")
            time.sleep(2)

    def recover_operation(self, kind: str, identifier: str, key: str) -> str:
        from nebius.api.nebius.common.v1 import ListOperationsRequest

        token = ""
        seen: set[str] = set()
        matches: list[str] = []
        while True:
            response = (
                self.clients[kind]
                .operation_service()
                .list(
                    ListOperationsRequest(resource_id=identifier, page_token=token),
                    **bounded_nebius_request_kwargs(),
                )
                .wait()
            )
            for operation in response.operations:
                payload = sdk_message_to_mapping(operation)
                values = (
                    payload.get("request_headers", {})
                    .get("x-idempotency-key", {})
                    .get("values", [])
                )
                request = payload.get("request", {})
                if (
                    key in values
                    and payload.get("resource_id") == identifier
                    and request.get("id") == identifier
                    and str(request.get("@type", "")).endswith(
                        {
                            "cluster": "/nebius.mk8s.v1.DeleteClusterRequest",
                            "filesystem": "/nebius.compute.v1.DeleteFilesystemRequest",
                            "disk": "/nebius.compute.v1.DeleteDiskRequest",
                            "gpu_cluster": "/nebius.compute.v1.DeleteGpuClusterRequest",
                        }[kind]
                    )
                ):
                    matches.append(str(payload["id"]))
            token = str(response.next_page_token or "")
            if not token:
                break
            if token in seen:
                raise RuntimeError("Destroy operation pagination did not advance")
            seen.add(token)
        if len(matches) != 1:
            raise RuntimeError(
                "Destroy request acceptance is unknown; provider operation evidence is required; no request was replayed"
            )
        return matches[0]

    def verify_cluster_absent(self, inventory: Mapping[str, Any]) -> None:
        cluster_id = str(inventory["cluster_id"])
        if self.get("cluster", cluster_id, absent_ok=True) is not None:
            raise RuntimeError("Destroy cluster is not yet absent; rerun to resume")
        for identifier in inventory["node_group_ids"]:
            if self.get("node_group", identifier, absent_ok=True) is not None:
                raise RuntimeError("Destroy node group remains after cluster deletion")
        groups = set(inventory["node_group_ids"])
        frozen_workers = set(inventory["worker_ids"])
        for instance in self.list("instance", self.project_id):
            if (
                metadata(instance)["id"] in frozen_workers
                or worker_group(instance, self.project_id, cluster_id, groups) is not None
            ):
                raise RuntimeError("Destroy worker remains after cluster deletion")

    def discover_vm_nfs(self, config: Mapping[str, Any]) -> dict[str, Any] | None:
        if not config.get("enabled"):
            return None
        from .soperator_infrastructure_identity import _server_addresses

        addresses = _server_addresses(str(config.get("server") or ""), ())
        instances = self.list("instance", self.project_id)
        matched = [
            instance
            for instance in instances
            if addresses
            & {
                row.get("ip_address", {}).get("address", "")
                for row in instance.get("status", {}).get("network_interfaces", [])
            }
        ]
        if len(matched) != 1:
            raise RuntimeError("VM-NFS cloud identity is missing or ambiguous")
        instance = matched[0]
        identifier = str(metadata(instance)["id"])
        binding = self._nfs_binding(instance)
        result = {
            "resources": {
                "instance": [identifier],
                "disk": sorted(row["id"] for row in binding["disks"]),
                "allocation": binding["allocations"],
            },
            "binding": binding,
        }
        self.verify_vm_nfs(result)
        return result

    @staticmethod
    def _nfs_binding(instance: Mapping[str, Any]) -> dict[str, Any]:
        spec, status = instance.get("spec", {}), instance.get("status", {})
        disks = []
        for attachment in spec.get("secondary_disks", []):
            existing = attachment.get("existing_disk", {}).get("id")
            managed = attachment.get("managed_disk", {}).get("name")
            matches = [
                row
                for row in status.get("disk_attachments", [])
                if (existing and row.get("id") == existing)
                or (managed and row.get("name") == managed)
            ]
            if len(matches) != 1 or not matches[0].get("id"):
                raise RuntimeError("VM-NFS data disk identity is missing or ambiguous")
            disks.append(
                {
                    "id": matches[0]["id"],
                    "device_id": attachment.get("device_id"),
                    "attach_mode": attachment.get("attach_mode"),
                    "managed": bool(matches[0].get("is_managed")),
                }
            )
        if not disks or len({row["id"] for row in disks}) != len(disks):
            raise RuntimeError("VM-NFS data disks are missing or duplicated")
        interfaces = status.get("network_interfaces", [])
        addresses = sorted(
            {row.get("ip_address", {}).get("address", "") for row in interfaces} - {""}
        )
        allocations = sorted(
            {
                row.get(field, {}).get("allocation_id", "")
                for row in interfaces
                for field in ("ip_address", "public_ip_address")
            }
            - {""}
        )
        if not addresses:
            raise RuntimeError("VM-NFS private address is unavailable")
        return {
            "disks": sorted(disks, key=lambda row: row["id"]),
            "addresses": addresses,
            "allocations": allocations,
        }

    def verify_vm_nfs(self, frozen: Mapping[str, Any] | None) -> None:
        if frozen is None:
            return
        for kind, identifiers in frozen["resources"].items():
            for identifier in identifiers:
                resource = self.get(kind, identifier)
                assert resource is not None
                if metadata(resource).get("parent_id") != self.project_id:
                    raise RuntimeError("VM-NFS resource belongs to a different project")
                if kind == "instance" and self._nfs_binding(resource) != frozen["binding"]:
                    raise RuntimeError("VM-NFS backing storage or network binding changed")
