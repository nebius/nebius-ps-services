"""Unpack column- and row-parallel linear layers across two H100 nodes."""

from __future__ import annotations

import argparse
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
    width = 2_048 if args.profile == "smoke" else 8_192
    batch = 32 if args.profile == "smoke" else 128
    if width % world_size:
        raise SystemExit("The hidden width must be divisible by the rank count.")
    inputs = torch.randn((batch, width), device=device, dtype=torch.bfloat16)
    weight = torch.randn((width, width), device=device, dtype=torch.bfloat16)
    reference = inputs @ weight.T
    column_weight = weight.chunk(world_size, dim=0)[rank].contiguous()
    row_weight = weight.chunk(world_size, dim=1)[rank].contiguous()
    row_input = inputs.chunk(world_size, dim=1)[rank].contiguous()

    def column_parallel() -> object:
        local_output = inputs @ column_weight.T
        gathered = [torch.empty_like(local_output) for _ in range(world_size)]
        torch.distributed.all_gather(gathered, local_output)
        return torch.cat(gathered, dim=-1)

    def row_parallel() -> object:
        output = row_input @ row_weight.T
        torch.distributed.all_reduce(output)
        return output

    def measure(operation: Callable[[], object]) -> list[float]:
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

    column_samples = measure(column_parallel)
    row_samples = measure(row_parallel)
    column_output = column_parallel()
    row_output = row_parallel()
    column_error = torch.linalg.vector_norm(
        column_output.float() - reference.float()
    ) / torch.linalg.vector_norm(reference.float()).clamp_min(1e-12)
    row_error = torch.linalg.vector_norm(
        row_output.float() - reference.float()
    ) / torch.linalg.vector_norm(reference.float()).clamp_min(1e-12)
    max_error = max(float(column_error.item()), float(row_error.item()))
    local_ok = torch.tensor(int(max_error < 0.02), device=device, dtype=torch.int32)
    torch.distributed.all_reduce(local_ok, op=torch.distributed.ReduceOp.MIN)
    if not bool(local_ok.item()):
        raise SystemExit(f"Tensor-parallel outputs diverged: relative L2={max_error}")
    if rank == 0:
        logical_output_bytes = reference.numel() * reference.element_size()
        target = write_result(
            args,
            lab_id="19_tensor_parallel_linear",
            environment={**environment, "rank_count": world_size},
            measurements={
                "batch": batch,
                "width": width,
                "replicated_reference_weight_bytes": weight.numel()
                * weight.element_size(),
                "parameter_shard_bytes_per_rank": column_weight.numel()
                * column_weight.element_size(),
                "logical_collective_tensor_bytes": logical_output_bytes,
                "timing_scope": "slowest rank per iteration",
                "column_parallel_all_gather_median_ms": round(
                    statistics.median(column_samples), 4
                ),
                "row_parallel_all_reduce_median_ms": round(
                    statistics.median(row_samples), 4
                ),
                "maximum_relative_l2": round(max_error, 8),
            },
            correctness={
                "column_parallel_matches_reference": True,
                "row_parallel_matches_reference": True,
            },
        )
        print(f"Completed tensor-parallel linear experiment: {target}")
    close_distributed(torch)


if __name__ == "__main__":
    main()
