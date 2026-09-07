"""Compare BF16 and optional Transformer Engine FP8 forward/backward paths."""

from __future__ import annotations

import argparse
import math
import statistics
from typing import Any, Callable

from common import (
    add_common_args,
    load_torch,
    require_h100,
    seed_everything,
    validate_common_args,
    write_result,
)


def relative_l2(torch: Any, candidate: Any, reference: Any) -> float:
    numerator = (candidate.float() - reference.float()).norm()
    denominator = reference.float().norm().clamp_min(1e-12)
    return float(numerator / denominator)


def measure_steps(
    torch: Any,
    operation: Callable[[], tuple[Any, Any]],
    *,
    warmup: int,
    iterations: int,
) -> tuple[list[float], dict[str, float]]:
    for _ in range(warmup):
        operation()
    torch.cuda.synchronize()
    steady_state_bytes = torch.cuda.memory_allocated()
    torch.cuda.reset_peak_memory_stats()
    samples: list[float] = []
    for _ in range(iterations):
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        operation()
        end.record()
        end.synchronize()
        samples.append(float(start.elapsed_time(end)))
    peak_bytes = torch.cuda.max_memory_allocated()
    memory = {
        "steady_state_allocated_mib": steady_state_bytes / 2**20,
        "incremental_peak_above_baseline_mib": max(0, peak_bytes - steady_state_bytes)
        / 2**20,
    }
    return samples, memory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--max-output-relative-l2", type=float, default=0.2)
    parser.add_argument("--max-gradient-relative-l2", type=float, default=0.3)
    args = parser.parse_args()
    validate_common_args(args)
    thresholds = (args.max_output_relative_l2, args.max_gradient_relative_l2)
    if any(not math.isfinite(value) or value <= 0 for value in thresholds):
        raise SystemExit("Relative-L2 thresholds must be finite and positive.")
    if args.warmup < 1:
        raise SystemExit(
            "--warmup must be at least one so BF16 and FP8 use warmed boundaries."
        )
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    try:
        import transformer_engine
        import transformer_engine.pytorch as te
        from transformer_engine.common.recipe import DelayedScaling, Format
    except ImportError as exc:
        raise SystemExit(
            "Optional lab blocked: activate a cluster-approved Transformer "
            "Engine environment compatible with the pinned PyTorch/CUDA stack."
        ) from exc

    rows, hidden = (1_024, 1_024) if args.profile == "smoke" else (4_096, 4_096)
    layer = te.Linear(
        hidden,
        hidden,
        bias=True,
        params_dtype=torch.bfloat16,
    ).cuda()
    inputs = torch.randn(rows, hidden, device="cuda", dtype=torch.bfloat16)
    recipe = DelayedScaling(
        fp8_format=Format.HYBRID,
        amax_history_len=16,
        amax_compute_algo="max",
    )

    def bf16_step() -> tuple[Any, Any]:
        layer.zero_grad(set_to_none=True)
        output = layer(inputs)
        loss = output.float().square().mean()
        loss.backward()
        return output.detach(), layer.weight.grad.detach()

    def fp8_step() -> tuple[Any, Any]:
        layer.zero_grad(set_to_none=True)
        with te.autocast(enabled=True, recipe=recipe):
            output = layer(inputs)
            loss = output.float().square().mean()
        loss.backward()
        return output.detach(), layer.weight.grad.detach()

    # Measure the BF16 path before an FP8 call can allocate scaling metadata.
    # Each path reports its warmed allocation baseline and only the incremental
    # peak above that baseline, not a standalone full-model footprint.
    bf16_samples, bf16_memory = measure_steps(
        torch,
        bf16_step,
        warmup=args.warmup,
        iterations=args.iterations,
    )

    # Delayed scaling needs prior observations. Validate the same warmed recipe
    # state used by the timing path instead of accepting a cold first call.
    for _ in range(args.warmup):
        fp8_step()

    def compare_warmed_once() -> dict[str, float | bool]:
        bf16_output, bf16_gradient = bf16_step()
        fp8_output, fp8_gradient = fp8_step()
        finite = all(
            bool(torch.isfinite(value).all())
            for value in (bf16_output, bf16_gradient, fp8_output, fp8_gradient)
        )
        return {
            "output_relative_l2": relative_l2(torch, fp8_output, bf16_output),
            "gradient_relative_l2": relative_l2(torch, fp8_gradient, bf16_gradient),
            "finite": finite,
        }

    validation_iterations = max(3, min(args.iterations, 8))
    numerical_samples = [compare_warmed_once() for _ in range(validation_iterations)]

    fp8_samples, fp8_memory = measure_steps(
        torch,
        fp8_step,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    numerical_samples.append(compare_warmed_once())
    output_errors = [
        float(sample["output_relative_l2"]) for sample in numerical_samples
    ]
    gradient_errors = [
        float(sample["gradient_relative_l2"]) for sample in numerical_samples
    ]
    finite = all(bool(sample["finite"]) for sample in numerical_samples)
    errors_finite = all(
        math.isfinite(value) for value in (*output_errors, *gradient_errors)
    )
    max_output_error = max(output_errors)
    max_gradient_error = max(gradient_errors)
    if (
        not finite
        or not errors_finite
        or max_output_error > args.max_output_relative_l2
        or max_gradient_error > args.max_gradient_relative_l2
    ):
        raise SystemExit("Warmed FP8 numerical acceptance gate failed.")
    environment["transformer_engine_version"] = getattr(
        transformer_engine, "__version__", "unknown"
    )
    target = write_result(
        args,
        lab_id="22_transformer_engine_fp8",
        environment=environment,
        measurements={
            "rows": rows,
            "hidden_size": hidden,
            "recipe": "DelayedScaling",
            "fp8_format": "HYBRID",
            "amax_history_len": 16,
            "bf16_median_device_region_ms": round(statistics.median(bf16_samples), 4),
            "fp8_median_device_region_ms": round(statistics.median(fp8_samples), 4),
            "memory_scope": (
                "per-path incremental peak above its warmed allocation baseline; "
                "not a standalone full-model footprint"
            ),
            "bf16_steady_state_allocated_mib": round(
                bf16_memory["steady_state_allocated_mib"], 1
            ),
            "bf16_incremental_peak_above_baseline_mib": round(
                bf16_memory["incremental_peak_above_baseline_mib"], 1
            ),
            "fp8_steady_state_allocated_mib": round(
                fp8_memory["steady_state_allocated_mib"], 1
            ),
            "fp8_incremental_peak_above_baseline_mib": round(
                fp8_memory["incremental_peak_above_baseline_mib"], 1
            ),
            "numerical_sample_count": len(numerical_samples),
            "validation_state": "warmed delayed-scaling recipe before and after timing",
            "median_output_relative_l2": round(statistics.median(output_errors), 6),
            "max_output_relative_l2": round(max_output_error, 6),
            "median_gradient_relative_l2": round(statistics.median(gradient_errors), 6),
            "max_gradient_relative_l2": round(max_gradient_error, 6),
        },
        correctness={
            "finite_outputs_gradients_and_errors": finite and errors_finite,
            "output_relative_l2_within_declared_threshold": True,
            "gradient_relative_l2_within_declared_threshold": True,
            "backward_outside_fp8_autocast": True,
        },
    )
    print(f"Completed optional Transformer Engine FP8 comparison: {target}")


if __name__ == "__main__":
    main()
