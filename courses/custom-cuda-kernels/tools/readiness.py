#!/usr/bin/env python3
"""Run a tiny GPU canary and verify usable Systems and Compute reports on one worker."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "labs"))
from course_evidence import atomic_result, gpu_family  # noqa: E402

TORCH_CANARY = r"""import re, torch
p=torch.cuda.get_device_properties(0)
assert torch.cuda.device_count()==1
assert re.search(r"\b(H100|H200)\b",p.name) and "MIG" not in p.name and (p.major,p.minor)==(9,0)
x=torch.ones(1024,device="cuda")
for _ in range(5): y=x+2
with torch.cuda.nvtx.range("ready_kernel"):
    y=x+2
    torch.cuda.synchronize()
assert bool(torch.all(y==3))
"""
CUDA_CANARY = r"""#include <cuda_runtime.h>
#include <nvtx3/nvToolsExt.h>
#include <cstring>
#include <regex>
__global__ void ready_kernel(float* x) { x[threadIdx.x]=3.0f; }
int main() {
 int count=0; if(cudaGetDeviceCount(&count)!=cudaSuccess || count!=1)return 2;
 cudaDeviceProp p; if(cudaGetDeviceProperties(&p,0)!=cudaSuccess || p.major!=9 || p.minor!=0 || !std::regex_search(p.name,std::regex("\\b(H100|H200)\\b")) || strstr(p.name,"MIG")) return 2;
 float* x; float h[32]; if(cudaMalloc(&x,sizeof(h))!=cudaSuccess) return 3;
 for(int i=0;i<5;++i)ready_kernel<<<1,32>>>(x);
 nvtxRangePushA("ready_kernel");ready_kernel<<<1,32>>>(x);cudaDeviceSynchronize();nvtxRangePop();
 if(cudaMemcpy(h,x,sizeof(h),cudaMemcpyDeviceToHost)!=cudaSuccess)return 4;
 cudaFree(x); for(float v:h)if(v!=3)return 5;return 0;
}
"""


def profiler_version(tool, text):
    pattern = {
        "nsys": r"^NVIDIA Nsight Systems version (\d{4}\.\d+\.\d+(?:\.\d+)?)(?:[-\s]|$)",
        "ncu": r"^Version (\d{4}\.\d+\.\d+(?:\.\d+)?)(?:\s|$)",
    }[tool]
    matches = re.findall(pattern, text, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"Cannot identify the installed {tool} version")
    return matches[0]


def verify_report_content(kernels_csv, ranges_csv, compute_csv, workload):
    kernels = list(csv.DictReader(io.StringIO(kernels_csv)))
    ranges = list(csv.DictReader(io.StringIO(ranges_csv)))
    # Compute can print deployment warnings before its CSV header, including
    # when a read-only container uses the installed stock sections in place.
    # Keep the complete output as evidence and parse only the identified table.
    records = csv.reader(io.StringIO(compute_csv))
    required = {"ID", "Kernel Name", "gpu__time_duration.sum"}
    header = next((row for row in records if required.issubset(row)), None)
    if header is None or len(header) != len(set(header)):
        raise ValueError("Compute raw report is missing a valid counter header")
    captures = []
    identity_column = header.index("ID")
    for row in records:
        if len(row) <= identity_column or not row[identity_column].isdigit():
            continue
        if len(row) != len(header):
            raise ValueError("Compute raw report contains a malformed capture row")
        captures.append(dict(zip(header, row, strict=True)))
    expected = "ready_kernel" if workload == "cuda" else "elementwise_kernel"
    if len(captures) != 1 or expected not in captures[0].get("Kernel Name", ""):
        raise ValueError("Compute must contain the one selected canary kernel")
    duration = float(captures[0].get("gpu__time_duration.sum", "nan"))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Compute canary duration counter is missing or invalid")

    # Raw symbols avoid different display-name simplifications in the two tools.
    name = captures[0]["Kernel Name"]
    matching = [row for row in kernels if row.get("Name", "") == name]
    if len(matching) != 1 or int(matching[0].get("Instances", "0")) < 6:
        raise ValueError(
            "Systems must contain the matching warmup and measured canary kernels"
        )
    selected = [row for row in ranges if row.get("Range") == ":ready_kernel"]
    if len(selected) != 1 or int(selected[0].get("Instances", "0")) != 1:
        raise ValueError("Systems must contain exactly one ready_kernel NVTX range")
    return captures[0]["Kernel Name"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=("torch", "cuda"), default="torch")
    args = parser.parse_args()
    if not os.environ.get("SLURM_JOB_ID"):
        parser.error("Run in a Slurm worker allocation")
    os.umask(0o077)
    started = time.time()
    run_id = secrets.token_hex(6)
    directory = ROOT / "results" / ("readiness-" + run_id)
    directory.mkdir(mode=0o700, parents=True)
    versions = {}
    for tool in ("nsys", "ncu"):
        text = subprocess.check_output(
            [tool, "--version"], text=True, stderr=subprocess.STDOUT, timeout=15
        )
        versions[tool] = profiler_version(tool, text)
    source = directory / ("canary.py" if args.workload == "torch" else "canary.cu")
    source.write_text(TORCH_CANARY if args.workload == "torch" else CUDA_CANARY)
    if args.workload == "torch":
        command = [sys.executable, str(source)]
    else:
        executable = directory / "canary"
        subprocess.run(
            ["nvcc", "-arch=sm_90", str(source), "-o", str(executable)],
            check=True,
            timeout=90,
        )
        command = [str(executable)]
    subprocess.run(command, check=True, timeout=30)
    systems = directory / "systems"
    compute = directory / "compute"
    systems_environment = os.environ.copy()
    systems_environment.pop("DEBUGINFOD_URLS", None)
    subprocess.run(
        [
            "nsys",
            "profile",
            "--trace=cuda,nvtx",
            "--sample=none",
            "--cpuctxsw=none",
            "--discard-environment=true",
            "--output",
            str(systems),
            *command,
        ],
        check=True,
        timeout=120,
        env=systems_environment,
    )
    subprocess.run(
        [
            "ncu",
            "--nvtx",
            "--nvtx-include",
            "ready_kernel/",
            "--launch-count",
            "1",
            "--set",
            "basic",
            "--clock-control",
            "none",
            "--export",
            str(compute),
            *command,
        ],
        check=True,
        timeout=180,
    )
    reports = {
        "systems_report_bytes": systems.with_suffix(".nsys-rep").stat().st_size,
        "compute_report_bytes": compute.with_suffix(".ncu-rep").stat().st_size,
    }
    if min(reports.values()) < 128:
        raise SystemExit("A capture report is missing or empty")
    subprocess.run(
        [
            "nsys",
            "stats",
            "--report",
            "cuda_gpu_kern_sum:mangled,nvtx_sum",
            "--format",
            "csv",
            "--output",
            str(directory / "evidence"),
            str(systems.with_suffix(".nsys-rep")),
        ],
        check=True,
        timeout=60,
    )
    compute_csv = subprocess.check_output(
        [
            "ncu",
            "--import",
            str(compute.with_suffix(".ncu-rep")),
            "--page",
            "raw",
            "--csv",
            "--print-kernel-base",
            "mangled",
        ],
        text=True,
        timeout=60,
    )
    (directory / "compute.csv").write_text(compute_csv)
    kernel = verify_report_content(
        (directory / "evidence_cuda_gpu_kern_sum_mangled.csv").read_text(),
        (directory / "evidence_nvtx_sum.csv").read_text(),
        compute_csv,
        args.workload,
    )
    identity = (
        subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=uuid,driver_version,name",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=10,
        )
        .strip()
        .splitlines()
    )
    if len(identity) != 1:
        raise SystemExit("Readiness requires exactly one full GPU per worker")
    gpu_uuid, driver, name = [part.strip() for part in identity[0].split(",")]
    family = gpu_family(name)
    if not gpu_uuid.startswith("GPU-"):
        raise SystemExit("Expected a full GPU UUID")
    versions["driver_version"] = driver
    versions["runtime_identity"] = os.environ.get("COURSE_RUNTIME_ID", sys.executable)
    payload = {
        "schema": "gpu-course-result/v1",
        "lab_id": "environment_readiness",
        "profile": "small",
        "seed": None,
        "run_id": run_id,
        "worker_identity": socket.gethostname(),
        "gpu_uuid": gpu_uuid,
        "environment": {
            "gpu_family": family,
            **versions,
            "canary": args.workload,
        },
        "measurements": reports,
        "correctness": {
            "canary_passed": True,
            "systems_report_readable": True,
            "compute_report_readable": True,
            "matching_canary_kernel": True,
            "expected_nvtx_range": True,
        },
        "experiment": {
            "started_unix_seconds": started,
            "ended_unix_seconds": time.time(),
            "instrumented": False,
            "parameters": {},
            "slurm_job_id": int(os.environ["SLURM_JOB_ID"]),
            "rank": int(os.environ.get("SLURM_PROCID", "0")),
        },
    }
    target = ROOT / "results" / f"environment_readiness-run-{run_id}.json"
    atomic_result(target, json.dumps(payload, indent=2) + "\n")
    print(
        f"Readiness result: {target}. Matched {kernel}. Open both reports in matching viewers before declaring readiness."
    )


if __name__ == "__main__":
    main()
