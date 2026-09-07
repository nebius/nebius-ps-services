"""Compare bounded serial and overlapped H2D input pipelines on one H100."""

from __future__ import annotations

import argparse
import math
import time
from typing import Any

from common import (
    add_common_args,
    load_torch,
    require_h100,
    summarize_ms,
    validate_common_args,
    write_result,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--mode", choices=("serial", "pipeline"), default="serial")
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--batches", type=int, default=16)
    parser.add_argument("--work", type=int, default=4, help="Dense GEMMs per batch")
    args = parser.parse_args()
    if not 1 <= args.slots <= 8 or not 2 <= args.batches <= 256:
        parser.error("--slots must be 1..8 and --batches must be 2..256")
    if not 1 <= args.work <= 32:
        parser.error("--work must be 1..32")
    return args


def run_pipeline(torch: Any, args: argparse.Namespace) -> tuple[float, int]:
    width = 512 if args.profile == "smoke" else 2048
    compute = torch.cuda.Stream()
    copy = torch.cuda.Stream() if args.mode == "pipeline" else compute
    hosts = [torch.empty((width, width), pin_memory=True) for _ in range(args.slots)]
    inputs = [torch.empty((width, width), device="cuda") for _ in hosts]
    outputs = [torch.empty_like(item) for item in inputs]
    weight = torch.full((width, width), 1 / width, device="cuda")
    checks = torch.empty(args.batches, dtype=torch.bool, device="cuda")
    ready = [torch.cuda.Event() for _ in hosts]
    done = [torch.cuda.Event() for _ in hosts]
    used = [False] * args.slots
    torch.cuda.synchronize()
    started = time.perf_counter()
    with torch.cuda.nvtx.range("h2d_loop"):
        for batch in range(args.batches):
            slot = batch % args.slots
            with torch.cuda.nvtx.range("slot_reuse_wait"):
                if used[slot]:
                    done[slot].synchronize()
            value = (batch + 1) / 4096
            with torch.cuda.nvtx.range("prepare_host"):
                hosts[slot].fill_(value)
            with torch.cuda.stream(copy), torch.cuda.nvtx.range("h2d_submit"):
                inputs[slot].copy_(hosts[slot], non_blocking=True)
                ready[slot].record(copy)
            with torch.cuda.stream(compute), torch.cuda.nvtx.range("consume_batch"):
                compute.wait_event(ready[slot])
                for _ in range(args.work):
                    torch.mm(inputs[slot], weight, out=outputs[slot])
                # Power-of-two width and inputs make this reference exact.
                checks[batch] = torch.all(outputs[slot] == value)
                done[slot].record(compute)
            used[slot] = True
        with torch.cuda.nvtx.range("pipeline_drain"):
            compute.synchronize()
            copy.synchronize()
    elapsed = (time.perf_counter() - started) * 1000
    if not bool(checks.all().item()) or not math.isfinite(elapsed) or elapsed <= 0:
        raise RuntimeError("H2D pipeline failed complete-batch reference or timing")
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
        lab_id="19_h2d_pipeline",
        environment=environment,
        measurements={
            "mode": args.mode,
            "slots": args.slots,
            "batches": args.batches,
            "gemms_per_batch": args.work,
            "pinned_pool_bytes": samples[0][1],
            "whole_loop": summarize_ms([item[0] for item in samples]),
            "whole_loop_samples_ms": [item[0] for item in samples],
            "timing_scope": "host fill, copy, GEMMs, device checks, slot waits and drain; allocation excluded",
            "overlap_evidence": "pending Nsight Systems timeline; host timing alone cannot prove overlap",
        },
        correctness={
            "every_batch_matches_exact_reference": True,
            "pipeline_drained": True,
        },
    )
    print(f"Completed H2D pipeline: {target}")


if __name__ == "__main__":
    main()
