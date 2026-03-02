from __future__ import annotations

from pathlib import Path

import yaml

from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_model import to_dynamic_payload
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.paths import resolve_instance_paths, validate_path_alignment
from nebius_cxcli.render import render_instance


def _instance_config_path(base: Path) -> Path:
    return (
        base
        / "deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "cluster-a"
        / "config.yaml"
    )


def _starter_payload(*, selected_infra: set[str], selected_apps: set[str]) -> dict:
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            env="prod",
            cluster_name="cluster-a",
            project_id="project-456",
            region_id="eu-north1",
            subnet_id="subnet-abc123",
            email="ops@example.com",
            selected_infra=selected_infra,
            selected_apps=selected_apps,
            infra_entries=component_entries("infra"),
            app_entries=component_entries("apps"),
        )
    )
    assert isinstance(payload, dict)
    payload["infra"]["ssh_public_key"] = (
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB8Yq7Rr0x2GdQ8gJ5Q40gF4yHahx7s6vH8kKf+demo"
    )
    return payload


def _infra_component_row(payload: dict, component_id: str) -> dict:
    components = payload.get("infra", {}).get("components", [])
    if not isinstance(components, list):
        raise KeyError(component_id)
    for item in components:
        if not isinstance(item, dict):
            continue
        if str(item.get("id", "")).strip().lower() == component_id:
            return item
    raise KeyError(component_id)


def test_render_creates_source_only_module_and_flux_outputs(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _instance_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"n8n"})
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    if isinstance(mk8s_inputs, dict):
        mk8s_inputs["subnet_id"] = "subnet-abc123"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    result = render_instance(config, paths)
    assert len(result.files_written) >= 3

    terraform_tf = (paths.infra_dir / "terraform.tf").read_text(encoding="utf-8")
    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")

    assert 'provider "nebius"' in terraform_tf
    assert 'module "custom-mk8s" {' in main_tf
    assert '../../platform-infra/modules/mk8s' in main_tf
    assert "subnet-abc123" in main_tf
    assert tfvars.strip() == "{}"

    n8n_release = paths.flux_dir / "apps" / "workloads" / "n8n-helmrelease.yaml"
    assert n8n_release.exists()


def test_render_emits_dynamic_provider_resource_blocks(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _instance_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    runtime_payload = _starter_payload(selected_infra=set(), selected_apps=set())
    dynamic_payload = to_dynamic_payload(runtime_payload)
    dynamic_payload["infra"]["components"] = [
        {
            "id": "runtime-network",
            "enabled": True,
            "inputs": {
                "resource_type": "nebius_vpc_v1_network",
                "resource_name": "runtime_network",
                "depends_on_platform": False,
                "parent_id": "project-456",
                "name": "runtime-network",
            },
        }
    ]

    config_path.write_text(yaml.safe_dump(dynamic_payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    render_instance(config, paths)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    assert 'resource "nebius_vpc_v1_network" "runtime_network"' in main_tf
    assert 'name = "runtime-network"' in main_tf
    assert 'parent_id = "project-456"' in main_tf


def test_render_dynamic_release_shape_writes_flux_manifests(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _instance_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    runtime_payload = _starter_payload(selected_infra=set(), selected_apps=set())
    dynamic_payload = to_dynamic_payload(runtime_payload)
    dynamic_payload["apps"]["releases"] = [
        {
            "id": "runtime-app",
            "section": "workloads",
            "enabled": True,
            "values": {
                "namespace": "runtime-app",
                "chart": {
                    "repo": "https://example.invalid/charts",
                    "name": "runtime-app",
                    "version": "1.0.0",
                },
                "values": {"replicaCount": 1},
            },
        }
    ]

    config_path.write_text(yaml.safe_dump(dynamic_payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    render_instance(config, paths)

    repo_sources = paths.flux_dir / "sources" / "helm-repositories.yaml"
    release = paths.flux_dir / "apps" / "workloads" / "runtime-app-helmrelease.yaml"
    assert repo_sources.exists()
    assert release.exists()

    release_doc = yaml.safe_load(release.read_text(encoding="utf-8"))
    assert release_doc["metadata"]["name"] == "runtime-app"
    assert release_doc["spec"]["chart"]["spec"]["chart"] == "runtime-app"
