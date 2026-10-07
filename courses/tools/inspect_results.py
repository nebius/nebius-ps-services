#!/usr/bin/env python3
"""Show completed lab measurements and exact artifact paths for a Slurm job."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from publish_results import extract, read_document, validate_result

ROOT = Path(__file__).resolve().parents[1]


def inspect(lab: str, job: int) -> list[str]:
    inventory = read_document(ROOT / "reference/observability.json")
    recipe = (
        inventory["setup"] if lab == "environment_readiness" else inventory["labs"][lab]
    )
    reports = []
    base = ROOT / "results" / lab / "jobs" / str(job)
    if any(p.is_symlink() for p in (base, *base.parents)):
        raise ValueError("Job output must not follow symlinks")
    for path in sorted(base.rglob("*.json")):
        if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
            continue
        try:
            document = read_document(path)
        except (ValueError, OSError):
            continue
        if (
            document.get("lab_id") != lab
            or document.get("experiment", {}).get("slurm_job_id") != job
        ):
            continue
        validate_result(document, lab, diagnostic=recipe["kind"] == "diagnostic")
        lines = [
            f"Completed artifact: {path}",
            f"Workload: {document['profile']}; job: {job}; correctness: passed",
        ]
        for metric in recipe["metrics"]:
            try:
                rows = extract(document["measurements"], metric["path"])
            except ValueError:
                if metric.get("optional"):
                    continue
                raise
            for case, value in rows:
                if value is None and metric.get("optional"):
                    continue
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("Non-numeric declared measurement")
                label = case or metric.get("case", "value")
                unit = metric["unit"].removeprefix("count:")
                if unit == "reqps":
                    unit = "requests/s"
                lines.append(
                    f"  {metric['path']} [{label}]: {value * metric.get('scale', 1):.6g} {unit}"
                )
        reports.append("\n".join(lines))
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", required=True)
    parser.add_argument(
        "--job",
        required=True,
        type=int,
        help="Submitted Slurm job number.",
    )
    args = parser.parse_args()
    if args.job < 1:
        parser.error("job must be positive")
    try:
        reports = inspect(args.lab, args.job)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"Result inspection failed: {exc}\n")
    if not reports:
        parser.exit(
            1,
            "No completed result for this lab/job. Check sacct and the job log; submission alone is not completion.\n",
        )
    print("\n\n".join(reports))


if __name__ == "__main__":
    main()
