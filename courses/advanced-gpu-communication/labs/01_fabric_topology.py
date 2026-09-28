"""Qualify rank placement and collective correctness on one or two eight-H100 nodes."""

import argparse
import json
import socket
import subprocess
import sys

from common import (
    add_common_args,
    close_distributed,
    validate_common_args,
    write_result,
)
from fabric_common import all_correct, initialize, measure


def validate_placement(rows, world):
    if len(rows) != world or {r[0] for r in rows} != set(range(world)):
        raise ValueError("Missing or duplicate global ranks")
    hosts = {r[2] for r in rows}
    if len(hosts) != world // 8:
        raise ValueError("Expected one host per group of eight ranks")
    for host in hosts:
        local = [r for r in rows if r[2] == host]
        if {r[1] for r in local} != set(range(8)) or len({r[3] for r in local}) != 8:
            raise ValueError("Ranks do not own eight distinct local GPUs")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch, rank, world, local, env = initialize(args.seed)
    try:
        props = torch.cuda.get_device_properties(local)
        rows = [None] * world
        torch.distributed.all_gather_object(
            rows, (rank, local, socket.gethostname(), str(props.uuid))
        )
        validate_placement(rows, world)
        guard = None
        if local == 0:
            guard = json.loads(
                subprocess.check_output(
                    [sys.executable, "tools/fabric_guard.py"], text=True
                )
            )
        guards = [None] * world
        torch.distributed.all_gather_object(guards, guard)
        counts = [g for g in guards if g is not None]
        value = torch.zeros(1, device="cuda")

        def collective():
            value.fill_(rank + 1)
            torch.distributed.all_reduce(value)

        times = measure(torch, collective, args.warmup, args.iterations)
        expected = world * (world + 1) // 2
        all_correct(torch, value.item() == expected, "Collective sum is incorrect")
        if rank == 0:
            print(
                write_result(
                    args,
                    lab_id="01_fabric_topology",
                    environment=env,
                    measurements={
                        "ranks": world,
                        "nodes": world // 8,
                        "gpus_per_node": 8,
                        "nvlink_peers_per_gpu": min(
                            g["nvlink_peers_per_gpu"] for g in counts
                        ),
                        "active_ib_ports_per_node_min": min(
                            g["active_ib_ports"] for g in counts
                        ),
                        "collective_sum": expected,
                        "all_reduce": times,
                    },
                    correctness={
                        "distinct_gpu_per_rank": True,
                        "all_reduce_exact": True,
                        "hardware_guard_passed": True,
                    },
                )
            )
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
