from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import nebius_cxcli.component_sources as component_sources
from nebius_cxcli.cli import app
from nebius_cxcli.component_sources import (
    ComponentOutput,
    reset_component_sources_cache,
    set_component_sources_file_override,
)
from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_model import is_dynamic_payload, to_dynamic_payload, to_runtime_payload
from nebius_cxcli.config_template import starter_config_yaml

runner = CliRunner()


def _tenant_folder_name(tenant_id: str = "tenant-123") -> str:
    folder_by_tenant_id = {
        "tenant-123": "tenant-acme-labs",
    }
    return folder_by_tenant_id[tenant_id]


def _project_folder_name(project_id: str = "project-456") -> str:
    folder_by_project_id = {
        "project-456": "gpu-training-prod",
    }
    return folder_by_project_id[project_id]


@pytest.fixture(autouse=True)
def _reset_component_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_tenant_project_ids_or_prompt",
        lambda **kwargs: (kwargs["tenant_id"], kwargs["project_id"]),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._resolve_create_target_folders",
        lambda **kwargs: (
            _tenant_folder_name(kwargs["tenant_id"]),
            _project_folder_name(kwargs["project_id"]),
        ),
    )
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
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_variable_names", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr(
        "nebius_cxcli.cli._helm_chart_metadata",
        lambda *, chart_name_or_ref, chart_repo, chart_version, cache=None: (
            chart_name_or_ref,
            set(),
            None,
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._with_infra_provider_groups", lambda entries: entries)
    set_component_sources_file_override(None)
    reset_component_sources_cache()
    reset_component_entry_cache()


def _starter_runtime_payload() -> dict:
    infra_entries = component_entries("infra")
    app_entries = component_entries("apps")
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="us-central1",
            email="ops@example.com",
            infra_entries=infra_entries,
            app_entries=app_entries,
        )
    )
    assert isinstance(payload, dict)
    return payload


def test_to_dynamic_payload_generates_components_and_charts() -> None:
    dynamic = to_dynamic_payload(_starter_runtime_payload())

    assert is_dynamic_payload(dynamic)
    assert isinstance(dynamic["infra"]["components"], list)
    assert isinstance(dynamic["apps"]["charts"], list)

    component_ids = {str(item.get("id")) for item in dynamic["infra"]["components"]}
    chart_ids = {str(item.get("id")) for item in dynamic["apps"]["charts"]}
    assert "mk8s" in component_ids
    assert "object-storage" in component_ids
    assert "n8n" in chart_ids


def test_to_runtime_payload_round_trip_keeps_enabled_flags() -> None:
    runtime = _starter_runtime_payload()
    runtime["deploy"] = {"observability": {"enabled": False}}
    dynamic = to_dynamic_payload(runtime)
    back = to_runtime_payload(dynamic)

    assert back["deploy"]["observability"]["enabled"] is False
    assert back["infra"]["mk8s"]["enabled"] is True
    assert back["infra"]["object_storage"]["enabled"] is False
    assert back["apps"]["workloads"]["n8n"]["enabled"] is False


def test_to_runtime_payload_keeps_multiple_instances_distinct() -> None:
    dynamic = to_dynamic_payload(_starter_runtime_payload())
    components = dynamic["infra"]["components"]
    assert isinstance(components, list)
    components.extend(
        [
            {
                "id": "managed-postgresql",
                "instance_id": "managed-postgresql",
                "enabled": True,
                "inputs": {"name": "primary"},
            },
            {
                "id": "managed-postgresql",
                "instance_id": "managed-postgresql-2",
                "enabled": True,
                "inputs": {"name": "analytics"},
            },
        ]
    )

    back = to_runtime_payload(dynamic)

    assert back["infra"]["managed_postgresql"]["enabled"] is True
    assert back["infra"]["managed_postgresql"]["name"] == "primary"
    assert back["infra"]["managed_postgresql_2"]["enabled"] is True
    assert back["infra"]["managed_postgresql_2"]["name"] == "analytics"


def test_load_config_accepts_dynamic_payload_with_extra_chart(tmp_path: Path) -> None:
    dynamic = to_dynamic_payload(_starter_runtime_payload())
    charts = dynamic["apps"]["charts"]
    assert isinstance(charts, list)
    charts.append(
        {
            "id": "runtime-app",
            "group": "workloads",
            "enabled": True,
            "target_ref": "mk8s",
            "repo": "https://example.invalid/charts",
            "version": "1.0.0",
            "namespace": "runtime-app",
            "release-name": "runtime-app",
            "values": {},
        }
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(dynamic, sort_keys=False), encoding="utf-8")

    loaded = load_config(config_path)
    assert loaded.apps.workloads.runtime_app.enabled is True
    assert loaded.apps.workloads.runtime_app.repo == "https://example.invalid/charts"


def test_create_writes_runtime_shape_with_selected_components(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--app",
            "n8n",
        ],
    )
    assert result.exit_code == 0, result.output

    config_path = (
        deployments_root
        / _tenant_folder_name("tenant-123")
        / _project_folder_name("project-456")
        / "config.yaml"
    )
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert isinstance(payload, dict)
    assert is_dynamic_payload(payload)
    infra_enabled = {
        str(item.get("id")): bool(item.get("enabled", False))
        for item in payload["infra"]["components"]
        if isinstance(item, dict)
    }
    app_enabled = {
        str(item.get("id")): bool(item.get("enabled", False))
        for item in payload["apps"]["charts"]
        if isinstance(item, dict)
    }
    assert infra_enabled["mk8s"] is True
    assert "object-storage" not in infra_enabled
    assert app_enabled["n8n"] is True
