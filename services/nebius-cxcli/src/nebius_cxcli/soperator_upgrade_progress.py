"""Terminal-safe progress rendering for long Soperator upgrade operations."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from rich.console import Console, RenderableType
from rich.progress import Progress, ProgressColumn, Task, TaskID, TextColumn, TimeElapsedColumn
from rich.spinner import Spinner
from rich.text import Text


class SoperatorProgressState(StrEnum):
    """Presentation-only state for one upgrade progress operation."""

    START = "start"
    UPDATE = "update"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class SoperatorProgressEvent:
    """UI-neutral event emitted by Soperator upgrade subsystems."""

    phase: str
    state: SoperatorProgressState
    description: str
    current: int | None = None
    total: int | None = None


SoperatorProgressSink = Callable[[SoperatorProgressEvent], None]

_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![a-z0-9_-])(?P<quote>[\"']?)(?P<key>"
    r"[a-z0-9_-]*(?:token|password|passwd|secret|api[-_]?key|"
    r"private[-_]?key)[a-z0-9_-]*)(?P=quote)(?P<separator>\s*(?:=>|[:=])\s*)"
)


def _redact_credential_assignments(line: str) -> str:
    """Redact quoted or unquoted credential assignments in one diagnostic line."""

    parts: list[str] = []
    cursor = 0
    while match := _CREDENTIAL_ASSIGNMENT_RE.search(line, cursor):
        parts.append(line[cursor : match.end()])
        value_start = match.end()
        if value_start >= len(line):
            parts.append("<redacted>")
            cursor = value_start
            break
        if line[value_start] in "([{":
            # A credential-bearing compound value can contain arbitrarily nested or
            # escaped scalars. Conservatively redact the rest of this diagnostic line
            # rather than risk exposing a later element while trying to preserve shape.
            parts.append("<redacted>")
            cursor = len(line)
            break
        quote = line[value_start] if line[value_start] in {'"', "'"} else ""
        if quote:
            index = value_start + 1
            escaped = False
            closed = False
            while index < len(line):
                character = line[index]
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    index += 1
                    closed = True
                    break
                index += 1
            parts.append(f"{quote}<redacted>{quote if closed else ''}")
            cursor = index
            continue
        index = value_start
        while index < len(line) and line[index] not in " \t\r\n,;}]":
            index += 1
        parts.append("<redacted>")
        cursor = index
    parts.append(line[cursor:])
    return "".join(parts)


def sanitized_bounded_command_output(
    text: str,
    *,
    tail_lines: int = 8,
    max_chars: int = 2048,
) -> str:
    """Return actionable subprocess detail without credential or output sprawl."""

    lines: list[str] = []
    inside_pem = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if inside_pem:
            if "-----END" in line.upper():
                inside_pem = False
            continue
        if "-----BEGIN" in line.upper():
            lines.append("sensitive credential material redacted")
            inside_pem = "-----END" not in line.upper()
            continue
        line = re.sub(r"https?://\S+", "<url>", line)
        line = re.sub(
            r"(?i)\bauthorization\s*[:=]\s*.*$",
            "Authorization: <redacted>",
            line,
        )
        line = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer <redacted>", line)
        line = _redact_credential_assignments(line)
        line = re.sub(
            r"(?i)\b(token|password|passwd|secret|api[-_ ]?key|private[-_ ]?key)"
            r"(\s+)(?![:=])([^\s,;]+)",
            lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
            line,
        )
        if line not in lines:
            lines.append(line)
    if len(lines) > tail_lines + 1:
        omitted = len(lines) - tail_lines - 1
        lines = [lines[0], f"... {omitted} lines omitted ...", *lines[-tail_lines:]]
    bounded = "\n".join(lines)
    if len(bounded) > max_chars:
        suffix = "... truncated ..."
        bounded = f"{bounded[: max_chars - len(suffix)]}{suffix}"
    return bounded


def bounded_identifier_summary(values: Sequence[str], *, limit: int = 3) -> str:
    """Render a deterministic, bounded identifier sample for diagnostics."""

    normalized = tuple(dict.fromkeys(value for value in values if value))
    visible = ", ".join(normalized[:limit])
    omitted = len(normalized) - limit
    if omitted > 0:
        return f"{visible}, +{omitted} more"
    return visible


def _progress_description(message: str, *, limit: int = 240) -> str | None:
    try:
        description = " ".join(Text.from_markup(message).plain.split()).rstrip(".")
    except Exception:
        return None
    if len(description) > limit:
        return f"{description[: limit - 3]}..."
    return description


class _StateColumn(ProgressColumn):
    def __init__(self) -> None:
        super().__init__()
        self._spinner = Spinner("dots", style="cyan")

    def render(self, task: Task) -> RenderableType:
        state = str(task.fields.get("state") or SoperatorProgressState.START)
        if state == SoperatorProgressState.SUCCESS:
            return Text("✓", style="bold green")
        if state == SoperatorProgressState.FAILURE:
            return Text("✗", style="bold red")
        if state == SoperatorProgressState.SKIPPED:
            return Text("–", style="dim")
        return self._spinner.render(task.get_time())


def _count_text(task: Task) -> Text:
    current = task.fields.get("current")
    total = task.total
    if isinstance(current, int) and isinstance(total, int) and total > 0:
        return Text(f" ({current}/{total})", style="dim")
    return Text("")


class _CountColumn(ProgressColumn):
    def render(self, task: Task) -> RenderableType:
        return _count_text(task)


class SoperatorProgressSequence:
    """One exclusive Rich live surface containing persistent phase rows."""

    _PLAIN_MILESTONE_LIMIT = 16

    def __init__(
        self,
        *,
        console: Console,
        terminal: bool,
        prefix: str,
    ) -> None:
        self._console = console
        self._terminal = terminal
        self._prefix = prefix
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None
        self._phase = ""
        self._description = ""
        self._active = False
        self._closed = False
        self._disabled = False
        self._pause_depth = 0
        self._plain_milestone_keys: set[str] = set()

    def _disable(self) -> None:
        """Disable presentation after a renderer failure without affecting work."""

        progress = self._progress
        self._progress = None
        self._task_id = None
        self._disabled = True
        if progress is not None:
            with suppress(Exception):
                progress.stop()

    def __enter__(self) -> SoperatorProgressSequence:
        if self._terminal:
            try:
                self._progress = Progress(
                    _StateColumn(),
                    TextColumn("{task.description}"),
                    _CountColumn(),
                    TimeElapsedColumn(),
                    console=self._console,
                    transient=False,
                    expand=False,
                )
                self._progress.start()
            except Exception:
                self._disable()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> Literal[False]:
        del exc_type, traceback
        try:
            if not self._closed and self._active:
                if exc is None:
                    self.success()
                else:
                    self.failure()
        finally:
            if self._progress is not None:
                try:
                    self._progress.stop()
                except Exception:
                    self._disable()
            self._closed = True
        return False

    def _plain(self, state: str, description: str) -> None:
        if self._disabled:
            return
        try:
            self._console.print(
                f"{self._prefix}: {state} {description}",
                markup=False,
                highlight=False,
                soft_wrap=True,
            )
        except Exception:
            self._disable()

    def _start_event(self, event: SoperatorProgressEvent) -> None:
        self._phase = event.phase
        self._description = event.description
        self._active = True
        self._plain_milestone_keys.clear()
        if self._disabled:
            return
        if self._terminal:
            if self._progress is None:
                self._disable()
                return
            try:
                self._task_id = self._progress.add_task(
                    event.description,
                    total=None,
                    state=event.state,
                    current=event.current,
                )
                self._progress.update(
                    self._task_id,
                    current=event.current,
                    total=event.total,
                )
            except Exception:
                self._disable()
            return
        label = "RETRY" if event.state == SoperatorProgressState.RETRY else "START"
        self._plain(label, event.description)

    def _finish_current(
        self,
        state: SoperatorProgressState,
        description: str | None,
        *,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        if not self._active:
            return
        final_description = description or self._description
        if self._disabled:
            pass
        elif self._terminal:
            if self._progress is None or self._task_id is None:
                self._disable()
            else:
                try:
                    if current is not None or total is not None:
                        self._progress.update(
                            self._task_id,
                            current=current,
                            total=total,
                        )
                    task = next(task for task in self._progress.tasks if task.id == self._task_id)
                    if task.total is None:
                        self._progress.update(
                            self._task_id,
                            description=final_description,
                            total=1,
                            completed=1,
                            state=state,
                        )
                    else:
                        self._progress.update(
                            self._task_id,
                            description=final_description,
                            completed=task.total,
                            state=state,
                        )
                    self._progress.refresh()
                    # Commit the finished row to terminal scrollback, then keep
                    # only the next active task inside Rich's live surface.
                    self._progress.stop()
                    self._progress.remove_task(self._task_id)
                    self._progress.start()
                except Exception:
                    self._disable()
        else:
            label = {
                SoperatorProgressState.SUCCESS: "OK",
                SoperatorProgressState.FAILURE: "FAILED",
                SoperatorProgressState.SKIPPED: "SKIPPED",
            }[state]
            self._plain(label, final_description)
        self._description = final_description
        self._task_id = None
        self._active = False

    def handle(self, event: SoperatorProgressEvent) -> None:
        """Render one typed event without changing operational control flow."""

        if event.state == SoperatorProgressState.START:
            if self._active:
                self._finish_current(SoperatorProgressState.SUCCESS, None)
            self._start_event(event)
            return
        if not self._active:
            if event.state == SoperatorProgressState.RETRY:
                self._start_event(event)
                return
            self._start_event(
                SoperatorProgressEvent(
                    phase=event.phase,
                    state=SoperatorProgressState.START,
                    description=event.description,
                    current=event.current,
                    total=event.total,
                )
            )
        if event.state in {SoperatorProgressState.UPDATE, SoperatorProgressState.RETRY}:
            self._phase = event.phase
            self._description = event.description
            if self._disabled:
                pass
            elif self._terminal:
                if self._progress is None or self._task_id is None:
                    self._disable()
                else:
                    try:
                        self._progress.update(
                            self._task_id,
                            description=event.description,
                            current=event.current,
                            total=event.total,
                            state=event.state,
                        )
                    except Exception:
                        self._disable()
            elif event.state == SoperatorProgressState.RETRY:
                self._plain("RETRY", event.description)
            return
        if event.state == SoperatorProgressState.SUCCESS:
            self._finish_current(
                SoperatorProgressState.SUCCESS,
                event.description,
                current=event.current,
                total=event.total,
            )
            return
        if event.state == SoperatorProgressState.FAILURE:
            self._finish_current(
                SoperatorProgressState.FAILURE,
                event.description,
                current=event.current,
                total=event.total,
            )
            return
        if event.state == SoperatorProgressState.SKIPPED:
            self._finish_current(
                SoperatorProgressState.SKIPPED,
                event.description,
                current=event.current,
                total=event.total,
            )

    def start(
        self,
        phase: str,
        description: str,
        *,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        self.handle(
            SoperatorProgressEvent(
                phase=phase,
                state=SoperatorProgressState.START,
                description=description,
                current=current,
                total=total,
            )
        )

    def update(
        self,
        description: str,
        *,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        self.handle(
            SoperatorProgressEvent(
                phase=self._phase,
                state=SoperatorProgressState.UPDATE,
                description=description,
                current=current,
                total=total,
            )
        )

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Suspend terminal rendering while nested interactive output owns the TTY."""

        progress = self._progress
        if not self._terminal or self._disabled or self._closed or progress is None:
            yield
            return
        self._pause_depth += 1
        if self._pause_depth == 1:
            previous_transient = progress.live.transient
            progress.live.transient = True
            try:
                progress.stop()
            except Exception:
                self._disable()
            finally:
                progress.live.transient = previous_transient
        try:
            yield
        finally:
            self._pause_depth -= 1
            if (
                progress is not None
                and self._pause_depth == 0
                and not self._disabled
                and not self._closed
                and self._progress is progress
            ):
                try:
                    progress.start()
                except Exception:
                    self._disable()

    def milestone(
        self,
        description: str,
        *,
        key: str | None = None,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        """Update the live row and emit one bounded non-terminal INFO record."""

        self.update(description, current=current, total=total)
        if self._terminal or self._disabled:
            return
        milestone_key = key or description
        if (
            milestone_key in self._plain_milestone_keys
            or len(self._plain_milestone_keys) >= self._PLAIN_MILESTONE_LIMIT
        ):
            return
        self._plain_milestone_keys.add(milestone_key)
        self._plain("INFO", description)

    def success(self, description: str | None = None) -> None:
        self._finish_current(SoperatorProgressState.SUCCESS, description)

    def failure(self, description: str | None = None) -> None:
        self._finish_current(SoperatorProgressState.FAILURE, description)

    def skipped(self, description: str | None = None) -> None:
        self._finish_current(SoperatorProgressState.SKIPPED, description)


class SoperatorUpgradeProgress:
    """Factory for exclusive, phase-scoped Soperator progress renderers."""

    def __init__(
        self,
        console: Console,
        *,
        terminal: bool | None = None,
        prefix: str = "Soperator upgrade",
    ) -> None:
        self._console = console
        self._terminal = console.is_terminal if terminal is None else terminal
        self._prefix = prefix

    def sequence(self) -> SoperatorProgressSequence:
        return SoperatorProgressSequence(
            console=self._console,
            terminal=self._terminal,
            prefix=self._prefix,
        )

    def message(self, description: str) -> None:
        """Write a durable notice through the same stderr progress console."""

        with suppress(Exception):
            self._console.print(description)

    @contextmanager
    def retry_wait(
        self,
        phase: str,
        description: str,
        *,
        success: str | None = None,
    ) -> Iterator[SoperatorProgressSequence]:
        """Keep retry backoff visible as one active elapsed-time task."""

        with self.sequence() as sequence:
            sequence.handle(
                SoperatorProgressEvent(
                    phase=phase,
                    state=SoperatorProgressState.RETRY,
                    description=description,
                )
            )
            try:
                yield sequence
            except BaseException:
                sequence.failure()
                raise
            else:
                sequence.success(success)

    @contextmanager
    def phase(
        self,
        phase: str,
        description: str,
        *,
        success: str | None = None,
    ) -> Iterator[SoperatorProgressSequence]:
        with self.sequence() as sequence:
            sequence.start(phase, description)
            try:
                yield sequence
            except BaseException:
                sequence.failure()
                raise
            else:
                sequence.success(success)


@contextmanager
def rendered_flux_progress_surface(
    console: Console,
    upgrade_progress: SoperatorUpgradeProgress | None,
    *,
    terminal: bool,
) -> Iterator[tuple[Callable[[str], None], Callable[[str], None], SoperatorProgressSink | None]]:
    """Provide one exclusive progress surface for rendered Flux operations."""

    if upgrade_progress is not None:
        with upgrade_progress.sequence() as sequence:

            def _set_phase(message: str) -> None:
                description = _progress_description(message)
                if not description:
                    return
                sequence.start(description.lower().replace(" ", "-"), description)

            def _update_detail(message: str) -> None:
                description = _progress_description(message)
                if description:
                    sequence.update(description)

            yield _set_phase, _update_detail, sequence.handle
        return
    with console.status(
        "[cyan]Preparing Flux deployment...[/cyan]",
        spinner="dots",
    ) as status:
        last_phase = ""

        def _set_phase(message: str) -> None:
            nonlocal last_phase
            status.update(message)
            if not terminal and message != last_phase:
                console.print(message)
            last_phase = message

        yield _set_phase, console.print, None


__all__ = [
    "SoperatorProgressEvent",
    "SoperatorProgressSequence",
    "SoperatorProgressSink",
    "SoperatorProgressState",
    "SoperatorUpgradeProgress",
    "bounded_identifier_summary",
    "rendered_flux_progress_surface",
    "sanitized_bounded_command_output",
]
