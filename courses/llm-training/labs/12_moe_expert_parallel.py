"""Measure expert-parallel routing, gradients, updates, and load balance."""

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
    rank, world_size, _ = init_nccl(torch)
    try:
        if world_size != 2:
            raise SystemExit("This bounded mechanics lab requires exactly two ranks.")
        hidden = 1_024 if args.profile == "smoke" else 8_192
        tokens_per_rank = 32 if args.profile == "smoke" else 512
        learning_rate = 1e-3
        send_counts = [3 * tokens_per_rank // 4, tokens_per_rank // 4]
        receive_counts = [send_counts[rank]] * world_size
        routed = torch.empty(
            (tokens_per_rank, hidden), device="cuda", dtype=torch.bfloat16
        )
        cursor = 0
        for destination, count in enumerate(send_counts):
            routed[cursor : cursor + count].fill_(rank * 10 + destination + 1)
            cursor += count
        received = torch.empty(
            (sum(receive_counts), hidden), device="cuda", dtype=torch.bfloat16
        )
        returned_output = torch.empty(
            (tokens_per_rank, hidden), device="cuda", dtype=torch.float32
        )
        returned_gradient = torch.empty_like(routed)
        expert_weight = torch.tensor(
            float(rank + 1), device="cuda", dtype=torch.float32, requires_grad=True
        )
        local_experts = 2
        expert_width = 256 if args.profile == "smoke" else 1_024
        grouped_weights = torch.randn(
            (local_experts, hidden, expert_width),
            device="cuda",
            dtype=torch.bfloat16,
        )

        def loop_experts() -> object:
            output = torch.empty(
                (received.shape[0], expert_width),
                device="cuda",
                dtype=torch.bfloat16,
            )
            for expert in range(local_experts):
                indices = torch.arange(
                    expert,
                    received.shape[0],
                    local_experts,
                    device="cuda",
                )
                output[indices] = received[indices] @ grouped_weights[expert]
            return output

        def grouped_experts() -> object:
            expert_indices = [
                torch.arange(
                    expert,
                    received.shape[0],
                    local_experts,
                    device="cuda",
                )
                for expert in range(local_experts)
            ]
            maximum_tokens = max(int(indices.numel()) for indices in expert_indices)
            padded = torch.zeros(
                (local_experts, maximum_tokens, hidden),
                device="cuda",
                dtype=torch.bfloat16,
            )
            for expert, indices in enumerate(expert_indices):
                padded[expert, : indices.numel()] = received[indices]
            grouped = torch.bmm(padded, grouped_weights)
            output = torch.empty(
                (received.shape[0], expert_width),
                device="cuda",
                dtype=torch.bfloat16,
            )
            for expert, indices in enumerate(expert_indices):
                output[indices] = grouped[expert, : indices.numel()]
            return output

        def measure_experts(operation: object) -> list[float]:
            for _ in range(args.warmup):
                operation()
            torch.cuda.synchronize()
            samples = []
            for _ in range(args.iterations):
                started = torch.cuda.Event(enable_timing=True)
                finished = torch.cuda.Event(enable_timing=True)
                started.record()
                operation()
                finished.record()
                finished.synchronize()
                samples.append(float(started.elapsed_time(finished)))
            return samples

        def train_step(*, update: bool) -> tuple[float, float, float]:
            expert_weight.grad = None
            torch.distributed.barrier()
            step_started = time.perf_counter()
            forward_started = torch.cuda.Event(enable_timing=True)
            forward_finished = torch.cuda.Event(enable_timing=True)
            forward_started.record()
            torch.distributed.all_to_all_single(
                received,
                routed,
                output_split_sizes=receive_counts,
                input_split_sizes=send_counts,
            )
            forward_finished.record()
            expert_output = received.float() * expert_weight
            loss = expert_output.sum() / received.numel()
            loss.backward()
            returned_output_local = expert_output.detach()
            backward_started = torch.cuda.Event(enable_timing=True)
            backward_finished = torch.cuda.Event(enable_timing=True)
            backward_started.record()
            torch.distributed.all_to_all_single(
                returned_output,
                returned_output_local,
                output_split_sizes=send_counts,
                input_split_sizes=receive_counts,
            )
            received_gradient = torch.ones_like(received)
            received_gradient.mul_(
                expert_weight.detach().to(torch.bfloat16) / received.numel()
            )
            torch.distributed.all_to_all_single(
                returned_gradient,
                received_gradient,
                output_split_sizes=send_counts,
                input_split_sizes=receive_counts,
            )
            backward_finished.record()
            if update:
                with torch.no_grad():
                    expert_weight.add_(expert_weight.grad, alpha=-learning_rate)
            backward_finished.synchronize()
            torch.cuda.synchronize()
            elapsed = torch.tensor(
                (time.perf_counter() - step_started) * 1_000,
                device="cuda",
                dtype=torch.float64,
            )
            torch.distributed.all_reduce(elapsed, op=torch.distributed.ReduceOp.MAX)
            return (
                float(elapsed.item()),
                float(forward_started.elapsed_time(forward_finished)),
                float(backward_started.elapsed_time(backward_finished)),
            )

        initial_weight = float(expert_weight.detach().item())
        train_step(update=True)
        loop_output = loop_experts()
        grouped_output = grouped_experts()
        grouped_matches_loop = bool(
            torch.allclose(loop_output, grouped_output, rtol=1e-2, atol=1e-2)
        )
        loop_expert_samples = measure_experts(loop_experts)
        grouped_expert_samples = measure_experts(grouped_experts)
        expected_output = routed.float().clone()
        expected_token_gradient = torch.empty_like(routed)
        cursor = 0
        for destination, count in enumerate(send_counts):
            expected_output[cursor : cursor + count].mul_(destination + 1)
            destination_received_elements = (
                world_size * send_counts[destination] * hidden
            )
            expected_token_gradient[cursor : cursor + count].fill_(
                (destination + 1) / destination_received_elements
            )
            cursor += count
        expected_weight_gradient = float(received.float().mean().item())
        expected_updated_weight = (
            initial_weight - learning_rate * expected_weight_gradient
        )
        forward_ok = bool(torch.equal(returned_output, expected_output))
        token_gradient_ok = bool(
            torch.equal(returned_gradient, expected_token_gradient)
        )
        weight_gradient_ok = (
            abs(float(expert_weight.grad.item()) - expected_weight_gradient) < 1e-5
        )
        update_ok = (
            abs(float(expert_weight.detach().item()) - expected_updated_weight) < 1e-5
        )
        local_ok = torch.tensor(
            int(
                forward_ok
                and token_gradient_ok
                and weight_gradient_ok
                and update_ok
                and grouped_matches_loop
            ),
            device="cuda",
            dtype=torch.int32,
        )
        torch.distributed.all_reduce(local_ok, op=torch.distributed.ReduceOp.MIN)
        if not bool(local_ok.item()):
            raise SystemExit(
                "Expert-parallel forward, gradient, update, or grouped work diverged."
            )

        for _ in range(args.warmup):
            train_step(update=True)
        step_samples: list[float] = []
        forward_communication: list[float] = []
        backward_communication: list[float] = []
        for _ in range(args.iterations):
            step_ms, forward_ms, backward_ms = train_step(update=True)
            step_samples.append(step_ms)
            forward_communication.append(forward_ms)
            backward_communication.append(backward_ms)

        global_load = [count * world_size for count in send_counts]
        if rank == 0:
            target = write_result(
                args,
                lab_id="12_moe_expert_parallel",
                environment={**environment, "rank_count": world_size},
                measurements={
                    "hidden_size": hidden,
                    "tokens_sent_to_experts_per_rank": send_counts,
                    "global_expert_token_load": global_load,
                    "expert_load_max_to_mean": round(
                        max(global_load) / statistics.mean(global_load), 4
                    ),
                    "local_experts_per_rank": local_experts,
                    "local_expert_token_counts": [
                        (received.shape[0] + local_experts - expert - 1)
                        // local_experts
                        for expert in range(local_experts)
                    ],
                    "expert_output_width": expert_width,
                    "loop_expert_gemm_median_ms": round(
                        statistics.median(loop_expert_samples), 4
                    ),
                    "padded_grouped_bmm_median_ms": round(
                        statistics.median(grouped_expert_samples), 4
                    ),
                    "grouped_operation_scope": (
                        "end-to-end padding, one batched GEMM, and scatter; compare "
                        "with specialized ragged grouped kernels before production use"
                    ),
                    "training_step_slowest_rank_median_ms": round(
                        statistics.median(step_samples), 4
                    ),
                    "forward_all_to_all_median_ms": round(
                        statistics.median(forward_communication), 4
                    ),
                    "output_and_gradient_return_median_ms": round(
                        statistics.median(backward_communication), 4
                    ),
                    "expert_weight_gradient": round(expected_weight_gradient, 6),
                    "learning_rate": learning_rate,
                    "training_evidence": [
                        "expert token load and imbalance",
                        "forward and backward communication",
                        "expert gradient and optimizer update",
                        "slowest-rank training step time",
                        "loop versus grouped local expert GEMM",
                    ],
                },
                correctness={
                    "routed_outputs_match_reference": True,
                    "returned_token_gradients_match_reference": True,
                    "expert_weight_gradient_matches_reference": True,
                    "optimizer_update_matches_reference": True,
                    "grouped_expert_output_matches_loop": grouped_matches_loop,
                },
            )
            print(f"Completed expert-parallel training experiment: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
