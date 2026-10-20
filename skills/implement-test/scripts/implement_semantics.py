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
ARTIFACT_KEYS = {"application"}
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
    if (
        assignment.get("read_only_warning_seconds")
        != liveness["read_only_warning_seconds"]
    ):
        errors.append("assignment warning budget does not match its canonical task")
    if assignment.get("read_only_seconds") != liveness["read_only_seconds"]:
        errors.append("assignment read-only budget does not match its canonical task")
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
    for required in ("app/frontend", "app/api", "app/database"):
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
            claim.get("kind") == "exact" and claim.get("path") == "compose.yaml"
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
        errors.append("artifacts must contain exactly application")
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
        run_dir / "bases" / assignment["feature_id"] / (fingerprint + ".json")
    )
    if impact.canonical_digest(historical) != fingerprint:
        raise ValueError("historical assignment basis was modified")
    lineage = {"impact_sha256", "spec_receipt_sha256", "spec_transition_sha256"}
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
        ):
            raise ValueError("impact ancestry lacks a valid owner reconciliation")
        current = parent


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
        if run["status"] != "done" or run["project_root"] != str(project):
            raise ValueError(
                "accepted objective has not completed at the selected project"
            )
        coordinator = execution._load_coordinator(run_dir, "FEAT-001")
        if coordinator["status"] != "done" or coordinator["active_wave"] is not None:
            raise ValueError("execution is not delivered")
        basis = impact.verify_execution(
            run_dir, "FEAT-001", coordinator["plan_digest"], project
        )
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
                or wave["merged_task_ids"] != wave["task_ids"]
            ):
                raise ValueError("wave lacks stable ordered integration and cleanup")
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
                    or len(task["worker_session_hash_history"]) != 1
                ):
                    raise ValueError(
                        "task lacks current immutable admission and isolated ownership"
                    )
                session = task["worker_session_hash"]
                if session in sessions:
                    raise ValueError("native worker session was reused")
                sessions.add(session)
                if _assignment_context_errors(
                    task["task"],
                    assignment,
                    execution.worker_liveness_profile(task["task"]["dependencies"]),
                ):
                    raise ValueError("worker assignment is not self-contained")
                if _git(
                    project, "rev-list", "--parents", "-n", "1", result["commit"]
                ).split() != [result["commit"], assignment["base_head"]]:
                    raise ValueError(
                        "task commit is not a direct child of its frozen wave base"
                    )
        from public_workflow_trial import validate_receipt

        validate_receipt(
            invocation_receipt, run_dir, objective["revisions"][-1]["sha256"]
        )
        delivered = Path(
            coordinator.get("delivered_checkout", coordinator["project_root"])
        )
        if (
            _git(delivered, "rev-parse", "HEAD") != expected_head
            or coordinator["promoted_head"] != expected_head
            or _git(delivered, "status", "--porcelain")
        ):
            raise ValueError("delivered Git identity changed")
        if coordinator["delivery_mode"] == "source" and _git(
            project, "merge-base", "--is-ancestor", run["source_head"], expected_head
        ):
            raise ValueError("source delivery ancestry is invalid")
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
    if data["schema"] != "implement-test/live-results-v1":
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
