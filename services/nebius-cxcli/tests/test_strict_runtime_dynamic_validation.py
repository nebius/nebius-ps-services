from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nebius_cxcli.cli import _validate_enabled_chart_sources, _validate_strict_config
from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml


@pytest.fixture(autouse=True)
def _reset_component_cache() -> None:
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


def test_strict_validation_requires_enabled_module_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr("nebius_cxcli.cli.provider_component_match_status", lambda _id: None)
    monkeypatch.setattr("nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config: [])

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

    monkeypatch.setattr("nebius_cxcli.cli.provider_component_match_status", lambda _id: None)
    monkeypatch.setattr("nebius_cxcli.cli._validate_enabled_chart_sources", lambda _config: [])

    with pytest.raises(RuntimeError) as exc_info:
        _validate_strict_config(config)
    assert "infra component 'runtime-custom' is enabled but has no module source configured" in str(
        exc_info.value
    )


def test_validate_enabled_chart_sources_reports_lookup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _starter_payload(selected_infra=set(), selected_apps={"n8n"})

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    monkeypatch.setattr(
        "nebius_cxcli.cli._helm_chart_dependency_names",
        lambda **_kwargs: (set(), "simulated lookup failure"),
    )

    issues = _validate_enabled_chart_sources(config)
    assert any("apps:n8n" in issue for issue in issues)
    assert any("simulated lookup failure" in issue for issue in issues)
