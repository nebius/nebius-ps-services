"""One-shot, journaled migration from the former spec-owner formats."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Callable

from .contracts import (
    DESIGN_SCHEMA,
    LEGACY_SDLC_DESIGN_SCHEMA,
    LEGACY_SDLC_REQUIREMENTS_SCHEMA,
    ProjectSpecError,
    REQUIREMENTS_SCHEMA,
    digest,
    legacy_task_markers,
    project_identity,
    spec_markers,
    stable_json,
    _read_file,
)
from .lifecycle import _outside_git, _secure_directory, codex_home


LOCK_NAME = ".migration.lock"
TRANSACTION_NAME = "transaction"
STAGING_PREFIX = ".transaction."
CLEANUP_PREFIX = ".cleanup."
JOURNAL_NAME = "journal.json"
BACKUP_NAMES = {
    "requirements": "requirements.md.backup",
    "design": "design.md.backup",
}


def _migration_root(project: Path, git_root: Path) -> Path:
    workspace = (
        f"{git_root.name}-{hashlib.sha256(str(git_root).encode()).hexdigest()[:12]}"
    )
    scope = hashlib.sha256(str(project).encode()).hexdigest()[:16]
    root = codex_home() / "project-specs" / workspace / "migrations" / scope
    _outside_git(root, git_root, "migration recovery state")
    _secure_directory(root)
    return root


def _transaction_dir(project_root: Path) -> Path:
    project, git_root, _scope, _head = project_identity(project_root)
    return _migration_root(project, git_root) / TRANSACTION_NAME


def _acquire_lock(root: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(root / LOCK_NAME, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise ProjectSpecError(
                "MIGRATION_RECOVERY_REQUIRED", "migration lock is unsafe"
            )
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _release_lock(descriptor: int) -> None:
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def _read_existing(path: Path, label: str) -> bytes:
    raw, _text = _read_file(path, label)
    return raw


def _marker_pair(text: str, start: str, end: str) -> bool:
    return text.count(start) == 1 and text.count(end) == 1


def _source_kind(requirements: str, design: str) -> str:
    req_old_start, req_old_end = legacy_task_markers("requirements")
    des_old_start, des_old_end = legacy_task_markers("design")
    req_new_start, req_new_end = spec_markers("requirements")
    des_new_start, des_new_end = spec_markers("design")
    all_text = requirements + "\n" + design
    has_new = any(
        marker in all_text
        for marker in (req_new_start, req_new_end, des_new_start, des_new_end)
    )
    has_task = any(
        marker in all_text
        for marker in (req_old_start, req_old_end, des_old_start, des_old_end)
    )
    req_sdlc = re.findall(
        rf"(?m)^schema:\s*['\"]?{re.escape(LEGACY_SDLC_REQUIREMENTS_SCHEMA)}['\"]?\s*$",
        requirements,
    )
    des_sdlc = re.findall(
        rf"(?m)^schema:\s*['\"]?{re.escape(LEGACY_SDLC_DESIGN_SCHEMA)}['\"]?\s*$",
        design,
    )
    has_sdlc = bool(req_sdlc or des_sdlc)
    owner_kinds = sum((has_new, has_task, has_sdlc))
    if owner_kinds != 1:
        raise ProjectSpecError(
            "SPEC_OWNER_CONFLICT",
            "requirements and design contain mixed or missing owner metadata",
        )
    if (
        has_new
        and _marker_pair(requirements, req_new_start, req_new_end)
        and _marker_pair(design, des_new_start, des_new_end)
    ):
        return "canonical"
    if (
        has_task
        and _marker_pair(requirements, req_old_start, req_old_end)
        and _marker_pair(design, des_old_start, des_old_end)
    ):
        return "task-implementer"
    if has_sdlc and len(req_sdlc) == 1 and len(des_sdlc) == 1:
        return "agentic-sdlc"
    raise ProjectSpecError(
        "SPEC_OWNER_CONFLICT",
        "requirements and design do not form one recognized owner pair",
    )


def _convert_task(raw: bytes, kind: str) -> bytes:
    text = raw.decode("utf-8")
    old_start, old_end = legacy_task_markers(kind)
    start, end = spec_markers(kind)
    if text.count(old_start) != 1 or text.count(old_end) != 1:
        raise ProjectSpecError(
            "SPEC_OWNER_CONFLICT", f"{kind} Task markers are ambiguous"
        )
    return text.replace(old_start, start).replace(old_end, end).encode("utf-8")


def _replace_frontmatter_owner(text: str, kind: str) -> str:
    legacy = (
        LEGACY_SDLC_REQUIREMENTS_SCHEMA
        if kind == "requirements"
        else LEGACY_SDLC_DESIGN_SCHEMA
    )
    current = REQUIREMENTS_SCHEMA if kind == "requirements" else DESIGN_SCHEMA
    skill = (
        "sdlc-create-requirements" if kind == "requirements" else "sdlc-create-design"
    )
    text, schema_count = re.subn(
        rf"(?m)^(schema:\s*)['\"]?{re.escape(legacy)}['\"]?\s*$",
        rf"\g<1>{current}",
        text,
    )
    text, created_count = re.subn(
        rf"(?m)^(created_by_skill:\s*)['\"]?{re.escape(skill)}['\"]?\s*$",
        r"\g<1>maintain-project-specs",
        text,
    )
    text, updated_count = re.subn(
        rf"(?m)^(updated_by_skill:\s*)['\"]?{re.escape(skill)}['\"]?\s*$",
        r"\g<1>maintain-project-specs",
        text,
    )
    if (schema_count, created_count, updated_count) != (1, 1, 1):
        raise ProjectSpecError(
            "SPEC_OWNER_CONFLICT", f"{kind} Agentic SDLC ownership is ambiguous"
        )
    return text


def _convert_sdlc(raw: bytes, kind: str) -> bytes:
    text = _replace_frontmatter_owner(raw.decode("utf-8"), kind)
    start, end = spec_markers(kind)
    newline = "\r\n" if "\r\n" in text else "\n"
    body = text.strip().replace("\r\n", "\n").replace("\n", newline)
    return f"{start}{newline}{body}{newline}{end}{newline}".encode("utf-8")


def _write_exclusive(path: Path, value: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        remaining = memoryview(value)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_exact(path: Path, expected: bytes, replacement: bytes) -> None:
    if _read_existing(path, path.name) != expected:
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", f"{path.name} changed before migration"
        )
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    try:
        _write_exclusive(temporary, replacement, 0o644)
        if _read_existing(path, path.name) != expected:
            raise ProjectSpecError(
                "CONCURRENT_MODIFICATION", f"{path.name} changed during migration"
            )
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _restore_pair(docs: Path, transaction: Path, originals: dict[str, bytes]) -> None:
    for kind, filename in (
        ("requirements", "requirements.md"),
        ("design", "design.md"),
    ):
        path = docs / filename
        backup = transaction / BACKUP_NAMES[kind]
        backup_raw = _read_recovery_file(
            backup, f"{kind} migration backup", 1024 * 1024
        )
        if backup_raw != originals[kind]:
            raise ProjectSpecError(
                "MIGRATION_RECOVERY_REQUIRED",
                f"{kind} migration backup changed during rollback",
            )
        current = _read_existing(path, kind)
        if current != backup_raw:
            _replace_exact(path, current, backup_raw)


def _remove_private_bundle(path: Path, *, complete: bool) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise ProjectSpecError(
            "MIGRATION_RECOVERY_REQUIRED", "migration transaction directory is unsafe"
        )
    allowed = {JOURNAL_NAME, *BACKUP_NAMES.values()}
    children = list(path.iterdir())
    names = {child.name for child in children}
    if not names <= allowed or (complete and names != allowed):
        raise ProjectSpecError(
            "MIGRATION_RECOVERY_REQUIRED", "migration transaction contents are unsafe"
        )
    for child in children:
        item = child.lstat()
        if (
            stat.S_ISLNK(item.st_mode)
            or not stat.S_ISREG(item.st_mode)
            or stat.S_IMODE(item.st_mode) != 0o600
            or (hasattr(os, "getuid") and item.st_uid != os.getuid())
        ):
            raise ProjectSpecError(
                "MIGRATION_RECOVERY_REQUIRED", "migration transaction file is unsafe"
            )
    for child in children:
        child.unlink()
    path.rmdir()
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _retire_transaction(transaction: Path) -> None:
    _validate_private_bundle(transaction, complete=True)
    cleanup = transaction.parent / f"{CLEANUP_PREFIX}{secrets.token_hex(8)}"
    os.rename(transaction, cleanup)
    directory = os.open(transaction.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    _remove_private_bundle(cleanup, complete=True)


def _validate_private_bundle(path: Path, *, complete: bool) -> list[Path]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return []
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise ProjectSpecError(
            "MIGRATION_RECOVERY_REQUIRED", "migration transaction directory is unsafe"
        )
    allowed = {JOURNAL_NAME, *BACKUP_NAMES.values()}
    children = list(path.iterdir())
    names = {child.name for child in children}
    if not names <= allowed or (complete and names != allowed):
        raise ProjectSpecError(
            "MIGRATION_RECOVERY_REQUIRED", "migration transaction contents are unsafe"
        )
    for child in children:
        item = child.lstat()
        if (
            stat.S_ISLNK(item.st_mode)
            or not stat.S_ISREG(item.st_mode)
            or stat.S_IMODE(item.st_mode) != 0o600
            or (hasattr(os, "getuid") and item.st_uid != os.getuid())
        ):
            raise ProjectSpecError(
                "MIGRATION_RECOVERY_REQUIRED", "migration transaction file is unsafe"
            )
    return children


def _cleanup_private_debris(root: Path) -> None:
    for candidate in root.iterdir():
        if candidate.name.startswith((STAGING_PREFIX, CLEANUP_PREFIX)):
            _remove_private_bundle(candidate, complete=False)


def _publish_transaction(
    root: Path,
    source: str,
    originals: dict[str, bytes],
    converted: dict[str, bytes],
    failure_injector: Callable[[str], None] | None,
) -> Path:
    transaction = root / TRANSACTION_NAME
    try:
        transaction.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ProjectSpecError(
            "MIGRATION_RECOVERY_REQUIRED",
            "an existing migration transaction requires recovery",
        )
    staging = root / f"{STAGING_PREFIX}{secrets.token_hex(8)}"
    staging.mkdir(mode=0o700)
    try:
        for kind in ("requirements", "design"):
            _write_exclusive(staging / BACKUP_NAMES[kind], originals[kind], 0o600)
            if failure_injector is not None:
                failure_injector(f"after-{kind}-backup")
        _write_exclusive(
            staging / JOURNAL_NAME,
            stable_json(
                {
                    "schema": "maintain-project-specs.migration.v1",
                    "source": source,
                    "before": {
                        kind: digest(value) for kind, value in originals.items()
                    },
                    "after": {kind: digest(value) for kind, value in converted.items()},
                }
            ),
            0o600,
        )
        if failure_injector is not None:
            failure_injector("after-journal")
        directory = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        os.rename(staging, transaction)
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        _remove_private_bundle(staging, complete=False)
        raise
    return transaction


def _read_recovery_file(path: Path, label: str, max_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ProjectSpecError(
            "MIGRATION_RECOVERY_REQUIRED", f"{label} is missing"
        ) from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size > max_bytes
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise ProjectSpecError("MIGRATION_RECOVERY_REQUIRED", f"{label} is unsafe")
    raw, _text = _read_file(path, label, max_bytes=max_bytes)
    return raw


def migrate_project(
    project_root: Path,
    *,
    failure_injector: Callable[[str], None] | None = None,
) -> dict[str, object]:
    project, git_root, _scope, _head = project_identity(project_root)
    docs = project / "docs"
    if docs.is_symlink() or not docs.is_dir():
        raise ProjectSpecError("UNSAFE_SPEC", "docs must be a real directory")
    root = _migration_root(project, git_root)
    lock_descriptor = _acquire_lock(root)
    try:
        _cleanup_private_debris(root)
        transaction_path = root / TRANSACTION_NAME
        try:
            transaction_path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ProjectSpecError(
                "MIGRATION_RECOVERY_REQUIRED",
                "an existing migration transaction requires recovery",
            )
        paths = {
            "requirements": docs / "requirements.md",
            "design": docs / "design.md",
        }
        originals = {kind: _read_existing(path, kind) for kind, path in paths.items()}
        source = _source_kind(
            originals["requirements"].decode("utf-8"),
            originals["design"].decode("utf-8"),
        )
        if source == "canonical":
            return {"status": "unchanged", "source": source}
        converted = {
            kind: (
                _convert_task(raw, kind)
                if source == "task-implementer"
                else _convert_sdlc(raw, kind)
            )
            for kind, raw in originals.items()
        }
        try:
            transaction = _publish_transaction(
                root, source, originals, converted, failure_injector
            )
        except FileExistsError as error:
            raise ProjectSpecError(
                "MIGRATION_RECOVERY_REQUIRED",
                "another migration transaction requires recovery",
            ) from error
        try:
            if failure_injector is not None:
                failure_injector("after-transaction-published")
            _replace_exact(
                paths["requirements"],
                originals["requirements"],
                converted["requirements"],
            )
            if failure_injector is not None:
                failure_injector("after-requirements")
            _replace_exact(paths["design"], originals["design"], converted["design"])
            if failure_injector is not None:
                failure_injector("after-design")
        except Exception:
            try:
                _restore_pair(docs, transaction, originals)
            except Exception as rollback_error:
                raise ProjectSpecError(
                    "MIGRATION_RECOVERY_REQUIRED",
                    "migration rollback failed; exact recovery artifacts were retained",
                ) from rollback_error
            _retire_transaction(transaction)
            raise
        _retire_transaction(transaction)
        return {
            "status": "migrated",
            "source": source,
            "requirements_sha256": digest(converted["requirements"]),
            "design_sha256": digest(converted["design"]),
        }
    finally:
        _release_lock(lock_descriptor)


def recover_migration(project_root: Path) -> dict[str, object]:
    project, git_root, _scope, _head = project_identity(project_root)
    docs = project / "docs"
    if docs.is_symlink() or not docs.is_dir():
        raise ProjectSpecError("UNSAFE_SPEC", "docs must be a real directory")
    root = _migration_root(project, git_root)
    lock_descriptor = _acquire_lock(root)
    try:
        _cleanup_private_debris(root)
        transaction = root / TRANSACTION_NAME
        try:
            metadata = transaction.lstat()
        except FileNotFoundError:
            return {"status": "clean"}
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
            or {path.name for path in transaction.iterdir()}
            != {JOURNAL_NAME, *BACKUP_NAMES.values()}
        ):
            raise ProjectSpecError(
                "MIGRATION_RECOVERY_REQUIRED", "migration transaction is incomplete"
            )
        journal_raw = _read_recovery_file(
            transaction / JOURNAL_NAME, "migration journal", 16 * 1024
        )
        try:
            record = json.loads(journal_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProjectSpecError(
                "MIGRATION_RECOVERY_REQUIRED", "migration journal is invalid"
            ) from error
        if (
            not isinstance(record, dict)
            or set(record) != {"schema", "source", "before", "after"}
            or record.get("schema") != "maintain-project-specs.migration.v1"
            or record.get("source") not in {"task-implementer", "agentic-sdlc"}
            or not isinstance(record.get("before"), dict)
            or not isinstance(record.get("after"), dict)
            or set(record["before"]) != {"requirements", "design"}
            or set(record["after"]) != {"requirements", "design"}
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for group in (record["before"], record["after"])
                for value in group.values()
            )
        ):
            raise ProjectSpecError(
                "MIGRATION_RECOVERY_REQUIRED", "migration journal contract is invalid"
            )
        backups = {kind: transaction / name for kind, name in BACKUP_NAMES.items()}
        originals = {
            kind: _read_recovery_file(path, f"{kind} migration backup", 1024 * 1024)
            for kind, path in backups.items()
        }
        for kind, filename in (
            ("requirements", "requirements.md"),
            ("design", "design.md"),
        ):
            if digest(originals[kind]) != record["before"][kind]:
                raise ProjectSpecError(
                    "MIGRATION_RECOVERY_REQUIRED", f"{kind} migration backup is stale"
                )
            current = _read_existing(docs / filename, kind)
            if digest(current) not in {
                record["before"][kind],
                record["after"][kind],
            }:
                raise ProjectSpecError(
                    "MIGRATION_RECOVERY_REQUIRED",
                    f"{kind} changed after migration failure",
                )
        for kind, filename in (
            ("requirements", "requirements.md"),
            ("design", "design.md"),
        ):
            target = docs / filename
            current = _read_existing(target, kind)
            _replace_exact(target, current, originals[kind])
        _retire_transaction(transaction)
        return {"status": "recovered"}
    finally:
        _release_lock(lock_descriptor)
