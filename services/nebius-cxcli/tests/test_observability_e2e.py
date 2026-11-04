from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest

from nebius_cxcli import observability_e2e as e2e
from nebius_cxcli import observability_probe as probe
from nebius_cxcli.cluster_config import cluster_rows
from nebius_cxcli.grafana_install import configure
from nebius_cxcli.observability import (
    normalize_observability_project_settings,
    observability_validation_specs,
)
from nebius_cxcli.observability_routing import collector_owners, ensure_routing_apps
from test_observability_routing import payload


@pytest.mark.integration
def test_grpc_probe_delivers_export_to_real_grpc_server():
    received = []
    server = grpc.server(ThreadPoolExecutor(max_workers=1))
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "opentelemetry.proto.collector.trace.v1.TraceService",
                {
                    "Export": grpc.unary_unary_rpc_method_handler(
                        lambda request, _context: received.append(request) or b""
                    )
                },
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        identity = {"cxcli_run": "run", "cxcli_target": "target", "cxcli_project": "project"}
        request = probe.trace_request(identity, "1" * 32, time.time_ns())
        probe.send_trace(f"127.0.0.1:{port}", request)
        assert received == [request]
        assert all(value.encode() in request for value in identity.values())
        assert bytes.fromhex("1" * 32) in request
    finally:
        server.stop(0).wait()


def test_managed_defaults_and_disabled_preserve_retained_collectors():
    data = payload()
    normalize_observability_project_settings(data)
    ensure_routing_apps(data)
    assert set(collector_owners(data, "cluster").values()) == {"nebius-observability-agent"}
    assert {row["id"] for row in data["apps"]["charts"] if row["enabled"]} == {
        "nebius-observability-agent"
    }
    specs = observability_validation_specs(data)
    assert specs[0]["required"]
    assert specs[0]["e2e"]["write_destinations"]["metrics"][0]["protocol"] == "otlp-http"
    cluster_rows(data)[0]["observability"]["enabled"] = False
    ensure_routing_apps(data)
    assert collector_owners(data, "cluster") == {}
    assert not observability_validation_specs(data)
    assert data["apps"]["charts"][0]["enabled"]


def test_local_freeze_has_required_backend_connections_and_grafana():
    spec = observability_validation_specs(configure(payload(), "cluster"))[0]
    assert spec["e2e"]["grafana"][0]["target_ref"] == "cluster"
    assert {item["name"] for item in spec["e2e"]["connections"]} == {
        "metrics-local",
        "logs-local",
        "traces-local",
    }
    pod = e2e.probe_document(spec["e2e"], "a" * 32, "b" * 32)
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["activeDeadlineSeconds"] == 600
    assert "@sha256:" in pod["spec"]["containers"][0]["image"]


def test_readback_rejects_stale_and_wrong_target_metrics_and_empty_success():
    identity = {"cxcli_run": "r", "cxcli_target": "t", "cxcli_project": "p"}
    started = time.time() - 3
    row = {"metric": dict(identity), "value": [time.time(), str(started + 1)]}
    response = {"status": "success", "data": {"result": [row]}}
    assert e2e.matches("prometheus", response, identity, "1" * 32, started)
    row["value"][1] = str(started - 100)
    assert not e2e.matches("prometheus", response, identity, "1" * 32, started)
    row["value"][1] = str(started + 1)
    row["metric"]["cxcli_target"] = "other"
    assert not e2e.matches("prometheus", response, identity, "1" * 32, started)
    assert not e2e.matches("loki", {"status": "success"}, identity, "1" * 32, started)


def test_missing_portal_access_overwrites_stale_success_and_creates_no_probe(monkeypatch, tmp_path):
    spec = observability_validation_specs(configure(payload(), "cluster"))[0]
    path = tmp_path / spec["report_file"]
    path.write_text(json.dumps({"passed": True}))
    monkeypatch.setattr(
        e2e, "readers", lambda *args: (_ for _ in ()).throw(RuntimeError("missing portal access"))
    )
    monkeypatch.setattr(e2e, "kubectl", lambda *args: pytest.fail("Probe must not be created"))
    with pytest.raises(RuntimeError, match="missing portal access"):
        e2e.run_validation(spec, reports_dir=tmp_path, extra_env={})
    assert json.loads(path.read_text())["passed"] is False


def test_cleanup_uses_uid_precondition(monkeypatch, tmp_path):
    spec = observability_validation_specs(configure(payload(), "cluster"))[0]
    calls = []

    class Reader:
        def request(self, *args, **kwargs):
            return {}

    monkeypatch.setattr(
        e2e, "readers", lambda *args: [("backend", {"type": "prometheus"}, Reader(), "")]
    )
    monkeypatch.setattr(e2e, "matches", lambda *args: True)

    def kubectl(args, env, body=None):
        calls.append((args, body))
        return {"metadata": {"name": "fixture-probe", "uid": "immutable-probe"}}

    monkeypatch.setattr(e2e, "kubectl", kubectl)
    result = e2e.run_validation(spec, reports_dir=tmp_path, extra_env={})
    assert json.loads(result.read_text())["passed"]
    assert calls[-1][0][0] == "delete"
    assert calls[-1][1]["preconditions"] == {"uid": "immutable-probe"}


def test_managed_identity_requires_explicit_human_profile(monkeypatch):
    monkeypatch.delenv("NEBIUS_PROFILE", raising=False)
    with pytest.raises(RuntimeError, match="human Nebius CLI profile"):
        e2e._managed_client()
    monkeypatch.setenv("NEBIUS_PROFILE", "codex-agent-fixture")
    with pytest.raises(RuntimeError, match="human Nebius CLI profile"):
        e2e._managed_client()


def test_preflight_failure_replaces_previous_success(monkeypatch, tmp_path):
    data = payload()
    normalize_observability_project_settings(data)
    ensure_routing_apps(data)
    spec = observability_validation_specs(data)[0]
    path = tmp_path / spec["report_file"]
    path.write_text(json.dumps({"passed": True}))
    monkeypatch.setattr(
        e2e, "_managed_client", lambda: (_ for _ in ()).throw(RuntimeError("no read identity"))
    )
    with pytest.raises(RuntimeError, match="no read identity"):
        e2e.preflight([spec], reports_dir=tmp_path)
    result = json.loads(path.read_text())
    assert result["passed"] is False and result["checks"] == []


def test_trace_readback_requires_named_identity_and_fresh_matching_span():
    identity = {"cxcli_run": "r", "cxcli_target": "t", "cxcli_project": "p"}
    started = time.time() - 3
    trace_id = "1" * 32
    span = {"traceID": trace_id, "startTime": int((started + 1) * 1e6)}
    tags = [{"key": key, "value": value} for key, value in identity.items()]
    result = {"data": [{"spans": [span], "processes": {"p1": {"tags": tags}}}]}
    assert e2e.matches("jaeger", result, identity, trace_id, started)
    span["startTime"] = int((started - 100) * 1e6)
    result["data"][0]["spans"].append({"traceID": "2" * 32, "startTime": int(time.time() * 1e6)})
    assert not e2e.matches("jaeger", result, identity, trace_id, started)
    span["startTime"] = int((started + 1) * 1e6)
    tags[0]["key"] = "unrelated"
    assert not e2e.matches("jaeger", result, identity, trace_id, started)


def test_explicit_disabled_collection_is_not_reenabled_by_selected_store():
    from nebius_cxcli.components import component_entries
    from nebius_cxcli.observability_routing import enable_app, target_settings

    data = payload()
    cluster_rows(data)[0]["observability"] = {"enabled": False}
    enable_app(
        data,
        "cluster",
        next(e for e in component_entries("apps") if e.id == "victoria-metrics-k8s-stack"),
    )
    ensure_routing_apps(data)
    assert cluster_rows(data)[0]["observability"]["enabled"] is False
    assert target_settings(data, "cluster")["local_stores"] == ["metrics"]
    assert collector_owners(data, "cluster") == {}
    assert {r["id"] for r in data["apps"]["charts"]} == {"victoria-metrics-k8s-stack"}


def test_default_routing_respects_disabled_signals_on_every_materialization():
    from nebius_cxcli.observability import (
        ensure_observability_app_rows,
        materialize_observability_app_values,
    )

    data = payload()
    cluster_rows(data)[0]["observability"] = {
        "enabled": True,
        "kubernetes": {"logs": {"enabled": False}, "traces": {"enabled": False}},
    }
    for _ in range(2):
        normalize_observability_project_settings(data)
        ensure_observability_app_rows(data)
        materialize_observability_app_values(data)
        agent = next(
            row for row in data["apps"]["charts"] if row["id"] == "nebius-observability-agent"
        )
        flags = agent["values"]["config"]
        assert flags["metrics"]["enabled"] is True
        assert flags["logs"]["enabled"] is False
        assert flags["traces"]["enabled"] is False
        assert observability_validation_specs(data)[0]["e2e"]["signals"] == ["metrics"]

    # Re-enabling collection must not require reconstructing erased route intent.
    settings = cluster_rows(data)[0]["observability"]
    for signal in ("logs", "traces"):
        settings["kubernetes"][signal]["enabled"] = True
    normalize_observability_project_settings(data)
    ensure_observability_app_rows(data)
    materialize_observability_app_values(data)
    assert observability_validation_specs(data)[0]["e2e"]["signals"] == [
        "metrics",
        "logs",
        "traces",
    ]
    assert all(
        agent["values"]["config"][signal]["enabled"] for signal in ("metrics", "logs", "traces")
    )

    settings = cluster_rows(data)[0]["observability"]
    for signal in ("metrics", "logs", "traces"):
        settings["kubernetes"][signal]["enabled"] = False
    normalize_observability_project_settings(data)
    ensure_observability_app_rows(data)
    materialize_observability_app_values(data)
    assert not collector_owners(data, "cluster")
    assert not observability_validation_specs(data)
    assert not any(
        agent["values"]["config"][signal]["enabled"] for signal in ("metrics", "logs", "traces")
    )


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("traces_enabled", [False, True])
def test_signal_selection_controls_rendered_trace_ingress(tmp_path, local, traces_enabled):
    import yaml

    from nebius_cxcli.config_loader import load_config
    from nebius_cxcli.config_model import to_dynamic_payload
    from nebius_cxcli.flux_render import render_flux
    from nebius_cxcli.observability_routing import required_apps
    from nebius_cxcli.paths import resolve_project_paths
    from test_render import _mk8s_inputs, _starter_payload

    data = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    next(row for row in data["infra"]["components"] if row["id"] == "mk8s")["inputs"] = (
        _mk8s_inputs()
    )
    cluster_rows(data)[0]["observability"] = {
        "enabled": True,
        "kubernetes": {"traces": {"enabled": traces_enabled}},
    }
    if local:
        data = configure(data, "mk8s")
        # A producer opt-out must not remove the retained store or its query path.
        assert "victoria-traces-single" in required_apps(data, "mk8s")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(to_dynamic_payload(data)))
    config = load_config(path)
    paths = render_flux(config, resolve_project_paths(path))
    aliases = [path for path in paths if path.name == "service-cxcli-otlp.yaml"]
    assert bool(aliases) is traces_enabled
    spec = observability_validation_specs(config)[0]
    assert ("traces" in spec["e2e"]["signals"]) is traces_enabled


def test_cleanup_failure_is_explained_in_json_and_aggregate(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from nebius_cxcli.deploy_validation_report import build_deploy_validation_report

    spec = observability_validation_specs(configure(payload(), "cluster"))[0]
    monkeypatch.setattr(
        e2e,
        "readers",
        lambda *args: [
            (
                "Backend metrics-local",
                {"type": "prometheus"},
                SimpleNamespace(request=lambda *a, **k: {}),
                "",
            )
        ],
    )
    monkeypatch.setattr(e2e, "matches", lambda *args: True)

    def kubectl(args, env, body=None):
        if args[0] == "delete":
            raise RuntimeError("deletion refused")
        return {"metadata": {"name": "probe", "uid": "owned-uid"}}

    monkeypatch.setattr(e2e, "kubectl", kubectl)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        e2e.run_validation(spec, reports_dir=tmp_path, extra_env={})
    report = json.loads((tmp_path / spec["report_file"]).read_text())
    assert report["passed"] is False and "cleanup failed" in report["error"]
    combined = build_deploy_validation_report([spec], reports_dir=tmp_path)
    assert "cleanup failed" in combined.results[0].summary


def test_generic_deploy_preflight_failure_clears_previous_observability_success(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from nebius_cxcli import cli

    spec = {
        "kind": "mk8s_observability_ingestion",
        "target_ref": "cluster",
        "report_file": "result.json",
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"passed": True}))
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _: [spec])
    monkeypatch.setattr(e2e, "preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli,
        "_run_generated_bundle_validation",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider preflight failed")),
    )
    with pytest.raises(RuntimeError, match="provider preflight failed"):
        cli._run_deploy_preflight({}, SimpleNamespace(reports_dir=tmp_path), manifest={})
    assert not path.exists()
