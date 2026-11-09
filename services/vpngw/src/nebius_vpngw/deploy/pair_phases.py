"""Optional pair barriers around existing regional deployment owners.

These callbacks may delay an owner. They never grant forwarding, route, cloud,
or approval authority and never replace a child's own durable journal.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

CURRENT: ContextVar[Any] = ContextVar("vpngw_pair_phases", default=None)


def reached(phase: str, journal: Any, *, evidence: dict | None = None) -> None:
    selected = CURRENT.get()
    if selected is None:
        return
    from ..apply_approval import digest

    snapshot = journal.snapshot()
    transaction = snapshot["transaction"] if snapshot else None
    if transaction is None:
        raise RuntimeError("Pair phase has no regional command authority")
    selected.reached(
        phase,
        {
            "command": digest(snapshot),
            "execution": transaction["execution_id"],
            "approval": transaction["approval"],
            "artifact": transaction["plan"]["artifact_sha256"],
            "evidence": digest(evidence) if evidence is not None else None,
        },
    )


def report(value: Any) -> None:
    selected = CURRENT.get()
    if selected is not None:
        selected.report(value.mapping())
