"""Guard corrected worked examples and honest runnable-versus-extension scope.

Arithmetic checks validate teaching examples, not GPU or distributed execution.
"""

from course_builder import metadata as cb_metadata

import pytest

from test_course_content_contract import lab_section, ROOT


def lesson(course: str, title: str) -> dict[str, str]:
    return next(
        item
        for item in cb_metadata.parse_course(ROOT / course / "COURSE.md")[2]
        if item["title"] == title
    )


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


def test_compact_interpretation_names_measured_scope():
    graphs = lab_section("gpu-optimizations", 4, "Check your results").lower()
    pipeline = lab_section("gpu-optimizations", 5, "Check your results").lower()
    assert "eager" in graphs and "graph" in graphs and "correctness" in graphs
    assert "zero" in pipeline and "four" in pipeline and "synthetic" in pipeline


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
    cutlass += (ROOT / "custom-cuda-kernels/labs/09_library_epilogue.cu").read_text()
    assert "OpClassSimt" in cutlass and "LinearCombinationRelu" in cutlass
    assert "expanded" in cutlass and "extension" in cutlass.lower()
    rmsnorm += " " + (ROOT / "custom-cuda-kernels/labs/11_residual_rmsnorm.cu").read_text()
    assert "256" in rmsnorm and "FP32" in rmsnorm and "extension" in rmsnorm.lower()
    assert 4096 * 2 * 2 == 16 * 1024  # BF16 residual write plus reread.
