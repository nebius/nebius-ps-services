"""Progress from serial output to a bounded, event-safe D2H worker pipeline."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import math
import time
from typing import Any, Callable

from common import (
    add_common_args,
    load_torch,
    require_h100,
    summarize_ms,
    validate_common_args,
    write_result,
)


class OutputSlot:
    """Keep output ownership until transfer and CPU consumption both finish."""

    def __init__(self) -> None:
        self.future: Future[Any] | None = None

    def acquire(self) -> Any:
        if self.future is None:
            return None
        value = self.future.result()
        self.future = None
        return value

    def submit(self, future: Future[Any]) -> None:
        if self.future is not None:
            raise RuntimeError("Output slot still belongs to its consumer")
        self.future = future


def consume_after_copy(event: Any, consume: Callable[[], Any]) -> Any:
    event.synchronize()
    return consume()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--mode",
        choices=("serial", "workers", "pooled", "nonblocking", "pipeline"),
        default="serial",
    )
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--batches", type=int, default=16)
    parser.add_argument(
        "--sink-ms",
        type=float,
        default=2.0,
        help="Synthetic blocking sink delay per output",
    )
    args = parser.parse_args()
    if (
        not 1 <= args.slots <= 8
        or not 1 <= args.workers <= 8
        or not 2 <= args.batches <= 256
    ):
        parser.error("--slots/--workers must be 1..8; --batches must be 2..256")
    if not math.isfinite(args.sink_ms) or not 0 <= args.sink_ms <= 100:
        parser.error("--sink-ms must be finite and 0..100")
    return args


def check_output(torch: Any, host: Any, batch: int, delay_ms: float) -> int:
    if not torch.equal(host, torch.full_like(host, (batch + 1) / 4096)):
        raise RuntimeError(f"Incomplete, stale or corrupted output for batch {batch}")
    # A controlled blocking sink, not a disk benchmark or CPU Python speedup.
    time.sleep(delay_ms / 1000)
    return batch


def run_pipeline(torch: Any, args: argparse.Namespace) -> tuple[float, int]:
    width = 512 if args.profile == "smoke" else 2048
    pooled = args.mode in ("pooled", "nonblocking", "pipeline")
    asynchronous = args.mode in ("nonblocking", "pipeline")
    compute = torch.cuda.Stream()
    egress = torch.cuda.Stream() if args.mode == "pipeline" else compute
    inputs = [torch.empty((width, width), device="cuda") for _ in range(args.slots)]
    outputs = [torch.empty_like(item) for item in inputs]
    hosts = (
        [torch.empty((width, width), pin_memory=True) for _ in inputs] if pooled else []
    )
    weight = torch.full((width, width), 1 / width, device="cuda")
    ready = [torch.cuda.Event() for _ in inputs]
    copied = [torch.cuda.Event() for _ in inputs]
    slots = [OutputSlot() for _ in inputs]
    completed: list[int] = []

    def release(slot: OutputSlot) -> None:
        batch = slot.acquire()
        if batch is not None:
            completed.append(batch)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        # Start the executor before timing; bounded slots limit submitted work.
        pool.submit(lambda: None).result()
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.cuda.nvtx.range("d2h_loop"):
            for batch in range(args.batches):
                index = batch % args.slots
                with torch.cuda.nvtx.range("output_backpressure"):
                    release(slots[index])
                with (
                    torch.cuda.stream(compute),
                    torch.cuda.nvtx.range("produce_output"),
                ):
                    inputs[index].fill_((batch + 1) / 4096)
                    torch.mm(inputs[index], weight, out=outputs[index])
                    ready[index].record(compute)
                host = (
                    hosts[index]
                    if pooled
                    else torch.empty((width, width), pin_memory=True)
                )
                with torch.cuda.stream(egress), torch.cuda.nvtx.range("d2h_submit"):
                    egress.wait_event(ready[index])
                    host.copy_(outputs[index], non_blocking=asynchronous)
                    copied[index].record(egress)

                def consume(host: Any = host, batch: int = batch) -> int:
                    with torch.cuda.nvtx.range("host_consume"):
                        return check_output(torch, host, batch, args.sink_ms)

                if args.mode == "serial":
                    completed.append(consume_after_copy(copied[index], consume))
                else:
                    slots[index].submit(
                        pool.submit(consume_after_copy, copied[index], consume)
                    )
            with torch.cuda.nvtx.range("output_drain"):
                for slot in slots:
                    release(slot)
                compute.synchronize()
                egress.synchronize()
        elapsed = (time.perf_counter() - started) * 1000
    if sorted(completed) != list(range(args.batches)):
        raise RuntimeError("Output pipeline lost or duplicated batches")
    if not math.isfinite(elapsed) or elapsed <= 0:
        raise RuntimeError("Invalid output pipeline duration")
    return elapsed, args.slots * width * width * 4


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    torch.set_num_threads(1)
    torch.backends.cuda.matmul.allow_tf32 = False
    with torch.inference_mode():
        for _ in range(args.warmup):
            run_pipeline(torch, args)
        samples = [run_pipeline(torch, args) for _ in range(args.iterations)]
    target = write_result(
        args,
        lab_id="20_d2h_pipeline",
        environment=environment,
        measurements={
            "mode": args.mode,
            "slots": args.slots,
            "workers": args.workers,
            "batches": args.batches,
            "synthetic_sink_ms": args.sink_ms,
            "in_flight_destination_capacity_bytes": samples[0][1],
            "whole_loop": summarize_ms([item[0] for item in samples]),
            "whole_loop_samples_ms": [item[0] for item in samples],
            "timing_scope": "GPU produce, D2H, exact CPU checks, synthetic sink, waits and final drain; initial pools excluded",
            "overlap_evidence": "pending timeline; threads model blocking sinks, not GIL-bound Python acceleration",
        },
        correctness={
            "every_output_matches_exact_reference": True,
            "all_outputs_consumed_once": True,
        },
    )
    print(f"Completed D2H output pipeline: {target}")


if __name__ == "__main__":
    main()
