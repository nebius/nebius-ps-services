"""Read-only, evidence-backed messages for automatic local delivery."""
from pathlib import Path
import json

from workflow_state import read, WorkflowError


def continuation(owner: str, project: str) -> str:
    command = {"implement": "implement", "agentic-sdlc": "sdlc-start"}[owner]
    return f"${command} continue {json.dumps(project, ensure_ascii=False)}"


def guidance(run_dir: Path, reasons=()) -> dict | None:
    from workflow_objectives import validate_run
    import worktree_manager as manager
    run, _ = validate_run(run_dir)
    if not run.get("automatic_outer"):
        return None
    row = read(Path(run["git_common_dir"]) / "workflow-projects" / (run_dir.name + ".json"))
    if (row["run_dir"] != str(run_dir) or row["owner"] != run["owner"]
            or row["git_common_dir"] != run["git_common_dir"]):
        raise WorkflowError("OWNERSHIP_INVALID", "delivery guidance ownership differs")
    if row["state"] not in {"aggregation-ready", "delivered", "cleaned"} or not row.get("outer"):
        return None
    outer = row["outer"]
    primary = Path(row["source_root"])
    source_branch = outer["source_branch"]
    stage = "recovery_pending"
    observed = list(reasons)
    try:
        from workflow_automatic import verify_delivery, _verify_validation
        from workflow_aggregation_gates import load as load_gates
        if row["state"] == "cleaned":
            verify_delivery(run_dir)
            stage = "complete"
        else:
            tip = manager._git(primary, "rev-parse", "refs/heads/" + source_branch)
            promoted = False
            for path in (run_dir / "aggregation/operations").glob("*.json"):
                receipt = read(path)
                target = receipt.get("target", {})
                if (receipt.get("status") != "verified" or receipt.get("outcome") != "passed"
                        or not target.get("head") or not manager._is_ancestor(primary, target["head"], tip)):
                    continue
                _verify_validation(run_dir, receipt, historical=True)
                gates = load_gates(run_dir, target, historical=True)
                if (gates["outcome"] != "passed"
                        or manager._merge_parents(primary, target["head"]) != (target["source_head"], target["child_head"])
                        or manager._git(primary, "rev-parse", target["head"] + "^{tree}") != target["tree"]):
                    raise WorkflowError("RESULT_INVALID", "delivery proof needs reconciliation")
                promoted = True
                break
            if promoted:
                stage = "finalization_pending"
            else:
                from worktree_state import load_manifest
                manifest = load_manifest(primary, outer["name"])
                child = manager._git(Path(outer["worktree"]), "rev-parse", "HEAD")
                # An integrated manifest or reachable child without full proof
                # may be a crash/manual effect. Never label it unpromoted.
                if manifest.status == "active" and not manager._is_ancestor(primary, child, tip):
                    stage = "promotion_pending"
            if manager.status_paths(primary):
                observed.append("The original checkout has uncommitted changes.")
            if manager._git(primary, "symbolic-ref", "--short", "HEAD", allowed=(0, 1)) != source_branch:
                observed.append("The original checkout is no longer on the recorded branch.")
            if manager._operation_in_progress(primary):
                observed.append("The original checkout has an unfinished Git operation.")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        stage = "recovery_pending"
        observed.append("Delivery evidence needs verification or recovery.")
    command = None if stage == "complete" else continuation(run["owner"], row["logical_project_root"])
    messages = {
        "promotion_pending": f"Implementation is complete in the retained outer worktree. Changes have not yet been delivered to {source_branch}.",
        "finalization_pending": f"Changes have been promoted to {source_branch}; delivery finalization or cleanup remains pending.",
        "recovery_pending": f"Delivery to {source_branch} needs verification or recovery; promotion is not yet confirmed.",
        "complete": f"Changes have been delivered to {source_branch} and owned cleanup is complete.",
    }
    message = messages[stage]
    if stage != "complete":
        message += " " + " ".join(dict.fromkeys(observed))
        message += " Resolve the reported blocker and ensure the original checkout is clean, then resume from the original project with:\n" + command
    from workflow_uat import outcome
    uat = outcome(run_dir)
    message += " UAT: " + uat["status"] + ". " + uat["reason"]
    return {"uat": uat, "delivery_stage": stage, "original_project": row["logical_project_root"],
            "source_branch": source_branch,
            "retained_checkout": outer["worktree"] if Path(outer["worktree"]).exists() else None,
            "reasons": list(dict.fromkeys(observed)), "next_action": command, "user_message": message}


def attach(run_dir: Path, response: dict, reasons=()) -> dict:
    """A reporting gap must never replace the original outcome/error."""
    try:
        value = guidance(run_dir, reasons)
        if value is not None:
            return {**response, "delivery": value, "next_action": value["next_action"],
                    "user_message": value["user_message"]}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        pass
    return response
