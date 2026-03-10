from __future__ import annotations

from mysterybox_bridge.config import Settings


def _set_required_env(monkeypatch) -> None:
    monkeypatch.setenv("NEBIUS_PROJECT_ID", "project-test")
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "token-value")


def test_from_env_reads_nebius_api_endpoint(monkeypatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("NEBIUS_API_ENDPOINT", "api.eu-north1.nebius.cloud:443")

    settings = Settings.from_env()

    assert settings.nebius_api_endpoint == "api.eu-north1.nebius.cloud:443"
