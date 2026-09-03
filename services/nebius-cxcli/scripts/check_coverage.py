#!/usr/bin/env python3
"""Enforce global and safety-critical combined line/branch coverage ratchets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ratchet_baseline import merge_base_payload

SCHEMA = "nebius-cxcli.coverage-ratchets.v1"
CRITICAL_FILES = (
    "src/nebius_cxcli/credential_compensation.py",
    "src/nebius_cxcli/project_bundle_transaction.py",
    "src/nebius_cxcli/soperator_infrastructure_identity.py",
    "src/nebius_cxcli/soperator_release_ownership.py",
    "src/nebius_cxcli/soperator_upgrade_supervisor.py",
)


def _percent(summary: Any, *, label: str) -> float:
    if not isinstance(summary, dict):
        raise RuntimeError(f"coverage report is missing {label} summary")
    value = summary.get("percent_covered")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RuntimeError(f"coverage report has invalid {label} percentage")
    return float(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--base-ref")
    return parser


def _validated_floors(
    baseline: dict[str, Any], *, label: str
) -> tuple[float, dict[str, float], float]:
    if baseline.get("schema") != SCHEMA:
        raise RuntimeError(f"{label} coverage baseline must use schema {SCHEMA}")
    critical = baseline.get("critical_combined_percent")
    if not isinstance(critical, dict) or set(critical) != set(CRITICAL_FILES):
        raise RuntimeError(f"{label} coverage baseline must define every critical module")
    global_floor = baseline.get("global_combined_percent")
    target_floor = baseline.get("target_critical_combined_percent")
    values = {
        "global_combined_percent": global_floor,
        "target_critical_combined_percent": target_floor,
    }
    values.update({str(path): value for path, value in critical.items()})
    for name, value in values.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not 0 <= float(value) <= 100
        ):
            raise RuntimeError(f"{label} coverage baseline has invalid floor for {name}")
    return (
        float(global_floor),
        {str(path): float(value) for path, value in critical.items()},
        float(target_floor),
    )


def _check_baseline_direction(current: dict[str, Any], previous: dict[str, Any]) -> None:
    current_global, current_critical, current_target = _validated_floors(current, label="current")
    previous_global, previous_critical, previous_target = _validated_floors(
        previous, label="merge-base"
    )
    regressions: list[str] = []
    if current_global < previous_global:
        regressions.append(f"global {previous_global:.2f} -> {current_global:.2f}")
    if current_target < previous_target:
        regressions.append(f"critical target {previous_target:.2f} -> {current_target:.2f}")
    for path in CRITICAL_FILES:
        if current_critical[path] < previous_critical[path]:
            regressions.append(
                f"{path} {previous_critical[path]:.2f} -> {current_critical[path]:.2f}"
            )
    if regressions:
        raise RuntimeError(
            "coverage baseline regression: floors may only increase: " + "; ".join(regressions)
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = args.project_root.resolve()
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("coverage report must be a JSON object")
    baseline_path = args.baseline.resolve()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(baseline, dict):
        raise RuntimeError("coverage baseline must be a JSON object")
    global_floor, critical_floors, _target_floor = _validated_floors(baseline, label="current")
    previous = merge_base_payload(project_root, baseline_path, args.base_ref)
    if previous is not None:
        _check_baseline_direction(baseline, previous)
    failures: list[str] = []
    global_percent = _percent(payload.get("totals"), label="global")
    if global_percent < global_floor:
        failures.append(f"global coverage {global_percent:.2f}% is below {global_floor:.2f}%")
    files = payload.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("coverage report is missing file summaries")
    for path in CRITICAL_FILES:
        floor = critical_floors[path]
        record = files.get(path)
        if not isinstance(record, dict):
            failures.append(f"critical module is absent from coverage report: {path}")
            continue
        percent = _percent(record.get("summary"), label=path)
        if percent < float(floor):
            failures.append(f"critical coverage {path}={percent:.2f}% is below {float(floor):.2f}%")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(
        f"Coverage floors passed: global {global_percent:.2f}% and "
        f"{len(CRITICAL_FILES)} critical module ratchets."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
