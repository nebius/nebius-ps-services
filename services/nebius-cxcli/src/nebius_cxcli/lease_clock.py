"""Suspend-aware elapsed time for contained process shutdown."""

from __future__ import annotations

import sys
import time


def elapsed() -> float:
    name = {"linux": "CLOCK_BOOTTIME", "darwin": "CLOCK_MONOTONIC_RAW"}.get(sys.platform)
    clock = getattr(time, name, None) if name else None
    if clock is None:
        raise RuntimeError("Process supervision requires a supported suspend-aware elapsed clock")
    return time.clock_gettime_ns(clock) / 1_000_000_000
