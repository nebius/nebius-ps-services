from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from nebius_vpngw.cli import app
from nebius_vpngw.config_loader import load_local_config, merge_with_peer_configs

REPO_ROOT = Path(__file__).resolve().parents[2]
DUMMY_SSH_PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestExampleKey"
DUMMY_PSK = "test-only-example-psk-1234567890"


def _sanitize_example_config(tmp_path: Path, example_name: str) -> Path:
    raw = yaml.safe_load((REPO_ROOT / example_name).read_text(encoding="utf-8")) or {}

    gateway_group = raw.get("gateway_group") or {}
    vm_spec = gateway_group.get("vm_spec") or {}
    vm_spec["ssh_public_key"] = DUMMY_SSH_PUBLIC_KEY
    vm_spec.pop("ssh_public_key_path", None)
    gateway_group["vm_spec"] = vm_spec
    raw["gateway_group"] = gateway_group

    for conn in raw.get("connections") or []:
        for tunnel in conn.get("tunnels") or []:
            psk = tunnel.get("psk")
            if isinstance(psk, str) and psk.startswith("${") and psk.endswith("}"):
                tunnel["psk"] = DUMMY_PSK

    config_path = tmp_path / example_name
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


@pytest.mark.parametrize(
    ("example_name", "instance_count", "connections_count", "tunnels_count"),
    [
        ("revolute-dev-vpn.config.yaml", 1, 3, 6),
        ("customer-gcp-3sites-single-vm.config.yaml", 1, 3, 6),
        ("customer-gcp-3sites-three-vms.config.yaml", 3, 3, 6),
    ],
)
def test_validate_config_accepts_sanitized_example_topologies(
    tmp_path: Path,
    example_name: str,
    instance_count: int,
    connections_count: int,
    tunnels_count: int,
) -> None:
    config_path = _sanitize_example_config(tmp_path, example_name)

    result = CliRunner().invoke(app, ["validate-config", str(config_path)])

    assert result.exit_code == 0
    assert f"Gateway instances: {instance_count}" in result.stdout
    assert f"Connections: {connections_count}" in result.stdout
    assert f"Tunnels: {tunnels_count}" in result.stdout


@pytest.mark.parametrize(
    ("example_name", "expected_connections_per_instance"),
    [
        (
            "revolute-dev-vpn.config.yaml",
            [["apps-gcp-ha-vpn", "etl-gcp-ha-vpn", "core-platform-gcp-ha-vpn"]],
        ),
        (
            "customer-gcp-3sites-single-vm.config.yaml",
            [["gcp-site-a", "gcp-site-b", "gcp-site-c"]],
        ),
        (
            "customer-gcp-3sites-three-vms.config.yaml",
            [["gcp-site-a"], ["gcp-site-b"], ["gcp-site-c"]],
        ),
    ],
)
def test_example_topologies_resolve_expected_per_vm_connection_ownership(
    tmp_path: Path,
    example_name: str,
    expected_connections_per_instance: list[list[str]],
) -> None:
    config_path = _sanitize_example_config(tmp_path, example_name)

    local_cfg = load_local_config(config_path)
    plan = merge_with_peer_configs(local_cfg, [])

    assert len(plan.per_instance) == len(expected_connections_per_instance)

    for inst_cfg, expected_connection_names in zip(
        plan.per_instance, expected_connections_per_instance, strict=True
    ):
        per_vm_cfg = yaml.safe_load(inst_cfg.config_yaml) or {}
        actual_connection_names = [
            str(conn.get("name") or "") for conn in per_vm_cfg.get("connections") or []
        ]

        assert actual_connection_names == expected_connection_names
        for conn in per_vm_cfg.get("connections") or []:
            for tunnel in conn.get("tunnels") or []:
                assert int(tunnel.get("gateway_instance_index", -1)) == inst_cfg.instance_index


def test_failover_requires_explicit_tunnel_name_for_multi_connection_example(
    tmp_path: Path,
) -> None:
    config_path = _sanitize_example_config(tmp_path, "customer-gcp-3sites-single-vm.config.yaml")

    result = CliRunner().invoke(app, ["failover", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert "Multiple tunnels found." in result.stdout


def test_failback_requires_explicit_tunnel_name_for_multi_connection_example(
    tmp_path: Path,
) -> None:
    config_path = _sanitize_example_config(tmp_path, "customer-gcp-3sites-single-vm.config.yaml")

    result = CliRunner().invoke(app, ["failback", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert "Multiple active tunnels found." in result.stdout
