"""Producer-owned acceptance receipts for exact task and combined-wave targets."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import shutil

from workflow_state import WorkflowError, read as _read, write, lock
from workflow_process import OwnedProcessGroup, ProcessCleanupError
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def read(path):
    try:
        return _read(path)
    except (OSError, ValueError) as error:
        raise WorkflowError("RESULT_INVALID", "required validation evidence is unavailable") from error


SCHEMA = "workflow/validation-plan-v1"
RECEIPT = "workflow/validation-receipt-v1"


def plan_path(plan: Path) -> Path:
    return plan.with_suffix(plan.suffix + ".validation.json")


def validate_plan(value: dict, task_ids: list[str]) -> dict:
    if (set(value) != {"schema", "wave_checks", "task_checks"} or value.get("schema") != SCHEMA
            or not isinstance(value.get("task_checks"), dict)
            or set(value["task_checks"]) != set(task_ids)):
        raise WorkflowError("VALIDATION_PLAN_INVALID", "validation plan must cover every frozen task")
    for checks in [value["wave_checks"], *value["task_checks"].values()]:
        if not isinstance(checks, list) or not checks:
            raise WorkflowError("VALIDATION_PLAN_INVALID", "nonempty independent acceptance checks are required")
        identifiers = set()
        for check in checks:
            if (not isinstance(check, dict) or set(check) != {"id", "argv", "timeout_seconds", "environment_keys"}
                    or not isinstance(check["id"], str) or not check["id"].strip() or check["id"] in identifiers
                    or not isinstance(check["argv"], list) or not check["argv"]
                    or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in check["argv"])
                    or type(check["timeout_seconds"]) is not int or not 1 <= check["timeout_seconds"] <= 3600
                    or not isinstance(check["environment_keys"], list)
                    or any(key not in {"PATH", "LANG", "LC_ALL", "TZ"} for key in check["environment_keys"])):
                raise WorkflowError("VALIDATION_PLAN_INVALID", "acceptance check definition is invalid")
            from workflow_security import contains_sensitive
            if contains_sensitive(json.dumps(check)):
                raise WorkflowError("VALIDATION_PLAN_INVALID", "acceptance check contains sensitive material")
            identifiers.add(check["id"])
    return value


def load_plan(run_dir: Path, execution_id: str) -> dict:
    import workflow_execution as e
    coordinator = e._load_coordinator(run_dir, execution_id)
    plan = read(plan_path(Path(coordinator["plan_path"])))
    validate_plan(plan, list(coordinator["task_waves"]))
    if digest(plan) != coordinator["validation_plan_sha256"]:
        raise WorkflowError("REPLAN_REQUIRED", "frozen acceptance checks changed")
    return plan


def target(run_dir: Path, execution_id: str, wave_id: str, task_id: str | None) -> tuple[dict, Path]:
    import workflow_execution as e
    coordinator = e._load_coordinator(run_dir, execution_id)
    e._verify_execution_objective_impact(run_dir, execution_id, coordinator)
    wave = read(e.wave_path(run_dir, execution_id, wave_id))
    if task_id is not None:
        from workflow_worker_results import read_receipt
        from workflow_resources import require_released
        assignment = read(e.assignment_path(run_dir, execution_id, wave_id, task_id))
        e._validate_assignment_record(assignment)
        state = read(e.task_path(run_dir, execution_id, wave_id, task_id))
        e._validate_assignment_handoff(run_dir, execution_id, coordinator, assignment, e._task_plan_from_state(state))
        _, native = read_receipt(assignment, state)
        if native["worker_result"]["status"] != "verified_no_change":
            raise WorkflowError("RESULT_INVALID", "task acceptance is for a no-change proposal")
        require_released(run_dir, assignment)
        cwd = Path(assignment["worktree"])
        if (e.head(cwd) != assignment["base_head"] or not e.clean(cwd)
                or e.branch(cwd) != assignment["branch"]
                or e.git_common_dir(cwd) != Path(assignment["git_common_dir"])):
            raise WorkflowError("RESULT_INVALID", "no-change task is not at its exact clean base")
        inputs = {"assignment_digest": assignment["assignment_digest"], "native_receipt_sha256": native["receipt_sha256"]}
        basis = assignment["basis_sha256"]
    else:
        if wave["status"] not in {"integrated", "done", "blocked"} or not wave.get("integration_head"):
            raise WorkflowError("VALIDATION_TARGET_INVALID", "wave must be integrated before validation")
        if wave["active_task_ids"] or wave["ready_task_ids"] or set(wave["admitted_task_ids"]) != set(wave["task_ids"]):
            raise WorkflowError("WRITER_NOT_QUIESCENT", "all wave writers must be admitted and stopped")
        cwd = Path(coordinator["integration_worktree"])
        if not e.clean(cwd) or e.head(cwd) != wave["integration_head"]:
            raise WorkflowError("VALIDATION_TARGET_INVALID", "combined wave target drifted")
        inputs = {task: read(e.result_path(run_dir, execution_id, wave_id, task))["result_digest"] for task in wave["task_ids"]}
        basis = e.canonical_digest(e.verify_objective_impact_execution(run_dir, execution_id, coordinator["plan_digest"], Path(coordinator["selected_project_root"])))
    return {"run_id": run_dir.name, "execution_id": execution_id, "wave_id": wave_id,
            "task_id": task_id, "basis_sha256": basis, "head": e.head(cwd),
            "tree": e.git(cwd, ["rev-parse", "HEAD^{tree}"], "bind validation tree"),
            "inputs": inputs}, cwd


def _root(run_dir, execution_id):
    import workflow_execution as e
    return e.execution_dir(run_dir, execution_id) / "validation"


def require_quiescent(run_dir, execution_id=None, *, wave_id=None, task_id=None):
    """Call under the lifecycle lock before ownership or target mutations."""
    roots = ([_root(run_dir, execution_id)] if execution_id is not None
             else (run_dir / "execution").glob("*/validation"))
    for root in roots:
        for path in sorted((root / "operations").glob("*.json")):
            operation = read(path)
            identity = operation.get("target", {})
            if wave_id is not None and identity.get("wave_id") != wave_id:
                continue
            if task_id is not None and identity.get("task_id") not in {None, task_id}:
                continue
            if operation.get("status") == "verified" or (
                    operation.get("status") == "interrupted" and operation.get("termination_proven") is True):
                continue
            raise WorkflowError("WRITER_NOT_QUIESCENT", "validation ownership is not quiescent; retain target and recover its evaluator")


def _run_check(check, cwd):
    env = {key: os.environ[key] for key in check["environment_keys"] if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0")
    argv = [sys.executable if arg == "{python}" else arg for arg in check["argv"]]
    candidate = str((cwd / argv[0]).resolve()) if "/" in argv[0] else argv[0]
    # Match Popen's target cwd when PATH contains relative or empty entries.
    search_path = os.pathsep.join(str((cwd / entry).resolve())
                                  for entry in env.get("PATH", os.defpath).split(os.pathsep))
    executable = shutil.which(candidate, path=search_path)
    if executable is None:
        raise WorkflowError("ENVIRONMENT_BLOCKER", "acceptance evaluator executable is unavailable")
    argv[0] = executable
    executable_sha256 = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        owner = OwnedProcessGroup()
        process = None
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, process_group=owner.id)
            deadline = time.monotonic() + check["timeout_seconds"]
            while process.poll() is None:
                if time.monotonic() >= deadline or stdout.tell() > 1048576 or stderr.tell() > 1048576:
                    break
                time.sleep(.02)
            code = process.returncode
            if stdout.tell() > 1048576 or stderr.tell() > 1048576:
                code = None
        finally:
            cleanup_error = None
            try:
                owner.close(grace=.05)
            except BaseException as error:
                cleanup_error = error
            try:
                if process is not None:
                    process.wait(timeout=5)
                owner.verify_absent()
            except BaseException as error:
                raise ProcessCleanupError("acceptance evaluator termination is unproven") from error
            if cleanup_error is not None:
                raise cleanup_error
        outputs = []
        for stream in (stdout, stderr):
            stream.seek(0)
            outputs.append(hashlib.sha256(stream.read(1048576)).hexdigest())
    return {"id": check["id"], "check_sha256": digest(check), "exit_code": code,
            "outcome": "passed" if code == 0 else "failed", "output_sha256": outputs,
            "environment_sha256": digest(env), "executable_sha256": executable_sha256}


def _publish(root, receipt):
    fingerprint = digest(receipt)
    path = root / "receipts" / (fingerprint + ".json")
    for destination, value in (
        (path, receipt),
        (root / "produced" / (fingerprint + ".json"),
         {"receipt_sha256": fingerprint, "target_sha256": digest(receipt["target"])}),
        (root / "targets" / (digest(receipt["target"]) + ".json"), {"receipt_sha256": fingerprint}),
    ):
        if destination.exists():
            if read(destination) != value:
                raise WorkflowError("RESULT_INVALID", "immutable producer admission changed")
        else:
            write(destination, value, exclusive=True)
    return {"receipt_path": str(path), "outcome": "passed" if all(item["outcome"] == "passed" for item in receipt["outcomes"]) else "failed"}


def produce(run_dir: Path, execution_id: str, wave_id: str, task_id: str | None = None, *, session: str) -> dict:
    """Reserve one operation; never hold peer lifecycle locks during evaluation."""
    import workflow_execution as e
    from workflow_objectives import authorize_coordinator, validate_run
    e._require_id(wave_id, e.WAVE_ID_RE, "wave ID")
    if task_id is not None:
        e._require_id(task_id, e.TASK_ID_RE, "task ID")
    root = _root(run_dir, execution_id)
    operation_key = digest([wave_id, task_id])
    with lock(root / "operation-locks" / operation_key):
        with e._execution_transition_lock(run_dir, execution_id):
            run, _ = authorize_coordinator(run_dir, session)
            plan = load_plan(run_dir, execution_id)
            identity, cwd = target(run_dir, execution_id, wave_id, task_id)
            operation = root / "operations" / (digest(identity) + ".json")
            if operation.exists():
                previous = read(operation)
                if previous.get("status") == "verified":
                    receipt = previous["receipt"]
                    if receipt["target"] != identity or receipt["plan_sha256"] != digest(plan):
                        raise WorkflowError("RESULT_INVALID", "validation operation identity changed")
                    return _publish(root, receipt)
                raise WorkflowError("RECOVERY_REQUIRED", "prior validation failed or was interrupted; preserve evidence and use failure classification/replanning")
            archived_plan = root / "plans" / (digest(plan) + ".json")
            if archived_plan.exists():
                if read(archived_plan) != plan:
                    raise WorkflowError("RESULT_INVALID", "frozen validation plan archive changed")
            else:
                write(archived_plan, plan, exclusive=True)
            intent = {"schema": "workflow/validation-operation-v1", "target": identity,
                      "plan_sha256": digest(plan), "status": "running",
                      "coordinator_session_sha256": run["coordinator_session_sha256"]}
            write(operation, intent, exclusive=True)
        outcomes = []
        try:
            checks = plan["wave_checks"] if task_id is None else plan["task_checks"][task_id]
            for check in checks:
                outcomes.append(_run_check(check, cwd))
                if outcomes[-1]["outcome"] != "passed":
                    break
            with e._execution_transition_lock(run_dir, execution_id):
                if validate_run(run_dir)[0]["coordinator_session_sha256"] != intent["coordinator_session_sha256"]:
                    raise WorkflowError("OWNERSHIP_INVALID", "validation coordinator changed during evaluation")
                if target(run_dir, execution_id, wave_id, task_id)[0] != identity:
                    raise WorkflowError("VALIDATION_TARGET_INVALID", "acceptance checks changed their target")
                receipt = {"schema": RECEIPT, "target": identity, "plan_sha256": digest(plan),
                           "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           "outcomes": outcomes}
                # Seal before publishing; replay repairs only these exact artifacts.
                write(operation, {**intent, "status": "verified", "receipt": receipt})
                return _publish(root, receipt)
        except BaseException as error:
            with e._execution_transition_lock(run_dir, execution_id):
                if read(operation).get("status") == "running":
                    proven = not isinstance(error, ProcessCleanupError)
                    write(operation, {**intent, "status": "interrupted" if proven else "termination-unproven",
                                      "termination_proven": proven, "outcomes": outcomes,
                                      "failure_code": getattr(error, "code", "VALIDATION_INTERRUPTED")})
            raise


def _historical_receipt(run_dir, execution_id, fingerprint, identity):
    import workflow_execution as e
    if (not isinstance(fingerprint, str) or e.SHA256_RE.fullmatch(fingerprint) is None
            or e.SHA256_RE.fullmatch(str(identity.get("basis_sha256"))) is None):
        raise WorkflowError("RESULT_INVALID", "validation receipt digest is invalid")
    root = _root(run_dir, execution_id)
    path = root / "receipts" / (fingerprint + ".json")
    receipt = read(path)
    admitted = read(root / "produced" / (fingerprint + ".json"))
    basis = read(run_dir / "bases" / execution_id / (identity["basis_sha256"] + ".json"))
    plan = read(root / "plans" / (basis["validation_plan_sha256"] + ".json"))
    checks = plan["wave_checks"] if identity["task_id"] is None else plan["task_checks"][identity["task_id"]]
    producer = basis["identities"].get(str(Path(__file__).resolve()))
    outcomes = receipt.get("outcomes", [])
    if (digest(basis) != identity["basis_sha256"] or digest(plan) != basis["validation_plan_sha256"]
            or digest(receipt) != fingerprint or receipt.get("schema") != RECEIPT or receipt.get("target") != identity
            or admitted != {"receipt_sha256": fingerprint, "target_sha256": digest(identity)}
            or receipt.get("plan_sha256") != digest(plan) or receipt.get("producer_sha256") != producer
            or len(outcomes) != len(checks)
            or any(result.get("id") != check["id"] or result.get("check_sha256") != digest(check)
                   or result.get("outcome") != "passed" or result.get("exit_code") != 0
                   or any(e.SHA256_RE.fullmatch(str(result.get(key))) is None for key in ("environment_sha256", "executable_sha256"))
                   or len(result.get("output_sha256", [])) != 2
                   or any(e.SHA256_RE.fullmatch(str(value)) is None for value in result.get("output_sha256", []))
                   for check, result in zip(checks, outcomes))):
        raise WorkflowError("INTEGRATION_VALIDATION_FAILED", "exact independent acceptance checks did not pass")
    return path, receipt


def verify(run_dir: Path, execution_id: str, wave_id: str, task_id: str | None = None,
           supplied: Path | None = None) -> tuple[Path, dict]:
    load_plan(run_dir, execution_id)
    identity, _ = target(run_dir, execution_id, wave_id, task_id)
    index = read(_root(run_dir, execution_id) / "targets" / (digest(identity) + ".json"))
    path, receipt = _historical_receipt(run_dir, execution_id, index.get("receipt_sha256"), identity)
    if supplied is not None and supplied.absolute() != path.absolute():
        raise WorkflowError("RESULT_INVALID", "validation receipt is not the current producer-admitted receipt")
    return path, receipt


def verify_recorded_task(assignment: dict, result: dict) -> None:
    expected = {"run_id": Path(assignment["run_dir"]).name, "execution_id": assignment["execution_id"],
                "wave_id": assignment["wave_id"], "task_id": assignment["task_id"], "basis_sha256": assignment["basis_sha256"],
                "head": result["head"], "tree": result["tree"],
                "inputs": {"assignment_digest": assignment["assignment_digest"],
                           "native_receipt_sha256": result["worker_result_receipt_sha256"]}}
    _historical_receipt(Path(assignment["run_dir"]), assignment["execution_id"], result.get("acceptance_receipt_sha256"), expected)


def verify_recorded_wave(run_dir, execution_id, wave, coordinator):
    import workflow_execution as e
    results = {}
    for task in wave["task_ids"]:
        result = read(e.result_path(run_dir, execution_id, wave["wave_id"], task))
        assignment = read(e.assignment_path(run_dir, execution_id, wave["wave_id"], task))
        e._validate_result_record(result, assignment)
        results[task] = result["result_digest"]
    identity = {"run_id": run_dir.name, "execution_id": execution_id, "wave_id": wave["wave_id"], "task_id": None,
                "basis_sha256": wave.get("validation_basis_sha256"), "head": wave["integration_head"],
                "tree": e.git(Path(coordinator["git_root"]), ["rev-parse", wave["integration_head"] + "^{tree}"], "verify historical wave tree"),
                "inputs": results}
    return _historical_receipt(run_dir, execution_id, wave.get("validation_receipt_sha256"), identity)
