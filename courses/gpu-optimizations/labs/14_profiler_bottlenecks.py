"""Create profile-ready examples for four common GPU bottleneck classes."""

from __future__ import annotations

import argparse
import math
import time
from typing import Any, Callable

from common import (
    add_common_args,
    cuda_times_ms,
    load_torch,
    require_h100,
    seed_everything,
    summarize_ms,
    validate_common_args,
    write_result,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--case",
        choices=("sync", "launch", "memory", "compute"),
        required=True,
        help="Bottleneck mechanism to expose to the profiler.",
    )
    parser.add_argument(
        "--mode",
        choices=("baseline", "optimized"),
        required=True,
        help="Slow baseline or mechanism-matched candidate.",
    )
    return parser.parse_args()


def compile_and_warm(
    torch: Any, function: Callable[[], Any]
) -> tuple[Callable[[], Any], float]:
    compiled = torch.compile(function, fullgraph=True)
    started = time.perf_counter()
    compiled()
    torch.cuda.synchronize()
    return compiled, (time.perf_counter() - started) * 1_000


def build_sync_case(
    torch: Any, args: argparse.Namespace
) -> tuple[Callable[[], Any], Callable[[], Any], dict[str, Any]]:
    elements = 2_000_000 if args.profile == "smoke" else 16_000_000
    steps = 8 if args.profile == "smoke" else 40
    values = torch.randn(elements, device="cuda", dtype=torch.float32)

    def synchronized() -> float:
        total = 0.0
        for _ in range(steps):
            total += values.square().mean().item()
        return total

    def delayed() -> Any:
        reductions = [values.square().mean() for _ in range(steps)]
        return torch.stack(reductions).sum()

    selected = synchronized if args.mode == "baseline" else delayed
    return selected, delayed, {"elements": elements, "steps": steps}


def build_launch_case(
    torch: Any, args: argparse.Namespace
) -> tuple[Callable[[], Any], Callable[[], Any], dict[str, Any]]:
    elements = 262_144 if args.profile == "smoke" else 1_048_576
    steps = 12 if args.profile == "smoke" else 30
    values = torch.randn(elements, device="cuda", dtype=torch.float32)

    def pointwise_chain() -> Any:
        output = values
        for _ in range(steps):
            output = torch.tanh(output * 1.001 + 0.01)
        return output

    compile_ms = None
    selected = pointwise_chain
    if args.mode == "optimized":
        selected, compile_ms = compile_and_warm(torch, pointwise_chain)
    return (
        selected,
        pointwise_chain,
        {
            "elements": elements,
            "pointwise_steps": steps,
            "compiled_first_call_ms": compile_ms,
        },
    )


def build_memory_case(
    torch: Any, args: argparse.Namespace
) -> tuple[Callable[[], Any], Callable[[], Any], dict[str, Any]]:
    elements = 8_000_000 if args.profile == "smoke" else 64_000_000
    values = torch.randn(elements, device="cuda", dtype=torch.float32)
    bias = torch.randn(elements, device="cuda", dtype=torch.float32)

    def pointwise_pipeline() -> Any:
        hidden = values * 1.25 + bias
        gated = torch.nn.functional.silu(hidden) * hidden
        return torch.tanh(gated + 0.1)

    compile_ms = None
    selected = pointwise_pipeline
    if args.mode == "optimized":
        selected, compile_ms = compile_and_warm(torch, pointwise_pipeline)
    minimum_bytes = 3 * elements * values.element_size()
    return (
        selected,
        pointwise_pipeline,
        {
            "elements": elements,
            "compiled_first_call_ms": compile_ms,
            "minimum_input_output_bytes": minimum_bytes,
            "operation_count_note": (
                "No portable FLOP count is assigned to SiLU or tanh; use the "
                "profiler report for measured work and traffic."
            ),
        },
    )


def build_compute_case(
    torch: Any, args: argparse.Namespace
) -> tuple[Callable[[], Any], Callable[[], Any], dict[str, Any]]:
    width = 1_024 if args.profile == "smoke" else 4_096
    left_fp32 = torch.randn((width, width), device="cuda", dtype=torch.float32)
    right_fp32 = torch.randn((width, width), device="cuda", dtype=torch.float32)
    dtype = torch.float32 if args.mode == "baseline" else torch.bfloat16
    left = left_fp32.to(dtype=dtype)
    right = right_fp32.to(dtype=dtype)

    def selected() -> Any:
        return left @ right

    def reference() -> Any:
        return left_fp32 @ right_fp32

    useful_operations = 2 * width**3
    minimum_bytes = (left.numel() + right.numel() + width**2) * left.element_size()
    return (
        selected,
        reference,
        {
            "width": width,
            "dtype": str(dtype).removeprefix("torch."),
            "useful_operations": useful_operations,
            "minimum_input_output_bytes": minimum_bytes,
            "algorithmic_arithmetic_intensity_flop_per_byte": round(
                useful_operations / minimum_bytes, 4
            ),
        },
    )


def scalar_value(torch: Any, value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(value.detach().float().mean().item())


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    builders = {
        "sync": build_sync_case,
        "launch": build_launch_case,
        "memory": build_memory_case,
        "compute": build_compute_case,
    }
    workload, reference, case_metadata = builders[args.case](torch, args)

    samples = cuda_times_ms(
        torch,
        workload,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    timing = summarize_ms(samples)

    with torch.cuda.nvtx.range("profile_region"):
        with torch.cuda.nvtx.range(f"{args.case}_{args.mode}"):
            observed = workload()
    torch.cuda.synchronize()
    expected = reference()
    torch.cuda.synchronize()

    error_metrics: dict[str, float] = {}
    if isinstance(observed, (int, float)):
        absolute_error = abs(float(observed) - scalar_value(torch, expected))
        equivalent = absolute_error <= max(1e-4, abs(float(observed)) * 1e-5)
        error_metrics["absolute_error"] = round(absolute_error, 8)
    elif args.case == "compute" and args.mode == "optimized":
        reference_norm = torch.linalg.vector_norm(expected.float()).clamp_min(1e-12)
        relative_l2_error = float(
            (
                torch.linalg.vector_norm(observed.float() - expected.float())
                / reference_norm
            ).item()
        )
        equivalent = relative_l2_error < 5e-2
        error_metrics["relative_l2_error"] = round(relative_l2_error, 8)
    else:
        equivalent = bool(
            torch.allclose(observed.float(), expected.float(), rtol=1e-4, atol=1e-4)
        )
    finite = (
        math.isfinite(float(observed))
        if isinstance(observed, (int, float))
        else bool(torch.isfinite(observed).all().item())
    )
    if not equivalent or not finite:
        raise SystemExit("The selected path failed its numerical equivalence gate.")

    median_seconds = timing["median_ms"] / 1_000
    measurements: dict[str, Any] = {
        "case": args.case,
        "mode": args.mode,
        "timing": timing,
        "profile_nvtx_range": "profile_region",
        "checksum": round(scalar_value(torch, observed), 8),
        **case_metadata,
    }
    operations = case_metadata.get("useful_operations")
    minimum_bytes = case_metadata.get("minimum_input_output_bytes")
    if operations:
        measurements["effective_workload_tflops"] = round(
            operations / median_seconds / 1e12, 4
        )
    if minimum_bytes:
        measurements["effective_minimum_io_gb_per_s"] = round(
            minimum_bytes / median_seconds / 1e9, 4
        )

    target = write_result(
        args,
        lab_id=f"14_profiler_bottlenecks_{args.case}_{args.mode}",
        environment=environment,
        measurements=measurements,
        correctness={
            "numerically_equivalent": True,
            "finite_output": True,
            **error_metrics,
        },
    )
    print(f"Completed {args.case}/{args.mode} profiler case: {target}")


if __name__ == "__main__":
    main()
