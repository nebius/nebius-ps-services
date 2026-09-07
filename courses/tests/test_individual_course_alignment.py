"""Course onboarding and evidence descriptions match the supplied experiments."""

import re
import importlib.util
import shutil
import subprocess

import pytest

from test_course_content_contract import ROOT
from test_course_review_fixes import load_lab


def text(course, relative):
    return (ROOT / course / relative).read_text()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Enabled", "MIG enabled"),
        (" disabled ", "MIG disabled; verify allocated-device identity in preflight"),
        ("N/A", "MIG mode unavailable or unrecognized"),
        ("", "MIG mode unavailable or unrecognized"),
        ("not enabled", "MIG mode unavailable or unrecognized"),
        ("[Not Supported]", "MIG mode unavailable or unrecognized"),
    ],
)
def test_health_query_never_infers_full_gpu_from_missing_mig_state(value, expected):
    with load_lab("gpu-fundamentals/labs/12_read_only_health.py") as lab:
        assert lab.describe_mig_mode(value) == expected


def test_fundamentals_onboarding_is_reproducible_not_workspace_history():
    readme = text("gpu-fundamentals", "README.md")
    assert "Existing local 2.13 files" not in readme
    assert "PyTorch 2.14" in readme
    assert "qualification remain pending" in readme


def test_optimization_timing_distinguishes_intervals_from_active_kernels():
    body = text("gpu-optimizations", "reference/labs/01_timing_basics.md")
    assert "omit host and queueing time" not in body
    assert "idle gaps" in body
    assert "outside the marker interval" in body
    assert "Lab 02 reports medians only" in body
    assert "raw-sample export" in body
    assert "min/median/max" not in body
    assert "host-time medians and CUDA-event min/median/p90" in body


def test_fusion_traffic_is_logical_until_profiled():
    body = text("gpu-optimizations", "reference/labs/03_compile_fusion.md")
    assert "2 GiB of intermediate HBM traffic" not in body
    assert "2 GiB of logical intermediate traffic" in body
    assert "actual HBM traffic" in body


def test_optimization_readme_includes_two_node_preflight():
    body = text("gpu-optimizations", "README.md")
    assert "preflight, the Lab 17 PyTorch communication sweep" in body
    assert "slurm/nccl_tests.sbatch" in body
    assert "MPI" in body


def test_training_summaries_keep_actual_validation_and_mfu_scope():
    runbook = text("llm-training", "reference/cluster-smoke-test.md")
    syllabus = text("llm-training", "SYLLABUS.md")
    assert "full-parameter update deltas" not in runbook
    assert "tracked-parameter update delta" in runbook
    assert "matmul-only" in runbook and "matmul-only" in syllabus
    assert "not full-model MFU" in runbook
    versions = text("llm-training", "VERSIONS.md")
    assert "Installed fallback baseline available" not in versions
    assert "joint compatibility pending" in versions


def test_lora_saved_artifacts_are_an_explicit_extension():
    body = text("llm-training", "reference/labs/05_lora_sft.md")
    assert "checkpoint saving is not implemented" in body
    assert "save/reload equivalence" in body
    assert "held_out_next_token_before/after" in body
    assert "held_out_logits_digest_before/after" in body


def test_recomputation_matches_work_within_each_lab_not_across_models():
    body = text("llm-training", "reference/labs/14_activation_checkpointing.md")
    assert "matched work within each lab" in body
    assert "Lab 14 does not execute an optimizer update" in body


def test_inference_readme_selects_mechanics_interpreter_for_mechanics_job():
    readme = text("llm-inference", "README.md")
    command = re.search(r"Example mechanics run:.*?```bash\n(.*?)```", readme, re.S)[1]
    assert 'COURSE_PYTHON="$PWD/.venv-mechanics/bin/python"' in command
    assert "sbatch slurm/single_gpu.sbatch labs/09_hf_prefill_decode.py" in command
    launcher = text("llm-inference", "slurm/single_gpu.sbatch")
    assert "COURSE_PYTHON" in launcher


def test_inference_pipeline_parallelism_is_conditional_live_not_topology_only():
    body = text("llm-inference", "reference/labs/19_tensor_parallel_linear.md")
    assert "PP is only a topology comparison" not in body
    assert "conditional advanced `pp` mode" in body


def test_inference_phase_lab_does_not_promise_unrecorded_client_metrics():
    body = text("llm-inference", "reference/labs/09_hf_prefill_decode.md")
    assert "per-token gaps, total generation time, and stop reason" not in body
    assert "fixed output-token budget" in body
    assert "client-boundary observations" in body


def test_cluster_launch_dimension_uses_runtime_struct_members():
    source = text("custom-cuda-kernels", "labs/10_hopper_cluster.cu")
    assert "attribute.val.clusterDim = dim3" not in source
    for member, value in (("x", 2), ("y", 1), ("z", 1)):
        assert f"attribute.val.clusterDim.{member} = {value};" in source


def test_default_sm90_is_selected_before_cuda_compiler_initialization():
    cmake = text("custom-cuda-kernels", "CMakeLists.txt")
    assert cmake.index("set(CMAKE_CUDA_ARCHITECTURES 90)") < cmake.index("project(")
    assert "if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)" in cmake


def test_custom_capstone_uses_completed_build_directory():
    body = text("custom-cuda-kernels", "reference/labs/12_capstone.md")
    assert "slurm/capstone_three_trials.sbatch build/12_capstone" not in body
    assert (
        '"${COURSE_BUILD_DIR:?set the completed build directory}/12_capstone"' in body
    )


@pytest.fixture(scope="module")
def cuda_argument_probe(tmp_path_factory):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler unavailable; argument runtime test pending")
    directory = tmp_path_factory.mktemp("course-arguments")
    source = directory / "probe.cpp"
    source.write_text(
        '#include "arguments.hpp"\n#include <iostream>\n'
        "int main(int argc, char** argv) {\n"
        '  if (wants_help(argc, argv)) { std::cout << "help"; return 0; }\n'
        "  try { std::cout << problem_size(argc, argv, 17, 4096); return 0; }\n"
        "  catch (const std::exception& e) { std::cerr << e.what(); return 2; }\n"
        "}\n"
    )
    binary = directory / "probe"
    subprocess.run(
        [
            compiler,
            "-std=c++20",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(ROOT / "custom-cuda-kernels/labs"),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


@pytest.mark.parametrize(
    ("arguments", "status", "output"),
    [
        ([], 0, "4096"),
        (["--smoke"], 0, "17"),
        (["--help"], 0, "help"),
        (["-h"], 0, "help"),
        (["--smok"], 2, ""),
        (["--smoke", "--smoke"], 2, ""),
        (["--smoke", "extra"], 2, ""),
        (["--help", "extra"], 2, ""),
        (["--size"], 2, ""),
    ],
)
def test_simple_cuda_flags_fail_before_any_device_work(
    cuda_argument_probe, arguments, status, output
):
    result = subprocess.run(
        [str(cuda_argument_probe), *arguments], capture_output=True, text=True
    )
    assert result.returncode == status
    assert result.stdout == output
    if status:
        assert "expected no arguments or --smoke" in result.stderr


@pytest.mark.parametrize("number", [0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11])
def test_simple_cuda_entrypoints_validate_flags_before_device_activation(number):
    path = next((ROOT / "custom-cuda-kernels/labs").glob(f"{number:02}_*.cu"))
    main = path.read_text().split("int main(", 1)[1]
    call = "validate_simple_arguments(" if number in (0, 9, 10) else "problem_size("
    assert main.index(call) < main.index("require_h100(")


def test_publication_safety_scans_cpp_headers(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "header_safety_validator", ROOT / "tools/validate_course_template.py"
    )
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    header = tmp_path / "arguments.hpp"
    header.write_text("// Public portable argument validation\n")
    validator.validate_publication_text([header])
    header.write_text("// private path: /" + "Users/example/private-file\n")
    with pytest.raises(SystemExit, match="publication-safety pattern"):
        validator.validate_publication_text([header])
