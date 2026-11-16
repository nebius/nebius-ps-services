"""Bounded pre-effect input correction, separate from feature repair authority."""
from __future__ import annotations

import time
import re
from pathlib import Path

from workflow_security import contains_sensitive
from workflow_state import WorkflowError, digest, lock, read, stable, write

SCHEMA = "workflow/input-correction-v1"
LIMIT = 2
ACTIVE_LIMIT_NS = 300 * 1_000_000_000


def submit(run_dir: Path, operation: dict, candidate: dict, feedback: dict, *, clock=time.time_ns) -> dict:
    """Called by a validator owner only, before any requested operation effects.

    One phase/assignment is one logical operation. The owner must not include a
    candidate hash, process identity, replacement attempt or caller operation ID.
    A live phase remains active across process restarts. Pausing must be explicit
    through the coordinator's pause/resume adapters, never inferred from silence.
    """
    if feedback.get("effects") != "none" or feedback.get("status") not in {"valid", "rejected"}:
        raise WorkflowError("CORRECTION_INVALID", "only pre-effect owner feedback is admissible")
    root = run_dir / "input-corrections"
    operation_id = digest(operation)
    path = root / (operation_id + ".json")
    candidate_id = digest(candidate)
    archive = root / operation_id / (candidate_id + ".json")
    with lock(root):
        state = read(path) if path.exists() else None
        eligible = feedback.get("correction_kind") is not None
        if state is None and not eligible:
            return feedback
        raw = stable(candidate)
        if len(raw) > 3 * 1024 * 1024 or contains_sensitive(raw.decode()):
            if state is not None:
                state["status"] = "semantic_change"
                write(path, state)
            return {**feedback, "status": "classification_required", "remaining_submissions": 0}
        now = clock()
        if state is None:
            result = {**feedback, "status": "correction_pending", "remaining_submissions": LIMIT}
            state = {"schema": SCHEMA, "operation": operation, "limit": LIMIT,
                     "active_limit_ns": ACTIVE_LIMIT_NS, "started_ns": now,
                     "paused_ns": None, "paused_total_ns": 0, "submissions": 0,
                     "semantic_sha256": feedback["semantic_sha256"], "status": "pending",
                     "candidates": []}
        else:
            if (state.get("schema") != SCHEMA or state.get("operation") != operation
                    or state.get("limit") != LIMIT or state.get("active_limit_ns") != ACTIVE_LIMIT_NS):
                raise WorkflowError("CORRECTION_INVALID", "logical operation correction state changed")
            elapsed = (state["paused_ns"] if state["paused_ns"] is not None else now) - state["started_ns"] - state["paused_total_ns"]
            if state["status"] == "pending" and (elapsed < 0 or elapsed >= ACTIVE_LIMIT_NS):
                state["status"] = "exhausted"
                write(path, state)
            if state["status"] in {"exhausted", "semantic_change"} or state["paused_ns"] is not None:
                return {**feedback, "status": "classification_required", "remaining_submissions": 0}
            for row in state["candidates"]:
                if row["candidate_sha256"] == candidate_id:
                    if read(archive) != candidate or row["feedback"] != feedback:
                        raise WorkflowError("CORRECTION_INVALID", "owner submission changed on replay")
                    return row["result"]
            if state["status"] == "accepted" and feedback["status"] == "valid":
                # A closed correction lane does not veto later canonical work.
                # It grants no new exemption and retains its consumed allowance.
                return feedback
            if state["status"] != "pending" or state["submissions"] >= LIMIT:
                return {**feedback, "status": "classification_required", "remaining_submissions": 0}
            state["submissions"] += 1
            if feedback.get("semantic_sha256") != state["semantic_sha256"]:
                state["status"], outcome = "semantic_change", "classification_required"
            elif feedback["status"] == "valid":
                state["status"], outcome = "accepted", "valid"
            elif state["submissions"] >= LIMIT or not eligible:
                state["status"], outcome = "exhausted", "classification_required"
            else:
                outcome = "correction_pending"
            result = {**feedback, "status": outcome,
                      "remaining_submissions": max(0, LIMIT - state["submissions"])}
        if archive.exists():
            if read(archive) != candidate:
                raise WorkflowError("CORRECTION_INVALID", "preserved candidate changed")
        else:
            write(archive, candidate, exclusive=True)
        state["candidates"].append({"candidate_sha256": candidate_id, "feedback": feedback, "result": result})
        write(path, state)
        return result


def pause(run_dir: Path, paused: bool, session: str, *, clock=time.time_ns) -> None:
    from workflow_objectives import authorize_coordinator
    authorize_coordinator(run_dir, session, owner="agentic-sdlc")
    root = run_dir / "input-corrections"
    if not root.exists():
        return
    with lock(root):
        for path in root.glob("*.json"):
            state = read(path)
            if state.get("schema") != SCHEMA or state.get("status") != "pending":
                continue
            now = clock()
            if paused and state["paused_ns"] is None:
                state["paused_ns"] = now
            elif not paused and state["paused_ns"] is not None:
                if now < state["paused_ns"]:
                    raise WorkflowError("CORRECTION_INVALID", "correction clock moved backwards")
                state["paused_total_ns"] += now - state["paused_ns"]
                state["paused_ns"] = None
            write(path, state)


def _authoring_phase(run_dir: Path, state: dict) -> str | None:
    """Resolve the coordinator's active authoring skill or recorded next phase.

    A completed phase checkpoint intentionally names its next owner separately.
    Do not guess from a generic failure or retroactively exempt classified work.
    """
    phases = {"sdlc-create-requirements", "sdlc-create-design", "sdlc-update-documents"}
    if state.get("status") != "running" or state.get("repair"):
        return None
    current, next_phase = state.get("current_phase"), state.get("next_recommended_skill")
    completed = current == state.get("last_successful_phase") or current == "sdlc-start"
    phase = next_phase if completed else current
    if phase not in phases:
        return None
    if state.get("run_id") != run_dir.name or state.get("project_id") != run_dir.parent.name:
        raise WorkflowError("CORRECTION_INVALID", "authoring phase belongs to another run")
    pointer = read(run_dir / "checkpoints/latest.json")
    checkpoint_id = pointer.get("checkpoint_id")
    if (not isinstance(checkpoint_id, str) or re.fullmatch(r"checkpoint-[0-9]{4,}", checkpoint_id) is None
            or pointer.get("path") != f"checkpoints/{checkpoint_id}.json"
            or state.get("checkpoint_id") != checkpoint_id):
        raise WorkflowError("CORRECTION_INVALID", "authoring phase lacks its coordinator checkpoint")
    checkpoint = read(run_dir / pointer["path"])
    keys = ("project_id", "run_id", "checkpoint_id", "current_phase", "last_successful_phase",
            "next_recommended_skill", "current_execution", "status", "repair")
    if any(checkpoint.get(key) != state.get(key) for key in keys):
        raise WorkflowError("CORRECTION_INVALID", "authoring phase checkpoint changed")
    return phase


def spec_submission(project: Path, requirements: bytes, design: bytes) -> dict | None:
    """Discover only the actual native coordinator binding; no active-run guess."""
    from agent_runtime import agent_home, native_session_id
    from workflow_objectives import observe_binding
    from workflow_impact import spec_checkout
    from project_specs_lib.candidates import check_pair
    session = native_session_id()
    if session is None:
        return None
    bound = observe_binding(agent_home(), "agentic-sdlc", session)
    if bound is None:
        return None
    run_dir, run, objective = bound
    if run["status"] in {"complete", "completed", "cancelled"}:
        return None
    try:
        state = read(run_dir / "current-state.json")
    except FileNotFoundError:
        # Accepted intake can publish through the canonical owner before phase
        # initialization. Without a phase there is no correction exemption.
        return None
    phase = _authoring_phase(run_dir, state)
    if phase is None:
        return None
    execution = state.get("current_execution")
    selected = spec_checkout(run_dir, Path(run["project_root"]), execution_id=execution, strict_head=False)
    if selected != project.resolve():
        return None
    operation = {"owner": "maintain-project-specs", "run_id": run_dir.name,
                 "objective_id": objective["objective_id"], "phase": phase,
                 "execution_id": execution, "revision": objective["revisions"][-1]["revision"]}
    feedback = check_pair(requirements, design)
    if feedback.get("code") in {"UNSAFE_SPEC", "SENSITIVE_SPEC"}:
        return feedback
    return submit(run_dir, operation, {"requirements": requirements.decode(), "design": design.decode()}, feedback)
