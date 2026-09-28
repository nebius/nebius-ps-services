"""Read-only binding of the live harness to the current SDLC checkout."""

from __future__ import annotations

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


def _private_json(path: Path, boundary: Path) -> dict:
    _safe_path(path, boundary)
    metadata = path.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o077
        or metadata.st_size > 1024 * 1024
    ):
        raise TargetError("Workflow identity must be a bounded private regular file.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TargetError("Workflow identity is not an object.")
    return value


def _run(arguments: list[str], env: dict[str, str]) -> str:
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
) -> dict[str, str]:
    """Resolve the exact active integration or completed promotion, never a caller path."""
    try:
        return _resolve_target(
            state, skills_root=skills_root, agent=agent, require_clean=require_clean,
            worker_task=worker_task, assignment_digest=assignment_digest,
        )
    except (
        OSError,
        ValueError,
        KeyError,
        subprocess.TimeoutExpired,
        owned_git_origin.OriginError,
    ) as error:
        raise TargetError(
            "Owned workflow execution target is unavailable or invalid."
        ) from error


def _registration(state: dict, agent: str):
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
    env = dict(os.environ)
    env["CODEX_HOME" if agent == "codex" else "CLAUDE_CONFIG_DIR"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    runs = home / "sdlc-runs"
    _safe_path(runs, boundary)
    entries = list(runs.iterdir())
    if len(entries) > 100:
        raise TargetError("Workflow workspace inventory exceeds its bound.")
    matches = []
    for entry in entries:
        _safe_path(entry, boundary)
        manifest = entry / "workspace.json"
        if not manifest.exists():
            continue
        workspace = _private_json(manifest, boundary)
        if workspace.get("project_root") == str(project):
            matches.append((manifest, workspace))
    if len(matches) != 1:
        raise TargetError("Exactly one owned prompt workspace is required.")
    manifest, workspace = matches[0]
    active = _private_json(manifest.parent / "active-run.json", boundary)
    run_id = active.get("run_id")
    if (
        not isinstance(run_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id) is None
    ):
        raise TargetError("Active workflow run is invalid.")
    run_dir = manifest.parent / run_id
    current = _private_json(run_dir / "current-state.json", boundary)
    lock = _private_json(manifest.parent / "active.lock", boundary)
    identity = {
        "project_id": workspace.get("project_id"),
        "project_root": str(project),
        "run_id": run_id,
    }
    if (
        not isinstance(identity["project_id"], str)
        or not identity["project_id"]
        or any(lock.get(key) != value for key, value in identity.items())
        or re.fullmatch(r"[0-9a-f]{64}", str(lock.get("owner_session_hash", ""))) is None
        or not isinstance(lock.get("created_at"), str)
        or not lock["created_at"]
        or not isinstance(lock.get("status"), str)
        or not lock["status"]
    ):
        raise TargetError("Hook ownership record does not bind the active workflow.")
    if current.get("run_id") != run_id or current.get("project_id") != identity["project_id"]:
        raise TargetError("Workflow checkpoint does not bind the active run.")
    return manifest, workspace, active, lock, run_dir, current, env


def observe_startup(state: dict, *, skills_root: Path, agent: str) -> dict:
    """Observe registration before the first phase; never repair product state."""
    try:
        manifest, workspace, active, lock, run_dir, current, env = _registration(state, agent)
        if (
            state["git"].get("promoted_sha") is not None
            or current.get("current_phase") != "sdlc-start"
            or current.get("status") != "running"
            or any((run_dir / name).exists() for name in ("execution", "plans"))
        ):
            raise TargetError("Workflow startup observation is too late; start a fresh trial.")
        project = Path(state["project_root"])
        if _run(["git", "-C", str(project), "rev-parse", "HEAD"], env) != state["git"]["baseline_sha"]:
            raise TargetError("Workflow startup requires the unchanged fixture baseline.")
        script = (
            "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "from sdlc_state import load_active_run; "
            "r=load_active_run(Path(sys.argv[2]),Path(sys.argv[3])); "
            "print(json.dumps(None if r is None else "
            "{'project_id':r.project_id,'run_id':r.run_id,'run_dir':str(r.run_dir)}))"
        )
        home = Path(state["private_root"]) / f"{agent}-home"
        observed = json.loads(_run([
            sys.executable, "-c", script,
            str(skills_root / "sdlc-start/assets/hooks/lib"), str(project), str(home),
        ], env))
        expected = {"project_id": workspace["project_id"], "run_id": run_dir.name, "run_dir": str(run_dir)}
        if observed != expected:
            raise TargetError("Hook discovery did not resolve the exact startup run.")
        lock_path = manifest.parent / "active.lock"
        boundary = Path(state["run_root"])
        if (
            _private_json(lock_path, boundary) != lock
            or _private_json(manifest.parent / "active-run.json", boundary) != active
            or _private_json(run_dir / "current-state.json", boundary) != current
        ):
            raise TargetError("Workflow startup registration changed during inspection.")
        return {
            "schema": "agentic-sdlc/workflow-startup-v1",
            "verification_id": state["verification_id"],
            "baseline_sha": state["git"]["baseline_sha"],
            "project_id": workspace["project_id"],
            "run_id": run_dir.name,
            "agent": agent,
            "current_phase": "sdlc-start",
            "hook_discovery": "MATCH",
            "owner_session_hash": lock["owner_session_hash"],
            "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
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
            set(value) != {"schema", "verification_id", "baseline_sha", "project_id", "run_id", "agent", "current_phase", "hook_discovery", "owner_session_hash", "lock_sha256", "observed_at"}
            or value.get("schema") != "agentic-sdlc/workflow-startup-v1"
            or value.get("verification_id") != state["verification_id"]
            or value.get("baseline_sha") != state["git"]["baseline_sha"]
            or value.get("agent") not in {"codex", "claude"}
            or value.get("current_phase") != "sdlc-start"
            or value.get("hook_discovery") != "MATCH"
            or any(not isinstance(value.get(key), str) or not value[key] for key in ("project_id", "run_id", "observed_at"))
            or any(re.fullmatch(r"[0-9a-f]{64}", str(value.get(key, ""))) is None for key in ("owner_session_hash", "lock_sha256"))
        ):
            raise TargetError("Workflow startup receipt identity is invalid.")
        return value
    except (OSError, ValueError, KeyError) as error:
        raise TargetError("Workflow startup receipt is unavailable or invalid.") from error


def _resolve_target(
    state: dict, *, skills_root: Path, agent: str, require_clean: bool,
    worker_task: str | None, assignment_digest: str | None,
) -> dict[str, str]:
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
    feature = current.get("current_feature")
    # Only the root coordinator's active authoring phase permits unsealed files.
    require_clean = require_clean or not (
        current.get("status") == "running"
        and current.get("current_phase")
        in {"sdlc-tdd", "sdlc-update-documents", "align"}
    )
    if (
        current.get("run_id") != run_id
        or current.get("project_id") != workspace.get("project_id")
        or not isinstance(feature, str)
        or re.fullmatch(r"FEAT-[0-9]{3,}", feature) is None
    ):
        raise TargetError(
            "Workflow checkpoint does not bind the active run and feature."
        )
    _run(
        [
            sys.executable,
            str(skills_root / "sdlc-start/scripts/prompt_workspace.py"),
            "verify",
            "--workspace",
            str(manifest),
            "--run-id",
            run_id,
            "--json",
        ],
        env,
    )
    coordinator_path = run_dir / "execution" / feature / "coordinator.json"
    coordinator = _private_json(coordinator_path, boundary)
    status = json.loads(
        _run(
            [
                sys.executable,
                str(skills_root / "sdlc-prepare-execution/scripts/sdlc_execution.py"),
                "status",
                "--run-dir",
                str(run_dir),
                "--feature",
                feature,
            ],
            env,
        )
    )
    if not isinstance(status, dict) or status.get("status") != "ok":
        raise TargetError("Execution inspection returned an invalid envelope.")
    status = status.get("result")
    if not isinstance(status, dict):
        raise TargetError("Execution inspection returned an invalid object.")
    for key in (
        "feature_id",
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
        or coordinator.get("feature_id") != feature
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
        if (
            coordinator["status"]
            not in {"prepared", "tdd_sealed", "waves_running", "integrated", "sealed"}
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
        batches = wave.get("batches", [])
        active_batch = wave.get("active_batch_index")
        if (
            not isinstance(active_batch, int)
            or active_batch < 0
            or active_batch >= len(batches)
            or batches[active_batch] != [worker_task]
        ):
            raise TargetError("Shared runtime requires exactly one active worker.")
        worker_assignment_path = (
            execution_root / "assignments" / wave_id / f"{worker_task}.json"
        )
        worker_assignment = _private_json(worker_assignment_path, boundary)
        watch = json.loads(_run([
            sys.executable,
            str(skills_root / "sdlc-prepare-execution/scripts/sdlc_execution.py"),
            "task-watch", "--run-dir", str(run_dir), "--feature", feature,
            "--wave", wave_id, "--task", worker_task,
            "--assignment-digest", assignment_digest,
        ], env))
        if (
            watch.get("status") != "ok"
            or watch.get("result", {}).get("status") != "ACTIVE"
            or worker_assignment.get("assignment_digest") != assignment_digest
        ):
            raise TargetError("Worker runtime target failed its live scope guard.")
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
        "feature_id": feature,
        "phase": current["current_phase"],
    }
