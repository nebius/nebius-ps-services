"""Show how prompt and padding masks choose which tokens train an LLM."""

from __future__ import annotations

import argparse

from common import (
    add_common_args,
    load_torch,
    require_h100,
    seed_everything,
    validate_common_args,
    write_result,
)
from tiny_lm import build_tiny_lm


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    vocab_size = 128
    model = build_tiny_lm(
        torch,
        vocab_size=vocab_size,
        hidden_size=256,
        layers=2,
        heads=4,
        max_sequence=16,
    ).to(device="cuda", dtype=torch.bfloat16)
    token_ids = torch.tensor(
        [
            [11, 12, 13, 41, 42, 43, 44, 0],
            [21, 22, 51, 52, 53, 0, 0, 0],
        ],
        device="cuda",
        dtype=torch.long,
    )
    labels = token_ids[:, 1:].clone()
    inputs = token_ids[:, :-1]
    unmasked_labels = labels.clone()
    labels[0, :2] = -100
    labels[1, :1] = -100
    labels[0, -1:] = -100
    labels[1, -3:] = -100
    logits = model(inputs)
    masked_loss = torch.nn.functional.cross_entropy(
        logits.reshape(-1, vocab_size), labels.reshape(-1), ignore_index=-100
    )
    unmasked_loss = torch.nn.functional.cross_entropy(
        logits.reshape(-1, vocab_size), unmasked_labels.reshape(-1)
    )
    masked_loss.backward()
    trained_tokens = int((labels != -100).sum().item())
    ignored_tokens = int((labels == -100).sum().item())
    finite_gradients = all(
        parameter.grad is None or bool(torch.isfinite(parameter.grad).all().item())
        for parameter in model.parameters()
    )
    if trained_tokens != 7 or ignored_tokens != 7 or not finite_gradients:
        raise SystemExit("Loss-mask invariants failed.")
    target = write_result(
        args,
        lab_id="13_loss_masking",
        environment=environment,
        measurements={
            "trained_tokens": trained_tokens,
            "ignored_tokens": ignored_tokens,
            "masked_loss": round(float(masked_loss.item()), 6),
            "unmasked_loss": round(float(unmasked_loss.item()), 6),
        },
        correctness={
            "ignore_index_applied": True,
            "finite_gradients": True,
        },
    )
    print(f"Completed loss-masking exercise: {target}")


if __name__ == "__main__":
    main()
