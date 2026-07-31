#!/usr/bin/env python3
"""Deterministic Agentic SDLC failure routing and repair-budget control."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any


FAILURE_EVENT_SCHEMA = "agentic-sdlc/failure-event-v1"
DIAGNOSIS_SCHEMA = "agentic-sdlc/diagnosis-v1"
CLASSIFICATION_SCHEMA = "agentic-sdlc/failure-classification-v1"
REPAIR_CONTROL_SCHEMA = "agentic-sdlc/repair-control-v1"
REPAIR_JOURNAL_SCHEMA = "agentic-sdlc/repair-journal-event-v1"
DESIGN_APPROVAL_SCHEMA = "agentic-sdlc/design-approval-v1"
REVALIDATION_EVIDENCE_SCHEMA = "agentic-sdlc/revalidation-evidence-v1"

FEATURE_RE = re.compile(r"FEAT-[0-9]{3,}")
COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")
DIGEST_RE = re.compile(r"(?:sha256:)?[0-9a-f]{64}")
CLASSIFICATIONS = {
    "SPEC_GAP",
    "CONTEXT_GAP",
    "DESIGN_DEFECT",
    "PLAN_DEFECT",
    "EXECUTION_PREPARATION_DEFECT",
    "WORKTREE_CONFLICT",
    "REPLAN_REQUIRED",
    "TEST_DEFECT",
    "IMPLEMENTATION_DEFECT",
    "INTEGRATION_CONFLICT",
    "CLEANUP_BLOCKED",
    "PROMOTION_BLOCKED",
    "PROMOTION_FAILED",
    "WORKFLOW_UPGRADE_REQUIRED",
    "VALIDATION_DEFECT",
    "EVALUATION_DEFECT",
    "UAT_DEFECT",
    "DOCUMENTATION_DRIFT",
    "PR_HEAD_DRIFT",
    "ENVIRONMENT_DEFECT",
    "POLICY_BLOCK",
    "HUMAN_INPUT_REQUIRED",
    "UNKNOWN_DEFECT",
}
CAUSE_STATUSES = {"proven", "high_confidence", "probable", "ambiguous", "unknown"}
CONFIDENCE = {"proven", "high_confidence", "probable", "unknown"}
EXECUTION_LIFECYCLES = {
    "unprepared",
    "active_task",
    "waves_completed",
    "sealed",
    "promoted",
    "completed",
}
DIAGNOSIS_RESULTS = {
    "localized_implementation_defect",
    "test_defect",
    "evaluation_defect",
    "environment_defect",
    "spec_gap",
    "design_defect",
    "blocked_missing_evidence",
    "unresolved",
    "policy_block",
    "human_input_required",
}
SYSTEM_CONTRACT_CHANGES = {
    "architecture_topology",
    "component_responsibility",
    "service_boundary",
    "public_interface",
    "data_ownership",
    "data_lifecycle",
    "migration_behavior",
    "security_boundary",
    "cross_component_workflow",
}
EXTERNAL_CHANGE_FLAGS = {
    "requirements",
    "public_contracts",
    "data_lifecycle",
    "security",
    "permissions",
    "deployment_scope",
    "external_behavior",
}
SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\b(?:password|passwd|api[_-]?key|access[_-]?token)\s*[:=]", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"https?://(?:localhost|127\.0\.0\.1|[^/\s]*\.(?:internal|local))\b", re.IGNORECASE),
)

RESULT_TO_CLASSIFICATION = {
    "localized_implementation_defect": "IMPLEMENTATION_DEFECT",
    "test_defect": "TEST_DEFECT",
    "evaluation_defect": "EVALUATION_DEFECT",
    "environment_defect": "ENVIRONMENT_DEFECT",
    "spec_gap": "SPEC_GAP",
    "design_defect": "DESIGN_DEFECT",
    "blocked_missing_evidence": "UNKNOWN_DEFECT",
    "unresolved": "UNKNOWN_DEFECT",
    "policy_block": "POLICY_BLOCK",
    "human_input_required": "HUMAN_INPUT_REQUIRED",
}

DIRECT_ROUTES = {
    "SPEC_GAP": "sdlc-create-requirements",
    "CONTEXT_GAP": "sdlc-gather-context",
    "DESIGN_DEFECT": "sdlc-create-design",
    "PLAN_DEFECT": "sdlc-create-plan",
    "EXECUTION_PREPARATION_DEFECT": "sdlc-prepare-execution",
    "WORKTREE_CONFLICT": "sdlc-prepare-execution",
    "REPLAN_REQUIRED": "sdlc-create-plan",
    "TEST_DEFECT": "sdlc-tdd",
    "INTEGRATION_CONFLICT": "sdlc-implement-plan",
    "CLEANUP_BLOCKED": None,
    "PROMOTION_BLOCKED": "sdlc-commit",
    "PROMOTION_FAILED": None,
    "WORKFLOW_UPGRADE_REQUIRED": None,
    "VALIDATION_DEFECT": "sdlc-validate-codes",
    "EVALUATION_DEFECT": "sdlc-evaluate",
    "UAT_DEFECT": "sdlc-uat-tests",
    "DOCUMENTATION_DRIFT": "sdlc-update-documents",
    "PR_HEAD_DRIFT": None,
    "ENVIRONMENT_DEFECT": None,
    "POLICY_BLOCK": None,
    "HUMAN_INPUT_REQUIRED": None,
    "UNKNOWN_DEFECT": None,
}
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


class RepairControlError(RuntimeError):
    """Fail-closed repair-control error with a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _bounded_string(value: Any, label: str, *, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RepairControlError("RECORD_INVALID", f"{label} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum or any(ord(character) < 32 for character in result):
        raise RepairControlError("RECORD_INVALID", f"{label} is not a bounded text field")
    return result


def _string_list(
    value: Any, label: str, *, required: bool = True, maximum: int = 32
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise RepairControlError("RECORD_INVALID", f"{label} must be a bounded list")
    result = [
        _bounded_string(item, f"{label} item", maximum=1024)
        for item in value
    ]
    if required and not result:
        raise RepairControlError("RECORD_INVALID", f"{label} must not be empty")
    return result


def _contains_sensitive(value: Any) -> bool:
    text = json.dumps(value, sort_keys=True, ensure_ascii=True)
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


def _reject_sensitive(value: Any) -> None:
    if _contains_sensitive(value):
        raise RepairControlError(
            "SECURITY_BLOCKER", "structured failure evidence contains sensitive material"
        )


def _now(value: Any) -> str:
    candidate = _bounded_string(value, "created_at", maximum=64)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", candidate) is None:
        raise RepairControlError("RECORD_INVALID", "created_at must be RFC3339 UTC")
    return candidate


def _normalize_blocker_field(value: Any, label: str) -> str:
    candidate = _bounded_string(value, label, maximum=128).lower()
    if COMMIT_RE.search(candidate):
        raise RepairControlError(
            "RECORD_INVALID", f"{label} must exclude commit or content hashes"
        )
    candidate = re.sub(r":\d+\b", "", candidate)
    candidate = re.sub(r"\b\d{4}-\d{2}-\d{2}t[^\s|]+", "", candidate)
    candidate = re.sub(r"\b(?:request|run|job|attempt)[-_ ]?[0-9a-f]{6,}\b", "", candidate)
    candidate = re.sub(r"\s+", "-", candidate).strip("-| ")
    if not candidate:
        raise RepairControlError("RECORD_INVALID", f"{label} has no stable content")
    return candidate


def _feature_id(value: Any) -> str:
    candidate = _bounded_string(value, "feature_id", maximum=32)
    if FEATURE_RE.fullmatch(candidate) is None:
        raise RepairControlError("RECORD_INVALID", "feature_id must match FEAT-NNN")
    return candidate


def _validate_digests(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value or len(value) > 32:
        raise RepairControlError("RECORD_INVALID", f"{label} must be a non-empty object")
    result: dict[str, str] = {}
    for raw_key, raw_digest in value.items():
        key = _bounded_string(raw_key, f"{label} key", maximum=128)
        digest = _bounded_string(raw_digest, f"{label}.{key}", maximum=72).lower()
        if DIGEST_RE.fullmatch(digest) is None:
            raise RepairControlError(
                "RECORD_INVALID", f"{label}.{key} must be a SHA-256 digest"
            )
        result[key] = digest if digest.startswith("sha256:") else f"sha256:{digest}"
    return result


def _identity_payload(value: dict[str, Any], *derived: str) -> dict[str, Any]:
    excluded = {"created_at", "updated_at", "checkpoint_id", *derived}
    return {key: item for key, item in value.items() if key not in excluded}


def build_failure_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize and identify one immutable SDLC gate failure."""

    if payload.get("schema") != FAILURE_EVENT_SCHEMA:
        raise RepairControlError("RECORD_INVALID", "failure event schema is invalid")
    feature_id = _feature_id(payload.get("feature_id"))
    phase = _bounded_string(payload.get("phase"), "phase", maximum=64)
    criterion_id = _bounded_string(
        payload.get("criterion_id"), "criterion_id", maximum=128
    )
    integration_commit = _bounded_string(
        payload.get("integration_commit"), "integration_commit", maximum=64
    ).lower()
    if COMMIT_RE.fullmatch(integration_commit) is None:
        raise RepairControlError(
            "RECORD_INVALID", "integration_commit must be a full Git object ID"
        )
    cause_status = _bounded_string(
        payload.get("cause_status"), "cause_status", maximum=32
    )
    if cause_status not in CAUSE_STATUSES:
        raise RepairControlError("RECORD_INVALID", "cause_status is unsupported")
    proposed = payload.get("proposed_classification")
    if proposed is not None:
        proposed = _bounded_string(
            proposed, "proposed_classification", maximum=64
        ).upper()
        if proposed not in CLASSIFICATIONS:
            raise RepairControlError(
                "RECORD_INVALID", "proposed_classification is unsupported"
            )
    component = _normalize_blocker_field(payload.get("component"), "component")
    operation = _normalize_blocker_field(payload.get("operation"), "operation")
    error_class = _normalize_blocker_field(payload.get("error_class"), "error_class")
    source_boundary = _normalize_blocker_field(
        payload.get("source_boundary"), "source_boundary"
    )
    blocker_key = "|".join((component, operation, error_class, source_boundary))
    result = {
        "schema": FAILURE_EVENT_SCHEMA,
        "feature_id": feature_id,
        "phase": phase,
        "criterion_id": criterion_id,
        "expected": _bounded_string(payload.get("expected"), "expected"),
        "observed": _bounded_string(payload.get("observed"), "observed"),
        "evidence_digests": _validate_digests(
            payload.get("evidence_digests"), "evidence_digests"
        ),
        "reproduction": _bounded_string(payload.get("reproduction"), "reproduction"),
        "integration_commit": integration_commit,
        "fingerprints": _validate_digests(
            payload.get("fingerprints"), "fingerprints"
        ),
        "component": component,
        "operation": operation,
        "error_class": error_class,
        "source_boundary": source_boundary,
        "blocker_key": blocker_key,
        "cause_status": cause_status,
        "proposed_classification": proposed,
        "execution_lifecycle": _bounded_string(
            payload.get("execution_lifecycle", "unprepared"),
            "execution_lifecycle",
            maximum=32,
        ),
        "independent_blocker_evidence": (
            _bounded_string(
                payload["independent_blocker_evidence"],
                "independent_blocker_evidence",
                maximum=1024,
            )
            if payload.get("independent_blocker_evidence")
            else None
        ),
        "design_gate": payload.get("design_gate"),
        "created_at": _now(payload.get("created_at")),
    }
    if result["execution_lifecycle"] not in EXECUTION_LIFECYCLES:
        raise RepairControlError(
            "RECORD_INVALID", "execution_lifecycle is unsupported"
        )
    _reject_sensitive(result)
    result["event_id"] = _digest(_identity_payload(result, "event_id", "blocker_key"))
    return result


def _validate_design_gate(
    value: Any, *, require_human_approval_id: bool = True
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RepairControlError(
            "DESIGN_ADMISSION_DENIED", "design_gate must be a structured object"
        )
    required_true = (
        "requirements_stable",
        "evaluator_valid",
        "environment_valid",
        "reproducible",
    )
    for field in required_true:
        if value.get(field) is not True:
            raise RepairControlError(
                "DESIGN_ADMISSION_DENIED", f"design_gate.{field} must be true"
            )
    changes = _string_list(value.get("system_contract_changes"), "system_contract_changes")
    unknown = sorted(set(changes) - SYSTEM_CONTRACT_CHANGES)
    if unknown:
        raise RepairControlError(
            "DESIGN_ADMISSION_DENIED",
            f"unsupported system-contract changes: {', '.join(unknown)}",
        )
    result: dict[str, Any] = {
        **{field: True for field in required_true},
        "system_contract_changes": changes,
        "violated_design_contract": _bounded_string(
            value.get("violated_design_contract"), "violated_design_contract"
        ),
        "localized_repair_insufficient_evidence": _string_list(
            value.get("localized_repair_insufficient_evidence"),
            "localized_repair_insufficient_evidence",
        ),
        "affected_features": _string_list(
            value.get("affected_features"), "affected_features"
        ),
        "invalidation_scope": _string_list(
            value.get("invalidation_scope"), "invalidation_scope"
        ),
        "estimated_work": _bounded_string(
            value.get("estimated_work"), "estimated_work", maximum=512
        ),
        "rollback_path": _bounded_string(
            value.get("rollback_path"), "rollback_path"
        ),
        "external_change_flags": value.get("external_change_flags", {}),
        "human_approval_id": value.get("human_approval_id"),
    }
    if any(FEATURE_RE.fullmatch(item) is None for item in result["affected_features"]):
        raise RepairControlError(
            "DESIGN_ADMISSION_DENIED", "affected_features contains an invalid FEAT ID"
        )
    flags = result["external_change_flags"]
    if (
        not isinstance(flags, dict)
        or set(flags) != EXTERNAL_CHANGE_FLAGS
        or any(not isinstance(item, bool) for item in flags.values())
    ):
        raise RepairControlError(
            "DESIGN_ADMISSION_DENIED",
            "external_change_flags must explicitly cover every approval boundary",
        )
    broader = any(flags.values())
    if broader:
        if require_human_approval_id:
            result["human_approval_id"] = _bounded_string(
                result["human_approval_id"], "human_approval_id", maximum=256
            )
        elif result["human_approval_id"] is not None:
            raise RepairControlError(
                "DESIGN_ADMISSION_DENIED",
                "approval draft must not predeclare human_approval_id",
            )
        result["approval_mode"] = "human"
    else:
        if result["human_approval_id"] is not None:
            result["human_approval_id"] = _bounded_string(
                result["human_approval_id"], "human_approval_id", maximum=256
            )
        result["approval_mode"] = "automatic_internal_reconsideration"
    return result


def build_diagnosis(payload: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Validate one troubleshooting handoff against its immutable failure."""

    if payload.get("schema") != DIAGNOSIS_SCHEMA:
        raise RepairControlError("DIAGNOSIS_INVALID", "diagnosis schema is invalid")
    if _feature_id(payload.get("feature_id")) != event["feature_id"]:
        raise RepairControlError("DIAGNOSIS_INVALID", "diagnosis feature does not match")
    if payload.get("event_id") != event["event_id"]:
        raise RepairControlError("DIAGNOSIS_INVALID", "diagnosis event does not match")
    if payload.get("blocker_key") != event["blocker_key"]:
        raise RepairControlError("DIAGNOSIS_INVALID", "diagnosis blocker does not match")
    result_name = _bounded_string(payload.get("result"), "result", maximum=64)
    if result_name not in DIAGNOSIS_RESULTS:
        raise RepairControlError("DIAGNOSIS_INVALID", "diagnosis result is unsupported")
    confidence = _bounded_string(payload.get("confidence"), "confidence", maximum=32)
    if confidence not in CONFIDENCE:
        raise RepairControlError("DIAGNOSIS_INVALID", "diagnosis confidence is unsupported")
    authorizes_change = result_name not in {
        "blocked_missing_evidence",
        "unresolved",
        "environment_defect",
        "policy_block",
        "human_input_required",
    }
    if authorizes_change and confidence not in {"proven", "high_confidence"}:
        raise RepairControlError(
            "DIAGNOSIS_INCOMPLETE",
            "probable or unknown diagnosis cannot authorize repair or redesign",
        )
    diagnosis = {
        "schema": DIAGNOSIS_SCHEMA,
        "feature_id": event["feature_id"],
        "event_id": event["event_id"],
        "blocker_key": event["blocker_key"],
        "result": result_name,
        "confidence": confidence,
        "expected": _bounded_string(
            payload.get("expected", event["expected"]), "expected"
        ),
        "observed": _bounded_string(
            payload.get("observed", event["observed"]), "observed"
        ),
        "earliest_divergence": payload.get("earliest_divergence"),
        "violated_invariant": payload.get("violated_invariant"),
        "causal_chain": payload.get("causal_chain", []),
        "bounded_repair_target": payload.get("bounded_repair_target"),
        "counterfactual": payload.get("counterfactual"),
        "alternatives_eliminated": payload.get("alternatives_eliminated", []),
        "affected_files": payload.get("affected_files", []),
        "regression_oracle": payload.get("regression_oracle"),
        "required_regression_test": payload.get("required_regression_test"),
        "evidence_references": payload.get("evidence_references", []),
        "constraints": payload.get("constraints", []),
        "design_gate": payload.get("design_gate"),
        "created_at": _now(payload.get("created_at")),
    }
    if result_name in {
        "localized_implementation_defect",
        "test_defect",
        "evaluation_defect",
        "spec_gap",
        "design_defect",
    }:
        divergence = diagnosis["earliest_divergence"]
        if not isinstance(divergence, dict):
            raise RepairControlError(
                "DIAGNOSIS_INCOMPLETE", "earliest_divergence must be structured"
            )
        diagnosis["earliest_divergence"] = {
            field: _bounded_string(divergence.get(field), f"earliest_divergence.{field}")
            for field in ("component", "operation", "source_boundary")
        }
        diagnosis["violated_invariant"] = _bounded_string(
            diagnosis["violated_invariant"], "violated_invariant"
        )
        diagnosis["causal_chain"] = _string_list(
            diagnosis["causal_chain"], "causal_chain"
        )
        diagnosis["counterfactual"] = _bounded_string(
            diagnosis["counterfactual"], "counterfactual"
        )
        diagnosis["alternatives_eliminated"] = _string_list(
            diagnosis["alternatives_eliminated"], "alternatives_eliminated"
        )
        diagnosis["regression_oracle"] = _bounded_string(
            diagnosis["regression_oracle"], "regression_oracle"
        )
        diagnosis["evidence_references"] = _string_list(
            diagnosis["evidence_references"], "evidence_references"
        )
        diagnosis["constraints"] = _string_list(
            diagnosis["constraints"], "constraints"
        )
    if result_name == "localized_implementation_defect":
        diagnosis["bounded_repair_target"] = _bounded_string(
            diagnosis["bounded_repair_target"], "bounded_repair_target"
        )
        diagnosis["affected_files"] = _string_list(
            diagnosis["affected_files"], "affected_files"
        )
        diagnosis["required_regression_test"] = _bounded_string(
            diagnosis["required_regression_test"], "required_regression_test"
        )
    if result_name == "design_defect":
        diagnosis["design_gate"] = _validate_design_gate(diagnosis["design_gate"])
    _reject_sensitive(diagnosis)
    diagnosis["diagnosis_id"] = _digest(
        _identity_payload(diagnosis, "diagnosis_id")
    )
    return diagnosis


def _approval_gate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID", "approval design_gate must be an object"
        )
    draft = dict(value)
    draft["human_approval_id"] = None
    return _validate_design_gate(draft, require_human_approval_id=False)


def _design_gate_digest(value: Any) -> str:
    return _digest(_approval_gate(value))


def build_design_approval(
    payload: dict[str, Any], event: dict[str, Any]
) -> dict[str, Any]:
    if payload.get("schema") != DESIGN_APPROVAL_SCHEMA:
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID", "design approval schema is invalid"
        )
    feature_id = _feature_id(payload.get("feature_id"))
    if feature_id != event["feature_id"] or payload.get("event_id") != event["event_id"]:
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID", "design approval failure identity changed"
        )
    gate = _approval_gate(payload.get("design_gate"))
    if not any(gate["external_change_flags"].values()):
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID",
            "human approval is only valid for a broader design change",
        )
    if payload.get("decision") != "approved":
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID", "design approval decision must be approved"
        )
    approval = {
        "schema": DESIGN_APPROVAL_SCHEMA,
        "feature_id": feature_id,
        "event_id": event["event_id"],
        "blocker_key": event["blocker_key"],
        "decision": "approved",
        "design_gate": gate,
        "design_gate_digest": _design_gate_digest(gate),
        "approver": _bounded_string(payload.get("approver"), "approver", maximum=256),
        "approval_evidence_reference": _bounded_string(
            payload.get("approval_evidence_reference"),
            "approval_evidence_reference",
            maximum=1024,
        ),
        "approval_evidence_digest": _bounded_string(
            payload.get("approval_evidence_digest"),
            "approval_evidence_digest",
            maximum=80,
        ),
        "approval_statement": _bounded_string(
            payload.get("approval_statement"),
            "approval_statement",
            maximum=512,
        ),
        "approved_at": _now(payload.get("approved_at")),
    }
    if DIGEST_RE.fullmatch(approval["approval_evidence_digest"]) is None:
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID",
            "approval evidence digest must be SHA-256",
        )
    _reject_sensitive(approval)
    approval["approval_id"] = _digest(_identity_payload(approval, "approval_id"))
    return approval


def _repair_root(run_dir: Path, feature_id: str) -> Path:
    candidate = run_dir.expanduser().absolute()
    if candidate.is_symlink():
        raise RepairControlError(
            "STATE_INVALID", "repair-control run directory is symlinked"
        )
    return candidate.resolve() / "repairs" / feature_id


def _control_path(run_dir: Path, feature_id: str) -> Path:
    return _repair_root(run_dir, feature_id) / "repair-control.json"


def _event_path(run_dir: Path, feature_id: str, event_id: str) -> Path:
    return _repair_root(run_dir, feature_id) / "events" / f"{event_id}.json"


def _diagnosis_path(run_dir: Path, feature_id: str, diagnosis_id: str) -> Path:
    return _repair_root(run_dir, feature_id) / "diagnoses" / f"{diagnosis_id}.json"


def _classification_path(
    run_dir: Path, feature_id: str, classification_id: str
) -> Path:
    return (
        _repair_root(run_dir, feature_id)
        / "classifications"
        / f"{classification_id}.json"
    )


def _approval_path(run_dir: Path, feature_id: str, approval_id: str) -> Path:
    return (
        _repair_root(run_dir, feature_id)
        / "approvals"
        / f"{approval_id}.json"
    )


def _revalidation_path(
    run_dir: Path, feature_id: str, revalidation_id: str
) -> Path:
    return (
        _repair_root(run_dir, feature_id)
        / "revalidations"
        / f"{revalidation_id}.json"
    )


def _journal_path(run_dir: Path, feature_id: str) -> Path:
    return _repair_root(run_dir, feature_id) / "repair-journal.jsonl"


def _reject_unsafe_target(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise RepairControlError(
                "STATE_INVALID", "repair-control path is symlinked"
            )
        if candidate.name in {"repairs", "evidence"}:
            return
    raise RepairControlError(
        "STATE_INVALID", "repair-control path is outside a private state subtree"
    )


@contextmanager
def _transition_lock(run_dir: Path, feature_id: str):
    root = _repair_root(run_dir, _feature_id(feature_id))
    _reject_unsafe_target(root)
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    path = root / ".repair-control.lock"
    _reject_unsafe_target(path)
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise RepairControlError(
            "STATE_INVALID", "repair-control transition lock is unsafe"
        ) from exc
    os.fchmod(descriptor, 0o600)
    deadline = time.monotonic() + 10
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RepairControlError(
                        "STATE_BUSY", "repair-control transition lock is busy"
                    )
                time.sleep(0.05)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    _reject_unsafe_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    _reject_unsafe_target(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepairControlError("STATE_INVALID", f"cannot read {path.name}") from exc
    if not isinstance(value, dict):
        raise RepairControlError("STATE_INVALID", f"{path.name} is not an object")
    return value


def _append_journal(
    run_dir: Path, feature_id: str, event: str, transition_id: str, **details: Any
) -> None:
    path = _journal_path(run_dir, feature_id)
    _reject_unsafe_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    record = {
        "schema": REPAIR_JOURNAL_SCHEMA,
        "event": event,
        "transition_id": transition_id,
        **details,
    }
    _reject_sensitive(record)
    if path.exists():
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                existing = json.loads(raw_line)
                if (
                    existing.get("event") == event
                    and existing.get("transition_id") == transition_id
                ):
                    if existing != record:
                        raise RepairControlError(
                            "STATE_TAMPERED",
                            "repair journal transition content changed",
                        )
                    return
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RepairControlError(
                "STATE_INVALID", "repair journal is unreadable or malformed"
            ) from exc
    try:
        descriptor = os.open(
            path,
            os.O_APPEND
            | os.O_CREAT
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise RepairControlError(
            "STATE_INVALID", "repair journal path is unsafe"
        ) from exc
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _new_blocker(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "blocker_key": event["blocker_key"],
        "tranche": 1,
        "started_at": event["created_at"],
        "active_seconds": 0,
        "attempt_limit": 3,
        "localized_limit": 2,
        "design_limit": 1,
        "time_limit_seconds": 3600,
        "localized_attempts": 0,
        "design_attempts": 0,
        "total_attempts": 0,
        "attempts": [],
        "experiments": [],
        "low_information_experiments": 0,
        "model_rebuild_required": False,
        "design_remediated": False,
        "status": "active",
        "stop_trigger": None,
    }


def _new_control(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": REPAIR_CONTROL_SCHEMA,
        "feature_id": event["feature_id"],
        "active_blocker": _new_blocker(event),
        "previous_blockers": [],
        "feature_dispatches": 0,
        "feature_dispatch_limit": 4,
        "route_history": [],
        "invalidations": [],
        "current_event_id": event["event_id"],
        "current_diagnosis_id": None,
        "current_classification_id": None,
        "revalidation": None,
        "status": "active",
        "updated_at": event["created_at"],
    }


def _load_control(run_dir: Path, feature_id: str) -> dict[str, Any]:
    control = _read_json(_control_path(run_dir, feature_id))
    if (
        control.get("schema") != REPAIR_CONTROL_SCHEMA
        or control.get("feature_id") != feature_id
        or not isinstance(control.get("active_blocker"), dict)
    ):
        raise RepairControlError("STATE_INVALID", "repair control schema is invalid")
    return control


def _store_immutable(path: Path, value: dict[str, Any], label: str) -> bool:
    if path.exists() or path.is_symlink():
        existing = _read_json(path)
        if existing != value:
            raise RepairControlError("STATE_TAMPERED", f"{label} content changed")
        return False
    _write_json_atomic(path, value)
    return True


def _archive_active_blocker(control: dict[str, Any]) -> None:
    blocker = control["active_blocker"]
    control["previous_blockers"].append(
        {
            "blocker_key": blocker["blocker_key"],
            "status": blocker["status"],
            "total_attempts": blocker["total_attempts"],
            "active_seconds": blocker["active_seconds"],
            "stop_trigger": blocker["stop_trigger"],
        }
    )


def _record_failure_event_unlocked(
    run_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    event = build_failure_event(payload)
    path = _event_path(run_dir, event["feature_id"], event["event_id"])
    if path.exists() or path.is_symlink():
        existing = _read_json(path)
        if _identity_payload(existing, "event_id", "blocker_key") != _identity_payload(
            event, "event_id", "blocker_key"
        ):
            raise RepairControlError("STATE_TAMPERED", "failure event content changed")
        event = existing
        created = False
    else:
        created = _store_immutable(path, event, "failure event")
    control_path = _control_path(run_dir, event["feature_id"])
    transition_id = _digest(
        {"action": "record-failure", "event_id": event["event_id"]}
    )
    if control_path.exists():
        control = _load_control(run_dir, event["feature_id"])
        current_key = control["active_blocker"]["blocker_key"]
        if not created and control.get("current_event_id") == event["event_id"]:
            _append_journal(
                run_dir,
                event["feature_id"],
                "failure-recorded",
                transition_id,
                event_id=event["event_id"],
                blocker_key=event["blocker_key"],
            )
            _write_failure_log(run_dir, event["feature_id"], control)
            return {"created": False, "event": event, "control": control}
        if current_key != event["blocker_key"]:
            if not event.get("independent_blocker_evidence"):
                raise RepairControlError(
                    "BLOCKER_IDENTITY_UNPROVEN",
                    "a different blocker requires causal-independence evidence",
                )
            _archive_active_blocker(control)
            control["active_blocker"] = _new_blocker(event)
            control["status"] = "active"
        control["current_event_id"] = event["event_id"]
        control["current_diagnosis_id"] = None
        control["current_classification_id"] = None
        control["revalidation"] = None
        control["updated_at"] = event["created_at"]
    else:
        control = _new_control(event)
    _write_json_atomic(control_path, control)
    _append_journal(
        run_dir,
        event["feature_id"],
        "failure-recorded",
        transition_id,
        event_id=event["event_id"],
        blocker_key=event["blocker_key"],
    )
    _write_failure_log(run_dir, event["feature_id"], control)
    return {"created": created, "event": event, "control": control}


def _load_event(run_dir: Path, feature_id: str, event_id: str) -> dict[str, Any]:
    event = _read_json(_event_path(run_dir, feature_id, event_id))
    rebuilt = build_failure_event(event)
    if rebuilt != event or rebuilt["event_id"] != event_id:
        raise RepairControlError("STATE_TAMPERED", "failure event digest is invalid")
    return event


def _verify_human_approval_source(
    run_dir: Path, approval: dict[str, Any]
) -> None:
    canonical_run = run_dir.expanduser().absolute().resolve()
    inputs_root = canonical_run / "inputs"
    if inputs_root.is_symlink():
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID", "approval input root is symlinked"
        )
    reference = Path(approval["approval_evidence_reference"])
    if reference.is_absolute() or ".." in reference.parts:
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID",
            "approval evidence must be a relative private input snapshot",
        )
    candidate = canonical_run / reference
    current = canonical_run
    for part in reference.parts:
        current = current / part
        if current.is_symlink():
            raise RepairControlError(
                "DESIGN_APPROVAL_INVALID", "approval evidence path is symlinked"
            )
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(inputs_root.resolve(strict=True))
        evidence = resolved.read_bytes()
    except (OSError, ValueError) as exc:
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID",
            "approval evidence is not a readable private input snapshot",
        ) from exc
    recorded_digest = approval["approval_evidence_digest"].removeprefix("sha256:")
    if hashlib.sha256(evidence).hexdigest() != recorded_digest:
        raise RepairControlError(
            "STATE_TAMPERED", "human approval input snapshot digest changed"
        )
    try:
        text = evidence.decode("utf-8")
    except UnicodeError as exc:
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID", "approval input snapshot is not UTF-8"
        ) from exc
    statement = approval["approval_statement"]
    if statement not in text or re.search(r"\bapprov(?:e|ed|es|al)\b", statement, re.I) is None:
        raise RepairControlError(
            "DESIGN_APPROVAL_INVALID",
            "approval statement is not an explicit excerpt from the user input",
        )


def _verify_revalidation_evidence_source(
    run_dir: Path, record: dict[str, Any]
) -> dict[str, Any]:
    canonical_run = run_dir.expanduser().absolute().resolve()
    evidence_root = canonical_run / "evidence"
    reference = Path(record["evidence_reference"])
    if (
        evidence_root.is_symlink()
        or reference.is_absolute()
        or ".." in reference.parts
    ):
        raise RepairControlError(
            "REVALIDATION_INVALID",
            "revalidation evidence must be a relative private evidence file",
        )
    candidate = canonical_run / reference
    current = canonical_run
    for part in reference.parts:
        current = current / part
        if current.is_symlink():
            raise RepairControlError(
                "REVALIDATION_INVALID", "revalidation evidence path is symlinked"
            )
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(evidence_root.resolve(strict=True))
        evidence = resolved.read_bytes()
    except (OSError, ValueError) as exc:
        raise RepairControlError(
            "REVALIDATION_INVALID",
            "revalidation evidence is not a readable private evidence file",
        ) from exc
    recorded_digest = record["evidence_digest"].removeprefix("sha256:")
    if hashlib.sha256(evidence).hexdigest() != recorded_digest:
        raise RepairControlError(
            "STATE_TAMPERED", "revalidation evidence digest changed"
        )
    try:
        gate = json.loads(evidence.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RepairControlError(
            "REVALIDATION_INVALID",
            "revalidation source must be structured UTF-8 JSON",
        ) from exc
    if (
        not isinstance(gate, dict)
        or gate.get("schema") != "agentic-sdlc/gate-evidence-v1"
        or gate.get("feature_id") != record["feature_id"]
        or gate.get("surface") != record["surface"]
        or gate.get("owner_skill") != record["next_recommended_skill"]
        or gate.get("status") != "passed"
        or gate.get("integration_commit") != record["integration_commit"]
        or gate.get("fingerprints") != record["fingerprints"]
        or not isinstance(gate.get("evidence"), list)
        or not gate["evidence"]
    ):
        raise RepairControlError(
            "REVALIDATION_INVALID",
            "phase-owned gate evidence is not an exact passing result",
        )
    _reject_sensitive(gate)
    return gate


def _load_private_state_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise RepairControlError("STATE_INVALID", f"{label} is symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepairControlError("STATE_INVALID", f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise RepairControlError("STATE_INVALID", f"{label} is not an object")
    return value


def _current_fingerprints(run_dir: Path) -> dict[str, str]:
    state = _load_private_state_json(run_dir.resolve() / "current-state.json", "current state")
    identifiers = state.get("fingerprint_ids")
    if not isinstance(identifiers, list) or not identifiers:
        raise RepairControlError(
            "REVALIDATION_INVALID", "current state has no fingerprint identities"
        )
    fingerprints: dict[str, str] = {}
    for identifier in identifiers:
        if not isinstance(identifier, str) or ":" not in identifier:
            raise RepairControlError(
                "REVALIDATION_INVALID", "current fingerprint identity is malformed"
            )
        name, value = identifier.split(":", 1)
        if name in fingerprints or DIGEST_RE.fullmatch(value) is None:
            raise RepairControlError(
                "REVALIDATION_INVALID", "current fingerprint identity is malformed"
            )
        fingerprints[name] = value
    if not {"requirements", "design", "plan"}.issubset(fingerprints):
        raise RepairControlError(
            "REVALIDATION_INVALID",
            "current workflow fingerprints are incomplete",
        )
    return fingerprints


def _git_output(project: Path, arguments: list[str], label: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepairControlError("REVALIDATION_INVALID", f"{label} is unavailable") from exc
    if result.returncode != 0:
        raise RepairControlError("REVALIDATION_INVALID", f"{label} is unavailable")
    return result.stdout.strip()


def _current_integration_head(run_dir: Path, coordinator: dict[str, Any]) -> str:
    worktree_value = coordinator.get("integration_worktree")
    if not isinstance(worktree_value, str) or not worktree_value:
        raise RepairControlError(
            "REVALIDATION_INVALID", "integration worktree identity is missing"
        )
    worktree = Path(worktree_value)
    try:
        worktree.resolve(strict=True).relative_to(run_dir.resolve())
    except (OSError, ValueError) as exc:
        raise RepairControlError(
            "REVALIDATION_INVALID", "integration worktree escapes the private run"
        ) from exc
    observed = _git_output(
        worktree, ["rev-parse", "HEAD"], "current integration commit"
    ).lower()
    if COMMIT_RE.fullmatch(observed) is None:
        raise RepairControlError(
            "REVALIDATION_INVALID", "current integration commit is malformed"
        )
    return observed


def _current_promoted_head(run_dir: Path, coordinator: dict[str, Any]) -> str:
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
        or COMMIT_RE.fullmatch(promoted.lower()) is None
        or coordinator.get("integration_head") != promoted
        or not isinstance(project_value, str)
        or not project_value
        or not isinstance(selected_value, str)
        or not selected_value
        or not isinstance(integration_value, str)
        or not integration_value
        or not isinstance(integration_branch, str)
        or not integration_branch
        or not isinstance(base_branch, str)
        or not base_branch
    ):
        raise RepairControlError(
            "REVALIDATION_INVALID",
            "commit revalidation requires completed promotion and cleanup",
        )
    project_candidate = Path(project_value)
    selected_candidate = Path(selected_value)
    if project_candidate.is_symlink() or selected_candidate.is_symlink():
        raise RepairControlError(
            "REVALIDATION_INVALID", "promoted project identity is symlinked"
        )
    try:
        project = project_candidate.resolve(strict=True)
        selected = selected_candidate.resolve(strict=True)
        selected.relative_to(project)
    except (OSError, ValueError) as exc:
        raise RepairControlError(
            "REVALIDATION_INVALID", "promoted project identity is invalid"
        ) from exc
    observed_root = _git_output(
        project, ["rev-parse", "--show-toplevel"], "promoted project root"
    )
    try:
        if Path(observed_root).resolve(strict=True) != project:
            raise RepairControlError(
                "REVALIDATION_INVALID", "promoted project root changed"
            )
    except OSError as exc:
        raise RepairControlError(
            "REVALIDATION_INVALID", "promoted project root is unavailable"
        ) from exc
    observed = _git_output(
        project, ["rev-parse", "HEAD"], "current promoted commit"
    ).lower()
    if observed != promoted.lower():
        raise RepairControlError(
            "REVALIDATION_INVALID", "promoted project HEAD drifted"
        )
    if _git_output(
        project, ["branch", "--show-current"], "promoted project branch"
    ) != base_branch:
        raise RepairControlError(
            "REVALIDATION_INVALID", "promoted project branch drifted"
        )
    if _git_output(
        project,
        ["status", "--porcelain=v1", "--untracked-files=normal"],
        "promoted project status",
    ):
        raise RepairControlError(
            "REVALIDATION_INVALID", "promoted project checkout is dirty"
        )
    integration = Path(integration_value)
    if integration.exists() or integration.is_symlink():
        raise RepairControlError(
            "REVALIDATION_INVALID", "integration worktree cleanup is incomplete"
        )
    try:
        integration.resolve(strict=False).relative_to(run_dir.resolve())
    except ValueError as exc:
        raise RepairControlError(
            "REVALIDATION_INVALID", "integration worktree identity escapes the private run"
        ) from exc
    try:
        branch_result = subprocess.run(
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
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepairControlError(
            "REVALIDATION_INVALID", "integration branch cleanup is unverifiable"
        ) from exc
    if branch_result.returncode != 1:
        raise RepairControlError(
            "REVALIDATION_INVALID", "integration branch cleanup is incomplete"
        )
    return promoted.lower()


def _current_revalidation_commit(
    run_dir: Path, feature_id: str, surface: str
) -> str:
    coordinator = _load_private_state_json(
        run_dir.resolve() / "execution" / feature_id / "coordinator.json",
        "execution coordinator",
    )
    if coordinator.get("schema") != "agentic-sdlc/execution-coordinator-v4":
        raise RepairControlError(
            "REVALIDATION_INVALID", "current execution coordinator is unsupported"
        )
    if surface == "commit":
        return _current_promoted_head(run_dir, coordinator)
    return _current_integration_head(run_dir, coordinator)


def _verify_revalidation_authority(
    run_dir: Path,
    control: dict[str, Any],
    record: dict[str, Any],
    *,
    require_current_gate: bool = True,
) -> None:
    revalidation = control["revalidation"]
    classification = _load_classification(
        run_dir, control["feature_id"], revalidation["classification_id"]
    )
    invalidates = classification.get("invalidates")
    if (
        not isinstance(invalidates, list)
        or any(surface not in REVALIDATION_ROUTES for surface in invalidates)
    ):
        raise RepairControlError(
            "STATE_TAMPERED", "classification invalidation route is invalid"
        )
    expected_required = [
        {
            "surface": surface,
            "next_recommended_skill": REVALIDATION_ROUTES[surface],
        }
        for surface in invalidates
    ]
    projection = {
        "schema": "agentic-sdlc/revalidation-cursor-v1",
        "classification_id": classification["classification_id"],
        "repair_dispatch_id": revalidation.get("repair_dispatch_id"),
        "required": expected_required,
    }
    attempt = next(
        (
            item
            for item in control["active_blocker"].get("attempts", [])
            if item.get("dispatch_id") == revalidation.get("repair_dispatch_id")
        ),
        None,
    )
    if (
        classification.get("feature_id") != control["feature_id"]
        or classification.get("event_id") != control["current_event_id"]
        or classification.get("blocker_key")
        != control["active_blocker"]["blocker_key"]
        or revalidation.get("required") != expected_required
        or revalidation.get("cursor_id") != _digest(projection)
        or attempt is None
        or attempt.get("classification_id") != classification["classification_id"]
        or attempt.get("status") != "completed"
        or attempt.get("result") != "succeeded"
        or record.get("cursor_id") != revalidation.get("cursor_id")
    ):
        raise RepairControlError(
            "STATE_TAMPERED",
            "revalidation cursor is not bound to the proven repair transition",
        )
    if require_current_gate:
        if record["integration_commit"] != _current_revalidation_commit(
            run_dir, control["feature_id"], record["surface"]
        ):
            raise RepairControlError(
                "REVALIDATION_INVALID",
                "revalidation evidence is stale for the current gate commit",
            )
        if record["fingerprints"] != _current_fingerprints(run_dir):
            raise RepairControlError(
                "REVALIDATION_INVALID",
                "revalidation evidence is stale for current workflow fingerprints",
            )


def _record_design_approval_unlocked(
    run_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    feature_id = _feature_id(payload.get("feature_id"))
    event_id = _bounded_string(payload.get("event_id"), "event_id", maximum=64)
    event = _load_event(run_dir, feature_id, event_id)
    approval = build_design_approval(payload, event)
    _verify_human_approval_source(run_dir, approval)
    created = _store_immutable(
        _approval_path(run_dir, feature_id, approval["approval_id"]),
        approval,
        "design approval",
    )
    transition_id = _digest(
        {"action": "record-design-approval", "approval_id": approval["approval_id"]}
    )
    _append_journal(
        run_dir,
        feature_id,
        "design-approval-recorded",
        transition_id,
        event_id=event_id,
        approval_id=approval["approval_id"],
        blocker_key=event["blocker_key"],
    )
    return {"created": created, "approval": approval}


def _load_design_approval(
    run_dir: Path,
    feature_id: str,
    approval_id: str,
    event: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    approval = _read_json(_approval_path(run_dir, feature_id, approval_id))
    rebuilt = build_design_approval(approval, event)
    if rebuilt != approval or rebuilt["approval_id"] != approval_id:
        raise RepairControlError(
            "STATE_TAMPERED", "design approval digest is invalid"
        )
    _verify_human_approval_source(run_dir, approval)
    if approval["design_gate_digest"] != _design_gate_digest(gate):
        raise RepairControlError(
            "DESIGN_ADMISSION_DENIED",
            "design approval is not bound to the admitted design change",
        )
    return approval


def _record_diagnosis_unlocked(
    run_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    feature_id = _feature_id(payload.get("feature_id"))
    event_id = _bounded_string(payload.get("event_id"), "event_id", maximum=64)
    event = _load_event(run_dir, feature_id, event_id)
    diagnosis = build_diagnosis(payload, event)
    path = _diagnosis_path(run_dir, feature_id, diagnosis["diagnosis_id"])
    if path.exists() or path.is_symlink():
        existing = _read_json(path)
        if _identity_payload(existing, "diagnosis_id") != _identity_payload(
            diagnosis, "diagnosis_id"
        ):
            raise RepairControlError("STATE_TAMPERED", "diagnosis content changed")
        diagnosis = existing
        created = False
    else:
        created = _store_immutable(path, diagnosis, "diagnosis")
    control = _load_control(run_dir, feature_id)
    if control.get("current_event_id") != event_id:
        raise RepairControlError(
            "STATE_INVALID", "diagnosis event is not the current failure event"
        )
    transition_id = _digest(
        {"action": "record-diagnosis", "diagnosis_id": diagnosis["diagnosis_id"]}
    )
    if not created and control.get("current_diagnosis_id") == diagnosis["diagnosis_id"]:
        _append_journal(
            run_dir,
            feature_id,
            "diagnosis-recorded",
            transition_id,
            event_id=event_id,
            diagnosis_id=diagnosis["diagnosis_id"],
            blocker_key=diagnosis["blocker_key"],
        )
        _write_failure_log(run_dir, feature_id, control)
        return {"created": False, "diagnosis": diagnosis, "control": control}
    if control["active_blocker"]["blocker_key"] != diagnosis["blocker_key"]:
        raise RepairControlError("STATE_INVALID", "diagnosis is not for the active blocker")
    control["current_event_id"] = event_id
    control["current_diagnosis_id"] = diagnosis["diagnosis_id"]
    control["current_classification_id"] = None
    control["revalidation"] = None
    control["status"] = "diagnosed"
    control["updated_at"] = diagnosis["created_at"]
    _write_json_atomic(_control_path(run_dir, feature_id), control)
    _append_journal(
        run_dir,
        feature_id,
        "diagnosis-recorded",
        transition_id,
        event_id=event_id,
        diagnosis_id=diagnosis["diagnosis_id"],
        blocker_key=diagnosis["blocker_key"],
    )
    _write_failure_log(run_dir, feature_id, control)
    return {"created": created, "diagnosis": diagnosis, "control": control}


def _load_diagnosis(
    run_dir: Path, feature_id: str, diagnosis_id: str, event: dict[str, Any]
) -> dict[str, Any]:
    diagnosis = _read_json(_diagnosis_path(run_dir, feature_id, diagnosis_id))
    rebuilt = build_diagnosis(diagnosis, event)
    if rebuilt != diagnosis or rebuilt["diagnosis_id"] != diagnosis_id:
        raise RepairControlError("STATE_TAMPERED", "diagnosis digest is invalid")
    return diagnosis


def _implementation_route(event: dict[str, Any]) -> tuple[str, str]:
    lifecycle = event["execution_lifecycle"]
    if lifecycle == "active_task":
        return "sdlc-implement-plan", "existing_task_attempt"
    if lifecycle in {"sealed", "promoted", "completed"}:
        return "sdlc-create-plan", "new_corrective_run_from_promoted_commit"
    return "sdlc-create-plan", "corrective_plan_v_next"


def _classification_from(
    event: dict[str, Any], diagnosis: dict[str, Any] | None
) -> dict[str, Any]:
    if diagnosis is None:
        proposed = event.get("proposed_classification")
        if (
            event["cause_status"] in {"proven", "high_confidence"}
            and proposed in CLASSIFICATIONS
        ):
            classification = str(proposed)
            source = "proven_failure_event"
            confidence = event["cause_status"]
        else:
            classification = (
                "EVALUATION_DEFECT"
                if event["phase"] in {"evaluation", "sdlc-evaluate", "uat"}
                else "UNKNOWN_DEFECT"
            )
            return {
                "classification": classification,
                "next_recommended_skill": "troubleshoot",
                "status": "diagnosis_required",
                "reason": "responsible cause is not yet proven",
                "confidence": event["cause_status"],
                "source": "failure_event",
                "corrective_mode": None,
                "invalidates": [],
            }
    else:
        if diagnosis["result"] in {"blocked_missing_evidence", "unresolved"}:
            return {
                "classification": "UNKNOWN_DEFECT",
                "next_recommended_skill": None,
                "status": diagnosis["result"],
                "reason": "diagnosis did not establish a repair owner",
                "confidence": diagnosis["confidence"],
                "source": "diagnosis",
                "corrective_mode": None,
                "invalidates": [],
            }
        classification = RESULT_TO_CLASSIFICATION[diagnosis["result"]]
        source = "diagnosis"
        confidence = diagnosis["confidence"]
    if classification == "DESIGN_DEFECT":
        gate = (
            diagnosis.get("design_gate")
            if diagnosis is not None
            else _validate_design_gate(event.get("design_gate"))
        )
        if diagnosis is not None:
            gate = _validate_design_gate(gate)
    else:
        gate = None
    if classification == "IMPLEMENTATION_DEFECT":
        route, corrective_mode = _implementation_route(event)
        invalidates = [
            "validation",
            "tests",
            "evaluation",
            "documentation",
            "alignment",
            "commit",
        ]
    elif classification == "TEST_DEFECT":
        route, corrective_mode = "sdlc-tdd", "test_repair"
        invalidates = ["tests", "evaluation", "documentation", "alignment", "commit"]
    elif classification == "PLAN_DEFECT":
        route, corrective_mode = "sdlc-create-plan", "plan_v_next"
        invalidates = [
            "execution_preparation",
            "implementation",
            "validation",
            "tests",
            "evaluation",
            "documentation",
            "alignment",
            "commit",
        ]
    elif classification == "DESIGN_DEFECT":
        route, corrective_mode = "sdlc-create-design", "design_reconsideration"
        invalidates = [
            "plan",
            "execution_preparation",
            "implementation",
            "validation",
            "tests",
            "evaluation",
            "documentation",
            "alignment",
            "commit",
        ]
    elif classification == "SPEC_GAP":
        route, corrective_mode = "sdlc-create-requirements", "requirements_revision"
        invalidates = [
            "design",
            "plan",
            "execution_preparation",
            "implementation",
            "validation",
            "tests",
            "evaluation",
            "documentation",
            "alignment",
            "commit",
        ]
    elif classification == "EVALUATION_DEFECT":
        route, corrective_mode = "sdlc-evaluate", "evaluator_repair_same_commit"
        invalidates = ["evaluation"]
    elif classification == "ENVIRONMENT_DEFECT":
        route, corrective_mode, invalidates = None, "rerun_gate_same_commit", [
            event["phase"]
        ]
    else:
        route = DIRECT_ROUTES.get(classification)
        corrective_mode = None
        invalidates = []
    blocked = route is None
    return {
        "classification": classification,
        "next_recommended_skill": route,
        "status": "blocked" if blocked else "routed",
        "reason": f"{source} established {classification}",
        "confidence": confidence,
        "source": source,
        "corrective_mode": corrective_mode,
        "invalidates": invalidates,
        "design_admission": gate,
    }


def _classify_failure_unlocked(
    run_dir: Path,
    feature_id: str,
    event_id: str,
    diagnosis_id: str | None = None,
) -> dict[str, Any]:
    feature_id = _feature_id(feature_id)
    event = _load_event(run_dir, feature_id, event_id)
    diagnosis = (
        _load_diagnosis(run_dir, feature_id, diagnosis_id, event)
        if diagnosis_id
        else None
    )
    routed = _classification_from(event, diagnosis)
    design_admission = routed.get("design_admission")
    if (
        routed["classification"] == "DESIGN_DEFECT"
        and isinstance(design_admission, dict)
        and design_admission.get("approval_mode") == "human"
    ):
        approval_id = _bounded_string(
            design_admission.get("human_approval_id"),
            "human_approval_id",
            maximum=256,
        )
        _load_design_approval(
            run_dir, feature_id, approval_id, event, design_admission
        )
    record = {
        "schema": CLASSIFICATION_SCHEMA,
        "feature_id": feature_id,
        "event_id": event_id,
        "diagnosis_id": diagnosis_id,
        "blocker_key": event["blocker_key"],
        **routed,
    }
    record["classification_id"] = _digest(
        _identity_payload(record, "classification_id")
    )
    path = _classification_path(run_dir, feature_id, record["classification_id"])
    created = _store_immutable(path, record, "classification")
    control = _load_control(run_dir, feature_id)
    if (
        not created
        and control["route_history"]
        and control["route_history"][-1].get("classification_id")
        == record["classification_id"]
    ):
        return {"created": False, "classification": record, "control": control}
    if control.get("current_event_id") != event_id:
        raise RepairControlError(
            "STATE_INVALID", "classification event is not the current failure event"
        )
    if control["active_blocker"]["blocker_key"] != event["blocker_key"]:
        raise RepairControlError("STATE_INVALID", "classification is not for active blocker")
    history = control["route_history"]
    semantic_key = "|".join(
        (
            record["classification"],
            str(record["next_recommended_skill"] or "stop"),
            event["blocker_key"],
        )
    )
    evidence_key = diagnosis_id or event_id
    duplicate = (
        history[-1]
        if history
        and history[-1].get("classification_id") == record["classification_id"]
        else None
    )
    if duplicate is None and len(history) >= 2:
        prior = history[-2]
        if (
            prior.get("semantic_key") == semantic_key
            and prior.get("evidence_key") == evidence_key
        ):
            record = dict(record)
            record["next_recommended_skill"] = None
            record["status"] = "blocked_semantic_cycle"
            record["reason"] = "A-to-B-to-A routing repeated without semantic evidence progress"
            record["classification_id"] = _digest(
                _identity_payload(record, "classification_id")
            )
            path = _classification_path(
                run_dir, feature_id, record["classification_id"]
            )
            created = _store_immutable(path, record, "classification")
            semantic_key = "|".join(
                (record["classification"], "stop", event["blocker_key"])
            )
    if duplicate is None:
        history.append(
            {
                "classification_id": record["classification_id"],
                "event_id": event_id,
                "diagnosis_id": diagnosis_id,
                "classification": record["classification"],
                "next_recommended_skill": record["next_recommended_skill"],
                "semantic_key": semantic_key,
                "evidence_key": evidence_key,
                "status": record["status"],
            }
        )
    control["current_event_id"] = event_id
    control["current_diagnosis_id"] = diagnosis_id
    control["current_classification_id"] = record["classification_id"]
    control["revalidation"] = None
    control["status"] = record["status"]
    for item in record["invalidates"]:
        invalidation = {
            "event_id": event_id,
            "classification_id": record["classification_id"],
            "surface": item,
        }
        if invalidation not in control["invalidations"]:
            control["invalidations"].append(invalidation)
    _write_json_atomic(_control_path(run_dir, feature_id), control)
    transition_id = _digest(
        {"action": "classify", "classification_id": record["classification_id"]}
    )
    if duplicate is None:
        _append_journal(
            run_dir,
            feature_id,
            "classification-recorded",
            transition_id,
            classification_id=record["classification_id"],
            event_id=event_id,
            diagnosis_id=diagnosis_id,
            blocker_key=event["blocker_key"],
        )
    _write_failure_log(run_dir, feature_id, control)
    return {"created": created and duplicate is None, "classification": record, "control": control}


def _load_classification(
    run_dir: Path, feature_id: str, classification_id: str
) -> dict[str, Any]:
    record = _read_json(_classification_path(run_dir, feature_id, classification_id))
    expected = _digest(_identity_payload(record, "classification_id"))
    if record.get("schema") != CLASSIFICATION_SCHEMA or expected != classification_id:
        raise RepairControlError("STATE_TAMPERED", "classification digest is invalid")
    return record


def _budget_stop(blocker: dict[str, Any], trigger: str) -> None:
    blocker["status"] = "exhausted"
    blocker["stop_trigger"] = trigger


def _new_revalidation(
    classification: dict[str, Any], dispatch_id: str
) -> dict[str, Any] | None:
    required: list[dict[str, str]] = []
    for surface in classification.get("invalidates", []):
        route = REVALIDATION_ROUTES.get(surface)
        if route is None:
            raise RepairControlError(
                "STATE_INVALID",
                f"invalidated surface has no deterministic route: {surface}",
            )
        required.append(
            {
                "surface": surface,
                "next_recommended_skill": route,
            }
        )
    if not required:
        return None
    projection = {
        "schema": "agentic-sdlc/revalidation-cursor-v1",
        "classification_id": classification["classification_id"],
        "repair_dispatch_id": dispatch_id,
        "required": required,
    }
    return {
        **projection,
        "cursor_id": _digest(projection),
        "status": "pending",
        "cursor": 0,
        "completed_revalidation_ids": [],
        "integration_commit": None,
    }


def _begin_remediation_unlocked(
    run_dir: Path,
    feature_id: str,
    classification_id: str,
    remedy_scale: str,
    hypothesis: str,
    evidence_reference: str,
    active_seconds_delta: int = 0,
) -> dict[str, Any]:
    """Admit one bounded remediation dispatch and atomically count it."""

    feature_id = _feature_id(feature_id)
    classification = _load_classification(run_dir, feature_id, classification_id)
    if classification["status"] != "routed":
        raise RepairControlError(
            "REPAIR_NOT_AUTHORIZED", "classification does not authorize remediation"
        )
    if remedy_scale not in {"localized", "design"}:
        raise RepairControlError("REPAIR_NOT_AUTHORIZED", "remedy_scale is invalid")
    if remedy_scale == "design" and classification["classification"] != "DESIGN_DEFECT":
        raise RepairControlError(
            "DESIGN_ADMISSION_DENIED", "only DESIGN_DEFECT can dispatch design repair"
        )
    if remedy_scale == "localized" and classification["classification"] == "DESIGN_DEFECT":
        raise RepairControlError(
            "REPAIR_NOT_AUTHORIZED", "design defect cannot dispatch localized repair"
        )
    hypothesis = _bounded_string(hypothesis, "hypothesis")
    evidence_reference = _bounded_string(
        evidence_reference, "evidence_reference", maximum=1024
    )
    if active_seconds_delta < 0:
        raise RepairControlError("BUDGET_INVALID", "active_seconds_delta is negative")
    control = _load_control(run_dir, feature_id)
    blocker = control["active_blocker"]
    if blocker["blocker_key"] != classification["blocker_key"]:
        raise RepairControlError("STATE_INVALID", "classification blocker is not active")
    duplicate = next(
        (
            attempt
            for attempt in blocker["attempts"]
            if attempt["classification_id"] == classification_id
            and attempt["hypothesis"] == hypothesis
            and attempt["evidence_reference"] == evidence_reference
            and attempt["remedy_scale"] == remedy_scale
        ),
        None,
    )
    if duplicate is not None:
        transition_id = _digest(
            {"action": "dispatch", "dispatch_id": duplicate["dispatch_id"]}
        )
        _append_journal(
            run_dir,
            feature_id,
            "remediation-dispatch-committed",
            transition_id,
            dispatch_id=duplicate["dispatch_id"],
            blocker_key=blocker["blocker_key"],
        )
        return {"created": False, "attempt": duplicate, "control": control}
    blocker["active_seconds"] += active_seconds_delta
    if blocker["active_seconds"] >= blocker["time_limit_seconds"]:
        _budget_stop(blocker, "time_limit")
        control["status"] = "exhausted"
        _write_json_atomic(_control_path(run_dir, feature_id), control)
        raise RepairControlError("REPAIR_BUDGET_EXHAUSTED", "60-minute ceiling reached")
    if control["feature_dispatches"] >= control["feature_dispatch_limit"]:
        control["status"] = "blocked_feature_dispatch_limit"
        _write_json_atomic(_control_path(run_dir, feature_id), control)
        raise RepairControlError(
            "REPAIR_BUDGET_EXHAUSTED", "four-dispatch feature ceiling reached"
        )
    if blocker["total_attempts"] >= blocker["attempt_limit"]:
        _budget_stop(blocker, "attempt_limit")
        control["status"] = "exhausted"
        _write_json_atomic(_control_path(run_dir, feature_id), control)
        raise RepairControlError("REPAIR_BUDGET_EXHAUSTED", "three-attempt ceiling reached")
    if remedy_scale == "localized" and blocker["localized_attempts"] >= blocker["localized_limit"]:
        raise RepairControlError(
            "REPAIR_BUDGET_EXHAUSTED", "two-localized-remediation ceiling reached"
        )
    if remedy_scale == "design" and (
        blocker["design_attempts"] >= blocker["design_limit"]
        or blocker["design_remediated"]
    ):
        raise RepairControlError(
            "REPAIR_BUDGET_EXHAUSTED", "design remediation cannot repeat for this blocker"
        )
    failed = [item for item in blocker["attempts"] if item.get("result") == "failed_same_blocker"]
    if failed:
        current_diagnosis = classification.get("diagnosis_id")
        if not current_diagnosis or current_diagnosis == failed[-1].get("diagnosis_id"):
            raise RepairControlError(
                "TROUBLESHOOT_REQUIRED",
                "a failed repair requires a new diagnosis before another remediation",
            )
        if any(item["hypothesis"] == hypothesis for item in blocker["attempts"]):
            raise RepairControlError(
                "RETRY_EVIDENCE_REQUIRED", "retry hypothesis is not genuinely new"
            )
        if any(
            item["evidence_reference"] == evidence_reference
            for item in blocker["attempts"]
        ):
            raise RepairControlError(
                "RETRY_EVIDENCE_REQUIRED", "retry evidence is not newly acquired"
            )
    ordinal = blocker["total_attempts"] + 1
    dispatch_id = _digest(
        {
            "blocker_key": blocker["blocker_key"],
            "ordinal": ordinal,
            "classification_id": classification_id,
            "remedy_scale": remedy_scale,
            "hypothesis": hypothesis,
            "evidence_reference": evidence_reference,
        }
    )
    attempt = {
        "dispatch_id": dispatch_id,
        "ordinal": ordinal,
        "blocker_key": blocker["blocker_key"],
        "classification_id": classification_id,
        "event_id": classification["event_id"],
        "diagnosis_id": classification.get("diagnosis_id"),
        "remedy_scale": remedy_scale,
        "hypothesis": hypothesis,
        "evidence_reference": evidence_reference,
        "status": "dispatched",
        "result": None,
        "verification_reference": None,
    }
    transition_id = _digest({"action": "dispatch", "dispatch_id": dispatch_id})
    _append_journal(
        run_dir,
        feature_id,
        "remediation-dispatch-intent",
        transition_id,
        dispatch_id=dispatch_id,
        blocker_key=blocker["blocker_key"],
    )
    blocker["attempts"].append(attempt)
    blocker["total_attempts"] += 1
    blocker[f"{remedy_scale}_attempts"] += 1
    blocker["status"] = "remediating"
    control["feature_dispatches"] += 1
    control["status"] = "remediating"
    _write_json_atomic(_control_path(run_dir, feature_id), control)
    _append_journal(
        run_dir,
        feature_id,
        "remediation-dispatch-committed",
        transition_id,
        dispatch_id=dispatch_id,
        blocker_key=blocker["blocker_key"],
    )
    _write_failure_log(run_dir, feature_id, control)
    return {"created": True, "attempt": attempt, "control": control}


def _complete_remediation_unlocked(
    run_dir: Path,
    feature_id: str,
    dispatch_id: str,
    result: str,
    verification_reference: str,
    active_seconds_delta: int = 0,
) -> dict[str, Any]:
    if result not in {"failed_same_blocker", "succeeded"}:
        raise RepairControlError("RECORD_INVALID", "remediation result is invalid")
    verification_reference = _bounded_string(
        verification_reference, "verification_reference", maximum=1024
    )
    if active_seconds_delta < 0:
        raise RepairControlError("BUDGET_INVALID", "active_seconds_delta is negative")
    control = _load_control(run_dir, _feature_id(feature_id))
    blocker = control["active_blocker"]
    attempt = next(
        (item for item in blocker["attempts"] if item["dispatch_id"] == dispatch_id),
        None,
    )
    if attempt is None:
        raise RepairControlError("STATE_INVALID", "remediation dispatch is unknown")
    if attempt["result"] is not None:
        if (
            attempt["result"] == result
            and attempt["verification_reference"] == verification_reference
        ):
            transition_id = _digest(
                {
                    "action": "complete",
                    "dispatch_id": dispatch_id,
                    "result": result,
                }
            )
            _append_journal(
                run_dir,
                feature_id,
                "remediation-completion-committed",
                transition_id,
                dispatch_id=dispatch_id,
                blocker_key=blocker["blocker_key"],
                result=result,
            )
            return {"updated": False, "attempt": attempt, "control": control}
        raise RepairControlError("STATE_TAMPERED", "completed remediation changed")
    transition_id = _digest({"action": "complete", "dispatch_id": dispatch_id, "result": result})
    _append_journal(
        run_dir,
        feature_id,
        "remediation-completion-intent",
        transition_id,
        dispatch_id=dispatch_id,
        blocker_key=blocker["blocker_key"],
    )
    blocker["active_seconds"] += active_seconds_delta
    attempt["status"] = "completed"
    attempt["result"] = result
    attempt["verification_reference"] = verification_reference
    if attempt["remedy_scale"] == "design":
        blocker["design_remediated"] = True
    if result == "succeeded":
        blocker["stop_trigger"] = None
        classification = _load_classification(
            run_dir, _feature_id(feature_id), attempt["classification_id"]
        )
        revalidation = _new_revalidation(classification, dispatch_id)
        control["revalidation"] = revalidation
        if revalidation is None:
            blocker["status"] = "resolved"
            control["status"] = "resolved"
        else:
            blocker["status"] = "revalidation_required"
            control["status"] = "revalidation_required"
    elif attempt["remedy_scale"] == "design":
        blocker["status"] = "blocked_after_design_remediation"
        blocker["stop_trigger"] = "design_remediation_failed"
        control["status"] = "blocked_after_design_remediation"
    elif blocker["active_seconds"] >= blocker["time_limit_seconds"]:
        _budget_stop(blocker, "time_limit")
        control["status"] = "exhausted"
    elif blocker["total_attempts"] >= blocker["attempt_limit"]:
        _budget_stop(blocker, "attempt_limit")
        control["status"] = "exhausted"
    else:
        blocker["status"] = "diagnosis_required"
        control["status"] = "diagnosis_required"
    _write_json_atomic(_control_path(run_dir, feature_id), control)
    _append_journal(
        run_dir,
        feature_id,
        "remediation-completion-committed",
        transition_id,
        dispatch_id=dispatch_id,
        blocker_key=blocker["blocker_key"],
        result=result,
    )
    _write_failure_log(run_dir, feature_id, control)
    return {"updated": True, "attempt": attempt, "control": control}


def _build_revalidation_evidence(
    payload: dict[str, Any], control: dict[str, Any]
) -> dict[str, Any]:
    if payload.get("schema") != REVALIDATION_EVIDENCE_SCHEMA:
        raise RepairControlError(
            "REVALIDATION_INVALID", "revalidation evidence schema is invalid"
        )
    feature_id = _feature_id(payload.get("feature_id"))
    if feature_id != control["feature_id"]:
        raise RepairControlError(
            "REVALIDATION_INVALID", "revalidation feature changed"
        )
    revalidation = control.get("revalidation")
    if not isinstance(revalidation, dict):
        raise RepairControlError(
            "REVALIDATION_INVALID", "repair has no active revalidation cursor"
        )
    surface = _bounded_string(payload.get("surface"), "surface", maximum=64)
    required = revalidation.get("required")
    matching = (
        next(
            (
                item
                for item in required
                if isinstance(item, dict) and item.get("surface") == surface
            ),
            None,
        )
        if isinstance(required, list)
        else None
    )
    if matching is None:
        raise RepairControlError(
            "REVALIDATION_INVALID", "surface is not invalidated by this repair"
        )
    integration_commit = _bounded_string(
        payload.get("integration_commit"),
        "integration_commit",
        maximum=64,
    ).lower()
    if COMMIT_RE.fullmatch(integration_commit) is None:
        raise RepairControlError(
            "REVALIDATION_INVALID", "integration_commit is not a full Git object ID"
        )
    evidence_digest = _bounded_string(
        payload.get("evidence_digest"), "evidence_digest", maximum=80
    )
    if DIGEST_RE.fullmatch(evidence_digest) is None:
        raise RepairControlError(
            "REVALIDATION_INVALID", "evidence_digest must be SHA-256"
        )
    record = {
        "schema": REVALIDATION_EVIDENCE_SCHEMA,
        "feature_id": feature_id,
        "event_id": control["current_event_id"],
        "classification_id": revalidation["classification_id"],
        "repair_dispatch_id": revalidation["repair_dispatch_id"],
        "cursor_id": revalidation["cursor_id"],
        "blocker_key": control["active_blocker"]["blocker_key"],
        "surface": surface,
        "next_recommended_skill": matching["next_recommended_skill"],
        "integration_commit": integration_commit,
        "fingerprints": _validate_digests(
            payload.get("fingerprints"), "fingerprints"
        ),
        "evidence_reference": _bounded_string(
            payload.get("evidence_reference"),
            "evidence_reference",
            maximum=1024,
        ),
        "evidence_digest": evidence_digest,
        "recorded_at": _now(payload.get("recorded_at")),
    }
    _reject_sensitive(record)
    record["revalidation_id"] = _digest(
        _identity_payload(record, "revalidation_id")
    )
    return record


def _record_revalidation_unlocked(
    run_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    feature_id = _feature_id(payload.get("feature_id"))
    control = _load_control(run_dir, feature_id)
    record = _build_revalidation_evidence(payload, control)
    _verify_revalidation_evidence_source(run_dir, record)
    revalidation = control["revalidation"]
    revalidation_id = record["revalidation_id"]
    if revalidation_id in revalidation["completed_revalidation_ids"]:
        _verify_revalidation_authority(
            run_dir, control, record, require_current_gate=False
        )
        _store_immutable(
            _revalidation_path(run_dir, feature_id, revalidation_id),
            record,
            "revalidation evidence",
        )
        transition_id = _digest(
            {"action": "record-revalidation", "revalidation_id": revalidation_id}
        )
        _append_journal(
            run_dir,
            feature_id,
            "revalidation-recorded",
            transition_id,
            revalidation_id=revalidation_id,
            surface=record["surface"],
            blocker_key=record["blocker_key"],
        )
        return {"created": False, "revalidation": record, "control": control}
    _verify_revalidation_authority(run_dir, control, record)
    cursor = revalidation.get("cursor")
    required = revalidation.get("required")
    if (
        revalidation.get("status") != "pending"
        or not isinstance(cursor, int)
        or not isinstance(required, list)
        or cursor < 0
        or cursor >= len(required)
        or required[cursor].get("surface") != record["surface"]
    ):
        raise RepairControlError(
            "REVALIDATION_OUT_OF_ORDER",
            "revalidation must advance the authoritative invalidation cursor",
        )
    bound_commit = revalidation.get("integration_commit")
    if record["surface"] != "commit":
        if bound_commit is None:
            revalidation["integration_commit"] = record["integration_commit"]
        elif bound_commit != record["integration_commit"]:
            raise RepairControlError(
                "REVALIDATION_INVALID",
                "pre-commit revalidation evidence spans multiple integration commits",
            )
    created = _store_immutable(
        _revalidation_path(run_dir, feature_id, revalidation_id),
        record,
        "revalidation evidence",
    )
    revalidation["completed_revalidation_ids"].append(revalidation_id)
    revalidation["cursor"] += 1
    if revalidation["cursor"] == len(required):
        revalidation["status"] = "complete"
        control["status"] = "resolved"
        control["active_blocker"]["status"] = "resolved"
    else:
        control["status"] = "revalidation_required"
        control["active_blocker"]["status"] = "revalidation_required"
    _write_json_atomic(_control_path(run_dir, feature_id), control)
    transition_id = _digest(
        {"action": "record-revalidation", "revalidation_id": revalidation_id}
    )
    _append_journal(
        run_dir,
        feature_id,
        "revalidation-recorded",
        transition_id,
        revalidation_id=revalidation_id,
        surface=record["surface"],
        blocker_key=record["blocker_key"],
    )
    _write_failure_log(run_dir, feature_id, control)
    return {"created": created, "revalidation": record, "control": control}


def _record_experiment_unlocked(
    run_dir: Path,
    feature_id: str,
    question: str,
    hypothesis: str,
    evidence_reference: str,
    information_gain: str,
    *,
    model_rebuilt: bool = False,
    active_seconds_delta: int = 0,
) -> dict[str, Any]:
    if information_gain not in {"decision_changing", "model_update", "low"}:
        raise RepairControlError("RECORD_INVALID", "information_gain is invalid")
    control = _load_control(run_dir, _feature_id(feature_id))
    blocker = control["active_blocker"]
    if blocker["model_rebuild_required"] and not model_rebuilt:
        raise RepairControlError(
            "MODEL_REBUILD_REQUIRED",
            "three low-information experiments require a rebuilt model",
        )
    experiment = {
        "question": _bounded_string(question, "question"),
        "hypothesis": _bounded_string(hypothesis, "hypothesis"),
        "evidence_reference": _bounded_string(
            evidence_reference, "evidence_reference"
        ),
        "information_gain": information_gain,
        "model_rebuilt": bool(model_rebuilt),
    }
    experiment["experiment_id"] = _digest(experiment)
    duplicate = next(
        (
            item
            for item in blocker["experiments"]
            if item["experiment_id"] == experiment["experiment_id"]
        ),
        None,
    )
    if duplicate is not None:
        return {"created": False, "experiment": duplicate, "control": control}
    blocker["active_seconds"] += active_seconds_delta
    if blocker["active_seconds"] >= blocker["time_limit_seconds"]:
        _budget_stop(blocker, "time_limit")
        control["status"] = "exhausted"
        _write_json_atomic(_control_path(run_dir, feature_id), control)
        raise RepairControlError("REPAIR_BUDGET_EXHAUSTED", "60-minute ceiling reached")
    blocker["experiments"].append(experiment)
    if information_gain == "low":
        blocker["low_information_experiments"] += 1
        if blocker["low_information_experiments"] >= 3:
            blocker["model_rebuild_required"] = True
    elif model_rebuilt:
        blocker["model_rebuild_required"] = False
        blocker["low_information_experiments"] = 0
    _write_json_atomic(_control_path(run_dir, feature_id), control)
    transition_id = _digest(
        {"action": "experiment", "experiment_id": experiment["experiment_id"]}
    )
    _append_journal(
        run_dir,
        feature_id,
        "diagnostic-experiment-recorded",
        transition_id,
        experiment_id=experiment["experiment_id"],
        blocker_key=blocker["blocker_key"],
    )
    return {"created": True, "experiment": experiment, "control": control}


def record_failure_event(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    feature_id = _feature_id(payload.get("feature_id"))
    with _transition_lock(run_dir, feature_id):
        return _record_failure_event_unlocked(run_dir, payload)


def record_design_approval(
    run_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    feature_id = _feature_id(payload.get("feature_id"))
    with _transition_lock(run_dir, feature_id):
        return _record_design_approval_unlocked(run_dir, payload)


def record_diagnosis(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    feature_id = _feature_id(payload.get("feature_id"))
    with _transition_lock(run_dir, feature_id):
        return _record_diagnosis_unlocked(run_dir, payload)


def classify_failure(
    run_dir: Path,
    feature_id: str,
    event_id: str,
    diagnosis_id: str | None = None,
) -> dict[str, Any]:
    with _transition_lock(run_dir, feature_id):
        return _classify_failure_unlocked(
            run_dir, feature_id, event_id, diagnosis_id
        )


def begin_remediation(
    run_dir: Path,
    feature_id: str,
    classification_id: str,
    remedy_scale: str,
    hypothesis: str,
    evidence_reference: str,
    active_seconds_delta: int = 0,
) -> dict[str, Any]:
    with _transition_lock(run_dir, feature_id):
        return _begin_remediation_unlocked(
            run_dir,
            feature_id,
            classification_id,
            remedy_scale,
            hypothesis,
            evidence_reference,
            active_seconds_delta,
        )


def complete_remediation(
    run_dir: Path,
    feature_id: str,
    dispatch_id: str,
    result: str,
    verification_reference: str,
    active_seconds_delta: int = 0,
) -> dict[str, Any]:
    with _transition_lock(run_dir, feature_id):
        return _complete_remediation_unlocked(
            run_dir,
            feature_id,
            dispatch_id,
            result,
            verification_reference,
            active_seconds_delta,
        )


def record_experiment(
    run_dir: Path,
    feature_id: str,
    question: str,
    hypothesis: str,
    evidence_reference: str,
    information_gain: str,
    *,
    model_rebuilt: bool = False,
    active_seconds_delta: int = 0,
) -> dict[str, Any]:
    with _transition_lock(run_dir, feature_id):
        return _record_experiment_unlocked(
            run_dir,
            feature_id,
            question,
            hypothesis,
            evidence_reference,
            information_gain,
            model_rebuilt=model_rebuilt,
            active_seconds_delta=active_seconds_delta,
        )


def record_revalidation(
    run_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    feature_id = _feature_id(payload.get("feature_id"))
    with _transition_lock(run_dir, feature_id):
        return _record_revalidation_unlocked(run_dir, payload)


def _write_failure_log(
    run_dir: Path, feature_id: str, control: dict[str, Any]
) -> None:
    path = run_dir.resolve() / "evidence" / feature_id / "failure-log.md"
    blocker = control["active_blocker"]
    lines = [
        f"# {feature_id} Failure Log",
        "",
        "<!-- Derived from failure-event-v1, diagnosis-v1, and repair-control-v1. -->",
        "",
        f"- Active blocker: `{blocker['blocker_key']}`",
        f"- Status: `{control['status']}`",
        f"- Current event: `{control['current_event_id']}`",
        f"- Current diagnosis: `{control['current_diagnosis_id'] or 'none'}`",
        f"- Feature repair dispatches: `{control['feature_dispatches']}` / `{control['feature_dispatch_limit']}`",
        f"- Blocker attempts: `{blocker['total_attempts']}` / `{blocker['attempt_limit']}`",
        f"- Active time: `{blocker['active_seconds']}` / `{blocker['time_limit_seconds']}` seconds",
        "",
    ]
    revalidation = control.get("revalidation")
    if isinstance(revalidation, dict):
        cursor = int(revalidation.get("cursor", 0) or 0)
        required = revalidation.get("required", [])
        next_surface = (
            required[cursor].get("surface")
            if isinstance(required, list) and cursor < len(required)
            else "complete"
        )
        lines.extend(
            [
                "## Revalidation",
                "",
                f"- Status: `{revalidation.get('status')}`",
                f"- Cursor: `{cursor}` / `{len(required)}`",
                f"- Next invalidated surface: `{next_surface}`",
                "",
            ]
        )
    lines.extend(["## Routes", ""])
    if control["route_history"]:
        for route in control["route_history"]:
            lines.append(
                "- "
                f"`{route['classification']}` -> "
                f"`{route['next_recommended_skill'] or 'stop'}` "
                f"({route['status']})"
            )
    else:
        lines.append("- None recorded.")
    lines.extend(["", "## Remediation Attempts", ""])
    if blocker["attempts"]:
        for attempt in blocker["attempts"]:
            lines.append(
                "- "
                f"attempt-{attempt['ordinal']}: `{attempt['remedy_scale']}` "
                f"`{attempt['status']}` / `{attempt['result'] or 'pending'}`"
            )
    else:
        lines.append("- None recorded.")
    lines.extend(["", "## Invalidated Evidence", ""])
    if control["invalidations"]:
        for invalidation in control["invalidations"]:
            lines.append(f"- `{invalidation['surface']}`")
    else:
        lines.append("- None.")
    lines.append("")
    _reject_sensitive(lines)
    _reject_unsafe_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_input(path: Path) -> dict[str, Any]:
    candidate = path.expanduser().absolute()
    if candidate.is_symlink():
        raise RepairControlError("STATE_INVALID", "input record is symlinked")
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepairControlError("STATE_INVALID", "input record is unreadable") from exc
    if not isinstance(value, dict):
        raise RepairControlError("STATE_INVALID", "input record is not an object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in (
        "record-failure",
        "record-diagnosis",
        "record-approval",
        "record-revalidation",
    ):
        command = subparsers.add_parser(name)
        command.add_argument("--run-dir", type=Path, required=True)
        command.add_argument("--input", type=Path, required=True)
    classify = subparsers.add_parser("classify")
    classify.add_argument("--run-dir", type=Path, required=True)
    classify.add_argument("--feature", required=True)
    classify.add_argument("--event", required=True)
    classify.add_argument("--diagnosis")
    dispatch = subparsers.add_parser("dispatch")
    dispatch.add_argument("--run-dir", type=Path, required=True)
    dispatch.add_argument("--feature", required=True)
    dispatch.add_argument("--classification", required=True)
    dispatch.add_argument("--remedy-scale", choices=("localized", "design"), required=True)
    dispatch.add_argument("--hypothesis", required=True)
    dispatch.add_argument("--evidence-reference", required=True)
    dispatch.add_argument("--active-seconds", type=int, default=0)
    complete = subparsers.add_parser("complete")
    complete.add_argument("--run-dir", type=Path, required=True)
    complete.add_argument("--feature", required=True)
    complete.add_argument("--dispatch", required=True)
    complete.add_argument(
        "--result", choices=("failed_same_blocker", "succeeded"), required=True
    )
    complete.add_argument("--verification-reference", required=True)
    complete.add_argument("--active-seconds", type=int, default=0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "record-failure":
            result = record_failure_event(args.run_dir, _load_input(args.input))
        elif args.command == "record-diagnosis":
            result = record_diagnosis(args.run_dir, _load_input(args.input))
        elif args.command == "record-approval":
            result = record_design_approval(args.run_dir, _load_input(args.input))
        elif args.command == "record-revalidation":
            result = record_revalidation(args.run_dir, _load_input(args.input))
        elif args.command == "classify":
            result = classify_failure(
                args.run_dir, args.feature, args.event, args.diagnosis
            )
        elif args.command == "dispatch":
            result = begin_remediation(
                args.run_dir,
                args.feature,
                args.classification,
                args.remedy_scale,
                args.hypothesis,
                args.evidence_reference,
                args.active_seconds,
            )
        else:
            result = complete_remediation(
                args.run_dir,
                args.feature,
                args.dispatch,
                args.result,
                args.verification_reference,
                args.active_seconds,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except RepairControlError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
