"""Presentation facts separate from service health and deployment authority."""

from __future__ import annotations

import math
import time


def peer_channel(record: dict | None, *, now: float | None = None) -> str:
    if not isinstance(record, dict):
        return "unknown"
    reasons = record.get("reasons", [])
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str)
        or reason
        in {
            "current-boot-status-stale",
            "current-boot-guard-not-installed",
        }
        for reason in reasons
    ):
        return "unknown"
    convergence = record.get("apply_convergence")
    if not isinstance(convergence, dict):
        return "unknown"
    sampled = convergence.get("observed_at")
    if (
        not isinstance(sampled, (int, float))
        or isinstance(sampled, bool)
        or not math.isfinite(sampled)
        or not 0 <= (time.time() if now is None else now) - sampled < 10
        or not record.get("guard_boot_id")
        or convergence.get("boot_id") != record["guard_boot_id"]
    ):
        return "unknown"
    heartbeat = (record or {}).get("heartbeat")
    if not isinstance(heartbeat, dict):
        return "unknown"
    if heartbeat.get("peer_v6_fresh") is True:
        return "authenticated-receive"
    failures = heartbeat.get("peer_send_consecutive_failures")
    if isinstance(failures, int) and failures > 0:
        return {
            "refused": "connection-refused",
            "timeout": "transport-timeout",
            "unreachable": "network-unreachable",
            "tls": "tls-failure",
        }.get(str(heartbeat.get("peer_send_error_kind", "")), "unknown")
    return "stale" if heartbeat.get("peer_v6_fresh") is False else "unknown"


def health_text(value: str, *, fallback: str = ""):
    from rich.text import Text

    return Text(value, style="green" if value.casefold() == "healthy" else fallback)


def peer_direction(assessment: str) -> str:
    if assessment == "authenticated-receive" or assessment == "stale":
        return "peer-to-gateway"
    if assessment in {
        "connection-refused",
        "transport-timeout",
        "network-unreachable",
        "tls-failure",
    }:
        return "gateway-to-peer"
    return "unknown"
