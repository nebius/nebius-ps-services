"""Measure the memory savings and compute cost of activation checkpointing."""

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


def checkpointed_forward(
    torch: object, model: object, token_ids: object, *, mode: str
) -> object:
    from torch.utils.checkpoint import checkpoint

    if mode not in {"selective", "full"}:
        raise ValueError(f"unsupported checkpoint mode: {mode}")
    positions = torch.arange(token_ids.shape[1], device=token_ids.device)
    hidden = model.token_embedding(token_ids) + model.position_embedding(positions)
    for block_index, block in enumerate(model.blocks):
        should_checkpoint = mode == "full" or block_index % 2 == 1
        hidden = (
            checkpoint(block, hidden, use_reentrant=False)
            if should_checkpoint
            else block(hidden)
        )
    return model.lm_head(model.final_norm(hidden))


def train_samples(
    torch: object,
    model: object,
    inputs: object,
    targets: object,
    *,
    mode: str,
    warmup: int,
    iterations: int,
) -> tuple[list[float], list[int], list[object], object]:
    def step() -> object:
        logits = (
            model(inputs)
            if mode == "baseline"
            else checkpointed_forward(torch, model, inputs, mode=mode)
        )
        loss = torch.nn.functional.cross_entropy(
            logits.reshape(-1, logits.shape[-1]), targets.reshape(-1)
        )
        loss.backward()
        return loss.detach()

    for _ in range(warmup):
        model.zero_grad(set_to_none=True)
        loss = step()
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
        loss = step()
        end.record()
        end.synchronize()
        timings.append(float(start.elapsed_time(end)))
        incremental_peaks.append(
            int(torch.cuda.max_memory_allocated()) - baseline_bytes
        )
        if any(
            parameter.requires_grad and parameter.grad is None
            for parameter in model.parameters()
        ):
            raise SystemExit(
                "Checkpoint probe did not produce every trainable parameter gradient."
            )
        gradients = [
            parameter.grad.detach().float().cpu().clone()
            for parameter in model.parameters()
            if parameter.grad is not None
        ]
    return timings, incremental_peaks, gradients, loss.float().cpu().clone()


def require_checkpoint_equivalence(
    torch: object,
    baseline_loss: object,
    baseline_gradients: list[object],
    candidate_loss: object,
    candidate_gradients: list[object],
) -> dict[str, bool]:
    """Require loss and every parameter gradient before accepting timing evidence."""
    losses_match = bool(
        torch.isfinite(baseline_loss).all()
        and torch.isfinite(candidate_loss).all()
        and torch.allclose(baseline_loss, candidate_loss, rtol=1e-2, atol=1e-2)
    )
    gradients_match = (
        bool(baseline_gradients)
        and len(baseline_gradients) == len(candidate_gradients)
        and all(
            torch.isfinite(left).all()
            and torch.isfinite(right).all()
            and torch.allclose(left, right, rtol=1e-2, atol=1e-2)
            for left, right in zip(baseline_gradients, candidate_gradients, strict=True)
        )
    )
    if not losses_match or not gradients_match:
        raise SystemExit(
            "Checkpoint loss or full parameter gradients diverged from baseline."
        )
    return {"loss_allclose": losses_match, "gradients_allclose": bool(gradients_match)}


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
    eager_times, eager_peaks, eager_grads, eager_loss = train_samples(
        torch,
        eager_model,
        inputs,
        targets,
        mode="baseline",
        warmup=args.warmup,
        iterations=args.iterations,
    )
    del eager_model
    torch.cuda.empty_cache()
    selective_model = copy.deepcopy(base).to(device="cuda", dtype=torch.bfloat16)
    selective_times, selective_peaks, selective_grads, selective_loss = train_samples(
        torch,
        selective_model,
        inputs,
        targets,
        mode="selective",
        warmup=args.warmup,
        iterations=args.iterations,
    )
    del selective_model
    torch.cuda.empty_cache()
    full_model = copy.deepcopy(base).to(device="cuda", dtype=torch.bfloat16)
    full_times, full_peaks, full_grads, full_loss = train_samples(
        torch,
        full_model,
        inputs,
        targets,
        mode="full",
        warmup=args.warmup,
        iterations=args.iterations,
    )
    selective_checks = require_checkpoint_equivalence(
        torch, eager_loss, eager_grads, selective_loss, selective_grads
    )
    full_checks = require_checkpoint_equivalence(
        torch, eager_loss, eager_grads, full_loss, full_grads
    )
    target = write_result(
        args,
        lab_id="14_activation_checkpointing",
        environment=environment,
        measurements={
            "shape": {"batch": 4, "sequence": sequence, "hidden": hidden},
            "eager": {
                "loss": float(eager_loss),
                "step_time": summarize_ms(eager_times),
                "median_incremental_peak_bytes": int(statistics.median(eager_peaks)),
            },
            "selective": {
                "loss": float(selective_loss),
                "checkpointed_block_indices": list(range(1, layers, 2)),
                "step_time": summarize_ms(selective_times),
                "median_incremental_peak_bytes": int(
                    statistics.median(selective_peaks)
                ),
            },
            "full": {
                "loss": float(full_loss),
                "checkpointed_block_indices": list(range(layers)),
                "step_time": summarize_ms(full_times),
                "median_incremental_peak_bytes": int(statistics.median(full_peaks)),
            },
        },
        correctness={
            "selective_loss_allclose": selective_checks["loss_allclose"],
            "full_loss_allclose": full_checks["loss_allclose"],
            "selective_gradients_allclose": selective_checks["gradients_allclose"],
            "full_gradients_allclose": full_checks["gradients_allclose"],
            "gradient_scope": "all trainable parameters, including token and position embeddings; token IDs are integers and have no input gradient",
        },
    )
    print(f"Completed activation-checkpointing experiment: {target}")


if __name__ == "__main__":
    main()
