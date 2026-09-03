"""Compile the immutable Flux source graph for one Soperator release."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

import yaml

from .soperator_adapter import (
    SOPERATOR_LIFECYCLE_LABEL,
    SOPERATOR_LIFECYCLE_RECREATABLE,
    soperator_vm_stack_cleanup_exception,
)
from .soperator_release import SoperatorReleaseGraphNode, SoperatorReleaseSnapshot

SOPERATOR_GRAPH_LABEL = "soperator.nebius.ai/release-graph"
SOPERATOR_GRAPH_LABEL_VALUE = "nebius-cxcli"
SOPERATOR_GRAPH_CONFIGMAP = "nebius-cxcli-soperator-release-graph"
SOPERATOR_GRAPH_SCHEMA = "nebius-cxcli.soperator-release-graph.v2"
_VM_STACK_OPERATOR_SOURCE_VALUES = {
    "admissionWebhooks": {"enable": True},
    "crd": {"cleanup": {"enabled": False}, "create": False},
    "operator": {
        "disable_prometheus_converter": False,
        "enable_converter_ownership": True,
    },
}
_VM_STACK_INSTALL_SOURCE_VALUES = {
    "crds": "Skip",
    "remediation": {"retries": 3},
}
_SLURM_CLUSTER_RELEASE_NAME = "soperator-fluxcd-slurm-cluster"
_SLURM_CONTROLLER_SPOOL_CLAIM_PATH = (
    "/spec/slurmNodes/controller/volumes/spool/volumeClaimTemplateSpec"
)
_SLURM_CONTROLLER_SPOOL_SOURCE_PATH = "/spec/slurmNodes/controller/volumes/spool/volumeSourceName"


def _slug(value: str) -> str:
    token = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not token or len(token) > 50:
        raise ValueError(f"release graph identity does not produce a safe name: {value!r}")
    return token


def target_soperator_release_name(value: str) -> str:
    """Return the cxcli owner name without changing Helm's release identity."""

    source = _slug(value)
    candidate = f"cxcli-{source}"
    if len(candidate) <= 63:
        return candidate
    suffix = hashlib.sha256(source.encode()).hexdigest()[:10]
    return f"{candidate[:52].rstrip('-')}-{suffix}"


def _enabled(value: Any, *, default: bool = False) -> bool:
    return value is True if value is not None else default


def _nested(values: Mapping[str, Any], *path: str) -> Mapping[str, Any]:
    current: Any = values
    for key in path:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return current if isinstance(current, Mapping) else {}


def expected_soperator_release_names(values: Mapping[str, Any]) -> frozenset[str]:
    names = {
        "soperator-fluxcd-ns",
        "soperator-fluxcd-kruise",
        "soperator-fluxcd-security-profiles-operator",
        "soperator-fluxcd-custom-configmaps",
        "soperator-fluxcd-soperator",
        "soperator-fluxcd-nodeconfigurator",
        "soperator-fluxcd-slurm-cluster",
    }
    if _enabled(_nested(values, "certManager").get("enabled"), default=True):
        names.add("soperator-fluxcd-cert-manager")
    if _enabled(_nested(values, "mariadbOperator").get("enabled"), default=True):
        names.update(
            {
                "soperator-fluxcd-mariadb-operator-crds",
                "soperator-fluxcd-mariadb-operator",
            }
        )
    if _enabled(_nested(values, "soperator", "soperatorChecks").get("enabled"), default=True):
        names.add("soperator-fluxcd-soperatorchecks")
    if _enabled(_nested(values, "nodesets").get("enabled")):
        names.add("soperator-fluxcd-nodesets")
    if _enabled(_nested(values, "soperatorActiveChecks").get("enabled"), default=True):
        names.add("soperator-fluxcd-soperator-activechecks")
    if _enabled(_nested(values, "storageClasses").get("enabled")):
        names.add("soperator-fluxcd-storageclasses")
    backup = _nested(values, "backup")
    if _enabled(backup.get("enabled")):
        names.add("soperator-fluxcd-k8up")
        if _enabled(_nested(backup, "config").get("enabled")):
            names.add("soperator-fluxcd-backup-config")
    observability = _nested(values, "observability")
    if _enabled(observability.get("enabled")):
        vm_stack = _nested(observability, "vmStack")
        vm_logs_enabled = _enabled(_nested(observability, "vmLogs").get("enabled"), default=True)
        vm_stack_enabled = _enabled(vm_stack.get("enabled"), default=True)
        if _enabled(_nested(observability, "prometheusOperator").get("enabled"), default=True):
            names.add("soperator-fluxcd-prometheus-operator-crds")
        if vm_logs_enabled:
            names.add("soperator-fluxcd-vm-logs")
        if vm_stack_enabled:
            names.update(
                {
                    "soperator-fluxcd-victoria-metrics-operator-crds",
                    "soperator-fluxcd-vm-stack",
                }
            )
            token_kind = str(observability.get("publicEndpointTokenKind") or "secret")
            writer = _nested(vm_stack, "tsaToken", "writer")
            if (
                _enabled(observability.get("publicEndpointEnabled"), default=True)
                and token_kind == "secret"
                and _enabled(writer.get("enabled"), default=True)
            ):
                names.add("soperator-fluxcd-tsa-token-writer")
        opentelemetry = _nested(observability, "opentelemetry")
        if _enabled(opentelemetry.get("enabled"), default=True):
            names.update(
                {
                    "soperator-fluxcd-opentelemetry-collector-events",
                    "soperator-fluxcd-opentelemetry-collector-logs",
                }
            )
            if _enabled(
                _nested(opentelemetry, "logs", "values", "jailLogs").get("enabled"),
                default=True,
            ):
                names.add("soperator-fluxcd-opentelemetry-collector-jail-logs")
        if _enabled(_nested(observability, "dcgmExporter").get("enabled"), default=True):
            names.add("soperator-fluxcd-dcgm-exporter")
        if _enabled(_nested(values, "notifier").get("enabled")) and _enabled(
            _nested(observability, "vmStack").get("enabled"), default=True
        ):
            names.add("soperator-fluxcd-soperator-notifier")
    if _enabled(_nested(values, "soperator", "monitoringDashboards").get("enabled")):
        names.add("soperator-fluxcd-monitoring-dashboards")
    return frozenset(names)


def _source_name(node: SoperatorReleaseGraphNode) -> str:
    prefix = "soperator-upstream" if node.owner == "upstream" else "soperator-third-party"
    return f"{prefix}-{_slug(node.chart_key)}"


def _repository_name(repository: str) -> str:
    suffix = hashlib.sha256(repository.encode()).hexdigest()[:12]
    return f"soperator-repository-{suffix}"


def _labels(lock: SoperatorReleaseSnapshot) -> dict[str, str]:
    return {
        SOPERATOR_GRAPH_LABEL: SOPERATOR_GRAPH_LABEL_VALUE,
        SOPERATOR_LIFECYCLE_LABEL: SOPERATOR_LIFECYCLE_RECREATABLE,
        "app.kubernetes.io/part-of": "soperator",
        "app.kubernetes.io/version": lock.release,
    }


def _oci_repository(
    lock: SoperatorReleaseSnapshot, node: SoperatorReleaseGraphNode
) -> dict[str, Any]:
    chart = lock.charts[node.chart_key]
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "OCIRepository",
        "metadata": {
            "name": _source_name(node),
            "namespace": "flux-system",
            "labels": _labels(lock),
        },
        "spec": {
            "interval": "30m",
            "url": chart.oci_url,
            "layerSelector": {
                "mediaType": "application/vnd.cncf.helm.chart.content.v1.tar+gzip",
                "operation": "copy",
            },
            "ref": {"digest": chart.digest},
        },
    }


def _helm_repository(
    lock: SoperatorReleaseSnapshot,
    *,
    repository: str,
) -> dict[str, Any]:
    spec: dict[str, Any] = {"interval": "30m", "url": repository}
    if repository.startswith("oci://"):
        spec["type"] = "oci"
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {
            "name": _repository_name(repository),
            "namespace": "flux-system",
            "labels": _labels(lock),
        },
        "spec": spec,
    }


def _helm_chart(lock: SoperatorReleaseSnapshot, node: SoperatorReleaseGraphNode) -> dict[str, Any]:
    chart = lock.third_party_charts[node.chart_key]
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmChart",
        "metadata": {
            "name": _source_name(node),
            "namespace": "flux-system",
            "labels": _labels(lock),
            "annotations": {
                "soperator.nebius.ai/package-sha256": chart.package_sha256,
            },
        },
        "spec": {
            "interval": "30m",
            "chart": chart.chart,
            "version": chart.version,
            "sourceRef": {
                "kind": "HelmRepository",
                "name": _repository_name(chart.repository),
            },
            "suspend": True,
        },
    }


def _adapter_storage_contract(
    documents: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for document in documents or []:
        kind = str(document.get("kind") or "")
        if kind not in {"PersistentVolume", "PersistentVolumeClaim"}:
            continue
        metadata = document.get("metadata")
        spec = document.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise ValueError("Soperator adapter storage document is invalid")
        labels = metadata.get("labels")
        lifecycle = (
            str(labels.get(SOPERATOR_LIFECYCLE_LABEL) or "") if isinstance(labels, Mapping) else ""
        )
        if lifecycle not in {"protected", "shared-adopted"}:
            continue
        identity: dict[str, Any] = {
            "kind": kind,
            "name": str(metadata.get("name") or ""),
            "namespace": str(metadata.get("namespace") or ""),
            "lifecycle": lifecycle,
            "storageClassName": str(spec.get("storageClassName") or ""),
        }
        if kind == "PersistentVolumeClaim":
            identity["volumeName"] = str(spec.get("volumeName") or "")
        else:
            identity["reclaimPolicy"] = str(spec.get("persistentVolumeReclaimPolicy") or "")
            for backend in ("local", "nfs", "csi"):
                backend_value = spec.get(backend)
                if isinstance(backend_value, Mapping):
                    identity["backend"] = {
                        "kind": backend,
                        "identity": hashlib.sha256(
                            json.dumps(
                                dict(backend_value),
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                    }
                    break
        if not identity["name"]:
            raise ValueError("Soperator adapter storage document has no name")
        inventory.append(identity)
    return sorted(
        inventory,
        key=lambda item: (item["kind"], item["namespace"], item["name"]),
    )


def _readiness_contract(
    values: Mapping[str, Any],
    *,
    adapter_documents: list[dict[str, Any]] | None,
    release_names: frozenset[str],
) -> dict[str, Any]:
    raw_nodesets = _nested(values, "nodesets", "overrideValues").get("nodesets")
    nodesets = (
        sorted(
            {
                str(item.get("name") or "")
                for item in raw_nodesets
                if isinstance(item, Mapping) and str(item.get("name") or "")
            }
        )
        if isinstance(raw_nodesets, list)
        else []
    )
    telemetry_releases = sorted(
        name
        for name in release_names
        if any(
            token in name
            for token in ("vm-", "victoria-metrics", "opentelemetry", "dcgm", "notifier")
        )
    )
    return {
        "roles": ["accounting", "controller", "login"],
        "nodeSets": nodesets,
        "activeChecksRequired": target_soperator_release_name(
            "soperator-fluxcd-soperator-activechecks"
        )
        in release_names,
        "telemetryReleases": telemetry_releases,
        "storage": _adapter_storage_contract(adapter_documents),
    }


def soperator_graph_post_render_patches(
    lock: SoperatorReleaseSnapshot,
    values: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected = expected_soperator_release_names(values)
    nodes = {node.release_name: node for node in lock.release_graph}
    unknown = expected - nodes.keys()
    if unknown:
        raise ValueError(
            "Soperator values enable an unverified release: " + ", ".join(sorted(unknown))
        )
    vm_stack_exception = soperator_vm_stack_cleanup_exception(lock)
    patches: list[dict[str, Any]] = []
    for release_name in sorted(expected):
        node = nodes[release_name]
        source_kind = "OCIRepository" if node.owner == "upstream" else "HelmChart"
        patch = [
            {"op": "remove", "path": "/spec/chart"},
            {
                "op": "add",
                "path": "/spec/chartRef",
                "value": {
                    "kind": source_kind,
                    "name": _source_name(node),
                    "namespace": "flux-system",
                },
            },
            {
                "op": "replace",
                "path": "/metadata/name",
                "value": target_soperator_release_name(release_name),
            },
            {
                "op": "add",
                "path": "/metadata/labels/soperator.nebius.ai~1release-graph",
                "value": SOPERATOR_GRAPH_LABEL_VALUE,
            },
            {
                "op": "add",
                "path": "/metadata/labels/soperator.nebius.ai~1release-stage",
                "value": str(node.stage),
            },
            {
                "op": "add",
                "path": "/metadata/labels/app.kubernetes.io~1version",
                "value": lock.release,
            },
        ]
        if node.dependencies:
            patch.insert(
                3,
                {
                    "op": "add",
                    "path": "/spec/dependsOn",
                    "value": [
                        {
                            "name": target_soperator_release_name(dependency),
                            "namespace": nodes[dependency].namespace,
                        }
                        for dependency in node.dependencies
                        if dependency in expected
                    ],
                },
            )
        if release_name == "soperator-fluxcd-vm-stack":
            patch.append(
                {
                    "op": "add",
                    "path": "/spec/values/grafana",
                    "value": {"enabled": False},
                }
            )
            if vm_stack_exception is not None:
                patch.extend(
                    [
                        {
                            "op": "test",
                            "path": "/spec/values/victoria-metrics-operator",
                            "value": _VM_STACK_OPERATOR_SOURCE_VALUES,
                        },
                        {
                            "op": "add",
                            "path": "/spec/values/victoria-metrics-operator/crds",
                            "value": {"cleanup": {"enabled": False}},
                        },
                        {
                            "op": "test",
                            "path": "/spec/install",
                            "value": _VM_STACK_INSTALL_SOURCE_VALUES,
                        },
                        {
                            "op": "add",
                            "path": "/spec/install/strategy",
                            "value": {
                                "name": "RetryOnFailure",
                                "retryInterval": "30s",
                            },
                        },
                    ]
                )
        if release_name == _SLURM_CLUSTER_RELEASE_NAME:
            override_values = _nested(values, "slurmCluster", "overrideValues")
            cluster_name = str(override_values.get("clusterName") or "").strip()
            if not cluster_name:
                raise ValueError("Soperator Slurm child requires an exact cluster identity")
            child_patch: list[dict[str, Any]] = [
                {
                    "op": "test",
                    "path": _SLURM_CONTROLLER_SPOOL_SOURCE_PATH,
                    "value": "controller-spool",
                },
                {
                    "op": "remove",
                    "path": _SLURM_CONTROLLER_SPOOL_CLAIM_PATH,
                },
            ]
            patch.append(
                {
                    "op": "add",
                    "path": "/spec/postRenderers",
                    "value": [
                        {
                            "kustomize": {
                                "patches": [
                                    {
                                        "target": {
                                            "group": "slurm.nebius.ai",
                                            "version": "v1",
                                            "kind": "SlurmCluster",
                                            "name": cluster_name,
                                        },
                                        "patch": yaml.safe_dump(
                                            child_patch,
                                            sort_keys=False,
                                        ),
                                    }
                                ]
                            }
                        }
                    ],
                }
            )
        patches.append(
            {
                "target": {
                    "group": "helm.toolkit.fluxcd.io",
                    "version": "v2",
                    "kind": "HelmRelease",
                    "name": release_name,
                },
                "patch": yaml.safe_dump(patch, sort_keys=False),
            }
        )
    return patches


def render_soperator_flux_graph_documents(
    lock: SoperatorReleaseSnapshot,
    values: Mapping[str, Any],
    *,
    adapter_documents: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    expected = expected_soperator_release_names(values)
    nodes = {node.release_name: node for node in lock.release_graph}
    unknown = expected - nodes.keys()
    if unknown:
        raise ValueError(
            "Soperator values enable an unverified release: " + ", ".join(sorted(unknown))
        )
    selected = [nodes[name] for name in sorted(expected)]
    repositories: dict[str, dict[str, Any]] = {}
    sources: dict[tuple[str, str], dict[str, Any]] = {}
    graph_payload: list[dict[str, Any]] = []
    for node in selected:
        if node.owner == "upstream":
            source = _oci_repository(lock, node)
            upstream_chart = lock.charts[node.chart_key]
            revision = upstream_chart.digest
        else:
            third_party_chart = lock.third_party_charts[node.chart_key]
            repositories.setdefault(
                third_party_chart.repository,
                _helm_repository(lock, repository=third_party_chart.repository),
            )
            source = _helm_chart(lock, node)
            revision = third_party_chart.package_sha256
        sources[(source["kind"], source["metadata"]["name"])] = source
        graph_payload.append(
            {
                "releaseName": target_soperator_release_name(node.release_name),
                "upstreamReleaseName": node.release_name,
                "namespace": node.namespace,
                "owner": node.owner,
                "stage": node.stage,
                "sourceKind": source["kind"],
                "sourceName": source["metadata"]["name"],
                "revision": revision,
                "dependencies": [
                    target_soperator_release_name(item)
                    for item in node.dependencies
                    if item in expected
                ],
                "isMain": node.is_main,
            }
        )
    main_rows = [row for row in graph_payload if row["isMain"]]
    if len(main_rows) != 1:
        raise ValueError("rendered Soperator release graph must contain exactly one main workload")
    contract = {
        "schema": SOPERATOR_GRAPH_SCHEMA,
        "release": lock.release,
        "sourceCommit": lock.commit,
        "sourceTree": lock.tree,
        "sourceManifestSha256": lock.source_manifest_sha256,
        "clusterName": str(
            _nested(values, "slurmCluster", "overrideValues").get("clusterName") or "soperator"
        ),
        "readiness": _readiness_contract(
            values,
            adapter_documents=adapter_documents,
            release_names=frozenset(target_soperator_release_name(name) for name in expected),
        ),
        "releases": sorted(graph_payload, key=lambda item: item["releaseName"]),
    }
    configmap = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": SOPERATOR_GRAPH_CONFIGMAP,
            "namespace": "flux-system",
            "labels": _labels(lock),
        },
        "data": {"graph.json": json.dumps(contract, sort_keys=True, separators=(",", ":"))},
    }
    return [
        *sorted(repositories.values(), key=lambda item: item["metadata"]["name"]),
        *[sources[key] for key in sorted(sources)],
        configmap,
    ]


__all__ = [
    "SOPERATOR_GRAPH_CONFIGMAP",
    "SOPERATOR_GRAPH_LABEL",
    "SOPERATOR_GRAPH_LABEL_VALUE",
    "SOPERATOR_GRAPH_SCHEMA",
    "expected_soperator_release_names",
    "render_soperator_flux_graph_documents",
    "soperator_graph_post_render_patches",
    "target_soperator_release_name",
]
