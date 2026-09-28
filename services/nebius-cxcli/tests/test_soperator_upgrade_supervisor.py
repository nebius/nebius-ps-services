from __future__ import annotations

import pytest

from nebius_cxcli.soperator_failures import (
    SoperatorFailureDisposition,
    SoperatorInvocationEnvironmentError,
    SoperatorSafetyPauseError,
)
from nebius_cxcli.soperator_upgrade_supervisor import (
    execute_committed_soperator_upgrade,
    single_use_soperator_upgrade_plan_printer,
)


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("unknown"),
        SoperatorSafetyPauseError("inputs differ"),
        FileNotFoundError("missing"),
    ],
)
def test_permanent_and_unknown_failures_stop_without_replaying_campaign(error):
    attempts = []
    reports = []

    def run():
        attempts.append("admission-and-mutation")
        raise error

    with pytest.raises(type(error)) as caught:
        execute_committed_soperator_upgrade(run, on_stop=lambda *args: reports.append(args))
    assert caught.value is error
    assert attempts == ["admission-and-mutation"]
    assert reports[0][1] is error
    assert reports[0][0] in {
        SoperatorFailureDisposition.STOP,
        SoperatorFailureDisposition.SAFETY_PAUSE,
    }


@pytest.mark.parametrize("original", [RuntimeError("primary"), KeyboardInterrupt("primary")])
def test_reporting_failure_preserves_original_cause(original):

    def report(*args):
        raise OSError("checkpoint unavailable")

    with pytest.raises(type(original), match="primary") as caught:
        execute_committed_soperator_upgrade(lambda: (_ for _ in ()).throw(original), on_stop=report)
    assert caught.value is original
    assert "OSError" in original.__notes__[0]


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(2)])
def test_explicit_interruption_is_never_replayed(interruption):
    with pytest.raises(type(interruption)):
        execute_committed_soperator_upgrade(lambda: (_ for _ in ()).throw(interruption))


def test_static_plan_prints_once_across_admission_and_execution():
    printed = []
    printer = single_use_soperator_upgrade_plan_printer(printed.append)
    printer(["assessment"])
    printer(["assessment"])
    assert printed == [["assessment"]]


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
        execute_committed_soperator_upgrade(
            lambda: (_ for _ in ()).throw(original),
        )

    assert "campaign remains resumable" in str(caught.value)
    assert "nebius-cxcli deploy CONFIG_YAML" in str(caught.value)
    assert "/private/checkout" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_success_returns_without_stop_report():
    assert (
        execute_committed_soperator_upgrade(
            lambda: "complete", on_stop=lambda *args: pytest.fail("stop report")
        )
        == "complete"
    )
