from copy import deepcopy
from unittest.mock import Mock

import pytest
from test_security_group_apply import cloud  # noqa: F401 -- shared generated-SDK fixture

from nebius_vpngw.deploy import attachment_command, security_group_apply
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect


@pytest.fixture
def context(request, tmp_path):
    state, manager = request.getfixturevalue("cloud")
    parent = CommandJournal("project-a", "gateway-a", root=tmp_path / "command")
    child = security_group_apply.AttachmentJournal(
        "project-a", "gateway-a", root=tmp_path / "child"
    )
    observed = security_group_apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    return state, manager, parent, child, observed


def inspect(parent, observed, child=None, **changes):
    return attachment_command.inspect(
        parent,
        observed=observed,
        child=child,
        **{"scope": "a" * 64, "artifact": "f" * 64, **changes},
    )


def test_preview_is_pure_and_unchanged_has_no_command(context):
    _state, manager, parent, child, observed = context
    assert inspect(parent, observed).approval == security_group_apply.digest(
        {
            "scope": "a" * 64,
            "attachments": observed,
        }
    )
    unchanged = security_group_apply.inspect_plan(manager, ("gateway-a-0",), ("sg-a",))
    assert inspect(parent, unchanged) is None
    assert not parent.root.exists() and not child.root.exists()


def test_completed_child_response_loss_finishes_parent_without_repeating_effects(context):
    state, manager, parent, child, observed = context
    command = inspect(parent, observed)
    with parent:
        command.begin()
    with child:
        child.begin(
            observed,
            scope=command.scope,
            approval=command.approval,
            operation_id=command.execution_id,
        )
        security_group_apply.update_member(manager, child, "gateway-a-0")
        child.state["transaction"].update(phase="complete", verified=["gateway-a-0"])
        child.save()
    before = child.path.read_bytes()
    current = security_group_apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    command = inspect(parent, current, child.read_transaction())
    assert command.completed_child and command.resumed
    command.execute(
        manager,
        Mock(),
        {},
        (),
        child,
        verify_ha=Mock(side_effect=AssertionError("repeated mutation")),
    )
    assert child.path.read_bytes() == before
    assert state.effects == 1
    assert parent.pending() is None
    before = parent.path.read_bytes(), child.path.read_bytes()
    assert inspect(parent, current, child.read_transaction()) is None
    assert (parent.path.read_bytes(), child.path.read_bytes()) == before


def test_another_pending_command_cannot_authorize_attachment_only_apply(context):
    _state, _manager, parent, _child, observed = context
    with parent:
        parent.begin(
            CommandPlan(
                parent.project,
                parent.gateway,
                "a" * 64,
                "f" * 64,
                (Effect("ha", "ha-lifecycle", "apply", ("compute-a",), "b" * 64, "c" * 64, True),),
            )
        )
    before = parent.path.read_bytes()
    with pytest.raises(RuntimeError, match="does not authorize"):
        inspect(parent, observed)
    assert parent.path.read_bytes() == before


@pytest.mark.parametrize("drift", ["scope", "artifact", "member", "desired"])
def test_pending_attachment_scope_cannot_expand(context, drift):
    _state, _manager, parent, child, observed = context
    command = inspect(parent, observed)
    with parent:
        command.begin()
    with child:
        child.begin(
            observed,
            scope=command.scope,
            approval=command.approval,
            operation_id=command.execution_id,
        )
    changed = deepcopy(observed)
    options = {}
    if drift in {"scope", "artifact"}:
        options[drift] = "b" * 64
    elif drift == "member":
        changed["members"]["gateway-a-0"]["compute_id"] = "foreign-compute"
    else:
        changed["desired_ids"] = ["sg-other"]
    before = parent.path.read_bytes(), child.path.read_bytes()
    with pytest.raises(RuntimeError):
        inspect(parent, changed, child.read_transaction(), **options)
    assert (parent.path.read_bytes(), child.path.read_bytes()) == before


def test_desired_readback_does_not_complete_accepted_unsettled_child(context):
    state, manager, parent, child, observed = context
    command = inspect(parent, observed)
    with parent:
        command.begin()
        with child:
            child.begin(
                observed,
                scope=command.scope,
                approval=command.approval,
                operation_id=command.execution_id,
            )
            state.timeout = True
            with pytest.raises(TimeoutError):
                security_group_apply.update_member(manager, child, "gateway-a-0")
        current = security_group_apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
        assert not security_group_apply.changed(current)
        with pytest.raises(RuntimeError, match="terminal child"):
            command.finish(child.read_transaction(), current)
    assert parent.pending() is not None
    assert next(iter(child.read_transaction()["operations"].values()))["status"] == "accepted"


def test_new_repair_cannot_reuse_previous_completed_child_after_parent_begin_loss(context):
    state, manager, parent, child, observed = context
    first = inspect(parent, observed)
    with parent:
        first.begin()
        with child:
            child.begin(
                observed,
                scope=first.scope,
                approval=first.approval,
                operation_id=first.execution_id,
            )
            security_group_apply.update_member(manager, child, "gateway-a-0")
            child.state["transaction"].update(phase="complete", verified=["gateway-a-0"])
            child.save()
        first.finish(
            child.read_transaction(),
            security_group_apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",)),
        )
    from nebius.api.nebius.compute.v1 import SecurityGroup

    # Independent external drift creates a new repair, not a retry of the first.
    state.instance.spec.network_interfaces[0].security_groups = [SecurityGroup(id="sg-a")]
    state.instance.metadata.resource_version += 1
    current = security_group_apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    second = inspect(parent, current, child.read_transaction())
    with parent:
        second.begin()  # Process stops before the new child starts.
    resumed = inspect(parent, current, child.read_transaction())
    assert resumed.resumed
    assert not resumed.completed_child
    with parent:
        resumed.begin()
        assert resumed.execution_id != first.execution_id
        with child:
            child.begin(
                current,
                scope=resumed.scope,
                approval=resumed.approval,
                operation_id=resumed.execution_id,
            )
            security_group_apply.update_member(manager, child, "gateway-a-0")
            child.state["transaction"].update(phase="complete", verified=["gateway-a-0"])
            child.save()
        final = security_group_apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
        resumed.finish(child.read_transaction(), final)
    assert state.effects == 2
    before = parent.path.read_bytes(), child.path.read_bytes()
    assert inspect(parent, final, child.read_transaction()) is None
    assert (parent.path.read_bytes(), child.path.read_bytes()) == before
