"""Generated artifact manifest helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .paths import InstancePaths
from .runtime_config import AttrDict, to_plain_data, wrap_runtime_config

GENERATED_MANIFEST_FILENAME = "nebius-cxcli-manifest.json"
GENERATED_MANIFEST_SCHEMA = "nebius-cxcli-generated/v1"


def _repo_relative_path(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def build_generated_manifest(
    *,
    config: Any,
    paths: InstancePaths,
    handoffs: Sequence[Mapping[str, Any]],
    required_component_outputs: Sequence[Mapping[str, Any]],
    source_profile: str | None = None,
    module_sources: Sequence[Mapping[str, Any]] = (),
    terraform_tfvars: Mapping[str, Any] | None = None,
    flux_version: str | None = None,
    terraform_version: str | None = None,
) -> dict[str, Any]:
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        raise ValueError("Runtime config payload must be a mapping")

    return {
        "schema": GENERATED_MANIFEST_SCHEMA,
        "source_contract": {
            "config_path": _repo_relative_path(paths.config_path, root=paths.repo_root),
        },
        "instance": {
            "client_name": paths.path_client_name,
            "tenant_id": paths.path_tenant_id,
            "project_id": paths.path_project_id,
        },
        "paths": {
            "generated_dir": _repo_relative_path(paths.generated_dir, root=paths.repo_root),
            "infra_dir": _repo_relative_path(paths.infra_dir, root=paths.repo_root),
            "flux_dir": _repo_relative_path(paths.flux_dir, root=paths.repo_root),
            "inventory_dir": _repo_relative_path(paths.inventory_dir, root=paths.repo_root),
        },
        "tools": {
            "flux_version": str(flux_version or "").strip(),
            "terraform_version": str(terraform_version or "").strip(),
        },
        "render": {
            "source_profile": str(source_profile or "").strip(),
            "module_sources": [dict(item) for item in module_sources],
            "terraform_tfvars": dict(terraform_tfvars or {}),
        },
        "deploy": {
            "handoffs": [dict(item) for item in handoffs],
            "required_component_outputs": [dict(item) for item in required_component_outputs],
        },
        "runtime_config": dict(payload),
    }


def manifest_path_for_generated_dir(generated_dir: Path) -> Path:
    return generated_dir / GENERATED_MANIFEST_FILENAME


def write_generated_manifest_to_path(
    path: Path,
    *,
    config: Any,
    paths: InstancePaths,
    handoffs: Sequence[Mapping[str, Any]],
    required_component_outputs: Sequence[Mapping[str, Any]],
    source_profile: str | None = None,
    module_sources: Sequence[Mapping[str, Any]] = (),
    terraform_tfvars: Mapping[str, Any] | None = None,
    flux_version: str | None = None,
    terraform_version: str | None = None,
) -> Path:
    manifest = build_generated_manifest(
        config=config,
        paths=paths,
        handoffs=handoffs,
        required_component_outputs=required_component_outputs,
        source_profile=source_profile,
        module_sources=module_sources,
        terraform_tfvars=terraform_tfvars,
        flux_version=flux_version,
        terraform_version=terraform_version,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_generated_manifest(
    *,
    config: Any,
    paths: InstancePaths,
    handoffs: Sequence[Mapping[str, Any]],
    required_component_outputs: Sequence[Mapping[str, Any]],
    source_profile: str | None = None,
    module_sources: Sequence[Mapping[str, Any]] = (),
    terraform_tfvars: Mapping[str, Any] | None = None,
    flux_version: str | None = None,
    terraform_version: str | None = None,
) -> Path:
    return write_generated_manifest_to_path(
        manifest_path_for_generated_dir(paths.generated_dir),
        config=config,
        paths=paths,
        handoffs=handoffs,
        required_component_outputs=required_component_outputs,
        source_profile=source_profile,
        module_sources=module_sources,
        terraform_tfvars=terraform_tfvars,
        flux_version=flux_version,
        terraform_version=terraform_version,
    )


def load_generated_manifest(generated_dir: Path) -> dict[str, Any]:
    path = manifest_path_for_generated_dir(generated_dir)
    if not path.exists():
        raise ValueError(
            f"Generated manifest not found: {path}. Rerun `nebius-cxcli render <config.yaml>` first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Generated manifest root must be a mapping: {path}")
    if payload.get("schema") != GENERATED_MANIFEST_SCHEMA:
        raise ValueError(
            f"Unsupported generated manifest schema in {path}: {payload.get('schema')!r}"
        )
    return payload


def runtime_config_from_manifest(manifest: Mapping[str, Any]) -> AttrDict:
    payload = manifest.get("runtime_config")
    if not isinstance(payload, Mapping):
        raise ValueError("Generated manifest is missing runtime_config")
    return wrap_runtime_config(dict(payload))


def terraform_tfvars_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    render = manifest.get("render")
    if not isinstance(render, Mapping):
        raise ValueError("Generated manifest is missing render metadata")
    payload = render.get("terraform_tfvars")
    if not isinstance(payload, Mapping):
        raise ValueError("Generated manifest is missing render.terraform_tfvars")
    return dict(payload)
