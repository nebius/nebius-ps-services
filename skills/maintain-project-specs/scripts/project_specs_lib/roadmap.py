"""Pure dependency and obligation coverage checks shared by design and SDLC."""

from __future__ import annotations


def invalid(message):
    raise ValueError(message)


def check_dag(rows, reject=invalid):
    remaining = {row["id"]: len(row["depends_on"]) for row in rows}
    children = {key: [] for key in remaining}
    for row in rows:
        for parent in row["depends_on"]:
            if parent not in children:
                reject("unknown program dependency")
            children[parent].append(row["id"])
    queue = [key for key, count in remaining.items() if not count]
    visited = 0
    while queue:
        visited += 1
        for child in children[queue.pop()]:
            remaining[child] -= 1
            if not remaining[child]:
                queue.append(child)
    if visited != len(rows):
        reject("program dependency cycle")


def check_schedule(phases_list, slices, obligations, reject=invalid):
    """Caller validates row shapes; no execution state or acceptance authority."""
    check_dag(phases_list, reject)
    check_dag(slices, reject)
    phases = {row["id"]: row for row in phases_list}
    seen = set()
    for row in slices:
        if not set(row["depends_on"]) <= seen:
            reject("slice declaration order must be topological")
        seen.add(row["id"])
    for phase_id, phase in phases.items():
        members = [row for row in slices if row["phase"] == phase_id]
        coverage = {
            item
            for row in members
            for item in row["criteria"] + row.get("regression_criteria", [])
        }
        if members and not set(phase["exit_criteria"]) <= coverage:
            reject("phase exit criteria must be executed by its delivery slices")
    for index, row in enumerate(slices):
        ancestors, pending = set(), list(phases[row["phase"]]["depends_on"])
        while pending:
            parent = pending.pop()
            if parent not in ancestors:
                ancestors.add(parent)
                pending.extend(phases[parent]["depends_on"])
        if any(later["phase"] in ancestors for later in slices[index:]):
            reject("slice declaration order violates a phase barrier")
    scheduled = {item for row in phases_list for item in row["exit_criteria"]} | {
        item for row in slices for item in row["criteria"]
    }
    if scheduled != set(obligations):
        reject("required source obligations are missing from program coverage")


def validate_roadmap(value, obligations):
    """Validate a reviewed standalone roadmap, without frozen execution checks."""
    if not isinstance(value, dict) or set(value) != {
        "mode",
        "rationale",
        "phases",
        "slices",
    }:
        invalid("roadmap shape is invalid")
    if (
        value["mode"] not in {"supplied", "derived"}
        or not isinstance(value["rationale"], str)
        or not value["rationale"].strip()
    ):
        invalid("roadmap requires reviewed supplied/derived provenance")
    for field in ("phases", "slices"):
        rows = value[field]
        if not isinstance(rows, list) or not rows:
            invalid("roadmap needs phases and slices")
        seen = set()
        for row in rows:
            keys = (
                {"id", "depends_on", "exit_criteria"}
                if field == "phases"
                else {"id", "depends_on", "phase", "criteria"}
            )
            if (
                not isinstance(row, dict)
                or set(row) != keys
                or not isinstance(row["id"], str)
                or not row["id"]
                or row["id"] in seen
            ):
                invalid("roadmap identities are invalid")
            seen.add(row["id"])
            for key in (
                "depends_on",
                "exit_criteria" if field == "phases" else "criteria",
            ):
                entries = row[key]
                if (
                    not isinstance(entries, list)
                    or any(not isinstance(item, str) for item in entries)
                    or len(entries) != len(set(entries))
                ):
                    invalid("roadmap references must be unique identifiers")
                if key != "depends_on" and (
                    not entries or not set(entries) <= set(obligations)
                ):
                    invalid("roadmap references unknown obligations")
    phases = {row["id"] for row in value["phases"]}
    if any(
        not isinstance(row["phase"], str) or row["phase"] not in phases
        for row in value["slices"]
    ):
        invalid("roadmap slice has unknown phase")
    check_schedule(value["phases"], value["slices"], obligations)
    return value
