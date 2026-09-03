"""Map a PyTorch operator chain to CPU launches and CUDA kernel activity."""

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    width = 1_024 if args.profile == "smoke" else 4_096
    batch = 64 if args.profile == "smoke" else 256
    values = torch.randn((batch, width), device="cuda", dtype=torch.bfloat16)
    weight = torch.randn((width, width), device="cuda", dtype=torch.bfloat16)
    bias = torch.randn((width,), device="cuda", dtype=torch.bfloat16)

    def workload() -> object:
        projected = values @ weight
        normalized = torch.nn.functional.layer_norm(projected, (width,))
        return torch.nn.functional.gelu(normalized + bias).square().mean()

    for _ in range(args.warmup):
        workload()
    torch.cuda.synchronize()
    with torch.profiler.profile(
        activities=[
            torch.profiler.ProfilerActivity.CPU,
            torch.profiler.ProfilerActivity.CUDA,
        ],
        record_shapes=True,
    ) as profiler:
        loss = None
        for _ in range(args.iterations):
            loss = workload()
            profiler.step()
    assert loss is not None
    events = profiler.key_averages()
    cuda_events = [event for event in events if event.self_cuda_time_total > 0]
    top_cuda = sorted(
        cuda_events, key=lambda event: event.self_cuda_time_total, reverse=True
    )[:10]
    rows = [
        {
            "event": event.key,
            "calls": int(event.count),
            "self_cuda_time_us": round(float(event.self_cuda_time_total), 3),
        }
        for event in top_cuda
    ]
    finite = bool(torch.isfinite(loss).item())
    if not finite or not rows:
        raise SystemExit("Profiler did not capture finite CUDA work.")
    target = write_result(
        args,
        lab_id="08_operator_to_kernels",
        environment=environment,
        measurements={
            "shape": [batch, width],
            "unique_profile_events": len(events),
            "cuda_events_with_device_time": len(cuda_events),
            "top_cuda_events": rows,
        },
        correctness={"finite_loss": True, "captured_cuda_events": True},
    )
    print(events.table(sort_by="self_cuda_time_total", row_limit=10))
    print(f"Completed operator-to-kernel map: {target}")


if __name__ == "__main__":
    main()
