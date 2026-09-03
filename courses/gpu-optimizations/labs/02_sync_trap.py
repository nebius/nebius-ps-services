"""Compare per-step .item() synchronization with one delayed reduction."""

from __future__ import annotations

import argparse
import statistics
import time

from common import (
    add_common_args,
    load_torch,
    require_h100,
    resolve_int_override,
    seed_everything,
    validate_common_args,
    write_result,
)


def measure(torch: object, function: object, warmup: int, iterations: int) -> float:
    for _ in range(warmup):
        function()
    torch.cuda.synchronize()
    samples = []
    for _ in range(iterations):
        torch.cuda.synchronize()
        start = time.perf_counter()
        function()
        torch.cuda.synchronize()
        samples.append((time.perf_counter() - start) * 1_000)
    return statistics.median(samples)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--steps", type=int, default=None)
    args = parser.parse_args()
    validate_common_args(args)
    steps = resolve_int_override(
        args.steps,
        25 if args.profile == "smoke" else 200,
        option="--steps",
    )
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    x = torch.randn(8_000_000, device="cuda")

    def synchronized_loop() -> float:
        total = 0.0
        for _ in range(steps):
            total += (x.square().mean()).item()
        return total

    def delayed_loop() -> float:
        values = []
        for _ in range(steps):
            values.append(x.square().mean())
        return float(torch.stack(values).sum().item())

    sync_ms = measure(torch, synchronized_loop, args.warmup, args.iterations)
    delayed_ms = measure(torch, delayed_loop, args.warmup, args.iterations)
    expected = synchronized_loop()
    actual = delayed_loop()
    correct = abs(expected - actual) <= max(1e-4, abs(expected) * 1e-5)
    if not correct:
        raise SystemExit("The two reduction strategies returned different values.")
    target = write_result(
        args,
        lab_id="02_sync_trap",
        environment=environment,
        measurements={
            "steps": steps,
            "per_step_item_median_ms": round(sync_ms, 4),
            "delayed_item_median_ms": round(delayed_ms, 4),
        },
        correctness={"equivalent_sum": True},
    )
    print(f"Completed synchronization experiment: {target}")


if __name__ == "__main__":
    main()
