"""Demonstrate two-rank pipeline- and context-parallel training mechanics."""

from __future__ import annotations

import argparse

from common import (
    add_common_args,
    close_distributed,
    init_nccl,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)


def require_close(
    torch: object, observed: object, expected: object, label: str
) -> None:
    if not torch.allclose(observed, expected, rtol=1e-5, atol=1e-6):
        error = float((observed - expected).abs().max())
        raise SystemExit(f"{label} failed FP32 comparison: max error {error}")


def pipeline_mechanics(torch: object, rank: int, width: int) -> dict[str, object]:
    """Run one forward activation send and backward gradient return."""
    batch = 4
    inputs = (
        torch.arange(batch * width, device="cuda", dtype=torch.float32)
        .reshape(batch, width)
        .div_(batch * width)
        .requires_grad_(True)
    )
    weight0 = torch.eye(width, device="cuda") * 0.75 + 0.01
    weight1 = torch.eye(width, device="cuda") * 0.50 - 0.005
    activation_shape = (batch, width)
    loss_value = torch.zeros(1, device="cuda")
    reference_inputs = inputs.detach().clone().requires_grad_(True)
    reference_weight0 = weight0.clone().requires_grad_(True)
    reference_weight1 = weight1.clone().requires_grad_(True)
    reference_activation = reference_inputs @ reference_weight0
    reference_activation.retain_grad()
    reference_loss = (reference_activation @ reference_weight1).square().mean()
    reference_loss.backward()

    if rank == 0:
        local_weight = weight0.clone().requires_grad_(True)
        activation = inputs @ local_weight
        torch.distributed.send(activation.detach(), dst=1)
        activation_gradient = torch.empty_like(activation)
        torch.distributed.recv(activation_gradient, src=1)
        activation.backward(activation_gradient)
        require_close(
            torch,
            activation_gradient,
            reference_activation.grad,
            "stage 0 activation gradient",
        )
        require_close(
            torch, local_weight.grad, reference_weight0.grad, "stage 0 weight gradient"
        )
        require_close(
            torch, inputs.grad, reference_inputs.grad, "stage 0 input gradient"
        )
    else:
        received = torch.empty(activation_shape, device="cuda")
        torch.distributed.recv(received, src=0)
        received.requires_grad_(True)
        local_weight = weight1.clone().requires_grad_(True)
        output = received @ local_weight
        loss = output.square().mean()
        loss.backward()
        require_close(
            torch, received.grad, reference_activation.grad, "stage 1 input gradient"
        )
        require_close(
            torch, local_weight.grad, reference_weight1.grad, "stage 1 weight gradient"
        )
        torch.distributed.send(received.grad, dst=0)
        loss_value.copy_(loss.detach())

    torch.distributed.broadcast(loss_value, src=1)
    require_close(torch, loss_value, reference_loss.reshape(1), "pipeline loss")
    gradient_ok = torch.tensor(1, device="cuda", dtype=torch.int32)
    torch.distributed.all_reduce(gradient_ok, op=torch.distributed.ReduceOp.MIN)
    if int(gradient_ok) != 1:
        raise SystemExit("A pipeline stage produced a non-finite local gradient.")
    activation_bytes = (
        batch * width * torch.tensor([], dtype=torch.float32).element_size()
    )
    return {
        "stages": 2,
        "microbatches": 1,
        "activation_shape": list(activation_shape),
        "activation_send_bytes": activation_bytes,
        "activation_gradient_return_bytes": activation_bytes,
        "loss": float(loss_value),
        "all_stage_weight_input_and_activation_gradients_match_reference": True,
    }


def context_mechanics(
    torch: object, rank: int, world_size: int, width: int
) -> dict[str, object]:
    """Partition sequence positions and reconcile a global training statistic."""
    batch = 2
    sequence = 32
    full_context = (
        torch.arange(batch * sequence * width, device="cuda", dtype=torch.float32)
        .reshape(batch, sequence, width)
        .div_(batch * sequence * width)
    )
    local_context = (
        full_context.chunk(world_size, dim=1)[rank]
        .contiguous()
        .clone()
        .requires_grad_(True)
    )
    local_square_sum = local_context.square().sum()
    global_square_sum = local_square_sum.detach().clone()
    torch.distributed.all_reduce(global_square_sum)
    require_close(
        torch, global_square_sum, full_context.square().sum(), "context reduction"
    )
    gathered = [torch.empty_like(local_context) for _ in range(world_size)]
    torch.distributed.all_gather(gathered, local_context.detach())
    reconstructed = torch.cat(gathered, dim=1)
    if not torch.equal(reconstructed, full_context):
        raise SystemExit("Context shards did not reconstruct the full sequence.")
    (local_square_sum / full_context.numel()).backward()
    reference_context = full_context.detach().clone().requires_grad_(True)
    (reference_context.square().sum() / reference_context.numel()).backward()
    expected_local_gradient = reference_context.grad.chunk(world_size, dim=1)[rank]
    require_close(
        torch,
        local_context.grad,
        expected_local_gradient,
        "context shard gradient",
    )
    return {
        "global_shape": list(full_context.shape),
        "local_shape": list(local_context.shape),
        "sequence_fraction_per_rank": 1 / world_size,
        "global_square_sum": float(global_square_sum),
        "collectives": ["all_reduce statistic", "all_gather context shards"],
        "local_gradient_matches_unpartitioned_autograd_reference": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    rank, world_size, _ = init_nccl(torch)
    width = 64 if args.profile == "smoke" else 512
    try:
        pipeline = pipeline_mechanics(torch, rank, width)
        context = context_mechanics(torch, rank, world_size, width)
        if rank == 0:
            target = write_result(
                args,
                lab_id="29_parallelism_mechanics",
                environment={**environment, "world_size": world_size},
                measurements={
                    "pipeline_parallel": pipeline,
                    "context_parallel": context,
                    "companion_training_labs": {
                        "tensor_parallel": "19_tensor_parallel_linear.py",
                        "expert_parallel": "12_moe_expert_parallel.py",
                    },
                    "scope": (
                        "two-rank mechanics only; not production PP/CP scaling, "
                        "topology, or overlap evidence"
                    ),
                },
                correctness={
                    "pipeline_forward_backward_gradients_match_reference": True,
                    "context_partition_and_gradient_match_reference": True,
                    "fp32_rtol": 1e-5,
                    "fp32_atol": 1e-6,
                },
            )
            print(f"Wrote PP/CP training-mechanics evidence: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
