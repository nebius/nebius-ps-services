from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

import nebius_cxcli.grafana_dashboard_export as grafana_export
from nebius_cxcli.component_sources import reset_component_sources_cache
from nebius_cxcli.grafana_dashboard_export import (
    CatalogDatasource,
    ExportedDashboard,
    GrafanaAuth,
)


@pytest.fixture(autouse=True)
def _reset_sources_cache() -> Iterator[None]:
    reset_component_sources_cache()
    yield
    reset_component_sources_cache()


def _write_grafana_catalog(path: Path, *, gnet_folder: str = "") -> None:
    dashboard_defaults: dict[str, Any] = {}
    if gnet_folder:
        dashboard_defaults[gnet_folder] = {
            "service-dashboard": {
                "gnetId": 23425,
                "revision": 1,
                "uid": "service-dashboard",
                "datasource": "Nebius Services",
            }
        }
    sources = {
        "components": {
            "infra": {},
            "apps": {
                "grafana": {
                    "source": {
                        "portable": {
                            "repo": "https://example.invalid/grafana",
                            "chart": "grafana",
                            "version": "1.0.0",
                        }
                    },
                    "release": {"namespace": "observability", "name": "grafana"},
                    "defaults": {
                        "values.dashboardProviders": {
                            "dashboardproviders.yaml": {"apiVersion": 1, "providers": []}
                        },
                        "values.dashboards": dashboard_defaults,
                    },
                }
            },
        }
    }
    settings = {
        "observability": {
            "endpoints": {
                "read": {
                    "metrics_service_provider_read": {
                        "label": "Services metrics",
                        "template": "https://example.invalid/services/{project_id}",
                    },
                    "metrics_user_read": {
                        "label": "User metrics",
                        "template": "https://example.invalid/metrics/{project_id}",
                    },
                    "logs_loki_read": {
                        "label": "Logs",
                        "template": "https://example.invalid/logs/{project_id}",
                    },
                },
                "write": {},
            }
        },
        "components": {
            "apps": {
                "grafana": {
                    "cli": {
                        "datasources": {
                            "services": {
                                "name": "Nebius Services",
                                "uid": "nebius-service-metrics",
                                "type": "prometheus",
                                "read_endpoint": "metrics_service_provider_read",
                            },
                            "user-metrics": {
                                "name": "Nebius User Metrics",
                                "uid": "nebius-user-metrics",
                                "type": "prometheus",
                                "read_endpoint": "metrics_user_read",
                            },
                            "logs": {
                                "name": "Nebius Logs",
                                "uid": "nebius-logs",
                                "type": "loki",
                                "read_endpoint": "logs_loki_read",
                            },
                        }
                    }
                }
            }
        },
    }
    path.write_text(yaml.safe_dump(sources, sort_keys=False), encoding="utf-8")
    path.with_name("component_cli_settings.yaml").write_text(
        yaml.safe_dump(settings, sort_keys=False),
        encoding="utf-8",
    )


def test_dashboard_json_removes_runtime_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_json(
        _base_url: str,
        _path: str,
        _auth_candidates: list[GrafanaAuth],
    ) -> object:
        return {
            "dashboard": {
                "id": 1,
                "version": 42,
                "uid": "cluster-autoscaler",
                "title": "Cluster Autoscaler",
                "panels": [],
            }
        }

    monkeypatch.setattr(grafana_export, "grafana_get_json", fake_get_json)

    dashboard = grafana_export.dashboard_json(
        "https://grafana.example/",
        [GrafanaAuth(kind="bearer", value="token", source="test")],
        dashboard_uid="cluster-autoscaler",
    )

    assert dashboard["uid"] == "cluster-autoscaler"
    assert "id" not in dashboard
    assert "version" not in dashboard


def test_dashboard_json_from_file_accepts_api_response_payload(tmp_path: Path) -> None:
    dashboard_file = tmp_path / "dashboard.json"
    dashboard_file.write_text(
        json.dumps(
            {
                "dashboard": {
                    "id": 12,
                    "version": 7,
                    "uid": "local-dashboard",
                    "title": "Local Dashboard",
                    "panels": [],
                },
                "meta": {"folderTitle": "Imported"},
            }
        ),
        encoding="utf-8",
    )

    dashboard = grafana_export.dashboard_json_from_file(dashboard_file)

    assert dashboard == {
        "uid": "local-dashboard",
        "title": "Local Dashboard",
        "panels": [],
    }


def test_dashboard_json_from_file_rejects_invalid_payloads(tmp_path: Path) -> None:
    missing_uid = tmp_path / "missing-uid.json"
    missing_uid.write_text(json.dumps({"title": "Missing UID"}), encoding="utf-8")
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid JSON"):
        grafana_export.dashboard_json_from_file(invalid_json)
    with pytest.raises(grafana_export.GrafanaApiError, match="top-level uid"):
        grafana_export.dashboard_json_from_file(missing_uid)
    with pytest.raises(RuntimeError, match="not found"):
        grafana_export.dashboard_json_from_file(tmp_path / "does-not-exist.json")


def test_bearer_auth_candidates_follow_documented_order() -> None:
    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="cli-token\n", stderr="")

    candidates = grafana_export.bearer_auth_candidates(
        token_env="CUSTOM_GRAFANA_TOKEN",
        env={
            "GRAFANA_TOKEN": "grafana-token",
            "NEBIUS_IAM_TOKEN": "iam-token",
            "CUSTOM_GRAFANA_TOKEN": "custom-token",
        },
        run=fake_run,
    )

    assert [item.source for item in candidates] == [
        "GRAFANA_TOKEN",
        "NEBIUS_IAM_TOKEN",
        "nebius iam get-access-token",
        "CUSTOM_GRAFANA_TOKEN",
    ]


def test_bearer_auth_candidates_warn_when_cli_token_lookup_fails() -> None:
    warnings: list[str] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            1,
            args,
            output="",
            stderr="not logged in",
        )

    candidates = grafana_export.bearer_auth_candidates(
        env={},
        run=fake_run,
        on_warning=warnings.append,
    )

    assert candidates == []
    assert warnings == [
        "Unable to read a Nebius IAM token with "
        "`nebius iam get-access-token`: not logged in."
    ]


def test_list_folders_sorts_by_title_then_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_json(
        _base_url: str,
        _path: str,
        _auth_candidates: list[GrafanaAuth],
    ) -> object:
        return [
            {"type": "dash-folder", "uid": "gamma", "title": "Gamma"},
            {"type": "dash-folder", "uid": "alpha-b", "title": "alpha"},
            {"type": "dash-folder", "uid": "alpha-a", "title": "alpha"},
            {"type": "dash-db", "uid": "ignored", "title": "Ignored"},
        ]

    monkeypatch.setattr(grafana_export, "grafana_get_json", fake_get_json)

    folders = grafana_export.list_folders(
        "https://grafana.example/",
        [GrafanaAuth(kind="bearer", value="token", source="test")],
    )

    assert [(folder.title, folder.uid) for folder in folders] == [
        ("alpha", "alpha-a"),
        ("alpha", "alpha-b"),
        ("Gamma", "gamma"),
    ]


def test_list_dashboards_sorts_by_title_then_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_json(
        _base_url: str,
        _path: str,
        _auth_candidates: list[GrafanaAuth],
    ) -> object:
        return [
            {"type": "dash-db", "uid": "gamma", "title": "Gamma"},
            {"type": "dash-db", "uid": "alpha-b", "title": "alpha"},
            {"type": "dash-db", "uid": "alpha-a", "title": "alpha"},
            {"type": "dash-folder", "uid": "ignored", "title": "Ignored"},
        ]

    monkeypatch.setattr(grafana_export, "grafana_get_json", fake_get_json)

    dashboards = grafana_export.list_dashboards(
        "https://grafana.example/",
        [GrafanaAuth(kind="bearer", value="token", source="test")],
        folder_uid="folder",
        folder_title="Folder",
    )

    assert [(dashboard.title, dashboard.uid) for dashboard in dashboards] == [
        ("alpha", "alpha-a"),
        ("alpha", "alpha-b"),
        ("Gamma", "gamma"),
    ]


def test_datasource_selection_and_rewrite_uses_catalog_uid_and_type() -> None:
    dashboard = {
        "uid": "logs",
        "panels": [
            {"datasource": {"type": "grafana", "uid": "-- Grafana --"}},
            {"datasource": {"type": "loki", "uid": "source-loki"}},
            {"targets": [{"datasource": {"type": "loki", "uid": "source-loki"}}]},
        ],
    }
    datasources = [
        CatalogDatasource(
            name="Nebius User Metrics",
            uid="nebius-user-metrics",
            datasource_type="prometheus",
        ),
        CatalogDatasource(name="Nebius Logs", uid="nebius-logs", datasource_type="loki"),
    ]

    selected = grafana_export.select_catalog_datasource(dashboard, datasources)
    rewritten = cast(
        dict[str, Any],
        grafana_export.rewrite_dashboard_datasources(dashboard, selected),
    )

    assert selected.name == "Nebius Logs"
    assert rewritten["panels"][0]["datasource"] == {"type": "grafana", "uid": "-- Grafana --"}
    assert rewritten["panels"][1]["datasource"] == {"type": "loki", "uid": "nebius-logs"}
    assert rewritten["panels"][2]["targets"][0]["datasource"] == {
        "type": "loki",
        "uid": "nebius-logs",
    }


def test_datasource_selection_rejects_mixed_types_for_attach() -> None:
    dashboard = {
        "uid": "mixed",
        "panels": [
            {"datasource": {"type": "prometheus", "uid": "metrics"}},
            {"datasource": {"type": "loki", "uid": "logs"}},
        ],
    }
    datasources = [
        CatalogDatasource(
            name="Nebius User Metrics",
            uid="nebius-user-metrics",
            datasource_type="prometheus",
        ),
        CatalogDatasource(name="Nebius Logs", uid="nebius-logs", datasource_type="loki"),
    ]

    with pytest.raises(RuntimeError, match="mixed datasource types"):
        grafana_export.select_catalog_datasource(
            dashboard,
            datasources,
            requested="Nebius User Metrics",
        )


def test_write_dashboard_file_requires_overwrite_for_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "dashboards" / "mk8s" / "cluster.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="already exists"):
        grafana_export.write_dashboard_file(
            target,
            {"uid": "cluster", "title": "Cluster"},
            overwrite=False,
        )

    grafana_export.write_dashboard_file(
        target,
        {"uid": "cluster", "title": "Cluster"},
        overwrite=True,
    )

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "uid": "cluster",
        "title": "Cluster",
    }
    assert target.stat().st_mode & 0o777 == 0o600


def test_attach_dashboards_to_catalog_creates_provider_and_dashboard_entry(tmp_path: Path) -> None:
    sources_path = tmp_path / "component_sources.yaml"
    _write_grafana_catalog(sources_path)
    dashboard_path = tmp_path / "dashboards" / "mk8s" / "cluster-autoscaler.json"
    dashboard_path.parent.mkdir(parents=True)
    dashboard_path.write_text(
        json.dumps({"uid": "cluster-autoscaler", "title": "Cluster Autoscaler", "panels": []}),
        encoding="utf-8",
    )

    grafana_export.attach_dashboards_to_catalog(
        sources_path,
        grafana_component_id="grafana",
        exports=[
            ExportedDashboard(
                uid="cluster-autoscaler",
                title="Cluster Autoscaler",
                folder_uid="folder-uid",
                folder_title="mk8s",
                catalog_folder="mk8s",
                dashboard_key="cluster-autoscaler",
                datasource_name="Nebius User Metrics",
                path=dashboard_path,
            )
        ],
        overwrite=False,
    )

    text = sources_path.read_text(encoding="utf-8")
    assert "name: mk8s" in text
    assert "cluster-autoscaler:" in text
    assert "datasource: Nebius User Metrics" in text
    assert "json_file: ./dashboards/mk8s/cluster-autoscaler.json" in text


def test_attach_dashboards_to_catalog_refuses_gnet_provider_mix(tmp_path: Path) -> None:
    sources_path = tmp_path / "component_sources.yaml"
    _write_grafana_catalog(sources_path, gnet_folder="nebius")
    dashboard_path = tmp_path / "dashboards" / "nebius" / "custom.json"
    dashboard_path.parent.mkdir(parents=True)
    dashboard_path.write_text(
        json.dumps({"uid": "custom", "title": "Custom", "panels": []}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="already contains Grafana.com"):
        grafana_export.attach_dashboards_to_catalog(
            sources_path,
            grafana_component_id="grafana",
            exports=[
                ExportedDashboard(
                    uid="custom",
                    title="Custom",
                    folder_uid="nebius",
                    folder_title="Nebius",
                    catalog_folder="nebius",
                    dashboard_key="custom",
                    datasource_name="Nebius User Metrics",
                    path=dashboard_path,
                )
            ],
            overwrite=False,
        )


def test_attach_dashboards_to_catalog_restores_catalog_when_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources_path = tmp_path / "component_sources.yaml"
    _write_grafana_catalog(sources_path)
    original = sources_path.read_text(encoding="utf-8")
    dashboard_path = tmp_path / "dashboards" / "mk8s" / "broken.json"
    dashboard_path.parent.mkdir(parents=True)
    dashboard_path.write_text(
        json.dumps({"uid": "broken", "title": "Broken", "panels": []}),
        encoding="utf-8",
    )

    def fail_load(*, explicit: Path | None = None) -> object:
        raise ValueError(f"invalid catalog: {explicit}")

    monkeypatch.setattr(grafana_export, "load_component_sources", fail_load)

    with pytest.raises(ValueError, match="invalid catalog"):
        grafana_export.attach_dashboards_to_catalog(
            sources_path,
            grafana_component_id="grafana",
            exports=[
                ExportedDashboard(
                    uid="broken",
                    title="Broken",
                    folder_uid="mk8s",
                    folder_title="mk8s",
                    catalog_folder="mk8s",
                    dashboard_key="broken",
                    datasource_name="Nebius User Metrics",
                    path=dashboard_path,
                )
            ],
            overwrite=False,
        )

    assert sources_path.read_text(encoding="utf-8") == original
