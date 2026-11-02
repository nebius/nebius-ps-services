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


# BEGIN shared runtime bootstrap
def _load_skill_support(group, anchor_file, declared_path, *, source_only=False):
    import hashlib as _hashlib
    import os as _os
    from pathlib import Path as _Path
    import stat as _stat
    import sys as _sys
    from types import ModuleType as _ModuleType

    def read_source(path):
        path = _Path(_os.path.abspath(path))
        for part in (*reversed(path.parents), path):
            metadata = part.lstat()
            if _stat.S_ISLNK(metadata.st_mode):
                if metadata.st_uid != 0 or part == path:
                    raise ImportError("shared runtime path contains an unsafe symlink")
                metadata = part.stat()
            if part != path:
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if (not _stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, _os.getuid()}
                        or metadata.st_mode & 0o022 and not sticky):
                    raise ImportError("unsafe shared runtime ancestry")
        path = path.resolve(strict=True)
        descriptor = _os.open(path.anchor, _os.O_RDONLY | _os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                child = _os.open(part, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW, dir_fd=descriptor)
                _os.close(descriptor)
                descriptor = child
                metadata = _os.fstat(descriptor)
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if metadata.st_uid not in {0, _os.getuid()} or metadata.st_mode & 0o022 and not sticky:
                    raise ImportError("unsafe shared runtime ancestry")
            child = _os.open(path.name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_NONBLOCK, dir_fd=descriptor)
            try:
                before = _os.fstat(child)
                if (not _stat.S_ISREG(before.st_mode) or before.st_uid != _os.getuid()
                        or before.st_mode & 0o022 or before.st_nlink != 1 or before.st_size > 1048576):
                    raise ImportError("unsafe shared runtime source")
                data = bytearray()
                while chunk := _os.read(child, min(65536, 1048577 - len(data))):
                    data.extend(chunk)
                    if len(data) > 1048576:
                        raise ImportError("shared runtime source exceeds size limit")
                after = _os.fstat(child)
                bound = _os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                def identity(value):
                    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if identity(before) != identity(after) or identity(after) != identity(bound):
                    raise ImportError("shared runtime source changed while reading")
                return bytes(data), identity(after)
            finally:
                _os.close(child)
        finally:
            _os.close(descriptor)

    anchor = _Path(_os.path.abspath(anchor_file))
    declared_paths = (declared_path,) if isinstance(declared_path, str) else declared_path
    candidates = []
    for declared in declared_paths:
        relative = _Path(declared)
        if tuple(anchor.parts[-len(relative.parts):]) == relative.parts:
            catalog = anchor.parents[len(relative.parts) - 1]
            candidates.append(("catalog", catalog, catalog / "global-context-management/scripts"))
    if not source_only:
        flat = anchor.parent.parent if anchor.parent.name == "lib" else anchor.parent
        candidates.append(("flat", flat, flat))
        agent = "codex" if _os.environ.get("CODEX_THREAD_ID") else _os.environ.get("SKILLS_AGENT", "codex")
        if agent not in {"codex", "claude"}:
            raise ImportError("SKILLS_AGENT must be codex or claude")
        key, default = ("CODEX_HOME", ".codex") if agent == "codex" else ("CLAUDE_CONFIG_DIR", ".claude")
        home = _Path(_os.environ.get(key, str(_Path.home() / default))).expanduser()
        if not home.is_absolute():
            raise ImportError("shared runtime home must be absolute")
        candidates.append(("flat", home / "hooks", home / "hooks"))
    for kind, root, support in candidates:
        loader_path = support / "trusted_runtime.py"
        if not loader_path.exists() and not loader_path.is_symlink():
            if ((kind == "catalog" and (support.exists() or support.is_symlink()))
                    or any((support / name).exists() or (support / name).is_symlink()
                           for name in ("agent_runtime.py", "hook_runtime.py", "task_state_permissions.py"))):
                raise ImportError("incomplete shared runtime bundle; reinstall current support")
            continue
        data, identity = read_source(loader_path)
        digest = _hashlib.sha256(data).hexdigest()
        cache_name = "_skills_trusted_runtime"
        loader = _sys.modules.get(cache_name)
        provenance = (str(loader_path), identity, digest)
        if cache_name in _sys.modules:
            if (type(loader) is not _ModuleType or getattr(loader, "_bootstrap_provenance", None) != provenance):
                raise ImportError("conflicting shared runtime loader")
        else:
            loader = _ModuleType(cache_name)
            loader.__file__ = str(loader_path)
            exec(compile(data, str(loader_path), "exec"), loader.__dict__)
            loader._bootstrap_provenance = provenance
            loader._read_source = read_source
            _sys.modules[cache_name] = loader
        return loader.load_support(group, anchor=(kind, root), source_only=source_only)
    raise ImportError("Shared skill runtime unavailable; install the complete current skill support")
# END shared runtime bootstrap
_load_skill_support('git-environment', __file__, 'worktree/scripts/workflow_state.py')
from git_environment import environment as _git_environment  # noqa: E402

class WorkflowError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def git_environment(cwd, **options):
    try:
        return _git_environment(cwd, **options)
    except ValueError as error:
        raise WorkflowError("UNSAFE_GIT_ENVIRONMENT", str(error)) from error


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
