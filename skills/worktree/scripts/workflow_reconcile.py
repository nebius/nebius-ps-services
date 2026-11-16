"""Coordinator-only writer reconciliation, distinct from admitting stale results."""

from pathlib import Path

from workflow_objectives import authorize_coordinator, _writers_quiescent, validate_run, now
from workflow_state import WorkflowError, read, write, lock, digest


def termination(run_dir: Path, feature: str, wave: str, task: str, evidence: Path, session: str):
    """Validate an immutable owner observation; it is never itself proof of a test pass."""
    authorize_coordinator(run_dir, session)
    return observe_termination(run_dir, feature, wave, task, evidence)


def observe_termination(run_dir: Path, feature: str, wave: str, task: str, evidence: Path):
    """Read exact retained native proof without acquiring coordinator authority."""
    from workflow_execution import assignment_path, task_path, _validate_assignment_record
    run, _ = validate_run(run_dir)
    assignment = read(assignment_path(run_dir, feature, wave, task))
    _validate_assignment_record(assignment)
    state = read(task_path(run_dir, feature, wave, task))
    expected_parent = run_dir / "execution" / feature / "terminations"
    if evidence.absolute().parent != expected_parent:
        raise WorkflowError("OWNERSHIP_INVALID", "termination observation must belong to this execution")
    value = read(evidence)
    if (value.get("schema") != "workflow/worker-termination-v1"
            or value.get("assignment_digest") != assignment["assignment_digest"]
            or value.get("worker_session_hash") != state.get("worker_session_hash")
            or value.get("observer_session_hash") != run["coordinator_session_sha256"]
            or value.get("status") != "terminated"
            or not value.get("native_handle") or not value.get("observation")):
        raise WorkflowError("WRITER_NOT_QUIESCENT", "exact coordinator-observed native termination is required")
    return assignment, state, value


def reconcile_worker(run_dir: Path, feature: str, wave: str, task: str, evidence: Path, session: str):
    """Fence-safe reconciliation retains work and evidence without result admission."""
    from workflow_execution import wave_path, task_path, _execution_transition_lock
    with _execution_transition_lock(run_dir, feature):
        from workflow_validation import require_quiescent
        require_quiescent(run_dir, feature, wave_id=wave, task_id=task)
        run, _ = authorize_coordinator(run_dir, session)
        if not run.get("fence"):
            raise WorkflowError("RECONCILIATION_INVALID", "normal results must use validated admission")
        assignment, state, observation = termination(run_dir, feature, wave, task, evidence, session)
        receipt = {"schema": "workflow/writer-reconciliation-v1", "execution_id": feature,
                   "wave_id": wave, "task_id": task, "assignment_digest": assignment["assignment_digest"],
                   "basis_sha256": assignment["basis_sha256"], "termination_sha256": digest(observation),
                   "retained_worktree": assignment["worktree"], "retained_branch": assignment["branch"],
                   "disposition": "superseded", "prior_status": state["status"]}
        path = run_dir / "execution" / feature / "reconciliations" / f"{wave}-{task}.json"
        if path.exists():
            existing = read(path)
            if any(existing.get(key) != receipt.get(key) for key in receipt if key != "prior_status"):
                raise WorkflowError("RECONCILIATION_INVALID", "writer reconciliation identity changed")
            receipt = existing
        else:
            write(path, receipt, exclusive=True)
        wave_file = wave_path(run_dir, feature, wave)
        record = read(wave_file)
        if task in record["active_task_ids"]:
            record["active_task_ids"].remove(task)
        superseded = record.setdefault("superseded_task_ids", [])
        if task not in superseded:
            superseded.append(task)
        write(wave_file, record)
        state["status"] = "superseded"
        write(task_path(run_dir, feature, wave, task), state)
        if run.get("shared_admission"):
            from workflow_projects import release_resources
            release_resources(run_dir, "workers", [f"{feature}/{wave}/{task}"])
        return receipt


def retire_fenced_waves(run_dir: Path, session: str):
    """Record incomplete waves as historical; delivered commits are never rolled back."""
    run, _ = authorize_coordinator(run_dir, session)
    if not run.get("fence"):
        raise WorkflowError("RECONCILIATION_INVALID", "execution is not fenced")
    _writers_quiescent(run_dir)
    from workflow_resources import require_released
    require_released(run_dir)
    retained = []
    for path in sorted((run_dir / "execution").glob("*/waves/*.json")):
        wave = read(path)
        if wave["status"] == "superseded":
            from workflow_capacity import reconcile_retired_wave
            reconcile_retired_wave(run_dir, path.parent.parent.name, wave)
            continue
        if wave["status"] in {"planned", "done", "failed"}:
            continue
        if wave.get("integrated_task_ids") or wave["status"] in {"integrating", "integrated"}:
            if run.get("fence") == "cancelled":
                # Terminal cancellation retains uncertainty; it neither changes
                # the execution basis nor certifies an unfinished integration.
                retained.append({"kind": "unresolved-integration", "path": str(path.relative_to(run_dir)),
                                 "sha256": digest(wave), "status": wave["status"],
                                 "disposition": "retained-recovery"})
                continue
            raise WorkflowError("RECOVERY_REQUIRED", "reconcile the existing integration journal before changing its basis")
        feature = path.parent.parent.name
        assignments = []
        from workflow_execution import assignment_path
        for task in wave["task_ids"]:
            assignment_file = assignment_path(run_dir, feature, wave["wave_id"], task)
            if not assignment_file.exists():
                continue
            assignment = read(assignment_file)
            reconciliation = run_dir / 'execution' / feature / 'reconciliations' / f"{wave['wave_id']}-{task}.json"
            admission = run_dir / 'execution' / feature / 'admissions' / f'{task}.json'
            if not reconciliation.exists() and not admission.exists():
                raise WorkflowError('RECONCILIATION_INVALID', 'allocated worker lacks termination reconciliation')
            proof_file = reconciliation if reconciliation.exists() else admission
            proof = read(proof_file)
            if proof.get('assignment_digest') != assignment['assignment_digest']:
                raise WorkflowError('RECONCILIATION_INVALID', 'worker reconciliation belongs to another assignment')
            assignments.append({'task_id': task, 'assignment_digest': assignment['assignment_digest'],
                                'disposition': 'retained-recovery', 'proof_sha256': digest(proof),
                                'proof_path': str(proof_file.relative_to(run_dir))})
        retention = {'schema': 'workflow/retained-wave-v1', 'wave_id': wave['wave_id'], 'assignments': assignments}
        retention_path = run_dir / 'execution' / feature / 'retentions' / (wave['wave_id'] + '.json')
        if retention_path.exists():
            if read(retention_path) != retention:
                raise WorkflowError('RECONCILIATION_INVALID', 'immutable retention receipt changed')
        else:
            write(retention_path, retention, exclusive=True)
        wave.update(status="superseded", ready_task_ids=[])
        write(path, wave)
        from workflow_capacity import reconcile_retired_wave
        reconcile_retired_wave(run_dir, feature, wave)
    for path in sorted((run_dir / "execution").glob("*/reconciliations/*.json")):
        retained.append(read(path))
    return retained + resource_inventory(run_dir)


def cancel_complete(run_dir: Path, session: str):
    with lock(run_dir.parent):
        run, _ = authorize_coordinator(run_dir, session)
        if run["status"] == "cancelled":
            _release_resource_free_cancellation(run_dir, run)
            return {"status": "CANCELLED", "retained_effects": run.get("retained_effects", [])}
        if run.get("fence") != "cancelled":
            raise WorkflowError("RECONCILIATION_INVALID", "cancellation must be accepted before reconciliation")
        retained = retire_fenced_waves(run_dir, session)
        # A retained branch/worktree is an explicit recovery artifact, not a rollback.
        run.update(status="cancelled", retained_effects=retained, updated_at=now())
        write(run_dir / "run.json", run)
        _release_resource_free_cancellation(run_dir, run)
        return {"status": "CANCELLED", "retained_effects": retained}


def _release_resource_free_cancellation(run_dir: Path, run: dict) -> None:
    """Release metadata-only cancellation after exact owner cleanup, never delete."""
    if not run.get("shared_admission"):
        return
    import workflow_projects as projects
    from worktree_state import matching_manifests
    row = projects.get(run_dir)
    if row is None:
        raise WorkflowError("PROJECT_REGISTRY_INVALID", "cancelled admission is missing")
    if row["state"] == "cancelled":
        projects.release(run_dir)
        return
    _writers_quiescent(run_dir)
    if resource_inventory(run_dir):
        return  # Retained recovery resources still own their scope and capacity.
    if matching_manifests(Path(row["source_root"]), scope=row["scope"], task_slug=run_dir.name):
        return  # Includes interrupted creation before the outer allocation checkpoint.
    if row["state"] not in {"requested", "admitted", "allocated"}:
        raise WorkflowError("RECOVERY_REQUIRED", "cancelled project has an unresolved aggregation")
    # No execution allocation or outer manifest may be forgotten. Metadata-only
    # queued/admitted runs have nothing to retain. Uncertain execution stays owned.
    if any((run_dir / "execution").glob("*/coordinator.json")):
        return
    if row.get("outer"):
        _release_removed_outer_cancellation(run_dir, row)
        return
    projects.release(run_dir)
    projects.update(run_dir, expected=row["state"], state="cancelled")


def _release_removed_outer_cancellation(run_dir: Path, row: dict) -> None:
    """Reconcile interrupted, unleased intake after the Worktree owner removed it."""
    import workflow_projects as projects
    import worktree_manager as manager
    import worktree_interop as interop
    from worktree_state import load_manifest

    primary, outer = Path(row["source_root"]), row["outer"]
    name = outer["name"]
    manager._validate_name(name)
    with interop.interop_lock(primary):
        if (Path(outer["worktree"]) != manager.checked_worktree_parent(primary) / name
                or outer["branch"] != manager._branch_for_name(name)):
            raise WorkflowError("OWNERSHIP_INVALID", "cancelled outer identity changed")
        if (load_manifest(primary, name, required=False) is not None
                or any(loader(primary, name) is not None for loader in (
                    interop.load_lease, interop.load_lease_removal,
                    interop.load_reservation, interop.load_preparation))):
            return  # Unfinished owner cleanup still owns the project claim.
        try:
            manager._assert_outer_absent(primary, name, None)
        except manager.WorktreeError:
            return  # Missing paths alone do not establish branch/registration absence.
        projects.release(run_dir)
        projects.update(run_dir, expected=row["state"], state="cancelled")


def resume_replanned(run_dir: Path, session: str):
    from workflow_impact import verify_execution
    with lock(run_dir.parent):
        run, _ = authorize_coordinator(run_dir, session)
        if run.get("fence") != "steering":
            raise WorkflowError("RECONCILIATION_INVALID", "no accepted steering is pending")
        _writers_quiescent(run_dir)
        from workflow_resources import require_released
        require_released(run_dir)
        coordinators = list((run_dir / "execution").glob("*/coordinator.json"))
        # Verify before lifting the durable fence; failure leaves it intact.
        for path in coordinators:
            coordinator = read(path)
            if coordinator["status"] == "done":
                continue
            verify_execution(run_dir, coordinator["execution_id"], coordinator["plan_digest"],
                             Path(coordinator["selected_project_root"]), allow_fenced=True)
            if coordinator.get("active_wave"):
                from workflow_execution import wave_path
                active = read(wave_path(run_dir, coordinator["execution_id"], coordinator["active_wave"]))
                if active["status"] != "superseded":
                    raise WorkflowError("RECOVERY_REQUIRED", "affected wave has not been retired")
                coordinator.update(active_wave=None, status="waves_running")
                write(path, coordinator)
        run.update(status="accepted", fence=None, updated_at=now())
        write(run_dir / "run.json", run)
        return {"status": "REPLANNED", "run_id": run_dir.name}


def resource_inventory(run_dir: Path):
    """Account for exact registered recovery resources without deleting user work."""
    from workflow_resources import active_leases
    records = []
    for path in sorted((run_dir / 'execution').glob('*/coordinator.json')):
        coordinator = read(path)
        for kind, field in [('integration', 'integration_worktree')]:
            target = coordinator.get(field)
            if target and Path(target).exists():
                records.append({'kind': kind, 'path': target, 'execution_id': coordinator['execution_id'],
                                'branch': coordinator.get('integration_branch'), 'disposition': 'retained-recovery'})
    for path in sorted((run_dir / 'execution').glob('*/assignments/*/*.json')):
        assignment = read(path)
        if Path(assignment['worktree']).exists():
            records.append({'kind': 'worker', 'path': assignment['worktree'], 'branch': assignment['branch'],
                            'assignment_digest': assignment['assignment_digest'], 'disposition': 'retained-recovery'})
    # The outer owner remains authoritative; never delete or appropriate its checkout.
    for path in [run_dir / 'execution/interop.json']:
        if not path.exists():
            continue
        records.append({'kind': 'outer-lease', 'path': str(path), 'sha256': digest(read(path)),
                        'disposition': 'retained-owner-handoff'})
    records.extend({'kind': 'runtime-lease', 'lease': value, 'disposition': 'retained-blocker'}
                   for value in active_leases(run_dir))
    return records


def validate_retained_wave(run_dir, feature, wave):
    from workflow_execution import assignment_path
    value = read(run_dir / 'execution' / feature / 'retentions' / (wave['wave_id'] + '.json'))
    if value.get('schema') != 'workflow/retained-wave-v1' or value.get('wave_id') != wave['wave_id']:
        raise WorkflowError('RECONCILIATION_INVALID', 'superseded wave lacks a resource disposition')
    expected_tasks = {task for task in wave['task_ids']
                      if assignment_path(run_dir, feature, wave['wave_id'], task).exists()}
    rows = value.get('assignments')
    if (not isinstance(rows, list) or len(rows) != len(expected_tasks)
            or any(not isinstance(row, dict) for row in rows)
            or {row.get('task_id') for row in rows} != expected_tasks):
        raise WorkflowError('RECONCILIATION_INVALID', 'retention omits or duplicates an allocated worker')
    for row in rows:
        task = row['task_id']
        assignment = read(assignment_path(run_dir, feature, wave['wave_id'], task))
        allowed = {f"execution/{feature}/reconciliations/{wave['wave_id']}-{task}.json",
                   f"execution/{feature}/admissions/{task}.json"}
        if (row['assignment_digest'] != assignment['assignment_digest']
                or row['disposition'] != 'retained-recovery' or row.get('proof_path') not in allowed):
            raise WorkflowError('RECONCILIATION_INVALID', 'superseded assignment disposition changed')
        proof = read(run_dir / row['proof_path'])
        if digest(proof) != row.get('proof_sha256') or proof.get('assignment_digest') != assignment['assignment_digest']:
            raise WorkflowError('RECONCILIATION_INVALID', 'retained native reconciliation evidence changed')
    from workflow_resources import require_released
    require_released(run_dir)
    return value


def recovery_path(run_dir, feature, task, attempt, coordinator_session_hash):
    return (run_dir / 'execution' / feature / 'recoveries'
            / f'{task}-{attempt}-{coordinator_session_hash}.json')


def recovery_failure_binding(run_dir, feature, coordinator, assignment, state):
    """Bind a blocked attempt to its owning repair; interruption is not a failure."""
    from workflow_worker_results import receipt_path, read_receipt
    binding = {'worker_result_receipt_sha256': None, 'repair_authority': None}
    path = receipt_path(assignment, state['attempt'])
    if not path.exists() and not path.is_symlink():
        return binding
    _, result = read_receipt(assignment, state)
    if result['worker_result']['status'] != 'blocked':
        raise WorkflowError('REPLAN_REQUIRED', 'completed or specification-gap results cannot renew a worker attempt')
    binding['worker_result_receipt_sha256'] = result['receipt_sha256']
    if assignment['owner'] != 'agentic-sdlc':
        return binding
    import sys
    scripts = Path(__file__).resolve().parents[2] / 'sdlc-classify-failure/scripts'
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from repair_control import RepairControlError, worker_recovery_authority
    try:
        binding['repair_authority'] = worker_recovery_authority(
            run_dir, feature, result['receipt_sha256'], coordinator['integration_head'],
        )
    except (RepairControlError, OSError, ValueError, KeyError, TypeError) as error:
        raise WorkflowError(
            'REPAIR_NOT_AUTHORIZED',
            'blocked SDLC recovery requires exact classified repair authority',
        ) from error
    return binding


def authorize_recovery(run_dir, feature, wave, task, evidence, session, next_session_sha256):
    from workflow_execution import _load_coordinator, _assert_current_assignment
    import re
    with lock(run_dir.parent):
        from workflow_validation import require_quiescent
        require_quiescent(run_dir, feature, wave_id=wave, task_id=task)
        assignment, state, observation = termination(run_dir, feature, wave, task, evidence, session)
        coordinator = _load_coordinator(run_dir, feature)
        _assert_current_assignment(run_dir, feature, coordinator, assignment)
        if state['status'] != 'running':
            raise WorkflowError('RECOVERY_REQUIRED', 'only a running task can authorize recovery')
        failure_binding = recovery_failure_binding(run_dir, feature, coordinator, assignment, state)
        if re.fullmatch(r'[0-9a-f]{64}', next_session_sha256) is None:
            raise WorkflowError('OWNERSHIP_INVALID', 'fresh native worker identity is required')
        receipt = {'schema': 'workflow/recovery-authorization-v2', 'assignment_digest': assignment['assignment_digest'],
                   'attempt': state['attempt'], 'prior_worker_session_hash': state['worker_session_hash'],
                   'next_worker_session_hash': next_session_sha256,
                   'coordinator_session_hash': validate_run(run_dir)[0]['coordinator_session_sha256'],
                   'termination_path': str(evidence), 'termination_sha256': digest(observation),
                   **failure_binding}
        path = recovery_path(run_dir, feature, task, state['attempt'], receipt['coordinator_session_hash'])
        if path.exists():
            if read(path) != receipt:
                raise WorkflowError('OWNERSHIP_INVALID', 'recovery authorization is already bound')
        else:
            write(path, receipt, exclusive=True)
        from workflow_execution import assignment_path
        from workflow_worker_contract import launch_contract
        return {**receipt, 'launch': launch_contract(
            assignment_path(run_dir, feature, wave, task), assignment,
            recovery_attempt=state['attempt'],
        )}


def observe_worker_repair(run_dir, feature, wave, task):
    """Read the still-active charged repair of an already recovered SDLC worker."""
    from workflow_execution import (
        _assert_current_assignment, _execution_transition_lock, _load_coordinator,
        _validate_assignment_record, _validate_task_liveness, _validated_session_history,
        assignment_path, task_path,
    )
    with _execution_transition_lock(run_dir, feature):
        run, _ = validate_run(run_dir)
        coordinator = _load_coordinator(run_dir, feature)
        assignment = read(assignment_path(run_dir, feature, wave, task))
        _validate_assignment_record(assignment)
        _assert_current_assignment(run_dir, feature, coordinator, assignment)
        state = read(task_path(run_dir, feature, wave, task))
        _validate_task_liveness(state)
        history = _validated_session_history(state)
        attempt = state.get('attempt')
        if (assignment.get('owner') != 'agentic-sdlc' or state.get('status') != 'running'
                or type(attempt) is not int or attempt < 2 or len(history) != attempt):
            raise WorkflowError('REPAIR_NOT_AUTHORIZED', 'a recovered active SDLC worker is required')
        previous = {**state, 'attempt': attempt - 1, 'worker_session_hash': history[-2]}
        binding = recovery_failure_binding(run_dir, feature, coordinator, assignment, previous)
        if binding['worker_result_receipt_sha256'] is None or binding['repair_authority'] is None:
            raise WorkflowError('REPAIR_NOT_AUTHORIZED', 'recovery has no classified blocked result')
        coordinator_hash = run['coordinator_session_sha256']
        approval = read(recovery_path(run_dir, feature, task, attempt - 1, coordinator_hash))
        expected_fields = {
            'schema', 'assignment_digest', 'attempt', 'prior_worker_session_hash',
            'next_worker_session_hash', 'coordinator_session_hash',
            'termination_path', 'termination_sha256',
            'worker_result_receipt_sha256', 'repair_authority',
        }
        if (set(approval) != expected_fields
                or approval.get('schema') != 'workflow/recovery-authorization-v2'
                or approval.get('assignment_digest') != assignment['assignment_digest']
                or type(approval.get('attempt')) is not int
                or approval.get('attempt') != attempt - 1
                or approval.get('prior_worker_session_hash') != history[-2]
                or approval.get('next_worker_session_hash') != state['worker_session_hash']
                or approval.get('coordinator_session_hash') != coordinator_hash
                or any(approval[key] != value for key, value in binding.items())):
            raise WorkflowError('REPAIR_NOT_AUTHORIZED', 'active worker recovery authority changed')
        termination_path = Path(str(approval.get('termination_path')))
        if (not termination_path.is_absolute()
                or termination_path.parent != run_dir / 'execution' / feature / 'terminations'):
            raise WorkflowError('OWNERSHIP_INVALID', 'recovery termination must belong to this execution')
        observed = read(termination_path)
        if (digest(observed) != approval['termination_sha256']
                or observed.get('schema') != 'workflow/worker-termination-v1'
                or observed.get('assignment_digest') != assignment['assignment_digest']
                or observed.get('worker_session_hash') != history[-2]
                or observed.get('observer_session_hash') != coordinator_hash
                or observed.get('status') != 'terminated'
                or not observed.get('native_handle') or not observed.get('observation')):
            raise WorkflowError('WRITER_NOT_QUIESCENT', 'recovery termination proof changed')
        return {'assignment_digest': assignment['assignment_digest'], 'attempt': attempt,
                'worker_session_hash': state['worker_session_hash'],
                'repair_authority': binding['repair_authority']}


def validate_takeover(run_dir, run, evidence, session):
    """Explicit new coordinator observation, never inferred from absence of a PID."""
    from workflow_execution import task_path
    from workflow_objectives import session_hash
    if evidence.absolute().parent != run_dir / 'takeovers':
        raise WorkflowError('OWNERSHIP_INVALID', 'takeover observation must be owned by this run')
    value = read(evidence)
    expected = []
    for path in sorted((run_dir / 'execution').glob('*/waves/*.json')):
        wave = read(path)
        for task in wave.get('active_task_ids', []):
            state = read(task_path(run_dir, path.parent.parent.name, wave['wave_id'], task))
            expected.append({'execution_id': path.parent.parent.name, 'wave_id': wave['wave_id'],
                             'task_id': task, 'worker_session_hash': state.get('worker_session_hash')})
    if (value.get('schema') != 'workflow/coordinator-takeover-v1'
            or value.get('prior_coordinator_session_sha256') != run['coordinator_session_sha256']
            or value.get('observer_session_sha256') != session_hash(session)
            or value.get('coordinator_status') != 'terminated'
            or not value.get('coordinator_native_handle') or not value.get('observation')
            or not isinstance(value.get('workers'), list) or len(value['workers']) != len(expected)):
        raise WorkflowError('WRITERS_ACTIVE', 'takeover requires exact native coordinator and worker termination observations')
    for row, identity in zip(value['workers'], expected):
        if (any(row.get(key) != item for key, item in identity.items())
                or row.get('status') != 'terminated' or not row.get('native_handle') or not row.get('observation')):
            raise WorkflowError('WRITERS_ACTIVE', 'takeover omitted or mismatched an active writer')
    return digest(value)
