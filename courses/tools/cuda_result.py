#!/usr/bin/env python3
"""Convert the course CUDA program's successful bounded text report to private JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "labs"))
from course_evidence import atomic_result, gpu_family  # noqa: E402


def workload_profile(arguments):
    if "--profile" not in arguments:
        return "large"
    index = arguments.index("--profile")
    if arguments.count("--profile") != 1 or index + 1 >= len(arguments):
        raise ValueError("Provide one --profile small|large")
    profile = arguments[index + 1]
    if profile not in ("small", "large"):
        raise ValueError("--profile must be small or large")
    return profile


def report_gpu_family(text):
    names = re.findall(r"^gpu_name=(.+)$", text, re.MULTILINE)
    capabilities = re.findall(r"^gpu_compute_capability=(.+)$", text, re.MULTILINE)
    if len(names) != 1 or capabilities != ["9.0"]:
        raise ValueError("CUDA result must contain one observed full SM90 GPU identity")
    return gpu_family(names[0])


def numeric(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite CUDA result")
    return int(number) if number.is_integer() else number


def parse_report(text, lab):
    result = {}
    cases = []
    current = None
    identities = []
    schemas = 0
    completed = 0
    for line in text.splitlines():
        if line == "sweep_point_begin":
            if current is not None:
                raise ValueError("Nested CUDA sweep")
            current = {}
            continue
        if line == "sweep_point_end":
            if current is None:
                raise ValueError("Unmatched CUDA sweep end")
            cases.append(current)
            current = None
            continue
        if line.startswith("case "):
            row = {}
            if "correctness=passed" not in line.split():
                raise ValueError("CUDA case is missing successful correctness")
            for key, value in re.findall(
                r"(?:^|\s)([a-zA-Z][a-zA-Z0-9_]*)=([^\s]+)", line
            ):
                if key == "correctness":
                    if value != "passed":
                        raise ValueError("CUDA case failed correctness")
                else:
                    if key in row:
                        raise ValueError(f"Duplicate CUDA case metric: {key}")
                    row[key] = numeric(value)
            if not row:
                raise ValueError("Empty CUDA case")
            cases.append(row)
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "course_checks":
            if value != "passed":
                raise ValueError("CUDA completion checks failed")
            completed += 1
            continue
        if (
            "correctness" in key
            or key in {"both_cpu_references", "outputs_match_each_other"}
        ) and value != "passed":
            raise ValueError("CUDA report contains a failed correctness marker")
        if key == "lab_id":
            identities.append(value)
            continue
        if key == "schema":
            schemas += 1
            if value != "gpu-course-result/v1":
                raise ValueError("Unsupported CUDA result schema")
            continue
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", key):
            continue
        if key == "variant_order":
            if value not in ("baseline-first", "candidate-first") or key in result:
                raise ValueError("Invalid or duplicate CUDA trial order")
            result[key] = value
            continue
        try:
            number = numeric(value)
        except ValueError:
            if value.lower() in ("nan", "inf", "-inf"):
                raise
            continue
        target = current if current is not None else result
        if key == "maximum_absolute_error":
            target[key] = max(number, target.get(key, 0))
            continue
        if key in target:
            raise ValueError(f"Duplicate unscoped CUDA metric: {key}")
        target[key] = number
    if (
        current is not None
        or not identities
        or set(identities) != {lab}
        or not schemas
        or completed != 1
    ):
        raise ValueError("Incomplete or mismatched CUDA result")
    if lab == "07_resource_sweep" and len(cases) != 6:
        raise ValueError("CUDA resource sweep requires all six cases")
    if lab == "08_async_pipeline" and len(cases) not in (1, 4):
        raise ValueError("Incomplete CUDA pipeline sweep")
    if cases:
        result["cases"] = cases
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    lab = args.executable.stem
    if any(v in ("--help", "-h") for v in args.arguments):
        raise SystemExit(subprocess.call([str(args.executable), *args.arguments]))
    inventory = json.loads((ROOT / "reference/observability.json").read_text())["labs"]
    if lab not in inventory:
        parser.error("Executable is not a course lab")
    if not os.environ.get("SLURM_JOB_ID"):
        parser.error("Run inside the Slurm allocation")
    image = os.environ.get("CUDA_IMAGE_DIGEST", "")
    if "@sha256:" not in image:
        parser.error("Record the pinned CUDA runtime image")
    os.umask(0o077)
    started = time.time()
    run_id = secrets.token_hex(6)
    log = ROOT / "results" / f"{lab}-run-{run_id}.log"
    log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # stdout stays bounded; it contains only course-owned scalar diagnostic output.
    command = [
        sys.executable,
        str(ROOT / "tools/profile_lab.py"),
        "--lab",
        lab,
        "--",
        str(args.executable),
        *args.arguments,
    ]
    with log.open("x") as stream:
        process = subprocess.Popen(
            command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True
        )

        def stop(signum, _frame):
            if process.poll() is None:
                os.killpg(process.pid, signum)

        previous = {
            sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            while process.poll() is None:
                if log.stat().st_size > 16 * 1024 * 1024:
                    stop(signal.SIGTERM, None)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        stop(signal.SIGKILL, None)
                        process.wait()
                    raise SystemExit("CUDA report exceeded its bounded size")
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            status = process.returncode
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    if status:
        raise SystemExit(
            f"CUDA run failed ({status}); inspect {log}. No successful artifact was published."
        )
    if log.stat().st_size > 16 * 1024 * 1024:
        raise SystemExit("CUDA report exceeded its bounded size")
    report_text = log.read_text()
    measurements = parse_report(report_text, lab)
    print(report_text, end="")
    parameters = {
        "work_iterations": None,
        "implementation_sha256": hashlib.sha256(
            args.executable.read_bytes()
        ).hexdigest(),
    }
    if "--work-iterations" in args.arguments:
        parameters["work_iterations"] = int(
            args.arguments[args.arguments.index("--work-iterations") + 1]
        )
    if lab == "01_vector_add":
        parameters["threads"] = measurements["threads_per_block"]
    result = {
        "schema": "gpu-course-result/v1",
        "lab_id": lab,
        "run_id": run_id,
        "profile": workload_profile(args.arguments),
        "seed": None,
        "environment": {
            "gpu_family": report_gpu_family(report_text),
            "runtime_identity": image,
            "cuda_device_contract": "full-sm90",
        },
        "measurements": measurements,
        "correctness": {"program_completed_checks": True},
        "experiment": {
            "started_unix_seconds": started,
            "ended_unix_seconds": time.time(),
            "slurm_job_id": int(os.environ["SLURM_JOB_ID"]),
            "rank": int(os.environ.get("SLURM_PROCID", "0")),
            "instrumented": os.environ.get("COURSE_PROFILE_TOOL", "none") != "none",
            "parameters": parameters,
        },
    }
    target = log.with_suffix(".json")
    atomic_result(target, json.dumps(result, indent=2) + "\n")
    print(f"Completed CUDA result: {target}")


if __name__ == "__main__":
    main()
