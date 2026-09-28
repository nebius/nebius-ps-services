"""Private state and strict filesystem primitives for run-labs."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected regular file: {path.name}")
    return json.loads(path.read_text())


def safe_path(path: Path) -> Path:
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Symlink paths are not accepted")
    return path


def directory(path: Path, *, private=True) -> Path:
    safe_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    if private and (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
        raise ValueError(
            f"Private directory must be owned by this user with mode 700: {path.name}"
        )
    return path


def write(path: Path, value, *, private=True):
    safe_path(path)
    directory(path.parent, private=private)
    fd, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600 if private else 0o644)
        os.replace(name, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def lock(path: Path):
    safe_path(path)
    directory(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def token(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", value):
        raise ValueError("Invalid identity token")
    return value
