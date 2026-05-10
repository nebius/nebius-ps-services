from __future__ import annotations

import json
from urllib.parse import parse_qs

import pytest

import nebius_cxcli.slack_notifier_runtime as slack_runtime


def _notifier_payload(*, mode: str = "existing-webhook") -> dict[str, object]:
    return {
        "apps": {
            "charts": [
                {
                    "id": "soperator-notifier",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "namespace": "soperator",
                    "release-name": "soperator-notifier",
                    "values": {
                        "slack": {
                            "mode": mode,
                            "existingSecret": "soperator-slack",
                            "existingSecretKey": "url",
                            "channelName": "slurm-alerts",
                            "oauth": {
                                "clientId": "123.456",
                                "redirectUri": "https://example.com/slack/oauth",
                            },
                        }
                    },
                }
            ]
        }
    }


def test_soperator_notifier_specs_reject_webhook_url_in_values() -> None:
    payload = _notifier_payload()
    chart = payload["apps"]["charts"][0]  # type: ignore[index]
    chart["values"]["slack"]["webhookUrl"] = "https://hooks.slack.com/services/example"  # type: ignore[index]

    with pytest.raises(RuntimeError, match="webhookUrl"):
        slack_runtime.soperator_notifier_release_specs(payload, target_ref="cluster1")


def test_ensure_existing_webhook_creates_runtime_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[dict[str, object]] = []
    monkeypatch.setattr(slack_runtime, "_validate_victoriametrics_crds", lambda *, extra_env: None)
    monkeypatch.setattr(slack_runtime, "_ensure_namespace", lambda _namespace, *, extra_env: None)
    monkeypatch.setattr(
        slack_runtime,
        "_secret_has_keys",
        lambda *, namespace, name, keys, extra_env: False,
    )
    monkeypatch.setattr(
        slack_runtime,
        "_apply_secret",
        lambda *, namespace, name, string_data, extra_env: applied.append(
            {"namespace": namespace, "name": name, "string_data": dict(string_data)}
        ),
    )

    slack_runtime.ensure_soperator_notifier_runtime_secrets(
        _notifier_payload(),
        target_ref="cluster1",
        extra_env={
            "NEBIUS_CXCLI_SOPERATOR_SLACK_WEBHOOK_URL_CLUSTER1": (
                "https://hooks.slack.com/services/example"
            )
        },
    )

    assert applied == [
        {
            "namespace": "soperator",
            "name": "soperator-slack",
            "string_data": {"url": "https://hooks.slack.com/services/example"},
        }
    ]


def test_ensure_oauth_webhook_creates_runtime_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[dict[str, object]] = []
    monkeypatch.setattr(slack_runtime, "_validate_victoriametrics_crds", lambda *, extra_env: None)
    monkeypatch.setattr(slack_runtime, "_ensure_namespace", lambda _namespace, *, extra_env: None)
    monkeypatch.setattr(
        slack_runtime,
        "_secret_has_keys",
        lambda *, namespace, name, keys, extra_env: False,
    )
    monkeypatch.setattr(
        slack_runtime,
        "_oauth_webhook",
        lambda spec, *, extra_env, prompt, emit: slack_runtime.SlackOAuthWebhook(
            url="https://hooks.slack.com/services/oauth-example",
            channel="slurm-alerts",
            channel_id="C123",
        ),
    )
    monkeypatch.setattr(
        slack_runtime,
        "_apply_secret",
        lambda *, namespace, name, string_data, extra_env: applied.append(
            {"namespace": namespace, "name": name, "string_data": dict(string_data)}
        ),
    )

    slack_runtime.ensure_soperator_notifier_runtime_secrets(
        _notifier_payload(mode="oauth-webhook"),
        target_ref="cluster1",
        extra_env={},
    )

    assert applied == [
        {
            "namespace": "soperator",
            "name": "soperator-slack",
            "string_data": {"url": "https://hooks.slack.com/services/oauth-example"},
        }
    ]


def test_build_slack_oauth_authorize_url_uses_incoming_webhook_scope() -> None:
    url = slack_runtime.build_slack_oauth_authorize_url(
        client_id="123.456",
        redirect_uri="https://example.com/slack/oauth",
        state="state-token",
    )
    query = parse_qs(url.split("?", maxsplit=1)[1])

    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert query["client_id"] == ["123.456"]
    assert query["redirect_uri"] == ["https://example.com/slack/oauth"]
    assert query["scope"] == ["incoming-webhook"]
    assert query["state"] == ["state-token"]


def test_exchange_slack_oauth_code_extracts_incoming_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, object] = {}

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "ok": True,
                    "incoming_webhook": {
                        "url": "https://hooks.slack.com/services/oauth-example",
                        "channel": "slurm-alerts",
                        "channel_id": "C123",
                    },
                }
            ).encode("utf-8")

    def fake_urlopen(request: object, *, timeout: int) -> Response:
        recorded["url"] = request.full_url  # type: ignore[attr-defined]
        recorded["headers"] = dict(request.header_items())  # type: ignore[attr-defined]
        recorded["body"] = request.data.decode("utf-8")  # type: ignore[attr-defined]
        recorded["timeout"] = timeout
        return Response()

    monkeypatch.setattr(slack_runtime, "urlopen", fake_urlopen)

    webhook = slack_runtime.exchange_slack_oauth_code(
        client_id="123.456",
        client_secret="client-secret",
        code="oauth-code",
        redirect_uri="https://example.com/slack/oauth",
    )

    assert webhook == slack_runtime.SlackOAuthWebhook(
        url="https://hooks.slack.com/services/oauth-example",
        channel="slurm-alerts",
        channel_id="C123",
    )
    assert recorded["url"] == "https://slack.com/api/oauth.v2.access"
    assert str(recorded["headers"]["Authorization"]).startswith("Basic ")
    assert parse_qs(str(recorded["body"])) == {
        "code": ["oauth-code"],
        "redirect_uri": ["https://example.com/slack/oauth"],
    }
