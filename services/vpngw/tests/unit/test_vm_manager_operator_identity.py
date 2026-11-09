"""Every management probe uses one resolved SSH identity, including overrides."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy.ssh_client_auth import OPERATOR_IDENTITY, SSHClientAuth
from nebius_vpngw.deploy.vm_manager import VMManager


@pytest.mark.parametrize("probe", ["network", "health", "existing-ha"])
@pytest.mark.parametrize("selection", ["operator", "configured", "legacy"])
def test_management_probes_select_one_identity(monkeypatch, probe, selection):
    configured_path = Path("/fixture/configured-key")
    selected_path = Path("/fixture/selected-key")
    exact = SSHClientAuth(
        source="test-exact", _key_type="ssh-ed25519", _key_data="fixture", _key_path=selected_path
    )
    policy = SimpleNamespace(
        assert_current=lambda: None,
        known_hosts_file=Path("/fixture/known-hosts"),
        pin_target_for=lambda name: name,
    )
    manager = VMManager(
        "project-test",
        "eu-north1",
        management_key_path=configured_path,
        management_public_key="configured-public" if selection == "configured" else None,
        ssh_policy=policy,
    )
    if selection == "configured":
        manager._ssh_client_auth = exact
    token = OPERATOR_IDENTITY.set(exact if selection == "operator" else None)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        text = kwargs.get("text", False)
        return subprocess.CompletedProcess(
            command, 0, stdout="" if text else b"", stderr="" if text else b""
        )

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("time.sleep", lambda _: None)
    try:
        if probe == "network":
            assert manager.wait_for_vm_network("gateway-0", "192.0.2.10", timeout=3)
        elif probe == "health":
            assert manager.check_vm_health("gateway-0", "192.0.2.10")["reachable"]
        else:
            manager.verify_vm_ha_existing_identities({"gateway-0": "192.0.2.10"})
    finally:
        OPERATOR_IDENTITY.reset(token)
    assert commands
    expected = configured_path if selection == "legacy" else selected_path
    excluded = selected_path if selection == "legacy" else configured_path
    for command in commands:
        assert command.count("-i") == 1
        assert command[command.index("-i") + 1] == str(expected)
        assert str(excluded) not in command
        assert "StrictHostKeyChecking=yes" in command
        assert "HostKeyAlias=gateway-0" in command
        if selection != "legacy":
            assert "IdentitiesOnly=yes" in command
