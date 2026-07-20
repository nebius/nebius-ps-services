from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from nebius_cxcli import soperator_validation
from nebius_cxcli.soperator_validation import (
    SOPERATOR_CLUSTER_VALIDATION_KIND,
    SoperatorValidationCommandResult,
    _gpu_driver_jail_script,
    _nccl_script,
    run_soperator_cluster_validations,
    soperator_acceptance_benchmark_specs,
    soperator_acceptance_smoke_specs,
    soperator_cluster_validation_specs,
)


def _gpu_driver_jail_result(
    command: tuple[str, ...],
    script: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> SoperatorValidationCommandResult:
    match = re.search(r"--nodelist=([^ \n']+)", script)
    hosts = match.group(1).split(",") if match else ["worker-gpu-0"]
    stdout = "".join(
        (
            "cxcli-soperator-gpu-driver-jail-ok "
            f"host={host} "
            "libcuda.so.1=/usr/lib/x86_64-linux-gnu/libcuda.so.1 "
            "libnvidia-ml.so.1=/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1\n"
        )
        for host in hosts
        if host
    )
    return SoperatorValidationCommandResult(command, returncode, stdout, stderr)


def _standard_gpu_nodeset_payload() -> dict[str, object]:
    return {
        "items": [
            {
                "metadata": {"name": "worker-gpu"},
                "spec": {
                    "gpu": {"enabled": True},
                    "slurmd": {
                        "resources": {"gpu": 8},
                        "volumes": {
                            "customVolumeMounts": [
                                {
                                    "name": "nvidia-driver-root",
                                    "mountPath": "/run/nvidia/driver",
                                    "readOnly": True,
                                    "volumeSource": {
                                        "hostPath": {
                                            "path": "/",
                                        }
                                    },
                                }
                            ]
                        },
                    },
                    "customInitContainers": [
                        {
                            "name": "cxcli-gpu-driver-jail",
                        }
                    ],
                },
                "status": {"phase": "Ready"},
            }
        ]
    }


def _standard_soperator_snapshot_result(
    command: tuple[str, ...],
) -> SoperatorValidationCommandResult | None:
    parts = list(command)
    try:
        get_index = parts.index("get")
    except ValueError:
        return None
    resource = parts[get_index + 1] if len(parts) > get_index + 1 else ""
    name = parts[get_index + 2] if len(parts) > get_index + 2 else ""
    if resource == "deployment" and name == "soperator-manager":
        return SoperatorValidationCommandResult(
            command,
            0,
            json.dumps(
                {
                    "spec": {"replicas": 1},
                    "status": {
                        "replicas": 1,
                        "readyReplicas": 1,
                        "availableReplicas": 1,
                        "updatedReplicas": 1,
                    },
                }
            ),
            "",
        )
    if resource == "nodesets":
        return SoperatorValidationCommandResult(
            command,
            0,
            json.dumps(_standard_gpu_nodeset_payload()),
            "",
        )
    if resource == "pvc" and name == "jail-pvc":
        return SoperatorValidationCommandResult(
            command,
            0,
            json.dumps({"status": {"phase": "Bound"}}),
            "",
        )
    if resource == "pv" and name == "jail-pv":
        return SoperatorValidationCommandResult(
            command,
            0,
            json.dumps({"status": {"phase": "Bound"}}),
            "",
        )
    if resource == "daemonset" and name == "jail-mount":
        return SoperatorValidationCommandResult(
            command,
            0,
            json.dumps(
                {
                    "status": {
                        "desiredNumberScheduled": 1,
                        "numberReady": 1,
                        "numberAvailable": 1,
                    }
                }
            ),
            "",
        )
    return None


def _standard_or_ok(command: tuple[str, ...]) -> SoperatorValidationCommandResult:
    standard = _standard_soperator_snapshot_result(command)
    if standard is not None:
        return standard
    return SoperatorValidationCommandResult(command, 0, "ok\n", "")


def test_soperator_validation_fails_missing_gpu_driver_jail_nodeset_contract() -> None:
    checks: list[dict[str, object]] = []

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        return SoperatorValidationCommandResult(
            command,
            0,
            json.dumps(
                {
                    "items": [
                        {
                            "metadata": {"name": "worker-gpu"},
                            "spec": {
                                "gpu": {"enabled": True},
                                "slurmd": {"resources": {"gpu": 1}},
                                "customInitContainers": [],
                            },
                        }
                    ]
                }
            ),
            "",
        )

    soperator_validation._check_gpu_driver_jail_nodeset_contract(
        _runner,
        {"namespace": "soperator", "target_version": "4.0.2-ps.3"},
        checks,
    )

    assert checks == [
        {
            "name": "GPU driver jail NodeSet contract",
            "status": "failed",
            "passed": False,
            "summary": (
                "GPU worker NodeSet(s) are missing the chart-owned "
                "nvidia-driver-root mount or cxcli-gpu-driver-jail init guard: "
                "worker-gpu."
            ),
            "command": "kubectl -n soperator get nodesets -o json",
            "gpu_driver_jail_nodesets": [
                {
                    "name": "worker-gpu",
                    "driver_root_mount": False,
                    "driver_jail_init": False,
                }
            ],
        }
    ]


def test_manager_validation_accepts_exact_checkpointed_bridge_pause() -> None:
    checks: list[dict[str, object]] = []

    def _runner(args, **_kwargs):
        command = tuple(str(item) for item in args)
        return SoperatorValidationCommandResult(
            command,
            0,
            json.dumps(
                {
                    "metadata": {
                        "uid": "manager-uid",
                        "generation": 7,
                    },
                    "spec": {"replicas": 0},
                    "status": {"observedGeneration": 7},
                }
            ),
            "",
        )

    soperator_validation._check_soperator_manager_deployment(
        _runner,
        {
            "namespace": "soperator",
            "checkpointed_manager_pause": {
                "schema": "nebius-cxcli/checkpointed-manager-pause-v1",
                "status": "verified",
                "deployment_uid": "manager-uid",
                "original_replicas": 1,
                "bridge_stage": "source-ha-active",
            },
        },
        checks,
    )

    assert checks[0]["status"] == "passed"
    assert checks[0]["checkpointed_pause"] is True


def test_soperator_validation_skips_gpu_driver_jail_nodeset_contract_for_old_chart() -> None:
    checks: list[dict[str, object]] = []
    calls: list[tuple[str, ...]] = []

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        return SoperatorValidationCommandResult(command, 0, '{"items": []}', "")

    soperator_validation._check_gpu_driver_jail_nodeset_contract(
        _runner,
        {"namespace": "soperator", "target_version": "4.0.1-ps.2"},
        checks,
    )

    assert calls == []
    assert checks == [
        {
            "name": "GPU driver jail NodeSet contract",
            "status": "skipped",
            "passed": False,
            "summary": (
                "Soperator chart version 4.0.1-ps.2 predates the chart-owned "
                "GPU driver jail NodeSet contract."
            ),
            "target_version": "4.0.1-ps.2",
            "minimum_contract_version": "4.0.2-ps.3",
        }
    ]


def test_soperator_validation_reports_zero_byte_gpu_driver_jail_library() -> None:
    checks: list[dict[str, object]] = []

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
            if command[6:10] == ("sinfo", "-N", "-h", "-o") and command[10] == "%P|%N|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu|worker-gpu-0|gpu:1\n", "")
            if command[6:9] == ("sinfo", "-h", "-p") and command[10:12] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("bash", "-lc"):
                return SoperatorValidationCommandResult(
                    command,
                    42,
                    (
                        "cxcli-soperator-gpu-driver-jail-empty "
                        "host=worker-gpu-0 lib=libcuda.so.1 "
                        "path=/usr/lib/x86_64-linux-gnu/libcuda.so.1\n"
                    ),
                    "",
                )
        return _standard_or_ok(command)

    assert (
        soperator_validation._check_slurm_gpu_driver_jail(
            _runner,
            {"namespace": "soperator", "mode": "acceptance"},
            checks,
            check_name="Slurm GPU driver jail acceptance",
        )
        is False
    )

    assert checks[0]["status"] == "failed"
    assert checks[0]["summary"] == (
        "GPU driver jail checks passed on 0/1 Slurm GPU node(s); failed=1, "
        "not_run=0; affected partition(s): gpu. First failure: "
        "cxcli-soperator-gpu-driver-jail-empty host=worker-gpu-0 lib=libcuda.so.1 "
        "path=/usr/lib/x86_64-linux-gnu/libcuda.so.1."
    )
    assert checks[0]["failure_details"] == [
        "cxcli-soperator-gpu-driver-jail-empty host=worker-gpu-0 "
        "lib=libcuda.so.1 path=/usr/lib/x86_64-linux-gnu/libcuda.so.1"
    ]


def test_gpu_driver_jail_script_checks_each_driver_library_independently() -> None:
    script = _gpu_driver_jail_script(partition="gpu", nodes=1, nodelist=("worker-gpu-0",))

    assert 'cuda_lib="$(check_lib libcuda.so.1)" || exit 42' in script
    assert 'nvml_lib="$(check_lib libnvidia-ml.so.1)" || exit 42' in script
    assert 'libs="$(check_lib libcuda.so.1)$(check_lib libnvidia-ml.so.1)"' not in script


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
            "report_file": "deploy-smoke-report-training.json",
            "inventory_report_file": "cluster-inventory-report-training.json",
            "readiness_timeout_seconds": 1200,
            "readiness_poll_seconds": 15.0,
            "required": True,
            "mode": "deploy",
        }
    ]


def test_soperator_acceptance_benchmark_specs_carry_run_only_overrides() -> None:
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
        "deploy": {"targets": [{"instance_id": "training", "kube_context": "training-context"}]},
    }

    specs = soperator_acceptance_benchmark_specs(
        payload,
        max_nodes=4,
        timeout="20m",
        average_bus_bandwidth_threshold_gbps=300.0,
    )

    assert specs == [
        {
            "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
            "name": "Soperator NCCL benchmark (training)",
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
            "report_file": "acceptance-benchmark-report-training.json",
            "inventory_report_file": "cluster-inventory-report-training.json",
            "readiness_timeout_seconds": 1200,
            "readiness_poll_seconds": 15.0,
            "required": True,
            "mode": "benchmark",
            "include_nccl_benchmark": True,
            "max_nodes": 4,
            "timeout": "20m",
            "average_bus_bandwidth_threshold_gbps": 300.0,
        }
    ]

    zero_threshold_specs = soperator_acceptance_benchmark_specs(
        payload,
        average_bus_bandwidth_threshold_gbps=0.0,
    )
    assert zero_threshold_specs[0]["average_bus_bandwidth_threshold_gbps"] == 0.0


def test_soperator_acceptance_smoke_specs_use_acceptance_report_filename() -> None:
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
        "deploy": {"targets": [{"instance_id": "training", "kube_context": "training-context"}]},
    }

    specs = soperator_acceptance_smoke_specs(
        payload,
        batch_size=64,
        concurrency=4,
        continue_on_failure=False,
    )

    assert specs[0]["name"] == "Soperator acceptance smoke test (training)"
    assert specs[0]["target_ref"] == "training"
    assert specs[0]["mode"] == "acceptance"
    assert specs[0]["report_file"] == "acceptance-smoke-report-training.json"
    assert specs[0]["inventory_report_file"] == "cluster-inventory-report-training.json"
    assert specs[0]["batch_size"] == 64
    assert specs[0]["concurrency"] == 4
    assert specs[0]["continue_on_failure"] is False


def test_nccl_script_honors_node_cap_and_optional_timeout() -> None:
    capped = _nccl_script(partition="gpu", max_nodes=4, timeout_seconds=20 * 60)
    uncapped = _nccl_script(partition="gpu")

    assert "requested_max_nodes=4" in capped
    assert "--time=00:20:00 --immediate=60" in capped
    assert "requested_max_nodes=0" in uncapped
    assert "--time=00:30:00" not in uncapped
    assert '--cpus-per-task="$cpus_per_task" --immediate=60' in uncapped
    assert "if (( gpus_per_node == 1 )); then" in capped
    assert "nccl_max_bytes=2G" in capped
    assert "nccl_max_bytes=8G" in capped
    assert "max_bytes=${CXCLI_NCCL_MAX_BYTES}" in capped
    assert '/usr/bin/all_reduce_perf_mpi -b 512M -e "$CXCLI_NCCL_MAX_BYTES" -f 2 -g 1' in capped


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
        "report_file": "deploy-smoke-report-training.json",
        "inventory_report_file": "cluster-inventory-report-training.json",
    }
    commands: list[tuple[str, ...]] = []
    timeouts: list[int] = []

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, check
        command = tuple(str(item) for item in args)
        commands.append(command)
        timeouts.append(timeout_seconds)
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
        return _standard_or_ok(command)

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    assert written == [tmp_path / "deploy-smoke-report-training.json"]
    assert (tmp_path / "cluster-inventory-report-training.json").exists()
    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["schema"] == "nebius-cxcli-soperator-cluster-validation/v2"
    assert report["test_purpose"] == "deployment-testing"
    assert report["scope"] == "soperator-deployment-snapshot"
    assert report["passed"] is True
    assert report["summary"] == "7/7 Soperator/Slurm checks passed."
    assert [check["name"] for check in report["checks"]] == [
        "Soperator manager deployment",
        "Soperator storage snapshot",
        "Soperator pod scheduling snapshot",
        "Soperator pod health snapshot",
        "SlurmCluster visibility",
        "NodeSet visibility",
        "GPU driver jail NodeSet contract",
    ]
    assert not any("slurmnodes" in " ".join(command) for command in commands)
    assert not any("cxcli-soperator-smoke" in " ".join(command) for command in commands)
    assert set(timeouts) == {30}


def test_soperator_acceptance_rejects_deploy_report_filename(tmp_path: Path) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator acceptance smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": "deploy-smoke-report-training.json",
        "mode": "acceptance",
    }
    commands: list[tuple[str, ...]] = []

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, check
        command = tuple(str(item) for item in args)
        commands.append(command)
        return _standard_or_ok(command)

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    assert written == [tmp_path / "acceptance-smoke-report-training.json"]
    assert not (tmp_path / "deploy-smoke-report-training.json").exists()
    assert commands == []
    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert report["test_purpose"] == "acceptance-smoke"
    assert report["checks"] == [
        {
            "name": "Soperator report file contract",
            "passed": False,
            "status": "failed",
            "summary": (
                "Soperator acceptance report_file must be "
                "acceptance-smoke-report-training.json; got deploy-smoke-report-training.json. "
                "Rerender or regenerate the acceptance-test spec with canonical report names."
            ),
        }
    ]


def test_run_soperator_cluster_validation_reports_slurmcluster_phase_without_waiting(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": "deploy-smoke-report-training.json",
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
        del input_text, check
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
        return _standard_or_ok(command)

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert slurmcluster_gets == 1
    slurmcluster_check = next(
        check for check in report["checks"] if check["name"] == "SlurmCluster visibility"
    )
    assert slurmcluster_check["phase"] == "Pending"


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
        "report_file": "deploy-smoke-report-training.json",
        "check_old_source_flux": True,
    }

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        del input_text, check
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
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Old source Flux desired state"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "deploy-smoke-report-training.json").read_text(encoding="utf-8")
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
        "report_file": "deploy-smoke-report-training.json",
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
        return _standard_or_ok(command)

    run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(
        (tmp_path / "deploy-smoke-report-training.json").read_text(encoding="utf-8")
    )
    skipped = [check for check in report["checks"] if check["status"] == "skipped"]
    assert skipped[0]["name"] == "Soperator pod scheduling snapshot"
    assert "mk8s-acct-db-0" in skipped[0]["summary"]
    assert "unschedulable" in skipped[0]["summary"]


def test_soperator_cluster_validation_recovers_stuck_populate_jail_job(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": "deploy-smoke-report-training.json",
        "populate_jail_recovery_timeout_seconds": 0,
    }
    deleted_pods: list[str] = []
    job_completed = False

    def _pods_payload() -> dict:
        items = [
            {
                "metadata": {
                    "name": "login-0",
                    "labels": {"app.kubernetes.io/component": "login"},
                },
                "status": {"phase": "Running"},
            },
            {
                "metadata": {
                    "name": "training-populate-jail-abc",
                    "labels": {"job-name": "training-populate-jail"},
                },
                "spec": {"nodeName": "system-node-1"},
                "status": {"phase": "Running"},
            },
            {
                "metadata": {"name": "jail-mount-xyz"},
                "spec": {"nodeName": "system-node-1"},
                "status": {"phase": "Running"},
            },
        ]
        return {"items": items}

    def _runner(
        args,
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        nonlocal job_completed
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:7] == (
            "kubectl",
            "-n",
            "soperator",
            "get",
            "job",
            "training-populate-jail",
            "-o",
        ):
            status = {"succeeded": 1} if job_completed else {"active": 1, "succeeded": 0}
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps({"spec": {"completions": 1, "backoffLimit": 6}, "status": status}),
                "",
            )
        if command[:5] == ("kubectl", "-n", "soperator", "get", "pods"):
            return SoperatorValidationCommandResult(command, 0, json.dumps(_pods_payload()), "")
        if command[:6] == ("kubectl", "-n", "soperator", "exec", "jail-mount-xyz", "--"):
            return SoperatorValidationCommandResult(command, 0, "2026-06-30T01:50:26+00:00\n", "")
        if command[:6] == (
            "kubectl",
            "-n",
            "soperator",
            "delete",
            "pod",
            "training-populate-jail-abc",
        ):
            deleted_pods.append(command[5])
            job_completed = True
            return SoperatorValidationCommandResult(
                command,
                0,
                'pod "training-populate-jail-abc" deleted\n',
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
        return _standard_or_ok(command)

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    assert deleted_pods == ["training-populate-jail-abc"]
    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    recovery = next(
        check for check in report["checks"] if check["name"] == "Populate jail recovery"
    )
    assert recovery["status"] == "passed"
    assert recovery["pod_name"] == "training-populate-jail-abc"
    assert recovery["jail_mount_pod"] == "jail-mount-xyz"
    assert recovery["sentinel_path"] == "/host/mnt/jail/.populated"


def test_populate_jail_job_failed_detects_zero_backoff_limit() -> None:
    assert (
        soperator_validation._job_failed(  # noqa: SLF001
            {"spec": {"backoffLimit": 0}, "status": {"failed": 1}}
        )
        is True
    )
    assert (
        soperator_validation._job_failed(  # noqa: SLF001
            {"spec": {}, "status": {"failed": 1}}
        )
        is False
    )


def test_soperator_cluster_validation_fails_on_crash_looping_soperator_pod(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": "deploy-smoke-report-training.json",
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
                                "metadata": {"name": "worker-0"},
                                "status": {
                                    "phase": "Running",
                                    "initContainerStatuses": [
                                        {
                                            "name": "cxcli-gpu-driver-jail",
                                            "state": {
                                                "waiting": {
                                                    "reason": "CrashLoopBackOff",
                                                    "message": (
                                                        "back-off restarting failed container"
                                                    ),
                                                }
                                            },
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
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Soperator pod health snapshot"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "deploy-smoke-report-training.json").read_text(encoding="utf-8")
    )
    failed = [check for check in report["checks"] if check["status"] == "failed"]
    assert failed[0]["name"] == "Soperator pod health snapshot"
    assert "worker-0" in failed[0]["summary"]
    assert "cxcli-gpu-driver-jail" in failed[0]["summary"]
    assert "CrashLoopBackOff" in failed[0]["summary"]


@pytest.mark.parametrize(
    ("mode", "report_file", "expected_test_purpose", "expected_scope"),
    [
        (
            "deploy",
            "deploy-smoke-report-training.json",
            "deployment-testing",
            "soperator-deployment-snapshot",
        ),
        (
            "acceptance",
            "acceptance-smoke-report-training.json",
            "acceptance-smoke",
            "all-node-slurm-smoke",
        ),
    ],
)
def test_soperator_cluster_validation_waits_for_jail_mount_pending_pods(
    tmp_path: Path,
    mode: str,
    report_file: str,
    expected_test_purpose: str,
    expected_scope: str,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": report_file,
        "readiness_timeout_seconds": 2,
        "readiness_poll_seconds": 0,
        "mode": mode,
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
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
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
                                    "MountVolume.NewMounter initialization failed for volume "
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
        return _standard_or_ok(command)

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    assert written == [tmp_path / report_file]
    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["mode"] == mode
    assert report["test_purpose"] == expected_test_purpose
    assert report["scope"] == expected_scope
    assert report["passed"] is True
    assert pod_gets >= 2
    assert daemonset_gets >= 2
    pod_check = next(
        check for check in report["checks"] if check["name"] == "Soperator pod scheduling snapshot"
    )
    assert pod_check["status"] == "passed"


def test_soperator_cluster_validation_reports_failed_mount_event_for_pending_pod(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "report_file": "deploy-smoke-report-training.json",
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
                                    "MountVolume.NewMounter initialization failed for volume "
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
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Soperator pod scheduling"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "deploy-smoke-report-training.json").read_text(encoding="utf-8")
    )
    failed = [check for check in report["checks"] if check["status"] == "failed"]
    assert failed[0]["name"] == "Soperator pod scheduling snapshot"
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
        "report_file": "acceptance-smoke-report-training.json",
        "readiness_timeout_seconds": 0,
        "mode": "acceptance",
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
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Soperator storage snapshot"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "acceptance-smoke-report-training.json").read_text(encoding="utf-8")
    )
    failed = [check for check in report["checks"] if check["status"] == "failed"]
    assert failed[0]["name"] == "Soperator storage snapshot"
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
        "report_file": "acceptance-smoke-report-training.json",
        "include_nccl_benchmark": True,
        "max_nodes": 4,
        "timeout": "20m",
        "average_bus_bandwidth_threshold_gbps": 580.2,
        "mode": "acceptance",
    }
    smoke_scripts: list[str] = []
    nccl_timeouts: list[int | None] = []

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
            if command[8:12] == ("sinfo", "-N", "-h", "-o") and command[12] == "%P|%N":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cpu|worker-cpu-0\ngpu|worker-gpu-0\n",
                    "",
                )
            if command[8:12] == ("sinfo", "-N", "-h", "-o") and command[12] == "%P|%N|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu|worker-gpu-0|gpu:8\n",
                    "",
                )
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                smoke_scripts.append(script)
                if "cxcli-soperator-partition-hostnames" in script:
                    partition = "gpu" if "--partition=gpu" in script else "cpu"
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        f"worker-{partition}-0\ncxcli-soperator-partition-hostnames-ok "
                        f"partition={partition} nodes=1\n",
                        "",
                    )
                if "cxcli-soperator-gpu-driver-jail" in script:
                    return _gpu_driver_jail_result(command, script)
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "\n".join(
                            [
                                "GPU 0: NVIDIA H100",
                                "cxcli-soperator-gpu-allocation-ok "
                                "host=worker-gpu-0 evidence=nvidia-smi",
                            ]
                        )
                        + "\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    nccl_timeouts.append(timeout_seconds)
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
        return _standard_or_ok(command)

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["summary"] == "14/14 Soperator/Slurm checks passed."
    assert [check["name"] for check in report["checks"]][-6:] == [
        "Slurm srun smoke job",
        "Slurm all-node hostname acceptance",
        "Slurm GPU driver jail acceptance",
        "Slurm all-node GPU allocation acceptance",
        "Slurm GPU driver jail benchmark preflight",
        "Slurm NCCL benchmark",
    ]
    contract_check = next(
        check for check in report["checks"] if check["name"] == "GPU driver jail NodeSet contract"
    )
    assert contract_check["summary"] == (
        "GPU worker NodeSets include the chart-owned read-only host driver root mount "
        "and GPU driver jail init guard."
    )
    partition_check = next(
        check for check in report["checks"] if check["name"] == "Slurm all-node hostname acceptance"
    )
    assert partition_check["summary"] == (
        "hostname acceptance jobs passed on 2/2 Slurm node(s) across 2 partition(s)."
    )
    assert partition_check["stdout"] == [
        "worker-cpu-0",
        "cxcli-soperator-partition-hostnames-ok partition=cpu nodes=1",
        "worker-gpu-0",
        "cxcli-soperator-partition-hostnames-ok partition=gpu nodes=1",
    ]
    assert partition_check["partition_hostnames"] == [
        {
            "partition": "cpu",
            "expected_node_count": 1,
            "tested_node_count": 1,
            "passed_node_count": 1,
            "failed_node_count": 0,
            "not_run_node_count": 0,
            "batch_size": 128,
            "batch_count": 1,
            "status": "passed",
            "nodes": [{"node_name": "worker-cpu-0", "status": "passed", "reported": True}],
        },
        {
            "partition": "gpu",
            "expected_node_count": 1,
            "tested_node_count": 1,
            "passed_node_count": 1,
            "failed_node_count": 0,
            "not_run_node_count": 0,
            "batch_size": 128,
            "batch_count": 1,
            "status": "passed",
            "nodes": [{"node_name": "worker-gpu-0", "status": "passed", "reported": True}],
        },
    ]
    driver_jail_check = next(
        check for check in report["checks"] if check["name"] == "Slurm GPU driver jail acceptance"
    )
    assert driver_jail_check["summary"] == (
        "GPU driver jail checks passed on 1/1 Slurm GPU node(s); libcuda.so.1, "
        "libnvidia-ml.so.1, and nvidia-smi were visible from the Slurm job root."
    )
    assert driver_jail_check["gpu_driver_jail"] == [
        {
            "partition": "gpu",
            "expected_node_count": 1,
            "tested_node_count": 1,
            "passed_node_count": 1,
            "failed_node_count": 0,
            "not_run_node_count": 0,
            "batch_size": 128,
            "batch_count": 1,
            "status": "passed",
            "nodes": [{"node_name": "worker-gpu-0", "status": "passed", "reported": True}],
        }
    ]
    gpu_check = next(
        check
        for check in report["checks"]
        if check["name"] == "Slurm all-node GPU allocation acceptance"
    )
    assert gpu_check["summary"] == (
        "one-GPU Slurm acceptance allocations passed on 1/1 GPU node(s) across 1 partition(s)."
    )
    assert gpu_check["stdout"] == [
        "GPU 0: NVIDIA H100",
        "cxcli-soperator-gpu-allocation-ok host=worker-gpu-0 evidence=nvidia-smi",
    ]
    assert gpu_check["gpu_allocations"] == [
        {
            "partition": "gpu",
            "expected_node_count": 1,
            "tested_node_count": 1,
            "passed_node_count": 1,
            "failed_node_count": 0,
            "not_run_node_count": 0,
            "batch_size": 128,
            "batch_count": 1,
            "status": "passed",
            "nodes": [
                {
                    "node_name": "worker-gpu-0",
                    "status": "passed",
                    "reported": True,
                    "evidence": "nvidia-smi",
                }
            ],
        }
    ]
    assert any(
        "cxcli-soperator-partition-hostnames" in script
        and "--partition=gpu" in script
        and "--nodelist=worker-gpu-0" in script
        and "--nodes=1 --ntasks=1" in script
        for script in smoke_scripts
    )
    assert any(
        "cxcli-soperator-gpu-allocation" in script
        and "--partition=gpu" in script
        and "--nodelist=worker-gpu-0" in script
        and "--nodes=1 --ntasks=1" in script
        for script in smoke_scripts
    )
    nccl_check = report["checks"][-1]
    assert nccl_check["summary"] == (
        "multi-node NCCL all_reduce_perf benchmark completed on partition gpu "
        "with 16 rank(s) across 2 node(s) (8 GPU(s) per node); "
        "average bus bandwidth 580.2 Gbps across 2G, 4G, 8G message sizes. "
        "Required threshold: 580.2 Gbps."
    )
    assert nccl_check["avg_large_message_bus_bandwidth_gbps"] == pytest.approx(580.2)
    assert nccl_check["large_message_bus_bandwidth_gbps"] == {
        "2G": 320.1,
        "4G": 640.3,
        "8G": 780.2,
    }
    assert nccl_check["average_bus_bandwidth_threshold_gbps"] == 580.2
    assert nccl_check["bandwidth_threshold_passed"] is True
    assert nccl_check["max_nodes"] == 4
    assert nccl_check["all_nodes"] is False
    assert nccl_check["timeout"] == "20m"
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
    assert any("requested_max_nodes=4" in script for script in smoke_scripts)
    assert any("--time=00:20:00 --immediate=60" in script for script in smoke_scripts)
    assert nccl_timeouts == [1200]
    assert any(
        '--nodes="$target_nodes" --ntasks="$target_nodes" --ntasks-per-node=1' in script
        for script in smoke_scripts
    )
    assert any(
        "srun --job-name=cxcli-soperator-nccl-launcher --nodes=1 --ntasks=1" in script
        for script in smoke_scripts
    )
    assert all("SLURM_PROCID" not in script for script in smoke_scripts)


def test_soperator_acceptance_benchmark_fails_without_gpu_partition(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator NCCL benchmark (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "acceptance-benchmark-report-training.json",
        "mode": "benchmark",
        "include_nccl_benchmark": True,
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
        if command[:4] == ("kubectl", "get", "helmreleases.helm.toolkit.fluxcd.io", "-A"):
            return SoperatorValidationCommandResult(command, 0, '{"items":[]}\n', "")
        if "pods" in command and "-o" in command:
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
        if "slurmclusters" in command and "-o" in command:
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
        if "pvc" in command and "-o" in command:
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps({"status": {"phase": "Bound"}}),
                "",
            )
        if "pv" in command and "-o" in command:
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps({"status": {"phase": "Bound"}}),
                "",
            )
        if "daemonset" in command and "-o" in command:
            return SoperatorValidationCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "status": {
                            "desiredNumberScheduled": 0,
                            "numberReady": 0,
                            "numberAvailable": 0,
                        }
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
                return SoperatorValidationCommandResult(command, 0, "cpu|idle|(null)\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Slurm NCCL benchmark"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "acceptance-benchmark-report-training.json").read_text(encoding="utf-8")
    )
    nccl_check = next(
        check for check in report["checks"] if check["name"] == "Slurm NCCL benchmark"
    )
    assert report["test_purpose"] == "acceptance-benchmark"
    assert report["scope"] == "slurm-nccl"
    assert report["passed"] is False
    assert nccl_check["status"] == "failed"
    assert nccl_check["failure_reason"] == "no_eligible_gpu_partition"
    assert "No eligible Slurm GPU partition" in nccl_check["summary"]
    assert not any("all_reduce_perf" in " ".join(command) for command in commands)


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
        "report_file": "acceptance-benchmark-report-training.json",
        "include_nccl_benchmark": True,
        "average_bus_bandwidth_threshold_gbps": 300.0,
        "mode": "benchmark",
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
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
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
                        f"worker-{partition}-0\n"
                        "cxcli-soperator-partition-hostnames-ok "
                        f"partition={partition} nodes=1\n",
                        "",
                    )
                if "cxcli-soperator-gpu-driver-jail" in script:
                    return _gpu_driver_jail_result(command, script)
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA H100\ncxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n",
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
        return _standard_or_ok(command)

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
        "report_file": "acceptance-benchmark-report-training.json",
        "include_nccl_benchmark": True,
        "average_bus_bandwidth_threshold_gbps": 300.0,
        "mode": "benchmark",
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
            if command[8:12] == ("sinfo", "-N", "-h", "-o") and command[12] == "%P|%N|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu|worker-gpu-0|gpu:8\n",
                    "",
                )
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
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
                if "cxcli-soperator-gpu-driver-jail" in script:
                    return _gpu_driver_jail_result(command, script)
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA H100\ncxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n",
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
        return _standard_or_ok(command)

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["summary"] == "10/10 Soperator/Slurm checks passed."
    nccl_check = report["checks"][-1]
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "passed"
    assert nccl_check["summary"] == (
        "single-node multi-GPU NCCL all_reduce_perf benchmark completed on partition gpu "
        "with 8 rank(s) on 1 node (8 GPU(s) on the node); average bus bandwidth "
        "566.9 Gbps across 2G, 4G, 8G message sizes. Required threshold: 300.0 Gbps."
    )
    assert nccl_check["avg_large_message_bus_bandwidth_gbps"] == pytest.approx(566.866, abs=0.01)
    assert nccl_check["large_message_bus_bandwidth_gbps"] == {
        "2G": 500.1,
        "4G": 590.4,
        "8G": 610.1,
    }
    assert nccl_check["average_bus_bandwidth_threshold_gbps"] == 300.0
    assert nccl_check["bandwidth_threshold_passed"] is True
    assert nccl_check["bandwidth_threshold_comment"] == ""
    assert nccl_check["one_gpu_platform"] is False
    assert nccl_check["multi_node_benchmark"] is False
    assert nccl_check["single_node_multi_gpu_benchmark"] is True
    assert nccl_check["benchmark_mode"] == "single-node-multi-gpu"
    assert nccl_check["mode"] == "single-node-multi-gpu"
    assert nccl_check["nodes"] == 1
    assert nccl_check["gpus_per_node"] == 8
    assert nccl_check["ranks"] == 8


def test_soperator_cluster_validation_fails_multi_gpu_slurm_nccl_below_threshold(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "acceptance-benchmark-report-training.json",
        "include_nccl_benchmark": True,
        "average_bus_bandwidth_threshold_gbps": 300.0,
        "mode": "benchmark",
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
            if command[8:12] == ("sinfo", "-N", "-h", "-o") and command[12] == "%P|%N|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu|worker-gpu-0|gpu:8\n",
                    "",
                )
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
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
                if "cxcli-soperator-gpu-driver-jail" in script:
                    return _gpu_driver_jail_result(command, script)
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA H100\ncxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n",
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
                                "2147483648 536870912 float sum -1 12345 80.0 100.1 0 "
                                "12346 79.9 100.0 0",
                                "4294967296 1073741824 float sum -1 12345 90.0 120.4 0 "
                                "12346 89.8 120.0 0",
                                "8589934592 2147483648 float sum -1 12345 100.0 140.1 0 "
                                "12346 99.8 140.0 0",
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
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Slurm NCCL benchmark"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "acceptance-benchmark-report-training.json").read_text(encoding="utf-8")
    )
    assert report["passed"] is False
    nccl_check = report["checks"][-1]
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "failed"
    assert nccl_check["summary"] == (
        "single-node multi-GPU NCCL all_reduce_perf benchmark completed on partition gpu "
        "with 8 rank(s) on 1 node (8 GPU(s) on the node); average bus bandwidth "
        "120.2 Gbps across 2G, 4G, 8G message sizes. Required threshold: 300.0 Gbps. "
        "Observed bandwidth is below the required threshold."
    )
    assert nccl_check["avg_large_message_bus_bandwidth_gbps"] == pytest.approx(120.2, abs=0.01)
    assert nccl_check["large_message_bus_bandwidth_gbps"] == {
        "2G": 100.1,
        "4G": 120.4,
        "8G": 140.1,
    }
    assert nccl_check["average_bus_bandwidth_threshold_gbps"] == 300.0
    assert nccl_check["bandwidth_threshold_passed"] is False
    assert nccl_check["bandwidth_threshold_comment"] == ""
    assert nccl_check["one_gpu_platform"] is False
    assert nccl_check["single_node_multi_gpu_benchmark"] is True
    assert nccl_check["gpus_per_node"] == 8
    assert nccl_check["ranks"] == 8


def test_soperator_cluster_validation_fails_reported_multi_gpu_slurm_nccl_below_threshold(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "acceptance-benchmark-report-training.json",
        "include_nccl_benchmark": True,
        "average_bus_bandwidth_threshold_gbps": 300.0,
        "mode": "benchmark",
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
                return SoperatorValidationCommandResult(command, 0, "gpu*|2\n", "")
            if command[8:11] == ("sinfo", "-h", "-o") and command[11] == "%P|%D|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu*|2|gpu:8\n", "")
            if command[8:12] == ("sinfo", "-N", "-h", "-o") and command[12] == "%P|%N|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu|worker-gpu-0|gpu:8\ngpu|worker-gpu-1|gpu:8\n",
                    "",
                )
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
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
                if "cxcli-soperator-gpu-driver-jail" in script:
                    return _gpu_driver_jail_result(command, script)
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA H100\ncxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "\n".join(
                            [
                                "cxcli-soperator-nccl-ok mode=multi-node partition=gpu "
                                "nodes=2 gpus_per_node=1 ranks=2 reported_gpus_per_node=8 "
                                "max_bytes=2G",
                                "# nThread 1 nGpus 1 minBytes 536870912 maxBytes 2147483648",
                                "2147483648 536870912 float sum -1 12345 70.0 120.1 0 "
                                "12346 69.8 119.9 0",
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
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Slurm NCCL benchmark"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "acceptance-benchmark-report-training.json").read_text(encoding="utf-8")
    )
    assert report["passed"] is False
    nccl_check = report["checks"][-1]
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "failed"
    assert nccl_check["avg_large_message_bus_bandwidth_gbps"] == pytest.approx(120.1)
    assert nccl_check["average_bus_bandwidth_threshold_gbps"] == 300.0
    assert nccl_check["bandwidth_threshold_passed"] is False
    assert nccl_check["bandwidth_threshold_comment"] == ""
    assert nccl_check["one_gpu_platform"] is False
    assert nccl_check["gpus_per_node"] == 1
    assert nccl_check["reported_gpus_per_node"] == 8
    assert nccl_check["ranks"] == 2


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
                'if [[ " $* " == *" cxcli-soperator-nccl-probe "* ]]; then',
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


def test_soperator_cluster_validation_fails_slurm_nccl_when_no_gpu_allocation_succeeds(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "acceptance-benchmark-report-training.json",
        "include_nccl_benchmark": True,
        "average_bus_bandwidth_threshold_gbps": 300.0,
        "mode": "benchmark",
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
            if command[8:12] == ("sinfo", "-N", "-h", "-o") and command[12] == "%P|%N":
                return SoperatorValidationCommandResult(command, 0, "gpu|worker-gpu-0\n", "")
            if command[8:12] == ("sinfo", "-N", "-h", "-o") and command[12] == "%P|%N|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu|worker-gpu-0|gpu:8\n",
                    "",
                )
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-partition-hostnames" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "worker-gpu-0\n"
                        "cxcli-soperator-partition-hostnames-ok partition=gpu nodes=1\n",
                        "",
                    )
                if "cxcli-soperator-gpu-driver-jail" in script:
                    return _gpu_driver_jail_result(command, script)
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA H100\ncxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        1,
                        "",
                        "cxcli-soperator-nccl-allocation-failed: could not allocate any GPU count.\n",
                    )
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-gpu-0\n",
                    "",
                )
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Slurm NCCL benchmark"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "acceptance-benchmark-report-training.json").read_text(encoding="utf-8")
    )
    nccl_check = report["checks"][-1]
    assert report["passed"] is False
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "failed"
    assert nccl_check["summary"] == (
        "Slurm NCCL benchmark could not allocate GPUs on partition gpu: "
        "could not allocate any GPU count."
    )
    assert nccl_check["failure_reason"] == "slurm_gpu_allocation_failed"


def test_soperator_cluster_validation_reports_slurm_nccl_cuda_driver_runtime_mismatch(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "acceptance-benchmark-report-training.json",
        "include_nccl_benchmark": True,
        "mode": "benchmark",
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
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        1,
                        (
                            "cxcli-soperator-nccl-ok mode=multi-node partition=gpu "
                            "nodes=5 gpus_per_node=1 ranks=5 reported_gpus_per_node=1\n"
                            "cxcli-soperator-nccl-hostfile\n"
                            "worker-0 slots=1\nworker-1 slots=1\n"
                        ),
                        (
                            "Test CUDA failure util.cu:555 'CUDA driver version is "
                            "insufficient for CUDA runtime version'\n"
                            "mpirun detected that one or more processes exited with "
                            "non-zero status\n"
                        ),
                    )
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-srun-ok\nworker-gpu-0\n",
                    "",
                )
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Slurm NCCL benchmark"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "acceptance-benchmark-report-training.json").read_text(encoding="utf-8")
    )
    nccl_check = report["checks"][-1]
    assert report["passed"] is False
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "failed"
    assert nccl_check["summary"] == (
        "NCCL all_reduce_perf failed on partition gpu: CUDA driver version is "
        "insufficient for the CUDA runtime used by /usr/bin/all_reduce_perf_mpi."
    )
    assert nccl_check["failure_reason"] == "cuda_driver_runtime_mismatch"
    assert nccl_check["benchmark_mode"] == "multi-node"
    assert nccl_check["nodes"] == 5
    assert nccl_check["gpus_per_node"] == 1
    assert nccl_check["ranks"] == 5


def test_soperator_cluster_validation_runs_slurm_nccl_smoke_on_one_total_gpu(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "acceptance-benchmark-report-training.json",
        "include_nccl_benchmark": True,
        "average_bus_bandwidth_threshold_gbps": 300.0,
        "mode": "benchmark",
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
            if command[8:12] == ("sinfo", "-N", "-h", "-o") and command[12] == "%P|%N|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu|worker-gpu-0|gpu:1\n",
                    "",
                )
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
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
                if "cxcli-soperator-gpu-driver-jail" in script:
                    return _gpu_driver_jail_result(command, script)
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA L40S\ncxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "\n".join(
                            [
                                "cxcli-soperator-nccl-ok mode=single-node-single-gpu "
                                "partition=gpu nodes=1 gpus_per_node=1 ranks=1 "
                                "reported_gpus_per_node=1 max_bytes=2G",
                                "# nThread 1 nGpus 1 minBytes 536870912 maxBytes 2147483648",
                                "2147483648 536870912 float sum -1 1410.26 1522.8 0.00 0 "
                                "0.09 2e+07 0.00 0",
                                "# Avg bus bandwidth    : 0",
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
        return _standard_or_ok(command)

    written = run_soperator_cluster_validations(
        [spec],
        reports_dir=tmp_path,
        command_runner=_runner,
    )

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["summary"] == "10/10 Soperator/Slurm checks passed."
    nccl_check = report["checks"][-1]
    assert nccl_check["name"] == "Slurm NCCL benchmark"
    assert nccl_check["status"] == "passed"
    assert nccl_check["summary"] == (
        "single-rank Slurm NCCL all_reduce_perf smoke completed on partition gpu with "
        "1 GPU on 1 node; no collective bandwidth metric is reported for a one-rank run."
    )
    assert nccl_check["single_rank_smoke"] is True
    assert nccl_check["avg_large_message_bus_bandwidth_gbps"] is None
    assert nccl_check["large_message_bus_bandwidth_gbps"] == {}
    assert nccl_check["average_bus_bandwidth_threshold_gbps"] == 300.0
    assert nccl_check["bandwidth_threshold_passed"] is None
    assert nccl_check["benchmark_mode"] == "single-node-single-gpu"
    assert nccl_check["max_message_size"] == "2G"
    assert nccl_check["ranks"] == 1


def test_soperator_cluster_validation_runs_slurm_nccl_benchmark_on_two_one_gpu_nodes(
    tmp_path: Path,
) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "acceptance-benchmark-report-training.json",
        "include_nccl_benchmark": True,
        "average_bus_bandwidth_threshold_gbps": 300.0,
        "mode": "benchmark",
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
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\nidle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-partition-hostnames" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "worker-gpu-0\n"
                        "cxcli-soperator-partition-hostnames-ok partition=gpu nodes=1\n",
                        "",
                    )
                if "cxcli-soperator-gpu-driver-jail" in script:
                    return _gpu_driver_jail_result(command, script)
                if "cxcli-soperator-gpu-allocation" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "GPU 0: NVIDIA L40S\ncxcli-soperator-gpu-allocation-ok host=worker-gpu-0\n",
                        "",
                    )
                if "cxcli-soperator-nccl" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "\n".join(
                            [
                                "cxcli-soperator-nccl-ok mode=multi-node partition=gpu "
                                "nodes=2 gpus_per_node=1 ranks=2 reported_gpus_per_node=1 "
                                "max_bytes=2G",
                                "# nThread 1 nGpus 1 minBytes 536870912 maxBytes 2147483648",
                                "2147483648 536870912 float sum -1 12345 70.0 120.1 0 "
                                "12346 69.8 119.9 0",
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
        return _standard_or_ok(command)

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
    assert nccl_check["summary"] == (
        "multi-node NCCL all_reduce_perf benchmark completed on partition gpu with "
        "2 rank(s) across 2 node(s) (1 GPU(s) per node); average bus bandwidth "
        "120.1 Gbps across 2G message size. Required threshold: 300.0 Gbps. "
        "Observed average bus bandwidth is below the configured threshold; for a "
        "1-GPU Slurm NCCL run this is recorded as informational because the NCCL "
        "workload completed and reported average bandwidth."
    )
    assert nccl_check["avg_large_message_bus_bandwidth_gbps"] == pytest.approx(120.1)
    assert nccl_check["large_message_bus_bandwidth_gbps"] == {"2G": 120.1}
    assert nccl_check["average_bus_bandwidth_threshold_gbps"] == 300.0
    assert nccl_check["bandwidth_threshold_passed"] is False
    assert "1-GPU Slurm NCCL run" in nccl_check["bandwidth_threshold_comment"]
    assert nccl_check["one_gpu_platform"] is True
    assert nccl_check["max_message_size"] == "2G"
    assert nccl_check["multi_node_benchmark"] is True
    assert nccl_check["single_rank_smoke"] is False
    assert nccl_check["gpus_per_node"] == 1
    assert nccl_check["ranks"] == 2


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
        "report_file": "acceptance-smoke-report-training.json",
        "mode": "acceptance",
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
            if command[8:12] == ("sinfo", "-N", "-h", "-o") and command[12] == "%P|%N":
                return SoperatorValidationCommandResult(command, 0, "gpu|worker-gpu-0\n", "")
            if command[8:12] == ("sinfo", "-N", "-h", "-o") and command[12] == "%P|%N|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "gpu|worker-gpu-0|gpu:8\n",
                    "",
                )
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "mix\nidle\n", "")
            if command[8:10] == ("squeue", "-h"):
                return SoperatorValidationCommandResult(command, 0, "", "")
            if command[8:10] == ("bash", "-lc"):
                script = command[10]
                if "cxcli-soperator-partition-hostnames" in script:
                    return SoperatorValidationCommandResult(
                        command,
                        0,
                        "worker-gpu-0\n"
                        "cxcli-soperator-partition-hostnames-ok partition=gpu nodes=1\n",
                        "",
                    )
                if "cxcli-soperator-gpu-driver-jail" in script:
                    return _gpu_driver_jail_result(command, script)
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
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Slurm all-node GPU allocation acceptance"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "acceptance-smoke-report-training.json").read_text(encoding="utf-8")
    )
    gpu_check = next(
        check
        for check in report["checks"]
        if check["name"] == "Slurm all-node GPU allocation acceptance"
    )
    assert report["passed"] is False
    assert gpu_check["status"] == "failed"
    assert gpu_check["summary"] == (
        "one-GPU Slurm acceptance allocations passed on 0/1 GPU node(s); "
        "failed=1, not_run=0; affected partition(s): gpu."
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
        "report_file": "acceptance-smoke-report-training.json",
        "mode": "acceptance",
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
        return _standard_or_ok(command)

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
        "report_file": "acceptance-smoke-report-training.json",
        "mode": "acceptance",
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
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
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
        return _standard_or_ok(command)

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
    smoke_check = next(
        check for check in report["checks"] if check["name"] == "Slurm srun smoke job"
    )
    assert "partition cpu" in smoke_check["summary"]
    driver_jail_check = next(
        check for check in report["checks"] if check["name"] == "Slurm GPU driver jail acceptance"
    )
    assert driver_jail_check["status"] == "skipped"


def test_soperator_cluster_validation_marks_inval_nodes_unhealthy(tmp_path: Path) -> None:
    spec = {
        "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
        "name": "Soperator cluster smoke test (training)",
        "target_ref": "training",
        "namespace": "soperator",
        "cluster_name": "training",
        "kube_context": "training-context",
        "report_file": "acceptance-smoke-report-training.json",
        "mode": "acceptance",
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
        return _standard_or_ok(command)

    with pytest.raises(RuntimeError, match="Slurm node status"):
        run_soperator_cluster_validations(
            [spec],
            reports_dir=tmp_path,
            command_runner=_runner,
        )

    report = json.loads(
        (tmp_path / "acceptance-smoke-report-training.json").read_text(encoding="utf-8")
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
        "report_file": "acceptance-smoke-report-training.json",
        "mode": "acceptance",
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
            if command[8:11] == ("sinfo", "-h", "-p") and command[12:14] == (
                "-o",
                "%t",
            ):
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
                                "cxcli-soperator-partition-hostnames-ok partition=cpu nodes=1",
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
        return _standard_or_ok(command)

    checks: list[dict[str, object]] = []
    sampled_spec = {**spec, "mode": "deploy"}
    soperator_validation._check_slurm_partition_hostnames(_runner, sampled_spec, checks)

    assert len(checks) == 1
    partition_check = checks[0]
    assert partition_check["status"] == "passed"
    assert partition_check["summary"] == (
        "hostname smoke jobs completed on sampled Slurm partition nodes: cpu=1/4."
    )
    assert hostname_attempts == 2
    assert all("--nodes=1 --ntasks=1" in script for script in hostname_scripts)
    assert all("--immediate=600" in script for script in hostname_scripts)


def test_soperator_partition_hostname_report_samples_large_partitions() -> None:
    spec = {"namespace": "soperator"}
    checks: list[dict[str, object]] = []
    hostnames = [f"worker-cpu-{index:04d}" for index in range(4000)]
    hostname_scripts: list[str] = []

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
            if command[6:9] == ("sinfo", "-h", "-p") and command[10:12] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("bash", "-lc"):
                hostname_scripts.append(command[8])
                stdout = "\n".join(
                    [
                        hostnames[0],
                        "cxcli-soperator-partition-hostnames-ok partition=cpu nodes=1",
                    ]
                )
                return SoperatorValidationCommandResult(command, 0, stdout + "\n", "")
        return _standard_or_ok(command)

    soperator_validation._check_slurm_partition_hostnames(_runner, spec, checks)

    assert len(checks) == 1
    hostname_check = checks[0]
    assert hostname_check["status"] == "passed"
    assert isinstance(hostname_check["stdout"], list)
    assert hostname_check["stdout"][:1] == hostnames[:1]
    assert hostname_check["stdout"][-1] == (
        "cxcli-soperator-partition-hostnames-ok partition=cpu nodes=1"
    )
    assert hostname_check["partition_hostnames"] == [
        {
            "partition": "cpu",
            "expected_node_count": 4000,
            "sampled_node_count": 1,
            "reported_hostname_count": 1,
            "status": "passed",
            "hostnames": [hostnames[0]],
        }
    ]
    assert len(hostname_scripts) == 1
    assert "--nodes=1 --ntasks=1" in hostname_scripts[0]
    assert "--nodes=4000" not in hostname_scripts[0]


def test_soperator_gpu_allocation_is_acceptance_only() -> None:
    spec = {"namespace": "soperator"}
    checks: list[dict[str, object]] = []
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
        return _standard_or_ok(command)

    soperator_validation._check_slurm_gpu_allocation(_runner, spec, checks)

    assert checks == []
    assert commands == []


def test_soperator_acceptance_hostname_batches_all_large_partition_nodes() -> None:
    spec = {
        "namespace": "soperator",
        "mode": "acceptance",
        "batch_size": 128,
        "concurrency": 1,
    }
    checks: list[dict[str, object]] = []
    hostnames = [f"worker-cpu-{index:04d}" for index in range(4000)]
    hostname_scripts: list[str] = []

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
            if command[6:10] == ("sinfo", "-N", "-h", "-o") and command[10] == "%P|%N":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "\n".join(f"cpu|{hostname}" for hostname in hostnames) + "\n",
                    "",
                )
            if command[6:9] == ("sinfo", "-h", "-p") and command[10:12] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("bash", "-lc"):
                script = command[8]
                hostname_scripts.append(script)
                raw_nodelist = script.split("--nodelist=", 1)[1].split()[0]
                batch_nodes = raw_nodelist.split(",")
                stdout = "\n".join(
                    [
                        *batch_nodes,
                        "cxcli-soperator-partition-hostnames-ok "
                        f"partition=cpu nodes={len(batch_nodes)}",
                    ]
                )
                return SoperatorValidationCommandResult(command, 0, stdout + "\n", "")
        return _standard_or_ok(command)

    soperator_validation._check_slurm_partition_hostnames(_runner, spec, checks)

    assert len(checks) == 1
    hostname_check = checks[0]
    assert hostname_check["name"] == "Slurm all-node hostname acceptance"
    assert hostname_check["status"] == "passed"
    assert len(hostname_scripts) == 32
    assert all("--nodelist=" in script for script in hostname_scripts)
    assert all("--nodes=4000" not in script for script in hostname_scripts)
    partition_report = hostname_check["partition_hostnames"][0]
    assert partition_report["expected_node_count"] == 4000
    assert partition_report["tested_node_count"] == 4000
    assert partition_report["passed_node_count"] == 4000
    assert partition_report["batch_size"] == 128
    assert partition_report["batch_count"] == 32
    assert len(partition_report["nodes"]) == 4000
    assert partition_report["nodes"][0] == {
        "node_name": "worker-cpu-0000",
        "status": "passed",
        "reported": True,
    }
    assert partition_report["nodes"][-1] == {
        "node_name": "worker-cpu-3999",
        "status": "passed",
        "reported": True,
    }


def test_soperator_acceptance_hostname_fail_fast_stops_scheduling_batches() -> None:
    spec = {
        "namespace": "soperator",
        "mode": "acceptance",
        "batch_size": 1,
        "concurrency": 4,
        "continue_on_failure": False,
    }
    checks: list[dict[str, object]] = []
    hostnames = [f"worker-cpu-{index}" for index in range(4)]
    hostname_scripts: list[str] = []

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
            if command[6:10] == ("sinfo", "-N", "-h", "-o") and command[10] == "%P|%N":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "\n".join(f"cpu|{hostname}" for hostname in hostnames) + "\n",
                    "",
                )
            if command[6:9] == ("sinfo", "-h", "-p") and command[10:12] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("bash", "-lc"):
                hostname_scripts.append(command[8])
                return SoperatorValidationCommandResult(command, 1, "", "srun failed\n")
        return _standard_or_ok(command)

    soperator_validation._check_slurm_partition_hostnames(_runner, spec, checks)

    assert len(hostname_scripts) == 1
    hostname_check = checks[0]
    assert hostname_check["status"] == "failed"
    partition_report = hostname_check["partition_hostnames"][0]
    assert partition_report["tested_node_count"] == 1
    assert partition_report["failed_node_count"] == 1
    assert partition_report["not_run_node_count"] == 3
    assert [node["status"] for node in partition_report["nodes"]] == [
        "failed",
        "not_run",
        "not_run",
        "not_run",
    ]


def test_soperator_acceptance_gpu_allocation_batches_all_large_partition_nodes() -> None:
    spec = {
        "namespace": "soperator",
        "mode": "acceptance",
        "batch_size": 128,
        "concurrency": 1,
    }
    checks: list[dict[str, object]] = []
    hosts = [f"worker-gpu-{index:04d}" for index in range(4000)]
    gpu_scripts: list[str] = []

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
            if command[6:10] == ("sinfo", "-N", "-h", "-o") and command[10] == "%P|%N|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "\n".join(f"gpu|{host}|gpu:8" for host in hosts) + "\n",
                    "",
                )
            if command[6:9] == ("sinfo", "-h", "-p") and command[10:12] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("bash", "-lc"):
                script = command[8]
                gpu_scripts.append(script)
                raw_nodelist = script.split("--nodelist=", 1)[1].split()[0]
                batch_nodes = raw_nodelist.split(",")
                stdout = "\n".join(
                    [
                        "GPU 0: NVIDIA H100",
                        *[
                            f"cxcli-soperator-gpu-allocation-ok host={host} evidence=nvidia-smi"
                            for host in batch_nodes
                        ],
                    ]
                )
                return SoperatorValidationCommandResult(command, 0, stdout + "\n", "")
        return _standard_or_ok(command)

    soperator_validation._check_slurm_gpu_allocation(_runner, spec, checks)

    assert len(checks) == 1
    gpu_check = checks[0]
    assert gpu_check["name"] == "Slurm all-node GPU allocation acceptance"
    assert gpu_check["status"] == "passed"
    assert len(gpu_scripts) == 32
    assert all("--nodelist=" in script for script in gpu_scripts)
    assert all("--nodes=4000" not in script for script in gpu_scripts)
    allocation_report = gpu_check["gpu_allocations"][0]
    assert allocation_report["expected_node_count"] == 4000
    assert allocation_report["tested_node_count"] == 4000
    assert allocation_report["passed_node_count"] == 4000
    assert allocation_report["batch_size"] == 128
    assert allocation_report["batch_count"] == 32
    assert len(allocation_report["nodes"]) == 4000
    assert allocation_report["nodes"][0] == {
        "node_name": "worker-gpu-0000",
        "status": "passed",
        "reported": True,
        "evidence": "nvidia-smi",
    }
    assert allocation_report["nodes"][-1] == {
        "node_name": "worker-gpu-3999",
        "status": "passed",
        "reported": True,
        "evidence": "nvidia-smi",
    }


def test_soperator_acceptance_gpu_allocation_accepts_proc_driver_device_evidence() -> None:
    spec = {
        "namespace": "soperator",
        "mode": "acceptance",
        "batch_size": 128,
        "concurrency": 1,
    }
    checks: list[dict[str, object]] = []
    gpu_scripts: list[str] = []

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
            if command[6:10] == ("sinfo", "-N", "-h", "-o") and command[10] == "%P|%N|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu|worker-4|gpu:1\n", "")
            if command[6:9] == ("sinfo", "-h", "-p") and command[10:12] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("bash", "-lc"):
                gpu_scripts.append(command[8])
                stdout = "\n".join(
                    [
                        "cxcli-soperator-gpu-allocation-warning host=worker-4 "
                        "nvidia-smi-unusable path=/usr/bin/nvidia-smi",
                        "cxcli-soperator-gpu-proc host=worker-4 Model: NVIDIA H100 80GB HBM3",
                        "cxcli-soperator-gpu-device host=worker-4 path=/dev/nvidia0",
                        "cxcli-soperator-gpu-allocation-ok "
                        "host=worker-4 evidence=proc-driver-device",
                    ]
                )
                return SoperatorValidationCommandResult(command, 0, stdout + "\n", "")
        return _standard_or_ok(command)

    soperator_validation._check_slurm_gpu_allocation(_runner, spec, checks)

    assert len(checks) == 1
    gpu_check = checks[0]
    assert gpu_check["status"] == "passed"
    assert "nvidia-smi-unusable" in "\n".join(gpu_check["stdout"])
    assert 'grep -h "^Model:"' in gpu_scripts[0]
    allocation_report = gpu_check["gpu_allocations"][0]
    assert allocation_report["passed_node_count"] == 1
    assert allocation_report["nodes"] == [
        {
            "node_name": "worker-4",
            "status": "passed",
            "reported": True,
            "evidence": "proc-driver-device",
        }
    ]


def test_soperator_acceptance_gpu_allocation_rejects_marker_without_evidence() -> None:
    spec = {
        "namespace": "soperator",
        "mode": "acceptance",
        "batch_size": 128,
        "concurrency": 1,
    }
    checks: list[dict[str, object]] = []

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
            if command[6:10] == ("sinfo", "-N", "-h", "-o") and command[10] == "%P|%N|%G":
                return SoperatorValidationCommandResult(command, 0, "gpu|worker-0|gpu:1\n", "")
            if command[6:9] == ("sinfo", "-h", "-p") and command[10:12] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("bash", "-lc"):
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "cxcli-soperator-gpu-allocation-ok host=worker-0\n",
                    "",
                )
        return _standard_or_ok(command)

    soperator_validation._check_slurm_gpu_allocation(_runner, spec, checks)

    assert len(checks) == 1
    gpu_check = checks[0]
    assert gpu_check["status"] == "failed"
    allocation_report = gpu_check["gpu_allocations"][0]
    assert allocation_report["failed_node_count"] == 1
    assert allocation_report["nodes"] == [
        {
            "node_name": "worker-0",
            "status": "failed",
            "reported": False,
        }
    ]


def test_soperator_acceptance_gpu_fail_fast_stops_scheduling_batches() -> None:
    spec = {
        "namespace": "soperator",
        "mode": "acceptance",
        "batch_size": 1,
        "concurrency": 4,
        "continue_on_failure": False,
    }
    checks: list[dict[str, object]] = []
    hosts = [f"worker-gpu-{index}" for index in range(4)]
    gpu_scripts: list[str] = []

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
            if command[6:10] == ("sinfo", "-N", "-h", "-o") and command[10] == "%P|%N|%G":
                return SoperatorValidationCommandResult(
                    command,
                    0,
                    "\n".join(f"gpu|{host}|gpu:8" for host in hosts) + "\n",
                    "",
                )
            if command[6:9] == ("sinfo", "-h", "-p") and command[10:12] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle\n", "")
            if command[6:8] == ("bash", "-lc"):
                gpu_scripts.append(command[8])
                return SoperatorValidationCommandResult(command, 1, "", "srun failed\n")
        return _standard_or_ok(command)

    soperator_validation._check_slurm_gpu_allocation(_runner, spec, checks)

    assert len(gpu_scripts) == 1
    gpu_check = checks[0]
    assert gpu_check["status"] == "failed"
    allocation_report = gpu_check["gpu_allocations"][0]
    assert allocation_report["tested_node_count"] == 1
    assert allocation_report["failed_node_count"] == 1
    assert allocation_report["not_run_node_count"] == 3
    assert [node["status"] for node in allocation_report["nodes"]] == [
        "failed",
        "not_run",
        "not_run",
        "not_run",
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
            if command[6:9] == ("sinfo", "-h", "-p") and command[10:12] == (
                "-o",
                "%t",
            ):
                return SoperatorValidationCommandResult(command, 0, "idle~\nidle~\n", "")
            if command[6:8] == ("bash", "-lc"):
                hostname_attempts += 1
                return SoperatorValidationCommandResult(
                    command,
                    1,
                    "srun: job 7 queued and waiting for resources\n",
                    "Copying stdout failed: read: connection reset by peer\n",
                )
        return _standard_or_ok(command)

    soperator_validation._check_slurm_partition_hostnames(_runner, spec, checks)

    assert hostname_attempts == 3
    assert len(checks) == 1
    assert checks[0]["name"] == "Slurm all-partition hostname check"
    assert checks[0]["status"] == "failed"
    assert checks[0]["passed"] is False
    assert checks[0]["summary"] == (
        "hostname smoke job failed or did not report the expected marker on partition(s): gpu."
    )
    assert "\n".join(checks[0]["stdout"]).count("queued and waiting for resources") == 3
    assert "\n".join(checks[0]["stderr"]).count("connection reset by peer") == 3
