"""Measure and verify a two-node NCCL all-reduce."""

from __future__ import annotations

import argparse
import statistics
import time

from common import (
    add_common_args,
    close_distributed,
    init_nccl,
    load_torch,
    require_h100,
    resolve_int_override,
    validate_common_args,
    write_result,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--payload-mib", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    payload_mib = resolve_int_override(
        args.payload_mib,
        8 if args.profile == "smoke" else 256,
        option="--payload-mib",
    )
    torch = load_torch()
    environment = require_h100(torch)
    rank, world_size, _ = init_nccl(torch)
    elements = payload_mib * 2**20 // 4
    tensor = torch.empty(elements, device="cuda", dtype=torch.float32)
    for _ in range(args.warmup):
        tensor.fill_(rank + 1)
        torch.distributed.all_reduce(tensor)
    torch.cuda.synchronize()
    samples = []
    for _ in range(args.iterations):
        tensor.fill_(rank + 1)
        torch.distributed.barrier()
        start = time.perf_counter()
        torch.distributed.all_reduce(tensor)
        torch.cuda.synchronize()
        samples.append((time.perf_counter() - start) * 1_000)
    correct = bool(torch.all(tensor == 3.0).item())
    if not correct:
        raise SystemExit("All-reduce result was not the sum of both ranks.")
    if rank == 0:
        median_ms = statistics.median(samples)
        target = write_result(
            args,
            lab_id="06_distributed_collectives",
            environment={**environment, "rank_count": world_size, "node_count": 2},
            measurements={
                "payload_mib": payload_mib,
                "median_ms": round(median_ms, 4),
                "effective_payload_gib_per_s": round(
                    (payload_mib / 1024) / (median_ms / 1_000), 2
                ),
            },
            correctness={"all_reduce_sum": True},
        )
        print(f"Completed collective benchmark: {target}")
    close_distributed(torch)


if __name__ == "__main__":
    main()
