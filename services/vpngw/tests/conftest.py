from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def block_unit_test_network(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if request.node.get_closest_marker("integration"):
        return

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
