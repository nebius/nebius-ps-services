from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebius_vpngw.agent.vm_ha.progress import (
    TransferProgressIdentity,
    TransferProgressStore,
    planned_request_fingerprint,
    validate_transfer_progress,
)


def _request() -> dict[str, object]:
    return {
        "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-passive",
        "generation_id": "a" * 64,
        "requested_at": 10.0,
    }


def _identity() -> TransferProgressIdentity:
    return TransferProgressIdentity(
        cluster_id="cluster-a",
        candidate_node_id="node-passive",
        former_owner_node_id="node-active",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests={
            "configuration": "b" * 64,
            "static_routes": "c" * 64,
            "bgp_policy": "d" * 64,
        },
        route_runtime_id="route-runtime-a",
        intent="planned-failover",
        request_fingerprint=planned_request_fingerprint(_request()),
        first_operation_id="boot-a:1:stop-former-owner:node-active",
        ownership_incarnation=2,
    )


def _store(path: Path, times: list[float]) -> TransferProgressStore:
    def writer(target: Path, payload: dict[str, object]) -> None:
        target.write_text(json.dumps(payload), encoding="utf-8")

    return TransferProgressStore(path, writer=writer, clock=lambda: times.pop(0))


def test_planned_request_fingerprint_is_stable_and_rejects_extra_fields() -> None:
    request = _request()

    assert planned_request_fingerprint(request) == planned_request_fingerprint(
        dict(reversed(tuple(request.items())))
    )
    request["extra"] = "not-authority"
    with pytest.raises(ValueError, match="cannot be fingerprinted"):
        planned_request_fingerprint(request)


def test_progress_closes_only_the_exact_attempted_operation(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    store = _store(path, [10.0, 11.0, 12.0])
    identity = _identity()
    operation = identity.first_operation_id

    store.attempting(
        identity,
        action="stop-former-owner",
        operation_id=operation,
        boot_id="boot-a",
        ownership_epoch="7",
    )
    store.completed(action="attach-candidate", operation_id="foreign")
    assert store.load()["history"][-1]["state"] == "attempting"  # type: ignore[index]

    store.completed(action="stop-former-owner", operation_id=operation)
    progress = validate_transfer_progress(json.loads(path.read_text(encoding="utf-8")))
    assert [(entry["state"], entry["sequence"]) for entry in progress["history"]] == [
        ("attempting", 1),
        ("completed", 2),
    ]


def test_failed_effect_can_retry_the_same_durable_operation(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    store = _store(path, [10.0, 11.0, 12.0])
    identity = _identity()
    operation = identity.first_operation_id

    store.attempting(
        identity,
        action="stop-former-owner",
        operation_id=operation,
        boot_id="boot-a",
        ownership_epoch="7",
    )
    store.failed(action="stop-former-owner", operation_id=operation)
    store.attempting(
        identity,
        action="stop-former-owner",
        operation_id=operation,
        boot_id="boot-a",
        ownership_epoch="7",
    )

    progress = store.load()
    assert progress is not None
    assert [entry["state"] for entry in progress["history"]] == [
        "attempting",
        "failed",
        "attempting",
    ]
    assert progress["history"][1]["error_type"] == "effect-failed"


def test_authoritative_postcondition_can_close_an_effect_reported_failed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "progress.json"
    store = _store(path, [10.0, 11.0, 12.0])
    identity = _identity()
    operation = identity.first_operation_id

    store.attempting(
        identity,
        action="stop-former-owner",
        operation_id=operation,
        boot_id="boot-a",
        ownership_epoch="7",
    )
    store.failed(action="stop-former-owner", operation_id=operation)
    store.completed(action="stop-former-owner", operation_id=operation)

    progress = store.load()
    assert progress is not None
    assert [entry["state"] for entry in progress["history"]] == [
        "attempting",
        "failed",
        "completed",
    ]


def test_new_boot_can_resume_the_same_transfer_with_monotonic_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "progress.json"
    store = _store(path, [10.0, 11.0])
    identity = _identity()

    store.attempting(
        identity,
        action="stop-former-owner",
        operation_id=identity.first_operation_id,
        boot_id="boot-a",
        ownership_epoch="7",
    )
    store.attempting(
        identity,
        action="stop-former-owner",
        operation_id="boot-b:2:stop-former-owner:node-active",
        boot_id="boot-b",
        ownership_epoch="8",
    )

    progress = store.load()
    assert progress is not None
    assert [(entry["sequence"], entry["boot_id"]) for entry in progress["history"]] == [
        (1, "boot-a"),
        (2, "boot-b"),
    ]
