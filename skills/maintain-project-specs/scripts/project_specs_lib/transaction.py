"""Locked, failure-atomic compare-and-swap publication for one spec pair."""

from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
from contextlib import contextmanager
from typing import Any

from .contracts import (
    MAX_SPEC_BYTES,
    ProjectSpecError,
    digest,
    inspect_pair_bytes,
    project_identity,
    stable_json,
    _read_file,
)
from .lifecycle import _outside_git, _secure_directory, codex_home


OPERATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _state_root(project: Path, git_root: Path) -> Path:
    workspace = f"{git_root.name}-{hashlib.sha256(str(git_root).encode()).hexdigest()[:12]}"
    scope = hashlib.sha256(str(project).encode()).hexdigest()[:16]
    root = codex_home() / "project-specs" / workspace / "transactions" / scope
    _outside_git(root, git_root, "project spec transaction state")
    _secure_directory(root)
    return root


def _acquire_lock(root: Path, *, shared: bool = False) -> int:
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(root / ".publication.lock", flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise ProjectSpecError("UNSAFE_SPEC", "publication lock is unsafe")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


@contextmanager
def spec_pair_lock(project: Path, git_root: Path, *, shared: bool = False):
    """Serialize owner-aware readers and writers for one selected project."""

    descriptor = _acquire_lock(_state_root(project, git_root), shared=shared)
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_optional_at(directory: int, name: str, label: str) -> bytes | None:
    try:
        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > MAX_SPEC_BYTES
        or metadata.st_mode & 0o022
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise ProjectSpecError("UNSAFE_SPEC", f"{label} path or mode is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory)
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > MAX_SPEC_BYTES
            or opened.st_mode & 0o022
            or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
        ):
            raise ProjectSpecError("UNSAFE_SPEC", f"{label} identity changed")
        value = bytearray()
        while len(value) <= MAX_SPEC_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, MAX_SPEC_BYTES + 1 - len(value)))
            if not chunk:
                break
            value.extend(chunk)
        if len(value) > MAX_SPEC_BYTES:
            raise ProjectSpecError("UNSAFE_SPEC", f"{label} is too large")
        final = os.fstat(descriptor)
        if (
            final.st_size != len(value)
            or final.st_nlink != 1
            or (
                final.st_size,
                final.st_mtime_ns,
                final.st_ctime_ns,
            )
            != (
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
        ):
            raise ProjectSpecError("CONCURRENT_MODIFICATION", f"{label} changed while read")
        return bytes(value)
    finally:
        os.close(descriptor)


def _cas_digest(value: bytes | None) -> str:
    return "absent" if value is None else digest(value)


def _write_file_at(directory: int, name: str, value: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(name, flags, mode, dir_fd=directory)
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


def _write_file(path: Path, value: bytes, mode: int) -> None:
    directory = _open_directory(path.parent, "transaction state")
    try:
        _write_file_at(directory, path.name, value, mode)
        os.fsync(directory)
    finally:
        os.close(directory)


def _replace_at(
    directory: int, name: str, value: bytes, expected: bytes | None
) -> None:
    if _read_optional_at(directory, name, name) != expected:
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", f"{name} changed at publication boundary"
        )
    temporary = f".{name}.{secrets.token_hex(8)}"
    try:
        _write_file_at(directory, temporary, value, 0o644)
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _restore_at(
    directory: int, name: str, original: bytes | None, expected_current: bytes
) -> None:
    if _read_optional_at(directory, name, name) != expected_current:
        raise ProjectSpecError(
            "SPEC_TRANSACTION_RECOVERY_REQUIRED",
            f"{name} changed before rollback",
        )
    if original is None:
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.fsync(directory)
    else:
        _replace_at(directory, name, original, expected_current)


def _open_directory(path: Path, label: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ProjectSpecError("UNSAFE_SPEC", f"{label} directory is unsafe") from error
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o022
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        os.close(descriptor)
        raise ProjectSpecError("UNSAFE_SPEC", f"{label} directory is unsafe")
    return descriptor


def _open_child_directory(parent: int, name: str, label: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except OSError as error:
        raise ProjectSpecError("UNSAFE_SPEC", f"{label} directory is unsafe") from error
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o022
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        os.close(descriptor)
        raise ProjectSpecError("UNSAFE_SPEC", f"{label} directory is unsafe")
    return descriptor


def _assert_directory_binding(path: Path, descriptor: int, label: str) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError as error:
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", f"{label} directory was replaced"
        ) from error
    opened = os.fstat(descriptor)
    if (
        stat.S_ISLNK(current.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or current.st_mode & 0o022
        or (hasattr(os, "getuid") and current.st_uid != os.getuid())
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", f"{label} directory was replaced"
        )


def _assert_child_binding(
    parent: int, name: str, descriptor: int, label: str
) -> None:
    try:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError as error:
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", f"{label} directory was replaced"
        ) from error
    opened = os.fstat(descriptor)
    if (
        stat.S_ISLNK(current.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or current.st_mode & 0o022
        or (hasattr(os, "getuid") and current.st_uid != os.getuid())
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", f"{label} directory was replaced"
        )


def _assert_publication_boundary(
    project: Path,
    project_descriptor: int,
    docs_descriptor: int,
    expected_head: str,
    expected_documents: dict[str, bytes | None],
) -> None:
    _assert_directory_binding(project, project_descriptor, "project")
    _assert_child_binding(project_descriptor, "docs", docs_descriptor, "docs")
    try:
        _project, _git_root, _scope, current_head = project_identity(project)
    except ProjectSpecError as error:
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", "project identity changed during publication"
        ) from error
    if current_head != expected_head:
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", "Git HEAD changed during publication"
        )
    for kind, expected in expected_documents.items():
        if _read_optional_at(docs_descriptor, f"{kind}.md", kind) != expected:
            raise ProjectSpecError(
                "CONCURRENT_MODIFICATION", f"{kind} changed during publication"
            )


def publish_spec_pair(
    project_root: Path,
    *,
    requirements_candidate: bytes,
    design_candidate: bytes,
    expected_git_head: str,
    expected_requirements_sha256: str,
    expected_design_sha256: str,
    operation_id: str,
) -> dict[str, Any]:
    """Publish a validated pair iff its bound Git and document state is current."""

    if OPERATION_RE.fullmatch(operation_id) is None:
        raise ProjectSpecError("SPEC_TRANSACTION_INVALID", "operation ID is invalid")
    if re.fullmatch(r"[0-9a-f]{40,64}", expected_git_head) is None:
        raise ProjectSpecError("SPEC_TRANSACTION_INVALID", "expected Git HEAD is invalid")
    for value in (expected_requirements_sha256, expected_design_sha256):
        if value != "absent" and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ProjectSpecError(
                "SPEC_TRANSACTION_INVALID", "expected document digest is invalid"
            )
    if any(
        len(value) > MAX_SPEC_BYTES
        for value in (requirements_candidate, design_candidate)
    ):
        raise ProjectSpecError("UNSAFE_SPEC", "specification candidate is too large")
    inspected = inspect_pair_bytes(requirements_candidate, design_candidate)
    if not all(
        bool(inspected[kind]["managed"]) for kind in ("requirements", "design")
    ):
        raise ProjectSpecError(
            "SPEC_TRANSACTION_INVALID", "both candidates must be canonically managed"
        )
    project, git_root, scope, head = project_identity(project_root)
    if head != expected_git_head:
        raise ProjectSpecError("CONCURRENT_MODIFICATION", "Git HEAD changed before publication")
    docs = project / "docs"
    if docs.exists() and (docs.is_symlink() or not docs.is_dir()):
        raise ProjectSpecError("UNSAFE_SPEC", "docs must be a real directory")
    root = _state_root(project, git_root)
    lock = _acquire_lock(root)
    project_descriptor: int | None = None
    docs_descriptor: int | None = None
    try:
        _project, _git_root, _scope, locked_head = project_identity(project)
        if locked_head != expected_git_head:
            raise ProjectSpecError(
                "CONCURRENT_MODIFICATION", "Git HEAD changed while awaiting publication"
            )
        project_descriptor = _open_directory(project, "project")
        try:
            os.mkdir("docs", 0o755, dir_fd=project_descriptor)
            os.fsync(project_descriptor)
        except FileExistsError:
            pass
        docs_descriptor = _open_child_directory(project_descriptor, "docs", "docs")
        _assert_directory_binding(project, project_descriptor, "project")
        _assert_child_binding(project_descriptor, "docs", docs_descriptor, "docs")
        originals = {
            kind: _read_optional_at(docs_descriptor, f"{kind}.md", kind)
            for kind in ("requirements", "design")
        }
        expected = {
            "requirements": expected_requirements_sha256,
            "design": expected_design_sha256,
        }
        actual = {kind: _cas_digest(value) for kind, value in originals.items()}
        candidates = {
            "requirements": requirements_candidate,
            "design": design_candidate,
        }
        after = {kind: digest(value) for kind, value in candidates.items()}
        receipt = {
            "schema": "maintain-project-specs.transaction.v1",
            "operation_id": operation_id,
            "project_scope": scope,
            "git_head": head,
            "before": expected,
            "after": after,
            "contract_status": inspected["status"],
        }
        journal = root / f"{operation_id}.json"
        try:
            journal.lstat()
        except FileNotFoundError:
            existing = None
        else:
            existing, _text = _read_file(
                journal, "transaction journal", max_bytes=16 * 1024
            )
            if existing != stable_json(receipt):
                raise ProjectSpecError(
                    "SPEC_TRANSACTION_CONFLICT", "operation ID was already used"
                )
            if actual == after:
                _assert_publication_boundary(
                    project,
                    project_descriptor,
                    docs_descriptor,
                    expected_git_head,
                    originals,
                )
                return receipt
            if any(
                actual[kind] not in {expected[kind], after[kind]}
                for kind in actual
            ):
                raise ProjectSpecError(
                    "SPEC_TRANSACTION_RECOVERY_REQUIRED",
                    "interrupted publication contains unknown document bytes",
                )
        if existing is None and actual != expected:
            raise ProjectSpecError(
                "CONCURRENT_MODIFICATION", "specification bytes changed before publication"
            )
        if existing is None:
            _write_file(journal, stable_json(receipt), 0o600)
        current_documents = dict(originals)
        written: list[str] = []
        try:
            _assert_publication_boundary(
                project,
                project_descriptor,
                docs_descriptor,
                expected_git_head,
                current_documents,
            )
            for kind in ("requirements", "design"):
                if current_documents[kind] == candidates[kind]:
                    continue
                _replace_at(
                    docs_descriptor,
                    f"{kind}.md",
                    candidates[kind],
                    current_documents[kind],
                )
                written.append(kind)
                current_documents[kind] = candidates[kind]
                _assert_publication_boundary(
                    project,
                    project_descriptor,
                    docs_descriptor,
                    expected_git_head,
                    current_documents,
                )
        except Exception:
            try:
                for kind in reversed(written):
                    _restore_at(
                        docs_descriptor,
                        f"{kind}.md",
                        originals[kind],
                        candidates[kind],
                    )
            except Exception as rollback_error:
                raise ProjectSpecError(
                    "SPEC_TRANSACTION_RECOVERY_REQUIRED",
                    "publication rollback failed; the private journal was retained",
                ) from rollback_error
            raise
        return receipt
    finally:
        if docs_descriptor is not None:
            os.close(docs_descriptor)
        if project_descriptor is not None:
            os.close(project_descriptor)
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
