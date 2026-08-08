#!/usr/bin/env python3
"""Stop hook that safely continues active Agentic SDLC runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

from lib.sdlc_policy import continue_with, stop
from lib.sdlc_state import (
    append_jsonl,
    git_head,
    hash_state,
    is_inside,
    load_active_run,
    load_json,
    now_iso,
    resolve_path,
    write_json_atomic,
)


TERMINAL_STATUSES = {"complete", "completed", "paused", "blocked"}
RETRY_DEFAULT = 3
COORDINATOR_SKILL = "sdlc-start"
REVALIDATION_ROUTES = {
    "requirements": "sdlc-create-requirements",
    "design": "sdlc-create-design",
    "plan": "sdlc-create-plan",
    "execution_preparation": "sdlc-prepare-execution",
    "implementation": "sdlc-implement-plan",
    "validation": "sdlc-validate-codes",
    "tests": "sdlc-unit-tests",
    "evaluation": "sdlc-evaluate",
    "documentation": "sdlc-update-documents",
    "alignment": "sdlc-align-specs",
    "commit": "sdlc-commit",
}


def _normalize_skill_name(value: str) -> str:
    aliases = {
        "align-specs": "sdlc-align-specs",
        "auto-steering": "sdlc-auto-steering",
        "classify-failure": "sdlc-classify-failure",
        "commit": "sdlc-commit",
        "create-design": "sdlc-create-design",
        "create-plan": "sdlc-create-plan",
        "prepare-execution": "sdlc-prepare-execution",
        "create-requirements": "sdlc-create-requirements",
        "evaluate": "sdlc-evaluate",
        "gather-context": "sdlc-gather-context",
        "gui-test": "sdlc-gui-test",
        "implement-plan": "sdlc-implement-plan",
        "merge-pr": "sdlc-merge-pr",
        "tdd": "sdlc-tdd",
        "diagnose": "troubleshoot",
        "troubleshoot": "troubleshoot",
        "tui-test": "sdlc-tui-test",
        "update-documents": "sdlc-update-documents",
        "uat-tests": "sdlc-uat-tests",
        "unit-tests": "sdlc-unit-tests",
        "validate-codes": "sdlc-validate-codes",
    }
    stripped = value.strip()
    return aliases.get(stripped, stripped)


def _promoted_revalidation_is_current(
    active: Any, coordinator: dict[str, Any], evidence: dict[str, Any]
) -> bool:
    promoted = coordinator.get("promoted_head")
    project_value = coordinator.get("project_root")
    selected_value = coordinator.get("selected_project_root")
    integration_value = coordinator.get("integration_worktree")
    integration_branch = coordinator.get("integration_branch")
    base_branch = coordinator.get("base_branch")
    if (
        coordinator.get("status") != "done"
        or coordinator.get("cleanup_retained") != []
        or not isinstance(promoted, str)
        or coordinator.get("integration_head") != promoted
        or evidence.get("integration_commit") != promoted
        or not isinstance(project_value, str)
        or not isinstance(selected_value, str)
        or not isinstance(integration_value, str)
        or not isinstance(integration_branch, str)
        or not integration_branch
        or not isinstance(base_branch, str)
        or not base_branch
    ):
        return False
    raw_project = Path(project_value)
    raw_selected = Path(selected_value)
    raw_integration = Path(integration_value)
    if (
        not raw_project.is_absolute()
        or not raw_selected.is_absolute()
        or not raw_integration.is_absolute()
        or raw_project.is_symlink()
        or raw_selected.is_symlink()
    ):
        return False
    project = resolve_path(raw_project)
    selected = resolve_path(raw_selected)
    integration = resolve_path(raw_integration)
    if (
        project != resolve_path(active.project_root)
        or not is_inside(selected, project)
        or not is_inside(integration, active.run_dir)
        or raw_integration.exists()
        or raw_integration.is_symlink()
        or git_head(project) != promoted
    ):
        return False
    try:
        status = subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        current_branch = subprocess.run(
            ["git", "-C", str(project), "branch", "--show-current"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        branch = subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{integration_branch}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        status.returncode == 0
        and not status.stdout
        and current_branch.returncode == 0
        and current_branch.stdout.strip() == base_branch
        and branch.returncode == 1
    )


def _continue_normally() -> dict[str, Any]:
    return {"continue": True}


def _feature_items(feature_queue: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("features", "queue", "items"):
        value = feature_queue.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _all_features_committed(feature_queue: dict[str, Any]) -> bool:
    features = _feature_items(feature_queue)
    if not features:
        return False
    committed_states = {
        "committed",
        "uat",
        "pr-ready",
        "pr",
        "review",
        "merged",
        "complete",
        "completed",
    }
    for feature in features:
        status = str(feature.get("status") or feature.get("phase") or "").lower()
        if not feature.get("committed") and status not in committed_states:
            return False
    return True


def _uat_passed(feature_queue: dict[str, Any], current_state: dict[str, Any]) -> bool:
    for source in (
        feature_queue.get("uat"),
        current_state.get("uat"),
        current_state.get("evidence"),
    ):
        if isinstance(source, dict):
            status = str(
                source.get("status") or source.get("uat") or source.get("result") or ""
            ).lower()
            if status in {"pass", "passed", "success"}:
                return True
    return str(current_state.get("uat_status") or "").lower() in {
        "pass",
        "passed",
        "success",
    }


def _uat_failed_addressable(current_state: dict[str, Any]) -> bool:
    status = str(current_state.get("uat_status") or "").lower()
    failure = str(
        current_state.get("failure_classification")
        or current_state.get("blocked_reason")
        or ""
    ).lower()
    return status in {"fail", "failed"} and any(
        token in failure
        for token in ("design", "implementation", "test", "environment")
    )


def _retry_exceeded(current_state: dict[str, Any]) -> str | None:
    phase = str(current_state.get("current_phase") or current_state.get("phase") or "")
    retry_counts = current_state.get("retry_counts")
    if not phase or not isinstance(retry_counts, dict):
        return None
    count = int(retry_counts.get(phase, 0) or 0)
    max_retries = int(current_state.get("max_retries", RETRY_DEFAULT) or RETRY_DEFAULT)
    if count >= max_retries:
        return phase
    return None


def _repair_stop_reason(active, current_state: dict[str, Any]) -> str | None:
    pointer = current_state.get("repair")
    if pointer is None:
        return None
    if (
        not isinstance(pointer, dict)
        or pointer.get("schema") != "agentic-sdlc/repair-state-pointer-v1"
    ):
        return "Agentic SDLC repair pointer is malformed."
    control_value = pointer.get("control")
    event_value = pointer.get("failure_event")
    if not isinstance(control_value, str) or not isinstance(event_value, str):
        return "Agentic SDLC repair pointer is incomplete."
    control_path = resolve_path(control_value, active.run_dir)
    event_path = resolve_path(event_value, active.run_dir)
    if not is_inside(control_path, active.run_dir) or not is_inside(
        event_path, active.run_dir
    ):
        return "Agentic SDLC repair pointer escapes the private run."
    try:
        control = load_json(control_path) or {}
        event = load_json(event_path) or {}
    except (json.JSONDecodeError, OSError):
        return "Agentic SDLC repair state is unreadable."
    if (
        control.get("schema") != "agentic-sdlc/repair-control-v1"
        or event.get("schema") != "agentic-sdlc/failure-event-v1"
        or control.get("feature_id") != current_state.get("current_feature")
        or event.get("feature_id") != current_state.get("current_feature")
        or control.get("current_event_id") != event.get("event_id")
        or control.get("active_blocker", {}).get("blocker_key")
        != event.get("blocker_key")
    ):
        return "Agentic SDLC repair state identity is inconsistent."
    blocker = control["active_blocker"]
    status = str(control.get("status") or "")
    if status in {
        "exhausted",
        "blocked_after_design_remediation",
        "blocked_feature_dispatch_limit",
        "blocked_semantic_cycle",
        "blocked_missing_evidence",
        "unresolved",
    }:
        return f"Agentic SDLC repair loop stopped with status {status}."
    if int(blocker.get("active_seconds", 0) or 0) >= int(
        blocker.get("time_limit_seconds", 3600) or 3600
    ):
        return "Agentic SDLC repair loop reached its active-time ceiling."
    if (
        int(control.get("feature_dispatches", 0) or 0)
        >= int(control.get("feature_dispatch_limit", 4) or 4)
        and status != "resolved"
    ):
        return "Agentic SDLC repair loop reached its feature dispatch ceiling."
    next_skill = _normalize_skill_name(
        str(current_state.get("next_recommended_skill") or "")
    )
    revalidation = control.get("revalidation")
    invalidations = control.get("invalidations")
    requires_revalidation = isinstance(invalidations, list) and bool(invalidations)
    if status == "revalidation_required" or (
        status == "resolved" and requires_revalidation
    ):
        if not isinstance(revalidation, dict):
            return "Resolved repair is missing its authoritative revalidation cursor."
        required = revalidation.get("required")
        completed_ids = revalidation.get("completed_revalidation_ids")
        cursor = revalidation.get("cursor")
        if (
            revalidation.get("schema") != "agentic-sdlc/revalidation-cursor-v1"
            or revalidation.get("classification_id")
            != control.get("current_classification_id")
            or not isinstance(required, list)
            or not required
            or not isinstance(completed_ids, list)
            or not isinstance(cursor, int)
            or cursor < 0
            or cursor > len(required)
            or len(completed_ids) != cursor
        ):
            return "Agentic SDLC revalidation cursor is inconsistent."
        classification_id = revalidation.get("classification_id")
        classification_path = (
            active.run_dir
            / "repairs"
            / str(current_state.get("current_feature"))
            / "classifications"
            / f"{classification_id}.json"
        )
        try:
            classification = load_json(classification_path) or {}
        except (json.JSONDecodeError, OSError):
            return "Agentic SDLC revalidation classification is unreadable."
        unsigned_classification = dict(classification)
        recorded_classification_id = unsigned_classification.pop(
            "classification_id", None
        )
        calculated_classification_id = hashlib.sha256(
            json.dumps(
                unsigned_classification,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        invalidated_surfaces = classification.get("invalidates")
        if (
            classification.get("schema") != "agentic-sdlc/failure-classification-v1"
            or recorded_classification_id != classification_id
            or calculated_classification_id != classification_id
            or classification.get("feature_id") != current_state.get("current_feature")
            or classification.get("event_id") != event.get("event_id")
            or classification.get("blocker_key") != event.get("blocker_key")
            or not isinstance(invalidated_surfaces, list)
            or any(
                surface not in REVALIDATION_ROUTES for surface in invalidated_surfaces
            )
        ):
            return "Agentic SDLC revalidation classification is inconsistent."
        expected_required = [
            {
                "surface": surface,
                "next_recommended_skill": REVALIDATION_ROUTES[surface],
            }
            for surface in invalidated_surfaces
        ]
        cursor_projection = {
            "schema": "agentic-sdlc/revalidation-cursor-v1",
            "classification_id": classification_id,
            "repair_dispatch_id": revalidation.get("repair_dispatch_id"),
            "required": expected_required,
        }
        cursor_id = hashlib.sha256(
            json.dumps(
                cursor_projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        attempts = control.get("active_blocker", {}).get("attempts")
        attempt = (
            next(
                (
                    item
                    for item in attempts
                    if isinstance(item, dict)
                    and item.get("dispatch_id")
                    == revalidation.get("repair_dispatch_id")
                ),
                None,
            )
            if isinstance(attempts, list)
            else None
        )
        if (
            required != expected_required
            or revalidation.get("cursor_id") != cursor_id
            or attempt is None
            or attempt.get("classification_id") != classification_id
            or attempt.get("status") != "completed"
            or attempt.get("result") != "succeeded"
        ):
            return "Agentic SDLC revalidation cursor is not repair-authorized."
        latest_evidence: dict[str, Any] | None = None
        for index, revalidation_id in enumerate(completed_ids):
            if not isinstance(revalidation_id, str) or not revalidation_id:
                return "Agentic SDLC revalidation identity is invalid."
            evidence_path = (
                active.run_dir
                / "repairs"
                / str(current_state.get("current_feature"))
                / "revalidations"
                / f"{revalidation_id}.json"
            )
            try:
                evidence = load_json(evidence_path) or {}
            except (json.JSONDecodeError, OSError):
                return "Agentic SDLC revalidation evidence is unreadable."
            unsigned_evidence = dict(evidence)
            recorded_id = unsigned_evidence.pop("revalidation_id", None)
            calculated_id = hashlib.sha256(
                json.dumps(
                    unsigned_evidence,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest()
            expected = required[index] if index < len(required) else {}
            reference = evidence.get("evidence_reference")
            evidence_digest = str(evidence.get("evidence_digest") or "").removeprefix(
                "sha256:"
            )
            if not isinstance(reference, str):
                return "Agentic SDLC revalidation evidence reference is invalid."
            source_path = resolve_path(reference, active.run_dir)
            try:
                source_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
            except OSError:
                return "Agentic SDLC revalidation source evidence is unreadable."
            if (
                recorded_id != revalidation_id
                or calculated_id != revalidation_id
                or evidence.get("schema") != "agentic-sdlc/revalidation-evidence-v1"
                or evidence.get("feature_id") != current_state.get("current_feature")
                or evidence.get("event_id") != event.get("event_id")
                or evidence.get("classification_id")
                != control.get("current_classification_id")
                or evidence.get("cursor_id") != cursor_id
                or evidence.get("repair_dispatch_id")
                != revalidation.get("repair_dispatch_id")
                or evidence.get("blocker_key") != event.get("blocker_key")
                or evidence.get("surface") != expected.get("surface")
                or evidence.get("next_recommended_skill")
                != expected.get("next_recommended_skill")
                or not is_inside(source_path, active.run_dir / "evidence")
                or source_digest != evidence_digest
            ):
                return "Agentic SDLC revalidation evidence is inconsistent."
            try:
                gate_evidence = json.loads(source_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return "Agentic SDLC gate evidence is unreadable."
            if (
                not isinstance(gate_evidence, dict)
                or gate_evidence.get("schema") != "agentic-sdlc/gate-evidence-v1"
                or gate_evidence.get("feature_id")
                != current_state.get("current_feature")
                or gate_evidence.get("surface") != evidence.get("surface")
                or gate_evidence.get("owner_skill")
                != evidence.get("next_recommended_skill")
                or gate_evidence.get("status") != "passed"
                or gate_evidence.get("integration_commit")
                != evidence.get("integration_commit")
                or gate_evidence.get("fingerprints") != evidence.get("fingerprints")
                or not isinstance(gate_evidence.get("evidence"), list)
                or not gate_evidence["evidence"]
            ):
                return "Agentic SDLC gate evidence is not a passing phase result."
            latest_evidence = evidence
        if latest_evidence is not None:
            coordinator_path = (
                active.run_dir
                / "execution"
                / str(current_state.get("current_feature"))
                / "coordinator.json"
            )
            try:
                coordinator = load_json(coordinator_path) or {}
            except (json.JSONDecodeError, OSError):
                return "Agentic SDLC execution coordinator is unreadable."
            integration_value = coordinator.get("integration_worktree")
            if coordinator.get("schema") != "agentic-sdlc/execution-coordinator-v7":
                return "Agentic SDLC execution coordinator is inconsistent."
            fingerprint_ids = current_state.get("fingerprint_ids")
            current_fingerprints: dict[str, str] = {}
            if isinstance(fingerprint_ids, list):
                for identifier in fingerprint_ids:
                    if isinstance(identifier, str) and ":" in identifier:
                        name, value = identifier.split(":", 1)
                        current_fingerprints[name] = value
            commit_is_current = False
            if latest_evidence.get("surface") == "commit":
                commit_is_current = _promoted_revalidation_is_current(
                    active, coordinator, latest_evidence
                )
            elif isinstance(integration_value, str):
                integration = resolve_path(integration_value, active.run_dir)
                commit_is_current = is_inside(integration, active.run_dir) and git_head(
                    integration
                ) == latest_evidence.get("integration_commit")
            if (
                not commit_is_current
                or not current_fingerprints
                or current_fingerprints != latest_evidence.get("fingerprints")
            ):
                return "Agentic SDLC revalidation evidence is stale."
        if status == "revalidation_required":
            if revalidation.get("status") != "pending" or cursor >= len(required):
                return "Pending Agentic SDLC revalidation cursor is inconsistent."
            authoritative_skill = _normalize_skill_name(
                str(required[cursor].get("next_recommended_skill") or "")
            )
            if not authoritative_skill or next_skill != authoritative_skill:
                return "Invalidated evidence must rerun through its authoritative gate."
        elif revalidation.get("status") != "complete" or cursor != len(required):
            return "Resolved repair still has invalidated evidence to rerun."
    if status == "diagnosis_required" and next_skill != "troubleshoot":
        return "Diagnosis-required failure must route to troubleshoot."
    if status == "diagnosed":
        diagnosis_value = pointer.get("diagnosis")
        if not isinstance(diagnosis_value, str):
            return "Diagnosed repair state is missing its diagnosis pointer."
        diagnosis_path = resolve_path(diagnosis_value, active.run_dir)
        if not is_inside(diagnosis_path, active.run_dir):
            return "Agentic SDLC diagnosis pointer escapes the private run."
        try:
            diagnosis = load_json(diagnosis_path) or {}
        except (json.JSONDecodeError, OSError):
            return "Agentic SDLC diagnosis is unreadable."
        if (
            diagnosis.get("schema") != "agentic-sdlc/diagnosis-v1"
            or diagnosis.get("diagnosis_id") != control.get("current_diagnosis_id")
            or diagnosis.get("event_id") != event.get("event_id")
            or diagnosis.get("blocker_key") != event.get("blocker_key")
        ):
            return "Agentic SDLC diagnosis identity is inconsistent."
        if next_skill != "sdlc-classify-failure":
            return "Every Agentic SDLC diagnosis must return through sdlc-classify-failure."
    if status in {"routed", "remediating"}:
        classification_id = control.get("current_classification_id")
        if not isinstance(classification_id, str) or not classification_id:
            return "Routed Agentic SDLC repair state is missing its classification."
        classification_path = (
            active.run_dir
            / "repairs"
            / str(current_state.get("current_feature"))
            / "classifications"
            / f"{classification_id}.json"
        )
        if not is_inside(classification_path, active.run_dir):
            return "Agentic SDLC classification pointer escapes the private run."
        try:
            classification = load_json(classification_path) or {}
        except (json.JSONDecodeError, OSError):
            return "Agentic SDLC classification is unreadable."
        unsigned_classification = dict(classification)
        recorded_classification_id = unsigned_classification.pop(
            "classification_id", None
        )
        calculated_classification_id = hashlib.sha256(
            json.dumps(
                unsigned_classification,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        history = control.get("route_history")
        latest_route = history[-1] if isinstance(history, list) and history else {}
        if (
            classification.get("schema") != "agentic-sdlc/failure-classification-v1"
            or classification.get("classification_id") != classification_id
            or recorded_classification_id != calculated_classification_id
            or classification.get("feature_id") != current_state.get("current_feature")
            or classification.get("event_id") != event.get("event_id")
            or classification.get("blocker_key") != event.get("blocker_key")
            or latest_route.get("classification_id") != classification_id
            or latest_route.get("next_recommended_skill")
            != classification.get("next_recommended_skill")
        ):
            return "Agentic SDLC routed classification identity is inconsistent."
        authoritative_skill = _normalize_skill_name(
            str(classification.get("next_recommended_skill") or "")
        )
        if not authoritative_skill or next_skill != authoritative_skill:
            return "Routed Agentic SDLC repair must use its authoritative owner."
    return None


def _steering_reason(active) -> str | None:
    try:
        text = active.steering_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return "STEERING.md could not be read."
    upper = text.upper()
    if any(token in upper for token in ("CRITICAL", "URGENT", "PRIORITY", "BLOCKER")):
        return "critical STEERING.md instructions are present"
    if (
        "PAUSE" in upper
        or "DO NOT CREATE A PR" in upper
        or "DO NOT CREATE PR" in upper
        or "NO PR" in upper
    ):
        return "STEERING.md contains pause or PR-control instructions"
    return None


def _current_iteration(current_state: dict[str, Any]) -> int:
    return int(
        current_state.get("iteration_count", current_state.get("iteration", 0)) or 0
    )


def _max_iterations(current_state: dict[str, Any]) -> int:
    return int(current_state.get("max_iterations", 200) or 200)


def _bound_prompt_filename(active, run_state: dict[str, Any]) -> str | None:
    try:
        binding = load_json(active.run_dir / "prompt.json") or {}
    except (json.JSONDecodeError, OSError):
        return None
    if (
        binding.get("schema") != "agentic-sdlc/prompt-binding-v2"
        or binding.get("run_id") != active.run_id
    ):
        return None
    filename = str(binding.get("prompt_filename") or "")
    if (
        not filename
        or Path(filename).name != filename
        or not filename.endswith(".md")
        or len(filename) > 255
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
    ):
        return None
    mirrors: list[str] = []
    prompt_value = run_state.get("prompt")
    if isinstance(prompt_value, dict) and prompt_value.get("filename"):
        mirrors.append(str(prompt_value["filename"]))
    if run_state.get("prompt_filename"):
        mirrors.append(str(run_state["prompt_filename"]))
    if any(mirror != filename for mirror in mirrors):
        return None
    return filename


def _continuation_prompt(
    current_state: dict[str, Any], next_skill: str, reason: str, prompt_filename: str
) -> str:
    return "\n".join(
        [
            f"Use ${COORDINATOR_SKILL} run {shlex.quote(prompt_filename)}",
            "Continue the active SDLC run from local state.",
            "",
            f"Current feature: {current_state.get('current_feature') or '<none>'}",
            f"Current phase: {current_state.get('current_phase') or '<unknown>'}",
            f"Next recommended skill: {_normalize_skill_name(next_skill)}",
            f"Reason: {reason}",
            "",
            "Before doing anything:",
            "- Read active.lock.",
            "- Read current-state.json.",
            "- Read feature-queue.json.",
            "- Check STEERING.md.",
            "- Read docs/requirements.md and docs/design.md only as needed.",
            "- Do not modify locked plans.",
            "- Do not edit requirements.md or design.md directly.",
            "- Persist evidence before stopping.",
            "- If blocked, classify the failure and update current-state.json.",
        ]
    )


def _load_continuation(active) -> dict[str, Any]:
    try:
        return load_json(active.history_dir / "continuation-state.json") or {}
    except json.JSONDecodeError:
        return {}


def _state_digest(active, current_state: dict[str, Any]) -> str:
    return hash_state(
        [
            active.current_state_path,
            active.feature_queue_path,
            active.fingerprints_path,
            active.evidence_dir,
        ],
        extra=[
            git_head(active.project_root),
            str(current_state.get("next_recommended_skill") or ""),
        ],
    )


def _record_continuation(
    active, payload: dict[str, Any], digest: str, no_progress_count: int, reason: str
) -> None:
    value = {
        "last_state_digest": digest,
        "last_continuation_turn_id": payload.get("turn_id"),
        "no_progress_count": no_progress_count,
        "last_reason": reason,
        "last_updated_at": now_iso(),
    }
    write_json_atomic(active.history_dir / "continuation-state.json", value)
    append_jsonl(
        active.history_dir / "hook-events.jsonl",
        {
            "event": "Stop",
            "turn_id": payload.get("turn_id"),
            "decision": "continue" if reason != "NO_PROGRESS" else "stop",
            "reason": reason,
            "project_id": active.project_id,
            "run_id": active.run_id,
        },
    )


def _no_progress_count(active, payload: dict[str, Any], digest: str) -> int:
    previous = _load_continuation(active)
    if payload.get("stop_hook_active") and previous.get("last_state_digest") == digest:
        return int(previous.get("no_progress_count", 0) or 0) + 1
    return 0


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("hook_event_name") != "Stop":
        return _continue_normally()

    cwd = resolve_path(payload.get("cwd") or ".")
    active = load_active_run(cwd)
    if active is None:
        return _continue_normally()
    if not is_inside(cwd, active.project_root):
        return _continue_normally()

    try:
        run_state = load_json(active.run_json_path) or {}
        current_state = load_json(active.current_state_path) or {}
        feature_queue = load_json(active.feature_queue_path) or {}
    except json.JSONDecodeError:
        return stop("SDLC state is corrupt and needs repair.")

    current_status = str(current_state.get("status") or "").lower()
    run_status = str(run_state.get("status") or "").lower()
    status = current_status or run_status
    if run_status in TERMINAL_STATUSES:
        status = run_status
    blocked_reason = current_state.get("blocked_reason") or run_state.get(
        "blocked_reason"
    )
    if status in TERMINAL_STATUSES:
        if status in {"complete", "completed"}:
            return stop("SDLC run complete.")
        if status == "paused":
            return stop("SDLC run paused.")
        return stop(
            f"SDLC run is blocked: {blocked_reason or 'no blocker reason recorded'}"
        )

    if current_state.get("needs_human") or run_state.get("needs_human"):
        return stop(
            f"Human input required: {blocked_reason or 'state requested human input'}"
        )

    repair_reason = _repair_stop_reason(active, current_state)
    if repair_reason:
        return stop(repair_reason)

    if _current_iteration(current_state) >= _max_iterations(current_state):
        return stop("Max SDLC iterations reached.")

    retry_phase = _retry_exceeded(current_state)
    if retry_phase:
        return stop(f"Retry budget exceeded for {retry_phase}.")

    digest = _state_digest(active, current_state)
    no_progress = _no_progress_count(active, payload, digest)
    if no_progress >= 2:
        _record_continuation(active, payload, digest, no_progress, "NO_PROGRESS")
        return stop("No progress after Stop continuation.")

    prompt_filename = _bound_prompt_filename(active, run_state)
    if prompt_filename is None:
        return stop(
            "WORKFLOW_UPGRADE_REQUIRED: unfinished SDLC run has no valid managed prompt binding."
        )

    next_skill = _normalize_skill_name(
        str(current_state.get("next_recommended_skill") or "").strip()
    )
    if (
        current_state.get("current_phase") == "outer-integration-pending"
        or next_skill == "worktree"
    ):
        return stop(
            "Local worktree integration requires a fresh explicit user invocation "
            "from the recorded primary checkout and will not be continued "
            "automatically."
        )

    steering_reason = _steering_reason(active)
    if steering_reason:
        prompt = _continuation_prompt(
            current_state, COORDINATOR_SKILL, steering_reason, prompt_filename
        )
        _record_continuation(active, payload, digest, no_progress, steering_reason)
        return continue_with(prompt)

    if _all_features_committed(feature_queue) and not _uat_passed(
        feature_queue, current_state
    ):
        reason = "all features are committed and UAT has not passed"
        prompt = _continuation_prompt(
            current_state, "sdlc-uat-tests", reason, prompt_filename
        )
        _record_continuation(active, payload, digest, no_progress, reason)
        return continue_with(prompt)

    if _uat_failed_addressable(current_state):
        reason = "UAT failed with an addressable classification"
        prompt = _continuation_prompt(
            current_state, COORDINATOR_SKILL, reason, prompt_filename
        )
        _record_continuation(active, payload, digest, no_progress, reason)
        return continue_with(prompt)

    if next_skill == "sdlc-merge-pr":
        return stop(
            "Merge requires an explicit user request and will not be continued automatically."
        )
    if next_skill:
        reason = f"state recommends {next_skill}"
        prompt = _continuation_prompt(
            current_state, next_skill, reason, prompt_filename
        )
        _record_continuation(active, payload, digest, no_progress, reason)
        return continue_with(prompt)

    return _continue_normally()


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        print(json.dumps(evaluate(payload), sort_keys=True))
        return 0
    except Exception as exc:  # pragma: no cover - stop hooks must output JSON
        print(json.dumps(stop(f"SDLC Stop hook failed closed: {exc}"), sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
