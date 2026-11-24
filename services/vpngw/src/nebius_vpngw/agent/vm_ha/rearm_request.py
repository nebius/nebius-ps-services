"""Shared strict codec for explicit operator retry intent."""

from __future__ import annotations

import math
import re
from typing import Any


def validate_request(
    payload: Any,
    *,
    cluster: str,
    owner: str,
    peer: str,
    generation: str,
    receipt: str,
) -> None:
    if not (
        isinstance(payload, dict)
        and set(payload)
        == {
            "cluster_id",
            "owner_node_id",
            "node_id",
            "generation_id",
            "promotion_receipt_id",
            "request_id",
            "requested_at",
            "schema",
            "target_node_id",
        }
        and payload["schema"] == "nebius-vpngw/vm-ha-rearm-request-v1"
        and payload["cluster_id"] == cluster
        and payload["owner_node_id"] == payload["node_id"] == owner
        and payload["generation_id"] == generation
        and payload["target_node_id"] == peer
        and payload["promotion_receipt_id"] == receipt
        and isinstance(payload["request_id"], str)
        and re.fullmatch(r"[0-9a-f]{64}", payload["request_id"])
        and type(payload["requested_at"]) in {int, float}
        and math.isfinite(payload["requested_at"])
        and payload["requested_at"] >= 0
    ):
        raise ValueError("VM-HA rearm request is invalid or stale")
