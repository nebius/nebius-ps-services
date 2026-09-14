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
_load_skill_support('runtime', __file__, 'maintain-project-specs/scripts/project_specs_lib/lifecycle.py')

from .contracts import ProjectSpecError, stable_json, validate_project  # noqa: E402 — verified bootstrap precedes runtime imports

from agent_runtime import agent_home  # noqa: E402

SAFE_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]{1,96}")


def codex_home() -> Path:
    return agent_home()


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
