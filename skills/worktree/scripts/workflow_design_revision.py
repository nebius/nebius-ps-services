"""Failure-driven canonical design revision for a quiescent, unmerged worker wave."""

from pathlib import Path
import re
import subprocess
import sys

import workflow_execution as engine
from workflow_objectives import authorize_coordinator, check_dispatch, _writers_quiescent, validate_run
from workflow_publication import _basis
from workflow_reconcile import termination, observe_termination, validate_retained_wave
from workflow_resources import require_released
from workflow_state import digest, read, write


def _fail(message):
    raise engine.ExecutionError("DESIGN_REVISION_INVALID", message)


def _directory(run_dir, execution, revision):
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{64}", revision) is None:
        _fail("revision identity is invalid")
    return engine.execution_dir(run_dir, execution) / "design-revisions" / revision


def _authority(run_dir, execution, classification, dispatch, *, active=True):
    scripts = Path(__file__).resolve().parents[2] / "sdlc-classify-failure/scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from repair_control import RepairControlError, prepared_design_authority
    try:
        return prepared_design_authority(run_dir, execution, classification, dispatch,
                                         require_active=active)
    except RepairControlError as error:
        raise engine.ExecutionError(error.code, str(error)) from error


def _save(path, value):
    receipt = {**value, "sha256": digest(value)}
    if path.exists():
        if read(path) != receipt:
            _fail("immutable design revision receipt changed")
    else:
        write(path, receipt, exclusive=True)
    return receipt


def _read(path, schema):
    value = read(path)
    unsigned = {key: item for key, item in value.items() if key != "sha256"}
    if value.get("schema") != schema or digest(unsigned) != value.get("sha256"):
        _fail("design revision receipt is invalid")
    return value


def _specs(coordinator):
    integration = Path(coordinator["integration_worktree"])
    project = integration / coordinator["project_scope"]
    from project_specs_lib.contracts import _read_file
    values = {kind: _read_file(project / "docs" / f"{kind}.md", kind)[0]
              for kind in ("requirements", "design")}
    if engine.inspect_project_spec_pair(values["requirements"], values["design"])["status"] != "current":
        _fail("canonical pair is not current")
    return project, {kind: engine.sha256_bytes(value) for kind, value in values.items()}


def _checkout(run_dir, execution, coordinator, *, strict=True):
    from workflow_spec_checkout import SpecCheckoutError, resolve_spec_checkout
    try:
        target = resolve_spec_checkout(run_dir, Path(coordinator["selected_project_root"]),
                                       execution_id=execution, strict_head=strict)
    except SpecCheckoutError as error:
        raise engine.ExecutionError(error.code, str(error)) from error
    if target != Path(coordinator["integration_worktree"]) / coordinator["project_scope"]:
        _fail("canonical revision checkout is not the registered integration")


def _reconcile(run_dir, execution, intent):
    """Publish immutable proof before updating projections; replay preserves prior status."""
    for row in intent["writers"]:
        proof_path = run_dir / row["proof_path"]
        proof = row["proof"]
        if proof_path.exists():
            if read(proof_path) != proof:
                _fail("retained writer proof changed")
        else:
            write(proof_path, proof, exclusive=True)
        state_path = engine.task_path(run_dir, execution, intent["wave_id"], row["task_id"])
        state = read(state_path)
        state["status"] = "superseded"
        write(state_path, state)
    retention = {
        "schema": "workflow/retained-wave-v1", "wave_id": intent["wave_id"],
        "assignments": [{"task_id": row["task_id"], "assignment_digest": row["proof"]["assignment_digest"],
                         "disposition": "retained-recovery", "proof_sha256": digest(row["proof"]),
                         "proof_path": row["proof_path"]} for row in intent["writers"]],
    }
    path = engine.execution_dir(run_dir, execution) / "retentions" / (intent["wave_id"] + ".json")
    if path.exists():
        if read(path) != retention:
            _fail("retained wave changed")
    else:
        write(path, retention, exclusive=True)
    wave_path = engine.wave_path(run_dir, execution, intent["wave_id"])
    wave = read(wave_path)
    wave.update(status="superseded", ready_task_ids=[], active_task_ids=[],
                superseded_task_ids=list(wave["task_ids"]), design_revision_id=intent["revision_id"])
    write(wave_path, wave)
    validate_retained_wave(run_dir, execution, wave)


def prepare_design_revision(run_dir, execution, classification_id, dispatch_id, terminations, session):
    """Consume a counted design dispatch and retain exact failed work without a steering fence."""
    with engine._execution_transition_lock(run_dir, execution):
        from workflow_validation import require_quiescent
        require_quiescent(run_dir, execution)
        _run, objective = authorize_coordinator(run_dir, session)
        check_dispatch(run_dir)
        authority = _authority(run_dir, execution, classification_id, dispatch_id)
        coordinator = engine._load_coordinator(run_dir, execution)
        directory = _directory(run_dir, execution, dispatch_id)
        intent_path = directory / "prepare.json"
        integration = Path(coordinator["integration_worktree"])
        _checkout(run_dir, execution, coordinator)
        if intent_path.exists():
            intent = _read(intent_path, "workflow/design-revision-prepare-v1")
            if (intent["authority"] != authority or intent["execution_id"] != execution
                    or intent["objective_sha256"] != objective["revisions"][-1]["sha256"]
                    or intent["basis_sha256"] != _basis(run_dir, execution)
                    or intent["revision_id"] != dispatch_id
                    or intent["parent"] != engine.head(integration)):
                _fail("prepared revision identity is stale")
            # Publication may have happened, but another seal or plan must not be overwritten.
            if coordinator.get("design_revision_id") not in {None, dispatch_id} or (directory / "sealed.json").exists():
                _fail("revision is already sealed or another revision is active")
            wave = read(engine.wave_path(run_dir, execution, intent["wave_id"]))
            if wave.get("integrated_task_ids") or wave["status"] in {"integrating", "integrated", "done", "failed"}:
                _fail("interrupted retirement acquired integration effects")
            if {str(path) for path in terminations} != {row["termination_path"] for row in intent["writers"]}:
                _fail("prepared termination selection changed")
            for row in intent["writers"]:
                assignment, _state, observation = termination(
                    run_dir, execution, intent["wave_id"], row["task_id"],
                    Path(row["termination_path"]), session)
                if (digest(observation) != row["proof"]["termination_sha256"]
                        or assignment["assignment_digest"] != row["proof"]["assignment_digest"]):
                    _fail("prepared writer observation changed")
        else:
            engine._verify_execution_objective_impact(run_dir, execution, coordinator)
            if (coordinator["status"] != "waves_running" or not coordinator.get("active_wave")
                    or coordinator.get("design_revision_id") is not None
                    or engine.head(integration) != coordinator["integration_head"]
                    or not engine.clean(integration)):
                _fail("design preparation requires a clean active worker wave at the recorded tip")
            _project, before = _specs(coordinator)
            basis = _basis(run_dir, execution)
            expected = {**{kind: "sha256:" + value for kind, value in before.items()},
                        "plan": "sha256:" + coordinator["plan_digest"], "basis": "sha256:" + basis}
            if (authority["integration_commit"] != coordinator["integration_head"]
                    or any(authority["fingerprints"].get(key) != value for key, value in expected.items())):
                _fail("failure belongs to another integration, plan, canonical snapshot or basis")
            wave_id = coordinator["active_wave"]
            wave = read(engine.wave_path(run_dir, execution, wave_id))
            if wave.get("integrated_task_ids") or wave["status"] in {"integrating", "integrated", "done", "failed", "superseded"}:
                _fail("partial or completed integration cannot be retired as unmerged work")
            for path in (run_dir / "execution").glob("*/waves/*.json"):
                if path != engine.wave_path(run_dir, execution, wave_id) and read(path).get("active_task_ids"):
                    _fail("another wave still owns active writers")
            supplied = {}
            for evidence in terminations:
                observation = read(evidence)
                key = observation.get("assignment_digest")
                if key in supplied:
                    _fail("duplicate termination observation")
                supplied[key] = evidence
            writers = []
            for task in wave["task_ids"]:
                path = engine.assignment_path(run_dir, execution, wave_id, task)
                if not path.exists():
                    continue
                assignment = read(path)
                evidence = supplied.pop(assignment["assignment_digest"], None)
                if evidence is None:
                    _fail("every allocated writer requires its exact native termination observation")
                assignment, state, observation = termination(run_dir, execution, wave_id, task, evidence, session)
                if assignment["basis_sha256"] != basis or assignment["base_head"] != coordinator["integration_head"]:
                    _fail("allocated writer belongs to another basis or parent")
                proof = {"schema": "workflow/writer-reconciliation-v1", "execution_id": execution,
                         "wave_id": wave_id, "task_id": task, "assignment_digest": assignment["assignment_digest"],
                         "basis_sha256": basis, "termination_sha256": digest(observation),
                         "retained_worktree": assignment["worktree"], "retained_branch": assignment["branch"],
                         "disposition": "superseded", "prior_status": state["status"],
                         "design_revision_id": dispatch_id}
                proof_path = engine.execution_dir(run_dir, execution) / "reconciliations" / f"{wave_id}-{task}.json"
                writers.append({"task_id": task, "termination_path": str(evidence), "proof": proof,
                                "proof_path": str(proof_path.relative_to(run_dir))})
            if supplied or not writers:
                _fail("termination set does not match the allocated failed wave")
            require_released(run_dir)
            intent = _save(intent_path, {
                "schema": "workflow/design-revision-prepare-v1", "execution_id": execution,
                "revision_id": dispatch_id, "authority": authority, "wave_id": wave_id,
                "parent": coordinator["integration_head"], "before": before,
                "plan_sha256": coordinator["plan_digest"],
                "objective_sha256": objective["revisions"][-1]["sha256"], "basis_sha256": basis,
                "writers": writers,
            })
        require_released(run_dir)
        _reconcile(run_dir, execution, intent)
        _writers_quiescent(run_dir)
        coordinator.update(status="design_revision_pending", active_wave=None, design_revision_id=dispatch_id)
        engine._save_coordinator(run_dir, execution, coordinator)
        return intent


def _verify_prepared(run_dir, execution, prepared, authority):
    from workflow_impact import _identity_files
    coordinator = engine._load_coordinator(run_dir, execution)
    basis = read(run_dir / "bases" / execution / (prepared["basis_sha256"] + ".json"))
    if (engine.canonical_digest(basis) != prepared["basis_sha256"]
            or basis["identities"] != _identity_files(Path(coordinator["selected_project_root"]))):
        _fail("prepared execution instructions or helpers changed")
    expected = {**{kind: "sha256:" + value for kind, value in prepared["before"].items()},
                "basis": "sha256:" + prepared["basis_sha256"],
                "plan": "sha256:" + prepared["plan_sha256"]}
    if (authority != prepared["authority"] or prepared["execution_id"] != execution
            or prepared["revision_id"] != authority["dispatch_id"]
            or prepared["parent"] != authority["integration_commit"]
            or any(authority["fingerprints"].get(key) != value for key, value in expected.items())):
        _fail("prepared revision authority changed")
    for row in prepared["writers"]:
        assignment, _state, observation = observe_termination(
            run_dir, execution, prepared["wave_id"], row["task_id"], Path(row["termination_path"]))
        if (read(run_dir / row["proof_path"]) != row["proof"]
                or assignment["assignment_digest"] != row["proof"]["assignment_digest"]
                or digest(observation) != row["proof"]["termination_sha256"]):
            _fail("original retained writer proof changed")


def observe_pending_revision(run_dir, execution):
    """Verify a pending revision without mutation or adopting the coordinator identity."""
    _run, objective = validate_run(run_dir)
    check_dispatch(run_dir)
    coordinator = engine._load_coordinator(run_dir, execution)
    directory = _directory(run_dir, execution, coordinator.get("design_revision_id"))
    prepared = _read(directory / "prepare.json", "workflow/design-revision-prepare-v1")
    if (coordinator["status"] != "design_revision_pending"
            or coordinator.get("active_wave") is not None
            or coordinator.get("promoted_head") is not None
            or (directory / "sealed.json").exists()
            or prepared["revision_id"] != coordinator["design_revision_id"]
            or prepared["parent"] != coordinator["integration_head"]
            or prepared["plan_sha256"] != coordinator["plan_digest"]
            or prepared["basis_sha256"] != _basis(run_dir, execution)
            or prepared["objective_sha256"] != objective["revisions"][-1]["sha256"]):
        _fail("observation requires the exact pending unchanged execution")
    authority = _authority(run_dir, execution, prepared["authority"]["classification_id"],
                           prepared["revision_id"])
    _verify_prepared(run_dir, execution, prepared, authority)
    _checkout(run_dir, execution, coordinator)
    for row in prepared["writers"]:
        state = read(engine.task_path(run_dir, execution, prepared["wave_id"], row["task_id"]))
        if (state["status"] != "superseded"
                or row["proof"].get("design_revision_id") != prepared["revision_id"]):
            _fail("pending revision retains a writer outside its superseded set")
    wave = read(engine.wave_path(run_dir, execution, prepared["wave_id"]))
    if (wave["status"] != "superseded" or wave.get("active_task_ids")
            or wave.get("integrated_task_ids")
            or wave.get("design_revision_id") != prepared["revision_id"]
            or {row["task_id"] for row in prepared["writers"]} != {
                row["task_id"] for row in validate_retained_wave(run_dir, execution, wave)["assignments"]}):
        _fail("pending revision requires its exact retained unmerged wave")
    _writers_quiescent(run_dir)
    require_released(run_dir)
    return prepared


def _git(integration, *arguments):
    return engine.git(integration, list(arguments), "seal canonical design revision")


def _changed_paths(integration, parent):
    # Include staged, unstaged and untracked names, including both sides of renames.
    tracked = _raw_git(integration, "diff", "--no-renames", "--name-only", "-z", parent).decode().split("\0")
    staged = _raw_git(integration, "diff", "--cached", "--no-renames", "--name-only", "-z", parent).decode().split("\0")
    untracked = _raw_git(integration, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    return {item for item in tracked + staged + untracked if item}


def _raw_git(integration, *arguments):
    try:
        return subprocess.run(["git", *arguments], cwd=integration, env=engine.git_environment(integration),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise engine.ExecutionError("GIT_OPERATION_FAILED", "canonical revision Git observation failed") from error


def seal_design_revision(run_dir, execution, revision_id, operation_id, message, session):
    """Commit only the semantic owner's exact admitted pair; recover the exact commit after interruption."""
    from project_specs_lib.transaction import read_spec_publication
    from project_specs_lib.contracts import ProjectSpecError
    with engine._execution_transition_lock(run_dir, execution):
        from workflow_validation import require_quiescent
        require_quiescent(run_dir, execution)
        _run, objective = authorize_coordinator(run_dir, session)
        check_dispatch(run_dir)
        engine._reject_sensitive_evidence(message)
        if not isinstance(message, str) or not message.strip() or "\n" in message or "\r" in message:
            _fail("one nonempty commit subject is required")
        directory = _directory(run_dir, execution, revision_id)
        prepared = _read(directory / "prepare.json", "workflow/design-revision-prepare-v1")
        sealed_path = directory / "sealed.json"
        authority = _authority(run_dir, execution, prepared["authority"]["classification_id"],
                               revision_id, active=not sealed_path.exists())
        _verify_prepared(run_dir, execution, prepared, authority)
        coordinator = engine._load_coordinator(run_dir, execution)
        integration = Path(coordinator["integration_worktree"])
        _checkout(run_dir, execution, coordinator, strict=False)
        project, after = _specs(coordinator)
        _writers_quiescent(run_dir)
        require_released(run_dir)
        validate_retained_wave(run_dir, execution, read(engine.wave_path(run_dir, execution, prepared["wave_id"])))
        try:
            publication = read_spec_publication(project, operation_id)
        except (ProjectSpecError, OSError) as error:
            raise engine.ExecutionError("DESIGN_REVISION_INVALID", "canonical publication proof is invalid") from error
        if (publication["git_head"] != prepared["parent"] or publication["before"] != prepared["before"]
                or publication["after"] != after or after == prepared["before"]
                or publication["workflow_identity"] != {"objective_sha256": prepared["objective_sha256"],
                                                        "basis_sha256": prepared["basis_sha256"]}):
            _fail("publication does not match the prepared revision")
        current = engine.head(integration)
        if sealed_path.exists():
            sealed = _read(sealed_path, "workflow/design-revision-sealed-v1")
            intent = _read(directory / "commit.json", "workflow/design-revision-commit-v1")
            if (sealed["head"] != current or sealed["message"] != message
                    or coordinator.get("design_revision_id") not in {None, revision_id}
                    or sealed["publication_sha256"] != digest(publication)
                    or sealed["prepare_sha256"] != prepared["sha256"]
                    or any(sealed.get(key) != intent.get(key) for key in (
                        "revision_id", "parent", "tree", "message", "prepare_sha256", "publication_sha256"))
                    or _git(integration, "rev-list", "--parents", "-n", "1", current).split() != [current, prepared["parent"]]
                    or _git(integration, "rev-parse", current + "^{tree}") != sealed["tree"]
                    or not engine.clean(integration)):
                _fail("sealed revision replay changed")
            if coordinator.get("design_revision_id") == revision_id:
                _finish(run_dir, execution, coordinator, sealed)
            elif sealed["sha256"] not in coordinator.get("design_revisions", []):
                _fail("sealed revision is absent from coordinator history")
            return sealed
        if (coordinator.get("design_revision_id") != revision_id
                or coordinator["status"] != "design_revision_pending"
                or coordinator["integration_head"] != prepared["parent"]
                or _basis(run_dir, execution) != prepared["basis_sha256"]
                or objective["revisions"][-1]["sha256"] != prepared["objective_sha256"]):
            _fail("design seal requires its pending unchanged execution")
        allowed = {(Path(coordinator["project_scope"]) / "docs" / f"{kind}.md").as_posix()
                   for kind in ("requirements", "design")}
        commit_path = directory / "commit.json"
        binding = {"schema": "workflow/design-revision-commit-v1", "revision_id": revision_id,
                   "parent": prepared["parent"], "message": message,
                   "prepare_sha256": prepared["sha256"], "publication_sha256": digest(publication)}
        if current == prepared["parent"]:
            if not _changed_paths(integration, current) <= allowed:
                _fail("unrelated dirty or staged paths prevent canonical sealing")
            _git(integration, "add", "-A")
            staged = set(filter(None, _raw_git(integration, "diff", "--cached", "--no-renames", "--name-only", "-z").decode().split("\0")))
            if not staged or not staged <= allowed:
                _fail("staged paths differ from the canonical pair")
            for kind in ("requirements", "design"):
                path = (Path(coordinator["project_scope"]) / "docs" / f"{kind}.md").as_posix()
                blob = _raw_git(integration, "show", ":" + path)
                if engine.sha256_bytes(blob) != after[kind]:
                    _fail("staged canonical bytes changed")
            tree = _git(integration, "write-tree")
            intent = _save(commit_path, {**binding, "tree": tree})
            if _specs(coordinator)[1] != after or engine.head(integration) != prepared["parent"]:
                _fail("canonical bytes or parent changed before commit")
            _git(integration, "commit", "-m", message)
            current = engine.head(integration)
        else:
            intent = _read(commit_path, "workflow/design-revision-commit-v1")
            if any(intent.get(key) != value for key, value in binding.items()):
                _fail("interrupted commit authority changed")
        if (_git(integration, "rev-list", "--parents", "-n", "1", current).split() != [current, prepared["parent"]]
                or _git(integration, "show", "-s", "--format=%s", current) != message
                or _git(integration, "rev-parse", current + "^{tree}") != intent["tree"]
                or not engine.clean(integration)):
            _fail("commit is not the exact journaled clean direct child")
        sealed = _save(sealed_path, {**binding, "schema": "workflow/design-revision-sealed-v1",
                                    "head": current, "tree": intent["tree"], "after": after})
        _finish(run_dir, execution, coordinator, sealed)
        return sealed


def _finish(run_dir, execution, coordinator, sealed):
    if coordinator.get("integration_head") not in {sealed["parent"], sealed["head"]}:
        _fail("integration advanced beyond this design seal")
    coordinator.update(integration_head=sealed["head"], status="waves_running", active_wave=None,
                       design_revision_id=None)
    revisions = coordinator.setdefault("design_revisions", [])
    if sealed["sha256"] not in revisions:
        revisions.append(sealed["sha256"])
    engine._save_coordinator(run_dir, execution, coordinator)
