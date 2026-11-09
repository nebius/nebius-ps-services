"""Immutable command approval identity, retained by existing operation owners."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SCHEMA = "nebius-vpngw.apply-approval.v1"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def manifest_identity(manifest: dict) -> str:
    # Parent/approval metadata is added only after the outer approval is bound.
    return digest({k: v for k, v in manifest.items() if k not in {"command_approval", "ha_parent"}})


def bind(approval: str, scope: dict) -> dict[str, str]:
    if not re.fullmatch(r"[a-f0-9]{64}", approval):
        raise RuntimeError("invalid command approval identity")
    return {"schema": SCHEMA, "approval": approval, "scope": digest(scope)}


def validate(record: dict) -> None:
    if (
        not isinstance(record, dict)
        or set(record) != {"schema", "approval", "scope"}
        or record.get("schema") != SCHEMA
        or not re.fullmatch(r"[a-f0-9]{64}", str(record.get("approval", "")))
        or not re.fullmatch(r"[a-f0-9]{64}", str(record.get("scope", "")))
    ):
        raise RuntimeError("interrupted apply command authority changed")


def resume(record: dict, scope: dict) -> str:
    validate(record)
    if record["scope"] != digest(scope):
        raise RuntimeError("interrupted apply command authority changed")
    return record["approval"]
