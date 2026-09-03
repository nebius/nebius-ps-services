from __future__ import annotations

import json
from dataclasses import replace

import yaml

from nebius_cxcli.soperator_flux_graph import (
    SOPERATOR_GRAPH_CONFIGMAP,
    expected_soperator_release_names,
    render_soperator_flux_graph_documents,
    soperator_graph_post_render_patches,
    target_soperator_release_name,
)
from soperator_fixtures import sample_snapshot


def _core_values() -> dict[str, object]:
    return {
        "backup": {"enabled": False, "config": {"enabled": False}},
        "observability": {"enabled": False},
        "storageClasses": {"enabled": False},
        "nodesets": {"enabled": True},
        "slurmCluster": {"overrideValues": {"clusterName": "soperator"}},
        "soperatorActiveChecks": {"enabled": False},
        "soperator": {
            "enabled": True,
            "nodeConfigurator": {"enabled": True},
            "soperatorChecks": {"enabled": True},
            "monitoringDashboards": {"enabled": False},
        },
    }


def _snapshot(values: dict[str, object]):
    return sample_snapshot(release_names=tuple(sorted(expected_soperator_release_names(values))))


def _vm_stack_snapshot(
    values: dict[str, object],
    *,
    package_sha256: str = (
        "sha256:01e38a9632441d5c6c11f7c047fb99dc41084b4cc32e96c15ede612f85e02eb9"
    ),
    chart_key: str = "victoriaMetricsStack",
):
    snapshot = _snapshot(values)
    chart = replace(
        snapshot.third_party_charts["certManager"],
        chart="victoria-metrics-k8s-stack",
        version="0.39.4",
        repository="https://victoriametrics.github.io/helm-charts",
        package_sha256=package_sha256,
        oci_digest=None,
    )
    return replace(
        snapshot,
        third_party_charts={
            **dict(snapshot.third_party_charts),
            "victoriaMetricsStack": chart,
        },
        release_graph=tuple(
            replace(node, owner="third-party", chart_key=chart_key)
            if node.release_name == "soperator-fluxcd-vm-stack"
            else node
            for node in snapshot.release_graph
        ),
    )


def test_core_graph_uses_only_immutable_sources() -> None:
    values = _core_values()
    lock = _snapshot(values)

    documents = render_soperator_flux_graph_documents(lock, values)
    repositories = [item for item in documents if item["kind"] == "OCIRepository"]
    configmap = next(
        item
        for item in documents
        if item["kind"] == "ConfigMap" and item["metadata"]["name"] == SOPERATOR_GRAPH_CONFIGMAP
    )

    assert repositories
    assert all(item["spec"]["ref"].get("digest", "").startswith("sha256:") for item in repositories)
    assert all("tag" not in item["spec"]["ref"] for item in repositories)
    graph = json.loads(configmap["data"]["graph.json"])
    assert {item["releaseName"] for item in graph["releases"]} == {
        target_soperator_release_name(name) for name in expected_soperator_release_names(values)
    }
    assert {item["upstreamReleaseName"] for item in graph["releases"]} == set(
        expected_soperator_release_names(values)
    )
    assert [item["releaseName"] for item in graph["releases"] if item["isMain"]] == [
        target_soperator_release_name("soperator-fluxcd-slurm-cluster")
    ]


def test_post_render_replaces_every_child_chart_with_locked_chart_ref() -> None:
    values = _core_values()
    lock = _snapshot(values)

    patches = soperator_graph_post_render_patches(lock, values)

    assert {item["target"]["name"] for item in patches} == set(
        expected_soperator_release_names(values)
    )
    for item in patches:
        operations = yaml.safe_load(item["patch"])
        assert operations[0] == {"op": "remove", "path": "/spec/chart"}
        chart_ref = operations[1]["value"]
        assert chart_ref["kind"] in {"OCIRepository", "HelmChart"}
        assert chart_ref["namespace"] == "flux-system"
        renamed = next(
            operation for operation in operations if operation["path"] == "/metadata/name"
        )
        assert renamed["value"] == target_soperator_release_name(item["target"]["name"])
        assert not any(operation["path"] == "/metadata/annotations" for operation in operations)
        assert any(
            operation["path"] == "/metadata/labels/soperator.nebius.ai~1release-stage"
            for operation in operations
        )


def test_post_render_repairs_exact_controller_storage_shape() -> None:
    values = _core_values()
    lock = _snapshot(values)

    patches = soperator_graph_post_render_patches(lock, values)

    slurm_patch = next(
        item for item in patches if item["target"]["name"] == "soperator-fluxcd-slurm-cluster"
    )
    operations = yaml.safe_load(slurm_patch["patch"])
    child_renderer = next(
        operation for operation in operations if operation["path"] == "/spec/postRenderers"
    )
    assert child_renderer == {
        "op": "add",
        "path": "/spec/postRenderers",
        "value": [
            {
                "kustomize": {
                    "patches": [
                        {
                            "target": {
                                "group": "slurm.nebius.ai",
                                "version": "v1",
                                "kind": "SlurmCluster",
                                "name": "soperator",
                            },
                            "patch": yaml.safe_dump(
                                [
                                    {
                                        "op": "test",
                                        "path": (
                                            "/spec/slurmNodes/controller/volumes/spool/"
                                            "volumeSourceName"
                                        ),
                                        "value": "controller-spool",
                                    },
                                    {
                                        "op": "remove",
                                        "path": (
                                            "/spec/slurmNodes/controller/volumes/spool/"
                                            "volumeClaimTemplateSpec"
                                        ),
                                    },
                                ],
                                sort_keys=False,
                            ),
                        }
                    ]
                }
            }
        ],
    }


def test_post_render_disables_cleanup_only_on_exact_vm_stack_raw_child() -> None:
    values = _core_values()
    values["observability"] = {"enabled": True, "vmStack": {"enabled": True}}
    lock = _vm_stack_snapshot(values)

    patches = soperator_graph_post_render_patches(lock, values)

    vm_stack_patch = next(
        item for item in patches if item["target"]["name"] == "soperator-fluxcd-vm-stack"
    )
    operations = yaml.safe_load(vm_stack_patch["patch"])
    assert {
        "op": "test",
        "path": "/spec/values/victoria-metrics-operator",
        "value": {
            "admissionWebhooks": {"enable": True},
            "crd": {"cleanup": {"enabled": False}, "create": False},
            "operator": {
                "disable_prometheus_converter": False,
                "enable_converter_ownership": True,
            },
        },
    } in operations
    assert {
        "op": "add",
        "path": "/spec/values/victoria-metrics-operator/crds",
        "value": {"cleanup": {"enabled": False}},
    } in operations
    assert {
        "op": "test",
        "path": "/spec/install",
        "value": {"crds": "Skip", "remediation": {"retries": 3}},
    } in operations
    assert {
        "op": "add",
        "path": "/spec/install/strategy",
        "value": {"name": "RetryOnFailure", "retryInterval": "30s"},
    } in operations


def test_post_render_does_not_patch_changed_vm_stack_identity() -> None:
    values = _core_values()
    values["observability"] = {"enabled": True, "vmStack": {"enabled": True}}

    for lock in (
        _vm_stack_snapshot(values, package_sha256="sha256:" + "d" * 64),
        _vm_stack_snapshot(values, chart_key="certManager"),
    ):
        patches = soperator_graph_post_render_patches(lock, values)
        vm_stack_patch = next(
            item for item in patches if item["target"]["name"] == "soperator-fluxcd-vm-stack"
        )
        operations = yaml.safe_load(vm_stack_patch["patch"])
        assert not any(
            operation["path"].startswith("/spec/values/victoria-metrics-operator")
            for operation in operations
        )
        assert not any(operation["path"] == "/spec/install/strategy" for operation in operations)


def test_optional_graph_is_explicit_and_complete() -> None:
    values = _core_values()
    values["backup"] = {"enabled": True, "config": {"enabled": True}}
    values["observability"] = {
        "enabled": True,
        "dcgmExporter": {"enabled": True},
        "vmStack": {"enabled": True},
    }
    values["notifier"] = {"enabled": True}
    values["soperatorActiveChecks"] = {"enabled": True}
    values["soperator"]["monitoringDashboards"] = {"enabled": True}  # type: ignore[index]

    names = expected_soperator_release_names(values)

    assert "soperator-fluxcd-k8up" in names
    assert "soperator-fluxcd-backup-config" in names
    assert "soperator-fluxcd-vm-stack" in names
    assert "soperator-fluxcd-opentelemetry-collector-jail-logs" in names
    assert "soperator-fluxcd-dcgm-exporter" in names
    assert "soperator-fluxcd-monitoring-dashboards" in names
    assert "soperator-fluxcd-soperator-activechecks" in names
    assert "soperator-fluxcd-soperator-notifier" in names


def test_disabled_upstream_children_are_not_required_by_the_release_graph() -> None:
    values = _core_values()
    values["certManager"] = {"enabled": False}
    values["mariadbOperator"] = {"enabled": False}
    values["soperator"]["soperatorChecks"] = {"enabled": False}  # type: ignore[index]
    values["observability"] = {
        "enabled": True,
        "prometheusOperator": {"enabled": False},
        "vmLogs": {"enabled": False},
        "vmStack": {"enabled": False},
        "opentelemetry": {"enabled": False},
        "dcgmExporter": {"enabled": False},
    }

    names = expected_soperator_release_names(values)

    assert "soperator-fluxcd-cert-manager" not in names
    assert "soperator-fluxcd-mariadb-operator-crds" not in names
    assert "soperator-fluxcd-mariadb-operator" not in names
    assert "soperator-fluxcd-soperatorchecks" not in names
    assert "soperator-fluxcd-prometheus-operator-crds" not in names
    assert "soperator-fluxcd-victoria-metrics-operator-crds" not in names
    assert "soperator-fluxcd-vm-logs" not in names
    assert "soperator-fluxcd-vm-stack" not in names
    assert "soperator-fluxcd-tsa-token-writer" not in names
    assert "soperator-fluxcd-opentelemetry-collector-events" not in names
    assert "soperator-fluxcd-opentelemetry-collector-logs" not in names
    assert "soperator-fluxcd-opentelemetry-collector-jail-logs" not in names
    assert "soperator-fluxcd-dcgm-exporter" not in names


def test_readiness_contract_binds_exact_storage_and_nodesets() -> None:
    values = _core_values()
    values["nodesets"] = {
        "enabled": True,
        "overrideValues": {"nodesets": [{"name": "worker-cpu"}]},
    }
    lock = _snapshot(values)
    adapter_documents = [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {
                "name": "controller-spool-pv",
                "labels": {"soperator.nebius.ai/lifecycle": "protected"},
            },
            "spec": {
                "storageClassName": "slurm-local-pv",
                "persistentVolumeReclaimPolicy": "Retain",
                "local": {"path": "/mnt/controller-spool/data"},
            },
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": "controller-spool",
                "namespace": "soperator",
                "labels": {"soperator.nebius.ai/lifecycle": "protected"},
            },
            "spec": {
                "storageClassName": "slurm-local-pv",
                "volumeName": "controller-spool-pv",
            },
        },
    ]

    documents = render_soperator_flux_graph_documents(
        lock,
        values,
        adapter_documents=adapter_documents,
    )
    configmap = next(item for item in documents if item["kind"] == "ConfigMap")
    readiness = json.loads(configmap["data"]["graph.json"])["readiness"]

    assert readiness["nodeSets"] == ["worker-cpu"]
    assert [(item["kind"], item["name"]) for item in readiness["storage"]] == [
        ("PersistentVolume", "controller-spool-pv"),
        ("PersistentVolumeClaim", "controller-spool"),
    ]
    assert readiness["storage"][0]["backend"]["kind"] == "local"
