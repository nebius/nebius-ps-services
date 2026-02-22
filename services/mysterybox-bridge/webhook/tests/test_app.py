from __future__ import annotations

from dataclasses import replace

from mysterybox_bridge.app import create_app
from mysterybox_bridge.config import Settings


class FakeMysteryBoxClient:
    def __init__(
        self,
        value: str = "secret-value",
        readiness_error: Exception | None = None,
    ) -> None:
        self.value = value
        self.calls: list[tuple[str, str, str | None]] = []
        self.readiness_calls = 0
        self.readiness_error = readiness_error

    def get_value(self, *, secret_reference: str, key: str, version: str | None) -> str:
        self.calls.append((secret_reference, key, version))
        return self.value

    def readiness_check(self) -> None:
        self.readiness_calls += 1
        if self.readiness_error is not None:
            raise self.readiness_error


def _settings() -> Settings:
    return Settings(
        listen_host="0.0.0.0",
        listen_port=8080,
        secret_endpoint_path="/v1/secret",
        log_level="INFO",
        nebius_project_id="project-test",
        nebius_iam_token="token",
        nebius_sa_id=None,
        nebius_auth_public_key_id=None,
        nebius_auth_private_key_pem=None,
        nebius_auth_private_key_file=None,
        nebius_api_endpoint=None,
        webhook_auth_header="X-MBX-Request",
        webhook_auth_token="abc",
        secret_id_cache_ttl_seconds=300,
    )


def test_secret_endpoint_requires_header_auth() -> None:
    settings = _settings()
    app = create_app(settings=settings, mysterybox_client=FakeMysteryBoxClient())
    client = app.test_client()

    response = client.get("/v1/secret?secret=my-secret&key=password")
    assert response.status_code == 403


def test_secret_endpoint_returns_value() -> None:
    settings = _settings()
    fake = FakeMysteryBoxClient(value="p@ss")
    app = create_app(settings=settings, mysterybox_client=fake)
    client = app.test_client()

    response = client.get(
        "/v1/secret?secret=my-secret&key=password&version=v1",
        headers={"X-MBX-Request": "abc"},
    )
    assert response.status_code == 200
    assert response.get_json() == {"value": "p@ss"}
    assert fake.calls == [("my-secret", "password", "v1")]


def test_secret_endpoint_reads_post_body_when_query_missing() -> None:
    settings = replace(_settings(), webhook_auth_header=None, webhook_auth_token=None)
    fake = FakeMysteryBoxClient(value="abc")
    app = create_app(settings=settings, mysterybox_client=fake)
    client = app.test_client()

    response = client.post(
        "/v1/secret",
        json={"secret": "n8n-runtime", "key": "N8N_ENCRYPTION_KEY"},
    )
    assert response.status_code == 200
    assert response.get_json() == {"value": "abc"}


def test_readyz_returns_200_when_bridge_is_ready() -> None:
    settings = _settings()
    fake = FakeMysteryBoxClient()
    app = create_app(settings=settings, mysterybox_client=fake)
    client = app.test_client()

    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ready"
    assert fake.readiness_calls == 1


def test_readyz_returns_503_when_bridge_is_not_ready() -> None:
    settings = _settings()
    fake = FakeMysteryBoxClient(readiness_error=RuntimeError("token expired"))
    app = create_app(settings=settings, mysterybox_client=fake)
    client = app.test_client()

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.get_data(as_text=True) == "not ready"
    assert fake.readiness_calls == 1
