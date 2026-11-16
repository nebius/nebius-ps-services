from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from nebius_vpngw import apply_approval
from nebius_vpngw.deploy import ordinary_apply, ordinary_replan
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
from nebius_vpngw.deploy.ordinary_command import package_manifests


@pytest.fixture
def predecessor(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    journal = CommandJournal("project", "gateway", root=tmp_path / "command")
    targets = {"node": ("compute", "transport")}
    manifest = {
        "wheel_sha256": "a" * 64,
        "expected_dependencies": {"managed": "1", "unrelated": "2"},
    }
    effect = Effect(
        "guest:node",
        "ordinary-runtime",
        "apply",
        targets["node"],
        apply_approval.digest(manifest),
        "b" * 64,
        True,
    )
    plan = CommandPlan(
        "project",
        "gateway",
        apply_approval.digest({"desired": {"generation": 1}, "targets": targets}),
        "a" * 64,
        (effect,),
    )
    with journal:
        journal.begin(plan, intents={"guest:node": manifest})
    for name in ("EnrollmentJournal", "RouteJournal", "AttachmentJournal"):
        factory = getattr(ordinary_replan, name)
        monkeypatch.setattr(
            ordinary_replan,
            name,
            lambda p, g, factory=factory, name=name: factory(p, g, root=tmp_path / name),
        )
    observed = {
        "runtime_pending": None,
        "operation": None,
        "boot_id": "current",
        "config_sha256": "b" * 64,
    }
    monkeypatch.setattr(
        ordinary_apply, "remote", lambda *_a, **_kw: {"observation": deepcopy(observed)}
    )
    monkeypatch.setattr(
        ordinary_replan,
        "Handoff",
        lambda *_a: NS(inspect=lambda: {"authority": None, "journal": None}),
    )
    inputs = {
        "ssh": object(),
        "instances": (NS(hostname="node"),),
        "local": {"generation": 2},
        "targets": targets,
        "artifact": "c" * 64,
    }
    return journal, plan, inputs, observed


def test_unused_predecessor_is_archived_only_after_review_and_cannot_replay_old_steps(predecessor):
    journal, old_plan, inputs, _ = predecessor
    before = journal.path.read_bytes()
    proposal = ordinary_replan.inspect(journal, **inputs)
    assert journal.path.read_bytes() == before
    assert not (journal.root / "history").exists()
    assert proposal.approval != old_plan.approval
    proposal.retire(ordinary_replan.inspect(journal, **inputs))
    assert journal.pending() is None
    assert [p.read_bytes() for p in (journal.root / "history").glob("*.json")] == [before]
    assert (
        package_manifests(
            journal, local=inputs["local"], targets=inputs["targets"], artifact=inputs["artifact"]
        )
        == {}
    )
    assert len(list((journal.root / "settlements").glob("*.json"))) == 1


def test_conclusively_completed_guest_can_retire_a_partial_parent(predecessor):
    journal, plan, inputs, observed = predecessor
    with journal:
        journal.started("guest:node")
    observed["runtime_completion"] = {
        "operation": "d" * 32,
        "approval": apply_approval.bind(plan.approval, plan.mapping()),
        "manifest": plan.effects[0].request_sha256,
    }
    proposal = ordinary_replan.inspect(journal, **inputs)
    proposal.retire(ordinary_replan.inspect(journal, **inputs))
    assert journal.pending() is None


@pytest.mark.parametrize(
    "evidence",
    [
        {"operation": {"recoverable": False}},
        {"operation": {"recoverable": True}},
        {"runtime_pending": {"phase": "packages"}},
    ],
)
def test_active_or_uncertain_guest_refuses_without_any_retirement(predecessor, evidence):
    journal, _, inputs, observed = predecessor
    before = journal.path.read_bytes()
    observed.update(evidence)
    with pytest.raises(RuntimeError, match="package-parent-changed"):
        ordinary_replan.inspect(journal, **inputs)
    assert journal.path.read_bytes() == before
    assert not (journal.root / "history").exists()


def test_changed_vm_identity_or_changed_authority_invalidates_approval(predecessor):
    journal, _, inputs, observed = predecessor
    proposal = ordinary_replan.inspect(journal, **inputs)
    observed["boot_id"] = "new-boot"
    before = journal.path.read_bytes()
    with pytest.raises(RuntimeError, match="approval-state-changed"):
        proposal.retire(ordinary_replan.inspect(journal, **inputs))
    assert journal.path.read_bytes() == before
    with pytest.raises(RuntimeError, match="checkpoint-binding-changed"):
        ordinary_replan.inspect(
            journal, **{**inputs, "targets": {"node": ("other-vm", "transport")}}
        )


@pytest.mark.parametrize(
    "kind,child",
    [
        (
            "routes",
            {"phase": "pending", "approval": "a", "operations": {"uncertain": {}}, "verified": []},
        ),
        (
            "credentials",
            {
                "phase": "pending",
                "approval": "a",
                "execution_id": "b",
                "operations": {},
                "selected": "key-created",
                "receipt": None,
            },
        ),
        (
            "attachments",
            {
                "phase": "pending",
                "approval": "a",
                "operation_id": "b",
                "member": 0,
                "operations": {},
                "locks": {"node": "held"},
                "verified": [],
            },
        ),
    ],
)
def test_hidden_child_effects_cannot_be_retired_from_parent_progress(kind, child):
    with pytest.raises(RuntimeError, match="durable-intent-changed"):
        ordinary_replan.require_child_settled(
            kind, {"transaction": child}, {"approval": "a", "execution_id": "b"}
        )


def test_completed_guest_reservation_transfer_recovers_lost_reply(
    predecessor, monkeypatch, tmp_path
):
    from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority

    journal, _, inputs, _ = predecessor
    owner = RouteAuthority(tmp_path / "guest")
    previous = apply_approval.digest(
        [journal.project, journal.gateway, journal.pending()["execution_id"]]
    )
    owner.reserve(previous, apply_approval.digest(owner.snapshot()))
    calls = []

    def transfer(old, new, value):
        calls.append(new)
        result = owner.supersede_reservation(old, new, apply_approval.digest(value))
        if len(calls) == 1:
            raise TimeoutError("lost transfer reply")
        return result

    monkeypatch.setattr(
        ordinary_replan,
        "Handoff",
        lambda *_a: NS(inspect=owner.snapshot, supersede_reservation=transfer),
    )
    proposal = ordinary_replan.inspect(journal, **inputs)
    original = journal.path.read_bytes()
    with pytest.raises(TimeoutError):
        proposal.retire(ordinary_replan.inspect(journal, **inputs))
    assert journal.path.read_bytes() == original
    assert owner.read()["writer"] == "workstation"
    resumed = ordinary_replan.inspect(journal, **inputs)
    assert resumed.resumed
    assert resumed.approval == proposal.approval
    resumed.retire(ordinary_replan.inspect(journal, **inputs))
    assert journal.pending() is None
    assert owner.read()["holder"] == ordinary_replan.parked_holder(proposal.mapping())
    assert journal.retirement_completed(proposal.mapping())
    assert len(set(calls)) == 1


@pytest.mark.parametrize("dry_run", [False, True])
def test_dry_run_or_declined_settlement_does_not_archive_or_change_any_owner(
    predecessor, monkeypatch, dry_run
):
    import click

    from nebius_vpngw import cli

    journal, _, inputs, _ = predecessor
    manager = NS(inspect_ordinary_targets=lambda _: inputs["targets"])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", lambda *_a, **_kw: False)
    original = journal.path.read_bytes()
    with pytest.raises(click.exceptions.Exit) as stopped:
        cli._prepare_ordinary_apply(
            manager,
            inputs["ssh"],
            inputs["instances"],
            inputs["local"],
            supplied=None,
            dry_run=dry_run,
            recreate=False,
            command_journal=journal,
            artifact_sha256=inputs["artifact"],
        )
    assert stopped.value.exit_code == 0
    assert journal.path.read_bytes() == original
    assert journal.retirement() is None
    assert not (journal.root / "history").exists()


def test_route_transfer_refuses_changed_holder_and_unexplained_null_binding_ledger(tmp_path):
    from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority

    owner = RouteAuthority(tmp_path / "guest")
    owner.reserve("a" * 64, apply_approval.digest(owner.snapshot()))
    expected = apply_approval.digest(owner.snapshot())
    with pytest.raises(RuntimeError, match="predecessor changed"):
        owner.supersede_reservation("b" * 64, "c" * 64, expected)
    (owner.root / "journal").mkdir()
    (owner.root / "journal" / "recovery.json").write_text("unexplained")
    with pytest.raises(RuntimeError, match="Unbound route ledger"):
        owner.supersede_reservation("a" * 64, "c" * 64, expected)
    assert owner.read()["holder"] == "a" * 64


def test_absent_owner_is_locked_before_parent_retirement(predecessor):
    journal, _, inputs, _ = predecessor
    proposal = ordinary_replan.inspect(journal, **inputs)
    fresh = ordinary_replan.inspect(journal, **inputs)
    before = journal.path.read_bytes()
    with proposal.owners["credentials"], pytest.raises(RuntimeError):
        proposal.retire(fresh)
    assert journal.path.read_bytes() == before
    assert journal.retirement() is None


@pytest.mark.parametrize("name", ["credentials", "routes", "attachments", "ssh", "provisioning", "replacement"])
def test_every_participating_owner_is_excluded_before_archival(predecessor, name):
    journal, _, inputs, _ = predecessor
    proposal = ordinary_replan.inspect(journal, **inputs)
    before = journal.path.read_bytes()
    with proposal.owners[name], pytest.raises(RuntimeError, match="still running"):
        proposal.retire(ordinary_replan.inspect(journal, **inputs))
    assert journal.path.read_bytes() == before
    assert not (journal.root / "history").exists()
