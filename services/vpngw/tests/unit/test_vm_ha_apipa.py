"""An ordinary DHCP renewal must not fence a proven, active BGP owner."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.agent.vm_ha.apipa import PeerRoute, harmless_dhcp_route
from nebius_vpngw.agent.vm_ha.runtime import CommandResult, SystemDataPlaneRuntime
from nebius_vpngw.agent.vm_ha_controller import DataPlaneMode

PEER = PeerRoute("169.254.10.2", "169.254.10.1", "xfrm1", 1)


@pytest.fixture
def evidence():
    route = dict(
        dst="169.254.0.0/16",
        gateway="10.0.0.1",
        dev="eth0",
        protocol="dhcp",
        prefsrc="10.0.0.2",
        metric=100,
        flags=[],
    )
    owner = dict(
        Family=2,
        Destination=[169, 254, 0, 0],
        DestinationPrefixLength=16,
        Gateway=[10, 0, 0, 1],
        PreferredSource=[10, 0, 0, 2],
        Priority=100,
        Table=254,
        Protocol=16,
        Type=1,
        ConfigSource="DHCPv4",
        ConfigState="configured",
        ConfigProvider=[169, 254, 169, 1],
    )
    return dict(
        broad=[route],
        rules=[
            {"priority": 0, "src": "all", "table": "local"},
            {"priority": 32766, "src": "all", "table": "main"},
            {"priority": 32767, "src": "all", "table": "default"},
        ],
        network=dict(
            Name="eth0",
            Type="ether",
            Routes=[owner],
            Addresses=[
                dict(
                    Family=2,
                    Address=[10, 0, 0, 2],
                    ConfigSource="DHCPv4",
                    ConfigState="configured",
                    ConfigProvider=[169, 254, 169, 1],
                )
            ],
        ),
        addresses=[dict(ifname="xfrm1", addr_info=[dict(family="inet", local=PEER.source)])],
        routes=[dict(dst=PEER.peer, dev="xfrm1", protocol="static", scope="link", flags=[])],
        links=[
            dict(
                ifname="xfrm1",
                flags=["UP"],
                linkinfo=dict(info_kind="xfrm", info_data=dict(if_id="0x1")),
            )
        ],
        selected=[dict(dst=PEER.peer, dev="xfrm1", **{"from": PEER.source}, flags=[])],
        protected=["10.20.0.0/24"],
        peers=(PEER,),
        configured=frozenset({PEER.peer}),
        calls=[],
    )


def reader(e):
    def run(*argv):
        e["calls"].append(argv)
        assert "oif" not in argv
        if "exact" in argv:
            key = "broad"
        elif "networkctl" in argv[0]:
            key = "network"
        elif "rule" in argv:
            key = "rules"
        elif "address" in argv:
            key = "addresses"
        elif "get" in argv:
            key = "selected"
        else:
            raise AssertionError(argv)
        value = e[key]
        if isinstance(value, Exception):
            raise value
        return CommandResult(0, json.dumps(value))

    return run


def prove(e):
    return harmless_dhcp_route(
        peers=e["peers"],
        configured_peers=e["configured"],
        interface="eth0",
        protected_prefixes=e["protected"],
        routes=e["routes"],
        links=e["links"],
        run=reader(e),
        ip_command="/usr/sbin/ip",
        networkctl_command="/usr/bin/networkctl",
    )


def test_native_dhcp_renewal_with_exact_peer_paths_is_safe(evidence):
    assert prove(evidence)
    assert evidence["calls"][-1][-4:] == ("get", PEER.peer, "from", PEER.source)


@pytest.mark.parametrize(
    "defect",
    [
        "foreign-protocol",
        "foreign-device",
        "foreign-owner",
        "foreign-provider",
        "wrong-gateway",
        "wrong-source",
        "wrong-metric",
        "pending-dhcp",
        "duplicate-broad",
        "multipath-broad",
        "missing-peer",
        "extra-peer",
        "empty-peers",
        "non-apipa",
        "missing-host-route",
        "wrong-host-route",
        "duplicate-host-route",
        "multipath-host-route",
        "missing-link",
        "link-down",
        "wrong-id",
        "wrong-kind",
        "missing-source",
        "diverted-lookup",
        "policy-table",
        "multipath-lookup",
        "lookup-failed",
        "configured-apipa-workload",
        "default-workload",
        "invalid-workload",
        "malformed-links",
        "malformed-network",
    ],
)
def test_uncertain_or_unsafe_route_cannot_be_admitted(evidence, defect):
    e = evidence
    if defect == "foreign-protocol":
        e["broad"][0]["protocol"] = "static"
    elif defect == "foreign-device":
        e["broad"][0]["dev"] = "eth1"
    elif defect == "foreign-owner":
        e["network"]["Routes"][0]["ConfigSource"] = "foreign"
    elif defect == "foreign-provider":
        e["network"]["Addresses"][0]["ConfigProvider"] = [10, 0, 0, 9]
    elif defect == "wrong-gateway":
        e["broad"][0]["gateway"] = "10.0.0.9"
    elif defect == "wrong-source":
        e["broad"][0]["prefsrc"] = "10.0.0.9"
    elif defect == "wrong-metric":
        e["broad"][0]["metric"] = 200
    elif defect == "pending-dhcp":
        e["network"]["Routes"][0]["ConfigState"] = "configuring"
    elif defect == "duplicate-broad":
        e["broad"] *= 2
    elif defect == "multipath-broad":
        e["broad"][0]["nexthops"] = []
    elif defect == "missing-peer":
        e["peers"] = ()
    elif defect == "extra-peer":
        e["configured"] |= {"169.254.20.2"}
    elif defect == "empty-peers":
        e["peers"] = ()
        e["configured"] = frozenset()
    elif defect == "non-apipa":
        e["peers"] = (PeerRoute("10.0.0.8", PEER.source, PEER.interface, 1),)
        e["configured"] = frozenset({"10.0.0.8"})
    elif defect == "missing-host-route":
        e["routes"] = []
    elif defect == "wrong-host-route":
        e["routes"][0]["dev"] = "eth0"
    elif defect == "duplicate-host-route":
        e["routes"] *= 2
    elif defect == "multipath-host-route":
        e["routes"][0]["nexthops"] = []
    elif defect == "missing-link":
        e["links"] = []
    elif defect == "link-down":
        e["links"][0]["flags"] = []
    elif defect == "wrong-id":
        e["links"][0]["linkinfo"]["info_data"]["if_id"] = 2
    elif defect == "wrong-kind":
        e["links"][0]["linkinfo"]["info_kind"] = "dummy"
    elif defect == "missing-source":
        e["addresses"][0]["addr_info"] = []
    elif defect == "diverted-lookup":
        e["selected"][0]["dev"] = "eth0"
    elif defect == "policy-table":
        e["selected"][0]["table"] = 100
    elif defect == "multipath-lookup":
        e["selected"][0]["nexthops"] = []
    elif defect == "lookup-failed":
        e["selected"] = RuntimeError("unavailable")
    elif defect == "configured-apipa-workload":
        e["protected"] = ["169.254.128.0/24"]
    elif defect == "default-workload":
        e["protected"] = ["0.0.0.0/0"]
    elif defect == "invalid-workload":
        e["protected"] = ["invalid"]
    elif defect == "malformed-links":
        e["links"] = {}
    elif defect == "malformed-network":
        e["network"] = {"Routes": [None]}
    assert not prove(e)


def test_cleanup_race_restores_ordinary_clean_condition(evidence):
    evidence["broad"] = []
    assert prove(evidence)
    assert len(evidence["calls"]) == 1


@pytest.mark.parametrize(
    "case,ready",
    [
        ("active", True),
        ("passive", False),
        ("originating", False),
        ("blocked", False),
        ("static", False),
        ("mixed", False),
        ("no-peers", False),
        ("table220", False),
        ("underlay", False),
        ("retained-overlap", False),
        ("learned-overlap", False),
        ("unknown-ledger", False),
    ],
)
def test_actual_collector_admits_only_active_bgp_with_all_existing_checks(
    tmp_path, monkeypatch, evidence, case, ready
):
    e = evidence
    native = reader(e)

    def runner(argv, timeout):
        assert 0 < timeout <= 5
        if Path(argv[0]).name == "systemctl":
            return CommandResult(0, "active")
        if Path(argv[0]).name == "sysctl":
            return CommandResult(0, "1")
        if Path(argv[0]).name == "swanctl":
            return CommandResult(0, "")
        if Path(argv[0]).name == "vtysh":
            if argv[-1] == "show bgp summary json":
                return CommandResult(
                    0, json.dumps({"ipv4Unicast": {"peers": {PEER.peer: {"state": "Idle"}}}})
                )
            if argv[-1] == "show bgp ipv4 unicast json":
                return CommandResult(
                    0,
                    json.dumps(
                        {
                            "routes": {"169.254.128.0/24": [{"valid": True, "bestpath": True}]}
                            if case == "learned-overlap"
                            else {}
                        }
                    ),
                )
            return CommandResult(0, "")
        if tuple(argv[1:]) == ("-j", "-4", "route", "show", "table", "all"):
            return CommandResult(0, json.dumps(e["routes"]))
        if tuple(argv[1:]) == ("rule", "show"):
            return CommandResult(0, "220: from all lookup 220" if case == "table220" else "")
        if tuple(argv[1:]) == ("route", "show", "169.254.0.0/16"):
            return CommandResult(0, "169.254.0.0/16 via 10.0.0.1 dev eth0 proto dhcp")
        if "xfrm" in argv and "link" not in argv:
            return CommandResult(0, "[]")
        if "link" in argv:
            return CommandResult(0, json.dumps(e["links"]))
        return native(*argv)

    def protected():
        if case == "unknown-ledger":
            raise RuntimeError("unproven")
        return frozenset({"169.254.0.0/16"} if case == "retained-overlap" else e["protected"])

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "state",
        guard_path=tmp_path / "guard",
        configured_bgp_sessions=() if case in {"static", "no-peers"} else (PEER.peer,),
        expected_bgp_policy_digest="a" * 64,
        runner=runner,
        bgp_peer_routes=e["peers"],
        physical_interface="eth0",
        destination_protection=SimpleNamespace(protected_prefixes=protected, ready=lambda: True),
        underlay_verifier=lambda: case != "underlay",
        static_tunnel_names=("static",) if case in {"static", "mixed"} else (),
    )
    monkeypatch.setattr(
        runtime,
        "mode",
        lambda: (
            DataPlaneMode(case)
            if case in {"passive", "originating", "blocked"}
            else DataPlaneMode.ACTIVE
        ),
    )
    observed = runtime.observe()
    assert observed.routing_hygiene_ready is ready
    assert observed.protection_ready
    if case in {"passive", "originating", "blocked", "static", "mixed", "no-peers"}:
        assert not e["calls"]


def test_proof_commands_share_the_existing_observation_deadline(tmp_path, evidence):
    elapsed = [0.0]
    timeouts = []
    native = reader(evidence)

    def runner(argv, timeout):
        timeouts.append(timeout)
        elapsed[0] += 0.35
        return native(*argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "state",
        guard_path=tmp_path / "guard",
        configured_bgp_sessions=(PEER.peer,),
        expected_bgp_policy_digest="a" * 64,
        runner=runner,
        monotonic_clock=lambda: elapsed[0],
    )
    runtime.observation_deadline = 0.9
    assert not harmless_dhcp_route(
        peers=evidence["peers"],
        configured_peers=evidence["configured"],
        interface="eth0",
        protected_prefixes=evidence["protected"],
        routes=evidence["routes"],
        links=evidence["links"],
        run=runtime._run,
        ip_command=runtime.commands.ip,
        networkctl_command=runtime.commands.networkctl,
    )
    assert timeouts == pytest.approx([0.9, 0.55, 0.2])
    assert not any("get" in call for call in evidence["calls"])


@pytest.mark.parametrize(
    "selector",
    [{"ipproto": "tcp", "dport": 179}, {"uidrange": "100-200"}, {"fwmark": "0x1"}, {"iif": "eth0"}],
)
def test_custom_policy_rules_cannot_hide_bgp_path_diversion(evidence, selector):
    evidence["rules"].insert(1, {"priority": 100, "src": "all", "table": 100, **selector})
    assert not prove(evidence)
    assert not any("get" in call for call in evidence["calls"])


@pytest.mark.parametrize(
    "selected",
    [
        [{"dev": "xfrm1"}],
        [{"dev": "xfrm1", "dst": "169.254.99.2", "from": PEER.source}],
        [{"dev": "xfrm1", "dst": PEER.peer, "from": "169.254.99.1"}],
    ],
)
def test_native_lookup_must_bind_exact_peer_and_source(evidence, selected):
    evidence["selected"] = selected
    assert not prove(evidence)


def test_every_configured_peer_needs_its_own_exact_path(evidence):
    second = PeerRoute("169.254.20.2", "169.254.20.1", "xfrm2", 2)
    evidence["peers"] = (PEER, second)
    evidence["configured"] = frozenset({PEER.peer, second.peer})
    assert not prove(evidence)
    assert any(call[-4:] == ("get", PEER.peer, "from", PEER.source) for call in evidence["calls"])
