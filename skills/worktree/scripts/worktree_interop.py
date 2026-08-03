#!/usr/bin/env python3
"""Private cross-skill coordination for managed outer worktrees.

This module deliberately owns no public skill actions. It serializes temporary
coordinator leases with durable local-integration reservations so one managed
child has one lifecycle owner and one source branch has one integration at a
time.
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

from worktree_state import (
    StateError,
    checked_state_directory,
    fsync_directory,
    load_manifest,
)


LEASE_SCHEMA = 4
RESERVATION_SCHEMA = 3
LEASE_KIND = "coordinator"
LEASE_REMOVAL_SCHEMA = 1
LEASE_REMOVAL_KIND = "coordinator-removal"
OWNER_KINDS = {"task-implementer", "agentic-sdlc"}
LEASE_RECORD_STATES = {"active", "released"}
RESOURCE_STATES = {"planned", "present", "absent"}
RESOURCE_KINDS = {"integration", "worker"}
INTEGRATION_STATES = {"planned", "present", "ready"}
OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40,64}$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WORKTREE_NAME_RE = re.compile(r"^project-[a-z0-9](?:[a-z0-9-]{0,86}[a-z0-9])?$")


class InteropError(RuntimeError):
    """Private interop state is unsafe, conflicting, or incomplete."""


def _outer_branch(name: str) -> str:
    if WORKTREE_NAME_RE.fullmatch(name) is None:
        raise InteropError("managed outer name is invalid")
    return f"feature/{name.removeprefix('project-')}"


def _private_dir(path: Path, *, create: bool = True) -> None:
    if path.is_symlink():
        raise InteropError(f"interop directory must not be a symlink: {path}")
    if path.exists():
        if not path.is_dir():
            raise InteropError(f"interop directory must be a directory: {path}")
        try:
            canonical = path.resolve(strict=True)
        except OSError as error:
            raise InteropError(f"interop directory is unavailable: {path}") from error
        if canonical != path:
            raise InteropError(f"interop directory must be canonical: {path}")
    elif create:
        try:
            path.mkdir(parents=False, mode=0o700)
            fsync_directory(path.parent)
        except OSError as error:
            raise InteropError(f"could not create interop directory: {path}") from error
        if path.is_symlink() or path.resolve(strict=True) != path:
            raise InteropError(f"interop directory must be canonical: {path}")
    if create:
        try:
            path.chmod(0o700)
        except OSError as error:
            raise InteropError(f"could not secure interop directory: {path}") from error


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
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary = Path(temporary_name)
        os.replace(temporary, path)
        fsync_directory(path.parent)
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
    normalized = str(Path(value).resolve())
    if os.path.normpath(value) != value:
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


def _root(primary: Path, *, create: bool = False) -> Path:
    try:
        return checked_state_directory(primary, create=create)
    except RuntimeError as error:
        raise InteropError(str(error)) from error


def lease_path(primary: Path, name: str) -> Path:
    directory = _root(primary) / "leases"
    _private_dir(directory, create=False)
    return directory / f"{name}.json"


def lease_removal_path(primary: Path, name: str) -> Path:
    directory = _root(primary) / "lease-removals"
    _private_dir(directory, create=False)
    return directory / f"{name}.json"


def reservation_path(primary: Path, name: str) -> Path:
    directory = _root(primary) / "reservations"
    _private_dir(directory, create=False)
    return directory / f"{name}.json"


@contextmanager
def interop_lock(primary: Path) -> Iterator[None]:
    root = _root(primary, create=True)
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


@contextmanager
def lifecycle_creation_lock(primary: Path) -> Iterator[None]:
    """Serialize managed lifecycle selection, creation, and activation."""

    root = _root(primary, create=True)
    path = root / ".lifecycle.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise InteropError(f"could not open lifecycle lock: {path}") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise InteropError(f"lifecycle lock must be a regular file: {path}")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def integration_transition_lock(primary: Path, name: str) -> Iterator[None]:
    """Serialize Git mutations for one child's durable integration attempt."""

    _outer_branch(name)
    directory = _root(primary, create=True) / "integration-locks"
    _private_dir(directory)
    path = directory / f"{name}.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise InteropError(
            f"could not open integration transition lock: {path}"
        ) from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise InteropError(
                f"integration transition lock must be a regular file: {path}"
            )
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
    if state not in RESOURCE_STATES:
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
        "state",
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
        "promotion_heads",
        "token",
        "resources",
    }
    if value.get("schema") in {1, 2, 3}:
        raise InteropError(
            "WORKFLOW_UPGRADE_REQUIRED: unfinished legacy lease schema is unsupported"
        )
    if set(value) != required or value.get("schema") != LEASE_SCHEMA:
        raise InteropError("task lease fields or schema are invalid")
    if value.get("kind") != LEASE_KIND or value.get("name") != name:
        raise InteropError("task lease identity is invalid")
    state = value.get("state")
    if state not in LEASE_RECORD_STATES:
        raise InteropError("task lease state is invalid")
    owner_kind = value.get("owner_kind")
    if owner_kind not in OWNER_KINDS:
        raise InteropError("task lease owner kind is invalid")
    branch = value.get("branch")
    if not isinstance(branch, str) or branch != _outer_branch(name):
        raise InteropError("task lease branch is invalid")
    scope = _safe_scope(value.get("scope"), "task lease outer scope")
    task_scope = _safe_scope(value.get("task_scope"), "task lease task scope")
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
    promotion_heads_value = value.get("promotion_heads")
    if not isinstance(promotion_heads_value, list) or not promotion_heads_value:
        raise InteropError("task lease promotion history is invalid")
    promotion_heads = [
        _sha(item, "task lease promotion history head")
        for item in promotion_heads_value
    ]
    initial_head = _sha(value.get("initial_head"), "task lease initial head")
    if promotion_heads[0] != initial_head or (
        (promoted is None and promotion_heads != [initial_head])
        or (promoted is not None and promotion_heads[-1] != promoted)
    ):
        raise InteropError("task lease promotion history is inconsistent")
    if state == "released" and (
        promoted is None or any(item["state"] != "absent" for item in resources)
    ):
        raise InteropError(
            "released task lease requires a promoted head and absent resources"
        )
    return {
        **value,
        "worktree": _absolute(value.get("worktree"), "task lease worktree"),
        "common_dir": _absolute(value.get("common_dir"), "task lease common directory"),
        "workspace": _absolute(value.get("workspace"), "task lease workspace"),
        "scope": scope,
        "task_scope": task_scope,
        "run_id": _run_id(value.get("run_id")),
        "initial_head": initial_head,
        "token": _token(value.get("token")),
        "promotion_heads": promotion_heads,
        "resources": resources,
    }


def load_lease(primary: Path, name: str) -> dict[str, object] | None:
    value = _load_json(lease_path(primary, name), "task lease")
    return validate_lease(value, name) if value is not None else None


def validate_lease_removal(value: dict[str, object], name: str) -> dict[str, object]:
    if set(value) != {"schema", "kind", "name", "receipt"} or (
        value.get("schema") != LEASE_REMOVAL_SCHEMA
        or value.get("kind") != LEASE_REMOVAL_KIND
        or value.get("name") != name
    ):
        raise InteropError("task lease removal intent is invalid")
    receipt_value = value.get("receipt")
    if not isinstance(receipt_value, dict):
        raise InteropError("task lease removal receipt is invalid")
    receipt = validate_lease(receipt_value, name)
    if receipt["state"] != "released":
        raise InteropError("task lease removal requires a released receipt")
    return {**value, "receipt": receipt}


def load_lease_removal(primary: Path, name: str) -> dict[str, object] | None:
    value = _load_json(lease_removal_path(primary, name), "task lease removal")
    return validate_lease_removal(value, name) if value is not None else None


def prepare_lease_removal(
    primary: Path, name: str, receipt: dict[str, object]
) -> dict[str, object]:
    validated = validate_lease(receipt, name)
    if validated["state"] != "released":
        raise InteropError("task lease removal requires a released receipt")
    existing = load_lease_removal(primary, name)
    if existing is not None:
        if existing["receipt"] != validated:
            raise InteropError("task lease removal receipt changed")
        return existing
    value: dict[str, object] = {
        "schema": LEASE_REMOVAL_SCHEMA,
        "kind": LEASE_REMOVAL_KIND,
        "name": name,
        "receipt": validated,
    }
    directory = _root(primary, create=True) / "lease-removals"
    _private_dir(directory)
    _atomic_json(directory / f"{name}.json", value)
    return value


def delete_lease_removal(
    primary: Path, name: str, *, expected_receipt: dict[str, object]
) -> None:
    intent = load_lease_removal(primary, name)
    if intent is None:
        return
    if intent["receipt"] != expected_receipt:
        raise InteropError("task lease removal receipt changed")
    try:
        path = lease_removal_path(primary, name)
        path.unlink()
        fsync_directory(path.parent)
    except OSError as error:
        raise InteropError("could not remove task lease removal intent") from error


def lifecycle_lease(
    primary: Path, name: str, *, allow_external_transition: bool = False
) -> dict[str, object] | None:
    """Reconcile manifest lease participation with the authoritative record."""

    if load_lease_removal(primary, name) is not None:
        raise InteropError(
            "outer worktree removal is in progress; repeat $worktree remove"
        )

    try:
        manifest = load_manifest(primary, name, required=False)
    except StateError as error:
        raise InteropError(str(error)) from error
    lease = load_lease(primary, name)
    if manifest is None:
        if lease is None:
            return None
        raise InteropError("task lease exists without its ownership manifest")
    if manifest.lease_state == "none":
        if lease is None:
            return None
        if lease["state"] == "active":
            return lease
        raise InteropError("released task lease is missing manifest participation")
    if lease is None:
        raise InteropError("participating task lease is missing")
    if (
        lease["owner_kind"] != manifest.lease_owner
        or lease["token"] != manifest.lease_token
    ):
        raise InteropError("task lease disagrees with its ownership manifest")
    if (
        lease["branch"] != manifest.branch
        or lease["worktree"] != manifest.worktree
        or lease["scope"] != manifest.scope
    ):
        raise InteropError("task lease outer identity disagrees with its manifest")
    if lease["state"] != manifest.lease_state:
        if not (
            allow_external_transition
            and manifest.lease_state == "active"
            and lease["state"] == "released"
        ):
            raise InteropError("task lease disagrees with its ownership manifest")
    return lease


def all_leases(primary: Path) -> list[dict[str, object]]:
    directory = _root(primary) / "leases"
    _private_dir(directory, create=False)
    leases: dict[str, dict[str, object]] = {}
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            value = load_lease(primary, path.stem)
            if value is not None:
                leases[path.stem] = value
    removals = _root(primary) / "lease-removals"
    _private_dir(removals, create=False)
    if removals.exists():
        for path in sorted(removals.glob("*.json")):
            intent = load_lease_removal(primary, path.stem)
            if intent is None:
                continue
            receipt = intent["receipt"]
            existing = leases.get(path.stem)
            if existing is not None and existing != receipt:
                raise InteropError("task lease disagrees with its removal intent")
            leases[path.stem] = receipt
    return [leases[name] for name in sorted(leases)]


def validate_reservation(value: dict[str, object], name: str) -> dict[str, object]:
    if value.get("schema") in {1, 2}:
        raise InteropError(
            "WORKFLOW_UPGRADE_REQUIRED: unfinished publication reservation is unsupported"
        )
    if (
        set(value)
        != {
            "schema",
            "name",
            "branch",
            "worktree",
            "source_branch",
            "source_ref",
            "source_head",
            "child_head",
            "integration_branch",
            "integration_worktree",
            "integration_head",
            "state",
            "token",
        }
        or value.get("schema") != RESERVATION_SCHEMA
    ):
        raise InteropError("integration reservation fields or schema are invalid")
    if value.get("name") != name or value.get("branch") != _outer_branch(name):
        raise InteropError("integration reservation identity is invalid")
    source_branch = value.get("source_branch")
    if (
        not isinstance(source_branch, str)
        or not source_branch
        or value.get("source_ref") != f"refs/heads/{source_branch}"
    ):
        raise InteropError("integration reservation source identity is invalid")
    integration_branch = value.get("integration_branch")
    if (
        not isinstance(integration_branch, str)
        or integration_branch != f"codex/worktree-integrate/{name}"
    ):
        raise InteropError("integration reservation branch is invalid")
    state = value.get("state")
    if state not in INTEGRATION_STATES:
        raise InteropError("integration reservation state is invalid")
    integration_head = value.get("integration_head")
    if state == "ready":
        _sha(integration_head, "integration candidate head")
    elif integration_head is not None:
        raise InteropError("unfinished integration reservation has a candidate head")
    return {
        **value,
        "worktree": _absolute(value.get("worktree"), "integration child worktree"),
        "source_head": _sha(value.get("source_head"), "integration source head"),
        "child_head": _sha(value.get("child_head"), "integration child head"),
        "integration_worktree": _absolute(
            value.get("integration_worktree"), "integration candidate worktree"
        ),
        "token": _token(value.get("token"), "integration reservation token"),
    }


def load_reservation(primary: Path, name: str) -> dict[str, object] | None:
    value = _load_json(reservation_path(primary, name), "integration reservation")
    return validate_reservation(value, name) if value is not None else None


def active_reservations(primary: Path) -> list[dict[str, object]]:
    directory = _root(primary) / "reservations"
    _private_dir(directory, create=False)
    if not directory.exists():
        return []
    reservations: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        value = load_reservation(primary, path.stem)
        if value is not None:
            reservations.append(value)
    return reservations


def assert_idle(primary: Path, name: str) -> None:
    lease = lifecycle_lease(primary, name)
    if lease is not None and lease["state"] == "active":
        raise InteropError(
            f"{lease['owner_kind']} still owns the outer worktree; repeat its managed "
            "run until final cleanup and final gates release the lease"
        )
    reservation = load_reservation(primary, name)
    if reservation is not None:
        raise InteropError(
            "a prior worktree integration must be repeated to reconcile its "
            "durable reservation"
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
        try:
            manifest = load_manifest(primary, name)
        except StateError as error:
            raise InteropError(str(error)) from error
        assert manifest is not None
        if load_reservation(primary, name) is not None:
            raise InteropError(
                "the outer worktree has an active integration reservation"
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
        if manifest.lease_state in {"active", "released"} and existing is None:
            raise InteropError("participating task lease is missing")
        if existing is not None:
            if manifest.lease_state == "active" and (
                manifest.lease_owner != existing["owner_kind"]
                or manifest.lease_token != existing["token"]
            ):
                raise InteropError("task lease disagrees with its ownership manifest")
            if existing["state"] == "released":
                raise InteropError(
                    "the outer worktree has a terminal released lease receipt; "
                    "remove it before starting another nested coordinator"
                )
            if any(existing.get(key) != value for key, value in expected.items()):
                raise InteropError(
                    "the outer worktree is leased by a different task run"
                )
            return {**existing, "status": "resumed"}
        lease: dict[str, object] = {
            "schema": LEASE_SCHEMA,
            "kind": LEASE_KIND,
            "state": "active",
            **expected,
            "promoted_head": None,
            "promotion_heads": [initial_head],
            "token": secrets.token_hex(16),
            "resources": [],
        }
        validate_lease(lease, name)
        _atomic_json(lease_path(primary, name), lease)
        return {**lease, "status": "acquired"}


def inspect_task_lease(
    primary: Path, *, name: str, token: str, owner_kind: str
) -> dict[str, object]:
    """Return one exact validated lease without mutating or reacquiring it."""

    with interop_lock(primary):
        lease = lifecycle_lease(primary, name, allow_external_transition=True)
        if lease is None:
            raise InteropError("task lease is missing")
        if lease["token"] != _token(token) or lease["owner_kind"] != owner_kind:
            raise InteropError("task lease identity does not match durable state")
        return lease


def update_task_lease(
    primary: Path,
    *,
    name: str,
    token: str,
    owner_kind: str,
    promoted_head: str | None = None,
    expected_previous_head: str | None = None,
    resource: dict[str, str] | None = None,
) -> dict[str, object]:
    with interop_lock(primary):
        lease = lifecycle_lease(primary, name)
        if (
            lease is None
            or lease["token"] != _token(token)
            or lease["owner_kind"] != owner_kind
        ):
            raise InteropError("task lease token does not match active state")
        if lease["state"] != "active":
            raise InteropError("released task lease cannot be updated")
        updated = dict(lease)
        if promoted_head is not None:
            promoted = _sha(promoted_head, "task lease promoted head")
            if expected_previous_head is None:
                raise InteropError("task lease promotion requires its expected head")
            expected = _sha(expected_previous_head, "task lease expected previous head")
            existing_promoted = lease.get("promoted_head")
            history = list(lease["promotion_heads"])
            current = existing_promoted or lease["initial_head"]
            if existing_promoted == promoted:
                prior = history[-2] if len(history) > 1 else history[-1]
                if expected not in {prior, promoted}:
                    raise InteropError("task lease promotion compare-and-set failed")
            elif current != expected:
                raise InteropError("task lease promotion compare-and-set failed")
            elif promoted != expected:
                history.append(promoted)
            updated["promoted_head"] = promoted
            updated["promotion_heads"] = history
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
            raise InteropError("task lease is missing; release cannot be proven")
        if lease["token"] != _token(token) or lease["owner_kind"] != owner_kind:
            raise InteropError("task lease token does not match active state")
        promoted = lease.get("promoted_head")
        if promoted is None or observed_outer_head != promoted:
            raise InteropError("outer worktree is not at the leased promoted head")
        released = lease["state"] == "released"
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
            if not released:
                lease["resources"] = resources
                _atomic_json(lease_path(primary, name), lease)
            raise InteropError(
                "task lease retains internal resources: " + ", ".join(retained)
            )
        if released:
            return {**lease, "status": "already-released"}
        lease["resources"] = resources
        lease["state"] = "released"
        validate_lease(lease, name)
        _atomic_json(lease_path(primary, name), lease)
        return {**lease, "status": "released"}


def validate_released_resources(
    lease: dict[str, object],
    *,
    registered_worktrees: set[Path],
    existing_branches: set[str],
) -> dict[str, object]:
    """Validate a terminal receipt and prove every private resource absent."""

    if lease["state"] != "released":
        raise InteropError("active task lease cannot be removed")
    retained = [
        f"{resource['path']} ({resource['branch']})"
        for resource in lease["resources"]
        if (
            Path(str(resource["path"])).resolve() in registered_worktrees
            or Path(str(resource["path"])).exists()
            or Path(str(resource["path"])).is_symlink()
            or resource["branch"] in existing_branches
        )
    ]
    if retained:
        raise InteropError(
            "released task lease resources reappeared: " + ", ".join(retained)
        )
    return lease


def validate_released_lease_resources(
    primary: Path,
    name: str,
    *,
    registered_worktrees: set[Path],
    existing_branches: set[str],
) -> dict[str, object] | None:
    lease = load_lease(primary, name)
    if lease is None:
        return None
    return validate_released_resources(
        lease,
        registered_worktrees=registered_worktrees,
        existing_branches=existing_branches,
    )


def delete_released_lease(
    primary: Path,
    name: str,
    *,
    registered_worktrees: set[Path],
    existing_branches: set[str],
    expected_lease: dict[str, object] | None = None,
) -> None:
    """Delete only a fully revalidated terminal receipt during outer removal."""

    lease = validate_released_lease_resources(
        primary,
        name,
        registered_worktrees=registered_worktrees,
        existing_branches=existing_branches,
    )
    if lease is None:
        return
    if expected_lease is not None and lease != expected_lease:
        raise InteropError("released task lease changed before receipt deletion")
    path = lease_path(primary, name)
    try:
        path.unlink()
        fsync_directory(path.parent)
    except OSError as error:
        raise InteropError(f"could not remove released task lease: {path}") from error


def begin_integration(
    primary: Path,
    *,
    name: str,
    branch: str,
    worktree: Path,
    source_branch: str,
    source_ref: str,
    source_head: str,
    child_head: str,
    integration_branch: str,
    integration_worktree: Path,
) -> dict[str, object]:
    with interop_lock(primary):
        lease = lifecycle_lease(primary, name)
        if lease is not None and lease["state"] == "active":
            raise InteropError(
                "a nested coordinator still owns the outer worktree; integration is blocked"
            )
        existing = load_reservation(primary, name)
        if existing is not None:
            expected = {
                "branch": branch,
                "worktree": str(worktree.resolve()),
                "source_branch": source_branch,
                "source_ref": source_ref,
                "source_head": source_head,
                "child_head": child_head,
                "integration_branch": integration_branch,
                "integration_worktree": str(integration_worktree.resolve()),
            }
            if any(existing.get(key) != value for key, value in expected.items()):
                raise InteropError(
                    "the worktree integration identity changed before resume"
                )
            return {**existing, "status": "resumed"}
        for reservation in active_reservations(primary):
            if reservation["source_ref"] == source_ref:
                raise InteropError(
                    "another managed child has an active integration into this source branch"
                )
        reservation: dict[str, object] = {
            "schema": RESERVATION_SCHEMA,
            "name": name,
            "branch": branch,
            "worktree": str(worktree.resolve()),
            "source_branch": source_branch,
            "source_ref": source_ref,
            "source_head": _sha(source_head, "integration source head"),
            "child_head": _sha(child_head, "integration child head"),
            "integration_branch": integration_branch,
            "integration_worktree": str(integration_worktree.resolve()),
            "integration_head": None,
            "state": "planned",
            "token": secrets.token_hex(16),
        }
        validate_reservation(reservation, name)
        _atomic_json(reservation_path(primary, name), reservation)
        return {**reservation, "status": "acquired"}


def update_integration(
    primary: Path,
    *,
    name: str,
    reservation_id: str,
    state: str,
    integration_head: str | None = None,
) -> dict[str, object]:
    with interop_lock(primary):
        reservation = load_reservation(primary, name)
        if reservation is None or reservation["token"] != _token(reservation_id):
            raise InteropError("integration reservation token does not match")
        updated = dict(reservation)
        transitions = {
            "planned": {"present"},
            "present": {"present", "ready"},
            "ready": {"ready"},
        }
        if state not in transitions[str(reservation["state"])]:
            raise InteropError("integration reservation state transition is invalid")
        updated["state"] = state
        updated["integration_head"] = integration_head
        validate_reservation(updated, name)
        _atomic_json(reservation_path(primary, name), updated)
        return updated


def end_integration(
    primary: Path, *, name: str, reservation_id: str
) -> dict[str, object]:
    with interop_lock(primary):
        reservation = load_reservation(primary, name)
        if reservation is None:
            return {"status": "already-released", "name": name}
        if reservation["token"] != _token(reservation_id):
            raise InteropError("integration reservation token does not match")
        path = reservation_path(primary, name)
        try:
            path.unlink()
            fsync_directory(path.parent)
        except OSError as error:
            raise InteropError(
                f"could not remove integration reservation: {path}"
            ) from error
        return {"status": "released", "name": name}
