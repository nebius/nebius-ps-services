"""Interactive Slurm job-control UI shared by Soperator workflows."""

from __future__ import annotations

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
    "space select | a select/clear all | i invert | r refresh | w wait | "
    "c cancel selected | C cancel all | q requeue selected | Q requeue all active | "
    "h requeue-hold selected | H requeue-hold all active | x abort | ? help"
)

TextPrompt = Callable[[str, str, bool], str]
TuiRunner = Callable[[Sequence[AffectedSlurmJob], str], tuple[str, tuple[str, ...]] | None]


@dataclass(frozen=True)
class SlurmJobControlResult:
    action: str
    selected_job_ids: tuple[str, ...]


def slurm_table_value(value: str) -> str:
    return value if str(value or "").strip() else "-"


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
        remaining_seconds = slurm_remaining_seconds(job.remaining)
        countdown = (
            "unknown"
            if remaining_seconds is None
            else format_slurm_duration_seconds(remaining_seconds - local_elapsed_seconds)
        )
        table.add_row(
            job.job_id,
            job.state,
            job.partition,
            slurm_table_value(job.allocated_nodes),
            slurm_table_value(job.requested_nodes),
            slurm_table_value(job.reason),
            countdown,
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
) -> tuple[str, tuple[str, ...]]:
    if is_tty:
        try:
            runner = tui_runner or run_slurm_job_control_tui
            result = runner(tuple(jobs), table_title)
            if result is not None:
                return result
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
) -> Any:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import DataTable, Footer, Static

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

        def compose(self) -> ComposeResult:
            yield Static(self.screen_title, id="title")
            yield DataTable(id="jobs")
            yield Static(self._status_text(), id="status")
            yield Static(SLURM_JOB_CONTROL_HELP, id="help")
            yield Footer()

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
            for job in self.affected_jobs:
                table.add_row(
                    "[ ]",
                    job.job_id,
                    job.state,
                    job.user,
                    job.partition,
                    slurm_table_value(job.allocated_nodes),
                    slurm_table_value(job.requested_nodes),
                    slurm_table_value(job.scheduled_nodes),
                    slurm_table_value(job.reason),
                    job.remaining or "unknown",
                    job.impact_scope,
                    job.name,
                    key=job.job_id,
                )
            table.focus()
            self._refresh_status()

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

        def _refresh_selection_marks(self) -> None:
            table = self.query_one("#jobs", DataTable)
            for job in self.affected_jobs:
                table.update_cell(
                    job.job_id,
                    "selected",
                    "[x]" if job.job_id in self.selected_job_ids else "[ ]",
                )
            self._refresh_status()

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

        def action_choose(self, action: str) -> None:
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
) -> tuple[str, tuple[str, ...]] | None:
    result = create_slurm_job_control_app(tuple(jobs), title=title).run()
    if result is None:
        return None
    return (result.action, result.selected_job_ids)
