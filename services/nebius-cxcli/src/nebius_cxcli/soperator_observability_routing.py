"""Explicit telemetry child transformations, applied to graph and Flux alike."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

import yaml

from .observability_backends import _otel_gateway, metrics_destinations, pushgateway_scrape
from .observability_routing import app_row, target_settings

ROUTING_KEY = "cxcliObservabilityRouting"


def bind_routing(payload: Mapping[str, Any], target: str, values: dict[str, Any]) -> None:
    routing = target_settings(payload, target)
    if not routing:
        return
    if routing["metrics"]["storage"] == "remote" and values.get("notifier", {}).get("enabled"):
        raise ValueError(
            "Remote-only native metrics requires explicit notifier cutover: this frozen notifier depends on local VMSingle"
        )
    desired = {
        "metrics": metrics_destinations(dict(payload), target),
        "logs": _otel_gateway(
            dict(payload), target, {"logs": "opentelemetry-collector", "traces": "soperator"}
        ),
        "metrics_storage": routing["metrics"]["storage"],
        "logs_storage": routing["logs"]["storage"],
    }
    from .soperator_values import EXPLICIT_VALUES_FIELD, explicit_values

    row = app_row(payload, target, "soperator") or {}
    authored = explicit_values(row) if EXPLICIT_VALUES_FIELD in row else row.get("values", {})
    authored = authored.get("observability", {})
    desired["metrics_retention"] = (
        authored.get("vmStack", {})
        .get("values", {})
        .get("vmsingle", {})
        .get("spec", {})
        .get("retentionPeriod", "90d")
    )
    desired["logs_retention"] = (
        authored.get("vmLogs", {}).get("values", {}).get("server", {}).get("retentionPeriod", "30d")
    )
    if routing.get("pushgateway"):
        desired["pushgateway"] = pushgateway_scrape(dict(payload), target)
    values[ROUTING_KEY] = desired
    # cxcli owns destinations completely. Render upstream without its coupled
    # public flag, which also removes the now-unused native token writer.
    values["observability"]["publicEndpointEnabled"] = False


def route_documents(
    documents: list[dict[str, Any]], values: Mapping[str, Any]
) -> list[dict[str, Any]]:
    routing = values.get(ROUTING_KEY)
    if not routing:
        return copy.deepcopy(documents)
    result = copy.deepcopy(documents)
    for doc in result:
        if doc.get("kind") != "HelmRelease":
            continue
        name = doc.get("metadata", {}).get("name", "")
        spec = doc["spec"]
        child = spec.get("values", {})
        if name.endswith("-vm-stack"):
            agent = child.setdefault("vmagent", {}).setdefault("spec", {})
            from .vmagent_routing import replace_destinations

            child["vmagent"]["additionalRemoteWrites"] = []
            replace_destinations(agent, routing["metrics"])
            jobs = yaml.safe_load(agent.get("inlineScrapeConfig") or "[]")
            jobs = [job for job in jobs if job.get("job_name") != "cxcli-pushgateway"]
            if routing.get("pushgateway"):
                jobs.append(routing["pushgateway"])
            agent["inlineScrapeConfig"] = yaml.safe_dump(jobs, sort_keys=False)
            # Existing PVC/store ownership is retained when routing becomes remote.
            # Explicit storage removal is a separate destructive lifecycle action.
            child.setdefault("vmsingle", {}).setdefault("spec", {})["retentionPeriod"] = routing[
                "metrics_retention"
            ]
        elif "-opentelemetry-collector-" in name:
            generated = routing["logs"]
            config = child["config"]
            # Preserve all upstream receivers/processors, including jail log paths.
            exporters = generated["config"]["exporters"]
            # Frozen upstream OTel versions use the original exporter identifiers.
            exporters = {
                key.replace("otlp_http/", "otlphttp/").replace("otlp_grpc/", "otlp/"): val
                for key, val in exporters.items()
            }
            config["exporters"] = copy.deepcopy(exporters)
            # Disable the child chart's default OTLP-to-debug pipeline when
            # upstream only declares a named pipeline such as logs/events.
            config["service"]["pipelines"].setdefault("logs", None)
            for key, pipeline in config["service"]["pipelines"].items():
                if isinstance(pipeline, dict) and key.split("/")[0] == "logs":
                    pipeline["exporters"] = [
                        key for key, value in exporters.items() if value is not None
                    ]
            child["extraEnvs"] = [
                env
                for env in child.get("extraEnvs", [])
                if not env.get("name", "").startswith("CXCLI_")
            ] + generated["extraEnvs"]
        elif name.endswith("-vm-logs"):
            child.setdefault("server", {})["retentionPeriod"] = routing["logs_retention"]
    return result


def child_patches(
    original: list[dict[str, Any]], routed: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Patch exactly the values used for graph selection, before owner renaming."""
    prior = {
        doc.get("metadata", {}).get("name"): doc
        for doc in original
        if doc.get("kind") == "HelmRelease"
    }
    patches = []
    for doc in routed:
        name = doc.get("metadata", {}).get("name")
        if doc.get("kind") != "HelmRelease" or doc == prior.get(name):
            continue
        patches.append(
            {
                "target": {
                    "group": "helm.toolkit.fluxcd.io",
                    "version": "v2",
                    "kind": "HelmRelease",
                    "name": name,
                },
                "patch": yaml.safe_dump(
                    [{"op": "replace", "path": "/spec/values", "value": doc["spec"]["values"]}],
                    sort_keys=False,
                ),
            }
        )
    return patches
