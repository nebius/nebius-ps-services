"""Key-free decision evidence must preserve native commands and admission timing."""

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.unit.test_ha_tunnel_restart import restart_runtime
from tests.unit.test_ha_tunnel_selection import (
    AUTH,
    Effects,
    RecoveryClock,
    config,
    observation,
    recovery_selector,
    recovery_tick,
)
from tests.unit.test_ha_tunnel_selection import (
    test_native_default_backup_is_gateway_only_with_exact_path_evidence as native_probe_case,
)

from nebius_vpngw.agent import vm_ha_rearm
from nebius_vpngw.agent.vm_ha import diagnostics
from nebius_vpngw.agent.vm_ha.tunnel_observation import path_health
from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime, NativeTunnelEffects
from nebius_vpngw.agent.vm_ha.tunnels import Health
from nebius_vpngw.agent.vm_ha_controller import DataPlaneMode
from nebius_vpngw.ha_tunnel_policy import connection_policies


def events(capsys):
    output = capsys.readouterr()
    assert "VPNGW_TUNNEL_DIAGNOSTIC " not in output.out
    return [
        json.loads(line.split(" ", 1)[1])
        for line in output.err.splitlines()
        if line.startswith("VPNGW_TUNNEL_DIAGNOSTIC ")
    ]


def test_sink_redaction_sequence_stderr_and_failure(capsys, monkeypatch):
    diagnostics.diagnostic(
        "test", reason="safe", config="SYNTHETIC_SECRET", target="raw secret text"
    )
    diagnostics.diagnostic("test", error_type="RuntimeError", stdout="SYNTHETIC_SECRET")
    records = events(capsys)
    assert records[1]["sequence"] == records[0]["sequence"] + 1
    assert all(r["pid"] > 0 and r["monotonic"] > 0 for r in records)
    assert "SYNTHETIC_SECRET" not in json.dumps(records)
    assert "target" not in records[0]

    def unavailable(*args, **kwargs):
        raise OSError("SYNTHETIC_SECRET")

    monkeypatch.setattr(diagnostics, "print", unavailable, raising=False)
    diagnostics.diagnostic("test")


def test_timing_confirmations_and_sink_failure_preserve_decisions(tmp_path, capsys, monkeypatch):
    effects, clock = Effects(), RecoveryClock()
    effects.health["preferred"] = Health.DOWN
    selector = recovery_selector(tmp_path, effects, clock)
    for second in range(31):
        recovery_tick(selector, clock, second)
    assert effects.calls == [("probe", "backup")]
    recovery_tick(selector, clock, 35)
    records = events(capsys)
    assert any(r.get("reason") == "continuous-loss-delay" and r["age"] == 29 for r in records)
    assert [
        r["confirmations"] for r in records if r["event"] == "static-recovery-confirmation"
    ] == [1, 2]
    assert any(r["event"] == "static-recovery-admitted" for r in records)

    def unavailable(*args, **kwargs):
        raise OSError("SYNTHETIC_SECRET")

    monkeypatch.setattr(diagnostics, "print", unavailable, raising=False)
    for second in range(36, 40):
        recovery_tick(selector, clock, second)
    assert selector.state["selected"] == {"site": "backup"}
    assert effects.calls == [
        ("probe", "backup"),
        ("probe", "backup"),
        ("suspend", "site"),
        ("activate", "backup"),
    ]


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("gap", "sample-gap"),
        ("backwards", "clock-backwards"),
        ("binding", "binding-changed"),
        ("healthy", "selected-not-down"),
        ("unknown", "selected-not-down"),
        ("authority", "inactive-authority"),
    ],
)
def test_continuity_reset_reason(tmp_path, capsys, fault, reason):
    effects, clock = Effects(), RecoveryClock()
    selector = recovery_selector(tmp_path, effects, clock)
    selector._recover_static(AUTH, {"site": "down"}, 1000)
    events(capsys)
    clock.now = 1
    if fault == "gap":
        clock.now = 11
    elif fault == "backwards":
        clock.now = -1
    selected_health = "down"
    if fault == "healthy":
        selected_health = "healthy"
    elif fault == "unknown":
        selected_health = "unknown"
    health = {"site": selected_health}
    if fault == "binding":
        selector.state["selected"]["site"] = "backup"
    if fault == "authority":
        effects.current = None
    selector._recover_static(AUTH, health, 1001)
    assert any(r.get("reason") == reason for r in events(capsys))


@pytest.mark.parametrize("outcome", [0, 1, "timeout", "deadline"])
def test_ping_returncode_timeout_and_redaction(tmp_path, capsys, outcome):
    calls = []

    def runner(argv, timeout):
        calls.append(tuple(argv))
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout, output="SYNTHETIC_SECRET")
        return SimpleNamespace(returncode=outcome, stdout="SYNTHETIC_SECRET")

    effects = NativeTunnelEffects(
        config(),
        tmp_path,
        lambda a: True,
        active_revalidate=lambda a: True,
        command_runner=runner,
        clock=lambda: 100,
    )
    if outcome == "deadline":
        effects.effect_deadline = 99
    if outcome == 0:
        assert effects.run(("ping", "SYNTHETIC_SECRET")) == "SYNTHETIC_SECRET"
    else:
        with pytest.raises(RuntimeError):
            effects.run(("ping", "SYNTHETIC_SECRET"))
    record = events(capsys)[-1]
    assert "SYNTHETIC_SECRET" not in json.dumps(record)
    if isinstance(outcome, int):
        assert record["returncode"] == outcome
    elif outcome == "timeout":
        assert record["error_type"] == "TimeoutExpired"
    else:
        assert record["reason"] == "deadline-exhausted"
        assert calls == []


@pytest.mark.parametrize(
    "fault,reason",
    [
        (None, "after-inner-ping"),
        ("missing-source", "inner-source-not-assigned"),
        ("wrong-interface", "native-health-not-healthy"),
        ("unknown-auth", "native-health-not-healthy"),
        ("missing-child", "native-health-not-healthy"),
        ("wrong-selectors", "native-health-not-healthy"),
        ("ping-failed", "exception"),
    ],
)
def test_default_probe_unchanged_commands_and_diagnostic(
    tmp_path, monkeypatch, capsys, fault, reason
):
    native_probe_case(tmp_path, monkeypatch, fault)
    records = events(capsys)
    assert any(r.get("reason") == reason for r in records)
    if fault is None:
        assert any(r["event"] == "inner-ping-started" for r in records)
        assert any(r["event"] == "inner-ping-command" and r["returncode"] == 0 for r in records)


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("link", "xfrm-link-missing"),
        ("state", "missing-matching-esp-states"),
        ("policy", "missing-policy-coverage"),
        ("parse", "parse-or-evidence-error"),
    ],
)
def test_native_path_rejection_reason(capsys, fault, reason):
    value = observation()
    policy = connection_policies(config())[0]
    if fault == "link":
        value["links"] = []
    elif fault == "state":
        value["states"] = ""
    elif fault == "policy":
        value["policies"] = ""
    else:
        value["sas"] = "SYNTHETIC_SECRET"
    assert path_health(policy.paths[0], policy, **value) in {Health.DOWN, Health.UNKNOWN}
    records = events(capsys)
    assert any(r.get("reason") == reason for r in records)
    assert "SYNTHETIC_SECRET" not in json.dumps(records)


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("service", "service-unhealthy"),
        ("sample", "sample-incomplete"),
        ("peer", "peer-unavailable"),
        ("stale", "peer-receipt-stale"),
    ],
)
def test_authority_gate_reason(tmp_path, capsys, fault, reason):
    runtime, _, _, _ = restart_runtime(tmp_path, DataPlaneMode.ACTIVE)
    snapshot = runtime.controller.snapshots.last_snapshot
    if fault == "service":
        snapshot = replace(snapshot, readiness=replace(snapshot.readiness, service_healthy=False))
    elif fault == "sample":
        snapshot = replace(snapshot, readiness=replace(snapshot.readiness, sample_complete=False))
    elif fault == "peer":
        snapshot = replace(snapshot, peer_heartbeat=None)
    else:
        snapshot = replace(snapshot, peer_received_at=80)
    runtime.controller.snapshots.observe = lambda: snapshot
    assert runtime.authority() is None
    assert any(reason in r.get("reasons", []) for r in events(capsys))


def test_steps_always_emit_start_end_and_skip(capsys, monkeypatch):
    runtime = object.__new__(ControllerTunnelRuntime)
    runtime.cfg = {"connections": []}
    runtime.step()
    records = events(capsys)
    assert [r["event"] for r in records] == ["step-started", "step-skipped", "step-finished"]
    assert records[1]["reason"] == "no-policies"
    runtime.cfg = config()
    runtime.state_dir = Path("/unused")
    monkeypatch.setattr(vm_ha_rearm, "_acquire_rearm_lock", lambda *args, **kwargs: None)
    runtime.step()
    records = events(capsys)
    assert records[1]["reason"] == "rearm-lock-busy"
    assert records[-1]["event"] == "step-finished"


def test_step_exception_preserved_without_error_text(capsys, monkeypatch):
    runtime = object.__new__(ControllerTunnelRuntime)

    def failure():
        raise ValueError("SYNTHETIC_SECRET")

    monkeypatch.setattr(runtime, "_step", failure)
    with pytest.raises(ValueError, match="SYNTHETIC_SECRET"):
        runtime.step()
    records = events(capsys)
    assert [r["event"] for r in records] == ["step-started", "step-failed", "step-finished"]
    assert records[1]["error_type"] == "ValueError"
    assert "SYNTHETIC_SECRET" not in json.dumps(records)


def test_apply_lock_skip_is_recorded_without_snapshot(capsys, monkeypatch, tmp_path):
    runtime = object.__new__(ControllerTunnelRuntime)
    runtime.cfg = config()
    runtime.state_dir = tmp_path
    descriptor = os.open(tmp_path / "lock", os.O_CREAT, 0o600)
    monkeypatch.setattr(vm_ha_rearm, "_acquire_rearm_lock", lambda *args, **kwargs: descriptor)
    runtime.controller = SimpleNamespace(
        snapshots=SimpleNamespace(
            providers=SimpleNamespace(apply_lock_operation_id=lambda: "apply")
        )
    )
    runtime.step()
    records = events(capsys)
    assert records[1]["reason"] == "apply-locked"
    assert records[-1]["event"] == "step-finished"
