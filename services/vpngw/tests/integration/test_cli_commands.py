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


def test_validate_config_smoke_passes_for_sample_config(tmp_path: Path, sample_config: dict) -> None:
    config_path = tmp_path / "integration.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(app, ["validate-config", str(config_path)])

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
