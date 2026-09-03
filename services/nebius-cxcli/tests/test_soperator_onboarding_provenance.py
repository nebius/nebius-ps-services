from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import soperator_registration
from nebius_cxcli.soperator_registration import (
    _materialize_soperator_chart_dependencies,
    collect_kubectl_soperator_snapshot,
    soperator_protected_storage_evidence,
    verify_live_soperator_release_provenance,
    verify_soperator_manifest_equivalence,
)

LIVE = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: soperator-controller
  namespace: soperator
  uid: generated
  resourceVersion: "9"
  annotations:
    meta.helm.sh/release-name: soperator
spec:
  replicas: 1
  selector:
    matchLabels:
      app: soperator
status:
  availableReplicas: 1
"""

RENDERED = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: soperator-controller
  namespace: soperator
spec:
  replicas: 1
  selector:
    matchLabels:
      app: soperator
"""


def test_soperator_release_candidate_rejects_an_explicit_non_product_chart() -> None:
    assert not soperator_registration._is_soperator_release_candidate(
        {
            "name": "soperator",
            "chart": "helm-slurm-cluster-1.22.3",
            "status": "deployed",
        }
    )


def test_helm_storage_namespace_is_independent_from_target_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def _helm_json(args, *_args, **_kwargs):
        command = tuple(args)
        calls.append(command)
        namespace = command[command.index("--namespace") + 1]
        if namespace == "flux-system":
            return [
                {
                    "name": "soperator-controller",
                    "namespace": "soperator-system",
                    "status": "deployed",
                }
            ]
        return []

    monkeypatch.setattr(soperator_registration, "_helm_json", _helm_json)

    storage_namespace = soperator_registration._helm_release_storage_namespace(
        kube_context="ctx",
        release_name="soperator-controller",
        namespace_names=("soperator-system", "flux-system"),
        timeout=30,
        errors=[],
        extra_env=None,
    )

    assert storage_namespace == "flux-system"
    assert len(calls) == 2


def test_soperator_release_candidate_accepts_the_controller_chart() -> None:
    assert soperator_registration._is_soperator_release_candidate(
        {
            "name": "soperator-controller",
            "chart": "helm-soperator-1.22.3",
            "status": "deployed",
        }
    )


def test_official_chart_dependencies_are_materialized_in_an_isolated_stage(
    tmp_path: Path,
) -> None:
    chart_path = tmp_path / "source" / "helm" / "soperator"
    chart_path.mkdir(parents=True)
    (chart_path / "Chart.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v2",
                "name": "helm-soperator",
                "version": "1.22.3",
                "dependencies": [
                    {
                        "name": "kruise",
                        "version": "1.8.0",
                        "repository": "https://openkruise.github.io/charts/",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []

    def _run(args, **_kwargs):
        command = tuple(args)
        calls.append(command)
        if command[:3] == ("helm", "dependency", "build"):
            staged = Path(command[-1])
            (staged / "charts").mkdir()
            (staged / "charts" / "kruise-1.8.0.tgz").write_bytes(b"chart")
            (staged / "Chart.lock").write_text(
                yaml.safe_dump(
                    {
                        "dependencies": [
                            {
                                "name": "kruise",
                                "version": "1.8.0",
                                "repository": "https://openkruise.github.io/charts/",
                            }
                        ],
                        "digest": "sha256:test",
                    }
                ),
                encoding="utf-8",
            )
        return ""

    staged = _materialize_soperator_chart_dependencies(
        chart_path=chart_path,
        staging_root=tmp_path / "stage",
        run=_run,
    )

    assert staged != chart_path
    assert not (chart_path / "Chart.lock").exists()
    assert calls[0][:5] == (
        "helm",
        "repo",
        "add",
        "cxcli-soperator-provenance-1",
        "https://openkruise.github.io/charts/",
    )
    assert calls[1][:4] == ("helm", "dependency", "build", "--skip-refresh")


def test_snapshot_collects_cluster_wide_workload_inventory_for_destroy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_commands: list[tuple[str, ...]] = []

    def _kubectl_json(args, *_args, **_kwargs):
        command = tuple(args)
        if command[-4:] == ("get", "namespace", "-o", "json"):
            return {"items": [{"metadata": {"name": "kube-system", "uid": "cluster-uid-a"}}]}
        return {"items": []}

    def _kubectl_list(args, *_args, **_kwargs):
        command = tuple(args)
        list_commands.append(command)
        if "replicasets" in ",".join(command):
            return {
                "items": [
                    {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "metadata": {
                            "name": "customer-api",
                            "namespace": "default",
                        },
                        "spec": {"replicas": 2},
                        "status": {"availableReplicas": 2},
                    }
                ]
            }
        return {"items": []}

    monkeypatch.setattr(soperator_registration, "_kubectl_json", _kubectl_json)
    monkeypatch.setattr(
        soperator_registration,
        "_kubectl_list_json_with_bounded_retry",
        _kubectl_list,
    )
    monkeypatch.setattr(soperator_registration, "_helm_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        soperator_registration,
        "_collect_worker_topology_by_nodeset",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        soperator_registration,
        "_collect_slurm_health_from_login",
        lambda **_kwargs: {"checked": False, "healthy": False},
    )

    snapshot = collect_kubectl_soperator_snapshot(
        kube_context="ctx-a",
        include_cluster_inventory=True,
    )

    cluster_inventory_commands = [
        command for command in list_commands if "replicasets" in ",".join(command)
    ]
    assert len(cluster_inventory_commands) == 1
    assert "-A" in cluster_inventory_commands[0]
    assert snapshot["cluster_namespace_resources"] == [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"labels": {}, "name": "customer-api", "namespace": "default"},
            "spec": {"replicas": 2},
            "status": {"availableReplicas": 2},
        }
    ]


def test_snapshot_keeps_required_workloads_when_optional_kruise_api_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_commands: list[tuple[str, ...]] = []

    def _kubectl_json(args, *_args, **_kwargs):
        command = tuple(args)
        if command[-4:] == ("get", "namespace", "-o", "json"):
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "cluster-uid-a"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid-a"}},
                ]
            }
        return {"items": []}

    def _kubectl_list(args, *_args, **_kwargs):
        command = tuple(args)
        list_commands.append(command)
        if any("deployments,statefulsets,daemonsets" in item for item in command):
            return {
                "items": [
                    {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "metadata": {"name": "required-controller", "namespace": "soperator"},
                    }
                ]
            }
        return {"items": []}

    monkeypatch.setattr(soperator_registration, "_kubectl_json", _kubectl_json)
    monkeypatch.setattr(
        soperator_registration,
        "_kubectl_list_json_with_bounded_retry",
        _kubectl_list,
    )
    monkeypatch.setattr(soperator_registration, "_helm_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        soperator_registration,
        "_collect_worker_topology_by_nodeset",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        soperator_registration,
        "_collect_slurm_health_from_login",
        lambda **_kwargs: {"checked": False, "healthy": False},
    )

    snapshot = collect_kubectl_soperator_snapshot(kube_context="ctx-a")

    assert all("statefulsets.apps.kruise.io" not in command for command in list_commands)
    assert snapshot["collection_errors"] == []
    assert [item["metadata"]["name"] for item in snapshot["soperator_namespace_resources"]] == [
        "required-controller"
    ]


def test_snapshot_collects_optional_kruise_workloads_without_poisoning_required_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optional_errors: list[object] = []

    def _kubectl_json(args, *_args, **_kwargs):
        command = tuple(args)
        if command[-4:] == ("get", "crd", "-o", "json"):
            return {"items": [{"metadata": {"name": "statefulsets.apps.kruise.io"}}]}
        if command[-4:] == ("get", "namespace", "-o", "json"):
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "cluster-uid-a"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid-a"}},
                ]
            }
        return {"items": []}

    def _kubectl_list(args, *_args, **kwargs):
        command = tuple(args)
        if "statefulsets.apps.kruise.io" in command:
            optional_errors.append(kwargs.get("errors"))
            return {
                "items": [
                    {
                        "apiVersion": "apps.kruise.io/v1beta1",
                        "kind": "StatefulSet",
                        "metadata": {"name": "optional-worker", "namespace": "soperator"},
                    }
                ]
            }
        if any("deployments,statefulsets,daemonsets" in item for item in command):
            return {
                "items": [
                    {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "metadata": {"name": "required-controller", "namespace": "soperator"},
                    }
                ]
            }
        return {"items": []}

    monkeypatch.setattr(soperator_registration, "_kubectl_json", _kubectl_json)
    monkeypatch.setattr(
        soperator_registration,
        "_kubectl_list_json_with_bounded_retry",
        _kubectl_list,
    )
    monkeypatch.setattr(soperator_registration, "_helm_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        soperator_registration,
        "_collect_worker_topology_by_nodeset",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        soperator_registration,
        "_collect_slurm_health_from_login",
        lambda **_kwargs: {"checked": False, "healthy": False},
    )

    snapshot = collect_kubectl_soperator_snapshot(kube_context="ctx-a")

    assert len(optional_errors) == 1
    assert optional_errors[0] is not None
    assert snapshot["collection_errors"] == []
    assert {item["metadata"]["name"] for item in snapshot["soperator_namespace_resources"]} == {
        "required-controller",
        "optional-worker",
    }


def test_snapshot_marks_kruise_collection_failure_in_workload_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _kubectl_json(args, *_args, **_kwargs):
        command = tuple(args)
        if command[-4:] == ("get", "crd", "-o", "json"):
            return {"items": [{"metadata": {"name": "statefulsets.apps.kruise.io"}}]}
        if command[-4:] == ("get", "namespace", "-o", "json"):
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "cluster-uid-a"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid-a"}},
                ]
            }
        return {"items": []}

    def _kubectl_list(args, *_args, **kwargs):
        if "statefulsets.apps.kruise.io" in tuple(args):
            kwargs["errors"].append(
                {"collector": "kubectl", "message": "optional workload read failed"}
            )
            return {}
        return {"items": []}

    monkeypatch.setattr(soperator_registration, "_kubectl_json", _kubectl_json)
    monkeypatch.setattr(
        soperator_registration,
        "_kubectl_list_json_with_bounded_retry",
        _kubectl_list,
    )
    monkeypatch.setattr(soperator_registration, "_helm_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        soperator_registration,
        "_collect_worker_topology_by_nodeset",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        soperator_registration,
        "_collect_slurm_health_from_login",
        lambda **_kwargs: {"checked": False, "healthy": False},
    )

    snapshot = collect_kubectl_soperator_snapshot(
        kube_context="ctx-a",
        require_complete_identity=False,
    )

    lanes = {lane["name"]: lane for lane in snapshot["collection_lanes"]}
    assert lanes["soperator-workloads"]["status"] == "failed"
    assert lanes["soperator-workloads"]["item_count"] is None
    assert any(error.get("collector") == "kubectl" for error in snapshot["collection_errors"])


def test_snapshot_projects_ready_flux_owned_soperator_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_commands: list[tuple[str, ...]] = []

    def _kubectl_json(args, *_args, **_kwargs):
        command = tuple(args)
        if command[-4:] == ("get", "crd", "-o", "json"):
            return {
                "items": [
                    {
                        "metadata": {
                            "name": "helmreleases.helm.toolkit.fluxcd.io",
                        }
                    }
                ]
            }
        if command[-4:] == ("get", "namespace", "-o", "json"):
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "cluster-uid-a"}},
                    {"metadata": {"name": "flux-system", "uid": "flux-uid-a"}},
                ]
            }
        return {"items": []}

    def _kubectl_list(args, *_args, **_kwargs):
        command = tuple(args)
        list_commands.append(command)
        if "helmreleases.helm.toolkit.fluxcd.io" in command:
            return {
                "items": [
                    {
                        "apiVersion": "helm.toolkit.fluxcd.io/v2",
                        "kind": "HelmRelease",
                        "metadata": {
                            "generation": 39,
                            "labels": {
                                "app.kubernetes.io/version": "4.1.7",
                                "soperator.nebius.ai/release-graph": "nebius-cxcli",
                            },
                            "name": "cxcli-soperator-fluxcd-soperator",
                            "namespace": "flux-system",
                        },
                        "spec": {
                            "releaseName": "soperator-controller",
                            "targetNamespace": "soperator-system",
                        },
                        "status": {
                            "conditions": [
                                {
                                    "reason": "UpgradeSucceeded",
                                    "status": "True",
                                    "type": "Ready",
                                }
                            ],
                            "history": [
                                {
                                    "appVersion": "4.1.7",
                                    "chartName": "helm-soperator",
                                    "chartVersion": "4.1.7+dfddf7cde96b",
                                    "status": "deployed",
                                }
                            ],
                            "observedGeneration": 39,
                        },
                    }
                ]
            }
        return {"items": []}

    monkeypatch.setattr(soperator_registration, "_kubectl_json", _kubectl_json)
    monkeypatch.setattr(
        soperator_registration,
        "_kubectl_list_json_with_bounded_retry",
        _kubectl_list,
    )
    monkeypatch.setattr(soperator_registration, "_helm_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        soperator_registration,
        "_collect_worker_topology_by_nodeset",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        soperator_registration,
        "_collect_slurm_health_from_login",
        lambda **_kwargs: {"checked": False, "healthy": False},
    )

    snapshot = collect_kubectl_soperator_snapshot(kube_context="ctx-a")

    flux_commands = [
        command for command in list_commands if "helmreleases.helm.toolkit.fluxcd.io" in command
    ]
    assert len(flux_commands) == 1
    assert snapshot["helm_releases"] == [
        {
            "app_version": "4.1.7",
            "chart": "helm-soperator-4.1.7+dfddf7cde96b",
            "chart_name": "helm-soperator",
            "chart_version": "4.1.7+dfddf7cde96b",
            "name": "soperator-controller",
            "namespace": "soperator-system",
            "source": "flux-helmrelease",
            "status": "deployed",
            "storage_namespace": "flux-system",
        }
    ]
    assert snapshot["collection_errors"] == []


def test_public_snapshot_collects_only_discovered_component_namespaces_and_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_commands: list[tuple[str, ...]] = []
    sensitive_sentinel = "SENSITIVE_COMPONENT_PAYLOAD"

    def _kubectl_json(args, *_args, **_kwargs):
        command = tuple(args)
        if command[-4:] == ("get", "crd", "-o", "json"):
            return {"items": [{"metadata": {"name": "statefulsets.apps.kruise.io"}}]}
        if command[-4:] == ("get", "namespace", "-o", "json"):
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "cluster-uid-a"}},
                    {"metadata": {"name": "custom-soperator"}},
                    {"metadata": {"name": "nvidia-gpu-operator"}},
                    {"metadata": {"name": "unrelated-customer-app"}},
                ]
            }
        return {"items": []}

    def _kubectl_list(args, *_args, **_kwargs):
        command = tuple(args)
        list_commands.append(command)
        namespace = command[command.index("-n") + 1] if "-n" in command else ""
        if "statefulsets.apps.kruise.io" in command:
            if namespace == "custom-soperator":
                return {
                    "items": [
                        {
                            "apiVersion": "apps.kruise.io/v1beta1",
                            "kind": "StatefulSet",
                            "metadata": {
                                "name": "custom-workers",
                                "namespace": namespace,
                            },
                        }
                    ]
                }
            return {"items": []}
        if namespace in {"custom-soperator", "nvidia-gpu-operator"}:
            return {
                "items": [
                    {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "metadata": {
                            "name": f"{namespace}-controller",
                            "namespace": namespace,
                            "labels": {"private": sensitive_sentinel},
                        },
                        "status": {"readyReplicas": 1, "message": sensitive_sentinel},
                    }
                ]
            }
        return {"items": []}

    def _helm_json(args, *_args, **_kwargs):
        command = tuple(args)
        if "-A" in command:
            return [
                {
                    "name": "soperator-controller",
                    "namespace": "custom-soperator",
                    "chart": "helm-soperator-4.1.7",
                }
            ]
        if "nvidia-gpu-operator" in command:
            return [
                {
                    "name": "gpu-operator",
                    "namespace": "nvidia-gpu-operator",
                    "chart": "gpu-operator-v25.10.0",
                }
            ]
        return []

    monkeypatch.setattr(soperator_registration, "_kubectl_json", _kubectl_json)
    monkeypatch.setattr(
        soperator_registration,
        "_kubectl_list_json_with_bounded_retry",
        _kubectl_list,
    )
    monkeypatch.setattr(soperator_registration, "_helm_json", _helm_json)
    monkeypatch.setattr(
        soperator_registration,
        "_collect_worker_topology_by_nodeset",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        soperator_registration,
        "_collect_slurm_health_from_login",
        lambda **_kwargs: {"checked": True, "healthy": True},
    )

    snapshot = collect_kubectl_soperator_snapshot(
        kube_context="ctx-a",
        require_complete_identity=False,
    )

    collected_namespaces = {
        command[command.index("-n") + 1]
        for command in list_commands
        if "deployments,statefulsets,daemonsets" in ",".join(command) and "-n" in command
    }
    assert collected_namespaces == {"custom-soperator", "nvidia-gpu-operator"}
    assert {
        item["metadata"]["namespace"] for item in snapshot["component_namespace_resources"]
    } == {
        "custom-soperator",
        "nvidia-gpu-operator",
    }
    assert any(
        "statefulsets.apps.kruise.io" in command
        and command[command.index("-n") + 1] == "custom-soperator"
        for command in list_commands
    )
    assert "unrelated-customer-app" not in collected_namespaces
    lanes = {lane["name"]: lane for lane in snapshot["collection_lanes"]}
    assert lanes["component-workloads"] == {
        "name": "component-workloads",
        "status": "succeeded",
        "item_count": 3,
    }
    assert lanes["soperator-workloads"]["status"] == "not-applicable"


def test_flux_owned_soperator_release_does_not_deploy_stale_generation() -> None:
    errors: list[dict[str, object]] = []

    release = soperator_registration._flux_soperator_main_release(
        {
            "items": [
                {
                    "metadata": {
                        "generation": 40,
                        "labels": {
                            "app.kubernetes.io/version": "4.1.7",
                            "soperator.nebius.ai/release-graph": "nebius-cxcli",
                        },
                        "name": "cxcli-soperator-fluxcd-soperator",
                        "namespace": "flux-system",
                    },
                    "spec": {
                        "releaseName": "soperator-controller",
                        "targetNamespace": "soperator-system",
                    },
                    "status": {
                        "conditions": [{"status": "True", "type": "Ready"}],
                        "history": [
                            {
                                "appVersion": "4.1.7",
                                "chartName": "helm-soperator",
                                "chartVersion": "4.1.7+dfddf7cde96b",
                                "status": "deployed",
                            }
                        ],
                        "observedGeneration": 39,
                    },
                }
            ]
        },
        errors=errors,
    )

    assert release["status"] == "pending-upgrade"
    assert errors == []


def test_flux_owned_soperator_release_rejects_ready_version_conflict() -> None:
    errors: list[dict[str, object]] = []

    release = soperator_registration._flux_soperator_main_release(
        {
            "items": [
                {
                    "metadata": {
                        "generation": 39,
                        "labels": {
                            "app.kubernetes.io/version": "4.1.7",
                            "soperator.nebius.ai/release-graph": "nebius-cxcli",
                        },
                        "name": "cxcli-soperator-fluxcd-soperator",
                        "namespace": "flux-system",
                    },
                    "spec": {
                        "releaseName": "soperator-controller",
                        "targetNamespace": "soperator-system",
                    },
                    "status": {
                        "conditions": [{"status": "True", "type": "Ready"}],
                        "history": [
                            {
                                "appVersion": "4.1.6",
                                "chartName": "helm-soperator",
                                "chartVersion": "4.1.6+outdated",
                                "status": "deployed",
                            }
                        ],
                        "observedGeneration": 39,
                    },
                }
            ]
        },
        errors=errors,
    )

    assert release == {}
    assert errors == [
        {
            "command": "project Flux Soperator HelmRelease",
            "message": "ready main workload history differs from its release graph",
        }
    ]


def test_official_render_equivalence_ignores_only_generated_state() -> None:
    evidence = verify_soperator_manifest_equivalence(
        live_manifest=LIVE,
        rendered_manifest=RENDERED,
    )

    assert evidence.method == "official-render-equivalence"
    assert evidence.live_manifest_sha256 == evidence.rendered_manifest_sha256
    assert evidence.owned_object_graph_sha256.startswith("sha256:")


def test_official_render_equivalence_normalizes_flux_and_api_generated_state() -> None:
    rendered = """
apiVersion: v1
kind: Service
metadata:
  name: kruise-webhook-service
  namespace: kruise-system
spec:
  type: ClusterIP
  ports:
    - port: 443
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kruise-manager
  namespace: kruise-system
---
apiVersion: v1
kind: Secret
metadata:
  name: kruise-webhook-certs
  namespace: kruise-system
---
apiVersion: batch/v1
kind: Job
metadata:
  name: soperator-controller-finalizer
  namespace: kruise-system
  annotations:
    helm.sh/hook: post-delete
"""
    live = """
apiVersion: v1
kind: Service
metadata:
  name: kruise-webhook-service
  namespace: kruise-system
  labels:
    helm.toolkit.fluxcd.io/name: controller
    helm.toolkit.fluxcd.io/namespace: flux-system
spec:
  clusterIP: 10.0.0.1
  clusterIPs: [10.0.0.1]
  ipFamilies: [IPv4]
  ipFamilyPolicy: SingleStack
  type: ClusterIP
  ports:
    - port: 443
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kruise-manager
  namespace: kruise-system
  labels:
    helm.toolkit.fluxcd.io/name: controller
    helm.toolkit.fluxcd.io/namespace: flux-system
secrets:
  - name: generated-token
---
apiVersion: v1
kind: Secret
metadata:
  name: kruise-webhook-certs
  namespace: kruise-system
  labels:
    helm.toolkit.fluxcd.io/name: controller
    helm.toolkit.fluxcd.io/namespace: flux-system
data:
  tls.crt: generated
  tls.key: generated
"""

    evidence = verify_soperator_manifest_equivalence(
        live_manifest=live,
        rendered_manifest=rendered,
    )

    assert evidence.live_manifest_sha256 == evidence.rendered_manifest_sha256


def test_manifest_normalization_accepts_only_the_proven_registry_relocation() -> None:
    expected = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "controller"},
        "spec": {
            "template": {
                "spec": {
                    "hostNetwork": False,
                    "containers": [
                        {
                            "image": "gcr.io/kubebuilder/kube-rbac-proxy:v0.15.0",
                            "resources": {"requests": {"cpu": "1000m"}},
                        }
                    ],
                }
            }
        },
    }
    relocated = json.loads(json.dumps(expected))
    relocated["spec"]["template"]["spec"].pop("hostNetwork")
    relocated["spec"]["template"]["spec"]["containers"][0]["image"] = (
        "quay.io/brancz/kube-rbac-proxy:v0.15.0"
    )
    relocated["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]["cpu"] = "1"
    unproven = json.loads(json.dumps(relocated))
    unproven["spec"]["template"]["spec"]["containers"][0]["image"] = (
        "example.invalid/kube-rbac-proxy:v0.15.0"
    )

    normalized_expected = soperator_registration._normalized_manifest_object(expected)
    normalized_relocated = soperator_registration._normalized_manifest_object(relocated)
    normalized_unproven = soperator_registration._normalized_manifest_object(unproven)

    assert normalized_expected == normalized_relocated
    assert normalized_expected != normalized_unproven


def test_kruise_active_webhooks_must_derive_from_the_recorded_template() -> None:
    expected = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingWebhookConfiguration",
        "metadata": {
            "name": "kruise-validating-webhook-configuration",
            "annotations": {"template": ""},
        },
        "webhooks": [
            {"name": "one.example", "failurePolicy": "Fail"},
            {"name": "two.example", "failurePolicy": "Fail"},
        ],
    }
    observed = json.loads(json.dumps(expected))
    observed["metadata"]["annotations"]["template"] = yaml.safe_dump(expected["webhooks"])
    observed["webhooks"] = [
        {"name": "one.example", "failurePolicy": "Fail", "matchPolicy": "Equivalent"}
    ]

    normalized = soperator_registration._controller_owned_webhook_template(
        expected,
        observed,
    )

    assert normalized["metadata"]["annotations"]["template"] == ""
    assert normalized["webhooks"] == expected["webhooks"]

    observed["webhooks"][0]["failurePolicy"] = "Ignore"
    with pytest.raises(RuntimeError, match="not derived"):
        soperator_registration._controller_owned_webhook_template(expected, observed)


@pytest.mark.parametrize(
    "rendered",
    [
        RENDERED.replace("replicas: 1", "replicas: 2"),
        RENDERED.replace("soperator-controller", "different-controller"),
        "",
    ],
)
def test_manifest_drift_missing_or_unexpected_objects_fail_closed(rendered: str) -> None:
    with pytest.raises(RuntimeError, match="not equivalent"):
        verify_soperator_manifest_equivalence(
            live_manifest=LIVE,
            rendered_manifest=rendered,
        )


def test_storage_evidence_contains_only_identity_not_volume_or_secret_data() -> None:
    snapshot = {
        "pvcs": [
            {
                "metadata": {
                    "name": "jail-pvc",
                    "namespace": "soperator",
                    "uid": "pvc-uid",
                },
                "spec": {"volumeName": "pv-jail"},
            }
        ],
        "pvs": [
            {
                "metadata": {"name": "pv-jail", "uid": "pv-uid"},
                "spec": {
                    "csi": {
                        "volumeHandle": "computefilesystem-jail",
                        "volumeAttributes": {"private": "must-not-persist"},
                    }
                },
            }
        ],
        "soperator_namespace_resources": [
            {"kind": "Secret", "data": {"password": "must-not-persist"}}
        ],
    }

    digest, bindings = soperator_protected_storage_evidence(snapshot)

    assert digest.startswith("sha256:")
    assert bindings == (
        {
            "backingKind": "sfs-csi",
            "filesystemId": "computefilesystem-jail",
            "mountTag": "jail",
            "namespace": "soperator",
            "pvc": "jail-pvc",
            "pvcUid": "pvc-uid",
            "pv": "pv-jail",
            "pvUid": "pv-uid",
        },
    )
    assert "must-not-persist" not in repr(bindings)


def test_storage_evidence_uses_retained_local_sfs_binding_not_compute_disk() -> None:
    snapshot = {
        "pvcs": [
            {
                "metadata": {
                    "name": "jail-pvc",
                    "namespace": "soperator",
                    "uid": "jail-pvc-uid",
                },
                "spec": {"volumeName": "jail-pv"},
            },
            {
                "metadata": {
                    "name": "storage-accounting-0",
                    "namespace": "soperator",
                    "uid": "disk-pvc-uid",
                },
                "spec": {"volumeName": "disk-pv"},
            },
        ],
        "pvs": [
            {
                "metadata": {"name": "jail-pv", "uid": "jail-pv-uid"},
                "spec": {
                    "local": {"path": "/mnt/jail/data"},
                    "persistentVolumeReclaimPolicy": "Retain",
                },
            },
            {
                "metadata": {"name": "disk-pv", "uid": "disk-pv-uid"},
                "spec": {"csi": {"volumeHandle": "computedisk-accounting"}},
            },
        ],
    }

    _digest, bindings = soperator_protected_storage_evidence(snapshot)

    assert bindings == (
        {
            "backingKind": "sfs-local",
            "mountTag": "jail",
            "namespace": "soperator",
            "pvc": "jail-pvc",
            "pvcUid": "jail-pvc-uid",
            "pv": "jail-pv",
            "pvUid": "jail-pv-uid",
        },
    )


def test_live_provenance_uses_exact_helm_render_and_live_object_uids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chart_path = tmp_path / "helm" / "soperator"
    chart_path.mkdir(parents=True)
    (chart_path / "Chart.yaml").write_text(
        "apiVersion: v2\nname: soperator\n",
        encoding="utf-8",
    )
    calls: list[tuple[tuple[str, ...], str | None]] = []

    def _run(args, *, input=None, **_kwargs):
        command = tuple(args)
        calls.append((command, input))
        if command[:3] == ("helm", "--kube-context", "ctx") and "values" in command:
            return SimpleNamespace(returncode=0, stdout="replicaCount: 1\n", stderr="")
        if command[:3] == ("helm", "--kube-context", "ctx") and "manifest" in command:
            return SimpleNamespace(returncode=0, stdout=RENDERED, stderr="")
        if command[:3] == ("helm", "template", "soperator"):
            return SimpleNamespace(returncode=0, stdout=RENDERED, stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {
                        "name": "soperator-controller",
                        "namespace": "soperator",
                        "uid": "deployment-uid-a",
                    },
                    "spec": {
                        "replicas": 1,
                        "selector": {"matchLabels": {"app": "soperator"}},
                        "progressDeadlineSeconds": 600,
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("nebius_cxcli.soperator_registration.subprocess.run", _run)

    evidence = verify_live_soperator_release_provenance(
        kube_context="ctx",
        release_name="soperator",
        namespace="soperator",
        storage_namespace="flux-system",
        source_dir=tmp_path,
    )

    assert calls[0][0][-5:] == (
        "--namespace",
        "flux-system",
        "--all",
        "--output",
        "yaml",
    )
    assert calls[1][0][-2:] == ("--namespace", "flux-system")

    template_command, template_input = calls[2]
    assert template_command == (
        "helm",
        "template",
        "soperator",
        str(chart_path),
        "--namespace",
        "soperator",
        "--values",
        "-",
    )
    assert template_input == "replicaCount: 1\n"
    assert calls[3][0][:7] == (
        "kubectl",
        "--context",
        "ctx",
        "--namespace",
        "soperator",
        "get",
        "Deployment.apps",
    )
    assert evidence.owned_object_graph_sha256.startswith("sha256:")
    assert "replicaCount" not in repr(evidence)


def test_live_provenance_rejects_live_object_spec_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chart_path = tmp_path / "helm" / "soperator"
    chart_path.mkdir(parents=True)
    (chart_path / "Chart.yaml").write_text(
        "apiVersion: v2\nname: soperator\n",
        encoding="utf-8",
    )

    def _run(args, **_kwargs):
        command = tuple(args)
        if "values" in command:
            return SimpleNamespace(returncode=0, stdout="replicaCount: 1\n", stderr="")
        if "manifest" in command or command[:2] == ("helm", "template"):
            return SimpleNamespace(returncode=0, stdout=RENDERED, stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {
                        "name": "soperator-controller",
                        "namespace": "soperator",
                        "uid": "deployment-uid-a",
                    },
                    "spec": {
                        "replicas": 2,
                        "selector": {"matchLabels": {"app": "soperator"}},
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("nebius_cxcli.soperator_registration.subprocess.run", _run)

    with pytest.raises(RuntimeError, match="live object content differs"):
        verify_live_soperator_release_provenance(
            kube_context="ctx",
            release_name="soperator",
            namespace="soperator",
            storage_namespace="flux-system",
            source_dir=tmp_path,
        )
