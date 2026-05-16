"""Validate Grafana dashboard query contracts against live datasources."""

from __future__ import annotations

import json
import re
import time
from base64 import b64encode
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen

from .component_sources import (
    ComponentDefault,
    GrafanaDatasourceSpec,
    load_component_sources,
)
from .deploy_targets import app_chart_target_ref
from .grafana_runtime import (
    GRAFANA_TARGET_CLUSTER_ID_ENV,
    _active_grafana_rows,
    _grafana_admin_credentials,
    _grafana_base_url,
    _grafana_component_id,
    grafana_release_specs,
)
from .runtime_config import to_plain_data


@dataclass(frozen=True)
class DashboardFitResult:
    target_ref: str
    signal: str
    dashboard_ref: str
    dashboard_uid: str
    datasource: str
    datasource_uid: str
    datasource_type: str
    read_endpoint: str
    source: str = ""
    checks: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class _GrafanaDashboardContract:
    signal: str
    folder: str
    dashboard: str
    dashboard_uid: str
    gnet_id: int
    dashboard_spec: Mapping[str, Any]
    datasource: GrafanaDatasourceSpec

    @property
    def dashboard_ref(self) -> str:
        return f"{self.folder}/{self.dashboard}"


@dataclass(frozen=True)
class _PrometheusContract:
    labels_by_metric: dict[str, set[str]]
    queries: tuple[str, ...]


@dataclass(frozen=True)
class _LokiContract:
    labels: set[str]
    queries: tuple[str, ...]


@dataclass(frozen=True)
class _TempoContract:
    attributes: set[str]
    queries: tuple[str, ...]


_LABEL_VALUES_RE = re.compile(
    r"label_values\(\s*(?P<selector>(?:[A-Za-z_:][A-Za-z0-9_:]*)?(?:\{[^)]*\})|"
    r"[A-Za-z_:][A-Za-z0-9_:]*)\s*,\s*"
    r"(?P<label>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
)
_SELECTOR_RE = re.compile(r"(?P<metric>[A-Za-z_:][A-Za-z0-9_:]*)?\{(?P<labels>[^{}]*)\}")
_LABEL_MATCHER_RE = re.compile(
    r'(?:"(?P<quoted_label>(?:[^"\\]|\\.)+)"|(?P<label>[A-Za-z_][A-Za-z0-9_]*))'
    r"\s*(?:=~|!~|!=|=)"
)
_TRACE_ATTRIBUTE_RE = re.compile(
    r"\b(?:resource|span|event|link|instrumentation)\.(?:\"[^\"]+\"|[A-Za-z0-9_:.])+"
)
_GRAFANA_VARIABLE_RE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z][A-Za-z0-9_]*)(?::[^}]*)?\}|(?P<plain>[A-Za-z][A-Za-z0-9_]*))"
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _query_text(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("query", "expr"):
            text = _query_text(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, str):
        return value.strip()
    return ""


def _summarize_items(items: list[str], *, singular: str, plural: str, limit: int = 8) -> str:
    shown = ", ".join(items[:limit])
    label = singular if len(items) == 1 else plural
    if len(items) > limit:
        return f"{len(items)} {label}: {shown}, ... (+{len(items) - limit} more)"
    return f"{len(items)} {label}: {shown}"


def _dashboard_defaults(
    defaults: tuple[ComponentDefault, ...],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    dashboards: dict[tuple[str, str], Mapping[str, Any]] = {}
    for default in defaults:
        if default.kind != "literal" or not default.target_path.startswith("values.dashboards"):
            continue
        path_parts = default.target_path.split(".")
        if default.target_path == "values.dashboards" and isinstance(default.value, Mapping):
            for folder, folder_dashboards in default.value.items():
                if not isinstance(folder_dashboards, Mapping):
                    continue
                for dashboard, dashboard_spec in folder_dashboards.items():
                    if isinstance(dashboard_spec, Mapping):
                        dashboards[(str(folder), str(dashboard))] = dashboard_spec
        elif len(path_parts) == 3 and isinstance(default.value, Mapping):
            folder = path_parts[2]
            for dashboard, dashboard_spec in default.value.items():
                if isinstance(dashboard_spec, Mapping):
                    dashboards[(folder, str(dashboard))] = dashboard_spec
        elif len(path_parts) == 4 and isinstance(default.value, Mapping):
            dashboards[(path_parts[2], path_parts[3])] = default.value
    return dashboards


def _dashboard_json_uid(dashboard_json: str) -> str:
    try:
        payload = json.loads(dashboard_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get("uid") or "").strip()


def _dashboard_payload(dashboard_json: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(dashboard_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("dashboard JSON is invalid") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("dashboard JSON is not an object")
    return payload


def _datasource_matches(
    node: Mapping[str, Any],
    datasource_type: str,
    *,
    datasource_name: str = "",
    datasource_uid: str = "",
) -> bool:
    datasource = node.get("datasource")
    if isinstance(datasource, Mapping):
        node_type = str(datasource.get("type") or "").strip()
        node_uid = str(datasource.get("uid") or "").strip()
        return node_type == datasource_type or (bool(datasource_uid) and node_uid == datasource_uid)
    datasource_text = str(datasource or "").strip()
    return bool(datasource_text) and datasource_text in {datasource_name, datasource_uid}


def _dashboard_queries(
    dashboard: Mapping[str, Any],
    datasource_type: str,
    *,
    datasource_name: str = "",
    datasource_uid: str = "",
) -> tuple[str, ...]:
    queries: list[str] = []
    templating = _mapping(dashboard.get("templating"))
    variables = templating.get("list")
    if isinstance(variables, list):
        for variable in variables:
            if not isinstance(variable, Mapping):
                continue
            if not _datasource_matches(
                variable,
                datasource_type,
                datasource_name=datasource_name,
                datasource_uid=datasource_uid,
            ):
                continue
            for key in ("query", "definition"):
                query = _query_text(variable.get(key))
                if query and query not in queries:
                    queries.append(query)

    def walk_panels(panels: Any) -> None:
        if not isinstance(panels, list):
            return
        for panel in panels:
            if not isinstance(panel, Mapping):
                continue
            targets = panel.get("targets")
            if isinstance(targets, list):
                for target in targets:
                    if not isinstance(target, Mapping):
                        continue
                    if not (
                        _datasource_matches(
                            target,
                            datasource_type,
                            datasource_name=datasource_name,
                            datasource_uid=datasource_uid,
                        )
                        or _datasource_matches(
                            panel,
                            datasource_type,
                            datasource_name=datasource_name,
                            datasource_uid=datasource_uid,
                        )
                    ):
                        continue
                    for key in ("expr", "query"):
                        query = _query_text(target.get(key))
                        if query and query not in queries:
                            queries.append(query)
            walk_panels(panel.get("panels"))

    walk_panels(dashboard.get("panels"))
    return tuple(queries)


def _selector_labels(selector_body: str) -> set[str]:
    labels: set[str] = set()
    for match in _LABEL_MATCHER_RE.finditer(selector_body):
        quoted_label = match.group("quoted_label")
        if quoted_label is not None:
            try:
                labels.add(json.loads(f'"{quoted_label}"'))
            except json.JSONDecodeError:
                labels.add(quoted_label)
            continue
        label = match.group("label")
        if label:
            labels.add(label)
    return labels


def _prometheus_contract(
    dashboard: Mapping[str, Any],
    *,
    datasource_name: str = "",
    datasource_uid: str = "",
) -> _PrometheusContract:
    labels_by_metric: dict[str, set[str]] = {}
    queries = _dashboard_queries(
        dashboard,
        "prometheus",
        datasource_name=datasource_name,
        datasource_uid=datasource_uid,
    )
    for query in queries:
        for match in _LABEL_VALUES_RE.finditer(query):
            selector = match.group("selector")
            metric = selector.split("{", 1)[0].strip()
            label = match.group("label")
            if metric:
                labels_by_metric.setdefault(metric, set()).add(label)
            selector_match = _SELECTOR_RE.search(selector)
            if selector_match and metric:
                labels_by_metric.setdefault(metric, set()).update(
                    _selector_labels(selector_match.group("labels"))
                )
        for match in _SELECTOR_RE.finditer(query):
            metric = str(match.group("metric") or "").strip()
            if not metric:
                continue
            labels_by_metric.setdefault(metric, set()).update(
                _selector_labels(match.group("labels"))
            )
    return _PrometheusContract(labels_by_metric=labels_by_metric, queries=queries)


def _loki_contract(
    dashboard: Mapping[str, Any],
    *,
    datasource_name: str = "",
    datasource_uid: str = "",
) -> _LokiContract:
    labels: set[str] = set()
    queries = _dashboard_queries(
        dashboard,
        "loki",
        datasource_name=datasource_name,
        datasource_uid=datasource_uid,
    )
    for query in queries:
        for match in _LABEL_VALUES_RE.finditer(query):
            labels.add(match.group("label"))
            selector_match = _SELECTOR_RE.search(match.group("selector"))
            if selector_match:
                labels.update(_selector_labels(selector_match.group("labels")))
        for match in _SELECTOR_RE.finditer(query):
            labels.update(_selector_labels(match.group("labels")))
    return _LokiContract(labels=labels, queries=queries)


def _tempo_contract(
    dashboard: Mapping[str, Any],
    *,
    datasource_name: str = "",
    datasource_uid: str = "",
) -> _TempoContract:
    queries = _dashboard_queries(
        dashboard,
        "tempo",
        datasource_name=datasource_name,
        datasource_uid=datasource_uid,
    )
    attributes: set[str] = set()
    for query in queries:
        attributes.update(
            match.group(0).replace('"', "") for match in _TRACE_ATTRIBUTE_RE.finditer(query)
        )
    return _TempoContract(attributes=attributes, queries=queries)


def _grafana_get_json(
    base_url: str,
    path: str,
    *,
    username: str,
    password: str,
    params: Mapping[str, Any] | None = None,
) -> Any:
    query_string = ""
    if params:
        query_string = "?" + urlencode(params, doseq=True)
    credentials = b64encode(f"{username}:{password}".encode()).decode("ascii")
    request = Request(
        urljoin(base_url, path) + query_string,
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
    except TimeoutError as exc:
        raise RuntimeError("Grafana API request timed out") from exc
    except URLError as exc:
        raise RuntimeError(f"Grafana API request failed: {exc.reason}") from exc
    try:
        return json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Grafana API returned invalid JSON") from exc


def _proxy_get_json(
    base_url: str,
    datasource_uid: str,
    path: str,
    *,
    username: str,
    password: str,
    params: Mapping[str, Any] | None = None,
) -> Any:
    return _grafana_get_json(
        base_url,
        f"api/datasources/proxy/uid/{quote(datasource_uid, safe='')}/{path.lstrip('/')}",
        username=username,
        password=password,
        params=params,
    )


def _regex_literal(value: str) -> str:
    # Prometheus/Loki parse this inside a quoted regex string. Do not escape
    # hyphen outside character classes; PromQL rejects the resulting "\-".
    escaped = re.sub(r"([.^$*+?{}\[\]\\|()])", r"\\\1", str(value))
    return escaped.replace('"', '\\"')


def _replace_grafana_variables(
    query: str,
    replacements: Mapping[str, str],
    *,
    default: str,
    search_default: str | None = None,
) -> str:
    def replace(match: re.Match[str]) -> str:
        variable_name = str(match.group("braced") or match.group("plain") or "").strip()
        if variable_name in replacements:
            return replacements[variable_name]
        if search_default is not None and variable_name == "search":
            return search_default
        return default

    return _GRAFANA_VARIABLE_RE.sub(replace, query)


def _dashboard_variable_values(
    dashboard: Mapping[str, Any] | None = None,
    *,
    target_cluster_id: str = "",
) -> dict[str, str]:
    values: dict[str, str] = {}
    templating = _mapping(_mapping(dashboard).get("templating"))
    variables = templating.get("list")
    if isinstance(variables, list):
        for item in variables:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            current = _mapping(item.get("current"))
            value = current.get("value")
            if isinstance(value, list):
                text = "|".join(str(part).strip() for part in value if str(part).strip())
            else:
                text = str(value or "").strip()
            if text in {"$__all", "__all", "All"}:
                text = str(item.get("allValue") or "").strip()
            if not text:
                text = str(current.get("text") or "").strip()
            if text in {"$__all", "__all", "All"}:
                text = str(item.get("allValue") or "").strip()
            if text:
                values[name] = text
    if target_cluster_id:
        values["Cluster"] = _regex_literal(target_cluster_id)
    return values


def _prometheus_series_matcher(
    metric: str,
    labels: set[str],
    *,
    target_cluster_id: str = "",
) -> str:
    if target_cluster_id and "k8s.cluster.id" in labels:
        return f'{metric}{{"k8s.cluster.id"=~"{_regex_literal(target_cluster_id)}"}}'
    if target_cluster_id and "mk8s_cluster_id" in labels:
        return f'{metric}{{mk8s_cluster_id=~"{_regex_literal(target_cluster_id)}"}}'
    return metric


def _prometheus_query_for_validation(query: str, variable_values: Mapping[str, str]) -> str:
    query = query.replace("$__rate_interval", "5m")
    query = query.replace("${__rate_interval}", "5m")
    query = query.replace("$__interval", "5m")
    query = query.replace("${__interval}", "5m")
    query = query.replace("$__range", "5m")
    query = query.replace("${__range}", "5m")
    return _replace_grafana_variables(query, variable_values, default=".+")


def _loki_query_for_validation(query: str, variable_values: Mapping[str, str]) -> str:
    query = query.replace("$__interval", "5m")
    query = query.replace("${__interval}", "5m")
    query = query.replace("$__range", "5m")
    query = query.replace("${__range}", "5m")
    return _replace_grafana_variables(
        query,
        variable_values,
        default=".+",
        search_default=".*",
    )


def _tempo_query_for_validation(query: str) -> str:
    return _replace_grafana_variables(query, {}, default=".*")


def _validate_prometheus(
    *,
    base_url: str,
    datasource_uid: str,
    datasource_name: str,
    username: str,
    password: str,
    dashboard: Mapping[str, Any],
    now: int,
    start: int,
    missing_metric_is_error: bool = True,
    run_query_checks: bool = True,
    target_cluster_id: str = "",
) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []
    no_series_metrics: list[str] = []
    no_series_queries: list[str] = []
    contract = _prometheus_contract(
        dashboard,
        datasource_name=datasource_name,
        datasource_uid=datasource_uid,
    )
    variable_values = _dashboard_variable_values(
        dashboard,
        target_cluster_id=target_cluster_id,
    )
    for metric, labels in sorted(contract.labels_by_metric.items()):
        series_matcher = _prometheus_series_matcher(
            metric,
            labels,
            target_cluster_id=target_cluster_id,
        )
        try:
            series_payload = _proxy_get_json(
                base_url,
                datasource_uid,
                "/api/v1/series",
                username=username,
                password=password,
                params={
                    "match[]": [series_matcher],
                    "start": str(start),
                    "end": str(now),
                    "limit": "200",
                },
            )
        except RuntimeError as exc:
            errors.append(f"Prometheus metric {series_matcher}: {exc}")
            continue
        series = _mapping(series_payload).get("data")
        if not isinstance(series, list) or not series:
            message = f"Prometheus metric {metric} has no series in the datasource"
            if target_cluster_id and series_matcher != metric:
                message = (
                    f"Prometheus metric {metric} has no series for target cluster "
                    f"{target_cluster_id}"
                )
            if missing_metric_is_error:
                errors.append(message)
            else:
                no_series_metrics.append(metric)
            continue
        available_labels = {
            str(key)
            for item in series
            if isinstance(item, Mapping)
            for key in item
            if str(key) != "__name__"
        }
        missing_labels = sorted(label for label in labels if label not in available_labels)
        if missing_labels:
            errors.append(
                f"Prometheus metric {metric} is missing required label(s): "
                + ", ".join(missing_labels)
            )
    if run_query_checks:
        for query in contract.queries:
            if query.startswith(("label_values(", "query_result(")):
                continue
            validation_query = _prometheus_query_for_validation(query, variable_values)
            try:
                payload = _proxy_get_json(
                    base_url,
                    datasource_uid,
                    "/api/v1/query",
                    username=username,
                    password=password,
                    params={"query": validation_query},
                )
            except RuntimeError as exc:
                message = f"Prometheus query failed: {query}: {exc}"
                if missing_metric_is_error:
                    errors.append(message)
                else:
                    warnings.append(message)
                continue
            result = _mapping(_mapping(payload).get("data")).get("result")
            if isinstance(result, list) and not result:
                if missing_metric_is_error:
                    if target_cluster_id:
                        warnings.append(
                            f"Prometheus query returned no series for target cluster "
                            f"{target_cluster_id}: {query}"
                        )
                    else:
                        warnings.append(f"Prometheus query returned no series: {query}")
                else:
                    no_series_queries.append(query)
    elif contract.queries:
        if errors or no_series_metrics:
            checks.append("Metric/label names not fully matched")
        else:
            checks.append("Metric/label names matched")
    if no_series_metrics:
        warnings.append(
            "Prometheus datasource has no series for "
            + _summarize_items(no_series_metrics, singular="metric", plural="metrics")
        )
    if no_series_queries:
        warnings.append(
            "Prometheus returned no series for "
            + _summarize_items(no_series_queries, singular="query", plural="queries")
        )
    return errors, warnings, checks


def _dashboard_source_label(contract: _GrafanaDashboardContract) -> str:
    if str(contract.dashboard_spec.get("json") or "").strip():
        return "cxcli-owned JSON"
    if contract.gnet_id:
        return "Grafana.com import"
    return "unknown"


def _dashboard_import_warnings(
    *,
    base_url: str,
    username: str,
    password: str,
    dashboard_uid: str,
) -> list[str]:
    if not dashboard_uid:
        return ["Dashboard UID could not be resolved from the bound dashboard JSON"]
    try:
        _grafana_get_json(
            base_url,
            f"api/dashboards/uid/{quote(dashboard_uid, safe='')}",
            username=username,
            password=password,
        )
    except RuntimeError as exc:
        message = str(exc)
        if "HTTP 404" in message:
            return [
                f"Grafana dashboard UID {dashboard_uid} is not imported yet; "
                "run deploy or flux apply the generated bundle, then wait for Grafana to import the dashboard ConfigMap"
            ]
        return [f"Grafana dashboard UID {dashboard_uid} lookup failed: {message}"]
    return []


def _dashboard_import_payload(
    *,
    base_url: str,
    username: str,
    password: str,
    dashboard_uid: str,
) -> tuple[Mapping[str, Any] | None, list[str]]:
    if not dashboard_uid:
        return None, ["Dashboard UID could not be resolved from the bound dashboard source"]
    try:
        payload = _grafana_get_json(
            base_url,
            f"api/dashboards/uid/{quote(dashboard_uid, safe='')}",
            username=username,
            password=password,
        )
    except RuntimeError as exc:
        message = str(exc)
        if "HTTP 404" in message:
            return None, [
                f"Grafana dashboard UID {dashboard_uid} is not imported yet; "
                "run deploy or flux apply the generated bundle, then wait for Grafana to import the dashboard source"
            ]
        return None, [f"Grafana dashboard UID {dashboard_uid} lookup failed: {message}"]
    dashboard = _mapping(_mapping(payload).get("dashboard"))
    if not dashboard:
        return None, [f"Grafana dashboard UID {dashboard_uid} did not return dashboard JSON"]
    return dashboard, []


def _validate_loki(
    *,
    base_url: str,
    datasource_uid: str,
    datasource_name: str,
    username: str,
    password: str,
    dashboard: Mapping[str, Any],
    now: int,
    start: int,
    target_cluster_id: str = "",
    missing_label_is_error: bool = True,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    contract = _loki_contract(
        dashboard,
        datasource_name=datasource_name,
        datasource_uid=datasource_uid,
    )
    nanos_start = str(start * 1_000_000_000)
    nanos_end = str(now * 1_000_000_000)
    variable_values = _dashboard_variable_values(
        dashboard,
        target_cluster_id=target_cluster_id,
    )
    label_query = '{__bucket__="default"}'
    if target_cluster_id:
        label_query = (
            '{__bucket__="default", '
            f'k8s_cluster_id=~"{_regex_literal(target_cluster_id)}"' + "}"
        )
    labels_payload: Any = {}
    try:
        labels_payload = _proxy_get_json(
            base_url,
            datasource_uid,
            "/loki/api/v1/labels",
            username=username,
            password=password,
            params={
                "start": nanos_start,
                "end": nanos_end,
            },
        )
    except RuntimeError as exc:
        warnings.append(f"Loki root labels lookup failed: {exc}")
    available_labels = set()
    labels = _mapping(labels_payload).get("data")
    if isinstance(labels, list):
        available_labels.update(str(label) for label in labels)
    try:
        labels_payload = _proxy_get_json(
            base_url,
            datasource_uid,
            "/loki/api/v1/labels",
            username=username,
            password=password,
            params={
                "query": label_query,
                "start": nanos_start,
                "end": nanos_end,
            },
        )
    except RuntimeError as exc:
        errors.append(f"Loki labels lookup failed: {exc}")
        labels_payload = {}
    labels = _mapping(labels_payload).get("data")
    if isinstance(labels, list):
        available_labels.update(str(label) for label in labels)
    missing_labels = sorted(label for label in contract.labels if label not in available_labels)
    if missing_labels:
        message = "Loki datasource is missing required label(s): " + ", ".join(missing_labels)
        if missing_label_is_error:
            errors.append(message)
        else:
            warnings.append(message)
    for query in contract.queries:
        if query.startswith("label_values("):
            continue
        validation_query = _loki_query_for_validation(query, variable_values)
        try:
            payload = _proxy_get_json(
                base_url,
                datasource_uid,
                "/loki/api/v1/query_range",
                username=username,
                password=password,
                params={
                    "query": validation_query,
                    "start": nanos_start,
                    "end": nanos_end,
                    "limit": "5",
                },
            )
        except RuntimeError as exc:
            message = f"Loki query failed: {query}: {exc}"
            if missing_label_is_error:
                errors.append(message)
            else:
                warnings.append(message)
            continue
        result = _mapping(_mapping(payload).get("data")).get("result")
        if isinstance(result, list) and not result:
            if target_cluster_id:
                warnings.append(
                    f"Loki query returned no streams for target cluster {target_cluster_id}: {query}"
                )
            else:
                warnings.append(f"Loki query returned no streams: {query}")
    return errors, warnings


def _tempo_tags(tags_payload: Any) -> set[str]:
    tags: set[str] = set()
    scopes = _mapping(tags_payload).get("scopes")
    if not isinstance(scopes, list):
        return tags
    for scope in scopes:
        if not isinstance(scope, Mapping):
            continue
        scope_name = str(scope.get("name") or "").strip()
        scope_tags = scope.get("tags")
        if isinstance(scope_tags, list):
            tags.update(
                f"{scope_name}.{tag}" if scope_name and scope_name != "intrinsic" else str(tag)
                for tag in scope_tags
            )
    return tags


def _validate_tempo(
    *,
    base_url: str,
    datasource_uid: str,
    datasource_name: str,
    username: str,
    password: str,
    dashboard: Mapping[str, Any],
    now: int,
    start: int,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    contract = _tempo_contract(
        dashboard,
        datasource_name=datasource_name,
        datasource_uid=datasource_uid,
    )
    try:
        tags_payload = _proxy_get_json(
            base_url,
            datasource_uid,
            "/api/v2/search/tags",
            username=username,
            password=password,
            params={"start": str(start), "end": str(now)},
        )
    except RuntimeError as exc:
        errors.append(f"Tempo tag discovery failed: {exc}")
        tags_payload = {}
    available_tags = _tempo_tags(tags_payload)
    missing_attributes = sorted(
        attribute for attribute in contract.attributes if attribute not in available_tags
    )
    if missing_attributes and available_tags:
        errors.append(
            "Tempo datasource is missing required attribute(s): " + ", ".join(missing_attributes)
        )
    elif missing_attributes:
        warnings.append(
            "Tempo did not return discoverable tags for required attribute(s): "
            + ", ".join(missing_attributes)
        )
    for query in contract.queries:
        validation_query = _tempo_query_for_validation(query)
        try:
            payload = _proxy_get_json(
                base_url,
                datasource_uid,
                "/api/search",
                username=username,
                password=password,
                params={
                    "q": validation_query,
                    "limit": "5",
                    "start": str(start),
                    "end": str(now),
                },
            )
        except RuntimeError as exc:
            errors.append(f"Tempo TraceQL query failed: {query}: {exc}")
            continue
        traces = _mapping(payload).get("traces")
        if not traces:
            warnings.append(f"Tempo query returned no traces: {query}")
    return errors, warnings


def _grafana_chart_contracts() -> tuple[_GrafanaDashboardContract, ...]:
    sources = load_component_sources()
    grafana_component_id = _grafana_component_id()
    if not grafana_component_id:
        return ()
    for chart in sources.helm_charts:
        if chart.name != grafana_component_id:
            continue
        datasources_by_name = {
            datasource.name: datasource for datasource in chart.grafana.datasources
        }
        signal_by_dashboard = {
            (binding.folder, binding.dashboard): binding.signal
            for binding in chart.grafana.dashboard_signals
        }
        contracts: list[_GrafanaDashboardContract] = []
        for (folder, dashboard), dashboard_spec in _dashboard_defaults(chart.defaults).items():
            datasource_name = str(dashboard_spec.get("datasource") or "").strip()
            datasource = datasources_by_name.get(datasource_name)
            if datasource is None:
                continue
            dashboard_json = str(dashboard_spec.get("json") or "").strip()
            dashboard_uid = (
                _dashboard_json_uid(dashboard_json) or str(dashboard_spec.get("uid") or "").strip()
            )
            raw_gnet_id = dashboard_spec.get("gnetId")
            gnet_id = raw_gnet_id if isinstance(raw_gnet_id, int) else 0
            contracts.append(
                _GrafanaDashboardContract(
                    signal=signal_by_dashboard.get((folder, dashboard), "dashboard"),
                    folder=folder,
                    dashboard=dashboard,
                    dashboard_uid=dashboard_uid,
                    gnet_id=gnet_id,
                    dashboard_spec=dashboard_spec,
                    datasource=datasource,
                )
            )
        return tuple(contracts)
    return ()


def _target_refs(payload_or_config: Any, explicit_target_ref: str) -> tuple[str, ...]:
    if explicit_target_ref:
        return (explicit_target_ref.strip().lower(),)
    refs: list[str] = []
    for row in _active_grafana_rows(payload_or_config):
        ref = app_chart_target_ref(row)
        if ref not in refs:
            refs.append(ref)
    return tuple(refs or [""])


def validate_grafana_dashboard_fits(
    payload_or_config: Any,
    *,
    target_ref: str = "",
    extra_env: Mapping[str, str] | None = None,
    target_extra_envs: Mapping[str, Mapping[str, str]] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> tuple[DashboardFitResult, ...]:
    payload = to_plain_data(payload_or_config)
    results: list[DashboardFitResult] = []
    contracts = _grafana_chart_contracts()
    if not contracts:
        return ()
    now = int(time.time())
    start = now - 6 * 3600
    seen_specs: set[tuple[str, str, str]] = set()
    release_specs = []
    for ref in _target_refs(payload, target_ref):
        for release_spec in grafana_release_specs(payload, target_ref=ref):
            spec_key = (
                release_spec.target_ref,
                release_spec.namespace,
                release_spec.release_name,
            )
            if spec_key in seen_specs:
                continue
            seen_specs.add(spec_key)
            release_specs.append(release_spec)
    total_checks = len(release_specs) * len(contracts)
    completed_checks = 0

    def _progress_label(*, target: str, dashboard_ref: str) -> str:
        target_label = target or "default"
        return f"{target_label}: {dashboard_ref}"

    def _progress_start(label: str) -> None:
        if progress_callback is not None:
            progress_callback(label, completed_checks, total_checks)

    def _append_result(label: str, result: DashboardFitResult) -> None:
        nonlocal completed_checks
        results.append(result)
        completed_checks += 1
        if progress_callback is not None:
            progress_callback(label, completed_checks, total_checks)

    def _extra_env_for_target(target: str) -> Mapping[str, str] | None:
        normalized = str(target or "").strip().lower()
        if target_extra_envs and normalized in target_extra_envs:
            return target_extra_envs[normalized]
        return extra_env

    if progress_callback is not None:
        progress_callback("init", 0, total_checks)
    for spec in release_specs:
        spec_extra_env = _extra_env_for_target(spec.target_ref)
        target_cluster_id = str(
            (spec_extra_env or {}).get(GRAFANA_TARGET_CLUSTER_ID_ENV) or ""
        ).strip()
        base_url = _grafana_base_url(spec, extra_env=spec_extra_env)
        credentials = _grafana_admin_credentials(spec, extra_env=spec_extra_env)
        if not base_url or not credentials:
            for contract in contracts:
                datasource = contract.datasource
                dashboard_ref = contract.dashboard_ref
                progress_label = _progress_label(
                    target=spec.target_ref,
                    dashboard_ref=dashboard_ref,
                )
                _progress_start(progress_label)
                _append_result(
                    progress_label,
                    DashboardFitResult(
                        target_ref=spec.target_ref,
                        signal=contract.signal,
                        dashboard_ref=dashboard_ref,
                        dashboard_uid=contract.dashboard_uid,
                        datasource=datasource.name,
                        datasource_uid=datasource.uid,
                        datasource_type=datasource.datasource_type,
                        read_endpoint=datasource.read_endpoint,
                        source=_dashboard_source_label(contract),
                        errors=("Grafana URL or admin credentials could not be resolved",),
                    ),
                )
            continue
        username, password = credentials
        try:
            grafana_datasources = _grafana_get_json(
                base_url,
                "api/datasources",
                username=username,
                password=password,
            )
        except RuntimeError as exc:
            for contract in contracts:
                datasource = contract.datasource
                dashboard_ref = contract.dashboard_ref
                progress_label = _progress_label(
                    target=spec.target_ref,
                    dashboard_ref=dashboard_ref,
                )
                _progress_start(progress_label)
                _append_result(
                    progress_label,
                    DashboardFitResult(
                        target_ref=spec.target_ref,
                        signal=contract.signal,
                        dashboard_ref=dashboard_ref,
                        dashboard_uid=contract.dashboard_uid,
                        datasource=datasource.name,
                        datasource_uid=datasource.uid,
                        datasource_type=datasource.datasource_type,
                        read_endpoint=datasource.read_endpoint,
                        source=_dashboard_source_label(contract),
                        errors=(str(exc),),
                    ),
                )
            continue
        live_by_uid = (
            {
                str(item.get("uid") or ""): item
                for item in grafana_datasources
                if isinstance(item, Mapping)
            }
            if isinstance(grafana_datasources, list)
            else {}
        )
        for contract in contracts:
            datasource = contract.datasource
            dashboard_ref = contract.dashboard_ref
            progress_label = _progress_label(
                target=spec.target_ref,
                dashboard_ref=dashboard_ref,
            )
            _progress_start(progress_label)
            dashboard_json = str(contract.dashboard_spec.get("json") or "").strip()
            dashboard_uid = contract.dashboard_uid or _dashboard_json_uid(dashboard_json)
            dashboard_source = _dashboard_source_label(contract)
            errors: list[str] = []
            warnings: list[str] = []
            checks: list[str] = []
            if target_cluster_id and contract.signal in {"metrics", "logs"}:
                checks.append(f"Target cluster ID: {target_cluster_id}")
            elif spec.target_ref and contract.signal in {"metrics", "logs"}:
                warnings.append(
                    "Target cluster ID was not resolved; dashboard variables were validated with "
                    "wildcard values"
                )
            live_datasource = _mapping(live_by_uid.get(datasource.uid))
            if not live_datasource:
                errors.append(
                    f"Grafana datasource {datasource.name} with UID {datasource.uid} is missing"
                )
            elif str(live_datasource.get("type") or "").strip() != datasource.datasource_type:
                errors.append(
                    f"Grafana datasource {datasource.name} has type "
                    f"{live_datasource.get('type')}, expected {datasource.datasource_type}"
                )
            dashboard_payload: Mapping[str, Any] | None = None
            if dashboard_json:
                try:
                    dashboard_payload = _dashboard_payload(dashboard_json)
                except RuntimeError as exc:
                    errors.append(str(exc))
                imported_payload, import_warnings = _dashboard_import_payload(
                    base_url=base_url,
                    username=username,
                    password=password,
                    dashboard_uid=dashboard_uid,
                )
                warnings.extend(import_warnings)
                if imported_payload and _dashboard_json_uid(dashboard_json) != dashboard_uid:
                    warnings.append(
                        f"Catalog dashboard JSON UID differs from expected UID {dashboard_uid}"
                    )
            elif dashboard_uid:
                imported_payload, import_warnings = _dashboard_import_payload(
                    base_url=base_url,
                    username=username,
                    password=password,
                    dashboard_uid=dashboard_uid,
                )
                dashboard_payload = imported_payload
                warnings.extend(import_warnings)
            else:
                warnings.append(
                    "Dashboard query JSON is not local to the catalog and no imported "
                    "dashboard UID is declared; live fit was not checked"
                )
            if dashboard_payload is not None and not errors:
                if datasource.datasource_type == "prometheus":
                    next_errors, next_warnings, next_checks = _validate_prometheus(
                        base_url=base_url,
                        datasource_uid=datasource.uid,
                        datasource_name=datasource.name,
                        username=username,
                        password=password,
                        dashboard=dashboard_payload,
                        now=now,
                        start=start,
                        missing_metric_is_error=contract.signal != "dashboard",
                        run_query_checks=contract.signal != "dashboard",
                        target_cluster_id=target_cluster_id,
                    )
                elif datasource.datasource_type == "loki":
                    next_errors, next_warnings = _validate_loki(
                        base_url=base_url,
                        datasource_uid=datasource.uid,
                        datasource_name=datasource.name,
                        username=username,
                        password=password,
                        dashboard=dashboard_payload,
                        now=now,
                        start=start,
                        target_cluster_id=target_cluster_id,
                        missing_label_is_error=contract.signal != "dashboard",
                    )
                    next_checks = []
                elif datasource.datasource_type == "tempo":
                    next_errors, next_warnings = _validate_tempo(
                        base_url=base_url,
                        datasource_uid=datasource.uid,
                        datasource_name=datasource.name,
                        username=username,
                        password=password,
                        dashboard=dashboard_payload,
                        now=now,
                        start=start,
                    )
                    next_checks = []
                else:
                    next_errors = [f"Unsupported datasource type {datasource.datasource_type}"]
                    next_warnings = []
                    next_checks = []
                errors.extend(next_errors)
                warnings.extend(next_warnings)
                checks.extend(next_checks)
            _append_result(
                progress_label,
                DashboardFitResult(
                    target_ref=spec.target_ref,
                    signal=contract.signal,
                    dashboard_ref=dashboard_ref,
                    dashboard_uid=dashboard_uid,
                    datasource=datasource.name,
                    datasource_uid=datasource.uid,
                    datasource_type=datasource.datasource_type,
                    read_endpoint=datasource.read_endpoint,
                    source=dashboard_source,
                    checks=tuple(checks),
                    errors=tuple(errors),
                    warnings=tuple(warnings),
                ),
            )
    if progress_callback is not None:
        progress_callback("done", completed_checks, total_checks)
    return tuple(results)
