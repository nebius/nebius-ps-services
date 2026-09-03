"""Private receipt storage and migration path safety.

Historical lifecycle records are parsed by the advisory hook and retention
helpers. This module deliberately exposes no lifecycle transition API.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any

from .contracts import ProjectSpecError, stable_json, validate_project

SAFE_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]{1,96}")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _safe_segment(value: object) -> str:
    raw = str(value)
    if SAFE_SEGMENT_RE.fullmatch(raw) and raw not in {".", ".."}:
        return raw
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")[:48] or "session"
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def lifecycle_dir(git_root: Path, session_id: object) -> Path:
    """Return the passive historical bundle directory for one session."""

    workspace = (
        f"{_safe_segment(git_root.name)}-"
        f"{hashlib.sha256(str(git_root).encode()).hexdigest()[:12]}"
    )
    return codex_home() / "project-specs" / workspace / _safe_segment(session_id)


def _secure_directory(path: Path) -> None:
    home = codex_home()
    if home.resolve(strict=False) != home:
        raise ProjectSpecError("UNSAFE_STATE", "CODEX_HOME must not use symlinks")
    try:
        home.mkdir(mode=0o700)
    except FileExistsError:
        pass
    home_metadata = home.lstat()
    if stat.S_ISLNK(home_metadata.st_mode) or not stat.S_ISDIR(home_metadata.st_mode):
        raise ProjectSpecError("UNSAFE_STATE", "CODEX_HOME is unsafe")
    private_root = home / "project-specs"
    try:
        relative = path.relative_to(private_root)
    except ValueError as error:
        raise ProjectSpecError(
            "UNSAFE_STATE", "private state escaped its managed root"
        ) from error
    current = private_root
    for part in ("", *relative.parts):
        if part:
            current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        metadata = current.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise ProjectSpecError("UNSAFE_STATE", "private state directory is unsafe")
        os.chmod(current, 0o700)


def _write_private_bytes(path: Path, data: bytes) -> None:
    _secure_directory(path.parent)
    temporary_name = f".{path.name}.{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    parent = os.open(path.parent, directory_flags)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
        os.fsync(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def write_validation_receipt(
    project_root: Path,
    output: Path,
    session_id: object | None,
) -> dict[str, Any]:
    """Store one advisory owner receipt in the exact private session bundle."""

    receipt = validate_project(project_root)
    if session_id is None:
        raise ProjectSpecError(
            "PRIVATE_SESSION_REQUIRED",
            "spec receipt output requires the current private session",
        )
    target = Path(os.path.abspath(output.expanduser()))
    git_root = Path(str(receipt["git_root"]))
    expected = Path(
        os.path.abspath(lifecycle_dir(git_root, session_id) / "spec-receipt.json")
    )
    if target != expected:
        raise ProjectSpecError(
            "UNSAFE_STATE",
            "spec receipt output must be the current session spec-receipt.json",
        )
    _write_private_bytes(target, stable_json(receipt))
    return receipt


def _outside_git(path: Path, git_root: Path, label: str) -> None:
    absolute = Path(os.path.abspath(path.expanduser())).resolve(strict=False)
    try:
        absolute.relative_to(git_root.resolve())
    except ValueError:
        return
    raise ProjectSpecError("UNSAFE_STATE", f"{label} must stay outside Git")
