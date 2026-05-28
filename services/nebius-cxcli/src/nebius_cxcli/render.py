"""Render orchestration for Terraform and Flux artifacts."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from .component_sources import SourceProfile
from .flux_render import render_flux
from .infra_render import render_terraform_artifacts
from .mk8s_gpu import materialize_mk8s_gpu_app_values, prune_inactive_mk8s_gpu_app_rows
from .mysterybox_eso import materialize_mysterybox_eso_app_values
from .observability import (
    materialize_observability_app_values,
    materialize_observability_infra_values,
)
from .paths import ProjectPaths
from .soperator_child_charts import materialize_soperator_child_chart_values
from .soperator_onboarding import write_soperator_onboarding_reports


@dataclass(frozen=True)
class RenderResult:
    files_written: list[Path]


def reset_generated_bundle(paths: ProjectPaths) -> None:
    """Remove the previously generated bundle completely."""
    if paths.generated_dir.exists():
        shutil.rmtree(paths.generated_dir)


def staged_generated_paths(paths: ProjectPaths) -> ProjectPaths:
    """Create a sibling staging directory for a transactional generated/ replacement."""
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=".generated-staging-",
            dir=paths.project_dir,
        )
    ).resolve()
    return replace(
        paths,
        generated_dir=staging_dir,
        infra_dir=staging_dir / "infra",
        flux_dir=staging_dir / "flux",
        inventory_dir=staging_dir / "inventory",
    )


def promote_staged_generated_paths(
    staged_paths: ProjectPaths,
    final_paths: ProjectPaths,
) -> None:
    """Swap a fully rendered staged bundle into the canonical generated/ path."""
    backup_dir: Path | None = None
    try:
        if final_paths.generated_dir.exists():
            backup_dir = final_paths.project_dir / f".generated-backup-{uuid4().hex}"
            final_paths.generated_dir.rename(backup_dir)
        staged_paths.generated_dir.rename(final_paths.generated_dir)
    except Exception:
        if (
            backup_dir is not None
            and backup_dir.exists()
            and not final_paths.generated_dir.exists()
        ):
            backup_dir.rename(final_paths.generated_dir)
        raise
    finally:
        if staged_paths.generated_dir.exists():
            shutil.rmtree(staged_paths.generated_dir, ignore_errors=True)
    if backup_dir is not None and backup_dir.exists():
        shutil.rmtree(backup_dir)


def _render_project_to_paths(
    config: Any,
    paths: ProjectPaths,
    *,
    component_output_values: dict[str, Any] | None = None,
    source_profile: SourceProfile = SourceProfile.PORTABLE,
) -> RenderResult:
    """Render Terraform and Flux artifacts into the provided target paths."""
    if isinstance(config, dict):
        from .cli import (
            _materialize_soperator_component_defaults,
            _materialize_soperator_render_only_values,
        )

        _materialize_soperator_component_defaults(config)
        _materialize_soperator_render_only_values(config)
    prune_inactive_mk8s_gpu_app_rows(config)
    materialize_mk8s_gpu_app_values(config)
    materialize_soperator_child_chart_values(config)
    materialize_observability_infra_values(config)
    materialize_observability_app_values(config)
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values=component_output_values,
    )
    paths.infra_dir.mkdir(parents=True, exist_ok=True)
    paths.flux_dir.mkdir(parents=True, exist_ok=True)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    written.extend(render_terraform_artifacts(config, paths, source_profile=source_profile))
    written.extend(render_flux(config, paths, component_output_values=component_output_values))
    written.extend(write_soperator_onboarding_reports(config, paths.generated_dir))
    return RenderResult(files_written=sorted(written))


def render_project(
    config: Any,
    paths: ProjectPaths,
    *,
    component_output_values: dict[str, Any] | None = None,
    source_profile: SourceProfile = SourceProfile.PORTABLE,
) -> RenderResult:
    """Render Terraform and Flux artifacts transactionally for one validated config."""
    staged_paths = staged_generated_paths(paths)
    try:
        staged_result = _render_project_to_paths(
            config,
            staged_paths,
            component_output_values=component_output_values,
            source_profile=source_profile,
        )
        promote_staged_generated_paths(staged_paths, paths)
    except Exception:
        reset_generated_bundle(staged_paths)
        raise
    return RenderResult(
        files_written=sorted(
            paths.generated_dir / item.relative_to(staged_paths.generated_dir)
            for item in staged_result.files_written
        )
    )
