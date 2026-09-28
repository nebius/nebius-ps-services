"""Materialize catalog-owned storage and non-native collector Helm values."""

from __future__ import annotations

import copy
from typing import Any
from urllib.parse import urlsplit

import yaml

from .observability_routing import (
    SIGNALS,
    STORE_APPS,
    app_row,
    collector_owners,
    helm_fullname,
    local_url,
    routed_targets,
    target_settings,
)

WRITE_SECRET = {"name": "cxcli-observability-write", "key": "token"}


def write_secret(remote: dict[str, Any]) -> dict[str, str] | None:
    return remote.get("auth_secret") or (WRITE_SECRET if remote.get("auth") == "nebius" else None)


def metrics_destinations(payload: dict[str, Any], target: str) -> list[dict[str, Any]]:
    signal = target_settings(payload, target)["metrics"]
    destinations = []
    if signal["storage"] != "remote":
        destinations.append({"url": local_url(payload, target, "metrics") + "/api/v1/write"})
    if signal["storage"] != "local":
        remote = signal["remote"]
        destination = {"url": remote["url"]}
        secret = write_secret(remote)
        if secret:
            destination["bearerTokenSecret"] = secret
        destinations.append(destination)
    return destinations


def pushgateway_address(payload: dict[str, Any], target: str) -> str:
    row = app_row(payload, target, "prometheus-pushgateway")
    if row is None:
        raise ValueError("Pushgateway publication requires the owned Pushgateway release")
    values = row.get("values", {})
    release = row.get("release-name", "prometheus-pushgateway")
    name = values.get("fullnameOverride") or helm_fullname(
        release, values.get("nameOverride") or "prometheus-pushgateway"
    )
    namespace = values.get("namespaceOverride") or row.get("namespace", "observability")
    port = values.get("service", {}).get("port", 9091)
    return f"{name[:63].rstrip('-')}.{namespace}.svc:{port}"


def pushgateway_scrape(payload: dict[str, Any], target: str) -> dict[str, Any]:
    return {
        "job_name": "cxcli-pushgateway",
        "scrape_interval": "5s",
        "honor_labels": True,
        "static_configs": [{"targets": [pushgateway_address(payload, target)]}],
    }


def _otel_gateway(payload: dict[str, Any], target: str, owners: dict[str, str]) -> dict[str, Any]:
    routing = target_settings(payload, target)
    exporters: dict[str, Any] = {"debug": None}
    pipelines: dict[str, Any] = {signal: None for signal in SIGNALS}
    environment = []
    for signal in ("logs", "traces"):
        if owners.get(signal) != "opentelemetry-collector":
            continue
        route = routing[signal]
        names = []
        if route["storage"] != "remote":
            name = f"otlp_http/{signal}_local"
            base = local_url(payload, target, signal).removesuffix("/select/jaeger")
            path = (
                "/insert/opentelemetry/v1/logs"
                if signal == "logs"
                else "/insert/opentelemetry/v1/traces"
            )
            exporters[name] = {f"{signal}_endpoint": base + path, "compression": "gzip"}
            names.append(name)
        if route["storage"] != "local":
            remote = route["remote"]
            grpc = remote["protocol"] == "otlp-grpc"
            name = ("otlp_grpc/" if grpc else "otlp_http/") + signal + "_remote"
            parsed = urlsplit(remote["url"])
            config: dict[str, Any] = (
                {"endpoint": parsed.netloc} if grpc else {f"{signal}_endpoint": remote["url"]}
            )
            if grpc:
                config["tls"] = {"insecure": parsed.scheme == "http"}
            secret = write_secret(remote)
            if secret:
                env = "CXCLI_" + signal.upper() + "_WRITE_TOKEN"
                environment.append({"name": env, "valueFrom": {"secretKeyRef": secret}})
                config["headers"] = {"Authorization": "Bearer ${env:" + env + "}"}
            if remote.get("auth") == "nebius":
                config.setdefault("headers", {})["iam-container"] = payload["client_info"][
                    "nebius"
                ]["project_id"]
            exporters[name] = config
            names.append(name)
        pipelines[signal] = {
            "receivers": ["otlp"],
            "processors": ["memory_limiter", "batch"],
            "exporters": names,
        }
    return {
        "mode": "deployment",
        "extraEnvs": environment,
        "config": {
            "exporters": exporters,
            "receivers": {"prometheus": None, "jaeger": None, "zipkin": None},
            "service": {"pipelines": pipelines},
        },
        "ports": {
            "jaeger-compact": {"enabled": False},
            "jaeger-thrift": {"enabled": False},
            "jaeger-grpc": {"enabled": False},
            "zipkin": {"enabled": False},
        },
    }


def _otel_logs(payload: dict[str, Any], target: str) -> dict[str, Any]:
    gateway = app_row(payload, target, "opentelemetry-collector")
    if not gateway:
        raise ValueError("Node logging requires the owned OTLP gateway")
    endpoint = f"{gateway.get('release-name', 'opentelemetry-collector')}.{gateway.get('namespace', 'observability')}.svc:4317"
    return {
        "mode": "daemonset",
        "presets": {
            "logsCollection": {"enabled": True, "includeCollectorLogs": False},
            "kubernetesAttributes": {"enabled": True},
        },
        "config": {
            "exporters": {
                "debug": None,
                "otlp_grpc/gateway": {"endpoint": endpoint, "tls": {"insecure": True}},
            },
            "receivers": {"otlp": None, "prometheus": None, "jaeger": None, "zipkin": None},
            "service": {
                "pipelines": {
                    "metrics": None,
                    "traces": None,
                    "logs": {
                        "receivers": ["filelog"],
                        "processors": ["memory_limiter", "k8sattributes", "batch"],
                        "exporters": ["otlp_grpc/gateway"],
                    },
                }
            },
        },
        "ports": {
            key: {"enabled": False}
            for key in (
                "otlp",
                "otlp-http",
                "jaeger-compact",
                "jaeger-thrift",
                "jaeger-grpc",
                "zipkin",
            )
        },
    }


def _merge(base: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def materialize_backend_values(payload: dict[str, Any]) -> bool:
    before = copy.deepcopy(payload)
    for target in routed_targets(payload):
        routing = target_settings(payload, target)
        owners = collector_owners(payload, target)
        for signal in ("logs", "traces"):
            store = app_row(payload, target, STORE_APPS[signal])
            if store:
                server = store.setdefault("values", {}).setdefault("server", {})
                if not server.get("fullnameOverride"):
                    server["fullnameOverride"] = store["release-name"] + "-server"
        metrics = app_row(payload, target, STORE_APPS["metrics"])
        if metrics:
            values = metrics.setdefault("values", {})
            # The chart adds its own local remoteWrite destination when vmsingle is
            # enabled. Override the complete list via spec to avoid duplicate writes.
            _merge(
                values,
                {
                    "vmsingle": {"enabled": "metrics" in routing.get("local_stores", [])},
                    "vmagent": {
                        "enabled": owners.get("metrics") == STORE_APPS["metrics"],
                    },
                },
            )
            from .vmagent_routing import replace_destinations

            values["vmagent"]["additionalRemoteWrites"] = []
            replace_destinations(values["vmagent"]["spec"], metrics_destinations(payload, target))
            jobs = yaml.safe_load(values["vmagent"]["spec"].get("inlineScrapeConfig") or "[]")
            from .observability import _additional_target_config, _selected_app_metric_targets

            targets = _selected_app_metric_targets(payload, target_ref=target)
            owned_jobs = {target.job_name for _, target in targets} | {"cxcli-pushgateway"}
            jobs = [job for job in jobs if job.get("job_name") not in owned_jobs]
            if owners.get("metrics") == STORE_APPS["metrics"]:
                jobs.extend(
                    _additional_target_config(namespace=row["namespace"], target=scrape)
                    for row, scrape in targets
                )
            if routing.get("pushgateway") and owners.get("metrics") == STORE_APPS["metrics"]:
                jobs.append(pushgateway_scrape(payload, target))
            values["vmagent"]["spec"]["inlineScrapeConfig"] = yaml.safe_dump(jobs, sort_keys=False)
        gateway = app_row(payload, target, "opentelemetry-collector")
        if gateway:
            gateway.setdefault("values", {})["fullnameOverride"] = gateway["release-name"]
            generated = _otel_gateway(payload, target, owners)
            gateway["values"].setdefault("config", {})["exporters"] = generated["config"][
                "exporters"
            ]
            gateway["values"]["config"].setdefault("service", {})["pipelines"] = generated[
                "config"
            ]["service"]["pipelines"]
            _merge(gateway["values"], generated)
        logs = app_row(payload, target, "opentelemetry-logs")
        if logs:
            logs.setdefault("values", {})["fullnameOverride"] = logs["release-name"]
            generated = _otel_logs(payload, target)
            logs["values"].setdefault("config", {})["exporters"] = generated["config"]["exporters"]
            logs["values"]["config"].setdefault("service", {})["pipelines"] = generated["config"][
                "service"
            ]["pipelines"]
            _merge(logs["values"], generated)
        agent = app_row(payload, target, "nebius-observability-agent")
        if agent:
            from .observability import (
                _catalog_metric_target_job_names,
                _collector_additional_targets,
                _collector_managed_values,
                _merge_managed_additional_targets,
                _set_path_value,
            )

            for path, value in _collector_managed_values(payload, target_ref=target).items():
                _set_path_value(agent, path, copy.deepcopy(value))
            _merge_managed_additional_targets(
                chart_row=agent,
                managed_targets=_collector_additional_targets(payload, target_ref=target)
                if owners.get("metrics") == "nebius-observability-agent"
                else (),
                managed_job_names=_catalog_metric_target_job_names(),
            )
            config = agent.setdefault("values", {}).setdefault("config", {})
            for signal in SIGNALS:
                config.setdefault(signal, {})["enabled"] = (
                    owners.get(signal) == "nebius-observability-agent"
                )
            config["metrics"]["additionalTargets"] = [
                job
                for job in config["metrics"].get("additionalTargets", [])
                if job.get("job_name") != "cxcli-pushgateway"
            ]
            if routing.get("pushgateway") and owners.get("metrics") == "nebius-observability-agent":
                config["metrics"]["additionalTargets"] = [
                    job
                    for job in config["metrics"].get("additionalTargets", [])
                    if job.get("job_name") != "cxcli-pushgateway"
                ] + [pushgateway_scrape(payload, target)]
    return payload != before
