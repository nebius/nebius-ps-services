"""Compare host and CUDA buffers with validated, bidirectional InfiniBand RDMA writes."""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

from course_evidence import allocation_gpu_family
from common import add_common_args, validate_common_args, write_result
from fabric_tools import PINS, qualified_binary, rdma_result


def vendor_command(binary, device, memory, size, iterations, depth, port, output):
    command = [
        str(binary),
        "-d",
        device,
        "-s",
        str(size),
        "-n",
        str(iterations),
        "-t",
        str(depth),
        "-p",
        str(port),
        "-b",
        "--report_gbits",
        "--data_validation",
        "--out_json",
        "--out_json_file=" + str(output),
    ]
    if memory != "host":
        command += ["--use_cuda=0"]
    if memory == "cuda-dmabuf":
        command += ["--use_cuda_dmabuf"]
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.set_defaults(iterations=1000, warmup=0)
    parser.add_argument(
        "--memory", choices=("host", "cuda-peermem", "cuda-dmabuf"), default="host"
    )
    parser.add_argument("--tx-depth", type=int, choices=(32, 64, 128, 256), default=128)
    parser.add_argument("--server-device", required=True)
    parser.add_argument("--client-device", required=True)
    args = parser.parse_args()
    validate_common_args(args)
    if args.iterations < 32 or args.warmup != 0:
        parser.error("Validated vendor runs require --iterations >=32 and --warmup 0")
    if (
        not os.environ.get("SLURM_JOB_ID")
        or os.environ.get("SLURM_JOB_NUM_NODES") != "2"
    ):
        parser.error("Use the two-node fabric_tools.sbatch allocation")
    if os.environ.get("COURSE_PROFILE_TOOL", "none") != "none":
        parser.error(
            "RDMA evidence comes from validated vendor reports, not CUDA kernel replay"
        )
    if any(
        not re.fullmatch(r"mlx5_[0-9]+", d)
        for d in (args.server_device, args.client_device)
    ):
        parser.error("Select the topology-qualified mlx5 device on each node")
    binary = qualified_binary("perftest")
    nodes = subprocess.check_output(
        ["scontrol", "show", "hostnames", os.environ["SLURM_JOB_NODELIST"]], text=True
    ).split()
    if len(nodes) != 2 or len(set(nodes)) != 2:
        parser.error("Expected two distinct allocated nodes")
    port = 24000 + int(os.environ["SLURM_JOB_ID"]) % 20000
    size = 65536 if args.profile == "small" else 4 * 2**20
    folder = (
        args.output_dir / "07_rdma_bandwidth" / ("vendor-" + args.run_id)
    ).resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    processes = []
    try:
        for index, role in enumerate(("server", "client")):
            command = vendor_command(
                binary,
                (args.server_device, args.client_device)[index],
                args.memory,
                size,
                args.iterations,
                args.tx_depth,
                port,
                folder / (role + ".json"),
            )
            if role == "client":
                command.append(nodes[0])
            step = [
                "srun",
                "--exclusive",
                "--nodes=1",
                "--ntasks=1",
                "--gpus-per-task=8",
                "--nodelist=" + nodes[index],
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
            ]
            process = subprocess.Popen(step, start_new_session=True)
            processes.append(process)
            if role == "server":
                deadline = time.monotonic() + 60
                while not (folder / "ready").is_file():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Server readiness failed; inspect private endpoint log"
                        )
                    time.sleep(0.1)
        for process in processes:
            if process.wait(timeout=240):
                raise RuntimeError(
                    "RDMA endpoint failed; no comparison artifact was accepted"
                )
        reports = [
            rdma_result(
                json.loads((folder / (role + ".json")).read_text()),
                (folder / (role + ".log")).read_text(),
                size,
                args.iterations,
                args.tx_depth,
                args.memory,
            )
            for role in ("server", "client")
        ]
        print(
            write_result(
                args,
                lab_id="07_rdma_bandwidth",
                environment={
                    "gpu_family": allocation_gpu_family(),
                    "nodes": 2,
                    "gpus_per_node": 8,
                    "vendor_revision": PINS["perftest"],
                },
                measurements={
                    "payload_bytes": size,
                    "iterations": args.iterations,
                    "bidirectional_Gbps": min(r["bidirectional_Gbps"] for r in reports),
                    "validated_bytes_min": min(r["validated_bytes"] for r in reports),
                },
                correctness={
                    "both_endpoints_validated_bytes": True,
                    "infiniBand_rc_confirmed": True,
                },
            )
        )
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


if __name__ == "__main__":
    main()
