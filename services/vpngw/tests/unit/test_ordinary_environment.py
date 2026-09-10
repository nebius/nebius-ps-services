"""Fresh Ubuntu/FRR admission without weakening effect settlement."""

from __future__ import annotations

import copy
import time

import pytest

from nebius_vpngw import ordinary_operations as ops


class EnvironmentManager(ops.Manager):
    def __init__(self):
        super().__init__(time.monotonic() + 30)
        self.units = {
            name: {
                "LoadState": "loaded",
                "ActiveState": "active",
                "SubState": "running",
                "MainPID": 10,
                "ControlPID": 0,
                "Job": [0, "/"],
            }
            for name in (*ops.MANAGEMENT, *ops.DATAPLANE)
        }
        self.pending = []

    def command(self, args, **kwargs):
        return '<method name="EnqueueUnitJob"/>'

    def jobs(self):
        return self.pending

    def unit(self, name):
        return copy.deepcopy(self.units[name])

    def network(self):
        return {"inputs": {}, "links": [{"name": "eth0"}]}

    def restart_wait(self, name="nebius-vpngw-agent.service"):
        self.units[name].update(ActiveState="activating", SubState="auto-restart", MainPID=0)


def test_restart_wait_can_be_planned_but_is_never_settled():
    manager = EnvironmentManager()
    manager.restart_wait()
    manager.preflight()
    assert not manager.quiet(ops.MANAGEMENT)
    assert not manager.stopped("nebius-vpngw-agent.service")


@pytest.mark.parametrize(
    "change",
    [
        {"MainPID": 17},
        {"ControlPID": 18},
        {"Job": [9, "/job/9"]},
        {"SubState": "start"},
        {"SubState": "auto-restart-queued"},
    ],
)
def test_restart_wait_with_unsettled_work_remains_blocked(change):
    manager = EnvironmentManager()
    manager.restart_wait()
    manager.units["nebius-vpngw-agent.service"].update(change)
    with pytest.raises(ops.OperationBlocked, match="manager_not_ready"):
        manager.preflight()


def test_queued_management_job_and_dataplane_restart_remain_blocked():
    manager = EnvironmentManager()
    manager.restart_wait()
    manager.pending = [[7, "nebius-vpngw-agent.service", "start"]]
    with pytest.raises(ops.OperationBlocked, match="manager_not_ready"):
        manager.preflight()
    manager.pending.clear()
    manager.restart_wait("frr.service")
    with pytest.raises(ops.OperationBlocked, match="manager_not_ready"):
        manager.preflight()


def missing_handler(manager):
    name = "heartbeat-failed@frr.service"
    manager.units["frr.service"]["OnFailure"] = [name]
    manager.units[name] = {
        "missing": True,
        "LoadState": "not-found",
        "ActiveState": "inactive",
        "SubState": "dead",
        "MainPID": 0,
        "ControlPID": 0,
        "Job": [0, "/"],
    }
    return name


def test_absent_frr_failure_handler_is_bound_to_environment():
    manager = EnvironmentManager()
    name = missing_handler(manager)
    assert manager.environment()["missing_failure_units"] == [name]
    # A newly installed handler must invalidate admission, even before it runs.
    manager.units[name].update(missing=False, LoadState="loaded")
    with pytest.raises(ops.OperationBlocked, match="service_scope_unknown"):
        manager.environment()


@pytest.mark.parametrize(
    "change",
    [
        {"LoadState": "masked"},
        {"LoadState": "error"},
        {"ActiveState": "active"},
        {"MainPID": 21},
        {"ControlPID": 22},
        {"Job": [2, "/job/2"]},
    ],
)
def test_inexact_failure_handler_absence_remains_blocked(change):
    manager = EnvironmentManager()
    name = missing_handler(manager)
    manager.units[name].update(change)
    with pytest.raises(ops.OperationBlocked, match="service_scope_unknown"):
        manager.environment()


@pytest.mark.parametrize("field", ["OnSuccess", "Upholds"])
def test_other_activation_dependencies_remain_blocked(field):
    manager = EnvironmentManager()
    name = missing_handler(manager)
    manager.units["frr.service"][field] = [name]
    with pytest.raises(ops.OperationBlocked, match="service_scope_unknown"):
        manager.environment()


def test_unit_observation_loads_inactive_definition_without_starting(monkeypatch):
    manager = ops.Manager(time.monotonic() + 30)
    calls = []

    def system(method, *args):
        calls.append((method, args))
        assert method == "LoadUnit"
        return {"type": "o", "data": ["/unit/handler"]}

    monkeypatch.setattr(manager, "system", system)
    monkeypatch.setattr(
        manager, "properties", lambda *args: {"LoadState": "loaded", "ActiveState": "inactive"}
    )
    assert not manager.unit("heartbeat-failed@frr.service")["missing"]
    assert calls == [("LoadUnit", ("s", "heartbeat-failed@frr.service"))]
