"""Compare CPU, transfer-inclusive GPU, and resident-GPU vector work."""

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    return parser.parse_args()


def cpu_time_ms(x: object, y: object, iterations: int) -> float:
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        _ = x * y + x
        samples.append((time.perf_counter() - start) * 1_000)
    return statistics.median(samples)


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    sizes = [1_024, 1_000_000]
    if args.profile == "h100":
        sizes.append(32_000_000)
    rows = []
    all_correct = True
    for size in sizes:
        x_cpu = torch.randn(size, dtype=torch.float32)
        y_cpu = torch.randn(size, dtype=torch.float32)
        x_gpu = x_cpu.to("cuda")
        y_gpu = y_cpu.to("cuda")
        resident = cuda_times_ms(
            torch,
            lambda: x_gpu * y_gpu + x_gpu,
            warmup=args.warmup,
            iterations=args.iterations,
        )
        transfer_samples = []
        for _ in range(args.iterations):
            start = time.perf_counter()
            transferred_x = x_cpu.to("cuda")
            transferred_y = y_cpu.to("cuda")
            result_gpu = transferred_x * transferred_y + transferred_x
            result = result_gpu.cpu()
            transfer_samples.append((time.perf_counter() - start) * 1_000)
        reference = x_cpu * y_cpu + x_cpu
        correct = bool(torch.allclose(reference, result, rtol=1e-5, atol=1e-6))
        all_correct = all_correct and correct
        rows.append(
            {
                "elements": size,
                "cpu_median_ms": round(cpu_time_ms(x_cpu, y_cpu, args.iterations), 4),
                "gpu_resident": summarize_ms(resident),
                "gpu_with_transfers_median_ms": round(
                    statistics.median(transfer_samples), 4
                ),
            }
        )
    if not all_correct:
        raise SystemExit("CPU and GPU results diverged.")
    target = write_result(
        args,
        lab_id="01_cpu_gpu_crossover",
        environment=environment,
        measurements={"sizes": rows},
        correctness={"allclose": True},
    )
    print(f"Completed crossover benchmark: {target}")


if __name__ == "__main__":
    main()
