from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import replace
from types import SimpleNamespace

import pytest

from nebius_vpngw.agent.vm_ha.models import StateValidationError
from nebius_vpngw.agent.vm_ha.store import AtomicGenerationStore, CorruptStateError
from nebius_vpngw.deploy.route_manager import RouteManager
from nebius_vpngw.deploy.vm_ha_cloud import AmbiguousHACloudError
from nebius_vpngw.deploy.vm_ha_routes import (
    BGPRouteReadiness,
    LogicalStaticRouteManifest,
    ManagedRouteKind,
    ManagedRouteOwnership,
    ManagedRouteSnapshot,
    RouteMutationKind,
    RouteReconciliationContext,
    RouteTransitionState,
    VerifiedAllocationOwnership,
    VMHARouteReconciler,
    execute_route_plan,
)


def _static_manifest(*prefixes: str) -> LogicalStaticRouteManifest:
    payload = json.dumps(
        [{"connection": "site-a", "remote_prefixes": list(prefixes)}],
        sort_keys=True,
        separators=(",", ":"),
    )
    return LogicalStaticRouteManifest.from_committed_json(
        payload,
        expected_digest=hashlib.sha256(payload.encode()).hexdigest(),
    )


def _bgp(
    *,
    learned: tuple[str, ...] = ("10.20.0.0/16",),
    usable: tuple[str, ...] = ("10.20.0.0/16",),
    required: tuple[str, ...] = ("10.20.0.0/16",),
    observed_policy: str = "policy-a",
) -> BGPRouteReadiness:
    return BGPRouteReadiness.normalize(
        configured_sessions=("169.254.10.2",),
        established_sessions=("169.254.10.2",),
        required_prefixes=required,
        optional_prefixes=("10.21.0.0/16",),
        learned_prefixes=learned,
        usable_xfrm_prefixes=usable,
        observed_import_policy_digest=observed_policy,
        committed_import_policy_digest="policy-a",
    )


def _owner(node_id: str = "node-b") -> VerifiedAllocationOwnership:
    return VerifiedAllocationOwnership(
        cluster_id="cluster-a",
        candidate_node_id="node-b",
        observed_owner_node_id=node_id,
        allocation_id="shared-allocation",
        ownership_epoch="7",
    )


def _context(
    *,
    operation_id: str = "boot-a:7:reconcile-routes:node-b",
    ownership_epoch: str = "7",
) -> RouteReconciliationContext:
    return RouteReconciliationContext(
        operation_id=operation_id,
        cluster_id="cluster-a",
        owner_node_id="node-b",
        allocation_id="shared-allocation",
        ownership_epoch=ownership_epoch,
        generation_id="generation-a",
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
        ownership_incarnation=3,
    )


def _observations(*plans):
    queued = deque(plans)
    return lambda: queued.popleft()


def _route(
    prefix: str,
    *,
    kind: ManagedRouteKind,
    allocation_id: str = "shared-allocation",
    route_id: str | None = None,
    cluster_id: str = "cluster-a",
) -> ManagedRouteSnapshot:
    return ManagedRouteSnapshot(
        route_id=route_id or f"route-{prefix}",
        prefix=prefix,
        allocation_id=allocation_id,
        ownership=ManagedRouteOwnership(cluster_id=cluster_id, kind=kind),
    )


def _reconciler(*, hold_down: float = 30, observations: int = 2) -> VMHARouteReconciler:
    return VMHARouteReconciler(
        cluster_id="cluster-a",
        node_id="node-b",
        takeover_hold_down_seconds=hold_down,
        withdrawal_stability_observations=observations,
    )


def test_committed_static_manifest_rejects_digest_mismatch() -> None:
    with pytest.raises(ValueError, match="digest mismatch"):
        LogicalStaticRouteManifest.from_committed_json("[]", expected_digest="wrong")


def test_non_owner_never_plans_route_mutations() -> None:
    state = RouteTransitionState(takeover_started_at=10)
    plan = _reconciler().plan(
        ownership=_owner(node_id="node-a"),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(),
        existing_routes=(),
        state=state,
        now=20,
    )

    assert not plan.authorized
    assert plan.mutations == ()
    assert plan.next_state is state


@pytest.mark.parametrize("ownership_epoch", ["", "0", "-1", "local-hash"])
def test_route_ownership_rejects_non_compute_epoch(ownership_epoch: str) -> None:
    with pytest.raises(ValueError, match="Compute resource revision"):
        replace(_owner(), ownership_epoch=ownership_epoch)


def test_non_owner_cannot_execute_even_a_forged_authorized_plan(tmp_path) -> None:
    plan = _reconciler().plan(
        ownership=_owner(node_id="node-a"),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=10,
    )
    calls: list[str] = []
    result = execute_route_plan(
        plan,
        lambda mutation: calls.append(mutation.operation_id),
        context=_context(),
        reobserve_ownership=_owner,
        reobserve_plan=lambda: plan,
        persist_receipt=AtomicGenerationStore(tmp_path / "ha").save_route_reconciliation_receipt,
        observe_receipt=lambda: None,
    )
    assert result.committed_state is None
    assert calls == []


def test_ambiguous_owner_observation_fails_before_route_mutation(tmp_path) -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=10,
    )
    calls: list[str] = []

    def ambiguous_owner() -> VerifiedAllocationOwnership:
        raise AmbiguousHACloudError("ambiguous owner")

    with pytest.raises(AmbiguousHACloudError, match="ambiguous owner"):
        RouteManager.execute_vm_ha_route_plan(
            plan,
            context=_context(),
            apply_mutation=lambda mutation: calls.append(mutation.operation_id),
            reobserve_ownership=ambiguous_owner,
            reobserve_plan=lambda: plan,
            receipt_store=AtomicGenerationStore(tmp_path / "ha"),
        )

    assert calls == []


def test_owner_reconciles_static_and_local_frr_routes_to_shared_allocation() -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=10),
        now=20,
    )

    assert {(item.kind, item.prefix, item.route_kind) for item in plan.mutations} == {
        (RouteMutationKind.CREATE, "10.10.0.0/16", ManagedRouteKind.STATIC),
        (RouteMutationKind.CREATE, "10.20.0.0/16", ManagedRouteKind.BGP),
    }
    assert {item.allocation_id for item in plan.mutations} == {"shared-allocation"}


def test_takeover_preserves_existing_bgp_route_but_allows_new_local_route() -> None:
    plan = _reconciler(hold_down=60).plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(learned=("10.21.0.0/16",), usable=("10.21.0.0/16",), required=()),
        existing_routes=(_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),),
        state=RouteTransitionState(takeover_started_at=100),
        now=120,
    )

    assert plan.held_bgp_prefixes == {"10.20.0.0/16"}
    assert [(item.kind, item.prefix) for item in plan.mutations] == [
        (RouteMutationKind.CREATE, "10.21.0.0/16")
    ]


def test_bgp_withdrawal_requires_bounded_stable_absence_observations() -> None:
    reconciler = _reconciler(hold_down=10, observations=2)
    existing = (_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),)
    first = reconciler.plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(learned=(), usable=(), required=()),
        existing_routes=existing,
        state=RouteTransitionState(takeover_started_at=100),
        now=111,
    )

    assert first.mutations == ()
    assert first.held_bgp_prefixes == {"10.20.0.0/16"}
    assert first.next_state.absence_counts() == {"10.20.0.0/16": 1}

    second = reconciler.plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(learned=(), usable=(), required=()),
        existing_routes=existing,
        state=first.next_state,
        now=112,
    )

    assert [(item.kind, item.prefix) for item in second.mutations] == [
        (RouteMutationKind.DELETE, "10.20.0.0/16")
    ]


def test_route_reappearance_resets_withdrawal_stability() -> None:
    existing = (_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),)
    plan = _reconciler(hold_down=0).plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(),
        existing_routes=existing,
        state=RouteTransitionState(
            takeover_started_at=100,
            absent_bgp_observations=(("10.20.0.0/16", 1),),
        ),
        now=110,
    )

    assert plan.mutations == ()
    assert plan.next_state.absence_counts() == {}


def test_bgp_readiness_requires_sessions_policy_prefixes_and_xfrm() -> None:
    readiness = BGPRouteReadiness.normalize(
        configured_sessions=("peer-a", "peer-b"),
        established_sessions=("peer-a",),
        required_prefixes=("10.20.0.0/16", "10.21.0.0/16"),
        optional_prefixes=("10.22.0.0/16",),
        learned_prefixes=("10.20.0.0/16", "10.22.0.0/16"),
        usable_xfrm_prefixes=("10.22.0.0/16",),
        observed_import_policy_digest="stale",
        committed_import_policy_digest="current",
    )

    assert not readiness.promotion_ready
    assert readiness.blocked_reasons == (
        "configured-bgp-sessions-not-established",
        "bgp-import-policy-mismatch",
        "required-bgp-prefixes-not-learned",
        "required-bgp-prefixes-lack-usable-xfrm-next-hop",
    )
    assert readiness.missing_optional_prefixes == {"10.22.0.0/16"}


def test_stale_import_policy_cannot_make_learned_route_eligible() -> None:
    readiness = _bgp(observed_policy="stale-policy")

    assert readiness.eligible_prefixes == frozenset()


def test_empty_import_policy_digests_are_not_promotion_ready() -> None:
    readiness = BGPRouteReadiness.normalize(
        configured_sessions=("peer-a",),
        established_sessions=("peer-a",),
        required_prefixes=(),
        learned_prefixes=("10.20.0.0/16",),
        usable_xfrm_prefixes=("10.20.0.0/16",),
        observed_import_policy_digest="",
        committed_import_policy_digest="",
    )

    assert not readiness.promotion_ready
    assert readiness.eligible_prefixes == frozenset()


@pytest.mark.parametrize(
    "state",
    [
        RouteTransitionState(takeover_started_at=float("nan")),
        RouteTransitionState(
            takeover_started_at=0,
            absent_bgp_observations=(("10.20.0.0/16", -1),),
        ),
    ],
)
def test_invalid_transition_state_fails_closed(state: RouteTransitionState) -> None:
    with pytest.raises(ValueError):
        _reconciler().plan(
            ownership=_owner(),
            static_manifest=_static_manifest(),
            bgp=_bgp(required=(), learned=(), usable=()),
            existing_routes=(_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),),
            state=state,
            now=100,
        )


def test_non_finite_observation_time_fails_closed() -> None:
    with pytest.raises(ValueError):
        _reconciler().plan(
            ownership=_owner(),
            static_manifest=_static_manifest(),
            bgp=_bgp(required=(), learned=(), usable=()),
            existing_routes=(_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),),
            state=RouteTransitionState(takeover_started_at=0),
            now=float("nan"),
        )


def test_non_finite_takeover_hold_down_is_rejected() -> None:
    with pytest.raises(ValueError):
        _reconciler(hold_down=float("nan"))


def test_duplicate_owned_route_is_deleted_after_one_exact_route_is_retained() -> None:
    exact = _route(
        "10.10.0.0/16",
        kind=ManagedRouteKind.STATIC,
        route_id="route-exact",
    )
    duplicate = _route(
        "10.10.0.0/16",
        kind=ManagedRouteKind.STATIC,
        route_id="route-duplicate",
    )
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(exact, duplicate),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )

    assert [(item.kind, item.route_id) for item in plan.mutations] == [
        (RouteMutationKind.DELETE, "route-duplicate")
    ]


def test_explicit_route_ownership_blocks_name_based_takeover_and_foreign_conflict() -> None:
    foreign = _route(
        "10.10.0.0/16",
        kind=ManagedRouteKind.STATIC,
        route_id="vpngw-name-is-not-authority",
        cluster_id="other-cluster",
    )
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(foreign,),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )

    assert plan.mutations == ()
    assert plan.blocked_reasons == ("foreign-route-conflict:10.10.0.0/16",)


def test_unledgered_occupied_prefix_flows_from_route_manager_to_conflict_plan() -> None:
    route_manager = RouteManager(project_id="project-test")
    foreign = SimpleNamespace(
        metadata=SimpleNamespace(id="route-foreign", name="unmanaged"),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr="10.10.0.0/16"),
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id="foreign-allocation"),
                default_egress_gateway=False,
            ),
        ),
    )

    observed = route_manager._vm_ha_route_snapshots((foreign,), ownership_by_route_id={})
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16", "10.11.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=observed,
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )

    assert plan.mutations == ()
    assert plan.blocked_reasons == ("foreign-route-conflict:10.10.0.0/16",)


@pytest.mark.parametrize(
    "next_hop",
    [
        SimpleNamespace(
            allocation=SimpleNamespace(id="foreign-allocation"),
            default_egress_gateway=True,
        ),
        SimpleNamespace(
            allocation=SimpleNamespace(id=""),
            default_egress_gateway=False,
        ),
    ],
)
def test_vm_ha_route_observation_rejects_ambiguous_or_missing_next_hop(
    next_hop: SimpleNamespace,
) -> None:
    route = SimpleNamespace(
        metadata=SimpleNamespace(id="route-foreign", name="unmanaged"),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr="10.10.0.0/16"),
            next_hop=next_hop,
        ),
    )

    with pytest.raises(ValueError, match="next hop"):
        RouteManager(project_id="project-test")._vm_ha_route_snapshots(
            (route,), ownership_by_route_id={}
        )


def test_existing_route_with_old_allocation_is_replaced_with_shared_allocation() -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(
            _route(
                "10.10.0.0/16",
                kind=ManagedRouteKind.STATIC,
                allocation_id="old-node-allocation",
                route_id="route-a",
            ),
        ),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )

    assert len(plan.mutations) == 1
    assert plan.mutations[0].kind is RouteMutationKind.REPLACE
    assert plan.mutations[0].route_id == "route-a"
    assert plan.mutations[0].allocation_id == "shared-allocation"


def test_partial_failure_does_not_commit_observation_state_and_retry_is_idempotent(
    tmp_path,
) -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16", "10.11.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    calls: list[str] = []

    def fail_second(mutation) -> None:
        calls.append(mutation.operation_id)
        if len(calls) == 2:
            raise RuntimeError("injected")

    store = AtomicGenerationStore(tmp_path / "ha")
    result = execute_route_plan(
        plan,
        fail_second,
        context=_context(),
        reobserve_ownership=_owner,
        reobserve_plan=lambda: plan,
        persist_receipt=store.save_route_reconciliation_receipt,
        observe_receipt=store.load_route_reconciliation_receipt,
    )

    assert len(result.applied) == 1
    assert result.failed == plan.mutations[1]
    assert result.remaining == plan.mutations[1:]
    assert result.committed_state is None

    installed = {
        mutation.prefix: _route(
            mutation.prefix,
            kind=mutation.route_kind,
            route_id=f"installed-{mutation.prefix}",
        )
        for mutation in result.applied
    }
    retry = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16", "10.11.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=installed.values(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )

    assert [item.prefix for item in retry.mutations] == [result.failed.prefix]
    assert (
        execute_route_plan(
            retry,
            lambda mutation: None,
            context=_context(),
            reobserve_ownership=_owner,
            reobserve_plan=_observations(retry, replace(retry, mutations=())),
            persist_receipt=store.save_route_reconciliation_receipt,
            observe_receipt=store.load_route_reconciliation_receipt,
        ).committed_state
        == retry.next_state
    )


def test_ownership_epoch_change_mid_plan_stops_before_next_mutation(tmp_path) -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16", "10.11.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    owners = deque((_owner(), _owner(), replace(_owner(), ownership_epoch="8")))
    calls: list[str] = []
    store = AtomicGenerationStore(tmp_path / "ha")

    result = execute_route_plan(
        plan,
        lambda mutation: calls.append(mutation.operation_id),
        context=_context(),
        reobserve_ownership=lambda: owners.popleft(),
        reobserve_plan=lambda: plan,
        persist_receipt=store.save_route_reconciliation_receipt,
        observe_receipt=store.load_route_reconciliation_receipt,
    )

    assert result.applied == plan.mutations[:1]
    assert result.failed == plan.mutations[1]
    assert result.remaining == plan.mutations[1:]
    assert result.receipt is None
    assert len(calls) == 1


def test_route_receipt_is_exact_operation_bound_and_restart_durable(tmp_path) -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    store = AtomicGenerationStore(tmp_path / "ha")
    calls: list[str] = []
    first = RouteManager.execute_vm_ha_route_plan(
        plan,
        context=_context(),
        apply_mutation=lambda mutation: calls.append(mutation.operation_id),
        reobserve_ownership=_owner,
        reobserve_plan=_observations(plan, replace(plan, mutations=())),
        receipt_store=store,
    )

    assert first.receipt is not None
    assert first.receipt.context.operation_id == _context().operation_id
    assert len(calls) == 1
    replay = RouteManager.execute_vm_ha_route_plan(
        plan,
        context=_context(),
        apply_mutation=lambda mutation: calls.append(mutation.operation_id),
        reobserve_ownership=_owner,
        reobserve_plan=lambda: replace(plan, mutations=()),
        receipt_store=AtomicGenerationStore(store.root),
    )
    assert replay.receipt == first.receipt
    assert len(calls) == 1

    foreign_current = replace(
        plan,
        blocked_reasons=("foreign-route-conflict:10.10.0.0/16",),
        mutations=(),
    )
    with pytest.raises(RuntimeError, match="does not satisfy"):
        RouteManager.execute_vm_ha_route_plan(
            plan,
            context=_context(),
            apply_mutation=lambda mutation: calls.append(mutation.operation_id),
            reobserve_ownership=_owner,
            reobserve_plan=lambda: foreign_current,
            receipt_store=store,
        )
    assert len(calls) == 1

    settled_plan = replace(plan, mutations=())
    changed_operation = RouteManager.execute_vm_ha_route_plan(
        settled_plan,
        context=_context(operation_id="boot-a:8:reconcile-routes:node-b"),
        apply_mutation=lambda mutation: calls.append(mutation.operation_id),
        reobserve_ownership=_owner,
        reobserve_plan=_observations(settled_plan, settled_plan),
        receipt_store=store,
    )
    assert changed_operation.receipt != first.receipt
    assert len(calls) == 1


def test_route_receipt_store_rejects_fields_outside_secret_free_contract(tmp_path) -> None:
    store = AtomicGenerationStore(tmp_path / "ha")
    context = {**_context().to_dict(), "unexpected": "not-persisted"}

    with pytest.raises(StateValidationError, match="context has an invalid shape"):
        store.save_route_reconciliation_receipt(
            {
                "schema": "nebius-vpngw/vm-ha-route-reconciliation-receipt-v1",
                "context": context,
                "plan_digest": "d" * 64,
            }
        )

    assert not store.route_receipt_path.exists()


def test_route_executor_rejects_stale_ownership_epoch_without_mutation(tmp_path) -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    calls: list[str] = []
    result = RouteManager.execute_vm_ha_route_plan(
        plan,
        context=_context(ownership_epoch="8"),
        apply_mutation=lambda mutation: calls.append(mutation.operation_id),
        reobserve_ownership=_owner,
        reobserve_plan=lambda: plan,
        receipt_store=AtomicGenerationStore(tmp_path / "ha"),
    )
    assert result.committed_state is None
    assert result.receipt is None
    assert calls == []


def test_corrupt_route_receipt_fails_closed_before_mutation(tmp_path) -> None:
    store = AtomicGenerationStore(tmp_path / "ha")
    store.route_receipt_path.write_text("{truncated", encoding="utf-8")
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    with pytest.raises(CorruptStateError, match="route reconciliation receipt"):
        RouteManager.execute_vm_ha_route_plan(
            plan,
            context=_context(),
            apply_mutation=lambda mutation: pytest.fail("must not mutate"),
            reobserve_ownership=_owner,
            reobserve_plan=lambda: plan,
            receipt_store=store,
        )


def test_generation_adapter_consumes_task_one_logical_manifest_shape() -> None:
    payload = '[{"connection":"site-a","remote_prefixes":["10.10.0.0/16"]}]'
    generation = SimpleNamespace(
        logical_manifests=SimpleNamespace(static_routes_json=payload),
        digests=SimpleNamespace(static_routes=hashlib.sha256(payload.encode()).hexdigest()),
    )

    manifest = LogicalStaticRouteManifest.from_generation(generation)

    assert manifest.prefixes == {"10.10.0.0/16"}
