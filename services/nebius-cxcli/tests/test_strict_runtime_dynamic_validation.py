from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nebius_cxcli.cli import _validate_enabled_chart_sources, _validate_strict_config
from nebius_cxcli.component_sources import (
    SourceProfile,
    reset_component_sources_cache,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.runtime_introspection import reset_runtime_introspection_cache

_VALID_ED25519_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f demo@example"
)


@pytest.fixture(autouse=True)
def _reset_component_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE", raising=False)
    set_component_sources_file_override(None)
    set_component_sources_profile_override(SourceProfile.LOCAL)
    reset_component_sources_cache()
    reset_runtime_introspection_cache()
    reset_component_entry_cache()


def _starter_payload(*, selected_infra: set[str], selected_apps: set[str]) -> dict:
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="us-central1",
            email="ops@example.com",
            selected_infra=selected_infra,
            selected_apps=selected_apps,
            infra_entries=component_entries("infra"),
            app_entries=component_entries("apps"),
        )
    )
    assert isinstance(payload, dict)
    return payload


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


def _align_infra_resource_name(payload: dict, row: dict, resource_name: str) -> None:
    component_id = str(row.get("id", "")).strip().lower()
    old_instance_id = str(row.get("instance_id", "")).strip()
    row["instance_id"] = resource_name
    inputs = row.setdefault("inputs", {})
    assert isinstance(inputs, dict)
    inputs["cluster_name" if component_id == "mk8s" else "name"] = resource_name
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


def _infra_component_path(component_id: str, instance_name: str) -> str:
    label = component_id if component_id == instance_name else f"{component_id}@{instance_name}"
    return f"infra.components[{label}]"


def _catalog_with_shared_admin_ssh(
    tmp_path: Path,
    *,
    user_name: str = "ubuntu",
    public_key: str = _VALID_ED25519_PUBLIC_KEY,
) -> Path:
    source_catalog = Path(__file__).resolve().parents[1] / "component_sources.yaml"
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
    settings_path = Path(__file__).resolve().parents[1] / "component_cli_settings.yaml"
    override_path.with_name("component_cli_settings.yaml").write_text(
        settings_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return override_path


def test_strict_validation_requires_enabled_module_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["inputs"] = {}

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("platform", "preset"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    message = str(exc_info.value)
    assert "infra.components[mk8s].inputs.platform is required" in message
    assert "infra.components[mk8s].inputs.preset is required" in message


def test_strict_validation_checks_dynamic_custom_component_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _starter_payload(selected_infra=set(), selected_apps=set())
    payload["infra"]["components"] = [
        {
            "id": "runtime-custom",
            "instance_id": "runtime-custom",
            "enabled": True,
            "source": "",
            "inputs": {},
        }
    ]
    payload["apps"]["charts"] = []

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    assert "infra.components[runtime-custom] is enabled but has no module source configured" in str(
        exc_info.value
    )


def test_strict_validation_rejects_unknown_custom_module_inputs(tmp_path: Path) -> None:
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    _align_infra_resource_name(payload, mk8s, "demo-cluster")
    mk8s["inputs"] = {
        "parent_id": "project-456",
        "cluster_name": "demo-cluster",
        "subnet_id": "subnet-123",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    assert (
        f"{_infra_component_path('mk8s', 'demo-cluster')}.inputs.ssh_public_key "
        "is not declared by module"
    ) in str(exc_info.value)


def test_strict_validation_allows_mk8s_wizard_helper_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    _align_infra_resource_name(payload, mk8s, "demo-cluster")
    mk8s["inputs"] = {
        "cluster": {
            "parent_id": "project-456",
            "cluster_name": "demo-cluster",
            "network_id": "network-123",
            "subnet_id": "subnet-123",
            "k8s_version": "1.32",
            "public_endpoint": True,
        },
        "node_groups": {
            "system": {
                "node_count": 1,
                "platform": "cpu-d3",
                "preset": "16vcpu-64gb",
            }
        },
        "node_group_defaults": {
            "cpu": {
                "platform": "cpu-d3",
                "preset": "16vcpu-64gb",
            }
        },
        "soperator": {
            "worker_total_nodes": 2,
            "worker_nodes_per_group": 100,
        },
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster", "node_groups"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_output_names",
        lambda _source: ("cluster_id", "cluster_ca_certificate", "instance_id"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    _validate_strict_config(config)


def test_strict_validation_requires_managed_postgresql_name_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _starter_payload(selected_infra={"managed-postgresql"}, selected_apps=set())
    managed_pg = _infra_component_row(payload, "managed-postgresql")
    managed_pg["inputs"] = {
        "parent_id": "project-456",
        "network_id": "vpcnetwork-123",
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("parent_id", "network_id"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    assert "infra.components[managed-postgresql].inputs.name is required" in str(exc_info.value)


def test_strict_validation_requires_mk8s_cpu_shape_when_baseline_pool_is_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    _align_infra_resource_name(payload, mk8s, "demo-cluster")
    mk8s["inputs"] = {
        "cluster": {
            "parent_id": "project-456",
            "cluster_name": "demo-cluster",
            "network_id": "network-123",
            "subnet_id": "subnet-123",
            "k8s_version": "1.32",
            "public_endpoint": True,
        },
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster", "node_groups"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_output_names",
        lambda _source: ("cluster_id", "cluster_ca_certificate", "instance_id"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    message = str(exc_info.value)
    mk8s_path = _infra_component_path("mk8s", "demo-cluster")
    assert f"{mk8s_path}.inputs.node_groups is required" in message


def test_strict_validation_rejects_removed_mk8s_gpu_shortcut_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _starter_payload(
        selected_infra={"mk8s"},
        selected_apps={"nvidia-gpu-operator"},
    )
    mk8s = _infra_component_row(payload, "mk8s")
    _align_infra_resource_name(payload, mk8s, "demo-cluster")
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

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster", "node_groups"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variable_names",
        lambda _source: ("cluster", "node_groups"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_output_names",
        lambda _source: ("cluster_id", "cluster_ca_certificate", "instance_id"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config, include_common_checks=False)
    message = str(exc_info.value)
    mk8s_path = _infra_component_path("mk8s", "demo-cluster")
    assert f"{mk8s_path}.inputs.cluster_name is not declared" in message
    assert f"{mk8s_path}.inputs.gpu_enabled is not declared" in message
    assert f"{mk8s_path}.inputs.gpu_nodes_platform is not declared" in message


def test_strict_validation_requires_object_storage_name_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _starter_payload(selected_infra={"object-storage"}, selected_apps=set())
    object_storage = _infra_component_row(payload, "object-storage")
    object_storage["inputs"] = {
        "parent_id": "project-456",
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("parent_id",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    assert "infra.components[object-storage].inputs.name is required" in str(exc_info.value)


def test_strict_validation_ssh_jumphost_requires_allowed_cidrs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _starter_payload(selected_infra={"ssh-jumphost"}, selected_apps=set())
    jumphost = _infra_component_row(payload, "ssh-jumphost")
    _align_infra_resource_name(payload, jumphost, "ssh-jh")
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "network_id": "network-123",
        "subnet_id": "subnet-123",
        "name": "ssh-jh",
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: (
            "parent_id",
            "subnet_id",
            "name",
            "platform",
            "preset",
            "source_image_family",
            "ssh_public_key",
            "allowed_cidrs",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    assert (
        f"{_infra_component_path('ssh-jumphost', 'ssh-jh')}.inputs.allowed_cidrs is required"
    ) in str(exc_info.value)


def test_strict_validation_mysterybox_requires_secrets_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _starter_payload(selected_infra={"mysterybox"}, selected_apps=set())
    mysterybox = _infra_component_row(payload, "mysterybox")
    mysterybox["inputs"] = {
        "parent_id": "project-456",
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("parent_id", "secrets"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    try:
        _validate_strict_config(config)
        message = ""
    except RuntimeError as exc:
        message = str(exc)
    assert "infra.components[mysterybox].inputs.secrets is required" in message


@pytest.mark.parametrize(
    ("component_id", "instance_name", "extra_inputs"),
    [
        ("wireguard-gw", "wg-gw", {"local_subnets": ["10.0.0.0/8"]}),
        ("ssh-jumphost", "ssh-jh", {"allowed_cidrs": ["203.0.113.10/32"]}),
    ],
)
def test_strict_validation_allows_explicit_ssh_public_key_for_jumphost(
    tmp_path: Path,
    component_id: str,
    instance_name: str,
    extra_inputs: dict[str, object],
) -> None:
    set_component_sources_file_override(_catalog_with_shared_admin_ssh(tmp_path))
    reset_component_sources_cache()
    reset_runtime_introspection_cache()
    reset_component_entry_cache()
    payload = _starter_payload(selected_infra={component_id}, selected_apps=set())
    jumphost = _infra_component_row(payload, component_id)
    _align_infra_resource_name(payload, jumphost, instance_name)
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "network_id": "network-123",
        "subnet_id": "subnet-123",
        "name": instance_name,
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        **extra_inputs,
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    _validate_strict_config(config)


@pytest.mark.parametrize(
    ("component_id", "instance_name", "extra_inputs"),
    [
        ("wireguard-gw", "wg-gw", {"local_subnets": ["10.0.0.0/8"]}),
        ("ssh-jumphost", "ssh-jh", {"allowed_cidrs": ["203.0.113.10/32"]}),
    ],
)
def test_strict_validation_rejects_missing_ssh_public_key_for_jumphost(
    tmp_path: Path,
    component_id: str,
    instance_name: str,
    extra_inputs: dict[str, object],
) -> None:
    payload = _starter_payload(selected_infra={component_id}, selected_apps=set())
    jumphost = _infra_component_row(payload, component_id)
    _align_infra_resource_name(payload, jumphost, instance_name)
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "network_id": "network-123",
        "subnet_id": "subnet-123",
        "name": instance_name,
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
        **extra_inputs,
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)

    assert (
        f"{_infra_component_path(component_id, instance_name)}.inputs.ssh_public_key is required"
        in str(exc_info.value)
    )


@pytest.mark.parametrize(
    ("component_id", "instance_name", "extra_inputs"),
    [
        ("wireguard-gw", "wg-gw", {"local_subnets": ["10.0.0.0/8"]}),
        ("ssh-jumphost", "ssh-jh", {"allowed_cidrs": ["203.0.113.10/32"]}),
    ],
)
def test_strict_validation_requires_existing_public_ip_allocation_id_for_jump_host(
    tmp_path: Path,
    component_id: str,
    instance_name: str,
    extra_inputs: dict[str, object],
) -> None:
    payload = _starter_payload(selected_infra={component_id}, selected_apps=set())
    jumphost = _infra_component_row(payload, component_id)
    _align_infra_resource_name(payload, jumphost, instance_name)
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "network_id": "network-123",
        "subnet_id": "subnet-123",
        "name": instance_name,
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "create_public_ip_allocation": False,
        **extra_inputs,
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)

    assert (
        f"{_infra_component_path(component_id, instance_name)}.inputs.public_ip_allocation_id is required"
        in str(exc_info.value)
    )


@pytest.mark.parametrize(
    ("component_id", "instance_name", "extra_inputs"),
    [
        ("wireguard-gw", "wg-gw", {"local_subnets": ["10.0.0.0/8"]}),
        ("ssh-jumphost", "ssh-jh", {"allowed_cidrs": ["203.0.113.10/32"]}),
    ],
)
def test_strict_validation_rejects_public_ip_allocation_id_when_jump_host_creates_one(
    tmp_path: Path,
    component_id: str,
    instance_name: str,
    extra_inputs: dict[str, object],
) -> None:
    payload = _starter_payload(selected_infra={component_id}, selected_apps=set())
    jumphost = _infra_component_row(payload, component_id)
    _align_infra_resource_name(payload, jumphost, instance_name)
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "network_id": "network-123",
        "subnet_id": "subnet-123",
        "name": instance_name,
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "create_public_ip_allocation": True,
        "public_ip_allocation_id": "allocation-123",
        **extra_inputs,
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)

    assert (
        f"{_infra_component_path(component_id, instance_name)}.inputs.create_public_ip_allocation must be false "
        "when inputs.public_ip_allocation_id is set"
    ) in str(exc_info.value)


@pytest.mark.parametrize(
    ("component_id", "instance_name", "extra_inputs"),
    [
        ("wireguard-gw", "wg-gw", {"local_subnets": ["10.0.0.0/8"]}),
        ("ssh-jumphost", "ssh-jh", {"allowed_cidrs": ["203.0.113.10/32"]}),
    ],
)
def test_strict_validation_rejects_invalid_public_ip_allocation_name_for_jump_host(
    tmp_path: Path,
    component_id: str,
    instance_name: str,
    extra_inputs: dict[str, object],
) -> None:
    payload = _starter_payload(selected_infra={component_id}, selected_apps=set())
    jumphost = _infra_component_row(payload, component_id)
    _align_infra_resource_name(payload, jumphost, instance_name)
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "network_id": "network-123",
        "subnet_id": "subnet-123",
        "name": instance_name,
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "create_public_ip_allocation": True,
        "public_ip_allocation_name": "jumpHost_Ip",
        **extra_inputs,
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)

    assert (
        f"{_infra_component_path(component_id, instance_name)}.inputs.public_ip_allocation_name "
        "must use lowercase letters, digits, and hyphens"
    ) in str(exc_info.value)


@pytest.mark.parametrize(
    ("component_id", "instance_name", "extra_inputs"),
    [
        ("vm", "vm", {}),
        ("wireguard-gw", "wg-gw", {"local_subnets": ["10.0.0.0/8"]}),
        ("ssh-jumphost", "ssh-jh", {"allowed_cidrs": ["203.0.113.10/32"]}),
        (
            "nfs",
            "nfs",
            {
                "export_path": "/srv/nfs",
                "client_cidrs": ["10.0.0.0/8"],
                "data_disk_enabled": True,
                "data_disk_size_gib": 128,
                "data_disk_type": "NETWORK_SSD",
            },
        ),
    ],
)
def test_strict_validation_rejects_boot_disk_encryption_on_unsupported_disk_type(
    tmp_path: Path,
    component_id: str,
    instance_name: str,
    extra_inputs: dict[str, object],
) -> None:
    payload = _starter_payload(selected_infra={component_id}, selected_apps=set())
    component = _infra_component_row(payload, component_id)
    _align_infra_resource_name(payload, component, instance_name)
    component["inputs"] = {
        "parent_id": "project-456",
        "network_id": "network-123",
        "subnet_id": "subnet-123",
        "name": instance_name,
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "boot_disk_size_gib": 64,
        "boot_disk_type": "NETWORK_SSD",
        "boot_disk_encryption_enabled": True,
        **extra_inputs,
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)

    assert (
        f"{_infra_component_path(component_id, instance_name)}.inputs.boot_disk_encryption_enabled can be true "
        "only for boot disk types that support explicit encryption"
    ) in str(exc_info.value)


@pytest.mark.parametrize(
    ("component_id", "instance_name", "extra_inputs"),
    [
        ("vm", "vm", {}),
        (
            "nfs",
            "nfs",
            {
                "export_path": "/srv/nfs",
                "client_cidrs": ["10.0.0.0/8"],
                "data_disk_enabled": True,
                "data_disk_size_gib": 128,
                "data_disk_type": "NETWORK_SSD",
            },
        ),
    ],
)
def test_strict_validation_rejects_created_disk_security_flags_with_existing_boot_disk(
    tmp_path: Path,
    component_id: str,
    instance_name: str,
    extra_inputs: dict[str, object],
) -> None:
    payload = _starter_payload(selected_infra={component_id}, selected_apps=set())
    component = _infra_component_row(payload, component_id)
    _align_infra_resource_name(payload, component, instance_name)
    component["inputs"] = {
        "parent_id": "project-456",
        "network_id": "network-123",
        "subnet_id": "subnet-123",
        "name": instance_name,
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "boot_disk_existing_id": "disk-123",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "boot_disk_encryption_enabled": True,
        "boot_disk_deletion_protection": True,
        **extra_inputs,
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)

    assert (
        f"{_infra_component_path(component_id, instance_name)}.inputs.boot_disk_encryption_enabled and "
        "inputs.boot_disk_deletion_protection apply only when cxcli creates the boot disk"
    ) in str(exc_info.value)


def test_strict_validation_rejects_data_disk_encryption_on_unsupported_disk_type(
    tmp_path: Path,
) -> None:
    payload = _starter_payload(selected_infra={"vm"}, selected_apps=set())
    component = _infra_component_row(payload, "vm")
    component["inputs"] = {
        "parent_id": "project-456",
        "network_id": "network-123",
        "subnet_id": "subnet-123",
        "name": "vm",
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "boot_disk_size_gib": 64,
        "boot_disk_type": "NETWORK_SSD",
        "data_disk_enabled": True,
        "data_disk_size_gib": 128,
        "data_disk_type": "NETWORK_SSD",
        "data_disk_encryption_enabled": True,
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)

    assert (
        "infra.components[vm].inputs.data_disk_encryption_enabled can be true only "
        "for data disk types that support explicit encryption"
    ) in str(exc_info.value)


def test_strict_validation_rejects_unaligned_high_performance_data_disk_size(
    tmp_path: Path,
) -> None:
    payload = _starter_payload(selected_infra={"vm"}, selected_apps=set())
    component = _infra_component_row(payload, "vm")
    component["inputs"] = {
        "parent_id": "project-456",
        "network_id": "network-123",
        "subnet_id": "subnet-123",
        "name": "vm",
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "boot_disk_size_gib": 64,
        "boot_disk_type": "NETWORK_SSD",
        "data_disk_enabled": True,
        "data_disk_size_gib": 128,
        "data_disk_type": "NETWORK_SSD_IO_M3",
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)

    assert (
        "infra.components[vm].inputs.data_disk_size_gib must be a multiple "
        "of 93 GiB for NETWORK_SSD_IO_M3"
    ) in str(exc_info.value)


def test_strict_validation_rejects_missing_local_custom_module_source_dir(tmp_path: Path) -> None:
    payload = _starter_payload(selected_infra=set(), selected_apps=set())
    payload["infra"]["components"] = [
        {
            "id": "runtime-custom",
            "instance_id": "runtime-custom",
            "enabled": True,
            "source": str(tmp_path / "missing-module"),
            "inputs": {},
        }
    ]

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    assert "does not resolve to an existing local directory" in str(exc_info.value)


def test_strict_validation_rejects_local_custom_module_source_without_tf_files(
    tmp_path: Path,
) -> None:
    module_dir = tmp_path / "empty-module"
    module_dir.mkdir()

    payload = _starter_payload(selected_infra=set(), selected_apps=set())
    payload["infra"]["components"] = [
        {
            "id": "runtime-custom",
            "instance_id": "runtime-custom",
            "enabled": True,
            "source": str(module_dir),
            "inputs": {},
        }
    ]

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    assert "has no Terraform .tf files" in str(exc_info.value)


def test_validate_enabled_chart_sources_reports_lookup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"n8n"})
    _retarget_enabled_apps(payload)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli._helm_chart_validation_issues",
        lambda **_kwargs: ("simulated lookup failure",),
    )

    issues = _validate_enabled_chart_sources(config)
    assert any("apps.charts[n8n@mk8s]" in issue for issue in issues)
    assert any("simulated lookup failure" in issue for issue in issues)


def test_validate_enabled_chart_sources_uses_catalog_chart_name_for_oci_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    network_operator = next(
        entry for entry in component_entries("apps") if entry.id == "nvidia-network-operator"
    )
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "group": "platform",
            "repo": network_operator.source,
            "version": network_operator.version,
            "namespace": network_operator.default_namespace,
            "release-name": network_operator.default_release_name,
            "values": {},
        }
    ]

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    captured: dict[str, str] = {}

    def _fake_validate(
        *,
        chart_name: str,
        chart_repo: str,
        chart_version: str,
        chart_meta_cache,
    ) -> tuple[str, ...]:
        _ = chart_meta_cache
        captured["chart_name"] = chart_name
        captured["chart_repo"] = chart_repo
        captured["chart_version"] = chart_version
        return ()

    monkeypatch.setattr("nebius_cxcli.cli._resolve_helm_chart_validation_issues", _fake_validate)

    issues = _validate_enabled_chart_sources(config, chart_meta_cache={})
    assert issues == []
    assert captured == {
        "chart_name": network_operator.chart_name,
        "chart_repo": network_operator.source,
        "chart_version": network_operator.version,
    }
