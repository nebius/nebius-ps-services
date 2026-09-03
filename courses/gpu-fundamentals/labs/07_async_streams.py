"""Contrast host enqueue time, device time, and independent CUDA streams."""

from __future__ import annotations

import argparse
import statistics
import time

from common import (
    add_common_args,
    load_torch,
    require_h100,
    seed_everything,
    summarize_ms,
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
    tensors = [
        torch.randn((width, width), device="cuda", dtype=torch.bfloat16)
        for _ in range(4)
    ]
    a, b, c, d = tensors
    stream_a = torch.cuda.Stream()
    stream_b = torch.cuda.Stream()

    def sequential() -> tuple[object, object]:
        return a @ b, c @ d

    def concurrent() -> tuple[object, object]:
        ready = torch.cuda.Event()
        ready.record()
        stream_a.wait_event(ready)
        stream_b.wait_event(ready)
        with torch.cuda.stream(stream_a):
            first = a @ b
        with torch.cuda.stream(stream_b):
            second = c @ d
        current = torch.cuda.current_stream()
        current.wait_stream(stream_a)
        current.wait_stream(stream_b)
        return first, second

    for _ in range(args.warmup):
        sequential()
        concurrent()
    torch.cuda.synchronize()

    host_enqueue_samples = []
    sequential_samples = []
    concurrent_samples = []
    sequential_result = None
    concurrent_result = None
    for _ in range(args.iterations):
        host_started = time.perf_counter()
        sequential_result = sequential()
        host_enqueue_samples.append((time.perf_counter() - host_started) * 1_000)
        torch.cuda.synchronize()

        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        sequential_result = sequential()
        end.record()
        end.synchronize()
        sequential_samples.append(float(start.elapsed_time(end)))

        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        concurrent_result = concurrent()
        end.record()
        end.synchronize()
        concurrent_samples.append(float(start.elapsed_time(end)))

    assert sequential_result is not None and concurrent_result is not None
    correct = all(
        torch.allclose(expected, observed, rtol=2e-2, atol=2e-2)
        for expected, observed in zip(sequential_result, concurrent_result, strict=True)
    )
    if not correct:
        raise SystemExit("Sequential and stream-based results diverged.")
    target = write_result(
        args,
        lab_id="07_async_streams",
        environment=environment,
        measurements={
            "matrix_width": width,
            "host_enqueue_median_ms": round(statistics.median(host_enqueue_samples), 4),
            "sequential_device": summarize_ms(sequential_samples),
            "independent_streams_device": summarize_ms(concurrent_samples),
        },
        correctness={"allclose": True},
    )
    print(f"Completed asynchronous stream experiment: {target}")


if __name__ == "__main__":
    main()
