#!/usr/bin/env python3
"""Emit normalized Agentic SDLC evaluation failure events."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


INPUT_SCHEMA = "agentic-sdlc/evaluation-failure-input-v1"
PROVEN_CAUSES = {
    "IMPLEMENTATION_DEFECT",
    "TEST_DEFECT",
    "SPEC_GAP",
    "EVALUATION_DEFECT",
    "ENVIRONMENT_DEFECT",
    "DESIGN_DEFECT",
    "POLICY_BLOCK",
    "HUMAN_INPUT_REQUIRED",
}


def _load_repair_control():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "sdlc-classify-failure"
        / "scripts"
        / "repair_control.py"
    )
    specification = importlib.util.spec_from_file_location(
        "agentic_sdlc_repair_control", module_path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("sdlc-classify-failure repair control is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def normalize_evaluation_failure(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the authoritative failure event plus its routing disposition."""

    if payload.get("schema") != INPUT_SCHEMA:
        raise ValueError("evaluation failure input schema is invalid")
    cause_status = str(payload.get("cause_status") or "")
    proposed = payload.get("proven_cause")
    if cause_status in {"proven", "high_confidence"}:
        if proposed not in PROVEN_CAUSES:
            raise ValueError("a proven evaluation cause needs a supported owner")
        disposition = "proven_owner"
    else:
        proposed = None
        disposition = "diagnosis_required"
    repair_control = _load_repair_control()
    event_payload = {
        "schema": repair_control.FAILURE_EVENT_SCHEMA,
        "feature_id": payload.get("feature_id"),
        "phase": "evaluation",
        "criterion_id": payload.get("criterion_id"),
        "expected": payload.get("expected"),
        "observed": payload.get("observed"),
        "evidence_digests": payload.get("evidence_digests"),
        "reproduction": payload.get("reproduction"),
        "integration_commit": payload.get("integration_commit"),
        "fingerprints": payload.get("fingerprints"),
        "component": payload.get("component"),
        "operation": payload.get("operation"),
        "error_class": payload.get("error_class"),
        "source_boundary": payload.get("source_boundary"),
        "cause_status": cause_status,
        "proposed_classification": proposed,
        "execution_lifecycle": payload.get("execution_lifecycle", "waves_completed"),
        "independent_blocker_evidence": payload.get("independent_blocker_evidence"),
        "design_gate": payload.get("design_gate"),
        "created_at": payload.get("created_at"),
    }
    event = repair_control.build_failure_event(event_payload)
    return {
        "schema": "agentic-sdlc/evaluation-failure-disposition-v1",
        "disposition": disposition,
        "failure_event": event,
    }


def emit_evaluation_failure(
    run_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    normalized = normalize_evaluation_failure(payload)
    repair_control = _load_repair_control()
    recorded = repair_control.record_failure_event(
        run_dir, normalized["failure_event"]
    )
    return {
        **normalized,
        "created": recorded["created"],
        "repair_control": recorded["control"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = emit_evaluation_failure(args.run_dir, payload)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
