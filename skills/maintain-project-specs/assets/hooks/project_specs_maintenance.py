#!/usr/bin/env python3
"""Private, fail-safe maintenance for project-spec lifecycle bundles."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

MAINTENANCE_SCHEMA = "maintain-project-specs.maintenance.v2"
ACTIVITY_SCHEMA = "maintain-project-specs.activity.v1"
JOURNAL_SCHEMA = "maintain-project-specs.cleanup-journal.v2"
USAGE_SCHEMA = "maintain-project-specs.aggregate-usage.v1"
ROOT_CURSOR_SCHEMA = "maintain-project-specs.root-cursor.v1"
OWNERSHIP_REGISTRY_SCHEMA = "project-agent-instructions.workspace-ownership.v1"
HIGH_WATER_BYTES = 1024 * 1024 * 1024
LOW_WATER_BYTES = 768 * 1024 * 1024
TERMINAL_RETENTION_NS = 30 * 24 * 60 * 60 * 1_000_000_000
UNFINISHED_RETENTION_NS = 90 * 24 * 60 * 60 * 1_000_000_000
FUTURE_TOLERANCE_NS = 5 * 60 * 1_000_000_000
MAX_VISITS = 256
# Isolated classifier startup is slower than an in-process import on clean
# installs; keep the SessionStart slice bounded while allowing that path to run.
MAX_RUNTIME_NS = 300 * 1_000_000
MAX_PRIVATE_JSON_BYTES = 1024 * 1024
TERMINAL_PHASES = frozenset({"sealed", "waived"})
EXPIRABLE_PHASES = frozenset(
    {
        "planning-required",
        "planned",
        "implementation-open",
        "reconciliation-required",
    }
)
PROTECTED_PHASES = frozenset({"seal-armed"})
STRUCTURAL_NAMES = frozenset({".maintenance", "migrations", "project-agent-ownership"})
LIFECYCLE_SCHEMA = "maintain-project-specs.lifecycle.v1"
LIFECYCLE_RULES_PATH = "pending-project-rules.md"
LIFECYCLE_KEYS = frozenset(
    {
        "schema",
        "project_scope",
        "git_head_at_prompt",
        "turn_sha256",
        "phase",
        "receipt_sha256",
        "requirements_sha256",
        "design_sha256",
        "rules_path",
        "rules_sha256",
        "project_instructions_state_sha256",
        "project_instructions_reload_required",
        "write_epoch",
        "planned_write_epoch",
        "waiver",
    }
)
WAIVER_KINDS = frozenset(
    {
        "documentation-only",
        "read-only",
        "non-project",
        "project-policy",
        "task-worker-delegated",
    }
)
_LOCAL_LOCKS = threading.local()


class MaintenanceError(RuntimeError):
    """An unsafe or ambiguous private-maintenance condition."""


class MaintenanceDeadline(MaintenanceError):
    """The bounded synchronous maintenance slice exhausted its time budget."""


def _check_deadline(deadline_ns: int | None) -> None:
    if deadline_ns is not None and time.monotonic_ns() >= deadline_ns:
        raise MaintenanceDeadline("private maintenance deadline reached")


def _owner_ok(metadata: os.stat_result) -> bool:
    return not hasattr(os, "getuid") or metadata.st_uid == os.getuid()


def _safe_directory(
    path: Path,
    *,
    mode: int = 0o700,
    create: bool = True,
    require_private: bool = True,
) -> None:
    absolute = Path(os.path.abspath(path))
    if absolute.resolve(strict=False) != absolute:
        raise MaintenanceError(f"unsafe private maintenance path: {path.name}")
    created = False
    if create:
        try:
            absolute.mkdir(mode=mode)
            created = True
        except FileExistsError:
            pass
    try:
        metadata = absolute.lstat()
    except FileNotFoundError:
        raise MaintenanceError(
            f"private maintenance directory is missing: {path.name}"
        ) from None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or not _owner_ok(metadata)
        or (require_private and stat.S_IMODE(metadata.st_mode) != mode)
    ):
        raise MaintenanceError(f"unsafe private maintenance directory: {path.name}")
    if created:
        os.chmod(absolute, mode)


def _workspace_for_state(state_path: Path) -> tuple[Path, str]:
    state_path = Path(os.path.abspath(state_path))
    if state_path.resolve(strict=False) != state_path:
        raise MaintenanceError("lifecycle path contains a symbolic-link ancestor")
    session_root = state_path.parent
    workspace_root = session_root.parent
    if (
        state_path.name != "lifecycle.json"
        or workspace_root.parent.name != "project-specs"
    ):
        raise MaintenanceError("lifecycle path is outside a workspace session")
    if session_root.name in {"", ".", ".."}:
        raise MaintenanceError("lifecycle session identity is unsafe")
    return workspace_root, session_root.name


def _maintenance_root(workspace_root: Path, *, create_home: bool = False) -> Path:
    _safe_directory(
        workspace_root.parent.parent,
        create=create_home,
        require_private=False,
    )
    _safe_directory(workspace_root.parent)
    _safe_directory(workspace_root)
    metadata = workspace_root.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or not _owner_ok(metadata)
        or metadata.st_mode & 0o077
    ):
        raise MaintenanceError("project-spec workspace is unsafe")
    root = workspace_root / ".maintenance"
    _safe_directory(root)
    _safe_directory(root / "sessions")
    _safe_directory(root / "staging")
    _safe_directory(root / "journals")
    return root


def _open_lock(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    metadata = os.fstat(descriptor)
    current = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or not _owner_ok(metadata)
        or (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino)
    ):
        os.close(descriptor)
        raise MaintenanceError("private maintenance lock is unsafe")
    os.fchmod(descriptor, 0o600)
    return descriptor


@contextmanager
def session_locks(
    state_path: Path,
    *,
    workspace_exclusive: bool = False,
    blocking: bool = True,
    create_home: bool = False,
) -> Iterator[tuple[int, int]]:
    """Lock stable workspace/session identities outside a movable bundle."""

    key = str(Path(os.path.abspath(state_path)))
    held = getattr(_LOCAL_LOCKS, "held", {})
    if key in held:
        depth, held_exclusive, workspace_descriptor, session_descriptor = held[key]
        if workspace_exclusive and not held_exclusive:
            raise MaintenanceError("cannot upgrade a nested maintenance lock")
        held[key] = (
            depth + 1,
            held_exclusive,
            workspace_descriptor,
            session_descriptor,
        )
        _LOCAL_LOCKS.held = held
        try:
            yield workspace_descriptor, session_descriptor
        finally:
            depth, held_exclusive, workspace_descriptor, session_descriptor = held[key]
            if depth == 1:
                del held[key]
            else:
                held[key] = (
                    depth - 1,
                    held_exclusive,
                    workspace_descriptor,
                    session_descriptor,
                )
        return
    workspace_root, session_name = _workspace_for_state(state_path)
    root = _maintenance_root(workspace_root, create_home=create_home)
    workspace_descriptor = _open_lock(root / "workspace.lock")
    session_descriptor: int | None = None
    operation = fcntl.LOCK_EX if workspace_exclusive else fcntl.LOCK_SH
    if not blocking:
        operation |= fcntl.LOCK_NB
    try:
        fcntl.flock(workspace_descriptor, operation)
        session_descriptor = _open_lock(root / "sessions" / f"{session_name}.lock")
        session_operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(session_descriptor, session_operation)
        held[key] = (1, workspace_exclusive, workspace_descriptor, session_descriptor)
        _LOCAL_LOCKS.held = held
        try:
            yield workspace_descriptor, session_descriptor
        finally:
            del held[key]
    finally:
        if session_descriptor is not None:
            fcntl.flock(session_descriptor, fcntl.LOCK_UN)
            os.close(session_descriptor)
        fcntl.flock(workspace_descriptor, fcntl.LOCK_UN)
        os.close(workspace_descriptor)


def _require_inherited_lock_descriptor(descriptor: int, expected: Path) -> None:
    if type(descriptor) is not int or descriptor < 0:
        raise MaintenanceError("inherited maintenance lock descriptor is invalid")
    try:
        metadata = os.fstat(descriptor)
        current = expected.lstat()
    except OSError as error:
        raise MaintenanceError(
            "inherited maintenance lock descriptor is unavailable"
        ) from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or not _owner_ok(metadata)
        or (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise MaintenanceError("inherited maintenance lock descriptor is unsafe")


@contextmanager
def workspace_operation_lock(
    private_root: Path,
    *,
    workspace_lock_fd: int | None = None,
    session_lock_fd: int | None = None,
) -> Iterator[None]:
    """Participate in maintenance locking for one ordinary session operation."""

    absolute = Path(os.path.abspath(private_root.expanduser()))
    session_root = (
        absolute.parent if absolute.name == "project-instructions" else absolute
    )
    if session_root.parent.parent.name != "project-specs":
        if workspace_lock_fd is not None or session_lock_fd is not None:
            raise MaintenanceError("inherited maintenance locks escaped project specs")
        yield
        return
    inherited = workspace_lock_fd is not None or session_lock_fd is not None
    if inherited:
        if workspace_lock_fd is None or session_lock_fd is None:
            raise MaintenanceError("inherited maintenance lock lease is incomplete")
        state_path = session_root / "lifecycle.json"
        workspace_root, session_name = _workspace_for_state(state_path)
        root = _maintenance_root(workspace_root, create_home=False)
        _require_inherited_lock_descriptor(workspace_lock_fd, root / "workspace.lock")
        _require_inherited_lock_descriptor(
            session_lock_fd, root / "sessions" / f"{session_name}.lock"
        )
        try:
            fcntl.flock(workspace_lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(session_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise MaintenanceError(
                "inherited maintenance lock lease is not held"
            ) from error
        yield
        return
    with session_locks(session_root / "lifecycle.json"):
        yield


@contextmanager
def _session_only_lock(maintenance_root: Path, session_name: str) -> Iterator[None]:
    descriptor = _open_lock(maintenance_root / "sessions" / f"{session_name}.lock")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _stable_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _read_json(
    path: Path, *, max_bytes: int = MAX_PRIVATE_JSON_BYTES
) -> dict[str, object]:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > max_bytes
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not _owner_ok(metadata)
    ):
        raise MaintenanceError(f"unsafe private maintenance file: {path.name}")
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        raw = b""
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            raw += chunk
            if len(raw) > max_bytes:
                raise MaintenanceError(
                    f"private maintenance file is too large: {path.name}"
                )
        after = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise MaintenanceError(f"private maintenance file changed: {path.name}")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MaintenanceError(
            f"invalid private maintenance JSON: {path.name}"
        ) from error
    if not isinstance(value, dict):
        raise MaintenanceError(f"invalid private maintenance object: {path.name}")
    return value


def _write_json(path: Path, value: dict[str, object]) -> None:
    _safe_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        raw = _stable_json(value)
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short private maintenance write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        directory_flags = os.O_RDONLY | (
            os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0
        )
        directory = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def touch_activity(state_path: Path, now_ns: int | None = None) -> None:
    """Refresh non-authoritative activity without changing lifecycle schema."""

    now = time.time_ns() if now_ns is None else now_ns
    with session_locks(state_path):
        activity = state_path.parent / "activity.json"
        try:
            current = _read_json(activity)
        except FileNotFoundError:
            current = {}
        if current and (
            set(current) != {"schema", "last_seen_ns"}
            or current.get("schema") != ACTIVITY_SCHEMA
            or type(current.get("last_seen_ns")) is not int
        ):
            raise MaintenanceError("session activity metadata is unsafe")
        _write_json(
            activity,
            {"schema": ACTIVITY_SCHEMA, "last_seen_ns": now},
        )


def _allocated_bytes(root: Path, deadline_ns: int | None = None) -> tuple[int, bool]:
    seen: set[tuple[int, int]] = set()

    def visit(parent: int, name: str, root_dev: int) -> int:
        _check_deadline(deadline_ns)
        metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
        identity = (metadata.st_dev, metadata.st_ino)
        if metadata.st_dev != root_dev or stat.S_ISLNK(metadata.st_mode):
            raise MaintenanceError("session contains a link or device crossing")
        if identity in seen:
            return 0
        seen.add(identity)
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1 or not _owner_ok(metadata):
                raise MaintenanceError("session contains unsafe authoritative evidence")
            return int(getattr(metadata, "st_blocks", 0)) * 512
        if not stat.S_ISDIR(metadata.st_mode) or not _owner_ok(metadata):
            raise MaintenanceError("session contains an unsupported filesystem object")
        total = int(getattr(metadata, "st_blocks", 0)) * 512
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(name, flags, dir_fd=parent)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != identity:
                raise MaintenanceError("session directory identity changed")
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    _check_deadline(deadline_ns)
                    total += visit(descriptor, entry.name, root_dev)
        finally:
            os.close(descriptor)
        return total

    try:
        parent_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            parent_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            parent_flags |= os.O_NOFOLLOW
        parent = os.open(root.parent, parent_flags)
        try:
            root_metadata = os.stat(root.name, dir_fd=parent, follow_symlinks=False)
            return visit(parent, root.name, root_metadata.st_dev), True
        finally:
            os.close(parent)
    except MaintenanceDeadline:
        raise
    except (FileNotFoundError, OSError, MaintenanceError):
        return 0, False


def _activity_age(session_root: Path, now_ns: int) -> tuple[int | None, str]:
    path = session_root / "activity.json"
    try:
        value = _read_json(path)
    except FileNotFoundError:
        _write_json(path, {"schema": ACTIVITY_SCHEMA, "last_seen_ns": now_ns})
        return None, "activity-initialized"
    except (OSError, MaintenanceError):
        return None, "unsafe-activity"
    last_seen = value.get("last_seen_ns")
    if (
        set(value) != {"schema", "last_seen_ns"}
        or value.get("schema") != ACTIVITY_SCHEMA
        or type(last_seen) is not int
        or int(last_seen) < 0
        or int(last_seen) > now_ns + FUTURE_TOLERANCE_NS
    ):
        return None, "unsafe-activity"
    return max(0, now_ns - int(last_seen)), "ok"


def _lifecycle_phase(session_root: Path) -> tuple[str | None, str]:
    try:
        state = _read_json(session_root / "lifecycle.json", max_bytes=64 * 1024)
    except (FileNotFoundError, OSError, MaintenanceError):
        return None, "unsafe-lifecycle"
    phase = state.get("phase")
    digest_fields = (
        "receipt_sha256",
        "requirements_sha256",
        "design_sha256",
        "rules_sha256",
        "project_instructions_state_sha256",
    )
    planned = state.get("planned_write_epoch")
    reload_required = state.get("project_instructions_reload_required")
    if (
        set(state) != LIFECYCLE_KEYS
        or state.get("schema") != LIFECYCLE_SCHEMA
        or phase not in TERMINAL_PHASES | EXPIRABLE_PHASES | PROTECTED_PHASES
        or not isinstance(state.get("project_scope"), str)
        or re.fullmatch(r"[0-9a-f]{40,64}", str(state.get("git_head_at_prompt")))
        is None
        or re.fullmatch(r"[0-9a-f]{64}", str(state.get("turn_sha256"))) is None
        or any(
            item is not None and re.fullmatch(r"[0-9a-f]{64}", str(item)) is None
            for item in (state.get(field) for field in digest_fields)
        )
        or type(state.get("write_epoch")) is not int
        or int(state["write_epoch"]) < 0
        or (planned is not None and (type(planned) is not int or int(planned) < 0))
        or state.get("rules_path") not in {None, LIFECYCLE_RULES_PATH}
        or (state.get("rules_path") is None) != (state.get("rules_sha256") is None)
        or (
            phase in {"planned", "implementation-open", "seal-armed", "sealed"}
            and state.get("rules_path") != LIFECYCLE_RULES_PATH
        )
        or (reload_required is not None and type(reload_required) is not bool)
        or state.get("waiver") not in {None, *WAIVER_KINDS}
        or (phase == "waived") != (state.get("waiver") is not None)
        or (
            phase in {"planned", "implementation-open", "seal-armed"}
            and (
                state.get("receipt_sha256") is None
                or planned != state.get("write_epoch")
            )
        )
        or (
            phase == "sealed"
            and (
                state.get("receipt_sha256") is None
                or state.get("requirements_sha256") is None
                or state.get("design_sha256") is None
                or state.get("project_instructions_state_sha256") is None
                or type(reload_required) is not bool
                or planned != state.get("write_epoch")
            )
        )
    ):
        return None, "unsafe-lifecycle"
    return str(phase), "ok"


def _pressure_order(workspace_root: Path, name: str) -> tuple[int, int, str]:
    """Order one bounded scan slice by phase class and oldest activity."""

    session_root = workspace_root / name
    phase, _reason = _lifecycle_phase(session_root)
    priority = 0 if phase in TERMINAL_PHASES else 1 if phase in EXPIRABLE_PHASES else 2
    try:
        activity = _read_json(session_root / "activity.json")
        last_seen = activity.get("last_seen_ns")
        if (
            activity.get("schema") != ACTIVITY_SCHEMA
            or type(last_seen) is not int
            or int(last_seen) < 0
        ):
            last_seen = 2**63 - 1
    except (FileNotFoundError, OSError, MaintenanceError):
        last_seen = 2**63 - 1
    return priority, int(last_seen), name


def _delete_tree_at(
    parent: int, name: str, root_dev: int, deadline_ns: int | None = None
) -> None:
    _check_deadline(deadline_ns)
    metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (
        metadata.st_dev != root_dev
        or stat.S_ISLNK(metadata.st_mode)
        or not _owner_ok(metadata)
    ):
        raise MaintenanceError("staged cleanup crossed a protected filesystem boundary")
    if stat.S_ISREG(metadata.st_mode):
        if metadata.st_nlink != 1:
            raise MaintenanceError("staged cleanup found hard-linked evidence")
        os.unlink(name, dir_fd=parent)
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise MaintenanceError("staged cleanup found an unsupported filesystem object")
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(name, flags, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        if opened.st_dev != root_dev or (opened.st_dev, opened.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            raise MaintenanceError("staged cleanup identity changed")
        with os.scandir(descriptor) as entries:
            for entry in entries:
                _check_deadline(deadline_ns)
                if entry.name in {".", ".."}:
                    raise MaintenanceError("staged cleanup entry is unsafe")
                _delete_tree_at(descriptor, entry.name, root_dev, deadline_ns)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent)


def _ownership_registry_snapshot(workspace_root: Path) -> tuple[int, str] | None:
    registry_root = workspace_root / "project-agent-ownership"
    try:
        _safe_directory(registry_root, create=False)
        registry = _read_json(registry_root / "registry.json")
    except (FileNotFoundError, OSError, MaintenanceError):
        return None
    generation = registry.get("generation")
    entries = registry.get("entries")
    if (
        set(registry) != {"schema", "generation", "entries"}
        or registry.get("schema") != OWNERSHIP_REGISTRY_SCHEMA
        or type(generation) is not int
        or int(generation) < 0
        or not isinstance(entries, dict)
    ):
        return None
    return int(generation), hashlib.sha256(_canonical_json(registry)).hexdigest()


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | (os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_staged_candidate(
    workspace_root: Path,
    maintenance_root: Path,
    journal_path: Path,
    staged: Path,
    session: str,
) -> bool:
    destination = workspace_root / session
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        return False
    os.rename(staged, destination)
    _fsync_directory(workspace_root)
    _fsync_directory(maintenance_root / "staging")
    journal_path.unlink()
    _fsync_directory(maintenance_root / "journals")
    return True


def _resume_journals(
    workspace_root: Path,
    maintenance_root: Path,
    cursor_after: str | None,
    deadline_ns: int | None,
) -> tuple[int, int, str | None]:
    deleted_bundles = 0
    deleted_bytes = 0
    journal_paths: list[Path] = []
    try:
        for path in (maintenance_root / "journals").iterdir():
            _check_deadline(deadline_ns)
            journal_paths.append(path)
    except MaintenanceDeadline:
        return 0, 0, cursor_after
    journal_paths.sort()
    ordered = [
        path
        for path in journal_paths
        if cursor_after is None or path.name > cursor_after
    ] + [
        path
        for path in journal_paths
        if cursor_after is not None and path.name <= cursor_after
    ]
    next_cursor = cursor_after
    for journal_path in ordered[:MAX_VISITS]:
        previous_cursor = next_cursor
        next_cursor = journal_path.name
        try:
            _check_deadline(deadline_ns)
            journal = _read_json(journal_path)
            session = journal.get("session")
            stage = journal.get("stage")
            device = journal.get("device")
            inode = journal.get("inode")
            allocated = journal.get("allocated_bytes")
            generation = journal.get("registry_generation")
            registry_sha256 = journal.get("registry_sha256")
            if (
                journal.get("schema") != JOURNAL_SCHEMA
                or set(journal)
                != {
                    "schema",
                    "session",
                    "stage",
                    "device",
                    "inode",
                    "allocated_bytes",
                    "disposition",
                    "registry_generation",
                    "registry_sha256",
                }
                or not isinstance(session, str)
                or re.fullmatch(r"[A-Za-z0-9._-]{1,200}", session) is None
                or session in {".", ".."}
                or not isinstance(stage, str)
                or re.fullmatch(rf"{re.escape(session)}\.[0-9a-f]{{24}}", stage) is None
                or journal_path.name != f"{stage}.json"
                or type(device) is not int
                or int(device) < 0
                or type(inode) is not int
                or int(inode) <= 0
                or type(allocated) is not int
                or int(allocated) < 0
                or journal.get("disposition") != "deletable"
                or (
                    generation is not None
                    and (type(generation) is not int or int(generation) < 0)
                )
                or (generation is None) is not (registry_sha256 is None)
                or (
                    registry_sha256 is not None
                    and (
                        not isinstance(registry_sha256, str)
                        or re.fullmatch(r"[0-9a-f]{64}", registry_sha256) is None
                    )
                )
            ):
                continue
            staged = maintenance_root / "staging" / stage
            metadata = staged.lstat()
            if (metadata.st_dev, metadata.st_ino) != (
                int(device),
                int(inode),
            ):
                continue
            if generation is not None and _ownership_registry_snapshot(
                workspace_root
            ) != (int(generation), str(registry_sha256)):
                _restore_staged_candidate(
                    workspace_root,
                    maintenance_root,
                    journal_path,
                    staged,
                    session,
                )
                continue
            staging_flags = os.O_RDONLY | (
                os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0
            )
            staging = os.open(maintenance_root / "staging", staging_flags)
            try:
                _delete_tree_at(staging, staged.name, metadata.st_dev, deadline_ns)
                os.fsync(staging)
            finally:
                os.close(staging)
            journal_path.unlink()
            _fsync_directory(maintenance_root / "journals")
            deleted_bundles += 1
            deleted_bytes += int(allocated)
        except MaintenanceDeadline:
            next_cursor = previous_cursor
            break
        except (FileNotFoundError, OSError, TypeError, ValueError, MaintenanceError):
            continue
    return deleted_bundles, deleted_bytes, next_cursor if journal_paths else None


def _stage_candidate(
    session_root: Path,
    maintenance_root: Path,
    *,
    allocated_bytes: int,
    disposition: str,
    registry_generation: int | None,
    registry_sha256: str | None,
) -> None:
    metadata = session_root.lstat()
    stage_name = f"{session_root.name}.{secrets.token_hex(12)}"
    journal_path = maintenance_root / "journals" / f"{stage_name}.json"
    journal: dict[str, object] = {
        "schema": JOURNAL_SCHEMA,
        "session": session_root.name,
        "stage": stage_name,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "allocated_bytes": allocated_bytes,
        "disposition": disposition,
        "registry_generation": registry_generation,
        "registry_sha256": registry_sha256,
    }
    _write_json(journal_path, journal)
    staged = maintenance_root / "staging" / stage_name
    if staged.parent.lstat().st_dev != metadata.st_dev:
        journal_path.unlink()
        raise MaintenanceError("cleanup staging is not on the workspace device")
    os.rename(session_root, staged)
    for directory_path in (session_root.parent, staged.parent):
        _fsync_directory(directory_path)


def _default_status() -> dict[str, object]:
    return {
        "schema": MAINTENANCE_SCHEMA,
        "cursor_after": None,
        "journal_cursor_after": None,
        "scan_allocated_bytes": 0,
        "last_completed_scan_ns": None,
        "allocated_bytes": 0,
        "aggregate_allocated_bytes": 0,
        "aggregate_complete": False,
        "pressure": False,
        "deleted_bytes": 0,
        "deleted_bundles": 0,
        "protected": {},
        "lock_contended": False,
        "last_error": None,
    }


def _load_status(path: Path) -> dict[str, object]:
    try:
        value = _read_json(path)
    except FileNotFoundError:
        return _default_status()
    expected = set(_default_status())
    cursor_after = value.get("cursor_after")
    journal_cursor_after = value.get("journal_cursor_after")
    last_completed_scan_ns = value.get("last_completed_scan_ns")
    protected = value.get("protected")
    last_error = value.get("last_error")
    if (
        value.get("schema") != MAINTENANCE_SCHEMA
        or set(value) != expected
        or (
            cursor_after is not None
            and (
                not isinstance(cursor_after, str)
                or re.fullmatch(r"[A-Za-z0-9._-]{1,200}", cursor_after) is None
                or cursor_after in STRUCTURAL_NAMES | {".", ".."}
            )
        )
        or (
            journal_cursor_after is not None
            and (
                not isinstance(journal_cursor_after, str)
                or re.fullmatch(r"[A-Za-z0-9._-]{1,300}", journal_cursor_after) is None
            )
        )
        or any(
            type(value.get(field)) is not int or int(value[field]) < 0
            for field in (
                "scan_allocated_bytes",
                "allocated_bytes",
                "aggregate_allocated_bytes",
                "deleted_bytes",
                "deleted_bundles",
            )
        )
        or (
            last_completed_scan_ns is not None
            and (
                type(last_completed_scan_ns) is not int
                or int(last_completed_scan_ns) < 0
            )
        )
        or type(value.get("pressure")) is not bool
        or type(value.get("aggregate_complete")) is not bool
        or type(value.get("lock_contended")) is not bool
        or not isinstance(protected, dict)
        or len(protected) > 64
        or any(
            not isinstance(key, str)
            or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key) is None
            or type(count) is not int
            or int(count) < 0
            for key, count in protected.items()
        )
        or (
            last_error is not None
            and (
                not isinstance(last_error, str)
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", last_error) is None
            )
        )
    ):
        raise MaintenanceError("maintenance status is unsafe")
    return value


def _update_aggregate_usage(
    workspace_root: Path,
    allocated_bytes: int,
    now_ns: int,
    deadline_ns: int | None = None,
) -> tuple[int, bool]:
    """Publish one complete workspace scan and return usage plus completeness."""

    project_specs_root = workspace_root.parent
    aggregate_root = project_specs_root / ".maintenance"
    _safe_directory(aggregate_root)
    lock_descriptor = _open_lock(aggregate_root / "usage.lock")
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        usage_path = aggregate_root / "usage.json"
        try:
            usage = _read_json(usage_path)
        except FileNotFoundError:
            usage = {"schema": USAGE_SCHEMA, "workspaces": {}}
        workspaces = usage.get("workspaces")
        if (
            set(usage) != {"schema", "workspaces"}
            or usage.get("schema") != USAGE_SCHEMA
            or not isinstance(workspaces, dict)
        ):
            raise MaintenanceError("aggregate maintenance usage is unsafe")
        current_names: set[str] = set()
        for entry in project_specs_root.iterdir():
            _check_deadline(deadline_ns)
            metadata = entry.lstat()
            if (
                entry.name != ".maintenance"
                and not stat.S_ISLNK(metadata.st_mode)
                and stat.S_ISDIR(metadata.st_mode)
            ):
                current_names.add(entry.name)
        updated = {
            name: value
            for name, value in workspaces.items()
            if name in current_names
            and isinstance(value, dict)
            and set(value) == {"allocated_bytes", "scanned_at_ns"}
            and type(value.get("allocated_bytes")) is int
            and type(value.get("scanned_at_ns")) is int
        }
        updated[workspace_root.name] = {
            "allocated_bytes": max(0, allocated_bytes),
            "scanned_at_ns": now_ns,
        }
        _write_json(
            usage_path,
            {"schema": USAGE_SCHEMA, "workspaces": updated},
        )
        return (
            sum(int(value["allocated_bytes"]) for value in updated.values()),
            set(updated) == current_names,
        )
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def _aggregate_usage_snapshot(
    workspace_root: Path, deadline_ns: int | None = None
) -> tuple[int, bool]:
    """Read exact aggregate authority without repairing malformed state."""

    project_specs_root = workspace_root.parent
    aggregate_root = project_specs_root / ".maintenance"
    _safe_directory(aggregate_root, create=False)
    lock_descriptor = _open_lock(aggregate_root / "usage.lock")
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_SH)
        usage = _read_json(aggregate_root / "usage.json")
        workspaces = usage.get("workspaces")
        if (
            set(usage) != {"schema", "workspaces"}
            or usage.get("schema") != USAGE_SCHEMA
            or not isinstance(workspaces, dict)
        ):
            raise MaintenanceError("aggregate maintenance usage is unsafe")
        current_names: set[str] = set()
        for entry in project_specs_root.iterdir():
            _check_deadline(deadline_ns)
            metadata = entry.lstat()
            if (
                entry.name != ".maintenance"
                and not stat.S_ISLNK(metadata.st_mode)
                and stat.S_ISDIR(metadata.st_mode)
            ):
                current_names.add(entry.name)
        if any(
            not isinstance(name, str)
            or name not in current_names
            or not isinstance(value, dict)
            or set(value) != {"allocated_bytes", "scanned_at_ns"}
            or type(value.get("allocated_bytes")) is not int
            or int(value["allocated_bytes"]) < 0
            or type(value.get("scanned_at_ns")) is not int
            or int(value["scanned_at_ns"]) < 0
            for name, value in workspaces.items()
        ):
            raise MaintenanceError("aggregate maintenance usage is unsafe")
        return (
            sum(int(value["allocated_bytes"]) for value in workspaces.values()),
            set(workspaces) == current_names,
        )
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


OwnershipClassifier = Callable[[Path, Path, int], tuple[str, int | None, str | None]]


def maintain_workspace(
    state_path: Path,
    *,
    current_session: str,
    ownership_classifier: OwnershipClassifier,
    now_ns: int | None = None,
    deadline_ns: int | None = None,
) -> dict[str, object]:
    """Perform one bounded, silent maintenance slice for a workspace."""

    started_ns = time.monotonic_ns()
    deadline_ns = started_ns + MAX_RUNTIME_NS if deadline_ns is None else deadline_ns
    if started_ns >= deadline_ns:
        return _default_status()
    now = time.time_ns() if now_ns is None else now_ns
    workspace_root, _ = _workspace_for_state(state_path)
    root = _maintenance_root(workspace_root)
    status_path = root / "status.json"
    try:
        lock_context = session_locks(
            state_path, workspace_exclusive=True, blocking=False
        )
        with lock_context:
            try:
                status = _load_status(status_path)
            except MaintenanceError:
                status = _default_status()
            if time.monotonic_ns() >= deadline_ns:
                return status
            status["lock_contended"] = False
            status["last_error"] = None
            resumed_bundles, resumed_bytes, journal_cursor = _resume_journals(
                workspace_root,
                root,
                str(status["journal_cursor_after"])
                if status["journal_cursor_after"] is not None
                else None,
                deadline_ns,
            )
            status["journal_cursor_after"] = journal_cursor
            status["deleted_bundles"] = int(status["deleted_bundles"]) + resumed_bundles
            status["deleted_bytes"] = int(status["deleted_bytes"]) + resumed_bytes
            status["allocated_bytes"] = max(
                0, int(status["allocated_bytes"]) - resumed_bytes
            )
            status["aggregate_allocated_bytes"] = max(
                0, int(status["aggregate_allocated_bytes"]) - resumed_bytes
            )
            after = status.get("cursor_after")
            pressure = bool(status["pressure"]) and bool(status["aggregate_complete"])
            if pressure:
                try:
                    aggregate_total, aggregate_complete = _aggregate_usage_snapshot(
                        workspace_root, deadline_ns
                    )
                except (FileNotFoundError, OSError, MaintenanceError):
                    pressure = False
                else:
                    pressure = aggregate_complete and aggregate_total == int(
                        status["aggregate_allocated_bytes"]
                    )
                if not pressure:
                    status["pressure"] = False
                    status["aggregate_complete"] = False
            names: list[str] = []
            try:
                for entry in workspace_root.iterdir():
                    _check_deadline(deadline_ns)
                    if entry.name not in STRUCTURAL_NAMES and (
                        pressure or after is None or entry.name > str(after)
                    ):
                        names.append(entry.name)
            except MaintenanceDeadline:
                status["protected"] = {"deadline": 1}
                _write_json(status_path, status)
                return status
            names.sort()
            if pressure:
                ordered: list[tuple[tuple[int, int, str], str]] = []
                for name in names:
                    if time.monotonic_ns() >= deadline_ns:
                        pressure = False
                        break
                    ordered.append((_pressure_order(workspace_root, name), name))
                ordered.sort()
                bounded_names = [name for _order, name in ordered[:MAX_VISITS]]
                cursor_boundary = None
            else:
                bounded_names = names[:MAX_VISITS]
                cursor_boundary = bounded_names[-1] if bounded_names else None
            prior_scan_total = int(status["scan_allocated_bytes"])
            scan_total = prior_scan_total
            pressure_remaining = int(status["aggregate_allocated_bytes"])
            protected: dict[str, int] = {}
            visited = 0
            last_name: str | None = None
            for name in bounded_names:
                if visited >= MAX_VISITS or time.monotonic_ns() >= deadline_ns:
                    break
                visited += 1
                last_name = name
                session_root = workspace_root / name
                try:
                    metadata = session_root.lstat()
                except FileNotFoundError:
                    continue
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_dev != workspace_root.lstat().st_dev
                    or not _owner_ok(metadata)
                ):
                    protected["unsafe-session"] = (
                        protected.get("unsafe-session", 0) + 1
                    )
                    continue
                try:
                    allocated, filesystem_safe = _allocated_bytes(
                        session_root, deadline_ns
                    )
                except MaintenanceDeadline:
                    break
                scan_total += allocated
                if name == current_session:
                    protected["current-session"] = (
                        protected.get("current-session", 0) + 1
                    )
                    continue
                if not filesystem_safe:
                    protected["unsafe-filesystem"] = (
                        protected.get("unsafe-filesystem", 0) + 1
                    )
                    continue
                phase, phase_reason = _lifecycle_phase(session_root)
                if phase is None:
                    protected[phase_reason] = protected.get(phase_reason, 0) + 1
                    continue
                age, activity_reason = _activity_age(session_root, now)
                if age is None:
                    protected[activity_reason] = protected.get(activity_reason, 0) + 1
                    continue
                eligible = (
                    phase in TERMINAL_PHASES
                    and (
                        age >= TERMINAL_RETENTION_NS
                        or (pressure and pressure_remaining > LOW_WATER_BYTES)
                    )
                ) or (phase in EXPIRABLE_PHASES and age >= UNFINISHED_RETENTION_NS)
                if not eligible or phase in PROTECTED_PHASES:
                    protected["retained"] = protected.get("retained", 0) + 1
                    continue
                remaining_ns = deadline_ns - time.monotonic_ns()
                if remaining_ns <= 0:
                    break
                with _session_only_lock(root, name):
                    disposition, generation, registry_sha256 = ownership_classifier(
                        session_root, workspace_root, remaining_ns
                    )
                    if disposition != "deletable":
                        protected[disposition] = protected.get(disposition, 0) + 1
                        continue
                    if time.monotonic_ns() >= deadline_ns:
                        protected["deadline"] = protected.get("deadline", 0) + 1
                        break
                    _stage_candidate(
                        session_root,
                        root,
                        allocated_bytes=allocated,
                        disposition=disposition,
                        registry_generation=generation,
                        registry_sha256=registry_sha256,
                    )
                    scan_total = max(0, scan_total - allocated)
                    pressure_remaining = max(0, pressure_remaining - allocated)
            slice_complete = visited == len(bounded_names)
            complete = len(names) <= MAX_VISITS and slice_complete
            if complete:
                structural_bytes = (
                    int(getattr(workspace_root.lstat(), "st_blocks", 0)) * 512
                )
                for structural_name in STRUCTURAL_NAMES:
                    structural = workspace_root / structural_name
                    try:
                        structural_allocated, structural_safe = _allocated_bytes(
                            structural, deadline_ns
                        )
                    except FileNotFoundError:
                        continue
                    except MaintenanceDeadline:
                        complete = False
                        break
                    if structural_safe:
                        structural_bytes += structural_allocated
                if not complete:
                    status["cursor_after"] = after
                    status["scan_allocated_bytes"] = prior_scan_total
                    status["protected"] = protected
                    _write_json(status_path, status)
                    return status
                scan_total += structural_bytes
                aggregate_total, aggregate_complete = _update_aggregate_usage(
                    workspace_root, max(0, scan_total), now, deadline_ns
                )
                status["cursor_after"] = None
                status["scan_allocated_bytes"] = 0
                status["allocated_bytes"] = max(0, scan_total)
                status["aggregate_allocated_bytes"] = aggregate_total
                status["aggregate_complete"] = aggregate_complete
                status["last_completed_scan_ns"] = now
                if not aggregate_complete:
                    status["pressure"] = False
                elif pressure:
                    status["pressure"] = aggregate_total > LOW_WATER_BYTES
                else:
                    status["pressure"] = aggregate_total >= HIGH_WATER_BYTES
            else:
                if pressure:
                    # Pressure ordering is not lexical. Re-evaluate the global
                    # workspace order after every bounded slice.
                    status["cursor_after"] = None
                    status["scan_allocated_bytes"] = prior_scan_total
                elif slice_complete:
                    status["cursor_after"] = cursor_boundary
                    status["scan_allocated_bytes"] = scan_total
                else:
                    status["cursor_after"] = last_name or after
                    status["scan_allocated_bytes"] = scan_total
            status["protected"] = protected
            _write_json(status_path, status)
            return status
    except BlockingIOError:
        try:
            status = _load_status(status_path)
            status["lock_contended"] = True
            return status
        except (OSError, MaintenanceError):
            return _default_status()
    except (OSError, MaintenanceError, TypeError, ValueError) as error:
        try:
            status = _load_status(status_path)
            status["last_error"] = type(error).__name__
            _write_json(status_path, status)
            return status
        except (OSError, MaintenanceError):
            return _default_status()


def maintain_project_specs_root(
    state_path: Path,
    *,
    current_session: str,
    ownership_classifier: OwnershipClassifier,
) -> dict[str, object]:
    """Rotate one bounded maintenance slice across all known workspaces."""

    deadline_ns = time.monotonic_ns() + MAX_RUNTIME_NS
    current_workspace, _ = _workspace_for_state(state_path)
    project_specs_root = current_workspace.parent
    aggregate_root = project_specs_root / ".maintenance"
    _safe_directory(project_specs_root)
    _safe_directory(aggregate_root)
    descriptor = _open_lock(aggregate_root / "scheduler.lock")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return _default_status()
    try:
        cursor_path = aggregate_root / "cursor.json"
        try:
            cursor = _read_json(cursor_path)
            cursor_after = cursor.get("workspace_after")
            if (
                set(cursor) != {"schema", "workspace_after"}
                or cursor.get("schema") != ROOT_CURSOR_SCHEMA
                or (
                    cursor_after is not None
                    and (
                        not isinstance(cursor_after, str)
                        or re.fullmatch(r"[A-Za-z0-9._-]{1,200}", cursor_after) is None
                    )
                )
            ):
                raise MaintenanceError("root maintenance cursor is unsafe")
        except (FileNotFoundError, MaintenanceError):
            cursor_after = None
        workspaces: list[Path] = []
        try:
            for entry in project_specs_root.iterdir():
                _check_deadline(deadline_ns)
                metadata = entry.lstat()
                if (
                    entry.name != ".maintenance"
                    and not stat.S_ISLNK(metadata.st_mode)
                    and stat.S_ISDIR(metadata.st_mode)
                    and _owner_ok(metadata)
                    and not metadata.st_mode & 0o077
                ):
                    workspaces.append(entry)
        except MaintenanceDeadline:
            return _default_status()
        workspaces.sort()
        if not workspaces:
            return _default_status()
        selected = next(
            (
                workspace
                for workspace in workspaces
                if cursor_after is None or workspace.name > str(cursor_after)
            ),
            workspaces[0],
        )
        if selected == current_workspace:
            selected_state = state_path
            selected_current = current_session
        else:
            selected_state = selected / "__maintenance_scan__" / "lifecycle.json"
            selected_current = "__no_current_session__"
        status = maintain_workspace(
            selected_state,
            current_session=selected_current,
            ownership_classifier=ownership_classifier,
            deadline_ns=deadline_ns,
        )
        _write_json(
            cursor_path,
            {
                "schema": ROOT_CURSOR_SCHEMA,
                "workspace_after": selected.name,
            },
        )
        return status
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def journal_digest(path: Path) -> str:
    """Return a bounded diagnostic digest without exposing private contents."""

    return hashlib.sha256(_stable_json(_read_json(path))).hexdigest()
