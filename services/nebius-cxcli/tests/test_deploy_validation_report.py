from __future__ import annotations

import json
from pathlib import Path

from nebius_cxcli.deploy_validation_report import (
    DEPLOY_REPORT_FILENAME,
    build_deploy_validation_report,
    clear_deploy_validation_artifacts,
    format_deploy_validation_summary_lines,
    validation_section_lines,
)


def test_build_deploy_validation_report_aggregates_results(tmp_path: Path) -> None:
    validations = [
        {
            "kind": "mk8s_gpu_operator_readiness",
            "name": "GPU stack readiness",
            "report_file": "gpu-stack-readiness-report.json",
        },
        {
            "kind": "mk8s_cluster_smoke",
            "name": "MK8s node inventory smoke",
            "report_file": "mk8s-node-inventory-smoke-report.json",
        },
        {
            "kind": "mk8s_cuda_smoke",
            "name": "CUDA smoke test",
            "report_file": "cuda-smoke-report.json",
        },
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test",
            "report_file": "nccl-test-report.json",
        },
        {
            "kind": "mk8s_observability_ingestion",
            "name": "Observability ingestion (cluster1)",
            "report_file": "observability-ingestion-report-cluster1.json",
        },
    ]
    (tmp_path / "gpu-stack-readiness-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "gpudirect_mode": "dma-buf",
                "gpu_operator": {"gpu_nodes": [{"name": "gpu-a"}, {"name": "gpu-b"}]},
                "network_operator": {
                    "required": True,
                    "rdma_required": True,
                    "device_plugin_snapshot": {
                        "rdma_resource_keys": ["rdma/shared_device"],
                        "rdma_resource_node_count": 2,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "mk8s-node-inventory-smoke-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "total_node_count": 5,
                "ready_node_count": 5,
                "cpu_node_count": 1,
                "ready_gpu_node_count": 4,
                "allocatable_gpu_count": 32,
                "expected_gpu_node_count": 4,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "cuda-smoke-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "selected_node_count": 2,
                "total_gpu_node_count": 4,
                "passed_node_count": 2,
                "skipped_node_count": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "observability-ingestion-report-cluster1.json").write_text(
        json.dumps(
            {
                "passed": True,
                "checks": [
                    {"name": "HelmRelease Ready", "passed": True, "summary": "Ready=True"},
                    {
                        "name": "Agent DaemonSet Ready",
                        "passed": True,
                        "summary": "DaemonSet pods ready 2/2",
                    },
                    {
                        "name": "Trace OTLP Service Ready",
                        "passed": True,
                        "summary": "OTLP/gRPC endpoints ready 2/2",
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    assert report.markdown_path == tmp_path / DEPLOY_REPORT_FILENAME
    assert report.overall_status == "incomplete"
    assert report.completed_count == 4
    assert report.passed_count == 4
    assert report.failed_count == 0
    assert report.not_run_count == 1
    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: INCOMPLETE (4/5 completed, 1 not run)",
        "  PASS GPU stack readiness: GPU Operator and Network Operator ready on 2 Ready GPU node(s); RDMA resources rdma/shared_device on 2 Ready GPU node(s); GPUDirect mode dma-buf.",
        "  PASS MK8s node inventory smoke: 5/5 Kubernetes node(s) Ready; 1 CPU-only node(s); 4 Ready GPU node(s) advertise 32 allocatable GPU(s); expected at least 4 GPU node(s).",
        "  PASS CUDA smoke test: 2/2 selected node(s) passed; total Ready GPU nodes 4; skipped 2.",
        "  NOT RUN NCCL test: No deploy validation results recorded yet.",
        "  PASS Observability ingestion (cluster1): 3/3 check(s) passed; DaemonSet pods ready 2/2; OTLP/gRPC endpoints ready 2/2.",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'gpu-stack-readiness-report.json'}",
        f"  JSON detail: {tmp_path / 'mk8s-node-inventory-smoke-report.json'}",
        f"  JSON detail: {tmp_path / 'cuda-smoke-report.json'}",
        f"  JSON detail: {tmp_path / 'observability-ingestion-report-cluster1.json'}",
    ]
    assert validation_section_lines(report) == [
        "## Validations",
        "",
        "- Overall status: `INCOMPLETE`",
        "- Completed validations: `4/5`",
        "- Passed: `4`",
        "- Failed: `0`",
        "- Not run: `1`",
        "",
        "### GPU stack readiness",
        "",
        "- Status: `PASS`",
        "- Detail report: `gpu-stack-readiness-report.json`",
        "- Summary: GPU Operator and Network Operator ready on 2 Ready GPU node(s); RDMA resources rdma/shared_device on 2 Ready GPU node(s); GPUDirect mode dma-buf.",
        "",
        "### MK8s node inventory smoke",
        "",
        "- Status: `PASS`",
        "- Detail report: `mk8s-node-inventory-smoke-report.json`",
        "- Summary: 5/5 Kubernetes node(s) Ready; 1 CPU-only node(s); 4 Ready GPU node(s) advertise 32 allocatable GPU(s); expected at least 4 GPU node(s).",
        "",
        "### CUDA smoke test",
        "",
        "- Status: `PASS`",
        "- Detail report: `cuda-smoke-report.json`",
        "- Summary: 2/2 selected node(s) passed; total Ready GPU nodes 4; skipped 2.",
        "",
        "### NCCL test",
        "",
        "- Status: `NOT RUN`",
        "- Detail report: `n/a`",
        "- Summary: No deploy validation results recorded yet.",
        "",
        "### Observability ingestion (cluster1)",
        "",
        "- Status: `PASS`",
        "- Detail report: `observability-ingestion-report-cluster1.json`",
        "- Summary: 3/3 check(s) passed; DaemonSet pods ready 2/2; OTLP/gRPC endpoints ready 2/2.",
        "- Checks (3):",
        "  1. `PASS` HelmRelease Ready: Ready=True",
        "  2. `PASS` Agent DaemonSet Ready: DaemonSet pods ready 2/2",
        "  3. `PASS` Trace OTLP Service Ready: OTLP/gRPC endpoints ready 2/2",
        "",
    ]


def test_clear_deploy_validation_artifacts_removes_stale_outputs(tmp_path: Path) -> None:
    validations = [
        {
            "kind": "mk8s_cluster_smoke",
            "report_file": "mk8s-node-inventory-smoke-report.json",
        },
        {"kind": "mk8s_cuda_smoke", "report_file": "cuda-smoke-report.json"},
        {"kind": "mk8s_nccl", "report_file": "nccl-test-report.json"},
    ]
    for name in (
        DEPLOY_REPORT_FILENAME,
        "deploy-validation-report.md",
        "mk8s-node-inventory-smoke-report.json",
        "cuda-smoke-report.json",
        "nccl-test-report.json",
    ):
        (tmp_path / name).write_text("data\n", encoding="utf-8")

    clear_deploy_validation_artifacts(validations, reports_dir=tmp_path)

    assert not (tmp_path / DEPLOY_REPORT_FILENAME).exists()
    assert not (tmp_path / "deploy-validation-report.md").exists()
    assert not (tmp_path / "mk8s-node-inventory-smoke-report.json").exists()
    assert not (tmp_path / "cuda-smoke-report.json").exists()
    assert not (tmp_path / "nccl-test-report.json").exists()


def test_clear_deploy_validation_artifacts_can_preserve_markdown(tmp_path: Path) -> None:
    validations = [
        {"kind": "soperator_cluster_smoke", "report_file": "soperator-smoke.json"},
    ]
    for name in (
        DEPLOY_REPORT_FILENAME,
        "deploy-validation-report.md",
        "soperator-smoke.json",
    ):
        (tmp_path / name).write_text("data\n", encoding="utf-8")

    clear_deploy_validation_artifacts(validations, reports_dir=tmp_path, include_markdown=False)

    assert (tmp_path / DEPLOY_REPORT_FILENAME).exists()
    assert (tmp_path / "deploy-validation-report.md").exists()
    assert not (tmp_path / "soperator-smoke.json").exists()


def test_build_deploy_validation_report_formats_soperator_smoke_summary(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "soperator_cluster_smoke",
            "name": "Soperator cluster smoke test (training)",
            "report_file": "soperator-cluster-validation-report-training.json",
        }
    ]
    (tmp_path / "soperator-cluster-validation-report-training.json").write_text(
        json.dumps(
            {
                "passed": True,
                "status": "passed",
                "summary": "5/5 Soperator/Slurm checks passed.",
                "checks": [
                    {
                        "name": "Slurm srun smoke job",
                        "passed": True,
                        "summary": "one-task synchronous srun job completed successfully.",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: PASS (1/1 completed, 0 not run)",
        "  PASS Soperator cluster smoke test (training): 5/5 Soperator/Slurm checks passed.",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'soperator-cluster-validation-report-training.json'}",
    ]
    assert "Slurm srun smoke job" in "\n".join(validation_section_lines(report))


def test_build_deploy_validation_report_preserves_skipped_soperator_subchecks(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "soperator_cluster_smoke",
            "name": "Soperator cluster smoke test (training)",
            "report_file": "soperator-cluster-validation-report-training.json",
        }
    ]
    (tmp_path / "soperator-cluster-validation-report-training.json").write_text(
        json.dumps(
            {
                "passed": True,
                "status": "passed",
                "summary": "7/8 Soperator/Slurm checks passed. 1 skipped.",
                "checks": [
                    {
                        "name": "Slurm NCCL benchmark",
                        "status": "skipped",
                        "passed": False,
                        "summary": (
                            "Skipped: full Slurm NCCL benchmark runs only on 8-GPU Slurm nodes."
                        ),
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)
    markdown = "\n".join(validation_section_lines(report))

    assert report.overall_status == "passed"
    assert "`SKIPPED` Slurm NCCL benchmark: Skipped:" in markdown
    assert "`FAIL` Slurm NCCL benchmark" not in markdown


def test_build_deploy_validation_report_formats_socket_mode_nccl_summary(tmp_path: Path) -> None:
    validations = [
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test",
            "report_file": "nccl-test-report.json",
        }
    ]
    (tmp_path / "nccl-test-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "launcher_phase": "Succeeded",
                "transport_label": "Socket/TCPIP",
                "avg_bus_bandwidth_gbps": 41.7,
                "threshold_gbps": 300.0,
                "threshold_enforced": False,
                "selected_worker_node_count": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: PASS (1/1 completed, 0 not run)",
        "  PASS NCCL test: Launcher phase Succeeded; Socket/TCPIP average bus bandwidth 41.7 Gbps across 2 worker node(s); RDMA threshold not enforced for this run.",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'nccl-test-report.json'}",
    ]
    assert (
        "- Summary: Launcher phase Succeeded; Socket/TCPIP average bus bandwidth "
        "**41.7** Gbps across 2 worker node(s); RDMA threshold not enforced for this run."
        in validation_section_lines(report)
    )


def test_build_deploy_validation_report_formats_single_rank_nccl_smoke(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test",
            "report_file": "nccl-test-report.json",
        }
    ]
    (tmp_path / "nccl-test-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "launcher_phase": "Succeeded",
                "transport_label": "Socket/TCPIP",
                "avg_bus_bandwidth_gbps": 0.0,
                "threshold_gbps": 300.0,
                "threshold_enforced": False,
                "bandwidth_observed": False,
                "single_rank_smoke": True,
                "selected_worker_node_count": 1,
                "nccl_dmabuf_env_name": "NCCL_DMABUF_ENABLE",
                "nccl_dmabuf_enable": "unset",
                "nccl_dmabuf_enable_source": "unset",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: PASS (1/1 completed, 0 not run)",
        "  PASS NCCL test: Launcher phase Succeeded; Socket/TCPIP single-rank smoke run across 1 worker node(s); no collective bus bandwidth observed; NCCL_DMABUF_ENABLE=unset; RDMA threshold not enforced for this run.",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'nccl-test-report.json'}",
    ]
    assert (
        "- Summary: Launcher phase Succeeded; Socket/TCPIP single-rank smoke run "
        "across 1 worker node(s); no collective bus bandwidth observed; "
        "NCCL_DMABUF_ENABLE=unset; RDMA threshold not enforced for this run."
        in validation_section_lines(report)
    )
    assert report.results[0].footer_summary == (
        "Succeeded; Socket/TCPIP single-rank smoke across 1 worker; "
        "no collective bandwidth observed; RDMA threshold not enforced."
    )


def test_build_deploy_validation_report_formats_skipped_gpu_workload_summary(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "mk8s_cuda_smoke",
            "name": "CUDA smoke test",
            "report_file": "cuda-smoke-report.json",
        },
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test",
            "report_file": "nccl-test-report.json",
        },
    ]
    for report_name, validation in (
        ("cuda-smoke-report.json", "CUDA smoke test"),
        ("nccl-test-report.json", "NCCL test"),
    ):
        (tmp_path / report_name).write_text(
            json.dumps(
                {
                    "validation": validation,
                    "passed": True,
                    "skipped": True,
                    "skip_reason": "all Ready GPU nodes already have their GPUs allocated to existing workloads",
                    "total_gpu_node_count": 2,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: PASS (2/2 completed, 0 not run)",
        "  PASS CUDA smoke test: Skipped: all Ready GPU nodes already have their GPUs allocated to existing workloads; total Ready GPU nodes 2.",
        "  PASS NCCL test: Skipped: all Ready GPU nodes already have their GPUs allocated to existing workloads; total Ready GPU nodes 2.",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'cuda-smoke-report.json'}",
        f"  JSON detail: {tmp_path / 'nccl-test-report.json'}",
    ]


def test_build_deploy_validation_report_uses_soperator_owned_gpu_smoke_for_skips(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "mk8s_cuda_smoke",
            "name": "CUDA smoke test (mk8s)",
            "target_ref": "mk8s",
            "report_file": "cuda-smoke-report-mk8s.json",
        },
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test (mk8s)",
            "target_ref": "mk8s",
            "report_file": "nccl-test-report-mk8s.json",
        },
        {
            "kind": "soperator_cluster_smoke",
            "name": "Soperator cluster smoke test (mk8s)",
            "target_ref": "mk8s",
            "report_file": "soperator-cluster-validation-report-mk8s.json",
        },
    ]
    for report_name, validation in (
        ("cuda-smoke-report-mk8s.json", "CUDA smoke test"),
        ("nccl-test-report-mk8s.json", "NCCL test"),
    ):
        (tmp_path / report_name).write_text(
            json.dumps(
                {
                    "validation": validation,
                    "passed": True,
                    "skipped": True,
                    "skip_reason": "all Ready GPU nodes already have their GPUs allocated to existing workloads",
                    "total_gpu_node_count": 2,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    (tmp_path / "soperator-cluster-validation-report-mk8s.json").write_text(
        json.dumps(
            {
                "kind": "soperator_cluster_smoke",
                "target_ref": "mk8s",
                "passed": True,
                "status": "passed",
                "summary": "7/7 Soperator/Slurm checks passed.",
                "checks": [
                    {
                        "name": "Slurm GPU allocation check",
                        "passed": True,
                        "summary": (
                            "one-GPU Slurm allocations reported NVIDIA GPUs across all reported "
                            "GPU partition nodes: gpu=2."
                        ),
                    },
                    {
                        "name": "Slurm NCCL benchmark",
                        "passed": True,
                        "summary": (
                            "two-node NCCL all_reduce_perf benchmark completed on partition gpu "
                            "with 16 rank(s) across 2 node(s) (8 GPU(s) per node); "
                            "average bus bandwidth 580.2 Gbps across 2G, 4G, 8G message sizes."
                        ),
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    summary_lines = format_deploy_validation_summary_lines(report)
    assert (
        "  PASS CUDA smoke test (mk8s): Soperator-owned Slurm GPU allocation passed: "
        "one-GPU Slurm allocations reported NVIDIA GPUs across all reported GPU "
        "partition nodes: gpu=2. "
        "The Kubernetes workload check was not scheduled because Soperator worker pods "
        "reserve all Ready GPU nodes."
    ) in summary_lines
    assert (
        "  PASS NCCL test (mk8s): Soperator-owned Slurm NCCL benchmark passed: "
        "two-node NCCL all_reduce_perf benchmark completed on partition gpu "
        "with 16 rank(s) across 2 node(s) (8 GPU(s) per node); "
        "average bus bandwidth 580.2 Gbps across 2G, 4G, 8G message sizes. "
        "The Kubernetes workload check was not scheduled because Soperator worker pods "
        "reserve all Ready GPU nodes."
    ) in summary_lines
    markdown = "\n".join(validation_section_lines(report))
    assert "### CUDA smoke test (mk8s)" in markdown
    assert "### NCCL test (mk8s)" in markdown
    assert "Summary: Skipped: all Ready GPU nodes" not in markdown
    assert "Soperator-owned Slurm GPU allocation passed" in markdown
    assert "Soperator-owned Slurm NCCL benchmark passed" in markdown
    assert "average bus bandwidth **580.2** Gbps" in markdown


def test_build_deploy_validation_report_does_not_promote_skipped_soperator_nccl(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test (mk8s)",
            "target_ref": "mk8s",
            "report_file": "nccl-test-report-mk8s.json",
        },
        {
            "kind": "soperator_cluster_smoke",
            "name": "Soperator cluster smoke test (mk8s)",
            "target_ref": "mk8s",
            "report_file": "soperator-cluster-validation-report-mk8s.json",
        },
    ]
    (tmp_path / "nccl-test-report-mk8s.json").write_text(
        json.dumps(
            {
                "validation": "NCCL test",
                "passed": True,
                "skipped": True,
                "skip_reason": "all Ready GPU nodes already have their GPUs allocated to existing workloads",
                "total_gpu_node_count": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "soperator-cluster-validation-report-mk8s.json").write_text(
        json.dumps(
            {
                "kind": "soperator_cluster_smoke",
                "target_ref": "mk8s",
                "passed": True,
                "status": "passed",
                "summary": "7/8 Soperator/Slurm checks passed. 1 skipped.",
                "checks": [
                    {
                        "name": "Slurm NCCL benchmark",
                        "status": "skipped",
                        "passed": False,
                        "summary": (
                            "Skipped: full Slurm NCCL benchmark runs only on 8-GPU Slurm nodes; "
                            "selected partition gpu reported 1 GPU(s) per node."
                        ),
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)
    summary_lines = format_deploy_validation_summary_lines(report)
    markdown = "\n".join(validation_section_lines(report))

    assert (
        "  PASS NCCL test (mk8s): Skipped: all Ready GPU nodes already have their GPUs "
        "allocated to existing workloads; total Ready GPU nodes 2."
    ) in summary_lines
    assert "Soperator-owned Slurm NCCL benchmark passed" not in markdown
    assert "`SKIPPED` Slurm NCCL benchmark: Skipped:" in markdown


def test_build_deploy_validation_report_formats_rdma_dmabuf_summary(tmp_path: Path) -> None:
    validations = [
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test",
            "report_file": "nccl-test-report.json",
        }
    ]
    (tmp_path / "nccl-test-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "launcher_phase": "Succeeded",
                "transport_label": "RDMA verbs (IB/RoCE)",
                "avg_bus_bandwidth_gbps": 781.2,
                "threshold_gbps": 300.0,
                "threshold_enforced": True,
                "selected_worker_node_count": 1,
                "gpudirect_mode": "dma-buf",
                "nccl_dmabuf_env_name": "NCCL_DMABUF_ENABLE",
                "nccl_dmabuf_enable": "1",
                "nccl_dmabuf_enable_source": "explicit MPI environment",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: PASS (1/1 completed, 0 not run)",
        "  PASS NCCL test: Launcher phase Succeeded; RDMA verbs (IB/RoCE) average bus bandwidth 781.2 Gbps vs threshold 300.0 Gbps across 1 worker node(s); NCCL_DMABUF_ENABLE=1 (explicit MPI environment; GPUDirect mode dma-buf).",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'nccl-test-report.json'}",
    ]
    assert (
        "- Summary: Launcher phase Succeeded; RDMA verbs (IB/RoCE) average bus bandwidth "
        "**781.2** Gbps vs threshold 300.0 Gbps across 1 worker node(s); "
        "NCCL_DMABUF_ENABLE=1 (explicit MPI environment; GPUDirect mode dma-buf)."
        in validation_section_lines(report)
    )


def test_build_deploy_validation_report_prefers_target_scoped_spec_name(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test (cluster2)",
            "report_file": "nccl-test-report-cluster2.json",
        }
    ]
    (tmp_path / "nccl-test-report-cluster2.json").write_text(
        json.dumps(
            {
                "validation": "NCCL test",
                "passed": True,
                "launcher_phase": "Succeeded",
                "transport_label": "Socket/TCPIP",
                "avg_bus_bandwidth_gbps": 1.0,
                "threshold_enforced": False,
                "selected_worker_node_count": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    assert report.results[0].name == "NCCL test (cluster2)"


def test_build_deploy_validation_report_summarizes_error_report(tmp_path: Path) -> None:
    validations = [
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test",
            "report_file": "nccl-test-report.json",
        }
    ]
    (tmp_path / "nccl-test-report.json").write_text(
        json.dumps(
            {
                "validation": "NCCL test",
                "passed": False,
                "error": "kubectl get pod nccl-test-nebius-launcher timed out after 30 seconds",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: FAIL (1/1 completed, 0 not run)",
        "  FAIL NCCL test: Failed before completion: kubectl get pod nccl-test-nebius-launcher timed out after 30 seconds",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'nccl-test-report.json'}",
    ]
