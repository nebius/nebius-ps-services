from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from tests.unit.test_ordinary_credential_recovery import recovery  # noqa: F401
from tests.unit.test_replacement import plan as replacement_plan

from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority
from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import apply_command, credential_enrollment
from nebius_vpngw.deploy import replacement_runtime_routes as runtime
from nebius_vpngw.deploy.ordinary_route_ownership import RouteJournal
from nebius_vpngw.deploy.replacement import Journal
from nebius_vpngw.vm_ha_managed_credentials import OrdinaryCredentialRecovery


@pytest.fixture
def transfer(recovery, tmp_path, monkeypatch):  # noqa: F811
    s = recovery
    original = s.value
    old = original["infrastructure"]["gateways"]["0"]
    source = RouteAuthority(tmp_path / "source")
    source.reserve("1" * 64, digest(source.snapshot()))
    source.release("1" * 64, original, None)
    with source.journal(original) as published:
        published.save()
    member = replace(
        replacement_plan().members[0],
        logical_name=original["node"],
        compute_id=old["compute_id"],
        primary_allocations=(old["allocation_id"],),
        configured_policy_sha256=original["generation"],
    )
    desired = replace(
        replacement_plan(),
        project_id=original["project"],
        gateway_name=original["gateway"],
        members=(member,),
        route_runtime={
            "predecessor": source.snapshot(),
            "credentials": s.inspect().approval_record(),
            "intent": {key: value for key, value in original.items() if key != "credentials"},
            "journal": source.snapshot()["journal"],
        },
    )
    journal = Journal(desired.project_id, desired.gateway_name, root=tmp_path / "replacement")
    ledger = RouteJournal(desired.project_id, desired.gateway_name, root=tmp_path / "routes")
    candidate = RouteAuthority(tmp_path / "candidate")
    target = NS(hostname=original["node"], compute_id="compute-new", address="192.0.2.2")
    installed = deepcopy(original)
    installed["infrastructure"]["gateways"]["0"].update(
        compute_id=target.compute_id, allocation_id="new-allocation"
    )

    class Link:
        def __init__(self, store):
            self.store, self.lost, self.calls = store, False, []

        def inspect(self):
            return self.store.snapshot()

        def reserve(self, holder, before):
            self.calls.append("reserve")
            return self.store.reserve(holder, digest(before))

        def install(self, holder, value, identity):
            assert self.store.read()["holder"] == holder
            self.calls.append("install")

        def release(self, holder, value, state):
            self.calls.append("release")
            result = self.store.release(holder, value, state)
            if self.lost:
                self.lost = False
                raise TimeoutError("release reply lost")
            return result

        def read_credential(self, value):
            self.calls.append(("credential-read", value))
            return s.payload

    source_link, candidate_link = Link(source), Link(candidate)
    owner = NS(
        sdk=object(),
        manager=NS(project_id=desired.project_id),
        deployment=NS(gateway_group=NS(name=desired.gateway_name)),
        local={},
        member=lambda _: desired.mapping()["members"][0],
        route_credentials=NS(nodes=[object()]),
    )
    monkeypatch.setattr(
        runtime,
        "transport",
        lambda _owner, identifier, _logical: (
            source_link if identifier == old["compute_id"] else candidate_link
        ),
    )
    monkeypatch.setattr(runtime, "RouteJournal", lambda *a: ledger)
    monkeypatch.setattr(runtime, "candidate_binding", lambda *a: deepcopy(installed))
    observation = {
        "infrastructure": installed["infrastructure"],
        "policy": NS(mapping=lambda: installed["policy"]),
        "routes": {"table": ()},
    }
    monkeypatch.setattr(runtime, "discovery", lambda *a: NS(observe=lambda: deepcopy(observation)))
    with journal:
        journal.start(desired, digest(desired.mapping()))
    return NS(**locals())


def test_reservation_imports_guest_authority_before_any_cutover(transfer):
    s = transfer
    with s.journal:
        runtime.verify_source(s.owner, s.journal, step="prepare")
        runtime.reserve_source(s.owner, s.journal)
        runtime.verify_source(s.owner, s.journal, step="stop-old")
        assert runtime.RESERVED in s.journal.state["transaction"]["results"]
    assert s.source.read()["writer"] == "workstation"
    with pytest.raises(RuntimeError, match="does not own"):
        s.source.require_runtime(s.original)
    # A fresh process resumes the durable holder; time never restores runtime.
    before = s.source.path.read_bytes()
    with s.journal:
        runtime.reserve_source(s.owner, s.journal)
    assert s.source.path.read_bytes() == before


def test_multi_member_replacement_preserves_retired_runtime_without_reacquiring_it(transfer):
    s = transfer
    s.source.reserve("2" * 64, digest(s.source.snapshot()))
    s.source.retire("2" * 64, "d" * 64, s.source.snapshot()["journal"])
    other = replace(s.member, logical_name="gateway-1", compute_id="compute-other")
    before = s.source.path.read_bytes()
    assert runtime.inspect(s.owner, [s.member, other]) == (None, None)
    assert s.source.path.read_bytes() == before
    assert not s.source_link.calls and not s.candidate_link.calls


def initial_scope(s, tmp_path):
    """Older source has workstation receipts but no installed runtime owner."""
    s.source_link.store = RouteAuthority(tmp_path / "older-source")
    scope = deepcopy(s.desired.route_runtime)
    scope["predecessor"] = {"authority": None, "journal": None}
    scope["credentials"].pop("recovery")
    scope["credentials"]["action"] = "reuse"
    desired = replace(s.desired, route_runtime=scope)
    journal = Journal(desired.project_id, desired.gateway_name, root=tmp_path / "initial")
    with journal:
        journal.start(desired, digest(desired.mapping()))
    return journal, desired


def test_replacement_enrolls_first_runtime_without_starting_it_before_retirement(
    transfer, tmp_path, monkeypatch
):
    s = transfer
    journal, desired = initial_scope(s, tmp_path)
    runtime.validate(desired.route_runtime, desired)
    from nebius_vpngw.deploy import replacement_cloud

    cloud = NS(get_instance=lambda *a, **kw: object(), require_stopped=Mock())
    monkeypatch.setattr(replacement_cloud, "Cloud", lambda *a, **kw: cloud)
    with journal:
        runtime.verify_source(s.owner, journal, step="prepare")
        runtime.reserve_source(s.owner, journal)
        assert s.source_link.store.read() is None
        assert s.source_link.calls == []
        assert s.ledger.inventory() == desired.route_runtime["journal"]["bindings"]
        assert s.ledger.transaction() == desired.route_runtime["journal"]["transaction"]
        runtime.prepare_candidate(s.owner, journal, s.target)
        assert s.candidate.read()["writer"] == "workstation"
        runtime.before_routes(s.owner, journal)
        cloud.require_stopped.assert_called_once_with(desired.members[0].compute_id)
        runtime.finish(s.owner, journal, s.target)
        s.candidate.require_runtime(s.installed)


def test_unreviewed_runtime_on_older_source_blocks_replacement(transfer, tmp_path):
    s = transfer
    journal, _ = initial_scope(s, tmp_path)
    with journal:
        runtime.reserve_source(s.owner, journal)
        s.source_link.store.reserve("a" * 64, digest(s.source_link.store.snapshot()))
        with pytest.raises(RuntimeError, match="Unreviewed runtime writer"):
            runtime.verify_source(s.owner, journal, step="stop-old")


def test_initial_binding_plan_uses_existing_route_and_credential_discovery(
    transfer, tmp_path, monkeypatch
):
    s = transfer
    s.source_link.store = RouteAuthority(tmp_path / "unbound")
    s.owner.local = {"connections": [{"routing_mode": "bgp"}]}
    s.owner.manager.auth_token = None
    s.owner.ssh = NS(_ssh_policy=object())
    s.owner.instances = {s.original["node"]: NS(config_yaml="gateway: {}\n")}
    routes = NS(_gateway_subnet_name=lambda _: s.original["gateway_subnet_name"])
    monkeypatch.setattr(runtime, "RouteManager", lambda *a, **kw: routes)
    observed = {
        "infrastructure": s.original["infrastructure"],
        "policy": NS(mapping=lambda: s.original["policy"]),
    }
    monkeypatch.setattr(runtime, "Discovery", lambda *a: NS(observe=lambda: observed))
    credentials = deepcopy(s.desired.route_runtime["credentials"])
    credentials.pop("recovery")
    credentials["action"] = "reuse"
    enrollment = Mock(return_value=NS(approval_record=lambda: credentials))
    monkeypatch.setattr(runtime, "credential_plan", enrollment)
    scope, ledger = runtime.inspect(s.owner, list(s.desired.members))
    assert scope["predecessor"] == {"authority": None, "journal": None}
    assert scope["journal"] == ledger.snapshot()
    assert "credentials" not in scope["intent"]
    assert scope["credentials"] == credentials
    assert enrollment.call_args.args[2] is None


def test_foreign_writer_blocks_replacement_without_importing_routes(transfer):
    s = transfer
    s.source.reserve("f" * 64, digest(s.source.snapshot()))
    with s.journal, pytest.raises(RuntimeError, match="Another command"):
        runtime.reserve_source(s.owner, s.journal)
    assert not s.ledger.path.exists()


def test_route_replay_rechecks_stopped_source_and_candidate_reservation(
    transfer, monkeypatch, tmp_path
):
    s = transfer
    from nebius_vpngw.deploy import replacement_cloud

    cloud = NS(get_instance=lambda *a, **kw: object(), require_stopped=Mock())
    monkeypatch.setattr(replacement_cloud, "Cloud", lambda *a, **kw: cloud)
    with s.journal:
        runtime.reserve_source(s.owner, s.journal)
        runtime.prepare_candidate(s.owner, s.journal, s.target)
        portable = RouteJournal(
            s.journal.project, s.journal.gateway, root=tmp_path / "portable-routes"
        )
        monkeypatch.setattr(runtime, "RouteJournal", lambda *a: portable)
        runtime.before_routes(s.owner, s.journal)
        assert portable.inventory() == s.source.journal(s.original).inventory()
        assert portable.snapshot() is not None
        cloud.require_stopped.assert_called_once_with(s.desired.members[0].compute_id)
        cloud.require_stopped.side_effect = RuntimeError("source running")
        with pytest.raises(RuntimeError, match="source running"):
            runtime.before_routes(s.owner, s.journal)
        cloud.require_stopped.side_effect = None
        s.candidate.release(runtime.holder(s.journal), s.installed, portable.snapshot())
        with pytest.raises(RuntimeError, match="candidate route writer"):
            runtime.before_routes(s.owner, s.journal)


def test_candidate_stays_reserved_until_verified_finish_and_lost_reply_never_replays(transfer):
    s = transfer
    with s.journal:
        runtime.reserve_source(s.owner, s.journal)
        runtime.prepare_candidate(s.owner, s.journal, s.target)
        assert s.candidate.read()["writer"] == "workstation"
        assert not runtime.released(s.owner, s.journal, s.target)
        s.candidate_link.lost = True
        with pytest.raises(TimeoutError, match="reply lost"):
            runtime.finish(s.owner, s.journal, s.target)
    assert s.source.read()["writer"] == "workstation"
    s.candidate.require_runtime(s.installed)
    # Runtime advances after release. Parent must audit that current snapshot,
    # retaining its original release request instead of reinstalling old state.
    live = s.candidate.journal(s.installed)
    with live:
        live.save()
    before = s.candidate.path.read_bytes(), live.path.read_bytes()
    with s.journal:
        runtime.finish(s.owner, s.journal, s.target)
        assert runtime.released(s.owner, s.journal, s.target)
    assert (s.candidate.path.read_bytes(), live.path.read_bytes()) == before
    assert s.candidate_link.calls.count("release") == 1


def test_current_runtime_route_drift_blocks_terminal_acceptance(transfer, monkeypatch):
    s = transfer
    with s.journal:
        runtime.prepare_candidate(s.owner, s.journal, s.target)
        runtime.finish(s.owner, s.journal, s.target)
    s.observation["infrastructure"] = deepcopy(s.installed["infrastructure"])
    s.observation["infrastructure"]["network_id"] = "foreign"
    with s.journal, pytest.raises(RuntimeError, match="authority changed"):
        runtime.released(s.owner, s.journal, s.target)


def test_replacement_plan_binds_credential_prerequisite_and_same_portable_execution(
    transfer, tmp_path
):
    s = transfer
    command = apply_command.CommandJournal(
        s.desired.project_id, s.desired.gateway_name, root=tmp_path / "command"
    )
    with command:
        apply_command.begin_replacement(command, s.journal.snapshot()["transaction"])
        assert command.state["transaction"]["progress"] == {
            "credentials": "planned",
            "replacement": "planned",
        }
        with pytest.raises(ValueError, match="journal is invalid"):
            candidate = deepcopy(command.state)
            candidate["transaction"]["progress"]["replacement"] = "started"
            command.validate(candidate)
        execution = command.state["transaction"]["execution_id"]
    restored = apply_command.CommandJournal(
        s.desired.project_id, s.desired.gateway_name, root=tmp_path / "other-command"
    )
    with restored:
        apply_command.begin_replacement(restored, s.journal.snapshot()["transaction"])
        assert restored.state["transaction"]["execution_id"] == execution


def test_portable_enrollment_restores_exact_receipt_and_rejects_other_operation(
    transfer, tmp_path, monkeypatch
):
    s = transfer
    command = apply_command.CommandJournal(
        s.desired.project_id, s.desired.gateway_name, root=tmp_path / "command"
    )
    child = credential_enrollment.EnrollmentJournal(
        command.project, command.gateway, root=tmp_path / "enrollment"
    )
    monkeypatch.setattr(credential_enrollment, "EnrollmentJournal", lambda *a: child)
    monkeypatch.setattr(
        runtime,
        "credential_plan",
        lambda _o, original, reader, enrollment: s.s.inspect(
            source=OrdinaryCredentialRecovery(original, reader), enrollment=enrollment
        ),
    )
    execute = credential_enrollment.execute

    def ensure(observed, **kwargs):
        assert kwargs.pop("node_ids") == (s.original["node"],)
        assert kwargs.pop("tenant_id") is None
        assert kwargs.pop("region_id") is None
        return s.s.ensure(observed, **kwargs)

    monkeypatch.setattr(
        credential_enrollment, "execute", lambda *a, **kw: execute(*a, **kw, ensure=ensure)
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.replacement_cloud.Cloud",
        lambda *a, **kw: NS(get_instance=lambda *a, **kw: NS()),
    )
    with command, s.journal:
        apply_command.begin_replacement(command, s.journal.state["transaction"])
        runtime.enroll(s.owner, s.journal, command)
        assert command.state["transaction"]["progress"]["credentials"] == "verified"
    saved = s.journal.snapshot()["transaction"]["results"][runtime.ENROLLMENT]
    portable = type(child)(command.project, command.gateway, root=tmp_path / "portable")
    args = dict(
        intent=saved["transaction"]["intent"],
        approval=saved["transaction"]["approval"],
        execution_id=saved["transaction"]["execution_id"],
    )
    portable.restore_completed(saved, **args)
    before = portable.path.read_bytes()
    portable.restore_completed(saved, **args)
    assert portable.path.read_bytes() == before
    foreign = deepcopy(saved)
    foreign["transaction"]["execution_id"] = "e" * 32
    with pytest.raises(RuntimeError, match="replace another"):
        portable.restore_completed(foreign, **{**args, "execution_id": "e" * 32})


def test_serving_verification_uses_runtime_receipt_after_release(monkeypatch):
    from tests.unit.test_replacement_ordinary import journal

    from nebius_vpngw.deploy import replacement_ordinary as module

    owner = module.OrdinaryReplacement.__new__(module.OrdinaryReplacement)
    owner.sdk = object()
    owner.ssh = object()
    owner.local = {}
    owner.instances = {"gateway-0": object()}
    owner._verify_allocations = lambda *a: None
    owner._verify_access = lambda *a: (
        None,
        NS(hostname="gateway-0", address="192.0.2.1", compute_id="new"),
        {"context": {"boot": "boot"}},
    )
    prepared = NS(noop=True, observation={"verified": True}, close=lambda: None)
    monkeypatch.setattr(module.ordinary_apply, "inspect_plan", lambda *a, **kw: prepared)
    monkeypatch.setattr(
        module.ordinary_apply,
        "execute_plan",
        lambda *a, **kw: {"receipt": {"boot_id": "boot", "config_sha256": "a" * 64}},
    )
    monkeypatch.setattr(module, "Cloud", lambda *a, **kw: NS(get_instance=lambda _: object()))
    monkeypatch.setattr(
        module, "single_nic_status", lambda _: NS(ip_address=NS(allocation_id="new-allocation"))
    )
    monkeypatch.setattr(runtime, "released", lambda *a: True)
    rebind = Mock(side_effect=AssertionError("must not replay historical routes"))
    monkeypatch.setattr(module.replacement_routes, "rebind", rebind)
    owner._serving(journal())
    rebind.assert_not_called()
