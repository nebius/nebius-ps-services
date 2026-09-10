"""Invocation-scoped presentation for install's shared, non-interactive operations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
from typing import ParamSpec, TypeVar

from .soperator_upgrade_progress import SoperatorProgressSequence, SoperatorUpgradeProgress

_PROGRESS: ContextVar[SoperatorUpgradeProgress | None] = ContextVar(
    "soperator_install_progress", default=None
)
_ACTIVE: ContextVar[SoperatorProgressSequence | None] = ContextVar(
    "soperator_install_active_phase", default=None
)
_P = ParamSpec("_P")
_R = TypeVar("_R")


def install_progress_active() -> bool:
    return _PROGRESS.get() is not None


@contextmanager
def install_progress_scope(progress: SoperatorUpgradeProgress) -> Iterator[None]:
    token = _PROGRESS.set(progress)
    try:
        yield
    finally:
        _PROGRESS.reset(token)


@contextmanager
def install_phase(phase: str, description: str) -> Iterator[SoperatorProgressSequence | None]:
    progress = _PROGRESS.get()
    if progress is None:
        yield None
        return
    active = _ACTIVE.get()
    with (
        active.paused() if active is not None else nullcontext(),
        progress.phase(phase, description) as sequence,
    ):
        token = _ACTIVE.set(sequence)
        try:
            yield sequence
        finally:
            _ACTIVE.reset(token)


def install_progress_step(
    phase: str, description: str, *, succeeded: Callable[[object], bool] | None = None
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Instrument a shared operation without changing its ordinary CLI behavior.

    Apply only to operations that do not prompt or own another live display.
    Descriptions are static; credentials and remote errors are never UI events.
    Classify returned outcomes when an operation handles its own failures.
    """

    def decorate(function: Callable[_P, _R]) -> Callable[_P, _R]:
        @wraps(function)
        def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with install_phase(phase, description) as sequence:
                result = function(*args, **kwargs)
                if sequence is not None and succeeded is not None:
                    try:
                        successful = succeeded(result)
                    except Exception:
                        sequence.skipped()
                    else:
                        if not successful:
                            sequence.failure()
                return result

        return run

    return decorate
