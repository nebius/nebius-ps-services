"""Small stdlib-only repair journal, outside the product state being rebuilt.

This record describes a confirmed reconstruction. It is not evidence that an
old cloud operation or guest process has stopped; effect owners supply that proof.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any

SCHEMA = "nebius-vpngw.repair.v1"
LIMIT = 4 * 1024 * 1024
PHASES = (
    "confirmed",
    "isolated",
    "staged",
    "reset",
    "installed",
    "enrolled",
    "prepared",
    "originating",
    "verified",
    "complete",
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def directory(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink():
            raise RuntimeError("Repair storage contains a symlink")
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    # pathlib's parents=True applies mode only to the leaf. Every newly
    # created repair storage component must have the same private permissions.
    for current in reversed(missing):
        current.mkdir(mode=0o700, exist_ok=True)
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError("Repair storage must be private and owned by the operator")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RuntimeError("Repair storage must be private and owned by the operator")


def read(path: Path, *, allow_hardlinks: bool = False) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            and not allow_hardlinks
            or info.st_size > LIMIT
        ):
            raise RuntimeError("Repair record is not a protected regular file")
        return stream.read(LIMIT + 1)


def sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path: Path, value: dict) -> None:
    directory(path.parent)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > LIMIT:
        raise RuntimeError("Repair record exceeds its limit")
    if path.is_symlink():
        raise RuntimeError("Repair record must not be a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".repair-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_intent(intent: dict) -> None:
    if (
        not isinstance(intent, dict)
        or set(intent) != {"project", "gateway", "configuration", "artifact", "targets", "ha"}
        or any(
            not isinstance(intent.get(key), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}", intent[key])
            for key in ("project", "gateway")
        )
        or any(
            not re.fullmatch(r"[a-f0-9]{64}", intent.get(key, ""))
            for key in ("configuration", "artifact")
        )
        or type(intent.get("ha")) is not bool
        or not isinstance(intent.get("targets"), list)
        or not intent["targets"]
    ):
        raise ValueError("Repair requires a complete immutable target intent")
    identities = []
    for target in intent["targets"]:
        if (
            not isinstance(target, dict)
            or set(target) != {"name", "compute", "disk", "transport", "binding"}
            or any(not isinstance(value, str) or not value for value in target.values())
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}", target["name"])
            or not re.fullmatch(r"[a-f0-9]{64}", target["binding"])
        ):
            raise ValueError("Repair target identity is incomplete")
        identities.append(target["compute"])
    if len(set(identities)) != len(identities) or (intent["ha"] and len(identities) != 2):
        raise ValueError("Repair target cardinality is invalid")


class Journal:
    """Caller holds canonical gateway exclusion across reads and writes."""

    def __init__(self, root: Path):
        self.root = root
        self.path = root / "operation.json"

    def load(self) -> dict | None:
        raw = read(self.path)
        if raw is None:
            return None
        return self.decode(raw)

    @staticmethod
    def decode(raw: bytes) -> dict:
        """Validate active and archived records through the same native codec."""
        try:
            value = json.loads(raw)
            validate_intent(value["intent"])
            valid = (
                set(value) == {"schema", "id", "intent", "phase", "effects", "previous"}
                and value["schema"] == SCHEMA
                and re.fullmatch(r"[a-f0-9]{32}", value["id"])
                and value["phase"] in PHASES
                and isinstance(value["effects"], dict)
                and (value["previous"] is None or re.fullmatch(r"[a-f0-9]{64}", value["previous"]))
            )
            for effect in value["effects"].values():
                valid = valid and (
                    isinstance(effect, dict)
                    and set(effect) == {"request", "state", "receipt"}
                    and re.fullmatch(r"[a-f0-9]{64}", effect["request"])
                    and effect["state"] in {"started", "accepted", "settled", "complete"}
                    and isinstance(effect["receipt"], dict)
                )
            if not valid:
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise RuntimeError(
                "Repair progress is unreadable; retained guest/cloud receipts require reconciliation"
            ) from error
        return value

    def begin(self, intent: dict, *, predecessor_settled: bool = False) -> dict:
        validate_intent(intent)
        previous = self.load()
        if previous is not None and previous["phase"] != "complete":
            if previous["intent"] == intent:
                return previous
            if not predecessor_settled:
                raise RuntimeError(
                    "Previous repair effects must settle before a confirmed successor"
                )
            if previous["intent"]["targets"] != intent["targets"]:
                raise RuntimeError("Repair successor cannot silently change retained VM identities")
            if any(
                effect["state"] not in {"complete", "settled"}
                for effect in previous["effects"].values()
            ):
                raise RuntimeError("Previous repair still has unsettled effects")
        prior = None
        if previous is not None:
            prior = digest(previous)
            archive = self.root / "history" / (previous["id"] + ".json")
            if read(archive) is None:
                publish(archive, previous)
            elif json.loads(read(archive) or b"null") != previous:
                raise RuntimeError("Repair history changed")
        value = dict(
            schema=SCHEMA,
            id=uuid.uuid4().hex,
            intent=intent,
            phase="confirmed",
            effects={},
            previous=prior,
        )
        publish(self.path, value)
        return value

    def retire_destroyed(self, previous: dict, receipt: dict) -> None:
        """Revoke exact repair authority after its owner proves destruction.

        The original phase and unfinished effects are preserved. Publication is
        restartable at every boundary; a different active repair is never cleared.
        Caller holds the gateway lock and supplies fresh terminal destruction proof.
        """
        self.decode(json.dumps(previous).encode())
        expected = {
            "schema",
            "repair",
            "repair_sha256",
            "project",
            "gateway",
            "destroy_plan",
            "targets",
            "cloud_sha256",
            "absence",
            "quarantine_absent",
        }
        intent = previous["intent"]
        if (
            set(receipt) != expected
            or receipt["schema"] != "nebius-vpngw.repair-destruction.v1"
            or receipt["repair"] != previous["id"]
            or receipt["repair_sha256"] != digest(previous)
            or receipt["project"] != intent["project"]
            or receipt["gateway"] != intent["gateway"]
            or receipt["targets"] != sorted(t["compute"] for t in intent["targets"])
            or any(
                not re.fullmatch(r"[a-f0-9]{64}", receipt[key])
                for key in (
                    "destroy_plan",
                    "cloud_sha256",
                )
            )
            or receipt["absence"] != [digest(receipt["targets"])] * 2
            or receipt["quarantine_absent"] is not True
        ):
            raise RuntimeError("Repair retirement requires exact destruction proof")
        current = self.load()
        archive = self.root / "history" / (previous["id"] + "-destroyed.json")
        proof = self.root / "retirements" / (previous["id"] + ".json")
        if current != previous and current is not None:
            raise RuntimeError("Destroy cannot retire a different active repair")
        archived = read(archive)
        if archived is None:
            if current is None:
                raise RuntimeError("Destroyed repair authority disappeared before archival")
            publish(archive, previous)
        elif json.loads(archived) != previous:
            raise RuntimeError("Destroyed repair archive changed")
        recorded = read(proof)
        if recorded is None:
            publish(proof, receipt)
        elif json.loads(recorded) != receipt:
            raise RuntimeError("Destroyed repair retirement proof changed")
        if self.load() is not None:
            if self.load() != previous:
                raise RuntimeError("Active repair changed during destruction retirement")
            self.path.unlink()
            sync(self.root)

    def effect(
        self, name: str, request: Any, *, state: str = "started", receipt: dict | None = None
    ) -> dict:
        value = self.load()
        if value is None or value["phase"] == "complete":
            raise RuntimeError("Repair effect requires a confirmed active operation")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,180}", name) or state not in {
            "started",
            "accepted",
            "settled",
            "complete",
        }:
            raise ValueError("Invalid repair effect")
        record: dict = {"request": digest(request), "state": state, "receipt": receipt or {}}
        previous = value["effects"].get(name)
        order = {"started": 0, "accepted": 1, "settled": 2, "complete": 2}
        if previous:
            if previous["request"] != record["request"]:
                raise RuntimeError("Repair effect request changed")
            if order[state] < order[previous["state"]]:
                return previous
            if previous["state"] == "accepted" and any(
                key not in record["receipt"] or record["receipt"][key] != value
                for key, value in previous["receipt"].items()
            ):
                raise RuntimeError("Accepted repair effect receipt changed")
            if previous["state"] in {"settled", "complete"}:
                if record != previous:
                    raise RuntimeError("Completed repair effect changed")
                return previous
        value["effects"][name] = record
        publish(self.path, value)
        return record

    def settle_pending(self, receipt: dict) -> dict:
        """Record proven quiescence, never successful completion of old effects."""
        value = self.load()
        if value is None or not receipt:
            raise RuntimeError("Repair settlement requires an exact owner receipt")
        for effect in value["effects"].values():
            if effect["state"] not in {"complete", "settled"}:
                effect["state"] = "settled"
                effect["receipt"] = {**effect["receipt"], "settlement": receipt}
        publish(self.path, value)
        return value

    def advance(self, phase: str) -> dict:
        value = self.load()
        if value is None or phase not in PHASES:
            raise ValueError("Invalid repair phase")
        current, target = PHASES.index(value["phase"]), PHASES.index(phase)
        if target < current:
            return value
        if target > current + 1 or any(
            effect["state"] != "complete" for effect in value["effects"].values()
        ):
            raise RuntimeError("Repair phase lacks settled preceding effects")
        value["phase"] = phase
        publish(self.path, value)
        return value
