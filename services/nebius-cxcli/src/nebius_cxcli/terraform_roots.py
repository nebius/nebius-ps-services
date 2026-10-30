"""Explicit generated Terraform roots; the project lease always uses the main backend."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from .paths import ProjectPaths
from .terraform_backend import TerraformBackendSettings, backend_settings_from_config
from .wireguard_dependencies import access_owner_rows


def access_backend(settings: TerraformBackendSettings) -> TerraformBackendSettings:
    return replace(settings, key=settings.key + ".wireguard-access")


def root_manifest(
    config: Any, *, settings: TerraformBackendSettings | None = None
) -> list[dict[str, Any]]:
    main = settings or backend_settings_from_config(config)
    roots = [{"name": "infra", "backend": asdict(main)}]
    if access_owner_rows(config):
        roots.insert(0, {"name": "infra-access", "backend": asdict(access_backend(main))})
    return roots


def owned_roots(paths: ProjectPaths, config: Any) -> tuple[Any, ...]:
    return (
        (paths.access_infra_dir, paths.infra_dir)
        if access_owner_rows(config)
        else (paths.infra_dir,)
    )


def merged_outputs(cli: Any, paths: ProjectPaths, config: Any, **kwargs: Any) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for root in owned_roots(paths, config):
        values = cli.terraform_output_json(root, **kwargs)
        if set(values) & set(outputs):
            raise RuntimeError("Terraform roots returned duplicate output identities")
        outputs.update(values)
    return outputs
