#!/usr/bin/env python3
"""Two-command prompt intake routing over private workspace/run primitives."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from prompt_workspace_core import (
    TERMINAL_RUN_STATUSES,
    PromptWorkspaceError,
    iso_seconds,
    load_json_object,
    now_utc,
    project_workspace_manifest,
    required_string,
    resolve_prompt_reference,
    verify_workspace,
)
from prompt_workspace_execution import load_coordinator_state
from prompt_workspace_interop import load_interop, managed
from prompt_workspace_runs import (
    _activate_next_queued_prompt_unlocked,
    _snapshot_prompt_unlocked,
    enqueue_prompt_unlocked,
    initialize_project_workspace,
    load_prompt_queue,
    load_prompt_activity,
    load_run_manifests,
    manifest_revisions,
    prompt_rows,
    read_handoff_text,
    record_prompt_invocation,
    recover_incomplete_revisions_unlocked,
    scope_lock,
    touch_handoff_invocation,
    verify_run,
)
from prompt_workspace_resume import (
    adopt_resume_plan,
    plan_run_resume,
    reconcile_committed_resume,
    reconcile_handoff_projection,
)
from prompt_workspace_specs import record_steering_revision


def _existing_route_result(
    run_dir: Path,
    manifest: dict[str, object],
    verified: dict[str, object],
) -> dict[str, object]:
    revision_id = str(verified["revision"])
    revisions = manifest_revisions(manifest)
    revision = next(item for item in revisions if item.get("revision") == revision_id)
    return {
        "run_id": run_dir.name,
        "revision": revision_id,
        "prompt_id": manifest["prompt_id"],
        "sha256": revision["sha256"],
        "intent_sha256": revision.get("intent_sha256") or revision["sha256"],
        "snapshot": str(run_dir / str(revision["snapshot"])),
        "manifest": str(run_dir / "manifest.json"),
    }


def _latest_route_result(
    run_dir: Path,
    manifest: dict[str, object],
    verified: dict[str, object],
) -> dict[str, object]:
    revision_id = str(verified["latest_revision"])
    revisions = manifest_revisions(manifest)
    revision = next(item for item in revisions if item.get("revision") == revision_id)
    return {
        "run_id": run_dir.name,
        "revision": revision_id,
        "prompt_id": manifest["prompt_id"],
        "sha256": revision["sha256"],
        "intent_sha256": revision.get("intent_sha256") or revision["sha256"],
        "snapshot": str(run_dir / str(revision["snapshot"])),
        "manifest": str(run_dir / "manifest.json"),
        "created_revision": False,
    }


def _record_steering(
    run_dir: Path,
    internal: dict[str, object],
    submitted_at: datetime,
) -> None:
    manifest = load_json_object(run_dir / "manifest.json", "run manifest")
    record_steering_revision(
        run_dir,
        manifest_revisions(manifest),
        str(internal["revision"]),
        submitted_at,
    )


def _steering_action(
    coordinator: dict[str, object] | None,
    run_dir: Path,
) -> tuple[str, str, str | None]:
    if coordinator is None:
        return "reconcile", "reconcile_pending", None
    active_wave = coordinator.get("active_wave")
    if isinstance(active_wave, str):
        wave = load_json_object(
            run_dir / "orchestration" / "waves" / f"{active_wave}.json",
            "active wave",
        )
        if wave.get("status") == "planned":
            return "reconcile", "reconcile_pending", None
    return (
        "steering_queued_after_wave",
        "steering_pending",
        "STEERING_QUEUED_AFTER_WAVE",
    )


def _run_resources_active(run_dir: Path) -> bool:
    interop = load_interop(run_dir, required=False)
    return bool(
        interop is not None and managed(interop) and interop["released"] is False
    )


def _resume_route(
    workspace: dict[str, object],
    run_dir: Path,
    internal: dict[str, object],
    *,
    clock: Callable[[], datetime],
) -> tuple[str, str, str, dict[str, object]]:
    """Adopt one stable v7 observation and return its public-safe route."""

    reconcile_committed_resume(workspace, run_dir.name, clock=clock)
    plan = plan_run_resume(workspace, run_dir.name, clock=clock)
    plan = adopt_resume_plan(workspace, run_dir.name, plan, clock=clock)
    if not plan.get("replay", False):
        reconcile_handoff_projection(
            workspace,
            run_dir.name,
            plan,
            expected_sha256=str(plan["handoff_sha256"]),
        )
    internal = {**internal, "resume": plan}
    outcome = str(plan["outcome"])
    if outcome == "execute":
        if plan.get("next_transition") == "run-finalize":
            return (
                "finalize",
                "finalization_pending",
                "TASK_LEASE_RELEASE_REQUIRED",
                internal,
            )
        return "continue", "running", "RESUME_EXECUTE", internal
    if outcome == "wait":
        return "wait", "running", "WORKER_ACTIVE", internal
    if outcome == "requires_confirmation":
        return (
            "blocked",
            "blocked",
            "RECOVERY_CONFIRMATION_REQUIRED",
            internal,
        )
    if outcome == "blocked":
        return "blocked", "blocked", "RESUME_BLOCKED", internal
    return "done", "done", "ALREADY_COMPLETE", internal


def route_project_prompt(
    project_path: Path,
    codex_home: Path,
    prompt_reference: str | Path,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Resolve one prompt to its private new/continue/reconcile/done transition."""

    manifest_path = project_workspace_manifest(project_path, codex_home)
    if not manifest_path.is_file():
        raise PromptWorkspaceError(
            "WORKSPACE_NOT_FOUND",
            "project prompt workspace is missing; run "
            "`$task-implementer workspace init [project-folder]` first",
        )
    try:
        workspace = verify_workspace(manifest_path)
    except PromptWorkspaceError as exc:
        if (
            exc.code != "WORKSPACE_STATE_INVALID"
            or exc.message != "VS Code workspace command is unsafe"
        ):
            raise
        # The generated editor launcher records a resolved Python executable so
        # that it never follows an unreviewed wrapper or symlink. Package-manager
        # upgrades can remove that exact executable while leaving the managed
        # lane, prompts, and run state valid. An explicit run may refresh only
        # this generated workspace surface through the canonical initializer;
        # every other workspace validation failure remains fail-closed.
        refreshed = initialize_project_workspace(project_path, codex_home)
        refreshed_manifest = Path(
            required_string(refreshed, "workspace", "workspace refresh")
        )
        if refreshed_manifest != manifest_path:
            raise PromptWorkspaceError(
                "WORKSPACE_MISMATCH",
                "workspace refresh resolved a different project scope",
            )
        workspace = verify_workspace(manifest_path)
    resolve_prompt_reference(
        manifest_path,
        prompt_reference,
        require_content=True,
    )
    invoked_at = clock()
    if invoked_at.tzinfo is None or invoked_at.utcoffset() is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run intake clock must be timezone-aware"
        )
    invoked_at_text = iso_seconds(invoked_at)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))

    with scope_lock(runs_root.parent):
        document = resolve_prompt_reference(
            manifest_path,
            prompt_reference,
            require_content=True,
        )
        load_prompt_activity(runs_root.parent)
        all_runs = load_run_manifests(runs_root)
        if recover_incomplete_revisions_unlocked(all_runs, clock=clock):
            all_runs = load_run_manifests(runs_root)
        coordinator_states = {
            run_dir.name: load_coordinator_state(run_dir) for run_dir, _ in all_runs
        }
        verified_runs = {
            run_dir.name: verify_run(workspace, run_dir.name, None)
            for run_dir, _ in all_runs
        }
        active = [
            (run_dir, manifest)
            for run_dir, manifest in all_runs
            if str(verified_runs[run_dir.name]["status"]) not in TERMINAL_RUN_STATUSES
            or bool(verified_runs[run_dir.name]["steering_pending"])
            or bool(verified_runs[run_dir.name]["reconciliation_pending"])
            or (
                coordinator_states[run_dir.name] is not None
                and coordinator_states[run_dir.name]["status"] == "running"
            )
            or _run_resources_active(run_dir)
        ]
        matching = [
            (run_dir, manifest)
            for run_dir, manifest in all_runs
            if manifest.get("prompt_id") == document.prompt_id
        ]

        if len(active) > 1:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "workspace has multiple unfinished runs"
            )

        action: str
        internal: dict[str, object]
        status: str
        outcome: str | None = None
        if active:
            active_dir, active_manifest = active[0]
            active_latest = manifest_revisions(active_manifest)[-1]
            if "intent_sha256" not in active_latest or "kind" not in active_latest:
                raise PromptWorkspaceError(
                    "WORKFLOW_UPGRADE_REQUIRED",
                    "unfinished prompt-v1 run is read-only; finish or retire its private state before running prompt-v3",
                )
            source_name = str(active_manifest.get("source_path", "prompt.md"))
            if active_manifest.get("prompt_id") != document.prompt_id:
                latest_matching = matching[-1] if matching else None
                if latest_matching is not None:
                    latest_dir, latest_manifest = latest_matching
                    latest_verified = verified_runs[latest_dir.name]
                    latest_revision = manifest_revisions(latest_manifest)[-1]
                    latest_intent = str(
                        latest_revision.get("intent_sha256")
                        or latest_revision["sha256"]
                    )
                    if (
                        str(latest_verified["status"]) in TERMINAL_RUN_STATUSES
                        and latest_intent == document.intent_sha256
                    ):
                        internal = _existing_route_result(
                            latest_dir, latest_manifest, latest_verified
                        )
                        invoked_at_text = record_prompt_invocation(
                            runs_root.parent, document.prompt_id, invoked_at
                        )
                        rows = prompt_rows(manifest_path, None, None)
                        return {
                            "action": "done",
                            "prompt": str(document.path),
                            "last_invoked_at": invoked_at_text,
                            "status": "done",
                            "prompts": rows,
                            "_internal": internal,
                        }
                queued = enqueue_prompt_unlocked(runs_root.parent, document, invoked_at)
                invoked_at_text = record_prompt_invocation(
                    runs_root.parent, document.prompt_id, invoked_at
                )
                rows = prompt_rows(manifest_path, None, None)
                return {
                    "action": str(queued["action"]),
                    "prompt": str(document.path),
                    "last_invoked_at": invoked_at_text,
                    "status": "queued",
                    "outcome": "PROMPT_QUEUED",
                    "queue_position": queued["position"],
                    "active_prompt": source_name,
                    "prompts": rows,
                    "_internal": queued["entry"],
                }
            verified = verified_runs[active_dir.name]
            status = str(verified["status"])
            latest_intent_sha256 = str(verified["latest_intent_sha256"])
            if status in TERMINAL_RUN_STATUSES and _run_resources_active(active_dir):
                if document.intent_sha256 != latest_intent_sha256:
                    queued = enqueue_prompt_unlocked(
                        runs_root.parent, document, invoked_at
                    )
                    invoked_at_text = record_prompt_invocation(
                        runs_root.parent, document.prompt_id, invoked_at
                    )
                    rows = prompt_rows(manifest_path, None, None)
                    return {
                        "action": str(queued["action"]),
                        "prompt": str(document.path),
                        "last_invoked_at": invoked_at_text,
                        "status": "queued",
                        "outcome": "PROMPT_QUEUED",
                        "queue_position": queued["position"],
                        "active_prompt": source_name,
                        "prompts": rows,
                        "_internal": queued["entry"],
                    }
                internal = _existing_route_result(active_dir, active_manifest, verified)
                action = "finalize"
                status = "finalization_pending"
                outcome = "TASK_LEASE_RELEASE_REQUIRED"
            elif status in TERMINAL_RUN_STATUSES and bool(
                verified["reconciliation_pending"]
            ):
                internal = _latest_route_result(active_dir, active_manifest, verified)
                _record_steering(active_dir, internal, invoked_at)
                action = "reconcile"
                status = "reconcile_pending"
            elif status == "snapshot_only":
                internal = _snapshot_prompt_unlocked(
                    manifest_path,
                    document.path,
                    run_id=(
                        None
                        if document.intent_sha256 == latest_intent_sha256
                        else active_dir.name
                    ),
                    force_new_run=False,
                    clock=clock,
                    expected_sha256=document.sha256,
                )
                if str(internal["revision"]) != "r0001":
                    _record_steering(active_dir, internal, invoked_at)
                action = "new"
            elif status == "running":
                if document.intent_sha256 != latest_intent_sha256:
                    internal = _snapshot_prompt_unlocked(
                        manifest_path,
                        document.path,
                        run_id=active_dir.name,
                        force_new_run=False,
                        clock=clock,
                        expected_sha256=document.sha256,
                        allow_running=True,
                    )
                    _record_steering(active_dir, internal, invoked_at)
                    active_manifest = load_json_object(
                        active_dir / "manifest.json", "run manifest"
                    )
                    verified = verify_run(workspace, active_dir.name, None)
                elif bool(verified["steering_pending"]):
                    internal = _latest_route_result(
                        active_dir, active_manifest, verified
                    )
                elif bool(verified["reconciliation_pending"]):
                    internal = _latest_route_result(
                        active_dir, active_manifest, verified
                    )
                    _record_steering(active_dir, internal, invoked_at)
                    verified = verify_run(workspace, active_dir.name, None)
                else:
                    internal = _existing_route_result(
                        active_dir, active_manifest, verified
                    )
                    action = "continue"
                if bool(verified["steering_pending"]):
                    action, status, outcome = _steering_action(
                        coordinator_states[active_dir.name], active_dir
                    )
            elif document.intent_sha256 == latest_intent_sha256:
                if bool(verified["reconciliation_pending"]):
                    internal = _snapshot_prompt_unlocked(
                        manifest_path,
                        document.path,
                        run_id=active_dir.name,
                        force_new_run=False,
                        clock=clock,
                        expected_sha256=document.sha256,
                    )
                    _record_steering(active_dir, internal, invoked_at)
                    action = "reconcile"
                    status = "reconcile_pending"
                else:
                    internal = _existing_route_result(
                        active_dir, active_manifest, verified
                    )
                    action = "continue"
            else:
                internal = _snapshot_prompt_unlocked(
                    manifest_path,
                    document.path,
                    run_id=active_dir.name,
                    force_new_run=False,
                    clock=clock,
                    expected_sha256=document.sha256,
                )
                _record_steering(active_dir, internal, invoked_at)
                action = "reconcile"
                status = "reconcile_pending"
            invoked_at_text = record_prompt_invocation(
                runs_root.parent,
                document.prompt_id,
                invoked_at,
            )
            touch_handoff_invocation(
                active_dir,
                datetime.fromisoformat(invoked_at_text),
            )
            coordinator = load_coordinator_state(active_dir)
            if (
                coordinator is not None
                and document.intent_sha256 == str(verified["latest_intent_sha256"])
                and not bool(verified["steering_pending"])
                and not bool(verified["reconciliation_pending"])
            ):
                action, status, outcome, internal = _resume_route(
                    workspace, active_dir, internal, clock=clock
                )
        else:
            queue = load_prompt_queue(runs_root.parent)
            if queue["entries"]:
                queued = enqueue_prompt_unlocked(runs_root.parent, document, invoked_at)
                head = load_prompt_queue(runs_root.parent)["entries"][0]
                if head["prompt_id"] != document.prompt_id:
                    activated = _activate_next_queued_prompt_unlocked(
                        manifest_path, clock=clock
                    )
                    invoked_at_text = record_prompt_invocation(
                        runs_root.parent, document.prompt_id, invoked_at
                    )
                    rows = prompt_rows(manifest_path, None, None)
                    result = {
                        "action": str(queued["action"]),
                        "prompt": str(document.path),
                        "last_invoked_at": invoked_at_text,
                        "status": "queued",
                        "outcome": "PROMPT_QUEUED",
                        "queue_position": queued["position"],
                        "prompts": rows,
                        "_internal": queued["entry"],
                    }
                    if activated is not None:
                        result["activated_queue_head"] = activated
                    return result
                activated = _activate_next_queued_prompt_unlocked(
                    manifest_path, clock=clock
                )
                if (
                    activated is not None
                    and activated.get("status") == "activated"
                    and activated.get("prompt_id") == document.prompt_id
                ):
                    internal = activated
                    action = "new"
                    status = "snapshot_only"
                    invoked_at_text = record_prompt_invocation(
                        runs_root.parent, document.prompt_id, invoked_at
                    )
                    rows = prompt_rows(manifest_path, None, None)
                    return {
                        "action": action,
                        "prompt": str(document.path),
                        "last_invoked_at": invoked_at_text,
                        "status": status,
                        "prompts": rows,
                        "_internal": internal,
                    }
            latest_matching = matching[-1] if matching else None
            if latest_matching is not None:
                latest_dir, latest_manifest = latest_matching
                verified = verified_runs[latest_dir.name]
                latest_intent_sha256 = str(verified["latest_intent_sha256"])
                latest_coordinator = None
                if str(verified["status"]) == "done":
                    completed_handoff = read_handoff_text(latest_dir)
                    if completed_handoff is None:
                        raise PromptWorkspaceError(
                            "RUN_STATE_INVALID", "completed run handoff is missing"
                        )
                    # Completed prompt history is readable only when its
                    # canonical coordinator-v7 execution state validates.
                    latest_coordinator = load_coordinator_state(latest_dir)
                if (
                    str(verified["status"]) == "done"
                    and document.intent_sha256 == latest_intent_sha256
                ):
                    internal = _existing_route_result(
                        latest_dir, latest_manifest, verified
                    )
                    interop = load_interop(latest_dir, required=False)
                    if (
                        interop is not None
                        and managed(interop)
                        and interop["released"] is False
                    ):
                        action = "finalize"
                        status = "finalization_pending"
                        outcome = "TASK_LEASE_RELEASE_REQUIRED"
                    else:
                        action = "done"
                        status = "done"
                    invoked_at_text = record_prompt_invocation(
                        runs_root.parent,
                        document.prompt_id,
                        invoked_at,
                    )
                    touch_handoff_invocation(
                        latest_dir,
                        datetime.fromisoformat(invoked_at_text),
                    )
                    if latest_coordinator is not None:
                        action, status, outcome, internal = _resume_route(
                            workspace, latest_dir, internal, clock=clock
                        )
                else:
                    internal = _snapshot_prompt_unlocked(
                        manifest_path,
                        document.path,
                        run_id=None,
                        force_new_run=document.intent_sha256 == latest_intent_sha256,
                        clock=clock,
                        expected_sha256=document.sha256,
                    )
                    action = "new"
                    status = "snapshot_only"
                    invoked_at_text = record_prompt_invocation(
                        runs_root.parent,
                        document.prompt_id,
                        invoked_at,
                    )
            else:
                internal = _snapshot_prompt_unlocked(
                    manifest_path,
                    document.path,
                    run_id=None,
                    force_new_run=False,
                    clock=clock,
                    expected_sha256=document.sha256,
                )
                action = "new"
                status = "snapshot_only"
                invoked_at_text = record_prompt_invocation(
                    runs_root.parent,
                    document.prompt_id,
                    invoked_at,
                )

        rows = prompt_rows(manifest_path, None, None)
        result: dict[str, object] = {
            "action": action,
            "prompt": str(document.path),
            "prompt_ref": document.prompt_ref,
            "last_invoked_at": invoked_at_text,
            "status": status,
            "prompts": rows,
            "_internal": internal,
        }
        if outcome is not None:
            result["outcome"] = outcome
        return result
