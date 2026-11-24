"""The opt-in contract must not broaden routing for other workload sources."""

from copy import deepcopy

import pytest

from nebius_vpngw.enforced_routing import compile_policies, resolve_sources, route_table
from nebius_vpngw.schema import VPNGatewayConfig


def enabled(sample_config, *, destinations=None):
    cfg = deepcopy(sample_config)
    cfg["connections"][0]["enforced_routing"] = {
        "source_subnets": ["10.0.1.0/24", "10.0.2.0/24"],
        "destinations": destinations or ["172.20.0.0/16", "172.21.0.0/16"],
    }
    return cfg


def test_static_enforcement_supplies_route_intent(sample_config):
    cfg = enabled(sample_config, destinations=["0.0.0.0/0"])
    cfg["connections"][0].pop("remote_prefixes")
    model = VPNGatewayConfig.model_validate(cfg)
    assert model.connections[0].enforced_routing.destinations == ["0.0.0.0/0"]


def test_absent_policy_stays_absent_in_serialized_config(sample_config):
    model = VPNGatewayConfig.model_validate(sample_config)
    assert "enforced_routing" not in model.model_dump(mode="json")["connections"][0]


@pytest.mark.parametrize("field", ["source_subnets", "destinations"])
@pytest.mark.parametrize("value", [[], ["::/0"], ["invalid"], ["10.0.1.1/24"]])
def test_policy_requires_explicit_canonical_ipv4_lists(sample_config, field, value):
    cfg = enabled(sample_config)
    cfg["connections"][0]["enforced_routing"][field] = value
    with pytest.raises(ValueError):
        VPNGatewayConfig.model_validate(cfg)


def test_source_must_be_covered_by_local_prefixes(sample_config):
    cfg = enabled(sample_config)
    cfg["connections"][0]["enforced_routing"]["source_subnets"] = ["10.1.0.0/24"]
    with pytest.raises(ValueError, match="local_prefixes"):
        VPNGatewayConfig.model_validate(cfg)


def test_explicit_local_destination_is_rejected_but_default_is_valid(sample_config):
    with pytest.raises(ValueError, match="local"):
        VPNGatewayConfig.model_validate(enabled(sample_config, destinations=["10.0.0.0/16"]))
    VPNGatewayConfig.model_validate(enabled(sample_config, destinations=["0.0.0.0/0"]))


def test_bgp_enforcement_does_not_widen_import_filter(sample_config):
    cfg = enabled(sample_config, destinations=["0.0.0.0/0"])
    connection = cfg["connections"][0]
    connection.update(routing_mode="bgp", bgp={"enabled": True, "remote_asn": 65020})
    with pytest.raises(ValueError, match="import"):
        VPNGatewayConfig.model_validate(cfg)
    connection["remote_prefixes"] = ["0.0.0.0/0"]
    VPNGatewayConfig.model_validate(cfg)


def test_exact_source_resolution_and_stable_table(sample_config):
    cfg = enabled(sample_config)
    policies = compile_policies(cfg)
    subnets = {"selected": ("10.0.1.0/24", "10.0.2.0/24"), "other": ("10.0.3.0/24",)}
    assert resolve_sources(policies, subnets, gateway_subnet_id="gateway") == {
        "static-peer": ("selected",)
    }
    conn = cfg["connections"][0]
    assert route_table(conn, "172.20.0.0/16") == policies[0].table
    assert route_table(conn, "203.0.113.0/24") == 254
    for bad in (
        {"selected": ("10.0.0.0/16",)},
        {"selected": ("10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24")},
        {**subnets, "ambiguous": ("10.0.1.0/24",)},
        {"gateway": ("10.0.1.0/24", "10.0.2.0/24")},
    ):
        with pytest.raises(ValueError, match="whole workload"):
            resolve_sources(policies, bad, gateway_subnet_id="gateway")
