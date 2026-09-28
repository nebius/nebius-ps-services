"""Strict vendor output contracts and private, owned RDMA endpoint execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import time
from pathlib import Path

PINS = {
    "nvbandwidth": "82fc4e8c6afa0babb8687793678f615b3b8d793e",
    "perftest": "b513a77278c8061ca6c4dcd1a95d08801c6e7623",
    "perftest_read_lat": "b513a77278c8061ca6c4dcd1a95d08801c6e7623",
}


def qualified_binary(name):
    prefix = Path(os.environ.get("COURSE_TOOLS", "")) / "fabric"
    manifest = json.loads((prefix / "builds.json").read_text())
    record = manifest[name]
    binary = prefix / record["binary"]
    if (
        record["revision"] != PINS[name]
        or hashlib.sha256(binary.read_bytes()).hexdigest() != record["sha256"]
    ):
        raise ValueError(
            "Vendor build differs from the shared README setup's pinned build receipt"
        )
    return binary


def nvbandwidth_result(document, testcase, buffer_mib, samples):
    root = document["nvbandwidth"]
    if root.get("error") or buffer_mib <= 0 or samples <= 0:
        raise ValueError("Failed or mismatched nvbandwidth experiment")
    tests = root["testcases"]
    if (
        len(tests) != 1
        or tests[0]["name"] != testcase
        or tests[0]["status"] != "Passed"
        or tests[0].get("error")
    ):
        raise ValueError("Expected exactly one passing peer-bandwidth test")
    matrix = tests[0]["bandwidth_matrix"]
    if len(matrix) != 8 or any(len(row) != 8 for row in matrix):
        raise ValueError("Expected a complete eight-GPU peer matrix")
    pairs = {}
    for source, row in enumerate(matrix):
        for destination, value in enumerate(row):
            if source == destination:
                continue
            number = float(value)
            if not math.isfinite(number) or number <= 0:
                raise ValueError(
                    "Every directed GPU pair must have finite positive bandwidth"
                )
            pairs[f"gpu{source}_to_gpu{destination}"] = number
    return {
        "pairs_GBps": pairs,
        "pairs": [
            {
                "source": source,
                "destination": destination,
                "GBps": pairs[f"gpu{source}_to_gpu{destination}"],
            }
            for source in range(8)
            for destination in range(8)
            if source != destination
        ],
        "minimum_GBps": min(pairs.values()),
        "mean_GBps": sum(pairs.values()) / len(pairs),
        "pair_count": len(pairs),
    }


def rdma_result(document, log, size, iterations, depth, memory):
    info, result = document["test_info"], document["results"]
    if (
        info["Connection_type"] != "RC"
        or info["Link_type"] != "IB"
        or info["TX_depth"] != depth
        or result["MsgSize"] != size
        or result["n_iterations"] != iterations
    ):
        raise ValueError(
            "RDMA report does not match the requested workload or transport"
        )
    if memory != "host" and info.get("cuda_device") != 0:
        raise ValueError("CUDA memory registration was not reported")
    if memory == "host" and "cuda_device" in info:
        raise ValueError("Host baseline unexpectedly used CUDA memory")
    passed = re.findall(
        r"VALIDATION: PASSED(?: \([^)]*\))? - ([0-9]+) chunks, ([0-9]+) bytes", log
    )
    if (
        len(passed) != 1
        or any(int(n) <= 0 for n in passed[0])
        or re.search(r"VALIDATION: (?:FAILED|ERROR|WARNING)", log)
    ):
        raise ValueError(
            "Endpoint must validate actual transferred bytes without errors"
        )
    bandwidth = float(result["BW_average"])
    if not math.isfinite(bandwidth) or bandwidth <= 0:
        raise ValueError("Invalid RDMA bandwidth")
    return {"bidirectional_Gbps": bandwidth, "validated_bytes": int(passed[0][1])}


def owns_listener(pid, port):
    """Inspect only this child process's socket inodes; never connect to the test port."""
    try:
        inodes = {
            p.readlink().name.removeprefix("socket:[").removesuffix("]")
            for p in Path(f"/proc/{pid}/fd").iterdir()
            if p.is_symlink()
        }
        for family in ("tcp", "tcp6"):
            for line in Path(f"/proc/{pid}/net/{family}").read_text().splitlines()[1:]:
                fields = line.split()
                if (
                    fields[3] == "0A"
                    and int(fields[1].rsplit(":", 1)[1], 16) == port
                    and fields[9] in inodes
                ):
                    return True
    except (FileNotFoundError, ProcessLookupError):
        pass
    return False


def endpoint():
    parser = argparse.ArgumentParser(
        description="Owned endpoint for the RDMA labs; invoked by their Slurm jobs."
    )
    parser.add_argument("--role", choices=("server", "client"), required=True)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not os.environ.get("SLURM_JOB_ID"):
        parser.error("An allocated job and vendor command are required")
    os.umask(0o077)
    with (args.folder / (args.role + ".log")).open("x") as log:
        child = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )

        def terminate(signum, frame):
            if child.poll() is None:
                os.killpg(child.pid, signum)

        previous = {
            s: signal.signal(s, terminate) for s in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            if args.role == "server":
                deadline = time.monotonic() + 30
                while not owns_listener(child.pid, args.port):
                    if child.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Owned server did not open its test listener"
                        )
                    time.sleep(0.1)
                (args.folder / "ready").touch(exist_ok=False)
            status = child.wait(timeout=180)
            if status:
                raise RuntimeError(
                    f"{args.role} vendor process failed with status {status}"
                )
        finally:
            terminate(signal.SIGTERM, None)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    endpoint()
