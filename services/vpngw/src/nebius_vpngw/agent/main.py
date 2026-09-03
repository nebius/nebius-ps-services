from __future__ import annotations

import argparse
import json
import math
import os
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
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
from .vm_ha.auto_healing import (
    AUTO_HEALING_CAPABILITY,
    AUTO_HEALING_REQUEST_SCHEMA,
    AUTO_HEALING_STATUS_SCHEMA,
    AutoHealingPolicyError,
    AutoHealingPolicyPhase,
    AutoHealingPolicyRecord,
    AutoHealingPolicyStore,
    AutoHealingRecoveryPhase,
    AutoHealingRecoveryRecord,
    AutoHealingRecoveryStore,
    StandbyAutoHealing,
    decode_policy_request,
    load_peer_policy_heartbeat,
    peer_policy_agrees,
    require_auto_healing_writer_quiescent,
)
from .vm_ha.inhibition import (
    LIVE_PEER_REPLACEMENT_CAPABILITY,
    STANDBY_REPLACEMENT_INHIBITION_CAPABILITY,
    STANDBY_REPLACEMENT_INHIBITION_FILENAME,
    STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
    STANDBY_REPLACEMENT_RELEASE_FILENAME,
    STANDBY_REPLACEMENT_RELEASE_SCHEMA,
    standby_replacement_inhibition_operation_id,
    standby_replacement_release_operation_id,
)
from .vm_ha.models import (
    DigestSet,
    PeerHeartbeat,
    StalePeerStateError,
    StateValidationError,
)
from .vm_ha.mtls import ManagedMTLSError, ManagedMTLSStore
from .vm_ha.progress import (
    TRANSFER_PROGRESS_ACTIONS,
    TransferProgressIdentity,
    TransferProgressStore,
    planned_request_fingerprint,
    validate_transfer_progress,
)
from .vm_ha.promotion_receipt import (
    PROMOTION_RECEIPT_FILENAME,
    PROMOTION_RECEIPT_SCHEMA,
    promotion_receipt_id_v1,
)
from .vm_ha.restoration import (
    STANDBY_RESTORATION_CAPABILITY,
    PolicyAgreementCertificate,
    PolicyAgreementStore,
    RestorationPhase,
    RestorationSource,
    StandbyRestorationAuthorization,
    StandbyRestorationError,
    StandbyRestorationStore,
    policy_authorizes_certificate,
    policy_authorizes_restoration,
    require_standby_restoration_writer_quiescent,
    restoration_is_active,
)
from .vm_ha.runtime import (
    VMHARuntimePorts,
    build_runtime_ports,
    runtime_identity_public_status,
)
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
VM_HA_TRANSFER_PROGRESS_PATH = VM_HA_STATE_DIR / "transfer-progress.json"
VM_HA_PROMOTION_RECEIPT_PATH = VM_HA_STATE_DIR / PROMOTION_RECEIPT_FILENAME
VM_HA_REARM_STATUS_PATH = VM_HA_STATE_DIR / "rearm-status.json"
VM_HA_STANDBY_READY_PATH = VM_HA_STATE_DIR / "standby-ready.json"
VM_HA_APPLY_LOCK_PATH = VM_HA_STATE_DIR / "apply.lock"
VM_HA_APPLY_OWNER_ADOPTION_PATH = VM_HA_STATE_DIR / "apply-owner-adoption.json"
VM_HA_EMERGENCY_PATH = VM_HA_STATE_DIR / "emergency-active-only.json"
VM_HA_STANDBY_REPLACEMENT_INHIBITION_PATH = (
    VM_HA_STATE_DIR / STANDBY_REPLACEMENT_INHIBITION_FILENAME
)

_CHECKPOINT_SCHEMA_V1 = "nebius-vpngw/vm-ha-controller-checkpoint-v1"
_CHECKPOINT_SCHEMA_V2 = "nebius-vpngw/vm-ha-controller-checkpoint-v2"
_CHECKPOINT_SCHEMA_V3 = "nebius-vpngw/vm-ha-controller-checkpoint-v3"
_CHECKPOINT_SCHEMA = "nebius-vpngw/vm-ha-controller-checkpoint-v4"
_STATUS_SCHEMA = "nebius-vpngw/vm-ha-status-v1"
_VM_HA_MTLS_ROTATION_QUIESCENCE_CAPABILITY = "vm-ha-mtls-rotation-quiescence-v1"
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
    transfer_inhibition_operation_id: Callable[[], str | None] = lambda: None
    standby_replacement_inhibition_operation_id: Callable[[], str | None] = lambda: None
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
    if recorded_generation != generation.get(
        "generation_id"
    ) or recorded_digests.to_dict() != generation.get("digests"):
        # The exact apply lock is installed before the staged manifest becomes
        # active. The old runtime ignores the well-formed target-generation
        # declaration while continuing to honor the lock itself.
        return None
    return payload


def _vm_ha_apply_lock_operation_id(
    *,
    state_dir: Path,
    cluster_id: str,
    node_id: str,
    generation_id: str,
) -> str | None:
    return _strict_apply_lock_record(
        state_dir / VM_HA_APPLY_LOCK_PATH.name,
        cluster_id=cluster_id,
        node_id=node_id,
        generation_id=generation_id,
    )


def _vm_ha_transfer_inhibition_operation_id(
    *,
    state_dir: Path,
    cluster_id: str,
    node_id: str,
    generation_id: str,
) -> str | None:
    mtls_operation = ManagedMTLSStore(state_dir / "mtls", create=False).inhibition_operation_id(
        cluster_id=cluster_id,
        node_id=node_id,
        generation_id=generation_id,
    )
    replacement_operation = standby_replacement_inhibition_operation_id(
        state_dir,
        cluster_id=cluster_id,
        node_id=node_id,
        generation_id=generation_id,
    )
    if mtls_operation is not None and replacement_operation is not None:
        raise ValueError("VM-HA has conflicting transfer inhibitions")
    return mtls_operation or replacement_operation


def _vm_ha_writer_inhibition_operation_id(
    *,
    state_dir: Path,
    cluster_id: str,
    node_id: str,
    generation_id: str,
) -> str | None:
    apply_operation = _vm_ha_apply_lock_operation_id(
        state_dir=state_dir,
        cluster_id=cluster_id,
        node_id=node_id,
        generation_id=generation_id,
    )
    mtls_operation = _vm_ha_transfer_inhibition_operation_id(
        state_dir=state_dir,
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

    def requested() -> bool:
        return (
            _read_manual_transfer_request(
                path=path,
                config=config,
                schema=schema,
                operation=operation,
                configured_role=configured_role,
            )
            is not None
        )

    return requested


def _read_manual_transfer_request(
    *,
    path: Path,
    config: Mapping[str, Any],
    schema: str,
    operation: str,
    configured_role: str,
) -> dict[str, Any] | None:
    """Read one exact planned-transfer request without reducing it to a boolean."""

    if not path.exists():
        return None
    expected = {
        "cluster_id": config.get("cluster_id"),
        "node_id": (config.get("node") or {}).get("node_id"),
        "generation_id": (config.get("generation") or {}).get("generation_id"),
    }
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
        and math.isfinite(float(payload["requested_at"]))
    ):
        raise ValueError(f"VM-HA manual {operation} request is invalid or stale")
    return payload


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


def _transfer_intent_matches_candidate_role(intent: TransferIntent, role: str) -> bool:
    """Keep manual preference directional while automatic owner loss is role-neutral."""

    if intent is TransferIntent.PLANNED_FAILBACK:
        return role == ConfiguredRole.ACTIVE.value
    if intent is TransferIntent.PLANNED_FAILOVER:
        return role == ConfiguredRole.PASSIVE.value
    return bool(
        intent
        in {
            TransferIntent.AUTOMATIC_FAILOVER,
            TransferIntent.APPLY_OWNER_ADOPTION,
        }
        and role in {ConfiguredRole.ACTIVE.value, ConfiguredRole.PASSIVE.value}
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
    if not _transfer_intent_matches_candidate_role(intent, role):
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
    assert intent is not None
    role = str(node.get("role") or "")
    if not _transfer_intent_matches_candidate_role(intent, role):
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
            if requested is TransferIntent.PLANNED_FAILBACK:
                request_path = state_dir / VM_HA_FAILBACK_PATH.name
                request = _read_manual_transfer_request(
                    path=request_path,
                    config=config,
                    schema="nebius-vpngw/vm-ha-manual-failback-v1",
                    operation="failback",
                    configured_role="active",
                )
            else:
                request_path = state_dir / VM_HA_FAILOVER_PATH.name
                request = _read_manual_transfer_request(
                    path=request_path,
                    config=config,
                    schema="nebius-vpngw/vm-ha-manual-failover-v1",
                    operation="failover",
                    configured_role="passive",
                )
            if request is None or float(request["requested_at"]) < float(lineage["started_at"]):
                raise ValueError("manual VM-HA request conflicts with sticky transfer lineage")
            # Older request entrypoints admitted a new manual preference after
            # an effect-backed lineage was already durable. The earlier lineage
            # remains authoritative; retire only the provably later request so
            # it cannot crash the controller or run after the transfer completes.
            _durably_unlink(request_path)
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
        self.last_transfer_inhibition_operation_id: str | None = None

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
        transfer_inhibition_operation_id = self.providers.transfer_inhibition_operation_id()
        standby_replacement_inhibition_operation_id = (
            self.providers.standby_replacement_inhibition_operation_id()
        )
        if (
            standby_replacement_inhibition_operation_id is not None
            and standby_replacement_inhibition_operation_id != transfer_inhibition_operation_id
        ):
            raise ValueError("VM-HA standby replacement inhibition is not the active transfer gate")
        if apply_operation_id is not None and transfer_inhibition_operation_id is not None:
            raise ValueError("VM-HA has conflicting writer inhibitions")
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
            transfer_inhibited=transfer_inhibition_operation_id is not None,
            standby_replacement_inhibited=(standby_replacement_inhibition_operation_id is not None),
        )
        self.last_apply_operation_id = apply_operation_id
        self.last_transfer_inhibition_operation_id = transfer_inhibition_operation_id
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


@contextmanager
def _vm_ha_transfer_effect_guard(
    action: ControllerAction,
    *,
    snapshots: VMHASnapshotAdapter,
    state_dir: Path,
) -> Iterator[bool]:
    """Serialize transfer effects and re-check mutable authority at dispatch."""

    if action.kind not in _TRANSFER_EFFECT_ACTIONS:
        yield True
        return
    from .vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

    descriptor = _acquire_rearm_lock(
        state_dir / REARM_LOCK_PATH.name,
        timeout_seconds=0.0,
    )
    if descriptor is None:
        yield False
        return
    try:
        snapshot = snapshots.last_snapshot
        observed_intent = (
            (snapshot.transfer_intent, snapshot.transfer_effect_started)
            if snapshot is not None
            else None
        )
        current_apply_lock = snapshots.providers.apply_lock_operation_id()
        current_inhibition = snapshots.providers.transfer_inhibition_operation_id()
        current_intent = snapshots.providers.transfer_intent()
        inhibited_owner_recovery = False
        if snapshot is not None and action.kind in {
            ActionKind.PREPARE_CANDIDATE_DATAPLANE,
            ActionKind.RECONCILE_ROUTES,
            ActionKind.ENABLE_ACTIVE,
        }:
            routes = snapshot.routes_reconciled_context
            replacement_provider = getattr(
                snapshots.providers,
                "standby_replacement_inhibition_operation_id",
                lambda: None,
            )
            current_replacement_inhibition = replacement_provider()
            exact_owner_forwarding = bool(
                current_inhibition is not None
                and current_inhibition == snapshots.last_transfer_inhibition_operation_id
                and snapshot.transfer_inhibited
                and snapshot.transfer_intent is None
                and not snapshot.transfer_effect_started
                and not snapshot.apply_locked
                and not snapshot.emergency_active_only
                and snapshot.guard_boot_id == snapshot.boot_id
                and snapshot.data_plane_mode is DataPlaneMode.PASSIVE
                and action.target_node_id == snapshot.local_node_id
                and action.allocation_id == snapshot.cloud.allocation_id
                and action.ownership_epoch == snapshot.cloud.ownership_epoch
                and action.generation_id == snapshot.local_generation_id
                and action.digests == snapshot.local_digests
                and snapshot.cloud.local_attachment_exact(snapshot.local_node_id)
            )
            exact_replacement_recovery = bool(
                exact_owner_forwarding
                and snapshot.standby_replacement_inhibited
                and current_replacement_inhibition == current_inhibition
            )
            if action.kind is ActionKind.PREPARE_CANDIDATE_DATAPLANE:
                inhibited_owner_recovery = bool(
                    exact_replacement_recovery and snapshot.readiness.candidate_preparation_required
                )
            elif action.kind is ActionKind.RECONCILE_ROUTES:
                inhibited_owner_recovery = bool(
                    exact_replacement_recovery and snapshot.readiness.promotion_ready
                )
            else:
                inhibited_owner_recovery = bool(
                    exact_owner_forwarding
                    and snapshot.readiness.promotion_ready
                    and routes is not None
                    and bool(routes.operation_id)
                    and replace(routes, operation_id="")
                    == replace(
                        snapshot.route_reconciliation_context,
                        ownership_incarnation=action.ownership_incarnation,
                    )
                )
        yield bool(
            snapshot is not None
            and current_apply_lock is None
            and (current_inhibition is None or inhibited_owner_recovery)
            and current_intent == observed_intent
        )
    finally:
        os.close(descriptor)


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
    promotion_receipt_path = path.parent / VM_HA_PROMOTION_RECEIPT_PATH.name
    try:
        promotion_receipt = _read_promotion_receipt(
            promotion_receipt_path,
            config=config,
        )
    except _PriorGenerationPromotionReceipt:
        cloud = snapshot.cloud
        intent_matches_role = bool(
            intent is None
            or _transfer_intent_matches_candidate_role(
                intent,
                snapshot.configured_role.value,
            )
        )
        first_effect_matches_cloud = bool(
            (
                action.kind is ActionKind.STOP_FORMER_OWNER
                and cloud.former_owner_compute_state is ComputeState.RUNNING
            )
            or (
                action.kind is ActionKind.DETACH_FORMER_ATTACHMENT
                and cloud.former_owner_compute_state is ComputeState.STOPPED
            )
        )
        if not (
            existing is None
            and intent_matches_role
            and not snapshot.transfer_effect_started
            and first_effect_matches_cloud
            and action.target_node_id == snapshot.peer_node_id
            and action.allocation_id == cloud.allocation_id
            and action.ownership_epoch == cloud.ownership_epoch
            and action.generation_id == snapshot.local_generation_id
            and action.digests == snapshot.local_digests
            and cloud.authoritative
            and cloud.observed_owner_node_id == snapshot.peer_node_id
            and cloud.former_owner_node_id == snapshot.peer_node_id
            and cloud.former_attachment_exact
            and not cloud.former_attachment_absent
            and cloud.candidate_attachment_absent
            and not cloud.candidate_attachment_exact
        ):
            raise
        # A non-owner retains its last locally committed receipt across an
        # apply because only the exact apply-declared owner can adopt the new
        # generation.  At the first explicitly requested transfer effect, the
        # unchanged authoritative pre-transfer topology proves that this exact
        # prior-generation receipt no longer grants local owner authority.
        # Retire it durably before recording current-generation lineage.
        _durably_unlink(promotion_receipt_path)
        promotion_receipt = None
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
        adoption is not None and snapshot.cloud.local_attachment_exact(snapshot.local_node_id)
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
        intent = TransferIntent.AUTOMATIC_FAILOVER
    if not _transfer_intent_matches_candidate_role(
        intent,
        snapshot.configured_role.value,
    ):
        raise RuntimeError("VM-HA transfer intent does not match the candidate role")

    if existing is not None:
        if TransferIntent(str(existing["intent"])) is not intent:
            raise RuntimeError("VM-HA transfer effect conflicts with durable lineage")
        return
    restoration_store = StandbyRestorationStore(path.parent)
    source = RestorationSource(intent.value)
    if intent is TransferIntent.AUTOMATIC_FAILOVER:
        try:
            certificate = PolicyAgreementStore(path.parent).load()
            binding, local, peer = _auto_healing_context(config)
            policy = AutoHealingPolicyStore(path.parent).require_bound(
                cluster_id=binding.cluster_id,
                node_id=local.node_id,
                peer_node_id=peer.node_id,
                generation_id=binding.generation_id,
            )
            if certificate is not None and policy_authorizes_certificate(
                policy,
                certificate,
                allow_prepared_disable=True,
            ):
                restoration_store.arm(
                    source=source,
                    certificate=certificate,
                    owner_node_id=snapshot.local_node_id,
                    former_owner_node_id=snapshot.peer_node_id,
                    allocation_id=snapshot.cloud.allocation_id,
                    generation_id=snapshot.local_generation_id,
                    digests=snapshot.local_digests.to_dict(),
                    request_fingerprint=None,
                    first_operation_id=action.operation_id,
                    updated_at=clock(),
                )
        except (AutoHealingPolicyError, StandbyRestorationError):
            # Automatic owner-loss promotion remains availability-authoritative.
            # Missing enabled-policy agreement inhibits only later restoration.
            pass
    else:
        authorization = restoration_store.load()
        request_path = (
            path.parent / VM_HA_FAILOVER_PATH.name
            if intent is TransferIntent.PLANNED_FAILOVER
            else path.parent / VM_HA_FAILBACK_PATH.name
        )
        request = _read_manual_transfer_request(
            path=request_path,
            config=config,
            schema=(
                "nebius-vpngw/vm-ha-manual-failover-v1"
                if intent is TransferIntent.PLANNED_FAILOVER
                else "nebius-vpngw/vm-ha-manual-failback-v1"
            ),
            operation=("failover" if intent is TransferIntent.PLANNED_FAILOVER else "failback"),
            configured_role=("passive" if intent is TransferIntent.PLANNED_FAILOVER else "active"),
        )
        if not (
            authorization is not None
            and authorization.phase is RestorationPhase.ARMED
            and authorization.source is source
            and request is not None
            and authorization.request_fingerprint == planned_request_fingerprint(request)
            and authorization.owner_node_id == snapshot.local_node_id
            and authorization.former_owner_node_id == snapshot.peer_node_id
            and authorization.allocation_id == snapshot.cloud.allocation_id
            and authorization.generation_id == snapshot.local_generation_id
            and dict(authorization.digests) == snapshot.local_digests.to_dict()
        ):
            raise RuntimeError("planned VM-HA transfer restoration authority is unavailable")
        restoration_store.bind_first_effect(
            authorization_id=authorization.authorization_id,
            operation_id=action.operation_id,
            updated_at=clock(),
        )
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


def _transfer_progress_identity(
    *,
    action: ControllerAction,
    snapshots: VMHASnapshotAdapter,
    config: Mapping[str, Any],
    state_dir: Path,
    store: TransferProgressStore,
) -> TransferProgressIdentity | None:
    """Build presentation identity only from exact durable transfer authority."""

    if action.kind.value not in TRANSFER_PROGRESS_ACTIONS:
        return None
    snapshot = snapshots.last_snapshot
    if snapshot is None:
        return None
    lineage = _read_transfer_lineage(
        state_dir / VM_HA_TRANSFER_LINEAGE_PATH.name,
        config=config,
    )
    if lineage is None:
        return None
    intent = str(lineage["intent"])
    request_fingerprint: str | None = None
    if intent in {TransferIntent.PLANNED_FAILOVER.value, TransferIntent.PLANNED_FAILBACK.value}:
        if intent == TransferIntent.PLANNED_FAILOVER.value:
            request_path = state_dir / VM_HA_FAILOVER_PATH.name
            request_schema = "nebius-vpngw/vm-ha-manual-failover-v1"
            operation = "failover"
            configured_role = "passive"
        else:
            request_path = state_dir / VM_HA_FAILBACK_PATH.name
            request_schema = "nebius-vpngw/vm-ha-manual-failback-v1"
            operation = "failback"
            configured_role = "active"
        request = _read_manual_transfer_request(
            path=request_path,
            config=config,
            schema=request_schema,
            operation=operation,
            configured_role=configured_role,
        )
        if request is not None:
            request_fingerprint = planned_request_fingerprint(request)
        else:
            current = store.load()
            lineage_keys = {
                "allocation_id",
                "candidate_node_id",
                "cluster_id",
                "digests",
                "first_operation_id",
                "former_owner_node_id",
                "generation_id",
                "intent",
            }
            if current is None or any(current.get(key) != lineage.get(key) for key in lineage_keys):
                return None
            request_fingerprint = cast(str, current["request_fingerprint"])
    return TransferProgressIdentity(
        cluster_id=str(lineage["cluster_id"]),
        candidate_node_id=str(lineage["candidate_node_id"]),
        former_owner_node_id=str(lineage["former_owner_node_id"]),
        allocation_id=str(lineage["allocation_id"]),
        generation_id=str(lineage["generation_id"]),
        digests=cast(Mapping[str, str], lineage["digests"]),
        route_runtime_id=snapshot.route_runtime_id,
        intent=intent,
        request_fingerprint=request_fingerprint,
        first_operation_id=str(lineage["first_operation_id"]),
        ownership_incarnation=action.ownership_incarnation,
    )


def _before_vm_ha_transfer_effect(
    *,
    action: ControllerAction,
    snapshots: VMHASnapshotAdapter,
    config: Mapping[str, Any],
    state_dir: Path,
    progress: TransferProgressStore,
) -> None:
    """Record authoritative lineage, then best-effort presentation progress."""

    _record_transfer_effect_lineage(
        action=action,
        snapshots=snapshots,
        config=config,
        path=state_dir / VM_HA_TRANSFER_LINEAGE_PATH.name,
    )
    try:
        identity = _transfer_progress_identity(
            action=action,
            snapshots=snapshots,
            config=config,
            state_dir=state_dir,
            store=progress,
        )
        if identity is not None:
            progress.attempting(
                identity,
                action=action.kind.value,
                operation_id=action.operation_id,
                boot_id=action.boot_id,
                ownership_epoch=action.ownership_epoch,
            )
    except Exception:
        # Transfer progress is presentation-only and must never become
        # ownership, routing, forwarding, or terminal-success authority.
        return


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
            else former_owner_state in {ComputeState.STOPPED.value, ComputeState.RUNNING.value}
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
    receipt_id = promotion_receipt_id_v1(
        allocation_id=str(authority["allocation_id"]),
        first_operation_id=str(authority["operation_id"]),
        generation_id=str(authority["generation_id"]),
        intent=str(authority["intent"]),
        owner_node_id=str(authority["owner_node_id"]),
        ownership_epoch=str(status["ownership_epoch"]),
        route_operation_id=str(route["operation_id"]),
    )
    receipt = {
        "schema": PROMOTION_RECEIPT_SCHEMA,
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
            max(0.0, committed_at - float(lineage["started_at"])) if lineage is not None else 0.0
        ),
    }
    if receipt_path.exists():
        current = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not (
            isinstance(current, dict)
            and set(current) == set(receipt)
            and current.get("schema") == PROMOTION_RECEIPT_SCHEMA
            and isinstance(current.get("receipt_id"), str)
            and bool(current["receipt_id"])
            and current.get("cluster_id") == authority["cluster_id"]
            and current.get("owner_node_id") == authority["owner_node_id"]
            and current.get("former_owner_node_id") == authority["former_owner_node_id"]
            and current.get("allocation_id") == authority["allocation_id"]
            and current.get("generation_id") == authority["generation_id"]
        ):
            raise ValueError("existing VM-HA promotion receipt is invalid or foreign")
    if adoption is not None:
        restoration_store = StandbyRestorationStore(receipt_path.parent)
        restoration = restoration_store.load()
        if restoration is not None:
            if restoration.phase not in {
                RestorationPhase.COMPLETED,
                RestorationPhase.BLOCKED,
            }:
                raise StandbyRestorationError(
                    "active standby restoration conflicts with apply owner adoption"
                )
            restoration_store.retire_terminal(
                authorization_id=restoration.authorization_id,
            )
    if not receipt_path.exists() or current.get("receipt_id") != receipt_id:
        _atomic_write_json(receipt_path, receipt)
    if lineage is not None:
        # The receipt is terminal transfer authority. Commit the exact
        # restoration authorization before retiring lineage so a crash cannot
        # leave a promoted owner with no durable way to restart its peer.
        restoration_store = StandbyRestorationStore(receipt_path.parent)
        try:
            restoration_store.commit(
                receipt=receipt,
                updated_at=committed_at,
            )
        except StandbyRestorationError:
            if lineage["intent"] != TransferIntent.AUTOMATIC_FAILOVER.value:
                raise
            # Automatic owner-loss promotion is availability-authoritative.
            # Unusable restoration evidence disables only the optional
            # standby restart; it must not invalidate an otherwise fenced,
            # terminal promotion.
            restoration_store.discard_unusable_authorization()
    if lineage is not None:
        _durably_unlink(lineage_path)
    if adoption is not None and adoption_path is not None:
        _durably_unlink(adoption_path)
    return receipt


def _advance_standby_restoration(
    *,
    status: Mapping[str, Any],
    state_dir: Path,
    clock: Callable[[], float],
) -> None:
    """Complete or time-bound the exact post-promotion restoration."""

    store = StandbyRestorationStore(state_dir)
    record = store.load()
    if record is None or record.phase is not RestorationPhase.AWAITING_STANDBY:
        return
    now = clock()
    if (
        status.get("redundancy_ready") is True
        and status.get("observed_owner_node_id") == record.owner_node_id
        and status.get("allocation_id") == record.allocation_id
        and status.get("generation_id") == record.generation_id
        and status.get("digests") == dict(record.digests)
    ):
        assert record.promotion_receipt_id is not None
        store.complete(receipt_id=record.promotion_receipt_id, updated_at=now)
        return
    if record.standby_deadline_at is not None and now >= record.standby_deadline_at:
        assert record.promotion_receipt_id is not None
        store.block(
            receipt_id=record.promotion_receipt_id,
            reason="standby-readiness-timeout",
            updated_at=now,
        )


def _advance_standby_restoration_under_writer_lock(
    *,
    status: Mapping[str, Any],
    state_dir: Path,
    clock: Callable[[], float],
) -> None:
    """Advance restoration only while holding its shared writer lock."""

    from .vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

    descriptor = _acquire_rearm_lock(
        state_dir / REARM_LOCK_PATH.name,
        timeout_seconds=0.0,
    )
    if descriptor is None:
        return
    try:
        _advance_standby_restoration(
            status=status,
            state_dir=state_dir,
            clock=clock,
        )
    finally:
        os.close(descriptor)


def _promotion_committed_for_status(
    *,
    snapshot: ControllerSnapshot,
    config: Mapping[str, Any],
    route_context: RouteReconciliationContext | None,
    receipt_path: Path,
    promotion_ready: bool,
    pending_operation: ControllerAction | None,
) -> bool:
    """Project a terminal receipt without exposing its durable identity."""

    if (
        not promotion_ready
        or snapshot.apply_locked
        or pending_operation is not None
        or route_context is None
    ):
        return False
    try:
        receipt = _read_promotion_receipt(receipt_path, config=config)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return bool(
        _promotion_receipt_matches_current_owner(
            receipt,
            cloud=snapshot.cloud,
            node_id=snapshot.local_node_id,
        )
        and receipt is not None
        and receipt.get("route_operation_id") == route_context.operation_id
        and route_context.owner_node_id == snapshot.local_node_id
        and route_context.ownership_epoch == snapshot.cloud.ownership_epoch
        and route_context.generation_id == snapshot.local_generation_id
        and route_context.digests == snapshot.local_digests
    )


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
        effect_failed: Callable[[ControllerAction], None] | None = None,
        effect_guard: (Callable[[ControllerAction], AbstractContextManager[bool]] | None) = None,
    ) -> None:
        if set(handlers) != set(ActionKind):
            raise ValueError("VM-HA effect adapter requires every controller action")
        self.handlers = dict(handlers)
        self.receipt_path = receipt_path
        self.clock = clock
        self.event_sink = event_sink or self._print_event
        self.before_effect = before_effect or (lambda _action: None)
        self.effect_failed = effect_failed or (lambda _action: None)
        self.effect_guard = effect_guard or (lambda _action: nullcontext(True))

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
        with self.effect_guard(action) as permitted:
            if not permitted:
                return
            self._apply_permitted(action)

    def _apply_permitted(self, action: ControllerAction) -> None:
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
            try:
                self.effect_failed(action)
            except Exception:
                pass
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
        transfer_progress: TransferProgressStore | None = None,
        peer_timeout_seconds: float = 10.0,
        suspicion_seconds: float = 5.0,
    ) -> None:
        self.snapshots = snapshots
        self.status_path = status_path
        self.guard = guard
        policy = VMHAController(
            peer_timeout_seconds=peer_timeout_seconds,
            suspicion_seconds=suspicion_seconds,
        )

        def observe_decision(
            snapshot: ControllerSnapshot,
            previous: ControllerCheckpoint,
            decision: ControllerResult,
        ) -> None:
            if transfer_progress is None or previous.pending_action is None:
                return
            pending = previous.pending_action
            if pending.kind.value not in TRANSFER_PROGRESS_ACTIONS:
                return
            current_pending = decision.checkpoint.pending_action
            if current_pending is not None and current_pending.operation_id == pending.operation_id:
                return
            try:
                if policy.pending_action_postcondition(pending, snapshot, previous):
                    transfer_progress.completed(
                        action=pending.kind.value,
                        operation_id=pending.operation_id,
                    )
            except Exception:
                # Presentation evidence cannot affect controller authority.
                return

        self.controller = RecoverableController(
            policy=policy,
            snapshots=snapshots,
            checkpoints=checkpoints,
            effects=effects,
            decision_observer=observe_decision,
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
            (not snapshot.transfer_inhibited, "mtls-rotation-active"),
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
            rearm_status_valid = False
            rearm_receipt_id: str | None = None
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
                        rearm_status_valid = True
                        rearm_receipt_id = cast(str | None, rearm.get("promotion_receipt_id"))
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
                        and isinstance(promotion.get("receipt_id"), str)
                        and bool(promotion["receipt_id"])
                        and isinstance(promotion.get("committed_at"), (int, float))
                        and isinstance(promotion.get("common_cutover_seconds"), (int, float))
                        and not isinstance(promotion.get("committed_at"), bool)
                        and not isinstance(promotion.get("common_cutover_seconds"), bool)
                        and math.isfinite(float(promotion["committed_at"]))
                        and math.isfinite(float(promotion["common_cutover_seconds"]))
                    ):
                        raise ValueError
                    phase_durations["common_cutover"] = float(promotion["common_cutover_seconds"])
                    if rearm_status_valid and rearm_receipt_id != promotion["receipt_id"]:
                        rearm_phase = "idle"
                        rearm_reason = "rearm-status-unavailable"
                    elif rearm_phase == "running" and rearm_path.exists():
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
            "controller_capabilities": [
                _VM_HA_MTLS_ROTATION_QUIESCENCE_CAPABILITY,
                LIVE_PEER_REPLACEMENT_CAPABILITY,
                STANDBY_REPLACEMENT_INHIBITION_CAPABILITY,
                STANDBY_RESTORATION_CAPABILITY,
            ],
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
            "transfer_inhibition_operation_id": (
                self.snapshots.last_transfer_inhibition_operation_id
            ),
            "transfer_inhibition_quiescent": bool(
                self.snapshots.last_transfer_inhibition_operation_id is not None
                and result.checkpoint.pending_action is None
                and not (self.status_path.parent / "accepted-cloud-operation.json").exists()
                and not (self.status_path.parent / "rearm-cloud-operation.json").exists()
            ),
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
            "promotion_committed": _promotion_committed_for_status(
                snapshot=snapshot,
                config=self.snapshots.config,
                route_context=route_context,
                receipt_path=self.status_path.parent / VM_HA_PROMOTION_RECEIPT_PATH.name,
                promotion_ready=ready,
                pending_operation=result.checkpoint.pending_action,
            ),
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
        if self.config is not None and self.promotion_receipt_path is not None:
            _refresh_standby_restoration_agreement(
                config=self.config,
                state_dir=self.promotion_receipt_path.parent,
                clock=self.clock,
            )
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
            writer_state_dir = (
                self.promotion_receipt_path.parent
                if self.promotion_receipt_path is not None
                else (request_path.parent if request_path is not None else None)
            )
            if writer_state_dir is not None:
                from .vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

                descriptor = _acquire_rearm_lock(
                    writer_state_dir / REARM_LOCK_PATH.name,
                    timeout_seconds=0.0,
                )
                if descriptor is not None:
                    try:
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
                                    for path in (
                                        self.manual_failback_path,
                                        self.manual_failover_path,
                                    )
                                    if path is not None
                                ),
                                clock=self.clock,
                            )
                    finally:
                        os.close(descriptor)
        else:
            self.active_reconcile_requested = False
        if self.promotion_receipt_path is not None:
            _advance_standby_restoration_under_writer_lock(
                status=status,
                state_dir=self.promotion_receipt_path.parent,
                clock=self.clock,
            )
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
    identity_proof_mode: str = "online",
    systemd_invocation_id: str | None = None,
) -> DefaultVMHAControllerRuntime:
    """Build the only production VM-HA runtime from the installed manifest."""

    config = _read_vm_ha_config(config_path)
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    current_boot_id = boot_id()
    effective_monotonic_clock = monotonic_clock or (time.monotonic if clock is time.time else clock)
    generation_store = AtomicGenerationStore(state_dir / "generation-store")
    if identity_proof_mode in {"systemd-preflight", "systemd-controller"}:
        binding, _local, peer = _auto_healing_context(config)
        generation_store.consume_accepted_peer_heartbeat_reset(
            peer.node_id,
            generation_id=binding.generation_id,
        )
    ports = build_runtime_ports(
        config,
        state_dir=state_dir,
        replay_store=generation_store,
        peer_heartbeat_store=generation_store,
        clock=clock,
        monotonic_clock=effective_monotonic_clock,
        boot_id=current_boot_id,
        identity_proof_mode=identity_proof_mode,
        systemd_invocation_id=systemd_invocation_id,
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
                apply_lock_operation_id=lambda: _vm_ha_apply_lock_operation_id(
                    state_dir=state_dir,
                    cluster_id=str(config.get("cluster_id") or ""),
                    node_id=str((config.get("node") or {}).get("node_id") or ""),
                    generation_id=str((config.get("generation") or {}).get("generation_id") or ""),
                ),
                transfer_inhibition_operation_id=lambda: _vm_ha_transfer_inhibition_operation_id(
                    state_dir=state_dir,
                    cluster_id=str(config.get("cluster_id") or ""),
                    node_id=str((config.get("node") or {}).get("node_id") or ""),
                    generation_id=str((config.get("generation") or {}).get("generation_id") or ""),
                ),
                standby_replacement_inhibition_operation_id=lambda: (
                    standby_replacement_inhibition_operation_id(
                        state_dir,
                        cluster_id=str(config.get("cluster_id") or ""),
                        node_id=str((config.get("node") or {}).get("node_id") or ""),
                        generation_id=str(
                            (config.get("generation") or {}).get("generation_id") or ""
                        ),
                    )
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
    transfer_progress = TransferProgressStore(
        state_dir / VM_HA_TRANSFER_PROGRESS_PATH.name,
        writer=_atomic_write_json,
        clock=clock,
    )
    return VMHAControllerRuntime(
        snapshots=snapshots,
        effects=VMHAEffectAdapter(
            handlers,
            receipt_path=state_dir / VM_HA_EFFECT_RECEIPT_PATH.name,
            effect_guard=lambda action: _vm_ha_transfer_effect_guard(
                action,
                snapshots=snapshots,
                state_dir=state_dir,
            ),
            before_effect=lambda action: _before_vm_ha_transfer_effect(
                action=action,
                snapshots=snapshots,
                config=config,
                state_dir=state_dir,
                progress=transfer_progress,
            ),
            effect_failed=lambda action: transfer_progress.failed(
                action=action.kind.value,
                operation_id=action.operation_id,
            ),
        ),
        checkpoints=VMHACheckpointFileStore(state_dir / VM_HA_CHECKPOINT_PATH.name),
        status_path=state_dir / VM_HA_STATUS_PATH.name,
        guard=lambda: install_vm_ha_cold_start_guard(
            state_dir=state_dir,
            boot_id=boot_id(),
        ),
        transfer_progress=transfer_progress,
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
        "controller_capabilities": [
            _VM_HA_MTLS_ROTATION_QUIESCENCE_CAPABILITY,
            LIVE_PEER_REPLACEMENT_CAPABILITY,
            STANDBY_REPLACEMENT_INHIBITION_CAPABILITY,
            STANDBY_RESTORATION_CAPABILITY,
        ],
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
        "promotion_committed": False,
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


def _project_runtime_identity_status(
    payload: dict[str, Any],
    *,
    state_dir: Path,
    current_boot_id: str,
) -> dict[str, Any]:
    projected = dict(payload)
    identity = runtime_identity_public_status(
        state_dir=state_dir,
        current_boot_id=current_boot_id,
    )
    projected["runtime_identity"] = identity
    if identity["state"] in {"blocked", "migration-required"}:
        projected["state"] = HAState.BLOCKED.value
        projected["data_plane_mode"] = "blocked"
        projected["promotion_ready"] = False
        projected["standby_ready"] = False
        projected["redundancy_ready"] = False
        reasons = list(projected.get("reasons") or [])
        if "runtime-identity-blocked" not in reasons:
            reasons.append("runtime-identity-blocked")
        projected["reasons"] = reasons
    return projected


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


def require_vm_ha_route_maintenance_readiness(*, state_dir: Path = VM_HA_STATE_DIR) -> None:
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
            promotion_committed=False,
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
        apply_operation_id = _vm_ha_apply_lock_operation_id(
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
            promotion_committed=False,
        )
    if not (
        payload.get("promotion_ready") is True
        and payload.get("data_plane_mode") == DataPlaneMode.ACTIVE.value
        and payload.get("apply_locked") is False
        and payload.get("pending_operation_id") is None
    ):
        payload["promotion_committed"] = False
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


def _project_transfer_progress(
    payload: dict[str, Any],
    *,
    state_dir: Path,
    current_boot_id: str,
) -> dict[str, Any]:
    """Add optional exact-lineage progress without making it status authority."""

    if payload.get("promotion_committed") is True or payload.get("apply_locked") is True:
        return payload
    try:
        progress_path = state_dir / VM_HA_TRANSFER_PROGRESS_PATH.name
        if not progress_path.exists():
            return payload
        raw = json.loads(progress_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            return payload
        progress = validate_transfer_progress(raw)
        checkpoint = VMHACheckpointFileStore(state_dir / VM_HA_CHECKPOINT_PATH.name).load()
        exact = bool(
            progress["cluster_id"] == payload.get("cluster_id")
            and progress["candidate_node_id"] == payload.get("node_id")
            and progress["allocation_id"] == payload.get("allocation_id")
            and progress["generation_id"] == payload.get("generation_id")
            and progress["digests"] == payload.get("digests")
            and progress["route_runtime_id"] == payload.get("route_runtime_id")
            and progress["ownership_incarnation"] == checkpoint.ownership_incarnation
        )
        latest = progress["history"][-1]
        if not exact or latest["boot_id"] != current_boot_id:
            return payload
        if latest["state"] == "attempting":
            pending = checkpoint.pending_action
            if not (
                pending is not None
                and pending.kind.value == latest["action"]
                and pending.operation_id == latest["operation_id"]
                and pending.boot_id == latest["boot_id"]
                and pending.ownership_epoch == latest["ownership_epoch"]
                and pending.allocation_id == progress["allocation_id"]
                and pending.generation_id == progress["generation_id"]
                and pending.digests.to_dict() == progress["digests"]
                and pending.ownership_incarnation == progress["ownership_incarnation"]
            ):
                return payload
        payload["transfer_progress"] = progress
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return payload
    return payload


def _auto_healing_context(
    config: Mapping[str, Any],
) -> tuple[VMHARuntimeBinding, Any, Any]:
    binding = VMHARuntimeBinding.model_validate(config.get("runtime_binding"))
    node = config.get("node")
    if not isinstance(node, Mapping):
        raise AutoHealingPolicyError("installed VM-HA node binding is invalid")
    local_node_id = str(node.get("node_id") or "")
    local = next((item for item in binding.nodes if item.node_id == local_node_id), None)
    if local is None or len(binding.nodes) != 2:
        raise AutoHealingPolicyError("installed VM-HA member set is invalid")
    peer = next(item for item in binding.nodes if item.node_id != local.node_id)
    return binding, local, peer


def _require_auto_healing_mutation_admission(
    *,
    state_dir: Path,
    recovery_operation_id: str | None = None,
    allow_matching_recovery: bool = False,
    allow_accepted_rearm: bool = False,
    matching_apply_lock: tuple[str, str, str, str] | None = None,
    matching_mtls_apply_operation: tuple[str, str, str, str] | None = None,
    matching_mtls_inhibition_operation_id: str | None = None,
) -> None:
    """Reject every conflicting durable VM-HA writer under rearm.lock."""

    apply_lock_path = state_dir / VM_HA_APPLY_LOCK_PATH.name
    if matching_apply_lock is not None and not apply_lock_path.exists():
        raise AutoHealingPolicyError("replacement policy adoption apply lock is missing")
    if apply_lock_path.exists():
        if matching_apply_lock is None:
            raise AutoHealingPolicyError("policy mutation conflicts with an apply or removal lock")
        cluster_id, node_id, generation_id, operation_id = matching_apply_lock
        try:
            observed_apply_operation = _strict_apply_lock_record(
                apply_lock_path,
                cluster_id=cluster_id,
                node_id=node_id,
                generation_id=generation_id,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise AutoHealingPolicyError(
                "replacement policy adoption apply lock is invalid"
            ) from error
        if observed_apply_operation != operation_id:
            raise AutoHealingPolicyError("replacement policy adoption apply lock changed")
    try:
        require_standby_restoration_writer_quiescent(state_dir)
    except StandbyRestorationError as error:
        raise AutoHealingPolicyError(
            "policy mutation conflicts with standby restoration"
        ) from error
    try:
        mtls_status = ManagedMTLSStore(state_dir / "mtls", create=False).status()
    except (OSError, UnicodeError, json.JSONDecodeError, ManagedMTLSError) as error:
        raise AutoHealingPolicyError("policy mutation mTLS state is invalid") from error
    observed_mtls_inhibition = mtls_status.inhibition_operation_id
    if observed_mtls_inhibition != matching_mtls_inhibition_operation_id:
        raise AutoHealingPolicyError("policy mutation conflicts with mTLS rotation")
    expected_mtls_apply_operation_id = (
        None if matching_mtls_apply_operation is None else matching_mtls_apply_operation[3]
    )
    if mtls_status.operation_id != expected_mtls_apply_operation_id:
        raise AutoHealingPolicyError("policy mutation conflicts with an mTLS apply transaction")
    if matching_mtls_apply_operation is not None:
        cluster_id, node_id, compute_id, _operation_id = matching_mtls_apply_operation
        if not (
            mtls_status.cluster_id == cluster_id
            and mtls_status.node_id == node_id
            and mtls_status.compute_id == compute_id
            and mtls_status.operation_kind in {"bootstrap", "replacement", "recovery"}
            and mtls_status.phase in {"local-active", "verified", "committed"}
        ):
            raise AutoHealingPolicyError(
                "replacement policy adoption mTLS apply authority is invalid"
            )
    if (state_dir / "accepted-cloud-operation.json").exists():
        raise AutoHealingPolicyError("policy mutation conflicts with a controller effect")
    recovery = AutoHealingRecoveryStore(state_dir).load()
    matching_recovery = bool(
        allow_matching_recovery
        and recovery is not None
        and recovery.operation_id == recovery_operation_id
        and recovery.phase
        in {
            AutoHealingRecoveryPhase.ARMED,
            AutoHealingRecoveryPhase.CONSUMED,
            AutoHealingRecoveryPhase.COMPLETED,
        }
    )
    if (state_dir / "rearm-cloud-operation.json").exists() and not (
        matching_recovery and allow_accepted_rearm
    ):
        raise AutoHealingPolicyError("policy mutation conflicts with accepted standby start")
    checkpoint = VMHACheckpointFileStore(state_dir / VM_HA_CHECKPOINT_PATH.name).load()
    if checkpoint.pending_action is not None:
        raise AutoHealingPolicyError("policy mutation conflicts with pending controller work")
    if (
        recovery is not None
        and recovery.phase is not AutoHealingRecoveryPhase.COMPLETED
        and recovery.operation_id != recovery_operation_id
    ):
        raise AutoHealingPolicyError("policy mutation conflicts with another recovery intent")


def vm_ha_auto_healing_policy(
    action: str,
    request_token: str | None = None,
    *,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = VM_HA_STATE_DIR,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Read or mutate one strict local participant under the rearm lock."""

    if action not in {
        "status",
        "initialize",
        "adopt-replacement",
        "prepare",
        "commit",
        "arm-recovery",
        "cancel-recovery",
        "clear-recovery",
    }:
        raise AutoHealingPolicyError("unsupported policy action")
    config = _read_vm_ha_config(config_path)
    if config is None:
        raise AutoHealingPolicyError("VM HA is not enabled on this node")
    binding, local, peer = _auto_healing_context(config)
    store = AutoHealingPolicyStore(state_dir)
    recovery_store = AutoHealingRecoveryStore(state_dir)

    descriptor: int | None = None
    if action != "status":
        from .vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

        descriptor = _acquire_rearm_lock(
            state_dir / REARM_LOCK_PATH.name,
            timeout_seconds=30.0,
        )
        if descriptor is None:
            raise AutoHealingPolicyError("rearm writer did not quiesce for policy change")
    try:
        record: AutoHealingPolicyRecord | None
        if action == "initialize":
            if request_token is not None:
                raise AutoHealingPolicyError("initialize does not accept a request")
            # Activation deliberately discards only restart-parity cache. A
            # previous agent may have written an older private heartbeat
            # schema, while the replay boundary remains valid security state.
            # The restarted controller must receive fresh authenticated peer
            # evidence before using it for policy agreement.
            AtomicGenerationStore(
                state_dir / "generation-store"
            ).request_accepted_peer_heartbeat_reset(
                peer.node_id,
                generation_id=binding.generation_id,
            )
            record = store.initialize(
                cluster_id=binding.cluster_id,
                node_id=local.node_id,
                peer_node_id=peer.node_id,
                generation_id=binding.generation_id,
                updated_at=clock(),
            )
            # This activation action owns only the local policy commit and
            # cache invalidation. Do not turn that successful mutation into a
            # failure by projecting unrelated peer or recovery evidence.
            return {
                "schema": "nebius-vpngw/vm-ha-auto-healing-initialize-result-v1",
                "cluster_id": binding.cluster_id,
                "node_id": local.node_id,
                "configured_role": local.role.value,
                "generation_id": binding.generation_id,
                "desired": record.desired.value,
                "phase": record.phase.value,
                "operation_id": record.operation_id,
                "decision_digest": record.decision_digest,
            }
        elif action in {
            "adopt-replacement",
            "prepare",
            "commit",
            "arm-recovery",
            "cancel-recovery",
            "clear-recovery",
        }:
            if request_token is None:
                raise AutoHealingPolicyError("policy mutation requires a request")
            request = decode_policy_request(request_token)
            policy_keys = {
                "schema",
                "desired",
                "operation_id",
                "coordinator_node_id",
                "predecessor_digest",
                "peer_record",
            }
            replacement_keys = {
                "apply_operation_id",
                "mtls_apply_operation_id",
                "mtls_inhibition_operation_id",
                "schema",
                "operation_id",
                "peer_record",
            }
            recovery_keys = {
                "schema",
                "desired",
                "operation_id",
                "approval_digest",
                "policy_digest",
                "predecessor_digest",
                "promotion_receipt_id",
                "allocation_id",
                "ownership_epoch",
                "stopped_revision",
                "target_node_id",
            }
            expected_keys = (
                replacement_keys
                if action == "adopt-replacement"
                else policy_keys
                if action in {"prepare", "commit"}
                else recovery_keys
                if action == "arm-recovery"
                else {"schema", "operation_id", "approval_digest"}
                if action == "cancel-recovery"
                else {"schema", "operation_id", "recovery_digest"}
            )
            if (
                set(request) != expected_keys
                or request.get("schema") != AUTO_HEALING_REQUEST_SCHEMA
            ):
                raise AutoHealingPolicyError("policy request has an invalid shape")
            operation_id = str(request["operation_id"])
            replacement_mtls_apply_operation_id: str | None = None
            replacement_mtls_inhibition_operation_id: str | None = None
            if action == "adopt-replacement":
                raw_mtls_apply_operation = request["mtls_apply_operation_id"]
                raw_mtls_inhibition_operation = request["mtls_inhibition_operation_id"]
                for label, raw_operation in (
                    ("apply", raw_mtls_apply_operation),
                    ("inhibition", raw_mtls_inhibition_operation),
                ):
                    if raw_operation is not None and not (
                        isinstance(raw_operation, str)
                        and len(raw_operation) == 64
                        and all(character in "0123456789abcdef" for character in raw_operation)
                    ):
                        raise AutoHealingPolicyError(
                            f"replacement policy adoption mTLS {label} operation is invalid"
                        )
                replacement_mtls_apply_operation_id = cast(str | None, raw_mtls_apply_operation)
                replacement_mtls_inhibition_operation_id = cast(
                    str | None, raw_mtls_inhibition_operation
                )
            _require_auto_healing_mutation_admission(
                state_dir=state_dir,
                recovery_operation_id=operation_id,
                allow_matching_recovery=action in {"arm-recovery", "cancel-recovery"},
                allow_accepted_rearm=action == "arm-recovery",
                matching_apply_lock=(
                    (
                        binding.cluster_id,
                        local.node_id,
                        binding.generation_id,
                        str(request["apply_operation_id"]),
                    )
                    if action == "adopt-replacement"
                    else None
                ),
                matching_mtls_apply_operation=(
                    (
                        binding.cluster_id,
                        local.node_id,
                        local.compute_id,
                        replacement_mtls_apply_operation_id,
                    )
                    if action == "adopt-replacement"
                    and replacement_mtls_apply_operation_id is not None
                    else None
                ),
                matching_mtls_inhibition_operation_id=(
                    replacement_mtls_inhibition_operation_id
                    if action == "adopt-replacement"
                    else None
                ),
            )
            if action == "adopt-replacement":
                if recovery_store.load() is not None:
                    raise AutoHealingPolicyError(
                        "replacement policy adoption conflicts with recovery state"
                    )
                peer_record = AutoHealingPolicyRecord.from_mapping(request["peer_record"])
                if operation_id != peer_record.operation_id:
                    raise AutoHealingPolicyError(
                        "replacement policy adoption operation is inconsistent"
                    )
                record = store.adopt_replacement_peer(
                    cluster_id=binding.cluster_id,
                    node_id=local.node_id,
                    peer_node_id=peer.node_id,
                    generation_id=binding.generation_id,
                    peer=peer_record,
                    updated_at=clock(),
                )
                AtomicGenerationStore(
                    state_dir / "generation-store"
                ).request_accepted_peer_heartbeat_reset(
                    peer.node_id,
                    generation_id=binding.generation_id,
                )
            if action in {"prepare", "commit"}:
                try:
                    desired = StandbyAutoHealing(str(request["desired"]))
                except ValueError as error:
                    raise AutoHealingPolicyError(
                        "policy request has an invalid desired state"
                    ) from error
                peer_record = AutoHealingPolicyRecord.from_mapping(request["peer_record"])
                common = {
                    "cluster_id": binding.cluster_id,
                    "node_id": local.node_id,
                    "peer_node_id": peer.node_id,
                    "generation_id": binding.generation_id,
                    "desired": desired,
                    "operation_id": operation_id,
                    "coordinator_node_id": str(request["coordinator_node_id"]),
                    "predecessor_digest": str(request["predecessor_digest"]),
                    "peer": peer_record,
                    "updated_at": clock(),
                }
            if action == "adopt-replacement":
                pass
            elif action == "prepare":
                record = store.prepare(
                    **common,
                )
            elif action == "commit":
                record = store.commit(
                    **common,
                )
            elif action == "arm-recovery":
                from .vm_ha_rearm import PROMOTION_RECEIPT_PATH, _promotion_receipt

                try:
                    desired = StandbyAutoHealing(str(request["desired"]))
                except ValueError as error:
                    raise AutoHealingPolicyError(
                        "recovery request has an invalid desired state"
                    ) from error
                current = store.require_bound(
                    cluster_id=binding.cluster_id,
                    node_id=local.node_id,
                    peer_node_id=peer.node_id,
                    generation_id=binding.generation_id,
                )
                policy_digest = str(request["policy_digest"])
                predecessor_digest = str(request["predecessor_digest"])
                terminal_disabled = bool(
                    current.phase is AutoHealingPolicyPhase.COMMITTED
                    and current.desired is StandbyAutoHealing.DISABLED
                    and current.peer_ack_digest == current.decision_digest
                    and desired is StandbyAutoHealing.ENABLED
                    and policy_digest == current.decision_digest
                    and predecessor_digest == current.decision_digest
                )
                partial_same_operation = bool(
                    current.operation_id == operation_id
                    and current.desired is desired
                    and current.phase
                    in {AutoHealingPolicyPhase.PREPARED, AutoHealingPolicyPhase.COMMITTED}
                    and policy_digest == current.decision_digest
                    and predecessor_digest == current.predecessor_digest
                )
                if not (terminal_disabled or partial_same_operation):
                    raise AutoHealingPolicyError(
                        "recovery request does not match the local policy transaction"
                    )
                receipt = _promotion_receipt(
                    state_dir / PROMOTION_RECEIPT_PATH.name,
                    binding=binding,
                    local=local,
                    peer=peer,
                )
                if receipt is None or not (
                    request["promotion_receipt_id"] == receipt.receipt_id
                    and request["allocation_id"] == receipt.allocation_id
                    and request["ownership_epoch"] == receipt.ownership_epoch
                    and request["target_node_id"] == peer.node_id
                ):
                    raise AutoHealingPolicyError("recovery authority is stale or foreign")
                recovery_store.arm(
                    AutoHealingRecoveryRecord(
                        cluster_id=binding.cluster_id,
                        node_id=local.node_id,
                        target_node_id=peer.node_id,
                        generation_id=binding.generation_id,
                        desired=desired,
                        operation_id=operation_id,
                        approval_digest=str(request["approval_digest"]),
                        policy_digest=policy_digest,
                        predecessor_digest=predecessor_digest,
                        promotion_receipt_id=receipt.receipt_id,
                        allocation_id=receipt.allocation_id,
                        ownership_epoch=receipt.ownership_epoch,
                        stopped_revision=str(request["stopped_revision"]),
                        phase=AutoHealingRecoveryPhase.ARMED,
                        rearm_operation_id=None,
                        updated_at=clock(),
                    )
                )
                record = current
            elif action == "cancel-recovery":
                current = store.require_bound(
                    cluster_id=binding.cluster_id,
                    node_id=local.node_id,
                    peer_node_id=peer.node_id,
                    generation_id=binding.generation_id,
                )
                if not (
                    current.desired is StandbyAutoHealing.DISABLED
                    and current.phase is AutoHealingPolicyPhase.COMMITTED
                    and current.peer_ack_digest == current.decision_digest
                ):
                    raise AutoHealingPolicyError(
                        "recovery cancellation requires committed disabled policy"
                    )
                recovery_store.cancel_armed(
                    operation_id=operation_id,
                    approval_digest=str(request["approval_digest"]),
                )
                record = current
            else:
                current = store.require_bound(
                    cluster_id=binding.cluster_id,
                    node_id=local.node_id,
                    peer_node_id=peer.node_id,
                    generation_id=binding.generation_id,
                )
                if not (
                    current.operation_id == operation_id
                    and current.desired is StandbyAutoHealing.ENABLED
                    and current.phase is AutoHealingPolicyPhase.COMMITTED
                ):
                    raise AutoHealingPolicyError(
                        "recovery clear requires the committed enabled transaction"
                    )
                recovery_store.clear_completed(
                    operation_id=operation_id,
                    recovery_digest=str(request["recovery_digest"]),
                )
                record = current
        else:
            if request_token is not None:
                raise AutoHealingPolicyError("status does not accept a request")
            try:
                record = store.require_bound(
                    cluster_id=binding.cluster_id,
                    node_id=local.node_id,
                    peer_node_id=peer.node_id,
                    generation_id=binding.generation_id,
                )
            except AutoHealingPolicyError:
                record = None
    finally:
        if descriptor is not None:
            os.close(descriptor)

    peer_heartbeat = load_peer_policy_heartbeat(state_dir, peer_node_id=peer.node_id)
    accepted_start = (state_dir / "rearm-cloud-operation.json").exists()
    recovery = recovery_store.load()
    recovery_authority: dict[str, str] | None = None
    try:
        from .vm_ha_rearm import PROMOTION_RECEIPT_PATH, _promotion_receipt

        receipt = _promotion_receipt(
            state_dir / PROMOTION_RECEIPT_PATH.name,
            binding=binding,
            local=local,
            peer=peer,
        )
        if receipt is not None:
            recovery_authority = {
                "allocation_id": receipt.allocation_id,
                "ownership_epoch": receipt.ownership_epoch,
                "promotion_receipt_id": receipt.receipt_id,
            }
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        recovery_authority = None
    peer_agrees = bool(
        record is not None
        and (
            peer_policy_agrees(record, peer_heartbeat, now=clock())
            or (
                record.desired is StandbyAutoHealing.DISABLED
                and record.phase is AutoHealingPolicyPhase.COMMITTED
                and record.peer_ack_digest == record.decision_digest
            )
        )
    )
    return {
        "schema": AUTO_HEALING_STATUS_SCHEMA,
        "cluster_id": binding.cluster_id,
        "node_id": local.node_id,
        "configured_role": local.role.value,
        "generation_id": binding.generation_id,
        "desired": None if record is None else record.desired.value,
        "phase": "blocked" if record is None else record.phase.value,
        "operation_id": None if record is None else record.operation_id,
        "decision_digest": None if record is None else record.decision_digest,
        "peer_agrees": peer_agrees,
        "accepted_start": accepted_start,
        "recovery_authority": recovery_authority,
        "recovery_phase": None if recovery is None else recovery.phase.value,
        "recovery": None if recovery is None else recovery.to_dict(),
        "record": None if record is None else record.to_dict(),
    }


def _restoration_matches_auto_healing_policy(
    restoration: StandbyRestorationAuthorization,
    policy: object,
) -> bool:
    try:
        record = AutoHealingPolicyRecord.from_mapping(policy)
    except AutoHealingPolicyError:
        return False
    return policy_authorizes_restoration(record, restoration)


def _project_auto_healing_status(
    payload: dict[str, Any],
    *,
    state_dir: Path,
) -> dict[str, Any]:
    projected = dict(payload)
    try:
        status = vm_ha_auto_healing_policy("status", state_dir=state_dir)
        desired = status["desired"]
        phase = status["phase"]
        peer_agrees = status["peer_agrees"]
        restoration = StandbyRestorationStore(state_dir).load()
        restoration_matches_policy = bool(
            restoration is not None
            and restoration.phase is not RestorationPhase.COMPLETED
            and _restoration_matches_auto_healing_policy(restoration, status.get("record"))
        )
        if (
            restoration is not None
            and restoration.phase is not RestorationPhase.COMPLETED
            and not restoration_matches_policy
        ):
            state = "blocked"
        elif (
            status["recovery_phase"]
            in {
                AutoHealingRecoveryPhase.ARMED.value,
                AutoHealingRecoveryPhase.CONSUMED.value,
            }
            or phase == AutoHealingPolicyPhase.PREPARED.value
            or restoration_is_active(restoration)
        ):
            state = "transitioning"
        elif (
            phase != AutoHealingPolicyPhase.COMMITTED.value
            or desired is None
            or (
                not peer_agrees
                and not (
                    restoration_matches_policy
                    and restoration is not None
                    and restoration.phase is RestorationPhase.BLOCKED
                    and desired == StandbyAutoHealing.ENABLED.value
                )
            )
        ):
            state = "blocked"
        else:
            state = desired
        projected["auto_healing"] = {
            "state": state,
            "peer_agrees": bool(peer_agrees),
            "accepted_start": bool(status["accepted_start"]),
        }
    except (AutoHealingPolicyError, StandbyRestorationError, OSError, ValueError):
        projected["auto_healing"] = {
            "state": "blocked",
            "peer_agrees": False,
            "accepted_start": (state_dir / "rearm-cloud-operation.json").exists(),
        }
    return projected


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
        return _project_auto_healing_status(
            _project_runtime_identity_status(
                blocked,
                state_dir=state_dir,
                current_boot_id=boot_id(),
            ),
            state_dir=state_dir,
        )

    status_path = state_dir / VM_HA_STATUS_PATH.name
    if status_path.exists():
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != _STATUS_SCHEMA:
            raise ValueError("VM-HA status record is invalid")
        current_boot_id = boot_id()
        guard_path = state_dir / VM_HA_GUARD_PATH.name
        guard = json.loads(guard_path.read_text(encoding="utf-8")) if guard_path.exists() else {}
        current = _project_current_vm_ha_status(
            payload,
            state_dir=state_dir,
            guard=guard if isinstance(guard, Mapping) else {},
            current_boot_id=current_boot_id,
            clock=clock,
        )
        return _project_auto_healing_status(
            _project_transfer_progress(
                _project_runtime_identity_status(
                    current,
                    state_dir=state_dir,
                    current_boot_id=current_boot_id,
                ),
                state_dir=state_dir,
                current_boot_id=current_boot_id,
            ),
            state_dir=state_dir,
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
        "promotion_committed": False,
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
    return _project_auto_healing_status(
        _project_runtime_identity_status(
            payload,
            state_dir=state_dir,
            current_boot_id=boot_id(),
        ),
        state_dir=state_dir,
    )


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
        require_auto_healing_writer_quiescent(state_dir)
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


def _vm_ha_standby_replacement_identity(
    *,
    config_path: Path,
) -> tuple[str, str, str]:
    config = _read_vm_ha_config(config_path)
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    node = config.get("node") or {}
    generation = config.get("generation") or {}
    identity = (
        str(config.get("cluster_id") or ""),
        str(node.get("node_id") or ""),
        str(generation.get("generation_id") or ""),
    )
    if (
        not identity[0]
        or not identity[1]
        or len(identity[2]) != 64
        or any(character not in "0123456789abcdef" for character in identity[2])
    ):
        raise ValueError("VM-HA standby replacement cannot bind the installed generation")
    return identity


def install_vm_ha_standby_replacement_inhibition(
    operation_id: str,
    *,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = VM_HA_STATE_DIR,
) -> dict[str, Any]:
    """Install an owner-preserving transfer gate for one replacement operation."""

    if len(operation_id) != 64 or any(
        character not in "0123456789abcdef" for character in operation_id
    ):
        raise ValueError("VM-HA standby replacement operation identity is invalid")
    cluster_id, node_id, generation_id = _vm_ha_standby_replacement_identity(
        config_path=config_path
    )
    from .vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

    descriptor = _acquire_rearm_lock(
        state_dir / REARM_LOCK_PATH.name,
        timeout_seconds=30.0,
    )
    if descriptor is None:
        raise RuntimeError("VM-HA rearm writer did not quiesce for standby replacement")
    try:
        StandbyRestorationStore(state_dir).retire_terminal_for_apply_owner_adoption()
        require_auto_healing_writer_quiescent(state_dir)
        if (
            _vm_ha_apply_lock_operation_id(
                state_dir=state_dir,
                cluster_id=cluster_id,
                node_id=node_id,
                generation_id=generation_id,
            )
            is not None
        ):
            raise RuntimeError("VM-HA standby replacement conflicts with an apply lock")
        if (
            ManagedMTLSStore(state_dir / "mtls", create=False).inhibition_operation_id(
                cluster_id=cluster_id,
                node_id=node_id,
                generation_id=generation_id,
            )
            is not None
        ):
            raise RuntimeError("VM-HA standby replacement conflicts with mTLS rotation")
        released_operation = standby_replacement_release_operation_id(
            state_dir,
            cluster_id=cluster_id,
            node_id=node_id,
            generation_id=generation_id,
        )
        if released_operation == operation_id:
            raise RuntimeError("VM-HA standby replacement inhibition was already released")
        if released_operation is not None:
            _durably_unlink(state_dir / STANDBY_REPLACEMENT_RELEASE_FILENAME)
        existing = standby_replacement_inhibition_operation_id(
            state_dir,
            cluster_id=cluster_id,
            node_id=node_id,
            generation_id=generation_id,
        )
        if existing is not None and existing != operation_id:
            raise RuntimeError("VM-HA standby replacement inhibition is owned elsewhere")
        if existing is None:
            _atomic_write_json(
                state_dir / STANDBY_REPLACEMENT_INHIBITION_FILENAME,
                {
                    "schema": STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
                    "cluster_id": cluster_id,
                    "node_id": node_id,
                    "generation_id": generation_id,
                    "operation_id": operation_id,
                },
            )
    finally:
        os.close(descriptor)
    return {
        "schema": STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
        "cluster_id": cluster_id,
        "node_id": node_id,
        "generation_id": generation_id,
        "operation_id": operation_id,
    }


class StandbyReplacementNotReady(RuntimeError):
    """The controller has not yet acknowledged an exact replacement inhibition."""


def verify_vm_ha_standby_replacement_quiescent(
    operation_id: str,
    *,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = VM_HA_STATE_DIR,
    boot_id: Callable[[], str] = _boot_id,
) -> dict[str, Any]:
    """Prove transfer writers are quiescent while the serving owner stays active."""

    cluster_id, node_id, generation_id = _vm_ha_standby_replacement_identity(
        config_path=config_path
    )
    observed = standby_replacement_inhibition_operation_id(
        state_dir,
        cluster_id=cluster_id,
        node_id=node_id,
        generation_id=generation_id,
    )
    status = vm_ha_status(state_dir=state_dir, boot_id=boot_id)
    capabilities = status.get("controller_capabilities")
    status_state = status.get("state")
    status_reasons = status.get("reasons")
    owner_state_safe = status_state == "active" or (
        status_state == "degraded" and status_reasons == ["peer-generation-unavailable"]
    )
    if not (
        observed == operation_id
        and isinstance(capabilities, list)
        and STANDBY_REPLACEMENT_INHIBITION_CAPABILITY in capabilities
        and LIVE_PEER_REPLACEMENT_CAPABILITY in capabilities
        and status.get("cluster_id") == cluster_id
        and status.get("node_id") == node_id
        and status.get("generation_id") == generation_id
        and owner_state_safe
        and status.get("data_plane_mode") == "active"
        and status.get("promotion_ready") is True
        and status.get("observed_owner_node_id") == node_id
        and status.get("apply_locked") is False
        and status.get("apply_operation_id") is None
        and status.get("transfer_inhibition_operation_id") == operation_id
        and status.get("transfer_inhibition_quiescent") is True
        and status.get("pending_operation_id") is None
        and not (state_dir / "accepted-cloud-operation.json").exists()
        and not (state_dir / "rearm-cloud-operation.json").exists()
    ):
        raise StandbyReplacementNotReady(
            "VM-HA standby replacement inhibition has not reached an active quiescent owner"
        )
    return {
        "schema": "nebius-vpngw/vm-ha-standby-replacement-quiescent-v1",
        "cluster_id": cluster_id,
        "node_id": node_id,
        "generation_id": generation_id,
        "operation_id": operation_id,
    }


def commit_vm_ha_standby_replacement_peer_binding(
    operation_id: str,
    *,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = VM_HA_STATE_DIR,
    staged_dir: Path = Path("/etc/nebius-vpngw/vm-ha-staged"),
) -> dict[str, Any]:
    """Publish only the replacement peer Compute identity without reloading services."""

    cluster_id, node_id, generation_id = _vm_ha_standby_replacement_identity(
        config_path=config_path
    )
    if (
        standby_replacement_inhibition_operation_id(
            state_dir,
            cluster_id=cluster_id,
            node_id=node_id,
            generation_id=generation_id,
        )
        != operation_id
    ):
        raise RuntimeError("VM-HA standby replacement peer binding is not inhibited")
    staged_path = staged_dir / f"{generation_id}.yaml"
    try:
        staged_stat = staged_path.lstat()
        if (
            staged_path.is_symlink()
            or not staged_path.is_file()
            or stat.S_IMODE(staged_stat.st_mode) != 0o600
            or staged_stat.st_uid != os.geteuid()
        ):
            raise RuntimeError
        staged_bytes = staged_path.read_bytes()
        current_bytes = config_path.read_bytes()
        staged_payload = yaml.safe_load(staged_bytes)
        current_payload = yaml.safe_load(current_bytes)
    except (OSError, UnicodeError, yaml.YAMLError, RuntimeError):
        raise RuntimeError("VM-HA staged replacement peer binding is unavailable") from None
    if not isinstance(staged_payload, dict) or not isinstance(current_payload, dict):
        raise RuntimeError("VM-HA staged replacement peer binding is malformed")
    staged_vm_ha = staged_payload.get("vm_ha")
    current_vm_ha = current_payload.get("vm_ha")
    if not isinstance(staged_vm_ha, dict) or not isinstance(current_vm_ha, dict):
        raise RuntimeError("VM-HA staged replacement peer binding is malformed")
    try:
        staged_binding = VMHARuntimeBinding.model_validate(staged_vm_ha.get("runtime_binding"))
        current_binding = VMHARuntimeBinding.model_validate(current_vm_ha.get("runtime_binding"))
    except ValueError:
        raise RuntimeError("VM-HA staged replacement peer binding is invalid") from None
    if not (
        staged_binding.cluster_id == cluster_id == current_binding.cluster_id
        and staged_binding.generation_id == generation_id == current_binding.generation_id
        and len(staged_binding.nodes) == len(current_binding.nodes) == 2
    ):
        raise RuntimeError("VM-HA staged replacement peer binding changed generation identity")
    staged_nodes = {item.node_id: item for item in staged_binding.nodes}
    current_nodes = {item.node_id: item for item in current_binding.nodes}
    if set(staged_nodes) != set(current_nodes) or node_id not in staged_nodes:
        raise RuntimeError("VM-HA staged replacement peer binding changed node identity")
    peer_node_id = next(item for item in staged_nodes if item != node_id)
    local_unchanged = staged_nodes[node_id] == current_nodes[node_id]
    staged_peer = staged_nodes[peer_node_id]
    current_peer = current_nodes[peer_node_id]
    peer_shape_unchanged = staged_peer.model_dump(exclude={"compute_id"}) == (
        current_peer.model_dump(exclude={"compute_id"})
    )
    binding_shape_unchanged = staged_binding.model_dump(exclude={"nodes"}) == (
        current_binding.model_dump(exclude={"nodes"})
    )
    if not (
        local_unchanged
        and peer_shape_unchanged
        and binding_shape_unchanged
        and staged_peer.compute_id
    ):
        raise RuntimeError("VM-HA staged replacement peer binding changed unauthorized fields")
    mtls_snapshot = ManagedMTLSStore(state_dir / "mtls", create=False).snapshot()
    peer_leaves = [item for item in mtls_snapshot.peers if item.node_id == peer_node_id]
    if not peer_leaves:
        raise RuntimeError("VM-HA replacement peer mTLS identity is unavailable")
    latest_epoch = max(item.epoch for item in peer_leaves)
    latest_compute_ids = {item.compute_id for item in peer_leaves if item.epoch == latest_epoch}
    if latest_compute_ids != {staged_peer.compute_id}:
        raise RuntimeError("VM-HA replacement peer binding does not match managed mTLS")
    if {key: value for key, value in staged_payload.items() if key != "vm_ha"} != {
        key: value for key, value in current_payload.items() if key != "vm_ha"
    } or {key: value for key, value in staged_vm_ha.items() if key != "runtime_binding"} != {
        key: value for key, value in current_vm_ha.items() if key != "runtime_binding"
    }:
        raise RuntimeError("VM-HA staged replacement config changed outside peer identity")
    if staged_bytes != current_bytes:
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=f".{config_path.name}.",
            dir=config_path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(temporary_fd, "wb") as stream:
                stream.write(staged_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, config_path)
            directory_fd = os.open(config_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()
    return {
        "schema": LIVE_PEER_REPLACEMENT_CAPABILITY,
        "cluster_id": cluster_id,
        "node_id": node_id,
        "generation_id": generation_id,
        "operation_id": operation_id,
    }


def release_vm_ha_standby_replacement_inhibition(
    operation_id: str,
    *,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = VM_HA_STATE_DIR,
    boot_id: Callable[[], str] = _boot_id,
) -> dict[str, Any]:
    """Release only the exact quiescent owner-preserving replacement gate."""

    cluster_id, node_id, generation_id = _vm_ha_standby_replacement_identity(
        config_path=config_path
    )
    from .vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

    descriptor = _acquire_rearm_lock(
        state_dir / REARM_LOCK_PATH.name,
        timeout_seconds=30.0,
    )
    if descriptor is None:
        raise RuntimeError("VM-HA rearm writer did not quiesce for inhibition release")
    try:
        require_auto_healing_writer_quiescent(state_dir)
        released_operation = standby_replacement_release_operation_id(
            state_dir,
            cluster_id=cluster_id,
            node_id=node_id,
            generation_id=generation_id,
        )
        if released_operation is not None and released_operation != operation_id:
            raise RuntimeError("VM-HA standby replacement release is owned elsewhere")
        if released_operation is None:
            verify_vm_ha_standby_replacement_quiescent(
                operation_id,
                config_path=config_path,
                state_dir=state_dir,
                boot_id=boot_id,
            )
            _atomic_write_json(
                state_dir / STANDBY_REPLACEMENT_RELEASE_FILENAME,
                {
                    "schema": STANDBY_REPLACEMENT_RELEASE_SCHEMA,
                    "cluster_id": cluster_id,
                    "node_id": node_id,
                    "generation_id": generation_id,
                    "operation_id": operation_id,
                },
            )
        observed_inhibition = standby_replacement_inhibition_operation_id(
            state_dir,
            cluster_id=cluster_id,
            node_id=node_id,
            generation_id=generation_id,
        )
        if observed_inhibition not in {None, operation_id}:
            raise RuntimeError("VM-HA standby replacement inhibition changed before release")
        _durably_unlink(state_dir / STANDBY_REPLACEMENT_INHIBITION_FILENAME)
    finally:
        os.close(descriptor)
    return {
        "schema": STANDBY_REPLACEMENT_RELEASE_SCHEMA,
        "cluster_id": cluster_id,
        "node_id": node_id,
        "generation_id": generation_id,
        "operation_id": operation_id,
    }


def _require_planned_transfer_auto_healing_enabled(
    *,
    config: Mapping[str, Any],
    state_dir: Path,
    clock: Callable[[], float] = time.time,
) -> PolicyAgreementCertificate:
    binding, local, peer = _auto_healing_context(config)
    record = AutoHealingPolicyStore(state_dir).require_bound(
        cluster_id=binding.cluster_id,
        node_id=local.node_id,
        peer_node_id=peer.node_id,
        generation_id=binding.generation_id,
    )
    if not (
        record.phase is AutoHealingPolicyPhase.COMMITTED
        and record.desired is StandbyAutoHealing.ENABLED
        and peer_policy_agrees(
            record,
            load_peer_policy_heartbeat(state_dir, peer_node_id=peer.node_id),
            now=clock(),
        )
    ):
        raise RuntimeError(
            "planned VM-HA ownership transfer requires enabled two-member standby auto-healing"
        )
    heartbeat = load_peer_policy_heartbeat(state_dir, peer_node_id=peer.node_id)
    if heartbeat is None:
        raise RuntimeError(
            "planned VM-HA ownership transfer requires enabled two-member standby auto-healing"
        )
    try:
        return PolicyAgreementStore(state_dir).capture(
            policy=record,
            heartbeat=heartbeat,
            captured_at=clock(),
        )
    except StandbyRestorationError as error:
        raise RuntimeError(
            "planned VM-HA ownership transfer requires enabled two-member standby auto-healing"
        ) from error


def _refresh_standby_restoration_agreement(
    *,
    config: Mapping[str, Any],
    state_dir: Path,
    clock: Callable[[], float] = time.time,
) -> None:
    """Retain the latest authenticated enabled-policy agreement for cutover."""

    try:
        binding, local, peer = _auto_healing_context(config)
        policy = AutoHealingPolicyStore(state_dir).require_bound(
            cluster_id=binding.cluster_id,
            node_id=local.node_id,
            peer_node_id=peer.node_id,
            generation_id=binding.generation_id,
        )
        heartbeat = load_peer_policy_heartbeat(state_dir, peer_node_id=peer.node_id)
        now = clock()
        if heartbeat is not None and peer_policy_agrees(policy, heartbeat, now=now):
            PolicyAgreementStore(state_dir).capture(
                policy=policy,
                heartbeat=heartbeat,
                captured_at=now,
            )
    except (AutoHealingPolicyError, StandbyRestorationError):
        # Failure to refresh never manufactures authority. A previously
        # captured exact agreement remains usable only for the same committed
        # policy and generation when an automatic promotion begins.
        return


def _matching_manual_transfer_request(
    *,
    state_dir: Path,
    config: Mapping[str, Any],
    operation: str,
    configured_role: str,
    intent: TransferIntent,
    request_path: Path,
    request_schema: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Return the exact initiating request for one already-started lineage."""

    lineage = _read_transfer_lineage(
        state_dir / VM_HA_TRANSFER_LINEAGE_PATH.name,
        config=config,
    )
    if lineage is None:
        return None, False
    request = _read_manual_transfer_request(
        path=request_path,
        config=config,
        schema=request_schema,
        operation=operation,
        configured_role=configured_role,
    )
    if (
        lineage.get("intent") == intent.value
        and request is not None
        and float(request["requested_at"]) <= float(lineage["started_at"])
    ):
        return request, True
    return None, True


def _request_manual_transfer(
    *,
    state_dir: Path,
    operation: str,
    configured_role: str,
    intent: TransferIntent,
    request_path_name: str,
    request_schema: str,
    conflicting_path_name: str,
    conflicting_operation: str,
) -> dict[str, Any]:
    config = _read_vm_ha_config()
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    node = config.get("node") or {}
    if node.get("role") != configured_role:
        raise RuntimeError(
            f"manual VM-HA {operation} may be requested only on the configured {configured_role}"
        )
    require_vm_ha_runtime_prerequisites(state_dir=state_dir)
    request_path = state_dir / request_path_name
    matching, started = _matching_manual_transfer_request(
        state_dir=state_dir,
        config=config,
        operation=operation,
        configured_role=configured_role,
        intent=intent,
        request_path=request_path,
        request_schema=request_schema,
    )
    if matching is not None:
        return matching
    if started:
        raise RuntimeError(
            f"manual VM-HA {operation} cannot be requested while a durable transfer is in progress"
        )

    from .vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

    descriptor = _acquire_rearm_lock(
        state_dir / REARM_LOCK_PATH.name,
        timeout_seconds=30.0,
    )
    if descriptor is None:
        raise RuntimeError(f"manual VM-HA {operation} request writer did not quiesce")
    try:
        matching, started = _matching_manual_transfer_request(
            state_dir=state_dir,
            config=config,
            operation=operation,
            configured_role=configured_role,
            intent=intent,
            request_path=request_path,
            request_schema=request_schema,
        )
        if matching is not None:
            return matching
        if started:
            raise RuntimeError(
                f"manual VM-HA {operation} cannot be requested while a durable transfer is in progress"
            )
        if (state_dir / conflicting_path_name).exists():
            raise RuntimeError(f"conflicting manual VM-HA {conflicting_operation} request exists")
        existing = _read_manual_transfer_request(
            path=request_path,
            config=config,
            schema=request_schema,
            operation=operation,
            configured_role=configured_role,
        )
        certificate = _require_planned_transfer_auto_healing_enabled(
            config=config,
            state_dir=state_dir,
        )
        request = existing
        if request is None:
            request = {
                "schema": request_schema,
                "cluster_id": config.get("cluster_id"),
                "node_id": node.get("node_id"),
                "generation_id": (config.get("generation") or {}).get("generation_id"),
                "requested_at": time.time(),
            }
            _atomic_write_json(request_path, request)
        try:
            binding, local, peer = _auto_healing_context(config)
            StandbyRestorationStore(state_dir).arm(
                source=RestorationSource(intent.value),
                certificate=certificate,
                owner_node_id=local.node_id,
                former_owner_node_id=peer.node_id,
                allocation_id=binding.shared_allocation_id,
                generation_id=binding.generation_id,
                digests=certificate.digests,
                request_fingerprint=planned_request_fingerprint(request),
                first_operation_id=None,
                updated_at=time.time(),
            )
        except Exception:
            _durably_unlink(request_path)
            raise
        return request
    finally:
        os.close(descriptor)


def request_manual_failback(*, state_dir: Path = VM_HA_STATE_DIR) -> dict[str, Any]:
    return _request_manual_transfer(
        state_dir=state_dir,
        operation="failback",
        configured_role="active",
        intent=TransferIntent.PLANNED_FAILBACK,
        request_path_name=VM_HA_FAILBACK_PATH.name,
        request_schema="nebius-vpngw/vm-ha-manual-failback-v1",
        conflicting_path_name=VM_HA_FAILOVER_PATH.name,
        conflicting_operation="failover",
    )


def request_manual_failover(*, state_dir: Path = VM_HA_STATE_DIR) -> dict[str, Any]:
    return _request_manual_transfer(
        state_dir=state_dir,
        operation="failover",
        configured_role="passive",
        intent=TransferIntent.PLANNED_FAILOVER,
        request_path_name=VM_HA_FAILOVER_PATH.name,
        request_schema="nebius-vpngw/vm-ha-manual-failover-v1",
        conflicting_path_name=VM_HA_FAILBACK_PATH.name,
        conflicting_operation="failback",
    )


def _run_vm_ha_controller(
    runtime: VMHAControllerRuntime | DefaultVMHAControllerRuntime | None = None,
    *,
    once: bool = False,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = VM_HA_STATE_DIR,
) -> None:
    """Execute the complete verified controller with deterministic cleanup."""

    config = _read_vm_ha_config(config_path)
    if config is None:
        raise RuntimeError("VM HA controller started without an enabled node manifest")
    if runtime is None:
        require_vm_ha_runtime_prerequisites()
        runtime = build_default_vm_ha_controller_runtime(
            config_path=config_path,
            state_dir=state_dir,
            identity_proof_mode="systemd-controller",
            systemd_invocation_id=os.environ.get("INVOCATION_ID"),
        )
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
                if not all(isinstance(value, str) and value for value in expected_authority):
                    raise RuntimeError("VM-HA force-reconcile requires complete authority")
                from .vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

                rearm_lock_fd = _acquire_rearm_lock(
                    VM_HA_STATE_DIR / REARM_LOCK_PATH.name,
                    timeout_seconds=0.0,
                )
                if rearm_lock_fd is None:
                    raise RuntimeError("VM-HA force-reconcile requires the rearm writer lock")
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
                    raise RuntimeError("VM-HA force-reconcile requires the mTLS writer lock")

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
    group.add_argument(
        "--vm-ha-auto-healing-action",
        choices=(
            "status",
            "initialize",
            "adopt-replacement",
            "prepare",
            "commit",
            "arm-recovery",
            "cancel-recovery",
            "clear-recovery",
        ),
    )
    group.add_argument("--vm-ha-removal-inhibit", metavar="OPERATION_ID")
    group.add_argument("--vm-ha-removal-ready", metavar="OPERATION_ID")
    group.add_argument("--vm-ha-standby-replacement-inhibit", metavar="OPERATION_ID")
    group.add_argument("--vm-ha-standby-replacement-ready", metavar="OPERATION_ID")
    group.add_argument("--vm-ha-standby-replacement-peer-binding", metavar="OPERATION_ID")
    group.add_argument("--vm-ha-standby-replacement-release", metavar="OPERATION_ID")
    group.add_argument("--vm-ha-mtls-action", choices=ACTION_NAMES)
    group.add_argument(
        "--vm-ha-auto-healing-quiescent",
        action="store_true",
        help=argparse.SUPPRESS,
    )
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
    parser.add_argument("--vm-ha-auto-healing-request", metavar="BASE64_JSON")
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
                        "vm-ha-controller-route-reconcile-v1",
                        _VM_HA_MTLS_ROTATION_QUIESCENCE_CAPABILITY,
                        AUTO_HEALING_CAPABILITY,
                        LIVE_PEER_REPLACEMENT_CAPABILITY,
                        STANDBY_REPLACEMENT_INHIBITION_CAPABILITY,
                        STANDBY_RESTORATION_CAPABILITY,
                    ],
                    "schema": "nebius-vpngw.agent-capabilities.v1",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return

    if args.vm_ha_auto_healing_quiescent:
        try:
            require_auto_healing_writer_quiescent(VM_HA_STATE_DIR)
        except AutoHealingPolicyError as error:
            parser.exit(2, f"standby auto-healing writer is active: {error}\n")
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

    if args.vm_ha_auto_healing_action:
        try:
            response = vm_ha_auto_healing_policy(
                args.vm_ha_auto_healing_action,
                args.vm_ha_auto_healing_request,
            )
        except AutoHealingPolicyError as error:
            parser.exit(2, f"standby auto-healing policy action failed: {error}\n")
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
        runtime = build_default_vm_ha_controller_runtime(
            identity_proof_mode="systemd-preflight",
            systemd_invocation_id=os.environ.get("INVOCATION_ID"),
        )
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
    if args.vm_ha_standby_replacement_inhibit:
        print(
            json.dumps(
                install_vm_ha_standby_replacement_inhibition(
                    args.vm_ha_standby_replacement_inhibit
                ),
                sort_keys=True,
            )
        )
        return
    if args.vm_ha_standby_replacement_ready:
        try:
            response = verify_vm_ha_standby_replacement_quiescent(
                args.vm_ha_standby_replacement_ready
            )
        except StandbyReplacementNotReady as error:
            parser.exit(75, f"{error}\n")
        print(json.dumps(response, sort_keys=True))
        return
    if args.vm_ha_standby_replacement_peer_binding:
        print(
            json.dumps(
                commit_vm_ha_standby_replacement_peer_binding(
                    args.vm_ha_standby_replacement_peer_binding
                ),
                sort_keys=True,
            )
        )
        return
    if args.vm_ha_standby_replacement_release:
        print(
            json.dumps(
                release_vm_ha_standby_replacement_inhibition(
                    args.vm_ha_standby_replacement_release
                ),
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
