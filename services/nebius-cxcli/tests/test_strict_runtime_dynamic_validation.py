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


def _catalog_with_shared_admin_ssh(
    tmp_path: Path,
    *,
    user_name: str = "ubuntu",
    public_key: str = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB8Yq7Rr0x2GdQ8gJ5Q40gF4yHahx7s6vH8kKf+demo",
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
    mk8s["inputs"] = {
        "parent_id": "project-456",
        "cluster_name": "demo-cluster",
        "subnet_id": "subnet-123",
        "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB8Yq7Rr0x2GdQ8gJ5Q40gF4yHahx7s6vH8kKf+demo",
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    assert "infra.components[mk8s].inputs.ssh_public_key is not declared by module" in str(
        exc_info.value
    )


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


def test_strict_validation_mk8s_cpu_shape_not_required_when_has_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cpu_nodes_platform/cpu_nodes_preset have default="" in TF, so they are
    not auto-detected as required.  Only truly required variables (no default,
    nullable!=true) are enforced."""
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["inputs"] = {
        "parent_id": "project-456",
        "cluster_name": "demo-cluster",
        "subnet_id": "subnet-123",
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("parent_id", "cluster_name", "subnet_id"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    try:
        _validate_strict_config(config)
        message = ""
    except RuntimeError as exc:
        message = str(exc)
    assert "cpu_nodes_platform is required" not in message
    assert "cpu_nodes_preset is required" not in message


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


def test_strict_validation_ssh_jumphost_allowed_cidrs_not_required_when_has_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allowed_cidrs has default=[] in TF, so it is not auto-detected as required."""
    payload = _starter_payload(selected_infra={"ssh-jumphost"}, selected_apps=set())
    jumphost = _infra_component_row(payload, "ssh-jumphost")
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "region": "eu-north1",
        "subnet_id": "subnet-123",
        "name": "ssh-jh",
        "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB8Yq7Rr0x2GdQ8gJ5Q40gF4yHahx7s6vH8kKf+demo",
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("parent_id", "region", "subnet_id", "name", "ssh_public_key"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    try:
        _validate_strict_config(config)
        message = ""
    except RuntimeError as exc:
        message = str(exc)
    assert "allowed_cidrs is required" not in message


def test_strict_validation_mysterybox_secrets_not_required_when_has_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """secrets has default={} in TF, so it is not auto-detected as required."""
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
        lambda _source: ("parent_id",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config, **_kw: []
    )

    try:
        _validate_strict_config(config)
        message = ""
    except RuntimeError as exc:
        message = str(exc)
    assert "secrets is required" not in message


def test_strict_validation_allows_explicit_ssh_public_key_for_jumphost(tmp_path: Path) -> None:
    set_component_sources_file_override(_catalog_with_shared_admin_ssh(tmp_path))
    reset_component_sources_cache()
    reset_runtime_introspection_cache()
    reset_component_entry_cache()
    payload = _starter_payload(selected_infra={"wireguard-jumphost"}, selected_apps=set())
    jumphost = _infra_component_row(payload, "wireguard-jumphost")
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "region": "eu-north1",
        "subnet_id": "subnet-123",
        "name": "wg-jumphost",
        "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB8Yq7Rr0x2GdQ8gJ5Q40gF4yHahx7s6vH8kKf+demo",
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    _validate_strict_config(config)


def test_strict_validation_rejects_missing_local_custom_module_source_dir(tmp_path: Path) -> None:
    payload = _starter_payload(selected_infra=set(), selected_apps=set())
    payload["infra"]["components"] = [
        {
            "id": "runtime-custom",
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
    payload = _starter_payload(selected_infra=set(), selected_apps={"n8n"})

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli._helm_chart_validation_issues",
        lambda **_kwargs: ("simulated lookup failure",),
    )

    issues = _validate_enabled_chart_sources(config)
    assert any("apps.charts[n8n]" in issue for issue in issues)
    assert any("simulated lookup failure" in issue for issue in issues)
