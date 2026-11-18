"""Real DHCP /32, networkd persistence and packet checks in the disposable fixture."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

from nebius_vpngw.agent import underlay

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires disposable systemd fixture",
    ),
]


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30).stdout


def eventually(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (RuntimeError, subprocess.CalledProcessError):
            pass
        time.sleep(0.1)
    raise AssertionError("native DHCP fixture did not converge")


@pytest.fixture
def provider(tmp_path, monkeypatch):
    assert Path("/.dockerenv").exists() and os.geteuid() == 0
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    # This is fixture setup, preceding the product trial. Preserve Docker's
    # uplink and use actual DHCP for the product-facing interface, not manual
    # guest addressing or a preinstalled product destination route.
    command("ip", "link", "set", "eth0", "name", "fixture-uplink")
    command("ip", "netns", "add", "underlay-provider")
    command("ip", "link", "add", "eth0", "type", "veth", "peer", "name", "provider0")
    command("ip", "link", "set", "provider0", "netns", "underlay-provider")
    prefix = ("ip", "netns", "exec", "underlay-provider")
    command(*prefix, "ip", "link", "set", "lo", "up")
    command(*prefix, "ip", "link", "set", "provider0", "up")
    command(*prefix, "ip", "addr", "add", "10.66.0.1/24", "dev", "provider0")
    command(*prefix, "ip", "addr", "add", "169.254.169.1/32", "dev", "provider0")
    for address in ("10.66.0.9/32", "10.66.16.5/32", "192.0.2.8/32"):
        command(*prefix, "ip", "addr", "add", address, "dev", "lo")
    lease = tmp_path / "leases"
    log = (tmp_path / "dnsmasq.log").open("w")
    dhcp = subprocess.Popen(
        [
            *prefix,
            "dnsmasq",
            "--no-daemon",
            "--port=0",
            "--bind-interfaces",
            "--interface=provider0",
            "--dhcp-authoritative",
            "--dhcp-range=10.66.0.5,10.66.0.5,255.255.255.0,120s",
            "--dhcp-option=1,255.255.255.255",
            "--dhcp-option=3,169.254.169.1",
            "--dhcp-leasefile=" + str(lease),
            "--log-dhcp",
        ],
        stdout=log,
        stderr=log,
    )
    network = Path("/etc/systemd/network/00-underlay-fixture.network")
    network.parent.mkdir(parents=True, exist_ok=True)
    network.write_text("[Match]\nName=eth0\n[Network]\nDHCP=ipv4\nLinkLocalAddressing=no\n")
    monkeypatch.setattr(underlay, "STATE", tmp_path / "product" / "underlay.json")
    try:
        command("systemctl", "start", "systemd-networkd.service")
        command("networkctl", "reload")
        command("ip", "link", "set", "eth0", "up")
        eventually(lambda: underlay.observe("eth0").address == "10.66.0.5")
        address = json.loads(command("ip", "-j", "-4", "addr", "show", "dev", "eth0"))
        assert any(
            a["local"] == "10.66.0.5" and a["prefixlen"] == 32 for a in address[0]["addr_info"]
        )
        assert dhcp.poll() is None
        yield
    finally:
        dhcp.terminate()
        dhcp.wait(timeout=10)
        log.close()
        dropin = network.with_name(network.name + ".d") / underlay.DROPIN
        dropin.unlink(missing_ok=True)
        network.unlink(missing_ok=True)
        command("ip", "link", "del", "eth0")
        command("ip", "netns", "del", "underlay-provider")
        command("ip", "link", "set", "fixture-uplink", "name", "eth0")
        command("networkctl", "reload")


@pytest.mark.parametrize(
    ("family", "kind", "args"),
    [
        ("-4", "route", ["198.51.100.0/24", "dev", "eth0", "table", "100", "proto", "boot"]),
        ("-6", "route", ["2001:db8::/64", "dev", "eth0", "table", "100", "proto", "boot"]),
        ("-4", "route", ["blackhole", "198.51.100.0/24", "table", "100", "proto", "boot"]),
        ("-4", "rule", ["priority", "10100", "table", "100", "protocol", "static"]),
        ("-6", "rule", ["priority", "10100", "table", "100", "protocol", "static"]),
    ],
)
def test_reconfiguration_preserves_foreign_routing_state(provider, family, kind, args):
    """Real iproute2 inventories must fail closed before networkd is changed."""
    cfg = {"gateway": {"local_prefixes": ["10.66.16.0/24"]}, "connections": []}
    command("ip", family, kind, "add", *args)
    query = ("ip", "-j", family, "-details", kind, "show")
    if kind == "route":
        query += ("table", "100")
    try:
        before = json.loads(command(*query))
        assert before
        with pytest.raises(underlay.UnderlayError, match="foreign policy rule|unmanaged route"):
            underlay.reconcile_locked(cfg, reconfigure=True)
        assert json.loads(command(*query)) == before
        assert not underlay.STATE.exists()
        assert not Path(
            "/etc/systemd/network/00-underlay-fixture.network.d", underlay.DROPIN
        ).exists()
    finally:
        command("ip", family, kind, "del", *args)


def test_networkd_dhcp_persistence_and_aggregate_packet_paths(provider):
    cfg = {
        "gateway": {"local_prefixes": ["10.66.16.0/24", "10.66.0.9/32"]},
        "connections": [
            {"remote_prefixes": ["10.0.0.0/8"], "tunnels": [{"remote_public_ip": "192.0.2.8"}]}
        ],
    }
    command("ip", "link", "add", "underlay-vpn", "type", "dummy")
    command("ip", "link", "set", "underlay-vpn", "up")
    command(
        "ip", "route", "add", "10.0.0.0/8", "dev", "underlay-vpn", "proto", "bgp", "metric", "20"
    )
    try:
        before = json.loads(command("ip", "-j", "route", "get", "10.66.16.5"))[0]
        assert before["dev"] == "underlay-vpn"
        try:
            underlay.reconcile_locked(cfg, reconfigure=True)
        except RuntimeError:
            print(command("ip", "-j", "-4", "route", "show", "table", "main"))
            print(command("networkctl", "status", "--json=short", "--no-pager", "eth0"))
            print(command("journalctl", "-u", "systemd-networkd", "--no-pager", "-n", "35"))
            raise
        for address in ("10.66.16.5", "10.66.0.9", "192.0.2.8"):
            selected = json.loads(command("ip", "-j", "route", "get", address))[0]
            assert selected["dev"] == "eth0" and selected["gateway"] == "169.254.169.1"
            command("ping", "-n", "-c", "2", "-W", "2", address)
        command("networkctl", "renew", "eth0")
        eventually(lambda: underlay.ready(cfg))
        command(
            "ip",
            "route",
            "del",
            "10.66.16.0/24",
            "via",
            "169.254.169.1",
            "dev",
            "eth0",
            "proto",
            "dhcp",
            "metric",
            "50",
        )
        assert not underlay.ready(cfg)
        underlay.reconcile_locked(cfg)
        command("ping", "-n", "-c", "2", "-W", "2", "10.66.16.5")
        command("systemctl", "restart", "systemd-networkd.service")
        eventually(lambda: underlay.ready(cfg))
        assert underlay.ready(cfg)
    except Exception:
        print(command("ip", "-j", "-4", "route", "show", "table", "main"))
        print(command("networkctl", "status", "--json=short", "--no-pager", "eth0"))
        print(command("journalctl", "-u", "systemd-networkd", "--no-pager", "-n", "35"))
        try:
            underlay.verify(cfg)
        except RuntimeError as error:
            print("Underlay verification:", error)
        raise
    finally:
        command("ip", "link", "del", "underlay-vpn")


@pytest.mark.parametrize("mode", ["ordinary", "ha"])
def test_packaged_boot_oneshot_prepares_routes_with_forwarding_disabled(
    provider, monkeypatch, tmp_path, mode
):
    """Start the real hardened unit from a lease-only, guarded boot checkpoint."""
    state = Path("/var/lib/nebius-vpngw/underlay.json")
    config = Path("/etc/nebius-vpngw/config-resolved.yaml")
    guard = Path("/var/lib/nebius-vpngw/ordinary/destination-protection.json")
    unit = Path("/etc/systemd/system/nebius-vpngw-underlay.service")
    override = unit.with_name(unit.name + ".d") / "10-fixture-python.conf"
    paths = (state, config, guard, unit, override)
    if mode == "ha":
        paths += (state.parent / "vm-ha" / "guard.json",)
    original = {
        p: (p.read_bytes(), p.stat().st_mode & 0o777) if p.exists() else None for p in paths
    }
    ordinary_mode = guard.parent.stat().st_mode & 0o777 if guard.parent.exists() else None
    previous_forwarding = command("sysctl", "-n", "net.ipv4.ip_forward").strip()
    cfg = {"gateway": {"local_prefixes": ["10.66.16.0/24"]}, "connections": []}
    if mode == "ha":
        from tests.unit.test_vm_ha_runtime_ports import _runtime_config

        ha = _runtime_config(tmp_path)
        ha["runtime_binding"]["nodes"][0]["peer_endpoint"] = "10.66.0.5:9443"
        ha["runtime_binding"]["nodes"][1]["peer_endpoint"] = "10.66.0.9:9443"
        cfg["vm_ha"] = ha
    try:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink(missing_ok=True)
        # Deployment keeps the operation journal directory private even when
        # no journal exists at this supported boot checkpoint.
        guard.parent.chmod(0o700)
        if mode == "ha":
            from nebius_vpngw.ha_repair import cold_guard

            ha_state = state.parent / "vm-ha"
            (ha_state / "mtls").mkdir(mode=0o700, exist_ok=True)
            cold_guard(state_dir=ha_state, boot_id=underlay.BOOT.read_text().strip())
        config.write_text(json.dumps(cfg))
        config.chmod(0o600)
        unit.write_bytes((Path(underlay.__file__).parents[1] / "systemd" / unit.name).read_bytes())
        # The isolated fixture owns dependency provisioning, while the actual
        # packaged /usr/bin/python3 ExecStart and sandbox remain unchanged.
        override.write_text(
            "[Service]\nEnvironment=PYTHONPATH=/workspace/src:"
            + sysconfig.get_paths()["purelib"]
            + "\n"
        )
        command("sysctl", "-w", "net.ipv4.ip_forward=0")
        command("systemctl", "daemon-reload")
        try:
            command("systemctl", "start", unit.name)
        except subprocess.CalledProcessError:
            print(command("journalctl", "--no-pager", "-u", unit.name, "-n", "30"))
            raise
        monkeypatch.setattr(underlay, "STATE", state)
        assert underlay.ready(cfg)
        assert (
            command("systemctl", "show", "--property=ActiveState", "--value", unit.name).strip()
            == "active"
        )
        assert command("sysctl", "-n", "net.ipv4.ip_forward").strip() == "0"
        command("ping", "-n", "-c", "2", "-W", "2", "10.66.16.5")
        command("systemctl", "restart", unit.name)
        assert underlay.ready(cfg)
        # A new, fixture-owned periodic-repair trial starts from active
        # ordinary or passive HA authority. This does not prove role activation.
        if mode == "ha":
            command("ping", "-n", "-c", "2", "-W", "2", "10.66.0.9")
            from nebius_vpngw.agent.vm_ha.store import atomic_write_json

            atomic_write_json(
                ha_state / "guard.json",
                {
                    "guard_boot_id": underlay.BOOT.read_text().strip(),
                    "data_plane_mode": "passive",
                },
            )
        else:
            command("sysctl", "-w", "net.ipv4.ip_forward=1")
        command(
            "ip",
            "route",
            "del",
            "10.66.16.0/24",
            "via",
            "169.254.169.1",
            "dev",
            "eth0",
            "proto",
            "dhcp",
            "metric",
            "50",
        )
        assert not underlay.ready(cfg)
        command(sys.executable, "-m", "nebius_vpngw.agent.fix_routes", "--config", str(config))
        assert underlay.ready(cfg)
        assert command("sysctl", "-n", "net.ipv4.ip_forward").strip() == (
            "0" if mode == "ha" else "1"
        )
    finally:
        command("systemctl", "stop", unit.name)
        for path, previous in original.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous[0])
                path.chmod(previous[1])
        if ordinary_mode is not None:
            guard.parent.chmod(ordinary_mode)
        command("sysctl", "-w", "net.ipv4.ip_forward=" + previous_forwarding)
        command("systemctl", "daemon-reload")
