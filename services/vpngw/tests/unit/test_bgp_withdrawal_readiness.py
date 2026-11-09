"""Withdrawal ends route obligations while durable plaintext protection survives."""

import json
from types import SimpleNamespace

import pytest
from tests.unit.test_ha_tunnel_selection import native_bgp_effects

from nebius_vpngw.agent.vm_ha.forwarding import SCHEMA, DestinationProtection
from nebius_vpngw.agent.vm_ha.runtime import (
    BGPExportState,
    BoundRouteRuntime,
    LocalDataPlaneObservation,
)
from nebius_vpngw.agent.vm_ha.tunnels import Health
from nebius_vpngw.agent.vm_ha_controller import DataPlaneMode
from nebius_vpngw.ha_tunnel_policy import connection_policies


@pytest.mark.parametrize("ordinary", [False, True])
@pytest.mark.parametrize("state", ["withdrawn", "advertised", "required", "unknown"])
def test_withdrawn_optional_prefix_does_not_poison_native_selection(tmp_path, ordinary, state):
    effects, _, responses = native_bgp_effects(tmp_path)
    if ordinary:
        del effects.cfg["vm_ha"]
    if state == "required":
        effects.cfg["connections"][0]["remote_prefixes"].append("10.2.0.0/16")
    policy = connection_policies(effects.cfg)[0]
    ledger = tmp_path / "destination-protection.json"
    ledger.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "cluster": "ordinary" if ordinary else "cluster",
                "admitted": {"10.2.0.0/16": "site"},
            }
        )
    )
    ledger.chmod(0o600)
    original = ledger.read_bytes()
    command = ("vtysh", "-c", f"show bgp ipv4 unicast neighbors {policy.paths[0].peer} routes json")
    if state == "advertised":
        responses[command] = json.dumps(
            {
                "routes": {
                    "10.1.0.0/16": [{"valid": True}],
                    "10.2.0.0/16": [{"valid": True}],
                }
            }
        )
    elif state == "unknown":
        responses[command] = "{}"
    expected = {"withdrawn": Health.HEALTHY, "unknown": Health.UNKNOWN}.get(state, Health.DOWN)
    assert effects.verify(policy, None) is expected
    protection = DestinationProtection(
        ledger,
        cluster="ordinary" if ordinary else "cluster",
        policies=(policy,),
        local_prefixes=("10.9.0.0/16",),
    )
    assert "10.2.0.0/16" in protection._destinations(protection._ledger())
    assert ledger.read_bytes() == original


@pytest.mark.parametrize("state", ["withdrawn", "advertised", "required", "unknown"])
def test_ha_readiness_distinguishes_protection_history_from_current_obligation(tmp_path, state):
    effects, _, _ = native_bgp_effects(tmp_path)
    ledger = tmp_path / "destination-protection.json"
    ledger.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "cluster": "cluster",
                "admitted": {
                    "10.2.0.0/16": "site",
                },
            }
        )
    )
    ledger.chmod(0o600)
    protection = DestinationProtection(
        ledger,
        cluster="cluster",
        policies=connection_policies(effects.cfg),
        local_prefixes=("10.9.0.0/16",),
    )
    protection.ready = lambda: True  # Native filter verification is a separate boundary.
    original = ledger.read_bytes()
    observation = LocalDataPlaneObservation(
        service_healthy=True,
        forwarding_enabled=False,
        static_prefixes=frozenset(),
        configured_bgp_sessions=frozenset({"peer"}),
        established_bgp_sessions=frozenset({"peer"}),
        learned_bgp_prefixes=frozenset({"10.2.0.0/16"}) if state == "advertised" else frozenset(),
        usable_xfrm_prefixes=frozenset(),
        observed_bgp_policy_digest="a" * 64,
        bgp_export_state=BGPExportState.MATCH,
    )

    def observe():
        if state == "unknown":
            raise RuntimeError("FRR observation unavailable")
        return observation

    runtime = object.__new__(BoundRouteRuntime)
    runtime.data_plane = SimpleNamespace(
        destination_protection=protection,
        observe=observe,
        mode=lambda: DataPlaneMode.ACTIVE,
    )
    runtime.static_manifest = SimpleNamespace(prefixes=frozenset())
    runtime.bgp_policies = [{"remote_prefixes": ["10.2.0.0/16"] if state == "required" else []}]
    runtime.expected_import_policy_digest = "a" * 64
    runtime.static_readiness_provider = None
    runtime.passive_bgp_policy_provider = None
    runtime.bgp_override_readiness_provider = None
    result = runtime.readiness()
    assert result.xfrm_ready is (state == "withdrawn")
    assert result.sample_complete is (state != "unknown")
    assert result.bgp_ready is (state in {"withdrawn", "advertised"})
    assert ledger.read_bytes() == original


@pytest.mark.parametrize(
    "rib",
    [
        [],
        {"unexpected": {}},
        {"routes": []},
        {"routes": {"bad-prefix": [{}]}},
        {"routes": {"10.2.0.0/16": []}},
    ],
)
def test_malformed_native_rib_is_unknown_instead_of_confirmed_withdrawal(tmp_path, rib):
    from pathlib import Path

    from nebius_vpngw.agent.vm_ha.runtime import CommandResult, SystemDataPlaneRuntime

    def runner(argv, timeout):
        command = Path(argv[0]).name
        if command == "systemctl":
            return CommandResult(0, "active\n")
        if command == "sysctl":
            return CommandResult(0, "0\n")
        if command == "vtysh":
            if argv[-1] == "show bgp summary json":
                return CommandResult(
                    0, json.dumps({"ipv4Unicast": {"peers": {"169.254.1.2": {"state": "Idle"}}}})
                )
            if argv[-1] == "show bgp ipv4 unicast json":
                return CommandResult(0, json.dumps(rib))
            if argv[-1] == "show running-config":
                return CommandResult(0, "")
        if command == "ip":
            return CommandResult(0, "[]" if "-j" in argv else "")
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "state.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=("169.254.1.2",),
        expected_bgp_policy_digest="a" * 64,
        expected_bgp_exports={"169.254.1.2": ()},
        runner=runner,
    )
    with pytest.raises(RuntimeError, match="FRR RIB"):
        runtime.observe()
