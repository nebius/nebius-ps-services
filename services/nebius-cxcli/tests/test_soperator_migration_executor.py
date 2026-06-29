from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.quota_checks import QuotaCheck, QuotaReport, QuotaRequirement
from nebius_cxcli.soperator_migration import (
    SoperatorMigrationCommandResult,
    soperator_migration_checkpoint_path,
)
from nebius_cxcli.soperator_migration import (
    execute_soperator_migration as _execute_soperator_migration,
)
from nebius_cxcli.soperator_onboarding import (
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
    values: dict[str, Any] = {}
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


def _backup_metadata(label: str = "original-pre-upgrade") -> dict[str, Any]:
    return {
        "path": f"backups/{label}.tar.gz",
        "sha256": f"{label}-sha256",
        "manifest_sha256": f"{label}-manifest-sha256",
    }


def execute_soperator_migration(*args: Any, **kwargs: Any):
    if kwargs.get("approved") is True and "backup_metadata" not in kwargs:
        kwargs["backup_metadata"] = _backup_metadata("test-pre-upgrade")
    return _execute_soperator_migration(*args, **kwargs)


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
        worker_topology_by_nodeset: dict[str, dict[str, int]] | None = None,
        slurm_resource_names: list[str] | None = None,
        slurm_queue_output: str = "",
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
                }
            ]
        )
        self.live_pvcs = list(live_pvcs or [])
        self.live_slurmclusters = list(
            live_slurmclusters
            or [
                {
                    "metadata": {"name": "external-cluster", "namespace": "soperator"},
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
            }
        )
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
        self.slurm_queue_returncode = slurm_queue_returncode
        self.file_update_payloads: list[dict[str, Any]] = []

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
        if (
            command
            and command[0] == "kubectl"
            and "--context" in command
            and "-n" in command
            and "get" in command
            and "-o" in command
            and command[-1] == "json"
        ):
            kind_by_resource = {
                "deployment": "Deployment",
                "deploy": "Deployment",
                "statefulset": "StatefulSet",
                "daemonset": "DaemonSet",
            }
            resource = command[command.index("get") + 1]
            name = command[command.index("get") + 2]
            namespace = command[command.index("-n") + 1]
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
        if (
            len(command) >= 9
            and command[:6]
            == (
                "kubectl",
                "--context",
                "external-context",
                "-n",
                "soperator",
                "get",
            )
            and command[6].startswith("job/")
            and command[-2:] == ("-o", "json")
        ):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"status": {"conditions": [{"type": "Complete", "status": "True"}]}}),
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
                    payload.setdefault("status", {"phase": "Available"})
                    return SoperatorMigrationCommandResult(command, 0, json.dumps(payload), "")
            return SoperatorMigrationCommandResult(command, 1, "", "NotFound")
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
            if command[8:11] == ("sinfo", "-h", "-o"):
                if len(command) > 11 and command[11] == "%N %T":
                    return SoperatorMigrationCommandResult(
                        command,
                        0,
                        "worker-gpu-[0-1] drained\nworker-cpu-[0-1] idle\n",
                        "",
                    )
                return SoperatorMigrationCommandResult(command, 0, "idle\nallocated\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorMigrationCommandResult(
                    command,
                    self.slurm_queue_returncode,
                    self.slurm_queue_output,
                    "" if self.slurm_queue_returncode == 0 else "squeue failed",
                )
            if command[8:10] == ("bash", "-lc") and "cxcli-soperator-smoke" in command[10]:
                return SoperatorMigrationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-0\n",
                    "",
                )
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

    requirements, gaps, lines = migration._new_target_node_group_quota_requirements(
        payload=_payload(include_placements=True),
        target_ref="external-cluster",
        source_report=_source_report(snapshot),
        worker_node_groups=("gpu-pool",),
        command_runner=_FakeCommandRunner(),
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

    requirements, gaps, lines = migration._safe_surge_node_group_quota_requirements(
        payload=payload,
        target_ref="external-cluster",
        source_report=_source_report(snapshot),
        worker_node_groups=("gpu-pool",),
        command_runner=_FakeCommandRunner(),
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
        "worker_wave_percent": 5,
        "max_parallel_worker_groups": 3,
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
        "worker_wave_groups": 1,
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

    changed, attachments = migration._attach_filesystems_to_source_node_groups(
        command_runner=runner,
        source_report=_source_report(),
        attachment_keys_by_group={"gpu-pool": ("jail",)},
        filesystem_ids_by_key={"jail": "filesystem-jail"},
        specs_by_key=specs_by_key,
    )

    assert changed is True
    assert attachments == [
        {
            "source_group": "gpu-pool",
            "node_group_id": "nodegroup-gpu-pool",
            "filesystem_keys": ["jail"],
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


def test_zero_surge_full_update_file_clears_cpu_gpu_settings() -> None:
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
        command_runner=runner,
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
    assert "--file" in update_calls[0]
    assert "--template-gpu-settings-drivers-preset" not in update_calls[0]
    node_group = runner.existing_node_groups[0]
    assert runner.file_update_payloads
    file_spec = runner.file_update_payloads[0]["spec"]
    assert isinstance(file_spec, dict)
    assert file_spec["strategy"]["drain_timeout"] == "1800s"
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
        "validation-and-rollback-hold",
        "retire-old-resources",
    )
    assert result.pending_phase == "none"
    assert "Auto-selected source worker node groups: gpu-pool" in "\n".join(result.lines)
    assert "target-gpu-stack-remediation: Applied target GPU stack chart" in "\n".join(result.lines)
    assert "post-upgrade-helm-check: Verified target Soperator Helm chart readiness" in "\n".join(
        result.lines
    )
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
        "jail",
        "controller-spool",
        "accounting",
    }
    assert {
        item["id"] for item in checkpoint["phase_state"]["target-gpu-stack-remediation"]["charts"]
    } == {"nvidia-gpu-operator", "nvidia-network-operator"}
    for phase_id in result.completed_phases:
        if phase_id in {"discovery-and-plan", "customer-approval"}:
            continue
        verification = checkpoint["phase_state"][phase_id]["fast_verification"]
        assert verification["status"] == "passed"
        assert verification["passed"] is True
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
    assert stage_verification["post-upgrade-mk8s-check"]["status"] == "passed"
    assert stage_verification["post-upgrade-helm-check"]["status"] == "passed"
    phase_reports = {item["id"]: item for item in upgrade_json_report["phases"]}
    assert phase_reports["target-gpu-stack-remediation"]["fast_verification"]["status"] == "passed"
    assert phase_reports["post-upgrade-helm-check"]["fast_verification"]["status"] == "passed"

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
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == "post-upgrade-helm-check"
    assert checkpoint["phase_state"]["post-upgrade-mk8s-check"]["fast_verification"][
        "status"
    ] == "passed"
    helm_verification = checkpoint["phase_state"]["post-upgrade-helm-check"][
        "fast_verification"
    ]
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
    migrate_report = (reports_dir / "ext-soperator-upgrade-report.md").read_text(
        encoding="utf-8"
    )
    assert "`post-upgrade-helm-check`: `FAIL`" in migrate_report


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
    assert helm_calls == 1
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == "post-upgrade-mk8s-check"
    mk8s_verification = checkpoint["phase_state"]["post-upgrade-mk8s-check"][
        "fast_verification"
    ]
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
    migrate_report = (reports_dir / "ext-soperator-upgrade-report.md").read_text(
        encoding="utf-8"
    )
    assert "`post-upgrade-mk8s-check`: `FAIL`" in migrate_report
    assert "`post-upgrade-helm-check`: `PENDING`" in migrate_report


def test_execute_preserves_original_backup_metadata_after_mutating_resume(
    tmp_path: Path,
) -> None:
    source_report = _source_report()
    config_path = tmp_path / "config.yaml"
    original_backup = {
        "path": "backups/original-pre-upgrade.tar.gz",
        "sha256": "original-sha256",
        "manifest_sha256": "original-manifest-sha256",
    }
    replacement_backup = {
        "path": "backups/partial-state.tar.gz",
        "sha256": "partial-sha256",
        "manifest_sha256": "partial-manifest-sha256",
    }
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
    assert retire_pending_observations == [("none", "")]
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["pending_phase"] == "none"
    assert checkpoint["pending_reason"] == ""
    assert any(
        event.get("event") == "execute-pending-cleared"
        and event.get("phase") == "validation-and-rollback-hold"
        for event in checkpoint["events"]
    )


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
    assert checkpoint["upgrade_safety"]["post_upgrade_verification"]["status"] == "passed"
    assert checkpoint["upgrade_safety"]["protected_customer_state"]["before_hash"]
    assert checkpoint["upgrade_safety"]["protected_customer_state"]["after_hash"]
    assert upgrade_json_report["schema"] == "nebius-cxcli-ext-soperator-upgrade-report/v1"
    assert upgrade_json_report["target_ref"] == "external-cluster"
    assert upgrade_json_report["upgrade_performed"] is True
    assert upgrade_json_report["pending_phase"] == "none"
    assert upgrade_json_report["checkpoint_path"].endswith(
        ".nebius-cxcli/ext-soperator-upgrades/external-cluster/checkpoint.json"
    )
    assert upgrade_json_report["backup"]["path"] == "backups/test-pre-upgrade.tar.gz"
    assert upgrade_json_report["backup"]["sha256"] == "test-pre-upgrade-sha256"
    assert upgrade_json_report["post_upgrade_verification"]["status"] == "passed"
    assert upgrade_json_report["protected_customer_state"]["before_hash"]
    assert upgrade_json_report["fast_smoke"]["status"] == "passed"
    assert {item["phase_id"]: item["status"] for item in upgrade_json_report["stage_verification"]}[
        "validation-and-rollback-hold"
    ] == "passed"
    assert "validation-and-rollback-hold" in {
        phase["id"] for phase in upgrade_json_report["phases"]
    }


def test_external_node_template_quiesces_one_node_service_roles(
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
            "node_count": 1,
            "labels": {
                "nebius.com/node-group": "login",
                "nebius.com/node-group-id": "nodegroup-login",
                "slurm.nebius.ai/nodeset": "login",
            },
            "nodes": ["login-node-a"],
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
                **_legacy_service_node_group("login"),
                "spec": {
                    **_legacy_service_node_group("login")["spec"],
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
        "worker_wave_percent": 1,
        "worker_group_strategy": {
            "max_surge_count": 1,
            "max_unavailable_count": 0,
            "drain_timeout": "30m",
        },
    }
    assert phase["control_plane"]["hops"]["1.32"]["status"] == "completed"
    assert phase["node_groups"]["login"]["strategy"] == "safe-surge"
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
            ]
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
    )

    assert result.pending_phase == "external-node-template-upgrade"
    assert "Running Slurm jobs exist on affected nodes" in result.pending_reason
    assert not any(
        call[0][:4] == ("nebius", "mk8s", "node-group", "update") and "--version" in call[0]
        for call in runner.calls
    )


def _external_upgrade_slurm_runner(
    *,
    queue_outputs: list[str],
    calls: list[tuple[str, ...]],
) -> Callable[..., SoperatorMigrationCommandResult]:
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
        ) and command[8:10] == ("squeue", "-h"):
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
                        "   NodeAddr=10.1.18.140 NodeHostName=worker-3 Version=24.11.6",
                        "   InstanceId=computeinstance-e00e8ctxvehgkyged0",
                    )
                ),
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
    assert len(squeue_calls) == 1
    assert squeue_calls[0][-2:] == ("-w", "worker-3")


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
        if command[6:11] == ("controller-0", "-c", "slurmctld", "--", "scontrol"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                "\n".join(
                    (
                        "NodeName=worker-0 Arch=x86_64 CoresPerSocket=8",
                        "   NodeAddr=10.1.18.140 NodeHostName=worker-0 Version=24.11.6",
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
        if command[6:11] == ("controller-0", "-c", "slurmctld", "--", "squeue"):
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
    assert [job.nodes for job in jobs] == ["worker-0"]
    assert any(call[6:11] == ("controller-0", "-c", "slurmctld", "--", "squeue") for call in calls)


def test_external_upgrade_slurm_jobs_retries_unfiltered_for_invalid_node_name() -> None:
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

    jobs = migration._external_upgrade_slurm_jobs(
        command_runner=runner,
        kube_context="external-context",
        node_names=("computeinstance-1",),
    )

    assert jobs == ()
    squeue_calls = [call for call in calls if call[8:10] == ("squeue", "-h")]
    assert len(squeue_calls) == 2
    assert squeue_calls[0][-2:] == ("-w", "computeinstance-1")
    assert "-w" not in squeue_calls[1]


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

    assert lines == ["Slurm job preflight: cleared blocking jobs with policy cancel-selected."]
    assert any(call[8:] == ("scancel", "42") for call in calls)


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

    assert lines == ["Slurm job preflight: cleared blocking jobs with policy cancel-all."]
    assert any(call[8:] == ("scancel", "42", "43") for call in calls)


def test_external_upgrade_wait_policy_waits_until_jobs_clear(
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
        policy="wait",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=30,
        refresh_interval_seconds=1,
    )

    assert lines == ["Slurm job preflight: waited for blocking jobs to finish."]
    assert sleeps == [1]
    assert queue_outputs == []


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

    assert lines == ["Slurm job preflight: cleared blocking jobs with policy requeue-selected."]
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
        "Slurm job preflight: cleared blocking jobs with policy requeue-hold-selected."
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

    assert lines == ["Slurm job preflight: cleared blocking jobs with policy requeue-hold-all."]
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

    assert lines == ["Slurm job preflight: cleared blocking jobs with policy requeue-all."]
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

    with pytest.raises(RuntimeError, match="status not returned by Nebius CLI"):
        migration._verify_completed_soperator_migration_mk8s_state(
            payload=_payload(target_k8s_version="1.34"),
            source_report=source_report,
            target_ref="external-cluster",
            worker_node_groups=("gpu-pool",),
            phase_ids=("external-node-template-upgrade",),
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
                "backup": _backup_metadata("control-plane-hop"),
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
    assert all(index < helm_upgrade_index for index, _call in legacy_nodeset_deletes)
    assert soperator_helm_upgrade[1] is not None
    helm_values = json.loads(soperator_helm_upgrade[1])
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


def test_final_cutover_reconcile_checks_preserved_worker_node_config() -> None:
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
    stale_runner = _FakeCommandRunner(
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
        command_runner=stale_runner,
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
        command_runner=stale_runner,
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
                "spec": {"nodeConfig": {"static": "CPUs=128 Gres=gpu:8"}},
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
    assert "workers drained=1, idle=1" in signal.summary
    assert "problem workers worker-gpu-[0-1]:drained" in signal.summary
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
    }
    assert migration._status_scope_for_phase("target-gpu-stack-remediation", compute_only) == {
        "storage": False,
        "mk8s": True,
        "slurm": False,
        "soperator": False,
    }
    assert migration._status_scope_for_phase("rolling-compute-migration", compute_only) == {
        "storage": False,
        "mk8s": True,
        "slurm": True,
        "soperator": True,
    }
    assert migration._status_scope_for_phase("final-control-plane-cutover", storage_only) == {
        "storage": True,
        "mk8s": True,
        "slurm": True,
        "soperator": True,
    }
    assert migration._status_scope_for_phase("final-control-plane-cutover", compute_only) == {
        "storage": False,
        "mk8s": True,
        "slurm": True,
        "soperator": True,
    }
    assert migration._status_scope_for_phase(
        "validation-and-rollback-hold",
        ("discovery-and-plan", "validation-and-rollback-hold"),
    ) == {
        "storage": False,
        "mk8s": False,
        "slurm": False,
        "soperator": False,
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
    assert len(apply_calls) == 1
    assert apply_calls[0][1] is not None
    applied = json.loads(apply_calls[0][1])
    job_names = {item["metadata"]["name"] for item in applied["items"]}
    assert job_names == {
        "cxcli-soperator-sync-jail",
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
        and call[0][6].startswith("job/")
    ]
    assert len(job_status_calls) == 9


def test_execute_blocks_data_copy_when_target_pvc_is_missing(tmp_path: Path) -> None:
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

    assert result.pending_phase == "online-bulk-data-sync"
    assert "Missing PVCs: target:jail:jail-pvc" in result.pending_reason


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
    assert set(updated_node_groups) == {"nodegroup-cpu-pool", "nodegroup-gpu-pool"}
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


def test_execute_rejects_incompatible_existing_aligned_sfs(tmp_path: Path) -> None:
    runner = _FakeCommandRunner(
        existing_filesystems={
            "external-cluster-jail": {
                "metadata": {"id": "filesystem-existing-jail"},
                "spec": {
                    "size_gibibytes": "128",
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
    assert "existing aligned SFS filesystem 'external-cluster-jail' is incompatible" in (
        result.pending_reason
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
    assert helm_values["gpuDriverJail"] == {"enabled": True}
    assert helm_values["customSlurmConfig"] == "PluginDir=/usr/lib/x86_64-linux-gnu/slurm"
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
    runner = _FakeCommandRunner(live_pods=[])
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
        migration._execute_rolling_compute_migration_phase(
            checkpoint={},
            payload=_payload(),
            source_report=_source_report(),
            live_snapshot=_snapshot(),
            target_ref="external-cluster",
            kube_context="external-context",
            worker_node_groups=("gpu-pool",),
            command_runner=_FakeCommandRunner(),
        )

    assert resumed == ["resumed"]


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
    assert len(helm_upgrades) == 2
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
    assert len(helm_upgrades) == 2


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
    execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(include_placements=True),
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
        payload=_payload(include_placements=True),
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
    assert len(helm_upgrade_calls) == 2
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

    assert not soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    ).exists()


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
            "schema": "nebius-cxcli-soperator-migration-execution/v1",
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


def test_checkpoint_rejects_report_refresh_when_plan_changed() -> None:
    with pytest.raises(RuntimeError, match="checkpoint is stale"):
        migration._checkpoint_for_run(
            existing={
                "schema": "nebius-cxcli-soperator-migration-execution/v1",
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
