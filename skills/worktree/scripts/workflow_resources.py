"""Exclusive shared-runtime leases bound to one current worker assignment."""

from contextlib import contextmanager
import hashlib
from pathlib import Path
import re

from workflow_state import WorkflowError, lock, read, write
from workflow_objectives import session_hash, validate_run

RESOURCE_RE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
IDENTITY = ("owner", "run_dir", "feature_id", "wave_id", "task_id", "assignment_digest", "basis_sha256")


def historical_assignment(path: Path):
    from workflow_execution import _validate_assignment_record, assignment_path
    value = read(path)
    _validate_assignment_record(value)
    expected = assignment_path(Path(value["run_dir"]), value["feature_id"], value["wave_id"], value["task_id"])
    if path.absolute() != expected or read(expected) != value:
        raise WorkflowError("OWNERSHIP_INVALID", "runtime requires the canonical registered assignment")
    return value


def _identity(path: Path, resource: str, session: str | None):
    from workflow_execution import task_path, coordinator_path
    from workflow_impact import verify_execution, canonical_digest
    assignment = historical_assignment(path)
    if RESOURCE_RE.fullmatch(resource) is None or "runtime:" + resource not in assignment["conflict_domains"]:
        raise WorkflowError("RESOURCE_UNASSIGNED", "runtime identity must be declared in this task's conflict domains")
    run_dir = Path(assignment["run_dir"])
    coordinator = read(coordinator_path(run_dir, assignment["feature_id"]))
    basis = verify_execution(run_dir, assignment["feature_id"], coordinator["plan_digest"],
                             Path(assignment["selected_project_root"]))
    if canonical_digest(basis) != assignment["basis_sha256"]:
        raise WorkflowError("REPLAN_REQUIRED", "runtime assignment is stale")
    task = read(task_path(run_dir, assignment["feature_id"], assignment["wave_id"], assignment["task_id"]))
    if (task["status"] != "running" or re.fullmatch(r"[0-9a-f]{64}", str(task.get("worker_session_hash"))) is None
            or session is not None and task["worker_session_hash"] != session_hash(session)):
        raise WorkflowError("OWNERSHIP_INVALID", "only the assigned running native worker may acquire its runtime")
    return assignment, task


def _path(home, resource):
    if not isinstance(resource, str) or RESOURCE_RE.fullmatch(resource) is None:
        raise WorkflowError("RESOURCE_UNASSIGNED", "invalid resource identity")
    return home / "workflow-resources" / (hashlib.sha256(resource.encode()).hexdigest() + ".json")


def _use_lock(home, resource):
    return lock(home / "workflow-resources", _path(home, resource).stem + ".use.lock")


def _lease(assignment, task, resource):
    return {"schema": "workflow/runtime-lease-v1", "resource": resource,
            **{key: assignment[key] for key in IDENTITY},
            "worker_session_hash": task["worker_session_hash"], "status": "active"}


def acquire(home: Path, assignment_path: Path, resource: str, session: str):
    if not isinstance(session, str) or not session.strip():
        raise WorkflowError("OWNERSHIP_INVALID", "runtime acquisition requires the native worker identity")
    preliminary = historical_assignment(assignment_path)
    if home.absolute() != Path(preliminary['run_dir']).parents[2]:
        raise WorkflowError('OWNERSHIP_INVALID', 'runtime lease home differs from the registered workflow home')
    with _use_lock(home, resource), lock(Path(preliminary["run_dir"]).parent), lock(home / "workflow-resources"):
        assignment, task = _identity(assignment_path, resource, session)
        value = _lease(assignment, task, resource)
        path = _path(home, resource)
        if path.exists():
            prior = read(path)
            if prior.get("schema") != value["schema"]:
                raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "unsupported runtime lease was preserved")
            if prior.get("status") == "active" and prior != value:
                raise WorkflowError("RESOURCE_BUSY", "shared runtime belongs to another assignment")
            if prior == value:
                return {**value, "lease_path": str(path)}
        write(path, value)
        return {**value, "lease_path": str(path)}


def _verify_locked(home, assignment_path, resource, session):
    assignment, task = _identity(assignment_path, resource, session)
    try:
        value = read(_path(home, resource))
    except FileNotFoundError as error:
        raise WorkflowError("RESOURCE_UNASSIGNED", "runtime requires an active assignment lease") from error
    if value != _lease(assignment, task, resource):
        raise WorkflowError("RESOURCE_UNASSIGNED", "runtime access requires the exact active assignment lease")
    return value


def _registered_home(home, assignment_path):
    preliminary = historical_assignment(assignment_path)
    if home.absolute() != Path(preliminary['run_dir']).parents[2]:
        raise WorkflowError('OWNERSHIP_INVALID', 'runtime lease home differs from the registered workflow home')
    return Path(preliminary['run_dir'])


@contextmanager
def consume(home: Path, assignment_path: Path, resource: str, session: str | None = None):
    """Hold only resource use during an action; keep heartbeat/observation available.

    Acquire/release take the same use lock before the run/resource state locks.
    The active assignment lease continues to fence admission and reassignment.
    """
    run_dir = _registered_home(home, assignment_path)
    with _use_lock(home, resource):
        with lock(run_dir.parent), lock(home / "workflow-resources"):
            value = _verify_locked(home, assignment_path, resource, session)
        yield value


def verify(home: Path, assignment_path: Path, resource: str, session: str | None = None):
    run_dir = _registered_home(home, assignment_path)
    with lock(run_dir.parent), lock(home / "workflow-resources"):
        return _verify_locked(home, assignment_path, resource, session)


def release(home: Path, assignment_path: Path, resource: str, session: str, *, termination: Path | None = None):
    preliminary = historical_assignment(assignment_path)
    if home.absolute() != Path(preliminary['run_dir']).parents[2]:
        raise WorkflowError('OWNERSHIP_INVALID', 'runtime lease home differs from the registered workflow home')
    run_dir = Path(preliminary["run_dir"])
    with _use_lock(home, resource), lock(run_dir.parent), lock(home / "workflow-resources"):
        assignment = historical_assignment(assignment_path)
        run, _ = validate_run(run_dir)
        path = _path(home, resource)
        value = read(path)
        if (value.get("schema") != "workflow/runtime-lease-v1" or value.get("resource") != resource
                or any(value.get(key) != assignment[key] for key in IDENTITY)):
            raise WorkflowError("OWNERSHIP_INVALID", "runtime lease belongs to another assignment")
        if session_hash(session) == run["coordinator_session_sha256"]:
            if termination is None:
                raise WorkflowError("WRITER_NOT_QUIESCENT", "coordinator release requires native termination evidence")
            from workflow_reconcile import termination as observe_termination
            observe_termination(run_dir, assignment["feature_id"], assignment["wave_id"],
                                assignment["task_id"], termination, session)
        elif session_hash(session) != value["worker_session_hash"]:
            raise WorkflowError("OWNERSHIP_INVALID", "only the lease owner may release the runtime")
        value["status"] = "released"
        write(path, value)
        return value


def active_leases(run_dir: Path):
    root = run_dir.parents[2] / "workflow-resources"
    if not root.exists():
        return []
    with lock(root):
        return [value for path in sorted(root.glob('*.json'))
                if (value := read(path)).get('run_dir') == str(run_dir) and value.get('status') == 'active']


def require_released(run_dir: Path, assignment=None):
    if any(assignment is None or item.get('assignment_digest') == assignment['assignment_digest']
           for item in active_leases(run_dir)):
        raise WorkflowError('RESOURCE_BUSY', 'release assignment runtime leases before admission, retirement or completion')
