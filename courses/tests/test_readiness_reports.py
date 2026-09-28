"""A readable report or installed version alone cannot establish readiness."""

import pytest
from test_observability_integration import ROOT, load


def readiness(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    return load("readiness")


def test_installed_versions_are_recorded_without_candidate_equality(monkeypatch):
    module = readiness(monkeypatch)
    assert (
        module.profiler_version(
            "nsys", "NVIDIA Nsight Systems version 2026.4.1.191-abcdef"
        )
        == "2026.4.1.191"
    )
    assert (
        module.profiler_version(
            "ncu",
            "NVIDIA (R) Nsight Compute Command Line Profiler\nVersion 2026.2.1.0 (build 123)",
        )
        == "2026.2.1.0"
    )
    for tool in ("nsys", "ncu"):
        with pytest.raises(ValueError, match="identify"):
            module.profiler_version(tool, "error: expected version 2026.5.1")


@pytest.mark.parametrize(
    "defect", [None, "kernel", "counter", "empty", "extra", "range", "warmup", "row"]
)
@pytest.mark.parametrize("warnings", [False, True])
def test_matching_kernel_counter_and_nvtx_content_required(
    monkeypatch, defect, warnings
):
    module = readiness(monkeypatch)
    kernels = "Instances,Name\n6,_Z12ready_kernelPf\n"
    ranges = "Instances,Range\n1,:ready_kernel\n"
    compute = '"ID","Kernel Name","gpu__time_duration.sum"\n"","","us"\n"0","_Z12ready_kernelPf","1.5"\n'
    if defect == "kernel":
        compute = compute.replace("ready_kernel", "other_kernel")
    elif defect == "counter":
        compute = compute.replace('"1.5"', '"nan"')
    elif defect == "empty":
        compute = "No kernels profiled\n"
    elif defect == "extra":
        compute += '"1","_Z12ready_kernelPf","1.5"\n'
    elif defect == "range":
        ranges = ranges.replace("ready_kernel", "wrong_range")
    elif defect == "warmup":
        kernels = kernels.replace("6,", "1,")
    elif defect == "row":
        compute += '"1","_Z12ready_kernelPf"\n'
    if warnings:
        compute = (
            "==WARNING== Could not deploy stock section files to the documents directory.\n"
            "==WARNING== Using installed sections instead.\n" + compute
        )
    if defect:
        with pytest.raises(ValueError):
            module.verify_report_content(kernels, ranges, compute, "cuda")
    else:
        assert (
            module.verify_report_content(kernels, ranges, compute, "cuda")
            == "_Z12ready_kernelPf"
        )
