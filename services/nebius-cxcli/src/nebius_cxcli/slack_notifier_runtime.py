"""Deploy-time Soperator Slack notifier secret bootstrap."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import webbrowser
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from getpass import getpass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import yaml

from .component_defaults import read_component_path
from .component_instances import component_instance_id, component_type_id
from .deploy_targets import app_chart_target_ref
from .runtime_config import to_plain_data

SOPERATOR_COMPONENT_ID = "soperator"
SOPERATOR_NOTIFIER_VALUES_KEY = "soperator-notifier"
SLACK_WEBHOOK_SOURCE_DEPLOY_TIME = "deploy-time"
SLACK_WEBHOOK_SOURCE_MYSTERYBOX = "mysterybox"
SLACK_WEBHOOK_SOURCES = frozenset(
    {
        SLACK_WEBHOOK_SOURCE_DEPLOY_TIME,
        SLACK_WEBHOOK_SOURCE_MYSTERYBOX,
    }
)
SLACK_WEBHOOK_URL_ENV = "NEBIUS_CXCLI_SOPERATOR_SLACK_WEBHOOK_URL"
SLACK_CLIENT_SECRET_ENV = "NEBIUS_CXCLI_SLACK_CLIENT_SECRET"
SLACK_OAUTH_CODE_ENV = "NEBIUS_CXCLI_SLACK_OAUTH_CODE"
SLACK_OAUTH_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"
SLACK_GOV_OAUTH_AUTHORIZE_URL = "https://slack-gov.com/oauth/v2/authorize"
SLACK_GOV_OAUTH_ACCESS_URL = "https://slack-gov.com/api/oauth.v2.access"
KUBE_CONTEXT_ENV = "NEBIUS_CXCLI_TARGET_KUBE_CONTEXT"
RuntimeSecretKey = tuple[str, str, str]
REQUIRED_VICTORIAMETRICS_CRDS = (
    "vmalertmanagerconfigs.operator.victoriametrics.com",
    "vmalertmanagers.operator.victoriametrics.com",
    "vmrules.operator.victoriametrics.com",
    "vmalerts.operator.victoriametrics.com",
)


@dataclass(frozen=True)
class SlackOAuthWebhook:
    url: str
    channel: str = ""
    channel_id: str = ""


@dataclass(frozen=True)
class SlackNotifierSpec:
    target_ref: str
    namespace: str
    release_name: str
    mode: str
    secret_name: str
    secret_key: str
    channel_name: str = ""
    channel_id: str = ""
    oauth_client_id: str = ""
    oauth_redirect_uri: str = ""
    gov_slack: bool = False
    webhook_source: str = SLACK_WEBHOOK_SOURCE_DEPLOY_TIME
    mysterybox_secret_id: str = ""
    mysterybox_property: str = ""


def _as_payload(value: Any) -> dict[str, Any]:
    payload = to_plain_data(value)
    return payload if isinstance(payload, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _active_notifier_rows(payload_or_config: Any) -> tuple[dict[str, Any], ...]:
    payload = _as_payload(payload_or_config)
    rows = _mapping(payload.get("apps")).get("charts")
    if not isinstance(rows, list):
        return ()
    return tuple(
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and bool(row.get("enabled", False))
        and component_type_id(row) == SOPERATOR_COMPONENT_ID
        and read_component_path(row, f"values.{SOPERATOR_NOTIFIER_VALUES_KEY}.enabled") is True
    )


def _soperator_row_target_ref(row: Mapping[str, Any]) -> str:
    target_ref = app_chart_target_ref(row)
    if target_ref:
        return target_ref
    instance_id = component_instance_id(row)
    if instance_id and instance_id != SOPERATOR_COMPONENT_ID:
        return instance_id
    return ""


def _validate_mysterybox_secret_id(secret_id: str) -> str:
    value = str(secret_id or "").strip()
    if not value:
        raise RuntimeError(
            "Soperator Slack notifier MysteryBox webhook source requires "
            f"values.{SOPERATOR_NOTIFIER_VALUES_KEY}.slack.mysterybox.secretId"
        )
    if value.startswith("mbsecver-"):
        raise RuntimeError(
            "Soperator Slack notifier MysteryBox webhook source requires a MysteryBox "
            "Secret ID such as `mbsec-...`, not a Secret version ID. The primary "
            "version is used automatically."
        )
    if not re.fullmatch(r"mbsec-[a-z0-9][a-z0-9-]*", value):
        raise RuntimeError(
            "Soperator Slack notifier MysteryBox webhook source requires a MysteryBox "
            "Secret ID with the `mbsec-...` form."
        )
    return value


def soperator_notifier_release_specs(
    payload_or_config: Any,
    *,
    target_ref: str = "",
) -> tuple[SlackNotifierSpec, ...]:
    normalized_target_ref = str(target_ref or "").strip().lower()
    specs: list[SlackNotifierSpec] = []
    for row in _active_notifier_rows(payload_or_config):
        row_target_ref = _soperator_row_target_ref(row)
        if normalized_target_ref and row_target_ref != normalized_target_ref:
            continue
        if not normalized_target_ref and row_target_ref:
            continue

        values = _mapping(row.get("values"))
        notifier_values = _mapping(values.get(SOPERATOR_NOTIFIER_VALUES_KEY))
        slack = _mapping(notifier_values.get("slack"))
        if "webhookUrl" in slack:
            raise RuntimeError(
                "apps.charts[] soperator values must not contain "
                f"values.{SOPERATOR_NOTIFIER_VALUES_KEY}.slack.webhookUrl. Store the URL "
                "in the runtime Kubernetes Secret referenced by "
                f"values.{SOPERATOR_NOTIFIER_VALUES_KEY}.slack.existingSecret and "
                f"values.{SOPERATOR_NOTIFIER_VALUES_KEY}.slack.existingSecretKey."
            )
        oauth = _mapping(slack.get("oauth"))
        mode = str(slack.get("mode") or "existing-webhook").strip()
        if mode not in {"existing-webhook", "oauth-webhook"}:
            raise RuntimeError(
                "apps.charts[] soperator "
                f"values.{SOPERATOR_NOTIFIER_VALUES_KEY}.slack.mode must be one of: "
                "existing-webhook, oauth-webhook"
            )
        webhook_source = str(
            slack.get("webhookSource") or SLACK_WEBHOOK_SOURCE_DEPLOY_TIME
        ).strip()
        if webhook_source not in SLACK_WEBHOOK_SOURCES:
            raise RuntimeError(
                "apps.charts[] soperator "
                f"values.{SOPERATOR_NOTIFIER_VALUES_KEY}.slack.webhookSource must be "
                "one of: deploy-time, mysterybox"
            )
        mysterybox = _mapping(slack.get("mysterybox"))
        mysterybox_secret_id = ""
        mysterybox_property = ""
        if webhook_source == SLACK_WEBHOOK_SOURCE_MYSTERYBOX:
            if mode != "existing-webhook":
                raise RuntimeError(
                    "Soperator Slack notifier MysteryBox webhook source requires "
                    f"values.{SOPERATOR_NOTIFIER_VALUES_KEY}.slack.mode=existing-webhook"
                )
            mysterybox_secret_id = _validate_mysterybox_secret_id(
                str(mysterybox.get("secretId") or "")
            )
            mysterybox_property = str(mysterybox.get("property") or "").strip()
        secret_name = str(
            slack.get("existingSecret") or "soperator-notifier-slack-webhook"
        ).strip()
        secret_key = str(slack.get("existingSecretKey") or "url").strip()
        specs.append(
            SlackNotifierSpec(
                target_ref=row_target_ref,
                namespace=str(row.get("namespace") or "soperator").strip() or "soperator",
                release_name=str(
                    notifier_values.get("fullnameOverride") or SOPERATOR_NOTIFIER_VALUES_KEY
                ).strip()
                or SOPERATOR_NOTIFIER_VALUES_KEY,
                mode=mode,
                secret_name=secret_name,
                secret_key=secret_key,
                channel_name=str(slack.get("channelName") or "").strip(),
                channel_id=str(slack.get("channelId") or "").strip(),
                oauth_client_id=str(oauth.get("clientId") or "").strip(),
                oauth_redirect_uri=str(oauth.get("redirectUri") or "").strip(),
                gov_slack=bool(oauth.get("govSlack", False)),
                webhook_source=webhook_source,
                mysterybox_secret_id=mysterybox_secret_id,
                mysterybox_property=mysterybox_property or secret_key,
            )
        )
    return tuple(specs)


def soperator_notifier_mysterybox_secret_refs(
    payload_or_config: Any,
    *,
    target_ref: str = "",
) -> tuple[dict[str, str], ...]:
    """Return MysteryBox-backed Secret refs required by Soperator notifier."""
    refs: list[dict[str, str]] = []
    target_refs = (target_ref,)
    if not target_ref:
        seen_targets: set[str] = set()
        discovered: list[str] = []
        for row in _active_notifier_rows(payload_or_config):
            row_target_ref = _soperator_row_target_ref(row)
            if row_target_ref in seen_targets:
                continue
            seen_targets.add(row_target_ref)
            discovered.append(row_target_ref)
        target_refs = tuple(discovered or [""])
    for current_target_ref in target_refs:
        specs = soperator_notifier_release_specs(payload_or_config, target_ref=current_target_ref)
        for spec in specs:
            if spec.webhook_source != SLACK_WEBHOOK_SOURCE_MYSTERYBOX:
                continue
            refs.append(
                {
                    "target_ref": spec.target_ref,
                    "namespace": spec.namespace,
                    "name": spec.secret_name,
                    "target_name": spec.secret_name,
                    "secret_key": spec.secret_key,
                    "secret_id": spec.mysterybox_secret_id,
                    "property": spec.mysterybox_property or spec.secret_key,
                }
            )
    return tuple(refs)


def soperator_notifier_enabled_for_target(
    payload_or_config: Any,
    *,
    target_ref: str = "",
) -> bool:
    return bool(soperator_notifier_release_specs(payload_or_config, target_ref=target_ref))


def _kubectl_env(extra_env: Mapping[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    return env


def _target_kube_context(extra_env: Mapping[str, str] | None) -> str:
    explicit_context = str((extra_env or {}).get(KUBE_CONTEXT_ENV) or "").strip()
    if explicit_context:
        return explicit_context
    env_context = str(os.environ.get(KUBE_CONTEXT_ENV) or "").strip()
    if env_context:
        return env_context
    kubeconfig_value = str(
        (extra_env or {}).get("KUBECONFIG") or os.environ.get("KUBECONFIG") or ""
    )
    kubeconfig_paths = (
        tuple(item for item in kubeconfig_value.split(os.pathsep) if item)
        if kubeconfig_value
        else (os.path.expanduser("~/.kube/config"),)
    )
    for kubeconfig_path in kubeconfig_paths:
        try:
            with open(kubeconfig_path, encoding="utf-8") as handle:
                payload = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError):
            continue
        context = str(_mapping(payload).get("current-context") or "").strip()
        if context:
            return context
    return ""


def _kubectl_command(
    args: Sequence[str],
    *,
    extra_env: Mapping[str, str] | None,
) -> list[str]:
    command = ["kubectl"]
    context_name = _target_kube_context(extra_env)
    if context_name:
        command.extend(["--context", context_name])
    command.extend(str(arg) for arg in args)
    return command


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _run_kubectl(
    args: Sequence[str],
    *,
    extra_env: Mapping[str, str] | None,
    input_text: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    command = _kubectl_command(args, extra_env=extra_env)
    completed = subprocess.run(
        command,
        env=_kubectl_env(extra_env),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = _first_non_empty_line(completed.stderr or completed.stdout or "")
        raise RuntimeError(f"{' '.join(command)} failed: {detail or completed.returncode}")
    return completed


def _apply_manifest(
    manifest: Mapping[str, Any],
    *,
    extra_env: Mapping[str, str] | None,
) -> None:
    rendered = yaml.safe_dump(dict(manifest), sort_keys=False)
    _run_kubectl(["apply", "-f", "-"], extra_env=extra_env, input_text=rendered)


def _ensure_namespace(namespace: str, *, extra_env: Mapping[str, str] | None) -> None:
    _apply_manifest(
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": namespace},
        },
        extra_env=extra_env,
    )


def _secret_has_keys(
    *,
    namespace: str,
    name: str,
    keys: Sequence[str],
    extra_env: Mapping[str, str] | None,
) -> bool:
    command = _kubectl_command(
        ["-n", namespace, "get", "secret", name, "-o", "json"],
        extra_env=extra_env,
    )
    completed = subprocess.run(
        command,
        env=_kubectl_env(extra_env),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").lower()
        if "notfound" in detail or "not found" in detail or "notfound" in detail.replace(" ", ""):
            return False
        message = _first_non_empty_line(completed.stderr or completed.stdout or "")
        raise RuntimeError(f"{' '.join(command)} failed: {message or completed.returncode}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{' '.join(command)} returned invalid JSON") from exc
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return False
    return all(str(key) in data for key in keys)


def _apply_secret(
    *,
    namespace: str,
    name: str,
    string_data: Mapping[str, str],
    extra_env: Mapping[str, str] | None,
) -> None:
    _apply_manifest(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {"name": name, "namespace": namespace},
            "stringData": dict(string_data),
        },
        extra_env=extra_env,
    )


def _crd_exists(crd_name: str, *, extra_env: Mapping[str, str] | None) -> bool:
    command = _kubectl_command(["get", "crd", crd_name, "-o", "name"], extra_env=extra_env)
    completed = subprocess.run(
        command,
        env=_kubectl_env(extra_env),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode == 0:
        return True
    detail = (completed.stderr or completed.stdout or "").lower()
    if "notfound" in detail or "not found" in detail or "notfound" in detail.replace(" ", ""):
        return False
    message = _first_non_empty_line(completed.stderr or completed.stdout or "")
    raise RuntimeError(f"{' '.join(command)} failed: {message or completed.returncode}")


def _validate_victoriametrics_crds(*, extra_env: Mapping[str, str] | None) -> None:
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required to deploy Soperator Slack notifier")
    missing = [
        crd_name
        for crd_name in REQUIRED_VICTORIAMETRICS_CRDS
        if not _crd_exists(crd_name, extra_env=extra_env)
    ]
    if missing:
        raise RuntimeError(
            "Soperator Slack notifier requires VictoriaMetrics Operator CRDs before deploy: "
            + ", ".join(missing)
            + ". Install a VictoriaMetrics Operator stack first, or disable apps:soperator "
            + f"values.{SOPERATOR_NOTIFIER_VALUES_KEY}.enabled."
        )


def _target_env_names(base_name: str, target_ref: str) -> tuple[str, ...]:
    suffix = re.sub(r"[^A-Z0-9]+", "_", str(target_ref or "").upper()).strip("_")
    if suffix:
        return (f"{base_name}_{suffix}",)
    return (base_name,)


def _env_value(
    base_name: str,
    *,
    target_ref: str,
    extra_env: Mapping[str, str] | None,
) -> str:
    merged = _kubectl_env(extra_env)
    for name in _target_env_names(base_name, target_ref):
        value = str(merged.get(name) or "").strip()
        if value:
            return value
    return ""


def _validate_webhook_url(webhook_url: str) -> str:
    url = str(webhook_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError("Slack incoming webhook URL must use https")
    if parsed.netloc not in {"hooks.slack.com", "hooks.slack-gov.com"}:
        raise RuntimeError(
            "Slack incoming webhook URL must use hooks.slack.com or hooks.slack-gov.com"
        )
    if not parsed.path.startswith("/services/"):
        raise RuntimeError("Slack incoming webhook URL path must start with /services/")
    return url


def _validate_https_redirect_uri(redirect_uri: str) -> str:
    value = str(redirect_uri or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("Slack OAuth redirect URI must be a registered HTTPS URL")
    return value


def _oauth_authorize_url(*, gov_slack: bool) -> str:
    return SLACK_GOV_OAUTH_AUTHORIZE_URL if gov_slack else SLACK_OAUTH_AUTHORIZE_URL


def _oauth_access_url(*, gov_slack: bool) -> str:
    return SLACK_GOV_OAUTH_ACCESS_URL if gov_slack else SLACK_OAUTH_ACCESS_URL


def build_slack_oauth_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    gov_slack: bool = False,
) -> str:
    params = {
        "client_id": str(client_id).strip(),
        "redirect_uri": _validate_https_redirect_uri(redirect_uri),
        "scope": "incoming-webhook",
    }
    if state:
        params["state"] = state
    return f"{_oauth_authorize_url(gov_slack=gov_slack)}?{urlencode(params)}"


def _callback_code_and_state(raw_value: str) -> tuple[str, str]:
    raw = str(raw_value or "").strip()
    if not raw:
        return "", ""
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        params = parse_qs(parsed.query)
        return (params.get("code") or [""])[0].strip(), (params.get("state") or [""])[0].strip()
    return raw, ""


def exchange_slack_oauth_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    gov_slack: bool = False,
    timeout: int = 20,
) -> SlackOAuthWebhook:
    redirect_uri = _validate_https_redirect_uri(redirect_uri)
    body = urlencode({"code": str(code).strip(), "redirect_uri": redirect_uri}).encode("utf-8")
    credentials = base64.b64encode(
        f"{str(client_id).strip()}:{str(client_secret).strip()}".encode()
    ).decode("ascii")
    request = Request(
        _oauth_access_url(gov_slack=gov_slack),
        data=body,
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"Slack OAuth token exchange returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Slack OAuth token exchange failed: {exc.reason}") from exc
    try:
        payload = json.loads(response_body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Slack OAuth token exchange returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("Slack OAuth token exchange did not return a JSON object")
    if not bool(payload.get("ok", False)):
        error = str(payload.get("error") or "unknown_error").strip()
        raise RuntimeError(f"Slack OAuth token exchange failed: {error}")
    incoming_webhook = payload.get("incoming_webhook")
    if not isinstance(incoming_webhook, Mapping):
        raise RuntimeError("Slack OAuth response did not include incoming_webhook")
    url = _validate_webhook_url(str(incoming_webhook.get("url") or ""))
    return SlackOAuthWebhook(
        url=url,
        channel=str(incoming_webhook.get("channel") or "").strip(),
        channel_id=str(incoming_webhook.get("channel_id") or "").strip(),
    )


def _existing_webhook_url(
    spec: SlackNotifierSpec,
    *,
    extra_env: Mapping[str, str] | None,
    prompt: bool,
) -> str:
    webhook_url = _env_value(
        SLACK_WEBHOOK_URL_ENV,
        target_ref=spec.target_ref,
        extra_env=extra_env,
    )
    if webhook_url:
        return _validate_webhook_url(webhook_url)
    if prompt:
        value = getpass(
            f"Slack incoming webhook URL for {spec.release_name} "
            f"({spec.namespace}/{spec.secret_name}:{spec.secret_key}): "
        ).strip()
        if value:
            return _validate_webhook_url(value)
    webhook_env = _target_env_names(SLACK_WEBHOOK_URL_ENV, spec.target_ref)[0]
    raise RuntimeError(
        f"Soperator Slack notifier Secret {spec.namespace}/{spec.secret_name}:{spec.secret_key} "
        f"is missing. Set {webhook_env}, rerun interactively, or precreate "
        "the Kubernetes Secret."
    )


def _runtime_secret_key(spec: SlackNotifierSpec) -> RuntimeSecretKey:
    return (spec.namespace, spec.secret_name, spec.secret_key)


def _oauth_webhook(
    spec: SlackNotifierSpec,
    *,
    extra_env: Mapping[str, str] | None,
    prompt: bool,
    emit: Callable[[str], None] | None,
) -> SlackOAuthWebhook:
    client_id = spec.oauth_client_id
    redirect_uri = spec.oauth_redirect_uri
    if not client_id:
        raise RuntimeError(
            "Soperator Slack notifier OAuth mode requires "
            "values.soperator-notifier.slack.oauth.clientId"
        )
    if not redirect_uri:
        raise RuntimeError(
            "Soperator Slack notifier OAuth mode requires "
            "values.soperator-notifier.slack.oauth.redirectUri"
        )
    redirect_uri = _validate_https_redirect_uri(redirect_uri)
    client_secret = _env_value(
        SLACK_CLIENT_SECRET_ENV,
        target_ref=spec.target_ref,
        extra_env=extra_env,
    )
    if not client_secret and prompt:
        client_secret = getpass("Slack app client secret: ").strip()
    if not client_secret:
        raise RuntimeError(
            "Soperator Slack notifier OAuth mode requires "
            f"{SLACK_CLIENT_SECRET_ENV} or an interactive hidden prompt."
        )

    raw_code = _env_value(SLACK_OAUTH_CODE_ENV, target_ref=spec.target_ref, extra_env=extra_env)
    expected_state = ""
    if not raw_code:
        if not prompt:
            raise RuntimeError(
                "Soperator Slack notifier OAuth mode requires an interactive terminal "
                f"or {SLACK_OAUTH_CODE_ENV}."
            )
        expected_state = secrets.token_urlsafe(24)
        authorize_url = build_slack_oauth_authorize_url(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=expected_state,
            gov_slack=spec.gov_slack,
        )
        if callable(emit):
            emit("Opening Slack OAuth authorization in the default browser.")
            emit(authorize_url)
        webbrowser.open(authorize_url)
        raw_code = input("Paste the full Slack OAuth redirect URL after approval: ").strip()

    code, returned_state = _callback_code_and_state(raw_code)
    if expected_state and returned_state != expected_state:
        raise RuntimeError("Slack OAuth callback state did not match the generated state")
    if not code:
        raise RuntimeError("Slack OAuth callback did not include a code")
    return exchange_slack_oauth_code(
        client_id=client_id,
        client_secret=client_secret,
        code=code,
        redirect_uri=redirect_uri,
        gov_slack=spec.gov_slack,
    )


def ensure_soperator_notifier_runtime_secrets(
    payload_or_config: Any,
    *,
    extra_env: Mapping[str, str] | None,
    target_ref: str = "",
    prompt: bool = False,
    emit: Callable[[str], None] | None = None,
    externally_managed_secret_keys: Collection[RuntimeSecretKey] | None = None,
) -> None:
    """Create runtime-only Kubernetes Secrets required by Soperator notifier."""
    specs = soperator_notifier_release_specs(payload_or_config, target_ref=target_ref)
    if not specs:
        return
    _validate_victoriametrics_crds(extra_env=extra_env)
    for spec in specs:
        _ensure_namespace(spec.namespace, extra_env=extra_env)
        runtime_key = _runtime_secret_key(spec)
        if spec.webhook_source == SLACK_WEBHOOK_SOURCE_MYSTERYBOX:
            if runtime_key not in (externally_managed_secret_keys or ()):
                raise RuntimeError(
                    "Soperator Slack notifier is configured to read the incoming webhook "
                    "from Nebius MysteryBox, but the generated MysteryBox ExternalSecret "
                    f"does not manage `{spec.namespace}/{spec.secret_name}:{spec.secret_key}`. "
                    "Render/apply the target MysteryBox ESO resources, or set "
                    f"values.{SOPERATOR_NOTIFIER_VALUES_KEY}.slack.webhookSource="
                    f"{SLACK_WEBHOOK_SOURCE_DEPLOY_TIME} to provide the webhook URL at deploy time."
                )
            if callable(emit):
                emit(
                    "Soperator Slack notifier Secret "
                    f"`{spec.namespace}/{spec.secret_name}:{spec.secret_key}` "
                    "is managed by MysteryBox External Secrets; skipping direct "
                    "webhook materialization."
                )
            continue
        if _secret_has_keys(
            namespace=spec.namespace,
            name=spec.secret_name,
            keys=(spec.secret_key,),
            extra_env=extra_env,
        ):
            continue
        if (
            spec.mode == "existing-webhook"
            and runtime_key in (externally_managed_secret_keys or ())
        ):
            if callable(emit):
                emit(
                    "Soperator Slack notifier Secret "
                    f"`{spec.namespace}/{spec.secret_name}:{spec.secret_key}` "
                    "is managed by MysteryBox External Secrets; skipping direct "
                    "webhook materialization."
                )
            continue
        if spec.mode == "oauth-webhook":
            webhook = _oauth_webhook(spec, extra_env=extra_env, prompt=prompt, emit=emit)
            webhook_url = webhook.url
            if callable(emit):
                channel = (
                    webhook.channel or spec.channel_name or webhook.channel_id or spec.channel_id
                )
                if channel:
                    emit(f"Authorized Slack incoming webhook for channel `{channel}`.")
        else:
            webhook_url = _existing_webhook_url(spec, extra_env=extra_env, prompt=prompt)
        _apply_secret(
            namespace=spec.namespace,
            name=spec.secret_name,
            string_data={spec.secret_key: webhook_url},
            extra_env=extra_env,
        )
        if callable(emit):
            emit(f"Created Soperator Slack notifier Secret `{spec.secret_name}`.")


__all__ = [
    "SLACK_CLIENT_SECRET_ENV",
    "SLACK_OAUTH_CODE_ENV",
    "SLACK_WEBHOOK_URL_ENV",
    "SlackNotifierSpec",
    "SlackOAuthWebhook",
    "build_slack_oauth_authorize_url",
    "ensure_soperator_notifier_runtime_secrets",
    "exchange_slack_oauth_code",
    "soperator_notifier_enabled_for_target",
    "soperator_notifier_mysterybox_secret_refs",
    "soperator_notifier_release_specs",
]
