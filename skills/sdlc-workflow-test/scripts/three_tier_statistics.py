"""Independent statistics observations and sanitized retention for disposable trials."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import three_tier_target


class StatisticsEvidenceError(RuntimeError):
    """Reporting evidence is absent, inconsistent or unobserved."""


def _safe(value):
    from workflow_security import contains_sensitive
    raw = json.dumps(value, sort_keys=True)
    if contains_sensitive(raw) or re.search(r"https?://|/(?:Users|home|private|tmp)/", raw):
        raise StatisticsEvidenceError("Statistics contain unsafe retained content.")


def observe(state, phase):
    """Observe only; never repair or advance the product's reporting state."""
    from agent_runtime import agent_name
    from three_tier_lifecycle import private_json
    category = "before" if phase == "sdlc-start" else "after" if phase == "complete" else "during"
    receipt = {"schema": "agentic-sdlc/statistics-observation-v1", "verification_id": state["verification_id"],
               "phase": phase, "category": category, "status": "UNAVAILABLE"}
    try:
        registration = three_tier_target.cleanup_registration(state, agent_name())
        run_dir = registration[4]
        with three_tier_target.workflow_imports(Path(__file__).resolve().parents[2]):
            from workflow_statistics import snapshot, _events, _initial, _read_lock
            from workflow_statistics_report import project
            from workflow_state import read, digest
            with _read_lock(run_dir):
                current = snapshot(run_dir)
                root = run_dir / "statistics"
                events = _events(root)
                baseline, initial = _initial(root, events)
                if initial is None:
                    raise StatisticsEvidenceError("Initial statistics are missing.")
                if events[-1]["state_sha256"] != digest({k: v for k, v in current.items() if k != "stage_counters"}):
                    raise StatisticsEvidenceError("The current checkpoint has no statistics observation.")
                report = read(root / "current.json")
                expected = project(events, events[-1]["state"], events[-1]["clock"], initial=baseline)
                expected["observation_sha256"] = events[-1]["sha256"]
                if report != expected:
                    raise StatisticsEvidenceError("Saved statistics differ from the deterministic owner history.")
                # Direct authoritative state cross-check, independent of reported counts.
                task_ids = set()
                accepted = integrated = 0
                for path in (run_dir / "execution").glob("*/waves/*.json"):
                    wave = read(path)
                    if wave["status"] == "superseded":
                        continue
                    task_ids.update((path.parent.parent.name, t) for t in wave["task_ids"])
                    accepted += len(wave["admitted_task_ids"])
                    integrated += len(wave["integrated_task_ids"])
                if (task_ids and report["task_counts"]["known"] < len(task_ids)
                        or report["task_counts"]["accepted"] != accepted
                        or report["task_counts"]["integrated"] != integrated):
                    raise StatisticsEvidenceError("Reported task counts differ from execution ownership.")
                if category == "before" and (task_ids or not baseline["architecture"]):
                    raise StatisticsEvidenceError("Initial architecture was not observed before execution.")
                if category == "during" and (report["status"] in {"done", "cancelled", "blocked", "failed"}
                        or not report["timing"]["worker_attempts"]):
                    raise StatisticsEvidenceError("During evidence requires observed execution history before termination.")
                if category == "after" and (report["status"] != "done" or not report["task_counts"]["known"]
                        or report["task_counts"]["integrated"] != report["task_counts"]["known"]
                        or not report["timing"]["worker_attempts"]):
                    raise StatisticsEvidenceError("Terminal reporting has no completed delivery history.")
                _safe(report)
                phase_entry = next((r for r in reversed(state.get("phases", [])) if r["phase"] == phase and r["status"] == "PASS"), None)
                if category != "after" and phase_entry is None:
                    raise StatisticsEvidenceError("Statistics have no independent phase observation.")
                receipt.update(status="PASS", run_id=run_dir.name, initial_sha256=initial["sha256"],
                               last_observation_sha256=events[-1]["sha256"], report=report,
                               phase_attempt_sha256=phase_entry["artifact_sha256"] if phase_entry else None)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        receipt["gap"] = "Statistics evidence is missing, inconsistent or unsafe; no live reporting qualification."
    raw = json.dumps(receipt, sort_keys=True).encode()
    path = Path(state["evidence_root"]) / "statistics" / (hashlib.sha256(raw).hexdigest() + ".json")
    try:
        if not path.exists():
            private_json(path, receipt)
    except (OSError, RuntimeError):
        return {"status": "UNAVAILABLE", "gap": "Statistics observation could not be retained."}
    return receipt


def require_complete(state):
    from three_tier_lifecycle import read_json
    receipts = []
    for path in (Path(state["evidence_root"]) / "statistics").glob("*.json"):
        value = read_json(path)
        if hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() != path.stem:
            raise StatisticsEvidenceError("Statistics observation digest changed.")
        receipts.append(value)
    matching = [r for r in receipts if r.get("schema") == "agentic-sdlc/statistics-observation-v1"
                and r.get("verification_id") == state["verification_id"] and r.get("status") == "PASS"]
    if not {"before", "during", "after"} <= {r["category"] for r in matching}:
        raise StatisticsEvidenceError("Before, during and after statistics observations are required.")
    if len({(r["run_id"], r["initial_sha256"]) for r in matching}) != 1:
        raise StatisticsEvidenceError("Statistics observations do not share the frozen initial baseline.")
    from agent_runtime import agent_name
    run_dir = three_tier_target.cleanup_registration(state, agent_name())[4]
    with three_tier_target.workflow_imports(Path(__file__).resolve().parents[2]):
        from workflow_statistics import _events, _initial
        from workflow_statistics_report import project
        events = _events(run_dir / "statistics")
        baseline, initial = _initial(run_dir / "statistics", events)
        if initial is None:
            raise StatisticsEvidenceError("Initial statistics are no longer verifiable.")
        for receipt in matching:
            observed = next((e for e in events if e["sha256"] == receipt["last_observation_sha256"]), None)
            if observed is None or receipt["initial_sha256"] != initial["sha256"] or receipt["run_id"] != run_dir.name:
                raise StatisticsEvidenceError("Statistics observation is not bound to owner history.")
            report = receipt["report"]
            expected = project(events[:observed["sequence"]], observed["state"], observed["clock"], initial=baseline)
            expected["observation_sha256"] = observed["sha256"]
            if report != expected:
                raise StatisticsEvidenceError("Statistics observation content differs from owner history.")
            category = "before" if receipt["phase"] == "sdlc-start" else "after" if receipt["phase"] == "complete" else "during"
            if receipt["category"] != category:
                raise StatisticsEvidenceError("Statistics category differs from the observed phase.")
            if category != "after" and not any(r["phase"] == receipt["phase"] and r["status"] == "PASS"
                    and r["artifact_sha256"] == receipt["phase_attempt_sha256"] for r in state.get("phases", [])):
                raise StatisticsEvidenceError("Statistics have no matching independent phase observation.")
            if category == "before" and (observed["state"]["tasks"] or not baseline["architecture"]):
                raise StatisticsEvidenceError("Before observation lacks architecture before execution.")
            if category == "during" and (report["status"] in {"done", "cancelled", "blocked", "failed"}
                    or not report["timing"]["worker_attempts"]):
                raise StatisticsEvidenceError("During observation has no nonterminal execution history.")
            if category == "after" and (report["status"] != "done" or not report["task_counts"]["known"]
                    or report["task_counts"]["integrated"] != report["task_counts"]["known"]
                    or not report["timing"]["worker_attempts"]):
                raise StatisticsEvidenceError("After observation has no completed delivery history.")
    return {"status": "PASS", "observed_categories": ["before", "during", "after"]}


def archive(state):
    """Retain only validated sanitized JSON/Markdown, never the private native home."""
    from agent_runtime import agent_name
    from three_tier_lifecycle import private_json
    destination = Path(state["report_path"]).parent / "statistics"
    summary = {"schema": "agentic-sdlc/statistics-retention-v1", "verification_id": state["verification_id"],
               "status": "PARTIAL", "retained": [], "gaps": [], "retention_pending": False}
    try:
        registration = three_tier_target.cleanup_registration(state, agent_name())
        run_dir = registration[4]
        with three_tier_target.workflow_imports(Path(__file__).resolve().parents[2]):
            from workflow_state import read, write
            from workflow_statistics import _markdown_write
            from workflow_statistics_report import markdown
            root = run_dir / "statistics"
            summary["retention_pending"] = root.exists()
            candidates = [root / "initial.json", root / "current.json", *sorted((root / "terminal").glob("*.json"))]
            for path in candidates:
                if not path.exists():
                    summary["gaps"].append(path.name + " unavailable")
                    continue
                report = read(path)
                if report.get("schema") != "workflow/statistics-report-v1" or report.get("run_id") != run_dir.name:
                    raise StatisticsEvidenceError("Retained statistics schema or run changed.")
                _safe(report)
                relative = path.relative_to(root)
                retained = destination / relative
                if retained.exists():
                    if read(retained) != report:
                        raise StatisticsEvidenceError("Retained statistics changed on cleanup replay.")
                else:
                    write(retained, report, exclusive=True)
                _markdown_write((destination / relative).with_suffix(".md"), markdown(report))
                summary["retained"].append(relative.as_posix())
            for path in sorted((Path(state["evidence_root"]) / "statistics").glob("*.json")):
                receipt = read(path)
                _safe(receipt)
                write(destination / "observations" / path.name, receipt)
            summary["status"] = "PASS" if not summary["gaps"] else "PARTIAL"
            summary["retention_pending"] = False
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        summary["gaps"].append("Some statistics could not be safely retained.")
    try:
        private_json(destination / "retention.json", summary)
    except (OSError, RuntimeError):
        summary["status"] = "UNAVAILABLE"
        summary["retention_pending"] = True
    return summary
