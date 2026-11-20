"""Frozen source fixtures and read-only phased-program qualification.

This observer never authors an SDLC program, accepts an objective, completes a
slice or changes a checkpoint. Those transitions belong to the native workflow.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import three_tier_target as target

SLICE_PHASES = (
    "sdlc-create-plan", "sdlc-prepare-execution", "sdlc-tdd", "sdlc-implement-plan",
    "sdlc-validate-codes", "sdlc-unit-tests", "sdlc-evaluate",
    "sdlc-update-documents", "align", "sdlc-commit", "local-ship",
)
REVISION_PHASES = (
    "sdlc-create-requirements", "sdlc-gather-context", "sdlc-create-design", "sdlc-auto-steering",
)
FINAL_PHASES = ("sdlc-uat-tests", "post-uat-documents")
SUPPLIED_PHASES = [
    {"id": "Phase-1", "depends_on": [], "exit_criteria": ["AC-CREATE", "AC-BLANK"]},
    {"id": "Phase-2", "depends_on": ["Phase-1"], "exit_criteria": ["AC-COMPLETE", "AC-FILTER"]},
    {"id": "Phase-3", "depends_on": ["Phase-2"], "exit_criteria": ["AC-RUNTIME", "AC-DOCS"]},
]
REQUIREMENTS = {
    "schema": "three-tier/source-requirements-v1",
    "outcome": "A browser task board with a Django/Gunicorn API and PostgreSQL",
    "acceptance": [
        {"id": "AC-CREATE", "text": "Create and list tasks through GUI and versioned API; stable database IDs."},
        {"id": "AC-BLANK", "text": "Normalize titles and reject blank input in GUI and API without a database write."},
        {"id": "AC-COMPLETE", "text": "Complete a task through the GUI and persist its state."},
        {"id": "AC-FILTER", "text": "Filter active and completed tasks through the shared domain function and API."},
        {"id": "AC-RUNTIME", "text": "Two private Compose services, health/readiness, migrations, non-root pinned web image and restart persistence."},
        {"id": "AC-DOCS", "text": "Document start, endpoint discovery, health, browser checks, reset and troubleshooting."},
    ],
}


def source_files(mode):
    if mode not in {"supplied", "derived"}:
        raise target.TargetError("Planning mode must be supplied or derived.")
    values = {"docs/contracts/task-board-source.json": REQUIREMENTS}
    if mode == "supplied":
        values["docs/contracts/supplied-phases.json"] = {"phases": SUPPLIED_PHASES}
    return {path: (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
            for path, value in values.items()}


def seed(project, mode):
    files = source_files(mode)
    for relative, content in files.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return {"mode": mode, "files": {p: hashlib.sha256(b).hexdigest() for p, b in files.items()}}


def observation_key(phase, objective, execution):
    if phase == "sdlc-start":
        return (phase, None, None)
    return (phase, objective, execution)


def execution_for_phase(state, phase, *, agent, coordinator_session=None):
    if phase == "sdlc-start" or phase in REVISION_PHASES:
        return None
    current = target._registration(state, agent, coordinator_session=coordinator_session)[5]
    execution = current.get("current_execution")
    if execution is None and phase == "sdlc-classify-failure":
        return None
    if not isinstance(execution, str) or re.fullmatch(r"(?:FEAT|SLICE)-[0-9]{3,}", execution) is None:
        raise target.TargetError("Phase observation needs an owner-selected execution.")
    return execution


def historical_identity(state, result, *, agent):
    """Validate a previously admitted observation; never grant live access."""
    _, _, _, _, run_dir, _, _ = target._registration(state, agent)
    boundary = Path(state["run_root"])
    with target.workflow_imports(Path(__file__).resolve().parents[2]):
        from workflow_objectives import validate_run
        from workflow_delivery import verify_completion, LOCAL_COMPLETION
        from workflow_impact import canonical_digest
        _, accepted = validate_run(run_dir, owner="agentic-sdlc")
        if result["objective_sha256"] not in {r["sha256"] for r in accepted["revisions"]}:
            raise target.TargetError("Historical observation is outside accepted revision ancestry.")
        execution = result["execution_id"]
        if execution is None:
            if result["basis_sha256"] is not None:
                raise target.TargetError("Revision observation cannot borrow an execution basis.")
            return
        completion_path = run_dir / "delivery" / execution / "completion.json"
        if not completion_path.exists():
            # Existing single-execution repair history still uses its exact
            # current target and the original narrow lineage validator.
            observation = ({"phase_observation": result["phase"]}
                           if result["phase"] in target.PHASE_OBSERVATION_PURPOSES else {})
            current = target.resolve_target(state, skills_root=Path(__file__).resolve().parents[2],
                                            agent=agent, require_clean=False, **observation)
            if (current["execution_id"] != execution
                    or target.current_objective(state, agent=agent) != result["objective_sha256"]):
                raise target.TargetError("Historical execution has no owner completion proof.")
            latest, promoted = current["basis_sha256"], state["git"].get("promoted_sha")
        else:
            completed = verify_completion(run_dir, target._private_json(completion_path, boundary))
            if completed["schema"] != LOCAL_COMPLETION or completed["objective_sha256"] != result["objective_sha256"]:
                raise target.TargetError("Historical observation belongs to another completed revision.")
            latest, promoted = completed["basis"]["sha256"], completed["result_sha"]
            if state["git"].get("aggregate_sha"):
                from workflow_automatic import historical_execution
                basis = historical_execution(run_dir, execution)
                if canonical_digest(basis) != latest:
                    raise target.TargetError("Aggregate changed its completed slice basis.")
        if result["basis_sha256"] is not None and result["basis_sha256"] != latest:
            target._validate_basis_lineage(state, run_dir, execution, result["basis_sha256"], latest,
                phase=result["phase"], recorded_head=result["recorded_head"], promoted_head=promoted)
        if promoted:
            target._run(["git", "-C", state["project_root"], "merge-base", "--is-ancestor",
                         result["recorded_head"], promoted], target.owned_git_origin.environment(Path(state["project_root"])))


def _validate_fixture_provenance(program, fixture):
    bundle = program["bundle"]
    planning = bundle["planning"]
    if (planning["mode"] != fixture["mode"]
            or not set(fixture["files"]) <= set(planning["sources"])):
        raise target.TargetError("Program planning provenance omits the frozen sources.")
    coverage = {row["source_id"]: row for row in bundle["coverage"]}
    source_hash = fixture["files"]["docs/contracts/task-board-source.json"]
    for requirement in REQUIREMENTS["acceptance"]:
        identity = requirement["id"]
        if (coverage.get(identity, {}).get("disposition") != "required"
                or bundle["obligation_basis"].get(identity, {}).get("source_sha256") != source_hash):
            raise target.TargetError("Every frozen fixture obligation must be required from its original source.")


def observe(state, *, agent, complete=True):
    """Recompute program and completion proof from workflow owners."""
    _, _, _, _, run_dir, _, env = target._registration(state, agent)
    with target.workflow_imports(Path(__file__).resolve().parents[2]):
        from workflow_program import accepted, verify_inputs, require_complete, completion_project
        from workflow_delivery import verify_completion, LOCAL_COMPLETION
        from workflow_state import read, digest
        run, latest, objective = accepted(run_dir)
        project = completion_project(run_dir)
        program = verify_inputs(run_dir, project)
        if program is None or objective["completion"]["boundary"] != "local-promoted":
            raise target.TargetError("Three-tier qualification requires an admitted local phased program.")
        fixture = state.get("planning_fixture")
        if not isinstance(fixture, dict) or fixture != {
                "mode": fixture.get("mode"),
                "files": {p: hashlib.sha256(b).hexdigest() for p, b in source_files(fixture.get("mode")).items()}}:
            raise target.TargetError("Frozen planning fixture changed.")
        for path, fingerprint in fixture["files"].items():
            from project_specs_lib.source_bundle import read_source
            if hashlib.sha256(read_source(project, path)).hexdigest() != fingerprint:
                raise target.TargetError("Frozen source fixture changed.")
            baseline = target._run(["git", "-C", state["project_root"], "rev-parse",
                                   state["git"]["baseline_sha"] + ":project-a/" + path], env)
            current = target._run(["git", "-C", str(project), "hash-object", path], env)
            if baseline != current:
                raise target.TargetError("Source fixture differs from its frozen baseline.")
        planning = program["bundle"]["planning"]
        _validate_fixture_provenance(program, fixture)
        definition = program["definition"]
        if fixture["mode"] == "supplied" and definition["phases"] != SUPPLIED_PHASES:
            raise target.TargetError("Supplied phase boundaries were not preserved.")
        if len(definition["phases"]) < 2 or len(definition["slices"]) < 3:
            raise target.TargetError("Native phase qualification requires multiple phases and at least three bounded slices.")
        if complete:
            require_complete(run_dir)
        slices = []
        for selected in definition["slices"]:
            path = run_dir / "delivery" / selected["id"] / "completion.json"
            if not path.exists() and not complete:
                continue
            receipt = verify_completion(run_dir, read(path))
            if receipt["schema"] != LOCAL_COMPLETION:
                raise target.TargetError("Disposable slices must finish locally.")
            slices.append({"id": selected["id"], "phase": selected["phase"],
                           "objective_sha256": receipt["objective_sha256"],
                           "completion_sha256": digest(receipt), "result_sha": receipt["result_sha"]})
        exits = {}
        for phase in definition["phases"]:
            path = run_dir / "program/phase-exits" / (phase["id"] + ".json")
            if path.exists():
                from workflow_delivery import verify_phase_exit
                value = read(path)
                verify_phase_exit(run_dir, value, phase)
                exits[phase["id"]] = digest(value)
        return {"mode": planning["mode"], "objective_sha256": latest["sha256"],
                "inputs": objective["inputs"], "slices": slices, "phase_exits": exits,
                "slice_order": [s["id"] for s in definition["slices"]],
                "owner_session_hash": run["coordinator_session_sha256"]}


def checkpoint(state, stage, *, agent):
    """Observe a stopped coordinator or a subsequent public continuation."""
    import verification_workspace as workspace
    import three_tier_native as native
    from three_tier_lifecycle import private_json
    if stage not in {"interrupted", "continued"}:
        raise target.TargetError("Unsupported program checkpoint.")
    _, _, _, _, run_dir, _, _ = target._registration(state, agent)
    with target.workflow_imports(Path(__file__).resolve().parents[2]):
        from workflow_objectives import _writers_quiescent
        _writers_quiescent(run_dir)
    program = observe(state, agent=agent, complete=False)
    if not 0 < len(program["slices"]) < len(program["slice_order"]):
        raise target.TargetError("Continuation trial needs completed and future slices.")
    root = Path(state["verification_root"])
    processes = workspace._native_records(workspace._json(root / workspace.NATIVE))
    if not processes:
        raise target.TargetError("No owned native invocation was observed.")
    if stage == "interrupted":
        workspace.require_native_absent(root)
        continuation = None
    else:
        prior = read_checkpoint(state, "interrupted")
        if (prior["program"]["owner_session_hash"] == program["owner_session_hash"]
                or {k: v for k, v in prior["program"].items() if k != "owner_session_hash"}
                != {k: v for k, v in program.items() if k != "owner_session_hash"}
                or not any(p not in prior["native_invocations"] for p in processes)):
            raise target.TargetError("Continuation must retain the same run/program under a fresh native coordinator.")
        continuation = native.observe_continuation(state, skills_root=Path(__file__).resolve().parents[2], agent=agent)
    value = {"schema": "agentic-sdlc/program-checkpoint-v1", "verification_id": state["verification_id"],
             "run_id": run_dir.name, "stage": stage, "program": program, "native_invocations": processes,
             "native": continuation}
    relative = "evidence/program/" + stage + ".json"
    path = Path(state["run_root"]) / relative
    if path.exists() or path.is_symlink():
        raise target.TargetError("Program checkpoints are immutable; start a fresh trial.")
    private_json(path, value)
    state.setdefault("program_checkpoints", {})[stage] = {
        "path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def read_checkpoint(state, stage):
    reference = state.get("program_checkpoints", {}).get(stage)
    if not isinstance(reference, dict) or reference.get("path") != f"evidence/program/{stage}.json":
        raise target.TargetError("Required native program checkpoint is missing.")
    raw = target._private_bytes(Path(state["run_root"]) / reference["path"], Path(state["run_root"]))
    if hashlib.sha256(raw).hexdigest() != reference.get("sha256"):
        raise target.TargetError("Native program checkpoint changed.")
    value = json.loads(raw)
    if (value.get("schema") != "agentic-sdlc/program-checkpoint-v1" or value.get("stage") != stage
            or value.get("verification_id") != state["verification_id"]):
        raise target.TargetError("Native program checkpoint belongs to another trial.")
    return value


def qualify(state, *, agent):
    final = observe(state, agent=agent)
    interrupted, continued = (read_checkpoint(state, name) for name in ("interrupted", "continued"))
    before, after = interrupted["program"], continued["program"]
    if (interrupted["run_id"] != continued["run_id"]
            or before["owner_session_hash"] == after["owner_session_hash"]
            or {k: v for k, v in before.items() if k != "owner_session_hash"}
            != {k: v for k, v in after.items() if k != "owner_session_hash"}
            or final["owner_session_hash"] != after["owner_session_hash"]
            or final["slices"][:len(before["slices"])] != before["slices"]
            or final["objective_sha256"] == before["objective_sha256"]
            or final["inputs"]["program_sha256"] == before["inputs"]["program_sha256"]):
        raise target.TargetError("Native qualification needs preserved completion, continuation and an accepted changed program.")
    # Recheck that both observed input programs are real accepted ancestors.
    _, _, _, _, run_dir, _, _ = target._registration(state, agent)
    if interrupted["run_id"] != run_dir.name or interrupted.get("native") is not None:
        raise target.TargetError("Program checkpoint belongs to another workflow run.")
    import three_tier_native as native
    native.validate_continuation(state, continued.get("native"),
                                 skills_root=Path(__file__).resolve().parents[2], agent=agent)
    with target.workflow_imports(Path(__file__).resolve().parents[2]):
        from workflow_objectives import validate_run
        from workflow_state import read, digest
        _, objective = validate_run(run_dir, owner="agentic-sdlc")
        revision = next((r for r in objective["revisions"] if r["sha256"] == before["objective_sha256"]), None)
        if revision is None or read(run_dir / revision["snapshot"])["inputs"] != before["inputs"]:
            raise target.TargetError("Observed continuation program is outside accepted ancestry.")
        original = read(run_dir / "program" / (before["inputs"]["program_sha256"] + ".json"))
        if digest(original) != before["inputs"]["program_sha256"]:
            raise target.TargetError("Original accepted program changed.")
    return {**final, "checkpoints": state["program_checkpoints"]}
