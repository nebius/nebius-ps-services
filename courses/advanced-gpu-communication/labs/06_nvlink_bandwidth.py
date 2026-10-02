"""Measure all 56 directed peer paths on one eight-H100 NVLink/NVSwitch node."""

import argparse
import json
import os
import subprocess

from course_evidence import allocation_gpu_family
from common import add_common_args, validate_common_args, write_result
from fabric_tools import PINS, nvbandwidth_result, qualified_binary
from vendor_capture import add_worker_prefix, validate_worker_prefix, worker_command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--engine", choices=("ce", "sm"), default="ce")
    parser.set_defaults(warmup=0)
    add_worker_prefix(parser)
    args = parser.parse_args()
    validate_worker_prefix(args, ucx=False)
    validate_common_args(args)
    if args.warmup != 0:
        parser.error("nvbandwidth owns warm-up; leave --warmup 0")
    if os.environ.get("SLURM_JOB_NUM_NODES") != "1":
        parser.error("Use --nodes=1 with fabric_tools.sbatch")
    binary = qualified_binary("nvbandwidth")
    size = 64 if args.workload == "small" else 512
    testcase = "device_to_device_memcpy_write_" + args.engine
    folder = args.output_dir / "06_nvlink_bandwidth" / ("vendor-" + args.run_id)
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    command = [
        "srun",
        "--nodes=1",
        "--ntasks=1",
        "--gpus-per-task=8",
        *worker_command(
            [
                str(binary),
                "--format",
                "json",
                "--testcase",
                testcase,
                "--bufferSize",
                str(size),
                "--testSamples",
                str(args.iterations),
            ],
            folder / "nvbandwidth.json",
            prefix=args.worker_prefix,
        ),
    ]
    with (
        (folder / "capture.log").open("x") as stdout,
        (folder / "stderr.log").open("x") as stderr,
    ):
        subprocess.run(command, stdout=stdout, stderr=stderr, check=True, timeout=600)
    measured = nvbandwidth_result(
        json.loads((folder / "nvbandwidth.json").read_text()),
        testcase,
        size,
        args.iterations,
    )
    print(
        write_result(
            args,
            lab_id="06_nvlink_bandwidth",
            environment={
                "gpu_family": allocation_gpu_family(),
                "gpus_per_node": 8,
                "vendor_revision": PINS["nvbandwidth"],
            },
            measurements={**measured, "buffer_mib": size},
            correctness={
                "vendor_verification_enabled": True,
                "all_directed_pairs_passed": True,
            },
        )
    )


if __name__ == "__main__":
    main()
