"""One foreground supervisor for a committed Soperator upgrade."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext

from .soperator_failures import (
    SoperatorFailureDisposition,
    SoperatorInvocationEnvironmentError,
    soperator_failure_disposition,
    soperator_invocation_environment_invalidated,
)

_INVOCATION_ENVIRONMENT_INVALIDATED_MESSAGE = (
    "The local cxcli runtime changed during this committed upgrade. "
    "The campaign remains resumable; restore the same cxcli environment and rerun "
    "the exact approved upgrade command."
)


def single_use_soperator_upgrade_plan_printer(
    printer: Callable[[Sequence[str]], None],
) -> Callable[[Sequence[str]], None]:
    """Return an invocation-scoped printer that commits one static plan."""

    printed = False

    def print_once(lines: Sequence[str]) -> None:
        nonlocal printed
        if printed:
            return
        printer(lines)
        printed = True

    return print_once


def supervise_committed_soperator_upgrade[Result](
    run_once: Callable[[], Result],
    *,
    classify_failure: Callable[[BaseException], SoperatorFailureDisposition] = (
        soperator_failure_disposition
    ),
    on_retry: Callable[[SoperatorFailureDisposition, int, BaseException], None] | None = None,
    retry_wait: (
        Callable[
            [SoperatorFailureDisposition, int, BaseException, float],
            AbstractContextManager[None],
        ]
        | None
    ) = None,
    retry_initial_seconds: float = 1.0,
    retry_max_seconds: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Result:
    """Reconcile until success, exact-main terminal evidence, or local runtime loss."""

    attempt = 0
    while True:
        try:
            return run_once()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            if soperator_invocation_environment_invalidated(exc):
                raise SoperatorInvocationEnvironmentError(
                    _INVOCATION_ENVIRONMENT_INVALIDATED_MESSAGE
                ) from None
            disposition = classify_failure(exc)
            if disposition is SoperatorFailureDisposition.TERMINAL:
                raise
            attempt += 1
            if on_retry is not None:
                on_retry(disposition, attempt, exc)
            base_delay = min(
                max(retry_initial_seconds, 0.0) * (2 ** min(attempt - 1, 16)),
                max(retry_max_seconds, 0.0),
            )
            jitter_factor = 0.9 + ((attempt - 1) % 3) * 0.1
            delay = min(base_delay * jitter_factor, max(retry_max_seconds, 0.0))
            wait_context = (
                retry_wait(disposition, attempt, exc, delay)
                if retry_wait is not None
                else nullcontext()
            )
            with wait_context:
                sleep(delay)


__all__ = [
    "single_use_soperator_upgrade_plan_printer",
    "supervise_committed_soperator_upgrade",
]
