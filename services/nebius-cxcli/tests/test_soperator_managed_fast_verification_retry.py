"""In-process convergence retry for managed upgrade fast stage verification."""

from __future__ import annotations

from typing import Any

import pytest

import nebius_cxcli.cli as cli
import nebius_cxcli.soperator_migration as migration
from nebius_cxcli.soperator_upgrade_safety import stage_fast_verification_check


def _passed_check() -> list[dict[str, str]]:
    return [stage_fast_verification_check("Stage", "passed", "ready")]


def _failed_check(summary: str = "not ready") -> list[dict[str, str]]:
    return [stage_fast_verification_check("Stage", "failed", summary)]


def _set_budget(
    monkeypatch: pytest.MonkeyPatch,
    *,
    timeout_seconds: int,
    poll_interval_seconds: int = 1,
) -> None:
    # The managed helper reads the external upgrade's convergence knobs via the
    # soperator_migration module so both paths share one budget.
    monkeypatch.setattr(
        migration,
        "_FAST_STAGE_VERIFICATION_CONVERGENCE_TIMEOUT_SECONDS",
        timeout_seconds,
    )
    monkeypatch.setattr(
        migration,
        "_FAST_STAGE_VERIFICATION_CONVERGENCE_POLL_INTERVAL_SECONDS",
        poll_interval_seconds,
    )


def test_retry_converges_within_budget_and_records_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_budget(monkeypatch, timeout_seconds=3600)
    sleeps: list[float] = []
    comments: list[str] = []
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    provider_results: list[tuple[str, list[dict[str, str]]]] = [
        ("workers not Ready", _failed_check("workers not Ready")),
        ("completed=5/5", _passed_check()),
    ]
    calls = {"count": 0}

    def _provider() -> tuple[str, list[dict[str, str]]]:
        result = provider_results[min(calls["count"], len(provider_results) - 1)]
        calls["count"] += 1
        return result

    summary, checks, attempts = cli._converge_stage_fast_verification_checks(
        summary="workers not Ready",
        checks=_failed_check("workers not Ready"),
        checks_provider=_provider,
        comment_emitter=comments.append,
    )

    assert attempts == 3
    assert calls["count"] == 2
    assert summary == "completed=5/5 (converged on in-process attempt 3)"
    assert [check["status"] for check in checks] == ["passed"]
    assert len(sleeps) == 2
    assert len(comments) == 2
    assert "has not converged" in comments[0]
    assert "retrying in-process" in comments[0]


def test_unconverged_failure_appends_attempt_count_and_stays_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_budget(monkeypatch, timeout_seconds=5, poll_interval_seconds=2)
    fake_now = {"value": 0.0}

    def _monotonic() -> float:
        return fake_now["value"]

    def _sleep(seconds: float) -> None:
        fake_now["value"] += seconds

    monkeypatch.setattr(cli.time, "monotonic", _monotonic)
    monkeypatch.setattr(cli.time, "sleep", _sleep)

    summary, checks, attempts = cli._converge_stage_fast_verification_checks(
        summary="still failing",
        checks=_failed_check("still failing"),
        checks_provider=lambda: ("still failing", _failed_check("still failing")),
    )

    assert attempts == 4
    assert summary == "still failing (unconverged after 4 in-process attempts)"
    assert [check["status"] for check in checks] == ["failed"]


def test_zero_budget_keeps_single_attempt_without_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The autouse conftest fixture zeroes the shared convergence budget for the
    # default test lane; the managed helper must honor that same knob.
    sleeps: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    calls = {"count": 0}

    def _provider() -> tuple[str, list[dict[str, str]]]:
        calls["count"] += 1
        return "ready", _passed_check()

    summary, checks, attempts = cli._converge_stage_fast_verification_checks(
        summary="not ready",
        checks=_failed_check(),
        checks_provider=_provider,
    )

    assert attempts == 1
    assert calls["count"] == 0
    assert sleeps == []
    assert summary == "not ready"
    assert "unconverged" not in summary
    assert [check["status"] for check in checks] == ["failed"]


def test_no_provider_keeps_single_attempt_even_with_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_budget(monkeypatch, timeout_seconds=3600)
    sleeps: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)

    summary, checks, attempts = cli._converge_stage_fast_verification_checks(
        summary="not ready",
        checks=_failed_check(),
        checks_provider=None,
    )

    assert attempts == 1
    assert sleeps == []
    assert summary == "not ready"


def test_non_failed_checks_never_invoke_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_budget(monkeypatch, timeout_seconds=3600)

    def _provider() -> tuple[str, list[dict[str, str]]]:
        raise AssertionError("provider must not run for non-failed checks")

    for checks in (
        _passed_check(),
        [stage_fast_verification_check("Stage", "skipped", "not required")],
    ):
        summary, final_checks, attempts = cli._converge_stage_fast_verification_checks(
            summary="already settled",
            checks=checks,
            checks_provider=_provider,
        )
        assert attempts == 1
        assert summary == "already settled"
        assert final_checks is checks


def test_provider_error_keeps_previous_failure_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_budget(monkeypatch, timeout_seconds=3600)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    calls = {"count": 0}

    def _provider() -> tuple[str, list[dict[str, str]]]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary live re-read failure")
        return "ready", _passed_check()

    summary, checks, attempts = cli._converge_stage_fast_verification_checks(
        summary="not ready",
        checks=_failed_check(),
        checks_provider=_provider,
    )

    assert calls["count"] == 2
    assert attempts == 3
    assert summary == "ready (converged on in-process attempt 3)"
    assert [check["status"] for check in checks] == ["passed"]


def test_provider_error_until_budget_expiry_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_budget(monkeypatch, timeout_seconds=3, poll_interval_seconds=2)
    fake_now = {"value": 0.0}
    monkeypatch.setattr(cli.time, "monotonic", lambda: fake_now["value"])
    monkeypatch.setattr(
        cli.time,
        "sleep",
        lambda seconds: fake_now.__setitem__("value", fake_now["value"] + seconds),
    )

    def _provider() -> tuple[str, list[dict[str, str]]]:
        raise RuntimeError("persistent live re-read failure")

    summary, checks, attempts = cli._converge_stage_fast_verification_checks(
        summary="not ready",
        checks=_failed_check(),
        checks_provider=_provider,
    )

    assert attempts > 1
    assert "unconverged after" in summary
    assert [check["status"] for check in checks] == ["failed"]


def test_managed_helper_shares_the_external_convergence_knob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Patching only the external module's constant must change managed
    # behavior: there is one knob for both upgrade paths.
    monkeypatch.setattr(
        migration,
        "_FAST_STAGE_VERIFICATION_CONVERGENCE_TIMEOUT_SECONDS",
        3600,
    )
    monkeypatch.setattr(
        migration,
        "_FAST_STAGE_VERIFICATION_CONVERGENCE_POLL_INTERVAL_SECONDS",
        1,
    )
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    results: list[tuple[str, list[dict[str, str]]]] = [
        ("still failing", _failed_check()),
        ("ready", _passed_check()),
    ]
    calls = {"count": 0}

    def _provider() -> tuple[str, Any]:
        result = results[min(calls["count"], len(results) - 1)]
        calls["count"] += 1
        return result

    summary, _checks, attempts = cli._converge_stage_fast_verification_checks(
        summary="not ready",
        checks=_failed_check(),
        checks_provider=_provider,
    )
    assert attempts == 3
    assert summary.endswith("(converged on in-process attempt 3)")
