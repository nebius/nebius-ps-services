"""Frozen run limits, fresh native observations and conservative wave reservations."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import platform
from pathlib import Path

from workflow_state import WorkflowError, read, write
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

SCHEMA = "workflow/execution-policy-v1"


def _validate_policy(value):
    if (set(value) != {"schema", "worker_limit", "wave_task_limit", "worker_worktree_limit"}
            or value["schema"] != SCHEMA
            or any(type(value[key]) is not int or value[key] < 1 for key in value if key != "schema")
            or value["worker_limit"] > value["wave_task_limit"]
            or value["wave_task_limit"] > value["worker_worktree_limit"]):
        raise WorkflowError("RESOURCE_LIMIT", "execution policy is invalid")


def _has_policy_history(run_dir):
    # Outer-worktree intake precedes internal allocation; its top-level interop
    # lease alone is not an execution policy or an allocated candidate.
    return (any(path != run_dir / "execution" / "interop.json"
                for path in (run_dir / "execution").rglob("*.json"))
            or any(any((run_dir / name).rglob("*.json")) for name in ("bases", "resource-reservations"))
            or any((run_dir / "worktrees").glob("*")))


def policy(run_dir: Path, *, worker_limit: int | None = None, initialize: bool = False) -> dict:
    path = run_dir / "execution-policy.json"
    if not path.exists():
        if not initialize or _has_policy_history(run_dir):
            raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "frozen execution policy is missing; preserve prior ownership")
        worker_limit = 4 if worker_limit is None else worker_limit
        candidate = {"schema": SCHEMA, "worker_limit": worker_limit,
                     "wave_task_limit": 2 * worker_limit, "worker_worktree_limit": 4 * worker_limit}
        _validate_policy(candidate)
        write(path, {"schema": SCHEMA, "worker_limit": worker_limit,
                     "wave_task_limit": 2 * worker_limit, "worker_worktree_limit": 4 * worker_limit}, exclusive=True)
    value = read(path)
    _validate_policy(value)
    if worker_limit is not None and value["worker_limit"] != worker_limit:
        raise WorkflowError("REPLAN_REQUIRED", "requested worker ceiling differs from frozen run policy")
    for path in (run_dir / "execution").glob("*/coordinator.json"):
        existing = read(path)
        if existing.get("policy_sha256") != digest(value):
            raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "frozen execution policy changed or is unsupported")
    return value


def configure(run_dir, worker_limit, wave_task_limit, worker_worktree_limit, session):
    from workflow_objectives import authorize_coordinator
    from workflow_state import lock
    with lock(run_dir.parent):
        authorize_coordinator(run_dir, session)
        wave_task_limit = 2 * worker_limit if wave_task_limit is None else wave_task_limit
        worker_worktree_limit = 2 * wave_task_limit if worker_worktree_limit is None else worker_worktree_limit
        value = {"schema": SCHEMA, "worker_limit": worker_limit,
                 "wave_task_limit": wave_task_limit, "worker_worktree_limit": worker_worktree_limit}
        _validate_policy(value)
        path = run_dir / "execution-policy.json"
        if path.exists():
            if policy(run_dir) != value:
                raise WorkflowError("REPLAN_REQUIRED", "execution policy is already frozen")
        else:
            if _has_policy_history(run_dir):
                raise WorkflowError("RECOVERY_REQUIRED", "cannot replace missing policy after resource ownership begins")
            write(path, value, exclusive=True)
        return value


def _host():
    return hashlib.sha256(platform.node().encode()).hexdigest()


def observe(run_dir: Path, *, native_slots: int | None, evidence: str, session: str) -> dict:
    from workflow_objectives import authorize_coordinator
    run, _ = authorize_coordinator(run_dir, session)
    if (native_slots is not None and (type(native_slots) is not int or native_slots < 0)) or not evidence.strip():
        raise WorkflowError("CAPACITY_UNAVAILABLE", "native capacity observation is invalid")
    value = {"schema": "workflow/host-capacity-v2", "native_slots": native_slots,
             "semantics": "total-workflow-slots",
             "host_sha256": _host(), "coordinator_session_sha256": run["coordinator_session_sha256"],
             "run_id": run_dir.name, "observed_at": datetime.now(timezone.utc).isoformat(),
             "evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest()}
    write(run_dir / "host-capacity.json", value)
    return value


def effective(run_dir: Path, requested: int) -> int:
    from workflow_objectives import validate_run
    path = run_dir / "host-capacity.json"
    if not path.exists():
        return min(requested, 1)
    value = read(path)
    if value.get("schema") != "workflow/host-capacity-v2":
        raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "native capacity schema is unsupported")
    if (set(value) != {"schema", "native_slots", "host_sha256", "coordinator_session_sha256", "run_id", "observed_at", "evidence_sha256", "semantics"}
            or value.get("semantics") != "total-workflow-slots"
            or any(not isinstance(value.get(key), str) or len(value[key]) != 64 or any(c not in "0123456789abcdef" for c in value[key])
                   for key in ("host_sha256", "coordinator_session_sha256", "evidence_sha256"))):
        raise WorkflowError("CAPACITY_UNAVAILABLE", "native capacity observation fields are invalid")
    slots = value.get("native_slots")
    if slots is not None and (type(slots) is not int or slots < 0):
        raise WorkflowError("CAPACITY_UNAVAILABLE", "native slots must be nonnegative or unknown")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value["observed_at"])).total_seconds()
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError("CAPACITY_UNAVAILABLE", "capacity observation time is invalid") from exc
    run, _ = validate_run(run_dir)
    if (not 0 <= age <= 60 or value.get("host_sha256") != _host()
            or value.get("coordinator_session_sha256") != run["coordinator_session_sha256"]
            or value.get("run_id") != run_dir.name or slots is None):
        return min(requested, 1)
    return min(requested, slots)


def _present(repo, path, branch, registered):
    import workflow_execution as e
    return (path.exists() or path.is_symlink() or path.resolve() in registered
            or e.local_branch_exists(repo, branch))


def reserve_integration(run_dir, repo, execution_id):
    """An unresolved historical integration retains its single run-wide slot."""
    import workflow_execution as e
    registered = e.worktrees(repo)
    for path in (run_dir / "execution").glob("*/coordinator.json"):
        if path.parent.name == execution_id:
            continue
        value = read(path)
        if value.get("status") != "done" or value.get("cleanup_retained"):
            raise WorkflowError("RECOVERY_REQUIRED", "prior integration owner has not completed cleanup")
        if _present(repo, Path(value["integration_worktree"]), value["integration_branch"], registered):
            raise WorkflowError("RESOURCE_LIMIT", "reconcile retained integration ownership before replacement")


def reserve_wave(run_dir, repo, execution_id, wave_id, task_ids):
    """Reserve a whole wave before allocating its first worktree; never guess absence."""
    import workflow_execution as e
    limits = policy(run_dir)
    root = run_dir / "resource-reservations"
    selected = root / f"{execution_id}-{wave_id}.json"
    expected = {"schema": "workflow/wave-reservation-v1", "execution_id": execution_id,
                "wave_id": wave_id, "task_ids": task_ids, "policy_sha256": digest(limits)}
    registered = e.worktrees(repo)
    occupied = set()
    for wave_file in (run_dir / "execution").glob("*/waves/*.json"):
        state = read(wave_file)
        reservation_file = root / f"{wave_file.parents[1].name}-{wave_file.stem}.json"
        if (state.get("base_head") is not None or state.get("active_task_ids")) and not reservation_file.exists():
            raise WorkflowError("RECOVERY_REQUIRED", "allocated wave has lost its resource reservation")
    for reservation_file in root.glob("*.json"):
        reservation = read(reservation_file)
        if reservation.get("schema") != expected["schema"] or reservation.get("policy_sha256") != expected["policy_sha256"]:
            raise WorkflowError("RESOURCE_LIMIT", "retained resource reservation cannot be reconciled")
        if set(reservation) != set(expected):
            raise WorkflowError("RESOURCE_LIMIT", "retained reservation shape is invalid")
        ex = e._require_id(reservation["execution_id"], e.EXECUTION_ID_RE, "execution ID")
        wave = e._require_id(reservation["wave_id"], e.WAVE_ID_RE, "wave ID")
        state = read(e.wave_path(run_dir, ex, wave))
        if (reservation["task_ids"] != state.get("task_ids") or not isinstance(reservation["task_ids"], list)
                or len(set(reservation["task_ids"])) != len(reservation["task_ids"])
                or any(not isinstance(task, str) or e.TASK_ID_RE.fullmatch(task) is None for task in reservation["task_ids"])):
            raise WorkflowError("RESOURCE_LIMIT", "retained reservation membership changed")
        for task in reservation["task_ids"]:
            identity = (ex, wave, task)
            worker = run_dir / "worktrees" / ex / "waves" / wave / task
            branch = f"codex/{read(e.coordinator_path(run_dir, ex))['owner']}/{e._safe_id(run_dir.name)}/{e._safe_id(ex)}/{e._safe_id(wave)}/{e._safe_id(task)}"
            if state["status"] not in {"done", "failed", "superseded"} or _present(repo, worker, branch, registered):
                occupied.add(identity)
    # Include assignments from interrupted/superseded executions even if their
    # reservation is missing. Their uncertainty consumes capacity, never frees it.
    for assignment_file in (run_dir / "execution").glob("*/assignments/*/*.json"):
        assignment = read(assignment_file)
        if _present(repo, Path(assignment["worktree"]), assignment["branch"], registered):
            occupied.add((assignment["execution_id"], assignment["wave_id"], assignment["task_id"]))
    occupied.update((execution_id, wave_id, task) for task in task_ids)
    if len(task_ids) > limits["wave_task_limit"] or len(occupied) > limits["worker_worktree_limit"]:
        raise WorkflowError("RESOURCE_LIMIT", "whole wave exceeds retained worker worktree budget; reconcile owners")
    if selected.exists():
        if read(selected) != expected:
            raise WorkflowError("RESOURCE_LIMIT", "immutable wave reservation changed")
    else:
        write(selected, expected, exclusive=True)
