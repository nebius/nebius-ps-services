"""Render orchestration for Terraform and Flux artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .flux_render import render_flux
from .infra_render import render_terraform_artifacts
from .paths import InstancePaths


@dataclass(frozen=True)
class RenderResult:
    files_written: list[Path]


def render_instance(config: Any, paths: InstancePaths) -> RenderResult:
    """Render Terraform and Flux artifacts for one validated config."""
    paths.infra_dir.mkdir(parents=True, exist_ok=True)
    paths.flux_dir.mkdir(parents=True, exist_ok=True)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    written.extend(render_terraform_artifacts(config, paths))
    written.extend(render_flux(config, paths))
    return RenderResult(files_written=sorted(written))
