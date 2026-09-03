"""Compare matrix shape and precision choices without assuming a speedup."""

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
    aligned = 1_024 if args.profile == "smoke" else 4_096
    misaligned = aligned - 7
    rows = []
    all_finite = True
    all_acceptable = True
    for label, width, dtype in (
        ("bf16_aligned", aligned, torch.bfloat16),
        ("bf16_misaligned", misaligned, torch.bfloat16),
        ("fp32_aligned", aligned, torch.float32),
    ):
        left_fp32 = torch.randn((width, width), device="cuda", dtype=torch.float32)
        right_fp32 = torch.randn((width, width), device="cuda", dtype=torch.float32)
        left = left_fp32.to(dtype=dtype)
        right = right_fp32.to(dtype=dtype)
        reference = left_fp32 @ right_fp32
        result = left @ right
        samples = cuda_times_ms(
            torch,
            lambda left=left, right=right: left @ right,
            warmup=args.warmup,
            iterations=args.iterations,
        )
        median_ms = summarize_ms(samples)["median_ms"]
        operations = 2 * width**3
        relative_l2_error = float(
            torch.linalg.vector_norm(result.float() - reference)
            / torch.linalg.vector_norm(reference)
        )
        acceptable = relative_l2_error < (5e-2 if dtype == torch.bfloat16 else 1e-5)
        rows.append(
            {
                "mode": label,
                "width": width,
                "dtype": str(dtype).removeprefix("torch."),
                "timing": summarize_ms(samples),
                "effective_tflops": round(operations / (median_ms / 1_000) / 1e12, 3),
                "relative_l2_error": round(relative_l2_error, 8),
                "numerically_acceptable": acceptable,
            }
        )
        all_finite = all_finite and bool(torch.isfinite(result).all().item())
        all_acceptable = all_acceptable and acceptable
    if not all_finite or not all_acceptable:
        raise SystemExit("A matrix result failed the declared numerical gate.")
    target = write_result(
        args,
        lab_id="10_shape_precision",
        environment=environment,
        measurements={"cases": rows},
        correctness={"all_results_finite": True, "all_numerically_acceptable": True},
    )
    print(f"Completed shape and precision experiment: {target}")


if __name__ == "__main__":
    main()
