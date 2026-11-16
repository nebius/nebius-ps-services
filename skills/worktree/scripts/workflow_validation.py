"""Producer-owned acceptance receipts for exact task and combined-wave targets."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import time
import shutil

from workflow_state import WorkflowError, read as _read, write, lock
from workflow_process import ProcessCleanupError, run_owned, admit_execution_policy
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def read(path):
    try:
        return _read(path)
    except (OSError, ValueError) as error:
        raise WorkflowError("RESULT_INVALID", "required validation evidence is unavailable") from error


SCHEMA = "workflow/validation-plan-v2"
RECEIPT = "workflow/validation-receipt-v2"


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
            if (not isinstance(check, dict) or set(check) != {"id", "argv", "execution_policy", "environment_keys"}
                    or not isinstance(check["id"], str) or not check["id"].strip() or check["id"] in identifiers
                    or not isinstance(check["argv"], list) or not check["argv"]
                    or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in check["argv"])
                    or not isinstance(check["environment_keys"], list)
                    or any(key not in {"PATH", "LANG", "LC_ALL", "TZ"} for key in check["environment_keys"])):
                raise WorkflowError("VALIDATION_PLAN_INVALID", "acceptance check definition is invalid")
            try:
                admit_execution_policy(check["execution_policy"])
            except ValueError as error:
                raise WorkflowError("VALIDATION_PLAN_INVALID", str(error)) from error
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


def task_validation_context(assignment: dict) -> dict:
    """Read only this task's checks from its immutable execution basis."""
    import re

    run_dir = Path(str(assignment.get("run_dir") or ""))
    execution_id = str(assignment.get("execution_id") or "")
    fingerprint = str(assignment.get("basis_sha256") or "")
    task_id = str(assignment.get("task_id") or "")
    cwd = Path(str(assignment.get("worktree") or ""))
    if (not run_dir.is_absolute() or not cwd.is_absolute()
            or re.fullmatch(r"(?:FEAT|SLICE)-[0-9]{3,}", execution_id) is None
            or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
            or re.fullmatch(r"TASK-[0-9]{3,}", task_id) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(assignment.get("plan_digest") or "")) is None
            or assignment.get("owner") not in {"implement", "agentic-sdlc"}):
        raise WorkflowError("BASIS_INVALID", "task validation basis identity is invalid")
    basis = read(run_dir / "bases" / execution_id / (fingerprint + ".json"))
    if (digest(basis) != fingerprint
            or basis.get("schema") != "workflow/objective-impact-execution-basis-v3"
            or basis.get("owner") != assignment.get("owner")
            or basis.get("execution_id") != execution_id
            or basis.get("plan_digest") != assignment.get("plan_digest")):
        raise WorkflowError("BASIS_INVALID", "task validation basis changed")
    frozen_plan = Path(str(basis.get("plan_path") or ""))
    if (not frozen_plan.is_absolute() or ".." in frozen_plan.parts
            or not frozen_plan.is_relative_to(run_dir / "plans")):
        raise WorkflowError("BASIS_INVALID", "task validation plan is outside its run")
    plan = read(plan_path(frozen_plan))
    if (digest(plan) != basis.get("validation_plan_sha256")
            or not isinstance(plan.get("task_checks"), dict)
            or task_id not in plan["task_checks"]):
        raise WorkflowError("REPLAN_REQUIRED", "frozen task validation changed")
    validate_plan(plan, list(plan["task_checks"]))
    return {"cwd": str(cwd), "checks": plan["task_checks"][task_id]}


def target(run_dir: Path, execution_id: str, wave_id: str | None, task_id: str | None) -> tuple[dict, Path]:
    import workflow_execution as e
    coordinator = e._load_coordinator(run_dir, execution_id)
    e._verify_execution_objective_impact(run_dir, execution_id, coordinator)
    if wave_id is None:
        if task_id is not None:
            raise WorkflowError("VALIDATION_TARGET_INVALID", "candidate validation cannot select a task")
        from workflow_candidate import target as candidate_target
        return candidate_target(run_dir, execution_id, coordinator)
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
    from workflow_task_acceptance import require_quiescent as task_acceptance_quiescent
    task_acceptance_quiescent(run_dir, execution_id, wave_id=wave_id, task_id=task_id)
    roots = ([_root(run_dir, execution_id)] if execution_id is not None
             else [*(run_dir / "execution").glob("*/validation"), run_dir / "aggregation"])
    delivery_root = run_dir / "delivery-admission"
    roots.append(delivery_root)
    for root in roots:
        for path in sorted((root / "operations").glob("*.json")):
            operation = read(path)
            identity = operation.get("target", {})
            if root != delivery_root and wave_id is not None and identity.get("wave_id") != wave_id:
                continue
            if root != delivery_root and task_id is not None and identity.get("task_id") not in {None, task_id}:
                continue
            if operation.get("status") == "verified" or (
                    operation.get("status") == "interrupted" and operation.get("termination_proven") is True):
                continue
            raise WorkflowError("WRITER_NOT_QUIESCENT", "validation ownership is not quiescent; retain target and recover its evaluator")


def _run_check(check, cwd, *, control=None):
    env = {key: os.environ[key] for key in check["environment_keys"] if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0")
    argv = [sys.executable if arg == "{python}" else arg for arg in check["argv"]]
    # Invocation location can select interpreter configuration (for example,
    # pyvenv.cfg). Hash executable bytes below without replacing a symlink path.
    candidate = str(cwd / argv[0]) if "/" in argv[0] else argv[0]
    # Match Popen's target cwd when PATH contains relative or empty entries.
    search_path = os.pathsep.join(str(cwd / entry)
                                  for entry in env.get("PATH", os.defpath).split(os.pathsep))
    executable = shutil.which(candidate, path=search_path)
    if executable is None:
        error = WorkflowError("ENVIRONMENT_BLOCKER", "acceptance evaluator executable is unavailable")
        error.observation = {"local_group_quiescent": True}
        raise error
    argv[0] = executable
    executable_sha256 = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    from workflow_timing import observed
    with observed("tools") as sample:
        observation = run_owned(argv, cwd=cwd, env=env, policy=check["execution_policy"], control=control)
        sample["failed"] = observation["exit_code"] != 0
    if (observation.get("local_group_quiescent") is not True
            or any(observation.get(name, {}).get("complete") is not True for name in ("stdout", "stderr"))):
        error = ProcessCleanupError("validation evaluator termination is unproven")
        error.observation = {**observation, "local_group_quiescent": False}
        raise error
    code = observation["exit_code"] if observation["stop_reason"] == "exited" else None
    complete = all(observation[name]["complete"] for name in ("stdout", "stderr"))
    return {"id": check["id"], "check_sha256": digest(check), "exit_code": code,
            "outcome": "passed" if code == 0 and complete else "failed",
            "output_sha256": [observation[name]["sha256"] for name in ("stdout", "stderr")],
            "output_bytes": [observation[name]["bytes"] for name in ("stdout", "stderr")],
            "output_complete": complete, "stop_reason": observation["stop_reason"],
            "elapsed_seconds": observation["elapsed_seconds"], "timings": observation["timings"],
            "timing_clock": observation["timing_clock"],
            "local_group_quiescent": observation["local_group_quiescent"],
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


def produce(run_dir: Path, execution_id: str, wave_id: str | None, task_id: str | None = None, *, session: str) -> dict:
    from workflow_timing import measure
    with measure(run_dir, "validation"):
        return _produce(run_dir, execution_id, wave_id, task_id, session=session)


def _produce(run_dir: Path, execution_id: str, wave_id: str | None, task_id: str | None = None, *, session: str) -> dict:
    """Reserve one operation; never hold peer lifecycle locks during evaluation."""
    import workflow_execution as e
    from workflow_objectives import authorize_coordinator
    if wave_id is not None:
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
        writer_quiescent = True
        def authority(sample=None):
            try:
                with e._execution_transition_lock(run_dir, execution_id):
                    current, _ = authorize_coordinator(run_dir, session)
                    if current["coordinator_session_sha256"] != intent["coordinator_session_sha256"]:
                        raise WorkflowError("OWNERSHIP_INVALID", "validation coordinator changed during evaluation")
                return "continue"
            except WorkflowError as error:
                if error.code == "WORKFLOW_BUSY":
                    return "health_unknown"
                raise
            except (OSError, TimeoutError):
                return "health_unknown"
        try:
            checks = plan["wave_checks"] if task_id is None else plan["task_checks"][task_id]
            for check in checks:
                while authority() != "continue":
                    time.sleep(check["execution_policy"]["observation_interval_seconds"])
                writer_quiescent = False
                try:
                    outcomes.append(_run_check(check, cwd, control=authority))
                    writer_quiescent = outcomes[-1].get("local_group_quiescent") is True
                except BaseException as error:
                    writer_quiescent = getattr(error, "observation", {}).get("local_group_quiescent") is True
                    raise
                if outcomes[-1]["outcome"] != "passed":
                    break
            with e._execution_transition_lock(run_dir, execution_id):
                if authorize_coordinator(run_dir, session)[0]["coordinator_session_sha256"] != intent["coordinator_session_sha256"]:
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
                    proven = writer_quiescent
                    write(operation, {**intent, "status": "interrupted" if proven else "termination-unproven",
                                      "termination_proven": proven, "outcomes": outcomes,
                                      "interrupted_observation": getattr(error, "observation", None),
                                      "failure_code": getattr(error, "code", "VALIDATION_INTERRUPTED")})
            raise


def _historical_receipt(run_dir, execution_id, fingerprint, identity, *, failed=False):
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
    statuses_valid = (
        0 < len(outcomes) <= len(checks)
        and all(row.get("outcome") == "passed" and row.get("exit_code") == 0 for row in outcomes[:-1])
        and outcomes[-1].get("outcome") == "failed" and outcomes[-1].get("exit_code") != 0
    ) if failed else (
        len(outcomes) == len(checks)
        and all(row.get("outcome") == "passed" and row.get("exit_code") == 0 for row in outcomes)
    )
    if (digest(basis) != identity["basis_sha256"] or digest(plan) != basis["validation_plan_sha256"]
            or digest(receipt) != fingerprint or receipt.get("schema") != RECEIPT or receipt.get("target") != identity
            or admitted != {"receipt_sha256": fingerprint, "target_sha256": digest(identity)}
            or receipt.get("plan_sha256") != digest(plan) or receipt.get("producer_sha256") != producer
            or not statuses_valid
            or any(result.get("id") != check["id"] or result.get("check_sha256") != digest(check)
                   or any(e.SHA256_RE.fullmatch(str(result.get(key))) is None for key in ("environment_sha256", "executable_sha256"))
                   or len(result.get("output_sha256", [])) != 2
                   or any(e.SHA256_RE.fullmatch(str(value)) is None for value in result.get("output_sha256", []))
                   for check, result in zip(checks, outcomes))):
        raise WorkflowError("INTEGRATION_VALIDATION_FAILED", "exact independent acceptance checks did not pass")
    return path, receipt


def failed_wave_receipt(run_dir, execution_id, wave_id):
    """Bind failure evidence without admitting it as a successful validation."""
    load_plan(run_dir, execution_id)
    identity, _ = target(run_dir, execution_id, wave_id, None)
    index = read(_root(run_dir, execution_id) / "targets" / (digest(identity) + ".json"))
    _, receipt = _historical_receipt(run_dir, execution_id, index.get("receipt_sha256"), identity, failed=True)
    return {"receipt_sha256": digest(receipt), "target": identity}


def verify_failed_wave_receipt(run_dir, execution_id, reference):
    if not isinstance(reference, dict) or set(reference) != {"receipt_sha256", "target"}:
        raise WorkflowError("RESULT_INVALID", "failed validation reference is missing")
    return _historical_receipt(run_dir, execution_id, reference["receipt_sha256"], reference["target"], failed=True)[1]


def review_plan_revision(run_dir, execution_id, coordinator, plan_path_value, replacement, failures):
    """Freeze a coordinator's equivalence review; never infer test success."""
    import workflow_execution as e
    from workflow_objectives import validate_run
    eligible = [wave for wave in failures if wave["combined_failure"]["authority"]["classification"] == "PLAN_DEFECT"]
    if not eligible:
        return None
    previous = load_plan(run_dir, execution_id)
    if any(replacement["task_checks"].get(task) != checks for task, checks in previous["task_checks"].items()):
        raise WorkflowError("REPLAN_REQUIRED", "replacement changes previously frozen task acceptance")
    if replacement["wave_checks"] == previous["wave_checks"]:
        raise WorkflowError("REPLAN_REQUIRED", "plan command repair requires a reviewed command correction")
    wave = eligible[-1]
    authority = e._validated_wave_failure(run_dir, execution_id, wave, active=True)
    if any(e._validated_wave_failure(run_dir, execution_id, item)[key] != authority[key]
           for item in failures for key in ("blocker_key", "oracle", "classification")):
        raise WorkflowError("REPLAN_REQUIRED", "plan failures require separate repair ownership")
    receipt = verify_failed_wave_receipt(run_dir, execution_id, wave["combined_failure"].get("validation_failure"))
    if receipt["plan_sha256"] != digest(previous):
        raise WorkflowError("REPLAN_REQUIRED", "plan repair does not bind the failed validation plan")
    old, new = previous["wave_checks"], replacement["wave_checks"]
    if (len(old) != len(new) or any(
            {key: value for key, value in left.items() if key != "argv"}
            != {key: value for key, value in right.items() if key != "argv"}
            for left, right in zip(old, new))):
        raise WorkflowError("REPLAN_REQUIRED", "plan command repair must preserve check identities, limits and environment")
    path = plan_path_value.with_suffix(plan_path_value.suffix + ".validation-review.json")
    e._reject_symlink_components(path, "validation plan review")
    if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 16384:
        raise WorkflowError("REPLAN_REQUIRED", "bounded regular validation plan review is required")
    review = read(path)
    _, objective = validate_run(run_dir)
    impact, _ = e.verify_current_objective_impact(run_dir, Path(coordinator["selected_project_root"]))
    expected = {
        "schema": "workflow/validation-plan-review-v1",
        "classification_id": authority["classification_id"], "dispatch_id": authority["dispatch_id"],
        "failed_receipt_sha256": digest(receipt),
        "prior_validation_plan_sha256": digest(previous),
        "replacement_validation_plan_sha256": digest(replacement),
        "replacement_plan_sha256": e.sha256_bytes(plan_path_value.read_bytes()),
        "objective_sha256": objective["revisions"][-1]["sha256"],
        "requirements_sha256": impact["requirements_sha256"], "design_sha256": impact["design_sha256"],
        "criteria_unchanged": True, "source_read_only": True,
    }
    if (set(review) != {*expected, "summary"} or any(review.get(key) != value for key, value in expected.items())
            or review.get("criteria_unchanged") is not True or review.get("source_read_only") is not True
            or not isinstance(review.get("summary"), str) or not review["summary"].strip()
            or len(review["summary"]) > 4096):
        raise WorkflowError("REPLAN_REQUIRED", "validation plan review changed authority or acceptance")
    from workflow_security import contains_sensitive
    if contains_sensitive(json.dumps(review)):
        raise WorkflowError("REPLAN_REQUIRED", "validation plan review contains sensitive material")
    archive = _root(run_dir, execution_id) / "revisions" / (digest(review) + ".json")
    if archive.exists():
        if read(archive) != review:
            raise WorkflowError("REPLAN_REQUIRED", "immutable validation plan review changed")
    else:
        write(archive, review, exclusive=True)
    return digest(review)


def verify_plan_revision(run_dir, execution_id, coordinator, failed_wave):
    """Recheck frozen authority before dispatch and historical resolution."""
    import workflow_execution as e
    failure = failed_wave["combined_failure"]
    authority = failure["authority"]
    receipt = verify_failed_wave_receipt(run_dir, execution_id, failure.get("validation_failure"))
    matches = []
    for fingerprint in coordinator.get("validation_revisions", []):
        if not isinstance(fingerprint, str) or e.SHA256_RE.fullmatch(fingerprint) is None:
            raise WorkflowError("REPLAN_REQUIRED", "invalid validation review identity")
        review = read(_root(run_dir, execution_id) / "revisions" / (fingerprint + ".json"))
        if digest(review) != fingerprint:
            raise WorkflowError("REPLAN_REQUIRED", "frozen validation review changed")
        if review["classification_id"] == authority["classification_id"] and review["dispatch_id"] == authority["dispatch_id"]:
            if (review["failed_receipt_sha256"] != digest(receipt)
                    or review["prior_validation_plan_sha256"] != receipt["plan_sha256"]
                    or review["criteria_unchanged"] is not True or review["source_read_only"] is not True):
                raise WorkflowError("REPLAN_REQUIRED", "plan review does not preserve failed evidence")
            matches.append((fingerprint, review))
    if len(matches) != 1:
        raise WorkflowError("REPLAN_REQUIRED", "plan failure requires its frozen validation review")
    return matches[0]


def verify_plan_revision_chain(run_dir, execution_id, coordinator, failed, correcting):
    """Trace every counted command revision to the successful producer plan."""
    import workflow_execution as e
    reference = failed["combined_failure"]["validation_failure"]
    expected_plan = verify_failed_wave_receipt(run_dir, execution_id, reference)["plan_sha256"]
    chain = []
    names = correcting["corrects"]
    for name in names[names.index(failed["wave_id"]):]:
        wave = read(e.wave_path(run_dir, execution_id, name))
        authority = e._validated_wave_failure(run_dir, execution_id, wave)
        if authority["classification"] != "PLAN_DEFECT":
            raise WorkflowError("REPLAN_REQUIRED", "command revision chain changed repair ownership")
        fingerprint, review = verify_plan_revision(run_dir, execution_id, coordinator, wave)
        if review["prior_validation_plan_sha256"] != expected_plan:
            raise WorkflowError("REPLAN_REQUIRED", "command revision chain lost a failed plan")
        expected_plan = review["replacement_validation_plan_sha256"]
        chain.append(fingerprint)
    _, receipt = verify_recorded_wave(run_dir, execution_id, correcting, coordinator)
    if receipt["plan_sha256"] != expected_plan:
        raise WorkflowError("INTEGRATION_VALIDATION_FAILED", "correction did not execute the reviewed plan")
    return chain


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
    repo = Path(coordinator["git_root"])
    from workflow_objectives import validate_run
    if validate_run(run_dir)[0].get("automatic_outer") and not repo.exists():
        from workflow_automatic import verify_delivery
        from workflow_projects import get
        verify_delivery(run_dir)
        repo = Path(get(run_dir)["source_root"])
    results = {}
    for task in wave["task_ids"]:
        result = read(e.result_path(run_dir, execution_id, wave["wave_id"], task))
        assignment = read(e.assignment_path(run_dir, execution_id, wave["wave_id"], task))
        e._validate_result_record(result, assignment)
        results[task] = result["result_digest"]
    identity = {"run_id": run_dir.name, "execution_id": execution_id, "wave_id": wave["wave_id"], "task_id": None,
                "basis_sha256": wave.get("validation_basis_sha256"), "head": wave["integration_head"],
                "tree": e.git(repo, ["rev-parse", wave["integration_head"] + "^{tree}"], "verify historical wave tree"),
                "inputs": results}
    return _historical_receipt(run_dir, execution_id, wave.get("validation_receipt_sha256"), identity)
