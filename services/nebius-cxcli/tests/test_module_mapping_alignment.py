from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import nebius_cxcli.component_sources as component_sources
from nebius_cxcli.component_sources import (
    ComponentOutput,
    SourceProfile,
    reset_component_sources_cache,
    resolve_component_sources_file,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.paths import resolve_project_paths, validate_path_alignment
from nebius_cxcli.render import render_project
from nebius_cxcli.runtime_introspection import ModuleVariable, reset_runtime_introspection_cache


@pytest.fixture(autouse=True)
def _stub_catalog_output_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        component_sources,
        "_discover_terraform_outputs",
        lambda _source: (
            ComponentOutput(
                name="cluster_id",
                kind="terraform_output",
                source_path="cluster_id",
                sensitive=False,
            ),
            ComponentOutput(
                name="cluster_ca_certificate",
                kind="terraform_output",
                source_path="cluster_ca_certificate",
                sensitive=True,
            ),
            ComponentOutput(
                name="instance_id",
                kind="terraform_output",
                source_path="instance_id",
                sensitive=False,
            ),
        ),
    )


def _set_catalog_profile(profile: SourceProfile) -> None:
    set_component_sources_file_override(
        Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    set_component_sources_profile_override(profile)
    reset_component_sources_cache()
    reset_runtime_introspection_cache()
    reset_component_entry_cache()


def _project_config_path(base: Path) -> Path:
    return base / "deployments" / "tenant-name-example" / "project-name-example" / "config.yaml"


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


def _payload_with_mysterybox() -> dict:
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            email=None,
            selected_infra={"mysterybox"},
            selected_apps=set(),
            infra_entries=component_entries("infra"),
            app_entries=component_entries("apps"),
        )
    )
    assert isinstance(payload, dict)
    components = payload.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    for row in components:
        if isinstance(row, dict) and str(row.get("id", "")).strip().lower() == "mysterybox":
            inputs = row.setdefault("inputs", {})
            assert isinstance(inputs, dict)
            inputs["parent_id"] = "project-456"
            inputs["secrets"] = [
                {
                    "name": "app-runtime",
                    "version_id": "n/a",
                    "kubernetes_secret_name": "app-runtime",
                    "payload": {
                        "API_KEY": {
                            "type": "text",
                        },
                    },
                },
            ]
            return payload
    raise AssertionError("mysterybox component missing from starter payload")


def test_render_tfvars_are_backed_by_declared_variables(tmp_path: Path) -> None:
    _set_catalog_profile(SourceProfile.LOCAL)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload_with_mk8s()
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

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


def test_render_mysterybox_payload_values_as_runtime_only_root_variable(
    tmp_path: Path,
) -> None:
    _set_catalog_profile(SourceProfile.LOCAL)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload_with_mysterybox()
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    variables_tf = (paths.infra_dir / "variables.tf").read_text(encoding="utf-8")
    tfvars_payload = json.loads(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert "payload_values = var.mysterybox_payload_values" in main_tf
    assert 'variable "mysterybox_secrets" {' in variables_tf
    assert "type = list(object({" in variables_tf
    assert 'variable "mysterybox_payload_values" {' in variables_tf
    assert "type = map(map(string))" in variables_tf
    assert "default = {}" in variables_tf
    assert "sensitive = true" in variables_tf
    assert "mysterybox_payload_values" not in tfvars_payload
    assert "kubernetes_secret_name" not in json.dumps(tfvars_payload)


def test_render_rejects_mysterybox_payload_values_in_config(tmp_path: Path) -> None:
    _set_catalog_profile(SourceProfile.LOCAL)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload_with_mysterybox()
    components = payload["infra"]["components"]
    mysterybox = next(row for row in components if row["id"] == "mysterybox")
    mysterybox["inputs"]["payload_values"] = {"app": {"API_KEY": "do-not-store"}}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    with pytest.raises(ValueError, match="input 'payload_values' is runtime-only"):
        render_project(config, paths, source_profile=SourceProfile.LOCAL)


def test_render_uses_resolved_local_path_for_local_module_sources(
    tmp_path: Path,
) -> None:
    _set_catalog_profile(SourceProfile.LOCAL)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload_with_mk8s()

    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    expected_mk8s_source = (
        resolve_component_sources_file().parent / "../../platform-infra/modules/mk8s"
    ).resolve()
    assert f'source = "{expected_mk8s_source}"' in main_tf


def test_render_rejects_version_for_local_module_sources(tmp_path: Path) -> None:
    _set_catalog_profile(SourceProfile.LOCAL)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
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
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    with pytest.raises(ValueError, match="resolves to a local directory"):
        render_project(config, paths, source_profile=SourceProfile.LOCAL)


def test_render_prefers_active_catalog_source_over_stale_config_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_catalog_profile(SourceProfile.PORTABLE)
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload_with_mk8s()

    components = payload.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    for row in components:
        if isinstance(row, dict) and str(row.get("id", "")).strip().lower() == "mk8s":
            row["source"] = "../../platform-infra/modules/mk8s"
            break

    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=False, type_hint="string"),
            ModuleVariable(name="cluster_name", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_count", required=False, type_hint="number"),
            ModuleVariable(name="cpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_preset", required=False, type_hint="string"),
            ModuleVariable(name="subnet_id", required=False, type_hint="string"),
            ModuleVariable(name="gpu_enabled", required=False, type_hint="bool"),
            ModuleVariable(
                name="gpu_stack_source",
                required=False,
                type_hint="string",
                has_default=True,
                default="nebius_image",
            ),
            ModuleVariable(name="gpu_stack_preset", required=False, type_hint="string"),
            ModuleVariable(
                name="mk8s_cluster_public_endpoint",
                required=False,
                type_hint="bool",
            ),
            ModuleVariable(
                name="kube_network_service_cidrs",
                required=False,
                type_hint="list(string)",
            ),
        ),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    render_project(config, paths, source_profile=SourceProfile.PORTABLE)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    assert (
        'source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main"'
        in main_tf
    )
