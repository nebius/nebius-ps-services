"""Check invalid references, norm overflow and compared tensor boundaries on CPU."""

from __future__ import annotations

import math

import pytest
from test_course_review_fixes import load_lab


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("experiment", ["accumulation", "bucketing", "partition"])
def test_nonfinite_reference_cannot_certify_a_finite_candidate(value, experiment):
    torch = pytest.importorskip("torch")
    expected, observed = torch.ones(4), torch.ones(4)
    expected[2] = value
    if experiment == "accumulation":
        with load_lab("llm-training/labs/02_gradient_accumulation.py") as lab:
            with pytest.raises(SystemExit, match="finite"):
                lab.validate_gradient_samples(torch, [expected], [observed])
    elif experiment == "bucketing":
        with load_lab("llm-inference/labs/18_padding_bucketing.py") as lab:
            with pytest.raises(SystemExit, match="finite"):
                lab.validate_last_token_logits(
                    torch, expected.reshape(1, 1, 4), {0: observed}, [1]
                )
    else:
        with load_lab("llm-training/labs/19_tensor_parallel_linear.py") as lab:
            assert lab.relative_l2(torch, observed, expected) == math.inf


def test_finite_inputs_with_overflowing_error_arithmetic_are_rejected():
    torch = pytest.importorskip("torch")
    large = torch.full((4,), torch.finfo(torch.float32).max)
    assert bool(torch.isfinite(large).all())
    with load_lab("llm-training/labs/02_gradient_accumulation.py") as lab:
        with pytest.raises(SystemExit, match="diverged"):
            lab.validate_gradient_samples(torch, [large], [-large])
    with load_lab("llm-inference/labs/18_padding_bucketing.py") as lab:
        with pytest.raises(SystemExit, match="finite"):
            lab.validate_last_token_logits(
                torch, large.reshape(1, 1, 4), {0: large}, [1]
            )
    with load_lab("llm-training/labs/19_tensor_parallel_linear.py") as lab:
        assert lab.relative_l2(torch, large, large) == math.inf


def test_accumulation_requires_present_samples_and_retains_tolerance():
    torch = pytest.importorskip("torch")
    expected = [torch.ones(4)]
    with load_lab("llm-training/labs/02_gradient_accumulation.py") as lab:
        assert lab.validate_gradient_samples(
            torch, expected, [torch.full((4,), 1.001)]
        ) == pytest.approx(0.001, abs=1e-7)
        with pytest.raises(SystemExit, match="missing"):
            lab.validate_gradient_samples(torch, expected, [None])
        with pytest.raises(SystemExit, match="nonempty"):
            lab.validate_gradient_samples(torch, expected, [])


def test_bucketing_compares_last_real_token_not_unused_padding():
    torch = pytest.importorskip("torch")
    padded = torch.ones(2, 3, 4)
    padded[0, 1:] = float("nan")
    buckets = {0: torch.ones(4), 1: torch.ones(4)}
    with load_lab("llm-inference/labs/18_padding_bucketing.py") as lab:
        assert lab.validate_last_token_logits(torch, padded, buckets, [1, 3]) == 0
        buckets[1] *= 1.01
        assert lab.validate_last_token_logits(
            torch, padded, buckets, [1, 3]
        ) == pytest.approx(0.01, abs=1e-7)
