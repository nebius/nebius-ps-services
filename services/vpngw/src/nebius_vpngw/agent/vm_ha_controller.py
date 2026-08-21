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
    DEGRADED_PATH = "degraded-path"
    REPAIRING = "repairing"
    REPAIR_EXHAUSTED = "repair-exhausted"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class ConfiguredRole(str, Enum):
    ACTIVE = "active"
    PASSIVE = "passive"


class TransferIntent(str, Enum):
    """One role-directed transfer trigger, independent of execution state."""

    PLANNED_FAILOVER = "planned-failover"
    PLANNED_FAILBACK = "planned-failback"
    AUTOMATIC_FAILOVER = "automatic-failover"
    APPLY_OWNER_ADOPTION = "apply-owner-adoption"


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
    REPAIR_LOCAL_DATAPLANE = "repair-local-dataplane"
    STOP_FORMER_OWNER = "stop-former-owner"
    DETACH_FORMER_ATTACHMENT = "detach-former-attachment"
    DETACH_CANDIDATE_FOR_REPROOF = "detach-candidate-for-reproof"
    ATTACH_CANDIDATE = "attach-candidate"
    CONFIRM_CANDIDATE_OWNERSHIP = "confirm-candidate-ownership"
    PREPARE_CANDIDATE_DATAPLANE = "prepare-candidate-dataplane"
    RECONCILE_ROUTES = "reconcile-routes"
    ENABLE_ACTIVE = "enable-active"


_PASSIVE_REPLAY_ACTIONS = frozenset(
    {
        ActionKind.STOP_FORMER_OWNER,
        ActionKind.DETACH_FORMER_ATTACHMENT,
        ActionKind.DETACH_CANDIDATE_FOR_REPROOF,
        ActionKind.ATTACH_CANDIDATE,
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
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

_REPAIR_BUDGET_SECONDS = 5.0
_REPAIR_FENCE_RESERVE_SECONDS = 1.0
_REPAIR_HEALTHY_RESET_SECONDS = 60.0


@dataclass(frozen=True)
class LocalReadiness:
    service_healthy: bool
    static_ready: bool
    bgp_ready: bool
    xfrm_ready: bool
    path_degraded: bool = False
    degraded_reasons: tuple[str, ...] = ()
    candidate_preparation_required: bool = False
    cold_standby_ready: bool = False
    routing_hygiene_ready: bool = True

    @property
    def promotion_ready(self) -> bool:
        return all(
            (
                self.service_healthy,
                self.static_ready,
                self.bgp_ready,
                self.xfrm_ready,
                self.routing_hygiene_ready,
            )
        )

    @property
    def transfer_ready(self) -> bool:
        """Return whether a non-owner can safely enter the fenced transfer chain."""

        return self.routing_hygiene_ready and (
            self.promotion_ready or self.cold_standby_ready
        )

    @property
    def transfer_blocked_reasons(self) -> tuple[str, ...]:
        if self.transfer_ready:
            return ()
        return self.blocked_reasons

    @property
    def blocked_reasons(self) -> tuple[str, ...]:
        checks = (
            (self.service_healthy, "local-service-unhealthy"),
            (self.static_ready, "static-routes-not-ready"),
            (self.bgp_ready, "bgp-not-ready"),
            (self.xfrm_ready, "xfrm-not-ready"),
            (self.routing_hygiene_ready, "routing-hygiene-not-ready"),
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
    former_attachment_exact: bool = False
    candidate_attachment_absent: bool = False

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
    route_runtime_id: str
    ownership_incarnation: int = 0
    operation_id: str = ""

    def __post_init__(self) -> None:
        if not self.route_runtime_id:
            raise ValueError("route reconciliation requires an exact route runtime identity")

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
    readiness: LocalReadiness
    cloud: CloudObservation
    guard_boot_id: str | None
    data_plane_mode: DataPlaneMode
    routes_reconciled_context: RouteReconciliationContext | None
    route_runtime_id: str = ""
    completed_effect_operation_id: str | None = None
    transfer_intent: TransferIntent | None = None
    transfer_effect_started: bool = False
    apply_owner_adoption: bool = False

    def __post_init__(self) -> None:
        if not all(
            (
                self.boot_id,
                self.cluster_id,
                self.local_node_id,
                self.peer_node_id,
            )
        ):
            raise ValueError("boot, cluster, and both node identities are required")
        if self.local_node_id == self.peer_node_id:
            raise ValueError("VM HA requires two distinct node identities")
        if not math.isfinite(self.now):
            raise ValueError("now must be finite")
        if self.peer_received_at is not None and not math.isfinite(self.peer_received_at):
            raise ValueError("peer_received_at must be finite")
        if self.transfer_effect_started and self.transfer_intent is None:
            raise ValueError("started transfer lineage requires an exact typed intent")

    @property
    def route_reconciliation_context(self) -> RouteReconciliationContext:
        return RouteReconciliationContext(
            owner_node_id=self.local_node_id,
            allocation_id=self.cloud.allocation_id,
            ownership_epoch=self.cloud.ownership_epoch,
            generation_id=self.local_generation_id,
            digests=self.local_digests,
            route_runtime_id=self.route_runtime_id,
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
    takeover_fence_required: bool = False
    repair_deadline_at: float | None = None
    repair_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind is ActionKind.REPAIR_LOCAL_DATAPLANE:
            if (
                self.repair_deadline_at is None
                or not math.isfinite(self.repair_deadline_at)
                or not self.repair_reasons
            ):
                raise ValueError("local repair action requires a deadline and failure fingerprint")
        elif self.repair_deadline_at is not None or self.repair_reasons:
            raise ValueError("non-repair action cannot carry local repair authority")


@dataclass(frozen=True)
class RepairAttempt:
    """One owner-bound repair budget; never cloud ownership authority."""

    operation_id: str
    owner_node_id: str
    allocation_id: str
    ownership_epoch: str
    ownership_incarnation: int
    generation_id: str
    boot_id: str
    failure_fingerprint: tuple[str, ...]
    started_at: float
    deadline_at: float
    healthy_since: float | None = None
    healthy_observations: int = 0

    def __post_init__(self) -> None:
        if not all(
            (
                self.operation_id,
                self.owner_node_id,
                self.allocation_id,
                self.ownership_epoch,
                self.generation_id,
                self.boot_id,
                self.failure_fingerprint,
            )
        ):
            raise ValueError("repair attempt identity is incomplete")
        if self.ownership_incarnation < 0:
            raise ValueError("repair attempt ownership incarnation is invalid")
        if not (
            math.isfinite(self.started_at)
            and math.isfinite(self.deadline_at)
            and self.deadline_at > self.started_at
        ):
            raise ValueError("repair attempt deadline is invalid")
        if self.healthy_since is not None and not math.isfinite(self.healthy_since):
            raise ValueError("repair attempt healthy timestamp is invalid")
        if not 0 <= self.healthy_observations <= 2:
            raise ValueError("repair attempt healthy observation count is invalid")


@dataclass(frozen=True)
class TransferContinuity:
    """Durable proof that one exact candidate attachment advanced Compute state."""

    attach_operation_id: str
    allocation_id: str
    former_owner_node_id: str
    candidate_node_id: str
    generation_id: str
    digests: DigestSet
    ownership_incarnation: int
    pre_attach_revision: str
    post_attach_revision: str | None = None
    ownership_confirmed: bool = False

    def __post_init__(self) -> None:
        if not all(
            (
                self.attach_operation_id,
                self.allocation_id,
                self.former_owner_node_id,
                self.candidate_node_id,
                self.generation_id,
                self.pre_attach_revision,
            )
        ):
            raise ValueError("transfer continuity identity is incomplete")
        if self.former_owner_node_id == self.candidate_node_id:
            raise ValueError("transfer continuity requires distinct nodes")
        if self.ownership_incarnation < 0:
            raise ValueError("transfer continuity ownership incarnation is invalid")
        if self.post_attach_revision is not None:
            if not (
                self.pre_attach_revision.isascii()
                and self.post_attach_revision.isascii()
                and self.pre_attach_revision.isdecimal()
                and self.post_attach_revision.isdecimal()
                and int(self.pre_attach_revision) > 0
                and int(self.post_attach_revision) > int(self.pre_attach_revision)
            ):
                raise ValueError("transfer continuity Compute revision did not advance")
        if self.ownership_confirmed and self.post_attach_revision is None:
            raise ValueError("confirmed transfer continuity has no post-attach revision")


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
    transfer_continuity: TransferContinuity | None = None
    repair_attempt: RepairAttempt | None = None

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
        continuity = self.transfer_continuity
        if (
            continuity is not None
            and continuity.ownership_incarnation != self.ownership_incarnation
        ):
            raise ValueError("transfer continuity ownership incarnation changed")
        repair = self.repair_attempt
        if repair is not None and repair.ownership_incarnation != self.ownership_incarnation:
            raise ValueError("repair attempt ownership incarnation changed")
        action = self.pending_action
        if action is not None and action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE:
            if repair is None:
                raise ValueError("pending repair action lost its durable attempt")
            if not (
                repair.operation_id == action.operation_id
                and repair.owner_node_id == action.target_node_id
                and repair.allocation_id == action.allocation_id
                and repair.ownership_epoch == action.ownership_epoch
                and repair.ownership_incarnation == action.ownership_incarnation
                and repair.generation_id == action.generation_id
                and repair.boot_id == action.boot_id
                and repair.deadline_at == action.repair_deadline_at
                and repair.failure_fingerprint == action.repair_reasons
            ):
                raise ValueError("pending repair action does not match its durable attempt")


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

        if not snapshot.route_runtime_id:
            return self._result(
                HAState.BLOCKED,
                ("route-runtime-identity-missing",),
                snapshot,
                replace(checkpoint, state=HAState.BLOCKED, pending_action=None),
            )

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
                    or pending.kind
                    in {
                        ActionKind.ENABLE_ACTIVE,
                        ActionKind.REPAIR_LOCAL_DATAPLANE,
                    }
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
                continuity = checkpoint.transfer_continuity
                if pending.kind is ActionKind.ATTACH_CANDIDATE and postcondition_met:
                    if continuity is None:
                        raise ValueError("candidate attach lost durable transfer continuity")
                    continuity = replace(
                        continuity,
                        former_owner_node_id=snapshot.peer_node_id,
                        candidate_node_id=snapshot.local_node_id,
                        post_attach_revision=snapshot.cloud.ownership_epoch,
                    )
                elif pending.kind is ActionKind.CONFIRM_CANDIDATE_OWNERSHIP and postcondition_met:
                    if continuity is None or continuity.post_attach_revision is None:
                        raise ValueError("candidate confirmation lost attachment continuity")
                    continuity = replace(continuity, ownership_confirmed=True)
                elif pending.kind is ActionKind.DETACH_CANDIDATE_FOR_REPROOF and postcondition_met:
                    continuity = None
                checkpoint = replace(checkpoint, pending_action=None)
                checkpoint = replace(checkpoint, transfer_continuity=continuity)
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
        if local_owns and snapshot.apply_owner_adoption:
            checkpoint = replace(
                checkpoint,
                established_ownership_context=snapshot.ownership_context,
                ownership_continuity_invalidated=False,
                transfer_continuity=None,
                suspect_since=None,
            )
        continuity = checkpoint.transfer_continuity
        if (
            local_owns
            and self._continuity_matches(continuity, snapshot, require_post=True)
            and continuity is not None
            and not continuity.ownership_confirmed
        ):
            return self._action(
                HAState.PROMOTING,
                ("candidate-ownership-re-read-required",),
                snapshot,
                checkpoint,
                ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
            )
        local_ownership_safe = self._local_ownership_safe(snapshot, checkpoint)
        if local_owns and not local_ownership_safe:
            if snapshot.data_plane_mode is DataPlaneMode.ACTIVE:
                return self._action(
                    HAState.BLOCKED,
                    ("local-ownership-lacks-establishment-proof",),
                    snapshot,
                    checkpoint,
                    ActionKind.DISABLE_ACTIVE,
                )
            if not self._transfer_in_progress(snapshot, checkpoint):
                return self._result(
                    HAState.BLOCKED,
                    ("local-ownership-lacks-establishment-proof",),
                    snapshot,
                    replace(checkpoint, state=HAState.BLOCKED),
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
        if local_owns and local_ownership_safe:
            return self._owner_decision(snapshot, checkpoint, parity_reasons)

        if (
            snapshot.configured_role is ConfiguredRole.PASSIVE
            and snapshot.transfer_intent is TransferIntent.PLANNED_FAILBACK
        ):
            return self._result(
                HAState.BLOCKED,
                ("manual-failback-invalid-for-passive-role",),
                snapshot,
                replace(checkpoint, state=HAState.BLOCKED, suspect_since=None),
            )
        if (
            snapshot.configured_role is ConfiguredRole.ACTIVE
            and snapshot.transfer_intent
            in {TransferIntent.PLANNED_FAILOVER, TransferIntent.AUTOMATIC_FAILOVER}
        ):
            return self._result(
                HAState.BLOCKED,
                ("manual-failover-invalid-for-active-role",),
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
        if not snapshot.readiness.transfer_ready:
            return self._result(
                HAState.BLOCKED,
                snapshot.readiness.transfer_blocked_reasons,
                snapshot,
                replace(checkpoint, state=HAState.BLOCKED, suspect_since=None),
            )

        manual_failback_required = snapshot.configured_role is ConfiguredRole.ACTIVE
        if manual_failback_required and snapshot.transfer_intent is not TransferIntent.PLANNED_FAILBACK:
            return self._result(
                HAState.NORMAL,
                ("manual-failback-required",),
                snapshot,
                replace(checkpoint, state=HAState.NORMAL, suspect_since=None),
            )

        if not self._planned_transfer_requested(snapshot) and self._peer_fresh(snapshot):
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
        repair = checkpoint.repair_attempt
        if repair is not None and not self._repair_attempt_matches(repair, snapshot, checkpoint):
            checkpoint = replace(checkpoint, repair_attempt=None)
            repair = None

        if not parity_reasons and routes_current:
            if snapshot.readiness.promotion_ready and not snapshot.readiness.path_degraded:
                checkpoint, waiting_for_health = self._record_repair_health(
                    snapshot, checkpoint
                )
                repair = checkpoint.repair_attempt
                if waiting_for_health:
                    return self._result(
                        HAState.REPAIRING,
                        ("repair-health-verification-active",),
                        snapshot,
                        replace(checkpoint, state=HAState.REPAIRING),
                    )
            elif snapshot.readiness.promotion_ready and snapshot.readiness.path_degraded:
                checkpoint = self._reset_repair_health(checkpoint)
            elif repair is not None:
                return self._existing_repair_decision(snapshot, checkpoint, repair)
            elif snapshot.data_plane_mode is DataPlaneMode.ACTIVE:
                return self._repair_action(snapshot, checkpoint)

        repair = checkpoint.repair_attempt
        if repair is not None and snapshot.data_plane_mode is not DataPlaneMode.ACTIVE:
            return self._result(
                HAState.REPAIR_EXHAUSTED,
                ("local-repair-exhausted-forwarding-fenced",),
                snapshot,
                replace(checkpoint, state=HAState.REPAIR_EXHAUSTED),
            )

        if snapshot.data_plane_mode is DataPlaneMode.ACTIVE:
            if not routes_current:
                return self._action(
                    HAState.BLOCKED,
                    ("active-route-reconciliation-context-stale",),
                    snapshot,
                    checkpoint,
                    ActionKind.DISABLE_ACTIVE,
                )
            if reasons:
                state = HAState.DEGRADED
            elif snapshot.readiness.path_degraded:
                state = HAState.DEGRADED_PATH
                reasons = snapshot.readiness.degraded_reasons or (
                    "redundant-path-degraded",
                )
            else:
                state = HAState.ACTIVE
            return self._result(
                state,
                reasons or ("authoritative-owner-active",),
                snapshot,
                replace(
                    checkpoint,
                    state=state,
                    suspect_since=None,
                    established_ownership_context=snapshot.ownership_context,
                    transfer_continuity=None,
                ),
            )

        # Even the authoritative owner must first enter the non-forwarding
        # passive mode.  That transition authorizes only node-local tunnel
        # materialization; promotion readiness and owner-only effects are
        # evaluated on a later observation.
        if snapshot.data_plane_mode is not DataPlaneMode.PASSIVE:
            return self._action(
                HAState.NORMAL,
                ("owner-must-materialize-passive-dataplane",),
                snapshot,
                checkpoint,
                ActionKind.ENTER_PASSIVE,
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

        if snapshot.readiness.candidate_preparation_required:
            return self._action(
                HAState.PROMOTING,
                ("candidate-dataplane-requires-owner-only-preparation",),
                snapshot,
                checkpoint,
                ActionKind.PREPARE_CANDIDATE_DATAPLANE,
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

    def _repair_action(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> ControllerResult:
        reasons = tuple(sorted(snapshot.readiness.blocked_reasons))
        deadline = snapshot.now + _REPAIR_BUDGET_SECONDS
        sequence = checkpoint.sequence + 1
        operation_id = (
            f"{snapshot.boot_id}:{sequence}:"
            f"{ActionKind.REPAIR_LOCAL_DATAPLANE.value}:{snapshot.local_node_id}"
        )
        attempt = RepairAttempt(
            operation_id=operation_id,
            owner_node_id=snapshot.local_node_id,
            allocation_id=snapshot.cloud.allocation_id,
            ownership_epoch=snapshot.cloud.ownership_epoch,
            ownership_incarnation=checkpoint.ownership_incarnation,
            generation_id=snapshot.local_generation_id,
            boot_id=snapshot.boot_id,
            failure_fingerprint=reasons,
            started_at=snapshot.now,
            deadline_at=deadline,
        )
        action = ControllerAction(
            kind=ActionKind.REPAIR_LOCAL_DATAPLANE,
            operation_id=operation_id,
            boot_id=snapshot.boot_id,
            target_node_id=snapshot.local_node_id,
            allocation_id=snapshot.cloud.allocation_id,
            ownership_epoch=snapshot.cloud.ownership_epoch,
            generation_id=snapshot.local_generation_id,
            digests=snapshot.local_digests,
            ownership_incarnation=checkpoint.ownership_incarnation,
            repair_deadline_at=deadline,
            repair_reasons=reasons,
        )
        next_checkpoint = replace(
            checkpoint,
            sequence=sequence,
            state=HAState.REPAIRING,
            pending_action=action,
            repair_attempt=attempt,
        )
        return self._result(
            HAState.REPAIRING,
            ("owner-local-repair-started", *reasons),
            snapshot,
            next_checkpoint,
            action,
        )

    def _existing_repair_decision(
        self,
        snapshot: ControllerSnapshot,
        checkpoint: ControllerCheckpoint,
        repair: RepairAttempt,
    ) -> ControllerResult:
        checkpoint = self._reset_repair_health(checkpoint)
        repair = checkpoint.repair_attempt or repair
        fence_at = repair.deadline_at - _REPAIR_FENCE_RESERVE_SECONDS
        if snapshot.data_plane_mode is not DataPlaneMode.ACTIVE:
            return self._result(
                HAState.REPAIR_EXHAUSTED,
                ("local-repair-exhausted-forwarding-fenced",),
                snapshot,
                replace(checkpoint, state=HAState.REPAIR_EXHAUSTED),
            )
        if snapshot.now < fence_at:
            return self._result(
                HAState.REPAIRING,
                ("local-repair-verification-active",),
                snapshot,
                replace(checkpoint, state=HAState.REPAIRING),
            )
        return self._action(
            HAState.REPAIR_EXHAUSTED,
            ("local-repair-budget-exhausted",),
            snapshot,
            checkpoint,
            ActionKind.DISABLE_ACTIVE,
        )

    def _record_repair_health(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> tuple[ControllerCheckpoint, bool]:
        repair = checkpoint.repair_attempt
        if repair is None:
            return checkpoint, False
        healthy_since = repair.healthy_since
        observations = repair.healthy_observations
        if healthy_since is None:
            healthy_since = snapshot.now
            observations = 1
        else:
            observations = min(2, observations + 1)
        repair = replace(
            repair,
            healthy_since=healthy_since,
            healthy_observations=observations,
        )
        if (
            observations >= 2
            and snapshot.now - healthy_since >= _REPAIR_HEALTHY_RESET_SECONDS
        ):
            return replace(checkpoint, repair_attempt=None), False
        return replace(checkpoint, repair_attempt=repair), observations < 2

    @staticmethod
    def _reset_repair_health(checkpoint: ControllerCheckpoint) -> ControllerCheckpoint:
        repair = checkpoint.repair_attempt
        if repair is None or (
            repair.healthy_since is None and repair.healthy_observations == 0
        ):
            return checkpoint
        return replace(
            checkpoint,
            repair_attempt=replace(
                repair,
                healthy_since=None,
                healthy_observations=0,
            ),
        )

    @staticmethod
    def _repair_attempt_matches(
        repair: RepairAttempt,
        snapshot: ControllerSnapshot,
        checkpoint: ControllerCheckpoint,
    ) -> bool:
        return bool(
            repair.owner_node_id == snapshot.local_node_id
            and repair.allocation_id == snapshot.cloud.allocation_id
            and repair.ownership_epoch == snapshot.cloud.ownership_epoch
            and repair.ownership_incarnation == checkpoint.ownership_incarnation
            and repair.generation_id == snapshot.local_generation_id
            and repair.boot_id == snapshot.boot_id
        )

    def _transfer_decision(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> ControllerResult:
        cloud = snapshot.cloud
        suspect_since = checkpoint.suspect_since
        if not self._planned_transfer_requested(snapshot):
            if suspect_since is None:
                return self._result(
                    HAState.SUSPECT,
                    (
                        "peer-heartbeat-unhealthy"
                        if self._peer_recent(snapshot)
                        else "peer-heartbeat-stale",
                    ),
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
        continuity = checkpoint.transfer_continuity
        if not self._continuity_matches(continuity, snapshot, require_post=True):
            return self._action(
                HAState.OWNERSHIP_TRANSFER,
                ("candidate-attachment-requires-reproof",),
                snapshot,
                replace(checkpoint, transfer_continuity=None),
                ActionKind.DETACH_CANDIDATE_FOR_REPROOF,
            )
        assert continuity is not None
        if not continuity.ownership_confirmed:
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
            and heartbeat.route_ready
            and heartbeat.promotion_ready
        )

    def _peer_recent(self, snapshot: ControllerSnapshot) -> bool:
        received_at = snapshot.peer_received_at
        return bool(
            snapshot.peer_heartbeat is not None
            and received_at is not None
            and 0 <= snapshot.now - received_at < self.peer_timeout_seconds
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
            takeover_fence_required=(
                kind
                in {
                    ActionKind.PREPARE_CANDIDATE_DATAPLANE,
                    ActionKind.RECONCILE_ROUTES,
                }
                and self._transfer_in_progress(snapshot, checkpoint)
            ),
        )
        next_checkpoint = replace(
            checkpoint,
            sequence=sequence,
            state=state,
            pending_action=action,
        )
        if kind is ActionKind.ATTACH_CANDIDATE:
            next_checkpoint = replace(
                next_checkpoint,
                transfer_continuity=TransferContinuity(
                    attach_operation_id=action.operation_id,
                    allocation_id=action.allocation_id,
                    former_owner_node_id=snapshot.cloud.former_owner_node_id,
                    candidate_node_id=snapshot.local_node_id,
                    generation_id=action.generation_id,
                    digests=action.digests,
                    ownership_incarnation=action.ownership_incarnation,
                    pre_attach_revision=action.ownership_epoch,
                ),
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
            ActionKind.REPAIR_LOCAL_DATAPLANE: (
                snapshot.completed_effect_operation_id == action.operation_id
            ),
            ActionKind.STOP_FORMER_OWNER: cloud.former_owner_compute_state is ComputeState.STOPPED,
            ActionKind.DETACH_FORMER_ATTACHMENT: cloud.former_attachment_absent,
            ActionKind.DETACH_CANDIDATE_FOR_REPROOF: bool(
                cloud.former_owner_compute_state is ComputeState.STOPPED
                and cloud.former_attachment_absent
                and not cloud.candidate_attachment_exact
                and cloud.observed_owner_node_id is None
            ),
            ActionKind.ATTACH_CANDIDATE: cloud.candidate_attachment_exact,
            ActionKind.CONFIRM_CANDIDATE_OWNERSHIP: cloud.transfer_complete(snapshot.local_node_id),
            ActionKind.PREPARE_CANDIDATE_DATAPLANE: bool(
                snapshot.readiness.promotion_ready
                and not snapshot.readiness.candidate_preparation_required
            ),
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
        if kind is ActionKind.REPAIR_LOCAL_DATAPLANE:
            repair = checkpoint.repair_attempt
            return bool(
                repair is not None
                and repair.operation_id == action.operation_id
                and self._repair_attempt_matches(repair, snapshot, checkpoint)
                and cloud.local_attachment_exact(snapshot.local_node_id)
                and snapshot.guard_boot_id == snapshot.boot_id
                and snapshot.data_plane_mode is DataPlaneMode.ACTIVE
                and self._routes_current(snapshot, checkpoint)
            )
        if kind is ActionKind.PREPARE_CANDIDATE_DATAPLANE:
            return bool(
                cloud.authoritative
                and self._local_ownership_safe(snapshot, checkpoint)
                and snapshot.data_plane_mode is DataPlaneMode.PASSIVE
                and snapshot.readiness.candidate_preparation_required
                and (
                    not action.takeover_fence_required
                    or (
                        cloud.transfer_complete(snapshot.local_node_id)
                        and self._transfer_intent_valid(snapshot, checkpoint)
                    )
                )
            )
        if not cloud.authoritative or not self._promotion_gates_clear(snapshot):
            return False
        if kind in _PASSIVE_REPLAY_ACTIONS and (
            snapshot.data_plane_mode is not DataPlaneMode.PASSIVE
        ):
            return False
        if kind in {
            ActionKind.STOP_FORMER_OWNER,
            ActionKind.DETACH_FORMER_ATTACHMENT,
            ActionKind.DETACH_CANDIDATE_FOR_REPROOF,
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
        if kind is ActionKind.DETACH_CANDIDATE_FOR_REPROOF:
            return bool(
                cloud.former_owner_compute_state is ComputeState.STOPPED
                and cloud.former_attachment_absent
                and cloud.candidate_attachment_exact
                and cloud.observed_owner_node_id == snapshot.local_node_id
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
        continuity = checkpoint.transfer_continuity
        if continuity is None or continuity.attach_operation_id != action.operation_id:
            return False
        pre_revision = continuity.pre_attach_revision
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
            and cloud.observed_owner_node_id == snapshot.local_node_id
        )

    @staticmethod
    def _continuity_matches(
        continuity: TransferContinuity | None,
        snapshot: ControllerSnapshot,
        *,
        require_post: bool,
    ) -> bool:
        if continuity is None:
            return False
        return bool(
            (not require_post or continuity.post_attach_revision == snapshot.cloud.ownership_epoch)
            and continuity.allocation_id == snapshot.cloud.allocation_id
            and continuity.former_owner_node_id == snapshot.peer_node_id
            and continuity.candidate_node_id == snapshot.local_node_id
            and continuity.generation_id == snapshot.local_generation_id
            and continuity.digests == snapshot.local_digests
        )

    def _promotion_gates_clear(self, snapshot: ControllerSnapshot) -> bool:
        return not (
            snapshot.apply_locked
            or snapshot.emergency_active_only
            or self._parity_reasons(snapshot)
            or not snapshot.readiness.transfer_ready
        )

    def _transfer_intent_valid(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> bool:
        intent = snapshot.transfer_intent
        if intent is TransferIntent.PLANNED_FAILBACK:
            return snapshot.configured_role is ConfiguredRole.ACTIVE
        if intent is TransferIntent.PLANNED_FAILOVER:
            return snapshot.configured_role is ConfiguredRole.PASSIVE
        if intent is TransferIntent.AUTOMATIC_FAILOVER:
            if snapshot.configured_role is not ConfiguredRole.PASSIVE:
                return False
            if snapshot.transfer_effect_started:
                return True
        elif intent is not None:
            return False
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
        return bool(
            snapshot.transfer_intent is not None or checkpoint.suspect_since is not None
        )

    @staticmethod
    def _planned_transfer_requested(snapshot: ControllerSnapshot) -> bool:
        return snapshot.transfer_intent in {
            TransferIntent.PLANNED_FAILBACK,
            TransferIntent.PLANNED_FAILOVER,
        }

    def _local_ownership_safe(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> bool:
        cloud = snapshot.cloud
        if not cloud.local_attachment_exact(snapshot.local_node_id):
            return False
        if self._transfer_in_progress(snapshot, checkpoint):
            if not cloud.transfer_complete(snapshot.local_node_id):
                return False
            continuity = checkpoint.transfer_continuity
            return bool(
                self._continuity_matches(continuity, snapshot, require_post=True)
                and continuity is not None
                and continuity.ownership_confirmed
            )
        if snapshot.configured_role is ConfiguredRole.ACTIVE:
            # A stable configured-active baseline may be adopted after a fresh
            # exact cloud read, but invalidated route evidence is still forced
            # through owner-gated reconciliation before forwarding.
            return True
        return bool(
            not checkpoint.ownership_continuity_invalidated
            and checkpoint.established_ownership_context == snapshot.ownership_context
        )

    def _invalidate_ownership_if_lost(
        self, snapshot: ControllerSnapshot, checkpoint: ControllerCheckpoint
    ) -> ControllerCheckpoint:
        """Persist loss of exact ownership independently of caller epochs."""

        cloud = snapshot.cloud
        if not cloud.authoritative or cloud.local_attachment_exact(snapshot.local_node_id):
            return checkpoint
        continuity = checkpoint.transfer_continuity
        if (
            cloud.observed_owner_node_id == snapshot.local_node_id
            and cloud.candidate_attachment_exact
            and cloud.former_attachment_absent
            and self._continuity_matches(continuity, snapshot, require_post=True)
            and continuity is not None
            and not continuity.ownership_confirmed
        ):
            # The attach completed at the exact expected Compute revision, but
            # the independent ownership re-read has not completed yet.  Keep
            # the durable attach proof while continuing to treat the node as a
            # non-owner until confirmation succeeds.
            return checkpoint
        return replace(
            checkpoint,
            established_ownership_context=None,
            ownership_continuity_invalidated=True,
            transfer_continuity=None,
            repair_attempt=None,
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
                and state in {HAState.ACTIVE, HAState.DEGRADED, HAState.DEGRADED_PATH}
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
