"""Execution-owned canonical authoring checkpoints before final validation."""

import sys
from pathlib import Path

import workflow_execution as engine
from workflow_revision import (
    _changed_paths,
    _checkout,
    _git,
    _raw_git,
    _read,
    _save,
    _specs,
)
from workflow_state import digest, read


def _fail(message):
    raise engine.ExecutionError("CANDIDATE_INVALID", message)


def _repair_binding(run_dir, execution):
    """Observe existing authority; never allocate, complete or reset a dispatch."""
    path = run_dir / "repairs" / execution / "repair-control.json"
    if not path.exists():
        return None
    scripts = Path(__file__).resolve().parents[2] / "sdlc-classify-failure/scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import repair_control as owner
    control = owner._load_control(run_dir, execution)
    if control["status"] == "resolved":
        return None
    cursor = control.get("revalidation")
    if (control["status"] != "revalidation_required" or not isinstance(cursor, dict)
            or cursor.get("status") != "pending" or type(cursor.get("cursor")) is not int
            or cursor["cursor"] != 0
            or cursor.get("integration_commit") is not None):
        _fail("checkpoint requires completed repair before its first revalidation gate")
    owner._verify_revalidation_authority(
        run_dir, control, {"cursor_id": cursor["cursor_id"]}, require_current_gate=False)
    return {key: cursor[key] for key in ("cursor_id", "classification_id", "repair_dispatch_id")}


def _quiescent(run_dir, execution, coordinator):
    from workflow_objectives import _writers_quiescent
    from workflow_resources import require_released
    from workflow_validation import require_quiescent
    require_quiescent(run_dir, execution)
    _writers_quiescent(run_dir)
    require_released(run_dir)
    if (coordinator["status"] != "integrated" or coordinator.get("active_wave") is not None
            or coordinator.get("promoted_head") is not None
            or any(not engine._wave_satisfied(run_dir, execution, coordinator, wave)
                   for wave in coordinator["wave_ids"])):
        _fail("checkpoint requires completed and reconciled waves before final sealing")


def checkpoint(run_dir: Path, execution: str, operation_id: str, message: str, *, session: str):
    """Commit one exact paired publication without sealing or promoting delivery."""
    from workflow_state import WorkflowError
    try:
        return _checkpoint(run_dir, execution, operation_id, message, session=session)
    except (engine.ExecutionError, WorkflowError):
        raise
    except RuntimeError as error:
        # Canonical, impact and repair owners expose stable codes. Preserve that
        # code through the execution CLI instead of leaking an untyped traceback.
        raise engine.ExecutionError(getattr(error, "code", "CANDIDATE_INVALID"),
                                    getattr(error, "message", "canonical authoring checkpoint rejected")) from error
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise engine.ExecutionError("CANDIDATE_INVALID", "checkpoint evidence is unavailable or invalid") from error


def _checkpoint(run_dir: Path, execution: str, operation_id: str, message: str, *, session: str):
    from project_specs_lib.transaction import read_spec_publication
    from workflow_impact import verify_current, verify_execution
    from workflow_objectives import authorize_coordinator
    with engine._execution_transition_lock(run_dir, execution):
        _run, objective = authorize_coordinator(run_dir, session)
        coordinator = engine._load_coordinator(run_dir, execution)
        _quiescent(run_dir, execution, coordinator)
        _checkout(run_dir, execution, coordinator, strict=False)
        engine._reject_sensitive_evidence(message)
        if not isinstance(message, str) or not message.strip() or "\n" in message or "\r" in message:
            _fail("checkpoint requires one nonempty commit subject")
        project, after = _specs(coordinator)
        publication = read_spec_publication(project, operation_id)
        impact, _ = verify_current(run_dir, Path(coordinator["selected_project_root"]))
        basis = verify_execution(run_dir, execution, coordinator["plan_digest"],
                                 Path(coordinator["selected_project_root"]))
        if impact["effects"] or impact["plan_action"] != "retain_plan":
            _fail("canonical checkpoint requires a reviewed nonmaterial update")
        identity = publication["workflow_identity"]
        if not isinstance(identity, dict):
            _fail("canonical publication requires workflow identity")
        publication_basis = identity.get("basis_sha256")
        if not isinstance(publication_basis, str) or engine.SHA256_RE.fullmatch(publication_basis) is None:
            _fail("canonical publication requires its frozen workflow basis")
        prior_basis = read(run_dir / "bases" / execution / (publication_basis + ".json"))
        if (engine.canonical_digest(prior_basis) != publication_basis
                or prior_basis.get("execution_id") != execution
                or prior_basis.get("plan_digest") != coordinator["plan_digest"]
                or prior_basis.get("objective_sha256") != objective["revisions"][-1]["sha256"]
                or publication["workflow_identity"].get("objective_sha256") != basis["objective_sha256"]):
            _fail("publication belongs to another objective or plan")
        integration = Path(coordinator["integration_worktree"])
        parent = publication["git_head"]
        paths = {kind: (Path(coordinator["project_scope"]) / "docs" / f"{kind}.md").as_posix()
                 for kind in ("requirements", "design")}
        before = {kind: engine.sha256_bytes(_raw_git(integration, "show", parent + ":" + path))
                  for kind, path in paths.items()}
        if before != publication["before"] or after != publication["after"] or before == after:
            _fail("publication must bind the exact changed committed canonical pair")
        directory = engine.execution_dir(run_dir, execution) / "authoring-checkpoints" / digest(operation_id)
        sealed_path, intent_path = directory / "sealed.json", directory / "commit.json"
        binding = {"schema": "workflow/authoring-checkpoint-intent-v1", "execution_id": execution,
                   "operation_id": operation_id, "parent": parent, "message": message,
                   "publication_sha256": digest(publication), "basis_sha256": engine.canonical_digest(basis),
                   "after": after}
        current = engine.head(integration)
        if sealed_path.exists():
            sealed = _read(sealed_path, "workflow/authoring-checkpoint-v1")
            intent = _read(intent_path, "workflow/authoring-checkpoint-intent-v1")
        else:
            if coordinator["integration_head"] != parent:
                _fail("checkpoint publication parent is stale")
            binding["repair"] = _repair_binding(run_dir, execution)
            if current == parent:
                if not _changed_paths(integration, parent) <= set(paths.values()):
                    _fail("unrelated dirty or staged paths prevent canonical checkpoint")
                _git(integration, "add", "-A")
                engine._verify_staged_integration(integration, coordinator["project_scope"], run_dir)
                for kind, path in paths.items():
                    if engine.sha256_bytes(_raw_git(integration, "show", ":" + path)) != after[kind]:
                        _fail("staged canonical bytes changed")
                tree = _git(integration, "write-tree")
                intent = _save(intent_path, {**binding, "tree": tree})
                if _specs(coordinator)[1] != after or engine.head(integration) != parent:
                    _fail("canonical bytes or parent changed before checkpoint commit")
                _git(integration, "commit", "--cleanup=whitespace", "-m", message)
                current = engine.head(integration)
            else:
                intent = _read(intent_path, "workflow/authoring-checkpoint-intent-v1")
            if any(intent.get(key) != value for key, value in binding.items()):
                _fail("interrupted checkpoint authority changed")
            _verify_commit(integration, current, intent)
            sealed = _save(sealed_path, {**binding, "schema": "workflow/authoring-checkpoint-v1",
                                        "head": current, "tree": intent["tree"],
                                        "intent_sha256": intent["sha256"]})
        if (any(intent.get(key) != value for key, value in binding.items())
                or any(sealed.get(key) != intent.get(key) for key in (*binding.keys(), "repair", "tree")
                       if key != "schema")
                or sealed["head"] != current or sealed["intent_sha256"] != intent["sha256"]):
            _fail("checkpoint replay changed")
        _verify_commit(integration, current, intent)
        if coordinator["integration_head"] not in {parent, current}:
            _fail("integration advanced beyond this checkpoint")
        history = coordinator.setdefault("authoring_checkpoints", [])
        if sealed["sha256"] not in history:
            history.append(sealed["sha256"])
        coordinator["integration_head"] = current
        engine._save_coordinator(run_dir, execution, coordinator)
        return {**sealed, "validation_command": [sys.executable,
                str(Path(__file__).with_name("workflow_transition.py")), "validate-candidate",
                "--run-dir", str(run_dir), "--execution", execution],
                "working_directory": str(integration)}


def _verify_commit(integration, current, intent):
    if (_git(integration, "rev-list", "--parents", "-n", "1", current).split() != [current, intent["parent"]]
            or _git(integration, "rev-parse", current + "^{tree}") != intent["tree"]
            or not engine.clean(integration)):
        _fail("checkpoint is not the journaled clean direct child")
    engine._require_seal_message(integration, current, intent["message"])


def target(run_dir, execution, coordinator):
    """A distinct candidate target never replaces recorded combined-wave proof."""
    from workflow_resources import require_released
    # The producer itself is an active validator during its final target check.
    # Observe writers here; transition mutations use the stronger quiescence gate.
    if any(read(path).get("active_task_ids") for path in
           (run_dir / "execution").glob("*/waves/*.json")):
        _fail("candidate validation requires all writers to be stopped")
    require_released(run_dir)
    cwd = Path(coordinator["integration_worktree"])
    if (coordinator["status"] not in {"integrated", "sealed"}
            or coordinator.get("active_wave") is not None or not engine.clean(cwd)
            or engine.head(cwd) != coordinator["integration_head"]
            or any(not engine._wave_satisfied(run_dir, execution, coordinator, wave)
                   for wave in coordinator["wave_ids"])):
        _fail("candidate validation requires the clean recorded completed integration")
    from workflow_impact import verify_execution
    basis = verify_execution(run_dir, execution, coordinator["plan_digest"],
                             Path(coordinator["selected_project_root"]))
    _verify_history(run_dir, execution, coordinator, cwd)
    return {"run_id": run_dir.name, "execution_id": execution, "wave_id": None, "task_id": None,
            "basis_sha256": engine.canonical_digest(basis), "head": engine.head(cwd),
            "tree": _git(cwd, "rev-parse", "HEAD^{tree}"),
            "inputs": {"waves": {wave: digest(read(engine.wave_path(run_dir, execution, wave)))
                                 for wave in coordinator["wave_ids"]},
                       "authoring_checkpoints": coordinator.get("authoring_checkpoints", [])}}, cwd


def _verify_history(run_dir, execution, coordinator, integration):
    history = coordinator.get("authoring_checkpoints", [])
    if (not isinstance(history, list) or any(not isinstance(item, str) or
            engine.SHA256_RE.fullmatch(item) is None for item in history)
            or len(set(history)) != len(history)):
        _fail("checkpoint history is invalid")
    directory = engine.execution_dir(run_dir, execution) / "authoring-checkpoints"
    receipts = {}
    for path in directory.glob("*/sealed.json"):
        sealed = _read(path, "workflow/authoring-checkpoint-v1")
        intent = _read(path.with_name("commit.json"), "workflow/authoring-checkpoint-intent-v1")
        if (sealed.get("intent_sha256") != intent["sha256"]
                or any(sealed.get(key) != value for key, value in intent.items() if key not in {"schema", "sha256"})
                or sealed.get("execution_id") != execution
                or path.parent.name != digest(sealed.get("operation_id"))):
            _fail("checkpoint history receipt is invalid")
        receipts[sealed["sha256"]] = sealed
    authorized_heads = {read(engine.wave_path(run_dir, execution, wave)).get("integration_head")
                        for wave in coordinator["wave_ids"]} - {None}
    previous = None
    for identity in history:
        if identity not in receipts:
            _fail("checkpoint history receipt is missing")
        receipt = receipts[identity]
        basis = read(run_dir / "bases" / execution / (receipt["basis_sha256"] + ".json"))
        if engine.canonical_digest(basis) != receipt["basis_sha256"] or basis.get("execution_id") != execution:
            _fail("checkpoint history basis changed")
        if (receipt["parent"] not in authorized_heads
                or previous is not None and not engine._is_ancestor(integration, previous["head"], receipt["parent"])
                or _git(integration, "rev-list", "--parents", "-n", "1", receipt["head"]).split()
                != [receipt["head"], receipt["parent"]]
                or _git(integration, "rev-parse", receipt["head"] + "^{tree}") != receipt["tree"]):
            _fail("checkpoint history does not match its Git lineage")
        engine._require_seal_message(integration, receipt["head"], receipt["message"])
        authorized_heads.add(receipt["head"])
        previous = receipt
    if previous is not None and engine.head(integration) not in authorized_heads:
        _fail("candidate drifted after its last authoring checkpoint")
