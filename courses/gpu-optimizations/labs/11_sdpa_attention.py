"""Compare materialized causal attention with PyTorch SDPA."""

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    return parser.parse_args()


def peak_bytes(torch: object, operation: object) -> int:
    torch.cuda.empty_cache()
    baseline_bytes = int(torch.cuda.memory_allocated())
    torch.cuda.reset_peak_memory_stats()
    operation()
    torch.cuda.synchronize()
    return int(torch.cuda.max_memory_allocated()) - baseline_bytes


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    sequence = 512 if args.profile == "smoke" else 2_048
    batch = 2
    heads = 8
    head_dim = 64
    query = torch.randn(
        (batch, heads, sequence, head_dim), device="cuda", dtype=torch.bfloat16
    )
    key = torch.randn_like(query)
    value = torch.randn_like(query)
    causal = torch.ones((sequence, sequence), device="cuda", dtype=torch.bool).tril()

    def materialized() -> object:
        scores = (query @ key.transpose(-2, -1)) / math.sqrt(head_dim)
        scores = scores.masked_fill(~causal, float("-inf"))
        return torch.softmax(scores, dim=-1) @ value

    def sdpa() -> object:
        return torch.nn.functional.scaled_dot_product_attention(
            query, key, value, is_causal=True
        )

    reference = materialized()
    observed = sdpa()
    max_error = float((reference.float() - observed.float()).abs().max().item())
    correct = bool(torch.allclose(reference, observed, rtol=3e-2, atol=3e-2))
    if not correct:
        raise SystemExit(f"Materialized attention and SDPA diverged: {max_error=}")
    rows = []
    for label, operation in (("materialized", materialized), ("sdpa", sdpa)):
        samples = cuda_times_ms(
            torch,
            operation,
            warmup=args.warmup,
            iterations=args.iterations,
        )
        rows.append(
            {
                "mode": label,
                "timing": summarize_ms(samples),
                "incremental_peak_bytes": peak_bytes(torch, operation),
            }
        )
    target = write_result(
        args,
        lab_id="11_sdpa_attention",
        environment=environment,
        measurements={
            "shape": [batch, heads, sequence, head_dim],
            "cases": rows,
        },
        correctness={"allclose": True, "max_abs_error": round(max_error, 6)},
    )
    print(f"Completed SDPA experiment: {target}")


if __name__ == "__main__":
    main()
