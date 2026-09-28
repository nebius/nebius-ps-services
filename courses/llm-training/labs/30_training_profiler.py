"""Profile a tiny H100 training step and emit a bounded operator summary."""

from __future__ import annotations

import argparse
from contextlib import nullcontext

from common import (
    add_common_args,
    load_torch,
    require_course_gpu,
    seed_everything,
    validate_common_args,
    write_result,
)
from course_evidence import annotated_operation
from tiny_lm import build_tiny_lm, make_language_batch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument(
        "--external-only",
        action="store_true",
        help="Skip the internal CUDA profiler during a separate Nsight capture.",
    )
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_course_gpu(torch)
    seed_everything(torch, args.seed)
    model = build_tiny_lm(
        torch, vocab_size=512, hidden_size=256, layers=2, heads=4, max_sequence=128
    ).to("cuda")
    inputs, labels = make_language_batch(
        torch, batch_size=4, sequence=64, vocab_size=512, device="cuda"
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)

    def training_step() -> object:
        optimizer.zero_grad(set_to_none=True)
        logits = model(inputs)
        loss = torch.nn.functional.cross_entropy(
            logits.reshape(-1, 512), labels.reshape(-1)
        )
        loss.backward()
        optimizer.step()
        torch.cuda.synchronize()
        return loss

    training_step = annotated_operation(training_step, "training_step")
    with (
        nullcontext()
        if args.external_only
        else torch.profiler.profile(
            activities=[
                torch.profiler.ProfilerActivity.CPU,
                torch.profiler.ProfilerActivity.CUDA,
            ],
            record_shapes=True,
            profile_memory=True,
        )
    ) as profile:
        loss = training_step()
    events = sorted(
        (profile.key_averages() if profile is not None else []),
        key=lambda event: event.self_device_time_total,
        reverse=True,
    )
    top = [
        {
            "name": event.key,
            "cuda_time_us": round(event.device_time_total, 3),
            "cpu_time_us": round(event.cpu_time_total, 3),
        }
        for event in events[:10]
    ]
    target = write_result(
        args,
        lab_id="30_training_profiler",
        environment=environment,
        measurements={
            "loss": float(loss.detach()),
            "top_operators": top,
            "top_operator_sort": "self_device_time_total_descending",
            "raw_trace_published": False,
        },
        correctness={"finite_loss": bool(torch.isfinite(loss))},
    )
    print(f"Wrote bounded profiler evidence: {target}")


if __name__ == "__main__":
    annotated_operation(main, "lab_workload")()
