"""Retire only known distributed entrypoints after replacement verification."""

import json
from pathlib import Path
import stat
import os

from .state import file_digest


def retire(root, scopes):
    definitions = json.loads(Path(__file__).with_name("retired.json").read_text())
    roots = [root / scope for scope in scopes]
    if any(not course.is_relative_to(root) or ".." in course.parts for course in roots):
        raise ValueError("Unsafe retirement scope")
    removed, preserved = [], []
    for course in roots:
        for filename, hashes in [("course_setup.py", definitions["course_setup.py"])]:
            path = course / "tools" / filename
            if not path.exists() and not path.is_symlink():
                continue
            info = path.lstat()
            if (
                any(parent.is_symlink() for parent in (path, *path.parents))
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
            ):
                preserved.append(str(path.relative_to(root)))
                continue
            if file_digest(path) not in hashes:
                preserved.append(str(path.relative_to(root)))
                continue
            # Do not remove a path replaced or modified during the check.
            current = path.lstat()
            if (
                current.st_ino,
                current.st_dev,
                current.st_mtime_ns,
                current.st_size,
            ) != (info.st_ino, info.st_dev, info.st_mtime_ns, info.st_size):
                raise ValueError("Retired entrypoint changed during verification")
            path.unlink()
            removed.append(str(path.relative_to(root)))
    return {"retired": removed, "preserved_modified": preserved}
