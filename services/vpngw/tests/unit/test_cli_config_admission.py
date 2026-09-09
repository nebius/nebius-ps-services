from __future__ import annotations

import copy

import pytest
import yaml
from typer.testing import CliRunner

from nebius_vpngw import cli


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("ha", [False, True])
def test_apply_missing_environment_exits_without_traceback_or_effects(
    monkeypatch, tmp_path, sample_config, dry_run, ha
):
    names = ("VPNGW_TEST_MISSING_PSK_A", "VPNGW_TEST_MISSING_PSK_B")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    sample_config["gateway_group"]["vm_ha"] = {"enabled": ha}
    tunnel = sample_config["connections"][0]["tunnels"][0]
    other = copy.deepcopy(tunnel)
    tunnel["psk"] = "${" + names[0] + "}"
    other["psk"] = "${" + names[1] + "}"
    sample_config["connections"][0]["tunnels"].append(other)
    sample_config["defaults"]["auth"]["psk"] = "test-secret-must-not-be-rendered"
    config = tmp_path / "gateway.config.yaml"
    config.write_text(yaml.safe_dump(sample_config))

    def unexpected(*args, **kwargs):
        pytest.fail("missing environment must stop before locks or deployment")

    for boundary in ("VMHAApplyLock", "_apply_impl", "_ensure_authentication"):
        monkeypatch.setattr(cli, boundary, unexpected)
    arguments = ["apply", "--local-config-file", str(config)]
    if dry_run:
        arguments.append("--dry-run")
    result = CliRunner().invoke(cli.app, arguments)

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Missing environment variables for placeholders" in result.output
    assert all(name in result.output for name in names)
    assert "shell" in result.output
    assert "Traceback" not in result.output
    assert "test-secret-must-not-be-rendered" not in result.output


def test_apply_with_resolved_environment_reaches_implementation(
    monkeypatch, tmp_path, sample_config
):
    monkeypatch.setenv("VPNGW_TEST_RESOLVED_PSK", "test-only-psk")
    sample_config["connections"][0]["tunnels"][0]["psk"] = "${VPNGW_TEST_RESOLVED_PSK}"
    config = tmp_path / "gateway.config.yaml"
    config.write_text(yaml.safe_dump(sample_config))
    calls = []
    monkeypatch.setattr(cli, "_apply_impl", lambda **kwargs: calls.append(kwargs))

    result = CliRunner().invoke(cli.app, ["apply", "-c", str(config), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
