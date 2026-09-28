"""Eight-/sixteen-rank experiments; all timing uses the slowest participating rank."""

from __future__ import annotations

import os
import statistics
import time

from common import init_nccl, load_torch, require_course_gpu, seed_everything
from course_evidence import annotated_operation


def initialize(seed):
    torch = load_torch()
    world = int(os.environ.get("WORLD_SIZE", "0"))
    if world not in (8, 16) or int(os.environ.get("LOCAL_WORLD_SIZE", "0")) != 8:
        raise SystemExit("Use fabric.sbatch: eight GPUs per node, one or two nodes.")
    rank, world, local = init_nccl(torch, expected_world_size=world)
    environment = {**require_course_gpu(torch), "rank_count": world, "gpus_per_node": 8}
    seed_everything(torch, seed)
    return torch, rank, world, local, environment


def all_correct(torch, value, message):
    flag = torch.tensor(int(bool(value)), device="cuda", dtype=torch.int32)
    torch.distributed.all_reduce(flag, op=torch.distributed.ReduceOp.MIN)
    if not flag.item():
        raise SystemExit(message)


def measure(torch, operation, warmup, iterations):
    warming = annotated_operation(operation, "course_warmup")
    measured = annotated_operation(operation, "course_measure")
    for _ in range(warmup):
        warming()
    torch.cuda.synchronize()
    samples = []
    for _ in range(iterations):
        torch.distributed.barrier()
        torch.cuda.synchronize()
        start = time.perf_counter()
        measured()
        torch.cuda.synchronize()
        elapsed = torch.tensor(
            (time.perf_counter() - start) * 1000, dtype=torch.float64, device="cuda"
        )
        torch.distributed.all_reduce(elapsed, op=torch.distributed.ReduceOp.MAX)
        samples.append(elapsed.item())
    return {
        "median_ms": statistics.median(samples),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "samples_ms": samples,
    }
