"""Run the same training step on one H100 or two-node DDP."""

from __future__ import annotations

import argparse
import math
import os
import statistics
import time

from common import (
    add_common_args,
    close_distributed,
    configure_slurm_distributed_env,
    init_nccl,
    load_torch,
    require_h100,
    resolve_int_override,
    seed_everything,
    validate_common_args,
    write_result,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--global-batch", type=int, default=None)
    args = parser.parse_args()
    validate_common_args(args)
    global_batch = resolve_int_override(
        args.global_batch,
        64 if args.profile == "smoke" else 512,
        option="--global-batch",
    )
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    _, detected_world, _ = configure_slurm_distributed_env()
    distributed = detected_world > 1 or int(os.environ.get("SLURM_NTASKS", "1")) > 1
    if distributed:
        rank, world_size, local_rank = init_nccl(torch)
    else:
        rank, world_size, local_rank = 0, 1, 0
        torch.cuda.set_device(0)
    if global_batch % world_size:
        raise SystemExit("--global-batch must be divisible by the number of ranks")
    local_batch = global_batch // world_size
    width = 2_048 if args.profile == "smoke" else 8_192
    model = torch.nn.Sequential(
        torch.nn.Linear(width, width * 2, bias=False),
        torch.nn.GELU(),
        torch.nn.Linear(width * 2, width, bias=False),
    ).to(device=f"cuda:{local_rank}", dtype=torch.bfloat16)
    if distributed:
        model = torch.nn.parallel.DistributedDataParallel(
            model, device_ids=[local_rank]
        )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    global_inputs = torch.randn(
        (global_batch, width), device=f"cuda:{local_rank}", dtype=torch.bfloat16
    )
    first = rank * local_batch
    inputs = global_inputs[first : first + local_batch]

    def step() -> float:
        optimizer.zero_grad(set_to_none=True)
        loss = model(inputs).float().square().mean()
        loss.backward()
        optimizer.step()
        return float(loss.detach())

    for _ in range(args.warmup):
        step()
    torch.cuda.synchronize()
    samples = []
    last_loss = 0.0
    for _ in range(args.iterations):
        if distributed:
            torch.distributed.barrier()
        start = time.perf_counter()
        last_loss = step()
        torch.cuda.synchronize()
        elapsed = torch.tensor(
            (time.perf_counter() - start) * 1_000,
            device=f"cuda:{local_rank}",
            dtype=torch.float64,
        )
        if distributed:
            torch.distributed.all_reduce(elapsed, op=torch.distributed.ReduceOp.MAX)
        samples.append(float(elapsed.item()))
    finite = torch.tensor(int(math.isfinite(last_loss)), device=f"cuda:{local_rank}")
    if distributed:
        torch.distributed.all_reduce(finite, op=torch.distributed.ReduceOp.MIN)
    if not bool(finite):
        raise SystemExit("At least one rank produced a non-finite loss.")
    if rank == 0:
        median_ms = statistics.median(samples)
        target = write_result(
            args,
            lab_id=f"08_distributed_scaling_{world_size}rank",
            environment={**environment, "rank_count": world_size},
            measurements={
                "global_batch": global_batch,
                "timing_scope": "slowest rank per iteration",
                "median_step_ms": round(median_ms, 4),
                "samples_per_second": round(global_batch / (median_ms / 1_000), 2),
            },
            correctness={"all_ranks_finite_loss": True},
        )
        print(f"Completed {world_size}-rank scaling run: {target}")
    close_distributed(torch)


if __name__ == "__main__":
    main()
