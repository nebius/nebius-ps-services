"""Resolve canonical spec bytes without changing execution or promotion state."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess


class SpecCheckoutError(RuntimeError):
    """Execution cannot establish the authoritative canonical-spec checkout."""

    def __init__(self, message: str, code: str = "WORKTREE_CONFLICT") -> None:
        super().__init__(message)
        self.code = code


def _fail(message: str, code: str = "WORKTREE_CONFLICT") -> None:
    raise SpecCheckoutError(message, code)


def _read(path: Path) -> dict:
    metadata = path.lstat()
    if (path != path.resolve() or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1 or metadata.st_mode & 0o077
            or metadata.st_uid != os.getuid() or metadata.st_size > 4 * 1024 * 1024):
        _fail("canonical-spec execution record is unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        _fail("canonical-spec execution record is invalid")
    return value


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *arguments], capture_output=True, text=True,
        timeout=30, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}, check=False,
    )
    if result.returncode:
        _fail("canonical-spec Git identity is unavailable")
    return result.stdout.strip()


def resolve_spec_checkout(
    run_dir: Path, selected_project: Path, *, feature_id: str | None = None,
    strict_head: bool = True,
) -> Path:
    """Derive the checkout; transition owners retain their journal recovery checks.

    Only execution callers may relax the recorded HEAD comparison while recovering
    their own interrupted commit. No path, branch or registration check is relaxed.
    """
    try:
        return _resolve(run_dir.resolve(), selected_project, feature_id, strict_head)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        raise SpecCheckoutError("canonical-spec checkout is unavailable or invalid") from error


def _resolve(run_dir: Path, selected: Path, feature_id: str | None, strict: bool) -> Path:
    if selected != selected.resolve() or run_dir != run_dir.resolve():
        _fail("canonical-spec identity contains a symlink")
    execution = run_dir / "execution"
    if not execution.exists():
        return selected
    if execution != execution.resolve() or not execution.is_dir():
        _fail("canonical-spec execution directory is unsafe")
    records = {}
    for entry in execution.iterdir():
        if re.fullmatch(r"FEAT-[0-9]{3,}", entry.name):
            if entry != entry.resolve() or not entry.is_dir():
                _fail("canonical-spec feature directory is unsafe")
            records[entry.name] = _read(entry / "coordinator.json")
    if feature_id is None:
        current = run_dir / "current-state.json"
        if current.exists():
            feature_id = _read(current).get("current_feature")
        active = [key for key, value in records.items() if value.get("status") != "done"]
        if len(active) > 1 or (active and feature_id not in (None, active[0])):
            _fail("canonical-spec active feature is ambiguous")
        feature_id = active[0] if active else feature_id
    if feature_id not in records:
        if any(value.get("status") != "done" for value in records.values()):
            _fail("canonical-spec active feature is missing")
        return selected
    value = records[feature_id]
    if value.get("schema") in {f"agentic-sdlc/execution-coordinator-v{n}" for n in range(1, 7)}:
        _fail("legacy execution coordinator schema is unsupported", "WORKFLOW_UPGRADE_REQUIRED")
    if (value.get("schema") != "agentic-sdlc/execution-coordinator-v7"
            or value.get("state_version") != 7
            or value.get("run_id") != run_dir.name
            or value.get("feature_id") != feature_id
            or value.get("selected_project_root") != str(selected)):
        _fail("canonical-spec coordinator identity is foreign")
    repo = Path(value["project_root"])
    scope = PurePosixPath(value["project_scope"])
    if (repo != repo.resolve() or value.get("git_root") != str(repo)
            or scope.is_absolute() or ".." in scope.parts
            or repo / scope != selected
            or Path(_git(repo, "rev-parse", "--show-toplevel")) != repo):
        _fail("canonical-spec selected project scope is invalid")
    common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if str(common) != value.get("git_common_dir"):
        _fail("canonical-spec common Git directory is foreign")
    integration = run_dir / "worktrees" / feature_id / "integration"
    if value.get("integration_worktree") != str(integration):
        _fail("canonical-spec integration path is foreign")
    status = value.get("status")
    if status in {"promoted", "cleanup", "done"}:
        if (_git(repo, "rev-parse", "HEAD") != value.get("promoted_head")
                or _git(repo, "branch", "--show-current") != value.get("base_branch")
                or _git(repo, "status", "--porcelain", "--untracked-files=all")):
            _fail("canonical-spec promoted checkout drifted")
        if status == "done" and (
                integration.exists() or integration.is_symlink()
                or _git(repo, "branch", "--list", value["integration_branch"])
                or f"worktree {integration}\n" in _git(repo, "worktree", "list", "--porcelain") + "\n"):
            _fail("canonical-spec completed execution retains integration resources")
        return selected
    # Integration ownership never permits edits or unsafe canonical files in
    # the unchanged primary anchor before promotion.
    for name in ("requirements", "design"):
        path = selected / "docs" / f"{name}.md"
        metadata = path.lstat()
        if (path != path.resolve() or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1 or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o022):
            _fail("primary canonical spec is unsafe", "SPEC_CONFLICT")
    promotion_interrupted = (
        status == "sealed"
        and _git(repo, "rev-parse", "HEAD") == value.get("integration_head")
        and _git(repo, "branch", "--show-current") == value.get("base_branch")
        and not _git(repo, "status", "--porcelain", "--untracked-files=all")
    )
    if not promotion_interrupted and _git(repo, "diff", "--name-only", value["base_head"], "--", str(scope / "docs/requirements.md"), str(scope / "docs/design.md")):
        _fail("primary canonical specs drifted before promotion", "REPLAN_REQUIRED")
    if status == "preparing" or (
            status == "blocked" and not integration.exists()
            and value.get("active_wave") is None
            and value.get("integration_head") == value.get("base_head")
            and not (execution / feature_id / "assignments").exists()):
        return selected
    if status not in {"prepared", "tdd_sealed", "waves_running", "integrated", "sealed", "blocked", "repair_required"}:
        _fail("canonical-spec execution lifecycle is invalid")
    if integration != integration.resolve() or not integration.is_dir():
        _fail("canonical-spec integration checkout is unavailable")
    registry = _git(repo, "worktree", "list", "--porcelain").split("\n\n")
    matches = [item.splitlines() for item in registry
               if item.splitlines()[0] == f"worktree {integration}"]
    if (len(matches) != 1
            or f"branch refs/heads/{value['integration_branch']}" not in matches[0]
            or _git(integration, "branch", "--show-current") != value["integration_branch"]
            or Path(_git(integration, "rev-parse", "--path-format=absolute", "--git-common-dir")) != common):
        _fail("canonical-spec integration registration drifted")
    if strict and _git(integration, "rev-parse", "HEAD") != value["integration_head"]:
        _fail("canonical-spec integration HEAD drifted")
    target = integration / scope
    if target != target.resolve() or not target.is_dir():
        _fail("canonical-spec integration scope is unsafe")
    return target
