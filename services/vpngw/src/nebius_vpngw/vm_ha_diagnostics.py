"""Identity-free negative readiness evidence; never grants lifecycle authority."""

from __future__ import annotations

import json

READINESS_SCHEMA = "nebius-vpngw/vm-ha-standby-replacement-not-ready-v1"
READINESS_REASONS = frozenset(
    {
        "inhibition-mismatch",
        "capability-unavailable",
        "identity-mismatch",
        "owner-state-not-ready",
        "data-plane-not-active",
        "promotion-not-ready",
        "ownership-unverified",
        "apply-locked",
        "apply-operation-pending",
        "inhibition-not-acknowledged",
        "transfer-not-quiescent",
        "operation-pending",
        "controller-operation-pending",
        "rearm-operation-pending",
    }
)


def readiness_reasons(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or len(value) > len(READINESS_REASONS):
        return ()
    if any(not isinstance(item, str) or item not in READINESS_REASONS for item in value):
        return ()
    return tuple(sorted(set(value)))


def encode_not_ready(reasons: tuple[str, ...]) -> str:
    return json.dumps(
        {"schema": READINESS_SCHEMA, "reasons": readiness_reasons(reasons)}, sort_keys=True
    )


def decode_not_ready(raw: bytes) -> tuple[str, ...]:
    if len(raw) > 2048:
        return ()
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError):
        return ()
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "reasons"}
        or value.get("schema") != READINESS_SCHEMA
    ):
        return ()
    return readiness_reasons(value["reasons"])
