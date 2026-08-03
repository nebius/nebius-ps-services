#!/usr/bin/env python3
"""Private cross-skill coordination for managed outer worktrees.

This module deliberately owns no public skill actions.  It serializes the
temporary coordinator lease with long-running worktree publication
reservations so the outer branch has one lifecycle owner at a time.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import tempfile
from typing import Any, Iterator

from worktree_state import state_directory


LEASE_SCHEMA = 3
RESERVATION_SCHEMA = 2
LEASE_KIND = "coordinator"
OWNER_KINDS = {"task-implementer", "agentic-sdlc"}
LEASE_STATES = {"planned", "present", "absent"}
RESOURCE_KINDS = {"integration", "worker"}
PUBLICATION_ACTIONS = {"push", "create-pr"}
OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40,64}$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InteropError(RuntimeError):
    """Private interop state is unsafe, conflicting, or incomplete."""


def _outer_branch(name: str) -> str:
    if not name.startswith("project-"):
        raise InteropError("managed outer name is invalid")
    return f"feature/{name.removeprefix('project-')}"


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise InteropError(f"interop directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    _private_dir(path.parent)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary = Path(temporary_name)
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    except OSError as error:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise InteropError(f"could not persist interop state: {path}") from error


def _load_json(path: Path, label: str) -> dict[str, object] | None:
    if path.is_symlink():
        raise InteropError(f"{label} must not be a symlink: {path}")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InteropError(f"{label} is unreadable or invalid: {path}") from error
    if not isinstance(payload, dict):
        raise InteropError(f"{label} must contain an object: {path}")
    return payload


def _safe_scope(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise InteropError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise InteropError(f"{label} is invalid")
    return value


def scope_contains(outer: str, inner: str) -> bool:
    if outer == ".":
        return True
    outer_path = PurePosixPath(outer)
    inner_path = PurePosixPath(inner)
    return inner_path == outer_path or outer_path in inner_path.parents


def _absolute(value: object, label: str) -> str:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise InteropError(f"{label} must be absolute")
    return str(Path(value).resolve())


def _resource_path(value: object) -> str:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise InteropError("task lease resource path must be absolute")
    normalized = os.path.abspath(value)
    if normalized != value:
        raise InteropError("task lease resource path must be normalized")
    return normalized


def _token(value: object, label: str = "interop token") -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise InteropError(f"{label} is invalid")
    return value


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or OBJECT_ID_RE.fullmatch(value) is None:
        raise InteropError(f"{label} is invalid")
    return value


def _run_id(value: object) -> str:
    if not isinstance(value, str) or RUN_ID_RE.fullmatch(value) is None:
        raise InteropError("task run identity is invalid")
    return value


def _root(primary: Path) -> Path:
    root = state_directory(primary)
    _private_dir(root)
    return root


def lease_path(primary: Path, name: str) -> Path:
    return _root(primary) / "leases" / f"{name}.json"


def reservation_path(primary: Path, name: str) -> Path:
    return _root(primary) / "reservations" / f"{name}.json"


@contextmanager
def interop_lock(primary: Path) -> Iterator[None]:
    root = _root(primary)
    path = root / ".interop.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise InteropError(f"could not open interop lock: {path}") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise InteropError(f"interop lock must be a regular file: {path}")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _validate_resource(value: object, owner_kind: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "kind",
        "path",
        "branch",
        "state",
    }:
        raise InteropError("task lease resource fields are invalid")
    kind = value.get("kind")
    branch = value.get("branch")
    state = value.get("state")
    if kind not in RESOURCE_KINDS:
        raise InteropError("task lease resource kind is invalid")
    patterns = {
        "task-implementer": r"codex/ti-[A-Za-z0-9._/-]+",
        "agentic-sdlc": r"codex/sdlc/[A-Za-z0-9._/-]+",
    }
    if (
        not isinstance(branch, str)
        or len(branch) > 240
        or re.fullmatch(patterns[owner_kind], branch) is None
    ):
        raise InteropError("task lease resource branch is invalid")
    if state not in LEASE_STATES:
        raise InteropError("task lease resource state is invalid")
    return {
        "kind": str(kind),
        "path": _resource_path(value.get("path")),
        "branch": branch,
        "state": str(state),
    }


def validate_lease(value: dict[str, object], name: str) -> dict[str, object]:
    required = {
        "schema",
        "kind",
        "owner_kind",
        "name",
        "branch",
        "worktree",
        "scope",
        "common_dir",
        "workspace",
        "run_id",
        "task_scope",
        "initial_head",
        "promoted_head",
        "token",
        "resources",
    }
    if value.get("schema") in {1, 2}:
        raise InteropError(
            "WORKFLOW_UPGRADE_REQUIRED: unfinished legacy lease schema is unsupported"
        )
    if set(value) != required or value.get("schema") != LEASE_SCHEMA:
        raise InteropError("task lease fields or schema are invalid")
    if value.get("kind") != LEASE_KIND or value.get("name") != name:
        raise InteropError("task lease identity is invalid")
    owner_kind = value.get("owner_kind")
    if owner_kind not in OWNER_KINDS:
        raise InteropError("task lease owner kind is invalid")
    branch = value.get("branch")
    if not isinstance(branch, str) or branch != _outer_branch(name):
        raise InteropError("task lease branch is invalid")
    scope = _safe_scope(value.get("scope"), "task lease outer scope")
    task_scope = _safe_scope(value.get("task_scope"), "task lease task scope")
    if not scope_contains(scope, task_scope):
        raise InteropError("task lease task scope escapes the outer scope")
    resources_value = value.get("resources")
    if not isinstance(resources_value, list):
        raise InteropError("task lease resources are invalid")
    resources = [_validate_resource(item, str(owner_kind)) for item in resources_value]
    identities = {(item["path"], item["branch"]) for item in resources}
    if len(identities) != len(resources):
        raise InteropError("task lease resources repeat")
    promoted = value.get("promoted_head")
    if promoted is not None:
        _sha(promoted, "task lease promoted head")
    return {
        **value,
        "worktree": _absolute(value.get("worktree"), "task lease worktree"),
        "common_dir": _absolute(value.get("common_dir"), "task lease common directory"),
        "workspace": _absolute(value.get("workspace"), "task lease workspace"),
        "scope": scope,
        "task_scope": task_scope,
        "run_id": _run_id(value.get("run_id")),
        "initial_head": _sha(value.get("initial_head"), "task lease initial head"),
        "token": _token(value.get("token")),
        "resources": resources,
    }


def load_lease(primary: Path, name: str) -> dict[str, object] | None:
    value = _load_json(lease_path(primary, name), "task lease")
    return validate_lease(value, name) if value is not None else None


def validate_reservation(value: dict[str, object], name: str) -> dict[str, object]:
    if value.get("schema") == 1:
        raise InteropError(
            "WORKFLOW_UPGRADE_REQUIRED: unfinished reservation schema v1 is unsupported"
        )
    if (
        set(value)
        != {
            "schema",
            "name",
            "branch",
            "worktree",
            "action",
            "starting_head",
            "token",
        }
        or value.get("schema") != RESERVATION_SCHEMA
    ):
        raise InteropError("publication reservation fields or schema are invalid")
    if value.get("name") != name or value.get("branch") != _outer_branch(name):
        raise InteropError("publication reservation identity is invalid")
    if value.get("action") not in PUBLICATION_ACTIONS:
        raise InteropError("publication reservation action is invalid")
    return {
        **value,
        "worktree": _absolute(
            value.get("worktree"), "publication reservation worktree"
        ),
        "starting_head": _sha(
            value.get("starting_head"), "publication reservation starting head"
        ),
        "token": _token(value.get("token"), "publication reservation token"),
    }


def load_reservation(primary: Path, name: str) -> dict[str, object] | None:
    value = _load_json(reservation_path(primary, name), "publication reservation")
    return validate_reservation(value, name) if value is not None else None


def assert_idle(primary: Path, name: str) -> None:
    lease = load_lease(primary, name)
    if lease is not None:
        raise InteropError(
            f"{lease['owner_kind']} still owns the outer worktree; repeat its managed "
            "run until final cleanup and final gates release the lease"
        )
    reservation = load_reservation(primary, name)
    if reservation is not None:
        raise InteropError(
            f"a prior worktree {reservation['action']} action must be repeated "
            "to reconcile its publication reservation"
        )


def acquire_task_lease(
    primary: Path,
    *,
    name: str,
    branch: str,
    worktree: Path,
    scope: str,
    common_dir: Path,
    workspace: Path,
    run_id: str,
    task_scope: str,
    initial_head: str,
    owner_kind: str,
) -> dict[str, object]:
    if owner_kind not in OWNER_KINDS:
        raise InteropError("task lease owner kind is invalid")
    with interop_lock(primary):
        if load_reservation(primary, name) is not None:
            raise InteropError(
                "the outer worktree has an active push/create-pr reservation"
            )
        expected = {
            "owner_kind": owner_kind,
            "name": name,
            "branch": branch,
            "worktree": str(worktree.resolve()),
            "scope": scope,
            "common_dir": str(common_dir.resolve()),
            "workspace": str(workspace.resolve()),
            "run_id": run_id,
            "task_scope": task_scope,
            "initial_head": initial_head,
        }
        existing = load_lease(primary, name)
        if existing is not None:
            if any(existing.get(key) != value for key, value in expected.items()):
                raise InteropError(
                    "the outer worktree is leased by a different task run"
                )
            return {**existing, "status": "resumed"}
        lease: dict[str, object] = {
            "schema": LEASE_SCHEMA,
            "kind": LEASE_KIND,
            **expected,
            "promoted_head": None,
            "token": secrets.token_hex(16),
            "resources": [],
        }
        validate_lease(lease, name)
        _atomic_json(lease_path(primary, name), lease)
        return {**lease, "status": "acquired"}


def update_task_lease(
    primary: Path,
    *,
    name: str,
    token: str,
    owner_kind: str,
    promoted_head: str | None = None,
    resource: dict[str, str] | None = None,
) -> dict[str, object]:
    with interop_lock(primary):
        lease = load_lease(primary, name)
        if (
            lease is None
            or lease["token"] != _token(token)
            or lease["owner_kind"] != owner_kind
        ):
            raise InteropError("task lease token does not match active state")
        updated = dict(lease)
        if promoted_head is not None:
            updated["promoted_head"] = _sha(promoted_head, "task lease promoted head")
        if resource is not None:
            item = _validate_resource(resource, owner_kind)
            resources = [dict(value) for value in lease["resources"]]
            match = next(
                (
                    value
                    for value in resources
                    if value["path"] == item["path"]
                    and value["branch"] == item["branch"]
                ),
                None,
            )
            if match is None:
                if item["state"] != "planned":
                    raise InteropError("new task lease resources must start planned")
                resources.append(item)
            else:
                transitions = {
                    "planned": {"planned", "present", "absent"},
                    "present": {"present", "absent"},
                    "absent": {"absent"},
                }
                if (
                    item["kind"] != match["kind"]
                    or item["state"] not in transitions[match["state"]]
                ):
                    raise InteropError("task lease resource transition is invalid")
                match["state"] = item["state"]
            updated["resources"] = resources
        validate_lease(updated, name)
        _atomic_json(lease_path(primary, name), updated)
        return updated


def release_task_lease(
    primary: Path,
    *,
    name: str,
    token: str,
    owner_kind: str,
    observed_outer_head: str,
    registered_worktrees: set[Path],
    existing_branches: set[str],
) -> dict[str, object]:
    with interop_lock(primary):
        lease = load_lease(primary, name)
        if lease is None:
            return {"status": "already-released", "name": name}
        if lease["token"] != _token(token) or lease["owner_kind"] != owner_kind:
            raise InteropError("task lease token does not match active state")
        promoted = lease.get("promoted_head")
        if promoted is None or observed_outer_head != promoted:
            raise InteropError("outer worktree is not at the leased promoted head")
        retained: list[str] = []
        resources = [dict(value) for value in lease["resources"]]
        for resource in resources:
            path = Path(resource["path"])
            branch = resource["branch"]
            if (
                path.resolve() in registered_worktrees
                or path.exists()
                or path.is_symlink()
                or branch in existing_branches
            ):
                retained.append(f"{path} ({branch})")
            else:
                resource["state"] = "absent"
        if retained:
            lease["resources"] = resources
            _atomic_json(lease_path(primary, name), lease)
            raise InteropError(
                "task lease retains internal resources: " + ", ".join(retained)
            )
        lease_path(primary, name).unlink()
        return {"status": "released", "name": name}


def begin_publication(
    primary: Path,
    *,
    name: str,
    branch: str,
    worktree: Path,
    action: str,
    starting_head: str,
) -> dict[str, object]:
    if action not in PUBLICATION_ACTIONS:
        raise InteropError("publication action is invalid")
    with interop_lock(primary):
        if load_lease(primary, name) is not None:
            raise InteropError(
                "a nested coordinator still owns the outer worktree; publication is blocked"
            )
        existing = load_reservation(primary, name)
        if existing is not None:
            if (
                existing["action"] != action
                or existing["branch"] != branch
                or existing["worktree"] != str(worktree.resolve())
            ):
                raise InteropError(
                    "a different worktree publication action must be reconciled first"
                )
            return {**existing, "status": "resumed"}
        reservation: dict[str, object] = {
            "schema": RESERVATION_SCHEMA,
            "name": name,
            "branch": branch,
            "worktree": str(worktree.resolve()),
            "action": action,
            "starting_head": _sha(starting_head, "publication starting head"),
            "token": secrets.token_hex(16),
        }
        validate_reservation(reservation, name)
        _atomic_json(reservation_path(primary, name), reservation)
        return {**reservation, "status": "acquired"}


def end_publication(
    primary: Path, *, name: str, action: str, reservation_id: str
) -> dict[str, object]:
    with interop_lock(primary):
        reservation = load_reservation(primary, name)
        if reservation is None:
            return {"status": "already-released", "name": name, "action": action}
        if reservation["action"] != action or reservation["token"] != _token(
            reservation_id
        ):
            raise InteropError("publication reservation token or action does not match")
        reservation_path(primary, name).unlink()
        return {"status": "released", "name": name, "action": action}
