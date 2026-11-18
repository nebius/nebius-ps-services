"""Route ownership and lifecycle tests; native networkd proof is a separate lane."""

from __future__ import annotations

import copy
import ipaddress
import json
import re
import subprocess

import pytest

from nebius_vpngw.agent import underlay


@pytest.fixture
def cfg():
    return {
        "gateway": {"local_prefixes": ["10.64.16.0/24"]},
        "connections": [
            {"remote_prefixes": ["10.0.0.0/8"], "tunnels": [{"remote_public_ip": "192.0.2.8"}]}
        ],
    }


@pytest.fixture
def host(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    network_root = root / "network"
    network_root.mkdir()
    network_file = network_root / "10-netplan-eth0.network"
    network_file.write_text("[Match]\nName=eth0\n[Network]\nDHCP=ipv4\n")
    boot = root / "boot"
    boot.write_text("boot-a")
    monkeypatch.setattr(underlay, "NETWORK_ROOTS", (network_root,))
    monkeypatch.setattr(underlay, "STATE", root / "state" / "underlay.json")
    monkeypatch.setattr(underlay, "BOOT", boot)

    class Host:
        def __init__(self):
            self.network_file = network_file
            self.gateway = "169.254.169.1"
            self.index = 2
            self.routes = [
                {
                    "dst": "default",
                    "gateway": self.gateway,
                    "protocol": "dhcp",
                    "dev": "eth0",
                    "metric": 100,
                }
            ]
            self.loaded = False
            self.calls = []
            self.fail_reload = False

        @property
        def dropin(self):
            return network_root / (self.network_file.name + ".d") / underlay.DROPIN

        def route(self, prefix):
            return {
                "dst": prefix.removesuffix("/32"),
                "gateway": self.gateway,
                "protocol": "dhcp",
                "dev": "eth0",
                "metric": underlay.METRIC,
                "flags": ["onlink"],
            }

        def run(self, argv, **kwargs):
            self.calls.append(argv)
            output = ""
            if argv[:2] == ["networkctl", "status"] and "--all" in argv:
                output = json.dumps(
                    {"Interfaces": [{"Name": "eth0", "NetworkFile": str(self.network_file)}]}
                )
            elif argv[:2] == ["networkctl", "status"]:
                output = json.dumps(
                    {
                        "Name": "eth0",
                        "Index": self.index,
                        "Type": "ether",
                        "NetworkFile": str(self.network_file),
                        "NetworkFileDropins": [str(self.dropin)] if self.loaded else [],
                        "Addresses": [
                            {
                                "Family": 2,
                                "Address": [10, 64, 0, 5],
                                "ConfigSource": "DHCPv4",
                                "ConfigState": "configured",
                            }
                        ],
                        "Routes": [
                            {
                                "Family": 2,
                                "DestinationPrefixLength": 0,
                                "Table": 254,
                                "Protocol": 16,
                                "ConfigSource": "DHCPv4",
                                "ConfigState": "configured",
                                "Destination": [0, 0, 0, 0],
                                "ProtocolString": "dhcp",
                                "Priority": 100,
                                "ScopeString": "global",
                                "Gateway": list(ipaddress.IPv4Address(self.gateway).packed),
                            }
                        ],
                    }
                )
            elif argv == ["networkctl", "reload"]:
                if self.fail_reload:
                    raise RuntimeError("simulated interrupted reload")
            elif argv[:2] == ["networkctl", "reconfigure"]:
                self.loaded = True
                for prefix in re.findall(r"Destination=(.*)", self.dropin.read_text()):
                    route = self.route(prefix)
                    if route not in self.routes:
                        self.routes.append(route)
            elif argv in (
                ["ip", "-j", "-4", "-details", "rule", "show"],
                ["ip", "-j", "-6", "-details", "rule", "show"],
            ):
                output = json.dumps(
                    [{"priority": 0, "src": "all", "table": "local", "protocol": "kernel"}]
                )
            elif argv == ["ip", "-j", "-6", "route", "show", "table", "all"]:
                output = json.dumps([{"dst": "fe80::/64", "dev": "eth0", "protocol": "kernel"}])
            elif argv[:6] == ["ip", "-j", "-4", "route", "show", "table"]:
                rows = self.routes
                if "exact" in argv:
                    rows = [r for r in rows if underlay._destination(r) == argv[-1]]
                output = json.dumps(rows)
            elif argv[:5] == ["ip", "-j", "-4", "route", "get"]:
                output = json.dumps([{"dev": "eth0", "gateway": self.gateway}])
            elif argv[:4] == ["ip", "-4", "route", "add"]:
                self.routes.append(self.route(argv[4]))
            elif argv[:4] == ["ip", "-4", "route", "del"]:
                self.routes.remove(self.route(argv[4]))
            else:
                raise AssertionError(argv)
            return subprocess.CompletedProcess(argv, 0, output, "")

    instance = Host()
    monkeypatch.setattr(underlay, "run", instance.run)
    return instance


@pytest.mark.parametrize(
    "family,kind", [("-4", "route"), ("-6", "route"), ("-4", "rule"), ("-6", "rule")]
)
def test_reconfigure_rejects_unobserved_foreign_routing(cfg, host, monkeypatch, family, kind):
    original = underlay.run
    foreign = (
        {
            "dst": "198.51.100.0/24" if family == "-4" else "2001:db8:1::/64",
            "gateway": host.gateway if family == "-4" else "fe80::1",
            "protocol": "boot",
            "dev": "eth0",
            "table": 100,
            "metric": 5,
        }
        if kind == "route"
        else {"priority": 100, "src": "all", "table": 100}
    )

    def run(argv, **kwargs):
        normalized = [arg for arg in argv if arg != "-details"]
        if normalized[:5] == ["ip", "-j", family, kind, "show"]:
            host.calls.append(argv)
            rows = list(host.routes) if family == "-4" and kind == "route" else []
            if kind == "rule" or "all" in argv:
                rows.append(foreign)
            return subprocess.CompletedProcess(argv, 0, json.dumps(rows), "")
        return original(argv, **kwargs)

    monkeypatch.setattr(underlay, "run", run)
    with pytest.raises(underlay.UnderlayError, match="unmanaged|foreign"):
        underlay.preflight_reconfigure(cfg)
    assert not any(
        c[:2] in (["networkctl", "reload"], ["networkctl", "reconfigure"]) for c in host.calls
    )


def test_specific_local_and_transport_routes_override_remote_aggregate(cfg, host):
    underlay.reconcile_locked(cfg, reconfigure=True)
    assert underlay.ready(cfg)
    text = host.dropin.read_text()
    assert "Destination=10.64.16.0/24" in text
    assert "Destination=192.0.2.8/32" in text
    assert "Gateway=_dhcp4" in text
    assert "GatewayOnLink=yes" in text
    assert "Destination=0.0.0.0/0" not in text


def test_timer_repairs_exact_owned_route_without_network_reload(cfg, host):
    underlay.reconcile_locked(cfg, reconfigure=True)
    host.routes.remove(host.route("10.64.16.0/24"))
    host.calls.clear()
    underlay.reconcile_locked(cfg)
    assert ["networkctl", "reload"] not in host.calls
    assert any(c[:4] == ["ip", "-4", "route", "add"] for c in host.calls)
    assert underlay.ready(cfg)


def test_timer_cannot_install_new_intent(cfg, host):
    with pytest.raises(underlay.UnderlayError, match="normal apply"):
        underlay.reconcile_locked(cfg)
    assert not underlay.STATE.exists()


@pytest.mark.parametrize("same_destination", [True, False])
def test_foreign_routes_are_not_adopted_or_deleted(cfg, host, same_destination):
    foreign = host.route("10.64.16.0/24" if same_destination else "10.65.0.0/24")
    host.routes.append(foreign)
    with pytest.raises(underlay.UnderlayError, match="foreign|unmanaged"):
        underlay.reconcile_locked(cfg, reconfigure=True)
    assert foreign in host.routes
    assert not any(c[:2] == ["networkctl", "reload"] for c in host.calls)


def test_exact_connected_route_is_preserved(cfg, host):
    connected = {"dst": "10.64.16.0/24", "protocol": "kernel", "scope": "link", "dev": "eth0"}
    host.routes.append(connected)
    underlay.reconcile_locked(cfg, reconfigure=True)
    assert connected in host.routes
    assert "Destination=10.64.16.0/24" not in host.dropin.read_text()
    assert underlay.ready(cfg)


def test_interrupted_update_retains_predecessor_ownership(cfg, host):
    underlay.reconcile_locked(cfg, reconfigure=True)
    updated = copy.deepcopy(cfg)
    updated["gateway"]["local_prefixes"] = ["10.65.16.0/24"]
    host.fail_reload = True
    with pytest.raises(RuntimeError, match="interrupted"):
        underlay.reconcile_locked(updated, reconfigure=True)
    assert not underlay.ready(updated)
    host.fail_reload = False
    underlay.reconcile_locked(updated, reconfigure=True)
    assert underlay.ready(updated)
    assert host.route("10.64.16.0/24") not in host.routes
    deletes = [c for c in host.calls if c[:4] == ["ip", "-4", "route", "del"]]
    assert deletes and all(
        "via" in c and "dev" in c and "proto" in c and "metric" in c for c in deletes
    )


def test_changed_intent_cannot_supersede_pending_write(cfg, host):
    host.fail_reload = True
    with pytest.raises(RuntimeError):
        underlay.reconcile_locked(cfg, reconfigure=True)
    cfg["gateway"]["local_prefixes"] = ["10.66.16.0/24"]
    with pytest.raises(underlay.UnderlayError, match="original apply"):
        underlay.reconcile_locked(cfg, reconfigure=True)


def test_boot_and_nic_identity_require_new_boot_preparation(cfg, host):
    underlay.reconcile_locked(cfg, reconfigure=True)
    underlay.BOOT.write_text("boot-b")
    host.index = 3
    assert not underlay.ready(cfg)
    underlay.reconcile_locked(cfg, reconfigure=True)
    assert underlay.ready(cfg)


def test_foreign_dropin_modification_is_not_overwritten(cfg, host):
    underlay.reconcile_locked(cfg, reconfigure=True)
    host.dropin.write_text("# operator edit\n")
    with pytest.raises(underlay.UnderlayError, match="modified"):
        underlay.reconcile_locked(cfg, reconfigure=True)
    assert host.dropin.read_text() == "# operator edit\n"


def test_default_local_prefix_is_never_synthesized(cfg):
    cfg["gateway"]["local_prefixes"] = ["0.0.0.0/0"]
    assert underlay.intent(cfg).destinations == ("192.0.2.8/32",)


def test_equal_local_remote_claim_is_rejected_before_effects(cfg, host):
    cfg["connections"][0]["remote_prefixes"] = ["10.64.16.0/24"]
    with pytest.raises(underlay.UnderlayError, match="same destination"):
        underlay.reconcile_locked(cfg, reconfigure=True)
    assert not host.calls


def test_narrower_vpn_route_inside_local_range_remains_valid(cfg, host):
    cfg["gateway"]["local_prefixes"] = ["10.0.0.0/8"]
    cfg["connections"][0]["remote_prefixes"] = ["10.0.0.0/16"]
    underlay.reconcile_locked(cfg, reconfigure=True)
    assert underlay.ready(cfg)
    assert not any(
        c[:5] == ["ip", "-j", "-4", "route", "get"] and c[-1] == "10.0.0.0" for c in host.calls
    )


def test_same_prefix_vpn_kernel_route_invalidates_readiness(cfg, host):
    underlay.reconcile_locked(cfg, reconfigure=True)
    host.routes.append({"dst": "10.64.16.0/24", "dev": "xfrm0", "protocol": "bgp"})
    assert not underlay.ready(cfg)


def test_symlink_file_is_rejected_without_writing(tmp_path):
    original = tmp_path / "original"
    original.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(original)
    with pytest.raises(OSError):
        underlay._read(link)
    assert original.read_text() == "keep"


def test_deleted_owned_dropin_is_restored_by_unchanged_apply(cfg, host):
    underlay.reconcile_locked(cfg, reconfigure=True)
    host.dropin.unlink()
    assert not underlay.ready(cfg)
    underlay.reconcile_locked(cfg, reconfigure=True)
    assert underlay.ready(cfg)


def test_matching_network_file_change_retires_only_owned_dropin(cfg, host):
    underlay.reconcile_locked(cfg, reconfigure=True)
    old = host.dropin
    foreign = old.with_name("80-operator.conf")
    foreign.write_text("# retained operator policy\n")
    host.network_file = host.network_file.with_name("20-new-primary.network")
    host.network_file.write_text("[Match]\nName=eth0\n[Network]\nDHCP=ipv4\n")
    host.loaded = False
    with pytest.raises(underlay.UnderlayError, match="normal apply"):
        underlay.reconcile_locked(cfg)
    underlay.reconcile_locked(cfg, reconfigure=True)
    assert not old.exists()
    assert foreign.exists()
    assert host.dropin.exists()
    assert underlay.ready(cfg)


def test_writable_ancestor_is_not_trusted(tmp_path):
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    path = unsafe / "receipt"
    path.write_text("{}")
    with pytest.raises(underlay.UnderlayError, match="ancestor"):
        underlay._read(path)


def test_classless_dhcp_gateway_matches_networkd_destination_selection():
    network = underlay.Network(
        "eth0",
        2,
        "10.64.0.5",
        "169.254.169.1",
        "/run/a.network",
        "x",
        (),
        dhcp_routes=(("10.0.0.0/8", "169.254.169.2"),),
    )
    assert underlay._gateway("10.64.16.0/24", network) == "169.254.169.2"
    assert underlay._gateway("192.0.2.8/32", network) == "169.254.169.1"
    assert "169.254.169.2" in underlay._route_command("add", "10.64.16.0/24", network)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("configured", True),
        ("configuring,configured", True),
        ("configuring", False),
        ("configured,removing", False),
        ("foreign", False),
    ],
)
def test_networkd_renewal_state_is_a_bitset(state, expected):
    assert underlay._configured({"ConfigState": state}) is expected
