"""Strict identity for operation-bound VM-HA transfer inhibition records."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

STANDBY_REPLACEMENT_INHIBITION_CAPABILITY = "vm-ha-standby-replacement-inhibition-v2"
LIVE_PEER_REPLACEMENT_CAPABILITY = "vm-ha-live-peer-replacement-v4"
STANDBY_REPLACEMENT_INHIBITION_FILENAME = "standby-replacement.inhibition.json"
STANDBY_REPLACEMENT_INHIBITION_SCHEMA = "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1"
STANDBY_REPLACEMENT_RELEASE_FILENAME = "standby-replacement.release.json"
STANDBY_REPLACEMENT_RELEASE_SCHEMA = "nebius-vpngw/vm-ha-standby-replacement-release-v1"

_DIGEST = re.compile(r"[0-9a-f]{64}")


def standby_replacement_inhibition_operation_id(
    state_dir: Path,
    *,
    cluster_id: str,
    node_id: str,
    generation_id: str,
) -> str | None:
    """Read one exact-generation inhibition without creating runtime state."""

    path = state_dir / STANDBY_REPLACEMENT_INHIBITION_FILENAME
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("VM-HA standby replacement inhibition path is invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("VM-HA standby replacement inhibition is malformed") from error
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema",
        "cluster_id",
        "node_id",
        "generation_id",
        "operation_id",
    }:
        raise ValueError("VM-HA standby replacement inhibition is invalid")
    operation_id = payload.get("operation_id")
    if (
        payload.get("schema") != STANDBY_REPLACEMENT_INHIBITION_SCHEMA
        or payload.get("cluster_id") != cluster_id
        or payload.get("node_id") != node_id
        or payload.get("generation_id") != generation_id
        or not isinstance(operation_id, str)
        or _DIGEST.fullmatch(operation_id) is None
    ):
        raise ValueError("VM-HA standby replacement inhibition identity is stale")
    return operation_id


def standby_replacement_release_operation_id(
    state_dir: Path,
    *,
    cluster_id: str,
    node_id: str,
    generation_id: str,
) -> str | None:
    """Read the exact durable receipt for a completed inhibition release."""

    path = state_dir / STANDBY_REPLACEMENT_RELEASE_FILENAME
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("VM-HA standby replacement release path is invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("VM-HA standby replacement release is malformed") from error
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema",
        "cluster_id",
        "node_id",
        "generation_id",
        "operation_id",
    }:
        raise ValueError("VM-HA standby replacement release is invalid")
    operation_id = payload.get("operation_id")
    if (
        payload.get("schema") != STANDBY_REPLACEMENT_RELEASE_SCHEMA
        or payload.get("cluster_id") != cluster_id
        or payload.get("node_id") != node_id
        or payload.get("generation_id") != generation_id
        or not isinstance(operation_id, str)
        or _DIGEST.fullmatch(operation_id) is None
    ):
        raise ValueError("VM-HA standby replacement release identity is stale")
    return operation_id
