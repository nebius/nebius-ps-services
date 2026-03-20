from __future__ import annotations

from pathlib import Path

import yaml

from nebius_cxcli.cli import _terraform_runtime_env
from nebius_cxcli.components import component_entries
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.terraform_provider import build_provider_module_name


def _build_config(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    infra_entries = component_entries("infra")
    app_entries = component_entries("apps")
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            email="ops@example.com",
            infra_entries=infra_entries,
            app_entries=app_entries,
        )
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return load_config(config_path)


def test_terraform_runtime_env_builds_mysterybox_tf_var(tmp_path: Path, monkeypatch) -> None:
    cfg = _build_config(tmp_path)
    monkeypatch.setenv("N8N_ENCRYPTION_KEY", "enc-key")
    monkeypatch.setenv("N8N_BASIC_AUTH_PASSWORD", "pass-123")

    runtime_env = _terraform_runtime_env(cfg)

    assert runtime_env == {
        "TF_VAR_nebius_provider_module_name": build_provider_module_name(
            client_name="client-a",
            project_id="project-456",
        ),
        "TF_VAR_nebius_provider_parent_id": "project-456",
    }


def test_terraform_runtime_env_fails_when_mysterybox_env_missing(
    tmp_path: Path, monkeypatch
) -> None:
    cfg = _build_config(tmp_path)
    monkeypatch.delenv("N8N_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("N8N_BASIC_AUTH_PASSWORD", raising=False)

    assert _terraform_runtime_env(cfg) == {
        "TF_VAR_nebius_provider_module_name": build_provider_module_name(
            client_name="client-a",
            project_id="project-456",
        ),
        "TF_VAR_nebius_provider_parent_id": "project-456",
    }
