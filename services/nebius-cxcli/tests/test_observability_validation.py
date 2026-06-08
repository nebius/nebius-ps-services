from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nebius_cxcli.observability_validation import run_observability_validations


def test_run_observability_validation_writes_pass_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    spec = {
        "kind": "mk8s_observability_ingestion",
        "target_ref": "cluster1",
        "name": "Observability ingestion (cluster1)",
        "namespace": "observability",
        "helmrelease_name": "nebius-observability-agent",
        "helmrelease_ready_condition": "Ready",
        "signal_value_paths": {
            "logs": "spec.values.config.logs.enabled",
            "metrics": "spec.values.config.metrics.enabled",
            "traces": "spec.values.config.traces.enabled",
        },
        "cluster_metric_targets_path": "spec.values.config.metrics.additionalTargets",
        "daemonset_name": "o11y-agent",
        "pod_selector": "app.kubernetes.io/instance=nebius-observability-agent",
        "pod_failure_sample_limit": 5,
        "trace_otlp_service": {
            "name": "nebius-observability-agent",
            "port": 4317,
            "endpoint_slice_selector": "kubernetes.io/service-name=nebius-observability-agent",
            "endpoint_slice_check_limit": 5,
        },
        "signals": {
            "logs": True,
            "metrics": True,
            "traces": True,
            "collect_k8s_cluster_metrics": True,
        },
        "report_file": "observability-ingestion-report-cluster1.json",
    }

    def _fake_kubectl_json(args: list[str], *, extra_env: dict[str, str] | None) -> dict[str, Any]:
        assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
        command = tuple(args)
        if command == (
            "-n",
            "observability",
            "get",
            "helmrelease.helm.toolkit.fluxcd.io",
            "nebius-observability-agent",
            "-o",
            "json",
        ):
            return {
                "spec": {
                    "values": {
                        "config": {
                            "logs": {"enabled": True},
                            "metrics": {
                                "enabled": True,
                                "additionalTargets": [{"job_name": "cxcli-kubernetes-nodes"}],
                            },
                            "traces": {"enabled": True},
                        }
                    }
                },
                "status": {
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "True",
                            "reason": "UpgradeSucceeded",
                            "message": "upgrade succeeded",
                        }
                    ]
                },
            }
        if command == (
            "-n",
            "observability",
            "get",
            "daemonset.apps",
            "o11y-agent",
            "-o",
            "json",
        ):
            return {
                "metadata": {"name": "o11y-agent"},
                "status": {
                    "desiredNumberScheduled": 2,
                    "numberReady": 2,
                    "updatedNumberScheduled": 2,
                    "numberAvailable": 2,
                    "numberMisscheduled": 0,
                },
            }
        if command == (
            "-n",
            "observability",
            "get",
            "service",
            "nebius-observability-agent",
            "-o",
            "json",
        ):
            return {"spec": {"ports": [{"name": "grpc", "port": 4317}]}}
        raise AssertionError(f"unexpected kubectl args: {args}")

    def _fake_kubectl_raw_json(
        path: str,
        *,
        extra_env: dict[str, str] | None,
    ) -> dict[str, Any]:
        assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
        assert path == (
            "/apis/discovery.k8s.io/v1/namespaces/observability/endpointslices"
            "?labelSelector=kubernetes.io%2Fservice-name%3Dnebius-observability-agent"
            "&limit=5"
        )
        return {
            "items": [
                {
                    "endpoints": [
                        {"conditions": {"ready": True}},
                        {"conditions": {"ready": True}},
                    ]
                }
            ]
        }

    monkeypatch.setattr("nebius_cxcli.observability_validation._kubectl_json", _fake_kubectl_json)
    monkeypatch.setattr(
        "nebius_cxcli.observability_validation._kubectl_raw_json",
        _fake_kubectl_raw_json,
    )

    reports = run_observability_validations(
        [spec],
        reports_dir=tmp_path,
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
    )

    assert reports == [tmp_path / "observability-ingestion-report-cluster1.json"]
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert [item["name"] for item in payload["checks"]] == [
        "HelmRelease Ready",
        "Agent signal config",
        "Agent DaemonSet Ready",
        "Trace OTLP Service Ready",
    ]


def test_run_observability_validation_treats_unknown_otlp_endpoint_readiness_as_not_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    spec = {
        "kind": "mk8s_observability_ingestion",
        "target_ref": "cluster1",
        "name": "Observability ingestion (cluster1)",
        "namespace": "observability",
        "helmrelease_name": "nebius-observability-agent",
        "helmrelease_ready_condition": "Ready",
        "signal_value_paths": {
            "logs": "spec.values.config.logs.enabled",
            "metrics": "spec.values.config.metrics.enabled",
            "traces": "spec.values.config.traces.enabled",
        },
        "cluster_metric_targets_path": "spec.values.config.metrics.additionalTargets",
        "daemonset_name": "o11y-agent",
        "pod_selector": "app.kubernetes.io/instance=nebius-observability-agent",
        "pod_failure_sample_limit": 5,
        "trace_otlp_service": {
            "name": "nebius-observability-agent",
            "port": 4317,
            "endpoint_slice_selector": "kubernetes.io/service-name=nebius-observability-agent",
            "endpoint_slice_check_limit": 5,
        },
        "signals": {"traces": True},
        "report_file": "observability-ingestion-report-cluster1.json",
    }

    def _fake_kubectl_json(args: list[str], *, extra_env: dict[str, str] | None) -> dict[str, Any]:
        command = tuple(args)
        if command == (
            "-n",
            "observability",
            "get",
            "helmrelease.helm.toolkit.fluxcd.io",
            "nebius-observability-agent",
            "-o",
            "json",
        ):
            return {
                "spec": {
                    "values": {
                        "config": {
                            "logs": {"enabled": False},
                            "metrics": {"enabled": False},
                            "traces": {"enabled": True},
                        }
                    }
                },
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        if command == (
            "-n",
            "observability",
            "get",
            "daemonset.apps",
            "o11y-agent",
            "-o",
            "json",
        ):
            return {
                "metadata": {"name": "o11y-agent"},
                "status": {
                    "desiredNumberScheduled": 1,
                    "numberReady": 1,
                    "updatedNumberScheduled": 1,
                    "numberAvailable": 1,
                    "numberMisscheduled": 0,
                },
            }
        if command == (
            "-n",
            "observability",
            "get",
            "service",
            "nebius-observability-agent",
            "-o",
            "json",
        ):
            return {"spec": {"ports": [{"name": "grpc", "port": 4317}]}}
        raise AssertionError(f"unexpected kubectl args: {args}")

    def _fake_kubectl_raw_json(
        path: str,
        *,
        extra_env: dict[str, str] | None,
    ) -> dict[str, Any]:
        return {"items": [{"endpoints": [{"conditions": {}}, {"conditions": {"ready": None}}]}]}

    monkeypatch.setattr("nebius_cxcli.observability_validation._kubectl_json", _fake_kubectl_json)
    monkeypatch.setattr(
        "nebius_cxcli.observability_validation._kubectl_raw_json",
        _fake_kubectl_raw_json,
    )

    with pytest.raises(RuntimeError, match="No ready OTLP/gRPC endpoint found"):
        run_observability_validations([spec], reports_dir=tmp_path, extra_env={})

    reports = [tmp_path / "observability-ingestion-report-cluster1.json"]
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    otlp_check = next(item for item in payload["checks"] if item["name"] == "Trace OTLP Service Ready")
    assert payload["passed"] is False
    assert otlp_check["passed"] is False
    assert otlp_check["summary"] == "No ready OTLP/gRPC endpoint found"
