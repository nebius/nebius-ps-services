"""Interactive Slurm job-control UI shared by Soperator workflows."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .slurm_jobs import (
    AffectedSlurmJob,
    format_slurm_duration_seconds,
    slurm_job_is_active,
    slurm_job_is_pending,
    slurm_remaining_seconds,
)

SLURM_JOB_CONTROL_ACTION_ALIASES = {
    "r": "refresh",
    "refresh": "refresh",
    "w": "wait-to-finish",
    "wait": "wait-to-finish",
    "wait-to-finish": "wait-to-finish",
    "c": "cancel-selected",
    "cancel": "cancel-selected",
    "cancel-selected": "cancel-selected",
    "C": "cancel-all",
    "all": "cancel-all",
    "cancel-all": "cancel-all",
    "q": "requeue-selected",
    "requeue": "requeue-selected",
    "requeue-selected": "requeue-selected",
    "Q": "requeue-all",
    "requeue-all": "requeue-all",
    "h": "requeue-hold-selected",
    "hold": "requeue-hold-selected",
    "requeue-hold": "requeue-hold-selected",
    "requeue-hold-selected": "requeue-hold-selected",
    "H": "requeue-hold-all",
    "requeue-hold-all": "requeue-hold-all",
    "x": "abort",
    "abort": "abort",
}
SLURM_JOB_CONTROL_ACTION_PROMPT = (
    "Action [r refresh, w wait-to-finish, c cancel selected, C cancel all displayed, "
    "q requeue selected, Q requeue all active, h requeue-hold selected, "
    "H requeue-hold all active, x abort]"
)
SLURM_JOB_CONTROL_HELP = (
    "Actions: w wait | c cancel selected | C cancel all | q requeue selected | "
    "Q requeue all active | h requeue+hold selected | H requeue+hold all active | "
    "r refresh | x abort\n"
    "Selection: space toggle | a all | i invert | ? legend"
)

TextPrompt = Callable[[str, str, bool], str]
TuiRunner = Callable[[Sequence[AffectedSlurmJob], str], tuple[str, tuple[str, ...]] | None]
JobsProvider = Callable[[], Sequence[AffectedSlurmJob]]

SLURM_JOB_CONTROL_WAIT_COMPLETED = "wait-completed"
SLURM_JOB_CONTROL_WAIT_TIMEOUT = "wait-timeout"


class SlurmJobControlRefreshError(RuntimeError):
    """Raised when the interactive job-control screen cannot refresh Slurm jobs."""


@dataclass(frozen=True)
class SlurmJobControlResult:
    action: str
    selected_job_ids: tuple[str, ...]
    error: BaseException | None = None


def slurm_table_value(value: str) -> str:
    return value if str(value or "").strip() else "-"


def _slurm_remaining_display(job: AffectedSlurmJob, *, local_elapsed_seconds: int) -> str:
    remaining_seconds = slurm_remaining_seconds(job.remaining)
    if remaining_seconds is None:
        return "unknown"
    return format_slurm_duration_seconds(remaining_seconds - local_elapsed_seconds)


def _slurm_textual_selection_mark(selected: bool) -> Text:
    if selected:
        return Text("YES", style="bold white on green")
    return Text("---", style="dim")


def normalize_slurm_job_control_action(raw_action: str) -> str:
    text = str(raw_action or "").strip()
    if not text:
        text = "r"
    return SLURM_JOB_CONTROL_ACTION_ALIASES.get(
        text,
        SLURM_JOB_CONTROL_ACTION_ALIASES.get(text.lower(), ""),
    )


def slurm_job_control_active_job_ids(jobs: Sequence[AffectedSlurmJob]) -> tuple[str, ...]:
    return tuple(job.job_id for job in jobs if slurm_job_is_active(job))


def slurm_job_control_pending_selected_ids(
    jobs: Sequence[AffectedSlurmJob],
    selected_job_ids: Sequence[str],
) -> tuple[str, ...]:
    selected = {str(job_id or "").strip() for job_id in selected_job_ids if str(job_id or "").strip()}
    return tuple(job.job_id for job in jobs if job.job_id in selected and slurm_job_is_pending(job))


def slurm_job_control_selected_ids_for_action(
    action: str,
    jobs: Sequence[AffectedSlurmJob],
    selected_job_ids: Sequence[str],
) -> tuple[str, ...]:
    if action == "cancel-all":
        return tuple(job.job_id for job in jobs)
    if action in {"requeue-all", "requeue-hold-all"}:
        return slurm_job_control_active_job_ids(jobs)
    return tuple(
        str(job_id or "").strip() for job_id in selected_job_ids if str(job_id or "").strip()
    )


def build_slurm_jobs_table(
    jobs: Sequence[AffectedSlurmJob],
    *,
    title: str,
    selected_job_ids: Sequence[str] = (),
    include_selection: bool = False,
) -> Table:
    selected = {str(job_id or "").strip() for job_id in selected_job_ids if str(job_id or "").strip()}
    table = Table(title=title)
    if include_selection:
        table.add_column("Sel", no_wrap=True)
    for column in (
        "Job",
        "User",
        "State",
        "Partition",
        "Allocated",
        "Requested",
        "Scheduled",
        "Reason",
        "Elapsed",
        "Limit",
        "Remaining",
        "Scope",
        "Name",
    ):
        justify = "right" if column in {"Job", "Elapsed", "Limit", "Remaining"} else "left"
        table.add_column(column, justify=justify, overflow="ellipsis")
    for job in jobs:
        cells: list[Any] = [
            job.job_id,
            job.user,
            job.state,
            job.partition,
            slurm_table_value(job.allocated_nodes),
            slurm_table_value(job.requested_nodes),
            slurm_table_value(job.scheduled_nodes),
            slurm_table_value(job.reason),
            job.elapsed,
            job.limit,
            job.remaining,
            job.impact_scope,
            job.name,
        ]
        if include_selection:
            cells.insert(0, Text("[x]" if job.job_id in selected else "[ ]"))
        table.add_row(*cells)
    return table


def build_slurm_wait_dashboard(
    jobs: Sequence[AffectedSlurmJob],
    *,
    local_elapsed_seconds: int,
    poll_interval_seconds: int,
) -> Table:
    table = Table(
        title=(
            "Waiting for affected Slurm jobs "
            f"(polling squeue every {max(poll_interval_seconds, 1)}s)"
        )
    )
    for column in (
        "Job",
        "State",
        "Partition",
        "Allocated",
        "Requested",
        "Reason",
        "Remaining",
        "Scope",
        "Name",
    ):
        justify = "right" if column in {"Job", "Remaining"} else "left"
        table.add_column(column, justify=justify, overflow="ellipsis")
    for job in jobs:
        table.add_row(
            job.job_id,
            job.state,
            job.partition,
            slurm_table_value(job.allocated_nodes),
            slurm_table_value(job.requested_nodes),
            slurm_table_value(job.reason),
            _slurm_remaining_display(job, local_elapsed_seconds=local_elapsed_seconds),
            job.impact_scope,
            job.name,
        )
    return table


def prompt_slurm_job_control(
    jobs: Sequence[AffectedSlurmJob],
    *,
    console: Console,
    table_title: str,
    is_tty: bool,
    text_prompt: TextPrompt,
    tui_runner: TuiRunner | None = None,
    jobs_provider: JobsProvider | None = None,
    wait_timeout_seconds: int = 0,
    poll_interval_seconds: int = 30,
) -> tuple[str, tuple[str, ...]]:
    if is_tty:
        try:
            if tui_runner is not None:
                result = tui_runner(tuple(jobs), table_title)
            else:
                result = run_slurm_job_control_tui(
                    tuple(jobs),
                    table_title,
                    jobs_provider=jobs_provider,
                    wait_timeout_seconds=wait_timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )
            if result is not None:
                return result
        except SlurmJobControlRefreshError:
            raise
        except Exception as exc:
            console.print(f"[yellow]Interactive TUI unavailable, using text fallback: {exc}[/yellow]")

    console.print(build_slurm_jobs_table(jobs, title=table_title))
    raw_ids = text_prompt("Job IDs to select, comma separated (blank for none)", "", False)
    selected_ids = tuple(item.strip() for item in raw_ids.split(",") if item.strip())
    raw_action = text_prompt(SLURM_JOB_CONTROL_ACTION_PROMPT, "r", True)
    action = normalize_slurm_job_control_action(raw_action)
    return (action or "", slurm_job_control_selected_ids_for_action(action, jobs, selected_ids))


def create_slurm_job_control_app(
    jobs: Sequence[AffectedSlurmJob],
    *,
    title: str,
    jobs_provider: JobsProvider | None = None,
    wait_timeout_seconds: int = 0,
    poll_interval_seconds: int = 30,
) -> Any:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import DataTable, Static
    from textual.worker import WorkerState

    class SlurmJobControlApp(App[SlurmJobControlResult | None]):
        CSS = """
        Screen {
            layout: vertical;
        }
        #title {
            text-style: bold;
            padding: 0 1;
        }
        #jobs {
            height: 1fr;
        }
        #jobs > .datatable--cursor {
            background: $accent;
            color: white;
            text-style: bold;
        }
        #jobs > .datatable--fixed-cursor {
            background: $accent;
            color: white;
            text-style: bold;
        }
        #status {
            padding: 0 1;
            color: yellow;
        }
        #help {
            padding: 0 1;
        }
        """
        BINDINGS = [
            Binding("space", "toggle_selected", "Select", key_display="space"),
            Binding("a", "toggle_all", "All"),
            Binding("i", "invert_selection", "Invert"),
            Binding("r", "choose('refresh')", "Refresh"),
            Binding("w", "choose('wait-to-finish')", "Wait"),
            Binding("c", "choose('cancel-selected')", "Cancel Sel"),
            Binding("upper_c", "choose('cancel-all')", "Cancel All", key_display="C"),
            Binding("q", "choose('requeue-selected')", "Requeue Sel"),
            Binding("upper_q", "choose('requeue-all')", "Requeue Active", key_display="Q"),
            Binding("h", "choose('requeue-hold-selected')", "Hold Sel"),
            Binding("upper_h", "choose('requeue-hold-all')", "Hold Active", key_display="H"),
            Binding("x", "choose('abort')", "Abort"),
            Binding("question_mark", "show_help", "Help", key_display="?"),
        ]

        def __init__(self, affected_jobs: Sequence[AffectedSlurmJob], screen_title: str) -> None:
            super().__init__()
            self.affected_jobs = tuple(affected_jobs)
            self.screen_title = screen_title
            self.selected_job_ids: set[str] = set()
            self.jobs_provider = jobs_provider
            self.wait_timeout_seconds = max(0, int(wait_timeout_seconds))
            self.poll_interval_seconds = max(1, int(poll_interval_seconds))
            self.waiting = False
            self.wait_started_at: float | None = None
            self.refreshing = False
            self.jobs_refreshed_at = time.monotonic()
            self.last_poll_at = self.jobs_refreshed_at

        def compose(self) -> ComposeResult:
            yield Static(self.screen_title, id="title")
            yield DataTable(id="jobs")
            yield Static(self._status_text(), id="status")
            yield Static(SLURM_JOB_CONTROL_HELP, id="help")

        def on_mount(self) -> None:
            table = self.query_one("#jobs", DataTable)
            table.cursor_type = "row"
            table.zebra_stripes = True
            for header, key in (
                ("Sel", "selected"),
                ("Job", "job_id"),
                ("State", "state"),
                ("User", "user"),
                ("Part", "partition"),
                ("Allocated", "allocated"),
                ("Requested", "requested"),
                ("Scheduled", "scheduled"),
                ("Reason", "reason"),
                ("Remaining", "remaining"),
                ("Scope", "scope"),
                ("Name", "name"),
            ):
                table.add_column(header, key=key)
            self._replace_jobs(self.affected_jobs, focus=False)
            table.focus()
            self._refresh_status()
            self.set_interval(1, self._tick, name="slurm-job-control-tick")

        def _current_job(self) -> AffectedSlurmJob | None:
            table = self.query_one("#jobs", DataTable)
            coordinate = table.cursor_coordinate
            row_index = int(getattr(coordinate, "row", 0))
            if row_index < 0 or row_index >= len(self.affected_jobs):
                return None
            return self.affected_jobs[row_index]

        def _status_text(self, detail: str = "") -> str:
            active_count = len(slurm_job_control_active_job_ids(self.affected_jobs))
            pending_count = sum(1 for job in self.affected_jobs if slurm_job_is_pending(job))
            base = (
                f"Selected {len(self.selected_job_ids)}/{len(self.affected_jobs)} | "
                f"Active {active_count} | Pending {pending_count}. "
                "Pending jobs cannot be requeued; cancel, wait, select active jobs, or abort."
            )
            return f"{base} {detail}".strip()

        def _refresh_status(self, detail: str = "") -> None:
            self.query_one("#status", Static).update(self._status_text(detail))

        def _job_row_cells(self, job: AffectedSlurmJob) -> tuple[Any, ...]:
            return (
                _slurm_textual_selection_mark(job.job_id in self.selected_job_ids),
                job.job_id,
                job.state,
                job.user,
                job.partition,
                slurm_table_value(job.allocated_nodes),
                slurm_table_value(job.requested_nodes),
                slurm_table_value(job.scheduled_nodes),
                slurm_table_value(job.reason),
                _slurm_remaining_display(job, local_elapsed_seconds=self._local_elapsed_seconds()),
                job.impact_scope,
                job.name,
            )

        def _replace_jobs(self, jobs: Sequence[AffectedSlurmJob], *, focus: bool = True) -> None:
            self.affected_jobs = tuple(jobs)
            displayed_ids = {job.job_id for job in self.affected_jobs}
            self.selected_job_ids.intersection_update(displayed_ids)
            self.jobs_refreshed_at = time.monotonic()
            table = self.query_one("#jobs", DataTable)
            table.clear()
            for job in self.affected_jobs:
                table.add_row(*self._job_row_cells(job), key=job.job_id)
            if focus:
                table.focus()
            self._refresh_status()

        def _local_elapsed_seconds(self) -> int:
            return max(0, int(time.monotonic() - self.jobs_refreshed_at))

        def _refresh_selection_marks(self) -> None:
            table = self.query_one("#jobs", DataTable)
            for job in self.affected_jobs:
                table.update_cell(
                    job.job_id,
                    "selected",
                    _slurm_textual_selection_mark(job.job_id in self.selected_job_ids),
                )
            self._refresh_status()

        def _refresh_remaining_cells(self) -> None:
            table = self.query_one("#jobs", DataTable)
            local_elapsed = self._local_elapsed_seconds()
            for job in self.affected_jobs:
                table.update_cell(
                    job.job_id,
                    "remaining",
                    _slurm_remaining_display(job, local_elapsed_seconds=local_elapsed),
                )

        def _wait_timed_out(self) -> bool:
            if not self.waiting or self.wait_started_at is None or self.wait_timeout_seconds <= 0:
                return False
            return time.monotonic() - self.wait_started_at >= self.wait_timeout_seconds

        def _poll_jobs_blocking(self) -> tuple[AffectedSlurmJob, ...]:
            if self.jobs_provider is None:
                return self.affected_jobs
            return tuple(self.jobs_provider())

        def _schedule_poll(self, *, reason: str) -> None:
            if self.jobs_provider is None:
                return
            if self.refreshing:
                self._refresh_status("Slurm refresh already in progress.")
                return
            self.refreshing = True
            self._refresh_status("Refreshing affected Slurm jobs...")
            self.run_worker(
                self._poll_jobs_blocking,
                name="slurm-job-control-poll",
                group="slurm-job-control-poll",
                description=reason,
                exclusive=True,
                thread=True,
                exit_on_error=False,
            )

        def on_worker_state_changed(self, event: Any) -> None:
            if getattr(event.worker, "name", "") != "slurm-job-control-poll":
                return
            if event.state not in {WorkerState.CANCELLED, WorkerState.ERROR, WorkerState.SUCCESS}:
                return
            self.refreshing = False
            if event.state == WorkerState.CANCELLED:
                self._refresh_status("Slurm refresh cancelled.")
                return
            if event.state == WorkerState.ERROR:
                exc = event.worker.error
                self.exit(
                    SlurmJobControlResult(
                        action="wait-to-finish" if self.waiting else "refresh",
                        selected_job_ids=(),
                        error=SlurmJobControlRefreshError(
                            "Could not refresh affected Slurm jobs: "
                            + (str(exc) if exc is not None else "unknown error")
                        ),
                    )
                )
                return
            jobs = tuple(event.worker.result or ())
            self.last_poll_at = time.monotonic()
            if not jobs:
                action = (
                    SLURM_JOB_CONTROL_WAIT_COMPLETED
                    if self.waiting
                    else "refresh"
                )
                self.exit(SlurmJobControlResult(action=action, selected_job_ids=()))
                return
            self._replace_jobs(jobs)

        def _tick(self) -> None:
            self._refresh_remaining_cells()
            if not self.waiting:
                return
            if self._wait_timed_out():
                self.exit(
                    SlurmJobControlResult(
                        action=SLURM_JOB_CONTROL_WAIT_TIMEOUT,
                        selected_job_ids=tuple(job.job_id for job in self.affected_jobs),
                    )
                )
                return
            if self.refreshing:
                self._refresh_status("Waiting for all affected jobs; squeue poll in progress.")
                return
            now = time.monotonic()
            next_poll_seconds = max(
                0,
                self.poll_interval_seconds - int(now - self.last_poll_at),
            )
            self._refresh_status(
                f"Waiting for all affected jobs; next squeue poll in {next_poll_seconds}s. "
                "Press x to abort."
            )
            if now - self.last_poll_at >= self.poll_interval_seconds:
                self._schedule_poll(reason="wait-to-finish")

        def action_toggle_selected(self) -> None:
            job = self._current_job()
            if job is None:
                return
            if job.job_id in self.selected_job_ids:
                self.selected_job_ids.remove(job.job_id)
            else:
                self.selected_job_ids.add(job.job_id)
            self._refresh_selection_marks()

        def action_toggle_all(self) -> None:
            all_ids = {job.job_id for job in self.affected_jobs}
            self.selected_job_ids = set() if self.selected_job_ids == all_ids else all_ids
            self._refresh_selection_marks()

        def action_invert_selection(self) -> None:
            all_ids = {job.job_id for job in self.affected_jobs}
            self.selected_job_ids = all_ids.difference(self.selected_job_ids)
            self._refresh_selection_marks()

        def action_show_help(self) -> None:
            self._refresh_status(SLURM_JOB_CONTROL_HELP)

        def _start_waiting(self) -> None:
            if self.jobs_provider is None:
                selected = tuple(
                    job.job_id for job in self.affected_jobs if job.job_id in self.selected_job_ids
                )
                self.exit(
                    SlurmJobControlResult(
                        action="wait-to-finish",
                        selected_job_ids=selected,
                    )
                )
                return
            self.waiting = True
            self.wait_started_at = time.monotonic()
            self._refresh_status(
                "Waiting for all affected jobs in this screen. Press x to abort."
            )
            self._schedule_poll(reason="wait-to-finish")

        def action_choose(self, action: str) -> None:
            if action == "refresh" and self.jobs_provider is not None:
                self._schedule_poll(reason="refresh")
                return
            if action == "wait-to-finish":
                self._start_waiting()
                return
            selected_job_ids = tuple(
                job.job_id for job in self.affected_jobs if job.job_id in self.selected_job_ids
            )
            selected = slurm_job_control_selected_ids_for_action(
                action,
                self.affected_jobs,
                selected_job_ids,
            )
            if action in {"cancel-selected", "requeue-selected", "requeue-hold-selected"} and not selected:
                self._refresh_status("Select at least one displayed job for this action.")
                return
            if action in {"requeue-selected", "requeue-hold-selected"}:
                pending = slurm_job_control_pending_selected_ids(self.affected_jobs, selected)
                if pending:
                    self._refresh_status(
                        "Pending jobs cannot be requeued: " + ", ".join(pending)
                    )
                    return
            if action in {"requeue-all", "requeue-hold-all"} and not selected:
                self._refresh_status("No displayed active jobs are available to requeue.")
                return
            self.exit(SlurmJobControlResult(action=action, selected_job_ids=selected))

    return SlurmJobControlApp(tuple(jobs), title)


def run_slurm_job_control_tui(
    jobs: Sequence[AffectedSlurmJob],
    title: str,
    *,
    jobs_provider: JobsProvider | None = None,
    wait_timeout_seconds: int = 0,
    poll_interval_seconds: int = 30,
) -> tuple[str, tuple[str, ...]] | None:
    result = create_slurm_job_control_app(
        tuple(jobs),
        title=title,
        jobs_provider=jobs_provider,
        wait_timeout_seconds=wait_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    ).run()
    if result is None:
        return None
    if result.error is not None:
        raise result.error
    return (result.action, result.selected_job_ids)
