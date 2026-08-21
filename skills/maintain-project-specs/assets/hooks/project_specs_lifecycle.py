#!/usr/bin/env python3
"""Codex lifecycle guardrails for canonical project specifications."""

from __future__ import annotations

import fcntl
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, NamedTuple


STATE_SCHEMA = "maintain-project-specs.lifecycle.v1"
CONFIG_SCHEMA = "maintain-project-specs.project.v1"
PHASES = {
    "planning-required",
    "planned",
    "implementation-open",
    "reconciliation-required",
    "seal-armed",
    "sealed",
    "waived",
}
STATE_KEYS = {
    "schema",
    "project_scope",
    "git_head_at_prompt",
    "turn_sha256",
    "phase",
    "receipt_sha256",
    "requirements_sha256",
    "design_sha256",
    "rules_path",
    "rules_sha256",
    "project_instructions_state_sha256",
    "project_instructions_reload_required",
    "write_epoch",
    "planned_write_epoch",
    "waiver",
}
RULES_NAME = "pending-project-rules.md"
MAX_STATE_BYTES = 64 * 1024
MUTATING_TOOLS = {"apply_patch", "Edit", "Write"}
READ_ONLY_COMMANDS = {
    "basename",
    "cat",
    "cut",
    "dirname",
    "du",
    "file",
    "find",
    "grep",
    "head",
    "jq",
    "ls",
    "nl",
    "pwd",
    "readlink",
    "rg",
    "stat",
    "tail",
    "test",
    "wc",
    "which",
}
READ_ONLY_GIT_COMMANDS = {
    "cat-file",
    "diff",
    "log",
    "ls-files",
    "ls-tree",
    "rev-list",
    "rev-parse",
    "show",
    "status",
}
UNSAFE_GIT_READ_OPTIONS = {
    "--ext-diff",
    "--filters",
    "--open-files-in-pager",
    "--output",
    "--textconv",
}
UNSAFE_RG_OPTIONS = {"--hostname-bin", "--pre", "--search-zip", "-z"}
MUTATING_GIT_COMMANDS = {
    "add",
    "am",
    "apply",
    "bisect",
    "branch",
    "checkout",
    "cherry-pick",
    "clean",
    "clone",
    "commit",
    "fetch",
    "gc",
    "init",
    "merge",
    "mv",
    "pull",
    "push",
    "rebase",
    "remote",
    "reset",
    "restore",
    "revert",
    "rm",
    "stash",
    "submodule",
    "switch",
    "tag",
    "worktree",
}
READ_ONLY_MCP_METHOD_RE = re.compile(
    r"^(?:fetch|get|inspect|list|open|query|read|search|view)(?:_|$)"
)
MUTATING_MCP_METHOD_RE = re.compile(
    r"(?:^|_)(?:add|apply|create|delete|deploy|execute|move|patch|publish|remove|run|send|set|trigger|update|upload|write)(?:_|$)"
)
COORDINATOR_SHELL_CONTROL_RE = re.compile(r"[;&|<>`\n]|\$\(")
COORDINATOR_BINDING_REASON = (
    "The coordinator command is malformed or not bound to the current lifecycle "
    "session. Use one uncomposed canonical coordinator command with the hook "
    "interpreter, exact current selected-project and session paths, and every "
    "documented action flag. Project-instructions inspect also requires explicit "
    "--codex-home and an absolute current-session --output path."
)
COMMIT_HELPER_SHA256 = (
    "a52b56192afb87c31c9206124460b010b75e4b692480ee5733c17af538ce5ba6"
)
COMMIT_BINDING_REASON = (
    "The commit transaction command is malformed or not bound to the current "
    "explicit $commit authorization. Use one uncomposed canonical commit helper "
    "command with the hook interpreter and exact repository, session, "
    "authorization, claim, token, reviewed-commit, and reviewed-tree bindings."
)
FILE_MUTATION_COMMANDS = {
    "apply_patch",
    "chmod",
    "chown",
    "cp",
    "install",
    "ln",
    "mkdir",
    "mv",
    "patch",
    "rm",
    "rmdir",
    "tee",
    "touch",
    "truncate",
}
EFFECT_NON_MATERIAL = "non-material"
EFFECT_PROJECT = "project"
EFFECT_EXTERNAL = "external"
EFFECT_MIXED = "mixed"
EFFECT_AMBIGUOUS = "ambiguous"
EFFECT_CONTROL_PLANE = "protected-control-plane"


class ShellParse(NamedTuple):
    """Quote-aware bounded shell structure used for lifecycle classification."""

    segments: tuple[tuple[str, ...], ...]
    write_control: bool
    detached: bool
    dynamic: bool


class EffectAnalysis(NamedTuple):
    """One shared PreToolUse/PostToolUse material-effect classification."""

    kind: str
    targets: tuple[Path, ...] = ()


def _valid_state(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != STATE_KEYS:
        return False
    digest_fields = (
        "receipt_sha256",
        "requirements_sha256",
        "design_sha256",
        "rules_sha256",
        "project_instructions_state_sha256",
    )
    planned = value.get("planned_write_epoch")
    reload_required = value.get("project_instructions_reload_required")
    return (
        value.get("schema") == STATE_SCHEMA
        and value.get("phase") in PHASES
        and isinstance(value.get("project_scope"), str)
        and re.fullmatch(r"[0-9a-f]{40,64}", str(value.get("git_head_at_prompt")))
        is not None
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("turn_sha256"))) is not None
        and all(
            item is None or re.fullmatch(r"[0-9a-f]{64}", str(item)) is not None
            for item in (value.get(field) for field in digest_fields)
        )
        and type(value.get("write_epoch")) is int
        and int(value["write_epoch"]) >= 0
        and (planned is None or (type(planned) is int and int(planned) >= 0))
        and value.get("rules_path") in {None, RULES_NAME}
        and (value.get("rules_path") is None) == (value.get("rules_sha256") is None)
        and (
            value.get("phase")
            not in {"planned", "implementation-open", "seal-armed", "sealed"}
            or value.get("rules_path") == RULES_NAME
        )
        and (reload_required is None or type(reload_required) is bool)
        and value.get("waiver")
        in {
            None,
            "documentation-only",
            "read-only",
            "non-project",
            "project-policy",
            "task-worker-delegated",
        }
        and (value.get("phase") == "waived") == (value.get("waiver") is not None)
        and (
            value.get("phase") not in {"planned", "implementation-open", "seal-armed"}
            or (
                value.get("receipt_sha256") is not None
                and planned == value.get("write_epoch")
            )
        )
        and (
            value.get("phase") != "sealed"
            or (
                value.get("receipt_sha256") is not None
                and value.get("requirements_sha256") is not None
                and value.get("design_sha256") is not None
                and value.get("project_instructions_state_sha256") is not None
                and type(reload_required) is bool
                and planned == value.get("write_epoch")
            )
        )
    )


class HookError(RuntimeError):
    """Fail-closed applicable hook error."""


class NonProjectScope(HookError):
    """Valid cwd that is outside the Git-bound project lifecycle."""


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _has_git_marker(cwd: Path) -> bool:
    current = cwd
    while True:
        try:
            (current / ".git").lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise HookError("Git worktree marker could not be inspected") from error
        else:
            return True
        if current.parent == current:
            return False
        current = current.parent


def _git_root(cwd: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HookError("project scope could not be inspected with Git") from error
    if completed.returncode != 0:
        if _has_git_marker(cwd):
            raise HookError("Git worktree marker exists but its scope is invalid")
        return None
    value = completed.stdout.strip()
    if not value:
        raise HookError("Git returned an empty project scope")
    return Path(value).resolve()


def _project(cwd_value: object) -> tuple[Path, Path, str]:
    if not isinstance(cwd_value, str) or not cwd_value:
        raise HookError("hook payload has no cwd")
    raw = Path(os.path.abspath(Path(cwd_value).expanduser()))
    if raw.is_symlink() or not raw.is_dir():
        raise HookError("selected project cwd is unsafe")
    project = raw.resolve()
    git_root = _git_root(project)
    if git_root is None:
        raise NonProjectScope("project scope is not in a Git worktree")
    try:
        relative = project.relative_to(git_root)
    except ValueError as error:
        raise HookError("selected project escaped its Git worktree") from error
    scope = "." if relative == Path(".") else relative.as_posix()
    return project, git_root, scope


def _disabled(project: Path) -> bool:
    policy_path = project / ".codex" / "project-specs.json"
    try:
        policy_path.lstat()
    except FileNotFoundError:
        return False
    try:
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "ls-files",
                "--error-unmatch",
                "--",
                ".codex/project-specs.json",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HookError("project spec policy tracking could not be verified") from error
    if tracked.returncode != 0:
        raise HookError("project spec policy must be tracked in Git")
    raw = _read_regular(policy_path, "project spec policy", 4096)
    git_root = _git_root(project)
    relative = policy_path.relative_to(git_root).as_posix()
    try:
        committed = subprocess.run(
            ["git", "-C", str(git_root), "show", f"HEAD:{relative}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HookError(
            "committed project spec policy could not be verified"
        ) from error
    if committed.returncode != 0 or committed.stdout != raw:
        raise HookError("project spec policy must match its committed Git blob")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HookError("project spec policy is invalid") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "mode", "scope"}
        or value.get("schema") != CONFIG_SCHEMA
        or value.get("mode") not in {"managed", "disabled"}
        or value.get("scope") != "."
    ):
        raise HookError("project spec policy contract is invalid")
    return value["mode"] == "disabled"


def _segment(value: object) -> str:
    raw = str(value)
    if re.fullmatch(r"[A-Za-z0-9._-]{1,96}", raw) and raw not in {".", ".."}:
        return raw
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")[:48] or "session"
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def _state_path(git_root: Path, session_id: object) -> Path:
    workspace = f"{_segment(git_root.name)}-{hashlib.sha256(str(git_root).encode()).hexdigest()[:12]}"
    return (
        _codex_home()
        / "project-specs"
        / workspace
        / _segment(session_id)
        / "lifecycle.json"
    )


def _unfinished_session_phases(git_root: Path, scope: str) -> list[str]:
    workspace = _state_path(git_root, "audit").parent.parent
    try:
        metadata = workspace.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_mode & 0o077
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise HookError("project spec lifecycle workspace is unsafe")
        entries = list(workspace.iterdir())
    except FileNotFoundError:
        return []
    if len(entries) > 128:
        raise HookError("project spec lifecycle audit exceeded its bounded session set")
    phases: list[str] = []
    for entry in entries:
        metadata = entry.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_mode & 0o077
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise HookError("project spec lifecycle audit found an unsafe session")
        state = _load(entry / "lifecycle.json")
        if (
            state is not None
            and state.get("project_scope") == scope
            and state.get("phase") not in {"sealed", "waived"}
        ):
            phases.append(str(state.get("phase")))
    return phases


def _secure_parent(path: Path) -> None:
    home = _codex_home()
    if home.resolve(strict=False) != home:
        raise HookError("CODEX_HOME must not use symbolic links")
    try:
        home.mkdir(mode=0o700)
    except FileExistsError:
        pass
    home_metadata = home.lstat()
    if stat.S_ISLNK(home_metadata.st_mode) or not stat.S_ISDIR(home_metadata.st_mode):
        raise HookError("CODEX_HOME is unsafe")
    private_root = home / "project-specs"
    try:
        relative = path.relative_to(private_root)
    except ValueError as error:
        raise HookError("project spec state escaped its private root") from error
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
            raise HookError("project spec state directory is unsafe")
        os.chmod(current, 0o700)


def _read_regular(path: Path, label: str, max_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise HookError(f"{label} is missing") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > max_bytes
        or metadata.st_mode & 0o022
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise HookError(f"{label} is unsafe")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise HookError(f"{label} changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HookError(f"{label} is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        before_tuple = (
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_mode,
            opened.st_nlink,
        )
        after_tuple = (
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
            after.st_nlink,
        )
        if after_tuple != before_tuple:
            raise HookError(f"{label} changed while reading")
    finally:
        os.close(descriptor)
    try:
        current = path.lstat()
    except FileNotFoundError as error:
        raise HookError(f"{label} disappeared while reading") from error
    if (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
        current.st_mode,
        current.st_nlink,
    ) != (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_mode,
        opened.st_nlink,
    ):
        raise HookError(f"{label} changed while reading")
    return b"".join(chunks)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > MAX_STATE_BYTES
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise HookError("project spec lifecycle state is unsafe")
    try:
        raw = _read_regular(path, "project spec lifecycle state", MAX_STATE_BYTES)
        value: Any = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HookError("project spec lifecycle state is invalid") from error
    if not _valid_state(value):
        raise HookError("project spec lifecycle state has an invalid schema")
    value["_loaded_sha256"] = hashlib.sha256(raw).hexdigest()
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    expected = value.get("_loaded_sha256")
    if expected is not None and not isinstance(expected, str):
        raise HookError("project spec lifecycle compare state is invalid")
    serialized = dict(value)
    serialized.pop("_loaded_sha256", None)
    if not _valid_state(serialized):
        raise HookError("refusing to write invalid project spec lifecycle state")
    _secure_parent(path.parent)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    parent = os.open(path.parent, directory_flags)
    lock_flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    lock_descriptor: int | None = None
    temporary_name = temporary.name
    descriptor: int | None = None
    try:
        lock_descriptor = os.open(".lifecycle.lock", lock_flags, 0o600, dir_fd=parent)
        lock_metadata = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_nlink != 1
            or (hasattr(os, "getuid") and lock_metadata.st_uid != os.getuid())
        ):
            raise HookError("project spec lifecycle lock is unsafe")
        os.fchmod(lock_descriptor, 0o600)
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        if expected is not None:
            current = _read_regular(
                path, "project spec lifecycle state", MAX_STATE_BYTES
            )
            if hashlib.sha256(current).hexdigest() != expected:
                raise HookError("project spec lifecycle changed before transition")
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        raw = (json.dumps(serialized, indent=2, sort_keys=True) + "\n").encode("utf-8")
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        value.pop("_loaded_sha256", None)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent)
        except FileNotFoundError:
            pass
        if lock_descriptor is not None:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        os.close(parent)


def _record_material_write(path: Path) -> None:
    """Advance the implementation epoch without losing concurrent successes."""
    for _attempt in range(128):
        state = _load(path)
        if state is None:
            raise HookError("material write completed without project lifecycle state")
        if state.get("phase") not in {
            "implementation-open",
            "reconciliation-required",
            "planned",
            "seal-armed",
            "sealed",
        }:
            raise HookError(
                "material write completed outside an implementation lifecycle"
            )
        state["phase"] = "reconciliation-required"
        state["write_epoch"] = int(state.get("write_epoch", 0)) + 1
        try:
            _write(path, state)
        except HookError as error:
            if str(error) != "project spec lifecycle changed before transition":
                raise
            continue
        return
    raise HookError("project spec lifecycle write contention did not converge")


def _record_task_review_correction(path: Path) -> None:
    """Reopen one exact non-project integration waiver for a bound correction."""

    for _attempt in range(128):
        state = _load(path)
        if (
            state is None
            or state.get("phase") != "waived"
            or state.get("waiver") != "non-project"
            or int(state.get("write_epoch", 0)) != 0
        ):
            raise HookError(
                "Task Implementer review correction has no eligible integration waiver"
            )
        state["phase"] = "reconciliation-required"
        state["waiver"] = None
        state["write_epoch"] = 1
        for field in (
            "receipt_sha256",
            "requirements_sha256",
            "design_sha256",
            "rules_path",
            "rules_sha256",
            "planned_write_epoch",
        ):
            state[field] = None
        try:
            _write(path, state)
        except HookError as error:
            if str(error) != "project spec lifecycle changed before transition":
                raise
            continue
        return
    raise HookError(
        "Task Implementer review correction lifecycle transition did not converge"
    )


def _record_task_worker_delegation(path: Path) -> None:
    """Close a worker lifecycle while shared reconciliation stays coordinator-owned."""

    for _attempt in range(128):
        state = _load(path)
        if state is None:
            raise HookError("Task Implementer worker lifecycle state is missing")
        if (
            state.get("phase") == "waived"
            and state.get("waiver") == "task-worker-delegated"
        ):
            return
        if state.get("phase") not in {
            "planned",
            "implementation-open",
            "reconciliation-required",
            "seal-armed",
            "sealed",
        }:
            raise HookError(
                "Task Implementer worker commit completed outside an active lifecycle"
            )
        state["phase"] = "waived"
        state["waiver"] = "task-worker-delegated"
        try:
            _write(path, state)
        except HookError as error:
            if str(error) != "project spec lifecycle changed before transition":
                raise
            continue
        return
    raise HookError("Task Implementer worker lifecycle transition did not converge")


def _open_stop_reconciliation(path: Path) -> dict[str, Any] | None:
    """Enter reconciliation before a Stop-generated continuation runs."""

    for _attempt in range(128):
        state = _load(path)
        if state is None or state.get("phase") not in {
            "implementation-open",
            "reconciliation-required",
        }:
            return state
        stale_fields = (
            "receipt_sha256",
            "requirements_sha256",
            "design_sha256",
            "rules_path",
            "rules_sha256",
            "planned_write_epoch",
        )
        if state.get("phase") == "reconciliation-required" and all(
            state.get(field) is None for field in stale_fields
        ):
            return state
        state["phase"] = "reconciliation-required"
        for field in stale_fields:
            state[field] = None
        try:
            _write(path, state)
        except HookError as error:
            if str(error) != "project spec lifecycle changed before transition":
                raise
            continue
        return state
    raise HookError("project spec lifecycle Stop transition did not converge")


def _turn_hash(value: object) -> str:
    raw = str(value)
    if not raw or len(raw) > 256:
        raise HookError("hook payload has no valid turn_id")
    return hashlib.sha256(raw.encode()).hexdigest()


def _context(event: str, message: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": message,
        }
    }


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _tool(payload: dict[str, Any]) -> tuple[str, Any]:
    return str(payload.get("tool_name") or ""), payload.get("tool_input")


def _command(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        value = tool_input.get("cmd") or tool_input.get("command")
        return (
            value if isinstance(value, str) else json.dumps(tool_input, sort_keys=True)
        )
    return tool_input if isinstance(tool_input, str) else ""


def _git_is_read_only(tokens: list[str]) -> bool:
    if tokens == ["git", "--version"]:
        return True
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-C":
            if index + 1 >= len(tokens):
                return False
            index += 2
            continue
        if token in {"--no-pager", "--paginate"}:
            index += 1
            continue
        if token == "-c":
            if index + 1 >= len(tokens) or "=" not in tokens[index + 1]:
                return False
            key, value = tokens[index + 1].split("=", 1)
            if key not in {"core.fsmonitor", "diff.external"} or value not in {
                "",
                "false",
            }:
                return False
            index += 2
            continue
        break
    if index >= len(tokens):
        return False
    command = tokens[index]
    arguments = tokens[index + 1 :]
    if command == "branch":
        return arguments == ["--show-current"]
    if command == "remote":
        return arguments == ["get-url", "origin"]
    if command == "config":
        return arguments == ["--get", "remote.origin.url"]
    if command == "symbolic-ref":
        return arguments in (
            ["-q", "--short", "HEAD"],
            ["--short", "refs/remotes/origin/HEAD"],
            ["-q", "--short", "refs/remotes/origin/HEAD"],
        )
    if command == "show-ref":
        return (
            len(arguments) in {2, 3}
            and arguments[0] == "--verify"
            and (len(arguments) == 2 or arguments[1] == "--quiet")
            and re.fullmatch(r"refs/[A-Za-z0-9][A-Za-z0-9._/-]{0,240}", arguments[-1])
            is not None
        )
    if command in MUTATING_GIT_COMMANDS or command not in READ_ONLY_GIT_COMMANDS:
        return False
    options = _options_before_delimiter(arguments)
    return not any(
        token.split("=", 1)[0] in UNSAFE_GIT_READ_OPTIONS for token in options
    )


def _options_before_delimiter(arguments: list[str]) -> list[str]:
    try:
        return arguments[: arguments.index("--")]
    except ValueError:
        return arguments


def _argument(tokens: list[str], name: str) -> str | None:
    indices = [index for index, token in enumerate(tokens) if token == name]
    if len(indices) != 1:
        return None
    index = indices[0]
    if index + 1 >= len(tokens):
        return None
    return tokens[index + 1]


def _trusted_python(value: str) -> bool:
    name = Path(value).name
    if re.fullmatch(r"python3(?:\.[0-9]+)?", name) is None:
        return False
    candidate = shutil.which(value) if not Path(value).is_absolute() else value
    if candidate is None:
        return False
    try:
        resolved = Path(candidate).resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return False
        if resolved == Path(sys.executable).resolve(strict=True):
            return True
        expected = shutil.which(name)
        return expected is not None and resolved == Path(expected).resolve(strict=True)
    except OSError:
        return False


def _trusted_named_executable(value: str, name: str) -> bool:
    if Path(value).name != name:
        return False
    expected = shutil.which(name)
    candidate = value if Path(value).is_absolute() else shutil.which(value)
    if expected is None or candidate is None:
        return False
    try:
        return Path(candidate).resolve(strict=True) == Path(expected).resolve(
            strict=True
        )
    except OSError:
        return False


def _user_skills_root() -> Path:
    return Path.home() / ".agents" / "skills"


def _source_skills_root() -> Path | None:
    skill_root = Path(__file__).resolve().parents[2]
    if (
        skill_root.name == "maintain-project-specs"
        and (skill_root / "SKILL.md").is_file()
    ):
        return skill_root.parent
    return None


def _coordinator_path_is_safe(path: Path, skills_root: Path) -> bool:
    expected_root = Path(os.path.abspath(skills_root.expanduser()))
    expected = Path(os.path.abspath(path.expanduser()))
    try:
        relative = expected.relative_to(expected_root)
    except ValueError:
        return False
    current = expected_root
    for part in ("", *relative.parts):
        if part:
            current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(metadata.st_mode):
            return False
        if current == expected:
            return stat.S_ISREG(metadata.st_mode)
        if not stat.S_ISDIR(metadata.st_mode):
            return False
    return False


def _known_coordinator_paths(*, require_safe: bool) -> dict[Path, str]:
    known: dict[Path, str] = {}
    roots = [_user_skills_root()]
    source_root = _source_skills_root()
    if source_root is not None:
        roots.append(source_root)
    for skills_root in roots:
        candidates = {
            skills_root
            / "maintain-project-specs"
            / "scripts"
            / "project_specs.py": "project-specs",
            skills_root
            / "project-agent-instructions"
            / "scripts"
            / "project_agent_instructions.py": "project-instructions",
        }
        for candidate, owner in candidates.items():
            lexical = Path(os.path.abspath(candidate.expanduser()))
            if not require_safe or _coordinator_path_is_safe(lexical, skills_root):
                known[lexical] = owner
    return known


def _known_task_implementer_helpers(*, require_safe: bool) -> set[Path]:
    known: set[Path] = set()
    roots = [_user_skills_root()]
    source_root = _source_skills_root()
    if source_root is not None:
        roots.append(source_root)
    for skills_root in roots:
        candidate = skills_root / "task-implementer" / "scripts" / "prompt_workspace.py"
        lexical = Path(os.path.abspath(candidate.expanduser()))
        if not require_safe or _coordinator_path_is_safe(lexical, skills_root):
            known.add(lexical)
    return known


def _known_commit_helpers(*, require_safe: bool) -> set[Path]:
    roots = [_user_skills_root()]
    source_root = _source_skills_root()
    if source_root is not None:
        roots.append(source_root)
    helpers: set[Path] = set()
    for skills_root in roots:
        candidate = skills_root / "commit" / "scripts" / "commit_transaction.py"
        lexical = Path(os.path.abspath(candidate.expanduser()))
        if require_safe and not _coordinator_path_is_safe(lexical, skills_root):
            continue
        if require_safe:
            try:
                raw = _read_regular(lexical, "commit transaction helper", 256 * 1024)
            except HookError:
                continue
            if hashlib.sha256(raw).hexdigest() != COMMIT_HELPER_SHA256:
                continue
        helpers.add(lexical)
    return helpers


def _commit_command_shape(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return (
        len(tokens) >= 3
        and re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", Path(tokens[0]).name)
        is not None
        and Path(tokens[1]).name == "commit_transaction.py"
        and tokens[2] in {"prepare", "execute", "review"}
    )


def _git_common_dir(git_root: Path) -> Path:
    try:
        completed = subprocess.run(
            ["git", "-C", str(git_root), "rev-parse", "--git-common-dir"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HookError(
            "Git common-directory identity could not be inspected"
        ) from error
    if completed.returncode != 0 or not completed.stdout.strip():
        raise HookError("Git common-directory identity is unavailable")
    value = Path(completed.stdout.strip())
    if not value.is_absolute():
        value = git_root / value
    return value.resolve(strict=True)


def _commit_private_paths(git_root: Path, session_id: object) -> tuple[Path, Path]:
    common_dir = _git_common_dir(git_root)
    repo_key = hashlib.sha256(str(common_dir).encode()).hexdigest()[:24]
    session_key = hashlib.sha256(str(session_id).encode()).hexdigest()[:24]
    ref = _git_symbolic_ref(git_root)
    ref_key = hashlib.sha256(ref.encode()).hexdigest()[:24]
    root = (
        Path(os.path.abspath(_codex_home())).resolve(strict=False)
        / "commit-transactions"
        / repo_key
    )
    return (
        root / "sessions" / session_key / "authorization.json",
        root / "claims" / f"{ref_key}.json",
    )


def _git_symbolic_ref(git_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(git_root), "symbolic-ref", "-q", "HEAD"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HookError("Git ref identity could not be inspected") from error
    value = completed.stdout.strip()
    if (
        completed.returncode != 0
        or re.fullmatch(r"refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]{0,240}", value) is None
    ):
        raise HookError("Git ref identity is unavailable")
    return value


def _git_execution_arguments(
    tokens: list[str], cwd: Path, git_root: Path
) -> tuple[str, list[str]] | None:
    """Return one direct Git subcommand bound to the selected repository."""

    if not tokens or not _trusted_named_executable(tokens[0], "git"):
        return None
    index = 1
    execution_cwd = cwd
    if index < len(tokens) and tokens[index] == "-C":
        if index + 1 >= len(tokens):
            return None
        candidate = Path(tokens[index + 1]).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            execution_cwd = Path(os.path.abspath(candidate)).resolve(strict=True)
        except OSError:
            return None
        index += 2
    if index >= len(tokens) or tokens[index].startswith("-"):
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(execution_cwd), "rev-parse", "--show-toplevel"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        discovered = Path(completed.stdout.strip()).resolve(strict=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or discovered != git_root.resolve(strict=True):
        return None
    return tokens[index], tokens[index + 1 :]


def _origin_remote_is_fixed(git_root: Path, *, push: bool) -> bool:
    arguments = ["git", "-C", str(git_root), "remote", "get-url"]
    if push:
        arguments.append("--push")
    arguments.extend(("--all", "origin"))
    try:
        completed = subprocess.run(
            arguments,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    urls = completed.stdout.splitlines()
    if completed.returncode != 0 or len(urls) != 1:
        return False
    url = urls[0]
    if not url or any(character.isspace() for character in url):
        return False
    host = (
        r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"
        r"|\[[0-9A-Fa-f:.]+\])"
    )
    port = r"(?::[0-9]{1,5})?"
    if re.fullmatch(rf"https://{host}{port}/[^\s]+", url):
        return True
    if re.fullmatch(rf"ssh://(?:[A-Za-z0-9._-]+@)?{host}{port}/[^\s]+", url):
        return True
    scp = re.fullmatch(
        rf"(?:(?P<user>[A-Za-z0-9._-]+)@)?(?P<host>{host}):"
        r"(?P<path>[A-Za-z0-9._~/+][^\s]*)",
        url,
    )
    return bool(
        scp
        and scp.group("path").strip("/")
        and (scp.group("user") or "." in scp.group("host"))
    )


def _git_hooks_are_absent(git_root: Path, names: tuple[str, ...]) -> bool:
    for name in names:
        try:
            completed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(git_root),
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-path",
                    f"hooks/{name}",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        values = completed.stdout.splitlines()
        if completed.returncode != 0 or len(values) != 1:
            return False
        hook = Path(values[0])
        if not hook.is_absolute():
            return False
        try:
            if hook.is_file() and os.access(hook, os.X_OK):
                return False
        except OSError:
            return False
    return True


def _bounded_git_effect(tokens: list[str], cwd: Path, git_root: Path) -> str | None:
    """Classify the exact remote Git shapes owned by commit-push."""

    invocation = _git_execution_arguments(tokens, cwd, git_root)
    if invocation is None:
        return None
    command, arguments = invocation
    try:
        branch = _git_symbolic_ref(git_root).removeprefix("refs/heads/")
    except HookError:
        return None
    source_ref = f"refs/heads/{branch}"
    tracking_ref = f"refs/remotes/origin/{branch}"
    if command == "ls-remote" and arguments in (
        ["--symref", "origin", "HEAD"],
        ["--exit-code", "--branches", "origin", source_ref],
    ):
        return (
            EFFECT_EXTERNAL if _origin_remote_is_fixed(git_root, push=False) else None
        )
    if command == "fetch" and arguments == [
        "--no-write-fetch-head",
        "--no-auto-maintenance",
        "--no-write-commit-graph",
        "--no-tags",
        "origin",
        f"{source_ref}:{tracking_ref}",
    ]:
        return (
            EFFECT_EXTERNAL
            if _origin_remote_is_fixed(git_root, push=False)
            and _git_hooks_are_absent(git_root, ("reference-transaction",))
            else None
        )
    if command != "push" or arguments not in (
        ["origin", f"HEAD:{branch}"],
        ["-u", "origin", f"HEAD:{branch}"],
        ["--set-upstream", "origin", f"HEAD:{branch}"],
    ):
        return None
    if not _origin_remote_is_fixed(git_root, push=True):
        return None
    return (
        EFFECT_EXTERNAL
        if _git_hooks_are_absent(git_root, ("pre-push", "reference-transaction"))
        else None
    )


def _exact_flag_map(
    tokens: list[str], *, boolean_flags: set[str]
) -> dict[str, str | bool] | None:
    values: dict[str, str | bool] = {}
    index = 3
    while index < len(tokens):
        flag = tokens[index]
        if not flag.startswith("--") or flag in values:
            return None
        if flag in boolean_flags:
            values[flag] = True
            index += 1
            continue
        if index + 1 >= len(tokens):
            return None
        values[flag] = tokens[index + 1]
        index += 2
    return values


def _commit_owner_matches(
    evidence: dict[str, Any], git_root: Path, session_id: object, *, claim: bool
) -> bool:
    owner_field = "authorization_owner" if claim else "owner"
    owner = evidence.get(owner_field)
    path_value = evidence.get("owner_evidence_path")
    digest = evidence.get("owner_evidence_sha256")
    if owner == "direct":
        return path_value is None and digest is None
    if (
        owner != "task-implementer"
        or not isinstance(path_value, str)
        or not Path(path_value).is_absolute()
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or digest != evidence.get("turn_sha256")
    ):
        return False
    plane_path = Path(path_value)
    task_root = (
        Path(os.path.abspath(_codex_home())).resolve(strict=False) / "task-implementer"
    )
    if not _private_path_is_safe(plane_path, task_root):
        return False
    try:
        plane: Any = json.loads(
            _read_regular(
                plane_path, "Task Implementer commit evidence", MAX_STATE_BYTES
            )
        )
        head = subprocess.check_output(
            ["git", "-C", str(git_root), "rev-parse", "HEAD"],
            text=True,
            timeout=10,
        ).strip()
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        OSError,
        subprocess.SubprocessError,
    ):
        return False
    if not (
        isinstance(plane, dict)
        and plane.get("state") == "running"
        and plane.get("worker_session_sha256")
        == hashlib.sha256(str(session_id).encode()).hexdigest()
        and plane.get("assignment_sha256") == evidence.get("turn_sha256")
    ):
        return False
    if claim and evidence.get("state") in {"REVIEW_REQUIRED", "COMMITTED"}:
        commit_head = evidence.get("commit_head")
        base_head = evidence.get("base_head")
        try:
            parents = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(git_root),
                    "rev-list",
                    "--parents",
                    "-n",
                    "1",
                    str(commit_head),
                ],
                text=True,
                timeout=10,
            ).split()
        except (OSError, subprocess.SubprocessError):
            return False
        return (
            plane.get("base_commit") == base_head
            and head == commit_head
            and parents == [commit_head, base_head]
        )
    return plane.get("base_commit") == head


def _bound_commit_command(
    command: str,
    git_root: Path,
    session_id: object,
    *,
    completed: bool = False,
) -> tuple[str, Path, str, str] | None:
    parsed = _parse_shell(command)
    if (
        parsed is None
        or len(parsed.segments) != 1
        or parsed.write_control
        or parsed.detached
        or parsed.dynamic
    ):
        return None
    tokens = list(parsed.segments[0])
    if (
        len(tokens) < 3
        or not _trusted_python(tokens[0])
        or Path(os.path.abspath(Path(tokens[1]).expanduser()))
        not in _known_commit_helpers(require_safe=True)
        or tokens[2] not in {"prepare", "execute", "review"}
    ):
        return None
    action = tokens[2]
    flags = _exact_flag_map(
        tokens,
        boolean_flags={"--allow-default-branch"} if action == "prepare" else set(),
    )
    if flags is None:
        return None
    if action == "prepare":
        required = {"--repo-root", "--session-id", "--authorization", "--claim"}
    elif action == "execute":
        required = {
            "--repo-root",
            "--session-id",
            "--claim",
            "--token",
            "--reviewed-tree",
            "--message",
        }
    else:
        required = {
            "--repo-root",
            "--session-id",
            "--claim",
            "--token",
            "--reviewed-commit",
            "--reviewed-tree",
        }
    allowed = required | ({"--allow-default-branch"} if action == "prepare" else set())
    if set(flags) - allowed or not required.issubset(flags):
        return None
    if flags["--session-id"] != str(session_id):
        return None
    repo_value = Path(str(flags["--repo-root"])).expanduser()
    if (
        not repo_value.is_absolute()
        or Path(os.path.abspath(repo_value)).resolve(strict=False) != git_root
    ):
        return None
    authorization_path, claim_path = _commit_private_paths(git_root, session_id)
    if not _absolute_argument_matches(tokens, "--claim", claim_path):
        return None
    evidence_path = authorization_path if action == "prepare" else claim_path
    evidence_flag = "--authorization" if action == "prepare" else "--claim"
    if not _absolute_argument_matches(tokens, evidence_flag, evidence_path):
        return None
    if not _private_path_is_safe(
        evidence_path,
        Path(os.path.abspath(_codex_home())).resolve(strict=False)
        / "commit-transactions",
    ):
        return None
    try:
        evidence: Any = json.loads(
            _read_regular(evidence_path, f"commit {action} evidence", MAX_STATE_BYTES)
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(evidence, dict):
        return None
    session_sha256 = hashlib.sha256(str(session_id).encode()).hexdigest()
    bound_owner: str
    if action == "prepare":
        authorization_owner = evidence.get("owner")
        authorization_state = (
            "CONSUMED"
            if completed and authorization_owner == "direct"
            else "AUTHORIZED"
        )
        expected_identity = {
            "schema": "commit-transaction.authorization.v1",
            "state": authorization_state,
            "repo_root": str(git_root),
            "worktree": str(git_root),
            "common_dir": str(_git_common_dir(git_root)),
            "ref": _git_symbolic_ref(git_root),
            "session_sha256": session_sha256,
        }
        if any(
            evidence.get(key) != value for key, value in expected_identity.items()
        ) or bool(flags.get("--allow-default-branch", False)) != evidence.get(
            "allow_default_branch"
        ):
            return None
        if completed:
            if not _private_path_is_safe(
                claim_path,
                Path(os.path.abspath(_codex_home())).resolve(strict=False)
                / "commit-transactions",
            ):
                return None
            try:
                claim: Any = json.loads(
                    _read_regular(
                        claim_path, "completed commit prepare claim", MAX_STATE_BYTES
                    )
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
            normalized_authorization = {**evidence, "state": "AUTHORIZED"}
            authorization_sha256 = hashlib.sha256(
                (
                    json.dumps(normalized_authorization, indent=2, sort_keys=True)
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()
            if (
                not isinstance(claim, dict)
                or claim.get("schema") != "commit-transaction.claim.v1"
                or claim.get("state")
                not in {"PREPARED", "STAGED", "REVIEW_REQUIRED", "COMMITTED"}
                or claim.get("repo_root") != str(git_root)
                or claim.get("worktree") != str(git_root)
                or claim.get("common_dir") != str(_git_common_dir(git_root))
                or claim.get("ref") != _git_symbolic_ref(git_root)
                or claim.get("session_sha256") != session_sha256
                or claim.get("turn_sha256") != evidence.get("turn_sha256")
                or claim.get("authorization_owner") != authorization_owner
                or claim.get("owner_evidence_path")
                != evidence.get("owner_evidence_path")
                or claim.get("owner_evidence_sha256")
                != evidence.get("owner_evidence_sha256")
                or claim.get("authorization_sha256") != authorization_sha256
                or not _commit_owner_matches(claim, git_root, session_id, claim=True)
            ):
                return None
            evidence = claim
        elif not _commit_owner_matches(evidence, git_root, session_id, claim=False):
            return None
        bound_owner = str(authorization_owner)
    elif action == "execute":
        if (
            evidence.get("schema") != "commit-transaction.claim.v1"
            or evidence.get("state") not in {"PREPARED", "STAGED", "COMMITTED"}
            or evidence.get("repo_root") != str(git_root)
            or evidence.get("common_dir") != str(_git_common_dir(git_root))
            or evidence.get("ref") != _git_symbolic_ref(git_root)
            or evidence.get("session_sha256") != session_sha256
            or evidence.get("token_sha256")
            != hashlib.sha256(str(flags.get("--token")).encode()).hexdigest()
            or evidence.get("candidate_tree") != flags.get("--reviewed-tree")
            or not isinstance(flags.get("--message"), str)
            or not str(flags["--message"]).strip()
            or not _commit_owner_matches(evidence, git_root, session_id, claim=True)
        ):
            return None
        bound_owner = str(evidence["authorization_owner"])
    else:
        if (
            evidence.get("schema") != "commit-transaction.claim.v1"
            or evidence.get("state") not in {"REVIEW_REQUIRED", "COMMITTED"}
            or evidence.get("repo_root") != str(git_root)
            or evidence.get("common_dir") != str(_git_common_dir(git_root))
            or evidence.get("ref") != _git_symbolic_ref(git_root)
            or evidence.get("session_sha256") != session_sha256
            or evidence.get("token_sha256")
            != hashlib.sha256(str(flags.get("--token")).encode()).hexdigest()
            or evidence.get("commit_head") != flags.get("--reviewed-commit")
            or evidence.get("commit_tree") != flags.get("--reviewed-tree")
            or not _commit_owner_matches(evidence, git_root, session_id, claim=True)
        ):
            return None
        bound_owner = str(evidence["authorization_owner"])
    return action, claim_path, bound_owner, str(evidence["turn_sha256"])


def _coordinator_command_shape(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if (
        len(tokens) < 3
        or re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", Path(tokens[0]).name) is None
    ):
        return False
    script = Path(os.path.abspath(Path(tokens[1]).expanduser()))
    if script.name == "prompt_workspace.py" and tokens[2] in {
        "coordinator-stage",
        "coordinator-commit",
    }:
        return True
    owner = _known_coordinator_paths(require_safe=False).get(script)
    if owner is None:
        owner = {
            "project_specs.py": "project-specs",
            "project_agent_instructions.py": "project-instructions",
        }.get(script.name)
    actions = (
        {
            "inspect",
            "validate",
            "migrate",
            "recover",
            "start-prompt",
            "plan",
            "open",
            "seal",
            "waive",
        }
        if owner == "project-specs"
        else {"inspect", "render", "apply", "verify"}
    )
    return owner is not None and tokens[2] in actions


def _task_implementer_impact_shape(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return (
        len(tokens) >= 3
        and re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", Path(tokens[0]).name)
        is not None
        and Path(tokens[1]).name == "prompt_workspace.py"
        and tokens[2] == "wave-plan"
    )


def _task_implementer_adapter(
    helper: Path,
    workspace: Path,
    run_id: str,
    kind: str,
    command: str,
) -> dict[str, Any] | None:
    helper = Path(os.path.abspath(helper.expanduser()))
    if (
        helper not in _known_task_implementer_helpers(require_safe=True)
        or not workspace.is_absolute()
        or not _private_path_is_safe(
            workspace,
            Path(os.path.abspath(_codex_home())).resolve(strict=False)
            / "task-implementer",
        )
    ):
        return None
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(helper),
                "lifecycle-authorize",
                "--workspace",
                str(workspace),
                "--run-id",
                run_id,
                "--kind",
                kind,
                "--json",
            ],
            input=command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        if completed.returncode != 0 or len(completed.stdout) > MAX_STATE_BYTES:
            return None
        evidence: Any = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.TimeoutExpired,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None
    if (
        not isinstance(evidence, dict)
        or evidence.get("status") != "authorized"
        or evidence.get("command_sha256")
        != hashlib.sha256(command.encode()).hexdigest()
    ):
        return None
    return evidence


def _task_implementer_coordinator(command: str, project: Path) -> dict[str, Any] | None:
    if not command or COORDINATOR_SHELL_CONTROL_RE.search(command) or "$" in command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if len(tokens) < 3 or tokens[2] not in {
        "validate",
        "inspect",
        "render",
        "coordinator-stage",
        "coordinator-commit",
    }:
        return None
    script = Path(os.path.abspath(Path(tokens[1]).expanduser()))
    if tokens[2] in {"coordinator-stage", "coordinator-commit"}:
        workspace_value = _argument(tokens, "--workspace")
        run_id = _argument(tokens, "--run-id")
        if workspace_value is None or run_id is None:
            return None
        workspace = Path(workspace_value).expanduser()
        evidence = _task_implementer_adapter(
            script, workspace, run_id, "project-instructions", command
        )
        if evidence is None:
            return None
        expected_keys = {
            "status",
            "action",
            "outer_project_root",
            "project_root",
            "command_sha256",
        }
        outer = evidence.get("outer_project_root")
        inner = evidence.get("project_root")
        if (
            set(evidence) != expected_keys
            or evidence.get("action") != tokens[2]
            or not isinstance(outer, str)
            or Path(outer).resolve(strict=False) != project
            or not isinstance(inner, str)
            or not Path(inner).is_absolute()
        ):
            return None
        return evidence
    owner = _known_coordinator_paths(require_safe=True).get(script)
    if (tokens[2] == "validate" and owner != "project-specs") or (
        tokens[2] in {"inspect", "render"} and owner != "project-instructions"
    ):
        return None
    if tokens[2] == "validate":
        output_value = _argument(tokens, "--output")
        if output_value is None:
            return None
        output = Path(output_value).expanduser()
        if not output.is_absolute() or output.name != "project-agent-spec-receipt.json":
            return None
        orchestration = output.parent
    else:
        private_value = _argument(tokens, "--private-root")
        if private_value is None:
            return None
        private_root = Path(private_value).expanduser()
        if (
            not private_root.is_absolute()
            or private_root.name != "project-agent-instructions"
        ):
            return None
        orchestration = private_root.parent
    run_dir = orchestration.parent
    if orchestration.name != "orchestration" or run_dir.parent.name != "runs":
        return None
    workspace = run_dir.parent.parent / "workspace.json"
    task_helper = (
        script.parents[2] / "task-implementer" / "scripts" / "prompt_workspace.py"
    )
    evidence = _task_implementer_adapter(
        task_helper, workspace, run_dir.name, "project-instructions", command
    )
    if evidence is None:
        return None
    expected_keys = {
        "status",
        "action",
        "outer_project_root",
        "project_root",
        "command_sha256",
    }
    outer = evidence.get("outer_project_root")
    inner = evidence.get("project_root")
    if (
        set(evidence) != expected_keys
        or evidence.get("action") != tokens[2]
        or not isinstance(outer, str)
        or Path(outer).resolve(strict=False) != project
        or not isinstance(inner, str)
        or not Path(inner).is_absolute()
    ):
        return None
    return evidence


def _task_implementer_impact(command: str, project: Path) -> dict[str, Any] | None:
    if not command or COORDINATOR_SHELL_CONTROL_RE.search(command) or "$" in command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if len(tokens) < 3 or tokens[2] != "wave-plan":
        return None
    helper = Path(os.path.abspath(Path(tokens[1]).expanduser()))
    workspace_value = _argument(tokens, "--workspace")
    run_id = _argument(tokens, "--run-id")
    if workspace_value is None or run_id is None:
        return None
    workspace = Path(workspace_value).expanduser()
    evidence = _task_implementer_adapter(helper, workspace, run_id, "impact", command)
    if evidence is None:
        return None
    expected_keys = {
        "status",
        "action",
        "outer_project_root",
        "command_sha256",
        "checkpoint_head",
        "review_correction",
    }
    outer = evidence.get("outer_project_root")
    if (
        set(evidence) != expected_keys
        or evidence.get("action") != "wave-plan"
        or not isinstance(outer, str)
        or Path(outer).resolve(strict=False) != project
        or type(evidence.get("review_correction")) is not bool
        or (
            evidence.get("checkpoint_head") is not None
            and (
                not isinstance(evidence["checkpoint_head"], str)
                or re.fullmatch(r"[0-9a-f]{40,64}", evidence["checkpoint_head"]) is None
            )
        )
    ):
        return None
    return evidence


def _task_implementer_commit(command: str, project: Path) -> dict[str, Any] | None:
    """Resolve an exact delegated worker commit without rebinding outer scope."""

    if not command or COORDINATOR_SHELL_CONTROL_RE.search(command) or "$" in command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if len(tokens) < 3 or tokens[2] not in {"prepare", "execute", "review"}:
        return None
    action = tokens[2]
    flags = _exact_flag_map(
        tokens,
        boolean_flags={"--allow-default-branch"} if action == "prepare" else set(),
    )
    if flags is None:
        return None
    worker_session_id = flags.get("--session-id")
    if not isinstance(worker_session_id, str) or not worker_session_id:
        return None
    evidence_value = flags.get("--authorization" if action == "prepare" else "--claim")
    if not isinstance(evidence_value, str):
        return None
    evidence_path = Path(evidence_value).expanduser()
    task_root = (
        Path(os.path.abspath(_codex_home())).resolve(strict=False) / "task-implementer"
    )
    commit_root = (
        Path(os.path.abspath(_codex_home())).resolve(strict=False)
        / "commit-transactions"
    )
    if not evidence_path.is_absolute() or not _private_path_is_safe(
        evidence_path, commit_root
    ):
        return None
    try:
        commit_evidence: Any = json.loads(
            _read_regular(
                evidence_path, "Task Implementer commit evidence", MAX_STATE_BYTES
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(commit_evidence, dict):
        return None
    owner_field = "owner" if action == "prepare" else "authorization_owner"
    plane_value = commit_evidence.get("owner_evidence_path")
    if commit_evidence.get(owner_field) != "task-implementer" or not isinstance(
        plane_value, str
    ):
        return None
    plane_path = Path(plane_value).expanduser()
    if not plane_path.is_absolute() or not _private_path_is_safe(plane_path, task_root):
        return None
    try:
        wave_tasks = plane_path.parents[1]
        orchestration = plane_path.parents[2]
        run_dir = plane_path.parents[3]
    except IndexError:
        return None
    if (
        wave_tasks.name != "tasks"
        or orchestration.name != "orchestration"
        or run_dir.parent.name != "runs"
    ):
        return None
    workspace = run_dir.parent.parent / "workspace.json"
    commit_helper = Path(os.path.abspath(Path(tokens[1]).expanduser()))
    task_helper = (
        commit_helper.parents[2]
        / "task-implementer"
        / "scripts"
        / "prompt_workspace.py"
    )
    evidence = _task_implementer_adapter(
        task_helper, workspace, run_dir.name, "commit", command
    )
    if evidence is None:
        return None
    expected_keys = {
        "status",
        "action",
        "outer_project_root",
        "worker_root",
        "worker_session_id",
        "command_sha256",
    }
    outer = evidence.get("outer_project_root")
    worker = evidence.get("worker_root")
    repo_value = flags.get("--repo-root")
    if (
        set(evidence) != expected_keys
        or evidence.get("action") != action
        or evidence.get("command_sha256")
        != hashlib.sha256(command.encode()).hexdigest()
        or not isinstance(outer, str)
        or Path(outer).resolve(strict=False) != project
        or not isinstance(worker, str)
        or not Path(worker).is_absolute()
        or evidence.get("worker_session_id") != worker_session_id
        or not isinstance(repo_value, str)
        or Path(repo_value).resolve(strict=False) != Path(worker).resolve(strict=False)
    ):
        return None
    return {**evidence, "worker_session_id": worker_session_id}


def _absolute_argument_matches(tokens: list[str], name: str, expected: Path) -> bool:
    value = _argument(tokens, name)
    if value is None:
        return False
    candidate = Path(value).expanduser()
    return candidate.is_absolute() and Path(os.path.abspath(candidate)) == Path(
        os.path.abspath(expected)
    )


def _bound_coordinator_command(
    command: str, project: Path, state_path: Path | None = None
) -> tuple[str, str] | None:
    if not command or COORDINATOR_SHELL_CONTROL_RE.search(command) or "$" in command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if (
        len(tokens) < 3
        or not re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", Path(tokens[0]).name)
        or not _trusted_python(tokens[0])
    ):
        return None
    script = Path(os.path.abspath(Path(tokens[1]).expanduser()))
    owner = _known_coordinator_paths(require_safe=True).get(script)
    if owner is None:
        return None
    allowed = (
        {
            "inspect",
            "validate",
            "migrate",
            "recover",
            "start-prompt",
            "plan",
            "open",
            "seal",
            "waive",
        }
        if owner == "project-specs"
        else {"inspect", "render", "apply", "verify"}
    )
    subcommand = tokens[2]
    if subcommand not in allowed:
        return None
    if owner == "project-specs" or subcommand == "inspect":
        root_value = _argument(tokens, "--project-root")
        if root_value is None:
            return None
        root = Path(os.path.abspath(Path(root_value).expanduser())).resolve(
            strict=False
        )
        if root != project:
            return None
    if state_path is not None:
        session_root = state_path.parent
        private_root = session_root / "project-instructions"
        bindings: dict[tuple[str, str], dict[str, Path]] = {
            ("project-specs", "plan"): {
                "--rules-file": private_root / "rules.md",
                "--render-state-file": private_root / "render-state.json",
                "--project-instructions-private-root": private_root,
            },
            ("project-specs", "seal"): {
                "--project-instructions-state": private_root / "state.json",
                "--project-instructions-private-root": private_root,
            },
            ("project-instructions", "inspect"): {
                "--spec-receipt": session_root / "spec-receipt.json",
                "--runtime-config": session_root / "runtime-config.json",
                "--private-root": private_root,
                "--output": private_root / "manifest.json",
            },
            ("project-instructions", "render"): {
                "--manifest": private_root / "manifest.json",
                "--decision": private_root / "decision.json",
                "--private-root": private_root,
                "--output": private_root / "rules.md",
                "--state": private_root / "render-state.json",
            },
            ("project-instructions", "apply"): {
                "--manifest": private_root / "manifest.json",
                "--decision": private_root / "decision.json",
                "--ownership": private_root / "ownership.json",
                "--state": private_root / "state.json",
                "--private-root": private_root,
            },
            ("project-instructions", "verify"): {
                "--state": private_root / "state.json",
                "--private-root": private_root,
            },
        }
        if any(
            not _absolute_argument_matches(tokens, flag, expected)
            for flag, expected in bindings.get((owner, subcommand), {}).items()
        ):
            return None
        if owner == "project-instructions" and subcommand == "inspect":
            if (
                _argument(tokens, "--spec-owner") != "maintain-project-specs"
                or _argument(tokens, "--requirements") != "docs/requirements.md"
                or _argument(tokens, "--design") != "docs/design.md"
                or not _absolute_argument_matches(tokens, "--codex-home", _codex_home())
            ):
                return None
    if owner == "project-instructions" and subcommand != "inspect":
        evidence_flag = "--state" if subcommand == "verify" else "--manifest"
        evidence_value = _argument(tokens, evidence_flag)
        if evidence_value is None:
            return None
        evidence_path = Path(evidence_value).expanduser()
        if not evidence_path.is_absolute():
            return None
        try:
            evidence: Any = json.loads(
                _read_regular(
                    evidence_path, "project-instructions evidence", MAX_STATE_BYTES
                )
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(evidence, dict) or evidence.get("project_root") != str(
            project
        ):
            return None
    return owner, subcommand


def _verified_apply_state(
    command: str, project: Path, lifecycle_state_path: Path
) -> None:
    try:
        tokens = shlex.split(command)
    except ValueError as error:
        raise HookError("project-instructions apply command is invalid") from error
    state_value = _argument(tokens, "--state")
    private_value = _argument(tokens, "--private-root")
    if state_value is None or private_value is None:
        raise HookError("project-instructions apply evidence is incomplete")
    state_path = Path(state_value).expanduser()
    private_root = Path(private_value).expanduser()
    if not state_path.is_absolute() or not private_root.is_absolute():
        raise HookError("project-instructions apply evidence must use absolute paths")
    expected_root = lifecycle_state_path.parent / "project-instructions"
    if (
        Path(os.path.abspath(state_path)) != expected_root / "state.json"
        or Path(os.path.abspath(private_root)) != expected_root
    ):
        raise HookError(
            "project-instructions apply evidence is not bound to the current lifecycle session"
        )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                tokens[1],
                "verify",
                "--state",
                str(state_path),
                "--private-root",
                str(private_root),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        verified: Any = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise HookError(
            "project-instructions apply verification could not run"
        ) from error
    if (
        completed.returncode != 0
        or not isinstance(verified, dict)
        or verified.get("status") != "ok"
        or verified.get("project_root") != str(project)
    ):
        raise HookError("project-instructions apply did not produce verified state")


def _parse_shell(command: str) -> ShellParse | None:
    raw_segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    write_control = False
    detached = False
    dynamic = False
    index = 0

    def flush() -> None:
        raw = "".join(current).strip()
        if raw:
            raw_segments.append(raw)
        current.clear()

    while index < len(command):
        character = command[index]
        if escaped:
            current.append(character)
            escaped = False
            index += 1
            continue
        if quote == "'":
            current.append(character)
            if character == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            current.append(character)
            if character == "\\":
                escaped = True
            elif character == '"':
                quote = None
            elif character == "`":
                write_control = True
                dynamic = True
            elif character == "$":
                dynamic = True
                if index + 1 < len(command) and command[index + 1] == "(":
                    write_control = True
            index += 1
            continue
        if character == "\\":
            current.append(character)
            escaped = True
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            current.append(character)
            index += 1
            continue
        if character in {";", "|", "&", "\n"}:
            flush()
            if character == "&":
                if index + 1 < len(command) and command[index + 1] == "&":
                    index += 1
                else:
                    detached = True
            elif (
                character == "|"
                and index + 1 < len(command)
                and command[index + 1] == "|"
            ):
                index += 1
            index += 1
            continue
        if character == ">":
            write_control = True
        elif character == "`":
            write_control = True
            dynamic = True
        elif character == "$":
            dynamic = True
            if index + 1 < len(command) and command[index + 1] == "(":
                write_control = True
        current.append(character)
        index += 1
    if quote is not None or escaped:
        return None
    flush()
    segments: list[tuple[str, ...]] = []
    try:
        for raw in raw_segments:
            lexer = shlex.shlex(raw, posix=True, punctuation_chars="<>")
            lexer.whitespace_split = True
            tokens = tuple(lexer)
            if tokens:
                segments.append(tokens)
    except ValueError:
        return None
    return ShellParse(tuple(segments), write_control, detached, dynamic)


def _shell_segments(command: str) -> list[list[str]] | None:
    parsed = _parse_shell(command)
    return [list(segment) for segment in parsed.segments] if parsed else None


def _segment_executable(tokens: list[str]) -> tuple[str, list[str]]:
    index = 0
    while index < len(tokens) and re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index]
    ):
        index += 1
    while index < len(tokens) and Path(tokens[index]).name in {
        "builtin",
        "command",
        "exec",
        "sudo",
    }:
        index += 1
    if index < len(tokens) and Path(tokens[index]).name == "env":
        index += 1
        while index < len(tokens) and (
            tokens[index].startswith("-")
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index])
        ):
            index += 1
    if index >= len(tokens):
        return "", []
    return Path(tokens[index]).name, tokens[index:]


def _find_is_read_only(command: list[str]) -> bool:
    arguments = command[1:]
    index = 0
    while index < len(arguments):
        action = arguments[index]
        if action in {"-delete", "-ok", "-okdir", "-execdir"}:
            return False
        if action != "-exec":
            index += 1
            continue
        end = next(
            (
                offset
                for offset in range(index + 1, len(arguments))
                if arguments[offset] in {";", "+"}
            ),
            None,
        )
        if end is None or end == index + 1:
            return False
        nested = arguments[index + 1 : end]
        if not nested or nested[0] != "stat":
            return False
        index = end + 1
    return True


def _token_may_expand_to_find_delete(token: str) -> bool:
    return fnmatchcase("-delete", token) or (
        token != "{}" and any(marker in token for marker in "{}")
    )


def _segment_contains_material_find(tokens: list[str]) -> bool:
    """Fail closed for a mutating find token hidden by shell grammar."""

    for index, token in enumerate(tokens):
        executable = token.lstrip("({").rstrip(")}")
        if Path(executable).name != "find":
            continue
        raw_arguments = tokens[index + 1 :]
        if any(_token_may_expand_to_find_delete(arg) for arg in raw_arguments):
            return True
        command = [
            "find",
            *(argument.rstrip(")}") for argument in raw_arguments),
        ]
        if not _find_is_read_only(command):
            return True
    return False


def _exact_find_delete_root(arguments: list[str]) -> str | None:
    """Return the sole literal system-temp descendant for exact safe cleanup."""

    if len(arguments) != 3 or arguments[1:] != ["-depth", "-delete"]:
        return None
    root_text = arguments[0]
    if any(character in root_text for character in "*?[]{}"):
        return None
    root = Path(root_text)
    if not root.is_absolute() or ".." in root.parts:
        return None
    lexical_root = Path(os.path.abspath(root))
    resolved_root = root.resolve(strict=False)
    for temporary_root in (
        Path(tempfile.gettempdir()),
        Path("/tmp"),
        Path("/private/tmp"),
    ):
        if not temporary_root.is_dir():
            continue
        lexical_temporary_root = Path(os.path.abspath(temporary_root))
        resolved_temporary_root = temporary_root.resolve(strict=False)
        for accepted_root in {
            lexical_temporary_root,
            resolved_temporary_root,
        }:
            try:
                relative = lexical_root.relative_to(accepted_root)
            except ValueError:
                continue
            expected_resolved = resolved_temporary_root / relative
            if relative.parts and resolved_root == expected_resolved:
                return root_text
    return None


def _shell_segment_is_material(tokens: list[str]) -> bool:
    executable, command = _segment_executable(tokens)
    if not executable:
        return False
    if executable == "git":
        return not _git_is_read_only(command)
    if executable in FILE_MUTATION_COMMANDS:
        return True
    if re.fullmatch(r"python[0-9.]*", executable) and "-c" in command[1:]:
        return True
    if executable in {"bash", "dash", "sh", "zsh"}:
        return "-n" not in command[1:]
    if executable in {"perl", "sed"}:
        return any(token.startswith("-i") for token in command[1:])
    if executable == "sort":
        return any(
            token == "-o" or token.startswith("--output=") for token in command[1:]
        )
    if executable == "find":
        return not _find_is_read_only(command)
    if executable in {"awk", "gawk", "mawk", "nawk"}:
        return any(re.search(r"\bsystem\s*\(", token) for token in command[1:])
    if executable == "curl":
        return any(
            token in {"-o", "--output", "-O", "--remote-name"}
            or token.startswith("--output=")
            for token in command[1:]
        )
    if executable in {"wget", "xargs"}:
        return True
    if executable == "rg":
        options = _options_before_delimiter(command[1:])
        return any(
            token.split("=", 1)[0] in UNSAFE_RG_OPTIONS
            or (
                token.startswith("-")
                and not token.startswith("--")
                and "z" in token[1:]
            )
            for token in options
        )
    if executable in READ_ONLY_COMMANDS:
        return False
    return False


def _potential_write(tool: str, tool_input: Any) -> bool:
    if tool in MUTATING_TOOLS or tool.startswith("mcp__"):
        method = tool.rsplit("__", 1)[-1]
        return not (
            tool.startswith("mcp__")
            and READ_ONLY_MCP_METHOD_RE.search(method)
            and not MUTATING_MCP_METHOD_RE.search(method)
        )
    if tool != "Bash":
        return False
    parsed = _parse_shell(_command(tool_input))
    if parsed is None:
        return True
    if parsed.write_control or parsed.detached:
        return True
    if not parsed.segments:
        return False
    return any(
        _shell_segment_is_material(list(segment))
        or _segment_contains_material_find(list(segment))
        for segment in parsed.segments
    )


def _effective_cwd(tool_input: Any, project: Path) -> Path:
    if not isinstance(tool_input, dict):
        return project
    value = tool_input.get("workdir") or tool_input.get("cwd")
    if value is None:
        return project
    if not isinstance(value, str) or not value:
        raise HookError("material tool working directory is invalid")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = project / candidate
    absolute = Path(os.path.abspath(candidate))
    if absolute.is_symlink() or not absolute.is_dir():
        raise HookError("material tool working directory is invalid")
    return absolute.resolve()


def _explicit_tool_targets(tool: str, tool_input: Any, cwd: Path) -> list[Path]:
    values: list[str] = []
    if tool == "apply_patch":
        command = _command(tool_input)
        for line in command.splitlines():
            match = re.match(r"\*\*\* (?:Add|Update|Delete) File: (.+)$", line.strip())
            if match:
                values.append(match.group(1).strip())
            moved = re.match(r"\*\*\* Move to: (.+)$", line.strip())
            if moved:
                values.append(moved.group(1).strip())
    elif isinstance(tool_input, dict):
        for key in ("path", "file", "file_path", "filename", "target", "destination"):
            value = tool_input.get(key)
            if isinstance(value, str):
                values.append(value)
    result: list[Path] = []
    for value in values:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        result.append(Path(os.path.abspath(candidate)))
    return result


def _path_operand(value: str, cwd: Path) -> Path | None:
    if not value or value == "-" or any(character in value for character in "*?[]"):
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return Path(os.path.abspath(candidate))


def _strip_redirections(
    tokens: list[str], cwd: Path
) -> tuple[list[str], list[Path], bool]:
    command: list[str] = []
    targets: list[Path] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {">", ">>"}:
            if index + 1 >= len(tokens):
                return command, targets, False
            target = _path_operand(tokens[index + 1], cwd)
            if target is None:
                return command, targets, False
            targets.append(target)
            index += 2
            continue
        if token == "<":
            if index + 1 >= len(tokens):
                return command, targets, False
            index += 2
            continue
        command.append(token)
        index += 1
    return command, targets, True


def _simple_operands(arguments: list[str]) -> tuple[list[str], bool]:
    operands: list[str] = []
    options_done = False
    for argument in arguments:
        if not options_done and argument == "--":
            options_done = True
            continue
        if not options_done and argument.startswith("-"):
            continue
        operands.append(argument)
    return operands, bool(operands)


def _target_directory_option(arguments: list[str]) -> tuple[str | None, bool]:
    targets: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            break
        if argument in {"-t", "--target-directory"}:
            if index + 1 >= len(arguments):
                return None, False
            targets.append(arguments[index + 1])
            index += 2
            continue
        if argument.startswith("--target-directory="):
            targets.append(argument.split("=", 1)[1])
        elif argument.startswith("--"):
            option_name = argument[2:].split("=", 1)[0]
            if option_name and "target-directory".startswith(option_name):
                return None, False
        elif argument.startswith("-") and "t" in argument[1:]:
            position = argument.index("t", 1)
            attached = argument[position + 1 :]
            if attached:
                targets.append(attached)
            elif index + 1 < len(arguments):
                targets.append(arguments[index + 1])
                index += 1
            else:
                return None, False
        index += 1
    if len(targets) > 1 or any(not target for target in targets):
        return None, False
    return (targets[0] if targets else None), True


def _segment_effect_targets(
    tokens: list[str], cwd: Path
) -> tuple[list[Path], bool, bool]:
    command_tokens, targets, complete = _strip_redirections(tokens, cwd)
    if not complete:
        return targets, False, False
    executable, command = _segment_executable(command_tokens)
    if not executable:
        return targets, bool(targets), False
    material = _shell_segment_is_material(command_tokens)
    if not material:
        return targets, True, False
    arguments = command[1:]
    recursive = executable == "mv" or any(
        argument in {"-R", "-r", "--recursive"}
        or (argument.startswith("-") and "R" in argument[1:])
        for argument in arguments
    )
    operands: list[str] = []
    if executable in {"touch", "truncate", "mkdir", "rm", "rmdir", "tee"}:
        operands, complete = _simple_operands(arguments)
    elif executable in {"chmod", "chown"}:
        values, has_values = _simple_operands(arguments)
        operands = values[1:] if len(values) >= 2 else []
        complete = has_values and bool(operands)
    elif executable == "cp":
        values, has_values = _simple_operands(arguments)
        target_directory, valid_target_option = _target_directory_option(arguments)
        operands = [target_directory] if target_directory is not None else values[-1:]
        complete = (
            valid_target_option
            and has_values
            and (bool(operands) if target_directory is not None else len(values) >= 2)
        )
    elif executable == "install":
        values, has_values = _simple_operands(arguments)
        target_directory, valid_target_option = _target_directory_option(arguments)
        directory_mode = any(
            argument in {"-d", "--directory"} for argument in arguments
        )
        if directory_mode:
            operands = values
        elif target_directory is not None:
            operands = [target_directory]
        else:
            operands = values[-1:]
        complete = (
            valid_target_option
            and has_values
            and (
                bool(operands)
                if directory_mode or target_directory is not None
                else len(values) >= 2
            )
        )
    elif executable == "ln":
        values, has_values = _simple_operands(arguments)
        target_directory, valid_target_option = _target_directory_option(arguments)
        symbolic = any(
            argument == "--symbolic"
            or (
                argument.startswith("-")
                and not argument.startswith("--")
                and "s" in argument[1:]
            )
            for argument in arguments
        )
        if target_directory is not None:
            operands = [target_directory]
            if not symbolic:
                operands.extend(values)
        else:
            operands = values[-1:] if symbolic and len(values) >= 2 else values
        complete = valid_target_option and has_values and len(values) >= 2
    elif executable == "mv":
        values, has_values = _simple_operands(arguments)
        operands = values if len(values) >= 2 else []
        complete = has_values and bool(operands)
    elif executable == "find":
        find_root = (
            _exact_find_delete_root(arguments)
            if command_tokens and Path(command_tokens[0]).name == "find"
            else None
        )
        operands = [find_root] if find_root is not None else []
        complete = find_root is not None
        recursive = complete
    elif executable == "sort":
        for index, argument in enumerate(arguments):
            if argument == "-o" and index + 1 < len(arguments):
                operands.append(arguments[index + 1])
            elif argument.startswith("--output="):
                operands.append(argument.split("=", 1)[1])
        complete = bool(operands)
    elif executable == "curl":
        for index, argument in enumerate(arguments):
            if argument in {"-o", "--output"} and index + 1 < len(arguments):
                operands.append(arguments[index + 1])
            elif argument.startswith("--output="):
                operands.append(argument.split("=", 1)[1])
        complete = bool(operands)
    else:
        complete = False
    for operand in operands:
        target = _path_operand(operand, cwd)
        if target is None:
            complete = False
            continue
        targets.append(target)
    return targets, complete and bool(targets), recursive


def _effect_targets(
    tool: str, tool_input: Any, cwd: Path
) -> tuple[list[Path], bool, bool]:
    if tool == "Bash":
        parsed = _parse_shell(_command(tool_input))
        if parsed is None:
            return [], False, False
        if len(parsed.segments) != 1 and any(
            _segment_executable(list(segment))[0] == "find"
            and _shell_segment_is_material(list(segment))
            for segment in parsed.segments
        ):
            return [], False, False
        targets: list[Path] = []
        complete = True
        recursive = False
        for segment in parsed.segments:
            segment_targets, segment_complete, segment_recursive = (
                _segment_effect_targets(list(segment), cwd)
            )
            if _shell_segment_is_material(list(segment)) or segment_targets:
                complete = complete and segment_complete
                targets.extend(segment_targets)
                recursive = recursive or segment_recursive
        return targets, complete and bool(targets), recursive
    targets = _explicit_tool_targets(tool, tool_input, cwd)
    return targets, bool(targets), False


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _lexically_inside(path: Path, root: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root)))
    except ValueError:
        return False
    return True


def _protected_control_roots() -> tuple[Path, ...]:
    codex_home = Path(os.path.abspath(_codex_home())).resolve(strict=False)
    return (
        codex_home / "project-specs",
        codex_home / "commit-transactions",
    )


def _touches_root(path: Path, root: Path, recursive: bool) -> bool:
    absolute = Path(os.path.abspath(path))
    resolved = absolute.resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    if _lexically_inside(absolute, root) or _inside(resolved, root_resolved):
        return True
    return recursive and _inside(root_resolved, resolved)


def _target_scope(path: Path, project: Path, recursive: bool) -> str:
    absolute = Path(os.path.abspath(path))
    resolved = absolute.resolve(strict=False)
    lexical_project = _lexically_inside(absolute, project)
    resolved_project = _inside(resolved, project)
    if lexical_project != resolved_project:
        return EFFECT_AMBIGUOUS
    if resolved_project or (recursive and _inside(project, resolved)):
        return EFFECT_PROJECT
    try:
        metadata = resolved.stat()
    except FileNotFoundError:
        metadata = None
    except OSError:
        return EFFECT_AMBIGUOUS
    if (
        metadata is not None
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink > 1
    ):
        return EFFECT_AMBIGUOUS
    return EFFECT_EXTERNAL


def _effect_analysis(
    tool: str,
    tool_input: Any,
    project: Path,
    git_root: Path,
    state_path: Path,
) -> EffectAnalysis:
    del state_path
    if not _potential_write(tool, tool_input):
        return EffectAnalysis(EFFECT_NON_MATERIAL)
    try:
        cwd = _effective_cwd(tool_input, project)
    except HookError:
        return EffectAnalysis(EFFECT_AMBIGUOUS)
    if tool == "Bash":
        parsed = _parse_shell(_command(tool_input))
        if parsed is None or parsed.detached or parsed.dynamic:
            return EffectAnalysis(EFFECT_AMBIGUOUS)
        if any(
            _segment_executable(list(segment))[0] in {"cd", "pushd", "popd"}
            for segment in parsed.segments
        ):
            return EffectAnalysis(EFFECT_AMBIGUOUS)
        if len(parsed.segments) == 1:
            git_effect = _bounded_git_effect(list(parsed.segments[0]), cwd, git_root)
            if git_effect is not None:
                return EffectAnalysis(git_effect)
    targets, complete, recursive = _effect_targets(tool, tool_input, cwd)
    if not complete or not targets:
        return EffectAnalysis(EFFECT_AMBIGUOUS, tuple(targets))
    if any(
        _touches_root(target, root, recursive)
        for target in targets
        for root in _protected_control_roots()
    ):
        return EffectAnalysis(EFFECT_CONTROL_PLANE, tuple(targets))
    scopes = {_target_scope(target, project, recursive) for target in targets}
    if EFFECT_AMBIGUOUS in scopes:
        return EffectAnalysis(EFFECT_AMBIGUOUS, tuple(targets))
    if scopes == {EFFECT_PROJECT}:
        return EffectAnalysis(EFFECT_PROJECT, tuple(targets))
    if scopes == {EFFECT_EXTERNAL}:
        return EffectAnalysis(EFFECT_EXTERNAL, tuple(targets))
    return EffectAnalysis(EFFECT_MIXED, tuple(targets))


def _canonical_spec_intent_to_add(tool: str, tool_input: Any, project: Path) -> bool:
    if tool != "Bash":
        return False
    parsed = _parse_shell(_command(tool_input))
    if (
        parsed is None
        or len(parsed.segments) != 1
        or parsed.write_control
        or parsed.detached
        or parsed.dynamic
    ):
        return False
    tokens = list(parsed.segments[0])
    if not tokens or not _trusted_named_executable(tokens[0], "git"):
        return False
    index = 1
    base: Path | None = None
    if index < len(tokens) and tokens[index] == "-C":
        if index + 1 >= len(tokens):
            return False
        base = Path(os.path.abspath(Path(tokens[index + 1]).expanduser()))
        index += 2
    else:
        try:
            base = _effective_cwd(tool_input, project)
        except HookError:
            return False
    if base.resolve(strict=False) != project.resolve() or tokens[index : index + 1] != [
        "add"
    ]:
        return False
    arguments = tokens[index + 1 :]
    if len(arguments) < 3 or arguments[0] not in {"-N", "--intent-to-add"}:
        return False
    if arguments[1] != "--":
        return False
    paths = arguments[2:]
    allowed = {"docs/requirements.md", "docs/design.md"}
    return bool(paths) and len(paths) == len(set(paths)) and set(paths) <= allowed


def _private_mode_target(tool: str, tool_input: Any, project: Path) -> Path | None:
    if tool != "Bash":
        return None
    parsed = _parse_shell(_command(tool_input))
    if (
        parsed is None
        or len(parsed.segments) != 1
        or parsed.write_control
        or parsed.detached
        or parsed.dynamic
    ):
        return None
    tokens = list(parsed.segments[0])
    if not tokens or not _trusted_named_executable(tokens[0], "chmod"):
        return None
    arguments = tokens[1:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if len(arguments) != 2 or arguments[0] not in {"600", "0600"}:
        return None
    try:
        cwd = _effective_cwd(tool_input, project)
    except HookError:
        return None
    return _path_operand(arguments[1], cwd)


def _task_state_segment(value: object) -> str:
    raw = str(value)
    if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", raw) and raw not in {".", ".."}:
        return raw
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")[:48] or "session"
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def _task_state_workspace(git_root: Path) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", git_root.name).strip("-")[:80]
    name = name or "workspace"
    return f"{name}-{hashlib.sha256(str(git_root).encode()).hexdigest()[:12]}"


def _private_path_is_safe(path: Path, private_root: Path) -> bool:
    absolute = Path(os.path.abspath(path.expanduser()))
    root = Path(os.path.abspath(private_root.expanduser()))
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in ("", *relative.parts):
        if part:
            current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            return False
        if current != absolute and not stat.S_ISDIR(metadata.st_mode):
            return False
        if current == absolute:
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                return False
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            return False
    return True


def _private_authoring_kind(
    tool: str,
    tool_input: Any,
    project: Path,
    git_root: Path,
    state_path: Path,
    session_id: object,
) -> str | None:
    if tool == "Bash":
        mode_target = _private_mode_target(tool, tool_input, project)
        targets = [mode_target] if mode_target is not None else []
    elif tool in MUTATING_TOOLS:
        try:
            cwd = _effective_cwd(tool_input, project)
        except HookError:
            return None
        targets = _explicit_tool_targets(tool, tool_input, cwd)
    else:
        return None
    if len(targets) != 1:
        return None
    target = targets[0]
    task_root = _codex_home() / "task-state"
    task_state = (
        task_root
        / _task_state_workspace(git_root)
        / _task_state_segment(session_id)
        / "current.md"
    )
    lifecycle_root = state_path.parent
    allowed = {
        task_state: ("task-state", task_root),
        lifecycle_root / "runtime-config.json": ("runtime-config", lifecycle_root),
        lifecycle_root / "project-instructions" / "decision.json": (
            "decision",
            lifecycle_root,
        ),
    }
    match = allowed.get(target)
    if match is None:
        return None
    kind, private_root = match
    if tool == "Bash" and kind == "task-state":
        return None
    return kind if _private_path_is_safe(target, private_root) else None


def _coordinator_identity_matches(
    command: str,
    payload: dict[str, Any],
    state: dict[str, Any],
    action: str,
) -> bool:
    if action == "validate":
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
        if "--output" not in tokens:
            return True
        return _argument(tokens, "--session-id") == str(payload.get("session_id"))
    if action not in {"start-prompt", "plan", "open", "seal", "waive"}:
        return True
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    session = _argument(tokens, "--session-id")
    if session != str(payload.get("session_id")):
        return False
    turn_id = _argument(tokens, "--turn-id")
    turn_token = _argument(tokens, "--turn-token")
    if (turn_id is None) == (turn_token is None):
        return False
    if turn_token is not None:
        return action != "start-prompt" and turn_token == state.get("turn_sha256")
    try:
        return _turn_hash(turn_id) == _turn_hash(payload.get("turn_id"))
    except HookError:
        return False


def _spec_or_agents(path: Path, project: Path) -> bool:
    return path in {
        project / "docs" / "requirements.md",
        project / "docs" / "design.md",
        project / "AGENTS.md",
    }


def _rules_context(state: dict[str, Any], state_path: Path) -> str | None:
    if state.get("rules_path") is None:
        return None
    if state.get("rules_path") != RULES_NAME or not isinstance(
        state.get("rules_sha256"), str
    ):
        raise HookError("pending project rules reference is invalid")
    path = state_path.parent / RULES_NAME
    raw = _read_regular(path, "pending project rules", 8192)
    if stat.S_IMODE(path.lstat().st_mode) != 0o600:
        raise HookError("pending project rules are unsafe")
    if hashlib.sha256(raw).hexdigest() != state["rules_sha256"]:
        raise HookError("pending project rules changed")
    return raw.decode("utf-8")


def _start(payload: dict[str, Any], event: str) -> dict[str, Any]:
    project, git_root, scope = _project(payload.get("cwd"))
    if _disabled(project):
        return _context(
            event,
            "Project-spec automation is disabled by .codex/project-specs.json for this selected project.",
        )
    session_id = payload.get("session_id")
    path = _state_path(git_root, session_id)
    if event == "UserPromptSubmit":
        fresh = {
            "schema": STATE_SCHEMA,
            "project_scope": scope,
            "git_head_at_prompt": subprocess.check_output(
                ["git", "-C", str(git_root), "rev-parse", "HEAD"], text=True
            ).strip(),
            "turn_sha256": _turn_hash(payload.get("turn_id")),
            "phase": "planning-required",
            "receipt_sha256": None,
            "requirements_sha256": None,
            "design_sha256": None,
            "rules_path": None,
            "rules_sha256": None,
            "project_instructions_state_sha256": None,
            "project_instructions_reload_required": None,
            "write_epoch": 0,
            "planned_write_epoch": None,
            "waiver": None,
        }
        state = _load(path)
        if state is None or state.get("phase") in {"sealed", "waived"}:
            state = fresh
        else:
            if state.get("project_scope") != scope:
                raise HookError(
                    "unfinished lifecycle is bound to another project scope"
                )
            state["git_head_at_prompt"] = fresh["git_head_at_prompt"]
            state["turn_sha256"] = fresh["turn_sha256"]
            if state.get("phase") != "seal-armed":
                state["phase"] = (
                    "reconciliation-required"
                    if int(state.get("write_epoch", 0)) > 0
                    or state.get("phase")
                    in {"implementation-open", "reconciliation-required"}
                    else "planning-required"
                )
                for field in (
                    "receipt_sha256",
                    "requirements_sha256",
                    "design_sha256",
                    "rules_path",
                    "rules_sha256",
                    "planned_write_epoch",
                ):
                    state[field] = None
        _write(path, state)
    else:
        state = _load(path)
    message = (
        "Project contract lifecycle is active for the selected project scope "
        f"{scope}. Before material work, use $maintain-project-specs to create or "
        "reconcile docs/requirements.md and docs/design.md, obtain the canonical "
        "receipt, render an explicit project-instructions decision, and open "
        "implementation. After implementation, reconcile the specs, apply that "
        "decision only as the terminal seal mutation, then seal the lifecycle. "
        "A not-needed decision leaves a missing project AGENTS.md absent. For genuinely "
        "read-only or non-project work, record a bounded spec-impact waiver."
    )
    if state is not None and state.get("phase") not in {"sealed", "waived"}:
        message += (
            f" Current lifecycle phase: {state.get('phase')}. "
            "For owner transitions, use --session-id "
            f"{_segment(session_id)} --turn-token {state.get('turn_sha256')}."
        )
    if event == "SessionStart":
        unfinished = _unfinished_session_phases(git_root, scope)
        if unfinished:
            phases = ", ".join(sorted(set(unfinished)))
            message += (
                " Unfinished lifecycle state exists for this project scope "
                f"({len(unfinished)} session(s); phases: {phases}); reconcile or "
                "explicitly supersede it before claiming completion."
            )
    return _context(event, message)


def _pre_tool(payload: dict[str, Any]) -> dict[str, Any]:
    project, git_root, scope = _project(payload.get("cwd"))
    if _disabled(project):
        return {}
    state_path = _state_path(git_root, payload.get("session_id"))
    state = _load(state_path)
    if state is None:
        return _deny(
            "Project contract lifecycle is missing. Re-enter through $maintain-project-specs before material work."
        )
    if state.get("project_scope") != scope:
        return _deny("Project contract lifecycle is bound to another selected scope.")
    tool, tool_input = _tool(payload)
    command = _command(tool_input)
    commit_shaped = tool == "Bash" and _commit_command_shape(command)
    commit = (
        _bound_commit_command(command, git_root, payload.get("session_id"))
        if commit_shaped
        else None
    )
    task_commit = (
        _task_implementer_commit(command, project)
        if commit_shaped and commit is None
        else None
    )
    if task_commit is not None:
        commit = _bound_commit_command(
            command,
            Path(str(task_commit["worker_root"])),
            task_commit["worker_session_id"],
        )
    coordinator = (
        _bound_coordinator_command(command, project, state_path)
        if tool == "Bash"
        else None
    )
    coordinator_shaped = tool == "Bash" and _coordinator_command_shape(command)
    task_coordinator = (
        _task_implementer_coordinator(command, project)
        if coordinator_shaped and coordinator is None
        else None
    )
    if (
        task_coordinator is not None
        and task_coordinator.get("command_sha256")
        != hashlib.sha256(command.encode()).hexdigest()
    ):
        task_coordinator = None
    task_impact_shaped = tool == "Bash" and _task_implementer_impact_shape(command)
    task_impact = (
        _task_implementer_impact(command, project) if task_impact_shaped else None
    )
    if (
        task_impact is not None
        and task_impact.get("command_sha256")
        != hashlib.sha256(command.encode()).hexdigest()
    ):
        task_impact = None
    phase = state.get("phase")
    if commit_shaped and commit is None:
        return _deny(COMMIT_BINDING_REASON)
    if commit is not None:
        _action, _claim_path, owner, commit_turn_sha256 = commit
        if owner == "direct" and phase not in {"sealed", "waived"}:
            return _deny(
                "Direct $commit requires the selected-project lifecycle to be sealed, "
                "or a fresh zero-write commit-only turn to hold a bounded waiver. "
                "Reconcile the current selected scope first; sibling project "
                "attestations are not required."
            )
        if owner == "direct" and commit_turn_sha256 != state.get("turn_sha256"):
            return _deny(
                "Direct $commit authorization belongs to another user turn. "
                "Run a fresh explicit $commit invocation."
            )
        return {}
    if coordinator_shaped and coordinator is None and task_coordinator is None:
        return _deny(COORDINATOR_BINDING_REASON)
    if task_coordinator is not None:
        if task_coordinator.get("action") not in {
            "validate",
            "inspect",
            "render",
            "coordinator-stage",
            "coordinator-commit",
        }:
            return _deny(
                "Task Implementer terminal project-instructions actions remain "
                "owned by the normal lifecycle seal transition."
            )
        if task_coordinator.get("action") in {
            "coordinator-stage",
            "coordinator-commit",
        }:
            if phase == "reconciliation-required":
                return {}
            return _deny(
                "Task Implementer coordinator commit is valid only while the "
                "selected project is reconciliation-required."
            )
        if (
            task_coordinator.get("action") != "validate"
            or _argument(shlex.split(command), "--session-id")
            == str(payload.get("session_id"))
        ) and phase in {
            "implementation-open",
            "reconciliation-required",
            "sealed",
        }:
            return {}
        return _deny(
            "Task Implementer project-instructions evidence is not valid in "
            f"phase {phase}."
        )
    if coordinator is not None:
        owner, action = coordinator
        if owner == "project-specs":
            if not _coordinator_identity_matches(command, payload, state, action):
                return _deny(
                    "The project-spec transition identity does not match the current hook session and turn."
                )
            allowed_phases = {
                "inspect": set(),
                "validate": set(),
                "migrate": {"planning-required", "reconciliation-required"},
                "recover": {"planning-required", "reconciliation-required"},
                "plan": {"planning-required", "reconciliation-required", "planned"},
                "open": {"planned"},
                "seal": {"seal-armed"},
                "waive": {"planning-required"},
                "start-prompt": set(PHASES),
            }
            permitted = allowed_phases[action]
            if action in {"inspect", "validate"} or phase in permitted:
                return {}
            return _deny(
                f"The project-spec {action} transition is not valid in phase {phase}."
            )
        if owner == "project-instructions":
            allowed_phases = {
                "inspect": {"planning-required", "reconciliation-required"},
                "render": {"planning-required", "reconciliation-required"},
                "apply": {"planned"},
                "verify": {"seal-armed", "sealed"},
            }
            if phase in allowed_phases[action]:
                return {}
            return _deny(
                f"The project-instructions {action} action is not valid in phase {phase}."
            )
    if task_impact_shaped:
        if task_impact is None:
            return _deny(
                "Task Implementer wave-plan is not bound to the active selected-project run."
            )
        if phase in {"implementation-open", "reconciliation-required"}:
            return {}
        if (
            phase == "waived"
            and state.get("waiver") == "non-project"
            and int(state.get("write_epoch", 0)) == 0
            and task_impact.get("review_correction") is True
        ):
            return {}
        return _deny(f"Task Implementer wave-plan is not valid in phase {phase}.")
    if _canonical_spec_intent_to_add(tool, tool_input, project):
        if phase in {"planning-required", "reconciliation-required"}:
            return {}
        return _deny(
            "Canonical spec intent-to-add is available only while planning or reconciling."
        )
    private_kind = _private_authoring_kind(
        tool,
        tool_input,
        project,
        git_root,
        state_path,
        payload.get("session_id"),
    )
    if private_kind == "task-state":
        return {}
    if private_kind in {"runtime-config", "decision"}:
        if phase in {"planning-required", "reconciliation-required"}:
            return {}
        return _deny(
            "Private project-instruction inputs may be authored only while planning or reconciling."
        )
    effect = _effect_analysis(tool, tool_input, project, git_root, state_path)
    if effect.kind == EFFECT_NON_MATERIAL:
        rules = _rules_context(state, state_path)
        return (
            _context("PreToolUse", "Exact pending project rules:\n" + rules)
            if rules
            else {}
        )
    if effect.kind == EFFECT_EXTERNAL:
        return {}
    if effect.kind == EFFECT_CONTROL_PLANE:
        return _deny("Authoritative project-spec lifecycle state remains protected.")
    if effect.kind in {EFFECT_MIXED, EFFECT_AMBIGUOUS}:
        return _deny(
            "Mixed or ambiguous material effects cannot bypass the selected-project lifecycle. Use fixed external targets or an explicit external working directory."
        )
    targets = [path.resolve(strict=False) for path in effect.targets]
    protected = [path for path in targets if _spec_or_agents(path, project)]
    if phase == "planning-required":
        allowed = {project / "docs" / "requirements.md", project / "docs" / "design.md"}
        if targets and all(path in allowed for path in targets):
            return {}
        return _deny(
            "Reconcile the canonical requirements and design, then open implementation before material writes."
        )
    if phase == "planned":
        return _deny(
            "Run the project-spec lifecycle open transition before implementation writes."
        )
    if phase == "reconciliation-required":
        allowed = {project / "docs" / "requirements.md", project / "docs" / "design.md"}
        if targets and all(path in allowed for path in targets):
            return {}
        if protected:
            return _deny(
                "Reconcile requirements/design first; the project-instructions decision is the terminal seal mutation."
            )
        rules = _rules_context(state, state_path)
        return (
            _context("PreToolUse", "Exact receipt-bound project rules:\n" + rules)
            if rules
            else {}
        )
    if phase == "implementation-open":
        if protected:
            return _deny(
                "Specs and AGENTS.md are coordinator-owned; reconcile them only during the terminal seal transition."
            )
        rules = _rules_context(state, state_path)
        return (
            _context("PreToolUse", "Exact receipt-bound project rules:\n" + rules)
            if rules
            else {}
        )
    if phase == "waived" and state.get("waiver") == "documentation-only":
        if (
            targets
            and all(path.suffix.lower() == ".md" for path in targets)
            and not any(_spec_or_agents(path, project) for path in targets)
        ):
            return {}
        return _deny(
            "The documentation-only waiver permits only non-contract Markdown files in the selected project."
        )
    if phase in {"sealed", "waived"}:
        return _deny(
            "This project-contract lifecycle is terminal; a new user prompt must start another lifecycle before material writes."
        )
    return _deny("Project contract lifecycle state is invalid.")


def _post_tool(payload: dict[str, Any]) -> dict[str, Any]:
    project, git_root, _scope = _project(payload.get("cwd"))
    if _disabled(project):
        return {}
    tool, tool_input = _tool(payload)
    state_path = _state_path(git_root, payload.get("session_id"))
    if _canonical_spec_intent_to_add(tool, tool_input, project):
        return {}
    if (
        _private_authoring_kind(
            tool,
            tool_input,
            project,
            git_root,
            state_path,
            payload.get("session_id"),
        )
        is not None
    ):
        return {}
    command = _command(tool_input)
    response = payload.get("tool_response")
    failed = isinstance(response, dict) and (
        response.get("isError") is True
        or response.get("success") is False
        or (type(response.get("exit_code")) is int and int(response["exit_code"]) != 0)
    )
    commit_shaped = tool == "Bash" and _commit_command_shape(command)
    commit = (
        _bound_commit_command(
            command,
            git_root,
            payload.get("session_id"),
            completed=not failed,
        )
        if commit_shaped
        else None
    )
    task_commit = (
        _task_implementer_commit(command, project)
        if commit_shaped and commit is None
        else None
    )
    if task_commit is not None:
        commit = _bound_commit_command(
            command,
            Path(str(task_commit["worker_root"])),
            task_commit["worker_session_id"],
            completed=not failed,
        )
    coordinator = (
        _bound_coordinator_command(command, project, state_path)
        if tool == "Bash"
        else None
    )
    coordinator_shaped = tool == "Bash" and _coordinator_command_shape(
        _command(tool_input)
    )
    task_coordinator = (
        _task_implementer_coordinator(command, project)
        if coordinator_shaped and coordinator is None
        else None
    )
    if (
        task_coordinator is not None
        and task_coordinator.get("command_sha256")
        != hashlib.sha256(command.encode()).hexdigest()
    ):
        task_coordinator = None
    task_impact_shaped = tool == "Bash" and _task_implementer_impact_shape(command)
    task_impact = (
        _task_implementer_impact(command, project) if task_impact_shaped else None
    )
    if (
        task_impact is not None
        and task_impact.get("command_sha256")
        != hashlib.sha256(command.encode()).hexdigest()
    ):
        task_impact = None
    if commit is not None:
        if (
            task_commit is not None
            and not failed
            and task_commit.get("action") == "execute"
        ):
            _record_task_worker_delegation(state_path)
            return _context(
                "PostToolUse",
                "Task worker commit completed; shared spec and project-instruction reconciliation remains delegated to the Task Implementer coordinator.",
            )
        return {}
    if commit_shaped:
        if failed:
            return {}
        _record_material_write(state_path)
        return _context(
            "PostToolUse",
            "An unbound commit-helper-shaped command completed; lifecycle evidence was invalidated.",
        )
    if coordinator is not None:
        if coordinator == ("project-instructions", "apply") and not failed:
            try:
                _verified_apply_state(_command(tool_input), project, state_path)
            except HookError:
                # Apply may have changed the selected target before final state
                # verification failed. Conservatively invalidate the plan and
                # make its decision input authorable again in reconciliation.
                _record_material_write(state_path)
                raise
            state = _load(state_path)
            if state is None or state.get("phase") != "planned":
                raise HookError(
                    "project-instructions apply completed outside the planned phase"
                )
            state["phase"] = "seal-armed"
            _write(state_path, state)
            return _context(
                "PostToolUse",
                "Terminal project-instructions decision applied; only verification and lifecycle seal remain.",
            )
        return {}
    if task_coordinator is not None:
        if task_coordinator.get("action") in {
            "validate",
            "inspect",
            "render",
            "coordinator-stage",
            "coordinator-commit",
        }:
            return {}
        if failed:
            return {}
        _record_material_write(state_path)
        return _context(
            "PostToolUse",
            "An invalid Task Implementer terminal project-instructions action completed; lifecycle evidence was invalidated.",
        )
    if coordinator_shaped:
        if failed:
            return {}
        _record_material_write(state_path)
        return _context(
            "PostToolUse",
            "An unbound coordinator-shaped command completed; lifecycle evidence was invalidated.",
        )
    if task_impact_shaped:
        if failed:
            return {}
        if task_impact is None or task_impact.get("checkpoint_head") is None:
            _record_material_write(state_path)
            return _context(
                "PostToolUse",
                "An unbound Task Implementer wave-plan completed; lifecycle evidence was invalidated.",
            )
        state = _load(state_path)
        if state is None:
            raise HookError(
                "Task Implementer wave-plan completed without lifecycle state"
            )
        if state.get("phase") == "implementation-open":
            _record_material_write(state_path)
            return _context(
                "PostToolUse",
                "Task Implementer checkpoint completed; reconcile the selected lane contract before dispatch.",
            )
        if state.get("phase") == "reconciliation-required":
            return {}
        if (
            state.get("phase") == "waived"
            and state.get("waiver") == "non-project"
            and task_impact.get("review_correction") is True
        ):
            _record_task_review_correction(state_path)
            return _context(
                "PostToolUse",
                "Task Implementer integration review correction opened; reconcile the selected lane contract before dispatch.",
            )
        raise HookError(
            "Task Implementer wave-plan completed outside an implementation lifecycle"
        )
    effect = _effect_analysis(tool, tool_input, project, git_root, state_path)
    if effect.kind in {EFFECT_NON_MATERIAL, EFFECT_EXTERNAL}:
        return {}
    if failed:
        return {}
    state = _load(state_path)
    if state is not None and state.get("phase") in {
        "planning-required",
        "reconciliation-required",
    }:
        allowed_specs = {
            project / "docs" / "requirements.md",
            project / "docs" / "design.md",
        }
        targets = [path.resolve(strict=False) for path in effect.targets]
        if targets and all(path in allowed_specs for path in targets):
            return {}
    _record_material_write(state_path)
    return {}


def evaluate_stop(payload: dict[str, Any]) -> dict[str, Any]:
    project, git_root, _scope = _project(payload.get("cwd"))
    if _disabled(project):
        return {"continue": True}
    state_path = _state_path(git_root, payload.get("session_id"))
    state = _load(state_path)
    if state is None:
        if payload.get("stop_hook_active"):
            return {
                "continue": False,
                "stopReason": "Managed project contract state is missing after continuation.",
            }
        return {
            "decision": "block",
            "reason": (
                "Managed project contract state is missing. Re-enter through "
                "$maintain-project-specs, reconcile the selected project, and seal "
                "or record a bounded waiver before completion."
            ),
        }
    if not payload.get("stop_hook_active"):
        state = _open_stop_reconciliation(state_path)
        if state is None:
            raise HookError("managed project contract state disappeared during Stop")
    phase = state.get("phase")
    if phase in {"sealed", "waived"}:
        return {"continue": True}
    if payload.get("stop_hook_active"):
        return {
            "continue": False,
            "stopReason": "Project contract reconciliation did not reach a sealed or waived state after continuation.",
        }
    if phase == "reconciliation-required":
        return {
            "decision": "block",
            "reason": (
                "Use $maintain-project-specs to review the accumulated implementation delta once. "
                "Update docs/requirements.md only for accepted intent or observable contract changes, "
                "and update docs/design.md only for implemented boundary, interface, workflow, operational, "
                "or evidence changes. For implementation-only or reverted changes, leave both documents "
                "unchanged and validate them at the latest write epoch. If impact is ambiguous, remain "
                "unsealed and resolve it. Before sealing, render, apply, and verify an explicit "
                "project-instructions decision; not-needed leaves a missing project AGENTS.md absent, "
                "while needed may create or update it. Then record the sealed lifecycle receipt."
            ),
        }
    return {
        "decision": "block",
        "reason": (
            "Use $maintain-project-specs to reconcile the lifecycle-bound project. "
            f"Current phase: {phase}. Validate and update docs/requirements.md and "
            "docs/design.md from the implemented changes, then render, apply, and verify "
            "the explicit project-instructions decision. A not-needed outcome leaves a "
            "missing project AGENTS.md absent. Finally record the sealed lifecycle receipt."
        ),
    }


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("hook_event_name")
    try:
        if event in {"SessionStart", "UserPromptSubmit"}:
            return _start(payload, str(event))
        if event == "PreToolUse":
            return _pre_tool(payload)
        if event == "PostToolUse":
            return _post_tool(payload)
        if event == "Stop":
            return evaluate_stop(payload)
        return {}
    except NonProjectScope:
        if event in {"SessionStart", "UserPromptSubmit"}:
            return _context(
                str(event),
                "Project-spec automation is not active outside a Git worktree. "
                "Initialize or select a Git project to enable its lifecycle.",
            )
        if event == "Stop":
            return {"continue": True}
        return {}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise HookError("hook payload must be an object")
        output = evaluate(payload)
    except Exception as error:
        event = (
            payload.get("hook_event_name")
            if "payload" in locals() and isinstance(payload, dict)
            else None
        )
        if event == "PreToolUse":
            output = _deny(f"Project contract hook failed closed: {error}")
        elif event == "Stop":
            output = {
                "continue": False,
                "stopReason": f"Project contract Stop audit failed: {error}",
            }
        elif event == "PostToolUse":
            output = _context(
                "PostToolUse",
                f"Project contract impact recording failed: {error}. Completion will fail closed until lifecycle recovery.",
            )
        else:
            output = _context(
                str(event or "SessionStart"),
                f"Project contract lifecycle audit failed: {error}",
            )
    if output:
        print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
