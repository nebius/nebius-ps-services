"""Pure factual report projection. No clocks, filesystem writes or workflow effects."""
from __future__ import annotations

from collections import Counter

from workflow_statistics_inventory import summarize


def elapsed(start, end):
    if (not start or not end or not start.get("clock_id")
            or start["clock_id"] != end.get("clock_id")):
        return None
    a, b = start.get("monotonic_ns"), end.get("monotonic_ns")
    return b - a if type(a) is int and type(b) is int and b >= a else None


def interval(start, end, **fields):
    return {**fields, "start": start, "end": end, "elapsed_ns": elapsed(start, end)}


def overlap(intervals):
    """Return measured interval union and wall time with two or more active tasks."""
    groups = {}
    for row in intervals:
        if row["elapsed_ns"] in (None, 0):
            continue
        clock = row["start"]["clock_id"]
        groups.setdefault(clock, []).extend(((row["start"]["monotonic_ns"], 1), (row["end"]["monotonic_ns"], -1)))
    union = shared = total = 0
    for events in groups.values():
        active, previous = 0, None
        for moment, delta in sorted(events):
            if previous is not None:
                duration = moment - previous
                union += duration if active else 0
                shared += duration if active > 1 else 0
                total += duration * active
            active += delta
            previous = moment
    complete = bool(intervals) and all(r["elapsed_ns"] is not None for r in intervals)
    # A point sample cannot supply missing intervals. A fully measured set of
    # zero-length attempts still has a known zero duration.
    measured = bool(groups) or complete
    return {"recorded_task_time_ns": total if measured else None,
            "recorded_union_ns": union if measured else None,
            "recorded_overlap_ns": shared if measured else None,
            "complete": complete}


def history(events, observation):
    launches, workers, phases, pauses, recoveries = [], [], [], [], []
    previous = {}
    open_launches, open_workers = {}, {}
    phase = pause = None
    gaps = []
    boundaries = {}
    for event in events:
        stamp, state, action = event["clock"], event["state"], event["event"]
        # Preserve the first event and first task at an equal clock, including
        # revisions and repeated clock samples. This index lives for one call.
        key = tuple(sorted(stamp.items()))
        if key not in boundaries:
            definitions = {}
            for definition in state["tasks"]:
                definitions.setdefault(definition["id"], definition)
            boundaries[key] = (state["objective"]["sha256"], definitions)
        current_phase = state["phase"]
        if phase is None or phase["name"] != current_phase:
            if phase is not None:
                phases.append(interval(phase["start"], stamp, phase=phase["name"]))
            phase = {"name": current_phase, "start": stamp}
        if action == "pause" and pause is None:
            pause = stamp
        elif action == "resume" and pause is not None:
            pauses.append(interval(pause, stamp))
            pause = None
        if event.get("operation") and action in {"recover_task", "replan_future", "requeue_task"}:
            recoveries.append(event["operation"])
        current = {r["id"]: r for r in state["tasks"]}
        for identity, row in current.items():
            old = previous.get(identity, {})
            dispatched = row.get("dispatched_at")
            if dispatched and dispatched != old.get("dispatched_at"):
                if identity in open_launches:
                    launches.append(interval(open_launches.pop(identity), None, task=identity, outcome="interrupted"))
                open_launches[identity] = stamp if action == "arm_task" else None
            started = row.get("worker_started_at")
            if started and (started != old.get("worker_started_at") or row["attempt"] != old.get("attempt")):
                if identity in open_workers:
                    prior = open_workers.pop(identity)
                    workers.append(interval(prior["start"], None,
                                            task=identity, attempt=prior["attempt"], outcome="recovered"))
                if identity in open_launches:
                    launches.append(interval(open_launches.pop(identity), stamp, task=identity, outcome="started"))
                open_workers[identity] = {"start": stamp if action in {"start_task", "recover_task"} else None, "attempt": row["attempt"]}
            if identity in open_launches and not dispatched:
                launches.append(interval(open_launches.pop(identity), stamp, task=identity, outcome="prestart_failed"))
            if identity in open_workers and row["status"] not in {"running", "assigned"}:
                prior = open_workers.pop(identity)
                workers.append(interval(prior["start"], stamp if action in {"worker_result", "finish_task", "recover_task"} else None,
                                        task=identity, attempt=prior["attempt"], outcome=row["status"]))
            if identity in open_workers and started is None:
                prior = open_workers.pop(identity)
                workers.append(interval(prior["start"], None,
                                        task=identity, attempt=prior["attempt"], outcome="recovered"))
        for identity in set(previous) - set(current):
            if identity in open_workers:
                prior = open_workers.pop(identity)
                workers.append(interval(prior["start"], None, task=identity, attempt=prior["attempt"], outcome="retired"))
        previous = current
    stopped = bool(events) and events[-1]["state"]["status"] in {"done", "cancelled", "failed", "interrupted", "blocked"}
    for identity, start in open_launches.items():
        launches.append(interval(start, None if stopped else observation, task=identity, outcome="interrupted" if stopped else "pending"))
    for identity, prior in open_workers.items():
        workers.append(interval(prior["start"], None if stopped else observation,
                                task=identity, attempt=prior["attempt"], outcome="interrupted" if stopped else "running"))
    if phase is not None:
        phases.append(interval(phase["start"], observation, phase=phase["name"]))
    if pause is not None:
        pauses.append(interval(pause, observation, outcome="paused"))
    if any(r["elapsed_ns"] is None for r in launches + workers + phases + pauses):
        gaps.append("Some intervals lack compatible clocks or observed owner boundaries.")
    for rows in (launches, workers):
        attempts = Counter()
        for row in rows:
            attempts[row["task"]] += 1
            row["observation_index"] = attempts[row["task"]]
            boundary = row["start"] or row["end"]
            bound = boundaries.get(tuple(sorted(boundary.items()))) if boundary else None
            definition = bound[1].get(row["task"], {}) if bound else {}
            row["definition_sha256"] = definition.get("definition_sha256")
            row["plan_sha256"] = definition.get("plan_sha256")
            row["objective_sha256"] = bound[0] if bound else None
    return {"launch_attempts": launches, "worker_attempts": workers, "phases": phases,
            "pauses": pauses, "recovery_operations": recoveries, "gaps": gaps}


def project(events: list[dict], state: dict, observation: dict, *, initial=None) -> dict:
    if (state.get("owner") not in {"implement", "agentic-sdlc"}
            or any(event["state"].get("owner") != state["owner"] for event in events)
            or initial is not None and initial.get("owner") != state["owner"]):
        raise ValueError("Statistics owner history is unsupported or inconsistent.")
    timeline = history(events, observation)
    architecture = summarize(state.get("architecture"))
    tasks = [dict(row) for row in state["tasks"]]
    attempts_by_task = {}
    for attempt in timeline["worker_attempts"]:
        attempts_by_task.setdefault(attempt["task"], []).append(attempt)
    for row in tasks:
        intervals = attempts_by_task.get(row["id"], [])
        row["recorded_elapsed_ns"] = (sum(r["elapsed_ns"] for r in intervals)
                                      if intervals and all(r["elapsed_ns"] is not None for r in intervals) else None)
    counts = Counter(row["status"] for row in tasks)
    accepted = sum(row["accepted"] for row in tasks)
    integrated = sum(row["integrated"] for row in tasks)
    retired = state.get("retired_tasks", [])
    retired_ids = {row["id"] for row in retired}
    retired_counts = {"known": len(retired_ids),
                      "accepted": len({r["id"] for r in retired if r["accepted"]}),
                      "integrated": len({r["id"] for r in retired if r["integrated"]})}
    groups = {}
    for row in tasks:
        groups.setdefault(row.get("component") or "unassigned", []).append(row["id"])
    baseline = initial or (events[0]["state"] if events else state)
    before = {r["id"]: r for r in baseline["tasks"]}
    after = {r["id"]: r for r in tasks}
    changes = {"added_tasks": sorted(after.keys() - before.keys()),
               "removed_tasks": sorted(before.keys() - after.keys()),
               "changed_tasks": sorted(k for k in before.keys() & after.keys()
                                       if before[k]["definition_sha256"] != after[k]["definition_sha256"]),
               "objective_changed": baseline["objective"]["sha256"] != state["objective"]["sha256"],
               "architecture_changed": baseline.get("architecture") != state.get("architecture")}
    original_components = {r["id"] for r in (baseline.get("architecture") or {}).get("components", [])}
    current_components = {r["id"] for r in architecture["components"]}
    changes.update(added_components=sorted(current_components - original_components),
                   removed_components=sorted(original_components - current_components))
    gaps = list(architecture["gaps"]) + timeline.pop("gaps") + list(state.get("gaps", []))
    if any(not r.get("component") for r in tasks):
        gaps.append("Some tasks have no primary component assignment.")
    components = {r["id"] for r in architecture["components"]}
    component_features = {}
    for row in architecture["components"]:
        component_features.setdefault(row["id"], set()).update(row.get("features", []))
    unknown = sorted({ref for task in tasks for ref in [task.get("component"), *task.get("touched_components", [])]
                      if ref and ref not in components})
    if unknown:
        gaps.append("Unknown component references: " + ", ".join(unknown))
    if initial is None:
        gaps.append("An initial report before execution was not captured.")
    if not events:
        gaps.append("No preserved execution timing observations are available.")
    workflow_start = (events[0].get("operation") or {}).get("start") if events and events[0]["event"] == "accepted" else None
    workflow_ns = elapsed(workflow_start, observation)
    if workflow_ns is None:
        gaps.append("Whole-workflow monotonic elapsed time is unavailable.")
    terminal = state["status"] in {"done", "cancelled", "failed", "blocked", "interrupted"}
    return {"schema": "workflow/statistics-report-v1", "run_id": state["run_id"],
            "owner": state["owner"],
            "scope_basis": state["scope_basis"], "delivery": state["delivery"],
            "objective": state["objective"], "phase": state["phase"], "status": state["status"],
            "report_kind": "terminal" if terminal else "progress",
            "completeness": "complete" if state["status"] == "done" and not gaps else "partial",
            "observed_at": observation["utc"], "architecture": architecture, "tasks": tasks,
            "retired_tasks": retired, "retired_task_counts": retired_counts,
            "logical_task_count": len({r["id"] for r in tasks} | retired_ids),
            "repair_controls": state.get("repair_controls", []),
            "repair_dispatches": state.get("repair_dispatches", []),
            "architecture_currentness": state.get("architecture_currentness", "unavailable"),
            "task_counts": {"known": len(tasks), "by_status": dict(counts), "accepted": accepted,
                            "integrated": integrated, "remaining": len(tasks) - accepted,
                            "running": counts["running"], "waiting": counts["planned"] + counts["assigned"],
                            "submitted": counts["submitted"], "blocked": counts["blocked"] + counts["replan_required"]},
            "component_tasks": groups, "undecomposed_slices": state["undecomposed_slices"],
            "component_features": {identity: sorted(features) for identity, features in component_features.items()},
            "planned_waves": state.get("planned_waves", {}),
            "executions": state["executions"], "limits": state["limits"],
            "scope_changes": changes, "timing": {**timeline, **overlap(timeline["worker_attempts"]),
                "peak_observed_workers": max(sum(row["status"] == "running" for row in snapshot["tasks"])
                                             for snapshot in [*(event["state"] for event in events), state]),
                "workflow_elapsed_ns": workflow_ns,
                "recorded_pause_ns": sum(r["elapsed_ns"] for r in timeline["pauses"]) if timeline["pauses"] and all(r["elapsed_ns"] is not None for r in timeline["pauses"]) else None,
                "recorded_recovery_operation_ns": sum(r["elapsed_ns"] for r in timeline["recovery_operations"]) if timeline["recovery_operations"] and all(r["elapsed_ns"] is not None for r in timeline["recovery_operations"]) else None,
                "measurement": "Owner-observed elapsed intervals; not CPU use or productive effort."},
            "measurement_gaps": sorted(set(gaps)), "stage_counters": state.get("stage_counters")}


def chat(report):
    counts, architecture = report["task_counts"], report["architecture"]
    layers = ", ".join(dict.fromkeys(r["name"] for r in architecture["layers"])) or "unavailable"
    components = ", ".join(dict.fromkeys(r["name"] for r in architecture["components"])) or "unavailable"
    retired = report["retired_task_counts"]
    owner = "Implement" if report["owner"] == "implement" else "SDLC"
    return (f"{owner} {report['report_kind']} — {report['phase']}: "
            f"layers {layers}; components {components}. "
            f"{report['logical_task_count']} known logical tasks; {counts['known']} current, {counts['accepted']} accepted, "
            f"{counts['integrated']} integrated, {counts['running']} running; "
            f"{counts['submitted']} submitted, {counts['blocked']} blocked. "
            f"{retired['known']} retired ({retired['accepted']} accepted before retirement); "
            f"{len(report['repair_dispatches'])} counted repair dispatches; "
            f"{len(report['undecomposed_slices'])} undecomposed slices. "
            f"Report {report['completeness']}; {len(report['measurement_gaps'])} measurement gaps.")


def markdown(report):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ") if value is not None else "unavailable"

    owner = "Implement" if report["owner"] == "implement" else "SDLC"
    lines = [f"# {owner} statistics", "", chat(report), "", f"Objective: {cell(report['objective']['outcome'])}",
             "", "## Architecture", "", "| ID | Name | Origin | Layer | Sources |", "| --- | --- | --- | --- | --- |"]
    for row in report["architecture"]["layers"] + report["architecture"]["components"]:
        sources = "; ".join(f"{r['kind']}:{r['path']} ({r['anchor']}, {r['sha256']})" for r in row["sources"])
        lines.append("| " + " | ".join(cell(v) for v in (row["id"], row["name"], row["origin"], row.get("layer"), sources)) + " |")
    lines += ["", "## Tasks", "", "| Task | Name | Component | State | Accepted | Integrated | Elapsed ns |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in report["tasks"]:
        lines.append("| " + " | ".join(cell(row.get(key)) for key in ("id", "name", "component", "status", "accepted", "integrated", "recorded_elapsed_ns")) + " |")
    counters = report.get("stage_counters")
    if counters:
        lines += ["", "## Observed timing counters", "", counters["coverage"], "",
                  "Inclusive totals overlap and cannot be summed as elapsed time.", "",
                  "| Stage | Samples | Failed | Measured ms | Unknown samples |",
                  "| --- | --- | --- | --- | --- |"]
        for name, row in sorted(counters["stages"].items()):
            lines.append("| " + " | ".join(cell(v) for v in
                (name, row["count"], row["failed"], row["total_ms"], row["unknown"])) + " |")
    # The complete projection is included so Markdown and JSON have identical detail.
    import json
    lines += ["", "## Recorded detail", "", "```json", json.dumps(report, indent=2, sort_keys=True), "```", ""]
    return "\n".join(lines)
