from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
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
from .vm_ha.models import DigestSet, PeerHeartbeat
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

_CHECKPOINT_SCHEMA = "nebius-vpngw/vm-ha-controller-checkpoint-v1"
_STATUS_SCHEMA = "nebius-vpngw/vm-ha-status-v1"
_RUNTIME_BLOCKERS = (
    "authoritative-allocation-identity-unavailable",
    "authoritative-compute-state-adapter-unavailable",
    "authoritative-nic-attachment-adapter-unavailable",
    "authenticated-peer-transport-unavailable",
    "route-runtime-adapter-unavailable",
)


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
        if completed == (action.operation_id, action.kind.value):
            return
        if completed is not None and completed[0] == action.operation_id:
            raise ValueError("VM-HA effect receipt operation kind does not match")
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
    return vm_ha if isinstance(vm_ha, dict) else None


def vm_ha_runtime_blockers() -> tuple[str, ...]:
    """Return capability owners that are not yet wired into the service shell.

    These are deliberately capabilities, not configuration keys.  Treating an
    operator-supplied identifier as authoritative cloud, peer, or route proof
    would let an incomplete integration cross the activation boundary.
    """

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
    """Persist a truthful BLOCKED record and refuse the activation boundary."""

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


def vm_ha_status(*, state_dir: Path = VM_HA_STATE_DIR) -> dict[str, Any]:
    """Return actionable, secret-free HA status without inferring cloud authority."""

    if vm_ha_runtime_blockers():
        return _blocked_vm_ha_status(state_dir=state_dir)

    status_path = state_dir / VM_HA_STATUS_PATH.name
    if status_path.exists():
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != _STATUS_SCHEMA:
            raise ValueError("VM-HA status record is invalid")
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
    runtime: VMHAControllerRuntime | None = None,
    *,
    once: bool = False,
    config_path: Path = CONFIG_PATH,
) -> None:
    """Execute the verified controller, or fail closed when ports are unwired."""

    config = _read_vm_ha_config(config_path)
    if config is None:
        raise RuntimeError("VM HA controller started without an enabled node manifest")
    if runtime is None:
        require_vm_ha_runtime_prerequisites()
        raise RuntimeError("VM-HA runtime ports are unavailable")
    while True:
        runtime.step()
        if once:
            return
        time.sleep(5)


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
