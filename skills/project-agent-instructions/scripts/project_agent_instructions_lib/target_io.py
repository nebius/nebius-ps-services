"""Race-safe creation and replacement of generated project instructions."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
from typing import Optional

from .contracts import GENERATED_MARKER_PREFIX
from .contracts import GENERATED_MARKER_SUFFIX
from .contracts import ProjectInstructionsError
from .contracts import _lstat_optional
from .contracts import _remove_owned_file
from .contracts import _sha256_bytes


def _generated_marker(body_sha256: str) -> bytes:
    return (
        GENERATED_MARKER_PREFIX + body_sha256.encode("ascii") + GENERATED_MARKER_SUFFIX
    )


def _generated_content(body: bytes) -> bytes:
    return _generated_marker(_sha256_bytes(body)) + body


def _exclusive_create(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError as error:
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "project AGENTS.md appeared concurrently",
        ) from error
    except OSError as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "project AGENTS.md could not be created"
        ) from error
    opened: Optional[os.stat_result] = None
    try:
        opened = os.fstat(descriptor)
        os.fchmod(descriptor, 0o644)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            current = path.lstat()
            if opened is not None and (
                current.st_dev,
                current.st_ino,
            ) == (opened.st_dev, opened.st_ino):
                path.unlink()
        except OSError:
            pass
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "project AGENTS.md could not be written safely"
        ) from error


def _guarded_replace(path: Path, expected_sha256: str, content: bytes) -> None:
    lock_path = path.parent / ".AGENTS.md.project-agent-instructions.lock"
    backup_path = path.parent / ".AGENTS.md.project-agent-instructions.backup"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    lock_identity: Optional[os.stat_result] = None
    try:
        lock_descriptor = os.open(lock_path, flags, 0o600)
        try:
            lock_identity = os.fstat(lock_descriptor)
            os.fchmod(lock_descriptor, 0o600)
        finally:
            os.close(lock_descriptor)
    except FileExistsError as error:
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "project AGENTS.md refresh lock already exists",
        ) from error
    except OSError as error:
        if lock_identity is not None:
            _remove_owned_file(lock_path, lock_identity)
        raise ProjectInstructionsError(
            "UNSAFE_TARGET",
            "project AGENTS.md refresh lock could not be created",
        ) from error
    assert lock_identity is not None
    temporary_path: Optional[Path] = None
    descriptor = -1
    try:
        if _lstat_optional(backup_path) is not None:
            raise ProjectInstructionsError(
                "STALE_GENERATED_FILE",
                "a preserved project AGENTS.md refresh backup requires recovery",
            )
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "project AGENTS.md target became unsafe"
            )
        if _sha256_bytes(path.read_bytes()) != expected_sha256:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md changed before refresh",
            )
        descriptor, temporary = tempfile.mkstemp(
            prefix=".AGENTS.md.", dir=str(path.parent)
        )
        temporary_path = Path(temporary)
        os.fchmod(descriptor, 0o644)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or _sha256_bytes(path.read_bytes()) != expected_sha256:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md changed during refresh",
            )
        os.replace(path, backup_path)
        if _sha256_bytes(backup_path.read_bytes()) != expected_sha256:
            _restore_backup(backup_path, path)
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md changed during refresh; changed bytes were "
                "preserved",
            )
        try:
            os.link(temporary_path, path)
        except FileExistsError as error:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md reappeared during refresh; original bytes "
                "were preserved in the recovery backup",
            ) from error
        temporary_path.unlink()
        backup_path.unlink()
    except OSError as error:
        if _lstat_optional(backup_path) is not None and _lstat_optional(path) is None:
            try:
                _restore_backup(backup_path, path)
            except OSError:
                pass
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "project AGENTS.md refresh could not complete safely",
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        _remove_owned_file(lock_path, lock_identity)


def _restore_backup(backup: Path, target: Path) -> None:
    if _lstat_optional(target) is not None:
        return
    try:
        os.link(backup, target)
    except FileExistsError:
        return
    backup.unlink()
