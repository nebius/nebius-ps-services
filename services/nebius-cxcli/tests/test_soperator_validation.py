from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from nebius_cxcli import soperator_validation
from nebius_cxcli.soperator_validation import (
    SOPERATOR_CLUSTER_VALIDATION_KIND,
    SoperatorValidationCommandResult,
    _nccl_script,
    run_soperator_cluster_validations,
    soperator_cluster_validation_specs,
)


def test_soperator_cluster_validation_specs_for_enabled_soperator_target() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "training",
                    "enabled": True,
                    "values": {"clusterName": "slurm-training"},
                }
            ]
        },
        "deploy": {
            "targets": [
                {
                    "instance_id": "training",
                    "kube_context": "training-context",
                }
            ]
        },
    }

    specs = soperator_cluster_validation_specs(payload)

    assert specs == [
        {
            "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
            "name": "Soperator cluster smoke test (training)",
            "target_ref": "training",
            "namespace": "soperator",
            "cluster_name": "slurm-training",
            "target_version": "",
            "jail_storage": {
                "pvc_name": "jail-pvc",
                "pv_name": "jail-pv",
                "mount_daemonset_name": "jail-mount",
                "local_path": "/mnt/jail",
            },
            "kube_context": "training-context",
            "report_file": "soperator-cluster-validation-report-training.json",
            "readiness_timeout_seconds": 1200,
            "readiness_poll_seconds": 15.0,
            "required": True,
        }
    ]


def test_soperator_cluster_validation_specs_use_custom_jail_storage_names() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "training",
                    "enabled": True,
                    "values": {
                        "clusterName": "slurm-training",
                        "volume": {"jail": {"name": "TrainingJail!", "localPath": "/mnt/train"}},
                    },
                }
            ]
        },
        "deploy": {"targets": [{"instance_id": "training"}]},
    }

    specs = soperator_cluster_validation_specs(payload)

    assert specs[0]["jail_storage"] == {
        "pvc_name": "training-jail-pvc",
        "pv_name": "training-jail-pv",
        "mount_daemonset_name": "training-jail-mount",
        "local_path": "/mnt/train",
    }


def test_run_soperator_cluster_validation_writes_smoke_report(tmp_path: Path) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }
    commands: list[tuple[str, ...]] = []

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        commands.append(command)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "training"},
                                "status": {"phase": "Available"},
                            }
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o"):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    assert written == [tmp_path / "soperator-cluster-validation-report-training.json"]
    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["schema"] == "nebius-cxcli-soperator-cluster-validation/v2"
    assert report["passed"] is True
    assert report["summary"] == "5/5 Soperator/Slurm checks passed."
    assert [check["name"] for check in report["checks"]] == [
        "Soperator manager rollout",
        "SlurmCluster availability",
        "Slurm node status",
        "Slurm queue access",
        "Slurm srun smoke job",
    ]
    assert any("cxcli-soperator-smoke" in " ".join(command) for command in commands)


def test_run_soperator_cluster_validation_waits_for_slurmcluster_availability(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": "soperator-cluster-validation-report-training.json",
        "readiness_timeout_seconds": 2,
        "readiness_poll_seconds": 0,
    }
    phases = ["Pending", "Available", "Available"]
    slurmcluster_gets = 0

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        nonlocal slurmcluster_gets
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:5] == ("kubectl", "-n", "soperator", "get", "pods"):
            return SoperatorValidationCommandResult(
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
        if command[:6] == ("kubectl", "-n", "soperator", "get", "slurmclusters", "-o"):
            slurmcluster_gets += 1
            phase = phases.pop(0) if phases else "Available"
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "training"},
                                "status": {"phase": phase},
                            }
                        ]
                    }
                ),
                "",
            )
        if command[:6] == ("kubectl", "-n", "soperator", "exec", "login-0", "--"):
            if command[6:9] == ("sinfo", "-h", "-o"):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[6:8] == ("bash", "-lc"):
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert slurmcluster_gets >= 3


def test_soperator_cluster_validation_fails_on_active_old_source_flux(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "target_version": "4.0.1-ps.1",
        "report_file": "soperator-cluster-validation-report-training.json",
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:4] == ("kubectl", "get", "helmreleases.helm.toolkit.fluxcd.io", "-A"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {
                                    "name": "flux-system-soperator-fluxcd-slurm-cluster",
                                    "namespace": "flux-system",
                                },
                                "spec": {
                                    "chart": {
                                        "spec": {
                                            "chart": "helm-slurm-cluster",
                                            "version": "2.0.5",
                                        }
                                    },
                                },
                            },
                            {
                                "metadata": {
                                    "name": "soperator-fluxcd-values",
                                    "namespace": "flux-system",
                                },
                                "spec": {
                                    "chart": {
                                        "spec": {
                                            "chart": "raw",
                                            "version": "2.0.0",
                                        }
                                    },
                                },
                            },
                        ]
                    }
                ),
                "",
            )
        if command[:5] == ("kubectl", "-n", "soperator", "get", "pods"):
            return SoperatorValidationCommandResult(
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
        if command[:6] == ("kubectl", "-n", "soperator", "get", "slurmclusters", "-o"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "training"},
                                "status": {"phase": "Available"},
                            }
                        ]
                    }
                ),
                "",
            )
        if command[:6] == ("kubectl", "-n", "soperator", "exec", "login-0", "--"):
            if command[6:9] == ("sinfo", "-h", "-o"):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[6:8] == ("bash", "-lc"):
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    with pytest.raises(RuntimeError, match="Old source Flux desired state"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "soperator-cluster-validation-report-training.json").read_text(encoding="utf-8")
    )
    failed = [check for check in report["checks"] if check["status"] == "failed"]
    assert failed[0]["name"] == "Old source Flux desired state"
    assert "flux-system/flux-system-soperator-fluxcd-slurm-cluster" in failed[0]["summary"]
    assert "flux-system/soperator-fluxcd-values" in failed[0]["summary"]


def test_soperator_cluster_validation_reports_pending_soperator_pods(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": "soperator-cluster-validation-report-training.json",
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:5] == ("kubectl", "-n", "soperator", "get", "pods"):
            return SoperatorValidationCommandResult(
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
                            },
                            {
                                "metadata": {"name": "mk8s-acct-db-0"},
                                "status": {
                                    "phase": "Pending",
                                    "conditions": [
                                        {
                                            "type": "PodScheduled",
                                            "message": "0/1 nodes are available: node is unschedulable.",
                                        }
                                    ],
                                },
                            },
                        ]
                    }
                ),
                "",
            )
        if command[:6] == ("kubectl", "-n", "soperator", "get", "slurmclusters", "-o"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "training"},
                                "status": {"phase": "Available"},
                            }
                        ]
                    }
                ),
                "",
            )
        if command[:6] == ("kubectl", "-n", "soperator", "exec", "login-0", "--"):
            if command[6:9] == ("sinfo", "-h", "-o"):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[6:8] == ("bash", "-lc"):
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    with pytest.raises(RuntimeError, match="Soperator pod scheduling"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "soperator-cluster-validation-report-training.json").read_text(encoding="utf-8")
    )
    failed = [check for check in report["checks"] if check["status"] == "failed"]
    assert failed[0]["name"] == "Soperator pod scheduling"
    assert "mk8s-acct-db-0" in failed[0]["summary"]
    assert "unschedulable" in failed[0]["summary"]


def test_soperator_cluster_validation_waits_for_jail_mount_pending_pods(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": "soperator-cluster-validation-report-training.json",
        "readiness_timeout_seconds": 2,
        "readiness_poll_seconds": 0,
    }
    pod_gets = 0
    daemonset_gets = 0

    def _pod_payload(*, pending: bool) -> dict:
        items = [
            {
                "metadata": {
                    "name": "login-0",
                    "labels": {"app.kubernetes.io/component": "login"},
                },
                "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
            }
        ]
        if pending:
            items.append(
                {
                    "metadata": {"name": "worker-cpu-1"},
                    "spec": {
                        "volumes": [
                            {
                                "name": "jail",
                                "persistentVolumeClaim": {"claimName": "jail-pvc"},
                            }
                        ]
                    },
                    "status": {
                        "phase": "Pending",
                        "conditions": [{"type": "PodScheduled", "status": "True"}],
                    },
                }
            )
        else:
            items.append(
                {
                    "metadata": {"name": "worker-cpu-1"},
                    "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
                }
            )
        return {"items": items}

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        nonlocal pod_gets, daemonset_gets
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:5] == ("kubectl", "-n", "soperator", "get", "pods"):
            pod_gets += 1
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(_pod_payload(pending=pod_gets == 1)),
                "",
            )
        if command[:5] == ("kubectl", "-n", "soperator", "get", "events"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "type": "Warning",
                                "reason": "FailedMount",
                                "message": (
                                    'MountVolume.NewMounter initialization failed for volume '
                                    '"jail-pv" : path "/mnt/jail" does not exist'
                                ),
                            }
                        ]
                    }
                ),
                "",
            )
        if command[:7] == ("kubectl", "-n", "soperator", "get", "pvc", "jail-pvc", "-o"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps({"status": {"phase": "Bound"}}),
                "",
            )
        if command[:5] == ("kubectl", "get", "pv", "jail-pv", "-o"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps({"status": {"phase": "Bound"}}),
                "",
            )
        if command[:7] == ("kubectl", "-n", "soperator", "get", "daemonset", "jail-mount", "-o"):
            daemonset_gets += 1
            ready = daemonset_gets > 1
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "status": {
                            "desiredNumberScheduled": 3,
                            "numberReady": 3 if ready else 2,
                            "numberAvailable": 3 if ready else 2,
                        }
                    }
                ),
                "",
            )
        if command[:6] == ("kubectl", "-n", "soperator", "get", "slurmclusters", "-o"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "training"},
                                "status": {"phase": "Available"},
                            }
                        ]
                    }
                ),
                "",
            )
        if command[:6] == ("kubectl", "-n", "soperator", "exec", "login-0", "--"):
            if command[6:9] == ("sinfo", "-h", "-o"):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[6:8] == ("bash", "-lc"):
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert pod_gets >= 2
    assert daemonset_gets >= 2
    assert all(check["name"] != "Soperator pod scheduling" for check in report["checks"])


def test_soperator_cluster_validation_reports_failed_mount_event_for_pending_pod(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": "soperator-cluster-validation-report-training.json",
        "readiness_timeout_seconds": 0,
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:5] == ("kubectl", "-n", "soperator", "get", "pods"):
            return SoperatorValidationCommandResult(
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
                            },
                            {
                                "metadata": {"name": "worker-cpu-1"},
                                "spec": {
                                    "volumes": [
                                        {
                                            "name": "jail",
                                            "persistentVolumeClaim": {"claimName": "jail-pvc"},
                                        }
                                    ]
                                },
                                "status": {
                                    "phase": "Pending",
                                    "conditions": [{"type": "PodScheduled", "status": "True"}],
                                },
                            },
                        ]
                    }
                ),
                "",
            )
        if command[:5] == ("kubectl", "-n", "soperator", "get", "events"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "type": "Warning",
                                "reason": "FailedMount",
                                "message": (
                                    'MountVolume.NewMounter initialization failed for volume '
                                    '"jail-pv" : path "/mnt/jail" does not exist'
                                ),
                            }
                        ]
                    }
                ),
                "",
            )
        if command[:7] == ("kubectl", "-n", "soperator", "get", "pvc", "jail-pvc", "-o"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps({"status": {"phase": "Bound"}}),
                "",
            )
        if command[:5] == ("kubectl", "get", "pv", "jail-pv", "-o"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps({"status": {"phase": "Bound"}}),
                "",
            )
        if command[:7] == ("kubectl", "-n", "soperator", "get", "daemonset", "jail-mount", "-o"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "status": {
                            "desiredNumberScheduled": 3,
                            "numberReady": 3,
                            "numberAvailable": 3,
                        }
                    }
                ),
                "",
            )
        if command[:6] == ("kubectl", "-n", "soperator", "get", "slurmclusters", "-o"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "training"},
                                "status": {"phase": "Available"},
                            }
                        ]
                    }
                ),
                "",
            )
        if command[:6] == ("kubectl", "-n", "soperator", "exec", "login-0", "--"):
            if command[6:9] == ("sinfo", "-h", "-o"):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[6:8] == ("bash", "-lc"):
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    with pytest.raises(RuntimeError, match="Soperator pod scheduling"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "soperator-cluster-validation-report-training.json").read_text(encoding="utf-8")
    )
    failed = [check for check in report["checks"] if check["status"] == "failed"]
    assert failed[0]["name"] == "Soperator pod scheduling"
    assert "worker-cpu-1" in failed[0]["summary"]
    assert "FailedMount" in failed[0]["summary"]
    assert "jail-pv" in failed[0]["summary"]


def test_soperator_cluster_validation_fails_when_jail_storage_resources_are_missing(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": "soperator-cluster-validation-report-training.json",
        "readiness_timeout_seconds": 0,
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:5] == ("kubectl", "-n", "soperator", "get", "pods"):
            return SoperatorValidationCommandResult(
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
        if command[:7] == ("kubectl", "-n", "soperator", "get", "pvc", "jail-pvc", "-o"):
            return SoperatorValidationCommandResult(
                command,
                1,
                "",
                'Error from server (NotFound): persistentvolumeclaims "jail-pvc" not found',
            )
        if command[:5] == ("kubectl", "get", "pv", "jail-pv", "-o"):
            return SoperatorValidationCommandResult(
                command,
                1,
                "",
                'Error from server (NotFound): persistentvolumes "jail-pv" not found',
            )
        if command[:7] == ("kubectl", "-n", "soperator", "get", "daemonset", "jail-mount", "-o"):
            return SoperatorValidationCommandResult(
                command,
                1,
                "",
                'Error from server (NotFound): daemonsets.apps "jail-mount" not found',
            )
        if command[:6] == ("kubectl", "-n", "soperator", "get", "slurmclusters", "-o"):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "training"},
                                "status": {"phase": "Available"},
                            }
                        ]
                    }
                ),
                "",
            )
        if command[:6] == ("kubectl", "-n", "soperator", "exec", "login-0", "--"):
            if command[6:9] == ("sinfo", "-h", "-o"):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[6:8] == ("bash", "-lc"):
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    with pytest.raises(RuntimeError, match="Soperator storage mounts"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "soperator-cluster-validation-report-training.json").read_text(encoding="utf-8")
    )
    failed = [check for check in report["checks"] if check["status"] == "failed"]
    assert failed[0]["name"] == "Soperator storage mounts"
    assert "pvc/jail-pvc lookup failed" in failed[0]["summary"]
    assert "daemonset/jail-mount lookup failed" in failed[0]["summary"]


def test_soperator_cluster_validation_runs_slurm_gpu_and_nccl_benchmark(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }
    smoke_scripts: list[str] = []

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cpu*|idle|(null)\ngpu|idle|gpu:8\n",
                    "",
                )
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D":
                return SoperatorValidationCommandResult(command, 0, "cpu*|1\ngpu|2\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu|2|gpu:8\n", "")
            if command[8:12] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                smoke_scripts.append(script)
                if "cxcli-soperator-partition-hostnames" in script:
                    partition = "gpu" if "--partition=gpu" in script else "cpu"
                    nodes = "2" if partition == "gpu" else "1"
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        f"worker-{partition}-0\ncxcli-soperator-partition-hostnames-ok "
                        f"partition={partition} nodes={nodes}\n",
                        "",
                    )
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "\n".join(
                            [
                                "GPU 0: NVIDIA H100",
                                "cxcli-soperator-gpu-allocation-ok host=worker-gpu-0",
                                "GPU 0: NVIDIA H100",
                                "cxcli-soperator-gpu-allocation-ok host=worker-gpu-1",
                            ]
                        )
                        + "\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "\n".join(
                            [
                                "cxcli-soperator-nccl-ok mode=multi-node partition=gpu nodes=2 "
                                "gpus_per_node=8 ranks=16",
                                "# nThread 1 nGpus 1 minBytes 536870912 maxBytes 8589934592",
                                "2147483648 536870912 float sum -1 12345 173.9 320.1 0 "
                                "12346 173.8 319.9 0",
                                "4294967296 1073741824 float sum -1 12345 347.8 640.3 0 "
                                "12346 347.7 640.1 0",
                                "8589934592 2147483648 float sum -1 12345 695.7 780.2 0 "
                                "12346 695.6 780.0 0",
                            ]
                        )
                        + "\n",
                        "",
                    )
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-cpu-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["summary"] == "8/8 Soperator/Slurm checks passed."
    assert [check["name"] for check in report["checks"]][-4:] == [
        "Slurm srun smoke job",
        "Slurm all-partition hostname check",
        "Slurm GPU allocation check",
        "Slurm NCCL benchmark",
    ]
    partition_check = next(
        check for check in report["checks"] if check["name"] == "Slurm all-partition hostname check"
    )
    assert partition_check["summary"] == (
        "hostname jobs completed across all reported Slurm partition nodes: cpu=1, gpu=2."
    )
    assert partition_check["stdout"] == [
        "worker-cpu-0",
        "cxcli-soperator-partition-hostnames-ok partition=cpu nodes=1",
        "worker-gpu-0",
        "cxcli-soperator-partition-hostnames-ok partition=gpu nodes=2",
    ]
    assert partition_check["partition_hostnames"] == [
        {
            "partition": "cpu",
            "expected_node_count": 1,
            "reported_hostname_count": 1,
            "status": "passed",
            "hostnames": ["worker-cpu-0"],
        },
        {
            "partition": "gpu",
            "expected_node_count": 2,
            "reported_hostname_count": 1,
            "status": "passed",
            "hostnames": ["worker-gpu-0"],
        },
    ]
    gpu_check = report["checks"][-2]
    assert gpu_check["summary"] == (
        "one-GPU Slurm allocations reported NVIDIA GPUs across all reported GPU "
        "partition nodes: gpu=2."
    )
    assert gpu_check["stdout"] == [
        "GPU 0: NVIDIA H100",
        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-0",
        "GPU 0: NVIDIA H100",
        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-1",
    ]
    assert gpu_check["gpu_allocations"] == [
        {
            "partition": "gpu",
            "expected_node_count": 2,
            "reported_host_count": 2,
            "status": "passed",
            "hosts": ["worker-gpu-0", "worker-gpu-1"],
        }
    ]
    nccl_check = report["checks"][-1]
    assert nccl_check["summary"] == (
        "two-node NCCL all_reduce_perf benchmark completed on partition gpu "
        "with 16 rank(s) across 2 node(s) (8 GPU(s) per node); "
        "average bus bandwidth 580.2 Gbps across 2G, 4G, 8G message sizes."
    )
    assert nccl_check["avg_large_message_bus_bandwidth_gbps"] == pytest.approx(580.2)
    assert nccl_check["large_message_bus_bandwidth_gbps"] == {
        "2G": 320.1,
        "4G": 640.3,
        "8G": 780.2,
    }
    assert nccl_check["multi_node_benchmark"] is True
    assert nccl_check["single_node_multi_gpu_benchmark"] is False
    assert nccl_check["benchmark_mode"] == "multi-node"
    assert nccl_check["mode"] == "multi-node"
    assert nccl_check["nodes"] == 2
    assert nccl_check["gpus_per_node"] == 8
    assert nccl_check["ranks"] == 16
    assert any("--partition=gpu" in script for script in smoke_scripts)
    assert any("mpirun --allow-run-as-root" in script for script in smoke_scripts)
    assert any("salloc --job-name=cxcli-soperator-nccl" in script for script in smoke_scripts)
    assert any(
        '--nodes="$target_nodes" --ntasks="$target_nodes" --ntasks-per-node=1' in script
        for script in smoke_scripts
    )
    assert any(
        "srun --job-name=cxcli-soperator-nccl-launcher --nodes=1 --ntasks=1" in script
        for script in smoke_scripts
    )
    assert all("SLURM_PROCID" not in script for script in smoke_scripts)


def test_soperator_cluster_validation_prefers_8_gpu_partition_for_slurm_nccl(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }
    nccl_scripts: list[str] = []

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu-dev*|idle|gpu:1\ngpu-prod|idle|gpu:8\n",
                    "",
                )
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu-dev*|2\ngpu-prod|2\n",
                    "",
                )
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu-dev*|2|gpu:1\ngpu-prod|2|gpu:8\n",
                    "",
                )
            if command[8:12] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-partition-hostnames" in script:
                    partition = "gpu-prod" if "--partition=gpu-prod" in script else "gpu-dev"
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        f"worker-{partition}-0\nworker-{partition}-1\n"
                        "cxcli-soperator-partition-hostnames-ok "
                        f"partition={partition} nodes=2\n",
                        "",
                    )
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA H100\n"
                        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n"
                        "GPU 0: NVIDIA H100\n"
                        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-1\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    nccl_scripts.append(script)
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "\n".join(
                            [
                                "cxcli-soperator-nccl-ok mode=multi-node partition=gpu-prod "
                                "nodes=2 gpus_per_node=8 ranks=16",
                                "# nThread 1 nGpus 1 minBytes 536870912 maxBytes 8589934592",
                                "2147483648 536870912 float sum -1 12345 173.9 320.1 0 "
                                "12346 173.8 319.9 0",
                                "4294967296 1073741824 float sum -1 12345 347.8 640.3 0 "
                                "12346 347.7 640.1 0",
                                "8589934592 2147483648 float sum -1 12345 695.7 780.2 0 "
                                "12346 695.6 780.0 0",
                            ]
                        )
                        + "\n",
                        "",
                    )
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-gpu-dev-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    nccl_check = report["checks"][-1]
    assert report["passed"] is True
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "passed"
    assert "partition gpu-prod" in nccl_check["summary"]
    assert nccl_scripts
    assert all("partition=gpu-prod" in script for script in nccl_scripts)
    assert all("partition=gpu-dev" not in script for script in nccl_scripts)


def test_soperator_cluster_validation_runs_slurm_nccl_benchmark_on_one_8_gpu_node(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|idle|gpu:8\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D":
                return SoperatorValidationCommandResult(command, 0, "gpu*|1\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|1|gpu:8\n", "")
            if command[8:12] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-partition-hostnames" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "worker-gpu-0\ncxcli-soperator-partition-hostnames-ok "
                        "partition=gpu nodes=1\n",
                        "",
                    )
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA H100\n"
                        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "\n".join(
                            [
                                "cxcli-soperator-nccl-ok mode=single-node-multi-gpu "
                                "partition=gpu nodes=1 gpus_per_node=8 ranks=8",
                                "# nThread 1 nGpus 1 minBytes 536870912 maxBytes 8589934592",
                                "2147483648 536870912 float sum -1 12345 280.0 500.1 0 "
                                "12346 279.9 500.0 0",
                                "4294967296 1073741824 float sum -1 12345 310.0 590.4 0 "
                                "12346 309.8 590.0 0",
                                "8589934592 2147483648 float sum -1 12345 330.0 610.1 0 "
                                "12346 329.8 610.0 0",
                            ]
                        )
                        + "\n",
                        "",
                    )
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-gpu-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["summary"] == "8/8 Soperator/Slurm checks passed."
    nccl_check = report["checks"][-1]
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "passed"
    assert nccl_check["summary"] == (
        "single-node multi-GPU NCCL all_reduce_perf benchmark completed on partition gpu "
        "with 8 rank(s) on 1 node (8 GPU(s) on the node); average bus bandwidth "
        "566.9 Gbps across 2G, 4G, 8G message sizes."
    )
    assert nccl_check["avg_large_message_bus_bandwidth_gbps"] == pytest.approx(566.866, abs=0.01)
    assert nccl_check["large_message_bus_bandwidth_gbps"] == {
        "2G": 500.1,
        "4G": 590.4,
        "8G": 610.1,
    }
    assert nccl_check["multi_node_benchmark"] is False
    assert nccl_check["single_node_multi_gpu_benchmark"] is True
    assert nccl_check["benchmark_mode"] == "single-node-multi-gpu"
    assert nccl_check["mode"] == "single-node-multi-gpu"
    assert nccl_check["nodes"] == 1
    assert nccl_check["gpus_per_node"] == 8
    assert nccl_check["ranks"] == 8


def test_nccl_script_uses_single_idle_8_gpu_node_when_partition_has_one_gpu_nodes(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sinfo = fake_bin / "sinfo"
    sinfo.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "printf '%s\\n' 'gpu-small|idle|gpu:1|16' 'gpu-big|idle|gpu:8|64'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    salloc = fake_bin / "salloc"
    salloc.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "if [[ \" $* \" == *\" cxcli-soperator-nccl-probe \"* ]]; then",
                "  exit 0",
                "fi",
                "printf '%s\\n' \\",
                "  'cxcli-soperator-nccl-ok mode=single-node-multi-gpu partition=gpu nodes=1 gpus_per_node=8 ranks=8 reported_gpus_per_node=8' \\",
                "  '# nThread 1 nGpus 1 minBytes 536870912 maxBytes 8589934592' \\",
                "  '2147483648 536870912 float sum -1 12345 280.0 500.1 0 12346 279.9 500.0 0' \\",
                "  '4294967296 1073741824 float sum -1 12345 310.0 590.4 0 12346 309.8 590.0 0' \\",
                "  '8589934592 2147483648 float sum -1 12345 330.0 610.1 0 12346 329.8 610.0 0'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    sinfo.chmod(0o755)
    salloc.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        ("bash", "-lc", _nccl_script(partition="gpu")),
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "cxcli-soperator-nccl-ok mode=single-node-multi-gpu partition=gpu "
        "nodes=1 gpus_per_node=8 ranks=8"
    ) in result.stdout
    assert "runs only on 8-GPU Slurm nodes" not in result.stdout


def test_soperator_cluster_validation_skips_slurm_nccl_when_allocatable_gpu_floor_is_low(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|idle|gpu:8\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D":
                return SoperatorValidationCommandResult(command, 0, "gpu*|2\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|2|gpu:8\n", "")
            if command[8:12] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-partition-hostnames" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "worker-gpu-0\nworker-gpu-1\n"
                        "cxcli-soperator-partition-hostnames-ok partition=gpu nodes=2\n",
                        "",
                    )
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA H100\n"
                        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n"
                        "GPU 0: NVIDIA H100\n"
                        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-1\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        (
                            "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark "
                            "requires at least 8 allocatable GPUs per selected Slurm node "
                            "on partition gpu; reported 8 GPU(s), allocatable 4.\n"
                        ),
                        "",
                    )
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-gpu-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    nccl_check = report["checks"][-1]
    assert report["passed"] is True
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "skipped"
    assert nccl_check["summary"] == (
        "Skipped: full Slurm NCCL benchmark requires at least 8 allocatable GPUs "
        "per selected Slurm node on partition gpu; reported 8 GPU(s), allocatable 4."
    )


def test_soperator_cluster_validation_skips_slurm_nccl_benchmark_on_one_total_gpu(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|idle|gpu:1\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D":
                return SoperatorValidationCommandResult(command, 0, "gpu*|1\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|1|gpu:1\n", "")
            if command[8:12] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-partition-hostnames" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "worker-gpu-0\ncxcli-soperator-partition-hostnames-ok "
                        "partition=gpu nodes=1\n",
                        "",
                    )
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA L40S\n"
                        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        (
                            "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark "
                            "runs only on 8-GPU Slurm nodes; selected partition gpu reported "
                            "1 GPU(s) per node.\n"
                        ),
                        "",
                    )
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-gpu-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["summary"] == "7/8 Soperator/Slurm checks passed. 1 skipped."
    nccl_check = report["checks"][-1]
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "skipped"
    assert nccl_check["skipped"] is True
    assert nccl_check["summary"] == (
        "Skipped: full Slurm NCCL benchmark runs only on 8-GPU Slurm nodes; selected "
        "partition gpu reported 1 GPU(s) per node."
    )


def test_soperator_cluster_validation_skips_slurm_nccl_benchmark_on_two_one_gpu_nodes(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|idle|gpu:1\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D":
                return SoperatorValidationCommandResult(command, 0, "gpu*|2\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|2|gpu:1\n", "")
            if command[8:12] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-partition-hostnames" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "worker-gpu-0\nworker-gpu-1\n"
                        "cxcli-soperator-partition-hostnames-ok partition=gpu nodes=2\n",
                        "",
                    )
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA L40S\n"
                        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n"
                        "GPU 0: NVIDIA L40S\n"
                        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-1\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        (
                            "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark "
                            "runs only on 8-GPU Slurm nodes; selected partition gpu reported "
                            "1 GPU(s) per node.\n"
                        ),
                        "",
                    )
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-gpu-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    nccl_check = report["checks"][-1]
    assert report["passed"] is True
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "skipped"
    assert nccl_check["summary"] == (
        "Skipped: full Slurm NCCL benchmark runs only on 8-GPU Slurm nodes; selected "
        "partition gpu reported 1 GPU(s) per node."
    )


def test_soperator_cluster_validation_skips_busy_slurm_gpu_allocation(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "idle\nmix\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|mix|gpu:8\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D":
                return SoperatorValidationCommandResult(command, 0, "gpu*|2\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|2|gpu:8\n", "")
            if command[8:12] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "mix\nidle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-partition-hostnames" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "worker-gpu-0\nworker-gpu-1\n"
                        "cxcli-soperator-partition-hostnames-ok partition=gpu nodes=2\n",
                        "",
                    )
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        1,
                        "",
                        "srun: error: Unable to allocate resources: Resources are not available",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        (
                            "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark "
                            "requires 2 idle GPU nodes on partition gpu; found 1 idle "
                            "GPU node(s), 2 total GPU node(s).\n"
                        ),
                        "",
                    )
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-gpu-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    gpu_check = next(check for check in report["checks"] if check["name"] == "Slurm GPU allocation check")
    assert report["passed"] is True
    assert gpu_check["status"] == "skipped"
    assert gpu_check["summary"] == (
        "Skipped: one-GPU Slurm allocation was not immediately schedulable on "
        "partition(s): gpu."
    )


def test_soperator_cluster_validation_prefers_idle_cpu_partition_for_smoke(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }
    smoke_commands: list[tuple[str, ...]] = []

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu*|inval|gpu:8\ncpu|idle|(null)\nhidden|idle|(null)\n",
                    "",
                )
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                smoke_commands.append(command)
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-cpu-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert smoke_commands
    assert "--partition=cpu" in smoke_commands[0][10]


def test_soperator_cluster_validation_allows_cloud_node_resume_for_smoke(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }
    smoke_scripts: list[str] = []
    smoke_timeouts: list[int] = []

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "idle~\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(command, 0, "cpu|idle~|(null)\n", "")
            if command[8:12] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle~\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                smoke_scripts.append(command[10])
                smoke_timeouts.append(timeout_seconds)
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-cpu-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert smoke_scripts
    assert "--partition=cpu" in smoke_scripts[0]
    assert "--immediate=600" in smoke_scripts[0]
    assert smoke_timeouts == [900]
    assert "partition cpu" in report["checks"][-1]["summary"]


def test_soperator_cluster_validation_marks_inval_nodes_unhealthy(tmp_path: Path) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "inval\nidle\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(command, 0, "cpu|idle|(null)\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-cpu-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    with pytest.raises(RuntimeError, match="Slurm node status"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "soperator-cluster-validation-report-training.json").read_text(encoding="utf-8")
    )
    assert report["passed"] is False
    node_status = next(check for check in report["checks"] if check["name"] == "Slurm node status")
    assert node_status["status"] == "failed"


def test_soperator_cluster_validation_retries_all_partition_hostname_scale_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        soperator_validation,
        "_SLURM_PARTITION_HOSTNAME_RETRY_DELAY_SECONDS",
        0,
    )
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "soperator-cluster-validation-report-training.json",
    }
    hostname_attempts = 0
    hostname_scripts: list[str] = []

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        nonlocal hostname_attempts
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "get",
            "pods",
        ):
            return SoperatorValidationCommandResult(
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
            "training-context",
            "-n",
            "soperator",
            "get",
            "slurmclusters",
            "-o",
        ):
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": "training"}, "status": {"phase": "Available"}}
                        ]
                    }
                ),
                "",
            )
        if command[:8] == (
            "kubectl",
            "--context",
            "training-context",
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
        ):
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%t":
                return SoperatorValidationCommandResult(command, 0, "idle~\nidle~\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(command, 0, "cpu*|idle~|(null)\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D":
                return SoperatorValidationCommandResult(command, 0, "cpu*|4\n", "")
            if command[8:12] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle~\nidle~\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-partition-hostnames" in script:
                    hostname_attempts += 1
                    hostname_scripts.append(script)
                    if hostname_attempts == 1:
                        return SoperatorValidationCommandResult(
                            command,
                            1,
                            "",
                            "srun: error: Unable to allocate resources: "
                            "Resources are not available\n",
                        )
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "\n".join(
                            [
                                "worker-cpu-0",
                                "worker-cpu-1",
                                "worker-cpu-2",
                                "worker-cpu-3",
                                "cxcli-soperator-partition-hostnames-ok partition=cpu nodes=4",
                            ]
                        )
                        + "\n",
                        "",
                    )
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-cpu-0\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    partition_check = next(
        check for check in report["checks"] if check["name"] == "Slurm all-partition hostname check"
    )
    assert report["passed"] is True
    assert partition_check["status"] == "passed"
    assert partition_check["summary"] == (
        "hostname jobs completed across all reported Slurm partition nodes: cpu=4."
    )
    assert hostname_attempts == 2
    assert all("--nodes=4 --ntasks=4" in script for script in hostname_scripts)
    assert all("--immediate=600" in script for script in hostname_scripts)


def test_soperator_partition_hostname_report_keeps_large_node_lists_vertical() -> None:
    spec = {"namespace": "soperator"}
    checks: list[dict[str, object]] = []
    hostnames = [f"worker-cpu-{index:04d}" for index in range(4000)]

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:5] == ("kubectl", "-n", "soperator", "get", "pods"):
            return SoperatorValidationCommandResult(
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
        if command[:6] == ("kubectl", "-n", "soperator", "exec", "login-0", "--"):
            if command[6:9] == ("sinfo", "-h", "-o") and command[9] == "%P|%D":
                return SoperatorValidationCommandResult(command, 0, "cpu|4000\n", "")
            if command[6:10] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle\n" * 4000, "")
            if command[6:8] == ("bash", "-lc"):
                stdout = "\n".join(
                    [
                        *hostnames,
                        "cxcli-soperator-partition-hostnames-ok partition=cpu nodes=4000",
                    ]
                )
                return SoperatorValidationCommandResult(command, 0, stdout + "\n", "")
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    soperator_validation._check_slurm_partition_hostnames(_runner, spec, checks)

    assert len(checks) == 1
    hostname_check = checks[0]
    assert hostname_check["status"] == "passed"
    assert isinstance(hostname_check["stdout"], list)
    assert hostname_check["stdout"][:3] == hostnames[:3]
    assert hostname_check["stdout"][3999] == hostnames[-1]
    assert hostname_check["stdout"][-1] == (
        "cxcli-soperator-partition-hostnames-ok partition=cpu nodes=4000"
    )
    assert hostname_check["partition_hostnames"] == [
        {
            "partition": "cpu",
            "expected_node_count": 4000,
            "reported_hostname_count": 4000,
            "status": "passed",
            "hostnames": hostnames,
        }
    ]


def test_soperator_gpu_allocation_report_keeps_large_host_lists_structured() -> None:
    spec = {"namespace": "soperator"}
    checks: list[dict[str, object]] = []
    hosts = [f"worker-gpu-{index:04d}" for index in range(4000)]

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:5] == ("kubectl", "-n", "soperator", "get", "pods"):
            return SoperatorValidationCommandResult(
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
        if command[:6] == ("kubectl", "-n", "soperator", "exec", "login-0", "--"):
            if command[6:9] == ("sinfo", "-h", "-o") and command[9] == "%P|%D|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu|4000|gpu:8\n", "")
            if command[6:10] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle\n" * 4000, "")
            if command[6:8] == ("bash", "-lc"):
                stdout_lines: list[str] = []
                for host in hosts:
                    stdout_lines.extend(f"GPU {index}: NVIDIA H100" for index in range(8))
                    stdout_lines.append(f"cxcli-soperator-gpu-allocation-ok host={host}")
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "\n".join(stdout_lines) + "\n",
                    "",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    soperator_validation._check_slurm_gpu_allocation(_runner, spec, checks)

    assert len(checks) == 1
    gpu_check = checks[0]
    assert gpu_check["status"] == "passed"
    assert isinstance(gpu_check["stdout"], list)
    assert len(gpu_check["stdout"]) == 10001
    assert gpu_check["stdout"][-1] == "... output truncated: 26000 additional line(s) ..."
    assert gpu_check["gpu_allocations"] == [
        {
            "partition": "gpu",
            "expected_node_count": 4000,
            "reported_host_count": 4000,
            "status": "passed",
            "hosts": hosts,
        }
    ]


def test_soperator_partition_hostname_retry_budget_still_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        soperator_validation,
        "_SLURM_PARTITION_HOSTNAME_RETRY_DELAY_SECONDS",
        0,
    )
    spec = {"namespace": "soperator"}
    checks: list[dict[str, object]] = []
    hostname_attempts = 0

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        nonlocal hostname_attempts
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:5] == ("kubectl", "-n", "soperator", "get", "pods"):
            return SoperatorValidationCommandResult(
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
        if command[:6] == ("kubectl", "-n", "soperator", "exec", "login-0", "--"):
            if command[6:9] == ("sinfo", "-h", "-o") and command[9] == "%P|%D":
                return SoperatorValidationCommandResult(command, 0, "gpu|2\n", "")
            if command[6:10] == ("sinfo", "-h", "-N", "-p"):
                return SoperatorValidationCommandResult(command, 0, "idle~\nidle~\n", "")
            if command[6:8] == ("bash", "-lc"):
                hostname_attempts += 1
                return SoperatorValidationCommandResult(
                    command,
                    1,
                    "srun: job 7 queued and waiting for resources\n",
                    "Copying stdout failed: read: connection reset by peer\n",
                )
        return SoperatorValidationCommandResult(command, 0, "ok\n", "")

    soperator_validation._check_slurm_partition_hostnames(_runner, spec, checks)

    assert hostname_attempts == 3
    assert len(checks) == 1
    assert checks[0]["name"] == "Slurm all-partition hostname check"
    assert checks[0]["status"] == "failed"
    assert checks[0]["passed"] is False
    assert checks[0]["summary"] == (
        "hostname job failed or did not report the expected marker on partition(s): gpu."
    )
    assert "\n".join(checks[0]["stdout"]).count("queued and waiting for resources") == 3
    assert "\n".join(checks[0]["stderr"]).count("connection reset by peer") == 3
