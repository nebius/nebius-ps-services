"""Bounded, owner-private state for the shared workflow execution primitives."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import threading
import uuid


class WorkflowError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def stable(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(stable(value)).hexdigest()


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


@contextmanager
def directory(path: Path, *, create: bool = False):
    """Pin all path components; never follow symlinks or chmod existing parents."""
    path = path.expanduser().absolute()
    if ".." in path.parts:
        raise WorkflowError("UNSAFE_STATE", "state path contains parent traversal")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    entered = False
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            metadata = os.fstat(child)
            sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
            if metadata.st_uid not in {0, os.getuid()} or metadata.st_mode & 0o022 and not sticky_root:
                os.close(child)
                raise WorkflowError("UNSAFE_STATE", "state ancestry is writable by another owner")
            os.close(descriptor)
            descriptor = child
        entered = True
        yield descriptor
    except OSError as error:
        if entered:
            raise
        raise WorkflowError("UNSAFE_STATE", "state path is missing or unsafe") from error
    finally:
        os.close(descriptor)


def _regular(metadata):
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > 4 * 1024 * 1024):
        raise WorkflowError("UNSAFE_STATE", "state must be one bounded owner-private regular file")


def read(path: Path) -> dict:
    with directory(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            _regular(before)
            data = bytearray()
            while chunk := os.read(descriptor, 65536):
                data.extend(chunk)
                if len(data) > 4 * 1024 * 1024:
                    raise WorkflowError("UNSAFE_STATE", "state exceeds size limit")
            after = os.fstat(descriptor)
            bound = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if _identity(before) != _identity(after) or _identity(after) != _identity(bound):
                raise WorkflowError("CONCURRENT_MODIFICATION", "state changed during observation")
        finally:
            os.close(descriptor)
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise WorkflowError("STATE_INVALID", "state JSON is invalid") from error
    if not isinstance(value, dict):
        raise WorkflowError("STATE_INVALID", "state must be an object")
    return value


def write(path: Path, value: object, *, exclusive: bool = False):
    data = stable(value)
    if len(data) > 4 * 1024 * 1024:
        raise WorkflowError("STATE_INVALID", "state exceeds size limit")
    with directory(path.parent, create=True) as parent:
        if exclusive:
            name = path.name
        else:
            try:
                _regular(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
            except FileNotFoundError:
                pass
            name = f".{path.name}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if not exclusive:
                os.replace(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            if not exclusive:
                try:
                    os.unlink(name, dir_fd=parent)
                except FileNotFoundError:
                    pass


_held_locks = threading.local()


@contextmanager
def lock(root: Path, name: str = "transition.lock"):
    identity = str(root.absolute() / name)
    held = getattr(_held_locks, "paths", set())
    if identity in held:
        yield
        return
    with _lock_once(root, name):
        _held_locks.paths = held | {identity}
        try:
            yield
        finally:
            _held_locks.paths = held


@contextmanager
def _lock_once(root: Path, name: str):
    if Path(name).name != name:
        raise WorkflowError("UNSAFE_STATE", "invalid lock name")
    with directory(root, create=True) as parent:
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            _regular(os.fstat(descriptor))
            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise WorkflowError("WORKFLOW_BUSY", "another transition is active")
                    time.sleep(0.05)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
