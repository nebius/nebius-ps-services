"""Generated artifact manifest helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .deploy_targets import normalize_generated_deploy_target
from .frozen_catalog import freeze_catalog
from .paths import ProjectPaths
from .project_bundle_transaction import recover_project_bundle
from .runtime_config import AttrDict, to_plain_data, wrap_runtime_config
from .terraform_backend import backend_settings_from_config

GENERATED_MANIFEST_FILENAME = "nebius-cxcli-manifest.json"
GENERATED_MANIFEST_SCHEMA = "nebius-cxcli-generated/v2"


def _repo_relative_path(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def build_generated_manifest(
    *,
    config: Any,
    paths: ProjectPaths,
    targets: Sequence[Mapping[str, Any]],
    required_component_outputs: Sequence[Mapping[str, Any]],
    status_watchers: Sequence[Mapping[str, Any]] = (),
    validations: Sequence[Mapping[str, Any]] = (),
    quota_report: Mapping[str, Any] | None = None,
    source_profile: str | None = None,
    module_sources: Sequence[Mapping[str, Any]] = (),
    terraform_tfvars: Mapping[str, Any] | None = None,
    flux_version: str | None = None,
    terraform_version: str | None = None,
    compatibility: Mapping[str, Any] | None = None,
    application_files: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        raise ValueError("Runtime config payload must be a mapping")
    client_info = payload.get("client_info")
    client_info_map = client_info if isinstance(client_info, Mapping) else {}
    nebius = client_info_map.get("nebius")
    nebius_map = nebius if isinstance(nebius, Mapping) else {}

    rendered_targets: list[dict[str, Any]] = []
    for index, item in enumerate(targets):
        target_item = normalize_generated_deploy_target(item, index=index)
        flux_dir = str(target_item.get("flux_dir", "") or "").strip()
        if flux_dir:
            target_item["flux_dir"] = _repo_relative_path(Path(flux_dir), root=paths.repo_root)
        rendered_targets.append(target_item)

    return {
        "schema": GENERATED_MANIFEST_SCHEMA,
        "execution": {"backend": asdict(backend_settings_from_config(config))},
        "source_contract": {
            "config_path": _repo_relative_path(paths.config_path, root=paths.repo_root),
        },
        "project": {
            "client_name": str(client_info_map.get("client_name", "") or "").strip(),
            "tenant_id": str(nebius_map.get("tenant_id", "") or "").strip(),
            "project_id": str(nebius_map.get("project_id", "") or "").strip(),
        },
        "paths": {
            "generated_dir": _repo_relative_path(paths.generated_dir, root=paths.repo_root),
            "infra_dir": _repo_relative_path(paths.infra_dir, root=paths.repo_root),
            "flux_dir": _repo_relative_path(paths.flux_dir, root=paths.repo_root),
            "reports_dir": _repo_relative_path(paths.reports_dir, root=paths.repo_root),
        },
        "tools": {
            "flux_version": str(flux_version or "").strip(),
            "terraform_version": str(terraform_version or "").strip(),
        },
        "quota": dict(quota_report or {}),
        "render": {
            "inputs": freeze_catalog(payload),
            "source_config_sha256": source_config_digest(paths.config_path),
            "compatibility": dict(compatibility or {}),
            "application_files": dict(application_files or {}),
            "source_profile": str(source_profile or "").strip(),
            "module_sources": [dict(item) for item in module_sources],
            "terraform_tfvars": dict(terraform_tfvars or {}),
        },
        "deploy": {
            "targets": rendered_targets,
            "required_component_outputs": [dict(item) for item in required_component_outputs],
            "status_watchers": [dict(item) for item in status_watchers],
            "validations": [dict(item) for item in validations],
        },
        "runtime_config": dict(payload),
    }


def manifest_path_for_generated_dir(generated_dir: Path) -> Path:
    return generated_dir / GENERATED_MANIFEST_FILENAME


def write_generated_manifest_to_path(
    path: Path,
    *,
    config: Any,
    paths: ProjectPaths,
    targets: Sequence[Mapping[str, Any]],
    required_component_outputs: Sequence[Mapping[str, Any]],
    status_watchers: Sequence[Mapping[str, Any]] = (),
    validations: Sequence[Mapping[str, Any]] = (),
    quota_report: Mapping[str, Any] | None = None,
    source_profile: str | None = None,
    module_sources: Sequence[Mapping[str, Any]] = (),
    terraform_tfvars: Mapping[str, Any] | None = None,
    flux_version: str | None = None,
    terraform_version: str | None = None,
    compatibility: Mapping[str, Any] | None = None,
    application_files: Mapping[str, Any] | None = None,
) -> Path:
    manifest = build_generated_manifest(
        config=config,
        paths=paths,
        targets=targets,
        required_component_outputs=required_component_outputs,
        status_watchers=status_watchers,
        validations=validations,
        quota_report=quota_report,
        source_profile=source_profile,
        module_sources=module_sources,
        terraform_tfvars=terraform_tfvars,
        flux_version=flux_version,
        terraform_version=terraform_version,
        compatibility=compatibility,
        application_files=application_files,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_generated_manifest(
    *,
    config: Any,
    paths: ProjectPaths,
    targets: Sequence[Mapping[str, Any]],
    required_component_outputs: Sequence[Mapping[str, Any]],
    status_watchers: Sequence[Mapping[str, Any]] = (),
    validations: Sequence[Mapping[str, Any]] = (),
    quota_report: Mapping[str, Any] | None = None,
    source_profile: str | None = None,
    module_sources: Sequence[Mapping[str, Any]] = (),
    terraform_tfvars: Mapping[str, Any] | None = None,
    flux_version: str | None = None,
    terraform_version: str | None = None,
    compatibility: Mapping[str, Any] | None = None,
    application_files: Mapping[str, Any] | None = None,
) -> Path:
    return write_generated_manifest_to_path(
        manifest_path_for_generated_dir(paths.generated_dir),
        config=config,
        paths=paths,
        targets=targets,
        required_component_outputs=required_component_outputs,
        status_watchers=status_watchers,
        validations=validations,
        quota_report=quota_report,
        source_profile=source_profile,
        module_sources=module_sources,
        terraform_tfvars=terraform_tfvars,
        flux_version=flux_version,
        terraform_version=terraform_version,
        compatibility=compatibility,
        application_files=application_files,
    )


def load_generated_manifest(generated_dir: Path) -> dict[str, Any]:
    from .deployment_recovery import is_deployment_preview

    if not is_deployment_preview():
        recover_project_bundle(generated_dir.parent)
    else:
        from .project_bundle_transaction import ProjectBundleTransaction

        ProjectBundleTransaction(generated_dir.parent).snapshot_preimages(
            [manifest_path_for_generated_dir(generated_dir)], read_only=True
        )
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
    if not isinstance(payload.get("execution", {}).get("backend"), dict):
        raise ValueError("Generated manifest is missing its frozen execution backend; rerender")
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


def source_config_digest(path: Path) -> str:
    """Bind authored intent without storing a second copy of user values."""
    import yaml

    from .compatibility_matrix import digest

    return digest(yaml.safe_load(path.read_text())) if path.is_file() else ""
