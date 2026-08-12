from __future__ import annotations

from copy import deepcopy

import pytest

from nebius_vpngw.schema import validate_config


def _base_bgp_config(sample_config: dict) -> dict:
    cfg = deepcopy(sample_config)
    cfg["defaults"]["routing"]["mode"] = "bgp"
    cfg["connections"][0]["name"] = "site-1"
    cfg["connections"][0]["vendor"] = "gcp"
    cfg["connections"][0]["routing_mode"] = "bgp"
    cfg["connections"][0]["bgp"] = {
        "enabled": True,
        "remote_asn": 65014,
        "advertise_local_prefixes": True,
    }
    cfg["connections"][0]["tunnels"] = [
        {
            "name": "site-1-tunnel-1",
            "gateway_instance_index": 0,
            "local_public_ip_index": 0,
            "ha_role": "active",
            "remote_public_ip": "198.51.100.10",
            "psk": "site-1-test-psk-0001",
            "inner_cidr": "169.254.10.0/30",
            "inner_local_ip": "169.254.10.1",
            "inner_remote_ip": "169.254.10.2",
        }
    ]
    return cfg


def _second_connection() -> dict:
    return {
        "name": "site-2",
        "vendor": "gcp",
        "routing_mode": "bgp",
        "bgp": {
            "enabled": True,
            "remote_asn": 65024,
            "advertise_local_prefixes": True,
        },
        "tunnels": [
            {
                "name": "site-2-tunnel-1",
                "gateway_instance_index": 0,
                "local_public_ip_index": 0,
                "ha_role": "active",
                "remote_public_ip": "198.51.100.20",
                "psk": "site-2-test-psk-0001",
                "inner_cidr": "169.254.20.0/30",
                "inner_local_ip": "169.254.20.1",
                "inner_remote_ip": "169.254.20.2",
            }
        ],
    }


def _duplicate_site_1_apipa(second_connection: dict) -> None:
    second_connection["tunnels"][0]["inner_cidr"] = "169.254.10.0/30"
    second_connection["tunnels"][0]["inner_local_ip"] = "169.254.10.1"
    second_connection["tunnels"][0]["inner_remote_ip"] = "169.254.10.2"


def _enable_vm_ha(cfg: dict) -> None:
    cfg["gateway_group"]["instance_count"] = 2
    cfg["gateway_group"]["external_ips"] = [["203.0.113.10"], ["203.0.113.20"]]
    cfg["gateway_group"]["vm_ha"] = {
        "enabled": True,
        "cluster_id": "gateway-cluster",
        "members": [
            {"node_id": "gateway-a", "instance_index": 0, "role": "active"},
            {"node_id": "gateway-b", "instance_index": 1, "role": "passive"},
        ],
    }
    second_tunnel = deepcopy(cfg["connections"][0]["tunnels"][0])
    second_tunnel.update(
        {
            "name": "site-1-tunnel-node-b",
            "gateway_instance_index": 1,
            "remote_public_ip": "198.51.100.11",
            "inner_cidr": "169.254.11.0/30",
            "inner_local_ip": "169.254.11.1",
            "inner_remote_ip": "169.254.11.2",
        }
    )
    cfg["connections"][0]["tunnels"].append(second_tunnel)


def test_schema_accepts_explicit_vm_ha_with_roles_distinct_from_tunnel_roles(
    sample_config: dict,
) -> None:
    cfg = _base_bgp_config(sample_config)
    _enable_vm_ha(cfg)

    validated = validate_config(cfg)

    assert validated.gateway_group.vm_ha is not None
    assert [member.role.value for member in validated.gateway_group.vm_ha.members] == [
        "active",
        "passive",
    ]
    assert [tunnel.ha_role.value for tunnel in validated.connections[0].tunnels] == [
        "active",
        "active",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda cfg: cfg["gateway_group"].update(instance_count=3), "instance_count=2"),
        (
            lambda cfg: cfg["gateway_group"]["vm_ha"]["members"].pop(),
            "exactly two nodes",
        ),
        (
            lambda cfg: cfg["gateway_group"]["vm_ha"]["members"][1].update(role="active"),
            "exactly one active and one passive",
        ),
    ],
)
def test_schema_rejects_invalid_vm_ha_topologies(
    sample_config: dict,
    mutation,
    message: str,
) -> None:
    cfg = _base_bgp_config(sample_config)
    _enable_vm_ha(cfg)
    mutation(cfg)

    with pytest.raises(ValueError, match=message):
        validate_config(cfg)


def test_schema_rejects_vm_ha_connection_missing_one_member(sample_config: dict) -> None:
    cfg = _base_bgp_config(sample_config)
    _enable_vm_ha(cfg)
    cfg["connections"][0]["tunnels"] = cfg["connections"][0]["tunnels"][:1]

    with pytest.raises(ValueError, match="must define tunnels for both VM-HA members"):
        validate_config(cfg)


def test_schema_accepts_multiple_connections_with_unique_tunnel_identity(
    sample_config: dict,
) -> None:
    cfg = _base_bgp_config(sample_config)
    cfg["connections"].append(_second_connection())

    validated = validate_config(cfg)

    assert [conn.name for conn in validated.connections] == ["site-1", "site-2"]


def test_schema_rejects_duplicate_tunnel_names_across_connections(sample_config: dict) -> None:
    cfg = _base_bgp_config(sample_config)
    second = _second_connection()
    second["tunnels"][0]["name"] = "site-1-tunnel-1"
    cfg["connections"].append(second)

    with pytest.raises(ValueError, match="Tunnel names must be globally unique"):
        validate_config(cfg)


def test_schema_rejects_duplicate_inner_cidr_on_same_instance(sample_config: dict) -> None:
    cfg = _base_bgp_config(sample_config)
    second = _second_connection()
    _duplicate_site_1_apipa(second)
    cfg["connections"].append(second)

    with pytest.raises(ValueError, match="inner_cidr must be unique per gateway instance"):
        validate_config(cfg)


def test_schema_rejects_duplicate_inner_local_ip_on_same_instance(sample_config: dict) -> None:
    cfg = _base_bgp_config(sample_config)
    second = _second_connection()
    _duplicate_site_1_apipa(second)
    cfg["connections"].append(second)

    with pytest.raises(ValueError, match="inner_local_ip must be unique per gateway instance"):
        validate_config(cfg)


def test_schema_rejects_duplicate_inner_remote_ip_on_same_instance(sample_config: dict) -> None:
    cfg = _base_bgp_config(sample_config)
    second = _second_connection()
    _duplicate_site_1_apipa(second)
    cfg["connections"].append(second)

    with pytest.raises(ValueError, match="inner_remote_ip must be unique per gateway instance"):
        validate_config(cfg)


def test_schema_allows_same_apipa_values_on_different_instances(sample_config: dict) -> None:
    cfg = _base_bgp_config(sample_config)
    cfg["gateway_group"]["instance_count"] = 2
    cfg["gateway_group"]["external_ips"] = [["203.0.113.10"], ["203.0.113.20"]]

    second = _second_connection()
    second["tunnels"][0]["gateway_instance_index"] = 1
    second["tunnels"][0]["inner_cidr"] = "169.254.10.0/30"
    second["tunnels"][0]["inner_local_ip"] = "169.254.10.1"
    second["tunnels"][0]["inner_remote_ip"] = "169.254.10.2"
    cfg["connections"].append(second)

    validated = validate_config(cfg)

    assert [t.gateway_instance_index for t in validated.connections[1].tunnels] == [1]


def test_schema_rejects_duplicate_external_ips_across_instances(sample_config: dict) -> None:
    cfg = _base_bgp_config(sample_config)
    cfg["gateway_group"]["instance_count"] = 2
    cfg["gateway_group"]["external_ips"] = [["203.0.113.10"], ["203.0.113.10"]]

    with pytest.raises(ValueError, match="external_ips entries must be globally unique"):
        validate_config(cfg)
