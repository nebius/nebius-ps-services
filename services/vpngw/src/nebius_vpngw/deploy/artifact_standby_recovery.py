"""Protected bootstrap receipt under the existing gateway command journal.

This locates an admitted lane after its capability changes. Fresh lifecycle,
SSH, package and canonical-apply checks remain responsible for every effect.
"""

import json
import os
from pathlib import Path

from .operator_guest import publish, read_file
from .operator_keys import protected_directory

PHASES = ("owner", "owner-ready", "standby-ready", "canonical", "complete")


def read(root: Path) -> dict | None:
    item = read_file(root / "artifact-standby-recovery.json")
    if item is None:
        return None
    value = json.loads(item["content"])
    if (
        item["uid"] != os.getuid()
        or item["mode"] != 0o600
        or set(value) != {"schema", "binding", "report", "phase"}
        or value["schema"] != "nebius-vpngw.artifact-standby-recovery.v1"
        or value["phase"] not in PHASES
    ):
        raise RuntimeError("Artifact standby recovery receipt is invalid")
    return value


def save(root: Path, value: dict, *, expected: dict | None) -> None:
    protected_directory(root)
    path = root / "artifact-standby-recovery.json"
    old = read_file(path)
    if (json.loads(old["content"]) if old else None) != expected:
        raise RuntimeError("Artifact standby recovery receipt changed")
    publish(
        path,
        old,
        dict(
            content=json.dumps(value, sort_keys=True), uid=os.getuid(), gid=os.getgid(), mode=0o600
        ),
    )


def advance(root: Path, value: dict, phase: str) -> dict:
    if PHASES.index(phase) < PHASES.index(value["phase"]):
        raise RuntimeError("Artifact standby recovery cannot regress")
    updated = dict(value, phase=phase)
    save(root, updated, expected=value)
    return updated
