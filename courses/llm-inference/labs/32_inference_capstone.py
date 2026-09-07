"""Measure one fresh-process attention capstone trial for a later decision."""

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--variant-order",
        choices=("baseline-first", "candidate-first"),
        required=True,
        help="Counterbalanced order selected by the three-trial launcher.",
    )
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    sequence = 256 if args.profile == "smoke" else 2_048
    query = torch.randn((1, 8, sequence, 64), device="cuda", dtype=torch.bfloat16)
    key = torch.randn_like(query)
    value = torch.randn_like(query)
    mask = torch.ones((sequence, sequence), device="cuda", dtype=torch.bool).tril()

    def baseline() -> object:
        scores = (query @ key.transpose(-2, -1)) / math.sqrt(64)
        scores = scores.masked_fill(~mask, float("-inf"))
        return torch.softmax(scores, dim=-1) @ value

    def candidate() -> object:
        return torch.nn.functional.scaled_dot_product_attention(
            query, key, value, is_causal=True
        )

    reference = baseline()
    observed = candidate()
    close = bool(torch.allclose(reference, observed, rtol=1e-2, atol=1e-2))
    if not close:
        raise SystemExit("The SDPA capstone trial failed output equivalence.")
    order = (
        ("materialized", "sdpa")
        if args.variant_order == "baseline-first"
        else ("sdpa", "materialized")
    )
    operations = {"materialized": baseline, "sdpa": candidate}
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
    provisional_observation = (
        "sdpa-median-lower-in-this-trial"
        if timing["sdpa"]["median_ms"] < timing["materialized"]["median_ms"]
        else "sdpa-median-not-lower-in-this-trial"
    )
    target_path = write_result(
        args,
        lab_id="32_inference_capstone",
        environment=environment,
        measurements={
            "isl": sequence,
            "osl": 1,
            "concurrency": 1,
            "warmup": args.warmup,
            "iterations": args.iterations,
            "trial_kind": "one fresh Python process",
            "variant_order": args.variant_order,
            "timing": timing,
            "incremental_peak_bytes": incremental_peak,
            "max_abs_error": round(
                float((reference.float() - observed.float()).abs().max()), 6
            ),
            "provisional_observation": provisional_observation,
            "publication_decision": "pending three independent run records",
            "service_claim": (
                "not made; live engine TTFT, ITL, throughput, and quality "
                "remain separate"
            ),
        },
        correctness={"output_allclose": True, "rtol": 1e-2, "atol": 1e-2},
    )
    print(f"Wrote one inference capstone trial: {target_path}")


if __name__ == "__main__":
    main()
