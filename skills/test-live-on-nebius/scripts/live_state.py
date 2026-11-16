"""Private, single-writer live-test journal. No cloud or auth effects on import."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
import tempfile
import time
import uuid
from pathlib import Path


class StateError(RuntimeError):
    """Stable error code without provider messages or secret values."""


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def safe_path(path, *, directory=False):
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise StateError("SYMLINK_PATH")
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise StateError("UNSAFE_PERMISSIONS")
    if directory and not stat.S_ISDIR(info.st_mode):
        raise StateError("NOT_DIRECTORY")
    if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise StateError("NOT_PRIVATE_REGULAR_FILE")
    return path


def atomic_json(path, value):
    path = Path(path)
    safe_path(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        safe_path(path)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Journal:
    def __init__(self, directory):
        self.directory = safe_path(directory, directory=True)
        self.path = self.directory / "state.json"
        safe_path(self.path)
        self.data = json.loads(self.path.read_text())
        if self.data.get("schema") != "nebius-live-run/v1":
            raise StateError("STATE_SCHEMA")
        self.locked = False

    @classmethod
    def create(cls, root, plan):
        root = Path(root).expanduser().absolute()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        safe_path(root, directory=True)
        run_id = str(uuid.uuid4())
        directory = root / run_id
        directory.mkdir(mode=0o700)
        atomic_json(
            directory / "state.json",
            {
                "schema": "nebius-live-run/v1",
                "run_id": run_id,
                "plan": plan,
                "plan_sha256": digest(plan),
                "phase": "planned",
                "created_at": time.time(),
                "operations": {},
                "resources": {},
                "evidence": {},
                "quota_requests": {},
                "cleanup_complete": False,
            },
        )
        return cls(directory)

    @contextlib.contextmanager
    def lock(self):
        fd = os.open(
            self.directory / "run.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            safe_path(self.directory / "run.lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StateError("RUN_BUSY") from None
            self.locked = True
            self.data = json.loads(self.path.read_text())
            if digest(self.data["plan"]) != self.data["plan_sha256"]:
                raise StateError("PLAN_CHANGED")
            yield self
        finally:
            self.locked = False
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def save(self):
        if not self.locked:
            raise StateError("LOCK_REQUIRED")
        if digest(self.data["plan"]) != self.data["plan_sha256"]:
            raise StateError("PLAN_CHANGED")
        atomic_json(self.path, self.data)

    def phase(self, phase):
        self.data["phase"] = phase
        self.save()

    def intent(self, key, request_hash, *, deleting=False, descriptor=None):
        if (
            self.data["phase"] in {"cleanup", "cleanup_pending", "complete"}
            and not deleting
        ):
            raise StateError("PROVISIONING_CLOSED")
        old = self.data["operations"].get(key)
        if old:
            if old["request_sha256"] != request_hash:
                raise StateError("INTENT_CHANGED")
            raise StateError("RECONCILE_BEFORE_RESUBMIT")
        record = {
            "request_sha256": request_hash,
            "idempotency_key": str(uuid.uuid4()),
            "status": "intent",
            "operation_id": "",
            "resource_id": "",
        }
        if descriptor is not None:
            if not isinstance(descriptor, dict) or record.keys() & descriptor.keys():
                raise StateError("INTENT_DESCRIPTOR_INVALID")
            record.update(descriptor)
        self.data["operations"][key] = record
        self.save()
        return record["idempotency_key"]

    def accepted(self, key, operation_id, resource_id):
        if not operation_id:
            raise StateError("OPERATION_ID_MISSING")
        self.data["operations"][key].update(
            status="accepted", operation_id=operation_id, resource_id=resource_id
        )
        self.save()

    def terminal(self, key, success):
        self.data["operations"][key]["status"] = "succeeded" if success else "failed"
        self.save()

    def own(self, key, *, resource_id, kind, parent, owner, dependencies=()):
        parent_keys = [
            k
            for k, r in self.data["resources"].items()
            if r["id"] == parent and not r["absent"]
        ]
        parent_owned = len(parent_keys) == 1 and parent_keys[0] in dependencies
        if (
            parent != self.data["plan"]["project"] and not parent_owned
        ) or owner not in {"sdk", "cxcli"}:
            raise StateError("RESOURCE_SCOPE")
        if not resource_id or any(
            d not in self.data["resources"] for d in dependencies
        ):
            raise StateError("RESOURCE_DEPENDENCIES")
        record = {
            "id": resource_id,
            "kind": kind,
            "parent": parent,
            "owner": owner,
            "dependencies": list(dependencies),
            "absent": False,
        }
        old = self.data["resources"].get(key)
        if old and {k: v for k, v in old.items() if k != "absent"} != {
            k: v for k, v in record.items() if k != "absent"
        }:
            raise StateError("RESOURCE_IDENTITY_CHANGED")
        self.data["resources"][key] = old or record
        self.save()

    def deletion_target(
        self, key, *, observed_id, observed_parent, observed_run, owner
    ):
        resource = self.data["resources"][key]
        if (
            observed_id != resource["id"]
            or observed_parent != resource["parent"]
            or observed_run != self.data["run_id"]
            or owner != resource["owner"]
        ):
            raise StateError("FOREIGN_RESOURCE")
        if any(
            key in r["dependencies"] and not r["absent"]
            for r in self.data["resources"].values()
        ):
            raise StateError("LIVE_DEPENDENTS")
        return resource["id"]

    def absent(self, key):
        self.data["resources"][key]["absent"] = True
        self.save()

    def evidence(self, key, value):
        # Callers pass only bounded sanitized facts, never raw SDK/SSH output.
        if len(json.dumps(value)) > 16384:
            raise StateError("EVIDENCE_TOO_LARGE")
        self.data["evidence"][key] = value
        self.save()

    def quota_intent(self, scope, absolute_limits):
        key = digest({"scope": scope, "limits": absolute_limits})
        if key in self.data["quota_requests"]:
            raise StateError("RECONCILE_QUOTA_REQUEST")
        self.data["quota_requests"][key] = {
            "scope": scope,
            "limits": absolute_limits,
            "status": "submission_pending",
            "receipt": None,
        }
        self.save()
        return key

    def finish_cleanup(self, *, writers_quiescent):
        if not writers_quiescent or any(
            not r["absent"] for r in self.data["resources"].values()
        ):
            raise StateError("CLEANUP_INCOMPLETE")
        if any(
            o["status"] in {"intent", "accepted"}
            for o in self.data["operations"].values()
        ):
            raise StateError("OPERATIONS_UNRESOLVED")
        self.data["cleanup_complete"] = True
        self.phase("complete")
