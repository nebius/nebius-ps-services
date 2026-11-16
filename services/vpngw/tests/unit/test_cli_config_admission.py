from __future__ import annotations

import copy
import json
import shlex

import pytest
import yaml
from typer.testing import CliRunner

from nebius_vpngw import cli


@pytest.mark.parametrize("command", ["apply"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "payload",
    [
        b"project_id: [private-configuration-sentinel\n",
        b"\xffprivate-configuration-sentinel",
        b"- private-configuration-sentinel\n",
        b"42\n",
        b"gateway_group:\n  vm_ha:\n    enabled: true\nregion_id: region-one\n",
    ],
    ids=["yaml", "utf8", "sequence", "scalar", "ha-schema"],
)
def test_invalid_configuration_has_shared_guidance_before_effects(
    monkeypatch, tmp_path, command, dry_run, payload
):
    config = tmp_path / "gateway's config.yaml"
    config.write_bytes(payload)

    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid configuration must stop before authentication or deployment")

    for boundary in ("VMHAApplyLock", "_apply_impl", "_ensure_authentication"):
        monkeypatch.setattr(cli, boundary, forbidden)
    args = [command, "-c", str(config), "--region", "region-one", "--output-format", "json"]
    if dry_run:
        args.append("--dry-run")
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 1
    recovery = json.loads(result.stdout)["recovery"]
    assert recovery["reason_code"] == "configuration-invalid"
    assert recovery["next_action"] and recovery["repair_prerequisite"]
    assert shlex.split(recovery["repair_command"]) == [
        "nebius-vpngw",
        "apply",
        "--local-config-file",
        str(config),
        "--region",
        "region-one",
        "--repair",
    ]
    assert "private-configuration-sentinel" not in result.output
    assert config.read_bytes() == payload
    assert sorted(tmp_path.iterdir()) == [config]


@pytest.mark.parametrize("command", ["apply"])
def test_configuration_read_failure_is_classified_without_private_detail(
    monkeypatch, tmp_path, command
):
    config = tmp_path / "gateway.yaml"
    config.write_text("{}\n")

    def denied(*_args, **_kwargs):
        raise PermissionError("private-read-detail")

    monkeypatch.setattr(
        cli,
        "load_local_config" if command == "apply" else "_read_regular_file_snapshot",
        denied,
    )
    result = CliRunner().invoke(cli.app, [command, "-c", str(config), "--output-format", "json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["recovery"]["reason_code"] == "configuration-invalid"
    assert "private-read-detail" not in result.output
    assert config.read_text() == "{}\n"


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


@pytest.mark.parametrize("dry_run", [False, True])
def test_removed_initial_security_groups_fail_before_authentication_and_lock(
    monkeypatch, tmp_path, sample_config, dry_run
):
    sample_config["gateway_group"]["initial_security_group_ids"] = ["sg-example"]
    config = tmp_path / "gateway.yaml"
    config.write_text(yaml.safe_dump(sample_config))

    def forbidden(*args, **kwargs):
        pytest.fail("renamed input must stop before any effect")

    for boundary in ("VMHAApplyLock", "_apply_impl", "_ensure_authentication"):
        monkeypatch.setattr(cli, boundary, forbidden)
    result = CliRunner().invoke(
        cli.app, ["apply", "-c", str(config)] + (["--dry-run"] if dry_run else [])
    )
    assert result.exit_code != 0
    assert "was renamed to gateway_group.security_group_ids" in result.output
