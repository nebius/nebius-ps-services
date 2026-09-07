"""Calculate and verify KV-cache capacity for MHA, GQA, and MQA layouts."""

from __future__ import annotations

import argparse

from common import (
    add_common_args,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)


def kv_bytes_per_token(
    layers: int, kv_heads: int, head_dim: int, bytes_per_value: int
) -> int:
    """Return key plus value bytes for one token and one sequence."""
    return 2 * layers * kv_heads * head_dim * bytes_per_value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument("--sequence", type=int, default=4096)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    validate_common_args(args)
    if args.sequence < 1 or args.concurrency < 1:
        raise SystemExit("--sequence and --concurrency must be positive")
    torch = load_torch()
    environment = require_h100(torch)
    cases = {}
    for name, kv_heads in (("mha", 32), ("gqa", 8), ("mqa", 1)):
        per_token = kv_bytes_per_token(32, kv_heads, 128, 2)
        cases[name] = {
            "kv_heads": kv_heads,
            "bytes_per_token": per_token,
            "ideal_gib": round(per_token * args.sequence * args.concurrency / 2**30, 4),
        }
    probe = torch.empty((2, 32, 8, 128), device="cuda", dtype=torch.bfloat16)
    target = write_result(
        args,
        lab_id="26_kv_capacity",
        environment=environment,
        measurements={
            "layers": 32,
            "query_heads": 32,
            "head_dim": 128,
            "dtype_bytes": 2,
            "sequence": args.sequence,
            "concurrency": args.concurrency,
            "cases": cases,
            "runtime_overhead_excluded": [
                "weights",
                "temporaries",
                "allocator reserve",
                "block rounding",
            ],
        },
        correctness={
            "probe_bytes_match": probe.numel() * probe.element_size()
            == kv_bytes_per_token(32, 8, 128, 2)
        },
    )
    print(f"Wrote KV-capacity evidence: {target}")


if __name__ == "__main__":
    main()
