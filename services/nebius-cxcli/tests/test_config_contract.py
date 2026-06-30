from __future__ import annotations

import pytest
import yaml

from nebius_cxcli.config_loader import validate_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.paths import resolve_project_paths, validate_path_alignment
from nebius_cxcli.runtime_validation import validate_dynamic_payload_structure


def _runtime_payload_with_chart(chart: dict) -> dict:
    return {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {
                "email_enabled": False,
                "email": None,
            },
        },
        "infra": {
            "components": [],
        },
        "deploy": {
            "targets": [
                {
                    "instance_id": "cluster1",
                    "kind": "external-mk8s",
                    "ownership": "external",
                    "kube_context": "cluster1",
                }
            ]
        },
        "apps": {
            "charts": [chart],
        },
    }


def test_starter_template_is_runtime_valid() -> None:
    yaml_text = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        email="ops@example.com",
    )

    payload = yaml.safe_load(yaml_text)
    assert isinstance(payload, dict)
    assert "shared" not in payload
    config = validate_config(payload)
    assert config.version == "v1"
    assert config.client_info.client_name == "client-a"
    assert config.client_info.nebius.project_id == "project-456"


def test_starter_template_disables_email_when_recipient_is_blank() -> None:
    yaml_text = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        email=None,
    )

    payload = yaml.safe_load(yaml_text)
    assert isinstance(payload, dict)
    notifications = payload["client_info"]["notifications"]
    assert notifications["email_enabled"] is False
    assert notifications["email"] is None

    config = validate_config(payload)
    assert config.client_info.notifications.email_enabled is False
    assert config.client_info.notifications.email is None


def test_existing_runtime_config_allows_missing_tenant_id(tmp_path) -> None:
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {
                "email_enabled": False,
                "email": None,
            },
        },
        "infra": {"components": []},
        "apps": {"charts": []},
    }

    config = validate_config(payload)
    assert config.client_info.nebius.tenant_id is None
    assert config.client_info.nebius.project_id == "project-456"

    config_path = tmp_path / "deployments" / "tenant-folder" / "project-folder" / "config.yaml"
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)


def test_runtime_payload_rejects_soperator_values_node_group_mapping() -> None:
    payload = _runtime_payload_with_chart(
        {
            "id": "soperator",
            "instance_id": "cluster1",
            "enabled": True,
            "install_mode": "production-cluster",
            "values": {
                "nodeGroupMapping": {
                    "worker": ["worker"],
                }
            },
        }
    )

    with pytest.raises(
        ValueError,
        match=r"apps\.charts\[0\]\.values\.nodeGroupMapping is no longer supported",
    ):
        validate_dynamic_payload_structure(payload)


def test_runtime_payload_rejects_placements_on_non_soperator_chart() -> None:
    payload = _runtime_payload_with_chart(
        {
            "id": "grafana",
            "instance_id": "cluster1",
            "enabled": True,
            "placements": {
                "system": "system",
            },
            "values": {},
        }
    )

    with pytest.raises(
        ValueError,
        match=r"apps\.charts\[0\]\.placements is only supported for chart 'soperator'",
    ):
        validate_dynamic_payload_structure(payload)
