"""Deploy-time Grafana secret bootstrap and status reporting."""

from __future__ import annotations

import json
import os
import re
import secrets
import string
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import yaml

from .component_instances import component_type_id
from .deploy_targets import app_chart_target_ref
from .iam_bootstrap import issue_observability_static_key
from .runtime_config import to_plain_data

GRAFANA_APP_ID = "grafana"
GRAFANA_STATUS_FILENAME = "grafana-status.json"
GRAFANA_STATIC_TOKEN_ENV = "NEBIUS_OBSERVABILITY_STATIC_TOKEN"
DEFAULT_ADMIN_SECRET_NAME = "nebius-cxcli-grafana-admin"
DEFAULT_TOKEN_SECRET_NAME = "nebius-cxcli-grafana-observability-read"
DEFAULT_ADMIN_USER_KEY = "admin-user"
DEFAULT_ADMIN_PASSWORD_KEY = "admin-password"
DEFAULT_TOKEN_KEY = "token"


@dataclass(frozen=True)
class GrafanaReleaseSpec:
    target_ref: str
    namespace: str
    release_name: str
    service_name: str
    admin_secret_name: str
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


def _active_grafana_rows(payload_or_config: Any) -> tuple[dict[str, Any], ...]:
    payload = _as_payload(payload_or_config)
    rows = _mapping(payload.get("apps")).get("charts")
    if not isinstance(rows, list):
        return ()
    return tuple(
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and bool(row.get("enabled", False))
        and component_type_id(row) == GRAFANA_APP_ID
    )


def grafana_release_specs(
    payload_or_config: Any,
    *,
    target_ref: str = "",
) -> tuple[GrafanaReleaseSpec, ...]:
    normalized_target_ref = str(target_ref or "").strip().lower()
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
        token_env = _mapping(env_value_from.get(GRAFANA_STATIC_TOKEN_ENV))
        token_secret_ref = _mapping(token_env.get("secretKeyRef"))
        route_main = _mapping(_mapping(values.get("route")).get("main"))
        parent_refs = route_main.get("parentRefs")
        gateway_name = ""
        gateway_namespace = ""
        if bool(route_main.get("enabled")) and isinstance(parent_refs, list) and parent_refs:
            parent_ref = _mapping(parent_refs[0])
            gateway_name = str(parent_ref.get("name") or "").strip()
            gateway_namespace = str(parent_ref.get("namespace") or "").strip()
        release_name = str(row.get("release-name") or GRAFANA_APP_ID).strip() or GRAFANA_APP_ID
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
                    admin.get("existingSecret") or DEFAULT_ADMIN_SECRET_NAME
                ).strip()
                or DEFAULT_ADMIN_SECRET_NAME,
                admin_user_key=str(admin.get("userKey") or DEFAULT_ADMIN_USER_KEY).strip()
                or DEFAULT_ADMIN_USER_KEY,
                admin_password_key=str(
                    admin.get("passwordKey") or DEFAULT_ADMIN_PASSWORD_KEY
                ).strip()
                or DEFAULT_ADMIN_PASSWORD_KEY,
                token_secret_name=str(
                    token_secret_ref.get("name") or DEFAULT_TOKEN_SECRET_NAME
                ).strip()
                or DEFAULT_TOKEN_SECRET_NAME,
                token_key=str(token_secret_ref.get("key") or DEFAULT_TOKEN_KEY).strip()
                or DEFAULT_TOKEN_KEY,
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
    completed = subprocess.run(
        ["kubectl", *args],
        env=_kubectl_env(extra_env),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = _first_non_empty_line(completed.stderr or completed.stdout or "")
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {detail or completed.returncode}")
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
        raise RuntimeError(f"kubectl {' '.join(args)} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"kubectl {' '.join(args)} did not return a JSON object")
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
    completed = subprocess.run(
        ["kubectl", "-n", namespace, "get", "secret", name, "-o", "json"],
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
            f"kubectl -n {namespace} get secret {name} failed: {message or completed.returncode}"
        )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"kubectl -n {namespace} get secret {name} returned invalid JSON") from exc
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return False
    return all(str(key) in data for key in keys)


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
                    spec.admin_user_key: "admin",
                    spec.admin_password_key: _generate_password(),
                },
                extra_env=extra_env,
            )
            if callable(emit):
                emit(f"Created Grafana admin credential secret `{spec.admin_secret_name}`.")
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


def _explore_url(base_url: str, *, datasource_uid: str, query: str = "") -> str:
    left: dict[str, Any] = {
        "datasource": datasource_uid,
        "queries": [{"refId": "A"}],
        "range": {"from": "now-1h", "to": "now"},
    }
    if query:
        left["queries"][0]["expr"] = query
    encoded = quote(json.dumps(left, separators=(",", ":")), safe="")
    return urljoin(base_url, f"explore?orgId=1&left={encoded}")


def collect_grafana_runtime_status(
    payload_or_config: Any,
    *,
    extra_env: Mapping[str, str] | None,
    target_ref: str = "",
) -> tuple[dict[str, Any], ...]:
    statuses: list[dict[str, Any]] = []
    for spec in grafana_release_specs(payload_or_config, target_ref=target_ref):
        base_url = _grafana_base_url(spec, extra_env=extra_env)
        status: dict[str, Any] = {
            "target_ref": spec.target_ref,
            "namespace": spec.namespace,
            "release_name": spec.release_name,
            "service_name": spec.service_name,
            "admin_secret_name": spec.admin_secret_name,
            "admin_user_key": spec.admin_user_key,
            "admin_password_key": spec.admin_password_key,
            "token_secret_name": spec.token_secret_name,
            "gateway_name": spec.gateway_name,
            "gateway_namespace": spec.gateway_namespace,
            "base_url": base_url,
        }
        if base_url:
            status["metrics_url"] = _explore_url(
                base_url,
                datasource_uid="nebius-service-metrics",
                query='count({__name__=~".+"})',
            )
            status["logs_url"] = _explore_url(
                base_url,
                datasource_uid="nebius-logs",
                query='{__bucket__="default"}',
            )
            status["traces_url"] = _explore_url(base_url, datasource_uid="nebius-traces")
            status["dashboards_url"] = urljoin(base_url, "dashboards")
        statuses.append(status)
    return tuple(statuses)


def write_grafana_status(paths: Any, statuses: Sequence[Mapping[str, Any]]) -> Path:
    inventory_dir = Path(paths.inventory_dir)
    inventory_dir.mkdir(parents=True, exist_ok=True)
    status_path = inventory_dir / GRAFANA_STATUS_FILENAME
    status_path.write_text(
        json.dumps({"grafana": [dict(item) for item in statuses]}, indent=2, sort_keys=True)
        + "\n",
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
    "collect_grafana_runtime_status",
    "ensure_grafana_runtime_secrets",
    "grafana_enabled_for_target",
    "grafana_release_specs",
    "read_grafana_status",
    "write_grafana_status",
]
