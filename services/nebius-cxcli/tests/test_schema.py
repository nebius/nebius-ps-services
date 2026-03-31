from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nebius_cxcli.components import component_entries
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml


def _dynamic_payload() -> dict:
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            email="ops@example.com",
            infra_entries=component_entries("infra"),
            app_entries=component_entries("apps"),
        )
    )
    assert isinstance(payload, dict)
    return payload


def test_schema_valid_dynamic_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    payload = _dynamic_payload()
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    loaded = load_config(config_path)
    assert loaded.version == "v1"
    assert loaded.client_info.client_name == "client-a"
    assert isinstance(loaded.infra.components, list)
    assert isinstance(loaded.apps.charts, list)


def test_schema_rejects_static_shape(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "version": "v1",
                "client_info": {"client_name": "x"},
                "infra": {"mk8s": {"enabled": True}},
                "apps": {"workloads": {}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "dynamic model" in str(exc_info.value)


def test_schema_rejects_duplicate_infra_component_ids(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    components = payload["infra"]["components"]
    assert isinstance(components, list)
    components.append({"id": "mk8s", "enabled": True, "inputs": {}})

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "is duplicated" in str(exc_info.value)


def test_schema_rejects_invalid_chart_group_token(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    charts = payload["apps"]["charts"]
    assert isinstance(charts, list)
    charts[0]["group"] = "bad group"

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "group must use lowercase letters, digits, and hyphens" in str(exc_info.value)


def test_schema_rejects_release_name_alias_in_app_chart(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    charts = payload["apps"]["charts"]
    assert isinstance(charts, list)
    release_name = charts[0].pop("release-name")
    charts[0]["release_name"] = release_name

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "apps.charts[0] has unsupported field(s): release_name" in str(exc_info.value)


def test_schema_rejects_unknown_root_key(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["unknown"] = True

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "unknown field(s) at root" in str(exc_info.value)


def test_schema_rejects_shared_root_key(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["shared"] = {"admin_ssh": {"user_name": "ubuntu", "public_key": "ssh-ed25519 AAAA demo"}}

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "unknown field(s) at root: shared" in str(exc_info.value)


def test_schema_rejects_legacy_client_info_fields(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    client_info = payload.get("client_info")
    assert isinstance(client_info, dict)
    client_info["env"] = "dev"
    client_info["cluster_name"] = "cluster-a"

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "client_info has unsupported field(s)" in str(exc_info.value)
