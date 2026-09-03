"""Diagnose a slow baseline and compare it with a measured optimized path."""

from __future__ import annotations

import argparse
import statistics
import time

from common import (
    add_common_args,
    load_torch,
    require_h100,
    seed_everything,
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
    host_a = torch.randn((size, size), dtype=torch.float32, pin_memory=True)
    host_b = torch.randn((size, size), dtype=torch.float32, pin_memory=True)
    device_a = host_a.to("cuda", dtype=torch.bfloat16)
    device_b = host_b.to("cuda", dtype=torch.bfloat16)

    def baseline() -> tuple[object, float]:
        a = host_a.to("cuda")
        b = host_b.to("cuda")
        output = torch.tanh(torch.nn.functional.silu(a @ b) + 0.1)
        scalar = output.mean().item()
        return output, scalar

    def optimized_function(a: object, b: object) -> object:
        return torch.tanh(torch.nn.functional.silu(a @ b) + 0.1)

    compiled = torch.compile(optimized_function, fullgraph=True)
    compiled(device_a, device_b)
    torch.cuda.synchronize()

    def optimized() -> tuple[object, float]:
        output = compiled(device_a, device_b)
        scalar = float(output.mean().cpu())
        return output, scalar

    def measure(function: object) -> tuple[float, object, float]:
        for _ in range(args.warmup):
            function()
        torch.cuda.synchronize()
        samples = []
        output = None
        scalar = 0.0
        for _ in range(args.iterations):
            torch.cuda.synchronize()
            start = time.perf_counter()
            output, scalar = function()
            torch.cuda.synchronize()
            samples.append((time.perf_counter() - start) * 1_000)
        return statistics.median(samples), output, scalar

    baseline_ms, baseline_output, baseline_scalar = measure(baseline)
    optimized_ms, optimized_output, optimized_scalar = measure(optimized)
    correct = (
        bool(
            torch.allclose(
                baseline_output.to(torch.bfloat16),
                optimized_output,
                rtol=2e-2,
                atol=2e-2,
            )
        )
        and abs(baseline_scalar - optimized_scalar) < 0.05
    )
    if not correct:
        raise SystemExit("Optimized capstone output failed the numerical tolerance.")
    target = write_result(
        args,
        lab_id="09_capstone",
        environment=environment,
        measurements={
            "baseline_median_ms": round(baseline_ms, 4),
            "optimized_median_ms": round(optimized_ms, 4),
            "observed_speedup": round(baseline_ms / optimized_ms, 3),
        },
        correctness={"allclose_with_mixed_precision_tolerance": True},
    )
    print(f"Completed optimization capstone: {target}")


if __name__ == "__main__":
    main()
