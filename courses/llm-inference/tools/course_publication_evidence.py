"""Select complete original evidence sets using explicit per-variant policies."""

from __future__ import annotations
import copy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from course_publication_state import MAX_JSON, SCHEMA, atomic, digest, read

KINDS = {
    "single-original",
    "single-diagnostic",
    "stage-diagnostic",
    "three-trials",
    "paired-performance-trials",
    "paired-probe-trials",
}
STATUSES = ("completed", "failed", "skipped", "unstarted")


def validate_policies(course):
    observability = read(course / "reference/observability.json")
    execution = read(course / "reference/execution.json")
    if (
        execution.get("schema") != "course-execution/v1"
        or execution.get("course") != observability["course"]
    ):
        raise ValueError("Execution and publication course identities differ")
    recipes = observability["labs"]
    variants = execution["labs"]
    if recipes.keys() != variants.keys():
        raise ValueError("Publication recipes and executable labs differ")
    count = 0
    for lab, spec in variants.items():
        recipe = recipes[lab]
        controls = recipe["publication"].get("controls", [])
        if len(controls) != len(set(controls)) or any(
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) for name in controls
        ):
            raise ValueError("Invalid bounded publication controls")
        policies = recipe["publication"]["policies"]
        if policies.keys() != {v["key"] for v in spec["variants"]}:
            raise ValueError(
                f"Publication policies differ from execution variants for {lab}"
            )
        for row in spec["variants"]:
            policy = policies[row["key"]]
            if policy["kind"] not in KINDS or policy["result_lab"] != lab:
                raise ValueError("Invalid explicit publication policy")
            diagnostic = row["profiler"] != "none" or row["experiment"] == "sanitizer"
            if diagnostic != (policy["kind"] == "stage-diagnostic"):
                raise ValueError("External diagnostics require a status-only policy")
            expected_view = "diagnostic" if "diagnostic" in policy["kind"] else "latest"
            if policy["view"] != expected_view:
                raise ValueError("Publication policy view differs")
            if (
                row["experiment"] == "trials"
                and not diagnostic
                and policy["kind"] != "three-trials"
            ):
                raise ValueError(
                    "Trial execution requires a complete three-trial policy"
                )
            if "trials" in policy["kind"] and policy.get("trials") != [1, 2, 3]:
                raise ValueError("Trial policy requires explicit ordinals 1, 2, 3")
            if policy["kind"].startswith("paired-"):
                roles = policy.get("roles", [])
                if (
                    len(roles) != 2
                    or len(set(roles)) != 2
                    or any(
                        not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", role)
                        for role in roles
                    )
                ):
                    raise ValueError("Paired publication requires two declared roles")
            if policy.get("probe_lab") and policy["probe_lab"] not in recipes:
                raise ValueError("Publication probe has no recipe")
            if (
                not diagnostic
                and recipe["kind"] == "diagnostic"
                and policy["kind"] != "single-diagnostic"
            ):
                raise ValueError("Intrinsic diagnostics require a diagnostic policy")
            count += 1
    return count


def configuration(stage, requested, channel):
    parts = [
        stage.get("experiment", requested["experiment"]),
        stage.get("device", requested.get("device") or "auto"),
    ]
    for key, short in (
        ("nodes", "n"),
        ("tasks", "t"),
        ("gpus_per_task", "g"),
        ("cpus_per_task", "c"),
        ("workers_per_node", "w"),
    ):
        if key in stage.get("scope", {}):
            value = stage["scope"][key]
            if type(value) is not int or value < 0:
                raise ValueError("Invalid execution topology")
            parts.append(f"{short}{value}")
    if channel == "diagnostic":
        parts.append(stage.get("profiler", stage["id"]))
        tool = requested.get("sanitizer")
        if tool:
            parts.append(tool)
    return "_".join(parts)


def validate_job(course, job):
    from course_execution import select
    from course_job import runtime_scope

    if (
        job.get("schema") != "course-job-result/v1"
        or job.get("course") != course.name
        or job.get("status") not in ("completed", "failed", "skipped")
    ):
        raise ValueError("Require terminal native job evidence")
    if not re.fullmatch(r"[1-9][0-9]*", job.get("job_id", "")) or job["requested"][
        "workload"
    ] not in ("small", "large"):
        raise ValueError("Invalid job identity/workload")
    expected = select(
        course,
        job["lab_id"],
        job["requested"],
        job["requested"].get("program_arguments", []),
    )
    if len(expected) != len(job["stages"]):
        raise ValueError("Job stage inventory differs from execution selection")
    for declared, stage in zip(expected, job["stages"], strict=True):
        if declared["status"] != "skipped":
            declared["scope"] = runtime_scope(declared["scope"], job["allocation"])
        for key, value in declared.items():
            if key != "status" and stage.get(key) != value:
                raise ValueError("Job stage metadata differs from execution selection")
        if stage["status"] not in STATUSES or (
            declared["status"] == "skipped" and stage["status"] != "skipped"
        ):
            raise ValueError("Invalid stage outcome")


def original(job_root, entry):
    relative = Path(entry["path"])
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[:1] != ("stages",)
    ):
        raise ValueError("Original result path escapes the native job")
    path = job_root / relative
    # Open once: the parsed document and digest must describe identical bytes.
    if (
        any(p.is_symlink() for p in (path, *path.parents))
        or not path.is_file()
        or path.stat().st_size > MAX_JSON
    ):
        raise ValueError("Unsafe or oversized original result")
    content = path.read_bytes()
    value = json.loads(content)
    actual = hashlib.sha256(content).hexdigest()
    if entry.get("sha256", actual) != actual:
        raise ValueError("Original result changed after publication selection")
    return value, actual


def freeze(course, job_path, identity, directory):
    job = read(job_path)
    validate_job(course, job)
    registration = job.get("publication", {}).get("registration")
    if not registration or registration.get("publisher_id") != identity["publisher_id"]:
        raise ValueError("Job has no registration for this publication installation")
    if read(directory / "runs" / f"{registration['sequence']}.json") != registration:
        raise ValueError("Job registration differs from shared ordering state")
    for key, expected in (
        ("course", course.name),
        ("lab", job["lab_id"]),
        ("job_id", job["job_id"]),
        ("started_at", job["started_at"]),
        ("job_path", str(job_path.absolute())),
    ):
        if registration[key] != expected:
            raise ValueError("Job registration identity differs")
    path = job_path.parent / "publication-snapshot.json"
    recipes = read(course / "reference/observability.json")
    if path.exists():
        snapshot = read(path)
        if snapshot["registration"] != registration or snapshot[
            "recipe_sha256"
        ] != digest(recipes):
            raise ValueError("Frozen publication registration or recipe changed")
        return path, snapshot
    job = copy.deepcopy(job)
    job.pop("publication", None)
    for stage in job["stages"]:
        entries = []
        for entry in stage.get("measurements", []):
            _, checksum = original(job_path.parent, entry)
            entries.append({"path": entry["path"], "sha256": checksum})
        stage["measurements"] = entries
        for key in ("diagnostics", "text_results", "correctness", "trial_summaries"):
            stage.pop(key, None)
    snapshot = {
        "schema": SCHEMA,
        "registration": registration,
        "job": job,
        "recipe_sha256": digest(recipes),
    }
    atomic(path, snapshot)
    return path, snapshot


def stage_policy(recipe, stage):
    if stage["status"] == "skipped":
        return {
            "kind": "stage-diagnostic",
            "view": "diagnostic",
            "result_lab": recipe["lab"],
        }
    return recipe["publication"]["policies"][stage["key"]]


def measurement_rows(course, snapshot, stage, policy):
    """Validate the entire set before exposing even one measurement."""
    from publish_results import comparison_payload, validate_result

    job = snapshot["job"]
    recipes = read(course / "reference/observability.json")["labs"]
    recipe = recipes[job["lab_id"]]
    diagnostic = policy["view"] == "diagnostic"
    if stage["status"] != "completed" or (
        not diagnostic and job["status"] != "completed"
    ):
        return []
    if policy["kind"] == "stage-diagnostic":
        return []
    records = {}
    root = Path(snapshot["registration"]["job_path"]).parent
    for entry in stage["measurements"]:
        if Path(entry["path"]).parts[:2] != ("stages", stage["id"]):
            raise ValueError("Original result belongs to another stage")
        value, _ = original(root, entry)
        lab = value.get("lab_id")
        if lab not in {policy["result_lab"], policy.get("probe_lab")}:
            raise ValueError("Unexpected original result lab in selected stage")
        validate_result(value, lab, diagnostic=diagnostic)
        if value.get("profile") != job["requested"]["workload"] or value["experiment"][
            "slurm_job_id"
        ] != int(job["job_id"]):
            raise ValueError("Original job or workload differs")
        if value["experiment"].get("rank", 0) != 0:
            raise ValueError("Only the authoritative rank-zero result is publishable")
        trial = value["experiment"].get("trial_index", 0)
        role = (
            value["experiment"]["parameters"].get("variant", "result")
            if policy.get("roles")
            else "result"
        )
        if policy.get("roles") and value["measurements"].get("variant", role) != role:
            raise ValueError("Probe role provenance differs")
        key = (lab, trial, role)
        if key in records:
            raise ValueError("Duplicate original role/trial")
        records[key] = value
    trials = policy.get("trials", [0])
    roles = policy.get("roles", ["result"])
    labs = [policy["result_lab"]] + (
        [policy["probe_lab"]] if policy.get("probe_lab") else []
    )
    if set(records) != {
        (lab, trial, role) for lab in labs for trial in trials for role in roles
    }:
        raise ValueError(
            "Original result set does not match the complete publication policy"
        )
    if policy["kind"] == "three-trials":
        values = list(records.values())
        if len({v.get("run_id") for v in values}) != 3 or any(
            not v.get("run_id") for v in values
        ):
            raise ValueError(
                "Capstone trials require distinct completed run identities"
            )
        for (_, trial, _), value in records.items():
            expected = "candidate-first" if trial == 2 else "baseline-first"
            if value["measurements"].get("variant_order") != expected:
                raise ValueError("Capstone trial order differs")
    if policy.get("roles"):
        for lab in labs:
            for trial in trials:
                left, right = (records[(lab, trial, role)] for role in roles)
                comparison_payload(left, right, recipes[lab])
                if policy.get("probe_workload") and lab == policy.get(
                    "probe_lab", policy["result_lab"]
                ):
                    if any(
                        v["experiment"]["parameters"].get("workload")
                        != policy["probe_workload"]
                        for v in (left, right)
                    ):
                        raise ValueError(
                            "Probe workload differs from the declared policy"
                        )
    # Workload/runtime controls must also agree across repetitions (seeds and
    # alternating variant order deliberately differ in capstone trials).
    for lab in labs:
        values = [v for (record_lab, _, _), v in records.items() if record_lab == lab]
        normalized = []
        for value in values:
            varying = {"variant", "variant_order"}
            if policy["kind"] == "three-trials":
                varying.add("seed")
            params = {
                k: v
                for k, v in value["experiment"]["parameters"].items()
                if k not in varying
            }
            normalized.append(
                {
                    "environment": value["environment"],
                    "parameters": params,
                    "seed": None
                    if policy["kind"] == "three-trials"
                    else value.get("seed"),
                }
            )
        if any(v != normalized[0] for v in normalized):
            raise ValueError("Trial runtime or workload controls differ")
    rows = []
    for (lab, trial, role), value in sorted(records.items()):
        if lab != policy["result_lab"]:
            continue
        # Reuse the declared metric extraction, units, case cardinality and
        # qualification gates of the explicit comparison publisher.
        for name, slot, case, number in comparison_payload(value, value, recipe):
            if slot == "baseline":
                rows.append((name, role, str(trial), case, number))
        for name in recipe["publication"].get("controls", []):
            number = value["experiment"]["parameters"].get(name)
            if type(number) in (int, float) and math.isfinite(number):
                rows.append(("control_" + name, role, str(trial), "value", number))
    return rows


def candidates(course, path, snapshot, identity):
    job = snapshot["job"]
    recipe = read(course / "reference/observability.json")["labs"][job["lab_id"]]
    result = []
    for stage in job["stages"]:
        policy = stage_policy(recipe, stage)
        channels = ["diagnostic"]
        if (
            policy["view"] == "latest"
            and stage["status"] == "completed"
            and job["status"] == "completed"
        ):
            channels.append("latest")
        for channel in channels:
            labels = {
                "publisher_id": identity["publisher_id"],
                "workspace": identity["binding"]["workspace"],
                "course": course.name,
                "lab": job["lab_id"],
                "profile": job["requested"]["workload"],
                "configuration": configuration(stage, job["requested"], channel),
            }
            result.append(
                {
                    "key": digest({"channel": channel, **labels})[:32],
                    "channel": channel,
                    "labels": labels,
                    "sequence": snapshot["registration"]["sequence"],
                    "job_id": int(job["job_id"]),
                    "stage": stage["id"],
                    "ended_at_seconds": datetime.fromisoformat(
                        job["finished_at"]
                    ).timestamp(),
                    "snapshot": str(path),
                    "snapshot_sha256": digest(snapshot),
                }
            )
    return result


def payload(course, candidate):
    snapshot = read(Path(candidate["snapshot"]))
    if digest(snapshot) != candidate["snapshot_sha256"]:
        raise ValueError("Frozen publication snapshot changed")
    job = snapshot["job"]
    validate_job(course, job)
    recipes = read(course / "reference/observability.json")
    if snapshot["recipe_sha256"] != digest(recipes):
        raise ValueError("Publication recipes changed after selection")
    stage = next(row for row in job["stages"] if row["id"] == candidate["stage"])
    recipe = recipes["labs"][job["lab_id"]]
    policy = stage_policy(recipe, stage)
    if candidate["channel"] == "latest" or policy["view"] == "diagnostic":
        rows = measurement_rows(course, snapshot, stage, policy)
    else:
        rows = []
    if candidate["channel"] == "diagnostic":
        rows += [
            (
                "stage_status",
                "status",
                "0",
                "not_run" if status == "unstarted" else status,
                int(stage["status"] == status),
            )
            for status in STATUSES
        ]
        rows.append(
            (
                "native_reports",
                "status",
                "0",
                "value",
                len(stage.get("native_reports", [])),
            )
        )
    return rows
