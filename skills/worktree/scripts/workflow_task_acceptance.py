"""SDLC task evidence and deferred obligations, separate from native results.

Only this producer executes checks. Hashes detect drift; they are not signatures
or a security boundary against an actor able to rewrite the private owner state.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import math
import uuid

from workflow_state import WorkflowError, lock, read as _read, write, git_environment
from workflow_validation import digest, _run_check

SCHEMA = "workflow/task-acceptance-policy-v3"
RECEIPT = "workflow/task-acceptance-receipt-v1"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")


def require(condition, message):
    if not condition:
        raise WorkflowError("TASK_ACCEPTANCE_INVALID", message)


def read(path):
    try:
        return _read(path)
    except (OSError, ValueError) as error:
        raise WorkflowError("TASK_ACCEPTANCE_INVALID", "required acceptance evidence is unavailable") from error


def policy_path(plan):
    return plan.with_suffix(plan.suffix + ".acceptance.json")


def validate(value, task_ids):
    require(isinstance(value, dict) and set(value) == {"schema", "tasks"}
            and value["schema"] == SCHEMA and isinstance(value["tasks"], dict)
            and set(value["tasks"]) == set(task_ids), "policy must cover every frozen task")
    identifiers = set()
    for task_id, task in value["tasks"].items():
        require(isinstance(task, dict) and set(task) == {"criteria", "requires"}
                and isinstance(task["criteria"], list) and task["criteria"]
                and isinstance(task["requires"], list), "task policy is incomplete")
        for criterion in task["criteria"]:
            require(isinstance(criterion, dict) and set(criterion) == {
                "id", "purpose", "disposition", "reason", "prerequisite", "resolution_stage", "check"
            }, "criterion fields are invalid")
            name = criterion["id"]
            require(isinstance(name, str) and ID.fullmatch(name) and name not in identifiers,
                    "criterion IDs must be unique within the execution")
            identifiers.add(name)
            require(criterion["purpose"] in {"implementation", "uat"}, "unknown criterion purpose")
            mode = criterion["disposition"]
            require(mode in {"check", "deferred", "not_applicable"}, "unknown disposition")
            require(isinstance(criterion["reason"], str) and 0 < len(criterion["reason"]) <= 1024,
                    "each disposition needs a bounded rationale")
            if mode == "deferred":
                require(isinstance(criterion["prerequisite"], str) and criterion["prerequisite"].strip()
                        and criterion["resolution_stage"] in {"before_consumer", "before_delivery", "uat"},
                        "deferred criteria must name a missing prerequisite and resolution stage")
            else:
                require(criterion["prerequisite"] is None and criterion["resolution_stage"] is None,
                        "only deferred criteria carry missing prerequisites")
            if mode == "not_applicable":
                require(criterion["check"] is None, "inapplicable criteria cannot hide checks")
                continue
            check = criterion["check"]
            require(isinstance(check, dict) and check.get("kind") in {"command", "container"},
                    "applicable criteria need an executable check")
            if check["kind"] == "command":
                from workflow_validation import validate_plan, SCHEMA as VALIDATION
                require(set(check) == {"kind", "definition"}, "command check fields are invalid")
                validate_plan({"schema": VALIDATION, "wave_checks": [check["definition"]],
                               "task_checks": {}}, [])
            else:
                from workflow_task_container import validate_check
                validate_check(check)
        require(len(task["requires"]) == len(set(task["requires"]))
                and all(isinstance(item, str) and ID.fullmatch(item) for item in task["requires"]),
                "evidence prerequisites are invalid")
    require(all(set(task["requires"]) <= identifiers for task in value["tasks"].values()),
            "evidence prerequisite references an unknown criterion")
    definitions = {c["id"]: c for row in value["tasks"].values() for c in row["criteria"]}
    require(all(definitions[c]["purpose"] == "implementation" for row in value["tasks"].values()
                for c in row["requires"]), "consumer correctness prerequisites must be implementation criteria")
    require(all((c["resolution_stage"] == "uat") == (c["purpose"] == "uat")
                for c in definitions.values() if c["disposition"] == "deferred"),
            "deferred resolution stage must match its purpose")
    from workflow_security import contains_sensitive
    import json
    require(not contains_sensitive(json.dumps(value)), "policy contains sensitive material")
    return value


def load_policy(run_dir, plan, task_ids):
    if read(run_dir / "run.json")["owner"] != "agentic-sdlc":
        return None
    path = policy_path(plan)
    require(path.is_file() and not path.is_symlink(), "mandatory SDLC task acceptance policy is missing")
    return validate(read(path), task_ids)


def assignment_policy(assignment):
    if assignment["owner"] != "agentic-sdlc":
        return None
    basis = read(Path(assignment["run_dir"]) / "bases" / assignment["execution_id"]
                 / (assignment["basis_sha256"] + ".json"))
    policy = basis.get("task_acceptance_policy")
    require(isinstance(policy, dict), "SDLC basis has no frozen acceptance policy")
    require(digest(policy) == basis.get("task_acceptance_policy_sha256"), "policy identity changed")
    validate(policy, list(policy["tasks"]))
    selected = policy["tasks"][assignment["task_id"]]
    require(assignment.get("task_acceptance") == selected, "assignment policy differs from frozen basis")
    return selected


def validate_replan(run_dir, execution_id, replacement):
    if replacement is None:
        return
    for path in (run_dir / "bases" / execution_id).glob("*.json"):
        old = read(path).get("task_acceptance_policy")
        require(isinstance(old, dict), "prior SDLC policy is missing")
        for task, policy in old["tasks"].items():
            if task in replacement["tasks"]:
                require(replacement["tasks"][task] == policy,
                        "replan cannot reinterpret an existing task's acceptance; add a corrective task")


def require_dependencies(run_dir, execution_id, coordinator, task, base_head):
    if coordinator["owner"] != "agentic-sdlc":
        return
    policy = load_policy(run_dir, Path(coordinator["plan_path"]), list(coordinator["task_waves"]))
    required = policy["tasks"][task.task_id]["requires"]
    if not required:
        return
    debt = obligations(run_dir)
    selected = {key: value for key, value in debt.items() if key in {execution_id + ":" + c for c in required}}
    if selected:
        tree = _git(Path(coordinator["git_root"]), ["rev-parse", base_head + "^{tree}"])
        # Subset receipts retain exact source and criterion identities.
        require_resolved(run_dir, selected, tree)
    from workflow_execution import assignment_path, result_path, _validate_result_record
    for criterion in required:
        owner, definition = next((name, c) for name, row in policy["tasks"].items()
                                 for c in row["criteria"] if c["id"] == criterion)
        require(definition["disposition"] != "not_applicable", "consumer requires an inapplicable criterion")
        require(owner in task.dependencies, "evidence consumers must declare their producer dependency")
        if definition["disposition"] == "deferred":
            continue
        wave = coordinator["task_waves"][owner]
        assignment = read(assignment_path(run_dir, execution_id, wave, owner))
        _validate_result_record(read(result_path(run_dir, execution_id, wave, owner)), assignment)


def _git(cwd, args, *, env=None):
    result = subprocess.run(["git", "-C", str(cwd), *args], env=env if env is not None else git_environment(cwd), capture_output=True, timeout=30)
    require(result.returncode == 0, "acceptance source snapshot is unavailable")
    return result.stdout.decode().strip()


def source_entries(root, tree):
    env = git_environment(root)
    listing = subprocess.run(["git", "-C", str(root), "ls-tree", "-rz", "--full-tree", tree],
                             env=env, capture_output=True, timeout=30)
    require(listing.returncode == 0, "source tree is unavailable")
    for row in listing.stdout.split(b"\0"):
        if not row:
            continue
        metadata, name = row.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        path = Path(os.fsdecode(name))
        require(mode in {"100644", "100755", "120000"} and kind == "blob"
                and not path.is_absolute() and ".." not in path.parts, "unsupported source mode or path")
        content = subprocess.run(["git", "-C", str(root), "cat-file", "blob", oid],
                                 env=env, capture_output=True, timeout=30)
        require(content.returncode == 0, "source object is unavailable")
        yield str(path), mode, content.stdout


def snapshot_tree(cwd):
    """Stage complete nonignored source in a private index, preserving the user's index."""
    from workflow_security import contains_sensitive
    listing = subprocess.run(["git", "-C", str(cwd), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             env=git_environment(cwd), capture_output=True, timeout=30)
    require(listing.returncode == 0, "cannot inspect source before snapshot")
    for name in set(listing.stdout.split(b"\0")) - {b""}:
        path = cwd / os.fsdecode(name)
        if path.is_file() and not path.is_symlink():
            require(not contains_sensitive(path.read_bytes().decode("utf-8", errors="replace")),
                    "source snapshot contains sensitive material")
    with tempfile.TemporaryDirectory(prefix="sdlc-acceptance-index-") as temporary:
        env = {**git_environment(cwd), "GIT_INDEX_FILE": str(Path(temporary) / "index")}
        _git(cwd, ["read-tree", "HEAD"], env=env)
        _git(cwd, ["add", "-A", "--", ":/"], env=env)
        tree = _git(cwd, ["write-tree"], env=env)
        for name, mode, contents in source_entries(cwd, tree):
            path = cwd / name
            actual = os.readlink(path).encode() if mode == "120000" else path.read_bytes()
            require(actual == contents, "Git filters transformed tested source bytes")
        return tree


def _root(run_dir):
    return run_dir / "task-acceptance"


def _task_key(assignment, attempt):
    return digest({"assignment": assignment["assignment_digest"], "attempt": attempt})


def _verify_produced(path):
    value = read(path)
    require(read(path.with_suffix(".produced.json")) == {"sha256": digest(value)},
            "acceptance receipt lacks matching owner production evidence")
    operation = read(path.parent.parent / "operations" / (value["operation"] + ".json"))
    require(operation.get("status") == "verified" and operation.get("receipt_sha256") == digest(value)
            and operation.get("target") == value["target"] and value["schema"] == RECEIPT,
            "acceptance operation is incomplete or changed")
    return value


def _publish(root, operation, value):
    from workflow_security import contains_sensitive
    import json
    require(not contains_sensitive(json.dumps(value)), "acceptance evidence contains sensitive material")
    path = root / "receipts" / (operation + ".json")
    write(path, value, exclusive=True)
    write(path.with_suffix(".produced.json"), {"sha256": digest(value)}, exclusive=True)
    write(root / "operations" / (operation + ".json"), {
        "status": "verified", "target": value["target"], "receipt_sha256": digest(value)})
    return path


def require_quiescent(run_dir, execution_id=None, *, wave_id=None, task_id=None):
    for path in (_root(run_dir) / "operations").glob("*.json"):
        row = read(path)
        target = row.get("target", {})
        if any(expected is not None and target.get(key) not in {None, expected}
               for key, expected in (("execution_id", execution_id), ("wave_id", wave_id), ("task_id", task_id))):
            continue
        require(row.get("status") == "verified" or (row.get("status") == "interrupted"
                and row.get("termination_proven") is True), "acceptance trial cleanup is not proven")


def _checks(criteria, cwd, tree, operation, *, assignment=None, session=None):
    results = []
    for criterion in criteria:
        check = criterion["check"]
        if check["kind"] == "command":
            try:
                result = _run_check(check["definition"], cwd)
            except WorkflowError as error:
                if error.code != "ENVIRONMENT_BLOCKER":
                    raise
                result = {"outcome": "failed", "reason": "evaluator_unavailable",
                          "local_group_quiescent": getattr(error, "observation", {}).get("local_group_quiescent") is True}
        else:
            from workflow_task_container import run
            result = run(check, cwd, tree, operation + "-" + str(len(results)),
                         assignment=assignment, session=session)
        results.append({"criterion": criterion["id"], "check_sha256": digest(check), "observation": result})
        # Do not transform a failed assertion into a deferred prerequisite.
        if result["outcome"] != "passed":
            break
    return results


def _passed(criteria, observations):
    valid = (len(criteria) == len(observations) and all(
        row["criterion"] == criterion["id"] and row["check_sha256"] == digest(criterion["check"])
        and row["observation"]["outcome"] == "passed"
        for criterion, row in zip(criteria, observations)))
    if not valid:
        return False
    for criterion, row in zip(criteria, observations):
        if criterion["check"]["kind"] == "container":
            from workflow_task_container import assess
            proof = row["observation"]
            require(proof.get("cleanup_proven") is True
                    and proof["collection"]["container_id"] != proof["execution"]["container_id"]
                    and proof["collection"]["process"]["outcome"] == "passed"
                    and proof["execution"]["process"]["outcome"] == "passed",
                    "container execution or cleanup evidence is incomplete")
            derived = assess(criterion["check"], proof["inventory"], proof["receipt"],
                             proof["input_sha256"], proof["operation"])
            require(derived == proof["assessment"] and derived["status"] == "PASS",
                    "container receipt assessment changed")
    return True


def _repair_owner():
    import sys
    scripts = Path(__file__).resolve().parents[2] / "sdlc-classify-failure/scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import repair_control
    return repair_control


def _complete_repair(value, path):
    if value.get("repair_dispatch"):
        _repair_owner().complete_remediation(
            path.parents[2], value["repair_execution"], value["repair_dispatch"],
            "succeeded" if value["outcome"] == "passed" else "failed_same_blocker",
            str(path.relative_to(path.parents[2])), active_seconds_delta=value["active_seconds"])


def _restore_reference(root, index):
    reference = read(index)
    if set(reference) == {"operation"}:
        operation = read(root / "operations" / (reference["operation"] + ".json"))
        if operation["status"] == "verified":
            path = root / "receipts" / (reference["operation"] + ".json")
            value = _verify_produced(path)
            _complete_repair(value, path)
            reference = {"path": str(path), "sha256": digest(value)}
            write(index, reference)
    return reference


def _interrupt(run_dir, operation, target, error, observations=None):
    proven = (getattr(error, "observation", {}).get("cleanup_proven") is True
              or getattr(error, "observation", {}).get("local_group_quiescent") is True
              or observations is not None and all(
                  row["observation"].get("local_group_quiescent") is True
                  or row["observation"].get("cleanup_proven") is True for row in observations))
    with lock(run_dir.parent):
        path = _root(run_dir) / "operations" / (operation + ".json")
        if read(path).get("status") == "running":
            write(path, {**read(path), "status": "interrupted", "target": target,
                         "termination_proven": proven})


def _recover_reservation(root, index):
    """Recover an interrupted prelaunch index write without executing a check."""
    if index.exists():
        return
    matches = [p for p in (root / "operations").glob("*.json")
               if str(index.relative_to(root)) in read(p).get("indices", [])]
    require(len(matches) <= 1, "missing reservation has ambiguous trial ownership")
    if matches:
        write(index, {"operation": matches[0].stem}, exclusive=True)


def _reserve(run_dir, operation, target, indices, repair=None):
    root = _root(run_dir)
    path = root / "operations" / (operation + ".json")
    # Publish ownership before consuming dispatch or index reservations. No
    # checks can start until all reservations have been durably recorded.
    write(path, {"status": "running", "target": target,
                 "indices": [str(p.relative_to(root)) for p in indices]}, exclusive=True)
    try:
        if repair:
            write(root / "repair-dispatches" / (repair["dispatch_id"] + ".json"), {"operation": operation}, exclusive=True)
        for index in indices:
            write(index, {"operation": operation})
    except BaseException as error:
        _interrupt(run_dir, operation, target, error, [])
        if repair:
            _repair_owner().complete_remediation(
                run_dir, repair["execution_id"], repair["dispatch_id"], "failed_same_blocker",
                "task-acceptance/operations/" + operation + ".json", active_seconds_delta=0)
        raise


def produce(assignment_path, session):
    """Run while the worker is running, before its native success result."""
    from workflow_resources import historical_assignment
    from workflow_execution import task_path, _load_coordinator, _assert_current_assignment
    from workflow_objectives import session_hash
    assignment = historical_assignment(assignment_path)
    policy = assignment_policy(assignment)
    require(policy is not None, "task acceptance belongs to SDLC")
    run_dir = Path(assignment["run_dir"])
    root = _root(run_dir)
    state_path = task_path(run_dir, assignment["execution_id"], assignment["wave_id"], assignment["task_id"])
    operation = uuid.uuid4().hex
    with lock(run_dir.parent):
        active = [row for p in (root / "operations").glob("*.json") for row in [read(p)]
                  if row.get("status") != "verified" and not (row.get("status") == "interrupted" and row.get("termination_proven") is True)]
        require(len(active) < 2 and all(row["target"].get("assignment_digest") != assignment["assignment_digest"] for row in active),
                "acceptance capacity is occupied; wait for owned trials to finish")
        state = read(state_path)
        require(state["status"] == "running" and state["worker_session_hash"] == session_hash(session),
                "acceptance requires the current running worker")
        coordinator = _load_coordinator(run_dir, assignment["execution_id"])
        _assert_current_assignment(run_dir, assignment["execution_id"], coordinator, assignment)
        cwd = Path(assignment["worktree"])
        tree = snapshot_tree(cwd)
        key = _task_key(assignment, state["attempt"])
        index = root / "tasks" / (key + ".json")
        _recover_reservation(root, index)
        if index.exists():
            prior = _restore_reference(root, index)
            if "path" in prior:
                verify_task(assignment, state["attempt"], tree)
                return {"receipt_path": prior["path"], "outcome": "passed", "status": "already-accepted"}
            raise WorkflowError("RECOVERY_REQUIRED", "this worker attempt was interrupted; use classified worker recovery")
        target = {"assignment_digest": assignment["assignment_digest"], "basis_sha256": assignment["basis_sha256"],
                  "attempt": state["attempt"], "tree": tree, "policy_sha256": digest(policy), "key": key,
                  **{k: assignment[k] for k in ("execution_id", "wave_id", "task_id")}}
        _reserve(run_dir, operation, target, [index])
    criteria = [c for c in policy["criteria"] if c["disposition"] == "check" and c["purpose"] == "implementation"]
    observations = None
    try:
        observations = _checks(criteria, Path(assignment["scope_cwd"]), tree, operation,
                               assignment=assignment_path, session=session)
        with lock(run_dir.parent):
            current = read(state_path)
            stable = (all(current.get(k) == state.get(k) for k in ("status", "attempt", "worker_session_hash"))
                      and snapshot_tree(cwd) == tree)
            value = {"schema": RECEIPT, "operation": operation, "target": target,
                     "observations": observations, "outcome": "passed" if stable and _passed(criteria, observations) else "failed"}
            path = _publish(root, operation, value)
            require(read(index) == {"operation": operation}, "attempt ownership changed")
            write(index, {"path": str(path), "sha256": digest(value)})
        return {"receipt_path": str(path), "outcome": value["outcome"]}
    except BaseException as error:
        _interrupt(run_dir, operation, target, error, observations)
        raise


def verify_task(assignment, attempt, tree):
    policy = assignment_policy(assignment)
    if policy is None:
        return None
    criteria = [c for c in policy["criteria"] if c["disposition"] == "check" and c["purpose"] == "implementation"]
    if not criteria:
        return digest({"policy": policy, "tree": tree, "dispositions_only": True})
    root = _root(Path(assignment["run_dir"]))
    key = _task_key(assignment, attempt)
    reference = read(root / "tasks" / (key + ".json"))
    require(set(reference) == {"path", "sha256"}, "task acceptance trial did not complete")
    path = Path(reference["path"])
    require(path.parent == root / "receipts", "task receipt escaped its owner")
    value = _verify_produced(path)
    require(value["target"] == {"assignment_digest": assignment["assignment_digest"],
            "basis_sha256": assignment["basis_sha256"], "attempt": attempt, "tree": tree,
            "policy_sha256": digest(policy), "key": key,
            **{k: assignment[k] for k in ("execution_id", "wave_id", "task_id")}} and reference["sha256"] == digest(value)
            and value["outcome"] == "passed" and _passed(criteria, value["observations"]),
            "task acceptance is failed, incomplete or bound to different source")
    for criterion, observation in zip(criteria, value["observations"]):
        if criterion["check"]["kind"] == "container":
            proof = observation["observation"]
            require(proof["input_sha256"] == digest({"tree": tree, "check": criterion["check"],
                                                   "scope": assignment["project_scope"]})
                    and proof["execution"]["image"] == criterion["check"]["image"]
                    and proof["collection"]["image"] == criterion["check"]["image"],
                    "container inputs differ from the accepted source and image")
    return digest(value)


def obligations(run_dir):
    """Union all frozen deferred obligations, including superseded executions."""
    expected = set()
    expected_plans = set()
    coordinators = {p.parent.name for p in (run_dir / "execution").glob("*/coordinator.json")}
    for execution in coordinators:
        # Replans can retire tasks before any assignment exists. Their existing
        # coordinator journal still names the prior plan whose debt must survive.
        journal = run_dir / "execution" / execution / "journals/coordinator.jsonl"
        require(journal.is_file() and not journal.is_symlink(), "required coordinator history is missing")
        try:
            events = [json.loads(line) for line in journal.read_text().splitlines() if line]
        except (OSError, ValueError) as error:
            raise WorkflowError("TASK_ACCEPTANCE_INVALID", "coordinator history is unreadable") from error
        require(events and all(isinstance(event, dict) for event in events), "coordinator history is invalid")
        for event in events:
            if event.get("event") not in {"future_replan_intent", "future_replanned"}:
                continue
            fields = ("old_digest", "new_digest") if event["event"] == "future_replanned" else ("old_digest",)
            for field in fields:
                sha = event.get(field)
                require(isinstance(sha, str) and SHA.fullmatch(sha), "historical plan identity is invalid")
                expected_plans.add((execution, sha))
        pointer = read(run_dir / "objective-impact/execution" / (execution + ".json"))
        sha = pointer.get("sha256")
        require(isinstance(sha, str) and SHA.fullmatch(sha)
                and pointer.get("schema") == "workflow/execution-basis-pointer-v2"
                and pointer.get("path") == f"bases/{execution}/{sha}.json", "required execution basis pointer changed")
        expected.add(run_dir / pointer["path"])
    for path in (run_dir / "execution").glob("*/assignments/*/*.json"):
        assignment = read(path)
        sha, execution = assignment.get("basis_sha256"), assignment.get("execution_id")
        require(isinstance(sha, str) and SHA.fullmatch(sha) and execution in coordinators,
                "assignment has no expected execution basis")
        expected.add(run_dir / "bases" / execution / (sha + ".json"))
    paths = set((run_dir / "bases").glob("*/*.json"))
    require(expected <= paths, "required historical execution basis is missing")
    found = {}
    plan_policies = {}
    for path in sorted(paths):
        basis = read(path)
        require(basis.get("owner") == "agentic-sdlc" and digest(basis) == path.stem
                and basis.get("execution_id") == path.parent.name,
                "historical SDLC basis identity changed")
        policy = basis.get("task_acceptance_policy")
        require(isinstance(policy, dict) and digest(policy) == basis.get("task_acceptance_policy_sha256"),
                "historical SDLC policy is missing or changed")
        plan_key = (basis["execution_id"], basis["plan_digest"])
        require(plan_key not in plan_policies or plan_policies[plan_key] == digest(policy),
                "the same historical plan changed its frozen acceptance policy")
        plan_policies[plan_key] = digest(policy)
        validate(policy, list(policy["tasks"]))
        for task in policy["tasks"].values():
            for criterion in task["criteria"]:
                if criterion["disposition"] != "deferred":
                    continue
                key = basis["execution_id"] + ":" + criterion["id"]
                require(key not in found or found[key] == criterion, "a deferred obligation was redefined")
                found[key] = criterion
    require(expected_plans <= plan_policies.keys(), "required historical plan basis is missing")
    return found


def repository(run_dir):
    run = read(run_dir / "run.json")
    if run.get("automatic_outer"):
        from workflow_projects import get
        project = get(run_dir)
        require(project is not None, "automatic project ownership is missing")
        return Path(project["source_root"])
    return Path(run["git_root"])


def require_final(run_dir, repository, head, *, phase_without_tasks=False):
    if read(run_dir / "run.json")["owner"] != "agentic-sdlc":
        return
    require(phase_without_tasks or any((run_dir / "execution").glob("*/coordinator.json")),
            "final acceptance needs prepared execution evidence")
    debt = {k: v for k, v in obligations(run_dir).items() if v["purpose"] == "implementation"}
    require_quiescent(run_dir)
    if not debt:
        return
    tree = _git(repository, ["rev-parse", head + "^{tree}"])
    require_resolved(run_dir, debt, tree)


def final_snapshot(run_dir, repository, head, *, phase_without_tasks=False):
    require_final(run_dir, repository, head, phase_without_tasks=phase_without_tasks)
    if read(run_dir / "run.json")["owner"] != "agentic-sdlc":
        return None
    debt = {k: v for k, v in obligations(run_dir).items() if v["purpose"] == "implementation"}
    return {"tree": _git(repository, ["rev-parse", head + "^{tree}"]),
            "criteria": sorted(debt), "sha256": digest(debt)}


def verify_final_snapshot(run_dir, repository, head, snapshot, *, current):
    if read(run_dir / "run.json")["owner"] != "agentic-sdlc":
        require(snapshot is None, "Implement cannot acquire SDLC acceptance policy")
        return
    debt = {k: v for k, v in obligations(run_dir).items() if v["purpose"] == "implementation"}
    require(isinstance(snapshot, dict) and set(snapshot) == {"tree", "criteria", "sha256"}
            and isinstance(snapshot["criteria"], list)
            and snapshot["criteria"] == sorted(set(snapshot["criteria"]))
            and set(snapshot["criteria"]) <= set(debt), "historical acceptance obligation identity changed")
    selected = {key: debt[key] for key in snapshot["criteria"]}
    require(snapshot["sha256"] == digest(selected)
            and snapshot["tree"] == _git(repository, ["rev-parse", head + "^{tree}"]),
            "historical acceptance tree or policy changed")
    require_resolved(run_dir, selected, snapshot["tree"])
    if current:
        require_quiescent(run_dir)
        require(selected == debt, "new deferred obligations require current qualification")


def require_resolved(run_dir, debt, tree):
    root = _root(run_dir)
    complete = obligations(run_dir)
    for key, criterion in debt.items():
        identity = {"tree": tree, "criterion": key, "sha256": digest(criterion)}
        index = root / "resolved" / (digest(identity) + ".json")
        if not index.exists():
            raise WorkflowError("EVIDENCE_PENDING", "deferred criterion requires evidence on the candidate tree")
        reference = read(index)
        require(set(reference) == {"path", "sha256"}, "resolution trial did not complete")
        path = Path(reference["path"])
        require(path.parent == root / "receipts", "resolution receipt escaped its owner")
        value = _verify_produced(path)
        target = value["target"]
        selected = {name: complete[name] for name in target["criteria"]}
        require(key in selected and target["tree"] == tree and target["obligations_sha256"] == digest(selected)
                and reference["sha256"] == digest(value) and value["outcome"] == "passed"
                and _passed(list(selected.values()), value["observations"]),
                "deferred obligations are not resolved on this exact candidate")


def resolve(run_dir, cwd, session, criterion_ids=None, repair_dispatch=None):
    """Coordinator runs the frozen deferred checks on a quiescent clean candidate."""
    from workflow_objectives import authorize_coordinator, _writers_quiescent
    from workflow_execution import clean, git_common_dir
    operation = uuid.uuid4().hex
    root = _root(run_dir)
    with lock(run_dir.parent):
        authorize_coordinator(run_dir, session)
        _writers_quiescent(run_dir)
        require_quiescent(run_dir)
        run_state = read(run_dir / "run.json")
        scope = Path(run_state["project_root"]).relative_to(Path(run_state["git_root"]))
        candidates = {Path(run_state["project_root"]).resolve()}
        for coordinator_file in (run_dir / "execution").glob("*/coordinator.json"):
            coordinator = read(coordinator_file)
            candidates.add((Path(coordinator["integration_worktree"]) / coordinator["project_scope"]).resolve())
        if run_state.get("automatic_outer"):
            from workflow_automatic import _candidate
            try:
                _, checkout, row = _candidate(run_dir)
                candidates.add((checkout / row["scope"]).resolve())
            except WorkflowError:
                pass  # No aggregate candidate exists until its owning transition.
        require(cwd == cwd.resolve() and cwd in candidates
                and cwd.relative_to(Path(_git(cwd, ["rev-parse", "--show-toplevel"]))) == scope,
                "resolution must use an exact registered candidate project directory")
        require(git_common_dir(cwd) == Path(run_state["git_common_dir"]) and clean(cwd),
                "resolution requires an exact clean workflow candidate")
        debt = obligations(run_dir)
        if criterion_ids is not None:
            require(set(criterion_ids) <= set(debt) and len(criterion_ids) == len(set(criterion_ids)), "unknown or duplicate deferred criterion")
            debt = {key: value for key, value in debt.items() if key in criterion_ids}
        require(debt, "no deferred obligations need resolution")
        tree = _git(cwd, ["rev-parse", "HEAD^{tree}"])
        pending, previous, failures = {}, {}, {}
        for key, criterion in debt.items():
            identity = {"tree": tree, "criterion": key, "sha256": digest(criterion)}
            index = root / "resolved" / (digest(identity) + ".json")
            _recover_reservation(root, index)
            if index.exists():
                reference = _restore_reference(root, index)
                try:
                    require_resolved(run_dir, {key: criterion}, tree)
                except WorkflowError:
                    if "path" in reference:
                        prior = _verify_produced(Path(reference["path"]))
                        require(prior["outcome"] == "failed", "changed passing evidence cannot authorize retry")
                    else:
                        prior = read(root / "operations" / (reference["operation"] + ".json"))
                        require(prior["status"] == "interrupted" and prior.get("termination_proven") is True,
                                "prior acceptance cleanup is unproven")
                    failures[key] = digest(prior)
                    previous[key] = reference
                else:
                    continue
            pending[key] = criterion
        if not pending:
            return {"outcome": "passed", "status": "already-resolved"}
        repair = None
        if failures:
            require(repair_dispatch is not None and len(set(failures.values())) == 1,
                    "retry each failed trial through its classified repair dispatch")
            execution = repair_dispatch.parent.parent.name
            require(repair_dispatch == run_dir / "repairs" / execution / "dispatches" / repair_dispatch.name
                    and all(key.startswith(execution + ":") for key in failures), "repair dispatch belongs to another execution")
            repair = _repair_owner().acceptance_retry_authority(run_dir, execution, repair_dispatch.stem, next(iter(failures.values())))
            require(not (root / "repair-dispatches" / (repair["dispatch_id"] + ".json")).exists(),
                    "repair dispatch was already consumed")
        else:
            require(repair_dispatch is None, "repair dispatch is only for an existing failed trial")
        debt = pending
        target = {"tree": tree, "criteria": list(debt), "obligations_sha256": digest(debt), "project_scope": str(scope)}
        indices = []
        for key, criterion in debt.items():
            identity = {"tree": tree, "criterion": key, "sha256": digest(criterion)}
            indices.append(root / "resolved" / (digest(identity) + ".json"))
        _reserve(run_dir, operation, target, indices, {**repair, "execution_id": execution} if repair else None)
    started = time.monotonic()
    value = None
    observations = None
    try:
        observations = _checks(list(debt.values()), cwd, tree, operation)
        with lock(run_dir.parent):
            authorize_coordinator(run_dir, session)
            require(not any(read(p).get("active_task_ids") for p in (run_dir / "execution").glob("*/waves/*.json")),
                    "writers appeared during resolution")
            require(clean(cwd) and tree == _git(cwd, ["rev-parse", "HEAD^{tree}"])
                    and all(obligations(run_dir).get(k) == v for k, v in debt.items()), "resolution source or obligations changed")
            value = {"schema": RECEIPT, "operation": operation, "target": target, "observations": observations,
                     "previous_trials": previous, "repair_dispatch": repair["dispatch_id"] if repair else None,
                     "repair_execution": execution if repair else None, "active_seconds": math.ceil(time.monotonic() - started),
                     "outcome": "passed" if _passed(list(debt.values()), observations) else "failed"}
            path = _publish(root, operation, value)
            _complete_repair(value, path)
            for key, criterion in debt.items():
                identity = {"tree": tree, "criterion": key, "sha256": digest(criterion)}
                index = root / "resolved" / (digest(identity) + ".json")
                require(read(index) == {"operation": operation}, "resolution ownership changed")
                write(index, {"path": str(path), "sha256": digest(value)})
        return {"receipt_path": str(path), "outcome": value["outcome"]}
    except BaseException as error:
        _interrupt(run_dir, operation, target, error, observations)
        if repair and read(root / "operations" / (operation + ".json"))["status"] == "interrupted":
            _repair_owner().complete_remediation(
                run_dir, execution, repair["dispatch_id"], "failed_same_blocker",
                "task-acceptance/operations/" + operation + ".json",
                active_seconds_delta=math.ceil(time.monotonic() - started))
        raise


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Private read-only SDLC acceptance gate")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    try:
        require_final(args.run_dir, args.repository, args.head)
        from workflow_uat import require_delivery
        print(json.dumps(require_delivery(args.run_dir, args.head), sort_keys=True))
    except (WorkflowError, OSError, ValueError, KeyError):
        raise SystemExit("SDLC task acceptance or deferred obligations are unverified") from None
