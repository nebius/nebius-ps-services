from __future__ import annotations

import json
import subprocess
from typing import Any
from urllib.parse import parse_qs

import pytest

import nebius_cxcli.slack_notifier_runtime as slack_runtime


def _notifier_payload(
    *,
    mode: str = "existing-webhook",
    webhook_source: str = "deploy-time",
) -> dict[str, Any]:
    return {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "namespace": "soperator",
                    "release-name": "soperator",
                    "values": {
                        "soperator-notifier": {
                            "enabled": True,
                            "slack": {
                                "mode": mode,
                                "webhookSource": webhook_source,
                                "existingSecret": "soperator-slack",
                                "existingSecretKey": "url",
                                "channelName": "slurm-alerts",
                                "mysterybox": {
                                    "secretId": "mbsec-e00slack",
                                    "property": "url",
                                },
                                "oauth": {
                                    "clientId": "123.456",
                                    "redirectUri": "https://example.com/slack/oauth",
                                },
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
    chart["values"]["soperator-notifier"]["slack"]["webhookUrl"] = (  # type: ignore[index]
        "https://hooks.slack.com/services/example"
    )

    with pytest.raises(RuntimeError, match="webhookUrl"):
        slack_runtime.soperator_notifier_release_specs(payload, target_ref="cluster1")


def test_ensure_existing_webhook_creates_runtime_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[dict[str, Any]] = []
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


def test_secret_has_keys_treats_explicit_kubernetes_not_found_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        slack_runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=("kubectl",),
            returncode=1,
            stdout=json.dumps({"kind": "Status", "reason": "NotFound"}),
            stderr="",
        ),
    )

    assert not slack_runtime._secret_has_keys(
        namespace="soperator",
        name="soperator-slack",
        keys=("url",),
        extra_env={},
    )


def test_secret_has_keys_does_not_treat_free_text_not_found_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        slack_runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=("kubectl",),
            returncode=1,
            stdout="",
            stderr='Error from server (Forbidden): secrets "not found" is forbidden',
        ),
    )

    with pytest.raises(RuntimeError, match="Forbidden"):
        slack_runtime._secret_has_keys(
            namespace="soperator",
            name="soperator-slack",
            keys=("url",),
            extra_env={},
        )


def test_target_kube_context_reads_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_CXCLI_TARGET_KUBE_CONTEXT", "cluster1-context")

    assert slack_runtime._target_kube_context({}) == "cluster1-context"


def test_existing_webhook_requires_target_specific_env_for_target_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        lambda **_kwargs: pytest.fail("bare Slack webhook env var must not be accepted"),
    )

    with pytest.raises(RuntimeError, match="SLACK_WEBHOOK_URL_CLUSTER1"):
        slack_runtime.ensure_soperator_notifier_runtime_secrets(
            _notifier_payload(),
            target_ref="cluster1",
            extra_env={
                "NEBIUS_CXCLI_SOPERATOR_SLACK_WEBHOOK_URL": (
                    "https://hooks.slack.com/services/example"
                )
            },
        )


def test_soperator_notifier_mysterybox_refs_use_primary_version() -> None:
    refs = slack_runtime.soperator_notifier_mysterybox_secret_refs(
        _notifier_payload(webhook_source="mysterybox"),
        target_ref="cluster1",
    )

    assert refs == (
        {
            "target_ref": "cluster1",
            "namespace": "soperator",
            "name": "soperator-slack",
            "target_name": "soperator-slack",
            "secret_key": "url",
            "secret_id": "mbsec-e00slack",
            "property": "url",
        },
    )


def test_soperator_notifier_mysterybox_source_rejects_version_id() -> None:
    payload = _notifier_payload(webhook_source="mysterybox")
    chart = payload["apps"]["charts"][0]  # type: ignore[index]
    chart["values"]["soperator-notifier"]["slack"]["mysterybox"]["secretId"] = (  # type: ignore[index]
        "mbsecver-e00slack"
    )

    with pytest.raises(RuntimeError, match="not a Secret version ID"):
        slack_runtime.soperator_notifier_release_specs(payload, target_ref="cluster1")


def test_mysterybox_source_requires_rendered_external_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_runtime, "_validate_victoriametrics_crds", lambda *, extra_env: None)
    monkeypatch.setattr(slack_runtime, "_ensure_namespace", lambda _namespace, *, extra_env: None)
    monkeypatch.setattr(
        slack_runtime,
        "_secret_has_keys",
        lambda *, namespace, name, keys, extra_env: False,
    )

    with pytest.raises(RuntimeError, match="generated MysteryBox ExternalSecret"):
        slack_runtime.ensure_soperator_notifier_runtime_secrets(
            _notifier_payload(webhook_source="mysterybox"),
            target_ref="cluster1",
            extra_env={
                "NEBIUS_CXCLI_SOPERATOR_SLACK_WEBHOOK_URL_CLUSTER1": (
                    "https://hooks.slack.com/services/ignored"
                )
            },
            prompt=False,
            externally_managed_secret_keys=set(),
        )


def test_mysterybox_source_rejects_stale_preexisting_kubernetes_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_runtime, "_validate_victoriametrics_crds", lambda *, extra_env: None)
    monkeypatch.setattr(slack_runtime, "_ensure_namespace", lambda _namespace, *, extra_env: None)
    monkeypatch.setattr(
        slack_runtime,
        "_secret_has_keys",
        lambda *, namespace, name, keys, extra_env: True,
    )

    with pytest.raises(RuntimeError, match="generated MysteryBox ExternalSecret"):
        slack_runtime.ensure_soperator_notifier_runtime_secrets(
            _notifier_payload(webhook_source="mysterybox"),
            target_ref="cluster1",
            extra_env={},
            prompt=False,
            externally_managed_secret_keys=set(),
        )


def test_ensure_existing_webhook_skips_secret_when_mysterybox_eso_manages_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _notifier_payload(webhook_source="mysterybox")
    payload.update(
        {
            "infra": {
                "components": [
                    {
                        "id": "mysterybox",
                        "instance_id": "mysterybox",
                        "enabled": True,
                        "inputs": {
                            "secrets": [
                                {
                                    "name": "soperator-slack-webhook",
                                    "version_id": "n/a",
                                    "kubernetes_secret_name": "soperator-slack",
                                    "payload": {"url": {"type": "text"}},
                                }
                            ]
                        },
                    }
                ]
            },
            "deploy": {
                "targets": [
                    {
                        "instance_id": "cluster1",
                        "secrets": {
                            "mysterybox": {
                                "enabled": True,
                                "sync_namespaces": ["soperator"],
                            }
                        },
                    }
                ]
            },
        }
    )
    emitted: list[str] = []
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
        lambda **_kwargs: pytest.fail("MysteryBox-managed Secret should not be applied directly"),
    )

    slack_runtime.ensure_soperator_notifier_runtime_secrets(
        payload,
        target_ref="cluster1",
        extra_env={},
        prompt=False,
        emit=emitted.append,
        externally_managed_secret_keys={("soperator", "soperator-slack", "url")},
    )

    assert emitted == [
        "Soperator Slack notifier Secret `soperator/soperator-slack:url` is managed by "
        "MysteryBox External Secrets; skipping direct webhook materialization."
    ]


def test_ensure_oauth_webhook_creates_runtime_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[dict[str, Any]] = []
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
    recorded: dict[str, Any] = {}

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
