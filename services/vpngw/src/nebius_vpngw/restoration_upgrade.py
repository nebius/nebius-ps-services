"""Deployment-only v1 conversion. Runtime readers accept only canonical v2."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .agent.vm_ha.models import canonical_json
from .agent.vm_ha.restoration import (
    RESTORATION_AUTHORIZATION_SCHEMA,
    RestorationPhase,
    StandbyRestorationAuthorization,
    StandbyRestorationStore,
)
from .agent.vm_ha.retry_window import WINDOW_SECONDS, RetryWindow
from .agent.vm_ha.store import atomic_write_json

JOURNAL = "standby-restoration-upgrade-v2.json"
OLD_SCHEMA = "nebius-vpngw/vm-ha-standby-restoration-authorization-v1"
RECOVERY = "standby-restoration-apply.json"


def recovery_transaction(state_dir: Path, expected: dict[str, Any], *, operation_id: str) -> dict:
    value = json.loads((state_dir / RECOVERY).read_text())
    if not (
        value.get("schema") == "nebius-vpngw/restoration-apply-v1"
        and value.get("identity") == expected
        and value.get("operation_id") == operation_id
        and value.get("phase") in {"prepared", "locked", "installed", "activated", "released"}
    ):
        raise ValueError("restoration apply transaction differs")
    return value


def park_for_upgrade(
    state_dir: Path, expected: dict[str, Any], *, operation_id: str, artifact: str
) -> dict:
    """Under the rearm lock, retain history and quiesce only admitted recovery."""
    path = state_dir / RECOVERY
    if path.exists():
        value = recovery_transaction(state_dir, expected, operation_id=operation_id)
        if value["artifact"] != artifact:
            raise ValueError("restoration apply artifact changed")
        if value["phase"] == "prepared":
            finish_parking(state_dir, value)
        return value
    digest = recovery_evidence(state_dir, expected)
    if (state_dir / "rearm-cloud-operation.json").exists():
        raise ValueError("accepted Start must settle before package fencing")
    if (state_dir / "rearm-request.json").exists():
        raise ValueError("another operator restoration request is pending")
    store = StandbyRestorationStore(state_dir)
    raw = json.loads(store.path.read_text())
    value = {
        "schema": "nebius-vpngw/restoration-apply-v1",
        "identity": expected,
        "operation_id": operation_id,
        "artifact": artifact,
        "evidence": digest,
        "predecessor": raw,
        "phase": "prepared",
    }
    atomic_write_json(path, value)
    finish_parking(state_dir, value)
    return value


def finish_parking(state_dir: Path, value: dict) -> None:
    """Replay the write after its history journal, retaining the installed codec."""
    store = StandbyRestorationStore(state_dir)
    raw = value["predecessor"]
    parked = {
        **raw,
        "phase": "blocked",
        "blocked_reason": "compute-start-failed",
        "next_attempt_at": None,
    }
    current = json.loads(store.path.read_text())
    if current not in (raw, parked):
        raise ValueError("restoration parking predecessor changed")
    if current != parked:
        atomic_write_json(store.path, parked)


def recovery_evidence(
    state_dir: Path, expected: dict[str, Any], *, operation_id: str | None = None
) -> str:
    """Read-only admission, in addition to apply's independent cloud/SSH proof."""
    from .agent.vm_ha.auto_healing import (
        AutoHealingPolicyPhase,
        AutoHealingPolicyStore,
        StandbyAutoHealing,
    )
    from .agent.vm_ha.restoration import policy_authorizes_restoration

    store = StandbyRestorationStore(state_dir)
    raw = json.loads(store.path.read_text())
    record = candidate(raw) if raw.get("schema") == OLD_SCHEMA else store.load()
    if record is None or record.phase is RestorationPhase.ARMED:
        raise ValueError("restoration recovery requires committed authority")
    receipt = store._load_durable_receipt()
    store._prove_receipt_binding(
        record, receipt, compare_source=record.source.value != "operator-restoration"
    )
    if not (
        record.cluster_id == expected["cluster_id"]
        and record.owner_node_id == expected["owner_node_id"]
        and record.former_owner_node_id == expected["peer_node_id"]
        and record.allocation_id == expected["allocation_id"]
        and record.generation_id == expected["generation_id"]
        and dict(record.digests) == expected["digests"]
    ):
        raise ValueError("restoration recovery identity differs")
    policy = AutoHealingPolicyStore(state_dir).load()
    if not (
        policy is not None
        and policy.phase is AutoHealingPolicyPhase.COMMITTED
        and policy.desired is StandbyAutoHealing.ENABLED
        and policy_authorizes_restoration(policy, record)
    ):
        raise ValueError("restoration recovery requires its committed enabled policy")
    if any(
        (state_dir / name).exists()
        for name in (
            "transfer-lineage.json",
            "accepted-cloud-operation.json",
            "mtls/inhibition.json",
            "standby-replacement.inhibition.json",
        )
    ):
        raise ValueError("restoration recovery has unresolved writer authority")
    lock_path = state_dir / "apply.lock"
    if lock_path.exists():
        if operation_id is None:
            raise ValueError("restoration recovery has an apply lock")
        recovery_transaction(state_dir, expected, operation_id=operation_id)
        lock = json.loads(lock_path.read_text())
        if any(
            lock.get(k) != v
            for k, v in {
                "cluster_id": expected["cluster_id"],
                "node_id": expected["owner_node_id"],
                "generation_id": expected["generation_id"],
                "operation_id": operation_id,
            }.items()
        ):
            raise ValueError("restoration recovery apply lock differs")
    accepted_path = state_dir / "rearm-cloud-operation.json"
    if accepted_path.exists():
        accepted = json.loads(accepted_path.read_text())
        checkpoint = json.loads((state_dir / "rearm-checkpoint.json").read_text())
        if not (
            set(accepted) == {"schema", "action_operation_id", "cloud_operation_id", "kind"}
            and accepted["schema"] == "nebius-vpngw/vm-ha-cloud-operation-v1"
            and accepted["kind"] == "start-instance"
            and isinstance(accepted["cloud_operation_id"], str)
            and accepted["cloud_operation_id"]
            and accepted["action_operation_id"]
            == record.rearm_operation_id
            == checkpoint.get("operation_id")
            and checkpoint.get("target_node_id") == record.former_owner_node_id
            and checkpoint.get("receipt_id") == record.promotion_receipt_id
            and checkpoint.get("stopped_revision") == record.stopped_revision
            and checkpoint.get("phase") in {"starting", "start-requested", "running", "blocked"}
            and record.phase in {RestorationPhase.START_ACCEPTED, RestorationPhase.BLOCKED}
        ):
            raise ValueError("restoration recovery accepted operation is not exactly bound")
        result_path = state_dir / "rearm-operation-result.json"
        # Older writers retained accepted operations after blocking without a
        # result file. Exact binding admits observation, never a new Start.
        if record.phase is RestorationPhase.BLOCKED and result_path.exists():
            result = json.loads(result_path.read_text())
            if not (
                result.get("schema") == "nebius-vpngw/vm-ha-start-result-v1"
                and result.get("action_operation_id") == accepted["action_operation_id"]
                and result.get("cloud_operation_id") == accepted["cloud_operation_id"]
                and type(result.get("successful")) is bool
            ):
                raise ValueError("accepted Start settlement proof is unavailable")
    return hashlib.sha256(
        canonical_json(
            {"receipt": receipt, "policy": policy.to_dict(), "identity": expected}
        ).encode("ascii")
    ).hexdigest()


def settle_accepted(
    state_dir: Path, expected: dict, *, accepted: dict, instance_id: str, successful: bool
) -> None:
    """Persist an operator-observed exact result before retiring its old journal.

    The caller holds rearm.lock. The original authorization remains in its
    installed schema and blocked; only later fresh controller proof can retry.
    """
    recovery_evidence(state_dir, expected)
    path = state_dir / "rearm-cloud-operation.json"
    if json.loads(path.read_text()) != accepted or type(successful) is not bool:
        raise ValueError("accepted Start settlement changed")
    store = StandbyRestorationStore(state_dir)
    raw = json.loads(store.path.read_text())
    result = {
        "schema": "nebius-vpngw/vm-ha-start-result-v1",
        "action_operation_id": accepted["action_operation_id"],
        "cloud_operation_id": accepted["cloud_operation_id"],
        "instance_id": instance_id,
        "successful": successful,
    }
    atomic_write_json(state_dir / "rearm-operation-result.json", result)
    atomic_write_json(
        store.path,
        {
            **raw,
            "phase": "blocked",
            "next_attempt_at": None,
            "blocked_reason": "compute-start-failed"
            if successful
            else "compute-start-permanent-failure",
        },
    )
    checkpoint = state_dir / "rearm-checkpoint.json"
    current = json.loads(checkpoint.read_text())
    atomic_write_json(
        checkpoint, {**current, "phase": "blocked", "failure_reason": "explicit-retry-required"}
    )
    path.unlink()
    import os

    directory = os.open(state_dir, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def prepare_recovery(state_dir: Path, expected: dict[str, Any], *, operation_id: str) -> None:
    """Prepare one operator intent after package conversion, before restarting writers."""
    from .agent.vm_ha.rearm_request import validate_request
    from .agent.vm_ha.restoration import RestorationSource

    recovery_evidence(state_dir, expected, operation_id=operation_id)
    store = StandbyRestorationStore(state_dir)
    record = store.load()
    assert record is not None and record.promotion_receipt_id is not None
    if (state_dir / "rearm-cloud-operation.json").exists():
        return  # The upgraded sole writer must observe this exact operation first.
    request_id = hashlib.sha256(("restoration-apply-v2:" + operation_id).encode()).hexdigest()
    if (
        record.source is RestorationSource.OPERATOR_RESTORATION
        and record.request_fingerprint == request_id
    ):
        return  # Resuming this apply never grants a second retry allowance.
    path = state_dir / "rearm-request.json"
    if path.exists():
        request = json.loads(path.read_text())
        validate_request(
            request,
            cluster=record.cluster_id,
            owner=record.owner_node_id,
            peer=record.former_owner_node_id,
            generation=record.generation_id,
            receipt=record.promotion_receipt_id,
        )
        if request["request_id"] != request_id:
            raise ValueError("another operator restoration request is pending")
        return
    if (
        record.phase is RestorationPhase.START_ACCEPTED
        and record.retry_window is not None
        and record.retry_window.elapsed >= WINDOW_SECONDS
    ):
        record = store.block(
            receipt_id=record.promotion_receipt_id,
            reason="automatic-retry-exhausted",
            updated_at=time.time(),
        )
    if record.phase is RestorationPhase.BLOCKED:
        assert record.promotion_receipt_id is not None
        from dataclasses import replace

        # Preserve the previous budget and identity until the controller proves
        # and publishes the new operator authority. Parking may precede Start.
        store._write(
            replace(
                record,
                phase=RestorationPhase.COMMITTED,
                blocked_reason=None,
                next_attempt_at=None,
                updated_at=time.time(),
            )
        )
    atomic_write_json(
        path,
        {
            "schema": "nebius-vpngw/vm-ha-rearm-request-v1",
            "cluster_id": record.cluster_id,
            "node_id": record.owner_node_id,
            "owner_node_id": record.owner_node_id,
            "target_node_id": record.former_owner_node_id,
            "generation_id": record.generation_id,
            "promotion_receipt_id": record.promotion_receipt_id,
            "request_id": request_id,
            "requested_at": time.time(),
        },
    )


def recovery_progress(state_dir: Path, expected: dict, *, operation_id: str) -> bool:
    """Observe the same apply-owned request/authority without renewing its budget."""
    from .agent.vm_ha.rearm_request import validate_request

    recovery_evidence(state_dir, expected, operation_id=operation_id)
    record = StandbyRestorationStore(state_dir).load()
    assert record is not None and record.promotion_receipt_id is not None
    if record.phase in {RestorationPhase.ARMED, RestorationPhase.BLOCKED}:
        return False
    request_id = hashlib.sha256(("restoration-apply-v2:" + operation_id).encode()).hexdigest()
    if record.source.value == "operator-restoration" and record.request_fingerprint == request_id:
        return True
    path = state_dir / "rearm-request.json"
    if not path.exists():
        return _settled_predecessor_progress(state_dir, expected, operation_id, record)
    value = json.loads(path.read_text())
    validate_request(
        value,
        cluster=record.cluster_id,
        owner=record.owner_node_id,
        peer=record.former_owner_node_id,
        generation=record.generation_id,
        receipt=record.promotion_receipt_id,
    )
    return bool(value["request_id"] == request_id)


def _settled_predecessor_progress(
    state_dir: Path,
    expected: dict,
    operation_id: str,
    record: StandbyRestorationAuthorization,
) -> bool:
    """Observe a previously started peer without granting a new Start allowance."""
    if record.phase not in {
        RestorationPhase.RUNNING,
        RestorationPhase.AWAITING_STANDBY,
        RestorationPhase.COMPLETED,
    }:
        return False
    try:
        transaction = recovery_transaction(state_dir, expected, operation_id=operation_id)
        raw = transaction["predecessor"]
        predecessor = (
            candidate(raw)
            if raw.get("schema") == OLD_SCHEMA
            else StandbyRestorationAuthorization.from_mapping(raw)
        )
        checkpoint = json.loads((state_dir / "rearm-checkpoint.json").read_text())
        return bool(
            transaction["phase"] == "released"
            and predecessor.authorization_id
            == predecessor.identity_digest()
            == record.authorization_id
            and predecessor.promotion_receipt_id == record.promotion_receipt_id
            and checkpoint.get("phase") == "running"
            and checkpoint.get("receipt_id") == record.promotion_receipt_id
            and checkpoint.get("target_node_id") == record.former_owner_node_id
            and record.rearm_operation_id is not None
            and checkpoint.get("operation_id") == record.rearm_operation_id
            and record.stopped_revision is not None
            and checkpoint.get("stopped_revision") == record.stopped_revision
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def candidate(raw: dict[str, Any]) -> StandbyRestorationAuthorization:
    """Validate the exact predecessor and preserve unknown history as unknown."""
    if (
        raw.get("schema") != OLD_SCHEMA
        or type(raw.get("attempt_count")) is not int
        or not 0 <= raw["attempt_count"] <= 5
    ):
        raise ValueError("restoration upgrade predecessor is invalid")
    new = {
        **raw,
        "schema": RESTORATION_AUTHORIZATION_SCHEMA,
        "ownership_incarnation": None,
        "baseline_epoch": None,
        "retry_window": None,
    }
    keys = {
        "allocation_id",
        "certificate_id",
        "cluster_id",
        "digests",
        "former_owner_node_id",
        "generation_id",
        "owner_node_id",
        "policy_digest",
        "policy_operation_id",
        "request_fingerprint",
        "schema",
        "source",
    }
    old_identity = {key: raw[key] for key in keys}
    if (
        raw["authorization_id"]
        != hashlib.sha256(canonical_json(old_identity).encode("ascii")).hexdigest()
    ):
        raise ValueError("restoration upgrade predecessor identity differs")
    new["authorization_id"] = hashlib.sha256(
        canonical_json({**old_identity, "schema": RESTORATION_AUTHORIZATION_SCHEMA}).encode("ascii")
    ).hexdigest()
    if raw["attempt_count"]:
        # No v1 timestamp proves when its first submission occurred. An accepted
        # operation can still settle; fresh submissions need operator authority.
        new["retry_window"] = RetryWindow(
            "legacy-timing-unavailable",
            raw["updated_at"],
            0.0,
            WINDOW_SECONDS,
        ).to_dict()
    return StandbyRestorationAuthorization.from_mapping(new)


def convert(state_dir: Path, *, artifact: str, boot: str, publish: bool) -> bool:
    """Caller proves exact package/config identity and holds stopped-writer locks."""
    store = StandbyRestorationStore(state_dir)
    if not store.path.exists():
        return False
    raw = json.loads(store.path.read_text())
    if raw.get("schema") == RESTORATION_AUTHORIZATION_SCHEMA:
        store.load()
        return False
    record = candidate(raw)
    if record.phase is not RestorationPhase.ARMED:
        store._prove_receipt_binding(
            record,
            store._load_durable_receipt(),
            compare_source=record.source.value != "operator-restoration",
        )
    if not publish:
        return True
    journal_path = state_dir / JOURNAL
    journal = {
        "schema": "nebius-vpngw/restoration-upgrade-v2",
        "artifact": artifact,
        "boot_id": boot,
        "predecessor": raw,
        "candidate": record.to_dict(),
    }
    if journal_path.exists():
        previous = json.loads(journal_path.read_text())
        if {key: value for key, value in previous.items() if key != "boot_id"} != {
            key: value for key, value in journal.items() if key != "boot_id"
        }:
            raise ValueError("restoration upgrade admission changed")
    else:
        atomic_write_json(journal_path, journal)
    if json.loads(store.path.read_text()) != raw:
        raise ValueError("restoration upgrade predecessor changed")
    atomic_write_json(store.path, record.to_dict())
    return True
