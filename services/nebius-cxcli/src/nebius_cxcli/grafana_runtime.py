"""Deploy-time Grafana secret bootstrap and status reporting."""

from __future__ import annotations

import json
import os
import re
import secrets
import string
import subprocess
import time
from base64 import b64decode, b64encode
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

import yaml

from .component_instances import component_type_id
from .component_sources import (
    GrafanaCliSettings,
    GrafanaDashboardSignalBinding,
    GrafanaDatasourceSpec,
    load_component_sources,
)
from .deploy_targets import app_chart_target_ref
from .iam_bootstrap import issue_observability_static_key
from .observability import observability_endpoint_summary
from .runtime_config import to_plain_data

GRAFANA_STATUS_FILENAME = "grafana-status.json"
GRAFANA_TARGET_CLUSTER_ID_ENV = "NEBIUS_CXCLI_TARGET_CLUSTER_ID"
GRAFANA_TARGET_KUBE_CONTEXT_ENV = "NEBIUS_CXCLI_TARGET_KUBE_CONTEXT"


@dataclass(frozen=True)
class GrafanaReleaseSpec:
    target_ref: str
    namespace: str
    release_name: str
    service_name: str
    admin_secret_name: str
    admin_user: str
    admin_user_key: str
    admin_password_key: str
    token_secret_name: str
    token_key: str
    gateway_name: str = ""
    gateway_namespace: str = ""


def _as_payload(value: Any) -> dict[str, Any]:
    payload = to_plain_data(value)
    return payload if isinstance(payload, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _grafana_component_id() -> str:
    try:
        sources = load_component_sources()
    except (OSError, ValueError, RuntimeError):
        return ""
    for module in sources.tf_modules:
        component_id = str(module.observability.grafana.chart_component_id or "").strip()
        if component_id:
            return component_id
    empty_settings = GrafanaCliSettings()
    for chart in sources.helm_charts:
        if chart.grafana != empty_settings:
            return chart.name
    return ""


def _active_grafana_rows(payload_or_config: Any) -> tuple[dict[str, Any], ...]:
    grafana_component_id = _grafana_component_id()
    if not grafana_component_id:
        return ()
    payload = _as_payload(payload_or_config)
    rows = _mapping(payload.get("apps")).get("charts")
    if not isinstance(rows, list):
        return ()
    return tuple(
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and bool(row.get("enabled", False))
        and component_type_id(row) == grafana_component_id
    )


def grafana_release_specs(
    payload_or_config: Any,
    *,
    target_ref: str = "",
) -> tuple[GrafanaReleaseSpec, ...]:
    normalized_target_ref = str(target_ref or "").strip().lower()
    grafana_component_id = _grafana_component_id()
    if not grafana_component_id:
        return ()
    grafana_settings = _grafana_cli_settings()
    specs: list[GrafanaReleaseSpec] = []
    for row in _active_grafana_rows(payload_or_config):
        row_target_ref = app_chart_target_ref(row)
        if normalized_target_ref and row_target_ref != normalized_target_ref:
            continue
        if not normalized_target_ref and row_target_ref:
            continue
        values = _mapping(row.get("values"))
        admin = _mapping(values.get("admin"))
        env_value_from = _mapping(values.get("envValueFrom"))
        read_token_env = grafana_settings.read_token.env
        token_env = _mapping(env_value_from.get(read_token_env)) if read_token_env else {}
        token_secret_ref = _mapping(token_env.get("secretKeyRef"))
        route_main = _mapping(_mapping(values.get("route")).get("main"))
        parent_refs = route_main.get("parentRefs")
        gateway_name = ""
        gateway_namespace = ""
        if bool(route_main.get("enabled")) and isinstance(parent_refs, list) and parent_refs:
            parent_ref = _mapping(parent_refs[0])
            gateway_name = str(parent_ref.get("name") or "").strip()
            gateway_namespace = str(parent_ref.get("namespace") or "").strip()
        release_name = (
            str(row.get("release-name") or grafana_component_id).strip() or grafana_component_id
        )
        namespace = str(row.get("namespace") or "observability").strip() or "observability"
        if gateway_name and not gateway_namespace:
            gateway_namespace = namespace
        specs.append(
            GrafanaReleaseSpec(
                target_ref=row_target_ref,
                namespace=namespace,
                release_name=release_name,
                service_name=release_name,
                admin_secret_name=str(
                    admin.get("existingSecret") or grafana_settings.admin_secret.secret_name
                ).strip(),
                admin_user=grafana_settings.admin_secret.user,
                admin_user_key=str(
                    admin.get("userKey") or grafana_settings.admin_secret.user_key
                ).strip(),
                admin_password_key=str(
                    admin.get("passwordKey") or grafana_settings.admin_secret.password_key
                ).strip(),
                token_secret_name=str(
                    token_secret_ref.get("name") or grafana_settings.read_token.secret_name
                ).strip(),
                token_key=str(
                    token_secret_ref.get("key") or grafana_settings.read_token.key
                ).strip(),
                gateway_name=gateway_name,
                gateway_namespace=gateway_namespace,
            )
        )
    return tuple(specs)


def grafana_enabled_for_target(payload_or_config: Any, *, target_ref: str = "") -> bool:
    return bool(grafana_release_specs(payload_or_config, target_ref=target_ref))


def _kubectl_env(extra_env: Mapping[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    return env


def _target_kube_context(extra_env: Mapping[str, str] | None) -> str:
    return str((extra_env or {}).get(GRAFANA_TARGET_KUBE_CONTEXT_ENV) or "").strip() or (
        _current_kube_context(extra_env)
    )


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


def _kubectl_command_text(
    args: Sequence[str],
    *,
    extra_env: Mapping[str, str] | None,
) -> str:
    return " ".join(_kubectl_command(args, extra_env=extra_env))


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _json_error_snippet(text: str, *, limit: int = 240) -> str:
    snippet = " ".join(str(text or "").strip().split())
    if not snippet:
        return "(empty response)"
    if len(snippet) > limit:
        return snippet[: limit - 3] + "..."
    return snippet


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


def _kubectl_json(
    args: Sequence[str],
    *,
    extra_env: Mapping[str, str] | None,
    timeout: int = 60,
) -> dict[str, Any]:
    completed = _run_kubectl(args, extra_env=extra_env, timeout=timeout)
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{_kubectl_command_text(args, extra_env=extra_env)} returned invalid JSON: "
            f"{_json_error_snippet(completed.stdout)}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"{_kubectl_command_text(args, extra_env=extra_env)} did not return a JSON object"
        )
    return payload


def _apply_manifest(
    manifest: Mapping[str, Any],
    *,
    extra_env: Mapping[str, str] | None,
) -> None:
    rendered = yaml.safe_dump(dict(manifest), sort_keys=False)
    _run_kubectl(["apply", "-f", "-"], extra_env=extra_env, input_text=rendered)


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
        raise RuntimeError(
            f"{' '.join(command)} failed: {message or completed.returncode}"
        )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{' '.join(command)} returned invalid JSON: "
            f"{_json_error_snippet(completed.stdout)}"
        ) from exc
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return False
    return all(str(key) in data for key in keys)


def _secret_data_values(
    *,
    namespace: str,
    name: str,
    keys: Sequence[str],
    extra_env: Mapping[str, str] | None,
) -> dict[str, str]:
    payload = _kubectl_json(
        ["-n", namespace, "get", "secret", name, "-o", "json"],
        extra_env=extra_env,
    )
    data = _mapping(payload.get("data"))
    values: dict[str, str] = {}
    for key in keys:
        encoded = data.get(str(key))
        if not isinstance(encoded, str) or not encoded:
            continue
        try:
            values[str(key)] = b64decode(encoded).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"kubectl -n {namespace} get secret {name} returned invalid data for {key}"
            ) from exc
    return values


def _ensure_namespace(namespace: str, *, extra_env: Mapping[str, str] | None) -> None:
    _apply_manifest(
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": namespace},
        },
        extra_env=extra_env,
    )


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


def _generate_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "-_."
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _safe_name_segment(value: str, *, fallback: str) -> str:
    token = re.sub(r"[^a-z0-9-]+", "-", str(value or "").lower()).strip("-")
    return token or fallback


def _project_id(payload_or_config: Any) -> str:
    payload = _as_payload(payload_or_config)
    nebius = _mapping(_mapping(payload.get("client_info")).get("nebius"))
    return str(nebius.get("project_id") or "").strip()


def _client_name(payload_or_config: Any) -> str:
    payload = _as_payload(payload_or_config)
    return str(_mapping(payload.get("client_info")).get("client_name") or "cxcli").strip()


def _issue_read_token(payload_or_config: Any, *, target_ref: str) -> str:
    project_id = _project_id(payload_or_config)
    if not project_id:
        raise RuntimeError("client_info.nebius.project_id is required to issue Grafana read token")
    client_name = _safe_name_segment(_client_name(payload_or_config), fallback="cxcli")
    target_segment = _safe_name_segment(target_ref or "cluster", fallback="cluster")
    service_account_name = f"{client_name}-grafana-observability-read"
    if len(service_account_name) > 63:
        service_account_name = f"{client_name[:38].rstrip('-')}-grafana-read"
    issued_at = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    key_name = f"{client_name}-{target_segment}-grafana-read-{issued_at}"
    if len(key_name) > 63:
        key_name = f"{client_name[:26].rstrip('-')}-{target_segment[:12]}-grafana-{issued_at}"
    result = issue_observability_static_key(
        project_id=project_id,
        service_account_name=service_account_name,
        service_account_description=(
            "nebius-cxcli Grafana read-only access to Observability public read endpoints"
        ),
        key_name=key_name,
        role_ids=["viewer"],
        profile=None,
        endpoint=str(os.environ.get("NEBIUS_ENDPOINT") or "").strip() or None,
        config_file=None,
    )
    return result.token


def _read_token_probe_url(payload_or_config: Any) -> str:
    endpoints = _mapping(observability_endpoint_summary(payload_or_config).get("read"))
    for datasource in _grafana_cli_settings().datasources:
        if datasource.datasource_type != "prometheus":
            continue
        read_endpoint = str(endpoints.get(datasource.read_endpoint) or "").strip()
        if read_endpoint:
            return read_endpoint.rstrip("/") + "/api/v1/query?query=1"
    return ""


def _observability_read_token_status(
    payload_or_config: Any,
    token: str,
    *,
    timeout: int = 10,
) -> bool | None:
    probe_url = _read_token_probe_url(payload_or_config)
    if not probe_url or not token:
        return None
    request = Request(
        probe_url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= int(getattr(response, "status", 0) or 0) < 300
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return False
        return None
    except URLError:
        return None


def _ensure_grafana_read_token_secret(
    payload_or_config: Any,
    spec: GrafanaReleaseSpec,
    *,
    extra_env: Mapping[str, str] | None,
    target_ref: str,
    emit: Any | None,
) -> None:
    if not _secret_has_keys(
        namespace=spec.namespace,
        name=spec.token_secret_name,
        keys=(spec.token_key,),
        extra_env=extra_env,
    ):
        token = _issue_read_token(payload_or_config, target_ref=spec.target_ref or target_ref)
        _apply_secret(
            namespace=spec.namespace,
            name=spec.token_secret_name,
            string_data={spec.token_key: token},
            extra_env=extra_env,
        )
        if callable(emit):
            emit(f"Created Grafana Observability read-token secret `{spec.token_secret_name}`.")
        return

    token_values = _secret_data_values(
        namespace=spec.namespace,
        name=spec.token_secret_name,
        keys=(spec.token_key,),
        extra_env=extra_env,
    )
    token = token_values.get(spec.token_key, "")
    token_status = _observability_read_token_status(payload_or_config, token)
    if token_status is not False:
        return
    replacement = _issue_read_token(payload_or_config, target_ref=spec.target_ref or target_ref)
    _apply_secret(
        namespace=spec.namespace,
        name=spec.token_secret_name,
        string_data={spec.token_key: replacement},
        extra_env=extra_env,
    )
    if callable(emit):
        emit(f"Refreshed Grafana Observability read-token secret `{spec.token_secret_name}`.")


def ensure_grafana_runtime_secrets(
    payload_or_config: Any,
    *,
    extra_env: Mapping[str, str] | None,
    target_ref: str = "",
    emit: Any | None = None,
) -> None:
    """Create the runtime-only Kubernetes Secrets required by Grafana."""
    for spec in grafana_release_specs(payload_or_config, target_ref=target_ref):
        _ensure_namespace(spec.namespace, extra_env=extra_env)
        if not _secret_has_keys(
            namespace=spec.namespace,
            name=spec.admin_secret_name,
            keys=(spec.admin_user_key, spec.admin_password_key),
            extra_env=extra_env,
        ):
            _apply_secret(
                namespace=spec.namespace,
                name=spec.admin_secret_name,
                string_data={
                    spec.admin_user_key: spec.admin_user,
                    spec.admin_password_key: _generate_password(),
                },
                extra_env=extra_env,
            )
            if callable(emit):
                emit(f"Created Grafana admin credential secret `{spec.admin_secret_name}`.")
        _ensure_grafana_read_token_secret(
            payload_or_config,
            spec,
            extra_env=extra_env,
            target_ref=target_ref,
            emit=emit,
        )


def _load_balancer_base_url(
    service: Mapping[str, Any],
) -> str:
    status = _mapping(service.get("status"))
    load_balancer = _mapping(status.get("loadBalancer"))
    ingress = load_balancer.get("ingress")
    host = ""
    if isinstance(ingress, list) and ingress:
        first = _mapping(ingress[0])
        host = str(first.get("hostname") or first.get("ip") or "").strip()
    if not host:
        return ""
    spec = _mapping(service.get("spec"))
    ports = spec.get("ports")
    port = 80
    if isinstance(ports, list) and ports:
        first_port = _mapping(ports[0]).get("port")
        if isinstance(first_port, int):
            port = first_port
    scheme = "https" if port == 443 else "http"
    suffix = "" if port in {80, 443} else f":{port}"
    return f"{scheme}://{host}{suffix}/"


def _gateway_base_url(gateway: Mapping[str, Any]) -> str:
    status = _mapping(gateway.get("status"))
    addresses = status.get("addresses")
    host = ""
    if isinstance(addresses, list) and addresses:
        first = _mapping(addresses[0])
        host = str(first.get("value") or "").strip()
    if not host:
        return ""
    spec = _mapping(gateway.get("spec"))
    listeners = spec.get("listeners")
    port = 80
    protocol = "HTTP"
    if isinstance(listeners, list) and listeners:
        first_listener = _mapping(listeners[0])
        first_port = first_listener.get("port")
        if isinstance(first_port, int):
            port = first_port
        protocol = str(first_listener.get("protocol") or protocol).strip().upper()
    scheme = "https" if protocol == "HTTPS" or port == 443 else "http"
    suffix = "" if port in {80, 443} else f":{port}"
    return f"{scheme}://{host}{suffix}/"


def _grafana_base_url(
    spec: GrafanaReleaseSpec,
    *,
    extra_env: Mapping[str, str] | None,
) -> str:
    if spec.gateway_name:
        try:
            gateway = _kubectl_json(
                [
                    "-n",
                    spec.gateway_namespace or spec.namespace,
                    "get",
                    "gateway",
                    spec.gateway_name,
                    "-o",
                    "json",
                ],
                extra_env=extra_env,
            )
            base_url = _gateway_base_url(gateway)
            if base_url:
                return base_url
        except RuntimeError:
            pass
    service = _kubectl_json(
        ["-n", spec.namespace, "get", "service", spec.service_name, "-o", "json"],
        extra_env=extra_env,
    )
    return _load_balancer_base_url(service)


def _current_kube_context(extra_env: Mapping[str, str] | None) -> str:
    explicit_context = str((extra_env or {}).get(GRAFANA_TARGET_KUBE_CONTEXT_ENV) or "").strip()
    if explicit_context:
        return explicit_context
    kubeconfig_value = str(
        (extra_env or {}).get("KUBECONFIG") or os.environ.get("KUBECONFIG") or ""
    )
    kubeconfig_paths = (
        tuple(item for item in kubeconfig_value.split(os.pathsep) if item)
        if kubeconfig_value
        else (str(Path.home().expanduser() / ".kube" / "config"),)
    )
    for kubeconfig_path in kubeconfig_paths:
        path = Path(kubeconfig_path).expanduser()
        if not path.exists():
            continue
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        context = str(_mapping(payload).get("current-context") or "").strip()
        if context:
            return context
    return ""


def _explore_url(
    base_url: str,
    *,
    datasource_uid: str,
    datasource_type: str,
    org_id: int,
    query: str = "",
) -> str:
    query_payload: dict[str, Any] = {
        "refId": "A",
        "datasource": {
            "uid": datasource_uid,
            "type": datasource_type,
        },
    }
    if query:
        query_payload["expr"] = query
    panes: dict[str, Any] = {
        "cxcli": {
            "datasource": datasource_uid,
            "queries": [query_payload],
            "range": {"from": "now-1h", "to": "now"},
        }
    }
    encoded = quote(json.dumps(panes, separators=(",", ":")), safe="")
    return urljoin(base_url, f"explore?schemaVersion=1&panes={encoded}&orgId={org_id}")


def _grafana_admin_credentials(
    spec: GrafanaReleaseSpec,
    *,
    extra_env: Mapping[str, str] | None,
) -> tuple[str, str] | None:
    values = _secret_data_values(
        namespace=spec.namespace,
        name=spec.admin_secret_name,
        keys=(spec.admin_user_key, spec.admin_password_key),
        extra_env=extra_env,
    )
    password = values.get(spec.admin_password_key)
    if not password:
        return None
    return values.get(spec.admin_user_key) or spec.admin_user, password


def _grafana_relative_path(url: str) -> str:
    parsed = urlparse(url)
    path = (parsed.path or "/").lstrip("/")
    if parsed.query:
        path = f"{path}?{parsed.query}"
    if parsed.fragment:
        path = f"{path}#{parsed.fragment}"
    return path


def _post_grafana_short_url(
    base_url: str,
    path: str,
    *,
    username: str,
    password: str,
) -> dict[str, Any]:
    credentials = b64encode(f"{username}:{password}".encode()).decode("ascii")
    request = Request(
        urljoin(base_url, "api/short-urls"),
        data=json.dumps({"path": path}).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"Grafana short URL API returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Grafana short URL API request failed: {exc.reason}") from exc
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Grafana short URL API returned invalid JSON: "
            f"{_json_error_snippet(body)}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Grafana short URL API did not return a JSON object")
    return payload


def _get_grafana_json(
    base_url: str,
    path: str,
    *,
    username: str,
    password: str,
) -> Any:
    credentials = b64encode(f"{username}:{password}".encode()).decode("ascii")
    request = Request(
        urljoin(base_url, path),
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {credentials}",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"Grafana API returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Grafana API request failed: {exc.reason}") from exc
    try:
        return json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Grafana API returned invalid JSON: {_json_error_snippet(body)}"
        ) from exc


def _public_grafana_url(base_url: str, grafana_url: str) -> str:
    parsed = urlparse(grafana_url)
    path = parsed.path.lstrip("/")
    if parsed.query:
        path = f"{path}?{parsed.query}"
    if parsed.fragment:
        path = f"{path}#{parsed.fragment}"
    return urljoin(base_url, path)


def _url_with_query_params(url: str, params: Mapping[str, str]) -> str:
    clean_params = {key: value for key, value in params.items() if key and value}
    if not clean_params:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(clean_params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _dashboard_url_with_target_variables(
    url: str,
    *,
    signal: str,
    extra_env: Mapping[str, str] | None,
) -> str:
    if signal not in {"metrics", "logs"}:
        return url
    cluster_id = str((extra_env or {}).get(GRAFANA_TARGET_CLUSTER_ID_ENV) or "").strip()
    if not cluster_id:
        return url
    return _url_with_query_params(url, {"var-Cluster": cluster_id})


def _grafana_dashboard_gnet_id(
    base_url: str,
    uid: str,
    *,
    username: str,
    password: str,
) -> int | None:
    payload = _get_grafana_json(
        base_url,
        f"api/dashboards/uid/{quote(uid, safe='')}",
        username=username,
        password=password,
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("Grafana dashboard detail API did not return a JSON object")
    dashboard = payload.get("dashboard")
    if not isinstance(dashboard, Mapping):
        return None
    raw_gnet_id = dashboard.get("gnetId")
    try:
        gnet_id = int(raw_gnet_id)
    except (TypeError, ValueError):
        return None
    return gnet_id if gnet_id > 0 else None


def _grafana_dashboard_url_by_uid(
    base_url: str,
    dashboard_uid: str,
    *,
    username: str,
    password: str,
) -> str:
    payload = _get_grafana_json(
        base_url,
        f"api/dashboards/uid/{quote(dashboard_uid, safe='')}",
        username=username,
        password=password,
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("Grafana dashboard detail API did not return a JSON object")
    meta_url = str(_mapping(payload.get("meta")).get("url") or "").strip()
    if meta_url:
        return _public_grafana_url(base_url, meta_url)
    dashboard = payload.get("dashboard")
    if isinstance(dashboard, Mapping) and str(dashboard.get("uid") or "").strip():
        return urljoin(base_url, f"d/{quote(str(dashboard.get('uid')), safe='')}")
    return ""


def _grafana_dashboard_url(
    base_url: str,
    dashboard_key: str,
    gnet_id: int,
    *,
    username: str,
    password: str,
) -> str:
    payload = _get_grafana_json(
        base_url,
        "api/search?type=dash-db&limit=5000",
        username=username,
        password=password,
    )
    if not isinstance(payload, list):
        raise RuntimeError("Grafana dashboard search API did not return a JSON array")
    candidates = [
        item
        for item in payload
        if isinstance(item, Mapping)
        and str(item.get("uid") or "").strip()
        and str(item.get("url") or "").strip()
    ]
    dashboard_key = dashboard_key.strip()
    preferred: list[Mapping[str, Any]] = []
    other: list[Mapping[str, Any]] = []
    for item in candidates:
        url = str(item.get("url") or "").strip()
        if f"/{dashboard_key}" in url:
            preferred.append(item)
        else:
            other.append(item)
    for item in [*preferred, *other]:
        uid = str(item.get("uid") or "").strip()
        try:
            candidate_gnet_id = _grafana_dashboard_gnet_id(
                base_url,
                uid,
                username=username,
                password=password,
            )
        except RuntimeError:
            continue
        if candidate_gnet_id == gnet_id:
            url = str(item.get("url") or "").strip()
            return _public_grafana_url(base_url, url) if url else ""
    return ""


def _grafana_dashboard_url_for_spec(
    base_url: str,
    dashboard_key: str,
    gnet_id: int,
    spec: GrafanaReleaseSpec,
    *,
    dashboard_uid: str = "",
    extra_env: Mapping[str, str] | None,
) -> str:
    try:
        credentials = _grafana_admin_credentials(spec, extra_env=extra_env)
    except RuntimeError:
        return ""
    if not credentials:
        return ""
    username, password = credentials
    try:
        if dashboard_uid:
            return _grafana_dashboard_url_by_uid(
                base_url,
                dashboard_uid,
                username=username,
                password=password,
            )
        if gnet_id <= 0:
            return ""
        return _grafana_dashboard_url(
            base_url,
            dashboard_key,
            gnet_id,
            username=username,
            password=password,
        )
    except RuntimeError:
        return ""


def _grafana_cli_settings() -> GrafanaCliSettings:
    grafana_component_id = _grafana_component_id()
    if not grafana_component_id:
        return GrafanaCliSettings()
    try:
        sources = load_component_sources()
    except (OSError, ValueError, RuntimeError):
        return GrafanaCliSettings()
    for chart in sources.helm_charts:
        if chart.name == grafana_component_id:
            return chart.grafana
    return GrafanaCliSettings()


def _grafana_dashboard_signal_bindings() -> dict[str, GrafanaDashboardSignalBinding]:
    settings = _grafana_cli_settings()
    return {item.signal: item for item in settings.dashboard_signals}


def _grafana_datasources_by_name() -> dict[str, GrafanaDatasourceSpec]:
    settings = _grafana_cli_settings()
    return {item.name: item for item in settings.datasources}


def _grafana_explore_urls(
    base_url: str,
    bindings: Mapping[str, GrafanaDashboardSignalBinding],
    datasources_by_name: Mapping[str, GrafanaDatasourceSpec],
    *,
    explore_queries: Mapping[str, str],
    org_id: int,
) -> dict[str, str]:
    urls: dict[str, str] = {}
    for signal, binding in bindings.items():
        datasource = datasources_by_name.get(binding.datasource)
        if datasource is None:
            continue
        urls[f"{signal}_url"] = _explore_url(
            base_url,
            datasource_uid=datasource.uid,
            datasource_type=datasource.datasource_type,
            org_id=org_id,
            query=explore_queries.get(signal, ""),
        )
    return urls


def _grafana_helm_release_root_url(
    spec: GrafanaReleaseSpec,
    *,
    extra_env: Mapping[str, str] | None,
) -> str:
    helm_release = _kubectl_json(
        ["-n", spec.namespace, "get", "helmrelease", spec.release_name, "-o", "json"],
        extra_env=extra_env,
    )
    values = _mapping(_mapping(helm_release.get("spec")).get("values"))
    grafana_ini = _mapping(values.get("grafana.ini"))
    server = _mapping(grafana_ini.get("server"))
    return str(server.get("root_url") or "").strip()


def _grafana_configmap_has_root_url(
    spec: GrafanaReleaseSpec,
    root_url: str,
    *,
    extra_env: Mapping[str, str] | None,
) -> bool:
    configmap = _kubectl_json(
        ["-n", spec.namespace, "get", "configmap", spec.release_name, "-o", "json"],
        extra_env=extra_env,
    )
    data = _mapping(configmap.get("data"))
    grafana_ini = str(data.get("grafana.ini") or "")
    return root_url in grafana_ini


def _patch_grafana_helm_release_root_url(
    spec: GrafanaReleaseSpec,
    root_url: str,
    *,
    extra_env: Mapping[str, str] | None,
) -> None:
    patch = {
        "spec": {
            "values": {
                "grafana.ini": {
                    "server": {
                        "root_url": root_url,
                    }
                }
            }
        }
    }
    _run_kubectl(
        [
            "-n",
            spec.namespace,
            "patch",
            "helmrelease",
            spec.release_name,
            "--type",
            "merge",
            "-p",
            json.dumps(patch, separators=(",", ":")),
        ],
        extra_env=extra_env,
    )
    _run_kubectl(
        [
            "-n",
            spec.namespace,
            "annotate",
            "helmrelease",
            spec.release_name,
            f"reconcile.fluxcd.io/requestedAt={datetime.now(UTC).isoformat()}",
            "--overwrite",
        ],
        extra_env=extra_env,
    )


def _wait_for_grafana_public_root_url(
    spec: GrafanaReleaseSpec,
    root_url: str,
    *,
    extra_env: Mapping[str, str] | None,
    timeout_seconds: float = 180.0,
    poll_interval_seconds: float = 5.0,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    last_error: RuntimeError | None = None
    while True:
        try:
            if _grafana_configmap_has_root_url(spec, root_url, extra_env=extra_env):
                break
        except RuntimeError as exc:
            last_error = exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail = f" Last probe error: {last_error}" if last_error is not None else ""
            raise RuntimeError(
                f"Timed out waiting for Grafana ConfigMap {spec.namespace}/{spec.release_name} "
                f"to contain root_url {root_url!r}.{detail}"
            )
        time.sleep(min(max(0.1, poll_interval_seconds), remaining))
    try:
        _run_kubectl(
            [
                "-n",
                spec.namespace,
                "rollout",
                "status",
                f"deployment/{spec.release_name}",
                "--timeout=180s",
            ],
            extra_env=extra_env,
            timeout=210,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"Grafana ConfigMap {spec.namespace}/{spec.release_name} contains root_url "
            f"{root_url!r}, but deployment rollout did not become ready: {exc}"
        ) from exc
    return True


def _ensure_grafana_public_root_url(
    spec: GrafanaReleaseSpec,
    base_url: str,
    *,
    extra_env: Mapping[str, str] | None,
) -> bool:
    root_url = base_url.rstrip("/") + "/"
    current_root_url = _grafana_helm_release_root_url(spec, extra_env=extra_env)
    if current_root_url != root_url:
        _patch_grafana_helm_release_root_url(spec, root_url, extra_env=extra_env)
    elif _grafana_configmap_has_root_url(spec, root_url, extra_env=extra_env):
        return True
    else:
        _patch_grafana_helm_release_root_url(spec, root_url, extra_env=extra_env)
    return _wait_for_grafana_public_root_url(spec, root_url, extra_env=extra_env)


def _public_goto_url(base_url: str, uid: str, *, org_id: int) -> str:
    return urljoin(base_url, f"goto/{quote(uid, safe='')}?orgId={org_id}")


def _create_grafana_short_url(
    base_url: str,
    url: str,
    *,
    org_id: int,
    username: str,
    password: str,
) -> str:
    payload = _post_grafana_short_url(
        base_url,
        _grafana_relative_path(url),
        username=username,
        password=password,
    )
    uid = str(payload.get("uid") or "").strip()
    if uid:
        return _public_goto_url(base_url, uid, org_id=org_id)
    returned_url = str(payload.get("url") or "").strip()
    returned_path = urlparse(returned_url).path.lstrip("/")
    if returned_path.startswith("goto/"):
        returned_query = urlparse(returned_url).query or f"orgId={org_id}"
        return urljoin(base_url, f"{returned_path}?{returned_query}")
    raise RuntimeError("Grafana short URL API response did not include a short URL uid")


def _shorten_grafana_urls(
    base_url: str,
    urls: Mapping[str, str],
    spec: GrafanaReleaseSpec,
    *,
    extra_env: Mapping[str, str] | None,
    org_id: int,
) -> dict[str, str]:
    try:
        credentials = _grafana_admin_credentials(spec, extra_env=extra_env)
    except RuntimeError:
        credentials = None
    if not credentials:
        return dict(urls)
    username, password = credentials
    shortened: dict[str, str] = {}
    for key, url in urls.items():
        try:
            shortened[key] = _create_grafana_short_url(
                base_url,
                url,
                org_id=org_id,
                username=username,
                password=password,
            )
        except RuntimeError:
            shortened[key] = url
    return shortened


def collect_grafana_runtime_status(
    payload_or_config: Any,
    *,
    extra_env: Mapping[str, str] | None,
    target_ref: str = "",
) -> tuple[dict[str, Any], ...]:
    statuses: list[dict[str, Any]] = []
    kube_context = _current_kube_context(extra_env)
    target_cluster_id = str((extra_env or {}).get(GRAFANA_TARGET_CLUSTER_ID_ENV) or "").strip()
    grafana_settings = _grafana_cli_settings()
    explore_queries = {item.signal: item.query for item in grafana_settings.explore_queries}
    for spec in grafana_release_specs(payload_or_config, target_ref=target_ref):
        base_url = _grafana_base_url(spec, extra_env=extra_env)
        status: dict[str, Any] = {
            "target_ref": spec.target_ref,
            "namespace": spec.namespace,
            "release_name": spec.release_name,
            "service_name": spec.service_name,
            "admin_secret_name": spec.admin_secret_name,
            "admin_user": spec.admin_user,
            "admin_user_key": spec.admin_user_key,
            "admin_password_key": spec.admin_password_key,
            "token_secret_name": spec.token_secret_name,
            "gateway_name": spec.gateway_name,
            "gateway_namespace": spec.gateway_namespace,
            "base_url": base_url,
        }
        if kube_context:
            status["kube_context"] = kube_context
        if target_cluster_id:
            status["cluster_id"] = target_cluster_id
        if base_url:
            dashboard_signal_bindings = _grafana_dashboard_signal_bindings()
            datasources_by_name = _grafana_datasources_by_name()
            explore_urls = _grafana_explore_urls(
                base_url,
                dashboard_signal_bindings,
                datasources_by_name,
                explore_queries=explore_queries,
                org_id=grafana_settings.org_id,
            )
            try:
                root_url_ready = _ensure_grafana_public_root_url(
                    spec,
                    base_url,
                    extra_env=extra_env,
                )
            except RuntimeError as exc:
                root_url_ready = False
                status["root_url_warning"] = str(exc)
            if root_url_ready:
                for signal, binding in dashboard_signal_bindings.items():
                    url_key = f"{signal}_url"
                    if url_key not in explore_urls:
                        continue
                    dashboard_url = _grafana_dashboard_url_for_spec(
                        base_url,
                        binding.dashboard,
                        binding.gnet_id,
                        spec,
                        dashboard_uid=binding.dashboard_uid,
                        extra_env=extra_env,
                    )
                    if dashboard_url:
                        dashboard_url = _dashboard_url_with_target_variables(
                            dashboard_url,
                            signal=signal,
                            extra_env=extra_env,
                        )
                        explore_urls[url_key] = dashboard_url
                        status[f"{signal}_url_kind"] = "dashboard"
                        if binding.gnet_id:
                            status[f"{signal}_url_gnet_id"] = binding.gnet_id
                        if binding.dashboard_uid:
                            status[f"{signal}_url_dashboard_uid"] = binding.dashboard_uid
                        status[f"{signal}_url_dashboard"] = f"{binding.folder}/{binding.dashboard}"
                    else:
                        status[f"{signal}_url_kind"] = "explore"
                status.update(
                    _shorten_grafana_urls(
                        base_url,
                        explore_urls,
                        spec,
                        extra_env=extra_env,
                        org_id=grafana_settings.org_id,
                    )
                )
            else:
                status.update(explore_urls)
            status["dashboards_url"] = urljoin(base_url, "dashboards")
        statuses.append(status)
    return tuple(statuses)


def _grafana_status_key(status: Mapping[str, Any]) -> tuple[str, str, str]:
    target_ref = str(status.get("target_ref") or "current-cluster").strip()
    namespace = str(status.get("namespace") or "observability").strip()
    release_name = str(
        status.get("release_name") or status.get("service_name") or "grafana"
    ).strip()
    return target_ref, namespace, release_name


def write_grafana_status(
    paths: Any,
    statuses: Sequence[Mapping[str, Any]],
    *,
    preserve_existing: bool = False,
) -> Path:
    inventory_dir = Path(paths.inventory_dir)
    inventory_dir.mkdir(parents=True, exist_ok=True)
    status_path = inventory_dir / GRAFANA_STATUS_FILENAME
    rows = [dict(item) for item in statuses]
    if preserve_existing:
        merged = {_grafana_status_key(item): dict(item) for item in read_grafana_status(paths)}
        for item in rows:
            merged[_grafana_status_key(item)] = dict(item)
        rows = list(merged.values())
    status_path.write_text(
        json.dumps({"grafana": rows}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return status_path


def read_grafana_status(paths: Any) -> tuple[dict[str, Any], ...]:
    status_path = Path(paths.inventory_dir) / GRAFANA_STATUS_FILENAME
    if not status_path.exists():
        return ()
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    rows = payload.get("grafana") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return ()
    return tuple(dict(item) for item in rows if isinstance(item, Mapping))


__all__ = [
    "GRAFANA_STATUS_FILENAME",
    "GRAFANA_TARGET_CLUSTER_ID_ENV",
    "GRAFANA_TARGET_KUBE_CONTEXT_ENV",
    "collect_grafana_runtime_status",
    "ensure_grafana_runtime_secrets",
    "grafana_enabled_for_target",
    "grafana_release_specs",
    "read_grafana_status",
    "write_grafana_status",
]
