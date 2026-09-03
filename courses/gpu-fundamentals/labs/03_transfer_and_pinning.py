"""Compare pageable, pinned, blocking, and nonblocking host-to-device copies."""

from __future__ import annotations

import argparse
import statistics
import time

from common import (
    add_common_args,
    load_torch,
    require_h100,
    resolve_int_override,
    validate_common_args,
    write_result,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--size-mib", type=int, default=None)
    return parser.parse_args()


def measure_copy(
    torch: object,
    source: object,
    destination: object,
    non_blocking: bool,
    warmup: int,
    iterations: int,
) -> float:
    for _ in range(warmup):
        destination.copy_(source, non_blocking=non_blocking)
    torch.cuda.synchronize()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        destination.copy_(source, non_blocking=non_blocking)
        torch.cuda.synchronize()
        samples.append((time.perf_counter() - start) * 1_000)
    return statistics.median(samples)


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    size_mib = resolve_int_override(
        args.size_mib,
        64 if args.profile == "smoke" else 512,
        option="--size-mib",
    )
    torch = load_torch()
    environment = require_h100(torch)
    elements = size_mib * 2**20 // 4
    pageable = torch.randn(elements, dtype=torch.float32)
    pinned = torch.empty(elements, dtype=torch.float32, pin_memory=True)
    pinned.copy_(pageable)
    destination = torch.empty(elements, device="cuda", dtype=torch.float32)
    rows = []
    for label, source, non_blocking in (
        ("pageable_blocking", pageable, False),
        ("pageable_nonblocking", pageable, True),
        ("pinned_blocking", pinned, False),
        ("pinned_nonblocking", pinned, True),
    ):
        median_ms = measure_copy(
            torch, source, destination, non_blocking, args.warmup, args.iterations
        )
        rows.append(
            {
                "mode": label,
                "median_ms": round(median_ms, 4),
                "effective_gib_per_s": round(
                    (size_mib / 1024) / (median_ms / 1_000), 2
                ),
            }
        )
    correct = bool(torch.equal(destination.cpu(), pageable))
    if not correct:
        raise SystemExit("Copied data did not match the source.")
    target = write_result(
        args,
        lab_id="03_transfer_and_pinning",
        environment=environment,
        measurements={"size_mib": size_mib, "modes": rows},
        correctness={"exact_copy": True},
    )
    print(f"Completed transfer benchmark: {target}")


if __name__ == "__main__":
    main()
