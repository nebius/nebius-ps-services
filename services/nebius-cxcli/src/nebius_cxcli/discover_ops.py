"""CI-oriented helpers for discovering changed configs."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run_git_diff(range_expr: str, *, cwd: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", range_expr],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _try_git_diff(range_expr: str, *, cwd: Path) -> list[str] | None:
    try:
        return _run_git_diff(range_expr, cwd=cwd)
    except subprocess.CalledProcessError:
        return None


def _head_files(*, cwd: Path) -> list[str]:
    result = subprocess.run(
        ["git", "show", "--pretty=format:", "--name-only", "HEAD"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _changed_files(*, cwd: Path) -> list[str]:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "pull_request":
        base_ref = os.environ.get("GITHUB_BASE_REF")
        if base_ref:
            diff = _try_git_diff(f"origin/{base_ref}...HEAD", cwd=cwd)
            if diff is not None:
                return diff
            diff = _try_git_diff(f"{base_ref}...HEAD", cwd=cwd)
            if diff is not None:
                return diff

    before_sha = os.environ.get("GITHUB_EVENT_BEFORE", "")
    if before_sha and before_sha != "0" * 40:
        diff = _try_git_diff(f"{before_sha}..HEAD", cwd=cwd)
        if diff is not None:
            return diff

    diff = _try_git_diff("HEAD~1..HEAD", cwd=cwd)
    if diff is not None:
        return diff

    # Initial commit or shallow-history edge case: fall back to changed files in HEAD.
    return _head_files(cwd=cwd)


def _normalize(path: str) -> str:
    return Path(path).as_posix().strip("/")


def _to_repo_relative(path: Path, *, repo_root: Path) -> str:
    try:
        return _normalize(str(path.resolve().relative_to(repo_root)))
    except ValueError:
        return _normalize(str(path))


def discover_configs(
    *,
    deployments_dir: str,
    include_all: bool = False,
    repo_root: Path | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Build discover payload for config.yaml files in this run."""
    root = (repo_root or Path.cwd()).resolve()
    deployment_root = _normalize(deployments_dir)
    deployment_path = Path(deployments_dir)
    if not deployment_path.is_absolute():
        deployment_path = root / deployment_path
    candidates: set[str] = set()

    if include_all:
        for config_file in deployment_path.glob("instances/*/*/*/config.yaml"):
            candidates.add(_to_repo_relative(config_file, repo_root=root))
    else:
        for changed in _changed_files(cwd=root):
            normalized = _normalize(changed)
            if not normalized.endswith("config.yaml"):
                continue
            marker = f"/{deployment_root}/"
            if marker in f"/{normalized}/":
                candidates.add(normalized)

    include = [{"config": path} for path in sorted(candidates)]
    return {"include": include}
