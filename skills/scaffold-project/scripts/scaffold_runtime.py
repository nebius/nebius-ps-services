"""Private target serialization shared by static apply and specialist readiness."""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def target_lock(target: Path):
    """Fail immediately when another cooperating process owns this target."""
    canonical = os.path.abspath(target)
    root = (
        Path(tempfile.gettempdir()).resolve() / f"scaffold-target-locks-{os.getuid()}"
    )
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = root.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("unsafe target lock directory")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        name = hashlib.sha256(canonical.encode()).hexdigest() + ".lock"
        descriptor = os.open(
            name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ValueError("unsafe target lock file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "another scaffold apply or bootstrap owns this target; retry later"
            ) from error
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)
