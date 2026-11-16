"""Execute a frozen repository-owned acceptance harness and admit exact evidence.

The reviewed harness owns assertions and independent product observations. The
coordinator owns scheduling and authority; it cannot turn supplied labels into
execution evidence. No raw harness output or environment values are persisted.
"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from workflow_state import git_environment, WorkflowError, digest, lock, read, write
from workflow_objectives import _git
from workflow_program import accepted, verify_inputs
from project_specs_lib.source_bundle import read_source, relative_path

SHA = re.compile(r"[0-9a-f]{64}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SCHEMA = "workflow/qualification-plan-v3"
RECEIPT = "workflow/qualification-receipt-v4"


def require(condition, message):
    if not condition:
        raise WorkflowError("QUALIFICATION_BLOCKED", message)


def _contains_commit(checkout, ancestor, head):
    try:
        return _git(checkout, "merge-base", ancestor, head) == ancestor
    except WorkflowError:
        return False


def _promoted_candidate(run_dir, project, coordinators):
    """Find the exact delivered tip containing the scope, independent of ID order."""
    outputs = []
    checkouts = [project]
    for row in coordinators:
        heads = {row["promoted_head"]}
        completion_path = run_dir / "delivery" / row["execution_id"] / "completion.json"
        if row["execution_id"].startswith("SLICE-") and completion_path.exists():
            from workflow_delivery import verify_completion
            completion = verify_completion(run_dir, read(completion_path))
            require(completion["execution_id"] == row["execution_id"]
                    and completion["intent"]["head"] == row["promoted_head"],
                    "merged output differs from the selected promoted implementation")
            # Squash/rebase delivery changes commit ancestry. Only the existing
            # protected delivery owner's verified result may stand for its tip.
            heads.add(completion["result_sha"])
        outputs.append(heads)
        if row.get("delivered_checkout"):
            checkout = Path(row["delivered_checkout"])
            checkouts.append(checkout / row["project_scope"] if row["project_scope"] != "." else checkout)
    delivered_heads = set().union(*outputs)
    for checkout in dict.fromkeys(checkouts):
        if not checkout.is_dir():
            continue
        try:
            head = _git(checkout, "rev-parse", "HEAD")
            if head not in delivered_heads or _git(checkout, "status", "--porcelain"):
                continue
            # A promoted record alone does not prove that parallel branches
            # have been combined into a usable end-to-end candidate.
            if all(any(_contains_commit(checkout, candidate, head) for candidate in heads)
                   for heads in outputs):
                return checkout, head
        except WorkflowError:
            continue
    require(False, "requested UAT scope has no clean combined promoted candidate; retain pending UAT and continue eligible delivery")


def target(run_dir, execution_id, *, stage="promoted"):
    require(stage in {"promoted", "aggregate"}, "unsupported qualification stage")
    require(stage == "promoted" or not execution_id.startswith("PHASE:"), "aggregate qualification requires a delivery slice")
    run, latest, snapshot = accepted(run_dir)
    project = Path(run["project_root"])
    program = verify_inputs(run_dir, project)
    if execution_id == "OBJECTIVE":
        from workflow_uat import software_readiness, context, scope_criteria
        policy = context(run_dir)[2]
        readiness = software_readiness(run_dir)
        require(not readiness["missing"], "requested scope is not implemented yet")
        coordinators = [read(run_dir / "execution" / name / "coordinator.json") for name in readiness["executions"]]
        if stage == "promoted":
            project, head = _promoted_candidate(run_dir, project, coordinators)
        selected = {"executions": readiness["executions"], "scope": policy["scope"]}
        criteria = scope_criteria(snapshot, policy, program["definition"] if program else None)
        basis = {row["execution_id"]: read(run_dir / "objective-impact/execution" / (row["execution_id"] + ".json"))
                 for row in coordinators}
        kind = "runtime"
    elif execution_id.startswith("PHASE:"):
        require(program is not None, "phase qualification requires accepted source coverage")
        phase_id = execution_id.removeprefix("PHASE:")
        selected = next((r for r in program["definition"]["phases"] if r["id"] == phase_id), None)
        require(selected is not None, "unknown phase")
        require(not any(s["phase"] == phase_id for s in program["definition"]["slices"]),
                "delivery phases qualify through their slices")
        criteria, head, basis = selected["exit_criteria"], _git(project, "rev-parse", "HEAD"), None
        kind = "external"
    else:
        require(program is not None, "slice qualification requires accepted source coverage")
        selected = next((r for r in program["definition"]["slices"] if r["id"] == execution_id), None)
        require(selected is not None, "unknown delivery slice")
        coordinator = read(run_dir / "execution" / execution_id / "coordinator.json")
        require(coordinator.get("schema") == "workflow/execution-coordinator-v3"
                and coordinator.get("status") in {"promoted", "cleanup", "done"}
                and coordinator.get("promoted_head"), "qualification requires a promoted candidate")
        from workflow_impact import verify_execution
        basis = verify_execution(run_dir, execution_id, coordinator["plan_digest"], project)
        head = coordinator["promoted_head"]
        criteria = sorted(set(selected["criteria"] + selected["regression_criteria"]))
        kind = selected["change_kind"]
    if stage == "aggregate":
        from workflow_automatic import _candidate as aggregate_candidate
        identity, checkout, row = aggregate_candidate(run_dir)
        project = checkout / row["scope"] if row["scope"] != "." else checkout
        head = identity["head"]
    else:
        identity = {"head": head}
    require(_git(project, "rev-parse", "HEAD") == head and not _git(project, "status", "--porcelain"),
            "candidate checkout is dirty or differs from promotion")
    return {"project": project, "head": head, "criteria": criteria, "kind": kind,
            "stage": stage, "target_identity": identity,
            "obligation_basis": {c: program["bundle"]["obligation_basis"][c] if program else digest(c) for c in criteria},
            "basis_sha256": digest(basis) if basis else None, "objective_sha256": latest["sha256"],
            "inputs": snapshot["inputs"], "completion": snapshot["completion"], "definition": selected}


def validate_plan(plan, expected):
    keys = {"schema", "execution_id", "runner", "runner_sha256", "criteria", "environment",
            "operations", "execution_policy", "environment_keys", "live", "backend"}
    require(isinstance(plan, dict) and set(plan) == keys and plan["schema"] == SCHEMA, "invalid qualification plan")
    require(isinstance(plan["criteria"], list) and all(isinstance(v, str) for v in plan["criteria"])
            and len(plan["criteria"]) == len(set(plan["criteria"])) == len(expected["criteria"])
            and set(plan["criteria"]) == set(expected["criteria"]), "all slice and inherited criteria must be tested")
    relative_path(plan["runner"])
    require(plan["runner"].endswith(".py") and SHA.fullmatch(str(plan["runner_sha256"])), "runner must be a pinned Python harness")
    from workflow_process import admit_execution_policy
    try:
        admit_execution_policy(plan["execution_policy"])
    except ValueError as error:
        raise WorkflowError("QUALIFICATION_BLOCKED", str(error)) from error
    require(plan["backend"] in {"existing", "container", "nebius"}, "invalid selected UAT backend")
    require(type(plan["live"]) is bool, "explicit live applicability is required")
    environment = plan["environment"]
    require(isinstance(environment, dict) and set(environment) == {"id", "resource", "nonproduction"}
            and environment["nonproduction"] is True
            and all(isinstance(environment[k], str) and LABEL.fullmatch(environment[k]) for k in ("id", "resource")),
            "qualification requires an identified confirmed disposable/nonproduction target")
    require(isinstance(plan["operations"], list) and all(isinstance(v, str) and v for v in plan["operations"]), "invalid operation scope")
    require(set(plan["operations"]) <= set(expected["completion"].get("live_operations", [])),
            "live operations exceed accepted run authority")
    require(isinstance(plan["environment_keys"], list)
            and all(isinstance(v, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", v)
                    and not v.startswith(("PYTHON", "LD_", "DYLD_"))
                    for v in plan["environment_keys"]), "invalid runtime environment allowlist")
    if not plan["live"]:
        require(expected["kind"] == "non-runtime" and expected["completion"].get("allow_nonruntime_na") is True,
                "live N/A is limited to accepted non-runtime delivery")
        require(not plan["operations"], "non-runtime validation cannot claim live operations")


def _runner(project, plan):
    body = read_source(project, plan["runner"])
    require(hashlib.sha256(body).hexdigest() == plan["runner_sha256"], "qualification harness changed")
    tracked = _git(project, "ls-files", "--", plan["runner"])
    require(tracked == plan["runner"], "qualification harness must be reviewed committed source")
    return project / plan["runner"]


def frozen_plan(run_dir, execution_id, plan, expected, *, create=True):
    validate_plan(plan, expected)
    require(plan["execution_id"] == execution_id, "qualification target changed")
    _runner(expected["project"], plan)
    if execution_id == "OBJECTIVE":
        path = run_dir / "uat" / ("plan-" + expected["objective_sha256"] + ".json")
        if path.exists():
            require(read(path) == plan, "campaign plan is frozen; retain the trial and use objective steering")
        else:
            require(create, "frozen campaign plan is missing; retain the trial evidence")
            write(path, plan, exclusive=True)
    elif not execution_id.startswith("PHASE:"):
        coordinator = read(run_dir / "execution" / execution_id / "coordinator.json")
        text = Path(coordinator["plan_path"]).read_text()
        require(re.search(r"(?m)^Qualification plan SHA256: " + digest(plan) + r"$", text),
                "qualification must be frozen in the locked implementation plan")


def command(project, plan, action, criterion=None, *, control=None):
    """One bounded process group; output digests only, strict structured result."""
    runner = _runner(project, plan)
    argv = [sys.executable, "-I", str(runner), action]
    if criterion is not None:
        argv.extend(["--criterion", criterion])
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8"}
    environment.update({k: os.environ[k] for k in plan["environment_keys"] if k in os.environ})
    owned = git_environment(project)
    if "SKILLS_GIT_PROFILE" in owned:
        for key in ("SKILLS_GIT_PROFILE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_ATTR_NOSYSTEM", "GIT_TERMINAL_PROMPT"):
            environment[key] = owned[key]
    from workflow_process import run_owned, Capture
    observation = run_owned(argv, cwd=project, env=environment,
                            policy=({**plan["execution_policy"], "deadline": None} if action == "cleanup" else plan["execution_policy"]),
                            stdout_capture=Capture(1024 * 1024), control=control)
    raw = observation["stdout"]["data"]
    proof = {"action": action, "criterion": criterion, "exit_code": observation["exit_code"],
             "stdout_sha256": observation["stdout"]["sha256"],
             "stderr_sha256": observation["stderr"]["sha256"],
             "stdout_bytes": observation["stdout"]["bytes"],
             "stderr_bytes": observation["stderr"]["bytes"],
             "elapsed_seconds": observation["elapsed_seconds"], "timings": observation["timings"],
             "timing_clock": observation["timing_clock"],
             "stop_reason": observation["stop_reason"],
             "local_group_quiescent": observation["local_group_quiescent"]}
    try:
        require(raw is not None and observation["local_group_quiescent"]
                and observation["stderr"]["complete"], "qualification result or termination is incomplete")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            value = None
        if observation["exit_code"] != 0 or observation["stop_reason"] != "exited":
            failed = action == "criterion" and _criterion_result(value, criterion) and value["failed"] > 0
            error = WorkflowError("UAT_ASSERTION_FAILED" if failed else "QUALIFICATION_BLOCKED",
                                  "UAT assertions failed" if failed else "UAT harness execution was incomplete")
            error.execution_evidence = proof
            raise error
        require(isinstance(value, dict), "harness result must be an object")
        return value, proof

    except BaseException as error:
        error.execution_evidence = proof
        raise


def _criterion_result(value, criterion):
    return (isinstance(value, dict) and set(value) == {"criterion", "tests", "passed", "failed", "skipped"}
            and value["criterion"] == criterion
            and all(type(value[k]) is int and value[k] >= 0 for k in ("tests", "passed", "failed", "skipped"))
            and value["tests"] == value["passed"] + value["failed"] + value["skipped"])


def _candidate(value, expected):
    require(isinstance(value, dict) and set(value) == {"head", "artifact_sha256", "configuration_sha256"}
            and value["head"] == expected["head"]
            and all(SHA.fullmatch(str(value[k])) for k in ("artifact_sha256", "configuration_sha256")),
            "candidate artifact/configuration identity is invalid")


def _observation(value, candidate, plan):
    require(set(value) == set(candidate) | {"environment", "trial", "reset", "intervened", "writers_quiescent"}
            and all(value.get(k) == v for k, v in candidate.items())
            and value["environment"] == plan["environment"]["id"]
            and value["intervened"] is False and value["writers_quiescent"] is True
            and all(isinstance(value[k], str) and LABEL.fullmatch(value[k]) for k in ("trial", "reset")),
            "deployment identity, trial provenance or intervention status is invalid")


def _cleanup(value, head, backend):
    require(isinstance(value, dict) and set(value) == {"head", "backend", "owner_receipt_sha256", "writers_quiescent", "owned_resources_absent"}
            and value["head"] == head and value["backend"] == backend
            and SHA.fullmatch(str(value["owner_receipt_sha256"]))
            and value["writers_quiescent"] is True and value["owned_resources_absent"] is True,
            "exact backend-owned cleanup and writer quiescence are unproven")


def qualify(run_dir: Path, execution_id: str, plan: dict, *, stage="promoted"):
    require(stage in {"promoted", "aggregate"}, "unsupported qualification stage")
    guard = lock(run_dir / "aggregation/validation-lock") if stage == "aggregate" else nullcontext()
    with guard:
        return _qualify(run_dir, execution_id, plan, stage=stage)


def recover_cleanup(run_dir, candidate):
    """Reconcile one retained trial; never rerun assertions or invent PASS."""
    from agent_runtime import native_session_id
    from workflow_objectives import authorize_coordinator
    from workflow_uat import _events, outcome
    require(isinstance(candidate, dict) and set(candidate) == {"receipt", "sha256", "checkout"},
            "cleanup recovery needs the exact retained receipt and checkout")
    relative_path(candidate["receipt"])
    with lock(run_dir.parent):
        authorize_coordinator(run_dir, native_session_id())
        path = run_dir / candidate["receipt"]
        receipt = read(path)
        require(receipt.get("schema") == RECEIPT and digest(receipt) == candidate["sha256"]
                and path == run_dir / "qualification" / "OBJECTIVE" / receipt["stage"] / (receipt["id"] + ".json"),
                "cleanup recovery receipt changed or belongs to another owner")
        events = [event for event in _events(run_dir) if event["campaign"] == receipt["id"]]
        require(events and events[0]["status"] == "running", "campaign start evidence is missing")
        origin = events[0]
        require(origin["head"] == receipt["head"] and origin["objective_sha256"] == receipt["objective_sha256"]
                and origin["executions"] == receipt["executions"]
                and origin["obligation_basis"] == receipt["obligation_basis"]
                and origin["criteria"] == sorted(receipt["plan"]["criteria"]), "campaign identity changed")
        if events[-1]["cleanup_proven"] and events[-1]["status"] != "running":
            return outcome(run_dir, receipt["head"])
        project = Path(candidate["checkout"]).resolve()
        plan = receipt["plan"]
        require(_git(project, "rev-parse", "HEAD") == receipt["head"] and not _git(project, "status", "--porcelain")
                and digest(plan) == receipt["plan_sha256"]
                and read(run_dir / "uat" / ("plan-" + receipt["objective_sha256"] + ".json")) == plan,
                "cleanup needs the original clean candidate and frozen harness")
        lease = run_dir.parents[2] / "qualification-leases" / digest(plan["environment"]["resource"])
        with lock(lease):
            operation_path = lease / "operation.json"
            operation = read(operation_path)
            require(operation.get("campaign") == receipt["id"] and operation.get("run_dir") == str(run_dir)
                    and operation.get("plan_sha256") == digest(plan), "cleanup recovery reservation changed")
            require(operation.get("status") == "unknown" and operation.get("local_writers_quiescent") is True,
                    "original qualification writers are active or unproven; reconcile them before cleanup recovery")
            recovering = {**operation, "status": "recovering"}
            write(operation_path, recovering)
    # Only the durable exact recovery reservation spans this external wait.
    recovery_quiescent = False
    try:
        cleanup, proof = command(project, plan, "cleanup")
        recovery_quiescent = proof.get("local_group_quiescent") is True
        _cleanup(cleanup, receipt["head"], plan["backend"])
        require(proof.get("local_group_quiescent") is True, "cleanup writer quiescence is unproven")
    except BaseException as error:
        observation = getattr(error, "observation", getattr(error, "execution_evidence", {}))
        recovery_quiescent = recovery_quiescent or observation.get("local_group_quiescent") is True
        with lock(run_dir.parent), lock(lease):
            require(read(operation_path) == recovering, "cleanup recovery reservation changed")
            write(operation_path, {**operation, "status": "unknown", "cleanup_proven": False,
                                   "local_writers_quiescent": recovery_quiescent})
        raise
    with lock(run_dir.parent), lock(lease):
        require(read(operation_path) == recovering and digest(read(path)) == candidate["sha256"],
                "cleanup recovery identity changed during execution")
        write(operation_path, {**operation, "status": "quiescent", "cleanup_proven": True, "local_writers_quiescent": True})
        recovery = {"schema": "workflow/uat-cleanup-recovery-v1", "campaign": receipt["id"],
                    "original_receipt_sha256": candidate["sha256"], "cleanup": cleanup, "command": proof}
        recovery_path = run_dir / "uat/recovery" / (digest(recovery) + ".json")
        if not recovery_path.exists():
            write(recovery_path, recovery, exclusive=True)
        with lock(run_dir / "uat"):
            # Preserve the original scope and all previous failure evidence.
            unrecorded_failure = ((receipt.get("assertion_failed") or receipt["status"] == "failed")
                                  and not any(event["status"] == "failed" for event in events))
            event = {**origin, "sequence": len(_events(run_dir)) + 1,
                     "status": "failed" if unrecorded_failure else "incomplete",
                     "result_sequence": events[-1].get("result_sequence", events[-1]["sequence"]),
                     "reason": "Original campaign cleanup was verified; retained assertion results are unchanged.",
                     "cleanup_proven": True, "qualification": None,
                     "recovery": {"receipt": str(recovery_path.relative_to(run_dir)), "sha256": digest(recovery)}}
            write(run_dir / "uat/events" / (digest(event) + ".json"), event, exclusive=True)
        return outcome(run_dir, receipt["head"])


def _qualify(run_dir: Path, execution_id: str, plan: dict, *, stage):
    from workflow_uat import check_stage, context, software_readiness, readiness, record
    check_stage(run_dir, stage)
    policy = context(run_dir)[2]
    require(policy["requested"], "UAT is opt-in; no campaign was requested")
    require(execution_id == "OBJECTIVE", "UAT campaigns target the accepted objective or coherent milestone scope")
    software = software_readiness(run_dir)
    if software["missing"]:
        return {"status": "pending", "missing": software["missing"], "delivery_eligible": __import__("workflow_uat").outcome(run_dir)["delivery_eligible"]}
    expected = target(run_dir, execution_id, stage=stage)
    ready = readiness(run_dir, expected["head"])
    if ready["missing"]:
        record(run_dir, "pending", expected["head"], reason="Missing UAT prerequisites: " + ", ".join(ready["missing"]))
        return {"status": "pending", "missing": ready["missing"], "delivery_eligible": __import__("workflow_uat").outcome(run_dir, expected["head"])["delivery_eligible"]}
    from workflow_task_acceptance import final_snapshot
    # target() independently validates the program phase and absence of slices.
    phase_without_tasks = execution_id.startswith("PHASE:") and expected["kind"] == "external" and expected["basis_sha256"] is None
    task_acceptance = final_snapshot(run_dir, Path(expected["project"]), expected["head"], phase_without_tasks=phase_without_tasks)
    require(plan["backend"] == policy["backend"], "campaign backend differs from explicit accepted choice")
    frozen_plan(run_dir, execution_id, plan, expected)
    root = run_dir / "qualification" / execution_id.replace(":", "-") / stage
    identity = uuid.uuid4().hex
    path = root / (identity + ".json")
    receipt = {"schema": RECEIPT, "id": identity, "execution_id": execution_id, "status": "incomplete",
               "stage": stage, "target_identity": expected["target_identity"],
               "objective_sha256": expected["objective_sha256"], "inputs": expected["inputs"],
               "basis_sha256": expected["basis_sha256"], "head": expected["head"], "plan": plan,
               "obligation_basis": expected["obligation_basis"],
               "executions": ready["executions"],
               "task_acceptance": task_acceptance,
               "plan_sha256": digest(plan), "started_at": time.time(), "commands": [], "criteria": {}}
    # Global across runs/projects; serialize the identified shared target.
    lease = run_dir.parents[2] / "qualification-leases" / digest(plan["environment"]["resource"])
    operation_path = lease / "operation.json"
    intent = {"schema": "workflow/qualification-operation-v1", "campaign": identity,
              "run_dir": str(run_dir), "target_sha256": digest(receipt["target_identity"]),
              "objective_sha256": expected["objective_sha256"], "plan_sha256": digest(plan),
              "status": "running", "cleanup_proven": False, "local_writers_quiescent": False}

    def authority(sample=None):
        try:
            with lock(run_dir.parent):
                check_stage(run_dir, stage)
                require(accepted(run_dir)[1]["sha256"] == expected["objective_sha256"], "qualification authority changed")
                if read(operation_path) != intent:
                    raise WorkflowError("QUALIFICATION_BLOCKED", "qualification operation reservation changed")
            return "continue"
        except WorkflowError as error:
            if error.code == "WORKFLOW_BUSY":
                return "health_unknown"
            raise
        except (OSError, TimeoutError):
            return "health_unknown"

    last_command_quiescent = True
    def execute(action, criterion=None):
        nonlocal last_command_quiescent
        while authority() != "continue":
            time.sleep(plan["execution_policy"]["observation_interval_seconds"])
        last_command_quiescent = False
        try:
            value, proof = command(expected["project"], plan, action, criterion, control=authority)
            last_command_quiescent = proof.get("local_group_quiescent") is True
            return value, proof
        except BaseException as error:
            proof = getattr(error, "observation", getattr(error, "execution_evidence", {}))
            last_command_quiescent = proof.get("local_group_quiescent") is True
            raise

    failure = None
    # Durable reservation spans the external operation; the coordinator lock is
    # never held during that wait. A second run cannot acquire an unknown target.
    with lock(run_dir.parent), lock(lease):
        check_stage(run_dir, stage)
        require(accepted(run_dir)[1]["sha256"] == expected["objective_sha256"], "qualification authority changed")
        if operation_path.exists():
            previous = read(operation_path)
            require(previous.get("cleanup_proven") is True and previous.get("status") == "quiescent",
                    "prior qualification operation is active or has unknown effects; reconcile its exact reservation")
        write(operation_path, intent)
        write(path, receipt, exclusive=True)
        record(run_dir, "running", expected["head"], reason="Requested UAT campaign is executing.", cleanup_proven=False,
               campaign=identity, criteria=plan["criteria"], obligation_basis=expected["obligation_basis"])
    try:
        candidate, proof = execute("candidate")
        _candidate(candidate, expected)
        receipt.update(candidate=candidate, resource_lease=digest(plan["environment"]["resource"]))
        receipt["commands"].append(proof)
        before, proof = execute("observe")
        if isinstance(before.get("trial"), str) and LABEL.fullmatch(before["trial"]):
            receipt["trial_id"] = before["trial"]
            write(path, receipt)
        _observation(before, candidate, plan)
        receipt.update(before=before)
        receipt["commands"].append(proof)
        write(path, receipt)
        # Stage-specific indexes do not reset shared live-trial provenance.
        for old_path in root.parent.glob("*/*.json"):
            old = read(old_path)
            if old_path != path and old.get("trial_id", old.get("before", {}).get("trial")) == before["trial"]:
                require(old.get("status") == "passed", "a failed/intervened trial requires a fresh reset and trial")
        for criterion in plan["criteria"]:
            receipt["attempted_criterion"] = criterion
            write(path, receipt)
            value, proof = execute("criterion", criterion)
            receipt["commands"].append(proof)
            write(path, receipt)
            require(_criterion_result(value, criterion), "criterion result is malformed")
            if value["failed"] > 0:
                receipt["assertion_failed"] = True
            receipt["criteria"][criterion] = value
            write(path, receipt)
            require(value["tests"] > 0 and value["passed"] == value["tests"]
                    and value["failed"] == value["skipped"] == 0,
                    "mandatory criterion did not execute and pass completely")
            receipt.pop("attempted_criterion")
            write(path, receipt)
        after, proof = execute("observe")
        _observation(after, candidate, plan)
        require(after == before, "qualification target or trial changed during testing")
        receipt.update(after=after)
        receipt["commands"].append(proof)
        require(target(run_dir, execution_id, stage=stage) == expected, "accepted source/candidate changed during qualification")
        receipt["status"] = "passed"
    except BaseException as error:
        failure = error
        if hasattr(error, "execution_evidence"):
            receipt["commands"].append(error.execution_evidence)
        receipt["status"] = "failed" if receipt.get("assertion_failed") or getattr(error, "code", None) == "UAT_ASSERTION_FAILED" else "incomplete"
    finally:
        receipt["cleanup_proven"] = False
        cleanup_writer_quiescent = False
        try:
            # Cleanup authority survives a new dispatch fence. This action may
            # only reconcile resources already owned by this declared trial.
            cleanup, proof = command(expected["project"], plan, "cleanup")
            cleanup_writer_quiescent = proof.get("local_group_quiescent") is True
            _cleanup(cleanup, expected["head"], plan["backend"])
            receipt["cleanup"] = cleanup
            receipt["commands"].append(proof)
            cleanup_writer_quiescent = proof.get("local_group_quiescent") is True
            receipt["cleanup_proven"] = last_command_quiescent and cleanup_writer_quiescent
        except BaseException as error:
            cleanup_observation = getattr(error, "observation", getattr(error, "execution_evidence", {}))
            cleanup_writer_quiescent = cleanup_writer_quiescent or cleanup_observation.get("local_group_quiescent") is True
            failure = failure or error
            if receipt["status"] != "failed":
                receipt["status"] = "incomplete"
        receipt["local_writers_quiescent"] = last_command_quiescent and cleanup_writer_quiescent
        with lock(run_dir.parent), lock(lease):
            current_operation = read(operation_path)
            require(current_operation.get("campaign") == identity, "qualification reservation changed during cleanup")
            write(operation_path, {**intent, "status": "quiescent" if receipt["cleanup_proven"] else "unknown",
                                   "cleanup_proven": receipt["cleanup_proven"],
                                   "local_writers_quiescent": receipt["local_writers_quiescent"]})
        write(path, receipt)
    if failure is not None:
        record(run_dir, receipt["status"], expected["head"],
               reason="A UAT assertion failed." if receipt["status"] == "failed" else "UAT execution was incomplete; retain trial evidence.",
               cleanup_proven=receipt["cleanup_proven"], campaign=identity, criteria=plan["criteria"],
               obligation_basis=expected["obligation_basis"])
        raise failure
    with lock(run_dir.parent):
        try:
            check_stage(run_dir, stage)
            require(accepted(run_dir)[1]["sha256"] == expected["objective_sha256"]
                    and target(run_dir, execution_id, stage=stage) == expected,
                    "qualification authority or candidate changed before acceptance")
            operation = read(operation_path)
            require(operation.get("campaign") == identity and operation.get("status") == "quiescent"
                    and operation.get("cleanup_proven") is True,
                    "qualification reservation is not quiescent")
        except BaseException:
            receipt["status"] = "incomplete"
            write(path, receipt)
            record(run_dir, "incomplete", expected["head"], reason="Qualification authority changed before acceptance.",
                   cleanup_proven=receipt["cleanup_proven"], campaign=identity, criteria=plan["criteria"],
                   obligation_basis=expected["obligation_basis"])
            raise
        receipt["finished_at"] = time.time()
        write(path, receipt)
        with lock(root):
            index_path = root / "produced.json"
            index = read(index_path) if index_path.exists() else {"receipts": {}}
            index["receipts"][identity] = digest(receipt)
            write(index_path, index)
        reference = {"receipt": str(path.relative_to(run_dir)), "sha256": digest(receipt)}
        record(run_dir, "passed", expected["head"], reason="Requested UAT criteria executed successfully.", qualification=reference,
               campaign=identity, criteria=plan["criteria"], obligation_basis=expected["obligation_basis"])
        return {"status": "QUALIFIED", **reference}


def verify_receipt(run_dir, reference, *, current=True):
    require(isinstance(reference, dict) and set(reference) == {"receipt", "sha256"}, "invalid qualification reference")
    relative_path(reference["receipt"])
    require(reference["receipt"].startswith("qualification/"), "qualification reference escapes its owner")
    value = read(run_dir / reference["receipt"])
    require(digest(value) == reference["sha256"] and value.get("schema") == RECEIPT
            and value.get("status") == "passed" and value.get("stage") in {"promoted", "aggregate"}
            and isinstance(value.get("target_identity"), dict), "qualification is missing, changed or failed")
    require(Path(reference["receipt"]).parent == Path("qualification") / value["execution_id"].replace(":", "-") / value["stage"]
            and Path(reference["receipt"]).name == value["id"] + ".json"
            and read((run_dir / reference["receipt"]).parent / "produced.json")["receipts"].get(value["id"]) == reference["sha256"],
            "receipt was not admitted by the qualification producer")
    require(value["plan_sha256"] == digest(value["plan"]), "qualification plan changed")
    criteria = value["plan"]["criteria"]
    require(set(value["criteria"]) == set(criteria) and len(value["commands"]) == len(criteria) + 4,
            "executed qualification coverage is incomplete")
    _cleanup(value["cleanup"], value["head"], value["plan"]["backend"])
    require(value["cleanup_proven"] is True, "campaign cleanup is unproven")
    _candidate(value["candidate"], value)
    _observation(value["before"], value["candidate"], value["plan"])
    require(value["before"] == value["after"], "trial evidence changed")
    for criterion in criteria:
        result = value["criteria"][criterion]
        require(result["criterion"] == criterion and type(result["tests"]) is int and result["tests"] > 0
                and result["tests"] == result["passed"] and result["failed"] == result["skipped"] == 0,
                "criterion result is incomplete")
    require(all(c["exit_code"] == 0 and SHA.fullmatch(c["stdout_sha256"]) and SHA.fullmatch(c["stderr_sha256"])
                for c in value["commands"]), "execution receipts are invalid")
    require([(c["action"], c["criterion"]) for c in value["commands"]]
            == [("candidate", None), ("observe", None)] + [("criterion", c) for c in criteria] + [("observe", None), ("cleanup", None)],
            "qualification command sequence is invalid")
    from workflow_task_acceptance import verify_final_snapshot, repository
    verify_final_snapshot(run_dir, repository(run_dir), value["head"], value["task_acceptance"], current=current)
    if current:
        expected = target(run_dir, value["execution_id"], stage=value["stage"])
        frozen_plan(run_dir, value["execution_id"], value["plan"], expected, create=False)
        require(value["target_identity"] == expected["target_identity"]
                and value["head"] == expected["head"] and value["basis_sha256"] == expected["basis_sha256"]
                and value["objective_sha256"] == expected["objective_sha256"]
                and value["inputs"] == expected["inputs"]
                and value["obligation_basis"] == expected["obligation_basis"], "qualification basis is stale")
        # Verification is read-only: disposable targets may already be cleaned.
        # Candidate identity and the immutable producer receipt bind the observed trial.
    return value
