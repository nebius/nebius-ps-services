"""Compare matched FP32, BF16, and scaled-FP16 tiny-LLM training steps."""

from __future__ import annotations

import argparse
import copy
import math
import statistics
import time
from typing import Any

from common import (
    add_common_args,
    load_torch,
    require_h100,
    seed_everything,
    validate_common_args,
    write_result,
)
from tiny_lm import build_tiny_lm, make_language_batch


def relative_error(candidate: float, reference: float) -> float:
    """Return scalar relative error with a stable near-zero denominator."""
    return abs(candidate - reference) / max(abs(reference), 1e-12)


def state_relative_l2(
    candidate: dict[str, Any],
    reference: dict[str, Any],
) -> float:
    """Compute relative L2 over matching named tensors without concatenation."""
    if candidate.keys() != reference.keys():
        raise SystemExit("Numerical comparison encountered different parameter sets.")
    difference_squared = 0.0
    reference_squared = 0.0
    for name, reference_value in reference.items():
        candidate_value = candidate[name]
        difference_squared += float((candidate_value - reference_value).square().sum())
        reference_squared += float(reference_value.square().sum())
    return math.sqrt(difference_squared) / max(math.sqrt(reference_squared), 1e-12)


def numerical_step(
    torch: Any,
    *,
    autocast_dtype: Any | None,
    initial_state: dict[str, Any],
    inputs: Any,
    labels: Any,
    vocab_size: int,
    model_shape: dict[str, int],
    seed: int,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Capture one update from identical weights, data, and random state."""
    seed_everything(torch, seed)
    model = build_tiny_lm(
        torch,
        vocab_size=vocab_size,
        hidden_size=model_shape["hidden"],
        layers=model_shape["layers"],
        heads=model_shape["heads"],
        max_sequence=model_shape["sequence"],
    ).to("cuda")
    model.load_state_dict(initial_state)
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
    use_fp16_scaler = autocast_dtype == torch.float16
    scaler = torch.amp.GradScaler("cuda", enabled=use_fp16_scaler)
    optimizer.zero_grad(set_to_none=True)
    with torch.autocast(
        device_type="cuda",
        dtype=autocast_dtype,
        enabled=autocast_dtype is not None,
    ):
        logits = model(inputs)
        loss = torch.nn.functional.cross_entropy(
            logits.reshape(-1, vocab_size), labels.reshape(-1)
        )
    scaler.scale(loss).backward()
    scaler.unscale_(optimizer)

    gradients: dict[str, Any] = {}
    finite = bool(torch.isfinite(loss))
    for name, parameter in model.named_parameters():
        if parameter.grad is None:
            raise SystemExit(f"Missing gradient for {name} in numerical comparison.")
        value = parameter.grad.detach().float().cpu()
        gradients[name] = value
        finite = finite and bool(torch.isfinite(value).all())
    gradient_l2 = math.sqrt(
        sum(float(value.square().sum()) for value in gradients.values())
    )

    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    scaler.step(optimizer)
    scaler.update()
    updates: dict[str, Any] = {}
    for name, parameter in model.named_parameters():
        value = parameter.detach().float().cpu() - initial_state[name].float()
        updates[name] = value
        finite = finite and bool(torch.isfinite(value).all())
    update_l2 = math.sqrt(
        sum(float(value.square().sum()) for value in updates.values())
    )
    if not finite or update_l2 <= 0:
        raise SystemExit(
            "A numerical comparison path was non-finite or did not update."
        )
    record = {
        "pre_update_loss": float(loss.detach()),
        "unclipped_gradient_l2": gradient_l2,
        "parameter_update_l2": update_l2,
        "parameters_remain_fp32": all(
            parameter.dtype == torch.float32 for parameter in model.parameters()
        ),
        "uses_dynamic_gradient_scaling": use_fp16_scaler,
        "gradient_scale_after_step": float(scaler.get_scale()),
        "finite_loss_gradients_and_update": finite,
    }
    return record, {"gradients": gradients, "updates": updates}


def train_mode(
    torch: Any,
    *,
    name: str,
    autocast_dtype: Any | None,
    initial_state: dict[str, Any],
    inputs: Any,
    labels: Any,
    vocab_size: int,
    model_shape: dict[str, int],
    seed: int,
    warmup: int,
    iterations: int,
) -> dict[str, Any]:
    """Measure a warmed multi-step run separately from the equivalence step."""
    seed_everything(torch, seed)
    model = build_tiny_lm(
        torch,
        vocab_size=vocab_size,
        hidden_size=model_shape["hidden"],
        layers=model_shape["layers"],
        heads=model_shape["heads"],
        max_sequence=model_shape["sequence"],
    ).to("cuda")
    model.load_state_dict(initial_state)
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
    use_fp16_scaler = autocast_dtype == torch.float16
    scaler = torch.amp.GradScaler("cuda", enabled=use_fp16_scaler)
    first_parameter = next(model.parameters())
    initial_parameter = first_parameter.detach().clone()

    def one_step() -> tuple[Any, Any]:
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(
            device_type="cuda",
            dtype=autocast_dtype,
            enabled=autocast_dtype is not None,
        ):
            logits = model(inputs)
            loss = torch.nn.functional.cross_entropy(
                logits.reshape(-1, vocab_size), labels.reshape(-1)
            )
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        gradient_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        scaler.step(optimizer)
        scaler.update()
        return loss.detach(), gradient_norm.detach()

    for _ in range(warmup):
        one_step()
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    device_samples: list[float] = []
    end_to_end_samples: list[float] = []
    losses: list[float] = []
    gradient_norms: list[float] = []
    for _ in range(iterations):
        torch.cuda.synchronize()
        wall_started = time.perf_counter()
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        loss, gradient_norm = one_step()
        end.record()
        end.synchronize()
        end_to_end_samples.append((time.perf_counter() - wall_started) * 1_000)
        device_samples.append(float(start.elapsed_time(end)))
        losses.append(float(loss))
        gradient_norms.append(float(gradient_norm))

    parameter_delta = float(
        (first_parameter.detach() - initial_parameter).float().norm()
    )
    finite = all(
        torch.isfinite(torch.tensor(values)).all().item()
        for values in (losses, gradient_norms)
    )
    if not finite or parameter_delta <= 0:
        raise SystemExit(f"{name} failed the finite-update correctness gate.")
    tokens = int(inputs.numel()) * iterations
    result = {
        "autocast_dtype": str(autocast_dtype).removeprefix("torch."),
        "parameters_remain_fp32": all(
            parameter.dtype == torch.float32 for parameter in model.parameters()
        ),
        "uses_dynamic_gradient_scaling": use_fp16_scaler,
        "final_gradient_scale": float(scaler.get_scale()),
        "median_device_region_ms": round(statistics.median(device_samples), 4),
        "median_end_to_end_step_ms": round(statistics.median(end_to_end_samples), 4),
        "p90_end_to_end_step_ms": round(
            sorted(end_to_end_samples)[
                max(0, math.ceil(0.9 * len(end_to_end_samples)) - 1)
            ],
            4,
        ),
        "end_to_end_tokens_per_second": round(
            tokens / (sum(end_to_end_samples) / 1_000), 1
        ),
        "first_timed_loss_after_warmup": round(losses[0], 6),
        "final_measured_loss": round(losses[-1], 6),
        "final_gradient_norm": round(gradient_norms[-1], 6),
        "parameter_delta_l2": round(parameter_delta, 6),
        "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1),
        "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 2**20, 1),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--max-loss-relative-error", type=float, default=0.05)
    parser.add_argument("--max-gradient-relative-l2", type=float, default=0.2)
    parser.add_argument("--max-update-relative-l2", type=float, default=0.2)
    args = parser.parse_args()
    validate_common_args(args)
    thresholds = (
        args.max_loss_relative_error,
        args.max_gradient_relative_l2,
        args.max_update_relative_l2,
    )
    if any(not math.isfinite(value) or value <= 0 for value in thresholds):
        raise SystemExit(
            "All numerical-equivalence thresholds must be finite and positive."
        )
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    torch.set_float32_matmul_precision("highest")

    model_shape = (
        {"hidden": 384, "layers": 3, "heads": 6, "sequence": 128, "batch": 4}
        if args.profile == "smoke"
        else {
            "hidden": 1_024,
            "layers": 6,
            "heads": 16,
            "sequence": 512,
            "batch": 8,
        }
    )
    vocab_size = 2_048 if args.profile == "smoke" else 8_192
    base = build_tiny_lm(
        torch,
        vocab_size=vocab_size,
        hidden_size=model_shape["hidden"],
        layers=model_shape["layers"],
        heads=model_shape["heads"],
        max_sequence=model_shape["sequence"],
    )
    initial_state = copy.deepcopy(base.state_dict())
    del base
    inputs, labels = make_language_batch(
        torch,
        batch_size=model_shape["batch"],
        sequence=model_shape["sequence"],
        vocab_size=vocab_size,
        device="cuda",
    )
    modes = {
        "fp32": None,
        "bf16": torch.bfloat16,
        "fp16": torch.float16,
    }
    numerical_records: dict[str, dict[str, Any]] = {}
    reference_record, reference_state = numerical_step(
        torch,
        autocast_dtype=None,
        initial_state=initial_state,
        inputs=inputs,
        labels=labels,
        vocab_size=vocab_size,
        model_shape=model_shape,
        seed=args.seed,
    )
    numerical_records["fp32"] = {
        **reference_record,
        "loss_relative_error_vs_fp32": 0.0,
        "gradient_relative_l2_vs_fp32": 0.0,
        "update_relative_l2_vs_fp32": 0.0,
    }
    torch.cuda.empty_cache()
    for name, dtype in (("bf16", torch.bfloat16), ("fp16", torch.float16)):
        candidate_record, candidate_state = numerical_step(
            torch,
            autocast_dtype=dtype,
            initial_state=initial_state,
            inputs=inputs,
            labels=labels,
            vocab_size=vocab_size,
            model_shape=model_shape,
            seed=args.seed,
        )
        loss_error = relative_error(
            candidate_record["pre_update_loss"], reference_record["pre_update_loss"]
        )
        gradient_error = state_relative_l2(
            candidate_state["gradients"], reference_state["gradients"]
        )
        update_error = state_relative_l2(
            candidate_state["updates"], reference_state["updates"]
        )
        equivalence_errors = (loss_error, gradient_error, update_error)
        candidate_record.update(
            {
                "loss_relative_error_vs_fp32": loss_error,
                "gradient_relative_l2_vs_fp32": gradient_error,
                "update_relative_l2_vs_fp32": update_error,
            }
        )
        if (
            any(not math.isfinite(value) for value in equivalence_errors)
            or loss_error > args.max_loss_relative_error
            or gradient_error > args.max_gradient_relative_l2
            or update_error > args.max_update_relative_l2
        ):
            raise SystemExit(f"{name} failed the declared FP32 equivalence gates.")
        numerical_records[name] = candidate_record
        del candidate_state
        torch.cuda.empty_cache()
    del reference_state

    measurements: dict[str, dict[str, Any]] = {}
    for name, dtype in modes.items():
        timed_record = train_mode(
            torch,
            name=name,
            autocast_dtype=dtype,
            initial_state=initial_state,
            inputs=inputs,
            labels=labels,
            vocab_size=vocab_size,
            model_shape=model_shape,
            seed=args.seed,
            warmup=args.warmup,
            iterations=args.iterations,
        )
        measurements[name] = {
            "one_step_numerical_acceptance": numerical_records[name],
            "warmed_timing": timed_record,
        }
        torch.cuda.empty_cache()
    target = write_result(
        args,
        lab_id="21_mixed_precision_training",
        environment=environment,
        measurements={
            "model_shape": model_shape,
            "vocab_size": vocab_size,
            "identical_initial_state_and_batch": True,
            "numerical_thresholds": {
                "max_loss_relative_error": args.max_loss_relative_error,
                "max_gradient_relative_l2": args.max_gradient_relative_l2,
                "max_update_relative_l2": args.max_update_relative_l2,
            },
            "modes": measurements,
        },
        correctness={
            "all_modes_finite": all(
                record["finite_loss_gradients_and_update"]
                for record in numerical_records.values()
            ),
            "all_modes_update_parameters": all(
                record["parameter_update_l2"] > 0
                for record in numerical_records.values()
            ),
            "bf16_and_fp16_match_fp32_within_declared_thresholds": True,
            "parameters_remain_fp32": all(
                record["parameters_remain_fp32"]
                for record in numerical_records.values()
            ),
            "fp16_uses_dynamic_gradient_scaling": numerical_records["fp16"][
                "uses_dynamic_gradient_scaling"
            ],
        },
    )
    print(f"Completed matched mixed-precision training comparison: {target}")


if __name__ == "__main__":
    main()
