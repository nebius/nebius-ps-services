from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import deployment_status as module


def event(error="RESOURCE_EXHAUSTED: VM schedule timeout", **overrides):
    values = {
        "level": "ERROR",
        "code": "ComputeInstanceCreationFailed",
        "message": "Compute instance creation failed",
        "error": error,
    }
    values.update(overrides)
    return NS(last_occurrence=NS(**values))


def poller_with(events, *, state="PROVISIONING"):
    poller = module._Mk8sStatusPoller.__new__(module._Mk8sStatusPoller)
    node_group = NS(
        metadata=NS(name="workers"),
        status=NS(events=events, ready_node_count=0, target_node_count=2, state=state),
    )
    poller._target = module.Mk8sDeploymentTarget(project_id="project", cluster_name="cluster")
    poller._find_cluster = lambda: NS(
        metadata=NS(id="cluster", name="cluster"), status=NS(state="RUNNING")
    )
    poller._list_node_groups = lambda _: [node_group]
    poller._node_group_state_name = lambda value: value
    poller._cluster_state_name = lambda value: value
    poller._latest_operation_summary = lambda _: None
    return poller


@pytest.mark.parametrize("state", ["PROVISIONING", "CREATING", "UPDATING"])
@pytest.mark.parametrize(
    "error",
    [
        "RESOURCE_EXHAUSTED: VM schedule timeout",
        NS(code="RESOURCE_EXHAUSTED", message="VM schedule timeout"),
    ],
)
def test_recognized_capacity_wait_does_not_abort_and_remains_visible(state, error):
    poller = poller_with([event(error)], state=state)
    assert poller.terminal_failure() is None
    summary = poller.summary()
    assert "waiting for cloud capacity" in summary
    assert "0/2 ready" in summary


@pytest.mark.parametrize(
    "error",
    [
        "RESOURCE_EXHAUSTED",
        "RESOURCE_EXHAUSTED: quota exceeded",
        "PERMISSION_DENIED: VM schedule timeout",
        "INVALID_ARGUMENT: invalid configuration",
        NS(code="RESOURCE_EXHAUSTED", message="Quota exceeded: VM schedule timeout"),
    ],
)
def test_unproven_capacity_or_genuine_failure_remains_terminal(error):
    assert poller_with([event(error)]).terminal_failure() is not None


def test_capacity_event_does_not_hide_later_terminal_error():
    poller = poller_with([event(), event("PERMISSION_DENIED: access denied")])
    assert "access denied" in poller.terminal_failure()


def test_terminal_group_state_is_not_treated_as_pending_capacity():
    assert poller_with([event()], state="ERROR").terminal_failure() is not None


def test_different_event_with_capacity_text_remains_terminal():
    assert poller_with([event(code="OtherFailure")]).terminal_failure() is not None


@pytest.mark.parametrize("state", ["PROVISIONING", "CREATING", "UPDATING"])
@pytest.mark.parametrize(
    "error",
    [
        "UNAVAILABLE: transaction failed: attach filesystem: context deadline exceeded",
        NS(code="UNAVAILABLE", message="attach filesystem: context deadline exceeded"),
    ],
)
def test_transient_instance_creation_keeps_waiting_for_cloud_reconciliation(state, error):
    poller = poller_with([event(error)], state=state)
    assert poller.terminal_failure() is None
    assert "waiting for cloud provisioning retry" in poller.summary()
    assert "0/2 ready" in poller.summary()


def test_transient_creation_event_does_not_hide_permanent_failure():
    poller = poller_with(
        [event("UNAVAILABLE: try again"), event("INVALID_ARGUMENT: invalid shape")]
    )
    assert "invalid shape" in poller.terminal_failure()


@pytest.mark.parametrize(
    "state,code", [("ERROR", "ComputeInstanceCreationFailed"), ("PROVISIONING", "OtherFailure")]
)
def test_unavailable_outside_creation_retry_contract_remains_terminal(state, code):
    assert poller_with([event("UNAVAILABLE: try again", code=code)], state=state).terminal_failure()
