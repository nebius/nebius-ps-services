#!/usr/bin/env python3
"""Stop hook that safely continues active Agentic SDLC runs."""

from __future__ import annotations

import json
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


def _normalize_skill_name(value: str) -> str:
    aliases = {
        "align-specs": "sdlc-align-specs",
        "classify-failure": "sdlc-classify-failure",
        "commit": "sdlc-commit",
        "create-design": "sdlc-create-design",
        "create-plan": "sdlc-create-plan",
        "create-requirements": "sdlc-create-requirements",
        "evaluate": "sdlc-evaluate",
        "gather-context": "sdlc-gather-context",
        "gui-test": "sdlc-gui-test",
        "implement-plan": "sdlc-implement-plan",
        "merge-pr": "sdlc-merge-pr",
        "tdd": "sdlc-tdd",
        "tui-test": "sdlc-tui-test",
        "uat-tests": "sdlc-uat-tests",
        "unit-tests": "sdlc-unit-tests",
        "validate-codes": "sdlc-validate-codes",
    }
    stripped = value.strip()
    return aliases.get(stripped, stripped)


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
    committed_states = {"committed", "uat", "pr-ready", "pr", "review", "merged", "complete", "completed"}
    for feature in features:
        status = str(feature.get("status") or feature.get("phase") or "").lower()
        if not feature.get("committed") and status not in committed_states:
            return False
    return True


def _uat_passed(feature_queue: dict[str, Any], current_state: dict[str, Any]) -> bool:
    for source in (feature_queue.get("uat"), current_state.get("uat"), current_state.get("evidence")):
        if isinstance(source, dict):
            status = str(source.get("status") or source.get("uat") or source.get("result") or "").lower()
            if status in {"pass", "passed", "success"}:
                return True
    return str(current_state.get("uat_status") or "").lower() in {"pass", "passed", "success"}


def _uat_failed_addressable(current_state: dict[str, Any]) -> bool:
    status = str(current_state.get("uat_status") or "").lower()
    failure = str(current_state.get("failure_classification") or current_state.get("blocked_reason") or "").lower()
    return status in {"fail", "failed"} and any(
        token in failure for token in ("design", "implementation", "test", "environment")
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
    if "PAUSE" in upper or "DO NOT CREATE A PR" in upper or "DO NOT CREATE PR" in upper or "NO PR" in upper:
        return "STEERING.md contains pause or PR-control instructions"
    return None


def _current_iteration(current_state: dict[str, Any]) -> int:
    return int(current_state.get("iteration_count", current_state.get("iteration", 0)) or 0)


def _max_iterations(current_state: dict[str, Any]) -> int:
    return int(current_state.get("max_iterations", 200) or 200)


def _continuation_prompt(active, current_state: dict[str, Any], next_skill: str, reason: str) -> str:
    return "\n".join(
        [
            f"Use ${COORDINATOR_SKILL}.",
            "Continue the active SDLC run from local state.",
            "",
            f"Project root: {active.project_root}",
            f"Project ID: {active.project_id}",
            f"Run ID: {active.run_id}",
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
        extra=[git_head(active.project_root), str(current_state.get("next_recommended_skill") or "")],
    )


def _record_continuation(active, payload: dict[str, Any], digest: str, no_progress_count: int, reason: str) -> None:
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
    blocked_reason = current_state.get("blocked_reason") or run_state.get("blocked_reason")
    if status in TERMINAL_STATUSES:
        if status in {"complete", "completed"}:
            return stop("SDLC run complete.")
        if status == "paused":
            return stop("SDLC run paused.")
        return stop(f"SDLC run is blocked: {blocked_reason or 'no blocker reason recorded'}")

    if current_state.get("needs_human") or run_state.get("needs_human"):
        return stop(f"Human input required: {blocked_reason or 'state requested human input'}")

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

    steering_reason = _steering_reason(active)
    if steering_reason:
        prompt = _continuation_prompt(active, current_state, COORDINATOR_SKILL, steering_reason)
        _record_continuation(active, payload, digest, no_progress, steering_reason)
        return continue_with(prompt)

    if _all_features_committed(feature_queue) and not _uat_passed(feature_queue, current_state):
        reason = "all features are committed and UAT has not passed"
        prompt = _continuation_prompt(active, current_state, "sdlc-uat-tests", reason)
        _record_continuation(active, payload, digest, no_progress, reason)
        return continue_with(prompt)

    if _uat_failed_addressable(current_state):
        reason = "UAT failed with an addressable classification"
        prompt = _continuation_prompt(active, current_state, COORDINATOR_SKILL, reason)
        _record_continuation(active, payload, digest, no_progress, reason)
        return continue_with(prompt)

    next_skill = str(current_state.get("next_recommended_skill") or "").strip()
    next_skill = _normalize_skill_name(next_skill)
    if next_skill == "sdlc-merge-pr":
        return stop("Merge requires an explicit user request and will not be continued automatically.")
    if next_skill:
        reason = f"state recommends {next_skill}"
        prompt = _continuation_prompt(active, current_state, next_skill, reason)
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
