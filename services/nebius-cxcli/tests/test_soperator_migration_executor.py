from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebius_cxcli import soperator_migration as migration
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
                }
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


class _FakeCommandRunner:
    def __init__(
        self,
        *,
        existing_filesystems: dict[str, dict[str, object]] | None = None,
        helm_errors: list[str] | None = None,
        helm_history: list[dict[str, object]] | None = None,
        existing_node_groups: list[dict[str, object]] | None = None,
        live_pvcs: list[dict[str, object]] | None = None,
        live_slurmclusters: list[dict[str, object]] | None = None,
        slurm_resource_names: list[str] | None = None,
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.existing_filesystems = existing_filesystems or {}
        self.helm_errors = list(helm_errors or [])
        self.helm_history = list(helm_history or [])
        self.existing_node_groups = list(existing_node_groups or [])
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
        self.slurm_resource_names = list(
            slurm_resource_names
            or [
                "slurmcluster.slurm.nebius.ai/external-cluster",
                "nodeset.slurm.nebius.ai/worker",
            ]
        )

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
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"metadata": {"id": f"filesystem-{name}", "name": name}}),
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
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(
                    {
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
                    }
                ),
                "",
            )
        if command[:4] == ("nebius", "mk8s", "node-group", "list"):
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": self.existing_node_groups}),
                "",
            )
        if command[:4] == ("nebius", "mk8s", "node-group", "create"):
            payload = json.loads(command[4])
            name = payload["metadata"]["name"]
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"metadata": {"id": f"nodegroup-{name}", "name": name}}),
                "",
            )
        if command and command[0] == "helm" and "history" in command:
            return SoperatorMigrationCommandResult(command, 0, json.dumps(self.helm_history), "")
        if command and command[0] == "helm" and "upgrade" in command:
            if self.helm_errors:
                error = self.helm_errors.pop(0)
                if check:
                    raise RuntimeError(error)
                return SoperatorMigrationCommandResult(command, 1, "", error)
            return SoperatorMigrationCommandResult(command, 0, "{}", "")
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
                                    "name": "login-0",
                                    "labels": {"app.kubernetes.io/component": "login"},
                                },
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
            return SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps({"items": self.live_slurmclusters}),
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
            if command[8:10] == ("squeue", "-h"):
                return SoperatorMigrationCommandResult(command, 0, "", "")
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
                        "existing_filesystem": {
                            "id": f"filesystem-external-cluster-{key}"
                        },
                    }
                    for key in filesystems_by_role[role]
                ],
                "taints": taints,
            },
        },
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
    assert result.blocked_phase == "customer-approval"
    assert "customer approval is required before mutating phases" in result.blocked_reason
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["completed_phases"] == ["discovery-and-plan"]
    assert checkpoint["blocked_phase"] == "customer-approval"
    assert checkpoint["events"][0]["event"] == "execute-preflight-completed"


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
        worker_node_groups=("gpu-pool",),
        command_runner=runner,
    )

    assert result.mutation_performed is True
    assert result.completed_phases == (
        "discovery-and-plan",
        "customer-approval",
        "create-aligned-sfs",
        "online-bulk-data-sync",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
    )
    assert result.blocked_phase == "none"
    assert "Approved worker node groups: gpu-pool" in "\n".join(result.lines)
    assert "Data sync skipped: no old Soperator storage was detected" in "\n".join(result.lines)
    assert any(call[0][:4] == ("nebius", "compute", "filesystem", "create") for call in runner.calls)
    assert any(call[0][:4] == ("nebius", "mk8s", "node-group", "update") for call in runner.calls)
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["completed_phases"] == list(result.completed_phases)
    assert checkpoint["blocked_phase"] == "none"
    assert checkpoint["worker_node_groups"] == ["gpu-pool"]
    assert checkpoint["events"][1]["event"] == "customer-approval-recorded"
    assert set(checkpoint["phase_state"]["create-aligned-sfs"]["filesystems"]) == {
        "jail",
        "controller-spool",
        "accounting",
    }

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
    assert resumed.blocked_phase == "none"
    assert runner.calls == []
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["completed_phases"] == list(result.completed_phases)
    assert checkpoint["blocked_phase"] == "none"
    assert checkpoint["worker_node_groups"] == ["gpu-pool"]


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
    runner = _FakeCommandRunner()

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        worker_node_groups=("gpu-pool",),
        command_runner=runner,
    )

    assert result.blocked_phase == "retire-old-resources"
    assert "requires manual confirmation" in result.blocked_reason
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
        worker_node_groups=("gpu-pool",),
        command_runner=_FakeCommandRunner(),
    )

    assert result.blocked_phase == "online-bulk-data-sync"
    assert "Missing PVCs: target:jail:jail-pvc" in result.blocked_reason


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
        worker_node_groups=("gpu-pool",),
        command_runner=runner,
    )

    updated_node_groups = [
        call[0][4]
        for call in runner.calls
        if call[0][:4] == ("nebius", "mk8s", "node-group", "update")
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
        worker_node_groups=("gpu-pool",),
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
        worker_node_groups=("gpu-pool",),
        command_runner=runner,
    )

    assert result.blocked_phase == "create-aligned-sfs"
    assert "existing aligned SFS filesystem 'external-cluster-jail' is incompatible" in (
        result.blocked_reason
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
            "metadata": {"name": "accounting-pv"},
            "spec": {"capacity": {"storage": "128Gi"}},
        }
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
    )

    result = execute_soperator_migration(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=payload,
        source_report=source_report,
        snapshot_collector=lambda *, kube_context: snapshot,
        approved=True,
        worker_node_groups=("gpu-pool",),
        command_runner=runner,
    )

    assert result.blocked_phase == "none"
    assert result.mutation_performed is True
    assert "Target Soperator chart values applied to aligned compute groups." in "\n".join(
        result.lines
    )
    create_calls = [
        call for call in runner.calls if call[0][:4] == ("nebius", "mk8s", "node-group", "create")
    ]
    assert len(create_calls) == 5
    helm_upgrade = next(call for call in runner.calls if call[0][0] == "helm" and "upgrade" in call[0])
    assert helm_upgrade[1] is not None
    assert "--force-conflicts" in helm_upgrade[0]
    assert "--wait" not in helm_upgrade[0]
    helm_values = json.loads(helm_upgrade[1])
    assert helm_values["nameOverride"] == "helm-soperator"
    assert helm_values["customSlurmConfig"] == "PluginDir=/usr/lib/x86_64-linux-gnu/slurm"
    assert helm_values["plugStackConfig"]["pyxis"] == {
        "importerPath": "",
        "required": False,
    }
    assert helm_values["volume"]["controllerSpool"]["size"] == "128Gi"
    assert helm_values["volume"]["jail"]["size"] == "2Ti"
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
    assert any(call[0][:3] == ("kubectl", "--context", "external-context") and "drain" in call[0] for call in runner.calls)
    assert any(call[0][:4] == ("nebius", "mk8s", "node-group", "delete") for call in runner.calls)
    checkpoint_path = soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["blocked_phase"] == "none"
    assert set(checkpoint["phase_state"]["rolling-compute-migration"]["target_node_groups"]) == {
        "system",
        "controller",
        "login",
        "accounting",
        "worker",
    }
    retired = checkpoint["phase_state"]["retire-old-resources"]["retired_node_groups"]
    assert {item["source_group"] for item in retired} == {"cpu-pool", "gpu-pool"}


def test_execute_blocks_incompatible_existing_target_node_group(tmp_path: Path) -> None:
    snapshot = _snapshot_with_compute_source()
    source_report = _source_report(snapshot)
    runner = _FakeCommandRunner(
        existing_node_groups=[
            _target_node_group(role, missing_role_label=(role == "worker"))
            for role in ("system", "controller", "login", "accounting", "worker")
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
        worker_node_groups=("gpu-pool",),
        command_runner=runner,
    )

    assert result.blocked_phase == "rolling-compute-migration"
    assert "existing target node group 'external-cluster-worker' is incompatible" in (
        result.blocked_reason
    )
    assert "slurm.nebius.ai/nodeset-name=worker" in result.blocked_reason


def test_final_cutover_blocks_when_target_slurmcluster_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_with_compute_source()
    checkpoint = {
        "phase_state": {
            "rolling-compute-migration": {
                "target_node_groups": {"worker": {"id": "target-worker"}}
            }
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
        migration.SoperatorMigrationPhaseBlocked,
        match="target SlurmCluster 'external-cluster' did not become Available",
    ):
        migration._execute_final_cutover_phase(
            checkpoint=checkpoint,
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
        worker_node_groups=("gpu-pool",),
        command_runner=runner,
    )

    assert result.blocked_phase == "none"
    helm_upgrades = [
        call for call in runner.calls if call[0][0] == "helm" and "upgrade" in call[0]
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
        worker_node_groups=("gpu-pool",),
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

    assert result.blocked_phase == "none"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    rolling = checkpoint["phase_state"]["rolling-compute-migration"]
    assert rolling["target_values_revision"] == 5
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
            'Error: UPGRADE FAILED: unable to continue with update: PriorityClass '
            '"soperator-worker-high-priority" in namespace "" exists and cannot be imported '
            'into the current release: invalid ownership metadata; annotation validation '
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
        worker_node_groups=("gpu-pool",),
        command_runner=runner,
    )

    assert result.blocked_phase == "none"
    helm_upgrade_calls = [
        call for call in runner.calls if call[0][:4] == ("helm", "--kube-context", "external-context", "upgrade")
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
            worker_node_groups=("gpu-pool",),
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
        "worker",
    }
    assert checkpoint["completed_phases"] == [
        "discovery-and-plan",
        "customer-approval",
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
            worker_node_groups=("gpu-pool",),
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
        worker_node_groups=("gpu-pool",),
        command_runner=_FakeCommandRunner(),
    )

    assert result.blocked_phase == "none"


def test_execute_requires_worker_groups_for_approved_compute_migration(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="requires --worker-node-groups"):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
        )

    assert not soperator_migration_checkpoint_path(
        tmp_path / "config.yaml",
        "external-cluster",
    ).exists()


def test_execute_rejects_unknown_worker_groups(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="were not found"):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot(),
            approved=True,
            worker_node_groups=("missing-gpu",),
        )


def test_execute_rejects_changed_live_source_version(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Live Soperator source version changed"):
        execute_soperator_migration(
            config_path=tmp_path / "config.yaml",
            target_ref="external-cluster",
            payload=_payload(),
            source_report=_source_report(),
            snapshot_collector=lambda *, kube_context: _snapshot("4.0.1"),
        )


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
        }
    ]
    live_snapshot = json.loads(json.dumps(source_snapshot))
    live_snapshot["soperator_resources"][0]["metadata"]["resourceVersion"] = "101"
    live_snapshot["soperator_resources"][0]["metadata"]["uid"] = "live-uid"
    live_snapshot["soperator_resources"][0]["status"] = {"phase": "Reconciling"}
    del live_snapshot["soperator_resources"][0]["spec"]["clusterType"]
    live_snapshot["soperator_resources"][0]["spec"]["plugStackConfig"]["pyxis"][
        "importerPath"
    ] = "/opt/slurm_scripts/pyxis_caching_importer.sh"
    live_snapshot["soperator_resources"][0]["spec"]["slurmNodes"]["controller"][
        "openMetrics"
    ] = {"enabled": True}
    live_snapshot["soperator_resources"][0]["spec"]["slurmNodes"]["controller"][
        "sssdDebugLevel"
    ] = 0
    live_snapshot["soperator_resources"][0]["spec"]["slurmNodes"]["login"][
        "sssdDebugLevel"
    ] = 0
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

    assert result.blocked_phase == "customer-approval"
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

    assert result.blocked_phase == "customer-approval"
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
