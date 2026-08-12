from __future__ import annotations

from dataclasses import replace

import pytest

from nebius_vpngw.agent.vm_ha import DigestSet, PeerHeartbeat
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ConfiguredRole,
    ControllerCheckpoint,
    ControllerSnapshot,
    DataPlaneMode,
    HAState,
    LocalReadiness,
    RecoverableController,
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
        digests=digests,
        service_healthy=True,
        route_ready=True,
        promotion_ready=True,
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
        manual_failback_requested=False,
        readiness=LocalReadiness(True, True, True, True),
        cloud=_cloud(),
        guard_boot_id="local-boot",
        data_plane_mode=DataPlaneMode.PASSIVE,
        routes_reconciled_context=None,
    )
    return replace(snapshot, **changes)


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
            ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
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
    routes = policy.decide(owned, ControllerCheckpoint())
    assert routes.action is not None
    assert routes.action.kind is ActionKind.RECONCILE_ROUTES

    forwarding = policy.decide(
        replace(owned, routes_reconciled_context=owned.route_reconciliation_context),
        ControllerCheckpoint(),
    )
    assert forwarding.action is not None
    assert forwarding.action.kind is ActionKind.ENABLE_ACTIVE

    active = policy.decide(
        replace(
            owned,
            routes_reconciled_context=owned.route_reconciliation_context,
            data_plane_mode=DataPlaneMode.ACTIVE,
        ),
        ControllerCheckpoint(),
    )
    assert active.state is HAState.ACTIVE
    assert active.action is None
    assert active.forwarding_enabled


def test_allocation_replacement_invalidates_route_reconciliation_before_forwarding(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud(), data_plane_mode=DataPlaneMode.ACTIVE)
    owned = replace(owned, routes_reconciled_context=owned.route_reconciliation_context)
    replacement = replace(
        owned,
        cloud=replace(
            owned.cloud,
            allocation_id="replacement-allocation",
            ownership_epoch="ownership-epoch-2",
        ),
    )

    disable = policy.decide(replacement, ControllerCheckpoint())
    assert disable.action is not None
    assert disable.action.kind is ActionKind.DISABLE_ACTIVE
    assert not disable.forwarding_enabled

    disabled = replace(replacement, data_plane_mode=DataPlaneMode.PASSIVE)
    reconcile = policy.decide(disabled, disable.checkpoint)
    assert reconcile.action is not None
    assert reconcile.action.kind is ActionKind.RECONCILE_ROUTES
    assert not reconcile.forwarding_enabled


def test_ownership_loss_and_reacquisition_requires_fresh_route_reconciliation(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud(), data_plane_mode=DataPlaneMode.ACTIVE)
    owned = replace(owned, routes_reconciled_context=owned.route_reconciliation_context)

    lost = replace(
        owned,
        cloud=_cloud(
            owner=None,
            state=ComputeState.STOPPED,
            absent=True,
            ownership_epoch="ownership-epoch-2",
        ),
    )
    disable = policy.decide(lost, ControllerCheckpoint())
    assert disable.action is not None
    assert disable.action.kind is ActionKind.DISABLE_ACTIVE

    reacquired = replace(
        owned,
        cloud=replace(_owned_cloud(), ownership_epoch="ownership-epoch-3"),
        data_plane_mode=DataPlaneMode.PASSIVE,
    )
    reconcile = policy.decide(reacquired, disable.checkpoint)
    assert reconcile.action is not None
    assert reconcile.action.kind is ActionKind.RECONCILE_ROUTES
    assert not reconcile.forwarding_enabled

    current = replace(
        reacquired,
        routes_reconciled_context=reacquired.route_reconciliation_context,
    )
    enable = policy.decide(current, ControllerCheckpoint())
    assert enable.action is not None
    assert enable.action.kind is ActionKind.ENABLE_ACTIVE


def test_route_failure_replays_without_enabling_forwarding(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    first = policy.decide(owned, ControllerCheckpoint())
    assert first.action is not None
    assert first.action.kind is ActionKind.RECONCILE_ROUTES

    replay = policy.decide(owned, first.checkpoint)
    assert replay.action == first.action
    assert not replay.forwarding_enabled


def test_checkpointed_route_effect_is_cancelled_when_ownership_epoch_changes(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    first = policy.decide(owned, ControllerCheckpoint())
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
    first = policy.decide(owned, ControllerCheckpoint())
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
        replace(snapshot, routes_reconciled_context=snapshot.route_reconciliation_context),
        ControllerCheckpoint(),
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
    decision = policy.decide(
        _snapshot(
            cloud=_owned_cloud(),
            **changes,
        ),
        ControllerCheckpoint(state=HAState.PROMOTING),
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
        cloud=_cloud(owner="node-b", attached=True, confirmed=True),
        data_plane_mode=DataPlaneMode.ACTIVE,
    )

    decision = policy.decide(
        replace(snapshot, routes_reconciled_context=snapshot.route_reconciliation_context),
        ControllerCheckpoint(),
    )

    assert decision.state is HAState.ACTIVE
    assert decision.action is None
    assert decision.forwarding_enabled
    assert decision.reasons == ("authoritative-owner-active",)


def test_passive_owner_requires_durable_establishment_when_peer_is_running(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        cloud=_cloud(owner="node-b", attached=True, confirmed=True),
        data_plane_mode=DataPlaneMode.ACTIVE,
    )
    snapshot = replace(
        snapshot,
        routes_reconciled_context=snapshot.route_reconciliation_context,
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


def test_failback_to_configured_active_requires_manual_intent(
    policy: VMHAController,
) -> None:
    snapshot = _snapshot(
        configured_role=ConfiguredRole.ACTIVE,
        peer_heartbeat=_peer(configured_role="passive"),
        peer_received_at=80.0,
    )
    automatic = policy.decide(snapshot, ControllerCheckpoint())
    assert automatic.state is HAState.NORMAL
    assert automatic.action is None
    assert automatic.reasons == ("manual-failback-required",)

    manual = policy.decide(
        replace(snapshot, manual_failback_requested=True), ControllerCheckpoint()
    )
    assert manual.action is not None
    assert manual.action.kind is ActionKind.STOP_FORMER_OWNER


def test_manual_failback_intent_is_rejected_on_configured_passive(
    policy: VMHAController,
) -> None:
    decision = policy.decide(
        _snapshot(manual_failback_requested=True),
        ControllerCheckpoint(),
    )

    assert decision.state is HAState.BLOCKED
    assert decision.action is None
    assert decision.reasons == ("manual-failback-invalid-for-passive-role",)


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


def test_restart_cancels_old_boot_effect_and_reinstalls_guard(
    policy: VMHAController,
) -> None:
    owned = _snapshot(cloud=_owned_cloud())
    owned = replace(owned, routes_reconciled_context=owned.route_reconciliation_context)
    enable = policy.decide(owned, ControllerCheckpoint())
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
    owner = replace(owner, routes_reconciled_context=owner.route_reconciliation_context)
    candidate = _snapshot(peer_received_at=80.0)

    owner_decision = policy.decide(owner, ControllerCheckpoint())
    candidate_decision = policy.decide(candidate, ControllerCheckpoint())

    assert owner_decision.forwarding_enabled
    assert owner_decision.action is None
    assert not candidate_decision.forwarding_enabled
    assert candidate_decision.action is None
    assert candidate_decision.state is HAState.SUSPECT


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
    )
    runtime.step()
    assert events == ["checkpoint", "effect"]


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
