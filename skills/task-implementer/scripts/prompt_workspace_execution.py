#!/usr/bin/env python3
"""Dependency-wave planning and private coordinator state validation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re

from prompt_workspace_core import (
    PromptWorkspaceError,
    load_json_object,
    stable_json,
)
from prompt_workspace_runs import markdown_section


COORDINATOR_SCHEMA = "task-implementer/coordinator-v7"
WAVE_SCHEMA = "task-implementer/wave-v4"
ASSIGNMENT_SCHEMA = "task-implementer/worker-assignment-v8"
RESULT_SCHEMA = "task-implementer/worker-result-v4"
TASK_PLANE_SCHEMA = "task-implementer/task-plane-v5"
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
    "committing",
    "reporting",
)
WORKER_GUARDRAILS = (
    "Stay inside the assigned worktree and private Task Implementer state. "
    "Read and execute installed Codex skill instructions, helper scripts, and "
    "standard local executables only as required by this assignment; never "
    "modify installed files. Do not intentionally create or write other paths. "
    "Do not access the network, credentials, external services, live runtimes, "
    "or other filesystem paths unless the immutable assignment explicitly "
    "authorizes that exact action. Emit heartbeats as direct bounded progress "
    "calls only; never create a background or autonomous heartbeat loop. Make "
    "task-start the first worker transition after reading this assignment and "
    "verifying its scope cwd, worktree, branch, and base. Invoke the exact "
    "embedded helper_path with the embedded workspace_manifest and pass the "
    "embedded assignment_sha256 unchanged; task-start performs authoritative "
    "canonical digest validation, so never recompute it with ad hoc JSON. Read the "
    "incoming handoff and perform deeper preflight only after task-start. Treat "
    "the immutable assignment and incoming handoff as the complete task context; "
    "do not reread the full managed prompt or coordinator-only state. Publish the "
    "worker result only after verifying that no canonical docs/requirements.md, "
    "docs/design.md, project AGENTS.md, README.md, or CHANGELOG.md path changed; "
    "those shared paths remain coordinator-owned even when a broad task write "
    "claim lexically contains them. Route required documentation or contract "
    "updates through typed spec_gaps in the result for root-coordinator "
    "reconciliation. Inherit root_intent_sha256 and project_spec_receipt exactly; "
    "never independently reclassify user intent, edit project specs, or claim a "
    "gap was accepted. Publish the "
    "worker result only through the exact transient result_context returned by "
    "task-start or task-recover, using its publication_cwd as the explicit external "
    "working directory and its exact draft_path and publish_argv for canonical "
    "digesting and atomic publication. Stop before any unassigned side effect."
)
INCOMING_HANDOFF_SCHEMA = "task-implementer/incoming-handoff-v1"
LEGACY_EXECUTION_SCHEMA = "task-implementer/execution-plane-v1"
TASK_ID_RE = re.compile(r"task-([1-9][0-9]*)")
SHA_RE = re.compile(r"[0-9a-f]{40,64}")
WAVE_STATES = (
    "planned",
    "preparing",
    "running",
    "integrating",
    "promotion_pending",
    "promoted",
    "cleanup",
    "done",
    "blocked",
)
TASK_STATES = (
    "planned",
    "assigned",
    "running",
    "committed",
    "merged",
    "failed",
    "superseded",
)
ACTIVE_WAVE_STATES = set(WAVE_STATES) - {"done", "blocked"}
EXCLUSIVE_CONFLICT_CLASSES = {
    "external-database",
    "external-kubernetes",
    "external-terraform",
    "migration-execution",
    "publication",
}


@dataclass(frozen=True)
class WriteClaim:
    kind: str
    path: str


@dataclass(frozen=True)
class TaskPlan:
    task_id: str
    position: int
    dependencies: tuple[str, ...]
    write_claims: tuple[WriteClaim, ...]
    conflict_domains: tuple[str, ...]
    requirement_ids: str
    design_id: str
    goal: str
    plan: str
    implementation_steps: str
    validation: str
    end_to_end_validation: str
    done_criteria: str
    rollback_notes: str
    stop_conditions: str
    ownership_known: bool


def worker_liveness_profile(
    dependencies: tuple[str, ...] | list[str],
) -> dict[str, object]:
    """Return the immutable liveness profile derived from task dependencies."""

    if dependencies:
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


def optional_field_block(section: str, label: str) -> str:
    try:
        return field_block(section, label)
    except PromptWorkspaceError:
        return ""


def sha256_json(value: object) -> str:
    return hashlib.sha256(stable_json(value)).hexdigest()


def field_block(section: str, label: str) -> str:
    match = re.search(
        rf"(?ms)^- {re.escape(label)}:\s*(.*?)(?=^- [A-Za-z][^:\n]*:\s*|^### |^## |\Z)",
        section,
    )
    if match is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"task is missing {label}"
        )
    return match.group(1).strip()


def task_sections(text: str) -> tuple[list[str], dict[str, str]]:
    queue = markdown_section(text, "Task Queue")
    matches = list(re.finditer(r"(?m)^### (task-[1-9][0-9]*)\s*$", queue))
    if not matches:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "handoff task queue is empty"
        )
    order: list[str] = []
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        task_id = match.group(1)
        if task_id in sections:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "handoff repeats a task ID"
            )
        end = matches[index + 1].start() if index + 1 < len(matches) else len(queue)
        order.append(task_id)
        sections[task_id] = queue[match.end() : end]
    return order, sections


def task_statuses(sections: dict[str, str]) -> dict[str, str]:
    allowed = {"pending", "in_progress", "done", "blocked", "superseded"}
    statuses: dict[str, str] = {}
    for task_id, section in sections.items():
        status = field_block(section, "Status").split()[0]
        if status not in allowed:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", f"{task_id} has an invalid status"
            )
        statuses[task_id] = status
    return statuses


def task_dependencies(
    section: str, known_tasks: set[str], task_id: str
) -> tuple[str, ...]:
    value = field_block(section, "Depends on")
    if value.casefold() in {"", "none"}:
        return ()
    dependencies = re.findall(r"task-[1-9][0-9]*", value)
    residual = re.sub(r"task-[1-9][0-9]*", "", value).strip(" ,\n")
    if not dependencies or residual or len(dependencies) != len(set(dependencies)):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{task_id} dependencies are malformed"
        )
    if task_id in dependencies:
        raise PromptWorkspaceError("DEPENDENCY_CYCLE", f"{task_id} depends on itself")
    if any(dependency not in known_tasks for dependency in dependencies):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{task_id} has an unknown dependency"
        )
    return tuple(dependencies)


def _repo_path(value: str, label: str) -> str:
    if value.startswith("/") or "\\" in value:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{label} is not repo-relative"
        )
    path = PurePosixPath(value)
    normalized = path.as_posix().rstrip("/")
    if (
        value in {"", "."}
        or ".." in path.parts
        or any(not part for part in path.parts)
        or value.rstrip("/") != normalized
        or path.parts[0] == ".git"
    ):
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", f"{label} is unsafe")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{label} has control bytes"
        )
    return normalized


def parse_write_claims(section: str) -> tuple[tuple[WriteClaim, ...], bool]:
    value = field_block(section, "Write claims")
    if value.casefold() in {"", "unknown", "none"}:
        return (), False
    claims: list[WriteClaim] = []
    for raw_line in value.splitlines():
        item = raw_line.strip()
        if item.startswith("- "):
            item = item[2:].strip()
        if item.startswith("`") and item.endswith("`"):
            item = item[1:-1]
        if re.search(r";\s*(?:exact|prefix):", item):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "each write claim must appear on its own line",
            )
        match = re.fullmatch(r"(exact|prefix):\s*(.+)", item)
        if match is None:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "Write claims must use `exact: path` or `prefix: directory`",
            )
        claim = WriteClaim(
            match.group(1), _repo_path(match.group(2).strip(), "write claim")
        )
        if claim in claims:
            raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "write claims repeat")
        claims.append(claim)
    return tuple(claims), bool(claims)


def parse_conflict_domains(section: str) -> tuple[tuple[str, ...], bool]:
    value = field_block(section, "Conflict domains")
    if value.casefold() in {"", "unknown", "none"}:
        return (), False
    domains: list[str] = []
    for raw_line in value.splitlines():
        item = raw_line.strip()
        if item.startswith("- "):
            item = item[2:].strip()
        if item.startswith("`") and item.endswith("`"):
            item = item[1:-1]
        if re.fullmatch(r"[a-z][a-z0-9-]*:[A-Za-z0-9._/@+-]+", item) is None:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "conflict domains must use `class:stable-key`",
            )
        if item in domains:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "conflict domains repeat"
            )
        domains.append(item)
    return tuple(domains), bool(domains)


def parse_task_plans(text: str) -> list[TaskPlan]:
    order, sections = task_sections(text)
    statuses = task_statuses(sections)
    known = set(order)
    plans: list[TaskPlan] = []
    for position, task_id in enumerate(order):
        if statuses[task_id] not in {"pending", "in_progress"}:
            continue
        section = sections[task_id]
        claims, claims_known = parse_write_claims(section)
        domains, domains_known = parse_conflict_domains(section)
        dependencies = task_dependencies(section, known, task_id)
        if any(
            statuses[dependency] not in {"done", "pending", "in_progress"}
            for dependency in dependencies
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                f"{task_id} depends on a blocked or superseded task",
            )
        implementation_steps = field_block(section, "Implementation steps")
        validation = field_block(section, "Validation")
        end_to_end_validation = field_block(section, "End-to-end validation")
        done_criteria = field_block(section, "Done criteria")
        if not all(
            (implementation_steps, validation, end_to_end_validation, done_criteria)
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                f"{task_id} lacks a self-contained implementation contract",
            )
        plans.append(
            TaskPlan(
                task_id=task_id,
                position=position,
                dependencies=dependencies,
                write_claims=claims,
                conflict_domains=domains,
                requirement_ids=optional_field_block(section, "Requirement IDs"),
                design_id=optional_field_block(section, "Design ID"),
                goal=optional_field_block(section, "Goal"),
                plan=optional_field_block(section, "Plan"),
                implementation_steps=implementation_steps,
                validation=validation,
                end_to_end_validation=end_to_end_validation,
                done_criteria=done_criteria,
                rollback_notes=optional_field_block(section, "Rollback notes"),
                stop_conditions=optional_field_block(section, "Stop conditions"),
                ownership_known=claims_known and domains_known,
            )
        )
    return plans


def claims_overlap(left: WriteClaim, right: WriteClaim) -> bool:
    left_path = PurePosixPath(left.path)
    right_path = PurePosixPath(right.path)
    if left.kind == right.kind == "exact":
        return (
            left_path == right_path
            or left_path in right_path.parents
            or right_path in left_path.parents
        )
    if left.kind == "prefix" and right.kind == "prefix":
        return (
            left_path == right_path
            or left_path in right_path.parents
            or right_path in left_path.parents
        )
    prefix, exact = (
        (left_path, right_path) if left.kind == "prefix" else (right_path, left_path)
    )
    return prefix == exact or prefix in exact.parents


def tasks_conflict(left: TaskPlan, right: TaskPlan) -> bool:
    if not left.ownership_known or not right.ownership_known:
        return True
    if any(claims_overlap(a, b) for a in left.write_claims for b in right.write_claims):
        return True
    if set(left.conflict_domains) & set(right.conflict_domains):
        return True
    for task in (left, right):
        if any(
            domain.split(":", 1)[0] in EXCLUSIVE_CONFLICT_CLASSES
            for domain in task.conflict_domains
        ):
            return True
    return False


def build_dependency_waves(tasks: list[TaskPlan]) -> list[list[TaskPlan]]:
    if not tasks:
        return []
    by_id = {task.task_id: task for task in tasks}
    if len(by_id) != len(tasks):
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "task IDs repeat")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise PromptWorkspaceError(
                "DEPENDENCY_CYCLE", "task dependency graph contains a cycle"
            )
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id].dependencies:
            if dependency in by_id:
                visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task in tasks:
        visit(task.task_id)

    waves: list[list[TaskPlan]] = []
    wave_index: dict[str, int] = {}
    unresolved = list(sorted(tasks, key=lambda item: item.position))
    while unresolved:
        progressed = False
        for task in tuple(unresolved):
            internal_dependencies = [
                item for item in task.dependencies if item in by_id
            ]
            if any(item not in wave_index for item in internal_dependencies):
                continue
            earliest = 0
            if internal_dependencies:
                earliest = max(wave_index[item] for item in internal_dependencies) + 1
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
            raise PromptWorkspaceError(
                "DEPENDENCY_CYCLE", "task dependency graph cannot advance"
            )
    return waves


def batches_for_wave(tasks: list[TaskPlan], capacity: int) -> list[list[TaskPlan]]:
    if capacity < 1:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "worker capacity must be positive"
        )
    return [tasks[index : index + capacity] for index in range(0, len(tasks), capacity)]


def orchestration_dir(run_dir: Path) -> Path:
    return run_dir / "orchestration"


def _legacy_artifacts(run_dir: Path) -> list[Path]:
    legacy = run_dir / "execution"
    return (
        sorted(legacy.glob("*.json"))
        if legacy.is_dir() and not legacy.is_symlink()
        else []
    )


def assert_no_unfinished_v1(run_dir: Path) -> None:
    artifacts = _legacy_artifacts(run_dir)
    if not artifacts:
        return
    for path in artifacts:
        value = load_json_object(path, "legacy execution plane")
        if value.get("schema") != LEGACY_EXECUTION_SCHEMA:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "unknown execution artifact"
            )
    raise PromptWorkspaceError(
        "WORKFLOW_UPGRADE_REQUIRED",
        "execution-plane-v1 is unsupported; start a new v7 run",
    )


def load_coordinator_state(run_dir: Path) -> dict[str, object] | None:
    assert_no_unfinished_v1(run_dir)
    path = orchestration_dir(run_dir) / "coordinator.json"
    if not path.exists():
        return None
    value = load_json_object(path, "coordinator state")
    if value.get("schema") in {
        "task-implementer/coordinator-v1",
        "task-implementer/coordinator-v2",
        "task-implementer/coordinator-v3",
        "task-implementer/coordinator-v4",
        "task-implementer/coordinator-v5",
        "task-implementer/coordinator-v6",
    }:
        raise PromptWorkspaceError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "legacy coordinator state is unsupported; start a new v7 run",
        )
    required = {
        "schema",
        "run_id",
        "base_branch",
        "initial_head",
        "default_remote",
        "default_branch",
        "default_ref",
        "default_head",
        "promotion_source",
        "prompt_revision",
        "prompt_intent_sha256",
        "plan_sha256",
        "waves",
        "active_wave",
        "status",
        "created_at",
        "updated_at",
    }
    if (
        set(value) != required
        or value.get("schema") != COORDINATOR_SCHEMA
        or value.get("run_id") != run_dir.name
        or re.fullmatch(r"r[0-9]{4}", str(value.get("prompt_revision") or "")) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("prompt_intent_sha256") or ""))
        is None
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "coordinator state is invalid"
        )
    if not isinstance(value.get("waves"), list) or not value["waves"]:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "coordinator wave index is invalid"
        )
    if value.get("status") not in {"running", "blocked", "done"}:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "coordinator status is invalid"
        )
    lane_identity_valid = value.get("promotion_source") == "managed-local" and all(
        value.get(field) is None
        for field in (
            "default_remote",
            "default_branch",
            "default_ref",
            "default_head",
        )
    )
    if not lane_identity_valid:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "coordinator promotion identity is invalid"
        )
    return value


def coordinator_active(run_dir: Path) -> bool:
    state = load_coordinator_state(run_dir)
    return state is not None and state["status"] == "running"
