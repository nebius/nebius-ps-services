from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.quota_checks import QuotaCheck, QuotaReport
from nebius_cxcli.soperator_migration import (
    SoperatorMigrationCommandResult,
    execute_soperator_migration,
    soperator_migration_checkpoint_path,
)
from nebius_cxcli.soperator_onboarding import (
    analyze_soperator_onboarding_snapshot,
    soperator_onboarding_fingerprint,
)


def _snapshot(version: str = "3.0.5") -> dict[str, object]:
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


def _source_worker_nodeset(name: str, *, gpu: bool) -> dict[str, object]:
    slurmd_resources: dict[str, object] = {
        "cpu": "127000m" if gpu else "15000m",
        "memory": "1438Gi" if gpu else "55Gi",
        "ephemeralStorage": "387Gi",
    }
    if gpu:
        slurmd_resources["nvidia.com/gpu"] = 8
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


def _source_report(snapshot: dict[str, object] | None = None) -> dict[str, object]:
    snapshot = snapshot or _snapshot()
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="external-cluster",
        pinned_chart_version="4.0.1-ps.1",
        pinned_app_version="4.0.1",
    ).to_dict()
    return {
        "schema": "nebius-cxcli-source-soperator-discovery/v1",
        "target_ref": "external-cluster",
        "snapshot": snapshot,
        "report": report,
    }


def _ready_node_group_status(version: str = "1.31", *, nodes: int = 1) -> dict[str, object]:
    return {
        "version": version,
        "ready_node_count": nodes,
        "target_node_count": nodes,
        "node_count": nodes,
        "outdated_node_count": 0,
        "reconciling": False,
    }


def _payload(*, include_role_mapping: bool = False) -> dict[str, object]:
    values: dict[str, object] = {}
    if include_role_mapping:
        values["nodeGroupMapping"] = {
            "system": ["cpu-pool"],
            "controller": ["cpu-pool"],
            "login": ["cpu-pool"],
            "accounting": ["cpu-pool"],
            "worker": ["gpu-pool"],
        }
    payload: dict[str, object] = {
        "client_info": {
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "eu-north1",
            }
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "external-cluster",
                    "enabled": True,
                    "install_mode": "onboard-existing-cluster",
                    "values": values,
                },
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
                            "remediate-target-gpu-stack",
                            "upgrade-soperator",
                            "approve-soperator-migration",
                            "create-aligned-sfs",
                            "plan-soperator-data-migration",
                            "plan-soperator-compute-migration",
                        ],
                        "source_version": "3.0.5",
                        "target_version": "4.0.1-ps.1",
                        "migration_profile_id": "v3-to-target",
                        "analysis_fingerprint": "",
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


def _add_external_gpu_cluster_inventory(payload: dict[str, object]) -> None:
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
        existing_filesystems: dict[str, dict[str, object]] | None = None,
        helm_errors: list[str] | None = None,
        helm_history: list[dict[str, object]] | None = None,
        existing_node_groups: list[dict[str, object]] | None = None,
        live_nodes: list[dict[str, object]] | None = None,
        live_pods: list[dict[str, object]] | None = None,
        live_pvcs: list[dict[str, object]] | None = None,
        live_slurmclusters: list[dict[str, object]] | None = None,
        live_nodesets: dict[str, dict[str, object]] | None = None,
        live_flux_helmreleases: list[dict[str, object]] | None = None,
        helm_statuses: dict[tuple[str, str], str] | None = None,
        helm_releases: list[dict[str, object]] | None = None,
        helm_manifests: dict[tuple[str, str], str] | None = None,
        live_workloads: dict[tuple[str, str, str], dict[str, object]] | None = None,
        live_kubernetes_resources: dict[str, list[dict[str, object]]] | None = None,
        helm_storage_secrets: list[dict[str, object]] | None = None,
        worker_topology_by_nodeset: dict[str, dict[str, int]] | None = None,
        slurm_resource_names: list[str] | None = None,
        cluster: dict[str, object] | None = None,
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.existing_filesystems = existing_filesystems or {}
        self.helm_errors = list(helm_errors or [])
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
        self.file_update_payloads: list[dict[str, object]] = []

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

    def _remove_helm_release_if_storage_is_gone(self, *, release_name: str) -> None:
        def _metadata_labels(secret: dict[str, object]) -> dict[str, object]:
            metadata = secret.get("metadata")
            if not isinstance(metadata, dict):
                return {}
            labels = metadata.get("labels")
            return labels if isinstance(labels, dict) else {}

        if any(
            str(_metadata_labels(secret).get("name", "")) == release_name
            for secret in self.helm_storage_secrets
        ):
            return
        self.helm_releases = [
            release
            for release in self.helm_releases
            if str(release.get("name", "") or "") != release_name
        ]

    def _delete_live_kubernetes_resource(
        self,
        *,
        resource_type: str,
        name: str,
        namespace: str = "",
    ) -> None:
        resources = self.live_kubernetes_resources.get(resource_type, [])
        kept: list[dict[str, object]] = []
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
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del timeout_seconds
        command = tuple(str(item) for item in args)
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
            and command[-1] == "json"
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
            len(command) >= 8
            and command[:3] == ("kubectl", "--context", "external-context")
            and command[3] == "-n"
            and command[5:7] == ("delete", "secret")
        ):
            namespace = command[4]
            secret_name = command[7]
            release_names: set[str] = set()
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
                    release_names.add(str(labels.get("name", "") or ""))
            self.helm_storage_secrets = [
                secret
                for secret in self.helm_storage_secrets
                if not (
                    isinstance(secret.get("metadata"), dict)
                    and str(secret["metadata"].get("namespace", "") or "") == namespace
                    and str(secret["metadata"].get("name", "") or "") == secret_name
                )
            ]
            for release_name in release_names:
                self._remove_helm_release_if_storage_is_gone(release_name=release_name)
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
                payload = dict(workload)
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
                return SoperatorMigrationCommandResult(command, 0, "", "")
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
        return SoperatorMigrationCommandResult(command, 0, "{}", "")


def _target_node_group(role: str, *, missing_role_label: bool = False) -> dict[str, object]:
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
) -> dict[str, object]:
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


def _snapshot_with_compute_source() -> dict[str, object]:
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
                    component_label="Soperator migration target external-cluster",
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
            payload=_payload(include_role_mapping=True),
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
    recorded_inputs: list[dict[str, object]] = []

    def _record_quota_inputs(**kwargs: object):
        inputs = kwargs.get("inputs")
        assert isinstance(inputs, dict)
        recorded_inputs.append(inputs)
        return (), ()

    monkeypatch.setattr(migration, "estimate_mk8s_quota_requirements", _record_quota_inputs)

    requirements, gaps, lines = migration._new_target_node_group_quota_requirements(
        payload=_payload(include_role_mapping=True),
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
        "Quota preflight worker node groups: preserved in place with zero-surge "
        "migration-owned template remediation, no parallel or surge worker quota required "
        "(gpu-pool); capacity may be reduced by one node in the active group during rollout."
    ) in lines


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
    source_report = _source_report()
    runner = _FakeCommandRunner()
    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
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
    assert "post-migration-helm-check: Verified target Soperator Helm chart readiness" in "\n".join(
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
    assert checkpoint["events"][1]["event"] == "customer-approval-recorded"
    assert set(checkpoint["phase_state"]["create-aligned-sfs"]["filesystems"]) == {
        "jail",
        "controller-spool",
        "accounting",
    }
    assert {
        item["id"] for item in checkpoint["phase_state"]["target-gpu-stack-remediation"]["charts"]
    } == {"nvidia-gpu-operator", "nvidia-network-operator"}

    runner.calls.clear()
    resumed = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        command_runner=runner,
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
    assert "post-migration-helm-check: Verified target Soperator Helm chart readiness" in "\n".join(
        resumed.lines
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["completed_phases"] == list(result.completed_phases)
    assert checkpoint["pending_phase"] == "none"
    assert checkpoint["worker_node_groups"] == ["gpu-pool"]


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
    )

    output = "\n".join(result.lines)
    assert result.pending_phase == "none"
    assert "post-migration-mk8s-check: External MK8s" in output
    assert "post-migration-helm-check: Verified target Soperator Helm chart readiness" in output
    assert "Retired stale source Soperator Helm release records" in output
    assert "soperator-system/soperator-controller" in output
    assert "soperator/slurm-cluster-storage" in output
    assert "Suspended old Flux HelmRelease desired state" in output
    assert "flux-system/soperator-fluxcd" in output
    assert "Suspended old Flux Kustomization desired state: flux-system/flux-system." in output
    assert "Pruned 2 old source operational resource(s)." in output
    assert "Preserved shared/storage/custom resources for source release(s): slurm-cluster-storage." in (
        output
    )
    assert "Deleted 2 stale Helm storage record(s)." in output

    remaining_release_names = {str(release.get("name", "") or "") for release in runner.helm_releases}
    assert "soperator-controller" not in remaining_release_names
    assert "slurm-cluster-storage" not in remaining_release_names
    assert runner.live_kubernetes_resources["deployment"] == []
    assert runner.live_kubernetes_resources["service"] == []
    assert runner.live_kubernetes_resources["persistentvolumeclaim"]
    assert runner.live_kubernetes_resources["nodeset"]
    assert all(
        item.get("spec", {}).get("suspend") is True
        for item in runner.live_flux_helmreleases
    )
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
        if call[0][:3] == ("kubectl", "--context", "external-context")
        and "delete" in call[0]
    ]
    assert not any("persistentvolumeclaim" in command for command in deleted_resources)
    assert not any("nodeset" in command for command in deleted_resources)


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
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
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


def test_target_gpu_stack_remediation_applies_catalog_post_render_patches() -> None:
    payload = _payload()
    _add_external_gpu_cluster_inventory(payload)
    runner = _FakeCommandRunner()
    checkpoint: dict[str, object] = {}

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
        call[0]
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
            "report_file": "external-cluster-gpu-stack-readiness-report.json",
        },
        {
            "kind": "mk8s_gpu_visibility",
            "target_ref": "external-cluster",
            "name": "GPU Visibility test",
            "report_file": "external-cluster-gpu-visibility-report.json",
        },
        {
            "kind": "mk8s_nccl",
            "target_ref": "external-cluster",
            "name": "NCCL test",
            "report_file": "external-cluster-nccl-test-report.json",
        },
        {
            "kind": "mk8s_nccl",
            "target_ref": "other-cluster",
            "name": "Other NCCL test",
            "report_file": "other-nccl-test-report.json",
        },
    ]
    calls: list[dict[str, object]] = []

    def _fake_specs(_payload: object) -> list[dict[str, object]]:
        return [dict(item) for item in specs]

    def _fake_run(
        validations: list[dict[str, object]],
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
        emit("Starting validation 1/3: GPU stack readiness.")
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
        "mk8s_nccl",
    ]
    assert calls[0]["extra_env"] == {
        "KUBECTL_CONTEXT": "external-context",
        "HELM_KUBECONTEXT": "external-context",
    }
    assert "validation-and-rollback-hold: Starting validation 1/3" in "\n".join(result.lines)
    reports_dir = tmp_path / "generated" / "reports"
    assert (reports_dir / "external-cluster-nccl-test-report.json").exists()
    assert "## Validations" in (reports_dir / "deploy-report.md").read_text(encoding="utf-8")
    migrate_report = (reports_dir / "migrate-report.md").read_text(encoding="utf-8")
    assert "## Migration Steps" in migrate_report
    assert "Soperator and Slurm smoke" in migrate_report
    assert "soperator-cluster-validation-report-external-cluster.json" in migrate_report
    assert "`external-cluster-gpu-stack-readiness-report.json`: `PASS`" in migrate_report
    assert "`external-cluster-gpu-visibility-report.json`: `PASS`" in migrate_report
    assert "`external-cluster-nccl-test-report.json`: `PASS`" in migrate_report
    soperator_report = json.loads(
        (reports_dir / "soperator-cluster-validation-report-external-cluster.json").read_text(
            encoding="utf-8"
        )
    )
    assert soperator_report["status"] == "passed"
    assert any(check["name"] == "Slurm srun smoke job" for check in soperator_report["checks"])
    checkpoint = json.loads(
        soperator_migration_checkpoint_path(tmp_path / "config.yaml", "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    validation_state = checkpoint["phase_state"]["validation-and-rollback-hold"]
    assert validation_state["validation_contract_revision"] == 2
    assert validation_state["mk8s_gpu_validation_count"] == 3
    assert validation_state["soperator_cluster_validation_count"] == 1
    assert len(validation_state["mk8s_gpu_validation_reports"]) == 3
    assert len(validation_state["soperator_cluster_validation_reports"]) == 1
    assert checkpoint["migrate_report"].endswith("generated/reports/migrate-report.md")


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


def test_execute_upgrades_external_node_template_with_zero_surge_strategy(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    source_report = _source_report()
    runner = _FakeCommandRunner()

    result = execute_soperator_migration(
        config_path=config_path,
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: _snapshot(),
        approved=True,
        command_runner=runner,
    )

    assert result.pending_phase == "none"
    cluster_updates = [
        call[0] for call in runner.calls if call[0][:4] == ("nebius", "mk8s", "cluster", "update")
    ]
    assert [
        command[command.index("--control-plane-version") + 1] for command in cluster_updates
    ] == ["1.32", "1.33"]
    node_template_updates = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" in call[0]
    ]
    assert node_template_updates
    update_command = node_template_updates[0]
    assert update_command[update_command.index("--version") + 1] == "1.33"
    assert (
        update_command[update_command.index("--template-gpu-settings-drivers-preset") + 1]
        == "cuda13.0"
    )
    assert update_command[update_command.index("--strategy-max-surge-count") + 1] == "0"
    assert update_command[update_command.index("--strategy-max-unavailable-count") + 1] == "1"
    assert update_command[update_command.index("--strategy-drain-timeout") + 1] == "30m"
    restore_commands = [
        call[0]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
        and call[0][4] == "nodegroup-gpu-pool"
        and "--version" not in call[0]
        and "--template-filesystems" not in call[0]
    ]
    assert restore_commands
    restore_command = restore_commands[0]
    assert restore_command[restore_command.index("--strategy-max-surge-count") + 1] == "1"
    assert restore_command[restore_command.index("--strategy-max-unavailable-count") + 1] == "0"
    assert restore_command[restore_command.index("--strategy-drain-timeout") + 1] == "0s"

    checkpoint = json.loads(
        soperator_migration_checkpoint_path(config_path, "external-cluster").read_text(
            encoding="utf-8"
        )
    )
    phase = checkpoint["phase_state"]["external-node-template-upgrade"]
    assert phase["control_plane"]["hops"]["1.33"]["status"] == "completed"
    assert phase["node_groups"]["gpu-pool"]["strategy_restored"] is True
    assert any(
        line.startswith("post-migration-mk8s-check: External MK8s node-template verified")
        for line in result.lines
    )


def test_completed_migration_mk8s_check_fails_when_live_node_template_drifts() -> None:
    source_report = _source_report()
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
                    "version": "1.33",
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
            payload=_payload(),
            source_report=source_report,
            target_ref="external-cluster",
            worker_node_groups=("gpu-pool",),
            phase_ids=("external-node-template-upgrade",),
            command_runner=runner,
        )


def test_completed_migration_mk8s_check_fails_when_status_is_missing() -> None:
    source_report = _source_report()
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
                    "version": "1.33",
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
            payload=_payload(),
            source_report=source_report,
            target_ref="external-cluster",
            worker_node_groups=("gpu-pool",),
            phase_ids=("external-node-template-upgrade",),
            command_runner=runner,
        )


def test_completed_migration_mk8s_check_fails_when_status_counts_are_missing() -> None:
    source_report = _source_report()
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
                    "version": "1.33",
                    "template": {
                        "resources": {
                            "platform": "gpu-h100-sxm",
                            "preset": "8gpu-128vcpu-1600gb",
                        },
                        "gpu_settings": {"drivers_preset": "cuda13.0"},
                        "os": {"name": "ubuntu24.04"},
                    },
                },
                "status": {"version": "1.33"},
            }
        ],
    )

    with pytest.raises(RuntimeError, match="status missing ready_node_count"):
        migration._verify_completed_soperator_migration_mk8s_state(
            payload=_payload(),
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
        "target_k8s_version": "1.33",
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
    ] == ["1.32", "1.33"]


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
    payload = _payload(include_role_mapping=True)
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
                "status": {"allocatable": {"cpu": "16"}},
            },
            {
                "metadata": {
                    "name": "system-a",
                    "labels": {
                        "nebius.com/node-group-id": "nodegroup-system",
                        "node.kubernetes.io/instance-type": "cpu-d3",
                    },
                },
                "status": {"allocatable": {"cpu": "8"}},
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
    payload = _payload(include_role_mapping=True)
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


def test_execute_pends_gpu_remediation_when_target_app_rows_are_missing(
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


def test_execute_runs_gpu_remediation_when_report_has_no_migration_plan(
    tmp_path: Path,
) -> None:
    payload = _payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    assert isinstance(target, dict)
    onboarding = target["soperator_onboarding"]
    assert isinstance(onboarding, dict)
    onboarding["actions"] = ["remediate-target-gpu-stack"]
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
        payload=_payload(include_role_mapping=True),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        command_runner=runner,
        status_callback=messages.append,
        status_poll_interval_seconds=0,
    )

    assert result.pending_phase == "none"
    assert any(
        "phase create-aligned-sfs" in message
        and "Storage" in message
        and "MK8s" in message
        and "Slurm" in message
        for message in messages
    )
    assert any(
        "phase rolling-compute-migration" in message
        and "MK8s" in message
        and "Slurm" in message
        and "Storage" not in message
        for message in messages
    )
    assert any(
        "phase final-control-plane-cutover" in message and "Soperator" in message
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
        "MK8s",
        "Slurm",
        "Soperator",
    }


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
    wait_calls = [call for call in runner.calls if "wait" in call[0]]
    assert len(wait_calls) == 3


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
        payload=_payload(include_role_mapping=True),
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
    payload = _payload(include_role_mapping=True)
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
    helm_upgrades = [call for call in runner.calls if call[0][0] == "helm" and "upgrade" in call[0]]
    assert [call[0][5] for call in helm_upgrades[:3]] == [
        "gpu-operator",
        "network-operator",
        "soperator",
    ]
    helm_upgrade = next(call for call in helm_upgrades if call[0][5] == "soperator")
    assert helm_upgrade[1] is not None
    assert "--force-conflicts" in helm_upgrade[0]
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
    assert mariadb_storage["volumeClaimTemplate"]["accessModes"] == ["ReadWriteMany"]
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
    assert any(
        call[0][:5] == ("kubectl", "--context", "external-context", "label", "nodes")
        and "nebius.com/node-group=cpu" in call[0]
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
            "kind": "NodeSet",
            "metadata": {"name": "worker", "namespace": "soperator"},
        }
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

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(include_role_mapping=True),
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
        payload=_payload(include_role_mapping=True),
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
            payload=_payload(include_role_mapping=True),
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
        payload=_payload(include_role_mapping=True),
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
        payload=_payload(include_role_mapping=True),
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
        payload=_payload(include_role_mapping=True),
        source_report=source_report,
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
        payload=_payload(include_role_mapping=True),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        command_runner=runner,
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
        payload=_payload(include_role_mapping=True),
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
            payload=_payload(include_role_mapping=True),
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
            payload=_payload(include_role_mapping=True),
            source_report=source_report,
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
        payload=_payload(include_role_mapping=True),
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
                "schema": "nebius-cxcli-soperator-migration-execution/v1",
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
