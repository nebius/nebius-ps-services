"""Behavioral and ownership tests for the five-course redesign."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COURSES = (
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
)
PYTHON_COURSES = COURSES[:-1]


def publication_files(course: Path, suffixes: set[str]):
    """Select publication files without descending into excluded environments."""
    for parent, directories, filenames in os.walk(course):
        directories[:] = [name for name in directories if not name.startswith(".venv")]
        for name in filenames:
            path = Path(parent) / name
            if (
                path.suffix in suffixes
                and not any(part.startswith(".venv") for part in path.parts)
                and path.is_file()
            ):
                yield path


@contextmanager
def load_lab(relative: str):
    path = ROOT / relative
    module_name = f"course_test_{path.stem}_{id(path)}"
    old_module = sys.modules.pop(module_name, None)
    old_common = sys.modules.pop("common", None)
    old_tiny = sys.modules.pop("tiny_lm", None)
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(module_name, None)
        if old_module is not None:
            sys.modules[module_name] = old_module
        sys.path.remove(str(path.parent))
        sys.modules.pop("common", None)
        sys.modules.pop("tiny_lm", None)
        if old_common is not None:
            sys.modules["common"] = old_common
        if old_tiny is not None:
            sys.modules["tiny_lm"] = old_tiny


def test_exact_five_course_roots_and_retired_combined_path() -> None:
    assert [name for name in COURSES if (ROOT / name).is_dir()] == list(COURSES)
    assert not (ROOT / "llm-training-inferencing").exists()


@pytest.mark.parametrize("course", COURSES)
def test_standalone_artifact_contract(course: str) -> None:
    root = ROOT / course
    for relative in (
        "MISSION.md",
        "COURSE.md",
        "SYLLABUS.md",
        "README.md",
        "GLOSSARY.md",
        "RESOURCES.md",
        "VERSIONS.md",
        "PUBLICATION-REVIEW.md",
        "index.html",
        "reference/course.json",
        "reference/visual-manifest.json",
        "reference/visual-plan.md",
        "reference/benchmark-record.md",
        "reference/cluster-smoke-test.md",
        "reference/evidence-security.md",
        "tools/validate_course.py",
    ):
        assert (root / relative).is_file(), f"{course}/{relative} is missing"


@pytest.mark.parametrize("course", PYTHON_COURSES)
def test_common_result_writer_is_private_and_no_clobber(course: str) -> None:
    with load_lab(f"{course}/labs/common.py") as common:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "result.json"
            common.write_json_exclusive(target, {"schema": "gpu-course-result/v1"})
            assert target.stat().st_mode & 0o777 == 0o600
            with pytest.raises(SystemExit, match="overwrite"):
                common.write_json_exclusive(target, {"schema": "gpu-course-result/v1"})


@pytest.mark.parametrize("course", PYTHON_COURSES)
def test_python_lab_help_is_dependency_free(course: str) -> None:
    for lab in sorted((ROOT / course / "labs").glob("[0-9][0-9]_*.py")):
        result = subprocess.run(
            [sys.executable, str(lab), "--help"],
            cwd=lab.parent.parent,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert result.returncode == 0, f"{lab}: {result.stderr}"
        assert "usage:" in result.stdout.lower()


def test_domain_specific_lab_ownership() -> None:
    assert not (ROOT / "gpu-optimizations/labs/06_activation_checkpointing.py").exists()
    assert not (ROOT / "gpu-optimizations/labs/11_sdpa_attention.py").exists()
    assert (ROOT / "llm-training/labs/14_activation_checkpointing.py").is_file()
    assert (ROOT / "llm-inference/labs/24_sdpa_attention.py").is_file()
    training = (ROOT / "llm-training/labs/12_moe_expert_parallel.py").read_text()
    inference = (ROOT / "llm-inference/labs/12_moe_expert_parallel.py").read_text()
    assert "training_evidence" in training
    assert "inference_evidence" in inference
    for lab in (ROOT / "llm-inference/labs").glob("*.py"):
        content = lab.read_text().lower()
        assert "training environment" not in content, lab
        assert "training extras" not in content, lab


def test_random_using_python_labs_apply_the_declared_seed() -> None:
    random_call = re.compile(r"torch\.(?:randn|randint|rand\(|rand_like|randn_like)")
    for course in PYTHON_COURSES:
        for lab in (ROOT / course / "labs").glob("[0-9][0-9]_*.py"):
            content = lab.read_text()
            if random_call.search(content):
                assert (
                    "seed_everything(torch, args.seed)" in content
                    or "manual_seed(args.seed)" in content
                    or "torch.Generator" in content
                ), lab


def test_serving_launchers_belong_only_to_inference() -> None:
    expected = {
        "vllm_offline.sbatch",
        "vllm_benchmark.sbatch",
        "vllm_chunked_prefill_ab.sbatch",
        "vllm_prefix_cache.sbatch",
        "vllm_speculative_ab.sbatch",
        "vllm_streaming_benchmark.sbatch",
        "vllm_two_node.sbatch",
        "trtllm_triton.sbatch",
        "aiperf.sbatch",
        "dynamo_disaggregated_preflight.sbatch",
    }
    actual = {path.name for path in (ROOT / "llm-inference/slurm").glob("*.sbatch")}
    assert expected <= actual
    assert not any((ROOT / "llm-training/slurm").glob("vllm*.sbatch"))


@pytest.mark.parametrize("course", COURSES)
def test_slurm_launchers_are_private_fail_closed(course: str) -> None:
    launchers = sorted((ROOT / course / "slurm").glob("*.sbatch"))
    assert launchers
    for launcher in launchers:
        text = launcher.read_text()
        assert "set -euo pipefail" in text
        assert "umask 077" in text
        assert "#SBATCH --gpus-per-node=1" in text
        syntax = subprocess.run(
            ["bash", "-n", str(launcher)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert syntax.returncode == 0, f"{launcher}: {syntax.stderr}"


def test_custom_cuda_build_contract() -> None:
    cmake = (ROOT / "custom-cuda-kernels/CMakeLists.txt").read_text()
    assert "CMAKE_CUDA_STANDARD 20" in cmake
    assert "CMAKE_CUDA_ARCHITECTURES 90" in cmake
    assert "COURSE_ENABLE_SM90A" in cmake
    assert "COURSE_ENABLE_CUTLASS" in cmake
    assert (
        'option(COURSE_ENABLE_CUTLASS "Build required Lab 09 with the pinned '
        'CUTLASS fused epilogue" ON)' in cmake
    )
    assert "CUTLASS_ROOT" in cmake
    assert "--resource-usage>" in cmake
    assert "--expt-relaxed-constexpr --resource-usage" not in cmake
    sources = sorted((ROOT / "custom-cuda-kernels/labs").glob("[0-9][0-9]_*.cu"))
    assert len(sources) == 13
    assert all('"common.cuh"' in source.read_text() for source in sources)
    common = (ROOT / "custom-cuda-kernels/labs/common.cuh").read_text()
    assert "benchmark_cuda" in common
    assert "median_ms" in common and "p90_ms" in common
    assert "std::isfinite(expected[index])" in common
    assert "std::isfinite(observed[index])" in common
    assert "tiled_unpadded_transpose" in sources[3].read_text()
    reduction = (ROOT / "custom-cuda-kernels/labs/04_reduction.cu").read_text()
    assert "block_atomic_sum" in reduction and "cub::DeviceReduce" in reduction
    resource = (ROOT / "custom-cuda-kernels/labs/07_resource_sweep.cu").read_text()
    assert "registers_per_thread" in resource and "local_bytes_per_thread" in resource
    assert "check_close({expected, expected}, observed)" in resource
    epilogue = (ROOT / "custom-cuda-kernels/labs/09_library_epilogue.cu").read_text()
    assert "CUTLASS_MAJOR == 4" in epilogue
    assert "CutlassEpilogue" in epilogue
    assert "cutlass_fused_correctness=passed" in epilogue
    assert "return 3;" in epilogue
    build_launcher = (
        ROOT / "custom-cuda-kernels/slurm/build_and_test.sbatch"
    ).read_text()
    assert "-DCOURSE_ENABLE_CUTLASS=ON" in build_launcher
    assert '-DCUTLASS_ROOT="${CUTLASS_ROOT}"' in build_launcher
    divergence = (ROOT / "custom-cuda-kernels/labs/06_divergence_tail.cu").read_text()
    assert "pack_grouped" in divergence and "scatter_grouped" in divergence
    assert "check_close(divergent_observed, grouped_observed)" in divergence
    assert "primary_grouped_timing_includes_pack_and_scatter=true" in divergence


def test_deep_training_and_serving_evidence_is_runnable() -> None:
    fundamentals_tail = (
        ROOT / "gpu-fundamentals/labs/11_scheduler_tail.py"
    ).read_text()
    optimization_tail = (
        ROOT / "gpu-optimizations/labs/15_tail_load_balance.py"
    ).read_text()
    for tail_lab in (fundamentals_tail, optimization_tail):
        assert 'fill_(float("nan"))' in tail_lab
        assert "tail_reference" in tail_lab
        assert "rtol=1e-5, atol=1e-6" in tail_lab
    profiler_cases = (
        ROOT / "gpu-optimizations/labs/14_profiler_bottlenecks.py"
    ).read_text()
    assert "input_scale = math.sqrt(width)" in profiler_cases
    assert "relative_l2_error" in profiler_cases
    assert "rtol=1e-2, atol=1e-2" in profiler_cases
    packing = (ROOT / "llm-training/labs/25_sequence_packing.py").read_text()
    assert "boundary_mask[start, prior_end - 1]" in packing
    assert "within_example_causal_controls_pass" in packing
    pipeline = (ROOT / "llm-training/labs/26_input_pipeline.py").read_text()
    assert "torch.utils.data.DataLoader" in pipeline
    assert 'options["prefetch_factor"]' in pipeline
    assert "sample_order_and_content_digest" in pipeline
    training_tp = (ROOT / "llm-training/labs/19_tensor_parallel_linear.py").read_text()
    assert "input_gradient_all_reduce_median_ms" in training_tp
    assert "optimizer_update_matches_reference" in training_tp
    training_ep = (ROOT / "llm-training/labs/12_moe_expert_parallel.py").read_text()
    assert "expert_weight_gradient_matches_reference" in training_ep
    assert "training_step_slowest_rank_median_ms" in training_ep
    checkpoint = (ROOT / "llm-training/labs/24_checkpoint_resume.py").read_text()
    assert "rng_generated_next_batch_exact" in checkpoint
    assert "resumed_inputs" in checkpoint and "resumed_labels" in checkpoint
    assert "torch.randint" in checkpoint
    assert "omitted_rng_restore_diverges" in checkpoint
    overlap = (ROOT / "llm-training/labs/28_communication_overlap.py").read_text()
    assert (
        "register_post_accumulate_grad_hook" in overlap and "async_op=True" in overlap
    )
    assert '"ascending"' in overlap and '"descending"' in overlap

    aiperf = (ROOT / "llm-inference/slurm/aiperf.sbatch").read_text()
    assert "vllm serve" in aiperf and "--streaming" in aiperf
    assert "--request-count 24" in aiperf and "--output-artifact-dir" in aiperf
    chunked = (ROOT / "llm-inference/slurm/vllm_chunked_prefill_ab.sbatch").read_text()
    assert "--no-enable-chunked-prefill" in chunked
    assert "--enable-chunked-prefill" in chunked
    assert "for trial in 1 2 3" in chunked
    assert 'run_trial disabled "${trial}"' in chunked
    assert 'run_trial enabled "${trial}"' in chunked
    prefix = (ROOT / "llm-inference/slurm/vllm_prefix_cache.sbatch").read_text()
    assert "for trial in 1 2 3" in prefix
    speculative = (ROOT / "llm-inference/slurm/vllm_speculative_ab.sbatch").read_text()
    assert "--speculative-config" in speculative
    assert "compare_pair" in speculative
    assert "for trial in 1 2 3" in speculative
    serving_parallel = (ROOT / "llm-inference/slurm/vllm_two_node.sbatch").read_text()
    assert "pp | tp | ep" in serving_parallel
    assert "--enable-expert-parallel" in serving_parallel
    assert "for trial in 1 2 3" in serving_parallel
    assert 'start_server "${trial}"' in serving_parallel
    inference_tp = (
        ROOT / "llm-inference/labs/19_tensor_parallel_linear.py"
    ).read_text()
    assert "broadcast(weight, src=0)" in inference_tp
    assert "column_parallel_matches_reference" in inference_tp
    assert "row_parallel_matches_reference" in inference_tp
    inference_ep = (ROOT / "llm-inference/labs/12_moe_expert_parallel.py").read_text()
    assert "global_expert_token_load" in inference_ep
    assert "expert_load_max_to_mean" in inference_ep
    training_capstone = (
        ROOT / "llm-training/slurm/capstone_three_trials.sbatch"
    ).read_text()
    inference_capstone = (
        ROOT / "llm-inference/slurm/capstone_three_trials.sbatch"
    ).read_text()
    for capstone in (training_capstone, inference_capstone):
        assert "for trial in 1 2 3" in capstone
        assert "candidate-first" in capstone
        assert '--output-dir "${campaign_dir}"' in capstone
        assert 'mkdir -m 700 -- "${campaign_dir}"' in capstone
        parent_create = capstone.index('mkdir -p -- "${course_dir}/results"')
        leaf_create = capstone.index('mkdir -m 700 -- "${campaign_dir}"')
        assert parent_create < leaf_create
        assert 'chmod 700 -- "${course_dir}/results"' in capstone
    triton = (ROOT / "llm-inference/slurm/trtllm_triton.sbatch").read_text()
    assert "TRITON_REPOSITORY_PROFILE" in triton
    assert "llmapi" in triton and "inflight_batcher" in triton
    assert "config.pbtxt" in triton
    engine_profile = (ROOT / "llm-inference/labs/30_engine_profile.py").read_text()
    assert "supported " in engine_profile and "course contract" in engine_profile


def test_aiperf_execution_is_inference_owned() -> None:
    for course in (
        "gpu-fundamentals",
        "gpu-optimizations",
        "llm-training",
        "custom-cuda-kernels",
    ):
        for path in publication_files(ROOT / course, {".py", ".sh", ".sbatch"}):
            assert "aiperf" not in path.read_text(errors="ignore").lower(), path


def test_custom_sanitizer_launcher_allowlists_tools() -> None:
    sanitizer = (ROOT / "custom-cuda-kernels/slurm/sanitizer.sbatch").read_text()
    assert "memcheck | racecheck | initcheck | synccheck" in sanitizer
    assert '--tool "${tool}"' in sanitizer


def test_engine_profiles_require_immutable_digests() -> None:
    for launcher in (
        "vllm_offline.sbatch",
        "vllm_benchmark.sbatch",
        "vllm_chunked_prefill_ab.sbatch",
        "vllm_prefix_cache.sbatch",
        "vllm_speculative_ab.sbatch",
        "vllm_streaming_benchmark.sbatch",
        "vllm_two_node.sbatch",
        "trtllm_triton.sbatch",
        "aiperf.sbatch",
        "dynamo_disaggregated_preflight.sbatch",
    ):
        text = (ROOT / "llm-inference/slurm" / launcher).read_text()
        assert "@sha256:" in text
        assert "latest" not in text.lower()
        assert "COURSE_CONTAINER_RUNNER" in text
        assert "IMAGE_DIGEST}" in text


def test_hugging_face_revisions_fail_closed() -> None:
    with load_lab("llm-inference/labs/common.py") as common:
        common.require_hf_commit_revision("a" * 40)
        with pytest.raises(SystemExit, match="immutable 40-character"):
            common.require_hf_commit_revision("main")
    with load_lab("llm-training/labs/common.py") as common:
        common.require_hf_commit_revision("b" * 40)
        with pytest.raises(SystemExit, match="immutable 40-character"):
            common.require_hf_commit_revision("main")
    for launcher in (
        "aiperf.sbatch",
        "vllm_benchmark.sbatch",
        "vllm_chunked_prefill_ab.sbatch",
        "vllm_prefix_cache.sbatch",
        "vllm_speculative_ab.sbatch",
        "vllm_streaming_benchmark.sbatch",
        "vllm_two_node.sbatch",
    ):
        text = (ROOT / "llm-inference/slurm" / launcher).read_text()
        assert "40-character Hugging Face commit ID" in text


@pytest.mark.parametrize(
    ("course", "lab_id", "correctness_key", "baseline_key", "candidate_key"),
    (
        (
            "llm-training",
            "31_training_capstone",
            "full_update_close",
            "baseline",
            "candidate",
        ),
        (
            "llm-inference",
            "32_inference_capstone",
            "output_allclose",
            "materialized",
            "sdpa",
        ),
    ),
)
def test_capstone_aggregator_requires_three_distinct_counterbalanced_runs(
    course: str,
    lab_id: str,
    correctness_key: str,
    baseline_key: str,
    candidate_key: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = []
        for index, order in enumerate(
            ("baseline-first", "candidate-first", "baseline-first"), start=1
        ):
            path = root / f"trial-{index}.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "gpu-course-result/v1",
                        "lab_id": lab_id,
                        "profile": "smoke",
                        "run_id": f"{index:012x}",
                        "seed": 16 + index,
                        "environment": {
                            "gpu_family": "NVIDIA H100",
                            "torch_version": "test",
                            "cuda_version": "test",
                        },
                        "measurements": {
                            "variant_order": order,
                            "shape": [512, 256],
                            "isl": 256,
                            "osl": 1,
                            "concurrency": 1,
                            "warmup": 3,
                            "iterations": 10,
                            "peak_tflops_reference": None,
                            "timing": {
                                baseline_key: {"median_ms": 2.0},
                                candidate_key: {"median_ms": 1.0},
                            },
                        },
                        "correctness": {correctness_key: True},
                    }
                )
            )
            inputs.append(path)
        output = root / "summary.json"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / course / "tools/aggregate_capstone.py"),
                "--input",
                *(str(path) for path in inputs),
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        summary = json.loads(output.read_text())
        assert summary["decision"] == "candidate-for-scoped-keep"
        assert summary["correctness"]["counterbalanced_order"] is True
        mismatched = json.loads(inputs[-1].read_text())
        mismatched["profile"] = "h100"
        inputs[-1].write_text(json.dumps(mismatched))
        rejected = subprocess.run(
            [
                sys.executable,
                str(ROOT / course / "tools/aggregate_capstone.py"),
                "--input",
                *(str(path) for path in inputs),
                "--output",
                str(root / "rejected.json"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert rejected.returncode != 0
        assert "differ" in rejected.stderr
