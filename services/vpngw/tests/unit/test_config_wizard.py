from __future__ import annotations

import stat
from pathlib import Path
from typing import Annotated
from unittest.mock import patch

import pytest
import yaml
from pydantic import StringConstraints, TypeAdapter, ValidationError
from rich.console import Console
from typer.testing import CliRunner

from nebius_vpngw import cli as cli_module
from nebius_vpngw.cli import app
from nebius_vpngw.config_wizard import (
    _base_candidate,
    _connection_phase,
    _format_validation_error,
    _validate_psk_input,
)
from nebius_vpngw.schema import VPNGatewayConfig


def _bgp_wizard_input(
    *,
    prepare_network: str = "no",
    psk: str = "",
    subnet_cidr: str = "",
    subnet_prefix_length: str = "",
) -> str:
    answers = [
        "tenant-test",
        "project-test",
        "eu-north1",
        "",  # gateway name
        "",  # instance count
        "no",  # advanced settings
        "",  # network id
        "",  # subnet name
        subnet_cidr,
        subnet_prefix_length,
        "",  # local prefixes
        "",  # connection count
        "",  # connection name -> site-1
        "",  # vendor -> generic
        "",  # routing -> bgp
        "",  # local ASN
        "",  # peer ASN
        "",  # optional BGP prefix allowlist
        "",  # paths per instance
        "",  # tunnel name
        "198.51.100.10",
        psk,
        "",  # APIPA /30
        "yes",  # Nebius uses first host
        "yes",  # write reviewed config
        prepare_network,
    ]
    return "\n".join(answers) + "\n"


def _static_wizard_input(*, psk: str = "literal-secret-123") -> str:
    answers = [
        "tenant-test",
        "project-test",
        "eu-north1",
        "",  # gateway name
        "",  # instance count
        "no",  # advanced settings
        "",  # network id
        "",  # subnet name
        "",  # subnet CIDR
        "",  # subnet prefix length
        "",  # local prefixes
        "",  # connection count
        "",  # connection name
        "",  # vendor
        "static",
        "",  # remote prefixes
        "",  # paths per instance
        "",  # tunnel name
        "198.51.100.10",
        psk,
        "",  # APIPA /30
        "yes",
        "yes",
        "no",
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
    assert model.connections[0].name == "site-1"
    assert model.connections[0].vendor == "generic"
    assert model.connections[0].tunnels[0].name == "site-1-gw1-tunnel1"
    assert model.connections[0].tunnels[0].psk == "${SITE_1_GW1_TUNNEL1_PSK}"
    assert "gcp" not in config_path.read_text(encoding="utf-8").casefold()
    assert result.output.count("Local BGP ASN") == 1
    assert result.output.index("Routing mode") < result.output.index("Local BGP ASN")
    assert "literal input" in result.output
    assert "Skipped. Run nebius-vpngw prep-network" in result.output
    assert "Prepare networking now, or later" in result.output
    assert "--local-config-file" in result.output
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_wizard_explains_creation_only_subnet_prefix(tmp_path: Path) -> None:
    config_path = tmp_path / "explicit-subnet.config.yaml"

    result = CliRunner().invoke(
        app,
        ["create-config", str(config_path), "--interactive"],
        input=_bgp_wizard_input(
            subnet_cidr="172.16.30.0/28",
            subnet_prefix_length="24",
        ),
    )

    assert result.exit_code == 0, result.output
    transcript = " ".join(result.output.split())
    assert "Automatic subnet prefix length (new subnet only)" in transcript
    assert (
        "Used only when CIDR is blank and the named subnet does not exist; it sets "
        "the size of the auto-created subnet. An explicit CIDR's /prefix always wins." in transcript
    )
    subnet = yaml.safe_load(config_path.read_text(encoding="utf-8"))["gateway_group"]["subnet"]
    assert subnet["cidr"] == "172.16.30.0/28"
    assert subnet["prefix_length"] == 24


def test_static_wizard_stores_hidden_literal_without_asking_for_asn(tmp_path: Path) -> None:
    config_path = tmp_path / "static.config.yaml"
    literal = "literal-secret-123"

    result = CliRunner().invoke(
        app,
        ["create-config", str(config_path), "--interactive"],
        input=_static_wizard_input(psk=literal),
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["connections"][0]["routing_mode"] == "static"
    assert payload["connections"][0]["tunnels"][0]["psk"] == literal
    assert "Local BGP ASN" not in result.output
    assert literal not in result.output


def test_mixed_connection_phase_asks_local_asn_once_at_first_bgp() -> None:
    class ScriptedPrompt:
        def __init__(self) -> None:
            self.console = Console(record=True)
            self.routing_modes = iter(("static", "bgp"))
            self.remote_ips = iter(("198.51.100.10", "198.51.100.20"))
            self.local_asn_prompts = 0

        def ask(self, label, *, default=None, validator=None, **_kwargs):
            if label.startswith("Remote public IP"):
                value = next(self.remote_ips)
            elif label == "Local BGP ASN":
                self.local_asn_prompts += 1
                value = "65010"
            elif label == "Peer BGP ASN":
                value = "64514"
            else:
                value = str(default or "")
            return validator(value) if validator is not None else value

        def ask_int(self, label, **_kwargs):
            return 2 if label == "Number of peer connections" else 1

        def ask_choice(self, label, _choices, **_kwargs):
            return next(self.routing_modes) if label == "Routing mode" else "generic"

        def ask_bool(self, _label, **_kwargs):
            return True

    candidate = _base_candidate()
    prompt = ScriptedPrompt()

    _connection_phase(candidate, prompt)  # type: ignore[arg-type]

    assert [connection["routing_mode"] for connection in candidate["connections"]] == [
        "static",
        "bgp",
    ]
    assert prompt.local_asn_prompts == 1
    assert [connection["name"] for connection in candidate["connections"]] == [
        "site-1",
        "site-2",
    ]


def test_psk_heuristic_and_validation_errors_never_render_secret_input() -> None:
    assert _validate_psk_input("PEER_TUNNEL_1_PSK") == "${PEER_TUNNEL_1_PSK}"
    assert _validate_psk_input("mixed-Case-secret") == "mixed-Case-secret"
    assert _validate_psk_input("ALLCAPSPSK") == "${ALLCAPSPSK}"
    with pytest.raises(ValueError, match="cannot contain"):
        _validate_psk_input("literal-${fragment}")

    secret = "secret-value-that-must-not-render"
    adapter = TypeAdapter(Annotated[str, StringConstraints(max_length=8)])
    with pytest.raises(ValidationError) as caught:
        adapter.validate_python(secret)
    rendered = _format_validation_error(caught.value)
    assert secret not in rendered
    assert "String should have at most 8 characters" in rendered


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
        "198.51.100.10\n\n",
        "not-an-ip\n198.51.100.10\n\n",
        1,
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
    assert kwargs["region"] is None
    assert kwargs["interactive"] is True
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
