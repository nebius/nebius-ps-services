"""Pure owner-specific delivery and Implement phase observations."""
from workflow_state import WorkflowError, digest


def _target_matches(candidate, target):
    return (isinstance(target, dict) and candidate.get("candidate_head") == target.get("head")
            and all(candidate.get(k) == target.get(k) for k in ("source_head", "child_head")))


def delivery(run, values, executions):
    """Only owner receipts describe aggregation; integrated tasks cannot supply it."""
    result = {"promotion": "recorded" if executions and all(r["promoted"] for r in executions) else "pending",
              "aggregation": "pending" if run.get("automatic_outer") else "not_applicable",
              "validation": "unavailable", "cleanup": "pending"}
    candidate = values.get("aggregation/candidate.json")
    correction = run.get("corrective_execution")
    if correction:
        intent = values.get(f"aggregation/corrections/{correction}.json", {})
        if (intent.get("schema") != "workflow/aggregation-correction-v1" or intent.get("run_id") != run["run_id"]
                or intent.get("execution_id") != correction or intent.get("status") != "ready"):
            raise WorkflowError("STATISTICS_UNAVAILABLE", "correction publication is incomplete")
        coordinator = values.get(f"execution/{correction}/coordinator.json", {})
        if candidate and (_target_matches(candidate, intent.get("target"))
                          or coordinator.get("status") != "done"):
            candidate = None
            result["aggregation"] = "correction"
        elif candidate and candidate.get("child_head") != coordinator.get("promoted_head"):
            raise WorkflowError("STATISTICS_UNAVAILABLE", "candidate does not follow the completed correction")
    receipts = [r for p, r in values.items() if p.startswith("aggregation/operations/")]
    receipt = values.get("aggregation/delivery.json")
    if receipt:
        if receipt.get("schema") != "workflow/automatic-delivery-v1" or receipt.get("run_id") != run["run_id"]:
            raise WorkflowError("STATISTICS_UNAVAILABLE", "delivery receipt identity differs")
        validation = next((r for r in receipts if digest(r) == receipt.get("validation_sha256")), None)
        if (not validation or validation.get("status") != "verified"
                or validation.get("outcome") != "passed" or validation.get("target") != receipt.get("target")):
            raise WorkflowError("STATISTICS_UNAVAILABLE", "delivery validation publication is incomplete")
        result.update(aggregation="delivered", validation="passed",
                      cleanup="recorded" if receipt.get("cleanup") else "pending")
    elif candidate:
        result["aggregation"] = "candidate"
        result["validation"] = "pending"
        matching = [r for r in receipts if _target_matches(candidate, r.get("target"))]
        if len(matching) > 1:
            raise WorkflowError("STATISTICS_UNAVAILABLE", "candidate validation is ambiguous")
        if matching:
            validation = matching[0]
            status = validation.get("status")
            if status == "verified" and validation.get("outcome") in {"passed", "failed"}:
                result["validation"] = validation["outcome"]
            elif status in {"running", "interrupted"}:
                result["validation"] = status
            else:
                raise WorkflowError("STATISTICS_UNAVAILABLE", "candidate validation status is unsupported")
    if not run.get("automatic_outer") and executions and all(r["cleanup"] == "complete" for r in executions):
        result["cleanup"] = "recorded"
    return result


def implement_phase(values, executions, delivered, planned):
    if delivered["aggregation"] == "delivered":
        return "complete" if delivered["cleanup"] == "recorded" else "delivery-cleanup"
    correction = values["run.json"].get("corrective_execution")
    if correction:
        execution = next((r for r in executions if r["id"] == correction), None)
        if execution is None:
            return "recovery-planning"
    else:
        execution = executions[0] if len(executions) == 1 else None
    if delivered["aggregation"] == "candidate":
        return "aggregation-validation"
    if not executions:
        return "planning" if planned else "accepted"
    # A correction owns a fresh execution; completed predecessors remain history.
    if execution is None:
        raise WorkflowError("STATISTICS_UNAVAILABLE", "Implement execution identity is ambiguous")
    phases = {"preparing": "preparation", "specs_prepared": "specification",
              "specs_sealing": "preparation", "prepared": "preparation",
              "tdd_sealed": "execution", "waves_running": "execution",
              "repair_required": "recovery", "blocked": "blocked",
              "integrated": "validation", "sealed": "promotion",
              "promoted": "delivery-cleanup", "cleanup": "delivery-cleanup",
              "done": "aggregation" if values["run.json"].get("automatic_outer") else "complete"}
    if execution["status"] not in phases:
        raise WorkflowError("STATISTICS_UNAVAILABLE", "Implement execution status is unsupported")
    wave = next((r for r in execution["waves"] if r["wave_id"] == execution["active_wave"]), {})
    if wave.get("status") in {"integrating", "integrated"}:
        return "integration" if wave["status"] == "integrating" else "wave-validation"
    return phases[execution["status"]]
