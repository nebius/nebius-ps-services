from __future__ import annotations

from typing import Any

from nebius_cxcli.cli import _resolve_apps_chart_dependencies
from nebius_cxcli.components import ComponentEntry


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
            origin="chart",
            dependency_match_names=("gateway-helm",),
        ),
        ComponentEntry(
            id="n8n",
            scope="apps",
            config_path="apps.workloads.n8n",
            description="n8n workload",
            enabled_path=("apps", "workloads", "n8n", "enabled"),
            source="https://8gears.github.io/n8n-helm-chart/n8n",
            origin="chart",
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
            origin="chart",
            dependency_match_names=("gateway-helm",),
        ),
        ComponentEntry(
            id="n8n",
            scope="apps",
            config_path="apps.workloads.n8n",
            description="n8n workload",
            enabled_path=("apps", "workloads", "n8n", "enabled"),
            source="https://8gears.github.io/n8n-helm-chart/n8n",
            origin="chart",
        ),
        ComponentEntry(
            id="cert-manager",
            scope="apps",
            config_path="apps.platform.cert_manager",
            description="cert-manager",
            enabled_path=("apps", "platform", "cert_manager", "enabled"),
            source="oci://quay.io/jetstack/charts/cert-manager",
            origin="chart",
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
