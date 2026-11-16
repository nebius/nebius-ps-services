"""Optional UAT intent, readiness and retained outcomes; never a test producer.

Readiness is a coordinator declaration, not proof of a passing product. Only the
qualification owner may attach executed evidence. Missing prerequisites do not
grant authority to provision resources or weaken required development checks.
"""
from pathlib import Path
import re

from workflow_state import WorkflowError, digest, lock, read, write

SCHEMA = "workflow/uat-policy-v1"
EVENT_SCHEMA = "workflow/uat-event-v2"
STATES = {"not_requested", "pending", "running", "passed", "failed", "incomplete"}
BACKENDS = {"existing", "container", "nebius"}
PREREQUISITES = {"environment", "access", "test_data", "scenarios", "smoke"}


def require(condition, message):
    if not condition:
        raise WorkflowError("UAT_INVALID", message)


def normalize(value=None):
    if value is None:
        value = {"requested": False, "backend": None,
                 "scope": {"kind": "objective", "executions": []}, "criteria": []}
    require(isinstance(value, dict) and set(value) in (
        {"requested", "backend", "scope", "criteria"},
        {"schema", "requested", "backend", "scope", "criteria"}), "invalid UAT policy fields")
    require(value.get("schema", SCHEMA) == SCHEMA and type(value["requested"]) is bool,
            "unsupported UAT policy")
    scope = value["scope"]
    require(isinstance(scope, dict) and set(scope) == {"kind", "executions"}
            and isinstance(scope["kind"], str) and scope["kind"] in {"objective", "milestone"}
            and isinstance(scope["executions"], list)
            and all(isinstance(v, str) and re.fullmatch(r"(?:FEAT|SLICE)-[0-9]{3,}", v)
                    for v in scope["executions"])
            and len(scope["executions"]) == len(set(scope["executions"]))
            and bool(scope["executions"]) == (scope["kind"] == "milestone"), "invalid UAT scope")
    require(isinstance(value["criteria"], list) and len(value["criteria"]) <= 256
            and all(isinstance(v, str) and 0 < len(v) <= 1024 for v in value["criteria"])
            and len(value["criteria"]) == len(set(value["criteria"])), "invalid UAT criteria")
    require((value["requested"] and isinstance(value["backend"], str) and value["backend"] in BACKENDS)
            or (not value["requested"] and value["backend"] is None
                and scope["kind"] == "objective" and not value["criteria"]),
            "UAT backend selection requires explicit accepted intent")
    return {"schema": SCHEMA, **{k: value[k] for k in ("requested", "backend", "scope", "criteria")}}


def context(run_dir):
    from workflow_program import accepted
    run, latest, snapshot = accepted(run_dir)
    require("uat" in snapshot, "unsupported frozen objective; preserve it and start a new run")
    policy = normalize(snapshot["uat"])
    return run, latest, policy


def scope_criteria(snapshot, policy, definition=None):
    """Resolve accepted criterion IDs without requiring an executable target."""
    if policy["criteria"]:
        return policy["criteria"]
    if definition is None:
        return snapshot["acceptance_criteria"]
    objective = policy["scope"]["kind"] == "objective"
    selected = set(policy["scope"]["executions"])
    coverage = {c for row in definition["slices"] if objective or row["id"] in selected
                for c in row["criteria"] + row["regression_criteria"]}
    if objective:
        coverage.update(c for phase in definition["phases"] for c in phase["exit_criteria"])
    return sorted(coverage)


def check_stage(run_dir, stage):
    """Allow the final candidate owner, without reopening worker dispatch."""
    from workflow_objectives import check_dispatch, validate_run, TERMINAL
    if stage != "aggregate":
        return check_dispatch(run_dir)
    run, objective = validate_run(run_dir)
    require(not run.get("fence") and run["status"] not in TERMINAL, "campaign authority is fenced")
    if run.get("shared_admission"):
        from workflow_projects import get
        entry = get(run_dir)
        require(entry is not None and entry["state"] == "aggregation-ready",
                "aggregate campaign requires the admitted final candidate")
    from workflow_automatic import _candidate
    _candidate(run_dir)
    return run, objective


def software_readiness(run_dir):
    run, latest, policy = context(run_dir)
    rows = {p.parent.name: read(p) for p in (run_dir / "execution").glob("*/coordinator.json")}
    selected = set(policy["scope"]["executions"])
    if policy["scope"]["kind"] == "objective":
        from workflow_program import verify_inputs
        from workflow_impact import spec_checkout
        program = verify_inputs(run_dir, spec_checkout(run_dir, Path(run["project_root"])))
        selected = {s["id"] for s in program["definition"]["slices"]} if program else set(rows)
        if program:
            selected.update(name for name in rows if name.startswith("FEAT-"))
    missing = []
    if not selected:
        missing.append("implemented_scope")
    if any(name not in rows or rows[name].get("status") not in {"promoted", "cleanup", "done"}
           or not rows[name].get("promoted_head") for name in selected):
        missing.append("implemented_scope")
    if any(row.get("active_wave") for row in rows.values()):
        missing.append("active_writers")
    return {"objective_sha256": latest["sha256"], "policy_sha256": digest(policy),
            "executions": sorted(selected), "missing": sorted(set(missing))}


def declare_readiness(run_dir, candidate):
    """Record bounded setup facts; software readiness is independently derived."""
    from agent_runtime import native_session_id
    from workflow_objectives import authorize_coordinator
    with lock(run_dir.parent):
        authorize_coordinator(run_dir, native_session_id())
        _, latest, policy = context(run_dir)
        require(policy["requested"], "UAT was not requested")
        require(isinstance(candidate, dict) and set(candidate) == {"head", "backend", "prerequisites"}
                and re.fullmatch(r"[0-9a-f]{40,64}", str(candidate["head"]))
                and candidate["backend"] == policy["backend"], "readiness target/backend changed")
        facts = candidate["prerequisites"]
        require(isinstance(facts, dict) and set(facts) == PREREQUISITES
                and all(type(v) is bool for v in facts.values()), "declare each runtime prerequisite")
        readiness = software_readiness(run_dir)
        value = {"schema": "workflow/uat-readiness-v1", **candidate, **readiness,
                 "objective_sha256": latest["sha256"]}
        write(run_dir / "uat/readiness.json", value)
        return value


def readiness(run_dir, head):
    software = software_readiness(run_dir)
    missing = list(software["missing"])
    path = run_dir / "uat/readiness.json"
    facts = read(path) if path.exists() else None
    if (facts is None or facts.get("head") != head
            or any(facts.get(k) != software[k] for k in ("objective_sha256", "policy_sha256", "executions"))):
        missing.extend(sorted(PREREQUISITES))
    else:
        missing.extend(key for key in sorted(PREREQUISITES) if facts["prerequisites"].get(key) is not True)
    return {**software, "missing": sorted(set(missing))}


def _events(run_dir):
    rows = []
    for path in sorted((run_dir / "uat/events").glob("*.json")):
        value = read(path)
        require(value.get("schema") == EVENT_SCHEMA, "unsupported UAT history; preserve it and start a new run")
        require(value.get("status") in STATES and digest(value) == path.stem, "UAT outcome history changed")
        _validate_basis(value["criteria"], value.get("obligation_basis"))
        rows.append(value)
    return sorted(rows, key=lambda v: v["sequence"])


def _validate_basis(criteria, basis):
    def sha(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
    require(isinstance(basis, dict) and set(basis) == set(criteria)
            and all(sha(value) or (isinstance(value, dict)
                    and set(value) == {"source_sha256", "coverage_sha256"}
                    and all(sha(part) for part in value.values())) for value in basis.values()),
            "UAT criterion identities are missing or changed")


def record(run_dir, status, head, *, reason, qualification=None, cleanup_proven=True,
           campaign=None, criteria=(), obligation_basis=None):
    require(status in STATES and isinstance(reason, str) and 0 < len(reason) <= 1024,
            "invalid UAT outcome")
    from workflow_security import contains_sensitive
    require(not contains_sensitive(reason), "UAT status must not contain sensitive data")
    _, latest, policy = context(run_dir)
    require(policy["requested"] or status == "not_requested", "UAT was not requested")
    basis = {} if obligation_basis is None else obligation_basis
    _validate_basis(criteria, basis)
    with lock(run_dir / "uat"):
        events = _events(run_dir)
        value = {"schema": EVENT_SCHEMA, "sequence": len(events) + 1,
                 "status": status, "head": head, "reason": reason,
                 "policy": policy, "policy_sha256": digest(policy),
                 "objective_sha256": latest["sha256"], "qualification": qualification,
                 "cleanup_proven": cleanup_proven, "campaign": campaign, "criteria": sorted(criteria),
                 "obligation_basis": basis,
                 "executions": software_readiness(run_dir)["executions"]}
        if status == "passed":
            from workflow_qualification import verify_receipt
            receipt = verify_receipt(run_dir, qualification, current=False)
            require(receipt["head"] == head and receipt["objective_sha256"] == latest["sha256"]
                    and receipt["executions"] == value["executions"]
                    and receipt["obligation_basis"] == basis
                    and set(receipt["criteria"]) == set(criteria),
                    "UAT evidence belongs to another candidate")
        write(run_dir / "uat/events" / (digest(value) + ".json"), value, exclusive=True)
        return value


def outcome(run_dir, head=None):
    _, latest, policy = context(run_dir)
    events = _events(run_dir)
    # Trial termination, product defects and cleanup are independent obligations.
    # A later small passing scope cannot erase a larger failed campaign.
    active, cleanup, defects = {}, {}, []
    for event in events:
        campaign = event["campaign"]
        if event["status"] == "running":
            active[campaign] = event
        elif event["status"] in {"passed", "failed", "incomplete"}:
            active.pop(campaign, None)
        if not event["cleanup_proven"]:
            cleanup[campaign] = event
        elif campaign is not None:
            cleanup.pop(campaign, None)
        if event["status"] == "failed":
            defects.append(event)
        elif event["status"] == "passed":
            from workflow_qualification import verify_receipt
            receipt = verify_receipt(run_dir, event["qualification"], current=False)
            defects = [old for old in defects if not (set(old["criteria"]) <= set(receipt["criteria"])
                       and set(old["executions"]) <= set(event["executions"])
                       and all(old["obligation_basis"][c] == receipt["obligation_basis"].get(c) for c in old["criteria"]))]
    unresolved = [*defects, *cleanup.values(), *active.values()]
    outstanding = unresolved[0] if unresolved else None
    if outstanding:
        status = "failed" if defects else "running" if active else "incomplete"
        reason = outstanding["reason"]
    else:
        current = [e for e in events if e["objective_sha256"] == latest["sha256"]
                   and e["policy_sha256"] == digest(policy) and (head is None or e["head"] == head)]
        latest_event = max(current, key=lambda e: (e.get("result_sequence", e["sequence"]), e["sequence"])) if current else None
        status = latest_event["status"] if latest_event else ("pending" if policy["requested"] else "not_requested")
        reason = latest_event["reason"] if latest_event else (
            "Requested UAT awaits a meaningful ready scope and runtime prerequisites."
            if policy["requested"] else "No UAT campaign was requested.")
    observed = outstanding if outstanding else latest_event
    return {"status": status, "reason": reason, "policy_sha256": digest(policy),
            "delivery_eligible": outstanding is None, "head": head if head is not None else observed["head"] if observed else None}


def require_delivery(run_dir, head):
    value = outcome(run_dir, head)
    require(value["delivery_eligible"], "an observed UAT defect or active/unresolved trial requires its owner")
    return value


def handoff(run_dir, head, *, repository):
    from workflow_objectives import _git
    from workflow_program import accepted
    _, latest, policy = context(run_dir)
    snapshot = accepted(run_dir)[2]
    definition = None
    if snapshot["inputs"] is not None:
        fingerprint = snapshot["inputs"]["program_sha256"]
        definition = read(run_dir / "program" / (fingerprint + ".json"))
        require(digest(definition) == fingerprint, "immutable program definition changed")
    readiness_path = run_dir / "uat/readiness.json"
    facts = read(readiness_path) if readiness_path.exists() else {}
    missing = [key for key in sorted(PREREQUISITES) if facts.get("head") != head or facts.get("prerequisites", {}).get(key) is not True] if policy["requested"] else []
    value = {"schema": "workflow/uat-handoff-v1", "objective_sha256": latest["sha256"],
             "candidate": {"head": head, "tree": _git(repository, "rev-parse", head + "^{tree}")},
             "criteria": scope_criteria(snapshot, policy, definition),
             "policy": policy, "outcome": outcome(run_dir, head), "missing_prerequisites": missing,
             "next_action": "Start a new explicitly requested campaign on this candidate; do not reopen the completed run."}
    write(run_dir / "uat/handoff.json", value)
    return value
