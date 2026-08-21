from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast

import yaml

from nebius_vpngw.schema import VMHARuntimeBinding

from .firewall_manager import update_firewall_from_config
from .frr_renderer import FRRRenderer
from .routing_guard import (
    acquire_routing_lock,
    enforce_routing_invariants,
    enforce_routing_invariants_locked,
    enforce_vm_ha_passive_routing_hygiene_locked,
    require_vm_ha_current_boot_ready,
    require_vm_ha_route_maintenance_ready,
)
from .state_store import StateStore
from .strongswan_renderer import StrongSwanRenderer
from .vm_ha.models import (
    DigestSet,
    PeerHeartbeat,
    StalePeerStateError,
    StateValidationError,
)
from .vm_ha.mtls import ManagedMTLSError, ManagedMTLSStore
from .vm_ha.runtime import VMHARuntimePorts, build_runtime_ports
from .vm_ha.store import AtomicGenerationStore
from .vm_ha.transport import PeerTransportError
from .vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ConfiguredRole,
    ControllerAction,
    ControllerCheckpoint,
    ControllerResult,
    ControllerSnapshot,
    DataPlaneMode,
    HAState,
    LocalReadiness,
    OwnershipContext,
    RecoverableController,
    RepairAttempt,
    RouteReconciliationContext,
    TransferContinuity,
    TransferIntent,
    VMHAController,
)
from .xfrm_manager import XFRMManager

CONFIG_PATH = Path("/etc/nebius-vpngw/config-resolved.yaml")
STATE_PATH = Path("/etc/nebius-vpngw/last-applied.json")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
VM_HA_STATE_DIR = Path("/var/lib/nebius-vpngw/vm-ha")
VM_HA_CHECKPOINT_PATH = VM_HA_STATE_DIR / "controller-checkpoint.json"
VM_HA_STATUS_PATH = VM_HA_STATE_DIR / "status.json"
VM_HA_MATERIALIZATION_PATH = VM_HA_STATE_DIR / "materialization.json"
VM_HA_GUARD_PATH = VM_HA_STATE_DIR / "guard.json"
VM_HA_FAILBACK_PATH = VM_HA_STATE_DIR / "manual-failback.json"
VM_HA_FAILOVER_PATH = VM_HA_STATE_DIR / "manual-failover.json"
VM_HA_EFFECT_RECEIPT_PATH = VM_HA_STATE_DIR / "effect-receipt.json"
VM_HA_TRANSFER_LINEAGE_PATH = VM_HA_STATE_DIR / "transfer-lineage.json"
VM_HA_PROMOTION_RECEIPT_PATH = VM_HA_STATE_DIR / "promotion-receipt.json"
VM_HA_REARM_STATUS_PATH = VM_HA_STATE_DIR / "rearm-status.json"
VM_HA_STANDBY_READY_PATH = VM_HA_STATE_DIR / "standby-ready.json"
VM_HA_APPLY_LOCK_PATH = VM_HA_STATE_DIR / "apply.lock"
VM_HA_APPLY_OWNER_ADOPTION_PATH = VM_HA_STATE_DIR / "apply-owner-adoption.json"
VM_HA_EMERGENCY_PATH = VM_HA_STATE_DIR / "emergency-active-only.json"

_CHECKPOINT_SCHEMA_V1 = "nebius-vpngw/vm-ha-controller-checkpoint-v1"
_CHECKPOINT_SCHEMA_V2 = "nebius-vpngw/vm-ha-controller-checkpoint-v2"
_CHECKPOINT_SCHEMA_V3 = "nebius-vpngw/vm-ha-controller-checkpoint-v3"
_CHECKPOINT_SCHEMA = "nebius-vpngw/vm-ha-controller-checkpoint-v4"
_STATUS_SCHEMA = "nebius-vpngw/vm-ha-status-v1"
_RUNTIME_BLOCKERS: tuple[str, ...] = ()
VM_HA_STANDBY_READY_MAX_AGE_SECONDS = 10.0


class _PriorGenerationPromotionReceipt(ValueError):
    """An otherwise exact promotion receipt from an older configuration generation."""


def _managed_mtls_status(
    state_dir: Path,
    *,
    peer: PeerHeartbeat | None = None,
    peer_fresh: bool = False,
) -> dict[str, Any]:
    """Return read-only, secret-free local and observed peer mTLS evidence."""

    try:
        local = ManagedMTLSStore(state_dir / "mtls", create=False).status().to_dict()
    except ManagedMTLSError:
        local = {
            "state": "invalid",
            "cluster_id": None,
            "node_id": None,
            "compute_id": None,
            "epoch": None,
            "certificate_fingerprint": None,
            "spki_fingerprint": None,
            "peer_fingerprints": [],
            "operation_id": None,
            "operation_kind": None,
            "target_epoch": None,
            "peer_target_epoch": None,
            "preserve_local": None,
            "inhibited": False,
            "inhibition_operation_id": None,
            "phase": None,
            "recovery": "inspect",
        }
    local["peer"] = (
        None
        if peer is None
        else {
            "node_id": peer.node_id,
            "boot_id": peer.boot_id,
            "sequence": peer.sequence,
            "epoch": peer.mtls_epoch,
            "certificate_fingerprint": peer.certificate_fingerprint,
            "fresh": peer_fresh,
        }
    )
    return local


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a private durable JSON record and fsync its directory."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _digests_from_dict(payload: object) -> DigestSet:
    if not isinstance(payload, dict) or set(payload) != {
        "configuration",
        "static_routes",
        "bgp_policy",
    }:
        raise ValueError("checkpoint digests have an invalid shape")
    return DigestSet(
        configuration=str(payload["configuration"]),
        static_routes=str(payload["static_routes"]),
        bgp_policy=str(payload["bgp_policy"]),
    )


def _completed_effect_operation(path: Path) -> tuple[str, str] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema", "operation_id", "kind"}:
        raise ValueError("VM-HA effect receipt is invalid")
    if value["schema"] != "nebius-vpngw/vm-ha-effect-receipt-v1":
        raise ValueError("VM-HA effect receipt has an unsupported schema")
    return str(value["operation_id"]), str(value["kind"])


def controller_checkpoint_to_dict(checkpoint: ControllerCheckpoint) -> dict[str, Any]:
    """Serialize every recovery-relevant controller and pending-action identity."""

    pending: dict[str, Any] | None = None
    if checkpoint.pending_action is not None:
        action = checkpoint.pending_action
        pending = {
            "kind": action.kind.value,
            "operation_id": action.operation_id,
            "boot_id": action.boot_id,
            "target_node_id": action.target_node_id,
            "allocation_id": action.allocation_id,
            "ownership_epoch": action.ownership_epoch,
            "generation_id": action.generation_id,
            "digests": action.digests.to_dict(),
            "ownership_incarnation": action.ownership_incarnation,
            "takeover_fence_required": action.takeover_fence_required,
            "repair_deadline_at": action.repair_deadline_at,
            "repair_reasons": list(action.repair_reasons),
        }
    established = (
        asdict(checkpoint.established_ownership_context)
        if checkpoint.established_ownership_context is not None
        else None
    )
    continuity = checkpoint.transfer_continuity
    continuity_payload = (
        None
        if continuity is None
        else {
            "allocation_id": continuity.allocation_id,
            "attach_operation_id": continuity.attach_operation_id,
            "candidate_node_id": continuity.candidate_node_id,
            "digests": continuity.digests.to_dict(),
            "former_owner_node_id": continuity.former_owner_node_id,
            "generation_id": continuity.generation_id,
            "ownership_confirmed": continuity.ownership_confirmed,
            "ownership_incarnation": continuity.ownership_incarnation,
            "post_attach_revision": continuity.post_attach_revision,
            "pre_attach_revision": continuity.pre_attach_revision,
        }
    )
    repair = checkpoint.repair_attempt
    repair_payload = (
        None
        if repair is None
        else {
            "allocation_id": repair.allocation_id,
            "boot_id": repair.boot_id,
            "deadline_at": repair.deadline_at,
            "failure_fingerprint": list(repair.failure_fingerprint),
            "generation_id": repair.generation_id,
            "healthy_observations": repair.healthy_observations,
            "healthy_since": repair.healthy_since,
            "operation_id": repair.operation_id,
            "owner_node_id": repair.owner_node_id,
            "ownership_epoch": repair.ownership_epoch,
            "ownership_incarnation": repair.ownership_incarnation,
            "started_at": repair.started_at,
        }
    )
    return {
        "schema": _CHECKPOINT_SCHEMA,
        "sequence": checkpoint.sequence,
        "state": checkpoint.state.value,
        "suspect_since": checkpoint.suspect_since,
        "pending_action": pending,
        "established_ownership_context": established,
        "ownership_continuity_invalidated": checkpoint.ownership_continuity_invalidated,
        "ownership_incarnation": checkpoint.ownership_incarnation,
        "transfer_continuity": continuity_payload,
        "repair_attempt": repair_payload,
    }


def controller_checkpoint_from_dict(payload: object) -> ControllerCheckpoint:
    """Restore the exact checkpoint; malformed or partial state always fails closed."""

    common = {
        "schema",
        "sequence",
        "state",
        "suspect_since",
        "pending_action",
        "established_ownership_context",
        "ownership_continuity_invalidated",
        "ownership_incarnation",
    }
    if not isinstance(payload, dict):
        raise ValueError("controller checkpoint has an invalid shape")
    schema = payload.get("schema")
    expected = common
    if schema in {_CHECKPOINT_SCHEMA_V2, _CHECKPOINT_SCHEMA_V3, _CHECKPOINT_SCHEMA}:
        expected |= {"transfer_continuity"}
    if schema == _CHECKPOINT_SCHEMA:
        expected |= {"repair_attempt"}
    if set(payload) != expected:
        raise ValueError("controller checkpoint has an invalid shape")
    if schema not in {
        _CHECKPOINT_SCHEMA_V1,
        _CHECKPOINT_SCHEMA_V2,
        _CHECKPOINT_SCHEMA_V3,
        _CHECKPOINT_SCHEMA,
    }:
        raise ValueError("unsupported controller checkpoint schema")

    pending_payload = payload["pending_action"]
    pending: ControllerAction | None = None
    if pending_payload is not None:
        pending_expected = {
            "kind",
            "operation_id",
            "boot_id",
            "target_node_id",
            "allocation_id",
            "ownership_epoch",
            "generation_id",
            "digests",
            "ownership_incarnation",
        }
        if schema in {_CHECKPOINT_SCHEMA_V3, _CHECKPOINT_SCHEMA}:
            pending_expected.add("takeover_fence_required")
        if schema == _CHECKPOINT_SCHEMA:
            pending_expected.update({"repair_deadline_at", "repair_reasons"})
        if not isinstance(pending_payload, dict) or set(pending_payload) != pending_expected:
            raise ValueError("pending controller action has an invalid shape")
        takeover_fence_required = pending_payload.get("takeover_fence_required")
        if schema in {_CHECKPOINT_SCHEMA_V3, _CHECKPOINT_SCHEMA}:
            if not isinstance(takeover_fence_required, bool):
                raise ValueError("pending takeover fence requirement must be boolean")
        else:
            # Legacy checkpoints did not distinguish a historical ownership
            # incarnation from an in-flight takeover. Preserve their stricter
            # behavior so upgrades never weaken a checkpointed promotion.
            takeover_fence_required = int(pending_payload["ownership_incarnation"]) > 0
        repair_deadline_at = pending_payload.get("repair_deadline_at")
        if repair_deadline_at is not None:
            if isinstance(repair_deadline_at, bool) or not isinstance(
                repair_deadline_at, (int, float)
            ):
                raise ValueError("pending repair deadline must be numeric")
            repair_deadline_at = float(repair_deadline_at)
        repair_reasons_payload = pending_payload.get("repair_reasons", [])
        if not isinstance(repair_reasons_payload, list) or not all(
            isinstance(reason, str) and reason for reason in repair_reasons_payload
        ):
            raise ValueError("pending repair reasons have an invalid shape")
        pending = ControllerAction(
            kind=ActionKind(str(pending_payload["kind"])),
            operation_id=str(pending_payload["operation_id"]),
            boot_id=str(pending_payload["boot_id"]),
            target_node_id=str(pending_payload["target_node_id"]),
            allocation_id=str(pending_payload["allocation_id"]),
            ownership_epoch=str(pending_payload["ownership_epoch"]),
            generation_id=str(pending_payload["generation_id"]),
            digests=_digests_from_dict(pending_payload["digests"]),
            ownership_incarnation=int(pending_payload["ownership_incarnation"]),
            takeover_fence_required=takeover_fence_required,
            repair_deadline_at=repair_deadline_at,
            repair_reasons=tuple(repair_reasons_payload),
        )

    ownership_payload = payload["established_ownership_context"]
    ownership: OwnershipContext | None = None
    if ownership_payload is not None:
        if not isinstance(ownership_payload, dict) or set(ownership_payload) != {
            "owner_node_id",
            "allocation_id",
            "ownership_epoch",
        }:
            raise ValueError("established ownership context has an invalid shape")
        ownership = OwnershipContext(
            owner_node_id=str(ownership_payload["owner_node_id"]),
            allocation_id=str(ownership_payload["allocation_id"]),
            ownership_epoch=str(ownership_payload["ownership_epoch"]),
        )

    continuity: TransferContinuity | None = None
    if schema in {_CHECKPOINT_SCHEMA_V2, _CHECKPOINT_SCHEMA_V3, _CHECKPOINT_SCHEMA}:
        continuity_payload = payload["transfer_continuity"]
        if continuity_payload is not None:
            continuity_expected = {
                "allocation_id",
                "attach_operation_id",
                "candidate_node_id",
                "digests",
                "former_owner_node_id",
                "generation_id",
                "ownership_confirmed",
                "ownership_incarnation",
                "post_attach_revision",
                "pre_attach_revision",
            }
            if (
                not isinstance(continuity_payload, dict)
                or set(continuity_payload) != continuity_expected
            ):
                raise ValueError("transfer continuity has an invalid shape")
            if not isinstance(continuity_payload["ownership_confirmed"], bool):
                raise ValueError("transfer continuity ownership_confirmed must be boolean")
            continuity = TransferContinuity(
                attach_operation_id=str(continuity_payload["attach_operation_id"]),
                allocation_id=str(continuity_payload["allocation_id"]),
                former_owner_node_id=str(continuity_payload["former_owner_node_id"]),
                candidate_node_id=str(continuity_payload["candidate_node_id"]),
                generation_id=str(continuity_payload["generation_id"]),
                digests=_digests_from_dict(continuity_payload["digests"]),
                ownership_incarnation=int(continuity_payload["ownership_incarnation"]),
                pre_attach_revision=str(continuity_payload["pre_attach_revision"]),
                post_attach_revision=(
                    None
                    if continuity_payload["post_attach_revision"] is None
                    else str(continuity_payload["post_attach_revision"])
                ),
                ownership_confirmed=bool(continuity_payload["ownership_confirmed"]),
            )

    repair_attempt: RepairAttempt | None = None
    if schema == _CHECKPOINT_SCHEMA:
        repair_payload = payload["repair_attempt"]
        repair_expected = {
            "allocation_id",
            "boot_id",
            "deadline_at",
            "failure_fingerprint",
            "generation_id",
            "healthy_observations",
            "healthy_since",
            "operation_id",
            "owner_node_id",
            "ownership_epoch",
            "ownership_incarnation",
            "started_at",
        }
        if repair_payload is not None:
            if not isinstance(repair_payload, dict) or set(repair_payload) != repair_expected:
                raise ValueError("repair attempt has an invalid shape")
            fingerprint = repair_payload["failure_fingerprint"]
            if not isinstance(fingerprint, list) or not all(
                isinstance(reason, str) and reason for reason in fingerprint
            ):
                raise ValueError("repair attempt fingerprint has an invalid shape")
            repair_attempt = RepairAttempt(
                operation_id=str(repair_payload["operation_id"]),
                owner_node_id=str(repair_payload["owner_node_id"]),
                allocation_id=str(repair_payload["allocation_id"]),
                ownership_epoch=str(repair_payload["ownership_epoch"]),
                ownership_incarnation=int(repair_payload["ownership_incarnation"]),
                generation_id=str(repair_payload["generation_id"]),
                boot_id=str(repair_payload["boot_id"]),
                failure_fingerprint=tuple(fingerprint),
                started_at=float(repair_payload["started_at"]),
                deadline_at=float(repair_payload["deadline_at"]),
                healthy_since=(
                    None
                    if repair_payload["healthy_since"] is None
                    else float(repair_payload["healthy_since"])
                ),
                healthy_observations=int(repair_payload["healthy_observations"]),
            )

    if not isinstance(payload["ownership_continuity_invalidated"], bool):
        raise ValueError("controller checkpoint invalidation flag must be boolean")
    if isinstance(payload["ownership_incarnation"], bool) or not isinstance(
        payload["ownership_incarnation"], int
    ):
        raise ValueError("controller checkpoint ownership incarnation must be an integer")
    invalidated = payload["ownership_continuity_invalidated"]
    incarnation = payload["ownership_incarnation"]
    state = HAState(str(payload["state"]))
    if schema == _CHECKPOINT_SCHEMA_V1:
        if pending is not None and pending.kind is ActionKind.ATTACH_CANDIDATE:
            continuity = TransferContinuity(
                attach_operation_id=pending.operation_id,
                allocation_id=pending.allocation_id,
                former_owner_node_id=(
                    str(ownership.owner_node_id) if ownership is not None else "legacy-former-owner"
                ),
                candidate_node_id=pending.target_node_id,
                generation_id=pending.generation_id,
                digests=pending.digests,
                ownership_incarnation=pending.ownership_incarnation,
                pre_attach_revision=pending.ownership_epoch,
            )
        elif (
            pending is not None
            and pending.kind
            in {
                ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
                ActionKind.PREPARE_CANDIDATE_DATAPLANE,
                ActionKind.RECONCILE_ROUTES,
                ActionKind.ENABLE_ACTIVE,
            }
        ) or (pending is None and state in {HAState.OWNERSHIP_TRANSFER, HAState.PROMOTING}):
            was_invalidated = invalidated
            pending = None
            ownership = None
            continuity = None
            invalidated = True
            incarnation = max(1, incarnation + (0 if was_invalidated else 1))
            state = HAState.BLOCKED

    return ControllerCheckpoint(
        sequence=int(payload["sequence"]),
        state=state,
        suspect_since=(
            None if payload["suspect_since"] is None else float(payload["suspect_since"])
        ),
        pending_action=pending,
        established_ownership_context=ownership,
        ownership_continuity_invalidated=invalidated,
        ownership_incarnation=incarnation,
        transfer_continuity=continuity,
        repair_attempt=repair_attempt,
    )


class VMHACheckpointFileStore:
    """Private atomic CheckpointStore used by the recoverable controller shell."""

    def __init__(self, path: Path = VM_HA_CHECKPOINT_PATH) -> None:
        self.path = path

    def load(self) -> ControllerCheckpoint:
        if not self.path.exists():
            return ControllerCheckpoint()
        return controller_checkpoint_from_dict(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, checkpoint: ControllerCheckpoint) -> None:
        _atomic_write_json(self.path, controller_checkpoint_to_dict(checkpoint))


def _boot_id() -> str:
    boot_id = BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    if not boot_id:
        raise RuntimeError("kernel boot identity is unavailable")
    return boot_id


@dataclass(frozen=True)
class VMHASnapshotProviders:
    peer: Callable[[], tuple[PeerHeartbeat | None, float | None]]
    readiness: Callable[[], LocalReadiness]
    cloud: Callable[[], CloudObservation]
    data_plane: Callable[[], DataPlaneMode]
    routes: Callable[[], RouteReconciliationContext | None]
    apply_lock_operation_id: Callable[[], str | None] = lambda: None
    emergency_active_only: Callable[[], bool] = lambda: False
    transfer_intent: Callable[[], tuple[TransferIntent | None, bool]] = lambda: (None, False)
    completed_effect: Callable[[], tuple[str, str] | None] = lambda: None


def _strict_boolean_record(path: Path, *, schema: str, field: str) -> bool:
    """Read an optional durable gate; malformed state blocks promotion."""

    if not path.exists():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not (
        isinstance(payload, dict)
        and set(payload) == {"schema", field}
        and payload.get("schema") == schema
        and isinstance(payload.get(field), bool)
    ):
        raise ValueError(f"VM-HA {field.replace('_', '-')} record is invalid")
    return bool(payload[field])


def _strict_apply_lock_record(
    path: Path,
    *,
    cluster_id: str,
    node_id: str,
    generation_id: str,
) -> str | None:
    """Read a node-scoped apply lock; malformed or foreign state blocks."""

    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_keys = {
        "apply_locked",
        "cluster_id",
        "generation_id",
        "node_id",
        "operation_id",
        "schema",
    }
    operation_id = payload.get("operation_id") if isinstance(payload, dict) else None
    locked_generation_id = payload.get("generation_id") if isinstance(payload, dict) else None
    if not (
        isinstance(payload, dict)
        and set(payload) == expected_keys
        and payload.get("schema") == "nebius-vpngw/vm-ha-apply-lock-v2"
        and payload.get("apply_locked") is True
        and payload.get("cluster_id") == cluster_id
        and payload.get("node_id") == node_id
        and isinstance(locked_generation_id, str)
        and len(locked_generation_id) == 64
        and all(char in "0123456789abcdef" for char in locked_generation_id)
        and isinstance(operation_id, str)
        and len(operation_id) == 64
        and all(char in "0123456789abcdef" for char in operation_id)
    ):
        raise ValueError("VM-HA apply-lock record is invalid or belongs to another node")
    # A managed generation change installs the target-generation lock before
    # replacing the currently active manifest.  The old runtime must honor that
    # exact node-scoped lock as a fail-closed gate during the handoff; rejecting
    # it would crash-loop the controller in the only interval the lock protects.
    # A stale valid lock is equally conservative: it keeps the node blocked until
    # the exact deploy operation removes or replaces it.
    del generation_id
    return operation_id


def _read_apply_owner_adoption(
    path: Path,
    *,
    config: Mapping[str, Any],
    apply_operation_id: str | None,
) -> dict[str, Any] | None:
    """Read the exact apply declaration that authorizes current-owner adoption."""

    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    node = config.get("node") or {}
    generation = config.get("generation") or {}
    binding = config.get("runtime_binding") or {}
    members = binding.get("nodes") if isinstance(binding, Mapping) else None
    local_node_id = str(node.get("node_id") or "")
    peer_ids = [
        str(member.get("node_id") or "")
        for member in members or ()
        if isinstance(member, Mapping) and member.get("node_id") != local_node_id
    ]
    expected_keys = {
        "allocation_id",
        "cluster_id",
        "digests",
        "generation_id",
        "node_id",
        "operation_id",
        "peer_node_id",
        "schema",
    }
    operation_id = payload.get("operation_id") if isinstance(payload, dict) else None
    if not (
        isinstance(payload, dict)
        and set(payload) == expected_keys
        and payload.get("schema") == "nebius-vpngw/vm-ha-apply-owner-adoption-v1"
        and len(peer_ids) == 1
        and payload.get("cluster_id") == config.get("cluster_id")
        and payload.get("node_id") == local_node_id
        and payload.get("peer_node_id") == peer_ids[0]
        and payload.get("allocation_id") == binding.get("shared_allocation_id")
        and isinstance(operation_id, str)
        and len(operation_id) == 64
        and all(character in "0123456789abcdef" for character in operation_id)
        and (apply_operation_id is None or operation_id == apply_operation_id)
    ):
        raise ValueError("VM-HA apply-owner adoption record is invalid or foreign")
    try:
        recorded_digests = DigestSet.from_mapping(payload.get("digests"))
    except StateValidationError as error:
        raise ValueError("VM-HA apply-owner adoption record is invalid or foreign") from error
    recorded_generation = payload.get("generation_id")
    if not (
        isinstance(recorded_generation, str)
        and len(recorded_generation) == 64
        and all(character in "0123456789abcdef" for character in recorded_generation)
    ):
        raise ValueError("VM-HA apply-owner adoption record is invalid or foreign")
    if (
        recorded_generation != generation.get("generation_id")
        or recorded_digests.to_dict() != generation.get("digests")
    ):
        # The exact apply lock is installed before the staged manifest becomes
        # active. The old runtime ignores the well-formed target-generation
        # declaration while continuing to honor the lock itself.
        return None
    return payload


def _vm_ha_writer_inhibition_operation_id(
    *,
    state_dir: Path,
    cluster_id: str,
    node_id: str,
    generation_id: str,
) -> str | None:
    apply_operation = _strict_apply_lock_record(
        state_dir / VM_HA_APPLY_LOCK_PATH.name,
        cluster_id=cluster_id,
        node_id=node_id,
        generation_id=generation_id,
    )
    mtls_operation = ManagedMTLSStore(
        state_dir / "mtls", create=False
    ).inhibition_operation_id(
        cluster_id=cluster_id,
        node_id=node_id,
        generation_id=generation_id,
    )
    if apply_operation is not None and mtls_operation is not None:
        raise ValueError("VM-HA has conflicting writer inhibitions")
    return apply_operation or mtls_operation


def _manual_transfer_provider(
    *,
    state_dir: Path,
    config: Mapping[str, Any],
    path_name: str,
    schema: str,
    operation: str,
    configured_role: str,
) -> Callable[[], bool]:
    path = state_dir / path_name
    expected = {
        "cluster_id": config.get("cluster_id"),
        "node_id": (config.get("node") or {}).get("node_id"),
        "generation_id": (config.get("generation") or {}).get("generation_id"),
    }

    def requested() -> bool:
        if not path.exists():
            return False
        if (config.get("node") or {}).get("role") != configured_role:
            raise ValueError(f"VM-HA manual {operation} request is invalid for this role")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not (
            isinstance(payload, dict)
            and set(payload) == {"schema", "cluster_id", "node_id", "generation_id", "requested_at"}
            and payload.get("schema") == schema
            and all(payload.get(key) == value for key, value in expected.items())
            and isinstance(payload.get("requested_at"), (int, float))
            and not isinstance(payload.get("requested_at"), bool)
        ):
            raise ValueError(f"VM-HA manual {operation} request is invalid or stale")
        return True

    return requested


def _manual_failback_provider(*, state_dir: Path, config: Mapping[str, Any]) -> Callable[[], bool]:
    return _manual_transfer_provider(
        state_dir=state_dir,
        config=config,
        path_name=VM_HA_FAILBACK_PATH.name,
        schema="nebius-vpngw/vm-ha-manual-failback-v1",
        operation="failback",
        configured_role="active",
    )


def _manual_failover_provider(*, state_dir: Path, config: Mapping[str, Any]) -> Callable[[], bool]:
    return _manual_transfer_provider(
        state_dir=state_dir,
        config=config,
        path_name=VM_HA_FAILOVER_PATH.name,
        schema="nebius-vpngw/vm-ha-manual-failover-v1",
        operation="failover",
        configured_role="passive",
    )


def _read_transfer_lineage(path: Path, *, config: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read one exact sticky transfer lineage without using it as cloud authority."""

    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    node = config.get("node") or {}
    generation = config.get("generation") or {}
    binding = config.get("runtime_binding") or {}
    digests = generation.get("digests") or {}
    expected_keys = {
        "allocation_id",
        "candidate_node_id",
        "cluster_id",
        "digests",
        "first_operation_id",
        "former_owner_node_id",
        "generation_id",
        "intent",
        "schema",
        "started_at",
    }
    members = binding.get("nodes") if isinstance(binding, Mapping) else None
    local_node_id = str(node.get("node_id") or "")
    peer_ids = [
        str(member.get("node_id") or "")
        for member in members or ()
        if isinstance(member, Mapping) and member.get("node_id") != local_node_id
    ]
    try:
        intent = TransferIntent(payload.get("intent")) if isinstance(payload, dict) else None
    except ValueError as error:
        raise ValueError("VM-HA transfer lineage has an invalid typed intent") from error
    if not (
        isinstance(payload, dict)
        and set(payload) == expected_keys
        and payload.get("schema") == "nebius-vpngw/vm-ha-transfer-lineage-v1"
        and len(peer_ids) == 1
        and payload.get("cluster_id") == config.get("cluster_id")
        and payload.get("candidate_node_id") == local_node_id
        and payload.get("former_owner_node_id") == peer_ids[0]
        and payload.get("allocation_id") == binding.get("shared_allocation_id")
        and payload.get("generation_id") == generation.get("generation_id")
        and payload.get("digests") == digests
        and isinstance(payload.get("first_operation_id"), str)
        and bool(payload["first_operation_id"])
        and isinstance(payload.get("started_at"), (int, float))
        and not isinstance(payload.get("started_at"), bool)
        and intent is not None
    ):
        raise ValueError("VM-HA transfer lineage is invalid or stale")
    role = str(node.get("role") or "")
    if (intent is TransferIntent.PLANNED_FAILBACK and role != "active") or (
        intent in {TransferIntent.PLANNED_FAILOVER, TransferIntent.AUTOMATIC_FAILOVER}
        and role != "passive"
    ):
        raise ValueError("VM-HA transfer lineage is invalid for this configured role")
    return payload


def _read_promotion_receipt(path: Path, *, config: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read a committed promotion receipt bound to this exact local generation."""

    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    node = config.get("node") or {}
    generation = config.get("generation") or {}
    binding = config.get("runtime_binding") or {}
    digests = generation.get("digests") or {}
    members = binding.get("nodes") if isinstance(binding, Mapping) else None
    local_node_id = str(node.get("node_id") or "")
    peer_ids = [
        str(member.get("node_id") or "")
        for member in members or ()
        if isinstance(member, Mapping) and member.get("node_id") != local_node_id
    ]
    expected_keys = {
        "allocation_id",
        "cluster_id",
        "committed_at",
        "common_cutover_seconds",
        "digests",
        "former_owner_node_id",
        "generation_id",
        "intent",
        "owner_node_id",
        "ownership_epoch",
        "receipt_id",
        "route_operation_id",
        "schema",
    }
    try:
        intent = TransferIntent(payload.get("intent")) if isinstance(payload, dict) else None
    except ValueError as error:
        raise ValueError("VM-HA promotion receipt has an invalid typed intent") from error
    identity_and_shape_valid = bool(
        isinstance(payload, dict)
        and set(payload) == expected_keys
        and payload.get("schema") == "nebius-vpngw/vm-ha-promotion-receipt-v1"
        and len(peer_ids) == 1
        and payload.get("cluster_id") == config.get("cluster_id")
        and payload.get("owner_node_id") == local_node_id
        and payload.get("former_owner_node_id") == peer_ids[0]
        and payload.get("allocation_id") == binding.get("shared_allocation_id")
        and isinstance(payload.get("ownership_epoch"), str)
        and bool(payload["ownership_epoch"])
        and isinstance(payload.get("receipt_id"), str)
        and bool(payload["receipt_id"])
        and isinstance(payload.get("route_operation_id"), str)
        and bool(payload["route_operation_id"])
        and isinstance(payload.get("committed_at"), (int, float))
        and not isinstance(payload.get("committed_at"), bool)
        and math.isfinite(float(payload["committed_at"]))
        and isinstance(payload.get("common_cutover_seconds"), (int, float))
        and not isinstance(payload.get("common_cutover_seconds"), bool)
        and math.isfinite(float(payload["common_cutover_seconds"]))
        and float(payload["common_cutover_seconds"]) >= 0
        and intent is not None
    )
    if not identity_and_shape_valid:
        raise ValueError("VM-HA promotion receipt is invalid or stale")
    role = str(node.get("role") or "")
    if (intent is TransferIntent.PLANNED_FAILBACK and role != "active") or (
        intent in {TransferIntent.PLANNED_FAILOVER, TransferIntent.AUTOMATIC_FAILOVER}
        and role != "passive"
    ):
        raise ValueError("VM-HA promotion receipt is invalid for this configured role")
    if (
        payload.get("generation_id") == generation.get("generation_id")
        and payload.get("digests") == digests
    ):
        return payload
    prior_generation_id = payload.get("generation_id")
    try:
        prior_digests = DigestSet.from_mapping(payload.get("digests"))
    except StateValidationError as error:
        raise ValueError("VM-HA promotion receipt is invalid or stale") from error
    if not (
        isinstance(prior_generation_id, str)
        and len(prior_generation_id) == 64
        and all(character in "0123456789abcdef" for character in prior_generation_id)
        and prior_generation_id != generation.get("generation_id")
        and prior_digests.to_dict() != digests
    ):
        raise ValueError("VM-HA promotion receipt is invalid or stale")
    raise _PriorGenerationPromotionReceipt(
        "VM-HA promotion receipt is invalid or stale for the current generation"
    )


def _promotion_receipt_matches_current_owner(
    receipt: Mapping[str, Any] | None,
    *,
    cloud: CloudObservation,
    node_id: str,
) -> bool:
    """Confirm that a terminal receipt still names the uninterrupted cloud owner."""

    return bool(
        receipt is not None
        and receipt.get("owner_node_id") == node_id
        and receipt.get("allocation_id") == cloud.allocation_id
        and receipt.get("ownership_epoch") == cloud.ownership_epoch
        and cloud.local_attachment_exact(node_id)
    )


def _typed_transfer_intent_provider(
    *, state_dir: Path, config: Mapping[str, Any]
) -> Callable[[], tuple[TransferIntent | None, bool]]:
    manual_failback = _manual_failback_provider(state_dir=state_dir, config=config)
    manual_failover = _manual_failover_provider(state_dir=state_dir, config=config)
    lineage_path = state_dir / VM_HA_TRANSFER_LINEAGE_PATH.name

    def intent() -> tuple[TransferIntent | None, bool]:
        failback = manual_failback()
        failover = manual_failover()
        if failback and failover:
            raise ValueError("conflicting manual VM-HA transfer requests exist")
        requested = (
            TransferIntent.PLANNED_FAILBACK
            if failback
            else TransferIntent.PLANNED_FAILOVER
            if failover
            else None
        )
        lineage = _read_transfer_lineage(lineage_path, config=config)
        if lineage is None:
            return requested, False
        sticky = TransferIntent(str(lineage["intent"]))
        if requested is not None and requested is not sticky:
            raise ValueError("manual VM-HA request conflicts with sticky transfer lineage")
        return sticky, True

    return intent


class VMHASnapshotAdapter:
    """Build exact policy input from immutable binding and live adapters."""

    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        providers: VMHASnapshotProviders,
        state_dir: Path = VM_HA_STATE_DIR,
        clock: Callable[[], float] = time.time,
        boot_id: Callable[[], str] = _boot_id,
    ) -> None:
        node = config.get("node")
        generation = config.get("generation")
        binding = config.get("runtime_binding")
        if not isinstance(node, Mapping) or not isinstance(generation, Mapping):
            raise ValueError("VM-HA node and generation binding are required")
        if not isinstance(binding, Mapping):
            raise ValueError("authoritative VM-HA runtime binding is required")
        runtime_binding = VMHARuntimeBinding.model_validate(binding)
        nodes = runtime_binding.nodes
        local_node_id = str(node.get("node_id") or "")
        peer_ids = [item.node_id for item in nodes if item.node_id != local_node_id]
        if len(peer_ids) != 1:
            raise ValueError("VM-HA runtime binding does not identify one exact peer")
        digests = generation.get("digests")
        if not isinstance(digests, Mapping):
            raise ValueError("VM-HA generation digests are required")
        self.cluster_id = str(config.get("cluster_id") or "")
        self.local_node_id = local_node_id
        self.peer_node_id = peer_ids[0]
        self.configured_role = ConfiguredRole(str(node.get("role")))
        local_binding = next(item for item in nodes if item.node_id == local_node_id)
        if local_binding.role.value != self.configured_role.value:
            raise ValueError("VM-HA runtime binding does not match the configured node role")
        self.allocation_id = runtime_binding.shared_allocation_id
        self.route_runtime_id = runtime_binding.route_runtime_id
        self.config = config
        self.generation_id = str(generation.get("generation_id") or "")
        self.digests = DigestSet(
            configuration=str(digests.get("configuration") or ""),
            static_routes=str(digests.get("static_routes") or ""),
            bgp_policy=str(digests.get("bgp_policy") or ""),
        )
        if not (
            runtime_binding.cluster_id == self.cluster_id
            and runtime_binding.generation_id == self.generation_id
            and runtime_binding.configuration_digest == self.digests.configuration
            and runtime_binding.static_routes_digest == self.digests.static_routes
            and runtime_binding.bgp_policy_digest == self.digests.bgp_policy
        ):
            raise ValueError("VM-HA runtime binding does not match the node generation")
        self.providers = providers
        self.state_dir = state_dir
        self.clock = clock
        self.boot_id = boot_id
        self.last_snapshot: ControllerSnapshot | None = None
        self.last_apply_operation_id: str | None = None

    def observe(self) -> ControllerSnapshot:
        peer, peer_received_at = self.providers.peer()
        cloud = self.providers.cloud()
        if cloud.allocation_id != self.allocation_id:
            raise ValueError("VM-HA cloud observation has the wrong allocation identity")
        guard_path = self.state_dir / VM_HA_GUARD_PATH.name
        guard = json.loads(guard_path.read_text(encoding="utf-8")) if guard_path.exists() else {}
        if not isinstance(guard, Mapping):
            raise ValueError("VM-HA guard record is invalid")
        apply_operation_id = self.providers.apply_lock_operation_id()
        completed_effect = self.providers.completed_effect()
        transfer_intent, transfer_effect_started = self.providers.transfer_intent()
        adoption_path = self.state_dir / VM_HA_APPLY_OWNER_ADOPTION_PATH.name
        adoption = _read_apply_owner_adoption(
            adoption_path,
            config=self.config,
            apply_operation_id=apply_operation_id,
        )
        adoption_authorized = bool(
            adoption is not None and cloud.local_attachment_exact(self.local_node_id)
        )
        if adoption is not None and not adoption_authorized:
            raise ValueError("VM-HA apply-owner adoption does not match cloud ownership")
        promotion_receipt_path = self.state_dir / VM_HA_PROMOTION_RECEIPT_PATH.name
        if (
            adoption_authorized
            and apply_operation_id is not None
            and promotion_receipt_path.exists()
        ):
            try:
                _read_promotion_receipt(promotion_receipt_path, config=self.config)
            except _PriorGenerationPromotionReceipt:
                # The exact apply declaration and current-generation lock make
                # the prior receipt replaceable without trusting its old route
                # or policy digests. A current receipt is still committed only
                # after the ordinary terminal controller gates.
                _durably_unlink(promotion_receipt_path)
        if transfer_intent is TransferIntent.AUTOMATIC_FAILOVER and transfer_effect_started:
            lineage_path = self.state_dir / VM_HA_TRANSFER_LINEAGE_PATH.name
            if adoption_authorized:
                _durably_unlink(lineage_path)
                transfer_intent = None
                transfer_effect_started = False
            else:
                receipt = _read_promotion_receipt(
                    promotion_receipt_path,
                    config=self.config,
                )
                lineage = _read_transfer_lineage(lineage_path, config=self.config)
                if (
                    receipt is not None
                    and _promotion_receipt_matches_current_owner(
                        receipt,
                        cloud=cloud,
                        node_id=self.local_node_id,
                    )
                    and lineage is not None
                    and lineage.get("candidate_node_id") == self.local_node_id
                    and float(lineage.get("started_at") or 0.0)
                    >= float(receipt.get("committed_at") or 0.0)
                ):
                    # A controller restart behind apply fencing must not reinterpret
                    # the already-committed owner as a fresh automatic takeover. The
                    # unchanged ownership epoch distinguishes this from a later
                    # legitimate failover back to the same configured-passive node.
                    _durably_unlink(lineage_path)
                    transfer_intent = None
                    transfer_effect_started = False
        snapshot = ControllerSnapshot(
            now=self.clock(),
            boot_id=self.boot_id(),
            cluster_id=self.cluster_id,
            local_node_id=self.local_node_id,
            peer_node_id=self.peer_node_id,
            configured_role=self.configured_role,
            local_generation_id=self.generation_id,
            local_digests=self.digests,
            peer_heartbeat=peer,
            peer_received_at=peer_received_at,
            apply_locked=apply_operation_id is not None,
            emergency_active_only=self.providers.emergency_active_only(),
            readiness=self.providers.readiness(),
            cloud=cloud,
            guard_boot_id=str(guard.get("guard_boot_id")) if guard.get("guard_boot_id") else None,
            data_plane_mode=self.providers.data_plane(),
            routes_reconciled_context=self.providers.routes(),
            route_runtime_id=self.route_runtime_id,
            completed_effect_operation_id=(
                completed_effect[0] if completed_effect is not None else None
            ),
            transfer_intent=transfer_intent,
            transfer_effect_started=transfer_effect_started,
            apply_owner_adoption=adoption_authorized,
        )
        self.last_apply_operation_id = apply_operation_id
        self.last_snapshot = snapshot
        return snapshot


_TRANSFER_EFFECT_ACTIONS = frozenset(
    {
        ActionKind.STOP_FORMER_OWNER,
        ActionKind.DETACH_FORMER_ATTACHMENT,
        ActionKind.DETACH_CANDIDATE_FOR_REPROOF,
        ActionKind.ATTACH_CANDIDATE,
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        ActionKind.RECONCILE_ROUTES,
        ActionKind.ENABLE_ACTIVE,
    }
)

_INITIAL_OWNER_RECONCILIATION_ACTIONS = frozenset(
    {
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        ActionKind.RECONCILE_ROUTES,
        ActionKind.ENABLE_ACTIVE,
    }
)


def _record_transfer_effect_lineage(
    *,
    action: ControllerAction,
    snapshots: VMHASnapshotAdapter,
    config: Mapping[str, Any],
    path: Path,
    clock: Callable[[], float] = time.time,
) -> None:
    """Persist typed lineage before the first accepted cutover effect."""

    if action.kind not in _TRANSFER_EFFECT_ACTIONS:
        return
    snapshot = snapshots.last_snapshot
    if snapshot is None:
        raise RuntimeError("VM-HA transfer effect has no observed snapshot")
    existing = _read_transfer_lineage(path, config=config)
    intent = snapshot.transfer_intent
    promotion_receipt = _read_promotion_receipt(
        path.parent / VM_HA_PROMOTION_RECEIPT_PATH.name,
        config=config,
    )
    committed_owner_reconciliation = _promotion_receipt_matches_current_owner(
        promotion_receipt,
        cloud=snapshot.cloud,
        node_id=snapshot.local_node_id,
    )
    adoption = _read_apply_owner_adoption(
        path.parent / VM_HA_APPLY_OWNER_ADOPTION_PATH.name,
        config=config,
        apply_operation_id=getattr(snapshots, "last_apply_operation_id", None),
    )
    adopted_owner_reconciliation = bool(
        adoption is not None
        and snapshot.cloud.local_attachment_exact(snapshot.local_node_id)
    )
    if (
        intent is None
        and existing is None
        and action.kind in _INITIAL_OWNER_RECONCILIATION_ACTIONS
        and (
            snapshot.configured_role is ConfiguredRole.ACTIVE
            or committed_owner_reconciliation
            or adopted_owner_reconciliation
        )
        and not snapshot.transfer_effect_started
        and snapshot.cloud.local_attachment_exact(snapshot.local_node_id)
    ):
        # Apply and restart recovery temporarily fence both nodes before the
        # already-authoritative configured owner, or an owner with an exact
        # terminal promotion receipt for the unchanged cloud ownership epoch,
        # prepares its tunnel, routes, and forwarding again. Those local
        # owner-only steps are not a new ownership transfer and must not
        # manufacture a transfer lineage.
        return
    if intent is None:
        if snapshot.configured_role is not ConfiguredRole.PASSIVE:
            raise RuntimeError("configured active cannot begin an automatic VM-HA transfer")
        intent = TransferIntent.AUTOMATIC_FAILOVER
    if (intent is TransferIntent.PLANNED_FAILBACK) != (
        snapshot.configured_role is ConfiguredRole.ACTIVE
    ):
        if not (
            snapshot.configured_role is ConfiguredRole.PASSIVE
            and intent in {TransferIntent.PLANNED_FAILOVER, TransferIntent.AUTOMATIC_FAILOVER}
        ):
            raise RuntimeError("VM-HA transfer intent does not match the candidate role")

    if existing is not None:
        if TransferIntent(str(existing["intent"])) is not intent:
            raise RuntimeError("VM-HA transfer effect conflicts with durable lineage")
        return
    _atomic_write_json(
        path,
        {
            "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
            "intent": intent.value,
            "cluster_id": snapshot.cluster_id,
            "candidate_node_id": snapshot.local_node_id,
            "former_owner_node_id": snapshot.peer_node_id,
            "allocation_id": snapshot.cloud.allocation_id,
            "generation_id": snapshot.local_generation_id,
            "digests": snapshot.local_digests.to_dict(),
            "first_operation_id": action.operation_id,
            "started_at": clock(),
        },
    )


def _durably_unlink(path: Path) -> None:
    if not path.exists():
        return
    path.unlink()
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _commit_promotion_receipt(
    *,
    status: Mapping[str, Any],
    config: Mapping[str, Any],
    lineage_path: Path,
    receipt_path: Path,
    request_paths: tuple[Path, ...],
    adoption_path: Path | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any] | None:
    """Commit terminal promotion only from durable controller postconditions."""

    lineage = _read_transfer_lineage(lineage_path, config=config)
    adoption = (
        None
        if adoption_path is None
        else _read_apply_owner_adoption(
            adoption_path,
            config=config,
            apply_operation_id=None,
        )
    )
    if lineage is None and adoption is None:
        return None
    if lineage is not None:
        authority = {
            "allocation_id": lineage["allocation_id"],
            "cluster_id": lineage["cluster_id"],
            "digests": lineage["digests"],
            "former_owner_node_id": lineage["former_owner_node_id"],
            "generation_id": lineage["generation_id"],
            "intent": lineage["intent"],
            "owner_node_id": lineage["candidate_node_id"],
            "operation_id": lineage["first_operation_id"],
        }
    else:
        assert adoption is not None
        authority = {
            "allocation_id": adoption["allocation_id"],
            "cluster_id": adoption["cluster_id"],
            "digests": adoption["digests"],
            "former_owner_node_id": adoption["peer_node_id"],
            "generation_id": adoption["generation_id"],
            "intent": TransferIntent.APPLY_OWNER_ADOPTION.value,
            "owner_node_id": adoption["node_id"],
            "operation_id": adoption["operation_id"],
        }
    route = status.get("route_reconciliation")
    former_owner_state = status.get("former_owner_compute_state")
    if not (
        status.get("promotion_ready") is True
        and status.get("data_plane_mode") == DataPlaneMode.ACTIVE.value
        and status.get("observed_owner_node_id") == authority["owner_node_id"]
        and status.get("cluster_id") == authority["cluster_id"]
        and status.get("allocation_id") == authority["allocation_id"]
        and status.get("generation_id") == authority["generation_id"]
        and status.get("digests") == authority["digests"]
        and (
            former_owner_state == ComputeState.STOPPED.value
            if lineage is not None
            else former_owner_state
            in {ComputeState.STOPPED.value, ComputeState.RUNNING.value}
        )
        and status.get("former_attachment_absent") is True
        and status.get("candidate_attachment_exact") is True
        and status.get("ownership_re_read_exact") is True
        and isinstance(status.get("ownership_epoch"), str)
        and bool(status["ownership_epoch"])
        and status.get("apply_locked") is False
        and status.get("pending_operation_id") is None
        and isinstance(route, Mapping)
        and route.get("operation_id")
        and route.get("owner_node_id") == authority["owner_node_id"]
        and route.get("allocation_id") == authority["allocation_id"]
        and not any(path.exists() for path in request_paths)
    ):
        return None
    committed_at = clock()
    identity = {
        "allocation_id": authority["allocation_id"],
        "first_operation_id": authority["operation_id"],
        "generation_id": authority["generation_id"],
        "intent": authority["intent"],
        "owner_node_id": authority["owner_node_id"],
        "ownership_epoch": status.get("ownership_epoch"),
        "route_operation_id": route["operation_id"],
    }
    receipt_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt = {
        "schema": "nebius-vpngw/vm-ha-promotion-receipt-v1",
        "receipt_id": receipt_id,
        "intent": authority["intent"],
        "cluster_id": authority["cluster_id"],
        "owner_node_id": authority["owner_node_id"],
        "former_owner_node_id": authority["former_owner_node_id"],
        "allocation_id": authority["allocation_id"],
        "ownership_epoch": status.get("ownership_epoch"),
        "generation_id": authority["generation_id"],
        "digests": authority["digests"],
        "route_operation_id": route["operation_id"],
        "committed_at": committed_at,
        "common_cutover_seconds": (
            max(0.0, committed_at - float(lineage["started_at"]))
            if lineage is not None
            else 0.0
        ),
    }
    if receipt_path.exists():
        current = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not (
            isinstance(current, dict)
            and set(current) == set(receipt)
            and current.get("schema") == "nebius-vpngw/vm-ha-promotion-receipt-v1"
            and isinstance(current.get("receipt_id"), str)
            and bool(current["receipt_id"])
            and current.get("cluster_id") == authority["cluster_id"]
            and current.get("owner_node_id") == authority["owner_node_id"]
            and current.get("former_owner_node_id") == authority["former_owner_node_id"]
            and current.get("allocation_id") == authority["allocation_id"]
            and current.get("generation_id") == authority["generation_id"]
        ):
            raise ValueError("existing VM-HA promotion receipt is invalid or foreign")
    if not receipt_path.exists() or current.get("receipt_id") != receipt_id:
        _atomic_write_json(receipt_path, receipt)
    if lineage is not None:
        _durably_unlink(lineage_path)
    if adoption is not None and adoption_path is not None:
        _durably_unlink(adoption_path)
    return receipt


class VMHAEffectAdapter:
    """Dispatch exact controller effects and durably deduplicate operation IDs."""

    def __init__(
        self,
        handlers: Mapping[ActionKind, Callable[[ControllerAction], None]],
        *,
        receipt_path: Path = VM_HA_EFFECT_RECEIPT_PATH,
        clock: Callable[[], float] = time.monotonic,
        event_sink: Callable[[Mapping[str, object]], None] | None = None,
        before_effect: Callable[[ControllerAction], None] | None = None,
    ) -> None:
        if set(handlers) != set(ActionKind):
            raise ValueError("VM-HA effect adapter requires every controller action")
        self.handlers = dict(handlers)
        self.receipt_path = receipt_path
        self.clock = clock
        self.event_sink = event_sink or self._print_event
        self.before_effect = before_effect or (lambda _action: None)

    @staticmethod
    def _print_event(event: Mapping[str, object]) -> None:
        print(
            "[VM-HA-EFFECT] " + json.dumps(event, sort_keys=True, separators=(",", ":")),
            flush=True,
        )

    def _event(
        self,
        action: ControllerAction,
        *,
        event: str,
        started_at: float,
        error_type: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "action": action.kind.value,
            "event": event,
            "monotonic_ms": round(self.clock() * 1000, 3),
            "operation_id": action.operation_id,
            "schema": "nebius-vpngw/vm-ha-effect-event-v1",
            "target_node_id": action.target_node_id,
        }
        if event != "started":
            payload["duration_ms"] = round((self.clock() - started_at) * 1000, 3)
        if error_type is not None:
            payload["error_type"] = error_type
        self.event_sink(payload)

    def _completed_operation(self) -> tuple[str, str] | None:
        return _completed_effect_operation(self.receipt_path)

    def apply(self, action: ControllerAction) -> None:
        self.before_effect(action)
        started_at = self.clock()
        self._event(action, event="started", started_at=started_at)
        completed = self._completed_operation()
        if completed is not None and completed[0] == action.operation_id:
            if completed[1] != action.kind.value:
                raise ValueError("VM-HA effect receipt operation kind does not match")
            # A process stop restores the guard after an effect completed.  The
            # controller has independently re-proved replay safety, so the
            # idempotent handler must re-establish the postcondition.
        try:
            self.handlers[action.kind](action)
            _atomic_write_json(
                self.receipt_path,
                {
                    "schema": "nebius-vpngw/vm-ha-effect-receipt-v1",
                    "operation_id": action.operation_id,
                    "kind": action.kind.value,
                },
            )
        except Exception as error:
            self._event(
                action,
                event="failed",
                started_at=started_at,
                error_type=type(error).__name__,
            )
            raise
        self._event(action, event="completed", started_at=started_at)


class VMHAControllerRuntime:
    """Concrete durable composition of snapshot, policy, effect, and status ports."""

    def __init__(
        self,
        *,
        snapshots: VMHASnapshotAdapter,
        effects: VMHAEffectAdapter,
        checkpoints: VMHACheckpointFileStore,
        status_path: Path,
        guard: Callable[[], Mapping[str, Any]],
        peer_timeout_seconds: float = 10.0,
        suspicion_seconds: float = 5.0,
    ) -> None:
        self.snapshots = snapshots
        self.status_path = status_path
        self.guard = guard
        self.controller = RecoverableController(
            policy=VMHAController(
                peer_timeout_seconds=peer_timeout_seconds,
                suspicion_seconds=suspicion_seconds,
            ),
            snapshots=snapshots,
            checkpoints=checkpoints,
            effects=effects,
        )

    def _write_status(self, result: ControllerResult) -> dict[str, Any]:
        snapshot = self.snapshots.last_snapshot
        if snapshot is None:
            raise RuntimeError("VM-HA controller has no observed snapshot")
        ready = bool(result.forwarding_enabled and result.action is None)
        route_context = snapshot.routes_reconciled_context
        cold_standby = bool(
            snapshot.data_plane_mode is DataPlaneMode.PASSIVE
            and snapshot.readiness.cold_standby_ready
        )
        warm_standby = bool(
            snapshot.data_plane_mode is DataPlaneMode.PASSIVE and snapshot.readiness.promotion_ready
        )
        standby_tunnel_state = "cold" if cold_standby else "warm" if warm_standby else "not-standby"
        standby_reasons: list[str] = []
        standby_checks = (
            (snapshot.guard_boot_id == snapshot.boot_id, "cold-start-guard-stale"),
            (snapshot.data_plane_mode is DataPlaneMode.PASSIVE, "data-plane-not-passive"),
            (snapshot.cloud.authoritative, "cloud-ownership-unavailable"),
            (
                snapshot.cloud.observed_owner_node_id == snapshot.peer_node_id,
                "local-node-is-not-exact-non-owner",
            ),
            (snapshot.cloud.former_attachment_exact, "owner-shared-alias-not-exact"),
            (snapshot.cloud.candidate_attachment_absent, "local-shared-alias-present"),
            (snapshot.readiness.transfer_ready, "standby-transfer-readiness-unavailable"),
            (not snapshot.apply_locked, "apply-lock-held"),
            (result.checkpoint.pending_action is None, "controller-effect-pending"),
        )
        standby_reasons.extend(reason for passed, reason in standby_checks if not passed)
        if not snapshot.readiness.transfer_ready:
            standby_reasons.extend(snapshot.readiness.transfer_blocked_reasons)
        standby_reasons = list(dict.fromkeys(standby_reasons))
        standby_ready = not standby_reasons
        standby_path = self.status_path.parent / VM_HA_STANDBY_READY_PATH.name
        if standby_ready:
            _atomic_write_json(
                standby_path,
                {
                    "schema": "nebius-vpngw/vm-ha-standby-ready-v1",
                    "cluster_id": snapshot.cluster_id,
                    "node_id": snapshot.local_node_id,
                    "boot_id": snapshot.boot_id,
                    "guard_boot_id": snapshot.guard_boot_id,
                    "data_plane_mode": DataPlaneMode.PASSIVE.value,
                    "observed_owner_node_id": snapshot.peer_node_id,
                    "allocation_id": snapshot.cloud.allocation_id,
                    "ownership_epoch": snapshot.cloud.ownership_epoch,
                    "generation_id": snapshot.local_generation_id,
                    "digests": snapshot.local_digests.to_dict(),
                    "route_runtime_id": snapshot.route_runtime_id,
                    "service_healthy": True,
                    "static_ready": snapshot.readiness.static_ready,
                    "bgp_ready": snapshot.readiness.bgp_ready,
                    "xfrm_ready": snapshot.readiness.xfrm_ready,
                    "apply_locked": False,
                    "pending_operation_id": None,
                    "ready_at": snapshot.now,
                },
            )
        else:
            _durably_unlink(standby_path)
        rearm_phase = "not-owner"
        rearm_reason: str | None = None
        phase_durations: dict[str, float | None] = {
            "preparation": None,
            "detection_repair": None,
            "common_cutover": None,
            "redundancy_restoration": None,
        }
        if snapshot.cloud.observed_owner_node_id == snapshot.local_node_id:
            rearm_path = self.status_path.parent / VM_HA_REARM_STATUS_PATH.name
            if rearm_path.exists():
                try:
                    rearm = json.loads(rearm_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    rearm_phase = "blocked"
                    rearm_reason = "rearm-status-invalid"
                else:
                    if not (
                        isinstance(rearm, dict)
                        and set(rearm)
                        == {
                            "completed_at",
                            "operation_id",
                            "phase",
                            "promotion_receipt_id",
                            "reason",
                            "schema",
                            "updated_at",
                        }
                        and rearm.get("schema") == "nebius-vpngw/vm-ha-rearm-status-v1"
                        and rearm.get("phase")
                        in {"idle", "inhibited", "blocked", "starting", "running"}
                        and (rearm.get("reason") is None or isinstance(rearm.get("reason"), str))
                        and (
                            rearm.get("promotion_receipt_id") is None
                            or (
                                isinstance(rearm.get("promotion_receipt_id"), str)
                                and bool(rearm["promotion_receipt_id"])
                            )
                        )
                        and (
                            rearm.get("operation_id") is None
                            or (
                                isinstance(rearm.get("operation_id"), str)
                                and bool(rearm["operation_id"])
                            )
                        )
                        and (
                            rearm.get("completed_at") is None
                            or (
                                isinstance(rearm.get("completed_at"), (int, float))
                                and not isinstance(rearm.get("completed_at"), bool)
                            )
                        )
                        and isinstance(rearm.get("updated_at"), (int, float))
                        and not isinstance(rearm.get("updated_at"), bool)
                        and math.isfinite(float(rearm["updated_at"]))
                    ):
                        rearm_phase = "blocked"
                        rearm_reason = "rearm-status-invalid"
                    else:
                        rearm_phase = str(rearm["phase"])
                        rearm_reason = (
                            None if rearm.get("reason") is None else str(rearm.get("reason"))
                        )
            else:
                rearm_phase = "idle"
                rearm_reason = "rearm-status-unavailable"
            promotion_path = self.status_path.parent / VM_HA_PROMOTION_RECEIPT_PATH.name
            if promotion_path.exists():
                try:
                    promotion = json.loads(promotion_path.read_text(encoding="utf-8"))
                    if not (
                        isinstance(promotion, dict)
                        and promotion.get("schema") == "nebius-vpngw/vm-ha-promotion-receipt-v1"
                        and promotion.get("owner_node_id") == snapshot.local_node_id
                        and isinstance(promotion.get("committed_at"), (int, float))
                        and isinstance(promotion.get("common_cutover_seconds"), (int, float))
                        and not isinstance(promotion.get("committed_at"), bool)
                        and not isinstance(promotion.get("common_cutover_seconds"), bool)
                        and math.isfinite(float(promotion["committed_at"]))
                        and math.isfinite(float(promotion["common_cutover_seconds"]))
                    ):
                        raise ValueError
                    phase_durations["common_cutover"] = float(promotion["common_cutover_seconds"])
                    if rearm_phase == "running" and rearm_path.exists():
                        phase_durations["redundancy_restoration"] = max(
                            0.0,
                            float(rearm.get("completed_at") or 0.0)
                            - float(promotion["committed_at"]),
                        )
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    rearm_phase = "blocked"
                    rearm_reason = "promotion-receipt-invalid"
        peer = snapshot.peer_heartbeat
        peer_fresh = bool(
            peer is not None
            and snapshot.peer_received_at is not None
            and 0 <= snapshot.now - snapshot.peer_received_at < 10.0
        )
        peer_standby_ready = bool(
            peer_fresh
            and peer is not None
            and peer.observed_owner_id == snapshot.local_node_id
            and peer.service_healthy
            and peer.route_ready
            and peer.promotion_ready
        )
        redundancy_ready = bool(
            standby_ready
            or (ready and peer_standby_ready and rearm_phase == "running" and rearm_reason is None)
        )
        payload = {
            "schema": _STATUS_SCHEMA,
            "state": result.state.value,
            "reasons": list(result.reasons),
            "recovery_action": None,
            "controller_ready_boot_id": snapshot.boot_id if ready else None,
            "guard_boot_id": snapshot.guard_boot_id,
            "data_plane_mode": snapshot.data_plane_mode.value,
            "cluster_id": snapshot.cluster_id,
            "node_id": snapshot.local_node_id,
            "configured_role": snapshot.configured_role.value,
            "observed_owner_node_id": snapshot.cloud.observed_owner_node_id,
            "allocation_id": snapshot.cloud.allocation_id,
            "ownership_epoch": snapshot.cloud.ownership_epoch,
            "former_owner_compute_state": snapshot.cloud.former_owner_compute_state.value,
            "former_attachment_absent": snapshot.cloud.former_attachment_absent,
            "former_attachment_exact": snapshot.cloud.former_attachment_exact,
            "candidate_attachment_exact": snapshot.cloud.candidate_attachment_exact,
            "candidate_attachment_absent": snapshot.cloud.candidate_attachment_absent,
            "ownership_re_read_exact": snapshot.cloud.ownership_re_read_exact,
            "generation_id": snapshot.local_generation_id,
            "digests": snapshot.local_digests.to_dict(),
            "apply_locked": snapshot.apply_locked,
            "apply_operation_id": self.snapshots.last_apply_operation_id,
            "route_runtime_id": snapshot.route_runtime_id,
            "route_reconciliation": (
                None
                if route_context is None
                else {
                    "allocation_id": route_context.allocation_id,
                    "digests": route_context.digests.to_dict(),
                    "generation_id": route_context.generation_id,
                    "operation_id": route_context.operation_id,
                    "owner_node_id": route_context.owner_node_id,
                    "ownership_epoch": route_context.ownership_epoch,
                    "ownership_incarnation": route_context.ownership_incarnation,
                    "route_runtime_id": route_context.route_runtime_id,
                }
            ),
            "promotion_ready": ready,
            "standby_ready": standby_ready,
            "standby_tunnel_state": standby_tunnel_state,
            "standby_readiness_reasons": standby_reasons,
            "redundancy_ready": redundancy_ready,
            "rearm_phase": rearm_phase,
            "rearm_reason": rearm_reason,
            "phase_durations_seconds": phase_durations,
            "pending_operation_id": (
                result.checkpoint.pending_action.operation_id
                if result.checkpoint.pending_action
                else None
            ),
            "mtls": _managed_mtls_status(
                self.status_path.parent,
                peer=peer,
                peer_fresh=peer_fresh,
            ),
            "repair": (
                None
                if result.checkpoint.repair_attempt is None
                else {
                    "deadline_at": result.checkpoint.repair_attempt.deadline_at,
                    "failure_fingerprint": list(
                        result.checkpoint.repair_attempt.failure_fingerprint
                    ),
                    "healthy_observations": (result.checkpoint.repair_attempt.healthy_observations),
                    "operation_id": result.checkpoint.repair_attempt.operation_id,
                    "owner_node_id": result.checkpoint.repair_attempt.owner_node_id,
                    "ownership_epoch": result.checkpoint.repair_attempt.ownership_epoch,
                    "remaining_seconds": max(
                        0.0,
                        result.checkpoint.repair_attempt.deadline_at - snapshot.now,
                    ),
                }
            ),
        }
        _atomic_write_json(self.status_path, payload)
        return payload

    def step(self) -> dict[str, Any]:
        try:
            return self._write_status(self.controller.step())
        except Exception as error:
            self.guard()
            try:
                self.snapshots.observe()
            except Exception:
                pass
            reason = (
                "route-ledger-identity-not-exact"
                if str(error) == "VM-HA ledger route identity is not exact in cloud state"
                else "controller-step-failed"
            )
            try:
                self._write_status(
                    ControllerResult(
                        state=HAState.BLOCKED,
                        reasons=(reason,),
                        forwarding_enabled=False,
                        action=None,
                        checkpoint=self.controller.checkpoints.load(),
                    )
                )
            except Exception:
                # Preserve the original controller error while the guard remains
                # the authoritative fail-closed state.
                pass
            raise


class DefaultVMHAControllerRuntime:
    """Service-owned composition with bounded peer I/O and guarded teardown."""

    def __init__(
        self,
        controller: VMHAControllerRuntime,
        ports: VMHARuntimePorts,
        *,
        boot_id: str,
        sequence_path: Path,
        guard_path: Path,
        config: Mapping[str, Any] | None = None,
        manual_failback_path: Path | None = None,
        manual_failover_path: Path | None = None,
        transfer_lineage_path: Path | None = None,
        promotion_receipt_path: Path | None = None,
        clock: Callable[[], float] = time.time,
        peer_timeout_seconds: float = 2.0,
        peer_send_attempts: int = 3,
        peer_retry_seconds: float = 0.05,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if peer_timeout_seconds <= 0 or peer_send_attempts < 1 or peer_retry_seconds < 0:
            raise ValueError("VM-HA peer retry bounds are invalid")
        self.controller = controller
        self.ports = ports
        self.boot_id = boot_id
        self.sequence_path = sequence_path
        self.guard_path = guard_path
        self.config = config
        self.manual_failback_path = manual_failback_path
        self.manual_failover_path = manual_failover_path
        self.transfer_lineage_path = transfer_lineage_path
        self.promotion_receipt_path = promotion_receipt_path
        self.clock = clock
        self.peer_timeout_seconds = peer_timeout_seconds
        self.peer_send_attempts = peer_send_attempts
        self.peer_retry_seconds = peer_retry_seconds
        self.sleeper = sleeper
        self.started_guarded = False
        self.active_reconcile_requested = False
        self.closed = False
        self.listener_stop = threading.Event()
        self.listener_thread: threading.Thread | None = None
        self.listener_error: BaseException | None = None

    def _reserve_heartbeat_sequence(self) -> int:
        sequence = 0
        if self.sequence_path.exists():
            value = json.loads(self.sequence_path.read_text(encoding="utf-8"))
            if not (
                isinstance(value, dict)
                and set(value) == {"schema", "boot_id", "next_sequence"}
                and value.get("schema") == "nebius-vpngw/vm-ha-heartbeat-sequence-v1"
                and isinstance(value.get("boot_id"), str)
                and bool(value["boot_id"])
                and isinstance(value.get("next_sequence"), int)
                and not isinstance(value.get("next_sequence"), bool)
                and value["next_sequence"] >= 0
            ):
                raise ValueError("VM-HA outbound heartbeat sequence is invalid")
            if value.get("boot_id") == self.boot_id:
                sequence = value["next_sequence"]
        _atomic_write_json(
            self.sequence_path,
            {
                "schema": "nebius-vpngw/vm-ha-heartbeat-sequence-v1",
                "boot_id": self.boot_id,
                "next_sequence": sequence + 1,
            },
        )
        return sequence

    def _listen_for_peer_state(self) -> None:
        while not self.listener_stop.is_set():
            try:
                self.ports.peer_runtime.poll(timeout_seconds=self.peer_timeout_seconds)
            except PeerTransportError:
                pass
            except (StalePeerStateError, StateValidationError):
                # Authenticated but invalid or replayed state is advisory.  The
                # peer runtime retains the last accepted heartbeat.
                pass
            except BaseException as error:
                self.listener_error = error
                self.listener_stop.set()
            if not self.listener_stop.is_set():
                self.listener_stop.wait(self.peer_retry_seconds)

    def _start_peer_listener(self) -> None:
        if self.listener_thread is not None:
            return
        self.listener_thread = threading.Thread(
            target=self._listen_for_peer_state,
            name="nebius-vpngw-vm-ha-peer-listener",
        )
        self.listener_thread.start()
        self.sleeper(self.peer_retry_seconds)

    def _exchange_peer_state(self) -> None:
        if self.listener_error is not None:
            raise RuntimeError("VM-HA peer listener failed") from self.listener_error
        heartbeat = self.ports.heartbeat(
            boot_id=self.boot_id,
            sequence=self._reserve_heartbeat_sequence(),
            clock=self.clock(),
        )

        for attempt in range(self.peer_send_attempts):
            try:
                self.ports.peer_runtime.send(heartbeat)
                return
            except PeerTransportError:
                if attempt + 1 < self.peer_send_attempts:
                    self.sleeper(self.peer_retry_seconds)

    def step(self) -> dict[str, Any]:
        if not self.started_guarded:
            self.ports.install_shutdown_guard(boot_id=self.boot_id)
            self.started_guarded = True
        self._start_peer_listener()
        self._exchange_peer_state()
        status = self.controller.step()
        if status.get("promotion_ready") is True:
            _atomic_write_json(
                self.guard_path,
                {
                    "schema": _STATUS_SCHEMA,
                    "guard_boot_id": self.boot_id,
                    "data_plane_mode": "active",
                    "installed_at": self.clock(),
                },
            )
            request_reconcile = getattr(
                getattr(self.ports, "data_plane", None), "request_agent_reconcile", None
            )
            if callable(request_reconcile) and not self.active_reconcile_requested:
                try:
                    request_reconcile()
                except Exception:
                    self.ports.install_shutdown_guard(boot_id=self.boot_id)
                    raise
                self.active_reconcile_requested = True
            request_path = {
                "active": self.manual_failback_path,
                "passive": self.manual_failover_path,
            }.get(self.ports.local.role.value)
            if (
                request_path is not None
                and status.get("observed_owner_node_id") == self.ports.local.node_id
                and request_path.exists()
            ):
                _durably_unlink(request_path)
            if (
                self.config is not None
                and self.transfer_lineage_path is not None
                and self.promotion_receipt_path is not None
            ):
                _commit_promotion_receipt(
                    status=status,
                    config=self.config,
                    lineage_path=self.transfer_lineage_path,
                    receipt_path=self.promotion_receipt_path,
                    adoption_path=(
                        self.promotion_receipt_path.parent
                        / VM_HA_APPLY_OWNER_ADOPTION_PATH.name
                    ),
                    request_paths=tuple(
                        path
                        for path in (self.manual_failback_path, self.manual_failover_path)
                        if path is not None
                    ),
                    clock=self.clock,
                )
        else:
            self.active_reconcile_requested = False
        return status

    def close(self, *, restore_guard: bool = True) -> None:
        if self.closed:
            return
        self.listener_stop.set()
        try:
            if restore_guard:
                self.ports.install_shutdown_guard(boot_id=self.boot_id)
        finally:
            try:
                if self.listener_thread is not None:
                    self.listener_thread.join(timeout=self.peer_timeout_seconds + 1.0)
                    if self.listener_thread.is_alive():
                        raise RuntimeError("VM-HA peer listener did not stop within its bound")
            finally:
                self.ports.close()
        self.closed = True


def _resolved_vm_ha_data_plane_config(config_path: Path) -> dict[str, Any]:
    resolved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(resolved, dict):
        raise RuntimeError("VM-HA resolved configuration is malformed")
    return resolved


def _prepare_vm_ha_active_data_plane(config_path: Path) -> None:
    """Restore owner-only firewall and BGP state before forwarding is enabled."""

    resolved = _resolved_vm_ha_data_plane_config(config_path)
    update_firewall_from_config(resolved, require_reload=True)
    FRRRenderer().render_and_apply(
        resolved,
        advertise_local_prefixes=True,
        require_reload=True,
    )


def _prepare_vm_ha_blocked_bgp(config_path: Path) -> None:
    """Install the non-owner deny-all export policy while the routing lock is held."""

    resolved = _resolved_vm_ha_data_plane_config(config_path)
    FRRRenderer().render_and_apply(
        resolved,
        advertise_local_prefixes=False,
        require_reload=True,
        prepare_vm_ha_controller_access=True,
    )


def build_default_vm_ha_controller_runtime(
    *,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = VM_HA_STATE_DIR,
    clock: Callable[[], float] = time.time,
    monotonic_clock: Callable[[], float] | None = None,
    boot_id: Callable[[], str] = _boot_id,
) -> DefaultVMHAControllerRuntime:
    """Build the only production VM-HA runtime from the installed manifest."""

    config = _read_vm_ha_config(config_path)
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    current_boot_id = boot_id()
    effective_monotonic_clock = monotonic_clock or (time.monotonic if clock is time.time else clock)
    ports = build_runtime_ports(
        config,
        state_dir=state_dir,
        replay_store=AtomicGenerationStore(state_dir / "generation-store"),
        clock=clock,
        monotonic_clock=effective_monotonic_clock,
        active_preparer=lambda: _prepare_vm_ha_active_data_plane(config_path),
        blocked_preparer=lambda: _prepare_vm_ha_blocked_bgp(config_path),
    )
    try:
        transfer_intent = _typed_transfer_intent_provider(state_dir=state_dir, config=config)
        controller = build_vm_ha_controller_runtime(
            config_path=config_path,
            providers=VMHASnapshotProviders(
                peer=ports.peer_runtime.observe,
                readiness=ports.routes.readiness,
                cloud=ports.cloud.observe,
                data_plane=ports.data_plane.mode,
                routes=ports.routes.receipt_context,
                apply_lock_operation_id=lambda: _vm_ha_writer_inhibition_operation_id(
                    state_dir=state_dir,
                    cluster_id=str(config.get("cluster_id") or ""),
                    node_id=str((config.get("node") or {}).get("node_id") or ""),
                    generation_id=str((config.get("generation") or {}).get("generation_id") or ""),
                ),
                emergency_active_only=lambda: _strict_boolean_record(
                    state_dir / VM_HA_EMERGENCY_PATH.name,
                    schema="nebius-vpngw/vm-ha-emergency-active-only-v1",
                    field="emergency_active_only",
                ),
                transfer_intent=transfer_intent,
            ),
            handlers=ports.handlers(),
            state_dir=state_dir,
            clock=effective_monotonic_clock,
            boot_id=lambda: current_boot_id,
        )
        return DefaultVMHAControllerRuntime(
            controller,
            ports,
            boot_id=current_boot_id,
            sequence_path=state_dir / "outbound-heartbeat-sequence.json",
            guard_path=state_dir / VM_HA_GUARD_PATH.name,
            config=config,
            manual_failback_path=state_dir / VM_HA_FAILBACK_PATH.name,
            manual_failover_path=state_dir / VM_HA_FAILOVER_PATH.name,
            transfer_lineage_path=state_dir / VM_HA_TRANSFER_LINEAGE_PATH.name,
            promotion_receipt_path=state_dir / VM_HA_PROMOTION_RECEIPT_PATH.name,
            clock=clock,
        )
    except Exception:
        ports.close()
        raise


def build_vm_ha_controller_runtime(
    *,
    config_path: Path,
    providers: VMHASnapshotProviders,
    handlers: Mapping[ActionKind, Callable[[ControllerAction], None]],
    state_dir: Path = VM_HA_STATE_DIR,
    clock: Callable[[], float] = time.time,
    boot_id: Callable[[], str] = _boot_id,
) -> VMHAControllerRuntime:
    """Construct the verified controller only from a complete node binding."""

    config = _read_vm_ha_config(config_path)
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    snapshots = VMHASnapshotAdapter(
        config=config,
        providers=replace(
            providers,
            completed_effect=lambda: _completed_effect_operation(
                state_dir / VM_HA_EFFECT_RECEIPT_PATH.name
            ),
        ),
        state_dir=state_dir,
        clock=clock,
        boot_id=boot_id,
    )
    return VMHAControllerRuntime(
        snapshots=snapshots,
        effects=VMHAEffectAdapter(
            handlers,
            receipt_path=state_dir / VM_HA_EFFECT_RECEIPT_PATH.name,
            before_effect=lambda action: _record_transfer_effect_lineage(
                action=action,
                snapshots=snapshots,
                config=config,
                path=state_dir / VM_HA_TRANSFER_LINEAGE_PATH.name,
            ),
        ),
        checkpoints=VMHACheckpointFileStore(state_dir / VM_HA_CHECKPOINT_PATH.name),
        status_path=state_dir / VM_HA_STATUS_PATH.name,
        guard=lambda: install_vm_ha_cold_start_guard(
            state_dir=state_dir,
            boot_id=boot_id(),
        ),
    )


def _vm_ha_config_from_resolved(
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    vm_ha = payload.get("vm_ha")
    if not isinstance(vm_ha, dict):
        return None
    if vm_ha.get("enabled") is False:
        return None
    resolved = dict(vm_ha)
    for data_plane_key in ("gateway", "connections"):
        if data_plane_key in payload:
            resolved[data_plane_key] = payload[data_plane_key]
    return resolved


def _read_vm_ha_config(path: Path = CONFIG_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _vm_ha_config_from_resolved(payload) if isinstance(payload, Mapping) else None


def vm_ha_runtime_blockers() -> tuple[str, ...]:
    """Return release-level capability blockers for explicit VM-HA activation."""

    return _RUNTIME_BLOCKERS


def _blocked_vm_ha_status(
    *, state_dir: Path, checkpoint: ControllerCheckpoint | None = None
) -> dict[str, Any]:
    checkpoint = (
        checkpoint or VMHACheckpointFileStore(state_dir / VM_HA_CHECKPOINT_PATH.name).load()
    )
    guard_path = state_dir / VM_HA_GUARD_PATH.name
    guard = json.loads(guard_path.read_text(encoding="utf-8")) if guard_path.exists() else {}
    config = _read_vm_ha_config()
    return {
        "schema": _STATUS_SCHEMA,
        "state": HAState.BLOCKED.value,
        "reasons": list(vm_ha_runtime_blockers()),
        "recovery_action": None,
        "guard_boot_id": guard.get("guard_boot_id"),
        "data_plane_mode": "blocked",
        "cluster_id": (config or {}).get("cluster_id"),
        "node_id": ((config or {}).get("node") or {}).get("node_id"),
        "configured_role": ((config or {}).get("node") or {}).get("role"),
        "observed_owner_node_id": None,
        "allocation_id": None,
        "ownership_epoch": None,
        "former_owner_compute_state": "unknown",
        "former_attachment_absent": False,
        "former_attachment_exact": False,
        "candidate_attachment_exact": False,
        "candidate_attachment_absent": False,
        "ownership_re_read_exact": False,
        "generation_id": ((config or {}).get("generation") or {}).get("generation_id"),
        "digests": ((config or {}).get("generation") or {}).get("digests"),
        "promotion_ready": False,
        "standby_ready": False,
        "standby_tunnel_state": "not-standby",
        "standby_readiness_reasons": ["authoritative-cloud-observation-unavailable"],
        "redundancy_ready": False,
        "rearm_phase": "inhibited",
        "rearm_reason": "runtime-prerequisites-blocked",
        "phase_durations_seconds": {
            "preparation": None,
            "detection_repair": None,
            "common_cutover": None,
            "redundancy_restoration": None,
        },
        "pending_operation_id": (
            checkpoint.pending_action.operation_id if checkpoint.pending_action else None
        ),
    }


def require_vm_ha_runtime_prerequisites(*, state_dir: Path = VM_HA_STATE_DIR) -> None:
    """Refuse activation only while a required runtime capability is unavailable."""

    blockers = vm_ha_runtime_blockers()
    if not blockers:
        return
    _atomic_write_json(
        state_dir / VM_HA_STATUS_PATH.name, _blocked_vm_ha_status(state_dir=state_dir)
    )
    raise RuntimeError(f"VM-HA activation BLOCKED: {', '.join(blockers)}")


def require_vm_ha_current_boot_readiness(*, state_dir: Path = VM_HA_STATE_DIR) -> None:
    config = _read_vm_ha_config()
    if config is None:
        return
    require_vm_ha_current_boot_ready(
        {"vm_ha": config},
        status_path=state_dir / VM_HA_STATUS_PATH.name,
        boot_id_path=BOOT_ID_PATH,
    )


def require_vm_ha_route_maintenance_readiness(
    *, state_dir: Path = VM_HA_STATE_DIR
) -> None:
    config = _read_vm_ha_config()
    if config is None:
        return
    require_vm_ha_route_maintenance_ready(
        {"vm_ha": config},
        status_path=state_dir / VM_HA_STATUS_PATH.name,
        boot_id_path=BOOT_ID_PATH,
    )


def _require_force_reconcile_authority(
    cfg: Mapping[str, Any],
    *,
    expected_owner_node_id: str,
    expected_generation_id: str,
    expected_ownership_epoch: str,
    expected_allocation_id: str,
    status_path: Path = VM_HA_STATUS_PATH,
) -> None:
    """Bind a forced VM-HA render to one lock-held authority snapshot."""

    vm_ha = cfg.get("vm_ha")
    generation = vm_ha.get("generation") if isinstance(vm_ha, Mapping) else None
    node = vm_ha.get("node") if isinstance(vm_ha, Mapping) else None
    digests = generation.get("digests") if isinstance(generation, Mapping) else None
    cluster_id = vm_ha.get("cluster_id") if isinstance(vm_ha, Mapping) else None
    node_id = node.get("node_id") if isinstance(node, Mapping) else None
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("force-reconcile authority changed") from error
    if not (
        all(
            (
                expected_owner_node_id,
                expected_generation_id,
                expected_ownership_epoch,
                expected_allocation_id,
            )
        )
        and isinstance(status, Mapping)
        and status.get("schema") == _STATUS_SCHEMA
        and isinstance(generation, Mapping)
        and isinstance(cluster_id, str)
        and cluster_id
        and isinstance(node_id, str)
        and node_id
        and generation.get("generation_id") == expected_generation_id
        and isinstance(digests, Mapping)
        and status.get("cluster_id") == cluster_id
        and status.get("node_id") == node_id
        and status.get("generation_id") == expected_generation_id
        and status.get("digests") == dict(digests)
        and status.get("observed_owner_node_id") == expected_owner_node_id
        and status.get("ownership_epoch") == expected_ownership_epoch
        and status.get("allocation_id") == expected_allocation_id
        and status.get("apply_locked") is False
        and status.get("pending_operation_id") is None
    ):
        raise RuntimeError("force-reconcile authority changed")
    try:
        writer_inhibition = _vm_ha_writer_inhibition_operation_id(
            state_dir=status_path.parent,
            cluster_id=cluster_id,
            node_id=node_id,
            generation_id=expected_generation_id,
        )
    except (ManagedMTLSError, OSError, ValueError) as error:
        raise RuntimeError("force-reconcile authority changed") from error
    if writer_inhibition is not None:
        raise RuntimeError("force-reconcile authority changed")


def _vm_ha_materialization_authorized(*, state_dir: Path = VM_HA_STATE_DIR) -> dict[str, Any]:
    config = _read_vm_ha_config(CONFIG_PATH)
    if config is None:
        return {}
    try:
        boot_id = _boot_id()
        guard = json.loads((state_dir / VM_HA_GUARD_PATH.name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("VM-HA passive materialization authority is unavailable") from error
    if not (
        isinstance(guard, dict)
        and guard.get("guard_boot_id") == boot_id
        and guard.get("data_plane_mode") == "passive"
    ):
        raise RuntimeError("VM-HA passive materialization is not authorized on this boot")
    return config


def require_vm_ha_current_boot_materialization(
    *,
    state_dir: Path = VM_HA_STATE_DIR,
    handoff_attempts: int = 50,
    handoff_interval_seconds: float = 0.1,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Require materialization to be complete and handed off by the agent."""

    if handoff_attempts < 1:
        raise ValueError("VM-HA materialization handoff attempts must be positive")
    if handoff_interval_seconds < 0:
        raise ValueError("VM-HA materialization handoff interval must not be negative")

    config = _vm_ha_materialization_authorized(state_dir=state_dir)
    if not config:
        return
    try:
        record = json.loads(
            (state_dir / VM_HA_MATERIALIZATION_PATH.name).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("VM-HA passive materialization is incomplete") from error
    generation = config.get("generation") or {}
    node = config.get("node") or {}
    if not (
        isinstance(record, dict)
        and record.get("schema") == "nebius-vpngw/vm-ha-materialization-v1"
        and record.get("boot_id") == _boot_id()
        and record.get("generation_id") == generation.get("generation_id")
        and record.get("node_id") == node.get("node_id")
    ):
        raise RuntimeError("VM-HA passive materialization is incomplete")
    lock_fd = None
    for attempt in range(1, handoff_attempts + 1):
        lock_fd = acquire_routing_lock(blocking=False)
        if lock_fd is not None:
            break
        if attempt < handoff_attempts:
            sleeper(handoff_interval_seconds)
    if lock_fd is None:
        raise RuntimeError("VM-HA passive materialization handoff is incomplete")
    os.close(lock_fd)


def _install_vm_ha_cold_start_guard_locked(*, state_dir: Path, boot_id: str) -> dict[str, Any]:
    result = subprocess.run(
        ["/usr/sbin/sysctl", "-w", "net.ipv4.ip_forward=0"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot install VM-HA forwarding guard: {result.stderr.strip()}")
    record = {
        "schema": _STATUS_SCHEMA,
        "guard_boot_id": boot_id,
        "data_plane_mode": "blocked",
        "installed_at": time.time(),
    }
    _atomic_write_json(state_dir / VM_HA_GUARD_PATH.name, record)
    _durably_unlink(state_dir / VM_HA_STANDBY_READY_PATH.name)
    return record


def _standby_ready_evidence_matches(
    *,
    state_dir: Path,
    status: Mapping[str, Any],
    guard: Mapping[str, Any],
    now: float,
) -> bool:
    path = state_dir / VM_HA_STANDBY_READY_PATH.name
    if not path.exists():
        return False
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    tunnel_state = status.get("standby_tunnel_state")
    warm_readiness = bool(
        tunnel_state == "warm"
        and evidence.get("static_ready") is True
        and evidence.get("bgp_ready") is True
        and evidence.get("xfrm_ready") is True
    )
    cold_readiness = bool(
        tunnel_state == "cold"
        and evidence.get("static_ready") is False
        and evidence.get("bgp_ready") is True
        and evidence.get("xfrm_ready") is False
    )
    return bool(
        isinstance(evidence, dict)
        and set(evidence)
        == {
            "allocation_id",
            "apply_locked",
            "bgp_ready",
            "boot_id",
            "cluster_id",
            "data_plane_mode",
            "digests",
            "generation_id",
            "guard_boot_id",
            "node_id",
            "observed_owner_node_id",
            "ownership_epoch",
            "pending_operation_id",
            "ready_at",
            "route_runtime_id",
            "schema",
            "service_healthy",
            "static_ready",
            "xfrm_ready",
        }
        and evidence.get("schema") == "nebius-vpngw/vm-ha-standby-ready-v1"
        and evidence.get("cluster_id") == status.get("cluster_id")
        and evidence.get("node_id") == status.get("node_id")
        and evidence.get("boot_id") == status.get("guard_boot_id")
        and evidence.get("guard_boot_id") == status.get("guard_boot_id")
        and evidence.get("data_plane_mode") == DataPlaneMode.PASSIVE.value
        and evidence.get("observed_owner_node_id") == status.get("observed_owner_node_id")
        and evidence.get("allocation_id") == status.get("allocation_id")
        and evidence.get("ownership_epoch") == status.get("ownership_epoch")
        and evidence.get("generation_id") == status.get("generation_id")
        and evidence.get("digests") == status.get("digests")
        and evidence.get("route_runtime_id") == status.get("route_runtime_id")
        and evidence.get("service_healthy") is True
        and (warm_readiness or cold_readiness)
        and evidence.get("apply_locked") is False
        and evidence.get("pending_operation_id") is None
        and isinstance(evidence.get("ready_at"), (int, float))
        and not isinstance(evidence.get("ready_at"), bool)
        and math.isfinite(float(evidence["ready_at"]))
        and 0 <= now - float(evidence["ready_at"]) < VM_HA_STANDBY_READY_MAX_AGE_SECONDS
        and guard.get("guard_boot_id") == evidence.get("boot_id")
        and guard.get("data_plane_mode") == DataPlaneMode.PASSIVE.value
    )


def install_vm_ha_cold_start_guard(
    *, state_dir: Path = VM_HA_STATE_DIR, boot_id: str | None = None
) -> dict[str, Any]:
    """Disable forwarding before VPN services and bind that proof to this boot."""

    current_boot_id = boot_id or _boot_id()
    lock_fd = acquire_routing_lock(blocking=True)
    if lock_fd is None:
        raise RuntimeError("cannot acquire VM-HA routing guard lock")
    try:
        return _install_vm_ha_cold_start_guard_locked(state_dir=state_dir, boot_id=current_boot_id)
    finally:
        os.close(lock_fd)


def _project_current_vm_ha_status(
    payload: dict[str, Any],
    *,
    state_dir: Path,
    guard: Mapping[str, Any],
    current_boot_id: str,
    clock: Callable[[], float],
) -> dict[str, Any]:
    """Project persisted status through current-boot durable authority."""

    guard_is_current = guard.get("guard_boot_id") == current_boot_id
    status_is_current = payload.get("guard_boot_id") == current_boot_id
    if not guard_is_current or not status_is_current:
        reason = (
            "current-boot-status-stale"
            if guard_is_current and not status_is_current
            else "current-boot-guard-not-installed"
        )
        cluster_id = str(payload.get("cluster_id") or "")
        node_id = str(payload.get("node_id") or "")
        generation_id = str(payload.get("generation_id") or "")
        apply_operation_id = _strict_apply_lock_record(
            state_dir / VM_HA_APPLY_LOCK_PATH.name,
            cluster_id=cluster_id,
            node_id=node_id,
            generation_id=generation_id,
        )
        payload.update(
            state=HAState.BLOCKED.value,
            reasons=[reason],
            recovery_action=None,
            controller_ready_boot_id=None,
            guard_boot_id=current_boot_id if guard_is_current else None,
            data_plane_mode=DataPlaneMode.BLOCKED.value,
            observed_owner_node_id=None,
            ownership_epoch=None,
            former_owner_compute_state="unknown",
            former_attachment_absent=False,
            former_attachment_exact=False,
            candidate_attachment_exact=False,
            candidate_attachment_absent=False,
            ownership_re_read_exact=False,
            apply_locked=apply_operation_id is not None,
            apply_operation_id=apply_operation_id,
            route_reconciliation=None,
            promotion_ready=False,
            standby_ready=False,
            standby_tunnel_state="not-standby",
            standby_readiness_reasons=[reason],
            redundancy_ready=False,
            rearm_phase="blocked",
            rearm_reason=reason,
            phase_durations_seconds={
                "preparation": None,
                "detection_repair": None,
                "common_cutover": None,
                "redundancy_restoration": None,
            },
            pending_operation_id=None,
            repair=None,
        )
        return payload

    live_cluster_id = payload.get("cluster_id")
    live_node_id = payload.get("node_id")
    live_generation_id = payload.get("generation_id")
    if all(
        isinstance(value, str) and value
        for value in (live_cluster_id, live_node_id, live_generation_id)
    ):
        apply_operation_id = _vm_ha_writer_inhibition_operation_id(
            state_dir=state_dir,
            cluster_id=str(live_cluster_id),
            node_id=str(live_node_id),
            generation_id=str(live_generation_id),
        )
        payload.update(
            apply_locked=apply_operation_id is not None,
            apply_operation_id=apply_operation_id,
        )
    guard_mode = guard.get("data_plane_mode")
    if guard_mode in {mode.value for mode in DataPlaneMode}:
        payload["data_plane_mode"] = guard_mode

    if payload.get("promotion_ready") is True and not (
        guard.get("data_plane_mode") == "active"
        and guard.get("guard_boot_id") == payload.get("guard_boot_id")
    ):
        payload.update(
            state=HAState.BLOCKED.value,
            reasons=["current-boot-guard-not-active"],
            recovery_action=None,
            controller_ready_boot_id=None,
            data_plane_mode="blocked",
            promotion_ready=False,
        )
    if payload.get("standby_ready") is True and not _standby_ready_evidence_matches(
        state_dir=state_dir,
        status=payload,
        guard=guard,
        now=clock(),
    ):
        reasons = list(payload.get("standby_readiness_reasons") or ())
        reasons.append("standby-ready-evidence-invalid")
        payload.update(
            standby_ready=False,
            standby_tunnel_state="not-standby",
            redundancy_ready=False,
            standby_readiness_reasons=list(dict.fromkeys(reasons)),
        )
    return payload


def vm_ha_status(
    *,
    state_dir: Path = VM_HA_STATE_DIR,
    clock: Callable[[], float] = time.monotonic,
    boot_id: Callable[[], str] = _boot_id,
) -> dict[str, Any]:
    """Return actionable, secret-free HA status without inferring cloud authority."""

    if vm_ha_runtime_blockers():
        blocked = _blocked_vm_ha_status(state_dir=state_dir)
        blocked["mtls"] = _managed_mtls_status(state_dir)
        return blocked

    status_path = state_dir / VM_HA_STATUS_PATH.name
    if status_path.exists():
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != _STATUS_SCHEMA:
            raise ValueError("VM-HA status record is invalid")
        current_boot_id = boot_id()
        guard_path = state_dir / VM_HA_GUARD_PATH.name
        guard = json.loads(guard_path.read_text(encoding="utf-8")) if guard_path.exists() else {}
        return _project_current_vm_ha_status(
            payload,
            state_dir=state_dir,
            guard=guard if isinstance(guard, Mapping) else {},
            current_boot_id=current_boot_id,
            clock=clock,
        )

    guard_path = state_dir / VM_HA_GUARD_PATH.name
    guard = json.loads(guard_path.read_text(encoding="utf-8")) if guard_path.exists() else {}
    checkpoint = VMHACheckpointFileStore(state_dir / VM_HA_CHECKPOINT_PATH.name).load()
    payload = {
        "schema": _STATUS_SCHEMA,
        "state": checkpoint.state.value,
        "reasons": ["authoritative-cloud-observation-not-yet-recorded"],
        "recovery_action": None,
        "guard_boot_id": guard.get("guard_boot_id"),
        "data_plane_mode": guard.get("data_plane_mode", "blocked"),
        "observed_owner_node_id": None,
        "allocation_id": None,
        "ownership_epoch": None,
        "former_owner_compute_state": "unknown",
        "former_attachment_absent": False,
        "former_attachment_exact": False,
        "candidate_attachment_exact": False,
        "candidate_attachment_absent": False,
        "ownership_re_read_exact": False,
        "generation_id": None,
        "digests": None,
        "promotion_ready": False,
        "standby_ready": False,
        "standby_tunnel_state": "not-standby",
        "standby_readiness_reasons": ["authoritative-cloud-observation-not-yet-recorded"],
        "redundancy_ready": False,
        "rearm_phase": "idle",
        "rearm_reason": "promotion-receipt-unavailable",
        "phase_durations_seconds": {
            "preparation": None,
            "detection_repair": None,
            "common_cutover": None,
            "redundancy_restoration": None,
        },
        "pending_operation_id": (
            checkpoint.pending_action.operation_id if checkpoint.pending_action else None
        ),
    }
    payload["mtls"] = _managed_mtls_status(state_dir)
    return payload


def install_vm_ha_removal_inhibition(
    operation_id: str,
    *,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = VM_HA_STATE_DIR,
) -> dict[str, Any]:
    """Install one exact controller-visible removal gate under the rearm lock."""

    if not (
        len(operation_id) == 64
        and all(character in "0123456789abcdef" for character in operation_id)
    ):
        raise ValueError("VM-HA removal inhibition operation identity is invalid")
    config = _read_vm_ha_config(config_path)
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    node = config.get("node") or {}
    generation = config.get("generation") or {}
    cluster_id = str(config.get("cluster_id") or "")
    node_id = str(node.get("node_id") or "")
    generation_id = str(generation.get("generation_id") or "")
    if not (
        cluster_id
        and node_id
        and len(generation_id) == 64
        and all(character in "0123456789abcdef" for character in generation_id)
    ):
        raise ValueError("VM-HA removal inhibition cannot bind the installed generation")
    from .vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

    descriptor = _acquire_rearm_lock(
        state_dir / REARM_LOCK_PATH.name,
        timeout_seconds=30.0,
    )
    if descriptor is None:
        raise RuntimeError("VM-HA rearm writer did not quiesce for removal inhibition")
    try:
        path = state_dir / VM_HA_APPLY_LOCK_PATH.name
        existing_operation = _strict_apply_lock_record(
            path,
            cluster_id=cluster_id,
            node_id=node_id,
            generation_id=generation_id,
        )
        if existing_operation is not None and existing_operation != operation_id:
            raise RuntimeError("VM-HA removal inhibition conflicts with another apply operation")
        record = {
            "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
            "apply_locked": True,
            "cluster_id": cluster_id,
            "node_id": node_id,
            "generation_id": generation_id,
            "operation_id": operation_id,
        }
        if existing_operation is None:
            _atomic_write_json(path, record)
    finally:
        os.close(descriptor)
    return {
        "schema": "nebius-vpngw/vm-ha-removal-inhibition-v1",
        "cluster_id": cluster_id,
        "node_id": node_id,
        "generation_id": generation_id,
        "operation_id": operation_id,
    }


def verify_vm_ha_removal_quiescent(
    operation_id: str,
    *,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = VM_HA_STATE_DIR,
    boot_id: Callable[[], str] = _boot_id,
) -> dict[str, Any]:
    """Prove the controller acknowledged inhibition and no cloud effect is accepted."""

    config = _read_vm_ha_config(config_path)
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    node = config.get("node") or {}
    generation = config.get("generation") or {}
    cluster_id = str(config.get("cluster_id") or "")
    node_id = str(node.get("node_id") or "")
    generation_id = str(generation.get("generation_id") or "")
    observed_operation = _strict_apply_lock_record(
        state_dir / VM_HA_APPLY_LOCK_PATH.name,
        cluster_id=cluster_id,
        node_id=node_id,
        generation_id=generation_id,
    )
    status = vm_ha_status(state_dir=state_dir, boot_id=boot_id)
    if not (
        observed_operation == operation_id
        and status.get("schema") == _STATUS_SCHEMA
        and status.get("cluster_id") == cluster_id
        and status.get("node_id") == node_id
        and status.get("generation_id") == generation_id
        and status.get("apply_locked") is True
        and status.get("apply_operation_id") == operation_id
        and status.get("pending_operation_id") is None
        and not (state_dir / "accepted-cloud-operation.json").exists()
        and not (state_dir / "rearm-cloud-operation.json").exists()
    ):
        raise RuntimeError("VM-HA removal inhibition has not reached a quiescent controller")
    return {
        "schema": "nebius-vpngw/vm-ha-removal-quiescent-v1",
        "cluster_id": cluster_id,
        "node_id": node_id,
        "generation_id": generation_id,
        "operation_id": operation_id,
    }


def request_manual_failback(*, state_dir: Path = VM_HA_STATE_DIR) -> dict[str, Any]:
    config = _read_vm_ha_config()
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    node = config.get("node") or {}
    if node.get("role") != "active":
        raise RuntimeError("manual VM-HA failback may be requested only on the configured active")
    require_vm_ha_runtime_prerequisites(state_dir=state_dir)
    if (state_dir / VM_HA_FAILOVER_PATH.name).exists():
        raise RuntimeError("conflicting manual VM-HA failover request exists")
    request = {
        "schema": "nebius-vpngw/vm-ha-manual-failback-v1",
        "cluster_id": config.get("cluster_id"),
        "node_id": node.get("node_id"),
        "generation_id": (config.get("generation") or {}).get("generation_id"),
        "requested_at": time.time(),
    }
    _atomic_write_json(state_dir / VM_HA_FAILBACK_PATH.name, request)
    return request


def request_manual_failover(*, state_dir: Path = VM_HA_STATE_DIR) -> dict[str, Any]:
    config = _read_vm_ha_config()
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    node = config.get("node") or {}
    if node.get("role") != "passive":
        raise RuntimeError("manual VM-HA failover may be requested only on the configured passive")
    require_vm_ha_runtime_prerequisites(state_dir=state_dir)
    if (state_dir / VM_HA_FAILBACK_PATH.name).exists():
        raise RuntimeError("conflicting manual VM-HA failback request exists")
    request = {
        "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
        "cluster_id": config.get("cluster_id"),
        "node_id": node.get("node_id"),
        "generation_id": (config.get("generation") or {}).get("generation_id"),
        "requested_at": time.time(),
    }
    _atomic_write_json(state_dir / VM_HA_FAILOVER_PATH.name, request)
    return request


def _run_vm_ha_controller(
    runtime: VMHAControllerRuntime | DefaultVMHAControllerRuntime | None = None,
    *,
    once: bool = False,
    config_path: Path = CONFIG_PATH,
) -> None:
    """Execute the complete verified controller with deterministic cleanup."""

    config = _read_vm_ha_config(config_path)
    if config is None:
        raise RuntimeError("VM HA controller started without an enabled node manifest")
    if runtime is None:
        require_vm_ha_runtime_prerequisites()
        runtime = build_default_vm_ha_controller_runtime(config_path=config_path)
    previous_handlers: dict[signal.Signals, Any] = {}
    ready_notified = False
    if isinstance(runtime, DefaultVMHAControllerRuntime):

        def request_stop(_signum: int, _frame: Any) -> None:
            raise SystemExit(0)

        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[stop_signal] = signal.getsignal(stop_signal)
            signal.signal(stop_signal, request_stop)
    try:
        if isinstance(runtime, DefaultVMHAControllerRuntime):
            notify_socket = os.environ.get("NOTIFY_SOCKET")
            if not notify_socket:
                raise RuntimeError("VM-HA systemd readiness socket is unavailable")
            subprocess.run(
                [
                    "/usr/bin/systemd-notify",
                    "--ready",
                    "--status=VM-HA guard installed; materializing passive data plane",
                ],
                check=True,
                timeout=5,
            )
            ready_notified = True
        while True:
            try:
                status = runtime.step()
            except RuntimeError as error:
                if not (
                    isinstance(runtime, DefaultVMHAControllerRuntime)
                    and ready_notified
                    and not once
                ):
                    raise
                # The guard is already current-boot authoritative.  Local
                # services ordered after this notify boundary may still be
                # starting; keep retrying observations with forwarding off.
                print(
                    f"[VM-HA] Controller step remains guarded: {error}",
                    flush=True,
                )
                time.sleep(5)
                continue
            if once:
                return
            state = str(status.get("state") or "")
            pending = status.get("pending_operation_id")
            if pending:
                delay = 0.05
            elif state in {
                HAState.SUSPECT.value,
                HAState.FENCING.value,
                HAState.OWNERSHIP_TRANSFER.value,
                HAState.PROMOTING.value,
                HAState.REPAIRING.value,
                HAState.REPAIR_EXHAUSTED.value,
            }:
                delay = 0.25
            else:
                delay = 1.0
            time.sleep(delay)
    finally:
        try:
            close = getattr(runtime, "close", None)
            if callable(close):
                close()
        finally:
            for stop_signal, previous in previous_handlers.items():
                signal.signal(stop_signal, previous)


class Agent:
    def __init__(self) -> None:
        self.state = StateStore(STATE_PATH)
        self.ss = StrongSwanRenderer()
        self.frr = FRRRenderer()
        self.xfrm = XFRMManager()

    def reload(
        self,
        *,
        force_reconcile: bool = False,
        expected_vm_ha_owner: str | None = None,
        expected_vm_ha_generation: str | None = None,
        expected_vm_ha_epoch: str | None = None,
        expected_vm_ha_allocation: str | None = None,
    ) -> None:
        expected_authority = (
            expected_vm_ha_owner,
            expected_vm_ha_generation,
            expected_vm_ha_epoch,
            expected_vm_ha_allocation,
        )
        if any(value is not None for value in expected_authority) and not all(
            isinstance(value, str) and value for value in expected_authority
        ):
            raise RuntimeError("force-reconcile authority is incomplete")

        rearm_lock_fd = None
        mtls_lock_fd = None
        if force_reconcile and CONFIG_PATH.exists():
            preflight_vm_ha = _read_vm_ha_config(CONFIG_PATH)
            if preflight_vm_ha is not None:
                if not all(
                    isinstance(value, str) and value for value in expected_authority
                ):
                    raise RuntimeError(
                        "VM-HA force-reconcile requires complete authority"
                    )
                from .vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

                rearm_lock_fd = _acquire_rearm_lock(
                    VM_HA_STATE_DIR / REARM_LOCK_PATH.name,
                    timeout_seconds=0.0,
                )
                if rearm_lock_fd is None:
                    raise RuntimeError(
                        "VM-HA force-reconcile requires the rearm writer lock"
                    )
                try:
                    mtls_lock_fd = ManagedMTLSStore(
                        VM_HA_STATE_DIR / "mtls", create=False
                    ).acquire_writer_lock(blocking=False)
                except ManagedMTLSError as error:
                    os.close(rearm_lock_fd)
                    rearm_lock_fd = None
                    raise RuntimeError(
                        "VM-HA force-reconcile requires the mTLS writer lock"
                    ) from error
                if mtls_lock_fd is None:
                    os.close(rearm_lock_fd)
                    rearm_lock_fd = None
                    raise RuntimeError(
                        "VM-HA force-reconcile requires the mTLS writer lock"
                    )

        lock_fd = None
        try:
            lock_fd = acquire_routing_lock(blocking=True)
        except Exception as e:
            print(f"[Agent] WARNING: Failed to acquire routing lock: {e}")

        try:
            if not CONFIG_PATH.exists():
                print(f"[Agent] Config not found: {CONFIG_PATH}")
                return
            cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                raise RuntimeError("resolved gateway configuration is malformed")
            vm_ha_config = _read_vm_ha_config(CONFIG_PATH)
            if lock_fd is None and (force_reconcile or vm_ha_config is not None):
                raise RuntimeError("VM-HA and forced reconciliation require the routing lock")
            if force_reconcile and vm_ha_config is not None and rearm_lock_fd is None:
                raise RuntimeError(
                    "VM-HA force-reconcile authority changed before writer serialization"
                )
            if any(value is not None for value in expected_authority):
                _require_force_reconcile_authority(
                    cfg,
                    expected_owner_node_id=cast(str, expected_vm_ha_owner),
                    expected_generation_id=cast(str, expected_vm_ha_generation),
                    expected_ownership_epoch=cast(str, expected_vm_ha_epoch),
                    expected_allocation_id=cast(str, expected_vm_ha_allocation),
                )
            vm_ha_blocked = False
            vm_ha_materialization = False
            if vm_ha_config is not None:
                try:
                    require_vm_ha_current_boot_readiness()
                except RuntimeError:
                    vm_ha_blocked = True
                    try:
                        _vm_ha_materialization_authorized()
                    except RuntimeError:
                        try:
                            guard = json.loads(VM_HA_GUARD_PATH.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            guard = {}
                        if isinstance(guard, dict) and guard.get("data_plane_mode") == "active":
                            if lock_fd is not None:
                                _install_vm_ha_cold_start_guard_locked(
                                    state_dir=VM_HA_STATE_DIR, boot_id=_boot_id()
                                )
                            else:
                                install_vm_ha_cold_start_guard()
                    else:
                        vm_ha_materialization = True

            if vm_ha_blocked and not vm_ha_materialization:
                self.ss.render_and_apply(cfg, activate=False)
                self.frr.render_and_apply(cfg, activate=False)
                print("[Agent] Rendered VM-HA configuration behind the blocked data-plane guard")
                return

            if vm_ha_materialization:
                if lock_fd is None:
                    raise RuntimeError("passive materialization requires the routing lock")
                if vm_ha_config is None:
                    raise RuntimeError("passive materialization lost VM-HA configuration")
                update_firewall_from_config(cfg, require_reload=True)
                interface_endpoints = self.ss.render_and_apply(cfg)
                if interface_endpoints:
                    self.xfrm.setup_interfaces(interface_endpoints)
                self.frr.render_and_apply(
                    cfg,
                    advertise_local_prefixes=False,
                    require_reload=True,
                    prepare_vm_ha_controller_access=True,
                )
                enforce_vm_ha_passive_routing_hygiene_locked(cfg)
                self.state.save_last_applied(cfg)
                _atomic_write_json(
                    VM_HA_MATERIALIZATION_PATH,
                    {
                        "schema": "nebius-vpngw/vm-ha-materialization-v1",
                        "boot_id": _boot_id(),
                        "node_id": (vm_ha_config.get("node") or {}).get("node_id"),
                        "generation_id": (vm_ha_config.get("generation") or {}).get(
                            "generation_id"
                        ),
                    },
                )
                print("[Agent] Materialized passive VM-HA data plane with forwarding fenced")
                return

            try:
                update_firewall_from_config(cfg)
            except Exception as e:
                print(f"[Agent] WARNING: Firewall update failed: {e}")

            interface_endpoints = self.ss.build_interface_endpoints(cfg)
            self.frr.ensure_local_prefix_routes(cfg)

            if not force_reconcile and not self.state.is_changed(cfg):
                print("[Agent] No changes detected; skipping config render")
                if interface_endpoints:
                    print("[Agent] Ensuring XFRM interfaces are present...")
                    self.xfrm.setup_interfaces(interface_endpoints)
                if lock_fd is not None:
                    enforce_routing_invariants_locked(cfg)
                else:
                    enforce_routing_invariants(cfg)
                return

            interface_endpoints = self.ss.render_and_apply(cfg)
            if interface_endpoints:
                print("[Agent] Setting up XFRM interfaces...")
                self.xfrm.setup_interfaces(interface_endpoints)
            self.frr.render_and_apply(cfg)
            self.state.save_last_applied(cfg)
            print("[Agent] Applied and persisted new configuration")

            if lock_fd is not None:
                enforce_routing_invariants_locked(cfg)
            else:
                enforce_routing_invariants(cfg)
        finally:
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except Exception:
                    pass
            if mtls_lock_fd is not None:
                try:
                    os.close(mtls_lock_fd)
                except Exception:
                    pass
            if rearm_lock_fd is not None:
                try:
                    os.close(rearm_lock_fd)
                except Exception:
                    pass


def _run_agent() -> None:
    agent = Agent()

    def handle_reload(signum, frame):
        print(f"[Agent] Received signal {signum}; reloading")
        agent.reload()

    agent.reload()
    signal.signal(signal.SIGHUP, handle_reload)
    print("[Agent] Running; await SIGHUP for reload")
    while True:
        try:
            signal.pause()
        except KeyboardInterrupt:
            print("[Agent] Received interrupt, exiting")
            break


def main() -> None:
    from .vm_ha.mtls_actions import ACTION_NAMES

    parser = argparse.ArgumentParser(add_help=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--vm-ha-guard", action="store_true")
    group.add_argument("--vm-ha-controller", action="store_true")
    group.add_argument("--vm-ha-preflight", action="store_true")
    group.add_argument("--vm-ha-status", action="store_true")
    group.add_argument("--vm-ha-ready", action="store_true")
    group.add_argument("--vm-ha-route-maintenance-ready", action="store_true")
    group.add_argument("--vm-ha-materialized", action="store_true")
    group.add_argument("--vm-ha-manual-failback", action="store_true")
    group.add_argument("--vm-ha-manual-failover", action="store_true")
    group.add_argument("--vm-ha-rearm-service", action="store_true")
    group.add_argument("--vm-ha-rearm-request", action="store_true")
    group.add_argument("--vm-ha-removal-inhibit", metavar="OPERATION_ID")
    group.add_argument("--vm-ha-removal-ready", metavar="OPERATION_ID")
    group.add_argument("--vm-ha-mtls-action", choices=ACTION_NAMES)
    group.add_argument(
        "--agent-capabilities",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    group.add_argument(
        "--force-reconcile",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--expected-vm-ha-owner", help=argparse.SUPPRESS)
    parser.add_argument("--expected-vm-ha-generation", help=argparse.SUPPRESS)
    parser.add_argument("--expected-vm-ha-epoch", help=argparse.SUPPRESS)
    parser.add_argument("--expected-vm-ha-allocation", help=argparse.SUPPRESS)
    parser.add_argument("--vm-ha-mtls-request", metavar="BASE64_JSON")
    args = parser.parse_args()

    force_authority_args = (
        args.expected_vm_ha_owner,
        args.expected_vm_ha_generation,
        args.expected_vm_ha_epoch,
        args.expected_vm_ha_allocation,
    )
    if any(value is not None for value in force_authority_args) and not args.force_reconcile:
        parser.error("expected VM-HA authority is valid only with --force-reconcile")
    if any(value is not None for value in force_authority_args) and not all(
        isinstance(value, str) and value for value in force_authority_args
    ):
        parser.error("all expected VM-HA authority fields are required together")

    if args.agent_capabilities:
        print(
            json.dumps(
                {
                    "features": [
                        "force-reconcile-v1",
                        "vm-ha-authority-bound-force-reconcile-v1",
                    ],
                    "schema": "nebius-vpngw.agent-capabilities.v1",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return

    if args.vm_ha_mtls_action:
        from .vm_ha.mtls import ManagedMTLSError
        from .vm_ha.mtls_actions import decode_action_request, execute_mtls_action

        if not args.vm_ha_mtls_request:
            parser.error("--vm-ha-mtls-action requires --vm-ha-mtls-request")
        try:
            response = execute_mtls_action(
                args.vm_ha_mtls_action,
                decode_action_request(args.vm_ha_mtls_request),
                state_dir=VM_HA_STATE_DIR,
                config_path=CONFIG_PATH,
            )
        except ManagedMTLSError as error:
            parser.exit(2, f"managed mTLS action failed: {error}\n")
        print(json.dumps(response, sort_keys=True, separators=(",", ":")))
        return

    if args.vm_ha_guard:
        print(json.dumps(install_vm_ha_cold_start_guard(), sort_keys=True))
        return
    if args.vm_ha_controller:
        _run_vm_ha_controller()
        return
    if args.vm_ha_preflight:
        require_vm_ha_runtime_prerequisites()
        runtime = build_default_vm_ha_controller_runtime()
        runtime.close(restore_guard=False)
        return
    if args.vm_ha_status:
        print(json.dumps(vm_ha_status(), sort_keys=True))
        return
    if args.vm_ha_ready:
        try:
            require_vm_ha_current_boot_readiness()
        except RuntimeError:
            # ExecCondition uses exit 1 as an expected passive-node skip.
            # Keep that normal control flow out of journald tracebacks; the
            # actionable reason remains available from --vm-ha-status.
            raise SystemExit(1) from None
        return
    if args.vm_ha_route_maintenance_ready:
        try:
            require_vm_ha_route_maintenance_readiness()
        except RuntimeError:
            # A blocked or transitioning VM-HA member is an expected systemd
            # condition skip; the periodic writer rechecks authority itself.
            raise SystemExit(1) from None
        return
    if args.vm_ha_materialized:
        require_vm_ha_current_boot_materialization()
        return
    if args.vm_ha_manual_failback:
        print(json.dumps(request_manual_failback(), sort_keys=True))
        return
    if args.vm_ha_manual_failover:
        print(json.dumps(request_manual_failover(), sort_keys=True))
        return
    if args.vm_ha_rearm_service:
        from .vm_ha_rearm import run_rearm_service

        run_rearm_service()
        return
    if args.vm_ha_removal_inhibit:
        print(
            json.dumps(
                install_vm_ha_removal_inhibition(args.vm_ha_removal_inhibit),
                sort_keys=True,
            )
        )
        return
    if args.vm_ha_removal_ready:
        print(
            json.dumps(
                verify_vm_ha_removal_quiescent(args.vm_ha_removal_ready),
                sort_keys=True,
            )
        )
        return
    if args.vm_ha_rearm_request:
        from .vm_ha_rearm import request_rearm_retry

        print(json.dumps(request_rearm_retry(), sort_keys=True))
        return
    if args.force_reconcile:
        Agent().reload(
            force_reconcile=True,
            expected_vm_ha_owner=args.expected_vm_ha_owner,
            expected_vm_ha_generation=args.expected_vm_ha_generation,
            expected_vm_ha_epoch=args.expected_vm_ha_epoch,
            expected_vm_ha_allocation=args.expected_vm_ha_allocation,
        )
        return
    _run_agent()


if __name__ == "__main__":
    main()
