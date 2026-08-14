#!/usr/bin/env python3
"""Explicit current-schema recovery for proven Task Implementer state defects."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
from pathlib import Path
import re

from prompt_workspace_core import (
    PromptWorkspaceError,
    iso_seconds,
    load_json_object,
    now_utc,
    require_mode,
    required_string,
    stable_json,
    verify_workspace,
    write_atomic,
    write_exclusive,
)
from prompt_workspace_execution import (
    field_block,
    load_coordinator_state,
    orchestration_dir,
    sha256_json,
    task_sections,
)
from prompt_workspace_runs import read_handoff_text, scope_lock, verify_run
from prompt_workspace_specs import (
    inspect_spec_documents,
    load_current_prompt_impact,
    verify_prompt_impact_plan,
    verify_requirements_refinement_contract,
)
from prompt_workspace_waves import (
    _load_task_plane,
    _load_wave,
    _run_dir,
    _save_coordinator,
)


PLAN_DIGEST_RECOVERY_SCHEMA = "task-implementer/plan-digest-recovery-v1"
HANDOFF_PROJECTION_RECOVERY_SCHEMA = "task-implementer/handoff-projection-recovery-v1"
IMPACT_PLAN_SCHEMA = "task-implementer/prompt-impact-plan-v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _recovery_path(run_dir: Path) -> Path:
    return orchestration_dir(run_dir) / "plan-digest-recovery.json"


def _plan_basis_path(run_dir: Path) -> Path:
    return run_dir / "prompt-impact" / "plan-basis.json"


def _handoff_projection_recovery_path(run_dir: Path) -> Path:
    return orchestration_dir(run_dir) / "handoff-projection-recovery.json"


def _indexed_tasks(coordinator: dict[str, object]) -> list[list[dict[str, object]]]:
    indexed = coordinator.get("waves")
    if not isinstance(indexed, list) or not indexed:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "coordinator wave index is invalid"
        )
    plans: list[list[dict[str, object]]] = []
    for entry in indexed:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"wave_id", "tasks", "batches"}
            or not isinstance(entry.get("wave_id"), str)
            or not isinstance(entry.get("tasks"), list)
            or not all(isinstance(task, dict) for task in entry["tasks"])
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator wave entry is invalid"
            )
        plans.append(list(entry["tasks"]))
    return plans


def _validate_indexed_state(
    run_dir: Path,
    coordinator: dict[str, object],
) -> None:
    for entry in coordinator["waves"]:
        wave_id = str(entry["wave_id"])
        wave = _load_wave(run_dir, wave_id)
        task_ids = [
            task.get("task_id") for task in entry["tasks"] if isinstance(task, dict)
        ]
        if task_ids != wave["task_ids"] or entry["batches"] != wave["batches"]:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator and wave plans differ"
            )
        for task_id in wave["task_ids"]:
            plane = _load_task_plane(run_dir, wave_id, str(task_id))
            if plane.get("state") != wave["task_states"].get(task_id):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "wave and task-plane state differ"
                )


def _load_handoff_projection_recovery(
    path: Path, run_id: str
) -> dict[str, object] | None:
    if path.is_symlink():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "handoff projection recovery path is invalid"
        )
    if not path.exists():
        return None
    if not path.is_file():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "handoff projection recovery path is invalid"
        )
    require_mode(path, 0o600, "handoff projection recovery")
    value = load_json_object(path, "handoff projection recovery")
    required = {
        "schema",
        "run_id",
        "expected_handoff_sha256",
        "post_handoff_sha256",
        "task_ids",
        "created_at",
        "phase",
    }
    try:
        created_at = datetime.fromisoformat(str(value.get("created_at")))
    except ValueError:
        created_at = None
    if (
        set(value) != required
        or value.get("schema") != HANDOFF_PROJECTION_RECOVERY_SCHEMA
        or value.get("run_id") != run_id
        or SHA256_RE.fullmatch(str(value.get("expected_handoff_sha256") or "")) is None
        or SHA256_RE.fullmatch(str(value.get("post_handoff_sha256") or "")) is None
        or value.get("expected_handoff_sha256") == value.get("post_handoff_sha256")
        or not isinstance(value.get("task_ids"), list)
        or not value["task_ids"]
        or not all(
            isinstance(task_id, str)
            and re.fullmatch(r"task-[1-9][0-9]*", task_id) is not None
            for task_id in value["task_ids"]
        )
        or len(value["task_ids"]) != len(set(value["task_ids"]))
        or value.get("phase") not in {"intent", "committed"}
        or created_at is None
        or created_at.tzinfo is None
        or created_at.utcoffset() is None
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "handoff projection recovery state is invalid"
        )
    return value


def _projection_statuses(
    run_dir: Path,
    coordinator: dict[str, object],
    text: str,
) -> tuple[list[str], list[str]]:
    """Return historical mismatches and all machine-recoverable task IDs."""

    from prompt_workspace_resume import _projected_task_status

    _order, sections = task_sections(text)
    seen: set[str] = set()
    mismatched: list[str] = []
    recoverable: list[str] = []
    for entry in coordinator["waves"]:
        wave_id = str(entry["wave_id"])
        wave = _load_wave(run_dir, wave_id)
        wave_status = str(wave["status"])
        for task_id_value in wave["task_ids"]:
            task_id = str(task_id_value)
            if task_id in seen or task_id not in sections:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "handoff task projection index differs"
                )
            seen.add(task_id)
            plane = _load_task_plane(run_dir, wave_id, task_id)
            plane_state = str(plane["state"])
            expected = _projected_task_status(plane_state, wave_status)
            current = field_block(sections[task_id], "Status")
            if expected == "in_progress" and plane_state in {"committed", "merged"}:
                recoverable.append(task_id)
            if current == expected:
                continue
            if current == "committed" and task_id in recoverable:
                mismatched.append(task_id)
                continue
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "handoff has a non-recoverable task status"
            )
    return mismatched, recoverable


def recover_handoff_projection(
    manifest_path: Path,
    run_id: str,
    expected_handoff_sha256: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Repair only the historical unsupported committed handoff projection."""

    if SHA256_RE.fullmatch(expected_handoff_sha256) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "handoff projection recovery identity is invalid"
        )
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir = _run_dir(workspace, run_id)
        coordinator = load_coordinator_state(run_dir)
        if coordinator is None or coordinator.get("status") != "running":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "run has no recoverable coordinator"
            )
        plans = _indexed_tasks(coordinator)
        if coordinator.get("plan_sha256") != sha256_json(plans):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator plan digest is invalid"
            )
        _validate_indexed_state(run_dir, coordinator)

        from prompt_workspace_resume import load_resume_control, _replace_indexed_status

        control = load_resume_control(run_dir, required=True)
        if control["phase"] != "idle":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "handoff projection recovery requires idle resume control",
            )

        text = read_handoff_text(run_dir)
        if text is None:
            raise PromptWorkspaceError("RUN_STATE_INVALID", "handoff is missing")
        current_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        recovery_path = _handoff_projection_recovery_path(run_dir)
        recovery = _load_handoff_projection_recovery(recovery_path, run_id)
        if (
            recovery is not None
            and recovery.get("expected_handoff_sha256") != expected_handoff_sha256
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "handoff projection recovery identity changed",
            )

        mismatched, recoverable = _projection_statuses(run_dir, coordinator, text)
        if recovery is None:
            if current_sha256 != expected_handoff_sha256 or mismatched != recoverable:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "handoff projection is not recoverable"
                )
            updated = text
            for task_id in mismatched:
                updated = _replace_indexed_status(
                    updated, "Task Queue", task_id, "in_progress"
                )
            post_sha256 = hashlib.sha256(updated.encode("utf-8")).hexdigest()
            recovery = {
                "schema": HANDOFF_PROJECTION_RECOVERY_SCHEMA,
                "run_id": run_id,
                "expected_handoff_sha256": expected_handoff_sha256,
                "post_handoff_sha256": post_sha256,
                "task_ids": mismatched,
                "created_at": iso_seconds(clock()),
                "phase": "intent",
            }
            write_exclusive(recovery_path, stable_json(recovery))
        else:
            post_sha256 = str(recovery["post_handoff_sha256"])
            task_ids = list(recovery["task_ids"])
            if current_sha256 == expected_handoff_sha256:
                if mismatched != task_ids or recoverable != task_ids:
                    raise PromptWorkspaceError(
                        "EXECUTION_STATE_INVALID",
                        "handoff projection recovery task identity changed",
                    )
                updated = text
                for task_id in task_ids:
                    updated = _replace_indexed_status(
                        updated, "Task Queue", task_id, "in_progress"
                    )
                if hashlib.sha256(updated.encode("utf-8")).hexdigest() != post_sha256:
                    raise PromptWorkspaceError(
                        "EXECUTION_STATE_INVALID",
                        "handoff projection recovery postimage changed",
                    )
            elif current_sha256 == post_sha256:
                if mismatched or recoverable != task_ids:
                    raise PromptWorkspaceError(
                        "EXECUTION_STATE_INVALID",
                        "handoff projection recovery task identity changed",
                    )
                updated = text
            else:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "handoff projection changed during recovery",
                )

        if current_sha256 == expected_handoff_sha256:
            write_atomic(run_dir / "handoff.md", updated.encode("utf-8"))
        recovery["phase"] = "committed"
        write_atomic(recovery_path, stable_json(recovery))
        return {
            "status": "recovered",
            "run_id": run_id,
            "handoff_sha256": recovery["post_handoff_sha256"],
            "task_ids": list(recovery["task_ids"]),
            "machine_state_changed": False,
        }


def _replacement_suffix(
    run_dir: Path,
    coordinator: dict[str, object],
    old_plan_sha256: str,
) -> tuple[int, list[str]]:
    indexed = coordinator["waves"]
    plans = _indexed_tasks(coordinator)
    candidates: list[tuple[int, list[str]]] = []
    for start in range(1, len(indexed)):
        suffix = indexed[start:]
        suffix_ids = [str(entry["wave_id"]) for entry in suffix]
        expected_ids = [
            f"wave-r{old_plan_sha256[:8]}-{position:03d}"
            for position in range(1, len(suffix) + 1)
        ]
        if (
            suffix_ids == expected_ids
            and sha256_json(plans[start:]) == old_plan_sha256
            and all(
                _load_wave(run_dir, str(entry["wave_id"]))["status"] == "done"
                for entry in indexed[:start]
            )
        ):
            candidates.append((start, suffix_ids))
    if len(candidates) != 1:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "coordinator does not have one recoverable replacement-tail digest",
        )
    return candidates[0]


def _validate_no_live_workers(
    run_dir: Path,
    coordinator: dict[str, object],
    suffix_start: int,
) -> None:
    for entry in coordinator["waves"][suffix_start:]:
        wave_id = str(entry["wave_id"])
        wave = _load_wave(run_dir, wave_id)
        for task_id in wave["task_ids"]:
            plane = _load_task_plane(run_dir, wave_id, str(task_id))
            if plane["state"] in {"assigned", "running"}:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "plan digest recovery cannot change a live worker plan identity",
                )


def _load_basis(run_dir: Path) -> dict[str, object]:
    value = load_json_object(_plan_basis_path(run_dir), "prompt impact plan basis")
    required = {
        "schema",
        "plan_sha256",
        "plan_basis_revision",
        "plan_basis_intent_sha256",
        "latest_settled_revision",
        "latest_settled_intent_sha256",
        "impact_sha256",
        "spec_receipt_sha256",
        "spec_transition_sha256",
        "plan_action",
    }
    if set(value) != required or value.get("schema") != IMPACT_PLAN_SCHEMA:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "prompt impact plan basis is invalid"
        )
    return value


def _pre_recovery_basis(
    basis: dict[str, object],
    coordinator: dict[str, object],
    old_plan_sha256: str,
) -> bool:
    """Recognize only the intact basis written with the historical tail digest."""

    return (
        basis.get("plan_sha256") == old_plan_sha256
        and basis.get("plan_basis_revision") == coordinator.get("prompt_revision")
        and basis.get("plan_basis_intent_sha256")
        == coordinator.get("prompt_intent_sha256")
        and isinstance(basis.get("latest_settled_revision"), str)
        and SHA256_RE.fullmatch(str(basis.get("latest_settled_intent_sha256") or ""))
        is not None
        and SHA256_RE.fullmatch(str(basis.get("impact_sha256") or "")) is not None
        and SHA256_RE.fullmatch(str(basis.get("spec_receipt_sha256") or "")) is not None
        and (
            basis.get("spec_transition_sha256") is None
            or SHA256_RE.fullmatch(str(basis.get("spec_transition_sha256"))) is not None
        )
        and basis.get("plan_action") == "retain_plan"
    )


def _recovered_basis(
    coordinator: dict[str, object],
    new_plan_sha256: str,
    impact: dict[str, object],
    impact_sha256: str,
) -> dict[str, object]:
    return {
        "schema": IMPACT_PLAN_SCHEMA,
        "plan_sha256": new_plan_sha256,
        "plan_basis_revision": coordinator.get("prompt_revision"),
        "plan_basis_intent_sha256": coordinator.get("prompt_intent_sha256"),
        "latest_settled_revision": impact.get("revision"),
        "latest_settled_intent_sha256": impact.get("intent_sha256"),
        "impact_sha256": impact_sha256,
        "spec_receipt_sha256": impact.get("spec_receipt_sha256"),
        "spec_transition_sha256": impact.get("spec_transition_sha256"),
        "plan_action": impact.get("plan_action"),
    }


def _basis_precedes_impact_publication(
    basis: dict[str, object], impact: dict[str, object]
) -> bool:
    """Recognize the sole crash window after impact publication, before intent."""

    transition = impact.get("spec_transition")
    return (
        isinstance(transition, dict)
        and impact.get("prior_impact_sha256") == basis.get("impact_sha256")
        and transition.get("prior_spec_receipt_sha256")
        == basis.get("spec_receipt_sha256")
        and impact.get("revision") == basis.get("latest_settled_revision")
        and impact.get("intent_sha256") == basis.get("latest_settled_intent_sha256")
        and impact.get("plan_action") == "retain_plan"
    )


def _load_recovery(path: Path, run_id: str) -> dict[str, object] | None:
    if path.is_symlink():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "plan digest recovery path is invalid"
        )
    if not path.exists():
        return None
    if not path.is_file():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "plan digest recovery path is invalid"
        )
    value = load_json_object(path, "plan digest recovery")
    required = {
        "schema",
        "run_id",
        "old_plan_sha256",
        "new_plan_sha256",
        "impact_sha256",
        "replacement_wave_ids",
        "recovered_at",
        "phase",
    }
    recovered_at = value.get("recovered_at")
    try:
        recovered_time = datetime.fromisoformat(str(recovered_at))
    except ValueError:
        recovered_time = None
    if (
        set(value) != required
        or value.get("schema") != PLAN_DIGEST_RECOVERY_SCHEMA
        or value.get("run_id") != run_id
        or SHA256_RE.fullmatch(str(value.get("old_plan_sha256") or "")) is None
        or SHA256_RE.fullmatch(str(value.get("new_plan_sha256") or "")) is None
        or SHA256_RE.fullmatch(str(value.get("impact_sha256") or "")) is None
        or value.get("phase")
        not in {"intent", "basis-committed", "coordinator-committed"}
        or not isinstance(value.get("replacement_wave_ids"), list)
        or not value["replacement_wave_ids"]
        or not all(isinstance(item, str) for item in value["replacement_wave_ids"])
        or recovered_time is None
        or recovered_time.tzinfo is None
        or recovered_time.utcoffset() is None
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "plan digest recovery state is invalid"
        )
    return value


def recover_replanned_plan_digest(
    manifest_path: Path,
    run_id: str,
    expected_plan_sha256: str,
    expected_index_sha256: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Repair only the proven replacement-tail digest writer defect."""

    if (
        SHA256_RE.fullmatch(expected_plan_sha256) is None
        or SHA256_RE.fullmatch(expected_index_sha256) is None
        or expected_plan_sha256 == expected_index_sha256
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "plan digest recovery identity is invalid"
        )
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        run_dir = _run_dir(workspace, run_id)
        resume_path = orchestration_dir(run_dir) / "resume-control.json"
        if resume_path.exists() or resume_path.is_symlink():
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "plan digest recovery requires a journal-less coordinator",
            )
        coordinator = load_coordinator_state(run_dir)
        if coordinator is None or coordinator.get("status") != "running":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "run has no recoverable coordinator"
            )
        plans = _indexed_tasks(coordinator)
        full_plan_sha256 = sha256_json(plans)
        if full_plan_sha256 != expected_index_sha256:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator index digest changed"
            )
        _validate_indexed_state(run_dir, coordinator)
        suffix_start, replacement_wave_ids = _replacement_suffix(
            run_dir, coordinator, expected_plan_sha256
        )
        active_wave = coordinator.get("active_wave")
        if active_wave not in replacement_wave_ids:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "active wave is outside the replacement plan"
            )
        _validate_no_live_workers(run_dir, coordinator, suffix_start)

        recovery_path = _recovery_path(run_dir)
        recovery = _load_recovery(recovery_path, run_id)
        basis = _load_basis(run_dir)
        current_impact = load_current_prompt_impact(run_dir, required=True)
        assert current_impact is not None
        impact_before, impact_sha256_before = current_impact
        if recovery is None:
            if coordinator.get("plan_sha256") != expected_plan_sha256:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "coordinator plan digest changed"
                )
            basis_matches_current = basis == _recovered_basis(
                coordinator,
                expected_plan_sha256,
                impact_before,
                impact_sha256_before,
            )
            if not (
                _pre_recovery_basis(basis, coordinator, expected_plan_sha256)
                and (
                    basis_matches_current
                    or _basis_precedes_impact_publication(basis, impact_before)
                )
            ):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "prompt impact plan basis changed"
                )
        elif (
            recovery.get("old_plan_sha256") != expected_plan_sha256
            or recovery.get("new_plan_sha256") != expected_index_sha256
            or recovery.get("replacement_wave_ids") != replacement_wave_ids
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "plan digest recovery identity changed"
            )
        else:
            if impact_sha256_before != recovery.get("impact_sha256"):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "plan digest recovery impact changed"
                )
            inspected = inspect_spec_documents(workspace)
            if any(
                inspected[kind].get("file_sha256")
                != impact_before.get(f"{kind}_sha256")
                for kind in ("requirements", "design")
            ):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "plan digest recovery impact changed"
                )

        run_state = verify_run(workspace, run_id, None)
        refinement_contract = verify_requirements_refinement_contract(
            workspace, run_dir, run_state
        )
        impact = refinement_contract["impact"]
        impact_sha256 = str(refinement_contract["impact_sha256"])
        if not isinstance(impact, dict) or impact.get("plan_action") != "retain_plan":
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED",
                "material prompt impact cannot use plan digest recovery",
            )

        if recovery is None:
            recovered_at = iso_seconds(clock())
            recovery = {
                "schema": PLAN_DIGEST_RECOVERY_SCHEMA,
                "run_id": run_id,
                "old_plan_sha256": expected_plan_sha256,
                "new_plan_sha256": expected_index_sha256,
                "impact_sha256": impact_sha256,
                "replacement_wave_ids": replacement_wave_ids,
                "recovered_at": recovered_at,
                "phase": "intent",
            }
            write_exclusive(recovery_path, stable_json(recovery))
        elif recovery.get("impact_sha256") != impact_sha256:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "plan digest recovery impact changed"
            )

        coordinator_digest = coordinator.get("plan_sha256")
        if coordinator_digest not in {expected_plan_sha256, expected_index_sha256}:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator plan digest changed"
            )
        expected_basis = _recovered_basis(
            coordinator, expected_index_sha256, impact, impact_sha256
        )
        basis_is_old = _pre_recovery_basis(basis, coordinator, expected_plan_sha256)
        basis_is_recovered = basis == expected_basis
        if not basis_is_old and not basis_is_recovered:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "prompt impact plan digest changed"
            )
        if coordinator_digest == expected_index_sha256 and not basis_is_recovered:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "plan digest recovery order is invalid"
            )
        phase = str(recovery["phase"])
        if (
            phase in {"basis-committed", "coordinator-committed"}
            and not basis_is_recovered
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "plan digest recovery phase is invalid"
            )
        if (
            phase == "coordinator-committed"
            and coordinator_digest != expected_index_sha256
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "plan digest recovery phase is invalid"
            )

        if basis_is_old:
            write_atomic(_plan_basis_path(run_dir), stable_json(expected_basis))
        if recovery["phase"] == "intent":
            recovery["phase"] = "basis-committed"
            write_atomic(recovery_path, stable_json(recovery))

        if coordinator_digest == expected_plan_sha256:
            coordinator["plan_sha256"] = expected_index_sha256
            coordinator["updated_at"] = recovery["recovered_at"]
            _save_coordinator(run_dir, coordinator)
        recovery["phase"] = "coordinator-committed"
        write_atomic(recovery_path, stable_json(recovery))

        current = load_coordinator_state(run_dir)
        assert current is not None
        verify_prompt_impact_plan(
            run_dir,
            current,
            Path(required_string(workspace, "source_root", "workspace manifest")),
        )
        if current.get("plan_sha256") != sha256_json(_indexed_tasks(current)):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "recovered coordinator digest is invalid"
            )
        return {
            "status": "recovered",
            "run_id": run_id,
            "plan_sha256": expected_index_sha256,
            "replacement_wave_ids": replacement_wave_ids,
            "live_worker_state_changed": False,
        }
