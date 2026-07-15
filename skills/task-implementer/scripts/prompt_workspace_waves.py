#!/usr/bin/env python3
"""Journaled Git worktree lifecycle for task-implementer dependency waves."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

from prompt_workspace_core import (
    PromptWorkspaceError,
    RUN_ID_RE,
    ensure_private_dir,
    iso_seconds,
    load_json_object,
    now_utc,
    required_string,
    stable_json,
    verify_workspace,
    write_atomic,
    write_exclusive,
)
from prompt_workspace_execution import (
    ASSIGNMENT_SCHEMA,
    COORDINATOR_SCHEMA,
    RESULT_SCHEMA,
    SHA_RE,
    TASK_PLANE_SCHEMA,
    TASK_ID_RE,
    TASK_STATES,
    WAVE_SCHEMA,
    WAVE_STATES,
    TaskPlan,
    assert_no_unfinished_v1,
    batches_for_wave,
    build_dependency_waves,
    load_coordinator_state,
    orchestration_dir,
    parse_task_plans,
    sha256_json,
)
from prompt_workspace_runs import (
    read_handoff_text,
    scope_lock,
    verify_run,
)


BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,180}")
WAVE_ID_RE = re.compile(r"wave-(?:[0-9]{3}|r[0-9a-f]{8}-[0-9]{3})")


def _utc(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave clock must be timezone-aware"
        )
    return iso_seconds(value)


def _run_dir(workspace: dict[str, object], run_id: str) -> Path:
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run ID is invalid")
    runs_root = Path(
        required_string(workspace, "runs_root", "workspace manifest")
    ).resolve()
    run_dir = runs_root / run_id
    if run_dir.parent != runs_root:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run path is invalid")
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run directory is missing or unsafe"
        )
    return run_dir


def _git(
    repo: Path,
    arguments: list[str],
    description: str,
    *,
    check: bool = True,
    timeout: int = 60,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", f"Git could not {description}"
        ) from exc
    if check and result.returncode != 0:
        raise PromptWorkspaceError(
            "GIT_OPERATION_FAILED", f"Git could not {description}"
        )
    return result


def _git_text(repo: Path, arguments: list[str], description: str) -> str:
    return (
        _git(repo, arguments, description)
        .stdout.decode("utf-8", errors="strict")
        .strip()
    )


def _head(repo: Path) -> str:
    head = _git_text(repo, ["rev-parse", "--verify", "HEAD"], "read HEAD")
    if SHA_RE.fullmatch(head) is None:
        raise PromptWorkspaceError("WORKTREE_CONFLICT", "Git HEAD is invalid")
    return head


def _branch(repo: Path) -> str:
    branch = _git_text(repo, ["branch", "--show-current"], "read the current branch")
    if not branch:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "a named project branch is required"
        )
    return branch


def _clean(repo: Path) -> bool:
    return not _git(
        repo,
        ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
        "inspect worktree",
    ).stdout


def _common_dir(repo: Path) -> Path:
    value = _git_text(
        repo,
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        "read Git common directory",
    )
    path = Path(value).resolve()
    if not path.is_dir() or not os.access(path, os.W_OK | os.X_OK):
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git common directory is not writable"
        )
    return path


def _ref_valid(repo: Path, branch: str) -> None:
    if BRANCH_RE.fullmatch(branch) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "temporary branch name is invalid"
        )
    result = _git(
        repo, ["check-ref-format", "--branch", branch], "validate a branch", check=False
    )
    if result.returncode != 0:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "temporary branch name is invalid"
        )


def _append_journal(path: Path, record: dict[str, object]) -> None:
    ensure_private_dir(path.parent)
    data = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "wave journal path is unsafe"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID", "wave journal must be a regular file"
            )
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise PromptWorkspaceError(
                    "WORKSPACE_PATH_INVALID", "wave journal write was interrupted"
                )
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _journaled_git(
    journal: Path,
    repo: Path,
    arguments: list[str],
    description: str,
    clock: Callable[[], datetime],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    operation = hashlib.sha256(
        stable_json([str(repo), arguments, description])
    ).hexdigest()[:16]
    _append_journal(
        journal,
        {
            "at": _utc(clock),
            "operation": operation,
            "phase": "intent",
            "git_argv": arguments,
        },
    )
    result = _git(repo, arguments, description, check=False)
    _append_journal(
        journal,
        {
            "at": _utc(clock),
            "operation": operation,
            "phase": "observed",
            "returncode": result.returncode,
            "head": _head(repo),
        },
    )
    if check and result.returncode != 0:
        raise PromptWorkspaceError(
            "GIT_OPERATION_FAILED", f"Git could not {description}"
        )
    return result


def _state_root(manifest_path: Path) -> Path:
    path = manifest_path.expanduser().resolve()
    if len(path.parents) < 5 or path.parents[4].name != "task-implementer":
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "private state root is invalid"
        )
    return path.parents[4]


def _worktree_root(
    manifest_path: Path, workspace: dict[str, object], run_id: str
) -> Path:
    state_root = _state_root(manifest_path)
    root = (
        state_root
        / "worktrees"
        / required_string(workspace, "project_id", "workspace manifest")
        / required_string(workspace, "scope_id", "workspace manifest")
        / run_id
    )
    current = state_root
    for part in root.relative_to(state_root).parts:
        current /= part
        if current.is_symlink():
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID", "managed worktree root contains a symlink"
            )
        ensure_private_dir(current)
    return root


def _wave_path(run_dir: Path, wave_id: str) -> Path:
    if WAVE_ID_RE.fullmatch(wave_id) is None:
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "wave ID is invalid")
    return orchestration_dir(run_dir) / "waves" / f"{wave_id}.json"


def _load_wave(run_dir: Path, wave_id: str) -> dict[str, object]:
    value = load_json_object(_wave_path(run_dir, wave_id), "wave state")
    required = {
        "schema",
        "run_id",
        "wave_id",
        "status",
        "base_commit",
        "contract_commit",
        "integrated_head",
        "coordinator_write_claims",
        "integration_branch",
        "integration_worktree",
        "task_ids",
        "task_states",
        "created_at",
        "updated_at",
        "promoted_head",
        "cleanup_retained",
    }
    if (
        set(value) != required
        or value.get("schema") != WAVE_SCHEMA
        or value.get("run_id") != run_dir.name
        or value.get("wave_id") != wave_id
    ):
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "wave state is invalid")
    if value.get("status") not in WAVE_STATES:
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "wave status is invalid")
    task_ids = value.get("task_ids")
    states = value.get("task_states")
    if (
        not isinstance(task_ids, list)
        or not task_ids
        or not isinstance(states, dict)
        or set(states) != set(task_ids)
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave task index is invalid"
        )
    if any(state not in TASK_STATES for state in states.values()):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave task status is invalid"
        )
    return value


def _save_wave(run_dir: Path, wave: dict[str, object]) -> None:
    write_atomic(_wave_path(run_dir, str(wave["wave_id"])), stable_json(wave))


def _save_coordinator(run_dir: Path, state: dict[str, object]) -> None:
    write_atomic(orchestration_dir(run_dir) / "coordinator.json", stable_json(state))


def _assignment_path(run_dir: Path, wave_id: str, task_id: str) -> Path:
    if WAVE_ID_RE.fullmatch(wave_id) is None or TASK_ID_RE.fullmatch(task_id) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "assignment identity is invalid"
        )
    return orchestration_dir(run_dir) / "assignments" / wave_id / f"{task_id}.json"


def _result_path(run_dir: Path, wave_id: str, task_id: str) -> Path:
    if WAVE_ID_RE.fullmatch(wave_id) is None or TASK_ID_RE.fullmatch(task_id) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "result identity is invalid"
        )
    return orchestration_dir(run_dir) / "results" / wave_id / f"{task_id}.json"


def _journal_path(run_dir: Path, wave_id: str) -> Path:
    if WAVE_ID_RE.fullmatch(wave_id) is None:
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "wave ID is invalid")
    return orchestration_dir(run_dir) / "journals" / f"{wave_id}.jsonl"


def _task_plane_path(run_dir: Path, wave_id: str, task_id: str) -> Path:
    if WAVE_ID_RE.fullmatch(wave_id) is None or TASK_ID_RE.fullmatch(task_id) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "task-plane identity is invalid"
        )
    return orchestration_dir(run_dir) / "tasks" / wave_id / f"{task_id}.json"


def _load_task_plane(run_dir: Path, wave_id: str, task_id: str) -> dict[str, object]:
    value = load_json_object(_task_plane_path(run_dir, wave_id, task_id), "task plane")
    required = {
        "schema",
        "run_id",
        "wave_id",
        "task_id",
        "state",
        "base_commit",
        "assignment_sha256",
        "worker_session_sha256",
        "result_sha256",
        "commit",
        "created_at",
        "updated_at",
    }
    if (
        set(value) != required
        or value.get("schema") != TASK_PLANE_SCHEMA
        or value.get("run_id") != run_dir.name
        or value.get("wave_id") != wave_id
        or value.get("task_id") != task_id
        or value.get("state") not in TASK_STATES
    ):
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "task plane is invalid")
    return value


def _save_task_plane(run_dir: Path, plane: dict[str, object]) -> None:
    write_atomic(
        _task_plane_path(run_dir, str(plane["wave_id"]), str(plane["task_id"])),
        stable_json(plane),
    )


def _task_record(task: TaskPlan) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "position": task.position,
        "dependencies": list(task.dependencies),
        "write_claims": [claim.__dict__ for claim in task.write_claims],
        "conflict_domains": list(task.conflict_domains),
        "validation": task.validation,
        "done_criteria": task.done_criteria,
        "ownership_known": task.ownership_known,
    }


def _coordinator_claims(workspace: dict[str, object]) -> list[dict[str, str]]:
    scope = required_string(workspace, "scope", "workspace manifest")
    prefix = "" if scope == "." else f"{scope}/"
    claims = [
        {"kind": "prefix", "path": f"{prefix}docs"},
        {"kind": "exact", "path": f"{prefix}README.md"},
        {"kind": "exact", "path": f"{prefix}CHANGELOG.md"},
    ]
    if scope != ".":
        claims.extend(
            (
                {"kind": "exact", "path": "README.md"},
                {"kind": "exact", "path": "CHANGELOG.md"},
            )
        )
    return claims


def _validated_assignment(path: Path) -> dict[str, object]:
    assignment = load_json_object(path, "worker assignment")
    required = {
        "schema",
        "run_id",
        "wave_id",
        "task_id",
        "base_commit",
        "branch",
        "worktree",
        "scope_cwd",
        "result_path",
        "write_claims",
        "conflict_domains",
        "validation",
        "done_criteria",
        "plan_sha256",
        "created_at",
        "assignment_sha256",
    }
    if set(assignment) != required or assignment.get("schema") != ASSIGNMENT_SCHEMA:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker assignment fields are invalid"
        )
    recorded = assignment.get("assignment_sha256")
    unsigned = {
        key: value for key, value in assignment.items() if key != "assignment_sha256"
    }
    if not isinstance(recorded, str) or recorded != sha256_json(unsigned):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker assignment digest is invalid"
        )
    return assignment


def _validate_wave_git_identity(
    manifest_path: Path,
    workspace: dict[str, object],
    run_id: str,
    wave: dict[str, object],
) -> None:
    expected_root = _worktree_root(manifest_path, workspace, run_id) / str(
        wave["wave_id"]
    )
    expected_branch = _temporary_branch(
        workspace, run_id, str(wave["wave_id"]), "integration"
    )
    if Path(str(wave["integration_worktree"])).resolve() != (
        expected_root / "integration"
    ).resolve() or wave["integration_branch"] not in {None, expected_branch}:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "managed integration identity is invalid"
        )


def _validate_assignment_context(
    assignment: dict[str, object],
    workspace: dict[str, object],
    coordinator: dict[str, object],
    run_dir: Path,
    wave: dict[str, object],
    task_id: str,
) -> None:
    wave_id = str(wave["wave_id"])
    expected_worktree = Path(str(wave["integration_worktree"])).parent / task_id
    scope = required_string(workspace, "scope", "workspace manifest")
    expected_scope = expected_worktree if scope == "." else expected_worktree / scope
    expected_result = _result_path(run_dir, wave_id, task_id)
    expected_branch = _temporary_branch(workspace, run_dir.name, wave_id, task_id)
    task = next(
        (
            item
            for item in _wave_plan(coordinator, wave_id)
            if item["task_id"] == task_id
        ),
        None,
    )
    if task is None or any(
        (
            assignment.get("run_id") != run_dir.name,
            assignment.get("wave_id") != wave_id,
            assignment.get("task_id") != task_id,
            assignment.get("base_commit") != wave["contract_commit"],
            assignment.get("branch") != expected_branch,
            Path(str(assignment.get("worktree"))).resolve()
            != expected_worktree.resolve(),
            Path(str(assignment.get("scope_cwd"))).resolve()
            != expected_scope.resolve(),
            Path(str(assignment.get("result_path"))).resolve()
            != expected_result.resolve(),
            assignment.get("write_claims") != task["write_claims"],
            assignment.get("conflict_domains") != task["conflict_domains"],
            assignment.get("validation") != task["validation"],
            assignment.get("done_criteria") != task["done_criteria"],
            assignment.get("plan_sha256") != coordinator["plan_sha256"],
        )
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker assignment context is invalid"
        )


def plan_waves(
    manifest_path: Path,
    run_id: str,
    capacity: int,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    run_dir = _run_dir(workspace, run_id)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        verify_run(workspace, run_id, None)
        assert_no_unfinished_v1(run_dir)
        existing = load_coordinator_state(run_dir)
        if existing is not None:
            return existing
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        _common_dir(repo)
        if not _clean(repo):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "project checkout must be clean before wave planning",
            )
        branch = _branch(repo)
        base = _head(repo)
        text = read_handoff_text(run_dir)
        if text is None:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "handoff is required before wave planning"
            )
        tasks = parse_task_plans(text)
        waves = build_dependency_waves(tasks)
        if not waves:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "no pending tasks can be planned"
            )
        plan = [[_task_record(task) for task in wave] for wave in waves]
        created = _utc(clock)
        wave_ids = [f"wave-{index:03d}" for index in range(1, len(waves) + 1)]
        root = _worktree_root(manifest_path, workspace, run_id)
        ensure_private_dir(orchestration_dir(run_dir))
        for wave_id, tasks_in_wave in zip(wave_ids, waves, strict=True):
            wave_root = root / wave_id
            wave = {
                "schema": WAVE_SCHEMA,
                "run_id": run_id,
                "wave_id": wave_id,
                "status": "planned",
                "base_commit": None,
                "contract_commit": None,
                "integrated_head": None,
                "coordinator_write_claims": _coordinator_claims(workspace),
                "integration_branch": None,
                "integration_worktree": str(wave_root / "integration"),
                "task_ids": [task.task_id for task in tasks_in_wave],
                "task_states": {task.task_id: "planned" for task in tasks_in_wave},
                "created_at": created,
                "updated_at": created,
                "promoted_head": None,
                "cleanup_retained": [],
            }
            _save_wave(run_dir, wave)
            for task_item in tasks_in_wave:
                _save_task_plane(
                    run_dir,
                    {
                        "schema": TASK_PLANE_SCHEMA,
                        "run_id": run_id,
                        "wave_id": wave_id,
                        "task_id": task_item.task_id,
                        "state": "planned",
                        "base_commit": None,
                        "assignment_sha256": None,
                        "worker_session_sha256": None,
                        "result_sha256": None,
                        "commit": None,
                        "created_at": created,
                        "updated_at": created,
                    },
                )
        state = {
            "schema": COORDINATOR_SCHEMA,
            "run_id": run_id,
            "base_branch": branch,
            "initial_head": base,
            "plan_sha256": sha256_json(plan),
            "waves": [
                {
                    "wave_id": wave_id,
                    "tasks": task_records,
                    "batches": [
                        [task.task_id for task in batch]
                        for batch in batches_for_wave(tasks_in_wave, capacity)
                    ],
                }
                for wave_id, task_records, tasks_in_wave in zip(
                    wave_ids, plan, waves, strict=True
                )
            ],
            "active_wave": wave_ids[0],
            "status": "running",
            "created_at": created,
            "updated_at": created,
        }
        _save_coordinator(run_dir, state)
        return state


def replan_waves(
    manifest_path: Path,
    run_id: str,
    capacity: int,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Replace only unpromoted planning after steering or a blocked wave."""

    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, active = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, active)
        if active["status"] != "planned":
            raise PromptWorkspaceError(
                "STEERING_QUEUED_AFTER_WAVE",
                "only a resource-free planned wave can be replaced; blocked resources "
                "must be recovered explicitly",
            )
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        expected_head = _expected_primary_head(
            run_dir, coordinator, str(active["wave_id"])
        )
        if (
            _branch(repo) != coordinator["base_branch"]
            or _head(repo) != expected_head
            or not _clean(repo)
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "primary checkout changed before wave replanning"
            )
        integration = Path(str(active["integration_worktree"]))
        branch = active.get("integration_branch")
        if integration.exists() or (
            isinstance(branch, str)
            and _git(
                repo,
                ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                "inspect planned integration branch",
                check=False,
            ).returncode
            == 0
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "planned wave already owns Git resources"
            )
        text = read_handoff_text(run_dir)
        if text is None:
            raise PromptWorkspaceError("RUN_STATE_INVALID", "handoff is missing")
        tasks = parse_task_plans(text)
        waves = build_dependency_waves(tasks)
        if not waves:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "replanning produced no pending tasks"
            )
        plan = [[_task_record(task) for task in wave] for wave in waves]
        plan_sha256 = sha256_json(plan)
        if plan_sha256 == coordinator["plan_sha256"]:
            return coordinator
        prefix = f"wave-r{plan_sha256[:8]}"
        wave_ids = [f"{prefix}-{index:03d}" for index in range(1, len(waves) + 1)]
        created = _utc(clock)
        root = _worktree_root(manifest_path, workspace, run_id)
        existing_ids = {str(item["wave_id"]) for item in coordinator["waves"]}
        replacement_records: list[dict[str, object]] = []
        for wave_id, task_records, tasks_in_wave in zip(
            wave_ids, plan, waves, strict=True
        ):
            path = _wave_path(run_dir, wave_id)
            existing_wave = (
                load_json_object(path, "replacement wave") if path.exists() else None
            )
            wave = {
                "schema": WAVE_SCHEMA,
                "run_id": run_id,
                "wave_id": wave_id,
                "status": "planned",
                "base_commit": None,
                "contract_commit": None,
                "integrated_head": None,
                "coordinator_write_claims": _coordinator_claims(workspace),
                "integration_branch": None,
                "integration_worktree": str(root / wave_id / "integration"),
                "task_ids": [task.task_id for task in tasks_in_wave],
                "task_states": {task.task_id: "planned" for task in tasks_in_wave},
                "created_at": (
                    existing_wave["created_at"]
                    if existing_wave is not None
                    else created
                ),
                "updated_at": (
                    existing_wave["updated_at"]
                    if existing_wave is not None
                    else created
                ),
                "promoted_head": None,
                "cleanup_retained": [],
            }
            if existing_wave is not None and existing_wave != wave:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "deterministic replacement wave differs"
                )
            _save_wave(run_dir, wave)
            for task_item in tasks_in_wave:
                task_path = _task_plane_path(run_dir, wave_id, task_item.task_id)
                existing_task = (
                    load_json_object(task_path, "replacement task plane")
                    if task_path.exists()
                    else None
                )
                task_plane = {
                    "schema": TASK_PLANE_SCHEMA,
                    "run_id": run_id,
                    "wave_id": wave_id,
                    "task_id": task_item.task_id,
                    "state": "planned",
                    "base_commit": None,
                    "assignment_sha256": None,
                    "worker_session_sha256": None,
                    "result_sha256": None,
                    "commit": None,
                    "created_at": (
                        existing_task["created_at"]
                        if existing_task is not None
                        else created
                    ),
                    "updated_at": (
                        existing_task["updated_at"]
                        if existing_task is not None
                        else created
                    ),
                }
                if existing_task is not None and existing_task != task_plane:
                    raise PromptWorkspaceError(
                        "EXECUTION_STATE_INVALID",
                        "deterministic replacement task differs",
                    )
                _save_task_plane(run_dir, task_plane)
            replacement_records.append(
                {
                    "wave_id": wave_id,
                    "tasks": task_records,
                    "batches": [
                        [task.task_id for task in batch]
                        for batch in batches_for_wave(tasks_in_wave, capacity)
                    ],
                }
            )
        active_index = next(
            index
            for index, item in enumerate(coordinator["waves"])
            if item["wave_id"] == active["wave_id"]
        )
        superseded = list(coordinator["waves"][active_index:])
        coordinator["waves"].extend(
            record
            for record in replacement_records
            if str(record["wave_id"]) not in existing_ids
        )
        coordinator["active_wave"] = wave_ids[0]
        coordinator["plan_sha256"] = plan_sha256
        coordinator["status"] = "running"
        coordinator["updated_at"] = created
        _save_coordinator(run_dir, coordinator)
        for item in superseded:
            old = _load_wave(run_dir, str(item["wave_id"]))
            if old["status"] == "planned":
                old["status"] = "blocked"
                old["updated_at"] = created
                _save_wave(run_dir, old)
        return coordinator


def _coordinator_and_wave(
    workspace: dict[str, object], run_id: str
) -> tuple[Path, dict[str, object], dict[str, object]]:
    run_dir = _run_dir(workspace, run_id)
    coordinator = load_coordinator_state(run_dir)
    if coordinator is None or coordinator["status"] != "running":
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "run has no active v2 coordinator"
        )
    wave_id = coordinator.get("active_wave")
    if not isinstance(wave_id, str):
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "run has no active wave")
    return run_dir, coordinator, _load_wave(run_dir, wave_id)


def _expected_primary_head(
    run_dir: Path, coordinator: dict[str, object], wave_id: str
) -> str:
    """Return the latest promoted head preceding a wave, or the run's initial head."""

    expected = str(coordinator["initial_head"])
    for item in coordinator["waves"]:
        if item["wave_id"] == wave_id:
            return expected
        prior = _load_wave(run_dir, str(item["wave_id"]))
        promoted = prior.get("promoted_head")
        if prior["status"] in {"promoted", "cleanup", "done"} and isinstance(
            promoted, str
        ):
            expected = promoted
    raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "active wave is not indexed")


def _wave_plan(coordinator: dict[str, object], wave_id: str) -> list[dict[str, object]]:
    for item in coordinator["waves"]:
        if isinstance(item, dict) and item.get("wave_id") == wave_id:
            tasks = item.get("tasks")
            if isinstance(tasks, list):
                return tasks
    raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "active wave plan is missing")


def _temporary_branch(
    workspace: dict[str, object], run_id: str, wave_id: str, suffix: str
) -> str:
    opaque = hashlib.sha256(
        f"{workspace['project_id']}\0{workspace['scope_id']}\0{run_id}".encode()
    ).hexdigest()[:12]
    return f"codex/ti-{opaque}-{wave_id}-{suffix}"


def _registered_worktrees(repo: Path) -> dict[Path, dict[str, str]]:
    raw = _git(repo, ["worktree", "list", "--porcelain", "-z"], "list worktrees").stdout
    records: dict[Path, dict[str, str]] = {}
    current: dict[str, str] = {}
    for field in raw.decode("utf-8", errors="strict").split("\0"):
        if not field:
            if "worktree" in current:
                records[Path(current["worktree"]).resolve()] = current
            current = {}
            continue
        key, _, value = field.partition(" ")
        current[key] = value
    if "worktree" in current:
        records[Path(current["worktree"]).resolve()] = current
    return records


def _verify_linked_worktree(
    repo: Path, worktree: Path, branch: str, *, expected_head: str | None = None
) -> None:
    record = _registered_worktrees(repo).get(worktree.resolve())
    if (
        record is None
        or record.get("branch") != f"refs/heads/{branch}"
        or Path(
            _git_text(
                worktree,
                ["rev-parse", "--path-format=absolute", "--show-toplevel"],
                "read worker worktree root",
            )
        ).resolve()
        != worktree.resolve()
        or _common_dir(worktree) != _common_dir(repo)
        or (expected_head is not None and _head(worktree) != expected_head)
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "assigned path is not the expected linked worktree"
        )


def _ensure_worktree(
    repo: Path,
    path: Path,
    branch: str,
    base: str,
    journal: Path,
    clock: Callable[[], datetime],
    *,
    allow_descendant: bool = False,
) -> None:
    _ref_valid(repo, branch)
    registered = _registered_worktrees(repo)
    resolved = path.resolve()
    branch_ref = f"refs/heads/{branch}"
    if resolved in registered:
        record = registered[resolved]
        current_head = _head(resolved)
        exact_or_descendant = current_head == base or (
            allow_descendant
            and _git(
                resolved,
                ["merge-base", "--is-ancestor", base, current_head],
                "verify managed worktree ancestry",
                check=False,
            ).returncode
            == 0
        )
        if (
            record.get("branch") != branch_ref
            or not exact_or_descendant
            or not _clean(resolved)
        ):
            raise PromptWorkspaceError(
                "WORKTREE_COLLISION", "managed worktree identity differs"
            )
        return
    if (
        path.exists()
        or _git(
            repo,
            ["show-ref", "--verify", "--quiet", branch_ref],
            "inspect branch",
            check=False,
        ).returncode
        == 0
    ):
        raise PromptWorkspaceError(
            "WORKTREE_COLLISION", "managed branch or path already exists"
        )
    ensure_private_dir(path.parent)
    _journaled_git(
        journal,
        repo,
        [
            "worktree",
            "add",
            "--lock",
            "--reason",
            "task-implementer managed",
            "-b",
            branch,
            str(path),
            base,
        ],
        "create a managed worktree",
        clock,
    )
    if _head(path) != base or _branch(path) != branch or not _clean(path):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "created worktree identity is invalid"
        )


def prepare_wave(
    manifest_path: Path,
    run_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        if wave["status"] not in {"planned", "preparing"}:
            return wave
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        if _branch(repo) != coordinator["base_branch"] or not _clean(repo):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "primary checkout branch or cleanliness changed"
            )
        expected_base = _expected_primary_head(
            run_dir, coordinator, str(wave["wave_id"])
        )
        if _head(repo) != expected_base:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "primary checkout moved before wave preparation"
            )
        wave["status"] = "preparing"
        wave["base_commit"] = expected_base
        wave["updated_at"] = _utc(clock)
        branch = _temporary_branch(
            workspace, run_id, str(wave["wave_id"]), "integration"
        )
        wave["integration_branch"] = branch
        _save_wave(run_dir, wave)
        path = Path(str(wave["integration_worktree"]))
        _ensure_worktree(
            repo,
            path,
            branch,
            str(expected_base),
            _journal_path(run_dir, str(wave["wave_id"])),
            clock,
            allow_descendant=True,
        )
        return wave


def _reject_special_claims(repo: Path, task: dict[str, object]) -> None:
    output = _git(
        repo, ["ls-files", "--stage", "-z"], "inspect gitlinks"
    ).stdout.decode("utf-8", errors="strict")
    gitlinks: list[PurePosixPath] = []
    symlinks: list[PurePosixPath] = []
    for entry in output.split("\0"):
        if "\t" not in entry:
            continue
        mode = entry.split(" ", 1)[0]
        path = PurePosixPath(entry.split("\t", 1)[1])
        if mode == "160000":
            gitlinks.append(path)
        elif mode == "120000":
            symlinks.append(path)
    for claim in task["write_claims"]:
        claim_path = PurePosixPath(claim["path"])
        for gitlink in gitlinks:
            crosses = claim_path == gitlink or gitlink in claim_path.parents
            if claim["kind"] == "prefix":
                crosses = crosses or claim_path in gitlink.parents
            if crosses:
                raise PromptWorkspaceError(
                    "UNSUPPORTED_SUBMODULE_SCOPE",
                    f"{task['task_id']} write scope crosses a gitlink",
                )
        for symlink in symlinks:
            crosses = claim_path == symlink or symlink in claim_path.parents
            if claim["kind"] == "prefix":
                crosses = crosses or claim_path in symlink.parents
            if crosses:
                raise PromptWorkspaceError(
                    "UNSUPPORTED_SYMLINK_SCOPE",
                    f"{task['task_id']} write scope crosses a tracked symlink",
                )


def dispatch_wave(
    manifest_path: Path,
    run_id: str,
    contract_commit: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        if wave["status"] not in {"preparing", "running"}:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "wave is not ready for dispatch"
            )
        integration = Path(str(wave["integration_worktree"]))
        if _head(integration) != contract_commit or not _clean(integration):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "integration contract commit is not clean and exact",
            )
        if wave["status"] == "running":
            if wave["contract_commit"] != contract_commit:
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT", "running wave contract commit changed"
                )
            tasks = _wave_plan(coordinator, str(wave["wave_id"]))
            assignments: list[str] = []
            for task in tasks:
                task_id = str(task["task_id"])
                target = _assignment_path(run_dir, str(wave["wave_id"]), task_id)
                assignment = _validated_assignment(target)
                _validate_assignment_context(
                    assignment, workspace, coordinator, run_dir, wave, task_id
                )
                assignments.append(str(target))
            return {"wave": wave, "assignments": assignments}
        if (
            _git(
                integration,
                [
                    "merge-base",
                    "--is-ancestor",
                    str(wave["base_commit"]),
                    contract_commit,
                ],
                "verify contract ancestry",
                check=False,
            ).returncode
            != 0
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "contract commit does not descend from wave base"
            )
        contract_count = int(
            _git_text(
                integration,
                [
                    "rev-list",
                    "--count",
                    f"{wave['base_commit']}..{contract_commit}",
                ],
                "count coordinator contract commits",
            )
        )
        contract_paths = _changed_paths(
            integration, str(wave["base_commit"]), contract_commit
        )
        if contract_count > 1 or any(
            not _path_allowed(path, wave["coordinator_write_claims"])
            for path in contract_paths
        ):
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED",
                "coordinator contract commit changed files outside its locked ownership",
            )
        tasks = _wave_plan(coordinator, str(wave["wave_id"]))
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        scope = required_string(workspace, "scope", "workspace manifest")
        wave_root = Path(str(wave["integration_worktree"])).parent
        for task in tasks:
            _reject_special_claims(repo, task)
            task_id = str(task["task_id"])
            branch = _temporary_branch(workspace, run_id, str(wave["wave_id"]), task_id)
            worktree = wave_root / task_id
            _ensure_worktree(
                repo,
                worktree,
                branch,
                contract_commit,
                _journal_path(run_dir, str(wave["wave_id"])),
                clock,
            )
            scope_cwd = worktree if scope == "." else worktree / scope
            if not scope_cwd.is_dir():
                raise PromptWorkspaceError(
                    "ENVIRONMENT_BLOCKER",
                    "worker scope directory is missing in linked worktree",
                )
            target = _assignment_path(run_dir, str(wave["wave_id"]), task_id)
            existing_assignment = (
                _validated_assignment(target) if target.exists() else None
            )
            assignment: dict[str, object] = {
                "schema": ASSIGNMENT_SCHEMA,
                "run_id": run_id,
                "wave_id": wave["wave_id"],
                "task_id": task_id,
                "base_commit": contract_commit,
                "branch": branch,
                "worktree": str(worktree),
                "scope_cwd": str(scope_cwd),
                "result_path": str(
                    _result_path(run_dir, str(wave["wave_id"]), task_id)
                ),
                "write_claims": task["write_claims"],
                "conflict_domains": task["conflict_domains"],
                "validation": task["validation"],
                "done_criteria": task["done_criteria"],
                "plan_sha256": coordinator["plan_sha256"],
                "created_at": (
                    existing_assignment["created_at"]
                    if existing_assignment is not None
                    else _utc(clock)
                ),
            }
            assignment["assignment_sha256"] = sha256_json(assignment)
            ensure_private_dir(target.parent)
            if target.exists():
                if load_json_object(target, "worker assignment") != assignment:
                    raise PromptWorkspaceError(
                        "EXECUTION_STATE_INVALID", "immutable worker assignment differs"
                    )
            else:
                write_exclusive(target, stable_json(assignment))
            wave["task_states"][task_id] = "assigned"
            plane = _load_task_plane(run_dir, str(wave["wave_id"]), task_id)
            plane.update(
                {
                    "state": "assigned",
                    "base_commit": contract_commit,
                    "assignment_sha256": assignment["assignment_sha256"],
                    "updated_at": _utc(clock),
                }
            )
            _save_task_plane(run_dir, plane)
        wave["contract_commit"] = contract_commit
        wave["status"] = "running"
        wave["updated_at"] = _utc(clock)
        _save_wave(run_dir, wave)
        return {
            "wave": wave,
            "assignments": [
                str(
                    _assignment_path(
                        run_dir, str(wave["wave_id"]), str(task["task_id"])
                    )
                )
                for task in tasks
            ],
        }


def _session_fingerprint(session_id: str | None = None) -> str:
    value = session_id if session_id is not None else os.environ.get("CODEX_THREAD_ID")
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise PromptWorkspaceError(
            "SESSION_ID_UNAVAILABLE", "worker session identifier is required"
        )
    return hashlib.sha256(value.encode()).hexdigest()


def start_task(
    manifest_path: Path,
    run_id: str,
    task_id: str,
    assignment_sha256: str,
    *,
    session_id: str | None = None,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        assignment = _validated_assignment(
            _assignment_path(run_dir, str(wave["wave_id"]), task_id)
        )
        _validate_assignment_context(
            assignment, workspace, coordinator, run_dir, wave, task_id
        )
        if (
            wave["status"] != "running"
            and wave["task_states"].get(task_id) != "running"
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "blocked wave cannot start a new worker"
            )
        if (
            assignment.get("schema") != ASSIGNMENT_SCHEMA
            or assignment.get("assignment_sha256") != assignment_sha256
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "worker assignment digest is invalid"
            )
        worktree = Path(required_string(assignment, "worktree", "worker assignment"))
        scope_cwd = Path(required_string(assignment, "scope_cwd", "worker assignment"))
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        _verify_linked_worktree(
            repo,
            worktree,
            str(assignment["branch"]),
            expected_head=str(assignment["base_commit"]),
        )
        if Path.cwd().resolve() != scope_cwd.resolve():
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "worker must start from its absolute assigned scope cwd",
            )
        if (
            _head(worktree) != assignment["base_commit"]
            or _branch(worktree) != assignment["branch"]
            or not _clean(worktree)
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "worker worktree identity changed before authorization",
            )
        if wave["task_states"].get(task_id) not in {"assigned", "running"}:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task is not assignable"
            )
        worker_session = _session_fingerprint(session_id)
        plane = _load_task_plane(run_dir, str(wave["wave_id"]), task_id)
        if plane["assignment_sha256"] != assignment_sha256:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task plane assignment differs"
            )
        if plane["worker_session_sha256"] not in {None, worker_session}:
            raise PromptWorkspaceError(
                "WORKSPACE_BUSY", "task is owned by another worker session"
            )
        tasks_root = orchestration_dir(run_dir) / "tasks"
        for candidate in sorted(tasks_root.glob("*/*.json")):
            other = load_json_object(candidate, "task plane")
            if (
                other.get("task_id") != task_id
                and other.get("worker_session_sha256") == worker_session
            ):
                raise PromptWorkspaceError(
                    "FRESH_SESSION_REQUIRED",
                    "one worker session cannot own multiple task planes",
                )
        wave["task_states"][task_id] = "running"
        wave["updated_at"] = _utc(clock)
        _save_wave(run_dir, wave)
        plane["state"] = "running"
        plane["worker_session_sha256"] = worker_session
        plane["updated_at"] = _utc(clock)
        _save_task_plane(run_dir, plane)
        return {"assignment": assignment, "worker_session_sha256": worker_session}


def _dirty_paths(repo: Path) -> list[str]:
    paths: set[str] = set()
    commands = (
        ["diff", "--name-only", "-z"],
        ["diff", "--cached", "--name-only", "-z"],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    for arguments in commands:
        raw = _git(repo, arguments, "inspect recoverable worker paths").stdout
        paths.update(
            item for item in raw.decode("utf-8", errors="strict").split("\0") if item
        )
    return sorted(paths)


def recover_task(
    manifest_path: Path,
    run_id: str,
    task_id: str,
    *,
    confirmed_stopped: bool,
    session_id: str | None = None,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Transfer one interrupted running task to a fresh, explicitly confirmed worker."""

    if not confirmed_stopped:
        raise PromptWorkspaceError(
            "RECOVERY_CONFIRMATION_REQUIRED",
            "task recovery requires confirmation that the previous worker stopped",
        )
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        assignment = _validated_assignment(
            _assignment_path(run_dir, str(wave["wave_id"]), task_id)
        )
        _validate_assignment_context(
            assignment, workspace, coordinator, run_dir, wave, task_id
        )
        plane = _load_task_plane(run_dir, str(wave["wave_id"]), task_id)
        if plane["state"] != "running" or wave["task_states"].get(task_id) != "running":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "only an interrupted running task can recover",
            )
        worktree = Path(required_string(assignment, "worktree", "worker assignment"))
        scope_cwd = Path(required_string(assignment, "scope_cwd", "worker assignment"))
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        _verify_linked_worktree(repo, worktree, str(assignment["branch"]))
        if Path.cwd().resolve() != scope_cwd.resolve():
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "recovery worker must start from its absolute assigned scope cwd",
            )
        if _branch(worktree) != assignment["branch"]:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "recovery worktree branch identity changed"
            )
        base = required_string(assignment, "base_commit", "worker assignment")
        observed = _head(worktree)
        if observed != base and (
            _git_text(
                worktree,
                ["rev-list", "--count", f"{base}..{observed}"],
                "count recoverable worker commits",
            )
            != "1"
            or _git_text(
                worktree, ["rev-parse", f"{observed}^"], "inspect recoverable parent"
            )
            != base
        ):
            raise PromptWorkspaceError(
                "COMMIT_CONTRACT_INVALID",
                "recovery worktree must be at its base or one direct-child commit",
            )
        changed = _changed_paths(worktree, base, observed) if observed != base else []
        changed = sorted(set(changed) | set(_dirty_paths(worktree)))
        if any(not _path_allowed(path, assignment["write_claims"]) for path in changed):
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED",
                "interrupted worker state exceeds the locked write claims",
            )
        worker_session = _session_fingerprint(session_id)
        if worker_session == plane["worker_session_sha256"]:
            raise PromptWorkspaceError(
                "FRESH_SESSION_REQUIRED", "recovery requires a fresh worker session"
            )
        tasks_root = orchestration_dir(run_dir) / "tasks"
        for candidate in sorted(tasks_root.glob("*/*.json")):
            other = load_json_object(candidate, "task plane")
            if (
                other.get("task_id") != task_id
                and other.get("worker_session_sha256") == worker_session
            ):
                raise PromptWorkspaceError(
                    "FRESH_SESSION_REQUIRED",
                    "one worker session cannot own multiple task planes",
                )
        plane["worker_session_sha256"] = worker_session
        plane["updated_at"] = _utc(clock)
        _save_task_plane(run_dir, plane)
        return {
            "assignment": assignment,
            "worker_session_sha256": worker_session,
            "observed_head": observed,
            "changed_paths": changed,
        }


def _changed_paths(repo: Path, base: str, commit: str) -> list[str]:
    raw = _git(
        repo, ["diff", "--name-only", "-z", f"{base}..{commit}"], "inspect task paths"
    ).stdout
    return sorted(
        item for item in raw.decode("utf-8", errors="strict").split("\0") if item
    )


def _path_allowed(path: str, claims: list[dict[str, str]]) -> bool:
    candidate = PurePosixPath(path)
    for claim in claims:
        owner = PurePosixPath(claim["path"])
        if claim["kind"] == "exact" and candidate == owner:
            return True
        if claim["kind"] == "prefix" and (
            candidate == owner or owner in candidate.parents
        ):
            return True
    return False


def accept_task_result(
    manifest_path: Path,
    run_id: str,
    task_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        assignment = _validated_assignment(
            _assignment_path(run_dir, str(wave["wave_id"]), task_id)
        )
        _validate_assignment_context(
            assignment, workspace, coordinator, run_dir, wave, task_id
        )
        plane = _load_task_plane(run_dir, str(wave["wave_id"]), task_id)
        if plane["assignment_sha256"] != assignment["assignment_sha256"]:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "task plane assignment differs from the worker result",
            )
        result = load_json_object(
            _result_path(run_dir, str(wave["wave_id"]), task_id), "worker result"
        )
        required = {
            "schema",
            "run_id",
            "wave_id",
            "task_id",
            "assignment_sha256",
            "status",
            "commit",
            "changed_paths",
            "validation",
            "end_to_end_validation",
            "code_review",
            "completed_at",
            "result_sha256",
        }
        if (
            set(result) != required
            or result.get("schema") != RESULT_SCHEMA
            or result.get("run_id") != run_id
            or result.get("wave_id") != wave["wave_id"]
            or result.get("task_id") != task_id
            or result.get("assignment_sha256") != assignment.get("assignment_sha256")
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "worker result identity is invalid"
            )
        recorded_result_digest = result.get("result_sha256")
        unsigned_result = {
            key: value for key, value in result.items() if key != "result_sha256"
        }
        if not isinstance(
            recorded_result_digest, str
        ) or recorded_result_digest != sha256_json(unsigned_result):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "worker result digest is invalid"
            )
        if plane["state"] == "committed":
            if plane["result_sha256"] == recorded_result_digest and plane[
                "commit"
            ] == result.get("commit"):
                return result
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "committed worker result changed on retry"
            )
        if plane["state"] != "running":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "worker result requires an authorized task plane",
            )
        if result.get("status") != "committed":
            wave["task_states"][task_id] = "failed"
            wave["status"] = "blocked"
            wave["updated_at"] = _utc(clock)
            _save_wave(run_dir, wave)
            plane["state"] = "failed"
            plane["result_sha256"] = recorded_result_digest
            plane["updated_at"] = _utc(clock)
            _save_task_plane(run_dir, plane)
            return result
        commit = result.get("commit")
        if not isinstance(commit, str) or SHA_RE.fullmatch(commit) is None:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "worker result commit is invalid"
            )
        worktree = Path(required_string(assignment, "worktree", "worker assignment"))
        base = required_string(assignment, "base_commit", "worker assignment")
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        _verify_linked_worktree(
            repo,
            worktree,
            str(assignment["branch"]),
            expected_head=str(result["commit"]),
        )
        if (
            not _clean(worktree)
            or _head(worktree) != commit
            or _branch(worktree) != assignment["branch"]
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "worker branch is not clean at the reported commit"
            )
        if (
            _git_text(
                worktree,
                ["rev-list", "--count", f"{base}..{commit}"],
                "count worker commits",
            )
            != "1"
            or _git_text(worktree, ["rev-parse", f"{commit}^"], "inspect worker parent")
            != base
        ):
            raise PromptWorkspaceError(
                "COMMIT_CONTRACT_INVALID",
                "worker must create exactly one direct-child commit",
            )
        actual = _changed_paths(worktree, base, commit)
        if (
            actual != result.get("changed_paths")
            or not actual
            or any(
                not _path_allowed(path, assignment["write_claims"]) for path in actual
            )
        ):
            wave["task_states"][task_id] = "failed"
            wave["status"] = "blocked"
            wave["updated_at"] = _utc(clock)
            _save_wave(run_dir, wave)
            plane["state"] = "failed"
            plane["result_sha256"] = recorded_result_digest
            plane["commit"] = commit
            plane["updated_at"] = _utc(clock)
            _save_task_plane(run_dir, plane)
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED",
                "worker changed paths outside the locked write claims",
            )
        if any(
            not isinstance(result.get(field), str) or not str(result[field]).strip()
            for field in ("validation", "end_to_end_validation", "code_review")
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "worker validation and review evidence is incomplete",
            )
        wave["task_states"][task_id] = "committed"
        wave["updated_at"] = _utc(clock)
        _save_wave(run_dir, wave)
        plane["state"] = "committed"
        plane["result_sha256"] = recorded_result_digest
        plane["commit"] = commit
        plane["updated_at"] = _utc(clock)
        _save_task_plane(run_dir, plane)
        return result


def integrate_wave(
    manifest_path: Path,
    run_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        states = wave["task_states"]
        if any(
            states[task_id] not in {"committed", "merged"}
            for task_id in wave["task_ids"]
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "every wave task must be committed before integration",
            )
        integration = Path(str(wave["integration_worktree"]))
        if not _clean(integration):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "integration worktree is dirty"
            )
        wave["status"] = "integrating"
        wave["updated_at"] = _utc(clock)
        _save_wave(run_dir, wave)
        for task_id in wave["task_ids"]:
            assignment = _validated_assignment(
                _assignment_path(run_dir, str(wave["wave_id"]), str(task_id))
            )
            _validate_assignment_context(
                assignment,
                workspace,
                coordinator,
                run_dir,
                wave,
                str(task_id),
            )
            result = load_json_object(
                _result_path(run_dir, str(wave["wave_id"]), str(task_id)),
                "worker result",
            )
            plane = _load_task_plane(run_dir, str(wave["wave_id"]), str(task_id))
            unsigned_result = {
                key: value for key, value in result.items() if key != "result_sha256"
            }
            if (
                result.get("result_sha256") != sha256_json(unsigned_result)
                or result.get("result_sha256") != plane["result_sha256"]
                or result.get("commit") != plane["commit"]
            ):
                wave["status"] = "blocked"
                wave["updated_at"] = _utc(clock)
                _save_wave(run_dir, wave)
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "accepted worker result changed before integration",
                )
            commit = str(result["commit"])
            worker = Path(str(assignment["worktree"]))
            repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
            _verify_linked_worktree(
                repo, worker, str(assignment["branch"]), expected_head=commit
            )
            if (
                _branch(worker) != assignment["branch"]
                or _head(worker) != commit
                or not _clean(worker)
            ):
                wave["status"] = "blocked"
                wave["updated_at"] = _utc(clock)
                _save_wave(run_dir, wave)
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT",
                    "worker branch changed after its result was accepted",
                )
            if (
                _git(
                    integration,
                    ["merge-base", "--is-ancestor", commit, "HEAD"],
                    "inspect integrated task",
                    check=False,
                ).returncode
                == 0
            ):
                states[task_id] = "merged"
                plane = _load_task_plane(run_dir, str(wave["wave_id"]), str(task_id))
                plane["state"] = "merged"
                plane["updated_at"] = _utc(clock)
                _save_task_plane(run_dir, plane)
                continue
            merge = _journaled_git(
                _journal_path(run_dir, str(wave["wave_id"])),
                integration,
                ["merge", "--no-ff", "--no-edit", commit],
                "merge a task branch",
                clock,
                check=False,
            )
            if merge.returncode != 0:
                _git(
                    integration,
                    ["merge", "--abort"],
                    "abort conflicted integration",
                    check=False,
                )
                wave["status"] = "blocked"
                wave["updated_at"] = _utc(clock)
                _save_wave(run_dir, wave)
                raise PromptWorkspaceError(
                    "INTEGRATION_CONFLICT",
                    "task branches conflict during ordered integration",
                )
            states[task_id] = "merged"
            plane = _load_task_plane(run_dir, str(wave["wave_id"]), str(task_id))
            plane["state"] = "merged"
            plane["updated_at"] = _utc(clock)
            _save_task_plane(run_dir, plane)
            wave["updated_at"] = _utc(clock)
            _save_wave(run_dir, wave)
        wave["status"] = "promotion_pending"
        wave["integrated_head"] = _head(integration)
        wave["updated_at"] = _utc(clock)
        _save_wave(run_dir, wave)
        return wave


def _replace_task_status(text: str, task_id: str, status: str) -> str:
    match = re.search(
        rf"(?ms)^### {re.escape(task_id)}\s*\n(.*?)(?=^### task-|^## |\Z)", text
    )
    if match is None:
        raise PromptWorkspaceError("RUN_STATE_INVALID", f"handoff is missing {task_id}")
    section, count = re.subn(
        r"(?m)^- Status:.*$", f"- Status: {status}", match.group(1), count=1
    )
    if count != 1:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"handoff task {task_id} has no status"
        )
    return text[: match.start(1)] + section + text[match.end(1) :]


def promote_wave(
    manifest_path: Path,
    run_id: str,
    evidence_path: Path,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        if wave["status"] != "promotion_pending":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "wave is not ready for promotion"
            )
        verified_run = verify_run(workspace, run_id, None)
        if verified_run["steering_pending"] or verified_run["reconciliation_pending"]:
            raise PromptWorkspaceError(
                "STEERING_QUEUED_AFTER_WAVE",
                "pending steering or reconciliation must be resolved before promotion",
            )
        steering_path = run_dir / "steering.json"
        steering_sha256 = (
            hashlib.sha256(steering_path.read_bytes()).hexdigest()
            if steering_path.exists()
            else "none"
        )
        expected_evidence = (
            orchestration_dir(run_dir) / "evidence" / f"{wave['wave_id']}.json"
        )
        if evidence_path.expanduser().resolve() != expected_evidence.resolve():
            raise PromptWorkspaceError(
                "INTEGRATION_VALIDATION_FAILED",
                "integration evidence must use the active wave's private path",
            )
        evidence = load_json_object(expected_evidence, "integration evidence")
        if (
            set(evidence)
            != {
                "integration_head",
                "bound_revision",
                "steering_sha256",
                "validation",
                "code_review",
                "steering_reconciled",
            }
            or any(
                not isinstance(evidence.get(key), str) or not evidence[key].strip()
                for key in ("validation", "code_review")
            )
            or evidence.get("steering_reconciled") is not True
            or evidence.get("bound_revision") != verified_run["revision"]
            or evidence.get("steering_sha256") != steering_sha256
        ):
            raise PromptWorkspaceError(
                "INTEGRATION_VALIDATION_FAILED",
                "combined validation, review, and steering evidence is required",
            )
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        integration = Path(str(wave["integration_worktree"]))
        target = _head(integration)
        if (
            evidence.get("integration_head") != target
            or _branch(integration) != wave["integration_branch"]
            or not _clean(integration)
        ):
            raise PromptWorkspaceError(
                "INTEGRATION_VALIDATION_FAILED",
                "integration evidence is stale or the integration worktree changed",
            )
        for task_id in wave["task_ids"]:
            plane = _load_task_plane(run_dir, str(wave["wave_id"]), str(task_id))
            commit = plane.get("commit")
            if (
                plane["state"] != "merged"
                or not isinstance(commit, str)
                or _git(
                    integration,
                    ["merge-base", "--is-ancestor", commit, target],
                    "verify integrated task ancestry",
                    check=False,
                ).returncode
                != 0
            ):
                raise PromptWorkspaceError(
                    "INTEGRATION_VALIDATION_FAILED",
                    "integration tip does not contain every verified task commit",
                )
        integrated_head = wave.get("integrated_head")
        if not isinstance(integrated_head, str):
            raise PromptWorkspaceError(
                "INTEGRATION_VALIDATION_FAILED", "wave has no sealed integrated head"
            )
        final_count = int(
            _git_text(
                integration,
                ["rev-list", "--count", f"{integrated_head}..{target}"],
                "count coordinator final commits",
            )
        )
        final_paths = _changed_paths(integration, integrated_head, target)
        direct_final_commit = target == integrated_head or (
            final_count == 1
            and _git_text(
                integration,
                ["rev-parse", f"{target}^"],
                "inspect coordinator final commit parent",
            )
            == integrated_head
        )
        if (
            final_count > 1
            or not direct_final_commit
            or any(
                not _path_allowed(path, wave["coordinator_write_claims"])
                for path in final_paths
            )
        ):
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED",
                "coordinator final commit changed files outside shared documentation ownership",
            )
        if _branch(repo) != coordinator["base_branch"]:
            raise PromptWorkspaceError(
                "PROMOTION_BLOCKED",
                "primary checkout branch changed since wave preparation",
            )
        observed = _head(repo)
        if observed == target and _clean(repo):
            result = None
        elif observed == wave["base_commit"] and _clean(repo):
            result = _journaled_git(
                _journal_path(run_dir, str(wave["wave_id"])),
                repo,
                ["merge", "--ff-only", target],
                "promote the verified integration branch",
                clock,
                check=False,
            )
            observed = _head(repo)
        else:
            raise PromptWorkspaceError(
                "PROMOTION_BLOCKED",
                "primary checkout moved or became dirty before promotion",
            )
        if result is not None and result.returncode != 0 and observed != target:
            classification = (
                "unchanged" if observed == wave["base_commit"] else "unexpectedly_moved"
            )
            raise PromptWorkspaceError(
                "PROMOTION_FAILED", f"promotion result classified as {classification}"
            )
        if observed != target:
            raise PromptWorkspaceError(
                "PROMOTION_FAILED",
                "promotion did not publish the verified integration tip",
            )
        if not _clean(repo):
            raise PromptWorkspaceError(
                "PROMOTION_FAILED",
                "promotion reached the verified tip but a hook dirtied the checkout",
            )
        handoff = read_handoff_text(run_dir)
        if handoff is None:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "handoff disappeared during promotion"
            )
        for task_id in wave["task_ids"]:
            handoff = _replace_task_status(handoff, str(task_id), "done")
        write_atomic(run_dir / "handoff.md", handoff.encode("utf-8"))
        wave["status"] = "promoted"
        wave["promoted_head"] = observed
        wave["updated_at"] = _utc(clock)
        _save_wave(run_dir, wave)
        return wave


def _finalize_cleaned_wave(
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
    clock: Callable[[], datetime],
) -> None:
    wave_index = [item["wave_id"] for item in coordinator["waves"]].index(
        wave["wave_id"]
    )
    if wave_index + 1 < len(coordinator["waves"]):
        coordinator["active_wave"] = coordinator["waves"][wave_index + 1]["wave_id"]
    else:
        coordinator["active_wave"] = None
        coordinator["status"] = "done"
    coordinator["updated_at"] = _utc(clock)
    _save_coordinator(run_dir, coordinator)


def cleanup_wave(
    manifest_path: Path,
    run_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        if wave["status"] == "done":
            _finalize_cleaned_wave(run_dir, coordinator, wave, clock)
            return wave
        if wave["status"] not in {"promoted", "cleanup"}:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "cleanup requires verified promotion"
            )
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        promoted = str(wave["promoted_head"])
        if (
            _branch(repo) != coordinator["base_branch"]
            or _head(repo) != promoted
            or not _clean(repo)
        ):
            raise PromptWorkspaceError(
                "CLEANUP_BLOCKED",
                "project checkout is not clean at the promoted wave tip",
            )
        wave["status"] = "cleanup"
        wave["updated_at"] = _utc(clock)
        retained: list[str] = []
        _save_wave(run_dir, wave)
        resources: list[tuple[Path, str, str]] = []
        for task_id in wave["task_ids"]:
            assignment = _validated_assignment(
                _assignment_path(run_dir, str(wave["wave_id"]), str(task_id))
            )
            _validate_assignment_context(
                assignment,
                workspace,
                coordinator,
                run_dir,
                wave,
                str(task_id),
            )
            plane = _load_task_plane(run_dir, str(wave["wave_id"]), str(task_id))
            resources.append(
                (
                    Path(str(assignment["worktree"])),
                    str(assignment["branch"]),
                    str(plane["commit"]),
                )
            )
        resources.append(
            (
                Path(str(wave["integration_worktree"])),
                str(wave["integration_branch"]),
                promoted,
            )
        )
        for worktree, branch, expected_tip in resources:
            worktrees = _registered_worktrees(repo)
            registration = worktrees.get(worktree.resolve())
            registered = registration is not None
            branch_result = _git(
                repo,
                ["rev-parse", "--verify", f"refs/heads/{branch}"],
                "read cleanup branch",
                check=False,
            )
            if branch_result.returncode != 0:
                if not registered and not worktree.exists():
                    continue
                retained.append(f"{worktree} ({branch})")
                continue
            branch_commit = branch_result.stdout.decode(
                "ascii", errors="strict"
            ).strip()
            if branch_commit != expected_tip:
                retained.append(f"{worktree} ({branch})")
                continue
            reachable = (
                _git(
                    repo,
                    ["merge-base", "--is-ancestor", branch_commit, promoted],
                    "verify cleanup ancestry",
                    check=False,
                ).returncode
                == 0
            )
            if (
                not reachable
                or (worktree.exists() and not registered)
                or (
                    registered
                    and (
                        registration.get("branch") != f"refs/heads/{branch}"
                        or _head(worktree) != expected_tip
                        or not _clean(worktree)
                    )
                )
            ):
                retained.append(f"{worktree} ({branch})")
                continue
            if registered:
                _journaled_git(
                    _journal_path(run_dir, str(wave["wave_id"])),
                    repo,
                    ["worktree", "unlock", str(worktree)],
                    "unlock managed worktree",
                    clock,
                    check=False,
                )
                removal = _journaled_git(
                    _journal_path(run_dir, str(wave["wave_id"])),
                    repo,
                    ["worktree", "remove", str(worktree)],
                    "remove managed worktree",
                    clock,
                    check=False,
                )
                if removal.returncode != 0:
                    retained.append(f"{worktree} ({branch})")
                    continue
            deletion = _journaled_git(
                _journal_path(run_dir, str(wave["wave_id"])),
                repo,
                ["branch", "-d", branch],
                "delete reachable managed branch",
                clock,
                check=False,
            )
            if deletion.returncode != 0:
                retained.append(f"{worktree} ({branch})")
        wave["cleanup_retained"] = retained
        wave["updated_at"] = _utc(clock)
        if retained:
            _save_wave(run_dir, wave)
            return wave
        wave["status"] = "done"
        _save_wave(run_dir, wave)
        _finalize_cleaned_wave(run_dir, coordinator, wave, clock)
        return wave
