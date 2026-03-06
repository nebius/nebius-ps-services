from __future__ import annotations

from pathlib import Path

import pytest
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
        / "project-456"
        / "config.yaml"
    )


def _starter_payload(*, selected_infra: set[str], selected_apps: set[str]) -> dict:
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
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
    assert len(result.files_written) >= 5

    versions_tf = (paths.infra_dir / "versions.tf").read_text(encoding="utf-8")
    providers_tf = (paths.infra_dir / "providers.tf").read_text(encoding="utf-8")
    variables_tf = (paths.infra_dir / "variables.tf").read_text(encoding="utf-8")
    backend_tf = (paths.infra_dir / "backend.tf").read_text(encoding="utf-8")
    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")

    assert "required_providers" in versions_tf
    assert 'backend "s3"' in backend_tf
    assert "use_lockfile = true" in backend_tf
    assert "access_key" not in backend_tf
    assert "secret_key" not in backend_tf
    assert 'provider "nebius"' in providers_tf
    assert 'module "mk8s" {' in main_tf
    assert 'module "custom-' not in main_tf
    assert (
        'source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main"'
        in main_tf
    )
    assert "subnet_id = var.mk8s_subnet_id" in main_tf
    assert "inputs = jsondecode(" not in main_tf
    assert 'variable "mk8s_subnet_id" {' in variables_tf
    assert '"mk8s_subnet_id": "subnet-abc123"' in tfvars

    n8n_release = paths.flux_dir / "helmrelease-workloads-n8n.yaml"
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


def test_render_dynamic_chart_shape_writes_flux_manifests(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _instance_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    runtime_payload = _starter_payload(selected_infra=set(), selected_apps=set())
    dynamic_payload = to_dynamic_payload(runtime_payload)
    dynamic_payload["apps"]["charts"] = [
        {
            "id": "runtime-app",
            "group": "workloads",
            "enabled": True,
            "repo": "https://example.invalid/charts",
            "version": "1.0.0",
            "namespace": "runtime-app",
            "release-name": "runtime-app",
            "values": {"replicaCount": 1},
        }
    ]

    config_path.write_text(yaml.safe_dump(dynamic_payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    render_instance(config, paths)

    repo_sources = paths.flux_dir / "helm-repositories.yaml"
    release = paths.flux_dir / "helmrelease-workloads-runtime-app.yaml"
    assert repo_sources.exists()
    assert release.exists()

    release_doc = yaml.safe_load(release.read_text(encoding="utf-8"))
    assert release_doc["metadata"]["name"] == "runtime-app"
    assert release_doc["spec"]["chart"]["spec"]["chart"] == "runtime-app"


def test_render_dynamic_oci_chart_writes_flux_oci_repository(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _instance_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    runtime_payload = _starter_payload(selected_infra=set(), selected_apps=set())
    dynamic_payload = to_dynamic_payload(runtime_payload)
    dynamic_payload["apps"]["charts"] = [
        {
            "id": "gateway-helm",
            "group": "platform",
            "enabled": True,
            "repo": "oci://docker.io/envoyproxy/gateway-helm",
            "version": "1.4.2",
            "namespace": "envoy-gateway-system",
            "release-name": "envoy-gateway",
            "values": {},
        }
    ]

    config_path.write_text(yaml.safe_dump(dynamic_payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    render_instance(config, paths)

    repo_sources = paths.flux_dir / "helm-repositories.yaml"
    release = paths.flux_dir / "helmrelease-platform-envoy-gateway.yaml"
    assert repo_sources.exists()
    assert release.exists()

    repo_docs = [
        doc
        for doc in yaml.safe_load_all(repo_sources.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    helm_repo_doc = next(doc for doc in repo_docs if doc.get("kind") == "HelmRepository")
    assert helm_repo_doc["spec"]["type"] == "oci"
    assert helm_repo_doc["spec"]["url"] == "oci://docker.io/envoyproxy"

    release_doc = yaml.safe_load(release.read_text(encoding="utf-8"))
    chart_spec = release_doc["spec"]["chart"]["spec"]
    assert chart_spec["sourceRef"]["kind"] == "HelmRepository"
    assert chart_spec["chart"] == "gateway-helm"
    assert chart_spec["version"] == "1.4.2"


def test_render_rejects_legacy_nested_flux_layout(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _instance_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"n8n"})
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    # Simulate stale legacy layout from previous versions.
    (paths.flux_dir / "apps").mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="Unsupported legacy Flux layout detected"):
        render_instance(config, paths)
