from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
        "connections": [],
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
        Path(__file__).parents[2] / "src/nebius_vpngw/systemd/nebius-vpngw-vm-ha.service"
    ).read_text(encoding="utf-8")

    assert "ProtectSystem=strict" in unit
    assert "PrivateTmp=true" in unit


def test_firewall_forward_policy_update_respects_exact_file_sandbox() -> None:
    script = (
        Path(__file__).parents[2] / "src/nebius_vpngw/systemd/setup-vpngw-firewall.sh"
    ).read_text(encoding="utf-8")

    assert "/etc/default/ufw" in script
    assert "path.write_text(updated)" in script
    assert "sed -i 's/DEFAULT_FORWARD_POLICY" not in script


def _bgp_config() -> dict:
    return {
        "gateway": {"local_prefixes": ["10.81.1.0/24"]},
        "connections": [
            {
                "name": "home",
                "routing_mode": "bgp",
                "tunnels": [
                    {"name": "home-1", "remote_public_ip": "192.0.2.1"},
                    {
                        "name": "home-disabled",
                        "remote_public_ip": "192.0.2.1",
                        "ha_role": "disable",
                    },
                ],
            }
        ],
    }


class HostFirewall:
    def __init__(self):
        self.rules = []
        self.calls = []
        self.interfaces = set()
        self.fail_install = False
        self.ignore_install = False

    def save(self):
        return "\n".join(
            ["*filter", ":INPUT DROP [0:0]", ":OUTPUT ACCEPT [0:0]", *self.rules, "COMMIT"]
        )

    def run(self, arguments, **kwargs):
        self.calls.append(arguments)
        if arguments == ["iptables-save", "-t", "filter"]:
            return SimpleNamespace(returncode=0, stdout=self.save())
        assert arguments[:2] == ["ufw", "allow"]
        direction, _, interface = arguments[2:]
        assert interface not in self.interfaces
        if self.fail_install:
            return SimpleNamespace(returncode=1, stdout="")
        chain, match = ("input", "-i") if direction == "in" else ("output", "-o")
        if not self.ignore_install:
            self.rules.append(f"-A ufw-user-{chain} {match} {interface} -j ACCEPT")
        return SimpleNamespace(returncode=0, stdout="")


def test_first_boot_preinstalls_configured_tunnel_access_before_interfaces_exist(
    tmp_path, monkeypatch
):
    _redirect_runtime_files(tmp_path, monkeypatch)
    kernel = HostFirewall()
    monkeypatch.setattr(firewall_manager, "run", kernel.run)

    def reset():
        kernel.rules.clear()
        return True

    monkeypatch.setattr(firewall_manager, "reload_firewall", reset)
    cfg = _bgp_config()
    firewall_manager.update_firewall_from_config(cfg, require_reload=True)
    assert kernel.interfaces == set()
    assert kernel.rules == [
        "-A ufw-user-input -i xfrm0 -j ACCEPT",
        "-A ufw-user-output -o xfrm0 -j ACCEPT",
    ]
    kernel.interfaces.add("xfrm0")
    firewall_manager.verify_tunnel_host_rules(cfg)
    first_install_count = len(kernel.calls)
    firewall_manager.ensure_tunnel_host_rules(cfg)
    assert kernel.calls[first_install_count:] == [["iptables-save", "-t", "filter"]]
    assert all("FORWARD" not in rule for rule in kernel.rules)


def test_unchanged_config_repairs_rules_missing_after_an_early_firewall_reset(
    tmp_path, monkeypatch
):
    _redirect_runtime_files(tmp_path, monkeypatch)
    kernel = HostFirewall()
    monkeypatch.setattr(firewall_manager, "run", kernel.run)
    monkeypatch.setattr(firewall_manager, "reload_firewall", lambda: True)
    cfg = _bgp_config()
    firewall_manager.update_firewall_from_config(cfg)
    kernel.rules.clear()
    firewall_manager.update_firewall_from_config(cfg)
    firewall_manager.verify_tunnel_host_rules(cfg)
    assert len(kernel.rules) == 2


@pytest.mark.parametrize("fault", ["failed-command", "successful-command-without-rule"])
def test_tunnel_rule_installation_requires_observed_success(monkeypatch, fault):
    kernel = HostFirewall()
    kernel.fail_install = fault == "failed-command"
    kernel.ignore_install = fault == "successful-command-without-rule"
    monkeypatch.setattr(firewall_manager, "run", kernel.run)
    with pytest.raises(RuntimeError, match="tunnel host firewall rules"):
        firewall_manager.ensure_tunnel_host_rules(_bgp_config())
