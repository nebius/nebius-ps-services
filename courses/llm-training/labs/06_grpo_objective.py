"""Unpack group-relative advantages and a clipped policy objective on the GPU."""

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument("--group-size", type=int, default=8)
    args = parser.parse_args()
    validate_common_args(args)
    if args.group_size < 2:
        raise SystemExit(
            "--group-size must be at least 2 for within-group normalization"
        )
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)

    prompts = 4
    old_log_probs = torch.randn((prompts, args.group_size), device="cuda") * 0.1
    new_log_probs = (
        old_log_probs + torch.randn_like(old_log_probs) * 0.02
    ).requires_grad_()
    rewards = torch.randn((prompts, args.group_size), device="cuda")
    advantages = (rewards - rewards.mean(dim=1, keepdim=True)) / (
        rewards.std(dim=1, keepdim=True, unbiased=False) + 1e-4
    )
    ratio = (new_log_probs - old_log_probs).exp()
    clipped_ratio = ratio.clamp(0.8, 1.2)
    surrogate = torch.minimum(ratio * advantages, clipped_ratio * advantages)
    reference_log_probs = old_log_probs - 0.01
    log_ratio = reference_log_probs - new_log_probs
    approximate_kl = log_ratio.exp() - log_ratio - 1
    loss = -(surrogate - 0.02 * approximate_kl).mean()
    loss.backward()
    group_mean_error = float(advantages.mean(dim=1).abs().max())
    if group_mean_error > 1e-5 or not torch.isfinite(loss):
        raise SystemExit(
            "The normalized advantages or policy objective failed validation."
        )
    target = write_result(
        args,
        lab_id="06_grpo_objective",
        environment=environment,
        measurements={
            "prompts": prompts,
            "group_size": args.group_size,
            "loss": round(float(loss.detach()), 6),
            "mean_approximate_kl": round(float(approximate_kl.mean()), 7),
            "max_group_advantage_mean_error": round(group_mean_error, 8),
        },
        correctness={
            "zero_mean_advantages": True,
            "finite_gradients": bool(torch.isfinite(new_log_probs.grad).all()),
        },
    )
    print(f"Completed GRPO-objective unpacking: {target}")


if __name__ == "__main__":
    main()
