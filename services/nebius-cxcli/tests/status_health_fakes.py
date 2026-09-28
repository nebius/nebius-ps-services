"""Attributed upstream-shaped status observations; no live calls."""

from __future__ import annotations

from typing import Any

from nebius_cxcli.soperator_flux_graph import SOPERATOR_GRAPH_LABEL, SOPERATOR_GRAPH_LABEL_VALUE


def reference(owner: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": owner["kind"],
        "apiVersion": owner["apiVersion"],
        "name": owner["metadata"]["name"],
        "uid": owner["metadata"]["uid"],
        "controller": True,
    }


def resource(kind: str, name: str, namespace: str = "soperator", **spec: Any) -> dict[str, Any]:
    api = (
        "v1"
        if kind == "Pod"
        else "apps.kruise.io/v1beta1"
        if kind == "StatefulSet"
        else "apps/v1"
        if kind in {"Deployment", "ReplicaSet", "DaemonSet"}
        else "slurm.nebius.ai/v1"
    )
    return {
        "apiVersion": api,
        "kind": kind,
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": f"{kind}-{name}",
            "generation": 1,
            "labels": {},
            "annotations": {},
        },
        "spec": spec,
        "status": {"observedGeneration": 1},
    }


def healthy_snapshot() -> dict[str, Any]:
    cluster = resource(
        "SlurmCluster",
        "soperator",
        slurmNodes={
            "controller": {"size": 2},
            "login": {"size": 1},
            "accounting": {"size": 1, "enabled": True, "mariadbOperator": {"enabled": True}},
            "rest": {"size": 1, "enabled": True},
            "exporter": {"size": 1, "enabled": True},
        },
        sConfigController={"node": {"size": 1}},
    )
    nodeset = resource("NodeSet", "worker", replicas=2)
    nodeset["metadata"]["annotations"]["slurm.nebius.ai/parental-cluster-ref"] = "soperator"
    database = resource("MariaDB", "accounting-db")
    database["metadata"]["ownerReferences"] = [reference(cluster)]
    database["status"]["conditions"] = [
        {"type": "Ready", "status": "True", "observedGeneration": 1}
    ]
    workloads = []
    for role, kind, count in (
        ("controller", "StatefulSet", 1),
        ("login", "StatefulSet", 1),
        ("accounting", "Deployment", 1),
        ("rest", "Deployment", 1),
        ("exporter", "Deployment", 1),
        ("sconfigcontroller", "Deployment", 1),
        ("nodeset", "StatefulSet", 2),
        ("operator", "Deployment", 1),
    ):
        name = "worker" if role == "nodeset" else role
        workload = resource(
            kind, name, "soperator-system" if role == "operator" else "soperator", replicas=count
        )
        workload["metadata"]["ownerReferences"] = [
            reference(nodeset if role == "nodeset" else cluster)
        ]
        labels = workload["metadata"]["labels"]
        labels.update(
            {
                "app.kubernetes.io/name": "slurmcluster",
                "app.kubernetes.io/instance": "soperator",
                "app.kubernetes.io/component": role,
            }
        )
        if role == "controller":
            labels["slurm.nebius.ai/controller-type"] = "main"
        if role == "nodeset":
            labels.update({"slurm.nebius.ai/nodeset": "worker", "slurm.nebius.ai/worker": "true"})
        if role == "operator":
            labels["control-plane"] = "controller-manager"
            workload["metadata"]["annotations"] = {
                "meta.helm.sh/release-name": "soperator-controller",
                "meta.helm.sh/release-namespace": "soperator-system",
            }
            workload["metadata"].pop("ownerReferences")
        workload["status"]["readyReplicas"] = count
        workloads.append(workload)
        owner = workload
        if kind == "Deployment":
            owner = resource("ReplicaSet", name + "-rs", workload["metadata"]["namespace"])
            owner["metadata"]["ownerReferences"] = [reference(workload)]
            workloads.append(owner)
        for i in range(count):
            pod = resource(
                "Pod",
                f"{name}-{i}",
                workload["metadata"]["namespace"],
                containers=[{"name": "sshd" if role == "login" else role}],
            )
            pod["metadata"]["ownerReferences"] = [reference(owner)]
            pod["status"] = {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
            }
            workloads.append(pod)
    release = {
        "name": "soperator-controller",
        "namespace": "soperator-system",
        "storage_namespace": "flux-system",
        "status": "deployed",
        "chart_version": "4.1.7+build",
        "app_version": "4.1.7",
    }
    return {
        "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
        "soperator_resources": [cluster, nodeset, database],
        "workloads": workloads,
        "status_release": release,
        "helm_releases": [release],
        "status_issues": [],
        "status_check_history": {"state": "collected", "detail": ""},
        "failed_namespaces": [],
        "collection_errors": [],
        "slurm_ping": {"state": "succeeded", "up": 1, "total": 1},
        "slurm_nodes": {
            "state": "succeeded",
            "nodes": {"worker-0": {"State": "IDLE"}, "worker-1": {"State": "ALLOCATED"}},
        },
    }


def add_flux_graph(snapshot: dict[str, Any], *, configurator: bool = True) -> None:
    release = snapshot["status_release"]
    children = [("soperator", release["name"], "helm-soperator")]
    if configurator:
        children.append(("nodeconfigurator", "node-preparation", "helm-nodeconfigurator"))
    snapshot["flux_releases"] = [
        {
            "kind": "HelmRelease",
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "metadata": {
                "name": f"cxcli-soperator-fluxcd-{role}",
                "namespace": "flux-system",
                "generation": 2,
                "labels": {
                    SOPERATOR_GRAPH_LABEL: SOPERATOR_GRAPH_LABEL_VALUE,
                    "app.kubernetes.io/version": "4.1.7",
                },
            },
            "spec": {"releaseName": name, "targetNamespace": "soperator-system"},
            "status": {
                "observedGeneration": 2,
                "storageNamespace": "flux-system",
                "conditions": [{"type": "Ready", "status": "True"}],
                "history": [
                    {
                        "chartName": chart,
                        "chartVersion": release["chart_version"],
                        "appVersion": "4.1.7",
                        "status": "deployed",
                    }
                ],
            },
        }
        for role, name, chart in children
    ]


def add_node_configurator(snapshot: dict[str, Any]) -> dict[str, Any]:
    config = resource("NodeConfigurator", "custom-preparation", "soperator-system")
    config["metadata"]["annotations"] = {
        "meta.helm.sh/release-name": "node-preparation",
        "meta.helm.sh/release-namespace": "soperator-system",
    }
    snapshot["soperator_resources"].append(config)
    snapshot["expected_configurator_releases"] = [
        {"name": "node-preparation", "namespace": "soperator-system"}
    ]
    ds = resource("DaemonSet", "prepare-nodes", "soperator-system")
    ds["metadata"]["ownerReferences"] = [reference(config)]
    ds["status"].update(desiredNumberScheduled=5, numberReady=5)
    snapshot["workloads"].append(ds)
    for i in range(5):
        pod = resource("Pod", f"prepare-{i}", "soperator-system")
        pod["metadata"]["ownerReferences"] = [reference(ds)]
        pod["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
        snapshot["workloads"].append(pod)
    return config
