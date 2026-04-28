from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

import nebius_cxcli.grafana_runtime as grafana_runtime
from nebius_cxcli.component_sources import (
    CliSettings,
    ComponentSources,
    GrafanaAdminSecretSpec,
    GrafanaCliSettings,
    GrafanaDatasourceSpec,
    GrafanaExploreQuerySpec,
    GrafanaReadTokenSecretSpec,
    GrafanaReportDashboardBinding,
    HelmChartSource,
    InfraObservabilitySettings,
    ObservabilityGrafanaSettings,
    TFModuleSource,
)


def _grafana_payload() -> dict[str, object]:
    return {
        "apps": {
            "charts": [
                {
                    "id": "grafana",
                    "instance_id": "cluster2",
                    "enabled": True,
                    "target_ref": "cluster2",
                    "namespace": "observability",
                    "release-name": "grafana",
                    "values": {
                        "admin": {
                            "existingSecret": "nebius-cxcli-grafana-admin",
                            "userKey": "admin-user",
                            "passwordKey": "admin-password",
                        },
                        "envValueFrom": {
                            "NEBIUS_OBSERVABILITY_STATIC_TOKEN": {
                                "secretKeyRef": {
                                    "name": "nebius-cxcli-grafana-observability-read",
                                    "key": "token",
                                }
                            }
                        },
                    },
                }
            ]
        }
    }


def _parse_query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_grafana_release_specs_uses_catalog_component_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = ComponentSources(
        cli=CliSettings(),
        shared={},
        tf_modules=(
            TFModuleSource(
                module="mk8s",
                source="",
                portable_source="",
                observability=InfraObservabilitySettings(
                    grafana=ObservabilityGrafanaSettings(chart_component_id="dashboards")
                ),
            ),
        ),
        helm_charts=(
            HelmChartSource(
                name="dashboards",
                namespace="observability",
                release_name="dashboards",
                grafana=GrafanaCliSettings(
                    admin_secret=GrafanaAdminSecretSpec(
                        secret_name="dashboards-admin",
                        user="admin",
                        user_key="admin-user",
                        password_key="admin-password",
                    ),
                    read_token=GrafanaReadTokenSecretSpec(
                        env="DASHBOARDS_TOKEN",
                        secret_name="dashboards-read",
                        key="token",
                    ),
                ),
            ),
        ),
    )
    monkeypatch.setattr(grafana_runtime, "load_component_sources", lambda: sources)

    specs = grafana_runtime.grafana_release_specs(
        {
            "apps": {
                "charts": [
                    {
                        "id": "dashboards",
                        "enabled": True,
                        "namespace": "dashboards-ns",
                        "release-name": "dashboards-release",
                        "values": {
                            "admin": {"existingSecret": "runtime-admin"},
                            "envValueFrom": {
                                "DASHBOARDS_TOKEN": {
                                    "secretKeyRef": {
                                        "name": "runtime-read",
                                        "key": "runtime-token",
                                    }
                                }
                            },
                        },
                    }
                ]
            }
        }
    )

    assert len(specs) == 1
    assert specs[0].namespace == "dashboards-ns"
    assert specs[0].release_name == "dashboards-release"
    assert specs[0].admin_secret_name == "runtime-admin"
    assert specs[0].token_secret_name == "runtime-read"
    assert specs[0].token_key == "runtime-token"


def test_explore_url_uses_grafana_panes_schema() -> None:
    url = grafana_runtime._explore_url(
        "http://203.0.113.10/",
        datasource_uid="nebius-service-metrics",
        datasource_type="prometheus",
        org_id=1,
        query='count({__name__=~".+"})',
    )

    params = _parse_query(url)
    panes = json.loads(params["panes"][0])
    query = panes["cxcli"]["queries"][0]

    assert url.startswith("http://203.0.113.10/explore?")
    assert "left=" not in url
    assert params["schemaVersion"] == ["1"]
    assert params["orgId"] == ["1"]
    assert panes["cxcli"]["datasource"] == "nebius-service-metrics"
    assert query["datasource"] == {
        "uid": "nebius-service-metrics",
        "type": "prometheus",
    }
    assert query["expr"] == 'count({__name__=~".+"})'


def test_create_grafana_short_url_uses_public_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, object] = {}

    def fake_post(
        base_url: str,
        path: str,
        *,
        username: str,
        password: str,
    ) -> dict[str, str]:
        recorded.update(
            {
                "base_url": base_url,
                "path": path,
                "username": username,
                "password": password,
            }
        )
        return {"uid": "abc123", "url": "http://localhost:3000/goto/abc123?orgId=1"}

    monkeypatch.setattr(grafana_runtime, "_post_grafana_short_url", fake_post)
    long_url = grafana_runtime._explore_url(
        "http://203.0.113.10/",
        datasource_uid="nebius-service-metrics",
        datasource_type="prometheus",
        org_id=1,
        query='count({__name__=~".+"})',
    )

    short_url = grafana_runtime._create_grafana_short_url(
        "http://203.0.113.10/",
        long_url,
        org_id=1,
        username="admin",
        password="secret",
    )

    assert short_url == "http://203.0.113.10/goto/abc123?orgId=1"
    assert recorded["base_url"] == "http://203.0.113.10/"
    assert recorded["path"].startswith("explore?")
    assert "panes=" in str(recorded["path"])


def test_shorten_grafana_urls_falls_back_to_long_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = grafana_runtime.GrafanaReleaseSpec(
        target_ref="cluster2",
        namespace="observability",
        release_name="grafana",
        service_name="grafana",
        admin_secret_name="nebius-cxcli-grafana-admin",
        admin_user="admin",
        admin_user_key="admin-user",
        admin_password_key="admin-password",
        token_secret_name="nebius-cxcli-grafana-observability-read",
        token_key="token",
    )
    urls = {"metrics_url": "http://203.0.113.10/explore?schemaVersion=1&panes=abc&orgId=1"}
    monkeypatch.setattr(
        grafana_runtime,
        "_grafana_admin_credentials",
        lambda _spec, *, extra_env: ("admin", "secret"),
    )

    def fail_create(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("not ready")

    monkeypatch.setattr(grafana_runtime, "_create_grafana_short_url", fail_create)

    assert (
        grafana_runtime._shorten_grafana_urls(
            "http://203.0.113.10/",
            urls,
            spec,
            extra_env=None,
            org_id=1,
        )
        == urls
    )


def test_ensure_grafana_runtime_secrets_refreshes_rejected_read_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = GrafanaCliSettings(
        admin_secret=GrafanaAdminSecretSpec(
            secret_name="nebius-cxcli-grafana-admin",
            user="admin",
            user_key="admin-user",
            password_key="admin-password",
        ),
        read_token=GrafanaReadTokenSecretSpec(
            env="NEBIUS_OBSERVABILITY_STATIC_TOKEN",
            secret_name="nebius-cxcli-grafana-observability-read",
            key="token",
        ),
    )
    applied: list[dict[str, object]] = []
    emitted: list[str] = []

    monkeypatch.setattr(grafana_runtime, "_grafana_cli_settings", lambda: settings)
    monkeypatch.setattr(grafana_runtime, "_ensure_namespace", lambda _namespace, *, extra_env: None)
    monkeypatch.setattr(
        grafana_runtime,
        "_secret_has_keys",
        lambda *, namespace, name, keys, extra_env: True,
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_secret_data_values",
        lambda *, namespace, name, keys, extra_env: {"token": "old-token"},
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_observability_read_token_status",
        lambda _payload, token: token != "old-token",
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_issue_read_token",
        lambda _payload, *, target_ref: "new-token",
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_apply_secret",
        lambda *, namespace, name, string_data, extra_env: applied.append(
            {
                "namespace": namespace,
                "name": name,
                "string_data": dict(string_data),
            }
        ),
    )

    grafana_runtime.ensure_grafana_runtime_secrets(
        _grafana_payload(),
        extra_env={},
        target_ref="cluster2",
        emit=emitted.append,
    )

    assert applied == [
        {
            "namespace": "observability",
            "name": "nebius-cxcli-grafana-observability-read",
            "string_data": {"token": "new-token"},
        }
    ]
    assert emitted == [
        "Refreshed Grafana Observability read-token secret "
        "`nebius-cxcli-grafana-observability-read`."
    ]


def test_grafana_dashboard_url_rewrites_to_public_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_get(
        base_url: str,
        path: str,
        *,
        username: str,
        password: str,
    ) -> object:
        calls.append(
            {
                "base_url": base_url,
                "path": path,
                "username": username,
                "password": password,
            }
        )
        if path == "api/search?type=dash-db&limit=5000":
            return [
                {
                    "uid": "k8s",
                    "title": "Kubernetes cluster monitoring (via Prometheus)",
                    "url": "/d/k8s/kubernetes-cluster-monitoring-via-prometheus",
                }
            ]
        if path == "api/dashboards/uid/k8s":
            return {"dashboard": {"gnetId": 315}}
        raise AssertionError(f"unexpected Grafana API path: {path}")

    monkeypatch.setattr(grafana_runtime, "_get_grafana_json", fake_get)

    url = grafana_runtime._grafana_dashboard_url(
        "http://203.0.113.20/",
        "kubernetes-cluster-monitoring",
        315,
        username="admin",
        password="secret",
    )

    assert url == "http://203.0.113.20/d/k8s/kubernetes-cluster-monitoring-via-prometheus"
    assert calls == [
        {
            "base_url": "http://203.0.113.20/",
            "path": "api/search?type=dash-db&limit=5000",
            "username": "admin",
            "password": "secret",
        },
        {
            "base_url": "http://203.0.113.20/",
            "path": "api/dashboards/uid/k8s",
            "username": "admin",
            "password": "secret",
        },
    ]


def test_grafana_explore_urls_use_bound_datasources() -> None:
    bindings = {
        "metrics": GrafanaReportDashboardBinding(
            signal="metrics",
            folder="nebius",
            dashboard="kubernetes-cluster-monitoring",
            gnet_id=315,
            datasource="Nebius User Metrics",
        )
    }
    datasources = {
        "Nebius User Metrics": GrafanaDatasourceSpec(
            key="user-metrics",
            name="Nebius User Metrics",
            uid="nebius-user-metrics",
            datasource_type="prometheus",
            read_endpoint="metrics_user_read",
        )
    }

    urls = grafana_runtime._grafana_explore_urls(
        "http://203.0.113.20/",
        bindings,
        datasources,
        explore_queries={"metrics": 'count({__name__=~".+"})'},
        org_id=1,
    )

    panes = json.loads(_parse_query(urls["metrics_url"])["panes"][0])
    assert panes["cxcli"]["datasource"] == "nebius-user-metrics"


def test_collect_grafana_runtime_status_records_kube_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "nebius-cluster2-mk8scluster-123-external",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_grafana_base_url",
        lambda _spec, *, extra_env: "http://203.0.113.20/",
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_ensure_grafana_public_root_url",
        lambda _spec, _base_url, *, extra_env: True,
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_grafana_report_dashboard_bindings",
        lambda: {
            "metrics": GrafanaReportDashboardBinding(
                signal="metrics",
                folder="nebius",
                dashboard="kubernetes-cluster-monitoring",
                gnet_id=315,
                datasource="Nebius User Metrics",
            ),
            "logs": GrafanaReportDashboardBinding(
                signal="logs",
                folder="nebius",
                dashboard="kubernetes-logs-from-loki",
                gnet_id=18494,
                datasource="Nebius Logs",
            ),
            "traces": GrafanaReportDashboardBinding(
                signal="traces",
                folder="nebius",
                dashboard="guardrails-starter-traces",
                gnet_id=20600,
                datasource="Nebius Traces",
            ),
        },
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_grafana_datasources_by_name",
        lambda: {
            "Nebius User Metrics": GrafanaDatasourceSpec(
                key="user-metrics",
                name="Nebius User Metrics",
                uid="nebius-user-metrics",
                datasource_type="prometheus",
                read_endpoint="metrics_user_read",
            ),
            "Nebius Logs": GrafanaDatasourceSpec(
                key="logs",
                name="Nebius Logs",
                uid="nebius-logs",
                datasource_type="loki",
                read_endpoint="logs_loki_read",
            ),
            "Nebius Traces": GrafanaDatasourceSpec(
                key="traces",
                name="Nebius Traces",
                uid="nebius-traces",
                datasource_type="tempo",
                read_endpoint="traces_tempo_read",
            ),
        },
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_grafana_cli_settings",
        lambda: GrafanaCliSettings(
            explore_queries=(
                GrafanaExploreQuerySpec(
                    signal="metrics",
                    query='count({__name__=~".+"})',
                ),
                GrafanaExploreQuerySpec(signal="logs", query='{__bucket__="default"}'),
            ),
            org_id=1,
        ),
    )

    def fake_dashboard_url(
        base_url: str,
        dashboard_key: str,
        gnet_id: int,
        _spec: grafana_runtime.GrafanaReleaseSpec,
        *,
        extra_env: dict[str, str] | None,
    ) -> str:
        del extra_env
        slugs = {
            ("kubernetes-cluster-monitoring", 315): (
                "kubernetes-cluster-monitoring-via-prometheus"
            ),
            ("kubernetes-logs-from-loki", 18494): "kubernetes-logs-from-loki",
            ("guardrails-starter-traces", 20600): "standard-guardrails-dash",
        }
        return f"{base_url}d/k8s/{slugs[(dashboard_key, gnet_id)]}"

    monkeypatch.setattr(
        grafana_runtime,
        "_grafana_dashboard_url_for_spec",
        fake_dashboard_url,
    )
    shortened_input: dict[str, str] = {}

    def fake_shorten(
        base_url: str,
        urls: dict[str, str],
        spec: grafana_runtime.GrafanaReleaseSpec,
        *,
        extra_env: dict[str, str] | None,
        org_id: int,
    ) -> dict[str, str]:
        del spec, extra_env, org_id
        shortened_input.update(urls)
        return {key: f"{base_url}goto/{key.replace('_url', '')}?orgId=1" for key in urls}

    monkeypatch.setattr(
        grafana_runtime,
        "_shorten_grafana_urls",
        fake_shorten,
    )

    statuses = grafana_runtime.collect_grafana_runtime_status(
        _grafana_payload(),
        extra_env={"KUBECONFIG": str(kubeconfig)},
        target_ref="cluster2",
    )

    assert len(statuses) == 1
    status = statuses[0]
    assert status["target_ref"] == "cluster2"
    assert status["kube_context"] == "nebius-cluster2-mk8scluster-123-external"
    assert status["metrics_url"] == "http://203.0.113.20/goto/metrics?orgId=1"
    assert status["metrics_url_kind"] == "dashboard"
    assert status["metrics_url_gnet_id"] == 315
    assert (
        shortened_input["metrics_url"]
        == "http://203.0.113.20/d/k8s/kubernetes-cluster-monitoring-via-prometheus"
    )
    assert status["logs_url"] == "http://203.0.113.20/goto/logs?orgId=1"
    assert status["logs_url_kind"] == "dashboard"
    assert status["logs_url_gnet_id"] == 18494
    assert shortened_input["logs_url"] == "http://203.0.113.20/d/k8s/kubernetes-logs-from-loki"
    assert status["traces_url"] == "http://203.0.113.20/goto/traces?orgId=1"
    assert status["traces_url_kind"] == "dashboard"
    assert status["traces_url_gnet_id"] == 20600
    assert shortened_input["traces_url"] == "http://203.0.113.20/d/k8s/standard-guardrails-dash"


def test_collect_grafana_runtime_status_uses_long_urls_when_public_root_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        grafana_runtime,
        "_grafana_base_url",
        lambda _spec, *, extra_env: "http://203.0.113.20/",
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_ensure_grafana_public_root_url",
        lambda _spec, _base_url, *, extra_env: False,
    )

    statuses = grafana_runtime.collect_grafana_runtime_status(
        _grafana_payload(),
        extra_env={},
        target_ref="cluster2",
    )

    assert len(statuses) == 1
    assert "schemaVersion=1" in statuses[0]["metrics_url"]
    assert "panes=" in statuses[0]["traces_url"]
