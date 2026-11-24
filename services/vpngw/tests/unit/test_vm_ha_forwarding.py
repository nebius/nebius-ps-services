import ipaddress
import json
import subprocess

import pytest
from tests.unit.test_ha_tunnel_selection import config

from nebius_vpngw.agent.vm_ha.forwarding import DestinationProtection, objects
from nebius_vpngw.ha_tunnel_policy import connection_policies


def verdict(rows, address, interface):
    """Evaluate the generated subset independently of its builder."""
    for row in rows:
        if "rule" not in row:
            continue
        expr = row["rule"]["expr"]
        right = expr[0]["match"]["right"]
        network = (
            right
            if isinstance(right, str)
            else f"{right['prefix']['addr']}/{right['prefix']['len']}"
        )
        if ipaddress.ip_address(address) not in ipaddress.ip_network(network):
            continue
        if len(expr) == 3:
            allowed = expr[1]["match"]["right"]
            if interface in ([allowed] if isinstance(allowed, str) else allowed["set"]):
                continue
        return next(iter(expr[-1]))
    return "continue"


def test_narrow_vpn_destination_cannot_escape_broad_local_prefix():
    rows = objects({"10.20.0.0/16": ("xfrm0",)}, ("10.0.0.0/8",))
    assert verdict(rows, "10.20.0.1", "eth0") == "drop"
    assert verdict(rows, "10.20.0.1", "xfrm0") == "return"
    assert verdict(rows, "10.30.0.1", "eth0") == "return"


def test_narrow_local_prefix_survives_protected_default_route():
    rows = objects({"0.0.0.0/0": ("xfrm0",)}, ("10.20.0.0/16",))
    assert verdict(rows, "10.20.0.1", "eth0") == "return"
    assert verdict(rows, "192.0.2.1", "eth0") == "drop"
    assert verdict(rows, "192.0.2.1", "xfrm0") == "return"


def test_ambiguous_equal_prefix_keeps_global_fence():
    with pytest.raises(ValueError, match="ambiguous"):
        objects({"10.20.0.0/16": ("xfrm0",)}, ("10.20.0.0/16",))


def test_retained_destination_without_owner_is_always_dropped():
    rows = objects({"192.0.2.0/24": ()}, ())
    assert verdict(rows, "192.0.2.1", "eth0") == "drop"
    assert verdict(rows, "192.0.2.1", "xfrm0") == "drop"


@pytest.mark.parametrize(
    "destination,prefix",
    [
        ("10.200.2.1", "10.200.2.1/32"),
        ("10.200.2.1/32", "10.200.2.1/32"),
        ("10.200.2.0/24", "10.200.2.0/24"),
        ("default", "0.0.0.0/0"),
    ],
)
def test_admission_matches_native_route_destinations(tmp_path, monkeypatch, destination, prefix):
    protection = DestinationProtection(
        tmp_path / "ledger",
        cluster="cluster",
        policies=connection_policies(config("bgp")),
        local_prefixes=(),
    )
    installed = []
    monkeypatch.setattr(protection, "install", lambda: installed.append(protection._ledger()))

    def run(argv, **kwargs):
        assert argv == ["ip", "-j", "-4", "route", "show", "table", "main"]
        return subprocess.CompletedProcess(
            argv, 0, json.dumps([{"dst": destination, "dev": "xfrm0"}])
        )

    protection.run = run
    protection.admit(frozenset({prefix}))
    assert installed == [{prefix: "site"}]
    assert verdict(protection._expected(), prefix.split("/")[0], "eth0") == "drop"


@pytest.mark.parametrize(
    "kind,identity,ready",
    [
        ("xfrm", 100, True),
        ("xfrm", "0x64", True),
        ("xfrm", "100", True),
        ("dummy", "0x64", False),
        ("dummy", 100, False),
        ("xfrm", 101, False),
        ("xfrm", "0x65", False),
        ("xfrm", "invalid", False),
        ("xfrm", None, False),
        ("xfrm", 100.0, False),
        (None, None, True),
    ],
)
def test_absent_path_is_safe_but_reused_interface_identity_is_not(tmp_path, kind, identity, ready):
    protection = DestinationProtection(
        tmp_path / "ledger",
        cluster="cluster",
        policies=connection_policies(config()),
        local_prefixes=(),
    )

    def run(argv, **kwargs):
        if argv[0] == "nft":
            value = {"nftables": protection._expected()}
        else:
            assert argv == ["ip", "-d", "-j", "link", "show"]
            value = (
                []
                if kind is None
                else [
                    {
                        "ifname": "xfrm0",
                        "linkinfo": {"info_kind": kind, "info_data": {"if_id": identity}},
                    }
                ]
            )
        return subprocess.CompletedProcess(argv, 0, json.dumps(value))

    protection.run = run
    assert protection.ready() is ready


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            RuntimeError("destination protection differs from admitted intent"),
            "protection-intent-drift",
        ),
        (RuntimeError("protected interface identity differs"), "interface-identity-drift"),
        (RuntimeError("secret-canary"), "observation-unavailable"),
        (subprocess.TimeoutExpired(["secret-canary"], 1), "observation-unavailable"),
    ],
)
def test_protection_failure_diagnostic_has_closed_reason(tmp_path, monkeypatch, error, reason):
    from nebius_vpngw.agent.vm_ha import diagnostics

    events = []
    protection = DestinationProtection(
        tmp_path / "ledger", cluster="cluster", policies=(), local_prefixes=()
    )
    monkeypatch.setattr(protection, "verify", lambda: (_ for _ in ()).throw(error))
    monkeypatch.setattr(
        diagnostics, "diagnostic", lambda event, **fields: events.append((event, fields))
    )
    assert not protection.ready()
    assert events == [("routing-safety-observation", {"scope": "protection", "reason": reason})]


def test_apipa_safety_inventory_includes_configured_local_and_retained_workloads(tmp_path):
    from nebius_vpngw.agent.vm_ha.forwarding import SCHEMA

    ledger = tmp_path / "ledger"
    ledger.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "cluster": "cluster",
                "admitted": {"169.254.128.0/24": "retired-connection"},
            }
        )
    )
    ledger.chmod(0o600)
    protection = DestinationProtection(
        ledger,
        cluster="cluster",
        policies=connection_policies(config("bgp")),
        local_prefixes=("169.254.64.0/24",),
    )
    assert protection.protected_prefixes() == frozenset(
        {"169.254.64.0/24", "169.254.128.0/24", "10.1.0.0/16"}
    )


@pytest.mark.parametrize("correct", [False, True])
def test_shared_admission_requires_table_and_interface_on_the_same_route(
    tmp_path, monkeypatch, correct
):
    from dataclasses import replace

    prefix = "10.200.2.0/24"
    policy = replace(
        connection_policies(config("bgp"))[0],
        enforced_sources=("10.0.1.0/24",),
        enforced_destinations=frozenset({prefix}),
        enforced_table=51002,
        ordinary_shared=frozenset({prefix}),
    )
    protection = DestinationProtection(
        tmp_path / "ledger", cluster="cluster", policies=(policy,), local_prefixes=()
    )
    monkeypatch.setattr(protection, "install", lambda: None)
    rows = [
        {"dst": prefix, "dev": "xfrm0", "table": 254},
        {"dst": prefix, "dev": "xfrm0" if correct else "foreign0", "table": 51002},
    ]
    protection.run = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(rows))
    if correct:
        protection.admit(frozenset({prefix}))
        assert protection._ledger() == {prefix: "site"}
    else:
        with pytest.raises(RuntimeError, match="ambiguous"):
            protection.admit(frozenset({prefix}))
