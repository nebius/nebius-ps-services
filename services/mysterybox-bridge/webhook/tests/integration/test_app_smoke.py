from __future__ import annotations

import pytest

import mysterybox_bridge.iam as iam
from mysterybox_bridge.app import create_app
from mysterybox_bridge.mysterybox_client import MysteryBoxClient


class FakeSDK:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


@pytest.mark.integration
def test_create_app_from_env_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_PROJECT_ID", "project-test")
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "token-value")
    monkeypatch.setenv("MYSTERYBOX_WEBHOOK_AUTH_HEADER", "X-MBX-Request")
    monkeypatch.setenv("MYSTERYBOX_WEBHOOK_AUTH_TOKEN", "abc")
    monkeypatch.setattr(iam, "_load_sdk_class", lambda: FakeSDK)
    monkeypatch.setattr(MysteryBoxClient, "readiness_check", lambda self: None)
    monkeypatch.setattr(MysteryBoxClient, "get_value", lambda self, **kwargs: "secret-value")

    app = create_app()
    client = app.test_client()

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.get_data(as_text=True) == "ok"

    secret = client.get(
        "/v1/secret?secret=my-secret&key=password",
        headers={"X-MBX-Request": "abc"},
    )
    assert secret.status_code == 200
    assert secret.get_json() == {"value": "secret-value"}
