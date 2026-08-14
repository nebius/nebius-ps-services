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
import shlex
import shutil
import stat
import subprocess
import sys

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
from prompt_workspace_contract_delta import (
    active_contract_delta_head,
    complete_terminal_lifecycle_promotion,
    complete_contract_delta_promotion,
    contract_delta_active,
    prepare_contract_delta_promotion,
    prepare_terminal_lifecycle_promotion,
    recover_contract_delta_promotion,
    recover_terminal_lifecycle_promotion,
    restore_contract_delta_after_failed_promotion,
    restore_terminal_lifecycle_after_failed_promotion,
    terminal_lifecycle_seal,
    terminal_lifecycle_seal_active,
    terminal_lifecycle_seal_promoted,
)
from prompt_workspace_execution import (
    ASSIGNMENT_SCHEMA,
    COORDINATOR_SCHEMA,
    EXCLUSIVE_CONFLICT_CLASSES,
    INCOMING_HANDOFF_SCHEMA,
    RESULT_SCHEMA,
    SHA_RE,
    TASK_PLANE_SCHEMA,
    WORKER_GUARDRAILS,
    WORKER_HEARTBEAT_SECONDS,
    WORKER_MAX_SECONDS,
    WORKER_PHASES,
    WORKER_START_SECONDS,
    WORKER_STALL_SECONDS,
    TASK_ID_RE,
    TASK_STATES,
    WAVE_SCHEMA,
    WAVE_STATES,
    TaskPlan,
    WriteClaim,
    assert_no_unfinished_v1,
    batches_for_wave,
    build_dependency_waves,
    load_coordinator_state,
    orchestration_dir,
    parse_task_plans,
    sha256_json,
    worker_liveness_profile,
)
from prompt_workspace_interop import (
    acquire_interop,
    inspect_anchor,
    inspect_active_resources,
    load_checkpoint_preparation,
    load_checkpoint_receipt,
    load_interop,
    prepare_checkpoint,
    record_promotion,
    record_resource,
    release_interop,
)
from prompt_workspace_lanes import claim_generation
from prompt_workspace_runs import (
    _activate_next_queued_prompt_unlocked,
    read_handoff_text,
    scope_lock,
    verify_run,
)
from prompt_workspace_specs import (
    inspect_spec_documents,
    load_requirements_refinement,
    save_requirements_refinement,
    settle_prompt_impact_plan,
    verify_prompt_impact_plan,
    verify_project_agent_contract,
    verify_requirements_refinement_contract,
)


WORKTREE_SCRIPTS = Path(__file__).resolve().parents[2] / "worktree" / "scripts"
if str(WORKTREE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WORKTREE_SCRIPTS))
from git_promotion import (  # noqa: E402
    GitPromotionError,
    promote_ff_only,
)


BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,180}")
WAVE_ID_RE = re.compile(r"wave-(?:[0-9]{3}|r[0-9a-f]{8}-[0-9]{3})")
EXCLUSIVE_DOMAIN_CLAIM_PREFIX = "task-implementer/exclusive-class:"
PENDING_PLAN_SCHEMA = "task-implementer/pending-plan-v1"


def _utc(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave clock must be timezone-aware"
        )
    return iso_seconds(value)


def _time_value(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{label} is invalid"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", f"{label} is invalid")
    return parsed


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
        "batches",
        "batch_states",
        "active_batch_index",
        "created_at",
        "updated_at",
        "promoted_head",
        "workers_cleaned",
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
    if not isinstance(value.get("workers_cleaned"), bool) or not isinstance(
        value.get("cleanup_retained"), list
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave cleanup state is invalid"
        )
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
    batches = value.get("batches")
    batch_states = value.get("batch_states")
    active_batch_index = value.get("active_batch_index")
    if (
        not isinstance(batches, list)
        or not batches
        or [task_id for batch in batches for task_id in batch] != task_ids
        or not isinstance(batch_states, list)
        or len(batch_states) != len(batches)
        or any(state not in {"planned", "active", "done"} for state in batch_states)
        or (
            active_batch_index is not None
            and (
                not isinstance(active_batch_index, int)
                or active_batch_index < 0
                or active_batch_index >= len(batches)
                or batch_states[active_batch_index] != "active"
            )
        )
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave capacity batches are invalid"
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


def _incoming_handoff_path(run_dir: Path, wave_id: str, task_id: str) -> Path:
    if WAVE_ID_RE.fullmatch(wave_id) is None or TASK_ID_RE.fullmatch(task_id) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "incoming handoff identity is invalid"
        )
    return (
        orchestration_dir(run_dir) / "incoming-handoffs" / wave_id / f"{task_id}.json"
    )


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
        "worker_session_sha256_history",
        "dispatched_at",
        "started_at",
        "last_heartbeat_at",
        "heartbeat_sequence",
        "heartbeat_phase",
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
        or not isinstance(value.get("worker_session_sha256_history"), list)
        or not isinstance(value.get("heartbeat_sequence"), int)
        or value["heartbeat_sequence"] < 0
        or value.get("heartbeat_phase") not in {None, *WORKER_PHASES}
        or (
            value.get("state") == "running"
            and (
                value.get("dispatched_at") is None
                or not isinstance(value.get("dispatched_at"), str)
                or value.get("started_at") is None
                or value.get("last_heartbeat_at") is None
                or value.get("heartbeat_phase") is None
                or value["heartbeat_sequence"] < 1
            )
        )
        or len(value["worker_session_sha256_history"])
        != len(set(value["worker_session_sha256_history"]))
        or any(
            not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
            for item in value["worker_session_sha256_history"]
        )
        or (
            value.get("worker_session_sha256") is not None
            and (
                not value["worker_session_sha256_history"]
                or value["worker_session_sha256_history"][-1]
                != value["worker_session_sha256"]
            )
        )
    ):
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "task plane is invalid")
    for field in ("dispatched_at", "started_at", "last_heartbeat_at"):
        if value.get(field) is not None:
            _time_value(value[field], f"task plane {field}")
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
        "requirement_ids": task.requirement_ids,
        "design_id": task.design_id,
        "goal": task.goal,
        "plan": task.plan,
        "implementation_steps": task.implementation_steps,
        "validation": task.validation,
        "end_to_end_validation": task.end_to_end_validation,
        "done_criteria": task.done_criteria,
        "rollback_notes": task.rollback_notes,
        "stop_conditions": task.stop_conditions,
        "ownership_known": task.ownership_known,
    }


def _task_from_record(record: dict[str, object]) -> TaskPlan:
    """Rebuild an immutable task plan after strict staged-artifact validation."""

    expected = {
        "task_id",
        "position",
        "dependencies",
        "write_claims",
        "conflict_domains",
        "requirement_ids",
        "design_id",
        "goal",
        "plan",
        "implementation_steps",
        "validation",
        "end_to_end_validation",
        "done_criteria",
        "rollback_notes",
        "stop_conditions",
        "ownership_known",
    }
    claims = record.get("write_claims")
    if (
        set(record) != expected
        or TASK_ID_RE.fullmatch(str(record.get("task_id") or "")) is None
        or not isinstance(record.get("position"), int)
        or int(record["position"]) < 0
        or not isinstance(record.get("dependencies"), list)
        or not all(isinstance(item, str) for item in record["dependencies"])
        or not isinstance(claims, list)
        or not all(
            isinstance(item, dict)
            and set(item) == {"kind", "path"}
            and item.get("kind") in {"exact", "prefix"}
            and isinstance(item.get("path"), str)
            for item in claims
        )
        or not isinstance(record.get("conflict_domains"), list)
        or not all(isinstance(item, str) for item in record["conflict_domains"])
        or not all(
            isinstance(record.get(field), str)
            for field in (
                "requirement_ids",
                "design_id",
                "goal",
                "plan",
                "implementation_steps",
                "validation",
                "end_to_end_validation",
                "done_criteria",
                "rollback_notes",
                "stop_conditions",
            )
        )
        or not isinstance(record.get("ownership_known"), bool)
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "staged pending task is invalid"
        )
    return TaskPlan(
        task_id=str(record["task_id"]),
        position=int(record["position"]),
        dependencies=tuple(str(item) for item in record["dependencies"]),
        write_claims=tuple(
            WriteClaim(kind=str(item["kind"]), path=str(item["path"]))
            for item in claims
        ),
        conflict_domains=tuple(str(item) for item in record["conflict_domains"]),
        requirement_ids=str(record["requirement_ids"]),
        design_id=str(record["design_id"]),
        goal=str(record["goal"]),
        plan=str(record["plan"]),
        implementation_steps=str(record["implementation_steps"]),
        validation=str(record["validation"]),
        end_to_end_validation=str(record["end_to_end_validation"]),
        done_criteria=str(record["done_criteria"]),
        rollback_notes=str(record["rollback_notes"]),
        stop_conditions=str(record["stop_conditions"]),
        ownership_known=bool(record["ownership_known"]),
    )


def _active_resume_arguments(
    run_dir: Path, transition: str
) -> dict[str, object] | None:
    path = orchestration_dir(run_dir) / "resume-control.json"
    if not path.exists():
        return None
    control = load_json_object(path, "resume control")
    arguments = control.get("arguments")
    if (
        control.get("schema") != "task-implementer/resume-control-v1"
        or control.get("phase") != "intent"
        or control.get("transition") != transition
        or not isinstance(arguments, dict)
        or control.get("arguments_sha256")
        != hashlib.sha256(stable_json(arguments)).hexdigest()
        or not isinstance(control.get("resume_token"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(control["resume_token"])) is None
    ):
        return None
    return dict(arguments)


def _active_replan_identity(run_dir: Path) -> tuple[str | None, int]:
    path = orchestration_dir(run_dir) / "resume-control.json"
    if not path.exists():
        return None, 0
    control = load_json_object(path, "resume control")
    if (
        _active_resume_arguments(run_dir, "wave-replan") is not None
        and isinstance(control.get("resume_token"), str)
        and isinstance(control.get("epoch"), int)
    ):
        return str(control["resume_token"]), int(control["epoch"])
    return None, 0


def _pending_plan_path(
    run_dir: Path,
    wave_id: str,
    records: list[dict[str, object]],
) -> tuple[Path, str | None, int]:
    resume_token, resume_epoch = _active_replan_identity(run_dir)
    identity = resume_token or "uncontrolled"
    tasks_sha256 = sha256_json(records)
    return (
        orchestration_dir(run_dir)
        / "pending-plans"
        / wave_id
        / f"{identity}-{tasks_sha256}.json",
        resume_token,
        resume_epoch,
    )


def _validate_pending_plan_artifact(
    artifact: dict[str, object],
    run_dir: Path,
    wave_id: str,
    *,
    records: list[dict[str, object]] | None = None,
) -> list[TaskPlan]:
    tasks = artifact.get("tasks")
    if not isinstance(tasks, list) or any(not isinstance(item, dict) for item in tasks):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "pending plan task records are invalid"
        )
    typed_records = [dict(item) for item in tasks]
    resume_token, resume_epoch = _active_replan_identity(run_dir)
    if (
        set(artifact)
        != {
            "schema",
            "run_id",
            "wave_id",
            "tasks",
            "tasks_sha256",
            "resume_token",
            "resume_epoch",
            "created_at",
        }
        or artifact.get("schema") != PENDING_PLAN_SCHEMA
        or artifact.get("run_id") != run_dir.name
        or artifact.get("wave_id") != wave_id
        or (records is not None and typed_records != records)
        or artifact.get("tasks_sha256") != sha256_json(typed_records)
        or artifact.get("resume_token") != resume_token
        or artifact.get("resume_epoch") != resume_epoch
        or not isinstance(artifact.get("created_at"), str)
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "pending plan identity or contents are invalid"
        )
    return [_task_from_record(record) for record in typed_records]


def _stage_pending_plan(
    run_dir: Path,
    wave_id: str,
    records: list[dict[str, object]],
    *,
    clock: Callable[[], datetime],
) -> list[TaskPlan]:
    """Persist and reload one immutable correction plan before state consumption."""

    tasks_sha256 = sha256_json(records)
    path, resume_token, resume_epoch = _pending_plan_path(run_dir, wave_id, records)
    if path.exists():
        artifact = load_json_object(path, "pending plan")
    else:
        artifact = {
            "schema": PENDING_PLAN_SCHEMA,
            "run_id": run_dir.name,
            "wave_id": wave_id,
            "tasks": records,
            "tasks_sha256": tasks_sha256,
            "resume_token": resume_token,
            "resume_epoch": resume_epoch,
            "created_at": _utc(clock),
        }
        write_exclusive(path, stable_json(artifact))
    return _validate_pending_plan_artifact(artifact, run_dir, wave_id, records=records)


def _load_pending_plan(
    run_dir: Path, wave_id: str, records: list[dict[str, object]]
) -> list[TaskPlan]:
    """Load the immutable correction bytes that preceded coordinator publication."""

    path, _resume_token, _resume_epoch = _pending_plan_path(run_dir, wave_id, records)
    if not path.exists():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "published correction tasks have no immutable pending plan",
        )
    return _validate_pending_plan_artifact(
        load_json_object(path, "pending plan"),
        run_dir,
        wave_id,
        records=records,
    )


def _load_active_pending_plan(
    run_dir: Path,
    wave_id: str,
    *,
    indexed_task_ids: set[str] | None = None,
) -> list[TaskPlan] | None:
    resume_token, _resume_epoch = _active_replan_identity(run_dir)
    identity = resume_token or "uncontrolled"
    root = orchestration_dir(run_dir) / "pending-plans" / wave_id
    if not root.exists():
        return None
    candidates = sorted(root.glob(f"{identity}-*.json"))
    validated = [
        _validate_pending_plan_artifact(
            load_json_object(path, "pending plan"), run_dir, wave_id
        )
        for path in candidates
    ]
    if indexed_task_ids is not None:
        if any(
            0 < len({task.task_id for task in tasks} - indexed_task_ids) < len(tasks)
            for tasks in validated
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "immutable pending plan is only partially indexed",
            )
        validated = [
            tasks
            for tasks in validated
            if {task.task_id for task in tasks}.isdisjoint(indexed_task_ids)
        ]
    if len(validated) > 1:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "resume transition has multiple immutable pending plans",
        )
    if not validated:
        return None
    return validated[0]


def _coordinator_claims(workspace: dict[str, object]) -> list[dict[str, str]]:
    scope = required_string(workspace, "scope", "workspace manifest")
    prefix = "" if scope == "." else f"{scope}/"
    claims = [
        {"kind": "prefix", "path": f"{prefix}docs"},
        {"kind": "exact", "path": f"{prefix}AGENTS.md"},
        {"kind": "exact", "path": f"{prefix}README.md"},
        {"kind": "exact", "path": f"{prefix}CHANGELOG.md"},
    ]
    return claims


def _repository_claims(
    workspace: dict[str, object], tasks: list[TaskPlan]
) -> list[dict[str, str]]:
    claims = list(_coordinator_claims(workspace))
    for task in tasks:
        claims.extend(claim.__dict__ for claim in task.write_claims)
        for domain in task.conflict_domains:
            claims.append({"kind": "domain", "path": domain})
            domain_class = domain.split(":", 1)[0]
            if domain_class in EXCLUSIVE_CONFLICT_CLASSES:
                claims.append(
                    {
                        "kind": "domain",
                        "path": f"{EXCLUSIVE_DOMAIN_CLAIM_PREFIX}{domain_class}",
                    }
                )
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for claim in claims:
        unique[(claim["kind"], claim["path"])] = claim
    return [unique[key] for key in sorted(unique)]


def _existing_run_interop(
    manifest_path: Path,
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
) -> dict[str, object]:
    state = load_interop(run_dir, required=False)
    if state is not None:
        active_wave = coordinator.get("active_wave")
        wave = (
            _load_wave(run_dir, str(active_wave))
            if isinstance(active_wave, str)
            else None
        )
        allow_outer_dirty = wave is not None and contract_delta_active(
            workspace, run_dir, coordinator, wave
        )
        return acquire_interop(
            workspace,
            run_dir,
            manifest_path,
            str(coordinator["initial_head"]),
            allow_outer_dirty=allow_outer_dirty,
        )
    raise PromptWorkspaceError(
        "WORKFLOW_UPGRADE_REQUIRED",
        "unfinished persistent-lane runs without interop state are unsupported",
    )


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
        "workspace_manifest",
        "helper_path",
        "result_path",
        "write_claims",
        "conflict_domains",
        "requirement_ids",
        "design_id",
        "goal",
        "plan",
        "implementation_steps",
        "validation",
        "end_to_end_validation",
        "done_criteria",
        "rollback_notes",
        "stop_conditions",
        "worker_guardrails",
        "start_seconds",
        "heartbeat_seconds",
        "worker_profile",
        "read_only_warning_seconds",
        "read_only_seconds",
        "stall_seconds",
        "max_worker_seconds",
        "dependencies",
        "incoming_handoff_path",
        "incoming_handoff_sha256",
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


def _validated_incoming_handoff(path: Path) -> dict[str, object]:
    if (
        path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "incoming handoff path or mode is invalid"
        )
    handoff = load_json_object(path, "incoming handoff")
    required = {
        "schema",
        "run_id",
        "wave_id",
        "task_id",
        "assignment_base_commit",
        "dependencies",
        "predecessors",
        "created_at",
        "handoff_sha256",
    }
    recorded = handoff.get("handoff_sha256")
    unsigned = {key: value for key, value in handoff.items() if key != "handoff_sha256"}
    if (
        set(handoff) != required
        or handoff.get("schema") != INCOMING_HANDOFF_SCHEMA
        or not isinstance(handoff.get("dependencies"), list)
        or not isinstance(handoff.get("predecessors"), list)
        or not isinstance(recorded, str)
        or recorded != sha256_json(unsigned)
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "incoming handoff is invalid"
        )
    return handoff


def _task_location(
    coordinator: dict[str, object], task_id: str
) -> tuple[str, dict[str, object]] | None:
    for wave in coordinator["waves"]:
        for task in wave["tasks"]:
            if task.get("task_id") == task_id:
                return str(wave["wave_id"]), task
    return None


def _predecessor_record(
    run_dir: Path, coordinator: dict[str, object], dependency: str
) -> dict[str, object] | None:
    located = _task_location(coordinator, dependency)
    if located is None:
        return None
    wave_id, _ = located
    assignment = _validated_assignment(_assignment_path(run_dir, wave_id, dependency))
    result = load_json_object(
        _result_path(run_dir, wave_id, dependency), "worker result"
    )
    plane = _load_task_plane(run_dir, wave_id, dependency)
    unsigned_result = {
        key: value for key, value in result.items() if key != "result_sha256"
    }
    if (
        result.get("schema") != RESULT_SCHEMA
        or result.get("task_id") != dependency
        or result.get("wave_id") != wave_id
        or result.get("assignment_sha256") != assignment.get("assignment_sha256")
        or result.get("result_sha256") != sha256_json(unsigned_result)
        or plane.get("state") not in {"committed", "merged", "superseded"}
        or plane.get("result_sha256") != result.get("result_sha256")
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "predecessor result is invalid"
        )
    return {
        "task_id": dependency,
        "wave_id": wave_id,
        "assignment_sha256": assignment["assignment_sha256"],
        "result_sha256": result.get("result_sha256"),
        "commit": result.get("commit"),
        "changed_paths": result.get("changed_paths"),
        "summary": result.get("summary"),
        "decisions": result.get("decisions"),
        "open_risks": result.get("open_risks"),
        "validation": result.get("validation"),
        "review": result.get("code_review"),
    }


def _build_incoming_handoff(
    run_dir: Path,
    coordinator: dict[str, object],
    wave_id: str,
    task: dict[str, object],
    base_commit: str,
    created_at: str,
) -> dict[str, object]:
    dependencies = list(task["dependencies"])
    predecessor_ids: list[str] = []
    current_wave_index = next(
        index
        for index, item in enumerate(coordinator["waves"])
        if item["wave_id"] == wave_id
    )
    for prior_wave in coordinator["waves"][:current_wave_index]:
        predecessor_ids.extend(str(item["task_id"]) for item in prior_wave["tasks"])
    current_wave = coordinator["waves"][current_wave_index]
    task_batch_index = next(
        index
        for index, batch in enumerate(current_wave["batches"])
        if task["task_id"] in batch
    )
    for prior_batch in current_wave["batches"][:task_batch_index]:
        predecessor_ids.extend(str(task_id) for task_id in prior_batch)
    predecessor_ids.extend(
        dependency for dependency in dependencies if dependency not in predecessor_ids
    )
    predecessors = [
        record
        for dependency in predecessor_ids
        if (record := _predecessor_record(run_dir, coordinator, str(dependency)))
        is not None
    ]
    handoff: dict[str, object] = {
        "schema": INCOMING_HANDOFF_SCHEMA,
        "run_id": run_dir.name,
        "wave_id": wave_id,
        "task_id": task["task_id"],
        "assignment_base_commit": base_commit,
        "dependencies": dependencies,
        "predecessors": predecessors,
        "created_at": created_at,
    }
    handoff["handoff_sha256"] = sha256_json(handoff)
    return handoff


def _validate_incoming_handoff_context(
    handoff: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave_id: str,
    task: dict[str, object],
    base_commit: str,
) -> None:
    expected = _build_incoming_handoff(
        run_dir,
        coordinator,
        wave_id,
        task,
        base_commit,
        str(handoff.get("created_at")),
    )
    if handoff != expected:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "incoming handoff context is invalid"
        )


def _validate_assignment_handoff(
    assignment: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave_id: str,
    task: dict[str, object],
) -> dict[str, object]:
    path = Path(
        required_string(assignment, "incoming_handoff_path", "worker assignment")
    )
    handoff = _validated_incoming_handoff(path)
    if handoff.get("handoff_sha256") != assignment.get("incoming_handoff_sha256"):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker assignment handoff digest differs"
        )
    _validate_incoming_handoff_context(
        handoff,
        run_dir,
        coordinator,
        wave_id,
        task,
        required_string(assignment, "base_commit", "worker assignment"),
    )
    return handoff


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
    expected_workspace = run_dir.parent.parent / "workspace.json"
    expected_helper = Path(__file__).resolve().with_name("prompt_workspace.py")
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
    plane = _load_task_plane(run_dir, wave_id, task_id)
    historical_assignment = plane["state"] in {"committed", "merged", "superseded"}
    assignment_base_valid = (
        assignment.get("base_commit") == plane.get("base_commit")
        if historical_assignment
        else assignment.get("base_commit") == wave["contract_commit"]
    )
    assignment_plan_valid = (
        re.fullmatch(r"[0-9a-f]{64}", str(assignment.get("plan_sha256") or ""))
        is not None
        if historical_assignment
        else assignment.get("plan_sha256") == coordinator["plan_sha256"]
    )
    assignment_started = (
        plane["state"] in {"running", "committed", "merged", "superseded"}
        and isinstance(plane.get("started_at"), str)
        and re.fullmatch(r"[0-9a-f]{64}", str(plane.get("worker_session_sha256") or ""))
        is not None
    )
    assignment_guardrails_valid = (
        assignment.get("worker_guardrails") == WORKER_GUARDRAILS or assignment_started
    )
    # A terminal assignment is immutable evidence, not a future execution
    # context. Its recorded helper stays bound by the assignment and task-plane
    # digests even when source recovery runs from another installed/source copy.
    # Active assignments still require the exact helper that is executing now.
    assignment_helper_valid = historical_assignment or (
        Path(str(assignment.get("helper_path"))).resolve() == expected_helper.resolve()
    )
    liveness = worker_liveness_profile(task["dependencies"] if task else [])
    if task is None or any(
        (
            assignment.get("run_id") != run_dir.name,
            assignment.get("wave_id") != wave_id,
            assignment.get("task_id") != task_id,
            assignment.get("assignment_sha256") != plane.get("assignment_sha256"),
            not assignment_base_valid,
            assignment.get("branch") != expected_branch,
            Path(str(assignment.get("worktree"))).resolve()
            != expected_worktree.resolve(),
            Path(str(assignment.get("scope_cwd"))).resolve()
            != expected_scope.resolve(),
            Path(str(assignment.get("workspace_manifest"))).resolve()
            != expected_workspace.resolve(),
            not assignment_helper_valid,
            Path(str(assignment.get("result_path"))).resolve()
            != expected_result.resolve(),
            assignment.get("write_claims") != task["write_claims"],
            assignment.get("conflict_domains") != task["conflict_domains"],
            assignment.get("requirement_ids") != task["requirement_ids"],
            assignment.get("design_id") != task["design_id"],
            assignment.get("goal") != task["goal"],
            assignment.get("plan") != task["plan"],
            assignment.get("implementation_steps") != task["implementation_steps"],
            assignment.get("validation") != task["validation"],
            assignment.get("end_to_end_validation") != task["end_to_end_validation"],
            assignment.get("done_criteria") != task["done_criteria"],
            assignment.get("rollback_notes") != task["rollback_notes"],
            assignment.get("stop_conditions") != task["stop_conditions"],
            not assignment_guardrails_valid,
            assignment.get("start_seconds") != WORKER_START_SECONDS,
            assignment.get("heartbeat_seconds") != WORKER_HEARTBEAT_SECONDS,
            assignment.get("worker_profile") != liveness["worker_profile"],
            assignment.get("read_only_warning_seconds")
            != liveness["read_only_warning_seconds"],
            assignment.get("read_only_seconds") != liveness["read_only_seconds"],
            assignment.get("stall_seconds") != WORKER_STALL_SECONDS,
            assignment.get("max_worker_seconds") != WORKER_MAX_SECONDS,
            assignment.get("dependencies") != task["dependencies"],
            Path(str(assignment.get("incoming_handoff_path"))).resolve()
            != _incoming_handoff_path(run_dir, wave_id, task_id).resolve(),
            not assignment_plan_valid,
        )
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker assignment context is invalid"
        )


def _append_promotion_review_corrections(
    *,
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
    interop: dict[str, object],
    run_state: dict[str, object],
    impact: dict[str, object],
    impact_sha256: str,
    capacity: int,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    """Append one independent correction round to an unpromoted reviewed wave."""

    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    integration = Path(str(wave["integration_worktree"]))
    integrated_head = required_string(wave, "integrated_head", "reviewed wave")
    adopted_contract_head = active_contract_delta_head(
        workspace, run_dir, coordinator, wave
    )
    adopted_contract = adopted_contract_head is not None
    correction_base = _head(integration)
    _verify_linked_worktree(
        repo,
        integration,
        str(wave["integration_branch"]),
        expected_head=correction_base,
    )
    if (
        _branch(repo) != coordinator["base_branch"]
        or _head(repo) != wave["base_commit"]
        or (not _clean(repo) and not adopted_contract)
        or _branch(integration) != wave["integration_branch"]
        or correction_base not in {integrated_head, adopted_contract_head}
        or not _clean(integration)
        or wave.get("active_batch_index") is not None
        or any(state != "done" for state in wave["batch_states"])
        or any(
            wave["task_states"].get(task_id) not in {"merged", "superseded"}
            for task_id in wave["task_ids"]
        )
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT",
            "promotion review corrections require the exact clean retained wave",
        )
    text = read_handoff_text(run_dir)
    if text is None:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "handoff is missing")
    pending = parse_task_plans(text)
    pending_by_id = {task.task_id: task for task in pending}
    indexed_records = {
        str(task["task_id"]): task
        for item in coordinator["waves"]
        for task in item["tasks"]
    }
    active_record = next(
        item for item in coordinator["waves"] if item["wave_id"] == wave["wave_id"]
    )
    active_index = next(
        index
        for index, item in enumerate(coordinator["waves"])
        if item["wave_id"] == wave["wave_id"]
    )
    staged_ids = [
        str(task["task_id"])
        for task in active_record["tasks"]
        if task["task_id"] not in wave["task_ids"]
    ]
    if staged_ids:
        staged_records = [
            dict(task)
            for task in active_record["tasks"]
            if str(task["task_id"]) in set(staged_ids)
        ]
        live_corrections = _load_pending_plan(
            run_dir, str(wave["wave_id"]), staged_records
        )
    else:
        active_pending = _load_active_pending_plan(
            run_dir,
            str(wave["wave_id"]),
            indexed_task_ids=set(indexed_records),
        )
        unindexed_corrections = (
            active_pending
            if active_pending is not None
            else [task for task in pending if task.task_id not in indexed_records]
        )
        correction_waves = build_dependency_waves(unindexed_corrections)
        live_corrections = correction_waves[0] if correction_waves else []
        staged_records = [_task_record(task) for task in live_corrections]
    if not live_corrections:
        raise PromptWorkspaceError(
            "REPLAN_REQUIRED",
            "promotion review found no new isolated correction tasks",
        )
    correction_ids = {
        task.task_id for task in pending if task.task_id not in indexed_records
    }
    dependency_updates: dict[str, dict[str, object]] = {}
    for task_id, record in indexed_records.items():
        if task_id in staged_ids:
            continue
        current = pending_by_id.get(task_id)
        if current is None:
            continue
        current_record = _task_record(current)
        if current_record == record:
            continue
        prior_without_dependencies = dict(record)
        current_without_dependencies = dict(current_record)
        prior_without_dependencies.pop("dependencies", None)
        current_without_dependencies.pop("dependencies", None)
        owner_index = next(
            index
            for index, item in enumerate(coordinator["waves"])
            if any(task["task_id"] == task_id for task in item["tasks"])
        )
        future_wave = _load_wave(
            run_dir, str(coordinator["waves"][owner_index]["wave_id"])
        )
        future_plane = _load_task_plane(run_dir, str(future_wave["wave_id"]), task_id)
        future_artifacts = (
            _assignment_path(run_dir, str(future_wave["wave_id"]), task_id),
            _incoming_handoff_path(run_dir, str(future_wave["wave_id"]), task_id),
            _result_path(run_dir, str(future_wave["wave_id"]), task_id),
        )
        current_dependencies = set(current_record["dependencies"])
        if (
            owner_index <= active_index
            or prior_without_dependencies != current_without_dependencies
            or not current_dependencies
            or not current_dependencies <= correction_ids
            or future_wave.get("status") != "planned"
            or future_wave.get("integration_branch") is not None
            or os.path.lexists(str(future_wave.get("integration_worktree")))
            or any(
                state != "planned"
                for state in future_wave.get("task_states", {}).values()
            )
            or future_plane.get("state") != "planned"
            or future_plane.get("base_commit") is not None
            or any(path.exists() or path.is_symlink() for path in future_artifacts)
        ):
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED",
                "promotion review correction cannot rewrite an indexed task",
            )
        dependency_updates[task_id] = current_record
    allowed_predecessors = {
        str(task["task_id"])
        for item in coordinator["waves"][: active_index + 1]
        for task in item["tasks"]
    } | {task.task_id for task in live_corrections}
    if any(
        dependency not in allowed_predecessors
        for task in live_corrections
        for dependency in task.dependencies
    ):
        raise PromptWorkspaceError(
            "REPLAN_REQUIRED",
            "promotion review correction depends on unpromoted future work",
        )
    corrections = (
        live_corrections
        if staged_ids
        else _stage_pending_plan(
            run_dir,
            str(wave["wave_id"]),
            staged_records,
            clock=clock,
        )
    )
    claim_generation(
        workspace,
        name=str(interop["name"]),
        generation=int(interop["generation"]),
        lease_id=str(interop["lease_id"]),
        claims=_repository_claims(workspace, corrections),
    )
    created = _utc(clock)
    correction_records = [_task_record(task) for task in corrections]
    correction_batches = [
        [task.task_id for task in batch]
        for batch in batches_for_wave(corrections, capacity)
    ]
    if not staged_ids:
        active_record["tasks"].extend(correction_records)
        active_record["batches"].extend(correction_batches)
        indexed_after = set(indexed_records) | {task.task_id for task in corrections}
        for task_id, updated in dependency_updates.items():
            if set(updated["dependencies"]) <= indexed_after:
                owner = next(
                    item
                    for item in coordinator["waves"]
                    if any(task["task_id"] == task_id for task in item["tasks"])
                )
                owner["tasks"] = [
                    updated if task["task_id"] == task_id else task
                    for task in owner["tasks"]
                ]
        coordinator["plan_sha256"] = sha256_json(
            [item["tasks"] for item in coordinator["waves"]]
        )
        coordinator["prompt_revision"] = run_state["latest_revision"]
        coordinator["prompt_intent_sha256"] = run_state["latest_intent_sha256"]
        coordinator["updated_at"] = created
        _save_coordinator(run_dir, coordinator)
    for task in corrections:
        path = _task_plane_path(run_dir, str(wave["wave_id"]), task.task_id)
        existing_plane = (
            load_json_object(path, "correction task plane") if path.exists() else None
        )
        plane_created = (
            str(existing_plane["created_at"]) if existing_plane is not None else created
        )
        plane_updated = (
            str(existing_plane["updated_at"]) if existing_plane is not None else created
        )
        plane = {
            "schema": TASK_PLANE_SCHEMA,
            "run_id": run_dir.name,
            "wave_id": wave["wave_id"],
            "task_id": task.task_id,
            "state": "planned",
            "base_commit": None,
            "assignment_sha256": None,
            "worker_session_sha256": None,
            "worker_session_sha256_history": [],
            "dispatched_at": None,
            "started_at": None,
            "last_heartbeat_at": None,
            "heartbeat_sequence": 0,
            "heartbeat_phase": None,
            "result_sha256": None,
            "commit": None,
            "created_at": plane_created,
            "updated_at": plane_updated,
        }
        if existing_plane is not None:
            if existing_plane != plane:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "correction task plane differs"
                )
        else:
            _save_task_plane(run_dir, plane)
    if any(task.task_id in wave["task_ids"] for task in corrections):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "promotion review correction is only partially indexed in the wave",
        )
    wave["task_ids"].extend(task.task_id for task in corrections)
    wave["task_states"].update({task.task_id: "planned" for task in corrections})
    wave["batches"].extend(correction_batches)
    wave["batch_states"].extend("planned" for _ in correction_batches)
    wave["contract_commit"] = correction_base
    wave["status"] = "preparing"
    wave["workers_cleaned"] = False
    wave["updated_at"] = created
    _save_wave(run_dir, wave)
    settle_prompt_impact_plan(run_dir, coordinator, impact, impact_sha256)
    return coordinator


def _run_checkpoint_inputs(
    workspace: dict[str, object], run_dir: Path
) -> tuple[str, list[TaskPlan], list[dict[str, str]]]:
    text = read_handoff_text(run_dir)
    if text is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "handoff is required before checkpoint preparation"
        )
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    _common_dir(repo)
    anchor = inspect_anchor(workspace)
    if anchor.get("status") != "task-lane":
        raise PromptWorkspaceError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "wave execution requires a workspace-v2 persistent lane",
        )
    tasks = parse_task_plans(text)
    dependency_waves = build_dependency_waves(tasks)
    if not dependency_waves:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "no pending tasks can be planned"
        )
    return (
        str(anchor["head"]),
        tasks,
        _repository_claims(workspace, tasks),
    )


def prepare_run_checkpoint(
    manifest_path: Path,
    run_id: str,
) -> dict[str, object]:
    """Reserve the exact first-generation candidate for coordinator review."""

    workspace = verify_workspace(manifest_path)
    run_dir = _run_dir(workspace, run_id)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_state = verify_run(workspace, run_id, None)
        assert_no_unfinished_v1(run_dir)
        if load_coordinator_state(run_dir) is not None:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "an existing wave plan cannot prepare another initial checkpoint",
            )
        verify_requirements_refinement_contract(workspace, run_dir, run_state)
        base, _, claims = _run_checkpoint_inputs(workspace, run_dir)
        return prepare_checkpoint(workspace, run_dir, manifest_path, base, claims)


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
        run_state = verify_run(workspace, run_id, None)
        assert_no_unfinished_v1(run_dir)
        existing = load_coordinator_state(run_dir)
        refinement_contract = verify_requirements_refinement_contract(
            workspace, run_dir, run_state
        )
        impact = dict(refinement_contract["impact"])
        impact_sha256 = str(refinement_contract["impact_sha256"])
        if existing is not None:
            if run_state["steering_pending"]:
                raise PromptWorkspaceError(
                    "REPLAN_REQUIRED",
                    "pending steering must settle before an existing plan can continue",
                )
            plan_matches_latest = (
                existing.get("prompt_revision") == run_state["latest_revision"]
                and existing.get("prompt_intent_sha256")
                == run_state["latest_intent_sha256"]
            )
            if not plan_matches_latest and impact.get("plan_action") != "retain_plan":
                raise PromptWorkspaceError(
                    "REPLAN_REQUIRED",
                    "material prompt impact is not bound to the existing plan",
                )
            settle_prompt_impact_plan(run_dir, existing, impact, impact_sha256)
            verify_prompt_impact_plan(
                run_dir,
                existing,
                Path(required_string(workspace, "source_root", "workspace manifest")),
            )
            _existing_run_interop(manifest_path, workspace, run_dir, existing)
            return existing
        base, tasks, claims = _run_checkpoint_inputs(workspace, run_dir)
        anchor = inspect_anchor(workspace)
        if anchor.get("status") == "task-lane":
            promotion: dict[str, object] = {
                "promotion_branch": str(anchor["branch"]),
                "promotion_initial_head": str(anchor["head"]),
                "promotion_source": "managed-local",
                "remote": None,
                "default_branch": None,
                "default_ref": None,
                "default_head": None,
            }
        else:
            raise PromptWorkspaceError(
                "WORKFLOW_UPGRADE_REQUIRED",
                "wave execution requires a workspace-v2 persistent lane",
            )
        branch = str(promotion["promotion_branch"])
        waves = build_dependency_waves(tasks)
        if not waves:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "no pending tasks can be planned"
            )
        plan = [[_task_record(task) for task in wave] for wave in waves]
        preparation = load_checkpoint_preparation(
            run_dir, claims=claims, required=False
        )
        refresh_preparation = preparation is None or (
            preparation["before_head"] != base and preparation["status"] == "prepared"
        )
        if refresh_preparation:
            preparation = prepare_checkpoint(
                workspace, run_dir, manifest_path, base, claims
            )
            if (
                preparation["requires_review"] is True
                and preparation["status"] != "active-recovered"
                and preparation["checkpoint_state"] != "committed"
            ):
                raise PromptWorkspaceError(
                    "CHECKPOINT_REVIEW_REQUIRED",
                    "the whole-lane checkpoint candidate is reserved; review every "
                    "reported path and applicable project instruction, then repeat "
                    "private wave-plan",
                )
        acquire_interop(
            workspace,
            run_dir,
            manifest_path,
            base,
            claims,
        )
        checkpoint = load_checkpoint_receipt(run_dir)
        assert checkpoint is not None
        base = str(checkpoint["initial_head"])
        created = _utc(clock)
        wave_ids = [f"wave-{index:03d}" for index in range(1, len(waves) + 1)]
        root = _worktree_root(manifest_path, workspace, run_id)
        ensure_private_dir(orchestration_dir(run_dir))
        for wave_id, tasks_in_wave in zip(wave_ids, waves, strict=True):
            wave_root = root / wave_id
            batches = [
                [task.task_id for task in batch]
                for batch in batches_for_wave(tasks_in_wave, capacity)
            ]
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
                "batches": batches,
                "batch_states": ["planned"] * len(batches),
                "active_batch_index": None,
                "created_at": created,
                "updated_at": created,
                "promoted_head": None,
                "workers_cleaned": False,
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
                        "worker_session_sha256_history": [],
                        "dispatched_at": None,
                        "started_at": None,
                        "last_heartbeat_at": None,
                        "heartbeat_sequence": 0,
                        "heartbeat_phase": None,
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
            "default_remote": promotion["remote"],
            "default_branch": promotion["default_branch"],
            "default_ref": promotion["default_ref"],
            "default_head": promotion["default_head"],
            "promotion_source": promotion["promotion_source"],
            "prompt_revision": run_state["latest_revision"],
            "prompt_intent_sha256": run_state["latest_intent_sha256"],
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
        settle_prompt_impact_plan(run_dir, state, impact, impact_sha256)
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
        run_dir = _run_dir(workspace, run_id)
        run_state = verify_run(workspace, run_id, None)
        if run_state["steering_pending"]:
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED",
                "resolve pending steering before replanning",
            )
        refinement_contract = verify_requirements_refinement_contract(
            workspace, run_dir, run_state
        )
        impact = dict(refinement_contract["impact"])
        impact_sha256 = str(refinement_contract["impact_sha256"])
        coordinator = load_coordinator_state(run_dir)
        if coordinator is None:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "run has no v7 coordinator"
            )
        active_wave = coordinator.get("active_wave")
        if coordinator["status"] == "running" and isinstance(active_wave, str):
            active = _load_wave(run_dir, active_wave)
        elif (
            coordinator["status"] == "done"
            and active_wave is None
            and coordinator["waves"]
        ):
            active = _load_wave(run_dir, str(coordinator["waves"][-1]["wave_id"]))
        else:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "run has no replannable wave boundary"
            )
        interop = _existing_run_interop(manifest_path, workspace, run_dir, coordinator)
        _validate_wave_git_identity(manifest_path, workspace, run_id, active)
        if active["status"] == "blocked":
            failed_ids = [
                str(task_id)
                for task_id, state in active["task_states"].items()
                if state == "failed"
            ]
            active_batch_index = active.get("active_batch_index")
            if (
                not failed_ids
                or not isinstance(active_batch_index, int)
                or set(active["batches"][active_batch_index]) != set(failed_ids)
                or any(
                    state not in {"merged", "failed"}
                    for state in active["task_states"].values()
                )
            ):
                raise PromptWorkspaceError(
                    "STEERING_QUEUED_AFTER_WAVE",
                    "blocked correction replanning requires one exact failed batch",
                )
            repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
            for task_id in failed_ids:
                assignment = _validated_assignment(
                    _assignment_path(run_dir, str(active["wave_id"]), task_id)
                )
                plane = _load_task_plane(run_dir, str(active["wave_id"]), task_id)
                result = load_json_object(
                    _result_path(run_dir, str(active["wave_id"]), task_id),
                    "failed worker result",
                )
                unsigned = {
                    key: value
                    for key, value in result.items()
                    if key != "result_sha256"
                }
                worker = Path(
                    required_string(assignment, "worktree", "worker assignment")
                )
                base = required_string(assignment, "base_commit", "worker assignment")
                _verify_linked_worktree(
                    repo,
                    worker,
                    str(assignment["branch"]),
                    expected_head=base,
                )
                if (
                    plane.get("state") != "failed"
                    or result.get("status") == "committed"
                    or result.get("result_sha256") != sha256_json(unsigned)
                    or plane.get("result_sha256") != result.get("result_sha256")
                    or result.get("commit") != base
                    or result.get("changed_paths") != []
                    or _head(worker) != base
                    or not _clean(worker)
                ):
                    raise PromptWorkspaceError(
                        "WORKTREE_CONFLICT",
                        "blocked correction task is not an exact clean no-op",
                    )
                plane["state"] = "superseded"
                plane["commit"] = base
                plane["updated_at"] = _utc(clock)
                _save_task_plane(run_dir, plane)
                active["task_states"][task_id] = "superseded"
            active["batch_states"][active_batch_index] = "done"
            active["active_batch_index"] = None
            active["status"] = "promotion_pending"
            active["updated_at"] = _utc(clock)
            _save_wave(run_dir, active)
            text = read_handoff_text(run_dir)
            if text is None:
                raise PromptWorkspaceError("RUN_STATE_INVALID", "handoff is missing")
            for task_id in failed_ids:
                text = _replace_task_status(text, task_id, "superseded")
            write_atomic(run_dir / "handoff.md", text.encode("utf-8"))
        if active["status"] == "promotion_pending":
            return _append_promotion_review_corrections(
                workspace=workspace,
                run_dir=run_dir,
                coordinator=coordinator,
                wave=active,
                interop=interop,
                run_state=run_state,
                impact=impact,
                impact_sha256=impact_sha256,
                capacity=capacity,
                clock=clock,
            )
        if (
            active["status"] == "preparing"
            and active.get("integrated_head") is not None
            and active.get("contract_commit") == active.get("integrated_head")
        ):
            settle_prompt_impact_plan(run_dir, coordinator, impact, impact_sha256)
            return coordinator
        append_after_done = active["status"] == "done"
        if active["status"] not in {"planned", "done"}:
            raise PromptWorkspaceError(
                "STEERING_QUEUED_AFTER_WAVE",
                "only a resource-free planned tail can be replaced or a correction "
                "round can be appended to an exact reviewed integration or after a "
                "cleaned wave; blocked resources must be recovered explicitly",
            )
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        if append_after_done:
            expected_head = required_string(active, "promoted_head", "cleaned wave")
        else:
            expected_head = _expected_primary_head(
                run_dir, coordinator, str(active["wave_id"])
            )
        if (
            _branch(repo) != coordinator["base_branch"]
            or _head(repo) != expected_head
            or not _clean(repo)
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "persistent lane changed before wave replanning"
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
        active_index = next(
            index
            for index, item in enumerate(coordinator["waves"])
            if item["wave_id"] == active["wave_id"]
        )
        if append_after_done:
            active_index += 1
        completed_prefix = [
            item
            for item in coordinator["waves"][:active_index]
            if _load_wave(run_dir, str(item["wave_id"]))["status"] == "done"
        ]
        combined_plan_sha256 = sha256_json(
            [*[item["tasks"] for item in completed_prefix], *plan]
        )
        if combined_plan_sha256 == coordinator["plan_sha256"]:
            if impact.get("plan_action") == "replan_required" and (
                coordinator.get("prompt_revision") != impact.get("revision")
                or coordinator.get("prompt_intent_sha256")
                != impact.get("intent_sha256")
            ):
                raise PromptWorkspaceError(
                    "REPLAN_REQUIRED",
                    "material prompt impact requires a new plan identity",
                )
            settle_prompt_impact_plan(run_dir, coordinator, impact, impact_sha256)
            return coordinator
        claim_generation(
            workspace,
            name=str(interop["name"]),
            generation=int(interop["generation"]),
            lease_id=str(interop["lease_id"]),
            claims=_repository_claims(workspace, tasks),
        )
        prefix = f"wave-r{plan_sha256[:8]}"
        wave_ids = [f"{prefix}-{index:03d}" for index in range(1, len(waves) + 1)]
        created = _utc(clock)
        root = _worktree_root(manifest_path, workspace, run_id)
        replacement_records: list[dict[str, object]] = []
        for wave_id, task_records, tasks_in_wave in zip(
            wave_ids, plan, waves, strict=True
        ):
            path = _wave_path(run_dir, wave_id)
            existing_wave = (
                load_json_object(path, "replacement wave") if path.exists() else None
            )
            batches = [
                [task.task_id for task in batch]
                for batch in batches_for_wave(tasks_in_wave, capacity)
            ]
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
                "batches": batches,
                "batch_states": ["planned"] * len(batches),
                "active_batch_index": None,
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
                "workers_cleaned": False,
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
                    "worker_session_sha256_history": [],
                    "dispatched_at": None,
                    "started_at": None,
                    "last_heartbeat_at": None,
                    "heartbeat_sequence": 0,
                    "heartbeat_phase": None,
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
        superseded = list(coordinator["waves"][active_index:])
        coordinator["waves"] = [*completed_prefix, *replacement_records]
        coordinator["active_wave"] = wave_ids[0]
        coordinator["prompt_revision"] = run_state["latest_revision"]
        coordinator["prompt_intent_sha256"] = run_state["latest_intent_sha256"]
        coordinator["plan_sha256"] = combined_plan_sha256
        coordinator["status"] = "running"
        coordinator["updated_at"] = created
        _save_coordinator(run_dir, coordinator)
        settle_prompt_impact_plan(run_dir, coordinator, impact, impact_sha256)
        for item in superseded:
            old = _load_wave(run_dir, str(item["wave_id"]))
            if old["status"] == "planned":
                old["status"] = "blocked"
                old["updated_at"] = created
                _save_wave(run_dir, old)
        return coordinator


def _promotion_already_at_target(
    workspace: dict[str, object],
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> bool:
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    integration = Path(str(wave.get("integration_worktree")))
    return (
        wave.get("status") == "promotion_pending"
        and _branch(repo) == coordinator.get("base_branch")
        and integration.is_dir()
        and not integration.is_symlink()
        and _branch(integration) == wave.get("integration_branch")
        and _head(repo) == _head(integration)
        and _clean(repo)
        and _clean(integration)
    )


def _coordinator_and_wave(
    workspace: dict[str, object],
    run_id: str,
    *,
    allow_interrupted_promotion: bool = False,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    run_dir = _run_dir(workspace, run_id)
    coordinator = load_coordinator_state(run_dir)
    if coordinator is None or coordinator["status"] != "running":
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "run has no active coordinator"
        )
    wave_id = coordinator.get("active_wave")
    if not isinstance(wave_id, str):
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "run has no active wave")
    wave = _load_wave(run_dir, wave_id)
    record = next(
        (
            item
            for item in coordinator["waves"]
            if isinstance(item, dict) and item.get("wave_id") == wave_id
        ),
        None,
    )
    if (
        record is None
        or [task.get("task_id") for task in record.get("tasks", [])] != wave["task_ids"]
        or record.get("batches") != wave["batches"]
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "coordinator and active wave task indexes differ",
        )
    try:
        verify_prompt_impact_plan(
            run_dir,
            coordinator,
            Path(required_string(workspace, "source_root", "workspace manifest")),
        )
    except PromptWorkspaceError as error:
        if not (
            allow_interrupted_promotion
            and error.code == "REPLAN_REQUIRED"
            and error.message
            == "canonical project specs drifted after impact settlement"
            and _promotion_already_at_target(workspace, coordinator, wave)
        ):
            raise
    return run_dir, coordinator, wave


def _exact_command_flags(
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


def _exact_absolute_path(value: object, expected: Path) -> bool:
    if not isinstance(value, str):
        return False
    candidate = Path(value).expanduser()
    return candidate.is_absolute() and Path(os.path.abspath(candidate)) == Path(
        os.path.abspath(expected)
    )


def _exact_resolved_path(value: object, expected: Path) -> bool:
    if not isinstance(value, str):
        return False
    candidate = Path(value).expanduser()
    return candidate.is_absolute() and candidate.resolve(
        strict=False
    ) == expected.resolve(strict=False)


def _trusted_python_command(tokens: list[str], helper: Path) -> bool:
    if len(tokens) < 3 or "$" in " ".join(tokens):
        return False
    name = Path(tokens[0]).name
    if re.fullmatch(r"python3(?:\.[0-9]+)?", name) is None:
        return False
    candidate = tokens[0] if Path(tokens[0]).is_absolute() else shutil.which(tokens[0])
    if candidate is None:
        return False
    try:
        executable = Path(candidate).resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            return False
        expected = shutil.which(name)
        trusted = executable == Path(sys.executable).resolve(strict=True) or (
            expected is not None and executable == Path(expected).resolve(strict=True)
        )
    except OSError:
        return False
    return trusted and _exact_absolute_path(tokens[1], helper)


def _nul_git_paths(repo: Path, arguments: list[str], label: str) -> set[str]:
    raw = _git(repo, arguments, label).stdout
    try:
        return {
            item for item in raw.decode("utf-8", errors="strict").split("\0") if item
        }
    except UnicodeDecodeError as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{label} returned an invalid path"
        ) from error


def _prepared_contract_delta_is_safe(integration: Path, project: Path) -> bool:
    try:
        relative_project = project.resolve().relative_to(integration.resolve())
    except ValueError:
        return False
    allowed = {
        (relative_project / "docs" / "requirements.md").as_posix(),
        (relative_project / "docs" / "design.md").as_posix(),
    }
    staged = _nul_git_paths(
        integration,
        ["diff", "--cached", "--no-renames", "--name-only", "-z", "--"],
        "inspect staged contract paths",
    )
    unstaged = _nul_git_paths(
        integration,
        ["diff", "--no-renames", "--name-only", "-z", "--"],
        "inspect unstaged contract paths",
    )
    untracked = _nul_git_paths(
        integration,
        ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        "inspect untracked contract paths",
    )
    deleted = _nul_git_paths(
        integration,
        [
            "diff",
            "--cached",
            "--no-renames",
            "--diff-filter=D",
            "--name-only",
            "-z",
            "--",
        ],
        "inspect deleted contract paths",
    )
    return (
        not unstaged
        and not untracked
        and not deleted
        and (not staged or staged == allowed)
        and all(
            not (integration / relative).is_symlink()
            for relative in allowed
            if (integration / relative).exists()
            or (integration / relative).is_symlink()
        )
    )


def _promotion_coordinator_delta_is_safe(integration: Path, project: Path) -> bool:
    """Admit only the documented coordinator-owned reconciliation surface."""

    try:
        relative_project = project.resolve().relative_to(integration.resolve())
    except ValueError:
        return False
    allowed = {
        (relative_project / "docs" / "requirements.md").as_posix(),
        (relative_project / "docs" / "design.md").as_posix(),
        (relative_project / "README.md").as_posix(),
        (relative_project / "CHANGELOG.md").as_posix(),
    }
    staged = _nul_git_paths(
        integration,
        ["diff", "--cached", "--no-renames", "--name-only", "-z", "--"],
        "inspect staged coordinator paths",
    )
    unstaged = _nul_git_paths(
        integration,
        ["diff", "--no-renames", "--name-only", "-z", "--"],
        "inspect unstaged coordinator paths",
    )
    untracked = _nul_git_paths(
        integration,
        ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        "inspect untracked coordinator paths",
    )
    deleted = _nul_git_paths(
        integration,
        [
            "diff",
            "HEAD",
            "--no-renames",
            "--diff-filter=D",
            "--name-only",
            "-z",
            "--",
        ],
        "inspect deleted coordinator paths",
    )
    changed = staged | unstaged
    return (
        bool(changed)
        and changed <= allowed
        and not untracked
        and not deleted
        and all(
            not (integration / relative).is_symlink()
            for relative in allowed
            if (integration / relative).exists()
            or (integration / relative).is_symlink()
        )
    )


def _promotion_coordinator_commit_is_safe(
    integration: Path, project: Path, integrated_head: str, current: str
) -> bool:
    """Recognize the one clean direct-child coordinator documentation commit."""

    try:
        relative_project = project.resolve().relative_to(integration.resolve())
    except ValueError:
        return False
    allowed = {
        (relative_project / "docs" / "requirements.md").as_posix(),
        (relative_project / "docs" / "design.md").as_posix(),
        (relative_project / "README.md").as_posix(),
        (relative_project / "CHANGELOG.md").as_posix(),
    }
    changed = set(_changed_paths(integration, integrated_head, current))
    return (
        bool(changed)
        and changed <= allowed
        and _clean(integration)
        and _git_text(
            integration,
            ["rev-list", "--count", f"{integrated_head}..{current}"],
            "count coordinator commits",
        )
        == "1"
        and _git_text(
            integration,
            ["rev-parse", f"{current}^"],
            "inspect coordinator commit parent",
        )
        == integrated_head
        and all(
            not (integration / relative).is_symlink()
            for relative in allowed
            if (integration / relative).exists()
            or (integration / relative).is_symlink()
        )
    )


def _active_integration_project(
    manifest_path: Path, workspace: dict[str, object], run_id: str
) -> tuple[Path, Path, dict[str, object]]:
    run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
    _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
    wave_status = wave["status"]
    if wave_status not in {"preparing", "promotion_pending"}:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "project-agent lifecycle requires a prepared or promotion-pending wave",
        )
    integration = Path(str(wave["integration_worktree"]))
    if integration.is_symlink() or not integration.is_dir():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "integration worktree is missing or unsafe"
        )
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    scope = required_string(workspace, "scope", "workspace manifest")
    project = integration if scope == "." else integration / scope
    integrated_head = wave.get("integrated_head")
    if wave_status == "promotion_pending":
        if not isinstance(integrated_head, str):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "promotion-pending integration has no sealed head",
            )
        current_head = _head(integration)
        expected_head = current_head
        delta_is_safe = (
            _promotion_coordinator_delta_is_safe(integration, project)
            if current_head == integrated_head
            else _promotion_coordinator_commit_is_safe(
                integration, project, integrated_head, current_head
            )
        )
    elif integrated_head is None:
        expected_head = wave["base_commit"]
        delta_is_safe = _prepared_contract_delta_is_safe(integration, project)
    else:
        adopted_contract_head = active_contract_delta_head(
            workspace, run_dir, coordinator, wave
        )
        contract_commit = wave.get("contract_commit")
        if contract_commit not in {integrated_head, adopted_contract_head}:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "integration worktree identity is stale",
            )
        expected_head = contract_commit
        delta_is_safe = _prepared_contract_delta_is_safe(integration, project)
    if (
        Path(
            _git_text(
                integration,
                ["rev-parse", "--path-format=absolute", "--show-toplevel"],
                "inspect integration root",
            )
        ).resolve()
        != integration.resolve()
        or _common_dir(integration) != _common_dir(repo)
        or _branch(integration) != wave["integration_branch"]
        or _head(integration) != expected_head
        or not delta_is_safe
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "integration worktree identity is stale"
        )
    if project.is_symlink() or not project.is_dir():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "integration project is missing or unsafe"
        )
    return run_dir, project, wave


def authorize_project_agent_lifecycle(
    manifest_path: Path, run_id: str, command: str
) -> dict[str, object]:
    """Attest one exact run-owned project-instructions command to the hook."""

    if not command or any(character in command for character in ";|<>`\n"):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent command is not canonical"
        )
    workspace = verify_workspace(manifest_path)
    run_dir, project, _wave = _active_integration_project(
        manifest_path, workspace, run_id
    )
    try:
        tokens = shlex.split(command)
    except ValueError as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent command is malformed"
        ) from error
    task_helper = Path(__file__).resolve().with_name("prompt_workspace.py")
    if (
        _trusted_python_command(tokens, task_helper)
        and tokens[2] == "coordinator-commit"
    ):
        flags = _exact_command_flags(tokens, boolean_flags={"--json"})
        if (
            flags is None
            or set(flags)
            not in (
                {"--workspace", "--run-id"},
                {"--workspace", "--run-id", "--json"},
            )
            or not _exact_absolute_path(flags.get("--workspace"), manifest_path)
            or flags.get("--run-id") != run_id
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "coordinator commit command flags are invalid",
            )
        outer_project = Path(
            required_string(workspace, "source_root", "workspace manifest")
        ).resolve()
        return {
            "status": "authorized",
            "action": "coordinator-commit",
            "outer_project_root": str(outer_project),
            "project_root": str(project.resolve()),
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        }
    instruction_helper = (
        Path(__file__).resolve().parents[2]
        / "project-agent-instructions"
        / "scripts"
        / "project_agent_instructions.py"
    )
    spec_helper = (
        Path(__file__).resolve().parents[2]
        / "maintain-project-specs"
        / "scripts"
        / "project_specs.py"
    )
    action = tokens[2]
    instruction_action = action in {"inspect", "render"} and _trusted_python_command(
        tokens, instruction_helper
    )
    validation_action = action == "validate" and _trusted_python_command(
        tokens, spec_helper
    )
    if not instruction_action and not validation_action:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent command owner is invalid"
        )
    flags = _exact_command_flags(tokens, boolean_flags=set())
    orchestration = run_dir / "orchestration"
    private_root = orchestration / "project-agent-instructions"
    manifest = private_root / "manifest.json"
    decision = private_root / "decision.json"
    state = private_root / "state.json"
    expected: dict[str, dict[str, object]] = {
        "validate": {
            "--project-root": project,
            "--output": orchestration / "project-agent-spec-receipt.json",
            "--task-implementer-workspace": manifest_path,
            "--task-implementer-run-id": run_id,
        },
        "inspect": {
            "--project-root": project,
            "--spec-owner": "maintain-project-specs",
            "--requirements": "docs/requirements.md",
            "--design": "docs/design.md",
            "--spec-receipt": orchestration / "project-agent-spec-receipt.json",
            "--runtime-config": orchestration / "project-agent-runtime.json",
            "--codex-home": _state_root(manifest_path).parent,
            "--private-root": private_root,
            "--output": manifest,
        },
        "render": {
            "--private-root": private_root,
            "--manifest": manifest,
            "--decision": decision,
            "--output": private_root / "rules.md",
            "--state": state,
        },
    }
    bindings = expected[action]
    if (
        flags is None
        or set(flags)
        != set(bindings) | ({"--session-id"} if action == "validate" else set())
        or (
            action == "validate"
            and re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                str(flags.get("--session-id", "")),
            )
            is None
        )
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent command flags are invalid"
        )
    for flag, value in bindings.items():
        observed = flags.get(flag)
        if flag == "--codex-home" and isinstance(value, Path):
            matches = _exact_resolved_path(observed, value)
        elif isinstance(value, Path):
            matches = _exact_absolute_path(observed, value)
        else:
            matches = observed == value
        if not matches:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", f"project-agent {flag} binding is invalid"
            )
    outer_project = Path(
        required_string(workspace, "source_root", "workspace manifest")
    ).resolve()
    return {
        "status": "authorized",
        "action": action,
        "outer_project_root": str(outer_project),
        "project_root": str(project.resolve()),
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
    }


def commit_coordinator_delta(
    manifest_path: Path,
    run_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Commit the single exact coordinator-owned post-integration delta."""

    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        if wave["status"] != "promotion_pending":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "coordinator commit requires a promotion-pending wave",
            )
        integration = Path(str(wave["integration_worktree"]))
        project = integration / required_string(
            workspace, "scope", "workspace manifest"
        )
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        integrated_head = wave.get("integrated_head")
        if not isinstance(integrated_head, str):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "wave has no sealed integrated head"
            )
        current = _head(integration)
        _verify_linked_worktree(
            repo,
            integration,
            str(wave["integration_branch"]),
            expected_head=current,
        )
        if current != integrated_head:
            final_paths = _changed_paths(integration, integrated_head, current)
            if not _promotion_coordinator_commit_is_safe(
                integration, project, integrated_head, current
            ):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "existing coordinator commit is not the exact final delta",
                )
            return {
                "status": "reused",
                "commit": current,
                "changed_paths": sorted(final_paths),
            }
        if not _promotion_coordinator_delta_is_safe(integration, project):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "coordinator delta is empty or outside shared documentation ownership",
            )
        staged = _nul_git_paths(
            integration,
            ["diff", "--cached", "--no-renames", "--name-only", "-z", "--"],
            "read staged coordinator paths",
        )
        unstaged = _nul_git_paths(
            integration,
            ["diff", "--no-renames", "--name-only", "-z", "--"],
            "read unstaged coordinator paths",
        )
        changed = sorted(staged | unstaged)
        stage = _journaled_git(
            _journal_path(run_dir, str(wave["wave_id"])),
            integration,
            ["add", "-A", "--", *changed],
            "stage coordinator documentation delta",
            clock,
            check=False,
        )
        if stage.returncode != 0:
            raise PromptWorkspaceError(
                "GIT_OPERATION_FAILED", "Git could not stage coordinator documentation"
            )
        staged_after = _nul_git_paths(
            integration,
            ["diff", "--cached", "--no-renames", "--name-only", "-z", "--"],
            "verify staged coordinator paths",
        )
        unstaged_after = _nul_git_paths(
            integration,
            ["diff", "--no-renames", "--name-only", "-z", "--"],
            "verify unstaged coordinator paths",
        )
        if staged_after != set(changed) or unstaged_after:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "coordinator staging changed unexpectedly"
            )
        committed = _journaled_git(
            _journal_path(run_dir, str(wave["wave_id"])),
            integration,
            ["commit", "-m", "Reconcile project contract and operator docs"],
            "commit coordinator documentation delta",
            clock,
            check=False,
        )
        if committed.returncode != 0:
            raise PromptWorkspaceError(
                "GIT_OPERATION_FAILED", "Git could not commit coordinator documentation"
            )
        commit = _head(integration)
        final_paths = _changed_paths(integration, integrated_head, commit)
        if (
            _git_text(
                integration,
                ["rev-parse", f"{commit}^"],
                "verify coordinator commit parent",
            )
            != integrated_head
            or not _clean(integration)
            or final_paths != changed
            or any(
                not _path_allowed(path, wave["coordinator_write_claims"])
                for path in final_paths
            )
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "coordinator commit postcondition is invalid",
            )
        return {
            "status": "committed",
            "commit": commit,
            "changed_paths": sorted(final_paths),
        }


def authorize_lifecycle_impact(
    manifest_path: Path, run_id: str, command: str
) -> dict[str, object]:
    """Attest the canonical first wave-plan command for lifecycle accounting."""

    workspace = verify_workspace(manifest_path)
    run_dir = _run_dir(workspace, run_id)
    try:
        tokens = shlex.split(command)
    except ValueError as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave-plan command is malformed"
        ) from error
    helper = Path(__file__).resolve().with_name("prompt_workspace.py")
    if not _trusted_python_command(tokens, helper) or tokens[2] != "wave-plan":
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave-plan command owner is invalid"
        )
    flags = _exact_command_flags(tokens, boolean_flags={"--json"})
    if flags is None or set(flags) not in (
        {"--workspace", "--run-id", "--capacity"},
        {"--workspace", "--run-id", "--capacity", "--json"},
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave-plan command flags are invalid"
        )
    try:
        capacity = int(str(flags["--capacity"]))
    except (KeyError, ValueError) as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave-plan capacity is invalid"
        ) from error
    if (
        not _exact_absolute_path(flags.get("--workspace"), manifest_path)
        or flags.get("--run-id") != run_id
        or capacity < 1
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave-plan binding is invalid"
        )
    checkpoint = load_checkpoint_receipt(run_dir, required=False)
    return {
        "status": "authorized",
        "action": "wave-plan",
        "outer_project_root": str(
            Path(
                required_string(workspace, "source_root", "workspace manifest")
            ).resolve()
        ),
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        "checkpoint_head": (
            str(checkpoint["initial_head"]) if checkpoint is not None else None
        ),
    }


def authorize_task_commit_lifecycle(
    manifest_path: Path, run_id: str, command: str
) -> dict[str, object]:
    """Attest one exact delegated worker commit command to the lifecycle hook."""

    if not command or any(character in command for character in ";|<>`\n"):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker commit command is not canonical"
        )
    workspace = verify_workspace(manifest_path)
    run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
    _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
    if wave["status"] != "running":
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker commit requires an active running wave"
        )
    try:
        tokens = shlex.split(command)
    except ValueError as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker commit command is malformed"
        ) from error
    helper = (
        Path(__file__).resolve().parents[2]
        / "commit"
        / "scripts"
        / "commit_transaction.py"
    )
    if not _trusted_python_command(tokens, helper) or tokens[2] not in {
        "prepare",
        "execute",
        "review",
    }:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker commit command owner is invalid"
        )
    action = tokens[2]
    flags = _exact_command_flags(
        tokens,
        boolean_flags={"--allow-default-branch"} if action == "prepare" else set(),
    )
    required = {
        "prepare": {"--repo-root", "--session-id", "--authorization", "--claim"},
        "execute": {
            "--repo-root",
            "--session-id",
            "--claim",
            "--token",
            "--reviewed-tree",
            "--message",
        },
        "review": {
            "--repo-root",
            "--session-id",
            "--claim",
            "--token",
            "--reviewed-commit",
            "--reviewed-tree",
        },
    }[action]
    allowed = required | ({"--allow-default-branch"} if action == "prepare" else set())
    if flags is None or not required.issubset(flags) or set(flags) - allowed:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker commit command flags are invalid"
        )
    repo_value = flags.get("--repo-root")
    raw_session = flags.get("--session-id")
    if not isinstance(repo_value, str) or not isinstance(raw_session, str):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker commit identity is incomplete"
        )
    worker_root = Path(repo_value).expanduser()
    if not worker_root.is_absolute():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker commit root must be absolute"
        )
    session_sha256 = hashlib.sha256(raw_session.encode()).hexdigest()
    active_batch_index = wave.get("active_batch_index")
    if not isinstance(active_batch_index, int):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "wave has no active capacity batch"
        )
    active_task_ids = wave["batches"][active_batch_index]
    matches: list[tuple[dict[str, object], Path]] = []
    for task_id in active_task_ids:
        assignment = _validated_assignment(
            _assignment_path(run_dir, str(wave["wave_id"]), str(task_id))
        )
        _validate_assignment_context(
            assignment, workspace, coordinator, run_dir, wave, str(task_id)
        )
        plane_path = _task_plane_path(run_dir, str(wave["wave_id"]), str(task_id))
        plane = _load_task_plane(run_dir, str(wave["wave_id"]), str(task_id))
        if (
            Path(str(assignment["worktree"])).resolve(strict=False)
            == worker_root.resolve(strict=False)
            and plane["state"] == "running"
            and plane["worker_session_sha256"] == session_sha256
            and plane["assignment_sha256"] == assignment["assignment_sha256"]
        ):
            matches.append((assignment, plane_path))
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "worker commit does not belong to one active assigned task",
        )
    assignment, plane_path = matches[0]
    _verify_linked_worktree(
        Path(required_string(workspace, "repo_root", "workspace manifest")),
        worker_root,
        required_string(assignment, "branch", "worker assignment"),
    )
    authorization_path, claim_path = _task_commit_paths(worker_root, raw_session)
    expected_path = authorization_path if action == "prepare" else claim_path
    evidence_flag = "--authorization" if action == "prepare" else "--claim"
    if (
        not _exact_absolute_path(flags.get(evidence_flag), expected_path)
        or not _exact_absolute_path(flags.get("--claim"), claim_path)
        or (action == "prepare" and bool(flags.get("--allow-default-branch", False)))
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker commit evidence paths are invalid"
        )
    evidence = load_json_object(expected_path, "worker commit evidence")
    owner_field = "owner" if action == "prepare" else "authorization_owner"
    if (
        evidence.get(owner_field) != "task-implementer"
        or evidence.get("repo_root") != str(worker_root.resolve())
        or evidence.get("worktree") != str(worker_root.resolve())
        or evidence.get("session_sha256") != session_sha256
        or evidence.get("turn_sha256") != assignment["assignment_sha256"]
        or evidence.get("owner_evidence_path") != str(plane_path.resolve())
        or evidence.get("owner_evidence_sha256") != assignment["assignment_sha256"]
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker commit evidence identity is stale"
        )
    outer_project = Path(
        required_string(workspace, "source_root", "workspace manifest")
    ).resolve()
    return {
        "status": "authorized",
        "action": action,
        "outer_project_root": str(outer_project),
        "worker_root": str(worker_root.resolve()),
        "worker_session_id": raw_session,
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
    }


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


def _direct_regular_file(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", f"{label} is missing"
        ) from error
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise PromptWorkspaceError("WORKTREE_CONFLICT", f"{label} is unsafe")


def _registered_worktree_admin(repo: Path, worktree: Path) -> Path:
    admin_root = Path(
        _git_text(
            repo,
            ["rev-parse", "--path-format=absolute", "--git-path", "worktrees"],
            "locate linked worktree registrations",
        )
    )
    if not admin_root.is_dir() or admin_root.is_symlink():
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "linked worktree registration root is unsafe"
        )
    expected_gitfile = Path(os.path.abspath(worktree)) / ".git"
    matches: list[Path] = []
    for candidate in admin_root.iterdir():
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        gitdir = candidate / "gitdir"
        try:
            _direct_regular_file(gitdir, "linked worktree gitdir")
            value = Path(gitdir.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeError):
            continue
        if value.is_absolute() and Path(os.path.abspath(value)) == expected_gitfile:
            matches.append(candidate)
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "linked worktree registration is missing or ambiguous"
        )
    return matches[0]


def _recover_registered_worktree(
    repo: Path,
    path: Path,
    branch: str,
    allowed_heads: set[str],
    journal: Path,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    """Rehydrate one missing exact locked registration without discarding evidence."""

    _ref_valid(repo, branch)
    if path.exists() or path.is_symlink():
        _verify_linked_worktree(repo, path, branch)
        observed = _head(path)
        if observed not in allowed_heads:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "managed worktree HEAD is outside recovery proof"
            )
        return {"restored": False, "head": observed}
    record = _registered_worktrees(repo).get(path.resolve())
    if (
        record is None
        or record.get("branch") != f"refs/heads/{branch}"
        or "locked" not in record
        or record.get("HEAD") not in allowed_heads
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "missing worktree registration identity changed"
        )
    observed = str(record["HEAD"])
    branch_head = _git_text(
        repo,
        ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
        "read retained worktree branch",
    )
    if branch_head != observed:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "retained worktree branch and registration differ"
        )
    admin = _registered_worktree_admin(repo, path)
    head_path = admin / "HEAD"
    index_path = admin / "index"
    _direct_regular_file(head_path, "linked worktree HEAD")
    _direct_regular_file(index_path, "linked worktree index")
    if head_path.read_text(encoding="utf-8").strip() != f"ref: refs/heads/{branch}":
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "linked worktree administrative HEAD changed"
        )
    if (admin / "index.lock").exists() or (admin / "index.lock").is_symlink():
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "linked worktree index is locked"
        )
    staged = _git(
        repo,
        [
            f"--git-dir={admin}",
            "diff",
            "--cached",
            "--quiet",
            "--no-ext-diff",
        ],
        "verify retained worktree index",
        check=False,
    )
    if staged.returncode != 0:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "retained worktree index contains uncommitted state"
        )
    ensure_private_dir(path.parent)
    _journaled_git(
        journal,
        repo,
        [
            "worktree",
            "add",
            "--force",
            "--force",
            "--lock",
            "--reason",
            "task-implementer managed recovery",
            str(path),
            branch,
        ],
        "rehydrate a missing managed worktree",
        clock,
    )
    _verify_linked_worktree(repo, path, branch, expected_head=observed)
    if not _clean(path):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "rehydrated managed worktree is not clean"
        )
    return {"restored": True, "head": observed}


def recover_wave_resources(
    manifest_path: Path,
    run_id: str,
    *,
    confirmed_stopped: bool,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Rehydrate missing active-wave paths without changing workflow state."""

    if not confirmed_stopped:
        raise PromptWorkspaceError(
            "RECOVERY_CONFIRMATION_REQUIRED",
            "wave resource recovery requires confirmation that prior workers stopped",
        )
    workspace = verify_workspace(manifest_path)
    source_root = Path(required_string(workspace, "source_root", "workspace manifest"))
    if Path.cwd().resolve() != source_root.resolve():
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "resource recovery must run from the owning scope"
        )
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        status = required_string(wave, "status", "wave state")
        active_index = wave.get("active_batch_index")
        if status == "running":
            if not isinstance(active_index, int):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "running wave has no active capacity batch",
                )
            integration_head = required_string(wave, "contract_commit", "wave state")
        elif status == "promotion_pending":
            if active_index is not None:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "promotion-pending wave still has an active capacity batch",
                )
            if any(
                wave["task_states"].get(task_id) != "merged"
                for task_id in wave["task_ids"]
            ):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "promotion-pending wave tasks are not all merged",
                )
            integration_head = required_string(wave, "integrated_head", "wave state")
        else:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "resource recovery requires a running or promotion-pending wave",
            )
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        expected: list[dict[str, object]] = [
            {
                "kind": "integration",
                "path": Path(str(wave["integration_worktree"])),
                "branch": required_string(wave, "integration_branch", "wave state"),
                "allowed_heads": {integration_head},
                "task_id": None,
            }
        ]
        active_tasks = (
            wave["batches"][active_index] if isinstance(active_index, int) else []
        )
        for task_id in active_tasks:
            state = wave["task_states"].get(task_id)
            if state not in {"assigned", "running"}:
                continue
            assignment = _validated_assignment(
                _assignment_path(run_dir, str(wave["wave_id"]), str(task_id))
            )
            _validate_assignment_context(
                assignment, workspace, coordinator, run_dir, wave, str(task_id)
            )
            plane = _load_task_plane(run_dir, str(wave["wave_id"]), str(task_id))
            if plane["state"] != state or plane["commit"] is not None:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "recoverable task plane identity changed"
                )
            base = required_string(assignment, "base_commit", "worker assignment")
            allowed_heads = {base}
            branch = required_string(assignment, "branch", "worker assignment")
            if state == "running":
                branch_head = _git_text(
                    repo,
                    ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
                    "read recoverable worker branch",
                )
                if branch_head != base:
                    if (
                        _git_text(
                            repo,
                            ["rev-list", "--count", f"{base}..{branch_head}"],
                            "count recoverable worker commits",
                        )
                        != "1"
                        or _git_text(
                            repo,
                            ["rev-parse", f"{branch_head}^"],
                            "inspect recoverable worker parent",
                        )
                        != base
                    ):
                        raise PromptWorkspaceError(
                            "COMMIT_CONTRACT_INVALID",
                            "recovery branch must be at its base or one direct child",
                        )
                    allowed_heads.add(branch_head)
            expected.append(
                {
                    "kind": "worker",
                    "path": Path(
                        required_string(assignment, "worktree", "worker assignment")
                    ),
                    "branch": branch,
                    "allowed_heads": allowed_heads,
                    "task_id": task_id,
                }
            )
        leased = inspect_active_resources(workspace, run_dir)
        journal = _journal_path(run_dir, str(wave["wave_id"]))
        recovered: list[dict[str, object]] = []
        for resource in expected:
            path = resource["path"]
            branch = resource["branch"]
            kind = resource["kind"]
            assert isinstance(path, Path) and isinstance(branch, str)
            matches = [
                item
                for item in leased
                if item
                == {
                    "kind": kind,
                    "path": str(path.absolute()),
                    "branch": branch,
                    "state": "present",
                }
            ]
            if len(matches) != 1:
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT", "active lease resource identity changed"
                )
            result = _recover_registered_worktree(
                repo,
                path,
                branch,
                set(resource["allowed_heads"]),
                journal,
                clock,
            )
            record_resource(
                workspace,
                run_dir,
                kind=str(kind),
                path=path,
                branch=branch,
                state="present",
            )
            recovered.append(
                {
                    "kind": kind,
                    "task_id": resource["task_id"],
                    "path": str(path),
                    "branch": branch,
                    "head": result["head"],
                    "restored": result["restored"],
                    "filesystem_only_state_lost": bool(result["restored"]),
                    "uncommitted_state_lost": bool(result["restored"])
                    and kind == "worker",
                }
            )
        return {
            "status": "RESOURCES_RECOVERED",
            "run_id": run_id,
            "wave_id": wave["wave_id"],
            "resources": recovered,
            "task_state_changed": False,
            "promotion_inferred": False,
        }


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
        run_state = verify_run(workspace, run_id, None)
        if run_state["steering_pending"]:
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED",
                "pending steering must settle before preparation",
            )
        verify_requirements_refinement_contract(workspace, run_dir, run_state)
        verify_prompt_impact_plan(
            run_dir,
            coordinator,
            Path(required_string(workspace, "source_root", "workspace manifest")),
        )
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        if wave["status"] not in {"planned", "preparing"}:
            return wave
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        adopted_contract_head = active_contract_delta_head(
            workspace, run_dir, coordinator, wave
        )
        adopted_contract = adopted_contract_head is not None
        if wave["status"] == "preparing" and wave.get("integrated_head") is not None:
            if wave.get("contract_commit") not in {
                wave.get("integrated_head"),
                adopted_contract_head,
            }:
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT",
                    "promotion review correction contract identity changed",
                )
            integration = Path(str(wave["integration_worktree"]))
            _verify_linked_worktree(
                repo,
                integration,
                str(wave["integration_branch"]),
                expected_head=str(wave["contract_commit"]),
            )
            if (
                _branch(repo) != coordinator["base_branch"]
                or _head(repo) != wave["base_commit"]
                or (not _clean(repo) and not adopted_contract)
                or _branch(integration) != wave["integration_branch"]
                or _head(integration) != wave["contract_commit"]
                or not _clean(integration)
            ):
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT",
                    "promotion review correction resources changed before dispatch",
                )
            return wave
        if _branch(repo) != coordinator["base_branch"] or not _clean(repo):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "persistent lane branch or cleanliness changed"
            )
        expected_base = _expected_primary_head(
            run_dir, coordinator, str(wave["wave_id"])
        )
        if _head(repo) != expected_base:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "persistent lane moved before wave preparation"
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
        record_resource(
            workspace,
            run_dir,
            kind="integration",
            path=path,
            branch=branch,
            state="planned",
        )
        _ensure_worktree(
            repo,
            path,
            branch,
            str(expected_base),
            _journal_path(run_dir, str(wave["wave_id"])),
            clock,
            allow_descendant=True,
        )
        record_resource(
            workspace,
            run_dir,
            kind="integration",
            path=path,
            branch=branch,
            state="present",
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
        _verify_linked_worktree(
            Path(required_string(workspace, "repo_root", "workspace manifest")),
            integration,
            str(wave["integration_branch"]),
            expected_head=contract_commit,
        )
        if _head(integration) != contract_commit or not _clean(integration):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "integration contract commit is not clean and exact",
            )
        scope = required_string(workspace, "scope", "workspace manifest")
        contract_project = integration if scope == "." else integration / scope
        verify_project_agent_contract(
            workspace, run_dir, contract_project, contract_commit
        )
        if wave["status"] == "running":
            if wave["contract_commit"] != contract_commit:
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT", "running wave contract commit changed"
                )
            active_index = wave.get("active_batch_index")
            if not isinstance(active_index, int):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "wave has no active capacity batch"
                )
            active_task_ids = set(wave["batches"][active_index])
            tasks = [
                task
                for task in _wave_plan(coordinator, str(wave["wave_id"]))
                if task["task_id"] in active_task_ids
            ]
            assignments: list[str] = []
            for task in tasks:
                task_id = str(task["task_id"])
                target = _assignment_path(run_dir, str(wave["wave_id"]), task_id)
                assignment = _validated_assignment(target)
                _validate_assignment_context(
                    assignment, workspace, coordinator, run_dir, wave, task_id
                )
                _validate_assignment_handoff(
                    assignment,
                    run_dir,
                    coordinator,
                    str(wave["wave_id"]),
                    task,
                )
                assignments.append(str(target))
            return {"wave": wave, "assignments": assignments}
        adopted_contract_head = active_contract_delta_head(
            workspace, run_dir, coordinator, wave
        )
        if wave.get("integrated_head") is not None:
            if wave.get(
                "contract_commit"
            ) != contract_commit or contract_commit not in {
                wave.get("integrated_head"),
                adopted_contract_head,
            }:
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT",
                    "promotion review correction contract identity changed",
                )
            if any(
                state not in {"merged", "committed", "planned", "superseded"}
                for state in wave["task_states"].values()
            ) or not any(state == "planned" for state in wave["task_states"].values()):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "promotion review correction task state is invalid",
                )
        else:
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
                    "WORKTREE_CONFLICT",
                    "contract commit does not descend from wave base",
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
        if wave.get("active_batch_index") is None:
            try:
                next_batch = wave["batch_states"].index("planned")
            except ValueError as error:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "wave has no planned capacity batch"
                ) from error
            wave["active_batch_index"] = next_batch
            wave["batch_states"][next_batch] = "active"
        active_batch_index = int(wave["active_batch_index"])
        active_task_ids = set(wave["batches"][active_batch_index])
        tasks = [
            task
            for task in _wave_plan(coordinator, str(wave["wave_id"]))
            if task["task_id"] in active_task_ids
        ]
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        for superseded_id, state in wave["task_states"].items():
            if state != "superseded":
                continue
            superseded_assignment = _validated_assignment(
                _assignment_path(run_dir, str(wave["wave_id"]), str(superseded_id))
            )
            superseded_base = required_string(
                superseded_assignment, "base_commit", "worker assignment"
            )
            if not _cleanup_resource(
                workspace=workspace,
                run_dir=run_dir,
                wave=wave,
                repo=repo,
                kind="worker",
                worktree=Path(
                    required_string(
                        superseded_assignment, "worktree", "worker assignment"
                    )
                ),
                branch=str(superseded_assignment["branch"]),
                expected_tip=superseded_base,
                reachable_tip=contract_commit,
                clock=clock,
            ):
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT",
                    "superseded worker resource could not be cleaned",
                )
        wave_root = Path(str(wave["integration_worktree"])).parent
        for task in tasks:
            _reject_special_claims(repo, task)
            task_id = str(task["task_id"])
            branch = _temporary_branch(workspace, run_id, str(wave["wave_id"]), task_id)
            worktree = wave_root / task_id
            if not os.path.lexists(worktree):
                record_resource(
                    workspace,
                    run_dir,
                    kind="worker",
                    path=worktree,
                    branch=branch,
                    state="planned",
                )
            _ensure_worktree(
                repo,
                worktree,
                branch,
                contract_commit,
                _journal_path(run_dir, str(wave["wave_id"])),
                clock,
            )
            record_resource(
                workspace,
                run_dir,
                kind="worker",
                path=worktree,
                branch=branch,
                state="present",
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
            handoff_path = _incoming_handoff_path(
                run_dir, str(wave["wave_id"]), task_id
            )
            existing_handoff = (
                _validated_incoming_handoff(handoff_path)
                if handoff_path.exists()
                else None
            )
            created_at = (
                str(existing_assignment["created_at"])
                if existing_assignment is not None
                else _utc(clock)
            )
            incoming_handoff = _build_incoming_handoff(
                run_dir,
                coordinator,
                str(wave["wave_id"]),
                task,
                contract_commit,
                str(existing_handoff["created_at"])
                if existing_handoff is not None
                else created_at,
            )
            ensure_private_dir(handoff_path.parent)
            if existing_handoff is not None:
                if existing_handoff != incoming_handoff:
                    raise PromptWorkspaceError(
                        "EXECUTION_STATE_INVALID", "immutable incoming handoff differs"
                    )
            else:
                write_exclusive(handoff_path, stable_json(incoming_handoff))
            assignment: dict[str, object] = {
                "schema": ASSIGNMENT_SCHEMA,
                "run_id": run_id,
                "wave_id": wave["wave_id"],
                "task_id": task_id,
                "base_commit": contract_commit,
                "branch": branch,
                "worktree": str(worktree),
                "scope_cwd": str(scope_cwd),
                "workspace_manifest": str(manifest_path.expanduser().resolve()),
                "helper_path": str(
                    Path(__file__).resolve().with_name("prompt_workspace.py")
                ),
                "result_path": str(
                    _result_path(run_dir, str(wave["wave_id"]), task_id)
                ),
                "write_claims": task["write_claims"],
                "conflict_domains": task["conflict_domains"],
                "requirement_ids": task["requirement_ids"],
                "design_id": task["design_id"],
                "goal": task["goal"],
                "plan": task["plan"],
                "implementation_steps": task["implementation_steps"],
                "validation": task["validation"],
                "end_to_end_validation": task["end_to_end_validation"],
                "done_criteria": task["done_criteria"],
                "rollback_notes": task["rollback_notes"],
                "stop_conditions": task["stop_conditions"],
                "worker_guardrails": WORKER_GUARDRAILS,
                "start_seconds": WORKER_START_SECONDS,
                "heartbeat_seconds": WORKER_HEARTBEAT_SECONDS,
                **worker_liveness_profile(task["dependencies"]),
                "stall_seconds": WORKER_STALL_SECONDS,
                "max_worker_seconds": WORKER_MAX_SECONDS,
                "dependencies": task["dependencies"],
                "incoming_handoff_path": str(handoff_path),
                "incoming_handoff_sha256": incoming_handoff["handoff_sha256"],
                "plan_sha256": coordinator["plan_sha256"],
                "created_at": created_at,
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


def advance_batch(
    manifest_path: Path,
    run_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, _, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        active_index = wave.get("active_batch_index")
        if wave["status"] != "running" or not isinstance(active_index, int):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "wave has no active capacity batch"
            )
        if any(
            wave["task_states"][task_id] != "committed"
            for task_id in wave["batches"][active_index]
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "active capacity batch is incomplete"
            )
        wave["batch_states"][active_index] = "done"
        next_index = active_index + 1
        if next_index >= len(wave["batches"]):
            wave["active_batch_index"] = None
            wave["updated_at"] = _utc(clock)
            _save_wave(run_dir, wave)
            return {"wave": wave, "assignments": []}
        wave["active_batch_index"] = next_index
        wave["batch_states"][next_index] = "active"
        wave["status"] = "preparing"
        wave["updated_at"] = _utc(clock)
        contract_commit = required_string(wave, "contract_commit", "wave state")
        _save_wave(run_dir, wave)
    return dispatch_wave(manifest_path, run_id, contract_commit, clock=clock)


def _session_fingerprint(session_id: str | None = None) -> str:
    value = session_id if session_id is not None else os.environ.get("CODEX_THREAD_ID")
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise PromptWorkspaceError(
            "SESSION_ID_UNAVAILABLE", "worker session identifier is required"
        )
    return hashlib.sha256(value.encode()).hexdigest()


def _ensure_commit_authorization_parent(root: Path, parent: Path) -> None:
    try:
        relative = parent.relative_to(root)
    except ValueError as error:
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID",
            "commit authorization path escapes the private Codex root",
        ) from error
    ensure_private_dir(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID",
                "commit authorization directory must not be a symlink",
            )
        if not current.exists():
            current.mkdir(mode=0o700)
        metadata = current.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or current.resolve(strict=True) != current
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID",
                "commit authorization directory is unsafe",
            )
        current.chmod(0o700)


def _task_commit_paths(worktree: Path, session_id: str) -> tuple[Path, Path]:
    common_value = Path(
        _git_text(
            worktree,
            ["rev-parse", "--git-common-dir"],
            "read the worker Git common directory",
        )
    )
    if not common_value.is_absolute():
        common_value = worktree / common_value
    common = common_value.resolve(strict=True)
    reference = _git_text(
        worktree, ["symbolic-ref", "-q", "HEAD"], "read the worker source ref"
    )
    repo_key = hashlib.sha256(str(common).encode()).hexdigest()[:24]
    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    codex_home = Path(
        os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    if not codex_home.is_absolute():
        raise PromptWorkspaceError("ENVIRONMENT_BLOCKER", "CODEX_HOME must be absolute")
    private_root = codex_home.resolve(strict=False)
    path = (
        private_root
        / "commit-transactions"
        / repo_key
        / "sessions"
        / session_key
        / "authorization.json"
    )
    claim_path = (
        private_root
        / "commit-transactions"
        / repo_key
        / "claims"
        / f"{hashlib.sha256(reference.encode()).hexdigest()[:24]}.json"
    )
    return path, claim_path


def _task_commit_authorization(
    worktree: Path,
    assignment: dict[str, object],
    plane_path: Path,
    session_id: str,
) -> tuple[Path, Path]:
    path, claim_path = _task_commit_paths(worktree, session_id)
    common_value = Path(
        _git_text(
            worktree,
            ["rev-parse", "--git-common-dir"],
            "read the worker Git common directory",
        )
    )
    if not common_value.is_absolute():
        common_value = worktree / common_value
    common = common_value.resolve(strict=True)
    reference = _git_text(
        worktree, ["symbolic-ref", "-q", "HEAD"], "read the worker source ref"
    )
    private_root = Path(
        os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    if not private_root.is_absolute():
        raise PromptWorkspaceError("ENVIRONMENT_BLOCKER", "CODEX_HOME must be absolute")
    private_root = private_root.resolve(strict=False)
    assignment_sha256 = required_string(
        assignment, "assignment_sha256", "worker assignment"
    )
    authorization: dict[str, object] = {
        "schema": "commit-transaction.authorization.v1",
        "state": "AUTHORIZED",
        "repo_root": str(worktree.resolve()),
        "worktree": str(worktree.resolve()),
        "common_dir": str(common),
        "ref": reference,
        "base_head": required_string(assignment, "base_commit", "worker assignment"),
        "session_sha256": hashlib.sha256(session_id.encode()).hexdigest(),
        "turn_sha256": assignment_sha256,
        "prompt_sha256": assignment_sha256,
        "owner": "task-implementer",
        "owner_evidence_path": str(plane_path.resolve()),
        "owner_evidence_sha256": assignment_sha256,
        "allow_default_branch": False,
    }
    _ensure_commit_authorization_parent(private_root, path.parent)
    write_atomic(path, stable_json(authorization))
    return path, claim_path


def _task_commit_context(
    workspace: dict[str, object],
    worktree: Path,
    session_id: str,
    commit_paths: tuple[Path, Path] | None,
) -> dict[str, object] | None:
    """Return the transient, unambiguous worker commit invocation contract."""

    if commit_paths is None:
        return None
    authorization, claim = commit_paths
    executable = Path(sys.executable).resolve(strict=True)
    helper = (
        Path(__file__).resolve().parents[2]
        / "commit"
        / "scripts"
        / "commit_transaction.py"
    ).resolve(strict=True)
    repo_root = worktree.resolve(strict=True)
    lifecycle_cwd = Path(
        required_string(workspace, "source_root", "workspace manifest")
    ).resolve(strict=True)
    prepare_argv = [
        str(executable),
        str(helper),
        "prepare",
        "--repo-root",
        str(repo_root),
        "--session-id",
        session_id,
        "--authorization",
        str(authorization),
        "--claim",
        str(claim),
    ]
    return {
        "schema": "task-implementer/worker-commit-context-v1",
        "python_executable": str(executable),
        "helper_path": str(helper),
        "repo_root": str(repo_root),
        "lifecycle_cwd": str(lifecycle_cwd),
        "session_id": session_id,
        "session_id_source": "CODEX_THREAD_ID",
        "authorization": str(authorization),
        "claim": str(claim),
        "prepare_argv": prepare_argv,
    }


def _task_result_context(assignment: dict[str, object]) -> dict[str, object]:
    """Return the exact private publication boundary without changing cwd."""

    result_path = Path(required_string(assignment, "result_path", "worker assignment"))
    if not result_path.is_absolute():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker result path must be absolute"
        )
    ensure_private_dir(result_path.parent)
    orchestration = result_path.parents[2]
    assignment_path = (
        orchestration / "assignments" / result_path.parent.name / result_path.name
    )
    draft_path = result_path.with_suffix(".draft.json")
    executable = Path(sys.executable).resolve(strict=True)
    helper = Path(__file__).resolve().with_name("prompt_workspace.py")
    return {
        "schema": "task-implementer/worker-result-context-v1",
        "result_path": str(result_path),
        "publication_cwd": str(result_path.parent),
        "draft_path": str(draft_path),
        "publish_argv": [
            str(executable),
            str(helper),
            "task-result-publish",
            "--assignment",
            str(assignment_path),
            "--draft",
            str(draft_path),
            "--result",
            str(result_path),
        ],
    }


def publish_task_result(
    assignment_path: Path,
    draft_path: Path,
    result_path: Path,
) -> dict[str, object]:
    """Validate, digest, and atomically publish one private worker result."""

    if not all(
        path.is_absolute() for path in (assignment_path, draft_path, result_path)
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "worker result publication paths must be absolute",
        )
    if Path.cwd().resolve() != result_path.parent.resolve():
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT",
            "worker result publication must run from its private publication cwd",
        )
    assignment = _validated_assignment(assignment_path)
    expected_result = Path(
        required_string(assignment, "result_path", "worker assignment")
    )
    expected_assignment = (
        expected_result.parents[2]
        / "assignments"
        / expected_result.parent.name
        / expected_result.name
    )
    if (
        result_path != expected_result
        or assignment_path != expected_assignment
        or draft_path != result_path.with_suffix(".draft.json")
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker result publication identity differs"
        )
    draft = load_json_object(draft_path, "worker result draft")
    required = {
        "schema",
        "run_id",
        "wave_id",
        "task_id",
        "assignment_sha256",
        "status",
        "commit",
        "changed_paths",
        "summary",
        "decisions",
        "open_risks",
        "validation",
        "end_to_end_validation",
        "code_review",
        "completed_at",
    }
    if (
        set(draft) != required
        or draft.get("schema") != RESULT_SCHEMA
        or draft.get("run_id") != assignment.get("run_id")
        or draft.get("wave_id") != assignment.get("wave_id")
        or draft.get("task_id") != assignment.get("task_id")
        or draft.get("assignment_sha256") != assignment.get("assignment_sha256")
        or not isinstance(draft.get("changed_paths"), list)
        or not all(isinstance(path, str) and path for path in draft["changed_paths"])
        or not isinstance(draft.get("decisions"), list)
        or not isinstance(draft.get("open_risks"), list)
        or any(
            not isinstance(item, str) or not item.strip()
            for item in [*draft["decisions"], *draft["open_risks"]]
        )
        or any(
            not isinstance(draft.get(field), str) or not str(draft[field]).strip()
            for field in (
                "status",
                "summary",
                "validation",
                "end_to_end_validation",
                "code_review",
                "completed_at",
            )
        )
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker result draft is invalid"
        )
    changed_paths = list(draft["changed_paths"])
    if len(set(changed_paths)) != len(changed_paths):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker result paths repeat"
        )
    unsigned_result = {**draft, "changed_paths": sorted(changed_paths)}
    result = {**unsigned_result, "result_sha256": sha256_json(unsigned_result)}
    if result_path.exists():
        if load_json_object(result_path, "worker result") != result:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "published worker result differs"
            )
    else:
        write_exclusive(result_path, stable_json(result))
    return {
        "status": "published",
        "result_path": str(result_path),
        "result_sha256": result["result_sha256"],
    }


def _session_was_used(
    run_dir: Path, worker_session: str, *, except_path: Path | None = None
) -> bool:
    tasks_root = orchestration_dir(run_dir) / "tasks"
    for candidate in sorted(tasks_root.glob("*/*.json")):
        if except_path is not None and candidate == except_path:
            continue
        other = load_json_object(candidate, "task plane")
        history = other.get("worker_session_sha256_history", [])
        if worker_session == other.get("worker_session_sha256") or (
            isinstance(history, list) and worker_session in history
        ):
            return True
    return False


def _task_start_context(
    assignment_path: Path,
    assignment: dict[str, object],
    start_lease: str,
) -> dict[str, object]:
    """Return one exact assignment-derived worker launch command and cwd."""

    helper = Path(required_string(assignment, "helper_path", "worker assignment"))
    workspace_manifest = Path(
        required_string(assignment, "workspace_manifest", "worker assignment")
    )
    scope_cwd = Path(required_string(assignment, "scope_cwd", "worker assignment"))
    worktree = Path(required_string(assignment, "worktree", "worker assignment"))
    if not all(
        path.is_absolute()
        for path in (
            assignment_path,
            helper,
            workspace_manifest,
            scope_cwd,
            worktree,
        )
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker launch paths must be absolute"
        )
    executable = Path(sys.executable).resolve(strict=True)
    return {
        "schema": "task-implementer/worker-start-context-v1",
        "assignment_path": str(assignment_path),
        "scope_cwd": str(scope_cwd),
        "worktree": str(worktree),
        "start_argv": [
            str(executable),
            str(helper),
            "task-start",
            "--workspace",
            str(workspace_manifest),
            "--run-id",
            required_string(assignment, "run_id", "worker assignment"),
            "--task-id",
            required_string(assignment, "task_id", "worker assignment"),
            "--assignment-sha256",
            required_string(assignment, "assignment_sha256", "worker assignment"),
            "--start-lease",
            start_lease,
            "--json",
        ],
    }


def arm_task(
    manifest_path: Path,
    run_id: str,
    task_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Start the pre-task-start deadline only when a worker slot is available."""

    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        assignment_path = _assignment_path(run_dir, str(wave["wave_id"]), task_id)
        assignment = _validated_assignment(assignment_path)
        _validate_assignment_context(
            assignment, workspace, coordinator, run_dir, wave, task_id
        )
        active_index = wave.get("active_batch_index")
        if (
            not isinstance(active_index, int)
            or task_id not in wave["batches"][active_index]
            or wave["batch_states"][active_index] != "active"
            or wave["status"] != "running"
            or wave["task_states"].get(task_id) != "assigned"
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task is not queued in the active batch"
            )
        plane = _load_task_plane(run_dir, str(wave["wave_id"]), task_id)
        if (
            plane["state"] != "assigned"
            or plane["assignment_sha256"] != assignment["assignment_sha256"]
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task plane is not assignable"
            )
        if plane["dispatched_at"] is None:
            worktree = Path(
                required_string(assignment, "worktree", "worker assignment")
            )
            _verify_linked_worktree(
                Path(required_string(workspace, "repo_root", "workspace manifest")),
                worktree,
                str(assignment["branch"]),
                expected_head=str(assignment["base_commit"]),
            )
            if not _clean(worktree):
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT", "queued worker worktree is not clean"
                )
            dispatched_at = _utc(clock)
            plane["dispatched_at"] = dispatched_at
            plane["updated_at"] = dispatched_at
            _save_task_plane(run_dir, plane)
        start_lease = required_string(plane, "dispatched_at", "task plane")
        return {
            "status": "ARMED",
            "task_id": task_id,
            "assignment_sha256": assignment["assignment_sha256"],
            "start_lease": start_lease,
            "start_context": _task_start_context(
                assignment_path, assignment, start_lease
            ),
        }


def rearm_task(
    manifest_path: Path,
    run_id: str,
    task_id: str,
    expected_dispatched_at: str,
    *,
    confirmed_stopped: bool,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Replace one expired clean prestart lease after the old worker stopped."""

    if not confirmed_stopped:
        raise PromptWorkspaceError(
            "RECOVERY_CONFIRMATION_REQUIRED",
            "task rearm requires confirmation that the previous worker stopped",
        )
    _time_value(expected_dispatched_at, "expected worker dispatch time")
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        assignment_path = _assignment_path(run_dir, str(wave["wave_id"]), task_id)
        assignment = _validated_assignment(assignment_path)
        _validate_assignment_context(
            assignment, workspace, coordinator, run_dir, wave, task_id
        )
        if (
            wave["status"] != "running"
            or wave["task_states"].get(task_id) != "assigned"
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task rearm requires one assigned task"
            )
        plane = _load_task_plane(run_dir, str(wave["wave_id"]), task_id)
        if plane["state"] != "assigned":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task plane is not assigned"
            )
        if plane["dispatched_at"] is None:
            raise PromptWorkspaceError(
                "TASK_NOT_ARMED", "task rearm requires an armed worker deadline"
            )
        if (
            plane["worker_session_sha256"] is not None
            or plane["worker_session_sha256_history"]
            or plane["started_at"] is not None
            or plane["last_heartbeat_at"] is not None
            or plane["heartbeat_sequence"] != 0
            or plane["heartbeat_phase"] is not None
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "assigned prestart task already contains worker-owned state",
            )
        worktree = Path(required_string(assignment, "worktree", "worker assignment"))
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        _verify_linked_worktree(repo, worktree, str(assignment["branch"]))
        base = required_string(assignment, "base_commit", "worker assignment")
        observed = _head(worktree)
        changed = sorted(set(_dirty_paths(worktree)))
        if observed != base:
            changed = sorted(
                set(changed) | set(_changed_paths(worktree, base, observed))
            )
        if observed != base or changed:
            raise PromptWorkspaceError(
                "WORKER_PRESTART_MUTATION",
                "task rearm requires the exact clean assigned base",
            )
        rearmed_at = _utc(clock)
        now = _time_value(rearmed_at, "worker rearm time")
        current_dispatch = _time_value(plane["dispatched_at"], "worker dispatch time")
        if plane["dispatched_at"] != expected_dispatched_at:
            replay = _active_resume_arguments(run_dir, "task-rearm")
            if replay == {
                "task_id": task_id,
                "expected_start_lease": expected_dispatched_at,
                "confirmed_stopped": True,
            }:
                replayed_lease = required_string(plane, "dispatched_at", "task plane")
                return {
                    "status": "REARMED",
                    "task_id": task_id,
                    "assignment_sha256": assignment["assignment_sha256"],
                    "start_lease": replayed_lease,
                    "start_context": _task_start_context(
                        assignment_path, assignment, replayed_lease
                    ),
                }
            raise PromptWorkspaceError(
                "WORKER_START_LEASE_CONFLICT",
                "task rearm expected a different prestart lease",
            )
        if (now - current_dispatch).total_seconds() < WORKER_START_SECONDS:
            raise PromptWorkspaceError(
                "WORKER_PRESTART_ACTIVE",
                "task rearm requires the task-start deadline to expire",
            )
        plane["dispatched_at"] = rearmed_at
        plane["updated_at"] = rearmed_at
        _save_task_plane(run_dir, plane)
        return {
            "status": "REARMED",
            "task_id": task_id,
            "assignment_sha256": assignment["assignment_sha256"],
            "start_lease": rearmed_at,
            "start_context": _task_start_context(
                assignment_path, assignment, rearmed_at
            ),
        }


def start_task(
    manifest_path: Path,
    run_id: str,
    task_id: str,
    assignment_sha256: str,
    dispatched_at: str,
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
        task = next(
            item
            for item in _wave_plan(coordinator, str(wave["wave_id"]))
            if item["task_id"] == task_id
        )
        active_index = wave.get("active_batch_index")
        if (
            not isinstance(active_index, int)
            or task_id not in wave["batches"][active_index]
            or wave["batch_states"][active_index] != "active"
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task is outside the active capacity batch"
            )
        _validate_assignment_handoff(
            assignment,
            run_dir,
            coordinator,
            str(wave["wave_id"]),
            task,
        )
        if (
            wave["status"] != "running"
            or wave["task_states"].get(task_id) != "assigned"
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task-start requires one queued task"
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
        if wave["task_states"].get(task_id) != "assigned":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task is not assignable"
            )
        raw_session = (
            session_id if session_id is not None else os.environ.get("CODEX_THREAD_ID")
        )
        if not isinstance(raw_session, str) or not raw_session.strip():
            raise PromptWorkspaceError(
                "SESSION_ID_UNAVAILABLE", "worker session identifier is required"
            )
        result_context = _task_result_context(assignment)
        worker_session = _session_fingerprint(raw_session)
        plane = _load_task_plane(run_dir, str(wave["wave_id"]), task_id)
        if plane["state"] != "assigned":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task-start is single-use"
            )
        if plane["assignment_sha256"] != assignment_sha256:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task plane assignment differs"
            )
        if plane["dispatched_at"] is None:
            raise PromptWorkspaceError(
                "TASK_NOT_ARMED", "coordinator must arm a worker slot before task-start"
            )
        if plane["dispatched_at"] != dispatched_at:
            raise PromptWorkspaceError(
                "WORKER_START_LEASE_INVALID",
                "worker task-start lease is stale or belongs to another launch",
            )
        started_at = _utc(clock)
        prestart_elapsed = (
            _time_value(started_at, "worker start time")
            - _time_value(plane["dispatched_at"], "worker dispatch time")
        ).total_seconds()
        if prestart_elapsed >= WORKER_START_SECONDS:
            raise PromptWorkspaceError(
                "WORKER_PRESTART_TIMEOUT", "worker missed the task-start deadline"
            )
        if plane["worker_session_sha256"] not in {None, worker_session}:
            raise PromptWorkspaceError(
                "WORKSPACE_BUSY", "task is owned by another worker session"
            )
        plane_path = _task_plane_path(run_dir, str(wave["wave_id"]), task_id)
        if _session_was_used(run_dir, worker_session, except_path=plane_path):
            raise PromptWorkspaceError(
                "FRESH_SESSION_REQUIRED",
                "one worker session cannot own multiple task planes",
            )
        wave["task_states"][task_id] = "running"
        wave["updated_at"] = started_at
        _save_wave(run_dir, wave)
        plane["state"] = "running"
        plane["worker_session_sha256"] = worker_session
        if not plane["worker_session_sha256_history"]:
            plane["worker_session_sha256_history"].append(worker_session)
        plane["started_at"] = started_at
        plane["last_heartbeat_at"] = started_at
        plane["heartbeat_sequence"] = 1
        plane["heartbeat_phase"] = "preflight"
        plane["updated_at"] = started_at
        _save_task_plane(run_dir, plane)
        commit_paths = _task_commit_authorization(
            worktree, assignment, plane_path, raw_session
        )
        return {
            "assignment": assignment,
            "worker_session_fingerprint_sha256": worker_session,
            "commit_authorization": str(commit_paths[0]),
            "commit_claim": str(commit_paths[1]),
            "commit_context": _task_commit_context(
                workspace, worktree, raw_session, commit_paths
            ),
            "result_context": result_context,
        }


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


def _worker_guard_status(
    assignment: dict[str, object],
    plane: dict[str, object],
    *,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    now_text = _utc(clock)
    now = _time_value(now_text, "worker guard clock")
    started = _time_value(plane.get("started_at"), "worker start time")
    heartbeat = _time_value(plane.get("last_heartbeat_at"), "worker heartbeat time")
    read_only_seconds = int(assignment["read_only_seconds"])
    warning_seconds = int(assignment["read_only_warning_seconds"])
    elapsed = max(0, int((now - started).total_seconds()))
    heartbeat_age = max(0, int((now - heartbeat).total_seconds()))
    worktree = Path(required_string(assignment, "worktree", "worker assignment"))
    base = required_string(assignment, "base_commit", "worker assignment")
    observed_head = _head(worktree)
    observed_paths = set(_dirty_paths(worktree))
    if observed_head != base:
        observed_paths.update(_changed_paths(worktree, base, observed_head))
    scope_violation = any(
        not _path_allowed(path, assignment["write_claims"]) for path in observed_paths
    )
    progress_observed = bool(observed_paths) and not scope_violation
    if scope_violation:
        status = "WORKER_SCOPE_VIOLATION"
    elif elapsed >= WORKER_MAX_SECONDS:
        status = "WORKER_TIMEOUT"
    elif not progress_observed and elapsed >= read_only_seconds:
        status = "WORKER_READ_ONLY_TIMEOUT"
    elif heartbeat_age >= WORKER_STALL_SECONDS:
        status = "WORKER_STALLED"
    else:
        status = "ACTIVE"
    warning = (
        "READ_ONLY_DEADLINE_NEAR"
        if status == "ACTIVE" and not progress_observed and elapsed >= warning_seconds
        else None
    )
    return {
        "status": status,
        "warning": warning,
        "elapsed_seconds": elapsed,
        "heartbeat_age_seconds": heartbeat_age,
        "progress_observed": progress_observed,
        "scope_violation": scope_violation,
        "heartbeat_sequence": plane["heartbeat_sequence"],
        "heartbeat_phase": plane["heartbeat_phase"],
        "observed_at": now_text,
    }


def heartbeat_task(
    manifest_path: Path,
    run_id: str,
    task_id: str,
    assignment_sha256: str,
    phase: str,
    *,
    session_id: str | None = None,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    if phase not in WORKER_PHASES:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worker heartbeat phase is invalid"
        )
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        assignment = _validated_assignment(
            _assignment_path(run_dir, str(wave["wave_id"]), task_id)
        )
        _validate_assignment_context(
            assignment, workspace, coordinator, run_dir, wave, task_id
        )
        if assignment.get("assignment_sha256") != assignment_sha256:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "worker assignment digest is invalid"
            )
        plane = _load_task_plane(run_dir, str(wave["wave_id"]), task_id)
        if (
            plane["state"] != "running"
            or wave["task_states"].get(task_id) != "running"
            or plane["worker_session_sha256"] != _session_fingerprint(session_id)
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "worker heartbeat ownership is invalid"
            )
        scope_cwd = Path(required_string(assignment, "scope_cwd", "worker assignment"))
        if Path.cwd().resolve() != scope_cwd.resolve():
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "worker heartbeat must run from assigned scope cwd"
            )
        status = _worker_guard_status(assignment, plane, clock=clock)
        if status["status"] != "ACTIVE":
            raise PromptWorkspaceError(
                str(status["status"]), "worker liveness budget was exceeded"
            )
        plane["last_heartbeat_at"] = status["observed_at"]
        plane["heartbeat_sequence"] = int(plane["heartbeat_sequence"]) + 1
        plane["heartbeat_phase"] = phase
        plane["updated_at"] = status["observed_at"]
        _save_task_plane(run_dir, plane)
        status["heartbeat_sequence"] = plane["heartbeat_sequence"]
        status["heartbeat_phase"] = phase
        return status


def watch_task(
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
        assignment = _validated_assignment(
            _assignment_path(run_dir, str(wave["wave_id"]), task_id)
        )
        _validate_assignment_context(
            assignment, workspace, coordinator, run_dir, wave, task_id
        )
        plane = _load_task_plane(run_dir, str(wave["wave_id"]), task_id)
        state = plane["state"]
        if state == "assigned" and wave["task_states"].get(task_id) == "assigned":
            observed_at = _utc(clock)
            worktree = Path(
                required_string(assignment, "worktree", "worker assignment")
            )
            base = required_string(assignment, "base_commit", "worker assignment")
            observed_head = _head(worktree)
            observed_paths = set(_dirty_paths(worktree))
            if observed_head != base:
                observed_paths.update(_changed_paths(worktree, base, observed_head))
            progress_observed = observed_head != base or bool(observed_paths)
            scope_violation = any(
                not _path_allowed(path, assignment["write_claims"])
                for path in observed_paths
            )
            if progress_observed:
                status = "WORKER_PRESTART_MUTATION"
                elapsed = 0
            elif plane["dispatched_at"] is None:
                status = "QUEUED"
                elapsed = 0
            else:
                now = _time_value(observed_at, "worker guard clock")
                dispatched_at = _time_value(
                    plane["dispatched_at"], "worker dispatch time"
                )
                elapsed = max(0, int((now - dispatched_at).total_seconds()))
                status = (
                    "WORKER_PRESTART_TIMEOUT"
                    if elapsed >= WORKER_START_SECONDS
                    else "PENDING_START"
                )
            return {
                "status": status,
                "dispatched_at": plane["dispatched_at"],
                "warning": None,
                "elapsed_seconds": elapsed,
                "heartbeat_age_seconds": None,
                "progress_observed": progress_observed,
                "scope_violation": scope_violation,
                "heartbeat_sequence": 0,
                "heartbeat_phase": None,
                "observed_at": observed_at,
            }
        if state != "running" or wave["task_states"].get(task_id) != "running":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "only an assigned or running task can be watched",
            )
        return _worker_guard_status(assignment, plane, clock=clock)


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
        task = next(
            item
            for item in _wave_plan(coordinator, str(wave["wave_id"]))
            if item["task_id"] == task_id
        )
        _validate_assignment_handoff(
            assignment,
            run_dir,
            coordinator,
            str(wave["wave_id"]),
            task,
        )
        plane_path = _task_plane_path(run_dir, str(wave["wave_id"]), task_id)
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
        raw_session = (
            session_id if session_id is not None else os.environ.get("CODEX_THREAD_ID")
        )
        if not isinstance(raw_session, str) or not raw_session.strip():
            raise PromptWorkspaceError(
                "SESSION_ID_UNAVAILABLE", "worker session identifier is required"
            )
        result_context = _task_result_context(assignment)
        worker_session = _session_fingerprint(raw_session)
        if worker_session == plane["worker_session_sha256"]:
            replay = _active_resume_arguments(run_dir, "task-recover")
            if replay != {
                "task_id": task_id,
                "confirmed_stopped": True,
            }:
                raise PromptWorkspaceError(
                    "FRESH_SESSION_REQUIRED",
                    "recovery requires a fresh worker session",
                )
            commit_paths = (
                _task_commit_authorization(
                    worktree, assignment, plane_path, raw_session
                )
                if observed == base
                else None
            )
            return {
                "assignment": assignment,
                "worker_session_fingerprint_sha256": worker_session,
                "observed_head": observed,
                "changed_paths": changed,
                "commit_authorization": (
                    str(commit_paths[0]) if commit_paths is not None else None
                ),
                "commit_claim": (
                    str(commit_paths[1]) if commit_paths is not None else None
                ),
                "commit_context": _task_commit_context(
                    workspace, worktree, raw_session, commit_paths
                ),
                "result_context": result_context,
            }
        if worker_session in plane[
            "worker_session_sha256_history"
        ] or _session_was_used(run_dir, worker_session):
            raise PromptWorkspaceError(
                "FRESH_SESSION_REQUIRED",
                "recovery session identity was already used by this run",
            )
        plane["worker_session_sha256"] = worker_session
        plane["worker_session_sha256_history"].append(worker_session)
        recovered_at = _utc(clock)
        plane["dispatched_at"] = recovered_at
        plane["started_at"] = recovered_at
        plane["last_heartbeat_at"] = recovered_at
        plane["heartbeat_sequence"] = 1
        plane["heartbeat_phase"] = "preflight"
        plane["updated_at"] = recovered_at
        _save_task_plane(run_dir, plane)
        commit_paths = (
            _task_commit_authorization(worktree, assignment, plane_path, raw_session)
            if observed == base
            else None
        )
        return {
            "assignment": assignment,
            "worker_session_fingerprint_sha256": worker_session,
            "observed_head": observed,
            "changed_paths": changed,
            "commit_authorization": (
                str(commit_paths[0]) if commit_paths is not None else None
            ),
            "commit_claim": (
                str(commit_paths[1]) if commit_paths is not None else None
            ),
            "commit_context": _task_commit_context(
                workspace, worktree, raw_session, commit_paths
            ),
            "result_context": result_context,
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
            "summary",
            "decisions",
            "open_risks",
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
            or not isinstance(result.get("summary"), str)
            or not result["summary"].strip()
            or not isinstance(result.get("decisions"), list)
            or not isinstance(result.get("open_risks"), list)
            or not isinstance(result.get("changed_paths"), list)
            or not all(
                isinstance(path, str) and path
                for path in result.get("changed_paths", [])
            )
            or len(set(result.get("changed_paths", [])))
            != len(result.get("changed_paths", []))
            or any(
                not isinstance(item, str) or not item.strip()
                for item in result.get("decisions", [])
            )
            or any(
                not isinstance(item, str) or not item.strip()
                for item in result.get("open_risks", [])
            )
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
        retrying_terminal_replan = (
            plane["state"] == "failed"
            and wave["status"] == "blocked"
            and wave["task_states"].get(task_id) == "failed"
            and result.get("status") != "committed"
            and plane.get("result_sha256") == recorded_result_digest
        )
        if retrying_terminal_replan:
            return result
        retrying_rejected_paths = (
            plane["state"] == "failed"
            and wave["status"] == "blocked"
            and wave["task_states"].get(task_id) == "failed"
            and result.get("status") == "committed"
            and plane.get("result_sha256") == recorded_result_digest
            and plane.get("commit") == result.get("commit")
            and all(
                state != "failed"
                for other_task, state in wave["task_states"].items()
                if other_task != task_id
            )
        )
        if plane["state"] != "running" and not retrying_rejected_paths:
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
        guard = (
            None
            if retrying_rejected_paths
            else _worker_guard_status(assignment, plane, clock=clock)
        )
        if guard is not None and guard["status"] != "ACTIVE":
            if guard["status"] == "WORKER_SCOPE_VIOLATION":
                failed_at = str(guard["observed_at"])
                wave["task_states"][task_id] = "failed"
                wave["status"] = "blocked"
                wave["updated_at"] = failed_at
                _save_wave(run_dir, wave)
                plane["state"] = "failed"
                plane["updated_at"] = failed_at
                _save_task_plane(run_dir, plane)
            raise PromptWorkspaceError(
                str(guard["status"]),
                "worker result arrived after its liveness budget expired",
            )
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
            actual != sorted(result.get("changed_paths", []))
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
        if retrying_rejected_paths:
            wave["status"] = "running"
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
        active_batch_index = wave.get("active_batch_index")
        if isinstance(active_batch_index, int):
            active_tasks = wave["batches"][active_batch_index]
            if any(states[task_id] != "committed" for task_id in active_tasks):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "active capacity batch is incomplete"
                )
            if active_batch_index + 1 < len(wave["batches"]):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "later capacity batches are pending"
                )
            wave["batch_states"][active_batch_index] = "done"
            wave["active_batch_index"] = None
            _save_wave(run_dir, wave)
        if any(state != "done" for state in wave["batch_states"]):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "capacity batches are incomplete"
            )
        if any(
            states[task_id] not in {"committed", "merged", "superseded"}
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
            if plane["state"] == "superseded":
                continue
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


def _cleanup_resource(
    *,
    workspace: dict[str, object],
    run_dir: Path,
    wave: dict[str, object],
    repo: Path,
    kind: str,
    worktree: Path,
    branch: str,
    expected_tip: str,
    reachable_tip: str,
    clock: Callable[[], datetime],
) -> bool:
    worktrees = _registered_worktrees(repo)
    registration = worktrees.get(worktree.resolve())
    registered = registration is not None
    path_present = os.path.lexists(worktree)
    branch_result = _git(
        repo,
        ["rev-parse", "--verify", f"refs/heads/{branch}"],
        "read cleanup branch",
        check=False,
    )
    if branch_result.returncode != 0:
        if not registered and not path_present:
            record_resource(
                workspace,
                run_dir,
                kind=kind,
                path=worktree,
                branch=branch,
                state="absent",
            )
            return True
        return False
    branch_commit = branch_result.stdout.decode("ascii", errors="strict").strip()
    if branch_commit != expected_tip:
        return False
    reachable = (
        _git(
            repo,
            ["merge-base", "--is-ancestor", branch_commit, reachable_tip],
            "verify cleanup ancestry",
            check=False,
        ).returncode
        == 0
    )
    if (
        not reachable
        or (path_present and not registered)
        or (
            registered
            and (
                registration.get("branch") != f"refs/heads/{branch}"
                or _head(worktree) != expected_tip
                or not _clean(worktree)
            )
        )
    ):
        return False
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
            return False
    deletion = _journaled_git(
        _journal_path(run_dir, str(wave["wave_id"])),
        repo,
        ["update-ref", "-d", f"refs/heads/{branch}", expected_tip],
        "delete exact managed branch tip",
        clock,
        check=False,
    )
    if deletion.returncode != 0:
        return False
    if (
        os.path.lexists(worktree)
        or worktree.resolve() in _registered_worktrees(repo)
        or _git(
            repo,
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            "verify cleanup branch removal",
            check=False,
        ).returncode
        == 0
    ):
        return False
    record_resource(
        workspace,
        run_dir,
        kind=kind,
        path=worktree,
        branch=branch,
        state="absent",
    )
    return True


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
        preliminary_run_dir = _run_dir(workspace, run_id)
        preliminary_coordinator = load_coordinator_state(preliminary_run_dir)
        if preliminary_coordinator is not None and isinstance(
            preliminary_coordinator.get("active_wave"), str
        ):
            preliminary_wave = _load_wave(
                preliminary_run_dir, str(preliminary_coordinator["active_wave"])
            )
            recover_contract_delta_promotion(
                workspace,
                preliminary_run_dir,
                preliminary_coordinator,
                preliminary_wave,
            )
        run_dir, coordinator, wave = _coordinator_and_wave(
            workspace, run_id, allow_interrupted_promotion=True
        )
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
                plane["state"] not in {"merged", "superseded"}
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
        adopted_contract = contract_delta_active(workspace, run_dir, coordinator, wave)
        if (
            _branch(repo) != coordinator["base_branch"]
            or _head(repo) not in {wave["base_commit"], target}
            or (not _clean(repo) and not adopted_contract)
        ):
            raise PromptWorkspaceError(
                "PROMOTION_BLOCKED",
                "persistent lane moved or became dirty before promotion",
            )
        retained_workers: list[str] = []
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
            worktree = Path(str(assignment["worktree"]))
            branch = str(assignment["branch"])
            if not _cleanup_resource(
                workspace=workspace,
                run_dir=run_dir,
                wave=wave,
                repo=repo,
                kind="worker",
                worktree=worktree,
                branch=branch,
                expected_tip=str(plane["commit"]),
                reachable_tip=target,
                clock=clock,
            ):
                retained_workers.append(f"{worktree} ({branch})")
        wave["workers_cleaned"] = not retained_workers
        wave["cleanup_retained"] = retained_workers
        wave["updated_at"] = _utc(clock)
        _save_wave(run_dir, wave)
        if retained_workers:
            raise PromptWorkspaceError(
                "CLEANUP_BLOCKED",
                "worker worktrees or branches could not be removed after combined validation",
            )
        prepared_contract_delta = prepare_contract_delta_promotion(
            workspace, run_dir, coordinator, wave, target
        )
        try:
            promotion = promote_ff_only(
                repo,
                expected_branch=str(coordinator["base_branch"]),
                expected_base=str(wave["base_commit"]),
                target=target,
            )
        except GitPromotionError as error:
            if prepared_contract_delta:
                restore_contract_delta_after_failed_promotion(
                    workspace, run_dir, coordinator, wave
                )
            code = "PROMOTION_FAILED" if _head(repo) == target else "PROMOTION_BLOCKED"
            raise PromptWorkspaceError(code, str(error)) from error
        observed = str(promotion["head"])
        complete_contract_delta_promotion(
            workspace, run_dir, coordinator, wave, observed
        )
        record_promotion(workspace, run_dir, observed)
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


def _reconcile_promoted_spec_impact(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
    clock: Callable[[], datetime],
) -> bool:
    """Settle the one exact post-integration coordinator spec reconciliation."""

    project_root = Path(required_string(workspace, "source_root", "workspace manifest"))
    try:
        verify_prompt_impact_plan(run_dir, coordinator, project_root)
    except PromptWorkspaceError as error:
        if (
            error.code != "REPLAN_REQUIRED"
            or error.message
            != "canonical project specs drifted after impact settlement"
        ):
            raise
    else:
        return False
    if wave.get("status") not in {"promoted", "cleanup"}:
        raise PromptWorkspaceError(
            "REPLAN_REQUIRED",
            "canonical project specs changed outside promoted reconciliation",
        )
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    promoted = wave.get("promoted_head")
    integrated_head = wave.get("integrated_head")
    integration = Path(str(wave.get("integration_worktree")))
    scope = required_string(workspace, "scope", "workspace manifest")
    integration_project = integration if scope == "." else integration / scope
    if (
        not isinstance(promoted, str)
        or not isinstance(integrated_head, str)
        or _branch(repo) != coordinator.get("base_branch")
        or _head(repo) != promoted
        or not _clean(repo)
        or integration.is_symlink()
        or not integration.is_dir()
        or _head(integration) != promoted
        or not _promotion_coordinator_commit_is_safe(
            integration, integration_project, integrated_head, promoted
        )
    ):
        raise PromptWorkspaceError(
            "REPLAN_REQUIRED",
            "promoted specification reconciliation is not the exact coordinator commit",
        )
    run_state = verify_run(workspace, run_dir.name, None)
    refinement = load_requirements_refinement(run_dir, required=True)
    assert refinement is not None
    if (
        refinement.get("prompt_id") != run_state.get("prompt_id")
        or refinement.get("revision") != run_state.get("latest_revision")
        or refinement.get("intent_sha256") != run_state.get("latest_intent_sha256")
        or refinement.get("status") != "ready"
    ):
        raise PromptWorkspaceError(
            "REQUIREMENTS_REFINEMENT_REQUIRED",
            "promoted reconciliation has no matching ready refinement",
        )
    inspected = inspect_spec_documents(workspace)
    managed_requirements = inspected["requirements"].get("managed_sha256")
    if not isinstance(managed_requirements, str):
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "promoted requirements are not managed product truth"
        )
    if refinement.get("compiled_requirements_sha256") != managed_requirements:
        refinement["compiled_requirements_sha256"] = managed_requirements
        refinement["updated_at"] = _utc(clock)
        save_requirements_refinement(run_dir, refinement)
    settled = verify_requirements_refinement_contract(workspace, run_dir, run_state)
    impact = dict(settled["impact"])
    if impact.get("plan_action") != "retain_plan":
        raise PromptWorkspaceError(
            "REPLAN_REQUIRED",
            "promoted specification reconciliation materially changed the remaining plan",
        )
    settle_prompt_impact_plan(
        run_dir, coordinator, impact, str(settled["impact_sha256"])
    )
    verify_prompt_impact_plan(run_dir, coordinator, project_root)
    return True


def _final_wave(coordinator: dict[str, object], wave: dict[str, object]) -> bool:
    indexed = [
        item.get("wave_id")
        for item in coordinator.get("waves", [])
        if isinstance(item, dict)
    ]
    return bool(indexed) and indexed[-1] == wave.get("wave_id")


def _promote_terminal_lifecycle_seal(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
    clock: Callable[[], datetime],
) -> None:
    """Promote the exact final seal before its retained integration is removed."""

    if not _final_wave(coordinator, wave) or wave.get("status") != "promoted":
        return
    recover_terminal_lifecycle_promotion(workspace, run_dir, coordinator, wave)
    receipt = terminal_lifecycle_seal(run_dir, str(wave["wave_id"]))
    if receipt is not None and receipt.get("phase") == "promoted":
        target = str(receipt["contract_head"])
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        if _head(repo) != target or not _clean(repo):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "terminal lifecycle promotion receipt disagrees with the lane",
            )
        record_promotion(workspace, run_dir, target)
        if wave.get("promoted_head") != target:
            wave["promoted_head"] = target
            wave["updated_at"] = _utc(clock)
            _save_wave(run_dir, wave)
    if terminal_lifecycle_seal_promoted(workspace, run_dir, coordinator, wave):
        return
    if not terminal_lifecycle_seal_active(workspace, run_dir, coordinator, wave):
        raise PromptWorkspaceError(
            "LIFECYCLE_SEAL_REQUIRED",
            "seal the selected-project lifecycle before final wave cleanup",
        )
    promotion = prepare_terminal_lifecycle_promotion(
        workspace, run_dir, coordinator, wave
    )
    if promotion is not None:
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        try:
            result = promote_ff_only(
                repo,
                expected_branch=str(coordinator["base_branch"]),
                expected_base=promotion["base"],
                target=promotion["target"],
            )
        except GitPromotionError as error:
            restore_terminal_lifecycle_after_failed_promotion(
                workspace, run_dir, coordinator, wave
            )
            code = (
                "PROMOTION_FAILED"
                if _head(repo) == promotion["target"]
                else "PROMOTION_BLOCKED"
            )
            raise PromptWorkspaceError(code, str(error)) from error
        observed = str(result["head"])
        complete_terminal_lifecycle_promotion(run_dir, wave, observed)
        record_promotion(workspace, run_dir, observed)
        wave["promoted_head"] = observed
        wave["updated_at"] = _utc(clock)
        _save_wave(run_dir, wave)
    if not terminal_lifecycle_seal_promoted(workspace, run_dir, coordinator, wave):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "terminal lifecycle seal did not reach the lane"
        )


def cleanup_wave(
    manifest_path: Path,
    run_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        preliminary_run_dir = _run_dir(workspace, run_id)
        preliminary_coordinator = load_coordinator_state(preliminary_run_dir)
        if preliminary_coordinator is not None and isinstance(
            preliminary_coordinator.get("active_wave"), str
        ):
            preliminary_wave = _load_wave(
                preliminary_run_dir, str(preliminary_coordinator["active_wave"])
            )
            _promote_terminal_lifecycle_seal(
                workspace,
                preliminary_run_dir,
                preliminary_coordinator,
                preliminary_wave,
                clock,
            )
            _reconcile_promoted_spec_impact(
                workspace,
                preliminary_run_dir,
                preliminary_coordinator,
                preliminary_wave,
                clock,
            )
        run_dir, coordinator, wave = _coordinator_and_wave(workspace, run_id)
        _validate_wave_git_identity(manifest_path, workspace, run_id, wave)
        if wave["status"] == "done":
            _finalize_cleaned_wave(run_dir, coordinator, wave, clock)
            return wave
        if wave["status"] not in {"promoted", "cleanup"}:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "cleanup requires verified promotion"
            )
        if _final_wave(coordinator, wave) and not terminal_lifecycle_seal_promoted(
            workspace, run_dir, coordinator, wave
        ):
            raise PromptWorkspaceError(
                "LIFECYCLE_SEAL_REQUIRED",
                "the final wave cannot clean up before its lifecycle seal is promoted",
            )
        if wave.get("workers_cleaned") is not True:
            raise PromptWorkspaceError(
                "CLEANUP_BLOCKED",
                "worker resources must be cleaned before promotion",
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
        resources = [
            (
                "integration",
                Path(str(wave["integration_worktree"])),
                str(wave["integration_branch"]),
                promoted,
            )
        ]
        for kind, worktree, branch, expected_tip in resources:
            if not _cleanup_resource(
                workspace=workspace,
                run_dir=run_dir,
                wave=wave,
                repo=repo,
                kind=kind,
                worktree=worktree,
                branch=branch,
                expected_tip=expected_tip,
                reachable_tip=promoted,
                clock=clock,
            ):
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


def finalize_run(
    manifest_path: Path,
    run_id: str,
    alignment: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Seal terminal handoff evidence and release the active lane generation."""

    normalized_alignment = alignment.strip()
    if (
        not normalized_alignment
        or len(normalized_alignment.encode("utf-8")) > 2048
        or re.search(r"[\x00-\x1f\x7f]", normalized_alignment) is not None
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "final alignment evidence must be one bounded printable line",
        )
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir = _run_dir(workspace, run_id)
        coordinator = load_coordinator_state(run_dir)
        if coordinator is None or coordinator.get("status") != "done":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "all waves must be cleaned before finalization",
            )
        verify_prompt_impact_plan(
            run_dir,
            coordinator,
            Path(required_string(workspace, "source_root", "workspace manifest")),
        )
        waves = [
            _load_wave(run_dir, str(item["wave_id"])) for item in coordinator["waves"]
        ]
        if not waves or any(
            wave["status"] != "done" or wave["cleanup_retained"] for wave in waves
        ):
            raise PromptWorkspaceError(
                "CLEANUP_BLOCKED", "internal task resources are not fully cleaned"
            )
        if not terminal_lifecycle_seal_promoted(
            workspace, run_dir, coordinator, waves[-1]
        ):
            raise PromptWorkspaceError(
                "LIFECYCLE_SEAL_REQUIRED",
                "terminal lifecycle evidence is required before generation release",
            )
        promoted_head = waves[-1].get("promoted_head")
        if (
            not isinstance(promoted_head, str)
            or SHA_RE.fullmatch(promoted_head) is None
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "the final promoted head is missing"
            )
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        if (
            _branch(repo) != coordinator["base_branch"]
            or _head(repo) != promoted_head
            or not _clean(repo)
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "project checkout must be clean at the final promoted head",
            )
        handoff = read_handoff_text(run_dir)
        if handoff is None:
            raise PromptWorkspaceError("RUN_STATE_INVALID", "handoff is missing")
        handoff, count = re.subn(
            r"(?m)^- Overall status:\s*[a-z_]+\s*$",
            "- Overall status: done",
            handoff,
            count=1,
        )
        if count != 1:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "handoff has no unique overall status"
            )
        final_section = (
            "## Final Alignment\n\n"
            f"- Completed at: {_utc(clock)}\n"
            f"- Promoted commit: {promoted_head}\n"
            f"- Evidence: {normalized_alignment}\n"
        )
        if re.search(r"(?m)^## Final Alignment\s*$", handoff):
            handoff = re.sub(
                r"(?ms)^## Final Alignment\s*\n.*?(?=^## |\Z)",
                final_section,
                handoff,
                count=1,
            )
        else:
            handoff = handoff.rstrip() + "\n\n" + final_section
        result = release_interop(workspace, run_dir, promoted_head)
        # Machine completion owns the terminal boundary. Publish the human
        # projection only after the external lease and local interop receipt
        # both prove release; replay re-enters this exact idempotent sequence.
        write_atomic(run_dir / "handoff.md", handoff.encode("utf-8"))
        next_prompt = _activate_next_queued_prompt_unlocked(manifest_path, clock=clock)
        response: dict[str, object] = {
            "status": "done",
            "run_id": run_id,
            "promoted_head": promoted_head,
            "interop": result,
        }
        if next_prompt is not None:
            response["next_prompt"] = next_prompt
        return response
