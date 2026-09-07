"""Measure one fresh-process training capstone trial with equivalent updates."""

from __future__ import annotations

import argparse
import math

from common import (
    add_common_args,
    cuda_times_ms,
    load_torch,
    require_h100,
    seed_everything,
    sgd_updates_match,
    summarize_ms,
    validate_common_args,
    write_result,
)


def linear_step_flops(tokens: int, width: int, input_requires_grad: bool) -> int:
    """Count forward and requested backward GEMMs; exclude elementwise work."""
    if tokens < 1 or width < 1:
        raise ValueError("tokens and width must be positive")
    # XW, dW = X.T @ dY, and only when requested dX = dY @ W.T.
    gemms = 2 + int(input_requires_grad)
    return gemms * 2 * tokens * width * width


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--variant-order",
        choices=("baseline-first", "candidate-first"),
        required=True,
        help="Counterbalanced order selected by the three-trial launcher.",
    )
    parser.add_argument(
        "--peak-tflops",
        type=float,
        default=None,
        help="Optional dense BF16 peak for a matmul-only utilization estimate.",
    )
    args = parser.parse_args()
    validate_common_args(args)
    if args.peak_tflops is not None and (
        not math.isfinite(args.peak_tflops) or args.peak_tflops <= 0
    ):
        raise SystemExit("--peak-tflops must be finite and positive")
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    width = 256 if args.profile == "smoke" else 2_048
    tokens = 512 if args.profile == "smoke" else 4_096
    inputs = torch.randn((tokens, width), device="cuda", dtype=torch.bfloat16)
    targets = torch.randn_like(inputs)
    base_weight = (
        torch.randn((width, width), device="cuda", dtype=torch.bfloat16) / width**0.5
    )
    base_bias = torch.zeros(width, device="cuda", dtype=torch.bfloat16)

    def make_state() -> tuple[object, object, object]:
        weight = base_weight.detach().clone().requires_grad_(True)
        bias = base_bias.detach().clone().requires_grad_(True)
        optimizer = torch.optim.SGD([weight, bias], lr=1e-3, foreach=False)
        return weight, bias, optimizer

    def step(
        weight: object, bias: object, optimizer: object, *, fused_bias: bool
    ) -> object:
        optimizer.zero_grad(set_to_none=True)
        hidden = (
            torch.addmm(bias, inputs, weight) if fused_bias else inputs @ weight + bias
        )
        loss = torch.nn.functional.mse_loss(
            torch.nn.functional.gelu(hidden).float(), targets.float()
        )
        loss.backward()
        optimizer.step()
        return loss

    check_baseline = make_state()
    check_candidate = make_state()
    reference_loss = step(*check_baseline, fused_bias=False)
    candidate_loss = step(*check_candidate, fused_bias=True)
    close = bool(
        torch.allclose(reference_loss, candidate_loss, rtol=1e-2, atol=1e-2)
        and sgd_updates_match(
            torch,
            (base_weight, base_bias),
            check_baseline[:2],
            check_candidate[:2],
            learning_rate=1e-3,
        )
    )
    if not close:
        raise SystemExit("Baseline and candidate training updates diverged.")

    baseline_state = make_state()
    candidate_state = make_state()
    operations = {
        "baseline": lambda: step(*baseline_state, fused_bias=False),
        "candidate": lambda: step(*candidate_state, fused_bias=True),
    }
    order = (
        ("baseline", "candidate")
        if args.variant_order == "baseline-first"
        else ("candidate", "baseline")
    )
    timing: dict[str, dict[str, float]] = {}
    incremental_peak: dict[str, int] = {}
    for name in order:
        timing[name] = summarize_ms(
            cuda_times_ms(
                torch,
                operations[name],
                warmup=args.warmup,
                iterations=args.iterations,
            )
        )
        torch.cuda.empty_cache()
        memory_before = int(torch.cuda.memory_allocated())
        torch.cuda.reset_peak_memory_stats()
        operations[name]()
        torch.cuda.synchronize()
        incremental_peak[name] = int(torch.cuda.max_memory_allocated()) - memory_before

    minimum_step_flops = linear_step_flops(tokens, width, inputs.requires_grad)
    candidate_tflops = (
        minimum_step_flops / (timing["candidate"]["median_ms"] / 1_000) / 1e12
    )
    provisional_observation = (
        "candidate-median-lower-in-this-trial"
        if timing["candidate"]["median_ms"] < timing["baseline"]["median_ms"]
        else "candidate-median-not-lower-in-this-trial"
    )
    target_path = write_result(
        args,
        lab_id="31_training_capstone",
        environment=environment,
        measurements={
            "shape": [tokens, width],
            "warmup": args.warmup,
            "iterations": args.iterations,
            "peak_tflops_reference": args.peak_tflops,
            "trial_kind": "one fresh Python process",
            "variant_order": args.variant_order,
            "timing": timing,
            "incremental_peak_bytes": incremental_peak,
            "candidate_tokens_per_second": round(
                tokens / (timing["candidate"]["median_ms"] / 1_000), 2
            ),
            "candidate_minimum_step_tflops": round(candidate_tflops, 4),
            "candidate_matmul_utilization_percent": (
                round(100 * candidate_tflops / args.peak_tflops, 4)
                if args.peak_tflops is not None
                else None
            ),
            "minimum_step_flops_note": (
                "two GEMMs: forward XW and weight gradient X.T @ dY; "
                "inputs require no gradient, so no dX GEMM; elementwise work "
                "excluded from numerator, full step included in denominator; "
                "matmul-only lower-bound utilization, not full-model LLM MFU"
            ),
            "minimum_step_flops": minimum_step_flops,
            "input_requires_grad": inputs.requires_grad,
            "communication_scope": (
                "single GPU; use Labs 28 and 30 for communication evidence"
            ),
            "provisional_observation": provisional_observation,
            "publication_decision": "pending three independent run records",
        },
        correctness={"full_update_close": True},
    )
    print(f"Wrote one training capstone trial: {target_path}")


if __name__ == "__main__":
    main()
