from copy import deepcopy

import pytest
import yaml

from nebius_vpngw.config_loader import load_local_config
from nebius_vpngw.deploy.ordinary_route_policy import freeze


@pytest.fixture
def local():
    return {
        "gateway": {"local_prefixes": ["10.0.0.0/24"]},
        "defaults": {"routing": {"mode": "bgp", "bgp": {"max_prefixes": 3}}},
        "connections": [
            {
                "name": "peer",
                "remote_prefixes": ["192.168.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 0, "inner_remote_ip": "169.254.1.2"},
                    {
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.2.2",
                        "ha_role": "passive",
                    },
                ],
            }
        ],
    }


def policy(local):
    return freeze(local, network_prefixes=("10.0.0.0/16",))


@pytest.mark.parametrize("mode", ["static", "bgp"])
@pytest.mark.parametrize("override", [False, True])
def test_route_policy_accepts_validated_yaml_modes(sample_config, tmp_path, mode, override):
    sample_config["defaults"]["routing"]["mode"] = mode
    connection = sample_config["connections"][0]
    connection["routing_mode"] = mode
    if override:
        sample_config["defaults"]["routing"]["mode"] = "bgp" if mode == "static" else "static"
    connection["bgp"].update(enabled=mode == "bgp", remote_asn=65020)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(sample_config))
    loaded = load_local_config(path)
    frozen = policy(loaded)
    assert frozen.connections[0].mode == mode
    assert frozen.dynamic is (mode == "bgp")
    learned = {connection["name"]: ("203.0.113.0/24",)} if mode == "bgp" else {}
    assert frozen.select(learned) == {"203.0.113.0/24": 0}


def test_bgp_policy_freezes_exact_peers_limit_and_destination_filter(local):
    frozen = policy(local)
    assert frozen.connections[0].peers == ("169.254.1.2", "169.254.2.2")
    assert frozen.connections[0].max_prefixes == 3
    assert frozen.select({"peer": ("192.168.2.0/24", "172.16.0.0/24")}) == {"192.168.2.0/24": 0}
    local["connections"][0]["remote_prefixes"] = []
    assert frozen.connections[0].prefixes == ("192.168.0.0/16",)


def test_no_whitelist_preserves_nonlocal_peer_routes_and_canonical_vpc_exclusions(local):
    local["connections"][0]["remote_prefixes"] = []
    frozen = policy(local)
    assert frozen.select({"peer": ("172.16.0.0/24", "10.0.2.0/24", "0.0.0.0/0")}) == {
        "172.16.0.0/24": 0
    }


def test_withdrawal_never_retires_admitted_routes_but_intent_retirement_does(local):
    frozen = policy(local)
    assert frozen.select({"peer": ()}) == {}
    assert frozen.retains("192.168.2.0/24", 0)
    assert not frozen.retains("192.168.2.0/24", 1)
    local["connections"][0]["remote_prefixes"] = ["192.168.3.0/24"]
    assert not policy(local).retains("192.168.2.0/24", 0)
    local["connections"] = []
    assert not policy(local).retains("192.168.2.0/24", 0)


@pytest.mark.parametrize("learned", [{}, {"other": ()}, {"peer": (), "other": ()}, {"peer": None}])
def test_incomplete_observation_never_means_empty_routes(local, learned):
    with pytest.raises((RuntimeError, ValueError), match="observation|evidence"):
        policy(local).select(learned)


def test_prefix_limit_is_approved_and_enforced_before_mutation(local):
    with pytest.raises(RuntimeError, match="limit"):
        policy(local).select({"peer": tuple(f"192.168.{i}.0/24" for i in range(4))})


def test_ambiguous_ownership_is_not_arbitrarily_selected(local):
    second = deepcopy(local["connections"][0])
    second["name"] = "second"
    for tunnel in second["tunnels"]:
        tunnel["gateway_instance_index"] = 1
    local["connections"].append(second)
    with pytest.raises(RuntimeError, match="multiple"):
        policy(local).select({"peer": ("192.168.1.0/24",), "second": ("192.168.1.0/24",)})


def test_static_policy_uses_exact_configuration_and_disabled_tunnels(local):
    local["connections"][0]["routing_mode"] = "static"
    local["connections"][0]["remote_prefixes"] = ["192.168.1.0/24"]
    frozen = policy(local)
    assert not frozen.dynamic
    assert frozen.select({}) == {"192.168.1.0/24": 0}
    assert not frozen.retains("192.168.1.0/25", 0)
    for tunnel in local["connections"][0]["tunnels"]:
        tunnel["ha_role"] = "disable"
    assert policy(local).select({}) == {}


@pytest.mark.parametrize(
    "change", ["no-peers", "multiple-owners", "duplicate-name", "limit", "host-prefix"]
)
def test_invalid_policy_fails_closed(local, change):
    if change == "no-peers":
        for tunnel in local["connections"][0]["tunnels"]:
            tunnel.pop("inner_remote_ip")
    elif change == "multiple-owners":
        local["connections"][0]["tunnels"][1]["gateway_instance_index"] = 1
    elif change == "duplicate-name":
        local["connections"].append(deepcopy(local["connections"][0]))
    elif change == "limit":
        local["defaults"]["routing"]["bgp"]["max_prefixes"] = 0
    else:
        local["connections"][0]["remote_prefixes"] = ["192.168.1.1/24"]
    with pytest.raises(ValueError):
        policy(local)


def test_summarization_admits_only_exact_unions_and_retires_changed_static_intent():
    from nebius_vpngw.deploy.ordinary_route_policy import Connection, Policy

    policy = Policy(
        (Connection("a", "static", 0, (), ("192.168.1.0/25", "192.168.1.128/25"), 2),),
        ("10.0.0.0/8",),
        summarize=True,
    )
    assert policy.select({}) == {"192.168.1.0/24": 0}
    assert policy.retains("192.168.1.0/24", 0)
    assert not policy.retains("192.168.0.0/23", 0)
    changed = Policy(
        (Connection("a", "static", 0, (), ("192.168.1.0/25",), 1),), ("10.0.0.0/8",), summarize=True
    )
    assert not changed.retains("192.168.1.0/24", 0)
    assert changed.select({}) == {"192.168.1.0/25": 0}


def test_summarization_never_combines_different_gateway_owners():
    from nebius_vpngw.deploy.ordinary_route_policy import Connection, Policy

    policy = Policy(
        (
            Connection("a", "static", 0, (), ("192.168.1.0/25",), 1),
            Connection("b", "static", 1, (), ("192.168.1.128/25",), 1),
        ),
        ("10.0.0.0/8",),
        summarize=True,
    )
    assert policy.select({}) == {"192.168.1.0/25": 0, "192.168.1.128/25": 1}
