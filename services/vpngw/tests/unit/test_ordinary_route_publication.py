from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from tests.unit.test_ordinary_route_plan import world  # noqa: F401
from tests.unit.test_ordinary_route_runtime import bound

from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority
from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import ordinary_route_publication as publication
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.ordinary_command import inspect as inspect_command
from nebius_vpngw.deploy.ordinary_route_command import PreparedRoutes
from nebius_vpngw.deploy.ordinary_route_discovery import Discovery
from nebius_vpngw.deploy.ordinary_route_plan import intent


@pytest.fixture
def scenario(request, tmp_path, monkeypatch):
    import hashlib
    import json

    cloud, route_journal, policy, plan, observation = request.getfixturevalue("world")
    variant = getattr(request, "param", "routes")
    command = CommandJournal("project", "gateway", root=tmp_path / "command")
    owner = RouteAuthority(tmp_path / "guest")
    instance = NS(hostname="gateway-0", config_yaml="gateway: {}\n")
    value = bound()
    value["generation"] = hashlib.sha256(instance.config_yaml.encode()).hexdigest()
    value["credentials"]["path"] = publication.binding.credential_path(
        generation=value["generation"], node=instance.hostname, sha256="e" * 64
    )
    value["policy"] = json.loads(json.dumps(policy.mapping()))
    value["infrastructure"]["excluded_prefixes"] = list(policy.excluded_prefixes)
    publication.binding.validate(value)
    if variant == "retire":
        owner.reserve("9" * 64, digest(owner.snapshot()))
        owner.release("9" * 64, value, None)

    def current_observation(*a, **k):
        return {
            **observation(),
            "infrastructure": value["infrastructure"],
            "policy": policy,
            "active_tables": ("table",),
        }

    monkeypatch.setattr(Discovery, "observe", current_observation)
    learned = ("192.168.1.0/24",) if variant == "routes" else ()
    monkeypatch.setattr(Discovery, "learned", lambda *a: {"peer": learned})
    monkeypatch.setattr(publication, "RouteJournal", lambda *a: route_journal)
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ordinary_route_ownership.RouteJournal", lambda *a: route_journal
    )
    monkeypatch.setattr(publication.binding, "build", lambda **kw: deepcopy(value))
    events = []

    class Transport:
        lose_reply = False

        def inspect(self):
            return owner.snapshot()

        def reserve(self, holder, observed):
            events.append("reserve")
            return owner.reserve(holder, digest(observed))

        def install(self, holder, binding, identity):
            assert owner.read()["holder"] == holder
            events.append("install")

        def release(self, holder, binding, journal):
            events.append("release")
            result = owner.release(holder, binding, journal)
            if self.lose_reply:
                self.lose_reply = False
                raise OSError("lost terminal SSH reply")
            return result

        def retire(self, holder, generation, journal):
            events.append("retire")
            result = owner.retire(holder, generation, journal)
            if self.lose_reply:
                self.lose_reply = False
                raise OSError("lost terminal SSH reply")
            return result

    transport = Transport()
    monkeypatch.setattr(publication, "Handoff", lambda *a: transport)
    credentials = NS(nodes=(NS(node_id=instance.hostname),))

    def enroll(parent, *args, **kwargs):
        events.append("credentials")
        parent.verified("credentials", {"exact": "credential-owner"})
        return NS(credentials=credentials)

    monkeypatch.setattr(publication.credential_enrollment, "execute", enroll)
    cp = NS(approval_record=lambda: {"action": "reuse"}, command_action="reuse")
    pub = publication.Publication(
        command,
        NS(),
        instance,
        {},
        "target",
        owner.snapshot(),
        owner.snapshot(),
        cp,
        runtime_enabled=variant != "retire",
    )
    manager = NS(_get_client=lambda: object())
    discovery = Discovery(
        manager, NS(), {}, route_journal, NS(_gateway_subnet_name=lambda local: "gateway-subnet")
    )
    if variant == "older":
        from nebius_vpngw.deploy.ordinary_route_execute import execute

        historical = plan()
        historical["infrastructure"] = value["infrastructure"]
        execute(object(), route_journal, historical, approval="d" * 64, observe=current_observation)
    selected = plan(learned=learned)
    selected["infrastructure"] = value["infrastructure"]
    routes = PreparedRoutes(
        discovery,
        intent(selected),
        selected,
        bool(selected["requests"]),
        None,
        route_journal.transaction(),
        False,
        pub,
    )
    composed = inspect_command(
        command,
        local={},
        targets={instance.hostname: ("compute", "target")},
        plans={},
        attachments=None,
        attachment_pending=None,
        artifact_sha256="f" * 64,
        routes=routes,
        publication=pub,
    )
    return NS(
        cloud=cloud,
        routes=routes,
        composed=composed,
        command=command,
        owner=owner,
        pub=pub,
        events=events,
        transport=transport,
        route_journal=route_journal,
    )


def test_first_publication_reserves_before_cloud_writes_and_releases_verified_journal(scenario):
    s = scenario
    with s.command:
        s.composed.begin()
        assert s.cloud.effects == [] and s.owner.read() is None
        s.command.started("routes")
        receipt = s.routes.finish(s.composed.approval)
        assert s.owner.read()["writer"] == "workstation"
        assert len(s.cloud.effects) == 1
        s.command.verified("routes", receipt)
        s.pub.finish(s.routes)
        s.command.complete()
    assert s.events == ["credentials", "reserve", "install", "release"]
    assert s.owner.read()["writer"] == "runtime"
    assert s.owner.snapshot()["journal"]["bindings"] == s.route_journal.inventory()


@pytest.mark.parametrize("change", ["noop", "pending", "route", "authority"])
def test_readback_tolerates_only_completed_same_inventory_churn(scenario, change):
    from nebius_vpngw.deploy.ordinary_runtime_handoff import same_completed_routes

    s = scenario
    with s.command:
        s.composed.begin()
        s.command.started("routes")
        s.command.verified("routes", s.routes.finish(s.composed.approval))
        s.pub.finish(s.routes)
        s.command.complete()
    before = s.owner.snapshot()
    after = deepcopy(before)
    after["journal"]["revision"] += 1
    if change == "pending":
        after["journal"]["transaction"]["phase"] = "pending"
    elif change == "route":
        receipt = next(iter(after["journal"]["bindings"].values()))
        receipt["snapshot"]["allocation_id"] = "different-allocation"
    elif change == "authority":
        after["authority"]["writer"] = "workstation"
    assert same_completed_routes(before, after) is (change == "noop")


def test_lost_release_reply_never_replays_finished_cloud_effects(scenario):
    s = scenario
    with s.command:
        s.composed.begin()
        s.command.started("routes")
        receipt = s.routes.finish(s.composed.approval)
        s.command.verified("routes", receipt)
        s.transport.lose_reply = True
        with pytest.raises(OSError, match="lost terminal"):
            s.pub.finish(s.routes)
        assert s.owner.read()["writer"] == "runtime"
        previous = deepcopy(s.cloud.effects)
        # A runtime journal revision after publication does not invalidate the
        # durable handoff receipt for the original workstation journal.
        guest_journal = s.owner.journal(s.owner.read()["binding"])
        with guest_journal:
            guest_journal.save()
        assert s.routes.finish(s.composed.approval) == receipt
        s.pub.finish(s.routes)
        s.command.complete()
    assert s.cloud.effects == previous
    assert s.events.count("release") == 1


def test_interruption_before_handoff_keeps_runtime_excluded(scenario):
    s = scenario
    with s.command:
        s.composed.begin()
        s.command.started("routes")
        s.routes.finish(s.composed.approval)
    restarted = RouteAuthority(s.owner.root)
    assert restarted.read()["writer"] == "workstation"
    with pytest.raises(RuntimeError, match="Another command"):
        restarted.reserve("2" * 64, digest(restarted.snapshot()))
    assert s.command.pending() is not None


def test_resumed_parent_accepts_release_receipt_without_reacquiring_runtime(scenario):
    s = scenario
    with s.command:
        s.composed.begin()
        s.command.started("routes")
        receipt = s.routes.finish(s.composed.approval)
        s.command.verified("routes", receipt)
        s.transport.lose_reply = True
        with pytest.raises(OSError):
            s.pub.finish(s.routes)
    restarted = publication.Publication(
        s.command,
        s.pub.ssh,
        s.pub.instance,
        s.pub.local,
        s.pub.target,
        s.owner.snapshot(),
        s.pub.predecessor,
        s.pub.credential_plan,
    )
    previous = s.route_journal.transaction()
    resumed_routes = PreparedRoutes(
        s.routes.discovery,
        previous["intent"],
        previous["plan"],
        False,
        None,
        previous,
        False,
        restarted,
    )
    resumed = inspect_command(
        s.command,
        local={},
        targets={"gateway-0": ("compute", "target")},
        plans={},
        attachments=None,
        attachment_pending=None,
        artifact_sha256="f" * 64,
        routes=resumed_routes,
        publication=restarted,
    )
    before = deepcopy(s.cloud.effects)
    with s.command:
        resumed.begin()
        assert resumed_routes.finish(resumed.approval) == receipt
        restarted.finish(resumed_routes)
        s.command.complete()
    assert s.cloud.effects == before
    assert s.events.count("reserve") == 1


@pytest.mark.parametrize("scenario", ["empty", "older"], indirect=True)
def test_publication_only_work_keeps_a_parent_bound_route_receipt(scenario):
    s = scenario
    assert s.routes.required
    before = deepcopy(s.cloud.effects)
    with s.command:
        s.composed.begin()
        s.command.started("routes")
        receipt = s.routes.finish(s.composed.approval)
        s.command.verified("routes", receipt)
        assert s.route_journal.transaction()["approval"] == s.composed.approval
        s.transport.lose_reply = True
        with pytest.raises(OSError):
            s.pub.finish(s.routes)
        assert s.routes.finish(s.composed.approval) == receipt
        s.pub.finish(s.routes)
        s.command.complete()
    assert s.cloud.effects == before


def test_changed_infrastructure_is_a_publication_effect_even_without_route_requests(scenario):
    s = scenario
    value = bound()
    s.pub.observed = {
        "authority": {"binding": value, "writer": "runtime", "receipt": None, "holder": None},
        "journal": None,
    }
    same = {key: deepcopy(value[key]) for key in ("policy", "infrastructure")}
    assert not s.pub.scope_changed(same)
    same["infrastructure"]["network_spec_sha256"] = "9" * 64
    assert s.pub.scope_changed(same)


@pytest.mark.parametrize("scenario", ["retire"], indirect=True)
def test_cardinality_retirement_resumes_lost_reply_without_restoring_runtime(scenario):
    s = scenario
    with s.command:
        s.composed.begin()
        assert s.owner.read()["writer"] == "workstation"
        s.command.started("routes")
        receipt = s.routes.finish(s.composed.approval)
        s.command.verified("routes", receipt)
        s.transport.lose_reply = True
        with pytest.raises(OSError, match="lost terminal"):
            s.pub.finish(s.routes)
        assert s.owner.read()["writer"] == "deployment"
        before = deepcopy(s.cloud.effects)
        assert s.routes.finish(s.composed.approval) == receipt
        s.pub.finish(s.routes)
        s.command.complete()
    assert s.cloud.effects == before
    assert s.events.count("retire") == 1
    assert "install" not in s.events and "release" not in s.events
    with pytest.raises(RuntimeError, match="does not own"):
        s.owner.require_runtime(s.owner.read()["binding"])


@pytest.mark.parametrize("writer", ["runtime", "deployment", "absent", "ambiguous"])
def test_multi_vm_inspection_selects_only_the_installed_writer(scenario, monkeypatch, writer):
    s = scenario
    first = NS(hostname="gateway-0", instance_index=0, config_yaml="connections: []\n")
    second = NS(hostname="gateway-1", instance_index=1, config_yaml="connections: []\n")
    value = bound()
    authority = {"writer": writer, "binding": value, "holder": None, "receipt": None}

    def handoff(ssh, target, instance, local):
        found = writer != "absent" and (instance.instance_index == 0 or writer == "ambiguous")
        return NS(inspect=lambda: {"authority": authority if found else None, "journal": None})

    monkeypatch.setattr(publication, "Handoff", handoff)
    monkeypatch.setattr(
        publication,
        "managed_vm_ha_credential_path",
        lambda **k: NS(exists=lambda: True, is_symlink=lambda: False),
    )
    monkeypatch.setattr(
        publication, "inspect_managed_vm_ha_credentials", lambda **k: s.pub.credential_plan
    )
    targets = {"gateway-0": ("compute0", "target0"), "gateway-1": ("compute1", "target1")}
    if writer == "ambiguous":
        with pytest.raises(RuntimeError, match="ambiguous"):
            publication.inspect(s.command, NS(), (first, second), {}, targets)
    else:
        result = publication.inspect(s.command, NS(), (first, second), {}, targets)
        if writer == "runtime":
            assert result.instance is first and not result.runtime_enabled
        else:
            assert result is None
