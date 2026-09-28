"""Private datasource and connection boundaries for shared teaching dashboards."""

from contextlib import nullcontext
from dataclasses import replace

import pytest

from nebius_cxcli import component_sources, grafana_runtime, observability, ordinary_apps


def spec():
    return grafana_runtime.GrafanaReleaseSpec(
        target_ref="cluster",
        namespace="course-observability",
        release_name="grafana",
        service_name="grafana",
        admin_secret_name="grafana-admin",
        admin_user="admin",
        admin_user_key="user",
        admin_password_key="password",
        token_secret_name="",
        token_key="",
    )


def test_internal_datasource_has_no_cloud_authorization():
    result = observability._grafana_datasource(
        name="Soperator Metrics",
        uid="course-soperator-metrics",
        datasource_type="prometheus",
        url="http://metrics.monitoring-system.svc:8429",
        token_env="",
        auth="none",
    )
    assert "jsonData" not in result and "secureJsonData" not in result
    bearer = observability._grafana_datasource(
        name="Cloud",
        uid="cloud",
        datasource_type="prometheus",
        url="https://example.invalid",
        token_env="CLOUD_TOKEN",
        auth="nebius_bearer",
    )
    assert bearer["secureJsonData"] == {"httpHeaderValue1": "Bearer ${CLOUD_TOKEN}"}


@pytest.mark.parametrize(
    "auth,kind,valid",
    [
        ("none", "prometheus", True),
        ("none", "loki", False),
        ("bad", "prometheus", False),
        ("nebius_bearer", "prometheus", True),
    ],
)
def test_datasource_auth_contract(auth, kind, valid):
    raw = {
        "metrics": dict(name="Metrics", uid="metrics", type=kind, read_endpoint="local", auth=auth)
    }
    if valid:
        result = component_sources._parse_grafana_datasources(raw, field_label="datasources")
        assert result[0].auth == auth
    else:
        with pytest.raises(ValueError):
            component_sources._parse_grafana_datasources(raw, field_label="datasources")


def test_internal_runtime_never_issues_cloud_token(monkeypatch):
    monkeypatch.setattr(grafana_runtime, "grafana_release_specs", lambda *a, **k: (spec(),))
    monkeypatch.setattr(grafana_runtime, "_ensure_namespace", lambda *a, **k: None)
    monkeypatch.setattr(grafana_runtime, "_secret_has_keys", lambda **k: True)
    monkeypatch.setattr(
        grafana_runtime,
        "_ensure_grafana_read_token_secret",
        lambda *a, **k: pytest.fail("cloud credential requested"),
    )
    grafana_runtime.ensure_grafana_runtime_secrets({}, extra_env={})


@pytest.mark.parametrize("mode", ["success", "caller-error", "early-exit", "timeout"])
def test_loopback_forward_is_pinned_and_always_cleaned(monkeypatch, mode):
    calls = []

    def base(spec, *, extra_env):
        assert extra_env[grafana_runtime.GRAFANA_TARGET_KUBE_CONTEXT_ENV] == "pinned-cluster"
        return ""

    monkeypatch.setattr(grafana_runtime, "_grafana_base_url", base)
    monkeypatch.setattr(
        grafana_runtime,
        "_kubectl_json",
        lambda *a, **k: {"spec": {"type": "ClusterIP", "ports": [{"port": 80}]}},
    )

    class Process:
        stopped = False

        def poll(self):
            return 1 if mode == "early-exit" or self.stopped else None

        def terminate(self):
            self.stopped = True
            calls.append("terminate")

        def kill(self):
            self.stopped = True
            calls.append("kill")

        def wait(self, timeout):
            calls.append("wait")
            return 0

    process = Process()

    def launch(command, **kwargs):
        assert command[:3] == ["kubectl", "--context", "pinned-cluster"]
        assert command[-3:] == ["127.0.0.1", "service/grafana", ":80"]
        if mode in {"success", "caller-error"}:
            kwargs["stdout"].write(b"Forwarding from 127.0.0.1:39123 -> 80\n")
            kwargs["stdout"].flush()
        return process

    monkeypatch.setattr(grafana_runtime.kubernetes_process, "popen", launch)

    def run():
        with grafana_runtime.grafana_api_endpoint(
            spec(),
            extra_env={grafana_runtime.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "pinned-cluster"},
            timeout_seconds=0 if mode == "timeout" else 1,
        ) as endpoint:
            assert endpoint == "http://127.0.0.1:39123/"
            if mode == "caller-error":
                raise ValueError("consumer failed")

    if mode == "success":
        run()
    else:
        with pytest.raises(ValueError if mode == "caller-error" else RuntimeError):
            run()
    if mode != "early-exit":
        assert calls == ["terminate", "wait"]


@pytest.mark.parametrize(
    "sample,generation,expected", [(1, 7, False), (1001, 6, False), (1001, 7, True)]
)
def test_generation_and_sample_freshness_are_independent(monkeypatch, sample, generation, expected):
    datasource = component_sources.GrafanaDatasourceSpec(
        key="m",
        name="M",
        uid="local",
        datasource_type="prometheus",
        read_endpoint="local",
        auth="none",
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_grafana_cli_settings",
        lambda: component_sources.GrafanaCliSettings(datasources=(datasource,)),
    )
    monkeypatch.setattr(grafana_runtime, "grafana_release_specs", lambda *a, **k: (spec(),))
    monkeypatch.setattr(grafana_runtime, "grafana_api_environment", lambda env: {"frozen": "yes"})
    monkeypatch.setattr(
        grafana_runtime,
        "grafana_api_endpoint",
        lambda *a, **k: nullcontext("http://127.0.0.1:1234"),
    )

    def credentials(*a, **k):
        assert k["extra_env"] == {"frozen": "yes"}
        return ("admin", "not-a-real-credential")

    monkeypatch.setattr(grafana_runtime, "_grafana_admin_credentials", credentials)

    def query(*a, **k):
        assert "/local/" in a[1]
        expression = k["params"]["query"]
        assert (
            "== 7" in expression and "timestamp(" in expression and ">= 1000.000000" in expression
        )
        rows = (
            [{"metric": {}, "value": [9999, str(generation)]}]
            if sample >= 1000 and generation == 7
            else []
        )
        return {"status": "success", "data": {"result": rows}}

    monkeypatch.setattr(grafana_runtime, "_get_grafana_json", query)
    assert (
        grafana_runtime.grafana_prometheus_has_fresh_series(
            {},
            target_ref="cluster",
            query="course_lab_publication_generation",
            not_before=1000,
            extra_env={},
            datasource_uid="local",
            expected_value=7,
        )
        is expected
    )


def accepted():
    identity = {"cluster_id": "cluster-example", "kubernetes_uid": "uid-example"}
    baseline = {"deployment_generation": "g1", "identities": {"cluster": identity}}
    record = {
        "active": None,
        "accepted": {
            "generation": "g1",
            "evidence": {
                "identities": {"cluster": identity},
                "targets": {"cluster": {"identity": identity}},
            },
        },
    }
    return record, baseline


@pytest.mark.parametrize("fault", ["pending", "generation", "identity", "target", "missing"])
def test_remote_authority_rejects_stale_or_conflicting_baselines(fault):
    import copy

    record, baseline = accepted()
    record = copy.deepcopy(record)
    if fault == "pending":
        record["active"] = {"operation": "running"}
    elif fault == "generation":
        record["accepted"]["generation"] = "g2"
    elif fault == "identity":
        record["accepted"]["evidence"]["identities"]["cluster"] = {}
    elif fault == "target":
        record["accepted"]["evidence"]["targets"]["cluster"]["identity"] = {}
    else:
        baseline.pop("deployment_generation")
    with pytest.raises(RuntimeError):
        ordinary_apps.validate_accepted_deployment_record(record, baseline, ["cluster"])


def test_accepted_identity_allows_repeated_app_generations():
    record, baseline = accepted()
    for _ in range(2):
        ordinary_apps.validate_accepted_deployment_record(record, baseline, ["cluster"])


@pytest.mark.parametrize("gateway", ["", "pending-public-gateway"])
def test_private_status_does_not_mask_a_pending_public_gateway(monkeypatch, gateway):
    selected = replace(spec(), gateway_name=gateway)
    monkeypatch.setattr(grafana_runtime, "grafana_release_specs", lambda *a, **kw: (selected,))
    monkeypatch.setattr(grafana_runtime, "_grafana_base_url", lambda *a, **kw: "")
    monkeypatch.setattr(
        grafana_runtime, "_grafana_cli_settings", lambda: component_sources.GrafanaCliSettings()
    )
    monkeypatch.setattr(
        grafana_runtime,
        "_kubectl_json",
        lambda *a, **kw: {"spec": {"type": "ClusterIP", "ports": [{"port": 80}]}},
    )
    status = grafana_runtime.collect_grafana_runtime_status(
        {}, extra_env={grafana_runtime.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "frozen-context"}
    )[0]
    assert status.get("access") == (None if gateway else "private-port-forward")
    assert status["base_url"] == ""
    if not gateway:
        assert status["port_forward_command"][:3] == ["kubectl", "--context", "frozen-context"]
        assert "127.0.0.1" in status["port_forward_command"]
