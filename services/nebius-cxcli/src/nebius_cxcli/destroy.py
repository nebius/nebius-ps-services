"""Receipt-owned cloud destruction; Kubernetes is not part of this workflow."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .destroy_cloud import DestroyCloud, TerminalDestroyOperationError
from .destroy_resources import (
    check_disk,
    check_gpu,
    detached_references,
    verify_pvc_postconditions,
    verify_pvc_scope,
)
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json

DESTROY_SCHEMA = "nebius-cxcli.destroy.v1"
_CHECKPOINTS = (
    "approved",
    "cluster_absent",
    "gpu_clusters_absent",
    "storage_resolved",
    "state_reconciled",
    "config_committed",
    "baseline_cleared",
)


def digest(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )


@dataclass(frozen=True)
class DestroyReceipt:
    approved: Mapping[str, Any]
    approval_fingerprint: str
    checkpoints: tuple[str, ...] = ()
    requests: Mapping[str, Any] = field(default_factory=dict)
    status: str = "planned"
    failure_classification: str = ""

    def __post_init__(self) -> None:
        data = self.approved
        if digest(data) != self.approval_fingerprint:
            raise ValueError("Destroy immutable approval was modified")
        required = {
            "schema",
            "target_ref",
            "ownership",
            "project_id",
            "cluster_id",
            "delete_sfs",
            "preserve_pvc_disks",
            "inventory",
            "config_sha256",
            "post_cleanup_config_sha256",
            "backend",
            "generation",
            "terraform",
            "destroy_inventory",
            "preserve_inventory",
        }
        if set(data) != required or data["schema"] != DESTROY_SCHEMA:
            raise ValueError(
                "Unsupported destroy receipt: only v1 is supported; older receipts are not imported, archived or migrated"
            )
        if (
            data["ownership"] not in {"managed", "onboarded"}
            or type(data["delete_sfs"]) is not bool
            or type(data["preserve_pvc_disks"]) is not bool
        ):
            raise ValueError("Destroy ownership or storage disposition is invalid")
        if any(
            not isinstance(data[k], str) or not data[k]
            for k in (
                "target_ref",
                "project_id",
                "cluster_id",
                "config_sha256",
                "post_cleanup_config_sha256",
                "backend",
            )
        ):
            raise ValueError("Destroy receipt identity is incomplete")
        inventory = data["inventory"]
        if (
            inventory["cluster_id"] != data["cluster_id"]
            or inventory["project_id"] != data["project_id"]
        ):
            raise ValueError("Destroy cloud identity differs from approval")
        for key in (
            "node_group_ids",
            "filesystem_ids",
            "worker_ids",
            "gpu_cluster_ids",
            "managed_gpu_cluster_ids",
            "pvc_disk_ids",
            "unclassified_disk_ids",
            "excluded_disk_ids",
        ):
            if inventory[key] != sorted(set(inventory[key])):
                raise ValueError("Destroy cloud inventory must be unique and sorted")
        if set(inventory["pvc_disks"]) != set(inventory["pvc_disk_ids"]):
            raise ValueError("Destroy PVC provenance is incomplete")
        if set(inventory["excluded_disk_ids"]) & set(inventory["pvc_disk_ids"]):
            raise ValueError("Excluded disks cannot be approved for PVC deletion")
        if not set(inventory["managed_gpu_cluster_ids"]) <= set(inventory["gpu_cluster_ids"]):
            raise ValueError("Managed GPU inventory exceeds approval")
        if self.checkpoints != _CHECKPOINTS[: len(self.checkpoints)]:
            raise ValueError("Destroy checkpoints are unordered")
        if self.status not in {"planned", "running", "failed", "complete"}:
            raise ValueError("Destroy receipt status is invalid")
        if self.status == "complete" and self.checkpoints != _CHECKPOINTS:
            raise ValueError("Destroy completion is missing postconditions")
        for identity, request in (self.requests or {}).items():
            kind, _, identifier = identity.partition(":")
            allowed = (
                [data["cluster_id"]]
                if kind == "cluster"
                else inventory["filesystem_ids"]
                if kind == "filesystem" and data["delete_sfs"]
                else inventory["gpu_cluster_ids"]
                if kind == "gpu_cluster"
                else inventory["pvc_disk_ids"]
                if kind == "disk" and not data["preserve_pvc_disks"]
                else []
            )
            if (
                identifier not in allowed
                or request.get("resource_id") != identifier
                or request.get("kind") != kind
                or not request.get("key")
            ):
                raise ValueError("Destroy request exceeds approved scope")
            if request.get("request_sha256") != digest({"kind": kind, "id": identifier}):
                raise ValueError("Destroy request identity differs")

    @property
    def target_ref(self) -> str:
        return str(self.approved["target_ref"])

    @property
    def cluster_id(self) -> str:
        return str(self.approved["cluster_id"])

    @property
    def project_id(self) -> str:
        return str(self.approved["project_id"])

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema": DESTROY_SCHEMA,
            "target_ref": self.target_ref,
            "cluster_id": self.cluster_id,
            "approved": dict(self.approved),
            "approval_fingerprint": self.approval_fingerprint,
            "checkpoints": list(self.checkpoints),
            "requests": dict(self.requests or {}),
            "status": self.status,
            "failure_classification": self.failure_classification,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> DestroyReceipt:
        if payload.get("schema") != DESTROY_SCHEMA:
            raise RuntimeError(
                "Unsupported destroy receipt: only v1 is supported; older receipts are not imported, archived or migrated"
            )
        receipt = cls(
            approved=payload["approved"],
            approval_fingerprint=payload["approval_fingerprint"],
            checkpoints=tuple(payload["checkpoints"]),
            requests=payload["requests"],
            status=payload["status"],
            failure_classification=payload.get("failure_classification", ""),
        )
        if (
            payload.get("target_ref") != receipt.target_ref
            or payload.get("cluster_id") != receipt.cluster_id
        ):
            raise ValueError("Destroy projected identity differs")
        return receipt


def build_destroy_receipt(**approved: Any) -> DestroyReceipt:
    approved = copy.deepcopy(approved)
    approved["schema"] = DESTROY_SCHEMA
    return DestroyReceipt(approved, digest(approved))


def load_destroy_receipt(path: Path) -> DestroyReceipt:
    payload = read_owner_only_json(path, label="MK8s destroy receipt")
    if not isinstance(payload, Mapping):
        raise ValueError("Destroy receipt must be a mapping")
    return DestroyReceipt.from_payload(payload)


def write_destroy_receipt(path: Path, receipt: DestroyReceipt) -> None:
    write_owner_only_json(path, receipt.as_payload())


def expected_destroy_confirmation(
    cluster_id: str,
    *,
    delete_sfs: bool = False,
    filesystem_count: int = 0,
    gpu_cluster_count: int = 0,
    pvc_disk_count: int = 0,
) -> str:
    phrase = f"destroy {cluster_id}"
    for count, label in (
        (gpu_cluster_count, "GPU cluster" if gpu_cluster_count == 1 else "GPU clusters"),
        (pvc_disk_count, "PVC disk" if pvc_disk_count == 1 else "PVC disks"),
        (filesystem_count if delete_sfs else 0, "SFS"),
    ):
        if count:
            phrase += f" and delete {count} {label}"
    return phrase


def receipt_confirmation(receipt: DestroyReceipt) -> str:
    data = receipt.approved
    inventory = data["inventory"]
    return expected_destroy_confirmation(
        receipt.cluster_id,
        delete_sfs=data["delete_sfs"],
        filesystem_count=len(inventory["filesystem_ids"]),
        gpu_cluster_count=len(inventory["gpu_cluster_ids"]),
        pvc_disk_count=0 if data["preserve_pvc_disks"] else len(inventory["pvc_disk_ids"]),
    )


def format_destroy_inventory(receipt: DestroyReceipt) -> tuple[str, ...]:
    return (
        "DESTROY:",
        *(f"  - {item}" for item in receipt.approved["destroy_inventory"]),
        "PRESERVE:",
        *(
            f"  - {item}"
            for item in receipt.approved["preserve_inventory"] or ["No retained resources"]
        ),
        "All workloads and node-local ephemeral data disappear with the cluster.",
        *(
            ("Confirmed PVC disk deletion permanently removes persistent application data.",)
            if receipt.approved["inventory"]["pvc_disk_ids"]
            and not receipt.approved["preserve_pvc_disks"]
            else ()
        ),
        "Independent resource provisioning and attachment/reuse automation must remain paused through completion.",
    )


def run_destroy(
    *,
    receipt: DestroyReceipt,
    cloud: DestroyCloud,
    save: Callable[[DestroyReceipt], None],
    approval_mode: Literal["interactive", "yes", "resume"],
    confirmation: str | None,
    reconcile: Callable[[], None],
    publish: Callable[[], None],
    clear_baseline: Callable[[], None],
    verify_preserved: Callable[[], None] = lambda: None,
    progress: Callable[[str], object] = lambda _message: None,
) -> DestroyReceipt:
    """Persist intent before every effect, and postconditions before publication."""
    current = receipt
    if approval_mode not in {"interactive", "yes", "resume"}:
        raise ValueError("Unsupported destroy approval mode")
    data = current.approved
    inventory = data["inventory"]

    def commit(**updates: Any) -> None:
        nonlocal current
        candidate = replace(current, **updates)
        save(candidate)
        current = candidate

    def checkpoint(name: str) -> None:
        commit(
            checkpoints=(*current.checkpoints, name), status="running", failure_classification=""
        )

    def validate_delete(kind: str, identifier: str) -> None:
        if kind == "disk":
            check_disk(cloud, inventory["pvc_disks"][identifier], delete=True, workers=set())
        elif kind == "gpu_cluster":
            detached_references(cloud, {**inventory, "gpu_cluster_ids": [identifier]}, True)
            check_gpu(cloud, identifier, set())
        elif kind == "filesystem":
            cloud.check_exclusive({identifier}, current.cluster_id, set())
            cloud.check_filesystem(identifier, delete=True, detached=True)

    def request_update(request_id: str, **updates: Any) -> None:
        requests = copy.deepcopy(dict(current.requests))
        requests[request_id].update(updates)
        commit(requests=requests)

    def finish(kind: str, identifier: str) -> None:
        if cloud.get(kind, identifier, absent_ok=True) is not None:
            raise RuntimeError("Delete operation completed but resource absence is not yet proven")
        request_update(f"{kind}:{identifier}", absent=True, terminal_failed=False)

    def delete(kind: str, identifier: str, *, wait: bool = True) -> str | None:
        request_id = f"{kind}:{identifier}"
        existing = copy.deepcopy(current.requests.get(request_id))
        if existing and existing.get("absent"):
            if cloud.get(kind, identifier, absent_ok=True) is not None:
                raise RuntimeError("A resource recorded absent reappeared")
            return None
        previous_operations = []
        if existing and existing.get("terminal_failed"):
            if approval_mode != "yes" and (
                approval_mode != "interactive" or confirmation != receipt_confirmation(current)
            ):
                raise RuntimeError(
                    "Retrying a terminal failed operation requires renewed confirmation or --yes"
                )
            if cloud.get(kind, identifier, absent_ok=True) is None:
                finish(kind, identifier)
                return None
            if kind == "cluster":
                live = cloud.inventory(
                    current.cluster_id,
                    delete_sfs=data["delete_sfs"],
                    preserve_pvc_disks=data["preserve_pvc_disks"],
                    managed_gpu_ids=inventory["managed_gpu_cluster_ids"],
                )
                verify_pvc_scope(cloud, inventory, live, data["preserve_pvc_disks"])
                if any(
                    not set(live[k]) <= set(inventory[k])
                    for k in (
                        "node_group_ids",
                        "filesystem_ids",
                        "gpu_cluster_ids",
                        "pvc_disk_ids",
                    )
                ) or any(
                    row != inventory["pvc_disks"].get(k) for k, row in live["pvc_disks"].items()
                ):
                    raise RuntimeError(
                        "A retry cannot expand the approved cluster or storage scope"
                    )
            else:
                cloud.verify_cluster_absent(inventory)
                detached_references(cloud, inventory, data["preserve_pvc_disks"])
                validate_delete(kind, identifier)
            previous_operations = [
                *existing.get("previous_operations", []),
                existing["operation_id"],
            ]
            existing = None
        if existing is None:
            cloud.assert_held()
            absent = cloud.get(kind, identifier, absent_ok=True) is None
            if not absent:
                validate_delete(kind, identifier)
            existing = {
                "kind": kind,
                "resource_id": identifier,
                "key": str(uuid4()),
                "request_sha256": digest({"kind": kind, "id": identifier}),
                "operation_id": "",
                "absent": absent,
                "previous_operations": previous_operations,
            }
            requests = copy.deepcopy(dict(current.requests))
            requests[request_id] = existing
            commit(requests=requests)
            if absent:
                return None
            operation_id = cloud.submit_delete(kind, identifier, existing["key"])
        else:
            operation_id = existing["operation_id"]
            if not operation_id:
                if cloud.get(kind, identifier, absent_ok=True) is None:
                    finish(kind, identifier)
                    return None
                operation_id = cloud.recover_operation(kind, identifier, existing["key"])
        request_update(request_id, operation_id=operation_id)
        if not wait:
            return operation_id
        try:
            cloud.poll_delete(kind, identifier, operation_id)
        except TerminalDestroyOperationError:
            request_update(request_id, terminal_failed=True)
            raise
        finish(kind, identifier)
        return None

    def delete_disks() -> None:
        pending = iter(inventory["pvc_disk_ids"])
        active: dict[str, tuple[str, float]] = {}
        exhausted = False
        while active or not exhausted:
            while len(active) < 8 and not exhausted:
                identifier = next(pending, None)
                if identifier is None:
                    exhausted = True
                    break
                operation = delete("disk", identifier, wait=False)
                if operation:
                    active[identifier] = (operation, time.monotonic() + 3600)
            for identifier, (operation, deadline) in list(active.items()):
                try:
                    done = cloud.poll_delete_once("disk", identifier, operation)
                except TerminalDestroyOperationError:
                    request_update(f"disk:{identifier}", terminal_failed=True)
                    raise
                if done:
                    finish("disk", identifier)
                    del active[identifier]
                elif time.monotonic() >= deadline:
                    raise RuntimeError("PVC disk deletion is still running; rerun to resume")
                else:
                    progress(f"Waiting for disk deletion: {identifier} ({len(active)} in flight)")
            if active:
                time.sleep(2)

    if current.status == "complete":
        return current
    try:
        if not current.checkpoints:
            expected = receipt_confirmation(current)
            if approval_mode == "resume":
                raise RuntimeError("MK8s destroy requires an interactive TTY or explicit --yes")
            if approval_mode == "interactive" and confirmation != expected:
                raise RuntimeError(
                    "MK8s destroy confirmation does not match the approved inventory"
                )
            cloud.verify_scope(
                inventory,
                delete_sfs=data["delete_sfs"],
                preserve_pvc_disks=data["preserve_pvc_disks"],
            )
            verify_preserved()
            checkpoint("approved")
        if "cluster_absent" not in current.checkpoints:
            progress("Deleting the approved cluster through the Nebius SDK")
            if f"cluster:{current.cluster_id}" not in (current.requests or {}):
                cloud.verify_scope(
                    inventory,
                    delete_sfs=data["delete_sfs"],
                    preserve_pvc_disks=data["preserve_pvc_disks"],
                )
            delete("cluster", current.cluster_id)
            cloud.verify_cluster_absent(inventory)
            checkpoint("cluster_absent")
        if "gpu_clusters_absent" not in current.checkpoints:
            progress("Deleting approved dedicated GPU clusters through the Nebius SDK")
            cloud.verify_cluster_absent(inventory)
            detached_references(cloud, inventory, data["preserve_pvc_disks"])
            for identifier in inventory["gpu_cluster_ids"]:
                delete("gpu_cluster", identifier)
            checkpoint("gpu_clusters_absent")
        if "storage_resolved" not in current.checkpoints:
            progress("Resolving approved PVC disk and SFS dispositions")
            cloud.verify_cluster_absent(inventory)
            detached_references(cloud, inventory, data["preserve_pvc_disks"])
            if not data["preserve_pvc_disks"]:
                delete_disks()
            verify_pvc_postconditions(cloud, inventory, data["preserve_pvc_disks"])
            for identifier in inventory["filesystem_ids"]:
                if data["delete_sfs"]:
                    delete("filesystem", identifier)
                else:
                    cloud.check_filesystem(identifier, delete=False)
            verify_preserved()
            checkpoint("storage_resolved")
        # Re-prove these postconditions on every recovery, including publication-only recovery.
        for identifier in inventory["gpu_cluster_ids"]:
            if cloud.get("gpu_cluster", identifier, absent_ok=True) is not None:
                raise RuntimeError("Approved GPU cluster is not absent")
        verify_pvc_postconditions(cloud, inventory, data["preserve_pvc_disks"])
        cloud.verify_cluster_absent(inventory)
        for identifier in inventory["filesystem_ids"]:
            if data["delete_sfs"]:
                if cloud.get("filesystem", identifier, absent_ok=True) is not None:
                    raise RuntimeError("Approved deleted SFS is not absent")
            else:
                cloud.check_filesystem(identifier, delete=False)
        verify_preserved()
        if "state_reconciled" not in current.checkpoints:
            progress("Reconciling approved managed resources and Terraform state")
            reconcile()
            checkpoint("state_reconciled")
        # A durable checkpoint does not prove that this workstation still has the
        # approved files. Publication verifies or restores the exact postimage.
        progress("Publishing the approved remaining project generation")
        publish()
        if "config_committed" not in current.checkpoints:
            checkpoint("config_committed")
        if "baseline_cleared" not in current.checkpoints:
            clear_baseline()
            checkpoint("baseline_cleared")
        commit(status="complete", failure_classification="")
        return current
    except BaseException:
        # Do not create remote approval for rejected first confirmation/preflight.
        if current.checkpoints:
            commit(status="failed", failure_classification="destroy-incomplete")
        raise
