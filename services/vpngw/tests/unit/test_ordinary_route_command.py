from types import SimpleNamespace as NS

import pytest
from test_ordinary_route_plan import world  # noqa: F401

from nebius_vpngw.deploy import ordinary_route_command as command
from nebius_vpngw.deploy.ordinary_route_plan import intent
from nebius_vpngw.deploy.route_manager import RouteManagementError


@pytest.fixture
def composed(request, monkeypatch):
    state, journal, policy, _plan, read = request.getfixturevalue("world")
    learned = {"peer": ("192.168.1.0/24",)}

    def observe():
        return {**read(), "policy": policy, "active_tables": ("table",), "conversions": []}

    def learn(_policy):
        if learned.get("fail"):
            raise RouteManagementError("incomplete observation")
        return learned

    discovery = NS(journal=journal, observe=observe, learned=learn, force_copy=False)
    manager = NS(project_id="project", auth_token=None, _get_client=lambda: object())
    discovery.manager = manager
    monkeypatch.setattr(command, "Discovery", lambda *args, **kwargs: discovery)
    deployment = NS(vm_ha=None, gateway_group=NS(name="gateway"))

    def prepare(runtime=False):
        return command.prepare(
            manager, deployment, {}, ssh_policy=None, runtime_changes=runtime, journal=journal
        )

    return state, journal, learned, prepare


def test_bgp_policy_is_persisted_before_runtime_then_selects_fresh_destinations(composed):
    state, journal, _learned, prepare = composed
    prepared = prepare(runtime=True)
    assert prepared.required
    assert not prepared.preview["requests"]
    prepared.begin("a" * 64)
    assert journal.transaction()["plan"] is None
    assert journal.transaction()["intent"] == prepared.intent
    receipt = prepared.finish("a" * 64)
    assert receipt["connectivity"] == "observed"
    assert len(state.effects) == 1
    assert intent(journal.transaction()["plan"]) == prepared.intent
    assert journal.transaction()["phase"] == "complete"
    before = journal.path.read_bytes()
    unchanged = prepare()
    assert not unchanged.required
    unchanged.finish("b" * 64)
    assert journal.path.read_bytes() == before


def test_lost_reply_resumes_original_selection_not_newly_learned_destinations(composed):
    state, journal, learned, prepare = composed
    prepared = prepare(runtime=True)
    prepared.begin("a" * 64)
    state.lost = True
    with pytest.raises(TimeoutError):
        prepared.finish("a" * 64)
    learned["peer"] = ("192.168.1.0/24", "192.168.2.0/24")
    resumed = prepare()
    assert resumed.intent == prepared.intent
    resumed.finish("a" * 64)
    assert len(state.effects) == 1
    assert {route.spec.destination.cidr for route in state.routes.values()} == {"192.168.1.0/24"}
    assert prepare().required  # The newly learned prefix belongs to a new command.


def test_unknown_bgp_evidence_has_no_route_effects_or_journal(composed):
    state, journal, learned, prepare = composed
    learned["fail"] = True
    prepared = prepare()
    assert prepared.connectivity_unknown
    assert not prepared.required
    prepared.finish("a" * 64)
    assert not journal.root.exists()
    assert state.effects == []


def test_pending_routes_reject_another_commands_approval(composed):
    state, journal, _learned, prepare = composed
    prepared = prepare(runtime=True)
    prepared.begin("a" * 64)
    with pytest.raises(RuntimeError, match="not bound"):
        prepared.finish("b" * 64)
    assert state.effects == []
    assert journal.transaction()["approval"] == "a" * 64


@pytest.mark.parametrize("whitelist", [True, False])
def test_review_exposes_deferred_bgp_authority_without_resolving_routes(composed, whitelist):
    state, journal, _learned, prepare = composed
    prepared = prepare(runtime=True)
    if not whitelist:
        prepared.intent["policy"]["connections"][0]["prefixes"] = ()
    original_digest = prepared.request_sha256
    text = "\n".join(prepared.review_lines())
    assert "BGP peers 169.254.1.2" in text
    assert "maximum 10 prefixes" in text
    assert (
        "subprefixes of 192.168.0.0/16"
        if whitelist
        else "all eligible prefixes learned from these configured peers"
    ) in text
    assert "exclude local/VPC destinations: 10.0.0.0/16" in text
    assert "target route tables: table" in text
    assert prepared.request_sha256 == original_digest
    assert not prepared.preview["requests"]
    assert not journal.root.exists()
    assert state.effects == []
