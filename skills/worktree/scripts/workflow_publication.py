"""Registered canonical-spec ownership at the paired publication boundary.

The Git-common registry is an explicit lease, never a run-discovery heuristic.
It contains identities only; normalized intent remains in private run state.
"""
from contextlib import contextmanager
import hashlib
import re
from pathlib import Path

from workflow_state import WorkflowError, lock, read, write
from workflow_objectives import validate_run, session_hash
from workflow_spec_checkout import _git, resolve_spec_checkout

SCHEMA = "workflow/spec-publication-owner-v1"


def registry_path(project: Path) -> Path:
    repo = Path(_git(project, "rev-parse", "--show-toplevel"))
    common = Path(_git(project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    scope = project.relative_to(repo).as_posix()
    key = hashlib.sha256(scope.encode()).hexdigest()
    return common / "workflow-spec-publication" / (key + ".json")


def register(run_dir: Path, feature: str, selected: Path) -> None:
    run, _objective = validate_run(run_dir)
    path = registry_path(selected)
    value = {"schema": SCHEMA, "run_dir": str(run_dir), "feature_id": feature,
             "selected_project": str(selected), "owner": run["owner"]}
    with lock(path.parent):
        if path.exists():
            prior = read(path)
            if prior == value:
                return
            if prior.get("schema") != SCHEMA:
                raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "unsupported specification owner registry")
            old_run, _ = validate_run(Path(prior["run_dir"]))
            old = read(Path(prior["run_dir"]) / "execution" / prior["feature_id"] / "coordinator.json")
            if old.get("status") != "done" and old_run.get("status") != "cancelled":
                raise WorkflowError("SPEC_CONFLICT", "another run owns canonical publication")
        write(path, value)


def _basis(run_dir: Path, feature: str):
    from workflow_impact import _basis_path
    from project_specs_lib.contracts import canonical_digest
    pointer = _basis_path(run_dir, feature)
    if not pointer.exists():
        return None
    record = read(pointer)
    fingerprint = record.get("sha256")
    if (record.get("schema") != "workflow/execution-basis-pointer-v1"
            or record.get("path") != f"bases/{feature}/{fingerprint}.json"
            or not isinstance(fingerprint, str) or len(fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in fingerprint)
            or canonical_digest(read(run_dir / record["path"])) != fingerprint):
        raise WorkflowError("BASIS_INVALID", "publication basis identity is invalid")
    return fingerprint


@contextmanager
def publication_guard(project: Path, *, objective_sha256, basis_sha256):
    path = registry_path(project)
    if not path.exists() and not path.is_symlink():
        yield lambda: None
        return
    owner = read(path)
    if owner.get("schema") != SCHEMA:
        raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "unsupported specification owner registry")
    run_dir = Path(owner["run_dir"])
    selected = Path(owner["selected_project"])
    if registry_path(selected) != path:
        raise WorkflowError("OWNERSHIP_INVALID", "specification owner belongs to another project")
    feature = owner["feature_id"]
    if (run_dir != run_dir.resolve() or selected != selected.resolve()
            or re.fullmatch(r"FEAT-[0-9]{3,}", str(feature)) is None):
        raise WorkflowError("OWNERSHIP_INVALID", "publication registry identity is invalid")
    with lock(run_dir.parent):
        def check():
            if read(path) != owner:
                raise WorkflowError("SPEC_CONFLICT", "specification owner changed during publication")
            run, objective = validate_run(run_dir, owner=owner["owner"])
            coordinator = read(run_dir / "execution" / feature / "coordinator.json")
            if (run.get("project_root") != str(selected)
                    or run.get("git_common_dir") != str(path.parent.parent)
                    or coordinator.get("schema") != "workflow/execution-coordinator-v1"
                    or coordinator.get("run_id") != run_dir.name
                    or coordinator.get("feature_id") != feature
                    or coordinator.get("selected_project_root") != str(selected)):
                raise WorkflowError("OWNERSHIP_INVALID", "publication registry and execution identity disagree")
            from agent_runtime import native_session_id
            native = native_session_id()
            if native is not None:
                native_digest = session_hash(native)
                for task_path in sorted((run_dir / "execution").glob("*/tasks/*/*.json")):
                    task = read(task_path)
                    if (native_digest == task.get("worker_session_hash")
                            or native_digest in task.get("worker_session_hash_history", [])):
                        raise WorkflowError("OWNERSHIP_INVALID", "workers report gaps; canonical publication belongs to the root")
            if coordinator.get("status") == "done":
                delivered = (Path(coordinator["integration_worktree"]) / coordinator["project_scope"]
                             if coordinator.get("delivery_mode") == "feature" else selected)
                if project != delivered:
                    raise WorkflowError("SPEC_CONFLICT", "canonical specifications belong to the delivered checkout")
                return
            if run.get("status") == "cancelled":
                if project != selected:
                    raise WorkflowError("SPEC_CONFLICT", "cancelled run does not own canonical publication")
                return
            if native is None or session_hash(native) != run.get("coordinator_session_sha256"):
                raise WorkflowError("OWNERSHIP_INVALID", "only the bound coordinator publishes canonical specs; workers report gaps")
            if objective_sha256 != objective["revisions"][-1]["sha256"]:
                raise WorkflowError("SPEC_CONFLICT", "publication requires the current accepted objective digest")
            if basis_sha256 != _basis(run_dir, feature):
                raise WorkflowError("SPEC_CONFLICT", "publication execution basis is stale")
            if coordinator.get("bootstrap_objective_sha256") is not None and (
                coordinator.get("plan_digest") is None or coordinator.get("status") == "specs_sealing"
            ) and (
                coordinator.get("status") != "specs_prepared"
                or coordinator["bootstrap_objective_sha256"] != objective["revisions"][-1]["sha256"]
            ):
                raise WorkflowError("SPEC_CONFLICT", "specification bootstrap is not ready for publication")
            if project != resolve_spec_checkout(run_dir, selected, feature_id=feature):
                raise WorkflowError("SPEC_CONFLICT", "canonical specs belong to the registered integration checkout")
            from workflow_objectives import _writers_quiescent
            _writers_quiescent(run_dir)
        check()
        yield check
