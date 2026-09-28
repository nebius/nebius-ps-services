from __future__ import annotations

import copy
from io import StringIO

import pytest
from rich.console import Console

from nebius_cxcli.soperator_status_health import (
    HEALTH_STYLES,
    ComponentHealth,
    StatusHealthReport,
    project_status_health,
)
from nebius_cxcli.soperator_status_render import (
    print_health_table,
    print_installed_release,
    print_overall_health,
)
from status_health_fakes import add_node_configurator, healthy_snapshot, reference, resource


def test_healthy_components_have_no_details_and_no_duplicate_version() -> None:
    snapshot = healthy_snapshot()
    report = project_status_health(snapshot)
    assert report.overall == "Healthy", report
    assert all(row.state == "Healthy" and not row.detail for row in report.components)
    output = StringIO()
    console = Console(file=output, width=100, force_terminal=False)
    print_installed_release(console, snapshot["status_release"])
    print_health_table(console, report)
    print_overall_health(console, report.overall)
    text = output.getvalue()
    assert "4.1.7+build" in text and "Version discrepancy" not in text
    assert "Overall health: Healthy" in text.splitlines()[-1]
    assert "\x1b" not in text


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("IDLE", "Healthy"),
        ("MIXED", "Healthy"),
        ("ALLOCATED", "Healthy"),
        ("IDLE+DRAIN", "Degraded"),
        ("DOWN", "Degraded"),
        ("IDLE*", "Degraded"),
        ("NEW_STATE", "Unknown"),
    ],
)
def test_slurm_worker_state_and_operational_impact(state: str, expected: str) -> None:
    snapshot = healthy_snapshot()
    snapshot["slurm_nodes"]["nodes"]["worker-0"]["State"] = state
    report = project_status_health(snapshot)
    row = next(r for r in report.components if r.component == "Workers: worker")
    assert row.state == expected
    if expected == "Degraded":
        assert "capacity is reduced" in row.detail
    if expected == "Healthy":
        assert row.detail == ""


def test_missing_worker_and_failed_query_are_distinct() -> None:
    snapshot = healthy_snapshot()
    snapshot["slurm_nodes"]["nodes"] = {}
    assert project_status_health(snapshot).overall == "Unhealthy"
    snapshot["slurm_nodes"] = {"state": "failed"}
    snapshot["status_issues"] = ["Slurm query timed out"]
    report = project_status_health(snapshot)
    assert report.overall == "Unknown"
    assert "partial report" in report.summary


def test_stale_workload_cannot_be_healthy_but_unversioned_cr_conditions_can() -> None:
    snapshot = healthy_snapshot()
    snapshot["soperator_resources"][0]["status"]["conditions"] = [
        {"type": "ControllersAvailable", "status": "True"}
    ]
    assert project_status_health(snapshot).overall == "Healthy"
    snapshot["workloads"][0]["metadata"]["generation"] = 2
    assert project_status_health(snapshot).overall == "Unknown"


def test_foreign_owner_and_placeholder_do_not_fill_missing_controller_replicas() -> None:
    snapshot = healthy_snapshot()
    main = snapshot["workloads"][0]
    main["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    placeholder = copy.deepcopy(main)
    placeholder["kind"] = "DaemonSet"
    placeholder["metadata"]["labels"]["slurm.nebius.ai/controller-type"] = "placeholder"
    placeholder["metadata"]["ownerReferences"] = [reference(snapshot["soperator_resources"][0])]
    snapshot["workloads"].append(placeholder)
    row = next(
        r for r in project_status_health(snapshot).components if r.component == "Slurm controllers"
    )
    assert row.state == "Unhealthy" and row.ready == 0


def test_terminating_pod_and_failed_readiness_have_impact() -> None:
    snapshot = healthy_snapshot()
    pod = next(
        r for r in snapshot["workloads"] if r["kind"] == "Pod" and r["metadata"]["name"] == "rest-0"
    )
    pod["metadata"]["deletionTimestamp"] = "2026-09-16T12:00:00Z"
    row = next(r for r in project_status_health(snapshot).components if r.component == "Slurm REST")
    assert row.state == "Unhealthy" and "REST clients" in row.detail


def test_database_failure_and_disabled_exporter() -> None:
    snapshot = healthy_snapshot()
    snapshot["soperator_resources"][0]["spec"]["slurmNodes"]["exporter"]["enabled"] = False
    snapshot["soperator_resources"][2]["status"]["conditions"][0]["status"] = "False"
    report = project_status_health(snapshot)
    assert report.overall == "Unhealthy"
    assert next(r for r in report.components if r.component == "Slurm exporter").state == "Disabled"
    assert (
        "job history"
        in next(r for r in report.components if r.component == "Accounting database").detail
    )


@pytest.mark.parametrize(
    ("states", "issues", "expected"),
    [
        ([], [], "Unknown"),
        (["Disabled", "Scaled to zero"], [], "Unknown"),
        (["Healthy", "Disabled"], [], "Healthy"),
        (["Healthy"], ["timeout"], "Unknown"),
        (["Degraded", "Unknown"], ["timeout"], "Degraded"),
        (["Unhealthy", "Degraded", "Unknown"], [], "Unhealthy"),
        (["Error", "Unhealthy"], [], "Error"),
    ],
)
def test_overall_precedence(states: list[str], issues: list[str], expected: str) -> None:
    report = StatusHealthReport(
        tuple(ComponentHealth(str(i), state) for i, state in enumerate(states)), tuple(issues)
    )
    assert report.overall == expected


def test_historical_checks_do_not_determine_current_health() -> None:
    snapshot = healthy_snapshot()
    check = resource("ActiveCheck", "native", slurmClusterRefName="soperator", checkType="slurmJob")
    check["status"]["slurmJobsStatus"] = {
        "lastRunStatus": "Failed",
        "lastRunSubmitTime": "2026-09-15T12:00:00Z",
    }
    snapshot["soperator_resources"].append(check)
    report = project_status_health(snapshot)
    assert report.overall == "Healthy"
    assert report.history.records[0].result == "Failed"


@pytest.mark.parametrize(
    "mismatch", ["storage_namespace", "release_name", "namespace", "stale_generation", "duplicate"]
)
def test_operator_identity_and_generation_remain_strict(mismatch: str) -> None:
    snapshot = healthy_snapshot()
    operator = next(
        r
        for r in snapshot["workloads"]
        if r["kind"] == "Deployment" and r["metadata"]["name"] == "operator"
    )
    if mismatch == "storage_namespace":
        operator["metadata"]["annotations"]["meta.helm.sh/release-namespace"] = "flux-system"
    elif mismatch == "release_name":
        operator["metadata"]["annotations"]["meta.helm.sh/release-name"] = "foreign"
    elif mismatch == "namespace":
        operator["metadata"]["namespace"] = "foreign"
    elif mismatch == "stale_generation":
        operator["metadata"]["generation"] = 2
    else:
        snapshot["workloads"].append(copy.deepcopy(operator))
    row = next(
        r for r in project_status_health(snapshot).components if r.component == "Soperator operator"
    )
    assert row.state == "Unknown"


def test_node_configurator_requires_current_workload_and_immutable_pod_ownership() -> None:
    snapshot = healthy_snapshot()
    add_node_configurator(snapshot)
    ds = next(r for r in snapshot["workloads"] if r["kind"] == "DaemonSet")
    ds["status"]["observedGeneration"] = 0
    assert project_status_health(snapshot).overall == "Unknown"
    ds["status"]["observedGeneration"] = ds["metadata"]["generation"]
    pod = next(r for r in snapshot["workloads"] if r["metadata"]["name"] == "prepare-0")
    pod["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    row = next(
        r
        for r in project_status_health(snapshot).components
        if r.component.startswith("Node configuration")
    )
    assert row.state == "Degraded" and row.ready == 4


@pytest.mark.parametrize("state", ["IDLE+CLOUD", "ALLOCATED+CLOUD", "MIXED+CLOUD"])
def test_cloud_modifier_does_not_degrade_active_workers(state: str) -> None:
    snapshot = healthy_snapshot()
    snapshot["slurm_nodes"]["nodes"]["worker-0"]["State"] = state
    assert project_status_health(snapshot).overall == "Healthy"


@pytest.mark.parametrize("active", [[1], []])
def test_dynamic_capacity_uses_exact_owned_active_ordinals(active: list[int]) -> None:
    snapshot = healthy_snapshot()
    nodeset = snapshot["soperator_resources"][1]
    nodeset["spec"]["ephemeralNodes"] = True
    power = resource("NodeSetPowerState", "worker", nodeSetRef="worker", activeNodes=active)
    power["metadata"]["ownerReferences"] = [reference(nodeset)]
    snapshot["soperator_resources"].append(power)
    workload = next(
        w
        for w in snapshot["workloads"]
        if w["kind"] == "StatefulSet" and w["metadata"]["name"] == "worker"
    )
    workload["spec"]["replicas"] = len(active)
    workload["status"]["readyReplicas"] = len(active)
    snapshot["workloads"] = [
        w
        for w in snapshot["workloads"]
        if not (
            w["kind"] == "Pod"
            and w["metadata"]["name"].startswith("worker-")
            and int(w["metadata"]["name"].split("-")[1]) not in active
        )
    ]
    snapshot["slurm_nodes"]["nodes"]["worker-0"]["State"] = "IDLE+CLOUD+POWERED_DOWN"
    report = project_status_health(snapshot)
    worker = next(r for r in report.components if r.component == "Workers: worker")
    assert worker.state == ("Healthy" if active else "Scaled to zero")
    assert report.overall == "Healthy"
    power["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    assert project_status_health(snapshot).overall == "Unknown"


def test_terminating_database_and_failed_release_cannot_be_healthy() -> None:
    snapshot = healthy_snapshot()
    snapshot["soperator_resources"][2]["metadata"]["deletionTimestamp"] = "2026-09-16T12:00:00Z"
    assert project_status_health(snapshot).overall == "Degraded"
    snapshot["status_release"]["status"] = "failed"
    assert project_status_health(snapshot).overall == "Unhealthy"


def test_maintenance_is_visible_and_does_not_use_normal_replica_expectations() -> None:
    snapshot = healthy_snapshot()
    snapshot["soperator_resources"][0]["spec"]["maintenance"] = "downscale"
    snapshot["soperator_resources"][2]["spec"]["replicas"] = 0
    for workload in snapshot["workloads"]:
        if (
            workload["kind"] in {"Deployment", "StatefulSet"}
            and workload["metadata"]["name"] != "operator"
        ):
            workload["spec"]["replicas"] = 0
            workload["status"]["readyReplicas"] = 0
    report = project_status_health(snapshot)
    assert report.overall == "Degraded"
    assert (
        next(r for r in report.components if r.component == "Slurm controllers").state
        == "Scaled to zero"
    )
    assert (
        next(r for r in report.components if r.component == "Slurm scheduling").state == "Degraded"
    )


@pytest.mark.parametrize("state", list(HEALTH_STYLES))
def test_terminal_applies_each_status_style_without_column_override(state: str) -> None:
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=True,
        color_system="standard",
        _environ={"TERM": "xterm"},
        width=120,
    )
    print_health_table(
        console, StatusHealthReport((ComponentHealth("Example", state, detail="attention"),))
    )
    print_overall_health(console, state)
    style = console.get_style(HEALTH_STYLES[state])
    assert style.render(state, color_system=console._color_system) in output.getvalue()


def test_no_color_sanitization_and_real_version_discrepancy() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, _environ={"NO_COLOR": "1"}, width=50)
    print_health_table(
        console,
        StatusHealthReport(
            (ComponentHealth("[red]injected\x1b\nname", "Healthy", detail="hidden"),)
        ),
    )
    print_installed_release(
        console, {"chart_version": "4.1.8+build", "app_version": "4.1.7", "status": "deployed"}
    )
    text = output.getvalue()
    assert "\x1b[32m" not in text and "\x1b[33m" not in text
    assert "hidden" not in text
    assert "[red]injected" in text
    assert "Version discrepancy" in text
