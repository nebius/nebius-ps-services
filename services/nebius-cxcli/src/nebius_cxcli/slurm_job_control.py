"""Interactive Slurm job-control UI shared by Soperator workflows."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .slurm_action_journal import (
    SLURM_ACTION_APPLIED,
    validate_slurm_action_journal,
)
from .slurm_jobs import (
    AffectedSlurmJob,
    clean_slurm_value,
    format_slurm_duration_seconds,
    slurm_job_is_active,
    slurm_job_is_pending,
    slurm_job_is_terminating,
    slurm_remaining_seconds,
)

SLURM_JOB_CONTROL_ACTION_ALIASES = {
    "r": "refresh",
    "refresh": "refresh",
    "w": "wait",
    "wait": "wait",
    "c": "cancel",
    "cancel": "cancel",
    "q": "requeue",
    "requeue": "requeue",
    "h": "hold",
    "hold": "hold",
    "H": "requeue-hold",
    "requeue-hold": "requeue-hold",
    "u": "release",
    "release": "release",
}
SLURM_JOB_CONTROL_ACTION_PROMPT = (
    "Action [r refresh, w wait, c cancel, q requeue, h hold, H requeue-and-hold, u release]"
)
SLURM_JOB_CONTROL_KEYS = (
    "Keys: Esc exit | space select | a all | i invert | r refresh | w wait | "
    "c cancel | q requeue | h hold | H requeue-and-hold | u release | ? help"
)
SLURM_JOB_CONTROL_HELP = (
    "Slurm job controls\n\n"
    "Screen\n"
    "Esc: leave this screen without changing any jobs.\n\n"
    "Selection\n"
    "space: select or clear the highlighted row.\n"
    "a: select all displayed jobs, or clear all when every row is selected.\n"
    "i: invert the current selection.\n\n"
    "Actions\n"
    "r: refresh the displayed affected jobs.\n"
    "w: wait in this screen until all affected jobs finish or clear.\n"
    "c: cancel selected displayed jobs with scancel.\n"
    "q: requeue selected active jobs with scontrol requeue.\n"
    "h: hold selected jobs with scontrol hold.\n"
    "H: requeue and hold selected active jobs with scontrol requeuehold.\n"
    "u: release selected held jobs with scontrol release.\n\n"
    "Cancel cleanup\n"
    "After scancel, Slurm may show a job as COMPLETING while the job processes "
    "stop and the allocated nodes return to service. cxcli keeps that job visible "
    "and blocking until Slurm clears it from the affected node list.\n\n"
    "Pending jobs\n"
    "A Pending (PD) job has not started yet. Pending jobs can be cancelled, "
    "but cxcli does not requeue them because they are already in the scheduler queue.\n\n"
    "Requeue\n"
    "Requeue stops the current execution and places the job back into the scheduler "
    "queue so it can receive another scheduling opportunity later.\n\n"
    "Hold and Requeue + Hold\n"
    "Hold keeps an eligible job from being scheduled until an operator releases it.\n"
    "Requeue + hold requeues the job and places it on hold. The scheduler will not "
    "run the held job until an operator releases it.\n\n"
    "Controller gaps\n"
    "cxcli durably queues actions while controller RPCs are unavailable. The Authority, "
    "Connectivity, Snapshot age, and Action result fields show whether an action is queued, "
    "dispatching, applied, rejected, or indeterminate."
)

TextPrompt = Callable[[str, str, bool], str]
TuiRunner = Callable[[Sequence[AffectedSlurmJob], str], tuple[str, tuple[str, ...]] | None]
JobsProvider = Callable[[], Sequence[AffectedSlurmJob]]
SlurmActionJournalProvider = Callable[[], Mapping[str, Any]]
JobActionHandler = Callable[
    [str, tuple[str, ...], tuple[AffectedSlurmJob, ...]],
    Sequence[AffectedSlurmJob],
]

SLURM_JOB_CONTROL_ACTION_APPLIED = "action-applied"
SLURM_JOB_CONTROL_EXIT = "exit"
SLURM_JOB_CONTROL_JOBS_CHANGED = "jobs-changed"
SLURM_JOB_CONTROL_JOBS_CLEARED = "jobs-cleared"
SLURM_JOB_CONTROL_WAIT_COMPLETED = "wait-completed"
SLURM_JOB_CONTROL_WAIT_TIMEOUT = "wait-timeout"

_SLURM_ACTION_BINDING_FIELDS = (
    "job_id",
    "user_id",
    "submit_time",
    "restart_baseline",
    "identity_fingerprint",
    "lineage_fingerprint",
)


class SlurmJobControlRefreshError(RuntimeError):
    """Raised when the interactive job-control screen cannot refresh Slurm jobs."""


@dataclass(frozen=True)
class SlurmJobControlResult:
    action: str
    selected_job_ids: tuple[str, ...]
    error: BaseException | None = None


def slurm_table_value(value: str) -> str:
    return clean_slurm_value(value) or "-"


def _slurm_remaining_display(job: AffectedSlurmJob, *, local_elapsed_seconds: int) -> str:
    remaining_seconds = slurm_remaining_seconds(job.remaining)
    if remaining_seconds is None:
        return "unknown"
    return format_slurm_duration_seconds(remaining_seconds - local_elapsed_seconds)


def _slurm_textual_selection_mark(selected: bool) -> Text:
    if selected:
        return Text("YES", style="bold white on green")
    return Text("---", style="dim")


def _slurm_state_text(job: AffectedSlurmJob) -> Text:
    state = slurm_table_value(job.state)
    if slurm_job_is_terminating(job):
        return Text(state, style="bold yellow")
    if slurm_job_is_pending(job):
        return Text(state, style="cyan")
    if slurm_job_is_active(job):
        return Text(state, style="green")
    return Text(state, style="dim")


def normalize_slurm_job_control_action(raw_action: str) -> str:
    text = str(raw_action or "").strip()
    if not text:
        text = "r"
    return SLURM_JOB_CONTROL_ACTION_ALIASES.get(text, "")


def slurm_job_control_active_job_ids(jobs: Sequence[AffectedSlurmJob]) -> tuple[str, ...]:
    return tuple(job.job_id for job in jobs if slurm_job_is_active(job))


def slurm_job_control_pending_selected_ids(
    jobs: Sequence[AffectedSlurmJob],
    selected_job_ids: Sequence[str],
) -> tuple[str, ...]:
    selected = {
        str(job_id or "").strip() for job_id in selected_job_ids if str(job_id or "").strip()
    }
    return tuple(job.job_id for job in jobs if job.job_id in selected and slurm_job_is_pending(job))


def slurm_job_control_selected_ids_for_action(
    action: str,
    jobs: Sequence[AffectedSlurmJob],
    selected_job_ids: Sequence[str],
) -> tuple[str, ...]:
    if action in {"refresh", "wait"}:
        return tuple(job.job_id for job in jobs)
    displayed = {job.job_id for job in jobs}
    return tuple(
        normalized
        for job_id in selected_job_ids
        if (normalized := str(job_id or "").strip()) and normalized in displayed
    )


def slurm_job_control_controller_status(journal: Mapping[str, Any] | None) -> str:
    """Render the durable controller/action-broker observation for the TUI."""

    if journal is None:
        return (
            "Authority unknown | Connectivity unknown | Snapshot age unknown | Broker unavailable"
        )
    validate_slurm_action_journal(journal)
    controller = journal["controller"]
    if not isinstance(controller, Mapping):
        raise ValueError("Slurm action journal controller observation must be a mapping.")
    authority = str(controller.get("authority", "") or "unknown")
    connectivity = str(controller.get("connectivity", "") or "unknown")
    snapshot_age = controller.get("snapshot_age_seconds")
    age_text = "unknown" if snapshot_age is None else f"{int(snapshot_age)}s"
    broker_mode = str(journal.get("broker_mode", "") or "unknown")
    login = journal["login"]
    if not isinstance(login, Mapping):
        raise ValueError("Slurm action journal login observation must be a mapping.")
    login_state = str(login.get("state", "") or "unknown")
    protected_pods = int(login.get("protected_pod_count", 0) or 0)
    active_sessions = int(login.get("active_session_count", 0) or 0)
    target_state = "ready" if login.get("target_ready") is True else "pending"
    status = (
        f"Authority {authority} | Connectivity {connectivity} | "
        f"Snapshot age {age_text} | Broker {broker_mode} | "
        f"Login {login_state} ({protected_pods} protected Pod(s), "
        f"{active_sessions} active session(s), target {target_state})"
    )
    confirmation_requests = login.get("exit_confirmation_requests", ())
    if isinstance(confirmation_requests, Sequence) and not isinstance(
        confirmation_requests, (str, bytes, bytearray)
    ):
        fingerprints = tuple(
            str(request.get("socket_fingerprint") or "")
            for request in confirmation_requests
            if isinstance(request, Mapping) and request.get("socket_fingerprint")
        )
        if fingerprints:
            status += " | Explicit SSH-exit confirmation required: " + ", ".join(fingerprints)
    return status


def slurm_job_control_action_result(
    journal: Mapping[str, Any] | None,
    job_id: str,
) -> str:
    """Render the newest action for the current immutable Slurm job lineage."""

    if journal is None:
        return "-"
    validate_slurm_action_journal(journal)
    normalized_job_id = str(job_id or "").strip()
    current_binding: Mapping[str, Any] | None = None
    snapshots = journal.get("job_snapshots")
    if snapshots is not None:
        if not isinstance(snapshots, Mapping):
            return "-"
        snapshot = snapshots.get(normalized_job_id)
        if not isinstance(snapshot, Mapping):
            return "-"
        raw_current_binding = snapshot.get("binding")
        if not isinstance(raw_current_binding, Mapping):
            return "-"
        current_binding = raw_current_binding
    actions = journal.get("actions", ())
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, bytearray)):
        raise ValueError("Slurm action journal actions must be a list.")
    for raw_action in reversed(actions):
        if not isinstance(raw_action, Mapping):
            continue
        binding = raw_action.get("binding")
        if not isinstance(binding, Mapping) or binding.get("job_id") != normalized_job_id:
            continue
        if current_binding is not None and any(
            binding.get(field) != current_binding.get(field)
            for field in _SLURM_ACTION_BINDING_FIELDS
        ):
            continue
        kind = str(raw_action.get("kind", "") or "unknown")
        state = str(raw_action.get("state", "") or "unknown")
        result = str(raw_action.get("result", "") or "").strip()
        return f"{kind}: {state}" + (f" - {result}" if result else "")
    return "-"


def build_slurm_jobs_table(
    jobs: Sequence[AffectedSlurmJob],
    *,
    title: str,
    selected_job_ids: Sequence[str] = (),
    include_selection: bool = False,
) -> Table:
    selected = {
        str(job_id or "").strip() for job_id in selected_job_ids if str(job_id or "").strip()
    }
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
            _slurm_state_text(job),
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
            _slurm_state_text(job),
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
    action_handler: JobActionHandler | None = None,
    action_journal_provider: SlurmActionJournalProvider | None = None,
    wait_timeout_seconds: int = 0,
    poll_interval_seconds: int = 30,
    exit_after_action: bool = False,
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
                    action_handler=action_handler,
                    action_journal_provider=action_journal_provider,
                    wait_timeout_seconds=wait_timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    exit_after_action=exit_after_action,
                )
            if result is not None:
                return result
            return (SLURM_JOB_CONTROL_EXIT, ())
        except SlurmJobControlRefreshError:
            raise
        except Exception as exc:
            console.print(
                f"[yellow]Interactive TUI unavailable, using text fallback: {exc}[/yellow]"
            )

    journal = action_journal_provider() if action_journal_provider is not None else None
    console.print(slurm_job_control_controller_status(journal))
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
    action_handler: JobActionHandler | None = None,
    action_journal_provider: SlurmActionJournalProvider | None = None,
    wait_timeout_seconds: int = 0,
    poll_interval_seconds: int = 30,
    exit_after_action: bool = False,
) -> Any:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import DataTable, Static
    from textual.worker import WorkerState

    class SlurmJobHelpScreen(ModalScreen[None]):
        CSS = """
        SlurmJobHelpScreen {
            align: center middle;
            background: $background 70%;
        }
        #help-dialog {
            width: 90%;
            height: 80%;
            border: heavy $accent;
            background: $surface;
            padding: 1 2;
        }
        #help-text {
            width: 1fr;
        }
        """
        BINDINGS = [
            Binding("escape", "close_help", "Close"),
            Binding("question_mark", "close_help", "Close", key_display="?", priority=True),
        ]

        def compose(self) -> ComposeResult:
            yield VerticalScroll(
                Static(SLURM_JOB_CONTROL_HELP, id="help-text"),
                id="help-dialog",
            )

        def on_key(self, event: Any) -> None:
            if getattr(event, "key", "") == "question_mark":
                event.stop()
                self.app.help_screen_open = False
                self.app.pop_screen()

        def action_close_help(self) -> None:
            self.app.help_screen_open = False
            self.app.pop_screen()

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
        #controller-status {
            padding: 0 1;
            color: cyan;
        }
        #keys {
            padding: 0 1;
            color: $text-muted;
        }
        """
        BINDINGS = [
            Binding("escape", "exit_jobs", "Exit", key_display="Esc"),
            Binding("space", "toggle_selected", "Select", key_display="space"),
            Binding("a", "toggle_all", "All"),
            Binding("i", "invert_selection", "Invert"),
            Binding("r", "choose('refresh')", "Refresh"),
            Binding("w", "choose('wait')", "Wait"),
            Binding("c", "choose('cancel')", "Cancel"),
            Binding("q", "choose('requeue')", "Requeue"),
            Binding("h", "choose('hold')", "Hold"),
            Binding(
                "upper_h",
                "choose('requeue-hold')",
                "Requeue + Hold",
                key_display="H",
            ),
            Binding("u", "choose('release')", "Release"),
            Binding("question_mark", "show_slurm_help", "Help", key_display="?", priority=True),
        ]

        def __init__(self, affected_jobs: Sequence[AffectedSlurmJob], screen_title: str) -> None:
            super().__init__()
            self.affected_jobs = tuple(affected_jobs)
            self.screen_title = screen_title
            self.selected_job_ids: set[str] = set()
            self.jobs_provider = jobs_provider
            self.action_handler = action_handler
            self.action_journal_provider = action_journal_provider
            self.wait_timeout_seconds = max(0, int(wait_timeout_seconds))
            self.poll_interval_seconds = max(1, int(poll_interval_seconds))
            self.exit_after_action = bool(exit_after_action)
            self.waiting = False
            self.wait_started_at: float | None = None
            self.refreshing = False
            self.action_running = False
            self.help_screen_open = False
            self.running_action = ""
            self.running_action_job_ids: tuple[str, ...] = ()
            self.running_action_displayed_jobs: tuple[AffectedSlurmJob, ...] = ()
            self.running_action_known_ids: frozenset[str] = frozenset()
            self.jobs_refreshed_at = time.monotonic()
            self.last_poll_at = self.jobs_refreshed_at

        def compose(self) -> ComposeResult:
            yield Static(self.screen_title, id="title")
            yield Static(self._controller_status_text(), id="controller-status")
            yield DataTable(id="jobs")
            yield Static(self._status_text(), id="status")
            yield Static(SLURM_JOB_CONTROL_KEYS, id="keys")

        def on_mount(self) -> None:
            table = self.query_one("#jobs", DataTable)
            table.cursor_type = "row"
            table.zebra_stripes = True
            for header, key in (
                ("Sel", "selected"),
                ("Job", "job_id"),
                ("User", "user"),
                ("State", "state"),
                ("Partition", "partition"),
                ("Allocated", "allocated"),
                ("Requested", "requested"),
                ("Scheduled", "scheduled"),
                ("Reason", "reason"),
                ("Elapsed", "elapsed"),
                ("Limit", "limit"),
                ("Remaining", "remaining"),
                ("Scope", "scope"),
                ("Name", "name"),
                ("Action result", "action_result"),
            ):
                table.add_column(header, key=key)
            self._replace_jobs(self.affected_jobs, focus=False)
            table.focus()
            self._refresh_status()
            self.set_interval(1, self._tick, name="slurm-job-control-tick")

        def on_key(self, event: Any) -> None:
            if getattr(event, "key", "") == "question_mark":
                event.stop()
                if self.help_screen_open:
                    self.help_screen_open = False
                    self.pop_screen()
                else:
                    self.action_show_slurm_help()

        def _current_job(self) -> AffectedSlurmJob | None:
            table = self.query_one("#jobs", DataTable)
            coordinate = table.cursor_coordinate
            row_index = int(getattr(coordinate, "row", 0))
            if row_index < 0 or row_index >= len(self.affected_jobs):
                return None
            return self.affected_jobs[row_index]

        def _journal_snapshot(self) -> Mapping[str, Any] | None:
            if self.action_journal_provider is None:
                return None
            journal = self.action_journal_provider()
            validate_slurm_action_journal(journal)
            return journal

        def _controller_status_text(self) -> str:
            return slurm_job_control_controller_status(self._journal_snapshot())

        def _running_action_records(
            self,
            journal: Mapping[str, Any] | None,
        ) -> tuple[Mapping[str, Any], ...]:
            if journal is None:
                return ()
            return tuple(
                raw_action
                for raw_action in journal.get("actions", ())
                if isinstance(raw_action, Mapping)
                and str(raw_action.get("action_id", "") or "") not in self.running_action_known_ids
                and isinstance(raw_action.get("binding"), Mapping)
                and raw_action["binding"].get("job_id") in self.running_action_job_ids
                and raw_action.get("kind") == self.running_action
            )

        def _status_text(self, detail: str = "") -> str:
            active_count = len(slurm_job_control_active_job_ids(self.affected_jobs))
            pending_count = sum(1 for job in self.affected_jobs if slurm_job_is_pending(job))
            terminating_count = sum(
                1 for job in self.affected_jobs if slurm_job_is_terminating(job)
            )
            base = (
                f"Selected {len(self.selected_job_ids)}/{len(self.affected_jobs)} | "
                f"Active {active_count}"
            )
            if terminating_count:
                base += f" ({terminating_count} completing)"
            base += (
                f" | Pending {pending_count}. "
                "Completing jobs are still freeing nodes; pending jobs cannot be requeued."
            )
            return f"{base} {detail}".strip()

        def _refresh_status(self, detail: str = "") -> None:
            self.query_one("#controller-status", Static).update(self._controller_status_text())
            self.query_one("#status", Static).update(self._status_text(detail))

        def _job_row_cells(self, job: AffectedSlurmJob) -> tuple[Any, ...]:
            return (
                _slurm_textual_selection_mark(job.job_id in self.selected_job_ids),
                job.job_id,
                job.user,
                _slurm_state_text(job),
                job.partition,
                slurm_table_value(job.allocated_nodes),
                slurm_table_value(job.requested_nodes),
                slurm_table_value(job.scheduled_nodes),
                slurm_table_value(job.reason),
                job.elapsed,
                job.limit,
                _slurm_remaining_display(job, local_elapsed_seconds=self._local_elapsed_seconds()),
                job.impact_scope,
                job.name,
                slurm_job_control_action_result(self._journal_snapshot(), job.job_id),
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
            journal = self._journal_snapshot()
            for job in self.affected_jobs:
                table.update_cell(
                    job.job_id,
                    "remaining",
                    _slurm_remaining_display(job, local_elapsed_seconds=local_elapsed),
                )
                table.update_cell(
                    job.job_id,
                    "action_result",
                    slurm_job_control_action_result(journal, job.job_id),
                )
            self.query_one("#controller-status", Static).update(
                slurm_job_control_controller_status(journal)
            )

        def _wait_timed_out(self) -> bool:
            if not self.waiting or self.wait_started_at is None or self.wait_timeout_seconds <= 0:
                return False
            return time.monotonic() - self.wait_started_at >= self.wait_timeout_seconds

        def _poll_jobs_blocking(self) -> tuple[AffectedSlurmJob, ...]:
            if self.jobs_provider is None:
                return self.affected_jobs
            return tuple(self.jobs_provider())

        def _run_action_blocking(self) -> tuple[AffectedSlurmJob, ...]:
            if self.action_handler is None:
                return self.affected_jobs
            return tuple(
                self.action_handler(
                    self.running_action,
                    self.running_action_job_ids,
                    self.running_action_displayed_jobs,
                )
            )

        def _schedule_poll(self, *, reason: str) -> None:
            if self.jobs_provider is None:
                return
            if self.refreshing or self.action_running:
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

        def _schedule_action(self, action: str, selected: tuple[str, ...]) -> None:
            if self.action_handler is None:
                self.exit(SlurmJobControlResult(action=action, selected_job_ids=selected))
                return
            if self.action_running:
                self._refresh_status("Slurm action already in progress.")
                return
            self.action_running = True
            self.running_action = action
            self.running_action_job_ids = selected
            self.running_action_displayed_jobs = self.affected_jobs
            journal = self._journal_snapshot()
            self.running_action_known_ids = frozenset(
                str(raw_action.get("action_id", "") or "")
                for raw_action in (() if journal is None else journal.get("actions", ()))
                if isinstance(raw_action, Mapping)
            )
            self._refresh_status(
                f"Recording durable {action} intent for displayed job(s): " + ", ".join(selected)
            )
            self.run_worker(
                self._run_action_blocking,
                name="slurm-job-control-action",
                group="slurm-job-control-action",
                description=action,
                exclusive=True,
                thread=True,
                exit_on_error=False,
            )

        def _exit_jobs_cleared(self) -> None:
            self._refresh_status("No affected Slurm jobs remain; continuing upgrade.")
            action = (
                SLURM_JOB_CONTROL_WAIT_COMPLETED if self.waiting else SLURM_JOB_CONTROL_JOBS_CLEARED
            )
            self.set_timer(
                0.25,
                lambda: self.exit(
                    SlurmJobControlResult(
                        action=action,
                        selected_job_ids=(),
                    )
                ),
            )

        def on_worker_state_changed(self, event: Any) -> None:
            worker_name = getattr(event.worker, "name", "")
            if worker_name not in {"slurm-job-control-poll", "slurm-job-control-action"}:
                return
            if event.state not in {WorkerState.CANCELLED, WorkerState.ERROR, WorkerState.SUCCESS}:
                return
            was_waiting = self.waiting
            if worker_name == "slurm-job-control-poll":
                self.refreshing = False
            else:
                self.action_running = False
            if event.state == WorkerState.CANCELLED:
                self._refresh_status("Slurm worker cancelled.")
                return
            if event.state == WorkerState.ERROR:
                exc = event.worker.error
                detail = str(exc) if exc is not None else "unknown error"
                if worker_name == "slurm-job-control-action":
                    self._refresh_status(
                        "Action adapter could not update the durable journal; no action result "
                        f"was claimed: {detail}"
                    )
                else:
                    self.last_poll_at = time.monotonic()
                    self._refresh_status(
                        "Controller unavailable; durable actions remain queued. "
                        f"Refresh failed: {detail}"
                    )
                return
            jobs = tuple(event.worker.result or ())
            journal = self._journal_snapshot()
            matching_actions = (
                self._running_action_records(journal)
                if worker_name == "slurm-job-control-action"
                else ()
            )
            action_applied = journal is None or (
                bool(matching_actions)
                and all(
                    str(raw_action.get("state", "") or "") == SLURM_ACTION_APPLIED
                    for raw_action in matching_actions
                )
            )
            self.last_poll_at = time.monotonic()
            use_jobs = worker_name == "slurm-job-control-poll" or action_applied
            if use_jobs and not jobs:
                self._exit_jobs_cleared()
                return
            if worker_name == "slurm-job-control-poll" and self.exit_after_action:
                previous_job_ids = tuple(job.job_id for job in self.affected_jobs)
                current_job_ids = tuple(job.job_id for job in jobs)
                if current_job_ids != previous_job_ids:
                    self.exit(
                        SlurmJobControlResult(
                            action=SLURM_JOB_CONTROL_JOBS_CHANGED,
                            selected_job_ids=(),
                        )
                    )
                    return
            if use_jobs:
                self._replace_jobs(jobs)
            else:
                self._refresh_remaining_cells()
                self._refresh_status()
            if worker_name == "slurm-job-control-action":
                if self.exit_after_action:
                    if not action_applied:
                        self._refresh_status(
                            "Action intent is durable and remains visible until it reaches a "
                            "definitive result."
                        )
                        return
                    self.exit(
                        SlurmJobControlResult(
                            action=SLURM_JOB_CONTROL_ACTION_APPLIED,
                            selected_job_ids=self.running_action_job_ids,
                        )
                    )
                    return
                self._refresh_status(
                    "Action intent recorded; see Action result for its durable state."
                )
                if was_waiting and not self.refreshing:
                    self._schedule_poll(reason="wait")

        def _tick(self) -> None:
            self._refresh_remaining_cells()
            if self.action_running:
                self._refresh_status(f"Running {self.running_action}; waiting for Slurm to update.")
                return
            now = time.monotonic()
            if not self.waiting:
                if self.jobs_provider is None:
                    return
                if self.refreshing:
                    self._refresh_status("Refreshing affected Slurm jobs...")
                    return
                next_poll_seconds = max(
                    0,
                    self.poll_interval_seconds - int(now - self.last_poll_at),
                )
                self._refresh_status(
                    f"Monitoring affected jobs; next squeue poll in {next_poll_seconds}s. "
                    "Press ? for help or w to wait."
                )
                if now - self.last_poll_at >= self.poll_interval_seconds:
                    self._schedule_poll(reason="monitor")
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
            next_poll_seconds = max(
                0,
                self.poll_interval_seconds - int(now - self.last_poll_at),
            )
            self._refresh_status(
                f"Waiting for all affected jobs; next squeue poll in {next_poll_seconds}s."
            )
            if now - self.last_poll_at >= self.poll_interval_seconds:
                self._schedule_poll(reason="wait")

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

        def action_show_slurm_help(self) -> None:
            if self.help_screen_open:
                return
            self.help_screen_open = True
            self.push_screen(SlurmJobHelpScreen())

        def _start_waiting(self) -> None:
            selected = tuple(job.job_id for job in self.affected_jobs)
            if self.action_handler is not None:
                self._schedule_action("wait", selected)
            elif self.jobs_provider is None:
                self.exit(
                    SlurmJobControlResult(
                        action="wait",
                        selected_job_ids=selected,
                    )
                )
                return
            self.waiting = True
            self.wait_started_at = time.monotonic()
            self._refresh_status("Waiting for all affected jobs in this screen.")
            if not self.action_running:
                self._schedule_poll(reason="wait")

        def action_choose(self, action: str) -> None:
            if action == "refresh":
                selected = tuple(job.job_id for job in self.affected_jobs)
                if self.jobs_provider is not None:
                    self._schedule_poll(reason=action)
                else:
                    self._schedule_action(action, selected)
                return
            if action == "wait":
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
            if action in {"cancel", "requeue", "hold", "requeue-hold", "release"} and not selected:
                self._refresh_status("Select at least one displayed job for this action.")
                return
            if action in {"requeue", "requeue-hold"}:
                pending = slurm_job_control_pending_selected_ids(self.affected_jobs, selected)
                if pending:
                    self._refresh_status("Pending jobs cannot be requeued: " + ", ".join(pending))
                    return
            self._schedule_action(action, selected)

        def action_exit_jobs(self) -> None:
            if self.action_running:
                self._refresh_status("Wait for the current Slurm action to finish before exiting.")
                return
            self.exit(None)

    return SlurmJobControlApp(tuple(jobs), title)


def run_slurm_job_control_tui(
    jobs: Sequence[AffectedSlurmJob],
    title: str,
    *,
    jobs_provider: JobsProvider | None = None,
    action_handler: JobActionHandler | None = None,
    action_journal_provider: SlurmActionJournalProvider | None = None,
    wait_timeout_seconds: int = 0,
    poll_interval_seconds: int = 30,
    exit_after_action: bool = False,
) -> tuple[str, tuple[str, ...]] | None:
    result = create_slurm_job_control_app(
        tuple(jobs),
        title=title,
        jobs_provider=jobs_provider,
        action_handler=action_handler,
        action_journal_provider=action_journal_provider,
        wait_timeout_seconds=wait_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        exit_after_action=exit_after_action,
    ).run()
    if result is None:
        return None
    if result.error is not None:
        raise result.error
    return (result.action, result.selected_job_ids)
