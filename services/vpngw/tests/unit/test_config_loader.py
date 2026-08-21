from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from nebius_vpngw.config_loader import (
    _detect_vendor,
    _validate_tunnel_inner_ips,
    _validate_vm_ha_nebius_credentials,
    load_local_config,
    merge_peer_configs_into_local_config,
    merge_with_peer_configs,
)
from nebius_vpngw.config_template import DEFAULT_CONFIG_TEMPLATE
from nebius_vpngw.schema import validate_config


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


def test_omitted_vm_ha_preserves_single_node_resolved_plan(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    config_path = tmp_path / "single-node.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    loaded = load_local_config(config_path)
    plan = merge_with_peer_configs(loaded, [])

    assert "vm_ha" not in loaded["gateway_group"]
    assert plan.vm_ha is None
    assert [(node.instance_index, node.hostname) for node in plan.per_instance] == [
        (0, "nebius-vpn-gw-0")
    ]
    assert "vm_ha" not in yaml.safe_load(plan.per_instance[0].config_yaml)


def test_vm_ha_resolves_two_deterministic_node_manifests(sample_config: dict) -> None:
    cfg = deepcopy(sample_config)
    cfg["gateway_group"].update(
        {
            "instance_count": 2,
            "external_ips": [["203.0.113.10"], ["203.0.113.20"]],
            "vm_ha": {
                "enabled": True,
                "cluster_id": "gateway-cluster",
                "members": [
                    {
                        "node_id": "gateway-a",
                        "instance_index": 0,
                        "role": "active",
                        "nebius_credentials_path": _credential_path("gateway-a"),
                    },
                    {
                        "node_id": "gateway-b",
                        "instance_index": 1,
                        "role": "passive",
                        "nebius_credentials_path": _credential_path("gateway-b"),
                    },
                ],
            },
        }
    )
    second_tunnel = deepcopy(cfg["connections"][0]["tunnels"][0])
    second_tunnel.update(
        {
            "name": "tunnel-node-b",
            "gateway_instance_index": 1,
            "remote_public_ip": "198.51.100.11",
            "inner_cidr": "169.254.19.0/30",
            "inner_local_ip": "169.254.19.1",
            "inner_remote_ip": "169.254.19.2",
        }
    )
    cfg["connections"][0]["tunnels"].append(second_tunnel)
    validated = validate_config(cfg)
    normalized = validated.model_dump(mode="python", exclude_none=False)

    first_plan = merge_with_peer_configs(deepcopy(normalized), [])
    second_plan = merge_with_peer_configs(deepcopy(normalized), [])
    reordered = deepcopy(normalized)
    reordered["gateway_group"]["vm_ha"]["members"].reverse()
    reordered_plan = merge_with_peer_configs(reordered, [])

    assert first_plan.vm_ha == second_plan.vm_ha
    assert first_plan.vm_ha is not None
    assert first_plan.gateway_group.vm_ha is not None
    assert first_plan.gateway_group.vm_ha.cluster_id == "gateway-cluster"
    assert first_plan.gateway_group.vm_ha.active_instance_index == 0
    assert reordered_plan.vm_ha is not None
    assert (
        reordered_plan.vm_ha.generation.generation_id == first_plan.vm_ha.generation.generation_id
    )
    assert len(first_plan.vm_ha.generation.generation_id) == 64
    static_routes = json.loads(first_plan.vm_ha.generation.logical_manifests.static_routes_json)
    assert static_routes == [{"connection": "static-peer", "remote_prefixes": ["203.0.113.0/24"]}]
    assert [node.vm_ha_node.node_id for node in first_plan.per_instance if node.vm_ha_node] == [
        "gateway-a",
        "gateway-b",
    ]
    manifests = [yaml.safe_load(node.config_yaml)["vm_ha"] for node in first_plan.per_instance]
    assert {manifest["node"]["role"] for manifest in manifests} == {"active", "passive"}
    assert len({manifest["generation"]["generation_id"] for manifest in manifests}) == 1
    assert all(
        manifest["readiness"]["required_node_ids"] == ["gateway-a", "gateway-b"]
        for manifest in manifests
    )
    assert all("credential_sources" not in yaml.safe_dump(manifest) for manifest in manifests)


def _credential_path(node_id: str) -> str:
    return f"/operator-secrets/{node_id}/nebius-credentials.json"


def test_vm_ha_nebius_credential_preflight_accepts_json_and_redacts_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "nebius-credentials.json"
    source.write_text('{"service_account_id":"fixture"}', encoding="utf-8")
    source.chmod(0o600)
    config = {
        "gateway_group": {
            "vm_ha": {
                "enabled": True,
                "members": [
                    {
                        "node_id": "gateway-a",
                        "nebius_credentials_path": str(source),
                    }
                ],
            }
        }
    }

    _validate_vm_ha_nebius_credentials(config)

    missing = tmp_path / "missing-nebius-credentials"
    config["gateway_group"]["vm_ha"]["members"][0]["nebius_credentials_path"] = str(
        missing
    )
    with pytest.raises(ValueError) as exc_info:
        _validate_vm_ha_nebius_credentials(config)
    assert str(missing) not in str(exc_info.value)
    assert "Nebius credentials for gateway-a are unavailable" in str(exc_info.value)


def test_vm_ha_nebius_credential_preflight_accepts_distinct_member_files(
    tmp_path: Path,
) -> None:
    members: list[dict[str, str]] = []
    for node_id in ("gateway-a", "gateway-b"):
        source = tmp_path / f"{node_id}.json"
        source.write_text(json.dumps({"node": node_id}), encoding="utf-8")
        source.chmod(0o600)
        members.append({"node_id": node_id, "nebius_credentials_path": str(source)})
    config = {
        "gateway_group": {
            "vm_ha": {
                "enabled": True,
                "members": members,
            }
        }
    }

    _validate_vm_ha_nebius_credentials(config)


def test_vm_ha_credential_preflight_rejects_symlinks_without_reading_them(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_text('{"credential":"fixture"}', encoding="utf-8")
    real.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(real)

    with pytest.raises(ValueError, match="are unavailable"):
        _validate_vm_ha_nebius_credentials(
            {
                "gateway_group": {
                    "vm_ha": {
                        "enabled": True,
                        "members": [
                            {
                                "node_id": "gateway-a",
                                "nebius_credentials_path": str(link),
                            }
                        ],
                    }
                }
            }
        )


@pytest.mark.parametrize("unsafe_mode", [0o644, 0o640, 0o660])
def test_vm_ha_credential_preflight_requires_mode_0600_and_redacts_path(
    tmp_path: Path,
    unsafe_mode: int,
) -> None:
    unsafe = tmp_path / "nebius-credentials.json"
    unsafe.write_text('{"credential":"fixture"}', encoding="utf-8")
    unsafe.chmod(unsafe_mode)

    with pytest.raises(ValueError, match="owner-only") as exc_info:
        _validate_vm_ha_nebius_credentials(
            {
                "gateway_group": {
                    "vm_ha": {
                        "enabled": True,
                        "members": [
                            {
                                "node_id": "gateway-a",
                                "nebius_credentials_path": str(unsafe),
                            }
                        ],
                    }
                }
            }
        )

    assert str(unsafe) not in str(exc_info.value)


def test_vm_ha_credential_preflight_rejects_cross_member_hardlink_aliases(
    tmp_path: Path,
) -> None:
    first = tmp_path / "gateway-a.json"
    second = tmp_path / "gateway-b.json"
    first.write_text('{"credential":"fixture"}', encoding="utf-8")
    first.chmod(0o600)
    os.link(first, second)
    members = [
        {"node_id": "gateway-a", "nebius_credentials_path": str(first)},
        {"node_id": "gateway-b", "nebius_credentials_path": str(second)},
    ]

    with pytest.raises(ValueError, match="non-linked regular file") as exc_info:
        _validate_vm_ha_nebius_credentials(
            {"gateway_group": {"vm_ha": {"enabled": True, "members": members}}}
        )

    assert str(second) not in str(exc_info.value)


def test_vm_ha_credential_preflight_rejects_malformed_json(tmp_path: Path) -> None:
    source = tmp_path / "malformed.json"
    source.write_text("not-json", encoding="utf-8")
    source.chmod(0o600)

    with pytest.raises(ValueError, match="not valid JSON") as exc_info:
        _validate_vm_ha_nebius_credentials(
            {
                "gateway_group": {
                    "vm_ha": {
                        "enabled": True,
                        "members": [
                            {
                                "node_id": "gateway-a",
                                "nebius_credentials_path": str(source),
                            }
                        ],
                    }
                }
            }
        )

    assert str(source) not in str(exc_info.value)


def test_template_and_vm_ha_example_match_the_schema() -> None:
    example_path = Path(__file__).parents[2] / "examples" / "vm-ha-bgp-example.yaml"

    template = validate_config(yaml.safe_load(DEFAULT_CONFIG_TEMPLATE))
    example = validate_config(yaml.safe_load(example_path.read_text(encoding="utf-8")))

    assert template.gateway_group.vm_ha is not None
    assert template.gateway_group.vm_ha.enabled is False
    assert example.gateway_group.vm_ha is not None
    assert example.gateway_group.vm_ha.enabled is True


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


def test_load_local_config_can_keep_short_missing_tunnel_psks_for_status(
    tmp_path: Path,
    sample_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variable = "PSK"
    monkeypatch.delenv(variable, raising=False)
    sample_config["connections"][0]["tunnels"][0]["psk"] = f"${{{variable}}}"
    config_path = tmp_path / "status.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    loaded = load_local_config(
        config_path,
        allow_missing_tunnel_psk_placeholders=True,
    )

    assert loaded["connections"][0]["tunnels"][0]["psk"] == f"${{{variable}}}"


def test_status_placeholder_policy_rejects_psk_variable_used_by_required_field(
    tmp_path: Path,
    sample_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variable = "STATUS_PSK_AND_PROJECT_ID"
    monkeypatch.delenv(variable, raising=False)
    sample_config["project_id"] = f"${{{variable}}}"
    sample_config["connections"][0]["tunnels"][0]["psk"] = f"${{{variable}}}"
    config_path = tmp_path / "status-invalid.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match=variable):
        load_local_config(
            config_path,
            allow_missing_tunnel_psk_placeholders=True,
        )


def test_status_placeholder_policy_keeps_literal_psk_schema_validation(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["connections"][0]["tunnels"][0]["psk"] = "short"
    config_path = tmp_path / "status-invalid-psk.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="at least 8 characters"):
        load_local_config(
            config_path,
            allow_missing_tunnel_psk_placeholders=True,
        )


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
