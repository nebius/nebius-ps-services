"""Composed receipts use real local journals and generated Compute requests."""

import json
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from test_security_group_apply import cloud  # noqa: F401

from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import ha_command
from nebius_vpngw.deploy import security_group_apply as sg
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus


@pytest.fixture
def context(request, tmp_path):
    state, manager = request.getfixturevalue("cloud")
    parent = CommandJournal("project-a", "gateway-a", root=tmp_path / "parent")
    child = sg.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "child")
    observed = sg.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    command = ha_command.create(
        parent,
        scope="a" * 64,
        artifact="f" * 64,
        action_scope="b" * 64,
        targets=("compute-a",),
        approval="c" * 64,
        runtime_scope="d" * 64,
        report={"kind": "apply-convergence"},
        attachment_plan=observed,
    )
    return state, manager, command, child, observed


def lifecycle(command, **changes):
    bindings = {
        "runtime-command-execution": command.execution_id,
        "runtime-command-approval": json.dumps(
            {
                "schema": "nebius-vpngw.apply-approval.v1",
                "approval": command.approval,
                "scope": command.intent["runtime_scope"],
            }
        ),
        "heartbeat-upgrade-artifact": command.plan.artifact_sha256,
    }
    bindings.update(changes)
    return NS(
        status=VMHALifecycleStatus.ACTIVE,
        project_id=command.plan.project_id,
        gateway_name=command.plan.gateway_name,
        members=(NS(compute_id="compute-a"),),
        transaction=NS(resource_bindings=bindings, pending_effect=None),
        record_sha256="e" * 64,
    )


def resume(command, **changes):
    return ha_command.resume(
        command.journal,
        **{
            "scope": "a" * 64,
            "artifact": "f" * 64,
            "action_scope": "b" * 64,
            "targets": ("compute-a",),
            **changes,
        },
    )


def test_prerequisites_survive_parent_publication_and_precede_runtime(context):
    _state, _manager, original, _child, _observed = context
    credentials = {"action": "create", "project_id": "project-a"}
    trust = {"targets": {"gateway-a-0": "gateway-a-0"}}
    command = ha_command.create(
        original.journal,
        scope="a" * 64,
        artifact="f" * 64,
        action_scope="b" * 64,
        targets=("compute-a",),
        approval="c" * 64,
        runtime_scope="d" * 64,
        report={"kind": "apply-convergence"},
        attachment_plan=None,
        credentials=credentials,
        node_ids=("node-a",),
        ssh=trust,
    )
    with command.journal:
        command.begin()
    before = command.journal.path.read_bytes()
    recovered = resume(command)
    assert recovered is not None
    assert recovered.runtime.targets == ("compute-a",)
    assert recovered.bindings["credentials"] == credentials
    assert recovered.bindings["ssh"] == trust
    assert ha_command.pending_prerequisites(command.journal)["bindings"] == recovered.bindings
    assert command.journal.path.read_bytes() == before
    with command.journal:
        recovered.begin()
        recovered.start_prerequisite("credentials")
        recovered.journal.verified("credentials", {"enrollment": "verified"})
        recovered.start_prerequisite("ssh")
        recovered.journal.verified("ssh", {"trust": "verified"})
        recovered.start_runtime()
        recovered.verify_runtime(lifecycle(recovered))
        recovered.journal.complete()
    assert command.journal.pending() is None


def test_runtime_cannot_start_before_credential_receipt(context):
    _state, _manager, original, _child, _observed = context
    command = ha_command.create(
        original.journal,
        scope="a" * 64,
        artifact="f" * 64,
        action_scope="b" * 64,
        targets=("compute-a",),
        approval="c" * 64,
        runtime_scope="d" * 64,
        report={"kind": "apply-convergence"},
        attachment_plan=None,
        credentials={"action": "create"},
        node_ids=("node-a",),
    )
    with command.journal:
        command.begin()
        before = command.journal.path.read_bytes()
        with pytest.raises(ValueError, match="Apply command journal is invalid"):
            command.start_runtime()
        assert command.journal.path.read_bytes() == before


def test_composite_approval_and_terminal_response_loss_keep_execution_identity(context):
    state, manager, command, child, observed = context
    assert not command.journal.root.exists()
    with command.journal:
        command.begin()
        command.start_runtime()
        command.verify_runtime(lifecycle(command))
        command.journal.started("attachments")
        with child:
            child.begin(
                observed,
                scope="a" * 64,
                approval=command.approval,
                operation_id=command.execution_id,
            )
            sg.update_member(manager, child, "gateway-a-0")
            child.state["transaction"].update(phase="complete", verified=["gateway-a-0"])
            child.save()
    current = sg.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    recovered = resume(command)
    assert recovered.approval != digest({"scope": "a" * 64, "attachments": observed})
    before = child.path.read_bytes()
    recovered.execute_attachments(
        manager,
        Mock(),
        {},
        (),
        child,
        lifecycle=lifecycle(command),
        verify_ha=Mock(),
        observed=current,
    )
    assert state.effects == 1
    assert child.path.read_bytes() == before
    assert command.journal.pending() is None
    before = command.journal.path.read_bytes()
    assert resume(command) is None
    assert command.journal.path.read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", "0" * 64),
        ("artifact", "0" * 64),
        ("action_scope", "0" * 64),
        ("targets", ("foreign-compute",)),
    ],
)
def test_new_scope_cannot_inherit_pending_command(context, field, value):
    _state, _manager, command, _child, _observed = context
    with command.journal:
        command.begin()
    before = command.journal.path.read_bytes()
    with pytest.raises(RuntimeError):
        resume(command, **{field: value})
    assert command.journal.path.read_bytes() == before


@pytest.mark.parametrize(
    "mutation", ["execution", "artifact", "approval", "member", "pending", "status"]
)
def test_historical_or_nonterminal_lifecycle_cannot_settle_runtime(context, mutation):
    _state, _manager, command, _child, _observed = context
    with command.journal:
        command.begin()
        command.start_runtime()
        observed = lifecycle(command)
        if mutation in {"execution", "artifact", "approval"}:
            key = {
                "execution": "runtime-command-execution",
                "artifact": "heartbeat-upgrade-artifact",
                "approval": "runtime-command-approval",
            }[mutation]
            observed.transaction.resource_bindings[key] = "0" * 64
        elif mutation == "member":
            observed.members[0].compute_id = "foreign-compute"
        elif mutation == "pending":
            observed.transaction.pending_effect = "some-effect"
        else:
            observed.status = VMHALifecycleStatus.ACTIVATING
        with pytest.raises(RuntimeError, match="terminal lifecycle"):
            command.verify_runtime(observed)
        assert command.journal.pending()["progress"]["runtime"] == "started"


def test_uncertain_attachment_is_not_settled_by_desired_readback(context):
    state, manager, command, child, observed = context
    with command.journal:
        command.begin()
        command.start_runtime()
        command.verify_runtime(lifecycle(command))
        command.journal.started("attachments")
        with child:
            child.begin(
                observed,
                scope="a" * 64,
                approval=command.approval,
                operation_id=command.execution_id,
            )
            state.timeout = True
            with pytest.raises(TimeoutError):
                sg.update_member(manager, child, "gateway-a-0")
        current = sg.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
        assert not sg.changed(current)
        with pytest.raises(RuntimeError, match="terminal attachment"):
            command.finish_attachments(child.read_transaction(), current)
        assert command.journal.pending() is not None
        foreign = deepcopy(child.read_transaction())
        foreign["operation_id"] = "0" * 32
        with pytest.raises(RuntimeError, match="another HA command"):
            command.attachment_plan(current, foreign)


def test_verified_runtime_never_admits_new_runtime_drift(context):
    _state, _manager, command, _child, _observed = context
    with command.journal:
        command.begin()
        command.start_runtime()
        command.verify_runtime(lifecycle(command))
    with pytest.raises(RuntimeError, match="separately reviewed"):
        ha_command.require_runtime_scope(resume(command), command.intent["runtime_scope"])


@pytest.mark.parametrize("publication_loss", [False, True])
def test_reviewed_successor_archives_exact_parent_and_resumes_publication_loss(
    context, monkeypatch, publication_loss
):
    _state, _manager, command, child, observed = context
    with command.journal:
        command.begin()
        command.start_runtime()
    original = command.journal.path.read_bytes()
    old_execution = command.journal.pending()["execution_id"]
    failed = lifecycle(command)
    failed.status = VMHALifecycleStatus.ACTIVATING
    failed.transaction.accepted_cloud_operation_id = None
    proposal = ha_command.reviewed_successor(
        command.journal.snapshot(),
        lifecycle=failed,
        kind="corrective-release",
        old_scope="a" * 64,
        action_scope="b" * 64,
        targets=("compute-a",),
        attachment_child=None,
    )
    successor = ha_command.create(
        command.journal,
        scope="0" * 64,
        artifact="1" * 64,
        action_scope="b" * 64,
        targets=("compute-a",),
        approval="2" * 64,
        runtime_scope="3" * 64,
        report={"kind": "corrective-release"},
        attachment_plan=observed,
        successor=proposal,
    )
    save = command.journal.save

    def lose_response():
        save()
        raise TimeoutError("published successor")

    with command.journal:
        if publication_loss:
            with monkeypatch.context() as patcher:
                patcher.setattr(command.journal, "save", lose_response)
                with pytest.raises(TimeoutError):
                    successor.begin()
        else:
            successor.begin()
    assert (
        command.journal.root / "history" / (proposal["predecessor"] + ".json")
    ).read_bytes() == original
    selected = ha_command.resume(
        command.journal,
        scope="0" * 64,
        artifact="1" * 64,
        action_scope="b" * 64,
        targets=("compute-a",),
    )
    before = command.journal.path.read_bytes()
    with command.journal:
        selected.begin()
        assert selected.execution_id != old_execution
    assert command.journal.path.read_bytes() == before


def test_reviewed_successor_refuses_uncertain_attachment_operation(context):
    state, manager, command, child, observed = context
    with command.journal:
        command.begin()
        command.start_runtime()
    with child:
        child.begin(
            observed, scope="a" * 64, approval=command.approval, operation_id=command.execution_id
        )
        state.timeout = True
        with pytest.raises(TimeoutError):
            sg.update_member(manager, child, "gateway-a-0")
    failed = lifecycle(command)
    failed.status = VMHALifecycleStatus.ACTIVATING
    failed.transaction.accepted_cloud_operation_id = None
    before = command.journal.path.read_bytes(), child.path.read_bytes()
    with pytest.raises(RuntimeError, match="Settle pending attachment"):
        ha_command.reviewed_successor(
            command.journal.snapshot(),
            lifecycle=failed,
            kind="corrective-release",
            old_scope="a" * 64,
            action_scope="b" * 64,
            targets=("compute-a",),
            attachment_child=child.read_transaction(),
        )
    assert (command.journal.path.read_bytes(), child.path.read_bytes()) == before


def test_composite_prerequisite_uses_public_approval_from_real_command(context):
    from nebius_vpngw.deploy.ha_composite import prerequisite_matches

    _state, _manager, command, _child, _observed = context
    with command.journal:
        command.begin()
    checkpoint = command.journal.snapshot()
    assert checkpoint["transaction"]["approval"] != command.approval
    assert prerequisite_matches(checkpoint, {"prerequisite": command.approval})
    assert not prerequisite_matches(
        checkpoint, {"prerequisite": checkpoint["transaction"]["approval"]}
    )
