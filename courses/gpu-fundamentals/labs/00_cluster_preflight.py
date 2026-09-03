"""Verify the two-node, one-H100-per-node course platform."""

from __future__ import annotations

import argparse
import socket

from common import (
    add_common_args,
    close_distributed,
    init_nccl,
    load_torch,
    require_h100,
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
    rank, world_size, _ = init_nccl(torch)
    names: list[str | None] = [None] * world_size
    torch.distributed.all_gather_object(names, socket.gethostname())
    unique_nodes = len(set(names))
    if unique_nodes != 2:
        raise SystemExit(f"Expected two distinct nodes, observed {unique_nodes}.")
    probe = torch.tensor([float(rank + 1)], device="cuda")
    torch.distributed.all_reduce(probe)
    collective_ok = float(probe.item()) == 3.0
    if not collective_ok:
        raise SystemExit("NCCL all-reduce returned an unexpected value.")
    if rank == 0:
        environment["node_count"] = unique_nodes
        environment["rank_count"] = world_size
        target = write_result(
            args,
            lab_id="00_cluster_preflight",
            environment=environment,
            measurements={"nccl_backend": torch.distributed.get_backend()},
            correctness={"two_distinct_nodes": True, "all_reduce": True},
        )
        print(f"Preflight passed; sanitized result: {target}")
    close_distributed(torch)


if __name__ == "__main__":
    main()
