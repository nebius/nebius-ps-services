from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from nebius_vpngw import apply_approval
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
from nebius_vpngw.deploy.ordinary_command import inspect


@pytest.fixture
def command_fixture(tmp_path):
    journal = CommandJournal("project", "gateway", root=tmp_path / "command")
    plans = {
        name: SimpleNamespace(
            noop=False,
            manifest={"wheel_sha256": "a" * 64, "config_sha256": name * 64},
            observation={"boot_id": "boot-" + name, "runtime_pending": None},
        )
        for name in ("b", "c")
    }
    inputs = {
        "local": {"desired": "configuration"},
        "targets": {name: ("compute-" + name, "target-" + name) for name in plans},
        "plans": plans,
        "attachments": {"desired_ids": [], "members": {}},
        "attachment_pending": None,
        "artifact_sha256": "a" * 64,
    }
    return journal, inputs


def begin(journal, inputs):
    command = inspect(journal, **inputs)
    with journal:
        command.begin()
    for prepared in inputs["plans"].values():
        prepared.manifest["command_approval"] = apply_approval.bind(
            command.approval, command.plan.mapping()
        )
    return command


def complete_guest(journal, inputs, name):
    with journal:
        journal.started("guest:" + name)
    prepared = inputs["plans"][name]
    prepared.noop = True
    prepared.observation["runtime_completion"] = {
        "operation": name * 32,
        "approval": prepared.manifest["command_approval"],
        "manifest": apply_approval.manifest_identity(prepared.manifest),
    }


def prepared_routes(**changes):
    intent = {
        "project": "project",
        "gateway": "gateway",
        "policy": {},
        "infrastructure": {},
        "allocations": {},
        "active_tables": ["table"],
        "observed": {},
        "ownership": {},
    }
    result = SimpleNamespace(
        **{
            "intent": intent,
            "required": True,
            "pending": None,
            "completed": None,
            "preview": {"verified": "current"},
            "request_sha256": apply_approval.digest(intent),
            "begin": lambda approval: None,
            **changes,
        }
    )
    result.verifies_completed = lambda: result.completed["intent"] == result.intent
    return result


def test_unstarted_guest_keeps_exact_package_intent(command_fixture):
    from nebius_vpngw.deploy.ordinary_command import package_manifests

    journal, inputs = command_fixture
    command = begin(journal, inputs)
    before = journal.path.read_bytes()
    manifests = package_manifests(
        journal,
        local=inputs["local"],
        targets=inputs["targets"],
        artifact=inputs["artifact_sha256"],
    )
    assert manifests == {name: prepared.manifest for name, prepared in inputs["plans"].items()}
    assert all(
        manifest["command_approval"]["approval"] == command.approval
        for manifest in manifests.values()
    )
    manifests["b"]["wheel_sha256"] = "0" * 64
    assert journal.path.read_bytes() == before
    for field in ("local", "targets", "artifact"):
        args = {
            "local": inputs["local"],
            "targets": inputs["targets"],
            "artifact": inputs["artifact_sha256"],
        }
        args[field] = "0" * 64 if field == "artifact" else {}
        with pytest.raises(RuntimeError, match="original.*intent"):
            package_manifests(journal, **args)


def test_route_effect_depends_on_guests_and_keeps_its_own_scope(command_fixture):
    journal, inputs = command_fixture
    inputs["routes"] = prepared_routes()
    command = inspect(journal, **inputs)
    assert command.plan.effects[-1].key == "routes"
    assert command.plan.effects[-1].requires == ("guest:b", "guest:c")
    with journal:
        command.begin()
    inputs["routes"].pending = {"approval": command.approval}
    assert inspect(journal, **inputs).approval == command.approval


def test_pending_guest_approval_does_not_authorize_new_routes(command_fixture):
    journal, inputs = command_fixture
    begin(journal, inputs)
    inputs["routes"] = prepared_routes()
    with pytest.raises(RuntimeError, match="newly discovered"):
        inspect(journal, **inputs)


def test_route_terminal_receipt_settles_lost_final_response(command_fixture):
    journal, inputs = command_fixture
    routes = prepared_routes()
    inputs["routes"] = routes
    command = begin(journal, inputs)
    for name in ("b", "c"):
        complete_guest(journal, inputs, name)
    with journal:
        resumed = inspect(journal, **inputs)
        resumed.begin()
        journal.started("routes")
    routes.required = False
    routes.completed = {
        "phase": "complete",
        "approval": command.approval,
        "intent": copy.deepcopy(routes.intent),
    }
    resumed = inspect(journal, **inputs)
    assert resumed.approval == command.approval
    with journal:
        resumed.begin()
        journal.complete()
    assert journal.pending() is None


def test_changed_route_policy_cannot_reuse_terminal_receipt(command_fixture):
    journal, inputs = command_fixture
    routes = prepared_routes()
    inputs["routes"] = routes
    command = begin(journal, inputs)
    routes.required = False
    routes.completed = {
        "phase": "complete",
        "approval": command.approval,
        "intent": copy.deepcopy(routes.intent),
    }
    routes.intent["policy"] = {"changed": True}
    with pytest.raises(RuntimeError, match="terminal receipt"):
        inspect(journal, **inputs)


def test_dry_discovery_and_unchanged_never_create_state(command_fixture):
    journal, inputs = command_fixture
    assert inspect(journal, **inputs).plan.effects
    assert not journal.root.exists()
    for prepared in inputs["plans"].values():
        prepared.noop = True
    assert not inspect(journal, **inputs).plan.effects
    assert not journal.root.exists()


def test_resume_completed_first_and_unattempted_second(command_fixture):
    journal, inputs = command_fixture
    first = begin(journal, inputs)
    complete_guest(journal, inputs, "b")
    resumed = inspect(journal, **inputs)
    assert resumed.approval == first.approval
    assert resumed.resumed
    with journal:
        resumed.begin()
        assert journal.state["transaction"]["progress"] == {
            "guest:b": "verified",
            "guest:c": "planned",
        }
        journal.started("guest:c")
        journal.verified("guest:c", {"exact_child_receipt": True})
        journal.complete()
    assert journal.pending() is None
    assert journal.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("point", ["before-child", "child-pending"])
def test_resume_before_submission_or_exact_pending_child(command_fixture, point):
    journal, inputs = command_fixture
    command = begin(journal, inputs)
    with journal:
        journal.started("guest:b")
    if point == "child-pending":
        inputs["plans"]["b"].observation["runtime_pending"] = {"operation": "d" * 32}
    assert inspect(journal, **inputs).approval == command.approval


def test_matching_artifact_does_not_cancel_unsettled_child(command_fixture):
    journal, inputs = command_fixture
    begin(journal, inputs)
    with journal:
        journal.started("guest:b")
    inputs["plans"]["b"].noop = True
    with pytest.raises(RuntimeError, match="terminal receipt"):
        inspect(journal, **inputs)


@pytest.mark.parametrize("change", ["configuration", "artifact", "identity", "new-effect"])
def test_pending_command_never_expands_authority(command_fixture, change):
    journal, inputs = command_fixture
    if change == "new-effect":
        inputs["plans"]["b"].noop = True
    begin(journal, inputs)
    if change == "configuration":
        inputs["local"]["desired"] = "different"
    elif change == "artifact":
        inputs["artifact_sha256"] = "f" * 64
    elif change == "identity":
        inputs["targets"]["b"] = ("different-compute", "target-b")
    else:
        inputs["plans"]["b"].noop = False
    with pytest.raises(RuntimeError, match="reviewed successor|newly discovered"):
        inspect(journal, **inputs)


def test_completed_step_drift_requires_new_approval(command_fixture):
    journal, inputs = command_fixture
    begin(journal, inputs)
    with journal:
        journal.started("guest:b")
        journal.verified("guest:b", {"exact_child_receipt": True})
    with pytest.raises(RuntimeError, match="another effect"):
        inspect(journal, **inputs)


def test_unstarted_predecessor_cannot_change_under_old_approval(command_fixture):
    journal, inputs = command_fixture
    begin(journal, inputs)
    inputs["plans"]["c"].observation["boot_id"] = "different-boot"
    with pytest.raises(RuntimeError, match="predecessor changed"):
        inspect(journal, **inputs)


def test_attachment_dependency_prevents_early_cloud_effect(command_fixture):
    journal, inputs = command_fixture
    inputs["attachments"]["members"] = {
        "b": {"compute_id": "compute-b", "before": ["old"], "desired": ["new"]}
    }
    begin(journal, inputs)
    with journal, pytest.raises(ValueError, match="journal is invalid"):
        journal.started("attachments")
    # Invalid in-memory progress was never published.
    assert journal.pending()["progress"]["attachments"] == "planned"


def test_lost_final_attachment_response_uses_terminal_child_receipt(command_fixture):
    journal, inputs = command_fixture
    for prepared in inputs["plans"].values():
        prepared.noop = True
    inputs["attachments"]["members"] = {
        "b": {"compute_id": "compute-b", "before": ["old"], "desired": ["new"]}
    }
    command = begin(journal, inputs)
    completed_plan = copy.deepcopy(inputs["attachments"])
    with journal:
        journal.started("attachments")
    inputs["attachments"]["members"]["b"]["before"] = ["new"]
    with pytest.raises(RuntimeError, match="terminal receipt"):
        inspect(journal, **inputs)
    inputs["attachment_completed"] = {
        "phase": "complete",
        "approval": command.approval,
        "operation_id": "0" * 32,
        "plan": completed_plan,
    }
    with pytest.raises(RuntimeError, match="terminal receipt"):
        inspect(journal, **inputs)
    inputs["attachment_completed"]["operation_id"] = journal.pending()["execution_id"]
    resumed = inspect(journal, **inputs)
    with journal:
        resumed.begin()
        journal.complete()
    snapshot = journal.path.read_bytes()
    assert not inspect(journal, **inputs).plan.effects
    assert journal.path.read_bytes() == snapshot


@pytest.mark.parametrize("damage", ["secret", "receipt", "scope", "complete"])
def test_corrupt_command_fails_closed(command_fixture, damage):
    journal, inputs = command_fixture
    begin(journal, inputs)
    value = json.loads(journal.path.read_text())
    if damage == "secret":
        value["transaction"]["token"] = "not-a-credential"
    elif damage == "receipt":
        value["transaction"]["progress"]["guest:b"] = "verified"
    elif damage == "scope":
        value["transaction"]["plan"]["artifact_sha256"] = "f" * 64
    else:
        value["transaction"]["phase"] = "complete"
    journal.path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        journal.pending()


def test_child_intent_is_bound_to_approved_request_and_survives_restart(tmp_path):
    journal = CommandJournal("project", "gateway", root=tmp_path / "command")
    intent = {"public_approval": "a" * 64, "actions": ["activate-exact-generation"]}
    plan = CommandPlan(
        "project",
        "gateway",
        "b" * 64,
        "c" * 64,
        (
            Effect(
                "ha",
                "ha-lifecycle",
                "apply",
                ("compute",),
                apply_approval.digest(intent),
                "d" * 64,
                True,
            ),
        ),
    )
    with journal:
        journal.begin(plan, intents={"ha": intent})
    intent["actions"].append("unapproved-action")
    with journal:
        original = journal.state["bindings"]
        assert original["ha"]["actions"] == ["activate-exact-generation"]
        before = journal.path.read_bytes()
        journal.begin(plan, intents=original)
        assert journal.path.read_bytes() == before
        with pytest.raises(RuntimeError, match="pending"):
            journal.begin(plan, intents={"ha": intent})
        assert journal.path.read_bytes() == before
    corrupted = json.loads(before)
    corrupted["bindings"]["ha"] = intent
    journal.path.write_text(json.dumps(corrupted))
    with pytest.raises(ValueError, match="invalid"):
        journal.snapshot()


@pytest.mark.parametrize("damage", ["wrong-digest", "unplanned-effect", "secret"])
def test_invalid_child_intent_cannot_replace_completed_history(tmp_path, damage):
    journal = CommandJournal("project", "gateway", root=tmp_path / "command")
    intent = {"scope": "a" * 64}
    plan = CommandPlan(
        "project",
        "gateway",
        "b" * 64,
        "c" * 64,
        (
            Effect(
                "ha",
                "ha-lifecycle",
                "apply",
                ("compute",),
                apply_approval.digest(intent),
                "d" * 64,
                True,
            ),
        ),
    )
    with journal:
        journal.begin(plan, intents={"ha": intent})
        journal.verified("ha", {"observed": "ready"})
        journal.complete()
        before = journal.path.read_bytes()
        bindings = (
            {"ha": {"scope": "e" * 64}}
            if damage == "wrong-digest"
            else (
                {"other": intent}
                if damage == "unplanned-effect"
                else {"ha": {"token": "forbidden"}}
            )
        )
        with pytest.raises(ValueError):
            journal.begin(plan, intents=bindings)
        assert journal.path.read_bytes() == before
        assert not (journal.root / "history").exists()
