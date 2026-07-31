"""Fenced re-pause of the immutable-child Soperator manager after an out-of-band resume."""

from __future__ import annotations

from typing import Any

import pytest

import nebius_cxcli.soperator_migration as migration
from nebius_cxcli.soperator_migration import SoperatorMigrationPhasePending

_UID = "8f6cc887-43e6-4bde-9d62-faeba4217524"
_FINGERPRINT = "f" * 64


def _handoff() -> dict[str, Any]:
    return {
        "status": "manager-paused",
        "manager": {
            "namespace": "soperator",
            "name": "soperator-manager",
            "uid": _UID,
            "resourceVersion": "100",
            "replicas": 1,
            "spec_fingerprint": _FINGERPRINT,
            "capture_generation": 1,
        },
        "manager_paused_generation": 2,
        "manager_paused_at": "2026-07-26T18:37:32Z",
        "manager_paused_resource_version": "200",
        "manager_pause_intent_at": "2026-07-26T18:37:00Z",
        "manager_pause_from_resource_version": "150",
        "manager_pause_from_generation": 1,
        "manager_pause_expected_generation": 2,
    }


def _phase(fence_status: str = "verified", suspended: bool = True) -> dict[str, Any]:
    return {
        "source_flux_reconciliation_fence": {
            "status": fence_status,
            "verified_at": "2026-07-26T19:25:14Z",
        },
        "suspended_flux_helmreleases": (
            ["flux-system-soperator-fluxcd-slurm-cluster"] if suspended else []
        ),
    }


def _live_manager(*, replicas: int, generation: int) -> dict[str, Any]:
    return {
        "metadata": {
            "name": "soperator-manager",
            "namespace": "soperator",
            "uid": _UID,
            "resourceVersion": "300",
            "generation": generation,
        },
        "spec": {"replicas": replicas},
        "status": {},
    }


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    handoff: dict[str, Any],
    phase: dict[str, Any],
    live: dict[str, Any],
    live_fingerprint: str = _FINGERPRINT,
) -> tuple[list[str], list[str], list[int]]:
    pause_calls: list[str] = []
    writes: list[int] = []

    monkeypatch.setattr(
        migration,
        "_immutable_child_get_namespaced_resource",
        lambda **_kwargs: live,
    )
    monkeypatch.setattr(
        migration,
        "_immutable_child_require_manager_spec_contract",
        lambda *, manager_binding, manager: (
            (_raise_contract() if live_fingerprint != _FINGERPRINT else None)
            or (live_fingerprint, int(manager["metadata"]["generation"]))
        ),
    )
    monkeypatch.setattr(
        migration,
        "_pause_soperator_manager_for_child_handoff",
        lambda **kwargs: pause_calls.append("paused"),
    )

    lines = migration._repause_soperator_manager_after_fenced_resume(
        phase=phase,
        handoff=handoff,
        command_runner=None,
        kube_context="external-context",
        checkpoint_writer=lambda: writes.append(1),
    )
    return lines, pause_calls, writes


def _raise_contract() -> None:
    raise SoperatorMigrationPhasePending(
        "immutable child handoff manager Deployment selector/template/image/env/SA "
        "contract changed after capture."
    )


def test_fenced_resume_journals_and_redrives_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    handoff = _handoff()
    lines, pause_calls, writes = _run(
        monkeypatch,
        handoff=handoff,
        phase=_phase(),
        live=_live_manager(replicas=1, generation=3),
    )

    assert pause_calls == ["paused"]
    assert writes == [1]
    assert lines and "Re-fenced" in lines[0]
    repauses = handoff["manager_fenced_repauses"]
    assert len(repauses) == 1
    assert repauses[0]["previous_paused_generation"] == 2
    assert repauses[0]["resumed_generation"] == 3
    assert repauses[0]["resumed_replicas"] == 1
    assert handoff["manager"]["capture_generation"] == 3
    for key in (
        "manager_pause_intent_at",
        "manager_pause_from_resource_version",
        "manager_pause_from_generation",
        "manager_pause_expected_generation",
    ):
        assert key not in handoff


def test_still_paused_manager_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    handoff = _handoff()
    lines, pause_calls, writes = _run(
        monkeypatch,
        handoff=handoff,
        phase=_phase(),
        live=_live_manager(replicas=0, generation=2),
    )
    assert lines == []
    assert pause_calls == []
    assert writes == []
    assert "manager_fenced_repauses" not in handoff


def test_unfenced_resume_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    for phase in (_phase(fence_status=""), _phase(suspended=False)):
        handoff = _handoff()
        with pytest.raises(
            SoperatorMigrationPhasePending,
            match="manager resumed after its durable pause checkpoint",
        ):
            _run(
                monkeypatch,
                handoff=handoff,
                phase=phase,
                live=_live_manager(replicas=1, generation=3),
            )
        assert "manager_fenced_repauses" not in handoff


def test_foreign_replica_count_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(
        SoperatorMigrationPhasePending,
        match="manager resumed after its durable pause checkpoint",
    ):
        _run(
            monkeypatch,
            handoff=_handoff(),
            phase=_phase(),
            live=_live_manager(replicas=2, generation=3),
        )


def test_stale_generation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(
        SoperatorMigrationPhasePending,
        match="manager resumed after its durable pause checkpoint",
    ):
        _run(
            monkeypatch,
            handoff=_handoff(),
            phase=_phase(),
            live=_live_manager(replicas=1, generation=2),
        )


def test_foreign_spec_drift_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(
        SoperatorMigrationPhasePending,
        match="contract changed after capture",
    ):
        _run(
            monkeypatch,
            handoff=_handoff(),
            phase=_phase(),
            live=_live_manager(replicas=1, generation=3),
            live_fingerprint="e" * 64,
        )
