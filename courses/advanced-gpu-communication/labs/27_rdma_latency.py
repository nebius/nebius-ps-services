"""Measure completed RDMA READ latency separately from bulk write bandwidth."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time

from course_evidence import allocation_gpu_family
from common import add_common_args, validate_common_args, write_result
from fabric_tools import PINS, qualified_binary
from job_processes import Processes, allocated_nodes, private_folder, step


def latency_result(document, size, iterations, memory):
    info, result = document["test_info"], document["results"]
    if (
        info["Connection_type"] != "RC"
        or info["Link_type"] != "IB"
        or result["MsgSize"] != size
        or result["n_iterations"] != iterations
    ):
        raise ValueError("Latency report has a different transport or workload")
    if (memory == "host" and "cuda_device" in info) or (
        memory != "host" and info.get("cuda_device") != 0
    ):
        raise ValueError("Report memory registration differs from the request")
    values = {
        "median_us": result["t_typical"],
        "mean_us": result["t_avg"],
        "p99_us": result["percentile_99"],
    }
    if any(
        type(value) not in (int, float) or not math.isfinite(value) or value <= 0
        for value in values.values()
    ):
        raise ValueError("Invalid latency distribution")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.set_defaults(warmup=0, iterations=1000)
    parser.add_argument(
        "--memory", choices=("host", "cuda-peermem", "cuda-dmabuf"), default="host"
    )
    parser.add_argument("--server-device", required=True)
    parser.add_argument("--client-device", required=True)
    args = parser.parse_args()
    validate_common_args(args)
    if args.iterations < 100 or args.warmup != 0:
        parser.error("Use --iterations >=100 and --warmup 0; the vendor owns timing")
    if not all(
        re.fullmatch(r"mlx5_[0-9]+", name)
        for name in (args.server_device, args.client_device)
    ):
        parser.error("Select observed mlx5 devices from the topology report")
    if os.environ.get("COURSE_PROFILE_TOOL", "none") != "none":
        parser.error("CUDA kernel replay is inapplicable to RDMA completion latency")
    nodes = allocated_nodes()
    binary = qualified_binary("perftest_read_lat")
    folder = private_folder(args, "27_rdma_latency")
    size = 64 if args.workload == "small" else 4096
    port = 35000 + int(os.environ["SLURM_JOB_ID"]) % 10000
    with Processes(folder) as processes:
        children = []
        for rank, role in enumerate(("server", "client")):
            command = [
                str(binary),
                "-d",
                (args.server_device, args.client_device)[rank],
                "-s",
                str(size),
                "-n",
                str(args.iterations),
                "-p",
                str(port),
                "--out_json",
                "--out_json_file=" + str(folder / (role + ".json")),
            ]
            if args.memory != "host":
                command.append("--use_cuda=0")
            if args.memory == "cuda-dmabuf":
                command.append("--use_cuda_dmabuf")
            if role == "client":
                command.append(nodes[0])
            children.append(
                processes.start(
                    role + "-step",
                    step(
                        nodes[rank],
                        [
                            sys.executable,
                            "labs/fabric_tools.py",
                            "--role",
                            role,
                            "--folder",
                            str(folder),
                            "--port",
                            str(port),
                            "--",
                            *command,
                        ],
                    ),
                )
            )
            if role == "server":
                deadline = time.monotonic() + 40
                while not (folder / "ready").exists():
                    processes.healthy()
                    if time.monotonic() > deadline:
                        raise RuntimeError("Owned RDMA endpoint did not become ready")
                    time.sleep(0.1)
        for child in children:
            processes.wait(child, timeout=240)
    report = latency_result(
        json.loads((folder / "client.json").read_text()),
        size,
        args.iterations,
        args.memory,
    )
    print(
        write_result(
            args,
            lab_id="27_rdma_latency",
            environment={
                "gpu_family": allocation_gpu_family(),
                "allocated_gpus": 16,
                "vendor_revision": PINS["perftest_read_lat"],
            },
            measurements={**report, "payload_bytes": size},
            correctness={
                "rc_operations_completed": True,
                "requested_memory_and_transport_reported": True,
            },
        )
    )


if __name__ == "__main__":
    main()
