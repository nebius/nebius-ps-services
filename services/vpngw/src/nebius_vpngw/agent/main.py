from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from nebius_vpngw.schema import VMHARuntimeBinding

from .firewall_manager import update_firewall_from_config
from .frr_renderer import FRRRenderer
from .routing_guard import (
    acquire_routing_lock,
    enforce_routing_invariants,
    enforce_routing_invariants_locked,
    require_vm_ha_current_boot_ready,
)
from .state_store import StateStore
from .strongswan_renderer import StrongSwanRenderer
from .vm_ha.models import (
    DigestSet,
    PeerHeartbeat,
    StalePeerStateError,
    StateValidationError,
)
from .vm_ha.runtime import VMHARuntimePorts, build_runtime_ports
from .vm_ha.store import AtomicGenerationStore
from .vm_ha.transport import PeerTransportError
from .vm_ha_controller import (
    ActionKind,
    CloudObservation,
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
    RouteReconciliationContext,
    VMHAController,
)
from .xfrm_manager import XFRMManager

CONFIG_PATH = Path("/etc/nebius-vpngw/config-resolved.yaml")
STATE_PATH = Path("/etc/nebius-vpngw/last-applied.json")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
VM_HA_STATE_DIR = Path("/var/lib/nebius-vpngw/vm-ha")
VM_HA_CHECKPOINT_PATH = VM_HA_STATE_DIR / "controller-checkpoint.json"
VM_HA_STATUS_PATH = VM_HA_STATE_DIR / "status.json"
VM_HA_GUARD_PATH = VM_HA_STATE_DIR / "guard.json"
VM_HA_FAILBACK_PATH = VM_HA_STATE_DIR / "manual-failback.json"
VM_HA_EFFECT_RECEIPT_PATH = VM_HA_STATE_DIR / "effect-receipt.json"
VM_HA_APPLY_LOCK_PATH = VM_HA_STATE_DIR / "apply.lock"
VM_HA_EMERGENCY_PATH = VM_HA_STATE_DIR / "emergency-active-only.json"

_CHECKPOINT_SCHEMA = "nebius-vpngw/vm-ha-controller-checkpoint-v1"
_STATUS_SCHEMA = "nebius-vpngw/vm-ha-status-v1"
_RUNTIME_BLOCKERS: tuple[str, ...] = ()


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
        }
    established = (
        asdict(checkpoint.established_ownership_context)
        if checkpoint.established_ownership_context is not None
        else None
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
    }


def controller_checkpoint_from_dict(payload: object) -> ControllerCheckpoint:
    """Restore the exact checkpoint; malformed or partial state always fails closed."""

    expected = {
        "schema",
        "sequence",
        "state",
        "suspect_since",
        "pending_action",
        "established_ownership_context",
        "ownership_continuity_invalidated",
        "ownership_incarnation",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("controller checkpoint has an invalid shape")
    if payload["schema"] != _CHECKPOINT_SCHEMA:
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
        if not isinstance(pending_payload, dict) or set(pending_payload) != pending_expected:
            raise ValueError("pending controller action has an invalid shape")
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

    return ControllerCheckpoint(
        sequence=int(payload["sequence"]),
        state=HAState(str(payload["state"])),
        suspect_since=(
            None if payload["suspect_since"] is None else float(payload["suspect_since"])
        ),
        pending_action=pending,
        established_ownership_context=ownership,
        ownership_continuity_invalidated=bool(payload["ownership_continuity_invalidated"]),
        ownership_incarnation=int(payload["ownership_incarnation"]),
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
    apply_locked: Callable[[], bool] = lambda: False
    emergency_active_only: Callable[[], bool] = lambda: False
    manual_failback_requested: Callable[[], bool] = lambda: False


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


def _manual_failback_provider(*, state_dir: Path, config: Mapping[str, Any]) -> Callable[[], bool]:
    path = state_dir / VM_HA_FAILBACK_PATH.name
    expected = {
        "cluster_id": config.get("cluster_id"),
        "node_id": (config.get("node") or {}).get("node_id"),
        "generation_id": (config.get("generation") or {}).get("generation_id"),
    }

    def requested() -> bool:
        if not path.exists():
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not (
            isinstance(payload, dict)
            and set(payload) == {"schema", "cluster_id", "node_id", "generation_id", "requested_at"}
            and payload.get("schema") == "nebius-vpngw/vm-ha-manual-failback-v1"
            and all(payload.get(key) == value for key, value in expected.items())
            and isinstance(payload.get("requested_at"), (int, float))
            and not isinstance(payload.get("requested_at"), bool)
        ):
            raise ValueError("VM-HA manual failback request is invalid or stale")
        return True

    return requested


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

    def observe(self) -> ControllerSnapshot:
        peer, peer_received_at = self.providers.peer()
        cloud = self.providers.cloud()
        if cloud.allocation_id != self.allocation_id:
            raise ValueError("VM-HA cloud observation has the wrong allocation identity")
        guard_path = self.state_dir / VM_HA_GUARD_PATH.name
        guard = json.loads(guard_path.read_text(encoding="utf-8")) if guard_path.exists() else {}
        if not isinstance(guard, Mapping):
            raise ValueError("VM-HA guard record is invalid")
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
            apply_locked=self.providers.apply_locked(),
            emergency_active_only=self.providers.emergency_active_only(),
            manual_failback_requested=self.providers.manual_failback_requested(),
            readiness=self.providers.readiness(),
            cloud=cloud,
            guard_boot_id=str(guard.get("guard_boot_id")) if guard.get("guard_boot_id") else None,
            data_plane_mode=self.providers.data_plane(),
            routes_reconciled_context=self.providers.routes(),
            route_runtime_id=self.route_runtime_id,
        )
        self.last_snapshot = snapshot
        return snapshot


class VMHAEffectAdapter:
    """Dispatch exact controller effects and durably deduplicate operation IDs."""

    def __init__(
        self,
        handlers: Mapping[ActionKind, Callable[[ControllerAction], None]],
        *,
        receipt_path: Path = VM_HA_EFFECT_RECEIPT_PATH,
    ) -> None:
        if set(handlers) != set(ActionKind):
            raise ValueError("VM-HA effect adapter requires every controller action")
        self.handlers = dict(handlers)
        self.receipt_path = receipt_path

    def _completed_operation(self) -> tuple[str, str] | None:
        if not self.receipt_path.exists():
            return None
        value = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {"schema", "operation_id", "kind"}:
            raise ValueError("VM-HA effect receipt is invalid")
        if value["schema"] != "nebius-vpngw/vm-ha-effect-receipt-v1":
            raise ValueError("VM-HA effect receipt has an unsupported schema")
        return str(value["operation_id"]), str(value["kind"])

    def apply(self, action: ControllerAction) -> None:
        completed = self._completed_operation()
        if completed is not None and completed[0] == action.operation_id:
            if completed[1] != action.kind.value:
                raise ValueError("VM-HA effect receipt operation kind does not match")
            # A process stop restores the guard after an effect completed.  The
            # controller has independently re-proved replay safety, so the
            # idempotent handler must re-establish the postcondition.
        self.handlers[action.kind](action)
        _atomic_write_json(
            self.receipt_path,
            {
                "schema": "nebius-vpngw/vm-ha-effect-receipt-v1",
                "operation_id": action.operation_id,
                "kind": action.kind.value,
            },
        )


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
        payload = {
            "schema": _STATUS_SCHEMA,
            "state": result.state.value,
            "reasons": list(result.reasons),
            "recovery_action": None if ready else "nebius-vpngw vm-ha-recover",
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
            "candidate_attachment_exact": snapshot.cloud.candidate_attachment_exact,
            "ownership_re_read_exact": snapshot.cloud.ownership_re_read_exact,
            "generation_id": snapshot.local_generation_id,
            "digests": snapshot.local_digests.to_dict(),
            "promotion_ready": ready,
            "pending_operation_id": (
                result.checkpoint.pending_action.operation_id
                if result.checkpoint.pending_action
                else None
            ),
        }
        _atomic_write_json(self.status_path, payload)
        return payload

    def step(self) -> dict[str, Any]:
        try:
            return self._write_status(self.controller.step())
        except Exception:
            self.guard()
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
        manual_failback_path: Path | None = None,
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
        self.manual_failback_path = manual_failback_path
        self.clock = clock
        self.peer_timeout_seconds = peer_timeout_seconds
        self.peer_send_attempts = peer_send_attempts
        self.peer_retry_seconds = peer_retry_seconds
        self.sleeper = sleeper
        self.started_guarded = False
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
            if (
                self.manual_failback_path is not None
                and self.ports.local.role.value == "active"
                and status.get("observed_owner_node_id") == self.ports.local.node_id
                and self.manual_failback_path.exists()
            ):
                self.manual_failback_path.unlink()
                directory_fd = os.open(self.manual_failback_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
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


def build_default_vm_ha_controller_runtime(
    *,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = VM_HA_STATE_DIR,
    clock: Callable[[], float] = time.time,
    boot_id: Callable[[], str] = _boot_id,
) -> DefaultVMHAControllerRuntime:
    """Build the only production VM-HA runtime from the installed manifest."""

    config = _read_vm_ha_config(config_path)
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    current_boot_id = boot_id()
    ports = build_runtime_ports(
        config,
        state_dir=state_dir,
        replay_store=AtomicGenerationStore(state_dir / "generation-store"),
        clock=clock,
    )
    try:
        manual_failback = _manual_failback_provider(state_dir=state_dir, config=config)
        controller = build_vm_ha_controller_runtime(
            config_path=config_path,
            providers=VMHASnapshotProviders(
                peer=ports.peer_runtime.observe,
                readiness=ports.routes.readiness,
                cloud=ports.cloud.observe,
                data_plane=ports.data_plane.mode,
                routes=ports.routes.receipt_context,
                apply_locked=lambda: _strict_boolean_record(
                    state_dir / VM_HA_APPLY_LOCK_PATH.name,
                    schema="nebius-vpngw/vm-ha-apply-lock-v1",
                    field="apply_locked",
                ),
                emergency_active_only=lambda: _strict_boolean_record(
                    state_dir / VM_HA_EMERGENCY_PATH.name,
                    schema="nebius-vpngw/vm-ha-emergency-active-only-v1",
                    field="emergency_active_only",
                ),
                manual_failback_requested=manual_failback,
            ),
            handlers=ports.handlers(),
            state_dir=state_dir,
            clock=clock,
            boot_id=lambda: current_boot_id,
        )
        return DefaultVMHAControllerRuntime(
            controller,
            ports,
            boot_id=current_boot_id,
            sequence_path=state_dir / "outbound-heartbeat-sequence.json",
            guard_path=state_dir / VM_HA_GUARD_PATH.name,
            manual_failback_path=state_dir / VM_HA_FAILBACK_PATH.name,
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
        providers=providers,
        state_dir=state_dir,
        clock=clock,
        boot_id=boot_id,
    )
    return VMHAControllerRuntime(
        snapshots=snapshots,
        effects=VMHAEffectAdapter(
            handlers,
            receipt_path=state_dir / VM_HA_EFFECT_RECEIPT_PATH.name,
        ),
        checkpoints=VMHACheckpointFileStore(state_dir / VM_HA_CHECKPOINT_PATH.name),
        status_path=state_dir / VM_HA_STATUS_PATH.name,
        guard=lambda: install_vm_ha_cold_start_guard(
            state_dir=state_dir,
            boot_id=boot_id(),
        ),
    )


def _read_vm_ha_config(path: Path = CONFIG_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    vm_ha = payload.get("vm_ha")
    if not isinstance(vm_ha, dict):
        return None
    if vm_ha.get("enabled") is False:
        return None
    resolved = dict(vm_ha)
    if "connections" in payload:
        resolved["connections"] = payload["connections"]
    return resolved


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
        "recovery_action": (
            "install a release that wires every authoritative VM-HA runtime adapter, "
            "then run nebius-vpngw vm-ha-recover"
        ),
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
        "candidate_attachment_exact": False,
        "ownership_re_read_exact": False,
        "generation_id": ((config or {}).get("generation") or {}).get("generation_id"),
        "digests": ((config or {}).get("generation") or {}).get("digests"),
        "promotion_ready": False,
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


def install_vm_ha_cold_start_guard(
    *, state_dir: Path = VM_HA_STATE_DIR, boot_id: str | None = None
) -> dict[str, Any]:
    """Disable forwarding before VPN services and bind that proof to this boot."""

    current_boot_id = boot_id or _boot_id()
    lock_fd = acquire_routing_lock(blocking=True)
    if lock_fd is None:
        raise RuntimeError("cannot acquire VM-HA routing guard lock")
    try:
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
            "guard_boot_id": current_boot_id,
            "data_plane_mode": "blocked",
            "installed_at": time.time(),
        }
        _atomic_write_json(state_dir / VM_HA_GUARD_PATH.name, record)
        return record
    finally:
        os.close(lock_fd)


def vm_ha_status(*, state_dir: Path = VM_HA_STATE_DIR) -> dict[str, Any]:
    """Return actionable, secret-free HA status without inferring cloud authority."""

    if vm_ha_runtime_blockers():
        return _blocked_vm_ha_status(state_dir=state_dir)

    status_path = state_dir / VM_HA_STATUS_PATH.name
    if status_path.exists():
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != _STATUS_SCHEMA:
            raise ValueError("VM-HA status record is invalid")
        guard_path = state_dir / VM_HA_GUARD_PATH.name
        guard = json.loads(guard_path.read_text(encoding="utf-8")) if guard_path.exists() else {}
        if payload.get("promotion_ready") is True and not (
            isinstance(guard, dict)
            and guard.get("data_plane_mode") == "active"
            and guard.get("guard_boot_id") == payload.get("guard_boot_id")
        ):
            payload.update(
                state=HAState.BLOCKED.value,
                reasons=["current-boot-guard-not-active"],
                recovery_action="nebius-vpngw vm-ha-recover",
                controller_ready_boot_id=None,
                data_plane_mode="blocked",
                promotion_ready=False,
            )
        return payload

    guard_path = state_dir / VM_HA_GUARD_PATH.name
    guard = json.loads(guard_path.read_text(encoding="utf-8")) if guard_path.exists() else {}
    checkpoint = VMHACheckpointFileStore(state_dir / VM_HA_CHECKPOINT_PATH.name).load()
    return {
        "schema": _STATUS_SCHEMA,
        "state": checkpoint.state.value,
        "reasons": ["authoritative-cloud-observation-not-yet-recorded"],
        "recovery_action": "nebius-vpngw vm-ha-recover",
        "guard_boot_id": guard.get("guard_boot_id"),
        "data_plane_mode": guard.get("data_plane_mode", "blocked"),
        "observed_owner_node_id": None,
        "allocation_id": None,
        "ownership_epoch": None,
        "former_owner_compute_state": "unknown",
        "former_attachment_absent": False,
        "candidate_attachment_exact": False,
        "ownership_re_read_exact": False,
        "generation_id": None,
        "digests": None,
        "promotion_ready": False,
        "pending_operation_id": (
            checkpoint.pending_action.operation_id if checkpoint.pending_action else None
        ),
    }


def request_manual_failback(*, state_dir: Path = VM_HA_STATE_DIR) -> dict[str, Any]:
    config = _read_vm_ha_config()
    if config is None:
        raise RuntimeError("VM HA is not enabled on this node")
    node = config.get("node") or {}
    if node.get("role") != "active":
        raise RuntimeError("manual VM-HA failback may be requested only on the configured active")
    require_vm_ha_runtime_prerequisites(state_dir=state_dir)
    request = {
        "schema": "nebius-vpngw/vm-ha-manual-failback-v1",
        "cluster_id": config.get("cluster_id"),
        "node_id": node.get("node_id"),
        "generation_id": (config.get("generation") or {}).get("generation_id"),
        "requested_at": time.time(),
    }
    _atomic_write_json(state_dir / VM_HA_FAILBACK_PATH.name, request)
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
        while True:
            status = runtime.step()
            if (
                isinstance(runtime, DefaultVMHAControllerRuntime)
                and not ready_notified
                and status.get("data_plane_mode") in {"blocked", "passive", "active"}
            ):
                notify_socket = os.environ.get("NOTIFY_SOCKET")
                if not notify_socket:
                    raise RuntimeError("VM-HA systemd readiness socket is unavailable")
                subprocess.run(
                    ["/usr/bin/systemd-notify", "--ready", "--status=VM-HA guard resolved"],
                    check=True,
                    timeout=5,
                )
                ready_notified = True
            if once:
                return
            time.sleep(5)
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

    def reload(self) -> None:
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
            if _read_vm_ha_config(CONFIG_PATH) is not None:
                require_vm_ha_current_boot_readiness()

            try:
                update_firewall_from_config(cfg)
            except Exception as e:
                print(f"[Agent] WARNING: Firewall update failed: {e}")

            interface_endpoints = self.ss.build_interface_endpoints(cfg)
            self.frr.ensure_local_prefix_routes(cfg)

            if not self.state.is_changed(cfg):
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
    parser = argparse.ArgumentParser(add_help=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--vm-ha-guard", action="store_true")
    group.add_argument("--vm-ha-controller", action="store_true")
    group.add_argument("--vm-ha-preflight", action="store_true")
    group.add_argument("--vm-ha-status", action="store_true")
    group.add_argument("--vm-ha-ready", action="store_true")
    group.add_argument("--vm-ha-recover", action="store_true")
    group.add_argument("--vm-ha-manual-failback", action="store_true")
    args = parser.parse_args()

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
        require_vm_ha_current_boot_readiness()
        return
    if args.vm_ha_recover:
        # Recovery is deliberately non-bypassing: validate durable state and report
        # the exact next pending operation for the normal controller service.
        print(json.dumps(vm_ha_status(), sort_keys=True))
        return
    if args.vm_ha_manual_failback:
        print(json.dumps(request_manual_failback(), sort_keys=True))
        return
    _run_agent()


if __name__ == "__main__":
    main()
