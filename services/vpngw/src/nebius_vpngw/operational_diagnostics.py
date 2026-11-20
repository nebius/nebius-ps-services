"""Presentation facts separate from service health and deployment authority."""

from __future__ import annotations

from dataclasses import dataclass


def _peer_assessment(record: dict | None) -> tuple[str, str]:
    if not isinstance(record, dict):
        return "unknown", "heartbeat evidence unavailable"
    reasons = record.get("reasons", [])
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str)
        or reason in {"current-boot-status-stale", "current-boot-guard-not-installed"}
        for reason in reasons
    ):
        return "unknown", "current-boot evidence unavailable"
    convergence = record.get("apply_convergence")
    if not isinstance(convergence, dict):
        return "unknown", "freshness evidence unavailable"
    if not record.get("guard_boot_id") or convergence.get("boot_id") != record["guard_boot_id"]:
        return "unknown", "current-boot identity mismatch"
    # Native status checks its monotonic observation against the same gateway
    # clock and current boot/mode. Wall time cannot revalidate that timestamp.
    fresh = convergence.get("fresh")
    if fresh is not True:
        return "unknown", (
            "heartbeat snapshot stale" if fresh is False else "freshness evidence unavailable"
        )
    heartbeat = record.get("heartbeat")
    if not isinstance(heartbeat, dict):
        return "unknown", "heartbeat evidence unavailable"
    if heartbeat.get("peer_v6_fresh") is True:
        return "authenticated-receive", ""
    failures = heartbeat.get("peer_send_consecutive_failures")
    if type(failures) is int and failures > 0:
        assessment = {
            "refused": "connection-refused",
            "timeout": "transport-timeout",
            "unreachable": "network-unreachable",
            "tls": "tls-failure",
        }.get(str(heartbeat.get("peer_send_error_kind", "")), "unknown")
        return assessment, "unclassified peer send failure" if assessment == "unknown" else ""
    if heartbeat.get("peer_v6_fresh") is False:
        return "stale", "no fresh authenticated heartbeat"
    return "unknown", "heartbeat evidence unavailable"


def peer_channel(record: dict | None) -> str:
    return _peer_assessment(record)[0]


@dataclass(frozen=True)
class PeerChannelObservation:
    assessment: str
    reason: str


def observe_peer_channel(record: dict | None) -> PeerChannelObservation:
    return PeerChannelObservation(*_peer_assessment(record))


def peer_channel_text(observed: PeerChannelObservation | None):
    from rich.text import Text

    if observed is None:
        return Text("Unverified · collection evidence unavailable", style="yellow")
    label, style = {
        "authenticated-receive": ("Open · mTLS verified", "green"),
        "connection-refused": ("Connection refused", "red"),
        "transport-timeout": ("Timeout", "red"),
        "network-unreachable": ("Unreachable", "red"),
        "tls-failure": ("TLS failed", "red"),
    }.get(observed.assessment, ("Unverified", "yellow"))
    text = Text(label, style=style)
    if observed.reason:
        text.append(" · " + observed.reason)
    return text


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
