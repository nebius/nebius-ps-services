"""CI-oriented helpers for discovering changed deployment projects."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from .github_secrets import build_github_environment_name


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


def _is_git_repository(*, cwd: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _changed_files(*, cwd: Path) -> list[str] | None:
    if not _is_git_repository(cwd=cwd):
        return None

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


def _project_config_relative(config_path: Path, *, repo_root: Path) -> str | None:
    relative = _to_repo_relative(config_path, repo_root=repo_root)
    if _infer_client_project_from_path(relative) is None:
        return None
    return relative


def _scan_scope_configs(*, deployment_path: Path, repo_root: Path) -> set[str]:
    candidates: set[str] = set()

    current = deployment_path
    while True:
        candidate = current / "config.yaml"
        if candidate.is_file():
            relative = _project_config_relative(candidate, repo_root=repo_root)
            if relative is not None:
                candidates.add(relative)
        if current == repo_root or current.parent == current:
            break
        current = current.parent

    for config_file in deployment_path.rglob("config.yaml"):
        relative = _project_config_relative(config_file, repo_root=repo_root)
        if relative is not None:
            candidates.add(relative)
    return candidates


def _generated_relative_for_config(config_relative: str) -> str:
    config_path = Path(_normalize(config_relative))
    return config_path.parent.joinpath("generated").as_posix()


def _infer_client_project_from_path(config_relative: str) -> tuple[str, str] | None:
    parts = [token for token in _normalize(config_relative).split("/") if token]
    if len(parts) < 4 or parts[-1] != "config.yaml":
        return None
    if parts[-4] != "projects":
        return None
    client_tenant = parts[-3]
    project_id = parts[-2]
    if not project_id:
        return None
    client_name, sep, _tenant_id = client_tenant.partition("--")
    if not sep or not client_name:
        return None
    return client_name, project_id


def _environment_name_for_config(*, root: Path, config_relative: str) -> str:
    client_project = _infer_client_project_from_path(config_relative)
    if client_project is not None:
        client_name, project_id = client_project
        return build_github_environment_name(client_name=client_name, project_id=project_id)

    config_file = (root / config_relative).resolve()
    payload = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"config payload is not a mapping: {config_relative}")
    client_info = payload.get("client_info") or {}
    nebius = client_info.get("nebius") if isinstance(client_info, dict) else {}
    client_name = str(client_info.get("client_name") if isinstance(client_info, dict) else "").strip()
    project_id = str(nebius.get("project_id") if isinstance(nebius, dict) else "").strip()
    if not client_name or not project_id:
        raise ValueError(
            f"Cannot infer GitHub environment from {config_relative}: missing client_info.client_name/project_id"
        )
    return build_github_environment_name(client_name=client_name, project_id=project_id)


def _config_relative_from_changed(path: str) -> str | None:
    normalized = _normalize(path)
    parts = [token for token in normalized.split("/") if token]
    if len(parts) < 4:
        return None
    if normalized.endswith("config.yaml") and parts[-4] == "projects":
        return normalized
    if "generated" not in parts:
        return None
    generated_index = parts.index("generated")
    if generated_index < 3:
        return None
    if parts[generated_index - 3] != "projects":
        return None
    prefix = parts[:generated_index]
    return "/".join([*prefix, "config.yaml"])


def discover_configs(
    *,
    deployments_dir: str,
    include_all: bool = False,
    repo_root: Path | None = None,
    ) -> dict[str, list[dict[str, object]]]:
    """Build discover payload for changed or known deployment projects in this run."""
    deployment_path = Path(deployments_dir)
    if repo_root is not None:
        root = repo_root.resolve()
    elif deployment_path.is_absolute():
        root = deployment_path.resolve().parent
    else:
        root = Path.cwd().resolve()

    if not deployment_path.is_absolute():
        deployment_path = root / deployment_path
    deployment_path = deployment_path.resolve()
    scope_configs = _scan_scope_configs(deployment_path=deployment_path, repo_root=root)

    candidates: dict[str, dict[str, object]] = {}

    if include_all:
        for config_relative in scope_configs:
            candidates[config_relative] = {
                "config": config_relative,
                "generated": _generated_relative_for_config(config_relative),
                "config_changed": False,
                "generated_changed": False,
            }
    else:
        changed_files = _changed_files(cwd=root)
        if changed_files is None:
            for config_relative in scope_configs:
                candidates[config_relative] = {
                    "config": config_relative,
                    "generated": _generated_relative_for_config(config_relative),
                    "config_changed": False,
                    "generated_changed": False,
                }
        else:
            for changed in changed_files:
                normalized = _normalize(changed)
                config_relative = _config_relative_from_changed(normalized)
                if not config_relative or config_relative not in scope_configs:
                    continue
                candidate = candidates.setdefault(
                    config_relative,
                    {
                        "config": config_relative,
                        "generated": _generated_relative_for_config(config_relative),
                        "config_changed": False,
                        "generated_changed": False,
                    },
                )
                if normalized.endswith("config.yaml"):
                    candidate["config_changed"] = True
                elif "/generated/" in f"/{normalized}/":
                    candidate["generated_changed"] = True

    include = [
        {
            **candidate,
            "github_environment": _environment_name_for_config(
                root=root,
                config_relative=str(candidate["config"]),
            ),
        }
        for _path, candidate in sorted(candidates.items())
    ]
    return {"include": include}
