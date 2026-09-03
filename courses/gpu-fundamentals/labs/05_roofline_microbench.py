"""Contrast a bandwidth-oriented vector kernel with a compute-oriented GEMM."""

from __future__ import annotations

import argparse

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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    vector_elements = 16_000_000 if args.profile == "smoke" else 128_000_000
    matrix_size = 2_048 if args.profile == "smoke" else 8_192
    x = torch.randn(vector_elements, device="cuda")
    y = torch.randn(vector_elements, device="cuda")
    a = torch.randn((matrix_size, matrix_size), device="cuda", dtype=torch.bfloat16)
    b = torch.randn((matrix_size, matrix_size), device="cuda", dtype=torch.bfloat16)
    bandwidth_samples = cuda_times_ms(
        torch,
        lambda: torch.add(y, x, alpha=1.25),
        warmup=args.warmup,
        iterations=args.iterations,
    )
    compute_samples = cuda_times_ms(
        torch,
        lambda: a @ b,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    bandwidth_ms = summarize_ms(bandwidth_samples)["median_ms"]
    compute_ms = summarize_ms(compute_samples)["median_ms"]
    bytes_moved = vector_elements * 4 * 3
    achieved_gib_s = bytes_moved / (bandwidth_ms / 1_000) / 2**30
    achieved_tflops = 2 * matrix_size**3 / (compute_ms / 1_000) / 1e12
    finite = bool(
        torch.isfinite(torch.add(y, x, alpha=1.25)).all()
        and torch.isfinite(a @ b).all()
    )
    if not finite:
        raise SystemExit("A roofline workload produced non-finite output.")
    target = write_result(
        args,
        lab_id="05_roofline_microbench",
        environment=environment,
        measurements={
            "bandwidth_kernel": {
                "timing": summarize_ms(bandwidth_samples),
                "estimated_gib_per_s": round(achieved_gib_s, 2),
                "arithmetic_intensity_flop_per_byte": round(2 / 12, 4),
            },
            "gemm": {
                "timing": summarize_ms(compute_samples),
                "achieved_tflops": round(achieved_tflops, 2),
            },
        },
        correctness={"finite_outputs": True},
    )
    print(f"Completed roofline microbenchmark: {target}")


if __name__ == "__main__":
    main()
