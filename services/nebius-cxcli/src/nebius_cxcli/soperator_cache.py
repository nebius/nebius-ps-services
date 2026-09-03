"""Small, fail-closed primitives for Soperator content-addressed caches."""

from __future__ import annotations

import fcntl
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_CACHE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{15,127}$")


def prepare_private_cache_root(root: Path) -> Path:
    """Create and validate one owner-only, non-symlink cache directory."""

    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_stat = root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise ValueError(f"Soperator cache root is not a safe directory: {root}")
    root.chmod(0o700)
    return root


@contextmanager
def locked_cache_entry(root: Path, key: str) -> Iterator[None]:
    """Serialize validation and publication for one content-addressed entry."""

    if not _CACHE_KEY_RE.fullmatch(key):
        raise ValueError("Soperator cache key is invalid")
    locks = prepare_private_cache_root(root) / ".locks"
    locks.mkdir(mode=0o700, exist_ok=True)
    locks_stat = locks.lstat()
    if not stat.S_ISDIR(locks_stat.st_mode) or stat.S_ISLNK(locks_stat.st_mode):
        raise ValueError("Soperator cache lock directory is unsafe")
    locks.chmod(0o700)
    path = locks / f"{key}.lock"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError("This platform cannot safely lock the Soperator cache")
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_RDWR | nofollow | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError("Soperator cache lock must be a single-link regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


__all__ = ["locked_cache_entry", "prepare_private_cache_root"]
