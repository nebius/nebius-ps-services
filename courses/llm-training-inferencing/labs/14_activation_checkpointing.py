"""Measure activation checkpointing as a memory-for-compute trade."""

from __future__ import annotations

import argparse
import copy
import statistics

from common import (
    add_common_args,
    load_torch,
    require_h100,
    seed_everything,
    summarize_ms,
    validate_common_args,
    write_result,
)
from tiny_lm import build_tiny_lm, make_language_batch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    return parser.parse_args()


def checkpointed_forward(torch: object, model: object, token_ids: object) -> object:
    from torch.utils.checkpoint import checkpoint

    positions = torch.arange(token_ids.shape[1], device=token_ids.device)
    hidden = model.token_embedding(token_ids) + model.position_embedding(positions)
    for block in model.blocks:
        hidden = checkpoint(block, hidden, use_reentrant=False)
    return model.lm_head(model.final_norm(hidden))


def train_samples(
    torch: object,
    model: object,
    inputs: object,
    targets: object,
    *,
    checkpointed: bool,
    warmup: int,
    iterations: int,
) -> tuple[list[float], list[int], list[object]]:
    def step() -> None:
        logits = (
            checkpointed_forward(torch, model, inputs)
            if checkpointed
            else model(inputs)
        )
        loss = torch.nn.functional.cross_entropy(
            logits.reshape(-1, logits.shape[-1]), targets.reshape(-1)
        )
        loss.backward()

    for _ in range(warmup):
        model.zero_grad(set_to_none=True)
        step()
    torch.cuda.synchronize()

    timings = []
    incremental_peaks = []
    gradients = []
    for _ in range(iterations):
        model.zero_grad(set_to_none=True)
        torch.cuda.empty_cache()
        baseline_bytes = int(torch.cuda.memory_allocated())
        torch.cuda.reset_peak_memory_stats()
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        step()
        end.record()
        end.synchronize()
        timings.append(float(start.elapsed_time(end)))
        incremental_peaks.append(
            int(torch.cuda.max_memory_allocated()) - baseline_bytes
        )
        gradients = [
            parameter.grad.detach().float().cpu().clone()
            for parameter in model.parameters()
            if parameter.grad is not None
        ]
    return timings, incremental_peaks, gradients


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    sequence = 256 if args.profile == "smoke" else 1_024
    hidden = 512 if args.profile == "smoke" else 1_024
    layers = 4 if args.profile == "smoke" else 8
    vocab_size = 2_048
    base = build_tiny_lm(
        torch,
        vocab_size=vocab_size,
        hidden_size=hidden,
        layers=layers,
        heads=8,
        max_sequence=sequence,
    )
    inputs, targets = make_language_batch(
        torch,
        batch_size=4,
        sequence=sequence,
        vocab_size=vocab_size,
        device="cuda",
    )
    eager_model = copy.deepcopy(base).to(device="cuda", dtype=torch.bfloat16)
    eager_times, eager_peaks, eager_grads = train_samples(
        torch,
        eager_model,
        inputs,
        targets,
        checkpointed=False,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    del eager_model
    torch.cuda.empty_cache()
    checkpoint_model = copy.deepcopy(base).to(device="cuda", dtype=torch.bfloat16)
    checkpoint_times, checkpoint_peaks, checkpoint_grads = train_samples(
        torch,
        checkpoint_model,
        inputs,
        targets,
        checkpointed=True,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    gradients_match = len(eager_grads) == len(checkpoint_grads) and all(
        torch.allclose(left, right, rtol=5e-2, atol=5e-2)
        for left, right in zip(eager_grads, checkpoint_grads, strict=True)
    )
    if not gradients_match:
        raise SystemExit("Checkpointed and eager gradients diverged.")
    target = write_result(
        args,
        lab_id="14_activation_checkpointing",
        environment=environment,
        measurements={
            "shape": {"batch": 4, "sequence": sequence, "hidden": hidden},
            "eager": {
                "step_time": summarize_ms(eager_times),
                "median_incremental_peak_bytes": int(statistics.median(eager_peaks)),
            },
            "checkpointed": {
                "step_time": summarize_ms(checkpoint_times),
                "median_incremental_peak_bytes": int(
                    statistics.median(checkpoint_peaks)
                ),
            },
        },
        correctness={"gradients_allclose": True},
    )
    print(f"Completed activation-checkpointing experiment: {target}")


if __name__ == "__main__":
    main()
