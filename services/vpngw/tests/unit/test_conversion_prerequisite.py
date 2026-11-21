from __future__ import annotations

import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import test_apply_ha_consolidation as consolidation
from test_apply_ha_consolidation import _Lock
from test_vm_ha_command import _inspection, _snapshot
from typer.testing import CliRunner

import nebius_vpngw.cli as cli
from nebius_vpngw.deploy import conversion_prerequisite as prerequisite
from nebius_vpngw.deploy.apply_plan import Evidence, Outcome
from nebius_vpngw.deploy.apply_result import ApplyReport
from nebius_vpngw.deploy.ha_convergence import ReportedExit
from nebius_vpngw.deploy.recovery import ApprovalDeclined, GuestFailure


@pytest.fixture(params=["static", "bgp"])
def conversion_files(sample_config, monkeypatch, tmp_path, request):
    if request.param == "bgp":
        sample_config["connections"][0]["routing_mode"] = "bgp"
        sample_config["connections"][0]["bgp"] = {
            "enabled": True,
            "remote_asn": 65002,
            "advertise_local_prefixes": True,
        }
    return consolidation.conversion_files.__wrapped__(sample_config, monkeypatch, tmp_path)


@pytest.mark.parametrize(
    "source_outcome", ["verified", "changed", "approved", "pending", "cancelled"]
)
def test_conversion_requires_ordinary_convergence_before_ha(
    conversion_files, monkeypatch, source_outcome
):
    source, target = conversion_files
    calls = []
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **kw: _Lock(calls))
    monkeypatch.setattr(cli, "_vm_ha_wizard_streams_interactive", lambda: False)
    inspected = _inspection(_snapshot(overall="HEALTHY", action="none"))

    def ordinary(**options):
        calls.append("ordinary")
        assert options["local_config_file"] == source
        assert options["repair"] is False
        if source_outcome == "approved":
            assert options["approve_disruption"] == "d" * 64
        if source_outcome == "cancelled":
            from nebius_vpngw.deploy.recovery import ApprovalDeclined

            raise ApprovalDeclined()
        if source_outcome == "pending":
            from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable

            raise ObservationUnavailable("ordinary-verify", "observation-incomplete")
        options["result_report"].verified(changed=source_outcome == "changed")

    def inspect(*args, **kwargs):
        calls.append("ha-inspect")
        return inspected

    monkeypatch.setattr(cli, "_apply_impl", ordinary)
    monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", inspect)
    monkeypatch.setattr(cli, "_confirm_vm_ha_healthy", lambda path, value, **kw: value)
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
            impact=cli.VMHACommandImpact("Verified unchanged", False, False, False),
        ),
    )
    monkeypatch.setattr(
        cli,
        "_execute_vm_ha_convergence_with_region",
        Mock(side_effect=AssertionError("unchanged HA must not execute")),
    )
    approval_args = (
        ["--approve", "a" * 64]
        if source_outcome == "changed"
        else ["--approve-disruption", "d" * 64]
        if source_outcome == "approved"
        else []
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "apply",
            "-c",
            str(source),
            "--convert-to-ha",
            "--output",
            str(target),
            "--output-format",
            "json",
            *approval_args,
        ],
    )
    assert "ordinary" in calls, (calls, result.output, result.exception)
    payload = json.loads(result.stdout)
    if source_outcome in {"verified", "changed", "approved"}:
        assert result.exit_code == 0, (result.output, result.exception)
        assert calls.index("ordinary") < calls.index("ha-inspect")
        assert "ordinary-convergence-verified" in payload["ha"]["actions"]
        if source_outcome == "changed":
            assert "ordinary-changes-applied" in payload["ha"]["actions"]
            assert payload["outcome"] == "applied"
    else:
        assert "ha-inspect" not in calls
        assert result.exit_code == (0 if source_outcome == "cancelled" else 1)
        assert (
            payload.get("cancelled")
            if source_outcome == "cancelled"
            else payload["outcome"] == "pending"
        )
    assert calls.count("enter") == calls.count("exit") == 1


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("outcome", ["pending", "newer", "unknown", "cancel", "plan", "interrupt"])
def test_prerequisite_failure_preserves_owner_and_exit(monkeypatch, tmp_path, dry_run, outcome):
    from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable
    from nebius_vpngw.deploy.release_policy import ReleaseBlocked

    child_reports = []

    def engine(**options):
        child = options["result_report"]
        child_reports.append(child)
        child.plan(("ordinary-effect",), approval="a" * 64)
        if outcome == "pending":
            raise ObservationUnavailable("ordinary-verify", "observation-incomplete")
        if outcome in {"newer", "unknown"}:
            raise ReleaseBlocked(outcome)
        if outcome == "cancel":
            raise ApprovalDeclined()
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        raise cli.typer.Exit(0)

    report = ApplyReport(dry_run=dry_run)
    with pytest.raises(ReportedExit) as stopped:
        prerequisite.run(
            NS(_apply_impl=engine),
            report,
            source=tmp_path / "source",
            options={"dry_run": dry_run},
            interactive=True,
        )
    expected = (
        130
        if outcome == "interrupt"
        else (0 if outcome == "cancel" or outcome == "plan" and dry_run else 1)
    )
    assert stopped.value.exit_code == expected
    assert len(child_reports) == 1
    assert report.approval == "a" * 64
    assert report.planned == ("ordinary-effect",)
    if outcome == "pending":
        assert report.result.outcome is Outcome.PENDING


@pytest.mark.parametrize("repair_outcome", ["verified", "cancelled", "pending"])
@pytest.mark.parametrize(
    "code,stage", [("runtime_drift", "verify"), ("ordinary_route_state_invalid", "inspect")]
)
def test_diagnosed_repair_requires_confirmation_then_fresh_ordinary_proof(
    tmp_path, repair_outcome, code, stage
):
    calls = []

    def engine(**options):
        calls.append(options["repair"])
        if len(calls) == 1:
            raise GuestFailure(code, stage, "local verification failed")
        if options["repair"] and repair_outcome == "cancelled":
            raise ApprovalDeclined()
        if options["repair"] and repair_outcome == "pending":
            from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable

            raise ObservationUnavailable("repair", "repair-worker-pending")
        if len(calls) == 3:
            assert options["approve_disruption"] is None
        options["result_report"].verified(changed=options["repair"])

    report = ApplyReport()
    args = dict(
        source=tmp_path / "source", options={"approve_disruption": "a" * 64}, interactive=True
    )
    api = NS(_apply_impl=engine, typer=cli.typer)
    if repair_outcome == "verified":
        assert prerequisite.run(api, report, **args) is True
        assert calls == [False, True, False]
        # A child success is not a terminal outer HA success.
        assert report.result.outcome is Outcome.BLOCKED
    else:
        with pytest.raises(ReportedExit) as stopped:
            prerequisite.run(api, report, **args)
        assert stopped.value.exit_code == (0 if repair_outcome == "cancelled" else 1)
        assert calls == [False, True]


@pytest.mark.parametrize(
    "code,stage,interactive",
    [
        ("runtime_drift", "inspect", True),
        ("handoff_execution_failed", "verify", True),
        ("final_artifact", "verify", False),
        ("ordinary_route_state_invalid", "inspect", False),
        ("ordinary_route_state_invalid", "transport", True),
    ],
)
def test_unknown_failure_and_noninteractive_never_reconstruct(tmp_path, code, stage, interactive):
    calls = []

    def engine(**options):
        calls.append(options["repair"])
        raise GuestFailure(code, stage, "diagnostic detail")

    with pytest.raises(ReportedExit):
        prerequisite.run(
            NS(_apply_impl=engine),
            ApplyReport(),
            source=tmp_path / "source",
            options={},
            interactive=interactive,
        )
    assert calls == [False]


def test_pending_repair_resumes_its_owner_without_conversion_options(tmp_path):
    import shlex

    from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable
    from nebius_vpngw.deploy.recovery import CURRENT, Invocation

    source = tmp_path / "source.yaml"
    outer = Invocation("apply", source, resume_options=("--convert-to-ha", "--output", "ha.yaml"))
    token = CURRENT.set(outer)

    def engine(**options):
        if not options["repair"]:
            raise GuestFailure("runtime_drift", "verify", "failed local proof")
        assert CURRENT.get().repair is True
        raise ObservationUnavailable("repair", "repair-worker-pending")

    report = ApplyReport()
    try:
        with pytest.raises(ReportedExit):
            prerequisite.run(
                NS(_apply_impl=engine, typer=cli.typer),
                report,
                source=source,
                options={},
                interactive=True,
            )
        assert CURRENT.get() is outer
        command = shlex.split(report.recovery.resume_command)
        assert "--repair" in command
        assert "--convert-to-ha" not in command and "--output" not in command
        assert str(source) in command
    finally:
        CURRENT.reset(token)


@pytest.mark.parametrize("evidence", [Evidence.UNKNOWN, Evidence.DEGRADED])
def test_local_proof_does_not_claim_workload_connectivity(evidence):
    report = ApplyReport()
    report.verified(changed=False, connectivity=evidence)
    assert prerequisite.ready(report)
    assert report.mapping()["connectivity"] == evidence.value
    report.verified(changed=False, local_runtime=Evidence.UNKNOWN)
    assert not prerequisite.ready(report)


@pytest.mark.parametrize(
    "status,expected",
    [
        (None, False),
        ("active", True),
        ("activating", True),
        ("provisioning", True),
        ("removal-in-progress", True),
        ("removed", False),
        ("destroyed", False),
    ],
)
def test_scoped_lifecycle_owns_resume_but_prepared_candidate_does_not(
    monkeypatch, tmp_path, status, expected
):
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus

    state = NS(status=VMHALifecycleStatus(status)) if status else None
    monkeypatch.setattr(
        prerequisite,
        "VMHALifecycleStore",
        lambda path: NS(read_hardened=lambda **kw: NS(state=state) if state else None),
    )
    monkeypatch.setattr(prerequisite, "pending_prerequisites", lambda *a, **kw: None)
    monkeypatch.setattr(prerequisite, "CommandJournal", lambda *a: NS(snapshot=lambda: None))
    assert (
        prerequisite.ha_owns(tmp_path / "candidate", {"project": "p", "gateway": "g"}) is expected
    )


def test_pending_ha_command_owns_before_lifecycle_publication(monkeypatch, tmp_path):
    monkeypatch.setattr(
        prerequisite, "VMHALifecycleStore", lambda path: NS(read_hardened=lambda **kw: None)
    )
    monkeypatch.setattr(prerequisite, "CommandJournal", lambda *a: object())
    monkeypatch.setattr(prerequisite, "pending_prerequisites", lambda *a, **kw: {"pending": True})
    assert prerequisite.ha_owns(tmp_path / "candidate", {"project": "p", "gateway": "g"})


def test_completed_ha_without_lifecycle_is_not_ordinary(monkeypatch, tmp_path):
    monkeypatch.setattr(
        prerequisite, "VMHALifecycleStore", lambda path: NS(read_hardened=lambda **kw: None)
    )
    monkeypatch.setattr(
        prerequisite,
        "CommandJournal",
        lambda *a: NS(snapshot=lambda: {"transaction": {"plan": {}}}),
    )
    monkeypatch.setattr(prerequisite, "pending_prerequisites", lambda *a, **kw: None)
    monkeypatch.setattr(
        prerequisite.CommandPlan,
        "parse",
        lambda p: NS(effects=(NS(key="runtime", owner="ha-lifecycle"),)),
    )
    with pytest.raises(prerequisite.ConversionFailure, match="conversion-lifecycle-unavailable"):
        prerequisite.ha_owns(tmp_path / "candidate", {"project": "p", "gateway": "g"})


def test_real_hardened_lifecycle_selects_ha_resume(monkeypatch, tmp_path):
    from pathlib import Path

    from test_vm_ha_lifecycle import _provisioning_state

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    candidate = tmp_path / "candidate.yaml"
    state = _provisioning_state()
    prerequisite.VMHALifecycleStore(candidate).write_verified(state)
    assert prerequisite.ha_owns(
        candidate, {"project": state.project_id, "gateway": state.gateway_name}
    )
    with pytest.raises(ValueError, match="project identity"):
        prerequisite.ha_owns(candidate, {"project": "foreign", "gateway": state.gateway_name})


def test_real_pending_ha_parent_selects_resume_before_lifecycle(monkeypatch, tmp_path):
    from pathlib import Path

    from nebius_vpngw.deploy import ha_command

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    journal = prerequisite.CommandJournal("project-a", "gateway-a")
    command = ha_command.create(
        journal,
        scope="a" * 64,
        artifact="b" * 64,
        action_scope="c" * 64,
        targets=("compute-a",),
        approval="d" * 64,
        runtime_scope="e" * 64,
        report={"kind": "apply-convergence"},
        attachment_plan=None,
    )
    with journal:
        command.begin()
    assert prerequisite.ha_owns(
        tmp_path / "candidate.yaml", {"project": "project-a", "gateway": "gateway-a"}
    )


def test_completed_ordinary_actions_survive_missing_ha_inputs(conversion_files, monkeypatch):
    source, target = conversion_files
    target.unlink()
    monkeypatch.setattr(cli, "_vm_ha_wizard_streams_interactive", lambda: False)
    monkeypatch.setattr(cli, "_apply_impl", lambda **kw: kw["result_report"].verified(changed=True))
    monkeypatch.setattr(
        cli,
        "_reserve_vm_ha_passive_public_ip",
        Mock(side_effect=AssertionError("noninteractive conversion cannot reserve")),
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "apply",
            "-c",
            str(source),
            "--convert-to-ha",
            "--output",
            str(target),
            "--output-format",
            "json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ha"]["reasons"] == ["conversion-input-required"]
    assert payload["ha"]["actions"] == ["ordinary-convergence-verified", "ordinary-changes-applied"]
    assert not target.exists()
