from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from nebius_vpngw import cli as cli_module
from nebius_vpngw.cli import app
from nebius_vpngw.schema import VPNGatewayConfig


def _bgp_wizard_input(*, prepare_network: str = "no") -> str:
    answers = [
        "tenant-test",
        "project-test",
        "eu-north1",
        "",  # gateway name
        "",  # instance count
        "",  # zone
        "no",  # advanced settings
        "",  # network id
        "",  # subnet name
        "",  # subnet CIDR
        "",  # subnet prefix length
        "auto",
        "",  # local ASN
        "",  # local prefixes
        "1",  # connection count
        "gcp-site",
        "gcp",
        "bgp",
        "64514",
        "",  # optional BGP prefix allowlist
        "1",  # paths per instance
        "",  # tunnel name
        "198.51.100.10",
        "GCP_SITE_TUNNEL_PSK",
        "",  # APIPA /30
        "yes",  # Nebius uses first host
        "yes",  # write reviewed config
        prepare_network,
    ]
    return "\n".join(answers) + "\n"


def test_forced_wizard_writes_schema_valid_bgp_config(tmp_path: Path) -> None:
    config_path = tmp_path / "wizard.config.yaml"

    result = CliRunner().invoke(
        app,
        ["create-config", str(config_path), "--interactive"],
        input=_bgp_wizard_input(),
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    model = VPNGatewayConfig.model_validate(payload)
    assert model.project_id == "project-test"
    assert model.gateway_group.vm_ha is not None
    assert model.gateway_group.vm_ha.enabled is False
    assert model.connections[0].tunnels[0].psk == "${GCP_SITE_TUNNEL_PSK}"
    assert "secret values not collected" in result.output
    assert "Skipped. Run nebius-vpngw prep-network" in result.output


def test_wizard_quit_preserves_existing_file_with_force(tmp_path: Path) -> None:
    config_path = tmp_path / "existing.config.yaml"
    config_path.write_text("user-owned: true\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["create-config", str(config_path), "--interactive", "--force"],
        input="q\n",
    )

    assert result.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == "user-owned: true\n"
    assert "No configuration file was written" in result.output


def test_wizard_reprompts_invalid_typed_value(tmp_path: Path) -> None:
    config_path = tmp_path / "reprompt.config.yaml"
    wizard_input = _bgp_wizard_input().replace(
        "198.51.100.10\nGCP_SITE_TUNNEL_PSK",
        "not-an-ip\n198.51.100.10\nGCP_SITE_TUNNEL_PSK",
    )

    result = CliRunner().invoke(
        app,
        ["create-config", str(config_path), "--interactive"],
        input=wizard_input,
    )

    assert result.exit_code == 0, result.output
    assert "Enter a valid IPv4 or IPv6 address" in result.output
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["project_id"] == "project-test"


def test_wizard_back_restarts_previous_section_without_write(tmp_path: Path) -> None:
    config_path = tmp_path / "back.config.yaml"

    result = CliRunner().invoke(
        app,
        ["create-config", str(config_path), "--interactive"],
        input="tenant-test\nproject-test\neu-north1\nb\nq\n",
    )

    assert result.exit_code == 0
    assert not config_path.exists()
    assert result.output.count("1. Nebius project") == 2


def test_wizard_help_and_eof_do_not_create_file(tmp_path: Path) -> None:
    help_path = tmp_path / "help.config.yaml"
    help_result = CliRunner().invoke(
        app,
        ["create-config", str(help_path), "--interactive"],
        input="?\nq\n",
    )

    assert help_result.exit_code == 0
    assert not help_path.exists()
    assert "The tenant containing the VPN gateway project" in help_result.output

    eof_path = tmp_path / "eof.config.yaml"
    eof_result = CliRunner().invoke(
        app,
        ["create-config", str(eof_path), "--interactive"],
        input="",
    )

    assert eof_result.exit_code == 130
    assert not eof_path.exists()
    assert "Input ended" in eof_result.output


def test_wizard_refuses_existing_file_before_prompt_without_force(tmp_path: Path) -> None:
    config_path = tmp_path / "manual.config.yaml"
    config_path.write_text("manual: true\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["create-config", str(config_path), "--interactive"],
    )

    assert result.exit_code == 1
    assert config_path.read_text(encoding="utf-8") == "manual: true\n"
    assert "File already exists" in result.output
    assert "Configuration Wizard" not in result.output


def test_wizard_network_preparation_requires_separate_confirmation(tmp_path: Path) -> None:
    config_path = tmp_path / "prepare.config.yaml"

    with patch("nebius_vpngw.cli._run_network_preparation") as prepare:
        result = CliRunner().invoke(
            app,
            ["create-config", str(config_path), "--interactive"],
            input=_bgp_wizard_input(prepare_network="yes"),
        )

    assert result.exit_code == 0, result.output
    prepare.assert_called_once()
    args, kwargs = prepare.call_args
    assert args == (config_path,)
    assert kwargs["zone"] is None
    assert config_path.exists()


def test_atomic_publish_failure_preserves_forced_overwrite_target(tmp_path: Path) -> None:
    config_path = tmp_path / "atomic.config.yaml"
    config_path.write_text("manual: true\n", encoding="utf-8")

    with patch("nebius_vpngw.cli.os.replace", side_effect=OSError("replace failed")):
        result = CliRunner().invoke(
            app,
            ["create-config", str(config_path), "--interactive", "--force"],
            input=_bgp_wizard_input(),
        )

    assert result.exit_code == 1
    assert config_path.read_text(encoding="utf-8") == "manual: true\n"
    assert "replace failed" in result.output
    assert list(tmp_path.iterdir()) == [config_path]


def test_wizard_refuses_target_created_while_prompts_are_running(tmp_path: Path) -> None:
    config_path = tmp_path / "concurrent.config.yaml"
    original_fingerprint = cli_module._file_fingerprint
    calls = 0

    def concurrent_fingerprint(path: Path):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        path.write_text("concurrent: true\n", encoding="utf-8")
        return original_fingerprint(path)

    with patch("nebius_vpngw.cli._file_fingerprint", side_effect=concurrent_fingerprint):
        result = CliRunner().invoke(
            app,
            ["create-config", str(config_path), "--interactive"],
            input=_bgp_wizard_input(),
        )

    assert result.exit_code == 1
    assert config_path.read_text(encoding="utf-8") == "concurrent: true\n"
    assert "Destination changed while the wizard was running" in result.output
    assert list(tmp_path.iterdir()) == [config_path]


def test_mutually_exclusive_interactive_flags_fail_before_write(tmp_path: Path) -> None:
    config_path = tmp_path / "flags.config.yaml"

    result = CliRunner().invoke(
        app,
        [
            "create-config",
            str(config_path),
            "--interactive",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 2
    assert not config_path.exists()
    assert "cannot be used together" in result.output


def test_create_config_help_documents_both_compatibility_modes() -> None:
    result = CliRunner().invoke(app, ["create-config", "--help"])

    assert result.exit_code == 0
    assert "--interactive" in result.output
    assert "--no-interactive" in result.output
    assert "--force" in result.output


@pytest.mark.parametrize("answer", ["q\n", "quit\n", ":quit\n"])
def test_wizard_quit_never_creates_partial_file(tmp_path: Path, answer: str) -> None:
    config_path = tmp_path / "cancel.config.yaml"

    result = CliRunner().invoke(
        app,
        ["create-config", str(config_path), "--interactive"],
        input=answer,
    )

    assert result.exit_code == 0
    assert not config_path.exists()
