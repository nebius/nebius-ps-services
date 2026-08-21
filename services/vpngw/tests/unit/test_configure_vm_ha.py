from __future__ import annotations

import copy
import io
import os
import stat
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import typer
import yaml
from rich.console import Console
from typer.testing import CliRunner

from nebius_vpngw.cli import _conditional_publish_text, _file_fingerprint, app
from nebius_vpngw.config_loader import load_local_config, merge_with_peer_configs
from nebius_vpngw.config_wizard import WizardValidationError
from nebius_vpngw.schema import VPNGatewayConfig
from nebius_vpngw.vm_ha_config_wizard import (
    _bounded_name,
    resolve_vm_ha_conversion_source,
    run_vm_ha_conversion_wizard,
)


def _answer_prompts(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    remaining = iter(answers)

    def fake_prompt(_label: str, *, default=None, **_kwargs):
        answer = next(remaining)
        return default if answer == "" else answer

    monkeypatch.setattr(typer, "prompt", fake_prompt)


def _credential_directories(tmp_path: Path) -> tuple[str, str]:
    credential_paths = []
    for index in range(2):
        source = tmp_path / f"nebius-credentials-{index}.json"
        source.write_text('{"subject-credentials":{"type":"test"}}', encoding="utf-8")
        source.chmod(0o600)
        credential_paths.append(str(source))
    return credential_paths[0], credential_paths[1]


def _happy_answers(
    tmp_path: Path,
    *,
    passive_ip: str = "203.0.113.20",
) -> list[str]:
    active_credentials, passive_credentials = _credential_directories(tmp_path)
    return [
        "",  # member-1 tunnel name
        "",  # PSK environment name
        "",  # APIPA /30
        "yes",  # Nebius uses first host
        active_credentials,
        passive_credentials,
        passive_ip,
        "yes",  # peer ready
        "198.51.100.20",  # peer public IP
        "",  # expected peer inner IP
        "yes",  # write candidate
    ]


def test_conversion_preserves_raw_source_and_only_adds_member_one(
    sample_config: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = copy.deepcopy(sample_config)
    source["tenant_id"] = "${TENANT_ID}"
    source["project_id"] = "${PROJECT_ID}"
    source["gateway_group"]["name"] = "${GATEWAY_NAME}"
    source["gateway"]["local_asn"] = "${LOCAL_ASN}"
    source["gateway"]["quotas"]["max_tunnels"] = "${MAX_TUNNELS}"
    source["connections"][0]["tunnels"][0]["psk"] = "${OLD_PSK}"
    before = copy.deepcopy(source)
    monkeypatch.setenv("TENANT_ID", "sentinel-tenant-secret")
    monkeypatch.setenv("PROJECT_ID", "sentinel-project-secret")
    monkeypatch.setenv("GATEWAY_NAME", "placeholder-gateway")
    monkeypatch.setenv("LOCAL_ASN", "65000")
    monkeypatch.setenv("MAX_TUNNELS", "32")
    monkeypatch.setenv("OLD_PSK", "sentinel-psk-secret")
    _answer_prompts(monkeypatch, _happy_answers(tmp_path))
    output = io.StringIO()

    result = run_vm_ha_conversion_wizard(
        Console(file=output, force_terminal=False, color_system=None),
        source,
        tmp_path / "candidate.vm-ha.config.yaml",
        reserve_passive_ip=lambda: (_ for _ in ()).throw(
            AssertionError("preallocated input must not authenticate")
        ),
    )

    assert source == before
    assert result.candidate is not None
    candidate = result.candidate
    assert candidate["tenant_id"] == "${TENANT_ID}"
    assert candidate["project_id"] == "${PROJECT_ID}"
    assert candidate["gateway_group"]["name"] == "${GATEWAY_NAME}"
    assert candidate["gateway"]["local_asn"] == "${LOCAL_ASN}"
    assert candidate["gateway"]["quotas"]["max_tunnels"] == "${MAX_TUNNELS}"
    assert candidate["gateway_group"]["vm_ha"]["members"][0]["node_id"] == (
        "placeholder-gateway-0"
    )
    assert candidate["gateway_group"]["instance_count"] == 2
    assert candidate["gateway_group"]["external_ips"] == [[], ["203.0.113.20"]]
    assert candidate["connections"][0]["tunnels"][0] == before["connections"][0]["tunnels"][0]
    member_one = candidate["connections"][0]["tunnels"][1]
    assert member_one["gateway_instance_index"] == 1
    assert member_one["ha_role"] == "active"
    assert member_one["psk"] == "${TUNNEL_1_GW2_PSK}"
    VPNGatewayConfig.model_validate(resolve_vm_ha_conversion_source(candidate))
    assert result.yaml_text is not None
    assert "${TENANT_ID}" in result.yaml_text
    assert "${PROJECT_ID}" in result.yaml_text
    assert "${GATEWAY_NAME}" in result.yaml_text
    assert "${LOCAL_ASN}" in result.yaml_text
    assert "${MAX_TUNNELS}" in result.yaml_text
    assert "${OLD_PSK}" in result.yaml_text
    combined = result.yaml_text + output.getvalue()
    assert "sentinel-tenant-secret" not in combined
    assert "sentinel-project-secret" not in combined
    assert "sentinel-psk-secret" not in combined
    assert "/operator-secrets/" not in output.getvalue()


def test_conversion_structural_guard_rejects_unrelated_change(
    sample_config: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _answer_prompts(monkeypatch, _happy_answers(tmp_path))
    from nebius_vpngw import vm_ha_config_wizard

    original = vm_ha_config_wizard._apply_member_one_tunnels

    def mutate_unrelated(candidate, derived):
        original(candidate, derived)
        candidate["gateway"]["local_asn"] += 1

    monkeypatch.setattr(vm_ha_config_wizard, "_apply_member_one_tunnels", mutate_unrelated)

    with pytest.raises(WizardValidationError, match="Structural guard"):
        run_vm_ha_conversion_wizard(
            Console(file=io.StringIO(), force_terminal=False, color_system=None),
            copy.deepcopy(sample_config),
            tmp_path / "candidate.vm-ha.config.yaml",
            reserve_passive_ip=lambda: "203.0.113.20",
        )


def test_conversion_back_restarts_the_current_section(
    sample_config: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _answer_prompts(monkeypatch, ["b", *_happy_answers(tmp_path)])
    output = io.StringIO()

    result = run_vm_ha_conversion_wizard(
        Console(file=output, force_terminal=False, color_system=None),
        copy.deepcopy(sample_config),
        tmp_path / "candidate.vm-ha.config.yaml",
        reserve_passive_ip=lambda: "203.0.113.20",
    )

    assert result.candidate is not None
    assert "Restarting the passive tunnel parameter section" in output.getvalue()


def test_conversion_covers_every_connection_and_raises_only_required_quota(
    sample_config: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = copy.deepcopy(sample_config)
    second = copy.deepcopy(source["connections"][0])
    second["name"] = "second-peer"
    second["remote_prefixes"] = ["198.18.0.0/15"]
    second["tunnels"][0].update(
        {
            "name": "tunnel-2",
            "remote_public_ip": "198.51.100.11",
            "inner_cidr": "169.254.19.0/30",
            "inner_local_ip": "169.254.19.1",
            "inner_remote_ip": "169.254.19.2",
        }
    )
    source["connections"].append(second)
    source["gateway"]["quotas"]["max_tunnels"] = 2
    active_credentials, passive_credentials = _credential_directories(tmp_path)
    _answer_prompts(
        monkeypatch,
        [
            "",
            "",
            "",
            "yes",
            "",
            "",
            "",
            "yes",
            active_credentials,
            passive_credentials,
            "203.0.113.20",
            "yes",
            "198.51.100.20",
            "",
            "198.51.100.21",
            "",
            "yes",
        ],
    )

    result = run_vm_ha_conversion_wizard(
        Console(file=io.StringIO(), force_terminal=False, color_system=None),
        source,
        tmp_path / "candidate.vm-ha.config.yaml",
        reserve_passive_ip=lambda: "203.0.113.20",
    )

    assert result.candidate is not None
    assert [len(connection["tunnels"]) for connection in result.candidate["connections"]] == [
        2,
        2,
    ]
    assert result.candidate["gateway"]["quotas"]["max_tunnels"] == 4
    plan = merge_with_peer_configs(result.candidate, [])
    assert plan.vm_ha is not None
    assert {member.instance_index for member in plan.vm_ha.members} == {0, 1}


def test_peer_not_ready_reserves_passive_ip_without_writing_candidate(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    original = yaml.safe_dump(sample_config, sort_keys=False)
    source.write_text(original, encoding="utf-8")
    destination = tmp_path / "ordinary.vm-ha.config.yaml"
    reserve = Mock(return_value="203.0.113.20")
    active_credentials, passive_credentials = _credential_directories(tmp_path)
    answers = "\n".join(
        ["", "", "", "yes", active_credentials, passive_credentials, "", "yes", "no"]
    ) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip", reserve),
    ):
        result = CliRunner().invoke(
            app,
            ["configure-vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 0, result.output
    reserve.assert_called_once()
    assert source.read_text(encoding="utf-8") == original
    assert not destination.exists()
    assert "No candidate was written; rerun when peer details are ready" in result.output
    assert "203.0.113.20 remains allocated" in result.output
    assert "apply --local-config-file" not in result.output


def test_declining_passive_reservation_never_authenticates(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    original = yaml.safe_dump(sample_config, sort_keys=False)
    source.write_text(original, encoding="utf-8")
    reserve = Mock()
    active_credentials, passive_credentials = _credential_directories(tmp_path)
    answers = "\n".join(
        ["", "", "", "yes", active_credentials, passive_credentials, "", "no"]
    ) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip", reserve),
    ):
        result = CliRunner().invoke(
            app,
            ["configure-vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 0, result.output
    reserve.assert_not_called()
    assert source.read_text(encoding="utf-8") == original
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()


def test_command_publishes_mode_0600_and_rerun_is_noop(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    original = yaml.safe_dump(sample_config, sort_keys=False)
    source.write_text(original, encoding="utf-8")
    destination = tmp_path / "ordinary.vm-ha.config.yaml"
    answers = "\n".join(_happy_answers(tmp_path)) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip") as reserve,
        patch("nebius_vpngw.cli.os.fchmod", wraps=os.fchmod),
    ):
        old_umask = os.umask(0)
        try:
            result = CliRunner().invoke(
                app,
                ["configure-vm-ha", "-c", str(source)],
                input=answers,
            )
        finally:
            os.umask(old_umask)

    assert result.exit_code == 0, result.output
    reserve.assert_not_called()
    assert source.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    VPNGatewayConfig.model_validate(yaml.safe_load(destination.read_text(encoding="utf-8")))
    with patch.dict(os.environ, {"TUNNEL_1_GW2_PSK": "fixture-new-psk"}):
        loaded = load_local_config(destination)
    assert merge_with_peer_configs(loaded, []).vm_ha is not None
    assert "apply --local-config-file" in result.output
    assert "--dry-run" in result.output

    with patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True):
        rerun = CliRunner().invoke(
            app,
            ["configure-vm-ha", "-c", str(source)],
        )
    assert rerun.exit_code == 0, rerun.output
    assert "already up to date" in rerun.output

    calls: list[str] = []

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_vm_ha_members(self, spec):
            calls.append("discover")
            return {"nebius-vpn-gw-0": "203.0.113.10"}

        def verify_vm_ha_existing_identities(self, existing, **kwargs) -> None:
            calls.append("verify-existing")

        def check_changes(self, spec):
            calls.append("check-changes")
            return []

        def ensure_group(self, *args, **kwargs):
            raise AssertionError("dry-run must not provision")

    with (
        patch.dict(os.environ, {"TUNNEL_1_GW2_PSK": "fixture-new-psk"}),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch("nebius_vpngw.cli.require_vm_ha_ssh_policy", return_value=object()),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush") as ssh_push,
    ):
        dry_run = CliRunner().invoke(
            app,
            ["apply", "--local-config-file", str(destination), "--dry-run"],
        )

    assert dry_run.exit_code == 0, dry_run.output
    assert "Ordinary gateway to VM-HA migration plan" in dry_run.output
    assert "no lifecycle, cloud, route, or host state was changed" in dry_run.output
    assert calls == ["discover", "verify-existing", "check-changes"]
    ssh_push.assert_not_called()


def test_reserved_ip_is_reported_when_candidate_publication_fails(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    original = yaml.safe_dump(sample_config, sort_keys=False)
    source.write_text(original, encoding="utf-8")
    active_credentials, passive_credentials = _credential_directories(tmp_path)
    answers = "\n".join(
        [
            "",
            "",
            "",
            "yes",
            active_credentials,
            passive_credentials,
            "",
            "yes",
            "yes",
            "198.51.100.20",
            "",
            "yes",
        ]
    ) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch(
            "nebius_vpngw.cli._reserve_vm_ha_passive_public_ip",
            return_value="203.0.113.20",
        ) as reserve,
        patch("nebius_vpngw.cli.os.link", side_effect=OSError("publish failed")),
    ):
        result = CliRunner().invoke(
            app,
            ["configure-vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 1, result.output
    reserve.assert_called_once()
    assert source.read_text(encoding="utf-8") == original
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()
    assert "203.0.113.20 remains allocated and will be reused" in result.output
    assert "rollback is claimed" in result.output


def test_conditional_publication_does_not_overwrite_a_racing_destination(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "candidate.config.yaml"
    destination.write_text("expected\n", encoding="utf-8")
    destination.chmod(0o600)
    expected = _file_fingerprint(destination)
    assert expected is not None
    real_rename = os.rename

    def replace_before_quarantine(source: Path, target: Path) -> None:
        destination.write_text("racing-writer\n", encoding="utf-8")
        destination.chmod(0o600)
        real_rename(source, target)

    with (
        patch("nebius_vpngw.cli.os.rename", side_effect=replace_before_quarantine),
        pytest.raises(OSError, match="changed file was restored"),
    ):
        _conditional_publish_text(
            destination,
            "wizard-candidate\n",
            expected_fingerprint=expected,
        )

    assert destination.read_text(encoding="utf-8") == "racing-writer\n"
    assert not (tmp_path / ".candidate.config.yaml.conditional-publication").exists()


@pytest.mark.parametrize("recovery_kind", ["directory", "symlink"])
def test_conditional_publication_never_trusts_preexisting_recovery_state(
    tmp_path: Path,
    recovery_kind: str,
) -> None:
    destination = tmp_path / "candidate.config.yaml"
    staging = tmp_path / ".candidate.config.yaml.conditional-publication"
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / "expected").write_text("attacker-controlled\n", encoding="utf-8")
    if recovery_kind == "directory":
        staging.mkdir()
        (staging / "expected").write_text("attacker-controlled\n", encoding="utf-8")
    else:
        staging.symlink_to(attacker, target_is_directory=True)

    with pytest.raises(OSError, match="recovery|interrupted"):
        _conditional_publish_text(
            destination,
            "wizard-candidate\n",
            expected_fingerprint=None,
        )

    assert not destination.exists()


def test_bounded_vm_ha_names_do_not_collide_after_truncation() -> None:
    shared_prefix = "a" * 63
    first = f"{shared_prefix}b"
    second = f"{shared_prefix}c"

    assert _bounded_name(first, "-0") != _bounded_name(second, "-0")
    assert _bounded_name(first, "-ha") != _bounded_name(second, "-ha")
    assert len(_bounded_name(first, "-0")) <= 64


def test_source_change_before_passive_reservation_blocks_authentication(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    active_credentials, passive_credentials = _credential_directories(tmp_path)
    answers = "\n".join(
        ["", "", "", "yes", active_credentials, passive_credentials, "", "yes"]
    ) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch("nebius_vpngw.cli._file_fingerprint", return_value=None),
        patch("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip") as reserve,
    ):
        result = CliRunner().invoke(
            app,
            ["configure-vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 1, result.output
    reserve.assert_not_called()
    assert "before cloud preparation" in result.output
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()


def test_possibly_accepted_passive_reservation_failure_reports_reuse_identity(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    active_credentials, passive_credentials = _credential_directories(tmp_path)
    answers = "\n".join(
        ["", "", "", "yes", active_credentials, passive_credentials, "", "yes"]
    ) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch(
            "nebius_vpngw.cli._reserve_vm_ha_passive_public_ip",
            side_effect=RuntimeError("operation status unavailable"),
        ) as reserve,
    ):
        result = CliRunner().invoke(
            app,
            ["configure-vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 1, result.output
    reserve.assert_called_once()
    assert "nebius-vpn-gw-1-eth0-ip" in result.output
    assert "may remain allocated" in result.output
    assert "rerun to resolve and reuse it" in result.output
    assert "No rollback is claimed" in result.output
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()


def test_source_change_during_wizard_blocks_candidate_publication(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    answers = "\n".join(_happy_answers(tmp_path)) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch("nebius_vpngw.cli._file_fingerprint", return_value=None),
    ):
        result = CliRunner().invoke(
            app,
            ["configure-vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 1, result.output
    assert "source configuration changed" in result.output.lower()
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()


@pytest.mark.parametrize("alias_kind", ["same", "symlink", "hardlink"])
def test_command_rejects_source_alias_destinations(
    sample_config: dict,
    tmp_path: Path,
    alias_kind: str,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    original = yaml.safe_dump(sample_config, sort_keys=False)
    source.write_text(original, encoding="utf-8")
    destination = source
    if alias_kind == "symlink":
        destination = tmp_path / "candidate.config.yaml"
        destination.symlink_to(source)
    elif alias_kind == "hardlink":
        destination = tmp_path / "candidate.config.yaml"
        os.link(source, destination)

    with patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True):
        result = CliRunner().invoke(
            app,
            [
                "configure-vm-ha",
                "-c",
                str(source),
                "-o",
                str(destination),
            ],
        )

    assert result.exit_code == 1
    assert source.read_text(encoding="utf-8") == original
    assert "candidate" in result.output.lower() or "symbolic link" in result.output.lower()


def test_command_requires_tty_before_auth_or_write(sample_config: dict, tmp_path: Path) -> None:
    source = tmp_path / "ordinary.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    with patch("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip") as reserve:
        result = CliRunner().invoke(app, ["configure-vm-ha", "-c", str(source)])
    assert result.exit_code == 1
    reserve.assert_not_called()
    assert "requires an interactive terminal" in result.output
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()
