"""Source-bound implementation admission, independent of optional runtime UAT."""
import time
import uuid

from workflow_state import WorkflowError, digest, lock, read, write
from workflow_objectives import _git
from workflow_qualification import require, target

SCHEMA = "workflow/delivery-admission-v1"


def _proof(run_dir, execution_id):
    import workflow_execution as engine
    coordinator = engine._load_coordinator(run_dir, execution_id)
    require(coordinator.get("status") in {"promoted", "cleanup", "done"}
            and coordinator.get("final_evidence") and coordinator.get("promoted_head"),
            "delivery requires a sealed and promoted implementation")
    require(coordinator.get("wave_ids") and all(engine._wave_satisfied(run_dir, execution_id, coordinator, wave)
            for wave in coordinator["wave_ids"]), "required implementation checks are incomplete")
    return {"promoted_head": coordinator["promoted_head"], "final_evidence": coordinator["final_evidence"],
            "waves": {wave: digest(read(engine.wave_path(run_dir, execution_id, wave)))
                      for wave in coordinator["wave_ids"]}}


def implementation_criteria(run_dir, expected):
    # Campaign selection never reclassifies implementation requirements. Task
    # IDs are execution-local and cannot classify source IDs in another slice.
    optional = set()
    from workflow_program import accepted
    inputs = accepted(run_dir)[2]["inputs"]
    if inputs is not None:
        program = read(run_dir / "program" / (inputs["program_sha256"] + ".json"))
        require(digest(program) == inputs["program_sha256"], "program UAT catalog changed")
        optional.update(program["uat_criteria"])
    return sorted(set(expected["criteria"]) - optional)


def admit(run_dir, execution_id, checks=None):
    from agent_runtime import native_session_id
    from workflow_objectives import authorize_coordinator, check_dispatch
    from workflow_task_acceptance import final_snapshot
    from workflow_uat import context, require_delivery
    from workflow_validation import validate_plan, _run_check, load_plan, require_quiescent, SCHEMA as VALIDATION
    session = native_session_id()
    with lock(run_dir.parent):
        run, _ = authorize_coordinator(run_dir, session)
        check_dispatch(run_dir)
        require_quiescent(run_dir)
        expected = target(run_dir, execution_id)
        criteria = implementation_criteria(run_dir, expected)
        if execution_id.startswith("PHASE:"):
            require(isinstance(checks, list) and {c.get("id") for c in checks} == set(criteria),
                    "phase-only implementation criteria need explicit executable checks")
            if checks:
                validate_plan({"schema": VALIDATION, "wave_checks": checks, "task_checks": {}}, [])
            frozen = run_dir / "delivery-admission" / execution_id / (expected["objective_sha256"] + ".plan.json")
            definition = {"checks": checks}
            if frozen.exists():
                require(read(frozen) == definition, "phase checks changed under the accepted objective")
            else:
                write(frozen, definition, exclusive=True)
            coverage = {criterion: [criterion] for criterion in criteria}
            proof = {"phase": execution_id}
        else:
            require(checks is None, "slice checks are owned by implementation execution")
            proof = _proof(run_dir, execution_id)
            checks = load_plan(run_dir, execution_id)["wave_checks"]
            coverage = expected["definition"]["implementation_coverage"]
        require(set(coverage) == set(criteria)
                and all(isinstance(ids, list) and ids and len(ids) == len(set(ids))
                        and set(ids) <= {check["id"] for check in checks} for ids in coverage.values()),
                "every implementation criterion must map to frozen required checks")
        task_acceptance = final_snapshot(run_dir, expected["project"], expected["head"],
                                         phase_without_tasks=execution_id.startswith("PHASE:"))
        operation = run_dir / "delivery-admission/operations" / (uuid.uuid4().hex + ".json")
        intent = {"schema": "workflow/delivery-admission-operation-v1", "status": "running",
                  "target": {"execution_id": execution_id, "head": expected["head"],
                             "objective_sha256": expected["objective_sha256"]},
                  "checks_sha256": digest(checks), "coordinator_session_sha256": run["coordinator_session_sha256"]}
        write(operation, intent, exclusive=True)

    results = []
    writer_quiescent = True
    def authority(sample=None):
        try:
            with lock(run_dir.parent):
                current, _ = authorize_coordinator(run_dir, session)
                check_dispatch(run_dir)
                require(current["coordinator_session_sha256"] == intent["coordinator_session_sha256"]
                        and read(operation) == intent, "delivery evaluator authority changed")
            return "continue"
        except WorkflowError as error:
            if error.code == "WORKFLOW_BUSY":
                return "health_unknown"
            raise
        except (OSError, TimeoutError):
            return "health_unknown"
    try:
        for check in checks:
            while authority() != "continue":
                time.sleep(check["execution_policy"]["observation_interval_seconds"])
            writer_quiescent = False
            try:
                results.append(_run_check(check, expected["project"], control=authority))
                writer_quiescent = results[-1].get("local_group_quiescent") is True
            except BaseException as error:
                writer_quiescent = getattr(error, "observation", {}).get("local_group_quiescent") is True
                raise
            if results[-1]["outcome"] != "passed":
                break
        with lock(run_dir.parent):
            authorize_coordinator(run_dir, session)
            check_dispatch(run_dir)
            require(read(operation) == intent, "delivery evaluator reservation changed")
            require(target(run_dir, execution_id) == expected, "implementation candidate changed")
            require(final_snapshot(run_dir, expected["project"], expected["head"],
                                   phase_without_tasks=execution_id.startswith("PHASE:")) == task_acceptance,
                    "implementation acceptance changed")
            if not execution_id.startswith("PHASE:"):
                require(_proof(run_dir, execution_id) == proof
                        and load_plan(run_dir, execution_id)["wave_checks"] == checks,
                        "implementation proof or checks changed")
            else:
                require(read(frozen) == definition, "phase checks changed during evaluation")
            validation = {"checks": checks, "coverage": coverage, "outcomes": results}
            attempt = {"head": expected["head"], "objective_sha256": expected["objective_sha256"],
                       "validation": validation}
            attempt_path = run_dir / "delivery-admission" / execution_id / "attempts" / (digest(attempt) + ".json")
            if not attempt_path.exists():
                write(attempt_path, attempt, exclusive=True)
            require(writer_quiescent and len(results) == len(checks)
                    and all(row["outcome"] == "passed" for row in results), "final implementation checks failed")
            policy = context(run_dir)[2]
            value = {"schema": SCHEMA, "execution_id": execution_id,
                     "head": expected["head"], "tree": _git(expected["project"], "rev-parse", "HEAD^{tree}"),
                     "objective_sha256": expected["objective_sha256"], "basis_sha256": expected["basis_sha256"],
                     "inputs": expected["inputs"], "obligation_basis": expected["obligation_basis"],
                     "criteria": criteria, "implementation": proof, "validation": validation,
                     "task_acceptance": task_acceptance,
                     "uat_policy": policy, "uat": require_delivery(run_dir, expected["head"])}
            fingerprint = digest(value)
            path = run_dir / "delivery-admission" / execution_id / (fingerprint + ".json")
            index = path.with_suffix(".produced.json")
            if path.exists():
                require(read(path) == value and read(index) == {"sha256": fingerprint}, "admission changed")
            else:
                write(path, value, exclusive=True)
                write(index, {"sha256": fingerprint}, exclusive=True)
            write(operation, {**intent, "status": "verified", "termination_proven": True,
                              "receipt_sha256": fingerprint, "outcomes": results})
            return {"receipt": str(path.relative_to(run_dir)), "sha256": fingerprint}
    except BaseException as error:
        with lock(run_dir.parent):
            if read(operation).get("status") == "running":
                write(operation, {**intent, "status": "interrupted" if writer_quiescent else "termination-unproven",
                                  "termination_proven": writer_quiescent, "outcomes": results,
                                  "interrupted_observation": getattr(error, "observation", None),
                                  "failure_code": getattr(error, "code", "VALIDATION_INTERRUPTED")})
        raise


def verify_receipt(run_dir, reference, *, current=True):
    from workflow_validation import digest as check_digest
    from project_specs_lib.source_bundle import relative_path
    from workflow_task_acceptance import verify_final_snapshot, repository
    from workflow_uat import context, require_delivery
    require(isinstance(reference, dict) and set(reference) == {"receipt", "sha256"}, "invalid delivery admission reference")
    relative_path(reference["receipt"])
    path = run_dir / reference["receipt"]
    value = read(path)
    require(value.get("schema") == SCHEMA and digest(value) == reference["sha256"]
            and path == run_dir / "delivery-admission" / value["execution_id"] / (reference["sha256"] + ".json")
            and read(path.with_suffix(".produced.json")) == {"sha256": reference["sha256"]},
            "delivery admission was not produced or changed")
    if value["execution_id"].startswith("PHASE:"):
        checks = read(path.parent / (value["objective_sha256"] + ".plan.json"))["checks"]
        require(value["implementation"] == {"phase": value["execution_id"]}, "phase evidence changed")
    else:
        require(value["implementation"] == _proof(run_dir, value["execution_id"]), "implementation evidence changed")
        from workflow_validation import load_plan
        checks = load_plan(run_dir, value["execution_id"])["wave_checks"]
    validation = value["validation"]
    require(validation["checks"] == checks and len(validation["outcomes"]) == len(checks)
            and set(validation["coverage"]) == set(value["criteria"])
            and all(ids and set(ids) <= {check["id"] for check in checks} for ids in validation["coverage"].values())
            and all(row["outcome"] == "passed" and row["exit_code"] == 0
                    and row.get("local_group_quiescent") is True and row.get("output_complete") is True
                    and row["id"] == check["id"] and row["check_sha256"] == check_digest(check)
                    for check, row in zip(checks, validation["outcomes"])), "final implementation evidence changed")
    require(value["uat"]["delivery_eligible"] is True and value["uat"]["status"] in {"not_requested", "pending", "passed", "incomplete"}
            and value["uat"]["policy_sha256"] == digest(value["uat_policy"]), "delivery admission misrepresents UAT")
    verify_final_snapshot(run_dir, repository(run_dir), value["head"], value["task_acceptance"], current=current)
    if current:
        expected = target(run_dir, value["execution_id"])
        require(all(value[k] == expected[k] for k in ("head", "objective_sha256", "basis_sha256", "inputs", "obligation_basis"))
                and value["tree"] == _git(expected["project"], "rev-parse", "HEAD^{tree}")
                and value["criteria"] == implementation_criteria(run_dir, expected)
                and value["uat_policy"] == context(run_dir)[2], "delivery admission is stale")
        coverage = ({criterion: [criterion] for criterion in value["criteria"]}
                    if value["execution_id"].startswith("PHASE:") else expected["definition"]["implementation_coverage"])
        require(validation["coverage"] == coverage, "implementation coverage changed")
        require_delivery(run_dir, expected["head"])
    return value
