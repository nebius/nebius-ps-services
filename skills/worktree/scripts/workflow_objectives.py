"""Accepted objectives and explicit session bindings, without conversation capture.

The root coordinator supplies a normalized objective. These primitives validate
and persist it; hooks never infer intent, select a run, or start execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import subprocess
import uuid

from workflow_state import git_environment, WorkflowError, digest, directory, lock, read, stable, write
from workflow_security import contains_sensitive


OBJECTIVE_SCHEMA = "workflow/accepted-objective-v3"
RUN_SCHEMA = "workflow/run-v4"
BINDING_SCHEMA = "workflow/session-binding-v1"
ROOTS = {"implement": "implement", "agentic-sdlc": "sdlc-runs"}
TERMINAL = {"done", "cancelled"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"run-[a-z0-9][a-z0-9-]{0,79}\Z")
TEXT_FIELDS = {"outcome", "scope", "constraints", "acceptance_criteria", "clarifications"}
FIELDS = TEXT_FIELDS | {"provenance", "inputs", "completion", "issue_resolution", "uat"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def session_hash(session: str) -> str:
    if not isinstance(session, str) or not session.strip():
        raise WorkflowError("NATIVE_IDENTITY_REQUIRED", "a native coordinator session is required")
    return hashlib.sha256(session.encode()).hexdigest()


def normalize(value: object) -> dict:
    if not isinstance(value, dict) or not (TEXT_FIELDS | {"provenance"}) <= set(value) or set(value) - FIELDS:
        raise WorkflowError("OBJECTIVE_INVALID", "supply only normalized outcome, scope, constraints, acceptance criteria, clarifications and provenance")
    result = {}
    for key in TEXT_FIELDS:
        items = [value[key]] if key == "outcome" else value[key]
        if not isinstance(items, list) or len(items) > 256:
            raise WorkflowError("OBJECTIVE_INVALID", "objective fields must be bounded lists")
        if key in {"outcome", "scope", "acceptance_criteria"} and not items:
            raise WorkflowError("OBJECTIVE_INVALID", f"{key} is required")
        cleaned = []
        for item in items:
            if not isinstance(item, str) or not item.strip() or len(item) > 8192:
                raise WorkflowError("OBJECTIVE_INVALID", "objective statements must be bounded nonempty text")
            text = " ".join(item.split())
            if contains_sensitive(text):
                raise WorkflowError("SENSITIVE_INPUT", "remove sensitive material before accepting intent")
            cleaned.append(text)
        result[key] = cleaned[0] if key == "outcome" else cleaned
    provenance = value["provenance"]
    if (not isinstance(provenance, dict) or set(provenance) != {"kind", "source_sha256"}
            or provenance["kind"] not in {"direct_request", "current_plan", "steering"}
            or not SHA.fullmatch(str(provenance["source_sha256"]))):
        raise WorkflowError("OBJECTIVE_INVALID", "provenance requires a source kind and digest, never raw turns")
    result["provenance"] = dict(provenance)
    from workflow_uat import normalize as normalize_uat
    result["uat"] = normalize_uat(value.get("uat"))
    inputs = value.get("inputs")
    if inputs is not None:
        if (not isinstance(inputs, dict) or set(inputs) != {"manifest", "manifest_sha256", "program_sha256"}
                or not isinstance(inputs["manifest"], str)
                or not inputs["manifest"].startswith("docs/")
                or ".." in Path(inputs["manifest"]).parts or "\\" in inputs["manifest"]
                or any(SHA.fullmatch(str(inputs[key])) is None for key in ("manifest_sha256", "program_sha256"))):
            raise WorkflowError("OBJECTIVE_INVALID", "document input must bind portable manifest and program digests")
    result["inputs"] = inputs
    completion = value.get("completion", {"boundary": "reviewed-pr"})
    if completion == {"boundary": "reviewed-pr"}:
        completion = {"boundary": "reviewed-pr", "live_operations": [], "allow_nonruntime_na": True}
    if (isinstance(completion, dict) and set(completion) == {"boundary", "live_operations", "allow_nonruntime_na"}
            and completion["boundary"] in {"reviewed-pr", "local-promoted"} and type(completion["allow_nonruntime_na"]) is bool
            and isinstance(completion["live_operations"], list)
            and all(isinstance(v, str) and v.strip() for v in completion["live_operations"])):
        result["completion"] = completion
    elif (isinstance(completion, dict) and set(completion) == {"boundary", "repository", "base", "method", "delivery_scope", "live_operations", "merge_effects", "allow_nonruntime_na"}
          and completion["boundary"] == "merge-verified"
          and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(completion["repository"]))
          and isinstance(completion["base"], str) and completion["base"]
          and completion["method"] in {"merge", "squash", "rebase"}
          and type(completion["allow_nonruntime_na"]) is bool
          and all(isinstance(completion[k], list) and all(isinstance(v, str) and v.strip() for v in completion[k]) for k in ("delivery_scope", "live_operations", "merge_effects"))
          and completion["delivery_scope"] and completion["merge_effects"]
          and all(re.fullmatch(r"SLICE-[0-9]{3,}", v) for v in completion["delivery_scope"])):
        result["completion"] = completion
    else:
        raise WorkflowError("OBJECTIVE_INVALID", "completion policy must declare a supported boundary and bounded authority")
    if "issue_resolution" in value:
        from workflow_issue_resolution import metadata
        result["issue_resolution"] = metadata(value["issue_resolution"])
    if contains_sensitive(str(result)):
        raise WorkflowError("SENSITIVE_INPUT", "objective policy must not contain sensitive values")
    if len(stable(result)) > 128 * 1024:
        raise WorkflowError("OBJECTIVE_INVALID", "normalized objective is too large")
    return result


def _git(project: Path, *args):
    result = subprocess.run(["git", "-C", str(project), *args], text=True, capture_output=True, timeout=30, env=git_environment(project))
    if result.returncode:
        raise WorkflowError("UNSUPPORTED_GIT_STATE", "a named branch with committed history is required")
    return result.stdout.strip()


def project_context(project: Path, home: Path, owner: str):
    if owner not in ROOTS:
        raise WorkflowError("OWNER_INVALID", "unknown workflow owner")
    project = project.expanduser().absolute()
    with directory(project):
        pass
    repo = Path(_git(project, "rev-parse", "--show-toplevel"))
    common = Path(_git(project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    from workflow_automatic import selected
    from workflow_projects import project_id
    relative = project.relative_to(repo).as_posix()
    project, logical = selected(project, common, owner)
    repo = Path(_git(project, "rev-parse", "--show-toplevel"))
    branch = _git(project, "symbolic-ref", "--short", "HEAD")
    head = _git(project, "rev-parse", "--verify", "HEAD^{commit}")
    home = home.expanduser().absolute()
    if home.is_relative_to(repo) or repo.is_relative_to(home):
        raise WorkflowError("UNSAFE_STATE", "agent state must be outside the project Git tree")
    identity = project_id(common, relative)
    root = home / ROOTS[owner] / identity
    context = {"schema": "workflow/project-v2", "owner": owner, "project_id": identity,
               "project_root": str(project), "logical_project_root": str(logical),
               "git_root": str(repo), "git_common_dir": str(common)}
    return root, context, branch, head


def _bindings(home: Path, owner: str):
    return home / ROOTS[owner] / "bindings"


def _binding_path(home: Path, owner: str, session: str):
    return _bindings(home, owner) / (session_hash(session) + ".json")


def validate_run(run_dir: Path, *, owner: str | None = None) -> tuple[dict, dict]:
    run = read(run_dir / "run.json")
    if run.get("schema") != RUN_SCHEMA:
        raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "obsolete runs are preserved; start a fresh direct-objective run")
    if (run.get("owner") not in ROOTS or owner is not None and run["owner"] != owner
            or run.get("run_id") != run_dir.name or not RUN_ID.fullmatch(run_dir.name)):
        raise WorkflowError("OWNERSHIP_INVALID", "run identity does not match its owner")
    binding = read(run_dir / "objective.json")
    if (binding.get("schema") != OBJECTIVE_SCHEMA or binding.get("run_id") != run_dir.name
            or binding.get("owner") != run["owner"]):
        raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "objective state is missing or unsupported")
    revisions = binding.get("revisions")
    if not isinstance(revisions, list) or not revisions:
        raise WorkflowError("OBJECTIVE_INVALID", "accepted objective has no revisions")
    parent = None
    for index, revision in enumerate(revisions, 1):
        name = f"r{index:04d}"
        if (revision.get("revision") != name or revision.get("parent_sha256") != parent
                or revision.get("snapshot") != f"objectives/{name}.json"):
            raise WorkflowError("OBJECTIVE_INVALID", "objective revision ancestry is invalid")
        snapshot = read(run_dir / revision["snapshot"])
        if "issue_resolution" in snapshot and run["owner"] != "implement":
            raise WorkflowError("OBJECTIVE_INVALID", "issue resolution is owned by Implement")
        if digest(snapshot) != revision.get("sha256") or normalize(snapshot) != snapshot:
            raise WorkflowError("OBJECTIVE_INVALID", "accepted objective bytes changed")
        if digest({key: snapshot[key] for key in snapshot if key != "provenance"}) != revision.get("intent_sha256"):
            raise WorkflowError("OBJECTIVE_INVALID", "accepted objective semantic identity changed")
        parent = revision["sha256"]
    return run, binding


def observe_binding(home: Path, owner: str, session: str, project: Path | None = None):
    """Read-only restoration; deliberately has no active-run fallback."""
    path = _binding_path(home, owner, session)
    if not path.exists():
        return None
    value = read(path)
    if (value.get("schema") != BINDING_SCHEMA or value.get("owner") != owner
            or value.get("session_sha256") != session_hash(session)):
        raise WorkflowError("BINDING_INVALID", "session binding is invalid")
    run_dir = Path(str(value.get("run_dir", "")))
    root = home / ROOTS[owner]
    if not run_dir.is_absolute() or run_dir.parent.parent != root:
        raise WorkflowError("BINDING_INVALID", "session binding escapes its owner root")
    run, objective = validate_run(run_dir, owner=owner)
    from workflow_automatic import matches
    if (value.get("project_root") != run.get("project_root")
            or project is not None and not matches(run, project)
            or value.get("objective_id") != objective.get("objective_id")
            or run.get("coordinator_session_sha256") != session_hash(session)):
        raise WorkflowError("BINDING_INVALID", "session does not own this project run")
    return run_dir, run, objective


def _bind(home, owner, session, run_dir, run, objective):
    write(_binding_path(home, owner, session), {
        "schema": BINDING_SCHEMA, "owner": owner, "session_sha256": session_hash(session),
        "run_dir": str(run_dir), "project_root": run["project_root"],
        "objective_id": objective["objective_id"],
    })
    write(run_dir.parent / "active.lock", {
        "schema": "workflow/coordinator-ownership-v1", "owner": owner,
        "project_id": run["project_id"], "project_root": run["project_root"],
        "run_id": run_dir.name, "status": run["status"],
        "owner_session_hash": session_hash(session), "updated_at": now(),
    })


def _revision(run_dir, objective, value):
    value = normalize(value)
    revisions = objective["revisions"]
    name = f"r{len(revisions) + 1:04d}"
    path = run_dir / "objectives" / (name + ".json")
    if path.exists():
        if read(path) != value:
            raise WorkflowError("RECOVERY_REQUIRED", "conflicting interrupted objective acceptance is retained")
    else:
        write(path, value, exclusive=True)
    revisions.append({"revision": name, "sha256": digest(value),
                      "intent_sha256": digest({key: value[key] for key in value if key != "provenance"}),
                      "snapshot": f"objectives/{name}.json", "accepted_at": now(),
                      "parent_sha256": revisions[-1]["sha256"] if revisions else None})
    write(run_dir / "objective.json", objective)


def _acquire_intake_lease(run_dir, run):
    """Reserve a managed SDLC source while it is still clean, before authoring."""
    if (run["owner"] != "agentic-sdlc" and not run.get("automatic_outer")) or run["status"] in TERMINAL:
        return
    if run.get("shared_admission"):
        from workflow_projects import get
        if get(run_dir)["state"] == "requested":
            return
    from workflow_interop import acquire, inspect_anchor, load, ExecutionInteropError
    project = Path(run["project_root"])
    try:
        existing = load(run_dir)
        if existing is not None:
            return  # Preparation reconciles contract commits before the clean gate.
        if inspect_anchor(project).get("status") == "managed":
            acquire(run_dir, project, project.relative_to(Path(run["git_root"])).as_posix(),
                    run["source_head"])
    except ExecutionInteropError as error:
        raise WorkflowError("OWNERSHIP_INVALID", str(error)) from error


def accept(project: Path, home: Path, owner: str, session: str, value: dict, *, revise: bool = False,
           input_dependencies: dict | None = None):
    try:
        from workflow_statistics import safe_clock
        statistics_started = safe_clock()
    except Exception:
        statistics_started = None
    from workflow_automatic import eligible, ensure
    value = normalize(value)
    if "issue_resolution" in value and owner != "implement":
        raise WorkflowError("OBJECTIVE_INVALID", "issue resolution is owned by Implement")
    root, context, branch, head = project_context(project, home, owner)
    with lock(root):
        if (root / "workspace.json").exists():
            raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "managed prompt state is unsupported and remains untouched")
        project_file = root / "project.json"
        if project_file.exists():
            prior = read(project_file)
            # A fresh objective may select another physical checkout after the
            # previous run ends. Per-run paths remain immutable below.
            if any(prior.get(key) != context[key] for key in
                   ("schema", "owner", "project_id", "git_common_dir")):
                raise WorkflowError("OWNERSHIP_INVALID", "logical project identity changed")
        bound = observe_binding(home, owner, session, project)
        if bound:
            run_dir, run, objective = bound
            read(run_dir / objective["revisions"][-1]["snapshot"])
            intent = {key: value[key] for key in value if key != "provenance"}
            if digest(intent) == objective["revisions"][-1]["intent_sha256"]:
                if input_dependencies is not None:
                    from workflow_inputs import declare
                    declare(run_dir, input_dependencies)
                run = ensure(run_dir, run, home)
                _bind(home, owner, session, run_dir, run, objective)
                _acquire_intake_lease(run_dir, run)
                return result(run_dir, run, objective, "ALREADY_COMPLETE" if run["status"] in TERMINAL else "CONTINUE")
            if run["status"] not in TERMINAL:
                if not revise:
                    raise WorkflowError("OBJECTIVE_ACTIVE", "accept material steering explicitly; unrelated objectives are not queued")
                # The fence is durable before acceptance. Existing writers may
                # finish; assignment changes wait for separately proven quiescence.
                run.update(fence="steering", status="replan_required", updated_at=now())
                write(run_dir / "run.json", run)
                _revision(run_dir, objective, value)
                if input_dependencies is not None:
                    from workflow_inputs import declare
                    declare(run_dir, input_dependencies)
                try:
                    from workflow_statistics import safe_record
                    safe_record(run_dir, "objective-revision")
                except Exception:
                    pass  # Reporting cannot change the accepted owner outcome.
                return result(run_dir, run, objective, "REPLAN_REQUIRED")
        active = root / "active-run.json"
        if active.exists():
            pointer = read(active)
            candidate = root / str(pointer.get("run_id", ""))
            if candidate.parent != root or not RUN_ID.fullmatch(candidate.name):
                raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "unsupported active-run pointer")
            other, _ = validate_run(candidate, owner=owner)
            if other["status"] not in TERMINAL:
                raise WorkflowError("CONTINUE_REQUIRED", "an objective is active; explicitly continue this project")
        run_dir = root / ("run-" + uuid.uuid4().hex)
        automatic = eligible(Path(context["project_root"]))
        if owner == 'agentic-sdlc' and not automatic:
            from workflow_interop import inspect_anchor
            anchor = inspect_anchor(project)
            if anchor.get('status') not in {'managed', 'unmanaged'}:
                raise WorkflowError('OWNERSHIP_INVALID', 'SDLC requires a source or ordinary managed checkout')
        write(project_file, context)
        run = {**context, "schema": RUN_SCHEMA, "run_id": run_dir.name,
               "coordinator_session_sha256": session_hash(session), "status": "accepted",
               "fence": None, "source_branch": branch, "source_head": head,
               "automatic_outer": automatic, "shared_admission": True,
               "created_at": now(), "updated_at": now()}
        objective = {"schema": OBJECTIVE_SCHEMA, "owner": owner, "run_id": run_dir.name,
                     "objective_id": "objective-" + uuid.uuid4().hex, "revisions": []}
        _revision(run_dir, objective, value)
        write(run_dir / "run.json", run)
        from workflow_projects import reserve
        reserve(run_dir, run, home, session_hash(session))
        write(active, {"schema": "workflow/active-run-v1", "run_id": run_dir.name})
        _bind(home, owner, session, run_dir, run, objective)
        if input_dependencies is not None:
            from workflow_inputs import declare
            declare(run_dir, input_dependencies)
        run = ensure(run_dir, run, home)
        _bind(home, owner, session, run_dir, run, objective)
        _acquire_intake_lease(run_dir, run)
        try:
            from workflow_statistics import safe_record
            safe_record(run_dir, "accepted", started=statistics_started)
        except Exception:
            pass  # Reporting cannot change the accepted owner outcome.
        return result(run_dir, run, objective, "ACCEPTED")


def _writers_quiescent(run_dir):
    from workflow_validation import require_quiescent
    require_quiescent(run_dir)
    # A worker result is insufficient: it can be written before the native
    # process terminates. Slot release is recorded only by the coordinator.
    for path in sorted((run_dir / "execution").glob("*/waves/*.json")):
        wave = read(path)
        if wave.get("active_task_ids"):
            raise WorkflowError("WRITERS_ACTIVE", "terminate and admit outstanding workers before changing ownership")


def continue_run(project: Path, home: Path, owner: str, session: str, *, takeover: Path | None = None,
                 input_dependencies: dict | None = None):
    from workflow_automatic import ensure, matches
    root, _context, _branch, _head = project_context(project, home, owner)
    with lock(root):
        if not (root / "active-run.json").exists():
            from workflow_projects import records, TERMINAL as PROJECT_TERMINAL
            for row in records(Path(_context["git_common_dir"])):
                if (row["logical_project_root"] == str(project.absolute())
                        and row["state"] not in PROJECT_TERMINAL and row["owner"] != owner):
                    command = "implement" if row["owner"] == "implement" else "sdlc-start"
                    raise WorkflowError("OWNER_CONFLICT", f"continue this project with ${command} continue {project}")
        pointer = read(root / "active-run.json")
        name = str(pointer.get("run_id", ""))
        if not RUN_ID.fullmatch(name):
            raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "unsupported active run; old history was preserved")
        run_dir = root / name
        run, objective = validate_run(run_dir, owner=owner)
        if not matches(run, project):
            raise WorkflowError("OWNERSHIP_INVALID", "project selection disagrees with active run")
        if run["coordinator_session_sha256"] != session_hash(session):
            from workflow_validation import require_quiescent
            require_quiescent(run_dir)
            if takeover is None:
                _writers_quiescent(run_dir)
            else:
                from workflow_reconcile import validate_takeover
                proof = validate_takeover(run_dir, run, takeover, session)
                run.setdefault('coordinator_history', []).append({
                    'session_sha256': run['coordinator_session_sha256'], 'takeover_sha256': proof})
            run["coordinator_session_sha256"] = session_hash(session)
            write(run_dir / "run.json", run)
        checkpoint = run_dir / "current-state.json"
        if owner == "agentic-sdlc" and run["status"] not in TERMINAL and checkpoint.exists():
            current = read(checkpoint)
            iteration = int(current.get("iteration_count", current.get("iteration", 0)))
            limit = int(current.get("checkpoint_interval_iterations", 200))
            if limit > 0 and iteration - int(run.get("observation_checkpoint_start", 0)) >= limit:
                run["observation_checkpoint_start"] = iteration
                run["observation_checkpoints"] = int(run.get("observation_checkpoints", 1)) + 1
                write(run_dir / "run.json", run)
        if input_dependencies is not None:
            from workflow_inputs import declare
            declare(run_dir, input_dependencies)
        run = ensure(run_dir, run, home)
        _bind(home, owner, session, run_dir, run, objective)
        _acquire_intake_lease(run_dir, run)
        if owner == "agentic-sdlc" and run["status"] not in TERMINAL:
            from workflow_corrections import pause
            pause(run_dir, False, session)
        return result(run_dir, run, objective, "ALREADY_COMPLETE" if run["status"] in TERMINAL else "CONTINUE")


def fence(run_dir: Path, session: str, reason: str):
    if reason not in {"steering", "cancelled"}:
        raise WorkflowError("STATE_INVALID", "invalid fence reason")
    with lock(run_dir.parent):
        run, objective = validate_run(run_dir)
        if run["coordinator_session_sha256"] != session_hash(session):
            raise WorkflowError("OWNERSHIP_INVALID", "only the bound coordinator can fence execution")
        if run["status"] in TERMINAL:
            return result(run_dir, run, objective, "ALREADY_COMPLETE")
        run.update(fence=reason, status="cancelling" if reason == "cancelled" else "replan_required")
        write(run_dir / "run.json", run)
        return result(run_dir, run, objective, "FENCED")


def check_dispatch(run_dir: Path, *, allow_complete: bool = False):
    run, objective = validate_run(run_dir)
    if run.get("shared_admission") and run["status"] not in TERMINAL:
        from workflow_projects import get
        entry = get(run_dir)
        if entry is None or entry["state"] != "allocated":
            raise WorkflowError("CAPACITY_WAIT", "objective is waiting or aggregation has fenced child writers")
    if run.get("fence") is not None or run["status"] in TERMINAL and not allow_complete:
        raise WorkflowError("EXECUTION_FENCED", "dispatch and promotion are fenced pending reconciliation")
    return run, objective


def result(run_dir, run, objective, status):
    latest = objective["revisions"][-1]
    value = read(run_dir / latest["snapshot"])
    if run.get("shared_admission"):
        from workflow_projects import get
        entry = get(run_dir)
        if entry is not None and entry["state"] == "requested":
            status = "CAPACITY_WAIT"
    return {"status": status, "owner": run["owner"], "project_root": run.get("logical_project_root", run["project_root"]) if run["status"] in TERMINAL else run["project_root"],
            "logical_project_root": run.get("logical_project_root", run["project_root"]),
            "project_id": run["project_id"], "run_dir": str(run_dir), "run_id": run_dir.name,
            "objective_id": objective["objective_id"], "revision": latest["revision"],
            "objective_sha256": latest["sha256"], "intent_sha256": latest["intent_sha256"],
            "outcome": value["outcome"], "fence": run["fence"],
            "uat_policy": value["uat"], "uat": __import__("workflow_uat").outcome(run_dir)}


def authorize_coordinator(run_dir: Path, session: str, *, owner: str | None = None):
    run, objective = validate_run(run_dir, owner=owner)
    if run["coordinator_session_sha256"] != session_hash(session):
        raise WorkflowError("OWNERSHIP_INVALID", "only the bound native coordinator may change execution")
    return run, objective


def complete_run(run_dir: Path, session: str):
    with lock(run_dir.parent):
        run, objective = authorize_coordinator(run_dir, session)
        _writers_quiescent(run_dir)
        records = [read(path) for path in (run_dir / "execution").glob("*/coordinator.json")]
        if run.get("fence") or not records or any(row.get("status") != "done" for row in records):
            raise WorkflowError("COMPLETION_BLOCKED", "every accepted execution must be delivered before completion")
        from workflow_program import require_complete
        require_complete(run_dir)
        from workflow_interop import load, _reconcile
        outer = load(run_dir)
        if run.get("automatic_outer"):
            from workflow_automatic import verify_delivery
            verify_delivery(run_dir)
        elif outer is not None and outer.get("mode") == "managed":
            observed = _reconcile(run_dir, Path(run["project_root"]), outer)
            if observed.get("state") != "released":
                raise WorkflowError("COMPLETION_BLOCKED", "release the managed outer lease after final evidence before completion")
            if run["owner"] == "agentic-sdlc":
                from workflow_interop import require_completed_source_integration, ExecutionInteropError
                try:
                    require_completed_source_integration(run_dir, Path(run["project_root"]))
                except ExecutionInteropError as error:
                    raise WorkflowError("COMPLETION_BLOCKED", str(error)) from error
        from workflow_uat import handoff, require_delivery
        from workflow_task_acceptance import repository
        repo = repository(run_dir)
        candidate = (read(run_dir / "aggregation/delivery.json")["head"] if run.get("automatic_outer")
                     else records[0]["promoted_head"] if len(records) == 1 and records[0].get("delivered_checkout")
                     else _git(Path(run["project_root"]), "rev-parse", "HEAD"))
        if not any(record.get("promoted_head") == candidate for record in records) and not run.get("automatic_outer"):
            raise WorkflowError("COMPLETION_BLOCKED", "final candidate differs from delivered execution evidence")
        require_delivery(run_dir, candidate)
        handoff(run_dir, candidate, repository=repo)
        from workflow_issue_resolution import write_completion
        write_completion(run_dir, records)
        if run.get("shared_admission") and not run.get("automatic_outer"):
            from workflow_projects import get, update, release
            entry = get(run_dir)
            if entry["state"] == "allocated":
                update(run_dir, expected="allocated", state="aggregation-ready")
                entry = get(run_dir)
            if entry["state"] == "aggregation-ready":
                update(run_dir, expected="aggregation-ready", state="delivered")
                entry = get(run_dir)
            if entry["state"] == "delivered":
                update(run_dir, expected="delivered", state="cleaned")
            release(run_dir)
        run.update(status="done", updated_at=now())
        write(run_dir / "run.json", run)
        try:
            from workflow_statistics import safe_record
            safe_record(run_dir, "complete")
        except Exception:
            pass  # Reporting cannot change the accepted owner outcome.
        home = run_dir.parent.parent.parent
        _bind(home, run["owner"], session, run_dir, run, objective)
        return result(run_dir, run, objective, "COMPLETE")
