"""Verify the two-node H100 optimization lab platform."""

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
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    rank, world_size, _ = init_nccl(torch)
    nodes: list[str | None] = [None] * world_size
    torch.distributed.all_gather_object(nodes, socket.gethostname())
    if len(set(nodes)) != 2:
        raise SystemExit("The full lab suite requires two distinct nodes.")
    probe = torch.tensor([rank + 1.0], device="cuda")
    torch.distributed.all_reduce(probe)
    if float(probe.item()) != 3.0:
        raise SystemExit("NCCL all-reduce validation failed.")
    if rank == 0:
        target = write_result(
            args,
            lab_id="00_cluster_preflight",
            environment={**environment, "node_count": 2, "rank_count": world_size},
            measurements={"nccl_backend": torch.distributed.get_backend()},
            correctness={"topology": True, "all_reduce": True},
        )
        print(f"Preflight passed: {target}")
    close_distributed(torch)


if __name__ == "__main__":
    main()
