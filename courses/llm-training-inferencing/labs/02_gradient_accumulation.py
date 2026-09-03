"""Compare full-batch and microbatch gradient accumulation memory use."""

from __future__ import annotations

import argparse
import statistics
import time

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
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)

    width = 2_048 if args.profile == "smoke" else 8_192
    total_batch, microbatch = (64, 16) if args.profile == "smoke" else (128, 16)
    reference = torch.nn.Sequential(
        torch.nn.Linear(width, 4 * width, bias=False),
        torch.nn.GELU(),
        torch.nn.Linear(4 * width, width, bias=False),
    ).to("cuda")
    inputs = torch.randn((total_batch, width), device="cuda")
    targets = torch.randn((total_batch, width), device="cuda")

    def one_pass(model: object, split: int) -> None:
        model.zero_grad(set_to_none=True)
        for start in range(0, total_batch, split):
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                prediction = model(inputs[start : start + split])
                loss = torch.nn.functional.mse_loss(
                    prediction, targets[start : start + split]
                )
                loss = loss * (split / total_batch)
            loss.backward()

    def run(model: object, split: int) -> dict[str, float]:
        for _ in range(args.warmup):
            one_pass(model, split)
        torch.cuda.synchronize()
        elapsed_samples: list[float] = []
        peak_samples: list[float] = []
        for _ in range(args.iterations):
            torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            one_pass(model, split)
            torch.cuda.synchronize()
            elapsed_samples.append((time.perf_counter() - started) * 1_000)
            peak_samples.append(torch.cuda.max_memory_allocated() / 2**20)
        return {
            "median_elapsed_ms": round(statistics.median(elapsed_samples), 4),
            "median_peak_allocated_mib": round(statistics.median(peak_samples), 2),
        }

    full_metrics = run(reference, total_batch)
    full_gradients = [
        parameter.grad.detach().flatten()[:4_096].cpu().clone()
        for parameter in reference.parameters()
    ]
    reference.zero_grad(set_to_none=True)
    torch.cuda.empty_cache()
    micro_metrics = run(reference, microbatch)
    sampled_gradient_error = max(
        float((expected - parameter.grad.detach().flatten()[:4_096].cpu()).abs().max())
        for expected, parameter in zip(full_gradients, reference.parameters())
    )
    if sampled_gradient_error > 2e-3:
        raise SystemExit(
            "Accumulated gradient samples diverged: "
            f"max error {sampled_gradient_error:.6f}"
        )
    target = write_result(
        args,
        lab_id="02_gradient_accumulation",
        environment=environment,
        measurements={
            "total_batch": total_batch,
            "microbatch": microbatch,
            "full_batch": full_metrics,
            "accumulated": micro_metrics,
            "sampled_max_gradient_error": round(sampled_gradient_error, 7),
        },
        correctness={"sampled_gradients_match": True},
    )
    print(f"Completed gradient-accumulation experiment: {target}")


if __name__ == "__main__":
    main()
