from __future__ import annotations

import copy
import os
import time

import pytest

from nebius_vpngw import ordinary_operations as ops


class Manager:
    def __init__(self):
        self.deadline = time.monotonic() + 20
        self.calls = []
        self.settled = True
        self.network_value = {"inputs": {}, "links": [{"name": "eth0"}]}

    def system(self, method, *args):
        self.calls.append((method, args))
        if method == "EnqueueUnitJob":
            return {"type": "uososa(uosos)", "data": [7, "/job/7", args[1], "/unit/1", args[2], []]}
        return {"type": "", "data": []}

    def unit_finished(self, effect):
        return self.unit_settled(effect)

    def unit(self, name):
        return {"Id": name, "Names": [name], "missing": False}

    def unit_settled(self, effect):
        return self.settled

    def quiet(self, names):
        return self.settled

    def network(self):
        return copy.deepcopy(self.network_value)


@pytest.mark.parametrize("outcome", ["settles", "busy", "late", "new-operation"])
def test_quiet_observation_keeps_one_deadline_and_rechecks_admission(monkeypatch, outcome):
    from types import SimpleNamespace

    clock = [100.0]
    calls = []
    monkeypatch.setattr(ops.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ops.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))

    def quiet(names):
        assert names == (*ops.MANAGEMENT, *ops.DATAPLANE)
        calls.append("observe")
        if outcome == "late":
            clock[0] = 101.1
            return True
        return outcome != "busy" and calls.count("observe") >= 2

    def admission():
        calls.append("admit")
        if outcome == "new-operation" and calls.count("observe") == 2:
            raise ops.OperationBlocked("ordinary_operation_in_progress")

    manager = SimpleNamespace(deadline=101.0, quiet=quiet)
    if outcome == "settles":
        ops.wait_quiet(manager, admission)
        assert calls == ["admit", "observe", "admit", "observe", "admit"]
        assert 100 < clock[0] < 101
    elif outcome == "new-operation":
        with pytest.raises(ops.OperationBlocked, match="in_progress"):
            ops.wait_quiet(manager, admission)
    else:
        with pytest.raises(TimeoutError, match="ordinary_manager_unresolved"):
            ops.wait_quiet(manager, admission)
        assert clock[0] >= manager.deadline
    assert manager.deadline == 101.0


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "JOURNAL", tmp_path / "ordinary" / "operation.json")
    monkeypatch.setattr(ops, "ROUTING_LOCK", tmp_path / "run" / "routing.lock")
    boot = tmp_path / "boot"
    boot.write_text("11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(ops, "BOOT", boot)

    def identity(pid=None):
        pid = os.getpid() if pid is None else pid
        if pid != os.getpid():
            raise FileNotFoundError
        return {"pid": pid, "start": "123", "boot": boot.read_text()}

    monkeypatch.setattr(ops, "identity", identity)
    return ops.Journal()


def begin(manager, journal=None):
    return ops.Operation.begin(
        request="1" * 32,
        config="2" * 64,
        artifact="3" * 64,
        predecessor="4" * 64,
        manager=manager,
        network=manager.network(),
        journal=journal,
    )


def abandon(store):
    value = store.read()
    value["owner"]["pid"] = 999999
    for effect in value["effects"]:
        effect["writer"]["pid"] = 999999
    store.write(value)
    return value


def test_reboot_resume_keeps_unacknowledged_unit_intent_readable(store):
    manager = Manager()
    operation = begin(manager, store)
    value = operation.value
    value["effects"].append(
        {
            "kind": "unit",
            "state": "intent",
            "writer": dict(value["owner"]),
            "unit": "strongswan.service",
            "action": "stop",
        }
    )
    store.write(value)
    old = abandon(store)
    ops.BOOT.write_text("22222222-2222-2222-2222-222222222222")
    resumed = ops.Operation.begin(
        request=old["id"],
        config=old["config"],
        artifact=old["artifact"],
        predecessor=old["predecessor"],
        manager=manager,
        network=manager.network(),
        previous=ops.digest(old),
        journal=store,
    )
    assert store.read()["effects"][0]["state"] == "settled"
    resumed.complete()
    assert store.read()["state"] == "complete"


def test_same_boot_handoff_stop_resume_round_trips_and_rechecks_settlement(store):
    manager = Manager()
    operation = ops.Operation.begin(
        request="1" * 32,
        config="2" * 64,
        artifact="3" * 64,
        predecessor="4" * 64,
        manager=manager,
        network=manager.network(),
        journal=store,
        purpose="ha-handoff",
    )
    operation.intent("unit", unit="strongswan-swanctl.service", action="stop")
    operation.fail()
    old = abandon(store)

    def resume(value):
        return ops.Operation.begin(
            request=value["id"],
            config=value["config"],
            artifact=value["artifact"],
            predecessor=value["predecessor"],
            manager=manager,
            network=manager.network(),
            previous=ops.digest(value),
            journal=store,
            purpose="ha-handoff",
            resume_inhibited_quiescence=True,
        )

    manager.settled = False
    with pytest.raises(ops.OperationBlocked, match="unresolved"):
        resume(old)
    assert store.read() == old
    manager.settled = True
    resumed = resume(old)
    retired = store.read()
    assert retired["effects"][0]["state"] == "settled"
    assert "jobs" not in retired["effects"][0]
    assert retired["effects"][0]["writer"] != retired["owner"]
    resumed.service("stop", "strongswan.service")
    resumed.phase("install")
    resumed.fail()
    # Preserve the distinction between the original and successor writers.
    current = store.read()
    current["owner"]["pid"] = 999998
    current["effects"][1]["writer"]["pid"] = 999998
    store.write(current)
    manager.settled = False
    with pytest.raises(ops.OperationBlocked, match="unresolved"):
        resume(current)
    manager.settled = True
    resumed = resume(current)
    resumed.complete()
    assert store.read()["state"] == "complete"


@pytest.mark.parametrize("change", ["ordinary", "start", "same-writer", "accepted"])
def test_receiptless_settled_stop_exception_is_narrow(store, change):
    operation = begin(Manager(), store)
    value = operation.value
    value["purpose"] = "ha-handoff"
    value["effects"] = [
        {
            "kind": "unit",
            "state": "settled",
            "action": "stop",
            "unit": "strongswan.service",
            "writer": {**value["owner"], "pid": 999999},
        }
    ]
    if change == "ordinary":
        value["purpose"] = "ordinary"
    elif change == "start":
        value["effects"][0]["action"] = "start"
    elif change == "same-writer":
        value["effects"][0]["writer"] = value["owner"]
    else:
        value["effects"][0]["state"] = "accepted"
    store.write(value)
    with pytest.raises(ops.OperationBlocked, match="corrupt"):
        store.read()


def test_guard_is_durable_before_submission_and_blocks_after_lock_release(store):
    manager = Manager()
    with ops.mutation_lock():
        operation = begin(manager)
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    assert not manager.calls
    with pytest.raises(ops.OperationBlocked, match="in_progress"), ops.mutation_lock():
        pytest.fail("admitted a concurrent writer")
    operation.fail()
    abandon(store)
    with pytest.raises(ops.OperationBlocked, match="unresolved"):
        ops.require_idle()


def test_journal_creation_failure_has_no_service_effect(store, monkeypatch):
    manager = Manager()
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OSError):
        begin(manager)
    assert manager.calls == []


def test_exact_service_job_reference_and_successful_retirement(store):
    manager = Manager()
    operation = begin(manager)
    operation.service("stop", ops.MANAGEMENT[0])
    effect = store.read()["effects"][0]
    assert effect["jobs"][0][:2] == [7, "/job/7"]
    assert effect["state"] == "settled"
    assert manager.calls[0][1][-1] == "fail"
    operation.complete()
    ops.require_idle()
    assert store.read()["state"] == "complete"


@pytest.mark.parametrize("outcome", ["settles", "descendant", "leader", "identity"])
def test_local_command_requires_bounded_whole_group_settlement(store, monkeypatch, outcome):
    import signal
    import subprocess

    from nebius_vpngw.agent import local_commands

    manager = Manager()
    operation = begin(manager)
    owner = ops.identity()
    child = {**owner, "pid": 999998}
    clock = [100.0]
    waits = []
    signals = []
    probes = []

    def identity(pid=None):
        if pid == child["pid"]:
            if outcome == "identity":
                raise FileNotFoundError
            return child
        return owner

    class Process:
        pid = child["pid"]
        returncode = 0

        def communicate(self, **kwargs):
            assert store.read()["effects"][0]["state"] == "accepted"

        def wait(self, timeout=None):
            waits.append(timeout)
            if outcome == "leader":
                assert timeout is not None, "cleanup must not wait without a deadline"
                raise subprocess.TimeoutExpired("fixture", timeout)
            return 0

    def empty(value):
        assert value == child
        assert store.read()["effects"][0]["state"] == "accepted"
        probes.append(value)
        return outcome == "settles" and len(probes) >= 2

    monkeypatch.setattr(ops, "identity", identity)
    monkeypatch.setattr(ops, "process_group_empty", empty)
    monkeypatch.setattr(local_commands.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(local_commands.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    budget = local_commands.CommandBudget(101.0, operation=operation, effect_deadline=100.5)
    token = local_commands.CURRENT.set(budget)
    try:
        if outcome == "settles":
            assert local_commands.run(["fixture"]).returncode == 0
            budget.require_success()
            operation.complete()
            assert store.read()["state"] == "complete"
            assert len(probes) == 2
        else:
            with pytest.raises((RuntimeError, subprocess.TimeoutExpired, FileNotFoundError)):
                local_commands.run(["fixture"])
            with pytest.raises(RuntimeError):
                budget.require_success()
            assert store.read()["effects"][0]["state"] != "settled"
            with pytest.raises(ops.OperationBlocked, match="unresolved"):
                operation.complete()
            operation.fail()
            value = abandon(store)
            monkeypatch.setattr(ops, "alive", lambda value: False)
            assert not ops.recoverable(value, manager)
        assert waits and all(timeout is not None and 0 <= timeout <= 1 for timeout in waits)
        assert clock[0] <= 101.0
        assert signals[0] == (child["pid"], signal.SIGKILL)
    finally:
        local_commands.CURRENT.reset(token)


def test_lost_reply_is_not_retried_or_cancelled(store, monkeypatch):
    manager = Manager()
    operation = begin(manager)

    def lost(method, *args):
        manager.calls.append((method, args))
        assert store.read()["effects"][0]["state"] == "intent"
        raise TimeoutError

    monkeypatch.setattr(manager, "system", lost)
    with pytest.raises(TimeoutError):
        operation.service("restart", "frr.service")
    operation.fail()
    value = abandon(store)
    assert not ops.recoverable(value, manager)
    assert len(manager.calls) == 1


def test_timeout_keeps_guard_when_manager_owns_execution(store):
    manager = Manager()
    manager.settled = False
    operation = begin(manager)
    with pytest.raises(TimeoutError):
        operation.service("stop", ops.MANAGEMENT[0], timeout=0.01)
    operation.fail()
    value = abandon(store)
    assert not ops.recoverable(value, manager)
    manager.settled = True
    assert ops.recoverable(value, manager)
    before = store.path.read_bytes()
    assert ops.observation(manager)["status"] == "review_required"
    assert store.path.read_bytes() == before
    with pytest.raises(ops.OperationBlocked):
        begin(manager)
    replacement = ops.Operation.begin(
        request="5" * 32,
        config="2" * 64,
        artifact="3" * 64,
        predecessor="6" * 64,
        manager=manager,
        network=manager.network(),
        previous=ops.digest(value),
    )
    assert replacement.value["id"] == "5" * 32


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "9" * 32),
        ("config_sha256", "9" * 64),
        ("boot_id", "99999999-9999-9999-9999-999999999999"),
    ],
)
def test_child_join_checks_every_binding(store, field, value):
    operation = begin(Manager())
    operation.phase("reconcile")
    request = {"request_id": "1" * 32, "config_sha256": "2" * 64, "boot_id": ops.BOOT.read_text()}
    request[field] = value
    with pytest.raises(ops.OperationBlocked, match="child_rejected"):
        ops.Operation.join(request, "3" * 64, Manager())


def test_delayed_child_cannot_act_after_phase_or_owner_changes(store):
    operation = begin(Manager())
    operation.phase("reconcile")
    request = {"request_id": "1" * 32, "config_sha256": "2" * 64, "boot_id": ops.BOOT.read_text()}
    child = ops.Operation.join(request, "3" * 64, Manager())
    operation.phase("activate")
    with pytest.raises(ops.OperationBlocked, match="owner_changed"):
        child.intent("process")
    with pytest.raises(ops.OperationBlocked):
        child.complete()
    operation.phase("reconcile")
    abandon(store)
    with pytest.raises(ops.OperationBlocked):
        child.intent("process")


def test_stale_child_cannot_overwrite_parent_failure(store):
    operation = begin(Manager())
    operation.phase("reconcile")
    stale = operation.check()
    other = ops.Operation(store.read(), Manager())
    other.fail()
    stale["effects"].append({"kind": "process", "state": "intent", "writer": ops.identity()})
    with pytest.raises(ops.OperationBlocked, match="owner_changed"):
        operation.save(stale)
    assert store.read()["state"] == "unresolved"


@pytest.mark.parametrize("data", ["{}", "broken", '{"schema":"future"}'])
def test_corrupt_or_unknown_journal_fails_closed(store, data):
    store.path.parent.mkdir(mode=0o700)
    store.path.write_text(data)
    store.path.chmod(0o600)
    with pytest.raises(ops.OperationBlocked, match="corrupt"):
        ops.require_idle()


def test_symlink_and_world_readable_journal_are_rejected(store, tmp_path):
    store.path.parent.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.write_text("{}")
    store.path.symlink_to(target)
    with pytest.raises(ops.OperationBlocked, match="unsafe"):
        store.read()
    store.path.unlink()
    store.path.write_text("{}")
    store.path.chmod(0o644)
    with pytest.raises(ops.OperationBlocked, match="unsafe"):
        store.read()


def test_failed_completion_does_not_unblock_writers(store, monkeypatch):
    operation = begin(Manager())
    original = store._write

    def fail_complete(value):
        if value["state"] == "complete":
            raise OSError("completion failure")
        original(value)

    operation.journal = store
    monkeypatch.setattr(store, "_write", fail_complete)
    with pytest.raises(OSError):
        operation.complete()
    assert store.read()["state"] == "unresolved"


def test_reboot_requires_fresh_network_and_manager_proof(store):
    manager = Manager()
    operation = begin(manager)
    operation.intent("reload")
    value = abandon(store)
    ops.BOOT.write_text("22222222-2222-2222-2222-222222222222")
    manager.settled = False
    assert not ops.recoverable(value, manager)
    manager.settled = True
    assert ops.recoverable(value, manager)
    with pytest.raises(ops.OperationBlocked):
        ops.require_idle()


def test_pending_netplan_is_not_settled_by_empty_systemd_jobs(store):
    manager = Manager()
    operation = begin(manager)
    index = operation.before_netplan()
    operation.update(index, state="accepted")
    operation.fail()
    value = abandon(store)
    manager.network_value["links"][0]["name"] = "changed"
    assert not ops.recoverable(value, manager)
    manager.network_value = value["effects"][0]["network"]
    assert ops.recoverable(value, manager)


def test_streamed_source_never_imports_installed_agent(tmp_path):
    import subprocess
    import sys

    code = ops.streamed_bootstrap() + "print(_ops.SCHEMA); assert 'nebius_vpngw' not in sys.modules"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ops.SCHEMA


def test_completed_record_with_pending_effect_is_corrupt(store):
    operation = begin(Manager())
    operation.intent("reload")
    value = store.read()
    value["state"] = "complete"
    store.write(value)
    with pytest.raises(ops.OperationBlocked, match="corrupt"):
        ops.require_idle()


def test_child_cannot_follow_parent_into_another_phase(store):
    manager = Manager()
    parent = begin(manager)
    parent.phase("reconcile")
    child = ops.Operation.join(
        {"request_id": "1" * 32, "config_sha256": "2" * 64, "boot_id": ops.BOOT.read_text()},
        "3" * 64,
        manager,
    )
    parent.phase("verify")
    with pytest.raises(ops.OperationBlocked):
        child.intent("unit", unit="frr.service", action="restart")
    assert store.read()["effects"] == []


def test_handoff_cannot_be_recovered_by_ordinary_apply(store):
    manager = Manager()
    operation = ops.Operation.begin(
        request="1" * 32,
        config="2" * 64,
        artifact="3" * 64,
        predecessor="4" * 64,
        manager=manager,
        network=manager.network(),
        purpose="ha-handoff",
    )
    operation.fail()
    value = abandon(store)
    assert not ops.recoverable(value, manager)
    with pytest.raises(ops.OperationBlocked):
        ops.Operation.begin(
            request="5" * 32,
            config="2" * 64,
            artifact="3" * 64,
            predecessor="4" * 64,
            manager=manager,
            network=manager.network(),
            previous=ops.digest(value),
        )


def test_first_directory_is_fsynced_before_any_submission(store, monkeypatch):
    events = []
    original = os.fsync

    def fsync(fd):
        events.append(os.fstat(fd).st_ino)
        return original(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    manager = Manager()
    operation = begin(manager)
    assert store.path.parent.parent.stat().st_ino in events
    assert events.index(store.path.parent.parent.stat().st_ino) < events.index(
        store.path.stat().st_ino
    )
    operation.service("stop", ops.MANAGEMENT[0])


def test_read_only_systemctl_status_is_bounded_without_service_submission(store, monkeypatch):
    from subprocess import CompletedProcess

    from nebius_vpngw.agent import local_commands

    manager = Manager()
    operation = begin(manager)
    calls = []
    monkeypatch.setattr(
        local_commands,
        "_bounded_run",
        lambda args, **kwargs: calls.append(args) or CompletedProcess(args, 0, "active\n", ""),
    )
    token = local_commands.CURRENT.set(
        local_commands.CommandBudget(time.monotonic() + 20, operation=operation)
    )
    try:
        assert local_commands.run(["systemctl", "is-active", "frr"]).stdout == "active\n"
    finally:
        local_commands.CURRENT.reset(token)
    assert calls == [["systemctl", "is-active", "frr"]]
    assert not manager.calls


@pytest.mark.parametrize("can_reload,expected", [(True, "reload"), (False, "restart")])
def test_required_frr_selects_one_action_before_submission(
    store, monkeypatch, can_reload, expected
):
    from nebius_vpngw.agent import local_commands

    manager = Manager()
    manager.unit = lambda name: {"ActiveState": "active", "CanReload": can_reload}
    operation = begin(manager)
    token = local_commands.CURRENT.set(
        local_commands.CommandBudget(time.monotonic() + 20, operation=operation)
    )
    try:
        local_commands.run(["systemctl", "reload-or-restart", "frr"])
    finally:
        local_commands.CURRENT.reset(token)
    assert [args[2] for method, args in manager.calls if method == "EnqueueUnitJob"] == [expected]


def test_fifo_journal_is_rejected_without_blocking(store):
    store.path.parent.mkdir(mode=0o700)
    os.mkfifo(store.path, mode=0o600)
    with pytest.raises(ops.OperationBlocked, match="unsafe"):
        store.read()


@pytest.mark.parametrize(
    "effect",
    [
        {"kind": "unit", "action": "start", "unit": "foreign.service", "state": "intent"},
        {"kind": "unit", "action": "start", "unit": "frr.service", "state": "accepted", "jobs": []},
        {"kind": "process", "state": "accepted"},
        {"kind": "reload", "state": "settled", "unexpected": True},
    ],
)
def test_malformed_effects_fail_closed(store, effect):
    begin(Manager())
    value = store.read()
    value["effects"] = [dict(effect, writer=ops.identity())]
    store.write(value)
    with pytest.raises(ops.OperationBlocked, match="corrupt"):
        ops.require_idle()


def test_pending_guard_blocks_health_and_periodic_route_repairs(store, monkeypatch):
    from unittest.mock import Mock

    from nebius_vpngw.agent import routing_guard
    from nebius_vpngw.agent.tunnel_health_monitor import TunnelHealthMonitor

    begin(Manager())
    monitor = TunnelHealthMonitor()
    repair = Mock(side_effect=AssertionError("mutation reached"))
    monkeypatch.setattr(monitor, "_restart_tunnel_locked", repair)
    assert monitor.restart_tunnel("fixture-tunnel") is False
    repair.assert_not_called()
    monkeypatch.setattr(routing_guard, "LOCK_PATH", ops.ROUTING_LOCK)
    monkeypatch.setattr(routing_guard, "_enforce_routing_invariants_locked", repair)
    routing_guard.enforce_periodic_routing_invariants({})
    repair.assert_not_called()


def test_pending_guard_leaves_daemon_waiting_and_rejects_forced_reload(
    store, monkeypatch, tmp_path
):
    from unittest.mock import Mock

    from nebius_vpngw.agent import main as agent_main
    from nebius_vpngw.agent import ordinary

    path = tmp_path / "config.yaml"
    path.write_text("version: 1\ngateway: {}\nconnections: []\n")
    monkeypatch.setattr(agent_main, "CONFIG_PATH", path)
    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda path: None)
    ops.ROUTING_LOCK.parent.mkdir(exist_ok=True)
    monkeypatch.setattr(
        agent_main,
        "acquire_routing_lock",
        lambda **kwargs: os.open(ops.ROUTING_LOCK, os.O_CREAT | os.O_RDWR, 0o600),
    )
    reconcile = Mock(side_effect=AssertionError("mutation reached"))
    monkeypatch.setattr(ordinary, "reconcile_locked", reconcile)
    begin(Manager())
    agent = agent_main.Agent()
    agent.reload()
    with pytest.raises(ops.OperationBlocked):
        agent.reload(force_reconcile=True)
    reconcile.assert_not_called()


def test_handoff_mutation_lock_waits_out_transient_contention(store, monkeypatch):
    # A one-shot operator handoff must not fail instantly when the agent momentarily
    # holds the guest writer lock during a reconcile cycle: with wait>0 it polls
    # (bounded backoff) and acquires once the holder releases.
    import fcntl

    clock = [0.0]
    monkeypatch.setattr(ops.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ops.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(ops, "require_idle", lambda: None)
    attempts: list[int] = []

    def flaky(_fd: int, _operation: int) -> None:
        attempts.append(_operation)
        if len(attempts) < 3:
            raise BlockingIOError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(fcntl, "flock", flaky)
    with ops.mutation_lock(wait=5.0):
        pass
    assert len(attempts) == 3


def test_mutation_lock_default_fails_fast_on_contention(store, monkeypatch):
    # Default (agent-side) callers keep the non-blocking behavior: fail immediately
    # when another holder is active, so a busy cycle is skipped rather than blocked.
    import fcntl

    monkeypatch.setattr(ops, "require_idle", lambda: None)

    def busy(_fd: int, _operation: int) -> None:
        raise BlockingIOError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(fcntl, "flock", busy)
    with pytest.raises(BlockingIOError), ops.mutation_lock():
        pytest.fail("acquired a contended lock without waiting")
