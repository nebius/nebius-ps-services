from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import types
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.quota_checks import QuotaCheck, QuotaReport, QuotaRequirement
from nebius_cxcli.soperator_jail_capacity import GIB, evaluate_jail_capacity
from nebius_cxcli.soperator_migration import (
    SoperatorMigrationCommandResult,
    soperator_migration_checkpoint_path,
)
from nebius_cxcli.soperator_migration import (
    execute_soperator_migration as _execute_soperator_migration,
)
from nebius_cxcli.soperator_onboarding import (
    SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA,
    analyze_soperator_onboarding_snapshot,
    soperator_onboarding_fingerprint,
)


def _snapshot(version: str = "3.0.5") -> dict[str, Any]:
    return {
        "node_groups": {
            "gpu-pool": {
                "gpu": True,
                "node_count": 2,
                "labels": {
                    "nebius.com/node-group": "gpu-pool",
                    "nebius.com/node-group-id": "nodegroup-gpu-pool",
                },
                "allocatable": {"nvidia.com/gpu": "8"},
                "nodes": ["node-a", "node-b"],
            }
        },
        "helm_releases": [
            {
                "name": "soperator",
                "namespace": "soperator",
                "chart": f"helm-soperator-{version}",
                "app_version": version,
            }
        ],
        "crds": [],
        "namespaces": ["soperator"],
        "pvs": [],
        "pvcs": [],
        "collection_errors": [],
    }


@pytest.mark.parametrize(
    ("phase_id", "expected_stage"),
    [
        ("external-node-template-upgrade", "MK8s Node Upgrades"),
        ("target-gpu-stack-remediation", "MK8s Node Upgrades"),
        ("post-upgrade-mk8s-check", "MK8s Node Upgrades"),
        ("discovery-and-plan", "Soperator Upgrade"),
        ("customer-approval", "Soperator Upgrade"),
        ("create-aligned-sfs", "Soperator Upgrade"),
        ("online-bulk-data-sync", "Soperator Upgrade"),
        ("rolling-compute-migration", "Soperator Upgrade"),
        ("final-control-plane-cutover", "Soperator Upgrade"),
        ("populate-jail-refresh", "Jail Upgrade"),
        ("validation-and-rollback-hold", "Soperator Upgrade"),
        ("retire-old-resources", "Soperator Upgrade"),
        ("post-upgrade-helm-check", "Soperator Upgrade"),
        ("backup", "Soperator Upgrade"),
    ],
)
def test_external_soperator_upgrade_top_level_stage_groups_known_phases(
    phase_id: str,
    expected_stage: str,
) -> None:
    assert migration.external_soperator_upgrade_top_level_stage(phase_id) == expected_stage


def test_external_upgrade_status_summary_distinguishes_operator_stop() -> None:
    assert (
        migration._external_upgrade_status_summary(  # noqa: SLF001
            pending_phase="external-node-template-upgrade",
            pending_reason=(
                "External Soperator upgrade stopped by operator while affected Slurm "
                "jobs are still present."
            ),
            mutation_performed=False,
        )
        == "stopped by operator before upgrade mutation; rerun the same command "
        "after choosing how to handle affected Slurm jobs."
    )
    assert (
        migration._external_upgrade_status_summary(  # noqa: SLF001
            pending_phase="rolling-compute-migration",
            pending_reason="External Soperator upgrade stopped by operator.",
            mutation_performed=True,
        )
        == "stopped by operator after upgrade mutation; rerun the same command "
        "after choosing how to handle affected Slurm jobs."
    )


@pytest.mark.parametrize(
    "stderr",
    [
        "not found",
        "resource not found",
        "Request error NOT_FOUND: node group missing",
        "rpc error: code = NotFound desc = node group missing",
        'Error from server (NotFound): helmreleases.helm.toolkit.fluxcd.io "missing" not found',
    ],
)
def test_command_not_found_recognizes_explicit_not_found_status(stderr: str) -> None:
    result = SoperatorMigrationCommandResult(
        ("nebius", "mk8s", "node-group", "delete"), 1, "", stderr
    )

    assert migration._command_not_found(result) is True


def test_default_command_runner_retries_transient_kubectl_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def _run(
        args: list[str],
        *,
        input: str | None,
        text: bool,
        capture_output: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        del input, text, capture_output, timeout, check
        calls.append(args)
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                args,
                1,
                "",
                (
                    "Unable to connect to the server: getting credentials: "
                    "authentication handshake failed: Client.Timeout exceeded "
                    "while awaiting headers"
                ),
            )
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(migration.subprocess, "run", _run)
    monkeypatch.setattr(migration.time, "sleep", lambda _seconds: None)

    result = migration._default_command_runner(  # noqa: SLF001
        ["kubectl", "get", "pods"],
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "ok"
    assert len(calls) == 2


def test_default_command_runner_retries_read_timeout_for_read_only_kubectl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def _run(
        args: list[str],
        *,
        input: str | None,
        text: bool,
        capture_output: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        del input, text, capture_output, timeout, check
        calls.append(args)
        if len(calls) == 1:
            return subprocess.CompletedProcess(args, 1, "", "read: operation timed out")
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(migration.subprocess, "run", _run)
    monkeypatch.setattr(migration.time, "sleep", lambda _seconds: None)

    result = migration._default_command_runner(  # noqa: SLF001
        ["kubectl", "--context", "external-context", "get", "pods"],
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "ok"
    assert len(calls) == 2


def test_default_command_runner_does_not_retry_read_timeout_for_kubectl_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def _run(
        args: list[str],
        *,
        input: str | None,
        text: bool,
        capture_output: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        del input, text, capture_output, timeout, check
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "read: operation timed out")

    monkeypatch.setattr(migration.subprocess, "run", _run)
    monkeypatch.setattr(migration.time, "sleep", lambda _seconds: None)

    result = migration._default_command_runner(  # noqa: SLF001
        [
            "kubectl",
            "--context",
            "external-context",
            "exec",
            "login-0",
            "--",
            "bash",
            "-lc",
            "scontrol update nodename=worker-0 state=resume",
        ],
        check=False,
    )

    assert result.returncode == 1
    assert len(calls) == 1


def test_node_group_payload_reads_through_nebius_api() -> None:
    class _Api:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_node_group(self, node_group_id: str) -> Mapping[str, Any]:
            self.calls.append(node_group_id)
            return {"metadata": {"id": node_group_id}}

    api = _Api()
    payload = migration._node_group_payload_by_id(  # noqa: SLF001
        nebius_api=api,  # type: ignore[arg-type]
        node_group_id="node-group-1",
    )

    assert payload["metadata"]["id"] == "node-group-1"
    assert api.calls == ["node-group-1"]


def test_sdk_cluster_control_plane_update_preserves_required_identity_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    current_cluster = {
        "metadata": {
            "id": "mk8scluster-123",
            "parent_id": "project-123",
            "name": "cluster-a",
        },
        "spec": {
            "control_plane": {
                "version": "1.32",
                "subnet_id": "vpcsubnet-123",
            }
        },
        "status": {
            "control_plane": {
                "endpoints": {
                    "private_endpoint": "https://private.example.invalid",
                    "public_endpoint": "https://public.example.invalid",
                }
            }
        },
    }

    class _Message:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class _UpdateClusterRequest(_Message):
        def set_mask(self, mask: object) -> None:
            self.mask = mask

    class _Request:
        def __init__(self, *, id: str) -> None:
            self.id = id

    class _GetOperation:
        def wait(self) -> Mapping[str, Any]:
            return current_cluster

    class _UpdateOperation:
        def wait(self) -> object:
            return object()

    class _ClusterClient:
        def get(self, request: _Request) -> _GetOperation:
            captured["get_request_id"] = request.id
            return _GetOperation()

        def update(self, request: _UpdateClusterRequest) -> _UpdateOperation:
            captured["update_request"] = request
            return _UpdateOperation()

    common_module = types.ModuleType("nebius.api.nebius.common.v1")
    common_module.ResourceMetadata = _Message  # type: ignore[attr-defined]
    mk8s_module = types.ModuleType("nebius.api.nebius.mk8s.v1")
    mk8s_module.ClusterSpec = _Message  # type: ignore[attr-defined]
    mk8s_module.ControlPlaneEndpointsSpec = _Message  # type: ignore[attr-defined]
    mk8s_module.ControlPlaneSpec = _Message  # type: ignore[attr-defined]
    mk8s_module.GetClusterRequest = _Request  # type: ignore[attr-defined]
    mk8s_module.PublicEndpointSpec = _Message  # type: ignore[attr-defined]
    mk8s_module.UpdateClusterRequest = _UpdateClusterRequest  # type: ignore[attr-defined]
    fieldmask_module = types.ModuleType("nebius.base.fieldmask")
    fieldmask_module.Mask = types.SimpleNamespace(unmarshal=lambda value: value)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "nebius.api.nebius.common.v1", common_module)
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.mk8s.v1", mk8s_module)
    monkeypatch.setitem(sys.modules, "nebius.base.fieldmask", fieldmask_module)
    monkeypatch.setattr(migration, "wait_nebius_operation", lambda *args, **kwargs: None)

    api = object.__new__(migration._SdkSoperatorMigrationNebiusApi)  # noqa: SLF001
    api._cluster_client = _ClusterClient()

    result = api.update_cluster_control_plane(
        cluster_id="mk8scluster-123",
        control_plane_version="1.33",
    )

    update_request = captured["update_request"]
    assert result == current_cluster
    assert captured["get_request_id"] == "mk8scluster-123"
    assert update_request.metadata.id == "mk8scluster-123"
    assert update_request.metadata.parent_id == "project-123"
    assert update_request.metadata.name == "cluster-a"
    assert update_request.spec.control_plane.version == "1.33"
    assert update_request.spec.control_plane.subnet_id == "vpcsubnet-123"
    assert isinstance(update_request.spec.control_plane.endpoints.public_endpoint, _Message)
    assert update_request.mask == "spec.control_plane.version"


def test_sdk_node_group_updates_preserve_required_identity_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    current_node_group = {
        "metadata": {
            "id": "nodegroup-123",
            "parent_id": "mk8scluster-123",
            "name": "worker-a",
        },
        "spec": {
            "version": "1.32",
            "fixed_node_count": 2,
            "template": {"os": "ubuntu22.04-driverless"},
        },
    }

    class _Message:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class _UpdateNodeGroupRequest(_Message):
        def set_mask(self, mask: object) -> None:
            self.mask = mask

    class _Request:
        def __init__(self, *, id: str) -> None:
            self.id = id

    class _GetOperation:
        def wait(self) -> Mapping[str, Any]:
            return current_node_group

    class _UpdateOperation:
        def wait(self) -> object:
            return object()

    class _NodeGroupClient:
        def get(self, request: _Request) -> _GetOperation:
            captured.setdefault("get_request_ids", []).append(request.id)
            return _GetOperation()

        def update(self, request: _UpdateNodeGroupRequest) -> _UpdateOperation:
            captured.setdefault("update_requests", []).append(request)
            return _UpdateOperation()

    common_module = types.ModuleType("nebius.api.nebius.common.v1")
    common_module.ResourceMetadata = _Message  # type: ignore[attr-defined]
    mk8s_module = types.ModuleType("nebius.api.nebius.mk8s.v1")
    mk8s_module.GetNodeGroupRequest = _Request  # type: ignore[attr-defined]
    mk8s_module.NodeGroupSpec = _Message  # type: ignore[attr-defined]
    mk8s_module.UpdateNodeGroupRequest = _UpdateNodeGroupRequest  # type: ignore[attr-defined]
    fieldmask_module = types.ModuleType("nebius.base.fieldmask")
    fieldmask_module.Mask = types.SimpleNamespace(unmarshal=lambda value: value)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "nebius.api.nebius.common.v1", common_module)
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.mk8s.v1", mk8s_module)
    monkeypatch.setitem(sys.modules, "nebius.base.fieldmask", fieldmask_module)
    monkeypatch.setattr(migration, "sdk_parse_message", lambda cls, payload: cls(**payload))
    monkeypatch.setattr(migration, "wait_nebius_operation", lambda *args, **kwargs: None)

    api = object.__new__(migration._SdkSoperatorMigrationNebiusApi)  # noqa: SLF001
    api._node_group_client = _NodeGroupClient()

    api.update_node_group(
        node_group_id="nodegroup-123",
        original_node_group=current_node_group,
        update_args=("--version", "1.33"),
        strategy_args=(),
    )
    api.scale_node_group(node_group_id="nodegroup-123", count=3)

    update_request, scale_request = captured["update_requests"]
    assert update_request.metadata.id == "nodegroup-123"
    assert update_request.metadata.parent_id == "mk8scluster-123"
    assert update_request.metadata.name == "worker-a"
    assert update_request.mask == "spec.version"
    assert scale_request.metadata.id == "nodegroup-123"
    assert scale_request.metadata.parent_id == "mk8scluster-123"
    assert scale_request.metadata.name == "worker-a"
    assert scale_request.mask == "spec.fixed_node_count"


def test_node_group_payload_propagates_nebius_api_error() -> None:
    class _Api:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_node_group(self, node_group_id: str) -> Mapping[str, Any]:
            self.calls.append(node_group_id)
            raise RuntimeError("Nebius API node-group get failed: PermissionDenied")

    api = _Api()

    with pytest.raises(RuntimeError, match="PermissionDenied"):
        migration._node_group_payload_by_id(  # noqa: SLF001
            nebius_api=api,  # type: ignore[arg-type]
            node_group_id="node-group-1",
        )

    assert api.calls == ["node-group-1"]


def test_execution_source_contract_ignores_slurmcluster_secrets_redaction() -> None:
    redacted = _snapshot()
    live = _snapshot()
    redacted["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1alpha1",
            "kind": "SlurmCluster",
            "metadata": {"name": "soperator", "namespace": "soperator"},
            "spec": {"secrets": "[redacted]", "clusterName": "soperator"},
        }
    ]
    live["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1alpha1",
            "kind": "SlurmCluster",
            "metadata": {"name": "soperator", "namespace": "soperator"},
            "spec": {"secrets": {}, "clusterName": "soperator"},
        }
    ]

    assert migration._fingerprint(
        migration._execution_source_contract(redacted)
    ) == migration._fingerprint(migration._execution_source_contract(live))


def test_execution_source_contract_ignores_redacted_secret_like_keys() -> None:
    redacted = _snapshot()
    live = _snapshot()
    redacted["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "soperator", "namespace": "soperator"},
            "spec": {
                "slurmNodes": {
                    "accounting": {
                        "mariadbOperator": {
                            "enabled": True,
                            "protectedSecret": "[redacted]",
                        }
                    }
                }
            },
        }
    ]
    live["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "soperator", "namespace": "soperator"},
            "spec": {
                "slurmNodes": {
                    "accounting": {
                        "mariadbOperator": {
                            "enabled": True,
                            "protectedSecret": True,
                        }
                    }
                }
            },
        }
    ]

    assert migration._fingerprint(
        migration._execution_source_contract(redacted)
    ) == migration._fingerprint(migration._execution_source_contract(live))


@pytest.mark.parametrize(
    "stderr",
    [
        "transient dependency not found while deleting node group",
        "permission denied because subject credentials secret was not found",
        "deadline exceeded while waiting for resource not found cleanup",
    ],
)
def test_command_not_found_does_not_mask_contextual_errors(stderr: str) -> None:
    result = SoperatorMigrationCommandResult(
        ("nebius", "mk8s", "node-group", "delete"), 1, "", stderr
    )

    assert migration._command_not_found(result) is False


def _source_worker_nodeset(name: str, *, gpu: bool) -> dict[str, Any]:
    slurmd_resources: dict[str, Any] = {
        "cpu": "127000m" if gpu else "15000m",
        "memory": "1438Gi" if gpu else "55Gi",
        "ephemeralStorage": "387Gi",
    }
    if gpu:
        slurmd_resources["nvidia.com/gpu"] = "8"
    return {
        "apiVersion": "slurm.nebius.ai/v1alpha1",
        "kind": "NodeSet",
        "metadata": {"name": name, "namespace": "soperator"},
        "spec": {
            "replicas": 2,
            "nodeSelector": {"slurm.nebius.ai/nodeset": name},
            "nodeConfig": {
                "dynamic": "",
                "static": "CPUs=127 Gres=gpu:8 Port=6818" if gpu else "CPUs=15 Port=6818",
            },
            "slurmd": {
                "image": {
                    "repository": "old/worker_slurmd",
                    "tag": "1.23.3",
                    "pullPolicy": "IfNotPresent",
                },
                "resources": slurmd_resources,
                "volumes": {
                    "spool": {"emptyDir": {}},
                    "jail": {"persistentVolumeClaim": {"claimName": "jail-pvc"}},
                    "sharedMemorySize": "64Gi",
                    "customVolumeMounts": [
                        {
                            "name": "slurm-scripts",
                            "mountPath": "/opt/slurm_scripts/",
                            "volumeSource": {"configMap": {"name": "legacy-slurm-scripts"}},
                        },
                        {
                            "name": "slurm-scripts-jail",
                            "mountPath": "/mnt/jail.upper/opt/slurm_scripts/",
                            "volumeSource": {"configMap": {"name": "legacy-slurm-scripts"}},
                        },
                        {
                            "name": "slurm-configs-jail",
                            "mountPath": "/mnt/jail/etc/slurm",
                            "volumeSource": {"configMap": {"name": "legacy-slurm-configs"}},
                        },
                        {
                            "name": "site-scratch",
                            "mountPath": "/site/scratch",
                            "volumeSource": {"emptyDir": {}},
                        },
                    ],
                },
            },
            "munge": {
                "image": {
                    "repository": "old/munge",
                    "tag": "1.23.3",
                    "pullPolicy": "IfNotPresent",
                },
                "resources": {
                    "cpu": "100m",
                    "memory": "512Mi",
                    "ephemeralStorage": "5Gi",
                },
            },
        },
    }


def test_worker_nodeset_ready_state_prefers_ready_replicas() -> None:
    ready, detail = migration._worker_nodeset_ready_state(  # noqa: SLF001
        {
            "spec": {"replicas": 2},
            "status": {
                "phase": "Ready",
                "replicas": 2,
                "readyReplicas": 1,
                "conditions": [{"type": "PodsReady", "status": "False"}],
            },
        }
    )

    assert ready is False
    assert detail == "phase=Ready, ready=1/2"


def _source_report(
    snapshot: dict[str, Any] | None = None,
    *,
    target_k8s_version: str = "1.32",
) -> dict[str, Any]:
    snapshot = snapshot or _snapshot()
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="external-cluster",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
        target_k8s_version=target_k8s_version,
    ).to_dict()
    return {
        "schema": "nebius-cxcli-source-soperator-discovery/v1",
        "target_ref": "external-cluster",
        "snapshot": snapshot,
        "report": report,
    }


def _ready_node_group_status(version: str = "1.31", *, nodes: int = 1) -> dict[str, Any]:
    return {
        "version": version,
        "ready_node_count": nodes,
        "target_node_count": nodes,
        "node_count": nodes,
        "outdated_node_count": 0,
        "reconciling": False,
    }


def test_node_group_rollout_timeout_uses_live_status_count() -> None:
    assert (
        migration._node_group_rollout_timeout_seconds(
            {"spec": {}, "status": {"target_node_count": "7", "node_count": "7"}}
        )
        == 4200
    )


def _payload(
    *,
    include_placements: bool = False,
    target_k8s_version: str = "1.32",
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "externalNfs": {
            "enabled": True,
            "server": "nfs.example.invalid",
            "path": "/exports/home",
            "mountPath": "/home",
        }
    }
    soperator_row: dict[str, Any] = {
        "id": "soperator",
        "instance_id": "external-cluster",
        "enabled": True,
        "install_mode": "onboard-existing-cluster",
        "values": values,
    }
    if include_placements:
        soperator_row["placements"] = {
            "system": "cpu-pool",
            "controller": "cpu-pool",
            "login": "cpu-pool",
            "accounting": "cpu-pool",
            "worker": ["gpu-pool"],
        }
    payload: dict[str, Any] = {
        "client_info": {
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "eu-north1",
            }
        },
        "apps": {
            "charts": [
                soperator_row,
                {
                    "id": "nvidia-gpu-operator",
                    "instance_id": "external-cluster",
                    "enabled": True,
                    "repo": (
                        "oci://registry.example.invalid/nvidia-gpu-operator/chart/gpu-operator"
                    ),
                    "version": "v25.10.0",
                    "namespace": "nvidia-gpu-operator",
                    "release-name": "gpu-operator",
                    "values": {"driver": {"enabled": False}},
                },
                {
                    "id": "nvidia-network-operator",
                    "instance_id": "external-cluster",
                    "enabled": True,
                    "repo": (
                        "oci://registry.example.invalid/nvidia-network-operator/chart/"
                        "network-operator"
                    ),
                    "version": "25.7.0",
                    "namespace": "nvidia-network-operator",
                    "release-name": "network-operator",
                    "values": {"operator": {"ofedDriver": {"deploy": False}}},
                },
            ]
        },
        "deploy": {
            "targets": [
                {
                    "instance_id": "external-cluster",
                    "kind": "external-mk8s",
                    "kube_context": "external-context",
                    "soperator_onboarding": {
                        "accepted": True,
                        "state": "existing-soperator-supported",
                        "storage_mode": "create-aligned-sfs",
                        "compute_mode": "create-aligned-node-groups",
                        "actions": [
                            "adopt-soperator",
                            "reconcile-target-gpu-stack",
                            "upgrade-external-node-template",
                            "upgrade-soperator",
                            "approve-external-soperator-upgrade",
                            "create-aligned-sfs",
                            "plan-soperator-data-migration",
                            "plan-soperator-compute-migration",
                        ],
                        "source_version": "3.0.5",
                        "target_version": "4.0.1-ps.1",
                        "migration_profile_id": "v3-to-target",
                        "analysis_fingerprint": "",
                        "node_template_upgrade": {
                            "target_k8s_version": target_k8s_version,
                            "target_os": "ubuntu24.04",
                            "target_gpu_stack_preset": "cuda13.0",
                        },
                    },
                }
            ]
        },
    }
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    target["soperator_onboarding"]["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="external-cluster",
    )
    return payload


def _locked_upgrade_path_fixture() -> dict[str, Any]:
    jail_rootfs = {
        "current_image": "cr.example/populate_jail:3.0.5-slurm-cuda12.8.0",
        "current_version": "3.0.5-slurm-cuda12.8.0",
        "current_source": "completed-populate-jail-job",
        "current_job_name": "mk8s-populate-jail",
        "live_desired_image": "cr.example/populate_jail:3.0.5-slurm-cuda12.8.0",
        "live_desired_version": "3.0.5-slurm-cuda12.8.0",
        "live_desired_source": "slurmcluster.spec.populateJail.image",
        "slurmcluster_name": "mk8s",
        "target_image": "cr.example/populate_jail:4.0.1-slurm-cuda12.9.0",
        "target_version": "4.0.1-slurm-cuda12.9.0",
        "target_source": "pinned-chart-defaults",
        "refresh_required": True,
        "reason": "target populate-jail image differs from current jail rootfs image",
    }
    return {
        "schema": SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA,
        "locked": True,
        "source_k8s_version": "1.31",
        "target_k8s_version": "1.32",
        "soperator_app": {
            "current_version": "3.0.5",
            "target_version": "4.0.1",
            "upgrade_required": True,
        },
        "soperator_chart": {
            "current_version": "3.0.5",
            "target_version": "4.0.1-ps.1",
            "upgrade_required": True,
        },
        "jail_rootfs": jail_rootfs,
        "segments": [
            {
                "id": "segment-1-kubernetes-1-31-1-32-soperator",
                "title": "Kubernetes 1.31 -> 1.32 plus Soperator",
                "index": 1,
                "kind": "external-upgrade",
                "current_k8s_version": "1.31",
                "target_k8s_version": "1.32",
                "soperator_app": {
                    "current_version": "3.0.5",
                    "target_version": "4.0.1",
                    "upgrade_required": True,
                },
                "soperator_chart": {
                    "current_version": "3.0.5",
                    "target_version": "4.0.1-ps.1",
                    "upgrade_required": True,
                },
                "jail_rootfs": jail_rootfs,
                "k8s_upgrade_required": True,
                "soperator_upgrade_required": True,
                "actions": [
                    "approve-external-soperator-upgrade",
                    "upgrade-external-node-template",
                    "upgrade-soperator",
                ],
            }
        ],
    }


def _backup_metadata(label: str = "original-pre-upgrade") -> dict[str, Any]:
    return {
        "path": f"backups/{label}.tar.gz",
        "sha256": f"{label}-sha256",
        "manifest_sha256": f"{label}-manifest-sha256",
        "included_categories": [
            "accounting",
            "generated",
            "kubernetes",
            "recreation",
            "slurm",
            "soperator",
            "source",
        ],
        "raw_secret_material": True,
        "accounting_db_dump": True,
    }


def _backup_metadata_with_archive(
    config_path: Path,
    label: str = "original-pre-upgrade",
) -> dict[str, Any]:
    metadata = _backup_metadata(label)
    archive_bytes = f"test backup archive: {label}\n".encode()
    archive_path = config_path.parent / str(metadata["path"])
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(archive_bytes)
    metadata["size_bytes"] = len(archive_bytes)
    metadata["sha256"] = hashlib.sha256(archive_bytes).hexdigest()
    return metadata


def _backup_metadata_with_existing_archive(
    config_path: Path,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = dict(metadata)
    archive_path = config_path.parent / str(normalized["path"])
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        archive_bytes = archive_path.read_bytes()
    else:
        archive_bytes = f"test backup archive: {archive_path.name}\n".encode()
        archive_path.write_bytes(archive_bytes)
    normalized["size_bytes"] = len(archive_bytes)
    normalized["sha256"] = hashlib.sha256(archive_bytes).hexdigest()
    return normalized


def execute_soperator_migration(*args: Any, **kwargs: Any):
    if kwargs.get("approved") is True:
        raw_config_path = kwargs.get("config_path") if "config_path" in kwargs else args[0]
        config_path = Path(raw_config_path)
        if "backup_metadata" in kwargs:
            kwargs["backup_metadata"] = _backup_metadata_with_existing_archive(
                config_path,
                kwargs["backup_metadata"],
            )
        else:
            kwargs["backup_metadata"] = _backup_metadata_with_archive(
                config_path,
                "test-pre-upgrade",
            )
    if kwargs.get("nebius_api") is None and kwargs.get("command_runner") is None:
        kwargs["command_runner"] = _FakeCommandRunner()
    return _execute_soperator_migration(*args, **kwargs)


_STALE_KUBE_RBAC_PROXY_REPOSITORY = "gcr.io/kubebuilder/kube-rbac-proxy"
_TARGET_KUBE_RBAC_PROXY_REPOSITORY = "registry.k8s.io/kubebuilder/kube-rbac-proxy"
_TARGET_KUBE_RBAC_PROXY_TAG = "v0.15.0"


def _target_soperator_chart_values(payload: dict[str, Any]) -> dict[str, Any]:
    charts = payload["apps"]["charts"]  # type: ignore[index]
    assert isinstance(charts, list)
    values = charts[0]["values"]
    assert isinstance(values, dict)
    return values


def _seed_stale_kube_rbac_proxy_values(payload: dict[str, Any]) -> None:
    values = _target_soperator_chart_values(payload)
    values["controllerManager"] = {
        "kubeRbacProxy": {
            "image": {
                "repository": _STALE_KUBE_RBAC_PROXY_REPOSITORY,
                "tag": _TARGET_KUBE_RBAC_PROXY_TAG,
                "pullPolicy": "Always",
            }
        }
    }
    values["soperator-checks"] = {
        "checks": {
            "kubeRbacProxy": {
                "image": {
                    "repository": _STALE_KUBE_RBAC_PROXY_REPOSITORY,
                    "tag": _TARGET_KUBE_RBAC_PROXY_TAG,
                    "pullPolicy": "IfNotPresent",
                }
            }
        }
    }


def _assert_target_kube_rbac_proxy_values(helm_values: dict[str, Any]) -> None:
    controller_image = helm_values["controllerManager"]["kubeRbacProxy"]["image"]
    assert controller_image["repository"] == _TARGET_KUBE_RBAC_PROXY_REPOSITORY
    assert controller_image["tag"] == _TARGET_KUBE_RBAC_PROXY_TAG
    assert controller_image["pullPolicy"] == "Always"

    checks_image = helm_values["soperator-checks"]["checks"]["kubeRbacProxy"]["image"]
    assert checks_image["repository"] == _TARGET_KUBE_RBAC_PROXY_REPOSITORY
    assert checks_image["tag"] == _TARGET_KUBE_RBAC_PROXY_TAG
    assert checks_image["pullPolicy"] == "IfNotPresent"


def test_patch_target_kube_rbac_proxy_images_repairs_missing_or_malformed_paths() -> None:
    values: dict[str, Any] = {
        "controllerManager": {"kubeRbacProxy": {"image": "stale"}},
        "soperator-checks": "stale",
    }

    migration._patch_target_kube_rbac_proxy_images(values)  # noqa: SLF001

    assert values["controllerManager"]["kubeRbacProxy"]["image"] == {
        "repository": _TARGET_KUBE_RBAC_PROXY_REPOSITORY,
        "tag": _TARGET_KUBE_RBAC_PROXY_TAG,
    }
    assert values["soperator-checks"]["checks"]["kubeRbacProxy"]["image"] == {
        "repository": _TARGET_KUBE_RBAC_PROXY_REPOSITORY,
        "tag": _TARGET_KUBE_RBAC_PROXY_TAG,
    }


def _add_external_gpu_cluster_inventory(payload: dict[str, Any]) -> None:
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    assert isinstance(target, dict)
    target["inventory"] = {
        "node_groups": {
            "gpu-pool": {
                "gpu": True,
                "node_count": 2,
                "labels": {
                    "nebius.com/driverful": "true",
                    "nebius.com/drivers-preset": "cuda13.0",
                    "nebius.com/resource-preset": "8gpu-128vcpu-1600gb",
                    "topology.nebius.com/gpu-cluster-id": "gpu-cluster-1",
                },
                "allocatable": {"nvidia.com/gpu": "8"},
            }
        }
    }


@pytest.fixture(autouse=True)
def _stub_migration_quota_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        migration,
        "estimate_mk8s_quota_requirements",
        lambda **_kwargs: ((), ()),
    )

    def _assess(**kwargs: object) -> QuotaReport:
        return QuotaReport(
            tenant_id=str(kwargs.get("tenant_id", "")),
            project_id=str(kwargs.get("project_id", "")),
            region_id=str(kwargs.get("region_id", "")),
            checked_at="2026-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(migration, "assess_live_quota_requirements", _assess)


class _FakeNebiusApi:
    def __init__(self, runner: _FakeCommandRunner) -> None:
        self.runner = runner
        self.calls: list[tuple[Any, ...]] = []

    def get_filesystem_by_name(self, *, project_id: str, name: str) -> Mapping[str, Any]:
        self.calls.append(("filesystem.get_by_name", project_id, name))
        result = self.runner(
            [
                "nebius",
                "compute",
                "filesystem",
                "get-by-name",
                "--parent-id",
                project_id,
                "--name",
                name,
                "--format",
                "json",
            ],
            check=False,
        )
        if result.returncode != 0:
            return {}
        return json.loads(result.stdout or "{}")

    def create_filesystem(
        self,
        *,
        project_id: str,
        spec: migration.SoperatorAlignedFilesystemSpec,
        timeout_seconds: int = 1800,
    ) -> Mapping[str, Any]:
        self.calls.append(("filesystem.create", project_id, spec.name, timeout_seconds))
        args = [
            "nebius",
            "compute",
            "filesystem",
            "create",
            "--parent-id",
            project_id,
            "--name",
            spec.name,
            "--type",
            spec.filesystem_type,
            "--size-gibibytes",
            str(spec.size_gib),
            "--block-size-bytes",
            str(spec.block_size_kib * 1024),
            "--format",
            "json",
            "--timeout",
            "30m",
        ]
        if spec.forbid_deletion:
            args.insert(-4, "--forbid-deletion")
        result = self.runner(args, timeout_seconds=timeout_seconds)
        return json.loads(result.stdout or "{}")

    def get_node_group(self, node_group_id: str) -> Mapping[str, Any]:
        self.calls.append(("node_group.get", node_group_id))
        result = self.runner(
            ["nebius", "mk8s", "node-group", "get", node_group_id, "--format", "json"]
        )
        return json.loads(result.stdout or "{}")

    def list_node_groups(self, cluster_id: str) -> tuple[Mapping[str, Any], ...]:
        self.calls.append(("node_group.list", cluster_id))
        result = self.runner(
            [
                "nebius",
                "mk8s",
                "node-group",
                "list",
                "--parent-id",
                cluster_id,
                "--format",
                "json",
                "--all",
            ]
        )
        parsed = json.loads(result.stdout or "{}")
        items = parsed.get("items", []) if isinstance(parsed, dict) else []
        return tuple(item for item in items if isinstance(item, Mapping))

    def update_node_group(
        self,
        *,
        node_group_id: str,
        original_node_group: Mapping[str, Any],
        update_args: Sequence[str],
        strategy_args: Sequence[str],
        clear_template_gpu_settings: bool = False,
        timeout_seconds: int = 2700,
    ) -> Mapping[str, Any]:
        self.calls.append(
            (
                "node_group.update",
                node_group_id,
                tuple(update_args),
                tuple(strategy_args),
                clear_template_gpu_settings,
                timeout_seconds,
            )
        )
        result = self.runner(
            [
                "nebius",
                "mk8s",
                "node-group",
                "update",
                node_group_id,
                *update_args,
                *strategy_args,
                "--format",
                "json",
                "--timeout",
                migration._node_group_rollout_timeout_text(timeout_seconds),  # noqa: SLF001
            ],
            timeout_seconds=timeout_seconds,
        )
        payload = json.loads(result.stdout or "{}")
        if clear_template_gpu_settings:
            for node_group in self.runner.existing_node_groups:
                metadata = node_group.get("metadata")
                if isinstance(metadata, dict) and metadata.get("id") == node_group_id:
                    template = node_group.get("spec", {}).get("template", {})
                    if isinstance(template, dict):
                        template.pop("gpu_settings", None)
                        template.pop("gpuSettings", None)
                    payload = node_group
                    break
        return payload

    def create_node_group(
        self,
        *,
        payload: Mapping[str, Any],
        timeout_seconds: int = 3600,
    ) -> Mapping[str, Any]:
        metadata = dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {})
        name = str(metadata.get("name", "") or "")
        parent_id = str(metadata.get("parent_id", "") or metadata.get("parentId", "") or "")
        self.calls.append(("node_group.create", name, parent_id, timeout_seconds))
        result = self.runner(
            [
                "nebius",
                "mk8s",
                "node-group",
                "create",
                json.dumps(payload, sort_keys=True),
                "--format",
                "json",
                "--timeout",
                "60m",
            ],
            timeout_seconds=timeout_seconds,
        )
        return json.loads(result.stdout or "{}")

    def scale_node_group(
        self,
        *,
        node_group_id: str,
        count: int,
        timeout_seconds: int = 3000,
    ) -> None:
        self.calls.append(("node_group.scale", node_group_id, count, timeout_seconds))
        self.runner(
            [
                "nebius",
                "mk8s",
                "node-group",
                "update",
                node_group_id,
                "--fixed-node-count",
                str(count),
                "--format",
                "json",
                "--timeout",
                "45m",
            ],
            timeout_seconds=timeout_seconds,
            check=False,
        )

    def delete_node_group(
        self,
        *,
        node_group_id: str,
        timeout_seconds: int = 3000,
    ) -> None:
        self.calls.append(("node_group.delete", node_group_id, timeout_seconds))
        self.runner(
            ["nebius", "mk8s", "node-group", "delete", node_group_id, "--timeout", "45m"],
            timeout_seconds=timeout_seconds,
            check=False,
        )

    def get_cluster(self, cluster_id: str) -> Mapping[str, Any]:
        self.calls.append(("cluster.get", cluster_id))
        result = self.runner(["nebius", "mk8s", "cluster", "get", cluster_id, "--format", "json"])
        return json.loads(result.stdout or "{}")

    def update_cluster_control_plane(
        self,
        *,
        cluster_id: str,
        control_plane_version: str,
        timeout_seconds: int = 3600,
    ) -> Mapping[str, Any]:
        self.calls.append(("cluster.update_control_plane", cluster_id, control_plane_version))
        result = self.runner(
            [
                "nebius",
                "mk8s",
                "cluster",
                "update",
                cluster_id,
                "--control-plane-version",
                control_plane_version,
                "--format",
                "json",
                "--timeout",
                "60m",
            ],
            timeout_seconds=timeout_seconds,
        )
        return json.loads(result.stdout or "{}")

    def list_allocations(self, *, project_id: str) -> tuple[Mapping[str, Any], ...]:
        self.calls.append(("allocation.list", project_id))
        return tuple(self.runner.allocations)

    def get_allocation(self, allocation_id: str) -> Mapping[str, Any]:
        self.calls.append(("allocation.get", allocation_id))
        for allocation in self.runner.allocations:
            metadata = allocation.get("metadata")
            if isinstance(metadata, Mapping) and metadata.get("id") == allocation_id:
                return allocation
        raise RuntimeError(f"allocation not found: {allocation_id}")

    def update_allocation_labels(
        self,
        *,
        allocation_id: str,
        original_allocation: Mapping[str, Any],
        labels: Mapping[str, str],
        timeout_seconds: int = 300,
    ) -> Mapping[str, Any]:
        self.calls.append(
            ("allocation.update_labels", allocation_id, dict(labels), timeout_seconds)
        )
        for allocation in self.runner.allocations:
            metadata = allocation.setdefault("metadata", {})
            if isinstance(metadata, dict) and metadata.get("id") == allocation_id:
                metadata["labels"] = dict(labels)
                return allocation
        raise RuntimeError(f"allocation not found: {allocation_id}")

    def close(self) -> None:
        self.calls.append(("close",))


class _FakeCommandRunner:
    def __init__(
        self,
        *,
        existing_filesystems: dict[str, dict[str, Any]] | None = None,
        helm_errors: list[str] | None = None,
        helm_errors_by_release: dict[tuple[str, str], list[str]] | None = None,
        helm_timeouts_by_release: dict[tuple[str, str], list[bool]] | None = None,
        helm_history: list[dict[str, Any]] | None = None,
        existing_node_groups: list[dict[str, Any]] | None = None,
        live_nodes: list[dict[str, Any]] | None = None,
        live_pods: list[dict[str, Any]] | None = None,
        live_pvcs: list[dict[str, Any]] | None = None,
        live_slurmclusters: list[dict[str, Any]] | None = None,
        live_nodesets: dict[str, dict[str, Any]] | None = None,
        live_flux_helmreleases: list[dict[str, Any]] | None = None,
        helm_statuses: dict[tuple[str, str], str] | None = None,
        helm_releases: list[dict[str, Any]] | None = None,
        helm_manifests: dict[tuple[str, str], str] | None = None,
        live_workloads: dict[tuple[str, str, str], dict[str, Any]] | None = None,
        live_kubernetes_resources: dict[str, list[dict[str, Any]]] | None = None,
        helm_storage_secrets: list[dict[str, Any]] | None = None,
        allocations: list[dict[str, Any]] | None = None,
        worker_topology_by_nodeset: dict[str, dict[str, int]] | None = None,
        slurm_resource_names: list[str] | None = None,
        slurm_queue_output: str = "",
        slurm_queue_outputs: list[str] | None = None,
        slurm_queue_returncode: int = 0,
        cluster: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.existing_filesystems = existing_filesystems or {}
        self.helm_errors = list(helm_errors or [])
        self.helm_errors_by_release = {
            key: list(value) for key, value in (helm_errors_by_release or {}).items()
        }
        self.helm_timeouts_by_release = {
            key: list(value) for key, value in (helm_timeouts_by_release or {}).items()
        }
        self.helm_history = list(helm_history or [])
        self.existing_node_groups = list(existing_node_groups or [])
        self.cluster = cluster or {
            "metadata": {"id": "cluster-123", "name": "external-cluster"},
            "spec": {"control_plane": {"version": "1.31"}},
        }
        self.live_nodes = list(
            live_nodes
            or [
                {
                    "metadata": {
                        "name": "cpu-node-a",
                        "labels": {
                            "nebius.com/node-group": "cpu-pool",
                            "slurm.nebius.ai/nodeset-name": "login",
                        },
                    },
                    "spec": {},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "metadata": {
                        "name": "node-a",
                        "labels": {"nebius.com/node-group": "gpu-pool"},
                    },
                    "spec": {},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
            ]
        )
        self.live_pods = list(
            live_pods
            if live_pods is not None
            else [
                {
                    "metadata": {
                        "name": "login-0",
                        "labels": {"app.kubernetes.io/component": "login"},
                    },
                    "status": {"phase": "Running"},
                },
                {
                    "metadata": {
                        "name": "worker-0",
                        "labels": {
                            "slurm.nebius.ai/nodeset": "worker",
                            "slurm.nebius.ai/worker": "true",
                        },
                    },
                    "spec": {"nodeName": "node-a"},
                    "status": {"phase": "Running"},
                }
            ]
        )
        self.live_pvcs = list(live_pvcs or [])
        self.live_jobs: dict[tuple[str, str], dict[str, Any]] = {}
        self.live_slurmclusters = list(
            live_slurmclusters
            or [
                {
                    "metadata": {"name": "external-cluster", "namespace": "soperator"},
                    "spec": {
                        "populateJail": {
                            "image": "registry.example/soperator/populate-jail:4.0.1-ps.1"
                        }
                    },
                    "status": {"phase": "Available"},
                }
            ]
        )
        self.live_nodesets = (
            None if live_nodesets is None else json.loads(json.dumps(live_nodesets))
        )
        self.live_flux_helmreleases = list(live_flux_helmreleases or [])
        self.helm_statuses = dict(helm_statuses or {})
        self.helm_releases = list(
            helm_releases
            if helm_releases is not None
            else [
                {
                    "name": "soperator",
                    "namespace": "soperator",
                    "chart": "soperator-4.0.1-ps.1",
                    "app_version": "4.0.1",
                    "status": "deployed",
                    "revision": "1",
                }
            ]
        )
        self.helm_storage_secrets = list(
            helm_storage_secrets
            if helm_storage_secrets is not None
            else [
                {
                    "metadata": {
                        "namespace": "flux-system",
                        "name": (
                            "sh.helm.release.v1."
                            + str(release.get("name", ""))
                            + ".v"
                            + str(release.get("revision", "1") or "1")
                        ),
                        "labels": {
                            "owner": "helm",
                            "name": str(release.get("name", "")),
                            "status": str(release.get("status", "")),
                            "version": str(release.get("revision", "1") or "1"),
                        },
                    }
                }
                for release in self.helm_releases
            ]
        )
        self.allocations = list(
            allocations
            if allocations is not None
            else [
                {
                    "metadata": {
                        "id": "vpcallocation-login",
                        "name": "login",
                        "labels": {},
                    },
                    "spec": {"ipv4_public": {"cidr": "203.0.113.10/32"}},
                    "status": {
                        "details": {"allocated_cidr": "203.0.113.10/32"},
                    },
                }
            ]
        )
        self.helm_manifests = dict(
            helm_manifests
            or {
                (
                    "soperator",
                    "soperator",
                ): """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: soperator-manager
  namespace: soperator
spec:
  replicas: 1
""",
            }
        )
        self.live_workloads = dict(
            live_workloads
            or {
                (
                    "Deployment",
                    "soperator",
                    "soperator-manager",
                ): {
                    "metadata": {"generation": 1},
                    "spec": {"replicas": 1},
                    "status": {
                        "observedGeneration": 1,
                        "readyReplicas": 1,
                        "availableReplicas": 1,
                        "updatedReplicas": 1,
                    },
                },
                (
                    "StatefulSet",
                    "soperator",
                    "login",
                ): {
                    "metadata": {"generation": 1},
                    "spec": {"replicas": 2},
                    "status": {
                        "observedGeneration": 1,
                        "readyReplicas": 2,
                        "updatedReplicas": 2,
                    },
                },
            }
        )
        self.live_services = [
            {
                "metadata": {
                    "name": "login",
                    "namespace": "soperator",
                    "uid": "svc-login",
                    "annotations": {
                        "nebius.com/load-balancer-allocation-id": "vpcallocation-login",
                    },
                },
                "spec": {"type": "LoadBalancer", "clusterIP": "10.0.0.10"},
                "status": {"loadBalancer": {"ingress": [{"ip": "203.0.113.10"}]}},
            },
        ]
        self.live_endpoint_slices = [
            {
                "metadata": {
                    "name": "login-ready",
                    "namespace": "soperator",
                    "labels": {"kubernetes.io/service-name": "login"},
                },
                "endpoints": [{"conditions": {"ready": True}}],
            }
        ]
        self.live_kubernetes_resources = {
            key: list(value) for key, value in (live_kubernetes_resources or {}).items()
        }
        self.worker_topology_by_nodeset = dict(worker_topology_by_nodeset or {})
        self.slurm_resource_names = list(
            slurm_resource_names
            or [
                "slurmcluster.slurm.nebius.ai/external-cluster",
                "nodeset.slurm.nebius.ai/worker",
            ]
        )
        self.slurm_queue_output = slurm_queue_output
        self.slurm_queue_outputs = list(slurm_queue_outputs or [])
        self.slurm_queue_returncode = slurm_queue_returncode
        self.file_update_payloads: list[dict[str, Any]] = []
        self._populate_jail_refresh_saved_pods: list[dict[str, Any]] | None = None
        self.nebius_api = _FakeNebiusApi(self)

    def _upsert_helm_release(
        self,
        *,
        namespace: str,
        release_name: str,
        chart: str,
        app_version: str,
    ) -> None:
        replacement = {
            "name": release_name,
            "namespace": namespace,
            "chart": chart,
            "app_version": app_version,
            "status": "deployed",
            "revision": "1",
        }
        for index, release in enumerate(self.helm_releases):
            if release.get("name") == release_name and release.get("namespace") == namespace:
                self.helm_releases[index] = replacement
                return
        self.helm_releases.append(replacement)

    def _remove_helm_release_if_storage_is_gone(
        self,
        *,
        release_name: str,
        revision: str = "",
    ) -> None:
        def _metadata_labels(secret: dict[str, Any]) -> dict[str, Any]:
            metadata = secret.get("metadata")
            if not isinstance(metadata, dict):
                return {}
            labels = metadata.get("labels")
            return labels if isinstance(labels, dict) else {}

        def _secret_matches(secret: dict[str, Any]) -> bool:
            labels = _metadata_labels(secret)
            if str(labels.get("name", "")) != release_name:
                return False
            return not revision or str(labels.get("version", "")) == revision

        if any(_secret_matches(secret) for secret in self.helm_storage_secrets):
            return
        self.helm_releases = [
            release
            for release in self.helm_releases
            if not (
                str(release.get("name", "") or "") == release_name
                and (
                    not revision
                    or not str(release.get("revision", "") or "")
                    or str(release.get("revision", "") or "") == revision
                )
            )
        ]

    def _delete_live_kubernetes_resource(
        self,
        *,
        resource_type: str,
        name: str,
        namespace: str = "",
    ) -> None:
        resources = self.live_kubernetes_resources.get(resource_type, [])
        kept: list[dict[str, Any]] = []
        for item in resources:
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                kept.append(item)
                continue
            item_name = str(metadata.get("name", "") or "")
            item_namespace = str(metadata.get("namespace", "") or "")
            if item_name == name and (not namespace or item_namespace == namespace):
                continue
            kept.append(item)
        self.live_kubernetes_resources[resource_type] = kept

    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        command: Any = tuple(str(item) for item in args)
        self.calls.append((command, input_text))
        if command[:4] == ("nebius", "compute", "filesystem", "get-by-name"):
            name = command[command.index("--name") + 1]
            if name in self.existing_filesystems:
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    json.dumps(self.existing_filesystems[name]),
                    "",
                )
            return SoperatorMigrationCommandResult(command, 1, "", "not found")
        if command[:4] == ("nebius", "compute", "filesystem", "create"):
            name = command[command.index("--name") + 1]
            self.existing_filesystems[name] = {
                "metadata": {"id": f"filesystem-{name}", "name": name},
                "spec": {
                    "size_gibibytes": int(command[command.index("--size-gibibytes") + 1]),
                    "block_size_bytes": int(command[command.index("--block-size-bytes") + 1]),
                    "type": command[command.index("--type") + 1],
                },
            }
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(self.existing_filesystems[name]),
                "",
            )
        if command[:4] == ("nebius", "mk8s", "node-group", "get"):
            for node_group in self.existing_node_groups:
                metadata = node_group.get("metadata")
                if isinstance(metadata, dict) and metadata.get("id") == command[4]:
                    return SoperatorMigrationCommandResult(
                        command,
                        0,
                        json.dumps(node_group),
                        "",
                    )
            default_node_group = {
                "metadata": {
                    "id": command[4],
                    "name": command[4],
                    "parent_id": "cluster-123",
                },
                "spec": {
                    "version": "1.31",
                    "template": {
                        "metadata": {"labels": {}},
                        "resources": {"platform": "cpu-d3", "preset": "4vcpu-16gb"},
                        "boot_disk": {"type": "NETWORK_SSD", "size_gibibytes": "93"},
                        "os": {"name": "ubuntu24.04"},
                        "network_interfaces": [{"subnet_id": "subnet-123"}],
                        "filesystems": [],
                    },
                },
                "status": _ready_node_group_status(),
            }
            self.existing_node_groups.append(default_node_group)
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(default_node_group),
                "",
            )
        if command[:4] == ("nebius", "mk8s", "cluster", "get"):
            cluster = json.loads(json.dumps(self.cluster))
            metadata = cluster.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata.setdefault("id", command[4])
            return SoperatorMigrationCommandResult(command, 0, json.dumps(cluster), "")
        if command[:4] == ("nebius", "mk8s", "cluster", "update"):
            spec = self.cluster.setdefault("spec", {})
            if not isinstance(spec, dict):
                spec = {}
                self.cluster["spec"] = spec
            control_plane = spec.setdefault("control_plane", {})
            if not isinstance(control_plane, dict):
                control_plane = {}
                spec["control_plane"] = control_plane
            if "--control-plane-version" in command:
                control_plane["version"] = command[command.index("--control-plane-version") + 1]
            return SoperatorMigrationCommandResult(command, 0, json.dumps(self.cluster), "")
        if command[:4] == ("nebius", "mk8s", "node-group", "list"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": self.existing_node_groups}),
                "",
            )
        if command[:4] == ("nebius", "mk8s", "node-group", "update"):
            node_group_id = command[4]
            for node_group in self.existing_node_groups:
                metadata = node_group.get("metadata")
                if not isinstance(metadata, dict) or metadata.get("id") != node_group_id:
                    continue
                if "--file" in command:
                    replacement = json.loads(
                        Path(command[command.index("--file") + 1]).read_text(encoding="utf-8")
                    )
                    self.file_update_payloads.append(json.loads(json.dumps(replacement)))
                    node_group.clear()
                    node_group.update(replacement)
                    node_group.setdefault(
                        "status",
                        _ready_node_group_status(
                            str(
                                (
                                    replacement.get("spec", {})
                                    if isinstance(replacement.get("spec"), dict)
                                    else {}
                                ).get("version", "1.31")
                            )
                        ),
                    )
                    return SoperatorMigrationCommandResult(
                        command,
                        0,
                        json.dumps(node_group),
                        "",
                    )
                spec = node_group.setdefault("spec", {})
                if not isinstance(spec, dict):
                    spec = {}
                    node_group["spec"] = spec
                template = spec.setdefault("template", {})
                if not isinstance(template, dict):
                    template = {}
                    spec["template"] = template
                if "--template-filesystems" in command:
                    template["filesystems"] = json.loads(
                        command[command.index("--template-filesystems") + 1]
                    )
                if "--version" in command:
                    spec["version"] = command[command.index("--version") + 1]
                if "--template-os" in command:
                    template["os"] = command[command.index("--template-os") + 1]
                if "--template-gpu-settings-drivers-preset" in command:
                    gpu_settings = template.setdefault("gpu_settings", {})
                    if not isinstance(gpu_settings, dict):
                        gpu_settings = {}
                        template["gpu_settings"] = gpu_settings
                    gpu_settings["drivers_preset"] = command[
                        command.index("--template-gpu-settings-drivers-preset") + 1
                    ]
                if "--fixed-node-count" in command:
                    spec["fixed_node_count"] = int(command[command.index("--fixed-node-count") + 1])
                strategy = spec.setdefault("strategy", {})
                if not isinstance(strategy, dict):
                    strategy = {}
                    spec["strategy"] = strategy
                if "--strategy-max-surge-count" in command:
                    strategy["max_surge"] = {
                        "count": int(command[command.index("--strategy-max-surge-count") + 1])
                    }
                if "--strategy-max-surge-percent" in command:
                    strategy["max_surge"] = {
                        "percent": int(command[command.index("--strategy-max-surge-percent") + 1])
                    }
                if "--strategy-max-unavailable-count" in command:
                    strategy["max_unavailable"] = {
                        "count": int(command[command.index("--strategy-max-unavailable-count") + 1])
                    }
                if "--strategy-max-unavailable-percent" in command:
                    strategy["max_unavailable"] = {
                        "percent": int(
                            command[command.index("--strategy-max-unavailable-percent") + 1]
                        )
                    }
                if "--strategy-drain-timeout" in command:
                    strategy["drain_timeout"] = command[
                        command.index("--strategy-drain-timeout") + 1
                    ]
                return SoperatorMigrationCommandResult(command, 0, json.dumps(node_group), "")
            return SoperatorMigrationCommandResult(command, 0, "{}", "")
        if command[:4] == ("nebius", "mk8s", "node-group", "create"):
            payload = json.loads(command[4])
            name = payload["metadata"]["name"]
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"metadata": {"id": f"nodegroup-{name}", "name": name}}),
                "",
            )
        if command and command[0] == "helm" and "status" in command:
            release_name = command[command.index("status") + 1]
            namespace = command[command.index("-n") + 1] if "-n" in command else ""
            status = self.helm_statuses.get((namespace, release_name))
            if not status:
                return SoperatorMigrationCommandResult(command, 1, "", "not found")
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"info": {"status": status}}),
                "",
            )
        if command and command[0] == "helm" and "list" in command:
            namespace = command[command.index("-n") + 1] if "-n" in command else ""
            filter_pattern = command[command.index("--filter") + 1] if "--filter" in command else ""
            releases = []
            for release in self.helm_releases:
                if namespace and release.get("namespace") != namespace:
                    continue
                if filter_pattern and not re.search(
                    filter_pattern,
                    str(release.get("name") or ""),
                ):
                    continue
                releases.append(release)
            return SoperatorMigrationCommandResult(command, 0, json.dumps(releases), "")
        if command and command[0] == "helm" and "get" in command and "manifest" in command:
            release_name = command[command.index("manifest") + 1]
            namespace = command[command.index("-n") + 1] if "-n" in command else ""
            manifest = self.helm_manifests.get((namespace, release_name), "")
            if manifest:
                return SoperatorMigrationCommandResult(command, 0, manifest, "")
            return SoperatorMigrationCommandResult(command, 1, "", "not found")
        if command and command[0] == "helm" and "history" in command:
            return SoperatorMigrationCommandResult(command, 0, json.dumps(self.helm_history), "")
        if command and command[0] == "helm" and "template" in command:
            namespace = command[command.index("-n") + 1] if "-n" in command else "soperator"
            return SoperatorMigrationCommandResult(
                command,
                0,
                f"""
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: slurm-local-pv
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: soperator-mount-scripts
  namespace: {namespace}
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: jail-persistent-home-pv
spec:
  claimRef:
    namespace: {namespace}
    name: jail-persistent-home-pvc
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: jail-persistent-home-pvc
  namespace: {namespace}
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: jail-mount
  namespace: {namespace}
""",
                "",
            )
        if command and command[0] == "helm" and "upgrade" in command:
            release_name = command[command.index("--install") + 1] if "--install" in command else ""
            namespace = command[command.index("-n") + 1] if "-n" in command else ""
            version = (
                command[command.index("--version") + 1] if "--version" in command else "4.0.1-ps.1"
            )
            chart = (
                command[command.index("--install") + 2] if "--install" in command else release_name
            )
            chart_name = Path(chart).name or release_name
            release_timeouts = self.helm_timeouts_by_release.get((namespace, release_name))
            if release_timeouts:
                release_timeouts.pop(0)
                if not release_timeouts:
                    self.helm_timeouts_by_release.pop((namespace, release_name), None)
                if release_name != "soperator":
                    self.helm_statuses[(namespace, release_name)] = "deployed"
                    self._upsert_helm_release(
                        namespace=namespace,
                        release_name=release_name,
                        chart=f"{chart_name}-{version}",
                        app_version=version,
                    )
                    self.helm_manifests.setdefault(
                        (namespace, release_name),
                        f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {release_name}
  namespace: {namespace}
spec:
  replicas: 1
""",
                    )
                    self.live_workloads.setdefault(
                        ("Deployment", namespace, release_name),
                        {
                            "metadata": {"generation": 1},
                            "spec": {"replicas": 1},
                            "status": {
                                "observedGeneration": 1,
                                "readyReplicas": 1,
                                "availableReplicas": 1,
                                "updatedReplicas": 1,
                            },
                        },
                    )
                raise subprocess.TimeoutExpired(list(command), timeout_seconds)
            release_errors = self.helm_errors_by_release.get((namespace, release_name))
            if release_errors:
                error = release_errors.pop(0)
                if not release_errors:
                    self.helm_errors_by_release.pop((namespace, release_name), None)
                if check:
                    raise RuntimeError(error)
                return SoperatorMigrationCommandResult(command, 1, "", error)
            if release_name != "soperator":
                self.helm_statuses[(namespace, release_name)] = "deployed"
                self._upsert_helm_release(
                    namespace=namespace,
                    release_name=release_name,
                    chart=f"{chart_name}-{version}",
                    app_version=version,
                )
                self.helm_manifests.setdefault(
                    (namespace, release_name),
                    f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {release_name}
  namespace: {namespace}
spec:
  replicas: 1
""",
                )
                self.live_workloads.setdefault(
                    ("Deployment", namespace, release_name),
                    {
                        "metadata": {"generation": 1},
                        "spec": {"replicas": 1},
                        "status": {
                            "observedGeneration": 1,
                            "readyReplicas": 1,
                            "availableReplicas": 1,
                            "updatedReplicas": 1,
                        },
                    },
                )
                return SoperatorMigrationCommandResult(command, 0, "{}", "")
            if self.helm_errors:
                error = self.helm_errors.pop(0)
                if check:
                    raise RuntimeError(error)
                return SoperatorMigrationCommandResult(command, 1, "", error)
            self.helm_statuses[(namespace, release_name)] = "deployed"
            self._upsert_helm_release(
                namespace=namespace,
                release_name=release_name,
                chart="soperator-4.0.1-ps.1",
                app_version="4.0.1",
            )
            return SoperatorMigrationCommandResult(command, 0, "{}", "")
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-A",
            "-o",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": self.live_flux_helmreleases}),
                "",
            )
        if command[:9] == (
            "kubectl",
            "--context",
            "external-context",
            "get",
            "secrets",
            "-A",
            "-l",
            "owner=helm",
            "-o",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": self.helm_storage_secrets}),
                "",
            )
        if (
            len(command) >= 7
            and command[:3] == ("kubectl", "--context", "external-context")
            and command[3] == "get"
            and "-o" in command
            and command[command.index("-o") + 1] == "json"
        ):
            resource_type = command[4]
            if resource_type in self.live_kubernetes_resources:
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    json.dumps({"items": self.live_kubernetes_resources[resource_type]}),
                    "",
                )
        if (
            len(command) >= 9
            and command[:3] == ("kubectl", "--context", "external-context")
            and command[3] == "-n"
            and command[4] == "soperator"
            and command[5] == "get"
            and command[6].startswith("job/")
            and command[-2:] == ("-o", "json")
        ):
            name = command[6].split("/", 1)[1]
            stored = self.live_jobs.get(("soperator", name))
            if stored is not None:
                return SoperatorMigrationCommandResult(command, 0, json.dumps(stored), "")
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"status": {"conditions": [{"type": "Complete", "status": "True"}]}}),
                "",
            )
        if (
            len(command) >= 7
            and command[:3] == ("kubectl", "--context", "external-context")
            and command[3] == "-n"
            and command[4] == "soperator"
            and command[5] == "logs"
            and command[6].endswith("-jail-capacity-probe")
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "active_used_kib=83886080\npassive_available_kib=134217728\n",
                "",
            )
        if (
            len(command) >= 7
            and command[:3] == ("kubectl", "--context", "external-context")
            and command[3] == "-n"
            and command[4] == "soperator"
            and command[5] == "logs"
            and command[6].endswith("-jail-persistent-source-probe")
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "\n".join(
                    (
                        '{"mount_path":"/home","source_path":"/store/home","target_path":"/store/shared/home","marker_path":"/store/.cxcli/persistent-migrations/home.json","source_status":"present","marker_status":"none","marker_present":false}',
                        '{"mount_path":"/data","source_path":"/store/data","target_path":"/store/shared/data","marker_path":"/store/.cxcli/persistent-migrations/data.json","source_status":"present","marker_status":"none","marker_present":false}',
                        '{"mount_path":"/scripts","source_path":"/store/scripts","target_path":"/store/shared/scripts","marker_path":"/store/.cxcli/persistent-migrations/scripts.json","source_status":"absent","marker_status":"none","marker_present":false}',
                        '{"mount_path":"/models","source_path":"/store/models","target_path":"/store/shared/models","marker_path":"/store/.cxcli/persistent-migrations/models.json","source_status":"absent","marker_status":"none","marker_present":false}',
                    )
                )
                + "\n",
                "",
            )
        if (
            len(command) >= 7
            and command[:3] == ("kubectl", "--context", "external-context")
            and command[3] == "-n"
            and command[4] == "soperator"
            and command[5] == "logs"
            and command[6].endswith("-jail-persistent-migration")
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "\n".join(
                    (
                        "persistent mount migration copied: /home",
                        "persistent mount migration copied: /data",
                        "persistent mount migration source missing: /scripts",
                        "persistent mount migration source missing: /models",
                    )
                )
                + "\n",
                "",
            )
        if command == ("kubectl", "--context", "external-context", "apply", "-f", "-"):
            try:
                payload = json.loads(input_text or "{}")
            except json.JSONDecodeError:
                payload = {}
            items = payload.get("items") if isinstance(payload, dict) else None
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    metadata = item.get("metadata")
                    if not isinstance(metadata, dict):
                        continue
                    name = str(metadata.get("name", "") or "")
                    namespace = str(metadata.get("namespace", "") or "soperator")
                    if not name:
                        continue
                    if item.get("kind") == "Job":
                        stored_job = json.loads(json.dumps(item))
                        stored_job.setdefault("status", {}).setdefault(
                            "conditions", [{"type": "Complete", "status": "True"}]
                        )
                        stored_job.setdefault("status", {}).setdefault("succeeded", 1)
                        self.live_jobs[(namespace, name)] = stored_job
                        continue
                    if item.get("kind") != "PersistentVolumeClaim":
                        continue
                    self.live_pvcs = [
                        pvc
                        for pvc in self.live_pvcs
                        if not (
                            isinstance(pvc.get("metadata"), dict)
                            and str(pvc["metadata"].get("name", "") or "") == name
                            and str(pvc["metadata"].get("namespace", "") or "soperator")
                            == namespace
                        )
                    ]
                    self.live_pvcs.append({"metadata": {"name": name, "namespace": namespace}})
            return SoperatorMigrationCommandResult(command, 0, "{}", "")
        if (
            len(command) >= 9
            and command[:3] == ("kubectl", "--context", "external-context")
            and command[3] == "-n"
            and command[5] == "get"
            and "/" in command[6]
            and "-o" in command
            and command[command.index("-o") + 1] == "json"
        ):
            namespace = command[4]
            resource_type, name = command[6].split("/", 1)
            if resource_type not in self.live_kubernetes_resources:
                return SoperatorMigrationCommandResult(command, 0, "{}", "")
            resources = self.live_kubernetes_resources.get(resource_type, [])
            for item in resources:
                metadata = item.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                if (
                    str(metadata.get("namespace", "") or "") == namespace
                    and str(metadata.get("name", "") or "") == name
                ):
                    return SoperatorMigrationCommandResult(
                        command,
                        0,
                        json.dumps(item),
                        "",
                    )
            if namespace == "soperator":
                return SoperatorMigrationCommandResult(command, 0, "{}", "")
            return SoperatorMigrationCommandResult(command, 1, "", "NotFound")
        if (
            len(command) >= 10
            and command[:3] == ("kubectl", "--context", "external-context")
            and command[3] == "-n"
            and command[5] == "scale"
            and "/" in command[6]
            and "--replicas" in command
        ):
            namespace = command[4]
            resource_type, name = command[6].split("/", 1)
            replicas = int(command[command.index("--replicas") + 1])
            for item in self.live_kubernetes_resources.get(resource_type, []):
                metadata = item.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                if (
                    str(metadata.get("namespace", "") or "") == namespace
                    and str(metadata.get("name", "") or "") == name
                ):
                    spec = item.setdefault("spec", {})
                    if not isinstance(spec, dict):
                        spec = {}
                        item["spec"] = spec
                    spec["replicas"] = replicas
                    status = item.setdefault("status", {})
                    if not isinstance(status, dict):
                        status = {}
                        item["status"] = status
                    status["availableReplicas"] = replicas
                    status["readyReplicas"] = replicas
            return SoperatorMigrationCommandResult(command, 0, "{}", "")
        if (
            len(command) >= 10
            and command[:3] == ("kubectl", "--context", "external-context")
            and command[3] == "-n"
            and command[5] == "scale"
            and command[6] == "deployment"
            and "--replicas=0" in command
        ):
            namespace = command[4]
            name = command[7]
            for item in self.live_kubernetes_resources.get("deployment", []):
                metadata = item.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                if (
                    str(metadata.get("namespace", "") or "") == namespace
                    and str(metadata.get("name", "") or "") == name
                ):
                    spec = item.setdefault("spec", {})
                    if not isinstance(spec, dict):
                        spec = {}
                        item["spec"] = spec
                    spec["replicas"] = 0
                    status = item.setdefault("status", {})
                    if not isinstance(status, dict):
                        status = {}
                        item["status"] = status
                    status["availableReplicas"] = 0
                    status["readyReplicas"] = 0
            return SoperatorMigrationCommandResult(command, 0, "{}", "")
        if (
            len(command) >= 8
            and command[:3] == ("kubectl", "--context", "external-context")
            and command[3] == "-n"
            and command[5:7] == ("delete", "secret")
        ):
            namespace = command[4]
            secret_name = command[7]
            release_revisions: set[tuple[str, str]] = set()
            for secret in self.helm_storage_secrets:
                metadata = secret.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                labels = metadata.get("labels")
                if not isinstance(labels, dict):
                    labels = {}
                if (
                    str(metadata.get("namespace", "") or "") == namespace
                    and str(metadata.get("name", "") or "") == secret_name
                ):
                    release_revisions.add(
                        (
                            str(labels.get("name", "") or ""),
                            str(labels.get("version", "") or ""),
                        )
                    )
            self.helm_storage_secrets = [
                secret
                for secret in self.helm_storage_secrets
                if not (
                    isinstance(secret.get("metadata"), dict)
                    and str(secret["metadata"].get("namespace", "") or "") == namespace
                    and str(secret["metadata"].get("name", "") or "") == secret_name
                )
            ]
            for release_name, revision in release_revisions:
                self._remove_helm_release_if_storage_is_gone(
                    release_name=release_name,
                    revision=revision,
                )
            return SoperatorMigrationCommandResult(command, 0, "{}", "")
        if (
            len(command) >= 8
            and command[:3] == ("kubectl", "--context", "external-context")
            and (
                command[3] == "delete"
                or (command[3] == "-n" and len(command) >= 10 and command[5] == "delete")
            )
        ):
            if command[3] == "-n":
                namespace = command[4]
                resource_type = command[6]
                name = command[7]
            else:
                namespace = ""
                resource_type = command[4]
                name = command[5]
            if resource_type in {
                "helmrelease",
                "helmreleases",
                "helmrelease.helm.toolkit.fluxcd.io",
                "helmreleases.helm.toolkit.fluxcd.io",
            }:
                self.live_flux_helmreleases = [
                    item
                    for item in self.live_flux_helmreleases
                    if not (
                        isinstance(item.get("metadata"), dict)
                        and str(item["metadata"].get("name", "") or "") == name
                        and (
                            not namespace
                            or str(item["metadata"].get("namespace", "") or "") == namespace
                        )
                    )
                ]
                return SoperatorMigrationCommandResult(command, 0, "{}", "")
            self._delete_live_kubernetes_resource(
                resource_type=resource_type,
                namespace=namespace,
                name=name,
            )
            return SoperatorMigrationCommandResult(command, 0, "{}", "")
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "flux-system",
            "patch",
            "helmrelease.helm.toolkit.fluxcd.io",
        ):
            name = command[7]
            for item in self.live_flux_helmreleases:
                metadata = item.get("metadata")
                if isinstance(metadata, dict) and metadata.get("name") == name:
                    spec = item.setdefault("spec", {})
                    if not isinstance(spec, dict):
                        spec = {}
                        item["spec"] = spec
                    spec["suspend"] = True
            return SoperatorMigrationCommandResult(command, 0, "{}", "")
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "annotate",
            "service",
        ):
            service_name = command[7]
            annotations = [
                item
                for item in command[8:]
                if "=" in item and not item.startswith("--")
            ]
            for item in self.live_services:
                metadata = item.setdefault("metadata", {})
                if not isinstance(metadata, dict) or metadata.get("name") != service_name:
                    continue
                service_annotations = metadata.setdefault("annotations", {})
                if not isinstance(service_annotations, dict):
                    service_annotations = {}
                    metadata["annotations"] = service_annotations
                for annotation in annotations:
                    key, value = annotation.split("=", 1)
                    service_annotations[key] = value
                return SoperatorMigrationCommandResult(command, 0, "service/login annotated", "")
            return SoperatorMigrationCommandResult(command, 1, "", "service not found")
        if (
            command
            and command[0] == "kubectl"
            and "--context" in command
            and "-n" in command
            and "get" in command
            and "-o" in command
            and command[command.index("-o") + 1] == "json"
        ):
            kind_by_resource = {
                "deployment": "Deployment",
                "deploy": "Deployment",
                "statefulset": "StatefulSet",
                "statefulset.apps.kruise.io": "StatefulSet",
                "statefulsets.apps.kruise.io": "StatefulSet",
                "daemonset": "DaemonSet",
            }
            resource = command[command.index("get") + 1]
            namespace = command[command.index("-n") + 1]
            if resource in {"service", "services", "svc"}:
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    json.dumps({"items": self.live_services}),
                    "",
                )
            if resource.startswith("endpointslices"):
                selector = command[command.index("-l") + 1] if "-l" in command else ""
                service_name = selector.removeprefix("kubernetes.io/service-name=")
                items = [
                    item
                    for item in self.live_endpoint_slices
                    if not service_name
                    or item.get("metadata", {}).get("labels", {}).get(
                        "kubernetes.io/service-name"
                    )
                    == service_name
                ]
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    json.dumps({"items": items}),
                    "",
                )
            name = command[command.index("get") + 2]
            kind = kind_by_resource.get(resource)
            workload = self.live_workloads.get((kind or "", namespace, name))
            if workload is not None:
                payload: dict[str, Any] = dict(workload)
                payload.setdefault("kind", kind)
                payload.setdefault("metadata", {})
                if isinstance(payload["metadata"], dict):
                    payload["metadata"].setdefault("name", name)
                    payload["metadata"].setdefault("namespace", namespace)
                return SoperatorMigrationCommandResult(command, 0, json.dumps(payload), "")
        if command[:5] == (
            "kubectl",
            "--context",
            "external-context",
            "get",
            "nodes",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": self.live_nodes}),
                "",
            )
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": self.live_pods}),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pvc",
            "-o",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": self.live_pvcs}),
                "",
            )
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "slurmcluster",
        ):
            name = command[7]
            for item in self.live_slurmclusters:
                metadata = item.get("metadata")
                if isinstance(metadata, dict) and metadata.get("name") == name:
                    payload = json.loads(json.dumps(item))
                    payload.setdefault("spec", {}).setdefault("populateJail", {}).setdefault(
                        "image",
                        "registry.example/soperator/populate-jail:4.0.1-ps.1",
                    )
                    payload.setdefault("status", {"phase": "Available"})
                    return SoperatorMigrationCommandResult(command, 0, json.dumps(payload), "")
            return SoperatorMigrationCommandResult(command, 1, "", "NotFound")
        if (
            command[:7]
            == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "get",
                "job",
            )
            and command[-2:] == ("-o", "json")
        ):
            stored = self.live_jobs.get(("soperator", command[7]))
            if stored is not None:
                return SoperatorMigrationCommandResult(command, 0, json.dumps(stored), "")
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "metadata": {"name": command[7], "namespace": "soperator"},
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [
                                        {
                                            "name": "populate-jail",
                                            "image": (
                                                "registry.example/soperator/"
                                                "populate-jail:4.0.1-ps.1"
                                            ),
                                        }
                                    ]
                                }
                            }
                        },
                        "status": {
                            "succeeded": 1,
                            "conditions": [{"type": "Complete", "status": "True"}],
                        },
                    }
                ),
                "",
            )
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "nodeset",
        ):
            name = command[7]
            if self.live_nodesets is None:
                payload = {
                    "metadata": {"name": name, "namespace": "soperator"},
                    "spec": {"replicas": 2},
                    "status": {"phase": "Ready", "replicas": 2},
                }
                return SoperatorMigrationCommandResult(command, 0, json.dumps(payload), "")
            item = self.live_nodesets.get(name)
            if item is not None:
                return SoperatorMigrationCommandResult(command, 0, json.dumps(item), "")
            return SoperatorMigrationCommandResult(command, 1, "", "NotFound")
        if command[:8] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "flux-system",
            "get",
            "helmreleases",
            "-o",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": self.live_flux_helmreleases}),
                "",
            )
        if (
            len(command) >= 13
            and command[:6]
            == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "exec",
            )
            and command[7:10] == ("-c", "slurmd", "--")
            and command[10:12] == ("/bin/sh", "-ceu")
        ):
            return SoperatorMigrationCommandResult(command, 0, "/home sfs-home fuse.sfs\n", "")
        if (
            len(command) >= 13
            and command[:6]
            == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "exec",
            )
            and command[7:12] == ("-c", "slurmd", "--", "slurmd", "-C")
            and command[12] == "--parameters=l3cache_as_socket"
        ):
            pod_name = command[6]
            for nodeset_name, topology in self.worker_topology_by_nodeset.items():
                if not pod_name.startswith(f"{nodeset_name}-"):
                    continue
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    (
                        f"NodeName={pod_name} CPUs={topology['cpus']} Boards=1 "
                        f"SocketsPerBoard={topology['sockets']} "
                        f"CoresPerSocket={topology['cores_per_socket']} "
                        f"ThreadsPerCore={topology['threads_per_core']} "
                        "Parameters=l3cache_as_socket Gres=gpu:nvidia_h100_80gb_hbm3:8\n"
                    ),
                    "",
                )
        if (
            len(command) >= 12
            and command[:6]
            == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "exec",
            )
            and command[7:11] == ("-c", "slurmd", "--", "lscpu")
            and command[11] == "-J"
        ):
            pod_name = command[6]
            for nodeset_name, topology in self.worker_topology_by_nodeset.items():
                if not pod_name.startswith(f"{nodeset_name}-"):
                    continue
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    json.dumps(
                        {
                            "lscpu": [
                                {"field": "CPU(s):", "data": str(topology["cpus"])},
                                {
                                    "field": "Socket(s):",
                                    "data": str(topology["sockets"]),
                                },
                                {
                                    "field": "Core(s) per socket:",
                                    "data": str(topology["cores_per_socket"]),
                                },
                                {
                                    "field": "Thread(s) per core:",
                                    "data": str(topology["threads_per_core"]),
                                },
                            ]
                        }
                    ),
                    "",
                )
            return SoperatorMigrationCommandResult(command, 1, "", "topology unavailable")
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "flux-system",
            "patch",
            "helmrelease",
        ):
            name = command[7]
            for item in self.live_flux_helmreleases:
                metadata = item.get("metadata")
                if isinstance(metadata, dict) and metadata.get("name") == name:
                    spec = item.setdefault("spec", {})
                    if not isinstance(spec, dict):
                        spec = {}
                        item["spec"] = spec
                    spec["suspend"] = True
            return SoperatorMigrationCommandResult(command, 0, "{}", "")
        if command[:8] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            items = []
            for item in self.live_slurmclusters:
                payload = json.loads(json.dumps(item))
                payload.setdefault("status", {"phase": "Available"})
                items.append(payload)
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": items}),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8] == "sinfo" and command[-1] == "%N %T":
                if "-N" in command:
                    return SoperatorMigrationCommandResult(
                        command,
                        0,
                        "\n".join(
                            (
                                "worker-gpu-0 drained",
                                "worker-gpu-1 drained",
                                "worker-cpu-0 idle",
                                "worker-cpu-1 idle",
                            )
                        ),
                        "",
                    )
                if len(command) > 11:
                    return SoperatorMigrationCommandResult(
                        command,
                        0,
                        "worker-gpu-[0-1] drained\nworker-cpu-[0-1] idle\n",
                        "",
                    )
                return SoperatorMigrationCommandResult(command, 0, "idle\nallocated\n", "")
            if command[8:] == ("scontrol", "show", "nodes"):
                node_names = list(
                    str(item.get("metadata", {}).get("name", "") or "")
                    for item in self.live_nodes
                    if isinstance(item.get("metadata"), dict)
                )
                for fallback_node in ("node-a", "node-b", "cpu-node-a", "worker-0", "worker-gpu-0"):
                    if fallback_node not in node_names:
                        node_names.append(fallback_node)
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    "\n".join(
                        f"NodeName={node} NodeHostName={node} Partitions=main"
                        for node in node_names
                        if node
                    ),
                    "",
                )
            if command[8:11] == ("scontrol", "show", "node") and command[-1] == "-o":
                nodes = command[11].split(",") if len(command) > 11 else []
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    "\n".join(f"NodeName={node} Partitions=main State=IDLE" for node in nodes),
                    "",
                )
            if command[8:10] == ("squeue", "-h"):
                if "PENDING" in command:
                    return SoperatorMigrationCommandResult(command, 0, "", "")
                queue_output = (
                    self.slurm_queue_outputs.pop(0)
                    if self.slurm_queue_outputs
                    else self.slurm_queue_output
                )
                return SoperatorMigrationCommandResult(
                    command,
                    self.slurm_queue_returncode,
                    queue_output,
                    "" if self.slurm_queue_returncode == 0 else "squeue failed",
                )
            if command[8:10] == ("bash", "-lc") and "cxcli-soperator-smoke" in command[10]:
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-0\n",
                    "",
                )
            if command[8:10] == ("/bin/sh", "-ceu"):
                return SoperatorMigrationCommandResult(command, 0, "/home sfs-home fuse.sfs\n", "")
            if command[8:10] == ("sh", "-ceu") and "ss -Htn" in command[10]:
                return SoperatorMigrationCommandResult(command, 0, "0\n", "")
            return SoperatorMigrationCommandResult(command, 0, "ok\n", "")
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters,nodesets",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "\n".join(self.slurm_resource_names) + "\n",
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "nodesets",
            "-o",
        ):
            if self.live_nodesets is None:
                items = [
                    {
                        "metadata": {"name": "worker", "namespace": "soperator"},
                        "spec": {"replicas": 2},
                        "status": {"phase": "Ready", "replicas": 2},
                    }
                ]
            else:
                items = list(self.live_nodesets.values())
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": items}),
                "",
            )
        return SoperatorMigrationCommandResult(command, 0, "{}", "")


def _target_node_group(role: str, *, missing_role_label: bool = False) -> dict[str, Any]:
    labels: dict[str, str] = {} if missing_role_label else {"slurm.nebius.ai/nodeset-name": role}
    if role != "worker":
        labels["nebius.com/gpu"] = "false"
    filesystems_by_role = {
        "system": ["jail"],
        "controller": ["jail", "controller-spool"],
        "login": ["jail"],
        "accounting": ["jail", "accounting"],
        "worker": ["jail"],
    }
    taints: list[dict[str, str]] = []
    if role in {"controller", "login", "accounting"}:
        taints.append(
            {
                "key": "slurm.nebius.ai/nodeset-name",
                "value": role,
                "effect": "NO_SCHEDULE",
            }
        )
    if role == "worker":
        taints.append({"key": "nvidia.com/gpu", "value": "true", "effect": "NO_SCHEDULE"})
    return {
        "metadata": {
            "id": f"nodegroup-external-cluster-{role}",
            "name": f"external-cluster-{role}",
            "parent_id": "cluster-123",
        },
        "spec": {
            "version": "1.31",
            "fixed_node_count": 2 if role == "worker" else 1,
            "template": {
                "metadata": {"labels": labels},
                "resources": {"platform": "cpu-d3", "preset": "4vcpu-16gb"},
                "boot_disk": {"type": "network_ssd", "size_gibibytes": "93"},
                "os": "ubuntu24.04",
                "network_interfaces": [{"subnet_id": "subnet-123"}],
                "filesystems": [
                    {
                        "attach_mode": "READ_WRITE",
                        "mount_tag": key,
                        "existing_filesystem": {"id": f"filesystem-external-cluster-{key}"},
                    }
                    for key in filesystems_by_role[role]
                ],
                "taints": taints,
            },
        },
    }


def _legacy_service_node_group(
    role: str,
    *,
    fixed_node_count: int = 1,
    preset: str = "4vcpu-16gb",
) -> dict[str, Any]:
    labels = {
        "slurm.nebius.ai/nodeset": role,
        "slurm.nebius.ai/workload": "cpu",
    }
    taints: list[dict[str, str]] = []
    if role in {"controller", "login", "accounting"}:
        taints.append(
            {
                "key": "slurm.nebius.ai/nodeset",
                "value": role,
                "effect": "NO_SCHEDULE",
            }
        )
    return {
        "metadata": {
            "id": f"nodegroup-{role}",
            "name": role,
            "parent_id": "cluster-123",
            "labels": labels,
        },
        "spec": {
            "fixed_node_count": fixed_node_count,
            "template": {
                "metadata": {"labels": dict(labels)},
                "resources": {"platform": "cpu-d3", "preset": preset},
                "filesystems": [],
                "taints": taints,
            },
        },
        "status": _ready_node_group_status(nodes=fixed_node_count),
    }


def _snapshot_with_compute_source() -> dict[str, Any]:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups["cpu-pool"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "cpu-pool",
            "nebius.com/node-group-id": "nodegroup-cpu-pool",
        },
        "nodes": ["cpu-node-a"],
    }
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "soperator", "namespace": "soperator"},
        }
    ]
    return snapshot


def test_execute_writes_checkpoint_and_stops_before_mutation(tmp_path: Path) -> None:
    payload = _payload()
    source_report = _source_report()
    calls: list[str] = []

    def _collector(*, kube_context: str):
        calls.append(kube_context)
        return _snapshot()

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        snapshot_collector=_collector,
    )

    assert calls == ["external-context"]
    assert result.mutation_performed is False
    assert result.completed_phases == ("discovery-and-plan",)
    assert result.pending_phase == "customer-approval"
    assert "customer approval is required before mutating phases" in result.pending_reason
    assert (
        "Upgrade status: waiting for customer approval; no upgrade mutation was performed."
        in result.lines
    )
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["completed_phases"] == ["discovery-and-plan"]
    assert checkpoint["pending_phase"] == "customer-approval"
    assert checkpoint["events"][0]["event"] == "execute-preflight-completed"


def test_execute_requires_backup_metadata_before_approved_mutation(tmp_path: Path) -> None:
    runner = _FakeCommandRunner()

    with pytest.raises(RuntimeError, match="requires restore-capable backup metadata"):
        _execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=runner,
        )

    commands = [call[0] for call in runner.calls]
    assert not any(
        command[:4] == ("nebius", "compute", "filesystem", "create") for command in commands
    )
    assert not any(command[:4] == ("nebius", "mk8s", "cluster", "update") for command in commands)
    assert not any(
        command[:4] == ("helm", "--kube-context", "external-context", "upgrade")
        for command in commands
    )


def test_execute_rejects_sparse_backup_metadata_before_approved_mutation(
    tmp_path: Path,
) -> None:
    sparse_backup = {
        "path": "backups/sparse.tar.gz",
        "sha256": "sparse-sha256",
        "manifest_sha256": "sparse-manifest-sha256",
    }

    with pytest.raises(RuntimeError, match="backup metadata is sparse"):
        _execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            backup_metadata=sparse_backup,
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=_FakeCommandRunner(),
        )


def test_resume_backup_metadata_rejects_archive_sha_mismatch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    archive_path = tmp_path / "backups" / "ext-soperator-upgrade.tar.gz"
    archive_path.parent.mkdir()
    archive_path.write_text("backup archive v1\n", encoding="utf-8")
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "completed_phases": ["target-gpu-stack-remediation"],
                "pending_phase": "external-node-template-upgrade",
                "backup": {
                    "path": str(archive_path),
                    "sha256": "0" * 64,
                    "manifest_sha256": "manifest-sha",
                    "included_categories": sorted(
                        migration._EXTERNAL_UPGRADE_BACKUP_REQUIRED_CATEGORIES  # noqa: SLF001
                    ),
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="archive SHA256 does not match"):
        migration.external_soperator_upgrade_resume_backup_metadata(
            config_path,
            "external-cluster",
        )


def test_resume_backup_metadata_uses_size_fast_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    archive_path = tmp_path / "backups" / "ext-soperator-upgrade.tar.gz"
    archive_path.parent.mkdir()
    archive_bytes = b"backup archive v1\n"
    archive_path.write_bytes(archive_bytes)
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "completed_phases": ["target-gpu-stack-remediation"],
                "pending_phase": "external-node-template-upgrade",
                "backup": {
                    "path": str(archive_path),
                    "size_bytes": len(archive_bytes),
                    "sha256": "not-read-on-size-match",
                    "manifest_sha256": "manifest-sha",
                    "included_categories": sorted(
                        migration._EXTERNAL_UPGRADE_BACKUP_REQUIRED_CATEGORIES  # noqa: SLF001
                    ),
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    def _fail_sha(_path: Path) -> str:
        raise AssertionError("full archive hash should not run when size metadata matches")

    monkeypatch.setattr(migration, "_external_upgrade_file_sha256", _fail_sha)

    metadata = migration.external_soperator_upgrade_resume_backup_metadata(
        config_path,
        "external-cluster",
    )

    assert metadata is not None
    assert metadata["size_bytes"] == len(archive_bytes)


def test_resume_backup_metadata_rejects_archive_size_mismatch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    archive_path = tmp_path / "backups" / "ext-soperator-upgrade.tar.gz"
    archive_path.parent.mkdir()
    archive_path.write_text("backup archive v1\n", encoding="utf-8")
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "completed_phases": ["target-gpu-stack-remediation"],
                "pending_phase": "external-node-template-upgrade",
                "backup": {
                    "path": str(archive_path),
                    "size_bytes": archive_path.stat().st_size + 1,
                    "sha256": "not-used",
                    "manifest_sha256": "manifest-sha",
                    "included_categories": sorted(
                        migration._EXTERNAL_UPGRADE_BACKUP_REQUIRED_CATEGORIES  # noqa: SLF001
                    ),
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="archive size does not match"):
        migration.external_soperator_upgrade_resume_backup_metadata(
            config_path,
            "external-cluster",
        )


def test_write_checkpoint_preserves_existing_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    existing = {
        "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
        "status": "old",
    }
    migration._write_checkpoint(checkpoint_path, existing)
    before = checkpoint_path.read_text(encoding="utf-8")

    def _fail_replace(_source: object, _target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(migration.os, "replace", _fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        migration._write_checkpoint(
            checkpoint_path,
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "status": "new",
            },
        )

    assert checkpoint_path.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob(".checkpoint.json.*.tmp"))


def test_write_checkpoint_uses_default_text_file_mode_for_new_file(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text("baseline\n", encoding="utf-8")
    checkpoint_path = tmp_path / "checkpoint.json"

    migration._write_checkpoint(
        checkpoint_path,
        {
            "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
            "status": "new",
        },
    )

    assert checkpoint_path.stat().st_mode & 0o777 == baseline_path.stat().st_mode & 0o777


def test_write_checkpoint_preserves_existing_file_mode(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text("{}\n", encoding="utf-8")
    checkpoint_path.chmod(0o640)

    migration._write_checkpoint(
        checkpoint_path,
        {
            "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
            "status": "new",
        },
    )

    assert checkpoint_path.stat().st_mode & 0o777 == 0o640


def _write_minimal_migrate_report(config_path: Path) -> Path:
    return migration._write_soperator_migrate_report(
        config_path=config_path,
        checkpoint_path=config_path.parent / ".nebius-cxcli" / "checkpoint.json",
        checkpoint={"phase_state": {}, "events": []},
        phase_ids=("discovery-and-plan",),
        completed_phases=set(),
        target_ref="external-cluster",
        source_version="3.0.5",
        target_version="4.0.2-ps.1",
        pending_phase="customer-approval",
        pending_reason="approval required",
        mutation_performed=False,
    )


def test_write_migrate_report_uses_default_text_file_mode_for_new_file(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text("baseline\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"

    report_path = _write_minimal_migrate_report(config_path)

    assert report_path.stat().st_mode & 0o777 == baseline_path.stat().st_mode & 0o777


def test_write_migrate_report_preserves_existing_file_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    report_path = tmp_path / "generated" / "reports" / "ext-soperator-upgrade-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Existing Migration Report\n", encoding="utf-8")
    report_path.chmod(0o640)

    written_path = _write_minimal_migrate_report(config_path)

    assert written_path == report_path
    assert report_path.stat().st_mode & 0o777 == 0o640


def test_write_migrate_report_preserves_existing_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    report_path = tmp_path / "generated" / "reports" / "ext-soperator-upgrade-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Existing Migration Report\n", encoding="utf-8")
    before = report_path.read_text(encoding="utf-8")

    def _fail_replace(_source: object, _target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(migration.os, "replace", _fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        _write_minimal_migrate_report(config_path)

    assert report_path.read_text(encoding="utf-8") == before
    assert not list(report_path.parent.glob(".ext-soperator-upgrade-report.md.*.tmp"))


def test_execute_quota_preflight_blocks_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_with_compute_source()
    runner = _FakeCommandRunner()

    def _insufficient(**kwargs: object) -> QuotaReport:
        return QuotaReport(
            tenant_id=str(kwargs.get("tenant_id", "")),
            project_id=str(kwargs.get("project_id", "")),
            region_id=str(kwargs.get("region_id", "")),
            checked_at="2026-01-01T00:00:00+00:00",
            checks=(
                QuotaCheck(
                    component_id="soperator-migration",
                    instance_id="external-cluster",
                    component_label="External Soperator upgrade target external-cluster",
                    quota_name="compute.filesystem.count",
                    region="eu-north1",
                    required=3,
                    reason="aligned SFS filesystems",
                    unit="1",
                    available=0,
                    sufficient=False,
                    tenant_limit=0,
                    tenant_usage=0,
                    project_limit=0,
                    project_usage=0,
                    source_scope="quota-allowance",
                    description="filesystem count",
                ),
            ),
        )

    monkeypatch.setattr(migration, "assess_live_quota_requirements", _insufficient)

    with pytest.raises(RuntimeError, match="quota preflight failed before any cluster mutation"):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(include_placements=True),
            source_report=_source_report(snapshot),
            snapshot_collector=lambda *, kube_context: snapshot,
            approved=True,
            command_runner=runner,
        )

    commands = [call[0] for call in runner.calls]
    assert not any(
        command[:4] == ("nebius", "compute", "filesystem", "create") for command in commands
    )
    assert not any(
        command[:4] == ("nebius", "mk8s", "node-group", "create") for command in commands
    )


def test_quota_preflight_does_not_count_preserved_worker_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_with_compute_source()
    recorded_inputs: list[dict[str, Any]] = []

    def _record_quota_inputs(**kwargs: object):
        inputs = kwargs.get("inputs")
        assert isinstance(inputs, dict)
        recorded_inputs.append(inputs)
        return (), ()

    monkeypatch.setattr(migration, "estimate_mk8s_quota_requirements", _record_quota_inputs)

    runner = _FakeCommandRunner()
    requirements, gaps, lines = migration._new_target_node_group_quota_requirements(
        payload=_payload(include_placements=True),
        target_ref="external-cluster",
        source_report=_source_report(snapshot),
        worker_node_groups=("gpu-pool",),
        nebius_api=runner.nebius_api,
    )

    assert requirements == []
    assert gaps == []
    assert recorded_inputs
    planned_groups = recorded_inputs[0]["node_groups"]
    assert isinstance(planned_groups, dict)
    assert set(planned_groups) == {
        "external-cluster-system",
        "external-cluster-controller",
        "external-cluster-login",
        "external-cluster-accounting",
    }
    assert "gpu-pool" not in planned_groups
    assert "worker" not in " ".join(planned_groups)
    assert (
        "Quota preflight worker node groups: compute migration preserves existing "
        "worker groups in place (gpu-pool); external node-template safe-surge spare "
        "capacity is checked separately when that action is selected."
    ) in lines


def test_safe_surge_quota_preflight_counts_service_and_worker_active_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_with_compute_source()
    payload = _payload(include_placements=True)
    onboarding = payload["deploy"]["targets"][0]["soperator_onboarding"]  # type: ignore[index]
    assert isinstance(onboarding, dict)
    rollout = migration.resolve_external_node_template_rollout(
        onboarding,
        strategy="safe-surge",
    )
    recorded_inputs: list[dict[str, Any]] = []

    def _record_quota_inputs(**kwargs: object):
        inputs = kwargs.get("inputs")
        assert isinstance(inputs, dict)
        recorded_inputs.append(inputs)
        return (), ()

    monkeypatch.setattr(migration, "estimate_mk8s_quota_requirements", _record_quota_inputs)

    runner = _FakeCommandRunner()
    requirements, gaps, lines = migration._safe_surge_node_group_quota_requirements(
        payload=payload,
        target_ref="external-cluster",
        source_report=_source_report(snapshot),
        worker_node_groups=("gpu-pool",),
        nebius_api=runner.nebius_api,
        rollout=rollout,
    )

    assert requirements == []
    assert gaps == []
    assert len(recorded_inputs) == 2
    service_planned_groups = recorded_inputs[0]["node_groups"]
    assert isinstance(service_planned_groups, dict)
    assert set(service_planned_groups) == {"external-cluster-cpu-pool-safe-surge"}
    assert service_planned_groups["external-cluster-cpu-pool-safe-surge"]["node_count"] == 1
    worker_planned_groups = recorded_inputs[1]["node_groups"]
    assert isinstance(worker_planned_groups, dict)
    assert set(worker_planned_groups) == {"external-cluster-gpu-pool-safe-surge"}
    assert worker_planned_groups["external-cluster-gpu-pool-safe-surge"]["node_count"] == 1
    assert any("safe-surge service group cpu-pool" in line for line in lines)
    assert any("safe-surge worker wave 1" in line for line in lines)
    assert any("requires 1 temporary surge node(s) per group" in line for line in lines)


def test_execute_worker_safe_surge_quota_shortage_blocks_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    onboarding = payload["deploy"]["targets"][0]["soperator_onboarding"]  # type: ignore[index]
    assert isinstance(onboarding, dict)
    onboarding["storage_mode"] = "keep-existing-storage"
    onboarding["compute_mode"] = "keep-existing-compute"
    onboarding["actions"] = ["upgrade-external-node-template"]
    onboarding["node_template_upgrade"] = {
        "rollout": {
            "strategy": "safe-surge",
            "worker_group_strategy": {
                "max_surge_count": 1,
                "max_unavailable_count": 0,
                "drain_timeout": "30m",
            },
        }
    }
    runner = _FakeCommandRunner()
    recorded_inputs: list[dict[str, Any]] = []

    def _estimate_safe_surge(**kwargs: object):
        if kwargs.get("context") != "external soperator upgrade safe-surge quota preflight":
            return (), ()
        inputs = kwargs.get("inputs")
        assert isinstance(inputs, dict)
        recorded_inputs.append(inputs)
        return (
            QuotaRequirement(
                component_id="mk8s",
                instance_id="external-cluster-soperator-safe-surge-worker-wave-1",
                component_label="external safe-surge",
                quota_name="compute.instance.count",
                region="eu-north1",
                required=1,
                reason="safe-surge active group",
            ),
        ), ()

    def _insufficient_safe_surge(**kwargs: object) -> QuotaReport:
        requirements = tuple(kwargs.get("requirements") or ())
        assert any(
            isinstance(item, QuotaRequirement) and item.reason == "safe-surge active group"
            for item in requirements
        )
        return QuotaReport(
            tenant_id=str(kwargs.get("tenant_id", "")),
            project_id=str(kwargs.get("project_id", "")),
            region_id=str(kwargs.get("region_id", "")),
            checked_at="2026-01-01T00:00:00+00:00",
            checks=(
                QuotaCheck(
                    component_id="mk8s",
                    instance_id="external-cluster-soperator-safe-surge-worker-wave-1",
                    component_label="external safe-surge",
                    quota_name="compute.instance.count",
                    region="eu-north1",
                    required=1,
                    reason="safe-surge active group",
                    unit="1",
                    available=0,
                    sufficient=False,
                    tenant_limit=0,
                    tenant_usage=0,
                    project_limit=0,
                    project_usage=0,
                    source_scope="quota-allowance",
                    description="Compute instance count",
                ),
            ),
        )

    monkeypatch.setattr(migration, "estimate_mk8s_quota_requirements", _estimate_safe_surge)
    monkeypatch.setattr(
        migration,
        "assess_live_quota_requirements",
        _insufficient_safe_surge,
    )

    with pytest.raises(RuntimeError, match="quota preflight failed before any cluster mutation"):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=payload,
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=runner,
        )

    assert recorded_inputs
    planned_groups = recorded_inputs[0]["node_groups"]
    assert isinstance(planned_groups, dict)
    assert set(planned_groups) == {"external-cluster-gpu-pool-safe-surge"}
    commands = [call[0] for call in runner.calls]
    assert not any(command[:4] == ("nebius", "mk8s", "cluster", "update") for command in commands)
    assert not any(
        command[:4] == ("nebius", "mk8s", "node-group", "update") for command in commands
    )


def test_external_node_template_rollout_config_validation_and_cli_precedence() -> None:
    onboarding = _payload()["deploy"]["targets"][0]["soperator_onboarding"]  # type: ignore[index]
    assert isinstance(onboarding, dict)

    default_rollout = migration.resolve_external_node_template_rollout(onboarding)
    assert default_rollout.to_manifest_dict() == {
        "strategy": "zero-surge",
        "service_role_strategy": "safe-surge",
        "service_role_group_strategy": {
            "max_surge_count": 1,
            "max_unavailable_count": 0,
            "drain_timeout": "30m",
        },
        "worker_group_strategy": {
            "max_surge_count": 0,
            "max_unavailable_count": 1,
            "drain_timeout": "30m",
        },
    }

    onboarding["node_template_upgrade"] = {
        "rollout": {
            "strategy": "zero-surge",
            "worker_wave_groups": 2,
        }
    }
    with pytest.raises(ValueError, match="zero-surge worker rollout does not use"):
        migration.resolve_external_node_template_rollout(onboarding)

    override = migration.resolve_external_node_template_rollout(
        onboarding,
        strategy="safe-surge",
        worker_wave_percent=5,
        max_parallel_worker_groups=3,
        strategy_max_surge_count=2,
        strategy_max_unavailable_count=1,
        strategy_drain_timeout="45m",
    )
    assert override.to_manifest_dict() == {
        "strategy": "safe-surge",
        "service_role_strategy": "safe-surge",
        "worker_wave_percent": 5,
        "max_parallel_worker_groups": 3,
        "service_role_group_strategy": {
            "max_surge_count": 1,
            "max_unavailable_count": 0,
            "drain_timeout": "30m",
        },
        "worker_group_strategy": {
            "max_surge_count": 2,
            "max_unavailable_count": 1,
            "drain_timeout": "45m",
        },
    }

    with pytest.raises(ValueError, match="mutually exclusive"):
        migration.resolve_external_node_template_rollout(
            onboarding,
            worker_wave_groups=1,
            worker_wave_percent=1,
        )

    with pytest.raises(ValueError, match="only supported with worker_wave_percent"):
        migration.resolve_external_node_template_rollout(
            onboarding,
            strategy="safe-surge",
            worker_wave_groups=1,
            max_parallel_worker_groups=1,
        )

    onboarding["node_template_upgrade"] = {
        "rollout": {
            "strategy": "safe-surge",
            "worker_wave_percent": 10,
            "max_parallel_worker_groups": 2,
        }
    }
    fixed_override = migration.resolve_external_node_template_rollout(
        onboarding,
        worker_wave_groups=1,
    )
    assert fixed_override.to_manifest_dict() == {
        "strategy": "safe-surge",
        "service_role_strategy": "safe-surge",
        "worker_wave_groups": 1,
        "service_role_group_strategy": {
            "max_surge_count": 1,
            "max_unavailable_count": 0,
            "drain_timeout": "30m",
        },
        "worker_group_strategy": {
            "max_surge_count": 1,
            "max_unavailable_count": 0,
            "drain_timeout": "30m",
        },
    }

    onboarding["node_template_upgrade"] = {
        "rollout": {
            "worker_wave_groups": 1,
            "worker_wave_percent": 1,
        }
    }
    with pytest.raises(ValueError, match="must set only one"):
        migration.resolve_external_node_template_rollout(onboarding)

    onboarding["node_template_upgrade"] = {
        "rollout": {
            "strategy": "safe-surge",
            "worker_wave_groups": 1,
            "max_parallel_worker_groups": 1,
        }
    }
    with pytest.raises(ValueError, match="only supported with worker_wave_percent"):
        migration.resolve_external_node_template_rollout(onboarding)

    onboarding["node_template_upgrade"] = {
        "rollout": {
            "max_global_unavailable_worker_percent": 1,
        }
    }
    with pytest.raises(ValueError, match="unsupported"):
        migration.resolve_external_node_template_rollout(onboarding)

    onboarding["node_template_upgrade"] = {
        "rollout": {
            "worker_group_strategy": {
                "max_surge_count": 0,
                "max_unavailable_count": 0,
            }
        }
    }
    with pytest.raises(ValueError, match="at least one"):
        migration.resolve_external_node_template_rollout(onboarding)

    onboarding["node_template_upgrade"] = {
        "rollout": {
            "worker_group_strategy": {"drain_timeout": "30"},
        }
    }
    with pytest.raises(ValueError, match="explicit Go-style duration"):
        migration.resolve_external_node_template_rollout(onboarding)


def test_external_node_template_completion_recheck_rejects_k8s_downgrade_target() -> None:
    payload = _payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    assert isinstance(target, dict)
    onboarding = target["soperator_onboarding"]
    assert isinstance(onboarding, dict)
    onboarding["actions"] = ["upgrade-external-node-template"]
    onboarding["node_template_upgrade"] = {
        "target_k8s_version": "1.32",
        "target_os": "ubuntu24.04",
        "target_gpu_stack_preset": "cuda13.0",
    }
    runner = _FakeCommandRunner(
        cluster={
            "metadata": {"id": "cluster-123", "name": "external-cluster"},
            "spec": {"control_plane": {"version": "1.34"}},
        }
    )

    with pytest.raises(RuntimeError, match="downgrade is not supported"):
        migration._external_node_template_upgrade_satisfied(
            payload=payload,
            source_report=_source_report(),
            target_ref="external-cluster",
            worker_node_groups=(),
            nebius_api=runner.nebius_api,
            command_runner=runner,
        )


def test_external_node_template_k8s_version_checks_fail_closed_on_malformed_live_version() -> None:
    message = migration._external_node_template_k8s_downgrade_error("garbage", "1.33")

    assert "could not parse" in message
    with pytest.raises(RuntimeError, match="could not parse"):
        migration._ensure_external_node_template_k8s_not_downgrade("garbage", "1.33")
    with pytest.raises(RuntimeError, match="could not parse"):
        migration._minor_version_at_least("garbage", "1.33")


def test_execute_external_node_template_rejects_skipped_k8s_minor(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="Run the next hop first with --to-k8s-version 1.32"):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(target_k8s_version="1.34"),
            source_report=_source_report(target_k8s_version="1.34"),
            backup_metadata=_backup_metadata("skip-minor"),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=_FakeCommandRunner(),
        )


def test_sfs_attachment_uses_zero_surge_strategy_and_restores_original() -> None:
    runner = _FakeCommandRunner(
        existing_node_groups=[
            {
                "metadata": {
                    "id": "nodegroup-gpu-pool",
                    "name": "gpu-pool",
                    "parent_id": "cluster-123",
                },
                "spec": {
                    "strategy": {
                        "max_surge": {"count": 1},
                        "max_unavailable": {"count": 0},
                    },
                    "template": {"filesystems": []},
                },
            }
        ]
    )
    specs = migration._aligned_filesystem_specs(
        payload=_payload(),
        target_ref="external-cluster",
    )
    specs_by_key = {spec.key: spec for spec in specs}
    assert "jail" not in specs_by_key

    changed, attachments = migration._attach_filesystems_to_source_node_groups(
        nebius_api=runner.nebius_api,
        source_report=_source_report(),
        attachment_keys_by_group={"gpu-pool": ("controller-spool",)},
        filesystem_ids_by_key={"controller-spool": "filesystem-controller-spool"},
        specs_by_key=specs_by_key,
    )

    assert changed is True
    assert attachments == [
        {
            "source_group": "gpu-pool",
            "node_group_id": "nodegroup-gpu-pool",
            "filesystem_keys": ["controller-spool"],
            "strategy": "zero-surge",
            "strategy_restored": True,
            "updated": True,
        }
    ]
    update_calls = [
        call[0]
        for call in runner.calls
        if call[0][:5] == ("nebius", "mk8s", "node-group", "update", "nodegroup-gpu-pool")
    ]
    assert len(update_calls) == 2
    attach_update, restore_update = update_calls
    assert "--template-filesystems" in attach_update
    assert attach_update[attach_update.index("--strategy-max-surge-count") + 1] == "0"
    assert attach_update[attach_update.index("--strategy-max-unavailable-count") + 1] == "1"
    assert attach_update[attach_update.index("--strategy-drain-timeout") + 1] == "30m"
    assert attach_update[attach_update.index("--timeout") + 1] == "60m"
    assert "--template-filesystems" not in restore_update
    assert restore_update[restore_update.index("--strategy-max-surge-count") + 1] == "1"
    assert restore_update[restore_update.index("--strategy-max-unavailable-count") + 1] == "0"
    assert restore_update[restore_update.index("--strategy-drain-timeout") + 1] == "0s"


def test_external_node_template_update_clears_stale_cpu_driver_preset() -> None:
    args = migration._external_node_template_update_args(
        node_group={
            "spec": {
                "version": "1.32",
                "template": {
                    "resources": {"platform": "cpu-d3"},
                    "gpu_settings": {"drivers_preset": "cuda12.8"},
                    "os": "ubuntu24.04",
                },
            }
        },
        source_group={"gpu": False},
        target=migration.SoperatorExternalNodeTemplateTarget(
            k8s_version="1.33",
            os="ubuntu24.04",
            gpu_stack_preset="cuda13.0",
        ),
    )

    assert args == ("--version", "1.33")
    assert migration._external_node_template_clears_cpu_gpu_settings(
        node_group={
            "spec": {
                "version": "1.32",
                "template": {
                    "resources": {"platform": "cpu-d3"},
                    "gpu_settings": {"drivers_preset": "cuda12.8"},
                    "os": "ubuntu24.04",
                },
            }
        },
        source_group={"gpu": False},
    )


def test_zero_surge_sdk_update_clears_cpu_gpu_settings() -> None:
    runner = _FakeCommandRunner(
        existing_node_groups=[
            {
                "metadata": {
                    "id": "nodegroup-worker-cpu",
                    "name": "worker-cpu-0",
                    "parent_id": "cluster-123",
                },
                "spec": {
                    "version": "1.32",
                    "strategy": {
                        "max_surge": {"count": 1},
                        "max_unavailable": {"count": 0},
                    },
                    "template": {
                        "resources": {"platform": "cpu-d3", "preset": "16vcpu-64gb"},
                        "gpu_settings": {"drivers_preset": "cuda12.8"},
                        "os": "ubuntu24.04",
                    },
                },
            }
        ]
    )

    migration._update_node_group_with_zero_surge_strategy(
        nebius_api=runner.nebius_api,
        node_group_id="nodegroup-worker-cpu",
        update_args=("--version", "1.33"),
        original_node_group=runner.existing_node_groups[0],
        clear_template_gpu_settings=True,
    )

    update_calls = [
        call[0]
        for call in runner.calls
        if call[0][:5]
        == (
            "nebius",
            "mk8s",
            "node-group",
            "update",
            "nodegroup-worker-cpu",
        )
    ]
    assert update_calls
    assert "--template-gpu-settings-drivers-preset" not in update_calls[0]
    node_group = runner.existing_node_groups[0]
    spec = node_group["spec"]
    assert isinstance(spec, dict)
    assert spec["version"] == "1.33"
    assert spec["strategy"]["drain_timeout"] == "0s"
    template = spec["template"]
    assert isinstance(template, dict)
    assert "gpu_settings" not in template
    assert "gpuSettings" not in template


def test_execute_records_approval_and_runs_checkpointed_mutators(tmp_path: Path) -> None:
    snapshot = _snapshot()
    source_report = _source_report(snapshot)
    runner = _FakeCommandRunner()
    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        backup_metadata=_backup_metadata(),
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )

    assert result.mutation_performed is True
    assert result.completed_phases == (
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "create-aligned-sfs",
        "online-bulk-data-sync",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "populate-jail-refresh",
        "validation-and-rollback-hold",
        "retire-old-resources",
        "post-upgrade-mk8s-check",
        "post-upgrade-helm-check",
    )
    assert result.pending_phase == "none"
    assert "Auto-selected source worker node groups: gpu-pool" in "\n".join(result.lines)
    assert "target-gpu-stack-remediation: Applied target GPU stack chart" in "\n".join(result.lines)
    assert "post-upgrade-helm-check: Verified target Soperator Helm chart readiness" in "\n".join(
        result.lines
    )
    assert "Phase validation post-upgrade-helm-check: PASS" in "\n".join(result.lines)
    assert "Data sync skipped: no old Soperator storage was detected" in "\n".join(result.lines)
    assert any(
        call[0][:4] == ("nebius", "compute", "filesystem", "create") for call in runner.calls
    )
    assert any(call[0][:4] == ("nebius", "mk8s", "node-group", "update") for call in runner.calls)
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["completed_phases"] == list(result.completed_phases)
    assert checkpoint["pending_phase"] == "none"
    assert checkpoint["worker_node_groups"] == ["gpu-pool"]
    assert "customer-approval-recorded" in {event["event"] for event in checkpoint["events"]}
    assert set(checkpoint["phase_state"]["create-aligned-sfs"]["filesystems"]) == {
        "controller-spool",
        "accounting",
    }
    assert {
        item["id"] for item in checkpoint["phase_state"]["target-gpu-stack-remediation"]["charts"]
    } == {"nvidia-gpu-operator", "nvidia-network-operator"}
    gpu_stack_helm_upgrades = [
        call
        for call in runner.calls
        if call[0][:5] == ("helm", "--kube-context", "external-context", "upgrade", "--install")
        and call[0][5] in {"gpu-operator", "network-operator"}
    ]
    assert gpu_stack_helm_upgrades
    assert all("--force-conflicts" in call[0] for call in gpu_stack_helm_upgrades)
    for phase_id in result.completed_phases:
        if phase_id in {"discovery-and-plan", "customer-approval"}:
            continue
        verification = checkpoint["phase_state"][phase_id]["fast_verification"]
        assert verification["status"] in {"passed", "skipped"}
        if verification["status"] == "passed":
            assert verification["passed"] is True
        else:
            assert verification["passed"] is None
        assert verification["verified_at"]
    assert "execute-phase-fast-verification-passed" in {
        event["event"] for event in checkpoint["events"]
    }
    reports_dir = tmp_path / "generated" / "reports"
    migrate_report = (reports_dir / "ext-soperator-upgrade-report.md").read_text(encoding="utf-8")
    upgrade_json_report = json.loads(
        (reports_dir / "ext-soperator-upgrade-report.json").read_text(encoding="utf-8")
    )
    assert "## Stage Fast Verification" in migrate_report
    assert "`target-gpu-stack-remediation`: `PASS`" in migrate_report
    stage_verification = {
        item["phase_id"]: item for item in upgrade_json_report["stage_verification"]
    }
    assert stage_verification["external-node-template-upgrade"]["status"] == "passed"
    assert stage_verification["target-gpu-stack-remediation"]["passed"] is True
    assert stage_verification["online-bulk-data-sync"]["status"] == "skipped"
    assert stage_verification["retire-old-resources"]["status"] == "skipped"
    assert stage_verification["post-upgrade-mk8s-check"]["status"] == "passed"
    assert stage_verification["post-upgrade-helm-check"]["status"] == "passed"
    phase_reports = {item["id"]: item for item in upgrade_json_report["phases"]}
    assert phase_reports["external-node-template-upgrade"]["top_level_stage"] == (
        "MK8s Node Upgrades"
    )
    assert phase_reports["post-upgrade-mk8s-check"]["top_level_stage"] == ("MK8s Node Upgrades")
    assert phase_reports["post-upgrade-helm-check"]["top_level_stage"] == "Soperator Upgrade"
    assert phase_reports["populate-jail-refresh"]["top_level_stage"] == "Jail Upgrade"
    assert phase_reports["target-gpu-stack-remediation"]["fast_verification"]["status"] == "passed"
    assert phase_reports["post-upgrade-helm-check"]["fast_verification"]["status"] == "passed"
    assert "- Top-level stage: `MK8s Node Upgrades`" in migrate_report
    assert "- Top-level stage: `Soperator Upgrade`" in migrate_report
    assert "- Top-level stage: `Jail Upgrade`" in migrate_report
    populate_phase = checkpoint["phase_state"]["populate-jail-refresh"]
    assert populate_phase["slurm_job_scope_nodes"]
    assert populate_phase["slurm_quiet_at"]
    assert populate_phase["slurm_resumed_at"]

    def _helm_input_values(index: int) -> dict[str, Any]:
        raw = runner.calls[index][1]
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    refresh_indices = [
        index
        for index, (command, _input_text) in enumerate(runner.calls)
        if command
        and command[0] == "helm"
        and "upgrade" in command
        and _helm_input_values(index).get("jailRootfs", {}).get("refresh", {}).get("mode")
        == "populatePassiveSlot"
    ]
    assert refresh_indices
    refresh_index = refresh_indices[-1]
    assert populate_phase["active_slot"] == "slot-b"
    assert populate_phase["rollback_slot"] == "slot-a"
    drain_indices = [
        index
        for index, (command, _input_text) in enumerate(runner.calls)
        if command[8:] == ("scontrol", "update", "PartitionName=ALL", "State=DRAIN")
    ]
    resume_indices = [
        index
        for index, (command, _input_text) in enumerate(runner.calls)
        if command[8:] == ("scontrol", "update", "PartitionName=ALL", "State=UP")
    ]
    last_drain_before_refresh = max(
        index for index in drain_indices if index < refresh_index
    )
    last_resume_before_refresh = max(
        (index for index in resume_indices if index < refresh_index),
        default=-1,
    )
    assert last_drain_before_refresh > last_resume_before_refresh
    assert any(index > refresh_index for index in resume_indices)

    runner.calls.clear()
    resumed = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )

    assert resumed.completed_phases == result.completed_phases
    assert resumed.pending_phase == "none"
    mutating_commands = [
        call[0]
        for call in runner.calls
        if call[0][:4]
        in {
            ("helm", "--kube-context", "external-context", "upgrade"),
            ("nebius", "compute", "filesystem", "create"),
            ("nebius", "mk8s", "node-group", "update"),
            ("kubectl", "--context", "external-context", "apply"),
        }
    ]
    assert mutating_commands == []
    assert "live state already satisfies completed action" in "\n".join(resumed.lines)
    assert "post-upgrade-helm-check: Verified target Soperator Helm chart readiness" in "\n".join(
        resumed.lines
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["completed_phases"] == list(result.completed_phases)
    assert checkpoint["pending_phase"] == "none"
    assert checkpoint["worker_node_groups"] == ["gpu-pool"]


def test_execute_fast_verification_failure_keeps_same_phase_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    source_report = _source_report(snapshot)
    monkeypatch.setattr(
        migration,
        "_target_gpu_stack_remediation_satisfied",
        lambda **_kwargs: False,
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        backup_metadata=_backup_metadata("fast-verification-pending"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
        worker_rollout_strategy="safe-surge",
    )

    assert result.pending_phase == "target-gpu-stack-remediation"
    assert "fast stage verification failed after target-gpu-stack-remediation" in (
        result.pending_reason
    )
    assert result.mutation_performed is True
    assert "target-gpu-stack-remediation" not in result.completed_phases
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == "target-gpu-stack-remediation"
    verification = checkpoint["phase_state"]["target-gpu-stack-remediation"]["fast_verification"]
    assert verification["status"] == "failed"
    assert verification["passed"] is False
    assert (
        "target GPU stack Helm releases or post-render patches are not ready"
        in (verification["summary"])
    )
    assert (
        checkpoint["phase_state"]["external-node-template-upgrade"]["fast_verification"]["status"]
        == "passed"
    )
    assert "execute-phase-fast-verification-failed" in {
        event["event"] for event in checkpoint["events"]
    }
    reports_dir = tmp_path / "generated" / "reports"
    migrate_report = (reports_dir / "ext-soperator-upgrade-report.md").read_text(encoding="utf-8")
    upgrade_json_report = json.loads(
        (reports_dir / "ext-soperator-upgrade-report.json").read_text(encoding="utf-8")
    )
    assert "`target-gpu-stack-remediation`: `FAIL`" in migrate_report
    assert upgrade_json_report["pending_phase"] == "target-gpu-stack-remediation"
    stage_verification = {
        item["phase_id"]: item for item in upgrade_json_report["stage_verification"]
    }
    assert stage_verification["target-gpu-stack-remediation"]["status"] == "failed"
    assert stage_verification["rolling-compute-migration"]["status"] == "not_run"


@pytest.mark.parametrize(
    ("phase_id", "verifier_name", "next_phase_id"),
    [
        (
            "online-bulk-data-sync",
            "_online_bulk_data_sync_fast_verification_checks",
            "rolling-compute-migration",
        ),
        (
            "rolling-compute-migration",
            "_rolling_compute_fast_verification_checks",
            "final-control-plane-cutover",
        ),
        (
            "validation-and-rollback-hold",
            "_validation_hold_fast_verification_checks",
            "retire-old-resources",
        ),
        (
            "retire-old-resources",
            "_retire_old_resources_fast_verification_checks",
            "post-upgrade-mk8s-check",
        ),
    ],
)
def test_execute_phase_fast_verification_failure_keeps_same_phase_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase_id: str,
    verifier_name: str,
    next_phase_id: str,
) -> None:
    summary = f"{phase_id} verification evidence was not satisfied."

    def _fail_phase_verification(**_kwargs: Any) -> tuple[str, list[Mapping[str, str]]]:
        return (
            summary,
            [
                migration._fast_verification_check(  # noqa: SLF001
                    phase_id,
                    "failed",
                    "phase proof missing",
                )
            ],
        )

    monkeypatch.setattr(migration, verifier_name, _fail_phase_verification)

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(_snapshot()),
        backup_metadata=_backup_metadata(f"{phase_id}-pending"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
        worker_rollout_strategy="safe-surge",
    )

    assert result.pending_phase == phase_id
    assert f"fast stage verification failed after {phase_id}" in result.pending_reason
    assert phase_id not in result.completed_phases
    result_text = "\n".join(result.lines)
    assert f"Phase validation {phase_id}: FAIL - {summary}" in result_text

    reports_dir = tmp_path / "generated" / "reports"
    migrate_report = (reports_dir / "ext-soperator-upgrade-report.md").read_text(
        encoding="utf-8"
    )
    upgrade_json_report = json.loads(
        (reports_dir / "ext-soperator-upgrade-report.json").read_text(encoding="utf-8")
    )
    assert upgrade_json_report["pending_phase"] == phase_id
    assert phase_id in upgrade_json_report["pending_reason"]
    assert f"`{phase_id}`: `FAIL`" in migrate_report
    assert f"`{next_phase_id}`: `PENDING`" in migrate_report
    stage_verification = {
        item["phase_id"]: item for item in upgrade_json_report["stage_verification"]
    }
    verification = stage_verification[phase_id]
    assert verification["status"] == "failed"
    assert verification["passed"] is False
    assert verification["verified_at"]
    assert verification["checks"][0]["summary"] == "phase proof missing"
    assert stage_verification[next_phase_id]["status"] == "not_run"


def test_execute_runtime_error_keeps_current_phase_pending_and_resumes_same_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    source_report = _source_report(snapshot)
    runner = _FakeCommandRunner()
    original_handler = migration._execute_target_gpu_stack_remediation_phase
    handler_calls = 0

    def _fail_once_target_gpu_stack(**kwargs: Any) -> tuple[bool, list[str]]:
        nonlocal handler_calls
        handler_calls += 1
        if handler_calls == 1:
            checkpoint = kwargs["checkpoint"]
            assert isinstance(checkpoint, dict)
            phase = checkpoint.setdefault("phase_state", {}).setdefault(
                migration._TARGET_GPU_STACK_PHASE_ID,
                {},
            )
            phase["attempt_recorded_before_failure"] = True
            writer = kwargs.get("checkpoint_writer")
            if callable(writer):
                writer()
            raise RuntimeError("target GPU stack failed mid-phase")
        return original_handler(**kwargs)

    monkeypatch.setattr(
        migration,
        "_execute_target_gpu_stack_remediation_phase",
        _fail_once_target_gpu_stack,
    )

    with pytest.raises(RuntimeError, match="target GPU stack failed mid-phase"):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=source_report,
            backup_metadata=_backup_metadata("runtime-error-pending"),
            snapshot_collector=lambda *, kube_context: snapshot,
            approved=True,
            command_runner=runner,
            worker_rollout_strategy="safe-surge",
        )

    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == migration._TARGET_GPU_STACK_PHASE_ID
    assert checkpoint["pending_reason"] == "target GPU stack failed mid-phase"
    assert checkpoint["completed_phases"] == [
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
    ]
    assert migration._TARGET_GPU_STACK_PHASE_ID not in checkpoint["completed_phases"]
    assert checkpoint["phase_state"][migration._TARGET_GPU_STACK_PHASE_ID][
        "attempt_recorded_before_failure"
    ] is True
    assert "execute-phase-failed" in {event["event"] for event in checkpoint["events"]}
    assert (tmp_path / "generated" / "reports" / "ext-soperator-upgrade-report.md").exists()

    runner.calls.clear()
    resumed = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )

    assert handler_calls == 2
    assert resumed.pending_phase == "none"
    assert migration._TARGET_GPU_STACK_PHASE_ID in resumed.completed_phases
    assert any(
        call[0][:4] == ("helm", "--kube-context", "external-context", "upgrade")
        for call in runner.calls
    )


def test_execute_populate_jail_failure_keeps_checkpointed_phase_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    source_report = _source_report(snapshot)
    runner = _FakeCommandRunner()
    locked_upgrade_path = _locked_upgrade_path_fixture()
    segment_id = locked_upgrade_path["segments"][0]["id"]
    original_handler = migration._execute_populate_jail_refresh_phase
    handler_calls = 0

    def _fail_once_populate_jail_refresh(**kwargs: Any) -> tuple[bool, list[str]]:
        nonlocal handler_calls
        handler_calls += 1
        if handler_calls == 1:
            checkpoint = kwargs["checkpoint"]
            assert isinstance(checkpoint, dict)
            phase = checkpoint.setdefault("phase_state", {}).setdefault(
                migration.POPULATE_JAIL_REFRESH_PHASE_ID,
                {},
            )
            phase["attempt_recorded_before_failure"] = True
            writer = kwargs.get("checkpoint_writer")
            if callable(writer):
                writer()
            raise RuntimeError("populate jail refresh failed mid-phase")
        return original_handler(**kwargs)

    monkeypatch.setattr(
        migration,
        "_execute_populate_jail_refresh_phase",
        _fail_once_populate_jail_refresh,
    )

    with pytest.raises(RuntimeError, match="populate jail refresh failed mid-phase"):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=source_report,
            backup_metadata=_backup_metadata("populate-jail-pending"),
            snapshot_collector=lambda *, kube_context: snapshot,
            approved=True,
            command_runner=runner,
            worker_rollout_strategy="safe-surge",
            locked_upgrade_path=locked_upgrade_path,
            upgrade_path_fingerprint="locked-path-fingerprint",
            upgrade_path_segment_id=segment_id,
        )

    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == migration.POPULATE_JAIL_REFRESH_PHASE_ID
    assert checkpoint["pending_reason"] == "populate jail refresh failed mid-phase"
    assert checkpoint["completed_phases"] == [
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "create-aligned-sfs",
        "online-bulk-data-sync",
        "rolling-compute-migration",
        "final-control-plane-cutover",
    ]
    assert migration.POPULATE_JAIL_REFRESH_PHASE_ID not in checkpoint["completed_phases"]
    assert migration.POPULATE_JAIL_REFRESH_PHASE_ID in checkpoint["planned_phases"]
    assert checkpoint["segment_state"][segment_id]["planned_phases"] == checkpoint[
        "planned_phases"
    ]
    assert checkpoint["phase_state"][migration.POPULATE_JAIL_REFRESH_PHASE_ID][
        "attempt_recorded_before_failure"
    ] is True
    assert any(
        event["event"] == "execute-phase-failed"
        and event.get("phase") == migration.POPULATE_JAIL_REFRESH_PHASE_ID
        for event in checkpoint["events"]
    )
    upgrade_json_report = json.loads(
        (
            tmp_path / "generated" / "reports" / "ext-soperator-upgrade-report.json"
        ).read_text(encoding="utf-8")
    )
    assert upgrade_json_report["pending_phase"] == migration.POPULATE_JAIL_REFRESH_PHASE_ID

    runner.calls.clear()
    resumed = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
        locked_upgrade_path=locked_upgrade_path,
        upgrade_path_fingerprint="locked-path-fingerprint",
        upgrade_path_segment_id=segment_id,
    )

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert handler_calls == 2
    assert resumed.pending_phase == "none"
    assert migration.POPULATE_JAIL_REFRESH_PHASE_ID in resumed.completed_phases
    assert checkpoint["pending_phase"] == "none"
    assert checkpoint["segment_state"][segment_id]["planned_phases"] == checkpoint[
        "planned_phases"
    ]


def test_execute_keyboard_interrupt_keeps_current_phase_pending_and_resumes_same_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    source_report = _source_report(snapshot)
    runner = _FakeCommandRunner()
    original_handler = migration._execute_target_gpu_stack_remediation_phase
    handler_calls = 0

    def _interrupt_once_target_gpu_stack(**kwargs: Any) -> tuple[bool, list[str]]:
        nonlocal handler_calls
        handler_calls += 1
        if handler_calls == 1:
            checkpoint = kwargs["checkpoint"]
            assert isinstance(checkpoint, dict)
            phase = checkpoint.setdefault("phase_state", {}).setdefault(
                migration._TARGET_GPU_STACK_PHASE_ID,
                {},
            )
            phase["attempt_recorded_before_interrupt"] = True
            writer = kwargs.get("checkpoint_writer")
            if callable(writer):
                writer()
            raise KeyboardInterrupt
        return original_handler(**kwargs)

    monkeypatch.setattr(
        migration,
        "_execute_target_gpu_stack_remediation_phase",
        _interrupt_once_target_gpu_stack,
    )

    with pytest.raises(KeyboardInterrupt):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=source_report,
            backup_metadata=_backup_metadata("keyboard-interrupt-pending"),
            snapshot_collector=lambda *, kube_context: snapshot,
            approved=True,
            command_runner=runner,
            worker_rollout_strategy="safe-surge",
        )

    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == migration._TARGET_GPU_STACK_PHASE_ID
    assert checkpoint["pending_reason"] == "interrupted by user"
    assert checkpoint["completed_phases"] == [
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
    ]
    assert checkpoint["phase_state"][migration._TARGET_GPU_STACK_PHASE_ID][
        "attempt_recorded_before_interrupt"
    ] is True
    assert "execute-interrupted" in {event["event"] for event in checkpoint["events"]}

    runner.calls.clear()
    resumed = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )

    assert handler_calls == 2
    assert resumed.pending_phase == "none"
    assert migration._TARGET_GPU_STACK_PHASE_ID in resumed.completed_phases


def test_execute_final_helm_check_failure_writes_pending_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_report = _source_report(_snapshot())
    original_helm_verifier = migration._verify_completed_soperator_migration_helm_state
    verifier_calls = 0

    def _fail_final_helm_check(**kwargs: Any) -> list[str]:
        nonlocal verifier_calls
        verifier_calls += 1
        if verifier_calls >= 2:
            raise RuntimeError("target Helm release is not ready")
        return original_helm_verifier(**kwargs)

    monkeypatch.setattr(
        migration,
        "_verify_completed_soperator_migration_helm_state",
        _fail_final_helm_check,
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        backup_metadata=_backup_metadata("final-helm-pending"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
        worker_rollout_strategy="safe-surge",
    )

    assert result.pending_phase == "post-upgrade-helm-check"
    assert result.pending_reason == "target Helm release is not ready"
    assert "Phase validation post-upgrade-helm-check: FAIL - target Helm release is not ready" in (
        "\n".join(result.lines)
    )
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == "post-upgrade-helm-check"
    assert (
        checkpoint["phase_state"]["post-upgrade-mk8s-check"]["fast_verification"]["status"]
        == "passed"
    )
    helm_verification = checkpoint["phase_state"]["post-upgrade-helm-check"]["fast_verification"]
    assert helm_verification["status"] == "failed"
    assert helm_verification["passed"] is False
    assert "execute-phase-fast-verification-failed" in {
        event["event"] for event in checkpoint["events"]
    }
    reports_dir = tmp_path / "generated" / "reports"
    upgrade_json_report = json.loads(
        (reports_dir / "ext-soperator-upgrade-report.json").read_text(encoding="utf-8")
    )
    stage_verification = {
        item["phase_id"]: item for item in upgrade_json_report["stage_verification"]
    }
    assert stage_verification["post-upgrade-mk8s-check"]["status"] == "passed"
    assert stage_verification["post-upgrade-helm-check"]["status"] == "failed"
    assert upgrade_json_report["pending_phase"] == "post-upgrade-helm-check"
    migrate_report = (reports_dir / "ext-soperator-upgrade-report.md").read_text(encoding="utf-8")
    assert "`post-upgrade-helm-check`: `FAIL`" in migrate_report


def test_execute_skips_target_helm_readiness_for_adopted_external_node_upgrade(
    tmp_path: Path,
) -> None:
    payload = _payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]
    assert isinstance(onboarding, dict)
    onboarding["storage_mode"] = "keep-existing-storage"
    onboarding["compute_mode"] = "keep-existing-compute"
    onboarding["actions"] = [
        "adopt-soperator",
        "reconcile-target-gpu-stack",
        "upgrade-external-node-template",
        "approve-external-soperator-upgrade",
    ]
    onboarding["source_version"] = "4.0.1"
    onboarding["target_version"] = "4.0.1"
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(
        payload,
        target_ref="external-cluster",
    )
    source_snapshot = _snapshot(version="4.0.1")
    source_report = _source_report(source_snapshot)
    report = source_report["report"]
    assert isinstance(report, dict)
    report["migration_plan"] = [
        {"id": "discovery-and-plan", "status": "complete"},
        {
            "id": "customer-approval",
            "status": "planned",
            "requires_customer_approval": True,
        },
        {"id": migration._EXTERNAL_NODE_TEMPLATE_PHASE_ID, "status": "planned"},
        {"id": migration._TARGET_GPU_STACK_PHASE_ID, "status": "planned"},
    ]
    runner = _FakeCommandRunner(helm_releases=[])

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        backup_metadata=_backup_metadata("adopted-external-node-upgrade"),
        snapshot_collector=lambda *, kube_context: source_snapshot,
        approved=True,
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )

    assert result.pending_phase == "none"
    assert result.completed_phases == (
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "post-upgrade-mk8s-check",
        "post-upgrade-helm-check",
    )
    output = "\n".join(result.lines)
    assert (
        "post-upgrade-helm-check: Skipped target Soperator Helm chart readiness: "
        "no target Soperator Helm release is installed or expected"
    ) in output
    assert not any(
        call[0][:6]
        == ("helm", "--kube-context", "external-context", "get", "manifest", "soperator")
        for call in runner.calls
    )
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    helm_verification = checkpoint["phase_state"]["post-upgrade-helm-check"][
        "fast_verification"
    ]
    assert helm_verification["status"] == "passed"
    assert "Skipped target Soperator Helm chart readiness" in helm_verification["summary"]


def test_execute_final_helm_check_keyboard_interrupt_writes_pending_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_report = _source_report(_snapshot())
    original_helm_verifier = migration._verify_completed_soperator_migration_helm_state
    verifier_calls = 0

    def _interrupt_final_helm_check(**kwargs: Any) -> list[str]:
        nonlocal verifier_calls
        verifier_calls += 1
        if verifier_calls >= 2:
            raise KeyboardInterrupt
        return original_helm_verifier(**kwargs)

    monkeypatch.setattr(
        migration,
        "_verify_completed_soperator_migration_helm_state",
        _interrupt_final_helm_check,
    )

    with pytest.raises(KeyboardInterrupt):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=source_report,
            backup_metadata=_backup_metadata("final-helm-interrupted"),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=_FakeCommandRunner(),
            worker_rollout_strategy="safe-surge",
        )

    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == "post-upgrade-helm-check"
    assert checkpoint["pending_reason"] == "interrupted by user"
    assert "post-upgrade-helm-check" not in checkpoint["completed_phases"]
    assert "execute-interrupted" in {event["event"] for event in checkpoint["events"]}
    reports_dir = tmp_path / "generated" / "reports"
    upgrade_json_report = json.loads(
        (reports_dir / "ext-soperator-upgrade-report.json").read_text(encoding="utf-8")
    )
    assert upgrade_json_report["pending_phase"] == "post-upgrade-helm-check"
    assert upgrade_json_report["pending_reason"] == "interrupted by user"


def test_execute_final_mk8s_check_failure_writes_pending_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_report = _source_report(_snapshot())
    original_mk8s_verifier = migration._verify_completed_soperator_migration_mk8s_state
    verifier_calls = 0
    helm_calls = 0

    def _fail_final_mk8s_check(**kwargs: Any) -> list[str]:
        nonlocal verifier_calls
        verifier_calls += 1
        if verifier_calls >= 2:
            raise RuntimeError("target MK8s node-template state is not ready")
        return original_mk8s_verifier(**kwargs)

    def _record_helm_check(**_kwargs: Any) -> list[str]:
        nonlocal helm_calls
        helm_calls += 1
        return ["Verified target Soperator Helm chart readiness."]

    monkeypatch.setattr(
        migration,
        "_verify_completed_soperator_migration_mk8s_state",
        _fail_final_mk8s_check,
    )
    monkeypatch.setattr(
        migration,
        "_verify_completed_soperator_migration_helm_state",
        _record_helm_check,
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        backup_metadata=_backup_metadata("final-mk8s-pending"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
        worker_rollout_strategy="safe-surge",
    )

    assert result.pending_phase == "post-upgrade-mk8s-check"
    assert result.pending_reason == "target MK8s node-template state is not ready"
    assert (
        "Phase validation post-upgrade-mk8s-check: FAIL - "
        "target MK8s node-template state is not ready"
    ) in "\n".join(result.lines)
    assert helm_calls == 1
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == "post-upgrade-mk8s-check"
    mk8s_verification = checkpoint["phase_state"]["post-upgrade-mk8s-check"]["fast_verification"]
    assert mk8s_verification["status"] == "failed"
    assert mk8s_verification["passed"] is False
    assert "post-upgrade-helm-check" not in checkpoint["phase_state"]
    reports_dir = tmp_path / "generated" / "reports"
    upgrade_json_report = json.loads(
        (reports_dir / "ext-soperator-upgrade-report.json").read_text(encoding="utf-8")
    )
    stage_verification = {
        item["phase_id"]: item for item in upgrade_json_report["stage_verification"]
    }
    assert stage_verification["post-upgrade-mk8s-check"]["status"] == "failed"
    assert stage_verification["post-upgrade-helm-check"]["status"] == "not_run"
    assert upgrade_json_report["pending_phase"] == "post-upgrade-mk8s-check"
    migrate_report = (reports_dir / "ext-soperator-upgrade-report.md").read_text(encoding="utf-8")
    assert "`post-upgrade-mk8s-check`: `FAIL`" in migrate_report
    assert "`post-upgrade-helm-check`: `PENDING`" in migrate_report


def test_execute_preserves_original_backup_metadata_after_mutating_resume(
    tmp_path: Path,
) -> None:
    source_report = _source_report()
    config_path = tmp_path / "config.yaml"
    original_backup = _backup_metadata_with_archive(config_path, "original-pre-upgrade")
    replacement_backup = _backup_metadata("partial-state")
    execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        backup_metadata=original_backup,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
        worker_rollout_strategy="safe-surge",
    )

    result = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        backup_metadata=replacement_backup,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        command_runner=_FakeCommandRunner(),
        worker_rollout_strategy="safe-surge",
    )

    assert result.pending_phase == "none"
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["backup"] == original_backup
    assert any(event["event"] == "backup-metadata-preserved" for event in checkpoint["events"])
    upgrade_json_report = json.loads(
        (tmp_path / "generated" / "reports" / "ext-soperator-upgrade-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert upgrade_json_report["backup"] == original_backup


def test_execute_uses_checkpoint_phase_plan_after_mutating_resume_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    source_report = _source_report()
    report = source_report["report"]
    assert isinstance(report, dict)
    report["migration_plan"] = [
        {"id": "discovery-and-plan", "status": "complete"},
        {
            "id": "customer-approval",
            "status": "planned",
            "requires_customer_approval": True,
        },
    ]
    planned_phases = (
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        migration.POPULATE_JAIL_REFRESH_PHASE_ID,
        "validation-and-rollback-hold",
        "retire-old-resources",
        "post-upgrade-mk8s-check",
        "post-upgrade-helm-check",
    )
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "source_report_fingerprint": "pre-mutation-source-report",
                "source_version": "3.0.5",
                "target_version": "4.0.1-ps.1",
                "planned_phases": list(planned_phases),
                "completed_phases": [
                    "discovery-and-plan",
                    "customer-approval",
                    "external-node-template-upgrade",
                    "target-gpu-stack-remediation",
                    "rolling-compute-migration",
                ],
                "pending_phase": "final-control-plane-cutover",
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, tuple[str, ...]] = {}

    def _capture_checkpoint_for_run(**kwargs: Any) -> dict[str, Any]:
        captured["phase_ids"] = tuple(kwargs["phase_ids"])
        raise RuntimeError("captured checkpoint phase plan")

    monkeypatch.setattr(migration, "_checkpoint_for_run", _capture_checkpoint_for_run)

    with pytest.raises(RuntimeError, match="captured checkpoint phase plan"):
        execute_soperator_migration(
            config_path=config_path,
            target_ref="external-cluster",
            payload=_payload(),
            source_report=source_report,
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=_FakeCommandRunner(),
            worker_rollout_strategy="safe-surge",
        )

    assert captured["phase_ids"] == planned_phases


def test_execute_uses_fresh_backup_after_completed_prior_hop(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "source_report_fingerprint": "previous-hop",
                "source_version": "3.0.5",
                "target_version": "4.0.1-ps.1",
                "pending_phase": "none",
                "planned_phases": [
                    "discovery-and-plan",
                    "customer-approval",
                    "external-node-template-upgrade",
                ],
                "completed_phases": [
                    "discovery-and-plan",
                    "customer-approval",
                    "external-node-template-upgrade",
                ],
                "backup": _backup_metadata("previous-hop"),
            }
        ),
        encoding="utf-8",
    )
    fresh_backup = _backup_metadata("fresh-hop")

    result = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(),
        backup_metadata=fresh_backup,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
        worker_rollout_strategy="safe-surge",
    )

    assert result.pending_phase == "none"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["backup"] == _backup_metadata_with_existing_archive(
        config_path,
        fresh_backup,
    )
    assert checkpoint["source_report_fingerprint"] != "previous-hop"


def test_execute_clears_stale_pending_phase_after_successful_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    source_report = _source_report(snapshot)
    runner = _FakeCommandRunner()
    validation_calls = 0
    retire_pending_observations: list[tuple[str, str]] = []

    def _flaky_validation_hold(**kwargs: Any) -> tuple[bool, list[str]]:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            raise migration.SoperatorMigrationPhasePending(
                "Soperator cluster smoke validation failed during validation-and-rollback-hold: "
                "Slurm NCCL benchmark failed"
            )
        checkpoint = kwargs["checkpoint"]
        assert isinstance(checkpoint, dict)
        phase = checkpoint.setdefault("phase_state", {}).setdefault(
            "validation-and-rollback-hold",
            {},
        )
        phase["validation_contract_revision"] = migration._VALIDATION_HOLD_REVISION
        phase["shared_safety_verification"] = {
            "status": "passed",
            "passed": True,
            "checks": [],
        }
        phase["soperator_cluster_validation_count"] = 1
        phase["soperator_cluster_validation_reports"] = [
            "generated/reports/deploy-smoke-report-external-cluster.json"
        ]
        phase["mk8s_gpu_validation_count"] = 0
        phase["mk8s_gpu_validation_reports"] = []
        return True, ["validation recovered"]

    def _observing_retire(**kwargs: Any) -> tuple[bool, list[str]]:
        checkpoint = kwargs["checkpoint"]
        assert isinstance(checkpoint, dict)
        phase = checkpoint.setdefault("phase_state", {}).setdefault("retire-old-resources", {})
        phase["skipped_reason"] = "test retire skipped"
        retire_pending_observations.append(
            (
                str(checkpoint.get("pending_phase", "") or ""),
                str(checkpoint.get("pending_reason", "") or ""),
            )
        )
        return False, ["retire skipped"]

    monkeypatch.setattr(migration, "_execute_validation_hold_phase", _flaky_validation_hold)
    monkeypatch.setattr(migration, "_execute_retire_old_resources_phase", _observing_retire)

    first = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        backup_metadata=_backup_metadata("validation-pending"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )

    assert first.pending_phase == "validation-and-rollback-hold"
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == "validation-and-rollback-hold"
    assert "Slurm NCCL benchmark failed" in checkpoint["pending_reason"]

    resumed = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )

    assert resumed.pending_phase == "none"
    assert retire_pending_observations == [("retire-old-resources", "")]
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == "none"
    assert checkpoint["pending_reason"] == ""
    assert any(
        event.get("event") == "execute-pending-cleared"
        and event.get("phase") == "validation-and-rollback-hold"
        for event in checkpoint["events"]
    )


def test_validation_hold_fast_verification_skips_absent_soperator_smoke() -> None:
    summary, checks = migration._validation_hold_fast_verification_checks(  # noqa: SLF001
        checkpoint={
            "phase_state": {
                "validation-and-rollback-hold": {
                    "validation_contract_revision": migration._VALIDATION_HOLD_REVISION,
                    "shared_safety_verification": {
                        "status": "passed",
                        "passed": True,
                        "checks": [],
                    },
                    "soperator_cluster_validation_count": 0,
                    "soperator_cluster_validation_reports": [],
                    "mk8s_gpu_validation_count": 0,
                    "mk8s_gpu_validation_reports": [],
                }
            }
        }
    )

    statuses = {check["name"]: check["status"] for check in checks}
    assert summary == "validation hold artifacts and shared safety result are recorded."
    assert statuses["Soperator deployment snapshot report"] == "skipped"
    assert statuses["Configured MK8s GPU validation reports"] == "skipped"


def test_execute_retires_stale_source_soperator_helm_releases_after_target_ready(
    tmp_path: Path,
) -> None:
    source_report = _source_report()
    runner = _FakeCommandRunner(
        helm_releases=[
            {
                "name": "soperator",
                "namespace": "soperator",
                "chart": "soperator-4.0.1-ps.1",
                "app_version": "4.0.1",
                "status": "deployed",
            },
            {
                "name": "soperator-controller",
                "namespace": "soperator-system",
                "chart": "helm-soperator-1.23.3",
                "app_version": "1.23.3",
                "status": "deployed",
            },
            {
                "name": "slurm-cluster-storage",
                "namespace": "soperator",
                "chart": "helm-slurm-cluster-storage-1.23.3",
                "app_version": "1.23.3",
                "status": "deployed",
            },
        ],
        live_flux_helmreleases=[
            {
                "metadata": {
                    "name": "soperator-fluxcd",
                    "namespace": "flux-system",
                    "labels": {
                        "kustomize.toolkit.fluxcd.io/name": "flux-system",
                        "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
                    },
                },
                "spec": {
                    "chart": {"spec": {"chart": "helm-soperator-fluxcd", "version": "1.23.3"}},
                    "targetNamespace": "flux-system",
                },
                "status": {
                    "history": [
                        {
                            "name": "flux-system-soperator-fluxcd",
                            "namespace": "flux-system",
                            "chartName": "helm-soperator-fluxcd",
                            "chartVersion": "1.23.3",
                            "appVersion": "1.23.3",
                        }
                    ]
                },
            },
            {
                "metadata": {
                    "name": "flux-system-soperator-fluxcd-soperator",
                    "namespace": "flux-system",
                },
                "spec": {
                    "releaseName": "soperator-controller",
                    "targetNamespace": "soperator-system",
                },
            },
            {
                "metadata": {
                    "name": "flux-system-soperator-fluxcd-slurm-cluster-storage",
                    "namespace": "flux-system",
                },
                "spec": {
                    "releaseName": "slurm-cluster-storage",
                    "targetNamespace": "soperator",
                },
            },
        ],
        live_kubernetes_resources={
            "deployment": [
                {
                    "metadata": {
                        "name": "soperator-controller",
                        "namespace": "soperator-system",
                        "annotations": {"meta.helm.sh/release-name": "soperator-controller"},
                    }
                }
            ],
            "service": [
                {
                    "metadata": {
                        "name": "soperator-controller",
                        "namespace": "soperator-system",
                        "annotations": {"meta.helm.sh/release-name": "soperator-controller"},
                    }
                }
            ],
            "persistentvolumeclaim": [
                {
                    "metadata": {
                        "name": "jail-pvc",
                        "namespace": "soperator",
                        "annotations": {"meta.helm.sh/release-name": "slurm-cluster-storage"},
                    }
                }
            ],
            "nodeset": [
                {
                    "metadata": {
                        "name": "worker",
                        "namespace": "soperator",
                        "annotations": {"meta.helm.sh/release-name": "soperator-nodesets"},
                    }
                }
            ],
        },
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )

    output = "\n".join(result.lines)
    assert result.pending_phase == "none"
    assert "post-upgrade-mk8s-check: External MK8s" in output
    assert "post-upgrade-helm-check: Verified target Soperator Helm chart readiness" in output
    assert "Retired stale source Soperator Helm release records" in output
    assert "soperator-system/soperator-controller" in output
    assert "soperator/slurm-cluster-storage" in output
    assert "Suspended old Flux HelmRelease desired state" in output
    assert "flux-system/soperator-fluxcd" in output
    assert "Deleted old Flux HelmRelease desired state records" in output
    assert "Suspended old Flux Kustomization desired state: flux-system/flux-system." in output
    assert "Pruned 2 old source operational resource(s)." in output
    assert (
        "Preserved shared/storage/custom resources for source release(s): slurm-cluster-storage."
        in (output)
    )
    assert "Deleted 2 stale Helm storage record(s)." in output
    helm_list_calls = [
        call[0] for call in runner.calls if call[0] and call[0][0] == "helm" and "list" in call[0]
    ]
    assert helm_list_calls
    assert all("-A" not in call for call in helm_list_calls)
    assert {call[call.index("-n") + 1] for call in helm_list_calls if "-n" in call} >= {
        "soperator",
        "soperator-system",
        "flux-system",
    }

    remaining_release_names = {
        str(release.get("name", "") or "") for release in runner.helm_releases
    }
    assert "soperator-controller" not in remaining_release_names
    assert "slurm-cluster-storage" not in remaining_release_names
    assert runner.live_kubernetes_resources["deployment"] == []
    assert runner.live_kubernetes_resources["service"] == []
    assert runner.live_kubernetes_resources["persistentvolumeclaim"]
    assert runner.live_kubernetes_resources["nodeset"]
    assert runner.live_flux_helmreleases == []
    assert any(
        call[0][:7]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "flux-system",
            "patch",
            "kustomization.kustomize.toolkit.fluxcd.io",
        )
        for call in runner.calls
    )
    deleted_resources = [
        call[0]
        for call in runner.calls
        if call[0][:3] == ("kubectl", "--context", "external-context") and "delete" in call[0]
    ]
    assert not any("persistentvolumeclaim" in command for command in deleted_resources)
    assert not any("nodeset" in command for command in deleted_resources)
    source_cleanup_deletes = [
        command for command in deleted_resources if "deployment" in command or "service" in command
    ]
    assert source_cleanup_deletes
    assert all("--wait=false" in command for command in source_cleanup_deletes)
    assert not any("--wait=true" in command for command in source_cleanup_deletes)


def test_completed_helm_state_retires_same_name_stale_source_before_readiness() -> None:
    runner = _FakeCommandRunner(
        helm_releases=[
            {
                "name": "soperator",
                "namespace": "soperator",
                "chart": "soperator-4.0.1-ps.1",
                "app_version": "4.0.1",
                "status": "deployed",
                "revision": "11",
            },
            {
                "name": "soperator",
                "namespace": "soperator",
                "chart": "helm-slurm-cluster-2.0.5",
                "app_version": "2.0.5",
                "status": "deployed",
                "revision": "1",
            },
        ]
    )

    lines = migration._verify_completed_soperator_migration_helm_state(
        command_runner=runner,
        kube_context="external-context",
        target_version="4.0.1-ps.1",
    )

    assert any("Retired stale source Soperator Helm release records" in line for line in lines)
    assert any("Deleted 1 stale Helm storage record(s)." in line for line in lines)
    assert [
        {
            "name": release.get("name"),
            "chart": release.get("chart"),
            "revision": release.get("revision"),
        }
        for release in runner.helm_releases
    ] == [
        {
            "name": "soperator",
            "chart": "soperator-4.0.1-ps.1",
            "revision": "11",
        }
    ]
    assert all(
        str(((secret.get("metadata") or {}).get("labels") or {}).get("version", "")) != "1"
        for secret in runner.helm_storage_secrets
    )


def test_completed_helm_state_deletes_suspended_source_flux_records() -> None:
    runner = _FakeCommandRunner(
        helm_releases=[
            {
                "name": "soperator",
                "namespace": "soperator",
                "chart": "soperator-4.0.1-ps.1",
                "app_version": "4.0.1",
                "status": "deployed",
                "revision": "11",
            },
            {
                "name": "flux-system-soperator-fluxcd-mariadb-operator-crds",
                "namespace": "flux-system",
                "chart": "mariadb-operator-crds-25.10.2",
                "app_version": "0.0.0",
                "status": "deployed",
                "revision": "1",
            },
            {
                "name": "flux-system-soperator-fluxcd-ns",
                "namespace": "flux-system",
                "chart": "raw-2.0.0",
                "app_version": "1.0.0",
                "status": "deployed",
                "revision": "1",
            },
            {
                "name": "soperator-fluxcd-values",
                "namespace": "flux-system",
                "chart": "raw-2.0.0",
                "app_version": "1.0.0",
                "status": "deployed",
                "revision": "1",
            },
            {
                "name": "flux-system-soperator-fluxcd-soperator",
                "namespace": "flux-system",
                "chart": "helm-soperator-4.0.1",
                "app_version": "4.0.1",
                "status": "deployed",
                "revision": "1",
            },
            {
                "name": "soperator-kruise",
                "namespace": "soperator",
                "chart": "kruise-1.8.0",
                "app_version": "1.8.0",
                "status": "deployed",
                "revision": "1",
            },
        ],
        live_flux_helmreleases=[
            {
                "metadata": {
                    "name": "flux-system-soperator-fluxcd-slurm-cluster",
                    "namespace": "flux-system",
                },
                "spec": {
                    "suspend": True,
                    "releaseName": "soperator",
                    "targetNamespace": "soperator",
                    "chart": {"spec": {"chart": "helm-slurm-cluster", "version": "2.0.5"}},
                },
            },
            {
                "metadata": {
                    "name": "flux-system-soperator-fluxcd-mariadb-operator-crds",
                    "namespace": "flux-system",
                },
                "spec": {
                    "suspend": True,
                    "chart": {"spec": {"chart": "mariadb-operator-crds", "version": "25.10.2"}},
                },
            },
            {
                "metadata": {
                    "name": "soperator-fluxcd-values",
                    "namespace": "flux-system",
                },
                "spec": {
                    "suspend": True,
                    "chart": {"spec": {"chart": "raw", "version": "2.0.0"}},
                },
            },
            {
                "metadata": {"name": "other-team", "namespace": "flux-system"},
                "spec": {
                    "suspend": True,
                    "chart": {"spec": {"chart": "unrelated", "version": "1.0.0"}},
                },
            },
        ],
    )

    lines = migration._verify_completed_soperator_migration_helm_state(
        command_runner=runner,
        kube_context="external-context",
        target_version="4.0.1-ps.1",
    )

    assert any("Retired stale source Soperator Helm release records" in line for line in lines)
    assert any("Deleted 4 stale Helm storage record(s)." in line for line in lines)
    assert any("Deleted old Flux HelmRelease desired state records" in line for line in lines)
    assert [
        (str(item["metadata"]["namespace"]), str(item["metadata"]["name"]))
        for item in runner.live_flux_helmreleases
    ] == [("flux-system", "other-team")]
    assert [
        (str(release.get("namespace")), str(release.get("name")))
        for release in runner.helm_releases
    ] == [("soperator", "soperator"), ("soperator", "soperator-kruise")]
    delete_calls = [
        call[0]
        for call in runner.calls
        if call[0][:7]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "flux-system",
            "delete",
            "helmrelease.helm.toolkit.fluxcd.io",
        )
    ]
    assert {call[7] for call in delete_calls} == {
        "flux-system-soperator-fluxcd-slurm-cluster",
        "flux-system-soperator-fluxcd-mariadb-operator-crds",
        "soperator-fluxcd-values",
    }
    patch_calls = [
        call[0]
        for call in runner.calls
        if call[0][:7]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "flux-system",
            "patch",
            "helmrelease.helm.toolkit.fluxcd.io",
        )
    ]
    assert patch_calls == []


def test_source_flux_cleanup_treats_missing_patch_targets_as_idempotent() -> None:
    flux_helmrelease = {
        "metadata": {
            "name": "flux-system-soperator-fluxcd-slurm-cluster",
            "namespace": "flux-system",
            "labels": {
                "kustomize.toolkit.fluxcd.io/name": "soperator-fluxcd",
                "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
            },
        },
        "spec": {
            "suspend": False,
            "releaseName": "soperator",
            "targetNamespace": "soperator",
            "chart": {"spec": {"chart": "helm-slurm-cluster", "version": "2.0.5"}},
        },
    }
    payload = {"items": [flux_helmrelease]}
    calls: list[tuple[tuple[str, ...], bool]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds
        command = tuple(args)
        calls.append((command, check))
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-A",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(command, 0, json.dumps(payload), "")
        if (
            len(command) >= 8
            and command[:6]
            == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "flux-system",
                "patch",
            )
            and command[6]
            in {
                "helmrelease.helm.toolkit.fluxcd.io",
                "kustomization.kustomize.toolkit.fluxcd.io",
            }
        ):
            return SoperatorMigrationCommandResult(
                command,
                1,
                "",
                'Error from server (NotFound): helmreleases.helm.toolkit.fluxcd.io "missing" not found',
            )
        if len(command) >= 8 and command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "flux-system",
            "delete",
            "helmrelease.helm.toolkit.fluxcd.io",
        ):
            return SoperatorMigrationCommandResult(command, 0, "", "")
        raise AssertionError(command)

    suspended, suspended_kustomizations = migration._suspend_source_flux_helmreleases(
        command_runner=runner,
        kube_context="external-context",
        stale_releases=(),
        target_version="4.0.1-ps.1",
    )
    deleted = migration._delete_source_flux_helmreleases(
        command_runner=runner,
        kube_context="external-context",
        stale_releases=(),
        target_version="4.0.1-ps.1",
    )

    assert suspended == ()
    assert suspended_kustomizations == ()
    assert deleted == ("flux-system/flux-system-soperator-fluxcd-slurm-cluster",)
    patch_calls = [call for call in calls if "patch" in call[0]]
    assert len(patch_calls) == 3
    assert all(check is False for _, check in patch_calls)
    delete_calls = [command for command, _check in calls if "delete" in command]
    assert len(delete_calls) == 1
    assert "--wait=false" in delete_calls[0]
    assert "--wait=true" not in delete_calls[0]


def test_profile_backed_source_selectors_cover_related_old_soperator_charts() -> None:
    assert migration._source_chart_matches_profile("helm-soperator-activechecks-2.0.5")  # noqa: SLF001
    assert migration._source_chart_matches_profile("helm-soperator-fluxcd-bootstrap-3.0.5")  # noqa: SLF001
    assert migration._source_chart_matches_profile("helm-nfs-server-4.0.1")  # noqa: SLF001
    assert migration._source_release_name_matches_profile(  # noqa: SLF001
        "flux-system-soperator-fluxcd-mariadb-operator-crds"
    )
    assert migration._source_release_name_matches_profile("soperator-fluxcd-values")  # noqa: SLF001
    assert not migration._source_chart_matches_profile("soperator-4.0.1-ps.1")  # noqa: SLF001


def test_execute_retries_completed_gpu_stack_action_when_live_release_is_missing(
    tmp_path: Path,
) -> None:
    source_report = _source_report()
    runner = _FakeCommandRunner()
    first = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        backup_metadata=_backup_metadata("gpu-stack-retry"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )
    assert first.pending_phase == "none"

    runner.helm_statuses.pop(("nvidia-gpu-operator", "gpu-operator"))
    runner.calls.clear()

    resumed = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )

    assert resumed.pending_phase == "none"
    assert "target-gpu-stack-remediation: live state no longer satisfies completed action" in (
        "\n".join(resumed.lines)
    )
    helm_upgrades = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("helm", "--kube-context", "external-context", "upgrade")
    ]
    assert [command[command.index("--install") + 1] for command in helm_upgrades] == [
        "gpu-operator",
        "network-operator",
    ]
    assert all("--no-hooks" in command for command in helm_upgrades)


def test_target_gpu_stack_remediation_applies_catalog_post_render_patches() -> None:
    payload = _payload()
    _add_external_gpu_cluster_inventory(payload)
    runner = _FakeCommandRunner()
    checkpoint: dict[str, Any] = {}

    mutated, lines = migration._execute_target_gpu_stack_remediation_phase(
        checkpoint=checkpoint,
        payload=payload,
        target_ref="external-cluster",
        kube_context="external-context",
        command_runner=runner,
    )

    assert mutated is True
    apply_calls = [
        call
        for call in runner.calls
        if call[0]
        == (
            "kubectl",
            "--context",
            "external-context",
            "apply",
            "-f",
            "-",
        )
    ]
    assert len(apply_calls) == 1
    assert "rdmaSharedDevicePlugin" in (apply_calls[0][1] or "")
    assert "network-operator-v25.7.0" in (apply_calls[0][1] or "")
    assert "Applied target GPU stack post-render patch" in "\n".join(lines)


def test_target_gpu_stack_remediation_clears_pending_helm_operation() -> None:
    payload = _payload()
    _add_external_gpu_cluster_inventory(payload)
    runner = _FakeCommandRunner(
        helm_errors_by_release={
            (
                "nvidia-network-operator",
                "network-operator",
            ): [
                "Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress"
            ]
        },
        helm_history=[
            {"revision": 1, "status": "pending-install"},
        ],
        helm_storage_secrets=[
            {
                "metadata": {
                    "namespace": "nvidia-network-operator",
                    "name": "sh.helm.release.v1.network-operator.v1",
                    "labels": {
                        "owner": "helm",
                        "name": "network-operator",
                        "status": "pending-install",
                        "version": "1",
                    },
                }
            }
        ],
    )
    checkpoint: dict[str, Any] = {}

    mutated, lines = migration._execute_target_gpu_stack_remediation_phase(
        checkpoint=checkpoint,
        payload=payload,
        target_ref="external-cluster",
        kube_context="external-context",
        command_runner=runner,
    )

    assert mutated is True
    network_operator_upgrades = [
        call
        for call in runner.calls
        if call[0][:4] == ("helm", "--kube-context", "external-context", "upgrade")
        and call[0][call[0].index("--install") + 1] == "network-operator"
    ]
    assert len(network_operator_upgrades) == 2
    assert all("--no-hooks" in call[0] for call in network_operator_upgrades)
    assert any(
        call[0][:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "nvidia-network-operator",
            "delete",
            "secret",
            "sh.helm.release.v1.network-operator.v1",
        )
        for call in runner.calls
    )
    assert "Applied target GPU stack chart: nvidia-network-operator" in "\n".join(lines)


def test_target_gpu_stack_remediation_accepts_ready_release_after_helm_timeout() -> None:
    payload = _payload()
    _add_external_gpu_cluster_inventory(payload)
    runner = _FakeCommandRunner(
        helm_timeouts_by_release={
            ("nvidia-network-operator", "network-operator"): [True],
        }
    )
    checkpoint: dict[str, Any] = {}

    mutated, lines = migration._execute_target_gpu_stack_remediation_phase(
        checkpoint=checkpoint,
        payload=payload,
        target_ref="external-cluster",
        kube_context="external-context",
        command_runner=runner,
    )

    assert mutated is True
    network_operator_upgrades = [
        call
        for call in runner.calls
        if call[0][:4] == ("helm", "--kube-context", "external-context", "upgrade")
        and call[0][call[0].index("--install") + 1] == "network-operator"
    ]
    assert len(network_operator_upgrades) == 1
    assert "Applied target GPU stack chart: nvidia-network-operator" in "\n".join(lines)
    assert "Accepted target GPU stack chart after Helm client timeout" in "\n".join(lines)
    phase = checkpoint["phase_state"]["target-gpu-stack-remediation"]
    network_chart = next(
        item for item in phase["charts"] if item["id"] == "nvidia-network-operator"
    )
    assert "workloads 1/1 ready" in network_chart["readiness"]
    assert network_chart["timeout_recovered"] == "true"
    assert network_chart["timeout_seconds"] == "3000"


def test_target_gpu_stack_remediation_timeout_reports_retry_when_readiness_probe_times_out() -> (
    None
):
    class _ReadinessProbeTimeoutRunner(_FakeCommandRunner):
        def __call__(
            self,
            args: Sequence[str],
            *,
            input_text: str | None = None,
            timeout_seconds: int = 300,
            check: bool = True,
        ) -> SoperatorMigrationCommandResult:
            command = tuple(str(item) for item in args)
            if command and command[0] == "helm" and "upgrade" in command:
                release_name = command[command.index("--install") + 1]
                if release_name == "network-operator":
                    self.calls.append((command, input_text))
                    raise subprocess.TimeoutExpired(list(command), timeout_seconds)
            if command and command[0] == "helm" and "list" in command:
                namespace = command[command.index("-n") + 1] if "-n" in command else ""
                if namespace == "nvidia-network-operator":
                    self.calls.append((command, input_text))
                    raise subprocess.TimeoutExpired(list(command), timeout_seconds)
            return super().__call__(
                command,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                check=check,
            )

    payload = _payload()
    _add_external_gpu_cluster_inventory(payload)
    runner = _ReadinessProbeTimeoutRunner()

    with pytest.raises(RuntimeError, match="timed out after 3000 seconds.*retry from live state"):
        migration._execute_target_gpu_stack_remediation_phase(
            checkpoint={},
            payload=payload,
            target_ref="external-cluster",
            kube_context="external-context",
            command_runner=runner,
        )


def test_target_gpu_stack_remediation_unsatisfied_when_post_render_patch_missing() -> None:
    payload = _payload()
    _add_external_gpu_cluster_inventory(payload)
    runner = _FakeCommandRunner(
        helm_statuses={
            ("nvidia-gpu-operator", "gpu-operator"): "deployed",
            ("nvidia-network-operator", "network-operator"): "deployed",
        }
    )

    assert (
        migration._target_gpu_stack_remediation_satisfied(
            payload=payload,
            target_ref="external-cluster",
            kube_context="external-context",
            command_runner=runner,
        )
        is False
    )
    assert any(
        call[0][:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "get",
            "-f",
            "-",
            "-o",
            "json",
        )
        and "--request-timeout=20s" in call[0]
        for call in runner.calls
    )


def test_target_gpu_stack_post_render_patch_satisfied_accepts_kubectl_list() -> None:
    patch = {
        "target": {
            "group": "mellanox.com",
            "version": "v1alpha1",
            "kind": "NicClusterPolicy",
            "name": "nic-cluster-policy",
        },
        "patch": "\n".join(
            [
                "apiVersion: mellanox.com/v1alpha1",
                "kind: NicClusterPolicy",
                "metadata:",
                "  name: nic-cluster-policy",
                "spec:",
                "  rdmaSharedDevicePlugin:",
                "    image: k8s-rdma-shared-dev-plugin",
                "    repository: nvcr.io/nvidia/mellanox",
                "    version: network-operator-v25.7.0",
                "    config: |",
                '      {"configList":[{"resourceName":"shared_device"}]}',
            ]
        ),
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        return SoperatorMigrationCommandResult(
            command,
            0,
            json.dumps(
                {
                    "kind": "List",
                    "items": [
                        {
                            "kind": "NicClusterPolicy",
                            "spec": {
                                "rdmaSharedDevicePlugin": {
                                    "image": "k8s-rdma-shared-dev-plugin",
                                    "repository": "nvcr.io/nvidia/mellanox",
                                    "version": "network-operator-v25.7.0",
                                    "config": json.dumps(
                                        {"configList": [{"resourceName": "shared_device"}]},
                                        indent=2,
                                    ),
                                    "imagePullSecrets": [],
                                }
                            },
                        }
                    ],
                }
            ),
            "",
        )

    assert migration._target_gpu_stack_post_render_patch_satisfied(
        command_runner=_runner,
        kube_context="external-context",
        patch=patch,
    )


def test_execute_runs_configured_mk8s_gpu_validations_during_validation_hold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = [
        {
            "kind": "mk8s_gpu_operator_readiness",
            "target_ref": "external-cluster",
            "name": "GPU stack readiness",
            "report_file": "deploy-gpu-stack-readiness-report-external-cluster.json",
        },
        {
            "kind": "mk8s_gpu_visibility",
            "target_ref": "external-cluster",
            "name": "GPU visibility probe",
            "report_file": "deploy-gpu-visibility-report-external-cluster.json",
        },
    ]
    calls: list[dict[str, Any]] = []

    def _fake_specs(_payload: object) -> list[dict[str, Any]]:
        return [dict(item) for item in specs]

    def _fake_run(
        validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit,
    ) -> list[Path]:
        calls.append(
            {
                "validations": [dict(item) for item in validations],
                "reports_dir": reports_dir,
                "extra_env": dict(extra_env or {}),
            }
        )
        written: list[Path] = []
        for item in validations:
            report_path = reports_dir / str(item["report_file"])
            report_path.write_text(
                json.dumps(
                    {
                        "kind": item["kind"],
                        "name": item["name"],
                        "target_ref": item["target_ref"],
                        "passed": True,
                    }
                ),
                encoding="utf-8",
            )
            written.append(report_path)
        emit("Starting validation 1/2: GPU stack readiness.")
        return written

    monkeypatch.setattr(migration, "mk8s_gpu_validation_specs", _fake_specs)
    monkeypatch.setattr(migration, "run_mk8s_gpu_validations", _fake_run)

    locked_upgrade_path = _locked_upgrade_path_fixture()
    segment_id = locked_upgrade_path["segments"][0]["id"]
    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
        locked_upgrade_path=locked_upgrade_path,
        upgrade_path_fingerprint="locked-path-fingerprint",
        upgrade_path_segment_id=segment_id,
    )

    assert result.pending_phase == "none"
    assert len(calls) == 1
    assert [item["kind"] for item in calls[0]["validations"]] == [
        "mk8s_gpu_operator_readiness",
        "mk8s_gpu_visibility",
    ]
    assert calls[0]["extra_env"] == {
        "KUBECTL_CONTEXT": "external-context",
        "HELM_KUBECONTEXT": "external-context",
    }
    result_text = "\n".join(result.lines)
    assert "validation-and-rollback-hold: Starting validation 1/2" in result_text
    assert (
        "validation-and-rollback-hold: External Soperator upgrade report will include the MK8s GPU validation rollup."
    ) in result_text
    assert (
        "validation-and-rollback-hold: Deploy-compatible validation report refreshed:"
        in result_text
    )
    reports_dir = tmp_path / "generated" / "reports"
    assert not (reports_dir / "acceptance-benchmark-report-external-cluster.json").exists()
    assert "## Validations" in (reports_dir / "deploy-report.md").read_text(encoding="utf-8")
    migrate_report = (reports_dir / "ext-soperator-upgrade-report.md").read_text(encoding="utf-8")
    upgrade_json_report = json.loads(
        (reports_dir / "ext-soperator-upgrade-report.json").read_text(encoding="utf-8")
    )
    assert "## Upgrade Steps" in migrate_report
    assert "## Locked Upgrade Path" in migrate_report
    assert "Kubernetes 1.31 -> 1.32 plus Soperator" in migrate_report
    assert "ext-soperator-upgrades/external-cluster/segment-1-kubernetes-1-31-1-32-soperator/report.md" in migrate_report
    assert "## Stage Fast Verification" in migrate_report
    assert "- Upgrade performed: `yes`" in migrate_report
    assert "`validation-and-rollback-hold`: `PASS`" in migrate_report
    assert "Soperator and Slurm smoke" in migrate_report
    assert "### MK8s GPU" in migrate_report
    assert "## Shared Upgrade Safety" in migrate_report
    assert "shared safety=passed/passed" in migrate_report
    assert "deploy-smoke-report-external-cluster.json" in migrate_report
    assert "`deploy-gpu-stack-readiness-report-external-cluster.json`: `PASS`" in migrate_report
    assert "`deploy-gpu-visibility-report-external-cluster.json`: `PASS`" in migrate_report
    assert "acceptance-benchmark-report-external-cluster.json" not in migrate_report
    soperator_report = json.loads(
        (reports_dir / "deploy-smoke-report-external-cluster.json").read_text(encoding="utf-8")
    )
    assert soperator_report["status"] == "passed"
    assert soperator_report["scope"] == "soperator-deployment-snapshot"
    check_names = {check["name"] for check in soperator_report["checks"]}
    assert {"SlurmCluster visibility", "NodeSet visibility"} <= check_names
    assert "Slurm srun smoke job" not in check_names
    checkpoint = json.loads(
        soperator_migration_checkpoint_path(tmp_path / "config.yaml", "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    validation_state = checkpoint["phase_state"]["validation-and-rollback-hold"]
    assert validation_state["validation_contract_revision"] == 2
    assert validation_state["mk8s_gpu_validation_count"] == 2
    assert validation_state["soperator_cluster_validation_count"] == 1
    assert len(validation_state["mk8s_gpu_validation_reports"]) == 2
    assert len(validation_state["soperator_cluster_validation_reports"]) == 1
    assert checkpoint["upgrade_report"].endswith(
        "generated/reports/ext-soperator-upgrade-report.md"
    )
    assert checkpoint["upgrade_report_json"].endswith(
        "generated/reports/ext-soperator-upgrade-report.json"
    )
    assert checkpoint["locked_upgrade_path"] == locked_upgrade_path
    assert checkpoint["upgrade_path_fingerprint"] == "locked-path-fingerprint"
    assert checkpoint["current_segment_id"] == segment_id
    assert checkpoint["completed_segment_ids"] == [segment_id]
    expected_backup = _backup_metadata_with_archive(tmp_path / "config.yaml", "test-pre-upgrade")
    segment_state = checkpoint["segment_state"][segment_id]
    assert segment_state["segment_report_path"].endswith(
        "generated/reports/ext-soperator-upgrades/external-cluster/"
        "segment-1-kubernetes-1-31-1-32-soperator/report.md"
    )
    assert segment_state["backup_path"] == expected_backup["path"]
    assert checkpoint["upgrade_safety"]["post_upgrade_verification"]["status"] == "passed"
    assert checkpoint["upgrade_safety"]["protected_customer_state"]["before_hash"]
    assert checkpoint["upgrade_safety"]["protected_customer_state"]["after_hash"]
    assert upgrade_json_report["schema"] == migration.SOPERATOR_MIGRATION_REPORT_SCHEMA
    assert upgrade_json_report["target_ref"] == "external-cluster"
    assert upgrade_json_report["upgrade_performed"] is True
    assert upgrade_json_report["pending_phase"] == "none"
    assert upgrade_json_report["checkpoint_path"].endswith(
        ".nebius-cxcli/ext-soperator-upgrades/external-cluster/checkpoint.json"
    )
    assert upgrade_json_report["backup"]["path"] == expected_backup["path"]
    assert upgrade_json_report["backup"]["sha256"] == expected_backup["sha256"]
    assert upgrade_json_report["backup"]["size_bytes"] == expected_backup["size_bytes"]
    assert upgrade_json_report["locked_upgrade_path"] == locked_upgrade_path
    assert upgrade_json_report["upgrade_path_progress"]["fingerprint"] == (
        "locked-path-fingerprint"
    )
    assert upgrade_json_report["upgrade_path_progress"]["completed_segment_ids"] == [segment_id]
    assert upgrade_json_report["segment_history"][0]["status"] == "completed"
    assert upgrade_json_report["segment_history"][0]["backup_path"] == expected_backup["path"]
    segment_report_path, segment_json_report_path = (
        migration.ext_soperator_upgrade_segment_report_paths(
            tmp_path / "config.yaml",
            "external-cluster",
            segment_id,
        )
    )
    assert segment_report_path.exists()
    assert segment_json_report_path.exists()
    segment_markdown_report = segment_report_path.read_text(encoding="utf-8")
    segment_json_report = json.loads(segment_json_report_path.read_text(encoding="utf-8"))
    assert segment_json_report["latest_markdown_report"].endswith(
        "generated/reports/ext-soperator-upgrade-report.md"
    )
    assert segment_json_report["markdown_report"] == str(segment_report_path)
    assert "### populate-jail-refresh" in segment_markdown_report
    assert "Jail Upgrade" in segment_markdown_report
    assert "populate-jail-refresh" in {phase["id"] for phase in segment_json_report["phases"]}
    assert upgrade_json_report["post_upgrade_verification"]["status"] == "passed"
    assert upgrade_json_report["protected_customer_state"]["before_hash"]
    assert upgrade_json_report["fast_smoke"]["status"] == "passed"
    assert {item["phase_id"]: item["status"] for item in upgrade_json_report["stage_verification"]}[
        "validation-and-rollback-hold"
    ] == "passed"
    assert "validation-and-rollback-hold" in {
        phase["id"] for phase in upgrade_json_report["phases"]
    }


def test_external_node_template_quiesces_zero_surge_service_roles(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(version="3.0.5")
    snapshot["node_groups"] = {
        "controller": {
            "gpu": False,
            "node_count": 1,
            "labels": {
                "nebius.com/node-group": "controller",
                "nebius.com/node-group-id": "nodegroup-controller",
                "slurm.nebius.ai/nodeset": "controller",
            },
            "nodes": ["controller-node-a"],
        },
        "login": {
            "gpu": False,
            "node_count": 2,
            "labels": {
                "nebius.com/node-group": "login",
                "nebius.com/node-group-id": "nodegroup-login",
                "slurm.nebius.ai/nodeset": "login",
            },
            "nodes": ["login-node-a", "login-node-b"],
        },
        "accounting": {
            "gpu": False,
            "node_count": 1,
            "labels": {
                "nebius.com/node-group": "accounting",
                "nebius.com/node-group-id": "nodegroup-accounting",
                "slurm.nebius.ai/nodeset": "accounting",
            },
            "nodes": ["accounting-node-a"],
        },
        "worker-gpu-0": {
            "gpu": True,
            "node_count": 2,
            "labels": {
                "nebius.com/node-group": "worker-gpu-0",
                "nebius.com/node-group-id": "nodegroup-worker-gpu",
                "slurm.nebius.ai/nodeset": "worker-gpu",
            },
            "allocatable": {"cpu": "127900m", "nvidia.com/gpu": "8"},
            "nodes": ["gpu-node-a", "gpu-node-b"],
        },
    }
    source_report = _source_report(snapshot)
    report = source_report["report"]
    assert isinstance(report, dict)
    report["migration_plan"] = [
        {"id": "discovery-and-plan", "status": "complete"},
        {
            "id": "customer-approval",
            "status": "planned",
            "requires_customer_approval": True,
        },
        {
            "id": "external-node-template-upgrade",
            "status": "planned",
            "requires_customer_approval": True,
        },
    ]
    payload = _payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    assert isinstance(target, dict)
    onboarding = target["soperator_onboarding"]
    assert isinstance(onboarding, dict)
    onboarding["actions"] = ["upgrade-external-node-template"]
    onboarding["node_template_upgrade"] = {
        "target_k8s_version": "1.33",
        "target_os": "ubuntu24.04",
        "target_gpu_stack_preset": "cuda13.0",
    }
    runner = _FakeCommandRunner(
        existing_node_groups=[
            {
                **_legacy_service_node_group("controller"),
                "spec": {
                    **_legacy_service_node_group("controller")["spec"],
                    "version": "1.32",
                },
            },
            {
                **_legacy_service_node_group("login", fixed_node_count=2),
                "spec": {
                    **_legacy_service_node_group("login", fixed_node_count=2)["spec"],
                    "version": "1.32",
                },
            },
            {
                **_legacy_service_node_group("accounting"),
                "spec": {
                    **_legacy_service_node_group("accounting")["spec"],
                    "version": "1.32",
                },
            },
            {
                "metadata": {
                    "id": "nodegroup-worker-gpu",
                    "name": "worker-gpu-0",
                    "parent_id": "cluster-123",
                },
                "spec": {
                    "version": "1.32",
                    "template": {
                        "resources": {
                            "platform": "gpu-h100-sxm",
                            "preset": "8gpu-128vcpu-1600gb",
                        },
                        "gpu_settings": {"drivers_preset": "cuda12.8"},
                        "os": "ubuntu24.04",
                    },
                },
                "status": _ready_node_group_status(version="1.32", nodes=2),
            },
        ],
        live_nodes=[
            {
                "metadata": {
                    "name": "gpu-node-a",
                    "labels": {"nebius.com/node-group": "worker-gpu-0"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
            {
                "metadata": {
                    "name": "gpu-node-b",
                    "labels": {"nebius.com/node-group": "worker-gpu-0"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
        ],
        cluster={
            "metadata": {"id": "cluster-123", "name": "external-cluster"},
            "spec": {"control_plane": {"version": "1.32"}},
        },
        live_kubernetes_resources={
            "deployment": [
                {
                    "metadata": {
                        "name": "security-profiles-operator-webhook",
                        "namespace": "security-profiles-operator-system",
                    },
                    "spec": {"replicas": 3},
                    "status": {"availableReplicas": 3, "readyReplicas": 3},
                }
            ],
            "statefulsets.apps.kruise.io": [
                {
                    "metadata": {"name": "login", "namespace": "soperator"},
                    "spec": {"replicas": 2},
                    "status": {"availableReplicas": 2, "readyReplicas": 2},
                }
            ]
        },
        live_slurmclusters=[
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "external-cluster", "namespace": "soperator"},
                "spec": {"slurmNodes": {}},
                "status": {"phase": "Available"},
            },
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "soperator", "namespace": "soperator"},
                "spec": {
                    "slurmNodes": {
                        "accounting": {"enabled": True},
                        "login": {"size": 2},
                    }
                },
                "status": {"phase": "Available"},
            },
        ],
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
        worker_rollout_strategy="zero-surge",
    )

    assert result.pending_phase == "none"
    commands = [call[0] for call in runner.calls]
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "scale",
        "statefulsets.apps.kruise.io/controller",
        "--replicas",
        "0",
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "scale",
        "statefulsets.apps.kruise.io/controller",
        "--replicas",
        "1",
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "security-profiles-operator-system",
        "scale",
        "deployment/security-profiles-operator-webhook",
        "--replicas",
        "2",
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "security-profiles-operator-system",
        "scale",
        "deployment/security-profiles-operator-webhook",
        "--replicas",
        "3",
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "patch",
        "slurmcluster/soperator",
        "--type",
        "merge",
        "-p",
        '{"spec": {"slurmNodes": {"login": {"size": 1}}}}',
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "scale",
        "statefulsets.apps.kruise.io/login",
        "--replicas",
        "1",
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "scale",
        "statefulsets.apps.kruise.io/login",
        "--replicas",
        "2",
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "patch",
        "slurmcluster/soperator",
        "--type",
        "merge",
        "-p",
        '{"spec": {"slurmNodes": {"login": {"size": 2}}}}',
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "patch",
        "slurmcluster/soperator",
        "--type",
        "merge",
        "-p",
        '{"spec": {"slurmNodes": {"accounting": {"enabled": false}}}}',
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "patch",
        "slurmcluster/soperator",
        "--type",
        "merge",
        "-p",
        '{"spec": {"slurmNodes": {"accounting": {"enabled": true}}}}',
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "patch",
        "mariadb.k8s.mariadb.com/soperator-acct-db",
        "--type",
        "merge",
        "-p",
        '{"spec": {"suspend": true}}',
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "patch",
        "mariadb.k8s.mariadb.com/soperator-acct-db",
        "--type",
        "merge",
        "-p",
        '{"spec": {"suspend": false}}',
    ) in commands
    checkpoint = json.loads(
        soperator_migration_checkpoint_path(
            tmp_path / "config.yaml",
            "external-cluster",
        ).read_text(encoding="utf-8")
    )
    assert (
        checkpoint["phase_state"]["external-node-template-upgrade"]["node_groups"]["controller"][
            "service_quiesce"
        ]["status"]
        == "restored"
    )


def test_execute_upgrades_external_node_template_with_safe_surge_strategy(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    snapshot = _snapshot()
    snapshot["node_groups"]["login"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "login",
            "nebius.com/node-group-id": "nodegroup-login",
            "slurm.nebius.ai/nodeset": "login",
        },
        "nodes": ["login-node-a"],
    }
    source_report = _source_report(snapshot)
    runner = _FakeCommandRunner(
        existing_node_groups=[_legacy_service_node_group("login")],
    )

    result = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
        worker_rollout_strategy="safe-surge",
    )

    assert result.pending_phase == "none"
    cluster_updates = [
        call[0] for call in runner.calls if call[0][:4] == ("nebius", "mk8s", "cluster", "update")
    ]
    assert [
        command[command.index("--control-plane-version") + 1] for command in cluster_updates
    ] == ["1.32"]
    node_template_updates = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" in call[0]
    ]
    assert node_template_updates
    update_command = node_template_updates[0]
    assert update_command[update_command.index("--version") + 1] == "1.32"
    assert (
        update_command[update_command.index("--template-gpu-settings-drivers-preset") + 1]
        == "cuda13.0"
    )
    assert update_command[update_command.index("--strategy-max-surge-count") + 1] == "1"
    assert update_command[update_command.index("--strategy-max-unavailable-count") + 1] == "0"
    assert update_command[update_command.index("--strategy-drain-timeout") + 1] == "30m"
    node_template_update_index = next(
        index for index, call in enumerate(runner.calls) if call[0] == update_command
    )
    restore_commands_after_node_template = [
        call[0]
        for call in runner.calls[node_template_update_index + 1 :]
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" not in call[0]
        and "--template-filesystems" not in call[0]
        and call[0][call[0].index("--timeout") + 1]
        == update_command[update_command.index("--timeout") + 1]
    ]
    assert restore_commands_after_node_template
    restore_command = restore_commands_after_node_template[0]
    assert restore_command[restore_command.index("--strategy-max-surge-count") + 1] == "1"
    assert restore_command[restore_command.index("--strategy-max-unavailable-count") + 1] == "0"
    assert restore_command[restore_command.index("--strategy-drain-timeout") + 1] == "0s"
    login_update_commands = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-login"
        and "--version" in call[0]
    ]
    assert login_update_commands
    login_update_command = login_update_commands[0]
    assert login_update_command[login_update_command.index("--strategy-max-surge-count") + 1] == "1"
    assert (
        login_update_command[login_update_command.index("--strategy-max-unavailable-count") + 1]
        == "0"
    )
    assert login_update_command[login_update_command.index("--strategy-drain-timeout") + 1] == "30m"
    commands = [call[0] for call in runner.calls]
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "scale",
        "statefulsets.apps.kruise.io/login",
        "--replicas",
        "0",
    ) not in commands

    checkpoint = json.loads(
        soperator_migration_checkpoint_path(config_path, "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    phase = checkpoint["phase_state"]["external-node-template-upgrade"]
    assert checkpoint["external_node_template_rollout"] == {
        "strategy": "safe-surge",
        "service_role_strategy": "safe-surge",
        "worker_wave_percent": 1,
        "service_role_group_strategy": {
            "max_surge_count": 1,
            "max_unavailable_count": 0,
            "drain_timeout": "30m",
        },
        "worker_group_strategy": {
            "max_surge_count": 1,
            "max_unavailable_count": 0,
            "drain_timeout": "30m",
        },
    }
    assert phase["control_plane"]["hops"]["1.32"]["status"] == "completed"
    assert phase["node_groups"]["login"]["strategy"] == "safe-surge"
    assert (
        phase["node_groups"]["login"]["login_service_ready_before_node_update"][
            "ready_endpoints"
        ]
        >= 1
    )
    assert (
        phase["node_groups"]["login"]["login_service_ready_after_node_update"][
            "ready_endpoints"
        ]
        >= 1
    )
    assert phase["node_groups"]["gpu-pool"]["strategy"] == "safe-surge"
    assert phase["node_groups"]["gpu-pool"]["strategy_restored"] is True
    assert phase["worker_waves"] == [["gpu-pool"]]
    assert any(
        line.startswith("post-upgrade-mk8s-check: External MK8s node-template verified")
        for line in result.lines
    )


def test_execute_external_node_template_parallel_worker_failure_checkpoints_after_executor_exit(
    tmp_path: Path,
) -> None:
    def _source_worker_group(name: str, node_group_id: str) -> dict[str, Any]:
        return {
            "gpu": True,
            "node_count": 2,
            "labels": {
                "nebius.com/node-group": name,
                "nebius.com/node-group-id": node_group_id,
                "slurm.nebius.ai/nodeset": name,
            },
            "allocatable": {"nvidia.com/gpu": "8"},
            "nodes": [f"{name}-a", f"{name}-b"],
        }

    def _live_worker_group(name: str, node_group_id: str) -> dict[str, Any]:
        return {
            "metadata": {
                "id": node_group_id,
                "name": name,
                "parent_id": "cluster-123",
            },
            "spec": {
                "version": "1.32",
                "strategy": {
                    "max_surge": {"count": 2},
                    "max_unavailable": {"count": 0},
                    "drain_timeout": "10m",
                },
                "template": {
                    "metadata": {"labels": {"nebius.com/node-group": name}},
                    "resources": {
                        "platform": "gpu-h100-sxm",
                        "preset": "8gpu-128vcpu-1600gb",
                    },
                    "boot_disk": {"type": "NETWORK_SSD", "size_gibibytes": "256"},
                    "gpu_settings": {"drivers_preset": "cuda12.8"},
                    "os": "ubuntu22.04",
                    "network_interfaces": [{"subnet_id": "subnet-123"}],
                    "filesystems": [],
                },
            },
            "status": _ready_node_group_status(version="1.32", nodes=2),
        }

    class _FailingParallelWorkerRunner(_FakeCommandRunner):
        def __call__(
            self,
            args,
            *,
            input_text: str | None = None,
            timeout_seconds: int = 300,
            check: bool = True,
        ) -> SoperatorMigrationCommandResult:
            command = tuple(str(item) for item in args)
            if (
                command[:5]
                == (
                    "nebius",
                    "mk8s",
                    "node-group",
                    "update",
                    "nodegroup-worker-b",
                )
                and "--version" in command
            ):
                self.calls.append((command, input_text))
                if check:
                    raise RuntimeError(f"{' '.join(command)} failed: simulated update failure")
                return SoperatorMigrationCommandResult(command, 1, "", "simulated update failure")
            return super().__call__(
                args,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                check=check,
            )

    config_path = tmp_path / "config.yaml"
    snapshot = _snapshot()
    snapshot["node_groups"] = {
        "worker-gpu-a": _source_worker_group("worker-gpu-a", "nodegroup-worker-a"),
        "worker-gpu-b": _source_worker_group("worker-gpu-b", "nodegroup-worker-b"),
    }
    source_report = _source_report(snapshot)
    payload = _payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    assert isinstance(target, dict)
    onboarding = target["soperator_onboarding"]
    assert isinstance(onboarding, dict)
    onboarding["actions"] = ["upgrade-external-node-template"]
    onboarding["node_template_upgrade"] = {
        "target_k8s_version": "1.33",
        "target_os": "ubuntu24.04",
        "target_gpu_stack_preset": "cuda13.0",
        "rollout": {"strategy": "safe-surge", "worker_wave_percent": 100},
    }
    runner = _FailingParallelWorkerRunner(
        existing_node_groups=[
            _live_worker_group("worker-gpu-a", "nodegroup-worker-a"),
            _live_worker_group("worker-gpu-b", "nodegroup-worker-b"),
        ],
        cluster={
            "metadata": {"id": "cluster-123", "name": "external-cluster"},
            "spec": {"control_plane": {"version": "1.32"}},
        },
        live_nodes=[
            {
                "metadata": {
                    "name": "worker-gpu-a-node-a",
                    "labels": {
                        "nebius.com/node-group": "worker-gpu-a",
                        "nebius.com/node-group-id": "nodegroup-worker-a",
                    },
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
            {
                "metadata": {
                    "name": "worker-gpu-b-node-a",
                    "labels": {
                        "nebius.com/node-group": "worker-gpu-b",
                        "nebius.com/node-group-id": "nodegroup-worker-b",
                    },
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
        ],
    )

    with pytest.raises(RuntimeError, match="simulated update failure"):
        execute_soperator_migration(
            config_path=config_path,
            target_ref="external-cluster",
            payload=payload,
            source_report=source_report,
            snapshot_collector=lambda *, kube_context: snapshot,
            approved=True,
            command_runner=runner,
        )

    checkpoint = json.loads(
        soperator_migration_checkpoint_path(config_path, "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    phase = checkpoint["phase_state"]["external-node-template-upgrade"]
    assert phase["worker_waves"] == [["worker-gpu-a", "worker-gpu-b"]]
    assert phase["node_groups"]["worker-gpu-b"]["status"] == "failed"
    assert "simulated update failure" in phase["node_groups"]["worker-gpu-b"]["error"]


class _TimeoutAfterAcceptedNodeTemplateUpdateRunner(_FakeCommandRunner):
    def __init__(self, *, leave_rollout_in_progress: bool = False) -> None:
        super().__init__(
            cluster={
                "metadata": {"id": "cluster-123", "name": "external-cluster"},
                "spec": {"control_plane": {"version": "1.32"}},
            },
            existing_node_groups=[
                {
                    "metadata": {
                        "id": "nodegroup-gpu-pool",
                        "name": "gpu-pool",
                        "parent_id": "cluster-123",
                    },
                    "spec": {
                        "version": "1.32",
                        "strategy": {
                            "max_surge": {"count": 2},
                            "max_unavailable": {"count": 0},
                            "drain_timeout": "10m",
                        },
                        "template": {
                            "metadata": {"labels": {"nebius.com/node-group": "gpu-pool"}},
                            "resources": {
                                "platform": "gpu-h100-sxm",
                                "preset": "8gpu-128vcpu-1600gb",
                            },
                            "boot_disk": {"type": "NETWORK_SSD", "size_gibibytes": "256"},
                            "gpu_settings": {"drivers_preset": "cuda12.8"},
                            "os": "ubuntu22.04",
                            "network_interfaces": [{"subnet_id": "subnet-123"}],
                            "filesystems": [],
                        },
                    },
                    "status": _ready_node_group_status(version="1.32", nodes=2),
                }
            ],
        )
        self.leave_rollout_in_progress = leave_rollout_in_progress
        self.timed_out = False

    def __call__(
        self,
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        command = tuple(str(item) for item in args)
        if (
            command[:4] == ("nebius", "mk8s", "node-group", "update")
            and command[4] == "nodegroup-gpu-pool"
            and "--version" in command
            and not self.timed_out
        ):
            super().__call__(
                command,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                check=check,
            )
            if self.leave_rollout_in_progress:
                target_version = command[command.index("--version") + 1]
                for node_group in self.existing_node_groups:
                    metadata = node_group.get("metadata")
                    if isinstance(metadata, dict) and metadata.get("id") == "nodegroup-gpu-pool":
                        node_group["status"] = {
                            "version": target_version,
                            "ready_node_count": 1,
                            "target_node_count": 2,
                            "node_count": 3,
                            "outdated_node_count": 1,
                            "reconciling": True,
                        }
            self.timed_out = True
            raise subprocess.TimeoutExpired(list(command), timeout_seconds)
        return super().__call__(
            command,
            input_text=input_text,
            timeout_seconds=timeout_seconds,
            check=check,
        )


class _TimeoutBeforeLiveNodeTemplateVisibleRunner(_TimeoutAfterAcceptedNodeTemplateUpdateRunner):
    def __call__(
        self,
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        command = tuple(str(item) for item in args)
        if (
            command[:4] == ("nebius", "mk8s", "node-group", "update")
            and command[4] == "nodegroup-gpu-pool"
            and "--version" in command
            and not self.timed_out
        ):
            self.calls.append((command, input_text))
            self.timed_out = True
            raise subprocess.TimeoutExpired(list(command), timeout_seconds)
        return super().__call__(
            command,
            input_text=input_text,
            timeout_seconds=timeout_seconds,
            check=check,
        )


def test_execute_external_node_template_reconciles_accepted_timeout_when_ready(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    runner = _TimeoutAfterAcceptedNodeTemplateUpdateRunner()

    result = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(target_k8s_version="1.33"),
        source_report=_source_report(target_k8s_version="1.33"),
        backup_metadata=_backup_metadata("waiting-rollout"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "none"
    node_template_updates = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" in call[0]
    ]
    assert len(node_template_updates) == 1
    assert any(
        line.startswith("post-upgrade-mk8s-check: External MK8s node-template verified")
        for line in result.lines
    )


def test_execute_external_node_template_resume_waiting_rollout_without_duplicate_update(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    runner = _TimeoutAfterAcceptedNodeTemplateUpdateRunner(leave_rollout_in_progress=True)

    result = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(target_k8s_version="1.33"),
        source_report=_source_report(target_k8s_version="1.33"),
        backup_metadata=_backup_metadata("waiting-rollout"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "external-node-template-upgrade"
    checkpoint = json.loads(
        soperator_migration_checkpoint_path(config_path, "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    group_state = checkpoint["phase_state"]["external-node-template-upgrade"]["node_groups"][
        "gpu-pool"
    ]
    assert group_state["status"] == "waiting-rollout"
    assert group_state["original_strategy_args"] == [
        "--strategy-max-surge-count",
        "2",
        "--strategy-max-unavailable-count",
        "0",
        "--strategy-drain-timeout",
        "10m",
    ]
    assert "rollout is still in progress" in group_state["pending_reason"]

    for node_group in runner.existing_node_groups:
        metadata = node_group.get("metadata")
        if isinstance(metadata, dict) and metadata.get("id") == "nodegroup-gpu-pool":
            node_group["status"] = _ready_node_group_status(version="1.33", nodes=2)

    resumed = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(target_k8s_version="1.33"),
        source_report=_source_report(target_k8s_version="1.33"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
    )

    node_template_updates = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" in call[0]
    ]
    assert len(node_template_updates) == 1
    strategy_restores = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" not in call[0]
        and "--strategy-max-surge-count" in call[0]
    ]
    assert (
        strategy_restores[-1][strategy_restores[-1].index("--strategy-max-surge-count") + 1] == "2"
    )
    assert (
        strategy_restores[-1][strategy_restores[-1].index("--strategy-max-unavailable-count") + 1]
        == "0"
    )
    assert strategy_restores[-1][strategy_restores[-1].index("--strategy-drain-timeout") + 1] == (
        "10m"
    )
    assert resumed.pending_phase == "none"
    assert any(
        "External node-template rollout completed after resume: gpu-pool; "
        "original strategy restored." in line
        for line in resumed.lines
    )
    checkpoint = json.loads(
        soperator_migration_checkpoint_path(config_path, "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    group_state = checkpoint["phase_state"]["external-node-template-upgrade"]["node_groups"][
        "gpu-pool"
    ]
    assert group_state["status"] == "completed"
    assert group_state["strategy_restored"] is True


def test_execute_external_node_template_resumes_failed_timeout_from_live_state(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    runner = _TimeoutBeforeLiveNodeTemplateVisibleRunner()

    with pytest.raises(RuntimeError, match="timed out before the live node group reported"):
        execute_soperator_migration(
            config_path=config_path,
            target_ref="external-cluster",
            payload=_payload(target_k8s_version="1.33"),
            source_report=_source_report(target_k8s_version="1.33"),
            backup_metadata=_backup_metadata("failed-timeout"),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=runner,
        )

    checkpoint = json.loads(
        soperator_migration_checkpoint_path(config_path, "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    group_state = checkpoint["phase_state"]["external-node-template-upgrade"]["node_groups"][
        "gpu-pool"
    ]
    assert group_state["status"] == "failed"

    for node_group in runner.existing_node_groups:
        metadata = node_group.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("id") != "nodegroup-gpu-pool":
            continue
        spec = node_group.setdefault("spec", {})
        assert isinstance(spec, dict)
        spec["version"] = "1.33"
        template = spec.setdefault("template", {})
        assert isinstance(template, dict)
        template["os"] = "ubuntu24.04"
        gpu_settings = template.setdefault("gpu_settings", {})
        assert isinstance(gpu_settings, dict)
        gpu_settings["drivers_preset"] = "cuda13.0"
        spec["strategy"] = {
            "max_surge": {"count": 1},
            "max_unavailable": {"count": 0},
            "drain_timeout": "30m",
        }
        node_group["status"] = _ready_node_group_status(version="1.33", nodes=2)

    resumed = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(target_k8s_version="1.33"),
        source_report=_source_report(target_k8s_version="1.33"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
    )

    node_template_updates = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" in call[0]
    ]
    assert len(node_template_updates) == 1
    strategy_restores = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" not in call[0]
        and "--strategy-max-surge-count" in call[0]
    ]
    assert (
        strategy_restores[-1][strategy_restores[-1].index("--strategy-max-surge-count") + 1] == "2"
    )
    assert (
        strategy_restores[-1][strategy_restores[-1].index("--strategy-max-unavailable-count") + 1]
        == "0"
    )
    assert strategy_restores[-1][strategy_restores[-1].index("--strategy-drain-timeout") + 1] == (
        "10m"
    )
    assert resumed.pending_phase == "none"
    assert any(
        "External node-template rollout completed after live-state reconciliation: "
        "gpu-pool; original strategy restored." in line
        for line in resumed.lines
    )
    checkpoint = json.loads(
        soperator_migration_checkpoint_path(config_path, "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    group_state = checkpoint["phase_state"]["external-node-template-upgrade"]["node_groups"][
        "gpu-pool"
    ]
    assert group_state["status"] == "completed"
    assert group_state["strategy_restored"] is True
    assert "error" not in group_state


def test_execute_external_node_template_allows_zero_surge_worker_fallback(
    tmp_path: Path,
) -> None:
    runner = _FakeCommandRunner(
        existing_node_groups=[
            {
                "metadata": {
                    "id": "nodegroup-gpu-pool",
                    "name": "nodegroup-gpu-pool",
                    "parent_id": "cluster-123",
                },
                "spec": {
                    "version": "1.31",
                    "template": {
                        "metadata": {"labels": {}},
                        "resources": {"platform": "gpu-h100-sxm", "preset": "8gpu"},
                        "boot_disk": {"type": "NETWORK_SSD", "size_gibibytes": "93"},
                        "os": {"name": "ubuntu24.04"},
                        "network_interfaces": [{"subnet_id": "subnet-123"}],
                        "filesystems": [],
                        "gpu_settings": {"drivers_preset": "cuda12.8"},
                    },
                },
                "status": _ready_node_group_status(nodes=100),
            }
        ]
    )

    execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
        worker_rollout_strategy="zero-surge",
    )

    node_template_updates = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" in call[0]
    ]
    assert node_template_updates
    update_command = node_template_updates[0]
    assert update_command[update_command.index("--strategy-max-surge-count") + 1] == "0"
    assert update_command[update_command.index("--strategy-max-unavailable-count") + 1] == "1"
    assert update_command[update_command.index("--strategy-drain-timeout") + 1] == "30m"
    assert update_command[update_command.index("--timeout") + 1] == "1000m"


def test_execute_rejects_changed_worker_rollout_settings_after_checkpoint(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"

    execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(),
        backup_metadata=_backup_metadata("rollout-settings"),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
    )

    with pytest.raises(RuntimeError, match="different external node-template rollout settings"):
        execute_soperator_migration(
            config_path=config_path,
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=_FakeCommandRunner(),
            worker_rollout_strategy="safe-surge",
        )


def test_execute_external_node_template_blocks_unhealthy_worker_before_mutation(
    tmp_path: Path,
) -> None:
    runner = _FakeCommandRunner(
        live_nodes=[
            {
                "metadata": {
                    "name": "node-a",
                    "labels": {"nebius.com/node-group": "gpu-pool"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "False"}]},
            }
        ]
    )

    with pytest.raises(
        RuntimeError,
        match="selected worker node group\\(s\\) have no Ready schedulable nodes",
    ):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=runner,
        )

    assert not any(
        call[0][:4] == ("nebius", "mk8s", "node-group", "update") and "--version" in call[0]
        for call in runner.calls
    )


def test_execute_external_node_template_groups_worker_preflight_by_matched_source_group(
    tmp_path: Path,
) -> None:
    runner = _FakeCommandRunner(
        live_nodes=[
            {
                "metadata": {
                    "name": "node-a",
                    "labels": {"nebius.com/node-group-id": "nodegroup-gpu-pool"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "False"}]},
            }
        ]
    )

    with pytest.raises(
        RuntimeError,
        match="selected worker node group\\(s\\) have no Ready schedulable nodes: gpu-pool",
    ):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=runner,
        )

    assert not any(
        call[0][:4] == ("nebius", "mk8s", "node-group", "update") and "--version" in call[0]
        for call in runner.calls
    )


def test_worker_preflight_prefers_specific_group_identity_over_generic_nodeset() -> None:
    worker_groups = [
        (
            "nodegroup-gpu",
            {
                "node_group_id": "nodegroup-gpu",
                "node_group_name": "worker-gpu-0",
                "labels": {
                    "nebius.com/node-group-id": "nodegroup-gpu",
                    "nebius.com/node-group": "worker-gpu-0",
                    "slurm.nebius.ai/nodeset": "worker",
                    "slurm.nebius.ai/nodeset-name": "worker-gpu",
                },
            },
        ),
        (
            "nodegroup-cpu",
            {
                "node_group_id": "nodegroup-cpu",
                "node_group_name": "worker-cpu-0",
                "labels": {
                    "nebius.com/node-group-id": "nodegroup-cpu",
                    "nebius.com/node-group": "worker-cpu-0",
                    "slurm.nebius.ai/nodeset": "worker",
                    "slurm.nebius.ai/nodeset-name": "worker-cpu",
                },
            },
        ),
    ]
    nodes = [
        {
            "metadata": {
                "name": "computeinstance-cpu",
                "labels": {
                    "nebius.com/node-group-id": "nodegroup-cpu",
                    "slurm.nebius.ai/nodeset": "worker",
                    "slurm.nebius.ai/nodeset-name": "worker-cpu",
                },
            }
        },
        {
            "metadata": {
                "name": "computeinstance-gpu",
                "labels": {
                    "nebius.com/node-group-id": "nodegroup-gpu",
                    "slurm.nebius.ai/nodeset": "worker",
                    "slurm.nebius.ai/nodeset-name": "worker-gpu",
                },
            }
        },
    ]

    grouped = migration._worker_nodes_by_source_group(  # noqa: SLF001
        nodes=nodes,
        worker_groups=worker_groups,
    )

    assert [item["metadata"]["name"] for item in grouped["nodegroup-gpu"]] == [
        "computeinstance-gpu"
    ]
    assert [item["metadata"]["name"] for item in grouped["nodegroup-cpu"]] == [
        "computeinstance-cpu"
    ]


def test_execute_external_node_template_wave_budget_does_not_allow_degraded_workers(
    tmp_path: Path,
) -> None:
    runner = _FakeCommandRunner(
        live_nodes=[
            {
                "metadata": {
                    "name": "node-a",
                    "labels": {"nebius.com/node-group-id": "nodegroup-gpu-pool"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
            {
                "metadata": {
                    "name": "node-b",
                    "labels": {"nebius.com/node-group-id": "nodegroup-gpu-pool"},
                },
                "spec": {"unschedulable": True},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
        ]
    )

    with pytest.raises(
        RuntimeError,
        match="selected worker nodes must start Ready and schedulable",
    ):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            command_runner=runner,
            worker_rollout_strategy="safe-surge",
            worker_wave_groups=2,
        )

    assert not any(
        call[0][:4] == ("nebius", "mk8s", "node-group", "update") and "--version" in call[0]
        for call in runner.calls
    )


def test_execute_external_node_template_blocks_nonempty_slurm_queue_before_mutation(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot_with_compute_source()
    runner = _FakeCommandRunner(slurm_queue_output="RUNNING\nPENDING\n")

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(include_placements=True),
        source_report=_source_report(snapshot),
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
        job_policy="fail",
    )

    assert result.pending_phase == "external-node-template-upgrade"
    assert "Affected Slurm jobs exist for the upgrade scope" in result.pending_reason
    assert not any(
        call[0][:4] == ("nebius", "mk8s", "node-group", "update") and "--version" in call[0]
        for call in runner.calls
    )


def test_execute_external_node_template_rechecks_slurm_queue_before_worker_wave(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot_with_compute_source()
    runner = _FakeCommandRunner(slurm_queue_outputs=["", "RUNNING\n"])

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(include_placements=True),
        source_report=_source_report(snapshot),
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
        job_policy="fail",
    )

    assert result.pending_phase == "external-node-template-upgrade"
    assert "Affected Slurm jobs exist for the upgrade scope" in result.pending_reason
    worker_updates = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" in call[0]
    ]
    assert worker_updates == []


def _external_upgrade_slurm_runner(
    *,
    queue_outputs: list[str],
    calls: list[tuple[str, ...]],
    pending_queue_outputs: list[str] | None = None,
) -> Callable[..., SoperatorMigrationCommandResult]:
    pending_outputs = pending_queue_outputs if pending_queue_outputs is not None else []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "login-0"},
                                "status": {"phase": "Running"},
                            }
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:] == ("scontrol", "show", "nodes"):
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    "NodeName=worker-gpu-0-0 NodeHostName=worker-gpu-0-0 Partitions=gpu",
                    "",
                )
            if command[8:11] == ("scontrol", "show", "node") and command[-1] == "-o":
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    f"NodeName={command[11]} Partitions=gpu State=IDLE",
                    "",
                )
            if command[8:10] == ("squeue", "-h"):
                if "PENDING" in command:
                    return SoperatorMigrationCommandResult(
                        command,
                        0,
                        pending_outputs.pop(0) if pending_outputs else "",
                        "",
                    )
                return SoperatorMigrationCommandResult(command, 0, queue_outputs.pop(0), "")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    return runner


def test_external_upgrade_slurm_jobs_translates_kubernetes_node_names() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {"items": [{"metadata": {"name": "login-0"}, "status": {"phase": "Running"}}]}
                ),
                "",
            )
        if command[8:] == ("scontrol", "show", "nodes"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "\n".join(
                    (
                        "NodeName=worker-3 Arch=x86_64 CoresPerSocket=8",
                        "   NodeAddr=10.1.18.140 NodeHostName=worker-3 Version=24.11.6 Partitions=main",
                        "   InstanceId=computeinstance-e00e8ctxvehgkyged0",
                    )
                ),
                "",
            )
        if command[8:] == ("scontrol", "show", "node", "worker-3", "-o"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "NodeName=worker-3 Partitions=main State=IDLE",
                "",
            )
        if command[8:10] == ("squeue", "-h"):
            return SoperatorMigrationCommandResult(command, 0, "", "")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    jobs = migration._external_upgrade_slurm_jobs(
        command_runner=runner,
        kube_context="external-context",
        node_names=("computeinstance-e00e8ctxvehgkyged0",),
    )

    assert jobs == ()
    squeue_calls = [call for call in calls if call[8:10] == ("squeue", "-h")]
    active_squeue_calls = [
        call for call in squeue_calls if migration._EXTERNAL_UPGRADE_ACTIVE_SQUEUE_STATES in call
    ]
    assert len(active_squeue_calls) == 1
    assert active_squeue_calls[0][-2:] == ("-w", "worker-3")


def test_external_upgrade_slurm_jobs_skips_stale_deleted_computeinstance_nodes() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {"items": [{"metadata": {"name": "login-0"}, "status": {"phase": "Running"}}]}
                ),
                "",
            )
        if command[8:] == ("scontrol", "show", "nodes"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "\n".join(
                    (
                        "NodeName=worker-0 NodeHostName=worker-0 Partitions=main",
                        "   InstanceId=computeinstance-new-0",
                        "NodeName=worker-1 NodeHostName=worker-1 Partitions=main",
                        "   InstanceId=computeinstance-new-1",
                    )
                ),
                "",
            )
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "get",
            "nodes",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "computeinstance-new-0"}},
                            {"metadata": {"name": "computeinstance-new-1"}},
                        ]
                    }
                ),
                "",
            )
        if command[8:10] == ("squeue", "-h"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "42|alice|RUNNING|gpu|worker-0|1|1|None|00:05|30:00|25:00|unrelated\n",
                "",
            )
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    jobs = migration._external_upgrade_slurm_jobs(
        command_runner=runner,
        kube_context="external-context",
        node_names=("computeinstance-old-0", "computeinstance-old-1"),
    )

    assert jobs == ()
    assert any(
        call[:5] == ("kubectl", "--context", "external-context", "get", "nodes")
        for call in calls
    )
    assert not any(call[8:10] == ("squeue", "-h") for call in calls)


def test_external_upgrade_slurm_jobs_empty_scope_returns_no_jobs() -> None:
    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[8:10] == ("squeue", "-h"):
            raise AssertionError("empty affected-node scope must not query all Slurm jobs")
        return SoperatorMigrationCommandResult(command, 0, "", "")

    jobs = migration._external_upgrade_slurm_jobs(
        command_runner=runner,
        kube_context="external-context",
        node_names=(),
    )

    assert jobs == ()


def test_external_upgrade_worker_nodeset_slurm_nodes_maps_running_worker_pods() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "login-0"},
                                "status": {"phase": "Running"},
                            },
                            {
                                "metadata": {
                                    "name": "controller-0",
                                    "labels": {"slurm.nebius.ai/nodeset-name": "controller"},
                                },
                                "status": {"phase": "Running"},
                            },
                            {
                                "metadata": {
                                    "name": "worker-gpu-0",
                                    "labels": {"slurm.nebius.ai/nodeset-name": "worker-gpu"},
                                },
                                "status": {"phase": "Running"},
                            },
                            {
                                "metadata": {
                                    "name": "worker-cpu-0",
                                    "labels": {"slurm.nebius.ai/nodeset": "worker-cpu"},
                                },
                                "status": {"phase": "Running"},
                            },
                            {
                                "metadata": {
                                    "name": "worker-gpu-pending",
                                    "labels": {"slurm.nebius.ai/nodeset-name": "worker-gpu"},
                                },
                                "status": {"phase": "Pending"},
                            },
                        ]
                    }
                ),
                "",
            )
        if command[8:] == ("scontrol", "show", "nodes"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "\n".join(
                    (
                        "NodeName=worker-gpu-0-0 NodeHostName=worker-gpu-0",
                        "NodeName=worker-cpu-0-0 NodeHostName=worker-cpu-0",
                    )
                ),
                "",
            )
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    nodes = migration._external_upgrade_worker_nodeset_slurm_nodes(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
    )

    assert nodes == ("worker-gpu-0-0", "worker-cpu-0-0")
    assert any(call[8:] == ("scontrol", "show", "nodes") for call in calls)


def test_external_upgrade_slurm_jobs_fails_closed_for_unmapped_kubernetes_node() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {"items": [{"metadata": {"name": "login-0"}, "status": {"phase": "Running"}}]}
                ),
                "",
            )
        if command[8:] == ("scontrol", "show", "nodes"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "\n".join(
                    (
                        "NodeName=worker-3 Arch=x86_64 CoresPerSocket=8",
                        "   NodeAddr=10.1.18.140 NodeHostName=worker-3 Version=24.11.6",
                        "   InstanceId=computeinstance-e00e8ctxvehgkyged0",
                    )
                ),
                "",
            )
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "get",
            "nodes",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(command, 1, "", "node inventory unavailable")
        if command[8:10] == ("squeue", "-h"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "42|alice|RUNNING|gpu|worker-9|00:05|30:00|25:00|unrelated\n",
                "",
            )
        if command[8:9] == ("scancel",):
            return SoperatorMigrationCommandResult(command, 0, "cancelled\n", "")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    with pytest.raises(RuntimeError, match="could not map Kubernetes node"):
        migration._handle_external_upgrade_slurm_jobs(  # noqa: SLF001
            command_runner=runner,
            kube_context="external-context",
            node_names=("computeinstance-missing",),
            policy="cancel-all",
            cancel_job_ids=(),
            requeue_job_ids=(),
            wait_timeout_seconds=0,
            refresh_interval_seconds=1,
        )

    assert not any(call[8:10] == ("squeue", "-h") for call in calls)
    assert not any(call[8:9] == ("scancel",) for call in calls)


def test_external_upgrade_slurm_jobs_falls_back_to_controller_for_login_config_failure() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {"items": [{"metadata": {"name": "login-0"}, "status": {"phase": "Running"}}]}
                ),
                "",
            )
        if command[6:9] == ("login-0", "--", "scontrol"):
            return SoperatorMigrationCommandResult(
                command,
                1,
                "",
                "scontrol: fatal: Could not establish a configuration source",
            )
        if command[6:11] == (
            "login-0",
            "--",
            "env",
            f"SLURM_CONF={migration._SOPERATOR_LEGACY_SLURM_CONF}",  # noqa: SLF001
            "scontrol",
        ):
            return SoperatorMigrationCommandResult(
                command,
                1,
                "",
                "scontrol: fatal: Could not establish a configuration source",
            )
        if command[6:11] == ("controller-0", "-c", "slurmctld", "--", "scontrol"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "\n".join(
                    (
                        "NodeName=worker-0 Arch=x86_64 CoresPerSocket=8",
                        "   NodeAddr=10.1.18.140 NodeHostName=worker-0 Version=24.11.6 Partitions=main",
                        "   InstanceId=computeinstance-e00worker0",
                    )
                ),
                "",
            )
        if command[6:9] == ("login-0", "--", "squeue"):
            return SoperatorMigrationCommandResult(
                command,
                1,
                "",
                "squeue: fatal: Could not establish a configuration source",
            )
        if command[6:11] == (
            "login-0",
            "--",
            "env",
            f"SLURM_CONF={migration._SOPERATOR_LEGACY_SLURM_CONF}",  # noqa: SLF001
            "squeue",
        ):
            return SoperatorMigrationCommandResult(
                command,
                1,
                "",
                "squeue: fatal: Could not establish a configuration source",
            )
        if command[6:11] == ("controller-0", "-c", "slurmctld", "--", "squeue"):
            if "PENDING" in command:
                return SoperatorMigrationCommandResult(command, 0, "", "")
            return SoperatorMigrationCommandResult(
                command,
                0,
                "42|user|RUNNING|main|worker-0|00:01|00:30|00:29|long-job\n",
                "",
            )
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    jobs = migration._external_upgrade_slurm_jobs(
        command_runner=runner,
        kube_context="external-context",
        node_names=("computeinstance-e00worker0",),
    )

    assert [job.job_id for job in jobs] == ["42"]
    assert [job.allocated_nodes for job in jobs] == ["worker-0"]
    assert any(call[6:11] == ("controller-0", "-c", "slurmctld", "--", "squeue") for call in calls)


def test_kubectl_exec_login_retries_slurm_with_legacy_conf_before_controller() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {"items": [{"metadata": {"name": "login-0"}, "status": {"phase": "Running"}}]}
                ),
                "",
            )
        if command[6:9] == ("login-0", "--", "squeue"):
            return SoperatorMigrationCommandResult(
                command,
                1,
                "",
                "squeue: error: resolve_ctls_from_dns_srv: res_nsearch error: Unknown host\n"
                "squeue: fatal: Could not establish a configuration source",
            )
        if command[6:11] == (
            "login-0",
            "--",
            "env",
            f"SLURM_CONF={migration._SOPERATOR_LEGACY_SLURM_CONF}",  # noqa: SLF001
            "squeue",
        ):
            return SoperatorMigrationCommandResult(command, 0, "RUNNING\n", "")
        return SoperatorMigrationCommandResult(command, 1, "", "unexpected command")

    result = migration._kubectl_exec_login(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        args=("squeue", "-h", "-o", "%T"),
        check=False,
        timeout_seconds=7,
    )

    assert result.returncode == 0
    assert result.stdout == "RUNNING\n"
    assert result.args[8:11] == (
        "env",
        f"SLURM_CONF={migration._SOPERATOR_LEGACY_SLURM_CONF}",  # noqa: SLF001
        "squeue",
    )
    assert not any(call[6:11] == ("controller-0", "-c", "slurmctld", "--", "squeue") for call in calls)


def test_kubectl_exec_login_falls_back_to_controller_after_timeout() -> None:
    calls: list[tuple[str, ...]] = []
    timeouts: list[int] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        timeouts.append(timeout_seconds)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {"items": [{"metadata": {"name": "login-0"}, "status": {"phase": "Running"}}]}
                ),
                "",
            )
        if command[6:9] == ("login-0", "--", "squeue"):
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        if command[6:11] == (
            "login-0",
            "--",
            "env",
            f"SLURM_CONF={migration._SOPERATOR_LEGACY_SLURM_CONF}",  # noqa: SLF001
            "squeue",
        ):
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        if command[6:11] == ("controller-0", "-c", "slurmctld", "--", "squeue"):
            return SoperatorMigrationCommandResult(command, 0, "", "")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    result = migration._kubectl_exec_login(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        args=("squeue", "-h", "-o", "%T"),
        check=False,
        timeout_seconds=7,
    )

    assert result.returncode == 0
    assert result.args[6:11] == ("controller-0", "-c", "slurmctld", "--", "squeue")
    assert 7 in timeouts
    assert any(call[6:9] == ("login-0", "--", "squeue") for call in calls)


def test_login_service_identity_guard_rejects_public_ip_change() -> None:
    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="changed load_balancer_external",
    ):
        migration._assert_login_service_identity_preserved(  # noqa: SLF001
            before=(
                {
                    "name": "external-soperator-login-svc",
                    "uid": "old-service",
                    "cluster_ip": "10.0.0.10",
                    "load_balancer_external": ["203.0.113.146"],
                    "load_balancer_allocation_id": "vpcallocation-old",
                },
            ),
            after=(
                {
                    "name": "external-soperator-login-svc",
                    "uid": "old-service",
                    "cluster_ip": "10.0.0.10",
                    "load_balancer_external": ["203.0.113.147"],
                    "load_balancer_allocation_id": "vpcallocation-old",
                },
            ),
        )


def test_login_service_load_balancer_precondition_requires_allocation_annotation() -> None:
    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="missing annotation nebius.com/load-balancer-allocation-id",
    ):
        migration._assert_login_service_stable_load_balancer_preconditions(  # noqa: SLF001
            (
                {
                    "name": "external-soperator-login-svc",
                    "type": "LoadBalancer",
                    "load_balancer_type": "internal",
                    "load_balancer_external": ["10.10.20.30"],
                    "load_balancer_allocation_id": "",
                },
            )
        )


def test_login_service_load_balancer_allocation_auto_converts_and_persists_values() -> None:
    runner = _FakeCommandRunner(
        allocations=[
            {
                "metadata": {
                    "id": "vpcallocation-login",
                    "name": "login",
                    "labels": {
                        "nebius.com/managed-by": "mk8s",
                        "customer": "keep",
                    },
                },
                "spec": {"ipv4_public": {"cidr": "203.0.113.10/32"}},
                "status": {"details": {"allocated_cidr": "203.0.113.10/32"}},
            }
        ]
    )
    runner.live_services[0]["metadata"]["annotations"] = {}
    values: dict[str, Any] = {}

    identities, decisions, lines = migration.stabilize_soperator_login_load_balancer_allocations(
        command_runner=runner,
        kube_context="external-context",
        project_id="project-1",
        nebius_api=runner.nebius_api,
        values=values,
    )

    assert identities[0]["load_balancer_allocation_id"] == "vpcallocation-login"
    assert [decision.as_payload() for decision in decisions] == [
        {
            "service_name": "login",
            "status": "converted-dynamic",
            "address": "203.0.113.10",
            "load_balancer_type": "external",
            "allocation_id": "vpcallocation-login",
            "allocation_cidr": "203.0.113.10/32",
            "removed_labels": ["nebius.com/managed-by"],
            "persisted_to_values": True,
        }
    ]
    assert "Converted dynamic login LoadBalancer allocation" in lines[0]
    assert runner.live_services[0]["metadata"]["annotations"] == {
        "nebius.com/load-balancer-allocation-id": "vpcallocation-login",
    }
    assert runner.allocations[0]["metadata"]["labels"] == {"customer": "keep"}
    assert values["slurmNodes"]["login"]["sshdServiceAnnotations"] == {
        "nebius.com/load-balancer-allocation-id": "vpcallocation-login",
    }
    assert ("allocation.list", "project-1") in runner.nebius_api.calls
    assert (
        "allocation.update_labels",
        "vpcallocation-login",
        {"customer": "keep"},
        300,
    ) in runner.nebius_api.calls


def test_login_service_load_balancer_allocation_persists_internal_type() -> None:
    runner = _FakeCommandRunner(
        allocations=[
            {
                "metadata": {"id": "vpcallocation-login-private", "labels": {}},
                "spec": {"ipv4_private": {"cidr": "10.10.20.30/32"}},
                "status": {"details": {"allocated_cidr": "10.10.20.30/32"}},
            }
        ]
    )
    runner.live_services[0] = {
        "metadata": {
            "name": "login",
            "namespace": "soperator",
            "uid": "svc-login",
            "annotations": {"nebius.com/load-balancer-type": "internal"},
        },
        "spec": {"type": "LoadBalancer", "clusterIP": "10.0.0.10"},
        "status": {"loadBalancer": {"ingress": [{"ip": "10.10.20.30"}]}},
    }
    values: dict[str, Any] = {}

    _identities, decisions, _lines = (
        migration.stabilize_soperator_login_load_balancer_allocations(
            command_runner=runner,
            kube_context="external-context",
            project_id="project-1",
            nebius_api=runner.nebius_api,
            values=values,
        )
    )

    assert decisions[0].allocation_id == "vpcallocation-login-private"
    assert runner.live_services[0]["metadata"]["annotations"] == {
        "nebius.com/load-balancer-type": "internal",
        "nebius.com/load-balancer-allocation-id": "vpcallocation-login-private",
    }
    assert values["slurmNodes"]["login"]["sshdServiceAnnotations"] == {
        "nebius.com/load-balancer-allocation-id": "vpcallocation-login-private",
        "nebius.com/load-balancer-type": "internal",
    }


@pytest.mark.parametrize(
    ("allocations", "match"),
    [
        (
            [
                {
                    "metadata": {"id": "vpcallocation-other", "labels": {}},
                    "spec": {"ipv4_public": {"cidr": "203.0.113.11/32"}},
                    "status": {"details": {"allocated_cidr": "203.0.113.11/32"}},
                }
            ],
            "could not find a unique external Nebius VPC allocation",
        ),
        (
            [
                {
                    "metadata": {"id": "vpcallocation-a", "labels": {}},
                    "spec": {"ipv4_public": {"cidr": "203.0.113.10/32"}},
                    "status": {"details": {"allocated_cidr": "203.0.113.10/32"}},
                },
                {
                    "metadata": {"id": "vpcallocation-b", "labels": {}},
                    "spec": {"ipv4_public": {"cidr": "203.0.113.10/32"}},
                    "status": {"details": {"allocated_cidr": "203.0.113.10/32"}},
                },
            ],
            "matched multiple Nebius VPC allocations",
        ),
        (
            [
                {
                    "metadata": {"id": "vpcallocation-private", "labels": {}},
                    "spec": {"ipv4_private": {"cidr": "203.0.113.10/32"}},
                    "status": {"details": {"allocated_cidr": "203.0.113.10/32"}},
                }
            ],
            "could not find a unique external Nebius VPC allocation",
        ),
    ],
)
def test_login_service_load_balancer_allocation_failures_block_handoff(
    allocations: list[dict[str, Any]],
    match: str,
) -> None:
    runner = _FakeCommandRunner(allocations=allocations)
    runner.live_services[0]["metadata"]["annotations"] = {}

    with pytest.raises(migration.SoperatorMigrationPhasePending, match=match):
        migration.stabilize_soperator_login_load_balancer_allocations(
            command_runner=runner,
            kube_context="external-context",
            project_id="project-1",
            nebius_api=runner.nebius_api,
            values={},
        )


def test_login_service_load_balancer_allocation_config_mismatch_blocks_handoff() -> None:
    runner = _FakeCommandRunner()
    values = {
        "slurmNodes": {
            "login": {
                "sshdServiceAnnotations": {
                    "nebius.com/load-balancer-allocation-id": "vpcallocation-other",
                }
            }
        }
    }

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="already declares nebius.com/load-balancer-allocation-id=vpcallocation-other",
    ):
        migration.stabilize_soperator_login_load_balancer_allocations(
            command_runner=runner,
            kube_context="external-context",
            project_id="project-1",
            nebius_api=runner.nebius_api,
            values=values,
        )


def test_login_service_identity_required_fails_closed_on_kubectl_error() -> None:
    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        return SoperatorMigrationCommandResult(command, 1, "", "rbac denied")

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="could not list Services",
    ):
        migration._login_service_identities(  # noqa: SLF001
            command_runner=runner,
            kube_context="external-context",
            required=True,
        )


def test_login_service_identity_required_fails_closed_on_malformed_json() -> None:
    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        return SoperatorMigrationCommandResult(command, 0, "{", "")

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="invalid Service JSON",
    ):
        migration._login_service_identities(  # noqa: SLF001
            command_runner=runner,
            kube_context="external-context",
            required=True,
        )


def test_wait_active_login_session_policy_leaves_phase_pending() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {"items": [{"metadata": {"name": "login-0"}, "status": {"phase": "Running"}}]}
                ),
                "",
            )
        if command[6:10] == ("login-0", "--", "sh", "-ceu"):
            return SoperatorMigrationCommandResult(command, 0, "1\n", "")
        return SoperatorMigrationCommandResult(command, 1, "", "unexpected command")

    phase: dict[str, Any] = {}
    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="active SSH session",
    ):
        migration._wait_for_login_session_policy(  # noqa: SLF001
            phase=phase,
            command_runner=runner,
            kube_context="external-context",
            policy=migration.EXTERNAL_LOGIN_SESSION_POLICY_WAIT_ACTIVE,
            timeout_seconds=0,
        )

    drain = phase["login_continuity"]["session_drain"]
    assert drain["status"] == "pending"
    assert drain["active_sessions"] == 1
    assert any(call[6:10] == ("login-0", "--", "sh", "-ceu") for call in calls)


def test_wait_active_login_session_policy_checks_only_source_login_pods() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command[6:10] == ("source-login-0", "--", "sh", "-ceu"):
            return SoperatorMigrationCommandResult(command, 0, "0\n", "")
        if command[6:10] == ("target-login-0", "--", "sh", "-ceu"):
            return SoperatorMigrationCommandResult(command, 0, "1\n", "")
        return SoperatorMigrationCommandResult(command, 1, "", "unexpected command")

    phase: dict[str, Any] = {}
    lines = migration._wait_for_login_session_policy(  # noqa: SLF001
        phase=phase,
        command_runner=runner,
        kube_context="external-context",
        policy=migration.EXTERNAL_LOGIN_SESSION_POLICY_WAIT_ACTIVE,
        timeout_seconds=0,
        pod_names=("source-login-0",),
    )

    assert lines == ["Login continuity: active SSH sessions drained before source retirement."]
    assert any(call[6:10] == ("source-login-0", "--", "sh", "-ceu") for call in calls)
    assert not any(call[6:10] == ("target-login-0", "--", "sh", "-ceu") for call in calls)


def test_external_upgrade_slurm_jobs_fails_closed_for_invalid_node_name() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {"items": [{"metadata": {"name": "login-0"}, "status": {"phase": "Running"}}]}
                ),
                "",
            )
        if command[8:] == ("scontrol", "show", "nodes"):
            return SoperatorMigrationCommandResult(command, 1, "", "slurmctld unavailable")
        if command[8:10] == ("squeue", "-h") and "-w" in command:
            return SoperatorMigrationCommandResult(
                command, 1, "", "squeue: error: Invalid node name computeinstance-1"
            )
        if command[8:10] == ("squeue", "-h"):
            return SoperatorMigrationCommandResult(command, 0, "", "")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    with pytest.raises(RuntimeError, match="Slurm rejected the scoped node filter"):
        migration._external_upgrade_slurm_jobs(  # noqa: SLF001
            command_runner=runner,
            kube_context="external-context",
            node_names=("computeinstance-1",),
        )

    squeue_calls = [call for call in calls if call[8:10] == ("squeue", "-h")]
    assert len(squeue_calls) == 1
    assert squeue_calls[0][-2:] == ("-w", "computeinstance-1")


def test_external_upgrade_slurm_jobs_include_scoped_pending_jobs() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {"items": [{"metadata": {"name": "login-0"}, "status": {"phase": "Running"}}]}
                ),
                "",
            )
        if command[8:] == ("scontrol", "show", "nodes"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "NodeName=worker-0 NodeHostName=worker-0 Partitions=main",
                "",
            )
        if command[8:] == ("scontrol", "show", "node", "worker-0", "-o"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "NodeName=worker-0 Partitions=main State=IDLE",
                "",
            )
        if (
            command[8:10] == ("squeue", "-h")
            and "PENDING" in command
            and command[-2:]
            == (
                "-p",
                "main",
            )
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "12|root|PENDING|main||||Priority|0:00|35:00|35:00|pending-main\n",
                "",
            )
        if command[8:10] == ("squeue", "-h") and "PENDING" in command:
            return SoperatorMigrationCommandResult(
                command,
                0,
                (
                    "12|root|PENDING|main||||Priority|0:00|35:00|35:00|pending-main\n"
                    "13|root|PENDING|other||||Resources|0:00|35:00|35:00|unrelated\n"
                    "14|root|PENDING|other||worker-0||Priority|0:00|35:00|35:00|pending-node\n"
                ),
                "",
            )
        if (
            command[8:10] == ("squeue", "-h")
            and migration._EXTERNAL_UPGRADE_ACTIVE_SQUEUE_STATES in command
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "10|root|STOPPED|main|worker-0|||None|0:09|35:00|34:51|stopped\n",
                "",
            )
        if command[8:10] == ("squeue", "-h"):
            return SoperatorMigrationCommandResult(command, 0, "", "")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    jobs = migration._external_upgrade_slurm_jobs(
        command_runner=runner,
        kube_context="external-context",
        node_names=("worker-0",),
    )

    assert [job.job_id for job in jobs] == ["10", "12", "14"]
    assert jobs[0].state == "STOPPED"
    assert jobs[1].impact_scope == "pending-partition"
    assert jobs[2].impact_scope == "pending-node"
    assert any(call[-2:] == ("-p", "main") for call in calls if "PENDING" in call)
    assert any(call[-2:] != ("-p", "main") for call in calls if "PENDING" in call)


def test_external_upgrade_prompt_selector_includes_pending_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_job = migration.AffectedSlurmJob(
        job_id="12",
        user="root",
        state="PENDING",
        partition="main",
        allocated_nodes="",
        requested_nodes="",
        scheduled_nodes="",
        reason="Priority",
        elapsed="0:00",
        limit="35:00",
        remaining="35:00",
        name="pending-main",
        impact_scope="pending-partition",
    )
    captured: dict[str, object] = {}

    def _prompt_slurm_job_control(*args: object, **kwargs: object) -> tuple[str, tuple[str, ...]]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return ("cancel-selected", ("12",))

    monkeypatch.setattr(migration, "_external_upgrade_is_tty_session", lambda: True)
    monkeypatch.setattr(migration, "prompt_slurm_job_control", _prompt_slurm_job_control)

    action, selected = migration._prompt_external_upgrade_slurm_job_control(
        (pending_job,),
        prompt_pause=None,
    )

    assert action == "cancel-selected"
    assert selected == ("12",)
    assert captured["args"][0] == (pending_job,)
    assert captured["kwargs"]["is_tty"] is True


def test_external_upgrade_slurm_jobs_fail_closed_when_partition_lookup_fails() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {"items": [{"metadata": {"name": "login-0"}, "status": {"phase": "Running"}}]}
                ),
                "",
            )
        if command[8:] == ("scontrol", "show", "nodes"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "NodeName=worker-0 NodeHostName=worker-0 Partitions=main",
                "",
            )
        if (
            command[8:10] == ("squeue", "-h")
            and migration._EXTERNAL_UPGRADE_ACTIVE_SQUEUE_STATES in command
        ):
            return SoperatorMigrationCommandResult(command, 0, "", "")
        if command[8:] == ("scontrol", "show", "node", "worker-0", "-o"):
            return SoperatorMigrationCommandResult(command, 1, "", "slurmctld unavailable")
        if command[8:10] == ("squeue", "-h"):
            raise AssertionError("pending squeue should not run after partition lookup failure")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    with pytest.raises(RuntimeError, match="could not inspect Slurm partitions"):
        migration._external_upgrade_slurm_jobs(
            command_runner=runner,
            kube_context="external-context",
            node_names=("worker-0",),
        )

    assert not any(call[8:10] == ("squeue", "-h") and "PENDING" in call for call in calls)


def test_external_upgrade_fail_policy_pauses_status_for_job_table() -> None:
    calls: list[tuple[str, ...]] = []
    pause_events: list[str] = []

    @contextmanager
    def _pause() -> Iterator[None]:
        pause_events.append("pause")
        yield
        pause_events.append("resume")

    with pytest.raises(migration.SoperatorMigrationPhasePending):
        migration._handle_external_upgrade_slurm_jobs(
            command_runner=_external_upgrade_slurm_runner(
                queue_outputs=[
                    "42|alice|RUNNING|gpu|worker-gpu-0-0|worker-gpu-0-0||None|00:05|30:00|25:00|active\n"
                ],
                calls=calls,
            ),
            kube_context="external-context",
            node_names=("worker-gpu-0-0",),
            policy="fail",
            cancel_job_ids=(),
            requeue_job_ids=(),
            wait_timeout_seconds=0,
            refresh_interval_seconds=1,
            interactive_prompt_pause=_pause,
        )

    assert pause_events == ["pause", "resume"]


def test_external_upgrade_interactive_policy_rejects_non_tty_before_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(migration, "_external_upgrade_is_tty_session", lambda: False)

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        return SoperatorMigrationCommandResult(command, 0, "", "")

    with pytest.raises(RuntimeError, match="interactive terminal"):
        migration._handle_external_upgrade_slurm_jobs(
            command_runner=runner,
            kube_context="external-context",
            node_names=("worker-0",),
            policy="interactive",
            cancel_job_ids=(),
            requeue_job_ids=(),
            wait_timeout_seconds=0,
            refresh_interval_seconds=1,
        )

    assert calls == []


def test_external_upgrade_job_policy_defaults_by_terminal_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(migration, "_external_upgrade_is_tty_session", lambda: False)
    assert migration._external_upgrade_job_policy(None) == "fail"  # noqa: SLF001

    monkeypatch.setattr(migration, "_external_upgrade_is_tty_session", lambda: True)
    assert migration._external_upgrade_job_policy(None) == "interactive"  # noqa: SLF001
    assert (
        migration._external_upgrade_job_policy(None, default_policy="fail") == "fail"  # noqa: SLF001
    )
    assert migration._external_upgrade_job_policy("wait-to-finish") == "wait-to-finish"  # noqa: SLF001

    monkeypatch.setattr(migration, "_external_upgrade_is_tty_session", lambda: False)
    assert (
        migration._external_upgrade_job_policy(
            "interactive",
            allow_resolved_interactive=True,
        )
        == "interactive"
    )
    with pytest.raises(RuntimeError, match="interactive terminal"):
        migration._external_upgrade_job_policy("interactive")  # noqa: SLF001
    with pytest.raises(RuntimeError, match="wait-to-finish"):
        migration._external_upgrade_job_policy("wait")  # noqa: SLF001


def test_external_upgrade_resolved_interactive_policy_skips_late_tty_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(migration, "_external_upgrade_is_tty_session", lambda: False)

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=_external_upgrade_slurm_runner(
            queue_outputs=[""],
            calls=calls,
        ),
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="interactive",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=0,
        refresh_interval_seconds=1,
        allow_resolved_interactive_job_policy=True,
    )

    assert lines == ["Slurm job preflight: no affected jobs in the upgrade scope."]
    assert any(command[8:10] == ("squeue", "-h") for command in calls)


def test_external_upgrade_wait_then_cancel_requires_positive_timeout() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        return SoperatorMigrationCommandResult(command, 0, "", "")

    with pytest.raises(RuntimeError, match="positive --job-wait-timeout"):
        migration._handle_external_upgrade_slurm_jobs(
            command_runner=runner,
            kube_context="external-context",
            node_names=("worker-0",),
            policy="wait-then-cancel",
            cancel_job_ids=(),
            requeue_job_ids=(),
            wait_timeout_seconds=0,
            refresh_interval_seconds=1,
        )

    assert calls == []


def test_external_upgrade_slurm_quiet_rechecks_after_partition_drain() -> None:
    calls: list[tuple[str, ...]] = []

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="started or remained after Slurm partition drain",
    ):
        migration._ensure_slurm_quiet(
            command_runner=_external_upgrade_slurm_runner(
                queue_outputs=[
                    "",
                    "99|root|RUNNING|gpu|worker-gpu-0-0|||None|0:01|30:00|29:59|late-job\n",
                ],
                calls=calls,
            ),
            kube_context="external-context",
            node_names=("worker-gpu-0-0",),
            job_policy="fail",
            cancel_job_ids=(),
            requeue_job_ids=(),
            job_wait_timeout_seconds=0,
            job_refresh_interval_seconds=1,
        )

    drain_index = next(
        index
        for index, call in enumerate(calls)
        if call[8:] == ("scontrol", "update", "PartitionName=ALL", "State=DRAIN")
    )
    active_squeue_indices = [
        index
        for index, call in enumerate(calls)
        if (
            call[8:10] == ("squeue", "-h")
            and migration._EXTERNAL_UPGRADE_ACTIVE_SQUEUE_STATES in call
        )
    ]
    assert len(active_squeue_indices) == 2
    assert active_squeue_indices[-1] > drain_index
    assert any(
        call[8:] == ("scontrol", "update", "PartitionName=ALL", "State=UP") for call in calls
    )


def test_populate_jail_refresh_phase_job_policy_fail_blocks_before_helm_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _FakeCommandRunner(
        live_pods=[
            {
                "metadata": {"name": "login-0"},
                "status": {"phase": "Running"},
            },
            {
                "metadata": {
                    "name": "worker-gpu-0",
                    "labels": {"slurm.nebius.ai/nodeset-name": "worker-gpu"},
                },
                "status": {"phase": "Running"},
            },
        ],
        slurm_queue_output=(
            "42|alice|RUNNING|gpu|worker-gpu-0|worker-gpu-0||None|"
            "00:05|30:00|25:00|active\n"
        ),
    )
    helm_calls: list[Mapping[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "_patch_target_values_for_compute",
        lambda **_kwargs: {
            "maintenance": "none",
            "populateJail": {"overwrite": False},
            "externalNfs": {
                "enabled": True,
                "server": "nfs.example.invalid",
                "path": "/exports/home",
                "mountPath": "/home",
            },
        },
    )
    monkeypatch.setattr(
        migration,
        "_helm_upgrade_target_soperator",
        lambda **kwargs: helm_calls.append(kwargs),
    )
    checkpoint: dict[str, Any] = {
        "phase_state": {},
        "worker_node_groups": ["gpu-pool"],
    }

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="Affected Slurm jobs"):
        migration._execute_populate_jail_refresh_phase(  # noqa: SLF001
            checkpoint=checkpoint,
            payload={},
            source_report={"report": {"target_version": "4.0.1"}},
            live_snapshot={},
            target_ref="external-cluster",
            kube_context="external-context",
            command_runner=runner,
            populate_jail_refresh="force",
            job_policy="fail",
            cancel_job_ids=(),
            requeue_job_ids=(),
            job_wait_timeout_seconds=0,
            job_refresh_interval_seconds=1,
            login_session_policy=migration.EXTERNAL_LOGIN_SESSION_POLICY_WAIT_ACTIVE,
            login_session_drain_timeout_seconds=0,
        )

    assert helm_calls == []
    assert any(
        command[8:10] == ("squeue", "-h") for command, _input_text in runner.calls
    )
    assert not any(
        command[8:] == ("scontrol", "update", "PartitionName=ALL", "State=DRAIN")
        for command, _input_text in runner.calls
    )


def test_populate_jail_refresh_phase_capacity_fail_blocks_before_slurm_and_helm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _FakeCommandRunner(
        live_pods=[
            {"metadata": {"name": "login-0"}, "status": {"phase": "Running"}},
            {
                "metadata": {
                    "name": "worker-gpu-0",
                    "labels": {"slurm.nebius.ai/nodeset-name": "worker-gpu"},
                },
                "status": {"phase": "Running"},
            },
        ],
        slurm_queue_output=(
            "42|alice|RUNNING|gpu|worker-gpu-0|worker-gpu-0||None|"
            "00:05|30:00|25:00|active\n"
        ),
    )
    helm_calls: list[Mapping[str, Any]] = []
    probe_kwargs: dict[str, Any] = {}
    target_values = migration.apply_jail_persistent_mount_values(
        {
            "populateJail": {"overwrite": False},
            "externalNfs": {
                "enabled": True,
                "server": "nfs.example.invalid",
                "path": "/exports/home",
                "mountPath": "/home",
            },
        },
        target_ref="external-cluster",
        layout="external",
    )
    monkeypatch.setattr(
        migration,
        "_patch_target_values_for_compute",
        lambda **_kwargs: target_values,
    )

    def _fake_probe_active_passive_jail_capacity(*_args: Any, **kwargs: Any) -> Any:
        probe_kwargs.update(kwargs)
        return evaluate_jail_capacity(
            passive_available_bytes=8 * GIB,
            active_used_bytes=80 * GIB,
        )

    monkeypatch.setattr(
        migration,
        "probe_active_passive_jail_capacity",
        _fake_probe_active_passive_jail_capacity,
    )
    monkeypatch.setattr(
        migration,
        "_helm_upgrade_target_soperator",
        lambda **kwargs: helm_calls.append(kwargs),
    )
    checkpoint: dict[str, Any] = {
        "phase_state": {},
        "worker_node_groups": ["gpu-pool"],
    }

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="passive jail rootfs"):
        migration._execute_populate_jail_refresh_phase(  # noqa: SLF001
            checkpoint=checkpoint,
            payload={},
            source_report={"report": {"target_version": "4.0.1"}},
            live_snapshot={},
            target_ref="external-cluster",
            kube_context="external-context",
            command_runner=runner,
            populate_jail_refresh="force",
            job_policy="fail",
            cancel_job_ids=(),
            requeue_job_ids=(),
            job_wait_timeout_seconds=0,
            job_refresh_interval_seconds=1,
            login_session_policy=migration.EXTERNAL_LOGIN_SESSION_POLICY_WAIT_ACTIVE,
            login_session_drain_timeout_seconds=0,
        )

    populate_phase = checkpoint["phase_state"]["populate-jail-refresh"]
    assert populate_phase["capacity_preflight"]["status"] == "failed"
    assert populate_phase["rootfs_slots"]["passive_pvc"] == "jail-rootfs-slot-b-pvc"
    assert probe_kwargs["active_pvc"] == "jail-pvc"
    assert probe_kwargs["passive_pvc"] == "jail-pvc"
    assert probe_kwargs["active_rootfs_path"] == "/mnt/jail"
    assert checkpoint["populate_jail_refresh"]["capacity_preflight"]["shortage_gib"] == 92
    assert helm_calls == []
    assert not any("squeue" in " ".join(command) for command, _input_text in runner.calls)


def test_populate_jail_refresh_phase_completed_migration_does_not_reserve_copy_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    target_values = migration.apply_jail_persistent_mount_values(
        {"nodesets": [{"name": "worker"}]},
        target_ref="external-cluster",
        layout="external",
    )
    payload["apps"]["charts"][0]["values"] = target_values
    runner = _FakeCommandRunner()
    probe_kwargs: dict[str, Any] = {}
    monkeypatch.setattr(
        migration,
        "_patch_target_values_for_compute",
        lambda **_kwargs: target_values,
    )

    def _fake_probe_active_passive_jail_capacity(*_args: Any, **kwargs: Any) -> Any:
        probe_kwargs.update(kwargs)
        return evaluate_jail_capacity(
            passive_available_bytes=8 * GIB,
            active_used_bytes=80 * GIB,
        )

    monkeypatch.setattr(
        migration,
        "probe_active_passive_jail_capacity",
        _fake_probe_active_passive_jail_capacity,
    )
    checkpoint: dict[str, Any] = {
        "phase_state": {
            "populate-jail-refresh": {
                "legacy_persistent_mount_migration": {
                    "status": "completed",
                    "entries": [
                        {
                            "mount_path": "/home",
                            "source_path": "/mnt/jail/home",
                            "target_local_path": "/mnt/jail/shared/home",
                            "status": "completed",
                        }
                    ],
                }
            }
        },
        "worker_node_groups": ["gpu-pool"],
    }

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="passive jail rootfs"):
        migration._execute_populate_jail_refresh_phase(  # noqa: SLF001
            checkpoint=checkpoint,
            payload=payload,
            source_report={"report": {"target_version": "4.0.1"}},
            live_snapshot={},
            target_ref="external-cluster",
            kube_context="external-context",
            command_runner=runner,
            populate_jail_refresh="force",
            job_policy="fail",
            cancel_job_ids=(),
            requeue_job_ids=(),
            job_wait_timeout_seconds=0,
            job_refresh_interval_seconds=1,
            login_session_policy=migration.EXTERNAL_LOGIN_SESSION_POLICY_WAIT_ACTIVE,
            login_session_drain_timeout_seconds=0,
        )

    populate_phase = checkpoint["phase_state"]["populate-jail-refresh"]
    assert populate_phase["legacy_persistent_mount_migration"]["status"] == "completed"
    assert probe_kwargs["extra_required_paths"] == ()
    assert probe_kwargs["exclude_paths"] == (
        "/mnt/jail/.cxcli",
        "/mnt/jail/shared/home",
        "/mnt/jail/shared/data",
        "/mnt/jail/shared/scripts",
        "/mnt/jail/shared/models",
        "/mnt/jail/home",
        "/mnt/jail/data",
        "/mnt/jail/scripts",
        "/mnt/jail/models",
    )


def test_populate_jail_refresh_phase_migrates_legacy_persistent_mounts_before_populate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    target_values = migration.apply_jail_persistent_mount_values(
        {
            "nodesets": [
                {
                    "name": "worker",
                    "slurmd": {
                        "volumes": {
                            "jail": {
                                "persistentVolumeClaim": {"claimName": "jail-pvc"},
                            }
                        }
                    },
                }
            ],
        },
        target_ref="external-cluster",
        persistent_mounts=migration.parse_jail_persistent_mount_specs(
            ("/data=/mnt/jail/shared/data",)
        ),
        layout="external",
    )
    payload["apps"]["charts"][0]["values"] = target_values
    runner = _FakeCommandRunner()
    helm_calls: list[Mapping[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "_patch_target_values_for_compute",
        lambda **_kwargs: target_values,
    )
    monkeypatch.setattr(
        migration,
        "_helm_upgrade_target_soperator",
        lambda **kwargs: helm_calls.append(kwargs),
    )
    checkpoint: dict[str, Any] = {
        "phase_state": {},
        "worker_node_groups": ["gpu-pool"],
    }

    mutated, lines = migration._execute_populate_jail_refresh_phase(  # noqa: SLF001
        checkpoint=checkpoint,
        payload=payload,
        source_report=_source_report(),
        live_snapshot={},
        target_ref="external-cluster",
        kube_context="external-context",
        command_runner=runner,
        populate_jail_refresh="force",
        job_policy="fail",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout_seconds=0,
        job_refresh_interval_seconds=1,
        login_session_policy=migration.EXTERNAL_LOGIN_SESSION_POLICY_WAIT_ACTIVE,
        login_session_drain_timeout_seconds=0,
    )

    assert mutated is True
    assert any(line.startswith("Persistent mount migration job completed:") for line in lines)
    assert len(helm_calls) == 2
    populate_phase = checkpoint["phase_state"]["populate-jail-refresh"]
    migration_state = populate_phase["legacy_persistent_mount_migration"]
    assert migration_state["status"] == "completed"
    assert [entry["status"] for entry in migration_state["entries"]] == [
        "completed",
        "completed",
        "completed",
        "completed",
    ]
    source_status_by_path = {
        entry["mount_path"]: entry["source_status"] for entry in migration_state["entries"]
    }
    assert source_status_by_path == {
        "/home": "present",
        "/data": "present",
        "/scripts": "absent",
        "/models": "absent",
    }
    copy_status_by_path = {
        entry["mount_path"]: entry["copy_status"] for entry in migration_state["entries"]
    }
    assert copy_status_by_path == {
        "/home": "copied",
        "/data": "copied",
        "/scripts": "source_missing",
        "/models": "source_missing",
    }
    decision_status_by_path = {
        entry["mount_path"]: entry["status"]
        for entry in checkpoint["persistent_jail_mounts"]["decisions"]
    }
    assert decision_status_by_path == {
        "/home": "present",
        "/data": "present",
        "/scripts": "absent",
        "/models": "absent",
    }
    decision_copy_status_by_path = {
        entry["mount_path"]: entry.get("copy_status")
        for entry in checkpoint["persistent_jail_mounts"]["decisions"]
    }
    assert decision_copy_status_by_path == {
        "/home": "copied",
        "/data": "copied",
        "/scripts": "source_missing",
        "/models": "source_missing",
    }
    source_probe = populate_phase["legacy_persistent_mount_source_probe"]
    assert source_probe["status"] == "completed"
    assert {entry["mount_path"] for entry in source_probe["entries"]} == {
        "/home",
        "/data",
        "/scripts",
        "/models",
    }
    assert populate_phase["persistent_migration_writer_hold"]["status"] == "restored"
    assert populate_phase["login_service_ready_before_switch"]["status"] == "skipped"
    assert checkpoint["populate_jail_refresh"]["legacy_persistent_mount_migration"][
        "status"
    ] == "completed"

    applied_jobs: list[tuple[int, Mapping[str, Any]]] = []
    for index, (command, input_text) in enumerate(runner.calls):
        if command != ("kubectl", "--context", "external-context", "apply", "-f", "-"):
            continue
        try:
            payload_text = json.loads(input_text or "{}")
        except json.JSONDecodeError:
            payload_text = migration.yaml.safe_load(input_text or "{}") or {}
        for item in payload_text.get("items", []):
            if isinstance(item, Mapping) and item.get("kind") == "Job":
                applied_jobs.append((index, item))

    capacity_job_index, capacity_job = next(
        (index, job)
        for index, job in applied_jobs
        if str(job["metadata"]["name"]).endswith("-jail-capacity-probe")
    )
    source_probe_job_index, source_probe_job = next(
        (index, job)
        for index, job in applied_jobs
        if job["metadata"]["labels"]["app.kubernetes.io/component"]
        == "jail-persistent-source-probe"
    )
    migration_job_index, migration_job = next(
        (index, job)
        for index, job in applied_jobs
        if job["metadata"]["labels"]["app.kubernetes.io/component"]
        == "jail-persistent-migration"
    )
    populate_job_index, populate_job = next(
        (index, job)
        for index, job in applied_jobs
        if job["metadata"]["labels"]["app.kubernetes.io/component"] == "populate-jail"
    )
    assert source_probe_job_index < capacity_job_index < migration_job_index < populate_job_index
    assert (
        source_probe_job["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
        == [{"name": "jail-store", "mountPath": "/store", "readOnly": True}]
    )

    capacity_script = capacity_job["spec"]["template"]["spec"]["containers"][0]["command"][-1]
    assert "subtract_path /mnt/active/home" in capacity_script
    assert "subtract_path /mnt/active/data" in capacity_script
    assert "subtract_path /mnt/active/scripts" in capacity_script
    assert "subtract_path /mnt/active/models" in capacity_script
    assert "add_extra_required_path /mnt/active/home" in capacity_script
    assert "add_extra_required_path /mnt/active/data" in capacity_script
    assert "add_extra_required_path /mnt/active/scripts" not in capacity_script
    assert "add_extra_required_path /mnt/active/models" not in capacity_script

    migration_spec = migration_job["spec"]["template"]["spec"]
    assert migration_spec["volumes"] == [
        {"name": "jail-store", "persistentVolumeClaim": {"claimName": "jail-pvc"}}
    ]
    container = migration_spec["containers"][0]
    assert container["volumeMounts"] == [{"name": "jail-store", "mountPath": "/store"}]
    script = container["command"][-1]
    assert "copy_entry /store/home /store/shared/home" in script
    assert "copy_entry /store/data /store/shared/data" in script
    assert "copy_entry /store/scripts /store/shared/scripts" in script
    assert "copy_entry /store/models /store/shared/models" in script
    assert "/store/.cxcli/persistent-migrations/home.json" in script
    assert "/store/.cxcli/persistent-migrations/data.json" in script
    assert "/store/.cxcli/persistent-migrations/scripts.json" in script
    assert "/store/.cxcli/persistent-migrations/models.json" in script
    assert "marker_matches()" in script
    assert "return 1" in script
    assert "source_missing marker is stale" in script
    assert '\\"source_path\\":\\"$source_path\\"' in script
    assert "marker does not match this copy" in script
    assert "tar --xattrs --acls --numeric-owner -cpf - ." in script
    assert "target is non-empty without marker" in script

    populate_spec = populate_job["spec"]["template"]["spec"]
    assert populate_spec["volumes"] == [
        {
            "name": "jail-rootfs",
            "persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"},
        }
    ]


def test_populate_jail_refresh_target_ready_blocks_before_login_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    target_values = migration.apply_jail_persistent_mount_values(
        {"nodesets": [{"name": "worker"}]},
        target_ref="external-cluster",
        layout="external",
    )
    payload["apps"]["charts"][0]["values"] = target_values
    runner = _FakeCommandRunner()
    monkeypatch.setattr(
        migration,
        "_patch_target_values_for_compute",
        lambda **_kwargs: target_values,
    )
    checkpoint: dict[str, Any] = {
        "phase_state": {},
        "worker_node_groups": ["gpu-pool"],
    }

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="target-ready login session policy refuses",
    ):
        migration._execute_populate_jail_refresh_phase(  # noqa: SLF001
            checkpoint=checkpoint,
            payload=payload,
            source_report=_source_report(),
            live_snapshot={},
            target_ref="external-cluster",
            kube_context="external-context",
            command_runner=runner,
            populate_jail_refresh="force",
            job_policy="fail",
            cancel_job_ids=(),
            requeue_job_ids=(),
            job_wait_timeout_seconds=0,
            job_refresh_interval_seconds=1,
        )

    hold_policy = checkpoint["phase_state"]["populate-jail-refresh"][
        "persistent_migration_login_hold_policy"
    ]
    assert hold_policy["status"] == "pending"
    assert not any(
        command[:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "scale",
            "statefulsets.apps.kruise.io/login",
        )
        for command, _input_text in runner.calls
    )


def test_persistent_mount_migration_marker_requires_valid_status(tmp_path: Path) -> None:
    store = tmp_path / "store"
    source = store / "home"
    target = store / "shared" / "home"
    marker = store / ".cxcli" / "persistent-migrations" / "home.json"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "status": "started",
                "mount_path": "/home",
                "source_path": str(source),
                "target_path": str(target),
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    script = migration._persistent_mount_migration_shell_command(  # noqa: SLF001
        [
            {
                "mount_path": "/home",
                "source_store_path": "/home",
                "target_store_path": "/shared/home",
                "marker_store_path": "/.cxcli/persistent-migrations/home.json",
            }
        ]
    ).replace("/store", str(store))

    result = subprocess.run(["bash", "-c", script], check=False, capture_output=True, text=True)

    assert result.returncode == 16
    assert "marker does not match this copy" in result.stderr


def test_persistent_mount_migration_source_missing_marker_fails_if_source_appears(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    source = store / "home"
    target = store / "shared" / "home"
    marker = store / ".cxcli" / "persistent-migrations" / "home.json"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "status": "source_missing",
                "mount_path": "/home",
                "source_path": str(source),
                "target_path": str(target),
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    script = migration._persistent_mount_migration_shell_command(  # noqa: SLF001
        [
            {
                "mount_path": "/home",
                "source_store_path": "/home",
                "target_store_path": "/shared/home",
                "marker_store_path": "/.cxcli/persistent-migrations/home.json",
            }
        ]
    ).replace("/store", str(store))

    result = subprocess.run(["bash", "-c", script], check=False, capture_output=True, text=True)

    assert result.returncode == 18
    assert "source_missing marker is stale" in result.stderr


def test_populate_jail_refresh_phase_restores_writers_when_migration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    target_values = migration.apply_jail_persistent_mount_values(
        {
            "nodesets": [
                {
                    "name": "worker",
                    "slurmd": {
                        "volumes": {
                            "jail": {
                                "persistentVolumeClaim": {"claimName": "jail-pvc"},
                            }
                        }
                    },
                }
            ],
        },
        target_ref="external-cluster",
        layout="external",
    )
    payload["apps"]["charts"][0]["values"] = target_values
    runner = _FakeCommandRunner()
    monkeypatch.setattr(
        migration,
        "_patch_target_values_for_compute",
        lambda **_kwargs: target_values,
    )
    monkeypatch.setattr(
        migration,
        "_helm_upgrade_target_soperator",
        lambda **_kwargs: pytest.fail("populate values must not apply after copy failure"),
    )

    def _fail_persistent_migration_job(*_args: Any, **kwargs: Any) -> None:
        if kwargs.get("job_label") == "Soperator persistent mount migration Job":
            raise RuntimeError("persistent copy failed")

    monkeypatch.setattr(
        migration,
        "_wait_for_job_complete_or_failed",
        _fail_persistent_migration_job,
    )
    checkpoint: dict[str, Any] = {
        "phase_state": {},
        "worker_node_groups": ["gpu-pool"],
    }

    with pytest.raises(RuntimeError, match="persistent copy failed"):
        migration._execute_populate_jail_refresh_phase(  # noqa: SLF001
            checkpoint=checkpoint,
            payload=payload,
            source_report=_source_report(),
            live_snapshot={},
            target_ref="external-cluster",
            kube_context="external-context",
            command_runner=runner,
            populate_jail_refresh="force",
            job_policy="fail",
            cancel_job_ids=(),
            requeue_job_ids=(),
            job_wait_timeout_seconds=0,
            job_refresh_interval_seconds=1,
            login_session_policy=migration.EXTERNAL_LOGIN_SESSION_POLICY_WAIT_ACTIVE,
            login_session_drain_timeout_seconds=0,
        )

    populate_phase = checkpoint["phase_state"]["populate-jail-refresh"]
    assert populate_phase["persistent_migration_writer_hold"]["status"] == "restored"
    assert "slurm_resumed_after_failure_at" in populate_phase
    commands = [call for call, _input_text in runner.calls]
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "scale",
        "statefulsets.apps.kruise.io/login",
        "--replicas",
        "0",
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "scale",
        "statefulsets.apps.kruise.io/login",
        "--replicas",
        "1",
    ) in commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "patch",
        "nodeset/worker",
        "--type",
        "merge",
        "-p",
        '{"spec": {"replicas": 2}}',
    ) in commands
    assert any(
        command[8:] == ("scontrol", "update", "PartitionName=ALL", "State=UP")
        for command in commands
    )


def test_populate_jail_refresh_phase_keeps_writers_held_after_completed_copy_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    target_values = migration.apply_jail_persistent_mount_values(
        {
            "nodesets": [
                {
                    "name": "worker",
                    "slurmd": {
                        "volumes": {
                            "jail": {
                                "persistentVolumeClaim": {"claimName": "jail-pvc"},
                            }
                        }
                    },
                }
            ],
        },
        target_ref="external-cluster",
        layout="external",
    )
    payload["apps"]["charts"][0]["values"] = target_values
    runner = _FakeCommandRunner()
    monkeypatch.setattr(
        migration,
        "_patch_target_values_for_compute",
        lambda **_kwargs: target_values,
    )
    monkeypatch.setattr(
        migration,
        "_helm_upgrade_target_soperator",
        lambda **_kwargs: None,
    )

    def _fail_passive_populate_job(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("passive populate failed after persistent copy")

    monkeypatch.setattr(
        migration,
        "wait_for_active_passive_populate_jail_job",
        _fail_passive_populate_job,
    )
    checkpoint: dict[str, Any] = {
        "phase_state": {},
        "worker_node_groups": ["gpu-pool"],
    }

    with pytest.raises(RuntimeError, match="passive populate failed after persistent copy"):
        migration._execute_populate_jail_refresh_phase(  # noqa: SLF001
            checkpoint=checkpoint,
            payload=payload,
            source_report=_source_report(),
            live_snapshot={},
            target_ref="external-cluster",
            kube_context="external-context",
            command_runner=runner,
            populate_jail_refresh="force",
            job_policy="fail",
            cancel_job_ids=(),
            requeue_job_ids=(),
            job_wait_timeout_seconds=0,
            job_refresh_interval_seconds=1,
            login_session_policy=migration.EXTERNAL_LOGIN_SESSION_POLICY_WAIT_ACTIVE,
            login_session_drain_timeout_seconds=0,
        )

    populate_phase = checkpoint["phase_state"]["populate-jail-refresh"]
    assert populate_phase["legacy_persistent_mount_migration"]["status"] == "completed"
    assert populate_phase["persistent_migration_writer_hold"]["status"] == "held"
    assert populate_phase["persistent_migration_failure_boundary"] == {
        "status": "writers_held",
        "reason": (
            "persistent mount migration already completed; keeping legacy writers "
            "stopped so the shared copy cannot become stale"
        ),
    }
    assert populate_phase["slurm_resume_after_failure"]["status"] == "skipped"
    commands = [call for call, _input_text in runner.calls]
    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "scale",
        "statefulsets.apps.kruise.io/login",
        "--replicas",
        "1",
    ) not in commands
    assert not any(
        command[8:] == ("scontrol", "update", "PartitionName=ALL", "State=UP")
        for command in commands
    )


def test_prepare_jail_persistent_mount_payload_patches_target_values() -> None:
    payload = _payload()
    payload["apps"]["charts"][0]["values"] = {"nodesets": [{"name": "worker"}]}

    patched, state = migration._prepare_jail_persistent_mount_payload(  # noqa: SLF001
        payload=payload,
        target_ref="external-cluster",
        jail_persistent_mounts=("/data=/mnt/jail/shared/data",),
        populate_jail_refresh="auto",
    )

    values = patched["apps"]["charts"][0]["values"]
    assert state["status"] == "planned"
    assert state["copy_required"] is True
    assert state["auto_preserve_paths"] == ["/home", "/data", "/scripts", "/models"]
    decision_status = {
        item["mount_path"]: item["status"]
        for item in state["decisions"]
    }
    assert decision_status == {
        "/home": "pending-probe",
        "/data": "explicit",
        "/scripts": "pending-probe",
        "/models": "pending-probe",
    }
    assert values["jailPersistentMounts"] == [
        {"mountPath": "/home", "localPath": "/mnt/jail/shared/home"},
        {"mountPath": "/data", "localPath": "/mnt/jail/shared/data"},
        {"mountPath": "/scripts", "localPath": "/mnt/jail/shared/scripts"},
        {"mountPath": "/models", "localPath": "/mnt/jail/shared/models"},
    ]
    assert state["migration"]["status"] == "planned"
    assert state["migration"]["entries"] == [
        {
            "name": "jail-persistent-migration-home",
            "mount_path": "/home",
            "source_path": "/mnt/jail/home",
            "source_store_path": "/home",
            "target_local_path": "/mnt/jail/shared/home",
            "target_store_path": "/shared/home",
            "marker_path": "/mnt/jail/.cxcli/persistent-migrations/home.json",
            "marker_store_path": "/.cxcli/persistent-migrations/home.json",
            "bytes_planned": None,
            "pvc_name": "",
            "status": "planned",
        },
        {
            "name": "jail-persistent-migration-data",
            "mount_path": "/data",
            "source_path": "/mnt/jail/data",
            "source_store_path": "/data",
            "target_local_path": "/mnt/jail/shared/data",
            "target_store_path": "/shared/data",
            "marker_path": "/mnt/jail/.cxcli/persistent-migrations/data.json",
            "marker_store_path": "/.cxcli/persistent-migrations/data.json",
            "bytes_planned": None,
            "pvc_name": "",
            "status": "planned",
        },
        {
            "name": "jail-persistent-migration-scripts",
            "mount_path": "/scripts",
            "source_path": "/mnt/jail/scripts",
            "source_store_path": "/scripts",
            "target_local_path": "/mnt/jail/shared/scripts",
            "target_store_path": "/shared/scripts",
            "marker_path": "/mnt/jail/.cxcli/persistent-migrations/scripts.json",
            "marker_store_path": "/.cxcli/persistent-migrations/scripts.json",
            "bytes_planned": None,
            "pvc_name": "",
            "status": "planned",
        },
        {
            "name": "jail-persistent-migration-models",
            "mount_path": "/models",
            "source_path": "/mnt/jail/models",
            "source_store_path": "/models",
            "target_local_path": "/mnt/jail/shared/models",
            "target_store_path": "/shared/models",
            "marker_path": "/mnt/jail/.cxcli/persistent-migrations/models.json",
            "marker_store_path": "/.cxcli/persistent-migrations/models.json",
            "bytes_planned": None,
            "pvc_name": "",
            "status": "planned",
        },
    ]
    assert values["jailRootfs"]["store"]["rootfsPath"] == "/mnt/jail/.cxcli/rootfs"
    assert values["jailRootfs"]["adoption"]["activeSource"] == "legacy-rootfs"
    assert "sfs" not in values
    phase_ids = migration._phase_ids_with_jail_persistent_mount_prerequisites(  # noqa: SLF001
        phase_ids=("customer-approval",),
        payload=patched,
        target_ref="external-cluster",
    )
    assert phase_ids == ("customer-approval", "populate-jail-refresh")


def test_prepare_jail_persistent_mount_payload_preserves_existing_external_home() -> None:
    payload = _payload()
    payload["apps"]["charts"][0]["values"] = {
        "externalNfs": {
            "enabled": True,
            "mountPath": "/home",
            "server": "nfs.example.invalid",
            "path": "/exports/home",
        }
    }

    patched, state = migration._prepare_jail_persistent_mount_payload(  # noqa: SLF001
        payload=payload,
        target_ref="external-cluster",
        jail_persistent_mounts=(),
        populate_jail_refresh="auto",
    )

    assert state["status"] == "verified"
    assert state["copy_required"] is True
    assert {
        item["mount_path"]: item["status"]
        for item in state["decisions"]
    } == {
        "/home": "existing-submount",
        "/data": "pending-probe",
        "/scripts": "pending-probe",
        "/models": "pending-probe",
    }
    assert state["migration"]["status"] == "planned"
    assert patched["apps"]["charts"][0]["values"]["jailPersistentMounts"] == [
        {"mountPath": "/data", "localPath": "/mnt/jail/shared/data"},
        {"mountPath": "/scripts", "localPath": "/mnt/jail/shared/scripts"},
        {"mountPath": "/models", "localPath": "/mnt/jail/shared/models"},
    ]


def test_external_upgrade_cancel_selected_policy_cancels_jobs() -> None:
    calls: list[tuple[str, ...]] = []
    queue_outputs = [
        "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train\n",
        "",
    ]

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=_external_upgrade_slurm_runner(
            queue_outputs=queue_outputs,
            calls=calls,
        ),
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="cancel-selected",
        cancel_job_ids=("42",),
        requeue_job_ids=(),
        wait_timeout_seconds=0,
        refresh_interval_seconds=1,
    )

    assert lines == ["Slurm job preflight: cleared affected jobs with policy cancel-selected."]
    assert any(call[8:] == ("scancel", "42") for call in calls)


def test_external_upgrade_cancel_selected_policy_cancels_pending_jobs() -> None:
    calls: list[tuple[str, ...]] = []
    pending_outputs = [
        "12|alice|PENDING|gpu||worker-gpu-0-0||Priority|0:00|30:00|30:00|pending-train\n",
        "",
    ]

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=_external_upgrade_slurm_runner(
            queue_outputs=["", ""],
            pending_queue_outputs=pending_outputs,
            calls=calls,
        ),
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="cancel-selected",
        cancel_job_ids=("12",),
        requeue_job_ids=(),
        wait_timeout_seconds=0,
        refresh_interval_seconds=1,
    )

    assert lines == ["Slurm job preflight: cleared affected jobs with policy cancel-selected."]
    assert any(call[8:] == ("scancel", "12") for call in calls)


def test_external_upgrade_requeue_selected_rejects_pending_jobs() -> None:
    calls: list[tuple[str, ...]] = []
    pending_outputs = [
        "12|alice|PENDING|gpu||worker-gpu-0-0||Priority|0:00|30:00|30:00|pending-train\n"
    ]

    with pytest.raises(RuntimeError, match="cannot requeue pending Slurm job"):
        migration._handle_external_upgrade_slurm_jobs(
            command_runner=_external_upgrade_slurm_runner(
                queue_outputs=[""],
                pending_queue_outputs=pending_outputs,
                calls=calls,
            ),
            kube_context="external-context",
            node_names=("worker-gpu-0-0",),
            policy="requeue-selected",
            cancel_job_ids=(),
            requeue_job_ids=("12",),
            wait_timeout_seconds=0,
            refresh_interval_seconds=1,
        )

    assert not any(call[8:10] == ("scontrol", "requeue") for call in calls)


def test_external_upgrade_cancel_all_policy_cancels_all_jobs() -> None:
    calls: list[tuple[str, ...]] = []
    queue_outputs = [
        "\n".join(
            (
                "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train",
                "43|bob|RUNNING|gpu|worker-gpu-0-0|00:02|30:00|28:00|restartable-eval",
            )
        )
        + "\n",
        "",
    ]

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=_external_upgrade_slurm_runner(
            queue_outputs=queue_outputs,
            calls=calls,
        ),
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="cancel-all",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=0,
        refresh_interval_seconds=1,
    )

    assert lines == ["Slurm job preflight: cleared affected jobs with policy cancel-all."]
    assert any(call[8:] == ("scancel", "42", "43") for call in calls)


def test_external_upgrade_wait_to_finish_policy_waits_until_jobs_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    queue_outputs = [
        "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train\n",
        "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train\n",
        "",
    ]
    sleeps: list[int] = []
    monkeypatch.setattr(migration.time, "sleep", lambda seconds: sleeps.append(seconds))

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=_external_upgrade_slurm_runner(
            queue_outputs=queue_outputs,
            calls=calls,
        ),
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="wait-to-finish",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=30,
        refresh_interval_seconds=1,
    )

    assert lines == ["Slurm job preflight: waited for affected jobs to finish."]
    assert sleeps == [1]
    assert queue_outputs == []


def test_external_upgrade_interactive_wait_completed_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    queue_outputs = [
        "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train\n",
    ]
    prompted: list[tuple[str, tuple[str, ...]]] = []

    def _prompt(
        jobs: Sequence[migration.AffectedSlurmJob],
        **_kwargs: object,
    ) -> tuple[str, tuple[str, ...]]:
        prompted.append(("prompted", tuple(job.job_id for job in jobs)))
        return migration.SLURM_JOB_CONTROL_WAIT_COMPLETED, ()

    monkeypatch.setattr(
        migration,
        "_prompt_external_upgrade_slurm_job_control",
        _prompt,
    )

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=_external_upgrade_slurm_runner(
            queue_outputs=queue_outputs,
            calls=calls,
        ),
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="interactive",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=30,
        refresh_interval_seconds=1,
        allow_resolved_interactive_job_policy=True,
    )

    assert lines == ["Slurm job preflight: waited for affected jobs to finish."]
    assert prompted == [("prompted", ("42",))]
    assert queue_outputs == []


def test_external_upgrade_interactive_wait_timeout_raises_without_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    queue_outputs = [
        "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train\n",
    ]

    monkeypatch.setattr(
        migration,
        "_prompt_external_upgrade_slurm_job_control",
        lambda _jobs, **_kwargs: (migration.SLURM_JOB_CONTROL_WAIT_TIMEOUT, ("42",)),
    )

    with pytest.raises(RuntimeError, match="Timed out waiting for Slurm jobs"):
        migration._handle_external_upgrade_slurm_jobs(
            command_runner=_external_upgrade_slurm_runner(
                queue_outputs=queue_outputs,
                calls=calls,
            ),
            kube_context="external-context",
            node_names=("worker-gpu-0-0",),
            policy="interactive",
            cancel_job_ids=(),
            requeue_job_ids=(),
            wait_timeout_seconds=30,
            refresh_interval_seconds=1,
            allow_resolved_interactive_job_policy=True,
        )

    assert not any(call[8:9] == ("scancel",) for call in calls)


def test_external_upgrade_wait_then_cancel_waits_then_cancels_displayed_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    decisions: list[dict[str, Any]] = []
    active_job = migration.AffectedSlurmJob(
        job_id="42",
        user="alice",
        state="RUNNING",
        partition="gpu",
        allocated_nodes="worker-gpu-0-0",
        requested_nodes="",
        scheduled_nodes="",
        reason="",
        elapsed="00:05",
        limit="30:00",
        remaining="25:00",
        name="restartable-train",
        impact_scope="allocated-node",
    )
    pending_job = migration.AffectedSlurmJob(
        job_id="43",
        user="alice",
        state="PENDING",
        partition="gpu",
        allocated_nodes="",
        requested_nodes="worker-gpu-0-0",
        scheduled_nodes="",
        reason="Priority",
        elapsed="00:00",
        limit="30:00",
        remaining="30:00",
        name="pending-train",
        impact_scope="pending-node",
    )
    wait_results = [(active_job, pending_job), ()]

    monkeypatch.setattr(
        migration,
        "_wait_for_external_upgrade_slurm_jobs_until_timeout",
        lambda **_kwargs: wait_results.pop(0),
    )

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=_external_upgrade_slurm_runner(
            queue_outputs=[
                "42|alice|RUNNING|gpu|worker-gpu-0-0||||00:05|30:00|25:00|restartable-train\n",
                "42|alice|RUNNING|gpu|worker-gpu-0-0||||00:05|30:00|25:00|restartable-train\n",
            ],
            pending_queue_outputs=[
                "43|alice|PENDING|gpu||worker-gpu-0-0||Priority|00:00|30:00|30:00|pending-train\n",
                "",
                "43|alice|PENDING|gpu||worker-gpu-0-0||Priority|00:00|30:00|30:00|pending-train\n",
                "",
            ],
            calls=calls,
        ),
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="wait-then-cancel",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=3600,
        refresh_interval_seconds=30,
        decision_recorder=decisions.append,
    )

    assert lines == ["Slurm job preflight: waited, cancelled timed-out affected jobs, and cleared."]
    assert any(call[8:] == ("scancel", "42", "43") for call in calls)
    assert [decision["action"] for decision in decisions] == [
        "blocking-jobs-detected",
        "wait-then-cancel-wait-started",
        "wait-then-cancel-timeout",
        "wait-then-cancel-auto-cancel",
        "wait-then-cancel-cleared",
    ]


def test_external_upgrade_non_terminal_wait_prints_once_per_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed: list[Any] = []
    sleeps: list[float] = []
    active_job = migration.AffectedSlurmJob(
        job_id="42",
        user="alice",
        state="RUNNING",
        partition="gpu",
        allocated_nodes="worker-gpu-0-0",
        requested_nodes="",
        scheduled_nodes="",
        reason="",
        elapsed="00:05",
        limit="30:00",
        remaining="25:00",
        name="restartable-train",
        impact_scope="allocated-node",
    )
    job_polls = [(active_job,), ()]

    class _FakeConsole:
        is_terminal = False

        def print(self, value: Any, **_kwargs: Any) -> None:
            printed.append(value)

    monkeypatch.setattr(migration, "_console", _FakeConsole())
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_jobs",
        lambda **_kwargs: job_polls.pop(0),
    )
    monkeypatch.setattr(migration.time, "sleep", lambda seconds: sleeps.append(seconds))

    remaining = migration._wait_for_external_upgrade_slurm_jobs_until_timeout(  # noqa: SLF001
        command_runner=_external_upgrade_slurm_runner(queue_outputs=[], calls=[]),
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        timeout_seconds=0,
        refresh_interval_seconds=30,
    )

    assert remaining == ()
    assert sleeps == [30]
    assert len(printed) == 2
    assert str(printed[0].title).startswith("Waiting for affected Slurm jobs")
    assert printed[1] == "No affected Slurm jobs remain"


def test_external_upgrade_non_terminal_wait_retries_transient_login_exec_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed: list[Any] = []
    sleeps: list[float] = []
    polls: list[object] = [
        RuntimeError(
            "External Soperator upgrade could not inspect Slurm jobs from a login pod: "
            'error: Internal error occurred: unable to upgrade connection: container not found ("sshd")'
        ),
        (),
    ]

    class _FakeConsole:
        is_terminal = False

        def print(self, value: Any, **_kwargs: Any) -> None:
            printed.append(value)

    def _jobs(**_kwargs: Any) -> tuple[migration.AffectedSlurmJob, ...]:
        result = polls.pop(0)
        if isinstance(result, RuntimeError):
            raise result
        return result

    monkeypatch.setattr(migration, "_console", _FakeConsole())
    monkeypatch.setattr(migration, "_external_upgrade_slurm_jobs", _jobs)
    monkeypatch.setattr(migration.time, "sleep", lambda seconds: sleeps.append(seconds))

    remaining = migration._wait_for_external_upgrade_slurm_jobs_until_timeout(  # noqa: SLF001
        command_runner=_external_upgrade_slurm_runner(queue_outputs=[], calls=[]),
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        timeout_seconds=0,
        refresh_interval_seconds=5,
    )

    assert remaining == ()
    assert sleeps == [5]
    assert str(printed[0]).startswith("Waiting for Slurm login readiness")
    assert printed[1] == "No affected Slurm jobs remain"


def test_external_upgrade_requeue_selected_policy_requeues_jobs() -> None:
    calls: list[tuple[str, ...]] = []
    queue_outputs = [
        "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train\n",
        "",
    ]

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "login-0"},
                                "status": {"phase": "Running"},
                            }
                        ]
                    }
                ),
                "",
            )
        if (
            command[:8]
            == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "exec",
                "login-0",
                "--",
            )
            and command[8:11] == ("scontrol", "show", "node")
            and command[-1] == "-o"
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                f"NodeName={command[11]} Partitions=gpu State=IDLE",
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ) and command[8:10] == ("squeue", "-h"):
            if "PENDING" in command:
                return SoperatorMigrationCommandResult(command, 0, "", "")
            return SoperatorMigrationCommandResult(command, 0, queue_outputs.pop(0), "")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=runner,
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="requeue-selected",
        cancel_job_ids=(),
        requeue_job_ids=("42",),
        wait_timeout_seconds=0,
        refresh_interval_seconds=1,
    )

    assert lines == ["Slurm job preflight: cleared affected jobs with policy requeue-selected."]
    assert any(call[8:] == ("scontrol", "requeue", "42") for call in calls)


def test_external_upgrade_requeue_hold_selected_policy_holds_jobs() -> None:
    calls: list[tuple[str, ...]] = []
    queue_outputs = [
        "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train\n",
        "",
    ]

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "login-0"},
                                "status": {"phase": "Running"},
                            }
                        ]
                    }
                ),
                "",
            )
        if (
            command[:8]
            == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "exec",
                "login-0",
                "--",
            )
            and command[8:11] == ("scontrol", "show", "node")
            and command[-1] == "-o"
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                f"NodeName={command[11]} Partitions=gpu State=IDLE",
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ) and command[8:10] == ("squeue", "-h"):
            if "PENDING" in command:
                return SoperatorMigrationCommandResult(command, 0, "", "")
            return SoperatorMigrationCommandResult(command, 0, queue_outputs.pop(0), "")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=runner,
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="requeue-hold-selected",
        cancel_job_ids=(),
        requeue_job_ids=("42",),
        wait_timeout_seconds=0,
        refresh_interval_seconds=1,
    )

    assert lines == [
        "Slurm job preflight: cleared affected jobs with policy requeue-hold-selected."
    ]
    assert any(call[8:] == ("scontrol", "requeuehold", "42") for call in calls)


def test_external_upgrade_requeue_hold_all_policy_holds_all_jobs() -> None:
    calls: list[tuple[str, ...]] = []
    queue_outputs = [
        "\n".join(
            (
                "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train",
                "43|bob|RUNNING|gpu|worker-gpu-0-0|00:02|30:00|28:00|restartable-eval",
            )
        )
        + "\n",
        "",
    ]

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "login-0"},
                                "status": {"phase": "Running"},
                            }
                        ]
                    }
                ),
                "",
            )
        if (
            command[:8]
            == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "exec",
                "login-0",
                "--",
            )
            and command[8:11] == ("scontrol", "show", "node")
            and command[-1] == "-o"
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                f"NodeName={command[11]} Partitions=gpu State=IDLE",
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ) and command[8:10] == ("squeue", "-h"):
            if "PENDING" in command:
                return SoperatorMigrationCommandResult(command, 0, "", "")
            return SoperatorMigrationCommandResult(command, 0, queue_outputs.pop(0), "")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=runner,
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="requeue-hold-all",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=0,
        refresh_interval_seconds=1,
    )

    assert lines == ["Slurm job preflight: cleared affected jobs with policy requeue-hold-all."]
    assert any(call[8:] == ("scontrol", "requeuehold", "42", "43") for call in calls)


def test_external_upgrade_requeue_policy_waits_for_slurm_transition(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    queue_outputs = [
        "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train\n",
        "42|alice|RUNNING|gpu|worker-gpu-0-0|00:05|30:00|25:00|restartable-train\n",
        "",
    ]
    sleeps: list[int] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "login-0"},
                                "status": {"phase": "Running"},
                            }
                        ]
                    }
                ),
                "",
            )
        if (
            command[:8]
            == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "exec",
                "login-0",
                "--",
            )
            and command[8:11] == ("scontrol", "show", "node")
            and command[-1] == "-o"
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                f"NodeName={command[11]} Partitions=gpu State=IDLE",
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ) and command[8:10] == ("squeue", "-h"):
            if "PENDING" in command:
                return SoperatorMigrationCommandResult(command, 0, "", "")
            return SoperatorMigrationCommandResult(command, 0, queue_outputs.pop(0), "")
        return SoperatorMigrationCommandResult(command, 0, "ok\n", "")

    monkeypatch.setattr(migration.time, "sleep", lambda seconds: sleeps.append(seconds))

    lines = migration._handle_external_upgrade_slurm_jobs(
        command_runner=runner,
        kube_context="external-context",
        node_names=("worker-gpu-0-0",),
        policy="requeue-all",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=30,
        refresh_interval_seconds=1,
    )

    assert lines == ["Slurm job preflight: cleared affected jobs with policy requeue-all."]
    assert any(call[8:] == ("scontrol", "requeue", "42") for call in calls)
    assert sleeps == [1]
    assert queue_outputs == []


def test_completed_migration_mk8s_check_fails_when_live_node_template_drifts() -> None:
    source_report = _source_report(target_k8s_version="1.34")
    runner = _FakeCommandRunner(
        cluster={
            "metadata": {"id": "cluster-123", "name": "external-cluster"},
            "spec": {"control_plane": {"version": "1.34"}},
        },
        existing_node_groups=[
            {
                "metadata": {
                    "id": "nodegroup-gpu-pool",
                    "name": "gpu-pool",
                    "parent_id": "cluster-123",
                },
                "spec": {
                    "version": "1.34",
                    "template": {
                        "resources": {
                            "platform": "gpu-h100-sxm",
                            "preset": "8gpu-128vcpu-1600gb",
                        },
                        "gpu_settings": {"drivers_preset": "cuda12.8"},
                        "os": "ubuntu22.04",
                    },
                },
            }
        ],
    )

    with pytest.raises(RuntimeError, match="live node template still needs update args"):
        migration._verify_completed_soperator_migration_mk8s_state(
            payload=_payload(target_k8s_version="1.34"),
            source_report=source_report,
            target_ref="external-cluster",
            worker_node_groups=("gpu-pool",),
            phase_ids=("external-node-template-upgrade",),
            nebius_api=runner.nebius_api,
            command_runner=runner,
        )


def test_completed_migration_mk8s_check_fails_when_status_is_missing() -> None:
    source_report = _source_report(target_k8s_version="1.34")
    runner = _FakeCommandRunner(
        cluster={
            "metadata": {"id": "cluster-123", "name": "external-cluster"},
            "spec": {"control_plane": {"version": "1.34"}},
        },
        existing_node_groups=[
            {
                "metadata": {
                    "id": "nodegroup-gpu-pool",
                    "name": "gpu-pool",
                    "parent_id": "cluster-123",
                },
                "spec": {
                    "version": "1.34",
                    "template": {
                        "resources": {
                            "platform": "gpu-h100-sxm",
                            "preset": "8gpu-128vcpu-1600gb",
                        },
                        "gpu_settings": {"drivers_preset": "cuda13.0"},
                        "os": {"name": "ubuntu24.04"},
                    },
                },
            }
        ],
    )

    with pytest.raises(RuntimeError, match="status not returned by Nebius API"):
        migration._verify_completed_soperator_migration_mk8s_state(
            payload=_payload(target_k8s_version="1.34"),
            source_report=source_report,
            target_ref="external-cluster",
            worker_node_groups=("gpu-pool",),
            phase_ids=("external-node-template-upgrade",),
            nebius_api=runner.nebius_api,
            command_runner=runner,
        )


def test_completed_migration_mk8s_check_fails_when_status_counts_are_missing() -> None:
    source_report = _source_report(target_k8s_version="1.34")
    runner = _FakeCommandRunner(
        cluster={
            "metadata": {"id": "cluster-123", "name": "external-cluster"},
            "spec": {"control_plane": {"version": "1.33"}},
        },
        existing_node_groups=[
            {
                "metadata": {
                    "id": "nodegroup-gpu-pool",
                    "name": "gpu-pool",
                    "parent_id": "cluster-123",
                },
                "spec": {
                    "version": "1.34",
                    "template": {
                        "resources": {
                            "platform": "gpu-h100-sxm",
                            "preset": "8gpu-128vcpu-1600gb",
                        },
                        "gpu_settings": {"drivers_preset": "cuda13.0"},
                        "os": {"name": "ubuntu24.04"},
                    },
                },
                "status": {"version": "1.34"},
            }
        ],
    )

    with pytest.raises(RuntimeError, match="status missing ready_node_count"):
        migration._verify_completed_soperator_migration_mk8s_state(
            payload=_payload(target_k8s_version="1.34"),
            source_report=source_report,
            target_ref="external-cluster",
            worker_node_groups=("gpu-pool",),
            phase_ids=("external-node-template-upgrade",),
            nebius_api=runner.nebius_api,
            command_runner=runner,
        )


def test_execute_rechecks_live_control_plane_before_skipping_completed_hop(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    source_report = _source_report()
    report = source_report["report"]
    assert isinstance(report, dict)
    report["migration_plan"] = [
        {"id": "discovery-and-plan", "status": "complete"},
        {
            "id": "customer-approval",
            "status": "planned",
            "requires_customer_approval": True,
        },
        {
            "id": "external-node-template-upgrade",
            "status": "planned",
            "requires_customer_approval": True,
        },
    ]
    payload = _payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    assert isinstance(target, dict)
    onboarding = target["soperator_onboarding"]
    assert isinstance(onboarding, dict)
    onboarding["actions"] = ["upgrade-external-node-template"]
    onboarding["node_template_upgrade"] = {
        "target_k8s_version": "1.32",
        "target_os": "ubuntu24.04",
        "target_gpu_stack_preset": "cuda13.0",
    }
    checkpoint_path = soperator_migration_checkpoint_path(
        config_path,
        "external-cluster",
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "source_report_fingerprint": migration._source_report_checkpoint_fingerprint(
                    source_report
                ),
                "source_version": "3.0.5",
                "target_version": "4.0.1-ps.1",
                "created_at": "2026-06-07T00:00:00Z",
                "completed_phases": ["discovery-and-plan", "customer-approval"],
                "customer_approved_at": "2026-06-07T00:00:00Z",
                "backup": _backup_metadata_with_archive(config_path, "control-plane-hop"),
                "events": [],
                "phase_state": {
                    "external-node-template-upgrade": {
                        "control_plane": {
                            "hops": {
                                "1.32": {
                                    "status": "completed",
                                    "target_version": "1.32",
                                }
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    runner = _FakeCommandRunner()

    result = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        command_runner=runner,
    )

    assert result.pending_phase == "none"
    cluster_updates = [
        call[0] for call in runner.calls if call[0][:4] == ("nebius", "mk8s", "cluster", "update")
    ]
    assert [
        command[command.index("--control-plane-version") + 1] for command in cluster_updates
    ] == ["1.32"]


def test_execute_auto_selects_console_worker_node_group_names_from_live_inventory(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot_with_compute_source()
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "soperator", "namespace": "soperator"},
            "spec": {
                "partitionConfiguration": {
                    "configType": "structured",
                    "partitions": [
                        {
                            "name": "gpu",
                            "nodeSetRefs": ["worker-gpu"],
                            "config": (
                                "Default=YES PriorityTier=20 MaxTime=INFINITE "
                                "State=UP OverSubscribe=YES"
                            ),
                        },
                        {
                            "name": "cpu",
                            "nodeSetRefs": ["worker-cpu"],
                            "config": (
                                "Default=NO PriorityTier=10 MaxTime=INFINITE "
                                "State=UP OverSubscribe=YES"
                            ),
                        },
                    ],
                }
            },
        },
        _source_worker_nodeset("worker-cpu", gpu=False),
        _source_worker_nodeset("worker-gpu", gpu=True),
    ]
    source_report = _source_report(snapshot)
    payload = _payload(include_placements=True)
    _seed_stale_kube_rbac_proxy_values(payload)
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    assert isinstance(target, dict)
    target["cluster_id"] = "cluster-123"
    runner = _FakeCommandRunner(
        existing_node_groups=[
            _legacy_service_node_group("system", fixed_node_count=3, preset="8vcpu-32gb"),
            _legacy_service_node_group("controller", preset="4vcpu-16gb"),
            _legacy_service_node_group("login", preset="16vcpu-64gb"),
            _legacy_service_node_group("accounting", preset="4vcpu-16gb"),
            {
                "metadata": {
                    "id": "nodegroup-worker-gpu",
                    "name": "worker-gpu-0",
                    "parent_id": "cluster-123",
                    "labels": {
                        "slurm.nebius.ai/nodeset": "worker-gpu",
                        "slurm.nebius.ai/workload": "gpu",
                    },
                },
                "spec": {
                    "fixed_node_count": 2,
                    "template": {
                        "metadata": {
                            "labels": {
                                "nebius.com/gpu": "true",
                                "slurm.nebius.ai/nodeset": "worker-gpu",
                                "slurm.nebius.ai/workload": "gpu",
                            }
                        },
                        "resources": {"platform": "gpu-h100-sxm", "preset": "8gpu-128vcpu-1600gb"},
                        "filesystems": [],
                    },
                },
                "status": _ready_node_group_status(nodes=2),
            },
            {
                "metadata": {
                    "id": "nodegroup-worker-cpu",
                    "name": "worker-cpu-0",
                    "parent_id": "cluster-123",
                    "labels": {
                        "slurm.nebius.ai/nodeset": "worker-cpu",
                        "slurm.nebius.ai/workload": "cpu",
                    },
                },
                "spec": {
                    "fixed_node_count": 2,
                    "template": {
                        "metadata": {
                            "labels": {
                                "slurm.nebius.ai/nodeset": "worker-cpu",
                                "slurm.nebius.ai/workload": "cpu",
                            }
                        },
                        "resources": {"platform": "cpu-d3", "preset": "16vcpu-64gb"},
                        "filesystems": [],
                    },
                },
                "status": _ready_node_group_status(nodes=2),
            },
        ],
        live_nodes=[
            {
                "metadata": {
                    "name": "gpu-node-a",
                    "labels": {
                        "nebius.com/node-group-id": "nodegroup-worker-gpu",
                        "node.kubernetes.io/instance-type": "gpu-h100-sxm",
                    },
                },
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "capacity": {"cpu": "128"},
                    "allocatable": {"cpu": "127900m", "nvidia.com/gpu": "8"},
                },
            },
            {
                "metadata": {
                    "name": "cpu-worker-a",
                    "labels": {
                        "nebius.com/node-group-id": "nodegroup-worker-cpu",
                        "node.kubernetes.io/instance-type": "cpu-d3",
                    },
                },
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "allocatable": {"cpu": "16"},
                },
            },
            {
                "metadata": {
                    "name": "system-a",
                    "labels": {
                        "nebius.com/node-group-id": "nodegroup-system",
                        "node.kubernetes.io/instance-type": "cpu-d3",
                    },
                },
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "allocatable": {"cpu": "8"},
                },
            },
        ],
        live_pods=[
            {
                "metadata": {
                    "name": "login-0",
                    "labels": {"app.kubernetes.io/component": "login"},
                },
                "status": {"phase": "Running"},
            },
            {
                "metadata": {
                    "name": "worker-gpu-0",
                    "labels": {"slurm.nebius.ai/nodeset-name": "worker-gpu"},
                },
                "status": {"phase": "Running"},
            },
        ],
        live_nodesets={
            "worker-cpu": {
                "metadata": {
                    "name": "worker-cpu",
                    "namespace": "soperator",
                    "annotations": {"meta.helm.sh/release-name": "soperator-nodesets"},
                },
                "spec": {
                    "replicas": 2,
                    "nodeConfig": {"dynamic": "", "static": "CPUs=15"},
                },
                "status": {"phase": "Ready", "replicas": 2},
            },
            "worker-gpu": {
                "metadata": {
                    "name": "worker-gpu",
                    "namespace": "soperator",
                    "annotations": {"meta.helm.sh/release-name": "soperator-nodesets"},
                },
                "spec": {
                    "replicas": 2,
                    "nodeConfig": {
                        "dynamic": "",
                        "static": (
                            "CPUs=128 Boards=1 SocketsPerBoard=2 CoresPerSocket=32 "
                            "ThreadsPerCore=2 Gres=gpu:8"
                        ),
                    },
                },
                "status": {"phase": "Ready", "replicas": 2},
            },
        },
        worker_topology_by_nodeset={
            "worker-gpu": {
                "cpus": 128,
                "sockets": 2,
                "cores_per_socket": 32,
                "threads_per_core": 2,
            }
        },
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "none"
    assert "Auto-selected source worker node groups: worker-gpu-0, worker-cpu-0" in "\n".join(
        result.lines
    )
    checkpoint = json.loads(
        soperator_migration_checkpoint_path(
            tmp_path / "config.yaml",
            "external-cluster",
        ).read_text(encoding="utf-8")
    )
    assert checkpoint["worker_node_groups"] == ["worker-gpu-0", "worker-cpu-0"]
    rolling = checkpoint["phase_state"]["rolling-compute-migration"]
    assert rolling["old_node_groups"] == {}
    assert rolling["in_place_worker_node_groups"] == ["worker-gpu-0", "worker-cpu-0"]
    assert rolling["in_place_worker_node_group_ids"] == {
        "worker-gpu-0": "nodegroup-worker-gpu",
        "worker-cpu-0": "nodegroup-worker-cpu",
    }
    assert {role: item["name"] for role, item in rolling["target_node_groups"].items()} == {
        "system": "system",
        "controller": "controller",
        "login": "login",
        "accounting": "accounting",
    }
    soperator_helm_upgrade = next(
        call
        for call in runner.calls
        if call[0][0] == "helm" and "upgrade" in call[0] and call[0][5] == "soperator"
    )
    helm_upgrade_index = next(
        index for index, call in enumerate(runner.calls) if call[0] == soperator_helm_upgrade[0]
    )
    legacy_nodeset_deletes = [
        (index, call[0])
        for index, call in enumerate(runner.calls)
        if call[0][:7]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "nodeset",
        )
    ]
    assert {call[7] for _index, call in legacy_nodeset_deletes} == {
        "worker-cpu",
        "worker-gpu",
    }
    assert all(index > helm_upgrade_index for index, _call in legacy_nodeset_deletes)
    assert soperator_helm_upgrade[1] is not None
    helm_values = json.loads(soperator_helm_upgrade[1])
    _assert_target_kube_rbac_proxy_values(helm_values)
    assert helm_values["slurmNodes"]["login"]["sshdServiceAnnotations"] == {
        "nebius.com/load-balancer-allocation-id": "vpcallocation-login",
    }
    system_terms = [
        {
            "matchExpressions": [
                {"key": "slurm.nebius.ai/nodeset-name", "operator": "In", "values": ["system"]}
            ]
        },
        {
            "matchExpressions": [
                {"key": "slurm.nebius.ai/nodeset", "operator": "In", "values": ["system"]}
            ]
        },
    ]
    assert (
        helm_values["controllerManager"]["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"]
        == system_terms
    )
    assert (
        helm_values["mariadb-operator"]["webhook"]["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"]
        == system_terms
    )
    assert {item["name"] for item in helm_values["nodesets"]} == {
        "worker-cpu",
        "worker-gpu",
    }
    worker_gpu = next(item for item in helm_values["nodesets"] if item["name"] == "worker-gpu")
    assert worker_gpu["gpu"]["enabled"] is True
    assert worker_gpu["slurmd"]["resources"]["gpu"] == 8
    assert (
        worker_gpu["nodeConfig"]["static"]
        == "CPUs=128 Boards=1 SocketsPerBoard=2 CoresPerSocket=32 ThreadsPerCore=2 Gres=gpu:8"
    )
    assert worker_gpu["slurmd"]["volumes"]["customVolumeMounts"] == [
        {
            "name": "site-scratch",
            "mountPath": "/site/scratch",
            "volumeSource": {"emptyDir": {}},
        }
    ]
    assert helm_values["gpuDriverJail"] == {"enabled": True}
    assert "image" not in worker_gpu["slurmd"]
    assert "image" not in worker_gpu["munge"]
    assert {
        partition["name"]: partition["nodeSetRefs"]
        for partition in helm_values["partitionConfiguration"]["partitions"]
    } == {
        "gpu": ["worker-gpu"],
        "cpu": ["worker-cpu"],
    }
    nodeset_patches = [
        call[0]
        for call in runner.calls
        if call[0][:7]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "patch",
            "nodeset",
        )
    ]
    assert {call[7] for call in nodeset_patches} == {"worker-cpu", "worker-gpu"}
    assert any(
        call[0][8:]
        == (
            "scontrol",
            "update",
            "NodeName=worker-gpu-[0-1]",
            "State=RESUME",
        )
        for call in runner.calls
    )
    assert not any(
        call[0][:4] == ("nebius", "mk8s", "node-group", "create") for call in runner.calls
    )


def test_reconcile_target_node_storage_labels_labels_exact_nodes() -> None:
    runner = _FakeCommandRunner(
        live_nodes=[
            {
                "metadata": {
                    "name": "accounting-node-a",
                    "labels": {"slurm.nebius.ai/nodeset-name": "accounting"},
                }
            },
            {
                "metadata": {
                    "name": "worker-node-a",
                    "labels": {"slurm.nebius.ai/nodeset": "worker"},
                }
            },
            {
                "metadata": {
                    "name": "unrelated-node-a",
                    "labels": {"slurm.nebius.ai/nodeset-name": "not-soperator"},
                }
            },
        ]
    )

    migration._reconcile_target_node_storage_labels(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
    )

    label_commands = [
        call[0]
        for call in runner.calls
        if call[0][:5] == ("kubectl", "--context", "external-context", "label", "node")
    ]
    assert (
        "kubectl",
        "--context",
        "external-context",
        "label",
        "node",
        "accounting-node-a",
        "slurm.nebius.ai/nodeset-name=accounting",
        "nebius.com/node-group=cpu",
        "--overwrite",
        "--request-timeout=20s",
    ) in label_commands
    assert not any("worker-node-a" in command for command in label_commands)
    assert not any(
        call[0][:5] == ("kubectl", "--context", "external-context", "label", "nodes")
        for call in runner.calls
    )


def test_reconcile_target_node_storage_labels_restores_specific_worker_labels() -> None:
    snapshot = _snapshot()
    snapshot["node_groups"] = {
        "worker-cpu-0": {
            "gpu": False,
            "node_count": 1,
            "labels": {
                "nebius.com/node-group": "worker-cpu-0",
                "nebius.com/node-group-id": "nodegroup-worker-cpu",
                "slurm.nebius.ai/nodeset": "worker",
                "slurm.nebius.ai/nodeset-name": "worker-cpu",
                "slurm.nebius.ai/workload": "cpu",
            },
            "nodes": ["worker-cpu-node"],
        },
        "worker-gpu-0": {
            "gpu": True,
            "node_count": 1,
            "labels": {
                "nebius.com/node-group": "worker-gpu-0",
                "nebius.com/node-group-id": "nodegroup-worker-gpu",
                "slurm.nebius.ai/nodeset": "worker",
                "slurm.nebius.ai/nodeset-name": "worker-gpu",
                "slurm.nebius.ai/workload": "gpu",
            },
            "allocatable": {"nvidia.com/gpu": "8"},
            "nodes": ["worker-gpu-node"],
        },
    }
    runner = _FakeCommandRunner(
        live_nodes=[
            {
                "metadata": {
                    "name": "worker-cpu-node",
                    "labels": {
                        "nebius.com/node-group": "gpu",
                        "nebius.com/node-group-id": "nodegroup-worker-cpu",
                        "slurm.nebius.ai/nodeset": "worker",
                        "slurm.nebius.ai/nodeset-name": "worker",
                    },
                }
            },
            {
                "metadata": {
                    "name": "worker-gpu-node",
                    "labels": {
                        "nebius.com/node-group": "gpu",
                        "nebius.com/node-group-id": "nodegroup-worker-gpu",
                        "slurm.nebius.ai/nodeset": "worker",
                        "slurm.nebius.ai/nodeset-name": "worker",
                    },
                }
            },
        ]
    )

    migration._reconcile_target_node_storage_labels(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        source_report=_source_report(snapshot),
        worker_node_groups=("worker-gpu-0", "worker-cpu-0"),
    )

    label_commands = [
        call[0]
        for call in runner.calls
        if call[0][:5] == ("kubectl", "--context", "external-context", "label", "node")
    ]
    assert (
        "kubectl",
        "--context",
        "external-context",
        "label",
        "node",
        "worker-cpu-node",
        "slurm.nebius.ai/nodeset-name=worker-cpu",
        "nebius.com/node-group=worker-cpu-0",
        "--overwrite",
        "--request-timeout=20s",
    ) in label_commands
    assert (
        "kubectl",
        "--context",
        "external-context",
        "label",
        "node",
        "worker-gpu-node",
        "slurm.nebius.ai/nodeset-name=worker-gpu",
        "nebius.com/node-group=worker-gpu-0",
        "--overwrite",
        "--request-timeout=20s",
    ) in label_commands
    assert "unrelated-node-a" not in {command[5] for command in label_commands if len(command) > 5}


def test_source_worker_nodeset_values_defaults_worker_proc_mount() -> None:
    nodeset = _source_worker_nodeset("worker-cpu", gpu=False)
    spec = nodeset["spec"]
    assert isinstance(spec, dict)
    slurmd = spec["slurmd"]
    munge = spec["munge"]
    assert isinstance(slurmd, dict)
    assert isinstance(munge, dict)
    slurmd["security"] = {"appArmorProfile": "unconfined", "limitsConfig": ""}
    munge["security"] = {
        "appArmorProfile": "unconfined",
        "limitsConfig": "",
        "procMount": "",
    }
    snapshot = _snapshot()
    snapshot["soperator_resources"] = [nodeset]

    values = migration._source_worker_nodeset_values(_source_report(snapshot))  # noqa: SLF001

    assert values[0]["slurmd"]["security"]["procMount"] == "Default"
    assert values[0]["munge"]["security"]["procMount"] == "Default"


def test_parse_slurmd_c_topology_with_l3cache_parameter() -> None:
    topology = migration._parse_slurmd_c_topology(  # noqa: SLF001
        "NodeName=worker-0 CPUs=128 Boards=1 SocketsPerBoard=2 "
        "CoresPerSocket=32 ThreadsPerCore=2 RealMemory=1612639 "
        "Parameters=l3cache_as_socket Gres=gpu:nvidia_h100_80gb_hbm3:8\n"
        "Found gpu:nvidia_h100_80gb_hbm3:8 with Autodetect=nvidia\n"
    )

    assert topology == {
        "cpus": 128,
        "boards": 1,
        "sockets": 2,
        "cores_per_socket": 32,
        "threads_per_core": 2,
        "parameters": "l3cache_as_socket",
    }


def test_patch_target_slurm_runtime_preserves_explicit_slurmd_parameters() -> None:
    values: dict[str, Any] = {
        "customSlurmConfig": "SlurmdParameters=numa_node_as_socket\nPluginDir=/old",
        "nodesets": [
            {
                "name": "worker",
                "nodeConfig": {"static": "CPUs=128 Gres=gpu:8"},
                "slurmd": {"resources": {"gpu": 8}},
            }
        ],
    }

    migration._patch_target_slurm_runtime(values)  # noqa: SLF001

    assert (
        values["customSlurmConfig"]
        == "SlurmdParameters=numa_node_as_socket\nPluginDir=/usr/lib/x86_64-linux-gnu/slurm"
    )


def test_source_worker_nodeset_values_uses_single_mismatched_worker_group_capacity() -> None:
    nodeset = _source_worker_nodeset("worker", gpu=False)
    spec = nodeset["spec"]
    assert isinstance(spec, dict)
    node_config = spec["nodeConfig"]
    assert isinstance(node_config, dict)
    node_config["static"] = (
        "Boards=1 SocketsPerBoard=1 CoresPerSocket=32 ThreadsPerCore=1 Gres=gpu:1 Port=6818"
    )
    spec["customInitContainers"] = [
        {"name": "cxcli-slurm-config-jail", "image": "chart-owned"},
        {"name": "cxcli-gpu-driver-jail", "image": "chart-owned"},
        {"name": "site-init", "image": "site/custom"},
    ]
    snapshot = _snapshot()
    snapshot["soperator_resources"] = [nodeset]
    snapshot["node_groups"] = {
        "worker-0-0": {
            "gpu": True,
            "node_count": 4,
            "labels": {
                "slurm.nebius.ai/nodeset": "worker-0",
                "nebius.com/node-group-id": "nodegroup-worker-0-0",
            },
            "allocatable": {"nvidia.com/gpu": "1"},
            "nodes": ["node-a"],
        }
    }
    live_snapshot = {
        "nodes": [
            {
                "metadata": {"name": "node-a"},
                "status": {"capacity": {"cpu": "16"}},
            }
        ]
    }

    values = migration._source_worker_nodeset_values(  # noqa: SLF001
        _source_report(snapshot),
        live_snapshot=live_snapshot,
    )

    assert values[0]["nodeConfig"]["static"] == "CPUs=16 Gres=gpu:1"
    assert values[0]["customInitContainers"] == [{"name": "site-init", "image": "site/custom"}]


def test_source_worker_nodeset_values_uses_single_group_allocatable_when_nodeset_label_differs() -> (
    None
):
    nodeset = _source_worker_nodeset("worker", gpu=False)
    spec = nodeset["spec"]
    assert isinstance(spec, dict)
    node_config = spec["nodeConfig"]
    assert isinstance(node_config, dict)
    node_config["static"] = (
        "Boards=1 SocketsPerBoard=1 CoresPerSocket=32 ThreadsPerCore=1 Gres=gpu:1 Port=6818"
    )
    snapshot = _snapshot()
    snapshot["soperator_resources"] = [nodeset]
    snapshot["node_groups"] = {
        "worker-0-0": {
            "gpu": True,
            "node_count": 2,
            "labels": {
                "nebius.com/node-group": "worker-0-0",
                "nebius.com/node-group-id": "nodegroup-worker-0-0",
                "slurm.nebius.ai/nodeset": "worker-0",
                "nebius.com/resource-preset": "1gpu-16vcpu-200gb",
            },
            "allocatable": {"cpu": "15900m", "nvidia.com/gpu": "1"},
            "nodes": ["old-node-a", "old-node-b"],
        }
    }

    values = migration._source_worker_nodeset_values(_source_report(snapshot))  # noqa: SLF001

    assert values[0]["nodeConfig"]["static"] == "CPUs=16 Gres=gpu:1"


def test_live_source_slurmcluster_present_treats_timeout_as_absent() -> None:
    class _TimeoutSlurmClustersRunner(_FakeCommandRunner):
        def __call__(
            self,
            args,
            *,
            input_text: str | None = None,
            timeout_seconds: int = 300,
            check: bool = True,
        ) -> SoperatorMigrationCommandResult:
            command = tuple(str(item) for item in args)
            if command[:8] == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "get",
                "slurmclusters",
                "-o",
            ):
                raise subprocess.TimeoutExpired(list(command), timeout_seconds)
            return super().__call__(
                command,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                check=check,
            )

    snapshot = _snapshot_with_compute_source()
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "source-soperator", "namespace": "soperator"},
        }
    ]

    assert not migration._live_source_slurmcluster_present(  # noqa: SLF001
        command_runner=_TimeoutSlurmClustersRunner(),
        kube_context="external-context",
        source_report=_source_report(snapshot),
        target_ref="external-cluster",
    )


def test_suspend_legacy_flux_helmreleases_treats_timeout_as_skip() -> None:
    class _TimeoutLegacyFluxRunner(_FakeCommandRunner):
        def __call__(
            self,
            args,
            *,
            input_text: str | None = None,
            timeout_seconds: int = 300,
            check: bool = True,
        ) -> SoperatorMigrationCommandResult:
            command = tuple(str(item) for item in args)
            self.calls.append((command, input_text))
            if command[:8] == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "flux-system",
                "get",
                "helmreleases",
                "-o",
            ):
                raise subprocess.TimeoutExpired(list(command), timeout_seconds)
            return super().__call__(
                command,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                check=check,
            )

    runner = _TimeoutLegacyFluxRunner()

    lines = migration._suspend_legacy_flux_helmreleases(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        phase={},
    )

    assert lines == [
        "Skipped legacy Flux HelmRelease suspension because the Kubernetes API "
        "did not answer within 30s."
    ]
    assert runner.calls[0][0][-1] == "--request-timeout=20s"


@pytest.mark.parametrize("profile_id", ["v2-to-target", "v3-to-target"])
def test_scale_down_legacy_soperator_controller_deployments(profile_id: str) -> None:
    runner = _FakeCommandRunner(
        live_kubernetes_resources={
            "deployment": [
                {
                    "metadata": {
                        "name": "soperator-controller-manager",
                        "namespace": "soperator-system",
                        "labels": {
                            "helm.sh/chart": "helm-soperator-2.0.5",
                            "app.kubernetes.io/version": "2.0.5",
                        },
                        "annotations": {
                            "meta.helm.sh/release-name": "soperator-controller",
                        },
                    },
                    "spec": {"replicas": 1},
                    "status": {"availableReplicas": 1, "readyReplicas": 1},
                },
                {
                    "metadata": {
                        "name": "soperator-manager",
                        "namespace": "soperator",
                        "labels": {
                            "helm.sh/chart": "soperator-4.0.1-ps.1",
                            "app.kubernetes.io/version": "4.0.1",
                        },
                        "annotations": {"meta.helm.sh/release-name": "soperator"},
                    },
                    "spec": {"replicas": 1},
                    "status": {"availableReplicas": 1, "readyReplicas": 1},
                },
            ],
            "mutatingwebhookconfiguration": [
                {"metadata": {"name": "soperator-controller-mutating-webhook-configuration"}},
                {"metadata": {"name": "soperator-mutating-webhook-configuration"}},
            ],
            "validatingwebhookconfiguration": [
                {"metadata": {"name": "soperator-controller-validating-webhook-configuration"}},
                {"metadata": {"name": "soperator-validating-webhook-configuration"}},
            ],
        }
    )
    phase: dict[str, Any] = {}

    changed, lines = migration._scale_down_legacy_soperator_controllers(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        phase=phase,
        target_version="4.0.1-ps.1",
        profile_group=migration.soperator_migration_profile_group(profile_id),
    )

    assert changed is True
    assert lines == [
        "Deleted old source Soperator admission webhooks before target takeover: "
        "MutatingWebhookConfiguration/soperator-controller-mutating-webhook-configuration, "
        "ValidatingWebhookConfiguration/soperator-controller-validating-webhook-configuration.",
        "Scaled down old source Soperator controller deployments before target "
        "takeover: soperator-system/soperator-controller-manager.",
    ]
    assert phase["deleted_source_controller_admission_webhooks"] == [
        "MutatingWebhookConfiguration/soperator-controller-mutating-webhook-configuration",
        "ValidatingWebhookConfiguration/soperator-controller-validating-webhook-configuration",
    ]
    assert phase["scaled_source_controller_deployments"] == [
        "soperator-system/soperator-controller-manager"
    ]
    deployments = runner.live_kubernetes_resources["deployment"]
    source = deployments[0]
    target = deployments[1]
    assert source["spec"]["replicas"] == 0
    assert source["status"]["availableReplicas"] == 0
    assert target["spec"]["replicas"] == 1
    assert runner.live_kubernetes_resources["mutatingwebhookconfiguration"] == [
        {"metadata": {"name": "soperator-mutating-webhook-configuration"}}
    ]
    assert runner.live_kubernetes_resources["validatingwebhookconfiguration"] == [
        {"metadata": {"name": "soperator-validating-webhook-configuration"}}
    ]


def test_scale_down_legacy_v1_soperator_controller_identities() -> None:
    runner = _FakeCommandRunner(
        live_kubernetes_resources={
            "deployment": [
                {
                    "metadata": {
                        "name": "soperator-controller-manager",
                        "namespace": "soperator-system",
                        "labels": {
                            "helm.sh/chart": "helm-soperator-1.23.3",
                            "app.kubernetes.io/version": "1.23.3",
                        },
                        "annotations": {
                            "meta.helm.sh/release-name": "soperator-controller",
                        },
                    },
                    "spec": {"replicas": 1},
                    "status": {"availableReplicas": 1, "readyReplicas": 1},
                },
                {
                    "metadata": {
                        "name": "slurm-operator-controller-manager",
                        "namespace": "soperator",
                        "labels": {
                            "helm.sh/chart": "slurm-operator-1.14.1",
                            "app.kubernetes.io/version": "1.14.1",
                        },
                        "annotations": {"meta.helm.sh/release-name": "slurm-operator"},
                    },
                    "spec": {"replicas": 1},
                    "status": {"availableReplicas": 1, "readyReplicas": 1},
                },
            ],
            "mutatingwebhookconfiguration": [
                {"metadata": {"name": "soperator-controller-mutating-webhook-configuration"}},
                {"metadata": {"name": "slurm-operator-mutating-webhook-configuration"}},
            ],
            "validatingwebhookconfiguration": [
                {"metadata": {"name": "soperator-controller-validating-webhook-configuration"}},
                {"metadata": {"name": "slurm-operator-validating-webhook-configuration"}},
            ],
        }
    )
    phase: dict[str, Any] = {}

    changed, lines = migration._scale_down_legacy_soperator_controllers(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        phase=phase,
        target_version="4.0.1-ps.1",
        profile_group=migration.soperator_migration_profile_group("legacy-v1-to-target"),
    )

    assert changed is True
    assert lines == [
        "Deleted old source Soperator admission webhooks before target takeover: "
        "MutatingWebhookConfiguration/soperator-controller-mutating-webhook-configuration, "
        "ValidatingWebhookConfiguration/soperator-controller-validating-webhook-configuration, "
        "MutatingWebhookConfiguration/slurm-operator-mutating-webhook-configuration, "
        "ValidatingWebhookConfiguration/slurm-operator-validating-webhook-configuration.",
        "Scaled down old source Soperator controller deployments before target "
        "takeover: soperator-system/soperator-controller-manager, "
        "soperator/slurm-operator-controller-manager.",
    ]
    assert phase["scaled_source_controller_deployments"] == [
        "soperator-system/soperator-controller-manager",
        "soperator/slurm-operator-controller-manager",
    ]
    assert phase["deleted_source_controller_admission_webhooks"] == [
        "MutatingWebhookConfiguration/soperator-controller-mutating-webhook-configuration",
        "ValidatingWebhookConfiguration/soperator-controller-validating-webhook-configuration",
        "MutatingWebhookConfiguration/slurm-operator-mutating-webhook-configuration",
        "ValidatingWebhookConfiguration/slurm-operator-validating-webhook-configuration",
    ]
    for deployment in runner.live_kubernetes_resources["deployment"]:
        assert deployment["spec"]["replicas"] == 0
        assert deployment["status"]["availableReplicas"] == 0
    assert runner.live_kubernetes_resources["mutatingwebhookconfiguration"] == []
    assert runner.live_kubernetes_resources["validatingwebhookconfiguration"] == []


def test_kubectl_json_or_empty_treats_timeout_as_empty() -> None:
    class _TimeoutJsonRunner(_FakeCommandRunner):
        def __call__(
            self,
            args,
            *,
            input_text: str | None = None,
            timeout_seconds: int = 300,
            check: bool = True,
        ) -> SoperatorMigrationCommandResult:
            command = tuple(str(item) for item in args)
            self.calls.append((command, input_text))
            raise subprocess.TimeoutExpired(list(command), timeout_seconds)

    runner = _TimeoutJsonRunner()

    payload = migration._kubectl_json_or_empty(  # noqa: SLF001
        command_runner=runner,
        command=[
            "kubectl",
            "--context",
            "external-context",
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-A",
            "-o",
            "json",
        ],
        description="kubectl get HelmRelease",
    )

    assert payload == {}
    assert runner.calls[0][0][-1] == "--request-timeout=20s"


def test_running_worker_pod_for_nodeset_treats_timeout_as_absent() -> None:
    class _TimeoutWorkerPodRunner(_FakeCommandRunner):
        def __call__(
            self,
            args,
            *,
            input_text: str | None = None,
            timeout_seconds: int = 300,
            check: bool = True,
        ) -> SoperatorMigrationCommandResult:
            command = tuple(str(item) for item in args)
            self.calls.append((command, input_text))
            if command[:7] == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "get",
                "pods",
            ):
                raise subprocess.TimeoutExpired(list(command), timeout_seconds)
            return super().__call__(
                command,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                check=check,
            )

    runner = _TimeoutWorkerPodRunner()

    pod_name = migration._running_worker_pod_for_nodeset(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        nodeset_name="worker-cpu",
    )

    assert pod_name == ""
    assert runner.calls[0][0][-1] == "--request-timeout=20s"


def test_login_pod_name_treats_timeout_as_absent() -> None:
    class _TimeoutLoginPodRunner(_FakeCommandRunner):
        def __call__(
            self,
            args,
            *,
            input_text: str | None = None,
            timeout_seconds: int = 300,
            check: bool = True,
        ) -> SoperatorMigrationCommandResult:
            command = tuple(str(item) for item in args)
            self.calls.append((command, input_text))
            if command[:7] == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "get",
                "pods",
            ):
                raise subprocess.TimeoutExpired(list(command), timeout_seconds)
            return super().__call__(
                command,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                check=check,
            )

    runner = _TimeoutLoginPodRunner()

    pod_name = migration._login_pod_name(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
    )

    assert pod_name == ""
    assert runner.calls[0][0][-1] == "--request-timeout=20s"


def test_final_cutover_reconcile_checks_preserved_worker_nodeset_readiness() -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups.update(
        {
            "gpu-pool": {
                "gpu": True,
                "node_count": 2,
                "labels": {
                    "nebius.com/node-group": "gpu-pool",
                    "nebius.com/node-group-id": "nodegroup-gpu-pool",
                    "slurm.nebius.ai/nodeset": "worker-gpu",
                },
                "allocatable": {"cpu": "127900m", "nvidia.com/gpu": "8"},
                "nodes": ["gpu-node-a"],
            },
            "cpu-pool": {
                "gpu": False,
                "node_count": 2,
                "labels": {
                    "nebius.com/node-group": "cpu-pool",
                    "nebius.com/node-group-id": "nodegroup-cpu-pool",
                    "slurm.nebius.ai/nodeset": "worker-cpu",
                },
                "allocatable": {"cpu": "15"},
                "nodes": ["cpu-node-a"],
            },
        }
    )
    snapshot["nodes"] = [
        {
            "metadata": {"name": "gpu-node-a"},
            "status": {
                "capacity": {"cpu": "128"},
                "allocatable": {"cpu": "127900m", "nvidia.com/gpu": "8"},
            },
        },
        {
            "metadata": {"name": "cpu-node-a"},
            "status": {
                "capacity": {"cpu": "16"},
                "allocatable": {"cpu": "15900m"},
            },
        },
    ]
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "external-cluster", "namespace": "soperator"},
        },
        _source_worker_nodeset("worker-cpu", gpu=False),
        _source_worker_nodeset("worker-gpu", gpu=True),
    ]
    checkpoint = {
        "phase_state": {
            "rolling-compute-migration": {
                "target_values_revision": migration._ROLLING_COMPUTE_VALUES_REVISION,
                "in_place_worker_node_groups": ["gpu-pool", "cpu-pool"],
                "target_node_groups": {},
            }
        }
    }
    payload = _payload(include_placements=True)
    source_report = _source_report(snapshot)
    unavailable_cluster_runner = _FakeCommandRunner(
        live_slurmclusters=[
            {
                "metadata": {"name": "external-cluster", "namespace": "soperator"},
                "status": {"phase": "Not available"},
            }
        ],
        live_nodesets={
            "worker-cpu": {
                "metadata": {"name": "worker-cpu", "namespace": "soperator"},
                "spec": {"nodeConfig": {"static": "CPUs=15"}},
                "status": {"phase": "Ready", "replicas": 2},
            },
            "worker-gpu": {
                "metadata": {"name": "worker-gpu", "namespace": "soperator"},
                "spec": {"nodeConfig": {"static": "CPUs=127 Gres=gpu:8 Port=6818"}},
                "status": {"phase": "Ready", "replicas": 2},
            },
        }
    )

    assert not migration._final_cutover_satisfied(
        checkpoint=checkpoint,
        payload=payload,
        source_report=source_report,
        live_snapshot=snapshot,
        target_ref="external-cluster",
        kube_context="external-context",
        command_runner=unavailable_cluster_runner,
    )
    reconcile_checkpoint = json.loads(json.dumps(checkpoint))
    completed_phases = {
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
    }

    reconcile_lines = migration._reconcile_completed_action_phases(
        checkpoint=reconcile_checkpoint,
        completed_phases=completed_phases,
        phase_ids=(
            "rolling-compute-migration",
            "final-control-plane-cutover",
            "validation-and-rollback-hold",
        ),
        payload=payload,
        source_report=source_report,
        live_snapshot=snapshot,
        target_ref="external-cluster",
        kube_context="external-context",
        worker_node_groups=("gpu-pool", "cpu-pool"),
        nebius_api=unavailable_cluster_runner.nebius_api,
        command_runner=unavailable_cluster_runner,
    )

    assert "rolling-compute-migration" not in completed_phases
    assert "final-control-plane-cutover" not in completed_phases
    assert "validation-and-rollback-hold" not in completed_phases
    assert (
        reconcile_checkpoint["phase_state"]["rolling-compute-migration"]["target_values_revision"]
        == 0
    )
    assert any("target Soperator values will be reapplied" in line for line in reconcile_lines)

    current_runner = _FakeCommandRunner(
        live_nodesets={
            "worker-cpu": {
                "metadata": {"name": "worker-cpu", "namespace": "soperator"},
                "spec": {"nodeConfig": {"static": "CPUs=15"}},
                "status": {"phase": "Ready", "replicas": 2},
            },
            "worker-gpu": {
                "metadata": {"name": "worker-gpu", "namespace": "soperator"},
                "spec": {
                    "nodeConfig": {
                        "static": (
                            "Boards=1 SocketsPerBoard=1 CoresPerSocket=32 "
                            "ThreadsPerCore=1 Gres=gpu:8"
                        )
                    }
                },
                "status": {"phase": "Ready", "replicas": 2},
            },
        }
    )

    assert migration._final_cutover_satisfied(
        checkpoint=checkpoint,
        payload=payload,
        source_report=source_report,
        live_snapshot=snapshot,
        target_ref="external-cluster",
        kube_context="external-context",
        command_runner=current_runner,
    )


def test_execute_pends_gpu_reconciliation_when_target_app_rows_are_missing(
    tmp_path: Path,
) -> None:
    payload = _payload()
    charts = payload["apps"]["charts"]  # type: ignore[index]
    assert isinstance(charts, list)
    charts[:] = [row for row in charts if row.get("id") == "soperator"]

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=payload,
        source_report=_source_report(),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
    )

    assert result.mutation_performed is True
    assert result.pending_phase == "target-gpu-stack-remediation"
    assert "requires target-scoped GPU Operator or Network Operator app rows" in (
        result.pending_reason
    )
    checkpoint = json.loads(
        soperator_migration_checkpoint_path(
            tmp_path / "config.yaml",
            "external-cluster",
        ).read_text(encoding="utf-8")
    )
    assert checkpoint["pending_phase"] == "target-gpu-stack-remediation"


def test_execute_runs_gpu_reconciliation_when_report_has_no_migration_plan(
    tmp_path: Path,
) -> None:
    payload = _payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    assert isinstance(target, dict)
    onboarding = target["soperator_onboarding"]
    assert isinstance(onboarding, dict)
    onboarding["actions"] = ["reconcile-target-gpu-stack"]
    onboarding["storage_mode"] = "keep-existing-storage"
    onboarding["compute_mode"] = "keep-existing-compute"
    source_report = _source_report()
    report = source_report["report"]
    assert isinstance(report, dict)
    report["migration_plan"] = []
    runner = _FakeCommandRunner()

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "none"
    assert result.completed_phases == (
        "discovery-and-plan",
        "customer-approval",
        "target-gpu-stack-remediation",
        "post-upgrade-mk8s-check",
        "post-upgrade-helm-check",
    )
    helm_upgrade_calls = [
        call
        for call in runner.calls
        if call[0][:4] == ("helm", "--kube-context", "external-context", "upgrade")
    ]
    assert [call[0][5] for call in helm_upgrade_calls] == [
        "gpu-operator",
        "network-operator",
    ]


def test_execute_emits_phase_aware_status_for_storage_and_compute(tmp_path: Path) -> None:
    snapshot = _snapshot_with_compute_source()
    source_report = _source_report(snapshot)
    runner = _FakeCommandRunner()
    messages: list[str] = []

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(include_placements=True),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
        status_callback=messages.append,
        status_poll_interval_seconds=0,
    )

    assert result.pending_phase == "none"
    assert any("phase execute-preflight" in message for message in messages)
    assert any("phase backup" in message for message in messages)
    assert any("phase protected-state-capture" in message for message in messages)
    assert any("phase post-upgrade-mk8s-check" in message for message in messages)
    assert any("phase post-upgrade-helm-check" in message for message in messages)
    assert any("phase report" in message for message in messages)
    assert not any(
        "External Soperator upgrade phase" in message and "top-level stage:" in message
        for message in messages
    )
    assert any(
        "phase create-aligned-sfs" in message
        and "[Aligned SFS creation]" in message
        and "Storage" in message
        and "MK8s" in message
        and "Slurm" in message
        for message in messages
    )
    assert any(
        "phase rolling-compute-migration" in message
        and "[Rolling compute migration]" in message
        and "MK8s" in message
        and "Slurm" in message
        and "Storage" not in message
        for message in messages
    )
    assert any(
        "phase final-control-plane-cutover" in message
        and "[Final control-plane cutover]" in message
        and "Soperator" in message
        for message in messages
    )
    assert any(
        "phase populate-jail-refresh" in message
        and "[Jail Upgrade]" in message
        and "MK8s" in message
        and "Slurm" in message
        and "Soperator" in message
        and "Populate-jail" in message
        and "no phase-specific status checks are planned" not in message
        for message in messages
    )

    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    status_events = [event for event in checkpoint["events"] if event["event"] == "execute-status"]
    assert status_events
    rolling_status = next(
        event
        for event in status_events
        if event["phase"] == "rolling-compute-migration" and event["point"] == "phase-start"
    )
    assert {signal["name"] for signal in rolling_status["signals"]} == {
        "MK8s Node Groups",
        "Slurm Workers",
        "Soperator",
    }
    populate_status = next(
        event
        for event in status_events
        if event["phase"] == "populate-jail-refresh" and event["point"] == "phase-start"
    )
    assert {signal["name"] for signal in populate_status["signals"]} == {
        "MK8s Node Groups",
        "Slurm Workers",
        "Soperator",
        "Populate-jail",
    }


def test_mk8s_status_reports_node_groups_and_replacing_nodes() -> None:
    runner = _FakeCommandRunner(
        live_nodes=[
            {
                "metadata": {
                    "name": "gpu-node-a",
                    "labels": {"nebius.com/node-group": "gpu-pool"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "False"}]},
            },
            {
                "metadata": {
                    "name": "gpu-node-b",
                    "labels": {"nebius.com/node-group": "gpu-pool"},
                },
                "spec": {"unschedulable": True},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
            {
                "metadata": {
                    "name": "login-node-a",
                    "labels": {"nebius.com/node-group": "login-pool"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
        ]
    )
    checkpoint = {
        "phase_state": {
            "external-node-template-upgrade": {
                "node_groups": {
                    "gpu-pool": {
                        "status": "updating",
                        "node_group_name": "gpu-pool",
                        "node_group_id": "nodegroup-gpu-pool",
                    }
                }
            }
        }
    }

    signal = migration._collect_mk8s_status(
        nebius_api=runner.nebius_api,
        command_runner=runner,
        kube_context="external-context",
        checkpoint=checkpoint,
        phase_id="external-node-template-upgrade",
    )

    assert signal.name == "MK8s Node Groups"
    assert signal.state == "degraded"
    assert signal.summary.startswith("Node groups: 2 group(s); ")
    assert "gpu-pool:1/2 Ready, login-pool:1/1 Ready" in signal.summary
    assert " || Nodes: 2/3 Ready; 1 cordoned; " in signal.summary
    assert (
        "in transition gpu-node-a:replacing (down), gpu-node-b:replacing (cordoned)"
    ) in signal.summary
    assert "problem nodes" not in signal.summary


def test_mk8s_status_reports_reconciling_node_group_when_nodes_ready() -> None:
    runner = _FakeCommandRunner(
        live_nodes=[
            {
                "metadata": {
                    "name": "system-node-a",
                    "labels": {"nebius.com/node-group-id": "nodegroup-system"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
            {
                "metadata": {
                    "name": "system-node-b",
                    "labels": {"nebius.com/node-group-id": "nodegroup-system"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
            {
                "metadata": {
                    "name": "system-node-c",
                    "labels": {"nebius.com/node-group-id": "nodegroup-system"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
        ],
        existing_node_groups=[
            {
                "metadata": {"id": "nodegroup-system", "name": "system"},
                "spec": {"version": "1.32"},
                "status": {
                    "ready_node_count": 3,
                    "target_node_count": 3,
                    "node_count": 3,
                    "outdated_node_count": 3,
                    "reconciling": True,
                    "state": "PROVISIONING",
                    "events": [
                        {
                            "last_occurrence": {
                                "code": "Draining",
                            },
                        }
                    ],
                },
            }
        ],
    )
    checkpoint = {
        "phase_state": {
            "external-node-template-upgrade": {
                "node_groups": {
                    "system": {
                        "status": "updating",
                        "node_group_name": "system",
                        "node_group_id": "nodegroup-system",
                    }
                }
            }
        }
    }

    signal = migration._collect_mk8s_status(
        nebius_api=runner.nebius_api,
        command_runner=runner,
        kube_context="external-context",
        checkpoint=checkpoint,
        phase_id="external-node-template-upgrade",
    )

    assert signal.name == "MK8s Node Groups"
    assert signal.state == "degraded"
    assert "nodegroup-system:3/3 Ready" in signal.summary
    assert (
        "updating system:PROVISIONING,ready=3/3,event=Draining,outdated=3,reconciling"
    ) in signal.summary
    assert "Nodes: 3/3 Ready" in signal.summary


def test_mk8s_status_keeps_problem_label_for_non_upgrading_notready_nodes() -> None:
    runner = _FakeCommandRunner(
        live_nodes=[
            {
                "metadata": {
                    "name": "gpu-node-a",
                    "labels": {"nebius.com/node-group": "gpu-pool"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "False"}]},
            },
            {
                "metadata": {
                    "name": "login-node-a",
                    "labels": {"nebius.com/node-group": "login-pool"},
                },
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
        ]
    )

    signal = migration._collect_mk8s_status(
        nebius_api=runner.nebius_api,
        command_runner=runner,
        kube_context="external-context",
        checkpoint={},
        phase_id="external-node-template-upgrade",
    )

    assert signal.name == "MK8s Node Groups"
    assert signal.state == "degraded"
    assert signal.summary.startswith(
        "Node groups: 2 group(s); gpu-pool:0/1 Ready, login-pool:1/1 Ready"
    )
    assert " || Nodes: 1/2 Ready; " in signal.summary
    assert "problem nodes gpu-node-a:NotReady (down)" in signal.summary
    assert "in transition" not in signal.summary


def test_mk8s_status_bounds_many_groups_and_transition_nodes() -> None:
    live_nodes: list[dict[str, Any]] = []
    node_groups: dict[str, dict[str, str]] = {}
    group_count = 40
    nodes_per_group = 100
    cordoned_per_group = 2
    for index in range(group_count):
        group_name = f"worker-{index:02d}"
        node_groups[group_name] = {
            "status": "updating",
            "node_group_name": group_name,
            "node_group_id": f"nodegroup-{index:02d}",
        }
        for node_index in range(nodes_per_group):
            cordoned = node_index < cordoned_per_group
            live_nodes.append(
                {
                    "metadata": {
                        "name": (
                            f"{group_name}-old-{node_index:02d}"
                            if cordoned
                            else f"{group_name}-node-{node_index:02d}"
                        ),
                        "labels": {"nebius.com/node-group": group_name},
                    },
                    "spec": {"unschedulable": True} if cordoned else {},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
            )
    runner = _FakeCommandRunner(live_nodes=live_nodes)
    checkpoint = {
        "phase_state": {
            "external-node-template-upgrade": {
                "node_groups": node_groups,
            }
        }
    }

    signal = migration._collect_mk8s_status(
        nebius_api=runner.nebius_api,
        command_runner=runner,
        kube_context="external-context",
        checkpoint=checkpoint,
        phase_id="external-node-template-upgrade",
    )

    assert signal.name == "MK8s Node Groups"
    assert signal.state == "degraded"
    assert signal.summary.startswith("Node groups: 40 group(s); ")
    assert "worker-00:100/100 Ready" in signal.summary
    assert "worker-05:100/100 Ready" in signal.summary
    assert "worker-06:100/100 Ready" not in signal.summary
    assert "+34 more groups" in signal.summary
    assert " || Nodes: 4000/4000 Ready; 80 cordoned; " in signal.summary
    assert "in transition worker-00-old-00:replacing (cordoned)" in signal.summary
    assert "worker-03-old-01:replacing (cordoned)" in signal.summary
    assert "worker-04-old-00:replacing (cordoned)" not in signal.summary
    assert "+72 more" in signal.summary
    assert len(signal.summary) < 800


def test_mk8s_status_sections_empty_node_inventory() -> None:
    runner = _FakeCommandRunner()
    runner.live_nodes = []

    signal = migration._collect_mk8s_status(
        nebius_api=runner.nebius_api,
        command_runner=runner,
        kube_context="external-context",
        checkpoint={},
        phase_id="external-node-template-upgrade",
    )

    assert signal.name == "MK8s Node Groups"
    assert signal.state == "down"
    assert signal.summary == "Node groups: 0 group(s) || Nodes: 0/0 Ready"


def test_slurm_status_reports_named_worker_states() -> None:
    signal = migration._collect_slurm_status(
        command_runner=_FakeCommandRunner(),
        kube_context="external-context",
    )

    assert signal.name == "Slurm Workers"
    assert signal.state == "draining"
    assert "workers drained=2, idle=2" in signal.summary
    assert "problem workers worker-gpu-0:drained, worker-gpu-1:drained" in signal.summary
    assert "queue empty" in signal.summary


def test_status_scope_matches_planned_migration_layers() -> None:
    storage_only = (
        "discovery-and-plan",
        "customer-approval",
        "target-gpu-stack-remediation",
        "create-aligned-sfs",
        "online-bulk-data-sync",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
    )
    compute_only = (
        "discovery-and-plan",
        "customer-approval",
        "target-gpu-stack-remediation",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
    )

    assert migration._status_scope_for_phase("create-aligned-sfs", storage_only) == {
        "storage": True,
        "mk8s": True,
        "slurm": True,
        "soperator": True,
        "populate_jail": False,
    }
    assert migration._status_scope_for_phase("target-gpu-stack-remediation", compute_only) == {
        "storage": False,
        "mk8s": True,
        "slurm": False,
        "soperator": False,
        "populate_jail": False,
    }
    assert migration._status_scope_for_phase("rolling-compute-migration", compute_only) == {
        "storage": False,
        "mk8s": True,
        "slurm": True,
        "soperator": True,
        "populate_jail": False,
    }
    assert migration._status_scope_for_phase("final-control-plane-cutover", storage_only) == {
        "storage": True,
        "mk8s": True,
        "slurm": True,
        "soperator": True,
        "populate_jail": False,
    }
    assert migration._status_scope_for_phase("final-control-plane-cutover", compute_only) == {
        "storage": False,
        "mk8s": True,
        "slurm": True,
        "soperator": True,
        "populate_jail": False,
    }
    assert migration._status_scope_for_phase(
        "validation-and-rollback-hold",
        ("discovery-and-plan", "validation-and-rollback-hold"),
    ) == {
        "storage": False,
        "mk8s": False,
        "slurm": False,
        "soperator": False,
        "populate_jail": False,
    }


def test_execute_runs_data_copy_jobs_when_source_storage_exists(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot["storage"] = {
        "jail": {"source": "pvc/old-jail"},
        "controller-spool": {"source": "pvc/old-controller-spool"},
        "accounting": {"source": "pvc/old-accounting"},
    }
    snapshot["pvcs"] = [
        {"metadata": {"name": "old-jail", "namespace": "soperator"}},
        {"metadata": {"name": "old-controller-spool", "namespace": "soperator"}},
        {"metadata": {"name": "old-accounting", "namespace": "soperator"}},
        {"metadata": {"name": "jail-pvc", "namespace": "soperator"}},
        {"metadata": {"name": "controller-spool-pvc", "namespace": "soperator"}},
        {"metadata": {"name": "accounting-pvc", "namespace": "soperator"}},
    ]
    source_report = _source_report(snapshot)
    report = source_report["report"]
    assert isinstance(report, dict)
    report["migration_plan"] = [
        {"id": "discovery-and-plan", "status": "complete"},
        {
            "id": "customer-approval",
            "status": "planned",
            "requires_customer_approval": True,
        },
        {"id": "create-aligned-sfs", "status": "planned"},
        {"id": "online-bulk-data-sync", "status": "planned"},
        {
            "id": "retire-old-resources",
            "status": "planned",
            "requires_customer_approval": True,
        },
    ]
    runner = _FakeCommandRunner()

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "retire-old-resources"
    assert "requires explicit confirmation" in result.pending_reason
    apply_calls = [
        call
        for call in runner.calls
        if call[0] == ("kubectl", "--context", "external-context", "apply", "-f", "-")
    ]
    sync_apply_calls = []
    for call in apply_calls:
        try:
            applied_payload = json.loads(call[1] or "{}")
        except json.JSONDecodeError:
            continue
        names = {item["metadata"]["name"] for item in applied_payload.get("items", [])}
        if names and all(name.startswith("cxcli-soperator-sync-") for name in names):
            sync_apply_calls.append(call)
    assert len(sync_apply_calls) == 1
    assert sync_apply_calls[0][1] is not None
    applied = json.loads(sync_apply_calls[0][1])
    job_names = {item["metadata"]["name"] for item in applied["items"]}
    assert job_names == {
        "cxcli-soperator-sync-controller-spool",
        "cxcli-soperator-sync-accounting",
    }
    job_status_calls = [
        call
        for call in runner.calls
        if call[0][:6]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
        )
        and call[0][6].startswith("job/cxcli-soperator-sync-")
    ]
    assert len(job_status_calls) == 6


def test_jail_data_sync_skips_single_sfs_rootfs_copy() -> None:
    snapshot = _snapshot()
    snapshot["storage"] = {"jail": {"source": "pvc/jail-pvc"}}
    snapshot["pvcs"] = [{"metadata": {"name": "jail-pvc", "namespace": "soperator"}}]
    payload = _payload()
    payload["apps"]["charts"][0]["values"] = migration.apply_jail_persistent_mount_values(
        {"nodesets": [{"name": "worker"}]},
        target_ref="external-cluster",
        layout="external",
    )
    source_report = _source_report(snapshot)
    checkpoint: dict[str, Any] = {"phase_state": {}}
    runner = _FakeCommandRunner(live_pvcs=list(snapshot["pvcs"]))

    mutated, lines = migration._execute_online_bulk_data_sync_phase(  # noqa: SLF001
        checkpoint=checkpoint,
        payload=payload,
        source_report=source_report,
        live_snapshot=snapshot,
        target_ref="external-cluster",
        kube_context="external-context",
        command_runner=runner,
    )

    assert mutated is False
    assert "Data sync skipped: no PVC copy pairs were detected." in lines
    apply_calls = [
        call
        for call in runner.calls
        if call[0] == ("kubectl", "--context", "external-context", "apply", "-f", "-")
    ]
    assert apply_calls == []
    phase = checkpoint["phase_state"]["online-bulk-data-sync"]
    assert "jail" not in phase["jobs"]


def test_persistent_mount_overwrite_checks_accept_configured_mounts() -> None:
    runner = _FakeCommandRunner()

    checks = migration._persistent_mount_overwrite_checks(  # noqa: SLF001
        checkpoint={"phase_state": {}},
        values={
            "jailPersistentMounts": [
                {"mountPath": "/home", "localPath": "/mnt/jail/shared/home"},
            ]
        },
        kube_context="external-context",
        command_runner=runner,
    )

    by_name = {check["name"]: check for check in checks}
    assert by_name["persistent jail mounts"]["status"] == "passed"
    assert runner.calls == []


def test_persistent_mount_overwrite_checks_fail_without_home() -> None:
    runner = _FakeCommandRunner()

    checks = migration._persistent_mount_overwrite_checks(  # noqa: SLF001
        checkpoint={"phase_state": {}},
        values={
            "jailPersistentMounts": [
                {"mountPath": "/data", "localPath": "/mnt/jail/shared/data"}
            ]
        },
        kube_context="external-context",
        command_runner=runner,
    )

    by_name = {check["name"]: check for check in checks}
    assert by_name["persistent jail mounts"]["status"] == "failed"


def test_execute_does_not_block_data_copy_for_missing_target_jail_pvc(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot["storage"] = {"jail": {"source": "pvc/old-jail"}}
    snapshot["pvcs"] = [{"metadata": {"name": "old-jail", "namespace": "soperator"}}]

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(snapshot),
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=_FakeCommandRunner(),
    )

    assert result.pending_phase != "online-bulk-data-sync"


def test_online_data_sync_deletes_failed_job_before_reapply() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "job/cxcli-copy-jail",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"status": {"conditions": [{"type": "Failed", "status": "True"}]}}),
                "",
            )
        return SoperatorMigrationCommandResult(command, 0, "{}", "")

    migration._delete_failed_job_before_reapply(
        command_runner=runner,
        kube_context="external-context",
        name="cxcli-copy-jail",
    )

    assert (
        "kubectl",
        "--context",
        "external-context",
        "-n",
        "soperator",
        "delete",
        "job",
        "cxcli-copy-jail",
        "--ignore-not-found",
    ) in calls


def test_online_data_sync_wait_fails_immediately_on_failed_job() -> None:
    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        return SoperatorMigrationCommandResult(
            command,
            0,
            json.dumps({"status": {"conditions": [{"type": "Failed", "status": "True"}]}}),
            "",
        )

    with pytest.raises(RuntimeError, match="data sync Job failed"):
        migration._wait_for_job_complete_or_failed(
            command_runner=runner,
            kube_context="external-context",
            name="cxcli-copy-jail",
            timeout_seconds=3900,
        )


def test_clear_controller_spool_deletes_failed_job_before_reapply() -> None:
    calls: list[tuple[str, ...]] = []
    get_count = 0
    job_name = "cxcli-soperator-clear-clustername"

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        nonlocal get_count
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            f"job/{job_name}",
        ):
            get_count += 1
            condition = "Failed" if get_count == 1 else "Complete"
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"status": {"conditions": [{"type": condition, "status": "True"}]}}),
                "",
            )
        return SoperatorMigrationCommandResult(command, 0, "{}", "")

    migration._clear_controller_spool_clustername(
        command_runner=runner,
        kube_context="external-context",
    )

    delete_index = calls.index(
        (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "job",
            job_name,
            "--ignore-not-found",
        )
    )
    apply_index = calls.index(("kubectl", "--context", "external-context", "apply", "-f", "-"))
    assert delete_index < apply_index
    assert not any("wait" in call for call in calls)


def test_clear_controller_spool_wait_fails_immediately_on_failed_job() -> None:
    get_count = 0
    job_name = "cxcli-soperator-clear-clustername"
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        nonlocal get_count
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            f"job/{job_name}",
        ):
            get_count += 1
            if get_count == 1:
                return SoperatorMigrationCommandResult(command, 1, "", "not found")
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"status": {"conditions": [{"type": "Failed", "status": "True"}]}}),
                "",
            )
        return SoperatorMigrationCommandResult(command, 0, "{}", "")

    with pytest.raises(RuntimeError, match="controller-spool cleanup Job failed"):
        migration._clear_controller_spool_clustername(
            command_runner=runner,
            kube_context="external-context",
        )

    assert not any(
        call[:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "pod",
            "controller-0",
        )
        for call in calls
    )


def test_execute_does_not_attach_sfs_to_unmapped_node_groups(tmp_path: Path) -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups["cpu-pool"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "cpu-pool",
            "nebius.com/node-group-id": "nodegroup-cpu-pool",
        },
    }
    node_groups["monitoring"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "monitoring",
            "nebius.com/node-group-id": "nodegroup-monitoring",
        },
    }
    runner = _FakeCommandRunner()

    execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(include_placements=True),
        source_report=_source_report(snapshot),
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
    )

    updated_node_groups = [
        call[0][4]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and "--template-filesystems" in call[0]
    ]
    assert set(updated_node_groups) == {"nodegroup-cpu-pool"}
    assert "nodegroup-monitoring" not in updated_node_groups


def test_execute_refreshes_live_snapshot_after_mutating_phases(tmp_path: Path) -> None:
    calls: list[str] = []

    def _collector(*, kube_context: str):
        calls.append(kube_context)
        return _snapshot()

    execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(),
        snapshot_collector=_collector,
        approved=True,
        command_runner=_FakeCommandRunner(),
    )

    assert len(calls) > 1


def test_system_service_role_quiesces_webhook_drain_blocker() -> None:
    assert (
        migration._source_group_service_quiesce_role(  # noqa: SLF001
            "system",
            {"node_count": 3},
        )
        == "system"
    )
    assert (
        "scale-down-one",
        "deployment/security-profiles-operator-webhook",
        "security-profiles-operator-system",
    ) in migration._external_service_role_quiesce_resources("system")  # noqa: SLF001


def test_accounting_service_quiesce_required_for_multi_node_group() -> None:
    assert migration._zero_surge_service_quiesce_required(  # noqa: SLF001
        "accounting",
        {"node_count": 2},
    )


def test_system_service_role_webhook_quiesce_does_not_scale_up_zero_replicas() -> None:
    runner = _FakeCommandRunner(
        live_kubernetes_resources={
            "deployment": [
                {
                    "metadata": {
                        "name": "security-profiles-operator-webhook",
                        "namespace": "security-profiles-operator-system",
                    },
                    "spec": {"replicas": 0},
                    "status": {"availableReplicas": 0, "readyReplicas": 0},
                }
            ]
        }
    )
    state: dict[str, Any] = {}

    lines = migration._quiesce_external_service_role(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        role="system",
        state=state,
    )

    assert lines == ["Quiesced Soperator system workloads before node-template rollout."]
    assert state["resources"] == [
        {
            "action": "scale-down-one",
            "namespace": "security-profiles-operator-system",
            "resource": "deployment/security-profiles-operator-webhook",
            "exists": True,
            "replicas": 0,
            "target_replicas": 0,
        }
    ]
    assert not any(call[0][5] == "scale" for call in runner.calls if len(call[0]) > 5)


def test_login_service_quiesce_skips_slurmcluster_without_login_role() -> None:
    runner = _FakeCommandRunner(
        live_slurmclusters=[
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "other", "namespace": "soperator"},
                "spec": {"slurmNodes": {"controller": {"size": 1}}},
                "status": {"phase": "Available"},
            }
        ]
    )
    state: dict[str, Any] = {}

    migration._quiesce_external_service_role(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        role="login",
        state=state,
    )

    slurm_items = [item for item in state["resources"] if item["action"] == "slurm-node-size"]
    assert slurm_items == [
        {
            "action": "slurm-node-size",
            "resource": "slurmclusters",
            "role": "login",
            "exists": False,
            "size": None,
            "target_size": None,
        }
    ]
    assert not any(
        len(call[0]) > 7 and call[0][5] == "patch" and call[0][6].startswith("slurmcluster")
        for call in runner.calls
    )


def test_login_service_quiesce_skips_empty_slurmcluster_list() -> None:
    runner = _FakeCommandRunner()
    runner.live_slurmclusters = []

    item = migration._quiesce_slurm_node_size_resource(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        resource="slurmclusters",
        role="login",
    )

    assert item == {
        "action": "slurm-node-size",
        "resource": "slurmclusters",
        "role": "login",
        "exists": False,
        "size": None,
        "target_size": None,
    }
    assert not any(
        len(call[0]) > 7 and call[0][5] == "patch" and call[0][6].startswith("slurmcluster")
        for call in runner.calls
    )


def test_login_service_quiesce_preserves_zero_slurmcluster_size() -> None:
    runner = _FakeCommandRunner(
        live_slurmclusters=[
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "soperator", "namespace": "soperator"},
                "spec": {"slurmNodes": {"login": {"size": 0}}},
                "status": {"phase": "Available"},
            }
        ]
    )
    state: dict[str, Any] = {}

    migration._quiesce_external_service_role(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        role="login",
        state=state,
    )
    migration._restore_external_service_role(  # noqa: SLF001
        command_runner=runner,
        kube_context="external-context",
        state=state,
    )

    slurm_items = [item for item in state["resources"] if item["action"] == "slurm-node-size"]
    assert slurm_items[0]["size"] == 0
    assert slurm_items[0]["target_size"] == 0
    assert not any(
        len(call[0]) > 7 and call[0][5] == "patch" and call[0][6].startswith("slurmcluster")
        for call in runner.calls
    )


def test_execute_runs_completion_safety_when_plan_omits_validation_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_ids = (
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "rolling-compute-migration",
    )
    monkeypatch.setattr(
        migration,
        "_phase_ids_for_actions",
        lambda *, report, onboarding, populate_jail_refresh="auto": phase_ids,
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
    )

    assert result.pending_phase == "none"
    assert (
        "validation-and-rollback-hold: Shared Soperator upgrade safety verification completed: passed."
        in "\n".join(result.lines)
    )
    checkpoint = json.loads(
        soperator_migration_checkpoint_path(tmp_path / "config.yaml", "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["upgrade_safety"]["post_upgrade_verification"]["status"] == "passed"
    assert checkpoint["upgrade_safety"]["protected_customer_state"]["after_hash"]


def test_execute_rejects_incompatible_existing_aligned_sfs(tmp_path: Path) -> None:
    runner = _FakeCommandRunner(
        existing_filesystems={
            "external-cluster-controller-spool": {
                "metadata": {"id": "filesystem-existing-controller-spool"},
                "spec": {
                    "size_gibibytes": "64",
                    "block_size_bytes": "4096",
                    "type": "NETWORK_SSD",
                },
            }
        }
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(),
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "create-aligned-sfs"
    assert (
        "existing aligned SFS filesystem 'external-cluster-controller-spool' is incompatible"
        in result.pending_reason
    )


def test_execute_rejects_existing_migration_lock(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    lock_path = soperator_migration_checkpoint_path(config_path, "external-cluster").with_suffix(
        ".lock"
    )
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("locked\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="already running"):
        execute_soperator_migration(
            config_path=config_path,
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
        )


def test_execute_migrates_compute_when_slurm_resources_exist(tmp_path: Path) -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups["cpu-pool"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "cpu-pool",
            "nebius.com/node-group-id": "nodegroup-cpu-pool",
        },
        "nodes": ["cpu-node-a"],
    }
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "soperator", "namespace": "soperator"},
        }
    ]
    snapshot["pvcs"] = [
        {
            "metadata": {"name": "controller-spool-pvc", "namespace": "soperator"},
            "spec": {"resources": {"requests": {"storage": "30Gi"}}},
            "status": {"capacity": {"storage": "128Gi"}},
        },
        {
            "metadata": {"name": "jail-pvc", "namespace": "soperator"},
            "spec": {"resources": {"requests": {"storage": "2Ti"}}},
            "status": {"capacity": {"storage": "2Ti"}},
        },
    ]
    snapshot["pvs"] = [
        {
            "metadata": {"name": "jail-pv"},
            "spec": {
                "capacity": {"storage": "2Ti"},
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "slurm.nebius.ai/jail",
                                        "operator": "In",
                                        "values": ["true"],
                                    }
                                ]
                            }
                        ]
                    }
                },
            },
        },
        {
            "metadata": {"name": "controller-spool-pv"},
            "spec": {
                "capacity": {"storage": "128Gi"},
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "slurm.nebius.ai/nodeset",
                                        "operator": "In",
                                        "values": ["controller"],
                                    }
                                ]
                            }
                        ]
                    }
                },
            },
        },
        {
            "metadata": {"name": "accounting-pv"},
            "spec": {
                "capacity": {"storage": "128Gi"},
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "slurm.nebius.ai/nodeset",
                                        "operator": "In",
                                        "values": ["accounting"],
                                    }
                                ]
                            }
                        ]
                    }
                },
            },
        },
    ]
    source_report = _source_report(snapshot)
    payload = _payload(include_placements=True)
    chart_values = payload["apps"]["charts"][0]["values"]  # type: ignore[index]
    assert isinstance(chart_values, dict)
    chart_values["nodesets"] = [
        {
            "name": "worker",
            "replicas": 2,
            "slurmd": {
                "image": {
                    "repository": "cr.eu-north1.nebius.cloud/soperator/worker_slurmd",
                    "tag": "3.0.5-slurm25.11.3",
                },
                "resources": {"cpu": "500m", "memory": "1Gi", "gpu": 1},
            },
            "munge": {
                "image": {
                    "repository": "cr.eu-north1.nebius.cloud/soperator/munge",
                    "tag": "3.0.5-slurm25.11.3",
                },
                "resources": {"cpu": "200m", "memory": "512Mi"},
            },
        }
    ]
    runner = _FakeCommandRunner(
        live_pvcs=[
            {
                "metadata": {
                    "name": "storage-external-cluster-acct-db-0",
                    "namespace": "soperator",
                },
                "spec": {"accessModes": ["ReadWriteOnce"]},
                "status": {"phase": "Pending"},
            }
        ],
        live_slurmclusters=[
            {"metadata": {"name": "soperator", "namespace": "soperator"}},
            {"metadata": {"name": "external-cluster", "namespace": "soperator"}},
            {"metadata": {"name": "other-team", "namespace": "soperator"}},
        ],
        live_flux_helmreleases=[
            {
                "metadata": {"name": "soperator-fluxcd", "namespace": "flux-system"},
                "spec": {},
            },
            {
                "metadata": {
                    "name": "flux-system-soperator-fluxcd-mariadb-operator-crds",
                    "namespace": "flux-system",
                },
                "spec": {},
            },
            {
                "metadata": {"name": "other-team", "namespace": "flux-system"},
                "spec": {},
            },
        ],
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "none"
    assert result.mutation_performed is True
    assert (
        "Target Soperator chart values applied to aligned service-role groups and preserved worker groups."
        in "\n".join(result.lines)
    )
    create_calls = [
        call for call in runner.calls if call[0][:4] == ("nebius", "mk8s", "node-group", "create")
    ]
    assert len(create_calls) == 4
    create_payloads = [json.loads(call[0][4]) for call in create_calls]
    accounting_payload = next(
        payload
        for payload in create_payloads
        if payload["metadata"]["name"] == "external-cluster-accounting"
    )
    accounting_labels = accounting_payload["spec"]["template"]["metadata"]["labels"]
    assert accounting_labels["slurm.nebius.ai/nodeset"] == "accounting"
    assert accounting_labels["slurm.nebius.ai/nodeset-name"] == "accounting"
    helm_upgrades = [call for call in runner.calls if call[0][0] == "helm" and "upgrade" in call[0]]
    assert [call[0][5] for call in helm_upgrades[:3]] == [
        "gpu-operator",
        "network-operator",
        "soperator",
    ]
    helm_upgrade = next(call for call in helm_upgrades if call[0][5] == "soperator")
    assert helm_upgrade[1] is not None
    assert "--force-conflicts" in helm_upgrade[0]
    assert "--take-ownership" in helm_upgrade[0]
    assert "--no-hooks" in helm_upgrade[0]
    assert "--timeout" in helm_upgrade[0]
    assert helm_upgrade[0][helm_upgrade[0].index("--timeout") + 1] == "30m"
    assert "--wait" not in helm_upgrade[0]
    crd_applies = [
        call
        for call in runner.calls
        if call[0][:5] == ("kubectl", "--context", "external-context", "apply", "--server-side")
    ]
    assert crd_applies
    assert all("--force-conflicts" in call[0] for call in crd_applies)
    helm_values = json.loads(helm_upgrade[1])
    assert helm_values["nameOverride"] == "helm-soperator"
    assert helm_values["slurmNodes"]["login"]["sshdServiceAnnotations"] == {
        "nebius.com/load-balancer-allocation-id": "vpcallocation-login",
    }
    assert helm_values["gpuDriverJail"] == {"enabled": True}
    assert (
        helm_values["customSlurmConfig"]
        == "SlurmdParameters=l3cache_as_socket\nPluginDir=/usr/lib/x86_64-linux-gnu/slurm"
    )
    assert helm_values["plugStackConfig"]["pyxis"] == {
        "importerPath": "",
        "required": False,
    }
    assert helm_values["volume"]["controllerSpool"]["size"] == "128Gi"
    assert helm_values["volume"]["jail"]["size"] == "2Ti"
    assert helm_values["storage"]["jail"]["matchExpressions"] == [
        {"key": "slurm.nebius.ai/jail", "operator": "In", "values": ["true"]}
    ]
    assert helm_values["storage"]["controllerSpool"]["matchExpressions"] == [
        {"key": "slurm.nebius.ai/nodeset", "operator": "In", "values": ["controller"]}
    ]
    assert helm_values["storage"]["accounting"]["matchExpressions"] == [
        {"key": "slurm.nebius.ai/nodeset", "operator": "In", "values": ["accounting"]}
    ]
    assert {item["name"] for item in helm_values["k8sNodeFilters"]} == {
        "no-gpu",
        "system",
        "controller",
        "login",
        "accounting",
    }
    mariadb_storage = helm_values["slurmNodes"]["accounting"]["mariadbOperator"]["storage"]
    assert mariadb_storage["size"] == "128Gi"
    assert mariadb_storage["storageClassName"] == "compute-csi-default-sc"
    assert mariadb_storage["volumeClaimTemplate"]["accessModes"] == ["ReadWriteOnce"]
    assert mariadb_storage["volumeClaimTemplate"]["storageClassName"] == "compute-csi-default-sc"
    worker_nodeset = next(item for item in helm_values["nodesets"] if item["name"] == "worker")
    assert worker_nodeset["replicas"] == 2
    assert "image" not in worker_nodeset["slurmd"]
    assert "image" not in worker_nodeset["munge"]
    jail_tolerations = helm_values["storage"]["jail"]["tolerations"]
    assert {
        (item["key"], item.get("value"))
        for item in jail_tolerations
        if item["key"] == "slurm.nebius.ai/nodeset-name"
    } == {
        ("slurm.nebius.ai/nodeset-name", "controller"),
        ("slurm.nebius.ai/nodeset-name", "login"),
        ("slurm.nebius.ai/nodeset-name", "accounting"),
    }
    assert any(
        call[0][:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "statefulset.apps",
            "external-cluster-acct-db",
        )
        for call in runner.calls
    )
    assert any(
        call[0][:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "statefulset.apps.kruise.io",
            "worker",
        )
        for call in runner.calls
    )
    assert any(
        call[0][:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "pvc",
            "storage-external-cluster-acct-db-0",
        )
        for call in runner.calls
    )
    assert any(
        call[0][:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "slurmcluster",
            "soperator",
        )
        for call in runner.calls
    )
    flux_patches = [
        call[0]
        for call in runner.calls
        if call[0][:7]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "flux-system",
            "patch",
            "helmrelease",
        )
    ]
    assert {call[7] for call in flux_patches} == {
        "soperator-fluxcd",
        "flux-system-soperator-fluxcd-mariadb-operator-crds",
    }
    assert not any(
        call[0][:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "slurmcluster",
            "other-team",
        )
        for call in runner.calls
    )
    assert any(
        call[0][:7]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "deployment.apps,statefulset.apps,daemonset.apps,service",
        )
        and any("app.kubernetes.io/instance=soperator" in part for part in call[0])
        for call in runner.calls
    )
    assert not any(
        call[0][:7]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "deployment.apps,statefulset.apps,daemonset.apps,service",
        )
        and any("app.kubernetes.io/instance!=external-cluster" in part for part in call[0])
        for call in runner.calls
    )
    label_node_commands = [
        call[0]
        for call in runner.calls
        if call[0][:5] == ("kubectl", "--context", "external-context", "label", "node")
    ]
    assert label_node_commands
    assert any("nebius.com/node-group=cpu" in command for command in label_node_commands)
    assert not any(
        call[0][:5] == ("kubectl", "--context", "external-context", "label", "nodes")
        for call in runner.calls
    )
    assert not any(
        call[0][:3] == ("kubectl", "--context", "external-context") and "drain" in call[0]
        for call in runner.calls
    )
    assert not any(
        call[0][:4] == ("nebius", "mk8s", "node-group", "delete") for call in runner.calls
    )
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == "none"
    assert set(checkpoint["phase_state"]["rolling-compute-migration"]["target_node_groups"]) == {
        "system",
        "controller",
        "login",
        "accounting",
    }
    assert checkpoint["phase_state"]["rolling-compute-migration"][
        "in_place_worker_node_groups"
    ] == ["gpu-pool"]
    assert (
        checkpoint["phase_state"]["retire-old-resources"]["skipped_reason"]
        == "in-place worker node groups preserved"
    )


def test_execute_recovers_partial_cutover_without_login_pod(tmp_path: Path) -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups["cpu-pool"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "cpu-pool",
            "nebius.com/node-group-id": "nodegroup-cpu-pool",
        },
        "nodes": ["cpu-node-a"],
    }
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "source-soperator", "namespace": "soperator"},
        },
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "NodeSet",
            "metadata": {"name": "worker", "namespace": "soperator"},
        },
    ]
    source_report = _source_report(snapshot)
    report = source_report["report"]
    assert isinstance(report, dict)
    report["migration_plan"] = [
        {"id": "discovery-and-plan", "status": "complete"},
        {
            "id": "customer-approval",
            "status": "planned",
            "requires_customer_approval": True,
        },
        {"id": "rolling-compute-migration", "status": "planned"},
    ]
    runner = _FakeCommandRunner(
        live_pods=[
            {
                "metadata": {
                    "name": "worker-0",
                    "labels": {"app.kubernetes.io/component": "worker"},
                },
                "status": {"phase": "Running"},
            }
        ]
    )
    payload = _payload(include_placements=True)
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]
    assert isinstance(onboarding, dict)
    onboarding["actions"] = [
        "adopt-soperator",
        "upgrade-soperator",
        "approve-external-soperator-upgrade",
        "plan-soperator-compute-migration",
    ]

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "none"
    assert "Slurm quiet window check skipped for partial cutover recovery" in "\n".join(
        result.lines
    )
    assert any(
        call[0][0] == "helm" and "upgrade" in call[0] and call[0][5] == "soperator"
        for call in runner.calls
    )


def test_rolling_compute_migration_resumes_slurm_after_post_drain_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resumed: list[str] = []

    monkeypatch.setattr(
        migration,
        "_create_or_reuse_target_node_groups",
        lambda **_kwargs: (False, []),
    )
    monkeypatch.setattr(migration, "_suspend_legacy_flux_helmreleases", lambda **_kwargs: [])
    monkeypatch.setattr(
        migration,
        "_scale_down_legacy_soperator_controllers",
        lambda **_kwargs: (False, []),
    )
    monkeypatch.setattr(migration, "_has_soperator_custom_resources", lambda _snapshot: True)
    monkeypatch.setattr(migration, "_live_source_slurmcluster_present", lambda **_kwargs: True)
    monkeypatch.setattr(migration, "_ensure_slurm_quiet", lambda **_kwargs: ["quiet"])
    monkeypatch.setattr(
        migration,
        "_ensure_worker_nodeset_topology_checkpoint",
        lambda **_kwargs: [],
    )

    def fail_delete(**_kwargs: object) -> None:
        raise RuntimeError("simulated post-drain failure")

    monkeypatch.setattr(migration, "_delete_conflicting_source_slurm_resources", fail_delete)
    monkeypatch.setattr(
        migration,
        "_resume_slurm_partitions",
        lambda **_kwargs: resumed.append("resumed"),
    )

    with pytest.raises(RuntimeError, match="simulated post-drain failure"):
        runner = _FakeCommandRunner()
        migration._execute_rolling_compute_migration_phase(
            checkpoint={},
            payload=_payload(),
            source_report=_source_report(),
            live_snapshot=_snapshot(),
            target_ref="external-cluster",
            kube_context="external-context",
            worker_node_groups=("gpu-pool",),
            nebius_api=runner.nebius_api,
            command_runner=runner,
        )

    assert resumed == ["resumed"]


def test_rolling_compute_migration_guards_login_before_source_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    monkeypatch.setattr(
        migration,
        "_create_or_reuse_target_node_groups",
        lambda **_kwargs: (False, []),
    )
    monkeypatch.setattr(migration, "_has_soperator_custom_resources", lambda _snapshot: True)
    monkeypatch.setattr(migration, "_live_source_slurmcluster_present", lambda **_kwargs: True)
    monkeypatch.setattr(migration, "_external_upgrade_worker_nodeset_slurm_nodes", lambda **_kwargs: ())
    monkeypatch.setattr(migration, "_ensure_slurm_quiet", lambda **_kwargs: ["quiet"])
    monkeypatch.setattr(
        migration,
        "_ensure_worker_nodeset_topology_checkpoint",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(migration, "_patch_target_values_for_compute", lambda **_kwargs: {})
    monkeypatch.setattr(migration, "_delete_pending_accounting_pvcs", lambda **_kwargs: None)
    monkeypatch.setattr(migration, "_reconcile_target_node_storage_labels", lambda **_kwargs: None)
    monkeypatch.setattr(
        migration,
        "_login_service_identities",
        lambda **_kwargs: (
            {
                "name": "login",
                "uid": "svc-login",
                "cluster_ip": "10.0.0.10",
                "load_balancer_external": ["203.0.113.10"],
                "load_balancer_allocation_id": "vpcallocation-login",
            },
        ),
    )
    monkeypatch.setattr(
        migration,
        "_helm_upgrade_target_soperator",
        lambda **_kwargs: events.append("helm"),
    )
    monkeypatch.setattr(
        migration,
        "_ensure_rolling_login_continuity",
        lambda **_kwargs: events.append("login") or ["login ready"],
    )
    monkeypatch.setattr(
        migration,
        "_suspend_legacy_flux_helmreleases",
        lambda **_kwargs: events.append("suspend") or [],
    )
    monkeypatch.setattr(
        migration,
        "_scale_down_legacy_soperator_controllers",
        lambda **_kwargs: events.append("scale") or (True, []),
    )
    monkeypatch.setattr(
        migration,
        "_delete_conflicting_source_slurm_resources",
        lambda **_kwargs: events.append("delete"),
    )
    monkeypatch.setattr(
        migration,
        "_clear_worker_nodeset_ephemeral_storage_aliases",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(migration, "_recreate_target_worker_statefulsets", lambda **_kwargs: None)
    monkeypatch.setattr(migration, "_wait_for_target_worker_nodesets_ready", lambda **_kwargs: None)
    monkeypatch.setattr(migration, "_kubectl_rollout_status", lambda **_kwargs: None)
    monkeypatch.setattr(
        migration,
        "_resume_slurm_after_cutover",
        lambda **_kwargs: ["resumed"],
    )

    runner = _FakeCommandRunner()
    mutated, lines = migration._execute_rolling_compute_migration_phase(  # noqa: SLF001
        checkpoint={},
        payload=_payload(),
        source_report=_source_report(),
        live_snapshot=_snapshot(),
        target_ref="external-cluster",
        kube_context="external-context",
        worker_node_groups=("gpu-pool",),
        nebius_api=runner.nebius_api,
        command_runner=runner,
    )

    assert mutated is True
    assert events == ["helm", "login", "suspend", "scale", "delete"]
    assert "login ready" in lines


def test_reconcile_slurm_worker_runtime_identity_uses_current_worker_pods() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {
                                    "name": "worker-0",
                                    "labels": {"slurm.nebius.ai/worker": "true"},
                                },
                                "spec": {"nodeName": "computeinstance-new-0"},
                                "status": {"phase": "Running"},
                            },
                            {
                                "metadata": {
                                    "name": "worker-1",
                                    "labels": {"slurm.nebius.ai/nodeset": "worker"},
                                },
                                "spec": {"nodeName": "computeinstance-new-1"},
                                "status": {"phase": "Running"},
                            },
                            {
                                "metadata": {
                                    "name": "login-0",
                                    "labels": {"app.kubernetes.io/component": "login"},
                                },
                                "spec": {"nodeName": "computeinstance-login"},
                                "status": {"phase": "Running"},
                            },
                        ]
                    }
                ),
                "",
            )
        if command[:7] == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "exec",
            "controller-0",
        ):
            script = command[-1]
            assert "expected_instance=computeinstance-new-0" in script
            assert "expected_instance=computeinstance-new-1" in script
            assert "computeinstance-login" not in script
            assert 'scontrol update NodeName="${node}" InstanceId="${expected_instance}"' in script
            assert 'scontrol update NodeName="${node}" NodeAddr="${expected_addr}"' in script
            assert "State=RESUME" not in script
            assert "resume_nodes" not in script
            assert "state_lower" not in script
            return SoperatorMigrationCommandResult(
                command,
                0,
                "worker-0 InstanceId computeinstance-old-0 -> computeinstance-new-0\n",
                "",
            )
        return SoperatorMigrationCommandResult(command, 0, "{}", "")

    lines = migration._reconcile_slurm_worker_runtime_identity(
        command_runner=runner,
        kube_context="external-context",
        target_ref="external-cluster",
    )

    assert lines == ["worker-0 InstanceId computeinstance-old-0 -> computeinstance-new-0"]
    assert len(calls) == 2


def test_execution_lock_replaces_dead_pid_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "checkpoint.lock"
    lock_path.write_text('{"pid": 12345, "created_at": "2026-06-09T00:00:00Z"}\n')

    def _fake_kill(pid: int, signal: int) -> None:
        assert pid == 12345
        assert signal == 0
        raise ProcessLookupError

    monkeypatch.setattr(migration.os, "kill", _fake_kill)

    with migration.SoperatorMigrationExecutionLock(lock_path):
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == migration.os.getpid()

    assert not lock_path.exists()


def test_execute_blocks_incompatible_existing_target_node_group(tmp_path: Path) -> None:
    snapshot = _snapshot_with_compute_source()
    source_report = _source_report(snapshot)
    runner = _FakeCommandRunner(
        existing_node_groups=[
            _target_node_group(role, missing_role_label=(role == "controller"))
            for role in ("system", "controller", "login", "accounting")
        ],
        live_slurmclusters=[
            {"metadata": {"name": "soperator", "namespace": "soperator"}},
            {"metadata": {"name": "external-cluster", "namespace": "soperator"}},
        ],
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(include_placements=True),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "rolling-compute-migration"
    assert "existing target node group 'external-cluster-controller' is incompatible" in (
        result.pending_reason
    )
    assert "slurm.nebius.ai/nodeset-name, slurm.nebius.ai/nodeset" in result.pending_reason


def test_final_cutover_blocks_when_target_slurmcluster_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_with_compute_source()
    checkpoint = {
        "phase_state": {
            "rolling-compute-migration": {"target_node_groups": {"worker": {"id": "target-worker"}}}
        }
    }
    runner = _FakeCommandRunner(
        live_slurmclusters=[{"metadata": {"name": "soperator", "namespace": "soperator"}}],
        slurm_resource_names=[
            "slurmcluster.slurm.nebius.ai/soperator",
            "nodeset.slurm.nebius.ai/worker",
        ],
    )
    ticks = iter((0.0, 901.0))
    monkeypatch.setattr(migration.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(migration.time, "sleep", lambda _: None)

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="target SlurmCluster 'external-cluster' did not become Available",
    ):
        migration._execute_final_cutover_phase(
            checkpoint=checkpoint,
            live_snapshot=snapshot,
            target_ref="external-cluster",
            kube_context="external-context",
            command_runner=runner,
        )


def test_completed_compute_reconcile_blocks_until_worker_nodesets_are_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_with_compute_source()
    checkpoint = {
        "phase_state": {
            "rolling-compute-migration": {
                "target_values_revision": migration._ROLLING_COMPUTE_VALUES_REVISION,
                "in_place_worker_node_groups": ["gpu-pool"],
                "in_place_worker_node_count": 2,
                "target_node_groups": {},
            }
        }
    }
    runner = _FakeCommandRunner(
        live_nodesets={
            "worker": {
                "metadata": {"name": "worker", "namespace": "soperator"},
                "spec": {"replicas": 2},
                "status": {
                    "phase": "Provisioning",
                    "replicas": 0,
                    "conditions": [
                        {"type": "PodsReady", "status": "False"},
                    ],
                },
            }
        }
    )
    ticks = iter((0.0, 1801.0))
    monkeypatch.setattr(migration.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(migration.time, "sleep", lambda _: None)

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="target worker NodeSet\\(s\\) did not become Ready",
    ):
        migration._reconcile_completed_compute_cutover(
            checkpoint=checkpoint,
            payload=_payload(include_placements=True),
            source_report=_source_report(snapshot),
            live_snapshot=snapshot,
            target_ref="external-cluster",
            kube_context="external-context",
            command_runner=runner,
            nebius_api=runner.nebius_api,
        )


def test_execute_clears_interrupted_pending_helm_operation(tmp_path: Path) -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups["cpu-pool"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "cpu-pool",
            "nebius.com/node-group-id": "nodegroup-cpu-pool",
        },
        "nodes": ["cpu-node-a"],
    }
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "cluster", "namespace": "soperator"},
        }
    ]
    runner = _FakeCommandRunner(
        helm_errors=[
            "Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress"
        ],
        helm_history=[
            {"revision": 8, "status": "failed"},
            {"revision": 9, "status": "pending-upgrade"},
        ],
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(include_placements=True),
        source_report=_source_report(snapshot),
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "none"
    helm_upgrades = [
        call
        for call in runner.calls
        if call[0][0] == "helm" and "upgrade" in call[0] and call[0][5] == "soperator"
    ]
    assert len(helm_upgrades) == 4
    assert any(
        call[0][:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "secret",
            "sh.helm.release.v1.soperator.v9",
        )
        for call in runner.calls
    )


def test_execute_retries_soperator_helm_upgrade_while_webhook_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(migration.time, "sleep", lambda _seconds: None)
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups["cpu-pool"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "cpu-pool",
            "nebius.com/node-group-id": "nodegroup-cpu-pool",
        },
        "nodes": ["cpu-node-a"],
    }
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "cluster", "namespace": "soperator"},
        }
    ]
    runner = _FakeCommandRunner(
        helm_errors=[
            (
                "Error: failed to create resource: server-side apply failed for object "
                "soperator/worker slurm.nebius.ai/v1alpha1, Kind=NodeSet: Internal "
                "error occurred: failed calling webhook "
                '"mnodeset-v1alpha1.kb.io": failed to call webhook: Post '
                '"https://soperator-webhook-service.soperator.svc:443/mutate?timeout=10s": '
                "dial tcp 10.3.218.128:443: connect: connection refused"
            )
        ]
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(include_placements=True),
        source_report=_source_report(snapshot),
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "none"
    helm_upgrades = [
        call
        for call in runner.calls
        if call[0][0] == "helm" and "upgrade" in call[0] and call[0][5] == "soperator"
    ]
    assert len(helm_upgrades) == 4


def test_execute_reconciles_completed_compute_cutover_cleanup(tmp_path: Path) -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups["cpu-pool"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "cpu-pool",
            "nebius.com/node-group-id": "nodegroup-cpu-pool",
        },
        "nodes": ["cpu-node-a"],
    }
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "cluster", "namespace": "soperator"},
        }
    ]
    config_path = tmp_path / "config.yaml"
    source_report = _source_report(snapshot)
    payload = _payload(include_placements=True)
    _seed_stale_kube_rbac_proxy_values(payload)
    execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        backup_metadata=_backup_metadata("compute-cutover-cleanup"),
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=_FakeCommandRunner(),
    )
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["phase_state"]["rolling-compute-migration"]["target_values_revision"] = 2
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    runner = _FakeCommandRunner(
        live_slurmclusters=[
            {"metadata": {"name": "soperator", "namespace": "soperator"}},
            {"metadata": {"name": "external-cluster", "namespace": "soperator"}},
        ],
    )

    result = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        command_runner=runner,
        approve_remediation=True,
    )

    assert result.pending_phase == "none"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    rolling = checkpoint["phase_state"]["rolling-compute-migration"]
    assert rolling["target_values_revision"] == migration._ROLLING_COMPUTE_VALUES_REVISION
    assert "controller_spool_clustername_cleared_at" in rolling
    helm_upgrade = next(
        call
        for call in runner.calls
        if call[0][0] == "helm" and "upgrade" in call[0] and call[0][5] == "soperator"
    )
    assert helm_upgrade[1] is not None
    _assert_target_kube_rbac_proxy_values(json.loads(helm_upgrade[1]))
    assert any(
        call[0][:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "statefulset.apps.kruise.io",
            "worker",
        )
        for call in runner.calls
    )
    assert any(
        call[0] == ("kubectl", "--context", "external-context", "apply", "-f", "-")
        and call[1] is not None
        and "cxcli-soperator-clear-clustername" in call[1]
        for call in runner.calls
    )
    assert any(
        call[0][:8]
        == (
            "kubectl",
            "--context",
            "external-context",
            "-n",
            "soperator",
            "delete",
            "pod",
            "controller-0",
        )
        for call in runner.calls
    )


def test_execute_adopts_existing_helm_owned_resource_conflict(tmp_path: Path) -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups["cpu-pool"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "cpu-pool",
            "nebius.com/node-group-id": "nodegroup-cpu-pool",
        },
        "nodes": ["cpu-node-a"],
    }
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "cluster", "namespace": "soperator"},
        }
    ]
    runner = _FakeCommandRunner(
        helm_errors=[
            "Error: UPGRADE FAILED: unable to continue with update: PriorityClass "
            '"soperator-worker-high-priority" in namespace "" exists and cannot be imported '
            "into the current release: invalid ownership metadata; annotation validation "
            'error: key "meta.helm.sh/release-name" must equal "soperator": current value '
            'is "nodesets"'
        ]
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(include_placements=True),
        source_report=_source_report(snapshot),
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "none"
    helm_upgrade_calls = [
        call
        for call in runner.calls
        if call[0][:4] == ("helm", "--kube-context", "external-context", "upgrade")
        and call[0][5] == "soperator"
    ]
    assert len(helm_upgrade_calls) == 4
    assert (
        (
            "kubectl",
            "--context",
            "external-context",
            "label",
            "priorityclass/soperator-worker-high-priority",
            "app.kubernetes.io/managed-by=Helm",
            "--overwrite",
            "--request-timeout=20s",
        ),
        None,
    ) in runner.calls
    assert (
        (
            "kubectl",
            "--context",
            "external-context",
            "annotate",
            "priorityclass/soperator-worker-high-priority",
            "meta.helm.sh/release-name=soperator",
            "meta.helm.sh/release-namespace=soperator",
            "--overwrite",
            "--request-timeout=20s",
        ),
        None,
    ) in runner.calls


def test_execute_checkpoints_created_node_groups_before_helm_failure(tmp_path: Path) -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups["cpu-pool"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "cpu-pool",
            "nebius.com/node-group-id": "nodegroup-cpu-pool",
        },
        "nodes": ["cpu-node-a"],
    }
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "cluster", "namespace": "soperator"},
        }
    ]
    config_path = tmp_path / "config.yaml"

    with pytest.raises(RuntimeError, match="helm boom"):
        execute_soperator_migration(
            config_path=config_path,
            target_ref="external-cluster",
            payload=_payload(include_placements=True),
            source_report=_source_report(snapshot),
            snapshot_collector=lambda *, kube_context: snapshot,
            approved=True,
            command_runner=_FakeCommandRunner(helm_errors=["helm boom"]),
        )

    checkpoint = json.loads(
        soperator_migration_checkpoint_path(config_path, "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    rolling_state = checkpoint["phase_state"]["rolling-compute-migration"]
    assert set(rolling_state["target_node_groups"]) == {
        "system",
        "controller",
        "login",
        "accounting",
    }
    assert rolling_state["in_place_worker_node_groups"] == ["gpu-pool"]
    assert checkpoint["completed_phases"] == [
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "create-aligned-sfs",
        "online-bulk-data-sync",
    ]


def test_execute_allows_target_version_after_mutating_checkpoint_progress(tmp_path: Path) -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    node_groups["cpu-pool"] = {
        "gpu": False,
        "node_count": 1,
        "labels": {
            "nebius.com/node-group": "cpu-pool",
            "nebius.com/node-group-id": "nodegroup-cpu-pool",
        },
        "nodes": ["cpu-node-a"],
    }
    snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {"name": "cluster", "namespace": "soperator"},
        }
    ]
    config_path = tmp_path / "config.yaml"
    source_report = _source_report(snapshot)

    with pytest.raises(RuntimeError, match="helm boom"):
        execute_soperator_migration(
            config_path=config_path,
            target_ref="external-cluster",
            payload=_payload(include_placements=True),
            source_report=source_report,
            backup_metadata=_backup_metadata("target-version-resume"),
            snapshot_collector=lambda *, kube_context: snapshot,
            approved=True,
            command_runner=_FakeCommandRunner(helm_errors=["helm boom"]),
        )

    live_snapshot = json.loads(json.dumps(snapshot))
    live_snapshot["helm_releases"][0]["chart"] = "helm-soperator-4.0.1"
    live_snapshot["helm_releases"][0]["app_version"] = "4.0.1"

    result = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(include_placements=True),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: live_snapshot,
        approved=True,
        command_runner=_FakeCommandRunner(),
    )

    assert result.pending_phase == "none"


def test_execute_requires_inferred_worker_groups_for_approved_compute_migration(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    snapshot["node_groups"] = {
        "system": {
            "gpu": False,
            "node_count": 1,
            "labels": {
                "nebius.com/node-group": "system",
                "nebius.com/node-group-id": "nodegroup-system",
                "slurm.nebius.ai/nodeset": "system",
                "slurm.nebius.ai/workload": "cpu",
            },
            "nodes": ["system-node-a"],
        }
    }
    with pytest.raises(RuntimeError, match="could not infer source worker node groups") as exc_info:
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(snapshot),
            snapshot_collector=lambda *, kube_context: snapshot,
            approved=True,
        )
    assert "nebius-cxcli ext-soperator onboard" in str(exc_info.value)

    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["backup"] == _backup_metadata_with_archive(
        tmp_path / "config.yaml",
        "test-pre-upgrade",
    )
    assert checkpoint["completed_phases"] == []
    assert checkpoint["pending_phase"] == "none"
    assert not checkpoint.get("customer_approved_at")


def test_execute_rejects_changed_live_source_version(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Live Soperator source version changed") as exc_info:
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot("4.0.1"),
        )
    assert "nebius-cxcli ext-soperator onboard" in str(exc_info.value)


def test_execute_uses_manual_source_version_for_noncanonical_release(
    tmp_path: Path,
) -> None:
    source_snapshot = _snapshot()
    source_snapshot["helm_releases"] = [
        {
            "name": "helm-slurm-cluster",
            "namespace": "default",
            "chart": "helm-slurm-cluster",
            "app_version": "",
        }
    ]
    source_snapshot["crds"] = ["slurmclusters.slurm.nebius.ai"]
    source_report = {
        "schema": "nebius-cxcli-source-soperator-discovery/v1",
        "target_ref": "external-cluster",
        "snapshot": source_snapshot,
        "report": analyze_soperator_onboarding_snapshot(
            source_snapshot,
            target_ref="external-cluster",
            pinned_chart_version="4.0.1-ps.1",
            pinned_app_version="4.0.1",
            source_version_override="3.0.5",
        ).to_dict(),
    }

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: source_snapshot,
    )

    assert result.live_source_version == "3.0.5"
    assert result.pending_phase == "customer-approval"


def test_execute_rejects_changed_live_source_fingerprint(tmp_path: Path) -> None:
    live_snapshot = _snapshot()
    live_snapshot["node_groups"] = {
        "gpu-pool": {
            "gpu": True,
            "node_count": 3,
            "labels": {"nebius.com/node-group": "gpu-pool"},
            "allocatable": {"nvidia.com/gpu": "8"},
        }
    }

    with pytest.raises(RuntimeError, match="source discovery changed since onboarding"):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: live_snapshot,
        )

    assert not soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    ).exists()


def test_execute_allows_provider_only_node_group_label_drift(tmp_path: Path) -> None:
    live_snapshot = _snapshot()
    gpu_pool = live_snapshot["node_groups"]["gpu-pool"]  # type: ignore[index]
    labels = gpu_pool["labels"]  # type: ignore[index]
    labels.pop("nebius.com/node-group")  # type: ignore[attr-defined]

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(),
        snapshot_collector=lambda *, kube_context: live_snapshot,
    )

    assert result.live_source_version == "3.0.5"
    assert result.pending_phase == "customer-approval"


def test_execute_allows_volatile_live_source_status_drift(tmp_path: Path) -> None:
    source_snapshot = _snapshot()
    source_snapshot["soperator_resources"] = [
        {
            "apiVersion": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "metadata": {
                "name": "soperator",
                "namespace": "soperator",
                "resourceVersion": "100",
                "uid": "source-uid",
            },
            "spec": {
                "clusterType": "gpu",
                "plugStackConfig": {"pyxis": {"required": False}},
                "slurmNodes": {"controller": {}, "login": {"size": 1}},
            },
            "status": {"phase": "Available"},
        },
        {
            "apiVersion": "slurm.nebius.ai/v1alpha1",
            "kind": "NodeSet",
            "metadata": {
                "name": "worker",
                "namespace": "soperator",
                "resourceVersion": "200",
            },
            "spec": {"nodesetName": "worker"},
        },
    ]
    live_snapshot = json.loads(json.dumps(source_snapshot))
    live_snapshot["soperator_resources"][0]["metadata"]["resourceVersion"] = "101"
    live_snapshot["soperator_resources"][0]["metadata"]["uid"] = "live-uid"
    live_snapshot["soperator_resources"][0]["status"] = {"phase": "Reconciling"}
    del live_snapshot["soperator_resources"][0]["spec"]["clusterType"]
    live_snapshot["soperator_resources"][0]["spec"]["plugStackConfig"]["pyxis"]["importerPath"] = (
        "/opt/slurm_scripts/pyxis_caching_importer.sh"
    )
    live_snapshot["soperator_resources"][0]["spec"]["slurmNodes"]["controller"]["openMetrics"] = {
        "enabled": True
    }
    live_snapshot["soperator_resources"][0]["spec"]["slurmNodes"]["controller"][
        "sssdDebugLevel"
    ] = 0
    live_snapshot["soperator_resources"][0]["spec"]["slurmNodes"]["login"]["sssdDebugLevel"] = 0
    live_snapshot["soperator_resources"][1]["spec"]["initialNumberEphemeralNodes"] = 1
    live_snapshot["soperator_resources"][1]["spec"]["sssdDebugLevel"] = 0
    live_snapshot["soperator_namespace_resources"] = [
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "worker-0", "namespace": "soperator"},
            "status": {"phase": "Running", "podIP": "10.0.0.10"},
        }
    ]

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(source_snapshot),
        snapshot_collector=lambda *, kube_context: live_snapshot,
    )

    assert result.pending_phase == "customer-approval"
    assert result.mutation_performed is False


def test_execute_allows_extra_live_target_aligned_node_groups(tmp_path: Path) -> None:
    live_snapshot = _snapshot()
    node_groups = live_snapshot["node_groups"]
    assert isinstance(node_groups, dict)
    for role in ("system", "controller", "login", "accounting", "worker"):
        node_groups[role] = {
            "gpu": role == "worker",
            "node_count": 1,
            "labels": {
                "nebius.com/node-group": role,
                "slurm.nebius.ai/nodeset-name": role,
            },
            "nodes": [f"{role}-node"],
        }

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=_source_report(),
        snapshot_collector=lambda *, kube_context: live_snapshot,
    )

    assert result.pending_phase == "customer-approval"
    assert result.mutation_performed is False


def test_execute_rejects_stale_checkpoint(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "source_report_fingerprint": "old",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="checkpoint is stale"):
        execute_soperator_migration(
            config_path=config_path,
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
        )


def test_execute_allows_premutation_checkpoint_source_report_refresh(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint_path.parent.mkdir(parents=True)
    payload = _payload()
    source_report = _source_report()
    onboarding = payload["deploy"]["targets"][0]["soperator_onboarding"]  # type: ignore[index]
    report = source_report["report"]
    assert isinstance(onboarding, dict)
    assert isinstance(report, dict)
    phase_ids = migration._phase_ids_for_actions(report=report, onboarding=onboarding)
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "source_report_fingerprint": "old-source-report",
                "source_version": "3.0.5",
                "target_version": "4.0.1-ps.1",
                "planned_phases": list(phase_ids),
                "completed_phases": [],
                "pending_phase": "none",
                "events": [],
            }
        ),
        encoding="utf-8",
    )

    result = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=_FakeCommandRunner(),
    )
    assert result.pending_phase == "none"

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["source_report_fingerprint"] == migration._source_report_checkpoint_fingerprint(
        source_report
    )
    assert checkpoint["backup"] == _backup_metadata_with_archive(
        config_path,
        "test-pre-upgrade",
    )
    events = [event["event"] for event in checkpoint["events"]]
    refresh_index = events.index("execute-checkpoint-source-report-refreshed")
    backup_index = events.index("backup-metadata-checkpointed")
    assert refresh_index < backup_index


def test_phase_ids_force_populate_jail_refresh_for_node_template_segment() -> None:
    payload = _payload(target_k8s_version="1.33")
    onboarding = payload["deploy"]["targets"][0]["soperator_onboarding"]  # type: ignore[index]
    report = _source_report(target_k8s_version="1.33")["report"]
    assert isinstance(onboarding, dict)
    assert isinstance(report, dict)
    onboarding["actions"] = [
        "approve-external-soperator-upgrade",
        "upgrade-external-node-template",
        "reconcile-target-gpu-stack",
    ]

    auto_phase_ids = migration._phase_ids_for_actions(
        report=report,
        onboarding=onboarding,
        populate_jail_refresh="auto",
    )
    force_phase_ids = migration._phase_ids_for_actions(
        report=report,
        onboarding=onboarding,
        populate_jail_refresh="force",
    )

    assert migration.POPULATE_JAIL_REFRESH_PHASE_ID not in auto_phase_ids
    assert migration.POPULATE_JAIL_REFRESH_PHASE_ID in force_phase_ids
    populate_index = force_phase_ids.index(migration.POPULATE_JAIL_REFRESH_PHASE_ID)
    assert force_phase_ids.index("external-node-template-upgrade") < populate_index
    assert force_phase_ids.index("target-gpu-stack-remediation") < populate_index
    assert populate_index < force_phase_ids.index("post-upgrade-mk8s-check")


def test_phase_ids_auto_populate_jail_refresh_for_jail_only_segment() -> None:
    payload = _payload(target_k8s_version="1.32")
    onboarding = payload["deploy"]["targets"][0]["soperator_onboarding"]  # type: ignore[index]
    report = _source_report(target_k8s_version="1.32")["report"]
    assert isinstance(onboarding, dict)
    assert isinstance(report, dict)
    onboarding["actions"] = ["approve-external-soperator-upgrade"]
    report["migration_plan"] = []
    report["jail_rootfs"] = {
        "current_version": "4.0.2-slurm25.11.3-cuda12.8.0",
        "target_version": "4.0.2-slurm25.11.3-cuda12.9.0",
        "refresh_required": True,
    }

    phase_ids = migration._phase_ids_for_actions(
        report=report,
        onboarding=onboarding,
        populate_jail_refresh="auto",
    )

    assert phase_ids[:3] == (
        "discovery-and-plan",
        "customer-approval",
        migration.POPULATE_JAIL_REFRESH_PHASE_ID,
    )
    assert "external-node-template-upgrade" not in phase_ids
    assert "rolling-compute-migration" not in phase_ids
    assert "post-upgrade-mk8s-check" in phase_ids


def test_execute_rejects_retired_migration_checkpoint_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    checkpoint_path = migration.legacy_soperator_migration_checkpoint_path(
        config_path,
        "external-cluster",
    )
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text('{"schema": "retired"}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="Retired external Soperator checkpoint found"):
        execute_soperator_migration(
            config_path=config_path,
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
        )


def test_external_upgrade_resume_backup_metadata_rejects_mutating_checkpoint_without_backup(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "completed_phases": ["target-gpu-stack-remediation"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="no restore-capable backup metadata"):
        migration.external_soperator_upgrade_resume_backup_metadata(
            config_path,
            "external-cluster",
        )


def test_external_upgrade_resume_backup_metadata_ignores_completed_checkpoint(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "pending_phase": "none",
                "planned_phases": [
                    "discovery-and-plan",
                    "customer-approval",
                    "external-node-template-upgrade",
                ],
                "completed_phases": [
                    "discovery-and-plan",
                    "customer-approval",
                    "external-node-template-upgrade",
                ],
                "backup": {
                    "path": "backups/previous-hop.tar.gz",
                    "sha256": "previous-hop-sha256",
                },
            }
        ),
        encoding="utf-8",
    )

    assert (
        migration.external_soperator_upgrade_resume_backup_metadata(
            config_path,
            "external-cluster",
        )
        is None
    )


def test_external_upgrade_runtime_messages_do_not_use_retired_public_migrate_wording() -> None:
    source = Path(migration.__file__).read_text(encoding="utf-8")
    forbidden = (
        "before executing migration",
        "after migration",
        "migration complete",
        "restart the migration",
        "rerunning migration",
        "Soperator external migration",
    )

    assert [phrase for phrase in forbidden if phrase in source] == []


def test_checkpoint_allows_same_plan_report_refresh_after_mutation_started() -> None:
    phase_ids = (
        "discovery-and-plan",
        "customer-approval",
        "target-gpu-stack-remediation",
        "rolling-compute-migration",
    )
    checkpoint = migration._checkpoint_for_run(
        existing={
            "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
            "target_ref": "external-cluster",
            "source_report_fingerprint": "old",
            "source_version": "1.23.3",
            "target_version": "4.0.1-ps.1",
            "planned_phases": list(phase_ids),
            "completed_phases": ["target-gpu-stack-remediation"],
            "events": [],
        },
        target_ref="external-cluster",
        source_report_fingerprint="new",
        source_version="1.23.3",
        target_version="4.0.1-ps.1",
        phase_ids=phase_ids,
        allow_source_report_refresh=True,
    )

    assert checkpoint["source_report_fingerprint"] == "new"
    assert checkpoint["events"][-1]["event"] == "execute-checkpoint-source-report-refreshed"
    assert checkpoint["events"][-1]["previous_source_report_fingerprint"] == "old"


def test_checkpoint_rejects_progress_only_locked_path_state() -> None:
    with pytest.raises(RuntimeError, match="Old progress-only checkpoints are not supported"):
        migration._checkpoint_for_run(
            existing={
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "source_report_fingerprint": "old",
                "source_version": "3.0.5",
                "target_version": "4.0.1-ps.1",
                "planned_phases": ["discovery-and-plan"],
                "completed_phases": ["discovery-and-plan"],
                "upgrade_path_fingerprint": "locked-path-fingerprint",
                "current_segment_id": "segment-1",
                "completed_segment_ids": ["segment-1"],
                "events": [],
            },
            target_ref="external-cluster",
            source_report_fingerprint="new",
            source_version="4.0.1",
            target_version="4.0.1-ps.1",
            phase_ids=("discovery-and-plan",),
            allow_source_report_refresh=True,
            upgrade_path_fingerprint="locked-path-fingerprint",
            upgrade_path_segment_id="segment-2",
        )


def test_checkpoint_allows_report_refresh_when_resume_plan_omits_terminal_phases() -> None:
    phase_ids = (
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "rolling-compute-migration",
    )
    planned_phases = (
        *phase_ids,
        "final-control-plane-cutover",
        migration.POPULATE_JAIL_REFRESH_PHASE_ID,
        "validation-and-rollback-hold",
        "retire-old-resources",
    )
    locked_path = _locked_upgrade_path_fixture()
    locked_path["segments"][0]["id"] = "segment-1"
    locked_path["segments"][0]["title"] = "Segment 1"
    checkpoint = migration._checkpoint_for_run(
        existing={
            "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
            "target_ref": "external-cluster",
            "source_report_fingerprint": "old",
            "source_version": "1.22.3",
            "target_version": "4.0.2-ps.3",
            "planned_phases": list(planned_phases),
            "completed_phases": ["rolling-compute-migration"],
            "locked_upgrade_path": locked_path,
            "upgrade_path_fingerprint": "locked-path-fingerprint",
            "current_segment_id": "segment-1",
            "segment_state": {"segment-1": {"started_at": "old"}},
            "events": [],
        },
        target_ref="external-cluster",
        source_report_fingerprint="new",
        source_version="1.22.3",
        target_version="4.0.2-ps.3",
        phase_ids=phase_ids,
        allow_source_report_refresh=True,
        upgrade_path_fingerprint="locked-path-fingerprint",
        upgrade_path_segment_id="segment-1",
    )

    assert checkpoint["source_report_fingerprint"] == "new"
    assert checkpoint["planned_phases"] == list(planned_phases)
    assert checkpoint["segment_state"]["segment-1"]["planned_phases"] == list(planned_phases)
    assert migration.POPULATE_JAIL_REFRESH_PHASE_ID in checkpoint["planned_phases"]
    assert checkpoint["events"][-1]["event"] == "execute-checkpoint-source-report-refreshed"


def test_external_upgrade_reports_checkpoint_planned_populate_jail_when_current_phase_ids_omit(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    phase_ids = (
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "rolling-compute-migration",
    )
    planned_phases = (
        *phase_ids,
        "final-control-plane-cutover",
        "populate-jail-refresh",
        "validation-and-rollback-hold",
        "retire-old-resources",
        "post-upgrade-mk8s-check",
        "post-upgrade-helm-check",
    )
    segment_id = "segment-1"
    locked_path = _locked_upgrade_path_fixture()
    locked_path["segments"][0]["id"] = segment_id
    locked_path["segments"][0]["title"] = "Segment 1 with Jail Upgrade"
    checkpoint = {
        "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
        "target_ref": "external-cluster",
        "source_report_fingerprint": "fingerprint",
        "source_version": "1.22.3",
        "target_version": "4.0.2-ps.3",
        "pending_phase": "none",
        "pending_reason": "",
        "planned_phases": list(planned_phases),
        "completed_phases": ["rolling-compute-migration"],
        "events": [],
        "phase_state": {
            "rolling-compute-migration": {
                "target_node_groups": {"login": "nodegroup-login"},
            },
            "populate-jail-refresh": {
                "result": {
                    "status": "skipped",
                    "reason": "target rootfs image already active",
                }
            },
        },
        "locked_upgrade_path": locked_path,
        "upgrade_path_fingerprint": "locked-path-fingerprint",
        "current_segment_id": segment_id,
        "completed_segment_ids": [segment_id],
        "segment_state": {segment_id: {}},
    }

    migration._write_soperator_migrate_report(  # noqa: SLF001
        config_path=config_path,
        checkpoint_path=soperator_migration_checkpoint_path(config_path, "external-cluster"),
        checkpoint=checkpoint,
        phase_ids=phase_ids,
        completed_phases={"rolling-compute-migration"},
        target_ref="external-cluster",
        source_version="1.22.3",
        target_version="4.0.2-ps.3",
        pending_phase="none",
        pending_reason="",
        mutation_performed=True,
    )

    latest_markdown = (
        tmp_path / "generated" / "reports" / "ext-soperator-upgrade-report.md"
    ).read_text(encoding="utf-8")
    latest_json = json.loads(
        (tmp_path / "generated" / "reports" / "ext-soperator-upgrade-report.json").read_text(
            encoding="utf-8"
        )
    )
    segment_report_path, segment_json_report_path = migration.ext_soperator_upgrade_segment_report_paths(
        config_path,
        "external-cluster",
        segment_id,
    )
    segment_markdown = segment_report_path.read_text(encoding="utf-8")
    segment_json = json.loads(segment_json_report_path.read_text(encoding="utf-8"))

    assert "### populate-jail-refresh" in latest_markdown
    assert "Jail Upgrade status=skipped; target rootfs image already active." in latest_markdown
    assert "### populate-jail-refresh" in segment_markdown
    assert "Jail Upgrade status=skipped; target rootfs image already active." in segment_markdown
    latest_phases = {phase["id"]: phase for phase in latest_json["phases"]}
    segment_phases = {phase["id"]: phase for phase in segment_json["phases"]}
    assert latest_phases["populate-jail-refresh"]["top_level_stage"] == "Jail Upgrade"
    assert latest_phases["populate-jail-refresh"]["summary"] == (
        "Jail Upgrade status=skipped; target rootfs image already active."
    )
    assert segment_phases["populate-jail-refresh"] == latest_phases["populate-jail-refresh"]


def test_checkpoint_starts_fresh_after_completed_prior_hop() -> None:
    phase_ids = (
        "discovery-and-plan",
        "customer-approval",
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
    )
    checkpoint = migration._checkpoint_for_run(
        existing={
            "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
            "target_ref": "external-cluster",
            "source_report_fingerprint": "old",
            "source_version": "1.22.3",
            "target_version": "4.0.2-ps.3",
            "planned_phases": [
                "discovery-and-plan",
                "customer-approval",
                "external-node-template-upgrade",
            ],
            "completed_phases": [
                "discovery-and-plan",
                "customer-approval",
                "external-node-template-upgrade",
            ],
            "pending_phase": "none",
            "events": [{"event": "old-hop"}],
        },
        target_ref="external-cluster",
        source_report_fingerprint="new",
        source_version="4.0.2",
        target_version="4.0.2-ps.3",
        phase_ids=phase_ids,
        allow_source_report_refresh=True,
    )

    assert checkpoint["source_report_fingerprint"] == "new"
    assert checkpoint["source_version"] == "4.0.2"
    assert checkpoint["completed_phases"] == []
    assert checkpoint["planned_phases"] == list(phase_ids)
    assert checkpoint["events"] == []


def test_checkpoint_rejects_report_refresh_when_plan_changed() -> None:
    with pytest.raises(RuntimeError, match="checkpoint is stale"):
        migration._checkpoint_for_run(
            existing={
                "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
                "target_ref": "external-cluster",
                "source_report_fingerprint": "old",
                "source_version": "1.23.3",
                "target_version": "4.0.1-ps.1",
                "planned_phases": ["discovery-and-plan", "customer-approval"],
                "completed_phases": ["target-gpu-stack-remediation"],
                "events": [],
            },
            target_ref="external-cluster",
            source_report_fingerprint="new",
            source_version="1.23.3",
            target_version="4.0.1-ps.1",
            phase_ids=(
                "discovery-and-plan",
                "customer-approval",
                "target-gpu-stack-remediation",
            ),
            allow_source_report_refresh=True,
        )


def test_execute_rejects_checkpoint_with_unsupported_completed_phase(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    source_report = _source_report()
    execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: _snapshot(),
    )
    checkpoint_path = soperator_migration_checkpoint_path(config_path, "external-cluster")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["completed_phases"] = ["unknown-phase"]
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(RuntimeError, match="cannot resume safely"):
        execute_soperator_migration(
            config_path=config_path,
            target_ref="external-cluster",
            payload=_payload(),
            source_report=source_report,
            snapshot_collector=lambda *, kube_context: _snapshot(),
        )
