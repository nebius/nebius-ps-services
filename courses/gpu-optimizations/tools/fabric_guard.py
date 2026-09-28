#!/usr/bin/env python3
"""Read-only admission for the course's eight-H100, NVLink/InfiniBand workers."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "labs"))
from course_evidence import gpu_family


def inspect_worker(
    names: str,
    topology: str,
    ports: list[dict],
    *,
    mig_modes: str,
    visible_devices: int,
) -> dict:
    devices = [line.strip() for line in names.splitlines() if line.strip()]
    if len(devices) != 8:
        raise ValueError(
            "Advanced labs require eight full GPUs on every worker; the two one-GPU cluster is not eligible"
        )
    families = {gpu_family(name) for name in devices}
    if len(families) != 1:
        raise ValueError("Require the same GPU family throughout each worker")
    modes = [line.strip() for line in mig_modes.splitlines() if line.strip()]
    if (
        len(modes) != 8
        or any(mode != "Disabled" for mode in modes)
        or visible_devices != 8
    ):
        raise ValueError(
            "Require MIG disabled on all eight GPUs and eight full CUDA devices visible to the allocation"
        )
    rows = [
        line.split() for line in topology.splitlines() if re.match(r"^GPU\d+\s", line)
    ]
    if len(rows) != 8 or {row[0] for row in rows} != {f"GPU{i}" for i in range(8)}:
        raise ValueError("Cannot establish the eight-GPU topology matrix")
    if any(
        len(row) < 9
        or sum(bool(re.fullmatch(r"NV[1-9]\d*", item)) for item in row[1:9]) != 7
        for row in rows
    ):
        raise ValueError(
            "Every GPU must have NVLink paths to its seven peers; ask the owner to qualify NVSwitch/Fabric Manager health"
        )
    active = [
        p
        for p in ports
        if p["link_layer"] == "InfiniBand" and p["state"].startswith("4:")
    ]
    if not active:
        raise ValueError(
            "No active InfiniBand port; this allocation cannot qualify the fabric track"
        )
    return {
        "gpu_family": families.pop(),
        "gpus_per_node": 8,
        "nvlink_peers_per_gpu": 7,
        "active_ib_ports": len(active),
    }


def visible_cuda_devices() -> int:
    driver = ctypes.CDLL("libcuda.so.1")
    driver.cuInit.argtypes = [ctypes.c_uint]
    driver.cuInit.restype = ctypes.c_int
    driver.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
    driver.cuDeviceGetCount.restype = ctypes.c_int
    count = ctypes.c_int()
    if driver.cuInit(0) or driver.cuDeviceGetCount(ctypes.byref(count)):
        raise ValueError("CUDA driver cannot enumerate the allocated devices")
    return count.value


def worker_evidence(sysfs: Path = Path("/sys/class/infiniband")) -> dict:
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Run hardware checks on allocated workers, not the login node")
    names = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        text=True,
        timeout=20,
    )
    topology = subprocess.check_output(
        ["nvidia-smi", "topo", "-m"], text=True, timeout=20
    )
    ports = []
    for port in sorted(sysfs.glob("*/ports/*")):
        ports.append(
            {key: (port / key).read_text().strip() for key in ("state", "link_layer")}
        )
    modes = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=mig.mode.current", "--format=csv,noheader"],
        text=True,
        timeout=20,
    )
    return inspect_worker(
        names, topology, ports, mig_modes=modes, visible_devices=visible_cuda_devices()
    )


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        print(json.dumps(worker_evidence(), sort_keys=True))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"Fabric readiness failed: {exc}") from exc


if __name__ == "__main__":
    main()
