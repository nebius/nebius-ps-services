#!/usr/bin/env python3
"""Authoritative, digest-bound resume planning for Task Implementer runs."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from prompt_workspace_core import (
    RUN_ID_RE,
    PromptWorkspaceError,
    ensure_private_dir,
    iso_seconds,
    load_json_object,
    now_utc,
    require_mode,
    required_string,
    stable_json,
    write_atomic,
)
from prompt_workspace_execution import (
    RESULT_SCHEMA,
    WORKER_START_SECONDS,
    WORKER_STALL_SECONDS,
    build_dependency_waves,
    load_coordinator_state,
    orchestration_dir,
    sha256_json,
)
from prompt_workspace_interop import load_interop, managed, observe_managed_state
from prompt_workspace_reporting import summary_phase
from prompt_workspace_runs import read_handoff_text, scope_lock
from prompt_workspace_specs import verify_prompt_impact_plan
from prompt_workspace_waves import (
    _assignment_path,
    _incoming_handoff_path,
    _valid_spec_gaps,
    _load_task_plane,
    _load_wave,
    _result_path,
    _task_record,
    _validated_assignment,
    _validated_incoming_handoff,
    _validate_assignment_context,
    _validate_wave_git_identity,
    _worker_guard_status,
    parse_task_plans,
)


RESUME_CONTROL_SCHEMA = "task-implementer/resume-control-v1"
RESUME_OUTCOMES = frozenset(
    {"execute", "wait", "requires_confirmation", "blocked", "complete"}
)
CONTROL_PHASES = frozenset(
    {"idle", "intent", "effect-observed", "state-committed", "projection-committed"}
)
CONTROLLED_TRANSITIONS = frozenset(
    {
        "checkpoint-prepare",
        "wave-plan",
        "wave-replan",
        "wave-prepare",
        "wave-dispatch",
        "batch-advance",
        "task-arm",
        "task-finish",
        "task-rearm",
        "task-recover",
        "wave-resource-recover",
        "wave-integrate",
        "wave-promote",
        "wave-cleanup",
        "run-finalize",
    }
)
RECOVERY_TRANSITIONS = frozenset(
    {"task-rearm", "task-recover", "wave-resource-recover"}
)
DEFAULT_RESUME_CAPACITY = 4
MAX_ALIGNMENT_BYTES = 2048


def _control_path(run_dir: Path) -> Path:
    return orchestration_dir(run_dir) / "resume-control.json"


def _run_dir(workspace: dict[str, object], run_id: str) -> Path:
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run ID is invalid")
    runs_root = Path(
        required_string(workspace, "runs_root", "workspace manifest")
    ).resolve()
    run_dir = runs_root / run_id
    if run_dir.parent != runs_root:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run path is invalid")
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run directory is unsafe")
    return run_dir


def _scope_dir(workspace: dict[str, object]) -> Path:
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    return runs_root.parent


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(repo: Path, arguments: list[str], label: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", f"Git could not {label}"
        ) from exc
    if result.returncode != 0:
        raise PromptWorkspaceError("WORKTREE_CONFLICT", f"Git could not {label}")
    return result.stdout.decode("utf-8", errors="strict").rstrip("\n")


def _git_observation(repo: Path) -> dict[str, object]:
    if repo.is_symlink() or not repo.is_dir():
        return {"present": False}
    return {
        "present": True,
        "branch": _git(repo, ["branch", "--show-current"], "read a branch"),
        "head": _git(repo, ["rev-parse", "HEAD"], "read a head"),
        "status_sha256": _sha256(
            _git(
                repo, ["status", "--porcelain=v1", "-z"], "read worktree state"
            ).encode()
        ),
    }


def _file_digests(run_dir: Path) -> list[dict[str, object]]:
    ignored = {
        run_dir / "handoff.md",
        _control_path(run_dir),
        orchestration_dir(run_dir) / ".workspace.lock",
    }
    records: list[dict[str, object]] = []
    for path in sorted(run_dir.rglob("*")):
        if path in ignored:
            continue
        if path.is_symlink():
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID", "run state contains a symlink"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID", "run state contains an unsafe object"
            )
        records.append(
            {
                "path": path.relative_to(run_dir).as_posix(),
                "sha256": _sha256(path.read_bytes()),
            }
        )
    return records


def _journal_state(run_dir: Path) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    journals = orchestration_dir(run_dir) / "journals"
    if not journals.exists():
        return observations
    if journals.is_symlink() or not journals.is_dir():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "resume journal directory is unsafe"
        )
    for path in sorted(journals.glob("*.jsonl")):
        if path.is_symlink() or not path.is_file():
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID", "resume journal path is unsafe"
            )
        pending: dict[str, int] = {}
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise PromptWorkspaceError(
                "RESUME_BLOCKED", "a wave journal has a partial final record"
            )
        for index, line in enumerate(raw.splitlines(), start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PromptWorkspaceError(
                    "RESUME_BLOCKED", "a wave journal contains invalid JSON"
                ) from exc
            if not isinstance(record, dict):
                raise PromptWorkspaceError(
                    "RESUME_BLOCKED", "a wave journal record is invalid"
                )
            operation = record.get("operation")
            phase = record.get("phase")
            if not isinstance(operation, str) or phase not in {"intent", "observed"}:
                raise PromptWorkspaceError(
                    "RESUME_BLOCKED", "a wave journal record is invalid"
                )
            if phase == "intent":
                pending[operation] = pending.get(operation, 0) + 1
            elif pending.get(operation, 0) > 0:
                pending[operation] -= 1
            else:
                raise PromptWorkspaceError(
                    "RESUME_BLOCKED", "a wave journal observation has no intent"
                )
        observations.append(
            {
                "path": path.relative_to(run_dir).as_posix(),
                "pending": sorted(key for key, count in pending.items() if count),
            }
        )
    return observations


def _validate_result(
    run_dir: Path,
    wave_id: str,
    task_id: str,
    assignment: dict[str, object],
    plane: dict[str, object],
    *,
    accepted: bool,
) -> dict[str, object]:
    result = load_json_object(_result_path(run_dir, wave_id, task_id), "worker result")
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
        "spec_gaps",
        "validation",
        "end_to_end_validation",
        "code_review",
        "completed_at",
        "result_sha256",
    }
    unsigned = {key: value for key, value in result.items() if key != "result_sha256"}
    if (
        set(result) != required
        or result.get("schema") != RESULT_SCHEMA
        or result.get("run_id") != run_dir.name
        or result.get("wave_id") != wave_id
        or result.get("task_id") != task_id
        or result.get("assignment_sha256") != assignment.get("assignment_sha256")
        or result.get("result_sha256") != sha256_json(unsigned)
        or not isinstance(result.get("summary"), str)
        or not str(result["summary"]).strip()
        or not isinstance(result.get("decisions"), list)
        or not isinstance(result.get("open_risks"), list)
        or not _valid_spec_gaps(result.get("spec_gaps"))
        or any(
            not isinstance(item, str) or not item.strip()
            for item in [*result["decisions"], *result["open_risks"]]
        )
        or (
            accepted
            and (
                plane.get("result_sha256") != result.get("result_sha256")
                or plane.get("commit") != result.get("commit")
            )
        )
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "accepted worker result is invalid"
        )
    return result


def _machine_observation(
    workspace: dict[str, object],
    run_dir: Path,
    *,
    observe_external: bool,
) -> dict[str, object]:
    coordinator = load_coordinator_state(run_dir)
    waves: list[dict[str, object]] = []
    tasks: list[dict[str, object]] = []
    resource_paths: set[Path] = set()
    if coordinator is not None:
        indexed = coordinator.get("waves")
        if not isinstance(indexed, list) or not indexed:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator wave index is invalid"
            )
        if coordinator.get("plan_sha256") != sha256_json(
            [entry.get("tasks") for entry in indexed if isinstance(entry, dict)]
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator plan digest is invalid"
            )
        wave_ids: list[str] = []
        for entry in indexed:
            if not isinstance(entry, dict) or not isinstance(entry.get("wave_id"), str):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "coordinator wave entry is invalid"
                )
            wave_id = str(entry["wave_id"])
            wave_ids.append(wave_id)
            wave = _load_wave(run_dir, wave_id)
            _validate_wave_git_identity(
                _scope_dir(workspace) / "workspace.json",
                workspace,
                run_dir.name,
                wave,
            )
            planned_tasks = entry.get("tasks")
            planned_batches = entry.get("batches")
            if (
                not isinstance(planned_tasks, list)
                or [
                    item.get("task_id")
                    for item in planned_tasks
                    if isinstance(item, dict)
                ]
                != wave["task_ids"]
                or planned_batches != wave["batches"]
            ):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "coordinator and wave plans differ"
                )
            if wave.get("integration_worktree") is not None:
                resource_paths.add(Path(str(wave["integration_worktree"])))
            for task_id in wave["task_ids"]:
                task_id = str(task_id)
                plane = _load_task_plane(run_dir, wave_id, task_id)
                if plane.get("state") != wave["task_states"].get(task_id):
                    raise PromptWorkspaceError(
                        "EXECUTION_STATE_INVALID", "wave and task-plane state differ"
                    )
                task_record: dict[str, object] = {
                    "wave_id": wave_id,
                    "task_id": task_id,
                    "plane": plane,
                }
                assignment_path = _assignment_path(run_dir, wave_id, task_id)
                incoming_path = _incoming_handoff_path(run_dir, wave_id, task_id)
                result_path = _result_path(run_dir, wave_id, task_id)
                if plane["state"] == "planned" and any(
                    path.exists()
                    for path in (assignment_path, incoming_path, result_path)
                ):
                    raise PromptWorkspaceError(
                        "EXECUTION_STATE_INVALID",
                        "planned task has premature immutable artifacts",
                    )
                if plane["state"] != "planned":
                    assignment = _validated_assignment(assignment_path)
                    if assignment.get("run_id") != run_dir.name:
                        raise PromptWorkspaceError(
                            "EXECUTION_STATE_INVALID", "assignment run identity differs"
                        )
                    _validate_assignment_context(
                        assignment, workspace, coordinator, run_dir, wave, task_id
                    )
                    handoff = _validated_incoming_handoff(incoming_path)
                    if (
                        handoff.get("handoff_sha256")
                        != assignment.get("incoming_handoff_sha256")
                        or handoff.get("run_id") != run_dir.name
                        or handoff.get("wave_id") != wave_id
                        or handoff.get("task_id") != task_id
                        or handoff.get("assignment_base_commit")
                        != assignment.get("base_commit")
                        or handoff.get("dependencies") != assignment.get("dependencies")
                    ):
                        raise PromptWorkspaceError(
                            "EXECUTION_STATE_INVALID", "assignment handoff differs"
                        )
                    task_record["assignment"] = assignment
                    if plane["state"] != "superseded":
                        resource_paths.add(Path(str(assignment["worktree"])))
                    if plane["state"] in {"committed", "merged", "superseded"}:
                        task_record["result"] = _validate_result(
                            run_dir,
                            wave_id,
                            task_id,
                            assignment,
                            plane,
                            accepted=True,
                        )
                    elif result_path.exists():
                        if plane["state"] == "failed":
                            failed_result = _validate_result(
                                run_dir,
                                wave_id,
                                task_id,
                                assignment,
                                plane,
                                accepted=False,
                            )
                            if (
                                failed_result.get("status")
                                not in {"REPLAN_REQUIRED", "COMPLETED"}
                                or plane.get("result_sha256")
                                != failed_result.get("result_sha256")
                                or wave.get("status") != "blocked"
                                or wave.get("task_states", {}).get(task_id) != "failed"
                            ):
                                raise PromptWorkspaceError(
                                    "EXECUTION_STATE_INVALID",
                                    "failed worker result is not terminal replan evidence",
                                )
                            task_record["result"] = failed_result
                        elif plane["state"] != "running":
                            raise PromptWorkspaceError(
                                "EXECUTION_STATE_INVALID",
                                "worker result exists before running state",
                            )
                        else:
                            task_record["pending_result"] = _validate_result(
                                run_dir,
                                wave_id,
                                task_id,
                                assignment,
                                plane,
                                accepted=False,
                            )
                tasks.append(task_record)
            waves.append(wave)
        if len(wave_ids) != len(set(wave_ids)):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator wave IDs are duplicated"
            )
        active_wave = coordinator.get("active_wave")
        if active_wave is not None and active_wave not in wave_ids:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator active wave is not indexed"
            )
        if coordinator.get("status") == "done" and active_wave is not None:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "done coordinator still has an active wave"
            )
        if coordinator.get("status") == "running" and not isinstance(active_wave, str):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "running coordinator has no active wave"
            )
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    git = {
        "lane": _git_observation(repo),
        "resources": [
            {"path": str(path), **_git_observation(path)}
            for path in sorted(resource_paths, key=str)
        ],
    }
    interop = load_interop(run_dir, required=False)
    lease: dict[str, object] | None = None
    repairs: dict[str, object] = {}
    if interop is not None and managed(interop) and observe_external:
        observed = observe_managed_state(
            workspace,
            run_dir,
            interop,
            allow_outer_dirty=False,
        )
        lease = dict(observed["lease"])
        repairs = dict(observed["repairs"])
    journals = _journal_state(run_dir)
    pending_unindexed_tasks = (
        _pending_unindexed_tasks(run_dir, coordinator)
        if coordinator is not None
        else []
    )
    prompt_impact_replan_required = False
    resource_observation = {
        "coordinator": coordinator,
        "waves": waves,
        "tasks": tasks,
        "git": git,
        "journals": journals,
    }
    if coordinator is not None and _active_planned_wave_is_resource_free(
        resource_observation
    ):
        try:
            verify_prompt_impact_plan(
                run_dir,
                coordinator,
                Path(required_string(workspace, "source_root", "workspace manifest")),
            )
        except PromptWorkspaceError as error:
            if error.code not in {"PROMPT_IMPACT_REQUIRED", "REPLAN_REQUIRED"}:
                raise
            prompt_impact_replan_required = True
    authoritative = {
        "files": _file_digests(run_dir),
        "coordinator": coordinator,
        "waves": waves,
        "tasks": tasks,
        "interop": interop,
        "lease": lease,
        "interop_repairs": repairs,
        "git": git,
        "journals": journals,
        "pending_unindexed_tasks": pending_unindexed_tasks,
        "prompt_impact_replan_required": prompt_impact_replan_required,
    }
    return {
        **authoritative,
        "state_sha256": _sha256(stable_json(authoritative)),
        "handoff_sha256": (_sha256((read_handoff_text(run_dir) or "").encode("utf-8"))),
    }


def _task_by_id(observation: dict[str, object], task_id: str) -> dict[str, object]:
    return next(item for item in observation["tasks"] if item["task_id"] == task_id)


def _transition(
    outcome: str,
    *,
    next_transition: str | None,
    reason: str,
    arguments: dict[str, object] | None = None,
    required_arguments: list[str] | None = None,
) -> dict[str, object]:
    if outcome not in RESUME_OUTCOMES:
        raise AssertionError(outcome)
    return {
        "outcome": outcome,
        "next_transition": next_transition,
        "reason": reason,
        "arguments": arguments or {},
        "required_arguments": required_arguments or [],
    }


def _pending_unindexed_tasks(
    run_dir: Path, coordinator: dict[str, object]
) -> list[str]:
    text = read_handoff_text(run_dir)
    if text is None:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "handoff is missing")
    indexed = {
        str(task["task_id"]) for wave in coordinator["waves"] for task in wave["tasks"]
    }
    return [
        task.task_id for task in parse_task_plans(text) if task.task_id not in indexed
    ]


def _planned_tail_drifted(run_dir: Path, observation: dict[str, object]) -> bool:
    """Return whether live pending task bytes differ from a resource-free plan."""

    coordinator = observation["coordinator"]
    if not isinstance(coordinator, dict):
        return False
    plan_sha256 = coordinator.get("plan_sha256")
    active_wave = coordinator.get("active_wave")
    waves = coordinator.get("waves")
    if (
        not isinstance(plan_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", plan_sha256) is None
        or not isinstance(active_wave, str)
        or not isinstance(waves, list)
    ):
        # Unit-level or legacy observations without a v7 immutable plan cannot
        # prove drift. The normal coordinator validator owns those schemas.
        return False
    try:
        active_index = next(
            index
            for index, wave in enumerate(waves)
            if isinstance(wave, dict) and wave.get("wave_id") == active_wave
        )
    except StopIteration as error:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "active wave is absent from the coordinator plan"
        ) from error
    text = read_handoff_text(run_dir)
    if text is None:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "handoff is missing")
    current_tail = [
        [_task_record(task) for task in wave]
        for wave in build_dependency_waves(parse_task_plans(text))
    ]
    completed_prefix = [
        wave["tasks"]
        for wave in waves[:active_index]
        if isinstance(wave, dict) and isinstance(wave.get("tasks"), list)
    ]
    return sha256_json([*completed_prefix, *current_tail]) != plan_sha256


def _active_planned_wave_is_resource_free(
    observation: dict[str, object],
) -> bool:
    """Prove that the active planned wave owns no material execution state."""

    coordinator = observation.get("coordinator")
    if not isinstance(coordinator, dict) or coordinator.get("status") != "running":
        return False
    active_wave = coordinator.get("active_wave")
    if not isinstance(active_wave, str):
        return False
    waves = observation.get("waves")
    tasks = observation.get("tasks")
    git = observation.get("git")
    journals = observation.get("journals")
    if (
        not isinstance(waves, list)
        or not isinstance(tasks, list)
        or not isinstance(git, dict)
        or not isinstance(journals, list)
        or any(
            isinstance(entry, dict) and bool(entry.get("pending")) for entry in journals
        )
    ):
        return False
    wave = next(
        (
            item
            for item in waves
            if isinstance(item, dict) and item.get("wave_id") == active_wave
        ),
        None,
    )
    if not isinstance(wave, dict) or wave.get("status") != "planned":
        return False
    integration_worktree = wave.get("integration_worktree")
    resources = git.get("resources")
    if not isinstance(integration_worktree, str) or not isinstance(resources, list):
        return False
    integration = next(
        (
            item
            for item in resources
            if isinstance(item, dict) and item.get("path") == integration_worktree
        ),
        None,
    )
    if not isinstance(integration, dict) or integration.get("present") is not False:
        return False
    active_tasks = [
        task
        for task in tasks
        if isinstance(task, dict) and task.get("wave_id") == active_wave
    ]
    task_ids = wave.get("task_ids")
    return (
        isinstance(task_ids, list)
        and len(active_tasks) == len(task_ids)
        and {task.get("task_id") for task in active_tasks} == set(task_ids)
        and all(
            isinstance(task.get("plane"), dict)
            and task["plane"].get("state") == "planned"
            and "assignment" not in task
            for task in active_tasks
        )
    )


def _resource_free_prepare_requires_replan(
    observation: dict[str, object], decision: dict[str, object]
) -> bool:
    return (
        decision.get("outcome") == "execute"
        and decision.get("next_transition") == "wave-replan"
        and _active_planned_wave_is_resource_free(observation)
    )


def _resume_capacity(
    coordinator: dict[str, object], requested_arguments: dict[str, object]
) -> int:
    requested = requested_arguments.get("capacity")
    if requested is not None:
        if (
            not isinstance(requested, int)
            or isinstance(requested, bool)
            or requested < 1
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "resume capacity is invalid"
            )
        return requested
    sizes = [
        len(batch)
        for wave in coordinator.get("waves", [])
        if isinstance(wave, dict)
        for batch in wave.get("batches", [])
        if isinstance(batch, list)
    ]
    return max(sizes, default=DEFAULT_RESUME_CAPACITY)


def _alignment_argument(value: object) -> str:
    if not isinstance(value, str):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "final alignment evidence is invalid"
        )
    normalized = value.strip()
    if (
        not normalized
        or len(normalized.encode("utf-8")) > MAX_ALIGNMENT_BYTES
        or re.search(r"[\x00-\x1f\x7f]", normalized) is not None
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "final alignment evidence must be one bounded printable line",
        )
    return normalized


def _choose_transition(
    run_dir: Path,
    observation: dict[str, object],
    *,
    clock: Callable[[], datetime],
    requested_arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    requested_arguments = requested_arguments or {}
    coordinator = observation["coordinator"]
    if coordinator is None:
        prepared = orchestration_dir(run_dir) / "lane-checkpoint-preparation.json"
        next_transition = "wave-plan" if prepared.exists() else "checkpoint-prepare"
        return _transition(
            "execute",
            next_transition=next_transition,
            reason="the accepted run has no coordinator plan",
            arguments=(
                {"capacity": _resume_capacity({}, requested_arguments)}
                if next_transition == "wave-plan"
                else {}
            ),
        )
    if coordinator["status"] == "blocked":
        return _transition(
            "blocked",
            next_transition=None,
            reason="the coordinator is blocked and requires an explicit correction",
        )
    pending_unindexed = list(observation.get("pending_unindexed_tasks", []))
    if pending_unindexed:
        active_id = coordinator.get("active_wave")
        candidate_id = (
            str(active_id)
            if isinstance(active_id, str)
            else str(coordinator["waves"][-1]["wave_id"])
        )
        candidate = next(
            item for item in observation["waves"] if item["wave_id"] == candidate_id
        )
        if candidate["status"] in {"promotion_pending", "done", "blocked"}:
            return _transition(
                "execute",
                next_transition="wave-replan",
                reason="new correction tasks await an immutable replan boundary",
                arguments={
                    "capacity": _resume_capacity(coordinator, requested_arguments)
                },
            )
    if coordinator["status"] == "done":
        interop = observation["interop"]
        if interop is None:
            return _transition(
                "blocked",
                next_transition=None,
                reason="the completed coordinator has no generation receipt",
            )
        handoff = read_handoff_text(run_dir) or ""
        projected_done = re.search(r"(?m)^- Overall status:\s*done\s*$", handoff)
        finalization_phase = summary_phase(run_dir)
        if (
            interop.get("released") is True
            and projected_done
            and finalization_phase in {None, "complete"}
        ):
            return _transition(
                "complete",
                next_transition=None,
                reason="the coordinator, generation, and terminal projection are complete",
            )
        alignment = requested_arguments.get("alignment")
        if alignment is not None:
            alignment_text = _alignment_argument(alignment)
            return _transition(
                "execute",
                next_transition="run-finalize",
                reason="terminal release, projection, or queue activation is incomplete",
                arguments={"alignment": alignment_text},
            )
        return _transition(
            "execute",
            next_transition="run-finalize",
            reason="fresh final-alignment evidence is required before terminal release",
            required_arguments=["alignment"],
        )
    active_wave = str(coordinator["active_wave"])
    wave = next(item for item in observation["waves"] if item["wave_id"] == active_wave)
    status = str(wave["status"])
    active_resource_paths = {str(wave["integration_worktree"])}
    active_resource_paths.update(
        str(task["assignment"]["worktree"])
        for task in observation["tasks"]
        if task["wave_id"] == active_wave and "assignment" in task
    )
    missing_resources = [
        item
        for item in observation["git"]["resources"]
        if item["path"] in active_resource_paths and item["present"] is False
    ]
    if missing_resources and status in {"running", "promotion_pending"}:
        return _transition(
            "requires_confirmation",
            next_transition="wave-resource-recover",
            reason="an active registered resource is missing and prior workers must be confirmed stopped",
        )
    if any(entry["pending"] for entry in observation["journals"]):
        return _transition(
            "blocked",
            next_transition=None,
            reason="a Git journal intent has no observed outcome",
        )
    if status == "blocked":
        publisher_migrations = [
            task
            for task in observation["tasks"]
            if task.get("wave_id") == active_wave
            and isinstance(task.get("plane"), dict)
            and task["plane"].get("state") == "failed"
            and wave.get("task_states", {}).get(task.get("task_id")) == "failed"
            and isinstance(task.get("result"), dict)
            and task["result"].get("status") == "COMPLETED"
        ]
        if len(publisher_migrations) == 1:
            return _transition(
                "execute",
                next_transition="task-finish",
                reason=(
                    "a retained pre-fix successful result requires strict "
                    "coordinator revalidation"
                ),
                arguments={"task_id": str(publisher_migrations[0]["task_id"])},
            )
        return _transition(
            "blocked",
            next_transition=None,
            reason="the active wave is blocked and its evidence is retained",
        )
    if status == "planned":
        if observation.get("prompt_impact_replan_required") is True:
            return _transition(
                "execute",
                next_transition="wave-replan",
                reason=(
                    "the resource-free plan has newer prompt impact evidence "
                    "that requires replanning"
                ),
                arguments={
                    "capacity": _resume_capacity(coordinator, requested_arguments)
                },
            )
        if _planned_tail_drifted(run_dir, observation):
            return _transition(
                "execute",
                next_transition="wave-replan",
                reason=(
                    "the resource-free planned tail differs from the current "
                    "task contract"
                ),
                arguments={
                    "capacity": _resume_capacity(coordinator, requested_arguments)
                },
            )
        return _transition(
            "execute",
            next_transition="wave-prepare",
            reason="the active wave is planned",
        )
    if status == "preparing":
        integration = next(
            item
            for item in observation["git"]["resources"]
            if item["path"] == str(wave["integration_worktree"])
        )
        if not integration["present"]:
            return _transition(
                "blocked",
                next_transition=None,
                reason="the preparing integration worktree is missing",
            )
        return _transition(
            "execute",
            next_transition="wave-dispatch",
            reason="the integration checkout is ready to bind and dispatch",
            arguments={"contract_commit": integration["head"]},
        )
    if status == "running":
        active_index = wave.get("active_batch_index")
        if not isinstance(active_index, int):
            if all(value == "done" for value in wave["batch_states"]):
                return _transition(
                    "execute",
                    next_transition="wave-integrate",
                    reason="all capacity batches are complete",
                )
            return _transition(
                "blocked",
                next_transition=None,
                reason="the running wave has no active batch",
            )
        active_tasks = [str(item) for item in wave["batches"][active_index]]
        for task_id in active_tasks:
            task = _task_by_id(observation, task_id)
            plane = task["plane"]
            state = plane["state"]
            result_path = _result_path(run_dir, active_wave, task_id)
            if state == "assigned":
                if plane["dispatched_at"] is None:
                    return _transition(
                        "execute",
                        next_transition="task-arm",
                        reason="an assigned task is waiting for a worker slot",
                        arguments={"task_id": task_id},
                    )
                dispatched = datetime.fromisoformat(str(plane["dispatched_at"]))
                age = (clock() - dispatched).total_seconds()
                if age < WORKER_START_SECONDS:
                    return _transition(
                        "wait",
                        next_transition=None,
                        reason="an armed worker is still inside its task-start deadline",
                        arguments={"task_id": task_id},
                    )
                return _transition(
                    "requires_confirmation",
                    next_transition="task-rearm",
                    reason="an armed worker expired and must be confirmed stopped",
                    arguments={
                        "task_id": task_id,
                        "expected_start_lease": plane["dispatched_at"],
                    },
                )
            if state == "running":
                assignment = task.get("assignment")
                if isinstance(assignment, dict):
                    guard = _worker_guard_status(assignment, plane, clock=clock)
                    if guard["status"] != "ACTIVE":
                        return _transition(
                            "requires_confirmation",
                            next_transition="task-recover",
                            reason=(
                                f"worker guard reported {guard['status']} and prior "
                                "ownership must be confirmed stopped"
                            ),
                            arguments={"task_id": task_id},
                        )
                if result_path.exists():
                    return _transition(
                        "execute",
                        next_transition="task-finish",
                        reason="a worker result is ready for coordinator acceptance",
                        arguments={"task_id": task_id},
                    )
                heartbeat = datetime.fromisoformat(str(plane["last_heartbeat_at"]))
                age = (clock() - heartbeat).total_seconds()
                if age < WORKER_STALL_SECONDS:
                    return _transition(
                        "wait",
                        next_transition=None,
                        reason="a running worker has a fresh heartbeat",
                        arguments={"task_id": task_id},
                    )
                return _transition(
                    "requires_confirmation",
                    next_transition="task-recover",
                    reason="a worker heartbeat is stale and prior ownership must be confirmed stopped",
                    arguments={"task_id": task_id},
                )
            if state == "failed":
                return _transition(
                    "blocked",
                    next_transition=None,
                    reason="a task failed and its evidence is retained",
                    arguments={"task_id": task_id},
                )
        if all(wave["task_states"][task_id] == "committed" for task_id in active_tasks):
            if active_index + 1 < len(wave["batches"]):
                return _transition(
                    "execute",
                    next_transition="batch-advance",
                    reason="the active batch is committed and another batch is pending",
                )
            return _transition(
                "execute",
                next_transition="wave-integrate",
                reason="the final capacity batch is committed",
            )
        return _transition(
            "blocked",
            next_transition=None,
            reason="the active batch has an inconsistent task-state combination",
        )
    if status == "integrating":
        return _transition(
            "execute",
            next_transition="wave-integrate",
            reason="ordered integration is incomplete",
        )
    if status == "promotion_pending":
        return _transition(
            "execute",
            next_transition="wave-promote",
            reason="the sealed integration awaits fresh combined evidence and promotion",
            arguments={
                "evidence": str(
                    orchestration_dir(run_dir) / "evidence" / f"{active_wave}.json"
                )
            },
        )
    if status == "promoted":
        return _transition(
            "execute",
            next_transition="wave-cleanup",
            reason="promotion cleanup or coordinator advancement is incomplete",
        )
    if status in {"cleanup", "done"}:
        return _transition(
            "execute",
            next_transition="wave-cleanup",
            reason="promotion cleanup or coordinator advancement is incomplete",
        )
    return _transition(
        "blocked", next_transition=None, reason="the active wave status is unsupported"
    )


def load_resume_control(
    run_dir: Path, *, required: bool = False
) -> dict[str, object] | None:
    path = _control_path(run_dir)
    if not path.exists():
        if required:
            raise PromptWorkspaceError("RESUME_REQUIRED", "resume control is missing")
        return None
    require_mode(path, 0o600, "resume control")
    value = load_json_object(path, "resume control")
    required_fields = {
        "schema",
        "run_id",
        "epoch",
        "adopted",
        "phase",
        "pre_state_sha256",
        "transition",
        "arguments",
        "arguments_sha256",
        "resume_token",
        "terminal_state_sha256",
        "projection_sha256",
        "updated_at",
    }
    if (
        set(value) != required_fields
        or value.get("schema") != RESUME_CONTROL_SCHEMA
        or value.get("run_id") != run_dir.name
        or not isinstance(value.get("epoch"), int)
        or int(value["epoch"]) < 1
        or value.get("adopted") is not True
        or value.get("phase") not in CONTROL_PHASES
        or any(
            item is not None
            and (
                not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
            )
            for item in (
                value.get("pre_state_sha256"),
                value.get("arguments_sha256"),
                value.get("terminal_state_sha256"),
                value.get("projection_sha256"),
            )
        )
        or (
            value.get("resume_token") is not None
            and (
                not isinstance(value.get("resume_token"), str)
                or re.fullmatch(r"[0-9a-f]{64}", str(value["resume_token"])) is None
            )
        )
        or (
            value.get("transition") is not None
            and value.get("transition") not in CONTROLLED_TRANSITIONS
        )
        or (
            value.get("arguments") is not None
            and not isinstance(value.get("arguments"), dict)
        )
        or (
            isinstance(value.get("arguments"), dict)
            and value.get("arguments_sha256")
            != _sha256(stable_json(value["arguments"]))
        )
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "resume control is invalid"
        )
    if value["phase"] == "idle" and any(
        value.get(key) is not None
        for key in ("transition", "arguments", "arguments_sha256", "resume_token")
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "idle resume control is invalid"
        )
    if value["phase"] != "idle" and any(
        value.get(key) is None
        for key in (
            "transition",
            "arguments",
            "arguments_sha256",
            "resume_token",
            "pre_state_sha256",
        )
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "active resume control is invalid"
        )
    return value


@contextmanager
def resume_execution_lock(workspace: dict[str, object], run_id: str) -> Iterator[None]:
    """Serialize one adopted resume-controlled CLI transition end to end."""

    run_dir = _run_dir(workspace, run_id)
    with scope_lock(_scope_dir(workspace)):
        ensure_private_dir(orchestration_dir(run_dir))
        with scope_lock(orchestration_dir(run_dir)):
            yield


def plan_run_resume(
    workspace: dict[str, object],
    run_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
    observe_external: bool = True,
    requested_arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return a pure authoritative resume decision without writing run state."""

    run_dir = _run_dir(workspace, run_id)
    control = load_resume_control(run_dir, required=False)
    if control is not None and control["phase"] != "idle":
        handoff_sha256 = _sha256((read_handoff_text(run_dir) or "").encode("utf-8"))
        if control["phase"] in {"state-committed", "projection-committed"}:
            return {
                "outcome": "execute",
                "next_transition": "resume-reconcile",
                "reason": "the committed transition must finish projection reconciliation",
                "arguments": {},
                "required_arguments": [],
                "resume_token": control["resume_token"],
                "state_sha256": control["terminal_state_sha256"],
                "handoff_sha256": handoff_sha256,
                "epoch": control["epoch"],
                "replay": True,
            }
        return {
            "outcome": "execute",
            "next_transition": control["transition"],
            "reason": "the recorded transition must be replayed before another can begin",
            "arguments": dict(control["arguments"]),
            "required_arguments": [],
            "resume_token": control["resume_token"],
            "state_sha256": control["pre_state_sha256"],
            "handoff_sha256": handoff_sha256,
            "epoch": control["epoch"],
            "replay": True,
        }
    observation = _machine_observation(
        workspace, run_dir, observe_external=observe_external
    )
    decision = _choose_transition(
        run_dir,
        observation,
        clock=clock,
        requested_arguments=requested_arguments,
    )
    if control is None and any(entry["pending"] for entry in observation["journals"]):
        decision = _transition(
            "blocked",
            next_transition=None,
            reason="a journal-less v7 run has an unresolved Git intent and cannot be adopted",
        )
    return {
        **decision,
        "resume_token": None,
        "state_sha256": observation["state_sha256"],
        "handoff_sha256": observation["handoff_sha256"],
        "epoch": int(control["epoch"]) if control is not None else 0,
        "replay": False,
    }


def _token(run_id: str, epoch: int, plan: dict[str, object]) -> str:
    return _sha256(
        stable_json(
            {
                "schema": "task-implementer/resume-token-v1",
                "run_id": run_id,
                "epoch": epoch,
                "state_sha256": plan["state_sha256"],
                "transition": plan["next_transition"],
                "arguments": plan["arguments"],
            }
        )
    )


def adopt_resume_plan(
    workspace: dict[str, object],
    run_id: str,
    plan: dict[str, object],
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Adopt or refresh a validated stable coordinator-v7 observation."""

    run_dir = _run_dir(workspace, run_id)
    current = load_resume_control(run_dir, required=False)
    if current is not None and current["phase"] != "idle":
        if plan.get("resume_token") != current.get("resume_token"):
            raise PromptWorkspaceError("RESUME_STALE", "active resume token changed")
        return plan
    if plan["outcome"] == "blocked":
        return plan
    refreshed = plan_run_resume(
        workspace,
        run_id,
        clock=clock,
        requested_arguments=dict(plan.get("arguments") or {}),
    )
    if (
        refreshed["state_sha256"] != plan["state_sha256"]
        or refreshed["outcome"] != plan["outcome"]
        or refreshed["next_transition"] != plan["next_transition"]
        or refreshed["arguments"] != plan["arguments"]
    ):
        raise PromptWorkspaceError(
            "RESUME_STALE", "authoritative run state changed during resume planning"
        )
    epoch = int(current["epoch"]) + 1 if current is not None else 1
    token = (
        _token(run_id, epoch, refreshed)
        if refreshed["outcome"] == "execute" and not refreshed.get("required_arguments")
        else None
    )
    state = {
        "schema": RESUME_CONTROL_SCHEMA,
        "run_id": run_id,
        "epoch": epoch,
        "adopted": True,
        "phase": "idle",
        "pre_state_sha256": refreshed["state_sha256"],
        "transition": None,
        "arguments": None,
        "arguments_sha256": None,
        "resume_token": None,
        "terminal_state_sha256": refreshed["state_sha256"],
        "projection_sha256": refreshed["handoff_sha256"],
        "updated_at": iso_seconds(clock()),
    }
    write_atomic(_control_path(run_dir), stable_json(state))
    return {**refreshed, "resume_token": token, "epoch": epoch}


def begin_resume_transition(
    workspace: dict[str, object],
    run_id: str,
    transition: str,
    token: str | None,
    *,
    arguments: dict[str, object] | None = None,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object] | None:
    """Fence a coordinator transition when the run has adopted resume control."""

    with scope_lock(_scope_dir(workspace)):
        run_dir = _run_dir(workspace, run_id)
        control = load_resume_control(run_dir, required=False)
        if transition not in CONTROLLED_TRANSITIONS:
            raise PromptWorkspaceError(
                "RESUME_BLOCKED", "transition is not resume-controlled"
            )
        actual_arguments = arguments or {}
        bootstrapped = control is None
        if control is None:
            candidate = plan_run_resume(
                workspace,
                run_id,
                clock=clock,
                requested_arguments=actual_arguments,
            )
            adopted = adopt_resume_plan(workspace, run_id, candidate, clock=clock)
            control = load_resume_control(run_dir, required=False)
            if control is None:
                raise PromptWorkspaceError(
                    "RESUME_BLOCKED",
                    "the first controlled transition could not adopt resume state",
                )
            if token is None:
                token = (
                    str(adopted["resume_token"])
                    if adopted.get("resume_token") is not None
                    else None
                )
        if control["phase"] != "idle":
            replay_without_token = (
                transition in RECOVERY_TRANSITIONS
                and token is None
                and actual_arguments.get("confirmed_stopped") is True
            )
            if (
                control["transition"] != transition
                or (control["resume_token"] != token and not replay_without_token)
                or control["arguments"] != actual_arguments
                or control["arguments_sha256"] != _sha256(stable_json(actual_arguments))
            ):
                raise PromptWorkspaceError(
                    "RESUME_STALE", "another resume transition is already active"
                )
            result = dict(control)
            if control["phase"] in {
                "effect-observed",
                "state-committed",
                "projection-committed",
            }:
                result["_effect_complete"] = True
                return result
            if transition in RECOVERY_TRANSITIONS:
                # Recovery helpers own transition-specific idempotency. They
                # must finish exact partial artifacts (for example commit
                # authorization after worker-session transfer) before the
                # controller can mark the effect observed.
                return result
            try:
                observation = _machine_observation(
                    workspace, run_dir, observe_external=True
                )
                decision = _choose_transition(run_dir, observation, clock=clock)
            except PromptWorkspaceError:
                return result
            if transition == "wave-prepare" and _resource_free_prepare_requires_replan(
                observation, decision
            ):
                _retire_resume_intent(control, observation, clock=clock)
                write_atomic(_control_path(run_dir), stable_json(control))
                raise PromptWorkspaceError(
                    "REPLAN_REQUIRED",
                    "the resource-free prepare intent was superseded by newer plan evidence",
                )
            if observation["state_sha256"] != control["pre_state_sha256"] and (
                decision["outcome"] not in {"execute", "requires_confirmation"}
                or decision["next_transition"] != transition
            ):
                result["_effect_complete"] = True
            return result
        plan = plan_run_resume(
            workspace,
            run_id,
            clock=clock,
            requested_arguments=actual_arguments,
        )
        recovery_authorized = (
            transition in RECOVERY_TRANSITIONS
            and plan["outcome"] == "requires_confirmation"
            and plan["next_transition"] == transition
            and actual_arguments
            == {**dict(plan["arguments"]), "confirmed_stopped": True}
            and token is None
        )
        token_plan = {
            **plan,
            "outcome": "execute",
            "arguments": actual_arguments,
        }
        expected = _token(run_id, int(control["epoch"]), token_plan)
        if recovery_authorized:
            token = expected
        elif (
            plan["outcome"] != "execute"
            or plan["next_transition"] != transition
            or plan["arguments"] != actual_arguments
            or plan.get("required_arguments")
            or token is None
            or token != expected
        ):
            raise PromptWorkspaceError(
                "RESUME_STALE", "resume token does not match the current transition"
            )
        if bootstrapped and token != expected:
            raise PromptWorkspaceError(
                "RESUME_STALE", "initial resume adoption token changed"
            )
        control.update(
            {
                "phase": "intent",
                "pre_state_sha256": plan["state_sha256"],
                "transition": transition,
                "arguments": actual_arguments,
                "arguments_sha256": _sha256(stable_json(actual_arguments)),
                "resume_token": token,
                "terminal_state_sha256": None,
                "projection_sha256": None,
                "updated_at": iso_seconds(clock()),
            }
        )
        write_atomic(_control_path(run_dir), stable_json(control))
        return control


def _replace_unique(text: str, pattern: str, replacement: str, label: str) -> str:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"handoff has no unique {label} projection field"
        )
    updated, _ = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    return updated


def _replace_section_field(
    text: str, heading: str, pattern: str, replacement: str, label: str
) -> str:
    match = re.search(rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", text)
    if match is None:
        return text.rstrip() + f"\n\n## {heading}\n\n{replacement}\n"
    matches = re.findall(pattern, match.group(1), flags=re.MULTILINE)
    if len(matches) > 1:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"handoff has no unique {label} projection field"
        )
    body = (
        _replace_unique(match.group(1), pattern, replacement, label)
        if matches
        else match.group(1).rstrip() + f"\n{replacement}\n"
    )
    return text[: match.start(1)] + body + text[match.end(1) :]


def _replace_indexed_status(
    text: str, parent_heading: str, item_heading: str, status: str
) -> str:
    parent = re.search(
        rf"(?ms)^## {re.escape(parent_heading)}\s*\n(.*?)(?=^## |\Z)", text
    )
    if parent is None:
        return (
            text.rstrip()
            + f"\n\n## {parent_heading}\n\n### {item_heading}\n\n- Status: {status}\n"
        )
    item = re.search(
        rf"(?ms)^### {re.escape(item_heading)}\s*\n(.*?)(?=^### |\Z)",
        parent.group(1),
    )
    if item is None:
        parent_body = (
            parent.group(1).rstrip() + f"\n\n### {item_heading}\n\n- Status: {status}\n"
        )
        return text[: parent.start(1)] + parent_body + text[parent.end(1) :]
    matches = re.findall(r"^- Status:.*$", item.group(1), flags=re.MULTILINE)
    if len(matches) > 1:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID",
            f"handoff has no unique {item_heading} status projection field",
        )
    body = (
        _replace_unique(
            item.group(1),
            r"^- Status:.*$",
            f"- Status: {status}",
            f"{item_heading} status",
        )
        if matches
        else item.group(1).rstrip() + f"\n- Status: {status}\n"
    )
    parent_body = (
        parent.group(1)[: item.start(1)] + body + parent.group(1)[item.end(1) :]
    )
    return text[: parent.start(1)] + parent_body + text[parent.end(1) :]


def _projected_task_status(plane_state: str, wave_status: str) -> str:
    """Map machine task state into the sole accepted handoff vocabulary."""

    if wave_status in {"promoted", "cleanup", "done"}:
        return "done"
    statuses = {
        "planned": "pending",
        "assigned": "in_progress",
        "running": "in_progress",
        "committed": "in_progress",
        "merged": "in_progress",
        "failed": "blocked",
        "superseded": "superseded",
    }
    try:
        return statuses[plane_state]
    except KeyError as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "task plane state cannot be projected"
        ) from error


def reconcile_handoff_projection(
    workspace: dict[str, object],
    run_id: str,
    plan: dict[str, object],
    *,
    expected_sha256: str,
) -> str:
    """Update only machine-owned handoff fields with a compare-and-swap digest."""

    run_dir = _run_dir(workspace, run_id)
    handoff_path = run_dir / "handoff.md"
    text = read_handoff_text(run_dir)
    if text is None:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "handoff is missing")
    if _sha256(text.encode("utf-8")) != expected_sha256:
        raise PromptWorkspaceError(
            "RESUME_STALE", "handoff changed while its projection was being reconciled"
        )
    coordinator = load_coordinator_state(run_dir)
    if coordinator is None:
        return expected_sha256
    effective = (
        "done"
        if plan["outcome"] == "complete"
        else "blocked"
        if plan["outcome"] == "blocked"
        else "running"
    )
    active_wave = str(coordinator.get("active_wave") or "none")
    indexed_waves = [
        _load_wave(run_dir, str(item["wave_id"])) for item in coordinator["waves"]
    ]
    promoted = [wave for wave in indexed_waves if wave.get("promoted_head") is not None]
    last_wave = str(promoted[-1]["wave_id"]) if promoted else "none"
    last_commit = str(promoted[-1]["promoted_head"]) if promoted else "none"
    updated = _replace_section_field(
        text,
        "Run",
        r"^- Active wave:.*$",
        f"- Active wave: {active_wave}",
        "run active wave",
    )
    updated = _replace_section_field(
        updated,
        "Run",
        r"^- Last promoted wave:.*$",
        f"- Last promoted wave: {last_wave}",
        "last promoted wave",
    )
    updated = _replace_section_field(
        updated,
        "Run",
        r"^- Last promoted commit:.*$",
        f"- Last promoted commit: {last_commit}",
        "last promoted commit",
    )
    updated = _replace_section_field(
        updated,
        "Run",
        r"^- Overall status:.*$",
        f"- Overall status: {effective}",
        "run status",
    )
    action = str(plan.get("next_transition") or plan["outcome"])
    updated = _replace_section_field(
        updated,
        "Coordinator Handoff",
        r"^- Current action:.*$",
        f"- Current action: {action}",
        "current action",
    )
    updated = _replace_section_field(
        updated,
        "Coordinator Handoff",
        r"^- Active wave:.*$",
        f"- Active wave: {active_wave}",
        "coordinator active wave",
    )
    for wave in indexed_waves:
        wave_status = str(wave["status"])
        updated = _replace_indexed_status(
            updated, "Dependency Waves", str(wave["wave_id"]), wave_status
        )
        for task_id in wave["task_ids"]:
            plane = _load_task_plane(run_dir, str(wave["wave_id"]), str(task_id))
            task_status = _projected_task_status(str(plane["state"]), wave_status)
            updated = _replace_indexed_status(
                updated, "Task Queue", str(task_id), task_status
            )
    if updated != text:
        write_atomic(handoff_path, updated.encode("utf-8"))
    return _sha256(updated.encode("utf-8"))


def complete_resume_transition(
    workspace: dict[str, object],
    run_id: str,
    transition: str,
    token: str | None,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> None:
    with scope_lock(_scope_dir(workspace)):
        run_dir = _run_dir(workspace, run_id)
        control = load_resume_control(run_dir, required=False)
        if control is None:
            return
        if control["transition"] != transition or control["resume_token"] != token:
            raise PromptWorkspaceError(
                "RESUME_STALE", "resume completion token changed"
            )
        control["phase"] = "effect-observed"
        control["updated_at"] = iso_seconds(clock())
        write_atomic(_control_path(run_dir), stable_json(control))
        observation = _machine_observation(workspace, run_dir, observe_external=True)
        decision = _choose_transition(run_dir, observation, clock=clock)
        plan = {
            **decision,
            "state_sha256": observation["state_sha256"],
            "handoff_sha256": observation["handoff_sha256"],
        }
        control["phase"] = "state-committed"
        control["terminal_state_sha256"] = plan["state_sha256"]
        control["updated_at"] = iso_seconds(clock())
        write_atomic(_control_path(run_dir), stable_json(control))
        projection = reconcile_handoff_projection(
            workspace,
            run_id,
            plan,
            expected_sha256=str(plan["handoff_sha256"]),
        )
        control["phase"] = "projection-committed"
        control["projection_sha256"] = projection
        control["updated_at"] = iso_seconds(clock())
        write_atomic(_control_path(run_dir), stable_json(control))
        control.update(
            {
                "phase": "idle",
                "pre_state_sha256": plan["state_sha256"],
                "transition": None,
                "arguments": None,
                "arguments_sha256": None,
                "resume_token": None,
                "terminal_state_sha256": plan["state_sha256"],
                "updated_at": iso_seconds(clock()),
            }
        )
        write_atomic(_control_path(run_dir), stable_json(control))


def reconcile_committed_resume(
    workspace: dict[str, object],
    run_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> None:
    """Finish a committed state/projection phase without replaying its effect."""

    with scope_lock(_scope_dir(workspace)):
        run_dir = _run_dir(workspace, run_id)
        control = load_resume_control(run_dir, required=False)
        if control is None or control["phase"] not in {
            "state-committed",
            "projection-committed",
        }:
            return
        if control["phase"] == "state-committed":
            observation = _machine_observation(
                workspace, run_dir, observe_external=True
            )
            if observation["state_sha256"] != control["terminal_state_sha256"]:
                raise PromptWorkspaceError(
                    "RESUME_STALE",
                    "committed transition state changed before projection recovery",
                )
            decision = _choose_transition(run_dir, observation, clock=clock)
            projection = reconcile_handoff_projection(
                workspace,
                run_id,
                {
                    **decision,
                    "state_sha256": observation["state_sha256"],
                    "handoff_sha256": observation["handoff_sha256"],
                },
                expected_sha256=str(observation["handoff_sha256"]),
            )
            control["phase"] = "projection-committed"
            control["projection_sha256"] = projection
            control["updated_at"] = iso_seconds(clock())
            write_atomic(_control_path(run_dir), stable_json(control))
        current_handoff = _sha256((read_handoff_text(run_dir) or "").encode("utf-8"))
        if current_handoff != control["projection_sha256"]:
            # Invocation metadata or user narrative may change after the
            # projection write and before control retirement. Revalidate the
            # machine terminal state, then CAS only coordinator-owned fields
            # over the current handoff bytes.
            observation = _machine_observation(
                workspace, run_dir, observe_external=True
            )
            if observation["state_sha256"] != control["terminal_state_sha256"]:
                raise PromptWorkspaceError(
                    "RESUME_STALE",
                    "committed transition state changed before projection retirement",
                )
            decision = _choose_transition(run_dir, observation, clock=clock)
            control["projection_sha256"] = reconcile_handoff_projection(
                workspace,
                run_id,
                {
                    **decision,
                    "state_sha256": observation["state_sha256"],
                    "handoff_sha256": observation["handoff_sha256"],
                },
                expected_sha256=str(observation["handoff_sha256"]),
            )
            control["updated_at"] = iso_seconds(clock())
            write_atomic(_control_path(run_dir), stable_json(control))
        control.update(
            {
                "phase": "idle",
                "pre_state_sha256": control["terminal_state_sha256"],
                "transition": None,
                "arguments": None,
                "arguments_sha256": None,
                "resume_token": None,
                "updated_at": iso_seconds(clock()),
            }
        )
        write_atomic(_control_path(run_dir), stable_json(control))


def abort_resume_transition_if_unchanged(
    workspace: dict[str, object],
    run_id: str,
    transition: str,
    token: str | None,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> None:
    """Clear a failed intent only when no authoritative state changed."""

    with scope_lock(_scope_dir(workspace)):
        run_dir = _run_dir(workspace, run_id)
        control = load_resume_control(run_dir, required=False)
        if (
            control is None
            or control["transition"] != transition
            or control["resume_token"] != token
        ):
            return
        current = _machine_observation(workspace, run_dir, observe_external=True)
        if current["state_sha256"] != control["pre_state_sha256"]:
            try:
                decision = _choose_transition(run_dir, current, clock=clock)
            except PromptWorkspaceError:
                return
            if transition != "wave-prepare" or not (
                _resource_free_prepare_requires_replan(current, decision)
            ):
                return
        _retire_resume_intent(control, current, clock=clock)
        write_atomic(_control_path(run_dir), stable_json(control))


def _retire_resume_intent(
    control: dict[str, object],
    observation: dict[str, object],
    *,
    clock: Callable[[], datetime],
) -> None:
    """Retire one proven no-effect intent at its current authoritative state."""

    control.update(
        {
            "phase": "idle",
            "pre_state_sha256": observation["state_sha256"],
            "transition": None,
            "arguments": None,
            "arguments_sha256": None,
            "resume_token": None,
            "terminal_state_sha256": observation["state_sha256"],
            "projection_sha256": observation["handoff_sha256"],
            "updated_at": iso_seconds(clock()),
        }
    )


def effective_run_status(run_dir: Path, handoff_status: str) -> str:
    """Return machine-owned status once coordinator-v7 exists."""

    coordinator = load_coordinator_state(run_dir)
    if coordinator is None:
        return handoff_status
    if coordinator["status"] == "blocked":
        return "blocked"
    if coordinator["status"] == "running":
        return "running"
    interop = load_interop(run_dir, required=False)
    return (
        "done" if interop is not None and interop.get("released") is True else "running"
    )


def resume_run(
    manifest_path: Path,
    run_id: str,
    *,
    clock: Callable[[], datetime] = now_utc,
    capacity: int | None = None,
    alignment: str | None = None,
) -> dict[str, object]:
    """Plan, adopt, and reconcile one run under its scope lock."""

    from prompt_workspace_core import verify_workspace

    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir = _run_dir(workspace, run_id)
        reconcile_committed_resume(workspace, run_id, clock=clock)
        requested_arguments: dict[str, object] = {}
        if capacity is not None:
            requested_arguments["capacity"] = capacity
        if alignment is not None:
            requested_arguments["alignment"] = alignment
        plan = plan_run_resume(
            workspace,
            run_id,
            clock=clock,
            requested_arguments=requested_arguments,
        )
        plan = adopt_resume_plan(workspace, run_id, plan, clock=clock)
        projection = str(plan["handoff_sha256"])
        if not plan["replay"]:
            projection = reconcile_handoff_projection(
                workspace,
                run_id,
                plan,
                expected_sha256=str(plan["handoff_sha256"]),
            )
        worker_context: dict[str, object] | None = None
        if plan.get("next_transition") == "task-recover":
            arguments = plan.get("arguments")
            task_id = str(
                arguments.get("task_id") if isinstance(arguments, dict) else ""
            )
            coordinator = load_coordinator_state(run_dir)
            active_wave = (
                coordinator.get("active_wave") if coordinator is not None else None
            )
            if not task_id or not isinstance(active_wave, str):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "task recovery launch context has no active assignment",
                )
            assignment_path = _assignment_path(run_dir, active_wave, task_id)
            assignment = _validated_assignment(assignment_path)
            helper = Path(
                required_string(assignment, "helper_path", "worker assignment")
            )
            workspace_manifest = Path(
                required_string(assignment, "workspace_manifest", "worker assignment")
            )
            scope_cwd = Path(
                required_string(assignment, "scope_cwd", "worker assignment")
            )
            worker_context = {
                "schema": "task-implementer/worker-recovery-context-v1",
                "assignment_path": str(assignment_path),
                "scope_cwd": str(scope_cwd),
                "worktree": required_string(
                    assignment, "worktree", "worker assignment"
                ),
                "recover_argv": [
                    str(Path(sys.executable).resolve(strict=True)),
                    str(helper),
                    "task-recover",
                    "--workspace",
                    str(workspace_manifest),
                    "--run-id",
                    run_id,
                    "--task-id",
                    task_id,
                    "--confirmed-stopped",
                    "--json",
                ],
            }
        result = {**plan, "projection_sha256": projection}
        if worker_context is not None:
            result["worker_context"] = worker_context
        return result
