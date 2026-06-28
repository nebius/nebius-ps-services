from __future__ import annotations

import json
import subprocess

import pytest

import nebius_cxcli.soperator_onboarding as soperator_onboarding_module
from nebius_cxcli.runtime_validation import validate_runtime_payload
from nebius_cxcli.soperator_discovery import load_soperator_discovery_bundle
from nebius_cxcli.soperator_onboarding import (
    ONBOARDING_ACTION_ADOPT_SOPERATOR,
    ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
    ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
    ONBOARDING_ACTION_INSTALL_SOPERATOR,
    ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
    ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
    ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
    ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
    ONBOARDING_ACTION_UPGRADE_SOPERATOR,
    ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS,
    ONBOARDING_COMPUTE_MODE_KEEP_EXISTING,
    ONBOARDING_STORAGE_MODE_CREATE_ALIGNED_SFS,
    ONBOARDING_STORAGE_MODE_KEEP_EXISTING,
    SOURCE_SOPERATOR_DISCOVERY_REPORT_NAME,
    analyze_soperator_onboarding_snapshot,
    build_soperator_onboarding_report_from_config,
    collect_kubectl_soperator_snapshot,
    soperator_migration_profile_for_version,
    soperator_migration_profile_versions,
    soperator_onboarding_fingerprint,
    soperator_onboarding_is_accepted,
    soperator_onboarding_report_for_modes,
    validate_soperator_onboarding_acceptance,
    write_soperator_onboarding_reports,
    write_source_soperator_discovery_report,
)


def _snapshot(*, release: dict[str, object] | None = None) -> dict[str, object]:
    releases = [release] if release is not None else []
    return {
        "node_groups": {
            "cpu-a": {
                "gpu": False,
                "node_count": 2,
                "labels": {"nebius.com/node-group": "cpu-a"},
            },
            "h100": {
                "gpu": True,
                "node_count": 2,
                "labels": {
                    "nebius.com/node-group": "h100",
                    "topology.nebius.com/tier-0": "leaf-a",
                },
                "allocatable": {"nvidia.com/gpu": "2", "rdma/shared_device": "8"},
            },
        },
        "helm_releases": releases,
        "crds": [],
        "namespaces": ["default"],
        "storage": {},
    }


def _target_compatible_legacy_snapshot() -> dict[str, object]:
    snapshot = _snapshot(
        release={
            "name": "soperator-controller",
            "namespace": "soperator-system",
            "chart": "helm-soperator-1.23.3",
            "app_version": "1.23.3",
        }
    )
    snapshot["storage"] = {
        "jail": {"source": "pvc/jail"},
        "controller-spool": {"source": "pvc/controller-spool"},
        "accounting": {"source": "pvc/accounting"},
    }
    snapshot["node_groups"] = {
        "system": {
            "gpu": False,
            "node_count": 3,
            "labels": {
                "nebius.com/node-group": "system",
                "slurm.nebius.ai/nodeset": "system",
            },
        },
        "controller": {
            "gpu": False,
            "node_count": 1,
            "labels": {
                "nebius.com/node-group": "controller",
                "slurm.nebius.ai/nodeset": "controller",
            },
        },
        "login": {
            "gpu": False,
            "node_count": 1,
            "labels": {
                "nebius.com/node-group": "login",
                "slurm.nebius.ai/nodeset": "login",
            },
        },
        "accounting": {
            "gpu": False,
            "node_count": 1,
            "labels": {
                "nebius.com/node-group": "accounting",
                "slurm.nebius.ai/nodeset": "accounting",
            },
        },
        "worker-gpu": {
            "gpu": True,
            "node_count": 2,
            "labels": {
                "nebius.com/node-group": "worker-gpu",
                "slurm.nebius.ai/nodeset": "worker-gpu",
                "topology.nebius.com/tier-0": "leaf-a",
            },
            "allocatable": {"nvidia.com/gpu": "8"},
        },
        "worker-cpu": {
            "gpu": False,
            "node_count": 2,
            "labels": {
                "nebius.com/node-group": "worker-cpu",
                "slurm.nebius.ai/nodeset": "worker-cpu",
            },
        },
    }
    return snapshot


def _with_provider_node_template_inventory(
    snapshot: dict[str, object],
    *,
    stale_group: str = "",
    stale_gpu_preset: str = "",
) -> dict[str, object]:
    snapshot = json.loads(json.dumps(snapshot))
    target_k8s_version = (
        soperator_onboarding_module.ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION
    )
    target_os = soperator_onboarding_module.ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_OS
    target_gpu_stack_preset = (
        soperator_onboarding_module.ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_GPU_STACK_PRESET
    )
    snapshot["provider"] = {
        "mk8s_cluster": {
            "id": "mk8scluster-source",
            "name": "source",
            "control_plane_version": target_k8s_version,
        }
    }
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    for group_name, raw_group in node_groups.items():
        assert isinstance(raw_group, dict)
        node_group_id = f"mk8snodegroup-{group_name}"
        raw_group["node_group_id"] = node_group_id
        raw_group["node_group_name"] = group_name
        labels = raw_group.setdefault("labels", {})
        assert isinstance(labels, dict)
        labels.setdefault("nebius.com/node-group-id", node_group_id)
        gpu_stack_preset = target_gpu_stack_preset if raw_group.get("gpu") is True else ""
        if group_name == stale_group:
            gpu_stack_preset = stale_gpu_preset
        raw_group["provider"] = {
            "node_group_id": node_group_id,
            "node_group_name": group_name,
            "node_template": {
                "k8s_version": target_k8s_version,
                "os": target_os,
                "gpu_stack_preset": gpu_stack_preset,
            },
        }
    return snapshot


def test_collect_snapshot_groups_nodes_by_unique_nebius_node_group_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        if cmd[:5] == ["kubectl", "--context", "ctx", "get", "nodes"]:
            return {
                "items": [
                    {
                        "metadata": {
                            "name": "node-system",
                            "labels": {
                                "nebius.com/node-group-id": "mk8snodegroup-system",
                                "nebius.com/node-group": "cpu",
                                "slurm.nebius.ai/nodeset": "system",
                            },
                        },
                        "status": {"allocatable": {"cpu": "8"}},
                    },
                    {
                        "metadata": {
                            "name": "node-controller",
                            "labels": {
                                "nebius.com/node-group-id": "mk8snodegroup-controller",
                                "nebius.com/node-group": "cpu",
                                "slurm.nebius.ai/nodeset": "controller",
                            },
                        },
                        "status": {"allocatable": {"cpu": "4"}},
                        "spec": {
                            "taints": [
                                {
                                    "key": "slurm.nebius.ai/nodeset",
                                    "value": "controller",
                                    "effect": "NoSchedule",
                                }
                            ]
                        },
                    },
                ]
            }
        return {"items": []}

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", lambda *_args, **_kwargs: [])

    snapshot = collect_kubectl_soperator_snapshot(kube_context="ctx")

    assert set(snapshot["node_groups"]) == {
        "mk8snodegroup-system",
        "mk8snodegroup-controller",
    }
    assert snapshot["node_groups"]["mk8snodegroup-system"]["selector"] == {
        "key": "nebius.com/node-group-id",
        "operator": "In",
        "values": ["mk8snodegroup-system"],
    }
    assert (
        snapshot["node_groups"]["mk8snodegroup-controller"]["labels"]["slurm.nebius.ai/nodeset"]
        == "controller"
    )


def test_collect_snapshot_discovers_soperator_helm_releases_across_namespaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helm_commands: list[list[str]] = []

    monkeypatch.setattr(
        soperator_onboarding_module,
        "_kubectl_json",
        lambda *_args, **_kwargs: {"items": []},
    )

    def fake_helm_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        helm_commands.append(list(cmd))
        if "-A" in cmd:
            return [
                {
                    "name": "flux-system-soperator-fluxcd",
                    "namespace": "flux-system",
                    "chart": "helm-soperator-2.0.5",
                    "app_version": "2.0.5",
                    "status": "deployed",
                },
                {
                    "name": "soperator",
                    "namespace": "custom",
                    "chart": "helm-soperator-3.0.7",
                    "app_version": "3.0.7",
                    "status": "deployed",
                },
                {
                    "name": "unrelated",
                    "namespace": "default",
                    "chart": "postgres-1.0.0",
                    "app_version": "1.0.0",
                    "status": "deployed",
                },
            ]
        return []

    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", fake_helm_json)

    snapshot = collect_kubectl_soperator_snapshot(kube_context="ctx")

    assert snapshot["helm_releases"] == [
        {
            "name": "flux-system-soperator-fluxcd",
            "namespace": "flux-system",
            "chart": "helm-soperator-2.0.5",
            "app_version": "2.0.5",
            "status": "deployed",
        },
        {
            "name": "soperator",
            "namespace": "custom",
            "chart": "helm-soperator-3.0.7",
            "app_version": "3.0.7",
            "status": "deployed",
        },
    ]
    assert helm_commands
    assert any(
        cmd[:4] == ["helm", "--kube-context", "ctx", "list"] and "-A" in cmd
        for cmd in helm_commands
    )
    assert {cmd[cmd.index("-n") + 1] for cmd in helm_commands if "-n" in cmd} == {
        "nvidia-gpu-operator",
        "nvidia-network-operator",
    }
    assert snapshot["gpu_stack"]["helm_releases"] == []
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.1",
        pinned_app_version="4.0.2",
    )
    assert any(finding.status == "helm-release-detected" for finding in report.findings)


def test_collect_snapshot_discovers_gpu_stack_helm_releases_and_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        if cmd[:5] == ["kubectl", "--context", "ctx", "get", "clusterpolicy,nicclusterpolicy"]:
            return {
                "items": [
                    {
                        "kind": "ClusterPolicy",
                        "metadata": {"name": "cluster-policy"},
                        "status": {"state": "ready"},
                    },
                    {
                        "kind": "NicClusterPolicy",
                        "metadata": {"name": "nic-cluster-policy"},
                        "status": {"state": "ready"},
                    },
                ]
            }
        return {"items": []}

    def fake_helm_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        if "-n" not in cmd:
            return []
        namespace = cmd[cmd.index("-n") + 1]
        if namespace == "nvidia-gpu-operator":
            return [
                {
                    "name": "gpu-operator",
                    "namespace": "nvidia-gpu-operator",
                    "chart": "gpu-operator-v25.10.0",
                    "app_version": "v25.10.0",
                    "status": "deployed",
                }
            ]
        if namespace == "nvidia-network-operator":
            return [
                {
                    "name": "network-operator",
                    "namespace": "nvidia-network-operator",
                    "chart": "network-operator-25.7.0",
                    "app_version": "v25.7.0",
                    "status": "deployed",
                }
            ]
        return []

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", fake_helm_json)

    snapshot = collect_kubectl_soperator_snapshot(kube_context="ctx")

    assert snapshot["gpu_stack"] == {
        "helm_releases": [
            {
                "name": "gpu-operator",
                "namespace": "nvidia-gpu-operator",
                "chart": "gpu-operator-v25.10.0",
                "app_version": "v25.10.0",
                "status": "deployed",
            },
            {
                "name": "network-operator",
                "namespace": "nvidia-network-operator",
                "chart": "network-operator-25.7.0",
                "app_version": "v25.7.0",
                "status": "deployed",
            },
        ],
        "policies": [
            {
                "kind": "ClusterPolicy",
                "metadata": {"name": "cluster-policy"},
                "status": {"state": "ready"},
            },
            {
                "kind": "NicClusterPolicy",
                "metadata": {"name": "nic-cluster-policy"},
                "status": {"state": "ready"},
            },
        ],
    }


def test_collect_snapshot_records_worker_nodeset_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        if cmd[:5] == ["kubectl", "--context", "ctx", "get", "namespace"]:
            return {"items": [{"metadata": {"name": "soperator"}}]}
        if (
            cmd[:5]
            == [
                "kubectl",
                "--context",
                "ctx",
                "get",
                "deployments,statefulsets,daemonsets,pods,services,configmaps,secrets",
            ]
            and "-n" in cmd
        ):
            return {
                "items": [
                    {
                        "kind": "Pod",
                        "metadata": {
                            "name": "worker-gpu-0",
                            "labels": {"slurm.nebius.ai/nodeset": "worker-gpu"},
                        },
                        "status": {"phase": "Running"},
                    }
                ]
            }
        return {"items": []}

    def fake_kubectl_text(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        assert "worker-gpu-0" in cmd
        return json.dumps(
            {
                "lscpu": [
                    {"field": "CPU(s):", "data": "128"},
                    {"field": "Socket(s):", "data": "2"},
                    {"field": "Core(s) per socket:", "data": "32"},
                    {"field": "Thread(s) per core:", "data": "2"},
                ]
            }
        )

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_text", fake_kubectl_text)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", lambda *_args, **_kwargs: [])

    snapshot = collect_kubectl_soperator_snapshot(kube_context="ctx")

    assert snapshot["worker_topology_by_nodeset"] == {
        "worker-gpu": {
            "cpus": 128,
            "boards": 1,
            "sockets": 2,
            "cores_per_socket": 32,
            "threads_per_core": 2,
            "source_pod": "worker-gpu-0",
        }
    }


def test_soperator_onboarding_analyzer_detects_vanilla_cluster_actions() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(),
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "no-soperator-detected"
    assert report.target_version == "0.25.0"
    action_ids = {action.id for action in report.actions}
    assert ONBOARDING_ACTION_INSTALL_SOPERATOR in action_ids
    assert ONBOARDING_ACTION_CREATE_ALIGNED_SFS in action_ids
    assert any(finding.layer == "storage-sfs" for finding in report.findings)
    assert any(
        finding.layer == "topology" and finding.status == "available" for finding in report.findings
    )


def test_soperator_onboarding_analyzer_allows_target_compatible_existing_storage() -> None:
    snapshot = _snapshot()
    snapshot["storage"] = {
        "jail": {"source": "pvc/jail"},
        "controller-spool": {"source": "pvc/controller-spool"},
        "accounting": {"source": "pvc/accounting"},
    }

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert report.state == "no-soperator-detected"
    assert not any(action.id == ONBOARDING_ACTION_CREATE_ALIGNED_SFS for action in report.actions)
    assert any(
        finding.layer == "storage-sfs" and finding.status == "target-compatible"
        for finding in report.findings
    )


def test_soperator_onboarding_analyzer_infers_storage_layout_from_pvc_names() -> None:
    snapshot = _snapshot()
    snapshot["pvcs"] = [
        {"metadata": {"name": "soperator-jail"}},
        {"metadata": {"name": "soperator-controller-spool"}},
        {"metadata": {"name": "soperator-accounting"}},
    ]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert not any(action.id == ONBOARDING_ACTION_CREATE_ALIGNED_SFS for action in report.actions)
    assert any(
        finding.layer == "storage-sfs" and finding.status == "target-compatible"
        for finding in report.findings
    )


def test_soperator_onboarding_analyzer_rejects_partial_existing_storage_layout() -> None:
    snapshot = _snapshot()
    snapshot["storage"] = {"jail": {"source": "pvc/jail"}}
    snapshot["pvcs"] = [{"metadata": {"name": "jail"}}]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert ONBOARDING_ACTION_CREATE_ALIGNED_SFS in {action.id for action in report.actions}
    assert any(
        finding.layer == "storage-sfs" and finding.status == "incompatible"
        for finding in report.findings
    )


def test_soperator_onboarding_analyzer_ignores_noncanonical_slurm_nebius_crds() -> None:
    snapshot = _snapshot()
    snapshot["crds"] = ["slurmjobs.example.nebius-internal.io"]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "no-soperator-detected"
    assert ONBOARDING_ACTION_INSTALL_SOPERATOR in {action.id for action in report.actions}


def test_soperator_onboarding_analyzer_accepts_fallback_selector_labels() -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]  # type: ignore[index]
    node_groups["h100"]["labels"] = {"yandex.cloud/node-group-id": "mk8snodegroup-123"}  # type: ignore[index]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert not any(
        finding.layer == "placements" and finding.status == "selector-required"
        for finding in report.findings
    )


def test_soperator_onboarding_analyzer_accepts_nebius_node_group_id_selector_labels() -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]  # type: ignore[index]
    node_groups["h100"]["labels"] = {"nebius.com/node-group-id": "mk8snodegroup-123"}  # type: ignore[index]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert not any(
        finding.layer == "placements" and finding.status == "selector-required"
        for finding in report.findings
    )


def test_soperator_onboarding_analyzer_offers_upgrade_for_older_release() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator",
                "namespace": "soperator",
                "chart": "soperator-3.0.5",
                "app_version": "3.0.5",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert report.state == "existing-soperator-supported"
    assert report.source_version == "3.0.5"
    assert report.migration_profile_id == "v3-to-target"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in {action.id for action in report.actions}
    assert ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK in {
        action.id for action in report.actions if action.selected
    }
    assert ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION in {action.id for action in report.actions}
    assert ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in {
        action.id for action in report.actions if action.selected
    }
    assert len([action.id for action in report.actions]) == len(
        {action.id for action in report.actions}
    )
    assert any(item.classification == "data-sensitive" for item in report.remediation)
    assert [phase.id for phase in report.migration_plan] == [
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "create-aligned-sfs",
        "online-bulk-data-sync",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
    ]
    assert any(
        finding.layer == "mk8s-node-template" and finding.status == "remediation-planned"
        for finding in report.findings
    )
    assert any(finding.status == "upgrade-available" for finding in report.findings)


def test_soperator_onboarding_analyzer_plans_gpu_stack_remediation_without_manual_action() -> None:
    snapshot = _snapshot(
        release={
            "name": "soperator",
            "namespace": "soperator",
            "chart": "soperator-3.0.5",
            "app_version": "3.0.5",
        }
    )
    gpu_group = snapshot["node_groups"]["h100"]  # type: ignore[index]
    gpu_group["allocatable"] = {"nvidia.com/gpu": "8"}  # type: ignore[index]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    selected_action_ids = {action.id for action in report.actions if action.selected}
    assert ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK in selected_action_ids
    assert any(
        finding.layer == "gpu-stack" and finding.status == "reconcile-planned"
        for finding in report.findings
    )
    assert any(
        finding.layer == "gpu-rdma" and finding.status == "validation-planned"
        for finding in report.findings
    )


def test_soperator_onboarding_analyzer_verifies_healthy_live_gpu_stack() -> None:
    snapshot = _snapshot(
        release={
            "name": "soperator",
            "namespace": "soperator",
            "chart": "soperator-4.0.1-ps.1",
            "app_version": "4.0.1",
            "status": "deployed",
        }
    )
    gpu_group = snapshot["node_groups"]["h100"]  # type: ignore[index]
    gpu_group["labels"].update(  # type: ignore[index,union-attr]
        {
            "nebius.com/drivers-preset": "cuda13.0",
            "nebius.com/nvidia_driver_version": "580.126.09-1ubuntu1",
            "nebius.com/cuda_version": "13.0.3-1",
        }
    )
    snapshot["gpu_stack"] = {
        "helm_releases": [
            {
                "name": "gpu-operator",
                "namespace": "nvidia-gpu-operator",
                "chart": "gpu-operator-v25.10.0",
                "app_version": "v25.10.0",
                "status": "deployed",
            },
            {
                "name": "network-operator",
                "namespace": "nvidia-network-operator",
                "chart": "network-operator-25.7.0",
                "app_version": "v25.7.0",
                "status": "deployed",
            },
        ],
        "policies": [
            {
                "kind": "ClusterPolicy",
                "metadata": {"name": "cluster-policy"},
                "status": {"state": "ready"},
            },
            {
                "kind": "NicClusterPolicy",
                "metadata": {"name": "nic-cluster-policy"},
                "status": {"state": "ready"},
            },
        ],
    }

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    gpu_stack = next(finding for finding in report.findings if finding.layer == "gpu-stack")
    assert gpu_stack.status == "verified"
    assert gpu_stack.severity == "info"
    assert "not a failure signal" in gpu_stack.message
    assert gpu_stack.evidence is not None
    assert gpu_stack.evidence["gpu_stack_verified"] is True
    assert gpu_stack.evidence["gpu_allocatable_node_groups"] == ["h100"]
    assert gpu_stack.evidence["rdma_allocatable_node_groups"] == ["h100"]
    assert gpu_stack.evidence["driver_presets"] == ["cuda13.0"]
    assert gpu_stack.evidence["cluster_policy_ready"] is True
    assert gpu_stack.evidence["nic_cluster_policy_ready"] is True
    assert gpu_stack.evidence["gpu_operator_release"]["chart"] == "gpu-operator-v25.10.0"
    assert gpu_stack.evidence["network_operator_release"]["chart"] == "network-operator-25.7.0"
    assert ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK in {
        action.id for action in report.actions if action.selected
    }


def test_soperator_onboarding_modes_make_compute_only_plan_consistent() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator",
                "namespace": "soperator",
                "chart": "soperator-3.0.5",
                "app_version": "3.0.5",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    adjusted = soperator_onboarding_report_for_modes(
        report,
        storage_mode=ONBOARDING_STORAGE_MODE_KEEP_EXISTING,
        compute_mode=ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS,
    )

    assert not any(item.classification == "data-sensitive" for item in adjusted.remediation)
    assert [phase.id for phase in adjusted.migration_plan] == [
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
    ]
    final_phase = next(
        phase for phase in adjusted.migration_plan if phase.id == "final-control-plane-cutover"
    )
    assert final_phase.progress_label == "Compute Migration: final control-plane cutover"
    assert "storage" not in final_phase.title.lower()
    assert not any("delta sync" in note for note in final_phase.notes)
    approval_phase = next(
        phase for phase in adjusted.migration_plan if phase.id == "customer-approval"
    )
    assert "storage" not in approval_phase.title.lower()
    approval_action = next(
        action
        for action in adjusted.actions
        if action.id == ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE
    )
    assert "storage" not in approval_action.title.lower()


def test_soperator_onboarding_analyzer_reuses_target_compatible_legacy_layout() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _target_compatible_legacy_snapshot(),
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    selected_action_ids = {action.id for action in report.actions if action.selected}
    assert report.state == "existing-soperator-supported"
    assert report.migration_profile_id == "legacy-v1-to-target"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in selected_action_ids
    assert ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in selected_action_ids
    assert ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK in selected_action_ids
    assert ONBOARDING_ACTION_CREATE_ALIGNED_SFS not in selected_action_ids
    assert ONBOARDING_ACTION_PLAN_DATA_MIGRATION not in selected_action_ids
    assert ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION not in selected_action_ids
    assert not any(item.classification == "data-sensitive" for item in report.remediation)
    assert any(
        finding.layer == "storage-sfs" and finding.status == "target-compatible"
        for finding in report.findings
    )
    assert any(
        finding.layer == "placements" and finding.status == "target-compatible"
        for finding in report.findings
    )
    assert [phase.id for phase in report.migration_plan] == [
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
    ]
    rolling_phase = next(
        phase for phase in report.migration_plan if phase.id == "rolling-compute-migration"
    )
    assert rolling_phase.title == "Soperator chart upgrade with existing compute layout"
    final_phase = next(
        phase for phase in report.migration_plan if phase.id == "final-control-plane-cutover"
    )
    assert final_phase.title == "Final Soperator chart cutover"


def test_soperator_onboarding_skips_external_node_template_when_provider_inventory_matches() -> (
    None
):
    report = analyze_soperator_onboarding_snapshot(
        _with_provider_node_template_inventory(_target_compatible_legacy_snapshot()),
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    selected_action_ids = {action.id for action in report.actions if action.selected}
    assert report.state == "existing-soperator-supported"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in selected_action_ids
    assert ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE not in selected_action_ids
    assert "external-node-template-upgrade" not in [phase.id for phase in report.migration_plan]
    finding = next(
        finding
        for finding in report.findings
        if finding.layer == "mk8s-node-template" and finding.status == "target-compatible"
    )
    assert finding.evidence is not None
    assert finding.evidence["matched_node_group_count"] == 6


def test_soperator_onboarding_keeps_external_node_template_when_provider_inventory_is_partial() -> (
    None
):
    snapshot = _with_provider_node_template_inventory(
        _target_compatible_legacy_snapshot(),
        stale_group="worker-cpu",
        stale_gpu_preset="cuda12.4",
    )

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    selected_action_ids = {action.id for action in report.actions if action.selected}
    assert ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in selected_action_ids
    assert "external-node-template-upgrade" in [phase.id for phase in report.migration_plan]
    finding = next(
        finding
        for finding in report.findings
        if finding.layer == "mk8s-node-template" and finding.status == "remediation-planned"
    )
    assert finding.evidence is not None
    remaining = finding.evidence["remaining_node_groups"]
    assert remaining[0]["node_group"] == "worker-cpu"
    assert "CPU node group still has GPU driver preset cuda12.4" in remaining[0]["reasons"]


def test_soperator_onboarding_keeps_external_node_template_when_provider_collection_errors() -> (
    None
):
    snapshot = _with_provider_node_template_inventory(_target_compatible_legacy_snapshot())
    snapshot["provider_collection_errors"] = [
        {
            "command": "nebius mk8s cluster/node-group inventory",
            "message": "partial provider inventory",
        }
    ]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    selected_action_ids = {action.id for action in report.actions if action.selected}
    assert ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in selected_action_ids
    finding = next(
        finding
        for finding in report.findings
        if finding.layer == "mk8s-node-template" and finding.status == "remediation-planned"
    )
    assert finding.evidence is not None
    assert finding.evidence["provider_collection_errors"] == [
        {
            "command": "nebius mk8s cluster/node-group inventory",
            "message": "partial provider inventory",
        }
    ]


def test_soperator_onboarding_modes_follow_analyzer_for_compatible_layout() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _target_compatible_legacy_snapshot(),
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    adjusted = soperator_onboarding_report_for_modes(
        report,
        storage_mode=ONBOARDING_STORAGE_MODE_CREATE_ALIGNED_SFS,
        compute_mode=ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS,
    )

    selected_action_ids = {action.id for action in adjusted.actions if action.selected}
    assert ONBOARDING_ACTION_CREATE_ALIGNED_SFS not in selected_action_ids
    assert ONBOARDING_ACTION_PLAN_DATA_MIGRATION not in selected_action_ids
    assert ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION not in selected_action_ids
    assert "create-aligned-sfs" not in [phase.id for phase in adjusted.migration_plan]
    assert "online-bulk-data-sync" not in [phase.id for phase in adjusted.migration_plan]
    assert any(
        phase.id == "rolling-compute-migration"
        and phase.title == "Soperator chart upgrade with existing compute layout"
        for phase in adjusted.migration_plan
    )


def test_soperator_onboarding_report_from_config_respects_selected_modes() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "install_mode": "onboard-existing-cluster",
                    "version": "4.0.1-ps.1",
                    "values": {},
                }
            ]
        },
        "deploy": {
            "targets": [
                {
                    "instance_id": "cluster1",
                    "kind": "external-mk8s",
                    "inventory": {"node_groups": {}},
                    "soperator_onboarding": {
                        "source_version": "3.0.5",
                        "storage_mode": ONBOARDING_STORAGE_MODE_KEEP_EXISTING,
                        "compute_mode": ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS,
                    },
                }
            ]
        },
    }

    report = build_soperator_onboarding_report_from_config(
        payload,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert "accepted_fingerprint" in report
    assert not any(item.get("classification") == "data-sensitive" for item in report["remediation"])
    assert [phase["id"] for phase in report["migration_plan"]] == [
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
    ]
    final_phase = next(
        phase for phase in report["migration_plan"] if phase["id"] == "final-control-plane-cutover"
    )
    assert final_phase["progress_label"] == "Compute Migration: final control-plane cutover"
    approval_phase = next(
        phase for phase in report["migration_plan"] if phase["id"] == "customer-approval"
    )
    assert "storage" not in approval_phase["title"].lower()
    approval_action = next(
        action
        for action in report["actions"]
        if action["id"] == ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE
    )
    assert "storage" not in approval_action["title"].lower()


def test_soperator_onboarding_report_from_config_honors_accepted_action_contract() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding.update(  # type: ignore[union-attr]
        {
            "state": "existing-soperator-target",
            "storage_mode": ONBOARDING_STORAGE_MODE_KEEP_EXISTING,
            "compute_mode": ONBOARDING_COMPUTE_MODE_KEEP_EXISTING,
            "actions": [
                ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
                ONBOARDING_ACTION_ADOPT_SOPERATOR,
            ],
            "source_version": "4.0.1",
            "target_version": "4.0.1-ps.1",
            "migration_profile_id": "v4-to-target",
        }
    )
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )

    report = build_soperator_onboarding_report_from_config(
        payload,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    selected_actions = [
        action["id"] for action in report["actions"] if action.get("selected") is True
    ]
    assert selected_actions == [
        ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
        ONBOARDING_ACTION_ADOPT_SOPERATOR,
    ]
    assert report["state"] == "existing-soperator-target"
    assert report["source_version"] == "4.0.1"
    assert report["target_version"] == "4.0.1-ps.1"
    assert report["migration_profile_id"] == "v4-to-target"
    assert report["migration_plan"] == []
    assert not any(
        action["id"] == ONBOARDING_ACTION_UPGRADE_SOPERATOR for action in report["actions"]
    )
    assert not any(
        action["id"] == ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE
        for action in report["actions"]
    )


def test_onboarding_report_writer_prefers_matching_source_discovery_report(tmp_path) -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding.update(  # type: ignore[union-attr]
        {
            "state": "existing-soperator-target",
            "storage_mode": ONBOARDING_STORAGE_MODE_KEEP_EXISTING,
            "compute_mode": ONBOARDING_COMPUTE_MODE_KEEP_EXISTING,
            "actions": [
                ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
                ONBOARDING_ACTION_ADOPT_SOPERATOR,
            ],
            "source_version": "4.0.1",
            "target_version": "4.0.1-ps.1",
            "migration_profile_id": "v4-to-target",
        }
    )
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )
    source_report = {
        "report": {
            "schema": "nebius-cxcli-soperator-onboarding/v2",
            "target_ref": "cluster1",
            "analyzed_at": "2026-06-09T00:00:00Z",
            "state": "existing-soperator-target",
            "fingerprint": "live-fingerprint",
            "findings": [
                {
                    "layer": "gpu-stack",
                    "status": "verified",
                    "severity": "info",
                    "message": "Live GPU stack evidence is healthy.",
                    "action_id": ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
                }
            ],
            "actions": [
                {
                    "id": ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
                    "title": "Reconcile target MK8s GPU stack and deploy-time validations",
                    "layer": "gpu-stack",
                    "required": True,
                    "selected": True,
                    "disruptive": False,
                    "reason": "Live target GPU stack is healthy and remains selected.",
                },
                {
                    "id": ONBOARDING_ACTION_ADOPT_SOPERATOR,
                    "title": "Adopt compatible existing Soperator release",
                    "layer": "soperator",
                    "required": False,
                    "selected": True,
                    "disruptive": False,
                    "reason": "Existing target release can be adopted.",
                },
            ],
            "source_version": "4.0.1",
            "target_version": "4.0.1-ps.1",
            "migration_profile_id": "v4-to-target",
            "remediation": [],
            "migration_plan": [],
        }
    }
    write_source_soperator_discovery_report(
        tmp_path,
        target_ref="cluster1",
        snapshot=_snapshot(),
        report=source_report["report"],
    )

    written = write_soperator_onboarding_reports(payload, tmp_path / "generated")

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["findings"][0]["status"] == "verified"
    assert report["actions"][0]["id"] == ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK
    assert report["actions"][1]["id"] == ONBOARDING_ACTION_ADOPT_SOPERATOR
    assert report["migration_plan"] == []


def test_soperator_onboarding_analyzer_accepts_official_helm_soperator_chart_identity() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator",
                "namespace": "soperator",
                "chart": "helm-soperator-3.0.5",
                "app_version": "3.0.5",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert report.state == "existing-soperator-supported"
    assert report.source_version == "3.0.5"
    assert report.migration_profile_id == "v3-to-target"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in {action.id for action in report.actions}


def test_soperator_onboarding_analyzer_accepts_legacy_slurm_operator_chart_identity() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "slurm-operator",
                "namespace": "soperator",
                "chart": "slurm-operator-1.14.1",
                "app_version": "1.14.1",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert report.state == "existing-soperator-supported"
    assert report.source_version == "1.14.1"
    assert report.migration_profile_id == "legacy-v1-to-target"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in {action.id for action in report.actions}


def test_soperator_onboarding_analyzer_accepts_legacy_controller_release_shape() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator-controller",
                "namespace": "soperator-system",
                "chart": "helm-soperator-1.23.3",
                "app_version": "1.23.3",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert report.state == "existing-soperator-supported"
    assert report.source_version == "1.23.3"
    assert report.migration_profile_id == "legacy-v1-to-target"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in {action.id for action in report.actions}


def test_soperator_onboarding_analyzer_accepts_exact_target_release() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator",
                "namespace": "soperator",
                "chart": "soperator-4.0.1-ps.1",
                "app_version": "4.0.1",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert report.state == "existing-soperator-target"
    assert report.source_version == "4.0.1"
    assert report.migration_profile_id == "v4-to-target"
    assert not report.migration_plan
    assert any(finding.status == "target-version" for finding in report.findings)


def test_soperator_onboarding_analyzer_ignores_shadowed_stale_source_record() -> None:
    snapshot = _snapshot()
    snapshot["helm_releases"] = [
        {
            "name": "soperator",
            "namespace": "soperator",
            "chart": "soperator-4.0.1-ps.1",
            "app_version": "4.0.1",
            "revision": "11",
            "status": "deployed",
        },
        {
            "name": "soperator",
            "namespace": "soperator",
            "chart": "helm-slurm-cluster-2.0.5",
            "app_version": "2.0.5",
            "revision": "1",
            "status": "deployed",
        },
    ]
    snapshot["crds"] = ["slurmclusters.slurm.nebius.ai"]
    snapshot["namespaces"] = ["soperator"]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert report.state == "existing-soperator-target"
    assert report.source_version == "4.0.1"
    assert report.migration_profile_id == "v4-to-target"
    assert any(
        finding.status == "stale-source-release" and finding.severity == "info"
        for finding in report.findings
    )
    assert not any(finding.status == "source-version-required" for finding in report.findings)


@pytest.mark.parametrize("live_chart", ["soperator-4.0.1", "soperator-4.0.1-ps.0"])
def test_soperator_onboarding_analyzer_treats_lower_ps_package_as_upgrade(
    live_chart: str,
) -> None:
    snapshot = _snapshot(
        release={
            "name": "soperator",
            "namespace": "soperator",
            "chart": live_chart,
            "app_version": "4.0.1",
        }
    )
    snapshot["storage"] = {"jail": {}, "controller-spool": {}, "accounting": {}}

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    assert report.state == "existing-soperator-supported"
    assert report.migration_profile_id == "v4-to-target"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in {action.id for action in report.actions}
    assert not any(item.classification == "data-sensitive" for item in report.remediation)


def test_soperator_onboarding_analyzer_does_not_downgrade_newer_release() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator",
                "namespace": "soperator",
                "chart": "soperator-0.26.0",
                "app_version": "0.26.0",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    action_ids = {action.id for action in report.actions}
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR not in action_ids
    assert report.state == "existing-soperator-newer"
    assert any(finding.status == "newer-than-cxcli" for finding in report.findings)


def test_soperator_onboarding_analyzer_requires_source_version_for_incompatible_release_identity() -> (
    None
):
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "custom-slurm",
                "namespace": "slurm",
                "chart": "soperator-1.0.0",
                "app_version": "1.0.0",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "existing-soperator-unknown"
    assert report.source_version == ""
    assert any(finding.status == "source-version-required" for finding in report.findings)


def test_soperator_onboarding_analyzer_uses_detected_version_for_nonstandard_release_identity() -> (
    None
):
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator",
                "namespace": "custom",
                "chart": "helm-soperator-3.0.7",
                "app_version": "3.0.7",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.1",
        pinned_app_version="4.0.2",
    )

    assert report.state == "existing-soperator-supported"
    assert report.source_version == "3.0.7"
    assert report.migration_profile_id == "v3-to-target"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in {action.id for action in report.actions}
    assert not any(finding.status == "source-version-required" for finding in report.findings)
    release_finding = next(
        finding for finding in report.findings if finding.status == "helm-release-detected"
    )
    assert release_finding.evidence.get("source_version") == "3.0.7"
    assert release_finding.evidence.get("migration_profile_id") == "v3-to-target"
    assert "name=soperator" in release_finding.message
    assert "namespace=custom" in release_finding.message
    assert "chart=helm-soperator-3.0.7" in release_finding.message
    assert "version=3.0.7" in release_finding.message
    assert "matched migration profile v3-to-target" in release_finding.message


def test_soperator_onboarding_analyzer_source_version_finding_names_profile_contract() -> None:
    snapshot = _snapshot()
    snapshot["crds"] = ["slurmclusters.slurm.nebius.ai"]
    snapshot["namespaces"] = ["soperator"]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.1",
        pinned_app_version="4.0.2",
    )

    findings = [
        finding for finding in report.findings if finding.status == "source-version-required"
    ]
    assert len(findings) == 1
    assert (
        "exact committed migration-profile row or known major-generation profile"
        in findings[0].message
    )


def test_soperator_onboarding_analyzer_matches_manual_source_version_for_crd_only_cluster() -> None:
    snapshot = _snapshot()
    snapshot["crds"] = ["slurmclusters.slurm.nebius.ai"]
    snapshot["namespaces"] = ["soperator"]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
        source_version_override="3.0.5",
    )

    assert report.state == "existing-soperator-supported"
    assert report.source_version == "3.0.5"
    assert report.migration_profile_id == "v3-to-target"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in {action.id for action in report.actions}
    assert any(
        finding.layer == "versions" and finding.status == "source-version-selected"
        for finding in report.findings
    )


def test_soperator_onboarding_source_version_allows_noncanonical_release() -> None:
    snapshot = _snapshot(
        release={
            "name": "soperator",
            "namespace": "custom",
            "chart": "soperator-3.0.5",
            "app_version": "",
        }
    )
    snapshot["crds"] = ["slurmclusters.slurm.nebius.ai"]
    snapshot["namespaces"] = ["soperator", "custom"]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
        source_version_override="3.0.5",
    )

    assert report.state == "existing-soperator-supported"
    assert report.source_version == "3.0.5"
    assert report.migration_profile_id == "v3-to-target"
    assert report.remediation
    assert report.migration_plan
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in {action.id for action in report.actions}
    assert ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE in {action.id for action in report.actions}
    assert ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION in {action.id for action in report.actions}
    assert any(
        finding.layer == "soperator" and finding.status == "source-version-selected"
        for finding in report.findings
    )
    assert any(
        finding.layer == "migration-profile" and finding.status == "matched"
        for finding in report.findings
    )


def test_soperator_onboarding_analyzer_ignores_soperator_sibling_charts() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator-checks",
                "namespace": "soperator",
                "chart": "soperator-checks-1.0.0",
                "app_version": "1.0.0",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "no-soperator-detected"
    assert not any(finding.status == "source-version-required" for finding in report.findings)


def test_soperator_migration_profile_history_covers_known_release_window() -> None:
    versions = set(soperator_migration_profile_versions())

    assert "1.14.1" in versions
    assert "3.0.5" in versions
    assert "4.0.1" in versions
    assert len(versions) >= 61


def test_soperator_migration_profile_generation_fallback_matches_v3_patch() -> None:
    exact_profile = soperator_migration_profile_for_version("3.0.99")
    fallback_profile = soperator_migration_profile_for_version(
        "3.0.99",
        allow_generation_fallback=True,
    )

    assert exact_profile is None
    assert fallback_profile is not None
    assert fallback_profile["profile_match"] == "generation-fallback"
    assert fallback_profile["profile_id"] == "v3-to-target"
    assert fallback_profile["generation"] == "v3"
    assert fallback_profile["requires_aligned_sfs"] is True


def test_soperator_migration_profile_exposes_component_compatibility_axes() -> None:
    profile = soperator_migration_profile_for_version("3.0.5")

    assert profile is not None
    assert profile["profile_id"] == "v3-to-target"
    assert profile["requires_aligned_sfs"] is True
    axes = profile["compatibility_axes"]
    assert axes["compute_layout"] == "replace-and-roll"
    assert axes["storage_layout"] == "create-aligned-sfs-and-migrate"
    assert axes["node_label_layout"]["source_role_label_keys"] == [
        "slurm.nebius.ai/nodeset",
        "slurm.nebius.ai/nodeset-name",
    ]
    assert axes["node_label_layout"]["target_role_label_key"] == "slurm.nebius.ai/nodeset-name"
    assert axes["node_label_layout"]["action"] == "normalize-target-node-labels"
    assert "SlurmCluster" in axes["slurm_components"]
    assert "NodeSet" in axes["slurm_components"]
    assert "NodeConfigurator" in axes["slurm_components"]


def test_soperator_migration_profile_matches_chart_version_aliases() -> None:
    profile = soperator_migration_profile_for_version("1.14.5")

    assert profile is not None
    assert profile["profile_id"] == "legacy-v1-to-target"
    assert "1.14.5" in profile.get("version_aliases", ())


@pytest.mark.parametrize(
    ("source_version", "expected_profile_status"),
    [
        ("3.0.7", "matched"),
        ("3.0.99", "matched-generation"),
    ],
)
def test_soperator_onboarding_analyzer_uses_profile_for_detected_patch(
    source_version: str,
    expected_profile_status: str,
) -> None:
    snapshot = _snapshot()
    snapshot["helm_releases"] = [
        {
            "name": "soperator",
            "namespace": "soperator",
            "chart": f"helm-soperator-{source_version}",
            "app_version": source_version,
            "revision": "1",
            "status": "deployed",
        },
        {
            "name": "soperator",
            "namespace": "custom",
            "chart": f"helm-soperator-{source_version}",
            "app_version": source_version,
            "revision": "1",
            "status": "deployed",
        },
    ]
    snapshot["crds"] = ["slurmclusters.slurm.nebius.ai"]
    snapshot["namespaces"] = ["soperator", "custom"]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.1",
        pinned_app_version="4.0.2",
    )

    assert report.state == "existing-soperator-supported"
    assert report.source_version == source_version
    assert report.migration_profile_id == "v3-to-target"
    assert not any(finding.status == "source-version-required" for finding in report.findings)
    assert any(
        finding.status == expected_profile_status
        and finding.evidence.get("source_version") == source_version
        for finding in report.findings
    )
    release_finding = next(
        finding for finding in report.findings if finding.status == "helm-release-detected"
    )
    assert release_finding.evidence.get("source_version") == source_version
    assert release_finding.evidence.get("migration_profile_id") == "v3-to-target"
    assert "name=soperator" in release_finding.message
    assert "namespace=custom" in release_finding.message
    assert f"chart=helm-soperator-{source_version}" in release_finding.message
    assert f"version={source_version}" in release_finding.message
    assert "matched migration profile v3-to-target" in release_finding.message


def test_soperator_onboarding_analyzer_blocks_collection_errors() -> None:
    report = analyze_soperator_onboarding_snapshot(
        {
            "collection_errors": [
                {
                    "command": "kubectl --context missing get nodes -o json",
                    "message": "context missing",
                }
            ]
        },
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "analysis-incomplete"
    assert any(
        finding.layer == "kubernetes" and finding.status == "analysis-failed"
        for finding in report.findings
    )
    assert report.actions == ()


def test_collect_kubectl_soperator_snapshot_records_shellout_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_called_process_error(command, **kwargs):
        _ = kwargs
        raise subprocess.CalledProcessError(
            1,
            command,
            stderr="forbidden by current context",
        )

    monkeypatch.setattr(
        "nebius_cxcli.soperator_onboarding.subprocess.run", _raise_called_process_error
    )

    snapshot = collect_kubectl_soperator_snapshot(kube_context="missing-context", timeout=1)

    assert snapshot["collection_errors"]
    report = analyze_soperator_onboarding_snapshot(snapshot, target_ref="cluster1")
    assert report.state == "analysis-incomplete"


def test_collect_kubectl_soperator_snapshot_skips_soperator_resources_without_crds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def _run(command, **kwargs):
        _ = kwargs
        command_list = list(command)
        commands.append(command_list)
        assert "slurmclusters,nodeconfigurators,nodesets" not in command_list
        stdout = "[]" if command_list[0] == "helm" else json.dumps({"items": []})
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("nebius_cxcli.soperator_onboarding.subprocess.run", _run)

    snapshot = collect_kubectl_soperator_snapshot(kube_context="vanilla", timeout=1)

    assert snapshot["collection_errors"] == []
    assert snapshot["soperator_resources"] == []
    assert not any("slurmclusters,nodeconfigurators,nodesets" in command for command in commands)


def test_collect_kubectl_soperator_snapshot_records_node_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(command, **kwargs):
        _ = kwargs
        command_list = list(command)
        if command_list[0] == "helm":
            stdout = "[]"
        elif len(command_list) > 4 and command_list[4] == "nodes":
            stdout = json.dumps(
                {
                    "items": [
                        {
                            "metadata": {
                                "name": "computeinstance-a",
                                "labels": {
                                    "node.kubernetes.io/instance-type": "cpu-d3",
                                },
                            },
                            "status": {"allocatable": {"cpu": "3900m"}},
                        },
                        {
                            "metadata": {
                                "name": "computeinstance-b",
                                "labels": {
                                    "node.kubernetes.io/instance-type": "cpu-d3",
                                },
                            },
                            "status": {"allocatable": {"cpu": "3900m"}},
                        },
                    ]
                }
            )
        else:
            stdout = json.dumps({"items": []})
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("nebius_cxcli.soperator_onboarding.subprocess.run", _run)

    snapshot = collect_kubectl_soperator_snapshot(kube_context="vanilla", timeout=1)

    assert snapshot["node_groups"]["cpu-d3"]["nodes"] == [
        "computeinstance-a",
        "computeinstance-b",
    ]


def test_collect_kubectl_soperator_snapshot_queries_only_discovered_soperator_crds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def _run(command, **kwargs):
        _ = kwargs
        command_list = list(command)
        commands.append(command_list)
        if command_list[0] == "helm":
            stdout = "[]"
        elif len(command_list) > 4 and command_list[4] == "crd":
            stdout = json.dumps(
                {
                    "items": [
                        {
                            "metadata": {
                                "name": "slurmclusters.slurm.nebius.ai",
                            }
                        }
                    ]
                }
            )
        else:
            stdout = json.dumps({"items": []})
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("nebius_cxcli.soperator_onboarding.subprocess.run", _run)

    snapshot = collect_kubectl_soperator_snapshot(kube_context="partial", timeout=1)

    assert snapshot["collection_errors"] == []
    assert any(command[4] == "slurmclusters" for command in commands if len(command) > 4)
    assert not any("nodeconfigurators" in command or "nodesets" in command for command in commands)


def _onboarding_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "deploy": {
            "targets": [
                {
                    "instance_id": "cluster1",
                    "kind": "external-mk8s",
                    "ownership": "external",
                    "access": "external",
                    "kube_context": "nebius-cluster1-mk8scluster-123-external",
                    "inventory": {
                        "node_groups": {
                            "cpu-a": {"gpu": False, "node_count": 2},
                            "h100": {"gpu": True, "node_count": 2},
                        }
                    },
                    "soperator_onboarding": {
                        "accepted": True,
                        "analysis_fingerprint": "",
                        "state": "no-soperator-detected",
                        "storage_mode": "create-aligned-sfs",
                        "compute_mode": "keep-existing-compute",
                        "target_version": "4.0.1-ps.1",
                        "source_version": "",
                        "migration_profile_id": "",
                        "actions": [
                            ONBOARDING_ACTION_INSTALL_SOPERATOR,
                            ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
                            ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
                        ],
                    },
                }
            ]
        },
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "group": "slurm",
                    "enabled": True,
                    "install_mode": "onboard-existing-cluster",
                    "repo": "oci://example.invalid/charts",
                    "version": "4.0.1-ps.1",
                    "namespace": "soperator",
                    "release-name": "soperator",
                    "values": {},
                },
                {
                    "id": "nvidia-gpu-operator",
                    "instance_id": "cluster1",
                    "group": "platform",
                    "enabled": True,
                    "target_ref": "cluster1",
                    "repo": "oci://example.invalid/charts",
                    "version": "25.3.0",
                    "namespace": "nvidia-gpu-operator",
                    "release-name": "gpu-operator",
                    "values": {},
                },
            ]
        },
    }
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    target["soperator_onboarding"]["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )
    return payload


def test_onboarding_acceptance_refuses_stale_analysis() -> None:
    payload = _onboarding_payload()
    validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")

    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    target["inventory"]["node_groups"]["extra-cpu"] = {"gpu": False, "node_count": 1}  # type: ignore[index]

    with pytest.raises(ValueError, match="does not have a current accepted"):
        validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")


def test_onboarding_acceptance_refuses_incomplete_analysis_even_with_current_fingerprint() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["accepted"] = True
    onboarding["state"] = "analysis-incomplete"
    onboarding["collection_errors"] = [
        {
            "command": "kubectl --context missing get nodes -o json",
            "message": "context missing",
        }
    ]
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(
        payload,
        target_ref="cluster1",
    )

    assert not soperator_onboarding_is_accepted(payload, target_ref="cluster1")
    with pytest.raises(ValueError, match="does not have a current accepted"):
        validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")


def test_onboarding_acceptance_refuses_unknown_analysis_even_with_current_fingerprint() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["accepted"] = True
    onboarding["state"] = "existing-soperator-unknown"
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(
        payload,
        target_ref="cluster1",
    )

    assert not soperator_onboarding_is_accepted(payload, target_ref="cluster1")
    with pytest.raises(ValueError, match="does not have a current accepted"):
        validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")


def test_onboarding_acceptance_rejects_unsupported_action_even_with_current_fingerprint() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["actions"] = ["remediate-target-gpu-stack"]
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(
        payload,
        target_ref="cluster1",
    )

    assert not soperator_onboarding_is_accepted(payload, target_ref="cluster1")
    with pytest.raises(ValueError, match="unsupported .*remediate-target-gpu-stack"):
        validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")


def test_onboarding_fingerprint_allows_day2_soperator_chart_pin_changes() -> None:
    payload = _onboarding_payload()
    original = soperator_onboarding_fingerprint(payload, target_ref="cluster1")
    chart = payload["apps"]["charts"][0]  # type: ignore[index]
    chart["version"] = "0.26.0"

    assert soperator_onboarding_fingerprint(payload, target_ref="cluster1") == original
    validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")
    validate_runtime_payload(payload)


def test_onboarding_acceptance_rejects_keep_existing_when_aligned_sfs_required() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["storage_mode"] = "keep-existing-storage"
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(
        payload,
        target_ref="cluster1",
    )

    assert not soperator_onboarding_is_accepted(payload, target_ref="cluster1")
    with pytest.raises(ValueError, match="requires .*storage_mode 'create-aligned-sfs'"):
        validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")


def test_onboarding_acceptance_rejects_keep_existing_when_aligned_compute_required() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["compute_mode"] = ONBOARDING_COMPUTE_MODE_KEEP_EXISTING
    onboarding["actions"].append(ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION)
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(
        payload,
        target_ref="cluster1",
    )

    assert not soperator_onboarding_is_accepted(payload, target_ref="cluster1")
    with pytest.raises(
        ValueError,
        match=f"requires .*compute_mode '{ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS}'",
    ):
        validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")


def test_onboarding_fingerprint_ignores_ephemeral_helm_release_metadata() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["helm_releases"] = [
        {
            "name": "soperator",
            "namespace": "soperator",
            "chart": "soperator-0.25.0",
            "app_version": "0.25.0",
            "updated": "2026-05-20 10:00:00",
            "revision": "1",
        }
    ]
    original = soperator_onboarding_fingerprint(payload, target_ref="cluster1")
    onboarding["helm_releases"][0]["updated"] = "2026-05-20 10:05:00"  # type: ignore[index]
    onboarding["helm_releases"][0]["revision"] = "2"  # type: ignore[index]

    assert soperator_onboarding_fingerprint(payload, target_ref="cluster1") == original


def test_runtime_validation_accepts_external_soperator_target_without_mk8s_infra() -> None:
    validate_runtime_payload(_onboarding_payload())


def test_runtime_validation_accepts_soperator_worker_rollout_config() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["node_template_upgrade"] = {  # type: ignore[index]
        "rollout": {
            "strategy": "safe-surge",
            "worker_wave_percent": 1,
            "max_parallel_worker_groups": 10,
            "worker_group_strategy": {
                "max_surge_count": 2,
                "max_unavailable_count": 0,
                "drain_timeout": "30m",
            },
        }
    }
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )

    validate_runtime_payload(payload)


def test_runtime_validation_rejects_soperator_worker_rollout_conflicting_budget() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["node_template_upgrade"] = {  # type: ignore[index]
        "rollout": {
            "strategy": "safe-surge",
            "worker_wave_groups": 1,
            "worker_wave_percent": 1,
        }
    }

    with pytest.raises(ValueError, match="must set only one"):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_soperator_fixed_groups_with_parallel_cap() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["node_template_upgrade"] = {  # type: ignore[index]
        "rollout": {
            "strategy": "safe-surge",
            "worker_wave_groups": 2,
            "max_parallel_worker_groups": 2,
        }
    }

    with pytest.raises(ValueError, match="only supported with worker_wave_percent"):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_zero_surge_worker_wave_fields() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["node_template_upgrade"] = {  # type: ignore[index]
        "rollout": {
            "strategy": "zero-surge",
            "worker_wave_groups": 2,
            "max_parallel_worker_groups": 2,
        }
    }

    with pytest.raises(ValueError, match="zero-surge.*does not use worker wave"):
        validate_runtime_payload(payload)


def test_onboarding_report_writer_persists_target_report(tmp_path) -> None:
    payload = _onboarding_payload()

    written = write_soperator_onboarding_reports(payload, tmp_path / "generated")

    assert len(written) == 1
    report_path = written[0]
    assert report_path == tmp_path / "generated" / "reports" / "soperator-onboarding-cluster1.json"
    report = report_path.read_text(encoding="utf-8")
    assert '"target_ref": "cluster1"' in report
    assert soperator_onboarding_fingerprint(payload, target_ref="cluster1") in report


def test_source_discovery_report_writer_persists_full_snapshot(tmp_path) -> None:
    snapshot = _snapshot()
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    path = write_source_soperator_discovery_report(
        tmp_path,
        target_ref="cluster1",
        snapshot=snapshot,
        report=report,
        cluster_id="mk8scluster-123",
        cluster_name="cluster1",
    )

    assert path == (
        tmp_path
        / "generated"
        / "reports"
        / SOURCE_SOPERATOR_DISCOVERY_REPORT_NAME
        / "cluster1"
        / "manifest.json"
    )
    payload = load_soperator_discovery_bundle(path)
    assert payload["cluster_id"] == "mk8scluster-123"
    assert payload["report"]["state"] == "no-soperator-detected"
    assert payload["snapshot"]["node_groups"]["h100"]["gpu"] is True
    assert (path.parent / "summary.md").exists()


def test_source_discovery_report_writer_is_stable_for_same_discovery(tmp_path) -> None:
    snapshot = _snapshot()
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    )

    path = write_source_soperator_discovery_report(
        tmp_path,
        target_ref="cluster1",
        snapshot=snapshot,
        report=report,
        cluster_id="mk8scluster-123",
        cluster_name="cluster1",
    )
    before = {
        item.name: item.read_text(encoding="utf-8")
        for item in sorted(path.parent.iterdir())
        if item.is_file()
    }

    report_payload = report.to_dict()
    report_payload["analyzed_at"] = "2030-01-01T00:00:00Z"
    write_source_soperator_discovery_report(
        tmp_path,
        target_ref="cluster1",
        snapshot=snapshot,
        report=report_payload,
        cluster_id="mk8scluster-123",
        cluster_name="cluster1",
    )

    after = {
        item.name: item.read_text(encoding="utf-8")
        for item in sorted(path.parent.iterdir())
        if item.is_file()
    }
    assert after == before


def test_onboarding_report_writer_preserves_collection_errors(tmp_path) -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    target["soperator_onboarding"]["collection_errors"] = [  # type: ignore[index]
        {
            "command": "kubectl --context missing get nodes -o json",
            "message": "context missing",
        }
    ]

    written = write_soperator_onboarding_reports(payload, tmp_path / "generated")

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["state"] == "analysis-incomplete"
    assert report["findings"][0]["evidence"]["errors"][0]["message"] == "context missing"
