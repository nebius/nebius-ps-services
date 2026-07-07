from __future__ import annotations

import ipaddress
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from nebius_vpngw.config_loader import GatewayGroupSpec
from nebius_vpngw.deploy.vm_manager import VMManager
from nebius_vpngw.schema import validate_config


def _gateway_group_spec(subnet: dict | None = None) -> GatewayGroupSpec:
    return GatewayGroupSpec(
        name="nebius-vpn-gw",
        instance_count=1,
        region="eu-west1",
        external_ips=[[]],
        vm_spec={"num_nics": 1},
        subnet=subnet or {},
    )


def test_validate_config_accepts_explicit_gateway_subnet_cidr(sample_config: dict) -> None:
    sample_config["gateway_group"]["subnet"] = {
        "name": "edge-gateway-subnet",
        "cidr": "172.16.30.7/24",
        "prefix_length": 28,
    }

    validated = validate_config(sample_config)

    assert validated.gateway_group.subnet.name == "edge-gateway-subnet"
    assert validated.gateway_group.subnet.cidr == "172.16.30.0/24"
    assert validated.gateway_group.subnet.prefix_length == 28


def test_validate_config_accepts_gateway_group_network_id(sample_config: dict) -> None:
    sample_config["gateway_group"]["network_id"] = "vpcnetwork-abc123def456"

    validated = validate_config(sample_config)

    assert validated.gateway_group.network_id == "vpcnetwork-abc123def456"


def test_validate_config_rejects_legacy_vm_spec_network_id(sample_config: dict) -> None:
    sample_config["gateway_group"]["vm_spec"]["network_id"] = "vpcnetwork-abc123def456"

    with pytest.raises(ValidationError):
        validate_config(sample_config)


def test_validate_config_rejects_non_private_or_too_small_gateway_subnet(
    sample_config: dict,
) -> None:
    sample_config["gateway_group"]["subnet"] = {"cidr": "192.0.2.0/29"}

    with pytest.raises(ValidationError):
        validate_config(sample_config)


def test_find_first_free_subnet_cidr_uses_next_pool_when_needed() -> None:
    result = VMManager._find_first_free_subnet_cidr(
        [
            ipaddress.IPv4Network("10.0.0.0/24"),
            ipaddress.IPv4Network("172.16.30.0/24"),
        ],
        [ipaddress.IPv4Network("10.0.0.0/24")],
        prefix_length=24,
    )

    assert result == "172.16.30.0/24"


def test_gateway_subnet_settings_apply_defaults() -> None:
    spec = _gateway_group_spec()

    assert VMManager._gateway_subnet_settings(spec) == {
        "name": "vpngw-subnet",
        "cidr": None,
        "prefix_length": 24,
    }
    assert VMManager._gateway_subnet_name(spec) == "vpngw-subnet"


def test_extract_explicit_subnet_networks_skips_invalid_and_inherited_pools() -> None:
    explicit_subnet = SimpleNamespace(
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(
                use_network_pools=False,
                pools=[
                    SimpleNamespace(
                        cidrs=[
                            SimpleNamespace(cidr="172.16.10.0/24"),
                            SimpleNamespace(cidr="2001:db8::/64"),
                            SimpleNamespace(cidr="not-a-cidr"),
                        ]
                    )
                ],
            )
        )
    )
    inherited_subnet = SimpleNamespace(
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(
                use_network_pools=True,
                pools=[SimpleNamespace(cidrs=[SimpleNamespace(cidr="172.16.20.0/24")])],
            )
        )
    )

    assert VMManager._extract_explicit_subnet_networks(explicit_subnet) == [
        ipaddress.ip_network("172.16.10.0/24")
    ]
    assert VMManager._extract_explicit_subnet_networks(inherited_subnet) == []


def test_build_cloud_init_includes_ssh_key_and_local_prefixes() -> None:
    manager = VMManager(project_id="project-test", zone="eu-west1")

    rendered = manager._build_cloud_init(
        ssh_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey",
        local_prefixes=["10.0.0.0/16", "172.16.0.0/24"],
    )

    assert "ssh_authorized_keys" in rendered
    assert "10.0.0.0/16" in rendered
    assert "172.16.0.0/24" in rendered
    assert "/usr/local/bin/setup-vpngw-firewall.sh" in rendered
    assert "/usr/local/bin/nebius-vpngw-esp4-preflight.sh" in rendered
    assert "/etc/systemd/system/nebius-vpngw-esp4-preflight.service" in rendered
    assert "/var/lib/nebius-vpngw/esp4-reboot-pending" in rendered
    assert "systemctl start nebius-vpngw-esp4-preflight strongswan-starter frr" in rendered
    assert "  - [ systemctl, start, strongswan-starter ]" not in rendered
    assert "net.ipv4.ip_forward = 1" in rendered
    assert yaml.safe_load(rendered)["package_upgrade"] is True
