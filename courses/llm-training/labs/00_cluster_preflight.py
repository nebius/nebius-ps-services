"""Verify the two-node, one-full-H100-per-node Slurm learning environment."""

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    rank, world_size, _ = init_nccl(torch)
    try:
        value = torch.tensor(float(rank + 1), device="cuda")
        torch.distributed.all_reduce(value)
        hosts: list[str | None] = [None] * world_size
        torch.distributed.all_gather_object(hosts, socket.gethostname())
        if rank == 0:
            if len(set(hosts)) != 2 or float(value) != 3.0:
                raise SystemExit(
                    "Expected two distinct hosts and an all-reduce result of 3.0."
                )
            target = write_result(
                args,
                lab_id="00_cluster_preflight",
                environment=environment,
                measurements={
                    "world_size": world_size,
                    "distinct_host_count": len(set(hosts)),
                },
                correctness={"two_nodes": True, "nccl_all_reduce": True},
            )
            print(f"Two-node H100 preflight passed: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
