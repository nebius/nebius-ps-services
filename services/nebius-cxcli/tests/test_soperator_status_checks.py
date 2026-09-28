from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from nebius_cxcli.soperator_status_checks import (
    CHECK_STYLES,
    CheckHistory,
    RecordedCheck,
    project_check_history,
)
from nebius_cxcli.soperator_status_render import print_check_history
from status_health_fakes import resource


def check(name="example", *, check_type="k8sJob", result="Complete", suspended=True):
    item = resource(
        "ActiveCheck",
        name,
        checkType=check_type,
        suspend=suspended,
        slurmClusterRefName="soperator",
    )
    subtree, field = (
        ("slurmJobsStatus", "lastRunStatus")
        if check_type == "slurmJob"
        else ("k8sJobsStatus", "lastJobStatus")
    )
    item["status"][subtree] = {field: result}
    return item


@pytest.mark.parametrize(
    ("kind", "native", "category"),
    [
        ("k8sJob", "Active", "running"),
        ("slurmJob", "InProgress", "running"),
        ("k8sJob", "Pending", "pending"),
        ("k8sJob", "Suspended", "suspended results"),
        ("k8sJob", "Unknown", "unverified results"),
        ("slurmJob", "Complete", "complete"),
        ("slurmJob", "Failed", "failures"),
        ("slurmJob", "Error", "failures"),
        ("slurmJob", "Cancelled", "cancelled"),
        ("slurmJob", "NEW_STATUS", "unverified results"),
        ("slurmJob", "", "suspended without results"),
    ],
)
def test_official_check_outcomes_are_distinct(kind, native, category) -> None:
    history = project_check_history({"state": "collected"}, [check(check_type=kind, result=native)])
    assert history.records[0].category == category


def test_only_declared_type_selects_status_and_absent_type_uses_api_default() -> None:
    item = check(check_type="slurmJob", result="Failed")
    item["status"]["k8sJobsStatus"] = {"lastJobStatus": "Complete"}
    assert project_check_history({"state": "collected"}, [item]).records[0].result == "Failed"
    item["spec"]["checkType"] = "invalid"
    row = project_check_history({"state": "collected"}, [item]).records[0]
    assert row.result == "Unrecognized check type" and row.timestamps == ()
    del item["spec"]["checkType"]
    assert project_check_history({"state": "collected"}, [item]).records[0].result == "Complete"


def test_no_results_and_suspended_schedules_do_not_override_recorded_completion() -> None:
    history = project_check_history(
        {"state": "collected"},
        [check("done"), check("paused", result=""), check("waiting", result="", suspended=False)],
    )
    assert (
        history.summary
        == "1 complete · 0 failures · 1 suspended without results · 1 without recorded results"
    )
    assert history.records[0].result == "Complete"


def test_one_shot_and_failed_check_timestamps_retain_their_actual_meanings() -> None:
    item = check(result="Failed")
    item["status"]["k8sJobsStatus"].update(
        lastJobSuccessfulTime="2026-09-15T10:00:00Z", lastTransitionTime="2026-09-16T12:00:00Z"
    )
    row = project_check_history({"state": "collected"}, [item]).records[0]
    assert row.result == "Failed"
    assert row.timestamps == (
        ("Last successful", "2026-09-15T10:00:00Z"),
        ("Status updated", "2026-09-16T12:00:00Z"),
    )
    item["status"]["k8sJobsStatus"].update(
        lastJobScheduleTime="0001-01-01T00:00:00Z",
        lastJobSuccessfulTime="bad timestamp",
        lastTransitionTime=None,
    )
    assert project_check_history({"state": "collected"}, [item]).records[0].timestamps == ()
    item = check(check_type="slurmJob")
    item["status"]["slurmJobsStatus"]["lastRunSubmitTime"] = "2026-09-16T12:00:00Z"
    assert project_check_history({"state": "collected"}, [item]).records[0].timestamps == (
        ("Submitted", "2026-09-16T12:00:00Z"),
    )


def test_default_history_is_bounded_and_expanded_history_is_complete() -> None:
    records = [check(f"failed-{i}", result="Failed") for i in reversed(range(7))]
    records += [check("completed-one-shot"), check("paused-without-result", result="")]
    history = project_check_history({"state": "collected"}, records)
    out = StringIO()
    print_check_history(Console(file=out, width=120), history)
    text = out.getvalue()
    assert "1 complete · 7 failures · 1 suspended without results" in text
    assert "failed-0" in text and "failed-4" in text and "failed-5" not in text
    assert "2 more recorded check issues; use --show-checks" in text
    assert "completed-one-shot" not in text and "paused-without-result" not in text
    out = StringIO()
    print_check_history(Console(file=out, width=120), history, show_checks=True)
    text = out.getvalue()
    assert all(r["metadata"]["name"] in text for r in records)
    assert "last run" not in text and "unknown" not in text
    assert "2 more" not in text


@pytest.mark.parametrize("state", ["collected", "unavailable", "not_installed", "not_checked"])
def test_empty_and_unavailable_history_are_never_conflated(state) -> None:
    history = project_check_history({"state": state, "detail": "observation detail"}, [])
    out = StringIO()
    print_check_history(Console(file=out), history, show_checks=True)
    text = out.getvalue()
    assert ("No recorded checks" in text) == (state == "collected")
    assert ("Warning:" in text) == (state == "unavailable")
    assert "0 failures" not in text


@pytest.mark.parametrize("result", list(CHECK_STYLES))
@pytest.mark.parametrize("no_color", [False, True])
def test_recorded_result_colors_and_plain_text_safety(result, no_color) -> None:
    out = StringIO()
    console = Console(
        file=out,
        force_terminal=True,
        color_system="standard",
        width=100,
        _environ={"NO_COLOR": "1"} if no_color else {"TERM": "xterm"},
    )
    history = CheckHistory(
        "collected", "", (RecordedCheck("[red]literal\nname", "k8sJob", result, True),)
    )
    print_check_history(console, history, show_checks=True)
    text = out.getvalue()
    assert "[red]literal name" in text
    if no_color:
        assert "\x1b[31m" not in text and "\x1b[32m" not in text
    else:
        style = console.get_style(CHECK_STYLES[result])
        colored_word = style.render(result, color_system=console._color_system).removesuffix(
            "\x1b[0m"
        )
        assert colored_word in text  # Rich may include cell padding before the reset.
