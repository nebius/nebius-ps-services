"""Descriptor-anchored, race-safe mutation of generated project instructions."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
from typing import Optional

from .contracts import GENERATED_MARKER_PREFIX
from .contracts import GENERATED_MARKER_SUFFIX
from .contracts import ProjectInstructionsError
from .contracts import _sha256_bytes


ParentIdentity = tuple[int, int]


def _generated_marker(
    manifest_sha256: str,
    decision_sha256: str,
    body_sha256: str,
) -> bytes:
    return (
        GENERATED_MARKER_PREFIX
        + b"manifest-sha256="
        + manifest_sha256.encode("ascii")
        + b" decision-sha256="
        + decision_sha256.encode("ascii")
        + b" body-sha256="
        + body_sha256.encode("ascii")
        + GENERATED_MARKER_SUFFIX
    )


def _generated_content(
    body: bytes,
    manifest_sha256: str,
    decision_sha256: str,
    prefix: bytes = b"",
) -> bytes:
    return (
        prefix
        + (b"\n\n" if prefix else b"")
        + _generated_marker(
            manifest_sha256,
            decision_sha256,
            _sha256_bytes(body),
        )
        + body
    )


def _open_parent(path: Path, expected: Optional[ParentIdentity]) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path.parent, flags)
        metadata = os.fstat(descriptor)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "project AGENTS.md parent could not be anchored"
        ) from error
    if not stat.S_ISDIR(metadata.st_mode) or (
        expected is not None and (metadata.st_dev, metadata.st_ino) != expected
    ):
        os.close(descriptor)
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "project AGENTS.md parent identity changed"
        )
    return descriptor


def _name_stat(parent: int, name: str) -> Optional[os.stat_result]:
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _read_name(parent: int, name: str, label: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"{label} is not regular")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as error:
        raise ProjectInstructionsError("UNSAFE_TARGET", f"{label} is unsafe") from error
    finally:
        if "descriptor" in locals():
            os.close(descriptor)


def _remove_owned_name(parent: int, name: str, expected: os.stat_result) -> None:
    try:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino):
            os.unlink(name, dir_fd=parent)
    except FileNotFoundError:
        return


def _exclusive_name(parent: int, name: str, mode: int) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(name, flags, mode, dir_fd=parent)


def _temporary_name(parent: int) -> tuple[int, str]:
    for _ in range(100):
        name = f".AGENTS.md.{secrets.token_hex(8)}"
        try:
            return _exclusive_name(parent, name, 0o600), name
        except FileExistsError:
            continue
    raise ProjectInstructionsError(
        "UNSAFE_TARGET", "project AGENTS.md temporary name could not be reserved"
    )


def _write_descriptor(descriptor: int, content: bytes, mode: int) -> None:
    os.fchmod(descriptor, mode)
    remaining = memoryview(content)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short write")
        remaining = remaining[written:]
    os.fsync(descriptor)


def _restore_backup(parent: int, backup: str, target: str) -> None:
    if _name_stat(parent, target) is not None:
        return
    try:
        os.link(
            backup,
            target,
            src_dir_fd=parent,
            dst_dir_fd=parent,
            follow_symlinks=False,
        )
    except FileExistsError:
        return
    os.unlink(backup, dir_fd=parent)
    os.fsync(parent)


def _exclusive_create(
    path: Path,
    content: bytes,
    parent_identity: Optional[ParentIdentity] = None,
) -> None:
    parent = _open_parent(path, parent_identity)
    lock_name = ".AGENTS.md.project-agent-instructions.lock"
    backup_name = ".AGENTS.md.project-agent-instructions.backup"
    lock_identity: Optional[os.stat_result] = None
    descriptor = -1
    opened: Optional[os.stat_result] = None
    try:
        try:
            lock_descriptor = _exclusive_name(parent, lock_name, 0o600)
            lock_identity = os.fstat(lock_descriptor)
            os.fchmod(lock_descriptor, 0o600)
            os.close(lock_descriptor)
        except FileExistsError as error:
            raise ProjectInstructionsError(
                "RECOVERY_REQUIRED",
                "project AGENTS.md creation lock requires recovery",
            ) from error
        if _name_stat(parent, backup_name) is not None:
            raise ProjectInstructionsError(
                "RECOVERY_REQUIRED",
                "a preserved project AGENTS.md backup requires recovery",
            )
        try:
            descriptor = _exclusive_name(parent, path.name, 0o644)
        except FileExistsError as error:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION", "project AGENTS.md appeared concurrently"
            ) from error
        opened = os.fstat(descriptor)
        _write_descriptor(descriptor, content, 0o644)
        os.close(descriptor)
        descriptor = -1
        os.fsync(parent)
        _remove_owned_name(parent, lock_name, lock_identity)
        lock_identity = None
        os.fsync(parent)
    except ProjectInstructionsError:
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if opened is not None:
            _remove_owned_name(parent, path.name, opened)
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "project AGENTS.md could not be written safely"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if lock_identity is not None:
            _remove_owned_name(parent, lock_name, lock_identity)
        os.close(parent)


def _guarded_replace(
    path: Path,
    expected_sha256: str,
    content: bytes,
    parent_identity: Optional[ParentIdentity] = None,
    *,
    retain_backup: bool = False,
) -> None:
    parent = _open_parent(path, parent_identity)
    lock_name = ".AGENTS.md.project-agent-instructions.lock"
    backup_name = ".AGENTS.md.project-agent-instructions.backup"
    lock_identity: Optional[os.stat_result] = None
    temporary_name: Optional[str] = None
    descriptor = -1
    try:
        try:
            lock_descriptor = _exclusive_name(parent, lock_name, 0o600)
            lock_identity = os.fstat(lock_descriptor)
            os.fchmod(lock_descriptor, 0o600)
            os.close(lock_descriptor)
        except FileExistsError as error:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md refresh lock already exists",
            ) from error
        if _name_stat(parent, backup_name) is not None:
            raise ProjectInstructionsError(
                "RECOVERY_REQUIRED",
                "a preserved project AGENTS.md refresh backup requires recovery",
            )
        before = _name_stat(parent, path.name)
        if (
            before is None
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "project AGENTS.md target became unsafe"
            )
        if (
            _sha256_bytes(_read_name(parent, path.name, "project AGENTS.md"))
            != expected_sha256
        ):
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION", "project AGENTS.md changed before refresh"
            )
        descriptor, temporary_name = _temporary_name(parent)
        _write_descriptor(descriptor, content, 0o644)
        os.close(descriptor)
        descriptor = -1
        after = _name_stat(parent, path.name)
        if (
            after is None
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or _sha256_bytes(_read_name(parent, path.name, "project AGENTS.md"))
            != expected_sha256
        ):
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION", "project AGENTS.md changed during refresh"
            )
        os.replace(
            path.name,
            backup_name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
        if (
            _sha256_bytes(_read_name(parent, backup_name, "refresh backup"))
            != expected_sha256
        ):
            _restore_backup(parent, backup_name, path.name)
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md changed during refresh; changed bytes were preserved",
            )
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md reappeared during refresh; original bytes were "
                "preserved in the recovery backup",
            ) from error
        os.unlink(temporary_name, dir_fd=parent)
        temporary_name = None
        if not retain_backup:
            os.unlink(backup_name, dir_fd=parent)
        os.fsync(parent)
    except OSError as error:
        if (
            _name_stat(parent, backup_name) is not None
            and _name_stat(parent, path.name) is None
        ):
            try:
                _restore_backup(parent, backup_name, path.name)
            except OSError:
                pass
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "project AGENTS.md refresh could not complete safely",
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if (
            temporary_name is not None
            and _name_stat(parent, temporary_name) is not None
        ):
            os.unlink(temporary_name, dir_fd=parent)
        if lock_identity is not None:
            _remove_owned_name(parent, lock_name, lock_identity)
        os.close(parent)


def _guarded_delete(
    path: Path,
    expected_sha256: str,
    parent_identity: Optional[ParentIdentity] = None,
    *,
    retain_backup: bool = False,
) -> None:
    parent = _open_parent(path, parent_identity)
    lock_name = ".AGENTS.md.project-agent-instructions.lock"
    backup_name = ".AGENTS.md.project-agent-instructions.backup"
    lock_identity: Optional[os.stat_result] = None
    try:
        try:
            lock_descriptor = _exclusive_name(parent, lock_name, 0o600)
            lock_identity = os.fstat(lock_descriptor)
            os.fchmod(lock_descriptor, 0o600)
            os.close(lock_descriptor)
        except FileExistsError as error:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md retirement lock already exists",
            ) from error
        if _name_stat(parent, backup_name) is not None:
            raise ProjectInstructionsError(
                "RECOVERY_REQUIRED",
                "a preserved project AGENTS.md backup requires recovery",
            )
        before = _name_stat(parent, path.name)
        if (
            before is None
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "project AGENTS.md target became unsafe"
            )
        if (
            _sha256_bytes(_read_name(parent, path.name, "project AGENTS.md"))
            != expected_sha256
        ):
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION", "project AGENTS.md changed before retirement"
            )
        after = _name_stat(parent, path.name)
        if (
            after is None
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or _sha256_bytes(_read_name(parent, path.name, "project AGENTS.md"))
            != expected_sha256
        ):
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md changed during retirement",
            )
        os.replace(
            path.name,
            backup_name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
        if (
            _sha256_bytes(_read_name(parent, backup_name, "retirement backup"))
            != expected_sha256
        ):
            _restore_backup(parent, backup_name, path.name)
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md changed during retirement; changed bytes were "
                "preserved",
            )
        if _name_stat(parent, path.name) is not None:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project AGENTS.md reappeared during retirement; removed bytes were "
                "preserved in the recovery backup",
            )
        if not retain_backup:
            os.unlink(backup_name, dir_fd=parent)
        os.fsync(parent)
    except OSError as error:
        if (
            _name_stat(parent, backup_name) is not None
            and _name_stat(parent, path.name) is None
        ):
            try:
                _restore_backup(parent, backup_name, path.name)
            except OSError:
                pass
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "project AGENTS.md retirement could not complete safely",
        ) from error
    finally:
        if lock_identity is not None:
            _remove_owned_name(parent, lock_name, lock_identity)
        os.close(parent)


def _complete_retained_backup(
    path: Path,
    backup_sha256: str,
    target_sha256: Optional[str],
    parent_identity: ParentIdentity,
) -> None:
    """Finalize a retained backup only after private state is durable."""

    parent = _open_parent(path, parent_identity)
    backup_name = ".AGENTS.md.project-agent-instructions.backup"
    try:
        backup = _name_stat(parent, backup_name)
        target = _name_stat(parent, path.name)
        if (
            backup is None
            or stat.S_ISLNK(backup.st_mode)
            or not stat.S_ISREG(backup.st_mode)
            or _sha256_bytes(_read_name(parent, backup_name, "transition backup"))
            != backup_sha256
            or (target_sha256 is None and target is not None)
            or (
                target_sha256 is not None
                and (
                    target is None
                    or stat.S_ISLNK(target.st_mode)
                    or not stat.S_ISREG(target.st_mode)
                    or _sha256_bytes(_read_name(parent, path.name, "project AGENTS.md"))
                    != target_sha256
                )
            )
        ):
            raise ProjectInstructionsError(
                "RECOVERY_REQUIRED",
                "project AGENTS.md transition backup could not be finalized",
            )
        os.unlink(backup_name, dir_fd=parent)
        os.fsync(parent)
    except OSError as error:
        raise ProjectInstructionsError(
            "RECOVERY_REQUIRED",
            "project AGENTS.md transition backup could not be finalized",
        ) from error
    finally:
        os.close(parent)
