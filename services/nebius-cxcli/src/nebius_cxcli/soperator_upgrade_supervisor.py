"""One foreground supervisor for a committed Soperator upgrade."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .soperator_failures import (
    SoperatorFailureDisposition,
    SoperatorInvocationEnvironmentError,
    soperator_failure_disposition,
    soperator_invocation_environment_invalidated,
)

_INVOCATION_ENVIRONMENT_INVALIDATED_MESSAGE = (
    "The local cxcli runtime changed during this committed upgrade. "
    "The campaign remains resumable; restore the same cxcli environment and run "
    "nebius-cxcli deploy CONFIG_YAML with the same frozen generation and execution controls."
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


def execute_committed_soperator_upgrade[Result](
    run_once: Callable[[], Result],
    *,
    on_stop: Callable[[SoperatorFailureDisposition, BaseException], None] | None = None,
) -> Result:
    """Execute once; retain durable intent when the local invocation cannot proceed.

    Retries belong to individual safe operations. Replaying this callback would
    repeat admission, scheduling and potentially ambiguous writes.
    """
    try:
        return run_once()
    except (Exception, KeyboardInterrupt) as exc:
        if on_stop is not None:
            try:
                on_stop(soperator_failure_disposition(exc), exc)
            except Exception as report_error:
                exc.add_note(f"Failure report could not be saved: {type(report_error).__name__}")
        if soperator_invocation_environment_invalidated(exc):
            raise SoperatorInvocationEnvironmentError(
                _INVOCATION_ENVIRONMENT_INVALIDATED_MESSAGE
            ) from None
        raise


__all__ = [
    "single_use_soperator_upgrade_plan_printer",
    "execute_committed_soperator_upgrade",
]
