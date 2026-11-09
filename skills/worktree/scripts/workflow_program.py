"""One root-owned program, immutable slice definitions, and explicit phase barriers."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

from workflow_state import git_environment, WorkflowError, digest, read, write
from workflow_objectives import validate_run

SPEC_SCRIPTS = Path(__file__).resolve().parents[2] / "maintain-project-specs" / "scripts"
if str(SPEC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SPEC_SCRIPTS))
from project_specs_lib.source_bundle import validate_bundle  # noqa: E402

SCHEMA = "workflow/program-v1"
SLICE = re.compile(r"SLICE-[0-9]{3,}\Z")
PHASE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,79}\Z")
FEATURE = re.compile(r"FEAT-[0-9]{3,}\Z")
REQUIREMENT = re.compile(r"REQ-[0-9]{3,}\Z")


def reject(message):
    raise WorkflowError("PROGRAM_INVALID", message)


def _ids(value, pattern, *, empty=False):
    if (not isinstance(value, list) or not empty and not value
            or any(not isinstance(v, str) or pattern.fullmatch(v) is None for v in value)
            or len(value) != len(set(value))):
        reject("program identifiers must be unique bounded lists")
    return set(value)


def _acyclic(rows):
    remaining = {r["id"]: len(r["depends_on"]) for r in rows}
    children = {k: [] for k in remaining}
    for row in rows:
        for parent in row["depends_on"]:
            if parent not in children:
                reject("unknown program dependency")
            children[parent].append(row["id"])
    queue = [k for k, n in remaining.items() if not n]
    visited = 0
    while queue:
        visited += 1
        for child in children[queue.pop()]:
            remaining[child] -= 1
            if not remaining[child]:
                queue.append(child)
    if visited != len(rows):
        reject("program dependency cycle")


def validate_definition(value, coverage):
    if (not isinstance(value, dict) or set(value) != {"schema", "phases", "slices"}
            or value["schema"] != SCHEMA or not isinstance(value["phases"], list)
            or not value["phases"] or not isinstance(value["slices"], list) or not value["slices"]):
        reject("program must contain explicit phases and delivery slices")
    obligations = {r["source_id"]: r for r in coverage if r["disposition"] == "required"}
    phase_ids, slice_ids = set(), set()
    for row in value["phases"]:
        if not isinstance(row, dict) or set(row) != {"id", "depends_on", "exit_criteria"}:
            reject("phase definition is invalid")
        _ids([row["id"]], PHASE)
        _ids(row["depends_on"], PHASE, empty=True)
        if row["id"] in phase_ids:
            reject("duplicate phase identity")
        phase_ids.add(row["id"])
        if (not isinstance(row["exit_criteria"], list) or not row["exit_criteria"]
                or any(not isinstance(c, str) or c not in obligations for c in row["exit_criteria"])
                or len(set(row["exit_criteria"])) != len(row["exit_criteria"])):
            reject("phase exit criteria must reference required source obligations")
    for row in value["slices"]:
        if not isinstance(row, dict) or set(row) != {"id", "phase", "depends_on", "feature_ids", "requirement_ids", "criteria", "regression_criteria", "change_kind"}:
            reject("slice definition is invalid")
        _ids([row["id"]], SLICE)
        _ids(row["depends_on"], SLICE, empty=True)
        features = _ids(row["feature_ids"], FEATURE)
        requirements = _ids(row["requirement_ids"], REQUIREMENT)
        if row["id"] in slice_ids or not isinstance(row["phase"], str) or row["phase"] not in phase_ids:
            reject("duplicate slice or unknown phase")
        if not set(row["depends_on"]) <= slice_ids:
            reject("slice declaration order must be topological")
        slice_ids.add(row["id"])
        if not isinstance(row["change_kind"], str) or row["change_kind"] not in {"non-runtime", "runtime", "deployment", "security", "data"}:
            reject("slice change classification is invalid")
        if (not isinstance(row["criteria"], list) or not row["criteria"]
                or any(not isinstance(c, str) or c not in obligations for c in row["criteria"])
                or len(set(row["criteria"])) != len(row["criteria"])):
            reject("slice requires explicit source acceptance coverage")
        if (not isinstance(row["regression_criteria"], list)
                or any(not isinstance(c, str) or c not in obligations for c in row["regression_criteria"])
                or len(set(row["regression_criteria"])) != len(row["regression_criteria"])):
            reject("slice regression criteria must reference source obligations")
        for criterion in row["criteria"]:
            if not set(obligations[criterion]["features"]) <= features or not set(obligations[criterion]["requirements"]) <= requirements:
                reject("slice mappings do not cover its declared criteria")
    _acyclic(value["phases"])
    _acyclic(value["slices"])
    phases = {r["id"]: r for r in value["phases"]}
    for phase_id, phase in phases.items():
        members = [s for s in value["slices"] if s["phase"] == phase_id]
        coverage_ids = {c for s in members for c in s["criteria"] + s["regression_criteria"]}
        if members and not set(phase["exit_criteria"]) <= coverage_ids:
            reject("phase exit criteria must be executed by its delivery slices")
    for index, row in enumerate(value["slices"]):
        ancestors, pending = set(), list(phases[row["phase"]]["depends_on"])
        while pending:
            parent = pending.pop()
            if parent not in ancestors:
                ancestors.add(parent)
                pending.extend(phases[parent]["depends_on"])
        if any(later["phase"] in ancestors for later in value["slices"][index:]):
            reject("slice declaration order violates a phase barrier")
    # Every required obligation remains scheduled, even when detailed tasks are deferred.
    scheduled = {c for r in value["phases"] for c in r["exit_criteria"]} | {c for r in value["slices"] for c in r["criteria"]}
    if scheduled != set(obligations):
        reject("required source obligations are missing from program coverage")
    return value


def accepted(run_dir):
    run, objective = validate_run(run_dir)
    latest = objective["revisions"][-1]
    return run, latest, read(run_dir / latest["snapshot"])


def admit_inputs(run_dir):
    """Stage only the exact validated portable snapshot and canonical pair."""
    from workflow_objectives import _git, check_dispatch
    check_dispatch(run_dir)
    run, _latest, snapshot = accepted(run_dir)
    inputs = snapshot["inputs"]
    if run["owner"] != "agentic-sdlc" or inputs is None:
        reject("document admission requires accepted SDLC inputs")
    project, repo = Path(run["project_root"]), Path(run["git_root"])
    bundle = validate_bundle(project, inputs["manifest"], inputs["manifest_sha256"])
    allowed = {str(project.relative_to(repo) / p) for p in bundle["paths"] + ["docs/requirements.md", "docs/design.md"]}
    head = _git(repo, "rev-parse", "HEAD")
    changed = set()
    for arguments in (("diff", "--name-only", "--no-renames", "-z"),
                      ("diff", "--cached", "--name-only", "--no-renames", "-z"),
                      ("ls-files", "--others", "--exclude-standard", "-z")):
        result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, text=True, timeout=30, check=True, env=git_environment(repo))
        changed.update(filter(None, result.stdout.split("\0")))
    if changed - allowed:
        reject("input admission contains unrelated repository changes")
    _git(repo, "add", "-A")
    staged = set(filter(None, _git(repo, "diff", "--cached", "--name-only", "--no-renames", "-z").split("\0")))
    if staged - allowed or _git(repo, "rev-parse", "HEAD") != head:
        reject("input admission scope or HEAD changed during staging")
    for relative in allowed:
        blob = subprocess.run(["git", "-C", str(repo), "show", ":" + relative], capture_output=True, timeout=30, check=True, env=git_environment(repo)).stdout
        from project_specs_lib.source_bundle import read_source
        if blob != read_source(repo, relative):
            reject("admitted index bytes differ from the reviewed source")
    return {"status": "INPUTS_ADMITTED", "paths": sorted(allowed), "head": head,
            "manifest_sha256": inputs["manifest_sha256"]}


def admit(run_dir: Path, candidate: dict):
    run, _latest, snapshot = accepted(run_dir)
    if run.get("status") in {"done", "cancelled"} or run.get("fence") not in {None, "steering"}:
        reject("terminal or cancelled programs cannot be admitted")
    if run.get("fence"):
        from workflow_objectives import _writers_quiescent
        _writers_quiescent(run_dir)
    if run["owner"] != "agentic-sdlc" or snapshot["inputs"] is None:
        reject("program admission requires accepted SDLC document inputs")
    inputs = snapshot["inputs"]
    from workflow_impact import spec_checkout
    project = spec_checkout(run_dir, Path(run["project_root"]))
    bundle = validate_bundle(project, inputs["manifest"], inputs["manifest_sha256"])
    validate_definition(candidate, bundle["coverage"])
    _preserve_delivered_slices(run_dir, candidate)
    if digest(candidate) != inputs["program_sha256"]:
        reject("program differs from the accepted objective; revise and replan")
    path = run_dir / "program" / (inputs["program_sha256"] + ".json")
    if path.exists():
        if read(path) != candidate:
            reject("immutable program definition changed")
    else:
        write(path, candidate, exclusive=True)
    return {"status": "PROGRAM_ADMITTED", "program_sha256": inputs["program_sha256"], **bundle}


def _preserve_delivered_slices(run_dir, candidate):
    """Historical merges cannot be relabeled as delivery of a changed contract."""
    from workflow_delivery import verify_completion
    from workflow_qualification import verify_receipt
    for path in (run_dir / "delivery").glob("*/completion.json"):
        completed = verify_completion(run_dir, read(path))
        receipt = verify_receipt(run_dir, completed["review"]["qualification"], current=False)
        prior_digest = receipt["inputs"]["program_sha256"]
        prior = read(run_dir / "program" / (prior_digest + ".json"))
        if digest(prior) != prior_digest:
            reject("delivered program history changed")
        selected = completed["execution_id"]
        index = next(i for i, row in enumerate(prior["slices"]) if row["id"] == selected)
        if len(candidate["slices"]) <= index or candidate["slices"][index] != prior["slices"][index]:
            reject("completed slice definitions and order are immutable; schedule a new corrective slice")


def verify_inputs(run_dir: Path, project: Path, *, specs=True):
    _run, _latest, snapshot = accepted(run_dir)
    inputs = snapshot["inputs"]
    if inputs is None:
        return None
    bundle = validate_bundle(project, inputs["manifest"], inputs["manifest_sha256"], specs=specs)
    value = read(run_dir / "program" / (inputs["program_sha256"] + ".json"))
    if digest(value) != inputs["program_sha256"]:
        reject("immutable program definition changed")
    validate_definition(value, bundle["coverage"])
    _preserve_delivered_slices(run_dir, value)
    return {"definition": value, "bundle": bundle, "inputs": inputs}


def _delivery(run_dir, slice_id):
    value = read(run_dir / "delivery" / slice_id / "completion.json")
    if value.get("schema") != "workflow/delivery-completion-v1" or value.get("execution_id") != slice_id or value.get("status") != "result-verified":
        raise WorkflowError("DELIVERY_PENDING", "prior slice is not independently merge-verified")
    from workflow_delivery import verify_completion
    verify_completion(run_dir, value)
    return digest(value)


def slice_basis(run_dir: Path, project: Path, execution_id: str, tasks=None):
    inputs = verify_inputs(run_dir, project)
    if inputs is None:
        if SLICE.fullmatch(execution_id):
            reject("slice execution requires an accepted program")
        return None
    program = inputs["definition"]
    selected = next((r for r in program["slices"] if r["id"] == execution_id), None)
    if selected is None:
        reject("execution is not an admitted delivery slice")
    phases = {r["id"]: r for r in program["phases"]}
    proofs = {}
    for phase_id in phases[selected["phase"]]["depends_on"]:
        record = read(run_dir / "program" / "phase-exits" / (phase_id + ".json"))
        if record.get("definition_sha256") != digest(phases[phase_id]):
            reject("phase exit definition changed")
        from workflow_delivery import verify_phase_exit
        verify_phase_exit(run_dir, record, phases[phase_id])
        proofs[phase_id] = digest(record)
    # One candidate at a time: declaration order is the stable delivery order.
    position = program["slices"].index(selected)
    for prior in program["slices"][:position]:
        proofs[prior["id"]] = _delivery(run_dir, prior["id"])
    for dependency in selected["depends_on"]:
        proofs[dependency] = _delivery(run_dir, dependency)
    if tasks is not None:
        features, requirements = set(selected["feature_ids"]), set(selected["requirement_ids"])
        covered, covered_requirements = set(), set()
        for task in tasks:
            if not task.feature_ids or not set(task.feature_ids) <= features or not set(task.requirements) <= requirements:
                reject("task mappings escape frozen slice membership")
            covered.update(task.feature_ids)
            covered_requirements.update(task.requirements)
        if covered != features or covered_requirements != requirements:
            reject("slice tasks omit selected requirement or feature mappings")
    return {"inputs": inputs["inputs"], "slice": selected, "predecessor_evidence": proofs}


def require_complete(run_dir):
    run, _latest, snapshot = accepted(run_dir)
    if snapshot["inputs"] is None:
        return
    from workflow_impact import spec_checkout
    program = verify_inputs(run_dir, spec_checkout(run_dir, Path(run["project_root"])))
    for selected in program["definition"]["slices"]:
        _delivery(run_dir, selected["id"])
    from workflow_delivery import verify_phase_exit
    for phase in program["definition"]["phases"]:
        record = read(run_dir / "program" / "phase-exits" / (phase["id"] + ".json"))
        verify_phase_exit(run_dir, record, phase)
    # Phase exits can intentionally be a subset. Full project completion also
    # needs fresh evidence for every scheduled acceptance/regression obligation.
    from workflow_qualification import verify_receipt
    current_basis = program["bundle"]["obligation_basis"]
    covered = set()
    for selected in program["definition"]["slices"]:
        completion = read(run_dir / "delivery" / selected["id"] / "completion.json")
        receipt = verify_receipt(run_dir, completion["review"]["qualification"], current=False)
        covered.update(c for c in receipt["criteria"] if receipt["obligation_basis"].get(c) == current_basis.get(c))
    required = {c for s in program["definition"]["slices"] for c in s["criteria"] + s["regression_criteria"]}
    if not required <= covered:
        reject("current source obligations still require qualification in a corrective slice")
