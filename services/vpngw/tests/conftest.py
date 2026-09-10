from __future__ import annotations

import os
import socket

import pytest


@pytest.fixture
def ordinary_static_observer(monkeypatch, tmp_path):
    """Real ordinary verifier with isolated non-routing observations.

    Tests supply routes produced by the real writer, or a real network namespace;
    this fixture never derives expected forwarding from the helper under test.
    """
    import ipaddress
    import json
    from types import SimpleNamespace

    from nebius_vpngw import ordinary_operations as ops
    from nebius_vpngw.agent import ordinary
    from nebius_vpngw.tunnel_state import collect_tunnel_state

    monkeypatch.setattr(ops, "JOURNAL", tmp_path / "observer/operation.json")
    swan, frr = tmp_path / "swanctl.conf", tmp_path / "frr.conf"
    swan.write_text("fixture connections\n")
    swan.chmod(0o600)
    frr.write_text("router bgp 65001\n")
    monkeypatch.setattr(ordinary, "SWANCTL_CONF", swan)
    monkeypatch.setattr(ordinary, "FRR_CONF", frr)
    monkeypatch.setattr(
        ordinary, "managed_files", lambda cfg: {swan: swan.read_text(), frr: frr.read_text()}
    )

    def prepare(cfg):
        tunnels, _, endpoints = collect_tunnel_state(cfg, log=lambda _: None)
        state = SimpleNamespace(routes=[], ip_reader=None)

        def ip_rows(args):
            if "rule" in args:
                return []
            if "route" in args:
                return state.routes
            if args == ["ip", "-d", "-j", "link", "show"]:
                return [
                    {"ifname": "eth0", "ifindex": 2, "mtu": 1500},
                    *(
                        ip_rows(["ip", "-d", "-j", "link", "show", "dev", e["name"]])[0]
                        for e in endpoints
                    ),
                ]
            if args[-1] == "eth0":
                return [{"ifindex": 2, "mtu": 1500}]
            endpoint = next(e for e in endpoints if e["name"] == args[-1])
            if "link" in args:
                return [
                    {
                        "ifname": endpoint["name"],
                        "ifindex": endpoint["if_id"],
                        "mtu": 1436,
                        "link_index": 2,
                        "flags": ["UP"],
                        "linkinfo": {
                            "info_kind": "xfrm",
                            "info_data": {"if_id": endpoint["if_id"]},
                        },
                    }
                ]
            if "addr" in args:
                return [
                    {
                        "addr_info": [
                            {
                                "local": endpoint["local_inner_ip"],
                                "prefixlen": ipaddress.ip_network(endpoint["cidr"]).prefixlen,
                            }
                        ]
                    }
                ]
            if "neigh" in args:
                return [{"dst": endpoint["remote_inner_ip"], "state": ["PERMANENT"]}]
            raise AssertionError(args)

        def output(args):
            if args[0] == "ip":
                return json.dumps((state.ip_reader or ip_rows)(args))
            if args[0] == "systemctl":
                return "active"
            if args[0] == "ufw":
                return "Status: active"
            if args[0] == "sysctl":
                return ordinary.REQUIRED_SYSCTLS.get(args[-1], "0")
            if args[0] == "swanctl":
                assert args[1:] == ["--list-conns", "--raw"]
                return "list-conn event {" + " ".join(t["name"] + " {}" for t in tunnels) + "}"
            if args[0] == "vtysh":
                return frr.read_text()
            if args[0] == "iptables-save":
                return "ufw-before-input\n" + "\n".join(
                    f"-A ufw-user-{direction} -{flag} {e['name']} -j ACCEPT"
                    for e in endpoints
                    for direction, flag in (("input", "i"), ("output", "o"))
                )
            raise AssertionError(args)

        monkeypatch.setattr(ordinary, "_output", output)
        state.ip_rows = ip_rows
        return state

    return prepare


@pytest.fixture
def ordinary_operation_guest(monkeypatch, tmp_path):
    """Explicit guest-manager boundary for host-independent orchestration tests."""
    from nebius_vpngw import ordinary_operations as ops

    boot = tmp_path / "operation-boot"
    boot.write_text("12345678-1234-1234-1234-123456789012")
    monkeypatch.setattr(ops, "BOOT", boot)
    monkeypatch.setattr(ops, "JOURNAL", tmp_path / "ordinary" / "operation.json")
    monkeypatch.setattr(ops, "ROUTING_LOCK", tmp_path / "operation-routing.lock")
    monkeypatch.setattr(
        ops,
        "identity",
        lambda pid=None: {"pid": pid or os.getpid(), "start": "1", "boot": ops.BOOT.read_text()},
    )

    class GuestManager:
        on_service = None

        def __init__(self, deadline, **kwargs):
            self.deadline = deadline

        def preflight(self):
            pass

        def environment(self):
            return {"network": self.network(), "unit_files": {}}

        def network(self):
            return {"inputs": {}, "links": [{"name": "eth0"}]}

        def unit(self, name):
            return {"missing": False}

        def stopped(self, name):
            return True

        def quiet(self, names):
            return True

        def management_admitted(self):
            return True

        def unit_finished(self, effect):
            return self.unit_settled(effect)

        def unit_settled(self, effect):
            return True

        def system(self, method, *args):
            if method == "EnqueueUnitJob":
                if self.on_service is not None:
                    self.on_service(args[2], args[1])
                return {
                    "type": "uososa(uosos)",
                    "data": [1, "/job/1", args[1], "/unit/1", args[2], []],
                }
            return {"type": "", "data": []}

    monkeypatch.setattr(ops, "Manager", GuestManager)
    monkeypatch.setattr(ops, "install_startup_guard", lambda: None)
    from nebius_vpngw.agent import ordinary

    monkeypatch.setattr(ordinary, "Manager", GuestManager)
    return GuestManager


@pytest.fixture(autouse=True)
def block_unit_test_network(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path,
) -> None:
    if request.node.get_closest_marker("integration"):
        return

    # Production intentionally serializes writers by deployment identity. Unit
    # tests reuse fixture identities, so give each test its own process-visible
    # lock root when pytest-xdist schedules those tests concurrently.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))

    def _blocked(*args, **kwargs):
        raise AssertionError("Network access is disabled in unit tests.")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture
def sample_config() -> dict:
    return {
        "version": 1,
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 1,
            "external_ips": [],
            "vm_spec": {
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
                "disk_boot_image": "ubuntu24.04-driverless",
                "disk_gb": 100,
                "disk_type": "network_ssd",
                "disk_block_bytes": 4096,
                "num_nics": 1,
                "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey",
                "ssh_private_key_path": "~/.ssh/id_ed25519",
            },
        },
        "gateway": {
            "local_asn": 65010,
            "local_prefixes": ["10.0.0.0/16"],
            "quotas": {
                "max_connections": 4,
                "max_tunnels": 8,
                "max_total_bandwidth_mbps": None,
            },
        },
        "defaults": {
            "vpn_type": "ipsec",
            "ike_version": 2,
            "allow_ikev1": False,
            "auth": {"method": "psk"},
            "crypto": {
                "ike_proposals": ["aes256-sha256-modp2048"],
                "ike_lifetime_seconds": 28800,
                "esp_proposals": ["aes256-sha256-modp2048"],
                "esp_lifetime_seconds": 3600,
                "dh_groups": [14],
            },
            "dpd": {"interval_seconds": 5, "timeout_seconds": 15},
            "health_monitoring": {
                "enabled": True,
                "check_interval_seconds": 10,
                "max_failures_before_restart": 2,
                "proactive_refresh_enabled": False,
                "proactive_refresh_hours": 8,
                "ping_enabled": False,
            },
            "ha_mode": "active-passive",
            "routing": {
                "mode": "static",
                "bgp": {
                    "router_id": None,
                    "hold_time_seconds": 6,
                    "keepalive_seconds": 2,
                    "graceful_restart": False,
                    "max_prefixes": 1000,
                    "bfd": {
                        "enabled": False,
                        "transmit_interval_ms": 300,
                        "receive_interval_ms": 300,
                        "detect_multiplier": 3,
                    },
                },
            },
        },
        "connections": [
            {
                "name": "static-peer",
                "vendor": "generic",
                "routing_mode": "static",
                "remote_prefixes": ["203.0.113.0/24"],
                "bgp": {
                    "enabled": False,
                    "remote_asn": None,
                    "advertise_local_prefixes": False,
                },
                "tunnels": [
                    {
                        "name": "tunnel-1",
                        "gateway_instance_index": 0,
                        "ha_role": "active",
                        "remote_public_ip": "198.51.100.10",
                        "psk": "test-only-static-routing-validation-psk",
                        "inner_cidr": "169.254.18.224/30",
                        "inner_local_ip": "169.254.18.225",
                        "inner_remote_ip": "169.254.18.226",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def ordinary_handoff_preview(monkeypatch):
    from unittest.mock import Mock

    from nebius_vpngw.deploy import ordinary_handoff

    inspector = Mock(return_value={"journal": "1" * 64, "environment": {}})
    monkeypatch.setattr(ordinary_handoff, "inspect", inspector)
    monkeypatch.setattr(
        ordinary_handoff,
        "reserve",
        Mock(side_effect=AssertionError("dry-run may not reserve migration admission")),
    )
    return inspector
