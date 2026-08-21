"""Private workflow-state ownership, containment, and atomic persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any, Optional

from .contracts import PRIVATE_ROOT_MARKER
from .contracts import PRIVATE_ROOT_SCHEMA
from .contracts import ProjectInstructionsError
from .contracts import _inside
from .contracts import _lstat_optional
from .contracts import _read_regular
from .contracts import _remove_owned_file
from .contracts import _stable_json


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    raw = _read_regular(path, label)
    try:
        value: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} must contain one JSON object"
        ) from error
    if not isinstance(value, dict):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} must contain one JSON object"
        )
    return value


def _load_private_json_object(
    path: Path,
    label: str,
    private_root: Path,
) -> dict[str, object]:
    target = _private_member(private_root, path, label)
    metadata = _lstat_optional(target)
    if metadata is not None and stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ProjectInstructionsError("UNSAFE_TARGET", f"{label} must use mode 0600")
    return _load_json_object(target, label)


def _git_context(path: Path) -> bool:
    for flag in ("--is-inside-work-tree", "--is-inside-git-dir"):
        try:
            completed = subprocess.run(
                ["git", "-C", str(path), "rev-parse", flag],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "could not validate the private root"
            ) from error
        if completed.returncode == 0 and completed.stdout.strip() == "true":
            return True
    return False


def _ensure_private_root(path: Path, git_root: Path) -> Path:
    root = Path(os.path.abspath(path.expanduser()))
    if root.resolve(strict=False) != root or _inside(root, git_root):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET",
            "private root must be a non-symlinked directory outside Git",
        )
    metadata = _lstat_optional(root)
    if metadata is None:
        parent = root.parent
        parent_metadata = _lstat_optional(parent)
        if (
            parent.resolve(strict=False) != parent
            or parent_metadata is None
            or stat.S_ISLNK(parent_metadata.st_mode)
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or stat.S_IMODE(parent_metadata.st_mode) & 0o077
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET",
                "private root parent must be an existing private directory",
            )
        try:
            root.mkdir(mode=0o700)
        except OSError as error:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "private root could not be created"
            ) from error
        metadata = root.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or _git_context(root)
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET",
            "private root must be private and outside every Git worktree",
        )
    marker = root / PRIVATE_ROOT_MARKER
    marker_content = _stable_json({"schema": PRIVATE_ROOT_SCHEMA})
    marker_metadata = _lstat_optional(marker)
    if marker_metadata is None:
        if any(root.iterdir()):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET",
                "unowned private root is not empty",
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        marker_identity: Optional[os.stat_result] = None
        try:
            descriptor = os.open(marker, flags, 0o600)
            marker_identity = os.fstat(descriptor)
            os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "wb")
            descriptor = -1
            with handle:
                handle.write(marker_content)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            if marker_identity is not None:
                _remove_owned_file(marker, marker_identity)
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "private root marker could not be created"
            ) from error
    else:
        marker_bytes = _read_regular(marker, "private root marker")
        if (
            stat.S_IMODE(marker_metadata.st_mode) != 0o600
            or marker_bytes != marker_content
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "private root marker is invalid"
            )
    return root


def _private_member(root: Path, path: Path, label: str) -> Path:
    supplied = path.expanduser()
    target = Path(
        os.path.abspath(supplied if supplied.is_absolute() else root / supplied)
    )
    if (
        target.resolve(strict=False) != target
        or not _inside(target, root)
        or target.parent != root
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET",
            f"{label} must be a direct child of the private root",
        )
    return target


def _relative_private_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _sync_private_directory(
    directory: Path,
    write_failure_code: str,
    write_failure_message: Optional[str],
) -> None:
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_descriptor = os.open(directory, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        raise ProjectInstructionsError(
            write_failure_code,
            write_failure_message or "private evidence could not be written safely",
        ) from error


def _write_private_json(
    path: Path,
    value: object,
    git_root: Path,
    private_root: Path,
    *,
    write_failure_code: str = "UNSAFE_TARGET",
    write_failure_message: Optional[str] = None,
) -> None:
    target = _private_member(private_root, path, "private evidence")
    if _inside(target, git_root):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET",
            "private evidence must stay outside the Git worktree",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = _stable_json(value)
    metadata = _lstat_optional(target)
    if metadata is not None:
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "private evidence target is unsafe"
            )
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET",
                "private evidence target must use mode 0600",
            )
        try:
            existing_bytes = target.read_bytes()
        except OSError as error:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "private evidence target could not be read safely"
            ) from error
        confirmed = _lstat_optional(target)
        if (
            confirmed is None
            or not stat.S_ISREG(confirmed.st_mode)
            or stat.S_IMODE(confirmed.st_mode) != 0o600
            or confirmed.st_nlink != 1
            or (confirmed.st_dev, confirmed.st_ino)
            != (metadata.st_dev, metadata.st_ino)
            or confirmed.st_size != metadata.st_size
            or confirmed.st_mtime_ns != metadata.st_mtime_ns
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "private evidence target changed during validation"
            )
        if existing_bytes == encoded:
            _sync_private_directory(
                target.parent,
                write_failure_code,
                write_failure_message,
            )
            return
        try:
            existing: Any = json.loads(existing_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET",
                "private evidence target is not owned by this workflow",
            ) from error
        expected_schema = value.get("schema") if isinstance(value, dict) else None
        if not isinstance(existing, dict) or existing.get("schema") != expected_schema:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET",
                "private evidence target is not owned by this workflow",
            )
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=str(target.parent)
        )
    except OSError as error:
        raise ProjectInstructionsError(
            write_failure_code,
            write_failure_message or "private evidence could not be staged",
        ) from error
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        _sync_private_directory(
            target.parent,
            write_failure_code,
            write_failure_message,
        )
    except OSError as error:
        raise ProjectInstructionsError(
            write_failure_code,
            write_failure_message or "private evidence could not be written safely",
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()
