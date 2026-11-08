from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import grpc
import pytest
from nebius.aio.service_error import RequestError, RequestStatusExtended
from nebius.api.nebius.common import v1 as common
from nebius.api.nebius.vpc import v1 as vpc
from test_ordinary_route_plan import world  # noqa: F401

from nebius_vpngw.deploy import replacement_routes as routes
from nebius_vpngw.deploy.ordinary_route_execute import execute
from nebius_vpngw.deploy.replacement_cloud import Cloud
from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message as clone


@pytest.fixture
def replacing(request, monkeypatch):
    state, ledger, _policy, plan, observe = request.getfixturevalue("world")
    execute(object(), ledger, plan(), approval="a" * 64, observe=observe)
    service = vpc.RouteServiceClient(object())

    def get(request, **kwargs):
        if request.id not in state.routes:
            raise RequestError(
                RequestStatusExtended(grpc.StatusCode.NOT_FOUND, "not found", [], "", "", [])
            )
        return NS(wait=lambda: clone(state.routes[request.id]))

    service.get = get
    service.list = lambda request, **kwargs: NS(
        wait=lambda: NS(items=tuple(state.routes.values()), next_page_token="")
    )
    monkeypatch.setattr(vpc, "RouteServiceClient", lambda sdk: service)
    monkeypatch.setattr(
        vpc,
        "RouteTableServiceClient",
        lambda sdk: NS(
            list=lambda request, **kwargs: NS(
                wait=lambda: NS(
                    items=[
                        vpc.RouteTable(
                            metadata=common.ResourceMetadata(id="table", parent_id="project")
                        )
                    ],
                    next_page_token="",
                )
            )
        ),
    )
    monkeypatch.setattr(routes, "RouteJournal", lambda *_args: ledger)
    replacement = NS(
        project="project",
        gateway="gateway",
        state={"transaction": {"approval": "b" * 64, "member": 0, "operations": {}}},
        save=Mock(),
    )
    cloud = Cloud(object(), replacement)
    approved = routes.inventory(object(), "project", {"allocation"}, gateway="gateway")
    return state, ledger, cloud, approved


def test_replacement_uses_delete_create_and_transfers_exact_ownership(replacing):
    state, ledger, cloud, approved = replacing
    old = approved[0]["id"]
    routes.rebind(cloud, approved, "allocation", "candidate")
    assert old not in state.routes
    assert [kind for kind, _ in state.effects] == [
        "CreateRouteRequest",
        "DeleteRouteRequest",
        "CreateRouteRequest",
    ]
    owned = ledger.inventory()
    assert set(owned) == set(state.routes)
    assert next(iter(owned.values()))["snapshot"]["allocation_id"] == "candidate"
    before = ledger.path.read_bytes()
    routes.rebind(cloud, approved, "allocation", "candidate", verify_only=True)
    assert ledger.path.read_bytes() == before
    assert len(state.effects) == 3


def test_replacement_inventory_preserves_unrelated_default_egress(replacing):
    state, ledger, cloud, approved = replacing
    state.routes["default-egress"] = vpc.Route(
        metadata=common.ResourceMetadata(
            id="default-egress", parent_id="table", resource_version=1
        ),
        spec=vpc.RouteSpec(
            destination=vpc.DestinationMatch(cidr="0.0.0.0/0"),
            next_hop=vpc.NextHop(default_egress_gateway=True),
        ),
    )
    assert routes.inventory(object(), "project", {"allocation"}, gateway="gateway") == approved
    assert "default-egress" not in ledger.inventory()


@pytest.mark.parametrize("point", ["delete", "create", "ledger"])
def test_replacement_interruption_keeps_exact_cloud_receipts_and_resumes(
    replacing, monkeypatch, point
):
    state, ledger, cloud, approved = replacing
    if point == "delete":
        state.lost = True
    elif point == "create":
        submit = vpc.RouteServiceClient(cloud.sdk).create

        def lose_create(request, **kwargs):
            state.lost = True
            return submit(request, **kwargs)

        vpc.RouteServiceClient(cloud.sdk).create = lose_create
    else:
        original = ledger.replaced
        monkeypatch.setattr(ledger, "replaced", Mock(side_effect=OSError("ledger interrupted")))
    with pytest.raises((TimeoutError, OSError)):
        routes.rebind(cloud, approved, "allocation", "candidate")
    if point == "create":
        vpc.RouteServiceClient(cloud.sdk).create = submit
    elif point == "ledger":
        monkeypatch.setattr(ledger, "replaced", original)
    routes.rebind(cloud, approved, "allocation", "candidate")
    assert len(state.effects) == 3
    assert next(iter(ledger.inventory().values()))["snapshot"]["allocation_id"] == "candidate"


def test_matching_legacy_route_name_never_grants_replacement_authority(replacing):
    state, _ledger, _cloud, _approved = replacing
    route = clone(next(iter(state.routes.values())))
    route.metadata.id = "unowned"
    route.metadata.name = "vpngw-192.168.1.0-24"
    state.routes = {"unowned": route}
    before = deepcopy(state.effects)
    with pytest.raises(RuntimeError, match="externally owned"):
        routes.inventory(object(), "project", {"allocation"}, gateway="gateway")
    assert state.effects == before


def test_delete_replay_rechecks_exact_original_object_before_resubmission(replacing):
    state, ledger, cloud, approved = replacing
    old = clone(next(iter(state.routes.values())))
    state.lost = True
    with pytest.raises(TimeoutError):
        routes.rebind(cloud, approved, "allocation", "candidate")
    old.spec.description = "concurrently changed"
    state.routes[old.metadata.id] = old
    before = deepcopy(state.effects)
    with pytest.raises(RuntimeError, match="deletion replay"):
        routes.admit_replay(cloud, approved)
    assert state.effects == before
    assert ledger.inventory()[old.metadata.id] == approved[0]["ownership"]
