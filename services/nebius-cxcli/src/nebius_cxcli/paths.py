"""Path resolution helpers for deployment instances."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InstancePaths:
    config_path: Path
    repo_root: Path
    deployments_dir: Path
    cluster_dir: Path
    generated_dir: Path
    infra_dir: Path
    flux_dir: Path
    inventory_dir: Path
    path_client_name: str
    path_tenant_id: str
    path_env: str
    path_cluster_name: str

    @property
    def instance_slug(self) -> str:
        return f"{self.path_client_name}--{self.path_tenant_id}"


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
    # <deployments-root>/instances/<client>--<tenant>/<env>/<cluster>/config.yaml
    parents = config_path.parents
    if len(parents) >= 5 and parents[3].name == "instances":
        return parents[4].resolve()

    raise ValueError(
        "Unable to infer deployments root from config path. "
        "Expected <deployments-root>/instances/<client>--<tenant>/<env>/<cluster>/config.yaml"
    )


def resolve_instance_paths(
    config_path: Path, deployments_dir_hint: str | None = None
) -> InstancePaths:
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
    if len(parts) != 5 or parts[0] != "instances":
        raise ValueError(
            "Config path must match instances/<client>--<tenant>/<env>/<cluster>/config.yaml"
        )

    client_tenant = parts[1]
    if "--" not in client_tenant:
        raise ValueError("Expected client and tenant in path segment '<client>--<tenant>'")

    client_name, tenant_id = client_tenant.split("--", maxsplit=1)
    cluster_dir = resolved.parent
    generated_dir = cluster_dir / "generated"

    return InstancePaths(
        config_path=resolved,
        repo_root=repo_root,
        deployments_dir=deployments_dir,
        cluster_dir=cluster_dir,
        generated_dir=generated_dir,
        infra_dir=generated_dir / "infra",
        flux_dir=generated_dir / "flux",
        inventory_dir=generated_dir / "inventory",
        path_client_name=client_name,
        path_tenant_id=tenant_id,
        path_env=parts[2],
        path_cluster_name=parts[3],
    )


def validate_path_alignment(config: Any, paths: InstancePaths) -> None:
    """Ensure canonical config values and path hierarchy are synchronized."""
    errors: list[str] = []
    if config.client_info.client_name != paths.path_client_name:
        errors.append(
            "client_name mismatch: "
            f"config='{config.client_info.client_name}', path='{paths.path_client_name}'"
        )
    if config.client_info.nebius.tenant_id != paths.path_tenant_id:
        errors.append(
            "tenant_id mismatch: "
            f"config='{config.client_info.nebius.tenant_id}', path='{paths.path_tenant_id}'"
        )
    config_env = str(getattr(config.client_info.env, "value", config.client_info.env))
    if config_env != paths.path_env:
        errors.append(
            f"env mismatch: config='{config_env}', path='{paths.path_env}'"
        )
    if config.client_info.cluster_name != paths.path_cluster_name:
        errors.append(
            "cluster_name mismatch: "
            f"config='{config.client_info.cluster_name}', path='{paths.path_cluster_name}'"
        )
    if errors:
        raise ValueError("Path alignment failed:\n  - " + "\n  - ".join(errors))
