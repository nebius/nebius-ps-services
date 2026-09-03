from __future__ import annotations

from dataclasses import replace

import pytest

from nebius_vpngw.agent.vm_ha import DigestSet, PeerHeartbeat
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ConfiguredRole,
    ControllerAction,
    ControllerCheckpoint,
    ControllerSnapshot,
    DataPlaneMode,
    HAState,
    LocalReadiness,
    RecoverableController,
    RepairAttempt,
    RouteReconciliationContext,
    TransferContinuity,
    TransferIntent,
    VMHAController,
)

DIGEST = "a" * 64
DIGESTS = DigestSet(DIGEST, "b" * 64, "c" * 64)


def _peer(
    *,
    node_id: str = "node-a",
    generation_id: str = DIGEST,
    configured_role: str = "active",
) -> PeerHeartbeat:
    digests = DIGESTS if generation_id == DIGEST else replace(DIGESTS, configuration=generation_id)
    return PeerHeartbeat(
        cluster_id="cluster",
        node_id=node_id,
        boot_id="peer-boot",
        sequence=1,
        sent_at="2026-08-12T00:00:00Z",
        configured_role=configured_role,
        observed_owner_id=node_id,
        generation_id=generation_id,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=digests,
        service_healthy=True,
        route_ready=True,
        promotion_ready=True,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest="e" * 64,
    )


def _cloud(
    *,
    owner: str | None = "node-a",
    state: ComputeState = ComputeState.RUNNING,
    absent: bool = False,
    attached: bool = False,
    confirmed: bool = False,
    authoritative: bool = True,
    allocation_id: str = "allocation",
    ownership_epoch: str = "ownership-epoch-1",
) -> CloudObservation:
    return CloudObservation(
        authoritative=authoritative,
        allocation_id=allocation_id,
        observed_owner_node_id=owner,
        former_owner_node_id="node-a",
        former_owner_compute_state=state,
        former_attachment_absent=absent,
        candidate_attachment_exact=attached,
        ownership_re_read_exact=confirmed,
        ownership_epoch=ownership_epoch,
    )


def _owned_cloud() -> CloudObservation:
    return _cloud(
        owner="node-b",
        state=ComputeState.STOPPED,
        absent=True,
        attached=True,
        confirmed=True,
        ownership_epoch="42",
    )


def _snapshot(**changes: object) -> ControllerSnapshot:
    snapshot = ControllerSnapshot(
        now=100.0,
        boot_id="local-boot",
        cluster_id="cluster",
        local_node_id="node-b",
        peer_node_id="node-a",
        configured_role=ConfiguredRole.PASSIVE,
        local_generation_id=DIGEST,
        local_digests=DIGESTS,
        peer_heartbeat=_peer(),
        peer_received_at=99.0,
        apply_locked=False,
        emergency_active_only=False,
        readiness=LocalReadiness(True, True, True, True),
        cloud=_cloud(),
        guard_boot_id="local-boot",
        data_plane_mode=DataPlaneMode.PASSIVE,
        routes_reconciled_context=None,
        route_runtime_id="route-runtime-a",
    )
    return replace(snapshot, **changes)


def _reconciled(
    snapshot: ControllerSnapshot,
    *,
    operation_id: str = "completed-route-effect",
    ownership_incarnation: int | None = None,
) -> RouteReconciliationContext:
    context = snapshot.route_reconciliation_context
    if ownership_incarnation is not None:
        context = replace(context, ownership_incarnation=ownership_incarnation)
    return replace(context, operation_id=operation_id)


def _proven_transfer_checkpoint(
    snapshot: ControllerSnapshot,
    *,
    state: HAState = HAState.PROMOTING,
    ownership_incarnation: int = 0,
    ownership_confirmed: bool = True,
) -> ControllerCheckpoint:
    post_revision = snapshot.cloud.ownership_epoch
    assert post_revision.isdecimal() and int(post_revision) > 1
    return ControllerCheckpoint(
        state=state,
        suspect_since=90.0,
        ownership_incarnation=ownership_incarnation,
        transfer_continuity=TransferContinuity(
            attach_operation_id="durable-attach-operation",
            allocation_id=snapshot.cloud.allocation_id,
            former_owner_node_id=snapshot.peer_node_id,
            candidate_node_id=snapshot.local_node_id,
            generation_id=snapshot.local_generation_id,
            digests=snapshot.local_digests,
            ownership_incarnation=ownership_incarnation,
            pre_attach_revision=str(int(post_revision) - 1),
            post_attach_revision=post_revision,
            ownership_confirmed=ownership_confirmed,
        ),
    )


def test_route_target_runtime_change_invalidates_reconciliation(policy: VMHAController) -> None:
    snapshot = _snapshot(cloud=_owned_cloud(), data_plane_mode=DataPlaneMode.PASSIVE)
    changed = replace(
        snapshot,
        route_runtime_id="route-runtime-b",
        routes_reconciled_context=_reconciled(snapshot),
    )
    result = policy.decide(changed, _proven_transfer_checkpoint(changed))
    assert result.action is not None
    assert result.action.kind is ActionKind.RECONCILE_ROUTES


@pytest.fixture
def policy() -> VMHAController:
    return VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)


def test_cold_start_always_installs_guard_before_any_other_action(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        guard_boot_id="old-boot",
        data_plane_mode=DataPlaneMode.ACTIVE,
        cloud=_owned_cloud(),
    )

    decision = policy.decide(snapshot, ControllerCheckpoint())

    assert decision.state is HAState.BLOCKED
    assert decision.action is not None
    assert decision.action.kind is ActionKind.INSTALL_COLD_START_GUARD
    assert not decision.forwarding_enabled


def test_non_owner_enters_passive_before_suspicion(policy: VMHAController) -> None:
    decision = policy.decide(
        _snapshot(data_plane_mode=DataPlaneMode.BLOCKED), ControllerCheckpoint()
    )
    assert decision.action is not None
    assert decision.action.kind is ActionKind.ENTER_PASSIVE


def test_initial_owner_enters_passive_before_promotion_readiness(
    policy: VMHAController,
) -> None:
    decision = policy.decide(
        _snapshot(
            configured_role=ConfiguredRole.ACTIVE,
            cloud=_owned_cloud(),
            data_plane_mode=DataPlaneMode.BLOCKED,
            readiness=LocalReadiness(False, False, False, False),
        ),
        ControllerCheckpoint(),
    )

    assert decision.action is not None
    assert decision.action.kind is ActionKind.ENTER_PASSIVE
    assert decision.reasons == ("owner-must-materialize-passive-dataplane",)
    assert not decision.forwarding_enabled


def test_static_cold_standby_can_enter_only_the_fenced_transfer_chain(
    policy: VMHAController,
) -> None:
    cold = LocalReadiness(
        True,
        False,
        True,
        False,
        candidate_preparation_required=True,
        cold_standby_ready=True,
    )
    decision = policy.decide(
        _snapshot(
            readiness=cold,
            transfer_intent=TransferIntent.PLANNED_FAILOVER,
        ),
        ControllerCheckpoint(),
    )

    assert decision.action is not None
    assert decision.action.kind is ActionKind.STOP_FORMER_OWNER
    assert not decision.forwarding_enabled


def test_static_candidate_prepares_tunnel_only_after_exact_transfer_confirmation(
    policy: VMHAController,
) -> None:
    cold = LocalReadiness(
        True,
        False,
        True,
        False,
        candidate_preparation_required=True,
        cold_standby_ready=True,
    )
    snapshot = _snapshot(
        cloud=_owned_cloud(),
        readiness=cold,
        transfer_intent=TransferIntent.PLANNED_FAILOVER,
    )
    checkpoint = _proven_transfer_checkpoint(snapshot)

    prepare = policy.decide(snapshot, checkpoint)

    assert prepare.action is not None
    assert prepare.action.kind is ActionKind.PREPARE_CANDIDATE_DATAPLANE
    assert prepare.action.takeover_fence_required
    assert snapshot.cloud.former_owner_compute_state is ComputeState.STOPPED
    assert snapshot.cloud.transfer_complete(snapshot.local_node_id)

    warmed = replace(
        snapshot,
        readiness=LocalReadiness(True, True, True, True),
    )
    routes = policy.decide(warmed, prepare.checkpoint)

    assert routes.action is not None
    assert routes.action.kind is ActionKind.RECONCILE_ROUTES
    assert not routes.forwarding_enabled


def test_mtls_rotation_inhibition_retires_checkpointed_candidate_preparation(
    policy: VMHAController,
) -> None:
    cold = LocalReadiness(
        True,
        False,
        True,
        False,
        candidate_preparation_required=True,
        cold_standby_ready=True,
    )
    snapshot = _snapshot(
        cloud=_owned_cloud(),
        readiness=cold,
        transfer_intent=TransferIntent.PLANNED_FAILOVER,
    )
    prepare = policy.decide(snapshot, _proven_transfer_checkpoint(snapshot))
    assert prepare.action is not None
    assert prepare.action.kind is ActionKind.PREPARE_CANDIDATE_DATAPLANE

    inhibited = policy.decide(
        replace(snapshot, transfer_inhibited=True),
        prepare.checkpoint,
    )

    assert inhibited.action is None
    assert inhibited.reasons == ("checkpointed-action-prerequisites-changed",)
    assert inhibited.checkpoint.pending_action is None


def test_standby_replacement_inhibition_replays_exact_owner_preparation(
    policy: VMHAController,
) -> None:
    readiness = LocalReadiness(
        True,
        False,
        True,
        False,
        candidate_preparation_required=True,
        cold_standby_ready=True,
    )
    owner = _snapshot(
        cloud=_owned_cloud(),
        readiness=readiness,
        transfer_inhibited=True,
        standby_replacement_inhibited=True,
    )
    checkpoint = ControllerCheckpoint(
        state=HAState.BLOCKED,
        established_ownership_context=owner.ownership_context,
    )

    prepare = policy.decide(owner, checkpoint)
    replay = policy.decide(owner, prepare.checkpoint)

    assert prepare.action is not None
    assert prepare.action.kind is ActionKind.PREPARE_CANDIDATE_DATAPLANE
    assert replay.action == prepare.action
    assert replay.reasons == ("replaying-checkpointed-action",)


def test_standby_replacement_inhibition_replays_exact_owner_route_reconciliation(
    policy: VMHAController,
) -> None:
    owner = _snapshot(
        cloud=_owned_cloud(),
        transfer_inhibited=True,
        standby_replacement_inhibited=True,
    )
    checkpoint = ControllerCheckpoint(
        state=HAState.BLOCKED,
        established_ownership_context=owner.ownership_context,
    )

    reconcile = policy.decide(owner, checkpoint)
    replay = policy.decide(owner, reconcile.checkpoint)

    assert reconcile.action is not None
    assert reconcile.action.kind is ActionKind.RECONCILE_ROUTES
    assert replay.action == reconcile.action
    assert replay.reasons == ("replaying-checkpointed-action",)


def test_timeout_boundary_begins_bounded_suspicion(policy: VMHAController) -> None:
    first = policy.decide(
        _snapshot(peer_received_at=90.0),
        ControllerCheckpoint(),
    )
    assert first.state is HAState.SUSPECT
    assert first.action is None
    assert first.checkpoint.suspect_since == 100.0

    before = policy.decide(_snapshot(now=104.999, peer_received_at=90.0), first.checkpoint)
    assert before.state is HAState.SUSPECT
    assert before.action is None

    boundary = policy.decide(_snapshot(now=105.0, peer_received_at=90.0), first.checkpoint)
    assert boundary.action is not None
    assert boundary.action.kind is ActionKind.STOP_FORMER_OWNER


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"apply_locked": True}, "apply-lock-held"),
        ({"emergency_active_only": True}, "emergency-active-only-generation"),
        (
            {"peer_heartbeat": _peer(generation_id="d" * 64)},
            "generation-mismatch",
        ),
        (
            {"peer_heartbeat": _peer(configured_role="passive")},
            "peer-configured-role-mismatch",
        ),
        (
            {"readiness": LocalReadiness(True, True, False, True)},
            "bgp-not-ready",
        ),
    ],
)
def test_automatic_failover_blocks_on_every_explicit_gate(
    policy: VMHAController, changes: dict[str, object], reason: str
) -> None:
    decision = policy.decide(_snapshot(peer_received_at=80.0, **changes), ControllerCheckpoint())
    assert decision.state is HAState.BLOCKED
    assert decision.action is None
    assert reason in decision.reasons


def test_fencing_and_transfer_order_is_strict(policy: VMHAController) -> None:
    checkpoint = ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0)
    cases = (
        (_cloud(state=ComputeState.RUNNING), ActionKind.STOP_FORMER_OWNER),
        (
            _cloud(state=ComputeState.STOPPED),
            ActionKind.DETACH_FORMER_ATTACHMENT,
        ),
        (
            _cloud(owner=None, state=ComputeState.STOPPED, absent=True),
            ActionKind.ATTACH_CANDIDATE,
        ),
        (
            _cloud(
                owner="node-b",
                state=ComputeState.STOPPED,
                absent=True,
                attached=True,
            ),
            ActionKind.DETACH_CANDIDATE_FOR_REPROOF,
        ),
    )
    for cloud, expected in cases:
        decision = policy.decide(
            _snapshot(now=110.0, peer_received_at=80.0, cloud=cloud), checkpoint
        )
        assert decision.action is not None
        assert decision.action.kind is expected


def test_ambiguous_cloud_state_blocks_without_mutation(policy: VMHAController) -> None:
    decision = policy.decide(
        _snapshot(
            peer_received_at=80.0,
            cloud=_cloud(state=ComputeState.UNKNOWN),
        ),
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert decision.state is HAState.BLOCKED
    assert decision.action is None
    assert decision.reasons == ("former-owner-compute-state-ambiguous",)


def test_unexpected_or_inconsistent_allocation_truth_blocks_before_fencing(
    policy: VMHAController,
) -> None:
    foreign = policy.decide(
        _snapshot(peer_received_at=80.0, cloud=_cloud(owner="foreign")),
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert foreign.state is HAState.BLOCKED
    assert foreign.action is None
    assert foreign.reasons == ("unexpected-allocation-owner",)

    inconsistent = policy.decide(
        _snapshot(
            peer_received_at=80.0,
            cloud=_cloud(owner="node-a", state=ComputeState.STOPPED, absent=True),
        ),
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert inconsistent.state is HAState.BLOCKED
    assert inconsistent.action is None
    assert inconsistent.reasons == ("former-attachment-observation-inconsistent",)

    missing_allocation = policy.decide(
        _snapshot(
            peer_received_at=80.0,
            cloud=replace(_cloud(), allocation_id=""),
        ),
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert missing_allocation.state is HAState.BLOCKED
    assert missing_allocation.action is None
    assert missing_allocation.reasons == ("shared-allocation-identity-missing",)

    inexact_candidate = policy.decide(
        _snapshot(
            peer_received_at=80.0,
            cloud=_cloud(
                owner="node-b",
                state=ComputeState.STOPPED,
                absent=True,
            ),
        ),
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert inexact_candidate.state is HAState.BLOCKED
    assert inexact_candidate.action is None
    assert inexact_candidate.reasons == ("candidate-owner-observation-without-exact-attachment",)


def test_routes_then_forwarding_require_exact_candidate_ownership(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud(), peer_received_at=80.0)
    checkpoint = _proven_transfer_checkpoint(owned)
    routes = policy.decide(owned, checkpoint)
    assert routes.action is not None
    assert routes.action.kind is ActionKind.RECONCILE_ROUTES

    forwarding = policy.decide(
        replace(
            owned,
            routes_reconciled_context=_reconciled(owned, operation_id=routes.action.operation_id),
        ),
        checkpoint,
    )
    assert forwarding.action is not None
    assert forwarding.action.kind is ActionKind.ENABLE_ACTIVE

    active = policy.decide(
        replace(
            owned,
            routes_reconciled_context=_reconciled(owned),
            data_plane_mode=DataPlaneMode.ACTIVE,
        ),
        checkpoint,
    )
    assert active.state is HAState.ACTIVE
    assert active.action is None
    assert active.forwarding_enabled


def test_checkpointed_enable_completion_remains_valid(policy: VMHAController) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    routed = replace(owned, routes_reconciled_context=_reconciled(owned))
    enable = policy.decide(routed, _proven_transfer_checkpoint(routed))
    assert enable.action is not None
    assert enable.action.kind is ActionKind.ENABLE_ACTIVE

    completed = policy.decide(
        replace(routed, data_plane_mode=DataPlaneMode.ACTIVE),
        enable.checkpoint,
    )

    assert completed.state is HAState.ACTIVE
    assert completed.action is None
    assert completed.forwarding_enabled
    assert completed.checkpoint.pending_action is None


def test_exact_owner_gets_one_bounded_local_repair_before_fencing(
    policy: VMHAController,
) -> None:
    owned = _snapshot(
        now=100.0,
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
        readiness=LocalReadiness(True, True, False, True),
    )
    checkpoint = _proven_transfer_checkpoint(owned)
    owned = replace(
        owned,
        routes_reconciled_context=_reconciled(
            owned, ownership_incarnation=checkpoint.ownership_incarnation
        ),
    )

    started = policy.decide(owned, checkpoint)

    assert started.state is HAState.REPAIRING
    assert started.action is not None
    assert started.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE
    assert started.action.repair_reasons == ("bgp-not-ready",)
    assert started.action.repair_deadline_at == 105.0
    assert started.checkpoint.repair_attempt is not None
    assert started.checkpoint.repair_attempt.operation_id == started.action.operation_id

    completed = policy.decide(
        replace(
            owned,
            now=101.0,
            completed_effect_operation_id=started.action.operation_id,
        ),
        started.checkpoint,
    )
    assert completed.action is None
    assert completed.state is HAState.REPAIRING
    assert completed.checkpoint.repair_attempt == started.checkpoint.repair_attempt

    exhausted = policy.decide(replace(owned, now=104.0), completed.checkpoint)
    assert exhausted.action is not None
    assert exhausted.action.kind is ActionKind.DISABLE_ACTIVE
    assert exhausted.checkpoint.repair_attempt is not None

    fenced = policy.decide(
        replace(owned, now=104.1, data_plane_mode=DataPlaneMode.BLOCKED),
        exhausted.checkpoint,
    )
    assert fenced.action is None
    assert fenced.state is HAState.REPAIR_EXHAUSTED


def test_unhealthy_fresh_owner_heartbeat_starts_suspicion_without_skipping_stop(
    policy: VMHAController,
) -> None:
    unhealthy = replace(
        _peer(),
        route_ready=False,
        promotion_ready=False,
    )
    first = policy.decide(
        _snapshot(peer_heartbeat=unhealthy, peer_received_at=99.0),
        ControllerCheckpoint(),
    )
    assert first.state is HAState.SUSPECT
    assert first.reasons == ("peer-heartbeat-unhealthy",)

    transfer = policy.decide(
        _snapshot(now=105.0, peer_heartbeat=unhealthy, peer_received_at=104.0),
        first.checkpoint,
    )
    assert transfer.action is not None
    assert transfer.action.kind is ActionKind.STOP_FORMER_OWNER


def test_repair_success_requires_two_observations_and_keeps_attempt_consumed(
    policy: VMHAController,
) -> None:
    owned = _snapshot(
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
        readiness=LocalReadiness(True, True, False, True),
    )
    checkpoint = _proven_transfer_checkpoint(owned)
    owned = replace(
        owned,
        routes_reconciled_context=_reconciled(
            owned, ownership_incarnation=checkpoint.ownership_incarnation
        ),
    )
    started = policy.decide(owned, checkpoint)
    assert started.action is not None
    healthy = replace(
        owned,
        now=101.0,
        readiness=LocalReadiness(True, True, True, True),
        completed_effect_operation_id=started.action.operation_id,
    )

    first = policy.decide(healthy, started.checkpoint)
    assert first.state is HAState.REPAIRING
    assert first.checkpoint.repair_attempt is not None
    assert first.checkpoint.repair_attempt.healthy_observations == 1

    second = policy.decide(replace(healthy, now=101.25), first.checkpoint)
    assert second.state is HAState.ACTIVE
    assert second.checkpoint.repair_attempt is not None
    assert second.checkpoint.repair_attempt.healthy_observations == 2

    flapped = policy.decide(
        replace(
            owned,
            now=102.0,
            readiness=LocalReadiness(False, True, False, True),
        ),
        second.checkpoint,
    )
    assert flapped.checkpoint.repair_attempt is not None
    assert flapped.checkpoint.repair_attempt.deadline_at == 105.0
    assert flapped.checkpoint.repair_attempt.failure_fingerprint == ("bgp-not-ready",)
    assert flapped.checkpoint.repair_attempt.healthy_observations == 0
    assert flapped.checkpoint.repair_attempt.healthy_since is None

    reset = policy.decide(replace(healthy, now=161.0), second.checkpoint)
    assert reset.state is HAState.ACTIVE
    assert reset.checkpoint.repair_attempt is None


def test_redundant_path_degradation_keeps_exact_owner_forwarding_without_repair(
    policy: VMHAController,
) -> None:
    owned = _snapshot(
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
        readiness=LocalReadiness(
            True,
            True,
            True,
            True,
            path_degraded=True,
            degraded_reasons=("redundant-bgp-session-unavailable",),
        ),
    )
    checkpoint = _proven_transfer_checkpoint(owned)
    owned = replace(
        owned,
        routes_reconciled_context=_reconciled(
            owned, ownership_incarnation=checkpoint.ownership_incarnation
        ),
    )

    result = policy.decide(owned, checkpoint)

    assert result.state is HAState.DEGRADED_PATH
    assert result.action is None
    assert result.forwarding_enabled
    assert result.checkpoint.repair_attempt is None


def test_allocation_replacement_invalidates_route_reconciliation_before_forwarding(
    policy: VMHAController,
) -> None:
    owned = _snapshot(
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
        peer_received_at=80.0,
    )
    owned = replace(owned, routes_reconciled_context=_reconciled(owned))
    replacement = replace(
        owned,
        cloud=replace(
            owned.cloud,
            allocation_id="replacement-allocation",
            ownership_epoch="ownership-epoch-2",
        ),
    )

    disable = policy.decide(replacement, _proven_transfer_checkpoint(owned))
    assert disable.action is not None
    assert disable.action.kind is ActionKind.DISABLE_ACTIVE
    assert not disable.forwarding_enabled

    disabled = replace(replacement, data_plane_mode=DataPlaneMode.PASSIVE)
    reproof = policy.decide(disabled, disable.checkpoint)
    assert reproof.action is not None
    assert reproof.action.kind is ActionKind.DETACH_CANDIDATE_FOR_REPROOF
    assert not reproof.forwarding_enabled


def test_ownership_loss_and_reacquisition_requires_fresh_route_reconciliation(
    policy: VMHAController,
) -> None:
    owned = _snapshot(
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
        peer_received_at=80.0,
    )
    owned = replace(owned, routes_reconciled_context=_reconciled(owned))

    lost = replace(
        owned,
        cloud=_cloud(
            owner=None,
            state=ComputeState.STOPPED,
            absent=True,
        ),
    )
    disable = policy.decide(lost, _proven_transfer_checkpoint(owned))
    assert disable.action is not None
    assert disable.action.kind is ActionKind.DISABLE_ACTIVE
    assert disable.checkpoint.ownership_continuity_invalidated
    assert disable.checkpoint.ownership_incarnation == 1

    reacquired = replace(
        owned,
        data_plane_mode=DataPlaneMode.PASSIVE,
    )
    reproof = policy.decide(reacquired, disable.checkpoint)
    assert reproof.action is not None
    assert reproof.action.kind is ActionKind.DETACH_CANDIDATE_FOR_REPROOF
    assert reproof.action.ownership_incarnation == 1
    assert not reproof.forwarding_enabled


def test_route_failure_replays_without_enabling_forwarding(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    first = policy.decide(owned, _proven_transfer_checkpoint(owned))
    assert first.action is not None
    assert first.action.kind is ActionKind.RECONCILE_ROUTES

    replay = policy.decide(owned, first.checkpoint)
    assert replay.action == first.action
    assert not replay.forwarding_enabled


def test_restart_cannot_clear_loss_invalidation_from_old_route_action(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    owned = replace(owned, routes_reconciled_context=_reconciled(owned))
    checkpoint = _proven_transfer_checkpoint(
        owned,
        ownership_incarnation=1,
    )
    reconcile = policy.decide(
        owned,
        replace(
            checkpoint,
            ownership_continuity_invalidated=True,
        ),
    )
    assert reconcile.action is not None
    assert reconcile.action.kind is ActionKind.RECONCILE_ROUTES

    restarted = policy.decide(
        replace(owned, boot_id="new-boot", guard_boot_id="local-boot"),
        reconcile.checkpoint,
    )

    assert restarted.action is not None
    assert restarted.action.kind is ActionKind.INSTALL_COLD_START_GUARD
    assert restarted.checkpoint.ownership_continuity_invalidated


def test_invalidated_route_evidence_requires_fresh_reconciliation(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    stale_context = _reconciled(
        owned,
        operation_id="stale-route-effect",
        ownership_incarnation=1,
    )

    decision = policy.decide(
        replace(owned, routes_reconciled_context=stale_context),
        replace(
            _proven_transfer_checkpoint(owned, ownership_incarnation=1),
            ownership_continuity_invalidated=True,
        ),
    )

    assert decision.action is not None
    assert decision.action.kind is ActionKind.RECONCILE_ROUTES
    assert decision.action.ownership_incarnation == 1
    assert decision.action.takeover_fence_required is True
    assert decision.checkpoint.ownership_continuity_invalidated


def test_historical_incarnation_does_not_turn_same_owner_reapply_into_takeover(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        configured_role=ConfiguredRole.ACTIVE,
        peer_heartbeat=replace(
            _peer(configured_role="passive"),
            observed_owner_id="node-b",
            promotion_ready=False,
        ),
        cloud=replace(
            _owned_cloud(),
            former_owner_compute_state=ComputeState.RUNNING,
        ),
    )
    checkpoint = ControllerCheckpoint(
        ownership_incarnation=3,
        established_ownership_context=snapshot.ownership_context,
    )

    decision = policy.decide(snapshot, checkpoint)

    assert decision.action is not None
    assert decision.action.kind is ActionKind.RECONCILE_ROUTES
    assert decision.action.ownership_incarnation == 3
    assert decision.action.takeover_fence_required is False


def test_checkpoint_before_route_effect_cannot_accept_stale_matching_context(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    stale_context = _reconciled(
        owned,
        operation_id="stale-route-effect",
        ownership_incarnation=1,
    )
    snapshot = replace(owned, routes_reconciled_context=stale_context)
    checkpoint = replace(
        _proven_transfer_checkpoint(snapshot, ownership_incarnation=1),
        ownership_continuity_invalidated=True,
    )

    scheduled = policy.decide(snapshot, checkpoint)
    assert scheduled.action is not None
    assert scheduled.action.kind is ActionKind.RECONCILE_ROUTES

    crashed_before_effect = policy.decide(snapshot, scheduled.checkpoint)
    assert crashed_before_effect.action == scheduled.action
    assert crashed_before_effect.reasons == ("replaying-checkpointed-action",)
    assert crashed_before_effect.checkpoint.ownership_continuity_invalidated

    completed = policy.decide(
        replace(
            snapshot,
            routes_reconciled_context=_reconciled(
                snapshot,
                operation_id=scheduled.action.operation_id,
                ownership_incarnation=scheduled.action.ownership_incarnation,
            ),
        ),
        scheduled.checkpoint,
    )
    assert completed.action is not None
    assert completed.action.kind is ActionKind.ENABLE_ACTIVE
    assert not completed.checkpoint.ownership_continuity_invalidated


@pytest.mark.parametrize(
    "changes",
    [
        {"ownership_continuity_invalidated": True},
        {
            "ownership_continuity_invalidated": True,
            "ownership_incarnation": 1,
            "established_ownership_context": _snapshot(cloud=_owned_cloud()).ownership_context,
        },
    ],
)
def test_checkpoint_rejects_inconsistent_ownership_invalidation(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="ownership continuity invalidation"):
        ControllerCheckpoint(**changes)


def test_legacy_checkpoint_without_ownership_fields_uses_safe_defaults() -> None:
    checkpoint = ControllerCheckpoint(sequence=3, state=HAState.NORMAL)

    assert checkpoint.ownership_incarnation == 0
    assert not checkpoint.ownership_continuity_invalidated
    assert checkpoint.established_ownership_context is None


def test_checkpointed_route_effect_is_cancelled_when_ownership_epoch_changes(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    first = policy.decide(owned, _proven_transfer_checkpoint(owned))
    assert first.action is not None
    assert first.action.kind is ActionKind.RECONCILE_ROUTES

    replaced = replace(
        owned,
        cloud=replace(owned.cloud, ownership_epoch="ownership-epoch-2"),
    )
    blocked = policy.decide(replaced, first.checkpoint)
    assert blocked.state is HAState.BLOCKED
    assert blocked.action is None
    assert blocked.reasons == ("checkpointed-action-prerequisites-changed",)


def test_checkpointed_promotion_effect_revalidates_failover_gates(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    first = policy.decide(owned, _proven_transfer_checkpoint(owned))
    assert first.action is not None
    assert first.action.kind is ActionKind.RECONCILE_ROUTES

    blocked = policy.decide(replace(owned, apply_locked=True), first.checkpoint)
    assert blocked.state is HAState.BLOCKED
    assert blocked.action is None
    assert blocked.reasons == ("checkpointed-action-prerequisites-changed",)


def test_owner_generation_drift_is_degraded_but_does_not_disable_serving(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        peer_heartbeat=_peer(generation_id="d" * 64),
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
    )
    decision = policy.decide(
        replace(snapshot, routes_reconciled_context=_reconciled(snapshot)),
        _proven_transfer_checkpoint(snapshot),
    )
    assert decision.state is HAState.DEGRADED
    assert decision.action is None
    assert decision.forwarding_enabled
    assert "generation-mismatch" in decision.reasons


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"apply_locked": True}, "apply-lock-held"),
        ({"emergency_active_only": True}, "emergency-active-only-generation"),
        (
            {"peer_heartbeat": _peer(generation_id="d" * 64)},
            "generation-mismatch",
        ),
    ],
)
def test_new_owner_cannot_finish_promotion_while_failover_is_disabled(
    policy: VMHAController, changes: dict[str, object], reason: str
) -> None:
    snapshot = _snapshot(
        cloud=_owned_cloud(),
        **changes,
    )
    decision = policy.decide(
        snapshot,
        _proven_transfer_checkpoint(snapshot),
    )
    assert decision.state is HAState.BLOCKED
    assert decision.action is None
    assert not decision.forwarding_enabled
    assert reason in decision.reasons


def test_loss_of_authoritative_ownership_disables_forwarding(
    policy: VMHAController,
) -> None:
    decision = policy.decide(
        _snapshot(
            cloud=_cloud(authoritative=False),
            data_plane_mode=DataPlaneMode.ACTIVE,
        ),
        ControllerCheckpoint(),
    )
    assert decision.action is not None
    assert decision.action.kind is ActionKind.DISABLE_ACTIVE


@pytest.mark.parametrize(
    "cloud",
    [
        _cloud(owner="node-b", attached=True, confirmed=True),
        _cloud(
            owner="node-b",
            state=ComputeState.STOPPED,
            attached=True,
            confirmed=True,
        ),
    ],
)
def test_takeover_ownership_requires_stopped_and_detached_former_owner(
    policy: VMHAController, cloud: CloudObservation
) -> None:
    decision = policy.decide(
        _snapshot(
            now=110.0,
            peer_received_at=80.0,
            cloud=cloud,
            data_plane_mode=DataPlaneMode.ACTIVE,
        ),
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )

    assert decision.state is HAState.BLOCKED
    assert decision.action is not None
    assert decision.action.kind is ActionKind.DISABLE_ACTIVE
    assert not decision.forwarding_enabled


def test_established_active_owner_remains_active_while_passive_peer_is_running(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        configured_role=ConfiguredRole.ACTIVE,
        peer_heartbeat=_peer(node_id="node-a", configured_role="passive"),
        cloud=_cloud(owner="node-b", absent=True, attached=True, confirmed=True),
        data_plane_mode=DataPlaneMode.ACTIVE,
    )

    decision = policy.decide(
        replace(snapshot, routes_reconciled_context=_reconciled(snapshot)),
        ControllerCheckpoint(),
    )

    assert decision.state is HAState.ACTIVE
    assert decision.action is None
    assert decision.forwarding_enabled
    assert decision.reasons == ("authoritative-owner-active",)


@pytest.mark.parametrize("data_plane_mode", [DataPlaneMode.PASSIVE, DataPlaneMode.ACTIVE])
def test_local_owner_with_former_attachment_present_is_rejected(
    policy: VMHAController, data_plane_mode: DataPlaneMode
) -> None:
    snapshot = _snapshot(
        configured_role=ConfiguredRole.ACTIVE,
        peer_heartbeat=_peer(node_id="node-a", configured_role="passive"),
        cloud=_cloud(owner="node-b", attached=True, confirmed=True),
        data_plane_mode=data_plane_mode,
    )
    snapshot = replace(
        snapshot,
        routes_reconciled_context=_reconciled(snapshot),
    )

    decision = policy.decide(snapshot, ControllerCheckpoint())

    assert decision.state is HAState.BLOCKED
    assert not decision.forwarding_enabled
    assert decision.reasons == ("candidate-owner-observation-with-former-attachment-present",)
    assert decision.checkpoint.ownership_continuity_invalidated
    if data_plane_mode is DataPlaneMode.ACTIVE:
        assert decision.action is not None
        assert decision.action.kind is ActionKind.DISABLE_ACTIVE
    else:
        assert decision.action is None


def test_passive_owner_requires_durable_establishment_when_peer_is_running(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        cloud=_cloud(owner="node-b", absent=True, attached=True, confirmed=True),
        data_plane_mode=DataPlaneMode.ACTIVE,
    )
    snapshot = replace(
        snapshot,
        routes_reconciled_context=_reconciled(snapshot),
    )

    unproven = policy.decide(snapshot, ControllerCheckpoint())
    assert unproven.action is not None
    assert unproven.action.kind is ActionKind.DISABLE_ACTIVE
    assert not unproven.forwarding_enabled

    established = policy.decide(
        snapshot,
        ControllerCheckpoint(established_ownership_context=snapshot.ownership_context),
    )
    assert established.state is HAState.ACTIVE
    assert established.action is None
    assert established.forwarding_enabled


def test_healthy_configured_passive_owner_does_not_trigger_preference_return(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        configured_role=ConfiguredRole.ACTIVE,
        peer_heartbeat=_peer(configured_role="passive"),
    )
    decision = policy.decide(snapshot, ControllerCheckpoint())

    assert decision.state is HAState.NORMAL
    assert decision.action is None
    assert decision.reasons == ("authoritative-owner-peer-is-healthy",)

    manual = policy.decide(
        replace(snapshot, transfer_intent=TransferIntent.PLANNED_FAILBACK),
        ControllerCheckpoint(),
    )
    assert manual.action is not None
    assert manual.action.kind is ActionKind.STOP_FORMER_OWNER


def test_configured_active_survivor_automatically_takes_over_stopped_owner_after_restart(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        now=110.0,
        configured_role=ConfiguredRole.ACTIVE,
        peer_heartbeat=_peer(configured_role="passive"),
        # The last authenticated peer heartbeat remains parity evidence across
        # a controller-process restart, but never remains fresh liveness evidence.
        peer_received_at=None,
        cloud=_cloud(state=ComputeState.STOPPED),
    )

    automatic = policy.decide(
        snapshot,
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )

    assert automatic.action is not None
    assert automatic.action.kind is ActionKind.DETACH_FORMER_ATTACHMENT
    assert automatic.reasons == ("former-allocation-attachment-must-be-absent",)


def test_manual_failback_intent_is_rejected_on_configured_passive(
    policy: VMHAController,
) -> None:
    decision = policy.decide(
        _snapshot(transfer_intent=TransferIntent.PLANNED_FAILBACK),
        ControllerCheckpoint(),
    )

    assert decision.state is HAState.BLOCKED
    assert decision.action is None
    assert decision.reasons == ("manual-failback-invalid-for-passive-role",)


def test_manual_failover_intent_bypasses_only_healthy_peer_suppression(
    policy: VMHAController,
) -> None:
    decision = policy.decide(
        _snapshot(transfer_intent=TransferIntent.PLANNED_FAILOVER),
        ControllerCheckpoint(),
    )

    assert decision.action is not None
    assert decision.action.kind is ActionKind.STOP_FORMER_OWNER
    assert decision.reasons == ("former-owner-must-be-stopped",)


def test_manual_failover_intent_is_rejected_on_configured_active(
    policy: VMHAController,
) -> None:
    decision = policy.decide(
        _snapshot(
            configured_role=ConfiguredRole.ACTIVE,
            peer_heartbeat=_peer(configured_role="passive"),
            transfer_intent=TransferIntent.PLANNED_FAILOVER,
        ),
        ControllerCheckpoint(),
    )

    assert decision.state is HAState.BLOCKED
    assert decision.action is None
    assert decision.reasons == ("manual-failover-invalid-for-active-role",)


def test_sticky_automatic_failover_intent_is_role_neutral(
    policy: VMHAController,
) -> None:
    decision = policy.decide(
        _snapshot(
            configured_role=ConfiguredRole.ACTIVE,
            peer_heartbeat=_peer(configured_role="passive"),
            peer_received_at=109.0,
            transfer_intent=TransferIntent.AUTOMATIC_FAILOVER,
            transfer_effect_started=True,
        ),
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )

    assert decision.action is not None
    assert decision.action.kind is ActionKind.STOP_FORMER_OWNER
    assert decision.reasons == ("former-owner-must-be-stopped",)


def test_crash_replay_reuses_operation_id_until_postcondition(
    policy: VMHAController,
) -> None:
    checkpoint = ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0)
    snapshot = _snapshot(now=110.0, peer_received_at=80.0)
    first = policy.decide(snapshot, checkpoint)
    assert first.action is not None

    replay = policy.decide(snapshot, first.checkpoint)
    assert replay.action == first.action
    assert replay.checkpoint == first.checkpoint

    completed = policy.decide(
        replace(snapshot, cloud=_cloud(state=ComputeState.STOPPED)),
        first.checkpoint,
    )
    assert completed.action is not None
    assert completed.action.kind is ActionKind.DETACH_FORMER_ATTACHMENT
    assert completed.action.operation_id != first.action.operation_id


def test_automatic_suspicion_cancels_before_effect_but_is_sticky_after_effect(
    policy: VMHAController,
) -> None:
    first = policy.decide(
        _snapshot(now=110.0, peer_received_at=80.0),
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert first.action is not None
    assert first.action.kind is ActionKind.STOP_FORMER_OWNER
    recovered_peer = _snapshot(now=110.0, peer_received_at=109.0)

    cancelled = policy.decide(recovered_peer, first.checkpoint)
    assert cancelled.action is None
    assert cancelled.state is HAState.BLOCKED

    sticky = policy.decide(
        replace(
            recovered_peer,
            transfer_intent=TransferIntent.AUTOMATIC_FAILOVER,
            transfer_effect_started=True,
        ),
        first.checkpoint,
    )
    assert sticky.action == first.action
    assert sticky.reasons == ("replaying-checkpointed-action",)


def test_effect_started_automatic_transfer_resumes_after_apply_fencing(
    policy: VMHAController,
) -> None:
    first = policy.decide(
        _snapshot(now=110.0, peer_received_at=80.0),
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert first.action is not None
    assert first.action.kind is ActionKind.STOP_FORMER_OWNER

    locked = _snapshot(
        now=111.0,
        peer_received_at=110.0,
        apply_locked=True,
        transfer_intent=TransferIntent.AUTOMATIC_FAILOVER,
        transfer_effect_started=True,
    )
    fenced = policy.decide(locked, first.checkpoint)

    assert fenced.action is None
    assert fenced.reasons == ("checkpointed-action-prerequisites-changed",)
    assert fenced.checkpoint.pending_action is None

    resumed = policy.decide(replace(locked, apply_locked=False), fenced.checkpoint)

    assert resumed.action is not None
    assert resumed.action.kind is ActionKind.STOP_FORMER_OWNER
    assert resumed.reasons == ("former-owner-must-be-stopped",)


def test_planned_request_disappearance_cannot_cancel_started_lineage(
    policy: VMHAController,
) -> None:
    first = policy.decide(
        _snapshot(transfer_intent=TransferIntent.PLANNED_FAILOVER),
        ControllerCheckpoint(),
    )
    assert first.action is not None

    sticky = policy.decide(
        _snapshot(
            transfer_intent=TransferIntent.PLANNED_FAILOVER,
            transfer_effect_started=True,
        ),
        first.checkpoint,
    )

    assert sticky.action == first.action


def test_started_transfer_requires_exact_typed_lineage() -> None:
    with pytest.raises(ValueError, match="typed intent"):
        _snapshot(transfer_effect_started=True)


@pytest.mark.parametrize("kind", list(ActionKind))
def test_forged_pending_operation_is_rejected_before_postcondition(
    policy: VMHAController,
    kind: ActionKind,
) -> None:
    snapshot = _snapshot(
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
    )
    target = (
        snapshot.peer_node_id
        if kind in {ActionKind.STOP_FORMER_OWNER, ActionKind.DETACH_FORMER_ATTACHMENT}
        else snapshot.local_node_id
    )
    repair_kwargs = (
        {"repair_deadline_at": snapshot.now + 5.0, "repair_reasons": ("bgp-not-ready",)}
        if kind is ActionKind.REPAIR_LOCAL_DATAPLANE
        else {}
    )
    forged = ControllerAction(
        kind=kind,
        operation_id="forged-operation",
        boot_id=snapshot.boot_id,
        target_node_id=target,
        allocation_id=snapshot.cloud.allocation_id,
        ownership_epoch=snapshot.cloud.ownership_epoch,
        generation_id=snapshot.local_generation_id,
        digests=snapshot.local_digests,
        **repair_kwargs,
    )
    if kind is ActionKind.RECONCILE_ROUTES:
        snapshot = replace(
            snapshot,
            routes_reconciled_context=_reconciled(
                snapshot,
                operation_id=forged.operation_id,
            ),
        )

    repair_attempt = (
        RepairAttempt(
            operation_id=forged.operation_id,
            owner_node_id=snapshot.local_node_id,
            allocation_id=snapshot.cloud.allocation_id,
            ownership_epoch=snapshot.cloud.ownership_epoch,
            ownership_incarnation=0,
            generation_id=snapshot.local_generation_id,
            boot_id=snapshot.boot_id,
            failure_fingerprint=("bgp-not-ready",),
            started_at=snapshot.now,
            deadline_at=snapshot.now + 5.0,
        )
        if kind is ActionKind.REPAIR_LOCAL_DATAPLANE
        else None
    )
    checkpoint = ControllerCheckpoint(
        sequence=7,
        pending_action=forged,
        repair_attempt=repair_attempt,
    )
    with pytest.raises(ValueError, match="pending action does not match"):
        policy.decide(snapshot, checkpoint)


def test_pending_repair_action_must_match_its_persisted_attempt() -> None:
    snapshot = _snapshot(cloud=_owned_cloud(), data_plane_mode=DataPlaneMode.ACTIVE)
    operation_id = "local-boot:7:repair-local-dataplane:node-b"
    attempt = RepairAttempt(
        operation_id=operation_id,
        owner_node_id=snapshot.local_node_id,
        allocation_id=snapshot.cloud.allocation_id,
        ownership_epoch=snapshot.cloud.ownership_epoch,
        ownership_incarnation=0,
        generation_id=snapshot.local_generation_id,
        boot_id=snapshot.boot_id,
        failure_fingerprint=("bgp-not-ready",),
        started_at=snapshot.now,
        deadline_at=snapshot.now + 5.0,
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
        repair_deadline_at=snapshot.now + 6.0,
        repair_reasons=attempt.failure_fingerprint,
    )

    with pytest.raises(ValueError, match="does not match its durable attempt"):
        ControllerCheckpoint(
            sequence=7,
            pending_action=action,
            repair_attempt=attempt,
        )


def test_forged_pending_route_context_cannot_complete_from_matching_marker(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(cloud=_owned_cloud())
    operation_id = "local-boot:7:reconcile-routes:node-b"
    action = ControllerAction(
        kind=ActionKind.RECONCILE_ROUTES,
        operation_id=operation_id,
        boot_id=snapshot.boot_id,
        target_node_id=snapshot.local_node_id,
        allocation_id="forged-allocation",
        ownership_epoch=snapshot.cloud.ownership_epoch,
        generation_id=snapshot.local_generation_id,
        digests=snapshot.local_digests,
    )
    snapshot = replace(
        snapshot,
        routes_reconciled_context=_reconciled(snapshot, operation_id=operation_id),
    )

    decision = policy.decide(
        snapshot,
        ControllerCheckpoint(sequence=7, pending_action=action),
    )

    assert decision.action is None
    assert decision.reasons == ("checkpointed-action-prerequisites-changed",)
    assert decision.checkpoint.established_ownership_context is None


@pytest.mark.parametrize(
    ("mutation", "operation_id"),
    [
        (
            {"target_node_id": "foreign-node"},
            "local-boot:7:stop-former-owner:foreign-node",
        ),
        ({"ownership_incarnation": 2}, "local-boot:7:stop-former-owner:node-a"),
    ],
)
def test_malformed_pending_action_structure_is_rejected(
    policy: VMHAController,
    mutation: dict[str, object],
    operation_id: str,
) -> None:
    snapshot = _snapshot(now=110.0, peer_received_at=80.0)
    action = ControllerAction(
        kind=ActionKind.STOP_FORMER_OWNER,
        operation_id=operation_id,
        boot_id=snapshot.boot_id,
        target_node_id=snapshot.peer_node_id,
        allocation_id=snapshot.cloud.allocation_id,
        ownership_epoch=snapshot.cloud.ownership_epoch,
        generation_id=snapshot.local_generation_id,
        digests=snapshot.local_digests,
        ownership_incarnation=1,
    )
    action = replace(action, **mutation)
    checkpoint = ControllerCheckpoint(
        sequence=7,
        suspect_since=90.0,
        pending_action=action,
        ownership_incarnation=1,
    )

    with pytest.raises(ValueError, match="pending action does not match"):
        policy.decide(snapshot, checkpoint)


def test_pending_action_requires_positive_checkpoint_sequence(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(data_plane_mode=DataPlaneMode.BLOCKED)
    action = ControllerAction(
        kind=ActionKind.INSTALL_COLD_START_GUARD,
        operation_id="local-boot:0:install-cold-start-guard:node-b",
        boot_id=snapshot.boot_id,
        target_node_id=snapshot.local_node_id,
        allocation_id=snapshot.cloud.allocation_id,
        ownership_epoch=snapshot.cloud.ownership_epoch,
        generation_id=snapshot.local_generation_id,
        digests=snapshot.local_digests,
    )

    with pytest.raises(ValueError, match="pending action does not match"):
        policy.decide(snapshot, ControllerCheckpoint(pending_action=action))


def _local_safety_case(
    policy: VMHAController,
    kind: ActionKind,
) -> tuple[ControllerSnapshot, ControllerCheckpoint, ControllerSnapshot]:
    if kind is ActionKind.INSTALL_COLD_START_GUARD:
        initial = _snapshot(
            guard_boot_id="previous-boot",
            data_plane_mode=DataPlaneMode.BLOCKED,
            cloud=replace(_cloud(), authoritative=False),
        )
        completed = replace(
            initial,
            guard_boot_id=initial.boot_id,
            local_generation_id="d" * 64,
            peer_heartbeat=_peer(generation_id="d" * 64),
        )
    elif kind is ActionKind.ENTER_PASSIVE:
        initial = _snapshot(data_plane_mode=DataPlaneMode.BLOCKED)
        completed = replace(
            initial,
            data_plane_mode=DataPlaneMode.PASSIVE,
            cloud=replace(
                initial.cloud,
                allocation_id="replacement-allocation",
                ownership_epoch="ownership-epoch-2",
            ),
        )
    elif kind is ActionKind.DISABLE_ACTIVE:
        initial = _snapshot(
            data_plane_mode=DataPlaneMode.ACTIVE,
            cloud=replace(_cloud(), authoritative=False),
        )
        completed = replace(
            initial,
            data_plane_mode=DataPlaneMode.BLOCKED,
            cloud=replace(
                initial.cloud,
                allocation_id="replacement-allocation",
                ownership_epoch="ownership-epoch-2",
            ),
        )
    else:
        raise AssertionError(f"not a local safety action: {kind}")

    scheduled = policy.decide(initial, ControllerCheckpoint())
    assert scheduled.action is not None
    assert scheduled.action.kind is kind
    return initial, scheduled.checkpoint, completed


@pytest.mark.parametrize(
    "kind",
    [
        ActionKind.INSTALL_COLD_START_GUARD,
        ActionKind.ENTER_PASSIVE,
        ActionKind.DISABLE_ACTIVE,
    ],
)
def test_completed_local_safety_action_clears_after_unrelated_context_drift(
    policy: VMHAController,
    kind: ActionKind,
) -> None:
    _initial, pending, completed = _local_safety_case(policy, kind)

    decision = policy.decide(completed, pending)

    assert decision.checkpoint.pending_action is None
    assert decision.action is None


@pytest.mark.parametrize(
    "kind",
    [
        ActionKind.INSTALL_COLD_START_GUARD,
        ActionKind.ENTER_PASSIVE,
        ActionKind.DISABLE_ACTIVE,
    ],
)
def test_local_safety_action_replays_after_context_drift_without_exact_postcondition(
    policy: VMHAController,
    kind: ActionKind,
) -> None:
    initial, pending, completed = _local_safety_case(policy, kind)
    missing = replace(
        completed,
        guard_boot_id=(
            "previous-boot"
            if kind is ActionKind.INSTALL_COLD_START_GUARD
            else completed.guard_boot_id
        ),
        data_plane_mode=(
            DataPlaneMode.ACTIVE if kind is ActionKind.DISABLE_ACTIVE else initial.data_plane_mode
        ),
    )

    decision = policy.decide(missing, pending)

    assert decision.action == pending.pending_action
    assert decision.checkpoint == pending
    assert decision.reasons == ("replaying-checkpointed-action",)


@pytest.mark.parametrize(
    "kind",
    [
        ActionKind.STOP_FORMER_OWNER,
        ActionKind.DETACH_FORMER_ATTACHMENT,
        ActionKind.ATTACH_CANDIDATE,
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
        ActionKind.RECONCILE_ROUTES,
        ActionKind.ENABLE_ACTIVE,
    ],
)
def test_authority_action_completion_remains_bound_to_original_context(
    policy: VMHAController,
    kind: ActionKind,
) -> None:
    snapshot = _snapshot(cloud=_owned_cloud())
    target = (
        snapshot.peer_node_id
        if kind
        in {
            ActionKind.STOP_FORMER_OWNER,
            ActionKind.DETACH_FORMER_ATTACHMENT,
        }
        else snapshot.local_node_id
    )
    operation_id = f"{snapshot.boot_id}:7:{kind.value}:{target}"
    action = ControllerAction(
        kind=kind,
        operation_id=operation_id,
        boot_id=snapshot.boot_id,
        target_node_id=target,
        allocation_id=snapshot.cloud.allocation_id,
        ownership_epoch=snapshot.cloud.ownership_epoch,
        generation_id=snapshot.local_generation_id,
        digests=snapshot.local_digests,
    )
    drifted = replace(
        snapshot,
        cloud=replace(snapshot.cloud, allocation_id="replacement-allocation"),
        data_plane_mode=(
            DataPlaneMode.ACTIVE if kind is ActionKind.ENABLE_ACTIVE else DataPlaneMode.PASSIVE
        ),
    )
    if kind is ActionKind.RECONCILE_ROUTES:
        drifted = replace(
            drifted,
            routes_reconciled_context=_reconciled(
                drifted,
                operation_id=operation_id,
            ),
        )

    decision = policy.decide(
        drifted,
        ControllerCheckpoint(sequence=7, suspect_since=90.0, pending_action=action),
    )

    assert decision.action != action
    assert decision.reasons == ("checkpointed-action-prerequisites-changed",)


@pytest.mark.parametrize(
    "kind",
    [
        ActionKind.INSTALL_COLD_START_GUARD,
        ActionKind.ENTER_PASSIVE,
        ActionKind.DISABLE_ACTIVE,
    ],
)
def test_deduplicating_effect_port_observes_local_safety_pending_clear_after_drift(
    policy: VMHAController,
    kind: ActionKind,
) -> None:
    initial, _pending, completed = _local_safety_case(policy, kind)
    applied_operation_ids: set[str] = set()

    class Snapshots:
        value = initial

        def observe(self) -> ControllerSnapshot:
            return self.value

    class Checkpoints:
        value = ControllerCheckpoint()

        def load(self) -> ControllerCheckpoint:
            return self.value

        def save(self, checkpoint: ControllerCheckpoint) -> None:
            self.value = checkpoint

    snapshots = Snapshots()
    checkpoints = Checkpoints()

    class Effects:
        def apply(self, action: ControllerAction) -> None:
            if action.operation_id in applied_operation_ids:
                return
            applied_operation_ids.add(action.operation_id)
            snapshots.value = completed

    runtime = RecoverableController(
        policy=policy,
        snapshots=snapshots,
        checkpoints=checkpoints,
        effects=Effects(),
    )

    applied = runtime.step()
    assert applied.action is not None
    cleared = runtime.step()

    assert applied_operation_ids == {applied.action.operation_id}
    assert cleared.action is None
    assert checkpoints.value.pending_action is None


@pytest.mark.parametrize(
    "kind",
    [
        ActionKind.STOP_FORMER_OWNER,
        ActionKind.DETACH_FORMER_ATTACHMENT,
        ActionKind.ATTACH_CANDIDATE,
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
        ActionKind.RECONCILE_ROUTES,
        ActionKind.ENABLE_ACTIVE,
    ],
)
@pytest.mark.parametrize("guard_boot_id", [None, "previous-boot"])
def test_pending_authority_action_installs_current_boot_guard_before_completion_or_replay(
    policy: VMHAController,
    kind: ActionKind,
    guard_boot_id: str | None,
) -> None:
    snapshot = _snapshot(
        cloud=_owned_cloud(),
        guard_boot_id=guard_boot_id,
        data_plane_mode=DataPlaneMode.PASSIVE,
    )
    target = (
        snapshot.peer_node_id
        if kind in {ActionKind.STOP_FORMER_OWNER, ActionKind.DETACH_FORMER_ATTACHMENT}
        else snapshot.local_node_id
    )
    operation_id = f"{snapshot.boot_id}:7:{kind.value}:{target}"
    action = ControllerAction(
        kind=kind,
        operation_id=operation_id,
        boot_id=snapshot.boot_id,
        target_node_id=target,
        allocation_id=snapshot.cloud.allocation_id,
        ownership_epoch=snapshot.cloud.ownership_epoch,
        generation_id=snapshot.local_generation_id,
        digests=snapshot.local_digests,
    )
    if kind is ActionKind.RECONCILE_ROUTES:
        snapshot = replace(
            snapshot,
            routes_reconciled_context=_reconciled(snapshot, operation_id=operation_id),
        )

    decision = policy.decide(
        snapshot,
        ControllerCheckpoint(sequence=7, pending_action=action),
    )

    assert decision.action is not None
    assert decision.action.kind is ActionKind.INSTALL_COLD_START_GUARD
    assert decision.reasons == ("checkpointed-action-requires-current-boot-guard",)
    assert decision.checkpoint.pending_action == decision.action


@pytest.mark.parametrize("kind", [ActionKind.STOP_FORMER_OWNER, ActionKind.ENABLE_ACTIVE])
@pytest.mark.parametrize("guard_boot_id", [None, "previous-boot"])
def test_pending_authority_action_disables_active_dataplane_before_guard_recovery(
    policy: VMHAController,
    kind: ActionKind,
    guard_boot_id: str | None,
) -> None:
    snapshot = _snapshot(
        cloud=_owned_cloud(),
        guard_boot_id=guard_boot_id,
        data_plane_mode=DataPlaneMode.ACTIVE,
    )
    target = (
        snapshot.peer_node_id if kind is ActionKind.STOP_FORMER_OWNER else snapshot.local_node_id
    )
    action = ControllerAction(
        kind=kind,
        operation_id=f"{snapshot.boot_id}:7:{kind.value}:{target}",
        boot_id=snapshot.boot_id,
        target_node_id=target,
        allocation_id=snapshot.cloud.allocation_id,
        ownership_epoch=snapshot.cloud.ownership_epoch,
        generation_id=snapshot.local_generation_id,
        digests=snapshot.local_digests,
    )

    decision = policy.decide(
        snapshot,
        ControllerCheckpoint(sequence=7, pending_action=action),
    )

    assert decision.action is not None
    assert decision.action.kind is ActionKind.DISABLE_ACTIVE
    assert decision.reasons == ("checkpointed-action-requires-current-boot-guard",)
    assert decision.checkpoint.pending_action == decision.action


def test_checkpointed_fencing_is_cancelled_if_peer_recovers(
    policy: VMHAController,
) -> None:
    stale = _snapshot(now=110.0, peer_received_at=80.0)
    first = policy.decide(stale, ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0))
    assert first.action is not None
    assert first.action.kind is ActionKind.STOP_FORMER_OWNER

    recovered = policy.decide(replace(stale, peer_received_at=109.0), first.checkpoint)
    assert recovered.state is HAState.BLOCKED
    assert recovered.action is None
    assert recovered.reasons == ("checkpointed-action-prerequisites-changed",)


def test_checkpointed_fencing_is_cancelled_if_cloud_target_identity_changes(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(now=110.0, peer_received_at=80.0)
    first = policy.decide(
        snapshot,
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert first.action is not None
    assert first.action.kind is ActionKind.STOP_FORMER_OWNER

    changed = policy.decide(
        replace(
            snapshot,
            cloud=replace(snapshot.cloud, former_owner_node_id="foreign-node"),
        ),
        first.checkpoint,
    )

    assert changed.action is None
    assert changed.reasons == ("checkpointed-action-prerequisites-changed",)


def test_crash_replay_blocks_when_attachment_authority_changes(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        now=110.0,
        peer_received_at=80.0,
        cloud=_cloud(owner=None, state=ComputeState.STOPPED, absent=True),
    )
    first = policy.decide(snapshot, ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0))
    assert first.action is not None
    assert first.action.kind is ActionKind.ATTACH_CANDIDATE

    raced = policy.decide(
        replace(snapshot, cloud=_cloud(owner="foreign", state=ComputeState.STOPPED, absent=True)),
        first.checkpoint,
    )
    assert raced.state is HAState.BLOCKED
    assert raced.action is None
    assert raced.reasons == ("checkpointed-action-prerequisites-changed",)


def test_crash_replay_blocks_when_shared_allocation_identity_changes(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        now=110.0,
        peer_received_at=80.0,
        cloud=_cloud(owner=None, state=ComputeState.STOPPED, absent=True),
    )
    first = policy.decide(
        snapshot,
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert first.action is not None
    assert first.action.kind is ActionKind.ATTACH_CANDIDATE

    changed = policy.decide(
        replace(snapshot, cloud=replace(snapshot.cloud, allocation_id="replacement")),
        first.checkpoint,
    )
    assert changed.state is HAState.BLOCKED
    assert changed.action is None
    assert changed.reasons == ("checkpointed-action-prerequisites-changed",)


def test_attach_completion_advances_to_authoritative_candidate_revision(
    policy: VMHAController,
) -> None:
    detached = _snapshot(
        now=110.0,
        peer_received_at=80.0,
        cloud=_cloud(
            owner=None,
            state=ComputeState.STOPPED,
            absent=True,
            ownership_epoch="41",
        ),
    )
    transfer = ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0)

    attach = policy.decide(detached, transfer)
    assert attach.action is not None
    assert attach.action.kind is ActionKind.ATTACH_CANDIDATE
    assert attach.action.ownership_epoch == "41"

    replay = policy.decide(detached, attach.checkpoint)
    assert replay.action == attach.action
    assert replay.checkpoint == attach.checkpoint

    attached = replace(
        detached,
        cloud=_cloud(
            owner="node-b",
            state=ComputeState.STOPPED,
            absent=True,
            attached=True,
            ownership_epoch="42",
        ),
    )
    confirm = policy.decide(attached, attach.checkpoint)
    assert confirm.action is not None
    assert confirm.action.kind is ActionKind.CONFIRM_CANDIDATE_OWNERSHIP
    assert confirm.action.ownership_epoch == "42"
    assert confirm.checkpoint.pending_action == confirm.action

    confirm_replay = policy.decide(attached, confirm.checkpoint)
    assert confirm_replay.action == confirm.action
    assert confirm_replay.checkpoint == confirm.checkpoint

    confirmed = replace(
        attached,
        cloud=replace(attached.cloud, ownership_re_read_exact=True),
    )
    routes = policy.decide(confirmed, confirm.checkpoint)
    assert routes.action is not None
    assert routes.action.kind is ActionKind.RECONCILE_ROUTES
    assert routes.action.ownership_epoch == "42"
    assert routes.action.takeover_fence_required is True

    routed = replace(
        confirmed,
        routes_reconciled_context=_reconciled(
            confirmed,
            operation_id=routes.action.operation_id,
            ownership_incarnation=routes.action.ownership_incarnation,
        ),
    )
    enable = policy.decide(routed, routes.checkpoint)
    assert enable.action is not None
    assert enable.action.kind is ActionKind.ENABLE_ACTIVE
    assert enable.action.ownership_epoch == "42"


def test_attach_completion_still_checkpoints_confirmation_after_exact_reread(
    policy: VMHAController,
) -> None:
    detached = _snapshot(
        now=110.0,
        peer_received_at=80.0,
        cloud=_cloud(
            owner=None,
            state=ComputeState.STOPPED,
            absent=True,
            ownership_epoch="41",
        ),
    )
    attach = policy.decide(
        detached,
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    observed = replace(
        detached,
        cloud=_cloud(
            owner="node-b",
            state=ComputeState.STOPPED,
            absent=True,
            attached=True,
            confirmed=True,
            ownership_epoch="42",
        ),
    )

    confirmation = policy.decide(observed, attach.checkpoint)

    assert confirmation.action is not None
    assert confirmation.action.kind is ActionKind.CONFIRM_CANDIDATE_OWNERSHIP
    assert confirmation.checkpoint.transfer_continuity is not None
    assert not confirmation.checkpoint.transfer_continuity.ownership_confirmed


@pytest.mark.parametrize(
    "changes",
    [
        {"ownership_epoch": "41"},
        {"ownership_epoch": "40"},
        {"ownership_epoch": "0"},
        {"ownership_epoch": ""},
        {"ownership_epoch": "forged"},
        {"ownership_epoch": "9" * 5000},
        {"authoritative": False},
        {"observed_owner_node_id": None},
        {"observed_owner_node_id": "foreign"},
        {"allocation_id": "replacement"},
        {"former_owner_node_id": "foreign"},
        {"former_owner_compute_state": ComputeState.RUNNING},
        {"former_attachment_absent": False},
        {"candidate_attachment_exact": False},
    ],
)
def test_attach_completion_rejects_incomplete_or_stale_observation(
    policy: VMHAController,
    changes: dict[str, object],
) -> None:
    detached = _snapshot(
        now=110.0,
        peer_received_at=80.0,
        cloud=_cloud(
            owner=None,
            state=ComputeState.STOPPED,
            absent=True,
            ownership_epoch="41",
        ),
    )
    attach = policy.decide(
        detached,
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert attach.action is not None
    assert attach.action.kind is ActionKind.ATTACH_CANDIDATE
    observed = replace(
        detached,
        cloud=replace(
            _cloud(
                owner="node-b",
                state=ComputeState.STOPPED,
                absent=True,
                attached=True,
                ownership_epoch="42",
            ),
            **changes,
        ),
    )

    decision = policy.decide(observed, attach.checkpoint)

    assert decision.state is HAState.BLOCKED
    assert decision.action is None
    assert decision.reasons == ("checkpointed-action-prerequisites-changed",)
    assert not decision.forwarding_enabled


@pytest.mark.parametrize(
    "snapshot_changes",
    [
        {"local_generation_id": "d" * 64},
        {"local_digests": replace(DIGESTS, static_routes="d" * 64)},
    ],
)
def test_attach_completion_rejects_changed_generation_or_policy(
    policy: VMHAController,
    snapshot_changes: dict[str, object],
) -> None:
    detached = _snapshot(
        now=110.0,
        peer_received_at=80.0,
        cloud=_cloud(
            owner=None,
            state=ComputeState.STOPPED,
            absent=True,
            ownership_epoch="41",
        ),
    )
    attach = policy.decide(
        detached,
        ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0),
    )
    assert attach.action is not None
    observed = replace(
        detached,
        cloud=_cloud(
            owner="node-b",
            state=ComputeState.STOPPED,
            absent=True,
            attached=True,
            ownership_epoch="42",
        ),
        **snapshot_changes,
    )

    decision = policy.decide(observed, attach.checkpoint)

    assert decision.state is HAState.BLOCKED
    assert decision.action is None
    assert decision.reasons == ("checkpointed-action-prerequisites-changed",)


def test_restart_cancels_old_boot_effect_and_reinstalls_guard(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    owned = replace(owned, routes_reconciled_context=_reconciled(owned))
    enable = policy.decide(owned, _proven_transfer_checkpoint(owned))
    assert enable.action is not None
    assert enable.action.kind is ActionKind.ENABLE_ACTIVE

    restarted = policy.decide(
        replace(owned, boot_id="new-boot", guard_boot_id="local-boot"),
        enable.checkpoint,
    )
    assert restarted.action is not None
    assert restarted.action.kind is ActionKind.INSTALL_COLD_START_GUARD
    assert restarted.action.boot_id == "new-boot"
    assert restarted.action.operation_id != enable.action.operation_id


def test_dual_suspicion_never_authorizes_two_active_nodes(policy: VMHAController) -> None:
    owner = _snapshot(
        local_node_id="node-a",
        peer_node_id="node-b",
        configured_role=ConfiguredRole.ACTIVE,
        peer_heartbeat=_peer(node_id="node-b", configured_role="passive"),
        cloud=replace(
            _owned_cloud(),
            observed_owner_node_id="node-a",
            former_owner_node_id="node-b",
        ),
        data_plane_mode=DataPlaneMode.ACTIVE,
        peer_received_at=80.0,
    )
    owner = replace(owner, routes_reconciled_context=_reconciled(owner))
    candidate = _snapshot(peer_received_at=80.0)

    owner_decision = policy.decide(owner, ControllerCheckpoint())
    candidate_decision = policy.decide(candidate, ControllerCheckpoint())

    assert owner_decision.forwarding_enabled
    assert owner_decision.action is None
    assert not candidate_decision.forwarding_enabled
    assert candidate_decision.action is None
    assert candidate_decision.state is HAState.SUSPECT


def test_promoted_passive_retains_active_authority_from_exact_establishment_proof(
    policy: VMHAController,
) -> None:
    promoted = _snapshot(
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
    )
    promoted = replace(promoted, routes_reconciled_context=_reconciled(promoted))

    decision = policy.decide(
        promoted,
        ControllerCheckpoint(
            state=HAState.ACTIVE,
            established_ownership_context=promoted.ownership_context,
        ),
    )

    assert decision.state is HAState.ACTIVE
    assert decision.action is None
    assert decision.forwarding_enabled
    assert decision.checkpoint.established_ownership_context == promoted.ownership_context


def test_mtls_rotation_inhibition_preserves_current_owner_dataplane(
    policy: VMHAController,
) -> None:
    owner = _snapshot(
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
        transfer_inhibited=True,
    )
    owner = replace(owner, routes_reconciled_context=_reconciled(owner))

    decision = policy.decide(
        owner,
        ControllerCheckpoint(
            state=HAState.ACTIVE,
            established_ownership_context=owner.ownership_context,
        ),
    )

    assert decision.action is None
    assert decision.forwarding_enabled
    assert decision.state is HAState.ACTIVE


def test_transfer_inhibition_preserves_owner_when_missing_peer_has_no_generation(
    policy: VMHAController,
) -> None:
    owner = _snapshot(
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
        peer_heartbeat=None,
        transfer_inhibited=True,
    )
    owner = replace(owner, routes_reconciled_context=_reconciled(owner))

    decision = policy.decide(
        owner,
        ControllerCheckpoint(
            state=HAState.ACTIVE,
            established_ownership_context=owner.ownership_context,
        ),
    )

    assert decision.action is None
    assert decision.forwarding_enabled
    assert decision.state is HAState.DEGRADED
    assert decision.reasons == ("peer-generation-unavailable",)


def test_transfer_inhibition_allows_exact_owner_forwarding_restore_only_with_current_proof(
    policy: VMHAController,
) -> None:
    owner = _snapshot(
        cloud=_owned_cloud(),
        transfer_inhibited=True,
    )
    owner = replace(owner, routes_reconciled_context=_reconciled(owner))
    checkpoint = ControllerCheckpoint(
        state=HAState.BLOCKED,
        established_ownership_context=owner.ownership_context,
    )

    enable = policy.decide(owner, checkpoint)

    assert enable.action is not None
    assert enable.action.kind is ActionKind.ENABLE_ACTIVE

    replay = policy.decide(owner, enable.checkpoint)

    assert replay.action == enable.action
    assert replay.reasons == ("replaying-checkpointed-action",)

    stale_routes = policy.decide(
        replace(owner, routes_reconciled_context=None),
        enable.checkpoint,
    )

    assert stale_routes.action is None
    assert stale_routes.reasons == ("checkpointed-action-prerequisites-changed",)
    assert stale_routes.checkpoint.pending_action is None


def test_mtls_rotation_inhibition_blocks_passive_transfer_without_action(
    policy: VMHAController,
) -> None:
    decision = policy.decide(
        _snapshot(
            transfer_inhibited=True,
            peer_received_at=80.0,
        ),
        ControllerCheckpoint(),
    )

    assert decision.state is HAState.BLOCKED
    assert decision.action is None
    assert decision.reasons == ("mtls-rotation-active",)
    assert not decision.forwarding_enabled


def test_apply_owner_adoption_establishes_promoted_owner_before_unlock(
    policy: VMHAController,
) -> None:
    cloud = replace(
        _owned_cloud(),
        former_owner_compute_state=ComputeState.RUNNING,
    )
    locked = _snapshot(
        cloud=cloud,
        apply_locked=True,
        apply_owner_adoption=True,
        data_plane_mode=DataPlaneMode.PASSIVE,
    )

    fenced = policy.decide(
        locked,
        ControllerCheckpoint(
            state=HAState.BLOCKED,
            ownership_continuity_invalidated=True,
            ownership_incarnation=1,
        ),
    )

    assert fenced.state is HAState.BLOCKED
    assert fenced.action is None
    assert fenced.reasons == ("apply-lock-held",)
    assert fenced.checkpoint.established_ownership_context == locked.ownership_context
    assert fenced.checkpoint.ownership_continuity_invalidated is False

    unlocked = policy.decide(replace(locked, apply_locked=False), fenced.checkpoint)

    assert unlocked.state is HAState.PROMOTING
    assert unlocked.action is not None
    assert unlocked.action.kind is ActionKind.RECONCILE_ROUTES


def test_apply_locked_active_owner_fences_then_materializes_passive(
    policy: VMHAController,
) -> None:
    active = _snapshot(
        cloud=replace(
            _owned_cloud(),
            former_owner_compute_state=ComputeState.RUNNING,
        ),
        apply_locked=True,
        apply_owner_adoption=True,
        data_plane_mode=DataPlaneMode.ACTIVE,
        readiness=LocalReadiness(True, True, False, True),
    )
    active = replace(active, routes_reconciled_context=_reconciled(active))
    checkpoint = ControllerCheckpoint(
        state=HAState.ACTIVE,
        established_ownership_context=active.ownership_context,
    )

    fenced = policy.decide(active, checkpoint)

    assert fenced.action is not None
    assert fenced.action.kind is ActionKind.DISABLE_ACTIVE
    assert fenced.checkpoint.repair_attempt is None

    materializing = policy.decide(
        replace(active, data_plane_mode=DataPlaneMode.BLOCKED),
        fenced.checkpoint,
    )

    assert materializing.action is not None
    assert materializing.action.kind is ActionKind.ENTER_PASSIVE
    assert materializing.checkpoint.repair_attempt is None

    locked_passive = policy.decide(
        replace(
            active,
            data_plane_mode=DataPlaneMode.PASSIVE,
            readiness=LocalReadiness(True, True, True, True),
        ),
        materializing.checkpoint,
    )

    assert locked_passive.state is HAState.BLOCKED
    assert locked_passive.action is None
    assert locked_passive.reasons == ("apply-lock-held",)
    assert locked_passive.checkpoint.repair_attempt is None

    unlocked = policy.decide(
        replace(
            active,
            apply_locked=False,
            data_plane_mode=DataPlaneMode.PASSIVE,
            readiness=LocalReadiness(True, True, True, True),
        ),
        locked_passive.checkpoint,
    )

    assert unlocked.state is HAState.PROMOTING
    assert unlocked.action is not None
    assert unlocked.action.kind is ActionKind.ENABLE_ACTIVE


def test_apply_locked_owner_retires_exhausted_repair_before_materialization(
    policy: VMHAController,
) -> None:
    locked = _snapshot(
        cloud=replace(
            _owned_cloud(),
            former_owner_compute_state=ComputeState.RUNNING,
        ),
        apply_locked=True,
        apply_owner_adoption=True,
        data_plane_mode=DataPlaneMode.BLOCKED,
        readiness=LocalReadiness(True, True, False, True),
    )
    locked = replace(locked, routes_reconciled_context=_reconciled(locked))
    checkpoint = ControllerCheckpoint(
        state=HAState.REPAIR_EXHAUSTED,
        established_ownership_context=locked.ownership_context,
        repair_attempt=RepairAttempt(
            operation_id="prior-repair",
            owner_node_id=locked.local_node_id,
            allocation_id=locked.cloud.allocation_id,
            ownership_epoch=locked.cloud.ownership_epoch,
            ownership_incarnation=0,
            generation_id=locked.local_generation_id,
            boot_id=locked.boot_id,
            failure_fingerprint=("bgp-not-ready",),
            started_at=90.0,
            deadline_at=95.0,
        ),
    )

    materializing = policy.decide(locked, checkpoint)

    assert materializing.action is not None
    assert materializing.action.kind is ActionKind.ENTER_PASSIVE
    assert materializing.checkpoint.repair_attempt is None


def test_apply_lock_cancels_checkpointed_owner_repair_before_replay(
    policy: VMHAController,
) -> None:
    active = _snapshot(
        cloud=replace(
            _owned_cloud(),
            former_owner_compute_state=ComputeState.RUNNING,
        ),
        data_plane_mode=DataPlaneMode.ACTIVE,
        readiness=LocalReadiness(True, True, False, True),
    )
    active = replace(active, routes_reconciled_context=_reconciled(active))
    checkpoint = ControllerCheckpoint(
        state=HAState.ACTIVE,
        established_ownership_context=active.ownership_context,
    )
    repairing = policy.decide(active, checkpoint)
    assert repairing.action is not None
    assert repairing.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE

    fenced = policy.decide(
        replace(
            active,
            apply_locked=True,
            apply_owner_adoption=True,
        ),
        repairing.checkpoint,
    )

    assert fenced.action is not None
    assert fenced.action.kind is ActionKind.DISABLE_ACTIVE
    assert fenced.checkpoint.pending_action == fenced.action
    assert fenced.checkpoint.repair_attempt is None


@pytest.mark.parametrize(
    "pending_kind",
    [
        ActionKind.STOP_FORMER_OWNER,
        ActionKind.DETACH_FORMER_ATTACHMENT,
        ActionKind.ATTACH_CANDIDATE,
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
        ActionKind.RECONCILE_ROUTES,
    ],
)
def test_runtime_disables_active_dataplane_before_pending_mutation_replay(
    policy: VMHAController,
    pending_kind: ActionKind,
) -> None:
    transfer_checkpoint = ControllerCheckpoint(state=HAState.SUSPECT, suspect_since=90.0)
    confirm_snapshot = _snapshot(
        now=110.0,
        peer_received_at=80.0,
        cloud=_cloud(
            owner="node-b",
            state=ComputeState.STOPPED,
            absent=True,
            attached=True,
            ownership_epoch="42",
        ),
    )
    reconcile_snapshot = _snapshot(cloud=_owned_cloud())
    cases = {
        ActionKind.STOP_FORMER_OWNER: (
            _snapshot(now=110.0, peer_received_at=80.0),
            transfer_checkpoint,
        ),
        ActionKind.DETACH_FORMER_ATTACHMENT: (
            _snapshot(
                now=110.0,
                peer_received_at=80.0,
                cloud=_cloud(state=ComputeState.STOPPED),
            ),
            transfer_checkpoint,
        ),
        ActionKind.ATTACH_CANDIDATE: (
            _snapshot(
                now=110.0,
                peer_received_at=80.0,
                cloud=_cloud(
                    owner=None,
                    state=ComputeState.STOPPED,
                    absent=True,
                ),
            ),
            transfer_checkpoint,
        ),
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP: (
            confirm_snapshot,
            _proven_transfer_checkpoint(
                confirm_snapshot,
                ownership_confirmed=False,
            ),
        ),
        ActionKind.RECONCILE_ROUTES: (
            reconcile_snapshot,
            _proven_transfer_checkpoint(reconcile_snapshot),
        ),
    }
    passive_snapshot, initial_checkpoint = cases[pending_kind]
    pending = policy.decide(passive_snapshot, initial_checkpoint)
    assert pending.action is not None
    assert pending.action.kind is pending_kind
    replay = policy.decide(passive_snapshot, pending.checkpoint)
    assert replay.action == pending.action

    events: list[str] = []

    class Snapshots:
        def observe(self) -> ControllerSnapshot:
            return replace(passive_snapshot, data_plane_mode=DataPlaneMode.ACTIVE)

    class Checkpoints:
        value = pending.checkpoint

        def load(self) -> ControllerCheckpoint:
            return self.value

        def save(self, checkpoint: ControllerCheckpoint) -> None:
            events.append("checkpoint")
            self.value = checkpoint

    class Effects:
        def apply(self, action: ControllerAction) -> None:
            events.append(action.kind.value)

    checkpoints = Checkpoints()
    runtime = RecoverableController(
        policy=policy,
        snapshots=Snapshots(),
        checkpoints=checkpoints,
        effects=Effects(),
    )

    disabled = runtime.step()

    assert disabled.action is not None
    assert disabled.action.kind is ActionKind.DISABLE_ACTIVE
    assert disabled.reasons == ("checkpointed-action-requires-passive-dataplane",)
    assert events == ["checkpoint", ActionKind.DISABLE_ACTIVE.value]
    assert checkpoints.value.pending_action == disabled.action


def test_runtime_persists_before_effect() -> None:
    events: list[str] = []

    class Snapshots:
        def observe(self) -> ControllerSnapshot:
            return _snapshot(data_plane_mode=DataPlaneMode.BLOCKED)

    class Checkpoints:
        value = ControllerCheckpoint()

        def load(self) -> ControllerCheckpoint:
            return self.value

        def save(self, checkpoint: ControllerCheckpoint) -> None:
            events.append("checkpoint")
            self.value = checkpoint

    class Effects:
        def apply(self, action: object) -> None:
            events.append("effect")

    runtime = RecoverableController(
        policy=VMHAController(peer_timeout_seconds=10, suspicion_seconds=5),
        snapshots=Snapshots(),
        checkpoints=Checkpoints(),
        effects=Effects(),
        decision_observer=lambda _snapshot, _previous, _decision: events.append("observer"),
    )
    runtime.step()
    assert events == ["checkpoint", "observer", "effect"]


def test_runtime_disables_active_dataplane_when_pending_enable_loses_authority() -> None:
    owned = _snapshot(cloud=_owned_cloud())
    owned = replace(owned, routes_reconciled_context=_reconciled(owned))
    events: list[str] = []
    actions: list[ActionKind] = []

    class Snapshots:
        value = owned

        def observe(self) -> ControllerSnapshot:
            return self.value

    class Checkpoints:
        value = _proven_transfer_checkpoint(owned)

        def load(self) -> ControllerCheckpoint:
            return self.value

        def save(self, checkpoint: ControllerCheckpoint) -> None:
            events.append("checkpoint")
            self.value = checkpoint

    snapshots = Snapshots()

    class Effects:
        def apply(self, action: ControllerAction) -> None:
            events.append("effect")
            actions.append(action.kind)
            if action.kind is ActionKind.ENABLE_ACTIVE:
                snapshots.value = replace(
                    owned,
                    data_plane_mode=DataPlaneMode.ACTIVE,
                    cloud=_cloud(
                        owner=None,
                        state=ComputeState.STOPPED,
                        absent=True,
                    ),
                )

    checkpoints = Checkpoints()
    runtime = RecoverableController(
        policy=VMHAController(peer_timeout_seconds=10, suspicion_seconds=5),
        snapshots=snapshots,
        checkpoints=checkpoints,
        effects=Effects(),
    )

    enable = runtime.step()
    assert enable.action is not None
    assert enable.action.kind is ActionKind.ENABLE_ACTIVE

    events.clear()
    disable = runtime.step()

    assert disable.action is not None
    assert disable.action.kind is ActionKind.DISABLE_ACTIVE
    assert disable.reasons == ("checkpointed-action-prerequisites-changed",)
    assert events == ["checkpoint", "effect"]
    assert actions == [ActionKind.ENABLE_ACTIVE, ActionKind.DISABLE_ACTIVE]
    assert checkpoints.value.pending_action == disable.action
    assert checkpoints.value.ownership_continuity_invalidated


def test_failed_effect_leaves_the_pre_effect_checkpoint_for_exact_replay() -> None:
    class Snapshots:
        def observe(self) -> ControllerSnapshot:
            return _snapshot(data_plane_mode=DataPlaneMode.BLOCKED)

    class Checkpoints:
        value = ControllerCheckpoint()

        def load(self) -> ControllerCheckpoint:
            return self.value

        def save(self, checkpoint: ControllerCheckpoint) -> None:
            self.value = checkpoint

    class Effects:
        def apply(self, action: object) -> None:
            raise RuntimeError("injected route failure")

    checkpoints = Checkpoints()
    runtime = RecoverableController(
        policy=VMHAController(peer_timeout_seconds=10, suspicion_seconds=5),
        snapshots=Snapshots(),
        checkpoints=checkpoints,
        effects=Effects(),
    )

    with pytest.raises(RuntimeError, match="injected route failure"):
        runtime.step()

    assert checkpoints.value.pending_action is not None
    replay = runtime.policy.decide(Snapshots().observe(), checkpoints.value)
    assert replay.action == checkpoints.value.pending_action
