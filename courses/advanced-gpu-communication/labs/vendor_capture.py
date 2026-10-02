"""Profile worker-side vendor executables without contaminating their raw output."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def native_systems_prefix(prefix: list[str], report: Path, *, server=False, ucx=False):
    """Validate visible native argv before a coordinator launches its GPU workers."""
    if prefix[:5] != ["env", "-u", "DEBUGINFOD_URLS", "nsys", "profile"]:
        raise ValueError(
            "Worker prefix must start with env -u DEBUGINFOD_URLS nsys profile"
        )
    expected = {
        "--trace": "cuda,nvtx,osrt,nccl"
        if server
        else "cuda,nvtx,osrt,ucx"
        if ucx
        else "cuda,nvtx,osrt",
        "--cuda-trace-scope": "process-tree",
        "--sample": "none",
        "--discard-environment": "true",
        "--force-overwrite": "false",
        "--kill": "none",
        "--output": "{report}",
    }
    if server:
        expected.update(
            {
                "--trace-fork-before-exec": "true",
                "--cuda-graph-trace": "node",
                "--capture-range": "cudaProfilerApi",
                "--capture-range-end": "stop",
                "--flush-on-cudaprofilerstop": "false",
                "--wait": "primary",
            }
        )
    else:
        expected.update({"--cpuctxsw": "none", "--duration": "300", "--wait": "all"})
    options = {}
    for token in prefix[5:]:
        key, separator, value = token.partition("=")
        if not separator or key in options:
            raise ValueError("Use each native worker option once, as --name=value")
        options[key] = value
    if options != expected:
        raise ValueError(
            "Native worker options must preserve the documented capture scope and private report"
        )
    return [
        str(report)
        if token == "{report}"
        else token.replace("={report}", "=" + str(report))
        for token in prefix
    ]


def add_worker_prefix(parser):
    parser.add_argument(
        "--worker-prefix",
        nargs=argparse.REMAINDER,
        help="Visible native Systems argv; put this option last. {report} is a private per-rank path.",
    )


def validate_worker_prefix(args, *, server=False):
    prefix = args.worker_prefix
    if prefix is None:
        return
    if os.environ.get("COURSE_PROFILE_TOOL", "none") != "none":
        raise ValueError(
            "Clear COURSE_PROFILE_TOOL before supplying native worker argv"
        )
    if server and args.capture != "systems":
        raise ValueError("Native server prefix requires --capture systems")
    native_systems_prefix(prefix, Path("report"), server=server, ucx=not server)
    os.environ["COURSE_CAPTURE"] = "1"
    if not server:
        os.environ["COURSE_PROFILE_TOOL"] = "nsys"


def worker_command(
    command: list[str], output: Path, *, ucx: bool = False, prefix=None
) -> list[str]:
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
    if prefix is not None:
        return [
            *native_systems_prefix(prefix, output.resolve().with_suffix(""), ucx=ucx),
            *wrapped,
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
