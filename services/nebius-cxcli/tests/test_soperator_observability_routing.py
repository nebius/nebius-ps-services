"""Route real frozen upstream templates, including disabled (null) pipelines."""

import copy
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import grafana_install, soperator_release_resolver
from nebius_cxcli.observability_backends import metrics_destinations
from nebius_cxcli.observability_routing import local_url, target_settings
from nebius_cxcli.soperator_observability_routing import (
    bind_routing,
    child_patches,
    route_documents,
)
from test_observability_routing import payload
from test_soperator_configuration_render import frozen_charts_for


@pytest.mark.parametrize("mode", ["local", "remote", "both"])
def test_real_frozen_native_templates_have_exact_destinations(tmp_path, monkeypatch, mode):
    snapshot, receipt, source = frozen_charts_for(tmp_path, "4.1.8")
    frozen = SimpleNamespace(
        snapshot=snapshot,
        source=receipt,
        source_context=SimpleNamespace(
            source=receipt, umbrella=snapshot.umbrella, charts=snapshot.charts
        ),
    )
    monkeypatch.setattr(
        soperator_release_resolver, "current_frozen_soperator_release", lambda *a, **k: frozen
    )
    data = payload()
    data["apps"]["charts"].append(
        {
            "id": "soperator",
            "instance_id": "cluster",
            "enabled": True,
            "version": "4.1.8",
            "namespace": "flux-system",
            "release-name": "soperator-fluxcd",
            "values": {},
        }
    )
    data = grafana_install.configure(
        data, "cluster", overrides={"metrics_storage": mode, "logs_storage": mode}
    )
    values = yaml.safe_load((source / "helm/soperator-fluxcd/values.yaml").read_text())
    values["notifier"]["enabled"] = False
    bind_routing(data, "cluster", values)
    path = tmp_path / "routing.yaml"
    path.write_text(yaml.safe_dump(values))
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "soperator-fluxcd",
            str(source / "helm/soperator-fluxcd"),
            "-f",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    original = [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]
    routed = route_documents(original, values)
    releases = {doc["metadata"]["name"]: doc for doc in routed if doc["kind"] == "HelmRelease"}
    stack = releases["soperator-fluxcd-vm-stack"]
    assert (
        stack["spec"]["releaseName"]
        == target_settings(data, "cluster")["native_backends"]["vmStack"]["releaseName"]
    )
    assert stack["spec"]["values"]["vmagent"]["spec"]["remoteWrite"] == metrics_destinations(
        data, "cluster"
    )
    assert stack["spec"]["values"]["vmagent"]["additionalRemoteWrites"] == []
    assert "vmsingle-metrics-victoria-metrics-k8s-stack" in local_url(data, "cluster", "metrics")
    collectors = [doc for name, doc in releases.items() if "opentelemetry-collector-" in name]
    assert len(collectors) == 3
    for doc in collectors:
        config = doc["spec"]["values"]["config"]
        assert config["service"]["pipelines"]["metrics"] is None
        assert config["service"]["pipelines"]["traces"] is None
        log_pipelines = [
            pipeline
            for name, pipeline in config["service"]["pipelines"].items()
            if name.split("/")[0] == "logs" and pipeline is not None
        ]
        assert log_pipelines
        assert all(
            len(pipeline["exporters"]) == (2 if mode == "both" else 1) for pipeline in log_pipelines
        )
        if mode == "local":
            assert "nebius.cloud" not in str(config["exporters"])
    assert len(child_patches(original, routed)) == 5
    assert route_documents(routed, values) == routed
    assert all(
        "tsa-token-writer" not in doc["metadata"]["name"] for doc in routed if doc.get("metadata")
    )
    # Removing Pushgateway also removes a previously rendered managed scrape job.
    previous = copy.deepcopy(routed)
    prior = next(
        doc
        for doc in previous
        if doc.get("metadata", {}).get("name") == "soperator-fluxcd-vm-stack"
    )
    prior["spec"]["values"]["vmagent"]["spec"]["inlineScrapeConfig"] = (
        "- job_name: cxcli-pushgateway\n"
    )
    assert "cxcli-pushgateway" not in str(route_documents(previous, values))
    # Authored native retention remains independent from export placement.
    native = next(row for row in data["apps"]["charts"] if row["id"] == "soperator")
    native["values"] = {
        "observability": {
            "vmStack": {"values": {"vmsingle": {"spec": {"retentionPeriod": "120d"}}}},
            "vmLogs": {"values": {"server": {"retentionPeriod": "60d"}}},
        }
    }
    bind_routing(data, "cluster", values)
    retained = {
        doc["metadata"]["name"]: doc
        for doc in route_documents(original, values)
        if doc["kind"] == "HelmRelease"
    }
    assert (
        retained["soperator-fluxcd-vm-stack"]["spec"]["values"]["vmsingle"]["spec"][
            "retentionPeriod"
        ]
        == "120d"
    )
    assert (
        retained["soperator-fluxcd-vm-logs"]["spec"]["values"]["server"]["retentionPeriod"] == "60d"
    )
