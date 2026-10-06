"""Private installation records; a directory is never a completion marker."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


SCHEMA = "course-installation/v1"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def regular(path, *, private=False):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError(f"Require an owned regular file: {path}")
    if private and stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError(f"Require a private mode-600 file: {path}")
    if info.st_mode & 0o022:
        raise ValueError(f"Refusing a file writable by another user: {path}")


def private_dir(path):
    path = Path(path)
    # Validate existing ancestors before mkdir, including dangling symlinks.
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise ValueError(f"Refusing a symlinked installation path: {parent}")
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError(f"Require an owned private mode-700 directory: {path}")
    return path


def read_json(path):
    for parent in Path(path).parents:
        if parent.is_symlink():
            raise ValueError("Installation records must not follow directory symlinks")
    regular(path, private=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        if os.fstat(stream.fileno()).st_size > 4 * 1024 * 1024:
            raise ValueError("Installation record exceeds its size limit")
        return json.load(stream)


def atomic_json(path, value):
    path = Path(path)
    private_dir(path.parent)
    if path.exists() or path.is_symlink():
        regular(path, private=True)
    fd, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def setup_lock(root):
    private_dir(root)
    path = root / "setup.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        regular(path, private=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Another course setup is running; retry after it finishes"
            ) from None
        yield
    finally:
        os.close(fd)


class State:
    def __init__(self, root):
        self.root = private_dir(Path(root) / ".runtime")
        self.records = private_dir(self.root / "components")
        self.installs = private_dir(self.root / "installations")
        self.runtimes = private_dir(self.root / "runtimes")
        self.cache = private_dir(self.root / "cache")

    def completed(self, name, fingerprint):
        path = self.records / f"{name}.json"
        if not path.exists() and not path.is_symlink():
            return None
        record = read_json(path)
        if record.get("schema") != SCHEMA or record.get("id") != name:
            raise ValueError(f"Invalid installation record: {name}")
        if record.get("fingerprint") != fingerprint:
            return None
        prefix = Path(record["prefix"])
        if not prefix.is_relative_to(self.installs):
            raise ValueError(f"Installation prefix escapes managed storage: {name}")
        if not prefix.is_dir():
            return None
        private_dir(prefix)
        for relative, expected in record["artifacts"].items():
            leaf = Path(relative)
            if leaf.is_absolute() or ".." in leaf.parts:
                raise ValueError("Unsafe recorded artifact path")
            artifact = prefix / leaf
            if not artifact.resolve().is_relative_to(prefix):
                raise ValueError("Recorded artifact escapes its installation")
            if not artifact.is_file() or file_digest(artifact) != expected:
                return None
        return record

    def generation(self, name, fingerprint):
        base = private_dir(self.installs / name)
        # Build here permanently: Python venvs embed this absolute path.
        return Path(tempfile.mkdtemp(prefix=fingerprint[:12] + "-", dir=base))

    def commit(self, name, fingerprint, prefix, artifacts, values):
        hashes = {}
        for relative in artifacts:
            leaf = Path(relative)
            if leaf.is_absolute() or ".." in leaf.parts:
                raise ValueError("Unsafe artifact path")
            path = prefix / leaf
            if not path.resolve().is_relative_to(prefix):
                raise ValueError("Installed artifact escapes its installation")
            if not path.is_file():
                raise RuntimeError(f"Installation did not produce {name}/{relative}")
            hashes[relative] = file_digest(path)
        stats = {
            relative: {
                "size": (prefix / relative).stat().st_size,
                "mtime_ns": (prefix / relative).stat().st_mtime_ns,
            }
            for relative in hashes
        }
        record = {
            "schema": SCHEMA,
            "id": name,
            "fingerprint": fingerprint,
            "prefix": str(prefix),
            "artifacts": hashes,
            "stats": stats,
            "values": values,
        }
        atomic_json(self.records / f"{name}.json", record)
        return record
