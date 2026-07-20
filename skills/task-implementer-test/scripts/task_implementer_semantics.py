#!/usr/bin/env python3
"""Validate live evidence against Git and canonical Task Implementer state."""

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
from datetime import datetime
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
    "ff_only_promotion",
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
    "plan",
    "implementation_steps",
    "validation",
    "end_to_end_validation",
    "done_criteria",
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
    if value.get("schema") != "task-implementer-test/application-evidence-v1":
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
        if lifecycle.get("owner") != "task-implementer-test":
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


def _validate_orchestration(
    project: Path,
    run_dir: Path,
    scripts: Path,
    managed_prompt: Path,
    expected_head: Any,
) -> list[str]:
    errors: list[str] = []
    try:
        if (
            project.is_symlink()
            or run_dir.is_symlink()
            or scripts.is_symlink()
            or managed_prompt.is_symlink()
        ):
            raise ValueError("symlinked validation roots are forbidden")
        if not all(path.is_dir() for path in (project, run_dir, scripts)):
            raise ValueError("validation roots must exist")
        sys.path.insert(0, str(scripts.resolve()))
        execution = importlib.import_module("prompt_workspace_execution")
        waves_module = importlib.import_module("prompt_workspace_waves")
        coordinator = execution.load_coordinator_state(run_dir)
        if coordinator is None or coordinator.get("status") != "done":
            raise ValueError("coordinator is not done")
        if coordinator.get("active_wave") is not None:
            raise ValueError("coordinator still has an active wave")
        wave_index = coordinator.get("waves")
        dependency_errors = _dependency_contract_errors(wave_index)
        if dependency_errors:
            raise ValueError("; ".join(dependency_errors))
        final_head: Any = None
        sessions: set[str] = set()
        for position, record in enumerate(wave_index):
            wave_id = str(record.get("wave_id"))
            wave = waves_module._load_wave(run_dir, wave_id)
            if wave.get("status") != "done" or wave.get("cleanup_retained") != []:
                raise ValueError(f"{wave_id} is not done and cleaned")
            final_head = wave.get("promoted_head")
            for task in record.get("tasks", []):
                task_id = str(task.get("task_id"))
                plane = waves_module._load_task_plane(run_dir, wave_id, task_id)
                if (
                    plane.get("state") != "merged"
                    or not plane.get("commit")
                    or not plane.get("assignment_sha256")
                    or not plane.get("result_sha256")
                    or len(plane.get("worker_session_sha256_history", [])) != 1
                ):
                    raise ValueError(f"{task_id} lacks merged single-session evidence")
                session = str(plane.get("worker_session_sha256"))
                if session in sessions:
                    raise ValueError("worker session was reused across tasks")
                sessions.add(session)
                assignment_path = (
                    run_dir
                    / "orchestration"
                    / "assignments"
                    / wave_id
                    / f"{task_id}.json"
                )
                incoming_path = (
                    run_dir
                    / "orchestration"
                    / "incoming-handoffs"
                    / wave_id
                    / f"{task_id}.json"
                )
                assignment = waves_module._validated_assignment(assignment_path)
                waves_module._validated_incoming_handoff(incoming_path)
                liveness = execution.worker_liveness_profile(
                    task.get("dependencies", [])
                )
                if _assignment_context_errors(task, assignment, liveness):
                    raise ValueError(
                        f"{task_id} lacks its self-contained worker profile"
                    )
        handoff = (run_dir / "handoff.md").read_text(encoding="utf-8")
        if (
            "- Overall status: done" not in handoff
            or "## Final Alignment" not in handoff
        ):
            raise ValueError("terminal handoff lacks final alignment")
        invoked_match = re.search(r"(?m)^- Last invoked at:\s*(\S+)\s*$", handoff)
        completed_values = re.findall(r"(?m)^- Completed at:\s*(\S+)\s*$", handoff)
        completed_values = [value for value in completed_values if value != "none"]
        if invoked_match is None or not completed_values:
            raise ValueError("handoff lacks invocation timing evidence")
        invoked = datetime.fromisoformat(invoked_match.group(1))
        completed = datetime.fromisoformat(completed_values[-1])
        if (
            invoked.utcoffset() is None
            or completed.utcoffset() is None
            or invoked < completed
        ):
            raise ValueError("no unchanged invocation is recorded after completion")
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        revisions = manifest.get("revisions")
        if not isinstance(revisions, list) or not revisions:
            raise ValueError("run manifest has no prompt revision")
        prompt_digest = hashlib.sha256(managed_prompt.read_bytes()).hexdigest()
        if revisions[-1].get("sha256") != prompt_digest:
            raise ValueError("managed prompt changed after the completed run")
        matching_runs = 0
        for candidate in run_dir.parent.iterdir():
            manifest_path = candidate / "manifest.json"
            if (
                candidate.is_dir()
                and manifest_path.is_file()
                and not manifest_path.is_symlink()
            ):
                candidate_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                if candidate_manifest.get("prompt_id") == manifest.get("prompt_id"):
                    matching_runs += 1
        if matching_runs != 1:
            raise ValueError("unchanged prompt unexpectedly created another run")
        head = _git(project, "rev-parse", "HEAD")
        if head != expected_head or final_head != head:
            raise ValueError("project and promoted heads do not match")
        if _git(project, "status", "--porcelain"):
            raise ValueError("project is not clean")
        if _git(project, "remote"):
            raise ValueError("project unexpectedly has a remote")
        worktrees = _git(project, "worktree", "list", "--porcelain")
        paths = [
            line[9:] for line in worktrees.splitlines() if line.startswith("worktree ")
        ]
        if paths != [str(project.resolve())]:
            raise ValueError("Task Implementer worktrees remain")
    except (AttributeError, ImportError, OSError, TypeError, ValueError) as exc:
        errors.append(f"canonical orchestration evidence failed: {str(exc)[:300]}")
    finally:
        if sys.path and sys.path[0] == str(scripts.resolve()):
            sys.path.pop(0)
    return errors


def validate_results(
    data: Any,
    evidence_root: Path,
    expected_generation: str | None = None,
    *,
    project_root: Path | None = None,
    run_dir: Path | None = None,
    task_implementer_scripts: Path | None = None,
    managed_prompt: Path | None = None,
    lifecycle_state: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict) or set(data) != TOP_KEYS:
        return ["manifest must contain exactly the required top-level keys"]
    if data["schema"] != "task-implementer-test/live-results-v1":
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
            task_implementer_scripts,
            managed_prompt,
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
                    task_implementer_scripts.resolve(),
                    managed_prompt.resolve(),
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
    parser.add_argument("--task-implementer-scripts", type=Path)
    parser.add_argument("--managed-prompt", type=Path)
    parser.add_argument("--lifecycle-state", type=Path)
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors = validate_results(
        data,
        args.evidence_root,
        args.expected_generation,
        project_root=args.project_root,
        run_dir=args.run_dir,
        task_implementer_scripts=args.task_implementer_scripts,
        managed_prompt=args.managed_prompt,
        lifecycle_state=args.lifecycle_state,
    )
    print(json.dumps({"valid": not errors, "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
