"""Bind coordinator reviews separately from executed aggregate acceptance.

Review reports are coordinator attestations, never synthetic executed tests.
Program qualification remains with its existing executable-harness owner.
"""
from pathlib import Path
import re

from workflow_state import WorkflowError, digest, read, write

GATES = {"alignment", "uat", "docs"}
SHA = re.compile(r"[0-9a-f]{64}\Z")


def require(value, message):
    if not value:
        raise WorkflowError("AGGREGATION_GATES_INVALID", message)


def report(run_dir, reference, head, gate, *, source_read_only, historical=False):
    require(isinstance(reference, dict) and set(reference) == {"path", "sha256"}, "invalid gate reference")
    relative = Path(reference["path"])
    require(not relative.is_absolute() and ".." not in relative.parts
            and relative.parts[:2] == ("aggregation", "reports")
            and SHA.fullmatch(str(reference["sha256"])), "gate report must remain in its private owner")
    value = read(run_dir / relative)
    require(digest(value) == reference["sha256"] and set(value) == {
        "schema", "gate", "head", "outcome", "source_read_only", "evidence"
    } and value["schema"] == "workflow/gate-review-v2" and value["gate"] == gate
        and value["head"] == head and (value["outcome"] in ({"not_requested", "pending", "running", "passed", "failed", "incomplete"}
                                            if gate == "uat" else {"passed", "failed"}))
        and type(value["source_read_only"]) is bool
        and (not source_read_only or value["source_read_only"])
        and isinstance(value["evidence"], list) and 0 < len(value["evidence"]) <= 64
        and all(isinstance(item, str) and 0 < len(item) <= 512 for item in value["evidence"]),
        "gate report is stale, changed, incomplete or permits candidate source edits")
    if gate == "uat" and not historical:
        from workflow_uat import outcome
        current = outcome(run_dir, head)["status"]
        require(value["outcome"] == current or (value["outcome"] in {"pending", "incomplete"} and current == "passed"),
                "UAT review differs from its campaign owner")
    return value


def eligible(reports):
    return all(row["outcome"] in ({"not_requested", "pending", "passed", "incomplete"}
               if gate == "uat" else {"passed"}) for gate, row in reports.items())


def required_slices(run_dir):
    from workflow_program import accepted, verify_inputs
    run, _, _ = accepted(run_dir)
    program = verify_inputs(run_dir, Path(run["project_root"]))
    if program is None:
        return []
    executions = {p.parent.name for p in (run_dir / "execution").glob("*/coordinator.json")}
    return sorted(row["id"] for row in program["definition"]["slices"] if row["id"] in executions)


def verify(run_dir, value, target, *, historical=False):
    require(set(value) == {"schema", "target", "inner", "aggregate", "qualifications", "required_slices", "outcome", "task_acceptance"}
            and value["schema"] == "workflow/aggregation-gates-v2" and value["target"] == target,
            "aggregate gate identity changed")
    from workflow_task_acceptance import verify_final_snapshot, repository
    require(isinstance(value["task_acceptance"], dict) and set(value["task_acceptance"]) == {"inner", "aggregate"},
            "final acceptance snapshots are missing")
    for stage, head in (("inner", target["child_head"]), ("aggregate", target["head"])):
        verify_final_snapshot(run_dir, repository(run_dir), head, value["task_acceptance"][stage], current=not historical)
    reports = {}
    for stage, head in (("inner", target["child_head"]), ("aggregate", target["head"])):
        require(isinstance(value[stage], dict) and set(value[stage]) == GATES, "all three gate reviews are required")
        reports[stage] = {gate: report(run_dir, ref, head, gate, source_read_only=stage == "aggregate", historical=historical)
                          for gate, ref in value[stage].items()}
    require(eligible(reports["inner"]), "inner final gates did not pass")
    outcome = "passed" if eligible(reports["aggregate"]) else "failed"
    require(value["outcome"] == outcome, "aggregate review result changed")
    if not historical:
        from workflow_uat import require_delivery
        require_delivery(run_dir, target["head"])
    expected = value["required_slices"] if historical else required_slices(run_dir)
    require(isinstance(expected, list) and expected == sorted(set(expected))
            and value["required_slices"] == expected, "required qualification coverage changed")
    require(isinstance(value["qualifications"], list), "qualification references must be a list")
    from workflow_qualification import verify_receipt
    observed = []
    for reference in value["qualifications"]:
        receipt = verify_receipt(run_dir, reference, current=not historical)
        require(receipt["stage"] == "aggregate" and receipt["target_identity"] == target,
                "inner qualification cannot certify aggregate acceptance")
        observed.append(receipt["execution_id"])
    require(observed in ([], ["OBJECTIVE"]), "aggregate campaign must cover the accepted ready scope")
    require((reports["aggregate"]["uat"]["outcome"] == "passed") == (observed == ["OBJECTIVE"]),
            "UAT PASS requires its executed aggregate campaign")
    return reports


def record(run_dir, session, candidate):
    from workflow_automatic import _aggregation_transition, _candidate, _frozen_bases
    from workflow_objectives import authorize_coordinator

    def locked(run_dir):
        authorize_coordinator(run_dir, session)
        target, _, _ = _candidate(run_dir)
        _frozen_bases(run_dir)
        require(isinstance(candidate, dict) and set(candidate) == {"target", "inner", "aggregate", "qualifications"}
                and candidate["target"] == target and isinstance(candidate["aggregate"], dict)
                and set(candidate["aggregate"]) == GATES, "gate input must name the current aggregate candidate and all reviews")
        value = {"schema": "workflow/aggregation-gates-v2", **candidate,
                 "required_slices": required_slices(run_dir)}
        from workflow_task_acceptance import final_snapshot, repository
        value["task_acceptance"] = {stage: final_snapshot(run_dir, repository(run_dir), head)
                                    for stage, head in (("inner", target["child_head"]), ("aggregate", target["head"]))}
        reports = {gate: report(run_dir, ref, target["head"], gate, source_read_only=True)
                   for gate, ref in candidate["aggregate"].items()}
        value["outcome"] = "passed" if eligible(reports) else "failed"
        verify(run_dir, value, target)
        require(_candidate(run_dir)[0] == target, "candidate changed during final review admission")
        path = run_dir / "aggregation/gates" / (digest(target) + ".json")
        failure = path.with_suffix(".failure.json")
        if failure.exists():
            require(path.exists(), "failed observation lost its original review")
            verify(run_dir, admitted(path), target, historical=True)
            path = failure
        elif path.exists() and read(path) != value:
            prior = admitted(path)
            verify(run_dir, prior, target, historical=True)
            require(prior["outcome"] == "passed" and value["outcome"] == "failed"
                    and prior["inner"] == value["inner"],
                    "only a later failed observation may follow a passed review")
            path = failure
        if path.exists():
            require(read(path) == value, "prior final-gate result is immutable; retain the failed trial")
        else:
            write(path, value, exclusive=True)
        index = path.with_suffix(".produced.json")
        if index.exists():
            require(read(index) == {"sha256": digest(value)}, "final-gate producer index changed")
        else:
            write(index, {"sha256": digest(value)}, exclusive=True)
        return value

    # This return value is an immutable evidence record, not a CLI envelope.
    return _aggregation_transition(locked, include_delivery=False)(run_dir)


def admitted(path):
    value = read(path)
    require(read(path.with_suffix(".produced.json")) == {"sha256": digest(value)}, "final gates were not owner-admitted")
    return value


def load(run_dir, target, *, historical=False):
    path = run_dir / "aggregation/gates" / (digest(target) + ".json")
    value = admitted(path)
    failure = path.with_suffix(".failure.json")
    if failure.exists():
        verify(run_dir, value, target, historical=True)
        failed = admitted(failure)
        require(value["outcome"] == "passed" and failed["outcome"] == "failed"
                and value["inner"] == failed["inner"], "failed observation changed its original review")
        value = failed
    verify(run_dir, value, target, historical=historical)
    return value
