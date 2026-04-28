from __future__ import annotations

from pathlib import Path

import pytest

import nebius_cxcli.component_sources as component_sources
from nebius_cxcli.component_sources import (
    ComponentOutput,
    InfraObservabilitySettings,
    SourceProfile,
    VmStandaloneCollectorSettings,
    reset_component_sources_cache,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.observability import (
    ensure_observability_app_rows,
    materialize_observability_app_values,
    materialize_observability_infra_values,
    normalize_observability_project_settings,
    observability_dependency_issues,
    observability_endpoint_summary,
    observability_gpu_node_label_reconciliation,
    observability_project_defaults,
    observability_status_summary,
    resolve_observability_app_selection,
)
from nebius_cxcli.runtime_introspection import reset_runtime_introspection_cache

_NEBIUS_CPU_ONLY_AFFINITY = {
    "nodeAffinity": {
        "requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [
                {
                    "matchExpressions": [
                        {
                            "key": "nebius.com/gpu",
                            "operator": "NotIn",
                            "values": ["true"],
                        }
                    ]
                }
            ]
        }
    }
}


def _reset_catalog_override() -> None:
    set_component_sources_file_override(None)
    set_component_sources_profile_override(None)
    reset_component_sources_cache()
    reset_runtime_introspection_cache()
    reset_component_entry_cache()


def setup_function() -> None:
    _reset_catalog_override()
    set_component_sources_file_override(_local_catalog_path())
    set_component_sources_profile_override(SourceProfile.LOCAL)
    reset_component_sources_cache()
    reset_component_entry_cache()


def teardown_function() -> None:
    _reset_catalog_override()


@pytest.fixture(autouse=True)
def _stub_catalog_output_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        component_sources,
        "_discover_terraform_outputs",
        lambda _source: (
            ComponentOutput(
                name="cluster_id",
                kind="terraform_output",
                source_path="cluster_id",
                sensitive=False,
            ),
        ),
    )


def _local_catalog_path() -> Path:
    return Path(__file__).resolve().parents[1] / "component_sources.yaml"


def _base_payload(
    *,
    observability_enabled: bool = False,
    mk8s_enabled: bool = True,
    vm_enabled: bool = False,
    enabled_apps: tuple[str, ...] = (),
) -> dict:
    charts = []
    for app_id in enabled_apps:
        namespace = "observability" if app_id == "nebius-observability-agent" else app_id
        release_name = (
            "nebius-observability-agent" if app_id == "nebius-observability-agent" else app_id
        )
        chart = {
            "id": app_id,
            "instance_id": "mk8s" if mk8s_enabled else app_id,
            "enabled": True,
            "group": "platform" if app_id.startswith("nvidia-") else "observability",
            "repo": "oci://example.invalid/chart",
            "version": "1.0.0",
            "namespace": namespace,
            "release-name": release_name,
            "values": {},
        }
        if mk8s_enabled:
            chart["target_ref"] = "mk8s"
        charts.append(chart)
    deploy = {}
    if mk8s_enabled:
        deploy["targets"] = [
            {
                "instance_id": "mk8s",
                "observability": {
                    "enabled": observability_enabled,
                },
            }
        ]
    if vm_enabled:
        deploy["observability"] = {
            "enabled": observability_enabled,
        }
    payload = {
        "deploy": deploy,
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": mk8s_enabled,
                    "inputs": {
                        "gpu_enabled": True,
                    },
                },
                {
                    "id": "vm",
                    "instance_id": "vm",
                    "enabled": vm_enabled,
                    "inputs": {},
                },
            ]
        },
        "apps": {"charts": charts},
    }
    normalize_observability_project_settings(payload)
    return payload


def _chart_row(payload: dict, app_id: str) -> dict:
    charts = payload.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    return next(item for item in charts if item.get("id") == app_id)


def _chart_rows(payload: dict, app_id: str) -> list[dict]:
    charts = payload.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    return [item for item in charts if item.get("id") == app_id]


def _set_observability_targets(payload: dict, *target_refs: str, enabled: bool = True) -> None:
    payload["deploy"] = {
        "targets": [
            {
                "instance_id": target_ref,
                "observability": {
                    "enabled": enabled,
                    "kubernetes": {
                        "logs": {
                            "enabled": True,
                            "collect_agent_logs": False,
                            "excluded_namespaces": ["kube-system"],
                        },
                        "metrics": {
                            "enabled": True,
                            "collect_agent_metrics": False,
                            "collect_k8s_cluster_metrics": True,
                            "excluded_namespaces": ["kube-system"],
                        },
                        "traces": {
                            "enabled": True,
                        },
                    },
                },
            }
            for target_ref in target_refs
        ]
    }


def test_observability_project_defaults_follow_catalog() -> None:
    defaults = observability_project_defaults()

    assert defaults["enabled"] is False
    assert defaults["kubernetes"]["logs"]["enabled"] is True
    assert defaults["kubernetes"]["logs"]["excluded_namespaces"] == ["kube-system"]
    assert defaults["kubernetes"]["metrics"]["collect_k8s_cluster_metrics"] is True
    assert defaults["kubernetes"]["traces"]["enabled"] is True
    assert defaults["vm"]["collector"]["enabled"] is False
    assert defaults["vm"]["collector"]["metrics"]["enabled"] is True
    assert defaults["vm"]["collector"]["logs"]["enabled"] is True
    assert defaults["vm"]["collector"]["logs"]["systemd_units"] == []
    assert defaults["vm"]["logs"]["enabled"] is True
    assert defaults["vm"]["logs"]["systemd_units"] == []


def test_normalize_observability_settings_adds_project_defaults() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {"charts": []},
    }

    changed = normalize_observability_project_settings(payload)

    assert changed is True
    target = payload["deploy"]["targets"][0]
    assert target["instance_id"] == "mk8s"
    assert target["observability"]["enabled"] is False
    assert target["observability"]["kubernetes"]["logs"]["excluded_namespaces"] == ["kube-system"]
    assert "observability" not in payload["deploy"]


def test_normalize_observability_settings_vm_only_omits_kubernetes_defaults() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vm",
                    "instance_id": "vm",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {"charts": []},
    }

    changed = normalize_observability_project_settings(payload)

    assert changed is True
    assert payload["deploy"]["observability"]["enabled"] is False
    assert "kubernetes" not in payload["deploy"]["observability"]
    assert payload["deploy"]["observability"]["vm"]["collector"]["enabled"] is False
    assert payload["deploy"]["observability"]["vm"]["logs"]["enabled"] is True


def test_normalize_observability_settings_preserves_invalid_root_kubernetes_branch() -> None:
    payload = {
        "deploy": {
            "observability": {
                "enabled": True,
                "kubernetes": {
                    "logs": {"enabled": False},
                },
            },
        },
        "infra": {
            "components": [
                {
                    "id": "vm",
                    "instance_id": "vm",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {"charts": []},
    }

    changed = normalize_observability_project_settings(payload)

    assert changed is True
    assert payload["deploy"]["observability"]["kubernetes"] == {"logs": {"enabled": False}}
    assert payload["deploy"]["observability"]["enabled"] is True
    assert payload["deploy"]["observability"]["vm"]["collector"]["enabled"] is False
    assert payload["deploy"]["observability"]["vm"]["logs"]["enabled"] is True


def test_observability_auto_enables_k8s_agent_when_enabled() -> None:
    payload = _base_payload(observability_enabled=True)

    selection = resolve_observability_app_selection(
        payload,
        selected_app_ids=set(),
        app_entries=component_entries("apps"),
    )

    assert selection.observability_enabled is True
    assert selection.kubernetes_agent_required is True
    assert selection.collector_app_id == "nebius-observability-agent"
    assert selection.grafana_app_id == "grafana"
    assert selection.grafana_required is True
    assert selection.grafana_gateway_app_id == "gateway-helm"
    assert selection.grafana_gateway_required is True
    assert selection.auto_enabled_app_ids == (
        "gateway-helm",
        "grafana",
        "nebius-observability-agent",
    )
    assert selection.selected_app_ids == (
        "gateway-helm",
        "grafana",
        "nebius-observability-agent",
    )


def test_ensure_observability_app_rows_seeds_collector_for_direct_config_edit() -> None:
    payload = _base_payload(observability_enabled=True)

    changed = ensure_observability_app_rows(payload, app_entries=component_entries("apps"))

    assert changed is True
    chart = _chart_row(payload, "nebius-observability-agent")
    assert chart["enabled"] is True
    assert chart["repo"] == (
        "oci://cr.nebius.cloud/observability/public/nebius-observability-agent-helm"
    )
    assert chart["version"] == "1.0.5"
    assert chart["namespace"] == "observability"
    assert chart["release-name"] == "nebius-observability-agent"
    gateway = _chart_row(payload, "gateway-helm")
    assert gateway["enabled"] is True
    assert gateway["repo"] == "oci://docker.io/envoyproxy/gateway-helm"
    assert gateway["version"] == "1.7.0"
    assert gateway["namespace"] == "envoy-gateway-system"
    assert gateway["release-name"] == "envoy-gateway"
    assert gateway["values"]["deployment"]["pod"]["affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    grafana = _chart_row(payload, "grafana")
    assert grafana["enabled"] is True
    assert grafana["repo"] == "https://grafana-community.github.io/helm-charts"
    assert grafana["version"] == "12.1.3"
    assert grafana["namespace"] == "observability"
    assert grafana["release-name"] == "grafana"
    assert grafana["values"]["affinity"] == _NEBIUS_CPU_ONLY_AFFINITY


def test_ensure_observability_app_rows_seeds_one_collector_per_target() -> None:
    payload = _base_payload(observability_enabled=True)
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "blue",
            "enabled": True,
            "inputs": {"gpu_enabled": False},
        },
        {
            "id": "mk8s",
            "instance_id": "green",
            "enabled": True,
            "inputs": {"gpu_enabled": False},
        },
    ]
    _set_observability_targets(payload, "blue", "green")

    changed = ensure_observability_app_rows(payload, app_entries=component_entries("apps"))

    assert changed is True
    charts = sorted(
        _chart_rows(payload, "nebius-observability-agent"),
        key=lambda item: str(item["target_ref"]),
    )
    assert [item["target_ref"] for item in charts] == ["blue", "green"]
    assert [item["instance_id"] for item in charts] == ["blue", "green"]
    assert all(item["namespace"] == "observability" for item in charts)
    assert all(item["release-name"] == "nebius-observability-agent" for item in charts)
    grafana_charts = sorted(
        _chart_rows(payload, "grafana"),
        key=lambda item: str(item["target_ref"]),
    )
    assert [item["target_ref"] for item in grafana_charts] == ["blue", "green"]
    assert [item["instance_id"] for item in grafana_charts] == ["blue", "green"]
    assert all(item["namespace"] == "observability" for item in grafana_charts)
    assert all(item["release-name"] == "grafana" for item in grafana_charts)
    gateway_charts = sorted(
        _chart_rows(payload, "gateway-helm"),
        key=lambda item: str(item["target_ref"]),
    )
    assert [item["target_ref"] for item in gateway_charts] == ["blue", "green"]
    assert [item["instance_id"] for item in gateway_charts] == ["blue", "green"]
    assert all(item["namespace"] == "envoy-gateway-system" for item in gateway_charts)
    assert all(item["release-name"] == "envoy-gateway" for item in gateway_charts)


def test_ensure_observability_app_rows_fills_missing_collector_target() -> None:
    payload = _base_payload(observability_enabled=True)
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "blue",
            "enabled": True,
            "inputs": {"gpu_enabled": False},
        },
        {
            "id": "mk8s",
            "instance_id": "green",
            "enabled": True,
            "inputs": {"gpu_enabled": False},
        },
    ]
    _set_observability_targets(payload, "blue", "green")
    payload["apps"]["charts"] = [
        {
            "id": "nebius-observability-agent",
            "instance_id": "blue",
            "enabled": True,
            "target_ref": "blue",
            "values": {},
        }
    ]

    changed = ensure_observability_app_rows(payload, app_entries=component_entries("apps"))

    assert changed is True
    charts = sorted(
        _chart_rows(payload, "nebius-observability-agent"),
        key=lambda item: str(item["target_ref"]),
    )
    assert [item["target_ref"] for item in charts] == ["blue", "green"]
    assert [item["instance_id"] for item in charts] == ["blue", "green"]


def test_observability_does_not_auto_enable_agent_when_disabled() -> None:
    payload = _base_payload(observability_enabled=False)

    selection = resolve_observability_app_selection(
        payload,
        selected_app_ids=set(),
        app_entries=component_entries("apps"),
    )

    assert selection.observability_enabled is False
    assert selection.kubernetes_agent_required is False
    assert selection.auto_enabled_app_ids == ()


def test_observability_dependency_issues_require_target_toggle_for_agent() -> None:
    payload = _base_payload(
        observability_enabled=False,
        enabled_apps=("nebius-observability-agent",),
    )

    issues = observability_dependency_issues(payload, app_entries=component_entries("apps"))

    assert issues == [
        "apps:nebius-observability-agent@mk8s requires "
        "deploy.targets[instance_id=mk8s].observability.enabled=true"
    ]


def test_observability_dependency_issues_are_bound_to_collector_target() -> None:
    payload = _base_payload(observability_enabled=False)
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "blue",
            "enabled": True,
            "inputs": {"gpu_enabled": False},
        },
        {
            "id": "mk8s",
            "instance_id": "green",
            "enabled": True,
            "inputs": {"gpu_enabled": False},
        },
    ]
    payload["deploy"] = {
        "targets": [
            {"instance_id": "blue", "observability": {"enabled": True}},
            {"instance_id": "green", "observability": {"enabled": False}},
        ]
    }
    payload["apps"]["charts"] = [
        {
            "id": "nebius-observability-agent",
            "instance_id": "blue",
            "enabled": True,
            "target_ref": "blue",
            "values": {},
        },
        {
            "id": "nebius-observability-agent",
            "instance_id": "green",
            "enabled": True,
            "target_ref": "green",
            "values": {},
        },
        {
            "id": "grafana",
            "instance_id": "blue",
            "enabled": True,
            "target_ref": "blue",
            "values": {},
        },
        {
            "id": "gateway-helm",
            "instance_id": "blue",
            "enabled": True,
            "target_ref": "blue",
            "values": {},
        },
    ]

    issues = observability_dependency_issues(payload, app_entries=component_entries("apps"))

    assert issues == [
        "apps:nebius-observability-agent@green requires "
        "deploy.targets[instance_id=green].observability.enabled=true"
    ]


def test_materialize_observability_agent_values_from_target_contract() -> None:
    payload = _base_payload(
        observability_enabled=True,
        enabled_apps=("nebius-observability-agent",),
    )

    materialize_observability_app_values(payload)
    chart = _chart_row(payload, "nebius-observability-agent")

    assert chart["values"]["config"]["logs"]["enabled"] is True
    assert chart["values"]["config"]["logs"]["collectAgentLogs"] is False
    assert chart["values"]["config"]["logs"]["excludedNamespaces"] == ["kube-system"]
    assert chart["values"]["config"]["metrics"]["enabled"] is True
    assert chart["values"]["config"]["metrics"]["collectAgentMetrics"] is False
    assert chart["values"]["config"]["metrics"]["collectK8sClusterMetrics"] is True
    assert chart["values"]["config"]["traces"]["enabled"] is True
    assert "additionalTargets" not in chart["values"]["config"]["metrics"]


def test_materialize_observability_agent_values_adds_catalog_metric_targets() -> None:
    payload = _base_payload(
        observability_enabled=True,
        enabled_apps=("nebius-observability-agent", "nvidia-gpu-operator"),
    )
    chart = _chart_row(payload, "nebius-observability-agent")
    chart["values"] = {
        "config": {
            "metrics": {
                "additionalTargets": [
                    {
                        "job_name": "customer-target",
                        "static_configs": [{"targets": ["customer:9090"]}],
                    }
                ]
            }
        }
    }

    materialize_observability_app_values(payload)

    additional_targets = chart["values"]["config"]["metrics"]["additionalTargets"]
    assert [item["job_name"] for item in additional_targets] == [
        "customer-target",
        "cxcli-nvidia-dcgm-exporter",
    ]
    dcgm_target = additional_targets[1]
    assert dcgm_target["kubernetes_sd_configs"] == [{"role": "endpoints"}]
    assert dcgm_target["relabel_configs"] == [
        {
            "source_labels": ["__meta_kubernetes_namespace"],
            "action": "keep",
            "regex": "nvidia-gpu-operator",
        },
        {
            "source_labels": ["__meta_kubernetes_service_name"],
            "action": "keep",
            "regex": "nvidia-dcgm-exporter",
        },
        {
            "source_labels": ["__meta_kubernetes_endpoint_port_number"],
            "action": "keep",
            "regex": "9400",
        },
    ]

    payload["apps"]["charts"] = [
        item for item in payload["apps"]["charts"] if item["id"] != "nvidia-gpu-operator"
    ]
    materialize_observability_app_values(payload)
    assert chart["values"]["config"]["metrics"]["additionalTargets"] == [
        {
            "job_name": "customer-target",
            "static_configs": [{"targets": ["customer:9090"]}],
        }
    ]


def test_materialize_grafana_datasources_from_observability_read_endpoints() -> None:
    payload = _base_payload(
        observability_enabled=True,
        enabled_apps=("grafana",),
    )
    payload["client_info"] = {
        "client_name": "client-a",
        "nebius": {
            "tenant_id": "tenant-123",
            "project_id": "project-456",
            "region_id": "eu-north1",
        },
    }

    materialize_observability_app_values(payload)

    values = _chart_row(payload, "grafana")["values"]
    assert values["admin"] == {
        "existingSecret": "nebius-cxcli-grafana-admin",
        "userKey": "admin-user",
        "passwordKey": "admin-password",
    }
    assert values["envValueFrom"] == {
        "NEBIUS_OBSERVABILITY_STATIC_TOKEN": {
            "secretKeyRef": {
                "name": "nebius-cxcli-grafana-observability-read",
                "key": "token",
            }
        }
    }
    datasources = values["datasources"]["datasources.yaml"]["datasources"]
    assert [item["uid"] for item in datasources] == [
        "nebius-service-metrics",
        "nebius-user-metrics",
        "nebius-logs",
        "nebius-traces",
    ]
    assert datasources[0]["name"] == "Nebius Services"
    assert datasources[0]["type"] == "prometheus"
    assert datasources[0]["url"] == (
        "https://read.monitoring.api.nebius.cloud/projects/project-456/service-provider/prometheus"
    )
    assert datasources[2]["type"] == "loki"
    assert datasources[2]["url"] == "https://read.logging.api.nebius.cloud/projects/project-456"
    assert datasources[3]["type"] == "tempo"
    assert datasources[3]["url"] == (
        "https://read.tracing.api.nebius.cloud/projects/project-456/tempo"
    )
    assert all(
        item["secureJsonData"]["httpHeaderValue1"] == "Bearer ${NEBIUS_OBSERVABILITY_STATIC_TOKEN}"
        for item in datasources
    )
    assert values["grafana.ini"]["auth"]["login_maximum_inactive_lifetime_duration"] == "20m"


def test_materialize_observability_agent_values_for_each_target_row() -> None:
    payload = _base_payload(
        observability_enabled=True,
        enabled_apps=("nebius-observability-agent",),
    )
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "blue",
            "enabled": True,
            "inputs": {"gpu_enabled": False},
        },
        {
            "id": "mk8s",
            "instance_id": "green",
            "enabled": True,
            "inputs": {"gpu_enabled": False},
        },
    ]
    _set_observability_targets(payload, "blue", "green")
    payload["apps"]["charts"] = [
        {
            "id": "nebius-observability-agent",
            "instance_id": "blue",
            "enabled": True,
            "target_ref": "blue",
            "group": "observability",
            "repo": "oci://example.invalid/chart",
            "version": "1.0.0",
            "namespace": "observability",
            "release-name": "nebius-observability-agent",
            "values": {
                "config": {
                    "logs": {
                        "enabled": False,
                    }
                }
            },
        },
        {
            "id": "nebius-observability-agent",
            "instance_id": "green",
            "enabled": True,
            "target_ref": "green",
            "group": "observability",
            "repo": "oci://example.invalid/chart",
            "version": "1.0.0",
            "namespace": "observability",
            "release-name": "nebius-observability-agent",
            "values": {},
        },
    ]

    materialize_observability_app_values(payload)

    charts = sorted(
        _chart_rows(payload, "nebius-observability-agent"),
        key=lambda item: str(item["target_ref"]),
    )
    for chart in charts:
        assert chart["values"]["config"]["logs"]["enabled"] is True
        assert chart["values"]["config"]["metrics"]["enabled"] is True
        assert chart["values"]["config"]["metrics"]["collectK8sClusterMetrics"] is True
        assert chart["values"]["config"]["traces"]["enabled"] is True


def test_materialize_observability_infra_values_sets_dcgm_gpu_node_labels() -> None:
    payload = _base_payload(
        observability_enabled=True,
        enabled_apps=("nebius-observability-agent", "nvidia-gpu-operator"),
    )
    mk8s_row = payload["infra"]["components"][0]
    mk8s_row["inputs"]["mk8s_gpu_node_group_overrides"] = {
        "labels": {
            "example.com/custom": "kept",
        }
    }

    changed = materialize_observability_infra_values(payload)

    labels = mk8s_row["inputs"]["mk8s_gpu_node_group_overrides"]["labels"]
    assert changed is True
    assert labels["example.com/custom"] == "kept"
    assert labels["nvidia.com/gpu.deploy.operands"] == "true"
    assert labels["nvidia.com/gpu.deploy.dcgm-exporter"] == "true"
    assert labels["nvidia.com/gpu.deploy.operator-validator"] == "true"
    assert labels["nvidia.com/gpu.deploy.device-plugin"] == "false"
    assert labels["nvidia.com/gpu.deploy.gpu-feature-discovery"] == "false"


def test_observability_gpu_node_label_reconciliation_uses_catalog_selector() -> None:
    payload = _base_payload(
        observability_enabled=True,
        enabled_apps=("nebius-observability-agent", "nvidia-gpu-operator"),
    )

    policy = observability_gpu_node_label_reconciliation(payload)

    assert policy.enabled is True
    assert dict(policy.selector) == {"nebius.com/gpu": "true"}
    assert dict(policy.labels)["nvidia.com/gpu.deploy.dcgm-exporter"] == "true"


def test_observability_gpu_node_label_reconciliation_skips_operator_managed_stack() -> None:
    payload = _base_payload(
        observability_enabled=True,
        enabled_apps=("nebius-observability-agent", "nvidia-gpu-operator"),
    )
    payload["infra"]["components"][0]["inputs"]["gpu_stack_source"] = "operator_managed"

    policy = observability_gpu_node_label_reconciliation(payload)
    changed = materialize_observability_infra_values(payload)

    assert policy.enabled is False
    assert changed is False


def test_materialize_observability_infra_values_cleans_stale_dcgm_gpu_node_labels() -> None:
    payload = _base_payload(
        observability_enabled=False,
        enabled_apps=("nebius-observability-agent", "nvidia-gpu-operator"),
    )
    mk8s_row = payload["infra"]["components"][0]
    mk8s_row["inputs"]["mk8s_gpu_node_group_overrides"] = {
        "labels": {
            "example.com/custom": "kept",
            "nvidia.com/gpu.deploy.operands": "true",
            "nvidia.com/gpu.deploy.dcgm-exporter": "true",
            "nvidia.com/gpu.deploy.operator-validator": "true",
            "nvidia.com/gpu.deploy.device-plugin": "false",
            "nvidia.com/gpu.deploy.gpu-feature-discovery": "false",
        }
    }

    changed = materialize_observability_infra_values(payload)

    labels = mk8s_row["inputs"]["mk8s_gpu_node_group_overrides"]["labels"]
    assert changed is True
    assert labels == {"example.com/custom": "kept"}


def test_materialize_observability_infra_values_preserves_non_catalog_label_values() -> None:
    payload = _base_payload(
        observability_enabled=False,
        enabled_apps=("nebius-observability-agent", "nvidia-gpu-operator"),
    )
    mk8s_row = payload["infra"]["components"][0]
    mk8s_row["inputs"]["mk8s_gpu_node_group_overrides"] = {
        "labels": {
            "nvidia.com/gpu.deploy.operands": "false",
        }
    }

    changed = materialize_observability_infra_values(payload)

    labels = mk8s_row["inputs"]["mk8s_gpu_node_group_overrides"]["labels"]
    assert changed is False
    assert labels == {"nvidia.com/gpu.deploy.operands": "false"}


def test_materialize_observability_infra_values_skips_dcgm_labels_when_metrics_disabled() -> None:
    payload = _base_payload(
        observability_enabled=True,
        enabled_apps=("nebius-observability-agent", "nvidia-gpu-operator"),
    )
    payload["deploy"]["targets"][0]["observability"]["kubernetes"]["metrics"]["enabled"] = False

    changed = materialize_observability_infra_values(payload)

    mk8s_row = payload["infra"]["components"][0]
    assert changed is False
    assert "mk8s_gpu_node_group_overrides" not in mk8s_row["inputs"]


def test_materialize_observability_infra_values_sets_vm_journald_labels() -> None:
    payload = _base_payload(
        observability_enabled=True,
        mk8s_enabled=False,
        vm_enabled=True,
    )
    payload["deploy"]["observability"]["vm"]["logs"]["enabled"] = True
    payload["deploy"]["observability"]["vm"]["logs"]["systemd_units"] = [
        "sshd.service",
        "docker.service",
    ]

    changed = materialize_observability_infra_values(payload)

    vm_row = payload["infra"]["components"][1]
    assert changed is True
    assert vm_row["inputs"]["labels"] == {
        "nebius.o11y.systemd-logs-collection.enabled": "true",
        "nebius.o11y.systemd-logs-collection.units": "sshd.service;docker.service",
    }


def test_materialize_observability_infra_values_cleans_vm_journald_labels_when_disabled() -> None:
    payload = _base_payload(
        observability_enabled=False,
        mk8s_enabled=False,
        vm_enabled=True,
    )
    payload["infra"]["components"][1]["inputs"]["labels"] = {
        "example.com/keep": "true",
        "nebius.o11y.systemd-logs-collection.enabled": "true",
        "nebius.o11y.systemd-logs-collection.units": "sshd.service",
    }

    changed = materialize_observability_infra_values(payload)

    vm_row = payload["infra"]["components"][1]
    assert changed is True
    assert vm_row["inputs"]["labels"] == {"example.com/keep": "true"}


def test_materialize_observability_infra_values_sets_vm_standalone_collector_inputs() -> None:
    payload = _base_payload(
        observability_enabled=True,
        mk8s_enabled=False,
        vm_enabled=True,
    )
    payload["client_info"] = {
        "nebius": {
            "project_id": "project-example",
            "region_id": "eu-north1",
        }
    }
    payload["deploy"]["observability"]["vm"]["collector"]["enabled"] = True
    payload["infra"]["components"][1]["inputs"]["service_account_id"] = "serviceaccount-1"

    changed = materialize_observability_infra_values(payload)

    vm_inputs = payload["infra"]["components"][1]["inputs"]
    assert changed is True
    assert vm_inputs["observability_collector_enabled"] is True
    assert vm_inputs["observability_collector_region_id"] == "eu-north1"
    assert vm_inputs["observability_collector_package_name"] == "nebius-o11y-agent"
    assert vm_inputs["observability_collector_package_version"] == "0.2.130"
    assert vm_inputs["observability_collector_apt_repository"] == (
        "https://artifactory.nebius.dev/artifactory/nebius-o11y-agent"
    )
    assert vm_inputs["observability_collector_apt_key_url"] == (
        "https://artifactory.nebius.dev/artifactory/nebius-o11y-agent/key.gpg"
    )
    assert vm_inputs["observability_collector_apt_suite"] == "stable"
    assert vm_inputs["observability_collector_apt_component"] == "main"
    assert vm_inputs["observability_collector_apt_origin"] == "artifactory.nebius.dev"
    assert vm_inputs["observability_collector_prometheus_package_name"] == "prometheus"
    assert vm_inputs["observability_collector_iam_token_file"] == "/mnt/cloud-metadata/token"
    assert vm_inputs["observability_collector_logs_enabled"] is True
    assert vm_inputs["observability_collector_logs_systemd_units"] == []
    assert vm_inputs["observability_collector_metrics_enabled"] is True
    assert vm_inputs["observability_collector_metrics_export_port"] == 19090
    assert vm_inputs["observability_collector_prometheus_agent_port"] == 19091


def test_materialize_observability_infra_values_cleans_vm_standalone_collector_inputs_when_disabled() -> (
    None
):
    payload = _base_payload(
        observability_enabled=False,
        mk8s_enabled=False,
        vm_enabled=True,
    )
    payload["infra"]["components"][1]["inputs"].update(
        {
            "observability_collector_enabled": True,
            "observability_collector_region_id": "eu-north1",
            "observability_collector_package_name": "nebius-o11y-agent",
            "observability_collector_package_version": "0.2.130",
            "observability_collector_apt_repository": (
                "https://artifactory.nebius.dev/artifactory/nebius-o11y-agent"
            ),
            "observability_collector_apt_key_url": (
                "https://artifactory.nebius.dev/artifactory/nebius-o11y-agent/key.gpg"
            ),
            "observability_collector_apt_suite": "stable",
            "observability_collector_apt_component": "main",
            "observability_collector_apt_origin": "artifactory.nebius.dev",
            "observability_collector_prometheus_package_name": "prometheus",
            "observability_collector_iam_token_file": "/mnt/cloud-metadata/token",
            "observability_collector_logs_enabled": True,
            "observability_collector_logs_systemd_units": ["sshd.service"],
            "observability_collector_metrics_enabled": True,
            "observability_collector_metrics_export_port": 19090,
            "observability_collector_prometheus_agent_port": 19091,
        }
    )

    changed = materialize_observability_infra_values(payload)

    assert changed is True
    assert payload["infra"]["components"][1]["inputs"] == {}


def test_observability_status_summary_reports_vm_agent_and_gpu_metrics() -> None:
    payload = _base_payload(
        observability_enabled=True,
        vm_enabled=True,
        enabled_apps=("nebius-observability-agent", "nvidia-gpu-operator"),
    )
    payload["deploy"]["observability"]["vm"]["logs"]["enabled"] = True

    summary = observability_status_summary(payload)

    assert summary == {
        "enabled": True,
        "kubernetes_agent": True,
        "grafana": False,
        "vm_monitoring_agent": True,
        "vm_journald_logs": True,
        "vm_standalone_collector": False,
        "vm_standalone_metrics": False,
        "vm_standalone_logs": False,
        "service_provider_metric_buckets": ["compute", "nbs", "gpu"],
        "service_log_buckets": ["sp_mk8s_control_plane", "sp_mk8s_audit_logs", "sp_serial"],
        "gpu_dcgm_metric_source": "additional_target",
        "gpu_dcgm_node_policy": "managed_gpu_operator_dcgm_labels",
        "gpu_dcgm_live_readiness": "verify_live_nvidia_dcgm_exporter_endpoints_after_deploy",
    }


def test_observability_status_summary_reports_vm_monitoring_agent_without_project_switch() -> None:
    payload = _base_payload(
        observability_enabled=False,
        mk8s_enabled=False,
        vm_enabled=True,
    )

    summary = observability_status_summary(payload)

    assert summary["enabled"] is False
    assert summary["vm_monitoring_agent"] is True
    assert summary["vm_journald_logs"] is False
    assert summary["vm_standalone_collector"] is False
    assert summary["vm_standalone_metrics"] is False
    assert summary["vm_standalone_logs"] is False


def test_observability_endpoint_summary_renders_public_read_and_write_endpoints() -> None:
    payload = _base_payload(observability_enabled=True)
    payload["client_info"] = {
        "nebius": {
            "project_id": "project-example",
            "region_id": "eu-north1",
        }
    }

    summary = observability_endpoint_summary(payload)

    assert summary["configured"] is True
    assert summary["read"]["metrics_service_provider_read"] == (
        "https://read.monitoring.api.nebius.cloud/projects/"
        "project-example/service-provider/prometheus"
    )
    assert summary["read"]["metrics_user_read"] == (
        "https://read.monitoring.api.nebius.cloud/projects/project-example/prometheus"
    )
    assert summary["read"]["logs_loki_read"] == (
        "https://read.logging.api.nebius.cloud/projects/project-example"
    )
    assert summary["read"]["traces_tempo_read"] == (
        "https://read.tracing.api.nebius.cloud/projects/project-example/tempo"
    )
    assert summary["write"]["metrics_otlp_write"] == (
        "https://write.monitoring.eu-north1.nebius.cloud/projects/"
        "project-example/opentelemetry/v1/metrics"
    )
    assert summary["write"]["metrics_prometheus_remote_write"] == (
        "https://write.monitoring.eu-north1.nebius.cloud/projects/"
        "project-example/prometheus/api/v1/write"
    )
    assert summary["write"]["logs_otlp_write"] == ("https://write.logging.eu-north1.nebius.cloud")
    assert summary["write"]["logs_agent_grpc_write"] == (
        "dns:///write.logging.eu-north1.nebius.cloud:443"
    )
    assert summary["write"]["traces_otlp_grpc_write"] == (
        "dns:///write.tracing.eu-north1.nebius.cloud:443"
    )
    assert "Bearer <observability static token or IAM token>" in summary["auth"]["read"]


def test_observability_endpoint_summary_respects_disabled_kubernetes_signals() -> None:
    payload = _base_payload(observability_enabled=True)
    payload["client_info"] = {
        "nebius": {
            "project_id": "project-example",
            "region_id": "eu-north1",
        }
    }
    payload["deploy"]["targets"][0]["observability"]["kubernetes"]["logs"]["enabled"] = False
    payload["deploy"]["targets"][0]["observability"]["kubernetes"]["traces"]["enabled"] = False

    summary = observability_endpoint_summary(payload)

    assert summary["signals"]["logs"] is True
    assert summary["signals"]["service_logs"] is True
    assert summary["signals"]["metrics"] is True
    assert summary["signals"]["traces"] is False
    assert "metrics_service_provider_read" in summary["read"]
    assert "metrics_user_read" in summary["read"]
    assert "metrics_otlp_write" in summary["write"]
    assert "logs_loki_read" in summary["read"]
    assert "logs_agent_grpc_write" not in summary["write"]
    assert "traces_tempo_read" not in summary["read"]
    assert "traces_otlp_grpc_write" not in summary["write"]


def test_observability_endpoint_summary_vm_only_reports_service_metrics_read_path() -> None:
    payload = _base_payload(
        observability_enabled=False,
        mk8s_enabled=False,
        vm_enabled=True,
    )
    payload["client_info"] = {
        "nebius": {
            "project_id": "project-example",
            "region_id": "eu-north1",
        }
    }

    summary = observability_endpoint_summary(payload)

    assert summary["signals"]["vm_service_metrics"] is True
    assert summary["signals"]["kubernetes_metrics"] is False
    assert summary["signals"]["logs"] is True
    assert summary["signals"]["service_logs"] is True
    assert summary["signals"]["traces"] is False
    assert "metrics_service_provider_read" in summary["read"]
    assert "metrics_federate_read" in summary["read"]
    assert "metrics_user_read" not in summary["read"]
    assert summary["service_provider_metric_buckets"] == ["compute", "nbs"]
    assert summary["service_log_buckets"] == ["sp_serial"]
    assert (
        "platform-managed regional endpoints" in summary["write"]["metrics_platform_managed_write"]
    )


def test_observability_endpoint_summary_vm_logs_adds_logs_read_path_only() -> None:
    payload = _base_payload(
        observability_enabled=True,
        mk8s_enabled=False,
        vm_enabled=True,
    )
    payload["deploy"]["observability"]["vm"]["logs"]["enabled"] = True
    payload["client_info"] = {
        "nebius": {
            "project_id": "project-example",
            "region_id": "eu-north1",
        }
    }

    summary = observability_endpoint_summary(payload)

    assert summary["signals"]["vm_logs"] is True
    assert summary["signals"]["logs"] is True
    assert summary["read"]["logs_loki_read"] == (
        "https://read.logging.api.nebius.cloud/projects/project-example"
    )
    assert "logs_agent_grpc_write" not in summary["write"]
    assert "platform-managed Logging ingest path" in summary["write"]["logs_platform_managed_write"]


def test_observability_endpoint_summary_vm_standalone_collector_adds_user_write_paths() -> None:
    payload = _base_payload(
        observability_enabled=True,
        mk8s_enabled=False,
        vm_enabled=True,
    )
    payload["client_info"] = {
        "nebius": {
            "project_id": "project-example",
            "region_id": "eu-north1",
        }
    }
    payload["deploy"]["observability"]["vm"]["collector"]["enabled"] = True
    payload["infra"]["components"][1]["inputs"]["service_account_id"] = "serviceaccount-1"

    summary = observability_endpoint_summary(payload)

    assert summary["signals"]["vm_standalone_collector"] is True
    assert summary["signals"]["vm_standalone_metrics"] is True
    assert summary["signals"]["vm_standalone_logs"] is True
    assert summary["read"]["metrics_user_read"] == (
        "https://read.monitoring.api.nebius.cloud/projects/project-example/prometheus"
    )
    assert summary["write"]["metrics_prometheus_remote_write"] == (
        "https://write.monitoring.eu-north1.nebius.cloud/projects/"
        "project-example/prometheus/api/v1/write"
    )
    assert summary["write"]["logs_agent_grpc_write"] == (
        "dns:///write.logging.eu-north1.nebius.cloud:443"
    )


def test_observability_endpoint_summary_managed_service_buckets_follow_catalog() -> None:
    payload = _base_payload(
        observability_enabled=False,
        mk8s_enabled=False,
        vm_enabled=False,
    )
    payload["infra"]["components"].extend(
        [
            {
                "id": "object-storage",
                "instance_id": "object-storage",
                "enabled": True,
                "inputs": {},
            },
            {
                "id": "managed-postgresql",
                "instance_id": "managed-postgresql",
                "enabled": True,
                "inputs": {},
            },
        ]
    )
    payload["client_info"] = {
        "nebius": {
            "project_id": "project-example",
            "region_id": "eu-north1",
        }
    }

    summary = observability_endpoint_summary(payload)

    assert summary["signals"]["service_metrics"] is True
    assert summary["signals"]["service_logs"] is True
    assert summary["read"]["metrics_service_provider_read"] == (
        "https://read.monitoring.api.nebius.cloud/projects/"
        "project-example/service-provider/prometheus"
    )
    assert summary["read"]["logs_loki_read"] == (
        "https://read.logging.api.nebius.cloud/projects/project-example"
    )
    assert summary["service_provider_metric_buckets"] == ["sp_storage", "msp"]
    assert summary["service_log_buckets"] == ["sp_postgres"]


def test_observability_dependency_issues_require_vm_service_account_for_standalone_collector() -> (
    None
):
    payload = _base_payload(
        observability_enabled=True,
        mk8s_enabled=False,
        vm_enabled=True,
    )
    payload["deploy"]["observability"]["vm"]["collector"]["enabled"] = True
    payload["deploy"]["observability"]["vm"]["logs"]["enabled"] = False

    issues = observability_dependency_issues(payload)

    assert issues == [
        "VM standalone collector requires inputs.service_account_id on enabled vm component(s): vm"
    ]


def test_observability_dependency_issues_require_vm_public_ingest_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.observability._vm_observability_settings",
        lambda: InfraObservabilitySettings(
            mode="monitoring_agent",
            standalone_collector=VmStandaloneCollectorSettings(),
        ),
    )
    payload = _base_payload(
        observability_enabled=True,
        mk8s_enabled=False,
        vm_enabled=True,
    )
    payload["deploy"]["observability"]["vm"]["collector"]["enabled"] = True
    payload["deploy"]["observability"]["vm"]["logs"]["enabled"] = False
    payload["infra"]["components"][1]["inputs"]["service_account_id"] = "serviceaccount-1"

    issues = observability_dependency_issues(payload)

    assert issues == [
        "VM standalone collector requires catalog fields under "
        "components.infra.vm.cli.observability: public_ingest.package.name, "
        "public_ingest.package.version, public_ingest.package.apt_repository, "
        "public_ingest.package.apt_key_url, public_ingest.package.apt_suite, "
        "public_ingest.package.apt_component, public_ingest.package.apt_origin, "
        "public_ingest.prometheus.package_name"
    ]
