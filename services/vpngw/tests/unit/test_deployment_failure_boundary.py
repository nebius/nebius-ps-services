import json
import shlex
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_vm_ha_command import ha_apply_admission  # noqa: F401
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.deploy.recovery import Invocation, advice


@pytest.mark.parametrize(
    "command,output_format", [("apply", "json"), ("apply", "JSON"), ("apply", "Json")]
)
def test_parser_failure_has_structured_recovery_and_original_exit(command, output_format, tmp_path):
    path = tmp_path / "missing ' config.yaml"
    result = CliRunner().invoke(
        cli.app,
        [command, "-c", str(path), "--region", "region-one", "--output-format", output_format],
    )
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    recovery = payload["recovery"]
    assert recovery["reason_code"] == "invalid-arguments"
    assert shlex.split(recovery["repair_command"]) == [
        "nebius-vpngw",
        "apply",
        "--local-config-file",
        str(path),
        "--region",
        "region-one",
        "--repair",
    ]
    assert recovery["repair_prerequisite"]


@pytest.mark.parametrize("command", ["apply"])
def test_help_is_not_a_deployment_failure(command):
    result = CliRunner().invoke(cli.app, [command, "--help"])
    assert result.exit_code == 0
    assert "Repair prerequisite:" not in result.output


def test_pair_parser_recovery_preserves_manifest_and_profile_owner(tmp_path):
    path = tmp_path / "pair's config" / "pair.json"
    result = CliRunner().invoke(cli.app, ["pair", "apply", str(path)])
    assert result.exit_code == 2
    recovery = json.loads(result.stdout)["recovery"]
    assert shlex.split(recovery["repair_command"]) == [
        "nebius-vpngw",
        "pair",
        "apply",
        str(path),
        "--repair",
    ]
    assert recovery["reason_code"] == "invalid-arguments"
    help_result = CliRunner().invoke(cli.app, ["pair", "apply", "--help"])
    assert help_result.exit_code == 0 and "Repair prerequisite:" not in help_result.stdout


def test_pair_child_reason_is_retained_without_copying_untrusted_text(tmp_path):
    from nebius_vpngw.deploy.recovery import RegionalFailure

    context = Invocation("pair apply", tmp_path / "pair.json")
    error = RegionalFailure(
        "b", {"recovery": {"reason_code": "dns-unavailable", "reason": "private-child-text"}}
    )
    recovery = advice(context, error)
    assert recovery.reason_code == "dns-unavailable"
    assert recovery.reason.startswith("Region b:")
    assert "private-child-text" not in recovery.text()


@pytest.mark.parametrize("member", [1, 2])
@pytest.mark.parametrize(
    "cause,code",
    [
        (socket.gaierror("private-dns"), "dns-unavailable"),
        (TimeoutError("private-timeout"), "operation-timeout"),
    ],
)
def test_pair_preserves_member_and_classified_preparation_cause(member, cause, code):
    from nebius_vpngw.deploy.recovery import RegionalFailure

    failure = cli._VMHAAgentPackagePreparationFailed(
        member, cli._vm_ha_package_failure_reason(cause)
    )
    failure.__cause__ = cause
    child = advice(Invocation("apply"), failure)
    assert child.cause_code == code
    parent = advice(Invocation("pair apply"), RegionalFailure("a", {"recovery": child.mapping()}))
    assert parent.reason_code == f"package-preparation-failed-member-{member}"
    assert parent.cause_code == code
    assert parent.reason.startswith(f"Region a: Package preparation failed on member {member}.")
    assert parent.repair_prerequisite == child.repair_prerequisite
    assert "private-" not in parent.text()


def test_unknown_controller_predicate_stays_unknown_and_redacted():
    recovery = advice(Invocation("apply"), codes=("controller-observation-private-value",))
    assert recovery.reason_code == "unknown-failure"
    assert "private-value" not in recovery.text()


@pytest.mark.parametrize("command", ["apply", "pair apply"])
@pytest.mark.parametrize(
    "code",
    [
        "predecessor-evidence-invalid",
        "predecessor-package-unproven",
        "predecessor-not-settled",
        "predecessor-infrastructure-changed",
        "predecessor-credential-unproven",
    ],
)
def test_known_settlement_blocker_has_sanitized_action_and_repair(command, code):
    from nebius_vpngw.deploy.recovery import RegionalFailure, ReplanRejected

    failure = ReplanRejected("private provider or guest evidence", code=code)
    recovery = advice(Invocation(command), failure)
    if command == "pair apply":
        recovery = advice(
            Invocation(command), RegionalFailure("b", {"recovery": recovery.mapping()})
        )
    assert recovery.reason_code == code
    assert recovery.next_action and recovery.repair_prerequisite
    assert shlex.split(recovery.repair_command)[-1] == "--repair"
    assert "private provider" not in recovery.text()
    assert "cause is unknown" not in recovery.text()


@pytest.mark.parametrize("command", ["apply"])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_settled_predecessor_guidance_allows_direct_confirmed_repair(
    monkeypatch, tmp_path, command, output_format
):
    from nebius_vpngw.deploy.recovery import PredecessorSettlementRequired

    path = tmp_path / "gateway's config.yaml"
    path.touch()

    def require_settlement(*_args, **_kwargs):
        raise PredecessorSettlementRequired()

    boundary = "load_local_config" if command == "apply" else "_resolve_vm_ha_effective_config"
    monkeypatch.setattr(cli, boundary, require_settlement)
    project_args = ["--project-id", "selected-project"] if command == "apply" else []
    result = CliRunner().invoke(
        cli.app,
        [
            command,
            "-c",
            str(path),
            *project_args,
            "--region",
            "selected-region",
            "--output-format",
            output_format,
        ],
    )
    assert result.exit_code == 1
    if output_format == "json":
        recovery = json.loads(result.stdout)["recovery"]
        assert recovery["reason_code"] == "predecessor-settlement-required"
        guidance = recovery["repair_prerequisite"]
        repair_command = recovery["repair_command"]
    else:
        assert result.stdout.count("Reason:") == 1
        assert result.stdout.count("Repair prerequisite:") == 1
        guidance = result.stdout
        repair_command = next(
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip().startswith("nebius-vpngw apply ")
        )
    assert "Repair handles predecessor retirement after confirmation" in guidance
    assert "ownership and prior request outcomes must be verifiable" in guidance
    assert shlex.split(repair_command) == [
        "nebius-vpngw",
        "apply",
        "--local-config-file",
        str(path),
        *project_args,
        "--region",
        "selected-region",
        "--repair",
    ]


def test_regional_settlement_guidance_keeps_repair_owned_retirement():
    from nebius_vpngw.deploy.recovery import PredecessorSettlementRequired, RegionalFailure

    child = advice(Invocation("apply"), PredecessorSettlementRequired())
    parent = advice(Invocation("pair apply"), RegionalFailure("b", {"recovery": child.mapping()}))
    assert parent.repair_prerequisite == child.repair_prerequisite
    assert "Repair handles predecessor retirement after confirmation" in parent.repair_prerequisite
    assert "ownership and prior request outcomes must be verifiable" in parent.repair_prerequisite
    assert shlex.split(parent.repair_command)[-1] == "--repair"
    assert parent.destroy_command is None


def test_pair_resume_keeps_valid_timeout_and_drops_rejected_approval():
    from nebius_vpngw.deployment_cli import invocation

    context = invocation(
        ["pair", "apply", "pair.json", "--approve-a", "a" * 64, "--timeout", "900"]
    )
    command = shlex.split(advice(context, codes=("approval-state-changed",)).resume_command)
    assert command == ["nebius-vpngw", "pair", "apply", "pair.json", "--timeout", "900"]


def test_unknown_failure_is_redacted_and_reported_once(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.touch()
    monkeypatch.setattr(
        cli,
        "load_local_config",
        lambda _: (_ for _ in ()).throw(LookupError("secret-sentinel-do-not-echo")),
    )
    result = CliRunner().invoke(cli.app, ["apply", "-c", str(path)])
    assert result.exit_code == 1
    assert "secret-sentinel" not in result.output
    assert result.output.count("Reason:") == 1
    assert "cause is unknown" in result.output
    assert "--repair" in result.output


def test_attached_short_path_and_operation_selectors_are_preserved():
    from nebius_vpngw.deployment_cli import invocation

    context = invocation(
        ["apply", "-codd path.yaml", "--standby-auto-healing", "enabled", "--region=r1"]
    )
    recovery = advice(context, TimeoutError())
    assert shlex.split(recovery.resume_command)[-4:] == [
        "--region",
        "r1",
        "--standby-auto-healing",
        "enabled",
    ]
    assert "odd path.yaml" in shlex.split(recovery.repair_command)
    assert "--standby-auto-healing" not in recovery.repair_command


def test_repair_recommendation_never_repeats_incompatible_dry_run():
    context = Invocation("apply", repair=True, resume_options=("--dry-run",))
    assert "--dry-run" not in advice(context).repair_command
    assert "--dry-run" not in advice(context).resume_command


@pytest.mark.parametrize(
    "reason",
    ["approval-digest-stale-or-incorrect", "approval-state-changed", "approval-not-applicable"],
)
def test_rejected_approval_is_not_repeated_in_resume_command(reason):
    context = Invocation(
        "apply", resume_options=("--approve", "a" * 64, "--standby-auto-healing", "enabled")
    )
    recovery = advice(context, codes=(reason,))
    assert recovery.reason_code != "unknown-failure"
    assert "--approve" not in shlex.split(recovery.resume_command)
    assert shlex.split(recovery.resume_command)[-2:] == ["--standby-auto-healing", "enabled"]


def test_noninteger_exit_payload_is_redacted_with_native_failure_status(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.touch()
    monkeypatch.setattr(cli, "load_local_config", lambda _: {})
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: SimpleNamespace())
    monkeypatch.setattr(
        cli, "_apply_impl", lambda **_: (_ for _ in ()).throw(SystemExit("private-exit-payload"))
    )
    result = CliRunner().invoke(cli.app, ["apply", "-c", str(path), "--output-format", "json"])
    assert result.exit_code == 1
    assert "private-exit-payload" not in result.output
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "blocked"  # Failure before any effects started.
    assert payload["recovery"]["reason_code"] == "unknown-failure"


def test_parser_never_reflects_invalid_argument_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.touch()
    result = CliRunner().invoke(
        cli.app, ["apply", "-c", str(path), "--standby-auto-healing", "secret-sentinel"]
    )
    assert result.exit_code == 2
    # An invalid selector must not be reflected in the diagnostic resume command.
    assert "secret-sentinel" not in result.output


@pytest.mark.parametrize("structured", [False, True])
def test_interrupted_apply_publishes_recovery_before_pair_report(monkeypatch, tmp_path, structured):
    from nebius_vpngw.deploy import pair_phases

    path = tmp_path / "config.yaml"
    path.touch()
    monkeypatch.setattr(cli, "load_local_config", lambda _: {})
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: SimpleNamespace())
    monkeypatch.setattr(cli, "_apply_impl", lambda **_: (_ for _ in ()).throw(KeyboardInterrupt()))
    reports = []
    monkeypatch.setattr(pair_phases, "report", lambda payload: reports.append(payload))
    result = CliRunner().invoke(
        cli.app, ["apply", "-c", str(path), *(["--output-format", "json"] if structured else [])]
    )
    assert result.exit_code != 0
    assert len(reports) == 1
    assert reports[0]["recovery"]["reason_code"] == "interrupted"
    if structured:
        assert json.loads(result.stdout) == json.loads(json.dumps(reports[0]))
    else:
        assert result.stdout.count("Reason:") == 1


def test_repair_uses_config_without_redundant_resolved_selectors(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.touch()
    monkeypatch.setattr(cli, "load_local_config", lambda _: {"region_id": "wrong"})
    monkeypatch.setattr(
        cli,
        "merge_with_peer_configs",
        lambda *_: SimpleNamespace(gateway_group=SimpleNamespace(region="correct", name="")),
    )
    monkeypatch.setattr(
        cli, "_apply_impl", lambda **_: (_ for _ in ()).throw(RuntimeError("private"))
    )
    result = CliRunner().invoke(cli.app, ["apply", "-c", str(path), "--output-format", "json"])
    recovery = json.loads(result.stdout)["recovery"]
    assert "--region" not in recovery["repair_command"]
    assert "wrong" not in recovery["repair_command"]


@pytest.mark.usefixtures("ha_apply_admission")
def test_conversion_unknown_exception_uses_authoritative_generated_config(monkeypatch, tmp_path):
    source, target = tmp_path / "single.yaml", tmp_path / "ha.yaml"
    source.touch()
    target.touch()
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_: cli._VMHAEffectiveConfig(target, ()),
    )
    monkeypatch.setattr(
        cli,
        "_inspect_vm_ha_status_with_region",
        lambda *_a, **_k: (_ for _ in ()).throw(LookupError("private")),
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "apply",
            "--convert-to-ha",
            "-c",
            str(source),
            "--output",
            str(target),
            "--output-format",
            "json",
        ],
    )
    recovery = json.loads(result.stdout)["recovery"]
    assert str(target) in shlex.split(recovery["repair_command"])
    assert str(source) not in recovery["repair_command"]
    assert "--output " not in recovery["resume_command"]


@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize(
    "reason",
    [
        "candidate-conflicts-with-source",
        "candidate-permissions-not-private",
        "ordinary-ssh-trust-required",
        "conversion-input-required",
        "peer-input-required",
        "passive-public-ip-required",
    ],
)
@pytest.mark.usefixtures("ha_apply_admission")
def test_conversion_prerequisite_keeps_source_repair_and_conversion_resume(
    monkeypatch,
    tmp_path,
    output_format,
    reason,
):
    source, target = tmp_path / "single's config.yaml", tmp_path / "unpublished HA.yaml"
    source.touch()
    prerequisite = cli._vm_ha_action_required(
        config_path=target,
        classification=cli.VMHACommandClassification.CONVERSION_REQUIRED,
        health=cli.VMHACommandHealth.NOT_CONFIGURED,
        reason=reason,
        next_action="Resolve the conversion prerequisite and retry.",
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion", lambda **_: prerequisite
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "apply",
            "--convert-to-ha",
            "-c",
            str(source),
            "--output",
            str(target),
            "--region",
            "region-one",
            "--output-format",
            output_format,
        ],
    )
    assert result.exit_code == 1
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["ha"]["outcome"] == "action-required"
        assert payload["ha"]["effective_config_file"] == str(target)
        recovery = payload["recovery"]
        assert recovery["reason_code"] == reason
        assert "unknown" not in recovery["reason"]
        assert recovery["repair_prerequisite"]
        assert shlex.split(recovery["repair_command"]) == [
            "nebius-vpngw",
            "apply",
            "--local-config-file",
            str(source),
            "--region",
            "region-one",
            "--repair",
        ]
        resume = shlex.split(recovery["resume_command"])
        if reason == "candidate-permissions-not-private":
            assert resume.pop() == "--force"
        assert resume[-5:] == [
            "--region",
            "region-one",
            "--convert-to-ha",
            "--output",
            str(target),
        ]
    else:
        assert result.stdout.count("Reason:") == 1
        assert "--repair" in result.stdout
        assert "Repair prerequisite:" in result.stdout
    assert not target.exists()


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_conflicting_conversion_candidate_public_failure_never_recommends_foreign_config(
    monkeypatch,
    tmp_path,
    sample_config,
    output_format,
):
    import yaml

    source, target = tmp_path / "single.yaml", tmp_path / "foreign.yaml"
    source.write_text(yaml.safe_dump(sample_config))
    target.write_text("foreign: true\n")
    before = (source.read_bytes(), target.read_bytes())
    monkeypatch.setattr(cli, "_ordinary_vm_ha_conversion_trust_prerequisite", lambda *_: None)
    monkeypatch.setattr(cli, "run_vm_ha_conversion_wizard", lambda **_: pytest.fail("No wizard"))
    result = CliRunner().invoke(
        cli.app,
        [
            "apply",
            "--convert-to-ha",
            "-c",
            str(source),
            "--output",
            str(target),
            "--force",
            "--output-format",
            output_format,
        ],
    )
    assert result.exit_code == 1
    if output_format == "json":
        recovery = json.loads(result.stdout)["recovery"]
        assert recovery["reason_code"] == "candidate-conflicts-with-source"
        assert shlex.split(recovery["repair_command"])[3:] == [
            str(source),
            "--repair",
        ]
    else:
        assert "candidate-conflicts-with-source" in result.stdout
        assert (
            "  nebius-vpngw apply --local-config-file " + str(source) + " --repair" in result.stdout
        )
    assert (source.read_bytes(), target.read_bytes()) == before


@pytest.mark.parametrize(
    "reason", ["operator-declined-approval", "conversion-cancelled", "approval-required"]
)
@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_approval_or_cancellation_does_not_offer_failure_repair(
    monkeypatch, tmp_path, reason
):
    source = tmp_path / "single.yaml"
    source.touch()
    result = cli._vm_ha_action_required(
        config_path=source,
        classification=cli.VMHACommandClassification.VM_HA_REQUIRED,
        health=cli.VMHACommandHealth.NOT_CONFIGURED,
        reason=reason,
        next_action="Review the plan when ready.",
    )
    monkeypatch.setattr("nebius_vpngw.deploy.ha_convergence.resolve_conversion", lambda **_: result)
    rendered = CliRunner().invoke(cli.app, ["apply", "-c", str(source), "--output-format", "json"])
    assert rendered.exit_code == (1 if reason == "approval-required" else 0)
    assert "recovery" not in json.loads(rendered.stdout)


@pytest.mark.parametrize(
    "predicate",
    [
        "desired-state-changed",
        "artifact-changed",
        "checkpoint-binding-changed",
        "approval-state-changed",
        "command-execution-changed",
        "package-parent-changed",
        "durable-intent-changed",
    ],
)
def test_preparation_causes_have_specific_recovery(predicate):
    from nebius_vpngw.deploy.apply_diagnostics import PreparationBlocked

    result = advice(Invocation("apply"), PreparationBlocked(predicate))
    assert result.reason_code == predicate
    assert "unknown" not in result.reason
    assert result.repair_prerequisite


@pytest.mark.parametrize(
    "error,code",
    [
        (socket.gaierror("private"), "dns-unavailable"),
        (TimeoutError("private"), "operation-timeout"),
        (LookupError("private"), "unknown-failure"),
    ],
)
def test_bounded_error_classification(error, code):
    recovery = advice(Invocation("apply", Path("test.yaml")), error)
    assert recovery.reason_code == code
    assert "private" not in recovery.text()


@pytest.mark.parametrize("exit_code", [1, 2, 3])
def test_pair_child_keeps_returned_exit_and_cleans_up(monkeypatch, exit_code):
    import typer.main

    from nebius_vpngw import pair_worker
    from nebius_vpngw.deploy import pair_phases

    child = SimpleNamespace(
        config=Path("child.yaml"),
        save=lambda: None,
        request={"repair": False, "approval": None, "inspect_repair": False, "lock_fd": 123},
        record={"result": None},
    )
    monkeypatch.setattr(pair_worker, "Child", lambda _: child)
    monkeypatch.setattr(
        typer.main, "get_command", lambda _: SimpleNamespace(main=lambda *_a, **_k: exit_code)
    )
    closed = []
    monkeypatch.setattr(pair_worker.os, "close", closed.append)
    with pytest.raises(SystemExit) as result:
        pair_worker.main(Path("request"))
    assert result.value.code == exit_code
    assert child.record["phase"] == "failed"
    assert closed == [123]
    assert pair_phases.CURRENT.get() is None


def test_pair_readiness_failure_invalidates_success_but_preserves_effects(monkeypatch):
    import typer.main

    from nebius_vpngw import pair_readiness, pair_worker
    from nebius_vpngw.deploy import pair_phases

    child = SimpleNamespace(
        config=Path("child.yaml"),
        save=lambda: None,
        deadline=0,
        request={"repair": False, "approval": None, "inspect_repair": False, "lock_fd": 123},
        record={
            "result": {
                "outcome": "applied",
                "completed": ["runtime"],
                "authority": "ready",
                "configuration": "ready",
            }
        },
    )
    monkeypatch.setattr(pair_worker, "Child", lambda _: child)
    monkeypatch.setattr(
        typer.main, "get_command", lambda _: SimpleNamespace(main=lambda *_a, **_k: 0)
    )

    def failed(_):
        raise pair_readiness.ReadinessFailure("verify-traffic", "encrypted-route-unavailable")

    monkeypatch.setattr(pair_readiness, "observe", failed)
    closed = []
    monkeypatch.setattr(pair_worker.os, "close", closed.append)
    with pytest.raises(SystemExit) as result:
        pair_worker.main(Path("request"))
    assert result.value.code == 1
    report = child.record["result"]
    assert report["outcome"] == "failed"
    assert report["completed"] == ["runtime"]
    assert report["authority"] == report["configuration"] == report["connectivity"] == "unknown"
    assert report["inhibition_retained"] is None
    assert report["failure"] == child.record["first_readiness_failure"]
    assert report["recovery"]["reason_code"] == "verification-failed"
    assert closed == [123] and pair_phases.CURRENT.get() is None


def test_configuration_diagnostic_omits_values_and_unknown_field_names(tmp_path, sample_config):
    import yaml

    path = tmp_path / "config.yaml"
    sample_config["gateway_group"]["instance_count"] = "private-value-sentinel"
    sample_config["private-key-sentinel"] = "private-value-sentinel"
    path.write_text(yaml.safe_dump(sample_config))
    result = CliRunner().invoke(cli.app, ["apply", "-c", str(path), "--output-format", "json"])
    assert result.exit_code == 1
    recovery = json.loads(result.stdout)["recovery"]
    assert "gateway_group.instance_count" in recovery["reason"]
    assert "private-value-sentinel" not in result.output
    assert "private-key-sentinel" not in result.output


def test_unsupported_gateway_fields_retain_safe_names_only(tmp_path, sample_config):
    import yaml

    path = tmp_path / "config.yaml"
    sample_config["gateway"]["ssh_allowed_cidrs"] = ["private-value-sentinel"]
    sample_config["gateway"]["private-key-sentinel"] = "private-value-sentinel"
    path.write_text(yaml.safe_dump(sample_config))
    result = CliRunner().invoke(cli.app, ["apply", "-c", str(path), "--output-format", "json"])
    assert result.exit_code == 1
    recovery = json.loads(result.stdout)["recovery"]
    assert "ssh_allowed_cidrs" in recovery["reason"]
    assert "private-value-sentinel" not in result.output
    assert "private-key-sentinel" not in result.output


@pytest.mark.parametrize("owned", [False, True])
def test_repair_replacement_advice_requires_owned_existing_targets(owned, tmp_path):
    from nebius_vpngw.deploy.recovery import Invocation, RepairBlocked, advice

    path = tmp_path / "gateway's config.yaml"
    context = Invocation(
        "apply", path, project="project-selected", region="region-selected", repair=True
    )
    recovery = advice(context, RepairBlocked("repair-ssh-unavailable", ownership_established=owned))
    assert recovery.reason_code == "repair-ssh-unavailable"
    assert bool(recovery.destroy_command) == owned
    assert bool(recovery.fresh_apply_command) == owned
    if owned:
        import shlex

        command = shlex.split(recovery.destroy_command)
        assert command == [
            "nebius-vpngw",
            "destroy",
            "--local-config-file",
            str(path),
            "--project-id",
            "project-selected",
            "--region",
            "region-selected",
        ]
        assert "--repair" not in shlex.split(recovery.fresh_apply_command)
        assert "deletes gateway VMs" in recovery.text()
        assert "normal apply is not a prerequisite" in recovery.text()
        assert "Last-resort VM rebuild" in recovery.text()
        assert recovery.text().count(recovery.repair_command) == 1
    missing = advice(context, RepairBlocked("repair-vm-missing", ownership_established=True))
    assert missing.destroy_command is None


@pytest.mark.parametrize(
    "reason",
    [
        "controller-no-progress",
        "controller-observation-budget-exhausted",
        "controller-observation-unavailable",
        "controller-observation-contradictory",
    ],
)
def test_controller_pending_guidance_keeps_specific_reason(reason, tmp_path):
    from nebius_vpngw.deploy.recovery import Invocation, advice

    recovery = advice(Invocation("apply", tmp_path / "ha.yaml"), codes=(reason,))
    assert recovery.reason_code == reason
    assert "unknown" not in recovery.reason
    assert "apply" in recovery.resume_command
    assert "--repair" in recovery.repair_command


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_service_account_failure_suggests_compatible_repair(
    monkeypatch, tmp_path, sample_config, repair, output_format
):
    from contextlib import nullcontext

    from nebius_vpngw.deploy import ssh_client_auth

    path = tmp_path / "gateway's config.yaml"
    path.touch()
    identity = tmp_path / "operator identity"
    monkeypatch.setattr(ssh_client_auth, "select_operator_identity", lambda _path: None)

    def fail_config(*_args, **_kwargs):
        raise socket.gaierror("private-provider-detail")

    monkeypatch.setattr(
        cli, "load_local_config", (lambda _path: sample_config) if repair else fail_config
    )
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **_kwargs: nullcontext())
    selectors = ["--project-id", "selected-project", "--region", "selected-region"]
    result = CliRunner().invoke(
        cli.app,
        [
            "--ssh-identity",
            str(identity),
            "apply",
            "-c",
            str(path),
            *selectors,
            "--sa",
            "selected-account",
            "--output-format",
            output_format,
            *(["--repair"] if repair else []),
        ],
    )
    assert result.exit_code == (2 if repair else 1)
    if output_format == "json":
        recovery = json.loads(result.stdout)["recovery"]
        repair_command = recovery["repair_command"]
        resume_command = recovery["resume_command"]
        prerequisite = recovery["repair_prerequisite"]
        assert recovery["reason_code"] == ("invalid-arguments" if repair else "dns-unavailable")
    else:
        assert result.stdout.count("Reason:") == 1
        resume_command = next(
            line.removeprefix("Resume command: ")
            for line in result.stdout.splitlines()
            if line.startswith("Resume command: ")
        )
        repair_command = (
            resume_command
            if repair
            else next(
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip().startswith("nebius-vpngw ")
            )
        )
        prerequisite = result.stdout
    expected = [
        "nebius-vpngw",
        "--ssh-identity",
        str(identity),
        "apply",
        "--local-config-file",
        str(path),
        *selectors,
    ]
    assert shlex.split(repair_command) == [*expected, "--repair"]
    assert shlex.split(resume_command) == [
        *expected,
        *(["--repair"] if repair else ["--sa", "selected-account"]),
    ]
    assert "operator authentication" in prerequisite
    assert "private-provider-detail" not in result.output
