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
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "labs"))
from course_evidence import atomic_result, gpu_family  # noqa: E402


def workload_profile(arguments):
    if "--workload" not in arguments:
        return "large"
    index = arguments.index("--workload")
    if arguments.count("--workload") != 1 or index + 1 >= len(arguments):
        raise ValueError("Provide one --workload small|large")
    profile = arguments[index + 1]
    if profile not in ("small", "large"):
        raise ValueError("--workload must be small or large")
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
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--started", type=float, required=True)
    parser.add_argument("--ended", type=float, required=True)
    parser.add_argument("executable", type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    lab = args.executable.stem
    inventory = json.loads((ROOT / "reference/observability.json").read_text())["labs"]
    if lab not in inventory or not os.environ.get("SLURM_JOB_ID"):
        parser.error("Require a course executable inside its Slurm allocation")
    image = os.environ.get("CUDA_IMAGE_DIGEST", "")
    if "@sha256:" not in image:
        parser.error("Record the pinned CUDA runtime image")
    if not 0 < args.started <= args.ended <= time.time() + 1:
        parser.error("Require the actual successful execution window")
    log = args.input
    if log.is_symlink() or not log.is_file() or log.stat().st_size > 16 * 1024 * 1024:
        parser.error("Require a regular bounded successful CUDA stdout report")
    report_text = log.read_text()
    measurements = parse_report(report_text, lab)
    started = args.started
    run_id = secrets.token_hex(6)
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
            "ended_unix_seconds": args.ended,
            "slurm_job_id": int(os.environ["SLURM_JOB_ID"]),
            "rank": int(os.environ.get("SLURM_PROCID", "0")),
            "instrumented": os.environ.get("COURSE_PROFILE_TOOL", "none") != "none",
            "parameters": parameters,
        },
    }
    target = Path(os.environ["COURSE_RESULTS_DIR"]) / f"{lab}-run-{run_id}.json"
    atomic_result(target, json.dumps(result, indent=2) + "\n")
    print(f"Completed CUDA result: {target}")


if __name__ == "__main__":
    main()
