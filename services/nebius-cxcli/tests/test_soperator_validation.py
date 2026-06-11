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
            "target_version": "",
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
        (tmp_path / "soperator-cluster-validation-report-training.json").read_text(
            encoding="utf-8"
        )
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
        (tmp_path / "soperator-cluster-validation-report-training.json").read_text(
            encoding="utf-8"
        )
    )
    failed = [check for check in report["checks"] if check["status"] == "failed"]
    assert failed[0]["name"] == "Soperator pod scheduling"
    assert "mk8s-acct-db-0" in failed[0]["summary"]
    assert "unschedulable" in failed[0]["summary"]


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
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%t|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cpu*|idle|(null)\ngpu|idle|gpu:8\n",
                    "",
                )
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                smoke_scripts.append(script)
                if "cxcli-soperator-gpu-visibility" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "cxcli-soperator-gpu-visibility-ok\nworker-gpu-0\nGPU 0: NVIDIA H100\n",
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
    assert report["summary"] == "7/7 Soperator/Slurm checks passed."
    assert [check["name"] for check in report["checks"]][-2:] == [
        "Slurm GPU visibility test",
        "Slurm NCCL benchmark",
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
                    {"items": [{"metadata": {"name": "training"}, "status": {"phase": "Available"}}]}
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
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-gpu-visibility" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "cxcli-soperator-gpu-visibility-ok\nworker-gpu-0\nGPU 0: NVIDIA H100\n",
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
    assert report["summary"] == "7/7 Soperator/Slurm checks passed."
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
                    {"items": [{"metadata": {"name": "training"}, "status": {"phase": "Available"}}]}
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
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-gpu-visibility" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "cxcli-soperator-gpu-visibility-ok\nworker-gpu-0\nGPU 0: NVIDIA L40S\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        (
                            "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark "
                            "requires at least 2 GPUs on the only GPU node in partition gpu; "
                            "found 1 GPU(s).\n"
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
    assert report["summary"] == "6/7 Soperator/Slurm checks passed. 1 skipped."
    nccl_check = report["checks"][-1]
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "skipped"
    assert nccl_check["skipped"] is True
    assert nccl_check["summary"] == (
        "Skipped: full Slurm NCCL benchmark requires at least 2 GPUs on the only GPU node "
        "in partition gpu; found 1 GPU(s)."
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
