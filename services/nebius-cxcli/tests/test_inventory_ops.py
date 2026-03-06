from __future__ import annotations

import json
from pathlib import Path

import yaml

from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.inventory_ops import write_inventory
from nebius_cxcli.paths import resolve_instance_paths, validate_path_alignment


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
    config_path = _instance_config_path(tmp_path)
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
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    assert artifacts.markdown.exists()
    assert artifacts.apps_json.exists()

    apps_payload = json.loads(artifacts.apps_json.read_text(encoding="utf-8"))
    assert apps_payload["n8n"]["enabled"] is True
    assert apps_payload["n8n"]["hostname"] == "n8n.example.com"
