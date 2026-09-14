#!/usr/bin/env python3
"""Shared state helpers for local Agentic SDLC Codex hooks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
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
_load_skill_support('runtime', __file__, 'sdlc-start/assets/hooks/lib/sdlc_state.py')

from agent_runtime import agent_home  # noqa: E402


CODEX_HOME = agent_home()
SDLC_RUNS = CODEX_HOME / "sdlc-runs"
CODEX_TASK_STATE = CODEX_HOME / "task-state"


@dataclass(frozen=True)
class ActiveRun:
    project_id: str
    project_root: Path
    run_id: str
    project_dir: Path
    run_dir: Path
    lock_path: Path
    canonical_project_root: Path | None = None
    execution_role: str | None = None
    execution_feature: str | None = None
    execution_wave: str | None = None
    execution_task: str | None = None
    registered_branch: str | None = None
    registered_head: str | None = None
    registered_git_common_dir: Path | None = None
    execution_identity_valid: bool = True
    execution_identity_reason: str | None = None

    @property
    def run_json_path(self) -> Path:
        return self.run_dir / "run.json"

    @property
    def current_state_path(self) -> Path:
        return self.run_dir / "current-state.json"

    @property
    def feature_queue_path(self) -> Path:
        return self.run_dir / "feature-queue.json"

    @property
    def fingerprints_path(self) -> Path:
        return self.run_dir / "fingerprints.json"

    @property
    def steering_path(self) -> Path:
        return self.run_dir / "STEERING.md"

    @property
    def permissions_dir(self) -> Path:
        return self.run_dir / "permissions"

    @property
    def plans_dir(self) -> Path:
        return self.run_dir / "plans"

    @property
    def evidence_dir(self) -> Path:
        return self.run_dir / "evidence"

    @property
    def history_dir(self) -> Path:
        return self.run_dir / "history"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def resolve_path(path: Path | str, cwd: Path | None = None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and cwd is not None:
        candidate = cwd / candidate
    return candidate.resolve(strict=False)


def is_inside(path: Path | str, root: Path | str) -> bool:
    try:
        resolve_path(path).relative_to(resolve_path(root))
        return True
    except ValueError:
        return False


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        raise
    if isinstance(value, dict):
        return value
    return {}


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(value)
    payload.setdefault("created_at", now_iso())
    with path.open("a", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def run_git(cwd: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def resolve_project_root(cwd: Path | str) -> Path:
    resolved = resolve_path(cwd)
    git_root = run_git(resolved, ["rev-parse", "--show-toplevel"])
    if git_root:
        return resolve_path(git_root)
    return resolved


def detect_current_branch(project_root: Path) -> str:
    return run_git(project_root, ["branch", "--show-current"])


def detect_default_identity(project_root: Path) -> tuple[str, str]:
    remote = run_git(project_root, ["ls-remote", "--symref", "origin", "HEAD"])
    symbolic = [
        line.removeprefix("ref: ").split("\t", 1)[0]
        for line in remote.splitlines()
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD")
    ]
    heads = [
        line.split("\t", 1)[0]
        for line in remote.splitlines()
        if line.endswith("\tHEAD")
        and re.fullmatch(r"[0-9a-f]{40,64}", line.split("\t", 1)[0])
    ]
    if len(symbolic) == 1 and len(heads) == 1:
        return symbolic[0].removeprefix("refs/heads/"), heads[0]
    return "", ""


def detect_default_branch(project_root: Path) -> str:
    return detect_default_identity(project_root)[0]


def git_head(project_root: Path) -> str:
    return run_git(project_root, ["rev-parse", "HEAD"])


def git_common_dir(project_root: Path) -> Path | None:
    value = run_git(project_root, ["rev-parse", "--git-common-dir"])
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return resolve_path(candidate)


def staged_files(project_root: Path) -> list[str]:
    output = run_git(project_root, ["diff", "--cached", "--name-only", "-z"])
    if not output:
        return []
    return [part for part in output.split("\x00") if part]


def staged_diff(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--no-ext-diff"],
            cwd=str(project_root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _run_dir_from_lock(project_dir: Path, lock: dict[str, Any]) -> Path | None:
    run_id = str(lock.get("run_id") or "")
    if run_id:
        run_dir = project_dir / run_id
        if run_dir.exists():
            return run_dir
    active_run = load_json(project_dir / "active-run.json")
    active_id = str((active_run or {}).get("run_id") or "")
    if active_id and (project_dir / active_id).exists():
        return project_dir / active_id
    active_dir = project_dir / "active"
    if active_dir.exists():
        return active_dir
    if run_id:
        return project_dir / run_id
    return None


def _execution_registration(
    run_dir: Path, cwd_path: Path
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for coordinator_path in sorted((run_dir / "execution").glob("*/coordinator.json")):
        try:
            coordinator = load_json(coordinator_path) or {}
        except json.JSONDecodeError:
            continue
        if coordinator.get("schema") != "agentic-sdlc/execution-coordinator-v7":
            continue
        integration_path = coordinator.get("integration_worktree")
        if integration_path and is_inside(cwd_path, str(integration_path)):
            return coordinator, {
                "role": "integration",
                "feature_id": str(
                    coordinator.get("feature_id") or coordinator_path.parent.name
                ),
                "wave_id": None,
                "task_id": None,
                "worktree": str(integration_path),
                "branch": str(coordinator.get("integration_branch") or ""),
                "head": str(coordinator.get("integration_head") or ""),
                "git_common_dir": str(coordinator.get("git_common_dir") or ""),
            }
        assignments_root = coordinator_path.parent / "assignments"
        for assignment_path in sorted(assignments_root.glob("*/*.json")):
            try:
                assignment = load_json(assignment_path) or {}
            except json.JSONDecodeError:
                continue
            if assignment.get("schema") != "agentic-sdlc/worker-assignment-v4":
                continue
            worker_path = assignment.get("worktree")
            if not worker_path or not is_inside(cwd_path, str(worker_path)):
                continue
            expected_head = str(assignment.get("base_head") or "")
            result_path = (
                coordinator_path.parent
                / "results"
                / str(assignment.get("wave_id") or "")
                / f"{assignment.get('task_id')}.json"
            )
            try:
                result = load_json(result_path) or {}
            except json.JSONDecodeError:
                result = {}
            if result.get("commit"):
                expected_head = str(result["commit"])
            return coordinator, {
                "role": "worker",
                "feature_id": str(assignment.get("feature_id") or ""),
                "wave_id": str(assignment.get("wave_id") or ""),
                "task_id": str(assignment.get("task_id") or ""),
                "worktree": str(worker_path),
                "branch": str(assignment.get("branch") or ""),
                "head": expected_head,
                "git_common_dir": str(assignment.get("git_common_dir") or ""),
            }
    return None


def _registered_identity(
    cwd_path: Path, registration: dict[str, Any]
) -> tuple[Path, bool, str | None]:
    expected_root = resolve_path(str(registration["worktree"]))
    actual_root = resolve_project_root(cwd_path)
    if actual_root != expected_root:
        return expected_root, False, "registered worktree root does not match Git root"
    actual_common = git_common_dir(actual_root)
    expected_common_raw = str(registration.get("git_common_dir") or "")
    expected_common = resolve_path(expected_common_raw) if expected_common_raw else None
    if (
        actual_common is None
        or expected_common is None
        or actual_common != expected_common
    ):
        return actual_root, False, "registered Git common directory changed"
    if detect_current_branch(actual_root) != registration.get("branch"):
        return actual_root, False, "registered branch changed"
    if git_head(actual_root) != registration.get("head"):
        return actual_root, False, "registered HEAD changed"
    return actual_root, True, None


def load_active_run(cwd: Path | str, codex_home: Path = CODEX_HOME) -> ActiveRun | None:
    cwd_path = resolve_path(cwd)
    runs_root = codex_home.expanduser() / "sdlc-runs"
    if not runs_root.exists():
        return None
    for lock_path in sorted(runs_root.glob("*/active.lock")):
        try:
            lock = load_json(lock_path)
        except json.JSONDecodeError:
            continue
        if not lock:
            continue
        project_root_raw = lock.get("project_root")
        if not project_root_raw:
            continue
        project_root = resolve_path(str(project_root_raw))
        project_dir = lock_path.parent
        run_dir = _run_dir_from_lock(project_dir, lock)
        if run_dir is None:
            continue
        project_id = str(lock.get("project_id") or project_dir.name)
        run_id = str(lock.get("run_id") or run_dir.name)
        if is_inside(cwd_path, project_root):
            return ActiveRun(
                project_id=project_id,
                project_root=project_root,
                run_id=run_id,
                project_dir=project_dir,
                run_dir=run_dir,
                lock_path=lock_path,
                canonical_project_root=project_root,
                execution_role="project",
            )
        found = _execution_registration(run_dir, cwd_path)
        if found is None:
            if is_inside(cwd_path, run_dir / "worktrees"):
                return ActiveRun(
                    project_id=project_id,
                    project_root=resolve_project_root(cwd_path),
                    run_id=run_id,
                    project_dir=project_dir,
                    run_dir=run_dir,
                    lock_path=lock_path,
                    canonical_project_root=project_root,
                    execution_role="unregistered",
                    execution_identity_valid=False,
                    execution_identity_reason=(
                        "private SDLC worktree is not registered in execution state"
                    ),
                )
            continue
        _coordinator, registration = found
        registered_root, identity_valid, identity_reason = _registered_identity(
            cwd_path, registration
        )
        return ActiveRun(
            project_id=project_id,
            project_root=registered_root,
            run_id=run_id,
            project_dir=project_dir,
            run_dir=run_dir,
            lock_path=lock_path,
            canonical_project_root=project_root,
            execution_role=str(registration["role"]),
            execution_feature=str(registration["feature_id"]),
            execution_wave=registration.get("wave_id"),
            execution_task=registration.get("task_id"),
            registered_branch=str(registration["branch"]),
            registered_head=str(registration["head"]),
            registered_git_common_dir=resolve_path(str(registration["git_common_dir"])),
            execution_identity_valid=identity_valid,
            execution_identity_reason=identity_reason,
        )
    return None


def load_active_state(
    cwd: Path | str,
) -> tuple[ActiveRun | None, dict[str, Any], dict[str, Any], dict[str, Any]]:
    active = load_active_run(cwd)
    if active is None:
        return None, {}, {}, {}
    run_state = load_json(active.run_json_path) or {}
    current_state = load_json(active.current_state_path) or {}
    feature_queue = load_json(active.feature_queue_path) or {}
    return active, run_state, current_state, feature_queue


def hash_state(paths: list[Path], extra: list[str] | None = None) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        if path.is_file():
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"<unreadable>")
        elif path.is_dir():
            for child in sorted(p for p in path.rglob("*") if p.is_file()):
                try:
                    stat = child.stat()
                    rel = str(child.relative_to(path))
                    digest.update(rel.encode("utf-8", errors="replace"))
                    digest.update(str(stat.st_mtime_ns).encode("ascii"))
                    digest.update(str(stat.st_size).encode("ascii"))
                except OSError:
                    continue
        else:
            digest.update(b"<missing>")
        digest.update(b"\0")
    for item in extra or []:
        digest.update(item.encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()
