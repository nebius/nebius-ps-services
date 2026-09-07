"""Measure a correct two-node FP32 all-reduce curve under one NCCL setting."""

from __future__ import annotations

import argparse
import os
import statistics
import time

from common import (
    add_common_args,
    close_distributed,
    init_nccl,
    load_torch,
    require_h100,
    summarize_ms,
    validate_common_args,
    write_result,
)
from networking import PROFILES, message_sizes, profile_settings


def summarize_curve(
    size: int, world: int, samples: list[float], wall_samples: list[float]
) -> dict:
    median_seconds = statistics.median(samples) / 1000
    if median_seconds <= 0:
        raise ValueError("Non-positive measured duration")
    algbw = size / median_seconds / 1e9
    return {
        "bytes": size,
        "slowest_rank_samples_ms": samples,
        **summarize_ms(samples),
        "slowest_rank_wall_samples_ms": wall_samples,
        "wall_summary": summarize_ms(wall_samples),
        "algbw_GBps_at_median": algbw,
        "normalized_busbw_GBps_at_median": algbw * 2 * (world - 1) / world,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--variant", choices=tuple(PROFILES), default="default")
    parser.add_argument("--min-bytes", type=int, default=8)
    parser.add_argument("--max-bytes", type=int, default=64 * 2**20)
    args = parser.parse_args()
    validate_common_args(args)
    if not 1 <= args.warmup <= 100 or not 3 <= args.iterations <= 1000:
        parser.error("Use 1–100 warmups and 3–1000 measured iterations")
    try:
        sizes = message_sizes(args.min_bytes, args.max_bytes)
        overrides = profile_settings(args.variant, os.environ)
    except ValueError as exc:
        parser.error(str(exc))
    # NCCL reads settings when the communicator is created, never mid-experiment.
    os.environ.update(overrides)
    torch = load_torch()
    environment = require_h100(torch)
    rank, world, local_rank = init_nccl(torch)
    expected = world * (world + 1) // 2
    records = []
    try:
        for size in sizes:
            values = torch.empty(
                size // 4, dtype=torch.float32, device=f"cuda:{local_rank}"
            )
            start = torch.cuda.Event(enable_timing=True)
            stop = torch.cuda.Event(enable_timing=True)
            samples = []
            wall_samples = []
            correct = True
            for iteration in range(args.warmup + args.iterations):
                values.fill_(rank + 1)
                torch.cuda.synchronize()
                torch.distributed.barrier()
                torch.cuda.synchronize()
                wall_start = time.perf_counter()
                start.record()
                work = torch.distributed.all_reduce(values, async_op=True)
                work.wait()  # Join NCCL's stream before recording the stop event.
                stop.record()
                stop.synchronize()
                wall_ms = (time.perf_counter() - wall_start) * 1000
                if iteration >= args.warmup:
                    samples.append(float(start.elapsed_time(stop)))
                    wall_samples.append(wall_ms)
                correct = correct and bool(torch.all(values == expected).item())
            sample_tensor = torch.tensor(
                samples, dtype=torch.float64, device=values.device
            )
            torch.distributed.all_reduce(
                sample_tensor, op=torch.distributed.ReduceOp.MAX
            )
            wall_tensor = torch.tensor(
                wall_samples, dtype=torch.float64, device=values.device
            )
            torch.distributed.all_reduce(wall_tensor, op=torch.distributed.ReduceOp.MAX)
            check = torch.tensor(int(correct), device=values.device)
            torch.distributed.all_reduce(check, op=torch.distributed.ReduceOp.MIN)
            if not bool(check.item()):
                raise SystemExit("All-reduce correctness failed on at least one rank")
            records.append(
                summarize_curve(
                    size, world, sample_tensor.tolist(), wall_tensor.tolist()
                )
            )
        if rank == 0:
            target = write_result(
                args,
                lab_id="17_nccl_transport_sweep",
                environment={
                    **environment,
                    "rank_count": world,
                    "nccl_version": list(torch.cuda.nccl.version()),
                },
                measurements={
                    "variant": args.variant,
                    "job_local_overrides": overrides,
                    "acceptance_timing": os.environ.get("NCCL_DEBUG", "WARN")
                    in ("WARN", "VERSION"),
                    "dtype": "float32",
                    "collective": "all_reduce_sum",
                    "timing_scope": "maximum rank CUDA-event time per synchronized iteration",
                    "warmup": args.warmup,
                    "iterations": args.iterations,
                    "curve": records,
                    "transport_and_gdr": "not inferred; correlate separate diagnostic evidence",
                },
                correctness={"all_sizes_exact_on_all_ranks": True},
            )
            print(f"Communication sweep completed: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
