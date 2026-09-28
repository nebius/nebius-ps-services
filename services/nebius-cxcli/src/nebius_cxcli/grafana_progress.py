"""Presentation-only progress for interactive and redirected Grafana imports."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from types import TracebackType

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn


class GrafanaProgress:
    """One stderr live surface; operation results and prompts retain stdout."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console if console is not None else Console(stderr=True)
        self._display: Progress | None = None
        self._task: TaskID | None = None
        self._description = ""
        self._pause_depth = 0
        self._disabled = False
        self._closed = False

    def __enter__(self) -> GrafanaProgress:
        return self

    def _disable(self) -> None:
        self._disabled = True
        if self._display is not None:
            with suppress(Exception):
                self._display.stop()

    def update(self, description: str) -> None:
        """Report an actual stage transition, never a synthetic percentage."""
        if self._closed or self._disabled or description == self._description:
            return
        self._description = description
        try:
            if not self.console.is_terminal or self.console.is_dumb_terminal:
                self.console.print(f"Grafana import: {description}", markup=False, highlight=False)
                return
            if self._display is None:
                self._display = Progress(
                    SpinnerColumn(),
                    TextColumn("{task.description}", markup=False),
                    TimeElapsedColumn(),
                    console=self.console,
                    transient=True,
                    refresh_per_second=8,
                    redirect_stdout=False,
                    redirect_stderr=False,
                )
            if self._task is None:
                self._task = self._display.add_task(description, total=None)
            else:
                self._display.reset(self._task, description=description, total=None)
            if not self._pause_depth:
                if not self._display.live.is_started:
                    self._display.start()
                self._display.refresh()
        except Exception:
            self._disable()

    @contextmanager
    def paused(self, *, resume: bool = True) -> Iterator[None]:
        """Give prompts and normal result messages exclusive terminal access."""
        self._pause_depth += 1
        was_running = self._display is not None and self._display.live.is_started
        if self._pause_depth == 1 and self._display is not None:
            try:
                self._display.stop()
            except Exception:
                self._disable()
        completed = False
        try:
            yield
            completed = True
        finally:
            self._pause_depth -= 1
            if (
                completed
                and resume
                and was_running
                and not self._pause_depth
                and self._display is not None
                and not self._disabled
                and not self._closed
            ):
                try:
                    self._display.start()
                except Exception:
                    self._disable()

    def close(self) -> None:
        self._closed = True
        if self._display is not None:
            with suppress(Exception):
                self._display.stop()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
        if exc_type is not None and self._description and not self._disabled:
            with suppress(Exception):
                self.console.print(
                    f"Grafana import stopped during: {self._description}",
                    markup=False,
                    highlight=False,
                )
