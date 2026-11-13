"""Best-effort key-free tunnel decisions captured by the controller journal."""

from __future__ import annotations

import itertools
import json
import math
import os
import re
import sys
import time

_SEQUENCE = itertools.count(1)
_TOKENS = {"reason", "connection", "target", "selected", "health", "error_type", "phase", "result"}
_NUMBERS = {
    "age",
    "gap",
    "wait_seconds",
    "confirmations",
    "returncode",
    "started_monotonic",
    "elapsed_seconds",
    "decision_monotonic",
    "count",
}
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def diagnostic(event: str, **fields: object) -> None:
    """Never log native output, configuration, exception text or arbitrary fields.

    Use the real clock, independent of injectable decision clocks. Failed writes
    may leave sequence gaps but must never prevent an authorized recovery.
    """
    try:
        if not _TOKEN.fullmatch(event):
            return
        value: dict[str, object] = {
            "event": event,
            "level": "INFO",
            "monotonic": time.monotonic(),
            "pid": os.getpid(),
            "sequence": next(_SEQUENCE),
        }
        for name, field in fields.items():
            if (
                name in _TOKENS
                and isinstance(field, str)
                and _TOKEN.fullmatch(field)
                or name in _NUMBERS
                and isinstance(field, (int, float))
                and not isinstance(field, bool)
                and math.isfinite(field)
            ):
                value[name] = field
            elif name == "reasons" and isinstance(field, list):
                value[name] = [
                    item for item in field if isinstance(item, str) and _TOKEN.fullmatch(item)
                ]
        print(
            "VPNGW_TUNNEL_DIAGNOSTIC " + json.dumps(value, separators=(",", ":")),
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        pass
