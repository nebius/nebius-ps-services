from __future__ import annotations

import ipaddress
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from nebius_vpngw.config_loader import (
    _detect_vendor,
    _validate_tunnel_inner_ips,
    load_local_config,
    merge_peer_configs_into_local_config,
    merge_with_peer_configs,
)
from nebius_vpngw.config_template import DEFAULT_CONFIG_TEMPLATE
from nebius_vpngw.schema import validate_config


@pytest.mark.parametrize(
    ("local_prefix", "remote_prefix", "source", "target", "error"),
    [
        ("10.0.0.1/16", "203.0.113.0/24", "10.0.0.2", "203.0.113.5", None),
        ("10.0.0.0/16", "203.0.113.1/24", "10.0.0.2", "203.0.113.5", None),
        ("10.0.0.1/16", "203.0.113.1/24", "10.0.0.2", "203.0.113.5", None),
        ("10.0.0.1/16", "203.0.113.1/24", "10.1.0.2", "203.0.113.5", "source is outside"),
        ("10.0.0.1/16", "203.0.113.1/24", "10.0.0.2", "203.0.114.5", "target is outside"),
    ],
)
@pytest.mark.parametrize("ha", [False, True])
def test_shared_probe_coverage_preserves_accepted_cidr_spelling(
    sample_config,
    ha,
    local_prefix,
    remote_prefix,
    source,
    target,
    error,
):
    cfg = deepcopy(sample_config)
    cfg["gateway_group"].update(
        instance_count=2,
        external_ips=[["203.0.113.10"], ["203.0.113.20"]],
        vm_ha={
            "enabled": True,
            "cluster_id": "gateway-cluster",
            "members": [
                {"node_id": "gateway-a", "instance_index": 0, "role": "active"},
                {"node_id": "gateway-b", "instance_index": 1, "role": "passive"},
            ],
        },
    )
    cfg["gateway"]["local_prefixes"] = [local_prefix]
    connection = cfg["connections"][0]
    connection["remote_prefixes"] = [remote_prefix]
    second = deepcopy(connection["tunnels"][0])
    second.update(
        name="tunnel-node-b",
        gateway_instance_index=1,
        inner_cidr="169.254.19.0/30",
        inner_local_ip="169.254.19.1",
        inner_remote_ip="169.254.19.2",
    )
    connection["tunnels"].append(second)
    if not ha:
        cfg["gateway_group"].pop("vm_ha")
    # Existing CIDR validation accepts host bits without rewriting the config.
    assert validate_config(cfg).gateway.local_prefixes == [local_prefix]
    cfg["gateway"]["tunnel_probes"] = {"tunnel-1": {"source_ip": source, "target_ip": target}}
    if error:
        with pytest.raises(ValueError, match=error):
            validate_config(cfg)
    else:
        validated = validate_config(cfg)
        assert validated.gateway.local_prefixes == [local_prefix]
        assert validated.connections[0].remote_prefixes == [remote_prefix]
        assert validated.gateway.tunnel_probes["tunnel-1"].source_ip == source


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


def test_load_local_config_applies_region_override_before_schema_validation(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config.pop("region_id", None)
    sample_config["gateway_group"].pop("region", None)
    config_path = tmp_path / "region-override.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    loaded = load_local_config(config_path, region_override="eu-north1")

    assert loaded["region_id"] == "eu-north1"
    assert loaded["gateway_group"]["region"] == "eu-north1"


def test_load_local_config_region_override_replaces_conflicting_placeholders(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["region_id"] = "${TOP_LEVEL_REGION}"
    sample_config["gateway_group"]["region"] = "${GROUP_REGION}"
    config_path = tmp_path / "conflicting-region-override.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    loaded = load_local_config(config_path, region_override="eu-north1")

    assert loaded["region_id"] == "eu-north1"
    assert loaded["gateway_group"]["region"] == "eu-north1"


def test_load_local_config_region_override_does_not_hide_other_missing_references(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["region_id"] = "${REGION_ID}"
    sample_config["gateway_group"]["region"] = "${REGION_ID}"
    sample_config["project_id"] = "${REGION_ID}"
    config_path = tmp_path / "shared-region-placeholder.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="REGION_ID"):
        load_local_config(config_path, region_override="eu-north1")


def test_load_local_config_rejects_blank_explicit_region(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    config_path = tmp_path / "blank-region-override.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="--region must resolve"):
        load_local_config(config_path, region_override="   ")


def test_merge_treats_blank_group_region_as_absent(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["region_id"] = "eu-north1"
    sample_config["gateway_group"]["region"] = "   "
    config_path = tmp_path / "blank-group-region.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    loaded = load_local_config(config_path)
    plan = merge_with_peer_configs(loaded, [])

    assert plan.gateway_group.region == "eu-north1"
    assert loaded["region_id"] == "eu-north1"
    assert loaded["gateway_group"]["region"] == "eu-north1"


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
                    },
                    {
                        "node_id": "gateway-b",
                        "instance_index": 1,
                        "role": "passive",
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
    # Public apply later inspects status with the effective region made
    # explicit. That equivalent input must retain the installed generation.
    explicit_region = deepcopy(normalized)
    explicit_region["gateway_group"]["region"] = normalized["region_id"]
    explicit_plan = merge_with_peer_configs(explicit_region, [])
    assert explicit_plan.vm_ha.generation == first_plan.vm_ha.generation
    repeated = deepcopy(normalized)
    repeated_first = merge_with_peer_configs(repeated, [])
    assert merge_with_peer_configs(repeated, []).vm_ha.generation == repeated_first.vm_ha.generation
    different_region = deepcopy(normalized)
    different_region["gateway_group"]["region"] = "eu-north1"
    different_plan = merge_with_peer_configs(different_region, [])
    assert different_plan.vm_ha.generation != first_plan.vm_ha.generation
    omitted_probes = deepcopy(normalized)
    omitted_probes["gateway"].pop("tunnel_probes", None)
    omitted_plan = merge_with_peer_configs(omitted_probes, [])
    assert omitted_plan.vm_ha.generation == first_plan.vm_ha.generation
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


def test_template_and_vm_ha_example_match_the_schema() -> None:
    example_path = Path(__file__).parents[2] / "examples" / "vm-ha-bgp-example.yaml"

    draft = yaml.safe_load(DEFAULT_CONFIG_TEMPLATE)
    template = validate_config(draft)
    assert "ssh_allowed_cidrs" not in template.gateway.model_dump()
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


@pytest.mark.parametrize("optional_field", ["network_id", "external_ips"])
@pytest.mark.parametrize("required_field", ["project_id", "psk", None])
@pytest.mark.parametrize("status_only", [False, True])
def test_optional_placeholder_does_not_hide_required_occurrence(
    tmp_path, sample_config, monkeypatch, optional_field, required_field, status_only
):
    variable = "NETWORK_ID"
    monkeypatch.delenv(variable, raising=False)
    placeholder = "${NETWORK_ID}"
    sample_config["gateway_group"][optional_field] = (
        placeholder if optional_field == "network_id" else [[placeholder]]
    )
    if required_field == "project_id":
        sample_config["project_id"] = placeholder
    elif required_field == "psk":
        sample_config["connections"][0]["tunnels"][0]["psk"] = placeholder
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(sample_config), encoding="utf-8")
    if required_field == "project_id" or (required_field == "psk" and not status_only):
        with pytest.raises(ValueError, match="Missing environment variables.*NETWORK_ID"):
            load_local_config(path, allow_missing_tunnel_psk_placeholders=status_only)
    else:
        load_local_config(path, allow_missing_tunnel_psk_placeholders=status_only)


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


@pytest.mark.parametrize("ha", [False, True])
@pytest.mark.parametrize("workload_probe", [False, True])
def test_probe_projection_is_local_and_does_not_mutate_source(sample_config, ha, workload_probe):
    cfg = deepcopy(sample_config)
    cfg["gateway_group"].update(instance_count=2, external_ips=[["203.0.113.10"], ["203.0.113.20"]])
    if ha:
        cfg["gateway_group"]["vm_ha"] = {
            "enabled": True,
            "cluster_id": "cluster",
            "members": [
                {"node_id": "a", "instance_index": 0, "role": "active"},
                {"node_id": "b", "instance_index": 1, "role": "passive"},
            ],
        }
    first = cfg["connections"][0]["tunnels"][0]
    second = deepcopy(first)
    second.update(name="second-node", gateway_instance_index=1)
    cfg["connections"][0]["tunnels"] = [first, second]
    cfg["gateway"]["tunnel_probes"] = {
        tunnel["name"]: {
            "source_ip": tunnel["inner_local_ip"],
            "target_ip": tunnel["inner_remote_ip"],
        }
        for tunnel in (first, second)
    }
    if workload_probe:
        network = ipaddress.IPv4Network(cfg["connections"][0]["remote_prefixes"][0])
        for probe in cfg["gateway"]["tunnel_probes"].values():
            probe["target_ip"] = str(network.network_address + 1)
    original = deepcopy(cfg)
    validated = validate_config(cfg)
    plan = merge_with_peer_configs(validated.model_dump(mode="python"), [])
    assert cfg == original
    for instance, tunnel in zip(plan.per_instance, (first, second), strict=True):
        rendered = yaml.safe_load(instance.config_yaml)
        assert set(rendered["gateway"]["tunnel_probes"]) == {tunnel["name"]}
        assert (
            rendered["gateway"]["tunnel_probes"][tunnel["name"]]["target_ip"]
            == cfg["gateway"]["tunnel_probes"][tunnel["name"]]["target_ip"]
        )
        assert "tunnel_probes" not in rendered.get("vm_ha", {})


@pytest.mark.parametrize("fault", ["unknown", "bgp", "disabled", "old-field"])
def test_shared_probe_rejects_invalid_scope_before_rendering(sample_config, fault):
    cfg = deepcopy(sample_config)
    tunnel = cfg["connections"][0]["tunnels"][0]
    cfg["gateway"]["tunnel_probes"] = {
        "missing" if fault == "unknown" else tunnel["name"]: {
            "source_ip": tunnel["inner_local_ip"],
            "target_ip": tunnel["inner_remote_ip"],
        }
    }
    if fault == "bgp":
        cfg["connections"][0]["routing_mode"] = "bgp"
        cfg["connections"][0]["bgp"].update(enabled=True, remote_asn=65001)
    elif fault == "disabled":
        tunnel["ha_role"] = "disable"
    elif fault == "old-field":
        cfg["gateway_group"]["vm_ha"] = {
            "enabled": False,
            "tunnel_probes": cfg["gateway"].pop("tunnel_probes"),
        }
    expected = {
        "unknown": "unknown tunnel",
        "bgp": "enabled Static",
        "disabled": "enabled Static",
        "old-field": "Move gateway_group.vm_ha.tunnel_probes",
    }
    with pytest.raises(ValueError, match=expected[fault]):
        validate_config(cfg)
