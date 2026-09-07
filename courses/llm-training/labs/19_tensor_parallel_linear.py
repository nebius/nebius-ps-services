"""Measure tensor-parallel forward, gradient, update, and communication mechanics."""

from __future__ import annotations

import argparse
import math
import statistics
import time

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


def relative_l2(torch: object, observed: object, expected: object) -> float:
    # Return a failing value so every rank can reach the collective verdict.
    if not bool(torch.isfinite(observed).all() and torch.isfinite(expected).all()):
        return math.inf
    numerator = torch.linalg.vector_norm(observed.float() - expected.float())
    denominator = torch.linalg.vector_norm(expected.float()).clamp_min(1e-12)
    if not bool(torch.isfinite(numerator) and torch.isfinite(denominator)):
        return math.inf
    error = float((numerator / denominator).item())
    return error if math.isfinite(error) else math.inf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    rank, world_size, local_rank = init_nccl(torch)
    try:
        device = f"cuda:{local_rank}"
        width = 2_048 if args.profile == "smoke" else 8_192
        batch = 32 if args.profile == "smoke" else 128
        learning_rate = 1e-2
        if width % world_size:
            raise SystemExit("The hidden width must be divisible by the rank count.")
        input_seed = torch.randn((batch, width), device=device, dtype=torch.bfloat16)
        weight_seed = torch.randn((width, width), device=device, dtype=torch.float32)

        reference_input = input_seed.detach().clone().requires_grad_(True)
        reference_weight = weight_seed.detach().clone().requires_grad_(True)
        reference_output = reference_input @ reference_weight.to(torch.bfloat16).T
        reference_loss = reference_output.float().square().mean()
        reference_loss.backward()
        with torch.no_grad():
            reference_updated = reference_weight - learning_rate * reference_weight.grad

        shard_input = input_seed.detach().clone().requires_grad_(True)
        shard_weight = (
            weight_seed.chunk(world_size, dim=0)[rank]
            .contiguous()
            .detach()
            .requires_grad_(True)
        )
        local_output = shard_input @ shard_weight.to(torch.bfloat16).T
        local_loss = local_output.float().square().sum() / reference_output.numel()
        local_loss.backward()
        torch.distributed.all_reduce(shard_input.grad)
        gathered_output = [torch.empty_like(local_output) for _ in range(world_size)]
        torch.distributed.all_gather(gathered_output, local_output.detach())
        with torch.no_grad():
            shard_updated = shard_weight - learning_rate * shard_weight.grad
        gathered_updated = [torch.empty_like(shard_updated) for _ in range(world_size)]
        torch.distributed.all_gather(gathered_updated, shard_updated)
        forward_error = relative_l2(
            torch, torch.cat(gathered_output, dim=-1), reference_output
        )
        weight_gradient_error = relative_l2(
            torch,
            shard_weight.grad,
            reference_weight.grad.chunk(world_size, dim=0)[rank],
        )
        input_gradient_error = relative_l2(
            torch, shard_input.grad, reference_input.grad
        )
        update_error = relative_l2(
            torch, torch.cat(gathered_updated, dim=0), reference_updated
        )

        row_weight = (
            weight_seed.chunk(world_size, dim=1)[rank].contiguous().to(torch.bfloat16)
        )
        row_input = input_seed.chunk(world_size, dim=1)[rank].contiguous()
        row_output = row_input @ row_weight.T
        torch.distributed.all_reduce(row_output)
        row_forward_error = relative_l2(torch, row_output, reference_output)
        maximum_error = max(
            forward_error,
            weight_gradient_error,
            input_gradient_error,
            update_error,
            row_forward_error,
        )
        local_ok = torch.tensor(
            int(maximum_error < 0.02), device=device, dtype=torch.int32
        )
        torch.distributed.all_reduce(local_ok, op=torch.distributed.ReduceOp.MIN)
        if not bool(local_ok.item()):
            raise SystemExit(f"Tensor-parallel equivalence failed: {maximum_error=}")

        timed_input = input_seed.detach().clone().requires_grad_(True)
        timed_weight = (
            weight_seed.chunk(world_size, dim=0)[rank]
            .contiguous()
            .detach()
            .requires_grad_(True)
        )

        def train_step() -> tuple[float, float]:
            timed_input.grad = None
            timed_weight.grad = None
            torch.distributed.barrier()
            started = time.perf_counter()
            output = timed_input @ timed_weight.to(torch.bfloat16).T
            loss = output.float().square().sum() / (batch * width)
            loss.backward()
            communication_started = torch.cuda.Event(enable_timing=True)
            communication_finished = torch.cuda.Event(enable_timing=True)
            communication_started.record()
            torch.distributed.all_reduce(timed_input.grad)
            communication_finished.record()
            with torch.no_grad():
                timed_weight.add_(timed_weight.grad, alpha=-learning_rate)
            communication_finished.synchronize()
            torch.cuda.synchronize()
            elapsed = torch.tensor(
                (time.perf_counter() - started) * 1_000,
                device=device,
                dtype=torch.float64,
            )
            torch.distributed.all_reduce(elapsed, op=torch.distributed.ReduceOp.MAX)
            return float(elapsed.item()), float(
                communication_started.elapsed_time(communication_finished)
            )

        for _ in range(args.warmup):
            train_step()
        step_samples: list[float] = []
        communication_samples: list[float] = []
        for _ in range(args.iterations):
            step_ms, communication_ms = train_step()
            step_samples.append(step_ms)
            communication_samples.append(communication_ms)

        if rank == 0:
            target = write_result(
                args,
                lab_id="19_tensor_parallel_linear",
                environment={**environment, "rank_count": world_size},
                measurements={
                    "batch": batch,
                    "width": width,
                    "learning_rate": learning_rate,
                    "replicated_parameter_bytes": weight_seed.numel()
                    * weight_seed.element_size(),
                    "parameter_shard_bytes_per_rank": shard_weight.numel()
                    * shard_weight.element_size(),
                    "input_gradient_all_reduce_bytes": timed_input.numel()
                    * timed_input.element_size(),
                    "training_step_slowest_rank_median_ms": round(
                        statistics.median(step_samples), 4
                    ),
                    "input_gradient_all_reduce_median_ms": round(
                        statistics.median(communication_samples), 4
                    ),
                    "forward_relative_l2": round(forward_error, 8),
                    "weight_gradient_relative_l2": round(weight_gradient_error, 8),
                    "input_gradient_relative_l2": round(input_gradient_error, 8),
                    "parameter_update_relative_l2": round(update_error, 8),
                    "row_parallel_forward_relative_l2": round(row_forward_error, 8),
                },
                correctness={
                    "column_forward_matches_reference": True,
                    "weight_gradient_matches_reference": True,
                    "input_gradient_matches_reference": True,
                    "optimizer_update_matches_reference": True,
                    "row_forward_matches_reference": True,
                },
            )
            print(f"Completed tensor-parallel training experiment: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
