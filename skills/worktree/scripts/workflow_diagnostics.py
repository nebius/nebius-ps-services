"""Bounded diagnostic sidecars; native controls launch them, never this owner."""
from __future__ import annotations

import time
import sys
from pathlib import Path

from workflow_objectives import authorize_coordinator
from workflow_state import WorkflowError, digest, lock, read, write
from workflow_security import contains_sensitive

SCHEMA = "workflow/diagnostic-assignment-v1"
PASSIVE_TOOLS = frozenset({"read_file", "list_files", "search_text"})


def reserve(run_dir: Path, event: dict, questions: list[dict], session: str, *,
            restrictions=None, remaining_seconds: int, clock=time.time_ns) -> dict:
    """The host must supply a live effective-restriction observer, not a role name.

    No caller JSON or stored capability claim authorizes delegation. Unsupported
    hosts deliberately take the serial path. The native adapter owns applying
    restrictions and observing them again before launch and terminal collection.
    """
    from workflow_capacity import effective
    from workflow_projects import reserve_resources, LIMITS
    authorize_coordinator(run_dir, session, owner="agentic-sdlc")
    if (type(remaining_seconds) is not int or remaining_seconds < 0
            or not 2 <= len(questions) <= 3):
        raise WorkflowError("DIAGNOSTIC_INVALID", "two or three bounded independent questions are required")
    if remaining_seconds == 0:
        return {"status": "serial", "reason": "shared_deadline_exhausted", "assignments": []}
    if restrictions is None:
        return {"status": "serial", "reason": "effective_read_only_controls_unavailable", "assignments": []}
    observed = restrictions()
    if (not isinstance(observed, dict) or observed.get("enforced") is not True
            or observed.get("no_recursive_delegation") is not True
            or observed.get("deadline_enforced") is not True
            or observed.get("scope") != "provided_evidence_only"
            or not isinstance(observed.get("tools"), list)
            or not set(observed["tools"]) <= PASSIVE_TOOLS
            or not isinstance(observed.get("native_policy_identity"), str)
            or not observed["native_policy_identity"]):
        return {"status": "serial", "reason": "effective_read_only_controls_unavailable", "assignments": []}
    if len({digest(question) for question in questions}) != len(questions):
        raise WorkflowError("DIAGNOSTIC_INVALID", "diagnostic questions must be distinct")
    scripts = Path(__file__).resolve().parents[2] / "sdlc-classify-failure/scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import repair_control as control
    canonical = control._load_event(run_dir, event["execution_id"], event["event_id"])
    if canonical != event:
        raise WorkflowError("DIAGNOSTIC_INVALID", "diagnostic event changed")
    with lock(run_dir.parent), control._transition_lock(run_dir, event["execution_id"]):
        current = control._load_control(run_dir, event["execution_id"])
        budget = current["active_blocker"]
        if current["current_event_id"] != event["event_id"] or budget["blocker_key"] != event["blocker_key"]:
            raise WorkflowError("DIAGNOSTIC_INVALID", "diagnostic event is no longer the active blocker")
        remaining_seconds = min(remaining_seconds, max(0, budget["time_limit_seconds"] - budget["active_seconds"]))
        if not remaining_seconds or budget["stop_trigger"] is not None:
            return {"status": "serial", "reason": "shared_deadline_exhausted", "assignments": []}
        root = run_dir / "repairs" / event["execution_id"] / "helpers" / event["event_id"]
        path = root / "assignments.json"
        if path.exists():
            saved = read(path)
            if saved.get("questions_sha256") != digest(questions):
                raise WorkflowError("DIAGNOSTIC_INVALID", "diagnostic questions changed on replay")
            if not (root / "reservation.json").exists():
                write(root / "reservation.json", {"status": "intent"}, exclusive=True)
            state = read(root / "reservation.json")
            if state["status"] in {"serial", "reconciled"}:
                return {"status": "serial", "reason": "diagnostic_assignment_terminal", "assignments": []}
            if any(clock() >= row["deadline_ns"] for row in saved["assignments"]):
                raise WorkflowError("DIAGNOSTIC_PENDING", "diagnostic deadline elapsed; observe termination before release")
            if any(restrictions() != row["policy"] for row in saved["assignments"]):
                raise WorkflowError("DIAGNOSTIC_PENDING", "effective restrictions changed; reconcile existing helpers")
            if state["status"] == "reserved":
                from workflow_projects import require_resource
                for identity in saved["reservations"]:
                    require_resource(run_dir, "workers", identity)
                return {**saved, "status": "reserved"}
            if state["status"] != "intent":
                raise WorkflowError("DIAGNOSTIC_INVALID", "diagnostic reservation state is invalid")
            held = reserve_resources(run_dir, "diagnostics", saved["reservations"],
                                     ceiling=effective(run_dir, LIMITS["workers"]))
            return _finish_reservation(run_dir, root, saved, held)
        now = clock()
        assignments = []
        for question in questions:
            if (set(question) != {"question", "evidence"} or not isinstance(question["question"], str)
                    or not question["question"].strip() or len(question["question"]) > 2048
                    or contains_sensitive(question["question"])
                    or not isinstance(question["evidence"], list) or not 1 <= len(question["evidence"]) <= 16):
                raise WorkflowError("DIAGNOSTIC_INVALID", "diagnostic question or evidence scope is invalid")
            evidence = {}
            for relative in question["evidence"]:
                candidate = Path(relative)
                if candidate.is_absolute() or '..' in candidate.parts or candidate.parts[:1] != ('evidence',):
                    raise WorkflowError("DIAGNOSTIC_INVALID", "diagnostic evidence must be scoped private evidence")
                from project_specs_lib.contracts import _read_file
                raw = _read_file(run_dir / candidate, "diagnostic evidence")[0]
                if len(raw) > 65536 or contains_sensitive(raw.decode()):
                    raise WorkflowError("DIAGNOSTIC_INVALID", "diagnostic evidence is unsafe or too large")
                import hashlib
                evidence[relative] = hashlib.sha256(raw).hexdigest()
            value = {"schema": SCHEMA, "event_id": event["event_id"],
                     "blocker_key": event["blocker_key"], "execution_id": event["execution_id"],
                     "question": question["question"], "evidence": evidence,
                     "policy": observed, "started_ns": now,
                     "deadline_ns": now + min(300, remaining_seconds) * 1_000_000_000,
                     "prohibited": ["delegation", "tests", "repairs", "external_mutations"],
                     "worktree": None}
            value["assignment_id"] = digest(value)
            assignments.append(value)
        identities = ["diagnostic/" + event["event_id"] + "/" + item["assignment_id"] for item in assignments]
        result = {"status": "prepared", "assignments": assignments, "reservations": identities,
                  "questions_sha256": digest(questions)}
        result["receipt_sha256"] = digest(result)
        # Durable identities precede the first capacity mutation.
        write(path, result, exclusive=True)
        write(root / "reservation.json", {"status": "intent"}, exclusive=True)
        held = reserve_resources(run_dir, "diagnostics", identities,
                                 ceiling=effective(run_dir, LIMITS["workers"]))
        return _finish_reservation(run_dir, root, result, held)


def _finish_reservation(run_dir, root, assignments, held):
    from workflow_projects import release_resources
    if len(held) != len(assignments["reservations"]):
        release_resources(run_dir, "diagnostics", held)
        write(root / "reservation.json", {"status": "serial"})
        return {"status": "serial", "reason": "shared_capacity_unavailable", "assignments": []}
    write(root / "reservation.json", {"status": "reserved"})
    return {**assignments, "status": "reserved"}


def reconcile(run_dir: Path, execution_id: str, event_id: str, findings: list[dict],
              session: str, *, observe_termination, restrictions, clock=time.time_ns) -> dict:
    """Retain contradictions and release only after live exact-handle termination."""
    from workflow_projects import release_resources
    authorize_coordinator(run_dir, session, owner="agentic-sdlc")
    root = run_dir / "repairs" / execution_id / "helpers" / event_id
    with lock(run_dir.parent):
        assignments = read(root / "assignments.json")
        expected = {row["assignment_id"]: row for row in assignments["assignments"]}
        if len(findings) != len(expected) or {row.get("assignment_id") for row in findings} != set(expected):
            raise WorkflowError("DIAGNOSTIC_PENDING", "every helper needs findings and observed termination")
        for row in findings:
            assignment = expected[row["assignment_id"]]
            if (set(row) != {"assignment_id", "native_handle", "findings", "contradictions", "status"}
                    or row["status"] not in {"completed", "cancelled", "failed"}
                    or not isinstance(row["native_handle"], str) or not row["native_handle"]
                    or not all(isinstance(row[key], list) for key in ("findings", "contradictions"))
                    or len(str(row)) > 16384 or contains_sensitive(str(row))):
                raise WorkflowError("DIAGNOSTIC_INVALID", "helper result is invalid")
            if not observe_termination(row["native_handle"], assignment):
                raise WorkflowError("DIAGNOSTIC_PENDING", "helper termination is unconfirmed")
            if restrictions() != assignment["policy"]:
                raise WorkflowError("DIAGNOSTIC_INVALID", "helper restrictions changed; retain intervention evidence")
        result = {"schema": "workflow/diagnostic-findings-v1", "event_id": event_id,
                  "assignments_sha256": digest(assignments),
                  "findings": findings, "requires_coordinator_reconciliation": True,
                  "completed_ns": clock(), "classification_authority": False}
        result["receipt_sha256"] = digest(result)
        # Findings are not diagnoses; only the coordinator returns diagnosis-v3.
        path = root / 'findings.json'
        if path.exists():
            prior = read(path)
            if prior['findings'] != findings:
                raise WorkflowError("DIAGNOSTIC_INVALID", "immutable helper findings changed")
            result = prior
        else:
            write(path, result, exclusive=True)
        release_resources(run_dir, "diagnostics", assignments["reservations"])
        write(root / "reservation.json", {"status": "reconciled"})
        scripts = Path(__file__).resolve().parents[2] / "sdlc-classify-failure/scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from repair_control import record_experiment
        start = min(row["started_ns"] for row in expected.values()) // 1_000_000_000
        end = (result["completed_ns"] + 999_999_999) // 1_000_000_000
        record_experiment(run_dir, execution_id, "Reconcile independent diagnostic boundaries.",
                          "Scoped evidence distinguishes the failing boundary.",
                          f"repairs/{execution_id}/helpers/{event_id}/findings.json", "model_update",
                          active_intervals=[[start, end]], expected_event_id=event_id,
                          expected_blocker_key=next(iter(expected.values()))["blocker_key"])
        return result


def require_reconciled(run_dir: Path, execution_id: str, event_id: str) -> None:
    root = run_dir / "repairs" / execution_id / "helpers" / event_id
    if not (root / "assignments.json").exists():
        return
    reservation = root / "reservation.json"
    if reservation.exists() and read(reservation).get("status") == "serial":
        from workflow_projects import require_released
        require_released(run_dir, read(root / "assignments.json")["reservations"])
        return
    if not (root / "findings.json").exists():
        raise WorkflowError("DIAGNOSTIC_PENDING", "reconcile all helpers before producing one diagnosis")
    assignments, result = read(root / "assignments.json"), read(root / "findings.json")
    if (digest({k: v for k, v in result.items() if k != "receipt_sha256"}) != result.get("receipt_sha256")
            or result.get("assignments_sha256") != digest(assignments)):
        raise WorkflowError("DIAGNOSTIC_INVALID", "helper reconciliation evidence changed")
    budget = read(run_dir / "repairs" / execution_id / "repair-control.json")
    if (budget["current_event_id"] != event_id or any(
            row["blocker_key"] != budget["active_blocker"]["blocker_key"] for row in assignments["assignments"])):
        raise WorkflowError("DIAGNOSTIC_INVALID", "helper findings belong to a historical blocker")
    evidence = f"repairs/{execution_id}/helpers/{event_id}/findings.json"
    start = min(row["started_ns"] for row in assignments["assignments"]) // 1_000_000_000
    end = (result["completed_ns"] + 999_999_999) // 1_000_000_000
    observations = budget["active_blocker"]["experiments"]
    if not any(row.get("evidence_reference") == evidence and row.get("active_intervals") == [[start, end]]
               and row.get("outcome") in {"recorded", "exhausted"} for row in observations):
        raise WorkflowError("DIAGNOSTIC_PENDING", "helper observation accounting is incomplete")
    if budget["status"] == "exhausted":
        raise WorkflowError("REPAIR_BUDGET_EXHAUSTED", "shared diagnostic deadline is exhausted")
