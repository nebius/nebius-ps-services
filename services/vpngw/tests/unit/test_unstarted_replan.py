from types import SimpleNamespace as NS

import click
import pytest

from nebius_vpngw import cli
from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import ha_command
from nebius_vpngw.deploy import unstarted_replan as replan
from nebius_vpngw.deploy.apply_command import CommandJournal, ReplanReady
from nebius_vpngw.deploy.apply_diagnostics import PreparationBlocked
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
from nebius_vpngw.deploy.apply_result import ApplyReport
from nebius_vpngw.deploy.recovery import WriterBusy


@pytest.fixture(params=["ha", "provisioning"])
def unused(request, tmp_path, monkeypatch):
    journal = CommandJournal("project", "gateway", root=tmp_path / "parent")
    local = {"setting": "old"}
    if request.param == "ha":
        command = ha_command.create(
            journal,
            scope=digest({"desired": local, "artifact": "a" * 64, "project": "project"}),
            artifact="a" * 64,
            action_scope="b" * 64,
            targets=("gateway",),
            approval="c" * 64,
            runtime_scope="d" * 64,
            report={"kind": "provisioning"},
            attachment_plan=None,
        )
        with journal:
            command.begin()
    else:
        intent = {"approved": "unexecuted"}
        plan = CommandPlan(
            "project",
            "gateway",
            digest(local),
            "a" * 64,
            (
                Effect(
                    "provisioning",
                    "ordinary-provisioning",
                    "create",
                    ("gateway",),
                    digest(intent),
                    "b" * 64,
                    False,
                ),
            ),
        )
        with journal:
            journal.begin(plan, intents={"provisioning": intent})
    for name in (
        "EnrollmentJournal",
        "TrustJournal",
        "AttachmentJournal",
        "ProvisioningJournal",
        "RouteJournal",
        "ReplacementJournal",
    ):
        factory = getattr(replan, name)
        monkeypatch.setattr(
            replan,
            name,
            lambda p, g, factory=factory, name=name: factory(p, g, root=tmp_path / name),
        )
    return journal, local


def test_unused_plan_needs_changed_intent_and_preserves_exact_history(unused):
    journal, local = unused
    assert replan.inspect(journal, local=local, lifecycle=None) is None
    before = journal.path.read_bytes()
    options = {"local": {"setting": "new"}, "lifecycle": None}
    proposal = replan.inspect(journal, **options)
    assert proposal is not None
    assert journal.path.read_bytes() == before
    proposal.retire(replan.inspect(journal, **options))
    assert journal.snapshot()["transaction"] is None
    assert journal.retirement_completed(proposal.proof)
    assert any(p.read_bytes() == before for p in (journal.root / "history").glob("*.json"))
    assert all(owner.snapshot() is None for owner in proposal.owners.values())


def test_artifact_change_is_a_new_approval(unused):
    journal, local = unused
    proposal = replan.inspect(journal, local=local, lifecycle=None, artifact="e" * 64)
    assert proposal is not None and proposal.approval != journal.pending()["approval"]


def test_started_parent_is_not_an_unused_plan(unused):
    journal, _ = unused
    with journal:
        journal.started(next(iter(journal.pending()["progress"])))
    assert replan.inspect(journal, local={"changed": True}, lifecycle=None) is None


def test_unpublished_package_owner_refuses_without_archiving(unused):
    journal, _ = unused
    from nebius_vpngw.deploy.package_reservation import path_for

    path = path_for(journal.root, journal.pending()["execution_id"], "host")
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text("unknown reservation")
    before = journal.path.read_bytes()
    with pytest.raises(PreparationBlocked, match="package-parent-changed"):
        replan.inspect(journal, local={"changed": True}, lifecycle=None)
    assert journal.path.read_bytes() == before
    assert not (journal.root / "history").exists()


def test_concurrent_child_refuses_before_archiving(unused):
    journal, _ = unused
    options = {"local": {"changed": True}, "lifecycle": None}
    proposal = replan.inspect(journal, **options)
    before = journal.path.read_bytes()
    with proposal.owners["provisioning"], pytest.raises(WriterBusy):
        proposal.retire(replan.inspect(journal, **options))
    assert journal.path.read_bytes() == before
    assert not (journal.root / "history").exists()


def test_changed_child_or_parent_invalidates_approval(unused):
    journal, _ = unused
    options = {"local": {"changed": True}, "lifecycle": None}
    proposal = replan.inspect(journal, **options)
    other = replan.inspect(journal, **{**options, "local": {"changed": "again"}})
    with pytest.raises(PreparationBlocked, match="approval-state-changed"):
        proposal.retire(other)
    assert journal.pending() is not None


@pytest.mark.parametrize("dry_run", [False, True])
def test_decline_and_preview_never_retire(unused, monkeypatch, dry_run):
    journal, _ = unused
    before = journal.path.read_bytes()
    monkeypatch.setattr(cli, "_approve_disruption", lambda *_a, **_k: False)
    with pytest.raises(click.exceptions.Exit) as error:
        cli._review_unused_apply_plan(
            journal,
            local={"changed": True},
            lifecycle_store=NS(read=lambda **_: None),
            supplied=None,
            dry_run=dry_run,
            passive=False,
            report=ApplyReport(),
        )
    assert error.value.exit_code == 0
    assert journal.path.read_bytes() == before
    assert not (journal.root / "history").exists()


def test_settlement_does_not_authorize_fresh_effects(unused, monkeypatch):
    journal, _ = unused
    options = dict(
        journal=journal,
        local={"changed": True},
        lifecycle_store=NS(read=lambda **_: None),
        supplied=None,
        dry_run=False,
        passive=False,
        report=ApplyReport(),
    )
    monkeypatch.setattr(cli, "_approve_disruption", lambda *_a, **_k: True)
    with pytest.raises(ReplanReady):
        cli._review_unused_apply_plan(**options)
    assert journal.pending() is None
    assert options["report"].mapping()["pending"] == ("retire-unused-predecessor",)


@pytest.mark.parametrize("status", ["intent", "accepted", "settled"])
def test_hidden_partial_child_refuses_even_when_parent_is_planned(unused, monkeypatch, status):
    journal, _ = unused
    child = {
        "phase": "pending",
        "execution_id": journal.pending()["execution_id"],
        "operations": {"request": {"status": status}},
    }
    monkeypatch.setattr(
        replan, "ProvisioningJournal", lambda *_: NS(snapshot=lambda: {"transaction": child})
    )
    before = journal.path.read_bytes()
    with pytest.raises(PreparationBlocked, match="command-execution-changed"):
        replan.inspect(journal, local={"changed": True}, lifecycle=None)
    assert journal.path.read_bytes() == before
    assert not (journal.root / "history").exists()


def test_actual_apply_previews_unused_retirement_before_discovery(unused, monkeypatch, tmp_path):
    from nebius_vpngw.deploy import apply_command, ordinary_apply

    journal, _ = unused
    ha = any(effect["owner"] == "ha-lifecycle" for effect in journal.pending()["plan"]["effects"])
    path = tmp_path / "config.yaml"
    path.touch()
    wheel = tmp_path / "artifact.whl"
    wheel.write_bytes(b"pinned fixture")
    local = {"changed": True, "gateway_group": {"vm_spec": {}}}
    plan = NS(
        vm_ha=NS() if ha else None,
        validate=lambda: None,
        gateway_group=NS(name="gateway", region="region"),
        iter_instance_configs=lambda: (),
    )
    monkeypatch.setattr(cli, "_load_config_with_region_override", lambda *_a, **_k: local)
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_a: plan)
    monkeypatch.setattr(cli, "_vm_ha_activation_blockers", lambda: ())
    monkeypatch.setattr(cli, "VMHALifecycleStore", lambda *_: NS(read=lambda **_: None))
    monkeypatch.setattr(apply_command, "CommandJournal", lambda *_a, **_k: journal)
    monkeypatch.setattr(ordinary_apply, "pin_agent_artifact", lambda *_: wheel)
    monkeypatch.setattr(
        cli, "VMManager", lambda **_: pytest.fail("unused plan reached cloud discovery")
    )
    before = journal.path.read_bytes()
    report = ApplyReport(dry_run=True)
    with pytest.raises(click.exceptions.Exit) as error:
        cli._apply_impl(
            local_config_file=path,
            recreate_gw=False,
            sa=None,
            project_id="project",
            region=None,
            dry_run=True,
            prepare_vm_ha_peer_rotation=False,
            approve_vm_ha_migration=None,
            recover_vm_ha_migration=None,
            replace_failed_vm_ha_passive=None,
            result_report=report,
        )
    assert error.value.exit_code == 0
    assert report.mapping()["pending"] == ("retire-unused-predecessor",)
    assert journal.path.read_bytes() == before


def test_retirement_receipt_recovers_lost_parent_write_without_reapproval(unused, monkeypatch):
    journal, _ = unused
    original = journal.path.read_bytes()
    saved = journal.save
    approvals = []
    failed = False

    def save():
        nonlocal failed
        if journal.state["transaction"] is None and not failed:
            failed = True
            raise OSError("injected lost retirement publication")
        saved()

    def approve(value, **_):
        approvals.append(value)
        return True

    monkeypatch.setattr(journal, "save", save)
    monkeypatch.setattr(cli, "_approve_disruption", approve)
    options = dict(local={"setting": "new"}, lifecycle_store=NS(read=lambda **_: None), supplied=None, dry_run=False, passive=False, report=ApplyReport())
    with pytest.raises(OSError, match="injected lost retirement publication"):
        cli._review_unused_apply_plan(journal, **options)
    assert journal.path.read_bytes() == original
    assert len(tuple((journal.root / "settlements").glob("*.json"))) == 1
    with pytest.raises(ReplanReady):
        cli._review_unused_apply_plan(journal, **options)
    assert journal.pending() is None
    assert len(approvals) == 1
    assert [p.read_bytes() for p in (journal.root / "history").glob("*.json")] == [original]
