from __future__ import annotations

from pathlib import Path

import yaml

from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.inventory_ops import write_inventory
from nebius_cxcli.paths import resolve_project_paths, validate_path_alignment


def _project_config_path(base: Path) -> Path:
    return (
        base
        / "deployments"
        / "projects"
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


def _chart_row(payload: dict, chart_id: str) -> dict:
    charts = payload.get("apps", {}).get("charts", [])
    if not isinstance(charts, list):
        raise KeyError(chart_id)
    for item in charts:
        if not isinstance(item, dict):
            continue
        if str(item.get("id", "")).strip().lower() == chart_id:
            return item
    raise KeyError(chart_id)


def test_write_inventory_handles_dynamic_component_model(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"n8n"})
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs["cpu_nodes_count"] = 1
    mk8s_inputs["cpu_nodes_platform"] = "cpu-d3"
    mk8s_inputs["cpu_nodes_preset"] = "4vcpu-16gb"

    n8n_release = _chart_row(payload, "n8n")
    n8n_values = n8n_release.get("values", {})
    assert isinstance(n8n_values, dict)
    values_payload = n8n_values.setdefault("values", {})
    assert isinstance(values_payload, dict)
    values_payload["route"] = {"hostname": "n8n.example.com"}

    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    assert artifacts.markdown.exists()
    assert not (paths.inventory_dir / "apps.json").exists()
    assert not (paths.inventory_dir / "mk8s.json").exists()
    assert not (paths.inventory_dir / "postgresql.json").exists()
    assert not (paths.inventory_dir / "sfs.json").exists()
    markdown = artifacts.markdown.read_text(encoding="utf-8")
    assert "## Components\n\n- MK8s:" in markdown
    assert "## Apps\n\n- Envoy Gateway:" in markdown
    assert "- n8n: `True` (n8n.example.com)" in markdown


def test_write_inventory_removes_stale_disabled_component_files(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)
    stale_pg = paths.inventory_dir / "postgresql.json"
    stale_sfs = paths.inventory_dir / "sfs.json"
    stale_apps = paths.inventory_dir / "apps.json"
    stale_infra = paths.inventory_dir / "infra.json"
    stale_mk8s = paths.inventory_dir / "mk8s.json"
    stale_pg.write_text("{\"enabled\": true}\n", encoding="utf-8")
    stale_sfs.write_text("{\"enabled\": true}\n", encoding="utf-8")
    stale_apps.write_text("{\"enabled\": true}\n", encoding="utf-8")
    stale_infra.write_text("{\"enabled\": true}\n", encoding="utf-8")
    stale_mk8s.write_text("{\"enabled\": true}\n", encoding="utf-8")

    artifacts = write_inventory(config, paths)

    assert artifacts.markdown.exists()
    assert not stale_pg.exists()
    assert not stale_sfs.exists()
    assert not stale_apps.exists()
    assert not stale_infra.exists()
    assert not stale_mk8s.exists()
