"""Compare NCCL all-reduce with an explicit two-level reduction of identical values."""

import argparse

from common import (
    add_common_args,
    close_distributed,
    validate_common_args,
    write_result,
)
from course_evidence import evidence_phase
from fabric_common import all_correct, initialize, measure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--layout", choices=("flat", "hierarchical"), default="flat")
    args = parser.parse_args()
    validate_common_args(args)
    torch, rank, world, local, env = initialize(args.seed)
    try:
        count = (1 if args.profile == "small" else 64) * 2**20 // 4
        # Every rank creates every group in the same order, including nonmembers.
        local_groups = [
            torch.distributed.new_group(list(range(start, start + 8)))
            for start in range(0, world, 8)
        ]
        leaders = list(range(0, world, 8))
        leader_group = torch.distributed.new_group(leaders)
        group = local_groups[rank // 8]
        leader = rank - local
        values = torch.empty(count, device="cuda", dtype=torch.float32)

        def operation():
            values.fill_(rank + 1)
            if args.layout == "flat":
                with evidence_phase("flat_all_reduce"):
                    torch.distributed.all_reduce(values)
            else:
                with evidence_phase("local_reduce"):
                    torch.distributed.reduce(values, dst=leader, group=group)
                with evidence_phase("leader_all_reduce"):
                    if local == 0:
                        torch.distributed.all_reduce(values, group=leader_group)
                with evidence_phase("local_broadcast"):
                    torch.distributed.broadcast(values, src=leader, group=group)

        times = measure(torch, operation, args.warmup, args.iterations)
        expected = world * (world + 1) // 2
        all_correct(
            torch, bool((values == expected).all().item()), "Reduced values differ"
        )
        if rank == 0:
            print(
                write_result(
                    args,
                    lab_id="11_collective_layout",
                    environment=env,
                    measurements={
                        "elements": count,
                        "payload_bytes": count * 4,
                        "ranks": world,
                        "collective": times,
                        "useful_GBps": count * 4 / times["median_ms"] / 1e6,
                    },
                    correctness={"all_elements_exact": True},
                )
            )
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
