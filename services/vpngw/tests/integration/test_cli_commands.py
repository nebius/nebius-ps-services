from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from nebius_vpngw.cli import app
from nebius_vpngw.config_template import DEFAULT_CONFIG_TEMPLATE
from nebius_vpngw.schema import VPNGatewayConfig

pytestmark = pytest.mark.integration


def test_create_config_writes_template(tmp_path: Path) -> None:
    config_path = tmp_path / "generated.config.yaml"

    result = CliRunner().invoke(app, ["create-config", str(config_path)])

    assert result.exit_code == 0
    assert config_path.exists()
    assert config_path.read_text(encoding="utf-8") == DEFAULT_CONFIG_TEMPLATE


def test_create_config_no_interactive_explicitly_writes_same_template(tmp_path: Path) -> None:
    config_path = tmp_path / "explicit-template.config.yaml"

    result = CliRunner().invoke(
        app,
        ["create-config", str(config_path), "--no-interactive"],
    )

    assert result.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == DEFAULT_CONFIG_TEMPLATE
    assert "Configuration Wizard" not in result.output


def test_configure_vm_ha_help_documents_two_phase_safe_handoff() -> None:
    result = CliRunner().invoke(app, ["configure-vm-ha", "--help"])

    assert result.exit_code == 0, result.output
    assert "--local-config-file" in result.output
    assert "--output" in result.output
    assert "source is never modified" in result.output.lower()
    assert "Phase 1" in result.output
    assert "Phase 2" in result.output


def test_set_vm_ha_mtls_help_exposes_digest_approval_without_target() -> None:
    result = CliRunner().invoke(app, ["set-vm-ha-mtls", "--help"])

    assert result.exit_code == 0, result.output
    assert "--dry-run" in result.output
    assert "--approve" in result.output
    assert "--target" not in result.output
    assert "Examples:" in result.output


@pytest.mark.parametrize(
    "arguments",
    (
        ("failover", "--help"),
        ("failback", "--help"),
        ("failover", "vm", "--help"),
        ("failover", "tunnel", "--help"),
        ("failback", "vm", "--help"),
        ("failback", "tunnel", "--help"),
    ),
)
def test_resource_scoped_transfer_help_smoke(arguments: tuple[str, ...]) -> None:
    result = CliRunner().invoke(app, list(arguments))

    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output
    assert "Examples:" in result.output


@pytest.mark.parametrize(
    "arguments",
    (
        ("vm-ha-failover",),
        ("vm-ha-failback",),
        ("vm-ha-recover",),
        ("vm-ha-status",),
        ("vm-ha-state",),
        ("status", "--vm-ha-only"),
        ("failover", "legacy-tunnel"),
        ("failback", "legacy-tunnel"),
        ("failover",),
        ("failback",),
    ),
)
def test_removed_or_incomplete_transfer_paths_are_rejected(
    arguments: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(app, list(arguments))

    assert result.exit_code == 2
    assert "Usage:" in result.output


def _static_wizard_input() -> str:
    answers = [
        "tenant-static",
        "project-static",
        "eu-north1",
        "",  # gateway name
        "",  # instance count
        "",  # zone
        "no",  # advanced settings
        "",  # network ID
        "",  # subnet name
        "",  # subnet CIDR
        "",  # subnet prefix
        "auto",
        "",  # local ASN
        "10.20.0.0/16",
        "1",  # connection count
        "onprem-static",
        "cisco",
        "static",
        "192.168.0.0/16, 192.169.0.0/16",
        "1",  # tunnel paths per VM
        "",  # generated tunnel name
        "198.51.100.20",
        "ONPREM_STATIC_PSK",
        "",  # generated APIPA
        "yes",
        "yes",  # write
        "no",  # prep network
    ]
    return "\n".join(answers) + "\n"


def _vm_ha_wizard_input() -> str:
    answers = [
        "tenant-ha",
        "project-ha",
        "eu-north1",
        "",  # gateway name
        "",  # initial instance count
        "",  # zone
        "yes",  # advanced settings
        "",  # platform
        "",  # preset
        "",  # disk size
        "",  # SSH public key path
        "",  # SSH private key path
        "",  # max connections
        "",  # max tunnels
        "",  # DPD interval
        "",  # DPD timeout
        "yes",  # explicitly enable VM-HA
        "",  # cluster ID
        "",  # active node ID
        "",  # active credential directory
        "",  # passive node ID
        "",  # passive credential directory
        "",  # network ID
        "",  # subnet name
        "",  # subnet CIDR
        "",  # subnet prefix
        "auto",
        "",  # local ASN
        "10.30.0.0/16",
        "1",  # connection count
        "ha-static",
        "generic",
        "static",
        "172.20.0.0/16",
        "1",  # one path per VM
        "",  # VM 0 tunnel name
        "198.51.100.30",
        "HA_STATIC_VM0_PSK",
        "",  # APIPA
        "yes",
        "",  # VM 1 tunnel name
        "198.51.100.31",
        "HA_STATIC_VM1_PSK",
        "",  # APIPA
        "yes",
        "yes",  # write
        "no",  # prep network
    ]
    return "\n".join(answers) + "\n"


@pytest.mark.parametrize(
    ("name", "wizard_input", "vm_ha_enabled"),
    [
        ("static-wizard.config.yaml", _static_wizard_input(), False),
        ("vm-ha-wizard.config.yaml", _vm_ha_wizard_input(), True),
    ],
)
def test_create_config_wizard_outputs_validate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    wizard_input: str,
    vm_ha_enabled: bool,
) -> None:
    config_path = tmp_path / name

    result = CliRunner().invoke(
        app,
        ["create-config", str(config_path), "--interactive"],
        input=wizard_input,
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    validated = VPNGatewayConfig.model_validate(payload)
    assert validated.gateway_group.vm_ha is not None
    assert validated.gateway_group.vm_ha.enabled is vm_ha_enabled
    if vm_ha_enabled:
        assert validated.gateway_group.instance_count == 2
        assert {t.gateway_instance_index for t in validated.connections[0].tunnels} == {0, 1}

    if not vm_ha_enabled:
        monkeypatch.setenv("ONPREM_STATIC_PSK", "test-placeholder-value-not-a-credential")
        validation = CliRunner().invoke(app, ["validate-config", str(config_path)])
        assert validation.exit_code == 0, validation.output
        assert "Configuration is valid" in validation.output


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
