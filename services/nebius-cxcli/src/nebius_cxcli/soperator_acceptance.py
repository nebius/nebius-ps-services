"""Shared install/upgrade acceptance choice and graceful terminal control."""

from __future__ import annotations

import copy
import functools
import inspect
import os
import select
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, get_type_hints

from .soperator_checks_policy import SoperatorChecksPolicy, checks_digest


class AcceptanceProfile(StrEnum):
    READINESS = "readiness"
    FULL = "full"


@dataclass
class AcceptanceControl:
    requested: str = "full"
    explicit: bool = False
    finish_requested: bool = False
    chosen: str | None = None
    reader: Callable[[], bool] = lambda: False
    outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)
    announced: bool = False

    def announce(self) -> None:
        if not self.announced:
            from rich.console import Console

            Console().print(
                "Soperator acceptance: [green]Ctrl+G[/green] interrupts extended testing safely; "
                "[red]Ctrl+C[/red] interrupts for later recovery. Required readiness and "
                "check restoration always run.",
                highlight=False,
                soft_wrap=True,
            )
            self.announced = True

    def poll(self) -> bool:
        self.finish_requested = self.finish_requested or self.reader()
        return self.finish_requested

    def choose(self) -> str:
        if self.chosen is None:
            if self.finish_requested:
                # Full/cancelled records the finish request on subsequent targets.
                self.chosen = "full"
            elif self.requested == "ask":
                import typer

                from .soperator_upgrade_progress import pause_rendered_flux_progress

                # EOF/Ctrl+C are interruptions. Neither is interpreted as No.
                with pause_rendered_flux_progress():
                    self.chosen = (
                        "full"
                        if typer.confirm(
                            "Required readiness passed. Run extended acceptance tests?",
                            default=False,
                        )
                        else "readiness"
                    )
            else:
                self.chosen = self.requested
        return self.chosen

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": "nebius-cxcli.acceptance-control/v1",
            "requested": self.requested,
            "chosen": self.chosen,
            "finish_requested": self.finish_requested,
            "outcomes": self.outcomes,
        }

    def restore(self, payload: Mapping[str, Any]) -> None:
        if (
            set(payload) != {"schema", "requested", "chosen", "finish_requested", "outcomes"}
            or payload["schema"] != "nebius-cxcli.acceptance-control/v1"
            or payload["requested"] not in {"ask", "readiness", "full"}
            or payload["chosen"] not in {None, "readiness", "full"}
            or type(payload["finish_requested"]) is not bool
            or not isinstance(payload["outcomes"], dict)
        ):
            raise RuntimeError("Invalid persisted acceptance control")
        locked = payload["chosen"] or payload["requested"]
        if self.explicit and self.requested != locked:
            raise RuntimeError("Acceptance choice cannot change during resume")
        self.requested = payload["requested"]
        self.chosen = payload["chosen"]
        self.finish_requested = payload["finish_requested"]
        self.outcomes = dict(payload["outcomes"])


_CONTROL: ContextVar[AcceptanceControl | None] = ContextVar("acceptance_control", default=None)


def current_control() -> AcceptanceControl:
    return _CONTROL.get() or AcceptanceControl()


def acceptance_command(function: Callable) -> Callable:
    """Own one invocation-wide choice and safe-finish request, including child targets."""

    @functools.wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if _CONTROL.get() is not None:
            return function(*args, **kwargs)
        arguments = inspect.signature(function).bind_partial(*args, **kwargs).arguments
        value = arguments.get("acceptance")
        interactive = bool(arguments.get("interactive", True)) and sys.stdin.isatty()
        control = AcceptanceControl(
            requested=str(value) if value is not None else "ask" if interactive else "full",
            explicit=value is not None,
        )
        token = _CONTROL.set(control)
        try:
            return function(*args, **kwargs)
        finally:
            _CONTROL.reset(token)

    wrapped.__annotations__ = get_type_hints(function, include_extras=True)
    return wrapped


@contextmanager
def graceful_keyboard(control: AcceptanceControl) -> Iterator[None]:
    """Read only while extended work owns the terminal; preserve ISIG/Ctrl+C."""
    if not sys.stdin.isatty():
        yield
        return
    import termios

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    changed = list(original)
    changed[6] = list(original[6])
    changed[3] &= ~(termios.ECHO | termios.ICANON)
    changed[6][termios.VMIN] = 0
    changed[6][termios.VTIME] = 0
    previous = control.reader

    def read() -> bool:
        return bool(select.select([fd], [], [], 0)[0] and b"\x07" in os.read(fd, 1024))

    termios.tcsetattr(fd, termios.TCSANOW, changed)
    control.reader = read
    try:
        yield
    finally:
        control.reader = previous
        termios.tcsetattr(fd, termios.TCSANOW, original)


def validation_contract(policy: SoperatorChecksPolicy) -> dict[str, Any]:
    required = [rule.name for rule in policy.readiness]
    extended = [rule.name for rule in policy.required if rule.name not in required]
    contract: dict[str, Any] = {
        "schema": "nebius-cxcli.acceptance/v1",
        "policy": policy.sha256,
        "readiness": required,
        "extended": extended,
    }
    if policy.diagnostics:
        contract["diagnostics"] = copy.deepcopy(dict(policy.diagnostics))
    return {**contract, "sha256": checks_digest(contract)}


def terminal_proof(state: Mapping[str, Any], policy: SoperatorChecksPolicy) -> Mapping[str, Any]:
    validation = state.get("validation", {})
    if validation.get("contract") != validation_contract(policy):
        raise RuntimeError("Acceptance validation contract changed")
    if validation.get("readiness") != "passed":
        raise RuntimeError("Required readiness is incomplete")
    profile, outcome = validation.get("profile"), validation.get("extended")
    if not (
        (profile == "readiness" and outcome == "skipped")
        or (profile == "full" and outcome in {"passed", "cancelled"})
    ):
        raise RuntimeError("Extended acceptance has no valid terminal outcome")
    if outcome == "cancelled" and validation.get("finish", {}).get("status") != "quiescent":
        raise RuntimeError("Graceful acceptance finish has no quiescence proof")
    if outcome == "cancelled" and validation["finish"].get("jobs_sha256") != checks_digest(
        state["jobs"]
    ):
        raise RuntimeError("Graceful acceptance cleanup evidence changed")
    return validation
