"""Durable, serial replacement workflow; provider and guest proofs are explicit ports.

The journal contains resource identities, hashes and operation receipts only. It
is portable between authorized operators and never contains configuration PSKs,
operator private keys, host private keys, or IAM credentials.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .operator_guest import finish_publication, publish, read_file
from .operator_keys import protected_directory, safe_name

SCHEMA = "nebius-vpngw.replacement.v1"
STEPS = (
    "prepare",
    "verify-prepared",
    "stop-old",
    "verify-stopped",
    "transfer",
    "commit-binding",
    "activate",
    "verify-serving",
    "retire",
    "verify-retired",
)
FORBIDDEN = frozenset(
    {
        "psk",
        "private_key",
        "token",
        "password",
        "passphrase",
        "cloud_init_user_data",
        "config_yaml",
        "content",
        "secret",
    }
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def metadata_only(value: Any) -> None:
    if isinstance(value, dict):
        if any(not isinstance(key, str) or key.lower() in FORBIDDEN for key in value):
            raise ValueError("Replacement state accepts only metadata, identities and digests")
        for item in value.values():
            metadata_only(item)
    elif isinstance(value, list | tuple):
        for item in value:
            metadata_only(item)
    elif value is not None and type(value) not in (str, int, bool, float):
        raise ValueError("Replacement state contains a non-JSON value")


@dataclass(frozen=True)
class Member:
    logical_name: str
    compute_id: str
    physical_name: str
    revision: int
    disk_id: str
    owned_disk: bool
    compute_sha256: str
    authorization_sha256: str
    configured_policy_sha256: str
    security_groups: tuple[dict[str, Any], ...]
    public_allocations: tuple[str, ...]
    primary_allocations: tuple[str, ...]
    candidate_name: str
    candidate_disk_name: str


@dataclass(frozen=True)
class Plan:
    project_id: str
    gateway_name: str
    operation_id: str
    image_id: str
    desired: dict[str, Any]
    configuration_sha256: str
    artifact_sha256: str
    members: tuple[Member, ...]
    routes: tuple[dict[str, Any], ...]
    vm_ha: bool = False
    observed_owner: str | None = None
    ha: dict[str, Any] | None = None

    def mapping(self) -> dict[str, Any]:
        result = json.loads(json.dumps(asdict(self)))
        metadata_only(result)
        if (
            not re.fullmatch(r"[a-f0-9]{32,64}", self.operation_id)
            or not self.members
            or len({item.logical_name for item in self.members}) != len(self.members)
            or any(
                not item.compute_id or not item.disk_id or item.revision <= 0
                for item in self.members
            )
        ):
            raise ValueError("Replacement plan has incomplete or duplicate resource authority")
        if self.vm_ha and (
            len(self.members) != 2
            or self.members[0].logical_name == self.observed_owner
            or self.members[1].logical_name != self.observed_owner
        ):
            raise ValueError("HA replacement must start with the observed nonowner")
        return result


class Journal:
    def __init__(self, project: str, gateway: str, *, root: Path | None = None) -> None:
        safe_name(project)
        safe_name(gateway)
        self.project, self.gateway = project, gateway
        self.root = (
            root
            or Path.home() / ".local" / "state" / "nebius-vpngw" / "replacement" / project / gateway
        ).absolute()
        # One canonical checkpoint is also the portable recovery bundle. Two
        # independently published copies could expose a stale recovery receipt.
        self.path = self.root / "recovery.json"
        self.bundle = self.path
        self.state: dict[str, Any] = {}
        self._before: dict[str, Any] | None = None
        self._lock: int | None = None

    def __enter__(self) -> Journal:
        protected_directory(self.root)
        self._lock = os.open(
            self.root / "operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        # Validate ownership/type/mode through the same bounded no-follow reader.
        try:
            lock = read_file(self.root / "operation.lock")
            if lock is None or lock["uid"] != os.getuid() or lock["mode"] != 0o600:
                raise ValueError("Replacement lock must be a private operator-owned file")
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._recover_publication()
            self._before = read_file(self.path)
            if self._before is not None and (
                self._before["uid"] != os.getuid() or self._before["mode"] != 0o600
            ):
                raise ValueError("Replacement state must be a private operator-owned file")
            self.state = (
                json.loads(self._before["content"])
                if self._before
                else {
                    "schema": SCHEMA,
                    "project_id": self.project,
                    "gateway_name": self.gateway,
                    "revision": 0,
                    "bindings": {},
                    "transaction": None,
                }
            )
            self.validate(self.state)
            return self
        except Exception:
            os.close(self._lock)
            self._lock = None
            raise

    def __exit__(self, *_args: Any) -> None:
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    def _recover_publication(self) -> None:
        recovery = self.path.parent / ("." + self.path.name + ".vpngw-recovery")
        if not recovery.exists():
            return
        active, candidate = read_file(self.path), read_file(recovery / "candidate")
        if candidate is None:
            if active is None:
                if recovery.is_symlink() or set(recovery.iterdir()):
                    raise ValueError("Replacement publication recovery is invalid")
                protected_directory(recovery)
                recovery.rmdir()
                return
            finish_publication(self.path, active, active)
            return
        records = []
        for item in (active, candidate):
            if item is None:
                continue
            if item["uid"] != os.getuid() or item["mode"] != 0o600:
                raise ValueError("Replacement recovery contains an unsafe checkpoint")
            state = json.loads(item["content"])
            self.validate(state)
            records.append((state["revision"], item))
        records.sort(key=lambda pair: pair[0])
        if len(records) == 2 and records[1][0] - records[0][0] not in {0, 1}:
            raise ValueError("Replacement publication revisions are not consecutive")
        if len(records) == 2 and records[1][0] == records[0][0] and records[1][1] != records[0][1]:
            raise ValueError("Replacement publication has conflicting checkpoints")
        expected = records[0][1] if len(records) == 2 else None
        finish_publication(self.path, expected, records[-1][1])

    def validate(self, state: dict) -> None:
        metadata_only(state)
        if (
            not isinstance(state, dict)
            or state.get("schema") != SCHEMA
            or state.get("project_id") != self.project
            or state.get("gateway_name") != self.gateway
        ):
            raise ValueError("Replacement state belongs to a different deployment")
        if (
            type(state.get("revision")) is not int
            or state["revision"] < 0
            or not isinstance(state.get("bindings"), dict)
        ):
            raise ValueError("Replacement registry is invalid")
        transaction = state.get("transaction")
        if transaction is not None:
            try:
                if not isinstance(transaction, dict) or not transaction:
                    raise ValueError("Missing transaction")
                mapping = transaction["plan"]
                restored = Plan(
                    **{**mapping, "members": tuple(Member(**item) for item in mapping["members"])}
                )
                if restored.mapping() != mapping:
                    raise ValueError("Noncanonical plan")
                if not isinstance(transaction["results"], dict) or not isinstance(
                    transaction["operations"], dict
                ):
                    raise ValueError("Invalid receipts")
            except (KeyError, TypeError, AttributeError, ValueError) as error:
                raise ValueError("Replacement checkpoint structure is invalid") from error
        if transaction and (
            transaction.get("phase") not in {"pending", "complete"}
            or digest(transaction["plan"]) != transaction["approval"]
            or type(transaction["step"]) is not int
            or type(transaction["member"]) is not int
            or transaction["step"] not in range(len(STEPS) + 1)
            or transaction["member"] not in range(len(transaction["plan"]["members"]) + 1)
        ):
            raise ValueError("Replacement checkpoint is invalid")
        if transaction and (
            transaction["plan"]["project_id"] != self.project
            or transaction["plan"]["gateway_name"] != self.gateway
            or transaction.get("pending") not in {None, "promote", "finish", *STEPS}
            or (
                transaction["phase"] == "complete"
                and transaction["member"] != len(transaction["plan"]["members"])
            )
        ):
            raise ValueError("Replacement transaction authority is invalid")

    def save(self) -> None:
        if self._lock is None:
            raise RuntimeError("Replacement journal requires its operation lock")
        self.state["revision"] += 1
        self.validate(self.state)
        desired = {
            "content": json.dumps(self.state, sort_keys=True, separators=(",", ":")) + "\n",
            "uid": os.getuid(),
            "gid": os.getgid(),
            "mode": 0o600,
        }
        publish(self.path, self._before, desired)
        self._before = desired

    def start(self, plan: Plan, approval: str) -> None:
        mapping = plan.mapping()
        if digest(mapping) != approval:
            raise ValueError("Replacement approval does not match the exact inspected plan")
        transaction = self.state["transaction"]
        if transaction and transaction["phase"] != "complete":
            if transaction["approval"] != approval:
                raise RuntimeError("A different replacement is pending; resume its recovery bundle")
            return
        self.state["transaction"] = {
            "plan": mapping,
            "approval": approval,
            "member": 0,
            "step": 0,
            "phase": "pending",
            "pending": None,
            "results": {},
            "operations": {},
        }
        self.save()

    def import_bundle(self, path: Path) -> None:
        record = read_file(path.expanduser().absolute())
        if record is None or record["uid"] != os.getuid() or record["mode"] & 0o077:
            raise ValueError("Recovery bundle must be a private operator-owned file")
        candidate = json.loads(record["content"])
        self.validate(candidate)
        current = self.state.get("transaction")
        if current and current["phase"] != "complete" and current != candidate["transaction"]:
            raise RuntimeError("Local replacement differs from this recovery bundle")
        if self.state["revision"] > candidate["revision"]:
            raise RuntimeError("Recovery bundle is older than the local checkpoint")
        self.state = candidate
        self.save()


class Ports(Protocol):
    def settle(self, journal: Journal) -> None: ...
    def verify_authority(self, journal: Journal, *, step: str) -> None: ...
    def execute(self, journal: Journal, *, step: str, effect_id: str) -> dict[str, Any]: ...
    def verify(self, journal: Journal, *, step: str, result: dict[str, Any]) -> None: ...
    def promote(self, journal: Journal, *, effect_id: str) -> None: ...
    def finish(self, journal: Journal) -> None: ...


def run(
    journal: Journal, ports: Ports, *, checkpoint: Callable[[str], None] = lambda _step: None
) -> None:
    """A recorded intent is replayed only through an exact, idempotent port.

    Every effect requires fresh authority and its independent postcondition.
    An unresolved accepted cloud operation blocks all subsequent effects.
    """
    transaction = journal.state.get("transaction")
    if not transaction:
        raise RuntimeError("Replacement has no approved transaction")
    if transaction["phase"] == "complete":
        return
    members = transaction["plan"]["members"]
    while transaction["member"] < len(members):
        ports.settle(journal)
        index = transaction["member"]
        if transaction["step"] == len(STEPS):
            if transaction["plan"]["vm_ha"] and index == 0:
                # The former owner cannot be replaced until canonical promotion
                # independently proves the new member owns the shared address.
                transaction["pending"] = "promote"
                journal.save()
                ports.promote(journal, effect_id=digest([transaction["approval"], "promote"]))
            transaction["member"] += 1
            transaction["step"] = 0
            transaction["pending"] = None
            journal.save()
            continue
        step = STEPS[transaction["step"]]
        ports.verify_authority(journal, step=step)
        if transaction["pending"] not in (None, step):
            raise RuntimeError("Replacement has a different pending action")
        transaction["pending"] = step
        journal.save()
        effect_id = digest([transaction["approval"], index, step])
        result = ports.execute(journal, step=step, effect_id=effect_id)
        metadata_only(result)
        ports.verify(journal, step=step, result=result)
        transaction["results"][f"{index}:{step}"] = result
        transaction["pending"] = None
        transaction["step"] += 1
        journal.save()
        checkpoint(step)
    ports.settle(journal)
    transaction["pending"] = "finish"
    journal.save()
    ports.finish(journal)
    transaction["pending"] = None
    transaction["phase"] = "complete"
    journal.save()
