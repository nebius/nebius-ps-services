from __future__ import annotations

import json
from importlib import resources

import nebius_cxcli.grafana_dashboard_validation as dashboard_validation
from nebius_cxcli.component_sources import GrafanaDatasourceSpec
from nebius_cxcli.grafana_runtime import GrafanaReleaseSpec


def _dashboard(name: str) -> dict:
    payload = resources.files("nebius_cxcli").joinpath("grafana_dashboards", name).read_text()
    return json.loads(payload)


def test_bundled_metrics_dashboard_contract_matches_nebius_user_metrics_labels() -> None:
    contract = dashboard_validation._prometheus_contract(_dashboard("kubernetes-metrics.json"))

    assert contract.labels_by_metric["container_cpu_usage_seconds_total"] == {
        "k8s.cluster.id",
        "kubernetes_io_hostname",
        "namespace",
        "pod",
    }
    assert contract.labels_by_metric["container_cpu_cfs_periods_total"] == {
        "k8s.cluster.id",
        "kubernetes_io_hostname",
        "namespace",
        "pod",
    }
    assert contract.labels_by_metric["container_cpu_cfs_throttled_periods_total"] == {
        "k8s.cluster.id",
        "kubernetes_io_hostname",
        "namespace",
        "pod",
    }
    assert contract.labels_by_metric["container_memory_working_set_bytes"] == {
        "k8s.cluster.id",
        "kubernetes_io_hostname",
        "namespace",
        "pod",
    }
    assert contract.labels_by_metric["container_memory_failures_total"] == {
        "k8s.cluster.id",
        "kubernetes_io_hostname",
        "namespace",
        "pod",
    }
    assert contract.labels_by_metric["container_network_receive_bytes_total"] == {
        "k8s.cluster.id",
        "kubernetes_io_hostname",
        "namespace",
        "pod",
    }
    assert contract.labels_by_metric["container_network_transmit_bytes_total"] == {
        "k8s.cluster.id",
        "kubernetes_io_hostname",
        "namespace",
        "pod",
    }
    assert contract.labels_by_metric["container_network_receive_errors_total"] == {
        "k8s.cluster.id",
        "kubernetes_io_hostname",
        "namespace",
        "pod",
    }
    assert contract.labels_by_metric["container_network_transmit_errors_total"] == {
        "k8s.cluster.id",
        "kubernetes_io_hostname",
        "namespace",
        "pod",
    }
    assert contract.labels_by_metric["container_fs_usage_bytes"] == {
        "device",
        "k8s.cluster.id",
        "kubernetes_io_hostname",
    }
    assert contract.labels_by_metric["container_fs_reads_bytes_total"] == {
        "device",
        "k8s.cluster.id",
        "kubernetes_io_hostname",
    }
    assert contract.labels_by_metric["container_fs_writes_bytes_total"] == {
        "device",
        "k8s.cluster.id",
        "kubernetes_io_hostname",
    }
    assert contract.labels_by_metric["apiserver_request_total"] == {
        "k8s.cluster.id",
    }
    assert contract.labels_by_metric["apiserver_current_inflight_requests"] == {
        "k8s.cluster.id",
    }
    assert 'query_result(count by ("k8s.cluster.id") (container_cpu_usage_seconds_total))' in (
        contract.queries
    )
    assert any("container_cpu_cfs_throttled_periods_total" in query for query in contract.queries)
    assert any("container_memory_failures_total" in query for query in contract.queries)
    assert any("container_fs_reads_bytes_total" in query for query in contract.queries)
    assert any("apiserver_request_total" in query for query in contract.queries)
    assert any("$__rate_interval" in query for query in contract.queries)


def test_bundled_gpu_dashboard_contract_matches_nebius_service_metrics_labels() -> None:
    dashboard = _dashboard("kubernetes-gpu.json")
    contract = dashboard_validation._prometheus_contract(dashboard)

    for metric in (
        "DCGM_FI_DEV_FB_FREE",
        "DCGM_FI_DEV_FB_USED",
        "DCGM_FI_DEV_GPU_TEMP",
        "DCGM_FI_DEV_GPU_UTIL",
        "DCGM_FI_DEV_MEM_CLOCK",
        "DCGM_FI_DEV_ECC_DBE_VOL_TOTAL",
        "DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL",
        "DCGM_FI_DEV_PCIE_REPLAY_COUNTER",
        "DCGM_FI_DEV_POWER_USAGE",
        "DCGM_FI_DEV_SM_CLOCK",
        "DCGM_FI_DEV_XID_ERRORS",
    ):
        assert contract.labels_by_metric[metric] == {
            "instance_id",
            "job",
            "mk8s_cluster_id",
            "uuid",
        }
    assert (
        'query_result(count by (mk8s_cluster_id) '
        '(DCGM_FI_DEV_GPU_UTIL{job="nebius-observability-agent"}))'
    ) in contract.queries
    assert any(
        "avg by (instance_id, uuid) (DCGM_FI_DEV_GPU_UTIL" in query
        for query in contract.queries
    )
    assert any(
        "avg by (instance_id, uuid) (DCGM_FI_DEV_POWER_USAGE" in query
        for query in contract.queries
    )
    assert any(
        "avg by (instance_id, uuid) (DCGM_FI_DEV_GPU_TEMP" in query
        for query in contract.queries
    )
    assert any("DCGM_FI_DEV_XID_ERRORS" in query for query in contract.queries)
    assert any("DCGM_FI_DEV_ECC_DBE_VOL_TOTAL" in query for query in contract.queries)
    timeseries_legends = [
        target.get("legendFormat", "")
        for panel in dashboard["panels"]
        if panel.get("type") == "timeseries"
        for target in panel.get("targets", [])
    ]
    assert timeseries_legends
    assert set(timeseries_legends) == {"{{uuid}} {{instance_id}}"}


def test_bundled_logs_dashboard_contract_matches_nebius_loki_labels() -> None:
    contract = dashboard_validation._loki_contract(_dashboard("kubernetes-logs.json"))

    assert {"__bucket__", "k8s_cluster_id", "k8s_namespace_name", "k8s_pod_name"}.issubset(
        contract.labels
    )
    assert any('__bucket__="default"' in query for query in contract.queries)


def test_bundled_vm_metrics_dashboard_contract_matches_built_in_agent_labels() -> None:
    contract = dashboard_validation._prometheus_contract(_dashboard("vm-metrics.json"))

    assert contract.labels_by_metric["node_cpu_seconds_total"] == {
        "instance_id",
        "job",
        "mode",
    }
    assert contract.labels_by_metric["node_memory_MemAvailable_bytes"] == {
        "instance_id",
        "job",
    }
    assert contract.labels_by_metric["node_disk_read_bytes_total"] == {
        "device",
        "instance_id",
        "job",
    }
    assert contract.labels_by_metric["node_network_receive_bytes_total"] == {
        "device",
        "instance_id",
        "job",
    }
    assert contract.labels_by_metric["DCGM_FI_DEV_GPU_UTIL"] == {
        "instance_id",
        "job",
    }
    assert any('job="nebius-observability-agent"' in query for query in contract.queries)
    assert all("cxcli-vm-collector" not in query for query in contract.queries)
    assert any("$__rate_interval" in query for query in contract.queries)


def test_bundled_vm_logs_dashboard_contract_uses_nebius_loki_buckets() -> None:
    dashboard = _dashboard("vm-logs.json")
    contract = dashboard_validation._loki_contract(dashboard)
    bucket_variable = next(
        item for item in dashboard["templating"]["list"] if item["name"] == "Bucket"
    )

    assert "__bucket__" in contract.labels
    assert bucket_variable["query"] == "sp_serial,default"
    assert bucket_variable["current"]["value"] == "sp_serial"
    assert all('collector="cxcli-vm-collector"' not in query for query in contract.queries)
    assert any("|~ \"$search\"" in query for query in contract.queries)


def test_bundled_traces_dashboard_contract_uses_traceql_search() -> None:
    contract = dashboard_validation._tempo_contract(
        _dashboard("kubernetes-traces.json"),
        datasource_uid="nebius-traces",
    )

    assert contract.queries == ("{}", "{ duration > 1s }", "{ status = error }")
    assert contract.attributes == set()


def test_bundled_grafana_contracts_cover_all_catalog_dashboards() -> None:
    contracts = {
        contract.dashboard_ref: contract
        for contract in dashboard_validation._grafana_chart_contracts()
    }

    assert set(contracts) == {
        "nebius/nebius-disk",
        "nebius-kubernetes/kubernetes-cluster-monitoring",
        "nebius-kubernetes/kubernetes-gpu",
        "nebius-kubernetes/kubernetes-logs-from-loki",
        "nebius-kubernetes/kubernetes-traces",
        "nebius-vm/vm-metrics",
        "nebius-vm/vm-logs",
    }
    assert contracts["nebius-kubernetes/kubernetes-cluster-monitoring"].signal == "metrics"
    assert contracts["nebius-kubernetes/kubernetes-logs-from-loki"].signal == "logs"
    assert contracts["nebius-kubernetes/kubernetes-traces"].signal == "traces"
    assert contracts["nebius/nebius-disk"].signal == "dashboard"
    assert contracts["nebius/nebius-disk"].dashboard_uid == "nebius-disk-user-stats"
    assert contracts["nebius/nebius-disk"].gnet_id == 23425
    assert contracts["nebius-kubernetes/kubernetes-gpu"].signal == "dashboard"
    assert contracts["nebius-kubernetes/kubernetes-gpu"].dashboard_uid == "cxcli-kubernetes-gpu"
    assert contracts["nebius-kubernetes/kubernetes-gpu"].datasource.name == "Nebius Services"
    assert contracts["nebius-vm/vm-metrics"].signal == "dashboard"
    assert contracts["nebius-vm/vm-metrics"].dashboard_uid == "cxcli-vm-metrics"
    assert contracts["nebius-vm/vm-metrics"].datasource.name == "Nebius Services"
    assert contracts["nebius-vm/vm-logs"].signal == "dashboard"
    assert contracts["nebius-vm/vm-logs"].dashboard_uid == "cxcli-vm-logs"
    assert contracts["nebius-vm/vm-logs"].datasource.name == "Nebius Logs"


def test_dashboard_import_warning_reports_missing_live_uid(
    monkeypatch,
) -> None:
    def raise_404(*args, **kwargs):
        raise RuntimeError("Grafana API returned HTTP 404")

    monkeypatch.setattr(dashboard_validation, "_grafana_get_json", raise_404)

    assert dashboard_validation._dashboard_import_warnings(
        base_url="http://grafana.example",
        username="admin",
        password="secret",
        dashboard_uid="cxcli-kubernetes-metrics",
    ) == [
        "Grafana dashboard UID cxcli-kubernetes-metrics is not imported yet; "
        "run deploy or flux apply the generated bundle, then wait for Grafana to import the dashboard ConfigMap"
    ]


def test_validate_dashboard_fits_reports_dashboard_level_progress(monkeypatch) -> None:
    datasource = GrafanaDatasourceSpec(
        key="services",
        name="Nebius Services",
        uid="nebius-services",
        datasource_type="prometheus",
        read_endpoint="metrics_service_provider_read",
    )
    contracts = (
        dashboard_validation._GrafanaDashboardContract(
            signal="dashboard",
            folder="nebius",
            dashboard="nebius-disk",
            dashboard_uid="disk",
            gnet_id=23425,
            dashboard_spec={"gnetId": 23425},
            datasource=datasource,
        ),
        dashboard_validation._GrafanaDashboardContract(
            signal="metrics",
            folder="nebius-kubernetes",
            dashboard="kubernetes-cluster-monitoring",
            dashboard_uid="k8s",
            gnet_id=0,
            dashboard_spec={"json": '{"uid": "k8s"}'},
            datasource=datasource,
        ),
    )

    monkeypatch.setattr(dashboard_validation, "_grafana_chart_contracts", lambda: contracts)
    monkeypatch.setattr(
        dashboard_validation,
        "_target_refs",
        lambda _payload, _target_ref: ("cluster1", "cluster2"),
    )
    monkeypatch.setattr(
        dashboard_validation,
        "grafana_release_specs",
        lambda _payload, *, target_ref="": (
            GrafanaReleaseSpec(
                target_ref=target_ref,
                namespace="observability",
                release_name="grafana",
                service_name="grafana",
                admin_secret_name="grafana-admin",
                admin_user="admin",
                admin_user_key="admin-user",
                admin_password_key="admin-password",
                token_secret_name="grafana-read",
                token_key="token",
            ),
        ),
    )
    monkeypatch.setattr(dashboard_validation, "_grafana_base_url", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        dashboard_validation,
        "_grafana_admin_credentials",
        lambda *_args, **_kwargs: ("admin", "secret"),
    )

    progress: list[tuple[str, int, int]] = []
    results = dashboard_validation.validate_grafana_dashboard_fits(
        {},
        progress_callback=lambda label, completed, total: progress.append(
            (label, completed, total)
        ),
    )

    assert len(results) == 4
    assert progress[0] == ("init", 0, 4)
    assert ("cluster1: nebius/nebius-disk", 0, 4) in progress
    assert ("cluster1: nebius/nebius-disk", 1, 4) in progress
    assert ("cluster2: nebius-kubernetes/kubernetes-cluster-monitoring", 3, 4) in progress
    assert ("cluster2: nebius-kubernetes/kubernetes-cluster-monitoring", 4, 4) in progress
    assert progress[-1] == ("done", 4, 4)


def test_non_report_prometheus_validation_reports_check_not_warning(monkeypatch) -> None:
    dashboard = {
        "panels": [
            {
                "targets": [
                    {
                        "datasource": {"type": "prometheus", "uid": "prom"},
                        "expr": 'rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])',
                    }
                ]
            }
        ]
    }

    def fake_proxy_get_json(*args, **kwargs):
        assert args[2] == "/api/v1/series"
        return {
            "data": [
                {
                    "__name__": "node_cpu_seconds_total",
                    "mode": "idle",
                    "instance": "node-1",
                }
            ]
        }

    monkeypatch.setattr(dashboard_validation, "_proxy_get_json", fake_proxy_get_json)

    errors, warnings, checks = dashboard_validation._validate_prometheus(
        base_url="http://grafana.example",
        datasource_uid="prom",
        datasource_name="Nebius Services",
        username="admin",
        password="secret",
        dashboard=dashboard,
        now=1000,
        start=0,
        missing_metric_is_error=False,
        run_query_checks=False,
    )

    assert errors == []
    assert warnings == []
    assert checks == ["Metric/label names matched"]


def test_prometheus_validation_scopes_cluster_metrics_to_target_cluster(monkeypatch) -> None:
    dashboard = _dashboard("kubernetes-metrics.json")
    captured_series_matchers: list[str] = []
    captured_queries: list[str] = []

    def fake_proxy_get_json(*args, **kwargs):
        path = args[2]
        params = kwargs["params"]
        if path == "/api/v1/series":
            matcher = params["match[]"][0]
            captured_series_matchers.append(matcher)
            if matcher.startswith('container_cpu_usage_seconds_total{'):
                return {"data": []}
            return {
                "data": [
                    {
                        "__name__": matcher.split("{", 1)[0],
                        "k8s.cluster.id": "mk8scluster-222",
                        "kubernetes_io_hostname": "node-1",
                        "container": "grafana",
                        "node": "node-1",
                        "service": "nvidia-dcgm-exporter",
                    }
                ]
            }
        if path == "/api/v1/query":
            captured_queries.append(params["query"])
            return {"data": {"result": [{"metric": {}, "value": [1, "1"]}]}}
        raise AssertionError(path)

    monkeypatch.setattr(dashboard_validation, "_proxy_get_json", fake_proxy_get_json)

    errors, warnings, checks = dashboard_validation._validate_prometheus(
        base_url="http://grafana.example",
        datasource_uid="nebius-user-metrics",
        datasource_name="Nebius User Metrics",
        username="admin",
        password="secret",
        dashboard=dashboard,
        now=1000,
        start=0,
        target_cluster_id="mk8scluster-222",
    )

    assert any(
        matcher
        == 'container_cpu_usage_seconds_total{"k8s.cluster.id"=~"mk8scluster-222"}'
        for matcher in captured_series_matchers
    )
    assert any('"k8s.cluster.id"=~"mk8scluster-222"' in query for query in captured_queries)
    assert (
        "Prometheus metric container_cpu_usage_seconds_total has no series for target cluster "
        "mk8scluster-222"
    ) in errors
    assert warnings == []
    assert checks == []


def test_prometheus_validation_scopes_service_metrics_to_target_cluster(monkeypatch) -> None:
    dashboard = _dashboard("kubernetes-gpu.json")
    captured_series_matchers: list[str] = []

    def fake_proxy_get_json(*args, **kwargs):
        path = args[2]
        params = kwargs["params"]
        if path == "/api/v1/series":
            matcher = params["match[]"][0]
            captured_series_matchers.append(matcher)
            return {
                "data": [
                    {
                        "__name__": matcher.split("{", 1)[0],
                        "instance_id": "computeinstance-1",
                        "job": "nebius-observability-agent",
                        "mk8s_cluster_id": "mk8scluster-222",
                        "uuid": "GPU-1",
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(dashboard_validation, "_proxy_get_json", fake_proxy_get_json)

    errors, warnings, checks = dashboard_validation._validate_prometheus(
        base_url="http://grafana.example",
        datasource_uid="nebius-service-metrics",
        datasource_name="Nebius Services",
        username="admin",
        password="secret",
        dashboard=dashboard,
        now=1000,
        start=0,
        missing_metric_is_error=False,
        run_query_checks=False,
        target_cluster_id="mk8scluster-222",
    )

    assert any(
        matcher == 'DCGM_FI_DEV_GPU_UTIL{mk8s_cluster_id=~"mk8scluster-222"}'
        for matcher in captured_series_matchers
    )
    assert errors == []
    assert warnings == []
    assert checks == ["Metric/label names matched"]


def test_loki_validation_scopes_label_discovery_to_target_cluster(monkeypatch) -> None:
    dashboard = _dashboard("kubernetes-logs.json")
    captured_label_queries: list[str] = []
    captured_log_queries: list[str] = []

    def fake_proxy_get_json(*args, **kwargs):
        path = args[2]
        params = kwargs["params"]
        if path == "/loki/api/v1/labels":
            query = params.get("query")
            if query:
                captured_label_queries.append(query)
            return {
                "data": [
                    "__bucket__",
                    "k8s_cluster_id",
                    "k8s_namespace_name",
                    "k8s_pod_name",
                ]
            }
        if path == "/loki/api/v1/query_range":
            captured_log_queries.append(params["query"])
            return {"data": {"result": [{"stream": {}, "values": []}]}}
        raise AssertionError(path)

    monkeypatch.setattr(dashboard_validation, "_proxy_get_json", fake_proxy_get_json)

    errors, warnings = dashboard_validation._validate_loki(
        base_url="http://grafana.example",
        datasource_uid="nebius-logs",
        datasource_name="Nebius Logs",
        username="admin",
        password="secret",
        dashboard=dashboard,
        now=1000,
        start=0,
        target_cluster_id="mk8scluster-222",
    )

    assert (
        '{__bucket__="default", k8s_cluster_id=~"mk8scluster-222"}'
        in captured_label_queries
    )
    assert any('k8s_cluster_id=~"mk8scluster-222"' in query for query in captured_log_queries)
    assert errors == []
    assert warnings == []


def test_loki_validation_accepts_vm_logs_bucket_only_dashboard(monkeypatch) -> None:
    dashboard = _dashboard("vm-logs.json")
    captured_queries: list[str] = []

    def fake_proxy_get_json(*args, **kwargs):
        path = args[2]
        if path == "/loki/api/v1/labels":
            return {"data": ["__bucket__"]}
        if path == "/loki/api/v1/query_range":
            captured_queries.append(kwargs["params"]["query"])
            return {"data": {"result": []}}
        raise AssertionError(path)

    monkeypatch.setattr(dashboard_validation, "_proxy_get_json", fake_proxy_get_json)

    errors, warnings = dashboard_validation._validate_loki(
        base_url="http://grafana.example",
        datasource_uid="nebius-logs",
        datasource_name="Nebius Logs",
        username="admin",
        password="secret",
        dashboard=dashboard,
        now=1000,
        start=0,
        missing_label_is_error=False,
    )

    assert errors == []
    assert not any("missing required label(s)" in warning for warning in warnings)
    assert any('__bucket__=~"sp_serial"' in query for query in captured_queries)
    assert all("$__interval" not in query for query in captured_queries)
    assert any("[5m]" in query for query in captured_queries)
