"""Measure how a strided view changes a pointwise GPU workload."""

from __future__ import annotations

import argparse
import math
import statistics

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


def break_even_reuses(
    copy_ms: float, strided_ms: float, packed_ms: float
) -> int | None:
    per_use_savings_ms = strided_ms - packed_ms
    if per_use_savings_ms <= 0:
        return None
    return max(1, math.ceil(copy_ms / per_use_savings_ms))


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    side = 4_096 if args.profile == "smoke" else 8_192
    contiguous = torch.randn((side, side), device="cuda")
    strided = contiguous.transpose(0, 1)
    packed = strided.contiguous()
    operations = {
        "contiguous": lambda: torch.sin(contiguous) * 0.5 + contiguous,
        "strided_view": lambda: torch.sin(strided) * 0.5 + strided,
        "repacked_contiguous": lambda: torch.sin(packed) * 0.5 + packed,
    }
    timing_samples = {}
    for name, operation in operations.items():
        timing_samples[name] = cuda_times_ms(
            torch,
            operation,
            warmup=args.warmup,
            iterations=args.iterations,
        )
    timing_samples["repack_copy"] = cuda_times_ms(
        torch,
        lambda: strided.contiguous(),
        warmup=args.warmup,
        iterations=args.iterations,
    )
    timings = {name: summarize_ms(samples) for name, samples in timing_samples.items()}
    raw_medians = {
        name: float(statistics.median(samples))
        for name, samples in timing_samples.items()
    }
    reuse_count = break_even_reuses(
        raw_medians["repack_copy"],
        raw_medians["strided_view"],
        raw_medians["repacked_contiguous"],
    )
    logical_tensor_bytes = contiguous.numel() * contiguous.element_size()
    useful_bytes = logical_tensor_bytes * 2
    useful_bandwidth = {
        name: round(useful_bytes / (median_ms / 1_000) / 2**30, 4)
        for name, median_ms in raw_medians.items()
    }
    reference = operations["strided_view"]()
    correct = bool(
        torch.allclose(
            reference, operations["repacked_contiguous"](), rtol=1e-5, atol=1e-6
        )
    )
    if not correct:
        raise SystemExit("Strided and packed results diverged.")
    target = write_result(
        args,
        lab_id="04_layout_and_coalescing",
        environment=environment,
        measurements={
            "shape": [side, side],
            "dtype": str(contiguous.dtype),
            "element_size_bytes": contiguous.element_size(),
            "logical_tensor_bytes": logical_tensor_bytes,
            "useful_bytes_per_operation": useful_bytes,
            "useful_bytes_definition": "logical input bytes plus output bytes",
            "layouts": {
                "contiguous": {
                    "strides": list(contiguous.stride()),
                    "is_contiguous": contiguous.is_contiguous(),
                },
                "strided_view": {
                    "strides": list(strided.stride()),
                    "is_contiguous": strided.is_contiguous(),
                },
                "repacked_contiguous": {
                    "strides": list(packed.stride()),
                    "is_contiguous": packed.is_contiguous(),
                },
            },
            "timings": timings,
            "useful_bandwidth_gib_per_s": useful_bandwidth,
            "repack": {
                "break_even_reuses": reuse_count,
                "break_even_reason": (
                    None if reuse_count is not None else "packed_not_faster"
                ),
                "calculation_median_ms": raw_medians,
            },
        },
        correctness={
            "allclose": True,
            "strided_view_confirmed": not strided.is_contiguous(),
            "repacked_contiguous_confirmed": packed.is_contiguous(),
        },
    )
    print(f"Completed layout benchmark: {target}")


if __name__ == "__main__":
    main()
