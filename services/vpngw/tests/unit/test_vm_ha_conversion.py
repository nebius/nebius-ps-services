from __future__ import annotations

import copy
import io
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import typer
import yaml
from rich.console import Console
from typer.testing import CliRunner

from nebius_vpngw.cli import (
    _apply_nebius_region_precedence,
    _conditional_publish_text,
    _file_fingerprint,
    _load_config_with_region_override,
    _ordinary_vm_ha_conversion_trust_prerequisite,
    _resolve_vm_ha_effective_config,
    _resolve_vm_ha_region,
    _VMHAEffectiveConfig,
    app,
)
from nebius_vpngw.config_loader import load_local_config, merge_with_peer_configs
from nebius_vpngw.config_wizard import WizardValidationError
from nebius_vpngw.schema import VPNGatewayConfig
from nebius_vpngw.vm_ha_command import VMHACommandResult
from nebius_vpngw.vm_ha_config_wizard import (
    _bounded_name,
    resolve_vm_ha_conversion_source,
    run_vm_ha_conversion_wizard,
)


class _ContextManagedFake:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None

    def ordinary_migration_ssh_imports(self, _spec, *, hostnames, **_kwargs):
        return {hostname: object() for hostname in hostnames}


@pytest.fixture(autouse=True)
def _admit_ordinary_conversion_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing conversion tests exercise behavior after the trust prerequisite."""

    monkeypatch.setattr(
        "nebius_vpngw.cli._ordinary_vm_ha_conversion_trust_prerequisite",
        lambda _path, _source: None,
    )


def test_conversion_requires_ordinary_apply_before_any_candidate_effect(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    reserve = Mock(side_effect=AssertionError("trust prerequisite reached cloud reservation"))

    with patch("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip", reserve):
        result = _resolve_vm_ha_effective_config(
            source_path=source,
            output=None,
            force=False,
            dry_run=False,
            interactive=True,
            region=None,
            ordinary_trust_preflight=_ordinary_vm_ha_conversion_trust_prerequisite,
        )

    assert isinstance(result, VMHACommandResult)
    assert result.reasons == ("ordinary-ssh-trust-required",)
    assert "nebius-vpngw apply" in result.next_action
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()
    reserve.assert_not_called()


def _answer_prompts(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    remaining = iter(answers)

    def fake_prompt(_label: str, *, default=None, **_kwargs):
        answer = next(remaining)
        return default if answer == "" else answer

    monkeypatch.setattr(typer, "prompt", fake_prompt)


@pytest.mark.parametrize(
    ("explicit", "group", "top_level", "expected"),
    (
        ("eu-west1", "eu-north1", "eu-east1", "eu-west1"),
        (None, "eu-north1", "eu-east1", "eu-north1"),
        (None, None, "eu-east1", "eu-east1"),
    ),
)
def test_vm_ha_region_precedence(
    explicit: str | None,
    group: str | None,
    top_level: str | None,
    expected: str,
) -> None:
    source = {
        "region_id": top_level,
        "gateway_group": {"region": group},
    }

    assert _resolve_vm_ha_region(source, explicit_region=explicit) == expected


def test_vm_ha_region_is_required_when_no_source_resolves() -> None:
    with pytest.raises(ValueError, match="region_id must resolve"):
        _resolve_vm_ha_region(
            {"region_id": "${REGION_ID}", "gateway_group": {}},
            explicit_region=None,
        )


@pytest.mark.parametrize(
    ("explicit", "group", "expected_authority"),
    (
        ("${CLI_REGION}", "eu-north1", "--region"),
        (None, "${GROUP_REGION}", "gateway_group.region"),
    ),
)
def test_vm_ha_region_does_not_fall_back_from_unresolved_selected_authority(
    explicit: str | None,
    group: str,
    expected_authority: str,
) -> None:
    with pytest.raises(ValueError, match=expected_authority):
        _resolve_vm_ha_region(
            {
                "region_id": "eu-east1",
                "gateway_group": {"region": group},
            },
            explicit_region=explicit,
        )


def test_region_materializer_synchronizes_both_retained_fields() -> None:
    config = {
        "region_id": "eu-east1",
        "gateway_group": {"region": "eu-north1"},
    }

    assert _apply_nebius_region_precedence(config, explicit_region=None) == "eu-north1"
    assert config["region_id"] == "eu-north1"
    assert config["gateway_group"]["region"] == "eu-north1"


def test_plan_materializes_group_precedence_without_cli_override(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    sample_config["region_id"] = "eu-east1"
    sample_config["gateway_group"]["region"] = "eu-north1"
    source = tmp_path / "group-region.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    loaded = _load_config_with_region_override(source, region=None)
    plan = merge_with_peer_configs(loaded, [])

    assert plan.gateway_group.region == "eu-north1"
    assert loaded["region_id"] == "eu-north1"
    assert loaded["gateway_group"]["region"] == "eu-north1"


def test_vm_ha_missing_region_fails_before_input_or_effects(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    sample_config.pop("region_id", None)
    sample_config["gateway_group"].pop("region", None)
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    destination = tmp_path / "ordinary.vm-ha.config.yaml"

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "-c",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["reasons"] == ["configuration-not-convertible"]
    assert not destination.exists()


def test_vm_ha_explicit_region_satisfies_missing_yaml_region(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    sample_config.pop("region_id", None)
    sample_config["gateway_group"].pop("region", None)
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    result = _resolve_vm_ha_effective_config(
        source_path=source,
        output=None,
        force=False,
        dry_run=True,
        interactive=False,
        region="eu-north1",
    )

    assert isinstance(result, VMHACommandResult)
    assert result.reasons == ("conversion-input-required",)


def test_vm_ha_unresolved_selected_region_fails_before_candidate_or_effects(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "unresolved-region.config.yaml"
    sample_config["region_id"] = "eu-east1"
    sample_config["gateway_group"]["region"] = "${GROUP_REGION}"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    result = _resolve_vm_ha_effective_config(
        source_path=source,
        output=None,
        force=False,
        dry_run=True,
        interactive=False,
        region=None,
    )

    assert isinstance(result, VMHACommandResult)
    assert result.reasons == ("region-unavailable",)
    assert not (tmp_path / "unresolved-region.vm-ha.config.yaml").exists()


def _happy_answers(
    tmp_path: Path,
    *,
    passive_ip: str = "203.0.113.20",
) -> list[str]:
    del tmp_path
    return [
        "",  # member-1 tunnel name
        "",  # PSK environment name
        "",  # APIPA /30
        "yes",  # Nebius uses first host
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
    assert candidate["gateway_group"]["vm_ha"]["members"][0]["node_id"] == ("placeholder-gateway-0")
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
    answers = "\n".join(["", "", "", "yes", "", "yes", "no"]) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip", reserve),
    ):
        result = CliRunner().invoke(
            app,
            ["vm-ha", "-c", str(source), "--region", "eu-north1"],
            input=answers,
        )

    assert result.exit_code == 3, result.output
    reserve.assert_called_once()
    assert reserve.call_args.kwargs["region"] == "eu-north1"
    assert "✓ preparing the passive Nebius public IP." in result.output
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
    answers = "\n".join(["", "", "", "yes", "", "no"]) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip", reserve),
    ):
        result = CliRunner().invoke(
            app,
            ["vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 3, result.output
    reserve.assert_not_called()
    assert source.read_text(encoding="utf-8") == original
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()


def test_command_publishes_mode_0600_and_rerun_is_noop(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    sample_config["region_id"] = "eu-east1"
    sample_config["gateway_group"]["region"] = "eu-north1"
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
                ["vm-ha", "-c", str(source)],
                input=answers,
            )
        finally:
            os.umask(old_umask)

    assert result.exit_code == 1, result.output
    reserve.assert_not_called()
    assert source.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    persisted_candidate = yaml.safe_load(destination.read_text(encoding="utf-8"))
    VPNGatewayConfig.model_validate(persisted_candidate)
    assert persisted_candidate["region_id"] == "eu-north1"
    assert persisted_candidate["gateway_group"]["region"] == "eu-north1"
    with patch.dict(os.environ, {"TUNNEL_1_GW2_PSK": "fixture-new-psk"}):
        loaded = load_local_config(destination)
    assert merge_with_peer_configs(loaded, []).vm_ha is not None
    resolved = _resolve_vm_ha_effective_config(
        source_path=source,
        output=None,
        force=False,
        dry_run=False,
        interactive=False,
        region=None,
    )
    assert isinstance(resolved, _VMHAEffectiveConfig)
    assert resolved.path == destination
    assert resolved.actions == ("candidate-reused",)

    calls: list[str] = []

    class FakeVMManager(_ContextManagedFake):
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
        patch(
            "nebius_vpngw.cli.inspect_managed_vm_ha_credentials",
            return_value=Mock(
                action="create",
                credentials=None,
                approval_record=Mock(
                    return_value={
                        "action": "create",
                        "service_account_name": "nebius-vpn-gw-vm-ha",
                    }
                ),
            ),
        ),
        patch("nebius_vpngw.cli.ensure_managed_vm_ha_credentials") as ensure_credentials,
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch("nebius_vpngw.cli.require_vm_ha_ssh_policy", return_value=object()),
        patch(
            "nebius_vpngw.cli._resolve_vm_ha_agent_artifact",
            return_value=SimpleNamespace(sha256="f" * 64),
        ),
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
    ensure_credentials.assert_not_called()
    ssh_push.assert_not_called()


def test_reserved_ip_is_reported_when_candidate_publication_fails(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    original = yaml.safe_dump(sample_config, sort_keys=False)
    source.write_text(original, encoding="utf-8")
    answers = (
        "\n".join(
            [
                "",
                "",
                "",
                "yes",
                "",
                "yes",
                "yes",
                "198.51.100.20",
                "",
                "yes",
            ]
        )
        + "\n"
    )

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
            ["vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 1, result.output
    reserve.assert_called_once()
    assert source.read_text(encoding="utf-8") == original
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()
    assert "passive-public-ip-reserved" in result.output
    assert "deterministic passive allocation will be resolved and reused" in result.output


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
    answers = "\n".join(["", "", "", "yes", "", "yes"]) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch("nebius_vpngw.cli._file_fingerprint", return_value=None),
        patch("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip") as reserve,
    ):
        result = CliRunner().invoke(
            app,
            ["vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 1, result.output
    reserve.assert_not_called()
    assert "conversion-failed-before-cloud-effects" in result.output
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()


def test_possibly_accepted_passive_reservation_failure_reports_reuse_identity(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordinary.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    answers = "\n".join(["", "", "", "yes", "", "yes"]) + "\n"

    with (
        patch("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", return_value=True),
        patch(
            "nebius_vpngw.cli._reserve_vm_ha_passive_public_ip",
            side_effect=RuntimeError("operation status unavailable"),
        ) as reserve,
    ):
        result = CliRunner().invoke(
            app,
            ["vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 1, result.output
    reserve.assert_called_once()
    assert "passive-allocation-may-exist" in result.output
    assert "deterministic passive allocation will be resolved and reused" in result.output
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
            ["vm-ha", "-c", str(source)],
            input=answers,
        )

    assert result.exit_code == 1, result.output
    assert "source-changed-before-candidate-publication" in result.output
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
                "vm-ha",
                "-c",
                str(source),
                "-o",
                str(destination),
            ],
        )

    assert result.exit_code == 1
    assert source.read_text(encoding="utf-8") == original
    assert "candidate-path-unsafe" in result.output


def test_command_requires_tty_before_auth_or_write(sample_config: dict, tmp_path: Path) -> None:
    source = tmp_path / "ordinary.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    with patch("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip") as reserve:
        result = CliRunner().invoke(app, ["vm-ha", "-c", str(source)])
    assert result.exit_code == 3
    reserve.assert_not_called()
    assert "conversion-input-required" in result.output
    assert not (tmp_path / "ordinary.vm-ha.config.yaml").exists()
