"""In-process convergence retry for external upgrade fast stage verification."""

from __future__ import annotations

from typing import Any

import pytest

import nebius_cxcli.soperator_migration as migration
from nebius_cxcli.soperator_migration import SoperatorMigrationPhasePending


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    phase_id: str,
    results: list[tuple[str, list[dict[str, str]]]],
    budget_seconds: int,
    snapshot_collector: Any | None = None,
    comment_emitter: Any | None = None,
) -> tuple[dict[str, Any], list[str], list[float], int]:
    checkpoint: dict[str, Any] = {"events": []}
    sleeps: list[float] = []
    compute_calls = 0

    def _fake_compute(**kwargs: Any) -> tuple[str, list[dict[str, str]]]:
        nonlocal compute_calls
        index = min(compute_calls, len(results) - 1)
        compute_calls += 1
        return results[index]

    monkeypatch.setattr(
        migration,
        "_compute_external_upgrade_phase_fast_verification",
        _fake_compute,
    )
    monkeypatch.setattr(
        migration,
        "_FAST_STAGE_VERIFICATION_CONVERGENCE_TIMEOUT_SECONDS",
        budget_seconds,
    )
    monkeypatch.setattr(
        migration,
        "_FAST_STAGE_VERIFICATION_CONVERGENCE_POLL_INTERVAL_SECONDS",
        1,
    )
    monkeypatch.setattr(migration.time, "sleep", sleeps.append)

    lines = migration._run_external_upgrade_phase_fast_verification(
        checkpoint=checkpoint,
        phase_id=phase_id,
        payload={},
        source_report={},
        live_snapshot={"marker": "initial"},
        target_ref="external-cluster",
        kube_context="external-context",
        worker_node_groups=(),
        nebius_api=None,
        command_runner=None,
        snapshot_collector=snapshot_collector,
        comment_emitter=comment_emitter,
    )
    return checkpoint, lines, sleeps, compute_calls


def _passed_check() -> list[dict[str, str]]:
    return [migration._fast_verification_check("Stage", "passed", "ready")]


def _failed_check(summary: str = "not ready") -> list[dict[str, str]]:
    return [migration._fast_verification_check("Stage", "failed", summary)]


def test_retry_converges_within_budget_and_records_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comments: list[str] = []
    checkpoint, lines, sleeps, compute_calls = _run(
        monkeypatch,
        phase_id="rolling-compute-migration",
        results=[
            ("workers not Ready", _failed_check("workers not Ready")),
            ("workers not Ready", _failed_check("workers not Ready")),
            ("completed=5/5", _passed_check()),
        ],
        budget_seconds=3600,
        comment_emitter=comments.append,
    )

    assert compute_calls == 3
    assert len(sleeps) == 2
    verification = checkpoint["phase_state"]["rolling-compute-migration"]["fast_verification"]
    assert verification["status"] == "passed"
    assert "converged on in-process attempt 3" in verification["summary"]
    assert [event["event"] for event in checkpoint["events"]] == [
        "execute-phase-fast-verification-passed"
    ]
    assert len(comments) == 2
    assert "has not converged" in comments[0]
    assert lines and "rolling-compute-migration" in lines[0]


def test_unconverged_failure_stays_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_now = {"value": 0.0}

    def _monotonic() -> float:
        return fake_now["value"]

    def _sleep(seconds: float) -> None:
        fake_now["value"] += seconds

    checkpoint: dict[str, Any] = {"events": []}
    monkeypatch.setattr(
        migration,
        "_compute_external_upgrade_phase_fast_verification",
        lambda **_kwargs: ("still failing", _failed_check("still failing")),
    )
    monkeypatch.setattr(
        migration, "_FAST_STAGE_VERIFICATION_CONVERGENCE_TIMEOUT_SECONDS", 5
    )
    monkeypatch.setattr(
        migration, "_FAST_STAGE_VERIFICATION_CONVERGENCE_POLL_INTERVAL_SECONDS", 2
    )
    monkeypatch.setattr(migration.time, "monotonic", _monotonic)
    monkeypatch.setattr(migration.time, "sleep", _sleep)

    with pytest.raises(SoperatorMigrationPhasePending) as excinfo:
        migration._run_external_upgrade_phase_fast_verification(
            checkpoint=checkpoint,
            phase_id="controller-ha-bridge",
            payload={},
            source_report={},
            live_snapshot={},
            target_ref="external-cluster",
            kube_context="external-context",
            worker_node_groups=(),
            nebius_api=None,
            command_runner=None,
        )

    assert "unconverged after" in str(excinfo.value)
    verification = checkpoint["phase_state"]["controller-ha-bridge"]["fast_verification"]
    assert verification["status"] == "failed"
    assert "unconverged after" in verification["summary"]
    assert [event["event"] for event in checkpoint["events"]] == [
        "execute-phase-fast-verification-failed"
    ]


def test_zero_budget_keeps_single_attempt_without_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint: dict[str, Any] = {"events": []}
    sleeps: list[float] = []
    calls = {"count": 0}

    def _fake_compute(**_kwargs: Any) -> tuple[str, list[dict[str, str]]]:
        calls["count"] += 1
        return "not ready", _failed_check()

    monkeypatch.setattr(
        migration,
        "_compute_external_upgrade_phase_fast_verification",
        _fake_compute,
    )
    monkeypatch.setattr(
        migration, "_FAST_STAGE_VERIFICATION_CONVERGENCE_TIMEOUT_SECONDS", 0
    )
    monkeypatch.setattr(migration.time, "sleep", sleeps.append)
    with pytest.raises(SoperatorMigrationPhasePending):
        migration._run_external_upgrade_phase_fast_verification(
            checkpoint=checkpoint,
            phase_id="rolling-compute-migration",
            payload={},
            source_report={},
            live_snapshot={},
            target_ref="external-cluster",
            kube_context="external-context",
            worker_node_groups=(),
            nebius_api=None,
            command_runner=None,
        )
    assert calls["count"] == 1
    assert sleeps == []
    verification = checkpoint["phase_state"]["rolling-compute-migration"]["fast_verification"]
    assert verification["status"] == "failed"
    assert "unconverged" not in verification["summary"]


def test_snapshot_refresh_only_for_snapshot_dependent_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = {"calls": 0}
    seen_snapshots: list[Any] = []

    def _collector(*, kube_context: str) -> dict[str, Any]:
        assert kube_context == "external-context"
        snapshots["calls"] += 1
        return {"marker": f"refresh-{snapshots['calls']}"}

    def _fake_compute(**kwargs: Any) -> tuple[str, list[dict[str, str]]]:
        seen_snapshots.append(kwargs["live_snapshot"])
        if len(seen_snapshots) < 3:
            return "not ready", _failed_check()
        return "ready", _passed_check()

    checkpoint: dict[str, Any] = {"events": []}
    monkeypatch.setattr(
        migration,
        "_compute_external_upgrade_phase_fast_verification",
        _fake_compute,
    )
    monkeypatch.setattr(
        migration, "_FAST_STAGE_VERIFICATION_CONVERGENCE_TIMEOUT_SECONDS", 3600
    )
    monkeypatch.setattr(
        migration, "_FAST_STAGE_VERIFICATION_CONVERGENCE_POLL_INTERVAL_SECONDS", 1
    )
    monkeypatch.setattr(migration.time, "sleep", lambda _seconds: None)

    migration._run_external_upgrade_phase_fast_verification(
        checkpoint=checkpoint,
        phase_id="rolling-compute-migration",
        payload={},
        source_report={},
        live_snapshot={"marker": "initial"},
        target_ref="external-cluster",
        kube_context="external-context",
        worker_node_groups=(),
        nebius_api=None,
        command_runner=None,
        snapshot_collector=_collector,
    )
    assert snapshots["calls"] == 2
    assert [snapshot["marker"] for snapshot in seen_snapshots] == [
        "initial",
        "refresh-1",
        "refresh-2",
    ]

    # A non-snapshot-dependent phase never refreshes the snapshot.
    snapshots["calls"] = 0
    seen_snapshots.clear()
    checkpoint = {"events": []}
    migration._run_external_upgrade_phase_fast_verification(
        checkpoint=checkpoint,
        phase_id="controller-ha-bridge",
        payload={},
        source_report={},
        live_snapshot={"marker": "initial"},
        target_ref="external-cluster",
        kube_context="external-context",
        worker_node_groups=(),
        nebius_api=None,
        command_runner=None,
        snapshot_collector=_collector,
    )
    assert snapshots["calls"] == 0
    assert [snapshot["marker"] for snapshot in seen_snapshots] == ["initial", "initial", "initial"]


def test_snapshot_refresh_failure_keeps_previous_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_snapshots: list[Any] = []

    def _collector(*, kube_context: str) -> dict[str, Any]:
        raise RuntimeError("temporary API failure")

    def _fake_compute(**kwargs: Any) -> tuple[str, list[dict[str, str]]]:
        seen_snapshots.append(kwargs["live_snapshot"])
        if len(seen_snapshots) < 2:
            return "not ready", _failed_check()
        return "ready", _passed_check()

    checkpoint: dict[str, Any] = {"events": []}
    monkeypatch.setattr(
        migration,
        "_compute_external_upgrade_phase_fast_verification",
        _fake_compute,
    )
    monkeypatch.setattr(
        migration, "_FAST_STAGE_VERIFICATION_CONVERGENCE_TIMEOUT_SECONDS", 3600
    )
    monkeypatch.setattr(
        migration, "_FAST_STAGE_VERIFICATION_CONVERGENCE_POLL_INTERVAL_SECONDS", 1
    )
    monkeypatch.setattr(migration.time, "sleep", lambda _seconds: None)

    migration._run_external_upgrade_phase_fast_verification(
        checkpoint=checkpoint,
        phase_id="rolling-compute-migration",
        payload={},
        source_report={},
        live_snapshot={"marker": "initial"},
        target_ref="external-cluster",
        kube_context="external-context",
        worker_node_groups=(),
        nebius_api=None,
        command_runner=None,
        snapshot_collector=_collector,
    )
    assert [snapshot["marker"] for snapshot in seen_snapshots] == ["initial", "initial"]
