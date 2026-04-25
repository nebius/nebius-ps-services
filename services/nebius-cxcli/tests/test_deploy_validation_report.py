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
            "kind": "mk8s_gpu_visibility",
            "name": "GPU Visibility test",
            "report_file": "gpu-visibility-report.json",
        },
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test",
            "report_file": "nccl-test-report.json",
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
    (tmp_path / "gpu-visibility-report.json").write_text(
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

    report = build_deploy_validation_report(validations, inventory_dir=tmp_path)

    assert report.markdown_path == tmp_path / DEPLOY_REPORT_FILENAME
    assert report.overall_status == "incomplete"
    assert report.completed_count == 2
    assert report.passed_count == 2
    assert report.failed_count == 0
    assert report.not_run_count == 1
    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: INCOMPLETE (2/3 completed, 1 not run)",
        "  PASS GPU stack readiness: GPU Operator and Network Operator ready on 2 Ready GPU node(s); RDMA resources rdma/shared_device on 2 Ready GPU node(s); GPUDirect mode dma-buf.",
        "  PASS GPU Visibility test: 2/2 selected node(s) passed; total Ready GPU nodes 4; skipped 2.",
        "  NOT RUN NCCL test: No deploy validation results recorded yet.",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'gpu-stack-readiness-report.json'}",
        f"  JSON detail: {tmp_path / 'gpu-visibility-report.json'}",
    ]
    assert validation_section_lines(report) == [
        "## Validations",
        "",
        "- Overall status: `INCOMPLETE`",
        "- Completed validations: `2/3`",
        "- Passed: `2`",
        "- Failed: `0`",
        "- Not run: `1`",
        "",
        "### GPU stack readiness",
        "",
        "- Status: `PASS`",
        "- Detail report: `gpu-stack-readiness-report.json`",
        "- Summary: GPU Operator and Network Operator ready on 2 Ready GPU node(s); RDMA resources rdma/shared_device on 2 Ready GPU node(s); GPUDirect mode dma-buf.",
        "",
        "### GPU Visibility test",
        "",
        "- Status: `PASS`",
        "- Detail report: `gpu-visibility-report.json`",
        "- Summary: 2/2 selected node(s) passed; total Ready GPU nodes 4; skipped 2.",
        "",
        "### NCCL test",
        "",
        "- Status: `NOT RUN`",
        "- Detail report: `n/a`",
        "- Summary: No deploy validation results recorded yet.",
        "",
    ]


def test_clear_deploy_validation_artifacts_removes_stale_outputs(tmp_path: Path) -> None:
    validations = [
        {"kind": "mk8s_gpu_visibility", "report_file": "gpu-visibility-report.json"},
        {"kind": "mk8s_nccl", "report_file": "nccl-test-report.json"},
    ]
    for name in (
        DEPLOY_REPORT_FILENAME,
        "deploy-validation-report.md",
        "gpu-visibility-report.json",
        "nccl-test-report.json",
    ):
        (tmp_path / name).write_text("data\n", encoding="utf-8")

    clear_deploy_validation_artifacts(validations, inventory_dir=tmp_path)

    assert not (tmp_path / DEPLOY_REPORT_FILENAME).exists()
    assert not (tmp_path / "deploy-validation-report.md").exists()
    assert not (tmp_path / "gpu-visibility-report.json").exists()
    assert not (tmp_path / "nccl-test-report.json").exists()


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

    report = build_deploy_validation_report(validations, inventory_dir=tmp_path)

    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: PASS (1/1 completed, 0 not run)",
        "  PASS NCCL test: Launcher phase Succeeded; Socket/TCPIP average bus bandwidth 41.7 Gbps across 2 worker node(s); RDMA threshold not enforced for this run.",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'nccl-test-report.json'}",
    ]


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

    report = build_deploy_validation_report(validations, inventory_dir=tmp_path)

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

    report = build_deploy_validation_report(validations, inventory_dir=tmp_path)

    assert format_deploy_validation_summary_lines(report) == [
        "Deploy validation summary:",
        "  Overall: FAIL (1/1 completed, 0 not run)",
        "  FAIL NCCL test: Failed before completion: kubectl get pod nccl-test-nebius-launcher timed out after 30 seconds",
        f"  Combined report: {tmp_path / DEPLOY_REPORT_FILENAME}",
        f"  JSON detail: {tmp_path / 'nccl-test-report.json'}",
    ]
