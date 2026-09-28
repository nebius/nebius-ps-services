import copy
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from nebius_cxcli import grafana_runtime
from nebius_cxcli.grafana_install import configure
from nebius_cxcli.observability_backends import metrics_destinations
from nebius_cxcli.observability_runtime import (
    admit_vmagent_transition,
    verify_vmagent_routes,
    write_secret_namespaces,
)
from test_observability_routing import payload


@pytest.mark.parametrize("fenced", [False, True])
@pytest.mark.parametrize("lose_authority", [False, True])
def test_connections_preserve_outer_authority_through_real_nested_client(
    monkeypatch, fenced, lose_authority
):
    from contextlib import nullcontext

    from nebius_cxcli import grafana_cluster, observability_runtime
    from nebius_cxcli.app_mutation import (
        app_mutation_scope,
        assert_app_mutation_authority,
        current_app_mutation_authority,
    )

    data = configure(payload(), "cluster")
    desired = observability_runtime.connections(data, "cluster")
    events = []

    def fence():
        events.append("fence")
        if lose_authority and "endpoint" in events:
            raise RuntimeError("operation authority lost")

    @contextmanager
    def endpoint(*args, **kwargs):
        events.append("endpoint")
        assert_app_mutation_authority()
        yield "http://127.0.0.1:3000"

    class Client:
        def __init__(self, *args, assert_authority, **kwargs):
            self.fence = assert_authority

        def connect(self):
            self.fence()
            events.append("connect")

        def datasources(self):
            self.fence()
            return desired

        def request(self, method, path):
            self.fence()
            assert method == "GET"
            events.append(path)
            if path.endswith("/health"):
                return {"status": "OK"}
            import time

            return {"data": {"result": [{"value": [time.time(), "1"]}]}}

    monkeypatch.setattr(
        grafana_cluster, "_grafana_admin_credentials", lambda *a, **k: ("fixture", "fixture")
    )
    monkeypatch.setattr(grafana_cluster, "grafana_api_endpoint", endpoint)
    monkeypatch.setattr(grafana_cluster, "GrafanaClient", Client)
    outer = current_app_mutation_authority()
    with app_mutation_scope(fence) if fenced else nullcontext():
        captured = current_app_mutation_authority()
        if fenced and lose_authority:
            with pytest.raises(RuntimeError, match="operation authority lost"):
                observability_runtime.verify_connections(data, target_ref="cluster", extra_env={})
            assert "connect" not in events
        else:
            observability_runtime.verify_connections(data, target_ref="cluster", extra_env={})
            assert "connect" in events
            assert sum(e.endswith("/health") for e in events) == len(desired)
        assert current_app_mutation_authority() is captured
    assert current_app_mutation_authority() is outer
    assert ("fence" in events) is fenced


def test_native_local_proxy_retries_empty_logs_and_checks_current_pod(monkeypatch):
    from nebius_cxcli import grafana_cluster, soperator_telemetry
    from nebius_cxcli.observability_runtime import verify_native_telemetry
    from test_soperator_telemetry import _scope

    data = configure(payload(), "cluster")
    scope = replace(_scope(), target_ref="cluster")
    monkeypatch.setattr(soperator_telemetry.time, "time", lambda: 1300.0)
    monkeypatch.setattr(soperator_telemetry.time, "sleep", lambda _: None)
    calls = []
    logs = []

    def request(method, path, **kwargs):
        calls.append(path)
        assert method == "GET" and kwargs["timeout_seconds"] <= 20
        if "api/v1/query?" in path:
            return {"status": "success", "data": {"result": [{"value": [1250.0, "1"]}]}}
        assert kwargs["ndjson"] and "select/logsql/query?" in path
        if not logs:
            logs.append(True)
            return []
        return [
            {
                "k8s.namespace.name": scope.namespace,
                "k8s.pod.name": scope.pod_name,
                "k8s.pod.uid": scope.pod_uid,
                "k8s.container.name": scope.container_name,
                "_time": "1970-01-01T00:20:00Z",
                "_msg": "fixture",
            }
        ]

    @contextmanager
    def client(*args, **kwargs):
        kwargs["fence"]()
        yield SimpleNamespace(request=request)

    monkeypatch.setattr(grafana_cluster, "release_client", client)
    receipt = verify_native_telemetry(
        data,
        scope,
        verification_id="fixture",
        extra_env={"KUBECONFIG": "/explicit/fixture"},
        emit=None,
        verifier=soperator_telemetry.verify_soperator_observability,
    )
    assert receipt.status == "passed" and receipt.attempts == 2
    assert receipt.credential_source == "grafana-datasource-secrets"
    assert receipt.product_log_stream_count == 1
    assert len(calls) == 4


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"", []),
        (b'{"_msg":"first"}\n{"_msg":"second"}\n', [{"_msg": "first"}, {"_msg": "second"}]),
    ],
)
def test_bounded_grafana_transport_reads_victorialogs_ndjson(body, expected):
    from contextlib import nullcontext

    from nebius_cxcli.grafana_api import GrafanaAuth, GrafanaClient

    client = GrafanaClient("http://127.0.0.1:3000/", GrafanaAuth("fixture"))

    def open_response(request, *, timeout):
        assert timeout == 2
        return nullcontext(SimpleNamespace(read=lambda limit: body))

    client.opener = SimpleNamespace(open=open_response)
    assert (
        client.request(
            "GET",
            "api/datasources/proxy/uid/local/select/logsql/query",
            ndjson=True,
            timeout_seconds=2,
        )
        == expected
    )


@pytest.mark.parametrize("stateful", [False, True])
def test_operator_arguments_must_contain_only_selected_urls(monkeypatch, stateful):
    data = configure(payload(), "cluster")
    url = metrics_destinations(data, "cluster")[0]["url"]
    cr = {
        "metadata": {"uid": "owned-cr"},
        "spec": {"statefulMode": stateful, "remoteWrite": [{"url": url}]},
    }
    controller = {
        "metadata": {"generation": 2, "ownerReferences": [{"uid": "owned-cr"}]},
        "spec": {
            "replicas": 1,
            "template": {
                "spec": {"containers": [{"name": "vmagent", "args": ["-remoteWrite.url=" + url]}]}
            },
        },
        "status": {"observedGeneration": 2, "updatedReplicas": 1, "readyReplicas": 1},
    }
    env = {"KUBECONFIG": "/explicit/fixture", "NEBIUS_CXCLI_TARGET_KUBE_CONTEXT": "target"}

    def read(args, *, extra_env):
        assert extra_env == env
        if "vmagent" in args:
            return cr
        assert ("statefulset" if stateful else "deployment") in args
        return controller

    monkeypatch.setattr(grafana_runtime, "_kubectl_json", read)
    verify_vmagent_routes(data, target_ref="cluster", extra_env=env)
    controller["spec"]["template"]["spec"]["containers"][0]["args"].append(
        "-remoteWrite.url=https://external.example/api/v1/write"
    )
    with pytest.raises(RuntimeError, match="unexpected remoteWrite"):
        verify_vmagent_routes(data, target_ref="cluster", extra_env=env)


def test_fresh_cluster_without_vmagent_crd_has_no_queue_to_adopt(monkeypatch):
    data = configure(payload(), "cluster")
    calls = []
    monkeypatch.setattr(
        grafana_runtime, "_kubectl_json", lambda args, **kw: calls.append(args) or {}
    )
    admit_vmagent_transition(
        data, target_ref="cluster", extra_env={"KUBECONFIG": "/explicit/fixture"}
    )
    assert len(calls) == 1
    assert "crd" in calls[0]


def test_writer_credentials_only_for_exporting_owned_signals():
    local = configure(payload(), "cluster")
    assert not write_secret_namespaces(local, "cluster")
    both = configure(local, "cluster", overrides={"metrics_storage": "both"})
    assert write_secret_namespaces(both, "cluster") == ("observability",)
    custom = copy.deepcopy(both)
    custom["deploy"]["targets"][0]["observability"]["routing"]["metrics"]["remote"][
        "auth_secret"
    ] = {"name": "customer-write", "key": "token"}
    assert not write_secret_namespaces(custom, "cluster")
