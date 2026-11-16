#!/usr/bin/env python3
"""Validate live evidence against Git and canonical Implement state."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath
from typing import Any


TOP_KEYS = {
    "schema",
    "generation_id",
    "status",
    "project_head",
    "orchestration",
    "application",
    "artifacts",
}
ORCHESTRATION_KEYS = {
    "dependency_waves",
    "worker_isolation",
    "reviewed_commits",
    "ordered_integration",
    "local_delivery",
    "direct_intake",
    "immutable_basis",
    "slot_refill",
    "native_overlap",
    "final_alignment",
    "already_complete",
}
APPLICATION_KEYS = {
    "frontend",
    "api_crud",
    "database_correlation",
    "restart_persistence",
    "loopback_only",
    "database_private",
}
ARTIFACT_KEYS = {"application", "browser"}
APPLICATION_EVIDENCE_KEYS = {
    "schema",
    "generation_id",
    "services",
    "web_port",
    "frontend_body_sha256",
    "created_task",
    "database_task",
    "persisted_after_restart",
}
ASSIGNMENT_CONTEXT_KEYS = (
    "dependencies",
    "goal",
    "validation",
    "done_criteria",
    "requirements",
    "write_claims",
    "conflict_domains",
)


def _assignment_context_errors(
    task: dict[str, Any],
    assignment: dict[str, Any],
    liveness: dict[str, object],
) -> list[str]:
    errors = [
        f"assignment {key} does not match its canonical task"
        for key in ASSIGNMENT_CONTEXT_KEYS
        if assignment.get(key) != task.get(key)
    ]
    if assignment.get("worker_profile") != liveness["worker_profile"]:
        errors.append("assignment worker profile does not match its canonical task")
    if assignment.get("health_policy") != liveness["health_policy"]:
        errors.append("assignment health policy does not match its canonical task")
    return errors


def _claims_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_kind, right_kind = left.get("kind"), right.get("kind")
    left_path, right_path = left.get("path"), right.get("path")
    if left_kind not in {"exact", "prefix"} or right_kind not in {
        "exact",
        "prefix",
    }:
        return True
    if not isinstance(left_path, str) or not isinstance(right_path, str):
        return True
    if left_kind == right_kind == "exact":
        return left_path == right_path
    if left_kind == right_kind == "prefix":
        return (
            left_path == right_path
            or left_path.startswith(right_path + "/")
            or right_path.startswith(left_path + "/")
        )
    if left_kind == "prefix":
        return right_path == left_path or right_path.startswith(left_path + "/")
    return left_path == right_path or left_path.startswith(right_path + "/")


def _claim_covers(claim: dict[str, Any], required: str) -> bool:
    path = claim.get("path")
    if not isinstance(path, str):
        return False
    if claim.get("kind") == "prefix":
        return required == path or required.startswith(path + "/")
    return required == path or path.startswith(required + "/")


def _dependency_contract_errors(wave_index: Any) -> list[str]:
    if not isinstance(wave_index, list) or len(wave_index) < 2:
        return ["at least two dependency waves are required"]
    first_tasks = wave_index[0].get("tasks")
    if not isinstance(first_tasks, list) or not first_tasks:
        return ["first dependency wave has no tasks"]
    errors: list[str] = []
    for index, left in enumerate(first_tasks):
        left_claims = left.get("write_claims")
        if not isinstance(left_claims, list):
            errors.append("first-wave task has invalid write claims")
            continue
        for right in first_tasks[index + 1 :]:
            right_claims = right.get("write_claims")
            if not isinstance(right_claims, list):
                errors.append("first-wave task has invalid write claims")
                continue
            if any(
                _claims_overlap(left_claim, right_claim)
                for left_claim in left_claims
                for right_claim in right_claims
            ):
                errors.append("first-wave task write claims overlap")
    tier_task_ids: set[str] = set()
    for required in ("project-a/app/frontend", "project-a/app/api", "project-a/app/database"):
        owners = {
            str(task.get("task_id"))
            for task in first_tasks
            if isinstance(task.get("write_claims"), list)
            and any(_claim_covers(claim, required) for claim in task["write_claims"])
        }
        owners.discard("None")
        if len(owners) != 1:
            errors.append(f"first wave must have one owner for {required}")
        tier_task_ids.update(owners)
    if len(tier_task_ids) != 3:
        errors.append("first wave must have three distinct tier task owners")
    integration_tasks = [
        task
        for wave in wave_index[1:]
        for task in wave.get("tasks", [])
        if isinstance(task.get("write_claims"), list)
        and any(
            claim.get("kind") == "exact" and claim.get("path") == "project-a/compose.yaml"
            for claim in task["write_claims"]
        )
    ]
    if len(integration_tasks) != 1:
        errors.append("later waves must have one exact compose.yaml owner")
    elif not tier_task_ids.issubset(set(integration_tasks[0].get("dependencies", []))):
        errors.append("integration task must depend on every first-wave tier task")
    return errors


def _boolean_map(value: Any, keys: set[str], label: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        errors.append(f"{label} must contain exactly the required keys")
        return
    for key in sorted(keys):
        if not isinstance(value[key], bool):
            errors.append(f"{label}.{key} must be boolean")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(
    artifacts: Any, evidence_root: Path, errors: list[str]
) -> tuple[dict[str, Any], str] | None:
    if not isinstance(artifacts, dict) or set(artifacts) != ARTIFACT_KEYS:
        errors.append("artifacts must contain exactly application and browser")
        return None
    item = artifacts["application"]
    if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
        errors.append("application artifact must contain path and sha256")
        return None
    relative = item.get("path")
    digest = item.get("sha256")
    if not isinstance(relative, str) or relative != "evidence/application.json":
        errors.append("application artifact path must be evidence/application.json")
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        errors.append("application artifact path is unsafe")
        return None
    candidate = evidence_root.parent / Path(*pure.parts)
    root = evidence_root.resolve()
    if candidate.is_symlink() or not candidate.is_file():
        errors.append("application artifact is missing or symlinked")
        return None
    resolved = candidate.resolve()
    if os.path.commonpath((str(root), str(resolved))) != str(root):
        errors.append("application artifact escapes evidence root")
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        errors.append("application artifact digest is invalid")
        return None
    if _sha256(candidate) != digest:
        errors.append("application artifact digest does not match")
        return None
    value = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != APPLICATION_EVIDENCE_KEYS:
        errors.append("application evidence has an invalid shape")
        return None
    return value, digest


def _validate_application(
    value: dict[str, Any], generation: Any, lifecycle: dict[str, Any] | None = None
) -> list[str]:
    errors: list[str] = []
    if value.get("schema") != "implement-test/application-evidence-v1":
        errors.append("application evidence schema is invalid")
    if value.get("generation_id") != generation:
        errors.append("application evidence generation does not match")
    if value.get("services") != ["api", "db", "frontend"]:
        errors.append("application evidence must prove exactly three live services")
    port = value.get("web_port")
    if type(port) is not int or not 1 <= port <= 65535:
        errors.append("application evidence has an invalid loopback port")
    if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("frontend_body_sha256", ""))):
        errors.append("application evidence lacks frontend content proof")
    created = value.get("created_task")
    database = value.get("database_task")
    if (
        not isinstance(created, dict)
        or set(created) != {"id", "title", "completed"}
        or type(created.get("id")) is not int
        or created.get("title") != "Verifier task"
        or created.get("completed") is not True
    ):
        errors.append("application evidence does not prove API create/update")
    if database != created:
        errors.append("database evidence does not correlate to the API task")
    if value.get("persisted_after_restart") is not True:
        errors.append("application evidence does not prove restart persistence")
    if lifecycle is not None:
        if lifecycle.get("owner") != "implement-test":
            errors.append("application evidence lifecycle owner does not match")
        if lifecycle.get("generation_id") != generation:
            errors.append("application evidence lifecycle generation does not match")
        if lifecycle.get("live_started") is not True:
            errors.append("application evidence lifecycle was not live")
        if lifecycle.get("web_port") != port:
            errors.append("application evidence port does not match lifecycle state")
    return errors


def _git(project: Path, *arguments: str) -> str:
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    completed = subprocess.run(
        ["git", *arguments],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError((completed.stderr or completed.stdout or "git failed")[:300])
    return completed.stdout.strip()


def _validate_admission_basis(run_dir, assignment, admission, basis, impact, state):
    """Bind completed work to its basis and the final owner reconciliation chain."""
    fingerprint = assignment["basis_sha256"]
    if admission.get("basis_sha256") != fingerprint:
        raise ValueError("admission does not bind the assignment basis")
    historical = state.read(
        run_dir / "bases" / assignment["execution_id"] / (fingerprint + ".json")
    )
    if impact.canonical_digest(historical) != fingerprint:
        raise ValueError("historical assignment basis was modified")
    lineage = {"impact_sha256", "spec_receipt_sha256", "spec_transition_sha256", "plan_action"}
    if any(value.get("plan_action") not in {"retain_plan", "replan_required"}
           for value in (historical, basis)):
        raise ValueError("completed assignment has a different execution contract")
    if ({k: v for k, v in historical.items() if k not in lineage}
            != {k: v for k, v in basis.items() if k not in lineage}):
        raise ValueError("completed assignment has a different execution contract")
    if historical == basis:
        return

    # Final owner reconciliation may advance spec receipts after all workers
    # finish. It must not change any task, objective, helper or input identity.
    paths = list((run_dir / "objective-impact").glob("attempt-*.json"))
    if not 1 <= len(paths) <= 256 or sum(p.lstat().st_size for p in paths) > 16 * 1024 * 1024:
        raise ValueError("impact history exceeds verifier bounds")
    receipts = {}
    for path in paths:
        receipt = impact._read(path, "historical objective impact receipt")
        generation = receipt.get("generation")
        if type(generation) is not int or generation < 1 or path.name != f"attempt-{generation:04d}.json":
            raise ValueError("impact history has an invalid generation")
        receipts[impact.canonical_digest(receipt)] = receipt

    def bound_receipt(value):
        receipt = receipts.get(value["impact_sha256"])
        if receipt is None or any(receipt.get(k) != value.get(k) for k in lineage - {"impact_sha256"}):
            raise ValueError("execution basis lacks its hash-bound impact receipt")
        return receipt

    previous = bound_receipt(historical)
    current = bound_receipt(basis)
    receipt_changes = {
        "generation", "prior_impact_sha256", "requirements_sha256", "design_sha256",
        "spec_receipt_sha256", "spec_transition", "spec_transition_sha256",
        "coverage", "effects", "plan_action",
    }
    identity = {k: v for k, v in previous.items() if k not in receipt_changes}
    visited = set()
    while impact.canonical_digest(current) != historical["impact_sha256"]:
        digest = impact.canonical_digest(current)
        if digest in visited or {k: v for k, v in current.items() if k not in receipt_changes} != identity:
            raise ValueError("impact ancestry changes the accepted execution contract")
        visited.add(digest)
        transition = current.get("spec_transition")
        parent = receipts.get(current.get("prior_impact_sha256"))
        if (
            not isinstance(transition, dict)
            or set(transition) != {"reason", "prior_impact_sha256", "prior_spec_receipt_sha256", "next_spec_receipt_sha256"}
            or impact.canonical_digest(transition) != current.get("spec_transition_sha256")
            or transition["reason"] != "owner_reconciliation"
            or parent is None
            or parent["generation"] >= current["generation"]
            or transition["prior_impact_sha256"] != current["prior_impact_sha256"]
            or transition["prior_spec_receipt_sha256"] != parent.get("spec_receipt_sha256")
            or transition["next_spec_receipt_sha256"] != current.get("spec_receipt_sha256")
            or transition["prior_spec_receipt_sha256"] == transition["next_spec_receipt_sha256"]
            or not _same_or_fulfilled_coverage(parent, current)
        ):
            raise ValueError("impact ancestry lacks a valid owner reconciliation")
        current = parent


def _same_or_fulfilled_coverage(previous, current):
    """Permit final coverage fulfillment, never new statements or new effects."""
    fields = ("coverage", "effects", "plan_action")
    if all(previous.get(key) == current.get(key) for key in fields):
        return True
    before, after = previous.get("coverage"), current.get("coverage")
    if (current.get("plan_action") != "retain_plan" or current.get("effects") != []
            or not isinstance(before, list) or not before
            or not isinstance(after, list) or len(before) != len(after)):
        return False
    identity = ("statement", "category", "statement_sha256")
    return all(
        isinstance(old, dict) and isinstance(new, dict)
        and all(old.get(key) == new.get(key) for key in identity)
        and new.get("effects") == []
        and new.get("disposition") in {"existing_contract", "non_contract"}
        for old, new in zip(before, after, strict=True)
    )


def _validate_worker_history(run_dir, assignment, task, result, admission, execution, state):
    """Replay retained recovery authority without reviving removed worktrees."""
    from workflow_worker_results import read_receipt, receipt_path

    history = execution._validated_session_history(task)
    if (not 1 <= len(history) <= 256 or type(task.get("attempt")) is not int
            or task["attempt"] != len(history) or task.get("status") != "finished"
            or result.get("attempt") != task["attempt"]
            or result.get("worker_session_hash") != history[-1]
            or task.get("commit") != result.get("commit")):
        raise ValueError("completed worker session history is invalid")
    root = run_dir / "execution" / assignment["execution_id"]

    def termination(path, digest, worker, observer=None, *, admission_digest=False):
        path = Path(str(path))
        if path.parent != root / "terminations":
            raise ValueError("worker termination escapes its execution")
        value = state.read(path)
        hash_value = execution.sha256_json(value) if admission_digest else state.digest(value)
        if (hash_value != digest or value.get("schema") != "workflow/worker-termination-v1"
                or value.get("assignment_digest") != assignment["assignment_digest"]
                or value.get("worker_session_hash") != worker
                or value.get("status") != "terminated"
                or not value.get("native_handle") or not value.get("observation")
                or re.fullmatch(r"[0-9a-f]{64}", str(value.get("observer_session_hash"))) is None
                or observer is not None and value["observer_session_hash"] != observer):
            raise ValueError("worker termination evidence changed")
        return value

    if (admission.get("schema") != "workflow/result-admission-v1"
            or admission.get("task_id") != assignment["task_id"]):
        raise ValueError("worker admission identity changed")
    termination(admission.get("termination_path"), admission.get("termination_sha256"),
                history[-1], admission_digest=True)
    _, final = read_receipt(assignment, task)
    if final["receipt_sha256"] != result.get("worker_result_receipt_sha256"):
        raise ValueError("completed worker result receipt changed")

    expected = {
        "schema", "assignment_digest", "attempt", "prior_worker_session_hash",
        "next_worker_session_hash", "coordinator_session_hash", "termination_path",
        "termination_sha256", "worker_result_receipt_sha256", "repair_authority",
    }
    for attempt, (prior, current) in enumerate(zip(history, history[1:]), 1):
        path = receipt_path(assignment, attempt)
        failed = None
        if path.exists() or path.is_symlink():
            _, failed = read_receipt(assignment, {"attempt": attempt, "worker_session_hash": prior})
            if failed["worker_result"]["status"] != "blocked":
                raise ValueError("recovery follows a non-blocked worker result")
        paths = list((root / "recoveries").glob(f"{assignment['task_id']}-{attempt}-*.json"))
        if not 1 <= len(paths) <= 256 or sum(p.lstat().st_size for p in paths) > 4 * 1024 * 1024:
            raise ValueError("worker recovery history is missing or exceeds bounds")
        authorized = False
        for path in paths:
            value = state.read(path)
            observer = value.get("coordinator_session_hash")
            if (set(value) != expected or value.get("schema") != "workflow/recovery-authorization-v2"
                    or re.fullmatch(r"[0-9a-f]{64}", str(observer)) is None
                    or path.name != f"{assignment['task_id']}-{attempt}-{observer}.json"
                    or value.get("assignment_digest") != assignment["assignment_digest"]
                    or value.get("attempt") != attempt
                    or value.get("prior_worker_session_hash") != prior
                    or value.get("worker_result_receipt_sha256") != (failed["receipt_sha256"] if failed else None)
                    or value.get("repair_authority") is not None):
                raise ValueError("worker recovery authority changed")
            termination(value["termination_path"], value["termination_sha256"], prior, observer)
            authorized |= value.get("next_worker_session_hash") == current
        if not authorized:
            raise ValueError("worker recovery lacks authority for its fresh session")
    return set(history)


def _validate_orchestration(
    project: Path,
    run_dir: Path,
    scripts: Path,
    invocation_receipt: Path,
    expected_head: Any,
) -> list[str]:
    errors = []
    expected_scripts = Path(__file__).resolve().parents[2] / "worktree/scripts"
    try:
        if scripts != expected_scripts or any(
            path.is_symlink()
            for path in (project, run_dir, scripts, invocation_receipt)
        ):
            raise ValueError(
                "validation must use canonical Worktree helpers and owned regular evidence"
            )
        sys.path.insert(0, str(scripts))
        execution = importlib.import_module("workflow_execution")
        objectives = importlib.import_module("workflow_objectives")
        impact = importlib.import_module("workflow_impact")
        state = importlib.import_module("workflow_state")
        run, objective = objectives.validate_run(run_dir, owner="implement")
        if run["status"] != "done" or run.get("logical_project_root") != str(project / "project-a"):
            raise ValueError(
                "accepted objective has not completed at the selected project"
            )
        coordinator = execution._load_coordinator(run_dir, "FEAT-001")
        if coordinator["status"] != "done" or coordinator["active_wave"] is not None:
            raise ValueError("execution is not delivered")
        from workflow_automatic import historical_execution, verify_delivery
        delivery = verify_delivery(run_dir)
        from implement_statistics import require_complete
        require_complete(run_dir)
        basis = historical_execution(run_dir, "FEAT-001")
        waves = [
            state.read(execution.wave_path(run_dir, "FEAT-001", wave))
            for wave in coordinator["wave_ids"]
        ]
        index = [
            {
                "tasks": [
                    state.read(
                        execution.task_path(run_dir, "FEAT-001", wave["wave_id"], task)
                    )["task"]
                    for task in wave["task_ids"]
                ]
            }
            for wave in waves
        ]
        failures = _dependency_contract_errors(index)
        if failures:
            raise ValueError("; ".join(failures))
        sessions = set()
        for wave in waves:
            if (
                wave["status"] != "done"
                or wave["cleanup_retained"]
                or wave["ready_task_ids"]
                or wave["active_task_ids"]
                or set(wave["admitted_task_ids"]) != set(wave["task_ids"])
                or wave["integrated_task_ids"] != wave["task_ids"]
            ):
                raise ValueError("wave lacks stable ordered integration and cleanup")
            from workflow_validation import verify_recorded_wave
            verify_recorded_wave(run_dir, "FEAT-001", wave, coordinator)
            for task_id in wave["task_ids"]:
                task = state.read(
                    execution.task_path(run_dir, "FEAT-001", wave["wave_id"], task_id)
                )
                assignment = state.read(
                    execution.assignment_path(
                        run_dir, "FEAT-001", wave["wave_id"], task_id
                    )
                )
                result = state.read(
                    execution.result_path(run_dir, "FEAT-001", wave["wave_id"], task_id)
                )
                execution._validate_assignment_record(assignment)
                execution._validate_result_record(result, assignment)
                admission = state.read(
                    run_dir / "execution/FEAT-001/admissions" / (task_id + ".json")
                )
                _validate_admission_basis(run_dir, assignment, admission, basis, impact, state)
                if (
                    admission["assignment_digest"] != assignment["assignment_digest"]
                    or admission["result_digest"] != result["result_digest"]
                    or assignment["base_head"] != wave["base_head"]
                ):
                    raise ValueError(
                        "task lacks current immutable admission and isolated ownership"
                    )
                history = _validate_worker_history(
                    run_dir, assignment, task, result, admission, execution, state
                )
                if sessions.intersection(history):
                    raise ValueError("native worker session was reused")
                sessions.update(history)
                if _assignment_context_errors(
                    task["task"],
                    assignment,
                    execution.worker_liveness_profile(task["task"]["dependencies"]),
                ):
                    raise ValueError("worker assignment is not self-contained")
                if result["status"] == "implemented" and _git(
                    project, "rev-list", "--parents", "-n", "1", result["commit"]
                ).split() != [result["commit"], assignment["base_head"]]:
                    raise ValueError(
                        "task commit is not a direct child of its frozen wave base"
                    )
        from public_workflow_trial import validate_receipt

        validate_receipt(
            invocation_receipt, run_dir, objective["revisions"][-1]["sha256"]
        )
        delivered = project
        if (_git(project, "rev-parse", "HEAD") != expected_head
                or _git(project, "status", "--porcelain")
                or _git(project, "merge-base", "--is-ancestor", delivery["delivered_head"], expected_head)):
            raise ValueError("aggregate Git identity changed")
        from public_workflow_trial import validate_projects
        validate_projects(invocation_receipt.parent, project, run_dir.parent.parent.parent)
        origin = project.parent / "origin.git"
        if (
            origin.is_symlink()
            or not origin.is_dir()
            or Path(_git(project, "remote", "get-url", "origin")).resolve()
            != origin.resolve()
        ):
            raise ValueError("owned local origin identity changed")
        registrations = [
            line[9:]
            for line in _git(project, "worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")
        ]
        expected = [str(project)] + ([str(delivered)] if delivered != project else [])
        if sorted(registrations) != sorted(expected):
            raise ValueError("unexpected worker worktrees remain")
        if not coordinator.get("final_evidence"):
            raise ValueError("final combined validation/alignment evidence is missing")
    except (
        AttributeError,
        ImportError,
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
    ) as error:
        errors.append("canonical orchestration evidence failed: " + str(error)[:300])
    finally:
        if sys.path and sys.path[0] == str(scripts):
            sys.path.pop(0)
    return errors


def validate_results(
    data: Any,
    evidence_root: Path,
    expected_generation: str | None = None,
    *,
    project_root: Path | None = None,
    run_dir: Path | None = None,
    workflow_scripts: Path | None = None,
    invocation_receipt: Path | None = None,
    lifecycle_state: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict) or set(data) != TOP_KEYS:
        return ["manifest must contain exactly the required top-level keys"]
    if data["schema"] != "implement-test/live-results-v2":
        errors.append("unsupported schema")
    generation = data["generation_id"]
    if not isinstance(generation, str):
        errors.append("generation_id must be a UUID string")
    else:
        try:
            if str(uuid.UUID(generation)) != generation:
                raise ValueError
        except ValueError:
            errors.append("generation_id must be a canonical UUID string")
    if expected_generation is not None and generation != expected_generation:
        errors.append("generation_id does not match the active lifecycle")
    if data["status"] not in {"PASS", "PARTIAL", "FAIL"}:
        errors.append("status must be PASS, PARTIAL, or FAIL")
    head = data["project_head"]
    if (
        not isinstance(head, str)
        or len(head) not in {40, 64}
        or any(c not in "0123456789abcdef" for c in head)
    ):
        errors.append("project_head must be a lowercase Git object ID")
    _boolean_map(data["orchestration"], ORCHESTRATION_KEYS, "orchestration", errors)
    _boolean_map(data["application"], APPLICATION_KEYS, "application", errors)
    application = _artifact(data["artifacts"], evidence_root, errors)
    lifecycle: dict[str, Any] | None = None
    if lifecycle_state is not None:
        if lifecycle_state.is_symlink() or not lifecycle_state.is_file():
            errors.append("lifecycle state is missing or symlinked")
        else:
            lifecycle = json.loads(lifecycle_state.read_text(encoding="utf-8"))
    if application is not None:
        application_value, application_digest = application
        errors.extend(_validate_application(application_value, generation, lifecycle))
        if (
            lifecycle is not None
            and lifecycle.get("application_evidence_sha256") != application_digest
        ):
            errors.append(
                "application evidence was not recorded by the lifecycle helper"
            )
    if data["status"] == "PASS":
        try:
            import implement_browser
            import implement_catalog
            if lifecycle is None or lifecycle_state is None:
                raise ValueError("browser evidence requires lifecycle ownership")
            run_path = lifecycle_state.parent
            implement_catalog.validate(lifecycle, run_path)
            browser = data["artifacts"].get("browser")
            if (not isinstance(browser, dict) or set(browser) != {"path", "sha256"}
                    or browser["path"] != "evidence/browser.json"
                    or browser["sha256"] != lifecycle.get("browser_evidence_sha256")):
                raise ValueError("browser artifact is missing or changed")
            implement_browser.validate(lifecycle, run_path)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            errors.append("browser/catalog evidence is incomplete: " + str(error)[:150])
        for group in (data["orchestration"], data["application"]):
            if isinstance(group, dict) and any(
                value is not True for value in group.values()
            ):
                errors.append("PASS requires every semantic capability to be true")
                break
        if None in {
            project_root,
            run_dir,
            workflow_scripts,
            invocation_receipt,
            lifecycle_state,
        }:
            errors.append(
                "PASS requires direct project and orchestration state validation"
            )
        else:
            errors.extend(
                _validate_orchestration(
                    project_root.resolve(),
                    run_dir.resolve(),
                    workflow_scripts.resolve(),
                    invocation_receipt.resolve(),
                    head,
                )
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--expected-generation")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--workflow-scripts", type=Path)
    parser.add_argument("--invocation-receipt", type=Path)
    parser.add_argument("--lifecycle-state", type=Path)
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors = validate_results(
        data,
        args.evidence_root,
        args.expected_generation,
        project_root=args.project_root,
        run_dir=args.run_dir,
        workflow_scripts=args.workflow_scripts,
        invocation_receipt=args.invocation_receipt,
        lifecycle_state=args.lifecycle_state,
    )
    print(json.dumps({"valid": not errors, "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
