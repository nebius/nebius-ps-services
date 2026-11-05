from __future__ import annotations

import fcntl
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.unit.test_ipsec_observation import sas

from nebius_vpngw import ordinary_operations as ops
from nebius_vpngw import tunnel_restart as restart


class Clock:
    value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


@pytest.fixture
def guest(tmp_path, monkeypatch, ordinary_operation_guest):
    config = {
        "gateway": {"local_asn": 65001},
        "connections": [
            {
                "routing_mode": "bgp",
                "tunnels": [
                    {"name": "edge", "inner_remote_ip": "192.0.2.2"},
                    {"name": "backup", "inner_remote_ip": "192.0.2.2"},
                ],
            }
        ],
    }
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(config))
    monkeypatch.setattr(restart, "CONFIG", path)
    monkeypatch.setattr(restart.shutil, "which", lambda _: "/swanctl")
    return config


class Runner:
    def __init__(
        self, clock, *, ready_at=0, fail_termination=False, detach=False, fail_shutdown=False
    ):
        self.clock = clock
        self.ready_at = ready_at
        self.fail_termination = fail_termination
        self.detach = detach
        self.fail_shutdown = fail_shutdown
        self.commands = []
        self.states = {"edge": "ready", "backup": "ready"}

    def __call__(self, args, *, timeout):
        assert timeout > 0
        self.commands.append((self.clock.now(), args, timeout))
        if "--terminate" in args:
            if not self.fail_termination:
                self.states[args[args.index("--ike") + 1]] = "down"
        if "--initiate" in args:
            self.states[args[args.index("--child") + 1]] = "starting"
            if self.detach:
                self.clock.sleep(timeout)
                raise subprocess.TimeoutExpired(args, timeout)
        if "--list-sas" in args:
            names = [args[args.index("--ike") + 1]] if "--ike" in args else list(self.states)
            raw = ""
            for unique, name in enumerate(names, 1):
                state = self.states[name]
                if state == "down" or state == "starting" and self.clock.now() < self.ready_at:
                    continue
                raw += (
                    sas(name=name, ifid="00000064" if name == "edge" else "00000065")
                    .replace("uniqueid=1", f"uniqueid={unique}")
                    .replace(" list-sas reply {}", "")
                )
            return SimpleNamespace(returncode=0, stdout=raw + " list-sas reply {}")
        if args[-1].startswith("neighbor ") and self.fail_shutdown:
            raise InterruptedError("simulated cancellation during shutdown")
        return SimpleNamespace(returncode=0, stdout="")


def run(guest, runner, clock, selector="edge"):
    value = restart.request(guest, selector)
    operation = restart.Restart(value["budget"], runner=runner, clock=clock.now, sleep=clock.sleep)
    return restart.execute(value, operation=operation)


def test_32_second_recovery_after_detach_submits_once(guest):
    clock = Clock()
    runner = Runner(clock, ready_at=32, detach=True)
    result = run(guest, runner, clock)
    assert result["status"] == "complete"
    assert clock.now() >= 32
    assert sum("--initiate" in args for _, args, _ in runner.commands) == 1
    assert result["reset_peers"] == ["192.0.2.2"]


def test_unconfirmed_termination_never_initiates(guest):
    clock = Clock()
    runner = Runner(clock, fail_termination=True)
    assert run(guest, runner, clock)["status"] == "incomplete"
    assert not any("--initiate" in args for _, args, _ in runner.commands)


def test_bounded_observation_never_retries_or_probes_after_deadline(guest):
    clock = Clock()
    runner = Runner(clock, ready_at=10000, detach=True)
    assert run(guest, runner, clock)["status"] == "incomplete"
    assert sum("--initiate" in args for _, args, _ in runner.commands) == 1
    assert clock.now() <= 87  # settle 2 + detached initiate 25 + observation 60
    assert all(
        at < 87 and at + timeout <= 87
        for at, args, timeout in runner.commands
        if "--list-sas" in args and at > 25
    )


def test_shared_peer_resets_once_and_budget_scales_per_target(guest):
    clock = Clock()
    runner = Runner(clock)
    value = restart.request(guest, "all")
    assert value["budget"] == 5 + 2 * 127 + 31
    result = run(guest, runner, clock, "all")
    assert result["status"] == "complete"
    assert result["completed_tunnels"] == ["edge", "backup"]
    assert sum(args[-1] == "neighbor 192.0.2.2 shutdown" for _, args, _ in runner.commands) == 1


def test_cancelled_shutdown_restores_under_same_real_writer_lock(guest):
    clock = Clock()
    runner = Runner(clock, fail_shutdown=True)
    checked = []

    def locked(args, **kwargs):
        with Path(ops.ROUTING_LOCK).open("r") as stream, pytest.raises(BlockingIOError):
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        checked.append(args)
        return runner(args, **kwargs)

    assert run(guest, locked, clock)["status"] == "incomplete"
    assert checked[-1][-1] == "no neighbor 192.0.2.2 shutdown"
    with Path(ops.ROUTING_LOCK).open("r") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_drift_and_partial_target_request_reject_before_any_command(guest):
    value = restart.request(guest, "all")
    value["targets"].pop()
    clock = Clock()
    runner = Runner(clock)
    operation = restart.Restart(1000, runner=runner, clock=clock.now, sleep=clock.sleep)
    assert restart.execute(value, operation=operation)["status"] == "incomplete"
    assert runner.commands == []
    value = restart.request(guest, "edge")
    value["config_digest"] = "0" * 64
    assert restart.execute(value, operation=operation)["status"] == "incomplete"
    assert runner.commands == []


def test_streamed_package_uses_same_request_and_observation(guest):
    namespace = {}
    exec(restart.streamed_source(), namespace)
    streamed = namespace["_module"]
    assert streamed.request(guest, "all") == restart.request(guest, "all")
    assert streamed.observation.child_ready(sas(), "edge", 100)
    assert not streamed.observation.child_ready("list-sas reply {}", "edge", 100)


def test_no_shutdown_without_restore_reservation():
    clock = Clock()
    runner = Runner(clock)
    operation = restart.Restart(30, runner=runner, clock=clock.now, sleep=clock.sleep)
    assert not operation.reset_peer("192.0.2.2", 65001)
    assert runner.commands == []


def test_signal_during_readiness_stops_subsequent_effects(guest):
    clock = Clock()
    runner = Runner(clock)
    operation = restart.Restart(500, clock=clock.now, sleep=clock.sleep)
    initiated = False

    def cancel_probe(args, **kwargs):
        nonlocal initiated
        if "--initiate" in args:
            initiated = True
        if "--list-sas" in args and initiated:
            operation.cancelled_signal(15, None)
        return runner(args, **kwargs)

    operation.runner = cancel_probe
    assert (
        restart.execute(restart.request(guest, "all"), operation=operation)["status"]
        == "incomplete"
    )
    assert sum("--initiate" in args for _, args, _ in runner.commands) == 1
    assert not any(args[0] == "vtysh" for _, args, _ in runner.commands)


@pytest.mark.parametrize("stage_end,deadline", [(1, 5), (5, 1)])
def test_readiness_validation_must_finish_before_stage_and_global_deadlines(
    monkeypatch, stage_end, deadline
):
    clock = Clock()
    operation = restart.Restart(deadline, runner=Runner(clock), clock=clock.now, sleep=clock.sleep)
    child_ready = restart.observation.child_ready

    def slow_validation(*args):
        ready = child_ready(*args)
        clock.value = min(stage_end, deadline)
        return ready

    monkeypatch.setattr(restart.observation, "child_ready", slow_validation)
    assert not operation.wait({"name": "edge", "if_id": 100}, stage_end)


def test_completion_rechecks_deadline_after_final_wait(guest, monkeypatch):
    clock = Clock()
    operation = restart.Restart(500, runner=Runner(clock), clock=clock.now, sleep=clock.sleep)
    wait = operation.wait

    def preempt_after_wait(target, end, **kwargs):
        result = wait(target, end, **kwargs)
        if end - clock.now() <= 5 and not kwargs:
            clock.value = operation.deadline
        return result

    monkeypatch.setattr(operation, "wait", preempt_after_wait)
    assert (
        restart.execute(restart.request(guest, "edge"), operation=operation)["status"]
        == "incomplete"
    )
