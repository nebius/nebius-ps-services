"""Measure NIXL GPU-buffer transfers using its consistency-checked two-process benchmark."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
from pathlib import Path

from course_evidence import allocation_gpu_family
from common import add_common_args, validate_common_args, write_result
from job_processes import Processes, allocated_nodes, private_folder, start_etcd, step
from vendor_capture import worker_command


def parse_rows(text, block_bytes, batch_size):
    rows = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 10 or not all(
            re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value)
            for value in fields
        ):
            continue
        values = list(map(float, fields))
        if int(values[0]) != block_bytes or int(values[1]) != batch_size:
            raise ValueError("NIXL row differs from requested block or batch size")
        if (
            any(not math.isfinite(x) or x < 0 for x in values)
            or values[2] <= 0
            or values[3] <= 0
        ):
            raise ValueError("Invalid NIXL measurement")
        rows.append(
            {
                "block_bytes": int(values[0]),
                "batch_size": int(values[1]),
                "GBps": values[2],
                "amortized_item_us": values[3],
                "preparation_us": values[4],
                "post_us": values[6],
                "transfer_us": values[8],
                "transfer_p99_us": values[9],
            }
        )
    if len(rows) != 1:
        raise ValueError(
            "Expected exactly one two-process SG benchmark row; retain raw output and check the pinned NIXLBench format"
        )
    return rows[0]


def benchmark_command(binary, endpoint, group, args):
    size = 4096 if args.profile == "small" else 4 * 2**20
    return [
        str(binary),
        "--etcd_endpoints",
        endpoint,
        "--benchmark_group",
        group,
        "--backend",
        "UCX",
        "--scheme",
        "pairwise",
        "--mode",
        "SG",
        "--initiator_seg_type",
        "VRAM",
        "--target_seg_type",
        "VRAM",
        "--op_type",
        "WRITE",
        "--check_consistency",
        "--total_buffer_size",
        str(2**30),
        "--start_block_size",
        str(size),
        "--max_block_size",
        str(size),
        "--start_batch_size",
        "1",
        "--max_batch_size",
        "1",
        "--warmup_iter",
        str(args.warmup),
        "--num_iter",
        str(args.iterations),
        "--large_blk_iter_ftr",
        "1",
        "--enable_pt=" + str(args.progress_thread == "on").lower(),
        "--progress_threads",
        "1" if args.progress_thread == "on" else "0",
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.set_defaults(warmup=50, iterations=500)
    parser.add_argument("--progress-thread", choices=("off", "on"), default="off")
    args = parser.parse_args()
    validate_common_args(args)
    if args.iterations < 100:
        parser.error(
            "Use at least 100 measured transfers for the tail-latency investigation"
        )
    nodes = allocated_nodes()
    binary = Path(os.environ.get("COURSE_NIXLBENCH", ""))
    if not binary.is_file():
        parser.error("Complete the shared README setup and export COURSE_NIXLBENCH")
    folder = private_folder(args, "29_nixl_transfer")
    with Processes(folder) as processes:
        endpoint = start_etcd(processes, nodes[0])
        command = benchmark_command(binary, endpoint, "course-" + args.run_id, args)
        children = [
            processes.start(
                "rank" + str(rank),
                step(
                    node,
                    [
                        "env",
                        "CUDA_VISIBLE_DEVICES=0",
                        *worker_command(
                            command, folder / f"rank{rank}.stdout", ucx=True
                        ),
                    ],
                ),
            )
            for rank, node in enumerate(nodes)
        ]
        for child in children:
            processes.wait(child, timeout=600)
    reports = []
    for rank in range(2):
        text = (folder / f"rank{rank}.stdout").read_text()
        # Only the benchmark's reporting process prints the aggregated table.
        if re.search(r"^\s*[0-9]+\s+1\s+[0-9]", text, re.MULTILINE):
            reports.append(
                parse_rows(text, 4096 if args.profile == "small" else 4 * 2**20, 1)
            )
    if len(reports) != 1:
        raise ValueError("Expected one authoritative NIXL reporting process")
    print(
        write_result(
            args,
            lab_id="29_nixl_transfer",
            environment={
                "gpu_family": allocation_gpu_family(),
                "participating_gpus": 2,
                "allocated_gpus": 16,
                "nixl_candidate": "1.4.1",
                "launcher_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            },
            measurements=reports[0],
            correctness={
                "both_processes_succeeded": True,
                "consistency_check_enabled": True,
            },
        )
    )


if __name__ == "__main__":
    main()
