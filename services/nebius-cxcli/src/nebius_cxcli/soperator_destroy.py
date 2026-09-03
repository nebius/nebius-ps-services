"""Fail-closed, resumable protected destruction for registered Soperator targets."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .nebius_api_helpers import nebius_operation_id, wait_nebius_operation
from .sdk_auth import init_nebius_sdk
from .soperator_infrastructure_identity import (
    VmNfsProtectedStorageIdentity,
    _instance_private_ips,
    _secondary_disk_attachments,
    _server_addresses,
    soperator_infrastructure_receipt_from_payload,
)
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json

SOPERATOR_DESTROY_SCHEMA = "nebius-cxcli.soperator-destroy.v2"
_CHECKPOINTS = (
    "approved",
    "storage_verified_before_cleanup",
    "cleanup_complete",
    "delete_requested",
    "cluster_absent",
    "storage_verified_after_delete",
    "config_committed",
)


def _stable_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(_stable_json(value)).hexdigest()


@dataclass(frozen=True)
class SoperatorDestroyReceipt:
    schema: str
    target_ref: str
    ownership: str
    project_id: str
    cluster_id: str
    kubernetes_uid: str
    destroy_inventory: tuple[str, ...]
    preserve_inventory: tuple[str, ...]
    protected_storage_sha256: str
    infrastructure_receipt: Mapping[str, object]
    config_sha256: str
    post_cleanup_config_sha256: str
    approval_fingerprint: str
    checkpoints: tuple[str, ...] = ()
    delete_operation_id: str = ""
    status: str = "planned"
    failure_classification: str = ""

    def __post_init__(self) -> None:
        if self.schema != SOPERATOR_DESTROY_SCHEMA:
            raise ValueError("Soperator destroy receipt schema is unsupported")
        if self.ownership not in {"managed", "onboarded"}:
            raise ValueError("Soperator destroy ownership is unsupported")
        if not all((self.target_ref, self.project_id, self.cluster_id, self.kubernetes_uid)):
            raise ValueError("Soperator destroy identity is incomplete")
        if self.destroy_inventory != tuple(sorted(set(self.destroy_inventory))):
            raise ValueError("destroy inventory must be unique and sorted")
        if self.preserve_inventory != tuple(sorted(set(self.preserve_inventory))):
            raise ValueError("preserve inventory must be unique and sorted")
        if not self.destroy_inventory or not self.preserve_inventory:
            raise ValueError("destroy and preserve inventories must both be explicit")
        if set(self.destroy_inventory).intersection(self.preserve_inventory):
            raise ValueError("destroy and preserve inventories overlap")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.protected_storage_sha256):
            raise ValueError("protected storage identity is incomplete")
        infrastructure = soperator_infrastructure_receipt_from_payload(self.infrastructure_receipt)
        if infrastructure.receipt_sha256 != self.protected_storage_sha256:
            raise ValueError("destroy protected-storage receipt digest differs")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.config_sha256):
            raise ValueError("destroy config identity is incomplete")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.post_cleanup_config_sha256):
            raise ValueError("destroy post-cleanup config identity is incomplete")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.approval_fingerprint):
            raise ValueError("destroy approval fingerprint is incomplete")
        if self.checkpoints != _CHECKPOINTS[: len(self.checkpoints)]:
            raise ValueError("destroy checkpoints are unordered or unsupported")
        if self.status not in {"planned", "running", "failed", "complete"}:
            raise ValueError("Soperator destroy status is unsupported")
        if self.status == "complete" and self.checkpoints != _CHECKPOINTS:
            raise ValueError("completed destroy receipt has incomplete checkpoints")
        if self.status == "failed" and not re.fullmatch(
            r"[a-z][a-z0-9-]*", self.failure_classification
        ):
            raise ValueError("failed destroy receipt has no safe failure classification")
        if self.status != "failed" and self.failure_classification:
            raise ValueError("non-failed destroy receipt has a failure classification")
        if self.approval_fingerprint != _approval_fingerprint(self):
            raise ValueError("Soperator destroy immutable evidence was modified")

    def as_payload(self) -> dict[str, object]:
        return asdict(self)


def _approval_material(receipt: SoperatorDestroyReceipt) -> dict[str, object]:
    return {
        "schema": receipt.schema,
        "target_ref": receipt.target_ref,
        "ownership": receipt.ownership,
        "project_id": receipt.project_id,
        "cluster_id": receipt.cluster_id,
        "kubernetes_uid": receipt.kubernetes_uid,
        "destroy_inventory": receipt.destroy_inventory,
        "preserve_inventory": receipt.preserve_inventory,
        "protected_storage_sha256": receipt.protected_storage_sha256,
        "infrastructure_receipt": receipt.infrastructure_receipt,
        "config_sha256": receipt.config_sha256,
        "post_cleanup_config_sha256": receipt.post_cleanup_config_sha256,
    }


def _approval_fingerprint(receipt: SoperatorDestroyReceipt) -> str:
    return _sha256(_approval_material(receipt))


def build_soperator_destroy_receipt(
    *,
    target_ref: str,
    ownership: str,
    project_id: str,
    cluster_id: str,
    kubernetes_uid: str,
    destroy_inventory: Sequence[str],
    preserve_inventory: Sequence[str],
    protected_storage_sha256: str,
    infrastructure_receipt: Mapping[str, object],
    config_sha256: str,
    post_cleanup_config_sha256: str,
) -> SoperatorDestroyReceipt:
    draft = SoperatorDestroyReceipt.__new__(SoperatorDestroyReceipt)
    object.__setattr__(draft, "schema", SOPERATOR_DESTROY_SCHEMA)
    object.__setattr__(draft, "target_ref", str(target_ref).strip())
    object.__setattr__(draft, "ownership", str(ownership).strip())
    object.__setattr__(draft, "project_id", str(project_id).strip())
    object.__setattr__(draft, "cluster_id", str(cluster_id).strip())
    object.__setattr__(draft, "kubernetes_uid", str(kubernetes_uid).strip())
    object.__setattr__(
        draft,
        "destroy_inventory",
        tuple(sorted({str(item).strip() for item in destroy_inventory if str(item).strip()})),
    )
    object.__setattr__(
        draft,
        "preserve_inventory",
        tuple(sorted({str(item).strip() for item in preserve_inventory if str(item).strip()})),
    )
    object.__setattr__(draft, "protected_storage_sha256", protected_storage_sha256)
    object.__setattr__(
        draft,
        "infrastructure_receipt",
        json.loads(json.dumps(infrastructure_receipt, sort_keys=True)),
    )
    object.__setattr__(draft, "config_sha256", config_sha256)
    object.__setattr__(draft, "post_cleanup_config_sha256", post_cleanup_config_sha256)
    object.__setattr__(draft, "approval_fingerprint", _approval_fingerprint(draft))
    object.__setattr__(draft, "checkpoints", ())
    object.__setattr__(draft, "delete_operation_id", "")
    object.__setattr__(draft, "status", "planned")
    object.__setattr__(draft, "failure_classification", "")
    draft.__post_init__()
    return draft


def write_soperator_destroy_receipt(
    path: Path,
    receipt: SoperatorDestroyReceipt,
) -> None:
    write_owner_only_json(path, receipt.as_payload())


def load_soperator_destroy_receipt(path: Path) -> SoperatorDestroyReceipt:
    try:
        raw = read_owner_only_json(path, label="Soperator destroy receipt")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Soperator destroy receipt is invalid") from exc
    if not isinstance(raw, Mapping):
        raise RuntimeError("Soperator destroy receipt is malformed")
    try:
        return SoperatorDestroyReceipt(
            schema=str(raw.get("schema") or ""),
            target_ref=str(raw.get("target_ref") or ""),
            ownership=str(raw.get("ownership") or ""),
            project_id=str(raw.get("project_id") or ""),
            cluster_id=str(raw.get("cluster_id") or ""),
            kubernetes_uid=str(raw.get("kubernetes_uid") or ""),
            destroy_inventory=tuple(str(item) for item in raw.get("destroy_inventory", ())),
            preserve_inventory=tuple(str(item) for item in raw.get("preserve_inventory", ())),
            protected_storage_sha256=str(raw.get("protected_storage_sha256") or ""),
            infrastructure_receipt=(
                raw.get("infrastructure_receipt")
                if isinstance(raw.get("infrastructure_receipt"), Mapping)
                else {}
            ),
            config_sha256=str(raw.get("config_sha256") or ""),
            post_cleanup_config_sha256=str(raw.get("post_cleanup_config_sha256") or ""),
            approval_fingerprint=str(raw.get("approval_fingerprint") or ""),
            checkpoints=tuple(str(item) for item in raw.get("checkpoints", ())),
            delete_operation_id=str(raw.get("delete_operation_id") or ""),
            status=str(raw.get("status") or ""),
            failure_classification=str(raw.get("failure_classification") or ""),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc


def format_soperator_destroy_inventory(
    receipt: SoperatorDestroyReceipt,
) -> tuple[str, ...]:
    return (
        "DESTROY:",
        *(f"  - {item}" for item in receipt.destroy_inventory),
        "PRESERVE:",
        *(f"  - {item}" for item in receipt.preserve_inventory),
    )


def expected_soperator_destroy_confirmation(cluster_id: str) -> str:
    return f"destroy {str(cluster_id).strip()}"


def _is_not_found_error(exc: Exception) -> bool:
    status = getattr(exc, "status", None)
    code = getattr(status, "code", None)
    code_name = str(getattr(code, "name", "") or "").upper()
    code_text = str(code or "").upper()
    return code_name == "NOT_FOUND" or code_text in {"NOT_FOUND", "STATUSCODE.NOT_FOUND"}


def delete_onboarded_soperator_cluster(
    *,
    project_id: str,
    cluster_id: str,
    idempotency_key: str,
    timeout_seconds: int = 3600,
) -> str:
    from nebius.api.nebius.mk8s.v1 import ClusterServiceClient, DeleteClusterRequest

    sdk = init_nebius_sdk(parent_id=project_id, context="Soperator destroy")
    try:
        operation = (
            ClusterServiceClient(sdk)
            .delete(
                DeleteClusterRequest(id=cluster_id),
                idempotency_key=idempotency_key,
            )
            .wait()
        )
        operation_id = nebius_operation_id(operation)
        wait_nebius_operation(
            operation,
            timeout_seconds=timeout_seconds,
            action=f"delete Soperator MK8s cluster {cluster_id}",
        )
        return operation_id or idempotency_key
    finally:
        with suppress(Exception):
            sdk.sync_close()


def soperator_cluster_is_absent(*, project_id: str, cluster_id: str) -> bool:
    from nebius.api.nebius.mk8s.v1 import ClusterServiceClient, GetClusterRequest

    sdk = init_nebius_sdk(parent_id=project_id, context="Soperator destroy verification")
    try:
        try:
            ClusterServiceClient(sdk).get(GetClusterRequest(id=cluster_id)).wait()
        except Exception as exc:
            if _is_not_found_error(exc):
                return True
            raise
        return False
    finally:
        with suppress(Exception):
            sdk.sync_close()


def verify_soperator_filesystems_exist(
    *,
    project_id: str,
    filesystem_ids: Sequence[str],
) -> None:
    from nebius.api.nebius.compute.v1 import FilesystemServiceClient, GetFilesystemRequest

    identities = tuple(sorted({str(item).strip() for item in filesystem_ids if str(item).strip()}))
    if not identities:
        raise RuntimeError("Soperator destroy has no physical filesystem identities to verify")
    sdk = init_nebius_sdk(parent_id=project_id, context="Soperator storage verification")
    try:
        client = FilesystemServiceClient(sdk)
        for filesystem_id in identities:
            try:
                filesystem = client.get(GetFilesystemRequest(id=filesystem_id)).wait()
            except Exception as exc:
                if _is_not_found_error(exc):
                    raise RuntimeError(
                        f"preserved SFS filesystem disappeared: {filesystem_id}"
                    ) from exc
                raise
            observed_id = str(
                getattr(getattr(filesystem, "metadata", None), "id", "") or ""
            ).strip()
            if observed_id != filesystem_id:
                raise RuntimeError(
                    f"preserved SFS identity mismatch: expected {filesystem_id}, "
                    f"observed {observed_id or 'missing'}"
                )
    finally:
        with suppress(Exception):
            sdk.sync_close()


def verify_soperator_vm_nfs_exists(
    *,
    project_id: str,
    identity: VmNfsProtectedStorageIdentity,
    export_probe: Callable[[str, str], None] | None = None,
) -> None:
    from nebius.api.nebius.compute.v1 import (
        DiskServiceClient,
        GetDiskRequest,
        GetInstanceRequest,
        InstanceServiceClient,
    )
    from nebius.api.nebius.vpc.v1 import AllocationServiceClient, GetAllocationRequest

    sdk = init_nebius_sdk(parent_id=project_id, context="Soperator VM-NFS verification")
    try:
        instance = (
            InstanceServiceClient(sdk).get(GetInstanceRequest(id=identity.instance_id)).wait()
        )
        observed_instance_id = str(
            getattr(getattr(instance, "metadata", None), "id", "") or ""
        ).strip()
        if observed_instance_id != identity.instance_id:
            raise RuntimeError("preserved VM-NFS instance identity differs from the frozen receipt")
        observed_private_ips = tuple(sorted(_instance_private_ips(instance)))
        if observed_private_ips != identity.private_ips:
            raise RuntimeError(
                "preserved VM-NFS private-address identity differs from the frozen receipt"
            )
        resolved_server_addresses = _server_addresses(
            identity.server_address,
            identity.private_ips,
        )
        if not resolved_server_addresses.intersection(identity.private_ips):
            raise RuntimeError(
                "preserved VM-NFS server address no longer resolves to its frozen instance"
            )
        observed_attachments = _secondary_disk_attachments(instance)
        if (
            tuple(item["id"] for item in observed_attachments) != identity.data_disk_ids
            or _sha256(observed_attachments) != identity.attachment_sha256
        ):
            raise RuntimeError("preserved VM-NFS disk attachments differ from the frozen receipt")
        disk_client = DiskServiceClient(sdk)
        for disk_id in identity.data_disk_ids:
            disk = disk_client.get(GetDiskRequest(id=disk_id)).wait()
            observed_disk_id = str(getattr(getattr(disk, "metadata", None), "id", "") or "").strip()
            if observed_disk_id != disk_id:
                raise RuntimeError("preserved VM-NFS disk identity differs from the frozen receipt")
        allocation_client = AllocationServiceClient(sdk)
        for allocation_id in identity.allocation_ids:
            allocation = allocation_client.get(GetAllocationRequest(id=allocation_id)).wait()
            observed_allocation_id = str(
                getattr(getattr(allocation, "metadata", None), "id", "") or ""
            ).strip()
            if observed_allocation_id != allocation_id:
                raise RuntimeError(
                    "preserved VM-NFS allocation identity differs from the frozen receipt"
                )
        expected_export_sha256 = _sha256(
            {"server": identity.server_address, "path": identity.export_path}
        )
        if expected_export_sha256 != identity.export_sha256:
            raise RuntimeError("preserved VM-NFS export identity is internally inconsistent")
        (export_probe or _probe_soperator_vm_nfs_export)(
            identity.server_address,
            identity.export_path,
        )
    except Exception as exc:
        if _is_not_found_error(exc):
            raise RuntimeError("preserved VM-NFS infrastructure disappeared") from exc
        raise
    finally:
        with suppress(Exception):
            sdk.sync_close()


def _probe_soperator_vm_nfs_export(server: str, export_path: str) -> None:
    """Independently prove the NFS service advertises the exact frozen export."""

    showmount = shutil.which("showmount")
    if not showmount:
        raise RuntimeError("showmount is required to verify the preserved VM-NFS export")
    try:
        result = subprocess.run(
            [showmount, "--exports", server],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("preserved VM-NFS export probe timed out") from exc
    if result.returncode != 0:
        detail = str(result.stderr or result.stdout or "").strip().splitlines()
        raise RuntimeError(
            "preserved VM-NFS service/export probe failed" + (f": {detail[0]}" if detail else "")
        )
    exports = {
        line.split()[0]
        for line in result.stdout.splitlines()
        if line.strip().startswith("/") and line.split()
    }
    if export_path not in exports:
        raise RuntimeError("preserved VM-NFS service no longer advertises the frozen export")


def validate_soperator_destroy_terraform_plan(
    plan: Mapping[str, object],
    *,
    allowed_module_names: Sequence[str],
    expected_cluster_id: str,
    allowed_root_addresses: Sequence[str] = (),
) -> tuple[str, ...]:
    modules = {str(item).strip() for item in allowed_module_names if str(item).strip()}
    roots = {str(item).strip() for item in allowed_root_addresses if str(item).strip()}
    raw_changes = plan.get("resource_changes")
    if not isinstance(raw_changes, list):
        raise RuntimeError("saved Soperator destroy plan has no resource_changes inventory")
    destructive: list[str] = []
    approved_cluster_id = str(expected_cluster_id or "").strip()
    if not approved_cluster_id:
        raise RuntimeError("Soperator destroy plan has no approved MK8s cluster identity")
    selected_cluster_deletions: list[tuple[str, str]] = []
    for raw in raw_changes:
        if not isinstance(raw, Mapping):
            raise RuntimeError("saved Soperator destroy plan contains a malformed change")
        address = str(raw.get("address") or "").strip()
        change = raw.get("change")
        actions = change.get("actions") if isinstance(change, Mapping) else None
        action_set = tuple(str(item) for item in actions or ())
        if not action_set or set(action_set) <= {"read", "no-op"}:
            continue
        if "sfs" in address.lower() or "filesystem" in address.lower():
            raise RuntimeError(f"Soperator destroy plan touches protected SFS: {address}")
        if set(action_set) != {"delete"}:
            raise RuntimeError(
                f"Soperator destroy plan has non-delete or replacement actions for {address}: "
                + ", ".join(action_set)
            )
        module_match = re.match(r"^module\.([A-Za-z_][A-Za-z0-9_]*)\.", address)
        root_allowed = any(address == root or address.startswith(f"{root}[") for root in roots)
        if (module_match and module_match.group(1) in modules) or root_allowed:
            destructive.append(address)
            if (
                module_match
                and module_match.group(1) in modules
                and ".nebius_mk8s_v1_cluster." in address
            ):
                before = change.get("before") if isinstance(change, Mapping) else None
                prior_id = (
                    str(before.get("id") or "").strip() if isinstance(before, Mapping) else ""
                )
                selected_cluster_deletions.append((address, prior_id))
            continue
        raise RuntimeError(
            "Soperator destroy plan escapes the selected MK8s ownership closure: "
            + (address or "missing address")
        )
    if not destructive:
        raise RuntimeError("Soperator destroy plan contains no selected MK8s deletion")
    if (
        len(selected_cluster_deletions) != 1
        or selected_cluster_deletions[0][1] != approved_cluster_id
    ):
        raise RuntimeError("Soperator destroy plan must delete exactly one approved MK8s cluster")
    return tuple(sorted(destructive))


def _checkpoint(
    path: Path,
    receipt: SoperatorDestroyReceipt,
    checkpoint: str,
    *,
    delete_operation_id: str | None = None,
) -> SoperatorDestroyReceipt:
    completed = tuple(item for item in _CHECKPOINTS if item in {*receipt.checkpoints, checkpoint})
    updated = replace(
        receipt,
        checkpoints=completed,
        delete_operation_id=(
            receipt.delete_operation_id
            if delete_operation_id is None
            else str(delete_operation_id).strip()
        ),
        status="complete" if completed == _CHECKPOINTS else "running",
        failure_classification="",
    )
    write_soperator_destroy_receipt(path, updated)
    return updated


def run_soperator_destroy(
    *,
    receipt_path: Path,
    dry_run: bool,
    interactive: bool,
    confirmation: str | None,
    verify_storage_before_cleanup: Callable[[], None],
    cleanup_cluster: Callable[[], None],
    request_cluster_delete: Callable[[], str],
    cluster_is_absent: Callable[[str], bool],
    verify_preserved_storage: Callable[[], None],
    commit_config_cleanup: Callable[[], None],
) -> SoperatorDestroyReceipt:
    receipt = load_soperator_destroy_receipt(receipt_path)
    if receipt.status == "complete" or dry_run:
        return receipt
    try:
        if "approved" not in receipt.checkpoints:
            if not interactive:
                raise RuntimeError("Soperator destroy execution requires an interactive TTY")
            expected = expected_soperator_destroy_confirmation(receipt.cluster_id)
            if confirmation != expected:
                raise RuntimeError(f"Soperator destroy confirmation must exactly match: {expected}")
            receipt = _checkpoint(receipt_path, receipt, "approved")
        if "storage_verified_before_cleanup" not in receipt.checkpoints:
            verify_storage_before_cleanup()
            receipt = _checkpoint(receipt_path, receipt, "storage_verified_before_cleanup")
        if "cleanup_complete" not in receipt.checkpoints:
            cleanup_cluster()
            receipt = _checkpoint(receipt_path, receipt, "cleanup_complete")
        if "delete_requested" not in receipt.checkpoints:
            operation_id = request_cluster_delete()
            if not operation_id:
                raise RuntimeError("cluster delete request returned no operation identity")
            receipt = _checkpoint(
                receipt_path,
                receipt,
                "delete_requested",
                delete_operation_id=operation_id,
            )
        if "cluster_absent" not in receipt.checkpoints:
            if not cluster_is_absent(receipt.delete_operation_id):
                raise RuntimeError("selected Soperator cluster deletion is still in progress")
            receipt = _checkpoint(receipt_path, receipt, "cluster_absent")
        if "storage_verified_after_delete" not in receipt.checkpoints:
            verify_preserved_storage()
            receipt = _checkpoint(receipt_path, receipt, "storage_verified_after_delete")
        if "config_committed" not in receipt.checkpoints:
            commit_config_cleanup()
            receipt = _checkpoint(receipt_path, receipt, "config_committed")
        return receipt
    except Exception as exc:
        classification = re.sub(r"(?<!^)(?=[A-Z])", "-", type(exc).__name__).lower()
        failed = replace(
            receipt,
            status="failed",
            failure_classification=classification or "operation-error",
        )
        write_soperator_destroy_receipt(receipt_path, failed)
        raise


__all__ = [
    "SOPERATOR_DESTROY_SCHEMA",
    "SoperatorDestroyReceipt",
    "build_soperator_destroy_receipt",
    "delete_onboarded_soperator_cluster",
    "expected_soperator_destroy_confirmation",
    "format_soperator_destroy_inventory",
    "load_soperator_destroy_receipt",
    "run_soperator_destroy",
    "soperator_cluster_is_absent",
    "validate_soperator_destroy_terraform_plan",
    "verify_soperator_filesystems_exist",
    "verify_soperator_vm_nfs_exists",
    "write_soperator_destroy_receipt",
]
