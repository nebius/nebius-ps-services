from __future__ import annotations

import copy
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from rich.console import Console
from test_vm_ha_command import _inspection, _snapshot
from test_vm_ha_conversion import _answer_prompts, _happy_answers
from typer.testing import CliRunner

import nebius_vpngw.cli as cli
from nebius_vpngw.deploy import ha_convergence, ha_conversion
from nebius_vpngw.deploy.apply_plan import Evidence
from nebius_vpngw.deploy.apply_result import ApplyReport
from nebius_vpngw.vm_ha_config_wizard import run_vm_ha_conversion_wizard


@pytest.fixture
def conversion_files(sample_config, monkeypatch, tmp_path):
    source = tmp_path / "single.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False))
    source.chmod(0o600)
    target = tmp_path / "custom-ha.yaml"
    _answer_prompts(monkeypatch, _happy_answers(tmp_path))
    prepared = copy.deepcopy(sample_config)
    cli._apply_nebius_region_precedence(prepared, explicit_region=None)
    converted = run_vm_ha_conversion_wizard(
        Console(file=io.StringIO()),
        prepared,
        target,
        reserve_passive_ip=lambda: pytest.fail("fixture must use an existing address"),
    )
    assert converted.yaml_text
    target.write_text(converted.yaml_text)
    target.chmod(0o600)
    for name in re.findall(r"\$\{([A-Z_0-9]+)\}", converted.yaml_text):
        monkeypatch.setenv(name, "fixture-only-conversion-psk")
    cli.load_local_config(target)
    return source, target


def resolve(source, output=None, **kwargs):
    return ha_conversion.resolve(
        cli,
        source_path=source,
        output=output,
        force=kwargs.pop("force", False),
        dry_run=kwargs.pop("dry_run", False),
        region=None,
        interactive=False,
        **kwargs,
    )


def test_conversion_locator_reuses_custom_candidate_after_ha_edit(conversion_files, monkeypatch):
    source, target = conversion_files
    source_bytes = source.read_bytes()
    assert resolve(source, target).path == target
    locator = source.with_name(source.name + ".ha-conversion.json")
    before = locator.read_bytes()
    assert locator.stat().st_mode & 0o777 == 0o600
    candidate = yaml.safe_load(target.read_text())
    candidate["gateway"]["local_asn"] += 1
    target.write_text(yaml.safe_dump(candidate, sort_keys=False))
    monkeypatch.setattr(
        cli,
        "_ordinary_vm_ha_conversion_trust_prerequisite",
        Mock(side_effect=AssertionError("Single trust must not run after conversion")),
    )
    assert resolve(source).path == target
    assert resolve(target).path == target
    assert source.read_bytes() == source_bytes
    assert locator.read_bytes() == before


@pytest.mark.parametrize(
    "change,code",
    [
        ("source", "conversion-source-changed"),
        ("scope", "conversion-scope-changed"),
        ("missing", "conversion-file-unavailable"),
        ("locator", "conversion-record-invalid"),
        ("output", "conversion-output-conflict"),
    ],
)
def test_conversion_conflicts_do_not_rewrite_files(conversion_files, change, code):
    source, target = conversion_files
    resolve(source, target)
    locator = source.with_name(source.name + ".ha-conversion.json")
    if change == "source":
        source.write_text(source.read_text() + "# new source intent\n")
    elif change == "scope":
        candidate = yaml.safe_load(target.read_text())
        candidate["project_id"] = "another-project"
        target.write_text(yaml.safe_dump(candidate))
    elif change == "missing":
        target.unlink()
    elif change == "locator":
        locator.write_text("{}")
    before = {p: p.read_bytes() for p in source.parent.iterdir() if p.is_file()}
    with pytest.raises(ha_conversion.ConversionFailure, match=code):
        resolve(source, source.parent / "another.yaml" if change == "output" else None)
    assert before == {p: p.read_bytes() for p in source.parent.iterdir() if p.is_file()}


def test_conversion_dry_run_does_not_publish_locator(conversion_files):
    source, target = conversion_files
    before = {p: p.read_bytes() for p in source.parent.iterdir()}
    assert resolve(source, target, dry_run=True).path == target
    assert before == {p: p.read_bytes() for p in source.parent.iterdir()}


def test_conversion_reconstructs_only_exact_candidate_after_publication_crash(conversion_files):
    source, target = conversion_files
    assert resolve(source, target).path == target
    locator = source.with_name(source.name + ".ha-conversion.json")
    locator.unlink()
    candidate = yaml.safe_load(target.read_text())
    candidate["gateway"]["local_asn"] += 1
    target.write_text(yaml.safe_dump(candidate))
    result = resolve(source, target)
    assert result.reasons == ("candidate-conflicts-with-source",)
    assert not locator.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--repair", "--convert-to-ha"],
        ["--repair", "--rotate-mtls"],
        ["--repair", "--dry-run"],
        ["--rotate-mtls", "--standby-auto-healing", "enabled"],
        ["--force"],
        ["--output", "other.yaml"],
        ["--approve", "a" * 64, "--approve-disruption", "b" * 64],
        ["--approve", "a" * 64, "--recreate-gw"],
        ["--approve", "a" * 64, "--resume-replacement"],
        ["--approve", "a" * 64, "--prepare-vm-ha-peer-rotation"],
    ],
)
def test_new_option_conflicts_precede_config_and_effects(tmp_path, monkeypatch, arguments):
    path = tmp_path / "invalid.yaml"
    path.write_text("invalid")
    loader = Mock(side_effect=AssertionError("conflict reached config loading"))
    monkeypatch.setattr(cli, "load_local_config", loader)
    result = CliRunner().invoke(cli.app, ["apply", "-c", str(path), *arguments])
    assert result.exit_code == 2, result.output
    loader.assert_not_called()


def test_removed_vm_ha_is_not_an_alias():
    result = CliRunner().invoke(cli.app, ["vm-ha", "--help"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_plain_single_apply_does_not_follow_conversion_record(conversion_files, monkeypatch):
    source, target = conversion_files
    resolve(source, target)
    calls = []
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **kw: _Lock(calls))

    def engine(**kwargs):
        calls.append(kwargs["local_config_file"])
        kwargs["result_report"].verified(changed=False)

    monkeypatch.setattr(cli, "_apply_impl", engine)
    result = CliRunner().invoke(cli.app, ["apply", "-c", str(source), "--output-format", "json"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert calls == ["enter", source, "exit"]
    assert json.loads(result.stdout)["outcome"] == "unchanged"


class _Lock:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        assert "enter" not in self.calls, "nested command lock"
        self.calls.append("enter")

    def __exit__(self, *args):
        self.calls.append("exit")


def test_healthy_ha_still_plans_desired_parity(conversion_files, monkeypatch):
    _, target = conversion_files
    calls = []
    inspected = _inspection(_snapshot(overall="HEALTHY", action="none"))
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **kw: _Lock(calls))
    monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", lambda *a, **kw: inspected)
    monkeypatch.setattr(cli, "_confirm_vm_ha_healthy", lambda path, value, **kw: value)

    def plan(*args, **kwargs):
        assert calls == ["enter"]
        calls.append("plan")
        return cli._VMHAApplyPlanReport(
            kind="unchanged",
            digest="a" * 64,
            engine_digest="a" * 64,
            effects=(),
            has_destructive_changes=False,
            managed_ssh_action=None,
            impact=cli.VMHACommandImpact("Verified unchanged", False, False, False),
        )

    monkeypatch.setattr(cli, "_plan_vm_ha_convergence_with_region", plan)
    monkeypatch.setattr(
        cli,
        "_execute_vm_ha_convergence_with_region",
        Mock(side_effect=AssertionError("no-op executed")),
    )
    result = CliRunner().invoke(cli.app, ["apply", "-c", str(target), "--output-format", "json"])
    assert result.exit_code == 0, (result.output, result.exception)
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "unchanged"
    assert payload["configuration"] == "ready"
    assert payload["ha"]["health"] == "healthy"
    assert calls == ["enter", "plan", "exit"]


def test_repair_dispatch_precedes_damaged_ha_lifecycle(conversion_files, monkeypatch):
    _, target = conversion_files
    Path(str(target) + ".vm-ha-lifecycle.json").write_text("not-json")
    calls = []
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **kw: _Lock(calls))
    monkeypatch.setattr(
        ha_convergence, "converge", Mock(side_effect=AssertionError("repair entered HA inspection"))
    )

    def repair(**kwargs):
        assert kwargs["repair"] is True
        calls.append("repair")
        kwargs["result_report"].verified(changed=True)

    monkeypatch.setattr(cli, "_apply_impl", repair)
    result = CliRunner().invoke(
        cli.app, ["apply", "-c", str(target), "--repair", "--output-format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert calls == ["enter", "repair", "exit"]
    assert json.loads(result.stdout)["outcome"] == "applied"


@pytest.mark.parametrize("fail_after_start", [False, True])
def test_rotation_preserves_selectors_and_has_one_json_result(
    conversion_files, monkeypatch, fail_after_start
):
    _, target = conversion_files
    calls = []
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **kw: _Lock(calls))
    monkeypatch.setattr(cli, "_vm_ha_wizard_streams_interactive", lambda: False)
    monkeypatch.setattr("nebius_vpngw.deploy.ha_composite.pending", lambda *a, **kw: False)

    def inspect(path):
        effective = cli.load_local_config(path)
        assert effective["project_id"] == "project-overridden"
        assert effective["gateway_group"]["region"] == "eu-north1"
        assert calls[0] == "enter"
        return SimpleNamespace(digest="a" * 64)

    monkeypatch.setattr(cli, "_inspect_vm_ha_mtls_rotation", inspect)

    def execute(plan):
        calls.append("rotate")
        if fail_after_start:
            raise TimeoutError()

    monkeypatch.setattr(cli, "_execute_vm_ha_mtls_rotation", execute)
    result = CliRunner().invoke(
        cli.app,
        [
            "apply",
            "-c",
            str(target),
            "--rotate-mtls",
            "--approve",
            "a" * 64,
            "--project-id",
            "project-overridden",
            "--region",
            "eu-north1",
            "--output-format",
            "json",
        ],
    )
    assert result.exit_code == int(fail_after_start), (result.output, result.exception)
    payload = json.loads(result.stdout)
    if fail_after_start:
        assert payload["outcome"] == "failed"
        assert payload["recovery"]["reason_code"] == "operation-timeout"
    else:
        assert payload["ha"]["actions"] == ["mtls-rotated"]
    assert calls == ["enter", "rotate", "exit"]
    assert cli.load_local_config(target)["project_id"] == "project-test"


@pytest.mark.parametrize("coverage", ["ready", "degraded", "unknown"])
@pytest.mark.parametrize("policy", ["enabled", "disabled"])
def test_local_convergence_preserves_remote_coverage_and_policy(coverage, policy):
    from test_apply_health import snapshot

    observed = snapshot(coverage=coverage, policy=policy)
    assert ha_convergence.local_convergence_ready(observed)
    report = ApplyReport()
    token = ha_convergence.REPORT.set(report)
    try:
        result = cli.VMHACommandResult(
            outcome=cli.VMHACommandOutcome.BLOCKED,
            classification=cli.VMHACommandClassification.AMBIGUOUS_STATE,
            health=cli.VMHACommandHealth.BLOCKED,
            effective_config_file=Path("ha.yaml"),
        )
        projected = ha_convergence.local_result(cli, result, observed, verified=True)
        assert projected.outcome is cli.VMHACommandOutcome.HEALTHY
        assert report.result.connectivity.value == coverage
        assert report.result.configuration is Evidence.UNKNOWN
        assert all(member.record["auto_healing"]["state"] == policy for member in observed.members)
    finally:
        ha_convergence.REPORT.reset(token)


def test_conversion_changed_source_recovery_targets_verified_ha_candidate(conversion_files):
    from nebius_vpngw.deploy.recovery import CURRENT, Invocation, advice

    source, target = conversion_files
    resolve(source, target)
    source.write_text(source.read_text() + "# changed original intent\n")
    context = Invocation("apply", source)
    token = CURRENT.set(context)
    try:
        with pytest.raises(ha_conversion.ConversionFailure) as failure:
            resolve(source)
        recovery = advice(context, failure.value)
        assert str(target) in recovery.repair_command
        assert recovery.reason_code == "conversion-source-changed"
        assert "inspect VM-HA service journals" not in recovery.next_action
    finally:
        CURRENT.reset(token)


def test_project_and_region_overrides_precede_placeholder_validation(conversion_files, monkeypatch):
    from nebius_vpngw.deploy.apply_context import invocation, load_local_config

    source, _target = conversion_files
    data = yaml.safe_load(source.read_text())
    data["project_id"] = "${MISSING_CONSOLIDATION_PROJECT}"
    data["region_id"] = "${MISSING_CONSOLIDATION_REGION}"
    data["gateway_group"]["region"] = "${MISSING_CONSOLIDATION_REGION}"
    source.write_text(yaml.safe_dump(data))
    monkeypatch.delenv("MISSING_CONSOLIDATION_PROJECT", raising=False)
    monkeypatch.delenv("MISSING_CONSOLIDATION_REGION", raising=False)
    with invocation({"project_id": "selected-project", "region": "eu-north1"}):
        selected = load_local_config(source)
    assert selected["project_id"] == "selected-project"
    assert selected["region_id"] == selected["gateway_group"]["region"] == "eu-north1"


@pytest.mark.parametrize("mode", [[], ["--rotate-mtls"], ["--standby-auto-healing", "enabled"]])
def test_approval_next_command_executes_instead_of_repeating_preview(mode):
    import shlex

    from nebius_vpngw.deploy.recovery import CURRENT, Invocation

    context = Invocation(
        "apply",
        Path("HA config.yaml"),
        project="p",
        region="r",
        dry_run=True,
        resume_options=(*mode, "--dry-run"),
    )
    result = cli.VMHACommandResult(
        outcome=cli.VMHACommandOutcome.PLANNED,
        classification=cli.VMHACommandClassification.APPLY_REQUIRED,
        health=cli.VMHACommandHealth.UNKNOWN,
        effective_config_file=context.config,
        approval=cli.VMHACommandApproval(
            kind="apply-convergence", digest="a" * 64, effects=("reconcile",)
        ),
    )
    token = CURRENT.set(context)
    try:
        report = ApplyReport(dry_run=True)
        assert ha_convergence._project(cli, report, result) == 0
        command = shlex.split(report.next_command)
        assert "--dry-run" not in command
        assert command[-2:] == ["--approve", "a" * 64]
        assert all(option in command for option in mode)
    finally:
        CURRENT.reset(token)


@pytest.mark.parametrize(
    "problem", ["dns", "timeout", "authentication", "dependency", "intent", "unknown"]
)
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_ha_apply_failures_publish_once_with_selectors_and_repair(
    conversion_files, monkeypatch, problem, dry_run, output_format
):
    import socket

    from nebius_vpngw.dependency_scope import DependencyFailure
    from nebius_vpngw.deploy import pair_phases
    from nebius_vpngw.deploy.apply_diagnostics import PreparationBlocked
    from nebius_vpngw.nebius_auth import NebiusCLIAuthenticationError

    _source, target = conversion_files
    error, code = {
        "dns": (socket.gaierror("private-error-sentinel"), "dns-unavailable"),
        "timeout": (TimeoutError("private-error-sentinel"), "operation-timeout"),
        "authentication": (
            NebiusCLIAuthenticationError("private-error-sentinel"),
            "authentication-or-provider-unavailable",
        ),
        "dependency": (
            DependencyFailure("dependency-resolver-unavailable", "private-error-sentinel"),
            "dependency-resolver-unavailable",
        ),
        "intent": (PreparationBlocked("desired-state-changed"), "desired-state-changed"),
        "unknown": (LookupError("private-error-sentinel"), "unknown-failure"),
    }[problem]
    calls, reports = [], []
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **kw: _Lock(calls))
    monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", Mock(side_effect=error))
    monkeypatch.setattr(pair_phases, "report", reports.append)
    args = [
        "apply",
        "-c",
        str(target),
        "--project-id",
        "selected-project",
        "--region",
        "eu-north1",
        "--output-format",
        output_format,
    ]
    if dry_run:
        args.append("--dry-run")
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 1, result.output
    assert calls == ([] if dry_run else ["enter", "exit"])
    assert len(reports) == 1
    recovery = reports[0]["recovery"]
    assert recovery["reason_code"] == code
    assert recovery["next_action"] and recovery["repair_prerequisite"]
    assert "--project-id selected-project --region eu-north1 --repair" in recovery["repair_command"]
    assert str(target) in recovery["repair_command"]
    assert "private-error-sentinel" not in result.output
    if output_format == "json":
        assert json.loads(result.stdout) == json.loads(json.dumps(reports[0]))
    else:
        assert result.stdout.count("Reason:") == 1
        assert result.stdout.count("--repair") == 1


@pytest.mark.parametrize("decision", ["preview", "decline", "approve", "stale"])
def test_ha_predecessor_settlement_has_separate_successor_approval(
    conversion_files, monkeypatch, decision
):
    from nebius_vpngw.deploy.apply_command import ReplanReady
    from nebius_vpngw.deploy.apply_result import ApplyReport

    _source, target = conversion_files
    calls = []
    settled = False
    first_digest, successor_digest = "a" * 64, "b" * 64
    effects = ("settle-ha-predecessor",)
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **kw: _Lock(calls))
    observed = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", lambda *a, **kw: observed)
    monkeypatch.setattr(cli, "_vm_ha_wizard_streams_interactive", lambda: decision == "decline")
    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **kw: False)
    monkeypatch.setattr(
        cli,
        "_plan_vm_ha_convergence_with_region",
        lambda path, **kw: cli._plan_vm_ha_apply_convergence(path),
    )

    def engine(**kwargs):
        nonlocal settled
        report = kwargs.get("result_report") or ApplyReport(dry_run=kwargs["dry_run"])
        if settled:
            assert kwargs["dry_run"] and kwargs["stop_after_vm_ha_plan"]
            # Retirement does not authorize the new deployment's effects.
            raise cli._VMHAApplyPlanCaptured(
                cli._VMHAApplyPlanReport(
                    kind="apply-convergence",
                    digest=successor_digest,
                    engine_digest=successor_digest,
                    effects=("new-deployment",),
                    has_destructive_changes=False,
                    managed_ssh_action=None,
                )
            )
        report.plan(effects, approval=first_digest)
        cli._admit_ha_settlement(
            first_digest,
            effects,
            passive=kwargs.get("stop_after_vm_ha_plan", False),
            expected=kwargs.get("expected_vm_ha_plan"),
        )
        assert kwargs["approve_disruption"] == first_digest
        assert not kwargs["dry_run"]
        calls.append("settle-original")
        settled = True
        raise ReplanReady()

    monkeypatch.setattr(cli, "_apply_impl", engine)
    args = ["apply", "-c", str(target)]
    if decision == "preview":
        args += ["--dry-run"]
    if decision in {"approve", "stale"}:
        args += ["--approve", first_digest if decision == "approve" else "c" * 64]
    if decision != "decline":
        args += ["--output-format", "json"]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == (0 if decision in {"preview", "decline"} else 1), result.output
    assert settled is (decision == "approve")
    assert calls == (
        []
        if decision == "preview"
        else ["enter", *(["settle-original"] if settled else []), "exit"]
    )
    if decision != "decline":
        payload = json.loads(result.stdout)
        if decision == "approve":
            assert payload["ha"]["approval"]["digest"] == successor_digest
            assert payload["next_command"].endswith("--approve " + successor_digest)
            assert "recovery" not in payload
        elif decision == "preview":
            assert payload["ha"]["approval"]["kind"] == "predecessor-settlement"
            assert payload["ha"]["approval"]["effects"] == list(effects)


@pytest.mark.parametrize(
    "expected_kind,expected_digest",
    [("apply-convergence", "a" * 64), ("predecessor-settlement", "b" * 64)],
)
def test_changed_settlement_cannot_borrow_any_other_plan_approval(expected_kind, expected_digest):
    from nebius_vpngw.deploy.apply_diagnostics import PreparationBlocked

    expected = SimpleNamespace(kind=expected_kind, engine_digest=expected_digest)
    with pytest.raises(PreparationBlocked, match="approval-state-changed"):
        cli._admit_ha_settlement(
            "a" * 64, ("settle-ha-predecessor",), passive=False, expected=expected
        )


@pytest.mark.parametrize(
    "option",
    [
        "--approve",
        "--approve-disruption",
        "--approve-vm-ha-migration",
        "--recover-vm-ha-migration",
        "--replace-failed-vm-ha-passive",
    ],
)
def test_settlement_consumes_approval_before_successor_planning_failure(monkeypatch, option):
    from nebius_vpngw.deploy import ha_convergence
    from nebius_vpngw.deploy.apply_command import ReplanReady
    from nebius_vpngw.deploy.apply_result import ApplyReport
    from nebius_vpngw.deploy.recovery import CURRENT, Invocation

    context = Invocation(command="apply", resume_options=(option, "a" * 64, "--convert-to-ha"))
    calls = []

    def run(api, report, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ReplanReady()
        raise TimeoutError()

    monkeypatch.setattr(ha_convergence, "run", run)
    token = CURRENT.set(context)
    report = ApplyReport()
    try:
        with pytest.raises(TimeoutError):
            cli._run_apply_reported(report, **{option[2:].replace("-", "_"): "a" * 64})
        assert all(value is None for value in calls[-1].values())
        assert context.resume_options == ("--convert-to-ha",)
        assert "--approve" not in report.recovery.resume_command
    finally:
        CURRENT.reset(token)


def test_recorded_ha_candidate_validation_failure_keeps_correct_repair_target(
    conversion_files, monkeypatch
):
    from nebius_vpngw.deploy.recovery import CURRENT, ConfigurationFailure, Invocation, advice

    source, target = conversion_files
    resolve(source, target)
    candidate = yaml.safe_load(target.read_text())
    candidate["connections"][0]["tunnels"][0]["psk"] = "${MISSING_CANDIDATE_PSK}"
    target.write_text(yaml.safe_dump(candidate))
    monkeypatch.delenv("MISSING_CANDIDATE_PSK", raising=False)
    context = Invocation(command="apply", config=source, resume_options=("--convert-to-ha",))
    token = CURRENT.set(context)
    try:
        with pytest.raises(ConfigurationFailure) as caught:
            resolve(source)
        recovery = advice(context, error=caught.value)
        assert recovery.reason_code == "configuration-invalid"
        assert str(target) in recovery.repair_command
        assert str(source) not in recovery.repair_command
    finally:
        CURRENT.reset(token)


def test_repeat_conversion_uses_current_ha_credentials_not_obsolete_source(
    conversion_files, monkeypatch
):
    source, target = conversion_files
    original = yaml.safe_load(source.read_text())
    original["connections"][0]["tunnels"][0]["psk"] = "${OLD_CONVERSION_PSK}"
    source.write_text(yaml.safe_dump(original, sort_keys=False))
    converted = yaml.safe_load(target.read_text())
    converted["connections"][0]["tunnels"][0]["psk"] = "${OLD_CONVERSION_PSK}"
    target.write_text(yaml.safe_dump(converted, sort_keys=False))
    monkeypatch.setenv("OLD_CONVERSION_PSK", "fixture-only-before-rotation")
    resolve(source, target)
    converted["connections"][0]["tunnels"][0]["psk"] = "${NEW_CONVERSION_PSK}"
    target.write_text(yaml.safe_dump(converted, sort_keys=False))
    monkeypatch.delenv("OLD_CONVERSION_PSK")
    monkeypatch.setenv("NEW_CONVERSION_PSK", "fixture-only-after-rotation")
    observed = _inspection(_snapshot(overall="HEALTHY", action="none"))
    # This test models an established HA gateway after credential rotation;
    # candidate existence alone no longer establishes that ownership.
    monkeypatch.setattr("nebius_vpngw.deploy.conversion_prerequisite.ha_owns", lambda *args: True)
    calls = []
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **kw: _Lock(calls))

    def inspect(path, **kwargs):
        assert path == target
        assert (
            cli.load_local_config(path)["connections"][0]["tunnels"][0]["psk"]
            == "fixture-only-after-rotation"
        )
        return observed

    monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", inspect)
    monkeypatch.setattr(cli, "_confirm_vm_ha_healthy", lambda path, observed, **kw: observed)
    monkeypatch.setattr(
        cli,
        "_plan_vm_ha_convergence_with_region",
        lambda *a, **kw: cli._VMHAApplyPlanReport(
            kind="unchanged",
            digest="a" * 64,
            engine_digest="a" * 64,
            effects=(),
            has_destructive_changes=False,
            managed_ssh_action=None,
        ),
    )
    result = CliRunner().invoke(
        cli.app, ["apply", "-c", str(source), "--convert-to-ha", "--output-format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert calls == ["enter", "exit"]
    assert json.loads(result.stdout)["ha"]["effective_config_file"] == str(target)
