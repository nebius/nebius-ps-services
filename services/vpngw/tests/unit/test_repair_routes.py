from copy import deepcopy
from types import SimpleNamespace

import pytest

from nebius_vpngw import repair_guest, repair_state
from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority
from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy.ordinary_route_ownership import RouteJournal


@pytest.fixture
def route_repair(tmp_path, monkeypatch):
    owner = RouteAuthority(tmp_path / "guest")
    owner.reserve("1" * 64, digest(owner.snapshot()))
    operation = "a" * 32
    state = {
        "id": operation,
        "phase": "installed",
        "intent": {
            "ha": False,
            "project": "project",
            "gateway": "gateway",
            "targets": [{"name": "gateway-0"}],
        },
    }
    monkeypatch.setattr(
        repair_state, "Journal", lambda _: SimpleNamespace(load=lambda: deepcopy(state))
    )
    calls = []
    monkeypatch.setattr(repair_guest, "verify_guard", lambda op: calls.append(("guard", op)))
    monkeypatch.setattr(repair_guest, "verify_quiescent", lambda: calls.append(("quiet",)))
    ledger = RouteJournal("project", "gateway", root=tmp_path / "local")
    with ledger:
        ledger.save()
    return owner, operation, state, ledger.snapshot(), calls


def test_repair_replaces_obsolete_holder_only_with_exact_cold_owner(route_repair):
    owner, operation, state, ledger, calls = route_repair
    expected = digest(owner.snapshot())
    with pytest.raises(RuntimeError, match="confirmed"):
        owner.repair_reserve("2" * 64, expected, "b" * 32, ledger)
    assert owner.read()["holder"] == "1" * 64 and calls == []
    owner.repair_reserve("2" * 64, expected, operation, ledger)
    assert owner.read()["holder"] == "2" * 64
    assert calls == [("guard", operation), ("quiet",)]
    with pytest.raises(RuntimeError, match="does not own"):
        owner.require_runtime({})
    state["phase"] = "originating"
    with pytest.raises(RuntimeError, match="confirmed"):
        owner.repair_reserve("3" * 64, digest(owner.snapshot()), operation, ledger)


def test_lost_repair_ledger_write_retains_reservation_and_resumes_same_request(
    route_repair, monkeypatch
):
    owner, operation, _, ledger, _ = route_repair
    expected = digest(owner.snapshot())
    save = RouteJournal.save
    monkeypatch.setattr(
        RouteJournal, "save", lambda _: (_ for _ in ()).throw(OSError("lost disk write"))
    )
    with pytest.raises(OSError):
        owner.repair_reserve("2" * 64, expected, operation, ledger)
    assert owner.read()["holder"] == "2" * 64
    monkeypatch.setattr(RouteJournal, "save", save)
    owner.repair_reserve("2" * 64, expected, operation, ledger)
    changed = {**ledger, "revision": ledger["revision"] + 1}
    with pytest.raises(RuntimeError, match="changed during"):
        owner.repair_reserve("2" * 64, expected, operation, changed)


@pytest.mark.parametrize("ha", [False, True])
def test_malformed_route_metadata_retires_without_adopting_routes(
    route_repair, monkeypatch, tmp_path, ha
):
    from nebius_vpngw import ordinary_route_binding
    from nebius_vpngw.deploy import repair_route_metadata as metadata

    owner, operation, state, ledger, _ = route_repair
    state["intent"]["ha"] = ha
    monkeypatch.setattr(repair_guest, "ROOT", tmp_path / "repair")
    monkeypatch.setattr(ordinary_route_binding, "read", lambda: None)
    owner.path.write_bytes(b"broken authority")
    journal = RouteJournal("project", "gateway", root=owner.root / "journal")
    repair_state.directory(journal.root)
    journal.path.write_bytes(b"broken route journal")
    journal.path.chmod(0o600)
    observed = owner.repair_metadata(operation)
    assert observed["metadata"]["states"] == []
    assert observed["binding"] is None
    request = {"observed": observed, "journal": ledger}
    owner.repair_metadata(operation, request)
    assert owner.read()["writer"] == "workstation"
    assert journal.snapshot()["bindings"] == {}
    assert metadata.observe(journal)["states"] == [ledger]
    # A lost reply resumes the identical reset; neither old holder nor corrupt
    # bytes become a prerequisite for reconstruction.
    owner.repair_metadata(operation, request)
    archive = repair_guest.ROOT / "archives" / operation / "route-metadata"
    assert (archive / "authority.json").read_bytes() == b"broken authority"
    assert (archive / "journal/active.json").read_bytes() == b"broken route journal"


def test_route_retirement_preserves_lock_inode_and_partial_publication(tmp_path):
    import os

    from nebius_vpngw.deploy import repair_route_metadata as metadata

    ledger = RouteJournal("project", "gateway", root=tmp_path / "routes")
    with ledger:
        ledger.save()
    desired = ledger.snapshot()
    lock = ledger.root / "operation.lock"
    inode = lock.stat().st_ino
    ledger.path.write_bytes(b"invalid partial publication")
    candidate = metadata.paths(ledger)["candidate"]
    repair_state.directory(candidate.parent)
    os.link(ledger.path, candidate)
    observed = metadata.observe(ledger)
    metadata.retire(ledger, tmp_path / "archive", observed["fingerprints"], desired)
    assert lock.stat().st_ino == inode
    assert ledger.snapshot() == desired
    assert not candidate.exists()
    assert (tmp_path / "archive/active.json").read_bytes() == b"invalid partial publication"
    assert (tmp_path / "archive/candidate.json").read_bytes() == b"invalid partial publication"


@pytest.mark.parametrize("blocker", ["operation", "phase", "guard", "writer"])
def test_ha_route_metadata_retirement_requires_exact_quiescent_repair(
    route_repair, monkeypatch, blocker
):
    owner, operation, state, ledger, _ = route_repair
    state["intent"]["ha"] = True
    before = owner.path.read_bytes()
    if blocker == "operation":
        operation = "b" * 32
    elif blocker == "phase":
        state["phase"] = "originating"
    else:

        def refuse(*_args):
            raise RuntimeError("repair admission blocked")

        monkeypatch.setattr(
            repair_guest, "verify_guard" if blocker == "guard" else "verify_quiescent", refuse
        )
    with pytest.raises(RuntimeError, match="confirmed|blocked"):
        owner.repair_metadata(operation)
    assert owner.path.read_bytes() == before
    # HA metadata settlement never grants an ordinary runtime reservation.
    with pytest.raises(RuntimeError, match="ordinary reconstruction"):
        owner.repair_reserve("2" * 64, digest(owner.snapshot()), state["id"], ledger)


def test_route_retirement_does_not_ignore_another_writer(tmp_path):
    from nebius_vpngw.deploy import repair_route_metadata as metadata

    ledger = RouteJournal("project", "gateway", root=tmp_path / "routes")
    with ledger:
        ledger.save()
        observed, desired = metadata.observe(ledger), deepcopy(ledger.state)
        with pytest.raises(RuntimeError, match="writer is still settling"):
            metadata.retire(ledger, tmp_path / "archive", observed["fingerprints"], desired)


@pytest.mark.parametrize("gateway_route", [False, True])
def test_repair_settles_copied_routes_without_claiming_gateway_ownership(
    tmp_path, monkeypatch, gateway_route
):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc import v1 as vpc

    from nebius_vpngw.deploy import repair_routes
    from nebius_vpngw.deploy.ordinary_route_ownership import labels, owned
    from nebius_vpngw.deploy.ordinary_route_plan import request_record
    from nebius_vpngw.deploy.replacement_cloud import Cloud, message_digest
    from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message

    journal = RouteJournal("project", "gateway", root=tmp_path / "routes")
    destination = "192.168.0.0/24" if gateway_route else "0.0.0.0/0"
    request = vpc.CreateRouteRequest(
        metadata=ResourceMetadata(
            parent_id="table",
            name="retained-route",
            labels=labels("project", "gateway", "table", destination, 0) if gateway_route else {},
        ),
        spec=vpc.RouteSpec(
            destination=vpc.DestinationMatch(cidr=destination),
            next_hop=vpc.NextHop(allocation=vpc.AllocationNextHop(id="allocation")),
        ),
    )
    route = vpc.Route(
        metadata=ResourceMetadata(
            id="route",
            parent_id="table",
            name="retained-route",
            resource_version=1,
            labels=dict(request.metadata.labels),
        ),
        spec=clone_nebius_sdk_message(request.spec),
    )
    wanted = request_record(request, **({"instance_index": 0} if gateway_route else {}))
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
        "requests": {"create": wanted},
    }
    creates = []

    def create(current, **kwargs):
        creates.append(current)
        return SimpleNamespace(
            wait=lambda: SimpleNamespace(
                id="operation",
                resource_id="route",
                successful=lambda: True,
                sync_wait=lambda **kwargs: None,
            )
        )

    def get(current, **kwargs):
        assert current.id == "route"
        return SimpleNamespace(wait=lambda: route)

    monkeypatch.setattr(vpc, "RouteServiceClient", lambda _sdk: SimpleNamespace(get=get))
    with journal:
        journal.begin(plan, approval="a" * 64)
        Cloud(object(), journal).mutate("create", request, create)
    original = clone_nebius_sdk_message(route)
    result = repair_routes.settle(journal, object())
    # Retrying a settled ledger must preserve the same ownership without
    # constructing an operation owner for a nonexistent transaction.
    repeated = repair_routes.settle(journal, object())
    assert repeated["bindings"] == result["bindings"]
    assert result["transaction"] is None
    assert len(creates) == 1
    assert message_digest(route) == message_digest(original)
    assert set(result["bindings"]) == ({"route"} if gateway_route else set())
    assert owned(route, result["bindings"], project="project", gateway="gateway") is gateway_route


def test_repair_accepts_empty_route_ledger(tmp_path):
    from nebius_vpngw.deploy import repair_routes

    journal = RouteJournal("project", "gateway", root=tmp_path / "routes")
    result = repair_routes.settle(journal, object())
    assert result["transaction"] is None
    assert result["bindings"] == {}
