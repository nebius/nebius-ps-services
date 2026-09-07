"""Collect a bounded read-only H100 sharing and health snapshot."""

from __future__ import annotations

import argparse
import csv
import io
import shutil
import subprocess

from common import (
    add_common_args,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)

FIELDS = (
    "name",
    "driver_version",
    "temperature.gpu",
    "power.draw",
    "clocks.sm",
    "pstate",
    "clocks_throttle_reasons.active",
    "ecc.errors.uncorrected.volatile.total",
    "retired_pages.pending",
    "mig.mode.current",
    "compute_mode",
)


def query_nvidia_smi() -> dict[str, str]:
    if shutil.which("nvidia-smi") is None:
        raise SystemExit("nvidia-smi is required for the read-only health snapshot")
    result = subprocess.run(
        [
            "nvidia-smi",
            f"--query-gpu={','.join(FIELDS)}",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise SystemExit(f"read-only nvidia-smi query failed: {result.stderr.strip()}")
    rows = list(csv.reader(io.StringIO(result.stdout)))
    if len(rows) != 1 or len(rows[0]) != len(FIELDS):
        raise SystemExit("expected exactly one allocated GPU from nvidia-smi")
    return dict(zip(FIELDS, (value.strip() for value in rows[0]), strict=True))


def describe_mig_mode(value: str) -> str:
    """Report only recognized management state; absence is not device proof."""
    return {
        "enabled": "MIG enabled",
        "disabled": "MIG disabled; verify allocated-device identity in preflight",
    }.get(value.strip().lower(), "MIG mode unavailable or unrecognized")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    snapshot = query_nvidia_smi()
    sharing = {
        "full_or_mig": describe_mig_mode(snapshot["mig.mode.current"]),
        "mps": "not inferable from a non-intervening per-process query",
        "time_slicing": "scheduler policy evidence required",
    }
    target = write_result(
        args,
        lab_id="12_read_only_health",
        environment=environment,
        measurements={
            "snapshot": snapshot,
            "sharing_evidence": sharing,
            "xid_history": "requires a separately authorized, sanitized system-event source",
            "dcgm_health": "run the documented read-only cluster procedure when DCGM access is available",
            "configuration_changed": False,
        },
        correctness={
            "single_h100": snapshot["name"].startswith("NVIDIA H100"),
            "read_only": True,
        },
    )
    print(f"Wrote read-only health evidence: {target}")


if __name__ == "__main__":
    main()
