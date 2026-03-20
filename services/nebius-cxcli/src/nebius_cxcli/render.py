"""Render orchestration for Terraform and Flux artifacts."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .flux_render import render_flux
from .infra_render import render_terraform_artifacts
from .inventory_ops import write_inventory
from .paths import InstancePaths


@dataclass(frozen=True)
class RenderResult:
    files_written: list[Path]


def _preserved_flux_bootstrap_dir(paths: InstancePaths) -> Path:
    return paths.flux_dir / "flux-system"


def reset_generated_bundle(paths: InstancePaths) -> None:
    """Remove the previously generated bundle while preserving bootstrap-owned Flux state."""
    preserved_source = _preserved_flux_bootstrap_dir(paths)
    temporary_root: Path | None = None
    preserved_copy: Path | None = None
    if preserved_source.exists():
        temporary_root = Path(
            tempfile.mkdtemp(prefix=".nebius-cxcli-preserve-", dir=str(paths.project_dir))
        )
        preserved_copy = temporary_root / "flux-system"
        shutil.move(str(preserved_source), str(preserved_copy))
    try:
        if paths.generated_dir.exists():
            shutil.rmtree(paths.generated_dir)
        if preserved_copy and preserved_copy.exists():
            restore_parent = _preserved_flux_bootstrap_dir(paths).parent
            restore_parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(preserved_copy), str(_preserved_flux_bootstrap_dir(paths)))
    finally:
        if temporary_root and temporary_root.exists():
            shutil.rmtree(temporary_root, ignore_errors=True)


def render_instance(
    config: Any,
    paths: InstancePaths,
    *,
    component_output_values: dict[str, Any] | None = None,
) -> RenderResult:
    """Render Terraform and Flux artifacts for one validated config."""
    reset_generated_bundle(paths)
    paths.infra_dir.mkdir(parents=True, exist_ok=True)
    paths.flux_dir.mkdir(parents=True, exist_ok=True)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    written.extend(render_terraform_artifacts(config, paths))
    written.extend(render_flux(config, paths, component_output_values=component_output_values))
    write_inventory(config, paths)
    return RenderResult(files_written=sorted(written))
