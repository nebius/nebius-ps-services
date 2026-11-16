"""Opaque retirement of route metadata under the confirmed repair owner.

Readable creation receipts remain authoritative; malformed bytes never grant
ownership of a cloud route. Callers settle returned effects before retirement.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import repair_guest, repair_state
from .ordinary_route_ownership import RouteJournal
from .repair_activation import archive_file
from .repair_retirement import accepted


def paths(journal: RouteJournal) -> dict[str, Path]:
    return {
        "active": journal.path,
        "candidate": journal.root / ".recovery.json.vpngw-recovery/candidate",
    }


def observe(journal: RouteJournal) -> dict:
    fingerprints, states, effects = {}, [], []
    for name, path in paths(journal).items():
        raw = repair_state.read(path, allow_hardlinks=True)
        fingerprints[name] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        if raw is None:
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        # Export only operation metadata from an otherwise invalid envelope.
        for record in accepted(value):
            effects.append(
                {
                    key: record[key]
                    for key in ("status", "cloud_operation_id", "request_type", "label")
                    if key in record and isinstance(record[key], str)
                }
            )
        try:
            journal.validate(value)
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        states.append(value)
    return {"fingerprints": fingerprints, "states": states, "effects": effects}


def retire(journal: RouteJournal, archive: Path, expected: dict, settled: dict) -> None:
    """Replace opaque journals, retaining lock inodes and settled route receipts."""
    journal.validate(settled)
    if settled["transaction"] is not None:
        raise RuntimeError("Repair route reset requires settled effects")
    identity = repair_state.digest({"expected": expected, "settled": settled})
    repair_state.directory(journal.root)
    with repair_guest.lock(journal.root / "operation.lock"):
        receipt = repair_state.read(archive / "reset.json")
        if receipt is not None:
            if json.loads(receipt)["identity"] != identity:
                raise RuntimeError("Repair route reset changed its settled input")
        else:
            if observe(journal)["fingerprints"] != expected:
                raise RuntimeError("Route metadata changed before repair retirement")
            repair_state.publish(archive / "reset.json", {"identity": identity})
        for name, path in paths(journal).items():
            # A receipt is persisted before retirement; a retry may see either
            # the old bytes, an absent path, or the exact replacement ledger.
            raw = repair_state.read(path, allow_hardlinks=True)
            if raw is None:
                continue
            if name == "active" and json_equal(raw, settled):
                continue
            if hashlib.sha256(raw).hexdigest() != expected[name]:
                raise RuntimeError("Route metadata changed during repair retirement")
            archive_file(path, archive / (name + ".json"))
        repair_state.publish(journal.path, settled)


def json_equal(raw: bytes, expected: dict) -> bool:
    try:
        return json.loads(raw) == expected
    except ValueError:
        return False
