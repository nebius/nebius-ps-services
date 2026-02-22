from __future__ import annotations

import json
from pathlib import Path

import yaml

from nebius_cxcli.cli import _terraform_runtime_env
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml


def _build_config(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            env="prod",
            cluster_name="client-a-prod",
            project_id="project-456",
            region_id="eu-north1",
            subnet_id="subnet-abc123",
            email="ops@example.com",
        )
    )
    payload["infra"]["mysterybox"]["enabled"] = True
    payload["infra"]["mysterybox"]["secrets"] = [
        {
            "id": "n8n-runtime",
            "scope": "apps",
            "name": "n8n-runtime",
            "entries": [
                {"key": "N8N_ENCRYPTION_KEY", "value_from_env": "N8N_ENCRYPTION_KEY"},
                {"key": "N8N_BASIC_AUTH_PASSWORD", "value_from_env": "N8N_BASIC_AUTH_PASSWORD"},
            ],
        }
    ]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return load_config(config_path)


def test_terraform_runtime_env_builds_mysterybox_tf_var(tmp_path: Path, monkeypatch) -> None:
    cfg = _build_config(tmp_path)
    monkeypatch.setenv("N8N_ENCRYPTION_KEY", "enc-key")
    monkeypatch.setenv("N8N_BASIC_AUTH_PASSWORD", "pass-123")

    runtime_env = _terraform_runtime_env(cfg)

    assert "TF_VAR_mysterybox_secrets" in runtime_env
    assert "TF_VAR_mysterybox_secret_values" in runtime_env
    secret_defs = json.loads(runtime_env["TF_VAR_mysterybox_secrets"])
    assert secret_defs == [
        {
            "id": "n8n-runtime",
            "labels": {},
            "name": "n8n-runtime",
            "payload_keys": ["N8N_ENCRYPTION_KEY", "N8N_BASIC_AUTH_PASSWORD"],
            "scope": "apps",
            "set_primary": True,
        }
    ]
    secret_values = json.loads(runtime_env["TF_VAR_mysterybox_secret_values"])
    assert secret_values == {
        "n8n-runtime": {
            "N8N_ENCRYPTION_KEY": "enc-key",
            "N8N_BASIC_AUTH_PASSWORD": "pass-123",
        }
    }


def test_terraform_runtime_env_fails_when_mysterybox_env_missing(
    tmp_path: Path, monkeypatch
) -> None:
    cfg = _build_config(tmp_path)
    monkeypatch.delenv("N8N_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("N8N_BASIC_AUTH_PASSWORD", raising=False)

    try:
        _terraform_runtime_env(cfg)
    except RuntimeError as exc:
        msg = str(exc)
        assert "Missing environment values for MysteryBox payload entries" in msg
        assert "n8n-runtime.N8N_ENCRYPTION_KEY <- $N8N_ENCRYPTION_KEY" in msg
        assert "n8n-runtime.N8N_BASIC_AUTH_PASSWORD <- $N8N_BASIC_AUTH_PASSWORD" in msg
    else:
        raise AssertionError(
            "Expected missing env validation failure for MysteryBox runtime values"
        )
