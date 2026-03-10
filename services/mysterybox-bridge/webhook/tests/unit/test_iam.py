from __future__ import annotations

from pathlib import Path

import mysterybox_bridge.iam as iam
from mysterybox_bridge.config import Settings
from mysterybox_bridge.iam import SDKProvider


class FakeSDK:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def _settings_with_token() -> Settings:
    return Settings(
        listen_host="0.0.0.0",
        listen_port=8080,
        secret_endpoint_path="/v1/secret",
        log_level="INFO",
        nebius_project_id="project-test",
        nebius_iam_token="token-value",
        nebius_sa_id=None,
        nebius_auth_public_key_id=None,
        nebius_auth_private_key_pem=None,
        nebius_auth_private_key_file=None,
        nebius_api_endpoint=None,
        webhook_auth_header=None,
        webhook_auth_token=None,
        secret_id_cache_ttl_seconds=300,
    )


def _settings_with_service_account(tmp_path: Path) -> Settings:
    return Settings(
        listen_host="0.0.0.0",
        listen_port=8080,
        secret_endpoint_path="/v1/secret",
        log_level="INFO",
        nebius_project_id="project-test",
        nebius_iam_token=None,
        nebius_sa_id="serviceaccount-123",
        nebius_auth_public_key_id="public-key-123",
        nebius_auth_private_key_pem="-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        nebius_auth_private_key_file=None,
        nebius_api_endpoint="api.eu-north1.nebius.cloud:443",
        webhook_auth_header=None,
        webhook_auth_token=None,
        secret_id_cache_ttl_seconds=300,
    )


def test_sdk_provider_prefers_token(monkeypatch) -> None:
    monkeypatch.setattr(iam, "_load_sdk_class", lambda: FakeSDK)

    provider = SDKProvider(_settings_with_token())
    sdk = provider.get_sdk()

    assert isinstance(sdk, FakeSDK)
    assert sdk.kwargs == {"credentials": "token-value"}


def test_sdk_provider_uses_service_account_when_token_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(iam, "_load_sdk_class", lambda: FakeSDK)

    provider = SDKProvider(_settings_with_service_account(tmp_path))
    sdk = provider.get_sdk()

    assert isinstance(sdk, FakeSDK)
    assert sdk.kwargs["service_account_id"] == "serviceaccount-123"
    assert sdk.kwargs["service_account_public_key_id"] == "public-key-123"
    assert "service_account_private_key_file_name" in sdk.kwargs
    assert Path(sdk.kwargs["service_account_private_key_file_name"]).exists()
