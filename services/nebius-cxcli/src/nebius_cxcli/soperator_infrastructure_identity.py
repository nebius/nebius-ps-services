"""Authoritative, non-secret infrastructure identity for Soperator upgrades."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .component_instances import normalize_component_token
from .sdk_auth import init_nebius_sdk

SOPERATOR_INFRASTRUCTURE_IDENTITY_SCHEMA = "nebius-cxcli.soperator-infrastructure-identity.v4"
_PROTECTED_SFS_ROLES = frozenset({"accounting", "controller-spool", "jail"})


def _sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SfsFilesystemIdentity:
    role: str
    filesystem_id: str
    mount_tag: str
    node_group_ids: tuple[str, ...]
    pv_names: tuple[str, ...]
    pvc_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.role or not self.filesystem_id or not self.mount_tag:
            raise ValueError("SFS filesystem identity is incomplete")
        for values in (self.node_group_ids, self.pv_names, self.pvc_names):
            if values != tuple(sorted(set(values))):
                raise ValueError("SFS identity collections must be unique and sorted")


@dataclass(frozen=True)
class SfsProtectedStorageIdentity:
    filesystems: tuple[SfsFilesystemIdentity, ...]

    def __post_init__(self) -> None:
        if not self.filesystems:
            raise ValueError("SFS protected-storage identity has no filesystems")
        if self.filesystems != tuple(sorted(self.filesystems, key=lambda item: item.role)):
            raise ValueError("SFS filesystem identities must be sorted by role")
        if len({item.role for item in self.filesystems}) != len(self.filesystems):
            raise ValueError("SFS filesystem roles must be unique")
        if len({item.filesystem_id for item in self.filesystems}) != len(self.filesystems):
            raise ValueError("SFS filesystem IDs must be unique")


@dataclass(frozen=True)
class VmNfsProtectedStorageIdentity:
    instance_id: str
    server_address: str
    private_ips: tuple[str, ...]
    data_disk_ids: tuple[str, ...]
    attachment_sha256: str
    allocation_ids: tuple[str, ...]
    export_path: str
    export_sha256: str

    def __post_init__(self) -> None:
        if (
            not self.instance_id
            or not self.server_address
            or not self.private_ips
            or not self.export_path.startswith("/")
        ):
            raise ValueError("NFS infrastructure identity is incomplete")
        if not self.data_disk_ids:
            raise ValueError("NFS infrastructure identity has no attached data disk")
        if self.private_ips != tuple(sorted(set(self.private_ips))):
            raise ValueError("NFS private IP identities must be unique and sorted")
        if self.data_disk_ids != tuple(sorted(set(self.data_disk_ids))):
            raise ValueError("NFS data-disk identities must be unique and sorted")
        if self.allocation_ids != tuple(sorted(set(self.allocation_ids))):
            raise ValueError("NFS allocation identities must be unique and sorted")
        if not self.attachment_sha256.startswith("sha256:") or not self.export_sha256.startswith(
            "sha256:"
        ):
            raise ValueError("NFS attachment identity is incomplete")


@dataclass(frozen=True)
class ProtectedStorageIdentity:
    kind: str
    sfs: SfsProtectedStorageIdentity | None = None
    vm_nfs: VmNfsProtectedStorageIdentity | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"sfs", "vm-nfs"}:
            raise ValueError("protected-storage kind is unsupported")
        if (self.sfs is None) == (self.vm_nfs is None):
            raise ValueError("protected-storage identity must contain exactly one variant")
        if self.kind == "sfs" and self.sfs is None:
            raise ValueError("SFS protected-storage identity is missing")
        if self.kind == "vm-nfs" and self.vm_nfs is None:
            raise ValueError("VM-NFS protected-storage identity is missing")


@dataclass(frozen=True)
class LoginAllocationIdentity:
    namespace: str
    service_name: str
    service_uid: str
    service_type: str
    cluster_ips: tuple[str, ...]
    ingress_addresses: tuple[str, ...]
    allocation_ids: tuple[str, ...]
    service_spec_sha256: str
    assignment_sha256: str

    def __post_init__(self) -> None:
        if not self.namespace:
            raise ValueError("login Service namespace is required")
        if self.service_name and not self.service_uid:
            raise ValueError("observed login Service has no immutable UID")
        for values in (self.cluster_ips, self.ingress_addresses, self.allocation_ids):
            if values != tuple(sorted(set(values))):
                raise ValueError("login Service identity collections must be unique and sorted")
        if not self.service_spec_sha256.startswith("sha256:"):
            raise ValueError("login Service spec identity is incomplete")
        if not self.assignment_sha256.startswith("sha256:"):
            raise ValueError("login allocation assignment identity is incomplete")


@dataclass(frozen=True)
class SoperatorInfrastructureReceipt:
    schema: str
    project_id: str
    nebius_cluster_id: str
    kubernetes_uid: str
    storage: ProtectedStorageIdentity
    login: LoginAllocationIdentity

    def __post_init__(self) -> None:
        if self.schema != SOPERATOR_INFRASTRUCTURE_IDENTITY_SCHEMA:
            raise ValueError("Soperator infrastructure identity schema is unsupported")
        if not self.project_id or not self.nebius_cluster_id or not self.kubernetes_uid:
            raise ValueError("Soperator infrastructure receipt identity is incomplete")

    @property
    def receipt_sha256(self) -> str:
        return _sha256(asdict(self))

    def as_payload(self) -> dict[str, object]:
        return {**asdict(self), "receipt_sha256": self.receipt_sha256}


def infrastructure_discovery_inputs_from_receipt(
    infrastructure: SoperatorInfrastructureReceipt,
) -> dict[str, object]:
    """Project an authenticated receipt into exact live re-read selectors."""

    storage = infrastructure.storage
    if storage.kind == "sfs":
        if storage.sfs is None:  # pragma: no cover - dataclass invariant
            raise RuntimeError("admitted Soperator SFS infrastructure identity is incomplete")
        return {
            "storage_kind": "sfs",
            "sfs_filesystems": tuple(
                {
                    "role": filesystem.role,
                    "filesystem_id": filesystem.filesystem_id,
                    "mount_tag": filesystem.mount_tag,
                    "node_group_ids": filesystem.node_group_ids,
                    "pv_names": filesystem.pv_names,
                    "pvc_names": filesystem.pvc_names,
                }
                for filesystem in storage.sfs.filesystems
            ),
        }
    if storage.vm_nfs is None:  # pragma: no cover - dataclass invariant
        raise RuntimeError("admitted Soperator VM-NFS infrastructure identity is incomplete")
    return {
        "storage_kind": "vm-nfs",
        "nfs_server": storage.vm_nfs.server_address,
        "nfs_allocation_ids": storage.vm_nfs.allocation_ids,
        "nfs_export": {
            "server": storage.vm_nfs.server_address,
            "path": storage.vm_nfs.export_path,
        },
    }


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(sorted({str(item).strip() for item in value if str(item).strip()}))


def _sfs_filesystem_identity(value: Mapping[str, object]) -> SfsFilesystemIdentity:
    return SfsFilesystemIdentity(
        role=str(value.get("role") or "").strip(),
        filesystem_id=str(value.get("filesystem_id") or value.get("filesystemId") or "").strip(),
        mount_tag=str(value.get("mount_tag") or value.get("mountTag") or "").strip(),
        node_group_ids=_string_tuple(
            value.get("node_group_ids") or value.get("nodeGroupIds") or ()
        ),
        pv_names=_string_tuple(value.get("pv_names") or value.get("pvNames") or ()),
        pvc_names=_string_tuple(value.get("pvc_names") or value.get("pvcNames") or ()),
    )


def _resource_value(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def sfs_filesystem_observations_from_node_groups(
    node_groups: Sequence[object],
    *,
    kubernetes_bindings: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[dict[str, object], ...]:
    """Resolve protected physical-SFS identities from exact MK8s attachments."""

    bindings = kubernetes_bindings or {}
    observations: dict[str, dict[str, object]] = {}
    for node_group in node_groups:
        node_group_id = _metadata_id(node_group)
        if not node_group_id:
            raise RuntimeError("protected SFS node group has no immutable ID")
        spec = _resource_value(node_group, "spec")
        template = _resource_value(spec, "template")
        raw_filesystems = _resource_value(template, "filesystems")
        filesystems = (
            raw_filesystems
            if isinstance(raw_filesystems, Sequence)
            and not isinstance(raw_filesystems, (str, bytes, bytearray))
            else ()
        )
        for attachment in filesystems:
            mount_tag = str(_resource_value(attachment, "mount_tag") or "").strip()
            if mount_tag not in _PROTECTED_SFS_ROLES:
                continue
            raw_attach_mode = _resource_value(attachment, "attach_mode")
            attach_mode = str(getattr(raw_attach_mode, "name", "") or raw_attach_mode or "").strip()
            if attach_mode != "READ_WRITE":
                raise RuntimeError(f"protected SFS role {mount_tag!r} must be attached READ_WRITE")
            existing = _resource_value(attachment, "existing_filesystem")
            filesystem_id = str(_resource_value(existing, "id") or "").strip()
            if not filesystem_id:
                raise RuntimeError(
                    f"protected SFS role {mount_tag!r} has no existing filesystem ID"
                )
            current = observations.setdefault(
                mount_tag,
                {
                    "role": mount_tag,
                    "filesystem_id": filesystem_id,
                    "mount_tag": mount_tag,
                    "node_group_ids": [],
                    "pv_names": [],
                    "pvc_names": [],
                },
            )
            if current["filesystem_id"] != filesystem_id:
                raise RuntimeError(
                    f"protected SFS role {mount_tag!r} resolves to multiple filesystem IDs"
                )
            node_group_ids = current.get("node_group_ids")
            if not isinstance(node_group_ids, list):  # pragma: no cover - constructor invariant
                raise RuntimeError("protected SFS node-group identity is malformed")
            node_group_ids.append(node_group_id)

    missing = sorted(_PROTECTED_SFS_ROLES - set(observations))
    if missing:
        raise RuntimeError(
            "protected SFS node-group attachments are incomplete; missing roles: "
            + ", ".join(missing)
        )
    for role, observation in observations.items():
        observation["node_group_ids"] = list(_string_tuple(observation.get("node_group_ids")))
        binding = bindings.get(role)
        if not isinstance(binding, Mapping):
            continue
        observation["pv_names"] = list(_string_tuple(binding.get("pv_names")))
        observation["pvc_names"] = list(_string_tuple(binding.get("pvc_names")))
    return tuple(observations[role] for role in sorted(observations))


def soperator_local_sfs_kubernetes_bindings(
    snapshot: Mapping[str, Any],
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Return retained local-SFS PV/PVC names grouped by protected role."""

    raw_pvcs = snapshot.get("pvcs")
    raw_pvs = snapshot.get("pvs")
    pvcs = [item for item in raw_pvcs or () if isinstance(item, Mapping)]
    pvs = [item for item in raw_pvs or () if isinstance(item, Mapping)]
    pv_by_name = {
        str(_resource_value(_resource_value(item, "metadata"), "name") or "").strip(): item
        for item in pvs
        if str(_resource_value(_resource_value(item, "metadata"), "name") or "").strip()
    }
    bindings: dict[str, dict[str, list[str]]] = {}
    for pvc in pvcs:
        metadata = _resource_value(pvc, "metadata")
        if str(_resource_value(metadata, "namespace") or "").strip() != "soperator":
            continue
        pvc_name = str(_resource_value(metadata, "name") or "").strip()
        pvc_spec = _resource_value(pvc, "spec")
        pv_name = str(_resource_value(pvc_spec, "volumeName") or "").strip()
        pv_spec = _resource_value(pv_by_name.get(pv_name), "spec")
        if not isinstance(pv_spec, Mapping) or not isinstance(pv_spec.get("local"), Mapping):
            continue
        if str(pv_spec.get("persistentVolumeReclaimPolicy") or "").strip() != "Retain":
            raise RuntimeError(f"protected local SFS PV {pv_name!r} must use reclaim policy Retain")
        identity = normalize_component_token(f"{pvc_name}-{pv_name}").replace("-", "")
        role = next(
            (
                candidate
                for candidate in sorted(_PROTECTED_SFS_ROLES)
                if candidate.replace("-", "") in identity
            ),
            "",
        )
        if not role:
            continue
        row = bindings.setdefault(role, {"pv_names": [], "pvc_names": []})
        row["pv_names"].append(pv_name)
        row["pvc_names"].append(pvc_name)
    return {
        role: {
            "pv_names": tuple(sorted(set(row["pv_names"]))),
            "pvc_names": tuple(sorted(set(row["pvc_names"]))),
        }
        for role, row in sorted(bindings.items())
    }


def sfs_filesystem_observations_from_snapshot(
    snapshot: Mapping[str, object],
    *,
    configured_filesystems: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], ...]:
    """Resolve physical SFS identities from PVC/PV bindings without retaining data."""

    configured = configured_filesystems or {}
    raw_pvcs = snapshot.get("pvcs")
    raw_pvs = snapshot.get("pvs")
    raw_pvc_items = (
        raw_pvcs
        if isinstance(raw_pvcs, Sequence) and not isinstance(raw_pvcs, (str, bytes, bytearray))
        else ()
    )
    raw_pv_items = (
        raw_pvs
        if isinstance(raw_pvs, Sequence) and not isinstance(raw_pvs, (str, bytes, bytearray))
        else ()
    )
    pvcs: list[Mapping[str, object]] = []
    for item in raw_pvc_items:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata")
        if isinstance(metadata, Mapping) and str(metadata.get("namespace") or "") == "soperator":
            pvcs.append(item)
    pvs = [item for item in raw_pv_items if isinstance(item, Mapping)]
    roles = tuple(sorted(str(item).strip() for item in configured if str(item).strip())) or (
        "accounting",
        "controller-spool",
        "jail",
    )
    observations: list[dict[str, object]] = []
    for role in roles:
        matching_pvcs = [
            item
            for item in pvcs
            if role.replace("-", "")
            in str(_resource_value(_resource_value(item, "metadata"), "name") or "")
            .lower()
            .replace("-", "")
        ]
        bindings: list[tuple[str, str, str]] = []
        for pvc in matching_pvcs:
            raw_metadata = pvc.get("metadata")
            metadata: Mapping[str, object] = (
                raw_metadata if isinstance(raw_metadata, Mapping) else {}
            )
            raw_pvc_spec = pvc.get("spec")
            spec: Mapping[str, object] = raw_pvc_spec if isinstance(raw_pvc_spec, Mapping) else {}
            pvc_name = str(metadata.get("name") or "").strip()
            pv_name = str(spec.get("volumeName") or "").strip()
            pv = next(
                (
                    item
                    for item in pvs
                    if str(_resource_value(_resource_value(item, "metadata"), "name") or "").strip()
                    == pv_name
                ),
                None,
            )
            raw_pv_spec = pv.get("spec") if isinstance(pv, Mapping) else None
            pv_spec: Mapping[str, object] = raw_pv_spec if isinstance(raw_pv_spec, Mapping) else {}
            raw_csi = pv_spec.get("csi")
            csi: Mapping[str, object] = raw_csi if isinstance(raw_csi, Mapping) else {}
            filesystem_id = str(csi.get("volumeHandle") or "").strip()
            if pvc_name and pv_name and filesystem_id:
                bindings.append((pvc_name, pv_name, filesystem_id))
        filesystem_ids = sorted({item[2] for item in bindings})
        if len(filesystem_ids) != 1:
            raise RuntimeError(
                f"protected SFS role {role!r} must resolve to exactly one CSI filesystem ID"
            )
        raw_spec = configured.get(role)
        spec = raw_spec if isinstance(raw_spec, Mapping) else {}
        node_groups = snapshot.get("node_groups")
        node_group_ids = (
            tuple(sorted(str(item) for item in node_groups))
            if isinstance(node_groups, Mapping)
            else ()
        )
        observations.append(
            {
                "role": role,
                "filesystem_id": filesystem_ids[0],
                "mount_tag": str(spec.get("mount_tag") or role).strip(),
                "node_group_ids": node_group_ids,
                "pv_names": sorted({item[1] for item in bindings}),
                "pvc_names": sorted({item[0] for item in bindings}),
            }
        )
    return tuple(observations)


def _authoritative_sfs_observations(
    observations: Sequence[Mapping[str, object]],
    *,
    get_filesystem: Any,
) -> tuple[dict[str, object], ...]:
    authoritative: list[dict[str, object]] = []
    for item in observations:
        filesystem_id = str(item.get("filesystem_id") or item.get("filesystemId") or "").strip()
        if not filesystem_id:
            raise RuntimeError("protected SFS observation has no immutable filesystem ID")
        filesystem = get_filesystem(filesystem_id)
        observed_id = _metadata_id(filesystem)
        if observed_id != filesystem_id:
            raise RuntimeError(
                f"protected SFS identity mismatch: expected {filesystem_id}, "
                f"observed {observed_id or 'missing'}"
            )
        authoritative.append(
            {
                **dict(item),
                "filesystem_id": filesystem_id,
            }
        )
    return tuple(authoritative)


def build_soperator_infrastructure_receipt(
    *,
    project_id: str,
    nebius_cluster_id: str,
    kubernetes_uid: str,
    storage_kind: str,
    login_observation: Mapping[str, object],
    allocations: Sequence[object],
    sfs_filesystems: Sequence[Mapping[str, object]] = (),
    nfs_server: str = "",
    instances: Sequence[object] = (),
    nfs_allocation_ids: Sequence[str] = (),
    nfs_export: Mapping[str, object] | None = None,
    namespace: str = "soperator",
    resolved_nfs_addresses: Sequence[str] = (),
) -> SoperatorInfrastructureReceipt:
    kind = str(storage_kind or "").strip().lower()
    if kind == "sfs":
        filesystems = tuple(
            sorted(
                (_sfs_filesystem_identity(item) for item in sfs_filesystems),
                key=lambda item: item.role,
            )
        )
        storage = ProtectedStorageIdentity(
            kind="sfs",
            sfs=SfsProtectedStorageIdentity(filesystems=filesystems),
        )
    elif kind == "vm-nfs":
        server = str(nfs_server or "").strip()
        if not server:
            raise RuntimeError("protected VM-based NFS server address is unavailable")
        export = dict(nfs_export or {})
        export_path = str(export.get("path") or "").strip()
        if not export_path.startswith("/"):
            raise RuntimeError("protected VM-based NFS export path is unavailable")
        if str(export.get("server") or "").strip() != server:
            raise RuntimeError("protected VM-based NFS export server identity is inconsistent")
        addresses = _server_addresses(server, resolved_nfs_addresses)
        matching_instances = [
            item for item in instances if addresses.intersection(_instance_private_ips(item))
        ]
        if len(matching_instances) != 1:
            raise RuntimeError(
                "protected VM-based NFS identity is ambiguous; expected exactly one Nebius "
                f"instance for {server}, found {len(matching_instances)}"
            )
        instance = matching_instances[0]
        instance_id = _metadata_id(instance)
        if not instance_id:
            raise RuntimeError("protected VM-based NFS instance has no immutable ID")
        attachments = _secondary_disk_attachments(instance)
        storage = ProtectedStorageIdentity(
            kind="vm-nfs",
            vm_nfs=VmNfsProtectedStorageIdentity(
                instance_id=instance_id,
                server_address=server,
                private_ips=tuple(sorted(_instance_private_ips(instance))),
                data_disk_ids=tuple(sorted(item["id"] for item in attachments)),
                attachment_sha256=_sha256(attachments),
                allocation_ids=tuple(sorted(set(str(item) for item in nfs_allocation_ids))),
                export_path=export_path,
                export_sha256=_sha256({"server": server, "path": export_path}),
            ),
        )
    else:
        raise RuntimeError("protected storage must be identified as sfs or vm-nfs")
    login = _login_allocation_identity(
        namespace=namespace,
        observation=login_observation,
        allocations=allocations,
    )
    return SoperatorInfrastructureReceipt(
        schema=SOPERATOR_INFRASTRUCTURE_IDENTITY_SCHEMA,
        project_id=str(project_id or "").strip(),
        nebius_cluster_id=str(nebius_cluster_id or "").strip(),
        kubernetes_uid=str(kubernetes_uid or "").strip(),
        storage=storage,
        login=login,
    )


def soperator_infrastructure_receipt_from_payload(
    payload: Mapping[str, object],
) -> SoperatorInfrastructureReceipt:
    raw_storage = payload.get("storage")
    raw_login = payload.get("login")
    if not isinstance(raw_storage, Mapping) or not isinstance(raw_login, Mapping):
        raise RuntimeError("Soperator infrastructure receipt is malformed")
    kind = str(raw_storage.get("kind") or "")
    raw_sfs = raw_storage.get("sfs")
    raw_vm_nfs = raw_storage.get("vm_nfs")
    sfs = None
    vm_nfs = None
    if isinstance(raw_sfs, Mapping):
        raw_filesystems = raw_sfs.get("filesystems")
        if not isinstance(raw_filesystems, Sequence) or isinstance(
            raw_filesystems, (str, bytes, bytearray)
        ):
            raise RuntimeError("Soperator SFS receipt is malformed")
        sfs = SfsProtectedStorageIdentity(
            filesystems=tuple(
                _sfs_filesystem_identity(item)
                for item in raw_filesystems
                if isinstance(item, Mapping)
            )
        )
    if isinstance(raw_vm_nfs, Mapping):
        vm_nfs = VmNfsProtectedStorageIdentity(
            instance_id=str(raw_vm_nfs.get("instance_id") or ""),
            server_address=str(raw_vm_nfs.get("server_address") or ""),
            private_ips=tuple(str(item) for item in raw_vm_nfs.get("private_ips", []) or []),
            data_disk_ids=tuple(str(item) for item in raw_vm_nfs.get("data_disk_ids", []) or []),
            attachment_sha256=str(raw_vm_nfs.get("attachment_sha256") or ""),
            allocation_ids=tuple(str(item) for item in raw_vm_nfs.get("allocation_ids", []) or []),
            export_path=str(raw_vm_nfs.get("export_path") or ""),
            export_sha256=str(raw_vm_nfs.get("export_sha256") or ""),
        )
    storage = ProtectedStorageIdentity(kind=kind, sfs=sfs, vm_nfs=vm_nfs)
    login = LoginAllocationIdentity(
        namespace=str(raw_login.get("namespace") or ""),
        service_name=str(raw_login.get("service_name") or ""),
        service_uid=str(raw_login.get("service_uid") or ""),
        service_type=str(raw_login.get("service_type") or ""),
        cluster_ips=tuple(str(item) for item in raw_login.get("cluster_ips", []) or []),
        ingress_addresses=tuple(str(item) for item in raw_login.get("ingress_addresses", []) or []),
        allocation_ids=tuple(str(item) for item in raw_login.get("allocation_ids", []) or []),
        service_spec_sha256=str(raw_login.get("service_spec_sha256") or ""),
        assignment_sha256=str(raw_login.get("assignment_sha256") or ""),
    )
    receipt = SoperatorInfrastructureReceipt(
        schema=str(payload.get("schema") or ""),
        project_id=str(payload.get("project_id") or ""),
        nebius_cluster_id=str(payload.get("nebius_cluster_id") or ""),
        kubernetes_uid=str(payload.get("kubernetes_uid") or ""),
        storage=storage,
        login=login,
    )
    if payload.get("receipt_sha256") != receipt.receipt_sha256:
        raise RuntimeError("Soperator infrastructure receipt was modified")
    return receipt


def discover_soperator_infrastructure_receipt(
    *,
    project_id: str,
    nebius_cluster_id: str,
    kubernetes_uid: str,
    storage_kind: str,
    login_observation: Mapping[str, object],
    sfs_filesystems: Sequence[Mapping[str, object]] = (),
    sfs_node_group_ids: Sequence[str] = (),
    sfs_kubernetes_bindings: Mapping[str, Mapping[str, object]] | None = None,
    nfs_server: str = "",
    nfs_allocation_ids: Sequence[str] = (),
    nfs_export: Mapping[str, object] | None = None,
    namespace: str = "soperator",
) -> SoperatorInfrastructureReceipt:
    """Read Nebius APIs once and return the exact managed/onboarded identity receipt."""

    sdk = init_nebius_sdk(parent_id=project_id, context="Soperator infrastructure discovery")
    try:
        from nebius.api.nebius.compute.v1 import (
            FilesystemServiceClient,
            GetFilesystemRequest,
            InstanceServiceClient,
            ListInstancesRequest,
        )
        from nebius.api.nebius.mk8s.v1 import GetNodeGroupRequest, NodeGroupServiceClient
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient, ListAllocationsRequest

        instances = (
            _paged_list(
                request_factory=lambda token: ListInstancesRequest(
                    parent_id=project_id,
                    page_size=1000,
                    page_token=token,
                ),
                request_call=InstanceServiceClient(sdk).list,
                field_names=("items", "instances"),
                context="Nebius compute instance",
            )
            if storage_kind == "vm-nfs"
            else []
        )
        allocations = _paged_list(
            request_factory=lambda token: ListAllocationsRequest(
                parent_id=project_id,
                page_size=1000,
                page_token=token,
            ),
            request_call=AllocationServiceClient(sdk).list,
            field_names=("items", "allocations"),
            context="Nebius VPC allocation",
        )
        authoritative_sfs = sfs_filesystems
        if storage_kind == "sfs":
            if authoritative_sfs and sfs_node_group_ids:
                raise RuntimeError(
                    "protected SFS discovery received competing configured and live attachments"
                )
            if not authoritative_sfs:
                node_group_ids = _string_tuple(sfs_node_group_ids)
                if not node_group_ids:
                    raise RuntimeError(
                        "protected SFS discovery requires exact MK8s node-group attachments"
                    )
                node_group_client = NodeGroupServiceClient(sdk)
                node_groups = []
                for node_group_id in node_group_ids:
                    node_group = node_group_client.get(GetNodeGroupRequest(id=node_group_id)).wait()
                    if _metadata_id(node_group) != node_group_id:
                        raise RuntimeError(
                            "protected SFS node-group identity differs from the requested ID"
                        )
                    node_groups.append(node_group)
                authoritative_sfs = sfs_filesystem_observations_from_node_groups(
                    node_groups,
                    kubernetes_bindings=sfs_kubernetes_bindings,
                )
            filesystem_client = FilesystemServiceClient(sdk)
            authoritative_sfs = _authoritative_sfs_observations(
                authoritative_sfs,
                get_filesystem=lambda filesystem_id: filesystem_client.get(
                    GetFilesystemRequest(id=filesystem_id)
                ).wait(),
            )
        return build_soperator_infrastructure_receipt(
            project_id=project_id,
            nebius_cluster_id=nebius_cluster_id,
            kubernetes_uid=kubernetes_uid,
            storage_kind=storage_kind,
            sfs_filesystems=authoritative_sfs,
            nfs_server=nfs_server,
            nfs_allocation_ids=nfs_allocation_ids,
            nfs_export=nfs_export,
            login_observation=login_observation,
            instances=instances,
            allocations=allocations,
            namespace=namespace,
        )
    finally:
        sdk.sync_close()


def verify_soperator_infrastructure_identity(
    *,
    before: SoperatorInfrastructureReceipt,
    after: SoperatorInfrastructureReceipt,
) -> dict[str, object]:
    fixed_identity_matches = (
        before.project_id,
        before.nebius_cluster_id,
        before.kubernetes_uid,
        before.storage.kind,
    ) == (
        after.project_id,
        after.nebius_cluster_id,
        after.kubernetes_uid,
        after.storage.kind,
    )
    binding_additions: list[dict[str, object]] = []
    storage_matches = before.storage == after.storage
    if before.storage.kind == "sfs" and after.storage.kind == "sfs":
        before_sfs = before.storage.sfs
        after_sfs = after.storage.sfs
        if before_sfs is None or after_sfs is None:  # pragma: no cover - dataclass invariant
            storage_matches = False
        else:
            before_by_role = {item.role: item for item in before_sfs.filesystems}
            after_by_role = {item.role: item for item in after_sfs.filesystems}
            storage_matches = before_by_role.keys() == after_by_role.keys()
            for role in sorted(before_by_role.keys() & after_by_role.keys()):
                left = before_by_role[role]
                right = after_by_role[role]
                physical_matches = (
                    left.filesystem_id,
                    left.mount_tag,
                    left.node_group_ids,
                ) == (
                    right.filesystem_id,
                    right.mount_tag,
                    right.node_group_ids,
                )
                bindings_preserved = set(left.pv_names) <= set(right.pv_names) and set(
                    left.pvc_names
                ) <= set(right.pvc_names)
                storage_matches = storage_matches and physical_matches and bindings_preserved
                added_pvs = sorted(set(right.pv_names) - set(left.pv_names))
                added_pvcs = sorted(set(right.pvc_names) - set(left.pvc_names))
                if added_pvs or added_pvcs:
                    binding_additions.append(
                        {"role": role, "pvNames": added_pvs, "pvcNames": added_pvcs}
                    )
    if not fixed_identity_matches or not storage_matches:
        raise RuntimeError("protected MK8s or storage infrastructure identity drifted")
    advisories: list[dict[str, str]] = []
    if before.login != after.login:
        advisories.append(
            {
                "code": "login-identity-drift",
                "beforeSha256": before.login.assignment_sha256,
                "afterSha256": after.login.assignment_sha256,
            }
        )
    return {
        "status": "verified",
        "advisories": advisories,
        "bindingAdditions": binding_additions,
    }


def _login_allocation_identity(
    *,
    namespace: str,
    observation: Mapping[str, object],
    allocations: Sequence[object],
) -> LoginAllocationIdentity:
    raw_services = observation.get("services")
    services = (
        [item for item in raw_services if isinstance(item, Mapping)]
        if isinstance(raw_services, Sequence)
        and not isinstance(raw_services, (str, bytes, bytearray))
        else []
    )
    load_balancers = [item for item in services if str(item.get("type") or "") == "LoadBalancer"]
    candidates = load_balancers or [item for item in services if item.get("ingress")]
    if not candidates and len(services) == 1:
        candidates = services
    if len(candidates) > 1:
        raise RuntimeError("login Service identity is ambiguous")
    if not candidates:
        return LoginAllocationIdentity(
            namespace=namespace,
            service_name="",
            service_uid="",
            service_type="",
            cluster_ips=(),
            ingress_addresses=(),
            allocation_ids=(),
            service_spec_sha256=_sha256({"status": "not-observed"}),
            assignment_sha256=_sha256({"status": "not-observed"}),
        )
    service = candidates[0]
    ingress = tuple(sorted(str(item) for item in service.get("ingress", []) or [] if str(item)))
    service_type = str(service.get("type") or "").strip()
    explicit_ids = tuple(
        sorted(str(item) for item in service.get("allocationIds", []) or [] if str(item))
    )
    allocation_ids = explicit_ids or _match_allocation_ids(ingress, allocations)
    if service_type == "LoadBalancer" and ingress and len(allocation_ids) != len(ingress):
        raise RuntimeError("login LoadBalancer allocation identity is missing or ambiguous")
    cluster_ips = service.get("clusterIPs") or [service.get("clusterIP")]
    normalized_cluster_ips = tuple(sorted(str(item) for item in cluster_ips if str(item or "")))
    spec_identity = {
        "name": str(service.get("name") or ""),
        "uid": str(service.get("uid") or ""),
        "type": service_type,
        "clusterIPs": normalized_cluster_ips,
        "ports": service.get("ports") or [],
        "selector": service.get("selector") or {},
        "annotations": service.get("annotations") or {},
    }
    assignment = {
        "service": spec_identity,
        "ingress": ingress,
        "allocationIds": allocation_ids,
    }
    return LoginAllocationIdentity(
        namespace=namespace,
        service_name=str(service.get("name") or "").strip(),
        service_uid=str(service.get("uid") or "").strip(),
        service_type=service_type,
        cluster_ips=normalized_cluster_ips,
        ingress_addresses=ingress,
        allocation_ids=allocation_ids,
        service_spec_sha256=_sha256(spec_identity),
        assignment_sha256=_sha256(assignment),
    )


def _match_allocation_ids(
    ingress_addresses: Sequence[str], allocations: Sequence[object]
) -> tuple[str, ...]:
    resolved: list[str] = []
    for address in ingress_addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise RuntimeError("login LoadBalancer ingress is not an IP allocation") from None
        matches = []
        for allocation in allocations:
            cidr = _allocation_cidr(allocation)
            if not cidr:
                continue
            try:
                if ip in ipaddress.ip_network(cidr, strict=False):
                    matches.append(_metadata_id(allocation))
            except ValueError:
                continue
        matches = sorted({item for item in matches if item})
        if len(matches) != 1:
            raise RuntimeError(
                "login LoadBalancer allocation identity is ambiguous; expected one "
                f"allocation for {address}, found {len(matches)}"
            )
        resolved.append(matches[0])
    return tuple(sorted(resolved))


def _secondary_disk_attachments(instance: object) -> list[dict[str, str]]:
    spec = getattr(instance, "spec", None)
    status = getattr(instance, "status", None)
    observed = list(getattr(status, "disk_attachments", []) or [])
    attachments: list[dict[str, str]] = []
    for disk in list(getattr(spec, "secondary_disks", []) or []):
        existing = getattr(getattr(disk, "existing_disk", None), "id", None)
        managed_name = getattr(getattr(disk, "managed_disk", None), "name", None)
        matches = [
            item
            for item in observed
            if (existing and str(getattr(item, "id", "")) == str(existing))
            or (managed_name and str(getattr(item, "name", "")) == str(managed_name))
        ]
        if len(matches) != 1 or not str(getattr(matches[0], "id", "")).strip():
            raise RuntimeError("protected NFS data-disk attachment is missing or ambiguous")
        item = matches[0]
        attachments.append(
            {
                "id": str(getattr(item, "id", "")).strip(),
                "deviceId": str(getattr(disk, "device_id", "")).strip(),
                "attachMode": str(getattr(disk, "attach_mode", "")).strip(),
                "managed": str(bool(getattr(item, "is_managed", False))).lower(),
            }
        )
    if not attachments:
        raise RuntimeError("protected VM-based NFS instance has no secondary data disk")
    if len({item["id"] for item in attachments}) != len(attachments):
        raise RuntimeError("protected NFS data-disk attachment identity is duplicated")
    return sorted(attachments, key=lambda item: item["id"])


def _instance_private_ips(instance: object) -> set[str]:
    status = getattr(instance, "status", None)
    addresses = {
        str(getattr(getattr(item, "ip_address", None), "address", "")).strip()
        for item in list(getattr(status, "network_interfaces", []) or [])
    }
    return {item for item in addresses if item}


def _allocation_cidr(allocation: object) -> str:
    status = getattr(allocation, "status", None)
    details = getattr(status, "details", None)
    return str(getattr(details, "allocated_cidr", "") or "").strip()


def _metadata_id(resource: object) -> str:
    return str(getattr(getattr(resource, "metadata", None), "id", "") or "").strip()


def _server_addresses(server: str, resolved: Sequence[str]) -> set[str]:
    addresses = {str(item).strip() for item in resolved if str(item).strip()}
    try:
        addresses.add(str(ipaddress.ip_address(server)))
    except ValueError:
        if not addresses:
            try:
                addresses.update(
                    str(item[4][0])
                    for item in socket.getaddrinfo(server, None, type=socket.SOCK_STREAM)
                    if item[4]
                )
            except OSError as exc:
                raise RuntimeError(
                    "protected VM-based NFS server address cannot be resolved"
                ) from exc
    if not addresses:
        raise RuntimeError("protected VM-based NFS server address cannot be resolved")
    return addresses


def _paged_list(
    *,
    request_factory: Any,
    request_call: Any,
    field_names: Sequence[str],
    context: str,
) -> list[object]:
    items: list[object] = []
    token = ""
    seen: set[str] = set()
    while True:
        response = request_call(request_factory(token)).wait()
        values: Sequence[object] = ()
        for field in field_names:
            candidate = getattr(response, field, None)
            if candidate is not None:
                values = list(candidate or [])
                break
        items.extend(values)
        next_token = str(getattr(response, "next_page_token", "") or "").strip()
        if not next_token:
            return items
        if next_token == token or next_token in seen:
            raise RuntimeError(f"{context} listing returned a repeated pagination token")
        seen.add(next_token)
        token = next_token


__all__ = [
    "LoginAllocationIdentity",
    "ProtectedStorageIdentity",
    "SfsFilesystemIdentity",
    "SfsProtectedStorageIdentity",
    "SOPERATOR_INFRASTRUCTURE_IDENTITY_SCHEMA",
    "SoperatorInfrastructureReceipt",
    "VmNfsProtectedStorageIdentity",
    "build_soperator_infrastructure_receipt",
    "discover_soperator_infrastructure_receipt",
    "soperator_infrastructure_receipt_from_payload",
    "soperator_local_sfs_kubernetes_bindings",
    "sfs_filesystem_observations_from_node_groups",
    "sfs_filesystem_observations_from_snapshot",
    "verify_soperator_infrastructure_identity",
]
