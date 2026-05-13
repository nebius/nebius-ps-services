from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import nebius_cxcli.component_sources as component_sources
from nebius_cxcli import cli
from nebius_cxcli.component_sources import (
    ComponentOutput,
    SourceProfile,
    load_component_sources,
    reset_component_sources_cache,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_model import to_dynamic_payload
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.deploy_targets import flux_target_dir
from nebius_cxcli.flux_render import render_flux
from nebius_cxcli.mk8s_gpu import materialize_mk8s_gpu_app_values
from nebius_cxcli.mysterybox_eso import materialize_mysterybox_eso_app_values
from nebius_cxcli.paths import resolve_project_paths, validate_path_alignment
from nebius_cxcli.render import render_project
from nebius_cxcli.runtime_introspection import ModuleVariable, reset_runtime_introspection_cache
from nebius_cxcli.terraform_provider import build_provider_module_name

_VALID_ED25519_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f demo@example"
)


def _portable_chart_source(*, repo: str, chart: str, version: str = "") -> dict[str, object]:
    portable: dict[str, object] = {
        "repo": repo,
        "chart": chart,
    }
    if version:
        portable["version"] = version
    return {"portable": portable}


def _reset_catalog_override() -> None:
    set_component_sources_file_override(None)
    set_component_sources_profile_override(None)
    reset_component_sources_cache()
    reset_runtime_introspection_cache()
    reset_component_entry_cache()


def setup_function() -> None:
    _reset_catalog_override()
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.LOCAL)


def teardown_function() -> None:
    _reset_catalog_override()


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
            ComponentOutput(
                name="bucket_name",
                kind="terraform_output",
                source_path="bucket_name",
                sensitive=False,
            ),
            ComponentOutput(
                name="bucket_endpoint",
                kind="terraform_output",
                source_path="bucket_endpoint",
                sensitive=False,
            ),
        ),
    )


def _project_config_path(base: Path) -> Path:
    return base / "deployments" / "tenant-name-example" / "project-name-example" / "config.yaml"


def _target_flux_dir(paths, target_ref: str = "mk8s") -> Path:
    return flux_target_dir(paths, target_ref)


def _load_mysterybox_eso_post_flux_objects(paths, target_ref: str = "mk8s") -> list[dict]:
    path = _target_flux_dir(paths, target_ref) / "post-flux-mysterybox-eso.yaml"
    return [item for item in yaml.safe_load_all(path.read_text(encoding="utf-8")) if item]


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


def _set_catalog_override(
    path: Path, *, source_profile: SourceProfile = SourceProfile.PORTABLE
) -> None:
    set_component_sources_file_override(path)
    set_component_sources_profile_override(source_profile)
    reset_component_sources_cache()
    reset_runtime_introspection_cache()
    reset_component_entry_cache()


def _local_catalog_path() -> Path:
    return Path(__file__).resolve().parents[1] / "component_sources.yaml"


def _catalog(
    *,
    infra: dict[str, object] | None = None,
    apps: dict[str, object] | None = None,
    cli: dict[str, object] | None = None,
    shared: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "components": {
            "infra": infra or {},
            "apps": apps or {},
        }
    }
    if cli is not None:
        payload["cli"] = cli
    if shared is not None:
        payload["shared"] = shared
    return payload


def _catalog_with_shared_admin_ssh(
    tmp_path: Path,
    *,
    user_name: str = "ubuntu",
    public_key: str = _VALID_ED25519_PUBLIC_KEY,
) -> Path:
    source_catalog = _local_catalog_path()
    payload = yaml.safe_load(source_catalog.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    shared = payload.setdefault("shared", {})
    assert isinstance(shared, dict)
    admin_ssh = shared.setdefault("admin_ssh", {})
    assert isinstance(admin_ssh, dict)
    admin_ssh["user_name"] = user_name
    admin_ssh["public_key"] = public_key
    override_path = tmp_path / "component_sources.yaml"
    override_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return override_path


def test_render_creates_source_only_module_and_flux_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.PORTABLE)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"n8n"})
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    if isinstance(mk8s_inputs, dict):
        mk8s_inputs["subnet_id"] = "subnet-abc123"
        mk8s_inputs["gpu_enabled"] = False
        mk8s_inputs["gpu_stack_source"] = "nebius_image"
        mk8s_inputs["gpu_nodes_boot_disk_type"] = "NETWORK_SSD"
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
            ModuleVariable(name="gpu_stack_source", required=False, type_hint="string"),
            ModuleVariable(name="gpu_nodes_boot_disk_type", required=False, type_hint="string"),
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

    result = render_project(config, paths, source_profile=SourceProfile.PORTABLE)
    assert len(result.files_written) >= 5

    versions_tf = (paths.infra_dir / "versions.tf").read_text(encoding="utf-8")
    providers_tf = (paths.infra_dir / "providers.tf").read_text(encoding="utf-8")
    variables_tf = (paths.infra_dir / "variables.tf").read_text(encoding="utf-8")
    backend_tf = (paths.infra_dir / "backend.tf").read_text(encoding="utf-8")
    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    outputs_tf = (paths.infra_dir / "outputs.tf").read_text(encoding="utf-8")
    tfvars = (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")

    assert "required_providers" in versions_tf
    assert 'backend "s3"' in backend_tf
    assert "use_lockfile = true" in backend_tf
    assert "access_key" not in backend_tf
    assert "secret_key" not in backend_tf
    assert 'provider "nebius"' in providers_tf
    assert 'module "mk8s" {' in main_tf
    assert 'module "custom-' not in main_tf
    portable_catalog = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml",
        source_profile=SourceProfile.PORTABLE,
    )
    mk8s_source = next(item.source for item in portable_catalog.tf_modules if item.module == "mk8s")
    assert f'source = "{mk8s_source}"' in main_tf
    assert "subnet_id = var.mk8s_subnet_id" in main_tf
    assert "inputs = jsondecode(" not in main_tf
    assert 'output "mk8s_cluster_id" {' in outputs_tf
    assert "value       = module.mk8s.cluster_id" in outputs_tf
    assert 'output "mk8s_cluster_ca_certificate" {' in outputs_tf
    assert "value       = module.mk8s.cluster_ca_certificate" in outputs_tf
    assert "sensitive   = true" in outputs_tf
    assert 'variable "mk8s_subnet_id" {' in variables_tf
    assert '"mk8s_subnet_id": "subnet-abc123"' in tfvars
    assert "mk8s_gpu_stack_source" not in tfvars
    assert "mk8s_gpu_nodes_boot_disk_type" not in tfvars
    assert '"mk8s_kube_network_service_cidrs": [' in tfvars
    assert '"/20"' in tfvars
    assert (
        f'"nebius_provider_module_name": "{build_provider_module_name(client_name="client-a", project_id="project-456")}"'
        in tfvars
    )

    n8n_release = _target_flux_dir(paths) / "helmrelease-workloads-n8n.yaml"
    assert n8n_release.exists()


def test_render_skips_empty_flux_repository_file_when_no_apps_are_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.PORTABLE)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    if isinstance(mk8s_inputs, dict):
        mk8s_inputs["subnet_id"] = "subnet-abc123"
        mk8s_inputs["cpu_nodes_platform"] = "cpu-d3"
        mk8s_inputs["cpu_nodes_preset"] = "4vcpu-16gb"
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
            ModuleVariable(name="gpu_stack_source", required=False, type_hint="string"),
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

    target_flux_dir = _target_flux_dir(paths)
    assert not (target_flux_dir / "helm-repositories.yaml").exists()
    kustomization_doc = yaml.safe_load(
        (target_flux_dir / "kustomization.yaml").read_text(encoding="utf-8")
    )
    assert kustomization_doc["resources"] == []


def test_render_passes_mk8s_preemptible_node_group_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.PORTABLE)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs.update(
        {
            "subnet_id": "subnet-abc123",
            "cpu_nodes_platform": "cpu-d3",
            "cpu_nodes_preset": "4vcpu-16gb",
            "cpu_nodes_preemptible": True,
            "gpu_enabled": True,
            "gpu_node_groups": 1,
            "gpu_nodes_count_per_group": 1,
            "gpu_nodes_platform": "gpu-h100-sxm",
            "gpu_nodes_preset": "1gpu-16vcpu-200gb",
            "gpu_nodes_preemptible": True,
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=False, type_hint="string"),
            ModuleVariable(name="cluster_name", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_count", required=False, type_hint="number"),
            ModuleVariable(name="cpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_preset", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_preemptible", required=False, type_hint="bool"),
            ModuleVariable(name="subnet_id", required=False, type_hint="string"),
            ModuleVariable(name="gpu_enabled", required=False, type_hint="bool"),
            ModuleVariable(name="gpu_node_groups", required=False, type_hint="number"),
            ModuleVariable(name="gpu_nodes_count_per_group", required=False, type_hint="number"),
            ModuleVariable(name="gpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="gpu_nodes_preset", required=False, type_hint="string"),
            ModuleVariable(name="gpu_nodes_preemptible", required=False, type_hint="bool"),
            ModuleVariable(name="gpu_stack_source", required=False, type_hint="string"),
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
    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert "cpu_nodes_preemptible = var.mk8s_cpu_nodes_preemptible" in main_tf
    assert "gpu_nodes_preemptible = var.mk8s_gpu_nodes_preemptible" in main_tf
    assert tfvars["mk8s_cpu_nodes_preemptible"] is True
    assert tfvars["mk8s_gpu_nodes_preemptible"] is True


def test_render_passes_vm_preemptible_contract_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.PORTABLE)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"vm"}, selected_apps=set())
    vm = _infra_component_row(payload, "vm")
    vm_inputs = vm.setdefault("inputs", {})
    assert isinstance(vm_inputs, dict)
    vm_inputs.update(
        {
            "name": "gpu-preemptible-vm",
            "subnet_id": "subnet-abc123",
            "platform": "gpu-h100-sxm",
            "preset": "1gpu-16vcpu-200gb",
            "source_image_family": "ubuntu24.04-cuda13.0",
            "ssh_user_name": "ubuntu",
            "preemptible_enabled": True,
            "preemptible_priority": 1,
            "recovery_policy": "FAIL",
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=False, type_hint="string"),
            ModuleVariable(name="name", required=False, type_hint="string"),
            ModuleVariable(name="subnet_id", required=False, type_hint="string"),
            ModuleVariable(name="platform", required=False, type_hint="string"),
            ModuleVariable(name="preset", required=False, type_hint="string"),
            ModuleVariable(name="source_image_family", required=False, type_hint="string"),
            ModuleVariable(name="ssh_user_name", required=False, type_hint="string"),
            ModuleVariable(name="preemptible_enabled", required=False, type_hint="bool"),
            ModuleVariable(name="preemptible_priority", required=False, type_hint="number"),
            ModuleVariable(name="recovery_policy", required=False, type_hint="string"),
        ),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.PORTABLE)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert "preemptible_enabled = var.vm_preemptible_enabled" in main_tf
    assert "preemptible_priority = var.vm_preemptible_priority" in main_tf
    assert "recovery_policy = var.vm_recovery_policy" in main_tf
    assert tfvars["vm_preemptible_enabled"] is True
    assert tfvars["vm_preemptible_priority"] == 1
    assert tfvars["vm_recovery_policy"] == "FAIL"


def test_render_keeps_duplicate_component_instances_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.PORTABLE)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra=set(), selected_apps=set())
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "mk8s",
            "enabled": True,
            "inputs": {
                "parent_id": "project-456",
                "cluster_name": "clust1",
                "cpu_nodes_platform": "cpu-d3",
                "cpu_nodes_preset": "4vcpu-16gb",
            },
        },
        {
            "id": "mk8s",
            "instance_id": "mk8s-2",
            "enabled": True,
            "inputs": {
                "parent_id": "project-456",
                "cluster_name": "clust2",
                "cpu_nodes_platform": "cpu-d3",
                "cpu_nodes_preset": "4vcpu-16gb",
            },
        },
    ]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=False, type_hint="string"),
            ModuleVariable(name="cluster_name", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_count", required=False, type_hint="number"),
            ModuleVariable(name="cpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_preset", required=False, type_hint="string"),
            ModuleVariable(name="gpu_enabled", required=False, type_hint="bool"),
            ModuleVariable(name="gpu_stack_source", required=False, type_hint="string"),
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
    outputs_tf = (paths.infra_dir / "outputs.tf").read_text(encoding="utf-8")

    assert 'module "mk8s" {' in main_tf
    assert 'module "mk8s_2" {' in main_tf
    assert 'output "mk8s_cluster_id" {' in outputs_tf
    assert 'output "mk8s_2_cluster_id" {' in outputs_tf


def test_render_uses_cluster_instance_ids_for_multi_target_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.PORTABLE)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra=set(), selected_apps=set())
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "cluster1",
            "enabled": True,
            "inputs": {
                "parent_id": "project-456",
                "cluster_name": "cluster1",
                "cpu_nodes_platform": "cpu-d3",
                "cpu_nodes_preset": "4vcpu-16gb",
                "gpu_enabled": False,
            },
        },
        {
            "id": "mk8s",
            "instance_id": "cluster2",
            "enabled": True,
            "inputs": {
                "parent_id": "project-456",
                "cluster_name": "cluster2",
                "cpu_nodes_platform": "cpu-d3",
                "cpu_nodes_preset": "4vcpu-16gb",
                "gpu_enabled": False,
            },
        },
    ]
    payload["apps"]["charts"] = [
        {
            "id": "demo-app",
            "instance_id": "cluster1",
            "group": "workloads",
            "enabled": True,
            "repo": "https://example.invalid/charts",
            "version": "1.0.0",
            "namespace": "demo",
            "release-name": "demo",
            "values": {},
        },
        {
            "id": "demo-app",
            "instance_id": "cluster2",
            "group": "workloads",
            "enabled": True,
            "repo": "https://example.invalid/charts",
            "version": "1.0.0",
            "namespace": "demo",
            "release-name": "demo",
            "values": {},
        },
    ]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=False, type_hint="string"),
            ModuleVariable(name="cluster_name", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_count", required=False, type_hint="number"),
            ModuleVariable(name="cpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_preset", required=False, type_hint="string"),
            ModuleVariable(name="gpu_enabled", required=False, type_hint="bool"),
            ModuleVariable(name="gpu_stack_source", required=False, type_hint="string"),
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
    outputs_tf = (paths.infra_dir / "outputs.tf").read_text(encoding="utf-8")

    assert 'module "cluster1" {' in main_tf
    assert 'module "cluster2" {' in main_tf
    assert 'module "mk8s" {' not in main_tf
    assert "var.mk8s_" not in main_tf
    assert 'output "cluster1_cluster_id" {' in outputs_tf
    assert 'output "cluster2_cluster_id" {' in outputs_tf
    assert "module.cluster1.cluster_id" in outputs_tf
    assert "module.cluster2.cluster_id" in outputs_tf

    assert (_target_flux_dir(paths, "cluster1") / "helmrelease-workloads-demo.yaml").exists()
    assert (_target_flux_dir(paths, "cluster2") / "helmrelease-workloads-demo.yaml").exists()
    assert not _target_flux_dir(paths, "mk8s").exists()


def test_render_binds_soperator_external_nfs_from_matching_nfs_output(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "nfs"}, selected_apps={"soperator"})
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "cluster1",
            "enabled": True,
            "inputs": {
                "parent_id": "project-456",
                "cluster_name": "cluster1",
                "gpu_enabled": True,
                "gpu_node_groups": 1,
                "gpu_nodes_count_per_group": 1,
                "gpu_nodes_platform": "gpu-h100-sxm",
                "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
            },
        },
        {
            "id": "nfs",
            "instance_id": "cluster1",
            "enabled": True,
            "inputs": {},
        },
    ]
    payload["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": "cluster1",
            "group": "slurm",
            "enabled": True,
            "repo": "https://github.com/nebius/nebius-ps-services/tree/main/helm-charts/soperator",
            "version": "",
            "namespace": "soperator",
            "release-name": "soperator",
            "values": {},
        }
    ]

    render_flux(
        payload,
        paths,
        component_output_values={
            "cluster1.server_ip": "10.10.0.5",
            "cluster1.export_path": "/srv/nfs/home",
        },
    )

    release_doc = yaml.safe_load(
        (_target_flux_dir(paths, "cluster1") / "helmrelease-slurm-soperator.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert release_doc["spec"]["values"]["externalNfs"] == {
        "enabled": True,
        "server": "10.10.0.5",
        "path": "/srv/nfs/home",
    }


def test_render_local_soperator_chart_source_writes_static_manifest(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    render_project(load_config(config_path), paths, source_profile=SourceProfile.LOCAL)

    flux_dir = _target_flux_dir(paths)
    rendered_chart = flux_dir / "post-flux-helmrender-slurm-soperator.yaml"
    assert rendered_chart.exists()
    assert not (flux_dir / "helmrelease-slurm-soperator.yaml").exists()
    kustomization = yaml.safe_load((flux_dir / "kustomization.yaml").read_text(encoding="utf-8"))
    assert "./post-flux-helmrender-slurm-soperator.yaml" not in kustomization["resources"]
    rendered = rendered_chart.read_text(encoding="utf-8")
    assert "kind: SlurmCluster" in rendered
    assert "kind: Deployment" in rendered
    rendered_docs = [doc for doc in yaml.safe_load_all(rendered) if isinstance(doc, dict)]
    slurm_cluster = next(doc for doc in rendered_docs if doc.get("kind") == "SlurmCluster")
    assert slurm_cluster["spec"]["partitionConfiguration"]["configType"] == "structured"
    assert "PluginDir=/usr/lib/x86_64-linux-gnu/slurm" in slurm_cluster["spec"]["customSlurmConfig"]
    assert slurm_cluster["spec"]["slurmNodes"]["accounting"]["enabled"] is True
    assert slurm_cluster["spec"]["slurmNodes"]["rest"]["enabled"] is True
    assert slurm_cluster["spec"]["slurmNodes"]["accounting"]["mariadbOperator"]["enabled"] is True
    nodeset = next(doc for doc in rendered_docs if doc.get("kind") == "NodeSet")
    worker_mounts = nodeset["spec"]["slurmd"]["volumes"]["customVolumeMounts"]
    assert {
        "mountPath": "/opt/slurm_scripts/",
        "name": "slurm-scripts",
        "volumeSource": {"configMap": {"defaultMode": 493, "name": "soperator-slurm-scripts"}},
    } in worker_mounts
    filter_names = {item["name"] for item in slurm_cluster["spec"]["k8sNodeFilters"]}
    assert filter_names == {"no-gpu", "system", "controller", "login", "accounting"}
    assert (
        slurm_cluster["spec"]["slurmNodes"]["exporter"]["podMonitorConfig"]["scrapeTimeout"]
        == "20s"
    )
    manager_deployment = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "Deployment"
        and doc.get("metadata", {}).get("name") == "soperator-manager"
    )
    assert manager_deployment["metadata"]["namespace"] == "soperator"
    mariadb_operator_deployment = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "Deployment"
        and doc.get("metadata", {}).get("name") == "soperator-mariadb-operator"
    )
    assert mariadb_operator_deployment["metadata"]["namespace"] == "soperator"
    mount_scripts = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "soperator-mount-scripts"
    )
    assert mount_scripts["metadata"]["namespace"] == "soperator"


def test_render_local_soperator_mixed_profile_writes_two_nodesets(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    for chart in payload["apps"]["charts"]:
        if isinstance(chart, dict) and chart.get("id") == "soperator":
            chart["profile"] = "nebius-mixed-v1"
            chart["values"] = {}
    cli._materialize_soperator_component_defaults(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    render_project(load_config(config_path), paths, source_profile=SourceProfile.LOCAL)

    rendered_chart = _target_flux_dir(paths, "mk8s") / "post-flux-helmrender-slurm-soperator.yaml"
    rendered_docs = [
        doc
        for doc in yaml.safe_load_all(rendered_chart.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    node_sets = {
        doc["metadata"]["name"]: doc
        for doc in rendered_docs
        if doc.get("apiVersion") == "slurm.nebius.ai/v1alpha1" and doc.get("kind") == "NodeSet"
    }
    assert set(node_sets) == {"worker-cpu", "worker-gpu"}
    assert "nvidia.com/gpu" not in node_sets["worker-cpu"]["spec"]["slurmd"]["resources"]
    assert node_sets["worker-gpu"]["spec"]["slurmd"]["resources"]["nvidia.com/gpu"] == 8
    assert node_sets["worker-cpu"]["spec"]["nodeConfig"]["static"] == (
        "Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=1"
    )
    assert node_sets["worker-gpu"]["spec"]["nodeConfig"]["static"] == (
        "Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=1 Gres=gpu:8"
    )

    slurm_cluster = next(doc for doc in rendered_docs if doc.get("kind") == "SlurmCluster")
    assert slurm_cluster["spec"]["partitionConfiguration"]["partitions"] == [
        {
            "name": "cpu",
            "nodeSetRefs": ["worker-cpu"],
            "config": "Default=YES MaxTime=INFINITE State=UP PriorityTier=5",
        },
        {
            "name": "gpu",
            "nodeSetRefs": ["worker-gpu"],
            "config": "Default=NO MaxTime=INFINITE State=UP PriorityTier=10",
        },
    ]


def test_render_local_soperator_partition_profile_writes_policy_partitions(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    for chart in payload["apps"]["charts"]:
        if isinstance(chart, dict) and chart.get("id") == "soperator":
            chart["profile"] = "nebius-mixed-v1"
            chart["values"] = {"partitionProfile": "with-debug-long"}
    cli._materialize_soperator_component_defaults(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    render_project(load_config(config_path), paths, source_profile=SourceProfile.LOCAL)

    rendered_chart = _target_flux_dir(paths, "mk8s") / "post-flux-helmrender-slurm-soperator.yaml"
    rendered_docs = [
        doc
        for doc in yaml.safe_load_all(rendered_chart.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    slurm_cluster = next(doc for doc in rendered_docs if doc.get("kind") == "SlurmCluster")
    assert slurm_cluster["spec"]["partitionConfiguration"]["partitions"] == [
        {
            "name": "cpu",
            "nodeSetRefs": ["worker-cpu"],
            "config": "Default=YES MaxTime=INFINITE State=UP PriorityTier=5",
        },
        {
            "name": "gpu",
            "nodeSetRefs": ["worker-gpu"],
            "config": "Default=NO MaxTime=INFINITE State=UP PriorityTier=10",
        },
        {
            "name": "debug",
            "nodeSetRefs": ["worker-cpu", "worker-gpu"],
            "config": "Default=NO MaxTime=00:30:00 State=UP PriorityTier=100",
        },
        {
            "name": "long",
            "nodeSetRefs": ["worker-cpu", "worker-gpu"],
            "config": "Default=NO MaxTime=7-00:00:00 State=UP PriorityTier=1",
        },
    ]


def test_render_local_soperator_feature_partition_profile_writes_node_features(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    for chart in payload["apps"]["charts"]:
        if isinstance(chart, dict) and chart.get("id") == "soperator":
            chart["profile"] = "nebius-mixed-v1"
            chart["values"] = {"partitionProfile": "with-h100-infiniband-debug-long"}
    cli._materialize_soperator_component_defaults(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    render_project(load_config(config_path), paths, source_profile=SourceProfile.LOCAL)

    rendered_chart = _target_flux_dir(paths, "mk8s") / "post-flux-helmrender-slurm-soperator.yaml"
    rendered_docs = [
        doc
        for doc in yaml.safe_load_all(rendered_chart.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    slurm_cluster = next(doc for doc in rendered_docs if doc.get("kind") == "SlurmCluster")
    assert [
        partition["name"]
        for partition in slurm_cluster["spec"]["partitionConfiguration"]["partitions"]
    ] == ["cpu", "gpu", "h100", "infiniband", "debug", "long"]
    worker_gpu = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "NodeSet" and doc.get("metadata", {}).get("name") == "worker-gpu"
    )
    assert worker_gpu["spec"]["nodeConfig"]["features"] == [
        "gpu",
        "cuda",
        "h100",
        "infiniband",
    ]


def test_render_local_soperator_notifier_uses_only_secret_reference(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra=set(), selected_apps={"soperator-notifier"})
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    render_project(load_config(config_path), paths, source_profile=SourceProfile.LOCAL)

    rendered_chart = paths.flux_dir / "post-flux-helmrender-slurm-soperator-notifier.yaml"
    rendered = rendered_chart.read_text(encoding="utf-8")
    assert "hooks.slack.com" not in rendered
    assert "webhookUrl" not in rendered
    rendered_docs = [doc for doc in yaml.safe_load_all(rendered) if isinstance(doc, dict)]
    alertmanager_config = next(
        doc for doc in rendered_docs if doc.get("kind") == "VMAlertmanagerConfig"
    )
    slack_config = alertmanager_config["spec"]["receivers"][0]["slack_configs"][0]
    assert slack_config["api_url"] == {
        "name": "soperator-notifier-slack-webhook",
        "key": "url",
    }


def test_render_local_soperator_backup_uses_only_secret_reference(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(
        selected_infra={"object-storage"},
        selected_apps={"soperator-backup-config"},
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    render_project(
        load_config(config_path),
        paths,
        component_output_values={
            "object-storage.bucket_name": "soperator-jail-backups",
            "object-storage.bucket_endpoint": "https://soperator-jail-backups.example:443",
        },
        source_profile=SourceProfile.LOCAL,
    )

    rendered_chart = paths.flux_dir / "post-flux-helmrender-slurm-soperator-jail-backup.yaml"
    rendered = rendered_chart.read_text(encoding="utf-8")
    assert "aws-access-key-id:" not in rendered
    assert "aws-secret-value" not in rendered
    rendered_docs = [doc for doc in yaml.safe_load_all(rendered) if isinstance(doc, dict)]
    schedule = next(doc for doc in rendered_docs if doc.get("kind") == "Schedule")
    backend = schedule["spec"]["backend"]
    assert backend["s3"]["bucket"] == "soperator-jail-backups"
    assert backend["s3"]["accessKeyIDSecretRef"] == {
        "name": "jail-backup",
        "key": "aws-access-key-id",
    }
    assert backend["repoPasswordSecretRef"] == {
        "name": "jail-backup",
        "key": "backup-password",
    }


def test_render_soperator_mk8s_node_groups_attach_sibling_sfs(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["instance_id"] = "cluster1"
    mk8s["inputs"] = {
        "cluster_name": "cluster1",
        "node_groups": {
            "controller": {"workload": "controller", "fixed_node_count": 1, "jail": True},
            "login": {"workload": "login", "fixed_node_count": 1, "jail": True},
            "system": {"workload": "system", "fixed_node_count": 1},
            "compact-cpu": {
                "nodeset_name": "worker-cpu",
                "workload": "worker",
                "fixed_node_count": 2,
                "jail": True,
                "sfs_filesystem_keys": ["controller-spool"],
            },
        },
        "mk8s_gpu_node_group_overrides": {
            "template": {"metadata": {"labels": {"slurm.nebius.ai/jail": "true"}}}
        },
    }
    sfs = _infra_component_row(payload, "sfs")
    sfs["instance_id"] = "sfs"
    sfs["inputs"] = {
        "filesystems": {
            "jail": {"name": "cluster1-jail", "size_gib": 1024, "mount_tag": "jail"},
            "controller-spool": {
                "name": "cluster1-controller-spool",
                "size_gib": 128,
                "mount_tag": "controller-spool",
            },
        }
    }
    for chart in payload["apps"]["charts"]:
        if isinstance(chart, dict) and chart.get("instance_id") == "mk8s":
            chart["instance_id"] = "cluster1"
    payload["deploy"]["targets"][0]["instance_id"] = "cluster1"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    assert 'module "cluster1" {' in main_tf
    assert 'module "sfs" {' in main_tf
    assert 'filesystems = [for key in ["jail", "controller-spool"] : {' in main_tf
    assert 'filesystems = [for key in ["jail"] : {' in main_tf
    assert main_tf.count('filesystems = [for key in ["jail", "controller-spool"] : {') == 2
    assert "mount_tag = module.sfs.filesystems[key].mount_tag" in main_tf
    assert "id = module.sfs.filesystems[key].id" in main_tf


def test_render_dynamic_chart_shape_writes_flux_manifests(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    runtime_payload = _starter_payload(selected_infra=set(), selected_apps=set())
    dynamic_payload = to_dynamic_payload(runtime_payload)
    dynamic_payload["apps"]["charts"] = [
        {
            "id": "runtime-app",
            "instance_id": "runtime-app",
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
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    repo_sources = paths.flux_dir / "helm-repositories.yaml"
    release = paths.flux_dir / "helmrelease-workloads-runtime-app.yaml"
    assert repo_sources.exists()
    assert release.exists()

    release_doc = yaml.safe_load(release.read_text(encoding="utf-8"))
    assert release_doc["metadata"]["name"] == "runtime-app"
    assert release_doc["spec"]["chart"]["spec"]["chart"] == "runtime-app"


def test_render_instance_resets_generated_bundle_and_removes_stale_files(
    tmp_path: Path,
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.LOCAL)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"gateway-helm"})
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    if isinstance(mk8s_inputs, dict):
        mk8s_inputs["subnet_id"] = "subnet-abc123"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    stale_tf = paths.infra_dir / "stale.tf"
    bootstrap_flux_dir = paths.flux_dir / "flux-system"
    bootstrap_sync = bootstrap_flux_dir / "gotk-sync.yaml"
    bootstrap_components = bootstrap_flux_dir / "gotk-components.yaml"
    bootstrap_kustomization = bootstrap_flux_dir / "kustomization.yaml"
    stale_flux_file = paths.flux_dir / "stale.yaml"
    stale_inventory = paths.inventory_dir / "old.json"
    stale_top_level = paths.generated_dir / "obsolete.txt"
    stale_tf.parent.mkdir(parents=True, exist_ok=True)
    bootstrap_flux_dir.mkdir(parents=True, exist_ok=True)
    stale_inventory.parent.mkdir(parents=True, exist_ok=True)
    stale_tf.write_text('resource "null_resource" "stale" {}\n', encoding="utf-8")
    bootstrap_sync.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    bootstrap_components.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    bootstrap_kustomization.write_text(
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n- ./gotk-components.yaml\n- ./gotk-sync.yaml\n",
        encoding="utf-8",
    )
    stale_flux_file.write_text("apiVersion: v1\nkind: Secret\n", encoding="utf-8")
    stale_inventory.write_text("{}\n", encoding="utf-8")
    stale_top_level.write_text("obsolete\n", encoding="utf-8")

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    assert not stale_tf.exists()
    assert not stale_flux_file.exists()
    assert not stale_inventory.exists()
    assert not stale_top_level.exists()
    assert not bootstrap_sync.exists()
    assert not bootstrap_components.exists()
    assert not bootstrap_kustomization.exists()
    assert (paths.infra_dir / "main.tf").exists()
    kustomization_doc = yaml.safe_load(
        (_target_flux_dir(paths) / "kustomization.yaml").read_text(encoding="utf-8")
    )
    assert "./flux-system" not in kustomization_doc["resources"]
    assert (paths.inventory_dir / "deploy-report.md").exists()


def test_render_instance_preserves_existing_generated_bundle_when_rerender_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"n8n"})
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    paths.generated_dir.mkdir(parents=True, exist_ok=True)
    preserved = paths.generated_dir / "preserved.txt"
    preserved.write_text("keep-me\n", encoding="utf-8")

    def _fake_render_terraform_artifacts(*_args, **_kwargs):
        target_paths = _args[1]
        written = target_paths.infra_dir / "main.tf"
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text("terraform {}\n", encoding="utf-8")
        return [written]

    monkeypatch.setattr(
        "nebius_cxcli.render.render_terraform_artifacts", _fake_render_terraform_artifacts
    )
    monkeypatch.setattr(
        "nebius_cxcli.render.render_flux",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        render_project(config, paths, source_profile=SourceProfile.LOCAL)

    assert preserved.read_text(encoding="utf-8") == "keep-me\n"
    assert not any(paths.project_dir.glob(".generated-staging-*"))


def test_render_dynamic_oci_chart_writes_flux_oci_repository(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    runtime_payload = _starter_payload(selected_infra=set(), selected_apps=set())
    dynamic_payload = to_dynamic_payload(runtime_payload)
    dynamic_payload["apps"]["charts"] = [
        {
            "id": "gateway-helm",
            "instance_id": "gateway-helm",
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
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    repo_sources = paths.flux_dir / "helm-repositories.yaml"
    namespace_manifest = paths.flux_dir / "namespace-envoy-gateway-system.yaml"
    release = paths.flux_dir / "helmrelease-platform-envoy-gateway.yaml"
    kustomization = paths.flux_dir / "kustomization.yaml"
    assert repo_sources.exists()
    assert namespace_manifest.exists()
    assert release.exists()
    assert kustomization.exists()

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

    namespace_doc = yaml.safe_load(namespace_manifest.read_text(encoding="utf-8"))
    assert namespace_doc == {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": "envoy-gateway-system"},
    }
    kustomization_doc = yaml.safe_load(kustomization.read_text(encoding="utf-8"))
    assert "./namespace-envoy-gateway-system.yaml" in kustomization_doc["resources"]
    assert kustomization_doc["resources"].index(
        "./namespace-envoy-gateway-system.yaml"
    ) < kustomization_doc["resources"].index("./helmrelease-platform-envoy-gateway.yaml")


def test_render_externalizes_grafana_dashboard_json_to_generated_bundle(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    payload["deploy"]["targets"][0]["observability"]["enabled"] = True
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    paths.flux_dir.mkdir(parents=True, exist_ok=True)

    written = render_flux(config, paths)

    dashboard_dir = paths.generated_dir / "grafana_dashboards" / "mk8s" / "nebius-kubernetes"
    dashboard_files = {
        item.name for item in dashboard_dir.iterdir() if item.is_file() and item.suffix == ".json"
    }
    assert dashboard_files == {
        "kubernetes-cluster-monitoring.json",
        "kubernetes-gpu.json",
        "kubernetes-logs-from-loki.json",
        "kubernetes-traces.json",
    }
    assert dashboard_dir / "kubernetes-cluster-monitoring.json" in written

    flux_dir = _target_flux_dir(paths)
    configmap = flux_dir / "configmap-grafana-nebius-kubernetes-dashboards.yaml"
    release = flux_dir / "helmrelease-observability-grafana.yaml"
    kustomization = flux_dir / "kustomization.yaml"
    assert configmap.exists()
    assert release.exists()

    configmap_doc = yaml.safe_load(configmap.read_text(encoding="utf-8"))
    assert configmap_doc["metadata"] == {
        "name": "grafana-nebius-kubernetes-dashboards",
        "namespace": "observability",
    }
    assert set(configmap_doc["data"]) == dashboard_files

    release_doc = yaml.safe_load(release.read_text(encoding="utf-8"))
    values = release_doc["spec"]["values"]
    assert values["dashboardsConfigMaps"] == {
        "nebius-kubernetes": "grafana-nebius-kubernetes-dashboards"
    }
    assert set(values["dashboards"]["nebius"]) == {"nebius-disk"}
    assert "json:" not in yaml.safe_dump(values["dashboards"], sort_keys=False)

    kustomization_doc = yaml.safe_load(kustomization.read_text(encoding="utf-8"))
    assert "./configmap-grafana-nebius-kubernetes-dashboards.yaml" in kustomization_doc["resources"]
    assert kustomization_doc["resources"].index(
        "./configmap-grafana-nebius-kubernetes-dashboards.yaml"
    ) < kustomization_doc["resources"].index("./helmrelease-observability-grafana.yaml")


def test_render_project_materializes_observability_agent_scrape_config(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    payload["deploy"]["targets"][0]["observability"]["enabled"] = True
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    release = _target_flux_dir(paths) / "helmrelease-observability-nebius-observability-agent.yaml"
    release_doc = yaml.safe_load(release.read_text(encoding="utf-8"))
    metrics = release_doc["spec"]["values"]["config"]["metrics"]
    assert metrics["collectK8sClusterMetrics"] is False
    assert [item["job_name"] for item in metrics["additionalTargets"]] == [
        "cxcli-kubernetes-apiservers",
        "cxcli-kubernetes-nodes",
        "cxcli-kubernetes-nodes-cadvisor",
        "cxcli-hubble",
    ]


def test_render_native_mysterybox_eso_objects_in_external_secrets_release(
    tmp_path: Path,
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.PORTABLE)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s", "mysterybox"},
        selected_apps=set(),
    )
    _infra_component_row(payload, "mysterybox")["inputs"] = {
        "parent_id": "project-456",
        "secrets": [
            {
                "name": "app-config",
                "version_id": "mbsecver-e00app",
                "eso_version_policy": "manual-version-pinning",
                "payload": {
                    "DB_USERNAME": {"type": "text"},
                    "DB_PASSWORD": {"type": "text"},
                },
            }
        ],
    }
    payload["deploy"]["targets"][0]["secrets"] = {
        "mysterybox": {
            "enabled": True,
            "allow_all_namespaces": False,
            "sync_namespaces": ["ns-1", "ns-2"],
            "refresh_interval": "1m",
        }
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    paths.flux_dir.mkdir(parents=True, exist_ok=True)

    materialize_mysterybox_eso_app_values(
        config,
        component_output_values={"mysterybox.secret_ids": {"app-config": "mbsec-e00app"}},
    )
    render_flux(
        config,
        paths,
        component_output_values={"mysterybox.secret_ids": {"app-config": "mbsec-e00app"}},
    )

    release_path = _target_flux_dir(paths) / "helmrelease-platform-external-secrets.yaml"
    release_doc = yaml.safe_load(release_path.read_text(encoding="utf-8"))
    assert release_doc["spec"]["install"] == {"createNamespace": True}
    assert "upgrade" not in release_doc["spec"]

    assert release_doc["metadata"] == {
        "name": "external-secrets",
        "namespace": "external-secrets",
    }
    values = release_doc["spec"]["values"]
    assert (
        values["global"]["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"][0]["matchExpressions"][0]["key"]
        == "nebius.com/gpu"
    )
    assert "extraObjects" not in values
    kustomization_doc = yaml.safe_load(
        (_target_flux_dir(paths) / "kustomization.yaml").read_text(encoding="utf-8")
    )
    assert "./post-flux-mysterybox-eso.yaml" not in kustomization_doc["resources"]
    extra_objects = _load_mysterybox_eso_post_flux_objects(paths)
    assert not any(item["kind"] == "Secret" for item in extra_objects)
    store = next(item for item in extra_objects if item["kind"] == "ClusterSecretStore")
    assert store["metadata"]["name"] == "nebius-mysterybox-shared"
    assert store["spec"]["conditions"] == [{"namespaces": ["ns-1", "ns-2"]}]
    provider = store["spec"]["provider"]["nebiusmysterybox"]
    assert provider["apiDomain"] == "api.nebius.cloud:443"
    assert "caProvider" not in provider
    assert provider["auth"]["serviceAccountCredsSecretRef"] == {
        "name": "nebius-mysterybox-shared-creds",
        "namespace": "external-secrets",
        "key": "credentials.json",
    }
    app_secret = next(
        item
        for item in extra_objects
        if item["kind"] == "ExternalSecret" and item["metadata"]["namespace"] == "ns-1"
    )
    assert app_secret["metadata"]["name"] == "app-config"
    assert app_secret["metadata"]["namespace"] == "ns-1"
    worker_secret = next(
        item
        for item in extra_objects
        if item["kind"] == "ExternalSecret" and item["metadata"]["namespace"] == "ns-2"
    )
    assert worker_secret["metadata"]["namespace"] == "ns-2"
    assert worker_secret["metadata"]["name"] == "app-config"
    assert app_secret["spec"]["refreshInterval"] == "1m"
    assert app_secret["spec"]["data"] == [
        {
            "secretKey": "DB_USERNAME",
            "remoteRef": {
                "key": "mbsec-e00app",
                "property": "DB_USERNAME",
                "version": "mbsecver-e00app",
            },
        },
        {
            "secretKey": "DB_PASSWORD",
            "remoteRef": {
                "key": "mbsec-e00app",
                "property": "DB_PASSWORD",
                "version": "mbsecver-e00app",
            },
        },
    ]
    assert "dataFrom" not in app_secret["spec"]
    assert worker_secret["spec"]["refreshInterval"] == "1m"
    assert worker_secret["spec"]["data"] == [
        {
            "secretKey": "DB_USERNAME",
            "remoteRef": {
                "key": "mbsec-e00app",
                "property": "DB_USERNAME",
                "version": "mbsecver-e00app",
            },
        },
        {
            "secretKey": "DB_PASSWORD",
            "remoteRef": {
                "key": "mbsec-e00app",
                "property": "DB_PASSWORD",
                "version": "mbsecver-e00app",
            },
        },
    ]
    assert "dataFrom" not in worker_secret["spec"]


def test_render_native_mysterybox_eso_defaults_to_cluster_wide_store(
    tmp_path: Path,
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.PORTABLE)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s", "mysterybox"},
        selected_apps=set(),
    )
    _infra_component_row(payload, "mysterybox")["inputs"] = {
        "parent_id": "project-456",
        "secrets": [
            {
                "name": "app-config",
                "version_id": "n/a",
                "payload": {"DB_PASSWORD": {"type": "text"}},
            }
        ],
    }
    payload["deploy"]["targets"][0]["secrets"] = {
        "mysterybox": {
            "enabled": True,
            "sync_namespaces": ["default", "app"],
        }
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    paths.flux_dir.mkdir(parents=True, exist_ok=True)

    materialize_mysterybox_eso_app_values(
        config,
        component_output_values={"mysterybox.secret_ids": {"app-config": "mbsec-e00app"}},
    )
    render_flux(
        config,
        paths,
        component_output_values={"mysterybox.secret_ids": {"app-config": "mbsec-e00app"}},
    )

    release_path = _target_flux_dir(paths) / "helmrelease-platform-external-secrets.yaml"
    release_doc = yaml.safe_load(release_path.read_text(encoding="utf-8"))
    assert "extraObjects" not in release_doc["spec"]["values"]
    kustomization_doc = yaml.safe_load(
        (_target_flux_dir(paths) / "kustomization.yaml").read_text(encoding="utf-8")
    )
    assert "./post-flux-mysterybox-eso.yaml" not in kustomization_doc["resources"]
    extra_objects = _load_mysterybox_eso_post_flux_objects(paths)
    store = next(item for item in extra_objects if item["kind"] == "ClusterSecretStore")
    namespaces = {item["metadata"]["name"] for item in extra_objects if item["kind"] == "Namespace"}

    assert "conditions" not in store["spec"]
    assert namespaces == {"app"}
    external_secret_namespaces = {
        item["metadata"]["namespace"] for item in extra_objects if item["kind"] == "ExternalSecret"
    }
    assert external_secret_namespaces == {"default", "app"}


def test_render_external_secrets_without_managed_mysterybox_keeps_helm_wait(
    tmp_path: Path,
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.PORTABLE)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s"},
        selected_apps={"external-secrets"},
    )
    external_secrets = next(
        item for item in payload["apps"]["charts"] if item["id"] == "external-secrets"
    )
    external_secrets["values"] = {
        "extraObjects": [
            {
                "apiVersion": "external-secrets.io/v1",
                "kind": "ExternalSecret",
                "metadata": {"name": "operator-owned", "namespace": "app"},
                "spec": {
                    "secretStoreRef": {"kind": "ClusterSecretStore", "name": "operator-owned"},
                    "target": {"name": "operator-owned"},
                    "dataFrom": [{"extract": {"key": "operator-owned"}}],
                },
            }
        ]
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    paths.flux_dir.mkdir(parents=True, exist_ok=True)

    render_flux(config, paths)

    release_path = _target_flux_dir(paths) / "helmrelease-platform-external-secrets.yaml"
    release_doc = yaml.safe_load(release_path.read_text(encoding="utf-8"))

    assert release_doc["spec"]["install"] == {"createNamespace": True}
    assert "upgrade" not in release_doc["spec"]


def test_render_dynamic_oci_chart_uses_catalog_chart_name_when_id_differs(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    runtime_payload = _starter_payload(selected_infra=set(), selected_apps=set())
    dynamic_payload = to_dynamic_payload(runtime_payload)
    dynamic_payload["apps"]["charts"] = [
        {
            "id": "nvidia-network-operator",
            "instance_id": "nvidia-network-operator",
            "group": "platform",
            "enabled": True,
            "repo": "oci://cr.eu-north1.nebius.cloud/marketplace/nebius/nvidia-network-operator/chart/network-operator",
            "version": "25.7.0",
            "namespace": "nvidia-network-operator",
            "release-name": "network-operator",
            "values": {},
        }
    ]

    config_path.write_text(yaml.safe_dump(dynamic_payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    repo_sources = paths.flux_dir / "helm-repositories.yaml"
    release = paths.flux_dir / "helmrelease-platform-network-operator.yaml"
    assert repo_sources.exists()
    assert release.exists()

    repo_docs = [
        doc
        for doc in yaml.safe_load_all(repo_sources.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    helm_repo_doc = next(doc for doc in repo_docs if doc.get("kind") == "HelmRepository")
    assert helm_repo_doc["spec"]["type"] == "oci"
    assert (
        helm_repo_doc["spec"]["url"]
        == "oci://cr.eu-north1.nebius.cloud/marketplace/nebius/nvidia-network-operator/chart"
    )

    release_doc = yaml.safe_load(release.read_text(encoding="utf-8"))
    chart_spec = release_doc["spec"]["chart"]["spec"]
    assert chart_spec["sourceRef"]["kind"] == "HelmRepository"
    assert chart_spec["chart"] == "network-operator"
    assert chart_spec["version"] == "25.7.0"


def test_render_uses_component_source_release_timeout_for_helm_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            _catalog(
                apps={
                    "demo-app": {
                        "source": _portable_chart_source(
                            repo="https://example.invalid/charts",
                            chart="demo-app",
                            version="1.0.0",
                        ),
                        "release": {
                            "namespace": "demo",
                            "name": "demo-app",
                            "timeout": "10m",
                        },
                    }
                }
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _set_catalog_override(sources_file, source_profile=SourceProfile.PORTABLE)
    monkeypatch.chdir(tmp_path)

    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _starter_payload(selected_infra=set(), selected_apps={"demo-app"})
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.PORTABLE)

    release_doc = yaml.safe_load(
        (paths.flux_dir / "helmrelease-workloads-demo-app.yaml").read_text(encoding="utf-8")
    )
    assert release_doc["spec"]["timeout"] == "10m"


def test_render_uses_global_flux_release_timeout_when_chart_omits_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            _catalog(
                apps={
                    "demo-app": {
                        "source": _portable_chart_source(
                            repo="https://example.invalid/charts",
                            chart="demo-app",
                            version="1.0.0",
                        ),
                        "release": {
                            "namespace": "demo",
                            "name": "demo-app",
                        },
                    }
                },
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    sources_file.with_name("component_cli_settings.yaml").write_text(
        yaml.safe_dump(
            {
                "cli": {
                    "flux": {
                        "version": "v2.8.0",
                        "release_timeout": "15m",
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _set_catalog_override(sources_file, source_profile=SourceProfile.PORTABLE)
    monkeypatch.chdir(tmp_path)

    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _starter_payload(selected_infra=set(), selected_apps={"demo-app"})
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.PORTABLE)

    release_doc = yaml.safe_load(
        (paths.flux_dir / "helmrelease-workloads-demo-app.yaml").read_text(encoding="utf-8")
    )
    assert release_doc["spec"]["timeout"] == "15m"


def test_render_materializes_nebius_gpu_operator_driver_crd_override_for_nebius_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.LOCAL)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"nvidia-gpu-operator"})
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs.update(
        {
            "subnet_id": "subnet-abc123",
            "cluster_name": "cluster1",
            "cpu_nodes_platform": "cpu-d3",
            "cpu_nodes_preset": "4vcpu-16gb",
            "gpu_enabled": True,
            "gpu_nodes_platform": "gpu-b300-sxm",
            "gpu_nodes_preset": "8gpu-192vcpu-2768gb",
            "gpu_stack_source": "nebius_image",
        }
    )
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
            ModuleVariable(name="gpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="gpu_nodes_preset", required=False, type_hint="string"),
            ModuleVariable(name="gpu_stack_source", required=False, type_hint="string"),
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
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    release_doc = yaml.safe_load(
        (_target_flux_dir(paths) / "helmrelease-platform-gpu-operator.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert release_doc["spec"]["values"]["driver"]["enabled"] is False
    assert release_doc["spec"]["values"]["toolkit"]["enabled"] is False
    assert release_doc["spec"]["values"]["driver"]["nvidiaDriverCRD"]["enabled"] is False
    assert release_doc["spec"]["values"]["node-feature-discovery"]["worker"]["affinity"][
        "nodeAffinity"
    ]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][
        0
    ] == {
        "key": "nebius.com/gpu",
        "operator": "In",
        "values": ["true"],
    }


def test_render_materializes_driverful_rdma_policy_for_nebius_gpu_clusters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.LOCAL)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s"},
        selected_apps={"nvidia-network-operator", "nvidia-gpu-operator"},
    )
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs.update(
        {
            "subnet_id": "subnet-abc123",
            "cluster_name": "cluster1",
            "cpu_nodes_platform": "cpu-d3",
            "cpu_nodes_preset": "4vcpu-16gb",
            "gpu_enabled": True,
            "gpu_nodes_platform": "gpu-b300-sxm",
            "gpu_nodes_preset": "8gpu-192vcpu-2768gb",
            "gpu_stack_source": "nebius_image",
            "infiniband_fabric": "fabric-1",
        }
    )
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
            ModuleVariable(name="gpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="gpu_nodes_preset", required=False, type_hint="string"),
            ModuleVariable(name="gpu_stack_source", required=False, type_hint="string"),
            ModuleVariable(name="infiniband_fabric", required=False, type_hint="string"),
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
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    target_flux_dir = _target_flux_dir(paths)
    network_release_doc = yaml.safe_load(
        (target_flux_dir / "helmrelease-platform-network-operator.yaml").read_text(encoding="utf-8")
    )
    gpu_release_doc = yaml.safe_load(
        (target_flux_dir / "helmrelease-platform-gpu-operator.yaml").read_text(encoding="utf-8")
    )

    network_values = network_release_doc["spec"]["values"]
    assert network_values["operator"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0] == {
        "key": "nebius.com/gpu",
        "operator": "NotIn",
        "values": ["true"],
    }
    assert network_values["operator"]["ofedDriver"]["deploy"] is False
    assert network_values["nfd"]["enabled"] is True
    assert network_values["nfd"]["deployNodeFeatureRules"] is True
    assert (
        network_values["node-feature-discovery"]["worker"]["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"][0]["matchExpressions"][0]["operator"]
        == "In"
    )
    assert (
        network_values["node-feature-discovery"]["worker"]["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"][0]["matchExpressions"][0]["key"]
        == "nebius.com/driverful"
    )
    assert network_values["node-feature-discovery"]["worker"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"] == ["true"]
    assert (
        network_values["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
            "nodeSelectorTerms"
        ][0]["matchExpressions"][0]["key"]
        == "feature.node.kubernetes.io/pci-15b3.present"
    )
    assert (
        network_values["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
            "nodeSelectorTerms"
        ][0]["matchExpressions"][0]["operator"]
        == "In"
    )
    assert network_values["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ][0]["matchExpressions"][0]["values"] == ["true"]
    network_patches = network_release_doc["spec"]["postRenderers"][0]["kustomize"]["patches"]
    assert network_patches[0]["target"]["kind"] == "NicClusterPolicy"
    assert '"periodicUpdateInterval": 0' in network_patches[0]["patch"]
    assert '"resourceName": "shared_device"' in network_patches[0]["patch"]
    assert '"linkTypes": ["infiniband"]' in network_patches[0]["patch"]

    gpu_values = gpu_release_doc["spec"]["values"]
    assert gpu_values["driver"]["enabled"] is False
    assert gpu_values["toolkit"]["enabled"] is False
    assert gpu_values["nfd"]["enabled"] is False
    assert "node-feature-discovery" not in gpu_values
    assert gpu_release_doc["spec"]["dependsOn"] == [
        {"name": "network-operator", "namespace": "nvidia-network-operator"}
    ]


def test_render_disables_gpu_operator_nfd_for_operator_managed_b200_network_operator_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.LOCAL)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s"},
        selected_apps={"nvidia-network-operator", "nvidia-gpu-operator"},
    )
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs.update(
        {
            "subnet_id": "subnet-abc123",
            "cluster_name": "cluster1",
            "cpu_nodes_platform": "cpu-d3",
            "cpu_nodes_preset": "4vcpu-16gb",
            "gpu_enabled": True,
            "gpu_nodes_platform": "gpu-b200-sxm",
            "gpu_nodes_preset": "8gpu-192vcpu-2768gb",
            "gpu_stack_source": "operator_managed",
        }
    )
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
            ModuleVariable(name="gpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="gpu_nodes_preset", required=False, type_hint="string"),
            ModuleVariable(name="gpu_stack_source", required=False, type_hint="string"),
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
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    target_flux_dir = _target_flux_dir(paths)
    network_release_doc = yaml.safe_load(
        (target_flux_dir / "helmrelease-platform-network-operator.yaml").read_text(encoding="utf-8")
    )
    gpu_release_doc = yaml.safe_load(
        (target_flux_dir / "helmrelease-platform-gpu-operator.yaml").read_text(encoding="utf-8")
    )
    network_values = network_release_doc["spec"]["values"]
    assert network_values["operator"]["ofedDriver"]["deploy"] is True
    assert network_values["nfd"]["enabled"] is True
    assert network_values["nfd"]["deployNodeFeatureRules"] is True
    assert gpu_release_doc["spec"]["values"]["nfd"]["enabled"] is False


def test_render_materializes_operator_managed_rdma_policy_for_gpu_cluster_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.LOCAL)
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s"},
        selected_apps={"nvidia-network-operator", "nvidia-gpu-operator"},
    )
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs.update(
        {
            "subnet_id": "subnet-abc123",
            "cluster_name": "cluster1",
            "cpu_nodes_platform": "cpu-d3",
            "cpu_nodes_preset": "4vcpu-16gb",
            "gpu_enabled": True,
            "gpu_nodes_platform": "gpu-b300-sxm",
            "gpu_nodes_preset": "8gpu-192vcpu-2768gb",
            "gpu_stack_source": "operator_managed",
            "infiniband_fabric": "fabric-1",
        }
    )
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
            ModuleVariable(name="gpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="gpu_nodes_preset", required=False, type_hint="string"),
            ModuleVariable(name="gpu_stack_source", required=False, type_hint="string"),
            ModuleVariable(name="infiniband_fabric", required=False, type_hint="string"),
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
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    target_flux_dir = _target_flux_dir(paths)
    network_release_doc = yaml.safe_load(
        (target_flux_dir / "helmrelease-platform-network-operator.yaml").read_text(encoding="utf-8")
    )
    gpu_release_doc = yaml.safe_load(
        (target_flux_dir / "helmrelease-platform-gpu-operator.yaml").read_text(encoding="utf-8")
    )

    network_values = network_release_doc["spec"]["values"]
    assert network_values["operator"]["ofedDriver"]["deploy"] is True
    assert network_values["nfd"]["enabled"] is True
    assert network_values["nfd"]["deployNodeFeatureRules"] is True
    network_patches = network_release_doc["spec"]["postRenderers"][0]["kustomize"]["patches"]
    assert network_patches[0]["target"]["kind"] == "NicClusterPolicy"
    assert '"periodicUpdateInterval": 0' in network_patches[0]["patch"]
    assert '"resourceName": "shared_device"' in network_patches[0]["patch"]
    assert '"linkTypes": ["infiniband"]' in network_patches[0]["patch"]

    gpu_values = gpu_release_doc["spec"]["values"]
    assert gpu_values["driver"]["enabled"] is True
    assert gpu_values["toolkit"]["enabled"] is True
    assert gpu_values["nfd"]["enabled"] is False


def test_render_removes_stale_legacy_nested_flux_layout(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"n8n"})
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    # Simulate stale legacy layout from previous versions.
    (paths.flux_dir / "apps").mkdir(parents=True, exist_ok=True)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    assert not (paths.flux_dir / "apps").exists()
    assert (_target_flux_dir(paths) / "kustomization.yaml").exists()


def test_render_rejects_unknown_custom_module_input(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s"},
        selected_apps={"nvidia-gpu-operator"},
    )
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["inputs"] = {
        "parent_id": "project-456",
        "cluster_name": "demo-cluster",
        "subnet_id": "subnet-123",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    with pytest.raises(ValueError, match="input 'ssh_public_key' is not declared by module"):
        render_project(config, paths, source_profile=SourceProfile.LOCAL)


def test_render_ignores_declared_mk8s_gpu_validation_helper_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s"},
        selected_apps={"nvidia-gpu-operator"},
    )
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["inputs"] = {
        "parent_id": "project-456",
        "cluster_name": "demo-cluster",
        "subnet_id": "subnet-123",
        "cpu_nodes_platform": "cpu-d3",
        "cpu_nodes_preset": "4vcpu-16gb",
        "gpu_enabled": True,
        "gpu_nodes_platform": "gpu-h100-sxm",
        "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
    }
    payload["deploy"] = {
        "targets": [
            {
                "instance_id": "mk8s",
                "validations": {
                    "mk8s_gpu": {
                        "operator_readiness": {"enabled": False},
                        "gpu_visibility": {"enabled": True, "max_nodes": 2},
                    }
                },
            }
        ],
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=False, type_hint="string"),
            ModuleVariable(name="cluster_name", required=False, type_hint="string"),
            ModuleVariable(name="subnet_id", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_count", required=False, type_hint="number"),
            ModuleVariable(name="cpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="cpu_nodes_preset", required=False, type_hint="string"),
            ModuleVariable(name="gpu_enabled", required=False, type_hint="bool"),
            ModuleVariable(name="gpu_stack_source", required=False, type_hint="string"),
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
            ModuleVariable(name="gpu_nodes_platform", required=False, type_hint="string"),
            ModuleVariable(name="gpu_nodes_preset", required=False, type_hint="string"),
        ),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    tfvars = (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    assert "gpu_validation_overrides" not in tfvars


def test_render_uses_materialized_shared_admin_ssh_username_for_wireguard_jumphost(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    _set_catalog_override(
        _catalog_with_shared_admin_ssh(tmp_path, user_name="adminuser"),
        source_profile=SourceProfile.LOCAL,
    )
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"wireguard-jumphost"}, selected_apps=set())
    jumphost = _infra_component_row(payload, "wireguard-jumphost")
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "region": "eu-north1",
        "subnet_id": "subnet-123",
        "name": "wg-jumphost",
        "ssh_user_name": "adminuser",
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")

    assert 'module "wireguard_jumphost" {' in main_tf
    assert "ssh_user_name = var.wireguard_jumphost_ssh_user_name" in main_tf
    assert '"wireguard_jumphost_ssh_user_name": "adminuser"' in tfvars
    assert "wireguard_jumphost_ssh_public_key" not in tfvars


def test_render_uses_materialized_shared_admin_ssh_username_for_ssh_jumphost(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    _set_catalog_override(
        _catalog_with_shared_admin_ssh(tmp_path, user_name="adminuser"),
        source_profile=SourceProfile.LOCAL,
    )
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"ssh-jumphost"}, selected_apps=set())
    jumphost = _infra_component_row(payload, "ssh-jumphost")
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "region": "eu-north1",
        "subnet_id": "subnet-123",
        "name": "ssh-jumphost",
        "ssh_user_name": "adminuser",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "allowed_cidrs": ["203.0.113.10/32"],
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)
    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")

    assert 'module "ssh_jumphost" {' in main_tf
    assert "ssh_user_name = var.ssh_jumphost_ssh_user_name" in main_tf
    assert '"ssh_jumphost_ssh_user_name": "adminuser"' in tfvars


def test_render_uses_materialized_shared_defaults_for_app_chart_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            _catalog(
                shared={
                    "admin_ssh": {
                        "user_name": "adminuser",
                        "public_key": _VALID_ED25519_PUBLIC_KEY,
                    }
                },
                apps={
                    "demo-app": {
                        "source": _portable_chart_source(
                            repo="https://example.invalid/charts",
                            chart="demo-app",
                            version="1.0.0",
                        ),
                        "release": {
                            "namespace": "demo",
                            "name": "demo-app",
                        },
                        "defaults": {
                            "values.admin.sshUser": "shared.admin_ssh.user_name",
                        },
                    }
                },
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _set_catalog_override(sources_file, source_profile=SourceProfile.LOCAL)
    monkeypatch.chdir(tmp_path)

    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": True, "email": "ops@example.com"},
        },
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "demo-app",
                    "instance_id": "demo-app",
                    "group": "workloads",
                    "enabled": True,
                    "repo": "https://example.invalid/charts",
                    "version": "1.0.0",
                    "namespace": "demo",
                    "release-name": "demo-app",
                    "values": {"admin": {"sshUser": "adminuser"}},
                }
            ]
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    release_doc = yaml.safe_load(
        (paths.flux_dir / "helmrelease-workloads-demo-app.yaml").read_text(encoding="utf-8")
    )
    assert release_doc["spec"]["values"]["admin"]["sshUser"] == "adminuser"


def test_render_supports_infra_input_binding_from_component_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer_dir = tmp_path / "modules" / "producer"
    producer_dir.mkdir(parents=True, exist_ok=True)
    (producer_dir / "main.tf").write_text(
        'output "instance_id" { value = "instance-123" }\n', encoding="utf-8"
    )

    consumer_dir = tmp_path / "modules" / "consumer"
    consumer_dir.mkdir(parents=True, exist_ok=True)
    (consumer_dir / "variables.tf").write_text(
        'variable "upstream_id" { type = string }\n', encoding="utf-8"
    )
    (consumer_dir / "main.tf").write_text("terraform {}\n", encoding="utf-8")

    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            _catalog(
                infra={
                    "producer": {
                        "source": {
                            "portable": "git::https://github.com/example/infra.git//modules/producer?ref=v1.2.3",
                            "local": str(producer_dir),
                        }
                    },
                    "consumer": {
                        "source": {
                            "portable": "git::https://github.com/example/infra.git//modules/consumer?ref=v1.2.3",
                            "local": str(consumer_dir),
                        },
                        "input": {"inputs.upstream_id": "producer.instance_id"},
                    },
                }
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _set_catalog_override(sources_file, source_profile=SourceProfile.LOCAL)
    monkeypatch.chdir(tmp_path)

    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": True, "email": "ops@example.com"},
        },
        "infra": {
            "components": [
                {
                    "id": "producer",
                    "instance_id": "producer",
                    "enabled": True,
                    "source": str(producer_dir),
                    "inputs": {},
                },
                {
                    "id": "consumer",
                    "instance_id": "consumer",
                    "enabled": True,
                    "source": str(consumer_dir),
                    "inputs": {},
                },
            ]
        },
        "apps": {"charts": []},
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    outputs_tf = (paths.infra_dir / "outputs.tf").read_text(encoding="utf-8")

    assert 'module "consumer" {' in main_tf
    assert "upstream_id = module.producer.instance_id" in main_tf
    assert 'output "producer_instance_id" {' in outputs_tf
    assert "value       = module.producer.instance_id" in outputs_tf


def test_render_supports_app_input_binding_from_component_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mk8s_dir = tmp_path / "modules" / "mk8s"
    mk8s_dir.mkdir(parents=True, exist_ok=True)
    (mk8s_dir / "main.tf").write_text(
        'output "cluster_id" { value = "cluster-u123" }\n', encoding="utf-8"
    )

    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            _catalog(
                infra={
                    "mk8s": {
                        "source": {
                            "portable": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
                            "local": str(mk8s_dir),
                        },
                    }
                },
                apps={
                    "demo-app": {
                        "source": _portable_chart_source(
                            repo="https://example.invalid/charts",
                            chart="demo-app",
                            version="1.0.0",
                        ),
                        "release": {
                            "namespace": "demo",
                            "name": "demo-app",
                        },
                        "input": {
                            "values.global.clusterId": "mk8s.cluster_id",
                        },
                    }
                },
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _set_catalog_override(sources_file, source_profile=SourceProfile.LOCAL)
    monkeypatch.chdir(tmp_path)

    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": True, "email": "ops@example.com"},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s-blue",
                    "enabled": True,
                    "source": str(mk8s_dir),
                    "inputs": {},
                },
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "demo-app",
                    "instance_id": "mk8s-blue",
                    "group": "workloads",
                    "enabled": True,
                    "repo": "https://example.invalid/charts",
                    "version": "1.0.0",
                    "namespace": "demo",
                    "release-name": "demo-app",
                    "values": {},
                }
            ]
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(
        config,
        paths,
        component_output_values={"mk8s-blue.cluster_id": "cluster-u123"},
        source_profile=SourceProfile.LOCAL,
    )

    release_doc = yaml.safe_load(
        (_target_flux_dir(paths, "mk8s-blue") / "helmrelease-workloads-demo-app.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert release_doc["spec"]["values"]["global"]["clusterId"] == "cluster-u123"


def test_render_supports_explicit_instance_qualified_app_input_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer_dir = tmp_path / "modules" / "producer"
    producer_dir.mkdir(parents=True, exist_ok=True)
    (producer_dir / "main.tf").write_text(
        'output "instance_id" { value = "instance-123" }\n', encoding="utf-8"
    )

    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            _catalog(
                infra={
                    "producer": {
                        "source": {
                            "portable": "git::https://github.com/example/infra.git//modules/producer?ref=v1.2.3",
                            "local": str(producer_dir),
                        }
                    }
                },
                apps={
                    "demo-app": {
                        "source": _portable_chart_source(
                            repo="https://example.invalid/charts",
                            chart="demo-app",
                            version="1.0.0",
                        ),
                        "release": {
                            "namespace": "demo",
                            "name": "demo-app",
                        },
                        "input": {
                            "values.global.upstreamId": "producer@producer-blue.instance_id",
                        },
                    }
                },
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _set_catalog_override(sources_file, source_profile=SourceProfile.LOCAL)
    monkeypatch.chdir(tmp_path)

    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": True, "email": "ops@example.com"},
        },
        "infra": {
            "components": [
                {
                    "id": "producer",
                    "instance_id": "producer-red",
                    "enabled": True,
                    "source": str(producer_dir),
                    "inputs": {},
                },
                {
                    "id": "producer",
                    "instance_id": "producer-blue",
                    "enabled": True,
                    "source": str(producer_dir),
                    "inputs": {},
                },
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "demo-app",
                    "instance_id": "demo-app",
                    "group": "workloads",
                    "enabled": True,
                    "repo": "https://example.invalid/charts",
                    "version": "1.0.0",
                    "namespace": "demo",
                    "release-name": "demo-app",
                    "values": {},
                }
            ]
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(
        config,
        paths,
        component_output_values={"producer-blue.instance_id": "instance-blue"},
        source_profile=SourceProfile.LOCAL,
    )

    release_doc = yaml.safe_load(
        (paths.flux_dir / "helmrelease-workloads-demo-app.yaml").read_text(encoding="utf-8")
    )
    assert release_doc["spec"]["values"]["global"]["upstreamId"] == "instance-blue"


def test_render_uses_component_source_defaults_when_config_omits_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_dir = tmp_path / "modules" / "demo-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "variables.tf").write_text(
        'variable "cluster_name" { type = string }\nvariable "cpu_nodes_count" { type = number }\n',
        encoding="utf-8",
    )
    (module_dir / "main.tf").write_text("terraform {}\n", encoding="utf-8")

    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            _catalog(
                infra={
                    "demo-module": {
                        "source": {
                            "portable": "git::https://github.com/example/infra.git//modules/demo-module?ref=v1.2.3",
                            "local": str(module_dir),
                        },
                        "defaults": {
                            "inputs.cluster_name": "demo-cluster",
                            "inputs.cpu_nodes_count": 3,
                        },
                    }
                },
                apps={
                    "demo-app": {
                        "source": _portable_chart_source(
                            repo="https://example.invalid/charts",
                            chart="demo-app",
                            version="1.0.0",
                        ),
                        "release": {
                            "namespace": "demo",
                            "name": "demo-app",
                        },
                        "defaults": {
                            "values.replicaCount": 2,
                            "values.image.tag": "stable",
                        },
                    }
                },
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _set_catalog_override(sources_file, source_profile=SourceProfile.LOCAL)
    monkeypatch.chdir(tmp_path)

    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": True, "email": "ops@example.com"},
        },
        "infra": {
            "components": [
                {
                    "id": "demo-module",
                    "instance_id": "demo-module",
                    "enabled": True,
                    "source": str(module_dir),
                    "inputs": {},
                },
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "demo-app",
                    "instance_id": "demo-app",
                    "group": "workloads",
                    "enabled": True,
                    "repo": "https://example.invalid/charts",
                    "version": "1.0.0",
                    "namespace": "demo",
                    "release-name": "demo-app",
                    "values": {},
                }
            ]
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    tfvars = (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    assert '"demo_module_cluster_name": "demo-cluster"' in tfvars
    assert '"demo_module_cpu_nodes_count": 3' in tfvars

    release_doc = yaml.safe_load(
        (paths.flux_dir / "helmrelease-workloads-demo-app.yaml").read_text(encoding="utf-8")
    )
    assert release_doc["spec"]["values"]["replicaCount"] == 2
    assert release_doc["spec"]["values"]["image"]["tag"] == "stable"
