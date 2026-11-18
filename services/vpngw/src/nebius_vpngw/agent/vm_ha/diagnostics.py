"""Best-effort key-free tunnel decisions captured by the controller journal."""

from __future__ import annotations

import contextlib
import itertools
import json
import math
import os
import re
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

_SEQUENCE = itertools.count(1)
_TOKENS = {
    "reason",
    "connection",
    "target",
    "selected",
    "health",
    "error_type",
    "phase",
    "result",
    "scope",
}
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

# Every controller pass re-evaluates these decisions. Emit one only when it
# differs from the last record written for the same subject, so a steady state
# leaves no journal trail. Elapsed-time measurements never make a decision new.
_ON_CHANGE = {
    "authority-accepted": "authority",
    "authority-refused": "authority",
    "step-admitted": "step-gate",
    "step-skipped": "step-gate",
    "path-health": "path-health",
    "static-recovery-reset": "static-recovery",
    "static-recovery-skip": "static-recovery",
    "static-recovery-wait": "static-recovery",
}
_SUBJECTS = {
    "authority": ("scope",),
    "step-gate": (),
    "path-health": ("connection", "target", "scope"),
    "static-recovery": ("connection",),
}
# Per-CHILD rejections only explain the result of the same path observation.
# Hold them until that result; both are written only when the pair changed.
_DETAILS = {"path-health-candidate-rejected": "path-health"}
_MEASUREMENTS = frozenset(
    {"age", "gap", "wait_seconds", "started_monotonic", "elapsed_seconds", "decision_monotonic"}
)


@dataclass
class _Scope:
    started: str
    at: float
    emitted: bool = False


_LOCK = threading.RLock()
_last: dict[tuple[object, ...], tuple[object, ...]] = {}
_held: dict[tuple[object, ...], list[tuple[str, dict[str, object]]]] = {}
_scopes: list[_Scope] = []


def _sanitize(fields: dict[str, object]) -> dict[str, object]:
    value: dict[str, object] = {}
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
    return value


def _decision(event: str, fields: dict[str, object]) -> tuple[object, ...]:
    return (
        event,
        *sorted(
            (name, json.dumps(field)) for name, field in fields.items() if name not in _MEASUREMENTS
        ),
    )


def _write(event: str, fields: dict[str, object]) -> bool:
    try:
        value: dict[str, object] = {
            "event": event,
            "level": "INFO",
            "monotonic": time.monotonic(),
            "pid": os.getpid(),
            "sequence": next(_SEQUENCE),
            **fields,
        }
        print(
            "VPNGW_TUNNEL_DIAGNOSTIC " + json.dumps(value, separators=(",", ":")),
            file=sys.stderr,
            flush=True,
        )
        return True
    except Exception:
        return False


def _open_scopes() -> bool:
    """Write the start of every enclosing scope before its first record."""
    for frame in _scopes:
        if not frame.emitted:
            if not _write(frame.started, {"started_monotonic": frame.at}):
                return False
            frame.emitted = True
    return True


def diagnostic(event: str, **fields: object) -> None:
    """Never log native output, configuration, exception text or arbitrary fields.

    Use the real clock, independent of injectable decision clocks. Failed writes
    may leave sequence gaps but must never prevent an authorized recovery. A
    failed write is not remembered, so the same changed decision is retried.
    """
    try:
        if not _TOKEN.fullmatch(event):
            return
        value = _sanitize(fields)
        with _LOCK:
            parent = _DETAILS.get(event)
            if parent is not None:
                held = (parent, *(value.get(name) for name in _SUBJECTS[parent]))
                _held.setdefault(held, []).append((event, value))
                return
            group = _ON_CHANGE.get(event)
            subject: tuple[object, ...] | None = None
            details: list[tuple[str, dict[str, object]]] = []
            decision: tuple[object, ...] = ()
            if group is not None:
                subject = (group, *(value.get(name) for name in _SUBJECTS[group]))
                details = _held.pop(subject, [])
                decision = (
                    *(_decision(name, fields) for name, fields in details),
                    _decision(event, value),
                )
                if _last.get(subject) == decision:
                    return
            if not _open_scopes():
                return
            for name, fields in (*details, (event, value)):
                if not _write(name, fields):
                    return
            if subject is not None:
                _last[subject] = decision
    except Exception:
        pass


@contextlib.contextmanager
def scope(started: str, finished: str, *, slow_seconds: float | None = None) -> Iterator[None]:
    """Bracket work only when it writes a record or runs at least ``slow_seconds``.

    The start record is deferred until the first record inside the scope, so a
    quiet pass writes nothing while a pass that changes a decision or fails is
    still bracketed. The finish record carries the original start time.
    """
    frame = _Scope(started, time.monotonic())
    with _LOCK:
        _scopes.append(frame)
    try:
        yield
    finally:
        with _LOCK:
            for index in range(len(_scopes) - 1, -1, -1):
                if _scopes[index] is frame:
                    del _scopes[index]
                    break
        elapsed = time.monotonic() - frame.at
        if frame.emitted or slow_seconds is not None and elapsed >= slow_seconds:
            diagnostic(finished, started_monotonic=frame.at, elapsed_seconds=elapsed)
