"""Show why host wall-clock timing without synchronization is misleading."""

from __future__ import annotations

import argparse
import statistics
import time

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
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    size = 2_048 if args.profile == "smoke" else 8_192
    a = torch.randn((size, size), device="cuda", dtype=torch.bfloat16)
    b = torch.randn((size, size), device="cuda", dtype=torch.bfloat16)

    def operation() -> object:
        return a @ b

    for _ in range(args.warmup):
        operation()
    torch.cuda.synchronize()
    unsynchronized = []
    for _ in range(args.iterations):
        start = time.perf_counter()
        operation()
        unsynchronized.append((time.perf_counter() - start) * 1_000)
    torch.cuda.synchronize()
    synchronized = []
    for _ in range(args.iterations):
        start = time.perf_counter()
        operation()
        torch.cuda.synchronize()
        synchronized.append((time.perf_counter() - start) * 1_000)
    event_samples = cuda_times_ms(
        torch, operation, warmup=args.warmup, iterations=args.iterations
    )
    target = write_result(
        args,
        lab_id="01_timing_basics",
        environment=environment,
        measurements={
            "host_without_sync_median_ms": round(statistics.median(unsynchronized), 4),
            "host_with_sync_median_ms": round(statistics.median(synchronized), 4),
            "cuda_events": summarize_ms(event_samples),
        },
        correctness={"finite_output": bool(torch.isfinite(operation()).all().item())},
    )
    print(f"Completed timing comparison: {target}")


if __name__ == "__main__":
    main()
