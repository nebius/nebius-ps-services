from types import SimpleNamespace as NS

import pytest
from test_replacement import Ports, plan

from nebius_vpngw import cli
from nebius_vpngw.deploy import (
    apply_command,
    replacement,
    replacement_ordinary,
    replacement_trust,
    security_group_apply,
)
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect


@pytest.fixture
def public_replacement(tmp_path, monkeypatch):
    desired = plan()
    child_type = replacement.Journal
    parent_type = apply_command.CommandJournal
    child = child_type(desired.project_id, desired.gateway_name, root=tmp_path / "child")
    parent = parent_type(desired.project_id, desired.gateway_name, root=tmp_path / "parent")
    monkeypatch.setattr(replacement, "Journal", lambda *args, **kwargs: child)
    monkeypatch.setattr(apply_command, "CommandJournal", lambda *args, **kwargs: parent)
    monkeypatch.setattr(
        security_group_apply, "AttachmentJournal", lambda *args: NS(pending=lambda: None)
    )
    monkeypatch.setattr(replacement_trust, "preview", lambda *args: object())
    monkeypatch.setattr(replacement_trust, "resolve", lambda *args: object())
    monkeypatch.setattr(cli, "_gateway_ssh_trust_scope", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "_approve_disruption", lambda digest, supplied, dry_run: not dry_run)
    ports = Ports()
    ports.inspect = lambda: desired
    state = NS(lose_final=False, reads=0)

    def terminal(journal):
        assert journal.state["transaction"]["phase"] == "complete"
        state.reads += 1
        if state.lose_final:
            state.lose_final = False
            raise TimeoutError("final independent read interrupted")
        return {"independent": "verified"}

    ports.terminal_receipt = terminal
    monkeypatch.setattr(replacement_ordinary, "OrdinaryReplacement", lambda *args, **kwargs: ports)

    def run(*, dry_run=False, resume=None):
        cli._execute_gateway_replacement(
            manager=NS(project_id=desired.project_id),
            ssh=NS(),
            plan=NS(vm_ha=None, gateway_group=NS(name=desired.gateway_name)),
            local={},
            instances=(),
            dry_run=dry_run,
            recreate=True,
            approval=replacement.digest(desired.mapping()),
            config_path=tmp_path / "config.yaml",
            resume=resume,
        )

    return state, ports, parent, child, run


def test_replacement_preview_does_not_create_parent_or_child_state(public_replacement):
    _state, ports, parent, child, run = public_replacement
    run(dry_run=True)
    assert not parent.root.exists()
    assert not child.root.exists()
    assert ports.effects == []


def test_replacement_parent_settles_lost_final_reply_and_repeated_resume_is_unchanged(
    public_replacement,
):
    state, ports, parent, child, run = public_replacement
    state.lose_final = True
    with pytest.raises(TimeoutError):
        run()
    assert child.snapshot()["transaction"]["phase"] == "complete"
    assert parent.pending()["progress"] == {"replacement": "started"}
    effects = list(ports.effects)
    run(resume=child.path)
    assert parent.pending() is None
    assert ports.effects == effects
    before = (parent.path.read_bytes(), child.path.read_bytes())
    run(resume=child.path)
    assert (parent.path.read_bytes(), child.path.read_bytes()) == before
    assert ports.effects == effects
    assert state.reads == 3


def test_public_replacement_resumes_external_older_bundle_after_progress(
    public_replacement, tmp_path
):
    _state, ports, parent, child, run = public_replacement
    ports.fail = "prepare"
    with pytest.raises(RuntimeError, match="interrupted"):
        run()
    bundle = tmp_path / "operator-copy.json"
    bundle.write_bytes(child.path.read_bytes())
    bundle.chmod(0o600)
    ports.fail = "transfer"
    with pytest.raises(RuntimeError, match="interrupted"):
        run(resume=bundle)
    ports.fail = None
    run(resume=bundle)
    assert parent.pending() is None
    assert [step for step, _ in ports.effects].count("prepare") == 2
    before = parent.path.read_bytes(), child.path.read_bytes(), list(ports.effects)
    run(resume=bundle)
    assert (parent.path.read_bytes(), child.path.read_bytes(), ports.effects) == before


def test_another_pending_apply_cannot_start_replacement_or_publish_its_child(public_replacement):
    _state, ports, parent, child, run = public_replacement
    with parent:
        parent.begin(
            CommandPlan(
                parent.project,
                parent.gateway,
                "a" * 64,
                "b" * 64,
                (
                    Effect(
                        "guest", "ordinary-runtime", "apply", ("compute",), "c" * 64, "d" * 64, True
                    ),
                ),
            )
        )
    before = parent.path.read_bytes()
    with pytest.raises(RuntimeError, match="pending apply"):
        run(dry_run=True)
    assert parent.path.read_bytes() == before
    assert not child.root.exists()
    assert ports.effects == []


def test_new_effectful_command_archives_exact_previous_envelope(tmp_path):
    journal = apply_command.CommandJournal("project", "gateway", root=tmp_path / "command")
    first = CommandPlan(
        "project",
        "gateway",
        "a" * 64,
        "b" * 64,
        (Effect("one", "owner", "apply", ("compute",), "c" * 64, "d" * 64, True),),
    )
    with journal:
        journal.begin(first)
        journal.verified("one", {"proof": "observed"})
        journal.complete()
    original = journal.path.read_bytes()
    with journal:
        journal.begin(CommandPlan("project", "gateway", "e" * 64, "b" * 64, first.effects))
    assert [p.read_bytes() for p in (journal.root / "history").glob("*.json")] == [original]
