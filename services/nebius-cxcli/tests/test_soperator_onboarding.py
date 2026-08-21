from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

import pytest

import nebius_cxcli.soperator_discovery as soperator_discovery_module
import nebius_cxcli.soperator_onboarding as soperator_onboarding_module
from nebius_cxcli import soperator_migration as soperator_migration_module
from nebius_cxcli.runtime_validation import validate_runtime_payload
from nebius_cxcli.soperator_discovery import (
    load_soperator_discovery_bundle,
    soperator_discovery_jail_rootfs_record,
    soperator_discovery_k8s_minor_text,
)
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
    SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA,
    SOPERATOR_UPGRADE_SUPPORT_LAYER,
    SOPERATOR_UPGRADE_SUPPORT_STATUS_NOT_VALIDATED,
    SOPERATOR_UPGRADE_SUPPORT_STATUS_SUPPORTED,
    SOPERATOR_UPGRADE_SUPPORT_STATUS_UNSUPPORTED,
    analyze_soperator_onboarding_snapshot,
    build_soperator_onboarding_report_from_config,
    collect_kubectl_soperator_snapshot,
    soperator_migration_profile_for_version,
    soperator_migration_profile_versions,
    soperator_onboarding_fingerprint,
    soperator_onboarding_is_accepted,
    soperator_onboarding_report_for_modes,
    soperator_report_with_accepted_onboarding_contract,
    soperator_runtime_report_with_accepted_upgrade_plan,
    soperator_upgrade_support_findings,
    soperator_upgrade_support_rejected,
    validate_soperator_onboarding_acceptance,
    write_soperator_onboarding_reports,
    write_source_soperator_discovery_report,
)
from nebius_cxcli.soperator_upgrade_campaign import finalize_soperator_upgrade_campaign


def test_controller_bridge_source_record_keeps_identities_and_only_one_way_hashes() -> None:
    workload_items = [
        {
            "apiVersion": "apps.kruise.io/v1beta1",
            "kind": "StatefulSet",
            "metadata": {
                "namespace": "soperator",
                "name": "controller",
                "uid": "controller-workload-uid",
                "resourceVersion": "7",
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "namespace": "soperator",
                "name": "controller-0",
                "uid": "controller-pod-uid",
                "resourceVersion": "9",
            },
            "spec": {
                "nodeName": "controller-node",
                "initContainers": [
                    {
                        "name": "ensure-jail-mounted",
                        "image": "registry.example/slurmctld:slurm24.11.6",
                        "volumeMounts": [{"name": "jail", "mountPath": "/mnt/jail"}],
                    }
                ],
                "containers": [
                    {
                        "name": "slurmctld",
                        "image": "registry.example/slurmctld:slurm24.11.6",
                        "volumeMounts": [
                            {"name": "state", "mountPath": "/var/spool/slurmctld"},
                            {"name": "jail", "mountPath": "/mnt/jail"},
                        ],
                    }
                ],
                "volumes": [
                    {"name": "config", "configMap": {"name": "slurm-config"}},
                    {"name": "auth", "secret": {"secretName": "slurm-auth"}},
                    {
                        "name": "state",
                        "persistentVolumeClaim": {"claimName": "controller-spool-controller-0"},
                    },
                    {
                        "name": "jail",
                        "persistentVolumeClaim": {"claimName": "jail-pvc"},
                    },
                ],
            },
            "status": {
                "containerStatuses": [
                    {
                        "name": "slurmctld",
                        "imageID": "registry.example/slurmctld@sha256:" + "a" * 64,
                    }
                ]
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "namespace": "soperator",
                "name": "slurm-config",
                "uid": "cm-uid",
                "resourceVersion": "10",
            },
            "data": {"slurm.conf": "ClusterName=example"},
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "namespace": "soperator",
                "name": "slurm-auth",
                "uid": "auth-uid",
                "resourceVersion": "11",
            },
            "data": {
                "munge.key": "sensitive-input-one",
                "jwt_hs256.key": "sensitive-input-two",
            },
        },
    ]
    soperator_resources = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {
                "namespace": "soperator",
                "name": "example",
                "uid": "slurmcluster-uid",
                "resourceVersion": "3",
            },
            "spec": {
                "volumeSources": [
                    {
                        "name": "jail",
                        "persistentVolumeClaim": {"claimName": "jail-pvc"},
                    }
                ],
                "slurmNodes": {"controller": {"volumes": {"jail": {"volumeSourceName": "jail"}}}},
            },
        }
    ]
    pvcs = [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "namespace": "soperator",
                "name": "controller-spool-controller-0",
                "uid": "pvc-uid",
                "resourceVersion": "5",
            },
            "spec": {"volumeName": "controller-pv"},
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "namespace": "soperator",
                "name": "jail-pvc",
                "uid": "jail-pvc-uid",
                "resourceVersion": "12",
            },
            "spec": {
                "volumeName": "jail-pv",
                "storageClassName": "jail",
                "accessModes": ["ReadWriteMany"],
                "volumeMode": "Filesystem",
            },
        },
    ]
    pvs = [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": "controller-pv", "uid": "pv-uid", "resourceVersion": "6"},
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {
                "name": "jail-pv",
                "uid": "jail-pv-uid",
                "resourceVersion": "13",
            },
            "spec": {
                "storageClassName": "jail",
                "capacity": {"storage": "1Ti"},
                "accessModes": ["ReadWriteMany"],
                "volumeMode": "Filesystem",
                "local": {"path": "/mnt/jail-store"},
            },
        },
    ]

    record = soperator_onboarding_module._controller_bridge_source_record(  # noqa: SLF001
        workloads=workload_items,
        soperator_resources=soperator_resources,
        pvcs=pvcs,
        pvs=pvs,
        slurm_health={"output": "Slurm 24.11.6"},
    )

    assert record["status"] == "ready"
    assert record["blockers"] == []
    assert record["slurmcluster"] == {
        "api_version": "slurm.nebius.ai/v1",
        "kind": "SlurmCluster",
        "namespace": "soperator",
        "name": "example",
        "uid": "slurmcluster-uid",
        "resource_version": "3",
    }
    assert record["controller_workload"] == {
        "api_version": "apps.kruise.io/v1beta1",
        "kind": "StatefulSet",
        "namespace": "soperator",
        "name": "controller",
        "uid": "controller-workload-uid",
        "resource_version": "7",
    }
    assert record["controller_pod"] == {
        "api_version": "v1",
        "kind": "Pod",
        "namespace": "soperator",
        "name": "controller-0",
        "uid": "controller-pod-uid",
        "resource_version": "9",
        "node_name": "controller-node",
        "container_name": "slurmctld",
        "declared_image": "registry.example/slurmctld:slurm24.11.6",
        "resolved_image_digest": "registry.example/slurmctld@sha256:" + "a" * 64,
        "slurm_version": "24.11.6",
    }
    assert record["controller_pvc"] == {
        "api_version": "v1",
        "kind": "PersistentVolumeClaim",
        "namespace": "soperator",
        "name": "controller-spool-controller-0",
        "uid": "pvc-uid",
        "resource_version": "5",
    }
    assert record["controller_pv"] == {
        "api_version": "v1",
        "kind": "PersistentVolume",
        "namespace": "",
        "name": "controller-pv",
        "uid": "pv-uid",
        "resource_version": "6",
    }
    assert record["jail_pvc"] == {
        "api_version": "v1",
        "kind": "PersistentVolumeClaim",
        "namespace": "soperator",
        "name": "jail-pvc",
        "uid": "jail-pvc-uid",
        "resource_version": "12",
    }
    assert record["jail_pv"] == {
        "api_version": "v1",
        "kind": "PersistentVolume",
        "namespace": "",
        "name": "jail-pv",
        "uid": "jail-pv-uid",
        "resource_version": "13",
    }
    assert record["jail_storage"] == {
        "filesystem_id": "",
        "local_path": "/mnt/jail-store",
        "storage_class_name": "jail",
        "storage_size": "1Ti",
        "access_modes": ["ReadWriteMany"],
        "volume_mode": "Filesystem",
    }
    assert record["configuration"]["config_map_names"] == ["slurm-config"]
    assert record["configuration"]["data_keys"] == ["slurm.conf"]
    assert len(record["configuration"]["fingerprint"]) == 64
    assert record["munge"]["object_names"] == ["slurm-auth"]
    assert record["munge"]["data_keys"] == ["munge.key"]
    assert len(record["munge"]["fingerprint"]) == 64
    assert record["jwt"]["object_names"] == ["slurm-auth"]
    assert record["jwt"]["data_keys"] == ["jwt_hs256.key"]
    assert len(record["jwt"]["fingerprint"]) == 64
    serialized = json.dumps(record, sort_keys=True)
    assert "sensitive-input-one" not in serialized
    assert "sensitive-input-two" not in serialized

    workload_items[1]["metadata"]["uid"] = ""  # type: ignore[index]
    blocked = soperator_onboarding_module._controller_bridge_source_record(  # noqa: SLF001
        workloads=workload_items,
        soperator_resources=soperator_resources,
        pvcs=pvcs,
        pvs=pvs,
        slurm_health={"output": "Slurm 24.11.6"},
    )
    assert blocked["status"] == "blocked"
    assert "controller Pod immutable identity is incomplete" in blocked["blockers"]


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


def _jail_slurmcluster_resource(
    *,
    claim_name: str = "active-rootfs-pvc",
    volume_source_name: str = "active-rootfs",
) -> dict[str, object]:
    return {
        "apiVersion": "slurm.nebius.ai/v1",
        "kind": "SlurmCluster",
        "metadata": {
            "name": "source-cluster",
            "namespace": "soperator",
            "uid": "slurmcluster-uid",
        },
        "spec": {
            "volumeSources": [
                {
                    "name": volume_source_name,
                    "persistentVolumeClaim": {"claimName": claim_name},
                }
            ],
            "slurmNodes": {
                "login": {"volumes": {"jail": {"volumeSourceName": volume_source_name}}}
            },
        },
    }


def _bound_jail_pvc(*, name: str = "active-rootfs-pvc") -> dict[str, object]:
    return {
        "metadata": {"name": name, "namespace": "soperator", "uid": "jail-pvc-uid"},
        "spec": {"volumeName": "opaque-pv-name"},
        "status": {"phase": "Bound"},
    }


def _bound_jail_pv(*, volume_handle: str = "filesystem-actual") -> dict[str, object]:
    return {
        "metadata": {"name": "opaque-pv-name", "uid": "pv-uid-must-not-be-used"},
        "spec": {
            "claimRef": {
                "name": "active-rootfs-pvc",
                "namespace": "soperator",
                "uid": "jail-pvc-uid",
            },
            "csi": {
                "driver": "filestore.csi.storage.nebius.cloud",
                "volumeHandle": volume_handle,
            },
        },
        "status": {"phase": "Bound"},
    }


def test_namespace_job_sanitizer_preserves_only_active_slot_identity_evidence() -> None:
    resources = soperator_onboarding_module._sanitize_namespace_resource_items(  # noqa: SLF001
        [
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {
                    "name": "cluster-populate-jail-passive-slot-b",
                    "namespace": "soperator",
                    "uid": "job-uid",
                    "creationTimestamp": "2026-07-11T00:00:00Z",
                    "labels": {
                        "slurm.nebius.ai/jail-rootfs-refresh": "active-passive",
                        "slurm.nebius.ai/jail-rootfs-slot": "slot-b",
                    },
                },
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {"name": "populate-jail", "image": "registry/populate:target"}
                            ],
                            "volumes": [
                                {
                                    "name": "rootfs",
                                    "persistentVolumeClaim": {
                                        "claimName": "jail-rootfs-slot-b-pvc"
                                    },
                                },
                                {"name": "credentials", "secret": {"secretName": "private"}},
                            ],
                        }
                    }
                },
                "status": {"succeeded": 1},
            }
        ]
    )

    assert resources == [
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": "cluster-populate-jail-passive-slot-b",
                "namespace": "soperator",
                "uid": "job-uid",
                "creationTimestamp": "2026-07-11T00:00:00Z",
                "labels": {
                    "slurm.nebius.ai/jail-rootfs-refresh": "active-passive",
                    "slurm.nebius.ai/jail-rootfs-slot": "slot-b",
                },
            },
            "status": {"succeeded": 1},
            "containers": [{"name": "populate-jail", "image": "registry/populate:target"}],
            "pvc_claim_names": ["jail-rootfs-slot-b-pvc"],
        }
    ]


def test_collect_snapshot_resolves_jail_identity_through_pvc_bound_pv_and_csi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unrelated_name_match = {
        "metadata": {"name": "jail-looking-but-unrelated"},
        "spec": {
            "claimRef": {"name": "another-pvc", "namespace": "other"},
            "csi": {"volumeHandle": "filesystem-wrong"},
        },
        "status": {"phase": "Bound"},
    }

    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        resource = cmd[4] if len(cmd) > 4 else ""
        if resource == "crd":
            return {"items": [{"metadata": {"name": "slurmclusters.slurm.nebius.ai"}}]}
        if resource == "namespace":
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "kubernetes-uid"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid"}},
                ]
            }
        if resource == "pv":
            return {"items": [unrelated_name_match, _bound_jail_pv()]}
        if resource == "pvc":
            return {"items": [_bound_jail_pvc()]}
        if resource == "slurmclusters":
            return {"items": [_jail_slurmcluster_resource()]}
        return {"items": []}

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", lambda *_args, **_kwargs: [])

    snapshot = collect_kubectl_soperator_snapshot(kube_context="ctx")

    assert snapshot["cluster_identity"] == {
        "kubernetes_uid": "kubernetes-uid",
        "soperator_uid": "soperator-uid",
        "slurmcluster_uid": "slurmcluster-uid",
        "jail_filesystem_id": "filesystem-actual",
    }


def test_collect_snapshot_retries_failed_jail_pvc_inventory_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pvc_attempts = 0

    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal pvc_attempts
        resource = cmd[4] if len(cmd) > 4 else ""
        if resource == "crd":
            return {"items": [{"metadata": {"name": "slurmclusters.slurm.nebius.ai"}}]}
        if resource == "namespace":
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "kubernetes-uid"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid"}},
                ]
            }
        if resource == "pv":
            return {"items": [_bound_jail_pv()]}
        if resource == "pvc":
            pvc_attempts += 1
            return {} if pvc_attempts == 1 else {"items": [_bound_jail_pvc()]}
        if resource == "slurmclusters":
            return {"items": [_jail_slurmcluster_resource()]}
        return {"items": []}

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", lambda *_args, **_kwargs: [])

    snapshot = collect_kubectl_soperator_snapshot(kube_context="ctx")

    assert pvc_attempts == 2
    assert snapshot["collection_errors"] == []
    assert snapshot["cluster_identity"]["jail_filesystem_id"] == "filesystem-actual"


def test_collect_snapshot_reports_failed_jail_pvc_read_without_calling_it_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pvc_attempts = 0

    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal pvc_attempts
        resource = cmd[4] if len(cmd) > 4 else ""
        if resource == "crd":
            return {"items": [{"metadata": {"name": "slurmclusters.slurm.nebius.ai"}}]}
        if resource == "namespace":
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "kubernetes-uid"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid"}},
                ]
            }
        if resource == "pv":
            return {"items": [_bound_jail_pv()]}
        if resource == "pvc":
            pvc_attempts += 1
            return {}
        if resource == "slurmclusters":
            return {"items": [_jail_slurmcluster_resource()]}
        return {"items": []}

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", lambda *_args, **_kwargs: [])

    with pytest.raises(RuntimeError, match="live PVC inventory collection failed after 3 attempts"):
        collect_kubectl_soperator_snapshot(kube_context="ctx")

    assert pvc_attempts == 3


def test_collect_snapshot_keeps_authoritative_empty_jail_pvc_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pvc_attempts = 0

    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal pvc_attempts
        resource = cmd[4] if len(cmd) > 4 else ""
        if resource == "crd":
            return {"items": [{"metadata": {"name": "slurmclusters.slurm.nebius.ai"}}]}
        if resource == "namespace":
            return {"items": [{"metadata": {"name": "soperator", "uid": "soperator-uid"}}]}
        if resource == "pv":
            return {"items": [_bound_jail_pv()]}
        if resource == "pvc":
            pvc_attempts += 1
            return {"items": []}
        if resource == "slurmclusters":
            return {"items": [_jail_slurmcluster_resource()]}
        return {"items": []}

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", lambda *_args, **_kwargs: [])

    with pytest.raises(RuntimeError, match="resolved to 0 live PVC objects"):
        collect_kubectl_soperator_snapshot(kube_context="ctx")

    assert pvc_attempts == 1


def test_collect_snapshot_rejects_multiple_slurmcluster_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _jail_slurmcluster_resource()
    second = _jail_slurmcluster_resource()
    second_metadata = second["metadata"]
    assert isinstance(second_metadata, dict)
    second_metadata.update({"name": "second-cluster", "uid": "second-slurmcluster-uid"})

    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        resource = cmd[4] if len(cmd) > 4 else ""
        if resource == "crd":
            return {"items": [{"metadata": {"name": "slurmclusters.slurm.nebius.ai"}}]}
        if resource == "namespace":
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "kubernetes-uid"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid"}},
                ]
            }
        if resource == "pv":
            return {"items": [_bound_jail_pv()]}
        if resource == "pvc":
            return {"items": [_bound_jail_pvc()]}
        if resource == "slurmclusters":
            return {"items": [first, second]}
        return {"items": []}

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", lambda *_args, **_kwargs: [])

    with pytest.raises(
        RuntimeError,
        match="expected exactly one discovered SlurmCluster identity, found 2",
    ):
        collect_kubectl_soperator_snapshot(kube_context="ctx")


def _target_handoff_slurmcluster_resource(
    *,
    uid: str = "target-slurmcluster-uid",
    helm_owned: bool = True,
    chart_name: str = "soperator",
) -> dict[str, object]:
    target = _jail_slurmcluster_resource()
    metadata = target["metadata"]
    assert isinstance(metadata, dict)
    metadata.update({"name": "target-cluster", "uid": uid})
    if helm_owned:
        metadata["annotations"] = {
            "meta.helm.sh/release-name": "soperator",
            "meta.helm.sh/release-namespace": "soperator",
        }
        metadata["labels"] = {
            "app.kubernetes.io/managed-by": "Helm",
            "helm.sh/chart": f"{chart_name}-4.0.2-ps.4",
        }
    return target


def _resume_slurmcluster_scope(
    *,
    mode: str = "target-handoff",
    target_uid: str = "",
) -> dict[str, object]:
    return {
        "schema": "nebius-cxcli-ext-soperator-resume-slurmcluster-identity/v1",
        "mode": mode,
        "phase_id": "rolling-compute-migration",
        "source": {
            "namespace": "soperator",
            "name": "source-cluster",
            "uid": "slurmcluster-uid",
        },
        "target": {
            "namespace": "soperator",
            "name": "target-cluster",
            "uid": target_uid,
        },
        "target_version": "4.0.2-ps.4",
        "allow_target_uid_bootstrap": mode == "target-handoff" and not target_uid,
    }


def test_resume_slurmcluster_identity_bootstraps_exact_helm_owned_target_uid() -> None:
    source = _jail_slurmcluster_resource()
    target = _target_handoff_slurmcluster_resource()

    selected, scope = soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
        (source, target),
        identity_scope=_resume_slurmcluster_scope(),
    )

    assert selected == (source,)
    assert scope["identity_role"] == "source"
    assert scope["target_uid_bootstrapped"] is True
    assert scope["target"] == {
        "namespace": "soperator",
        "name": "target-cluster",
        "uid": "target-slurmcluster-uid",
    }


def test_resume_slurmcluster_identity_accepts_passive_slot_target_creation_phase() -> None:
    source = _jail_slurmcluster_resource()
    target = _target_handoff_slurmcluster_resource()
    identity_scope = _resume_slurmcluster_scope()
    identity_scope["phase_id"] = "populate-jail-refresh"

    selected, scope = soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
        (source, target),
        identity_scope=identity_scope,
    )

    assert selected == (source,)
    assert scope["phase_id"] == "populate-jail-refresh"
    assert scope["target"]["uid"] == "target-slurmcluster-uid"


def test_resume_slurmcluster_identity_accepts_precreation_source_only_handoff() -> None:
    source = _jail_slurmcluster_resource()

    selected, scope = soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
        (source,),
        identity_scope=_resume_slurmcluster_scope(),
    )

    assert selected == (source,)
    assert scope == {}


def test_resume_slurmcluster_identity_source_only_rejects_source_uid_drift() -> None:
    source = _jail_slurmcluster_resource()
    metadata = source["metadata"]
    assert isinstance(metadata, dict)
    metadata["uid"] = "replacement-source-uid"

    with pytest.raises(RuntimeError, match="live source SlurmCluster UID differs"):
        soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
            (source,),
            identity_scope=_resume_slurmcluster_scope(),
        )


def test_resume_slurmcluster_identity_requires_checkpointed_target_to_remain_present() -> None:
    with pytest.raises(RuntimeError, match="expected exactly 2 object"):
        soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
            (_jail_slurmcluster_resource(),),
            identity_scope=_resume_slurmcluster_scope(
                target_uid="target-slurmcluster-uid",
            ),
        )


def test_resume_slurmcluster_identity_bootstrap_requires_checkpoint_target_version() -> None:
    scope = _resume_slurmcluster_scope()
    scope["target_version"] = ""

    with pytest.raises(RuntimeError, match="requires exact Helm ownership"):
        soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
            (_jail_slurmcluster_resource(), _target_handoff_slurmcluster_resource()),
            identity_scope=scope,
        )


def test_resume_slurmcluster_identity_bootstrap_rejects_wrong_chart_same_version() -> None:
    with pytest.raises(RuntimeError, match="requires exact Helm ownership"):
        soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
            (
                _jail_slurmcluster_resource(),
                _target_handoff_slurmcluster_resource(chart_name="unrelated"),
            ),
            identity_scope=_resume_slurmcluster_scope(),
        )


@pytest.mark.parametrize("binding", ["source", "target"])
def test_resume_slurmcluster_identity_requires_explicit_checkpoint_namespace(
    binding: str,
) -> None:
    scope = _resume_slurmcluster_scope()
    bound_ref = scope[binding]
    assert isinstance(bound_ref, dict)
    bound_ref.pop("namespace")

    with pytest.raises(
        RuntimeError, match=f"incomplete {binding} binding|incomplete target binding"
    ):
        soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
            (_jail_slurmcluster_resource(), _target_handoff_slurmcluster_resource()),
            identity_scope=scope,
        )


@pytest.mark.parametrize(
    ("resources", "scope", "message"),
    [
        (
            (_target_handoff_slurmcluster_resource(),),
            _resume_slurmcluster_scope(),
            "exactly one immutable source binding",
        ),
        (
            (
                _jail_slurmcluster_resource(),
                _target_handoff_slurmcluster_resource(),
                {
                    "kind": "SlurmCluster",
                    "metadata": {
                        "namespace": "soperator",
                        "name": "unexpected",
                        "uid": "unexpected-uid",
                    },
                },
            ),
            _resume_slurmcluster_scope(),
            "expected exactly 1 or 2 object",
        ),
        (
            (
                {
                    **_jail_slurmcluster_resource(),
                    "metadata": {
                        "namespace": "soperator",
                        "name": "source-cluster",
                        "uid": "replaced-source-uid",
                    },
                },
                _target_handoff_slurmcluster_resource(),
            ),
            _resume_slurmcluster_scope(),
            "live source SlurmCluster UID differs",
        ),
        (
            (
                _jail_slurmcluster_resource(),
                _target_handoff_slurmcluster_resource(uid="replacement-target-uid"),
            ),
            _resume_slurmcluster_scope(target_uid="target-slurmcluster-uid"),
            "live target SlurmCluster UID differs",
        ),
        (
            (
                _jail_slurmcluster_resource(),
                _target_handoff_slurmcluster_resource(helm_owned=False),
            ),
            _resume_slurmcluster_scope(),
            "requires exact Helm ownership",
        ),
    ],
)
def test_resume_slurmcluster_identity_rejects_unbound_or_drifted_inventory(
    resources: tuple[dict[str, object], ...],
    scope: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
            resources,
            identity_scope=scope,
        )


def test_resume_slurmcluster_identity_source_cleanup_accepts_before_and_after_delete() -> None:
    source = _jail_slurmcluster_resource()
    target = _target_handoff_slurmcluster_resource()
    scope = _resume_slurmcluster_scope(
        mode="source-cleanup",
        target_uid="target-slurmcluster-uid",
    )

    before, before_scope = soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
        (source, target),
        identity_scope=scope,
    )
    after, after_scope = soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
        (target,),
        identity_scope=scope,
    )

    assert before == (source,)
    assert before_scope["identity_role"] == "source"
    assert after == (target,)
    assert after_scope["identity_role"] == "target"


def test_scope_resume_snapshot_source_cleanup_projects_checkpoint_source_identity() -> None:
    target = _target_handoff_slurmcluster_resource()
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"namespace": "soperator", "name": "login"},
    }
    snapshot = {
        "cluster_identity": {
            "kubernetes_uid": "kubernetes-uid",
            "soperator_uid": "soperator-uid",
            "slurmcluster_uid": "target-slurmcluster-uid",
            "jail_filesystem_id": "filesystem-uid",
        },
        "soperator_resources": [target, service],
    }

    scoped = soperator_onboarding_module._scope_soperator_resume_snapshot_identity(  # noqa: SLF001
        snapshot,
        identity_scope=_resume_slurmcluster_scope(
            mode="source-cleanup",
            target_uid="target-slurmcluster-uid",
        ),
    )

    assert snapshot["cluster_identity"]["slurmcluster_uid"] == "target-slurmcluster-uid"  # type: ignore[index]
    assert scoped["cluster_identity"]["slurmcluster_uid"] == "slurmcluster-uid"
    assert scoped["resume_slurmcluster_identity"]["identity_role"] == "target"
    assert scoped["resume_slurmcluster_identity"]["target"]["uid"] == "target-slurmcluster-uid"
    assert scoped["identity_soperator_resources"] == [target, service]


def test_scope_resume_snapshot_rejects_foreign_target_uid_without_mutation() -> None:
    target = _target_handoff_slurmcluster_resource(uid="foreign-target-uid")
    snapshot = {
        "cluster_identity": {"slurmcluster_uid": "foreign-target-uid"},
        "soperator_resources": [target],
    }

    with pytest.raises(RuntimeError, match="live target SlurmCluster UID differs"):
        soperator_onboarding_module._scope_soperator_resume_snapshot_identity(  # noqa: SLF001
            snapshot,
            identity_scope=_resume_slurmcluster_scope(
                mode="source-cleanup",
                target_uid="target-slurmcluster-uid",
            ),
        )

    assert snapshot["cluster_identity"]["slurmcluster_uid"] == "foreign-target-uid"  # type: ignore[index]


def test_resume_slurmcluster_identity_target_only_rejects_source_reappearance() -> None:
    scope = _resume_slurmcluster_scope(
        mode="target-only",
        target_uid="target-slurmcluster-uid",
    )

    with pytest.raises(RuntimeError, match="expected exactly 1 object"):
        soperator_onboarding_module._resume_slurmcluster_identity_resources(  # noqa: SLF001
            (_jail_slurmcluster_resource(), _target_handoff_slurmcluster_resource()),
            identity_scope=scope,
        )


def test_collect_snapshot_scopes_exact_dual_cr_resume_to_immutable_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _jail_slurmcluster_resource()
    target = _target_handoff_slurmcluster_resource()

    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        resource = cmd[4] if len(cmd) > 4 else ""
        if resource == "crd":
            return {"items": [{"metadata": {"name": "slurmclusters.slurm.nebius.ai"}}]}
        if resource == "namespace":
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "kubernetes-uid"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid"}},
                ]
            }
        if resource == "pv":
            return {"items": [_bound_jail_pv()]}
        if resource == "pvc":
            return {"items": [_bound_jail_pvc()]}
        if resource == "slurmclusters":
            return {"items": [source, target]}
        return {"items": []}

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", lambda *_args, **_kwargs: [])

    snapshot = collect_kubectl_soperator_snapshot(
        kube_context="ctx",
        slurmcluster_identity_scope=_resume_slurmcluster_scope(),
    )

    assert snapshot["cluster_identity"]["slurmcluster_uid"] == "slurmcluster-uid"
    assert len(snapshot["soperator_resources"]) == 2
    assert snapshot["identity_soperator_resources"] == [source]
    assert snapshot["resume_slurmcluster_identity"]["target"]["uid"] == ("target-slurmcluster-uid")


def test_collect_snapshot_source_cleanup_after_delete_retains_logical_source_uid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target_handoff_slurmcluster_resource()

    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        resource = cmd[4] if len(cmd) > 4 else ""
        if resource == "crd":
            return {"items": [{"metadata": {"name": "slurmclusters.slurm.nebius.ai"}}]}
        if resource == "namespace":
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "kubernetes-uid"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid"}},
                ]
            }
        if resource == "pv":
            return {"items": [_bound_jail_pv()]}
        if resource == "pvc":
            return {"items": [_bound_jail_pvc()]}
        if resource == "slurmclusters":
            return {"items": [target]}
        return {"items": []}

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", lambda *_args, **_kwargs: [])

    snapshot = collect_kubectl_soperator_snapshot(
        kube_context="ctx",
        slurmcluster_identity_scope=_resume_slurmcluster_scope(
            mode="source-cleanup",
            target_uid="target-slurmcluster-uid",
        ),
    )

    assert snapshot["resume_slurmcluster_identity"]["identity_role"] == "target"
    assert snapshot["cluster_identity"]["slurmcluster_uid"] == "slurmcluster-uid"
    assert snapshot["identity_soperator_resources"] == [target]


def test_collect_snapshot_resume_reads_exact_slurmclusters_when_crd_inventory_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _jail_slurmcluster_resource()
    target = _target_handoff_slurmcluster_resource()
    calls: list[str] = []

    def fake_kubectl_json(cmd, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        resource = cmd[4] if len(cmd) > 4 else ""
        calls.append(resource)
        if resource == "crd":
            return {"items": []}
        if resource == "namespace":
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "kubernetes-uid"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid"}},
                ]
            }
        if resource == "pv":
            return {"items": [_bound_jail_pv()]}
        if resource == "pvc":
            return {"items": [_bound_jail_pvc()]}
        if resource == "slurmclusters":
            return {"items": [source, target]}
        return {"items": []}

    monkeypatch.setattr(soperator_onboarding_module, "_kubectl_json", fake_kubectl_json)
    monkeypatch.setattr(soperator_onboarding_module, "_helm_json", lambda *_args, **_kwargs: [])

    snapshot = collect_kubectl_soperator_snapshot(
        kube_context="ctx",
        slurmcluster_identity_scope=_resume_slurmcluster_scope(),
    )

    assert "slurmclusters" in calls
    assert snapshot["soperator_resources"] == [source, target]
    assert snapshot["identity_soperator_resources"] == [source]
    assert snapshot["resume_slurmcluster_identity"]["target"]["uid"] == ("target-slurmcluster-uid")


def test_slurmcluster_identity_requires_immutable_uid() -> None:
    slurmcluster = _jail_slurmcluster_resource()
    metadata = slurmcluster["metadata"]
    assert isinstance(metadata, dict)
    metadata["uid"] = ""

    with pytest.raises(RuntimeError, match="has no immutable UID"):
        soperator_onboarding_module._soperator_slurmcluster_uid(  # noqa: SLF001
            (slurmcluster,)
        )


def test_jail_identity_rejects_zero_discovered_pvc_bindings() -> None:
    slurmcluster = _jail_slurmcluster_resource()
    slurmcluster["spec"] = {"volumeSources": []}

    with pytest.raises(RuntimeError, match="exactly one discovered Jail PVC binding, found 0"):
        soperator_onboarding_module._soperator_jail_filesystem_identity(  # noqa: SLF001
            soperator_resources=(slurmcluster,),
            pvcs=(),
            pvs=(),
        )


def test_jail_identity_rejects_ambiguous_discovered_pvc_bindings() -> None:
    slurmcluster = _jail_slurmcluster_resource()
    slurmcluster["spec"] = {
        "slurmNodes": {
            "controller": {"volumes": {"jail": {"persistentVolumeClaim": {"claimName": "jail-a"}}}},
            "login": {"volumes": {"jail": {"persistentVolumeClaim": {"claimName": "jail-b"}}}},
        }
    }

    with pytest.raises(RuntimeError, match="exactly one discovered Jail PVC binding, found 2"):
        soperator_onboarding_module._soperator_jail_filesystem_identity(  # noqa: SLF001
            soperator_resources=(slurmcluster,),
            pvcs=(),
            pvs=(),
        )


@pytest.mark.parametrize("pvc_count", (0, 2))
def test_jail_identity_rejects_missing_or_duplicate_live_pvc_binding(
    pvc_count: int,
) -> None:
    pvc = _bound_jail_pvc()

    with pytest.raises(
        RuntimeError,
        match=rf"active-rootfs-pvc resolved to {pvc_count} live PVC objects",
    ):
        soperator_onboarding_module._soperator_jail_filesystem_identity(  # noqa: SLF001
            soperator_resources=(_jail_slurmcluster_resource(),),
            pvcs=tuple(pvc for _index in range(pvc_count)),
            pvs=(_bound_jail_pv(),),
        )


def test_jail_identity_defers_active_passive_local_pv_to_provider_attachment() -> None:
    pv = _bound_jail_pv()
    pv["spec"].pop("csi")  # type: ignore[union-attr]
    pv["spec"]["local"] = {"path": "/mnt/jail-store/rootfs/slot-a"}  # type: ignore[index]

    assert (
        soperator_onboarding_module._soperator_jail_filesystem_identity(  # noqa: SLF001
            soperator_resources=(_jail_slurmcluster_resource(),),
            pvcs=(_bound_jail_pvc(),),
            pvs=(pv,),
        )
        == ""
    )


def test_jail_identity_rejects_non_csi_non_local_pv() -> None:
    pv = _bound_jail_pv()
    pv["spec"].pop("csi")  # type: ignore[union-attr]

    with pytest.raises(RuntimeError, match="has no CSI volumeHandle"):
        soperator_onboarding_module._soperator_jail_filesystem_identity(  # noqa: SLF001
            soperator_resources=(_jail_slurmcluster_resource(),),
            pvcs=(_bound_jail_pvc(),),
            pvs=(pv,),
        )


def test_jail_identity_resolves_one_provider_attachment_for_active_passive_slots() -> None:
    node_groups = {
        role: {
            "provider": {
                "node_group_name": role,
                "node_template": {
                    "filesystems": [
                        {
                            "mount_tag": "jail",
                            "existing_filesystem": {"id": "filesystem-jail"},
                        }
                    ]
                },
            }
        }
        for role in ("controller", "login", "worker")
    }

    assert (
        soperator_onboarding_module.soperator_jail_filesystem_identity_from_provider_node_groups(
            node_groups
        )
        == "filesystem-jail"
    )


def test_jail_identity_rejects_provider_attachment_drift_between_node_groups() -> None:
    node_groups = {
        "controller": {
            "provider": {
                "node_template": {
                    "filesystems": [
                        {
                            "mount_tag": "jail",
                            "existing_filesystem": {"id": "filesystem-a"},
                        }
                    ]
                }
            }
        },
        "worker": {
            "provider": {
                "node_template": {
                    "filesystems": [
                        {
                            "mount_tag": "jail",
                            "existing_filesystem": {"id": "filesystem-b"},
                        }
                    ]
                }
            }
        },
    }

    with pytest.raises(RuntimeError, match="multiple backing filesystems"):
        soperator_onboarding_module.soperator_jail_filesystem_identity_from_provider_node_groups(
            node_groups
        )


def test_jail_identity_rejects_csi_and_provider_attachment_conflict() -> None:
    node_groups = {
        "worker": {
            "provider": {
                "node_template": {
                    "filesystems": [
                        {
                            "mount_tag": "jail",
                            "existing_filesystem": {"id": "filesystem-provider"},
                        }
                    ]
                }
            }
        }
    }

    with pytest.raises(RuntimeError, match="CSI identity.*conflicts"):
        soperator_onboarding_module.soperator_jail_filesystem_identity_from_provider_node_groups(
            node_groups,
            kubernetes_identity="filesystem-csi",
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


def test_discovery_collects_kubernetes_node_and_slurm_health_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def _run(args, **_kwargs):  # type: ignore[no-untyped-def]
        command = tuple(str(item) for item in args)
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            "Slurmctld(primary) at controller is UP\n",
            "",
        )

    monkeypatch.setattr(soperator_onboarding_module.subprocess, "run", _run)
    health = soperator_onboarding_module._collect_slurm_health_from_login(  # noqa: SLF001
        kube_context="ctx",
        workloads={
            "items": [
                {
                    "kind": "Pod",
                    "metadata": {
                        "name": "login-0",
                        "labels": {"app.kubernetes.io/component": "login"},
                    },
                    "status": {"phase": "Running"},
                }
            ]
        },
        timeout=30,
        extra_env=None,
    )
    nodes = soperator_onboarding_module._sanitize_kubernetes_node_items(  # noqa: SLF001
        [
            {
                "metadata": {
                    "name": "worker-0",
                    "uid": "worker-0-uid",
                    "labels": {"nebius.com/node-group": "gpu"},
                },
                "spec": {"unschedulable": False},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        ]
    )

    assert health["checked"] is True
    assert health["healthy"] is True
    assert calls[0][-2:] == ("scontrol", "ping")
    assert nodes == [
        {
            "metadata": {
                "name": "worker-0",
                "uid": "worker-0-uid",
                "labels": {"nebius.com/node-group": "gpu"},
            },
            "spec": {"unschedulable": False},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
    ]


def test_kubectl_list_inventory_retries_transient_timeout_without_recording_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def _run(args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise subprocess.TimeoutExpired(args, timeout=30)
        return subprocess.CompletedProcess(args, 0, '{"items": []}', "")

    monkeypatch.setattr(soperator_onboarding_module.subprocess, "run", _run)
    errors: list[dict[str, object]] = []

    payload = soperator_onboarding_module._kubectl_list_json_with_bounded_retry(  # noqa: SLF001
        ["kubectl", "get", "pods", "-o", "json"],
        30,
        errors=errors,
    )

    assert payload == {"items": []}
    assert attempts == 2
    assert errors == []


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
                "deployments,statefulsets,statefulsets.apps.kruise.io,"
                "daemonsets,pods,jobs,services,configmaps,secrets",
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
        "create-aligned-sfs",
        "controller-ha-bridge",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "online-bulk-data-sync",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "populate-jail-refresh",
        "validation-and-rollback-hold",
        "retire-old-resources",
    ]
    controller_bridge = next(
        phase for phase in report.migration_plan if phase.id == "controller-ha-bridge"
    )
    assert controller_bridge.title == "Establish the temporary two-controller Slurm HA bridge"
    assert (
        controller_bridge.progress_label
        == "Controller Bridge: transferring source authority to HA pair"
    )
    assert controller_bridge.requires_customer_approval is True
    assert any("roll-forward only" in note for note in controller_bridge.notes)
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
        "controller-ha-bridge",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "populate-jail-refresh",
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
    placement_finding = next(
        finding
        for finding in report.findings
        if finding.layer == "placements" and finding.status == "target-compatible"
    )
    assert "accepted in-place or blue-green compute migration" in placement_finding.message
    assert "source node-group mutation" in placement_finding.message
    assert [phase.id for phase in report.migration_plan] == [
        "discovery-and-plan",
        "customer-approval",
        "controller-ha-bridge",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "populate-jail-refresh",
        "validation-and-rollback-hold",
        "retire-old-resources",
    ]
    rolling_phase = next(
        phase for phase in report.migration_plan if phase.id == "rolling-compute-migration"
    )
    assert rolling_phase.title == "Soperator chart upgrade on accepted target compute"
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


def test_soperator_onboarding_plans_external_node_template_for_same_soperator_version_k8s_hop() -> (
    None
):
    snapshot = _with_provider_node_template_inventory(_target_compatible_legacy_snapshot())
    snapshot["helm_releases"] = [
        {
            "name": "soperator",
            "namespace": "soperator",
            "chart": "soperator-4.0.2-ps.3",
            "app_version": "4.0.2",
        }
    ]
    provider = snapshot["provider"]
    assert isinstance(provider, dict)
    mk8s_cluster = provider["mk8s_cluster"]
    assert isinstance(mk8s_cluster, dict)
    mk8s_cluster["control_plane_version"] = "1.33"
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    for raw_group in node_groups.values():
        assert isinstance(raw_group, dict)
        group_provider = raw_group["provider"]
        assert isinstance(group_provider, dict)
        template = group_provider["node_template"]
        assert isinstance(template, dict)
        template["k8s_version"] = "1.33"

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.3",
        pinned_app_version="4.0.2",
        target_k8s_version="1.34",
    )

    selected_action_ids = {action.id for action in report.actions if action.selected}
    assert report.state == "existing-soperator-target"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR not in selected_action_ids
    assert ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in selected_action_ids
    assert ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE in selected_action_ids
    assert [phase.id for phase in report.migration_plan] == [
        "discovery-and-plan",
        "customer-approval",
        "controller-ha-bridge",
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
    assert rolling_phase.title == "Kubernetes target-version compute migration"
    final_phase = next(
        phase for phase in report.migration_plan if phase.id == "final-control-plane-cutover"
    )
    assert final_phase.title == "Final Kubernetes target-version compute and control-plane cutover"
    finding = next(
        finding
        for finding in report.findings
        if finding.layer == "mk8s-node-template" and finding.status == "remediation-planned"
    )
    assert "separately accepted in-place or blue-green" in finding.message
    assert "uses blue/green replacements" not in finding.message
    assert finding.evidence is not None
    assert finding.evidence["control_plane"]["current_k8s_version"] == "1.33"
    assert finding.evidence["control_plane"]["target_k8s_version"] == "1.34"


def test_default_migration_plan_makes_k8s_only_upgrade_blue_green() -> None:
    phases = soperator_onboarding_module._default_soperator_migration_plan(
        include_data_migration=False,
        include_compute_migration=False,
        include_soperator_upgrade=False,
        include_external_node_template_upgrade=True,
    )

    assert [phase.id for phase in phases] == [
        "discovery-and-plan",
        "customer-approval",
        "controller-ha-bridge",
        "external-node-template-upgrade",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
    ]
    assert (
        next(phase.title for phase in phases if phase.id == "rolling-compute-migration")
        == "Kubernetes target-version compute migration"
    )


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


def test_soperator_onboarding_explicit_aligned_modes_override_compatible_layout() -> None:
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
    assert ONBOARDING_ACTION_CREATE_ALIGNED_SFS in selected_action_ids
    assert ONBOARDING_ACTION_PLAN_DATA_MIGRATION in selected_action_ids
    assert ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION in selected_action_ids
    assert "create-aligned-sfs" in [phase.id for phase in adjusted.migration_plan]
    assert "online-bulk-data-sync" in [phase.id for phase in adjusted.migration_plan]
    assert any(phase.id == "rolling-compute-migration" for phase in adjusted.migration_plan)


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
        "controller-ha-bridge",
        "external-node-template-upgrade",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "populate-jail-refresh",
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
        kube_context="nebius-cluster1-mk8scluster-123-external",
    )

    written = write_soperator_onboarding_reports(payload, tmp_path / "generated")

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["onboard_description"] == (
        soperator_onboarding_module.EXT_SOPERATOR_ONBOARD_DESCRIPTION
    )
    assert report["findings"][0]["status"] == "verified"
    assert report["actions"][0]["id"] == ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK
    assert report["actions"][1]["id"] == ONBOARDING_ACTION_ADOPT_SOPERATOR
    assert report["migration_plan"] == []


def test_accepted_onboarding_contract_preserves_live_evidence_and_replaces_plan() -> None:
    raw_report = {
        "schema": "nebius-cxcli-soperator-onboarding/v2",
        "target_ref": "cluster1",
        "analyzed_at": "2026-07-13T00:00:00Z",
        "state": "existing-soperator-supported",
        "fingerprint": "live-fingerprint",
        "findings": [
            {
                "layer": "versions",
                "status": "migration-required",
                "severity": "warning",
                "message": "Live Soperator requires an upgrade.",
                "action_id": ONBOARDING_ACTION_UPGRADE_SOPERATOR,
                "evidence": {"live": True},
            }
        ],
        "actions": [
            {
                "id": ONBOARDING_ACTION_UPGRADE_SOPERATOR,
                "title": "Upgrade Soperator",
                "layer": "versions",
                "selected": True,
                "reason": "Live version differs.",
            }
        ],
        "source_version": "1.22.3",
        "target_version": "4.0.2-ps.4",
        "migration_profile_id": "legacy-v1-to-target",
        "migration_plan": [],
        "live_extension": {"preserved": True},
    }
    onboarding = {
        "state": "existing-soperator-supported",
        "actions": [
            ONBOARDING_ACTION_UPGRADE_SOPERATOR,
            ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
            ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
            ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
            ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
        ],
        "source_version": "1.22.3",
        "target_version": "4.0.2-ps.4",
        "migration_profile_id": "legacy-v1-to-target",
        "collection_errors": [],
    }

    effective = soperator_report_with_accepted_onboarding_contract(raw_report, onboarding)

    assert effective["fingerprint"] == "live-fingerprint"
    assert effective["analyzed_at"] == "2026-07-13T00:00:00Z"
    assert effective["live_extension"] == {"preserved": True}
    assert effective["findings"][0]["evidence"] == {"live": True}
    assert [action["id"] for action in effective["actions"]] == onboarding["actions"]
    phase_ids = [phase["id"] for phase in effective["migration_plan"]]
    assert phase_ids.count("create-aligned-sfs") == 1
    assert phase_ids.count("online-bulk-data-sync") == 1
    assert phase_ids.index("create-aligned-sfs") < phase_ids.index("controller-ha-bridge")
    assert phase_ids.index("controller-ha-bridge") < phase_ids.index("online-bulk-data-sync")


def test_runtime_accepted_plan_preserves_fresh_discovery_evidence() -> None:
    raw_report = {
        "state": "existing-soperator-supported",
        "fingerprint": "fresh-live-fingerprint",
        "findings": [
            {
                "layer": "placements",
                "status": "compatible",
                "severity": "info",
                "message": "Fresh placement evidence.",
                "action_id": ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
            }
        ],
        "actions": [
            {
                "id": ONBOARDING_ACTION_UPGRADE_SOPERATOR,
                "selected": True,
                "title": "Live upgrade recommendation",
            }
        ],
        "migration_plan": [],
    }
    onboarding = {
        "state": "existing-soperator-target",
        "actions": [
            ONBOARDING_ACTION_UPGRADE_SOPERATOR,
            ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
            ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
            ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
            ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
        ],
    }

    effective = soperator_runtime_report_with_accepted_upgrade_plan(raw_report, onboarding)

    assert effective["state"] == "existing-soperator-supported"
    assert effective["fingerprint"] == "fresh-live-fingerprint"
    assert effective["findings"] == raw_report["findings"]
    assert effective["actions"] == raw_report["actions"]
    phase_ids = [phase["id"] for phase in effective["migration_plan"]]
    assert "create-aligned-sfs" in phase_ids
    assert "online-bulk-data-sync" in phase_ids


def test_runtime_accepted_plan_preserves_known_unaccepted_live_action() -> None:
    raw_report = {
        "state": "existing-soperator-supported",
        "actions": [
            {
                "id": ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
                "selected": True,
            }
        ],
    }
    onboarding = {
        "actions": [ONBOARDING_ACTION_UPGRADE_SOPERATOR],
        "upgrade_path": {
            "segments": [{"actions": [ONBOARDING_ACTION_UPGRADE_SOPERATOR]}],
        },
    }

    effective = soperator_runtime_report_with_accepted_upgrade_plan(raw_report, onboarding)

    assert effective["actions"] == raw_report["actions"]
    assert "external-node-template-upgrade" not in {
        phase["id"] for phase in effective["migration_plan"]
    }


def test_runtime_accepted_plan_rejects_unknown_selected_action() -> None:
    raw_report = {
        "state": "existing-soperator-supported",
        "actions": [{"id": "future-destructive-action", "selected": True}],
    }
    onboarding = {
        "actions": [ONBOARDING_ACTION_UPGRADE_SOPERATOR],
        "upgrade_path": {
            "segments": [{"actions": [ONBOARDING_ACTION_UPGRADE_SOPERATOR]}],
        },
    }

    with pytest.raises(ValueError, match="future-destructive-action"):
        soperator_runtime_report_with_accepted_upgrade_plan(raw_report, onboarding)


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
                "chart": "soperator-4.0.2-ps.3",
                "app_version": "4.0.2",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.3",
        pinned_app_version="4.0.2",
    )

    assert report.state == "existing-soperator-target"
    assert report.source_version == "4.0.2"
    assert report.migration_profile_id == "v4-to-target"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR not in {action.id for action in report.actions}
    assert any(finding.status == "target-version" for finding in report.findings)


def _single_support_finding(report: object) -> dict[str, object]:
    findings = soperator_upgrade_support_findings(report)  # type: ignore[arg-type]
    assert len(findings) == 1
    return dict(findings[0])


def test_soperator_support_policy_rejects_legacy_target_before_k8s_133() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator-controller",
                "namespace": "soperator-system",
                "chart": "helm-soperator-1.22.4",
                "app_version": "1.22.4",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="1.22.5",
        pinned_app_version="1.22.5",
        target_k8s_version="1.33",
    )

    finding = _single_support_finding(report)
    assert finding["layer"] == SOPERATOR_UPGRADE_SUPPORT_LAYER
    assert finding["status"] == SOPERATOR_UPGRADE_SUPPORT_STATUS_UNSUPPORTED
    assert finding["severity"] == "required"
    assert finding["evidence"]["rule_id"] == "k8s-1-33-requires-soperator-1-23"
    assert finding["evidence"]["target_k8s_version"] == "1.33"
    assert soperator_upgrade_support_rejected(report)


def test_soperator_support_policy_rejects_pre_123_target_before_source_age_rule() -> None:
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
        pinned_chart_version="1.22.5",
        pinned_app_version="1.22.5",
        target_k8s_version="1.33",
    )

    finding = _single_support_finding(report)
    assert finding["status"] == SOPERATOR_UPGRADE_SUPPORT_STATUS_UNSUPPORTED
    assert finding["evidence"]["rule_id"] == "k8s-1-33-requires-soperator-1-23"
    assert soperator_upgrade_support_rejected(report)


def test_soperator_support_policy_marks_unmatched_legacy_source_not_validated() -> None:
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
        target_k8s_version="1.34",
    )

    finding = _single_support_finding(report)
    assert finding["status"] == SOPERATOR_UPGRADE_SUPPORT_STATUS_NOT_VALIDATED
    assert finding["evidence"]["rule_id"] == "legacy-before-1-22-not-validated"
    assert soperator_upgrade_support_rejected(report)


def test_soperator_support_policy_supports_122_plus_before_k8s_133() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator-controller",
                "namespace": "soperator-system",
                "chart": "helm-soperator-1.22.3",
                "app_version": "1.22.3",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.3",
        pinned_app_version="4.0.2",
        target_k8s_version="1.32",
    )

    finding = _single_support_finding(report)
    assert finding["status"] == SOPERATOR_UPGRADE_SUPPORT_STATUS_SUPPORTED
    assert finding["evidence"]["rule_id"] == "k8s-before-1-33-soperator-1-22-plus-supported"
    assert finding["evidence"]["target_version"] == "4.0.2-ps.3"
    assert finding["evidence"]["target_app_version"] == "4.0.2"
    assert finding["evidence"]["target_chart_version"] == "4.0.2-ps.3"
    assert finding["evidence"]["approved_target_chart_version"] == "4.0.2-ps.3"
    assert finding["evidence"]["recommended_order"] == {"soperator_after_k8s_min": "1.32"}
    assert not soperator_upgrade_support_rejected(report)


def test_soperator_support_policy_marks_intermediate_target_not_validated() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator-controller",
                "namespace": "soperator-system",
                "chart": "helm-soperator-1.22.4",
                "app_version": "1.22.4",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="1.23.3",
        pinned_app_version="1.23.3",
        target_k8s_version="1.33",
    )

    finding = _single_support_finding(report)
    assert finding["status"] == SOPERATOR_UPGRADE_SUPPORT_STATUS_NOT_VALIDATED
    assert finding["severity"] == "required"
    assert finding["evidence"]["rule_id"] == "soperator-target-before-cxcli-pin-not-validated"
    assert soperator_upgrade_support_rejected(report)


def test_soperator_support_policy_accepts_v4_target_on_k8s_133() -> None:
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
        pinned_chart_version="4.0.2-ps.3",
        pinned_app_version="4.0.2",
        target_k8s_version="1.33",
    )

    finding = _single_support_finding(report)
    assert finding["status"] == SOPERATOR_UPGRADE_SUPPORT_STATUS_SUPPORTED
    assert finding["evidence"]["rule_id"] == "k8s-1-33-soperator-4-supported"
    assert finding["evidence"]["target_version"] == "4.0.2-ps.3"
    assert finding["evidence"]["target_app_version"] == "4.0.2"
    assert finding["evidence"]["recommended_order"] == {"soperator_after_k8s_min": "1.32"}
    assert not soperator_upgrade_support_rejected(report)


@pytest.mark.parametrize("target_chart_version", ["4.0.2", "4.0.2-ps.1", "4.0.2-ps.999"])
def test_soperator_support_policy_marks_same_app_non_pin_target_not_validated(
    target_chart_version: str,
) -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator-controller",
                "namespace": "soperator-system",
                "chart": "helm-soperator-1.22.4",
                "app_version": "1.22.4",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version=target_chart_version,
        pinned_app_version="4.0.2",
        approved_target_chart_version="4.0.2-ps.3",
        target_k8s_version="1.33",
    )

    finding = _single_support_finding(report)
    assert finding["status"] == SOPERATOR_UPGRADE_SUPPORT_STATUS_NOT_VALIDATED
    assert finding["severity"] == "required"
    assert finding["evidence"]["rule_id"] == "soperator-target-same-app-non-cxcli-pin-not-validated"
    assert finding["evidence"]["target_version"] == target_chart_version
    assert finding["evidence"]["target_app_version"] == "4.0.2"
    assert finding["evidence"]["approved_target_chart_version"] == "4.0.2-ps.3"
    assert soperator_upgrade_support_rejected(report)


def test_soperator_support_policy_marks_newer_upstream_target_not_validated() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator-controller",
                "namespace": "soperator-system",
                "chart": "helm-soperator-1.22.4",
                "app_version": "1.22.4",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="4.1.1",
        pinned_app_version="4.1.1",
        target_k8s_version="1.33",
    )

    finding = _single_support_finding(report)
    assert finding["status"] == SOPERATOR_UPGRADE_SUPPORT_STATUS_NOT_VALIDATED
    assert finding["severity"] == "required"
    assert finding["evidence"]["rule_id"] == "soperator-target-newer-than-cxcli-pin-not-validated"
    assert soperator_upgrade_support_rejected(report)


def test_soperator_support_policy_marks_unmatched_path_not_validated() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator-controller",
                "namespace": "soperator-system",
                "chart": "helm-soperator-1.22.4",
                "app_version": "1.22.4",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="5.0.0",
        pinned_app_version="5.0.0",
        target_k8s_version="1.33",
    )

    finding = _single_support_finding(report)
    assert finding["status"] == SOPERATOR_UPGRADE_SUPPORT_STATUS_NOT_VALIDATED
    assert finding["evidence"]["rule_id"] == "default-not-validated"
    assert soperator_upgrade_support_rejected(report)


def test_host_driver_jail_cuda_policy_parses_committed_driver_facts() -> None:
    policy = soperator_onboarding_module.soperator_host_driver_jail_cuda_policy()

    assert policy["schema"] == ("nebius-cxcli-soperator-host-driver-jail-cuda-policy/v1")
    assert policy["driver_presets"] == {
        "cuda12.8": {"driver_branch": 570},
        "cuda13.0": {"driver_branch": 580},
    }
    assert policy["jail_cuda"]["12.9.0"]["minimum_driver_branch"] == 575
    assert policy["jail_cuda"]["12.9.0"]["allow_newer_driver_branches"] is True


def test_host_driver_jail_cuda_policy_allows_cuda13_and_rejects_cuda12_8() -> None:
    managed_gpu_operator = {
        "component_id": "nvidia-gpu-operator",
        "chart": "gpu-operator",
        "chart_version": "v25.10.0",
        "repository": (
            "oci://cr.eu-north1.nebius.cloud/marketplace/nebius/"
            "nvidia-gpu-operator/chart/gpu-operator"
        ),
    }
    target = {
        "name": "worker-gpu",
        "gpu_software_mode": "provider-managed",
        "target": {"drivers_preset": "cuda13.0"},
    }

    soperator_onboarding_module.validate_soperator_host_driver_jail_cuda_compatibility(
        jail_cuda_version="12.9.0",
        node_group_targets=(target,),
        managed_gpu_operator_target=managed_gpu_operator,
    )

    target["target"]["drivers_preset"] = "cuda12.8"  # type: ignore[index]
    with pytest.raises(RuntimeError, match=r"branch 570, below Jail CUDA 12\.9\.0.*575"):
        soperator_onboarding_module.validate_soperator_host_driver_jail_cuda_compatibility(
            jail_cuda_version="12.9.0",
            node_group_targets=(target,),
            managed_gpu_operator_target=managed_gpu_operator,
        )


def test_host_driver_jail_cuda_policy_operator_managed_requires_exact_gpu_operator() -> None:
    policy = soperator_onboarding_module.soperator_host_driver_jail_cuda_policy()
    required = policy["jail_cuda"]["12.9.0"]["operator_managed"][  # type: ignore[index]
        "required_managed_gpu_operator"
    ]
    target = {
        "name": "worker-gpu",
        "gpu_software_mode": "operator-managed",
        "target": {"drivers_preset": ""},
    }

    soperator_onboarding_module.validate_soperator_host_driver_jail_cuda_compatibility(
        jail_cuda_version="12.9",
        node_group_targets=(target,),
        managed_gpu_operator_target=required,
    )

    drifted = dict(required)
    drifted["chart_version"] = "v25.9.0"
    with pytest.raises(
        RuntimeError,
        match="operator-managed mode requires the exact committed managed GPU operator target",
    ):
        soperator_onboarding_module.validate_soperator_host_driver_jail_cuda_compatibility(
            jail_cuda_version="12.9.0",
            node_group_targets=(target,),
            managed_gpu_operator_target=drifted,
        )


def test_host_driver_jail_cuda_policy_fails_closed_for_unknown_cuda_or_preset() -> None:
    provider_target = {
        "name": "worker-gpu",
        "gpu_software_mode": "provider-managed",
        "target": {"drivers_preset": "cuda12.7"},
    }
    with pytest.raises(RuntimeError, match="drivers_preset cuda12.7 is not committed"):
        soperator_onboarding_module.validate_soperator_host_driver_jail_cuda_compatibility(
            jail_cuda_version="12.9.0",
            node_group_targets=(provider_target,),
            managed_gpu_operator_target={},
        )
    with pytest.raises(RuntimeError, match="has no rule for Jail CUDA 13.0.0"):
        soperator_onboarding_module.validate_soperator_host_driver_jail_cuda_compatibility(
            jail_cuda_version="13.0.0",
            node_group_targets=(provider_target,),
            managed_gpu_operator_target={},
        )


def test_host_driver_jail_cuda_policy_rejects_malformed_committed_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        soperator_onboarding_module,
        "_load_soperator_migration_profile_data",
        lambda: {
            "host_driver_jail_cuda_policy": {
                "schema": (
                    soperator_onboarding_module.SOPERATOR_HOST_DRIVER_JAIL_CUDA_POLICY_SCHEMA
                ),
                "driver_presets": {"cuda13.0": {"driver_branch": "580"}},
                "jail_cuda": {},
            }
        },
    )

    with pytest.raises(RuntimeError, match="driver_branch must be a positive integer"):
        soperator_onboarding_module.soperator_host_driver_jail_cuda_policy()


def test_soperator_support_policy_rejects_stale_override_evidence() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator-controller",
                "namespace": "soperator-system",
                "chart": "helm-soperator-1.22.4",
                "app_version": "1.22.4",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="1.22.5",
        pinned_app_version="1.22.5",
        target_k8s_version="1.33",
    ).to_dict()
    finding = next(
        item for item in report["findings"] if item["layer"] == SOPERATOR_UPGRADE_SUPPORT_LAYER
    )
    finding["evidence"]["override_used"] = True

    assert soperator_upgrade_support_rejected(report)


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


def test_soperator_onboarding_analyzer_uses_resource_labels_when_helm_release_missing() -> None:
    snapshot = _snapshot()
    snapshot["helm_releases"] = []
    snapshot["crds"] = ["slurmclusters.slurm.nebius.ai", "nodesets.slurm.nebius.ai"]
    snapshot["namespaces"] = ["soperator"]
    snapshot["soperator_resources"] = [
        {
            "kind": "NodeSet",
            "metadata": {
                "name": "worker",
                "namespace": "soperator",
                "labels": {
                    "app.kubernetes.io/instance": "soperator",
                    "app.kubernetes.io/name": "soperator",
                    "app.kubernetes.io/version": "4.0.2",
                    "helm.sh/chart": "soperator-4.0.2-ps.3",
                },
            },
        }
    ]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.3",
        pinned_app_version="4.0.2",
    )

    assert report.state == "existing-soperator-target"
    assert report.source_version == "4.0.2"
    assert not any(finding.status == "source-version-required" for finding in report.findings)
    assert any(
        finding.status == "resource-label-version-detected"
        and finding.evidence["release"]["chart_version"] == "4.0.2-ps.3"
        for finding in report.findings
    )


def test_soperator_onboarding_analyzer_prefers_canonical_resource_labels() -> None:
    snapshot = _snapshot()
    snapshot["helm_releases"] = []
    snapshot["crds"] = ["slurmclusters.slurm.nebius.ai", "nodesets.slurm.nebius.ai"]
    snapshot["namespaces"] = ["soperator", "custom"]
    snapshot["soperator_resources"] = [
        {
            "kind": "NodeSet",
            "metadata": {
                "name": "custom-worker",
                "namespace": "custom",
                "labels": {
                    "app.kubernetes.io/instance": "soperator",
                    "app.kubernetes.io/name": "soperator",
                    "app.kubernetes.io/version": "3.0.7",
                    "helm.sh/chart": "soperator-3.0.7",
                },
            },
        },
        {
            "kind": "NodeSet",
            "metadata": {
                "name": "worker",
                "namespace": "soperator",
                "labels": {
                    "app.kubernetes.io/instance": "soperator",
                    "app.kubernetes.io/name": "soperator",
                    "app.kubernetes.io/version": "4.0.2",
                    "helm.sh/chart": "soperator-4.0.2-ps.3",
                },
            },
        },
    ]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.3",
        pinned_app_version="4.0.2",
    )

    assert report.state == "existing-soperator-target"
    assert report.source_version == "4.0.2"


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
                        "compute_migration": {
                            "mode": "in-place",
                            "slurm_scheduling_pause": True,
                            "node_group_rollout": {
                                "strategy": "zero-surge",
                                "worker": {
                                    "max_unavailable": "all",
                                    "drain_timeout": "10m",
                                    "max_parallel_groups": 8,
                                },
                            },
                        },
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


def _onboarding_campaign_payload() -> dict[str, object]:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    target["cluster_id"] = "mk8scluster-123"  # type: ignore[index]
    return payload


def _locked_upgrade_path_payload() -> dict[str, object]:
    jail_rootfs = {
        "current_image": "cr.example/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0",
        "current_version": "4.0.2-slurm25.11.3-cuda12.9.0",
        "current_source": "completed-populate-jail-job",
        "current_job_name": "mk8s-populate-jail",
        "live_desired_image": "cr.example/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0",
        "live_desired_version": "4.0.2-slurm25.11.3-cuda12.9.0",
        "live_desired_source": "slurmcluster.spec.populateJail.image",
        "slurmcluster_name": "mk8s",
        "target_image": "cr.example/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0",
        "target_version": "4.0.2-slurm25.11.3-cuda12.9.0",
        "target_cuda_version": "12.9.0",
        "target_digest": "",
        "target_identity_warning": ("Artifact digest unavailable; campaign pins catalog identity."),
        "target_source": "pinned-chart-defaults",
        "refresh_required": False,
        "reason": "current populate-jail image matches target chart image",
    }
    campaign = {
        "schema": SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA,
        "locked": True,
        "identity": {
            "project_id": "project-456",
            "cluster_id": "mk8scluster-123",
            "cluster_name": "cluster1",
            "target_ref": "cluster1",
            "kubernetes_uid": "kubernetes-uid-123",
            "soperator_uid": "soperator-uid-123",
            "slurmcluster_uid": "slurmcluster-uid-123",
            "jail_filesystem_id": "filesystem-123",
        },
        "source_provenance": {
            "discovery": "live-kubernetes-and-nebius-sdk",
            "provider_capabilities": ("nebius-sdk-list-versions-and-compatibility-matrix"),
            "component_catalog": "component_sources.yaml",
            "support_policy": "committed-soperator-upgrade-support-policy",
            "source_contract": "campaign-source-waypoints",
        },
        "catalog_fingerprint": "a" * 64,
        "capabilities_source": "nebius-sdk",
        "source_k8s_version": "1.32",
        "target_k8s_version": "1.33",
        "mk8s": {
            "control_plane": {"source_version": "1.32", "target_version": "1.33"},
            "node_groups": [
                {
                    "id": "nodegroup-123",
                    "name": "cpu-a",
                    "role": "worker",
                    "platform": "cpu-d3",
                    "preset": "8vcpu-32gb",
                    "gpu_software_mode": "none",
                    "fixed_size": 1,
                    "provider_identity": {
                        "resource_uid": "nodegroup-123",
                        "resource_version": 201,
                        "reservation_policy": "",
                        "reservation_ids": [],
                        "failure_domains": [],
                        "gpu_cluster_id": "",
                    },
                    "source": {
                        "kubernetes_version": "1.32",
                        "os": "ubuntu22.04",
                        "drivers_preset": "",
                    },
                    "target": {
                        "kubernetes_version": "1.33",
                        "os": "ubuntu24.04",
                        "drivers_preset": "",
                    },
                    "compatibility_source": "nebius-sdk-get-compatibility-matrix",
                }
            ],
        },
        "soperator_app": {
            "current_version": "1.23.3",
            "target_version": "4.0.2",
            "upgrade_required": True,
        },
        "soperator_chart": {
            "current_version": "1.23.3",
            "target_version": "4.0.2-ps.3",
            "upgrade_required": True,
        },
        "migration_profile": {
            "id": "legacy-v1-to-target",
            "execution_contract": {
                "source_controller_pause": {
                    "required_before_target_compute_reconcile": True,
                }
            },
        },
        "jail_rootfs": jail_rootfs,
        "support_status": "supported",
        "support_rule_id": "k8s-1-33-soperator-4-supported",
        "recommended_order": [],
        "recommended_order_policy": {},
        "final_targets": {
            "kubernetes": "1.33",
            "soperator_app": "4.0.2",
            "soperator_chart": "4.0.2-ps.3",
            "jail_rootfs_image": ("cr.example/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0"),
            "jail_rootfs_version": "4.0.2-slurm25.11.3-cuda12.9.0",
            "jail_cuda_version": "12.9.0",
            "jail_artifact_digest": "",
            "jail_artifact_identity_warning": (
                "Artifact digest unavailable; campaign pins catalog identity."
            ),
        },
        "managed_operators": {},
        "compute_migration": {
            "mode": "in-place",
            "slurm_scheduling_pause": True,
            "node_group_rollout": {
                "strategy": "zero-surge",
                "worker": {
                    "max_unavailable": "all",
                    "resolved_max_unavailable": {"nodegroup-123": 1},
                    "drain_timeout": "10m",
                    "max_parallel_groups": 8,
                },
            },
        },
        "segments": [
            {
                "id": "segment-1-kubernetes-1-32-1-33",
                "index": 1,
                "depends_on": [],
                "kind": "external-node-template-hop",
                "title": "Kubernetes 1.32 -> 1.33",
                "current_k8s_version": "1.32",
                "target_k8s_version": "1.33",
                "soperator_app": {
                    "current_version": "1.23.3",
                    "target_version": "4.0.2",
                    "upgrade_required": True,
                },
                "soperator_chart": {
                    "current_version": "1.23.3",
                    "target_version": "4.0.2-ps.3",
                    "upgrade_required": True,
                },
                "jail_rootfs": jail_rootfs,
                "k8s_upgrade_required": True,
                "soperator_upgrade_required": True,
                "mk8s": {
                    "control_plane": {
                        "source_version": "1.32",
                        "target_version": "1.33",
                    },
                    "node_groups": [
                        {
                            "id": "nodegroup-123",
                            "name": "cpu-a",
                            "role": "worker",
                            "fixed_size": 1,
                            "platform": "cpu-d3",
                            "preset": "8vcpu-32gb",
                            "gpu_software_mode": "none",
                            "provider_identity": {
                                "resource_uid": "nodegroup-123",
                                "resource_version": 201,
                                "reservation_policy": "",
                                "reservation_ids": [],
                                "failure_domains": [],
                                "gpu_cluster_id": "",
                            },
                            "source": {
                                "kubernetes_version": "1.32",
                                "os": "ubuntu22.04",
                                "drivers_preset": "",
                            },
                            "target": {
                                "kubernetes_version": "1.33",
                                "os": "ubuntu24.04",
                                "drivers_preset": "",
                            },
                            "compatibility_source": ("nebius-sdk-get-compatibility-matrix"),
                        }
                    ],
                },
                "actions": [
                    ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
                    ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
                    ONBOARDING_ACTION_UPGRADE_SOPERATOR,
                ],
            }
        ],
    }
    return finalize_soperator_upgrade_campaign(
        campaign,
        created_at="2026-07-11T12:00:00+00:00",
    )


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
    payload = _onboarding_campaign_payload()
    onboarding = payload["deploy"]["targets"][0]["soperator_onboarding"]  # type: ignore[index]
    onboarding["upgrade_path"] = _locked_upgrade_path_payload()  # type: ignore[index]
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )
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
    payload = _onboarding_campaign_payload()
    onboarding = payload["deploy"]["targets"][0]["soperator_onboarding"]  # type: ignore[index]
    onboarding["upgrade_path"] = _locked_upgrade_path_payload()  # type: ignore[index]
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )

    validate_runtime_payload(payload)


def test_runtime_validation_rejects_managed_gpu_operator_pins_for_cpu_only_campaign() -> None:
    payload = _onboarding_campaign_payload()
    onboarding = payload["deploy"]["targets"][0]["soperator_onboarding"]  # type: ignore[index]
    campaign = _locked_upgrade_path_payload()
    campaign["managed_operators"] = {"gpu": {}}
    onboarding["upgrade_path"] = finalize_soperator_upgrade_campaign(  # type: ignore[index]
        campaign,
        created_at="2026-07-11T12:00:00+00:00",
    )
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )

    with pytest.raises(
        ValueError,
        match=r"upgrade_path\.managed_operators must be empty.*CPU-only",
    ):
        validate_runtime_payload(payload)


def test_runtime_validation_accepts_soperator_onboarding_with_locked_upgrade_path() -> None:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["actions"] = [  # type: ignore[index]
        ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
        ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
    ]
    onboarding["storage_mode"] = ONBOARDING_STORAGE_MODE_KEEP_EXISTING  # type: ignore[index]
    onboarding["node_template_upgrade"] = {  # type: ignore[index]
        "target_k8s_version": "1.33",
        "target_os": "ubuntu24.04",
        "target_gpu_stack_preset": "cuda13.0",
    }
    onboarding["upgrade_path"] = _locked_upgrade_path_payload()  # type: ignore[index]
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )

    validate_runtime_payload(payload)


def test_runtime_validation_rejects_target_only_bad_soperator_onboarding() -> None:
    payload = _onboarding_payload()
    payload["apps"]["charts"] = []  # type: ignore[index]
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["actions"] = "upgrade-soperator"  # type: ignore[index]

    with pytest.raises(ValueError, match=r"soperator_onboarding\.actions must be a list"):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_soperator_onboarding_missing_actions() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding.pop("actions")  # type: ignore[union-attr]

    with pytest.raises(ValueError, match=r"soperator_onboarding\.actions is required"):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_soperator_onboarding_unknown_key() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["analysis_report"] = {}  # type: ignore[index]

    with pytest.raises(ValueError, match=r"soperator_onboarding has unsupported field"):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_soperator_onboarding_actions_string_even_when_fingerprinted() -> (
    None
):
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["actions"] = "upgrade-soperator"  # type: ignore[index]
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )

    with pytest.raises(ValueError, match=r"soperator_onboarding\.actions must be a list"):
        validate_runtime_payload(payload)


@pytest.mark.parametrize("target_k8s_version", ["not-a-version", "1.33.1"])
def test_runtime_validation_rejects_soperator_onboarding_bad_target_k8s_version(
    target_k8s_version: str,
) -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["node_template_upgrade"] = {  # type: ignore[index]
        "target_k8s_version": target_k8s_version,
    }
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )

    with pytest.raises(ValueError, match=r"target_k8s_version must be a Kubernetes major\.minor"):
        validate_runtime_payload(payload)


def test_runtime_validation_requires_v6_campaign_for_external_onboarding() -> None:
    payload = _onboarding_campaign_payload()
    onboarding = payload["deploy"]["targets"][0]["soperator_onboarding"]  # type: ignore[index]
    onboarding.pop("upgrade_path", None)  # type: ignore[union-attr]

    with pytest.raises(
        ValueError,
        match=r"upgrade_path is required and must use 'nebius-cxcli-ext-soperator-upgrade-campaign/v6'",
    ):
        validate_runtime_payload(payload)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda path: path.update({"schema": "nebius-cxcli-ext-soperator-upgrade-path/v2"}),
            r"upgrade_path\.schema must be",
        ),
        (
            lambda path: path["segments"][0].update({"actions": ["not-a-current-action"]}),
            r"upgrade_path\.segments\[0\]\.actions\[0\] has unsupported value",
        ),
        (
            lambda path: path["segments"][0].pop("title"),
            r"upgrade_path\.segments\[0\]\.title must be a non-empty string",
        ),
        (
            lambda path: path.pop("support_status"),
            r"upgrade_path\.support_status is required",
        ),
        (
            lambda path: path["identity"].pop("kubernetes_uid"),
            r"upgrade_path\.identity\.kubernetes_uid must be a non-empty string",
        ),
        (
            lambda path: path["segments"][0].update({"index": 2}),
            r"upgrade_path\.segments\[0\]\.index must be 1",
        ),
        (
            lambda path: path["segments"][0]["mk8s"]["control_plane"].update(
                {"target_version": "1.34"}
            ),
            r"upgrade_path\.segments\[0\]\.mk8s\.control_plane must advance exactly one",
        ),
        (
            lambda path: path["segments"][0]["mk8s"]["node_groups"][0]["target"].pop(
                "drivers_preset"
            ),
            r"upgrade_path\.segments\[0\]\.mk8s\.node_groups\[0\]\.target\.drivers_preset is required",
        ),
        (
            lambda path: path["segments"][0]["mk8s"]["node_groups"][0].update({"preset": ""}),
            r"upgrade_path\.segments\[0\]\.mk8s\.node_groups\[0\]\.preset must be a non-empty string",
        ),
        (
            lambda path: path["segments"][0]["mk8s"]["node_groups"][0].update(
                {"unexpected": "value"}
            ),
            r"upgrade_path\.segments\[0\]\.mk8s\.node_groups\[0\] has unsupported field",
        ),
        (
            lambda path: path["source_provenance"].pop("source_contract"),
            r"upgrade_path\.source_provenance\.source_contract must be a non-empty string",
        ),
        (
            lambda path: path["final_targets"].update({"kubernetes": "1.34"}),
            r"upgrade_path\.final_targets\.kubernetes must match target_k8s_version",
        ),
        (
            lambda path: (
                path["mk8s"]["node_groups"][0].update(
                    {
                        "platform": "gpu-h100-sxm",
                        "gpu_software_mode": "operator-managed",
                    }
                ),
                path["segments"][0]["mk8s"]["node_groups"][0].update(
                    {
                        "platform": "gpu-h100-sxm",
                        "gpu_software_mode": "operator-managed",
                    }
                ),
            ),
            r"upgrade_path\.managed_operators\.gpu must be a mapping",
        ),
        (
            lambda path: path["segments"][0].update({"depends_on": ["not-prior"]}),
            r"upgrade_path\.segments\[0\]\.depends_on must be \[\]",
        ),
        (
            lambda path: path["compute_migration"].update({"mode": "rolling"}),
            r"upgrade_path\.compute_migration\.mode must be one of",
        ),
        (
            lambda path: path["compute_migration"].pop("node_group_rollout"),
            r"upgrade_path\.compute_migration\.node_group_rollout must be a mapping",
        ),
        (
            lambda path: path["compute_migration"].update({"slurm_scheduling_pause": "true"}),
            r"upgrade_path\.compute_migration\.slurm_scheduling_pause must be true or false",
        ),
        (
            lambda path: path["migration_profile"].pop("execution_contract"),
            r"upgrade_path\.migration_profile\.execution_contract must be a non-empty mapping",
        ),
        (
            lambda path: path.update({"rollout": {"strategy": "zero-surge"}}),
            r"upgrade_path has unsupported field\(s\): rollout",
        ),
    ],
)
def test_runtime_validation_rejects_soperator_onboarding_malformed_upgrade_path(
    mutation: Callable[[dict[str, object]], object],
    match: str,
) -> None:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    upgrade_path = _locked_upgrade_path_payload()
    mutation(upgrade_path)
    onboarding["upgrade_path"] = upgrade_path  # type: ignore[index]

    with pytest.raises(ValueError, match=match):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_soperator_campaign_stale_fingerprint() -> None:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    upgrade_path = _locked_upgrade_path_payload()
    upgrade_path["catalog_fingerprint"] = "b" * 64
    onboarding["upgrade_path"] = upgrade_path  # type: ignore[index]

    with pytest.raises(
        ValueError,
        match=r"upgrade_path\.fingerprint does not match the immutable campaign payload",
    ):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_soperator_campaign_id_not_derived_from_fingerprint() -> None:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    upgrade_path = _locked_upgrade_path_payload()
    upgrade_path["campaign_id"] = "campaign-ffffffffffffffff"
    onboarding["upgrade_path"] = upgrade_path  # type: ignore[index]

    with pytest.raises(
        ValueError,
        match=r"upgrade_path\.campaign_id must be .* for the campaign fingerprint",
    ):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_soperator_campaign_identity_mismatch() -> None:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    upgrade_path = _locked_upgrade_path_payload()
    upgrade_path["identity"]["cluster_id"] = "another-cluster"  # type: ignore[index]
    onboarding["upgrade_path"] = upgrade_path  # type: ignore[index]

    with pytest.raises(
        ValueError,
        match=r"upgrade_path\.identity\.cluster_id must match the owning config value",
    ):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_duplicate_soperator_campaign_segment_id() -> None:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    upgrade_path = _locked_upgrade_path_payload()
    first_segment = upgrade_path["segments"][0]  # type: ignore[index]
    duplicate_segment = json.loads(json.dumps(first_segment))
    duplicate_segment["index"] = 2
    duplicate_segment["depends_on"] = [first_segment["id"]]  # type: ignore[index]
    upgrade_path["segments"].append(duplicate_segment)  # type: ignore[union-attr]
    onboarding["upgrade_path"] = upgrade_path  # type: ignore[index]

    with pytest.raises(
        ValueError,
        match=r"upgrade_path\.segments\[1\]\.id .* is duplicated",
    ):
        validate_runtime_payload(payload)


def _runtime_payload_with_refinalized_campaign(
    upgrade_path: dict[str, object],
) -> dict[str, object]:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["upgrade_path"] = finalize_soperator_upgrade_campaign(  # type: ignore[index]
        upgrade_path,
        created_at="2026-07-11T12:00:00+00:00",
    )
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )
    return payload


def test_runtime_validation_rejects_refingerprinted_campaign_that_omits_soperator_work() -> None:
    upgrade_path = _locked_upgrade_path_payload()
    segment = upgrade_path["segments"][0]  # type: ignore[index]
    for record_name in ("soperator_app", "soperator_chart"):
        record = segment[record_name]
        record["current_version"] = record["target_version"]
        record["upgrade_required"] = False
    segment["soperator_upgrade_required"] = False
    segment["actions"] = [
        action for action in segment["actions"] if action != ONBOARDING_ACTION_UPGRADE_SOPERATOR
    ]
    payload = _runtime_payload_with_refinalized_campaign(upgrade_path)

    with pytest.raises(
        ValueError,
        match=r"soperator_app.current_version must match the previous segment target",
    ):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_refingerprinted_soperator_segment_flag_mismatch() -> None:
    upgrade_path = _locked_upgrade_path_payload()
    upgrade_path["segments"][0]["soperator_upgrade_required"] = False  # type: ignore[index]
    payload = _runtime_payload_with_refinalized_campaign(upgrade_path)

    with pytest.raises(
        ValueError,
        match=r"soperator_upgrade_required must be true for its app/chart version transitions",
    ):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_refingerprinted_version_record_flag_mismatch() -> None:
    upgrade_path = _locked_upgrade_path_payload()
    upgrade_path["soperator_chart"]["upgrade_required"] = False  # type: ignore[index]
    payload = _runtime_payload_with_refinalized_campaign(upgrade_path)

    with pytest.raises(
        ValueError,
        match=r"soperator_chart.upgrade_required must be true because current_version differs",
    ):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_refingerprinted_campaign_that_omits_jail_refresh() -> None:
    upgrade_path = _locked_upgrade_path_payload()
    campaign_jail = upgrade_path["jail_rootfs"]  # type: ignore[index]
    campaign_jail["current_image"] = "cr.example/populate_jail:1.23.3-cuda12.8.0"
    campaign_jail["current_version"] = "1.23.3-cuda12.8.0"
    campaign_jail["refresh_required"] = True
    segment_jail = upgrade_path["segments"][0]["jail_rootfs"]  # type: ignore[index]
    segment_jail["current_image"] = campaign_jail["current_image"]
    segment_jail["current_version"] = campaign_jail["current_version"]
    segment_jail["refresh_required"] = False
    payload = _runtime_payload_with_refinalized_campaign(upgrade_path)

    with pytest.raises(
        ValueError,
        match=r"segments must contain exactly one Jail rootfs refresh",
    ):
        validate_runtime_payload(payload)


def _two_hop_soperator_campaign() -> dict[str, object]:
    upgrade_path = _locked_upgrade_path_payload()
    first_segment = upgrade_path["segments"][0]  # type: ignore[index]
    second_segment = json.loads(json.dumps(first_segment))
    second_segment.update(
        {
            "id": "segment-2-kubernetes-1-33-1-34",
            "index": 2,
            "depends_on": [first_segment["id"]],  # type: ignore[index]
            "title": "Kubernetes 1.33 -> 1.34",
            "current_k8s_version": "1.33",
            "target_k8s_version": "1.34",
            "soperator_upgrade_required": False,
        }
    )
    for record_name in ("soperator_app", "soperator_chart"):
        record = second_segment[record_name]
        record["current_version"] = record["target_version"]
        record["upgrade_required"] = False
    second_segment["actions"] = [
        action
        for action in second_segment["actions"]
        if action != ONBOARDING_ACTION_UPGRADE_SOPERATOR
    ]
    second_segment["mk8s"]["control_plane"] = {
        "source_version": "1.33",
        "target_version": "1.34",
    }
    second_group = second_segment["mk8s"]["node_groups"][0]
    second_group["source"] = json.loads(
        json.dumps(first_segment["mk8s"]["node_groups"][0]["target"])  # type: ignore[index]
    )
    second_group["target"] = {
        "kubernetes_version": "1.34",
        "os": "ubuntu24.04",
        "drivers_preset": "",
    }
    upgrade_path["segments"].append(second_segment)  # type: ignore[union-attr]
    upgrade_path["target_k8s_version"] = "1.34"
    upgrade_path["final_targets"]["kubernetes"] = "1.34"  # type: ignore[index]
    upgrade_path["mk8s"]["control_plane"]["target_version"] = "1.34"  # type: ignore[index]
    upgrade_path["mk8s"]["node_groups"][0]["target"] = json.loads(  # type: ignore[index]
        json.dumps(second_group["target"])
    )
    return finalize_soperator_upgrade_campaign(
        upgrade_path,
        created_at="2026-07-11T12:00:00+00:00",
    )


def test_runtime_validation_accepts_contiguous_two_hop_soperator_campaign() -> None:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["upgrade_path"] = _two_hop_soperator_campaign()  # type: ignore[index]
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )

    validate_runtime_payload(payload)


def test_runtime_validation_rejects_discontinuous_node_group_tuple_between_hops() -> None:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    upgrade_path = _two_hop_soperator_campaign()
    second_group = upgrade_path["segments"][1]["mk8s"]["node_groups"][0]  # type: ignore[index]
    second_group["source"]["os"] = "ubuntu22.04"
    onboarding["upgrade_path"] = upgrade_path  # type: ignore[index]

    with pytest.raises(
        ValueError,
        match=r"node group 'nodegroup-123' source tuple must match its previous segment",
    ):
        validate_runtime_payload(payload)


def test_runtime_validation_rejects_refingerprinted_discontinuous_soperator_chart_hops() -> None:
    upgrade_path = _two_hop_soperator_campaign()
    second_segment = upgrade_path["segments"][1]  # type: ignore[index]
    second_chart = second_segment["soperator_chart"]
    second_chart["current_version"] = "4.0.1-ps.1"
    second_chart["upgrade_required"] = True
    second_segment["soperator_upgrade_required"] = True
    payload = _runtime_payload_with_refinalized_campaign(upgrade_path)

    with pytest.raises(
        ValueError,
        match=r"soperator_chart.current_version must match the previous segment target",
    ):
        validate_runtime_payload(payload)


def test_runtime_validation_accepts_complete_empty_soperator_campaign() -> None:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    upgrade_path = _locked_upgrade_path_payload()
    upgrade_path["source_k8s_version"] = "1.32"
    upgrade_path["target_k8s_version"] = "1.32"
    upgrade_path["final_targets"]["kubernetes"] = "1.32"  # type: ignore[index]
    upgrade_path["segments"] = []
    upgrade_path["mk8s"]["control_plane"]["target_version"] = "1.32"  # type: ignore[index]
    upgrade_path["mk8s"]["node_groups"][0]["target"] = json.loads(  # type: ignore[index]
        json.dumps(upgrade_path["mk8s"]["node_groups"][0]["source"])  # type: ignore[index]
    )
    for record_name in ("soperator_app", "soperator_chart"):
        record = upgrade_path[record_name]  # type: ignore[index]
        record["current_version"] = record["target_version"]  # type: ignore[index]
        record["upgrade_required"] = False  # type: ignore[index]
    onboarding["upgrade_path"] = finalize_soperator_upgrade_campaign(  # type: ignore[index]
        upgrade_path,
        created_at="2026-07-11T12:00:00+00:00",
    )
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )

    validate_runtime_payload(payload)


def test_runtime_validation_accepts_external_scheduling_pause_override() -> None:
    payload = _onboarding_campaign_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["compute_migration"]["slurm_scheduling_pause"] = False  # type: ignore[index]
    upgrade_path = _locked_upgrade_path_payload()
    upgrade_path["compute_migration"]["slurm_scheduling_pause"] = False  # type: ignore[index]
    onboarding["upgrade_path"] = finalize_soperator_upgrade_campaign(  # type: ignore[index]
        upgrade_path,
        created_at="2026-07-11T12:00:00+00:00",
    )
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )

    validate_runtime_payload(payload)


def test_runtime_validation_rejects_legacy_external_rollout_config() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["node_template_upgrade"] = {  # type: ignore[index]
        "rollout": {"strategy": "zero-surge"},
    }

    with pytest.raises(
        ValueError,
        match=r"node_template_upgrade has unsupported field\(s\): rollout",
    ):
        validate_runtime_payload(payload)


def test_onboarding_report_writer_persists_target_report(tmp_path) -> None:
    payload = _onboarding_payload()

    written = write_soperator_onboarding_reports(payload, tmp_path / "generated")

    assert len(written) == 1
    report_path = written[0]
    assert report_path == (
        tmp_path
        / "generated"
        / "reports"
        / "soperator-clusters"
        / "nebius-cluster1-mk8scluster-123-external"
        / "onboarding"
        / "report.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["target_ref"] == "cluster1"
    assert report["onboard_description"] == (
        soperator_onboarding_module.EXT_SOPERATOR_ONBOARD_DESCRIPTION
    )
    assert report["accepted_fingerprint"] == soperator_onboarding_fingerprint(
        payload,
        target_ref="cluster1",
    )


def test_source_discovery_report_writer_persists_full_snapshot(tmp_path) -> None:
    snapshot = _with_provider_node_template_inventory(_snapshot())
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
        target_versions={
            "k8s_version": (
                soperator_onboarding_module.ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION
            )
        },
        guidance_lines=("- Soperator upgrade path: status=supported, rule=test-rule",),
    )

    assert path == (
        tmp_path
        / "generated"
        / "reports"
        / "soperator-clusters"
        / "mk8scluster-123"
        / "discovery"
        / "manifest.json"
    )
    payload = load_soperator_discovery_bundle(path)
    assert payload["cluster_id"] == "mk8scluster-123"
    assert (
        payload["current_k8s_version"]
        == soperator_onboarding_module.ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION
    )
    assert (
        payload["target_k8s_version"]
        == soperator_onboarding_module.ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION
    )
    assert payload["report"]["state"] == "no-soperator-detected"
    assert payload["snapshot"]["node_groups"]["h100"]["gpu"] is True
    summary = (path.parent / "summary.md").read_text(encoding="utf-8")
    assert "- Current Kubernetes version:" in summary
    assert "- Target Kubernetes version:" in summary
    assert "## Upgrade Guidance" in summary
    assert "- Soperator upgrade path: status=supported, rule=test-rule" in summary


def test_source_discovery_report_writer_treats_output_dir_as_root(tmp_path) -> None:
    output_root = tmp_path / "custom-root"
    jail_image = "cr.eu-north1.nebius.cloud/soperator/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0"
    snapshot = _snapshot(
        release={
            "name": "soperator",
            "namespace": "soperator",
            "chart": "soperator-4.0.2-ps.3",
            "app_version": "4.0.2",
        }
    )
    snapshot["soperator_resources"] = [
        {
            "kind": "NodeSet",
            "metadata": {
                "name": "worker",
                "namespace": "soperator",
                "labels": {
                    "app.kubernetes.io/instance": "soperator",
                    "app.kubernetes.io/name": "soperator",
                    "app.kubernetes.io/version": "4.0.2",
                    "helm.sh/chart": "soperator-4.0.2-ps.3",
                },
            },
        }
    ]
    snapshot["soperator_resources"] += [
        {
            "kind": "SlurmCluster",
            "metadata": {"name": "mk8s", "namespace": "soperator"},
            "spec": {"populateJail": {"image": jail_image}},
        }
    ]
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.3",
        pinned_app_version="4.0.2",
    )

    path = write_source_soperator_discovery_report(
        tmp_path,
        target_ref="cluster1",
        snapshot=snapshot,
        report=report,
        output_dir=output_root,
        target_versions={
            "jail_rootfs": {
                "target_image": jail_image,
                "target_source": "test-target",
            }
        },
    )

    assert path == (
        output_root
        / "generated"
        / "reports"
        / "soperator-clusters"
        / "cluster1"
        / "discovery"
        / "manifest.json"
    )
    assert not (output_root / "manifest.json").exists()
    summary = (path.parent / "summary.md").read_text(encoding="utf-8")
    assert "- Soperator status: `installed`" in summary
    assert "- Source version: `4.0.2`" in summary
    assert "- Soperator chart version: `4.0.2-ps.3`" in summary
    assert "- Jail rootfs version: `4.0.2-slurm25.11.3-cuda12.9.0`" in summary
    payload = load_soperator_discovery_bundle(path)
    assert payload["jail_rootfs"]["current_image"] == jail_image
    assert payload["jail_rootfs"]["current_version"] == "4.0.2-slurm25.11.3-cuda12.9.0"
    assert payload["jail_rootfs"]["target_image"] == jail_image
    assert payload["jail_rootfs"]["refresh_required"] is True
    assert "evidence is incomplete" in payload["jail_rootfs"]["reason"]
    assert payload["report"]["jail_rootfs"]["refresh_required"] is True
    kubernetes = json.loads((path.parent / "kubernetes.json").read_text(encoding="utf-8"))
    assert [item["metadata"]["name"] for item in kubernetes["nodesets"]] == ["worker"]


def test_source_discovery_report_writer_marks_jail_refresh_when_target_image_changes(
    tmp_path,
) -> None:
    current_image = (
        "cr.eu-north1.nebius.cloud/soperator/populate_jail:4.0.2-slurm25.11.3-cuda12.8.0"
    )
    target_image = "cr.eu-north1.nebius.cloud/soperator/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0"
    snapshot = _snapshot(
        release={
            "name": "soperator",
            "namespace": "soperator",
            "chart": "soperator-4.0.2-ps.3",
            "app_version": "4.0.2",
        }
    )
    snapshot["soperator_resources"] = [
        {
            "kind": "SlurmCluster",
            "metadata": {"name": "mk8s", "namespace": "soperator"},
            "spec": {"populateJail": {"image": current_image}},
        }
    ]
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.3",
        pinned_app_version="4.0.2",
    )

    path = write_source_soperator_discovery_report(
        tmp_path,
        target_ref="cluster1",
        snapshot=snapshot,
        report=report,
        target_versions={
            "jail_rootfs": {
                "target_image": target_image,
                "target_source": "test-target",
            }
        },
    )

    payload = load_soperator_discovery_bundle(path)
    jail_rootfs = payload["jail_rootfs"]
    assert jail_rootfs["current_image"] == current_image
    assert jail_rootfs["current_version"] == "4.0.2-slurm25.11.3-cuda12.8.0"
    assert jail_rootfs["target_image"] == target_image
    assert jail_rootfs["target_version"] == "4.0.2-slurm25.11.3-cuda12.9.0"
    assert jail_rootfs["refresh_required"] is True
    assert "differs" in jail_rootfs["reason"]
    summary = (path.parent / "summary.md").read_text(encoding="utf-8")
    assert "- Target Jail rootfs version: `4.0.2-slurm25.11.3-cuda12.9.0`" in summary


def test_source_discovery_round_trip_preserves_sshd_secret_reference(
    tmp_path,
) -> None:
    snapshot = _snapshot(
        release={
            "name": "soperator",
            "namespace": "soperator",
            "chart": "soperator-4.0.2-ps.3",
            "app_version": "4.0.2",
        }
    )
    snapshot["soperator_resources"] = [
        {
            "kind": "SlurmCluster",
            "metadata": {
                "name": "legacy-source",
                "namespace": "soperator",
                "uid": "source-uid",
            },
            "spec": {
                "secrets": {"sshdKeysName": "customer-host-identity"},
            },
        }
    ]
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="target-cluster",
        pinned_chart_version="4.0.2-ps.3",
        pinned_app_version="4.0.2",
    )

    path = write_source_soperator_discovery_report(
        tmp_path,
        target_ref="target-cluster",
        snapshot=snapshot,
        report=report,
    )
    loaded = load_soperator_discovery_bundle(path)
    persisted_snapshot = loaded["snapshot"]

    assert persisted_snapshot["soperator_resources"][0]["spec"]["secrets"] == "[redacted]"
    assert loaded["source_slurmcluster_ref"] == {
        "status": "resolved",
        "namespace": "soperator",
        "name": "legacy-source",
        "uid": "source-uid",
        "sshd_host_key_secret_name": "customer-host-identity",
    }
    assert (
        soperator_migration_module._source_sshd_host_key_secret_name(  # noqa: SLF001
            loaded,
            target_ref="target-cluster",
        )
        == "customer-host-identity"
    )


def _active_passive_jail_snapshot(*, active_slot: str) -> dict[str, object]:
    old_image = "cr.eu-north1.nebius.cloud/soperator/populate_jail:4.0.2-slurm25.11.3-cuda12.8.0"
    target_image = "cr.eu-north1.nebius.cloud/soperator/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0"
    snapshot = _snapshot(
        release={
            "name": "soperator",
            "namespace": "soperator",
            "chart": "soperator-4.0.2-ps.3",
            "app_version": "4.0.2",
        }
    )
    active_pvc = f"jail-rootfs-{active_slot}-pvc"
    active_source = f"jail-rootfs-{active_slot}"
    snapshot["soperator_resources"] = [
        {
            "kind": "SlurmCluster",
            "metadata": {"name": "mk8s", "namespace": "soperator", "uid": "slurm-uid"},
            "spec": {
                "populateJail": {"image": target_image},
                "volumeSources": [
                    {
                        "name": "jail",
                        "persistentVolumeClaim": {"claimName": active_pvc},
                    },
                    {
                        "name": active_source,
                        "persistentVolumeClaim": {"claimName": active_pvc},
                    },
                ],
                "slurmNodes": {
                    "controller": {"volumes": {"jail": {"volumeSourceName": active_source}}},
                    "login": {"volumes": {"jail": {"volumeSourceName": active_source}}},
                },
            },
        },
        {
            "kind": "NodeSet",
            "metadata": {"name": "worker", "namespace": "soperator"},
            "spec": {
                "slurmd": {
                    "volumes": {"jail": {"persistentVolumeClaim": {"claimName": active_pvc}}}
                }
            },
        },
    ]
    snapshot["soperator_namespace_resources"] = [
        {
            "kind": "Job",
            "metadata": {
                "name": "mk8s-populate-jail-passive-slot-a",
                "namespace": "soperator",
                "uid": "populate-slot-a-uid",
                "creationTimestamp": "2026-07-04T10:00:00Z",
                "labels": {
                    "slurm.nebius.ai/jail-rootfs-refresh": "active-passive",
                    "slurm.nebius.ai/jail-rootfs-slot": "slot-a",
                },
            },
            "status": {"succeeded": 1, "completionTime": "2026-07-04T10:03:00Z"},
            "containers": [{"name": "populate-jail", "image": old_image}],
            "pvc_claim_names": ["jail-rootfs-slot-a-pvc"],
        },
        {
            "kind": "Job",
            "metadata": {
                "name": "mk8s-populate-jail-passive-slot-b",
                "namespace": "soperator",
                "uid": "populate-slot-b-uid",
                "creationTimestamp": "2026-07-05T10:00:00Z",
                "labels": {
                    "slurm.nebius.ai/jail-rootfs-refresh": "active-passive",
                    "slurm.nebius.ai/jail-rootfs-slot": "slot-b",
                },
            },
            "status": {"succeeded": 1, "completionTime": "2026-07-05T10:03:00Z"},
            "containers": [{"name": "populate-jail", "image": target_image}],
            "pvc_claim_names": ["jail-rootfs-slot-b-pvc"],
        },
    ]
    snapshot["pvcs"] = [
        {
            "metadata": {
                "name": f"jail-rootfs-{slot}-pvc",
                "namespace": "soperator",
                "uid": f"jail-rootfs-{slot}-pvc-uid",
            },
            "status": {"phase": "Bound"},
        }
        for slot in ("slot-a", "slot-b")
    ]
    snapshot["cluster_identity"] = {
        "kubernetes_uid": "kubernetes-uid",
        "soperator_uid": "soperator-uid",
        "slurmcluster_uid": "slurm-uid",
        "jail_filesystem_id": "jail-filesystem-id",
    }
    return snapshot


def test_completed_passive_populate_job_is_not_current_before_consumer_switch() -> None:
    target_image = "cr.eu-north1.nebius.cloud/soperator/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0"

    jail_rootfs = soperator_discovery_jail_rootfs_record(
        snapshot=_active_passive_jail_snapshot(active_slot="slot-a"),
        report={"actions": []},
        target_versions={
            "jail_rootfs": {
                "target_image": target_image,
                "target_source": "test-target",
            }
        },
    )

    assert jail_rootfs["current_image"].endswith("cuda12.8.0")
    assert jail_rootfs["current_job_name"] == "mk8s-populate-jail-passive-slot-a"
    assert jail_rootfs["current_slot"] == "slot-a"
    assert jail_rootfs["current_pvc_name"] == "jail-rootfs-slot-a-pvc"
    assert jail_rootfs["current_evidence_status"] == "active-slot-verified"
    assert jail_rootfs["refresh_required"] is True


def test_active_jail_binding_follows_exact_resume_identity_across_dual_cr_handoff() -> None:
    def slurmcluster(*, name: str, uid: str, pvc_name: str) -> dict[str, object]:
        return {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": name, "namespace": "soperator", "uid": uid},
            "spec": {
                "volumeSources": [
                    {
                        "name": "jail",
                        "persistentVolumeClaim": {"claimName": pvc_name},
                    }
                ],
                "slurmNodes": {"login": {"volumes": {"jail": {"volumeSourceName": "jail"}}}},
            },
        }

    source = slurmcluster(name="source-cluster", uid="source-uid", pvc_name="source-jail-pvc")
    target = slurmcluster(name="target-cluster", uid="target-uid", pvc_name="target-jail-pvc")
    source_ref = {"namespace": "soperator", "name": "source-cluster", "uid": "source-uid"}
    target_ref = {"namespace": "soperator", "name": "target-cluster", "uid": "target-uid"}
    snapshot = {
        "soperator_resources": [source, target],
        "identity_soperator_resources": [source],
        "resume_slurmcluster_identity": {
            "identity_role": "source",
            "source": source_ref,
            "target": target_ref,
        },
        "pvcs": [
            {
                "metadata": {
                    "name": "source-jail-pvc",
                    "namespace": "soperator",
                    "uid": "source-jail-pvc-uid",
                },
                "status": {"phase": "Bound"},
            },
            {
                "metadata": {
                    "name": "target-jail-pvc",
                    "namespace": "soperator",
                    "uid": "target-jail-pvc-uid",
                },
                "status": {"phase": "Bound"},
            },
        ],
        "cluster_identity": {"jail_filesystem_id": "source-jail-filesystem-id"},
    }

    source_binding = soperator_discovery_module._active_jail_pvc_binding(snapshot)  # noqa: SLF001
    assert source_binding["status"] == "bound"
    assert source_binding["slurmcluster_name"] == "source-cluster"
    assert source_binding["pvc_name"] == "source-jail-pvc"
    assert source_binding["pvc_uid"] == "source-jail-pvc-uid"

    snapshot["identity_soperator_resources"] = [target]
    snapshot["resume_slurmcluster_identity"] = {
        "identity_role": "target",
        "source": source_ref,
        "target": target_ref,
    }
    snapshot["cluster_identity"] = {"jail_filesystem_id": "target-jail-filesystem-id"}
    target_binding = soperator_discovery_module._active_jail_pvc_binding(snapshot)  # noqa: SLF001
    assert target_binding["status"] == "bound"
    assert target_binding["slurmcluster_name"] == "target-cluster"
    assert target_binding["pvc_name"] == "target-jail-pvc"
    assert target_binding["pvc_uid"] == "target-jail-pvc-uid"


def test_failed_active_slot_populate_job_is_not_current_evidence() -> None:
    target_image = "cr.eu-north1.nebius.cloud/soperator/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0"
    snapshot = _active_passive_jail_snapshot(active_slot="slot-b")
    active_job = snapshot["soperator_namespace_resources"][1]  # type: ignore[index]
    active_job["status"]["failed"] = 1  # type: ignore[index]

    jail_rootfs = soperator_discovery_jail_rootfs_record(
        snapshot=snapshot,
        report={"actions": []},
        target_versions={
            "jail_rootfs": {
                "target_image": target_image,
                "target_source": "test-target",
            }
        },
    )

    assert jail_rootfs["current_source"] == "slurmcluster.spec.populateJail.image"
    assert jail_rootfs["current_evidence_status"] == "unverified"
    assert jail_rootfs["refresh_required"] is True


def test_source_discovery_report_writer_accepts_completed_job_after_active_consumer_switch(
    tmp_path,
) -> None:
    completed_job_image = (
        "cr.eu-north1.nebius.cloud/soperator/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0"
    )
    snapshot = _active_passive_jail_snapshot(active_slot="slot-b")
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="4.0.2-ps.3",
        pinned_app_version="4.0.2",
    )

    path = write_source_soperator_discovery_report(
        tmp_path,
        target_ref="cluster1",
        snapshot=snapshot,
        report=report,
        target_versions={
            "jail_rootfs": {
                "target_image": completed_job_image,
                "target_source": "test-target",
            }
        },
    )

    payload = load_soperator_discovery_bundle(path)
    jail_rootfs = payload["jail_rootfs"]
    assert jail_rootfs["current_image"] == completed_job_image
    assert jail_rootfs["current_source"] == "completed-populate-jail-job"
    assert jail_rootfs["current_job_name"] == "mk8s-populate-jail-passive-slot-b"
    assert jail_rootfs["current_job_uid"] == "populate-slot-b-uid"
    assert jail_rootfs["current_slot"] == "slot-b"
    assert jail_rootfs["current_pvc_name"] == "jail-rootfs-slot-b-pvc"
    assert jail_rootfs["current_pvc_uid"] == "jail-rootfs-slot-b-pvc-uid"
    assert jail_rootfs["current_jail_filesystem_id"] == "jail-filesystem-id"
    assert jail_rootfs["current_evidence_status"] == "active-slot-verified"
    assert jail_rootfs["live_desired_image"] == completed_job_image
    assert jail_rootfs["target_image"] == completed_job_image
    assert jail_rootfs["refresh_required"] is False


def test_jail_rootfs_matching_desired_image_without_completed_job_requires_refresh() -> None:
    image = "cr.example/populate_jail:4.0.2-slurm25.11.3-cuda12.9.0"
    snapshot = {
        "soperator_resources": [
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "mk8s", "namespace": "soperator"},
                "spec": {"populateJail": {"image": image}},
            }
        ],
        "soperator_namespace_resources": [],
    }

    jail_rootfs = soperator_discovery_jail_rootfs_record(
        snapshot=snapshot,
        report={"actions": []},
        target_versions={
            "jail_rootfs": {
                "target_image": image,
                "target_source": "pinned-chart-defaults",
                "target_cuda_version": "12.9.0",
            }
        },
    )

    assert jail_rootfs["current_source"] == "slurmcluster.spec.populateJail.image"
    assert jail_rootfs["current_image"] == image
    assert jail_rootfs["refresh_required"] is True
    assert "does not prove a completed active-slot population" in jail_rootfs["reason"]


def test_source_discovery_report_writer_filters_resource_labels_by_namespace(tmp_path) -> None:
    snapshot = _snapshot()
    snapshot["helm_releases"] = []
    snapshot["crds"] = ["slurmclusters.slurm.nebius.ai", "nodesets.slurm.nebius.ai"]
    snapshot["soperator_resources"] = [
        {
            "kind": "NodeSet",
            "metadata": {
                "name": "worker",
                "namespace": "soperator",
                "labels": {
                    "app.kubernetes.io/instance": "soperator",
                    "app.kubernetes.io/name": "soperator",
                    "app.kubernetes.io/version": "1.23.3",
                    "helm.sh/chart": "soperator-1.23.3",
                },
            },
        },
        {
            "kind": "NodeSet",
            "metadata": {
                "name": "custom-worker",
                "namespace": "custom",
                "labels": {
                    "app.kubernetes.io/instance": "soperator",
                    "app.kubernetes.io/name": "soperator",
                    "app.kubernetes.io/version": "3.0.7",
                    "helm.sh/chart": "soperator-3.0.7",
                },
            },
        },
    ]

    path = write_source_soperator_discovery_report(
        tmp_path,
        target_ref="cluster1",
        snapshot=snapshot,
        report={
            "state": "existing-soperator-unknown",
            "source_version": "",
            "target_version": "4.0.2-ps.3",
            "fingerprint": "fingerprint",
            "findings": [],
            "actions": [],
        },
        namespace="custom",
        release_name="soperator",
    )

    payload = load_soperator_discovery_bundle(path)
    assert payload["namespace"] == "custom"
    assert payload["source_version"] == "3.0.7"
    assert payload["chart_version"] == "3.0.7"


def test_source_discovery_report_writer_omits_malformed_k8s_versions(tmp_path) -> None:
    snapshot = _snapshot()
    snapshot["provider"] = {
        "mk8s_cluster": {
            "id": "mk8scluster-123",
            "name": "cluster1",
            "control_plane_version": "version pending",
        }
    }
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
        target_versions={"k8s_version": "not-a-version"},
    )

    payload = load_soperator_discovery_bundle(path)
    assert payload["current_k8s_version"] == ""
    assert payload["target_k8s_version"] == ""
    summary = (path.parent / "summary.md").read_text(encoding="utf-8")
    assert "- Current Kubernetes version:" not in summary
    assert "- Target Kubernetes version:" not in summary


def test_soperator_discovery_k8s_minor_text_rejects_freeform_status_text() -> None:
    assert soperator_discovery_k8s_minor_text("v1.32.7") == "1.32"
    assert soperator_discovery_k8s_minor_text("1.33+provider.1") == "1.33"
    assert soperator_discovery_k8s_minor_text("version 1.32 pending") == ""


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
