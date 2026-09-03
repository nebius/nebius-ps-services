"""Measure the memory/time tradeoff from activation checkpointing."""

from __future__ import annotations

import argparse
import math
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
    from torch.utils.checkpoint import checkpoint

    def run_layers(layer_set: object, inputs: object, use_checkpoint: bool) -> object:
        hidden = inputs
        for layer in layer_set:

            def function(value: object, layer: object = layer) -> object:
                return torch.nn.functional.gelu(layer(value))

            hidden = (
                checkpoint(function, hidden, use_reentrant=False)
                if use_checkpoint
                else function(hidden)
            )
        return hidden

    probe_width = 512
    probe_depth = 2
    probe_batch = 4
    probe_layers = torch.nn.ModuleList(
        [
            torch.nn.Linear(
                probe_width,
                probe_width,
                bias=False,
                device="cuda",
                dtype=torch.bfloat16,
            )
            for _ in range(probe_depth)
        ]
    )
    probe_inputs = torch.randn(
        (probe_batch, probe_width), device="cuda", dtype=torch.bfloat16
    )

    def gradient_snapshot(
        use_checkpoint: bool,
    ) -> tuple[float, list[tuple[str, object]]]:
        probe_layers.zero_grad(set_to_none=True)
        inputs = probe_inputs.detach().clone().requires_grad_(True)
        hidden = run_layers(probe_layers, inputs, use_checkpoint)
        loss = hidden.float().square().mean()
        loss.backward()
        gradients = [("inputs", inputs.grad)]
        gradients.extend(
            (f"layer_{index}.weight", layer.weight.grad)
            for index, layer in enumerate(probe_layers)
        )
        if any(gradient is None for _, gradient in gradients):
            raise SystemExit("The checkpointing probe produced a missing gradient.")
        if any(
            not bool(torch.isfinite(gradient).all().item()) for _, gradient in gradients
        ):
            raise SystemExit("The checkpointing probe produced a non-finite gradient.")
        return float(loss.detach()), [
            (name, gradient.detach().float().cpu().clone())
            for name, gradient in gradients
        ]

    baseline_probe_loss, baseline_gradients = gradient_snapshot(False)
    checkpoint_probe_loss, checkpoint_gradients = gradient_snapshot(True)
    if [name for name, _ in baseline_gradients] != [
        name for name, _ in checkpoint_gradients
    ]:
        raise SystemExit("Checkpointing probe gradient names diverged.")
    gradients_allclose = True
    max_abs_gradient_error = 0.0
    squared_gradient_error = 0.0
    squared_reference_norm = 0.0
    for (name, baseline_gradient), (other_name, checkpoint_gradient) in zip(
        baseline_gradients, checkpoint_gradients, strict=True
    ):
        if name != other_name:
            raise SystemExit("Checkpointing probe gradient order diverged.")
        difference = checkpoint_gradient - baseline_gradient
        max_abs_gradient_error = max(
            max_abs_gradient_error, float(difference.abs().max().item())
        )
        squared_gradient_error += float(difference.square().sum().item())
        squared_reference_norm += float(baseline_gradient.square().sum().item())
        gradients_allclose = gradients_allclose and bool(
            torch.allclose(
                baseline_gradient,
                checkpoint_gradient,
                rtol=5e-2,
                atol=5e-2,
            )
        )
    relative_l2_gradient_error = math.sqrt(squared_gradient_error) / max(
        math.sqrt(squared_reference_norm), 1e-12
    )
    probe_loss_close = math.isclose(
        baseline_probe_loss, checkpoint_probe_loss, rel_tol=5e-2, abs_tol=5e-2
    )
    if not gradients_allclose or not probe_loss_close:
        raise SystemExit("Checkpointed and eager correctness-probe results diverged.")
    probe_layers.to("cpu")
    probe_inputs = probe_inputs.cpu()
    baseline_gradients.clear()
    checkpoint_gradients.clear()
    torch.cuda.empty_cache()

    width = 2_048 if args.profile == "smoke" else 8_192
    depth = 4 if args.profile == "smoke" else 8
    batch = 16
    layers = torch.nn.ModuleList(
        [
            torch.nn.Linear(
                width, width, bias=False, device="cuda", dtype=torch.bfloat16
            )
            for _ in range(depth)
        ]
    )
    base_inputs = torch.randn((batch, width), device="cuda", dtype=torch.bfloat16)

    def train_step(use_checkpoint: bool) -> float:
        layers.zero_grad(set_to_none=True)
        inputs = base_inputs.detach().clone().requires_grad_(True)
        hidden = run_layers(layers, inputs, use_checkpoint)
        loss = hidden.float().square().mean()
        loss.backward()
        return float(loss.detach())

    rows = {}
    losses = {}
    for label, enabled in (("baseline", False), ("checkpointed", True)):
        samples = []
        peaks = []
        for _ in range(args.warmup):
            train_step(enabled)
        torch.cuda.synchronize()
        for _ in range(args.iterations):
            torch.cuda.reset_peak_memory_stats()
            start = time.perf_counter()
            losses[label] = train_step(enabled)
            torch.cuda.synchronize()
            samples.append((time.perf_counter() - start) * 1_000)
            peaks.append(torch.cuda.max_memory_allocated() / 2**20)
        rows[label] = {
            "median_ms": round(statistics.median(samples), 4),
            "peak_allocated_mib": round(statistics.median(peaks), 2),
        }
    correct = abs(losses["baseline"] - losses["checkpointed"]) <= 1e-2
    if not correct:
        raise SystemExit("Checkpointed and baseline loss values diverged unexpectedly.")
    target = write_result(
        args,
        lab_id="06_activation_checkpointing",
        environment=environment,
        measurements={
            "width": width,
            "depth": depth,
            "variants": rows,
            "correctness_probe": {
                "shape": {
                    "batch": probe_batch,
                    "width": probe_width,
                    "depth": probe_depth,
                },
                "rtol": 5e-2,
                "atol": 5e-2,
                "max_abs_gradient_error": round(max_abs_gradient_error, 8),
                "relative_l2_gradient_error": round(relative_l2_gradient_error, 8),
            },
        },
        correctness={
            "loss_close": True,
            "probe_loss_close": True,
            "gradients_allclose": True,
        },
    )
    print(f"Completed checkpointing experiment: {target}")


if __name__ == "__main__":
    main()
