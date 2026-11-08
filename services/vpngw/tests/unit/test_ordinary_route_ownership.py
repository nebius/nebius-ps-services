from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.vpc import v1 as vpc

from nebius_vpngw.deploy.ordinary_route_ownership import RouteJournal, labels, owned, read_ownership
from nebius_vpngw.deploy.ordinary_route_plan import request_record
from nebius_vpngw.deploy.replacement_cloud import Cloud, message_digest
from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message


@pytest.fixture
def route_case(tmp_path, monkeypatch):
    journal = RouteJournal("project", "gateway", root=tmp_path / "routes")
    request = vpc.CreateRouteRequest(
        metadata=ResourceMetadata(
            name="vpngw-route",
            parent_id="table",
            labels=labels("project", "gateway", "table", "192.168.0.0/24", 0),
        ),
        spec=vpc.RouteSpec(
            destination=vpc.DestinationMatch(cidr="192.168.0.0/24"),
            next_hop=vpc.NextHop(allocation=vpc.AllocationNextHop(id="allocation")),
        ),
    )
    route = vpc.Route(
        metadata=ResourceMetadata(
            id="route",
            parent_id="table",
            name="vpngw-route",
            resource_version=1,
            labels=dict(request.metadata.labels),
        ),
        spec=clone_nebius_sdk_message(request.spec),
    )
    state = NS(keys=[], route=route, lose_receipt=False, effects=0, operation=None)

    def create(current, **kwargs):
        assert message_digest(current) == message_digest(request)
        key = dict(kwargs["metadata"])["x-idempotency-key"]
        state.keys.append(key)
        if state.operation is None:
            state.effects += 1
            state.operation = NS(
                id="operation",
                resource_id="route",
                successful=lambda: True,
                sync_wait=lambda **kwargs: None,
            )

        def wait():
            if state.lose_receipt:
                state.lose_receipt = False
                raise TimeoutError("response lost after creation")
            return state.operation

        return NS(wait=wait)

    monkeypatch.setattr(vpc, "RouteServiceClient", lambda _sdk: NS(create=create))
    plan = {
        "project": "project",
        "gateway": "gateway",
        "policy": {},
        "infrastructure": {},
        "allocations": {},
        "active_tables": ["table"],
        "observed": {},
        "ownership": {},
        "conversions": {},
        "released": [],
        "options": {"force_copy": False},
        "requests": {"create": request_record(request)},
    }
    return journal, request, plan, state, create


def test_names_labels_and_matching_destinations_never_adopt_external_routes(route_case):
    journal, _request, _plan, state, _create = route_case
    assert not owned(state.route, {}, project="project", gateway="gateway")
    assert read_ownership("project", "gateway", root=journal.root) == {}
    assert not journal.root.exists()


def test_successful_creation_and_exact_readback_are_both_required(route_case):
    journal, request, plan, state, create = route_case
    with journal:
        journal.begin(plan, approval="a" * 64)
        cloud = Cloud(object(), journal)
        with pytest.raises(RuntimeError, match="receipt"):
            journal.created(cloud, "create", state.route, index=0)
        cloud.mutate("create", request, create)
        foreign = clone_nebius_sdk_message(state.route)
        foreign.spec.description = "changed independently"
        with pytest.raises(RuntimeError, match="request"):
            journal.created(cloud, "create", foreign, index=0)
        assert journal.state["bindings"] == {}
        journal.created(cloud, "create", state.route, index=0)
        before = journal.path.read_bytes()
        journal.created(cloud, "create", state.route, index=0)
        assert journal.path.read_bytes() == before
    assert owned(state.route, journal.inventory(), project="project", gateway="gateway")
    assert state.effects == 1
    assert journal.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("point", ["accepted-response", "before-ledger"])
def test_interruption_settles_original_create_before_recording_ownership(route_case, point):
    journal, request, plan, state, create = route_case
    with journal:
        journal.begin(plan, approval="a" * 64)
        state.lose_receipt = point == "accepted-response"
        cloud = Cloud(object(), journal)
        if state.lose_receipt:
            with pytest.raises(TimeoutError):
                cloud.mutate("create", request, create)
        else:
            cloud.mutate("create", request, create)
    assert journal.inventory() == {}
    with journal:
        journal.begin(plan, approval="a" * 64)
        cloud = Cloud(object(), journal)
        cloud.settle()
        journal.created(cloud, "create", state.route, index=0)
    assert state.effects == 1
    assert len(set(state.keys)) == 1
    assert owned(state.route, journal.inventory(), project="project", gateway="gateway")


@pytest.mark.parametrize("change", ["id", "table", "labels", "target", "name"])
def test_changed_route_cannot_reuse_ledger_authority(route_case, change):
    journal, request, plan, state, create = route_case
    with journal:
        journal.begin(plan, approval="a" * 64)
        cloud = Cloud(object(), journal)
        cloud.mutate("create", request, create)
        journal.created(cloud, "create", state.route, index=0)
    other = clone_nebius_sdk_message(state.route)
    if change == "id":
        other.metadata.id = "other"
        assert not owned(other, journal.inventory(), project="project", gateway="gateway")
        return
    if change == "table":
        other.metadata.parent_id = "another-table"
    elif change == "labels":
        other.metadata.labels = {}
    elif change == "target":
        other.spec.next_hop.allocation.id = "another-target"
    else:
        other.metadata.name = "renamed"
    with pytest.raises(RuntimeError, match="authority"):
        owned(other, journal.inventory(), project="project", gateway="gateway")


def test_dry_noop_creates_no_route_journal(route_case):
    journal, _request, _plan, _state, _create = route_case
    assert journal.snapshot() is None
    assert not journal.root.exists()


def test_complete_transaction_requires_each_exact_settled_operation_receipt(route_case):
    from copy import deepcopy

    journal, _request, plan, _state, _create = route_case
    with journal:
        journal.begin(plan, approval="a" * 64)
    forged = deepcopy(journal.snapshot())
    forged["transaction"]["phase"] = "complete"
    forged["transaction"]["verified"] = list(plan["requests"])
    assert forged["transaction"]["operations"] == {}
    with pytest.raises(ValueError, match="invalid"):
        journal.validate(forged)
