"""Coordinator-owned objective impact, using the canonical spec interpreter."""

from pathlib import Path
import hashlib

from workflow_objectives import validate_run
from workflow_state import WorkflowError, read, write
from workflow_impact import publish, spec_checkout, CLAIM_SCHEMA


def refinement(run_dir: Path) -> dict:
    _run, binding = validate_run(run_dir)
    latest = binding["revisions"][-1]
    accepted = read(run_dir / latest["snapshot"])
    questions_path = run_dir / "clarifications.json"
    questions = read(questions_path).get("questions") if questions_path.exists() else []
    if not isinstance(questions, list):
        raise WorkflowError("CLARIFICATION_REQUIRED", "clarification ledger is invalid")
    seen = set()
    for question in questions:
        if (not isinstance(question, dict) or not question.get("id") or question["id"] in seen
                or question.get("status") not in {"open", "answered", "reopened"}
                or type(question.get("material")) is not bool):
            raise WorkflowError("CLARIFICATION_REQUIRED", "stable clarification identity is invalid")
        seen.add(question["id"])
        if question["material"] and question["status"] != "answered":
            raise WorkflowError("CLARIFICATION_REQUIRED", "material objective questions remain open")
    extracted = {key: ([accepted[key]] if key == "outcome" else accepted[key])
                 for key in ("outcome", "scope", "constraints", "acceptance_criteria", "clarifications")}
    return {"schema": "workflow/requirements-refinement-v1", "objective_id": binding["objective_id"],
            "revision": latest["revision"], "intent_sha256": latest["intent_sha256"],
            "status": "ready", "extracted": extracted, "questions": questions}


def ready(run_dir: Path) -> dict:
    run, _binding = validate_run(run_dir)
    value = refinement(run_dir)
    project = spec_checkout(run_dir, Path(run["project_root"]))
    requirements = project / "docs/requirements.md"
    if not requirements.exists() or requirements.is_symlink():
        raise WorkflowError("SPEC_REQUIRED", "SDLC requirements admission needs canonical requirements")
    from project_specs_lib.contracts import inspect_document_bytes
    observed = inspect_document_bytes("requirements", requirements.read_bytes())
    if not observed["managed"]:
        raise WorkflowError("SPEC_REQUIRED", "requirements must be published by their canonical owner")
    value["compiled_requirements_sha256"] = hashlib.sha256(requirements.read_bytes()).hexdigest()
    return value


def settle(run_dir: Path, claim: dict):
    run, binding = validate_run(run_dir)
    value = refinement(run_dir)
    if claim.get("schema") != CLAIM_SCHEMA:
        raise WorkflowError("OBJECTIVE_IMPACT_INVALID", "objective impact claim schema is unsupported")
    write(run_dir / "objective-impact-claim.json", claim)
    receipt, fingerprint = publish(Path(run["project_root"]), run_dir, binding, value)
    write(run_dir / "requirements-refinement.json", value)
    return {"status": "SETTLED", "receipt_sha256": fingerprint, "revision": receipt["revision"],
            "effects": receipt["effects"], "plan_action": receipt["plan_action"]}
