"""Route tokens to two rank-local experts with NCCL all-to-all collectives."""

from __future__ import annotations

import argparse

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    rank, world_size, _ = init_nccl(torch)
    try:
        hidden = 1_024 if args.profile == "smoke" else 8_192
        tokens_per_destination = 16 if args.profile == "smoke" else 256
        routed = torch.empty(
            (world_size, tokens_per_destination, hidden),
            device="cuda",
            dtype=torch.bfloat16,
        )
        for destination in range(world_size):
            routed[destination].fill_(rank * 10 + destination + 1)
        received = torch.empty_like(routed)
        returned = torch.empty_like(routed)

        def route() -> None:
            torch.distributed.all_to_all_single(received, routed)
            received.mul_(rank + 1)
            torch.distributed.all_to_all_single(returned, received)

        for _ in range(args.warmup):
            route()
        torch.cuda.synchronize()
        samples: list[float] = []
        for _ in range(args.iterations):
            started = torch.cuda.Event(enable_timing=True)
            ended = torch.cuda.Event(enable_timing=True)
            started.record()
            route()
            ended.record()
            ended.synchronize()
            samples.append(float(started.elapsed_time(ended)))
        expected = routed.clone()
        for destination in range(world_size):
            expected[destination].mul_(destination + 1)
        correct = bool(torch.equal(returned, expected))
        correct_tensor = torch.tensor(int(correct), device="cuda")
        torch.distributed.all_reduce(correct_tensor, op=torch.distributed.ReduceOp.MIN)
        if rank == 0:
            if not bool(correct_tensor):
                raise SystemExit(
                    "Expert routing did not return the expected rank-specific outputs."
                )
            target = write_result(
                args,
                lab_id="12_moe_expert_parallel",
                environment=environment,
                measurements={
                    "world_size": world_size,
                    "hidden_size": hidden,
                    "tokens_per_destination": tokens_per_destination,
                    "round_trip": summarize_ms(samples),
                },
                correctness={"all_ranks_routed_to_expected_experts": True},
            )
            print(f"Completed expert-parallel routing: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
