"""Read-only binding of the live harness to the current SDLC checkout."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

import owned_git_origin


class TargetError(RuntimeError):
    """The selected workflow checkout cannot be independently verified."""


@contextmanager
def workflow_imports(skills_root: Path):
    """Keep lazy owner imports available through the complete checked action."""
    previous = list(sys.path)
    sys.path.insert(0, str(skills_root / "worktree/scripts"))
    try:
        yield
    finally:
        # Owner imports may insert their own paths; never pop an assumed slot.
        sys.path[:] = previous


def _safe_path(path: Path, boundary: Path) -> None:
    if (
        not path.is_absolute()
        or path != path.resolve()
        or not path.is_relative_to(boundary)
    ):
        raise TargetError("Workflow target escaped the owned run.")
    current = path
    while True:
        metadata = current.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o022
        ):
            raise TargetError("Workflow target path has unsafe ownership or a symlink.")
        if current == boundary:
            break
        current = current.parent


def _private_bytes(path: Path, boundary: Path) -> bytes:
    _safe_path(path, boundary)
    metadata = path.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o077
        or metadata.st_size > 1024 * 1024
    ):
        raise TargetError("Workflow identity must be a bounded private regular file.")
    return path.read_bytes()


def _private_json(path: Path, boundary: Path) -> dict:
    value = json.loads(_private_bytes(path, boundary).decode("utf-8"))
    if not isinstance(value, dict):
        raise TargetError("Workflow identity is not an object.")
    return value


def _run(arguments: list[str], env: dict[str, str]) -> str:
    if arguments[:2] == ["git", "-C"] and len(arguments) >= 3:
        env = owned_git_origin.environment(Path(arguments[2]), env=env)
    result = subprocess.run(
        arguments,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        env=env,
    )
    if result.returncode != 0:
        raise TargetError("Read-only workflow identity validation failed.")
    return result.stdout.strip()


def _registry(project: Path, env: dict[str, str]) -> dict[str, dict[str, str]]:
    raw = _run(
        ["git", "-C", str(project), "worktree", "list", "--porcelain", "-z"], env
    )
    result = {}
    for group in raw.split("\0\0"):
        record = {}
        for field in group.split("\0"):
            if field:
                key, _, value = field.partition(" ")
                record[key] = value
        if "worktree" in record:
            result[record["worktree"]] = record
    return result


def resolve_target(
    state: dict, *, skills_root: Path, agent: str, require_clean: bool = True,
    worker_task: str | None = None, assignment_digest: str | None = None,
    phase_observation: str | None = None,
) -> dict[str, str]:
    """Resolve the exact active integration or completed promotion, never a caller path."""
    try:
        with workflow_imports(skills_root):
            return _resolve_target(
                state, skills_root=skills_root, agent=agent, require_clean=require_clean,
                worker_task=worker_task, assignment_digest=assignment_digest,
                phase_observation=phase_observation,
            )
    except TargetError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        subprocess.TimeoutExpired,
        owned_git_origin.OriginError,
        RuntimeError,
    ) as error:
        raise TargetError(
            "Owned workflow execution target is unavailable or invalid."
        ) from error


def _registration(state: dict, agent: str, *, coordinator_session: str | None = None):
    try:
        return _owned_registration(state, agent, coordinator_session=coordinator_session, cleanup=False)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        if isinstance(error, TargetError):
            raise
        raise TargetError("Workflow coordinator registration is unavailable or invalid.") from error


def cleanup_registration(state: dict, agent: str):
    """Resolve ownership for quiescence only; never certify phase admission."""
    try:
        return _owned_registration(state, agent, coordinator_session=None, cleanup=True)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        if isinstance(error, TargetError):
            raise
        raise TargetError("Workflow cleanup ownership is unavailable or invalid.") from error


def _owned_registration(state: dict, agent: str, *, coordinator_session: str | None, cleanup: bool):
    if agent not in {"codex", "claude"}:
        raise TargetError("Workflow host is invalid.")
    boundary = Path(state["run_root"])
    project = Path(state["project_root"])
    home = Path(state["private_root"]) / f"{agent}-home"
    _safe_path(home, boundary)
    owned_git_origin.validate(
        project,
        boundary,
        state["verification_id"],
        baseline=state["git"]["baseline_sha"],
    )
    env = owned_git_origin.environment(project)
    env["CODEX_HOME" if agent == "codex" else "CLAUDE_CONFIG_DIR"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    runs = home / "sdlc-runs"
    _safe_path(runs, boundary)
    startup = validate_startup_receipt(state) if state.get('workflow_startup') else None
    expected_context = None
    if startup:
        if coordinator_session is not None:
            raise TargetError("Coordinator selector is only valid for initial startup observation.")
        # Startup identity is immutable; its former session may bind another run.
        if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", startup[key]) is None
               for key in ('project_id', 'run_id')):
            raise TargetError('Startup run identity is invalid.')
        run_dir = runs / startup['project_id'] / startup['run_id']
    elif cleanup:
        # The product owner derives the project identity under the sealed Git
        # environment. Cleanup need not manufacture a successful startup receipt.
        script = (
            "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "from workflow_objectives import project_context; "
            "root,context,_,_=project_context(Path(sys.argv[2]),Path(sys.argv[3]),'agentic-sdlc'); "
            "print(json.dumps({'root':str(root),'context':context}))"
        )
        discovered = json.loads(_run([
            sys.executable, "-c", script,
            str(Path(__file__).resolve().parents[2] / "worktree/scripts"), str(project), str(home),
        ], env))
        project_dir = Path(discovered["root"])
        expected_context = discovered["context"]
        if project_dir.parent != runs or project_dir.name != expected_context.get("project_id"):
            raise TargetError("Cleanup project identity escapes the workflow home.")
        pointer = _private_json(project_dir / "active-run.json", boundary)
        run_id = pointer.get("run_id")
        if not isinstance(run_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) is None:
            raise TargetError("Cleanup active run identity is invalid.")
        run_dir = project_dir / run_id
    else:
        if not isinstance(coordinator_session, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", coordinator_session) is None:
            raise TargetError('An explicit native coordinator selector is required for initial observation.')
        session_sha = hashlib.sha256(coordinator_session.encode()).hexdigest()
        binding = _private_json(runs / 'bindings' / (session_sha + '.json'), boundary)
        if (binding.get('schema') != 'workflow/session-binding-v1' or binding.get('owner') != 'agentic-sdlc'
                or binding.get('session_sha256') != session_sha or binding.get('project_root') != str(project)):
            raise TargetError('Recorded coordinator binding does not own this fixture.')
        run_dir = Path(str(binding.get('run_dir', '')))
    if run_dir.parent.parent != runs:
        raise TargetError('Recorded run binding escapes the workflow home.')
    manifest = run_dir.parent / 'project.json'
    workspace = _private_json(manifest, boundary)
    active = _private_json(run_dir.parent / 'active-run.json', boundary)
    lock = _private_json(run_dir.parent / 'active.lock', boundary)
    current_path = run_dir / 'current-state.json'
    current = (None if cleanup and not startup and not current_path.exists() and not current_path.is_symlink()
               else _private_json(current_path, boundary))
    with workflow_imports(Path(__file__).resolve().parents[2]):
        from workflow_objectives import validate_run
        run, objective = validate_run(run_dir, owner='agentic-sdlc')
    if expected_context is not None and any(workspace.get(key) != value for key, value in expected_context.items()):
        raise TargetError("Cleanup project registration differs from the product owner context.")
    if startup:
        if (startup['run_id'] != run_dir.name or startup['project_id'] != workspace.get('project_id')
                or startup['objective_sha256'] not in {item['sha256'] for item in objective['revisions']}):
            raise TargetError('Startup receipt belongs to a different run or objective ancestry.')
    if startup or cleanup:
        session_sha = run['coordinator_session_sha256']
        if not isinstance(session_sha, str) or re.fullmatch(r"[0-9a-f]{64}", session_sha) is None:
            raise TargetError("Recorded coordinator identity is invalid.")
        binding = _private_json(runs / 'bindings' / (session_sha + '.json'), boundary)
        if (binding.get('schema') != 'workflow/session-binding-v1' or binding.get('owner') != 'agentic-sdlc'
                or binding.get('session_sha256') != session_sha or binding.get('project_root') != str(project)
                or binding.get('run_dir') != str(run_dir)):
            raise TargetError('Current coordinator binding does not own the observed run.')
    identity = {'project_id': workspace.get('project_id'), 'project_root': str(project), 'run_id': run_dir.name}
    if (workspace.get('schema') != 'workflow/project-v1' or workspace.get('owner') != 'agentic-sdlc'
            or active.get('schema') != 'workflow/active-run-v1' or active.get('run_id') != run_dir.name
            or lock.get('schema') != 'workflow/coordinator-ownership-v1' or lock.get('owner') != 'agentic-sdlc'
            or any(lock.get(key) != value for key, value in identity.items())
            or run.get('coordinator_session_sha256') != session_sha or lock.get('owner_session_hash') != session_sha
            or run.get('project_root') != str(project) or workspace.get('project_root') != str(project)
            or binding.get('objective_id') != objective['objective_id']
            or current is not None and (current.get('run_id') != run_dir.name or current.get('project_id') != workspace['project_id'])):
        raise TargetError('Hook ownership record does not bind the active workflow.')
    return manifest, workspace, active, lock, run_dir, current or {}, env


def current_objective(state: dict, *, agent: str, coordinator_session: str | None = None) -> str:
    """Read the current revision through the previously observed binding."""
    _manifest, _workspace, _active, _lock, run_dir, _current, _env = _registration(state, agent, coordinator_session=coordinator_session)
    return _private_json(run_dir / "objective.json", Path(state["run_root"]))["revisions"][-1]["sha256"]


def validate_historical_basis(
    state: dict, recorded: str, *, agent: str, phase: str, recorded_head: str,
) -> None:
    """Validate historical settlement and the original TDD's preserved lineage."""
    try:
        _validate_historical_basis(
            state, recorded, agent=agent, phase=phase, recorded_head=recorded_head,
        )
    except TargetError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise TargetError("Historical execution basis is unavailable or invalid.") from error


def _validate_historical_basis(
    state: dict, recorded: str, *, agent: str, phase: str, recorded_head: str,
) -> None:
    if not isinstance(recorded, str) or re.fullmatch(r"[0-9a-f]{64}", recorded) is None:
        raise TargetError("Historical execution basis digest is invalid.")
    current_target = resolve_target(
        state, skills_root=Path(__file__).resolve().parents[2], agent=agent,
        require_clean=False,
    )
    _, _, _, _, run_dir, current, _ = _registration(state, agent)
    boundary = Path(state["run_root"])
    feature = current["current_execution"]

    def canonical(value):
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()).hexdigest()

    def basis(fingerprint):
        value = _private_json(run_dir / "bases" / feature / (fingerprint + ".json"), boundary)
        if (canonical(value) != fingerprint
                or value.get("schema") != "workflow/objective-impact-execution-basis-v2"):
            raise TargetError("Historical execution basis changed.")
        return value

    prior = basis(recorded)
    latest = basis(current_target["basis_sha256"])
    settlement_fields = {"impact_sha256", "spec_receipt_sha256", "spec_transition_sha256", "plan_action"}
    changed_plan = prior.get("plan_digest") != latest.get("plan_digest")
    if changed_plan and phase == "sdlc-tdd":
        settlement_fields |= {
            "plan_digest", "plan_path", "assignment_plan_sha256", "validation_plan_sha256",
        }
    if (set(prior) != set(latest)
            or any(prior[key] != latest[key] for key in prior.keys() - settlement_fields)):
        raise TargetError("Historical execution basis has a material identity change.")
    if changed_plan:
        _validate_original_tdd_replan(
            state, run_dir, boundary, prior, latest, recorded, recorded_head,
        )

    # Follow immutable owner receipts, never a caller assertion or arbitrary basis file.
    paths = list((run_dir / "objective-impact").glob("attempt-*.json"))
    if len(paths) > 1024:
        raise TargetError("Historical impact chain exceeds the evidence bound.")
    receipts = {}
    for path in paths:
        value = _private_json(path, boundary)
        fingerprint = canonical(value)
        if fingerprint in receipts:
            raise TargetError("Historical impact chain has duplicate receipts.")
        receipts[fingerprint] = value
    old = receipts.get(prior["impact_sha256"])
    if (not isinstance(old, dict)
            or old.get("spec_receipt_sha256") != prior["spec_receipt_sha256"]):
        raise TargetError("Historical impact receipt is missing or changed.")
    cursor = latest["impact_sha256"]
    expected_spec = latest["spec_receipt_sha256"]
    seen = set()
    while cursor != prior["impact_sha256"]:
        row = receipts.get(cursor)
        if (cursor in seen or not isinstance(row, dict)
                or row.get("schema") != "workflow/objective-impact-receipt-v1"
                or row.get("workflow") != latest["owner"]
                or row.get("objective_id") != latest["objective_id"]
                or row.get("revision") != latest["latest_settled_revision"]
                or row.get("objective_sha256") != latest["objective_sha256"]
                or row.get("effects") != [] or row.get("plan_action") != "retain_plan"
                or row.get("spec_receipt_sha256") != expected_spec):
            raise TargetError("Historical impact succession is missing or material.")
        seen.add(cursor)
        predecessor = receipts.get(row.get("prior_impact_sha256"))
        if not isinstance(predecessor, dict):
            raise TargetError("Historical impact predecessor is missing.")
        previous_spec = predecessor.get("spec_receipt_sha256")
        transition = row.get("spec_transition")
        expected_transition = ({
            "prior_spec_receipt_sha256": previous_spec,
            "next_spec_receipt_sha256": expected_spec,
            "prior_impact_sha256": row["prior_impact_sha256"],
            "reason": "owner_reconciliation",
        } if previous_spec != expected_spec else None)
        if (transition != expected_transition
                or row.get("spec_transition_sha256") != (
                    canonical(transition) if transition is not None else None)):
            raise TargetError("Historical specification transition is invalid.")
        cursor = row["prior_impact_sha256"]
        expected_spec = previous_spec
    if expected_spec != prior["spec_receipt_sha256"]:
        raise TargetError("Historical specification ancestry is incomplete.")


def _validate_original_tdd_replan(state, run_dir, boundary, prior, latest, recorded, recorded_head):
    """Observe existing execution-owner records; never create historical authority."""
    with workflow_imports(Path(__file__).resolve().parents[2]):
        import workflow_execution as execution

        feature = latest["execution_id"]
        coordinator = _private_json(execution.coordinator_path(run_dir, feature), boundary)
        if (coordinator.get("status") != "done"
                or coordinator.get("promoted_head") != state["git"].get("promoted_sha")
                or not state["git"].get("promoted_sha")):
            raise TargetError("Historical original TDD requires completed clean promotion.")
        for value in (prior, latest):
            if value["assignment_plan_sha256"] != value["plan_digest"]:
                raise TargetError("Historical TDD locked plan bindings changed.")

        # Resolve exact hashes, never a latest-named plan or caller-selected replacement.
        paths = list((run_dir / "plans").rglob("*.md"))
        if len(paths) > 1024:
            raise TargetError("Historical TDD locked plan inventory exceeds its bound.")
        plans = {}
        total = 0
        for path in paths:
            raw = _private_bytes(path, boundary)
            total += len(raw)
            if total > 16 * 1024 * 1024:
                raise TargetError("Historical TDD locked plan inventory exceeds its bound.")
            fingerprint = hashlib.sha256(raw).hexdigest()
            if fingerprint in plans:
                raise TargetError("Historical TDD locked plan identity is ambiguous.")
            plans[fingerprint] = (path, raw)

        def tasks(fingerprint):
            if fingerprint not in plans:
                raise TargetError("Historical TDD locked plan is missing or changed.")
            path, raw = plans[fingerprint]
            _private_bytes(path.with_suffix(path.suffix + ".lock"), boundary)
            return {item.task_id: execution._task_json(item)
                    for item in execution.parse_locked_plan(raw.decode("utf-8"))}

        for value in (prior, latest):
            from workflow_validation import digest as validation_digest, plan_path, validate_plan
            definitions = tasks(value["plan_digest"])
            if str(plans[value["plan_digest"]][0]) != value["plan_path"]:
                raise TargetError("Historical TDD locked plan path changed.")
            checks = _private_json(plan_path(Path(value["plan_path"])), boundary)
            validate_plan(checks, list(definitions))
            if validation_digest(checks) != value["validation_plan_sha256"]:
                raise TargetError("Historical TDD executable validation plan changed.")

        rows = [json.loads(line) for line in _private_bytes(
            execution.journal_path(run_dir, feature, "coordinator"), boundary,
        ).splitlines() if line]
        if any(not isinstance(row, dict) for row in rows):
            raise TargetError("Historical TDD replan succession is malformed.")
        seals = [(index, row) for index, row in enumerate(rows) if row.get("event") == "tdd_sealed"]
        if len(seals) != 1:
            raise TargetError("Historical original TDD seal is missing or changed.")
        seal_index, seal = seals[0]
        sealed_head = seal.get("head")
        wave_ids = coordinator.get("wave_ids")
        if (not isinstance(sealed_head, str)
                or re.fullmatch(r"[0-9a-f]{40,64}", sealed_head) is None
                or not isinstance(wave_ids, list) or not wave_ids):
            raise TargetError("Historical original TDD seal is missing or changed.")
        first_wave = _private_json(execution.wave_path(run_dir, feature, wave_ids[0]), boundary)
        if first_wave.get("base_head") != sealed_head:
            raise TargetError("Historical original TDD seal does not bind the first wave.")
        project = Path(state["project_root"])
        if sealed_head != recorded_head:
            # TDD normally reports its uncommitted output before the owner seals it.
            # Only that exact journaled direct-child seal may bridge the receipt.
            parents = execution.git(
                project, ["rev-list", "--parents", "-n", "1", sealed_head],
                "inspect historical TDD seal parent",
            ).split()
            message = execution.sealed_commit_message(project, sealed_head)
            if (parents != [sealed_head, recorded_head]
                    or not any(row.get("event") == "tdd_seal_intent"
                               and row.get("base") == recorded_head
                               and isinstance(row.get("message"), str)
                               and execution.canonical_seal_message(project, row["message"]) == message
                               for row in rows[:seal_index])):
                raise TargetError("Historical original TDD seal intent or parent changed.")
        execution.git(
            project, ["merge-base", "--is-ancestor", sealed_head, coordinator["promoted_head"]],
            "verify historical TDD seal ancestry",
        )

        original_tasks = tasks(prior["plan_digest"])
        cursor = prior["plan_digest"]
        pending = None
        seen = {cursor}
        for row in rows[seals[0][0] + 1:]:
            event = row.get("event")
            if event not in {"future_replan_intent", "future_replanned"}:
                continue
            edge = (row.get("old_digest"), row.get("new_digest"))
            if event == "future_replan_intent":
                if pending is not None or edge[0] != cursor or edge[1] in seen:
                    raise TargetError("Historical TDD replan succession is ambiguous.")
                pending = edge
                continue
            if pending != edge or edge[0] != cursor:
                raise TargetError("Historical TDD replan succession is incomplete.")
            before, after = tasks(cursor), tasks(edge[1])
            if not before.keys() < after.keys() or any(after[key] != value for key, value in before.items()):
                raise TargetError("Historical TDD replan changed preserved task definitions.")
            cursor = edge[1]
            seen.add(cursor)
            pending = None
        if pending is not None or cursor != latest["plan_digest"]:
            raise TargetError("Historical TDD replan succession is incomplete.")

        from workflow_validation import verify_recorded_wave
        checked_waves = set()
        for task_id, definition in original_tasks.items():
            wave_id = coordinator["task_waves"][task_id]
            wave = _private_json(execution.wave_path(run_dir, feature, wave_id), boundary)
            task = _private_json(execution.task_path(run_dir, feature, wave_id, task_id), boundary)
            assignment = _private_json(execution.assignment_path(run_dir, feature, wave_id, task_id), boundary)
            unsigned = dict(assignment)
            digest = unsigned.pop("assignment_digest", None)
            if (wave.get("execution_id") != feature or wave.get("status") != "done"
                    or task_id not in wave.get("integrated_task_ids", [])
                    or task.get("status") != "finished" or task.get("task") != definition
                    or task.get("task_definition_digest") != execution.sha256_json(definition)
                    or digest != execution.sha256_json(unsigned) or task.get("assignment_digest") != digest
                    or assignment.get("base_head") != wave.get("base_head")
                    or assignment.get("schema") != execution.ASSIGNMENT_SCHEMA
                    or assignment.get("owner") != latest["owner"]
                    or assignment.get("basis_sha256") != recorded
                    or assignment.get("plan_digest") != prior["plan_digest"]
                    or assignment.get("task_id") != task_id or assignment.get("wave_id") != wave_id
                    or assignment.get("execution_id") != feature or assignment.get("run_dir") != str(run_dir)):
                raise TargetError("Historical TDD preserved task evidence changed.")
            checked_waves.add(wave_id)
        for wave_id in sorted(checked_waves):
            wave = _private_json(execution.wave_path(run_dir, feature, wave_id), boundary)
            verify_recorded_wave(run_dir, feature, wave, coordinator)


def observe_startup(state: dict, *, skills_root: Path, agent: str, coordinator_session: str | None = None) -> dict:
    """Observe registration before the first phase; never repair product state."""
    try:
        manifest, workspace, active, lock, run_dir, current, env = _registration(state, agent, coordinator_session=coordinator_session)
        if (
            state["git"].get("promoted_sha") is not None
            or current.get("current_phase") != "sdlc-start"
            or current.get("status") != "running"
            or any((run_dir / name).exists() for name in ("execution", "plans"))
        ):
            raise TargetError("Workflow startup observation is too late; start a fresh trial.")
        project = Path(state["project_root"])
        owned_git_origin.validate(project, Path(state["run_root"]), state["verification_id"],
                                  baseline=state["git"]["baseline_sha"], require_clean=True)
        if _run(["git", "-C", str(project), "rev-parse", "HEAD"], env) != state["git"]["baseline_sha"]:
            raise TargetError("Workflow startup requires the unchanged fixture baseline.")
        if coordinator_session is None:
            raise TargetError("An explicit native coordinator selector is required for source discovery.")
        script = (
            "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "from sdlc_state import load_active_run; "
            "r=load_active_run(Path(sys.argv[2]),Path(sys.argv[3]),session_id=sys.argv[4]); "
            "print(json.dumps(None if r is None else "
            "{'project_id':r.project_id,'run_id':r.run_id,'run_dir':str(r.run_dir)}))"
        )
        home = Path(state["private_root"]) / f"{agent}-home"
        observed = json.loads(_run([
            sys.executable, "-c", script,
            str(skills_root / "sdlc-start/assets/hooks/lib"), str(project), str(home), coordinator_session,
        ], env))
        expected = {"project_id": workspace["project_id"], "run_id": run_dir.name, "run_dir": str(run_dir)}
        if observed != expected:
            raise TargetError("Source state discovery did not resolve the exact startup run.")
        lock_path = manifest.parent / "active.lock"
        boundary = Path(state["run_root"])
        if (
            _private_json(lock_path, boundary) != lock
            or _private_json(manifest.parent / "active-run.json", boundary) != active
            or _private_json(run_dir / "current-state.json", boundary) != current
        ):
            raise TargetError("Workflow startup registration changed during inspection.")
        return {
            "schema": "agentic-sdlc/workflow-startup-v3",
            "verification_id": state["verification_id"],
            "baseline_sha": state["git"]["baseline_sha"],
            "project_id": workspace["project_id"],
            "run_id": run_dir.name,
            "agent": agent,
            "current_phase": "sdlc-start",
            "source_state_discovery": "MATCH",
            "owner_session_hash": lock["owner_session_hash"],
            "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            "objective_sha256": _private_json(run_dir / 'objective.json', boundary)['revisions'][-1]['sha256'],
        }
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired, owned_git_origin.OriginError) as error:
        raise TargetError("Workflow startup registration is unavailable or invalid.") from error


def validate_startup_receipt(state: dict) -> dict:
    """Require the lifecycle-bound startup observation, including at aggregate ingestion."""
    try:
        receipt = state.get("workflow_startup")
        relative = "evidence/workflow-startup.json"
        if not isinstance(receipt, dict) or set(receipt) != {"path", "sha256"} or receipt["path"] != relative:
            raise TargetError("Workflow startup receipt is missing or invalid.")
        path = Path(state["run_root"]) / relative
        value = _private_json(path, Path(state["run_root"]))
        if hashlib.sha256(path.read_bytes()).hexdigest() != receipt["sha256"]:
            raise TargetError("Workflow startup receipt digest does not match.")
        if (
            set(value) != {"schema", "verification_id", "baseline_sha", "project_id", "run_id", "agent", "current_phase", "source_state_discovery", "owner_session_hash", "lock_sha256", "objective_sha256", "observed_at"}
            or value.get("schema") != "agentic-sdlc/workflow-startup-v3"
            or value.get("verification_id") != state["verification_id"]
            or value.get("baseline_sha") != state["git"]["baseline_sha"]
            or value.get("agent") not in {"codex", "claude"}
            or value.get("current_phase") != "sdlc-start"
            or value.get("source_state_discovery") != "MATCH"
            or any(not isinstance(value.get(key), str) or not value[key] for key in ("project_id", "run_id", "observed_at"))
            or any(re.fullmatch(r"[0-9a-f]{64}", str(value.get(key, ""))) is None for key in ("owner_session_hash", "lock_sha256", "objective_sha256"))
        ):
            raise TargetError("Workflow startup receipt identity is invalid.")
        return value
    except (OSError, ValueError, KeyError) as error:
        raise TargetError("Workflow startup receipt is unavailable or invalid.") from error


def _resolve_target(
    state: dict, *, skills_root: Path, agent: str, require_clean: bool,
    worker_task: str | None, assignment_digest: str | None,
    phase_observation: str | None,
) -> dict[str, str]:
    if phase_observation is not None and (
        not isinstance(phase_observation, str)
        or phase_observation not in {"sdlc-classify-failure", "sdlc-tdd"}
        or worker_task is not None or assignment_digest is not None
    ):
        raise TargetError("Phase observation requires a supported root coordinator purpose.")
    if (worker_task is None) != (assignment_digest is None) or (
        worker_task is not None and (
            re.fullmatch(r"TASK-[0-9]{3,}", worker_task) is None
            or re.fullmatch(r"[0-9a-f]{64}", assignment_digest or "") is None
        )
    ):
        raise TargetError("Worker target requires an exact task and assignment digest.")
    manifest, workspace, active, lock, run_dir, current, env = _registration(state, agent)
    boundary = Path(state["run_root"])
    project = Path(state["project_root"])
    run_id = run_dir.name
    feature = current.get("current_execution")
    # Classification and documented TDD output must be observable before sealing.
    # Runtime callers never opt into this read-only path.
    # An explicit clean request still wins, and the primary checkout below is
    # always clean, so this exception cannot qualify a dirty promoted deployment.
    observation_checkpoints = {
        "sdlc-classify-failure": {"sdlc-classify-failure"},
        "sdlc-tdd": {"sdlc-tdd", "tests_red", "tests_ready"},
    }
    unsealed_checkpoints = (
        {"sdlc-tdd", "sdlc-update-documents", "align"}
        if phase_observation is None else observation_checkpoints[phase_observation]
    )
    require_clean = require_clean or not (
        current.get("status") == "running"
        and current.get("current_phase") in unsealed_checkpoints
    )
    if (
        current.get("run_id") != run_id
        or current.get("project_id") != workspace.get("project_id")
        or not isinstance(feature, str)
        or re.fullmatch(r"(?:FEAT|SLICE)-[0-9]{3,}", feature) is None
    ):
        raise TargetError(
            "Workflow checkpoint does not bind the active run and feature."
        )
    coordinator_path = run_dir / "execution" / feature / "coordinator.json"
    coordinator = _private_json(coordinator_path, boundary)
    from workflow_impact import verify_execution, canonical_digest
    from workflow_execution import describe_status, watch_task
    from workflow_resources import verify as verify_runtime
    basis = verify_execution(run_dir, feature, coordinator['plan_digest'], project)
    basis_sha256 = canonical_digest(basis)
    status = describe_status(run_dir, feature)
    for key in (
        "execution_id",
        "status",
        "base_branch",
        "base_head",
        "integration_branch",
        "integration_worktree",
        "integration_head",
        "promoted_head",
    ):
        if status.get(key) != coordinator.get(key):
            raise TargetError("Execution identity changed during inspection.")
    if (
        coordinator.get("run_id") != run_id
        or coordinator.get("execution_id") != feature
        or coordinator.get("project_root") != str(project)
        or coordinator.get("git_root") != str(project)
        or coordinator.get("selected_project_root") != str(project)
        or coordinator.get("project_scope") != "."
    ):
        raise TargetError("Execution belongs to another workflow project or feature.")

    def git(path: Path, *arguments: str) -> str:
        return _run(["git", "-C", str(path), *arguments], env)

    common = Path(git(project, "rev-parse", "--git-common-dir"))
    common = (
        (project / common).resolve() if not common.is_absolute() else common.resolve()
    )
    if coordinator.get("git_common_dir") != str(common):
        raise TargetError("Execution Git common directory is foreign.")
    registry = _registry(project, env)
    worker_assignment = None
    worker_assignment_path = None
    integration = run_dir / "worktrees" / feature / "integration"
    if coordinator.get("integration_worktree") != str(integration):
        raise TargetError("Integration path is not the exact owned location.")
    if git(project, "branch", "--show-current") != coordinator["base_branch"]:
        raise TargetError("Project promotion branch changed.")
    if git(project, "status", "--porcelain=v1"):
        raise TargetError("Project checkout is dirty.")
    if coordinator["status"] == "done":
        target, expected = project, coordinator["promoted_head"]
        branch = coordinator["base_branch"]
        if (
            integration.exists()
            or str(integration) in registry
            or git(project, "branch", "--list", coordinator["integration_branch"])
        ):
            raise TargetError("Completed execution retains integration resources.")
        if state["git"]["promoted_sha"] not in {None, expected}:
            raise TargetError("Recorded lifecycle promotion differs from execution.")
    else:
        pending_classifier = (
            phase_observation == "sdlc-classify-failure"
            and current.get("status") == "running"
            and current.get("current_phase") == "sdlc-classify-failure"
            and coordinator["status"] == "design_revision_pending"
        )
        if pending_classifier:
            from workflow_design_revision import observe_pending_revision
            observe_pending_revision(run_dir, feature)
        if (
            (coordinator["status"]
             not in {"prepared", "tdd_sealed", "waves_running", "integrated", "sealed"}
             and not pending_classifier)
            or coordinator["promoted_head"] is not None
            or state["git"]["promoted_sha"] is not None
            or git(project, "rev-parse", "HEAD") != coordinator["base_head"]
        ):
            raise TargetError("Execution is not at a stable pre-promotion boundary.")
        target, expected = integration, coordinator["integration_head"]
        branch = coordinator["integration_branch"]
        _safe_path(target, boundary)
        registration = registry.get(str(target), {})
        if (
            registration.get("branch") != f"refs/heads/{branch}"
            or registration.get("HEAD") != expected
        ):
            raise TargetError("Integration worktree is unregistered or stale.")
    if worker_task is not None:
        wave_id = coordinator.get("active_wave")
        if (
            current.get("status") != "running"
            or current.get("current_phase") != "sdlc-implement-plan"
            or coordinator.get("status") != "waves_running"
            or not isinstance(wave_id, str)
            or re.fullmatch(r"WAVE-[0-9]{3,}", wave_id) is None
        ):
            raise TargetError("Worker runtime requires an active implementation wave.")
        execution_root = run_dir / "execution" / feature
        wave = _private_json(execution_root / "waves" / f"{wave_id}.json", boundary)
        if worker_task not in wave.get('active_task_ids', []):
            raise TargetError('Runtime worker does not own an active scheduler slot.')
        worker_assignment_path = (
            execution_root / "assignments" / wave_id / f"{worker_task}.json"
        )
        worker_assignment = _private_json(worker_assignment_path, boundary)
        watch = watch_task(run_dir, feature, wave_id, worker_task, assignment_digest)
        if (
            watch.get("status") != "ACTIVE"
            or worker_assignment.get("assignment_digest") != assignment_digest
        ):
            raise TargetError("Worker runtime target failed its live scope guard.")
        if worker_assignment.get('basis_sha256') != basis_sha256:
            raise TargetError('Runtime worker belongs to a stale execution basis.')
        verify_runtime(run_dir.parents[2], worker_assignment_path,
                       'sdlc-three-tier-' + state['verification_id'])
        target = run_dir / "worktrees" / feature / "waves" / wave_id / worker_task
        _safe_path(target, boundary)
        expected = worker_assignment.get("base_head")
        branch = worker_assignment.get("branch")
        registration = registry.get(str(target), {})
        if (
            worker_assignment.get("worktree") != str(target)
            or worker_assignment.get("scope_cwd") != str(target)
            or worker_assignment.get("git_common_dir") != str(common)
            or registration.get("branch") != f"refs/heads/{branch}"
            or registration.get("HEAD") != expected
        ):
            raise TargetError("Worker worktree identity is foreign or stale.")
        # Uncommitted task files are the subject of pre-commit runtime testing.
        # task-watch, rather than an unscoped dirty-tree allowance, owns claims.
        require_clean = False
    if (
        not isinstance(expected, str)
        or re.fullmatch(r"[0-9a-f]{40,64}", expected) is None
    ):
        raise TargetError("Execution target commit is invalid.")
    target_common = Path(git(target, "rev-parse", "--git-common-dir"))
    target_common = (
        (target / target_common).resolve()
        if not target_common.is_absolute()
        else target_common.resolve()
    )
    if (
        git(target, "rev-parse", "--show-toplevel") != str(target)
        or target_common != common
        or git(target, "branch", "--show-current") != branch
        or git(target, "rev-parse", "HEAD") != expected
        or (require_clean and git(target, "status", "--porcelain=v1"))
    ):
        raise TargetError("Execution target live Git identity or cleanliness changed.")
    git(project, "merge-base", "--is-ancestor", state["git"]["baseline_sha"], expected)
    if (
        _private_json(manifest.parent / "active-run.json", boundary) != active
        or _private_json(manifest.parent / "active.lock", boundary) != lock
        or _private_json(run_dir / "current-state.json", boundary) != current
        or _private_json(coordinator_path, boundary) != coordinator
        or (worker_assignment_path is not None and _private_json(
            worker_assignment_path, boundary
        ) != worker_assignment)
    ):
        raise TargetError("Workflow ownership changed during target inspection.")
    return {
        "path": str(target),
        "head": expected,
        "branch": branch,
        "run_id": run_id,
        "execution_id": feature,
        "phase": current["current_phase"],
        "basis_sha256": basis_sha256,
    }


def inventory_recovery_evidence(state, failed, *, skills_root, agent):
    try:
        return _inventory_recovery_evidence(
            state, failed, skills_root=skills_root, agent=agent
        )
    except TargetError:
        raise
    except Exception as error:
        raise TargetError(
            "Inventory recovery evidence is unavailable or invalid."
        ) from error


def _inventory_recovery_evidence(state, failed, *, skills_root, agent):
    """Read classifier, successful completion and cursor proof without resolving it."""
    if not failed:
        return []
    with workflow_imports(skills_root):
        sys.path.insert(0, str(skills_root / "sdlc-classify-failure/scripts"))
        import repair_control as owner

        _manifest, _project, _active, _lock, run_dir, _current, _env = _registration(
            state, agent
        )
        boundary = Path(state["run_root"])
        resolved = []
        for summary, observation in failed:
            failed_target = observation.get("target") or {}
            feature = failed_target.get("execution_id")
            if (
                failed_target.get("run_id") != run_dir.name
                or not isinstance(feature, str)
                or re.fullmatch(r"(?:FEAT|SLICE)-[0-9]{3,}", feature) is None
            ):
                raise TargetError("Inventory recovery failed target is unavailable.")
            repair_root = run_dir / "repairs" / feature
            control = _private_json(repair_root / "repair-control.json", boundary)
            if control.get("schema") != owner.REPAIR_CONTROL_SCHEMA:
                raise TargetError("Inventory recovery control is invalid.")
            contexts = [control]
            archives = sorted((repair_root / "cursor-archives").glob("*.json"))
            if len(archives) > 64:
                raise TargetError("Inventory recovery history exceeds its bound.")
            for path in archives:
                archive = _private_json(path, boundary)
                if archive.get("schema") != "agentic-sdlc/revalidation-archive-v1":
                    raise TargetError("Inventory recovery archive is invalid.")
                contexts.append(
                    {
                        "execution_id": feature,
                        "current_event_id": archive["event_id"],
                        "active_blocker": archive["blocker"],
                        "revalidation": archive["previous"],
                    }
                )
            found = None
            for context in contexts:
                cursor = context.get("revalidation")
                if not isinstance(cursor, dict):
                    continue
                classification_id, dispatch_id = (
                    cursor.get("classification_id"),
                    cursor.get("repair_dispatch_id"),
                )
                if any(
                    not isinstance(value, str)
                    or re.fullmatch(r"[0-9a-f]{64}", value) is None
                    for value in (classification_id, dispatch_id)
                ):
                    raise TargetError("Inventory recovery identity is invalid.")
                classification_path = (
                    repair_root / "classifications" / (classification_id + ".json")
                )
                _private_json(classification_path, boundary)
                classification = owner._load_classification(
                    run_dir, feature, classification_id
                )
                event_id = classification.get("event_id")
                if (
                    not isinstance(event_id, str)
                    or re.fullmatch(r"[0-9a-f]{64}", event_id) is None
                ):
                    raise TargetError("Inventory recovery event is invalid.")
                event_path = repair_root / "events" / (event_id + ".json")
                _private_json(event_path, boundary)
                event = owner._load_event(run_dir, feature, event_id)
                digests = {
                    value.removeprefix("sha256:")
                    for value in event["evidence_digests"].values()
                }
                if summary["sha256"] not in digests:
                    continue
                if (
                    classification.get("status") != "routed"
                    or classification.get("corrective_mode") != "existing_task_attempt"
                    or event.get("execution_lifecycle") != "active_task"
                    or event.get("integration_commit") != failed_target.get("head")
                ):
                    raise TargetError(
                        "Inventory recovery does not bind the failed target."
                    )
                owner._verify_revalidation_authority(
                    run_dir,
                    context,
                    {"cursor_id": cursor.get("cursor_id")},
                    require_current_gate=False,
                )
                attempt = next(
                    item
                    for item in context["active_blocker"]["attempts"]
                    if item["dispatch_id"] == dispatch_id
                )
                dispatch_path = repair_root / "dispatches" / (dispatch_id + ".json")
                dispatch = _private_json(dispatch_path, boundary)
                identity = {
                    key: dispatch.get(key)
                    for key in (
                        "blocker_key",
                        "ordinal",
                        "classification_id",
                        "remedy_scale",
                        "hypothesis",
                        "evidence_reference",
                    )
                }
                if (
                    owner._digest(identity) != dispatch_id
                    or dispatch.get("event_id") != event_id
                    or dispatch.get("classification_id") != classification_id
                    or any(dispatch.get(key) != attempt.get(key) for key in identity)
                ):
                    raise TargetError("Inventory recovery dispatch changed.")
                journal = repair_root / "repair-journal.jsonl"
                _safe_path(journal, boundary)
                metadata = journal.stat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_mode & 0o077
                    or metadata.st_size > 1048576
                ):
                    raise TargetError("Inventory recovery journal is unsafe.")
                completion = {
                    "schema": owner.REPAIR_JOURNAL_SCHEMA,
                    "event": "remediation-completion-committed",
                    "transition_id": owner._digest(
                        {
                            "action": "complete",
                            "dispatch_id": dispatch_id,
                            "result": "succeeded",
                        }
                    ),
                    "dispatch_id": dispatch_id,
                    "blocker_key": event["blocker_key"],
                    "result": "succeeded",
                }
                if completion not in [
                    json.loads(line) for line in journal.read_text().splitlines()
                ]:
                    raise TargetError("Inventory recovery completion is unproven.")
                reference = attempt.get("verification_reference")
                if (
                    not isinstance(reference, str)
                    or not reference.startswith("evidence/")
                    or Path(reference).is_absolute()
                    or ".." in Path(reference).parts
                ):
                    raise TargetError("Inventory recovery evidence path is invalid.")
                evidence_path = run_dir / reference
                _private_json(evidence_path, boundary)
                source_paths = (
                    classification_path,
                    event_path,
                    dispatch_path,
                    evidence_path,
                )
                found = {
                    "observation_sha256": summary["sha256"],
                    "classification_id": classification_id,
                    "dispatch_id": dispatch_id,
                    "completion_sha256": owner._digest(completion),
                    "evidence": [
                        {
                            "path": str(path.relative_to(boundary)),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                        for path in source_paths
                    ],
                }
                break
            if not found:
                raise TargetError(
                    "blocked_missing_evidence: inventory failure lacks classifier/recovery evidence."
                )
            previous = [
                item
                for item in state.get("runtime_recoveries", [])
                if item.get("observation_sha256") == summary["sha256"]
            ]
            if previous and previous != [found]:
                raise TargetError(
                    "Inventory recovery evidence changed after admission."
                )
            resolved.append(found)
        return resolved
