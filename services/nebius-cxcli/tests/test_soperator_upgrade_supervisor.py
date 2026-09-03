from __future__ import annotations

from contextlib import contextmanager

import pytest

from nebius_cxcli.soperator_failures import (
    SoperatorInvocationEnvironmentError,
    SoperatorMainWorkloadIdentity,
    SoperatorMainWorkloadTerminalError,
    SoperatorSafetyPauseError,
)
from nebius_cxcli.soperator_upgrade_supervisor import (
    single_use_soperator_upgrade_plan_printer,
    supervise_committed_soperator_upgrade,
)


def _main_identity() -> SoperatorMainWorkloadIdentity:
    return SoperatorMainWorkloadIdentity(
        api_version="helm.toolkit.fluxcd.io/v2",
        kind="HelmRelease",
        namespace="flux-system",
        name="cxcli-soperator-fluxcd-slurm-cluster",
        source_kind="OCIRepository",
        source_name="soperator-upstream-umbrella",
        source_revision="sha256:" + "1" * 64,
        uid="main-uid",
        generation=3,
        observed_generation=3,
    )


def test_supervisor_retries_ordinary_and_safety_failures_without_budget() -> None:
    failures = [RuntimeError("dependency unavailable"), SoperatorSafetyPauseError("reprove")]
    attempts: list[int] = []
    sleeps: list[float] = []

    def run_once() -> str:
        attempts.append(len(attempts) + 1)
        if failures:
            raise failures.pop(0)
        return "complete"

    result = supervise_committed_soperator_upgrade(
        run_once,
        retry_initial_seconds=0,
        retry_max_seconds=0,
        sleep=sleeps.append,
    )

    assert result == "complete"
    assert attempts == [1, 2, 3]
    assert sleeps == [0.0, 0.0]


def test_supervisor_returns_only_for_exact_main_terminal_failure() -> None:
    terminal = SoperatorMainWorkloadTerminalError(
        "main workload stalled",
        identity=_main_identity(),
        reason="RetriesExceeded",
    )

    with pytest.raises(SoperatorMainWorkloadTerminalError) as caught:
        supervise_committed_soperator_upgrade(
            lambda: (_ for _ in ()).throw(terminal),
            sleep=lambda _seconds: pytest.fail("terminal failure must not sleep"),
        )

    assert caught.value.identity == _main_identity()


def test_supervisor_caps_jittered_backoff_at_configured_maximum() -> None:
    failures = [RuntimeError("retry") for _ in range(3)]
    sleeps: list[float] = []

    def run_once() -> str:
        if failures:
            raise failures.pop(0)
        return "complete"

    assert (
        supervise_committed_soperator_upgrade(
            run_once,
            retry_initial_seconds=10,
            retry_max_seconds=15,
            sleep=sleeps.append,
        )
        == "complete"
    )

    assert sleeps == [9.0, 15.0, 15.0]


def test_supervisor_keeps_retry_wait_context_active_through_backoff() -> None:
    events: list[str] = []
    attempts = 0

    def run_once() -> str:
        nonlocal attempts
        attempts += 1
        events.append(f"run-{attempts}")
        if attempts == 1:
            raise SoperatorSafetyPauseError("authority changed")
        return "complete"

    def on_retry(
        _disposition: object,
        attempt: int,
        _error: BaseException,
    ) -> None:
        events.append(f"report-{attempt}")

    @contextmanager
    def retry_wait(
        _disposition: object,
        attempt: int,
        _error: BaseException,
        delay: float,
    ):  # type: ignore[no-untyped-def]
        events.append(f"wait-enter-{attempt}-{delay:.1f}")
        try:
            yield
        finally:
            events.append(f"wait-exit-{attempt}")

    result = supervise_committed_soperator_upgrade(
        run_once,
        on_retry=on_retry,
        retry_wait=retry_wait,
        retry_initial_seconds=1,
        retry_max_seconds=30,
        sleep=lambda delay: events.append(f"sleep-{delay:.1f}"),
    )

    assert result == "complete"
    assert events == [
        "run-1",
        "report-1",
        "wait-enter-1-0.9",
        "sleep-0.9",
        "wait-exit-1",
        "run-2",
    ]


def test_release_retry_runs_twice_but_prints_static_plan_once() -> None:
    attempts = 0
    printed: list[tuple[str, ...]] = []
    print_plan = single_use_soperator_upgrade_plan_printer(
        lambda lines: printed.append(tuple(lines))
    )

    def run_release() -> str:
        nonlocal attempts
        attempts += 1
        print_plan(("Soperator direct-upstream upgrade plan", f"attempt {attempts}"))
        if attempts == 1:
            raise SoperatorSafetyPauseError("authority changed")
        return "complete"

    result = supervise_committed_soperator_upgrade(
        run_release,
        retry_initial_seconds=0,
        retry_max_seconds=0,
        sleep=lambda _delay: None,
    )

    assert result == "complete"
    assert attempts == 2
    assert printed == [("Soperator direct-upstream upgrade plan", "attempt 1")]


def test_supervisor_closes_retry_wait_when_backoff_is_interrupted() -> None:
    events: list[str] = []

    @contextmanager
    def retry_wait(
        _disposition: object,
        attempt: int,
        _error: BaseException,
        _delay: float,
    ):  # type: ignore[no-untyped-def]
        events.append(f"wait-enter-{attempt}")
        try:
            yield
        finally:
            events.append(f"wait-exit-{attempt}")

    with pytest.raises(KeyboardInterrupt):
        supervise_committed_soperator_upgrade(
            lambda: (_ for _ in ()).throw(SoperatorSafetyPauseError("authority changed")),
            retry_wait=retry_wait,
            retry_initial_seconds=0,
            retry_max_seconds=0,
            sleep=lambda _delay: (_ for _ in ()).throw(KeyboardInterrupt()),
        )

    assert events == ["wait-enter-1", "wait-exit-1"]


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(2)])
def test_supervisor_propagates_explicit_process_interruption(
    interruption: BaseException,
) -> None:
    with pytest.raises(type(interruption)):
        supervise_committed_soperator_upgrade(
            lambda: (_ for _ in ()).throw(interruption),
            sleep=lambda _seconds: pytest.fail("explicit interruption must not retry"),
        )


@pytest.mark.parametrize(
    "detail",
    [
        (
            "kubectl failed: Unable to connect to the server: getting credentials: "
            "exec: fork/exec /private/checkout/.venv/bin/nebius-cxcli: "
            "no such file or directory"
        ),
        (
            "kubectl failed: getting credentials: exec: executable python failed: "
            "No module named 'nebius_cxcli'"
        ),
    ],
)
def test_supervisor_aborts_resumably_when_local_exec_runtime_vanishes(detail: str) -> None:
    original = RuntimeError(detail)

    with pytest.raises(SoperatorInvocationEnvironmentError) as caught:
        supervise_committed_soperator_upgrade(
            lambda: (_ for _ in ()).throw(original),
            on_retry=lambda *_args: pytest.fail("local runtime loss must not retry"),
            sleep=lambda _seconds: pytest.fail("local runtime loss must not sleep"),
        )

    assert "campaign remains resumable" in str(caught.value)
    assert "exact approved upgrade command" in str(caught.value)
    assert "/private/checkout" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_supervisor_keeps_unrelated_missing_file_failure_retryable() -> None:
    attempts = 0

    def run_once() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("release cache file: no such file or directory")
        return "complete"

    assert (
        supervise_committed_soperator_upgrade(
            run_once,
            retry_initial_seconds=0,
            retry_max_seconds=0,
            sleep=lambda _seconds: None,
        )
        == "complete"
    )
    assert attempts == 2
