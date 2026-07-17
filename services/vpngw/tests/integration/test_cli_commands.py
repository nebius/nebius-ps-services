from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from nebius_vpngw.cli import app

pytestmark = pytest.mark.integration


def test_create_config_writes_template(tmp_path: Path) -> None:
    config_path = tmp_path / "generated.config.yaml"

    result = CliRunner().invoke(app, ["create-config", str(config_path)])

    assert result.exit_code == 0
    assert config_path.exists()
    assert "gateway_group:" in config_path.read_text(encoding="utf-8")


def test_validate_config_smoke_passes_for_sample_config(
    tmp_path: Path, sample_config: dict
) -> None:
    config_path = tmp_path / "integration.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(app, ["validate-config", str(config_path)])

    assert result.exit_code == 0
    assert "Configuration is valid" in result.stdout


@pytest.mark.parametrize(
    "config_name",
    ["static-example.config.yaml", "bgp-example.config.yaml"],
)
def test_published_example_config_validates(
    monkeypatch: pytest.MonkeyPatch, config_name: str
) -> None:
    example_path = Path(__file__).resolve().parents[2] / "examples" / config_name
    placeholder_values = {
        "TENANT_ID": "tenant-example",
        "PROJECT_ID": "project-example",
        "REGION_ID": "eu-north1",
        "SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleOnlyPublicKey",
        "STATIC_TUNNEL_1_PSK": "replace-with-a-secure-static-tunnel-1-psk",
        "STATIC_TUNNEL_2_PSK": "replace-with-a-secure-static-tunnel-2-psk",
        "BGP_TUNNEL_1_PSK": "replace-with-a-secure-bgp-tunnel-1-psk",
        "BGP_TUNNEL_2_PSK": "replace-with-a-secure-bgp-tunnel-2-psk",
    }
    for name, value in placeholder_values.items():
        monkeypatch.setenv(name, value)

    result = CliRunner().invoke(app, ["validate-config", str(example_path)])

    assert result.exit_code == 0
    assert "Configuration is valid" in result.stdout


def test_validate_config_rejects_duplicate_external_ips(
    tmp_path: Path, sample_config: dict
) -> None:
    sample_config["gateway_group"]["instance_count"] = 2
    sample_config["gateway_group"]["external_ips"] = [["203.0.113.10"], ["203.0.113.10"]]

    config_path = tmp_path / "duplicate-external-ips.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(app, ["validate-config", str(config_path)])

    assert result.exit_code == 1
    assert "gateway_group.external_ips entries must be globally" in result.output
    assert "Conflicts: 203.0.113.10: external_ips[0][0], external_ips[1][0]" in result.output
