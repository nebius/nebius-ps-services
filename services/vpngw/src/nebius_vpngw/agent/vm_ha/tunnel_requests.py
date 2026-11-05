"""Local request mailbox; only the running HA controller admits native effects."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import canonical_json
from .store import CorruptStateError, _envelope, _unwrap, atomic_write_json
from .tunnels import STATE_NAME, Authority, read_state

REQUEST_NAME = "tunnel-request.json"


def read_request(state_dir: Path) -> dict[str, Any] | None:
    try:
        value = dict(_unwrap(json.loads((state_dir / REQUEST_NAME).read_text())))
        if set(value) != {"id", "authority", "verb", "target", "expires", "result"}:
            raise ValueError
        Authority(**value["authority"])
        if (
            value["verb"] not in {"failover", "failback"}
            or value["target"] is not None
            and not isinstance(value["target"], str)
            or not isinstance(value["id"], str)
            or len(value["id"]) != 64
            or type(value["expires"]) not in {int, float}
            or not math.isfinite(value["expires"])
            or value["result"]
            not in {"outstanding", "request-expired", "authority-changed", "request-rejected"}
        ):
            raise ValueError
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError):
        raise CorruptStateError("HA tunnel request is malformed") from None


def write_request(state_dir: Path, value: dict[str, Any]) -> None:
    atomic_write_json(state_dir / REQUEST_NAME, _envelope(value))


def tunnel_status(state_dir: Path) -> dict[str, Any]:
    # Admission persists the operation before removing the mailbox. Read the
    # mailbox first so an interleaving can show queued or admitted, never neither.
    request = read_request(state_dir)
    value = read_state(state_dir / STATE_NAME)
    value["pending_request"] = request
    return value


def submit_request(
    cfg: dict[str, Any], state_dir: Path, payload: dict[str, Any], *, now: float | None = None
) -> dict[str, str]:
    """Checkpoint a request without cloud calls, listeners or identity invalidation.

    The mailbox is not authority: the service verifies exact current ownership,
    generation, incarnation and fresh authenticated peer capability before effects.
    """
    from ..vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

    if not isinstance(payload, dict) or set(payload) != {"authority", "verb", "target"}:
        raise ValueError("HA tunnel request has an invalid shape")
    expected = Authority(**payload["authority"])
    if payload["verb"] not in {"failover", "failback"} or (
        payload["target"] is not None and not isinstance(payload["target"], str)
    ):
        raise ValueError("HA tunnel request is invalid")
    ha = cfg["vm_ha"]
    if (
        expected.cluster != ha["cluster_id"]
        or expected.owner != ha["node"]["node_id"]
        or expected.allocation != ha["runtime_binding"]["shared_allocation_id"]
        or expected.generation != ha["generation"]["generation_id"]
    ):
        raise RuntimeError("HA tunnel request does not match the installed member")
    # Healthy controller/rearm ticks briefly hold this same writer lock. Wait
    # for that existing writer to finish before checkpointing a request; a
    # nonblocking acquisition makes ordinary CLI admission timing-dependent.
    # The controller still revalidates exact authority before any effects.
    descriptor = _acquire_rearm_lock(state_dir / REARM_LOCK_PATH.name, timeout_seconds=10.0)
    if descriptor is None:
        raise RuntimeError("HA writer is busy")
    try:
        now = time.time() if now is None else now
        existing = read_request(state_dir)
        if (
            existing is not None
            and existing["result"] == "outstanding"
            and now < existing["expires"]
        ):
            if all(existing[k] == payload[k] for k in payload):
                return {"operation_id": existing["id"], "result": "outstanding"}
            raise RuntimeError("another HA tunnel request is outstanding")
        state = read_state(state_dir / STATE_NAME)
        operation = state["operation"]
        if operation is not None and operation["phase"] not in {"failed", "succeeded"}:
            if (
                state["authority"] == asdict(expected)
                and operation["requested_verb"] == payload["verb"]
                and (payload["target"] is None or operation["target"] == payload["target"])
            ):
                return {"operation_id": operation["id"], "result": "outstanding"}
            raise RuntimeError("another HA tunnel operation is outstanding")
        if (
            operation is not None
            and operation["phase"] == "succeeded"
            and state["authority"] == asdict(expected)
            and operation["requested_verb"] == payload["verb"]
            and payload["target"] == operation["requested_target"]
            and (
                state["selected"].get(operation["connection"]) == operation["target"]
                if operation["requested_verb"] == "failover"
                else operation["connection"] not in state["selected"]
                or state["selected"][operation["connection"]] == operation["target"]
            )
        ):
            return {"operation_id": operation["id"], "result": "succeeded"}
        request = {
            **payload,
            "id": hashlib.sha256((canonical_json(payload) + uuid.uuid4().hex).encode()).hexdigest(),
            "expires": now + 30,
            "result": "outstanding",
        }
        write_request(state_dir, request)
        return {"operation_id": request["id"], "result": "outstanding"}
    finally:
        os.close(descriptor)
