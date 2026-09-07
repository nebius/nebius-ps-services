"""Compare deterministic serial and worker-prefetched training input pipelines."""

from __future__ import annotations

import argparse
import hashlib
import statistics
import time
from typing import Any

from common import (
    add_common_args,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)


class DeterministicSlowDataset:
    """Generate index-derived token rows with a controlled producer delay."""

    def __init__(self, samples: int, sequence_length: int, delay_ms: float) -> None:
        self.samples = samples
        self.sequence_length = sequence_length
        self.delay_ms = delay_ms

    def __len__(self) -> int:
        return self.samples

    def __getitem__(self, index: int) -> tuple[int, list[int], float]:
        cpu_started = time.process_time()
        tokens = [
            (index * 131 + position * 17) % 32_000
            for position in range(self.sequence_length)
        ]
        cpu_ms = (time.process_time() - cpu_started) * 1_000
        time.sleep(self.delay_ms / 1_000)
        return index, tokens, cpu_ms


def collate_samples(
    samples: list[tuple[int, list[int], float]],
) -> dict[str, Any]:
    import torch

    return {
        "indices": torch.tensor([sample[0] for sample in samples]),
        "tokens": torch.tensor([sample[1] for sample in samples], dtype=torch.int64),
        "producer_cpu_ms": torch.tensor([sample[2] for sample in samples]),
    }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--batches", type=int, default=8)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--prefetch", type=int, default=2)
    parser.add_argument("--producer-delay-ms", type=float, default=3.0)
    args = parser.parse_args()
    validate_common_args(args)
    if args.batches < 2:
        raise SystemExit("--batches must be at least 2")
    if args.workers < 1 or args.prefetch < 1 or args.producer_delay_ms < 0:
        raise SystemExit("workers/prefetch must be positive and delay non-negative")
    torch = load_torch()
    environment = require_h100(torch)
    batch_size = 8 if args.profile == "smoke" else 32
    sequence_length = 256 if args.profile == "smoke" else 2_048
    sample_count = args.batches * batch_size

    def run_case(*, workers: int, prefetch: int | None) -> dict[str, Any]:
        dataset = DeterministicSlowDataset(
            sample_count, sequence_length, args.producer_delay_ms
        )
        options: dict[str, Any] = {
            "dataset": dataset,
            "batch_size": batch_size,
            "shuffle": False,
            "num_workers": workers,
            "pin_memory": True,
            "collate_fn": collate_samples,
        }
        if workers:
            options["prefetch_factor"] = prefetch
        loader = torch.utils.data.DataLoader(**options)
        iterator = iter(loader)
        digest = hashlib.sha256()
        batch_ready_ms: list[float] = []
        h2d_ms: list[float] = []
        step_ms: list[float] = []
        producer_cpu_ms: list[float] = []
        queue_depth_before_fetch: list[int | None] = []
        total_started = time.perf_counter()
        for _ in range(args.batches):
            queue = getattr(iterator, "_data_queue", None)
            try:
                queue_depth_before_fetch.append(
                    queue.qsize() if queue is not None else None
                )
            except (AttributeError, NotImplementedError):
                queue_depth_before_fetch.append(None)
            ready_started = time.perf_counter()
            batch = next(iterator)
            ready_finished = time.perf_counter()
            batch_ready_ms.append((ready_finished - ready_started) * 1_000)
            indices = batch["indices"]
            tokens = batch["tokens"]
            digest.update(indices.numpy().tobytes())
            digest.update(tokens.numpy().tobytes())
            producer_cpu_ms.extend(float(value) for value in batch["producer_cpu_ms"])
            copy_started = torch.cuda.Event(enable_timing=True)
            copy_finished = torch.cuda.Event(enable_timing=True)
            step_finished = torch.cuda.Event(enable_timing=True)
            copy_started.record()
            device_tokens = tokens.to("cuda", non_blocking=True)
            copy_finished.record()
            value = device_tokens.float().square().mean()
            step_finished.record()
            step_finished.synchronize()
            if not bool(torch.isfinite(value)):
                raise SystemExit("Input-pipeline consumer produced a non-finite value.")
            h2d_ms.append(float(copy_started.elapsed_time(copy_finished)))
            step_ms.append(float(copy_started.elapsed_time(step_finished)))
        wall_seconds = time.perf_counter() - total_started
        total_tokens = sample_count * sequence_length
        return {
            "workers": workers,
            "prefetch_factor": prefetch,
            "configured_queue_capacity_batches": workers * (prefetch or 0),
            "queue_depth_before_fetch": queue_depth_before_fetch,
            "sample_order_and_content_digest": digest.hexdigest(),
            "batch_ready_ms": batch_ready_ms,
            "batch_ready_p50_ms": round(statistics.median(batch_ready_ms), 4),
            "batch_ready_p90_ms": round(percentile(batch_ready_ms, 0.9), 4),
            "producer_cpu_ms_per_sample_p50": round(
                statistics.median(producer_cpu_ms), 4
            ),
            "configured_producer_service_delay_ms_per_sample": args.producer_delay_ms,
            "h2d_ms": h2d_ms,
            "h2d_p50_ms": round(statistics.median(h2d_ms), 4),
            "consumer_step_ms": step_ms,
            "gpu_idle_gap_proxy_ms": batch_ready_ms,
            "tokens_per_second": round(total_tokens / wall_seconds, 3),
            "wall_seconds": round(wall_seconds, 4),
        }

    serial = run_case(workers=0, prefetch=None)
    prefetched = run_case(workers=args.workers, prefetch=args.prefetch)
    equivalent = (
        serial["sample_order_and_content_digest"]
        == prefetched["sample_order_and_content_digest"]
    )
    target = write_result(
        args,
        lab_id="26_input_pipeline",
        environment=environment,
        measurements={
            "batch_size": batch_size,
            "sequence_length": sequence_length,
            "serial_slow_producer": serial,
            "worker_prefetched": prefetched,
            "interpretation_boundary": (
                "batch-ready time is a host-side starvation proxy; confirm GPU idle "
                "gaps and queue behavior with a profiler before publishing a claim"
            ),
        },
        correctness={
            "identical_sample_order_and_content": equivalent,
            "all_batches_consumed_in_both_cases": len(serial["batch_ready_ms"])
            == len(prefetched["batch_ready_ms"])
            == args.batches,
            "all_timings_positive": all(
                value > 0
                for case in (serial, prefetched)
                for value in case["consumer_step_ms"]
            ),
        },
    )
    print(f"Wrote input-pipeline evidence: {target}")


if __name__ == "__main__":
    main()
