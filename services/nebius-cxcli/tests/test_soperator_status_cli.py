from __future__ import annotations

import time
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from nebius_cxcli import cli, soperator_status_collect
from status_health_fakes import healthy_snapshot, resource


@pytest.fixture
def status_cli(monkeypatch: pytest.MonkeyPatch, tmp_path):
    snapshot = healthy_snapshot()
    calls = []
    target = SimpleNamespace(target_ref="example")
    registration = {
        "cluster_id": "cluster-example",
        "kube_context": "example",
        "inventory": {"cluster_identity": {"kubernetes_uid": "cluster-uid-a"}},
    }
    monkeypatch.setattr(cli, "_read_config_payload", lambda _: {})
    monkeypatch.setattr(
        cli, "_resolve_soperator_command_target", lambda *a, **k: (target, registration, True)
    )
    monkeypatch.setattr(cli, "_source_helm_chart_row", lambda *a: {"version": "4.1.7"})
    monkeypatch.setattr(
        cli,
        "resolve_project_paths",
        lambda _: SimpleNamespace(project_dir=tmp_path, reports_dir=tmp_path),
    )
    monkeypatch.setattr(
        cli, "read_local_deployment_record", lambda _: calls.append("deployment") or None
    )
    monkeypatch.setattr(cli, "read_soperator_operation_status", lambda **k: None)
    monkeypatch.setattr(cli, "read_soperator_completed_upgrade_evidence", lambda **k: None)
    monkeypatch.setattr(
        soperator_status_collect,
        "read_status_identity",
        lambda **k: {"cluster_identity": {"kubernetes_uid": "cluster-uid-a"}},
    )

    def collect(**kwargs):
        calls.append("health")
        kwargs["progress"]("Reading component readiness")
        return snapshot

    monkeypatch.setattr(soperator_status_collect, "collect_status_snapshot", collect)
    return (
        snapshot,
        calls,
        ["soperator", "status", str(tmp_path / "config.yaml"), "--no-interactive"],
    )


@pytest.mark.parametrize("expected", ["Healthy", "Degraded", "Unhealthy", "Unknown"])
def test_cli_report_footer_and_exit_code_agree(status_cli, expected: str) -> None:
    snapshot, _, argv = status_cli
    if expected in {"Degraded", "Unhealthy"}:
        snapshot["slurm_nodes"]["nodes"]["worker-0"]["State"] = "DOWN"
        if expected == "Unhealthy":
            snapshot["slurm_nodes"]["nodes"]["worker-1"]["State"] = "DOWN"
    elif expected == "Unknown":
        snapshot["status_issues"] = ["Query timed out"]
        snapshot["slurm_nodes"] = {"state": "failed"}
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == (0 if expected == "Healthy" else 1), result.output
    assert f"Overall health: {expected}" in result.stdout
    assert "Overall health:" in result.stdout.splitlines()[-1]
    assert "Lifecycle: idle" in result.stdout and "Operation: none" not in result.stdout
    assert "START Reading deployment records" in result.stderr
    assert "START" not in result.stdout
    assert "\x1b" not in result.stdout + result.stderr


def test_offline_status_performs_no_remote_queries(status_cli) -> None:
    _, calls, argv = status_cli
    result = CliRunner().invoke(cli.app, [*argv, "--no-live"])
    assert result.exit_code == 0
    assert calls == []
    assert "Local lifecycle: idle" in result.stdout
    assert "Overall health: Not checked" in result.stdout


@pytest.mark.parametrize("live", [False, True])
def test_completed_upgrade_keeps_provider_evidence_internal(status_cli, monkeypatch, live):
    _, _, argv = status_cli
    evidence = SimpleNamespace(
        target_release="4.2.0",
        target_kubernetes_version="1.34",
        ownership="managed",
        backend="terraform",
        provider_compatibility=("worker@1.34=ubuntu24.04/cuda13.0",),
        gpu_runtime=("worker=passed",),
        receipt_path="completed-upgrade.json",
    )
    monkeypatch.setattr(cli, "read_soperator_completed_upgrade_evidence", lambda **k: evidence)

    result = CliRunner().invoke(cli.app, argv if live else [*argv, "--no-live"])

    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Last completed full-stack upgrade: 4.2.0, Kubernetes 1.34" in output
    assert "GPU runtime evidence: worker=passed" in output
    assert "Completed upgrade receipt:" in output
    assert "Frozen provider compatibility:" not in output
    assert evidence.provider_compatibility[0] not in output
    assert evidence.provider_compatibility == ("worker@1.34=ubuntu24.04/cuda13.0",)


def test_show_checks_remains_offline_without_live(status_cli) -> None:
    _, calls, argv = status_cli
    result = CliRunner().invoke(cli.app, [*argv, "--no-live", "--show-checks"])
    assert result.exit_code == 0 and calls == []
    assert "Recorded checks: Not checked" in result.stdout
    assert "Overall health: Not checked" in result.stdout


@pytest.mark.parametrize("show_checks", [False, True])
@pytest.mark.parametrize("history_state", ["collected", "unavailable"])
def test_check_history_never_changes_current_health_or_exit(
    status_cli, show_checks, history_state
) -> None:
    snapshot, calls, argv = status_cli
    snapshot["status_check_history"] = {"state": history_state, "detail": "Query timed out"}
    for name, state in (("passed-check", "Complete"), ("failed-check", "Failed")):
        item = resource("ActiveCheck", name, slurmClusterRefName="soperator", checkType="k8sJob")
        item["status"]["k8sJobsStatus"] = {"lastJobStatus": state}
        snapshot["soperator_resources"].append(item)
    result = CliRunner().invoke(cli.app, [*argv, *(["--show-checks"] if show_checks else [])])
    assert result.exit_code == 0, result.output
    assert calls == ["deployment", "health"]
    assert result.stdout.rstrip().endswith("Overall health: Healthy")
    assert "partial report" not in result.stdout
    if history_state == "unavailable":
        assert "Warning: recorded check history unavailable" in result.stdout
        assert "0 failures" not in result.stdout and "failed-check" not in result.stdout
    else:
        assert "failed-check" in result.stdout
        assert ("passed-check" in result.stdout) == show_checks


def test_identity_mismatch_stops_before_component_collection(
    status_cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, calls, argv = status_cli
    monkeypatch.setattr(
        soperator_status_collect,
        "read_status_identity",
        lambda **k: {"cluster_identity": {"kubernetes_uid": "foreign"}},
    )
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 1
    assert "health" not in calls
    assert "Overall health: Error" in result.stdout
    assert "K8s Ready" not in result.stdout


def test_unexpected_transport_exception_does_not_disclose_raw_error(
    status_cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, argv = status_cli

    def fail(_):
        raise RuntimeError("private transport credential sentinel")

    monkeypatch.setattr(cli, "read_local_deployment_record", fail)
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 1
    assert "credential sentinel" not in result.output
    assert "Overall health: Error" in result.stdout


@pytest.mark.parametrize("interrupt", [False, True])
def test_terminal_progress_runs_during_slow_collection_and_cleans_up(
    status_cli, monkeypatch: pytest.MonkeyPatch, interrupt: bool
) -> None:
    snapshot, _, argv = status_cli
    stderr, stdout = StringIO(), StringIO()
    progress_console = Console(
        file=stderr,
        force_terminal=True,
        color_system="standard",
        _environ={"TERM": "xterm"},
        width=110,
    )
    monkeypatch.setattr(cli, "progress_console", progress_console)
    monkeypatch.setattr(cli, "console", Console(file=stdout, force_terminal=False, width=110))

    def collect(**kwargs):
        kwargs["progress"]("Waiting for delayed observation")
        assert progress_console._live_stack
        live = progress_console._live_stack[-1]
        assert live._started
        time.sleep(0.15)
        live.refresh()
        assert "Waiting for delayed observation" in stderr.getvalue()
        if interrupt:
            raise KeyboardInterrupt
        return snapshot

    monkeypatch.setattr(soperator_status_collect, "collect_status_snapshot", collect)
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == (130 if interrupt else 0)
    assert not progress_console._live_stack
    if interrupt:
        assert "Overall health: Healthy" not in stdout.getvalue()
    else:
        assert stdout.getvalue().rstrip().endswith("Overall health: Healthy")


def test_testdev_profile_is_labelled_as_configuration_without_live_evidence(
    status_cli, monkeypatch
):
    _, calls, argv = status_cli
    monkeypatch.setattr(
        cli,
        "_source_helm_chart_row",
        lambda *a: {"version": "4.1.9", "values": {"deploymentProfile": "fast-dev-test"}},
    )
    result = CliRunner().invoke(cli.app, [*argv, "--no-live"])
    assert result.exit_code == 0, result.output
    assert calls == []
    assert "Configured deployment profile: fast-dev-test" in result.stdout
    assert "configuration only" in result.stdout
    assert "Recorded acceptance" not in result.stdout


def test_accepted_testdev_waivers_are_visible_without_show_checks(status_cli, monkeypatch):
    from nebius_cxcli.soperator_adapter import compile_upstream_soperator_values
    from test_soperator_checks_gpu_shapes import values_for
    from test_soperator_upstream_adapter import _RELEASE

    _, _, argv = status_cli
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=_RELEASE)
    validation = {
        "readiness": "passed",
        "extended": "waived",
        "contract": {"diagnostics": compiled["cxcliDiagnostics"]},
    }
    monkeypatch.setattr(
        cli,
        "read_local_deployment_record",
        lambda _: SimpleNamespace(
            value={
                "accepted": {
                    "generation": "example",
                    "evidence": {
                        "acceptanceControl": {"outcomes": {"example": {"validation": validation}}}
                    },
                }
            }
        ),
    )
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    output = " ".join(result.stdout.split())
    assert "Fast deploy" in output
    assert "Dev/Test only" in output
    assert "GPU health and performance qualification are disabled" in output
