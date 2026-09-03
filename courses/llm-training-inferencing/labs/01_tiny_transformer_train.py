"""Train a small decoder-only language model with BF16 tensor-core compute."""

from __future__ import annotations

import argparse
import math
import statistics

from common import (
    add_common_args,
    load_torch,
    open_private_exclusive,
    require_h100,
    seed_everything,
    validate_common_args,
    write_result,
)
from tiny_lm import build_tiny_lm, make_language_batch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--save-checkpoint", action="store_true")
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)

    hidden, layers, heads, sequence, batch = (
        (512, 4, 8, 256, 8) if args.profile == "smoke" else (2_048, 12, 16, 1_024, 4)
    )
    vocab_size = 4_096
    model = build_tiny_lm(
        torch,
        vocab_size=vocab_size,
        hidden_size=hidden,
        layers=layers,
        heads=heads,
        max_sequence=sequence,
    ).to("cuda")
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
    inputs, labels = make_language_batch(
        torch,
        batch_size=batch,
        sequence=sequence,
        vocab_size=vocab_size,
        device="cuda",
    )
    samples: list[float] = []
    losses: list[float] = []
    gradient_norms: list[float] = []
    trainable_parameters = [
        parameter for parameter in model.parameters() if parameter.requires_grad
    ]

    def train_step() -> tuple[object, object]:
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            logits = model(inputs)
            loss = torch.nn.functional.cross_entropy(
                logits.reshape(-1, vocab_size), labels.reshape(-1)
            )
        loss.backward()
        if any(parameter.grad is None for parameter in trainable_parameters):
            raise SystemExit("At least one trainable parameter has no gradient.")
        gradient_norm = torch.nn.utils.clip_grad_norm_(
            trainable_parameters,
            1.0,
            error_if_nonfinite=True,
        )
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        return loss.detach(), gradient_norm.detach()

    for _ in range(args.warmup):
        train_step()
    torch.cuda.synchronize()
    tracked_parameter = trainable_parameters[0]
    parameter_before = tracked_parameter.detach().float().cpu().clone()
    torch.cuda.synchronize()
    baseline_allocated_bytes = int(torch.cuda.memory_allocated())
    baseline_reserved_bytes = int(torch.cuda.memory_reserved())
    torch.cuda.reset_peak_memory_stats()
    for _ in range(args.iterations):
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        loss, gradient_norm = train_step()
        end.record()
        end.synchronize()
        samples.append(float(start.elapsed_time(end)))
        losses.append(float(loss))
        gradient_norms.append(float(gradient_norm))

    peak_allocated_bytes = int(torch.cuda.max_memory_allocated())
    peak_reserved_bytes = int(torch.cuda.max_memory_reserved())
    parameter_after = tracked_parameter.detach().float().cpu()
    parameter_delta_l2 = float((parameter_after - parameter_before).norm().item())
    finite_losses = all(math.isfinite(loss) for loss in losses)
    finite_gradients = all(
        math.isfinite(gradient_norm) and gradient_norm > 0
        for gradient_norm in gradient_norms
    )
    parameter_updated = math.isfinite(parameter_delta_l2) and parameter_delta_l2 > 0
    if not finite_losses:
        raise SystemExit("Training produced a non-finite loss.")
    if not finite_gradients:
        raise SystemExit("Training produced a missing, zero, or non-finite gradient.")
    if not parameter_updated:
        raise SystemExit("The optimizer did not update the tracked parameter.")
    tokens = batch * sequence * args.iterations
    if args.save_checkpoint:
        checkpoint = (
            args.output_dir / f"tiny-transformer-checkpoint-run-{args.run_id}.pt"
        )
        with open_private_exclusive(checkpoint, binary=True) as stream:
            torch.save(
                {
                    "model": model.state_dict(),
                    "optimizer": optimizer.state_dict(),
                    "run_id": args.run_id,
                    "step": args.warmup + args.iterations,
                },
                stream,
            )
    target = write_result(
        args,
        lab_id="01_tiny_transformer_train",
        environment=environment,
        measurements={
            "parameters": sum(parameter.numel() for parameter in model.parameters()),
            "tokens": tokens,
            "initial_loss": round(losses[0], 5),
            "final_loss": round(losses[-1], 5),
            "median_step_ms": round(statistics.median(samples), 4),
            "tokens_per_second": round(tokens / (sum(samples) / 1_000), 1),
            "gradient_norm": {
                "initial": round(gradient_norms[0], 6),
                "final": round(gradient_norms[-1], 6),
                "median": round(statistics.median(gradient_norms), 6),
            },
            "memory": {
                "baseline_allocated_bytes": baseline_allocated_bytes,
                "baseline_reserved_bytes": baseline_reserved_bytes,
                "peak_allocated_bytes": peak_allocated_bytes,
                "peak_reserved_bytes": peak_reserved_bytes,
                "incremental_peak_allocated_bytes": (
                    peak_allocated_bytes - baseline_allocated_bytes
                ),
                "incremental_peak_reserved_bytes": (
                    peak_reserved_bytes - baseline_reserved_bytes
                ),
            },
            "parameter_delta_l2": round(parameter_delta_l2, 8),
        },
        correctness={
            "finite_loss": True,
            "finite_gradients": True,
            "all_trainable_parameters_had_gradients": True,
            "parameter_updated": True,
        },
    )
    print(f"Completed tiny-transformer training: {target}")


if __name__ == "__main__":
    main()
