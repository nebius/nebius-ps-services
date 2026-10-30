"""Shared ordinary-cluster observability intent and application-facing ingress."""

from __future__ import annotations

import copy
from typing import Any

from .observability_routing import (
    SIGNALS,
    app_row,
    collector_owners,
    effective_signals,
    target_settings,
)

TRACE_SERVICE_NAME = "cxcli-otlp"
TRACE_SERVICE_NAMESPACE = "observability"


def trace_endpoint() -> str:
    """The canonical application endpoint supports OTLP/gRPC traces only."""
    return f"{TRACE_SERVICE_NAME}.{TRACE_SERVICE_NAMESPACE}.svc.cluster.local:4317"


def trace_receiver(payload: dict[str, Any], target: str) -> str:
    owner = collector_owners(payload, target).get("traces")
    row = app_row(payload, target, owner or "nebius-observability-agent")
    if not row:
        raise ValueError(f"{target}: no configured trace receiver")
    # The Nebius chart owns a fixed Service name; the OTel chart is rendered
    # with fullnameOverride equal to its release name.
    name = (
        "nebius-observability-agent"
        if owner == "nebius-observability-agent"
        else row["release-name"]
    )
    return f"{name}.{row['namespace']}.svc.cluster.local"


def preset_overrides(
    payload: dict[str, Any], target: str, *, local: bool, overrides: dict[str, Any]
) -> dict[str, Any]:
    """Entering the local workflow is explicit intent; reruns keep saved routes."""
    from .observability import soperator_target_refs

    flags = dict(overrides)
    if target in soperator_target_refs(payload):
        return flags
    routing = target_settings(payload, target)
    if not routing or (local and not routing.get("grafana_access")):
        for signal in SIGNALS:
            if flags.get(f"{signal}_storage") is None:
                flags[f"{signal}_storage"] = "local" if local else "remote"
    return flags


def select_collectors(payload: dict[str, Any], target: str, settings: dict[str, Any]) -> None:
    """Change desired ownership; the deployment handover owns execution ordering."""
    from .observability import soperator_target_refs
    from .observability_routing import save_settings

    if target in soperator_target_refs(payload):
        return
    candidate = copy.deepcopy(payload)
    settings.pop("collectors", None)
    save_settings(candidate, target, settings)
    settings["collectors"] = collector_owners(candidate, target)


def effective_write_destinations(
    payload: dict[str, Any], target: str
) -> dict[str, list[dict[str, str]]]:
    """Describe each producer transport, including both sides of dual writing."""
    from .component_sources import load_component_sources
    from .observability_routing import endpoint_defaults, local_url

    routing = target_settings(payload, target)
    owners = collector_owners(payload, target)
    defaults = endpoint_defaults(payload)
    identity = payload.get("client_info", {}).get("nebius", {})
    result = {}
    for signal in effective_signals(payload, target):
        route = routing[signal]
        destinations = []
        if route["storage"] != "remote":
            url = local_url(payload, target, signal).removesuffix("/select/jaeger")
            url += {
                "metrics": "/api/v1/write",
                "logs": "/insert/opentelemetry/v1/logs",
                "traces": "/insert/opentelemetry/v1/traces",
            }[signal]
            destinations.append(
                {
                    "url": url,
                    "protocol": "prometheus-remote-write" if signal == "metrics" else "otlp-http",
                    "collector": owners.get(signal, ""),
                }
            )
        if route["storage"] != "local":
            remote = route["remote"]
            url = remote.get("url", defaults[signal]["write"])
            protocol = (
                "prometheus-remote-write"
                if signal == "metrics"
                else remote.get("protocol", "otlp-grpc")
            )
            if owners.get(signal) == "nebius-observability-agent":
                if signal == "metrics":
                    template = next(
                        item.template
                        for item in load_component_sources().observability.endpoints.write
                        if item.key == "metrics_otlp_write"
                    )
                    url = template.format(
                        region=identity.get("region_id", ""),
                        project_id=identity.get("project_id", ""),
                    )
                    protocol = "otlp-http"
                else:
                    protocol = "nebius-" + signal + "-grpc"
            destinations.append(
                {"url": url, "protocol": protocol, "collector": owners.get(signal, "")}
            )
        result[signal] = destinations
    return result
