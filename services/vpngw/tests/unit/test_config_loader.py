from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from nebius_vpngw.config_loader import (
    _detect_vendor,
    _validate_tunnel_inner_ips,
    load_local_config,
    merge_peer_configs_into_local_config,
)


def test_load_local_config_reads_ssh_public_key_from_path(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    public_key_path = tmp_path / "id_ed25519.pub"
    public_key_path.write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyFromFile",
        encoding="utf-8",
    )
    vm_spec = sample_config["gateway_group"]["vm_spec"]
    vm_spec.pop("ssh_public_key")
    vm_spec["ssh_public_key_path"] = str(public_key_path)

    config_path = tmp_path / "ssh-key.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    loaded = load_local_config(config_path)

    assert loaded["gateway_group"]["vm_spec"]["ssh_public_key"] == public_key_path.read_text(
        encoding="utf-8"
    )


def test_load_local_config_reports_missing_environment_variables(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["project_id"] = "${PROJECT_ID}"
    config_path = tmp_path / "missing-env.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="Missing environment variables"):
        load_local_config(config_path)


def test_load_local_config_can_keep_missing_placeholders_for_bootstrap_commands(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["connections"][0]["tunnels"][0]["psk"] = "${GCP_TUNNEL_1_PSK}"
    sample_config["connections"][0]["tunnels"].append(
        {
            "name": "tunnel-2",
            "gateway_instance_index": 0,
            "ha_role": "passive",
            "remote_public_ip": "198.51.100.11",
            "psk": "${GCP_TUNNEL_2_PSK}",
            "inner_cidr": "169.254.19.0/30",
            "inner_local_ip": "169.254.19.1",
            "inner_remote_ip": "169.254.19.2",
        }
    )
    config_path = tmp_path / "bootstrap.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    loaded = load_local_config(
        config_path,
        allow_missing_placeholders=True,
        validate_schema=False,
    )

    tunnels = loaded["connections"][0]["tunnels"]
    assert tunnels[0]["psk"] == "${GCP_TUNNEL_1_PSK}"
    assert tunnels[1]["psk"] == "${GCP_TUNNEL_2_PSK}"


def test_detect_vendor_uses_known_template_hints() -> None:
    assert _detect_vendor("Google Cloud HA VPN and Cloud Router") == "gcp"
    assert _detect_vendor("Amazon customer gateway configuration") == "aws"
    assert _detect_vendor("Azure virtual network gateway") == "azure"
    assert _detect_vendor("Cisco IOS crypto isakmp") == "cisco"
    assert _detect_vendor("Some generic VPN document") == "generic"


def test_validate_tunnel_inner_ips_rejects_addresses_outside_cidr() -> None:
    with pytest.raises(ValueError, match="NOT within inner_cidr"):
        _validate_tunnel_inner_ips(
            {
                "inner_cidr": "169.254.10.0/30",
                "inner_local_ip": "169.254.10.1",
                "inner_remote_ip": "169.254.10.9",
            },
            "peer-a",
        )


def test_merge_peer_configs_fills_missing_fields_without_overwriting_local_values(
    sample_config: dict,
) -> None:
    tunnel = sample_config["connections"][0]["tunnels"][0]
    tunnel["remote_public_ip"] = None
    tunnel["psk"] = "keep-local-value"

    peer_spec = {
        "vendor": "aws",
        "remote_asn": 65020,
        "tunnels": [
            {
                "psk": "peer-value",
                "remote_public_ip": "198.51.100.99",
                "inner_cidr": "169.254.18.224/30",
                "inner_local_ip": "169.254.18.225",
                "inner_remote_ip": "169.254.18.226",
                "crypto": {
                    "ike_proposals": ["aes256-sha256-modp2048"],
                    "ike_lifetime_seconds": 28800,
                    "esp_proposals": ["aes256-sha256-modp2048"],
                    "esp_lifetime_seconds": 3600,
                },
            }
        ],
    }

    with patch("nebius_vpngw.config_loader._parse_peer_file", return_value=peer_spec):
        merged = merge_peer_configs_into_local_config(sample_config, [Path("peer.txt")])

    merged_tunnel = merged["connections"][0]["tunnels"][0]
    assert merged_tunnel["psk"] == "keep-local-value"
    assert merged_tunnel["remote_public_ip"] == "198.51.100.99"
    assert merged_tunnel["crypto"]["ike_proposals"] == ["aes256-sha256-modp2048"]


def test_merge_peer_configs_can_prefer_peer_values(sample_config: dict) -> None:
    sample_config["connections"][0]["vendor"] = "generic"
    sample_config["connections"][0]["bgp"]["remote_asn"] = None

    peer_spec = {
        "vendor": "gcp",
        "remote_asn": 65100,
        "tunnels": [
            {
                "psk": "peer-psk",
                "remote_public_ip": "198.51.100.88",
                "inner_cidr": "169.254.18.224/30",
                "inner_local_ip": "169.254.18.225",
                "inner_remote_ip": "169.254.18.226",
            }
        ],
    }

    with patch("nebius_vpngw.config_loader._parse_peer_file", return_value=peer_spec):
        merged = merge_peer_configs_into_local_config(
            sample_config,
            [Path("peer.txt")],
            prefer_peer=True,
        )

    connection = merged["connections"][0]
    tunnel = connection["tunnels"][0]
    assert connection["vendor"] == "gcp"
    assert connection["bgp"]["remote_asn"] == 65100
    assert tunnel["psk"] == "peer-psk"
    assert tunnel["remote_public_ip"] == "198.51.100.88"
