"""Opt-in Flux controller proof on a disposable kind-cxcli-retirement-* cluster.

The caller owns cluster setup/teardown; never point this lane at a deployment.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from nebius_cxcli.soperator_graph_transition import (
    HR,
    SCHEMA,
    NativeGraphTransition,
    capture_transition,
    spec_digest,
    witness,
)
from nebius_cxcli.soperator_observability_scope import OWNED_WRITER, WRITER
from nebius_cxcli.soperator_operation import soperator_sha256

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    selected = os.environ.get("CXCLI_RETIREMENT_KUBECONFIG")
    if not selected:
        pytest.skip("Requires an isolated kind-cxcli-retirement-* controller fixture")
    config = yaml.safe_load(Path(selected).read_text())
    assert str(config.get("current-context", "")).startswith("kind-cxcli-retirement-")
    assert all(
        row["cluster"]["server"].startswith("https://127.0.0.1:") for row in config["clusters"]
    )
    env = {**os.environ, "KUBECONFIG": selected}

    def run(args, *, input_text=None):
        return subprocess.run(
            args, env=env, input=input_text, capture_output=True, text=True, check=True, timeout=120
        )

    def apply(documents):
        run(["kubectl", "apply", "-f", "-"], input_text=yaml.safe_dump_all(documents))

    root = tmp_path_factory.mktemp("native-charts")
    version = "1.0.0"
    raw = root / "raw"
    parent = root / "parent"
    for chart in (raw, parent):
        (chart / "templates").mkdir(parents=True)
        (chart / "Chart.yaml").write_text(
            yaml.safe_dump({"apiVersion": "v2", "name": chart.name, "version": version})
        )
    (raw / "templates/resources.yaml").write_text(
        "{{ range .Values.resources }}\n---\n{{ toYaml . }}\n{{ end }}\n"
    )
    (raw / "templates/agent.yaml").write_text("""{{ if .Values.vmagent }}
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMAgent
metadata:
  name: {{ .Release.Name }}
  namespace: {{ .Release.Namespace }}
spec:
{{ .Values.vmagent.spec | toYaml | indent 2 }}
{{ end }}
""")
    (parent / "values.yaml").write_text("observability:\n  publicEndpointEnabled: true\n")
    (
        parent / "templates/writer.yaml"
    ).write_text("""{{ if .Values.observability.publicEndpointEnabled }}
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: cxcli-soperator-fluxcd-tsa-token-writer
  namespace: flux-system
  labels:
    nebius.ai/soperator-release-graph: "true"
spec:
  interval: 1s
  releaseName: soperator-fluxcd-tsa-token-writer
  targetNamespace: monitoring
  chartRef:
    kind: HelmChart
    name: fixture-raw
  values:
    resources:
    - apiVersion: v1
      kind: ServiceAccount
      metadata:
        name: soperator-fluxcd-tsa-token-writer
        namespace: monitoring
    - apiVersion: rbac.authorization.k8s.io/v1
      kind: Role
      metadata:
        name: soperator-fluxcd-tsa-token-writer
        namespace: monitoring
      rules: []
    - apiVersion: rbac.authorization.k8s.io/v1
      kind: RoleBinding
      metadata:
        name: soperator-fluxcd-tsa-token-writer
        namespace: monitoring
      roleRef:
        apiGroup: rbac.authorization.k8s.io
        kind: Role
        name: soperator-fluxcd-tsa-token-writer
      subjects:
      - kind: ServiceAccount
        name: soperator-fluxcd-tsa-token-writer
        namespace: monitoring
    - apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: soperator-fluxcd-tsa-token-writer
        namespace: monitoring
      spec:
        replicas: 1
        selector:
          matchLabels:
            app: retirement-writer
        template:
          metadata:
            labels:
              app: retirement-writer
          spec:
            serviceAccountName: soperator-fluxcd-tsa-token-writer
            terminationGracePeriodSeconds: 1
            containers:
            - name: idle
              image: busybox:1.37.0
              command: [sh, -c, "sleep 3600"]
{{ end }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Release.Name }}-retained
  namespace: flux-system
data:
  retained: "true"
""")
    # Use the production label constant rather than an approximate fixture label.
    from nebius_cxcli.soperator_flux_graph import SOPERATOR_GRAPH_LABEL, SOPERATOR_GRAPH_LABEL_VALUE

    template = parent / "templates/writer.yaml"
    template.write_text(
        template.read_text().replace(
            'nebius.ai/soperator-release-graph: "true"',
            f'{SOPERATOR_GRAPH_LABEL}: "{SOPERATOR_GRAPH_LABEL_VALUE}"',
        )
    )
    (parent / "templates/metrics.yaml").write_text(
        """{{ if .Values.fixtureMetrics }}
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: {{ .Release.Name }}-vm-stack
  namespace: flux-system
  labels:
    __GRAPH_LABEL__: "__GRAPH_VALUE__"
spec:
  interval: 1s
  suspend: true
  releaseName: {{ .Release.Name }}-vm-stack
  install:
    disableWait: true
  upgrade:
    disableWait: true
    remediation:
      retries: 0
      remediateLastFailure: true
  chartRef:
    kind: HelmChart
    name: fixture-raw
  values:
    resources:
    - apiVersion: v1
      kind: ConfigMap
      metadata:
        name: {{ .Release.Name }}-metrics-sentinel
        namespace: flux-system
      data:
        fixture: "true"
    vmagent:
      spec:
        remoteWriteSettings: {{ .Values.fixtureMetrics | toJson }}
{{ end }}
""".replace("__GRAPH_LABEL__", SOPERATOR_GRAPH_LABEL).replace(
            "__GRAPH_VALUE__", SOPERATOR_GRAPH_LABEL_VALUE
        )
    )
    for chart in (raw, parent):
        run(["helm", "package", str(chart), "-d", str(root)])
    run(["helm", "repo", "index", str(root), "--url", "http://fixture-charts.flux-system.svc"])
    files = [*root.glob("*.tgz"), root / "index.yaml"]
    apply(
        [
            {
                "apiVersion": "apiextensions.k8s.io/v1",
                "kind": "CustomResourceDefinition",
                "metadata": {"name": "vmagents.operator.victoriametrics.com"},
                "spec": {
                    "group": "operator.victoriametrics.com",
                    "scope": "Namespaced",
                    "names": {"plural": "vmagents", "singular": "vmagent", "kind": "VMAgent"},
                    "versions": [
                        {
                            "name": "v1beta1",
                            "served": True,
                            "storage": True,
                            "schema": {
                                "openAPIV3Schema": {
                                    "type": "object",
                                    "properties": {
                                        "spec": {
                                            "type": "object",
                                            "properties": {
                                                "remoteWriteSettings": {
                                                    "type": "object",
                                                    "properties": {
                                                        "label": {
                                                            "type": "object",
                                                            "additionalProperties": {
                                                                "type": "string"
                                                            },
                                                        }
                                                    },
                                                }
                                            },
                                        }
                                    },
                                }
                            },
                        }
                    ],
                },
            },
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "monitoring"}},
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "fixture-charts", "namespace": "flux-system"},
                "binaryData": {p.name: base64.b64encode(p.read_bytes()).decode() for p in files},
            },
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "fixture-charts", "namespace": "flux-system"},
                "spec": {
                    "selector": {"matchLabels": {"app": "fixture-charts"}},
                    "template": {
                        "metadata": {"labels": {"app": "fixture-charts"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "http",
                                    "image": "busybox:1.37.0",
                                    "command": ["httpd", "-f", "-p", "8080", "-h", "/charts"],
                                    "volumeMounts": [{"name": "charts", "mountPath": "/charts"}],
                                }
                            ],
                            "volumes": [
                                {"name": "charts", "configMap": {"name": "fixture-charts"}}
                            ],
                        },
                    },
                },
            },
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "fixture-charts", "namespace": "flux-system"},
                "spec": {
                    "selector": {"app": "fixture-charts"},
                    "ports": [{"port": 80, "targetPort": 8080}],
                },
            },
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "retirement-sentinel", "namespace": "monitoring"},
            },
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": "retirement-sentinel", "namespace": "monitoring"},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "1Mi"}},
                },
            },
        ]
    )
    sources = [
        {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "HelmRepository",
            "metadata": {"name": "fixture", "namespace": "flux-system"},
            "spec": {"interval": "1s", "url": "http://fixture-charts.flux-system.svc"},
        }
    ]
    for name in ("raw", "parent"):
        sources.append(
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "HelmChart",
                "metadata": {"name": f"fixture-{name}", "namespace": "flux-system"},
                "spec": {
                    "interval": "1s",
                    "chart": name,
                    "version": version,
                    "sourceRef": {"kind": "HelmRepository", "name": "fixture"},
                },
            }
        )
    apply(sources)
    run(
        [
            "kubectl",
            "-n",
            "flux-system",
            "wait",
            "helmchart/fixture-parent",
            "helmchart/fixture-raw",
            "--for=condition=Ready",
            "--timeout=120s",
        ]
    )
    cache = root / "charts"
    cache.mkdir()
    payload = (root / f"raw-{version}.tgz").read_bytes()
    (cache / (hashlib.sha256(payload).hexdigest() + ".tgz")).write_bytes(payload)
    return (
        run,
        apply,
        sources,
        "sha256:" + hashlib.sha256((root / f"raw-{version}.tgz").read_bytes()).hexdigest(),
        root,
    )


def test_real_flux_uninstall_replay_preserves_credentials_and_storage(cluster):
    run, apply, sources, digest, _ = cluster

    def get(kind, name, namespace="flux-system"):
        raw = run(
            ["kubectl", "-n", namespace, "get", kind, name, "--ignore-not-found", "-o", "json"]
        ).stdout
        return json.loads(raw) if raw.strip() else {}

    parent = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "retirement-parent", "namespace": "flux-system"},
        "spec": {
            "interval": "1s",
            "releaseName": "retirement-parent",
            "chartRef": {"kind": "HelmChart", "name": "fixture-parent"},
            "values": {"observability": {"publicEndpointEnabled": True}},
        },
    }
    apply([parent])
    for name in ("retirement-parent", OWNED_WRITER):
        deadline = time.monotonic() + 120
        while not get(HR, name):
            assert time.monotonic() < deadline
            time.sleep(1)
        run(
            [
                "kubectl",
                "-n",
                "flux-system",
                "wait",
                "hr/" + name,
                "--for=condition=Ready",
                "--timeout=120s",
            ]
        )
    sentinel = {
        kind: get(kind, "retirement-sentinel", "monitoring")["metadata"]["uid"]
        for kind in ("secret", "pvc")
    }
    parent = get(HR, "retirement-parent")
    child = get(HR, OWNED_WRITER)
    desired = copy.deepcopy(parent)
    desired["spec"]["values"]["observability"]["publicEndpointEnabled"] = False
    graph = {"releases": []}
    writer = {
        "releaseName": OWNED_WRITER,
        "upstreamReleaseName": WRITER,
        "namespace": "flux-system",
        "sourceKind": "HelmChart",
        "sourceName": "fixture-raw",
        "revision": digest,
        "dependencies": [],
        "stage": 0,
    }
    state = capture_transition(
        resources=[parent, child],
        desired=graph,
        outer=desired,
        target={"targetRef": "fixture"},
        run=run,
        predecessor_graph={"releases": [writer]},
        sources=sources,
    )
    checkpoint = {}

    def persist(value):
        checkpoint.clear()
        checkpoint.update(copy.deepcopy(value))

    operation = NativeGraphTransition(state, run=run, persist=persist, authority=lambda: None)
    operation.fence()
    assert get(HR, "retirement-parent")["spec"]["suspend"] is True
    assert get(HR, OWNED_WRITER)["spec"]["suspend"] is True

    # Lose the response after the API accepts the parent mutation, then reload.
    def interrupted(args, **kwargs):
        result = run(args, **kwargs)
        if "patch" in args and "retirement-parent" in args and "--dry-run=server" not in args:
            raise RuntimeError("simulated lost parent response")
        return result

    operation.run = interrupted
    with pytest.raises(RuntimeError, match="lost parent response"):
        operation.publish(desired)
    replay = NativeGraphTransition(checkpoint, run=run, persist=persist, authority=lambda: None)
    replay.fence()
    replay.publish(desired)
    replay.wait_absent(timeout=120, interval=1)
    assert get(HR, OWNED_WRITER) == {}
    assert get("deployment", WRITER, "monitoring") == {}
    assert (
        json.loads(
            run(
                [
                    "kubectl",
                    "-n",
                    "monitoring",
                    "get",
                    "pods",
                    "-l",
                    "app=retirement-writer",
                    "-o",
                    "json",
                ]
            ).stdout
        )["items"]
        == []
    )
    assert get("configmap", "retirement-parent-retained")
    for kind, uid in sentinel.items():
        assert get(kind, "retirement-sentinel", "monitoring")["metadata"]["uid"] == uid
    replay.suspend_parent(True)
    replay.publish_parent({**desired, "spec": {**desired["spec"], "suspend": True}})
    replay.suspend_parent(False)
    replay.verify_readonly()


def test_negative_control_suspended_prune_orphans_workload(cluster):
    """Independent fixture control, never used as product success evidence."""
    run, apply, _, digest, chart_root = cluster
    parent = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "negative-parent", "namespace": "flux-system"},
        "spec": {
            "interval": "1s",
            "releaseName": "negative-parent",
            "chartRef": {"kind": "HelmChart", "name": "fixture-parent"},
            "values": {"observability": {"publicEndpointEnabled": True}},
        },
    }
    apply([parent])
    run(
        [
            "kubectl",
            "-n",
            "flux-system",
            "wait",
            "hr/negative-parent",
            "--for=condition=Ready",
            "--timeout=120s",
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            "flux-system",
            "wait",
            "hr/" + OWNED_WRITER,
            "--for=condition=Ready",
            "--timeout=120s",
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            "flux-system",
            "patch",
            "hr",
            OWNED_WRITER,
            "--type=merge",
            "-p",
            '{"spec":{"suspend":true}}',
        ]
    )
    parent["spec"]["values"]["observability"]["publicEndpointEnabled"] = False
    apply([parent])
    run(
        [
            "kubectl",
            "-n",
            "flux-system",
            "wait",
            "--for=delete",
            "hr/" + OWNED_WRITER,
            "--timeout=120s",
        ]
    )
    workload = json.loads(
        run(["kubectl", "-n", "monitoring", "get", "deployment", WRITER, "-o", "json"]).stdout
    )
    assert workload["metadata"]["annotations"]["meta.helm.sh/release-name"] == WRITER
    assert json.loads(
        run(
            [
                "kubectl",
                "-n",
                "monitoring",
                "get",
                "pods",
                "-l",
                "app=retirement-writer",
                "-o",
                "json",
            ]
        ).stdout
    )["items"]


def test_parent_server_side_apply_empty_remote_write_settings(cluster, monkeypatch):
    """Qualify the actual Helm/SSA representation at the child boundary."""
    run, apply, _, digest, chart_root = cluster
    parent = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "metrics-parent", "namespace": "flux-system"},
        "spec": {
            "interval": "1s",
            "install": {"disableWait": True},
            "upgrade": {"disableWait": True},
            "chartRef": {"kind": "HelmChart", "name": "fixture-parent"},
            "values": {
                "observability": {"publicEndpointEnabled": False},
                "fixtureMetrics": {"label": {"cluster": "fixture"}},
            },
        },
    }

    def get(name):
        return json.loads(
            run(
                [
                    "kubectl",
                    "-n",
                    "flux-system",
                    "get",
                    HR,
                    name,
                    "-o",
                    "json",
                ]
            ).stdout
        )

    def reconcile():
        apply([parent])
        deadline = time.monotonic() + 120
        while True:
            observed = get("metrics-parent")
            if observed.get("status", {}).get("observedGeneration") == observed["metadata"][
                "generation"
            ] and any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in observed.get("status", {}).get("conditions", [])
            ):
                return observed
            assert time.monotonic() < deadline
            time.sleep(1)

    reconcile()
    name = "metrics-parent-vm-stack"
    before = get(name)
    assert before["spec"]["values"]["vmagent"]["spec"]["remoteWriteSettings"] == {
        "label": {"cluster": "fixture"}
    }
    run(
        [
            "kubectl",
            "-n",
            "flux-system",
            "patch",
            "hr",
            name,
            "--type=merge",
            "-p",
            '{"spec":{"suspend":false}}',
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            "flux-system",
            "wait",
            "hr/" + name,
            "--for=condition=Ready",
            "--timeout=120s",
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            "flux-system",
            "patch",
            "hr",
            name,
            "--type=merge",
            "-p",
            '{"spec":{"suspend":true}}',
        ]
    )
    parent["spec"]["postRenderers"] = [
        {
            "kustomize": {
                "patches": [
                    {
                        "target": {"kind": "HelmRelease", "name": name},
                        "patch": json.dumps(
                            [
                                {
                                    "op": "replace",
                                    "path": "/spec/values/vmagent/spec/remoteWriteSettings",
                                    "value": {},
                                }
                            ]
                        ),
                    }
                ]
            }
        }
    ]
    reconcile()
    after = get(name)
    assert after["metadata"]["uid"] == before["metadata"]["uid"]
    # SSA prunes the last previously owned map entry into a null postimage.
    # Do not mistake that postimage for the explicit empty map Helm published.
    assert after["spec"]["values"]["vmagent"]["spec"]["remoteWriteSettings"] is None
    current_parent = get("metrics-parent")
    graph = {
        "releases": [
            {
                "namespace": "flux-system",
                "releaseName": name,
                "upstreamReleaseName": "soperator-fluxcd-vm-stack",
                "sourceKind": "HelmChart",
                "sourceName": "fixture-raw",
                "revision": digest,
            }
        ]
    }
    admission = {"parent": witness(current_parent), "desiredGraphSha256": soperator_sha256(graph)}
    checkpoint = {}

    def persist(value):
        checkpoint.clear()
        checkpoint.update(copy.deepcopy(value))

    operation = NativeGraphTransition(
        {
            "schema": SCHEMA,
            "admission": admission,
            "admissionSha256": soperator_sha256(admission),
            "phase": "verified-absent",
            "publishedSpecSha256": spec_digest(current_parent),
        },
        run=run,
        persist=persist,
        authority=lambda: None,
    )
    operation.suspend_parent(True)
    operation.resume_child("flux-system", name)
    materialized = get(name)
    assert materialized["metadata"]["uid"] == before["metadata"]["uid"]
    assert materialized["spec"]["values"]["vmagent"]["spec"]["remoteWriteSettings"] == {}
    assert materialized["spec"]["suspend"] is False
    assert next(iter(checkpoint["childMaterializations"].values()))["status"] == "verified"
    run(
        [
            "kubectl",
            "-n",
            "flux-system",
            "wait",
            "hr/" + name,
            "--for=condition=Stalled",
            "--timeout=120s",
        ]
    )
    failed = get(name)
    assert any(
        "remoteWriteSettings" in c.get("message", "") and "null" in c.get("message", "")
        for c in failed["status"]["conditions"]
    )
    assert failed["status"]["history"][0]["action"] == "rollback"
    assert failed["status"]["history"][0]["status"] == "deployed"
    from nebius_cxcli.soperator_vmagent_recovery import recover_stage

    monkeypatch.setattr(
        "nebius_cxcli.soperator_vmagent_recovery.default_soperator_source_cache_root",
        lambda: chart_root,
    )

    deadline = time.monotonic() + 120
    while recover_stage(operation, graph, get(name)):
        assert time.monotonic() < deadline
        time.sleep(1)
    assert checkpoint["vmagentMaterialization"]["phase"] == "converged"
    assert (
        get(name)["status"]["lastHandledResetAt"] == checkpoint["vmagentMaterialization"]["token"]
    )
    installed = json.loads(
        run(["helm", "get", "values", name, "-n", "flux-system", "-o", "json"]).stdout
    )
    assert installed["vmagent"]["spec"]["remoteWriteSettings"] == {}
    agent = json.loads(
        run(["kubectl", "-n", "flux-system", "get", "vmagent", name, "-o", "json"]).stdout
    )
    assert agent["spec"]["remoteWriteSettings"] == {}
