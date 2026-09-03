"""Strict, non-authoritative VM-HA transfer progress records."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRANSFER_PROGRESS_SCHEMA = "nebius-vpngw/vm-ha-transfer-progress-v1"
TRANSFER_PROGRESS_HISTORY_LIMIT = 32

TRANSFER_PROGRESS_ACTIONS = frozenset(
    {
        "stop-former-owner",
        "detach-former-attachment",
        "detach-candidate-for-reproof",
        "attach-candidate",
        "confirm-candidate-ownership",
        "prepare-candidate-dataplane",
        "reconcile-routes",
        "enable-active",
    }
)
TRANSFER_PROGRESS_STATES = frozenset({"attempting", "completed", "failed"})
TRANSFER_PROGRESS_INTENTS = frozenset(
    {"planned-failover", "planned-failback", "automatic-failover"}
)
_PLANNED_REQUEST_SCHEMAS = frozenset(
    {
        "nebius-vpngw/vm-ha-manual-failover-v1",
        "nebius-vpngw/vm-ha-manual-failback-v1",
    }
)
_REQUEST_FINGERPRINT_DOMAIN = b"nebius-vpngw/vm-ha-planned-request-v1\0"
_RECORD_IDENTITY_KEYS = (
    "allocation_id",
    "candidate_node_id",
    "cluster_id",
    "digests",
    "first_operation_id",
    "former_owner_node_id",
    "generation_id",
    "intent",
    "ownership_incarnation",
    "request_fingerprint",
    "route_runtime_id",
)


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def planned_request_fingerprint(payload: Mapping[str, Any]) -> str:
    """Return one domain-separated identity for an already strict request."""

    expected_keys = {"cluster_id", "generation_id", "node_id", "requested_at", "schema"}
    requested_at = payload.get("requested_at")
    if not (
        set(payload) == expected_keys
        and payload.get("schema") in _PLANNED_REQUEST_SCHEMAS
        and all(
            _is_nonempty_string(payload.get(key))
            for key in ("cluster_id", "generation_id", "node_id")
        )
        and isinstance(requested_at, (int, float))
        and not isinstance(requested_at, bool)
        and math.isfinite(float(requested_at))
    ):
        raise ValueError("VM-HA planned request cannot be fingerprinted")
    canonical = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(_REQUEST_FINGERPRINT_DOMAIN + canonical).hexdigest()


@dataclass(frozen=True)
class TransferProgressIdentity:
    cluster_id: str
    candidate_node_id: str
    former_owner_node_id: str
    allocation_id: str
    generation_id: str
    digests: Mapping[str, str]
    route_runtime_id: str
    intent: str
    request_fingerprint: str | None
    first_operation_id: str
    ownership_incarnation: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "candidate_node_id": self.candidate_node_id,
            "former_owner_node_id": self.former_owner_node_id,
            "allocation_id": self.allocation_id,
            "generation_id": self.generation_id,
            "digests": dict(self.digests),
            "route_runtime_id": self.route_runtime_id,
            "intent": self.intent,
            "request_fingerprint": self.request_fingerprint,
            "first_operation_id": self.first_operation_id,
            "ownership_incarnation": self.ownership_incarnation,
        }


def validate_transfer_progress(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one complete private progress record and return a plain copy."""

    expected_keys = {"schema", "history", *_RECORD_IDENTITY_KEYS}
    digests = payload.get("digests")
    intent = payload.get("intent")
    request_fingerprint = payload.get("request_fingerprint")
    ownership_incarnation = payload.get("ownership_incarnation")
    history = payload.get("history")
    if not (
        set(payload) == expected_keys
        and payload.get("schema") == TRANSFER_PROGRESS_SCHEMA
        and all(
            _is_nonempty_string(payload.get(key))
            for key in (
                "allocation_id",
                "candidate_node_id",
                "cluster_id",
                "first_operation_id",
                "former_owner_node_id",
                "route_runtime_id",
            )
        )
        and _is_sha256(payload.get("generation_id"))
        and isinstance(digests, Mapping)
        and set(digests) == {"bgp_policy", "configuration", "static_routes"}
        and all(_is_sha256(value) for value in digests.values())
        and intent in TRANSFER_PROGRESS_INTENTS
        and (
            _is_sha256(request_fingerprint)
            if intent in {"planned-failover", "planned-failback"}
            else request_fingerprint is None
        )
        and isinstance(ownership_incarnation, int)
        and not isinstance(ownership_incarnation, bool)
        and ownership_incarnation >= 0
        and isinstance(history, list)
        and 0 < len(history) <= TRANSFER_PROGRESS_HISTORY_LIMIT
    ):
        raise ValueError("VM-HA transfer progress record is invalid")

    previous_sequence = 0
    validated_history: list[dict[str, Any]] = []
    for raw_entry in history:
        expected_entry_keys = {
            "action",
            "boot_id",
            "error_type",
            "operation_id",
            "ownership_epoch",
            "recorded_at",
            "sequence",
            "state",
        }
        sequence = raw_entry.get("sequence") if isinstance(raw_entry, Mapping) else None
        recorded_at = raw_entry.get("recorded_at") if isinstance(raw_entry, Mapping) else None
        state = raw_entry.get("state") if isinstance(raw_entry, Mapping) else None
        error_type = raw_entry.get("error_type") if isinstance(raw_entry, Mapping) else None
        if not (
            isinstance(raw_entry, Mapping)
            and set(raw_entry) == expected_entry_keys
            and raw_entry.get("action") in TRANSFER_PROGRESS_ACTIONS
            and _is_nonempty_string(raw_entry.get("boot_id"))
            and _is_nonempty_string(raw_entry.get("operation_id"))
            and _is_nonempty_string(raw_entry.get("ownership_epoch"))
            and isinstance(sequence, int)
            and not isinstance(sequence, bool)
            and sequence > previous_sequence
            and isinstance(recorded_at, (int, float))
            and not isinstance(recorded_at, bool)
            and math.isfinite(float(recorded_at))
            and state in TRANSFER_PROGRESS_STATES
            and (error_type == "effect-failed" if state == "failed" else error_type is None)
        ):
            raise ValueError("VM-HA transfer progress history is invalid")
        previous_sequence = sequence
        validated_history.append(dict(raw_entry))

    result = dict(payload)
    result["digests"] = dict(digests)
    result["history"] = validated_history
    return result


class TransferProgressStore:
    """Atomically append presentation-only transitions for one exact lineage."""

    def __init__(
        self,
        path: Path,
        *,
        writer: Callable[[Path, dict[str, Any]], None],
        clock: Callable[[], float],
    ) -> None:
        self.path = path
        self.writer = writer
        self.clock = clock

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("VM-HA transfer progress record is invalid")
        return validate_transfer_progress(raw)

    @staticmethod
    def _matches_identity(record: Mapping[str, Any], identity: TransferProgressIdentity) -> bool:
        expected = identity.to_dict()
        return all(record.get(key) == expected[key] for key in _RECORD_IDENTITY_KEYS)

    def _write_transition(
        self,
        *,
        identity: TransferProgressIdentity,
        action: str,
        operation_id: str,
        boot_id: str,
        ownership_epoch: str,
        state: str,
        error_type: str | None,
        replace_foreign: bool = False,
    ) -> None:
        if action not in TRANSFER_PROGRESS_ACTIONS or state not in TRANSFER_PROGRESS_STATES:
            raise ValueError("VM-HA transfer progress transition is invalid")
        try:
            current = self.load()
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            current = None
        if current is None or not self._matches_identity(current, identity):
            if current is not None and not replace_foreign:
                raise ValueError("VM-HA transfer progress belongs to another lineage")
            history: list[dict[str, Any]] = []
            record = {"schema": TRANSFER_PROGRESS_SCHEMA, **identity.to_dict()}
        else:
            history = list(current["history"])
            record = {key: value for key, value in current.items() if key != "history"}

        if history:
            latest = history[-1]
            same_action = bool(
                latest["action"] == action and latest["operation_id"] == operation_id
            )
            if state == "attempting" and same_action and latest["state"] == "attempting":
                return
            if state in {"completed", "failed"}:
                completion_after_failed_effect = bool(
                    state == "completed" and same_action and latest["state"] == "failed"
                )
                if (
                    not (same_action and latest["state"] == "attempting")
                    and not completion_after_failed_effect
                ):
                    raise ValueError("VM-HA transfer progress terminal transition is out of order")
            if state == "attempting":
                retrying_failed_action = bool(same_action and latest["state"] == "failed")
                resuming_after_boot_change = latest["boot_id"] != boot_id
                if (
                    latest["state"] != "completed"
                    and not retrying_failed_action
                    and not resuming_after_boot_change
                ):
                    raise ValueError("VM-HA transfer progress advanced before completion")

        next_sequence = (int(history[-1]["sequence"]) + 1) if history else 1
        history.append(
            {
                "sequence": next_sequence,
                "action": action,
                "state": state,
                "operation_id": operation_id,
                "boot_id": boot_id,
                "ownership_epoch": ownership_epoch,
                "recorded_at": self.clock(),
                "error_type": error_type,
            }
        )
        record["history"] = history[-TRANSFER_PROGRESS_HISTORY_LIMIT:]
        self.writer(self.path, validate_transfer_progress(record))

    def attempting(
        self,
        identity: TransferProgressIdentity,
        *,
        action: str,
        operation_id: str,
        boot_id: str,
        ownership_epoch: str,
    ) -> None:
        self._write_transition(
            identity=identity,
            action=action,
            operation_id=operation_id,
            boot_id=boot_id,
            ownership_epoch=ownership_epoch,
            state="attempting",
            error_type=None,
            replace_foreign=True,
        )

    def completed(self, *, action: str, operation_id: str) -> None:
        current = self.load()
        if current is None:
            return
        latest = current["history"][-1]
        if latest["state"] == "completed":
            return
        if latest["action"] != action or latest["operation_id"] != operation_id:
            return
        identity = TransferProgressIdentity(**{key: current[key] for key in _RECORD_IDENTITY_KEYS})
        self._write_transition(
            identity=identity,
            action=str(latest["action"]),
            operation_id=str(latest["operation_id"]),
            boot_id=str(latest["boot_id"]),
            ownership_epoch=str(latest["ownership_epoch"]),
            state="completed",
            error_type=None,
        )

    def failed(self, *, action: str, operation_id: str) -> None:
        current = self.load()
        if current is None:
            return
        latest = current["history"][-1]
        if latest["action"] != action or latest["operation_id"] != operation_id:
            return
        identity = TransferProgressIdentity(**{key: current[key] for key in _RECORD_IDENTITY_KEYS})
        self._write_transition(
            identity=identity,
            action=action,
            operation_id=operation_id,
            boot_id=str(latest["boot_id"]),
            ownership_epoch=str(latest["ownership_epoch"]),
            state="failed",
            error_type="effect-failed",
        )
