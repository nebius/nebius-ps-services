"""Measure inference tensor-parallel fit, latency, and communication mechanics."""

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


def partition_operations(torch, inputs, weight, rank: int, world_size: int):
    """Return forward-only column/all-gather and row/all-reduce operations."""
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

    return column_parallel, row_parallel


def validate_outputs(torch, reference, column_output, row_output) -> dict[str, float]:
    """Check finite results and the recipe-specific BF16 error budgets."""
    if not all(
        bool(torch.isfinite(value).all().item())
        for value in (reference, column_output, row_output)
    ):
        raise ValueError("Tensor-parallel output/reference contains non-finite values.")
    differences = [
        output.float() - reference.float() for output in (column_output, row_output)
    ]
    denominator = torch.linalg.vector_norm(reference.float()).clamp_min(1e-12)
    max_error = max(
        float((torch.linalg.vector_norm(delta) / denominator).item())
        for delta in differences
    )
    # Row partitioning changes the BF16 reduction order; relative L2 bounds its
    # aggregate error without a misleading pointwise test near cancellation.
    if max_error >= 0.02 or not torch.allclose(
        column_output, reference, rtol=1e-2, atol=1e-2
    ):
        raise ValueError(
            f"Tensor-parallel outputs diverged from reference: {max_error=}"
        )
    return {
        "maximum_relative_l2": max_error,
        "maximum_absolute_error": max(
            float(delta.abs().max().item()) for delta in differences
        ),
    }


def run_experiment(
    torch, args, environment, rank: int, world_size: int, local_rank: int
):
    if world_size != 2:
        raise SystemExit("This bounded mechanics lab requires exactly two ranks.")
    device = f"cuda:{local_rank}"
    seed_everything(torch, args.seed)
    width = 2_048 if args.profile == "smoke" else 8_192
    batch = (
        args.batch_size
        if args.batch_size is not None
        else (32 if args.profile == "smoke" else 128)
    )
    inputs = torch.empty((batch, width), device=device, dtype=torch.bfloat16)
    weight = torch.empty((width, width), device=device, dtype=torch.bfloat16)
    if rank == 0:
        inputs.normal_()
        weight.normal_()
    # A shared seed is not a substitute for proving that ranks use one model.
    torch.distributed.broadcast(inputs, src=0)
    torch.distributed.broadcast(weight, src=0)
    reference = inputs @ weight.T
    column_parallel, row_parallel = partition_operations(
        torch, inputs, weight, rank, world_size
    )

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
    errors = {}
    failure = None
    try:
        errors = validate_outputs(torch, reference, column_output, row_output)
    except ValueError as exc:
        failure = str(exc)
    # Every rank participates before any rank exits for numerical failure.
    local_ok = torch.tensor(int(failure is None), device=device, dtype=torch.int32)
    torch.distributed.all_reduce(local_ok, op=torch.distributed.ReduceOp.MIN)
    if not bool(local_ok.item()):
        raise SystemExit(
            failure or "Tensor-parallel reference check failed on another rank."
        )
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
                "parameter_shard_bytes_per_rank": weight.numel()
                * weight.element_size()
                // world_size,
                "logical_collective_tensor_bytes": logical_output_bytes,
                "inference_contract": [
                    "weight fit per rank",
                    "operator communication latency",
                    "companion vllm_two_node tp mode measures live TTFT and throughput",
                ],
                "model_fit_fraction_per_rank": round(1 / world_size, 4),
                "timing_scope": "slowest rank per iteration",
                "column_parallel_all_gather_median_ms": round(
                    statistics.median(column_samples), 4
                ),
                "row_parallel_all_reduce_median_ms": round(
                    statistics.median(row_samples), 4
                ),
                "column_parallel_all_gather_samples_ms": column_samples,
                "row_parallel_all_reduce_samples_ms": row_samples,
                **errors,
            },
            correctness={
                "column_parallel_matches_reference": True,
                "row_parallel_matches_reference": True,
            },
        )
        print(f"Completed tensor-parallel linear experiment: {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Input rows per forward operation; use 1 to examine small-message latency.",
    )
    args = parser.parse_args()
    validate_common_args(args)
    if args.batch_size is not None and args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    torch = load_torch()
    environment = require_h100(torch)
    rank, world_size, local_rank = init_nccl(torch)
    try:
        run_experiment(torch, args, environment, rank, world_size, local_rank)
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
