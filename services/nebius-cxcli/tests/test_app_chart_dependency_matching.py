from __future__ import annotations

from typing import Any

import pytest

from nebius_cxcli.cli import _resolve_apps_chart_dependencies
from nebius_cxcli.component_sources import SourceProfile
from nebius_cxcli.components import ComponentEntry, component_entries, soperator_install_entry


@pytest.mark.parametrize(
    "app_targets,soperator_enabled,derived_ref,expected_ordering",
    [
        (["slurm"], True, None, False),
        (["slurm"], True, "slurm", False),
        (["slurm", "ordinary"], True, None, True),
        (["ordinary"], True, None, True),
        ([""], True, None, True),
        (["slurm"], False, None, True),
        (["slurm"], True, "ordinary", True),
        ([], True, None, True),
    ],
)
def test_apps_dependency_resolution_uses_release_install_after(
    monkeypatch, app_targets, soperator_enabled, derived_ref, expected_ordering
) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.cli._helm_chart_metadata",
        lambda **kwargs: (kwargs["chart_name_or_ref"], set(), None),
    )

    app_entries = (
        ComponentEntry(
            id="cert-manager",
            scope="apps",
            config_path="apps.platform.cert_manager",
            description="cert-manager",
        ),
        ComponentEntry(
            id="external-secrets",
            scope="apps",
            config_path="apps.platform.external_secrets",
            description="External Secrets Operator",
        ),
        ComponentEntry(
            id="sample-app",
            scope="apps",
            config_path="apps.platform.sample_app",
            description="Sample app",
            default_release_install_after=("cert-manager", "external-secrets"),
        ),
    )

    rows = [
        {"id": "soperator", "instance_id": "slurm", "enabled": soperator_enabled},
        *[{"id": "sample-app", "instance_id": target, "enabled": True} for target in app_targets],
    ]
    if derived_ref is not None:
        rows[1]["target_ref"] = derived_ref
    payload = {
        "infra": {
            "components": [
                {"id": "mk8s", "instance_id": target, "enabled": True, "inputs": {}}
                for target in ("slurm", "ordinary")
            ]
        },
        "apps": {"charts": rows},
    }
    selected, adjustments, warnings = _resolve_apps_chart_dependencies(
        payload=payload,
        selected_apps={"sample-app"},
        app_entries=app_entries,
        cache={},
        collect_warnings=True,
    )

    assert selected == (
        {"cert-manager", "external-secrets", "sample-app"}
        if expected_ordering
        else {"sample-app", "external-secrets"}
    )
    assert {(item.dependency_app_id, item.dependency_kind) for item in adjustments} == (
        {
            ("cert-manager", "install_after"),
            ("external-secrets", "install_after"),
        }
        if expected_ordering
        else {("external-secrets", "install_after")}
    )
    assert warnings == ()


def test_soperator_family_is_absent_from_generic_app_catalog() -> None:
    app_ids = {entry.id for entry in component_entries("apps", source_profile=SourceProfile.LOCAL)}

    assert "soperator" not in app_ids
    assert {
        "soperator-activechecks",
        "soperator-backup-config",
        "soperator-checks",
        "soperator-dcgm-exporter",
        "soperator-notifier",
        "k8up",
    }.isdisjoint(app_ids)
    assert soperator_install_entry("4.1.7").id == "soperator"


def test_apps_dependency_resolution_uses_source_chart_name_fallback(monkeypatch) -> None:
    def _fake_chart_metadata(
        *,
        chart_name_or_ref: str,
        chart_repo: str,
        chart_version: str,
        cache: dict[tuple[str, str, str], tuple[str | None, set[str], str | None]],
    ) -> tuple[str | None, set[str], str | None]:
        _ = (chart_repo, chart_version, cache)
        if chart_name_or_ref == "n8n":
            return "n8n", {"gateway-helm"}, None
        if chart_name_or_ref == "gateway-helm":
            return "gateway-helm", set(), None
        return None, set(), "unknown chart"

    monkeypatch.setattr("nebius_cxcli.cli._helm_chart_metadata", _fake_chart_metadata)

    payload: dict[str, Any] = {
        "apps": {
            "charts": [
                {
                    "id": "gateway-helm",
                    "enabled": False,
                    "group": "platform",
                    "repo": "oci://docker.io/envoyproxy/gateway-helm",
                    "version": "1.4.2",
                    "namespace": "envoy-gateway-system",
                    "release-name": "envoy-gateway",
                    "values": {},
                },
                {
                    "id": "n8n",
                    "enabled": True,
                    "group": "workloads",
                    "repo": "https://8gears.github.io/n8n-helm-chart/",
                    "version": "1.0.6",
                    "namespace": "n8n",
                    "release-name": "n8n",
                    "values": {},
                },
            ],
        }
    }
    app_entries = (
        ComponentEntry(
            id="gateway-helm",
            scope="apps",
            config_path="apps.platform.gateway_helm",
            description="Envoy Gateway control plane",
            enabled_path=("apps", "platform", "gateway_helm", "enabled"),
            source="oci://docker.io/envoyproxy/gateway-helm",
            dependency_match_names=("gateway-helm",),
        ),
        ComponentEntry(
            id="n8n",
            scope="apps",
            config_path="apps.workloads.n8n",
            description="n8n workload",
            enabled_path=("apps", "workloads", "n8n", "enabled"),
            source="https://8gears.github.io/n8n-helm-chart/n8n",
        ),
    )

    selected, adjustments, warnings = _resolve_apps_chart_dependencies(
        payload=payload,
        selected_apps={"n8n"},
        app_entries=app_entries,
        cache={},
        collect_warnings=True,
    )
    assert "gateway-helm" in selected
    assert any(item.dependency_app_id == "gateway-helm" for item in adjustments)
    assert warnings == ()


def test_apps_dependency_resolution_uses_catalog_chart_name_for_oci_repo(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_chart_metadata(
        *,
        chart_name_or_ref: str,
        chart_repo: str,
        chart_version: str,
        cache: dict[tuple[str, str, str], tuple[str | None, set[str], str | None]],
    ) -> tuple[str | None, set[str], str | None]:
        _ = (chart_repo, chart_version, cache)
        calls.append(chart_name_or_ref)
        if chart_name_or_ref == "gpu-operator":
            return "gpu-operator", {"network-operator"}, None
        if chart_name_or_ref == "network-operator":
            return "network-operator", set(), None
        return None, set(), "unknown chart"

    monkeypatch.setattr("nebius_cxcli.cli._helm_chart_metadata", _fake_chart_metadata)

    payload: dict[str, Any] = {
        "apps": {
            "charts": [
                {
                    "id": "nvidia-network-operator",
                    "enabled": False,
                    "group": "platform",
                    "repo": "oci://cr.eu-north1.nebius.cloud/marketplace/nebius/nvidia-network-operator/chart/network-operator",
                    "version": "25.7.0",
                    "namespace": "nvidia-network-operator",
                    "release-name": "network-operator",
                    "values": {},
                },
                {
                    "id": "nvidia-gpu-operator",
                    "enabled": True,
                    "group": "platform",
                    "repo": "oci://cr.eu-north1.nebius.cloud/marketplace/nebius/nvidia-gpu-operator/chart/gpu-operator",
                    "version": "v25.10.0",
                    "namespace": "nvidia-gpu-operator",
                    "release-name": "gpu-operator",
                    "values": {},
                },
            ],
        }
    }
    app_entries = (
        ComponentEntry(
            id="nvidia-network-operator",
            scope="apps",
            config_path="apps.platform.nvidia_network_operator",
            description="NVIDIA Network Operator",
            enabled_path=("apps", "platform", "nvidia_network_operator", "enabled"),
            source="oci://cr.eu-north1.nebius.cloud/marketplace/nebius/nvidia-network-operator/chart/network-operator",
            chart_name="network-operator",
            dependency_match_names=("network-operator",),
        ),
        ComponentEntry(
            id="nvidia-gpu-operator",
            scope="apps",
            config_path="apps.platform.nvidia_gpu_operator",
            description="NVIDIA GPU Operator",
            enabled_path=("apps", "platform", "nvidia_gpu_operator", "enabled"),
            source="oci://cr.eu-north1.nebius.cloud/marketplace/nebius/nvidia-gpu-operator/chart/gpu-operator",
            chart_name="gpu-operator",
        ),
    )

    selected, adjustments, warnings = _resolve_apps_chart_dependencies(
        payload=payload,
        selected_apps={"nvidia-gpu-operator"},
        app_entries=app_entries,
        cache={},
        collect_warnings=True,
    )
    assert "nvidia-network-operator" in selected
    assert any(item.dependency_app_id == "nvidia-network-operator" for item in adjustments)
    assert warnings == ()
    assert "gpu-operator" in calls
    assert "network-operator" in calls
    assert "nvidia-gpu-operator" not in calls


def test_apps_dependency_resolution_skips_live_chart_lookup_for_unrelated_apps(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def _fake_chart_metadata(
        *,
        chart_name_or_ref: str,
        chart_repo: str,
        chart_version: str,
        cache: dict[tuple[str, str, str], tuple[str | None, set[str], str | None]],
    ) -> tuple[str | None, set[str], str | None]:
        _ = (chart_repo, chart_version, cache)
        calls.append(chart_name_or_ref)
        if chart_name_or_ref == "n8n":
            return "n8n", {"gateway-helm"}, None
        if chart_name_or_ref == "gateway-helm":
            return "gateway-helm", set(), None
        if chart_name_or_ref == "cert-manager":
            return "cert-manager", set(), None
        return None, set(), "unknown chart"

    monkeypatch.setattr("nebius_cxcli.cli._helm_chart_metadata", _fake_chart_metadata)

    payload: dict[str, Any] = {
        "apps": {
            "charts": [
                {
                    "id": "gateway-helm",
                    "enabled": False,
                    "group": "platform",
                    "repo": "oci://docker.io/envoyproxy",
                    "version": "1.4.2",
                    "namespace": "envoy-gateway-system",
                    "release-name": "envoy-gateway",
                    "values": {},
                },
                {
                    "id": "n8n",
                    "enabled": True,
                    "group": "workloads",
                    "repo": "https://8gears.github.io/n8n-helm-chart/",
                    "version": "1.0.6",
                    "namespace": "n8n",
                    "release-name": "n8n",
                    "values": {},
                },
                {
                    "id": "cert-manager",
                    "enabled": False,
                    "group": "platform",
                    "repo": "oci://quay.io/jetstack/charts",
                    "version": "v1.19.2",
                    "namespace": "cert-manager",
                    "release-name": "cert-manager",
                    "values": {},
                },
            ],
        }
    }
    app_entries = (
        ComponentEntry(
            id="gateway-helm",
            scope="apps",
            config_path="apps.platform.gateway_helm",
            description="Envoy Gateway control plane",
            enabled_path=("apps", "platform", "gateway_helm", "enabled"),
            source="oci://docker.io/envoyproxy/gateway-helm",
            dependency_match_names=("gateway-helm",),
        ),
        ComponentEntry(
            id="n8n",
            scope="apps",
            config_path="apps.workloads.n8n",
            description="n8n workload",
            enabled_path=("apps", "workloads", "n8n", "enabled"),
            source="https://8gears.github.io/n8n-helm-chart/n8n",
        ),
        ComponentEntry(
            id="cert-manager",
            scope="apps",
            config_path="apps.platform.cert_manager",
            description="cert-manager",
            enabled_path=("apps", "platform", "cert_manager", "enabled"),
            source="oci://quay.io/jetstack/charts/cert-manager",
        ),
    )

    selected, adjustments, warnings = _resolve_apps_chart_dependencies(
        payload=payload,
        selected_apps={"n8n"},
        app_entries=app_entries,
        cache={},
        collect_warnings=True,
    )
    assert "gateway-helm" in selected
    assert any(item.dependency_app_id == "gateway-helm" for item in adjustments)
    assert warnings == ()
    assert "cert-manager" not in calls
