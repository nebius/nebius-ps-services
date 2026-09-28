"""Numerical and rank-failure regressions for Lab 19 and its local diagnostic."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
import torch
from test_course_review_fixes import load_lab

COMMON = "advanced-gpu-communication/labs/gradient_overlap_common.py"


def seeds(elements=4096):
    generator = torch.Generator().manual_seed(17)
    return [
        (
            torch.randn(elements, dtype=torch.bfloat16, generator=generator),
            torch.linspace(0.5, 1.5, elements, dtype=torch.bfloat16),
        )
    ]


@pytest.mark.parametrize("elements", (257, 4096, 8 * 2**20 // 2))
def test_independent_reference_models_bf16_backward_casts(elements):
    with load_lab(COMMON) as module:
        pairs = seeds(elements)
        parameters = [pairs[0][0].clone().requires_grad_(True)]
        module.bucket_loss(parameters, pairs).backward()
        actual = [parameters[0].grad]
        reference = module.local_gradient_reference(torch, pairs)
        assert torch.equal(actual[0], reference[0])
        checks = module.gradient_checks(torch, reference, actual)
        assert checks["passed"] and checks["all_finite"]
        assert checks["buckets"][0]["max_normalized_abs_error"] == 0


@pytest.mark.parametrize("factor", (0, -1, 0.5, 2 / 3, 2))
def test_scale_corruption_fails_even_when_old_absolute_tolerance_passes(factor):
    with load_lab(COMMON) as module:
        reference = module.local_gradient_reference(torch, seeds())
        actual = [reference[0] * factor]
        assert torch.allclose(reference[0], actual[0], rtol=0.01, atol=0.01)
        assert not module.gradient_checks(torch, reference, actual)["passed"]


def test_small_bf16_perturbation_passes_and_small_errors_are_not_rounded():
    with load_lab(COMMON) as module:
        reference = module.local_gradient_reference(torch, seeds())
        actual = [
            (reference[0].float() + reference[0].float().abs().max() * 0.001).bfloat16()
        ]
        checks = module.gradient_checks(torch, reference, actual)
        assert checks["passed"]
        assert 0 < checks["buckets"][0]["max_abs_error"] < 0.0001


@pytest.mark.parametrize(
    "defect",
    (
        "missing",
        "count",
        "empty",
        "shape",
        "dtype",
        "device",
        "nan",
        "inf",
        "reference_nan",
    ),
)
def test_invalid_buckets_return_failure_without_raising(defect):
    reference = [torch.ones(4, dtype=torch.bfloat16)]
    actual = [reference[0].clone()]
    if defect == "missing":
        actual = [None]
    elif defect == "count":
        actual *= 2
    elif defect == "empty":
        reference, actual = [], []
    elif defect == "shape":
        actual = [torch.ones(1, dtype=torch.bfloat16)]
    elif defect == "dtype":
        actual = [actual[0].float()]
    elif defect == "device":
        actual = [torch.empty(4, dtype=torch.bfloat16, device="meta")]
    elif defect == "reference_nan":
        reference[0][0] = float("nan")
    else:
        actual[0][0] = float(defect)
    with load_lab(COMMON) as module:
        assert not module.gradient_checks(torch, reference, actual)["passed"]
        assert not module.gradient_checks(torch, None, actual)["passed"]
        assert not module.gradient_checks(
            torch,
            [torch.empty(0, dtype=torch.bfloat16)],
            [torch.empty(0, dtype=torch.bfloat16)],
        )["passed"]


def test_zero_reference_requires_exact_zeros():
    reference = [torch.zeros(4, dtype=torch.bfloat16)]
    with load_lab(COMMON) as module:
        assert module.gradient_checks(torch, reference, reference)["passed"]
        actual = [reference[0] + 1e-30]
        assert not module.gradient_checks(torch, reference, actual)["passed"]


@pytest.mark.parametrize("rank", (0, 1))
def test_one_invalid_rank_stops_every_rank_before_warmup_and_output(monkeypatch, rank):
    events = []

    def all_reduce(value, op=None, async_op=False):
        if op == "MIN":
            events.append(("consensus", int(value.item())))
            value.fill_(0)  # Rank 1 fails; all ranks observe the same MIN verdict.
        elif op == "MAX":
            events.append(("reference_step", None))
        else:
            value.mul_(2)
        return SimpleNamespace(wait=lambda: None) if async_op else None

    event = SimpleNamespace(
        record=lambda: None, synchronize=lambda: None, elapsed_time=lambda _other: 1.0
    )

    class CpuTorch:
        cuda = SimpleNamespace(Event=lambda **_kwargs: event, synchronize=lambda: None)
        distributed = SimpleNamespace(
            all_reduce=all_reduce, ReduceOp=SimpleNamespace(MIN="MIN", MAX="MAX")
        )

        def __getattr__(self, name):
            return getattr(torch, name)

        def tensor(self, value, **kwargs):
            kwargs.pop("device", None)
            return torch.tensor(value, **kwargs)

    with load_lab("advanced-gpu-communication/labs/19_gradient_overlap.py") as module:
        monkeypatch.setattr(module, "load_torch", CpuTorch)
        monkeypatch.setattr(module, "require_course_gpu", lambda _torch: {})
        monkeypatch.setattr(module, "seed_everything", lambda *_args: None)
        monkeypatch.setattr(module, "validate_common_args", lambda _args: None)
        monkeypatch.setattr(module, "init_nccl", lambda _torch: (rank, 2, 0))
        monkeypatch.setattr(
            module, "close_distributed", lambda _torch: events.append(("closed", None))
        )
        monkeypatch.setattr(
            module, "make_bucket_seeds", lambda *_args, **_kwargs: seeds(32)
        )
        monkeypatch.setattr(
            module,
            "write_result",
            lambda *_args, **_kwargs: pytest.fail("wrote result"),
        )
        if rank == 1:
            original = module.gradient_checks
            monkeypatch.setattr(
                module, "gradient_checks", lambda t, r, _o: original(t, r, [None])
            )
        monkeypatch.setattr(sys, "argv", ["lab", "--warmup", "2", "--iterations", "1"])
        with pytest.raises(SystemExit, match="failed equivalence"):
            module.main()
    assert events == [
        ("reference_step", None),
        ("reference_step", None),
        ("consensus", int(rank == 0)),
        ("closed", None),
    ]
