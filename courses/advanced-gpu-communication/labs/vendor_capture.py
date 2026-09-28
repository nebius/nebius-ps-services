"""Profile worker-side vendor executables without contaminating their raw output."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def worker_command(command: list[str], output: Path, *, ucx: bool = False) -> list[str]:
    """Call inside srun; keep the vendor stdout separate from profiler diagnostics."""
    tool = os.environ.get("COURSE_PROFILE_TOOL", "none")
    if tool not in ("none", "nsys"):
        raise ValueError("Vendor communication captures support Nsight Systems only")
    wrapped = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--stdout",
        str(output.resolve()),
        "--",
        *command,
    ]
    if tool == "none":
        return wrapped
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Vendor capture requires a Slurm allocation")
    return [
        "env",
        "-u",
        "DEBUGINFOD_URLS",
        "nsys",
        "profile",
        "--trace=cuda,nvtx,osrt,ucx" if ucx else "--trace=cuda,nvtx,osrt",
        "--cuda-trace-scope=process-tree",
        "--sample=none",
        "--cpuctxsw=none",
        "--discard-environment=true",
        "--force-overwrite=false",
        "--duration=300",
        "--kill=none",
        "--wait=all",
        "--output",
        str(output.resolve().with_suffix("")),
        *wrapped,
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdout", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a vendor executable after --")
    os.umask(0o077)
    # Exclusive creation protects authoritative output from retries and collisions.
    with args.stdout.open("x") as output:
        os.dup2(output.fileno(), sys.stdout.fileno())
    os.execvpe(command[0], command, os.environ.copy())


if __name__ == "__main__":
    main()
