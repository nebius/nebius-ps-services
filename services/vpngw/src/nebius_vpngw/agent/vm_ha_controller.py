"""Deterministic, fail-closed policy for two-node VM HA.

The policy consumes already authenticated peer state and authoritative cloud
observations.  It returns at most one idempotent external action.  A caller
must durably persist ``decision.checkpoint`` before executing that action and
persist the next checkpoint after its authoritative postcondition is observed.
Peer state and checkpoints are deliberately never cloud-ownership authority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from nebius_vpngw.agent.vm_ha.models import DigestSet, PeerHeartbeat


class HAState(str, Enum):
    NORMAL = "normal"
    SUSPECT = "suspect"
    FENCING = "fencing"
    OWNERSHIP_TRANSFER = "ownership-transfer"
    PROMOTING = "promoting"
    ACTIVE = "active"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class ConfiguredRole(str, Enum):
    ACTIVE = "active"
    PASSIVE = "passive"


class ComputeState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    STOPPING = "stopping"
    TRANSITIONAL = "transitional"
    ERROR = "error"
    UNKNOWN = "unknown"


class DataPlaneMode(str, Enum):
    BLOCKED = "blocked"
    PASSIVE = "passive"
    ACTIVE = "active"


class ActionKind(str, Enum):
    INSTALL_COLD_START_GUARD = "install-cold-start-guard"
    ENTER_PASSIVE = "enter-passive"
    DISABLE_ACTIVE = "disable-active"
    STOP_FORMER_OWNER = "stop-former-owner"
    DETACH_FORMER_ATTACHMENT = "detach-former-attachment"
    ATTACH_CANDIDATE = "attach-candidate"
    CONFIRM_CANDIDATE_OWNERSHIP = "confirm-candidate-ownership"
    RECONCILE_ROUTES = "reconcile-routes"
    ENABLE_ACTIVE = "enable-active"


_PASSIVE_REPLAY_ACTIONS = frozenset(
    {
        ActionKind.STOP_FORMER_OWNER,
        ActionKind.DETACH_FORMER_ATTACHMENT,
        ActionKind.ATTACH_CANDIDATE,
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
        ActionKind.RECONCILE_ROUTES,
    }
)
_FORMER_OWNER_ACTIONS = frozenset(
    {ActionKind.STOP_FORMER_OWNER, ActionKind.DETACH_FORMER_ATTACHMENT}
)
_LOCAL_SAFETY_ACTIONS = frozenset(
    {
        ActionKind.INSTALL_COLD_START_GUARD,
        ActionKind.ENTER_PASSIVE,
        ActionKind.DISABLE_ACTIVE,
    }
)


@dataclass(frozen=True)
class LocalReadiness:
    service_healthy: bool
    static_ready: bool
    bgp_ready: bool
    xfrm_ready: bool

    @property
    def promotion_ready(self) -> bool:
        return all((self.service_healthy, self.static_ready, self.bgp_ready, self.xfrm_ready))

    @property
    def blocked_reasons(self) -> tuple[str, ...]:
        checks = (
            (self.service_healthy, "local-service-unhealthy"),
            (self.static_ready, "static-routes-not-ready"),
            (self.bgp_ready, "bgp-not-ready"),
            (self.xfrm_ready, "xfrm-not-ready"),
        )
        return tuple(reason for ready, reason in checks if not ready)


@dataclass(frozen=True)
class CloudObservation:
    """Fresh cloud truth for one controller evaluation."""

    authoritative: bool
    allocation_id: str
    observed_owner_node_id: str | None
    former_owner_node_id: str
    former_owner_compute_state: ComputeState
    former_attachment_absent: bool
    candidate_attachment_exact: bool
    ownership_re_read_exact: bool
    ownership_epoch: str = ""

    def local_attachment_exact(self, node_id: str) -> bool:
        """Return fresh exact ownership without inferring a transfer."""

        return bool(
            self.authoritative
            and self.allocation_id
            and self.ownership_epoch
            and self.observed_owner_node_id == node_id
            and self.former_attachment_absent
            and self.candidate_attachment_exact
            and self.ownership_re_read_exact
        )

    def transfer_complete(self, node_id: str) -> bool:
        """Return exact ownership plus the mandatory takeover fencing proof."""

        return bool(
            self.local_attachment_exact(node_id)
            and self.former_owner_compute_state is ComputeState.STOPPED
            and self.former_attachment_absent
        )


@dataclass(frozen=True)
class OwnershipContext:
    """Stable identity of one uninterrupted authoritative attachment."""

    owner_node_id: str
    allocation_id: str
    ownership_epoch: str


@dataclass(frozen=True)
class RouteReconciliationContext:
    """Authority and policy identity covered by one route reconciliation."""

    owner_node_id: str
    allocation_id: str
    ownership_epoch: str
    generation_id: str
    digests: DigestSet
    ownership_incarnation: int = 0
    operation_id: str = ""

    @property
    def ownership_context(self) -> OwnershipContext:
        return OwnershipContext(
            owner_node_id=self.owner_node_id,
            allocation_id=self.allocation_id,
            ownership_epoch=self.ownership_epoch,
        )


@dataclass(frozen=True)
class ControllerSnapshot:
    now: float
    boot_id: str
    cluster_id: str
    local_node_id: str
    peer_node_id: str
    configured_role: ConfiguredRole
    local_generation_id: str
    local_digests: DigestSet
    peer_heartbeat: PeerHeartbeat | None
    peer_received_at: float | None
    apply_locked: bool
    emergency_active_only: bool
    manual_failback_requested: bool
    readiness: LocalReadiness
    cloud: CloudObservation
    guard_boot_id: str | None
    data_plane_mode: DataPlaneMode
    routes_reconciled_context: RouteReconciliationContext | None

    def __post_init__(self) -> None:
        if not all((self.boot_id, self.cluster_id, self.local_node_id, self.peer_node_id)):
            raise ValueError("boot, cluster, and both node identities are required")
        if self.local_node_id == self.peer_node_id:
            raise ValueError("VM HA requires two distinct node identities")
        if not math.isfinite(self.now):
            raise ValueError("now must be finite")
        if self.peer_received_at is not None and not math.isfinite(self.peer_received_at):
            raise ValueError("peer_received_at must be finite")

    @property
    def route_reconciliation_context(self) -> RouteReconciliationContext:
        return RouteReconciliationContext(
            owner_node_id=self.local_node_id,
            allocation_id=self.cloud.allocation_id,
            ownership_epoch=self.cloud.ownership_epoch,
            generation_id=self.local_generation_id,
            digests=self.local_digests,
        )

    @property
    def ownership_context(self) -> OwnershipContext:
        return self.route_reconciliation_context.ownership_context


@dataclass(frozen=True)
class ControllerAction:
    kind: ActionKind
    operation_id: str
    boot_id: str
    target_node_id: str
    allocation_id: str
    ownership_epoch: str
    generation_id: str
    digests: DigestSet
    ownership_incarnation: int = 0


@dataclass(frozen=True)
class ControllerCheckpoint:
    """Durable recovery state; it is never allocation authority.

    The established context records continuity only.  The controller always
    pairs it with a fresh exact authoritative attachment observation.
    """

    sequence: int = 0
    state: HAState = HAState.BLOCKED
    suspect_since: float | None = None
    pending_action: ControllerAction | None = None
    established_ownership_context: OwnershipContext | None = None
    ownership_continuity_invalidated: bool = False
    ownership_incarnation: int = 0

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("checkpoint sequence must be non-negative")
        if self.ownership_incarnation < 0:
            raise ValueError("ownership incarnation must be non-negative")
        if self.ownership_continuity_invalidated:
            if self.ownership_incarnation == 0:
                raise ValueError(
                    "ownership continuity invalidation requires a positive incarnation"
                )
            if self.established_ownership_context is not None:
                raise ValueError(
                    "ownership continuity invalidation cannot retain established context"
                )
        if self.suspect_since is not None and not math.isfinite(self.suspect_since):
            raise ValueError("suspect_since must be finite")


@dataclass(frozen=True)
class ControllerResult:
    """One immutable controller decision and its required pre-effect checkpoint."""

    state: HAState
    reasons: tuple[str, ...]
    forwarding_enabled: bool
    action: ControllerAction | None
    checkpoint: ControllerCheckpoint


class CheckpointStore(Protocol):
    def load(self) -> ControllerCheckpoint: ...

    def save(self, checkpoint: ControllerCheckpoint) -> None: ...


class EffectPort(Protocol):
    """Execute each operation ID at most once and make retries idempotent."""

    def apply(self, action: ControllerAction) -> None: ...


class SnapshotPort(Protocol):
    def observe(self) -> ControllerSnapshot: ...


class VMHAController:
    """Pure two-node state machine with bounded suspicion and crash replay."""

    def __init__(self, *, peer_timeout_seconds: float, suspicion_seconds: float) -> None:
        if not math.isfinite(peer_timeout_seconds) or peer_timeout_seconds <= 0:
            raise ValueError("peer_timeout_seconds must be finite and positive")
        if not math.isfinite(suspicion_seconds) or suspicion_seconds < 0:
            raise ValueError("suspicion_seconds must be finite and non-negative")
        self.peer_timeout_seconds = peer_timeout_seconds
        self.suspicion_seconds = suspicion_seconds

    def decide(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> ControllerResult:
        """Return one safe action, checkpointed before execution."""

        pending = checkpoint.pending_action
        if pending is not None:
            expected_target = (
                snapshot.peer_node_id
                if pending.kind in _FORMER_OWNER_ACTIONS
                else snapshot.local_node_id
            )
            expected_operation = (
                f"{pending.boot_id}:{checkpoint.sequence}:{pending.kind.value}:{expected_target}"
                if isinstance(pending.kind, ActionKind)
                else ""
            )
            if not (
                checkpoint.sequence > 0
                and pending.boot_id
                and pending.target_node_id == expected_target
                and pending.ownership_incarnation == checkpoint.ownership_incarnation
                and pending.operation_id == expected_operation
            ):
                raise ValueError("pending action does not match its durable checkpoint")
            if (
                pending.boot_id == snapshot.boot_id
                and (
                    pending.kind in _PASSIVE_REPLAY_ACTIONS
                    or pending.kind is ActionKind.ENABLE_ACTIVE
                )
                and snapshot.guard_boot_id != snapshot.boot_id
            ):
                guard_action = ActionKind.INSTALL_COLD_START_GUARD
                if snapshot.data_plane_mode is DataPlaneMode.ACTIVE:
                    guard_action = ActionKind.DISABLE_ACTIVE
                return self._action(
                    HAState.BLOCKED,
                    ("checkpointed-action-requires-current-boot-guard",),
                    snapshot,
                    replace(checkpoint, state=HAState.BLOCKED, pending_action=None),
                    guard_action,
                )
            postcondition_met = bool(
                pending.boot_id == snapshot.boot_id
                and self._postcondition(pending, snapshot)
                and (
                    pending.kind in _LOCAL_SAFETY_ACTIONS
                    or (
                        pending.kind is ActionKind.ATTACH_CANDIDATE
                        and self._attach_completion_context_matches(pending, snapshot, checkpoint)
                    )
                    or (
                        pending.kind is not ActionKind.ATTACH_CANDIDATE
                        and self._pending_action_context_matches(pending, snapshot, checkpoint)
                    )
                )
            )
            if pending.boot_id != snapshot.boot_id or postcondition_met:
                checkpoint = replace(checkpoint, pending_action=None)
                if pending.kind is ActionKind.RECONCILE_ROUTES and postcondition_met:
                    checkpoint = replace(
                        checkpoint,
                        established_ownership_context=snapshot.ownership_context,
                        ownership_continuity_invalidated=False,
                    )
            elif (
                pending.kind in _PASSIVE_REPLAY_ACTIONS
                and snapshot.data_plane_mode is not DataPlaneMode.PASSIVE
            ):
                passive_action = (
                    ActionKind.DISABLE_ACTIVE
                    if snapshot.data_plane_mode is DataPlaneMode.ACTIVE
                    else ActionKind.ENTER_PASSIVE
                )
                return self._action(
                    HAState.BLOCKED,
                    ("checkpointed-action-requires-passive-dataplane",),
                    snapshot,
                    replace(checkpoint, pending_action=None),
                    passive_action,
                )
            elif not self._pending_action_safe(pending, snapshot, checkpoint):
                checkpoint = self._invalidate_ownership_if_lost(
                    snapshot,
                    replace(
                        checkpoint,
                        state=HAState.BLOCKED,
                        pending_action=None,
                    ),
                )
                if snapshot.data_plane_mode is DataPlaneMode.ACTIVE:
                    return self._action(
                        HAState.BLOCKED,
                        ("checkpointed-action-prerequisites-changed",),
                        snapshot,
                        checkpoint,
                        ActionKind.DISABLE_ACTIVE,
                    )
                return self._result(
                    HAState.BLOCKED,
                    ("checkpointed-action-prerequisites-changed",),
                    snapshot,
                    checkpoint,
                )
            else:
                return self._result(
                    checkpoint.state,
                    ("replaying-checkpointed-action",),
                    snapshot,
                    checkpoint,
                    pending,
                )

        # Every boot begins behind a guard tied to this boot identity.
        if snapshot.guard_boot_id != snapshot.boot_id:
            return self._action(
                HAState.BLOCKED,
                ("cold-start-guard-not-installed",),
                snapshot,
                checkpoint,
                ActionKind.INSTALL_COLD_START_GUARD,
            )

        if not snapshot.cloud.authoritative:
            return self._block_or_disable("cloud-ownership-unavailable", snapshot, checkpoint)

        checkpoint = self._invalidate_ownership_if_lost(snapshot, checkpoint)
        cloud_reasons = self._cloud_consistency_reasons(snapshot)
        if cloud_reasons:
            return self._block_or_disable(cloud_reasons, snapshot, checkpoint)

        local_owns = snapshot.cloud.local_attachment_exact(snapshot.local_node_id)
        if local_owns and not self._local_ownership_safe(snapshot, checkpoint):
            return self._block_or_disable(
                "local-ownership-lacks-establishment-proof", snapshot, checkpoint
            )
        if snapshot.data_plane_mode is DataPlaneMode.ACTIVE and not local_owns:
            return self._action(
                HAState.BLOCKED,
                ("active-node-lacks-exact-allocation-ownership",),
                snapshot,
                checkpoint,
                ActionKind.DISABLE_ACTIVE,
            )

        parity_reasons = self._parity_reasons(snapshot)
        if local_owns:
            return self._owner_decision(snapshot, checkpoint, parity_reasons)

        if (
            snapshot.configured_role is ConfiguredRole.PASSIVE
            and snapshot.manual_failback_requested
        ):
            return self._result(
                HAState.BLOCKED,
                ("manual-failback-invalid-for-passive-role",),
                snapshot,
                replace(checkpoint, state=HAState.BLOCKED, suspect_since=None),
            )

        # A non-owner must remain passive before it can suspect or transfer.
        if snapshot.data_plane_mode is not DataPlaneMode.PASSIVE:
            return self._action(
                HAState.NORMAL,
                ("non-owner-must-remain-passive",),
                snapshot,
                checkpoint,
                ActionKind.ENTER_PASSIVE,
            )

        if snapshot.apply_locked:
            return self._result(
                HAState.BLOCKED,
                ("apply-lock-held",),
                snapshot,
                replace(checkpoint, state=HAState.BLOCKED, suspect_since=None),
            )
        if snapshot.emergency_active_only:
            return self._result(
                HAState.BLOCKED,
                ("emergency-active-only-generation",),
                snapshot,
                replace(checkpoint, state=HAState.BLOCKED, suspect_since=None),
            )
        if parity_reasons:
            return self._result(
                HAState.BLOCKED,
                parity_reasons,
                snapshot,
                replace(checkpoint, state=HAState.BLOCKED, suspect_since=None),
            )
        if not snapshot.readiness.promotion_ready:
            return self._result(
                HAState.BLOCKED,
                snapshot.readiness.blocked_reasons,
                snapshot,
                replace(checkpoint, state=HAState.BLOCKED, suspect_since=None),
            )

        manual_failback_required = snapshot.configured_role is ConfiguredRole.ACTIVE
        if manual_failback_required and not snapshot.manual_failback_requested:
            return self._result(
                HAState.NORMAL,
                ("manual-failback-required",),
                snapshot,
                replace(checkpoint, state=HAState.NORMAL, suspect_since=None),
            )

        if not snapshot.manual_failback_requested and self._peer_fresh(snapshot):
            return self._result(
                HAState.NORMAL,
                ("authoritative-owner-peer-is-healthy",),
                snapshot,
                replace(checkpoint, state=HAState.NORMAL, suspect_since=None),
            )

        return self._transfer_decision(snapshot, checkpoint)

    def _owner_decision(
        self,
        snapshot: ControllerSnapshot,
        checkpoint: ControllerCheckpoint,
        parity_reasons: tuple[str, ...],
    ) -> ControllerResult:
        reasons = (*parity_reasons, *snapshot.readiness.blocked_reasons)
        routes_current = self._routes_current(snapshot, checkpoint)
        if snapshot.data_plane_mode is DataPlaneMode.ACTIVE:
            if not routes_current:
                return self._action(
                    HAState.BLOCKED,
                    ("active-route-reconciliation-context-stale",),
                    snapshot,
                    checkpoint,
                    ActionKind.DISABLE_ACTIVE,
                )
            state = HAState.DEGRADED if reasons else HAState.ACTIVE
            return self._result(
                state,
                reasons or ("authoritative-owner-active",),
                snapshot,
                replace(
                    checkpoint,
                    state=state,
                    suspect_since=None,
                    established_ownership_context=snapshot.ownership_context,
                ),
            )

        promotion_blockers: tuple[str, ...] = ()
        if snapshot.apply_locked:
            promotion_blockers = ("apply-lock-held",)
        elif snapshot.emergency_active_only:
            promotion_blockers = ("emergency-active-only-generation",)
        elif parity_reasons:
            promotion_blockers = parity_reasons
        if promotion_blockers:
            return self._result(
                HAState.BLOCKED,
                promotion_blockers,
                snapshot,
                replace(checkpoint, state=HAState.BLOCKED, suspect_since=None),
            )

        if not routes_current:
            if not snapshot.readiness.promotion_ready:
                return self._result(
                    HAState.DEGRADED,
                    reasons,
                    snapshot,
                    replace(checkpoint, state=HAState.DEGRADED, suspect_since=None),
                )
            return self._action(
                HAState.PROMOTING,
                ("owner-routes-require-reconciliation",),
                snapshot,
                checkpoint,
                ActionKind.RECONCILE_ROUTES,
            )
        if not snapshot.readiness.promotion_ready:
            return self._result(
                HAState.DEGRADED,
                reasons,
                snapshot,
                replace(checkpoint, state=HAState.DEGRADED, suspect_since=None),
            )
        return self._action(
            HAState.PROMOTING,
            ("exact-owner-ready-to-enable-forwarding",),
            snapshot,
            checkpoint,
            ActionKind.ENABLE_ACTIVE,
        )

    def _transfer_decision(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> ControllerResult:
        cloud = snapshot.cloud
        suspect_since = checkpoint.suspect_since
        if not snapshot.manual_failback_requested:
            if suspect_since is None:
                return self._result(
                    HAState.SUSPECT,
                    ("peer-heartbeat-stale",),
                    snapshot,
                    replace(checkpoint, state=HAState.SUSPECT, suspect_since=snapshot.now),
                )
            if snapshot.now - suspect_since < self.suspicion_seconds:
                return self._result(
                    HAState.SUSPECT,
                    ("suspicion-window-active",),
                    snapshot,
                    replace(checkpoint, state=HAState.SUSPECT),
                )

        if cloud.former_owner_compute_state is not ComputeState.STOPPED:
            if cloud.former_owner_compute_state in {
                ComputeState.ERROR,
                ComputeState.UNKNOWN,
            }:
                return self._result(
                    HAState.BLOCKED,
                    ("former-owner-compute-state-ambiguous",),
                    snapshot,
                    replace(checkpoint, state=HAState.BLOCKED),
                )
            return self._action(
                HAState.FENCING,
                ("former-owner-must-be-stopped",),
                snapshot,
                checkpoint,
                ActionKind.STOP_FORMER_OWNER,
            )
        if not cloud.former_attachment_absent:
            return self._action(
                HAState.OWNERSHIP_TRANSFER,
                ("former-allocation-attachment-must-be-absent",),
                snapshot,
                checkpoint,
                ActionKind.DETACH_FORMER_ATTACHMENT,
            )
        if not cloud.candidate_attachment_exact:
            return self._action(
                HAState.OWNERSHIP_TRANSFER,
                ("candidate-requires-exact-shared-allocation",),
                snapshot,
                checkpoint,
                ActionKind.ATTACH_CANDIDATE,
            )
        if not cloud.ownership_re_read_exact:
            return self._action(
                HAState.PROMOTING,
                ("candidate-ownership-re-read-required",),
                snapshot,
                checkpoint,
                ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
            )
        # Exact local ownership will be visible on the next observation.  The
        # retained transfer checkpoint keeps the fencing proof mandatory until
        # promotion has completed.
        return self._result(
            HAState.PROMOTING,
            ("candidate-ownership-awaiting-authoritative-observation",),
            snapshot,
            replace(checkpoint, state=HAState.PROMOTING),
        )

    def _peer_fresh(self, snapshot: ControllerSnapshot) -> bool:
        heartbeat = snapshot.peer_heartbeat
        received_at = snapshot.peer_received_at
        return bool(
            heartbeat is not None
            and received_at is not None
            and 0 <= snapshot.now - received_at < self.peer_timeout_seconds
            and heartbeat.service_healthy
        )

    @staticmethod
    def _parity_reasons(snapshot: ControllerSnapshot) -> tuple[str, ...]:
        peer = snapshot.peer_heartbeat
        if peer is None:
            return ("peer-generation-unavailable",)
        reasons: list[str] = []
        if peer.cluster_id != snapshot.cluster_id or peer.node_id != snapshot.peer_node_id:
            reasons.append("peer-identity-mismatch")
        expected_peer_role = (
            ConfiguredRole.PASSIVE
            if snapshot.configured_role is ConfiguredRole.ACTIVE
            else ConfiguredRole.ACTIVE
        )
        if peer.configured_role != expected_peer_role.value:
            reasons.append("peer-configured-role-mismatch")
        if peer.generation_id != snapshot.local_generation_id:
            reasons.append("generation-mismatch")
        if peer.digests.configuration != snapshot.local_digests.configuration:
            reasons.append("configuration-digest-mismatch")
        if peer.digests.static_routes != snapshot.local_digests.static_routes:
            reasons.append("static-route-digest-mismatch")
        if peer.digests.bgp_policy != snapshot.local_digests.bgp_policy:
            reasons.append("bgp-policy-digest-mismatch")
        return tuple(reasons)

    @staticmethod
    def _cloud_consistency_reasons(snapshot: ControllerSnapshot) -> tuple[str, ...]:
        cloud = snapshot.cloud
        reasons: list[str] = []
        if not cloud.allocation_id:
            reasons.append("shared-allocation-identity-missing")
        if not cloud.ownership_epoch:
            reasons.append("authoritative-ownership-epoch-missing")
        if cloud.former_owner_node_id != snapshot.peer_node_id:
            reasons.append("former-owner-identity-mismatch")
        if cloud.observed_owner_node_id not in {
            None,
            snapshot.local_node_id,
            snapshot.peer_node_id,
        }:
            reasons.append("unexpected-allocation-owner")
        if (
            cloud.former_attachment_absent
            and cloud.observed_owner_node_id == cloud.former_owner_node_id
        ):
            reasons.append("former-attachment-observation-inconsistent")
        if (
            cloud.observed_owner_node_id == snapshot.local_node_id
            and not cloud.candidate_attachment_exact
        ):
            reasons.append("candidate-owner-observation-without-exact-attachment")
        if (
            cloud.observed_owner_node_id == snapshot.local_node_id
            and not cloud.former_attachment_absent
        ):
            reasons.append("candidate-owner-observation-with-former-attachment-present")
        if cloud.candidate_attachment_exact and (
            cloud.observed_owner_node_id != snapshot.local_node_id
        ):
            reasons.append("candidate-attachment-observation-inconsistent")
        if cloud.ownership_re_read_exact and not cloud.candidate_attachment_exact:
            reasons.append("ownership-confirmation-without-exact-attachment")
        return tuple(reasons)

    def _block_or_disable(
        self,
        reason: str | tuple[str, ...],
        snapshot: ControllerSnapshot,
        checkpoint: ControllerCheckpoint,
    ) -> ControllerResult:
        reasons = (reason,) if isinstance(reason, str) else reason
        if snapshot.data_plane_mode is DataPlaneMode.ACTIVE:
            return self._action(
                HAState.BLOCKED,
                reasons,
                snapshot,
                checkpoint,
                ActionKind.DISABLE_ACTIVE,
            )
        return self._result(
            HAState.BLOCKED,
            reasons,
            snapshot,
            replace(checkpoint, state=HAState.BLOCKED, suspect_since=None),
        )

    def _action(
        self,
        state: HAState,
        reasons: tuple[str, ...],
        snapshot: ControllerSnapshot,
        checkpoint: ControllerCheckpoint,
        kind: ActionKind,
    ) -> ControllerResult:
        sequence = checkpoint.sequence + 1
        target = (
            snapshot.cloud.former_owner_node_id
            if kind in _FORMER_OWNER_ACTIONS
            else snapshot.local_node_id
        )
        action = ControllerAction(
            kind=kind,
            operation_id=f"{snapshot.boot_id}:{sequence}:{kind.value}:{target}",
            boot_id=snapshot.boot_id,
            target_node_id=target,
            allocation_id=snapshot.cloud.allocation_id,
            ownership_epoch=snapshot.cloud.ownership_epoch,
            generation_id=snapshot.local_generation_id,
            digests=snapshot.local_digests,
            ownership_incarnation=checkpoint.ownership_incarnation,
        )
        next_checkpoint = replace(
            checkpoint,
            sequence=sequence,
            state=state,
            pending_action=action,
        )
        return self._result(state, reasons, snapshot, next_checkpoint, action)

    @staticmethod
    def _postcondition(action: ControllerAction, snapshot: ControllerSnapshot) -> bool:
        cloud = snapshot.cloud
        kind = action.kind
        return {
            ActionKind.INSTALL_COLD_START_GUARD: snapshot.guard_boot_id == snapshot.boot_id
            and snapshot.data_plane_mode is DataPlaneMode.BLOCKED,
            ActionKind.ENTER_PASSIVE: snapshot.data_plane_mode is DataPlaneMode.PASSIVE,
            ActionKind.DISABLE_ACTIVE: snapshot.data_plane_mode is not DataPlaneMode.ACTIVE,
            ActionKind.STOP_FORMER_OWNER: cloud.former_owner_compute_state is ComputeState.STOPPED,
            ActionKind.DETACH_FORMER_ATTACHMENT: cloud.former_attachment_absent,
            ActionKind.ATTACH_CANDIDATE: cloud.candidate_attachment_exact,
            ActionKind.CONFIRM_CANDIDATE_OWNERSHIP: cloud.transfer_complete(snapshot.local_node_id),
            ActionKind.RECONCILE_ROUTES: snapshot.routes_reconciled_context
            == replace(
                snapshot.route_reconciliation_context,
                ownership_incarnation=action.ownership_incarnation,
                operation_id=action.operation_id,
            ),
            ActionKind.ENABLE_ACTIVE: snapshot.data_plane_mode is DataPlaneMode.ACTIVE
            and cloud.local_attachment_exact(snapshot.local_node_id),
        }[kind]

    def _pending_action_safe(
        self,
        action: ControllerAction,
        snapshot: ControllerSnapshot,
        checkpoint: ControllerCheckpoint,
    ) -> bool:
        """Revalidate mutable authority before replaying a checkpointed effect."""

        kind = action.kind
        cloud = snapshot.cloud
        expected_target = (
            cloud.former_owner_node_id if kind in _FORMER_OWNER_ACTIONS else snapshot.local_node_id
        )
        if action.target_node_id != expected_target:
            return False
        if kind in _LOCAL_SAFETY_ACTIONS:
            return True
        if not self._pending_action_context_matches(action, snapshot, checkpoint):
            return False
        if not cloud.authoritative or not self._promotion_gates_clear(snapshot):
            return False
        if kind in _PASSIVE_REPLAY_ACTIONS and (
            snapshot.data_plane_mode is not DataPlaneMode.PASSIVE
        ):
            return False
        if kind in {
            ActionKind.STOP_FORMER_OWNER,
            ActionKind.DETACH_FORMER_ATTACHMENT,
            ActionKind.ATTACH_CANDIDATE,
            ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
        } and not self._transfer_intent_valid(snapshot, checkpoint):
            return False
        if kind is ActionKind.STOP_FORMER_OWNER:
            return cloud.observed_owner_node_id == cloud.former_owner_node_id
        if kind is ActionKind.DETACH_FORMER_ATTACHMENT:
            return bool(
                cloud.former_owner_compute_state is ComputeState.STOPPED
                and cloud.observed_owner_node_id == cloud.former_owner_node_id
            )
        if kind is ActionKind.ATTACH_CANDIDATE:
            return bool(
                cloud.former_owner_compute_state is ComputeState.STOPPED
                and cloud.former_attachment_absent
                and cloud.observed_owner_node_id is None
            )
        if kind is ActionKind.CONFIRM_CANDIDATE_OWNERSHIP:
            return bool(
                cloud.former_owner_compute_state is ComputeState.STOPPED
                and cloud.former_attachment_absent
                and cloud.candidate_attachment_exact
                and cloud.observed_owner_node_id == snapshot.local_node_id
            )
        if kind is ActionKind.RECONCILE_ROUTES:
            return (
                self._local_ownership_safe(snapshot, checkpoint)
                and snapshot.readiness.promotion_ready
            )
        if kind is ActionKind.ENABLE_ACTIVE:
            return bool(
                self._local_ownership_safe(snapshot, checkpoint)
                and snapshot.readiness.promotion_ready
                and self._routes_current(snapshot, checkpoint)
            )
        return False

    @staticmethod
    def _pending_action_context_matches(
        action: ControllerAction,
        snapshot: ControllerSnapshot,
        checkpoint: ControllerCheckpoint,
    ) -> bool:
        return bool(
            action.allocation_id == snapshot.cloud.allocation_id
            and action.ownership_epoch == snapshot.cloud.ownership_epoch
            and action.generation_id == snapshot.local_generation_id
            and action.digests == snapshot.local_digests
            and action.ownership_incarnation == checkpoint.ownership_incarnation
        )

    @staticmethod
    def _attach_completion_context_matches(
        action: ControllerAction,
        snapshot: ControllerSnapshot,
        checkpoint: ControllerCheckpoint,
    ) -> bool:
        """Bind attach completion to the exact newer Compute revision."""

        cloud = snapshot.cloud
        pre_revision = action.ownership_epoch
        observed_revision = cloud.ownership_epoch
        try:
            revisions_advance = bool(
                pre_revision.isascii()
                and observed_revision.isascii()
                and pre_revision.isdecimal()
                and observed_revision.isdecimal()
                and int(pre_revision) > 0
                and int(observed_revision) > int(pre_revision)
            )
        except ValueError:
            revisions_advance = False
        return bool(
            revisions_advance
            and cloud.authoritative
            and action.target_node_id == snapshot.local_node_id
            and action.allocation_id == cloud.allocation_id
            and action.generation_id == snapshot.local_generation_id
            and action.digests == snapshot.local_digests
            and action.ownership_incarnation == checkpoint.ownership_incarnation
            and cloud.former_owner_node_id == snapshot.peer_node_id
            and cloud.former_owner_compute_state is ComputeState.STOPPED
            and cloud.former_attachment_absent
            and cloud.candidate_attachment_exact
            and not cloud.ownership_re_read_exact
            and cloud.observed_owner_node_id == snapshot.local_node_id
        )

    def _promotion_gates_clear(self, snapshot: ControllerSnapshot) -> bool:
        return not (
            snapshot.apply_locked
            or snapshot.emergency_active_only
            or self._parity_reasons(snapshot)
            or not snapshot.readiness.promotion_ready
        )

    def _transfer_intent_valid(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> bool:
        if snapshot.manual_failback_requested:
            return snapshot.configured_role is ConfiguredRole.ACTIVE
        if snapshot.configured_role is ConfiguredRole.ACTIVE:
            return False
        return bool(
            checkpoint.suspect_since is not None
            and snapshot.now - checkpoint.suspect_since >= self.suspicion_seconds
            and not self._peer_fresh(snapshot)
        )

    @staticmethod
    def _transfer_in_progress(
        snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> bool:
        return bool(snapshot.manual_failback_requested or checkpoint.suspect_since is not None)

    def _local_ownership_safe(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> bool:
        cloud = snapshot.cloud
        if not cloud.local_attachment_exact(snapshot.local_node_id):
            return False
        if cloud.transfer_complete(snapshot.local_node_id):
            return True
        if checkpoint.ownership_continuity_invalidated:
            return False
        if checkpoint.established_ownership_context == snapshot.ownership_context:
            return True
        return bool(
            snapshot.configured_role is ConfiguredRole.ACTIVE
            and not self._transfer_in_progress(snapshot, checkpoint)
        )

    def _invalidate_ownership_if_lost(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> ControllerCheckpoint:
        """Persist loss of exact ownership independently of caller epochs."""

        cloud = snapshot.cloud
        if not cloud.authoritative or cloud.local_attachment_exact(snapshot.local_node_id):
            return checkpoint
        return replace(
            checkpoint,
            established_ownership_context=None,
            ownership_continuity_invalidated=True,
            ownership_incarnation=(
                checkpoint.ownership_incarnation
                if checkpoint.ownership_continuity_invalidated
                else checkpoint.ownership_incarnation + 1
            ),
        )

    @staticmethod
    def _route_context(
        snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> RouteReconciliationContext:
        return replace(
            snapshot.route_reconciliation_context,
            ownership_incarnation=checkpoint.ownership_incarnation,
        )

    def _routes_current(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> bool:
        reconciled = snapshot.routes_reconciled_context
        return bool(
            not checkpoint.ownership_continuity_invalidated
            and reconciled is not None
            and reconciled.operation_id
            and replace(reconciled, operation_id="") == self._route_context(snapshot, checkpoint)
        )

    @staticmethod
    def _result(
        state: HAState,
        reasons: tuple[str, ...],
        snapshot: ControllerSnapshot,
        checkpoint: ControllerCheckpoint,
        action: ControllerAction | None = None,
    ) -> ControllerResult:
        return ControllerResult(
            state=state,
            reasons=reasons,
            forwarding_enabled=bool(
                action is None
                and state in {HAState.ACTIVE, HAState.DEGRADED}
                and snapshot.data_plane_mode is DataPlaneMode.ACTIVE
                and snapshot.cloud.local_attachment_exact(snapshot.local_node_id)
            ),
            action=action,
            checkpoint=checkpoint,
        )


class RecoverableController:
    """Small orchestration shell enforcing checkpoint-before-effect ordering."""

    def __init__(
        self,
        *,
        policy: VMHAController,
        snapshots: SnapshotPort,
        checkpoints: CheckpointStore,
        effects: EffectPort,
    ) -> None:
        self.policy = policy
        self.snapshots = snapshots
        self.checkpoints = checkpoints
        self.effects = effects

    def step(self) -> ControllerResult:
        checkpoint = self.checkpoints.load()
        decision = self.policy.decide(self.snapshots.observe(), checkpoint)
        if decision.checkpoint != checkpoint:
            self.checkpoints.save(decision.checkpoint)
        if decision.action is not None:
            self.effects.apply(decision.action)
        return decision
