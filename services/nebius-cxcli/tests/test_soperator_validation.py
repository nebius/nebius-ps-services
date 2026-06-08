from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebius_cxcli.soperator_validation import (
    SOPERATOR_CLUSTER_VALIDATION_KIND,
    SoperatorValidationCommandResult,
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
            "kube_context": "training-context",
            "report_file": "soperator-cluster-validation-report-training.json",
            "required": True,
        }
    ]


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
                json.dumps({"items": [{"metadata": {"name": "training"}, "status": {"phase": "Available"}}]}),
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
                json.dumps({"items": [{"metadata": {"name": "training"}, "status": {"phase": "Available"}}]}),
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
        (tmp_path / "soperator-cluster-validation-report-training.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["passed"] is False
    node_status = next(check for check in report["checks"] if check["name"] == "Slurm node status")
    assert node_status["status"] == "failed"
