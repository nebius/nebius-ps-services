"""Path resolution helpers for project-scoped deployment configs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProjectPaths:
    config_path: Path
    repo_root: Path
    deployments_dir: Path
    project_dir: Path
    generated_dir: Path
    infra_dir: Path
    flux_dir: Path
    inventory_dir: Path
    path_tenant_id: str
    path_project_id: str


def find_git_root(start: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return Path(result.stdout.strip()).resolve()
    except Exception:
        for candidate in [start, *start.parents]:
            if (candidate / ".git").exists():
                return candidate.resolve()
        return start.resolve()


def _locate_deployments_dir(config_path: Path, repo_root: Path, hint: str | None = None) -> Path:
    if hint:
        hinted = Path(hint)
        if not hinted.is_absolute():
            hinted = repo_root / hinted
        if hinted.exists():
            return hinted.resolve()
        raise ValueError(f"Deployments dir does not exist: {hinted}")

    # Infer deployments root from canonical config path shape:
    # <deployments-root>/<tenant>/<project>/config.yaml
    parents = config_path.parents
    if len(parents) >= 3:
        return parents[2].resolve()

    raise ValueError(
        "Unable to infer deployments root from config path. "
        "Expected <deployments-root>/<tenant>/<project>/config.yaml"
    )


def resolve_project_paths(
    config_path: Path, deployments_dir_hint: str | None = None
) -> ProjectPaths:
    resolved = config_path.resolve()
    if resolved.name != "config.yaml":
        raise ValueError("Config path must end with config.yaml")

    repo_root = find_git_root(resolved.parent)
    deployments_dir = _locate_deployments_dir(resolved, repo_root, deployments_dir_hint)

    try:
        relative = resolved.relative_to(deployments_dir)
    except ValueError as exc:
        raise ValueError(f"{resolved} is not inside {deployments_dir}") from exc

    parts = relative.parts
    if len(parts) != 3 or parts[-1] != "config.yaml":
        raise ValueError("Config path must match <tenant>/<project>/config.yaml")

    tenant_id = parts[0]
    project_id = parts[1]
    if not tenant_id or not project_id:
        raise ValueError("Config path must include non-empty tenant_id and project_id segments")
    project_dir = resolved.parent
    generated_dir = project_dir / "generated"

    return ProjectPaths(
        config_path=resolved,
        repo_root=repo_root,
        deployments_dir=deployments_dir,
        project_dir=project_dir,
        generated_dir=generated_dir,
        infra_dir=generated_dir / "infra",
        flux_dir=generated_dir / "flux",
        inventory_dir=generated_dir / "inventory",
        path_tenant_id=tenant_id,
        path_project_id=project_id,
    )


def resolve_generated_paths(
    target_path: Path, deployments_dir_hint: str | None = None
) -> ProjectPaths:
    resolved = target_path.resolve()
    generated_dir: Path | None = None

    if resolved.name == "generated":
        generated_dir = resolved
    else:
        for candidate in [resolved.parent, *resolved.parents]:
            if candidate.name == "generated":
                generated_dir = candidate
                break

    if generated_dir is None:
        raise ValueError(
            "Generated artifact path must point to `generated/`, one of its subdirectories, "
            "or a file under that tree."
        )

    config_path = generated_dir.parent / "config.yaml"
    return resolve_project_paths(config_path, deployments_dir_hint=deployments_dir_hint)


def resolve_generated_bundle_config_paths(
    target_path: Path,
    *,
    command_label: str,
    deployments_dir_hint: str | None = None,
) -> ProjectPaths:
    resolved = target_path.resolve()
    if resolved.name == "config.yaml":
        return resolve_project_paths(resolved, deployments_dir_hint=deployments_dir_hint)

    if resolved.name == "generated" or any(candidate.name == "generated" for candidate in resolved.parents):
        raise ValueError(
            f"{command_label} target must be project config.yaml, not generated/. "
            f"Pass <tenant>/<project>/config.yaml; {command_label.lower()} resolves sibling generated/ automatically."
        )

    raise ValueError(
        f"{command_label} target must be project config.yaml "
        "(<deployments-root>/<tenant-id>/<project-id>/config.yaml)."
    )


def resolve_deploy_config_paths(
    target_path: Path, deployments_dir_hint: str | None = None
) -> ProjectPaths:
    return resolve_generated_bundle_config_paths(
        target_path,
        command_label="Deploy",
        deployments_dir_hint=deployments_dir_hint,
    )


def resolve_destroy_config_paths(
    target_path: Path, deployments_dir_hint: str | None = None
) -> ProjectPaths:
    return resolve_generated_bundle_config_paths(
        target_path,
        command_label="Destroy",
        deployments_dir_hint=deployments_dir_hint,
    )


def resolve_report_config_paths(
    target_path: Path, deployments_dir_hint: str | None = None
) -> ProjectPaths:
    return resolve_generated_bundle_config_paths(
        target_path,
        command_label="Report write",
        deployments_dir_hint=deployments_dir_hint,
    )


def resolve_email_config_paths(
    target_path: Path, deployments_dir_hint: str | None = None
) -> ProjectPaths:
    return resolve_generated_bundle_config_paths(
        target_path,
        command_label="Email",
        deployments_dir_hint=deployments_dir_hint,
    )


def validate_path_alignment(config: Any, paths: ProjectPaths) -> None:
    """Ensure canonical config values and path hierarchy are synchronized."""
    errors: list[str] = []
    if config.client_info.nebius.tenant_id != paths.path_tenant_id:
        errors.append(
            "tenant_id mismatch: "
            f"config='{config.client_info.nebius.tenant_id}', path='{paths.path_tenant_id}'"
        )
    config_project_id = str(getattr(config.client_info.nebius, "project_id", None) or "").strip()
    if config_project_id != paths.path_project_id:
        errors.append(
            f"project_id mismatch: config='{config_project_id}', path='{paths.path_project_id}'"
        )
    if errors:
        raise ValueError("Path alignment failed:\n  - " + "\n  - ".join(errors))
