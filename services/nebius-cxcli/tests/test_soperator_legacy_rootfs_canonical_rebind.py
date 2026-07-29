"""Pre-write canonical rebind for the legacy-rootfs dual Slurm config bridge."""

from __future__ import annotations

from typing import Any

import pytest

import nebius_cxcli.soperator_migration as migration
from nebius_cxcli.soperator_migration import SoperatorMigrationPhasePending

_PREV_SHA = "a" * 64
_PREV_FP = "b" * 64
_NEW_SHA = "c" * 64
_NEW_FP = "d" * 64


def _journal() -> dict[str, Any]:
    return {
        "stage": "target-ha-active",
        "authority": {
            "owner": "bridge-target",
            "source_restart_prohibited": True,
        },
    }


def _state(fence: dict[str, Any]) -> dict[str, Any]:
    return {
        "configmap": {
            "compatible_sha256": _PREV_SHA,
            "compatible_data_fingerprint": _PREV_FP,
        },
        "source_configmap": {
            "compatible_sha256": _PREV_SHA,
            "compatible_data_fingerprint": _PREV_FP,
        },
        "canonical_payload": {
            "data_fingerprint": _PREV_FP,
            "source": "target-configmap-compatible-transform",
        },
        "dual_writer_fence": fence,
    }


def _mid_zero_fence() -> dict[str, Any]:
    return {
        "schema": "nebius-cxcli/legacy-rootfs-dual-writer-fence-v2",
        "status": "zero",
        "deployment_uid": "sconfig-deployment-uid",
        "zero_at": "2026-07-26T18:39:23Z",
        "zero_generation": 2,
        "zero_resource_version": "109011",
    }


def _adopt(state: dict[str, Any], writes: list[int]) -> None:
    migration._legacy_rootfs_adopt_active_bridge_canonical_recovery(
        state=state,
        status="intent",
        handoff={"status": "manager-paused"},
        controller_bridge=_journal(),
        live={},
        compatible_sha=_NEW_SHA,
        canonical_data_fingerprint=_NEW_FP,
        canonical_files=(("slurm.conf", "/mnt/jail/etc/slurm/slurm.conf", _NEW_SHA),),
        canonical_source="target-ha-active-controller-bridge-compatible-transform",
        removed={"metrics_type": 1},
        command_runner=None,
        kube_context="external-context",
        checkpoint_writer=lambda: writes.append(1),
    )


def test_mid_zero_window_rebinds_unconsumed_intent_without_live_reads() -> None:
    state = _state(_mid_zero_fence())
    writes: list[int] = []

    # command_runner=None proves no live ConfigMap, login, or health read happens.
    _adopt(state, writes)

    assert writes == [1]
    for key in ("configmap", "source_configmap"):
        assert state[key]["compatible_sha256"] == _NEW_SHA
        assert state[key]["compatible_data_fingerprint"] == _NEW_FP
        assert state[key]["removed_directives"] == {"metrics_type": 1}
    assert state["canonical_payload"]["data_fingerprint"] == _NEW_FP
    assert (
        state["canonical_payload"]["source"]
        == "target-ha-active-controller-bridge-compatible-transform"
    )
    recovery = state["canonical_payload_recovery"]
    assert recovery["status"] == "rebound-pre-write"
    assert recovery["previous_compatible_sha256"] == _PREV_SHA
    assert recovery["previous_data_fingerprint"] == _PREV_FP
    assert recovery["compatible_sha256"] == _NEW_SHA
    assert recovery["data_fingerprint"] == _NEW_FP
    assert recovery["fence_status"] == "zero"


def test_mid_zero_window_requires_exact_zero_evidence() -> None:
    fence = _mid_zero_fence()
    del fence["zero_at"]
    state = _state(fence)

    with pytest.raises(SoperatorMigrationPhasePending, match="ready dual-writer fence"):
        _adopt(state, [])
    assert "canonical_payload_recovery" not in state


def test_completed_zero_window_still_requires_historical_proof() -> None:
    fence = _mid_zero_fence()
    fence["status"] = "ready"
    state = _state(fence)

    with pytest.raises(SoperatorMigrationPhasePending, match="ready dual-writer fence"):
        _adopt(state, [])
    assert "canonical_payload_recovery" not in state


def test_written_payload_never_rebinds() -> None:
    fence = _mid_zero_fence()
    fence["zero_jail_verified_at"] = "2026-07-26T18:45:00Z"
    state = _state(fence)

    # A zero-window that already verified its jail payload is no longer an
    # unconsumed intent; it must take the full ready-fence adoption path.
    with pytest.raises(SoperatorMigrationPhasePending, match="ready dual-writer fence"):
        _adopt(state, [])
    assert "canonical_payload_recovery" not in state


def test_unchanged_fingerprint_is_a_noop() -> None:
    state = _state(_mid_zero_fence())
    state["canonical_payload"]["data_fingerprint"] = _NEW_FP
    writes: list[int] = []

    migration._legacy_rootfs_adopt_active_bridge_canonical_recovery(
        state=state,
        status="intent",
        handoff={"status": "manager-paused"},
        controller_bridge=_journal(),
        live={},
        compatible_sha=_NEW_SHA,
        canonical_data_fingerprint=_NEW_FP,
        canonical_files=(("slurm.conf", "/mnt/jail/etc/slurm/slurm.conf", _NEW_SHA),),
        canonical_source="target-ha-active-controller-bridge-compatible-transform",
        removed={},
        command_runner=None,
        kube_context="external-context",
        checkpoint_writer=lambda: writes.append(1),
    )
    assert writes == []
    assert "canonical_payload_recovery" not in state
