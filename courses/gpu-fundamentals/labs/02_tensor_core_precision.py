"""Measure H100 matrix multiplication across practical training precisions."""

from __future__ import annotations

import argparse
import math

from common import (
    add_common_args,
    cuda_times_ms,
    load_torch,
    require_h100,
    resolve_int_override,
    seed_everything,
    summarize_ms,
    validate_common_args,
    write_result,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--matrix-size", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    size = resolve_int_override(
        args.matrix_size,
        2_048 if args.profile == "smoke" else 8_192,
        option="--matrix-size",
        minimum=256,
    )
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    a32 = torch.randn((size, size), device="cuda", dtype=torch.float32)
    b32 = torch.randn((size, size), device="cuda", dtype=torch.float32)
    torch.set_float32_matmul_precision("highest")
    reference = a32 @ b32
    modes = [
        ("fp32_highest", torch.float32, "highest"),
        ("tf32_high", torch.float32, "high"),
        ("bf16", torch.bfloat16, "high"),
        ("fp16", torch.float16, "high"),
    ]
    rows = []
    for name, dtype, matmul_mode in modes:
        torch.set_float32_matmul_precision(matmul_mode)
        a = a32.to(dtype)
        b = b32.to(dtype)

        def operation(a: object = a, b: object = b) -> object:
            return a @ b

        samples = cuda_times_ms(
            torch,
            operation,
            warmup=args.warmup,
            iterations=args.iterations,
        )
        output = operation().float()
        median_ms = summarize_ms(samples)["median_ms"]
        tflops = 2 * size**3 / (median_ms / 1_000) / 1e12
        relative_l2 = (output - reference).norm() / reference.norm()
        rows.append(
            {
                "mode": name,
                "timing": summarize_ms(samples),
                "achieved_tflops": round(float(tflops), 2),
                "relative_l2_error": round(float(relative_l2), 7),
            }
        )
    torch.set_float32_matmul_precision("highest")
    valid_errors = all(
        math.isfinite(row["relative_l2_error"]) and row["relative_l2_error"] < 0.1
        for row in rows
    )
    if not valid_errors:
        raise SystemExit(
            "A precision mode produced a non-finite or unexpectedly inaccurate result."
        )
    target = write_result(
        args,
        lab_id="02_tensor_core_precision",
        environment=environment,
        measurements={"matrix_size": size, "modes": rows},
        correctness={"finite_outputs_within_teaching_tolerance": True},
    )
    print(f"Completed precision benchmark: {target}")


if __name__ == "__main__":
    main()
