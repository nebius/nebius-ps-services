#!/usr/bin/env python3
"""Validate three training capstone trials and write a scoped decision."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
from pathlib import Path
from typing import Any


def load_trial(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("schema") != "gpu-course-result/v1":
        raise SystemExit(f"Unexpected schema in {path}")
    if payload.get("lab_id") != "31_training_capstone":
        raise SystemExit(f"Unexpected lab_id in {path}")
    if payload.get("correctness", {}).get("full_update_close") is not True:
        raise SystemExit(f"Correctness did not pass in {path}")
    measurements = payload.get("measurements")
    environment = payload.get("environment")
    if (
        payload.get("profile") not in ("smoke", "h100")
        or not isinstance(environment, dict)
        or environment.get("gpu_family") != "NVIDIA H100"
        or any(
            not isinstance(environment.get(key), str) or not environment[key].strip()
            for key in ("torch_version", "cuda_version")
        )
        or not isinstance(measurements, dict)
        or type(payload.get("seed")) is not int
        or not isinstance(payload.get("run_id"), str)
        or re.fullmatch(r"[0-9a-f]{12}", payload["run_id"]) is None
    ):
        raise SystemExit(f"Invalid or incomplete capstone contract in {path}")
    for key, minimum in (("warmup", 0), ("iterations", 1)):
        value = measurements.get(key)
        if type(value) is not int or value < minimum:
            raise SystemExit(f"Invalid {key} in capstone contract: {path}")
    shape = measurements.get("shape")
    if (
        not isinstance(shape, list)
        or len(shape) != 2
        or any(type(value) is not int or value < 1 for value in shape)
    ):
        raise SystemExit(f"Invalid shape in capstone contract: {path}")
    peak = measurements.get("peak_tflops_reference")
    if peak is not None:
        positive_finite(peak, "peak TFLOPS contract")
    return payload


def positive_finite(value: Any, label: str) -> float:
    if type(value) not in (int, float):
        raise SystemExit(f"{label} must be a finite positive numeric value.")
    try:
        number = float(value)
    except OverflowError as exc:
        raise SystemExit(f"{label} must be finite.") from exc
    if not math.isfinite(number) or number <= 0:
        raise SystemExit(f"{label} must be finite and positive.")
    return number


def write_exclusive(path: Path, payload: dict[str, Any]) -> None:
    document = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SystemExit(f"Refusing to overwrite {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(document)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs=3, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    trials = [load_trial(path) for path in args.input]
    run_ids = [trial["run_id"] for trial in trials]
    seeds = [trial["seed"] for trial in trials]
    orders = [trial["measurements"]["variant_order"] for trial in trials]
    if len(set(run_ids)) != 3 or len(set(seeds)) != 3:
        raise SystemExit("Capstone inputs must have three distinct run IDs and seeds.")
    if set(orders) != {"baseline-first", "candidate-first"}:
        raise SystemExit("Capstone inputs do not demonstrate counterbalanced order.")
    contracts = [
        {
            "profile": trial.get("profile"),
            "environment": trial.get("environment"),
            "shape": trial["measurements"].get("shape"),
            "warmup": trial["measurements"].get("warmup"),
            "iterations": trial["measurements"].get("iterations"),
            "peak_tflops_reference": trial["measurements"].get("peak_tflops_reference"),
        }
        for trial in trials
    ]
    if any(contract != contracts[0] for contract in contracts[1:]):
        raise SystemExit("Capstone profile, environment, shape, or options differ.")
    baseline = [
        positive_finite(
            trial["measurements"]["timing"]["baseline"]["median_ms"], "Trial median"
        )
        for trial in trials
    ]
    candidate = [
        positive_finite(
            trial["measurements"]["timing"]["candidate"]["median_ms"], "Trial median"
        )
        for trial in trials
    ]
    baseline_median = statistics.median(baseline)
    candidate_median = statistics.median(candidate)
    ratio = positive_finite(baseline_median / candidate_median, "Timing ratio")
    decision = (
        "candidate-for-scoped-keep"
        if candidate_median < baseline_median
        else "reject-under-tested-contract"
    )
    write_exclusive(
        args.output,
        {
            "schema": "gpu-course-capstone/v1",
            "course": "llm-training",
            "input_run_ids": run_ids,
            "seeds": seeds,
            "variant_orders": orders,
            "verified_shared_contract": contracts[0],
            "baseline_trial_medians_ms": baseline,
            "candidate_trial_medians_ms": candidate,
            "baseline_median_of_medians_ms": round(baseline_median, 4),
            "candidate_median_of_medians_ms": round(candidate_median, 4),
            "baseline_to_candidate_ratio": round(ratio, 4),
            "decision": decision,
            "claim_scope": (
                "exact single-H100 shape, update, software, and measurement "
                "contract in the three input records"
            ),
            "causal_report_required": True,
            "correctness": {
                "three_distinct_runs": True,
                "three_distinct_seeds": True,
                "counterbalanced_order": True,
                "shared_profile_environment_shape_and_options": True,
                "all_updates_equivalent": True,
            },
        },
    )
    print(f"Wrote scoped training capstone decision: {args.output}")


if __name__ == "__main__":
    main()
