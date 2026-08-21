from __future__ import annotations

from pathlib import Path

import pytest

from nebius_vpngw.agent import firewall_manager


def _redirect_runtime_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firewall_manager, "PEER_IPS_FILE", tmp_path / "peers")
    monkeypatch.setattr(firewall_manager, "MGMT_CIDRS_FILE", tmp_path / "management")
    monkeypatch.setattr(firewall_manager, "LOCAL_PREFIXES_FILE", tmp_path / "local")


def test_required_firewall_reload_runs_even_when_inputs_are_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _redirect_runtime_files(tmp_path, monkeypatch)
    config = {
        "gateway": {"local_prefixes": ["10.0.0.0/24"]},
        "connections": [
            {"tunnels": [{"remote_public_ip": "192.0.2.10"}]},
        ],
    }
    calls = 0

    def reload() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(firewall_manager, "reload_firewall", reload)

    firewall_manager.update_firewall_from_config(config)
    firewall_manager.update_firewall_from_config(config, require_reload=True)

    assert calls == 2


def test_required_firewall_reload_failure_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _redirect_runtime_files(tmp_path, monkeypatch)
    monkeypatch.setattr(firewall_manager, "reload_firewall", lambda: False)

    with pytest.raises(RuntimeError, match="required firewall reload failed"):
        firewall_manager.update_firewall_from_config(
            {"connections": []},
            require_reload=True,
        )


def test_vm_ha_controller_has_a_private_writable_temporary_directory() -> None:
    unit = (
        Path(__file__).parents[2]
        / "src/nebius_vpngw/systemd/nebius-vpngw-vm-ha.service"
    ).read_text(encoding="utf-8")

    assert "ProtectSystem=strict" in unit
    assert "PrivateTmp=true" in unit


def test_firewall_forward_policy_update_respects_exact_file_sandbox() -> None:
    script = (
        Path(__file__).parents[2]
        / "src/nebius_vpngw/systemd/setup-vpngw-firewall.sh"
    ).read_text(encoding="utf-8")

    assert "/etc/default/ufw" in script
    assert "path.write_text(updated)" in script
    assert "sed -i 's/DEFAULT_FORWARD_POLICY" not in script
