"""Command-scoped repair presentation; never evidence or repair authority."""

from __future__ import annotations

import contextlib
import contextvars
import functools
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any, TextIO

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from ..runtime_migration import APT_REFRESH_TIMEOUT


class Phase(str, Enum):
    ADMIT = "opening the confirmed repair transaction"
    SETTLE = "settling the previous repair transaction"
    PREPARE = "preparing retained gateways for repair"
    ACCESS = "checking release identity and authenticated SSH access"
    INSPECT = f"inspecting repair package prerequisites (APT refresh max {APT_REFRESH_TIMEOUT}s)"
    RESOLVE = "resolving and downloading repair dependencies"
    STAGE = f"uploading and verifying repair packages (each APT refresh max {APT_REFRESH_TIMEOUT}s)"
    GUARD = "installing and verifying the repair boot guard"
    STOP = "stopping retained gateways and verifying isolation"
    START = "starting the gateway under its repair boot guard"
    INSTALL = "installing the managed software stack"
    VERIFY = "verifying the installed repair packages"
    BIND = "binding repaired gateways to fresh activation authority"
    PLAN = "inspecting repaired gateway activation"
    CONFIGURE = "staging repaired gateway configuration"
    ACTIVATE = "activating and verifying repaired gateways"
    FINISH = "verifying and completing the repair transaction"


class Wait(str, Enum):
    SSH = "waiting for authenticated SSH access"
    WRITER = "waiting for the existing deployment writer"
    RUNNING_OWNED = "package worker is running"
    WAIT_EXTERNAL_LOCK = "package worker is waiting for a package-manager lock"
    READY_TO_CONTINUE = "continuing the settled package worker"
    PREPARED = "package worker completed; verifying its result"


@dataclass
class Frame:
    key: object
    label: str
    started: float
    task: Any = None
    detail: str = ""
    observation: object = None
    next_update: float = 0
    warnings: tuple[str, ...] = ()


class Reporter:
    def __init__(self, stream: TextIO):
        self.stream = stream
        self.frames: list[Frame] = []
        self.display: Progress | None = None
        self.disabled = False
        try:
            if stream.isatty():
                self.display = Progress(
                    SpinnerColumn("dots"),
                    TextColumn("{task.description}", markup=False),
                    TimeElapsedColumn(),
                    console=Console(file=stream, highlight=False),
                    transient=True,
                    redirect_stdout=False,
                    redirect_stderr=False,
                )
        except Exception:
            self.disabled = True

    def show(self, frame: Frame, *, terminal: str | None = None) -> None:
        """A broken terminal must not change a deployment outcome."""
        if self.disabled:
            return
        try:
            label = frame.label + (" — " + frame.detail if frame.detail else "")
            terminal_label = frame.label
            if frame.warnings:
                warning = "; warning: " + "; ".join(frame.warnings)
                label += warning
                terminal_label += warning
            if terminal is not None:
                if self.display is not None:
                    self.display.remove_task(frame.task)
                    self.display.console.print(
                        f"{terminal} {terminal_label} ({time.monotonic() - frame.started:.0f}s).",
                        markup=False,
                    )
                else:
                    self.stream.write(f"{terminal} {terminal_label}.\n")
                    self.stream.flush()
            elif self.display is not None:
                if frame.task is None:
                    frame.task = self.display.add_task(label, total=None)
                    self.display.start()
                else:
                    self.display.update(frame.task, description=label, visible=True)
                for parent in self.frames:
                    if parent is not frame and parent.task is not None:
                        self.display.update(parent.task, visible=False)
            else:
                self.stream.write(f"… {label}.\n")
                self.stream.flush()
        except Exception:
            self.disabled = True
            self.close()

    def start(self, key: object, label: str) -> Frame:
        frame = Frame(key, label, time.monotonic())
        self.frames.append(frame)
        self.show(frame)
        return frame

    def finish(self, frame: Frame, *, success: bool) -> None:
        if frame not in self.frames:
            return
        # An outer failure closes unfinished nested activation stages first.
        while self.frames[-1] is not frame:
            self.finish(self.frames[-1], success=False)
        self.frames.pop()
        self.show(frame, terminal="✓" if success else "✗")
        if self.frames and self.display is not None:
            self.show(self.frames[-1])
        elif not self.frames:
            self.close()

    def update(self, detail: str, *, observation: object = None) -> None:
        if not self.frames:
            return
        frame = self.frames[-1]
        now = time.monotonic()
        if observation != frame.observation or now >= frame.next_update:
            frame.detail = detail
            frame.observation = observation
            frame.next_update = now + 5
            self.show(frame)

    def close(self) -> None:
        if self.display is not None:
            try:
                self.display.stop()
            except Exception:
                self.disabled = True


CURRENT: contextvars.ContextVar[Reporter | None] = contextvars.ContextVar(
    "repair_progress", default=None
)


@contextlib.contextmanager
def reporting() -> Iterator[None]:
    reporter = Reporter(sys.stderr)
    token = CURRENT.set(reporter)
    try:
        yield
    finally:
        reporter.close()
        CURRENT.reset(token)


@contextlib.contextmanager
def step(phase: Phase, member: int | None = None) -> Iterator[None]:
    reporter = CURRENT.get()
    label = phase.value + (f" — member {member}" if member is not None else "")
    frame = reporter.start(phase, label) if reporter is not None else None
    try:
        yield
    except BaseException:
        if reporter is not None and frame is not None:
            reporter.finish(frame, success=False)
        raise
    else:
        if reporter is not None and frame is not None:
            reporter.finish(frame, success=True)


def waiting(state: Wait, *, deadline: float | None = None) -> None:
    reporter = CURRENT.get()
    if reporter is not None:
        detail = state.value
        if deadline is not None:
            detail += f"; {max(0, deadline - time.monotonic()):.0f}s remaining in this wait"
        reporter.update(detail, observation=state)


def for_member(phase: Phase) -> Any:
    def decorate(function: Any) -> Any:
        @functools.wraps(function)
        def observed(owner: Any, name: str, *args: Any, **kwargs: Any) -> Any:
            if CURRENT.get() is None:
                return function(owner, name, *args, **kwargs)
            with step(phase, member_index(owner, name)):
                return function(owner, name, *args, **kwargs)

        return observed

    return decorate


def member_index(owner: Any, name: str) -> int | None:
    # Use the same configured index as canonical HA observations and warnings.
    return owner.instances[name].instance_index if CURRENT.get() is not None else None


def activation(event: Any) -> None:
    """Reuse canonical HA events in the same repair display and output stream."""
    from .. import cli

    reporter = CURRENT.get()
    if reporter is None:
        return
    key = ("ha", event.phase)
    frame = next((frame for frame in reversed(reporter.frames) if frame.key == key), None)
    if frame is not None:
        frame.warnings = event.warnings
    if event.state is cli._VMHAProgressState.STARTED:
        reporter.start(key, cli._VMHA_PROGRESS_LABELS[event.phase])
    elif event.state is cli._VMHAProgressState.WAITING:
        if event.wait is not None:
            wait = event.wait
            reporter.update(
                f"member {wait.member_index}: {wait.check}; "
                f"{wait.observation.predicate or 'required state observed'}; "
                f"{wait.observation.remaining_seconds:.0f}s remaining in this wait",
                observation=(
                    wait.member_index,
                    wait.check,
                    wait.observation.predicate,
                    event.warnings,
                ),
            )
        elif event.warnings:
            reporter.update("", observation=event.warnings)
    else:
        if frame is not None:
            reporter.finish(frame, success=event.state is cli._VMHAProgressState.COMPLETED)
