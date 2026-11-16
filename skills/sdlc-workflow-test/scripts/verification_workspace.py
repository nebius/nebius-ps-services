#!/usr/bin/env python3
"""Own fixture roots and aggregate teardown; never perform SDLC transitions."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
from functools import wraps
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import uuid

if __name__ == "__main__":
    # Lifecycle callbacks must share this process's lock registry and error type.
    sys.modules["verification_workspace"] = sys.modules[__name__]

ROOT_MARKER = ".sdlc-workflow-test-root.json"
ROOT_SCHEMA = "agentic-sdlc/verification-root-v1"
FIXTURE_MARKER = ".sdlc-workflow-test-fixture.json"
FIXTURE_CONTENT = '{"schema":"agentic-sdlc/disposable-fixture-v2"}\n'
RECEIPT = ".workspace-fixture.json"
JOURNAL = ".workspace-cleanup.json"
ARCHIVE = ".workspace-archive"
LOCK = ".workspace.lock"
NATIVE = ".workspace-native.json"
CORE = (
    "disposable-project",
    "origin.git",
    ".owned-git-origin.json",
    ".sdlc-git-environment",
    "verification-context.json",
)
AUXILIARY = (
    "fixture-agent-home",
    "hook-fixture-agent-home",
    "empty-fixture-agent-home",
    "fixture-plugin-data",
    "evidence",
)
_guard = threading.RLock()
_held: dict[tuple[int, int, str], int] = {}


class WorkspaceError(ValueError):
    """An ownership or quiescence condition prevents fixture mutation."""


def _identity(path: Path) -> list[int]:
    info = path.lstat()
    if info.st_uid != os.getuid() or stat.S_ISLNK(info.st_mode):
        raise WorkspaceError("Workspace path has foreign ownership or a symlink.")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise WorkspaceError("Workspace path has an unsupported file type.")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise WorkspaceError("Workspace file has multiple links.")
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]


def _canonical(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path.resolve(strict=False) != path:
        raise WorkspaceError("Workspace paths must be canonical without symlinks.")
    return path


def _open_parent(path: Path):
    path = _canonical(path)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read(path: Path, *, maximum=16 * 1024 * 1024, private=False) -> bytes:
    parent = _open_parent(path)
    descriptor = None
    try:
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_size > maximum
            or private
            and before.st_mode & 0o077
        ):
            raise WorkspaceError(
                "Workspace file has unsafe type, ownership, permissions or size."
            )

        def signature(info):
            return (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        after = os.fstat(descriptor)
        bound = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (
            len(data) > maximum
            or signature(before) != signature(after)
            or signature(after) != signature(bound)
        ):
            raise WorkspaceError("Workspace file changed while reading.")
        return data
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _json(path: Path) -> dict:
    try:
        value = json.loads(_read(path, maximum=1048576, private=True))
    except (OSError, ValueError) as error:
        raise WorkspaceError("Workspace record is invalid or unsafe.") from error
    if not isinstance(value, dict):
        raise WorkspaceError("Workspace record must be an object.")
    return value


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write(path: Path, value: dict, *, exclusive=False) -> None:
    _canonical(path)
    if path.exists():
        _json(path)
        if exclusive:
            raise WorkspaceError("Immutable workspace record already exists.")
    fd, name = tempfile.mkstemp(prefix=".workspace-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
            temporary.unlink()
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def admit_root(
    value: Path,
    *,
    create=False,
    source_root: Path | None = None,
    host_home: Path | None = None,
) -> Path:
    root = _canonical(value)
    source_root = (source_root or Path(__file__).resolve().parents[3]).resolve()
    if root in {
        Path(root.anchor),
        Path.home(),
        Path.cwd(),
        host_home,
        source_root,
    } or root.is_relative_to(source_root):
        raise WorkspaceError(
            "Verification root must be a dedicated directory outside source."
        )
    if not root.exists():
        if not create:
            raise FileNotFoundError(root)
        root.mkdir(parents=True, mode=0o700)
        _write(root / ROOT_MARKER, {"schema": ROOT_SCHEMA}, exclusive=True)
    _identity(root)
    if not root.is_dir() or root.stat().st_mode & 0o077:
        raise WorkspaceError("Verification root must be a private directory.")
    if _json(root / ROOT_MARKER) != {"schema": ROOT_SCHEMA}:
        raise WorkspaceError("Verification-root ownership marker is invalid.")
    return root


@contextmanager
def operation(value: Path, *, create=False):
    """Lock order: workspace, live lifecycle, then product/Git ownership locks."""
    root = admit_root(value, create=create)
    key = (os.getpid(), threading.get_ident(), str(root))
    with _guard:
        nested = key in _held
        if nested:
            _held[key] += 1
        else:
            path = root / LOCK
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                _identity(path)
                if os.fstat(descriptor).st_mode & 0o077:
                    raise WorkspaceError("Workspace lock must be private.")
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise WorkspaceError(
                    "WORKSPACE_BUSY: another owned operation is active."
                ) from None
            _held[key] = 1
    try:
        yield root
    finally:
        with _guard:
            _held[key] -= 1
            if not _held[key]:
                del _held[key]
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


def serialized(function):
    @wraps(function)
    def wrapped(value, *args, **kwargs):
        with operation(value) as root:
            if (root / JOURNAL).exists() and not (root / RECEIPT).exists():
                raise WorkspaceError(
                    "Fixture generation ended; stale writers cannot recreate evidence."
                )
            return function(value, *args, **kwargs)

    return wrapped


def fixture_setup(function):
    @wraps(function)
    def wrapped(ctx):
        try:
            with operation(ctx.verification_root):
                _report_allowed(
                    ctx.verification_root,
                    ctx.report_path or ctx.verification_root / "report.md",
                )
                fresh = not (ctx.disposable_project / ".git").exists()
                if (ctx.verification_root / RECEIPT).exists():
                    _validate_receipt(ctx.verification_root, _json(ctx.verification_root / RECEIPT))
                if function(ctx):
                    register_fixture(
                        ctx.verification_root,
                        fresh=fresh,
                        report=ctx.report_path or ctx.verification_root / "report.md",
                    )
                    return True
                return False
        except (WorkspaceError, OSError) as error:
            ctx.add(
                "Disposable SDLC golden-path run results",
                "Workspace fixture admission",
                "FAIL",
                str(error),
            )
            return False

    return wrapped


def _report_allowed(root: Path, report: Path) -> str:
    report = _canonical(report)
    if not report.is_relative_to(root) or report == root:
        raise WorkspaceError("Report must remain under its owned root.")
    relative = report.relative_to(root)
    if relative.parts[0] in {
        *CORE,
        *AUXILIARY,
        RECEIPT,
        JOURNAL,
        NATIVE,
        LOCK,
        ROOT_MARKER,
        ARCHIVE,
        "live-results.json",
        "three-tier-live",
    }:
        raise WorkspaceError("Report overlaps fixture cleanup or ownership state.")
    return str(relative)


def register_fixture(root: Path, *, fresh: bool, report: Path) -> None:
    """Seal exact creation authority before any auxiliary fixture writes."""
    with operation(root):
        report_relative = _report_allowed(root, report)
        if (root / JOURNAL).exists() and _json(root / JOURNAL).get(
            "status"
        ) != "DESTROYED":
            raise WorkspaceError(
                "Interrupted workspace cleanup must finish before fixture setup."
            )
        if (root / RECEIPT).exists():
            receipt = _json(root / RECEIPT)
            _validate_receipt(root, receipt)
            if receipt["report"] != report_relative:
                raise WorkspaceError(
                    "Fixture report identity changed; use a fresh verification root."
                )
            return
        if not fresh:
            raise WorkspaceError(
                "Existing fixture has no workspace generation receipt; use a fresh root."
            )
        context = _json(root / "verification-context.json")
        manifest = _manifest_path(root, context, report_relative)
        if manifest.exists():
            raise WorkspaceError("Unowned live manifest prevents fixture registration.")
        targets = {}
        for name in CORE:
            targets[name] = _identity(root / name)
        for name in AUXILIARY:
            path = root / name
            if path.exists() or path.is_symlink():
                raise WorkspaceError(
                    "Unowned auxiliary fixture path prevents registration."
                )
        for name in AUXILIARY:
            path = root / name
            path.mkdir(mode=0o700)
            targets[name] = _identity(path)
        _write(
            root / RECEIPT,
            {
                "schema": "agentic-sdlc/workspace-fixture-v1",
                "generation": uuid.uuid4().hex,
                "root": _identity(root),
                "targets": targets,
                "report": report_relative,
                "manifest": str(manifest.relative_to(root)),
                "context_sha256": hashlib.sha256(
                    _read(root / "verification-context.json", private=True)
                ).hexdigest(),
            },
            exclusive=True,
        )


def _validate_receipt_header(root: Path, receipt: dict) -> None:
    if (
        set(receipt)
        != {
            "schema",
            "generation",
            "root",
            "targets",
            "report",
            "manifest",
            "context_sha256",
        }
        or receipt["schema"] != "agentic-sdlc/workspace-fixture-v1"
        or receipt["root"] != _identity(root)
        or not isinstance(receipt["generation"], str)
        or len(receipt["generation"]) != 32
        or set(receipt["targets"]) != {*CORE, *AUXILIARY}
    ):
        raise WorkspaceError("Workspace fixture receipt is invalid.")
    _report_allowed(root, root / receipt["report"])


def _validate_receipt(root: Path, receipt: dict) -> None:
    _validate_receipt_header(root, receipt)
    context = _json(root / "verification-context.json")
    if (
        str(_manifest_path(root, context, receipt["report"]).relative_to(root))
        != receipt["manifest"]
    ):
        raise WorkspaceError("Fixture manifest path changed.")
    for name, identity in receipt["targets"].items():
        if _identity(root / name) != identity:
            raise WorkspaceError("Workspace fixture path was replaced.")
    if (
        hashlib.sha256(
            _read(root / "verification-context.json", private=True)
        ).hexdigest()
        != receipt["context_sha256"]
    ):
        raise WorkspaceError("Workspace verification context changed.")


def restore_context(value: Path, original_path: Path) -> dict:
    """Restore a proven original context; never adopt a new fixture generation."""
    with operation(value) as root:
        _canonical(root / NATIVE)
        require_native_absent(root)
        active = _canonical(root / "three-tier-live/active.json")
        runs = _canonical(root / "three-tier-live/runs")
        if active.exists() or active.is_symlink() or runs.is_symlink() or (runs.exists() and any(runs.iterdir())):
            raise WorkspaceError("Context restoration requires no live fixture.")
        journal = _canonical(root / JOURNAL)
        if journal.exists() and _json(journal).get("status") != "DESTROYED":
            raise WorkspaceError("Context restoration cannot alter an active cleanup checkpoint.")
        original_path = _canonical(original_path)
        archive = root / ARCHIVE
        if original_path.parent != archive:
            raise WorkspaceError("Original context evidence must be directly under the owned archive.")
        _identity(archive)
        receipt_path = root / RECEIPT
        receipt_raw = _read(receipt_path, private=True)
        receipt = _json(receipt_path)
        _validate_receipt_header(root, receipt)
        if re.fullmatch(r"[0-9a-f]{32}", receipt["generation"]) is None:
            raise WorkspaceError("Fixture generation is invalid.")
        for name, identity in receipt["targets"].items():
            if name != "verification-context.json" and _identity(root / name) != identity:
                raise WorkspaceError("Workspace fixture path was replaced.")
        original_raw = _read(original_path, private=True)
        if hashlib.sha256(original_raw).hexdigest() != receipt["context_sha256"]:
            raise WorkspaceError("Original context does not match its recorded hash.")
        original = _json(original_path)
        if original_raw != (json.dumps(original, indent=2, sort_keys=True) + "\n").encode():
            raise WorkspaceError("Original context encoding is not canonical.")
        context_path = root / "verification-context.json"
        current_raw = _read(context_path, private=True)
        current = _json(context_path)
        keys = {"schema", "verification_id", "project_root", "git_root", "baseline_head", "live_results"}
        if (set(original) != keys or set(current) != keys
                or any(current[key] != original[key] for key in keys - {"verification_id"})
                or any(not isinstance(row["verification_id"], str)
                       or re.fullmatch(r"[0-9a-f]{64}", row["verification_id"]) is None
                       for row in (original, current))):
            raise WorkspaceError("Context replacement changed the frozen fixture scope.")
        manifest = _manifest_path(root, original, receipt["report"])
        if str(manifest.relative_to(root)) != receipt["manifest"] or manifest.exists() or manifest.is_symlink():
            raise WorkspaceError("Context restoration requires no live evidence manifest.")
        import owned_git_origin

        try:
            owned_git_origin.validate(root / "disposable-project", root, "lightweight", require_clean=True)
        except owned_git_origin.OriginError as error:
            raise WorkspaceError(str(error)) from error
        if _read(root / "disposable-project" / FIXTURE_MARKER).decode() != FIXTURE_CONTENT:
            raise WorkspaceError("Lightweight public fixture marker changed.")
        if current_raw == original_raw and _identity(context_path) == receipt["targets"]["verification-context.json"]:
            _validate_receipt(root, receipt)
            return {"status": "ALREADY_RESTORED", "generation": receipt["generation"]}
        audit_path = archive / ("context-restoration-" + receipt["generation"] + ".json")
        if audit_path.exists():
            audit = _json(audit_path)
            if (set(audit) != {"schema", "receipt", "superseded_context", "superseded_identity"}
                    or audit["schema"] != "agentic-sdlc/context-restoration-v1"
                    or audit["receipt"] != receipt
                    or current not in (original, audit["superseded_context"])
                    or current != original and _identity(context_path) != audit["superseded_identity"]):
                raise WorkspaceError("Context restoration evidence changed.")
        else:
            _write(audit_path, {"schema": "agentic-sdlc/context-restoration-v1", "receipt": receipt,
                               "superseded_context": current, "superseded_identity": _identity(context_path)}, exclusive=True)
        if _read(receipt_path, private=True) != receipt_raw or _read(context_path, private=True) != current_raw:
            raise WorkspaceError("Context restoration target changed before writing.")
        if current_raw != original_raw:
            _write(context_path, original)
        restored = {**receipt, "targets": {**receipt["targets"], "verification-context.json": _identity(context_path)}}
        _write(receipt_path, restored)
        _validate_receipt(root, restored)
        return {"status": "RESTORED", "generation": receipt["generation"], "evidence": str(audit_path.relative_to(root))}


def _manifest_path(root: Path, context: dict, report: str) -> Path:
    value = context.get("live_results")
    if not isinstance(value, str):
        raise WorkspaceError("Fixture manifest identity is missing.")
    path = _canonical(Path(value))
    if not path.is_relative_to(root) or path == root or path == root / report:
        raise WorkspaceError("Fixture manifest overlaps report or escapes its root.")
    relative = path.relative_to(root)
    if relative.parts[0] in {
        *CORE,
        *AUXILIARY[:-1],
        ROOT_MARKER,
        RECEIPT,
        JOURNAL,
        LOCK,
        NATIVE,
        ARCHIVE,
        "three-tier-live",
    }:
        raise WorkspaceError(
            "Fixture manifest overlaps ownership or unrelated fixture state."
        )
    return path


def _validate_checkpoint(root: Path, snapshot: dict, name: str) -> None:
    required = {
        "schema",
        "root",
        "generation",
        "targets",
        "entries",
        "fixture",
        "context",
        "parents",
    }
    if (
        set(snapshot) != required
        or snapshot["schema"] != "agentic-sdlc/workspace-cleanup-v1"
        or snapshot["root"] != _identity(root)
        or not isinstance(snapshot["generation"], str)
        or re.fullmatch(r"[0-9a-f]{32}", snapshot["generation"]) is None
        or name != snapshot["generation"] + ".json"
    ):
        raise WorkspaceError("Cleanup checkpoint schema or generation is invalid.")
    receipt, context = snapshot["fixture"], snapshot["context"]
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {
            "schema",
            "generation",
            "root",
            "targets",
            "report",
            "manifest",
            "context_sha256",
        }
        or receipt["schema"] != "agentic-sdlc/workspace-fixture-v1"
        or receipt["generation"] != snapshot["generation"]
        or receipt["root"] != snapshot["root"]
        or set(receipt["targets"]) != {*CORE, *AUXILIARY}
        or not isinstance(context, dict)
        or context.get("schema")
        not in {
            "agentic-sdlc/verification-context-v1",
            "agentic-sdlc/verification-context-claude-v1",
        }
        or context.get("git_root") != str(root / "disposable-project")
    ):
        raise WorkspaceError("Cleanup fixture authority is invalid.")
    _report_allowed(root, root / receipt["report"])
    manifest = _manifest_path(root, context, receipt["report"])
    if str(manifest.relative_to(root)) != receipt["manifest"]:
        raise WorkspaceError("Cleanup manifest authority changed.")
    allowed = {*CORE, *AUXILIARY, RECEIPT}
    targets = snapshot["targets"]
    if (
        not isinstance(targets, list)
        or set(targets) not in (allowed, allowed | {receipt["manifest"]})
        or len(targets) != len(set(targets))
    ):
        raise WorkspaceError("Cleanup targets exceed the admitted fixture scope.")
    entries = snapshot["entries"]
    if (
        not isinstance(entries, dict)
        or not set(targets).issubset(entries)
        or len(entries) > 4096
    ):
        raise WorkspaceError("Cleanup entries are incomplete or oversized.")
    for relative, record in entries.items():
        path = Path(relative)
        if (
            not isinstance(relative, str)
            or path.is_absolute()
            or str(path) != relative
            or any(part in {".", ".."} for part in path.parts)
            or not any(path == Path(t) or path.is_relative_to(Path(t)) for t in targets)
            or path == Path(receipt["report"])
            or not isinstance(record, dict)
            or set(record) not in ({"identity"}, {"identity", "sha256"})
        ):
            raise WorkspaceError("Cleanup entry escapes admitted scope.")
        identity = record["identity"]
        if (
            not isinstance(identity, list)
            or len(identity) != 3
            or not all(type(n) is int and n >= 0 for n in identity)
            or identity[2] not in {stat.S_IFDIR, stat.S_IFREG}
        ):
            raise WorkspaceError("Cleanup entry identity is invalid.")
        if (
            (identity[2] == stat.S_IFREG) != ("sha256" in record)
            or "sha256" in record
            and re.fullmatch(r"[0-9a-f]{64}", str(record["sha256"])) is None
        ):
            raise WorkspaceError("Cleanup entry digest is invalid.")
    expected_parents = {
        str(p)
        for name in targets
        for p in Path(name).parents
        if str(p) != "." and str(p) not in entries
    }
    parents = snapshot["parents"]
    if not isinstance(parents, dict) or set(parents) != expected_parents:
        raise WorkspaceError("Cleanup parent scope changed.")
    for relative, record in parents.items():
        identity = record.get("identity") if isinstance(record, dict) else None
        if (
            not isinstance(record, dict)
            or set(record) != {"identity"}
            or not isinstance(identity, list)
            or len(identity) != 3
            or not all(type(n) is int and n >= 0 for n in identity)
            or identity[2] != stat.S_IFDIR
            or _identity(root / relative) != identity
        ):
            raise WorkspaceError("Retained manifest parent changed.")
    for target, identity in receipt["targets"].items():
        if entries[target]["identity"] != identity:
            raise WorkspaceError("Cleanup target no longer binds the fixture receipt.")
    if manifest.exists():
        value = _json(manifest)
        if value.get(
            "schema"
        ) != "agentic-sdlc/verification-live-results-v3" or value.get(
            "verification_id"
        ) != context.get("verification_id"):
            raise WorkspaceError("Cleanup manifest is not the admitted raw evidence.")


def _tree(root: Path, names: list[str]) -> dict[str, dict]:
    result = {}

    def visit(path):
        identity = _identity(path)
        relative = str(path.relative_to(root))
        record = {"identity": identity}
        if identity[2] == stat.S_IFDIR:
            parent = _open_parent(path)
            try:
                fd = os.open(
                    path.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
                try:
                    observed = os.fstat(fd)
                    if [
                        observed.st_dev,
                        observed.st_ino,
                        stat.S_IFMT(observed.st_mode),
                    ] != identity:
                        raise WorkspaceError(
                            "Cleanup directory changed during inventory."
                        )
                    children = sorted(os.listdir(fd))
                    if len(result) + len(children) > 4096:
                        raise WorkspaceError(
                            "Lightweight cleanup inventory exceeds its bound."
                        )
                    for child in children:
                        visit(path / child)
                    if (
                        _identity(path) != identity
                        or sorted(os.listdir(fd)) != children
                    ):
                        raise WorkspaceError(
                            "Cleanup directory changed during inventory."
                        )
                finally:
                    os.close(fd)
            finally:
                os.close(parent)
        else:
            record["sha256"] = hashlib.sha256(_read(path)).hexdigest()
            if _identity(path) != identity:
                raise WorkspaceError("Cleanup file changed during inventory.")
        result[relative] = record
        if len(result) > 4096:
            raise WorkspaceError("Lightweight cleanup inventory exceeds its bound.")

    for name in names:
        visit(root / name)
    return result


def register_native(root: Path, *, pid: int | None = None) -> None:
    """Record each exact launcher before exec, under one generation owner."""
    pid = os.getpid() if pid is None else pid
    if type(pid) is not int or pid <= 1:
        raise WorkspaceError("Native coordinator process identity is invalid.")
    with operation(root):
        path = root / NATIVE
        value = _json(path) if path.exists() else {"schema": "agentic-sdlc/workspace-native-v2", "processes": []}
        _native_records(value)
        observed = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                                  capture_output=True, text=True, check=True).stdout.strip()
        if not observed:
            raise WorkspaceError("Native coordinator identity is unavailable.")
        record = {"pid": pid, "started": observed}
        if record not in value["processes"]:
            if len(value["processes"]) >= 64:
                raise WorkspaceError("Native invocation inventory exceeds its bound.")
            value["processes"].append(record)
        _write(path, value)


def _native_records(value):
    if (set(value) != {"schema", "processes"} or value["schema"] != "agentic-sdlc/workspace-native-v2"
            or not isinstance(value["processes"], list) or len(value["processes"]) > 64
            or any(not isinstance(row, dict) or set(row) != {"pid", "started"}
                   or type(row["pid"]) is not int or row["pid"] <= 1
                   or not isinstance(row["started"], str) or not row["started"] for row in value["processes"])):
        raise WorkspaceError("Native coordinator ownership is invalid.")
    if len({(row["pid"], row["started"]) for row in value["processes"]}) != len(value["processes"]):
        raise WorkspaceError("Native coordinator ownership has duplicate records.")
    return value["processes"]


def require_native_absent(root: Path) -> None:
    if not (root / NATIVE).exists():
        return
    for value in _native_records(_json(root / NATIVE)):
        result = subprocess.run(["ps", "-o", "lstart=", "-p", str(value["pid"])], capture_output=True, text=True)
        if result.returncode not in {0, 1}:
            raise WorkspaceError("Native coordinator absence cannot be measured.")
        if result.returncode == 0 and result.stdout.strip() == value["started"]:
            raise WorkspaceError("WORKSPACE_BUSY: an owned native coordinator is still active.")


def _retire_native_history(root: Path) -> bool:
    """Archive a quiescent terminal ledger before retiring its active path."""
    path = root / NATIVE
    if not path.exists() and not path.is_symlink():
        return False
    raw = _read(path, maximum=1048576, private=True)
    try:
        ledger = json.loads(raw)
    except ValueError as error:
        raise WorkspaceError("Native coordinator ownership is invalid.") from error
    if not isinstance(ledger, dict):
        raise WorkspaceError("Native coordinator ownership is invalid.")
    _native_records(ledger)
    entries = _tree(root, [NATIVE])
    if hashlib.sha256(raw).hexdigest() != entries[NATIVE]["sha256"]:
        raise WorkspaceError("Native coordinator ownership changed before retirement.")
    require_native_absent(root)
    history = {
        "schema": "agentic-sdlc/workspace-native-history-v1",
        "root": _identity(root),
        "ledger": ledger,
        "ledger_sha256": entries[NATIVE]["sha256"],
    }
    archive = _canonical(root / ARCHIVE)
    archive.mkdir(mode=0o700, exist_ok=True)
    _identity(archive)
    if not archive.is_dir() or archive.stat().st_mode & 0o077:
        raise WorkspaceError("Native history archive must be a private directory.")
    saved = archive / ("native-" + _digest(history) + ".json")
    if not saved.exists() and not saved.is_symlink():
        _write(saved, history, exclusive=True)
    if _json(saved) != history:
        raise WorkspaceError("Native history archive differs; preserve it for diagnosis.")
    require_native_absent(root)
    _delete_checked(root, NATIVE, entries[NATIVE], entries, history["root"])
    return True


def _checkpoint(root: Path) -> tuple[dict, dict]:
    journal_path = root / JOURNAL
    if journal_path.exists():
        journal = _json(journal_path)
        if journal.get("status") != "DESTROYED":
            name = journal.get("checkpoint", "")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not name.endswith(".json")
            ):
                raise WorkspaceError("Cleanup checkpoint path is invalid.")
            snapshot = _json(root / ARCHIVE / name)
            if _digest(snapshot) != journal.get("sha256") or snapshot.get(
                "root"
            ) != _identity(root):
                raise WorkspaceError("Cleanup checkpoint binding changed.")
            _validate_checkpoint(root, snapshot, name)
            return snapshot, journal
    receipt_path = root / RECEIPT
    if not receipt_path.exists():
        if any(
            (root / name).exists() or (root / name).is_symlink()
            for name in (*CORE, *AUXILIARY, "live-results.json")
        ):
            raise WorkspaceError(
                "Lightweight remnants lack a valid workspace generation receipt; preserved."
            )
        return {}, {}
    receipt = _json(receipt_path)
    _validate_receipt(root, receipt)
    import owned_git_origin

    project = root / "disposable-project"
    if _read(project / FIXTURE_MARKER).decode() != FIXTURE_CONTENT:
        raise WorkspaceError("Lightweight public fixture marker changed.")
    try:
        owned_git_origin.validate(project, root, "lightweight", require_clean=True)
    except owned_git_origin.OriginError as error:
        raise WorkspaceError(str(error)) from error
    names = [*CORE, *AUXILIARY, RECEIPT]
    context = _json(root / "verification-context.json")
    manifest = _manifest_path(root, context, receipt["report"])
    if manifest.exists() or manifest.is_symlink():
        value = _json(manifest)
        if value.get(
            "schema"
        ) != "agentic-sdlc/verification-live-results-v3" or value.get(
            "verification_id"
        ) != context.get("verification_id"):
            raise WorkspaceError("Live manifest is not bound to this fixture.")
        names.append(str(manifest.relative_to(root)))
    snapshot = {
        "schema": "agentic-sdlc/workspace-cleanup-v1",
        "root": _identity(root),
        "generation": receipt["generation"],
        "targets": names,
        "entries": _tree(root, names),
        "fixture": receipt,
        "context": context,
    }
    snapshot["parents"] = {
        str(p): {"identity": _identity(root / p)}
        for name in names
        for p in Path(name).parents
        if str(p) != "." and str(p) not in snapshot["entries"]
    }
    archive = root / ARCHIVE
    if not archive.exists():
        archive.mkdir(mode=0o700)
    _identity(archive)
    name = receipt["generation"] + ".json"
    _validate_checkpoint(root, snapshot, name)
    if (archive / name).exists():
        if _json(archive / name) != snapshot:
            raise WorkspaceError(
                "Unbound cleanup checkpoint differs; preserve it for diagnosis."
            )
    else:
        _write(archive / name, snapshot, exclusive=True)
    journal = {
        "schema": "agentic-sdlc/workspace-cleanup-journal-v1",
        "checkpoint": name,
        "sha256": _digest(snapshot),
        "status": "CLEANING",
        "removed": [],
        "live": "PENDING",
    }
    _write(journal_path, journal)
    return snapshot, journal


def _delete_checked(
    root: Path, relative: str, expected: dict, entries: dict, root_identity: list
) -> None:
    """Recheck parent identities and exact content at the no-follow unlink boundary."""
    parts = Path(relative).parts
    if (
        not parts
        or Path(relative).is_absolute()
        or any(p in {".", ".."} for p in parts)
    ):
        raise WorkspaceError("Cleanup path is invalid.")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:

        def identity(info):
            return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]

        if identity(os.fstat(descriptor)) != root_identity:
            raise WorkspaceError("Cleanup root was replaced.")
        for index, part in enumerate(parts[:-1], 1):
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
            if (
                identity(os.fstat(descriptor))
                != entries[str(Path(*parts[:index]))]["identity"]
            ):
                raise WorkspaceError("Cleanup parent was replaced.")
        info = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        if identity(info) != expected["identity"]:
            raise WorkspaceError("Cleanup target was replaced.")
        if stat.S_ISDIR(info.st_mode):
            os.rmdir(parts[-1], dir_fd=descriptor)
        else:
            fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
            try:
                before = os.fstat(fd)
                if (
                    identity(before) != expected["identity"]
                    or before.st_uid != os.getuid()
                    or before.st_nlink != 1
                    or before.st_size > 16 * 1024 * 1024
                ):
                    raise WorkspaceError("Cleanup file identity changed.")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    data = stream.read(16 * 1024 * 1024 + 1)
                after = os.fstat(fd)
                bound = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
                if (
                    len(data) > 16 * 1024 * 1024
                    or hashlib.sha256(data).hexdigest() != expected["sha256"]
                    or identity(bound) != expected["identity"]
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                ):
                    raise WorkspaceError("Cleanup file changed after admission.")
                os.unlink(parts[-1], dir_fd=descriptor)
            finally:
                os.close(fd)
    finally:
        os.close(descriptor)


@contextmanager
def product_quiescence(root: Path, live):
    """Hold the existing product transition lock through cleanup admission/effects."""
    if not live.paths(root)["active"].exists():
        yield
        return
    _, state = live.load_active(root)
    if state.get("filesystem_cleanup") is not None:
        live.validate_filesystem_cleanup(root, state)
        require_native_absent(root)
        yield
        return
    home = Path(state["private_root"]) / (live.agent_name() + "-home")
    companion = Path(state["evidence_root"]) / "companion"
    if (companion / "launch.json").exists():
        terminal = _json(companion / "finished.json")
        groups = terminal.get("process_groups")
        if (terminal.get("verification_id") != state["verification_id"]
                or terminal.get("status") not in {"PASS", "PARTIAL", "FAIL"}
                or terminal.get("cleanup_verified") is not True
                or not isinstance(groups, list) or len(groups) > 2
                or any(type(pid) is not int or pid <= 1 for pid in groups)):
            raise WorkspaceError("Companion launch has no terminal generation proof.")
        for pid in groups:
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                continue
            raise WorkspaceError("Companion native process group remains active.")
        for path in companion.glob("*-invocation.json"):
            value = _json(path)
            if value.get("cleanup_verified") is not True:
                raise WorkspaceError("Companion native cleanup is incomplete.")
            try:
                os.killpg(value["process_group"], 0)
            except ProcessLookupError:
                continue
            raise WorkspaceError("Companion native process group remains active.")
    runs = home / "sdlc-runs"
    launched = (
        bool(state.get("workflow_startup")) or runs.exists() and any(runs.iterdir())
    )
    if not launched:
        require_native_absent(root)
        yield
        return
    if not (root / NATIVE).exists():
        raise WorkspaceError(
            "Native launcher ownership is missing for a started workflow."
        )
    require_native_absent(root)
    target = live.three_tier_target
    try:
        registration = target.cleanup_registration(state, live.agent_name())
        run_dir = registration[4]
        with target.workflow_imports(Path(__file__).resolve().parents[2]):
            from workflow_state import lock
            from workflow_objectives import validate_run, _writers_quiescent
            from workflow_resources import require_released

            with lock(run_dir.parent):
                current = target.cleanup_registration(state, live.agent_name())
                if current[4] != run_dir or current[2:4] != registration[2:4]:
                    raise WorkspaceError(
                        "Product registration changed before cleanup admission."
                    )
                validate_run(run_dir)
                _writers_quiescent(run_dir)
                require_released(run_dir)
                from contextlib import ExitStack
                with ExitStack() as stack:
                    for peer in sorted((home / "implement").glob("*/run-*/run.json")):
                        peer_dir = peer.parent
                        stack.enter_context(lock(peer_dir.parent))
                        peer_run, _ = validate_run(peer_dir, owner="implement")
                        if Path(peer_run.get("logical_project_root", "")) != Path(state["project_root"]) / "project-b":
                            raise WorkspaceError("Companion objective has foreign project identity.")
                        _writers_quiescent(peer_dir)
                        require_released(peer_dir)
                    yield
    except (OSError, ValueError, RuntimeError) as error:
        if isinstance(error, WorkspaceError):
            raise
        raise WorkspaceError(
            "Product writer quiescence is unavailable; retain the owned run."
        ) from error


def destroy(value: Path) -> dict:
    requested = _canonical(value)
    if not requested.exists():
        return {
            "status": "ALREADY_DESTROYED",
            "lightweight": "ABSENT",
            "live": "ABSENT",
        }
    with operation(requested) as root:
        require_native_absent(root)
        import three_tier_lifecycle as live

        live_paths = live.paths(root)
        runs = live_paths["runs"]
        if not live_paths["active"].exists() and runs.exists() and any(runs.iterdir()):
            raise WorkspaceError(
                "Orphaned live run remains; ownership must be resolved before cleanup."
            )
        with product_quiescence(root, live):
            return _destroy_locked(root, live)


def _destroy_locked(root: Path, live) -> dict:
    snapshot, journal = _checkpoint(root)
    # Validate every remaining path before deleting any resource.
    if snapshot:
        names = [
            name
            for name in snapshot["targets"]
            if (root / name).exists() or (root / name).is_symlink()
        ]
        current = _tree(root, names)
        if any(
            relative not in snapshot["entries"]
            or record != snapshot["entries"][relative]
            for relative, record in current.items()
        ):
            raise WorkspaceError("Cleanup tree changed after checkpoint; preserved.")
    live_status, _ = live.destroy(root)
    if snapshot:
        journal["live"] = live_status
        _write(root / JOURNAL, journal)
        for relative in sorted(
            snapshot["entries"], key=lambda p: (len(Path(p).parts), p), reverse=True
        ):
            path = root / relative
            if path.exists() or path.is_symlink():
                _delete_checked(
                    root,
                    relative,
                    snapshot["entries"][relative],
                    {**snapshot["entries"], **snapshot["parents"]},
                    snapshot["root"],
                )
            if relative not in journal["removed"]:
                journal["removed"].append(relative)
            _write(root / JOURNAL, journal)
        journal["status"] = "DESTROYED"
        _write(root / JOURNAL, journal)
    retired_native = _retire_native_history(root)
    result = (
        "DESTROYED"
        if snapshot or live_status == "DESTROYED" or retired_native
        else "ALREADY_DESTROYED"
    )
    return {
        "status": result,
        "lightweight": "DESTROYED" if snapshot else "ABSENT",
        "live": live_status,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--verification-root", type=Path, required=True)
    parser.add_argument("action", choices=("destroy", "restore-context"))
    parser.add_argument("--original-context", type=Path)
    args = parser.parse_args(argv)
    if (args.action == "restore-context") != (args.original_context is not None):
        parser.error("Only restore-context requires --original-context.")
    try:
        result = (restore_context(args.verification_root, args.original_context)
                  if args.action == "restore-context" else destroy(args.verification_root))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (WorkspaceError, OSError, RuntimeError) as error:
        label = "CONTEXT_RESTORE_FAILED" if args.action == "restore-context" else "CLEANUP_FAILED"
        print(f"{label}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
