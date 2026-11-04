"""Collector lifecycle patches shared by rendering and staged handover."""

from __future__ import annotations

import copy
import json
from typing import Any

COLLECTORS = frozenset(
    {
        "nebius-observability-agent",
        "victoria-metrics-k8s-stack",
        "opentelemetry-collector",
        "opentelemetry-logs",
    }
)
SIGNALS_ANNOTATION = "cxcli.nebius.com/telemetry-signals"
COMPONENT_ANNOTATION = "cxcli.nebius.com/telemetry-component"


def owned_signals(component: str, owners: dict[str, str]) -> list[str]:
    if component == "opentelemetry-logs":
        return ["logs"] if owners.get("logs") == "opentelemetry-collector" else []
    return sorted(signal for signal, owner in owners.items() if owner == component)


def inactive_patches(component: str) -> list[dict[str, Any]]:
    if component == "victoria-metrics-k8s-stack":
        return [
            {
                "target": {"kind": "VMAgent"},
                "patch": json.dumps(
                    [
                        {"op": "add", "path": "/spec/replicaCount", "value": 0},
                    ]
                ),
            }
        ]
    # A contradictory selector is unsatisfiable even if a node acquires this key.
    affinity = {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {
                        "matchExpressions": [
                            {"key": "cxcli.nebius.com/retired", "operator": "Exists"},
                            {"key": "cxcli.nebius.com/retired", "operator": "DoesNotExist"},
                        ]
                    }
                ]
            }
        }
    }
    return [
        {
            "target": {"kind": "DaemonSet"},
            "patch": json.dumps(
                [
                    {"op": "add", "path": "/spec/template/spec/affinity", "value": affinity},
                ]
            ),
        },
        {
            "target": {"kind": "Deployment"},
            "patch": json.dumps(
                [
                    {"op": "add", "path": "/spec/replicas", "value": 0},
                ]
            ),
        },
    ]


def inactive_release(document: dict[str, Any], component: str) -> dict[str, Any]:
    result = copy.deepcopy(document)
    result.setdefault("metadata", {}).setdefault("annotations", {})[SIGNALS_ANNOTATION] = ""
    patches = result["spec"].setdefault("postRenderers", [])
    patches.append({"kustomize": {"patches": inactive_patches(component)}})
    return result


def application_scrape_job() -> dict[str, Any]:
    """Same public Pod annotation contract as the Nebius agent."""
    return {
        "job_name": "cxcli-annotated-pods",
        "scrape_interval": "15s",
        "kubernetes_sd_configs": [{"role": "pod"}],
        "relabel_configs": [
            {
                "source_labels": ["__meta_kubernetes_pod_annotation_prometheus_io_scrape"],
                "action": "keep",
                "regex": "true",
            },
            {
                "source_labels": ["__meta_kubernetes_pod_phase"],
                "action": "keep",
                "regex": "Running",
            },
            {
                "source_labels": ["__meta_kubernetes_pod_annotation_prometheus_io_port"],
                "target_label": "__tmp_scrape_port",
            },
            {
                "source_labels": ["__meta_kubernetes_pod_container_port_number"],
                "action": "keepequal",
                "target_label": "__tmp_scrape_port",
            },
            {
                "source_labels": ["__meta_kubernetes_pod_annotation_prometheus_io_path"],
                "regex": "(.+)",
                "target_label": "__metrics_path__",
            },
            {
                "source_labels": ["__meta_kubernetes_pod_annotation_prometheus_io_scheme"],
                "regex": "(https?)",
                "target_label": "__scheme__",
            },
            {"source_labels": ["__meta_kubernetes_namespace"], "target_label": "namespace"},
            {"source_labels": ["__meta_kubernetes_pod_name"], "target_label": "pod"},
        ],
    }
