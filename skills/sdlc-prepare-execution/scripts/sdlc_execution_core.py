#!/usr/bin/env python3
"""Private Agentic SDLC dependency-wave and Git worktree execution engine."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator

import sys

from sdlc_execution_interop import (
    ExecutionInteropError,
    acquire as acquire_outer_lease,
    inspect_anchor as inspect_outer_anchor,
    reconcile_promotion as reconcile_outer_promotion,
    record_promotion as record_outer_promotion,
    record_resource as record_outer_resource,
)


WORKTREE_SCRIPTS = Path(__file__).resolve().parents[2] / "worktree" / "scripts"
if str(WORKTREE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WORKTREE_SCRIPTS))
from git_promotion import (  # noqa: E402
    GitPromotionError,
    ensure_promotion_branch,
    promote_ff_only,
    verify_remote_default,
)


COORDINATOR_SCHEMA = "agentic-sdlc/execution-coordinator-v7"
WAVE_SCHEMA = "agentic-sdlc/execution-wave-v2"
TASK_SCHEMA = "agentic-sdlc/execution-task-v4"
ASSIGNMENT_SCHEMA = "agentic-sdlc/worker-assignment-v3"
RESULT_SCHEMA = "agentic-sdlc/worker-result-v4"
INCOMING_HANDOFF_SCHEMA = "agentic-sdlc/incoming-handoff-v1"
TASK_FINISH_INTENT_SCHEMA = "agentic-sdlc/task-finish-intent-v1"
WORKER_HEARTBEAT_SECONDS = 30
WORKER_START_SECONDS = 60
WORKER_STANDARD_WARNING_SECONDS = 240
WORKER_STANDARD_READ_ONLY_SECONDS = 300
WORKER_INTEGRATION_WARNING_SECONDS = 360
WORKER_INTEGRATION_READ_ONLY_SECONDS = 420
WORKER_STALL_SECONDS = 240
WORKER_MAX_SECONDS = 1800
WORKER_PHASES = (
    "preflight",
    "implementing",
    "validating",
    "reviewing",
    "reporting",
)
FEATURE_ID_RE = re.compile(r"FEAT-[0-9]{3,}")
REQUIREMENT_ID_RE = re.compile(r"REQ-[0-9]{3,}")
WAVE_ID_RE = re.compile(r"WAVE-[0-9]{3,}")
TASK_ID_RE = re.compile(r"TASK-[0-9]{3,}")
SAFE_ID_RE = re.compile(r"[^a-z0-9._-]+")
SHA_RE = re.compile(r"[0-9a-f]{40,64}")
SINGLETON_DOMAIN_CLASSES = {
    "database",
    "external",
    "kubernetes",
    "migration",
    "publication",
    "terraform",
}
SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAWS_ACCESS_KEY_ID\b\s*[:=]\s*[A-Z0-9]{16,}"),
    re.compile(r"\bAWS_SECRET_ACCESS_KEY\b\s*[:=]\s*[A-Za-z0-9/+=]{30,}"),
    re.compile(r"\bGITHUB_TOKEN\b\s*[:=]\s*[A-Za-z0-9_ghopsu-]{20,}"),
    re.compile(r"\bOPENAI_API_KEY\b\s*[:=]\s*sk-[A-Za-z0-9_-]{16,}"),
    re.compile(
        r"(?i)\b(password|secret|token)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+=:-]{12,}"
    ),
    re.compile(r"(?i)https?://[^\s/]+\.(?:internal|corp|local)(?::[0-9]+)?(?:/|\b)"),
)
SENSITIVE_FILENAMES = (
    re.compile(r"(?:^|/)\.env(?:\..+)?$", re.IGNORECASE),
    re.compile(r"(?:^|/)(?:id_rsa|id_ed25519)(?:\.pub)?$", re.IGNORECASE),
    re.compile(r"(?:^|/)(?:credentials|service-account)(?:\.[^/]+)?$", re.IGNORECASE),
    re.compile(r"(?:^|/)kubeconfig(?:\.[^/]+)?$", re.IGNORECASE),
)


class ExecutionError(RuntimeError):
    """Stable private execution error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _interop(action: str, callback, *args, **kwargs):
    try:
        return callback(*args, **kwargs)
    except ExecutionInteropError as exc:
        raise ExecutionError("WORKTREE_CONFLICT", f"{action} failed") from exc


@dataclass(frozen=True)
class WriteClaim:
    kind: str
    path: str


@dataclass(frozen=True)
class TaskPlan:
    task_id: str
    position: int
    requirements: tuple[str, ...]
    goal: str
    dependencies: tuple[str, ...]
    write_claims: tuple[WriteClaim, ...]
    conflict_domains: tuple[str, ...]
    validation: str
    done_criteria: str
    rollback: str
    diagnosis_id: str | None
    regression_oracle: str | None
    ownership_complete: bool


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _clock_utc(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _time_value(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ExecutionError("EXECUTION_STATE_INVALID", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionError("EXECUTION_STATE_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ExecutionError("EXECUTION_STATE_INVALID", f"{label} is invalid")
    return parsed.astimezone(timezone.utc)


def _system_clock() -> datetime:
    return datetime.now(timezone.utc)


def worker_liveness_profile(dependencies: Iterable[str]) -> dict[str, object]:
    if tuple(dependencies):
        return {
            "worker_profile": "integration",
            "read_only_warning_seconds": WORKER_INTEGRATION_WARNING_SECONDS,
            "read_only_seconds": WORKER_INTEGRATION_READ_ONLY_SECONDS,
        }
    return {
        "worker_profile": "standard",
        "read_only_warning_seconds": WORKER_STANDARD_WARNING_SECONDS,
        "read_only_seconds": WORKER_STANDARD_READ_ONLY_SECONDS,
    }


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: dict[str, Any]) -> str:
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _session_hash(value: str) -> str:
    if not value.strip() or len(value) > 1024:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker session identity is invalid"
        )
    return sha256_bytes(value.encode("utf-8"))


def _validated_session_history(task_record: dict[str, Any]) -> list[str]:
    if task_record.get("schema") in {
        "agentic-sdlc/execution-task-v1",
        "agentic-sdlc/execution-task-v2",
        "agentic-sdlc/execution-task-v3",
    }:
        raise ExecutionError(
            "WORKFLOW_UPGRADE_REQUIRED", "legacy execution task schema is unsupported"
        )
    history = task_record.get("worker_session_hash_history")
    current = task_record.get("worker_session_hash")
    if (
        task_record.get("schema") != TASK_SCHEMA
        or not isinstance(history, list)
        or len(history) != len(set(history))
        or any(
            not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
            for item in history
        )
        or (
            current is not None
            and (not isinstance(current, str) or not history or history[-1] != current)
        )
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker session history is invalid"
        )
    return history


def _contains_sensitive(value: str) -> bool:
    placeholders = (
        "example",
        "dummy",
        "placeholder",
        "redacted",
        "<token>",
        "<secret>",
        "<password>",
        "changeme",
        "not-a-secret",
    )
    for line in value.splitlines() or [value]:
        lowered = line.lower()
        if any(marker in lowered for marker in placeholders):
            continue
        if any(pattern.search(line) for pattern in SENSITIVE_PATTERNS):
            return True
    return False


def _reject_sensitive_evidence(*values: str) -> None:
    if any(_contains_sensitive(value) for value in values):
        raise ExecutionError(
            "SECURITY_BLOCKER", "worker evidence or commit metadata is sensitive"
        )


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExecutionError("EXECUTION_STATE_INVALID", f"missing {path}") from exc
    except json.JSONDecodeError as exc:
        raise ExecutionError("EXECUTION_STATE_INVALID", f"corrupt {path}") from exc
    if not isinstance(value, dict):
        raise ExecutionError("EXECUTION_STATE_INVALID", f"invalid object in {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@contextmanager
def _execution_transition_lock(run_dir: Path, feature_id: str) -> Iterator[None]:
    directory = execution_dir(run_dir, feature_id)
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / ".transition.lock"
    if path.is_symlink():
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "execution transition lock is unsafe"
        )
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "execution transition lock could not open"
        ) from exc
    try:
        if os.name == "posix":
            import fcntl

            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ExecutionError(
                            "WORKSPACE_BUSY", "another execution transition is active"
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif os.name == "nt":  # pragma: no cover - exercised on Windows.
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            deadline = time.monotonic() + 10
            while True:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ExecutionError(
                            "WORKSPACE_BUSY", "another execution transition is active"
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - unsupported operating system.
            raise ExecutionError(
                "ENVIRONMENT_BLOCKER", "execution locking is unavailable"
            )
    finally:
        os.close(descriptor)


def _claim_worker_session(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    session_hash: str,
) -> None:
    directory = execution_dir(run_dir, feature_id) / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / f"{session_hash}.json"
    claim = {
        "feature_id": feature_id,
        "wave_id": wave_id,
        "task_id": task_id,
        "worker_session_hash": session_hash,
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".claim", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(claim, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except FileExistsError:
        try:
            temporary.unlink()
            _fsync_directory(directory)
        except OSError as exc:
            raise ExecutionError(
                "EXECUTION_STATE_INVALID",
                "worker session claim cleanup could not be persisted",
            ) from exc
        if (
            path.is_symlink()
            or not path.is_file()
            or (path.stat().st_mode & 0o777) != 0o600
            or read_json(path) != claim
        ):
            raise ExecutionError(
                "FRESH_SESSION_REQUIRED", "worker session already owns another task"
            )
        return
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
            _fsync_directory(directory)
        except OSError:
            pass
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker session claim could not be published"
        ) from exc
    try:
        _fsync_directory(directory)
        temporary.unlink()
        _fsync_directory(directory)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ExecutionError(
            "EXECUTION_STATE_INVALID",
            "worker session claim publication could not be persisted",
        ) from exc


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(f"directory sync target is not a directory: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_journal(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("created_at", utc_now())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    path.chmod(0o600)


def _journal_has_intent(path: Path, event: str, base: str, message: str) -> bool:
    if not path.exists():
        return False
    try:
        entries = [json.loads(line) for line in path.read_text().splitlines() if line]
    except json.JSONDecodeError as exc:
        raise ExecutionError("EXECUTION_STATE_INVALID", f"corrupt {path}") from exc
    return any(
        isinstance(entry, dict)
        and entry.get("event") == event
        and entry.get("base") == base
        and entry.get("message") == message
        for entry in entries
    )


def _run(
    argv: list[str], cwd: Path, action: str, *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExecutionError("GIT_OPERATION_FAILED", f"{action} failed: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise ExecutionError("GIT_OPERATION_FAILED", f"{action} failed: {detail}")
    return result


def git(cwd: Path, args: list[str], action: str, *, check: bool = True) -> str:
    return _run(["git", *args], cwd, action, check=check).stdout.strip()


def git_root(cwd: Path) -> Path:
    value = git(cwd, ["rev-parse", "--show-toplevel"], "resolve Git root")
    return Path(value).resolve()


def git_common_dir(cwd: Path) -> Path:
    value = git(cwd, ["rev-parse", "--git-common-dir"], "resolve Git common dir")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve()


def branch(cwd: Path) -> str:
    return git(cwd, ["branch", "--show-current"], "read branch")


def head(cwd: Path) -> str:
    value = git(cwd, ["rev-parse", "HEAD"], "read HEAD")
    if SHA_RE.fullmatch(value) is None:
        raise ExecutionError("EXECUTION_STATE_INVALID", "Git HEAD is invalid")
    return value


def clean(cwd: Path) -> bool:
    return not git(cwd, ["status", "--porcelain=v1"], "read Git status")


def worktrees(cwd: Path) -> dict[Path, dict[str, str]]:
    output = git(cwd, ["worktree", "list", "--porcelain"], "list worktrees")
    result: dict[Path, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if "worktree" in current:
                result[Path(current["worktree"]).resolve()] = dict(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    return result


def _require_id(value: str, pattern: re.Pattern[str], label: str) -> str:
    if pattern.fullmatch(value) is None:
        raise ExecutionError("EXECUTION_STATE_INVALID", f"invalid {label}: {value}")
    return value


def _reject_symlink_components(path: Path, label: str) -> None:
    candidate = path.expanduser().absolute()
    for part in reversed((candidate, *candidate.parents)):
        if part.parent == Path("/"):
            continue
        if os.path.lexists(part) and part.is_symlink():
            raise ExecutionError(
                "WORKTREE_CONFLICT", f"{label} contains a symlink: {part}"
            )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _path_in_project_scope(path: str, project_scope: str) -> bool:
    if project_scope == ".":
        return True
    try:
        PurePosixPath(path).relative_to(PurePosixPath(project_scope))
        return True
    except ValueError:
        return False


def _verify_claim_paths(
    project_root: Path, project_scope: str, tasks: Iterable[TaskPlan]
) -> None:
    tasks = tuple(tasks)
    _reject_special_claims(project_root, tasks)
    for task in tasks:
        for claim in task.write_claims:
            if not _path_in_project_scope(claim.path, project_scope):
                raise ExecutionError(
                    "REPLAN_REQUIRED",
                    f"{task.task_id} write claim escapes initialized project scope",
                )
            try:
                _reject_symlink_components(
                    project_root / claim.path, f"{task.task_id} write claim"
                )
            except ExecutionError as exc:
                raise ExecutionError("PLAN_INVALID", str(exc)) from exc


def _reject_special_claims(project_root: Path, tasks: Iterable[TaskPlan]) -> None:
    output = git(
        project_root,
        ["ls-files", "--stage", "-z"],
        "inspect special Git entries",
    )
    gitlinks: list[PurePosixPath] = []
    symlinks: list[PurePosixPath] = []
    for entry in output.split("\x00"):
        if "\t" not in entry:
            continue
        mode = entry.split(" ", 1)[0]
        path = PurePosixPath(entry.split("\t", 1)[1])
        if mode == "160000":
            gitlinks.append(path)
        elif mode == "120000":
            symlinks.append(path)
    for task in tasks:
        for claim in task.write_claims:
            claim_path = PurePosixPath(claim.path)
            for special, code, label in (
                (gitlinks, "UNSUPPORTED_SUBMODULE_SCOPE", "gitlink"),
                (symlinks, "UNSUPPORTED_SYMLINK_SCOPE", "tracked symlink"),
            ):
                for entry in special:
                    crosses = claim_path == entry or entry in claim_path.parents
                    if claim.kind == "prefix":
                        crosses = crosses or claim_path in entry.parents
                    if crosses:
                        raise ExecutionError(
                            code,
                            f"{task.task_id} write scope crosses a {label}",
                        )


def local_branch_exists(cwd: Path, branch_name: str) -> bool:
    result = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        cwd,
        "check local branch",
        check=False,
    )
    return result.returncode == 0


def normalized_repo_path(value: str) -> str:
    candidate = value.strip().replace("\\", "/")
    pure = PurePosixPath(candidate)
    if (
        not candidate
        or pure.is_absolute()
        or candidate == "."
        or ".." in pure.parts
        or ".git" in pure.parts
    ):
        raise ExecutionError("PLAN_INVALID", f"unsafe write claim path: {value}")
    return str(pure)


def _field(section: str, label: str, task_id: str) -> str:
    match = re.search(rf"(?m)^- {re.escape(label)}:\s*(.+?)\s*$", section)
    if match is None or not match.group(1).strip():
        raise ExecutionError("PLAN_INVALID", f"{task_id} is missing {label}")
    return match.group(1).strip()


def _optional_field(section: str, label: str) -> str | None:
    match = re.search(rf"(?m)^- {re.escape(label)}:\s*(.+?)\s*$", section)
    if match is None:
        return None
    value = match.group(1).strip()
    if not value or value.lower() in {"none", "n/a", "na"}:
        return None
    return value


def _split_values(value: str) -> tuple[str, ...]:
    if value.lower() in {"none", "n/a", "na"}:
        return ()
    return tuple(
        item.strip() for item in re.split(r"\s*[;,]\s*", value) if item.strip()
    )


def parse_locked_plan(text: str) -> list[TaskPlan]:
    matches = list(re.finditer(r"(?m)^### (TASK-[0-9]{3,})\s*$", text))
    if not matches:
        raise ExecutionError("PLAN_INVALID", "locked plan has no TASK-* records")
    tasks: list[TaskPlan] = []
    seen: set[str] = set()
    for position, match in enumerate(matches):
        task_id = match.group(1)
        expected_task_id = f"TASK-{position + 1:03d}"
        if task_id != expected_task_id:
            raise ExecutionError(
                "PLAN_INVALID",
                f"task IDs must be contiguous; expected {expected_task_id}, got {task_id}",
            )
        if task_id in seen:
            raise ExecutionError("PLAN_INVALID", f"duplicate task ID: {task_id}")
        seen.add(task_id)
        end = (
            matches[position + 1].start() if position + 1 < len(matches) else len(text)
        )
        section = text[match.end() : end]
        dependencies = _split_values(_field(section, "Depends on", task_id))
        requirements = _split_values(_field(section, "Requirements", task_id))
        if not requirements or any(
            REQUIREMENT_ID_RE.fullmatch(item) is None for item in requirements
        ):
            raise ExecutionError(
                "PLAN_INVALID", f"{task_id} has malformed requirement IDs"
            )
        claim_values = _split_values(_field(section, "Write claims", task_id))
        claims: list[WriteClaim] = []
        ownership_complete = True
        for item in claim_values:
            if item.lower() == "unknown":
                ownership_complete = False
                continue
            claim_match = re.fullmatch(r"(exact|prefix):\s*(.+)", item)
            if claim_match is None:
                raise ExecutionError(
                    "PLAN_INVALID", f"{task_id} has malformed write claim"
                )
            claims.append(
                WriteClaim(
                    claim_match.group(1), normalized_repo_path(claim_match.group(2))
                )
            )
        domains = _split_values(_field(section, "Conflict domains", task_id))
        for domain in domains:
            if domain.lower() == "unknown":
                ownership_complete = False
            elif re.fullmatch(r"[a-z][a-z0-9-]*:[A-Za-z0-9._/-]+", domain) is None:
                raise ExecutionError(
                    "PLAN_INVALID", f"{task_id} has malformed conflict domain"
                )
        if not claims:
            ownership_complete = False
        diagnosis_id = _optional_field(section, "Diagnosis")
        regression_oracle = _optional_field(section, "Regression oracle")
        if (diagnosis_id is None) != (regression_oracle is None):
            raise ExecutionError(
                "PLAN_INVALID",
                f"{task_id} must bind both Diagnosis and Regression oracle",
            )
        if (
            diagnosis_id is not None
            and re.fullmatch(r"[0-9a-f]{64}", diagnosis_id) is None
        ):
            raise ExecutionError(
                "PLAN_INVALID", f"{task_id} has malformed diagnosis ID"
            )
        tasks.append(
            TaskPlan(
                task_id=task_id,
                position=position,
                requirements=requirements,
                goal=_field(section, "Goal", task_id),
                dependencies=dependencies,
                write_claims=tuple(claims),
                conflict_domains=domains,
                validation=_field(section, "Validation", task_id),
                done_criteria=_field(section, "Done criteria", task_id),
                rollback=_field(section, "Rollback or stop conditions", task_id),
                diagnosis_id=diagnosis_id,
                regression_oracle=regression_oracle,
                ownership_complete=ownership_complete,
            )
        )
    known = {task.task_id for task in tasks}
    for task in tasks:
        if task.task_id in task.dependencies:
            raise ExecutionError(
                "DEPENDENCY_CYCLE", f"{task.task_id} depends on itself"
            )
        unknown = sorted(set(task.dependencies) - known)
        if unknown:
            raise ExecutionError(
                "PLAN_INVALID",
                f"{task.task_id} has unknown dependencies: {', '.join(unknown)}",
            )
    _verify_acyclic(tasks)
    return tasks


def _verify_acyclic(tasks: list[TaskPlan]) -> None:
    by_id = {task.task_id: task for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ExecutionError(
                "DEPENDENCY_CYCLE", "task dependency graph contains a cycle"
            )
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id].dependencies:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task in tasks:
        visit(task.task_id)


def claim_overlap(left: WriteClaim, right: WriteClaim) -> bool:
    left_path = PurePosixPath(left.path)
    right_path = PurePosixPath(right.path)
    if left.kind == "exact" and right.kind == "exact":
        return left_path == right_path
    if left.kind == "prefix":
        try:
            right_path.relative_to(left_path)
            return True
        except ValueError:
            pass
    if right.kind == "prefix":
        try:
            left_path.relative_to(right_path)
            return True
        except ValueError:
            pass
    return False


def tasks_conflict(left: TaskPlan, right: TaskPlan) -> bool:
    if not left.ownership_complete or not right.ownership_complete:
        return True
    if any(claim_overlap(a, b) for a in left.write_claims for b in right.write_claims):
        return True
    left_domains = set(left.conflict_domains)
    right_domains = set(right.conflict_domains)
    if left_domains & right_domains:
        return True
    for domain in left_domains | right_domains:
        domain_class = domain.partition(":")[0]
        if domain_class in SINGLETON_DOMAIN_CLASSES:
            return True
    return False


def build_dependency_waves(tasks: list[TaskPlan]) -> list[list[TaskPlan]]:
    waves: list[list[TaskPlan]] = []
    wave_index: dict[str, int] = {}
    unresolved = list(sorted(tasks, key=lambda item: item.position))
    while unresolved:
        progressed = False
        for task in tuple(unresolved):
            if any(dependency not in wave_index for dependency in task.dependencies):
                continue
            earliest = (
                max((wave_index[item] for item in task.dependencies), default=-1) + 1
            )
            target = earliest
            while target < len(waves) and any(
                tasks_conflict(task, peer) for peer in waves[target]
            ):
                target += 1
            if target == len(waves):
                waves.append([])
            waves[target].append(task)
            wave_index[task.task_id] = target
            unresolved.remove(task)
            progressed = True
        if not progressed:
            raise ExecutionError(
                "DEPENDENCY_CYCLE", "task dependency graph cannot advance"
            )
    return waves


def capacity_batches(tasks: list[TaskPlan], capacity: int) -> list[list[TaskPlan]]:
    if capacity < 1:
        raise ExecutionError("PLAN_INVALID", "capacity must be positive")
    return [tasks[index : index + capacity] for index in range(0, len(tasks), capacity)]


def _safe_id(value: str) -> str:
    normalized = SAFE_ID_RE.sub("-", value.lower()).strip("-._")
    if not normalized:
        raise ExecutionError("EXECUTION_STATE_INVALID", "resource ID is empty")
    return normalized[:48]


def execution_dir(run_dir: Path, feature_id: str) -> Path:
    return run_dir / "execution" / _require_id(feature_id, FEATURE_ID_RE, "feature ID")


def coordinator_path(run_dir: Path, feature_id: str) -> Path:
    return execution_dir(run_dir, feature_id) / "coordinator.json"


def wave_path(run_dir: Path, feature_id: str, wave_id: str) -> Path:
    return (
        execution_dir(run_dir, feature_id)
        / "waves"
        / f"{_require_id(wave_id, WAVE_ID_RE, 'wave ID')}.json"
    )


def task_path(run_dir: Path, feature_id: str, wave_id: str, task_id: str) -> Path:
    return (
        execution_dir(run_dir, feature_id)
        / "tasks"
        / _require_id(wave_id, WAVE_ID_RE, "wave ID")
        / f"{_require_id(task_id, TASK_ID_RE, 'task ID')}.json"
    )


def assignment_path(run_dir: Path, feature_id: str, wave_id: str, task_id: str) -> Path:
    return (
        execution_dir(run_dir, feature_id)
        / "assignments"
        / _require_id(wave_id, WAVE_ID_RE, "wave ID")
        / f"{_require_id(task_id, TASK_ID_RE, 'task ID')}.json"
    )


def result_path(run_dir: Path, feature_id: str, wave_id: str, task_id: str) -> Path:
    return (
        execution_dir(run_dir, feature_id)
        / "results"
        / _require_id(wave_id, WAVE_ID_RE, "wave ID")
        / f"{_require_id(task_id, TASK_ID_RE, 'task ID')}.json"
    )


def incoming_handoff_path(
    run_dir: Path, feature_id: str, wave_id: str, task_id: str
) -> Path:
    return (
        execution_dir(run_dir, feature_id)
        / "incoming-handoffs"
        / _require_id(wave_id, WAVE_ID_RE, "wave ID")
        / f"{_require_id(task_id, TASK_ID_RE, 'task ID')}.json"
    )


def journal_path(run_dir: Path, feature_id: str, name: str) -> Path:
    return execution_dir(run_dir, feature_id) / "journals" / f"{name}.jsonl"


def _task_json(task: TaskPlan) -> dict[str, Any]:
    value = asdict(task)
    for field in ("requirements", "dependencies", "conflict_domains"):
        value[field] = list(value[field])
    value["write_claims"] = [asdict(item) for item in task.write_claims]
    return value


def _task_definition_digest(task: TaskPlan) -> str:
    return sha256_json(_task_json(task))


def _load_coordinator(run_dir: Path, feature_id: str) -> dict[str, Any]:
    value = read_json(coordinator_path(run_dir, feature_id))
    if value.get("schema") in {
        "agentic-sdlc/execution-coordinator-v1",
        "agentic-sdlc/execution-coordinator-v2",
        "agentic-sdlc/execution-coordinator-v3",
        "agentic-sdlc/execution-coordinator-v4",
        "agentic-sdlc/execution-coordinator-v5",
        "agentic-sdlc/execution-coordinator-v6",
    }:
        raise ExecutionError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "legacy execution coordinator schema is unsupported",
        )
    if value.get("schema") != COORDINATOR_SCHEMA:
        raise ExecutionError("EXECUTION_STATE_INVALID", "coordinator schema is invalid")
    managed_local = value.get("promotion_source") == "managed-local"
    remote_identity_valid = (
        value.get("default_remote") == "origin"
        and isinstance(value.get("default_branch"), str)
        and bool(value["default_branch"])
        and value.get("default_ref") == f"origin/{value['default_branch']}"
        and SHA_RE.fullmatch(str(value.get("default_head") or "")) is not None
        and value.get("promotion_source") in {"existing", "auto-created"}
        and value.get("base_branch") != value.get("default_branch")
    )
    local_identity_valid = managed_local and all(
        value.get(field) is None
        for field in (
            "default_remote",
            "default_branch",
            "default_ref",
            "default_head",
        )
    )
    if value.get("state_version") != 7 or not (
        remote_identity_valid or local_identity_valid
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "coordinator promotion identity is invalid"
        )
    return value


def _save_coordinator(run_dir: Path, feature_id: str, value: dict[str, Any]) -> None:
    value["updated_at"] = utc_now()
    write_json_atomic(coordinator_path(run_dir, feature_id), value)


def prepare_execution(
    run_dir: Path,
    project_root: Path,
    feature_id: str,
    plan_path: Path,
    capacity: int,
) -> dict[str, Any]:
    feature_id = _require_id(feature_id, FEATURE_ID_RE, "feature ID")
    requested_run_dir = run_dir.expanduser().absolute()
    requested_plan_path = plan_path.expanduser().absolute()
    _reject_symlink_components(requested_run_dir, "private run directory")
    _reject_symlink_components(requested_plan_path, "locked plan path")
    run_dir = requested_run_dir.resolve()
    selected_project_root = project_root.resolve()
    project_root = git_root(selected_project_root)
    try:
        project_scope = (
            selected_project_root.relative_to(project_root).as_posix() or "."
        )
    except ValueError as exc:
        raise ExecutionError(
            "WORKTREE_CONFLICT", "initialized project folder is outside its Git root"
        ) from exc
    plan_path = requested_plan_path.resolve()
    common_dir = git_common_dir(project_root)
    if (
        _inside(run_dir, project_root)
        or _inside(project_root, run_dir)
        or _inside(run_dir, common_dir)
        or _inside(common_dir, run_dir)
    ):
        raise ExecutionError(
            "WORKTREE_CONFLICT",
            "private run directory must be outside Git worktrees and metadata",
        )
    if not _inside(plan_path, run_dir / "plans"):
        raise ExecutionError(
            "PLAN_INVALID",
            "locked plan must live under the private run plans directory",
        )
    lock_path = plan_path.with_suffix(plan_path.suffix + ".lock")
    try:
        _reject_symlink_components(lock_path, "locked-plan marker")
    except ExecutionError as exc:
        raise ExecutionError("PLAN_INVALID", str(exc)) from exc
    if not lock_path.is_file():
        raise ExecutionError(
            "PLAN_INVALID", f"locked-plan marker is missing: {lock_path}"
        )
    if capacity < 1:
        raise ExecutionError("PLAN_INVALID", "capacity must be positive")
    plan_bytes = plan_path.read_bytes()
    plan_digest = sha256_bytes(plan_bytes)
    plan_text = plan_bytes.decode("utf-8")
    if (
        re.search(rf"(?m)^# {re.escape(feature_id)} Plan v[0-9]+\s*$", plan_text)
        is None
    ):
        raise ExecutionError(
            "PLAN_INVALID", "locked plan heading does not match the feature ID"
        )
    tasks = parse_locked_plan(plan_text)
    _verify_claim_paths(project_root, project_scope, tasks)
    waves = build_dependency_waves(tasks)
    anchor = _interop(
        "outer worktree inspection", inspect_outer_anchor, selected_project_root
    )
    if anchor.get("status") == "managed":
        promotion: dict[str, object] = {
            "promotion_branch": str(anchor["branch"]),
            "promotion_initial_head": str(anchor["head"]),
            "promotion_source": "managed-local",
            "remote": None,
            "default_branch": None,
            "default_ref": None,
            "default_head": None,
        }
    elif anchor.get("status") == "unmanaged":
        try:
            promotion = ensure_promotion_branch(
                project_root,
                lifecycle_id=run_dir.name,
                task_slug="sdlc",
            )
        except GitPromotionError as exc:
            code = (
                "WORKTREE_CONFLICT" if "must be clean" in str(exc) else "POLICY_BLOCK"
            )
            raise ExecutionError(code, str(exc)) from exc
    else:
        raise ExecutionError(
            "WORKTREE_CONFLICT",
            "Agentic SDLC cannot start from a Task Implementer persistent lane; "
            "start from the source checkout or an ordinary Worktree child",
        )
    current_branch = str(promotion["promotion_branch"])
    base_head = str(promotion["promotion_initial_head"])
    state_path = coordinator_path(run_dir, feature_id)
    recovering = state_path.exists()
    if state_path.exists():
        coordinator = _load_coordinator(run_dir, feature_id)
        expected = {
            "project_root": str(project_root),
            "selected_project_root": str(selected_project_root),
            "project_scope": project_scope,
            "base_branch": current_branch,
            "base_head": base_head,
            "default_remote": promotion["remote"],
            "default_branch": promotion["default_branch"],
            "default_ref": promotion["default_ref"],
            "plan_digest": plan_digest,
            "git_common_dir": str(common_dir),
        }
        if any(coordinator.get(key) != value for key, value in expected.items()):
            raise ExecutionError(
                "REPLAN_REQUIRED", "prepared execution identity changed"
            )
        integration = Path(str(coordinator["integration_worktree"])).resolve()
        integration_branch = str(coordinator["integration_branch"])
        integration_worktree = integration
        if coordinator.get("status") not in {"preparing", "blocked", "prepared"}:
            registration = worktrees(project_root).get(integration)
            if (
                registration is None
                or branch(integration) != integration_branch
                or head(integration) != coordinator["integration_head"]
            ):
                raise ExecutionError(
                    "WORKTREE_CONFLICT", "integration worktree drifted"
                )
            return coordinator
    else:
        run_id = _safe_id(run_dir.name)
        feature_name = _safe_id(feature_id)
        integration_branch = f"codex/sdlc/{run_id}/{feature_name}/integration"
        integration_worktree = (
            run_dir / "worktrees" / feature_id / "integration"
        ).resolve()
        if integration_worktree.exists():
            raise ExecutionError(
                "WORKTREE_COLLISION", "integration path already exists"
            )
        if local_branch_exists(project_root, integration_branch):
            raise ExecutionError(
                "WORKTREE_COLLISION", "integration branch already exists"
            )
        coordinator = {
            "schema": COORDINATOR_SCHEMA,
            "state_version": 7,
            "feature_id": feature_id,
            "run_id": run_dir.name,
            "project_root": str(project_root),
            "git_root": str(project_root),
            "selected_project_root": str(selected_project_root),
            "project_scope": project_scope,
            "git_common_dir": str(common_dir),
            "base_branch": current_branch,
            "base_head": base_head,
            "default_remote": promotion["remote"],
            "default_branch": promotion["default_branch"],
            "default_ref": promotion["default_ref"],
            "default_head": promotion["default_head"],
            "promotion_source": promotion["promotion_source"],
            "plan_path": str(plan_path),
            "plan_digest": plan_digest,
            "capacity": capacity,
            "status": "preparing",
            "integration_branch": integration_branch,
            "integration_worktree": str(integration_worktree),
            "integration_head": base_head,
            "wave_ids": [f"WAVE-{index:03d}" for index in range(1, len(waves) + 1)],
            "active_wave": None,
            "promoted_head": None,
            "cleanup_retained": [],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        _save_coordinator(run_dir, feature_id, coordinator)
        append_journal(
            journal_path(run_dir, feature_id, "coordinator"),
            {
                "event": "prepare_intent",
                "branch": integration_branch,
                "base": base_head,
            },
        )
    _interop(
        "outer lease acquisition",
        acquire_outer_lease,
        run_dir,
        selected_project_root,
        project_scope,
        base_head,
    )
    _interop(
        "integration resource registration",
        record_outer_resource,
        run_dir,
        selected_project_root,
        kind="integration",
        path=integration_worktree,
        branch=integration_branch,
        state="planned",
    )
    registration = worktrees(project_root).get(integration_worktree)
    branch_exists = local_branch_exists(project_root, integration_branch)
    try:
        if not branch_exists:
            if registration is not None or integration_worktree.exists():
                raise ExecutionError(
                    "WORKTREE_COLLISION", "integration path exists without its branch"
                )
            git(
                project_root,
                ["branch", integration_branch, base_head],
                "create integration branch",
            )
            branch_exists = True
        branch_head = git(
            project_root,
            ["rev-parse", f"refs/heads/{integration_branch}"],
            "read integration branch",
        )
        if branch_head != base_head and registration is None:
            raise ExecutionError(
                "WORKTREE_COLLISION", "integration branch is not at the recorded base"
            )
        if registration is None:
            if integration_worktree.exists():
                raise ExecutionError(
                    "WORKTREE_COLLISION",
                    "integration path is not a registered worktree",
                )
            integration_worktree.parent.mkdir(parents=True, exist_ok=True)
            git(
                project_root,
                [
                    "worktree",
                    "add",
                    "--lock",
                    "--reason",
                    "Agentic SDLC feature integration",
                    str(integration_worktree),
                    integration_branch,
                ],
                "create integration worktree",
            )
    except ExecutionError:
        coordinator["status"] = "blocked"
        coordinator["cleanup_retained"] = [
            {
                "kind": "integration",
                "branch": integration_branch,
                "path": str(integration_worktree),
            }
        ]
        _save_coordinator(run_dir, feature_id, coordinator)
        raise
    if (
        branch(integration_worktree) != integration_branch
        or head(integration_worktree) != base_head
        or not clean(integration_worktree)
        or git_common_dir(integration_worktree) != common_dir
    ):
        coordinator["status"] = "blocked"
        _save_coordinator(run_dir, feature_id, coordinator)
        raise ExecutionError(
            "WORKTREE_CONFLICT", "created integration identity is invalid"
        )
    _interop(
        "integration resource registration",
        record_outer_resource,
        run_dir,
        selected_project_root,
        kind="integration",
        path=integration_worktree,
        branch=integration_branch,
        state="present",
    )
    if recovering and coordinator.get("wave_ids") != [
        f"WAVE-{index:03d}" for index in range(1, len(waves) + 1)
    ]:
        raise ExecutionError("EXECUTION_STATE_INVALID", "recorded wave IDs changed")
    for index, wave_tasks in enumerate(waves, start=1):
        wave_id = f"WAVE-{index:03d}"
        expected_wave = {
            "schema": WAVE_SCHEMA,
            "wave_id": wave_id,
            "feature_id": feature_id,
            "status": "planned",
            "task_ids": [item.task_id for item in wave_tasks],
            "batches": [
                [item.task_id for item in batch]
                for batch in capacity_batches(wave_tasks, capacity)
            ],
            "active_batch_index": None,
            "batch_states": ["pending" for _ in capacity_batches(wave_tasks, capacity)],
            "base_head": None,
            "merged_task_ids": [],
            "integration_head": None,
            "cleanup_retained": [],
        }
        wave_file = wave_path(run_dir, feature_id, wave_id)
        if wave_file.exists():
            existing_wave = read_json(wave_file)
            for key in ("schema", "wave_id", "feature_id", "task_ids", "batches"):
                if existing_wave.get(key) != expected_wave[key]:
                    raise ExecutionError(
                        "EXECUTION_STATE_INVALID", f"recorded {wave_id} changed"
                    )
            if existing_wave.get("status") != "planned":
                raise ExecutionError(
                    "EXECUTION_STATE_INVALID",
                    f"recorded {wave_id} advanced before preparation completed",
                )
        else:
            write_json_atomic(wave_file, expected_wave)
        for task in wave_tasks:
            expected_task = {
                "schema": TASK_SCHEMA,
                "wave_id": wave_id,
                "status": "planned",
                "task": _task_json(task),
                "task_definition_digest": _task_definition_digest(task),
                "assignment_digest": None,
                "result_digest": None,
                "commit": None,
                "attempt": 0,
                "worker_session_hash": None,
                "worker_session_hash_history": [],
                "dispatched_at": None,
                "worker_started_at": None,
                "last_heartbeat_at": None,
                "heartbeat_sequence": 0,
                "heartbeat_phase": None,
                "finish_intent": None,
            }
            task_file = task_path(run_dir, feature_id, wave_id, task.task_id)
            if task_file.exists():
                existing_task = read_json(task_file)
                _validate_task_liveness(existing_task)
                if (
                    existing_task.get("wave_id") != wave_id
                    or existing_task.get("task") != expected_task["task"]
                    or existing_task.get("task_definition_digest")
                    != expected_task["task_definition_digest"]
                ):
                    raise ExecutionError(
                        "EXECUTION_STATE_INVALID",
                        f"recorded {task.task_id} changed",
                    )
            else:
                write_json_atomic(task_file, expected_task)
    coordinator["status"] = "prepared"
    coordinator["cleanup_retained"] = []
    _save_coordinator(run_dir, feature_id, coordinator)
    append_journal(
        journal_path(run_dir, feature_id, "coordinator"),
        {"event": "prepared", "head": base_head, "waves": coordinator["wave_ids"]},
    )
    return coordinator


def replan_future(
    run_dir: Path,
    feature_id: str,
    plan_path: Path,
    capacity: int,
) -> dict[str, Any]:
    with _execution_transition_lock(run_dir, feature_id):
        return _replan_future_locked(
            run_dir,
            feature_id,
            plan_path,
            capacity,
        )


def _replan_future_locked(
    run_dir: Path,
    feature_id: str,
    plan_path: Path,
    capacity: int,
) -> dict[str, Any]:
    coordinator = _load_coordinator(run_dir, feature_id)
    if coordinator.get("status") in {"sealed", "promoted", "done"}:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "sealed execution cannot replan"
        )
    requested = plan_path.expanduser().absolute()
    _reject_symlink_components(requested, "replacement locked plan")
    plan_path = requested.resolve()
    if not _inside(plan_path, run_dir.resolve() / "plans"):
        raise ExecutionError(
            "PLAN_INVALID", "replacement plan must live under run plans"
        )
    lock_path = plan_path.with_suffix(plan_path.suffix + ".lock")
    if not lock_path.is_file() or lock_path.is_symlink():
        raise ExecutionError(
            "PLAN_INVALID", "replacement locked-plan marker is missing"
        )
    if capacity < 1:
        raise ExecutionError("PLAN_INVALID", "capacity must be positive")
    raw = plan_path.read_bytes()
    digest = sha256_bytes(raw)
    if digest == coordinator.get("plan_digest"):
        return coordinator
    text = raw.decode("utf-8")
    if re.search(rf"(?m)^# {re.escape(feature_id)} Plan v[0-9]+\s*$", text) is None:
        raise ExecutionError("PLAN_INVALID", "replacement plan heading is invalid")
    tasks = parse_locked_plan(text)
    _verify_claim_paths(
        Path(str(coordinator["project_root"])), str(coordinator["project_scope"]), tasks
    )
    new_waves = build_dependency_waves(tasks)
    old_wave_ids = list(coordinator["wave_ids"])
    preserved = 0
    for wave_id in old_wave_ids:
        wave = read_json(wave_path(run_dir, feature_id, wave_id))
        if wave.get("status") == "planned":
            break
        preserved += 1
    if len(new_waves) < preserved:
        raise ExecutionError(
            "REPLAN_REQUIRED", "replacement plan removes active history"
        )
    for index in range(preserved):
        old = read_json(wave_path(run_dir, feature_id, old_wave_ids[index]))
        new_ids = [task.task_id for task in new_waves[index]]
        if old.get("task_ids") != new_ids:
            raise ExecutionError(
                "REPLAN_REQUIRED", "replacement plan changes completed or active waves"
            )
        for task in new_waves[index]:
            recorded = read_json(
                task_path(
                    run_dir,
                    feature_id,
                    old_wave_ids[index],
                    task.task_id,
                )
            )
            expected_definition = _task_json(task)
            expected_digest = _task_definition_digest(task)
            if (
                recorded.get("task") != expected_definition
                or recorded.get("task_definition_digest") != expected_digest
                or sha256_json(expected_definition) != expected_digest
            ):
                raise ExecutionError(
                    "REPLAN_REQUIRED",
                    "replacement plan changes a completed or active task definition",
                )
    feature_execution = execution_dir(run_dir, feature_id)
    integration = Path(str(coordinator["integration_worktree"]))
    registered_worktrees = worktrees(integration)
    old_future_wave_ids = old_wave_ids[preserved:]
    for wave_id in old_future_wave_ids:
        wave = read_json(wave_path(run_dir, feature_id, wave_id))
        if wave.get("status") != "planned":
            raise ExecutionError(
                "REPLAN_REQUIRED", "future wave already owns resources"
            )
        assignments = feature_execution / "assignments" / wave_id
        results = feature_execution / "results" / wave_id
        worker_root = (run_dir / "worktrees" / feature_id / "waves" / wave_id).resolve()
        journal = journal_path(run_dir, feature_id, wave_id)
        task_ids = [str(item) for item in wave.get("task_ids", [])]
        branch_names = [
            f"codex/sdlc/{_safe_id(run_dir.name)}/{_safe_id(feature_id)}/"
            f"{_safe_id(wave_id)}/{_safe_id(task_id)}"
            for task_id in task_ids
        ]
        registered = any(
            path == worker_root or _inside(path, worker_root)
            for path in registered_worktrees
        )
        if (
            (assignments.exists() and any(assignments.iterdir()))
            or (results.exists() and any(results.iterdir()))
            or worker_root.exists()
            or worker_root.is_symlink()
            or registered
            or journal.exists()
            or any(local_branch_exists(integration, name) for name in branch_names)
        ):
            raise ExecutionError(
                "REPLAN_REQUIRED", "future wave already owns resources"
            )
    new_wave_ids = [f"WAVE-{index:03d}" for index in range(1, len(new_waves) + 1)]
    for wave_id in new_wave_ids[preserved:]:
        if wave_id in old_future_wave_ids:
            continue
        if (
            wave_path(run_dir, feature_id, wave_id).exists()
            or (feature_execution / "tasks" / wave_id).exists()
        ):
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "replacement wave collides with stale state"
            )
    old_digest = str(coordinator["plan_digest"])
    append_journal(
        journal_path(run_dir, feature_id, "coordinator"),
        {
            "event": "future_replan_intent",
            "old_digest": old_digest,
            "new_digest": digest,
        },
    )
    stage_root = Path(
        tempfile.mkdtemp(prefix=".future-replan-stage.", dir=str(feature_execution))
    )
    backup_root = Path(
        tempfile.mkdtemp(prefix=".future-replan-backup.", dir=str(feature_execution))
    )
    for index, wave_tasks in enumerate(new_waves[preserved:], start=preserved + 1):
        wave_id = f"WAVE-{index:03d}"
        write_json_atomic(
            stage_root / "waves" / f"{wave_id}.json",
            {
                "schema": WAVE_SCHEMA,
                "wave_id": wave_id,
                "feature_id": feature_id,
                "status": "planned",
                "task_ids": [task.task_id for task in wave_tasks],
                "batches": [
                    [task.task_id for task in batch]
                    for batch in capacity_batches(wave_tasks, capacity)
                ],
                "active_batch_index": None,
                "batch_states": [
                    "pending" for _ in capacity_batches(wave_tasks, capacity)
                ],
                "base_head": None,
                "merged_task_ids": [],
                "integration_head": None,
                "cleanup_retained": [],
            },
        )
        for task in wave_tasks:
            write_json_atomic(
                stage_root / "tasks" / wave_id / f"{task.task_id}.json",
                {
                    "schema": TASK_SCHEMA,
                    "wave_id": wave_id,
                    "status": "planned",
                    "task": _task_json(task),
                    "task_definition_digest": _task_definition_digest(task),
                    "assignment_digest": None,
                    "result_digest": None,
                    "commit": None,
                    "attempt": 0,
                    "worker_session_hash": None,
                    "worker_session_hash_history": [],
                    "dispatched_at": None,
                    "worker_started_at": None,
                    "last_heartbeat_at": None,
                    "heartbeat_sequence": 0,
                    "heartbeat_phase": None,
                    "finish_intent": None,
                },
            )
    replaced: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    affected_wave_ids = sorted(set(old_future_wave_ids) | set(new_wave_ids[preserved:]))
    try:
        for wave_id in affected_wave_ids:
            for live in (
                feature_execution / "waves" / f"{wave_id}.json",
                feature_execution / "tasks" / wave_id,
            ):
                if live.exists() or live.is_symlink():
                    relative = live.relative_to(feature_execution)
                    backup = backup_root / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(live, backup)
                    replaced.append((live, backup))
        for wave_id in new_wave_ids[preserved:]:
            for staged, live in (
                (
                    stage_root / "waves" / f"{wave_id}.json",
                    feature_execution / "waves" / f"{wave_id}.json",
                ),
                (
                    stage_root / "tasks" / wave_id,
                    feature_execution / "tasks" / wave_id,
                ),
            ):
                live.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, live)
                installed.append(live)
        updated = dict(coordinator)
        updated["plan_path"] = str(plan_path)
        updated["plan_digest"] = digest
        updated["capacity"] = capacity
        updated["wave_ids"] = new_wave_ids
        _save_coordinator(run_dir, feature_id, updated)
        coordinator = updated
    except Exception:
        for live in reversed(installed):
            if live.is_dir() and not live.is_symlink():
                shutil.rmtree(live)
            elif live.exists() or live.is_symlink():
                live.unlink()
        for live, backup in reversed(replaced):
            live.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, live)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        shutil.rmtree(backup_root, ignore_errors=True)
    append_journal(
        journal_path(run_dir, feature_id, "coordinator"),
        {"event": "future_replanned", "old_digest": old_digest, "new_digest": digest},
    )
    return coordinator


def seal_tdd_base(run_dir: Path, feature_id: str, message: str) -> dict[str, Any]:
    _reject_sensitive_evidence(message)
    coordinator = _load_coordinator(run_dir, feature_id)
    if coordinator["status"] not in {"prepared", "tdd_sealed"}:
        raise ExecutionError("EXECUTION_STATE_INVALID", "TDD base cannot be sealed now")
    integration = Path(coordinator["integration_worktree"])
    if coordinator["status"] == "tdd_sealed":
        if head(integration) != coordinator["integration_head"] or not clean(
            integration
        ):
            raise ExecutionError("WORKTREE_CONFLICT", "sealed TDD base drifted")
        return coordinator
    recorded_head = coordinator["integration_head"]
    current_head = head(integration)
    coordinator_journal = journal_path(run_dir, feature_id, "coordinator")
    if current_head != recorded_head:
        parents = git(
            integration,
            ["rev-list", "--parents", "-n", "1", current_head],
            "inspect interrupted TDD seal",
        ).split()
        subject = git(
            integration,
            ["show", "-s", "--format=%s", current_head],
            "inspect interrupted TDD seal message",
        )
        if (
            not clean(integration)
            or len(parents) != 2
            or parents[1] != recorded_head
            or subject != message
            or not _journal_has_intent(
                coordinator_journal, "tdd_seal_intent", recorded_head, message
            )
        ):
            raise ExecutionError(
                "WORKTREE_CONFLICT", "integration HEAD drifted before TDD seal"
            )
        _verify_integration_commit(
            integration,
            recorded_head,
            current_head,
            str(coordinator["project_scope"]),
        )
    elif not clean(integration):
        append_journal(
            coordinator_journal,
            {"event": "tdd_seal_intent", "base": recorded_head, "message": message},
        )
        git(integration, ["add", "-A"], "stage TDD base")
        try:
            _verify_staged_integration(integration, str(coordinator["project_scope"]))
            git(integration, ["commit", "-m", message], "commit TDD base")
        except ExecutionError:
            git(integration, ["restore", "--staged", ":/"], "unstage TDD base")
            raise
    coordinator["integration_head"] = head(integration)
    coordinator["status"] = "tdd_sealed"
    _save_coordinator(run_dir, feature_id, coordinator)
    append_journal(
        journal_path(run_dir, feature_id, "coordinator"),
        {"event": "tdd_sealed", "head": coordinator["integration_head"]},
    )
    return coordinator


def _task_plan_from_state(value: dict[str, Any]) -> TaskPlan:
    _validate_task_liveness(value)
    raw = value.get("task")
    if not isinstance(raw, dict):
        raise ExecutionError("EXECUTION_STATE_INVALID", "task record is invalid")
    return TaskPlan(
        task_id=str(raw["task_id"]),
        position=int(raw["position"]),
        requirements=tuple(raw.get("requirements", [])),
        goal=str(raw["goal"]),
        dependencies=tuple(raw.get("dependencies", [])),
        write_claims=tuple(WriteClaim(**item) for item in raw.get("write_claims", [])),
        conflict_domains=tuple(raw.get("conflict_domains", [])),
        validation=str(raw["validation"]),
        done_criteria=str(raw["done_criteria"]),
        rollback=str(raw["rollback"]),
        diagnosis_id=(
            str(raw["diagnosis_id"]) if raw.get("diagnosis_id") is not None else None
        ),
        regression_oracle=(
            str(raw["regression_oracle"])
            if raw.get("regression_oracle") is not None
            else None
        ),
        ownership_complete=bool(raw["ownership_complete"]),
    )


def _read_incoming_handoff(path: Path) -> dict[str, Any]:
    if (
        path.is_symlink()
        or not path.is_file()
        or (path.stat().st_mode & 0o777) != 0o600
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "incoming handoff path or mode is invalid"
        )
    value = read_json(path)
    required = {
        "schema",
        "feature_id",
        "wave_id",
        "task_id",
        "assignment_base_head",
        "dependencies",
        "predecessors",
        "created_at",
        "handoff_digest",
    }
    unsigned = dict(value)
    recorded = unsigned.pop("handoff_digest", None)
    if (
        set(value) != required
        or value.get("schema") != INCOMING_HANDOFF_SCHEMA
        or not isinstance(value.get("dependencies"), list)
        or not isinstance(value.get("predecessors"), list)
        or not isinstance(recorded, str)
        or recorded != sha256_json(unsigned)
    ):
        raise ExecutionError("EXECUTION_STATE_INVALID", "incoming handoff is invalid")
    return value


def _validate_assignment_record(assignment: dict[str, Any]) -> None:
    if assignment.get("schema") in {
        "agentic-sdlc/worker-assignment-v1",
        "agentic-sdlc/worker-assignment-v2",
    }:
        raise ExecutionError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "legacy worker assignment schema is unsupported",
        )
    unsigned = dict(assignment)
    recorded = unsigned.pop("assignment_digest", None)
    if (
        assignment.get("schema") != ASSIGNMENT_SCHEMA
        or not isinstance(recorded, str)
        or recorded != sha256_json(unsigned)
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker assignment digest is invalid"
        )
    helper = Path(str(assignment.get("execution_helper") or ""))
    bound_run_dir = Path(str(assignment.get("run_dir") or ""))
    dependencies = assignment.get("dependencies")
    if not isinstance(dependencies, list) or any(
        not isinstance(item, str) for item in dependencies
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker assignment dependencies are invalid"
        )
    expected_profile = worker_liveness_profile(dependencies)
    expected_liveness = {
        "worker_profile": expected_profile["worker_profile"],
        "heartbeat_seconds": WORKER_HEARTBEAT_SECONDS,
        "start_seconds": WORKER_START_SECONDS,
        "read_only_warning_seconds": expected_profile[
            "read_only_warning_seconds"
        ],
        "read_only_seconds": expected_profile["read_only_seconds"],
        "stall_seconds": WORKER_STALL_SECONDS,
        "max_seconds": WORKER_MAX_SECONDS,
        "worker_phases": list(WORKER_PHASES),
    }
    expected_helper = Path(__file__).with_name("sdlc_execution.py").resolve()
    if (
        not helper.is_absolute()
        or helper.is_symlink()
        or helper.resolve() != expected_helper
        or not helper.is_file()
        or not bound_run_dir.is_absolute()
        or bound_run_dir.is_symlink()
        or any(assignment.get(key) != value for key, value in expected_liveness.items())
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker assignment liveness is invalid"
        )


def _validate_result_record(result: dict[str, Any], assignment: dict[str, Any]) -> None:
    unsigned = dict(result)
    recorded = unsigned.pop("result_digest", None)
    if (
        result.get("schema") != RESULT_SCHEMA
        or not isinstance(recorded, str)
        or recorded != sha256_json(unsigned)
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker result digest is invalid"
        )
    expected_oracle = assignment.get("regression_oracle")
    evidence = result.get("regression_oracle_evidence")
    if expected_oracle:
        if not isinstance(evidence, dict):
            raise ExecutionError(
                "INTEGRATION_VALIDATION_FAILED",
                "corrective result is missing regression-oracle evidence",
            )
        evidence_unsigned = dict(evidence)
        evidence_digest = evidence_unsigned.pop("evidence_digest", None)
        if (
            evidence.get("schema") != "agentic-sdlc/regression-oracle-evidence-v1"
            or evidence.get("diagnosis_id") != assignment.get("diagnosis_id")
            or evidence.get("oracle") != expected_oracle
            or evidence.get("outcome") != "passed"
            or evidence.get("commit") != result.get("commit")
            or not isinstance(evidence.get("evidence_reference"), str)
            or not evidence["evidence_reference"].strip()
            or evidence_digest != sha256_json(evidence_unsigned)
        ):
            raise ExecutionError(
                "INTEGRATION_VALIDATION_FAILED",
                "corrective regression-oracle evidence is invalid",
            )
    elif evidence is not None:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID",
            "non-corrective result must not carry regression-oracle evidence",
        )


def _validate_regression_oracle_claim(
    assignment: dict[str, Any],
    supplied: dict[str, Any] | None,
) -> tuple[str, str, str] | None:
    expected_oracle = assignment.get("regression_oracle")
    if not expected_oracle:
        if supplied is not None:
            raise ExecutionError(
                "EXECUTION_STATE_INVALID",
                "non-corrective task supplied regression-oracle evidence",
            )
        return None
    if not isinstance(supplied, dict):
        raise ExecutionError(
            "INTEGRATION_VALIDATION_FAILED",
            "corrective task requires structured regression-oracle evidence",
        )
    oracle = supplied.get("oracle")
    outcome = supplied.get("outcome")
    evidence_reference = supplied.get("evidence_reference")
    if (
        oracle != expected_oracle
        or outcome != "passed"
        or not isinstance(evidence_reference, str)
        or not evidence_reference.strip()
    ):
        raise ExecutionError(
            "INTEGRATION_VALIDATION_FAILED",
            "corrective task did not prove the exact original regression oracle passed",
        )
    _reject_sensitive_evidence(oracle, outcome, evidence_reference)
    return oracle, outcome, evidence_reference.strip()


def _build_regression_oracle_evidence(
    assignment: dict[str, Any],
    supplied: dict[str, Any] | None,
    commit: str,
) -> dict[str, Any] | None:
    claim = _validate_regression_oracle_claim(assignment, supplied)
    if claim is None:
        return None
    oracle, outcome, evidence_reference = claim
    evidence = {
        "schema": "agentic-sdlc/regression-oracle-evidence-v1",
        "diagnosis_id": assignment["diagnosis_id"],
        "oracle": oracle,
        "outcome": outcome,
        "evidence_reference": evidence_reference,
        "commit": commit,
    }
    evidence["evidence_digest"] = sha256_json(evidence)
    return evidence


def _find_task_wave(
    run_dir: Path, feature_id: str, coordinator: dict[str, Any], task_id: str
) -> str | None:
    for candidate_wave in coordinator["wave_ids"]:
        candidate = read_json(wave_path(run_dir, feature_id, candidate_wave))
        if task_id in candidate.get("task_ids", []):
            return str(candidate_wave)
    return None


def _build_incoming_handoff(
    run_dir: Path,
    feature_id: str,
    coordinator: dict[str, Any],
    wave_id: str,
    task: TaskPlan,
    base_head: str,
    created_at: str,
) -> dict[str, Any]:
    predecessors: list[dict[str, Any]] = []
    predecessor_ids: list[str] = []
    wave_position = coordinator["wave_ids"].index(wave_id)
    for prior_wave_id in coordinator["wave_ids"][:wave_position]:
        prior_wave = read_json(wave_path(run_dir, feature_id, prior_wave_id))
        predecessor_ids.extend(str(item) for item in prior_wave["task_ids"])
    current_wave = read_json(wave_path(run_dir, feature_id, wave_id))
    task_batch_index = next(
        index
        for index, batch in enumerate(current_wave["batches"])
        if task.task_id in batch
    )
    for prior_batch in current_wave["batches"][:task_batch_index]:
        predecessor_ids.extend(str(item) for item in prior_batch)
    predecessor_ids.extend(
        dependency
        for dependency in task.dependencies
        if dependency not in predecessor_ids
    )
    for dependency in predecessor_ids:
        dependency_wave = _find_task_wave(run_dir, feature_id, coordinator, dependency)
        if dependency_wave is None:
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "dependency task state is missing"
            )
        dependency_task = read_json(
            task_path(run_dir, feature_id, dependency_wave, dependency)
        )
        dependency_assignment = read_json(
            assignment_path(run_dir, feature_id, dependency_wave, dependency)
        )
        dependency_result = read_json(
            result_path(run_dir, feature_id, dependency_wave, dependency)
        )
        _validate_assignment_record(dependency_assignment)
        _validate_result_record(dependency_result, dependency_assignment)
        unsigned_assignment = dict(dependency_assignment)
        assignment_digest = unsigned_assignment.pop("assignment_digest", None)
        unsigned_result = dict(dependency_result)
        result_digest = unsigned_result.pop("result_digest", None)
        if (
            dependency_task.get("status") not in {"committed", "merged"}
            or dependency_assignment.get("schema") != ASSIGNMENT_SCHEMA
            or assignment_digest != sha256_json(unsigned_assignment)
            or dependency_result.get("schema") != RESULT_SCHEMA
            or result_digest != sha256_json(unsigned_result)
            or dependency_task.get("result_digest") != result_digest
            or dependency_result.get("assignment_digest") != assignment_digest
        ):
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "dependency result is not accepted"
            )
        predecessors.append(
            {
                "task_id": dependency,
                "wave_id": dependency_wave,
                "assignment_digest": dependency_assignment.get("assignment_digest"),
                "result_digest": dependency_result.get("result_digest"),
                "commit": dependency_result.get("commit"),
                "changed_paths": dependency_result.get("changed_paths"),
                "summary": dependency_result.get("summary"),
                "decisions": dependency_result.get("decisions"),
                "open_risks": dependency_result.get("open_risks"),
                "validation": dependency_result.get("validation"),
                "review": dependency_result.get("review"),
            }
        )
    value: dict[str, Any] = {
        "schema": INCOMING_HANDOFF_SCHEMA,
        "feature_id": feature_id,
        "wave_id": wave_id,
        "task_id": task.task_id,
        "assignment_base_head": base_head,
        "dependencies": list(task.dependencies),
        "predecessors": predecessors,
        "created_at": created_at,
    }
    value["handoff_digest"] = sha256_json(value)
    return value


def _validate_assignment_handoff(
    run_dir: Path,
    feature_id: str,
    coordinator: dict[str, Any],
    assignment: dict[str, Any],
    task: TaskPlan,
) -> dict[str, Any]:
    path = Path(str(assignment.get("incoming_handoff_path") or ""))
    _validate_assignment_record(assignment)
    expected_path = incoming_handoff_path(
        run_dir, feature_id, str(assignment["wave_id"]), task.task_id
    ).resolve()
    if not path.is_absolute() or path.resolve() != expected_path:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "incoming handoff path is invalid"
        )
    handoff = _read_incoming_handoff(path)
    expected = _build_incoming_handoff(
        run_dir,
        feature_id,
        coordinator,
        str(assignment["wave_id"]),
        task,
        str(assignment["base_head"]),
        str(handoff.get("created_at")),
    )
    if handoff != expected or handoff.get("handoff_digest") != assignment.get(
        "incoming_handoff_digest"
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "incoming handoff context is invalid"
        )
    return handoff


def prepare_wave(run_dir: Path, feature_id: str, wave_id: str) -> list[dict[str, Any]]:
    coordinator = _load_coordinator(run_dir, feature_id)
    if coordinator["status"] not in {"tdd_sealed", "waves_running"}:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "wave preparation is not allowed"
        )
    if coordinator.get("active_wave") not in {None, wave_id}:
        raise ExecutionError("EXECUTION_STATE_INVALID", "another wave is active")
    if wave_id not in coordinator["wave_ids"]:
        raise ExecutionError("EXECUTION_STATE_INVALID", "unknown wave")
    wave = read_json(wave_path(run_dir, feature_id, wave_id))
    if wave["status"] in {"running", "integrating", "integrated", "done"}:
        active_index = wave.get("active_batch_index")
        task_ids = (
            wave["batches"][active_index]
            if isinstance(active_index, int)
            else wave["task_ids"]
        )
        assignments = [
            read_json(assignment_path(run_dir, feature_id, wave_id, task_id))
            for task_id in task_ids
            if assignment_path(run_dir, feature_id, wave_id, task_id).exists()
        ]
        for assignment in assignments:
            _validate_assignment_record(assignment)
        return assignments
    if wave["status"] not in {"planned", "preparing", "blocked"}:
        raise ExecutionError("EXECUTION_STATE_INVALID", "wave is not preparable")
    wave_position = coordinator["wave_ids"].index(wave_id)
    for prior in coordinator["wave_ids"][:wave_position]:
        if read_json(wave_path(run_dir, feature_id, prior)).get("status") != "done":
            raise ExecutionError("EXECUTION_STATE_INVALID", "prior wave is incomplete")
    integration = Path(coordinator["integration_worktree"])
    if not clean(integration) or head(integration) != coordinator["integration_head"]:
        raise ExecutionError(
            "WORKTREE_CONFLICT", "integration worktree is not at its recorded tip"
        )
    base_head = head(integration)
    wave["status"] = "preparing"
    wave["base_head"] = base_head
    if wave.get("active_batch_index") is None:
        wave["active_batch_index"] = 0
        wave["batch_states"][0] = "active"
    write_json_atomic(wave_path(run_dir, feature_id, wave_id), wave)
    coordinator["active_wave"] = wave_id
    coordinator["status"] = "waves_running"
    _save_coordinator(run_dir, feature_id, coordinator)
    assignments: list[dict[str, Any]] = []
    active_batch_index = int(wave["active_batch_index"])
    for task_id in wave["batches"][active_batch_index]:
        task_record_path = task_path(run_dir, feature_id, wave_id, task_id)
        task_record = read_json(task_record_path)
        task = _task_plan_from_state(task_record)
        branch_name = (
            f"codex/sdlc/{_safe_id(run_dir.name)}/{_safe_id(feature_id)}/"
            f"{_safe_id(wave_id)}/{_safe_id(task_id)}"
        )
        worktree_path = (
            run_dir / "worktrees" / feature_id / "waves" / wave_id / task_id
        ).resolve()
        assignment_file = assignment_path(run_dir, feature_id, wave_id, task_id)
        if assignment_file.exists():
            existing = read_json(assignment_file)
            _validate_assignment_record(existing)
            worker = Path(str(existing.get("worktree") or ""))
            if (
                existing.get("plan_digest") != coordinator["plan_digest"]
                or existing.get("base_head") != base_head
                or not worker.is_absolute()
                or not worker.exists()
                or branch(worker) != existing.get("branch")
                or head(worker) != base_head
                or not clean(worker)
                or git_common_dir(worker) != Path(coordinator["git_common_dir"])
            ):
                raise ExecutionError(
                    "WORKTREE_CONFLICT", f"recorded worker drifted for {task_id}"
                )
            _validate_assignment_handoff(
                run_dir, feature_id, coordinator, existing, task
            )
            assignments.append(existing)
            continue
        handoff_file = incoming_handoff_path(run_dir, feature_id, wave_id, task_id)
        existing_handoff = (
            _read_incoming_handoff(handoff_file) if handoff_file.exists() else None
        )
        created_at = (
            str(existing_handoff["created_at"])
            if existing_handoff is not None
            else utc_now()
        )
        incoming_handoff = _build_incoming_handoff(
            run_dir,
            feature_id,
            coordinator,
            wave_id,
            task,
            base_head,
            created_at,
        )
        handoff_file.parent.mkdir(parents=True, exist_ok=True)
        handoff_file.parent.chmod(0o700)
        if existing_handoff is not None and existing_handoff != incoming_handoff:
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "immutable incoming handoff differs"
            )
        if existing_handoff is None:
            write_json_atomic(handoff_file, incoming_handoff)
        assignment: dict[str, Any] = {
            "schema": ASSIGNMENT_SCHEMA,
            "feature_id": feature_id,
            "wave_id": wave_id,
            "task_id": task_id,
            "plan_digest": coordinator["plan_digest"],
            "base_head": base_head,
            "branch": branch_name,
            "worktree": str(worktree_path),
            "project_root": coordinator["project_root"],
            "git_root": coordinator["git_root"],
            "project_scope": coordinator["project_scope"],
            "scope_cwd": str(
                worktree_path
                if coordinator["project_scope"] == "."
                else worktree_path / coordinator["project_scope"]
            ),
            "git_common_dir": coordinator["git_common_dir"],
            "write_claims": [asdict(item) for item in task.write_claims],
            "conflict_domains": list(task.conflict_domains),
            "requirements": list(task.requirements),
            "goal": task.goal,
            "dependencies": list(task.dependencies),
            "validation": task.validation,
            "done_criteria": task.done_criteria,
            "rollback": task.rollback,
            "diagnosis_id": task.diagnosis_id,
            "regression_oracle": task.regression_oracle,
            "incoming_handoff_path": str(handoff_file),
            "incoming_handoff_digest": incoming_handoff["handoff_digest"],
            "execution_helper": str(
                Path(__file__).with_name("sdlc_execution.py").resolve()
            ),
            "run_dir": str(run_dir.resolve()),
            "heartbeat_seconds": WORKER_HEARTBEAT_SECONDS,
            "start_seconds": WORKER_START_SECONDS,
            "stall_seconds": WORKER_STALL_SECONDS,
            "max_seconds": WORKER_MAX_SECONDS,
            "worker_phases": list(WORKER_PHASES),
            "created_at": created_at,
        }
        assignment.update(worker_liveness_profile(task.dependencies))
        assignment["assignment_digest"] = sha256_json(assignment)
        selected_project_root = Path(str(coordinator["selected_project_root"]))
        _interop(
            "worker resource registration",
            record_outer_resource,
            run_dir,
            selected_project_root,
            kind="worker",
            path=worktree_path,
            branch=branch_name,
            state="planned",
        )
        registered = worktrees(integration).get(worktree_path)
        branch_exists = local_branch_exists(integration, branch_name)
        if worktree_path.exists() or registered is not None:
            if not (
                worktree_path.exists()
                and registered is not None
                and branch_exists
                and branch(worktree_path) == branch_name
                and head(worktree_path) == base_head
                and clean(worktree_path)
                and git_common_dir(worktree_path) == Path(coordinator["git_common_dir"])
            ):
                raise ExecutionError(
                    "WORKTREE_COLLISION", f"worker resource collision for {task_id}"
                )
        else:
            append_journal(
                journal_path(run_dir, feature_id, wave_id),
                {
                    "event": "worker_create_intent",
                    "task_id": task_id,
                    "branch": branch_name,
                },
            )
            try:
                if branch_exists:
                    branch_head = git(
                        integration,
                        ["rev-parse", f"refs/heads/{branch_name}"],
                        "read interrupted worker branch",
                    )
                    if branch_head != base_head:
                        raise ExecutionError(
                            "WORKTREE_COLLISION",
                            f"worker branch is not at the wave base for {task_id}",
                        )
                else:
                    git(
                        integration,
                        ["branch", branch_name, base_head],
                        "create worker branch",
                    )
                worktree_path.parent.mkdir(parents=True, exist_ok=True)
                git(
                    integration,
                    [
                        "worktree",
                        "add",
                        "--lock",
                        "--reason",
                        f"Agentic SDLC {feature_id} {task_id}",
                        str(worktree_path),
                        branch_name,
                    ],
                    "create worker worktree",
                )
            except ExecutionError:
                wave["status"] = "blocked"
                wave["blocker"] = "WORKTREE_CONFLICT"
                wave["cleanup_retained"].append(
                    {
                        "task_id": task_id,
                        "branch": branch_name,
                        "path": str(worktree_path),
                    }
                )
                write_json_atomic(wave_path(run_dir, feature_id, wave_id), wave)
                raise
        if (
            branch(worktree_path) != branch_name
            or head(worktree_path) != base_head
            or not clean(worktree_path)
            or git_common_dir(worktree_path) != Path(coordinator["git_common_dir"])
        ):
            raise ExecutionError(
                "WORKTREE_CONFLICT", f"worker identity invalid for {task_id}"
            )
        scope_cwd = Path(str(assignment["scope_cwd"]))
        if scope_cwd.is_symlink() or not scope_cwd.is_dir():
            raise ExecutionError(
                "WORKTREE_CONFLICT", f"worker project scope is invalid for {task_id}"
            )
        _interop(
            "worker resource registration",
            record_outer_resource,
            run_dir,
            selected_project_root,
            kind="worker",
            path=worktree_path,
            branch=branch_name,
            state="present",
        )
        write_json_atomic(assignment_file, assignment)
        task_record["status"] = "assigned"
        task_record["assignment_digest"] = assignment["assignment_digest"]
        write_json_atomic(task_record_path, task_record)
        assignments.append(assignment)
    wave["status"] = "running"
    wave["blocker"] = None
    wave["cleanup_retained"] = []
    write_json_atomic(wave_path(run_dir, feature_id, wave_id), wave)
    return assignments


def advance_batch(run_dir: Path, feature_id: str, wave_id: str) -> list[dict[str, Any]]:
    _load_coordinator(run_dir, feature_id)
    wave_file = wave_path(run_dir, feature_id, wave_id)
    wave = read_json(wave_file)
    active_index = wave.get("active_batch_index")
    if wave.get("status") != "running" or not isinstance(active_index, int):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "wave has no active capacity batch"
        )
    current_task_ids = wave["batches"][active_index]
    for task_id in current_task_ids:
        task_record = read_json(task_path(run_dir, feature_id, wave_id, task_id))
        if task_record.get("status") != "committed":
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "active capacity batch is incomplete"
            )
    wave["batch_states"][active_index] = "done"
    next_index = active_index + 1
    if next_index >= len(wave["batches"]):
        wave["active_batch_index"] = None
        write_json_atomic(wave_file, wave)
        return []
    wave["active_batch_index"] = next_index
    wave["batch_states"][next_index] = "active"
    wave["status"] = "preparing"
    write_json_atomic(wave_file, wave)
    return prepare_wave(run_dir, feature_id, wave_id)


def arm_task(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    assignment_digest: str,
    *,
    clock: Callable[[], datetime] = _system_clock,
) -> dict[str, Any]:
    with _execution_transition_lock(run_dir, feature_id):
        coordinator = _load_coordinator(run_dir, feature_id)
        assignment = read_json(
            assignment_path(run_dir, feature_id, wave_id, task_id)
        )
        _validate_assignment_record(assignment)
        if assignment.get("assignment_digest") != assignment_digest:
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "assignment digest mismatch"
            )
        task_record_path = task_path(run_dir, feature_id, wave_id, task_id)
        task_record = read_json(task_record_path)
        _validate_task_liveness(task_record)
        _validate_assignment_handoff(
            run_dir,
            feature_id,
            coordinator,
            assignment,
            _task_plan_from_state(task_record),
        )
        wave = read_json(wave_path(run_dir, feature_id, wave_id))
        active_index = wave.get("active_batch_index")
        if (
            not isinstance(active_index, int)
            or task_id not in wave["batches"][active_index]
            or wave["batch_states"][active_index] != "active"
        ):
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "task is outside the active capacity batch"
            )
        if task_record.get("status") != "assigned":
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "only an assigned task can be armed"
            )
        worker = Path(str(assignment["worktree"]))
        if (
            branch(worker) != assignment["branch"]
            or head(worker) != assignment["base_head"]
            or not clean(worker)
            or git_common_dir(worker) != Path(assignment["git_common_dir"])
        ):
            raise ExecutionError(
                "WORKTREE_CONFLICT", "worker arm identity mismatch"
            )
        if task_record.get("dispatched_at") is None:
            task_record["dispatched_at"] = _clock_utc(clock)
            write_json_atomic(task_record_path, task_record)
            append_journal(
                journal_path(run_dir, feature_id, wave_id),
                {"event": "worker_armed", "task_id": task_id},
            )
        return task_record


def requeue_task(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    assignment_digest: str,
    expected_dispatched_at: str,
    *,
    confirmed_stopped: bool,
) -> dict[str, Any]:
    if not confirmed_stopped:
        raise ExecutionError(
            "WORKSPACE_BUSY", "previous prestart worker must be confirmed stopped"
        )
    with _execution_transition_lock(run_dir, feature_id):
        coordinator = _load_coordinator(run_dir, feature_id)
        assignment = read_json(
            assignment_path(run_dir, feature_id, wave_id, task_id)
        )
        _validate_assignment_record(assignment)
        if assignment.get("assignment_digest") != assignment_digest:
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "assignment digest mismatch"
            )
        task_record_path = task_path(run_dir, feature_id, wave_id, task_id)
        task_record = read_json(task_record_path)
        _validate_task_liveness(task_record)
        _validate_assignment_handoff(
            run_dir,
            feature_id,
            coordinator,
            assignment,
            _task_plan_from_state(task_record),
        )
        if task_record.get("status") != "assigned":
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "only an assigned task can be requeued"
            )
        if (
            task_record.get("dispatched_at") != expected_dispatched_at
            or not expected_dispatched_at
        ):
            raise ExecutionError(
                "WORKSPACE_BUSY", "worker dispatch changed before requeue"
            )
        if (
            task_record.get("worker_session_hash") is not None
            or task_record.get("worker_session_hash_history")
            or int(task_record.get("attempt") or 0) != 0
        ):
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "prestart task already has worker ownership"
            )
        worker = Path(str(assignment["worktree"]))
        if branch(worker) != assignment["branch"] or git_common_dir(worker) != Path(
            str(assignment["git_common_dir"])
        ):
            raise ExecutionError(
                "WORKTREE_CONFLICT", "prestart worker identity changed before requeue"
            )
        observation = _worker_scope_observation(assignment)
        if observation["scope_violation"]:
            raise ExecutionError(
                "WORKER_SCOPE_VIOLATION", "prestart worker exceeded its write scope"
            )
        if observation["progress_observed"]:
            raise ExecutionError(
                "WORKER_PRESTART_MUTATION", "prestart worker changed its worktree"
            )
        task_record["dispatched_at"] = None
        write_json_atomic(task_record_path, task_record)
        append_journal(
            journal_path(run_dir, feature_id, wave_id),
            {"event": "worker_requeued", "task_id": task_id},
        )
        return task_record


def start_task(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    assignment_digest: str,
    session_identity: str,
    scope_cwd: Path,
    *,
    clock: Callable[[], datetime] = _system_clock,
) -> dict[str, Any]:
    with _execution_transition_lock(run_dir, feature_id):
        return _start_task_locked(
            run_dir,
            feature_id,
            wave_id,
            task_id,
            assignment_digest,
            session_identity,
            scope_cwd,
            clock=clock,
        )


def _start_task_locked(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    assignment_digest: str,
    session_identity: str,
    scope_cwd: Path,
    *,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    coordinator = _load_coordinator(run_dir, feature_id)
    assignment = read_json(assignment_path(run_dir, feature_id, wave_id, task_id))
    _validate_assignment_record(assignment)
    if assignment.get("assignment_digest") != assignment_digest:
        raise ExecutionError("EXECUTION_STATE_INVALID", "assignment digest mismatch")
    worktree_path = Path(assignment["worktree"])
    expected_scope = Path(str(assignment["scope_cwd"])).resolve()
    requested_scope = scope_cwd.expanduser().resolve()
    if requested_scope != expected_scope:
        raise ExecutionError("WORKTREE_CONFLICT", "worker scope cwd mismatch")
    if branch(worktree_path) != assignment["branch"] or git_common_dir(
        worktree_path
    ) != Path(assignment["git_common_dir"]):
        raise ExecutionError("WORKTREE_CONFLICT", "worker start identity mismatch")
    task_record_path = task_path(run_dir, feature_id, wave_id, task_id)
    task_record = read_json(task_record_path)
    _validate_task_liveness(task_record)
    task = _task_plan_from_state(task_record)
    _validate_assignment_handoff(run_dir, feature_id, coordinator, assignment, task)
    wave = read_json(wave_path(run_dir, feature_id, wave_id))
    active_index = wave.get("active_batch_index")
    if (
        not isinstance(active_index, int)
        or task_id not in wave["batches"][active_index]
        or wave["batch_states"][active_index] != "active"
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "task is outside the active capacity batch"
        )
    if task_record["status"] not in {"assigned", "running"}:
        raise ExecutionError("EXECUTION_STATE_INVALID", "task cannot start")
    session_hash = _session_hash(session_identity)
    existing_hash = task_record.get("worker_session_hash")
    if task_record["status"] == "running":
        if existing_hash != session_hash:
            raise ExecutionError(
                "WORKSPACE_BUSY", "task belongs to another worker session"
            )
        _claim_worker_session(run_dir, feature_id, wave_id, task_id, session_hash)
        return assignment
    dispatched_at = task_record.get("dispatched_at")
    if dispatched_at is None:
        raise ExecutionError(
            "TASK_NOT_ARMED", "coordinator must arm a worker slot before task-start"
        )
    now_text = _clock_utc(clock)
    elapsed = (
        _time_value(now_text, "worker start time")
        - _time_value(dispatched_at, "worker dispatch time")
    ).total_seconds()
    observation = _worker_scope_observation(assignment)
    if observation["scope_violation"]:
        raise ExecutionError(
            "WORKER_SCOPE_VIOLATION", "prestart worker exceeded its write scope"
        )
    if observation["progress_observed"]:
        raise ExecutionError(
            "WORKER_PRESTART_MUTATION", "worker changed its worktree before task-start"
        )
    if elapsed >= int(assignment["start_seconds"]):
        raise ExecutionError(
            "WORKER_PRESTART_TIMEOUT", "worker missed the task-start deadline"
        )
    for other in execution_dir(run_dir, feature_id).glob("tasks/*/*.json"):
        if other == task_record_path:
            continue
        value = read_json(other)
        history = _validated_session_history(value)
        if value.get("worker_session_hash") == session_hash or (
            isinstance(history, list) and session_hash in history
        ):
            raise ExecutionError(
                "FRESH_SESSION_REQUIRED", "worker session already owns another task"
            )
    _claim_worker_session(run_dir, feature_id, wave_id, task_id, session_hash)
    task_record["status"] = "running"
    task_record["worker_started_at"] = now_text
    task_record["last_heartbeat_at"] = now_text
    task_record["heartbeat_sequence"] = 1
    task_record["heartbeat_phase"] = "preflight"
    task_record["worker_session_hash"] = session_hash
    task_record["worker_session_hash_history"].append(session_hash)
    task_record["attempt"] = max(1, int(task_record.get("attempt") or 0) + 1)
    write_json_atomic(task_record_path, task_record)
    append_journal(
        journal_path(run_dir, feature_id, wave_id),
        {"event": "worker_started", "task_id": task_id, "attempt": task_record["attempt"]},
    )
    return assignment


def _working_paths(worktree_path: Path) -> list[str]:
    values: set[str] = set()
    for args, action in (
        (["diff", "--name-only", "-z"], "read worker changes"),
        (["diff", "--cached", "--name-only", "-z"], "read staged worker changes"),
        (
            ["ls-files", "--others", "--exclude-standard", "-z"],
            "read untracked worker changes",
        ),
    ):
        values.update(_split_nul(git(worktree_path, args, action)))
    return sorted(values)


def _validate_task_liveness(task_record: dict[str, Any]) -> None:
    _validated_session_history(task_record)
    if "finish_intent" not in task_record:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "task finish intent state is missing"
        )
    finish_intent = task_record.get("finish_intent")
    if finish_intent is not None:
        _validate_finish_intent(finish_intent)
    dispatched_at = task_record.get("dispatched_at")
    started_at = task_record.get("worker_started_at")
    heartbeat_at = task_record.get("last_heartbeat_at")
    sequence = task_record.get("heartbeat_sequence")
    phase = task_record.get("heartbeat_phase")
    for value, label in (
        (dispatched_at, "worker dispatch time"),
        (started_at, "worker start time"),
        (heartbeat_at, "worker heartbeat time"),
    ):
        if value is not None:
            _time_value(value, label)
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker heartbeat sequence is invalid"
        )
    if phase is not None and phase not in WORKER_PHASES:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker heartbeat phase is invalid"
        )
    if task_record.get("status") in {"planned", "assigned"}:
        if any(value is not None for value in (started_at, heartbeat_at, phase)) or sequence:
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "inactive worker liveness state is invalid"
            )
    elif task_record.get("status") in {"running", "committed"}:
        if (
            dispatched_at is None
            or started_at is None
            or heartbeat_at is None
            or sequence < 1
            or phase is None
        ):
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "active worker liveness state is invalid"
            )
    if task_record.get("status") == "committed" and finish_intent is None:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "committed task finish intent is missing"
        )


def _validate_finish_intent(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "task finish intent is invalid"
        )
    required = {
        "schema",
        "assignment_digest",
        "base_head",
        "tree",
        "message",
        "evidence_digest",
        "intent_digest",
    }
    unsigned = dict(value)
    recorded = unsigned.pop("intent_digest", None)
    if (
        set(value) != required
        or value.get("schema") != TASK_FINISH_INTENT_SCHEMA
        or SHA_RE.fullmatch(str(value.get("base_head") or "")) is None
        or SHA_RE.fullmatch(str(value.get("tree") or "")) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("assignment_digest") or ""))
        is None
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("evidence_digest") or ""))
        is None
        or not isinstance(value.get("message"), str)
        or not value["message"]
        or not isinstance(recorded, str)
        or recorded != sha256_json(unsigned)
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "task finish intent is invalid"
        )
    return value


def _worker_scope_observation(assignment: dict[str, Any]) -> dict[str, object]:
    worker = Path(str(assignment["worktree"]))
    paths = _working_paths(worker)
    claims = tuple(WriteClaim(**item) for item in assignment["write_claims"])
    project_scope = str(assignment["project_scope"])
    scope_violation = head(worker) != assignment["base_head"] or any(
        not _path_in_project_scope(path, project_scope)
        or not _path_allowed(path, claims)
        for path in paths
    )
    if not scope_violation:
        for path in paths:
            mode = git(
                worker,
                ["ls-files", "--stage", "--", path],
                "inspect worker path mode",
            ).partition(" ")[0]
            if mode in {"120000", "160000"}:
                scope_violation = True
                break
    return {
        "paths": paths,
        "progress_observed": bool(paths) and not scope_violation,
        "scope_violation": scope_violation,
    }


def _worker_guard_status(
    assignment: dict[str, Any],
    task_record: dict[str, Any],
    *,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    now_text = _clock_utc(clock)
    now = _time_value(now_text, "worker guard clock")
    started = _time_value(task_record.get("worker_started_at"), "worker start time")
    heartbeat = _time_value(
        task_record.get("last_heartbeat_at"), "worker heartbeat time"
    )
    elapsed = max(0, int((now - started).total_seconds()))
    heartbeat_age = max(0, int((now - heartbeat).total_seconds()))
    observation = _worker_scope_observation(assignment)
    progress_observed = bool(observation["progress_observed"])
    scope_violation = bool(observation["scope_violation"])
    if scope_violation:
        status = "WORKER_SCOPE_VIOLATION"
    elif elapsed >= int(assignment["max_seconds"]):
        status = "WORKER_TIMEOUT"
    elif not progress_observed and elapsed >= int(assignment["read_only_seconds"]):
        status = "WORKER_READ_ONLY_TIMEOUT"
    elif heartbeat_age >= int(assignment["stall_seconds"]):
        status = "WORKER_STALLED"
    else:
        status = "ACTIVE"
    warning = (
        "READ_ONLY_DEADLINE_NEAR"
        if status == "ACTIVE"
        and not progress_observed
        and elapsed >= int(assignment["read_only_warning_seconds"])
        else None
    )
    return {
        "status": status,
        "warning": warning,
        "elapsed_seconds": elapsed,
        "heartbeat_age_seconds": heartbeat_age,
        "progress_observed": progress_observed,
        "scope_violation": scope_violation,
        "heartbeat_sequence": task_record["heartbeat_sequence"],
        "heartbeat_phase": task_record["heartbeat_phase"],
        "observed_at": now_text,
    }


def heartbeat_task(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    assignment_digest: str,
    phase: str,
    session_identity: str,
    scope_cwd: Path,
    *,
    clock: Callable[[], datetime] = _system_clock,
) -> dict[str, object]:
    if phase not in WORKER_PHASES:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker heartbeat phase is invalid"
        )
    with _execution_transition_lock(run_dir, feature_id):
        assignment = read_json(
            assignment_path(run_dir, feature_id, wave_id, task_id)
        )
        _validate_assignment_record(assignment)
        if assignment.get("assignment_digest") != assignment_digest:
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "assignment digest mismatch"
            )
        expected_scope = Path(str(assignment["scope_cwd"])).resolve()
        if scope_cwd.expanduser().resolve() != expected_scope:
            raise ExecutionError(
                "WORKTREE_CONFLICT", "worker heartbeat scope cwd mismatch"
            )
        task_record_path = task_path(run_dir, feature_id, wave_id, task_id)
        task_record = read_json(task_record_path)
        _validate_task_liveness(task_record)
        if (
            task_record.get("status") != "running"
            or task_record.get("worker_session_hash")
            != _session_hash(session_identity)
        ):
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "worker heartbeat ownership is invalid"
            )
        status = _worker_guard_status(assignment, task_record, clock=clock)
        if status["status"] != "ACTIVE":
            raise ExecutionError(
                str(status["status"]), "worker liveness budget was exceeded"
            )
        task_record["last_heartbeat_at"] = status["observed_at"]
        task_record["heartbeat_sequence"] = int(
            task_record["heartbeat_sequence"]
        ) + 1
        task_record["heartbeat_phase"] = phase
        write_json_atomic(task_record_path, task_record)
        status["heartbeat_sequence"] = task_record["heartbeat_sequence"]
        status["heartbeat_phase"] = phase
        return status


def watch_task(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    assignment_digest: str,
    *,
    clock: Callable[[], datetime] = _system_clock,
) -> dict[str, object]:
    with _execution_transition_lock(run_dir, feature_id):
        assignment = read_json(
            assignment_path(run_dir, feature_id, wave_id, task_id)
        )
        _validate_assignment_record(assignment)
        if assignment.get("assignment_digest") != assignment_digest:
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "assignment digest mismatch"
            )
        task_record = read_json(task_path(run_dir, feature_id, wave_id, task_id))
        _validate_task_liveness(task_record)
        if task_record.get("status") == "assigned":
            now_text = _clock_utc(clock)
            observation = _worker_scope_observation(assignment)
            dispatched_at = task_record.get("dispatched_at")
            if observation["scope_violation"]:
                status = "WORKER_SCOPE_VIOLATION"
                elapsed = 0
            elif observation["progress_observed"]:
                status = "WORKER_PRESTART_MUTATION"
                elapsed = 0
            elif dispatched_at is None:
                status = "QUEUED"
                elapsed = 0
            else:
                elapsed = max(
                    0,
                    int(
                        (
                            _time_value(now_text, "worker guard clock")
                            - _time_value(dispatched_at, "worker dispatch time")
                        ).total_seconds()
                    ),
                )
                status = (
                    "WORKER_PRESTART_TIMEOUT"
                    if elapsed >= int(assignment["start_seconds"])
                    else "PENDING_START"
                )
            return {
                "status": status,
                "warning": None,
                "elapsed_seconds": elapsed,
                "heartbeat_age_seconds": None,
                "progress_observed": observation["progress_observed"],
                "scope_violation": observation["scope_violation"],
                "heartbeat_sequence": 0,
                "heartbeat_phase": None,
                "observed_at": now_text,
            }
        if task_record.get("status") != "running":
            raise ExecutionError(
                "EXECUTION_STATE_INVALID",
                "only an assigned or running task can be watched",
            )
        return _worker_guard_status(assignment, task_record, clock=clock)


def _verify_integration_paths(
    integration: Path,
    paths: Iterable[str],
    project_scope: str,
) -> None:
    values = list(paths)
    outside = [
        path for path in values if not _path_in_project_scope(path, project_scope)
    ]
    if outside:
        raise ExecutionError(
            "REPLAN_REQUIRED",
            "integration changed paths outside initialized project scope: "
            + ", ".join(outside),
        )
    if any(pattern.search(path) for path in values for pattern in SENSITIVE_FILENAMES):
        raise ExecutionError(
            "SECURITY_BLOCKER", "integration staged a sensitive credential path"
        )
    for path in values:
        index_entry = git(
            integration,
            ["ls-files", "--stage", "--", path],
            "inspect integration path mode",
        )
        mode = index_entry.partition(" ")[0]
        if mode in {"120000", "160000"}:
            raise ExecutionError(
                "REPLAN_REQUIRED",
                f"integration path must not be a symlink or submodule: {path}",
            )


def _verify_staged_integration(integration: Path, project_scope: str) -> None:
    staged = _split_nul(
        git(
            integration,
            ["diff", "--cached", "--name-only", "-z"],
            "read staged integration paths",
        )
    )
    _verify_integration_paths(integration, staged, project_scope)
    staged_diff = git(
        integration,
        ["diff", "--cached", "--no-ext-diff", "--unified=0"],
        "inspect staged integration content",
    )
    if _contains_sensitive(staged_diff):
        raise ExecutionError("SECURITY_BLOCKER", "integration staged sensitive content")


def _verify_integration_commit(
    integration: Path,
    base_head: str,
    current_head: str,
    project_scope: str,
) -> None:
    paths = _split_nul(
        git(
            integration,
            ["diff", "--name-only", "-z", f"{base_head}..{current_head}"],
            "read integration commit paths",
        )
    )
    _verify_integration_paths(integration, paths, project_scope)
    committed_diff = git(
        integration,
        ["diff", "--no-ext-diff", "--unified=0", f"{base_head}..{current_head}"],
        "inspect integration commit content",
    )
    if _contains_sensitive(committed_diff):
        raise ExecutionError(
            "SECURITY_BLOCKER", "integration commit contains sensitive content"
        )


def _finish_evidence_digest(
    assignment: dict[str, Any],
    *,
    validation: str,
    review: str,
    message: str,
    summary: str,
    decisions: list[str],
    open_risks: list[str],
    regression_oracle_evidence: dict[str, Any] | None,
) -> str:
    return sha256_json(
        {
            "assignment_digest": assignment["assignment_digest"],
            "validation": validation,
            "review": review,
            "message": message,
            "summary": summary,
            "decisions": decisions,
            "open_risks": open_risks,
            "regression_oracle_evidence": regression_oracle_evidence,
        }
    )


def _finish_intent_record(
    assignment: dict[str, Any],
    *,
    tree: str,
    message: str,
    evidence_digest: str,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": TASK_FINISH_INTENT_SCHEMA,
        "assignment_digest": assignment["assignment_digest"],
        "base_head": assignment["base_head"],
        "tree": tree,
        "message": message,
        "evidence_digest": evidence_digest,
    }
    value["intent_digest"] = sha256_json(value)
    return value


def _adopt_finish_commit(
    worktree_path: Path,
    assignment: dict[str, Any],
    task_record: dict[str, Any],
    *,
    message: str,
    evidence_digest: str,
) -> str:
    intent_value = task_record.get("finish_intent")
    if intent_value is None:
        raise ExecutionError(
            "WORKER_SCOPE_VIOLATION",
            "worker HEAD moved without a coordinator task-finish intent",
        )
    intent = _validate_finish_intent(intent_value)
    current = head(worktree_path)
    base = str(assignment["base_head"])
    parents = git(
        worktree_path,
        ["rev-list", "--parents", "-n", "1", current],
        "inspect recoverable task commit parent",
    ).split()
    tree = git(
        worktree_path,
        ["show", "-s", "--format=%T", current],
        "inspect recoverable task commit tree",
    )
    commit_message = git(
        worktree_path,
        ["show", "-s", "--format=%B", current],
        "inspect recoverable task commit message",
    )
    expected = _finish_intent_record(
        assignment,
        tree=tree,
        message=message,
        evidence_digest=evidence_digest,
    )
    if (
        intent != expected
        or len(parents) != 2
        or parents[1] != base
        or commit_message != message
        or branch(worktree_path) != assignment["branch"]
        or git_common_dir(worktree_path) != Path(assignment["git_common_dir"])
        or not clean(worktree_path)
    ):
        raise ExecutionError(
            "WORKER_SCOPE_VIOLATION",
            "moved worker HEAD does not match the coordinator task-finish intent",
        )
    return current


def recover_task(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    session_identity: str,
    scope_cwd: Path,
    *,
    expected_attempt: int,
    confirmed_stopped: bool,
    clock: Callable[[], datetime] = _system_clock,
) -> dict[str, Any]:
    with _execution_transition_lock(run_dir, feature_id):
        return _recover_task_locked(
            run_dir,
            feature_id,
            wave_id,
            task_id,
            session_identity,
            scope_cwd,
            expected_attempt=expected_attempt,
            confirmed_stopped=confirmed_stopped,
            clock=clock,
        )


def _recover_task_locked(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    session_identity: str,
    scope_cwd: Path,
    *,
    expected_attempt: int,
    confirmed_stopped: bool,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    if not confirmed_stopped:
        raise ExecutionError(
            "WORKSPACE_BUSY", "previous worker must be explicitly confirmed stopped"
        )
    coordinator = _load_coordinator(run_dir, feature_id)
    assignment = read_json(assignment_path(run_dir, feature_id, wave_id, task_id))
    _validate_assignment_record(assignment)
    task_record_path = task_path(run_dir, feature_id, wave_id, task_id)
    task_record = read_json(task_record_path)
    _validate_task_liveness(task_record)
    history = _validated_session_history(task_record)
    task = _task_plan_from_state(task_record)
    _validate_assignment_handoff(run_dir, feature_id, coordinator, assignment, task)
    if task_record.get("status") != "running":
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "only a running task can recover"
        )
    if expected_attempt < 1 or int(task_record.get("attempt") or 0) != expected_attempt:
        raise ExecutionError(
            "WORKSPACE_BUSY", "worker recovery attempt changed before transfer"
        )
    new_hash = _session_hash(session_identity)
    if new_hash in history:
        raise ExecutionError(
            "FRESH_SESSION_REQUIRED", "recovery requires a fresh session"
        )
    for other in execution_dir(run_dir, feature_id).glob("tasks/*/*.json"):
        if other == task_record_path:
            continue
        value = read_json(other)
        other_history = _validated_session_history(value)
        if value.get("worker_session_hash") == new_hash or new_hash in other_history:
            raise ExecutionError(
                "FRESH_SESSION_REQUIRED", "worker session already owns another task"
            )
    worker = Path(str(assignment["worktree"]))
    expected_scope = Path(str(assignment["scope_cwd"])).resolve()
    if scope_cwd.expanduser().resolve() != expected_scope:
        raise ExecutionError("WORKTREE_CONFLICT", "worker scope cwd mismatch")
    if branch(worker) != assignment["branch"] or git_common_dir(worker) != Path(
        assignment["git_common_dir"]
    ):
        raise ExecutionError("WORKTREE_CONFLICT", "worker recovery identity drifted")
    base = str(assignment["base_head"])
    current = head(worker)
    claims = tuple(WriteClaim(**item) for item in assignment["write_claims"])
    if current != base:
        raise ExecutionError(
            "WORKER_SCOPE_VIOLATION",
            "worker recovery cannot adopt a worker-created commit",
        )
    _verify_worker_paths(
        worker,
        _working_paths(worker),
        claims,
        str(assignment["project_scope"]),
    )
    _claim_worker_session(run_dir, feature_id, wave_id, task_id, new_hash)
    recovered_at = _clock_utc(clock)
    task_record["worker_session_hash"] = new_hash
    task_record["worker_session_hash_history"].append(new_hash)
    task_record["attempt"] = int(task_record.get("attempt") or 1) + 1
    task_record["dispatched_at"] = recovered_at
    task_record["worker_started_at"] = recovered_at
    task_record["last_heartbeat_at"] = recovered_at
    task_record["heartbeat_sequence"] = 1
    task_record["heartbeat_phase"] = "preflight"
    task_record["recovered_at"] = recovered_at
    write_json_atomic(task_record_path, task_record)
    append_journal(
        journal_path(run_dir, feature_id, wave_id),
        {"event": "worker_recovered", "task_id": task_id, "attempt": task_record["attempt"]},
    )
    return assignment


def _path_allowed(path: str, claims: Iterable[WriteClaim]) -> bool:
    candidate = PurePosixPath(path)
    for claim in claims:
        owner = PurePosixPath(claim.path)
        if claim.kind == "exact" and candidate == owner:
            return True
        if claim.kind == "prefix":
            try:
                candidate.relative_to(owner)
                return True
            except ValueError:
                pass
    return False


def _verify_worker_paths(
    worktree_path: Path,
    paths: Iterable[str],
    claims: tuple[WriteClaim, ...],
    project_scope: str = ".",
) -> None:
    outside = [
        path
        for path in paths
        if not _path_in_project_scope(path, project_scope)
        or not _path_allowed(path, claims)
    ]
    if outside:
        raise ExecutionError(
            "REPLAN_REQUIRED",
            "task changed undeclared paths: " + ", ".join(outside),
        )
    for path in paths:
        index_entry = git(
            worktree_path,
            ["ls-files", "--stage", "--", path],
            "inspect worker path mode",
        )
        mode = index_entry.partition(" ")[0]
        if mode in {"120000", "160000"}:
            raise ExecutionError(
                "REPLAN_REQUIRED",
                f"task path must not be a symlink or submodule: {path}",
            )


def finish_task(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    validation: str,
    review: str,
    message: str,
    *,
    summary: str,
    decisions: Iterable[str] = (),
    open_risks: Iterable[str] = (),
    regression_oracle_evidence: dict[str, Any] | None = None,
    clock: Callable[[], datetime] = _system_clock,
) -> dict[str, Any]:
    with _execution_transition_lock(run_dir, feature_id):
        return _finish_task_locked(
            run_dir,
            feature_id,
            wave_id,
            task_id,
            validation,
            review,
            message,
            summary=summary,
            decisions=decisions,
            open_risks=open_risks,
            regression_oracle_evidence=regression_oracle_evidence,
            clock=clock,
        )


def _finish_task_locked(
    run_dir: Path,
    feature_id: str,
    wave_id: str,
    task_id: str,
    validation: str,
    review: str,
    message: str,
    *,
    summary: str,
    decisions: Iterable[str],
    open_risks: Iterable[str],
    regression_oracle_evidence: dict[str, Any] | None,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    if (
        not validation.strip()
        or not review.strip()
        or not message.strip()
        or message != message.strip()
    ):
        raise ExecutionError(
            "INTEGRATION_VALIDATION_FAILED",
            "worker validation, review, and canonical commit message are required",
        )
    summary_value = summary
    decision_values = list(decisions)
    risk_values = list(open_risks)
    if (
        not summary_value.strip()
        or any(
            not isinstance(item, str) or not item.strip() for item in decision_values
        )
        or any(not isinstance(item, str) or not item.strip() for item in risk_values)
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "worker handoff evidence is invalid"
        )
    _reject_sensitive_evidence(
        validation, review, message, summary_value, *decision_values, *risk_values
    )
    coordinator = _load_coordinator(run_dir, feature_id)
    assignment = read_json(assignment_path(run_dir, feature_id, wave_id, task_id))
    _validate_assignment_record(assignment)
    worktree_path = Path(assignment["worktree"])
    task_record_path = task_path(run_dir, feature_id, wave_id, task_id)
    task_record = read_json(task_record_path)
    _validate_task_liveness(task_record)
    _validate_assignment_handoff(
        run_dir,
        feature_id,
        coordinator,
        assignment,
        _task_plan_from_state(task_record),
    )
    if assignment.get("regression_oracle") and regression_oracle_evidence is None:
        raise ExecutionError(
            "INTEGRATION_VALIDATION_FAILED",
            "corrective task requires structured regression-oracle evidence",
        )
    if (
        not assignment.get("regression_oracle")
        and regression_oracle_evidence is not None
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID",
            "non-corrective task supplied regression-oracle evidence",
        )
    _validate_regression_oracle_claim(assignment, regression_oracle_evidence)
    evidence_digest = _finish_evidence_digest(
        assignment,
        validation=validation,
        review=review,
        message=message,
        summary=summary_value,
        decisions=decision_values,
        open_risks=risk_values,
        regression_oracle_evidence=regression_oracle_evidence,
    )
    if task_record["status"] == "committed":
        result = read_json(result_path(run_dir, feature_id, wave_id, task_id))
        _validate_result_record(result, assignment)
        adopted = _adopt_finish_commit(
            worktree_path,
            assignment,
            task_record,
            message=message,
            evidence_digest=evidence_digest,
        )
        expected_oracle = _build_regression_oracle_evidence(
            assignment, regression_oracle_evidence, adopted
        )
        if (
            adopted != result["commit"]
            or result.get("summary") != summary_value
            or result.get("decisions") != decision_values
            or result.get("open_risks") != risk_values
            or result.get("validation") != validation
            or result.get("review") != review
            or result.get("regression_oracle_evidence") != expected_oracle
        ):
            raise ExecutionError("WORKTREE_CONFLICT", "accepted worker result drifted")
        return result
    if task_record["status"] != "running":
        raise ExecutionError("EXECUTION_STATE_INVALID", "task is not running")
    if (
        branch(worktree_path) != assignment["branch"]
        or git_common_dir(worktree_path) != Path(assignment["git_common_dir"])
    ):
        raise ExecutionError("WORKTREE_CONFLICT", "worker branch changed")
    base_head = assignment["base_head"]
    task_head = head(worktree_path)
    if task_head != base_head:
        task_head = _adopt_finish_commit(
            worktree_path,
            assignment,
            task_record,
            message=message,
            evidence_digest=evidence_digest,
        )
    else:
        guard = _worker_guard_status(assignment, task_record, clock=clock)
        if guard["status"] != "ACTIVE":
            raise ExecutionError(
                str(guard["status"]), "worker liveness budget was exceeded"
            )
        if not clean(worktree_path):
            git(worktree_path, ["add", "-A"], "stage worker task")
            staged = _split_nul(
                git(
                    worktree_path,
                    ["diff", "--cached", "--name-only", "-z"],
                    "read staged paths",
                )
            )
            claims = tuple(WriteClaim(**item) for item in assignment["write_claims"])
            try:
                _verify_worker_paths(
                    worktree_path, staged, claims, str(assignment["project_scope"])
                )
                if any(
                    pattern.search(path)
                    for path in staged
                    for pattern in SENSITIVE_FILENAMES
                ):
                    raise ExecutionError(
                        "SECURITY_BLOCKER",
                        "worker staged a sensitive credential path",
                    )
                staged_diff = git(
                    worktree_path,
                    ["diff", "--cached", "--no-ext-diff", "--unified=0"],
                    "inspect staged worker content",
                )
                if _contains_sensitive(staged_diff):
                    raise ExecutionError(
                        "SECURITY_BLOCKER", "worker staged sensitive content"
                    )
            except ExecutionError:
                git(
                    worktree_path,
                    ["restore", "--staged", ":/"],
                    "unstage out-of-claim task changes",
                )
                raise
            tree = git(worktree_path, ["write-tree"], "record worker task tree")
            intent = _finish_intent_record(
                assignment,
                tree=tree,
                message=message,
                evidence_digest=evidence_digest,
            )
            existing_intent = task_record.get("finish_intent")
            if existing_intent is None:
                task_record["finish_intent"] = intent
                write_json_atomic(task_record_path, task_record)
                append_journal(
                    journal_path(run_dir, feature_id, wave_id),
                    {
                        "event": "task_finish_intent",
                        "task_id": task_id,
                        "tree": tree,
                    },
                )
            elif _validate_finish_intent(existing_intent) != intent:
                raise ExecutionError(
                    "WORKER_SCOPE_VIOLATION",
                    "worker tree changed after coordinator task-finish intent",
                )
            git(worktree_path, ["commit", "-m", message], "commit worker task")
        task_head = head(worktree_path)
    count = git(
        worktree_path,
        ["rev-list", "--count", f"{base_head}..{task_head}"],
        "count worker commits",
    )
    parents = git(
        worktree_path,
        ["rev-list", "--parents", "-n", "1", task_head],
        "read worker commit parent",
    ).split()
    commit_tree = git(
        worktree_path,
        ["show", "-s", "--format=%T", task_head],
        "read worker commit tree",
    )
    commit_message = git(
        worktree_path,
        ["show", "-s", "--format=%B", task_head],
        "read worker commit message",
    )
    finish_intent = _validate_finish_intent(task_record.get("finish_intent"))
    if (
        count != "1"
        or len(parents) != 2
        or parents[1] != base_head
        or commit_tree != finish_intent["tree"]
        or commit_message != finish_intent["message"]
        or not clean(worktree_path)
    ):
        raise ExecutionError(
            "WORKTREE_CONFLICT", "worker must contain one clean direct-child commit"
        )
    changed_paths = _split_nul(
        git(
            worktree_path,
            ["diff", "--name-only", "-z", f"{base_head}..{task_head}"],
            "read worker changed paths",
        )
    )
    claims = tuple(WriteClaim(**item) for item in assignment["write_claims"])
    _verify_worker_paths(
        worktree_path, changed_paths, claims, str(assignment["project_scope"])
    )
    if any(
        pattern.search(path)
        for path in changed_paths
        for pattern in SENSITIVE_FILENAMES
    ):
        raise ExecutionError(
            "SECURITY_BLOCKER", "worker commit contains a sensitive path"
        )
    committed_diff = git(
        worktree_path,
        ["show", "--format=", "--no-ext-diff", "--unified=0", task_head],
        "inspect worker commit content",
    )
    if _contains_sensitive(committed_diff):
        raise ExecutionError(
            "SECURITY_BLOCKER", "worker commit contains sensitive content"
        )
    oracle_evidence = _build_regression_oracle_evidence(
        assignment, regression_oracle_evidence, task_head
    )
    expected_result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "feature_id": feature_id,
        "wave_id": wave_id,
        "task_id": task_id,
        "assignment_digest": assignment["assignment_digest"],
        "commit": task_head,
        "changed_paths": changed_paths,
        "summary": summary_value,
        "decisions": decision_values,
        "open_risks": risk_values,
        "validation": validation,
        "review": review,
        "regression_oracle_evidence": oracle_evidence,
        "attempt": int(task_record.get("attempt") or 1),
    }
    result_file = result_path(run_dir, feature_id, wave_id, task_id)
    if result_file.exists():
        result = read_json(result_file)
        _validate_result_record(result, assignment)
        if any(result.get(key) != value for key, value in expected_result.items()):
            raise ExecutionError(
                "EXECUTION_STATE_INVALID",
                "persisted task result does not match task-finish intent",
            )
    else:
        result = {**expected_result, "completed_at": utc_now()}
        result["result_digest"] = sha256_json(result)
        write_json_atomic(result_file, result)
    task_record["status"] = "committed"
    task_record["commit"] = task_head
    task_record["result_digest"] = result["result_digest"]
    write_json_atomic(task_record_path, task_record)
    return result


def _split_nul(value: str) -> list[str]:
    return [item for item in value.split("\x00") if item]


def integrate_wave(run_dir: Path, feature_id: str, wave_id: str) -> dict[str, Any]:
    coordinator = _load_coordinator(run_dir, feature_id)
    wave_file = wave_path(run_dir, feature_id, wave_id)
    wave = read_json(wave_file)
    if wave["status"] == "integrated":
        if head(Path(coordinator["integration_worktree"])) != wave["integration_head"]:
            raise ExecutionError("WORKTREE_CONFLICT", "integrated wave tip drifted")
        coordinator["integration_head"] = wave["integration_head"]
        _save_coordinator(run_dir, feature_id, coordinator)
        return wave
    if wave["status"] not in {"running", "integrating"}:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "wave is not ready to integrate"
        )
    active_batch_index = wave.get("active_batch_index")
    if isinstance(active_batch_index, int):
        active_tasks = wave["batches"][active_batch_index]
        if any(
            read_json(task_path(run_dir, feature_id, wave_id, task_id)).get("status")
            != "committed"
            for task_id in active_tasks
        ):
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "active capacity batch is incomplete"
            )
        if active_batch_index + 1 < len(wave["batches"]):
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", "later capacity batches are pending"
            )
        wave["batch_states"][active_batch_index] = "done"
        wave["active_batch_index"] = None
        write_json_atomic(wave_file, wave)
    if any(state != "done" for state in wave.get("batch_states", [])):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", "capacity batches are incomplete"
        )
    integration = Path(coordinator["integration_worktree"])
    if not clean(integration):
        raise ExecutionError("INTEGRATION_CONFLICT", "integration worktree is dirty")
    wave["status"] = "integrating"
    write_json_atomic(wave_file, wave)
    for task_id in wave["task_ids"]:
        task_record = read_json(task_path(run_dir, feature_id, wave_id, task_id))
        if task_record["status"] != "committed":
            raise ExecutionError(
                "EXECUTION_STATE_INVALID", f"{task_id} is not committed"
            )
        result = read_json(result_path(run_dir, feature_id, wave_id, task_id))
        assignment = read_json(assignment_path(run_dir, feature_id, wave_id, task_id))
        _validate_assignment_handoff(
            run_dir,
            feature_id,
            coordinator,
            assignment,
            _task_plan_from_state(task_record),
        )
        _validate_result_record(result, assignment)
        worker = Path(assignment["worktree"])
        if (
            result["assignment_digest"] != assignment["assignment_digest"]
            or head(worker) != result["commit"]
            or not clean(worker)
        ):
            raise ExecutionError(
                "WORKTREE_CONFLICT", f"{task_id} result identity drifted"
            )
        if task_id in wave["merged_task_ids"]:
            ancestor = _run(
                ["git", "merge-base", "--is-ancestor", result["commit"], "HEAD"],
                integration,
                "verify prior ordered merge",
                check=False,
            )
            if ancestor.returncode != 0:
                raise ExecutionError(
                    "INTEGRATION_CONFLICT", "recorded merge is not reachable"
                )
            continue
        current_tip = head(integration)
        tip_parents = git(
            integration,
            ["rev-list", "--parents", "-n", "1", current_tip],
            "inspect interrupted ordered merge",
        ).split()
        if len(tip_parents) >= 3 and tip_parents[2] == result["commit"]:
            wave["merged_task_ids"].append(task_id)
            write_json_atomic(wave_file, wave)
            continue
        append_journal(
            journal_path(run_dir, feature_id, wave_id),
            {"event": "merge_intent", "task_id": task_id, "commit": result["commit"]},
        )
        try:
            git(
                integration,
                ["merge", "--no-ff", "--no-edit", assignment["branch"]],
                f"merge {task_id}",
            )
        except ExecutionError as exc:
            git(
                integration,
                ["merge", "--abort"],
                "abort failed integration",
                check=False,
            )
            wave["status"] = "blocked"
            wave["blocker"] = "INTEGRATION_CONFLICT"
            write_json_atomic(wave_file, wave)
            raise ExecutionError("INTEGRATION_CONFLICT", str(exc)) from exc
        wave["merged_task_ids"].append(task_id)
        write_json_atomic(wave_file, wave)
    wave["status"] = "integrated"
    wave["integration_head"] = head(integration)
    write_json_atomic(wave_file, wave)
    coordinator["integration_head"] = wave["integration_head"]
    _save_coordinator(run_dir, feature_id, coordinator)
    return wave


def complete_wave(
    run_dir: Path, feature_id: str, wave_id: str, evidence: str
) -> dict[str, Any]:
    if not evidence.strip():
        raise ExecutionError(
            "INTEGRATION_VALIDATION_FAILED", "combined evidence is empty"
        )
    coordinator = _load_coordinator(run_dir, feature_id)
    wave_file = wave_path(run_dir, feature_id, wave_id)
    wave = read_json(wave_file)
    if wave["status"] == "done":
        coordinator["active_wave"] = None
        coordinator["integration_head"] = wave["integration_head"]
        if all(
            read_json(wave_path(run_dir, feature_id, item))["status"] == "done"
            for item in coordinator["wave_ids"]
        ):
            coordinator["status"] = "integrated"
        else:
            coordinator["status"] = "waves_running"
        _save_coordinator(run_dir, feature_id, coordinator)
        return wave
    cleanup_retry = (
        wave["status"] == "blocked" and wave.get("blocker") == "CLEANUP_BLOCKED"
    )
    if wave["status"] != "integrated" and not cleanup_retry:
        raise ExecutionError("EXECUTION_STATE_INVALID", "wave is not integrated")
    integration = Path(coordinator["integration_worktree"])
    if head(integration) != wave["integration_head"] or not clean(integration):
        raise ExecutionError(
            "INTEGRATION_VALIDATION_FAILED", "integration evidence tip drifted"
        )
    retained: list[dict[str, str]] = []
    for task_id in reversed(wave["task_ids"]):
        assignment = read_json(assignment_path(run_dir, feature_id, wave_id, task_id))
        worker = Path(assignment["worktree"])
        task_commit = read_json(result_path(run_dir, feature_id, wave_id, task_id))[
            "commit"
        ]
        if not _cleanup_internal_resource(
            run_dir=run_dir,
            coordinator=coordinator,
            repo=integration,
            kind="worker",
            worktree=worker,
            branch_name=str(assignment["branch"]),
            expected_tip=str(task_commit),
            reachable_tip=str(wave["integration_head"]),
        ):
            retained.append(
                {
                    "task_id": task_id,
                    "branch": assignment["branch"],
                    "path": str(worker),
                }
            )
    wave["combined_evidence"] = evidence.strip()
    wave["cleanup_retained"] = retained
    if retained:
        wave["status"] = "blocked"
        wave["blocker"] = "CLEANUP_BLOCKED"
        write_json_atomic(wave_file, wave)
        raise ExecutionError("CLEANUP_BLOCKED", "worker resources remain")
    wave["status"] = "done"
    write_json_atomic(wave_file, wave)
    coordinator["active_wave"] = None
    if all(
        read_json(wave_path(run_dir, feature_id, item))["status"] == "done"
        for item in coordinator["wave_ids"]
    ):
        coordinator["status"] = "integrated"
    else:
        coordinator["status"] = "waves_running"
    coordinator["integration_head"] = wave["integration_head"]
    _save_coordinator(run_dir, feature_id, coordinator)
    return wave


def _is_ancestor(cwd: Path, ancestor: str, descendant: str) -> bool:
    result = _run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd,
        "verify ancestry",
        check=False,
    )
    return result.returncode == 0


def _cleanup_internal_resource(
    *,
    run_dir: Path,
    coordinator: dict[str, Any],
    repo: Path,
    kind: str,
    worktree: Path,
    branch_name: str,
    expected_tip: str,
    reachable_tip: str,
) -> bool:
    registration = worktrees(repo).get(worktree.resolve())
    path_present = os.path.lexists(worktree)
    ref = f"refs/heads/{branch_name}"
    branch_result = _run(
        ["git", "rev-parse", "--verify", ref],
        repo,
        "read cleanup branch",
        check=False,
    )
    if branch_result.returncode != 0:
        if registration is not None or path_present:
            return False
        _interop(
            f"{kind} resource release",
            record_outer_resource,
            run_dir,
            Path(str(coordinator["selected_project_root"])),
            kind=kind,
            path=worktree,
            branch=branch_name,
            state="absent",
        )
        return True
    branch_tip = branch_result.stdout.strip()
    if branch_tip != expected_tip or not _is_ancestor(
        repo, expected_tip, reachable_tip
    ):
        return False
    if path_present and registration is None:
        return False
    if registration is not None:
        if (
            not worktree.is_dir()
            or worktree.is_symlink()
            or registration.get("branch") != ref
            or git_common_dir(worktree) != git_common_dir(repo)
            or head(worktree) != expected_tip
            or not clean(worktree)
        ):
            return False
        git(
            repo,
            ["worktree", "unlock", str(worktree)],
            f"unlock {kind}",
            check=False,
        )
        removal = _run(
            ["git", "worktree", "remove", str(worktree)],
            repo,
            f"remove {kind} worktree",
            check=False,
        )
        if removal.returncode != 0:
            return False
    deletion = _run(
        ["git", "update-ref", "-d", ref, expected_tip],
        repo,
        f"delete exact {kind} branch tip",
        check=False,
    )
    if deletion.returncode != 0:
        return False
    if (
        os.path.lexists(worktree)
        or worktree.resolve() in worktrees(repo)
        or local_branch_exists(repo, branch_name)
    ):
        return False
    _interop(
        f"{kind} resource release",
        record_outer_resource,
        run_dir,
        Path(str(coordinator["selected_project_root"])),
        kind=kind,
        path=worktree,
        branch=branch_name,
        state="absent",
    )
    return True


def seal_feature(
    run_dir: Path, feature_id: str, evidence: str, message: str
) -> dict[str, Any]:
    """Seal final integration-only changes before project-branch promotion."""

    if not evidence.strip():
        raise ExecutionError("PROMOTION_BLOCKED", "final evidence is empty")
    _reject_sensitive_evidence(evidence, message)
    coordinator = _load_coordinator(run_dir, feature_id)
    if coordinator["status"] == "sealed":
        integration = Path(coordinator["integration_worktree"])
        if head(integration) != coordinator["integration_head"] or not clean(
            integration
        ):
            raise ExecutionError("WORKTREE_CONFLICT", "sealed feature tip drifted")
        return coordinator
    if coordinator["status"] != "integrated":
        raise ExecutionError("PROMOTION_BLOCKED", "feature is not fully integrated")
    integration = Path(coordinator["integration_worktree"])
    recorded_head = coordinator["integration_head"]
    current_head = head(integration)
    coordinator_journal = journal_path(run_dir, feature_id, "coordinator")
    if current_head != recorded_head:
        parents = git(
            integration,
            ["rev-list", "--parents", "-n", "1", current_head],
            "inspect interrupted final seal",
        ).split()
        subject = git(
            integration,
            ["show", "-s", "--format=%s", current_head],
            "inspect interrupted final seal message",
        )
        if (
            not clean(integration)
            or len(parents) != 2
            or parents[1] != recorded_head
            or subject != message
            or not _journal_has_intent(
                coordinator_journal, "feature_seal_intent", recorded_head, message
            )
        ):
            raise ExecutionError(
                "WORKTREE_CONFLICT", "integration HEAD drifted before final seal"
            )
        _verify_integration_commit(
            integration,
            recorded_head,
            current_head,
            str(coordinator["project_scope"]),
        )
    elif not clean(integration):
        append_journal(
            coordinator_journal,
            {
                "event": "feature_seal_intent",
                "base": recorded_head,
                "message": message,
            },
        )
        git(integration, ["add", "-A"], "stage final feature changes")
        try:
            _verify_staged_integration(integration, str(coordinator["project_scope"]))
            git(integration, ["commit", "-m", message], "commit final feature changes")
        except ExecutionError:
            git(
                integration,
                ["restore", "--staged", ":/"],
                "unstage final feature changes",
            )
            raise
    coordinator["integration_head"] = head(integration)
    coordinator["final_evidence"] = evidence.strip()
    coordinator["status"] = "sealed"
    _save_coordinator(run_dir, feature_id, coordinator)
    append_journal(
        journal_path(run_dir, feature_id, "coordinator"),
        {"event": "feature_sealed", "head": coordinator["integration_head"]},
    )
    return coordinator


def promote_feature(run_dir: Path, feature_id: str, evidence: str) -> dict[str, Any]:
    if not evidence.strip():
        raise ExecutionError("PROMOTION_BLOCKED", "final evidence is empty")
    coordinator = _load_coordinator(run_dir, feature_id)
    if coordinator["status"] in {"promoted", "cleanup", "done"}:
        promoted_head = coordinator.get("promoted_head")
        if not isinstance(promoted_head, str):
            raise ExecutionError(
                "PROMOTION_BLOCKED", "promoted coordinator is missing its exact head"
            )
        _interop(
            "outer promotion reconciliation",
            reconcile_outer_promotion,
            run_dir,
            Path(str(coordinator["selected_project_root"])),
            promoted_head,
        )
    if coordinator["status"] == "done":
        return coordinator
    if coordinator["status"] not in {"sealed", "promoted", "cleanup"}:
        raise ExecutionError("PROMOTION_BLOCKED", "feature is not fully integrated")
    project = Path(coordinator["project_root"])
    integration = Path(coordinator["integration_worktree"])
    if coordinator["status"] == "sealed":
        if (
            not clean(integration)
            or head(integration) != coordinator["integration_head"]
        ):
            raise ExecutionError(
                "PROMOTION_BLOCKED", "integration worktree is not sealed"
            )
        project_branch = branch(project)
        project_head = head(project)
        if project_branch != coordinator["base_branch"] or not clean(project):
            raise ExecutionError(
                "PROMOTION_BLOCKED", "project branch moved or is dirty"
            )
        if project_head not in {
            coordinator["base_head"],
            coordinator["integration_head"],
        }:
            raise ExecutionError(
                "PROMOTION_BLOCKED", "project HEAD is neither the base nor promoted tip"
            )
        if coordinator.get("promotion_source") != "managed-local":
            try:
                verify_remote_default(
                    project,
                    expected_remote=str(coordinator["default_remote"]),
                    expected_branch=str(coordinator["default_branch"]),
                    expected_ref=str(coordinator["default_ref"]),
                    expected_head=str(coordinator["default_head"]),
                )
            except GitPromotionError as exc:
                raise ExecutionError("PROMOTION_BLOCKED", str(exc)) from exc
        append_journal(
            journal_path(run_dir, feature_id, "coordinator"),
            {
                "event": "promotion_intent",
                "base": coordinator["base_head"],
                "target": coordinator["integration_head"],
            },
        )
        try:
            promote_ff_only(
                project,
                expected_branch=str(coordinator["base_branch"]),
                expected_base=str(coordinator["base_head"]),
                target=str(coordinator["integration_head"]),
            )
        except GitPromotionError as exc:
            code = (
                "PROMOTION_FAILED"
                if head(project) == coordinator["integration_head"]
                else "PROMOTION_BLOCKED"
            )
            raise ExecutionError(code, str(exc)) from exc
        _interop(
            "outer promotion registration",
            record_outer_promotion,
            run_dir,
            Path(str(coordinator["selected_project_root"])),
            str(coordinator["integration_head"]),
        )
        coordinator["promoted_head"] = coordinator["integration_head"]
        coordinator["promotion_evidence"] = evidence.strip()
        coordinator["status"] = "promoted"
        _save_coordinator(run_dir, feature_id, coordinator)
    if (
        branch(project) != coordinator["base_branch"]
        or head(project) != coordinator["promoted_head"]
        or not clean(project)
    ):
        raise ExecutionError("PROMOTION_BLOCKED", "promoted project checkout drifted")
    coordinator["status"] = "cleanup"
    _save_coordinator(run_dir, feature_id, coordinator)
    append_journal(
        journal_path(run_dir, feature_id, "coordinator"),
        {
            "event": "integration_cleanup_intent",
            "branch": coordinator["integration_branch"],
            "head": coordinator["promoted_head"],
        },
    )
    retained: list[dict[str, str]] = []
    if not _cleanup_internal_resource(
        run_dir=run_dir,
        coordinator=coordinator,
        repo=project,
        kind="integration",
        worktree=integration,
        branch_name=str(coordinator["integration_branch"]),
        expected_tip=str(coordinator["promoted_head"]),
        reachable_tip=str(coordinator["promoted_head"]),
    ):
        retained.append(
            {
                "kind": "integration",
                "branch": coordinator["integration_branch"],
                "path": str(integration),
            }
        )
    coordinator["cleanup_retained"] = retained
    if retained:
        _save_coordinator(run_dir, feature_id, coordinator)
        raise ExecutionError("CLEANUP_BLOCKED", "integration resources remain")
    coordinator["status"] = "done"
    _save_coordinator(run_dir, feature_id, coordinator)
    return coordinator


def describe_status(run_dir: Path, feature_id: str) -> dict[str, Any]:
    coordinator = _load_coordinator(run_dir, feature_id)
    worker_liveness: list[dict[str, object]] = []
    active_wave = coordinator.get("active_wave")
    if isinstance(active_wave, str):
        active_wave_path = wave_path(run_dir, feature_id, active_wave)
        if active_wave_path.exists():
            active_wave_record = read_json(active_wave_path)
            for task_id in active_wave_record.get("task_ids", []):
                record_path = task_path(run_dir, feature_id, active_wave, task_id)
                if not record_path.exists():
                    continue
                task_record = read_json(record_path)
                worker_liveness.append(
                    {
                        "task_id": task_id,
                        "status": task_record.get("status"),
                        "attempt": task_record.get("attempt"),
                        "dispatched_at": task_record.get("dispatched_at"),
                        "worker_started_at": task_record.get("worker_started_at"),
                        "last_heartbeat_at": task_record.get("last_heartbeat_at"),
                        "heartbeat_sequence": task_record.get(
                            "heartbeat_sequence"
                        ),
                        "heartbeat_phase": task_record.get("heartbeat_phase"),
                    }
                )
    return {
        "feature_id": feature_id,
        "status": coordinator["status"],
        "base_branch": coordinator["base_branch"],
        "base_head": coordinator["base_head"],
        "integration_branch": coordinator["integration_branch"],
        "integration_worktree": coordinator["integration_worktree"],
        "integration_head": coordinator["integration_head"],
        "active_wave": coordinator["active_wave"],
        "wave_ids": coordinator["wave_ids"],
        "promoted_head": coordinator["promoted_head"],
        "cleanup_retained": coordinator["cleanup_retained"],
        "worker_liveness": worker_liveness,
    }
