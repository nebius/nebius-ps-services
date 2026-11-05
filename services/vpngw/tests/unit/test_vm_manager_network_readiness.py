"""Deployment readiness follows the authenticated management path, not ICMP."""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nebius_vpngw.deploy.vm_manager import VMManager


@pytest.fixture
def management_probe(monkeypatch):
    manager = object.__new__(VMManager)
    manager._management_key_path = Path("/fixture/management-key")
    manager._management_public_key = "fixture-public-key"
    manager._ssh_policy = object()
    manager._require_ssh_client_auth = Mock(return_value=object())
    build = Mock(return_value=["ssh", "-o", "StrictHostKeyChecking=yes"])
    monkeypatch.setattr("nebius_vpngw.deploy.vm_manager.build_openssh_base_command", build)
    clock = SimpleNamespace(now=0.0)

    def advance(seconds):
        clock.now += seconds

    monkeypatch.setattr("time.monotonic", lambda: clock.now)
    monkeypatch.setattr("time.time", lambda: clock.now)
    monkeypatch.setattr("time.sleep", advance)
    return manager, build, clock, advance


def test_authenticated_ssh_succeeds_when_icmp_is_blocked(management_probe, monkeypatch):
    manager, build, _, _ = management_probe
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0 if command[0] == "ssh" else 1, stderr="")

    monkeypatch.setattr("subprocess.run", run)
    assert manager.wait_for_vm_network("gateway-0", "192.0.2.10", timeout=3)
    assert len(commands) == 1 and commands[0][-2:] == ["ubuntu@192.0.2.10", "true"]
    assert build.call_args.kwargs["policy"] is manager._ssh_policy
    assert build.call_args.kwargs["hostname"] == "gateway-0"
    assert build.call_args.kwargs["client_auth"] is manager._require_ssh_client_auth.return_value
    assert build.call_args.kwargs["key_path"] is None


def test_ping_response_cannot_authorize_deployment_without_ssh(management_probe, monkeypatch):
    manager, _, clock, advance = management_probe
    deadlines = []

    def run(command, **kwargs):
        deadlines.append(kwargs["timeout"])
        advance(kwargs["timeout"])
        return SimpleNamespace(returncode=0 if command[0] == "ping" else 255, stderr="refused")

    monkeypatch.setattr("subprocess.run", run)
    assert not manager.wait_for_vm_network("gateway-0", "192.0.2.10", timeout=3)
    assert clock.now <= 3 and all(value <= 3 for value in deadlines)


@pytest.mark.parametrize(
    "error",
    ["Host key verification failed.", "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!"],
)
def test_identity_failure_is_terminal(management_probe, monkeypatch, error):
    manager, _, _, _ = management_probe
    run = Mock(return_value=SimpleNamespace(returncode=255, stderr=error))
    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(RuntimeError, match="SSH host identity verification failed"):
        manager.wait_for_vm_network("gateway-0", "192.0.2.10", timeout=3)
    assert run.call_count == 1


def test_transient_ssh_failure_preserves_progress_and_selected_user(management_probe, monkeypatch):
    manager, _, _, _ = management_probe
    run = Mock(
        side_effect=[
            SimpleNamespace(returncode=255, stderr="Connection refused"),
            SimpleNamespace(returncode=0, stderr=""),
        ]
    )
    progress = Mock()
    monkeypatch.setattr("subprocess.run", run)
    assert manager.wait_for_vm_network(
        "gateway-0", "192.0.2.10", timeout=3, username="operator", progress_callback=progress
    )
    assert run.call_count == 2 and progress.call_count == 1
    assert all(call.args[0][-2:] == ["operator@192.0.2.10", "true"] for call in run.call_args_list)


def test_expired_budget_does_not_open_a_connection(management_probe, monkeypatch):
    manager, build, _, _ = management_probe
    run = Mock()
    monkeypatch.setattr("subprocess.run", run)
    assert not manager.wait_for_vm_network("gateway-0", "192.0.2.10", timeout=0)
    run.assert_not_called()
    build.assert_not_called()


def test_probe_timeout_consumes_only_remaining_budget(management_probe, monkeypatch):
    manager, _, clock, advance = management_probe
    probes = []

    def run(command, **kwargs):
        probes.append(kwargs["timeout"])
        advance(kwargs["timeout"])
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("subprocess.run", run)
    assert not manager.wait_for_vm_network("gateway-0", "192.0.2.10", timeout=12)
    assert probes == [10, 1] and clock.now == 12


def test_late_ssh_success_does_not_extend_deadline(management_probe, monkeypatch):
    manager, _, _, advance = management_probe

    def run(command, **kwargs):
        advance(kwargs["timeout"] + 0.1)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("subprocess.run", run)
    assert not manager.wait_for_vm_network("gateway-0", "192.0.2.10", timeout=3)
