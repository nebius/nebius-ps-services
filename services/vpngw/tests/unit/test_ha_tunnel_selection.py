from __future__ import annotations

import json
from dataclasses import replace

import pytest

from nebius_vpngw.agent.vm_ha.tunnel_observation import path_health, scoped_ike_ids
from nebius_vpngw.agent.vm_ha.tunnels import (
    Authority,
    Health,
    TunnelController,
    TunnelScopeError,
    read_state,
)
from nebius_vpngw.ha_tunnel_policy import connection_policies, selections
from nebius_vpngw.ipsec_observation import Inventory
from nebius_vpngw.ipsec_observation import command as inventory_command
from nebius_vpngw.tunnel_state import collect_tunnel_state


def config(mode="static"):
    return {
        "vm_ha": {"cluster_id": "cluster"},
        "gateway": {},
        "connections": [
            {
                "name": "site",
                "routing_mode": mode,
                "remote_prefixes": ["10.1.0.0/16"],
                "tunnels": [
                    {
                        "name": name,
                        "ha_role": role,
                        "remote_public_ip": f"192.0.2.{i}",
                        "inner_remote_ip": f"169.254.{i}.2",
                        "inner_local_ip": f"169.254.{i}.1",
                        "inner_cidr": f"169.254.{i}.0/30",
                    }
                    for i, (name, role) in enumerate(
                        (("preferred", "active"), ("backup", "passive")), 1
                    )
                ],
            }
        ],
    }


AUTH = Authority("cluster", "node-a", "allocation", "epoch", 1, "a" * 64)


class Effects:
    def __init__(self):
        self.current = AUTH
        self.health = {"preferred": Health.HEALTHY, "backup": Health.HEALTHY}
        self.calls = []
        self.fail_activate = False

    def revalidate(self, authority):
        return authority == self.current

    def active(self, authority):
        return self.revalidate(authority)

    def candidate(self, policy, target):
        return self.health[target]

    def observe(self, policy, target, *, selected):
        return self.health[target]

    def suspend(self, policy, authority):
        self.calls.append(("suspend", policy.name))

    def activate(self, policy, target, authority):
        self.calls.append(("activate", target))
        if self.fail_activate:
            raise RuntimeError("injected failure")

    def verify(self, policy, target):
        return self.health[target or policy.preferred]

    def authority_restored(self, policy, target, authority):
        return self.revalidate(authority) and not self.fail_activate

    def fence(self):
        self.calls.append(("fence",))


def test_ordinary_static_order_and_no_policy():
    cfg = config()
    del cfg["vm_ha"]
    assert connection_policies(cfg) == ()
    endpoints = collect_tunnel_state(cfg)[2]
    assert [e["remote_prefixes"] for e in endpoints] == [["10.1.0.0/16"]] * 2


def test_static_defaults_and_selection_preserve_interfaces_and_roles():
    cfg = config()
    assert selections(cfg) == {"site": "preferred"}
    before = collect_tunnel_state(cfg)
    cfg["_ha_tunnel_selections"] = {"site": "backup"}
    after = collect_tunnel_state(cfg)
    assert [(e["name"], e["if_id"]) for e in before[2]] == [
        (e["name"], e["if_id"]) for e in after[2]
    ]
    assert [e["remote_prefixes"] for e in after[2]] == [[], ["10.1.0.0/16"]]
    assert cfg["connections"][0]["tunnels"][0]["ha_role"] == "active"


def test_disjoint_paths_are_preserved_and_switching_unavailable():
    cfg = config()
    cfg["connections"][0]["tunnels"][1]["static_routes"] = {"remote_prefixes": ["10.2.0.0/16"]}
    assert connection_policies(cfg)[0].unavailable_reason == "unequal-destination-coverage"
    assert selections(cfg) == {}
    assert all(e["remote_prefixes"] for e in collect_tunnel_state(cfg)[2])


@pytest.mark.parametrize("mode", ["static", "bgp"])
@pytest.mark.parametrize("prefixes", [None, []])
def test_unspecified_destinations_leave_native_policy_and_reject_switch(tmp_path, mode, prefixes):
    cfg = config(mode)
    cfg["connections"][0]["remote_prefixes"] = prefixes
    cfg["connections"][0]["bgp"] = {"remote_prefixes": prefixes}
    policy = connection_policies(cfg)[0]
    assert policy.unavailable_reason == "destination-coverage-unspecified"
    assert selections(cfg) == {}
    effects = Effects()
    selector = TunnelController(cfg, tmp_path / "state.json", effects)
    selector.tick(AUTH, 100)
    with pytest.raises(ValueError, match="selection is unavailable"):
        selector.request("failover", "backup", AUTH, 101)
    assert effects.calls == []
    assert selector.state["selected"] == {}
    assert selector.state["episode"] is None


@pytest.mark.parametrize("mode", ["static", "bgp"])
def test_null_connection_destinations_accept_complete_nested_coverage(mode):
    cfg = config(mode)
    connection = cfg["connections"][0]
    connection["remote_prefixes"] = None
    if mode == "static":
        for tunnel in connection["tunnels"]:
            tunnel["static_routes"] = {"remote_prefixes": ["10.1.0.0/16"]}
    else:
        connection["bgp"] = {"remote_prefixes": ["10.1.0.0/16"]}
    policy = connection_policies(cfg)[0]
    assert policy.eligible and policy.destinations == {"10.1.0.0/16"}


def test_manual_progress_is_persisted_and_retry_is_idempotent(tmp_path):
    effects = Effects()
    path = tmp_path / "state.json"
    selector = TunnelController(config(), path, effects)
    operation = selector.request("failover", "backup", AUTH, 100)
    assert read_state(path)["operation"]["phase"] == "suspend"
    assert selector.request("failover", "backup", AUTH, 101) == operation
    for now in range(101, 105):
        selector = TunnelController(config(), path, effects)
        selector.tick(AUTH, now)
        if selector._pending():
            assert selector.request("failover", "backup", AUTH, now) == operation
    assert selector.state["operation"]["phase"] == "succeeded"
    assert selector.state["selected"] == {"site": "backup"}
    assert effects.calls == [("suspend", "site"), ("activate", "backup")]


@pytest.mark.parametrize("mode", ["static", "bgp"])
def test_failback_already_preferred_verifies_without_native_mutation(tmp_path, mode):
    effects = Effects()
    path = tmp_path / "state.json"
    selector = TunnelController(config(mode), path, effects)
    operation = selector.request("failback", "preferred", AUTH, 100)
    for now in (101, 102, 103, 104):
        selector = TunnelController(config(mode), path, effects)
        selector.tick(AUTH, now)
    assert selector.state["operation"]["id"] == operation
    assert effects.calls == []
    assert selector.state["operation"]["phase"] == "succeeded"
    assert selector.state["operation"]["successes"] == 2
    assert selector.state["episode"] is None


def test_unchanged_failback_does_not_renew_consumed_episode(tmp_path):
    effects = Effects()
    selector = TunnelController(config(), tmp_path / "state", effects)
    selector.bind(AUTH)
    selector._episode(50, AUTH)
    selector.state["episode"].update(exhausted=True, last_observed=99, healthy_since=90)
    selector._save()
    selector.request("failback", "preferred", AUTH, 100)
    selector.tick(AUTH, 101)
    selector.tick(AUTH, 102)
    assert selector.state["operation"]["phase"] == "succeeded"
    assert selector.state["episode"]["started"] == 50
    assert selector.state["episode"]["deadline"] == 80
    assert selector.state["episode"]["healthy_since"] == 90
    assert effects.calls == []


@pytest.mark.parametrize("mode", ["static", "bgp"])
@pytest.mark.parametrize("failure", ["down", "unknown", "fenced", "timeout", "disconnected"])
def test_unchanged_failback_requires_fresh_active_evidence(tmp_path, mode, failure):
    effects = Effects()
    selector = TunnelController(config(mode), tmp_path / "state", effects)
    selector.request("failback", "preferred", AUTH, 100)
    selector.tick(AUTH, 101)
    if failure in {"down", "unknown"}:
        effects.health["preferred"] = Health(failure)
    elif failure == "fenced":
        effects.active = lambda authority: False
    elif failure == "disconnected":

        def disconnected(*args):
            raise RuntimeError("bounded native observation disconnected")

        effects.verify = disconnected
    selector._advance(AUTH, 130 if failure == "timeout" else 102)
    assert selector.state["operation"]["phase"] != "succeeded"
    if selector._pending():
        selector._advance(AUTH, 130)
    assert selector.state["operation"]["phase"] == "failed"
    assert effects.calls == []


def test_consumed_episode_rejects_healthy_manual_switch_without_fencing(tmp_path):
    effects = Effects()
    selector = TunnelController(config(), tmp_path / "state", effects)
    selector.request("failover", "backup", AUTH, 100)
    for now in range(101, 106):
        selector.tick(AUTH, now)
    with pytest.raises(RuntimeError, match="consumed"):
        selector.request("failback", "preferred", AUTH, 140)
    assert ("fence",) not in effects.calls


def test_automatic_backup_is_sticky(tmp_path):
    effects = Effects()
    effects.health["preferred"] = Health.DOWN
    selector = TunnelController(config(), tmp_path / "state", effects)
    for now in range(100, 106):
        selector.tick(AUTH, now)
    effects.health["preferred"] = Health.HEALTHY
    selector.tick(AUTH, 110)
    assert selector.state["selected"] == {"site": "backup"}


def test_unknown_does_not_initiate_switch(tmp_path):
    effects = Effects()
    effects.health["preferred"] = Health.UNKNOWN
    selector = TunnelController(config(), tmp_path / "state", effects)
    selector.tick(AUTH, 100)
    assert selector.state["operation"] is None
    assert selector.state["episode"] is None


def test_episode_not_extended_by_restart_config_retry_or_second_failure(tmp_path):
    effects = Effects()
    effects.health = {"preferred": Health.DOWN, "backup": Health.DOWN}
    path = tmp_path / "state"
    selector = TunnelController(config(), path, effects)
    selector.tick(AUTH, 100)
    deadline = selector.state["episode"]["deadline"]
    changed = replace(AUTH, generation="b" * 64)
    effects.current = changed
    selector = TunnelController(config(), path, effects)
    selector.tick(changed, 120)
    assert selector.state["episode"]["deadline"] == deadline == 130
    selector.tick(changed, 129)
    assert ("fence",) not in effects.calls
    assert not selector.state["episode"]["exhausted"]
    selector.tick(changed, 130)
    assert selector.state["episode"]["exhausted"]
    selector.tick(changed, 140)
    assert selector.state["episode"]["deadline"] == 130


def test_episode_reset_requires_continuous_health_or_new_owner(tmp_path):
    effects = Effects()
    selector = TunnelController(config(), tmp_path / "state", effects)
    selector.bind(AUTH)
    selector._episode(100, AUTH)
    selector.tick(AUTH, 101)
    for now in range(106, 161, 5):
        selector.tick(AUTH, now)
    assert selector.state["episode"] is not None
    selector.tick(AUTH, 161)
    assert selector.state["episode"] is None
    selector._episode(200, AUTH)
    changed = replace(AUTH, incarnation=2)
    effects.current = changed
    selector.bind(changed)
    assert selector.state["episode"] is None


def test_bgp_failed_override_restores_native_policy(tmp_path):
    effects = Effects()
    selector = TunnelController(config("bgp"), tmp_path / "state", effects)
    selector.request("failover", "backup", AUTH, 100)
    for now in (101, 102, 103):
        selector.tick(AUTH, now)
    effects.health["backup"] = Health.DOWN
    selector.tick(AUTH, 104)
    assert selector.state["selected"] == {}
    assert selector.state["operation"]["phase"] == "verify"
    assert ("activate", None) in effects.calls
    for now in (105, 106, 107):
        selector.tick(AUTH, now)


def raw_sas(extra=None):
    return Inventory.from_events(
        [
            {
                "preferred": {
                    "uniqueid": "1",
                    "state": "ESTABLISHED",
                    "local-host": "192.0.2.2",
                    "remote-host": "192.0.2.1",
                    "local-id": "192.0.2.2",
                    "remote-id": "192.0.2.1",
                    "child-sas": {
                        "preferred-1": {
                            "name": "preferred",
                            "state": "INSTALLED",
                            "protocol": "ESP",
                            "if-id-in": "00000064",
                            "if-id-out": "00000064",
                            "reqid": "1",
                            "spi-in": "00000001",
                            "spi-out": "00000002",
                            "local-ts": ["0.0.0.0/0"],
                            "remote-ts": ["0.0.0.0/0"],
                        }
                    },
                }
            },
            *([extra] if extra is not None else []),
        ]
    ).json()


def observation():
    return dict(
        sas=raw_sas(),
        states="src 192.0.2.1 dst 192.0.2.2\n proto esp spi 0x1 reqid 1 mode tunnel\n if_id 0x64\n"
        "src 192.0.2.2 dst 192.0.2.1\n proto esp spi 0x2 reqid 1 mode tunnel\n if_id 0x64\n",
        policies="src 0.0.0.0/0 dst 0.0.0.0/0\n dir out\n tmpl src 192.0.2.2 dst 192.0.2.1 proto esp reqid 1 mode tunnel\n if_id 0x64\n"
        "src 0.0.0.0/0 dst 0.0.0.0/0\n dir in\n tmpl src 192.0.2.1 dst 192.0.2.2 proto esp reqid 1 mode tunnel\n if_id 0x64\n"
        "src 0.0.0.0/0 dst 0.0.0.0/0\n dir fwd\n tmpl src 192.0.2.1 dst 192.0.2.2 proto esp reqid 1 mode tunnel\n if_id 0x64\n",
        links=[
            {
                "ifname": "xfrm0",
                "flags": ["UP"],
                "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": 100}},
            }
        ],
        routes=[{"dst": "10.1.0.0/16", "dev": "xfrm0"}],
        require_routes=True,
    )


def test_rekey_overlap_keeps_exact_installed_replacement_healthy():
    policy = connection_policies(config())[0]
    evidence = observation()
    evidence["sas"] = raw_sas(
        {"preferred": {"uniqueid": "2", "state": "CONNECTING", "child-sas": {}}}
    )
    assert path_health(policy.paths[0], policy, **evidence) is Health.HEALTHY


def test_native_socket_bypass_policies_do_not_invalidate_tunnel_evidence():
    policy = connection_policies(config())[0]
    evidence = observation()
    evidence["policies"] += (
        "src 0.0.0.0/0 dst 0.0.0.0/0\n\tsocket in priority 0\n"
        "src ::/0 dst ::/0\n\tsocket out priority 0\n"
    )
    assert path_health(policy.paths[0], policy, **evidence) is Health.HEALTHY
    evidence["policies"] = "src 0.0.0.0/0 dst 0.0.0.0/0\n\tsocket in priority 0\n"
    assert path_health(policy.paths[0], policy, **evidence) is Health.DOWN


def test_native_host_route_destination_has_implicit_prefix_length():
    policy = replace(connection_policies(config())[0], destinations=frozenset({"10.1.0.1/32"}))
    evidence = observation()
    evidence["routes"] = [{"dst": "10.1.0.1", "dev": "xfrm0"}]
    assert path_health(policy.paths[0], policy, **evidence) is Health.HEALTHY


def test_native_nat_socket_matches_kernel_and_authenticated_public_identity():
    policy = connection_policies(config())[0]
    path = replace(policy.paths[0], outer_pair=("203.0.113.1", "192.0.2.1"))
    evidence = observation()
    evidence["sas"] = evidence["sas"].replace('"local-id":"192.0.2.2"', '"local-id":"203.0.113.1"')
    assert path_health(path, policy, **evidence) is Health.HEALTHY
    evidence["sas"] = evidence["sas"].replace(
        '"local-id":"203.0.113.1"', '"local-id":"203.0.113.99"'
    )
    assert path_health(path, policy, **evidence) is Health.DOWN


@pytest.mark.parametrize("missing", ["states", "policies", "sas", "routes", "links"])
def test_interface_or_partial_evidence_cannot_prove_health(missing):
    policy = connection_policies(config())[0]
    evidence = observation()
    evidence[missing] = (
        []
        if missing in {"routes", "links"}
        else ""
        if missing != "sas"
        else Inventory.from_events([]).json()
    )
    assert path_health(policy.paths[0], policy, **evidence) is Health.DOWN


def test_shared_unrelated_child_blocks_scoped_termination():
    with pytest.raises(ValueError, match="unrelated"):
        scoped_ike_ids(
            raw_sas().replace('"name":"preferred"', '"name":"unrelated"'), {"preferred", "backup"}
        )


@pytest.mark.parametrize("direction", ["in", "out", "fwd"])
@pytest.mark.parametrize("selector", ["src", "dst"])
def test_narrow_kernel_policy_does_not_prove_coverage(direction, selector):
    policy = connection_policies(config())[0]
    evidence = observation()
    old = f"src 0.0.0.0/0 dst 0.0.0.0/0\n dir {direction}"
    new = old.replace(f"{selector} 0.0.0.0/0", f"{selector} 203.0.113.0/24")
    evidence["policies"] = evidence["policies"].replace(old, new)
    assert path_health(policy.paths[0], policy, **evidence) is Health.DOWN


def test_wrong_kernel_template_does_not_prove_coverage():
    policy = connection_policies(config())[0]
    evidence = observation()
    evidence["policies"] = evidence["policies"].replace("tmpl src 192.0.2.1", "tmpl src 192.0.2.99")
    assert path_health(policy.paths[0], policy, **evidence) is Health.DOWN


def test_unproven_suspension_fails_local_operation_without_restore_or_gateway_fence(tmp_path):
    effects = Effects()

    def reject(policy, authority):
        raise TunnelScopeError("shared IKE")

    effects.suspend = reject
    selector = TunnelController(config(), tmp_path / "state", effects)
    selector.request("failover", "backup", AUTH, 100)
    selector.tick(AUTH, 101)
    assert selector.state["operation"]["phase"] == "failed"
    assert selector.state["operation"]["result"] == "suspension-scope-unproven"
    assert selector.state["selected"] == {}
    assert effects.calls == []


@pytest.mark.parametrize("phase_time", [None, 101, 102])
def test_bgp_restart_after_deadline_restores_policy_before_terminal_result(tmp_path, phase_time):
    effects = Effects()
    path = tmp_path / "state"
    selector = TunnelController(config("bgp"), path, effects)
    selector.request("failover", "backup", AUTH, 100)
    if phase_time:
        for now in range(101, phase_time + 1):
            selector.tick(AUTH, now)
    selector = TunnelController(config("bgp"), path, effects)
    selector.tick(AUTH, 140)
    assert selector.state["selected"] == {}
    assert selector.state["operation"]["phase"] == "verify"
    assert effects.calls[-1] == ("activate", None)
    for now in (141, 142):
        selector.tick(AUTH, now)
    assert selector.state["operation"]["phase"] == "failed"
    assert selector.state["operation"]["result"] == "normal-policy-restored"


def native_bgp_effects(tmp_path, peer_state="Idle"):
    import json

    from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects

    cfg = config("bgp")
    cfg["gateway"]["local_prefixes"] = ["10.9.0.0/16"]
    evidence = observation()
    policy = connection_policies(cfg)[0]
    summary = {
        "ipv4Unicast": {
            "peers": {
                policy.paths[0].peer: {"state": "Established"},
                policy.paths[1].peer: {"state": peer_state},
            }
        }
    }
    routes = [
        {"dst": "10.1.0.0/16", "dev": "xfrm0", "gateway": policy.paths[0].peer, "protocol": "bgp"}
    ]
    responses = {
        inventory_command(): evidence["sas"],
        ("ip", "-j", "xfrm", "state"): evidence["states"],
        ("ip", "-j", "xfrm", "policy"): evidence["policies"],
        ("ip", "-d", "-j", "link", "show", "type", "xfrm"): json.dumps(evidence["links"]),
        ("ip", "-j", "-4", "route", "show", "table", "main"): json.dumps(routes),
        ("vtysh", "-c", "show bgp summary json"): json.dumps(summary),
        (
            "vtysh",
            "-c",
            f"show bgp ipv4 unicast neighbors {policy.paths[0].peer} routes json",
        ): json.dumps({"routes": {"10.1.0.0/16": [{"valid": True}]}}),
        (
            "vtysh",
            "-c",
            f"show bgp ipv4 unicast neighbors {policy.paths[0].peer} advertised-routes json",
        ): json.dumps({"totalPrefixCounter": 1, "advertisedRoutes": {"10.9.0.0/16": {}}}),
    }
    effects = NativeTunnelEffects(
        cfg, tmp_path, lambda authority: True, active_revalidate=lambda authority: True
    )
    effects.run = lambda argv: responses[tuple(argv)]
    effects.observer.run = effects.run
    return effects, policy, responses


@pytest.mark.parametrize("prefix", ["10.9.0.0/16", "10.9.0.1/16"])
def test_native_bgp_release_verifies_healthy_coverage_with_down_redundant_peer(tmp_path, prefix):
    effects, policy, _ = native_bgp_effects(tmp_path)
    effects.cfg["gateway"]["local_prefixes"] = [prefix]
    assert effects.verify(policy, None) is Health.HEALTHY


@pytest.mark.parametrize("mode", ["static", "bgp"])
@pytest.mark.parametrize("host_bits", [False, True])
def test_policy_canonicalizes_accepted_prefixes(mode, host_bits):
    cfg = config(mode)
    local = "10.9.0.1/16" if host_bits else "10.9.0.0/16"
    remote = "10.1.0.1/16" if host_bits else "10.1.0.0/16"
    cfg["gateway"]["local_prefixes"] = [local]
    cfg["connections"][0]["remote_prefixes"] = [remote]
    if mode == "static":
        for tunnel in cfg["connections"][0]["tunnels"]:
            tunnel["static_routes"] = {"remote_prefixes": [remote]}
    policy = connection_policies(cfg)[0]
    assert policy.eligible
    assert policy.destinations == frozenset({"10.1.0.0/16"})
    for index, path in enumerate(policy.paths, 1):
        assert path.destinations == frozenset({"10.1.0.0/16"})
        assert path.local_selectors == frozenset({"10.9.0.0/16", f"169.254.{index}.0/30"})
    assert cfg["gateway"]["local_prefixes"] == [local]
    assert cfg["connections"][0]["remote_prefixes"] == [remote]


@pytest.mark.parametrize(
    "prefixes, expected",
    [
        (["10.9.0.0/16"], {"10.9.0.0/16"}),
        (["10.9.0.1/16"], {"10.9.0.0/16"}),
        (["10.9.0.1/16", "2001:db8::1/64"], {"10.9.0.0/16", "2001:db8::/64"}),
    ],
)
def test_controller_base_exports_are_canonical(tmp_path, prefixes, expected):
    from types import SimpleNamespace

    import yaml

    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime

    cfg = config("bgp")
    cfg["gateway"]["local_prefixes"] = prefixes
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    ports = SimpleNamespace(routes=SimpleNamespace(), data_plane=SimpleNamespace())
    runtime = ControllerTunnelRuntime(path, tmp_path, None, ports)
    assert runtime.base_exports == {
        "169.254.1.2": frozenset(expected),
        "169.254.2.2": frozenset(expected),
    }
    for mode in ("static", "bgp"):
        cfg["connections"][0]["routing_mode"] = mode
        for index, tunnel in enumerate(connection_policies(cfg)[0].paths, 1):
            assert tunnel.local_selectors == frozenset({*expected, f"169.254.{index}.0/30"})
    assert not hasattr(runtime, "watchdog")


@pytest.mark.parametrize("level", ["summary", "ipv4Unicast", "peers"])
@pytest.mark.parametrize("malformed", [None, [], "unavailable"])
def test_native_bgp_candidate_malformed_summary_is_unknown(tmp_path, level, malformed):
    import json

    effects, policy, responses = native_bgp_effects(tmp_path)
    summary = malformed
    if level == "peers":
        summary = {"peers": summary}
    if level != "summary":
        summary = {"ipv4Unicast": summary}
    responses[("vtysh", "-c", "show bgp summary json")] = json.dumps(summary)

    assert effects.candidate(policy, policy.preferred) is Health.UNKNOWN


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        ({"state": "Established"}, Health.HEALTHY),
        ({"state": "Idle"}, Health.DOWN),
        ({"state": "unavailable"}, Health.UNKNOWN),
        (None, Health.UNKNOWN),
    ],
)
def test_native_bgp_candidate_distinguishes_peer_evidence(tmp_path, peer, expected):
    import json

    effects, policy, responses = native_bgp_effects(tmp_path)
    peers = {} if peer is None else {policy.paths[0].peer: peer}
    responses[("vtysh", "-c", "show bgp summary json")] = json.dumps(
        {"ipv4Unicast": {"peers": peers}}
    )

    assert effects.candidate(policy, policy.preferred) is expected


def test_native_bgp_host_route_uses_same_canonical_destination_as_received_route(tmp_path):
    import json

    effects, policy, responses = native_bgp_effects(tmp_path)
    policy = replace(policy, destinations=frozenset({"10.1.0.1/32"}))
    route_key = ("ip", "-j", "-4", "route", "show", "table", "main")
    routes = json.loads(responses[route_key])
    routes[0]["dst"] = "10.1.0.1"
    responses[route_key] = json.dumps(routes)
    responses[
        ("vtysh", "-c", f"show bgp ipv4 unicast neighbors {policy.paths[0].peer} routes json")
    ] = '{"routes": {"10.1.0.1/32": [{"valid": true}]}}'
    assert effects.verify(policy, None) is Health.HEALTHY


def test_native_bgp_release_unknown_redundant_peer_stays_fail_closed(tmp_path):
    effects, policy, _ = native_bgp_effects(tmp_path, "unavailable")
    assert effects.verify(policy, None) is Health.UNKNOWN


def test_native_bgp_release_requires_received_route_and_actual_bgp_selection(tmp_path):
    effects, policy, responses = native_bgp_effects(tmp_path)
    key = ("vtysh", "-c", f"show bgp ipv4 unicast neighbors {policy.paths[0].peer} routes json")
    responses[key] = '{"routes": {}}'
    assert effects.verify(policy, None) is Health.DOWN


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("healthy", Health.HEALTHY),
        ("withdrawal", Health.DOWN),
        ("received", Health.DOWN),
        ("selected", Health.DOWN),
        ("exports", Health.DOWN),
        ("unknown-routes", Health.UNKNOWN),
        ("unknown-exports", Health.UNKNOWN),
    ],
)
def test_bgp_override_health_tracks_native_route_evidence(tmp_path, change, expected):
    import json

    effects, policy, responses = native_bgp_effects(tmp_path, peer_state="Established")
    target, backup = policy.paths
    route_key = ("ip", "-j", "-4", "route", "show", "table", "main")
    received_key = ("vtysh", "-c", f"show bgp ipv4 unicast neighbors {target.peer} routes json")
    export_key = (
        "vtysh",
        "-c",
        f"show bgp ipv4 unicast neighbors {target.peer} advertised-routes json",
    )
    responses[("vtysh", "-c", f"show bgp ipv4 unicast neighbors {backup.peer} routes json")] = (
        responses[received_key]
    )
    responses[
        ("vtysh", "-c", f"show bgp ipv4 unicast neighbors {backup.peer} advertised-routes json")
    ] = '{"totalPrefixCounter": 0, "advertisedRoutes": {}}'
    # A retained, verified override can select either configured role.
    path = tmp_path / "tunnel-selection.json"
    selector = TunnelController(effects.cfg, path, effects)
    selector.bind(AUTH)
    selector.state["selected"] = {policy.name: target.name}
    selector._save()
    assert effects.verify(policy, target.name) is Health.HEALTHY

    if change in {"withdrawal", "received"}:
        responses[received_key] = '{"routes": {}}'
    if change in {"withdrawal", "selected"}:
        responses[route_key] = json.dumps(
            [
                {
                    "dst": "10.1.0.0/16",
                    "dev": backup.interface,
                    "gateway": backup.peer,
                    "protocol": "bgp",
                }
            ]
        )
    if change == "exports":
        responses[export_key] = '{"totalPrefixCounter": 0, "advertisedRoutes": {}}'
    if change == "unknown-routes":
        responses[route_key] = "unavailable"
    if change == "unknown-exports":
        responses[export_key] = "unavailable"
    if not change.startswith("unknown"):
        # Session-only health misses confirmed loss of required route coverage.
        assert effects.observe(policy, target.name, selected=True) is Health.HEALTHY
    assert effects.verify(policy, target.name) is expected
    activations = []

    def activate(connection, selected, authority):
        saved = read_state(path)
        assert saved["operation"]["verb"] == "release"
        assert saved["operation"]["phase"] == "activate"
        assert saved["selected"] == {}
        assert authority == AUTH
        activations.append((connection.name, selected))

    effects.activate = activate
    selector = TunnelController(effects.cfg, path, effects)
    selector.tick(AUTH, 100)
    assert selector.state["health"][policy.name] == expected.value
    if expected is Health.DOWN:
        assert selector.state["selected"] == {}
        assert activations == [(policy.name, None)]
        assert read_state(path)["operation"]["phase"] == "verify"
    else:
        assert selector.state["selected"] == {policy.name: target.name}
        assert selector.state["operation"] is None
        assert activations == []


def test_native_suspension_scope_rejects_before_mutations(tmp_path):
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects

    effects = NativeTunnelEffects(
        config(),
        tmp_path,
        lambda authority: True,
        active_revalidate=lambda authority: True,
        routing_lock=tmp_path / "route.lock",
        clock=lambda: 101,
    )
    selector = TunnelController(config(), tmp_path / "tunnel-selection.json", effects)
    selector.request("failover", "backup", AUTH, 100)
    calls = []

    def run(argv):
        calls.append(tuple(argv))
        return raw_sas().replace('"name":"preferred"', '"name":"unrelated"')

    effects.run = run
    with pytest.raises(TunnelScopeError):
        effects.suspend(selector.policies["site"], AUTH)
    assert calls == [inventory_command()]


def test_static_suspension_removes_orphan_kernel_objects_after_daemon_loss(tmp_path):
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects

    effects = NativeTunnelEffects(
        config(),
        tmp_path,
        lambda authority: authority == AUTH,
        active_revalidate=lambda authority: authority == AUTH,
        routing_lock=tmp_path / "route.lock",
        clock=lambda: 101,
    )
    selector = TunnelController(config(), tmp_path / "tunnel-selection.json", effects)
    selector.request("failover", "backup", AUTH, 100)
    local, peer = "198.51.100.10", "192.0.2.1"
    states = [
        {
            "src": src,
            "dst": dst,
            "proto": "esp",
            "mode": "tunnel",
            "reqid": 1,
            "spi": hex(spi),
            "if_id": if_id,
        }
        for if_id in (100, 777)
        for src, dst, spi in ((local, peer, 1000 + if_id), (peer, local, 2000 + if_id))
    ]
    policies = [
        {
            "src": "0.0.0.0/0",
            "dst": "0.0.0.0/0",
            "dir": "out",
            "index": if_id * 8 + 1,
            "if_id": if_id,
            "tmpl": [{"src": local, "dst": peer, "proto": "esp", "mode": "tunnel", "reqid": 1}],
        }
        for if_id in (100, 777)
    ]
    unrelated = (
        [dict(x) for x in states if x["if_id"] == 777],
        [dict(x) for x in policies if x["if_id"] == 777],
    )
    unloaded = False

    def unload(cfg):
        nonlocal unloaded
        assert cfg["_ha_suspended_connections"] == ["site"]
        unloaded = True

    def run(argv):
        argv = tuple(argv)
        if argv == inventory_command():
            return Inventory.from_events([]).json()
        if argv == (*inventory_command(), "--connections"):
            return "[]"
        if argv == ("ip", "-s", "xfrm", "policy"):
            return json.dumps(policies)
        if argv[:3] == ("ip", "xfrm", "state"):
            if "delete" in argv:
                assert unloaded
                spi = argv[argv.index("spi") + 1]
                states[:] = [s for s in states if int(s["spi"], 0) != int(spi, 0)]
                return ""
            return json.dumps(states)
        if argv[:3] == ("ip", "xfrm", "policy"):
            if "delete" in argv:
                assert unloaded
                index = int(argv[argv.index("index") + 1])
                policies[:] = [p for p in policies if p["index"] != index]
                return ""
            return json.dumps(policies)
        if "link" in argv:
            return json.dumps(
                [
                    {
                        "ifname": f"xfrm{i}",
                        "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": 100 + i}},
                    }
                    for i in range(2)
                ]
            )
        if "address" in argv:
            return json.dumps([{"addr_info": [{"local": local}]}])
        raise AssertionError(argv)

    effects.run = run
    effects._swan_config = unload
    effects.suspend(selector.policies["site"], AUTH)
    assert states == unrelated[0], "VICI absence must not leave orphan SAs on the suspended path"
    assert policies == unrelated[1], "suspension must also remove orphan scoped policies"


@pytest.mark.parametrize(
    "outcome", ["expired", "still-present", "unknown", "authority-lost", "loaded", "partial-native"]
)
def test_kernel_cleanup_requires_authority_and_complete_effect_readback(tmp_path, outcome):
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects

    effects = NativeTunnelEffects(
        config(),
        tmp_path,
        lambda authority: outcome != "authority-lost",
        active_revalidate=lambda authority: True,
        routing_lock=tmp_path / "route.lock",
    )
    command = (
        "ip",
        "xfrm",
        "state",
        "delete",
        "src",
        "198.51.100.10",
        "dst",
        "192.0.2.1",
        "proto",
        "esp",
        "spi",
        "0x1234",
    )
    mutated = False
    remaining = True

    def inventory(policy):
        if mutated and outcome == "unknown":
            raise RuntimeError("incomplete kernel readback")
        return (command,) if remaining else ()

    def run(argv):
        nonlocal mutated, remaining
        if tuple(argv) == (*inventory_command(), "--connections"):
            if outcome == "loaded":
                return '["preferred"]'
            if outcome == "partial-native":
                return '{"incomplete":true}'
            return "[]"
        if tuple(argv) == inventory_command():
            return Inventory.from_events([]).json()
        assert tuple(argv) == command
        mutated = True
        if outcome == "expired":
            remaining = False
        raise RuntimeError("deletion reply unavailable")

    effects.run = run
    effects._kernel_deletions = inventory
    policy = connection_policies(config())[0]
    if outcome == "expired":
        effects._remove_orphan_kernel_objects(policy, AUTH)
        assert mutated and not remaining
    else:
        with pytest.raises(RuntimeError):
            effects._remove_orphan_kernel_objects(policy, AUTH)
        assert remaining
        assert mutated is (outcome in {"still-present", "unknown"})


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("admitted", [False, True])
def test_activation_kernel_cleanup_uses_exact_effective_file_and_fenced_authority(
    tmp_path, monkeypatch, candidate, admitted
):
    from nebius_vpngw.agent import strongswan_renderer
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import (
        NativeTunnelEffects,
        reconcile_inactive_static,
    )

    cfg = config()
    cfg["_ha_tunnel_selections"] = {"site": "backup"}
    effective = cfg if candidate else {**cfg, "_ha_static_passive": True}
    path = tmp_path / "swanctl.conf"
    monkeypatch.setattr(strongswan_renderer, "SWANCTL_CONF", path)
    files = {}
    strongswan_renderer.StrongSwanRenderer().render_and_apply(effective, rendered_files=files)
    path.write_text(files[path])
    path.chmod(0o600)
    removed = []

    def cleanup(self, policy, authority):
        assert self.revalidate(authority)
        removed.extend(p.name for p in policy.paths)

    monkeypatch.setattr(NativeTunnelEffects, "_remove_orphan_kernel_objects", cleanup)
    args = dict(candidate=candidate, permitted=lambda deadline: admitted, runner=lambda *a: None)
    if not admitted:
        with pytest.raises(RuntimeError, match="authority is unproven"):
            reconcile_inactive_static(cfg, tmp_path, AUTH, **args)
        assert removed == []
        return
    reconcile_inactive_static(cfg, tmp_path, AUTH, **args)
    assert removed == (["preferred"] if candidate else ["preferred", "backup"])
    removed.clear()
    path.write_text("changed loader authority")
    with pytest.raises(RuntimeError, match="loader policy is unproven"):
        reconcile_inactive_static(cfg, tmp_path, AUTH, **args)
    assert not removed


@pytest.mark.parametrize("after_failure", ["absent", "present", "unavailable", "shared"])
def test_failed_termination_requires_fresh_proof_of_scoped_sa_absence(tmp_path, after_failure):
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects

    effects = NativeTunnelEffects(
        config(),
        tmp_path,
        lambda authority: True,
        active_revalidate=lambda authority: True,
        routing_lock=tmp_path / "route.lock",
        clock=lambda: 101,
    )
    selector = TunnelController(config(), tmp_path / "tunnel-selection.json", effects)
    selector.request("failover", "backup", AUTH, 100)
    terminated = []

    def run(argv):
        if tuple(argv) in {
            ("ip", "xfrm", "state", "list", "nokeys"),
            ("ip", "-s", "xfrm", "policy"),
        }:
            return "[]"
        if tuple(argv) == (*inventory_command(), "--connections"):
            return "[]"
        if argv[1] == "--terminate":
            terminated.append(tuple(argv))
            raise RuntimeError("termination command failed")
        assert tuple(argv) == inventory_command()
        if not terminated or after_failure == "present":
            return raw_sas()
        if after_failure == "unavailable":
            raise RuntimeError("observation unavailable")
        if after_failure == "shared":
            return raw_sas().replace('"name":"preferred"', '"name":"unrelated"')
        return Inventory.from_events([]).json()

    effects.run = run
    effects._swan_config = lambda cfg: None
    if after_failure == "absent":
        effects.suspend(selector.policies["site"], AUTH)
    else:
        with pytest.raises(RuntimeError):
            effects.suspend(selector.policies["site"], AUTH)
    assert len(terminated) == 1


def test_exhausted_episode_allows_restored_health_without_renewing_repair_budget(tmp_path):
    effects = Effects()
    effects.health = {"preferred": Health.DOWN, "backup": Health.DOWN}
    selector = TunnelController(config(), tmp_path / "state", effects)
    selector.tick(AUTH, 100)
    selector.tick(AUTH, 130)
    assert selector.state["episode"]["exhausted"]
    fences = effects.calls.count(("fence",))
    effects.health["preferred"] = Health.HEALTHY
    selector.tick(AUTH, 130)
    assert effects.calls.count(("fence",)) == fences
    assert selector.state["episode"]["deadline"] == 130

    effects.health["preferred"] = Health.DOWN
    selector.tick(AUTH, 131)
    assert effects.calls.count(("fence",)) == fences == 0
    assert selector.state["episode"]["deadline"] == 130


def test_exhausted_restore_survives_restart_until_authority_is_verified(tmp_path):
    effects = Effects()
    effects.health = {"preferred": Health.DOWN, "backup": Health.DOWN}
    path = tmp_path / "state"
    selector = TunnelController(config(), path, effects)
    selector.tick(AUTH, 100)
    selector.tick(AUTH, 130)
    assert selector.state["operation"]["phase"] == "verify-restore"
    effects.fail_activate = True  # independent readback is indeterminate
    selector = TunnelController(config(), path, effects)
    selector.tick(AUTH, 140)
    assert selector.state["operation"]["phase"] == "verify-restore"
    effects.fail_activate = False
    selector.tick(AUTH, 141)
    assert selector.state["operation"]["result"] == "native-recovery-retained"
    assert selector.state["episode"]["deadline"] == 130
    assert effects.calls.count(("activate", "preferred")) == 1


def request_config():
    cfg = config()
    cfg["vm_ha"].update(
        node={"node_id": AUTH.owner},
        runtime_binding={"shared_allocation_id": AUTH.allocation},
        generation={"generation_id": AUTH.generation},
    )
    return cfg


def request_payload():
    from dataclasses import asdict

    return {"authority": asdict(AUTH), "verb": "failover", "target": "backup"}


def test_mailbox_request_is_idempotent_and_has_no_effect_before_controller_admission(tmp_path):
    from nebius_vpngw.agent.vm_ha.tunnel_requests import read_request, submit_request, tunnel_status
    from nebius_vpngw.agent.vm_ha.tunnels import require_tunnel_quiescent

    cfg = request_config()
    first = submit_request(cfg, tmp_path, request_payload(), now=100)
    assert submit_request(cfg, tmp_path, request_payload(), now=101) == first
    assert read_state(tmp_path / "tunnel-selection.json")["operation"] is None
    assert read_request(tmp_path)["expires"] == 130
    assert tunnel_status(tmp_path)["pending_request"]["id"] == first["operation_id"]
    with pytest.raises(RuntimeError, match="request is outstanding"):
        require_tunnel_quiescent(tmp_path)


def test_mailbox_waits_for_in_progress_controller_writer_before_admission(tmp_path, monkeypatch):
    import fcntl
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from nebius_vpngw.agent import vm_ha_rearm
    from nebius_vpngw.agent.vm_ha.tunnel_requests import read_request, submit_request

    descriptor = os.open(tmp_path / "rearm.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    attempted = threading.Event()
    acquire = vm_ha_rearm._acquire_rearm_lock

    def observed_acquire(*args, **kwargs):
        attempted.set()
        return acquire(*args, **kwargs)

    monkeypatch.setattr(vm_ha_rearm, "_acquire_rearm_lock", observed_acquire)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(submit_request, request_config(), tmp_path, request_payload(), now=100)
        try:
            assert attempted.wait(1)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.05)
            assert read_request(tmp_path) is None
        finally:
            os.close(descriptor)
        result = future.result(timeout=1)
    assert result["result"] == "outstanding"
    assert read_request(tmp_path)["id"] == result["operation_id"]
    assert read_state(tmp_path / "tunnel-selection.json")["operation"] is None


def test_running_controller_admits_mailbox_and_replays_crash_after_operation_checkpoint(tmp_path):
    from nebius_vpngw.agent.vm_ha.tunnel_requests import read_request, submit_request, write_request
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime

    cfg = request_config()
    first = submit_request(cfg, tmp_path, request_payload(), now=100)
    queued = read_request(tmp_path)
    effects = Effects()
    selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", effects)
    runtime = object.__new__(ControllerTunnelRuntime)
    runtime.state_dir = tmp_path
    runtime.clock = lambda: 101
    runtime._consume_request(selector, None)
    assert read_request(tmp_path) == queued
    assert effects.calls == []
    runtime._consume_request(selector, AUTH)
    assert read_request(tmp_path) is None
    assert selector.state["operation"]["id"] == first["operation_id"]
    assert effects.calls == []
    write_request(tmp_path, queued)  # Crash after operation save, before mailbox removal.
    selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", effects)
    runtime._consume_request(selector, AUTH)
    assert read_request(tmp_path) is None
    assert selector.state["sequence"] == 1
    for now in range(101, 105):
        selector.tick(AUTH, now)
    assert selector.state["operation"]["phase"] == "succeeded"
    assert (
        submit_request(cfg, tmp_path, request_payload(), now=106)["operation_id"]
        == first["operation_id"]
    )


@pytest.mark.parametrize("changed", ["expired", "authority"])
def test_mailbox_rejects_stale_requests_without_effects(tmp_path, changed):
    from nebius_vpngw.agent.vm_ha.tunnel_requests import read_request, submit_request
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime

    cfg = request_config()
    submit_request(cfg, tmp_path, request_payload(), now=100)
    effects = Effects()
    selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", effects)
    runtime = object.__new__(ControllerTunnelRuntime)
    runtime.state_dir = tmp_path
    runtime.clock = lambda: 131 if changed == "expired" else 101
    runtime._consume_request(
        selector, None if changed == "expired" else replace(AUTH, incarnation=2)
    )
    assert read_request(tmp_path)["result"] == (
        "request-expired" if changed == "expired" else "authority-changed"
    )
    assert effects.calls == []
    assert selector.state["operation"] is None


def test_tunnel_composition_accepts_real_frozen_runtime_ports_without_starting_watchdog(tmp_path):
    from dataclasses import fields
    from types import SimpleNamespace

    import yaml

    from nebius_vpngw.agent.vm_ha.runtime import VMHARuntimePorts
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime

    args = {field.name: None for field in fields(VMHARuntimePorts)}
    args.update(
        routes=SimpleNamespace(),
        data_plane=SimpleNamespace(),
        tunnel_recovery_provider=lambda: None,
    )
    ports = VMHARuntimePorts(**args)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(request_config()))
    runtime = ControllerTunnelRuntime(path, tmp_path, None, ports)
    assert not hasattr(runtime, "watchdog")
    bound = replace(ports, tunnel_recovery_provider=runtime.recovery_claim)
    assert bound.tunnel_recovery_provider() is None
    assert ports.routes.static_readiness_provider == runtime.static_ready


def test_completed_unnamed_retry_does_not_toggle_path(tmp_path):
    from nebius_vpngw.agent.vm_ha.tunnel_requests import read_request, submit_request
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime

    cfg = request_config()
    payload = request_payload()
    payload["target"] = None
    first = submit_request(cfg, tmp_path, payload, now=100)
    selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", Effects())
    runtime = object.__new__(ControllerTunnelRuntime)
    runtime.state_dir = tmp_path
    runtime.clock = lambda: 101
    runtime._consume_request(selector, AUTH)
    for now in range(101, 105):
        selector.tick(AUTH, now)
    assert selector.state["selected"] == {"site": "backup"}
    assert submit_request(cfg, tmp_path, payload, now=106)["operation_id"] == first["operation_id"]
    assert read_request(tmp_path) is None


def test_status_cannot_miss_mailbox_and_admitted_operation(tmp_path, monkeypatch):
    from nebius_vpngw.agent.vm_ha import tunnel_requests

    cfg = request_config()
    first = tunnel_requests.submit_request(cfg, tmp_path, request_payload(), now=100)

    def admit_then_read(state_dir):
        selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", Effects())
        selector.request("failover", "backup", AUTH, 101, operation_id=first["operation_id"])
        (state_dir / tunnel_requests.REQUEST_NAME).unlink()
        return None

    monkeypatch.setattr(tunnel_requests, "read_request", admit_then_read)
    status = tunnel_requests.tunnel_status(tmp_path)
    assert status["operation"]["id"] == first["operation_id"]


@pytest.mark.parametrize(
    "phase,pending",
    [
        ("suspend", True),
        ("activate", True),
        ("verify", True),
        ("restore", True),
        ("verify-restore", True),
        ("succeeded", False),
        ("failed", False),
    ],
)
def test_reload_admission_checks_exact_persisted_tunnel_authority(tmp_path, phase, pending):
    import json

    from nebius_vpngw.agent.vm_ha.tunnel_runtime import tunnel_operation_pending
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
    from nebius_vpngw.agent.vm_ha_controller import ControllerCheckpoint

    cfg = config()
    cfg["vm_ha"].update(
        node={"node_id": AUTH.owner},
        runtime_binding={"shared_allocation_id": AUTH.allocation},
        generation={"generation_id": AUTH.generation},
    )
    selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", Effects())
    selector.request("failover", "backup", AUTH, 100)
    selector.state["operation"]["phase"] = phase
    selector._save()
    checkpoint = tmp_path / "controller-checkpoint.json"
    checkpoint.write_text(
        json.dumps(controller_checkpoint_to_dict(ControllerCheckpoint(ownership_incarnation=1)))
    )
    assert tunnel_operation_pending(cfg, tmp_path) is pending
    checkpoint.write_text(
        json.dumps(controller_checkpoint_to_dict(ControllerCheckpoint(ownership_incarnation=2)))
    )
    assert not tunnel_operation_pending(cfg, tmp_path)


def test_normal_bgp_health_follows_surviving_routes_instead_of_preferred_name(tmp_path):
    effects, _, _ = native_bgp_effects(tmp_path)
    # The configured preferred path is down; FRR selected the healthy backup.
    tunnels = effects.cfg["connections"][0]["tunnels"]
    tunnels[0]["ha_role"], tunnels[1]["ha_role"] = "passive", "active"
    selector = TunnelController(effects.cfg, tmp_path / "selector.json", effects)
    selector.tick(AUTH, 100)
    assert selector.state["health"] == {"site": "healthy"}
    assert selector.state["selected"] == {}
    assert selector.state["operation"] is None


@pytest.mark.parametrize(
    "case",
    [
        "pending",
        "completed",
        "stale-owner",
        "invalidated",
        "generation",
        "ineligible",
        "foreign-target",
        "foreign-previous",
    ],
)
def test_serving_export_alternatives_are_current_and_connection_scoped(tmp_path, case):
    import copy
    import json
    from types import SimpleNamespace

    import yaml

    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
    from nebius_vpngw.agent.vm_ha_controller import ControllerCheckpoint

    cfg = config("bgp")
    cfg["gateway"]["local_prefixes"] = ["10.0.0.0/24"]
    cfg["vm_ha"].update(
        node={"node_id": AUTH.owner},
        runtime_binding={"shared_allocation_id": AUTH.allocation},
        generation={"generation_id": AUTH.generation},
    )
    other = copy.deepcopy(cfg["connections"][0])
    other["name"] = "other"
    other["remote_prefixes"] = ["10.2.0.0/16"]
    for index, tunnel in enumerate(other["tunnels"], 3):
        tunnel["name"] = "other-" + tunnel["name"]
        tunnel["inner_remote_ip"] = f"169.254.{index}.2"
    cfg["connections"].append(other)
    effects = Effects()
    effects.health.update({"other-preferred": Health.HEALTHY, "other-backup": Health.HEALTHY})
    selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", effects)
    selector.request("failover", "backup", AUTH, 100)
    selector.tick(AUTH, 101)
    selector.state["selected"]["other"] = "other-backup"
    if case == "completed":
        selector.state["operation"]["phase"] = "succeeded"
    if case == "foreign-target":
        selector.state["operation"]["target"] = "other-backup"
        selector.state["operation"]["verb"] = "release"
    if case == "foreign-previous":
        selector.state["operation"]["previous"] = "other-backup"
    selector._save()
    if case == "generation":
        cfg["vm_ha"]["generation"]["generation_id"] = "b" * 64
    if case == "ineligible":
        cfg["connections"][0]["remote_prefixes"] = []
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    (tmp_path / "controller-checkpoint.json").write_text(
        json.dumps(
            controller_checkpoint_to_dict(
                ControllerCheckpoint(
                    ownership_incarnation=2 if case == "stale-owner" else 1,
                    ownership_continuity_invalidated=case == "invalidated",
                )
            )
        )
    )
    runtime = ControllerTunnelRuntime(
        path,
        tmp_path,
        None,
        SimpleNamespace(routes=SimpleNamespace(), data_plane=SimpleNamespace()),
    )
    alternatives = runtime.serving_export_alternatives()
    if case != "pending":
        assert alternatives == ()
        return
    assert len(alternatives) == 2
    for exports in alternatives:
        assert exports["169.254.3.2"] == frozenset()
        assert exports["169.254.4.2"] == frozenset({"10.0.0.0/24"})
        assert exports["169.254.1.2"] == frozenset({"10.0.0.0/24"})
    assert alternatives[0]["169.254.2.2"] == frozenset({"10.0.0.0/24"})
    assert alternatives[1]["169.254.2.2"] == frozenset()


@pytest.mark.parametrize("trigger", ["deadline", "failed-verification"])
def test_release_retains_installed_requested_policy_until_native_restore(tmp_path, trigger):
    import json
    from types import SimpleNamespace

    import yaml

    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
    from nebius_vpngw.agent.vm_ha_controller import ControllerCheckpoint

    cfg = config("bgp")
    cfg["gateway"]["local_prefixes"] = ["10.0.0.0/24"]
    cfg["vm_ha"].update(
        node={"node_id": AUTH.owner},
        runtime_binding={"shared_allocation_id": AUTH.allocation},
        generation={"generation_id": AUTH.generation},
    )
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    (tmp_path / "controller-checkpoint.json").write_text(
        json.dumps(controller_checkpoint_to_dict(ControllerCheckpoint(ownership_incarnation=1)))
    )
    runtime = ControllerTunnelRuntime(
        path,
        tmp_path,
        None,
        SimpleNamespace(routes=SimpleNamespace(), data_plane=SimpleNamespace()),
    )

    class ServingEffects(Effects):
        installed = None
        release_observed = False

        def activate(self, policy, target, authority):
            if target is None and self.installed == "backup":
                state = read_state(tmp_path / "tunnel-selection.json")
                assert state["operation"]["verb"] == "release" and not state["selected"]
                actual = {
                    "169.254.1.2": frozenset(),
                    "169.254.2.2": frozenset({"10.0.0.0/24"}),
                }
                assert actual != runtime.current_exports()
                assert actual in runtime.serving_export_alternatives()
                self.release_observed = True
            self.installed = target

    effects = ServingEffects()
    selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", effects)
    selector.request("failover", "backup", AUTH, 100)
    selector.tick(AUTH, 101)
    assert effects.installed == "backup"
    if trigger == "failed-verification":
        effects.health["backup"] = Health.DOWN
        selector.tick(AUTH, 111)
        assert selector.state["operation"]["verb"] == "release"
        selector.tick(AUTH, 112)
    else:
        selector.tick(AUTH, selector.state["operation"]["deadline"] + 1)
    assert effects.release_observed
