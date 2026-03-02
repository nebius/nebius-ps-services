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
        if chart_name_or_ref == "gw-renamed":
            return "gateway-helm", set(), None
        return None, set(), "unknown chart"

    monkeypatch.setattr("nebius_cxcli.cli._helm_chart_metadata", _fake_chart_metadata)

    payload: dict[str, Any] = {
        "apps": {
            "releases": [
                {
                    "id": "envoy-gateway",
                    "enabled": False,
                    "section": "platform",
                    "values": {
                        "chart": {
                            "repo": "https://envoyproxy.github.io/gateway-helm",
                            "name": "gw-renamed",
                            "version": "1.4.2",
                        }
                    },
                },
                {
                    "id": "n8n",
                    "enabled": True,
                    "section": "workloads",
                    "values": {
                        "chart": {
                            "repo": "https://8gears.github.io/n8n-helm-chart/",
                            "name": "n8n",
                            "version": "1.0.6",
                        }
                    },
                },
            ],
        }
    }
    app_entries = (
        ComponentEntry(
            id="envoy-gateway",
            scope="apps",
            config_path="apps.platform.envoy_gateway",
            description="Envoy Gateway control plane",
            enabled_path=("apps", "platform", "envoy_gateway", "enabled"),
            source="https://envoyproxy.github.io/gateway-helm/gateway-helm",
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
    assert "envoy-gateway" in selected
    assert any(item.dependency_app_id == "envoy-gateway" for item in adjustments)
    assert warnings == ()
