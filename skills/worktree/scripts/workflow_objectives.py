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

from workflow_state import WorkflowError, digest, directory, lock, read, stable, write
from workflow_security import contains_sensitive


OBJECTIVE_SCHEMA = "workflow/accepted-objective-v1"
RUN_SCHEMA = "workflow/run-v1"
BINDING_SCHEMA = "workflow/session-binding-v1"
ROOTS = {"implement": "implement", "agentic-sdlc": "sdlc-runs"}
TERMINAL = {"done", "cancelled"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"run-[a-z0-9][a-z0-9-]{0,79}\Z")
FIELDS = {"outcome", "scope", "constraints", "acceptance_criteria", "clarifications", "provenance"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def session_hash(session: str) -> str:
    if not isinstance(session, str) or not session.strip():
        raise WorkflowError("NATIVE_IDENTITY_REQUIRED", "a native coordinator session is required")
    return hashlib.sha256(session.encode()).hexdigest()


def normalize(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise WorkflowError("OBJECTIVE_INVALID", "supply only normalized outcome, scope, constraints, acceptance criteria, clarifications and provenance")
    result = {}
    for key in FIELDS - {"provenance"}:
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
    if len(stable(result)) > 128 * 1024:
        raise WorkflowError("OBJECTIVE_INVALID", "normalized objective is too large")
    return result


def _git(project: Path, *args):
    result = subprocess.run(["git", "-C", str(project), *args], text=True, capture_output=True, timeout=30)
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
    branch = _git(project, "symbolic-ref", "--short", "HEAD")
    head = _git(project, "rev-parse", "--verify", "HEAD^{commit}")
    home = home.expanduser().absolute()
    if home.is_relative_to(repo) or repo.is_relative_to(home):
        raise WorkflowError("UNSAFE_STATE", "agent state must be outside the project Git tree")
    identity = hashlib.sha256((str(common) + "\0" + str(project)).encode()).hexdigest()[:24]
    root = home / ROOTS[owner] / identity
    context = {"schema": "workflow/project-v1", "owner": owner, "project_id": identity,
               "project_root": str(project), "git_root": str(repo), "git_common_dir": str(common)}
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
        if digest(snapshot) != revision.get("sha256") or normalize(snapshot) != snapshot:
            raise WorkflowError("OBJECTIVE_INVALID", "accepted objective bytes changed")
        if digest({key: snapshot[key] for key in FIELDS - {"provenance"}}) != revision.get("intent_sha256"):
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
    if (value.get("project_root") != run.get("project_root")
            or project is not None and str(project.absolute()) != run["project_root"]
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
    revisions = objective["revisions"]
    name = f"r{len(revisions) + 1:04d}"
    path = run_dir / "objectives" / (name + ".json")
    if path.exists():
        if read(path) != value:
            raise WorkflowError("RECOVERY_REQUIRED", "conflicting interrupted objective acceptance is retained")
    else:
        write(path, value, exclusive=True)
    revisions.append({"revision": name, "sha256": digest(value),
                      "intent_sha256": digest({key: value[key] for key in FIELDS - {"provenance"}}),
                      "snapshot": f"objectives/{name}.json", "accepted_at": now(),
                      "parent_sha256": revisions[-1]["sha256"] if revisions else None})
    write(run_dir / "objective.json", objective)


def accept(project: Path, home: Path, owner: str, session: str, value: dict, *, revise: bool = False):
    value = normalize(value)
    root, context, branch, head = project_context(project, home, owner)
    with lock(root):
        if (root / "workspace.json").exists():
            raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "managed prompt state is unsupported and remains untouched")
        project_file = root / "project.json"
        if project_file.exists() and read(project_file) != context:
            raise WorkflowError("OWNERSHIP_INVALID", "project execution identity changed")
        bound = observe_binding(home, owner, session, project)
        if bound:
            run_dir, run, objective = bound
            read(run_dir / objective["revisions"][-1]["snapshot"])
            intent = {key: value[key] for key in FIELDS - {"provenance"}}
            if digest(intent) == objective["revisions"][-1]["intent_sha256"]:
                return result(run_dir, run, objective, "ALREADY_COMPLETE" if run["status"] in TERMINAL else "CONTINUE")
            if run["status"] not in TERMINAL:
                if not revise:
                    raise WorkflowError("OBJECTIVE_ACTIVE", "accept material steering explicitly; unrelated objectives are not queued")
                # The fence is durable before acceptance. Existing writers may
                # finish; assignment changes wait for separately proven quiescence.
                run.update(fence="steering", status="replan_required", updated_at=now())
                write(run_dir / "run.json", run)
                _revision(run_dir, objective, value)
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
        promotion = None
        if owner == 'agentic-sdlc':
            from workflow_interop import inspect_anchor
            from git_promotion import ensure_promotion_branch, GitPromotionError
            anchor = inspect_anchor(project)
            if anchor.get('status') == 'unmanaged':
                try:
                    promotion = ensure_promotion_branch(project, lifecycle_id=run_dir.name, task_slug='sdlc')
                except GitPromotionError as error:
                    raise WorkflowError('POLICY_BLOCK', str(error)) from error
                branch, head = promotion['promotion_branch'], promotion['promotion_initial_head']
            elif anchor.get('status') != 'managed':
                raise WorkflowError('OWNERSHIP_INVALID', 'SDLC requires a source or ordinary managed checkout')
        write(project_file, context)
        if promotion is not None:
            write(run_dir / 'git-promotion.json', promotion)
        run = {**context, "schema": RUN_SCHEMA, "run_id": run_dir.name,
               "coordinator_session_sha256": session_hash(session), "status": "accepted",
               "fence": None, "source_branch": branch, "source_head": head,
               "created_at": now(), "updated_at": now()}
        objective = {"schema": OBJECTIVE_SCHEMA, "owner": owner, "run_id": run_dir.name,
                     "objective_id": "objective-" + uuid.uuid4().hex, "revisions": []}
        _revision(run_dir, objective, value)
        write(run_dir / "run.json", run)
        write(active, {"schema": "workflow/active-run-v1", "run_id": run_dir.name})
        _bind(home, owner, session, run_dir, run, objective)
        return result(run_dir, run, objective, "ACCEPTED")


def _writers_quiescent(run_dir):
    # A worker result is insufficient: it can be written before the native
    # process terminates. Slot release is recorded only by the coordinator.
    for path in sorted((run_dir / "execution").glob("*/waves/*.json")):
        wave = read(path)
        if wave.get("active_task_ids"):
            raise WorkflowError("WRITERS_ACTIVE", "terminate and admit outstanding workers before changing ownership")


def continue_run(project: Path, home: Path, owner: str, session: str, *, takeover: Path | None = None):
    root, _context, _branch, _head = project_context(project, home, owner)
    with lock(root):
        pointer = read(root / "active-run.json")
        name = str(pointer.get("run_id", ""))
        if not RUN_ID.fullmatch(name):
            raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "unsupported active run; old history was preserved")
        run_dir = root / name
        run, objective = validate_run(run_dir, owner=owner)
        if run["project_root"] != str(project.absolute()):
            raise WorkflowError("OWNERSHIP_INVALID", "project selection disagrees with active run")
        if run["coordinator_session_sha256"] != session_hash(session):
            if takeover is None:
                _writers_quiescent(run_dir)
            else:
                from workflow_reconcile import validate_takeover
                proof = validate_takeover(run_dir, run, takeover, session)
                run.setdefault('coordinator_history', []).append({
                    'session_sha256': run['coordinator_session_sha256'], 'takeover_sha256': proof})
            run["coordinator_session_sha256"] = session_hash(session)
            write(run_dir / "run.json", run)
        _bind(home, owner, session, run_dir, run, objective)
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
    if run.get("fence") is not None or run["status"] in TERMINAL and not allow_complete:
        raise WorkflowError("EXECUTION_FENCED", "dispatch and promotion are fenced pending reconciliation")
    return run, objective


def result(run_dir, run, objective, status):
    latest = objective["revisions"][-1]
    value = read(run_dir / latest["snapshot"])
    return {"status": status, "owner": run["owner"], "project_root": run["project_root"],
            "project_id": run["project_id"], "run_dir": str(run_dir), "run_id": run_dir.name,
            "objective_id": objective["objective_id"], "revision": latest["revision"],
            "objective_sha256": latest["sha256"], "intent_sha256": latest["intent_sha256"],
            "outcome": value["outcome"], "fence": run["fence"]}


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
        from workflow_interop import load, _reconcile
        outer = load(run_dir)
        if outer is not None and outer.get("mode") == "managed":
            observed = _reconcile(run_dir, Path(run["project_root"]), outer)
            if observed.get("state") != "released":
                raise WorkflowError("COMPLETION_BLOCKED", "release the managed outer lease after final evidence before completion")
        run.update(status="done", updated_at=now())
        write(run_dir / "run.json", run)
        home = run_dir.parent.parent.parent
        _bind(home, run["owner"], session, run_dir, run, objective)
        return result(run_dir, run, objective, "COMPLETE")
