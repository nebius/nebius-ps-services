from __future__ import annotations

import json
from pathlib import Path

import pytest

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
            "report_file": "deploy-gpu-stack-readiness-report.json",
        },
        {
            "kind": "mk8s_cluster_smoke",
            "name": "MK8s node inventory smoke",
            "report_file": "cluster-inventory-report.json",
        },
        {
            "kind": "mk8s_gpu_visibility",
            "name": "GPU visibility probe",
            "report_file": "deploy-gpu-visibility-report.json",
        },
        {
            "kind": "mk8s_observability_ingestion",
            "name": "Observability ingestion (cluster1)",
            "report_file": "observability-ingestion-report-cluster1.json",
        },
    ]
    (tmp_path / "deploy-gpu-stack-readiness-report.json").write_text(
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
    (tmp_path / "cluster-inventory-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "total_node_count": 5,
                "ready_node_count": 5,
                "cpu_node_count": 1,
                "ready_gpu_node_count": 4,
                "allocatable_gpu_count": 32,
                "expected_gpu_node_count": 4,
                "checks": [
                    {
                        "name": "Minimum expected Ready GPU nodes",
                        "passed": True,
                        "summary": (
                            "4 Ready GPU node(s) discovered; "
                            "configured minimum expected Ready GPU nodes: 4"
                        ),
                    },
                    {
                        "name": "Minimum expected Ready GPU nodes per group",
                        "passed": True,
                        "summary": "Minimum expected Ready GPU nodes per group met: gpu=4/4",
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "deploy-gpu-visibility-report.json").write_text(
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
    assert report.overall_status == "passed"
    assert report.completed_count == 4
    assert report.passed_count == 4
    assert report.failed_count == 0
    assert report.not_run_count == 0
    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: PASS (4/4 completed, 0 not run)",
        "  PASS GPU stack readiness: GPU Operator and Network Operator ready on 2 Ready GPU node(s); RDMA resources rdma/shared_device on 2 Ready GPU node(s); GPUDirect mode dma-buf.",
        "  PASS MK8s node inventory smoke: 5/5 Kubernetes node(s) Ready; 1 CPU-only node(s); 4 Ready GPU node(s) advertise 32 allocatable GPU(s); configured minimum expected Ready GPU nodes: 4.",
        "  PASS GPU visibility probe: 2/2 selected node(s) passed; total Ready GPU nodes 4; skipped 2.",
        "  PASS Observability ingestion (cluster1): 3/3 check(s) passed; DaemonSet pods ready 2/2; OTLP/gRPC endpoints ready 2/2.",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'deploy-gpu-stack-readiness-report.json'}",
        f"  JSON detail: {tmp_path / 'cluster-inventory-report.json'}",
        f"  JSON detail: {tmp_path / 'deploy-gpu-visibility-report.json'}",
        f"  JSON detail: {tmp_path / 'observability-ingestion-report-cluster1.json'}",
    ]
    assert validation_section_lines(report) == [
        "## Validations",
        "",
        "- Overall status: `PASS`",
        "- Completed validations: `4/4`",
        "- Passed: `4`",
        "- Failed: `0`",
        "- Not run: `0`",
        "",
        "### GPU stack readiness",
        "",
        "- Status: `PASS`",
        "- Detail report: `deploy-gpu-stack-readiness-report.json`",
        "- Summary: GPU Operator and Network Operator ready on 2 Ready GPU node(s); RDMA resources rdma/shared_device on 2 Ready GPU node(s); GPUDirect mode dma-buf.",
        "",
        "### MK8s node inventory smoke",
        "",
        "- Status: `PASS`",
        "- Detail report: `cluster-inventory-report.json`",
        "- Summary: 5/5 Kubernetes node(s) Ready; 1 CPU-only node(s); 4 Ready GPU node(s) advertise 32 allocatable GPU(s); configured minimum expected Ready GPU nodes: 4.",
        "- Checks (2):",
        "  1. `PASS` Minimum expected Ready GPU nodes: 4 Ready GPU node(s) discovered; configured minimum expected Ready GPU nodes: 4",
        "  2. `PASS` Minimum expected Ready GPU nodes per group: Minimum expected Ready GPU nodes per group met: gpu=4/4",
        "",
        "### GPU visibility probe",
        "",
        "- Status: `PASS`",
        "- Detail report: `deploy-gpu-visibility-report.json`",
        "- Summary: 2/2 selected node(s) passed; total Ready GPU nodes 4; skipped 2.",
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
            "report_file": "cluster-inventory-report.json",
        },
        {"kind": "mk8s_gpu_visibility", "report_file": "deploy-gpu-visibility-report.json"},
    ]
    for name in (
        DEPLOY_REPORT_FILENAME,
        "deploy-validation-report.md",
        "cluster-inventory-report.json",
        "deploy-gpu-visibility-report.json",
    ):
        (tmp_path / name).write_text("data\n", encoding="utf-8")

    clear_deploy_validation_artifacts(validations, reports_dir=tmp_path)

    assert not (tmp_path / DEPLOY_REPORT_FILENAME).exists()
    assert not (tmp_path / "deploy-validation-report.md").exists()
    assert not (tmp_path / "cluster-inventory-report.json").exists()
    assert not (tmp_path / "deploy-gpu-visibility-report.json").exists()


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
            "report_file": "deploy-smoke-report-training.json",
        }
    ]
    (tmp_path / "deploy-smoke-report-training.json").write_text(
        json.dumps(
            {
                "passed": True,
                "status": "passed",
                "summary": "5/5 Soperator/Slurm checks passed.",
                "checks": [
                    {
                        "name": "Soperator manager deployment",
                        "passed": True,
                        "summary": "soperator-manager deployment is visible.",
                    },
                    {
                        "name": "Soperator storage snapshot",
                        "passed": True,
                        "summary": "Soperator jail storage objects are visible and ready.",
                    },
                    {
                        "name": "Soperator pod scheduling snapshot",
                        "passed": True,
                        "summary": "No Pending Soperator pods were visible in the fast snapshot.",
                    },
                    {
                        "name": "SlurmCluster visibility",
                        "passed": True,
                        "summary": "target SlurmCluster training is visible with phase Available.",
                    },
                    {
                        "name": "NodeSet visibility",
                        "passed": True,
                        "summary": "Soperator NodeSets are visible: worker=Ready.",
                    },
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
        f"  JSON detail: {tmp_path / 'deploy-smoke-report-training.json'}",
    ]
    markdown = "\n".join(validation_section_lines(report))
    assert "`PASS` NodeSet visibility: Soperator NodeSets are visible: worker=Ready." in markdown
    assert "Slurm srun smoke job" not in markdown


def test_build_deploy_validation_report_preserves_skipped_soperator_subchecks(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "soperator_cluster_smoke",
            "name": "Soperator cluster smoke test (training)",
            "report_file": "deploy-smoke-report-training.json",
        }
    ]
    (tmp_path / "deploy-smoke-report-training.json").write_text(
        json.dumps(
            {
                "passed": True,
                "status": "passed",
                "summary": "4/5 Soperator/Slurm checks passed. 1 skipped.",
                "checks": [
                    {
                        "name": "Soperator pod scheduling snapshot",
                        "status": "skipped",
                        "passed": False,
                        "summary": (
                            "Pending Soperator pod(s) visible in the fast snapshot: "
                            "worker-0: scheduling in progress."
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
    assert "`SKIPPED` Soperator pod scheduling snapshot: Pending Soperator pod" in markdown
    assert "`FAIL` Soperator pod scheduling snapshot" not in markdown
    assert "Slurm NCCL benchmark" not in markdown


def test_build_deploy_validation_report_rejects_acceptance_only_kinds(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test",
            "report_file": "acceptance-benchmark-report.json",
        },
        {
            "kind": "mk8s_cuda_smoke",
            "name": "K8s CUDA acceptance smoke",
            "report_file": "acceptance-smoke-report.json",
        },
    ]

    with pytest.raises(ValueError, match="acceptance-test smoke or acceptance-test benchmark"):
        build_deploy_validation_report(validations, reports_dir=tmp_path)


def test_build_deploy_validation_report_formats_skipped_gpu_workload_summary(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "mk8s_gpu_visibility",
            "name": "GPU visibility probe",
            "report_file": "deploy-gpu-visibility-report.json",
        },
    ]
    (tmp_path / "deploy-gpu-visibility-report.json").write_text(
        json.dumps(
            {
                "validation": "GPU visibility probe",
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
        "  Overall: PASS (1/1 completed, 0 not run)",
        "  PASS GPU visibility probe: Skipped: all Ready GPU nodes already have their GPUs allocated to existing workloads; total Ready GPU nodes 2.",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'deploy-gpu-visibility-report.json'}",
    ]


def test_build_deploy_validation_report_keeps_soperator_gpu_visibility_skip(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "mk8s_gpu_visibility",
            "name": "GPU visibility probe (mk8s)",
            "target_ref": "mk8s",
            "report_file": "deploy-gpu-visibility-report-mk8s.json",
        },
        {
            "kind": "soperator_cluster_smoke",
            "name": "Soperator cluster smoke test (mk8s)",
            "target_ref": "mk8s",
            "report_file": "deploy-smoke-report-mk8s.json",
        },
    ]
    (tmp_path / "deploy-gpu-visibility-report-mk8s.json").write_text(
        json.dumps(
            {
                "validation": "GPU visibility probe (mk8s)",
                "passed": True,
                "skipped": True,
                "skip_reason": "all Ready GPU nodes already have their GPUs allocated to existing workloads",
                "total_gpu_node_count": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "deploy-smoke-report-mk8s.json").write_text(
        json.dumps(
            {
                "kind": "soperator_cluster_smoke",
                "target_ref": "mk8s",
                "passed": True,
                "status": "passed",
                "summary": "5/5 Soperator/Slurm checks passed.",
                "checks": [
                    {
                        "name": "Soperator manager deployment",
                        "passed": True,
                        "summary": "soperator-manager deployment is visible.",
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
        "  PASS GPU visibility probe (mk8s): Skipped: all Ready GPU nodes already have "
        "their GPUs allocated to existing workloads; total Ready GPU nodes 2."
    ) in summary_lines
    markdown = "\n".join(validation_section_lines(report))
    assert "### GPU visibility probe (mk8s)" in markdown
    assert "Summary: Skipped: all Ready GPU nodes" in markdown
    assert ("Soperator-owned " + "Slurm GPU allocation passed") not in markdown


def test_build_deploy_validation_report_prefers_target_scoped_spec_name(
    tmp_path: Path,
) -> None:
    validations = [
        {
            "kind": "soperator_cluster_smoke",
            "name": "Soperator cluster smoke test (cluster2)",
            "report_file": "deploy-smoke-report-cluster2.json",
        }
    ]
    (tmp_path / "deploy-smoke-report-cluster2.json").write_text(
        json.dumps(
            {
                "validation": "Soperator cluster smoke test",
                "passed": True,
                "summary": "Soperator smoke passed.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    assert report.results[0].name == "Soperator cluster smoke test (cluster2)"


def test_build_deploy_validation_report_summarizes_error_report(tmp_path: Path) -> None:
    validations = [
        {
            "kind": "mk8s_gpu_visibility",
            "name": "GPU visibility probe",
            "report_file": "deploy-gpu-visibility-report.json",
        }
    ]
    (tmp_path / "deploy-gpu-visibility-report.json").write_text(
        json.dumps(
            {
                "validation": "GPU visibility probe",
                "passed": False,
                "error": "kubectl get pod cuda-smoke-gpu-a timed out after 30 seconds",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_deploy_validation_report(validations, reports_dir=tmp_path)

    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: FAIL (1/1 completed, 0 not run)",
        "  FAIL GPU visibility probe: Failed before completion: kubectl get pod cuda-smoke-gpu-a timed out after 30 seconds",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'deploy-gpu-visibility-report.json'}",
    ]
