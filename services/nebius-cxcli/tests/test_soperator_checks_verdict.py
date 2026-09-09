"""Native report failures cannot become successful lifecycle acceptance."""

import json

import pytest

from nebius_cxcli.soperator_checks_verdict import (
    OUTPUT_LIMIT,
    health_verdict,
    nccl_verdict,
    read_native_verdict,
)


@pytest.fixture
def report():
    return {
        "status": "PASS",
        "tests": [
            {
                "enable": True,
                "state": {"code": 0, "error": ""},
                "checks": [{"enable": True, "state": {"status": "PASS", "error": ""}}],
            }
        ],
    }


def output(report, status="PASS"):
    return f"Health checker output:\n{json.dumps(report)}\nHealth checker status: {status}\n"


def test_actual_health_pass(report):
    assert health_verdict(output(report))["checks"] == 1


@pytest.fixture
def cuda_report():
    return {
        "status": "PASS",
        "tests": [
            {
                "name": name,
                "cmd": name + " ",
                "enable": True,
                "state": {"code": 0, "error": ""},
                "checks": None,
            }
            for name in ("deviceQuery", "vectorAdd", "simpleMultiGPU", "p2pBandwidthLatencyTest")
        ],
    }


def test_native_cuda_command_only_reports_are_valid(cuda_report):
    verdict = read_native_verdict(
        lambda _: output(cuda_report),
        check="cuda-samples",
        name="native-cuda",
        result={"nodes": ["worker-0"], "jobId": "42", "allocatedGpus": 8},
    )
    assert verdict["status"] == "PASS"
    assert verdict["checks"] == 4


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "duplicate",
        "disabled",
        "wrong-command",
        "failed",
        "error",
        "empty-checks",
        "missing-checks",
        "malformed-name",
    ],
)
def test_cuda_requires_every_exact_native_command(cuda_report, defect):
    test = cuda_report["tests"][0]
    if defect == "missing":
        cuda_report["tests"].pop()
    elif defect == "duplicate":
        cuda_report["tests"][-1] = test.copy()
    elif defect == "disabled":
        test["enable"] = False
    elif defect == "wrong-command":
        test["cmd"] = "true"
    elif defect == "failed":
        test["state"]["code"] = 1
    elif defect == "error":
        test["state"]["error"] = "execution failed"
    elif defect == "missing-checks":
        test.pop("checks")
    elif defect == "malformed-name":
        test["name"] = []
    else:
        test["checks"] = []
    with pytest.raises(RuntimeError, match="native health diagnostic"):
        health_verdict(output(cuda_report), cuda_samples=True)


def test_command_only_report_not_accepted_as_another_diagnostic(cuda_report):
    with pytest.raises(RuntimeError, match="malformed test coverage"):
        health_verdict(output(cuda_report))


def test_extensive_cuda_phase_accepts_native_command_only_report(report, cuda_report):
    phases = (
        "passive_checks",
        "all_reduce_with_ib",
        "all_reduce_without_ib",
        "cuda_samples",
        "gpu_fryer",
        "mem_perf",
    )
    value = "".join(
        f"Start health-checker run '{phase}' on worker-0...\n"
        + output(cuda_report if phase == "cuda_samples" else report).replace(
            "Health checker status: PASS", "Health checker finished with status 'PASS'"
        )
        for phase in phases
    )
    assert health_verdict(value, extensive=True)["checks"] == 9


def test_extensive_requires_all_six_phases(report, cuda_report):
    phases = [
        "passive_checks",
        "all_reduce_with_ib",
        "all_reduce_without_ib",
        "cuda_samples",
        "gpu_fryer",
        "mem_perf",
    ]
    value = ""
    for phase in phases:
        value += f"Start health-checker run '{phase}' on worker-0...\n"
        value += output(cuda_report if phase == "cuda_samples" else report).replace(
            "Health checker status: PASS", "Health checker finished with status 'PASS'"
        )
    assert health_verdict(value, extensive=True)["reports"] == 6
    with pytest.raises(RuntimeError, match="phase coverage"):
        health_verdict(value.split("Start health-checker run 'mem_perf'")[0], extensive=True)


@pytest.mark.parametrize("status", ["ERROR", "FAIL", "EMPTY", "WARNING", "", "null"])
def test_every_nonpass_status_rejected(report, status):
    report["status"] = status
    with pytest.raises(RuntimeError, match="health diagnostic reported"):
        health_verdict(output(report, status))


@pytest.mark.parametrize(
    "defect", ["no-tests", "disabled", "nonzero", "bool-code", "error", "check-error", "no-checks"]
)
def test_top_level_pass_does_not_mask_failed_or_missing_test(report, defect):
    test = report["tests"][0]
    if defect == "no-tests":
        report["tests"] = []
    elif defect == "disabled":
        test["enable"] = False
    elif defect == "nonzero":
        test["state"]["code"] = 1
    elif defect == "bool-code":
        test["state"]["code"] = False
    elif defect == "error":
        test["state"]["error"] = "affinity unavailable"
    elif defect == "check-error":
        test["checks"][0]["state"]["status"] = "ERROR"
    else:
        test["checks"] = []
    with pytest.raises(RuntimeError, match="native health diagnostic"):
        health_verdict(output(report))


@pytest.mark.parametrize("defect", ["missing", "malformed", "duplicate", "status-missing"])
def test_report_must_be_unambiguous(report, defect):
    value = output(report)
    if defect == "missing":
        value = "Unsupported platform\n"
    elif defect == "malformed":
        value = value.replace('{"status"', '{invalid "status"')
    elif defect == "duplicate":
        value += value
    else:
        value = value.replace("Health checker status: PASS", "")
    with pytest.raises(RuntimeError, match="native health diagnostic report"):
        health_verdict(value)


@pytest.fixture
def nccl():
    return (
        "# Collective test starting: all_reduce_perf\n"
        "# nThread 1 nGpus 8 minBytes 536870912 maxBytes 8589934592 "
        "step: 2(factor) warmup iters: 1 iters: 20 agg iters: 1 validation: 1 graph: 0\n"
        "# Out of bounds values : 0 OK\n"
        "# Avg bus bandwidth    : 48.4017\n"
        "# Collective test concluded: all_reduce_perf\n"
    )


def test_completed_nccl_validates_gpu_count_and_results(nccl):
    assert nccl_verdict(nccl, gpu_count=8)["status"] == "PASS"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("nGpus 8", "nGpus 4"),
        ("validation: 1", "validation: 0"),
        ("0 OK", "2 FAILED"),
        ("48.4017", "0"),
        ("48.4017", "nan"),
        ("Collective test concluded:", "incomplete:"),
    ],
)
def test_invalid_nccl_rejected(nccl, before, after):
    with pytest.raises(RuntimeError, match="native NCCL diagnostic"):
        nccl_verdict(nccl.replace(before, after), gpu_count=8)


def test_reader_uses_exact_upstream_output_path_and_records_digest(report):
    commands = []

    def read(command):
        commands.append(command)
        return output(report)

    result = read_native_verdict(
        read,
        check="gpu-fryer",
        name="check-123",
        result={"nodes": ["worker-0"], "jobId": "42", "allocatedGpus": 8},
    )
    assert commands == [
        "head -c 4194305 -- /opt/soperator-outputs/slurm_jobs/worker-0.check-123.42.out"
    ]
    assert result["outputSha256"].startswith("sha256:")
    assert "output" not in result


def test_oversized_report_rejected():
    with pytest.raises(RuntimeError, match="verification limit"):
        read_native_verdict(
            lambda _: "a" * (OUTPUT_LIMIT + 1),
            check="gpu-fryer",
            name="check-123",
            result={"nodes": ["worker-0"], "jobId": "42", "allocatedGpus": 8},
        )


def test_unsafe_identity_rejected_before_output_read():
    def no_read(_):
        pytest.fail("unsafe path reached the transport")

    with pytest.raises(RuntimeError, match="identity"):
        read_native_verdict(
            no_read,
            check="gpu-fryer",
            name="check-123; id",
            result={"nodes": ["worker-0"], "jobId": "42", "allocatedGpus": 8},
        )
