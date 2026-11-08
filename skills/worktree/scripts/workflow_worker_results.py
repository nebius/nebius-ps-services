"""Coordinator-owned ingestion of native results for both workflow coordinators."""
from __future__ import annotations

from pathlib import Path
import re

from workflow_objectives import authorize_coordinator
from workflow_state import WorkflowError, digest, read, write
from workflow_worker_contract import result_rejection

SCHEMA = "workflow/worker-result-receipt-v1"
HASH = re.compile(r"[0-9a-f]{64}\Z")


def receipt_path(assignment: dict, attempt: int) -> Path:
    if type(attempt) is not int or attempt < 1:
        raise WorkflowError("RESULT_INVALID", "worker result attempt is invalid")
    return (Path(assignment["run_dir"]) / "execution" / assignment["execution_id"]
            / "worker-results" / assignment["wave_id"]
            / f"{assignment['task_id']}-{attempt:04d}.json")


def _context(run_dir, execution_id, wave_id, task_id):
    import workflow_execution as engine
    coordinator = engine._load_coordinator(run_dir, execution_id)
    assignment = read(engine.assignment_path(run_dir, execution_id, wave_id, task_id))
    engine._validate_assignment_record(assignment)
    engine._assert_current_assignment(run_dir, execution_id, coordinator, assignment)
    state = read(engine.task_path(run_dir, execution_id, wave_id, task_id))
    engine._validate_task_liveness(state)
    return assignment, state


def read_receipt(assignment: dict, state: dict) -> tuple[Path, dict]:
    """Validate immutable historical evidence without reassigning its issuer."""
    path = receipt_path(assignment, state["attempt"])
    if not path.exists() and not path.is_symlink():
        raise WorkflowError("WORKER_RESULT_REQUIRED", "ingest the complete native worker result first")
    try:
        value = read(path)
    except FileNotFoundError as error:
        raise WorkflowError("WORKER_RESULT_REQUIRED", "ingest the complete native worker result first") from error
    unsigned = dict(value)
    recorded = unsigned.pop("receipt_sha256", None)
    expected = {
        "schema", "assignment_digest", "basis_sha256", "worker_session_hash", "attempt",
        "coordinator_session_hash", "termination_path", "termination_sha256", "worker_result",
        "receipt_sha256",
    }
    if (set(value) != expected or value.get("schema") != SCHEMA
            or recorded != digest(unsigned)
            or value.get("assignment_digest") != assignment["assignment_digest"]
            or value.get("basis_sha256") != assignment["basis_sha256"]
            or value.get("worker_session_hash") != state["worker_session_hash"]
            or type(value.get("attempt")) is not int or value["attempt"] != state["attempt"]
            or HASH.fullmatch(str(value.get("coordinator_session_hash"))) is None
            or result_rejection(value.get("worker_result"), assignment) is not None):
        raise WorkflowError("RESULT_INVALID", "worker result receipt identity changed")
    termination_path = Path(str(value.get("termination_path")))
    parent = Path(assignment["run_dir"]) / "execution" / assignment["execution_id"] / "terminations"
    if termination_path.parent != parent or not termination_path.is_absolute():
        raise WorkflowError("RESULT_INVALID", "worker result termination path changed")
    observed = read(termination_path)
    if (digest(observed) != value["termination_sha256"]
            or observed.get("schema") != "workflow/worker-termination-v1"
            or observed.get("assignment_digest") != assignment["assignment_digest"]
            or observed.get("worker_session_hash") != state["worker_session_hash"]
            or observed.get("observer_session_hash") != value["coordinator_session_hash"]
            or observed.get("status") != "terminated"
            or not observed.get("native_handle") or not observed.get("observation")):
        raise WorkflowError("RESULT_INVALID", "worker result termination evidence changed")
    return path, value


def ingest_result(run_dir: Path, execution_id: str, wave_id: str, task_id: str,
                  payload: object, termination_path: Path, session: str) -> dict:
    """Validate before output or effects; preserve an exact per-attempt receipt."""
    import workflow_execution as engine
    from workflow_reconcile import termination
    with engine._execution_transition_lock(run_dir, execution_id):
        run, _ = authorize_coordinator(run_dir, session)
        assignment, state = _context(run_dir, execution_id, wave_id, task_id)
        rejection = result_rejection(payload, assignment)
        if rejection is not None:
            raise WorkflowError("WORKER_FAILED", f"worker result is invalid (reason={rejection})")
        path = receipt_path(assignment, state["attempt"])
        if path.exists():
            _, receipt = read_receipt(assignment, state)
            if (receipt["worker_result"] != payload
                    or receipt["termination_path"] != str(termination_path.absolute())
                    or receipt["termination_sha256"] != digest(read(termination_path))):
                raise WorkflowError("RESULT_CONFLICT", "immutable worker result already differs")
        else:
            if state["status"] != "running":
                raise WorkflowError("RESULT_INVALID", "only a running worker can return a new result")
            if payload["status"] == "verified_no_change":
                guard = engine._worker_guard_status(assignment, state, clock=engine._system_clock)
                if guard["status"] != "ACTIVE":
                    raise WorkflowError(str(guard["status"]), "no-change proposal exceeded its worker budget")
            wave = read(engine.wave_path(run_dir, execution_id, wave_id))
            if task_id not in wave["active_task_ids"]:
                raise WorkflowError("RESULT_INVALID", "worker result has no active slot")
            _, observed_state, observation = termination(
                run_dir, execution_id, wave_id, task_id, termination_path, session,
            )
            if observed_state != state:
                raise WorkflowError("RESULT_INVALID", "worker changed during result ingestion")
            receipt = {
                "schema": SCHEMA, "assignment_digest": assignment["assignment_digest"],
                "basis_sha256": assignment["basis_sha256"],
                "worker_session_hash": state["worker_session_hash"], "attempt": state["attempt"],
                "coordinator_session_hash": run["coordinator_session_sha256"],
                "termination_path": str(termination_path.absolute()),
                "termination_sha256": digest(observation), "worker_result": payload,
            }
            receipt["receipt_sha256"] = digest(receipt)
            write(path, receipt, exclusive=True)
        return {"receipt_path": str(path), "receipt_sha256": receipt["receipt_sha256"],
                "worker_status": receipt["worker_result"]["status"], "attempt": receipt["attempt"]}


def successful_result(assignment: dict, state: dict, supplied_path: Path) -> dict:
    expected = receipt_path(assignment, state["attempt"])
    if supplied_path.absolute() != expected:
        raise WorkflowError("RESULT_INVALID", "task finish requires its canonical current result receipt")
    _, receipt = read_receipt(assignment, state)
    if receipt["worker_result"]["status"] not in {"implemented", "verified_no_change"}:
        raise WorkflowError("WORKER_RESULT_INCOMPLETE", "blocked or replan work cannot finish")
    return receipt


def require_dispatchable(run_dir: Path, execution_id: str) -> None:
    """Stop new work only; existing peers retain their drain/recovery operations."""
    import workflow_execution as engine
    root = engine.execution_dir(run_dir, execution_id)
    for task_file in sorted((root / "tasks").glob("*/*.json")):
        state = read(task_file)
        if state.get("status") == "superseded" or not state.get("attempt"):
            continue
        wave_id, task_id = task_file.parent.name, task_file.stem
        wave = read(engine.wave_path(run_dir, execution_id, wave_id))
        if wave["status"] in {"done", "failed", "superseded"}:
            continue
        assignment = read(engine.assignment_path(run_dir, execution_id, wave_id, task_id))
        engine._validate_assignment_record(assignment)
        if not receipt_path(assignment, state["attempt"]).exists():
            continue
        _, receipt = read_receipt(assignment, state)
        status = receipt["worker_result"]["status"]
        if status in {"blocked", "replan_required"}:
            code = "WORKER_BLOCKED" if status == "blocked" else "WORKER_REPLAN_REQUIRED"
            raise WorkflowError(code, "a current worker result requires coordinator diagnosis before new dispatch")
