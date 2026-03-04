from __future__ import annotations

import json
from pathlib import Path

import yaml

from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.paths import resolve_instance_paths, validate_path_alignment
from nebius_cxcli.render import render_instance


def _instance_config_path(base: Path) -> Path:
    return (
        base
        / "deployments"
        / "instances"
        / "client-a--tenant-123"
        / "project-456"
        / "config.yaml"
    )


def _payload_with_mk8s() -> dict:
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            email=None,
            selected_infra={"mk8s"},
            selected_apps=set(),
            infra_entries=component_entries("infra"),
            app_entries=component_entries("apps"),
        )
    )
    assert isinstance(payload, dict)
    components = payload.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    for row in components:
        if isinstance(row, dict) and str(row.get("id", "")).strip().lower() == "mk8s":
            inputs = row.setdefault("inputs", {})
            assert isinstance(inputs, dict)
            inputs["subnet_id"] = "subnet-abc123"
            inputs["cluster_name"] = "cluster-a"
            return payload
    raise AssertionError("mk8s component missing from starter payload")


def test_render_tfvars_are_backed_by_declared_variables(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _instance_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload_with_mk8s()
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    render_instance(config, paths)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    providers_tf = (paths.infra_dir / "providers.tf").read_text(encoding="utf-8")
    variables_tf = (paths.infra_dir / "variables.tf").read_text(encoding="utf-8")
    tfvars_payload = json.loads(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )
    assert isinstance(tfvars_payload, dict)
    assert tfvars_payload

    for key in tfvars_payload:
        assert f'variable "{key}"' in variables_tf
        assert f"var.{key}" in main_tf or f"var.{key}" in providers_tf


def test_render_uses_component_version_as_git_ref_for_local_module_sources(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    config_path = _instance_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload_with_mk8s()

    components = payload.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    for row in components:
        if isinstance(row, dict) and str(row.get("id", "")).strip().lower() == "mk8s":
            row["version"] = "v0.1.0"
            break

    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    render_instance(config, paths)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    assert (
        'source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=v0.1.0"'
        in main_tf
    )
