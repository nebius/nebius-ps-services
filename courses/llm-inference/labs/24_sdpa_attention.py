"""Profile prefill- and decode-shaped attention against PyTorch SDPA dispatch."""

from __future__ import annotations

import argparse
import math

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


def peak_bytes(torch: object, operation: object) -> int:
    torch.cuda.empty_cache()
    baseline_bytes = int(torch.cuda.memory_allocated())
    torch.cuda.reset_peak_memory_stats()
    operation()
    torch.cuda.synchronize()
    return int(torch.cuda.max_memory_allocated()) - baseline_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    sequence = 512 if args.profile == "smoke" else 2_048
    batch, heads, head_dim = 2, 8, 64
    rows = []
    for label, query_length, causal in (
        ("prefill", sequence, True),
        ("decode", 1, False),
    ):
        query = torch.randn(
            (batch, heads, query_length, head_dim),
            device="cuda",
            dtype=torch.bfloat16,
        )
        key = torch.randn(
            (batch, heads, sequence, head_dim),
            device="cuda",
            dtype=torch.bfloat16,
        )
        value = torch.randn_like(key)
        causal_mask = (
            torch.ones((query_length, sequence), device="cuda", dtype=torch.bool).tril()
            if causal
            else None
        )

        def materialized() -> object:
            scores = (query @ key.transpose(-2, -1)) / math.sqrt(head_dim)
            if causal_mask is not None:
                scores = scores.masked_fill(~causal_mask, float("-inf"))
            return torch.softmax(scores, dim=-1) @ value

        def sdpa() -> object:
            return torch.nn.functional.scaled_dot_product_attention(
                query, key, value, is_causal=causal
            )

        reference = materialized()
        observed = sdpa()
        max_error = float((reference.float() - observed.float()).abs().max().item())
        correct = bool(torch.allclose(reference, observed, rtol=1e-2, atol=1e-2))
        if not correct:
            raise SystemExit(
                f"Materialized and SDPA {label} paths diverged: {max_error=}"
            )
        with torch.profiler.profile(
            activities=[
                torch.profiler.ProfilerActivity.CPU,
                torch.profiler.ProfilerActivity.CUDA,
            ]
        ) as profile:
            sdpa()
            torch.cuda.synchronize()
        dispatch = sorted(
            event.key
            for event in profile.key_averages()
            if "scaled_dot_product" in event.key.lower()
            or "flash" in event.key.lower()
            or "attention" in event.key.lower()
        )
        timings = {}
        peaks = {}
        for mode, operation in (("materialized", materialized), ("sdpa", sdpa)):
            timings[mode] = summarize_ms(
                cuda_times_ms(
                    torch,
                    operation,
                    warmup=args.warmup,
                    iterations=args.iterations,
                )
            )
            peaks[mode] = peak_bytes(torch, operation)
        rows.append(
            {
                "phase": label,
                "query_length": query_length,
                "key_value_length": sequence,
                "causal": causal,
                "timing": timings,
                "incremental_peak_bytes": peaks,
                "dispatch_keys": dispatch,
                "max_abs_error": round(max_error, 6),
            }
        )
    target_path = write_result(
        args,
        lab_id="24_sdpa_attention",
        environment=environment,
        measurements={"shape": [batch, heads, sequence, head_dim], "cases": rows},
        correctness={"all_cases_close": True, "bf16_rtol_atol": [1e-2, 1e-2]},
    )
    print(f"Completed SDPA backend experiment: {target_path}")


if __name__ == "__main__":
    main()
