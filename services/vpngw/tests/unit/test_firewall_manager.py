from __future__ import annotations

import fcntl
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.agent import firewall_manager


def _redirect_runtime_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firewall_manager.ingress_policy, "install", lambda *args, **kwargs: None)
    monkeypatch.setattr(firewall_manager.ingress_policy, "verify", lambda *args, **kwargs: None)
    monkeypatch.setattr(firewall_manager, "PEER_IPS_FILE", tmp_path / "peers")
    monkeypatch.setattr(firewall_manager, "LOCAL_PREFIXES_FILE", tmp_path / "local")
    monkeypatch.setattr(firewall_manager, "UFW_USER_RULES", tmp_path / "user.rules")
    monkeypatch.setattr(firewall_manager, "UFW_LOCK", tmp_path / "ufw.lock")


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
            {"gateway": {}, "connections": []},
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
        self.saved_rules = []
        self.restore_failure = None
        self.ignore_restore = False
        self.missing_chain = False
        self.fail_install = False
        self.ignore_install = False

    def save(self):
        return "\n".join(
            [
                "*filter",
                ":INPUT DROP [0:0]",
                ":OUTPUT ACCEPT [0:0]",
                *(
                    []
                    if self.missing_chain
                    else [":ufw-user-input - [0:0]", ":ufw-user-output - [0:0]"]
                ),
                *self.rules,
                "COMMIT",
            ]
        )

    def run(self, arguments, **kwargs):
        self.calls.append(arguments)
        if arguments == ["iptables-save", "-t", "filter"]:
            return SimpleNamespace(returncode=0, stdout=self.save())
        if arguments[0] == "iptables-restore":
            testing = "--test" in arguments
            if self.restore_failure == ("test" if testing else "apply"):
                return SimpleNamespace(returncode=1, stdout="")
            if not testing and not self.ignore_restore:
                lines = kwargs["input"].splitlines()
                affected = {line.split()[0][1:] for line in lines if line.startswith(":")}
                self.rules = [line for line in self.rules if shlex.split(line)[1] not in affected]
                self.rules.extend(line for line in lines if line.startswith("-A "))
            return SimpleNamespace(returncode=0, stdout="")
        assert arguments[:2] == ["ufw", "allow"]
        direction, _, interface = arguments[2:]
        if self.fail_install:
            return SimpleNamespace(returncode=1, stdout="")
        chain, match = ("input", "-i") if direction == "in" else ("output", "-o")
        rule = f"-A ufw-user-{chain} {match} {interface} -j ACCEPT"
        if not self.ignore_install and rule not in self.saved_rules:
            self.saved_rules.append(rule)
            self.rules.append(rule)
            firewall_manager.UFW_USER_RULES.write_text(
                "\n".join(
                    [
                        "*filter",
                        ":ufw-user-input - [0:0]",
                        ":ufw-user-output - [0:0]",
                        *self.saved_rules,
                        "COMMIT",
                    ]
                )
            )
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
def test_tunnel_rule_installation_requires_observed_success(tmp_path, monkeypatch, fault):
    _redirect_runtime_files(tmp_path, monkeypatch)
    kernel = HostFirewall()
    kernel.fail_install = fault == "failed-command"
    kernel.ignore_install = fault == "successful-command-without-rule"
    monkeypatch.setattr(firewall_manager, "run", kernel.run)
    with pytest.raises(RuntimeError, match="tunnel host firewall"):
        firewall_manager.ensure_tunnel_host_rules(_bgp_config())


@pytest.fixture
def installed_policy(tmp_path, monkeypatch):
    _redirect_runtime_files(tmp_path, monkeypatch)
    kernel = HostFirewall()
    monkeypatch.setattr(firewall_manager, "run", kernel.run)
    firewall_manager.ensure_tunnel_host_rules(_bgp_config())
    kernel.calls.clear()
    return kernel


@pytest.mark.parametrize("direction", ["input", "output"])
def test_saved_policy_repairs_kernel_only_loss_in_original_order(installed_policy, direction):
    kernel = installed_policy
    target = f"ufw-user-{direction}"
    permit = next(line for line in kernel.rules if target in line)
    deny = f"-A {target} -p tcp --dport 179 -j DROP"
    kernel.rules.append(deny)
    kernel.rules.remove(permit)
    saved = firewall_manager.UFW_USER_RULES.read_text().replace("COMMIT", deny + "\nCOMMIT")
    firewall_manager.UFW_USER_RULES.write_text(saved)
    firewall_manager.ensure_tunnel_host_rules(_bgp_config())
    assert [line for line in kernel.rules if target in line] == [permit, deny]
    assert firewall_manager.UFW_USER_RULES.read_text() == saved
    assert [call for call in kernel.calls if call[0] == "iptables-restore"] == [
        ["iptables-restore", "--noflush", "--wait", "5", "--test"],
        ["iptables-restore", "--noflush", "--wait", "5"],
    ]
    kernel.calls.clear()
    firewall_manager.ensure_tunnel_host_rules(_bgp_config())
    assert kernel.calls == [["iptables-save", "-t", "filter"]]


@pytest.mark.parametrize("failure", ["test", "apply", "ineffective", "timeout"])
def test_saved_policy_failure_never_reports_success(installed_policy, monkeypatch, failure):
    kernel = installed_policy
    kernel.rules.clear()
    kernel.restore_failure = failure
    kernel.ignore_restore = failure == "ineffective"
    if failure == "timeout":
        original = kernel.run

        def run(arguments, **kwargs):
            if arguments[0] == "iptables-restore":
                raise subprocess.TimeoutExpired(arguments, 20)
            return original(arguments, **kwargs)

        monkeypatch.setattr(firewall_manager, "run", run)
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        firewall_manager.ensure_tunnel_host_rules(_bgp_config())
    if failure == "test":
        assert all("--test" in call for call in kernel.calls if call[0] == "iptables-restore")
    assert not any("reset" in call or "reload" in call for call in kernel.calls)


@pytest.mark.parametrize(
    "failure", ["absent", "malformed", "missing-permit", "missing-chain", "permit-outside-filter"]
)
def test_invalid_saved_policy_never_restores(installed_policy, failure):
    kernel = installed_policy
    kernel.rules.clear()
    path = firewall_manager.UFW_USER_RULES
    if failure == "absent":
        path.unlink()
    elif failure == "malformed":
        path.write_text("*filter\n-F INPUT\nCOMMIT\n")
    elif failure == "missing-permit":
        path.write_text("*filter\n:ufw-user-input - [0:0]\n:ufw-user-output - [0:0]\nCOMMIT\n")
    elif failure == "permit-outside-filter":
        path.write_text(
            path.read_text().replace("-A ufw-user-input -i xfrm0 -j ACCEPT\n", "")
            + "\n-A ufw-user-input -i xfrm0 -j ACCEPT\n"
        )
    else:
        kernel.missing_chain = True
    with pytest.raises(RuntimeError, match="saved tunnel host firewall policy"):
        firewall_manager.ensure_tunnel_host_rules(_bgp_config())
    assert not any(call[0] == "iptables-restore" for call in kernel.calls)


def test_saved_policy_lock_timeout_is_bounded(installed_policy, monkeypatch):
    kernel = installed_policy
    kernel.rules.clear()
    monkeypatch.setattr(firewall_manager, "UFW_LOCK_TIMEOUT", 0)

    def locked(*args):
        raise BlockingIOError()

    monkeypatch.setattr(firewall_manager.fcntl, "lockf", locked)
    with pytest.raises(RuntimeError, match="timed out locking"):
        firewall_manager.ensure_tunnel_host_rules(_bgp_config())
    assert not any(call[0] == "iptables-restore" for call in kernel.calls)


def test_saved_policy_rechecks_after_taking_posix_lock(installed_policy, monkeypatch):
    kernel = installed_policy
    saved = list(kernel.rules)
    kernel.rules.clear()
    locks = []

    def lockf(file, operation):
        locks.append(operation)
        kernel.rules[:] = saved

    monkeypatch.setattr(firewall_manager.fcntl, "lockf", lockf)
    firewall_manager.ensure_tunnel_host_rules(_bgp_config())
    assert locks == [fcntl.LOCK_EX | fcntl.LOCK_NB, fcntl.LOCK_UN]
    assert not any(call[0] == "iptables-restore" for call in kernel.calls)


@pytest.mark.parametrize(
    "text",
    [
        "*filter\n:ufw-user-input - [0:0]\n",
        "*filter\nCOMMIT\n*filter\nCOMMIT\n",
        "*filter\n:ufw-user-input - [0:0]\n-A missing -j ACCEPT\nCOMMIT\n",
        '*filter\n:ufw-user-input - [0:0]\n-A ufw-user-input --comment "\nCOMMIT\n',
    ],
)
def test_filter_policy_rejects_incomplete_or_ambiguous_source(text):
    with pytest.raises(ValueError):
        firewall_manager._filter_policy(text)


def test_disabled_only_config_never_reads_or_mutates_firewall(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("disabled tunnels must have no firewall effects")

    monkeypatch.setattr(firewall_manager, "run", unexpected)
    firewall_manager.ensure_tunnel_host_rules(
        {
            "connections": [
                {
                    "routing_mode": "static",
                    "tunnels": [{"remote_public_ip": "192.0.2.1", "ha_role": "disable"}],
                }
            ]
        }
    )


def test_native_ufw_logging_insert_is_not_replayed(installed_policy):
    kernel = installed_policy
    kernel.rules.clear()
    path = firewall_manager.UFW_USER_RULES
    path.write_text(
        path.read_text().replace(
            "COMMIT",
            ":ufw-logging-deny - [0:0]\n"
            "-I ufw-logging-deny -m conntrack --ctstate INVALID -j RETURN\nCOMMIT",
        )
    )
    firewall_manager.ensure_tunnel_host_rules(_bgp_config())
    assert len(kernel.rules) == 2
    assert all("ufw-logging-deny" not in rule for rule in kernel.rules)
