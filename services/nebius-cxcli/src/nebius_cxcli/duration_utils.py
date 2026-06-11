"""Shared duration parsing helpers."""

from __future__ import annotations

import re

_GO_DURATION_RE = re.compile(r"(?P<value>[0-9]+)(?P<unit>ns|us|\u00b5s|ms|s|m|h)")
_GO_DURATION_UNIT_NS = {
    "ns": 1,
    "us": 1_000,
    "\u00b5s": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "m": 60 * 1_000_000_000,
    "h": 3600 * 1_000_000_000,
}


def parse_go_duration_seconds(value: str) -> int:
    """Parse a positive Go-style duration and return whole seconds."""

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Duration must not be empty.")
    total_ns = 0
    position = 0
    for match in _GO_DURATION_RE.finditer(raw):
        if match.start() != position:
            raise ValueError(
                f"Invalid Go-style duration '{value}'. Use values such as 10m, 30m, or 1h."
            )
        position = match.end()
        total_ns += int(match.group("value")) * _GO_DURATION_UNIT_NS[match.group("unit")]
    if position != len(raw) or total_ns <= 0:
        raise ValueError(
            f"Invalid Go-style duration '{value}'. Use values such as 10m, 30m, or 1h."
        )
    seconds = total_ns // 1_000_000_000
    return max(1, seconds) if total_ns else 0
