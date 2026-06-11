from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest
import yaml

import nebius_cxcli.component_sources as component_sources
import nebius_cxcli.flux_render as flux_render_module
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
from nebius_cxcli.deploy_targets import flux_target_dir, strip_app_chart_target_refs
from nebius_cxcli.flux_render import (
    _build_local_helm_chart_dependencies,
    _inject_local_chart_namespace,
    _stage_local_helm_chart,
    render_flux,
)
from nebius_cxcli.infra_render import _build_module_plans, _render_module_block
from nebius_cxcli.mk8s_gpu import ensure_mk8s_gpu_app_rows, materialize_mk8s_gpu_app_values
from nebius_cxcli.mysterybox_eso import materialize_mysterybox_eso_app_values
from nebius_cxcli.nfs_csi import ensure_nfs_csi_app_rows
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


@pytest.fixture(scope="module", autouse=True)
def _cache_local_soperator_chart_dependencies(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Avoid rebuilding the same local Soperator chart dependencies for every render test."""
    monkeypatch = pytest.MonkeyPatch()
    original_stage = flux_render_module._stage_local_helm_chart
    original_build = flux_render_module._build_local_helm_chart_dependencies
    cache_root = tmp_path_factory.mktemp("soperator-chart-staging")
    staged_cache: dict[Path, str] = {}
    prepared_staged_paths: set[Path] = set()

    def _is_soperator_chart(chart_path: str) -> bool:
        source = Path(chart_path).expanduser().resolve()
        return source.name == "soperator" and (source / "Chart.yaml").is_file()

    def _stage_local_helm_chart(chart_path: str, staging_root: Path) -> str:
        if not _is_soperator_chart(chart_path):
            return original_stage(chart_path, staging_root)
        source = Path(chart_path).expanduser().resolve()
        staged = staged_cache.get(source)
        if staged is None:
            staged = original_stage(str(source), cache_root)
            original_build(staged)
            staged_cache[source] = staged
            prepared_staged_paths.add(Path(staged).resolve())
        return staged

    def _build_local_helm_chart_dependencies(chart_path: str) -> None:
        if Path(chart_path).expanduser().resolve() in prepared_staged_paths:
            return
        original_build(chart_path)

    monkeypatch.setattr(
        flux_render_module,
        "_stage_local_helm_chart",
        _stage_local_helm_chart,
    )
    monkeypatch.setattr(
        flux_render_module,
        "_build_local_helm_chart_dependencies",
        _build_local_helm_chart_dependencies,
    )
    try:
        yield
    finally:
        monkeypatch.undo()


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


def _write_minimal_mk8s_module(base: Path) -> Path:
    module_dir = base / "modules" / "mk8s"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text(
        'output "cluster_id" { value = "mk8scluster-test" }\n',
        encoding="utf-8",
    )
    return module_dir


def _minimal_mk8s_catalog_entry(module_dir: Path) -> dict[str, object]:
    return {
        "source": {
            "portable": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
            "local": str(module_dir),
        },
        "defaults": {
            "inputs.cluster.cluster_name": "mk8s",
        },
    }


def _mk8s_module_variables() -> tuple[ModuleVariable, ...]:
    return (
        ModuleVariable(name="cluster", required=True, type_hint="object"),
        ModuleVariable(name="node_groups", required=True, type_hint="map(object)"),
        ModuleVariable(name="gpu_clusters", required=False, type_hint="map(object)"),
        ModuleVariable(name="node_group_defaults", required=False, type_hint="object"),
    )


def _mk8s_inputs(
    *,
    cluster_name: str = "mk8s",
    subnet_id: str = "subnet-abc123",
    cpu: bool = True,
    gpu: bool = False,
    gpu_platform: str = "gpu-b300-sxm",
    gpu_preset: str = "8gpu-192vcpu-2768gb",
    gpu_stack_source: str = "nebius_image",
    infiniband_fabric: str = "",
) -> dict[str, object]:
    inputs: dict[str, object] = {
        "cluster": {
            "parent_id": "project-456",
            "cluster_name": cluster_name,
            "network_id": "vpcnetwork-123",
            "subnet_id": subnet_id,
            "k8s_version": "1.31",
            "public_endpoint": True,
        },
        "node_groups": {},
    }
    node_groups = inputs["node_groups"]
    assert isinstance(node_groups, dict)
    if cpu:
        node_groups["cpu"] = {
            "node_count": 1,
            "gpu": False,
            "platform": "cpu-d3",
            "preset": "4vcpu-16gb",
        }
    if gpu:
        gpu_group: dict[str, object] = {
            "node_count": 1,
            "gpu": True,
            "platform": gpu_platform,
            "preset": gpu_preset,
            "gpu_stack_source": gpu_stack_source,
        }
        if infiniband_fabric:
            gpu_group["gpu_cluster_key"] = "workers"
            inputs["gpu_clusters"] = {"workers": {"infiniband_fabric": infiniband_fabric}}
        node_groups["worker"] = gpu_group
    return inputs


def _retarget_enabled_apps(payload: dict, target_ref: str = "mk8s") -> None:
    charts = payload.get("apps", {}).get("charts", [])
    if not isinstance(charts, list):
        return
    for chart in charts:
        if isinstance(chart, dict) and chart.get("enabled") is True:
            chart["instance_id"] = target_ref


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


def _align_infra_resource_name(
    payload: dict,
    row: dict,
    resource_name: str,
    *,
    name_input: str | None = None,
) -> None:
    component_id = str(row.get("id", "")).strip().lower()
    old_instance_id = str(row.get("instance_id", "")).strip()
    row["instance_id"] = resource_name
    inputs = row.setdefault("inputs", {})
    assert isinstance(inputs, dict)
    if component_id == "mk8s":
        inputs.pop("cluster_name", None)
        cluster = inputs.setdefault("cluster", {})
        assert isinstance(cluster, dict)
        cluster["cluster_name"] = resource_name
    else:
        inputs[name_input or "name"] = resource_name
    if component_id != "mk8s" or not old_instance_id or old_instance_id == resource_name:
        return
    for chart in payload.get("apps", {}).get("charts", []):
        if not isinstance(chart, dict):
            continue
        if chart.get("instance_id") == old_instance_id:
            chart["instance_id"] = resource_name
        if chart.get("target_ref") == old_instance_id:
            chart["target_ref"] = resource_name
    for target in payload.get("deploy", {}).get("targets", []):
        if isinstance(target, dict) and target.get("instance_id") == old_instance_id:
            target["instance_id"] = resource_name


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


def _local_soperator_chart_path() -> Path:
    return Path(__file__).resolve().parents[3] / "helm-charts" / "soperator"


def _stage_soperator_chart_for_helm_template(tmp_path: Path) -> Path:
    return Path(
        flux_render_module._stage_local_helm_chart(
            str(_local_soperator_chart_path()),
            tmp_path / "soperator-chart-staging",
        )
    )


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
        mk8s_inputs.update(_mk8s_inputs(subnet_id="subnet-abc123", cpu=True, gpu=False))
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: _mk8s_module_variables(),
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
    assert 'version = ">= 0.6.8, < 0.7.0"' in versions_tf
    assert 'backend "s3"' in backend_tf
    assert "use_lockfile                = true" in backend_tf
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
    assert "cluster = var.mk8s_cluster" in main_tf
    assert "node_groups = var.mk8s_node_groups" in main_tf
    assert "inputs = jsondecode(" not in main_tf
    assert 'output "mk8s_cluster_id" {' in outputs_tf
    assert "value       = module.mk8s.cluster_id" in outputs_tf
    assert 'output "mk8s_cluster_ca_certificate" {' in outputs_tf
    assert "value       = module.mk8s.cluster_ca_certificate" in outputs_tf
    assert "sensitive   = true" in outputs_tf
    assert 'variable "mk8s_cluster" {' in variables_tf
    assert "type        = string" in variables_tf
    assert 'default     = "nebius_cxcli"' in variables_tf
    assert "sensitive   = true" in variables_tf
    assert '"subnet_id": "subnet-abc123"' in tfvars
    assert "mk8s_gpu_stack_source" not in tfvars
    assert "mk8s_gpu_nodes_boot_disk_type" not in tfvars
    assert '"service_cidrs": [' in tfvars
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
        mk8s_inputs.update(_mk8s_inputs(subnet_id="subnet-abc123", cpu=True, gpu=False))
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: _mk8s_module_variables(),
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


def test_render_passes_typed_mk8s_node_group_scale_and_preemptible_inputs(
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
    mk8s_inputs.update(_mk8s_inputs(subnet_id="subnet-abc123", cpu=True, gpu=True))
    node_groups = mk8s_inputs["node_groups"]
    assert isinstance(node_groups, dict)
    cpu_group = node_groups["cpu"]
    gpu_group = node_groups["worker"]
    assert isinstance(cpu_group, dict)
    assert isinstance(gpu_group, dict)
    cpu_group.pop("node_count", None)
    cpu_group["autoscaling"] = {
        "min_node_count": 1,
        "max_node_count": 3,
    }
    cpu_group["preemptible"] = True
    gpu_group["preemptible"] = True
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: _mk8s_module_variables(),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.PORTABLE)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert "node_groups = var.mk8s_node_groups" in main_tf
    assert tfvars["mk8s_node_groups"]["cpu"]["autoscaling"] == {
        "min_node_count": 1,
        "max_node_count": 3,
    }
    assert "node_count" not in tfvars["mk8s_node_groups"]["cpu"]
    assert tfvars["mk8s_node_groups"]["cpu"]["preemptible"] is True
    assert tfvars["mk8s_node_groups"]["worker"]["preemptible"] is True


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
            "network_id": "vpcnetwork-abc123",
            "subnet_id": "subnet-abc123",
            "platform": "gpu-h100-sxm",
            "preset": "1gpu-16vcpu-200gb",
            "source_image_family": "ubuntu24.04-cuda13.0",
            "ssh_user_name": "ubuntu",
            "preemptible_enabled": True,
            "recovery_policy": "FAIL",
        }
    )
    _align_infra_resource_name(payload, vm, "gpu-preemptible-vm")
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=False, type_hint="string"),
            ModuleVariable(name="name", required=False, type_hint="string"),
            ModuleVariable(name="network_id", required=False, type_hint="string"),
            ModuleVariable(name="subnet_id", required=False, type_hint="string"),
            ModuleVariable(name="platform", required=False, type_hint="string"),
            ModuleVariable(name="preset", required=False, type_hint="string"),
            ModuleVariable(name="source_image_family", required=False, type_hint="string"),
            ModuleVariable(name="ssh_user_name", required=False, type_hint="string"),
            ModuleVariable(name="preemptible_enabled", required=False, type_hint="bool"),
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

    assert "preemptible_enabled = var.gpu_preemptible_vm_preemptible_enabled" in main_tf
    assert "preemptible_priority" not in main_tf
    assert "recovery_policy = var.gpu_preemptible_vm_recovery_policy" in main_tf
    assert tfvars["gpu_preemptible_vm_preemptible_enabled"] is True
    assert "gpu_preemptible_vm_preemptible_priority" not in tfvars
    assert tfvars["gpu_preemptible_vm_recovery_policy"] == "FAIL"
    assert tfvars["gpu_preemptible_vm_network_id"] == "vpcnetwork-abc123"
    assert tfvars["gpu_preemptible_vm_subnet_id"] == "subnet-abc123"


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
            "instance_id": "clust1",
            "enabled": True,
            "inputs": _mk8s_inputs(cluster_name="clust1", cpu=True, gpu=False),
        },
        {
            "id": "mk8s",
            "instance_id": "clust2",
            "enabled": True,
            "inputs": _mk8s_inputs(cluster_name="clust2", cpu=True, gpu=False),
        },
    ]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: _mk8s_module_variables(),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.PORTABLE)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    outputs_tf = (paths.infra_dir / "outputs.tf").read_text(encoding="utf-8")

    assert 'module "clust1" {' in main_tf
    assert 'module "clust2" {' in main_tf
    assert 'output "clust1_cluster_id" {' in outputs_tf
    assert 'output "clust2_cluster_id" {' in outputs_tf


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
            "inputs": _mk8s_inputs(cluster_name="cluster1", cpu=True, gpu=False),
        },
        {
            "id": "mk8s",
            "instance_id": "cluster2",
            "enabled": True,
            "inputs": _mk8s_inputs(cluster_name="cluster2", cpu=True, gpu=False),
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
        lambda _source: _mk8s_module_variables(),
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
            "inputs": _mk8s_inputs(
                cluster_name="cluster1",
                cpu=True,
                gpu=True,
                gpu_platform="gpu-h100-sxm",
                gpu_preset="8gpu-128vcpu-1600gb",
            ),
        },
        {
            "id": "nfs",
            "instance_id": "nfs-cluster1",
            "enabled": True,
            "inputs": {"kubernetes_target_ref": "cluster1"},
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
            "nfs-cluster1.server_ip": "10.10.0.5",
            "nfs-cluster1.export_path": "/srv/nfs/home",
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
    patches = release_doc["spec"]["postRenderers"][0]["kustomize"]["patches"]
    certificate_patches = {
        patch["target"]["name"]: yaml.safe_load(patch["patch"])
        for patch in patches
        if patch["target"].get("group") == "cert-manager.io"
        and patch["target"].get("kind") == "Certificate"
    }
    assert set(certificate_patches) == {
        "soperator-serving-cert",
        "soperator-mariadb-operator-webhook-cert",
    }
    assert {
        patch["spec"]["privateKey"]["rotationPolicy"] for patch in certificate_patches.values()
    } == {"Always"}


def test_render_soperator_certificate_patch_preserves_explicit_rotation_policy(
    tmp_path: Path,
) -> None:
    _set_catalog_override(_local_catalog_path(), source_profile=SourceProfile.PORTABLE)
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"soperator"})
    soperator = next(chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator")
    soperator.setdefault("values", {}).setdefault("certManager", {})["privateKey"] = {
        "rotationPolicy": "Never"
    }
    _retarget_enabled_apps(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    render_project(load_config(config_path), paths, source_profile=SourceProfile.PORTABLE)

    release_doc = yaml.safe_load(
        (_target_flux_dir(paths) / "helmrelease-slurm-soperator.yaml").read_text(encoding="utf-8")
    )
    patches = release_doc["spec"]["postRenderers"][0]["kustomize"]["patches"]
    certificate_patches = {
        patch["target"]["name"]: yaml.safe_load(patch["patch"])
        for patch in patches
        if patch["target"].get("group") == "cert-manager.io"
        and patch["target"].get("kind") == "Certificate"
    }

    assert release_doc["spec"]["values"]["certManager"]["privateKey"]["rotationPolicy"] == "Never"
    assert (
        certificate_patches["soperator-serving-cert"]["spec"]["privateKey"]["rotationPolicy"]
        == "Never"
    )
    assert (
        certificate_patches["soperator-mariadb-operator-webhook-cert"]["spec"]["privateKey"][
            "rotationPolicy"
        ]
        == "Always"
    )


def test_render_binds_general_nfs_csi_storage_class_from_matching_nfs_output(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "nfs"}, selected_apps={"csi-driver-nfs"})
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "cluster1",
            "enabled": True,
            "inputs": {
                "parent_id": "project-456",
                "cluster_name": "cluster1",
            },
        },
        {
            "id": "nfs",
            "instance_id": "nfs-cluster1",
            "enabled": True,
            "inputs": {"kubernetes_target_ref": "cluster1"},
        },
    ]
    payload["apps"]["charts"] = [
        {
            "id": "csi-driver-nfs",
            "instance_id": "cluster1",
            "group": "storage",
            "enabled": True,
            "repo": "https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/master/charts",
            "version": "4.13.2",
            "namespace": "kube-system",
            "release-name": "csi-driver-nfs",
            "values": {
                "controller": {"replicas": 2},
                "storageClass": {
                    "name": "nfs-rwx-retain",
                    "parameters": {
                        "subDir": "${pvc.metadata.namespace}/${pvc.metadata.name}",
                        "mountPermissions": "0770",
                        "onDelete": "retain",
                    },
                    "reclaimPolicy": "Retain",
                    "volumeBindingMode": "Immediate",
                },
            },
        }
    ]

    render_flux(
        payload,
        paths,
        component_output_values={
            "nfs-cluster1.server_ip": "10.10.0.5",
            "nfs-cluster1.export_path": "/srv/k8s-nfs",
            "nfs-cluster1.mount_options": ["nfsvers=4.1"],
        },
    )

    release_doc = yaml.safe_load(
        (_target_flux_dir(paths, "cluster1") / "helmrelease-storage-csi-driver-nfs.yaml").read_text(
            encoding="utf-8"
        )
    )
    storage_class = release_doc["spec"]["values"]["storageClass"]
    assert storage_class["create"] is True
    assert storage_class["name"] == "nfs-rwx-retain"
    assert storage_class["parameters"] == {
        "subDir": "${pvc.metadata.namespace}/${pvc.metadata.name}",
        "mountPermissions": "0770",
        "onDelete": "retain",
        "server": "10.10.0.5",
        "share": "/srv/k8s-nfs",
    }
    assert storage_class["reclaimPolicy"] == "Retain"
    assert storage_class["volumeBindingMode"] == "Immediate"
    assert storage_class["mountOptions"] == ["nfsvers=4.1"]


def test_render_omits_nfs_csi_storage_class_until_nfs_outputs_exist(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "nfs"}, selected_apps={"csi-driver-nfs"})
    payload["infra"]["components"] = [
        {"id": "mk8s", "instance_id": "cluster1", "enabled": True, "inputs": {}},
        {
            "id": "nfs",
            "instance_id": "nfs-cluster1",
            "enabled": True,
            "inputs": {"kubernetes_target_ref": "cluster1"},
        },
    ]
    payload["apps"]["charts"] = [
        {
            "id": "csi-driver-nfs",
            "instance_id": "cluster1",
            "group": "storage",
            "enabled": True,
            "repo": "https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/master/charts",
            "version": "4.13.2",
            "namespace": "kube-system",
            "release-name": "csi-driver-nfs",
            "values": {"storageClass": {"name": "nfs-rwx-retain"}},
        }
    ]

    render_flux(payload, paths, component_output_values={})

    release_doc = yaml.safe_load(
        (_target_flux_dir(paths, "cluster1") / "helmrelease-storage-csi-driver-nfs.yaml").read_text(
            encoding="utf-8"
        )
    )
    storage_class = release_doc["spec"]["values"]["storageClass"]
    assert storage_class["name"] == "nfs-rwx-retain"
    assert "create" not in storage_class
    assert "server" not in storage_class["parameters"]
    assert "share" not in storage_class["parameters"]


def test_nfs_component_auto_enables_csi_driver_for_matching_mk8s_target() -> None:
    payload = _starter_payload(selected_infra={"mk8s", "nfs"}, selected_apps=set())
    payload["infra"]["components"] = [
        {"id": "mk8s", "instance_id": "cluster1", "enabled": True, "inputs": {}},
        {"id": "nfs", "instance_id": "nfs-cluster1", "enabled": True, "inputs": {}},
    ]
    payload["apps"]["charts"] = []

    assert ensure_nfs_csi_app_rows(payload, app_entries=component_entries("apps")) is True

    assert payload["apps"]["charts"] == [
        {
            "id": "csi-driver-nfs",
            "instance_id": "cluster1",
            "group": "storage",
            "enabled": True,
            "repo": "https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/master/charts",
            "version": "4.13.2",
            "namespace": "kube-system",
            "release-name": "csi-driver-nfs",
            "values": {
                "controller": {"replicas": 2},
                "storageClass": {
                    "name": "nfs-rwx-retain",
                    "parameters": {
                        "subDir": "${pvc.metadata.namespace}/${pvc.metadata.name}",
                        "mountPermissions": "0770",
                        "onDelete": "retain",
                    },
                    "reclaimPolicy": "Retain",
                    "volumeBindingMode": "Immediate",
                },
            },
        }
    ]


def test_load_config_persists_nfs_csi_app_row_for_direct_config_edit(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s", "nfs"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    _align_infra_resource_name(payload, mk8s, "cluster1")
    nfs = _infra_component_row(payload, "nfs")
    _align_infra_resource_name(payload, nfs, "nfs-cluster1")
    nfs_inputs = nfs.setdefault("inputs", {})
    nfs_inputs["kubernetes_target_ref"] = "cluster1"
    nfs_inputs["ssh_user_name"] = "ubuntu"
    payload["apps"]["charts"] = []
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path, persist_normalized=True)
    paths = resolve_project_paths(config_path)
    render_project(config, paths, source_profile=SourceProfile.PORTABLE)

    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = persisted["apps"]["charts"]
    assert [chart["id"] for chart in charts] == ["csi-driver-nfs"]
    assert charts[0]["instance_id"] == "cluster1"
    assert charts[0]["enabled"] is True
    assert "target_ref" not in charts[0]
    assert (
        _target_flux_dir(paths, "cluster1") / "helmrelease-storage-csi-driver-nfs.yaml"
    ).exists()


def test_load_config_persists_unscoped_nfs_csi_rows_for_each_mk8s_target(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s", "nfs"}, selected_apps=set())
    mk8s_template = _infra_component_row(payload, "mk8s")
    cluster1 = copy.deepcopy(mk8s_template)
    cluster2 = copy.deepcopy(mk8s_template)
    _align_infra_resource_name(payload, cluster1, "cluster1")
    _align_infra_resource_name(payload, cluster2, "cluster2")
    nfs = _infra_component_row(payload, "nfs")
    _align_infra_resource_name(payload, nfs, "nfs-shared")
    nfs_inputs = nfs.setdefault("inputs", {})
    nfs_inputs.pop("kubernetes_target_ref", None)
    nfs_inputs["ssh_user_name"] = "ubuntu"
    payload["infra"]["components"] = [cluster1, cluster2, nfs]
    payload["deploy"]["targets"] = [
        {"instance_id": "cluster1"},
        {"instance_id": "cluster2"},
    ]
    payload["apps"]["charts"] = []
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path, persist_normalized=True)
    paths = resolve_project_paths(config_path)
    render_project(config, paths, source_profile=SourceProfile.PORTABLE)

    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = persisted["apps"]["charts"]
    assert [(chart["id"], chart["instance_id"], chart["enabled"]) for chart in charts] == [
        ("csi-driver-nfs", "cluster1", True),
        ("csi-driver-nfs", "cluster2", True),
    ]
    assert (
        _target_flux_dir(paths, "cluster1") / "helmrelease-storage-csi-driver-nfs.yaml"
    ).exists()
    assert (
        _target_flux_dir(paths, "cluster2") / "helmrelease-storage-csi-driver-nfs.yaml"
    ).exists()


def test_load_config_materializes_soperator_before_gpu_app_rows_for_profile_switch(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s", "sfs"},
        selected_apps={"cert-manager", "soperator"},
    )
    cli._materialize_soperator_component_defaults(payload)
    soperator = next(row for row in payload["apps"]["charts"] if row["id"] == "soperator")
    soperator["profile"] = "nebius-mixed-v1"
    soperator["values"]["partitionProfile"] = "with-h100-infiniband-debug-long"
    soperator["values"]["topologyProfile"] = "nebius-nvl-rack-v1"
    cli._materialize_soperator_component_defaults(payload)
    assert [node["name"] for node in soperator["values"]["nodesets"]] == [
        "worker-cpu",
        "worker-gpu",
    ]
    assert soperator["values"]["slurmConfig"]["topologyPlugin"] == "topology/block"
    assert ensure_mk8s_gpu_app_rows(payload, app_entries=component_entries("apps")) is True
    payload["apps"]["charts"].append(
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        }
    )
    assert soperator["values"]["nodeGroupMapping"]["worker"] == ["worker-gpu"]
    assert any(row["id"] == "nvidia-gpu-operator" for row in payload["apps"]["charts"])
    assert any(row["id"] == "nvidia-network-operator" for row in payload["apps"]["charts"])

    soperator["profile"] = "nebius-cpu-v1"
    soperator["values"]["partitionProfile"] = "shape-default"
    soperator["values"]["topologyProfile"] = "disabled"
    for target in payload.get("deploy", {}).get("targets", []):
        gpu_validation = target.get("validations", {}).get("mk8s_gpu", {})
        for check in gpu_validation.values():
            if isinstance(check, dict) and "enabled" in check:
                check["enabled"] = False
    strip_app_chart_target_refs(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    load_config(config_path, persist_normalized=True)

    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    enabled_chart_ids = [
        row["id"] for row in persisted["apps"]["charts"] if row.get("enabled") is True
    ]
    assert "soperator" in enabled_chart_ids
    assert "cert-manager" in enabled_chart_ids
    assert "nvidia-gpu-operator" not in enabled_chart_ids
    assert "nvidia-network-operator" not in enabled_chart_ids
    mk8s_inputs = persisted["infra"]["components"][0]["inputs"]
    assert not mk8s_inputs.get("gpu_clusters")
    node_groups = mk8s_inputs["node_groups"]
    assert sorted(node_groups) == ["accounting", "controller", "login", "system", "worker-cpu"]
    assert node_groups["worker-cpu"]["node_count"] == 1
    values = next(row for row in persisted["apps"]["charts"] if row["id"] == "soperator")["values"]
    assert values["nodeGroupMapping"]["worker"] == ["worker-cpu"]
    assert [node["name"] for node in values["nodesets"]] == ["worker-cpu"]
    assert [item["name"] for item in values["partitionConfiguration"]["partitions"]] == [
        "cpu",
    ]
    jail_groups = values["storage"]["jail"]["matchExpressions"][0]["values"]
    assert {"worker", "worker-cpu", "worker-gpu"}.issubset(set(jail_groups))
    assert "slurmConfig" not in values or "topologyPlugin" not in values["slurmConfig"]


def test_render_local_soperator_chart_source_writes_static_manifest(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    soperator_chart = next(
        chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator"
    )
    soperator_values = soperator_chart.setdefault("values", {})
    soperator_values.setdefault("soperator-checks", {})["enabled"] = True
    soperator_values.setdefault("soperator-activechecks", {})["enabled"] = True
    soperator_values.setdefault("certManager", {})["enabled"] = None
    soperator_values.setdefault("mariadb-operator", {}).setdefault("webhook", {}).setdefault(
        "cert", {}
    ).setdefault("certManager", {})["enabled"] = None
    cli._materialize_soperator_component_defaults(payload)
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
    assert "helm.sh/hook" not in rendered
    assert "pre-delete-cleanup" not in rendered
    assert "value: null" not in rendered
    rendered_docs = [doc for doc in yaml.safe_load_all(rendered) if isinstance(doc, dict)]
    rendered_names = {
        str(doc.get("metadata", {}).get("name") or "")
        for doc in rendered_docs
        if isinstance(doc.get("metadata"), dict)
    }
    assert "soperator-mariadb-operator-cert-controller" not in rendered_names
    certificates = [
        doc
        for doc in rendered_docs
        if doc.get("apiVersion") == "cert-manager.io/v1" and doc.get("kind") == "Certificate"
    ]
    assert {
        str(doc.get("metadata", {}).get("name") or "")
        for doc in certificates
        if isinstance(doc.get("metadata"), dict)
    } == {
        "soperator-mariadb-operator-webhook-cert",
        "soperator-serving-cert",
    }
    assert {
        doc.get("spec", {}).get("privateKey", {}).get("rotationPolicy")
        for doc in certificates
        if isinstance(doc.get("spec"), dict)
    } == {"Always"}
    slurm_cluster = next(doc for doc in rendered_docs if doc.get("kind") == "SlurmCluster")
    assert slurm_cluster["spec"]["partitionConfiguration"]["configType"] == "structured"
    assert "PluginDir=" not in slurm_cluster.get("spec", {}).get("customSlurmConfig", "")
    assert slurm_cluster["spec"]["slurmNodes"]["accounting"]["enabled"] is True
    assert slurm_cluster["spec"]["slurmNodes"]["rest"]["enabled"] is True
    assert slurm_cluster["spec"]["slurmNodes"]["accounting"]["mariadbOperator"]["enabled"] is True
    assert slurm_cluster["spec"]["slurmNodes"]["accounting"]["mariadbOperator"]["storage"] == {
        "size": "128Gi",
        "storageClassName": "compute-csi-default-sc",
        "volumeClaimTemplate": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "128Gi"}},
            "storageClassName": "compute-csi-default-sc",
        },
    }
    assert slurm_cluster["spec"]["sConfigController"]["runAsUid"] == 0
    assert slurm_cluster["spec"]["sConfigController"]["runAsGid"] == 0
    nodeset = next(doc for doc in rendered_docs if doc.get("kind") == "NodeSet")
    worker_mounts = nodeset["spec"]["slurmd"]["volumes"]["customVolumeMounts"]
    assert {
        "mountPath": "/opt/slurm_scripts/",
        "name": "slurm-scripts",
        "volumeSource": {"configMap": {"defaultMode": 493, "name": "mk8s-slurm-scripts"}},
    } in worker_mounts

    filter_names = {item["name"] for item in slurm_cluster["spec"]["k8sNodeFilters"]}
    assert filter_names == {"no-gpu", "system", "controller", "login", "accounting"}
    filters_by_name = {item["name"]: item for item in slurm_cluster["spec"]["k8sNodeFilters"]}
    assert filters_by_name["controller"]["tolerations"] == [
        {
            "key": "slurm.nebius.ai/nodeset-name",
            "operator": "Equal",
            "value": "controller",
            "effect": "NoSchedule",
        }
    ]
    assert filters_by_name["login"]["tolerations"] == [
        {
            "key": "slurm.nebius.ai/nodeset-name",
            "operator": "Equal",
            "value": "login",
            "effect": "NoSchedule",
        }
    ]
    assert filters_by_name["accounting"]["tolerations"] == [
        {
            "key": "slurm.nebius.ai/nodeset-name",
            "operator": "Equal",
            "value": "accounting",
            "effect": "NoSchedule",
        }
    ]

    activechecks_pod_template = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "PodTemplate"
        and doc.get("metadata", {}).get("name") == "create-user-soperatorchecks"
    )
    assert activechecks_pod_template["template"]["spec"]["hostUsers"] is True
    assert activechecks_pod_template["template"]["spec"]["restartPolicy"] == "Never"
    assert activechecks_pod_template["template"]["spec"]["containers"] == [
        {
            "name": "create-user-soperatorchecks",
            "image": "cr.eu-north1.nebius.cloud/soperator/k8s_check_job:4.0.1-slurm25.11.3",
        }
    ]
    activecheck = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "ActiveCheck"
        and doc.get("metadata", {}).get("name") == "create-user-soperatorchecks"
    )
    assert activecheck["spec"]["podTemplateNameRef"] == "create-user-soperatorchecks"
    assert "hostUsers" not in activecheck["spec"]
    enroot_activecheck = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "ActiveCheck"
        and doc.get("metadata", {}).get("name") == "enroot-cleanup"
    )
    enroot_sbatch_script = enroot_activecheck["spec"]["slurmJobSpec"]["sbatchScript"]
    assert "job-scoped Enroot containers" in enroot_sbatch_script
    assert "pyxis_([0-9]+" in enroot_sbatch_script
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
    system_affinity = filters_by_name["system"]["affinity"]
    assert manager_deployment["spec"]["template"]["spec"]["affinity"] == system_affinity
    checks_deployment = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "Deployment"
        and doc.get("metadata", {}).get("name") == "soperator-checks-checks"
    )
    assert checks_deployment["spec"]["template"]["spec"]["affinity"] == system_affinity
    mariadb_operator_deployment = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "Deployment"
        and doc.get("metadata", {}).get("name") == "soperator-mariadb-operator"
    )
    assert mariadb_operator_deployment["metadata"]["namespace"] == "soperator"
    assert mariadb_operator_deployment["spec"]["template"]["spec"]["affinity"] == system_affinity
    mount_scripts = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "mk8s-mount-scripts"
    )
    assert mount_scripts["metadata"]["namespace"] == "soperator"
    slurm_scripts = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "mk8s-slurm-scripts"
    )
    cleanup_enroot = slurm_scripts["data"]["cleanup_enroot.sh"]
    assert "Cleanup leftover Enroot containers for this job" in cleanup_enroot
    assert "pyxis_${SLURM_JOB_ID}" in cleanup_enroot
    assert "pyxis_.+_${SLURM_JOB_ID}" in cleanup_enroot


def test_render_local_soperator_defaults_skip_optional_service_gates(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    soperator_values = next(
        chart["values"] for chart in config["apps"]["charts"] if chart["id"] == "soperator"
    )
    assert soperator_values["soperator-activechecks"]["enabled"] is False
    assert soperator_values["soperator-activechecks"]["waitForChecks"]["enabled"] is False
    assert "srunReadyPartition" not in soperator_values["soperator-activechecks"]
    assert soperator_values["soperator-checks"]["enabled"] is False
    assert soperator_values["soperator-notifier"]["enabled"] is False
    assert soperator_values["soperator-backup-config"]["enabled"] is False
    assert soperator_values["soperator-dcgm-exporter"]["enabled"] is False

    rendered_chart = _target_flux_dir(paths) / "post-flux-helmrender-slurm-soperator.yaml"
    rendered_docs = [
        doc
        for doc in yaml.safe_load_all(rendered_chart.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    rendered_names = {
        str(doc.get("metadata", {}).get("name") or "")
        for doc in rendered_docs
        if isinstance(doc.get("metadata"), dict)
    }
    assert all(doc.get("kind") != "ActiveCheck" for doc in rendered_docs)
    assert "soperator-checks-checks" not in rendered_names
    assert "soperator-notifier" not in rendered_names
    assert "soperator-jail-backup" not in rendered_names
    assert "soperator-dcgm-exporter" not in rendered_names
    nodeconfigurator = next(doc for doc in rendered_docs if doc.get("kind") == "NodeConfigurator")
    assert nodeconfigurator["spec"]["customContainer"]["enabled"] is True
    assert nodeconfigurator["spec"]["customContainer"]["command"] == [
        "/bin/sh",
        "-c",
        "trap : TERM INT; sleep infinity & wait",
    ]
    assert nodeconfigurator["spec"]["rebooter"]["enabled"] is False
    assert all(
        "nodeconfigurator-role" not in str(doc.get("metadata", {}).get("name") or "")
        and "nodeconfigurator-binding" not in str(doc.get("metadata", {}).get("name") or "")
        for doc in rendered_docs
        if doc.get("kind") in {"ClusterRole", "ClusterRoleBinding"}
    )
    assert all(
        "nodeconfigurator" not in str(doc.get("metadata", {}).get("name") or "")
        for doc in rendered_docs
        if doc.get("kind") == "ServiceAccount"
    )
    assert all(
        "sssd" not in (doc.get("spec") or {})
        for doc in rendered_docs
        if doc.get("kind") == "NodeSet"
    )


def test_render_soperator_preserves_operator_service_gate_opt_ins(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    soperator_chart = next(
        chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator"
    )
    values = soperator_chart.setdefault("values", {})
    values["nodesets"] = [{"name": "worker", "sssd": {"enabled": True}}]
    values["rebooter"] = {"enabled": True}
    values["slurmNodes"] = {"sssd": {"enabled": True}}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    soperator_values = next(
        chart["values"] for chart in config["apps"]["charts"] if chart["id"] == "soperator"
    )
    worker_values = next(item for item in soperator_values["nodesets"] if item["name"] == "worker")
    assert worker_values["sssd"]["enabled"] is True
    assert soperator_values["rebooter"]["enabled"] is True
    assert soperator_values["slurmNodes"]["sssd"]["enabled"] is True

    rendered_chart = _target_flux_dir(paths) / "post-flux-helmrender-slurm-soperator.yaml"
    rendered_docs = [
        doc
        for doc in yaml.safe_load_all(rendered_chart.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    worker_nodeset = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "NodeSet" and doc.get("metadata", {}).get("name") == "worker"
    )
    assert "sssd" in worker_nodeset["spec"]
    slurm_cluster = next(doc for doc in rendered_docs if doc.get("kind") == "SlurmCluster")
    assert "sssd" in slurm_cluster["spec"]["slurmNodes"]["controller"]
    assert "sssd" in slurm_cluster["spec"]["slurmNodes"]["login"]
    nodeconfigurator = next(doc for doc in rendered_docs if doc.get("kind") == "NodeConfigurator")
    assert nodeconfigurator["spec"]["customContainer"]["enabled"] is True
    assert nodeconfigurator["spec"]["rebooter"]["enabled"] is True
    assert any(
        doc.get("kind") == "ClusterRole"
        and str(doc.get("metadata", {}).get("name") or "").endswith("nodeconfigurator-role")
        for doc in rendered_docs
    )
    assert any(
        doc.get("kind") == "ClusterRoleBinding"
        and str(doc.get("metadata", {}).get("name") or "").endswith("nodeconfigurator-binding")
        for doc in rendered_docs
    )
    rebooter_service_account = nodeconfigurator["spec"]["rebooter"]["serviceAccountName"]
    assert any(
        doc.get("kind") == "ServiceAccount"
        and doc.get("metadata", {}).get("name") == rebooter_service_account
        for doc in rendered_docs
    )


def test_render_soperator_guided_sssd_gate_materializes_all_identity_surfaces(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    soperator_chart = next(
        chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator"
    )
    values = soperator_chart.setdefault("values", {})
    values["sssd"] = {"enabled": True}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    soperator_values = next(
        chart["values"] for chart in config["apps"]["charts"] if chart["id"] == "soperator"
    )
    assert "sssd" not in soperator_values
    assert soperator_values["slurmNodes"]["sssd"]["enabled"] is True
    assert all(
        (nodeset.get("sssd") or {}).get("enabled") is True
        for nodeset in soperator_values["nodesets"]
        if isinstance(nodeset, dict)
    )

    rendered_chart = _target_flux_dir(paths) / "post-flux-helmrender-slurm-soperator.yaml"
    rendered_docs = [
        doc
        for doc in yaml.safe_load_all(rendered_chart.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    nodesets = [doc for doc in rendered_docs if doc.get("kind") == "NodeSet"]
    assert nodesets
    assert all("sssd" in (doc.get("spec") or {}) for doc in nodesets)
    slurm_cluster = next(doc for doc in rendered_docs if doc.get("kind") == "SlurmCluster")
    assert "sssd" in slurm_cluster["spec"]["slurmNodes"]["controller"]
    assert "sssd" in slurm_cluster["spec"]["slurmNodes"]["login"]


def test_render_soperator_guided_sssd_false_clears_stale_identity_surfaces(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    soperator_chart = next(
        chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator"
    )
    values = soperator_chart.setdefault("values", {})
    values["sssd"] = {"enabled": False}
    values["nodesets"] = [{"name": "worker", "sssd": {"enabled": True}}]
    values["slurmNodes"] = {"sssd": {"enabled": True}}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    soperator_values = next(
        chart["values"] for chart in config["apps"]["charts"] if chart["id"] == "soperator"
    )
    assert "sssd" not in soperator_values
    assert soperator_values["slurmNodes"]["sssd"]["enabled"] is False
    assert all(
        (nodeset.get("sssd") or {}).get("enabled") is False
        for nodeset in soperator_values["nodesets"]
        if isinstance(nodeset, dict)
    )

    rendered_chart = _target_flux_dir(paths) / "post-flux-helmrender-slurm-soperator.yaml"
    rendered_docs = [
        doc
        for doc in yaml.safe_load_all(rendered_chart.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    nodesets = [doc for doc in rendered_docs if doc.get("kind") == "NodeSet"]
    assert nodesets
    assert all("sssd" not in (doc.get("spec") or {}) for doc in nodesets)
    slurm_cluster = next(doc for doc in rendered_docs if doc.get("kind") == "SlurmCluster")
    assert "sssd" not in slurm_cluster["spec"]["slurmNodes"]["controller"]
    assert "sssd" not in slurm_cluster["spec"]["slurmNodes"]["login"]


def test_render_local_soperator_cpu_profile_disables_dcgm_without_gpu_nodes(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    soperator_chart = next(
        chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator"
    )
    soperator_chart["profile"] = "nebius-cpu-v1"
    soperator_chart["values"] = {"soperator-dcgm-exporter": {"enabled": True}}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    soperator_values = next(
        chart["values"] for chart in config["apps"]["charts"] if chart["id"] == "soperator"
    )
    assert soperator_values["soperator-dcgm-exporter"]["enabled"] is False

    rendered_chart = _target_flux_dir(paths) / "post-flux-helmrender-slurm-soperator.yaml"
    rendered_docs = [
        doc
        for doc in yaml.safe_load_all(rendered_chart.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    assert "soperator-dcgm-exporter" not in {
        str(doc.get("metadata", {}).get("name") or "") for doc in rendered_docs
    }


def test_render_project_materializes_soperator_profile_defaults(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    soperator_chart = next(
        chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator"
    )
    soperator_chart.setdefault("values", {}).setdefault("soperator-activechecks", {})["enabled"] = (
        True
    )
    mk8s_row = next(row for row in payload["infra"]["components"] if row["id"] == "mk8s")
    mk8s_row.setdefault("inputs", {})["soperator"] = {
        "system_node_count": 2,
        "system_autoscaling": {
            "enabled": True,
            "min_node_count": 1,
            "max_node_count": 4,
        },
        "controller_node_count": 2,
        "login_node_count": 1,
        "accounting_node_count": 1,
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    soperator_values = next(
        chart["values"] for chart in config["apps"]["charts"] if chart["id"] == "soperator"
    )
    assert soperator_values["clusterName"] == "mk8s"
    assert soperator_values["soperator-checks"]["enabled"] is True
    assert soperator_values["soperator-activechecks"]["slurmClusterRefName"] == "mk8s"
    assert soperator_values["soperator-activechecks"]["srunReadyPartition"] == "hidden"
    mk8s_inputs = next(
        row["inputs"]
        for row in config["infra"]["components"]
        if isinstance(row, dict) and row.get("id") == "mk8s"
    )
    for group_name in ("system", "controller", "login", "accounting"):
        group = mk8s_inputs["node_groups"][group_name]
        assert group["platform"] == "cpu-d3"
        assert group["preset"] == "8vcpu-32gb"
    assert mk8s_inputs["node_groups"]["system"]["autoscaling"] == {
        "min_node_count": 1,
        "max_node_count": 4,
    }
    assert "node_count" not in mk8s_inputs["node_groups"]["system"]
    assert mk8s_inputs["node_groups"]["controller"]["node_count"] == 2
    assert mk8s_inputs["node_groups"]["login"]["node_count"] == 1
    assert mk8s_inputs["node_groups"]["accounting"]["node_count"] == 1
    assert mk8s_inputs["node_groups"]["login"]["taints"] == [
        {
            "key": "slurm.nebius.ai/nodeset-name",
            "value": "login",
            "effect": "NO_SCHEDULE",
        }
    ]
    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    assert "autoscaling = {" in main_tf
    assert "min_node_count = 1" in main_tf
    assert "max_node_count = 4" in main_tf


def test_render_soperator_uses_cluster_target_name_not_client_name(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    for row in payload["infra"]["components"]:
        if isinstance(row, dict) and row.get("id") == "mk8s":
            row["instance_id"] = "soperator-cluster1"
            row["inputs"] = _mk8s_inputs(cluster_name="soperator-cluster1", cpu=True, gpu=False)
    for row in payload["apps"]["charts"]:
        if isinstance(row, dict) and row.get("enabled") is True:
            row["instance_id"] = "soperator-cluster1"
            row.pop("target_ref", None)
    for row in payload.get("deploy", {}).get("targets", []):
        if isinstance(row, dict) and row.get("instance_id") == "mk8s":
            row["instance_id"] = "soperator-cluster1"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    assert 'module "soperator_cluster1" {' in main_tf
    assert 'module "client_a" {' not in main_tf
    assert 'module "mk8s" {' not in main_tf

    soperator_values = next(
        chart["values"] for chart in config["apps"]["charts"] if chart["id"] == "soperator"
    )
    assert soperator_values["clusterName"] == "soperator-cluster1"
    assert _target_flux_dir(paths, "soperator-cluster1").exists()
    assert not _target_flux_dir(paths, "client-a").exists()
    assert not _target_flux_dir(paths, "mk8s").exists()


def test_inject_local_chart_namespace_skips_hooks_only_render() -> None:
    rendered = yaml.safe_dump(
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": "cleanup",
                "annotations": {"helm.sh/hook": "pre-delete"},
            },
        },
        sort_keys=False,
    )

    assert _inject_local_chart_namespace(rendered, namespace="slurm") == ""


def test_inject_local_chart_namespace_keeps_opted_in_hooks() -> None:
    rendered = yaml.safe_dump(
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": "qos-reconcile",
                "annotations": {
                    "helm.sh/hook": "post-install,post-upgrade",
                    "nebius-cxcli.nebius.ai/include-local-render": "true",
                },
            },
        },
        sort_keys=False,
    )

    docs = list(yaml.safe_load_all(_inject_local_chart_namespace(rendered, namespace="slurm")))
    assert docs[0]["metadata"]["namespace"] == "slurm"
    assert docs[0]["metadata"]["name"] == "qos-reconcile"


def test_inject_local_chart_namespace_makes_cert_manager_rotation_policy_explicit() -> None:
    rendered = yaml.safe_dump_all(
        [
            {
                "apiVersion": "cert-manager.io/v1",
                "kind": "Certificate",
                "metadata": {"name": "missing-private-key"},
                "spec": {"secretName": "missing-private-key"},
            },
            {
                "apiVersion": "cert-manager.io/v1",
                "kind": "Certificate",
                "metadata": {"name": "preserve-private-key"},
                "spec": {
                    "secretName": "preserve-private-key",
                    "privateKey": {"algorithm": "ECDSA"},
                },
            },
            {
                "apiVersion": "cert-manager.io/v1",
                "kind": "Certificate",
                "metadata": {"name": "explicit-never"},
                "spec": {
                    "secretName": "explicit-never",
                    "privateKey": {"rotationPolicy": "Never"},
                },
            },
        ],
        sort_keys=False,
    )

    docs = [
        doc
        for doc in yaml.safe_load_all(_inject_local_chart_namespace(rendered, namespace="slurm"))
        if isinstance(doc, dict)
    ]
    by_name = {doc["metadata"]["name"]: doc for doc in docs}

    assert by_name["missing-private-key"]["spec"]["privateKey"]["rotationPolicy"] == "Always"
    assert by_name["preserve-private-key"]["spec"]["privateKey"] == {
        "algorithm": "ECDSA",
        "rotationPolicy": "Always",
    }
    assert by_name["explicit-never"]["spec"]["privateKey"]["rotationPolicy"] == "Never"


def test_stage_local_helm_chart_copies_symlink_targets(tmp_path: Path) -> None:
    chart_dir = tmp_path / "example-chart"
    templates_dir = chart_dir / "templates"
    templates_dir.mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text(
        "apiVersion: v2\nname: example-chart\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    outside_template = tmp_path / "outside-template.yaml"
    outside_template.write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: copied\n",
        encoding="utf-8",
    )
    (templates_dir / "linked.yaml").symlink_to(outside_template)

    staged_chart = Path(_stage_local_helm_chart(str(chart_dir), tmp_path / "staging"))
    staged_link = staged_chart / "templates" / "linked.yaml"

    assert staged_link.exists()
    assert not staged_link.is_symlink()
    assert "name: copied" in staged_link.read_text(encoding="utf-8")


def test_stage_local_helm_chart_replaces_stale_staged_copy(tmp_path: Path) -> None:
    chart_dir = tmp_path / "example-chart"
    templates_dir = chart_dir / "templates"
    templates_dir.mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text(
        "apiVersion: v2\nname: example-chart\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (templates_dir / "config.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: first\n",
        encoding="utf-8",
    )
    staging_root = tmp_path / "staging"

    staged_chart = Path(_stage_local_helm_chart(str(chart_dir), staging_root))
    (staged_chart / "stale.yaml").write_text("stale\n", encoding="utf-8")
    (templates_dir / "config.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: second\n",
        encoding="utf-8",
    )

    restaged_chart = Path(_stage_local_helm_chart(str(chart_dir), staging_root))

    assert restaged_chart == staged_chart
    assert not (restaged_chart / "stale.yaml").exists()
    assert "name: second" in (restaged_chart / "templates" / "config.yaml").read_text(
        encoding="utf-8"
    )


def test_build_local_helm_chart_dependencies_reuses_packaged_archives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chart_dir = tmp_path / "chart"
    charts_dir = chart_dir / "charts"
    charts_dir.mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v2",
                "name": "parent",
                "version": "0.1.0",
                "dependencies": [
                    {
                        "name": "child",
                        "version": "1.2.3",
                        "repository": "https://example.invalid/charts",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (chart_dir / "Chart.lock").write_text(
        yaml.safe_dump(
            {
                "dependencies": [
                    {
                        "name": "child",
                        "version": "1.2.3",
                        "repository": "https://example.invalid/charts",
                    }
                ],
                "digest": "sha256:test",
                "generated": "2026-01-01T00:00:00Z",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (charts_dir / "child-1.2.3.tgz").write_bytes(b"packaged")

    def fail_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("helm dependency build should not run when archives are packaged")

    monkeypatch.setattr("nebius_cxcli.flux_render.subprocess.run", fail_run)

    _build_local_helm_chart_dependencies(str(chart_dir))


def test_build_local_helm_chart_dependencies_rebuilds_file_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_dir = tmp_path / "parent"
    charts_dir = parent_dir / "charts"
    charts_dir.mkdir(parents=True)
    (parent_dir / "Chart.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v2",
                "name": "parent",
                "version": "0.1.0",
                "dependencies": [
                    {
                        "name": "child",
                        "version": "1.2.3",
                        "repository": "file://../child",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (parent_dir / "Chart.lock").write_text(
        yaml.safe_dump(
            {
                "dependencies": [
                    {
                        "name": "child",
                        "version": "1.2.3",
                        "repository": "file://../child",
                    }
                ],
                "digest": "sha256:test",
                "generated": "2026-01-01T00:00:00Z",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (charts_dir / "child-1.2.3.tgz").write_bytes(b"stale")
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command: list[str], **_kwargs: object) -> Result:
        calls.append(command)
        return Result()

    monkeypatch.setattr("nebius_cxcli.flux_render.subprocess.run", fake_run)

    _build_local_helm_chart_dependencies(str(parent_dir))

    assert calls == [["helm", "dependency", "build", "--skip-refresh", str(parent_dir)]]


def test_build_local_helm_chart_dependencies_adds_remote_repositories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()
    dependency = {
        "name": "child",
        "version": "1.2.3",
        "repository": "https://example.invalid/charts",
    }
    (chart_dir / "Chart.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v2",
                "name": "parent",
                "version": "0.1.0",
                "dependencies": [dependency],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (chart_dir / "Chart.lock").write_text(
        yaml.safe_dump(
            {
                "dependencies": [dependency],
                "digest": "sha256:test",
                "generated": "2026-01-01T00:00:00Z",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    monkeypatch.setenv(
        "HELM_REPOSITORY_CONFIG",
        str(tmp_path / "missing-repositories.yaml"),
    )

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command: list[str], **_kwargs: object) -> Result:
        calls.append(command)
        return Result()

    monkeypatch.setattr("nebius_cxcli.flux_render.subprocess.run", fake_run)

    _build_local_helm_chart_dependencies(str(chart_dir))

    assert len(calls) == 2
    assert calls[0][:5] == [
        "helm",
        "repo",
        "add",
        "cxcli-local-1",
        "https://example.invalid/charts",
    ]
    assert calls[0][5] == "--force-update"
    assert calls[1][0:4] == ["helm", "dependency", "build", "--skip-refresh"]
    assert calls[1][-1] == str(chart_dir)
    repo_config = calls[0][calls[0].index("--repository-config") + 1]
    repo_cache = calls[0][calls[0].index("--repository-cache") + 1]
    assert calls[1][calls[1].index("--repository-config") + 1] == repo_config
    assert calls[1][calls[1].index("--repository-cache") + 1] == repo_cache


def test_build_local_helm_chart_dependencies_filters_unrelated_repository_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()
    dependency = {
        "name": "child",
        "version": "1.2.3",
        "repository": "https://example.invalid/charts",
    }
    (chart_dir / "Chart.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v2",
                "name": "parent",
                "version": "0.1.0",
                "dependencies": [dependency],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (chart_dir / "Chart.lock").write_text(
        yaml.safe_dump(
            {
                "dependencies": [dependency],
                "digest": "sha256:test",
                "generated": "2026-01-01T00:00:00Z",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    source_config = tmp_path / "repositories.yaml"
    source_config.write_text(
        yaml.safe_dump(
            {
                "repositories": [
                    {
                        "name": "kuberay",
                        "url": "https://ray-project.github.io/kuberay-helm/",
                    },
                    {
                        "name": "example",
                        "url": "https://example.invalid/charts",
                        "username": "user",
                        "password": "redacted",
                    },
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    seeded_configs: list[object] = []
    monkeypatch.setenv("HELM_REPOSITORY_CONFIG", str(source_config))

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command: list[str], **_kwargs: object) -> Result:
        calls.append(command)
        if command[:3] == ["helm", "repo", "update"]:
            repo_config = Path(command[command.index("--repository-config") + 1])
            seeded_configs.append(yaml.safe_load(repo_config.read_text(encoding="utf-8")))
        return Result()

    monkeypatch.setattr("nebius_cxcli.flux_render.subprocess.run", fake_run)

    _build_local_helm_chart_dependencies(str(chart_dir))

    assert calls[0][:4] == ["helm", "repo", "update", "example"]
    assert seeded_configs == [
        {
            "repositories": [
                {
                    "name": "example",
                    "url": "https://example.invalid/charts",
                    "username": "user",
                    "password": "redacted",
                }
            ]
        }
    ]


def test_render_local_soperator_cpu_profile_writes_single_cpu_nodeset(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    for chart in payload["apps"]["charts"]:
        if isinstance(chart, dict) and chart.get("id") == "soperator":
            chart["profile"] = "nebius-cpu-v1"
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
    assert set(node_sets) == {"worker-cpu"}
    assert "nvidia.com/gpu" not in node_sets["worker-cpu"]["spec"]["slurmd"]["resources"]
    assert node_sets["worker-cpu"]["spec"]["nodeConfig"]["static"] == (
        "Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=1"
    )

    slurm_cluster = next(doc for doc in rendered_docs if doc.get("kind") == "SlurmCluster")
    assert "clusterType" not in slurm_cluster["spec"]
    exporter = slurm_cluster["spec"]["slurmNodes"]["exporter"]
    assert "jobSource" not in exporter
    assert "accountingJobStates" not in exporter
    assert "accountingJobsLookback" not in exporter
    assert slurm_cluster["spec"]["partitionConfiguration"]["partitions"] == [
        {
            "name": "cpu",
            "nodeSetRefs": ["worker-cpu"],
            "config": "Default=YES State=UP MaxTime=INFINITE PriorityTier=5",
        },
    ]
    assert not any(
        doc.get("metadata", {}).get("labels", {}).get("app") == "nvidia-dcgm-exporter"
        for doc in rendered_docs
    )


def test_soperator_chart_template_renders_all_priority_weight_fields(tmp_path: Path) -> None:
    chart_path = _stage_soperator_chart_for_helm_template(tmp_path)

    result = subprocess.run(
        [
            "helm",
            "template",
            "weights-smoke",
            str(chart_path),
            "--namespace",
            "soperator",
            "--show-only",
            "templates/slurm-cluster/slurm-cluster-cr.yaml",
            "--set",
            "schedulingConfig.accountingStorageEnforce[0]=associations",
            "--set",
            "schedulingConfig.accountingStorageEnforce[1]=limits",
            "--set",
            "schedulingConfig.accountingStorageEnforce[2]=qos",
            "--set",
            "schedulingConfig.enforcePartLimits=ANY",
            "--set",
            "schedulingConfig.priorityType=priority/multifactor",
            "--set",
            "schedulingConfig.priorityWeights.age=100",
            "--set",
            "schedulingConfig.priorityWeights.assoc=300",
            "--set",
            "schedulingConfig.priorityWeights.fairshare=200",
            "--set",
            "schedulingConfig.priorityWeights.partition=1000",
            "--set",
            "schedulingConfig.priorityWeights.jobSize=500",
            "--set",
            "schedulingConfig.priorityWeights.qos=900",
            "--set",
            "schedulingConfig.priorityWeights.tres=gres/gpu=1000",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "AccountingStorageEnforce=associations,limits,qos" in result.stdout
    assert "EnforcePartLimits=ANY" in result.stdout
    assert "PriorityWeightAge=100" in result.stdout
    assert "PriorityWeightAssoc=300" in result.stdout
    assert "PriorityWeightFairshare=200" in result.stdout
    assert "PriorityWeightPartition=1000" in result.stdout
    assert "PriorityWeightJobSize=500" in result.stdout
    assert "PriorityWeightQOS=900" in result.stdout
    assert "PriorityWeightTRES=gres/gpu=1000" in result.stdout


def test_soperator_chart_template_uses_custom_content_file_for_builtin_script(
    tmp_path: Path,
) -> None:
    chart_path = _stage_soperator_chart_for_helm_template(tmp_path)

    result = subprocess.run(
        [
            "helm",
            "template",
            "script-smoke",
            str(chart_path),
            "--namespace",
            "soperator",
            "--show-only",
            "templates/slurm-cluster/slurm-scripts-cm.yaml",
            "--set",
            "slurmScripts.builtIn.cleanup_enroot\\.sh.customContentFile=local_slurm_scripts/cleanup_enroot.sh",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Cleanup leftover Enroot containers for this job" in result.stdout
    assert "pyxis_${SLURM_JOB_ID}" in result.stdout


def test_soperator_chart_template_fails_for_missing_custom_content_file(
    tmp_path: Path,
) -> None:
    chart_path = _stage_soperator_chart_for_helm_template(tmp_path)

    result = subprocess.run(
        [
            "helm",
            "template",
            "script-smoke",
            str(chart_path),
            "--namespace",
            "soperator",
            "--show-only",
            "templates/slurm-cluster/slurm-scripts-cm.yaml",
            "--set",
            "slurmScripts.builtIn.cleanup_enroot\\.sh.customContentFile=local_slurm_scripts/does-not-exist.sh",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert (
        "slurmScripts.builtIn.cleanup_enroot.sh.customContentFile references missing file "
        '"local_slurm_scripts/does-not-exist.sh"'
    ) in result.stderr


def test_soperator_chart_schema_rejects_unknown_nodeconfigurator_container_keys(
    tmp_path: Path,
) -> None:
    chart_path = _stage_soperator_chart_for_helm_template(tmp_path)

    result = subprocess.run(
        [
            "helm",
            "template",
            "schema-smoke",
            str(chart_path),
            "--namespace",
            "soperator",
            "--set",
            "customContainer.args[0]=sleep",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "additional properties 'args' not allowed" in result.stderr


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
            "config": "Default=YES State=UP MaxTime=INFINITE PriorityTier=5",
        },
        {
            "name": "gpu",
            "nodeSetRefs": ["worker-gpu"],
            "config": "Default=NO State=UP MaxTime=INFINITE PriorityTier=10",
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
            "config": "Default=YES State=UP MaxTime=INFINITE PriorityTier=5",
        },
        {
            "name": "gpu",
            "nodeSetRefs": ["worker-gpu"],
            "config": "Default=NO State=UP MaxTime=INFINITE PriorityTier=10",
        },
        {
            "name": "debug",
            "nodeSetRefs": ["worker-cpu", "worker-gpu"],
            "config": "Default=NO State=UP MaxTime=00:30:00 PriorityTier=100",
        },
        {
            "name": "long",
            "nodeSetRefs": ["worker-cpu", "worker-gpu"],
            "config": "Default=NO State=UP MaxTime=7-00:00:00 PriorityTier=1",
        },
    ]


def test_render_local_soperator_qos_preemption_profile_writes_slurm_policy(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    for chart in payload["apps"]["charts"]:
        if isinstance(chart, dict) and chart.get("id") == "soperator":
            chart["profile"] = "nebius-gpu-v1"
            chart["values"] = {"partitionProfile": "with-qos-preemption"}
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
    qos_script = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "ConfigMap"
        and str(doc.get("metadata", {}).get("name", "")).endswith("-qos-reconcile-script")
    )

    assert "PreemptType=preempt/qos" in slurm_cluster["spec"]["customSlurmConfig"]
    assert "PreemptMode=REQUEUE" in slurm_cluster["spec"]["customSlurmConfig"]
    assert "PreemptParameters=send_user_signal" in slurm_cluster["spec"]["customSlurmConfig"]
    assert "PriorityWeightQOS=1000" in slurm_cluster["spec"]["customSlurmConfig"]
    assert "PriorityWeightFairshare=100" in slurm_cluster["spec"]["customSlurmConfig"]
    assert (
        "AccountingStorageEnforce=associations,limits,qos"
        in slurm_cluster["spec"]["customSlurmConfig"]
    )
    assert "EnforcePartLimits=ANY" in slurm_cluster["spec"]["customSlurmConfig"]
    assert "app.kubernetes.io/component=accounting" in qos_script["data"]["driver.sh"]
    assert (
        'exec -i "${POD}" -c accounting -- /bin/bash -s < /scripts/reconcile.sh'
        in (qos_script["data"]["driver.sh"])
    )
    assert "kubectl cp" not in qos_script["data"]["driver.sh"]
    assert "apply_account 'root'" in qos_script["data"]["reconcile.sh"]
    assert (
        "apply_association 'root' 'root' 'DefaultQOS=data' 'Qos=debug,eval,train,data'"
    ) in qos_script["data"]["reconcile.sh"]
    qos_role = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "Role"
        and str(doc.get("metadata", {}).get("name", "")).endswith("-qos-reconcile")
    )
    assert qos_role["rules"][0]["verbs"] == ["get", "list", "watch"]
    qos_job = next(
        doc
        for doc in rendered_docs
        if doc.get("kind") == "Job"
        and str(doc.get("metadata", {}).get("name", "")).endswith("-qos-reconcile")
    )
    assert qos_job["spec"]["template"]["spec"]["containers"][0]["image"] == "alpine/k8s:1.33.5"
    assert qos_job["spec"]["activeDeadlineSeconds"] == 1200
    assert [
        partition["name"]
        for partition in slurm_cluster["spec"]["partitionConfiguration"]["partitions"]
    ] == ["gpu", "debug", "eval", "train", "data"]
    assert slurm_cluster["spec"]["partitionConfiguration"]["partitions"][-1] == {
        "name": "data",
        "nodeSetRefs": ["worker"],
        "config": "Default=NO State=UP MaxTime=1-00:00:00 PriorityTier=10 AllowQos=data",
    }


def test_load_config_rejects_soperator_qos_preemption_without_qos_configuration(
    tmp_path: Path,
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    for chart in payload["apps"]["charts"]:
        if isinstance(chart, dict) and chart.get("id") == "soperator":
            chart["profile"] = "nebius-gpu-v1"
            chart["values"] = {
                "partitionProfile": "with-qos-preemption",
                "qosConfiguration": {"enabled": False},
            }
    cli._materialize_soperator_component_defaults(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="Slurm preempt/qos"):
        load_config(config_path)


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

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"soperator"})
    soperator_chart = next(
        chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator"
    )
    soperator_chart.setdefault("values", {}).setdefault("soperator-notifier", {})["enabled"] = True
    _retarget_enabled_apps(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    render_project(load_config(config_path), paths, source_profile=SourceProfile.LOCAL)

    rendered_chart = _target_flux_dir(paths) / "post-flux-helmrender-slurm-soperator.yaml"
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


def test_render_soperator_notifier_mysterybox_eso_secret_source(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"soperator"})
    soperator_chart = next(
        chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator"
    )
    notifier_values = soperator_chart.setdefault("values", {}).setdefault("soperator-notifier", {})
    notifier_values["enabled"] = True
    notifier_values["slack"] = {
        "mode": "existing-webhook",
        "webhookSource": "mysterybox",
        "existingSecret": "soperator-notifier-slack-webhook",
        "existingSecretKey": "url",
        "mysterybox": {
            "secretId": "mbsec-e00slack",
            "property": "url",
        },
    }
    _retarget_enabled_apps(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    render_project(
        load_config(config_path),
        paths,
        source_profile=SourceProfile.LOCAL,
    )

    mysterybox_objects = _load_mysterybox_eso_post_flux_objects(paths)
    external_secret = next(item for item in mysterybox_objects if item["kind"] == "ExternalSecret")
    assert external_secret["metadata"]["namespace"] == "soperator"
    assert external_secret["metadata"]["name"] == "soperator-notifier-slack-webhook"
    assert external_secret["spec"]["target"]["name"] == "soperator-notifier-slack-webhook"
    assert external_secret["spec"]["data"] == [
        {
            "secretKey": "url",
            "remoteRef": {
                "key": "mbsec-e00slack",
                "property": "url",
            },
        }
    ]
    assert cli._mysterybox_eso_rendered_secret_keys(_target_flux_dir(paths)) == {
        ("soperator", "soperator-notifier-slack-webhook", "url")
    }
    rendered = (_target_flux_dir(paths) / "post-flux-helmrender-slurm-soperator.yaml").read_text(
        encoding="utf-8"
    )
    assert "hooks.slack.com" not in rendered
    assert "webhookUrl" not in rendered


def test_render_local_soperator_backup_uses_only_secret_reference(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(
        selected_infra={"mk8s", "object-storage"},
        selected_apps={"soperator"},
    )
    soperator_chart = next(
        chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator"
    )
    soperator_chart.setdefault("values", {}).setdefault("soperator-backup-config", {})[
        "enabled"
    ] = True
    _retarget_enabled_apps(payload)
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

    rendered_chart = _target_flux_dir(paths) / "post-flux-helmrender-slurm-soperator.yaml"
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


def test_render_soperator_backup_uses_chart_owned_k8up_dependency(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(
        selected_infra={"mk8s", "object-storage"},
        selected_apps={"soperator"},
    )
    soperator_chart = next(
        chart for chart in payload["apps"]["charts"] if chart["id"] == "soperator"
    )
    soperator_chart["repo"] = (
        "https://github.com/nebius/nebius-ps-services/tree/main/helm-charts/soperator"
    )
    soperator_chart.setdefault("values", {}).setdefault("soperator-backup-config", {})[
        "enabled"
    ] = True
    _retarget_enabled_apps(payload)
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

    release_doc = yaml.safe_load(
        (_target_flux_dir(paths) / "helmrelease-slurm-soperator.yaml").read_text(encoding="utf-8")
    )
    assert {"name": "k8up", "namespace": "k8up"} not in release_doc["spec"].get("dependsOn", [])
    assert not (_target_flux_dir(paths) / "helmrelease-storage-k8up.yaml").exists()


def test_render_soperator_mk8s_node_groups_attach_sibling_sfs(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["instance_id"] = "cluster1"
    mk8s["inputs"] = {
        "cluster": {
            "cluster_name": "cluster1",
        },
        "node_groups": {
            "controller": {
                "workload": "controller",
                "node_count": 1,
                "jail": True,
                "sfs_filesystem_keys": ["jail", "controller-spool"],
            },
            "login": {
                "workload": "login",
                "node_count": 1,
                "jail": True,
                "sfs_filesystem_keys": ["jail"],
            },
            "system": {"workload": "system", "node_count": 1},
            "compact-cpu": {
                "nodeset_name": "worker-cpu",
                "workload": "worker",
                "node_count": 2,
                "jail": True,
                "sfs_filesystem_keys": ["jail", "controller-spool"],
            },
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
    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )
    assert 'module "cluster1" {' in main_tf
    assert 'module "sfs" {' in main_tf
    assert 'filesystems = [for key in ["jail", "controller-spool"] : {' in main_tf
    assert 'filesystems = [for key in ["jail"] : {' in main_tf
    assert main_tf.count('filesystems = [for key in ["jail", "controller-spool"] : {') == 2
    assert "mount_tag = module.sfs.filesystems[key].mount_tag" in main_tf
    assert "id = module.sfs.filesystems[key].id" in main_tf
    assert tfvars["sfs_filesystems"]["jail"]["block_size_kib"] == 4
    assert tfvars["sfs_filesystems"]["jail"]["forbid_deletion"] is False
    assert tfvars["sfs_filesystems"]["controller-spool"]["block_size_kib"] == 4
    assert tfvars["sfs_filesystems"]["controller-spool"]["forbid_deletion"] is False


@pytest.mark.parametrize("sfs_first", [False, True])
def test_render_target_scoped_sfs_attachments_use_sfs_module_when_ids_match(
    sfs_first: bool,
) -> None:
    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["instance_id"] = "cluster1"
    mk8s["inputs"] = {
        "cluster": {
            "cluster_name": "cluster1",
        },
        "node_groups": {
            "controller": {
                "workload": "controller",
                "node_count": 1,
                "jail": True,
                "sfs_filesystem_keys": ["jail", "controller-spool"],
            },
            "worker": {
                "workload": "worker",
                "node_count": 1,
                "jail": True,
                "sfs_filesystem_keys": ["jail"],
            },
        },
    }
    sfs = _infra_component_row(payload, "sfs")
    sfs["instance_id"] = "cluster1"
    sfs["inputs"] = {
        "filesystems": {
            "jail": {
                "name": "cluster1-jail",
                "size_gib": 1024,
                "mount_tag": "jail",
            },
            "controller-spool": {
                "name": "cluster1-controller-spool",
                "size_gib": 128,
                "mount_tag": "controller-spool",
            },
        }
    }
    payload["infra"]["components"] = [sfs, mk8s] if sfs_first else [mk8s, sfs]
    for chart in payload["apps"]["charts"]:
        if isinstance(chart, dict) and chart.get("instance_id") == "mk8s":
            chart["instance_id"] = "cluster1"
    payload["deploy"]["targets"][0]["instance_id"] = "cluster1"

    plans = _build_module_plans(payload, source_profile=SourceProfile.LOCAL)
    main_tf = "\n\n".join(_render_module_block(plan) for plan in plans)
    sfs_module_name = "cluster1" if sfs_first else "cluster1_2"
    mk8s_module_name = "cluster1_2" if sfs_first else "cluster1"
    assert f'module "{sfs_module_name}" {{' in main_tf
    assert f'module "{mk8s_module_name}" {{' in main_tf
    assert f"mount_tag = module.{sfs_module_name}.filesystems[key].mount_tag" in main_tf
    assert f"id = module.{sfs_module_name}.filesystems[key].id" in main_tf
    assert f"module.{mk8s_module_name}.filesystems[key]" not in main_tf


def test_render_standalone_sfs_preserves_scalar_fields(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"sfs"}, selected_apps=set())
    sfs = _infra_component_row(payload, "sfs")
    sfs["instance_id"] = "shared-scratch"
    sfs["inputs"] = {
        "name": "shared-scratch",
        "size_gib": 2048,
        "block_size_kib": 8,
        "mount_tag": "scratch",
        "forbid_deletion": True,
        "type": "NETWORK_SSD",
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    render_project(load_config(config_path), paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )
    assert 'module "shared_scratch" {' in main_tf
    assert tfvars["shared_scratch_name"] == "shared-scratch"
    assert tfvars["shared_scratch_size_gib"] == 2048
    assert tfvars["shared_scratch_block_size_kib"] == 8
    assert tfvars["shared_scratch_mount_tag"] == "scratch"
    assert tfvars["shared_scratch_forbid_deletion"] is True


def test_render_plain_mk8s_node_group_attach_explicit_sfs_keys(tmp_path: Path) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    paths = resolve_project_paths(config_path)

    payload = _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["instance_id"] = "cluster1"
    mk8s["inputs"] = {
        "cluster": {
            "cluster_name": "cluster1",
        },
        "node_groups": {
            "system": {
                "node_count": 2,
                "gpu": False,
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
                "sfs_filesystem_keys": ["scratch"],
            },
        },
    }
    sfs = _infra_component_row(payload, "sfs")
    sfs["instance_id"] = "sfs"
    sfs["inputs"] = {
        "filesystems": {
            "scratch": {
                "name": "cluster1-scratch",
                "size_gib": 1024,
                "block_size_kib": 8,
                "mount_tag": "scratch",
                "forbid_deletion": True,
            },
        }
    }
    payload["deploy"]["targets"][0]["instance_id"] = "cluster1"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )
    assert 'module "cluster1" {' in main_tf
    assert 'module "sfs" {' in main_tf
    assert 'filesystems = [for key in ["scratch"] : {' in main_tf
    assert "mount_tag = module.sfs.filesystems[key].mount_tag" in main_tf
    assert "id = module.sfs.filesystems[key].id" in main_tf
    assert tfvars["sfs_filesystems"]["scratch"] == {
        "name": "cluster1-scratch",
        "size_gib": 1024,
        "block_size_kib": 8,
        "mount_tag": "scratch",
        "forbid_deletion": True,
    }


def test_render_row_level_vpc_bindings_for_mk8s(monkeypatch: pytest.MonkeyPatch) -> None:
    def _discover_outputs(source: str) -> tuple[ComponentOutput, ...]:
        if "modules/vpc" in str(source):
            return (
                ComponentOutput(
                    name="network_id",
                    kind="terraform_output",
                    source_path="network_id",
                ),
                ComponentOutput(
                    name="subnets",
                    kind="terraform_output",
                    source_path="subnets",
                ),
            )
        return (
            ComponentOutput(
                name="cluster_id",
                kind="terraform_output",
                source_path="cluster_id",
            ),
        )

    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", _discover_outputs)
    reset_component_sources_cache()
    reset_component_entry_cache()

    payload = _starter_payload(selected_infra={"mk8s", "vpc"}, selected_apps=set())
    vpc = _infra_component_row(payload, "vpc")
    vpc["instance_id"] = "cluster1-vpc"
    vpc["inputs"] = {
        "parent_id": "project-123",
        "network": {
            "name": "cluster1-network",
            "ipv4_private_cidrs": ["10.10.0.0/16"],
        },
        "subnets": {
            "worker": {
                "name": "cluster1-worker",
                "use_network_private_pools": False,
                "ipv4_private_cidrs": ["10.10.0.0/24"],
            }
        },
    }
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["instance_id"] = "cluster1"
    mk8s["inputs"] = {
        "cluster": {
            "parent_id": "project-123",
            "cluster_name": "cluster1",
        }
    }
    mk8s["bindings"] = {
        "inputs.cluster.network_id": {
            "source_component": "vpc",
            "source_instance": "cluster1-vpc",
            "source_output": "network_id",
        },
        "inputs.cluster.subnet_id": {
            "source_component": "vpc",
            "source_instance": "cluster1-vpc",
            "source_output": "subnets",
            "key": "worker",
            "attribute": "id",
        },
    }

    plans = _build_module_plans(payload, source_profile=SourceProfile.LOCAL)
    main_tf = "\n\n".join(_render_module_block(plan) for plan in plans)

    assert 'module "cluster1_vpc" {' in main_tf
    assert "\n  inputs = {" not in main_tf
    assert "network_id = module.cluster1_vpc.network_id" in main_tf
    assert 'subnet_id = module.cluster1_vpc.subnets["worker"].id' in main_tf


def test_render_vpc_network_without_subnets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"vpc"}, selected_apps=set())
    vpc = _infra_component_row(payload, "vpc")
    vpc["inputs"] = {
        "parent_id": "project-456",
        "network": {
            "name": "mynetwork",
            "ipv4_private_source_pool_id": "vpcpool-source",
            "ipv4_private_cidrs": ["172.16.0.0/12"],
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=True, type_hint="string"),
            ModuleVariable(name="network", required=True, type_hint="object"),
            ModuleVariable(name="subnets", required=False, type_hint="map(object)"),
        ),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert 'module "vpc" {' in main_tf
    assert "network = var.vpc_network" in main_tf
    assert "parent_id = var.vpc_parent_id" in main_tf
    assert "subnets = " not in main_tf
    assert tfvars["vpc_network"] == {
        "name": "mynetwork",
        "ipv4_private_source_pool_id": "vpcpool-source",
        "ipv4_private_cidrs": ["172.16.0.0/12"],
    }
    assert "vpc_subnets" not in tfvars


def test_render_vpc_declared_subnet_preserves_explicit_private_cidrs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"vpc"}, selected_apps=set())
    vpc = _infra_component_row(payload, "vpc")
    vpc["inputs"] = {
        "parent_id": "project-456",
        "network": {"name": "mynetwork", "ipv4_private_cidrs": ["172.16.0.0/12"]},
        "subnets": {
            "worker": {
                "name": "worker-subnet",
                "use_network_private_pools": False,
                "ipv4_private_cidrs": ["172.16.0.0/16"],
            }
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=True, type_hint="string"),
            ModuleVariable(name="network", required=True, type_hint="object"),
            ModuleVariable(name="subnets", required=False, type_hint="map(object)"),
        ),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert "subnets = var.vpc_subnets" in main_tf
    assert tfvars["vpc_subnets"] == {
        "worker": {
            "name": "worker-subnet",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["172.16.0.0/16"],
        }
    }


def test_render_vpc_explicit_subnet_cidr_preserves_child_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"vpc"}, selected_apps=set())
    vpc = _infra_component_row(payload, "vpc")
    vpc["inputs"] = {
        "parent_id": "project-456",
        "network": {"name": "mynetwork", "ipv4_private_cidrs": ["172.16.0.0/12"]},
        "subnets": {
            "worker": {
                "name": "worker-subnet",
                "use_network_private_pools": False,
                "ipv4_private_cidrs": ["172.16.0.0/16"],
            }
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=True, type_hint="string"),
            ModuleVariable(name="network", required=True, type_hint="object"),
            ModuleVariable(name="subnets", required=False, type_hint="map(object)"),
        ),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert tfvars["vpc_subnets"] == {
        "worker": {
            "name": "worker-subnet",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["172.16.0.0/16"],
        }
    }


def test_render_vpc_preserves_public_pool_and_private_only_subnet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"vpc"}, selected_apps=set())
    vpc = _infra_component_row(payload, "vpc")
    vpc["inputs"] = {
        "parent_id": "project-456",
        "network": {
            "name": "mynetwork",
            "ipv4_private_cidrs": ["172.16.0.0/12"],
            "ipv4_public_pool_ids": ["vpcpool-public"],
        },
        "subnets": {
            "private": {
                "name": "private-subnet",
                "use_network_private_pools": False,
                "ipv4_private_cidrs": ["172.16.0.0/16"],
                "use_network_public_pools": False,
            }
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=True, type_hint="string"),
            ModuleVariable(name="network", required=True, type_hint="object"),
            ModuleVariable(name="subnets", required=False, type_hint="map(object)"),
        ),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert tfvars["vpc_network"] == {
        "name": "mynetwork",
        "ipv4_private_cidrs": ["172.16.0.0/12"],
        "ipv4_public_pool_ids": ["vpcpool-public"],
    }
    assert tfvars["vpc_subnets"] == {
        "private": {
            "name": "private-subnet",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["172.16.0.0/16"],
            "use_network_public_pools": False,
        }
    }


def test_render_vpc_four_subnet_private_pool_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"vpc"}, selected_apps=set())
    vpc = _infra_component_row(payload, "vpc")
    vpc["inputs"] = {
        "parent_id": "project-456",
        "network": {
            "name": "cxcli-tf-172-16-network",
            "ipv4_private_cidrs": ["172.16.0.0/12"],
        },
        "subnets": {
            "subnet1": {
                "name": "subnet1",
                "use_network_private_pools": False,
                "ipv4_private_cidrs": ["172.16.0.0/14"],
            },
            "subnet2": {
                "name": "subnet2",
                "use_network_private_pools": False,
                "ipv4_private_cidrs": ["172.20.0.0/14"],
            },
            "subnet3": {
                "name": "subnet3",
                "use_network_private_pools": False,
                "ipv4_private_cidrs": ["172.24.0.0/14"],
            },
            "subnet4": {
                "name": "subnet4",
                "use_network_private_pools": False,
                "ipv4_private_cidrs": ["172.28.0.0/14"],
            },
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=True, type_hint="string"),
            ModuleVariable(name="network", required=True, type_hint="object"),
            ModuleVariable(name="subnets", required=False, type_hint="map(object)"),
        ),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = yaml.safe_load(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert 'module "vpc" {' in main_tf
    assert "network = var.vpc_network" in main_tf
    assert "subnets = var.vpc_subnets" in main_tf
    assert tfvars["vpc_network"] == {
        "name": "cxcli-tf-172-16-network",
        "ipv4_private_cidrs": ["172.16.0.0/12"],
    }
    assert tfvars["vpc_subnets"] == {
        "subnet1": {
            "name": "subnet1",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["172.16.0.0/14"],
        },
        "subnet2": {
            "name": "subnet2",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["172.20.0.0/14"],
        },
        "subnet3": {
            "name": "subnet3",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["172.24.0.0/14"],
        },
        "subnet4": {
            "name": "subnet4",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["172.28.0.0/14"],
        },
    }


def _install_vpc_output_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    def _discover_outputs(source: str) -> tuple[ComponentOutput, ...]:
        if "modules/vpc" in str(source):
            return (
                ComponentOutput(
                    name="network_id",
                    kind="terraform_output",
                    source_path="network_id",
                ),
                ComponentOutput(
                    name="subnets",
                    kind="terraform_output",
                    source_path="subnets",
                ),
            )
        return (
            ComponentOutput(
                name="instance_id",
                kind="terraform_output",
                source_path="instance_id",
            ),
        )

    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", _discover_outputs)
    reset_component_sources_cache()
    reset_component_entry_cache()


def _planned_vpc_row(payload: dict, *, subnet_key: str) -> None:
    vpc = _infra_component_row(payload, "vpc")
    vpc["instance_id"] = "worker-vpc"
    vpc["inputs"] = {
        "parent_id": "project-123",
        "network": {"name": "worker-network", "ipv4_private_cidrs": ["10.20.0.0/16"]},
        "subnets": {
            subnet_key: {
                "name": f"worker-{subnet_key}",
                "use_network_private_pools": False,
                "ipv4_private_cidrs": ["10.20.0.0/24"],
            }
        },
    }


def _bind_component_to_planned_vpc(row: dict, *, subnet_key: str) -> None:
    row["bindings"] = {
        "inputs.network_id": {
            "source_component": "vpc",
            "source_instance": "worker-vpc",
            "source_output": "network_id",
        },
        "inputs.subnet_id": {
            "source_component": "vpc",
            "source_instance": "worker-vpc",
            "source_output": "subnets",
            "key": subnet_key,
            "attribute": "id",
        },
    }


def test_render_row_level_vpc_bindings_for_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_vpc_output_discovery(monkeypatch)

    payload = _starter_payload(selected_infra={"vm", "vpc"}, selected_apps=set())
    _planned_vpc_row(payload, subnet_key="vm")
    vm = _infra_component_row(payload, "vm")
    vm["instance_id"] = "worker"
    vm["inputs"] = {
        "parent_id": "project-123",
        "name": "worker",
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "source_image_family": "ubuntu24.04",
    }
    _bind_component_to_planned_vpc(vm, subnet_key="vm")

    plans = _build_module_plans(payload, source_profile=SourceProfile.LOCAL)
    main_tf = "\n\n".join(_render_module_block(plan) for plan in plans)

    assert 'module "worker_vpc" {' in main_tf
    assert 'module "worker" {' in main_tf
    assert "\n  inputs = {" not in main_tf
    assert "network_id = module.worker_vpc.network_id" in main_tf
    assert 'subnet_id = module.worker_vpc.subnets["vm"].id' in main_tf


@pytest.mark.parametrize(
    ("component_id", "instance_id", "subnet_key", "required_inputs"),
    [
        (
            "nfs",
            "worker-nfs",
            "nfs",
            {
                "name": "worker-nfs",
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
                "ssh_user_name": "ubuntu",
                "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
                "source_image_family": "ubuntu24.04",
                "data_disk_enabled": True,
                "data_disk_size_gib": 128,
            },
        ),
        (
            "ssh-jumphost",
            "ssh-jumphost",
            "ssh",
            {
                "name": "ssh-jumphost",
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
                "source_image_family": "ubuntu24.04",
                "ssh_user_name": "ubuntu",
                "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
                "allowed_cidrs": ["203.0.113.10/32"],
            },
        ),
        (
            "wireguard-gw",
            "wg-gw",
            "wg",
            {
                "name": "wg-gw",
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
                "source_image_family": "ubuntu24.04",
                "ssh_user_name": "ubuntu",
                "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
                "wireguard_tunnel_cidr": "10.9.0.1/22",
                "local_subnets": ["10.0.0.0/8"],
            },
        ),
    ],
)
def test_render_row_level_vpc_bindings_for_vm_backed_wrappers(
    monkeypatch: pytest.MonkeyPatch,
    component_id: str,
    instance_id: str,
    subnet_key: str,
    required_inputs: dict[str, object],
) -> None:
    _install_vpc_output_discovery(monkeypatch)

    payload = _starter_payload(selected_infra={component_id, "vpc"}, selected_apps=set())
    _planned_vpc_row(payload, subnet_key=subnet_key)
    component = _infra_component_row(payload, component_id)
    component["instance_id"] = instance_id
    component["inputs"] = {
        "parent_id": "project-123",
        **required_inputs,
    }
    _bind_component_to_planned_vpc(component, subnet_key=subnet_key)

    plans = _build_module_plans(payload, source_profile=SourceProfile.LOCAL)
    main_tf = "\n\n".join(_render_module_block(plan) for plan in plans)

    assert 'module "worker_vpc" {' in main_tf
    assert f'module "{instance_id.replace("-", "_")}" {{' in main_tf
    assert "\n  inputs = {" not in main_tf
    assert "network_id = module.worker_vpc.network_id" in main_tf
    assert f'subnet_id = module.worker_vpc.subnets["{subnet_key}"].id' in main_tf


def test_render_vm_sfs_attachments_from_planned_sfs() -> None:
    payload = _starter_payload(selected_infra={"vm", "sfs"}, selected_apps=set())
    sfs = _infra_component_row(payload, "sfs")
    sfs["instance_id"] = "worker-sfs"
    sfs["inputs"] = {
        "filesystems": {
            "scratch": {
                "name": "worker-scratch",
                "size_gib": 1024,
                "mount_tag": "scratch",
            }
        }
    }
    vm = _infra_component_row(payload, "vm")
    vm["instance_id"] = "worker"
    vm["inputs"] = {
        "parent_id": "project-123",
        "network_id": "vpcnetwork-live",
        "subnet_id": "vpcsubnet-live",
        "name": "worker",
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "source_image_family": "ubuntu24.04",
        "sfs_attachments": [
            {
                "source_instance": "worker-sfs",
                "keys": ["scratch"],
                "attach_mode": "READ_WRITE",
            }
        ],
    }

    plans = _build_module_plans(payload, source_profile=SourceProfile.LOCAL)
    main_tf = "\n\n".join(_render_module_block(plan) for plan in plans)

    assert 'module "worker_sfs" {' in main_tf
    assert 'filesystems = [for key in ["scratch"] : {' in main_tf
    assert "mount_tag = module.worker_sfs.filesystems[key].mount_tag" in main_tf
    assert "id = module.worker_sfs.filesystems[key].id" in main_tf
    assert "sfs_attachments" not in main_tf


def test_render_dynamic_chart_shape_writes_flux_manifests(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    runtime_payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    dynamic_payload = to_dynamic_payload(runtime_payload)
    dynamic_payload["apps"]["charts"] = [
        {
            "id": "runtime-app",
            "instance_id": "mk8s",
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

    repo_sources = _target_flux_dir(paths) / "helm-repositories.yaml"
    release = _target_flux_dir(paths) / "helmrelease-workloads-runtime-app.yaml"
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
        mk8s_inputs.update(_mk8s_inputs(subnet_id="subnet-abc123", cpu=True, gpu=False))
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
    stale_report = paths.reports_dir / "old.json"
    stale_top_level = paths.generated_dir / "obsolete.txt"
    stale_tf.parent.mkdir(parents=True, exist_ok=True)
    bootstrap_flux_dir.mkdir(parents=True, exist_ok=True)
    stale_report.parent.mkdir(parents=True, exist_ok=True)
    stale_tf.write_text('resource "null_resource" "stale" {}\n', encoding="utf-8")
    bootstrap_sync.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    bootstrap_components.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    bootstrap_kustomization.write_text(
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n- ./gotk-components.yaml\n- ./gotk-sync.yaml\n",
        encoding="utf-8",
    )
    stale_flux_file.write_text("apiVersion: v1\nkind: Secret\n", encoding="utf-8")
    stale_report.write_text("{}\n", encoding="utf-8")
    stale_top_level.write_text("obsolete\n", encoding="utf-8")

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    assert not stale_tf.exists()
    assert not stale_flux_file.exists()
    assert not stale_report.exists()
    assert not stale_top_level.exists()
    assert not bootstrap_sync.exists()
    assert not bootstrap_components.exists()
    assert not bootstrap_kustomization.exists()
    assert (paths.infra_dir / "main.tf").exists()
    kustomization_doc = yaml.safe_load(
        (_target_flux_dir(paths) / "kustomization.yaml").read_text(encoding="utf-8")
    )
    assert "./flux-system" not in kustomization_doc["resources"]
    assert not (paths.reports_dir / "deploy-report.md").exists()


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

    runtime_payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    dynamic_payload = to_dynamic_payload(runtime_payload)
    dynamic_payload["apps"]["charts"] = [
        {
            "id": "gateway-helm",
            "instance_id": "mk8s",
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

    repo_sources = _target_flux_dir(paths) / "helm-repositories.yaml"
    namespace_manifest = _target_flux_dir(paths) / "namespace-envoy-gateway-system.yaml"
    release = _target_flux_dir(paths) / "helmrelease-platform-envoy-gateway.yaml"
    kustomization = _target_flux_dir(paths) / "kustomization.yaml"
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
    vm_dashboard_dir = paths.generated_dir / "grafana_dashboards" / "mk8s" / "nebius-vm"
    vm_dashboard_files = {
        item.name
        for item in vm_dashboard_dir.iterdir()
        if item.is_file() and item.suffix == ".json"
    }
    assert dashboard_files == {
        "kubernetes-cluster-monitoring.json",
        "kubernetes-gpu.json",
        "kubernetes-logs-from-loki.json",
        "kubernetes-traces.json",
    }
    assert vm_dashboard_files == {
        "vm-metrics.json",
        "vm-logs.json",
    }
    assert dashboard_dir / "kubernetes-cluster-monitoring.json" in written
    assert vm_dashboard_dir / "vm-metrics.json" in written

    flux_dir = _target_flux_dir(paths)
    configmap = flux_dir / "configmap-grafana-nebius-kubernetes-dashboards.yaml"
    vm_configmap = flux_dir / "configmap-grafana-nebius-vm-dashboards.yaml"
    release = flux_dir / "helmrelease-observability-grafana.yaml"
    kustomization = flux_dir / "kustomization.yaml"
    assert configmap.exists()
    assert vm_configmap.exists()
    assert release.exists()

    configmap_doc = yaml.safe_load(configmap.read_text(encoding="utf-8"))
    assert configmap_doc["metadata"] == {
        "name": "grafana-nebius-kubernetes-dashboards",
        "namespace": "observability",
    }
    assert set(configmap_doc["data"]) == dashboard_files
    vm_configmap_doc = yaml.safe_load(vm_configmap.read_text(encoding="utf-8"))
    assert vm_configmap_doc["metadata"] == {
        "name": "grafana-nebius-vm-dashboards",
        "namespace": "observability",
    }
    assert set(vm_configmap_doc["data"]) == vm_dashboard_files

    release_doc = yaml.safe_load(release.read_text(encoding="utf-8"))
    values = release_doc["spec"]["values"]
    assert values["dashboardsConfigMaps"] == {
        "nebius-kubernetes": "grafana-nebius-kubernetes-dashboards",
        "nebius-vm": "grafana-nebius-vm-dashboards",
    }
    assert set(values["dashboards"]["nebius"]) == {"nebius-disk"}
    assert "json:" not in yaml.safe_dump(values["dashboards"], sort_keys=False)

    kustomization_doc = yaml.safe_load(kustomization.read_text(encoding="utf-8"))
    assert "./configmap-grafana-nebius-kubernetes-dashboards.yaml" in kustomization_doc["resources"]
    assert "./configmap-grafana-nebius-vm-dashboards.yaml" in kustomization_doc["resources"]
    assert kustomization_doc["resources"].index(
        "./configmap-grafana-nebius-kubernetes-dashboards.yaml"
    ) < kustomization_doc["resources"].index("./helmrelease-observability-grafana.yaml")
    assert kustomization_doc["resources"].index(
        "./configmap-grafana-nebius-vm-dashboards.yaml"
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

    runtime_payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    dynamic_payload = to_dynamic_payload(runtime_payload)
    dynamic_payload["apps"]["charts"] = [
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
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

    repo_sources = _target_flux_dir(paths) / "helm-repositories.yaml"
    release = _target_flux_dir(paths) / "helmrelease-platform-network-operator.yaml"
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
    mk8s_dir = _write_minimal_mk8s_module(tmp_path)
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            _catalog(
                infra={"mk8s": _minimal_mk8s_catalog_entry(mk8s_dir)},
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
                },
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _set_catalog_override(sources_file, source_profile=SourceProfile.PORTABLE)
    monkeypatch.chdir(tmp_path)

    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"demo-app"})
    _retarget_enabled_apps(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.PORTABLE)

    release_doc = yaml.safe_load(
        (_target_flux_dir(paths) / "helmrelease-workloads-demo-app.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert release_doc["spec"]["timeout"] == "10m"


def test_render_uses_global_flux_release_timeout_when_chart_omits_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mk8s_dir = _write_minimal_mk8s_module(tmp_path)
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            _catalog(
                infra={"mk8s": _minimal_mk8s_catalog_entry(mk8s_dir)},
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
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"demo-app"})
    _retarget_enabled_apps(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.PORTABLE)

    release_doc = yaml.safe_load(
        (_target_flux_dir(paths) / "helmrelease-workloads-demo-app.yaml").read_text(
            encoding="utf-8"
        )
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
        _mk8s_inputs(
            cluster_name="cluster1",
            subnet_id="subnet-abc123",
            cpu=True,
            gpu=True,
            gpu_platform="gpu-b300-sxm",
            gpu_preset="8gpu-192vcpu-2768gb",
            gpu_stack_source="nebius_image",
        )
    )
    _align_infra_resource_name(payload, mk8s, "cluster1")
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: _mk8s_module_variables(),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    release_doc = yaml.safe_load(
        (_target_flux_dir(paths, "cluster1") / "helmrelease-platform-gpu-operator.yaml").read_text(
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
        _mk8s_inputs(
            cluster_name="cluster1",
            subnet_id="subnet-abc123",
            cpu=True,
            gpu=True,
            gpu_platform="gpu-b300-sxm",
            gpu_preset="8gpu-192vcpu-2768gb",
            gpu_stack_source="nebius_image",
            infiniband_fabric="fabric-1",
        )
    )
    _align_infra_resource_name(payload, mk8s, "cluster1")
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: _mk8s_module_variables(),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    target_flux_dir = _target_flux_dir(paths, "cluster1")
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
        _mk8s_inputs(
            cluster_name="cluster1",
            subnet_id="subnet-abc123",
            cpu=True,
            gpu=True,
            gpu_platform="gpu-b200-sxm",
            gpu_preset="8gpu-192vcpu-2768gb",
            gpu_stack_source="operator_managed",
        )
    )
    _align_infra_resource_name(payload, mk8s, "cluster1")
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: _mk8s_module_variables(),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    target_flux_dir = _target_flux_dir(paths, "cluster1")
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
        _mk8s_inputs(
            cluster_name="cluster1",
            subnet_id="subnet-abc123",
            cpu=True,
            gpu=True,
            gpu_platform="gpu-b300-sxm",
            gpu_preset="8gpu-192vcpu-2768gb",
            gpu_stack_source="operator_managed",
            infiniband_fabric="fabric-1",
        )
    )
    _align_infra_resource_name(payload, mk8s, "cluster1")
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.infra_render.module_variables",
        lambda _source: _mk8s_module_variables(),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    materialize_mk8s_gpu_app_values(config)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    target_flux_dir = _target_flux_dir(paths, "cluster1")
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
        **_mk8s_inputs(
            cluster_name="demo-cluster",
            subnet_id="subnet-123",
            cpu=True,
            gpu=False,
        ),
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
    }
    _align_infra_resource_name(payload, mk8s, "demo-cluster")
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
    mk8s["inputs"] = _mk8s_inputs(
        cluster_name="demo-cluster",
        subnet_id="subnet-123",
        cpu=True,
        gpu=True,
        gpu_platform="gpu-h100-sxm",
        gpu_preset="8gpu-128vcpu-1600gb",
    )
    _align_infra_resource_name(payload, mk8s, "demo-cluster")
    payload["deploy"] = {
        "targets": [
            {
                "instance_id": "demo-cluster",
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
        lambda _source: _mk8s_module_variables(),
    )

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    tfvars = (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    assert "gpu_validation_overrides" not in tfvars


def test_render_uses_materialized_shared_admin_ssh_username_for_wireguard_gw(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    _set_catalog_override(
        _catalog_with_shared_admin_ssh(tmp_path, user_name="adminuser"),
        source_profile=SourceProfile.LOCAL,
    )
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"wireguard-gw"}, selected_apps=set())
    wireguard = _infra_component_row(payload, "wireguard-gw")
    wireguard["inputs"] = {
        "parent_id": "project-456",
        "network_id": "vpcnetwork-123",
        "subnet_id": "subnet-123",
        "name": "wg-gw",
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "adminuser",
        "wireguard_tunnel_cidr": "10.9.0.1/22",
        "local_subnets": ["10.0.0.0/8"],
    }
    _align_infra_resource_name(payload, wireguard, "wg-gw")
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    render_project(config, paths, source_profile=SourceProfile.LOCAL)

    main_tf = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    tfvars = (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")

    assert 'module "wg_gw" {' in main_tf
    assert "ssh_user_name = var.wg_gw_ssh_user_name" in main_tf
    assert "network_id = var.wg_gw_network_id" in main_tf
    assert '"wg_gw_ssh_user_name": "adminuser"' in tfvars
    assert '"wg_gw_network_id": "vpcnetwork-123"' in tfvars
    assert '"wg_gw_subnet_id": "subnet-123"' in tfvars
    assert '"wg_gw_wireguard_tunnel_cidr": "10.9.0.1/22"' in tfvars
    assert "wg_gw_ssh_public_key" not in tfvars


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
        "network_id": "vpcnetwork-123",
        "subnet_id": "subnet-123",
        "name": "ssh-jumphost",
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
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
    assert "network_id = var.ssh_jumphost_network_id" in main_tf
    assert '"ssh_jumphost_ssh_user_name": "adminuser"' in tfvars
    assert '"ssh_jumphost_network_id": "vpcnetwork-123"' in tfvars
    assert '"ssh_jumphost_subnet_id": "subnet-123"' in tfvars


def test_render_uses_materialized_shared_defaults_for_app_chart_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mk8s_dir = _write_minimal_mk8s_module(tmp_path)
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
                infra={"mk8s": _minimal_mk8s_catalog_entry(mk8s_dir)},
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
        "deploy": {"targets": [{"instance_id": "mk8s"}]},
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
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
                    "instance_id": "mk8s",
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
        (_target_flux_dir(paths) / "helmrelease-workloads-demo-app.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert release_doc["spec"]["values"]["admin"]["sshUser"] == "adminuser"


def test_render_supports_infra_input_binding_from_component_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mk8s_dir = _write_minimal_mk8s_module(tmp_path)
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
                    "mk8s": _minimal_mk8s_catalog_entry(mk8s_dir),
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
        "deploy": {"targets": [{"instance_id": "mk8s"}]},
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "source": str(mk8s_dir),
                    "inputs": {},
                },
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
    mk8s_dir = _write_minimal_mk8s_module(tmp_path)
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
                    "mk8s": _minimal_mk8s_catalog_entry(mk8s_dir),
                    "producer": {
                        "source": {
                            "portable": "git::https://github.com/example/infra.git//modules/producer?ref=v1.2.3",
                            "local": str(producer_dir),
                        }
                    },
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
        "deploy": {"targets": [{"instance_id": "mk8s"}]},
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "source": str(mk8s_dir),
                    "inputs": {},
                },
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
                    "instance_id": "mk8s",
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
        (_target_flux_dir(paths) / "helmrelease-workloads-demo-app.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert release_doc["spec"]["values"]["global"]["upstreamId"] == "instance-blue"


def test_render_uses_component_source_defaults_when_config_omits_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mk8s_dir = _write_minimal_mk8s_module(tmp_path)
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
                    "mk8s": _minimal_mk8s_catalog_entry(mk8s_dir),
                    "demo-module": {
                        "source": {
                            "portable": "git::https://github.com/example/infra.git//modules/demo-module?ref=v1.2.3",
                            "local": str(module_dir),
                        },
                        "defaults": {
                            "inputs.cluster_name": "demo-cluster",
                            "inputs.cpu_nodes_count": 3,
                        },
                    },
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
        "deploy": {"targets": [{"instance_id": "mk8s"}]},
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "source": str(mk8s_dir),
                    "inputs": {},
                },
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
                    "instance_id": "mk8s",
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
        (_target_flux_dir(paths) / "helmrelease-workloads-demo-app.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert release_doc["spec"]["values"]["replicaCount"] == 2
    assert release_doc["spec"]["values"]["image"]["tag"] == "stable"
