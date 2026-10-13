"""Course onboarding and evidence descriptions match the supplied experiments."""

import importlib.util
import json
import re
import shlex
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
    assert "(../lab-guide.html#lab-preparation-scripts)" in readme
    assert "(VERSIONS.md)" in readme
    versions = text("gpu-fundamentals", "VERSIONS.md")
    assert "PyTorch | 2.14.0 manifest authority" in versions
    assert "qualification pending; no fallback approved" in versions


def test_optimization_timing_distinguishes_intervals_from_active_kernels():
    body = text("gpu-optimizations", "reference/labs/01_timing_basics.md")
    assert "omit host and queueing time" not in body
    assert "idle gaps" in body
    assert "min/median/max" not in body
    assert "host and event timing boundaries" in body


def test_fusion_traffic_is_logical_until_profiled():
    body = text("gpu-optimizations", "reference/labs/03_compile_fusion.md")
    assert "2 GiB of intermediate HBM traffic" not in body
    assert "logical intermediate traffic" in body
    assert "actual HBM traffic" in body


def test_optimization_readme_routes_distributed_practice_to_advanced_owner():
    body = text("gpu-optimizations", "README.md")
    assert (
        "Distributed transport, scaling and profiling practice belongs to"
        in body
    )
    assert "../advanced-gpu-communication/index.html" in body


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
    assert "does not save checkpoints" in body
    assert "held-out predictions and adapter updates" in body


def test_recomputation_matches_work_within_each_lab_not_across_models():
    body = text("llm-training", "reference/labs/14_activation_checkpointing.md")
    assert "matched tiny-transformer paths" in body
    assert "excludes optimizer work and optimizer state" in body


def test_inference_readme_selects_mechanics_interpreter_for_mechanics_job():
    readme = text("llm-inference", "README.md")
    command = re.search(
        r"Example mechanics run:.*?```bash\n(.*?)```", readme, re.DOTALL
    )[1]
    assert "COURSE_PYTHON=" not in command
    assert "source tools/course_env.sh llm-inference.08_kv_cache.ncu --runtime" in text(
        "llm-inference", "slurm/09_hf_prefill_decode.sbatch"
    )
    assert (
        'slurm/09_hf_prefill_decode.sbatch'
        in command
    )
    assert 'sbatch slurm/09_hf_prefill_decode.sbatch' in command
    launcher = text("llm-inference", "slurm/09_hf_prefill_decode.sbatch")
    assert "COURSE_PYTHON" in launcher


def test_distributed_serving_has_a_complete_advanced_owner():
    body = text(
        "advanced-gpu-communication", "reference/labs/24_inference_tensor_parallel.md"
    )
    assert "do not measure live serving TTFT" in body
    serving = text(
        "advanced-gpu-communication", "reference/labs/32_dynamo_disaggregation.md"
    )
    assert "slurm/32_dynamo_disaggregation.sbatch" in serving
    assert "--profiler all" in serving


def test_inference_phase_lab_does_not_promise_unrecorded_client_metrics():
    body = text("llm-inference", "reference/labs/09_hf_prefill_decode.md")
    assert "per-token gaps, total generation time, and stop reason" not in body
    assert "fixed output-token budget" in body
    assert "client-boundary observations" in body


def test_prefill_continuation_recipes_exercise_both_phases():
    lab = "09_hf_prefill_decode"
    guide = text("llm-inference", f"reference/labs/{lab}.md")
    commands = [shlex.split(line) for line in guide.splitlines() if line.startswith("sbatch ")]
    observation = json.loads(text("llm-inference", "reference/observability.json"))["labs"][lab]
    commands.extend(shlex.split(value) for key, value in observation.items() if key.endswith("_command"))
    for relative in ("skills/run-labs", ".agents/skills/run-labs"):
        recipe = json.loads((ROOT / relative / "references/recipes.json").read_text())["labs"][f"llm-inference:{lab}"]
        commands.extend(variant["argv"] for variant in recipe["variants"])
        commands.extend(recipe["profilers"].values())
        commands.extend(run["argv"] for run in recipe["profiling_runs"])
    assert len(commands) == 21
    with load_lab(f"llm-inference/labs/{lab}.py") as module:
        for command in commands:
            tokens = int(command[command.index("--new-tokens") + 1])
            schedule = module.generation_schedule(prompt_tokens=8, new_tokens=tokens)
            assert schedule[0].phase == "prefill"
            assert any(step.phase == "decode" for step in schedule), command


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
        'slurm/12_capstone.sbatch' in body
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
        (["--workload", "small"], 0, "17"),
        (["--workload", "large"], 0, "4096"),
        (["--workload", "h100"], 2, ""),
        (["--smoke"], 2, ""),
        (["--help"], 0, "help"),
        (["-h"], 0, "help"),
        (["--smok"], 2, ""),
        (["--workload", "small", "--workload", "small"], 2, ""),
        (["--workload", "small", "extra"], 2, ""),
        (["--help", "extra"], 2, ""),
        (["--size"], 2, ""),
    ],
)
def test_simple_cuda_flags_fail_before_any_device_work(
    cuda_argument_probe, arguments, status, output
):
    result = subprocess.run(
        [str(cuda_argument_probe), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == status
    assert result.stdout == output
    if status:
        assert (
            "--workload" in result.stderr
            and "small" in result.stderr
            and "large" in result.stderr
        )


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 13])
def test_simple_cuda_entrypoints_validate_flags_before_device_activation(number):
    path = next((ROOT / "custom-cuda-kernels/labs").glob(f"{number:02}_*.cu"))
    main = path.read_text().split("int main(", 1)[1]
    call = (
        "for (int index = 1; index < argc;"
        if number == 1
        else "validate_simple_arguments("
        if number in (9, 10, 13)
        else "problem_size("
    )
    assert main.index(call) < main.index("require_course_gpu(")


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
