from __future__ import annotations

import asyncio
import threading

import pytest
from rich.console import Console
from rich.text import Text
from textual.widgets import DataTable, Footer, Static

from nebius_cxcli.slurm_job_control import (
    SLURM_JOB_CONTROL_ACTION_PROMPT,
    SLURM_JOB_CONTROL_HELP,
    SLURM_JOB_CONTROL_WAIT_COMPLETED,
    SlurmJobControlRefreshError,
    build_slurm_jobs_table,
    create_slurm_job_control_app,
    normalize_slurm_job_control_action,
    prompt_slurm_job_control,
    slurm_job_control_pending_selected_ids,
    slurm_job_control_selected_ids_for_action,
)
from nebius_cxcli.slurm_jobs import AffectedSlurmJob, slurm_remaining_seconds


def _job(
    job_id: str,
    *,
    state: str = "RUNNING",
    allocated_nodes: str = "worker-0",
    reason: str = "",
    remaining: str = "30:00",
    impact_scope: str = "allocated-node",
) -> AffectedSlurmJob:
    return AffectedSlurmJob(
        job_id=job_id,
        user="root",
        state=state,
        partition="main",
        allocated_nodes=allocated_nodes,
        requested_nodes="",
        scheduled_nodes="",
        reason=reason,
        elapsed="00:05",
        limit="35:00",
        remaining=remaining,
        name="sop-gpu-job-test-01",
        impact_scope=impact_scope,
    )


def test_slurm_job_control_action_keys_reserve_a_for_select_all() -> None:
    assert normalize_slurm_job_control_action("c") == "cancel-selected"
    assert normalize_slurm_job_control_action("C") == "cancel-all"
    assert normalize_slurm_job_control_action("q") == "requeue-selected"
    assert normalize_slurm_job_control_action("Q") == "requeue-all"
    assert normalize_slurm_job_control_action("h") == "requeue-hold-selected"
    assert normalize_slurm_job_control_action("H") == "requeue-hold-all"
    assert normalize_slurm_job_control_action("a") == ""
    assert normalize_slurm_job_control_action("i") == ""


def test_slurm_job_control_all_requeue_actions_select_only_active_jobs() -> None:
    active = _job("41")
    pending = _job(
        "44",
        state="PENDING",
        allocated_nodes="",
        reason="Priority",
        impact_scope="pending-partition",
    )
    jobs = (active, pending)

    assert slurm_job_control_selected_ids_for_action("cancel-all", jobs, ()) == ("41", "44")
    assert slurm_job_control_selected_ids_for_action("requeue-all", jobs, ()) == ("41",)
    assert slurm_job_control_selected_ids_for_action("requeue-hold-all", jobs, ()) == ("41",)
    assert slurm_job_control_pending_selected_ids(jobs, ("41", "44")) == ("44",)


def test_slurm_jobs_table_renders_aligned_columns_and_selection_marks() -> None:
    jobs = (
        _job("41"),
        _job("44", state="PENDING", allocated_nodes="", reason="Priority"),
    )
    table = build_slurm_jobs_table(
        jobs,
        title="Affected Slurm jobs",
        selected_job_ids=("44",),
        include_selection=True,
    )

    assert [column.header for column in table.columns][:5] == [
        "Sel",
        "Job",
        "User",
        "State",
        "Partition",
    ]
    console = Console(record=True, width=180, color_system=None)
    console.print(table)
    rendered = console.export_text()
    assert "Affected Slurm jobs" in rendered
    assert "[x]" in rendered
    assert "Priority" in rendered


def test_slurm_job_control_text_fallback_uses_visible_action_keys() -> None:
    jobs = (
        _job("41"),
        _job("44", state="PENDING", allocated_nodes="", reason="Priority"),
    )
    prompts: list[tuple[str, str, bool]] = []
    answers = iter(["44", "C"])

    def _prompt(message: str, default: str, show_default: bool) -> str:
        prompts.append((message, default, show_default))
        return next(answers)

    action, selected = prompt_slurm_job_control(
        jobs,
        console=Console(record=True, width=180, color_system=None),
        table_title="Affected Slurm jobs",
        is_tty=False,
        text_prompt=_prompt,
    )

    assert action == "cancel-all"
    assert selected == ("41", "44")
    assert prompts[1][0] == SLURM_JOB_CONTROL_ACTION_PROMPT
    assert prompts[1][1:] == ("r", True)


def test_slurm_job_control_tui_failure_falls_back_to_text_prompt() -> None:
    jobs = (_job("41"),)
    answers = iter(["41", "q"])

    def _prompt(_message: str, _default: str, _show_default: bool) -> str:
        return next(answers)

    def _broken_tui(_jobs, title: str):
        assert title == "Affected Slurm jobs"
        raise RuntimeError("no terminal")

    console = Console(record=True, width=180, color_system=None)
    action, selected = prompt_slurm_job_control(
        jobs,
        console=console,
        table_title="Affected Slurm jobs",
        is_tty=True,
        text_prompt=_prompt,
        tui_runner=_broken_tui,
    )

    assert action == "requeue-selected"
    assert selected == ("41",)
    assert "Interactive TUI unavailable" in console.export_text()


def test_slurm_job_control_refresh_failure_does_not_fall_back_to_stale_prompt() -> None:
    prompts: list[str] = []

    def _prompt(message: str, _default: str, _show_default: bool) -> str:
        prompts.append(message)
        return ""

    def _broken_refresh(_jobs, _title: str):
        raise SlurmJobControlRefreshError("squeue failed")

    with pytest.raises(SlurmJobControlRefreshError, match="squeue failed"):
        prompt_slurm_job_control(
            (_job("41"),),
            console=Console(record=True, width=180, color_system=None),
            table_title="Affected Slurm jobs",
            is_tty=True,
            text_prompt=_prompt,
            tui_runner=_broken_refresh,
        )

    assert prompts == []


def test_slurm_job_control_textual_app_returns_selected_action() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"), _job("44", state="PENDING")),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("space")
            await pilot.press("c")
        return app.return_value

    result = asyncio.run(_run())

    assert result.action == "cancel-selected"
    assert result.selected_job_ids == ("41",)


def test_slurm_job_control_textual_app_uses_visible_selection_mark() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)) as pilot:
            table = app.query_one("#jobs", DataTable)
            unselected = table.get_row("41")[0]
            await pilot.press("space")
            selected = table.get_row("41")[0]
            return unselected, selected

    unselected, selected = asyncio.run(_run())

    assert isinstance(unselected, Text)
    assert isinstance(selected, Text)
    assert unselected.plain == "---"
    assert selected.plain == "YES"
    assert "white" in str(selected.style)
    assert "green" in str(selected.style)


def test_slurm_job_control_textual_app_uses_single_concise_legend() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)):
            help_widget = app.query_one("#help", Static)
            return str(help_widget.content), len(list(app.query(Footer)))

    help_text, footer_count = asyncio.run(_run())

    assert footer_count == 0
    assert "Actions:" in help_text
    assert "w wait" in help_text
    assert "H requeue+hold all active" in help_text
    assert "Selection:" in help_text
    assert help_text == SLURM_JOB_CONTROL_HELP


def test_slurm_job_control_textual_app_ticks_remaining_time() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("41", remaining="0:05"),),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(1.25)
            table = app.query_one("#jobs", DataTable)
            return table.get_row("41")[9]

    remaining = asyncio.run(_run())

    assert isinstance(remaining, str)
    seconds = slurm_remaining_seconds(remaining)
    assert seconds is not None
    assert seconds < 5


def test_slurm_job_control_textual_wait_stays_in_app_until_jobs_clear() -> None:
    snapshots = [
        (_job("41", remaining="0:03"),),
        (),
    ]

    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        return snapshots.pop(0) if snapshots else ()

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41", remaining="0:04"),),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            wait_timeout_seconds=5,
            poll_interval_seconds=1,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("w")
            assert app.return_value is None
            await pilot.pause(2.25)
        return app.return_value

    result = asyncio.run(_run())

    assert result is not None
    assert result.action == SLURM_JOB_CONTROL_WAIT_COMPLETED
    assert result.selected_job_ids == ()


def test_slurm_job_control_textual_wait_returns_refresh_errors() -> None:
    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        raise RuntimeError("squeue failed")

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            wait_timeout_seconds=5,
            poll_interval_seconds=1,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("w")
            await pilot.pause(0.1)
        return app.return_value

    result = asyncio.run(_run())

    assert result is not None
    assert isinstance(result.error, SlurmJobControlRefreshError)
    assert "squeue failed" in str(result.error)


def test_slurm_job_control_textual_wait_can_abort_while_poll_is_running() -> None:
    started = threading.Event()
    release = threading.Event()

    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        started.set()
        release.wait(1)
        return (_job("41"),)

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            wait_timeout_seconds=5,
            poll_interval_seconds=1,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("w")
            assert await asyncio.to_thread(started.wait, 1)
            await pilot.press("x")
            release.set()
            await pilot.pause(0.1)
        return app.return_value

    result = asyncio.run(_run())

    assert result is not None
    assert result.action == "abort"


def test_slurm_job_control_textual_app_preserves_display_order_for_selected_jobs() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("2"), _job("10")),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("a")
            await pilot.press("c")
        return app.return_value

    result = asyncio.run(_run())

    assert result.action == "cancel-selected"
    assert result.selected_job_ids == ("2", "10")


def test_slurm_job_control_textual_app_keeps_a_and_i_as_selection_keys() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"), _job("44", state="PENDING")),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("a")
            await pilot.press("i")
            await pilot.press("a")
            await pilot.press("upper_c")
        return app.return_value

    result = asyncio.run(_run())

    assert result.action == "cancel-all"
    assert result.selected_job_ids == ("41", "44")
