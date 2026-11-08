from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.common import v1 as common
from nebius.api.nebius.vpc import v1 as vpc

from nebius_vpngw.deploy.ordinary_route_execute import execute
from nebius_vpngw.deploy.ordinary_route_ownership import RouteJournal, snapshot
from nebius_vpngw.deploy.ordinary_route_plan import inspect
from nebius_vpngw.deploy.ordinary_route_policy import Connection, Policy
from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message


@pytest.fixture
def world(tmp_path, monkeypatch):
    state = NS(routes={}, operations={}, effects=[], lost=False)
    journal = RouteJournal("project", "gateway", root=tmp_path / "routes")
    policy = Policy(
        (Connection("peer", "bgp", 0, ("169.254.1.2",), ("192.168.0.0/16",), 10),), ("10.0.0.0/16",)
    )

    def mutate(request, **kwargs):
        key = dict(kwargs["metadata"])["x-idempotency-key"]
        if key not in state.operations:
            state.effects.append((type(request).__name__, key))
            if isinstance(request, vpc.CreateRouteRequest):
                assert not any(
                    r.spec.destination.cidr == request.spec.destination.cidr
                    for r in state.routes.values()
                )
                identifier = "route-" + str(len(state.effects))
                state.routes[identifier] = vpc.Route(
                    metadata=common.ResourceMetadata(
                        id=identifier,
                        parent_id=request.metadata.parent_id,
                        name=request.metadata.name,
                        labels=dict(request.metadata.labels),
                        resource_version=1,
                    ),
                    spec=clone_nebius_sdk_message(request.spec),
                )
            else:
                identifier = request.id
                state.routes.pop(identifier, None)
            state.operations[key] = NS(
                id="operation-" + str(len(state.effects)),
                resource_id=identifier,
                successful=lambda: True,
                sync_wait=lambda **kwargs: None,
            )

        def wait():
            if state.lost:
                state.lost = False
                raise TimeoutError("lost accepted response")
            return state.operations[key]

        return NS(wait=wait)

    monkeypatch.setattr(vpc, "RouteServiceClient", lambda sdk: NS(create=mutate, delete=mutate))
    monkeypatch.setattr(
        common,
        "OperationServiceClient",
        lambda sdk: NS(
            get=lambda request, **kwargs: NS(
                wait=lambda: next(op for op in state.operations.values() if op.id == request.id)
            )
        ),
    )

    def observe():
        return {
            "infrastructure": {"table_ids": ["table"], "exact_hosts": "verified"},
            "allocations": {0: "allocation"},
            "routes": {
                "table": tuple(clone_nebius_sdk_message(route) for route in state.routes.values())
            },
        }

    def plan(learned=("192.168.1.0/24",), *, intent=policy):
        observed = observe()
        return inspect(
            project="project",
            gateway="gateway",
            policy=intent,
            learned={"peer": learned} if intent.dynamic else {},
            allocations=observed["allocations"],
            active_tables=("table",),
            routes=observed["routes"],
            ownership=journal.inventory(),
            infrastructure=observed["infrastructure"],
        )

    return state, journal, policy, plan, observe


def external_route(destination="192.168.1.0/24", allocation="allocation"):
    return vpc.Route(
        metadata=common.ResourceMetadata(
            id="external", parent_id="table", name="vpngw-192.168.1.0-24", resource_version=3
        ),
        spec=vpc.RouteSpec(
            destination=vpc.DestinationMatch(cidr=destination),
            next_hop=vpc.NextHop(allocation=vpc.AllocationNextHop(id=allocation)),
        ),
    )


def test_existing_matching_legacy_route_satisfies_coverage_without_adoption(world):
    state, journal, _policy, plan, observe = world
    state.routes["external"] = external_route()
    approved = plan()
    assert not approved["requests"]
    assert next(iter(approved["coverage"].values()))["kind"] == "external"
    execute(object(), journal, approved, approval="a" * 64, observe=observe)
    assert state.effects == []
    assert not journal.root.exists()


def test_creation_second_apply_and_withdrawal_preserve_exact_owned_routes(world):
    state, journal, _policy, plan, observe = world
    approved = plan()
    execute(object(), journal, approved, approval="a" * 64, observe=observe)
    assert len(state.effects) == 1
    before = journal.path.read_bytes()
    for next_plan in (plan(), plan(()), approved):
        execute(object(), journal, next_plan, approval="a" * 64, observe=observe)
        assert journal.path.read_bytes() == before
    assert len(state.routes) == 1
    assert len(state.effects) == 1


def test_authoritative_retirement_removes_only_exact_owned_route(world):
    state, journal, _policy, plan, observe = world
    state.routes["external"] = external_route("172.16.0.0/24", "unrelated")
    execute(object(), journal, plan(), approval="a" * 64, observe=observe)
    other_before = snapshot(state.routes["external"])
    retired = plan(intent=Policy((), ("10.0.0.0/16",)))
    assert [request["type"] for request in retired["requests"].values()] == ["DeleteRouteRequest"]
    execute(object(), journal, retired, approval="b" * 64, observe=observe)
    assert journal.inventory() == {}
    assert list(state.routes) == ["external"]
    assert snapshot(state.routes["external"]) == other_before


@pytest.mark.parametrize("destination", ["192.168.1.0/24", "192.168.1.0/25"])
def test_conflicting_foreign_exact_or_more_specific_routes_block_without_effects(
    world, destination
):
    state, _journal, _policy, plan, _observe = world
    state.routes["external"] = external_route(destination, "other")
    with pytest.raises(RuntimeError, match="conflict"):
        plan()
    assert not state.effects


@pytest.mark.parametrize(
    "boundary",
    ["intent", "accepted", "settled", "ownership", "verified", "complete", "lost-response"],
)
def test_every_creation_boundary_resumes_same_operation_and_records_exact_ownership(
    world, monkeypatch, boundary
):
    state, journal, _policy, plan, observe = world
    approved = plan()
    save = journal.save
    fired = False

    def interrupted_save():
        nonlocal fired
        save()
        transaction = journal.state["transaction"]
        operations = list(transaction["operations"].values())
        reached = bool(
            boundary in {"intent", "accepted", "settled"}
            and operations
            and operations[0]["status"] == boundary
            or boundary == "ownership"
            and journal.state["bindings"]
            or boundary == "verified"
            and transaction["verified"]
            or boundary == "complete"
            and transaction["phase"] == "complete"
        )
        if reached and not fired:
            fired = True
            raise RuntimeError("injected boundary")

    monkeypatch.setattr(journal, "save", interrupted_save)
    state.lost = boundary == "lost-response"
    with pytest.raises((RuntimeError, TimeoutError), match="injected|lost"):
        execute(object(), journal, approved, approval="a" * 64, observe=observe)
    execute(object(), journal, approved, approval="a" * 64, observe=observe)
    assert len(state.effects) == 1
    assert set(journal.inventory()) == set(state.routes)
    before = journal.path.read_bytes()
    execute(object(), journal, approved, approval="a" * 64, observe=observe)
    assert journal.path.read_bytes() == before


def test_infrastructure_drift_after_approval_blocks_before_cloud_mutation(world):
    state, journal, _policy, plan, observe = world
    approved = plan()

    def changed():
        value = observe()
        value["infrastructure"]["exact_hosts"] = "different"
        return value

    with pytest.raises(RuntimeError, match="infrastructure"):
        execute(object(), journal, approved, approval="a" * 64, observe=changed)
    assert not state.effects


def test_rejected_creation_plan_expansion_never_borrows_pending_approval(world):
    _state, journal, _policy, plan, observe = world
    approved = plan()
    with journal:
        journal.begin(approved, approval="a" * 64)
    expanded = deepcopy(approved)
    expanded["policy"] = {"different": "intent"}
    with pytest.raises(RuntimeError, match="original approved"):
        execute(object(), journal, expanded, approval="a" * 64, observe=observe)


@pytest.mark.parametrize("failure", [False, True])
def test_summary_is_verified_before_working_specifics_are_retired(world, monkeypatch, failure):
    from dataclasses import replace

    state, journal, _policy, plan, observe = world
    specifics = Policy(
        (Connection("static", "static", 0, (), ("192.168.1.0/25", "192.168.1.128/25"), 2),),
        ("10.0.0.0/8",),
    )
    execute(object(), journal, plan(intent=specifics), approval="a" * 64, observe=observe)
    approved = plan(intent=replace(specifics, summarize=True))
    before = deepcopy(state.routes)
    if failure:
        service = vpc.RouteServiceClient(object())

        def reject(request, **kwargs):
            raise RuntimeError("summary quota exhausted")

        monkeypatch.setattr(
            vpc, "RouteServiceClient", lambda sdk: NS(create=reject, delete=service.delete)
        )
        with pytest.raises(RuntimeError, match="quota"):
            execute(object(), journal, approved, approval="b" * 64, observe=observe)
        assert {key: snapshot(route) for key, route in state.routes.items()} == {
            key: snapshot(route) for key, route in before.items()
        }
        assert len(state.effects) == 2
    else:
        execute(object(), journal, approved, approval="b" * 64, observe=observe)
        assert [kind for kind, _ in state.effects] == [
            "CreateRouteRequest",
            "CreateRouteRequest",
            "CreateRouteRequest",
            "DeleteRouteRequest",
            "DeleteRouteRequest",
        ]
        assert {route.spec.destination.cidr for route in state.routes.values()} == {
            "192.168.1.0/24"
        }
        assert len(journal.inventory()) == 1
