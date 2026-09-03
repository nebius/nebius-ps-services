"""Compare serialized and overlapped compute plus two-node all-reduce."""

from __future__ import annotations

import argparse
import math
import statistics
import time
from typing import Callable

from common import (
    add_common_args,
    close_distributed,
    init_nccl,
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
    rank, world_size, local_rank = init_nccl(torch)
    device = f"cuda:{local_rank}"
    matrix_size = 2_048 if args.profile == "smoke" else 4_096
    collective_mib = 64 if args.profile == "smoke" else 512
    element_count = collective_mib * 2**20 // 2
    collective = torch.empty(element_count, device=device, dtype=torch.bfloat16)
    left = torch.randn((matrix_size, matrix_size), device=device, dtype=torch.bfloat16)
    right = torch.randn_like(left)
    product = torch.empty_like(left)
    communication_stream = torch.cuda.Stream(device=local_rank)
    default_stream = torch.cuda.default_stream(device=local_rank)

    def serialized() -> None:
        collective.fill_(rank + 1)
        torch.distributed.all_reduce(collective)
        torch.mm(left, right, out=product)

    def overlapped() -> None:
        collective.fill_(rank + 1)
        communication_stream.wait_stream(default_stream)
        with torch.cuda.stream(communication_stream):
            work = torch.distributed.all_reduce(collective, async_op=True)
        torch.mm(left, right, out=product)
        work.wait()
        default_stream.wait_stream(communication_stream)

    def measure(operation: Callable[[], None]) -> list[float]:
        for _ in range(args.warmup):
            operation()
        torch.cuda.synchronize()
        samples: list[float] = []
        for _ in range(args.iterations):
            torch.distributed.barrier()
            started = time.perf_counter()
            operation()
            torch.cuda.synchronize()
            elapsed = torch.tensor(
                (time.perf_counter() - started) * 1_000,
                device=device,
                dtype=torch.float64,
            )
            torch.distributed.all_reduce(elapsed, op=torch.distributed.ReduceOp.MAX)
            samples.append(float(elapsed.item()))
        return samples

    serialized_samples = measure(serialized)
    overlapped_samples = measure(overlapped)
    overlapped()
    torch.cuda.synchronize()
    expected = float(sum(range(1, world_size + 1)))
    collective_ok = bool(
        torch.all(collective == torch.tensor(expected, device=device)).item()
    )
    compute_ok = bool(torch.isfinite(product).all().item())
    local_ok = torch.tensor(
        int(collective_ok and compute_ok), device=device, dtype=torch.int32
    )
    torch.distributed.all_reduce(local_ok, op=torch.distributed.ReduceOp.MIN)
    if not bool(local_ok.item()):
        raise SystemExit("At least one rank failed the overlap correctness gates.")
    if rank == 0:
        serialized_median = statistics.median(serialized_samples)
        overlapped_median = statistics.median(overlapped_samples)
        target = write_result(
            args,
            lab_id="13_collective_overlap",
            environment={**environment, "rank_count": world_size},
            measurements={
                "matrix_size": matrix_size,
                "collective_mib": collective_mib,
                "timing_scope": "slowest rank per iteration",
                "serialized_median_ms": round(serialized_median, 4),
                "overlapped_median_ms": round(overlapped_median, 4),
                "serialized_to_overlap_ratio": round(
                    serialized_median / overlapped_median, 4
                ),
            },
            correctness={
                "all_reduce_exact": True,
                "compute_is_finite": True,
                "ratio_is_finite": math.isfinite(serialized_median / overlapped_median),
            },
        )
        print(f"Completed collective-overlap experiment: {target}")
    close_distributed(torch)


if __name__ == "__main__":
    main()
