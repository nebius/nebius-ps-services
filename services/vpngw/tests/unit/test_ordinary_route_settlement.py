from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from tests.unit import test_ordinary_route_plan as route_tests

from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import ordinary_replan
from nebius_vpngw.deploy.apply_command import CommandJournal, ReplanReady
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
from nebius_vpngw.deploy.ordinary_route_execute import execute
from nebius_vpngw.deploy.ordinary_route_plan import intent

world = route_tests.world


@pytest.fixture
def interrupted(world, tmp_path, monkeypatch):
    state, routes, _, route_plan, observe = world
    monkeypatch.setenv("HOME", str(tmp_path))
    command = CommandJournal("project", "gateway", root=tmp_path / "command")
    approved = route_plan(("192.168.1.0/24", "192.168.2.0/24"))
    frozen = intent(approved)
    effect = Effect("routes", "ordinary-routes", "apply", ("node",), digest(frozen), "a" * 64, True)
    plan = CommandPlan("project", "gateway", "b" * 64, "c" * 64, (effect,))
    with command:
        command.begin(plan, intents={"routes": frozen})
        command.started("routes")
    holder = digest([command.project, command.gateway, command.pending()["execution_id"]])
    authority = {
        "authority": {"writer": "workstation", "holder": holder, "binding": None},
        "journal": None,
    }
    from nebius_vpngw.deploy.ordinary_replan import transferred_snapshot

    def supersede(previous, next_holder, observed):
        assert previous == holder
        authority["authority"].update(
            holder=next_holder,
            receipt={
                "holder": next_holder,
                "request": digest(["supersede", previous, next_holder, digest(observed)]),
            },
        )
        return deepcopy(authority)

    handoff = NS(inspect=lambda: deepcopy(authority), supersede_reservation=supersede)
    monkeypatch.setattr(ordinary_replan, "Handoff", lambda *_: handoff)
    monkeypatch.setattr(ordinary_replan, "RouteJournal", lambda *_: routes)
    inputs = dict(
        ssh=object(),
        instances=(NS(hostname="node"),),
        local={"generation": 2},
        targets={"node": ("compute", "transport")},
        artifact="d" * 64,
        route_factory=lambda _: (object(), observe),
    )

    def start(boundary):
        save = routes.save
        fired = False

        def interrupted_save():
            nonlocal fired
            save()
            records = list(routes.state["transaction"]["operations"].values())
            if records and records[0]["status"] == boundary and not fired:
                fired = True
                raise OSError("injected route interruption")

        monkeypatch.setattr(routes, "save", interrupted_save)
        state.lost = boundary == "lost-response"
        with pytest.raises((OSError, TimeoutError), match="injected|lost"):
            execute(object(), routes, approved, approval=plan.approval, observe=observe)
        monkeypatch.setattr(routes, "save", save)
        return ordinary_replan.inspect(command, **inputs)

    return NS(
        state=state,
        routes=routes,
        command=command,
        inputs=inputs,
        start=start,
        authority=authority,
        expected_transfer=transferred_snapshot,
        plan=route_plan,
    )


@pytest.mark.parametrize("boundary", ["intent", "accepted", "settled", "lost-response"])
def test_reconcile_original_request_then_review_retirement_without_old_suffix(
    interrupted, boundary
):
    world = interrupted
    proposal = world.start(boundary)
    original = world.routes.path.read_bytes()
    parent = world.command.path.read_bytes()
    assert proposal.pending_routes is not None
    refreshed = ordinary_replan.inspect(world.command, **world.inputs)
    assert world.routes.path.read_bytes() == original
    assert world.command.path.read_bytes() == parent
    assert not proposal.pending_routes.path.exists()
    assert not (world.routes.root / "history").exists()
    with pytest.raises(ReplanReady):
        proposal.retire(refreshed)
    assert len(world.state.effects) == 1
    assert len(world.state.routes) == 1
    assert world.command.path.read_bytes() == parent
    assert world.routes.transaction()["phase"] == "pending"
    assert len(world.routes.transaction()["verified"]) == 1
    assert original in [p.read_bytes() for p in (world.routes.root / "history").glob("*.json")]
    settled = ordinary_replan.inspect(world.command, **world.inputs)
    assert settled.pending_routes is None
    assert settled.approval != proposal.approval
    settled.retire(ordinary_replan.inspect(world.command, **world.inputs))
    assert world.command.pending() is None
    assert world.routes.transaction() is None
    assert len(world.routes.inventory()) == 1
    successor = world.plan(("192.168.1.0/24", "192.168.3.0/24"))
    assert len(successor["requests"]) == 1
    assert all(
        value["request"]["spec"]["destination"]["cidr"] == "192.168.3.0/24"
        for value in successor["requests"].values()
    )


def test_lost_settlement_receipt_resumes_same_request_without_new_approval(
    interrupted, monkeypatch
):
    world = interrupted
    proposal = world.start("lost-response")
    save = world.routes.save
    fired = False

    def lost_reply():
        nonlocal fired
        save()
        if world.routes.transaction()["verified"] and not fired:
            fired = True
            raise OSError("lost settlement receipt")

    monkeypatch.setattr(world.routes, "save", lost_reply)
    with pytest.raises(OSError, match="lost settlement"):
        proposal.retire(ordinary_replan.inspect(world.command, **world.inputs))
    monkeypatch.setattr(world.routes, "save", save)
    resumed = ordinary_replan.inspect(world.command, **world.inputs)
    assert resumed.resumed
    assert resumed.approval == proposal.approval
    with pytest.raises(ReplanReady):
        resumed.retire(ordinary_replan.inspect(world.command, **world.inputs))
    assert len(world.state.effects) == 1
    assert len(world.routes.transaction()["operations"]) == 1


@pytest.mark.parametrize(
    "owner", ["credentials", "routes", "attachments", "ssh", "provisioning", "replacement"]
)
def test_route_settlement_excludes_every_local_owner(interrupted, owner):
    world = interrupted
    proposal = world.start("lost-response")
    held = proposal.owners[owner]
    competitor = type(held)(held.project, held.gateway, root=held.root)
    before = world.routes.path.read_bytes()
    with competitor, pytest.raises(RuntimeError, match="still running"):
        proposal.retire(ordinary_replan.inspect(world.command, **world.inputs))
    assert world.routes.path.read_bytes() == before
    assert not proposal.pending_routes.path.exists()


def test_route_settlement_requires_runtime_reservation_and_exact_cloud_readback(interrupted):
    world = interrupted
    proposal = world.start("accepted")
    before = world.routes.path.read_bytes()
    world.authority["authority"]["writer"] = "runtime"
    with pytest.raises(RuntimeError, match="durable-intent-changed"):
        ordinary_replan.inspect(world.command, **world.inputs)
    world.authority["authority"]["writer"] = "workstation"
    route = next(iter(world.state.routes.values()))
    route.spec.destination.cidr = "198.51.100.0/24"
    with pytest.raises(RuntimeError, match="outside|readback"):
        proposal.retire(ordinary_replan.inspect(world.command, **world.inputs))
    assert world.routes.path.read_bytes() == before
    assert not proposal.pending_routes.path.exists()


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_route_settlement_dry_run_and_decline_are_read_only(interrupted, monkeypatch, dry_run):
    import click

    from nebius_vpngw import cli
    from nebius_vpngw.deploy.ordinary_route_discovery import Discovery

    world = interrupted
    proposal = world.start("lost-response")
    before = world.routes.path.read_bytes()
    manager = NS(
        inspect_ordinary_targets=lambda _: world.inputs["targets"],
        project_id="project",
        auth_token=None,
        _get_client=lambda: object(),
    )
    monkeypatch.setattr(Discovery, "observe", lambda _: proposal.pending_routes.observe())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", lambda *_a, **_kw: False)
    with pytest.raises(click.exceptions.Exit) as stopped:
        cli._prepare_ordinary_apply(
            manager,
            NS(_ssh_policy=None),
            world.inputs["instances"],
            world.inputs["local"],
            supplied=None,
            dry_run=dry_run,
            recreate=False,
            command_journal=world.command,
            artifact_sha256=world.inputs["artifact"],
            deployment=NS(),
        )
    assert stopped.value.exit_code == 0
    assert world.routes.path.read_bytes() == before
    assert not proposal.pending_routes.path.exists()
    assert len(world.state.effects) == 1


@pytest.mark.parametrize("resumed", [False, True])
def test_settlement_refuses_changed_approval_record_after_preview(
    interrupted, monkeypatch, resumed
):
    import json

    from nebius_vpngw.deploy import ordinary_route_settlement
    from nebius_vpngw.deploy.recovery import ReplanRejected

    world = interrupted
    proposal = world.start("lost-response")
    if resumed:

        def interrupted_settlement(*_a, **_kw):
            raise OSError("injected before settlement")

        with monkeypatch.context() as patch:
            patch.setattr(ordinary_route_settlement, "settle_submitted", interrupted_settlement)
            with pytest.raises(OSError, match="injected"):
                proposal.retire(ordinary_replan.inspect(world.command, **world.inputs))
        proposal = ordinary_replan.inspect(world.command, **world.inputs)
        assert proposal.resumed
    fresh = ordinary_replan.inspect(world.command, **world.inputs)
    path = proposal.pending_routes.path
    path.parent.mkdir(mode=0o700, exist_ok=True)
    competing = json.dumps(
        {
            "schema": "nebius-vpngw.route-settlement.v1",
            "proof": {"another": "approval"},
            "original": proposal.pending_routes.original,
            "phase": "settling",
        },
        sort_keys=True,
    )
    path.write_text(competing)
    path.chmod(0o600)
    before = world.routes.path.read_bytes(), world.command.path.read_bytes()
    with pytest.raises(ReplanRejected, match="changed"):
        proposal.retire(fresh)
    assert path.read_text() == competing
    assert before == (world.routes.path.read_bytes(), world.command.path.read_bytes())
    assert len(world.state.effects) == 1


def test_settlement_completion_does_not_overwrite_a_changed_approval(interrupted, monkeypatch):
    from nebius_vpngw.deploy import ordinary_route_settlement

    world = interrupted
    proposal = world.start("accepted")
    path = proposal.pending_routes.path
    settle = ordinary_route_settlement.settle_submitted

    def change_after_settlement(*args, **kwargs):
        settle(*args, **kwargs)
        path.write_text('{"competing": "record"}')

    monkeypatch.setattr(ordinary_route_settlement, "settle_submitted", change_after_settlement)
    with pytest.raises((ValueError, RuntimeError), match="changed"):
        proposal.retire(ordinary_replan.inspect(world.command, **world.inputs))
    assert path.read_text() == '{"competing": "record"}'
    assert world.command.pending() is not None
    assert world.routes.transaction()["phase"] == "pending"
    assert len(world.state.effects) == 1
