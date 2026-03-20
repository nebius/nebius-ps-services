from __future__ import annotations

import yaml

from nebius_cxcli.config_loader import validate_config
from nebius_cxcli.config_template import starter_config_yaml


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
