"""Shared Hopper admission must never falsify result hardware identity."""

import importlib.util
from types import SimpleNamespace

import pytest
from test_observability_integration import ROOT, load


@pytest.mark.parametrize(
    "name,family",
    [
        ("NVIDIA H100 80GB HBM3", "NVIDIA H100"),
        ("NVIDIA H200", "NVIDIA H200"),
        ("NVIDIA H200 MIG 1g.18gb", None),
        ("NVIDIA H2000", None),
        ("NVIDIA A100", None),
    ],
)
def test_observed_full_hopper_family(name, family):
    evidence = load("course_evidence")
    if family:
        assert evidence.gpu_family(name) == family
    else:
        with pytest.raises(ValueError):
            evidence.gpu_family(name)


COMMONS = [
    "gpu-fundamentals/labs/common.py",
    "gpu-optimizations/labs/common.py",
    "llm-training/labs/common.py",
    "llm-inference/labs/common.py",
    "advanced-gpu-communication/labs/common.py",
    "advanced-gpu-communication/labs/collective_common.py",
    "advanced-gpu-communication/labs/training_common.py",
    "advanced-gpu-communication/labs/inference_common.py",
]


@pytest.mark.parametrize("relative", COMMONS)
@pytest.mark.parametrize("rank,local_rank,world", [(1, 0, 2), (9, 1, 16)])
def test_nccl_first_barrier_uses_local_gpu_not_global_rank(
    monkeypatch, relative, rank, local_rank, world
):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    for name, value in (
        ("RANK", rank),
        ("LOCAL_RANK", local_rank),
        ("WORLD_SIZE", world),
        ("MASTER_ADDR", "127.0.0.1"),
        ("MASTER_PORT", 29500),
    ):
        monkeypatch.setenv(name, str(value))
    spec = importlib.util.spec_from_file_location("nccl_common", ROOT / relative)
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    devices, groups = [], []
    torch = SimpleNamespace(
        cuda=SimpleNamespace(set_device=devices.append),
        device=lambda kind, index: (kind, index),
        distributed=SimpleNamespace(init_process_group=lambda **kw: groups.append(kw)),
    )
    assert common.init_nccl(torch, expected_world_size=world) == (
        rank,
        world,
        local_rank,
    )
    assert devices == [local_rank]
    assert len(groups) == 1
    assert groups[0]["device_id"] == ("cuda", local_rank)


@pytest.mark.parametrize("relative", COMMONS)
def test_actual_course_admission_records_h200_and_rejects_wrong_architecture(
    monkeypatch, relative
):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("hardware_common", ROOT / relative)
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    props = SimpleNamespace(
        name="NVIDIA H200", major=9, minor=0, total_memory=141 * 2**30
    )
    torch = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True,
            current_device=lambda: 0,
            get_device_properties=lambda _: props,
        ),
        __version__="fixture",
        version=SimpleNamespace(cuda="fixture"),
    )
    environment = common.require_course_gpu(torch)
    assert environment["gpu_family"] == "NVIDIA H200"
    assert environment["memory_gib"] == 141
    props.major = 8
    with pytest.raises(SystemExit):
        common.require_course_gpu(torch)


@pytest.mark.parametrize("fault", [None, "mixed", "architecture", "missing"])
def test_fabric_result_identity_requires_every_gpu(monkeypatch, fault):
    evidence = load("course_evidence")
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setenv("SLURM_JOB_NUM_NODES", "2")
    rows = ["NVIDIA H200, 9.0"] * 16
    if fault == "mixed":
        rows[-1] = "NVIDIA H100, 9.0"
    elif fault == "architecture":
        rows[-1] = "NVIDIA H200, 8.0"
    elif fault == "missing":
        rows.pop()
    monkeypatch.setattr(
        evidence.subprocess, "check_output", lambda *a, **kw: "\n".join(rows)
    )
    if fault:
        with pytest.raises(ValueError):
            evidence.allocation_gpu_family()
    else:
        assert evidence.allocation_gpu_family() == "NVIDIA H200"


def test_cuda_report_requires_observed_hardware(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    module = load("cuda_result")
    text = "gpu_name=NVIDIA H200\ngpu_compute_capability=9.0\n"
    assert module.report_gpu_family(text) == "NVIDIA H200"
    for invalid in (
        "",
        text + text,
        text.replace("H200", "A100"),
        text.replace("9.0", "8.0"),
    ):
        with pytest.raises(ValueError):
            module.report_gpu_family(invalid)


def test_health_result_accepts_observed_h200(monkeypatch):
    from test_course_review_fixes import load_lab

    with load_lab("gpu-fundamentals/labs/12_read_only_health.py") as lab:
        monkeypatch.setattr("sys.argv", ["12_read_only_health.py"])
        monkeypatch.setattr(lab, "load_torch", lambda: object())
        monkeypatch.setattr(
            lab, "require_course_gpu", lambda _: {"gpu_family": "NVIDIA H200"}
        )
        monkeypatch.setattr(
            lab,
            "query_nvidia_smi",
            lambda: {"name": "NVIDIA H200", "mig.mode.current": "Disabled"},
        )
        result = {}

        def write(_args, **kwargs):
            result.update(kwargs)
            return "fixture-result"

        monkeypatch.setattr(lab, "write_result", write)
        lab.main()
        assert result["correctness"] == {"single_full_gpu": True, "read_only": True}
