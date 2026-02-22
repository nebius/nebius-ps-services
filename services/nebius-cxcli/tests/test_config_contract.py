from __future__ import annotations

from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.schema import validate_config_from_yaml


def test_starter_template_is_schema_valid() -> None:
    yaml_text = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )

    config = validate_config_from_yaml(yaml_text)
    assert config.version == "v1"
    assert config.client_info.client_name == "client-a"
    assert config.client_info.cluster_name == "client-a-prod"
