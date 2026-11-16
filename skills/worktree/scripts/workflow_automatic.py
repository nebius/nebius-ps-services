"""Private automatic-outer adapter used by both public workflow coordinators.

Public Worktree invocation remains explicit. This adapter uses its existing
Git lifecycle owners and binds every operation to an admitted objective.
"""
from pathlib import Path
from functools import wraps

from workflow_state import WorkflowError, digest, lock, read, write
import workflow_projects as projects


def _aggregation_transition(function, *, include_delivery=True):
    @wraps(function)
    def serialized(run_dir, *args, **kwargs):
        from workflow_timing import scope, flush
        with scope(run_dir), lock(run_dir / "aggregation/validation-lock"):
            try:
                value = function(run_dir, *args, **kwargs)
                if include_delivery:
                    from workflow_delivery_guidance import attach
                    value = attach(run_dir, value, value.get("reasons", ()))
                return value
            finally:
                # Cleanup may fail after promotion. Observe the receipts that
                # actually exist without changing the successful effects/error.
                try:
                    from workflow_statistics import safe_record
                    flush()
                    safe_record(run_dir, function.__name__)
                except Exception:
                    pass  # Reporting cannot replace a product result or error.
    return serialized


def eligible(project: Path) -> bool:
    import worktree_manager as manager
    primary, current = manager.discover_repository(project)
    if primary != current:
        return False
    try:
        default = manager._configured_default_branch(primary)
    except manager.WorktreeError:
        return False  # Existing unknown-default delivery remains the owner.
    branch = manager._git(primary, "symbolic-ref", "-q", "--short", "HEAD", allowed=(0, 1))
    return bool(branch and branch != default)


def selected(project: Path, common: Path, owner: str) -> tuple[Path, Path]:
    """Resolve an original project to its active execution checkout, read-only."""
    for row in projects.records(common):
        if row["owner"] != owner or row["state"] in projects.TERMINAL:
            continue
        outer = row["outer"]
        original = Path(row["logical_project_root"])
        execution = Path(outer["scope_cwd"]) if outer else original
        if project in {original, execution}:
            if row["state"] == "delivered" and not execution.exists():
                return original, original
            return execution, original
    return project, project


def matches(run: dict, project: Path) -> bool:
    return str(project.absolute()) in {run["project_root"], run.get("logical_project_root", run["project_root"])}


def ensure(run_dir: Path, run: dict, home: Path) -> dict:
    if not run.get("shared_admission") or run["status"] in {"done", "cancelled"}:
        return run
    import worktree_manager as manager
    from worktree_state import matching_manifests
    row = projects.get(run_dir)
    if row is None:
        row = projects.reserve(run_dir, run, home, run["coordinator_session_sha256"])
    if row["state"] in {"delivered", "cleaned"}:
        return run
    if row["state"] == "requested":
        if not projects.admit(run_dir):
            return run
        row = projects.update(run_dir, expected="requested", state="admitted")
    if row["state"] == "admitted":
        if not run.get("automatic_outer"):
            if run["owner"] == "agentic-sdlc":
                from workflow_interop import inspect_anchor
                from git_promotion import ensure_promotion_branch, GitPromotionError
                project = Path(run["project_root"])
                if inspect_anchor(project).get("status") == "unmanaged":
                    try:
                        promotion = ensure_promotion_branch(project, lifecycle_id=run_dir.name, task_slug="sdlc")
                    except GitPromotionError as error:
                        raise WorkflowError("POLICY_BLOCK", str(error)) from error
                    write(run_dir / "git-promotion.json", promotion)
                    run.update(source_branch=promotion["promotion_branch"], source_head=promotion["promotion_initial_head"])
                    write(run_dir / "run.json", run)
            projects.update(run_dir, expected="admitted", state="allocated")
            return run
        primary = Path(row["source_root"])
        if (manager._git(primary, "symbolic-ref", "--short", "HEAD") != run["source_branch"]
                or not manager._is_ancestor(primary, run["source_head"], manager._git(primary, "rev-parse", "HEAD"))):
            raise WorkflowError("SOURCE_IDENTITY_CHANGED", "automatic outer source identity changed")
        # Immutable run ID is the creation intent. Recovery never searches by
        # branch similarity or adopts another manually created lifecycle.
        slug = run_dir.name
        existing = matching_manifests(primary, scope=row["scope"], task_slug=slug)
        if len(existing) > 1:
            raise WorkflowError("RECOVERY_REQUIRED", "automatic creation has ambiguous ownership")
        if existing and existing[0].status in {"planned", "recovery"}:
            # The low-level removal owner proves exact creation-base identity
            # before removal. It refuses advanced, dirty or foreign resources.
            manager.remove_worktree(cwd=primary, name=existing[0].name)
            existing = []
        try:
            outer = manager.add_worktree(cwd=primary, project=row["scope"], task_slug=slug,
                                         reuse=existing[0].name if existing else None, automatic_run=run_dir)
        except manager.WorktreeError as error:
            raise WorkflowError("SOURCE_WAIT", str(error)) from error
        if (outer["source_branch"] != run["source_branch"]
                or not manager._is_ancestor(primary, run["source_head"], outer["base_sha"])):
            raise WorkflowError("SOURCE_IDENTITY_CHANGED", "automatic outer source identity changed")
        row = projects.update(run_dir, expected="admitted", state="allocated", outer=outer)
    if row["outer"]:
        outer = row["outer"]
        if run["project_root"] == row["logical_project_root"]:
            run.update(logical_project_root=row["logical_project_root"], project_root=outer["scope_cwd"],
                       git_root=outer["worktree"], source_branch=outer["branch"], source_head=outer["base_sha"])
            write(run_dir / "run.json", run)
        elif (run["project_root"] != outer["scope_cwd"] or run["git_root"] != outer["worktree"]
              or run["source_branch"] != outer["branch"]):
            raise WorkflowError("OWNERSHIP_INVALID", "automatic execution checkout identity changed")
        context = {key: run[key] for key in ("owner", "project_id", "project_root", "logical_project_root", "git_root", "git_common_dir")}
        write(run_dir.parent / "project.json", {"schema": "workflow/project-v2", **context})
    return run


def status(project: Path, home: Path) -> dict:
    from workflow_objectives import _git
    repo = Path(_git(project, "rev-parse", "--show-toplevel"))
    common = Path(_git(project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    relative = project.relative_to(repo).as_posix()
    items = []
    for row in projects.records(common):
        if row["home"] != str(home) or relative != "." and row["scope"] != relative:
            continue
        run_dir = Path(row["run_dir"])
        if row["owner"] in {"agentic-sdlc", "implement"}:
            from workflow_statistics import status as statistics_status
            statistics = statistics_status(run_dir)
            available = statistics.get("schema") == "workflow/statistics-report-v1"
            command = "implement" if row["owner"] == "implement" else "sdlc-start"
            items.append({"project": row["scope"], "owner": command,
                          "objective": statistics["objective"]["outcome"] if available else "unavailable",
                          "phase": statistics["phase"] if available else "unavailable",
                          "workers": statistics["task_counts"]["running"] if available else None,
                          "reserved_worker_slots": sum(r["active_slots"] for r in statistics["executions"]) if available else None,
                          "wait_reason": "shared objective capacity" if row["state"] == "requested" else None,
                          "next_action": None if row["state"] in projects.TERMINAL else f"${command} continue {row['logical_project_root']}",
                          "statistics": statistics})
            from workflow_delivery_guidance import attach
            items[-1] = attach(run_dir, items[-1])
            if items[-1].get("delivery", {}).get("reasons"):
                items[-1]["wait_reason"] = " ".join(items[-1]["delivery"]["reasons"])
            continue
    return {"status": "STATUS", "projects": items}


def validate_capability(run_dir: Path, primary: Path, name: str, lease: dict | None) -> dict:
    """Private automatic integration authority, never accepted by public CLI."""
    from agent_runtime import agent_home, native_session_id
    from workflow_objectives import authorize_coordinator
    run, _ = authorize_coordinator(run_dir, native_session_id())
    row = projects.get(run_dir)
    if (not run.get("automatic_outer") or row is None
            or row["state"] != "aggregation-ready" or Path(row["home"]) != agent_home()
            or Path(row["source_root"]) != primary or not row["outer"]
            or row["outer"]["name"] != name or lease is None
            or lease["state"] != "aggregation-ready" or lease["workspace"] != str(run_dir)
            or lease["run_id"] != run_dir.name or lease["owner_kind"] != run["owner"]
            or lease["worktree"] != row["outer"]["worktree"]
            or lease["branch"] != row["outer"]["branch"]):
        raise WorkflowError("AUTOMATIC_OWNER_INVALID", "automatic aggregation capability does not match its objective")
    local = read(run_dir / "execution/interop.json")
    if local["lease_id"] != lease["token"] or local["promoted_head"] != lease["promoted_head"]:
        raise WorkflowError("AUTOMATIC_OWNER_INVALID", "automatic aggregation lease token or promoted head changed")
    return row


@_aggregation_transition
def begin_correction(run_dir: Path, session: str, execution: str) -> dict:
    """Start a new immutable execution from a failed combined candidate.

    Completed coordinators, seals, validation operations and the old reservation
    remain historical evidence. The same unreleased outer lease continues.
    """
    import re
    import worktree_manager as manager
    import worktree_interop as interop
    from worktree_state import load_manifest, write_manifest
    from workflow_objectives import authorize_coordinator, _writers_quiescent
    if re.fullmatch(r"FEAT-[0-9]{3,}", execution) is None:
        raise WorkflowError("PLAN_INVALID", "corrective execution needs a fresh FEAT identifier")
    with lock(run_dir.parent):
        run, _ = authorize_coordinator(run_dir, session)
        _writers_quiescent(run_dir)
        row = projects.get(run_dir)
        if row is None or not run.get("automatic_outer") or row["state"] not in {"aggregation-ready", "allocated"}:
            raise WorkflowError("AGGREGATION_BLOCKED", "no automatic aggregation can be corrected")
        root = run_dir / "aggregation/corrections"
        path = root / (execution + ".json")
        if path.exists():
            intent = read(path)
            if intent.get("execution_id") != execution or intent.get("run_id") != run_dir.name:
                raise WorkflowError("OWNERSHIP_INVALID", "correction identity changed")
            if intent["status"] == "ready":
                return intent
        else:
            if (run_dir / "execution" / execution).exists():
                raise WorkflowError("PLAN_INVALID", "sealed executions cannot be reopened")
            identity, _candidate_path, _ = _candidate(run_dir)
            failure = _failed_acceptance(run_dir, identity)
            if (failure.get("status") != "verified" or failure.get("outcome") != "failed"
                    or failure.get("target") != identity):
                raise WorkflowError("RECOVERY_REQUIRED", "correction needs observed failed combined acceptance")
            old = [read(p) for p in (run_dir / "execution").glob("*/coordinator.json")]
            if not old or any(item["status"] != "done" for item in old):
                raise WorkflowError("RECOVERY_REQUIRED", "prior execution must be sealed and fully cleaned")
            primary = Path(row["source_root"])
            reservation = interop.load_reservation(primary, row["outer"]["name"])
            intent = {"schema": "workflow/aggregation-correction-v1", "run_id": run_dir.name,
                      "execution_id": execution, "target": identity, "reservation": reservation,
                      "failure": failure, "failure_sha256": digest(failure), "status": "preparing"}
            write(path, intent, exclusive=True)
        primary, name = Path(row["source_root"]), row["outer"]["name"]
        target, reservation = intent["target"], intent["reservation"]
        failure = _failed_acceptance(run_dir, target)
        if (intent.get("schema") != "workflow/aggregation-correction-v1"
                or failure != intent.get("failure") or digest(failure) != intent.get("failure_sha256")
                or failure.get("target") != target or failure.get("status") != "verified"
                or failure.get("outcome") != "failed" or not isinstance(reservation, dict)
                or reservation.get("token") != target.get("reservation")
                or reservation.get("integration_head") != target.get("head")
                or reservation.get("child_head") != target.get("child_head")):
            raise WorkflowError("RECOVERY_REQUIRED", "frozen correction evidence changed")
        outer = Path(row["outer"]["worktree"])
        with interop.integration_transition_lock(primary, name):
            with interop.interop_lock(primary):
                manifest = load_manifest(primary, name)
                if manifest.status != "active" or manifest.branch != row["outer"]["branch"]:
                    raise WorkflowError("RECOVERY_REQUIRED", "outer cannot enter corrective execution")
                if (manager._git(outer, "symbolic-ref", "--short", "HEAD") != manifest.branch
                        or manager.status_paths(outer) or manager._operation_in_progress(outer)):
                    raise WorkflowError("RECOVERY_REQUIRED", "corrective outer identity or cleanliness changed")
                head = manager._git(outer, "rev-parse", "HEAD")
                if head not in {target["child_head"], target["head"]}:
                    raise WorkflowError("RECOVERY_REQUIRED", "outer advanced outside its corrective intent")
                if manager._git(outer, "rev-parse", target["head"] + "^{tree}") != target["tree"]:
                    raise WorkflowError("RECOVERY_REQUIRED", "frozen corrective candidate changed")
                lease = interop.lifecycle_lease(primary, name, allow_external_transition=True)
                local = read(run_dir / "execution/interop.json")
                if (lease is None or lease["token"] != local["lease_id"]
                        or lease["workspace"] != str(run_dir)
                        or lease["state"] not in {"aggregation-ready", "active"}
                        or lease["promoted_head"] not in {target["child_head"], target["head"]}):
                    raise WorkflowError("AUTOMATIC_OWNER_INVALID", "corrective lease cannot be reacquired or replaced")
                current = interop.load_reservation(primary, name)
                if current != reservation and not (current is None and head == target["head"]
                                                   and lease["promoted_head"] == target["head"]):
                    raise WorkflowError("RECOVERY_REQUIRED", "aggregation reservation changed during correction")
                # Ownership and the immutable failed candidate are proved before Git changes.
                if head != target["head"]:
                    manager._git(outer, "merge", "--ff-only", target["head"])
                lease["state"] = "active"
                if lease["promoted_head"] != target["head"]:
                    lease["promotion_heads"].append(target["head"])
                    lease["promoted_head"] = target["head"]
                interop.validate_lease(lease, name)
                interop._atomic_json(interop.lease_path(primary, name), lease)
                write_manifest(primary, manifest.updated(lease_state="active"))
                local["promoted_head"] = target["head"]
                write(run_dir / "execution/interop.json", local)
            current = interop.load_reservation(primary, name)
            if current is not None:
                if current != reservation:
                    raise WorkflowError("RECOVERY_REQUIRED", "aggregation reservation changed during correction")
                manager._remove_integration_candidate(primary, current, abort_merge=False)
                interop.end_integration(primary, name=name, reservation_id=current["token"])
        run.update(source_head=target["head"], corrective_execution=execution)
        write(run_dir / "run.json", run)
        if row["state"] == "aggregation-ready":
            projects.update(run_dir, expected="aggregation-ready", state="allocated")
        intent["status"] = "ready"
        write(path, intent)
        return intent

def _failed_acceptance(run_dir, target):
    operation = run_dir / "aggregation/operations" / (digest(target) + ".json")
    if operation.exists():
        value = read(operation)
        if value.get("status") == "verified" and value.get("outcome") == "failed":
            return value
    from workflow_aggregation_gates import load
    gates = load(run_dir, target, historical=True)
    if gates["outcome"] != "failed":
        raise WorkflowError("RECOVERY_REQUIRED", "no failed aggregate acceptance is recorded")
    return {"schema": "workflow/aggregation-gate-failure-v1", "target": target,
            "status": "verified", "outcome": "failed", "gates_sha256": digest(gates)}


@_aggregation_transition
def prepare_aggregation(run_dir: Path, session: str) -> dict:
    from workflow_objectives import authorize_coordinator, _writers_quiescent
    from workflow_interop import load, _reconcile
    import worktree_manager as manager
    import worktree_interop as interop
    from worktree_state import load_manifest, write_manifest
    run, _ = authorize_coordinator(run_dir, session)
    row = projects.get(run_dir)
    if not run.get("automatic_outer") or row is None:
        raise WorkflowError("AUTOMATIC_OWNER_INVALID", "objective does not own an automatic outer")
    _writers_quiescent(run_dir)
    executions = [read(path) for path in (run_dir / "execution").glob("*/coordinator.json")]
    if run.get("fence") or not executions or any(item["status"] != "done" for item in executions):
        raise WorkflowError("AGGREGATION_BLOCKED", "complete inner execution and final gates before aggregation")
    correction = run.get("corrective_execution")
    if correction and not (run_dir / "execution" / correction / "coordinator.json").exists():
        raise WorkflowError("AGGREGATION_BLOCKED", "complete the fresh corrective execution before aggregation")
    if row["state"] == "allocated":
        row = projects.update(run_dir, expected="allocated", state="aggregation-ready")
    if row["state"] in {"delivered", "cleaned"}:
        return {"status": "DELIVERED", "delivery": row["delivery"]}
    if row["state"] != "aggregation-ready":
        raise WorkflowError("AGGREGATION_BLOCKED", "objective is not ready for aggregation")
    primary = Path(row["source_root"])
    name = row["outer"]["name"]
    state = load(run_dir)
    if state is None or state["mode"] != "managed":
        raise WorkflowError("AUTOMATIC_OWNER_INVALID", "automatic outer lease is missing")
    _reconcile(run_dir, Path(run["project_root"]), state)
    with interop.interop_lock(primary):
        lease = interop.lifecycle_lease(primary, name, allow_external_transition=True)
        if (lease is None or lease["token"] != state["lease_id"]
                or lease["promoted_head"] != state["promoted_head"]
                or lease["state"] not in {"active", "aggregation-ready"}):
            raise WorkflowError("AUTOMATIC_OWNER_INVALID", "automatic aggregation cannot reopen a released lease")
        registered = {Path(item.path).resolve() for item in manager.list_worktrees(primary)}
        branches = set(manager._git(primary, "for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines())
        interop.validate_absent_resources(lease, registered_worktrees=registered, existing_branches=branches)
        lease["state"] = "aggregation-ready"
        interop.validate_lease(lease, name)
        interop._atomic_json(interop.lease_path(primary, name), lease)
        manifest = load_manifest(primary, name)
        write_manifest(primary, manifest.updated(lease_state="aggregation-ready"))
    reservation = interop.load_reservation(primary, name)
    source_head = manager._git(primary, "rev-parse", "HEAD")
    restart = reservation is not None and source_head not in {reservation["source_head"], reservation.get("integration_head")}
    preflight = manager.integration_preflight(cwd=primary, name=name, automatic_run=run_dir, restart=restart)
    if preflight["blockers"] or preflight.get("source_dirty_paths"):
        reasons = list(preflight["blockers"])
        if preflight.get("source_dirty_paths"):
            reasons.append("The original checkout has uncommitted changes.")
        return {"status": "AGGREGATION_WAIT", "reasons": reasons}
    if restart:
        for path in list((run_dir / "aggregation/operations").glob("*.json")) + list((run_dir / "aggregation/gates").glob("*.json")):
            prior = read(path)
            if "target" not in prior:
                continue
            if prior["target"]["reservation"] == reservation["token"] and prior.get("outcome") != "passed":
                raise WorkflowError("RECOVERY_REQUIRED", "failed acceptance requires corrective execution before a new candidate")
        projects.charge_rebuild(run_dir, reservation["token"])
    try:
        candidate = manager.integrate_worktree(cwd=primary, name=name, validated_head=None, restart=restart,
                                              expected_source_head=preflight["source_head"],
                                              expected_child_head=preflight["child_head"], automatic_run=run_dir)
    except manager.WorktreeError:
        if manager.status_paths(primary):
            return {"status": "AGGREGATION_WAIT", "reasons": ["The original checkout became dirty during delivery preparation."]}
        raise
    write(run_dir / "aggregation/candidate.json", candidate)
    return candidate


def _candidate(run_dir: Path) -> tuple[dict, Path, dict]:
    import worktree_manager as manager
    import worktree_interop as interop
    row = projects.get(run_dir)
    if row is None or not row["outer"]:
        raise WorkflowError("AUTOMATIC_OWNER_INVALID", "automatic outer is missing")
    primary = Path(row["source_root"])
    name = row["outer"]["name"]
    validate_capability(run_dir, primary, name, interop.lifecycle_lease(primary, name))
    reservation = interop.load_reservation(primary, name)
    if reservation is None or reservation["state"] != "ready":
        raise WorkflowError("AGGREGATION_BLOCKED", "combined candidate is not ready")
    manager._require_candidate_identity(primary, reservation)
    candidate = Path(reservation["integration_worktree"])
    if (manager.status_paths(candidate) or manager._git(candidate, "rev-parse", "HEAD") != reservation["integration_head"]
            or manager._merge_parents(candidate, reservation["integration_head"]) != (reservation["source_head"], reservation["child_head"])):
        raise WorkflowError("AGGREGATION_BLOCKED", "combined candidate changed")
    identity = {"run_id": run_dir.name, "source_head": reservation["source_head"],
                "child_head": reservation["child_head"], "head": reservation["integration_head"],
                "tree": manager._git(candidate, "rev-parse", "HEAD^{tree}"), "reservation": reservation["token"]}
    return identity, candidate, row


@_aggregation_transition
def validate_aggregation(run_dir: Path, session: str) -> dict:
    """Run frozen acceptance through the existing bounded evaluator owner."""
    import hashlib
    import workflow_validation as validation
    from workflow_objectives import authorize_coordinator
    root = run_dir / "aggregation"
    with lock(root / "validation-lock"):
        run, _ = authorize_coordinator(run_dir, session)
        identity, cwd, _row = _candidate(run_dir)
        checks = []
        plans = {}
        bases = _frozen_bases(run_dir)
        for path in sorted((run_dir / "execution").glob("*/coordinator.json")):
            plan = validation.load_plan(run_dir, path.parent.name)
            plans[path.parent.name] = digest(plan)
            checks.extend(plan["wave_checks"])
        if not checks:
            raise WorkflowError("VALIDATION_PLAN_INVALID", "aggregation requires frozen acceptance checks")
        operation = root / "operations" / (digest(identity) + ".json")
        intent = {"schema": "workflow/aggregation-validation-v1", "target": identity,
                  "plans": plans, "bases": bases, "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "session_sha256": run["coordinator_session_sha256"], "status": "running"}
        if operation.exists():
            prior = read(operation)
            if (prior.get("status") == "verified" and prior.get("outcome") == "passed"
                    and all(prior.get(key) == value for key, value in intent.items() if key not in {"status", "session_sha256"})):
                _publish_validation(run_dir, prior)
                return prior
            raise WorkflowError("RECOVERY_REQUIRED", "prior aggregation validation is retained; classify failure before corrective execution")
        write(operation, intent, exclusive=True)
        outcomes = []
        try:
            for check in checks:
                outcomes.append(validation._run_check(check, cwd))
                if outcomes[-1]["outcome"] != "passed":
                    break
            authorize_coordinator(run_dir, session)
            if _candidate(run_dir)[0] != identity:
                raise WorkflowError("VALIDATION_TARGET_INVALID", "aggregation acceptance changed the candidate")
            outcome = "passed" if len(outcomes) == len(checks) and all(item["outcome"] == "passed" for item in outcomes) else "failed"
            receipt = {**intent, "status": "verified", "outcome": outcome, "outcomes": outcomes}
            write(operation, receipt)
            if outcome != "passed":
                raise WorkflowError("AGGREGATION_VALIDATION_FAILED", "combined acceptance failed; preserve the candidate for diagnosis")
            _publish_validation(run_dir, receipt)
            return receipt
        except BaseException as error:
            if read(operation)["status"] == "running":
                write(operation, {**intent, "status": "interrupted", "termination_proven": not isinstance(error, validation.ProcessCleanupError),
                                  "outcomes": outcomes, "failure_code": getattr(error, "code", "VALIDATION_INTERRUPTED")})
            raise


def _verify_validation(run_dir: Path, receipt: dict, *, require_index: bool = True, historical: bool = False) -> None:
    import hashlib
    import re
    import workflow_validation as validation
    plans, checks = {}, []
    bases = _frozen_bases(run_dir, historical=historical)
    for path in sorted((run_dir / "execution").glob("*/coordinator.json")):
        plan = validation.load_plan(run_dir, path.parent.name)
        plans[path.parent.name] = digest(plan)
        checks.extend(plan["wave_checks"])
    outcomes = receipt.get("outcomes", [])
    sha = re.compile(r"[0-9a-f]{64}\Z")
    if (receipt.get("schema") != "workflow/aggregation-validation-v1"
            or receipt.get("status") != "verified" or receipt.get("outcome") != "passed"
            or receipt.get("bases") != bases or receipt.get("plans") != plans or not checks or len(outcomes) != len(checks)
            or sha.fullmatch(str(receipt.get("producer_sha256"))) is None
            or not historical and receipt.get("producer_sha256") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            or any(item.get("id") != check["id"] or item.get("check_sha256") != validation.digest(check)
                   or item.get("outcome") != "passed" or item.get("exit_code") != 0
                   or len(item.get("output_sha256", [])) != 2
                   or any(sha.fullmatch(str(value)) is None for value in
                          [item.get("environment_sha256"), item.get("executable_sha256"), *item.get("output_sha256", [])])
                   for item, check in zip(outcomes, checks))):
        raise WorkflowError("RESULT_INVALID", "aggregation receipt does not prove every frozen acceptance check")
    if require_index:
        index = read(run_dir / "aggregation/produced" / (digest(receipt["target"]) + ".json"))
        if index != {"receipt_sha256": digest(receipt)}:
            raise WorkflowError("RESULT_INVALID", "aggregation receipt changed after producer publication")


def _publish_validation(run_dir: Path, receipt: dict) -> None:
    _verify_validation(run_dir, receipt, require_index=False)
    path = run_dir / "aggregation/produced" / (digest(receipt["target"]) + ".json")
    value = {"receipt_sha256": digest(receipt)}
    if path.exists():
        if read(path) != value:
            raise WorkflowError("RESULT_INVALID", "aggregation producer index already differs")
    else:
        write(path, value, exclusive=True)


@_aggregation_transition
def finish_aggregation(run_dir: Path, session: str) -> dict:
    from workflow_objectives import authorize_coordinator, _writers_quiescent
    from workflow_interop import release, complete_source_integration
    import worktree_manager as manager
    from workflow_aggregation_gates import load as load_gates
    run, _ = authorize_coordinator(run_dir, session)
    _writers_quiescent(run_dir)
    row = projects.get(run_dir)
    if row is None or not run.get("automatic_outer"):
        raise WorkflowError("AUTOMATIC_OWNER_INVALID", "objective does not own an automatic outer")
    if row["state"] == "cleaned":
        verified = verify_delivery(run_dir)
        projects.release(run_dir)
        return verified
    primary = Path(row["source_root"])
    if row["state"] == "aggregation-ready":
        # A crash after Git promotion may already have removed the candidate.
        from worktree_state import load_manifest
        manifest = load_manifest(primary, row["outer"]["name"])
        if manifest.status == "integrated":
            manager._verify_recorded_integration(primary, manifest, automatic_run=run_dir)
            candidates = [read(path) for path in (run_dir / "aggregation/operations").glob("*.json")]
            receipts = [item for item in candidates if item.get("status") == "verified" and item.get("outcome") == "passed"
                        and item["target"]["head"] == manifest.integration_head]
            if len(receipts) != 1:
                raise WorkflowError("RESULT_INVALID", "promoted aggregate has no unique validation receipt")
            receipt = receipts[0]
        else:
            identity, _cwd, _ = _candidate(run_dir)
            receipt = read(run_dir / "aggregation/operations" / (digest(identity) + ".json"))
            if receipt.get("status") != "verified" or receipt.get("outcome") != "passed" or receipt.get("target") != identity:
                raise WorkflowError("RESULT_INVALID", "aggregation needs its exact passing producer receipt")
        _verify_validation(run_dir, receipt)
        target = receipt["target"]
        gates = load_gates(run_dir, target, historical=manifest.status == "integrated")
        if gates["outcome"] != "passed":
            raise WorkflowError("AGGREGATION_GATES_FAILED", "aggregate reviews failed; preserve the candidate for correction")
        manager.integrate_worktree(cwd=primary, name=row["outer"]["name"], validated_head=target["head"], restart=False,
                                   expected_source_head=target["source_head"], expected_child_head=target["child_head"], automatic_run=run_dir)
        delivery = {"schema": "workflow/automatic-delivery-v1", "run_id": run_dir.name,
                    "source_branch": row["outer"]["source_branch"], "source_root": str(primary),
                    "head": target["head"], "child_head": target["child_head"], "tree": target["tree"],
                    "validation_sha256": digest(receipt), "target": target, "gates_sha256": digest(gates),
                    "outer": row["outer"], "cleanup": None,
                    "executions": _execution_proofs(run_dir)}
        write(run_dir / "aggregation/delivery.json", delivery, exclusive=not (run_dir / "aggregation/delivery.json").exists())
        row = projects.update(run_dir, expected="aggregation-ready", state="delivered", delivery=delivery)
    if row["state"] != "delivered":
        raise WorkflowError("AGGREGATION_BLOCKED", "aggregation is not delivered")
    delivery = row["delivery"]
    outer_project = Path(row["outer"]["scope_cwd"])
    if Path(row["outer"]["worktree"]).exists():
        gates = load_gates(run_dir, delivery["target"], historical=True)
        if digest(gates) != delivery["gates_sha256"] or gates["outcome"] != "passed":
            raise WorkflowError("AGGREGATION_GATES_INVALID", "delivered final gate evidence changed")
        refs = {gate: reference["path"] + "#" + reference["sha256"]
                for gate, reference in gates["inner"].items()}
        release(run_dir, outer_project, delivery["child_head"], final_alignment=refs["alignment"],
                uat=refs["uat"], docs=refs["docs"])
        complete_source_integration(run_dir, outer_project)
    # The caller/native session stays in its original project. Do not remove a
    # checkout if the current process still uses it as its working directory.
    if Path.cwd().resolve().is_relative_to(Path(row["outer"]["worktree"])):
        raise WorkflowError("CLEANUP_WAIT", "continue from the original project folder to finish cleanup")
    cleanup = manager.remove_worktree(cwd=primary, name=row["outer"]["name"])
    delivery = {**delivery, "cleanup": cleanup["status"]}
    write(run_dir / "aggregation/delivery.json", delivery)
    projects.update(run_dir, expected="delivered", state="cleaned", delivery=delivery)
    projects.release(run_dir)
    return verify_delivery(run_dir)


def _frozen_bases(run_dir: Path, *, historical: bool = False) -> dict:
    """Validate immutable helper and instruction identities before new effects."""
    import workflow_impact as impact
    import workflow_validation as validation
    import re
    run = read(run_dir / "run.json")
    current = None if historical else impact._identity_files(Path(run["project_root"]), owner=run["owner"])
    result = {}
    for path in sorted((run_dir / "execution").glob("*/coordinator.json")):
        execution = path.parent.name
        pointer = read(run_dir / "objective-impact/execution" / (execution + ".json"))
        fingerprint = pointer.get("sha256", "")
        if (pointer.get("schema") != "workflow/execution-basis-pointer-v2"
                or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
                or pointer.get("path") != f"bases/{execution}/{fingerprint}.json"):
            raise WorkflowError("BASIS_INVALID", "aggregation execution pointer changed")
        basis = read(run_dir / pointer["path"])
        from workflow_inputs import verify_basis
        verify_basis(run_dir, basis)
        # Execution fingerprints retain the producer's compact serialization.
        if (impact.canonical_digest(basis) != fingerprint or basis.get("execution_id") != execution
                or basis.get("validation_plan_sha256") != impact.canonical_digest(validation.load_plan(run_dir, execution))
                or not basis.get("identities")
                or not historical and basis["identities"] != current):
            raise WorkflowError("BASIS_INVALID", "aggregation frozen helper or instruction closure changed")
        result[execution] = fingerprint
    if not result:
        raise WorkflowError("BASIS_INVALID", "aggregation needs immutable execution bases")
    return result


def _execution_proofs(run_dir: Path) -> dict:
    result = {}
    for path in sorted((run_dir / "execution").glob("*/coordinator.json")):
        execution = path.parent.name
        pointer = run_dir / "objective-impact/execution" / (execution + ".json")
        if not pointer.exists():
            raise WorkflowError("BASIS_INVALID", "automatic delivery requires every immutable execution basis")
        coordinator = read(path)
        if coordinator.get("status") != "done" or not coordinator.get("final_evidence"):
            raise WorkflowError("COMPLETION_BLOCKED", "execution final gates are missing")
        result[execution] = {"coordinator_sha256": digest(coordinator),
                             "basis": read(pointer)}
    return result


def historical_execution(run_dir: Path, execution: str) -> dict:
    """Verify frozen delivered evidence without recreating removed checkouts."""
    import workflow_impact as impact
    verify_delivery(run_dir)
    proof = read(run_dir / "aggregation/delivery.json")["executions"].get(execution)
    if not proof or proof["basis"] is None:
        raise WorkflowError("BASIS_INVALID", "delivered execution basis is missing")
    pointer = proof["basis"]
    fingerprint = pointer.get("sha256", "")
    if (pointer.get("schema") != "workflow/execution-basis-pointer-v2"
            or pointer.get("path") != f"bases/{execution}/{fingerprint}.json"):
        raise WorkflowError("BASIS_INVALID", "historical execution pointer changed")
    basis = read(run_dir / pointer["path"])
    if impact.canonical_digest(basis) != fingerprint:
        raise WorkflowError("BASIS_INVALID", "historical execution basis changed")
    from workflow_inputs import verify_basis
    verify_basis(run_dir, basis)
    return basis

def verify_delivery(run_dir: Path) -> dict:
    """Completion proof remains valid after every owned checkout is removed."""
    import worktree_manager as manager
    row = projects.get(run_dir)
    receipt = read(run_dir / "aggregation/delivery.json")
    if row is None or row["state"] != "cleaned" or row["delivery"] != receipt:
        raise WorkflowError("COMPLETION_BLOCKED", "automatic delivery and cleanup are incomplete")
    if receipt.get("executions") != _execution_proofs(run_dir):
        raise WorkflowError("COMPLETION_BLOCKED", "delivered execution evidence changed")
    primary = Path(row["source_root"])
    tip = manager._git(primary, "rev-parse", "refs/heads/" + receipt["source_branch"])
    if not manager._is_ancestor(primary, receipt["head"], tip):
        raise WorkflowError("COMPLETION_BLOCKED", "automatic delivery is no longer in aggregate history")
    proofs = [read(path) for path in (run_dir / "aggregation/operations").glob("*.json")]
    proofs = [proof for proof in proofs if digest(proof) == receipt["validation_sha256"]]
    if len(proofs) != 1:
        raise WorkflowError("COMPLETION_BLOCKED", "automatic delivery validation receipt is missing")
    _verify_validation(run_dir, proofs[0], historical=True)
    target = proofs[0]["target"]
    from workflow_aggregation_gates import load as load_gates
    gates = load_gates(run_dir, target, historical=True)
    if target != receipt.get("target") or digest(gates) != receipt.get("gates_sha256") or gates["outcome"] != "passed":
        raise WorkflowError("COMPLETION_BLOCKED", "aggregate final gates are missing or changed")
    if (target["head"] != receipt["head"] or target["child_head"] != receipt["child_head"]
            or target["tree"] != receipt["tree"]
            or manager._merge_parents(primary, receipt["head"]) != (target["source_head"], target["child_head"])
            or manager._git(primary, "rev-parse", receipt["head"] + "^{tree}") != receipt["tree"]):
        raise WorkflowError("COMPLETION_BLOCKED", "automatic delivery commit differs from its validated candidate")
    outer = row["outer"]
    if (Path(outer["worktree"]).exists() or Path(outer["worktree"]).is_symlink()
            or manager._local_branch_exists(primary, outer["branch"])
            or any(Path(item.path).resolve() == Path(outer["worktree"]) for item in manager.list_worktrees(primary))):
        raise WorkflowError("CLEANUP_REQUIRED", "automatic outer resources remain or reappeared")
    return {"status": "COMPLETE", "source_head": tip, "delivered_head": receipt["head"],
            "project_root": row["logical_project_root"], "cleanup": "verified"}
