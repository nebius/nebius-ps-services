"""Guard corrected worked examples and honest runnable-versus-extension scope.

Arithmetic checks validate teaching examples, not GPU or distributed execution.
"""

import math

import pytest

from test_course_content_contract import lab_section, ROOT, load_builder


def lesson(course: str, title: str) -> dict[str, str]:
    return next(
        item
        for item in load_builder().parse_course(ROOT / course / "COURSE.md")[2]
        if item["title"] == title
    )


def test_roofline_bound_is_independent_of_achieved_performance() -> None:
    example = lab_section("gpu-fundamentals", 5, "Practice")
    assert "does not depend on the starting measured performance" in example
    operations, original_bytes, bandwidth = 2e9, 20e9, 2e12
    original_roof = bandwidth * operations / original_bytes / 1e12
    reduced_traffic_roof = bandwidth * operations / (original_bytes / 2) / 1e12
    assert original_roof == pytest.approx(0.2)
    assert reduced_traffic_roof == pytest.approx(0.4)
    assert "0.2 to 0.4 TOP/s" in example


def test_divergence_regrouping_preserves_all_tasks() -> None:
    example = lab_section("gpu-fundamentals", 11, "Practice")
    assert "64" in example and "48" in example and "24" in example
    # Two mixed warps each contain 16 long and 16 short tasks. Regrouping
    # produces one full long warp and one full short warp, not extra work.
    before = (2 * 16, 2 * 16)
    after = (32, 32)
    assert before == after
    long_path, short_path = 16, 8
    assert 2 * (long_path + short_path) == 48
    assert long_path + short_path == 24


def test_token_weighting_distinguishes_batch_and_per_token_weights() -> None:
    example = lab_section("llm-training", 13, "Practice")
    assert "twice its intended batch contribution" in example
    assert "three times the weight of a token" in example
    short_tokens, long_tokens = 100, 300
    assert 0.5 / (short_tokens / (short_tokens + long_tokens)) == 2
    assert (0.5 / short_tokens) / (0.5 / long_tokens) == 3
    assert (-math.log(0.7) - math.log(0.2)) / 2 == pytest.approx(0.9831, abs=5e-5)


def test_ddp_normalization_derivation_matches_global_token_mean() -> None:
    mechanism = lesson("llm-training", "The parameter-update lifecycle")["How it works"]
    assert "R × local_loss_sum / global_valid_tokens" in mechanism
    assert "entire intended accumulation window" in mechanism
    # Unequal ranks and unequal accumulation microbatches; these are scalar
    # derivative sums, so differentiating a weighted loss uses the same algebra.
    counts = ((40, 60), (120, 180))
    gradients = ((20.0, 100.0), (-60.0, 240.0))
    global_count = sum(sum(rank) for rank in counts)
    ranks = len(counts)
    ddp_average = sum(ranks * sum(rank) / global_count for rank in gradients) / ranks
    reference = sum(sum(rank) for rank in gradients) / global_count
    naive_rank_mean = (
        sum(
            sum(rank_grad) / sum(rank_count)
            for rank_grad, rank_count in zip(gradients, counts, strict=True)
        )
        / ranks
    )
    assert ddp_average == pytest.approx(reference)
    assert naive_rank_mean != pytest.approx(reference)


def test_packing_requires_real_attention_boundaries() -> None:
    mechanism = lesson("llm-training", "Causal training data")["How it works"].lower()
    assert "block-diagonal" in mechanism
    assert "position" in mechanism and "alone" in mechanism
    assert "boundar" in mechanism


def test_sampling_worked_distribution_and_temperature() -> None:
    example = lab_section("llm-inference", 17, "Practice")
    assert "0.625" in example and "0.375" in example
    probabilities = (0.5, 0.3, 0.15, 0.05)
    assert sum(probabilities[:2]) < 0.9 <= sum(probabilities[:3])
    cooled = [p**2 / sum(q**2 for q in probabilities) for p in probabilities]
    assert cooled == pytest.approx((0.685, 0.247, 0.062, 0.007), abs=0.0006)


def test_optimization_practice_names_baselines_and_extensions() -> None:
    graphs = lab_section("gpu-optimizations", 4, "Practice").lower()
    pipeline = lab_section("gpu-optimizations", 5, "Practice").lower()
    assert "fixed" in graphs and "extension" in graphs and "fallback" in graphs
    assert "zero versus four" in pipeline and "extension" in pipeline
    assert "synthetic" in pipeline


def test_inference_capstone_separates_mechanics_from_serving() -> None:
    practice = lab_section("llm-inference", 32, "Practice")
    assert "Lab 32" in practice and "mechanics" in practice.lower()
    assert "vllm_chunked_prefill_ab.sbatch" in practice
    assert "three" in practice
    first_token = lesson("llm-inference", "Autoregressive generation")[
        "Mental model"
    ].lower()
    assert "prefill" in first_token and "first" in first_token
    assert "last sampled token" in first_token


def test_custom_lessons_match_supplied_implementation_scope() -> None:
    cutlass = " ".join(
        lesson(
            "custom-cuda-kernels", "Matrix multiplication and output fusion"
        ).values()
    )
    rmsnorm = " ".join(
        lesson("custom-cuda-kernels", "Residual connections and normalization").values()
    )
    cutlass += (
        ROOT / "custom-cuda-kernels/reference/labs/09_library_epilogue.md"
    ).read_text()
    assert "OpClassSimt" in cutlass and "LinearCombinationRelu" in cutlass
    assert "expanded" in cutlass and "extension" in cutlass.lower()
    rmsnorm += " " + lab_section("custom-cuda-kernels", 11, "Practice")
    assert "256" in rmsnorm and "FP32" in rmsnorm and "extension" in rmsnorm.lower()
    assert "16 KiB" in rmsnorm
    assert 4096 * 2 * 2 == 16 * 1024  # BF16 residual write plus reread.
