"""Deploy smoke uses the upstream graph rather than legacy chart object names."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_deploy_validation as validation


@pytest.fixture
def setup(tmp_path, monkeypatch):
    reports = tmp_path / "generated" / "reports"
    graph = reports.parent / "flux" / "targets" / "cluster" / "soperator-release-graph.yaml"
    graph.parent.mkdir(parents=True)
    graph.write_text("frozen graph")
    contract = {
        "clusterName": "cluster",
        "readiness": {"storage": [{"name": "jail-rootfs-slot-a-pvc"}]},
    }
    monkeypatch.setattr(validation, "_rendered_soperator_graph_contract", lambda _: contract)
    monkeypatch.setattr(
        validation, "_flux_wait_targets", lambda _: [SimpleNamespace(is_soperator_main=True)]
    )
    calls = []

    def readiness(actual, *, env):
        assert actual == contract
        calls.append(actual)
        return True, "complete upstream graph Ready"

    monkeypatch.setattr(validation, "_soperator_product_readiness", readiness)
    monkeypatch.setattr(
        validation.subprocess, "run", lambda *_a, **_k: pytest.fail("unexpected subprocess")
    )
    spec = {
        "kind": "soperator_cluster_smoke",
        "target_ref": "cluster",
        "cluster_name": "cluster",
        "readiness_timeout_seconds": 0,
        "report_file": "deploy-smoke-report-cluster.json",
    }
    return reports, graph, contract, spec, calls


def test_deploy_uses_canonical_graph_and_report_contract(setup):
    reports, _, _, spec, calls = setup
    paths = validation.run_soperator_deploy_validations([spec], reports_dir=reports)
    assert len(calls) == 1
    report = json.loads(paths[0].read_text())
    assert paths[0].name == "deploy-smoke-report-cluster.json"
    assert report["passed"] and report["scope"]
    assert report["release_graph_sha256"]


@pytest.mark.parametrize(
    "change", ["missing", "foreign", "main", "target", "report", "context", "drift", "unready"]
)
def test_deploy_rejects_unproven_graph_or_product(setup, monkeypatch, change):
    reports, graph, contract, spec, calls = setup
    if change == "missing":
        monkeypatch.setattr(validation, "_rendered_soperator_graph_contract", lambda _: None)
    elif change == "foreign":
        contract["clusterName"] = "foreign"
    elif change == "main":
        monkeypatch.setattr(validation, "_flux_wait_targets", lambda _: [])
    elif change == "target":
        spec["target_ref"] = "../foreign"
    elif change == "report":
        spec["report_file"] = "../foreign.json"
    elif change == "context":
        spec["kube_context"] = "expected"
        monkeypatch.setattr(
            validation.subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout="foreign")
        )
    elif change == "drift":

        def changed(*_a, **_k):
            graph.write_text("changed")
            return True, "Ready"

        monkeypatch.setattr(validation, "_soperator_product_readiness", changed)
    else:
        monkeypatch.setattr(
            validation,
            "_soperator_product_readiness",
            lambda *_a, **_k: (False, "required current check failed"),
        )
    with pytest.raises((ValueError, RuntimeError)):
        validation.run_soperator_deploy_validations([spec], reports_dir=reports)
    assert not calls
    if change == "unready":
        assert not json.loads((reports / spec["report_file"]).read_text())["passed"]


def test_cli_deploy_batch_routes_to_upstream_validator(tmp_path, monkeypatch):
    from nebius_cxcli import cli

    calls = []
    monkeypatch.setattr(
        validation,
        "run_soperator_deploy_validations",
        lambda specs, **kw: calls.append((specs, kw)) or [],
    )
    monkeypatch.setattr(
        cli,
        "run_soperator_cluster_validations",
        lambda *_a, **_k: pytest.fail("legacy deploy smoke"),
    )
    assert (
        cli._run_deploy_validation_batch(
            "soperator",
            [{"target_ref": "cluster"}],
            reports_dir=tmp_path,
            extra_env=None,
            emit=None,
        )
        == []
    )
    assert calls[0][0] == [{"target_ref": "cluster"}]
