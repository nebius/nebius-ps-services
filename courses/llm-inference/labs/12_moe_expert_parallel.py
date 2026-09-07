"""Measure inference expert routing, token-load balance, and latency mechanics."""

from __future__ import annotations

import argparse
import statistics

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
        if world_size != 2:
            raise SystemExit("This bounded mechanics lab requires exactly two ranks.")
        hidden = 1_024 if args.profile == "smoke" else 8_192
        tokens_per_rank = 32 if args.profile == "smoke" else 512
        send_counts = [3 * tokens_per_rank // 4, tokens_per_rank // 4]
        receive_counts = [send_counts[rank]] * world_size
        routed = torch.empty(
            (tokens_per_rank, hidden),
            device="cuda",
            dtype=torch.bfloat16,
        )
        cursor = 0
        for destination, count in enumerate(send_counts):
            routed[cursor : cursor + count].fill_(rank * 10 + destination + 1)
            cursor += count
        received = torch.empty(
            (sum(receive_counts), hidden), device="cuda", dtype=torch.bfloat16
        )
        returned = torch.empty_like(routed)

        def route() -> None:
            torch.distributed.all_to_all_single(
                received,
                routed,
                output_split_sizes=receive_counts,
                input_split_sizes=send_counts,
            )
            received.mul_(rank + 1)
            torch.distributed.all_to_all_single(
                returned,
                received,
                output_split_sizes=send_counts,
                input_split_sizes=receive_counts,
            )

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
        cursor = 0
        for destination, count in enumerate(send_counts):
            expected[cursor : cursor + count].mul_(destination + 1)
            cursor += count
        correct = bool(torch.equal(returned, expected))
        correct_tensor = torch.tensor(int(correct), device="cuda")
        torch.distributed.all_reduce(correct_tensor, op=torch.distributed.ReduceOp.MIN)
        global_expert_token_load = [count * world_size for count in send_counts]
        if rank == 0:
            if not bool(correct_tensor):
                raise SystemExit(
                    "Expert routing did not return the expected rank-specific outputs."
                )
            target = write_result(
                args,
                lab_id="12_moe_expert_parallel",
                environment={**environment, "rank_count": world_size},
                measurements={
                    "world_size": world_size,
                    "hidden_size": hidden,
                    "tokens_sent_to_experts_per_rank": send_counts,
                    "global_expert_token_load": global_expert_token_load,
                    "expert_load_max_to_mean": round(
                        max(global_expert_token_load)
                        / statistics.mean(global_expert_token_load),
                        4,
                    ),
                    "toy_expert_parameter_fraction_per_rank": round(1 / world_size, 4),
                    "parameter_fraction_scope": (
                        "expert parameters only; shared and non-expert parameters "
                        "remain replicated in expert parallelism"
                    ),
                    "inference_evidence": [
                        "request token load",
                        "round-trip latency",
                        "toy expert-parameter ownership fraction",
                        "companion vllm_two_node ep mode measures live TTFT and throughput",
                    ],
                    "round_trip": summarize_ms(samples),
                },
                correctness={"all_ranks_routed_to_expected_experts": True},
            )
            print(f"Completed expert-parallel routing: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
