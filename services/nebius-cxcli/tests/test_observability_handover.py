from __future__ import annotations

import copy
from dataclasses import dataclass

import pytest
import yaml

from nebius_cxcli import observability_handover as handover
from nebius_cxcli.grafana_database import app_owner
from nebius_cxcli.observability_collectors import (
    COMPONENT_ANNOTATION,
    SIGNALS_ANNOTATION,
    inactive_release,
)
from test_observability_routing import payload


@dataclass
class Paths:
    flux_dir: object


@pytest.fixture
def cluster(monkeypatch, tmp_path):
    config = payload()
    owner = app_owner(config, "cluster")

    def release(component, signals):
        return {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {
                "name": component,
                "namespace": "observability",
                "annotations": {COMPONENT_ANNOTATION: component, SIGNALS_ANNOTATION: signals},
            },
            "spec": {"values": {"config": {"signals": signals}}},
        }

    source = release("nebius-observability-agent", "logs,metrics,traces")
    destination = release("opentelemetry-collector", "traces")
    desired_source = inactive_release(
        release("nebius-observability-agent", ""), "nebius-observability-agent"
    )
    documents = [desired_source, destination]
    for doc in documents:
        (tmp_path / (doc["metadata"]["name"] + ".yaml")).write_text(yaml.safe_dump(doc))
    alias = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": "cxcli-otlp",
            "namespace": "observability",
            "annotations": {handover.OWNER: owner},
        },
        "spec": {
            "type": "ExternalName",
            "externalName": "destination.observability.svc.cluster.local",
        },
    }
    (tmp_path / "service-cxcli-otlp.yaml").write_text(yaml.safe_dump(alias))
    objects = {
        ("helmrelease", source["metadata"]["name"]): source,
        ("service", "cxcli-otlp"): {
            **copy.deepcopy(alias),
            "spec": {
                "type": "ExternalName",
                "externalName": "source.observability.svc.cluster.local",
            },
        },
    }
    events = []

    def kubectl(args, env, body=None):
        if "get" in args:
            at = args.index("get")
            kind, name = args[at + 1 : at + 3]
            if kind == "crd":
                return {"metadata": {"name": name}}
            if kind == "namespace":
                return {"metadata": {"name": name, "uid": "cluster-uid"}}
            if kind == "pods":
                events.append("stopped")
                return {"items": []}
            return copy.deepcopy(objects.get((kind, name), {}))
        doc = copy.deepcopy(body)
        doc["metadata"]["resourceVersion"] = str(len(events))
        objects[(doc["kind"].lower(), doc["metadata"]["name"])] = doc
        events.append(doc.get("data", {}).get("phase", "switch-alias"))
        return doc

    monkeypatch.setattr(handover, "kubectl", kubectl)
    stages = []

    def apply(paths):
        stage = {}
        for file in paths.flux_dir.glob("*.yaml"):
            doc = yaml.safe_load(file.read_text())
            if doc.get("kind") == "HelmRelease":
                doc["metadata"]["generation"] = 1
                doc["status"] = {
                    "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}]
                }
                objects[("helmrelease", doc["metadata"]["name"])] = copy.deepcopy(doc)
                stage[doc["metadata"]["name"]] = doc
        assert objects[("service", "cxcli-otlp")]["spec"]["externalName"].startswith("source.")
        stages.append(stage)

    return Paths(tmp_path), config, objects, events, stages, apply


def test_handover_orders_quiescence_before_activation_and_alias(cluster):
    paths, config, objects, events, stages, apply = cluster
    before = {p.name: p.read_bytes() for p in paths.flux_dir.iterdir()}
    assert handover.handover(
        paths,
        config=config,
        target_ref="cluster",
        env={},
        apply=apply,
        assert_authority=lambda: None,
        emit=lambda _: None,
    )
    assert stages[0]["opentelemetry-collector"]["spec"]["postRenderers"]
    assert stages[0]["nebius-observability-agent"]["spec"].get("postRenderers") is None
    assert all(doc["spec"].get("postRenderers") for doc in stages[1].values())
    assert not stages[2]["opentelemetry-collector"]["spec"].get("postRenderers")
    assert (
        events.index("quiescing")
        < events.index("stopped")
        < events.index("quiesced")
        < events.index("switch-alias")
    )
    assert events[-1] == "activated"
    assert before == {p.name: p.read_bytes() for p in paths.flux_dir.iterdir()}


def test_resume_never_reactivates_quiesced_source(cluster):
    paths, config, objects, events, stages, apply = cluster

    def interrupted(paths):
        apply(paths)
        if len(stages) == 2:
            raise RuntimeError("interrupted after source stopped")

    with pytest.raises(RuntimeError, match="interrupted"):
        handover.handover(
            paths,
            config=config,
            target_ref="cluster",
            env={},
            apply=interrupted,
            assert_authority=lambda: None,
            emit=lambda _: None,
        )
    assert objects[("configmap", handover.RECEIPT)]["data"]["phase"] == "quiescing"
    handover.handover(
        paths,
        config=config,
        target_ref="cluster",
        env={},
        apply=apply,
        assert_authority=lambda: None,
        emit=lambda _: None,
    )
    assert all(doc["spec"].get("postRenderers") for doc in stages[2].values())
    assert events[-1] == "activated"


def test_changed_destination_during_interrupted_handover_is_rejected(cluster):
    paths, config, objects, events, stages, apply = cluster

    def interrupted(paths):
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError):
        handover.handover(
            paths,
            config=config,
            target_ref="cluster",
            env={},
            apply=interrupted,
            assert_authority=lambda: None,
            emit=lambda _: None,
        )
    file = paths.flux_dir / "opentelemetry-collector.yaml"
    desired = yaml.safe_load(file.read_text())
    desired["spec"]["values"]["different"] = True
    file.write_text(yaml.safe_dump(desired))
    with pytest.raises(RuntimeError, match="Resume the recorded"):
        handover.handover(
            paths,
            config=config,
            target_ref="cluster",
            env={},
            apply=apply,
            assert_authority=lambda: None,
            emit=lambda _: None,
        )
    assert not stages


def test_vmagent_quiescence_keeps_owned_cr_identity_across_deletion(monkeypatch):
    release = {
        "metadata": {
            "name": "stack",
            "namespace": "obs",
            "annotations": {COMPONENT_ANNOTATION: "victoria-metrics-k8s-stack"},
        }
    }
    calls = []
    present = True

    def kubectl(args, env):
        calls.append(args)
        if "crd" in args:
            return {"metadata": {"name": "vmagents.operator.victoriametrics.com"}}
        return (
            {
                "items": [
                    {
                        "metadata": {
                            "name": "custom-agent",
                            "annotations": {"meta.helm.sh/release-name": "stack"},
                        }
                    }
                ]
            }
            if present
            else {"items": []}
        )

    monkeypatch.setattr(handover, "kubectl", kubectl)
    selectors = handover._vmagent_selectors([release], {}, {})
    assert selectors == {
        "obs/stack": [
            "app.kubernetes.io/name=vmagent,app.kubernetes.io/instance=custom-agent,managed-by=vm-operator"
        ]
    }
    present = False
    import json

    assert (
        handover._vmagent_selectors(
            [release], {}, {"data": {"vmAgentSelectors": json.dumps(selectors)}}
        )
        == selectors
    )
    assert "app.kubernetes.io/instance=stack" in calls[-1]


def test_stale_ready_condition_cannot_authorize_activation(monkeypatch):
    release = {"metadata": {"name": "collector", "namespace": "obs"}}
    monkeypatch.setattr(
        handover,
        "_get",
        lambda *args: {
            "metadata": {"generation": 2},
            "status": {
                "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}]
            },
        },
    )
    ticks = iter([0, 181])
    monkeypatch.setattr(handover.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="generation is not reconciled"):
        handover._wait_reconciled([release], {}, lambda: None)
