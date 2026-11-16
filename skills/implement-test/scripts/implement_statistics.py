"""Verify and retain sanitized Implement reporting before disposable-home cleanup."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys


class StatisticsError(RuntimeError):
    """Observed reporting cannot substantiate the requested evidence."""


def _imports():
    scripts = str(Path(__file__).resolve().parents[2] / "worktree/scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def _safe(report):
    from workflow_security import contains_sensitive
    raw = json.dumps(report, sort_keys=True)
    if contains_sensitive(raw) or re.search(r"https?://|/(?:Users|home|private|tmp)/", raw):
        raise StatisticsError("Unsafe statistics cannot be retained.")


def reports(run_dir: Path, *, complete=False):
    """Independently replay recorded history; never produce product observations."""
    _imports()
    from workflow_objectives import validate_run
    from workflow_statistics import _events, _initial, _read_lock, _bytes, snapshot
    from workflow_statistics_report import project, markdown
    from workflow_state import read, digest
    with _read_lock(run_dir):
        run, _ = validate_run(run_dir, owner="implement")
        root = run_dir / "statistics"
        events = _events(root)
        if not events:
            if complete:
                raise StatisticsError("Reporting history is missing.")
            return {}
        baseline, initial = _initial(root, events)
        by_digest = {event["sha256"]: event for event in events}
        paths = [root / "initial.json", root / "current.json", *sorted((root / "terminal").glob("*.json"))]
        result = {}
        for path in paths:
            if not path.exists():
                continue
            report = read(path)
            event = by_digest.get(report.get("observation_sha256"))
            if event is None or report.get("owner") != "implement" or report.get("run_id") != run_dir.name:
                raise StatisticsError("Report identity differs from recorded owner history.")
            expected = project(events[:event["sequence"]], event["state"], event["clock"], initial=baseline)
            expected["observation_sha256"] = event["sha256"]
            if path.name == "initial.json":
                if event != initial:
                    raise StatisticsError("Initial report is not bound to the original observation.")
                expected["report_kind"] = "initial"
            if report != expected or _bytes(path.with_suffix(".md")).decode() != markdown(report):
                raise StatisticsError("Saved reporting differs from its deterministic projection.")
            _safe(report)
            result[path.relative_to(root).as_posix()] = report
        if complete:
            final = result.get("terminal/" + events[-1]["sha256"] + ".json")
            if (not final or final["report_kind"] != "terminal"
                    or final["observation_sha256"] != events[-1]["sha256"]):
                raise StatisticsError("Latest immutable terminal reporting is missing.")
            current = snapshot(run_dir)
            if (not initial or not baseline.get("architecture") or baseline["executions"]
                    or "initial.json" not in result or "current.json" not in result):
                raise StatisticsError("Initial architecture before specification bootstrap is missing.")
            if (run["status"] != "done" or current["status"] != "done"
                    or events[-1]["state_sha256"] != digest({k: v for k, v in current.items() if k != "stage_counters"})
                    or result["current.json"]["observation_sha256"] != events[-1]["sha256"]):
                raise StatisticsError("Terminal reporting does not match completed owner state.")
            report = result["current.json"]
            if (not report["task_counts"]["known"]
                    or report["task_counts"]["integrated"] != report["task_counts"]["known"]
                    or report["delivery"]["cleanup"] != "recorded"
                    or run.get("automatic_outer") and report["delivery"]["aggregation"] != "delivered"
                    or not any(e["sequence"] > initial["sequence"] and any(t["status"] == "running" for t in e["state"]["tasks"])
                               for e in events[:-1])):
                raise StatisticsError("During execution or final delivery reporting evidence is incomplete.")
        return result


def require_complete(run_dir: Path):
    retained = reports(run_dir, complete=True)
    return {"status": "PASS", "reports": len(retained),
            "initial_sha256": retained["initial.json"]["observation_sha256"],
            "terminal_sha256": retained["current.json"]["observation_sha256"]}


def archive(home: Path, destination: Path):
    """Only owned run reports are copied; raw native state never leaves the home."""
    _imports()
    from workflow_statistics import _report
    from workflow_state import read, write
    runs = sorted((home / "implement").glob("*/run-*/statistics"))
    manifest = {"schema": "implement-test/statistics-retention-v1", "reports": [], "gaps": []}
    for root in runs:
        run_dir = root.parent
        identity = hashlib.sha256(run_dir.name.encode()).hexdigest()
        for name, report in reports(run_dir).items():
            target = destination / identity / name
            if target.exists() and read(target) != report:
                # Current reports can advance between finish and later destroy.
                # Preserve every retained version rather than replacing history.
                target = destination / identity / "revisions" / (report["observation_sha256"] + ".json")
            _report(target.parent, target.stem, report, immutable=True)
            manifest["reports"].append(target.relative_to(destination).as_posix())
        if not (root / "initial.json").exists():
            manifest["gaps"].append("An initial reporting boundary was not captured.")
    if not runs:
        manifest["gaps"].append("No Implement reporting was recorded.")
    write(destination / "retention.json", manifest)
    return manifest
