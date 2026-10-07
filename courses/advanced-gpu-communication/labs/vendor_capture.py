"""Profile worker-side vendor executables without contaminating their raw output."""

from __future__ import annotations

import argparse
import os
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


def validate_worker_prefix(args, *, server=False, ucx=True):
    prefix = args.worker_prefix
    if prefix is None:
        return
    if os.environ.get("COURSE_PROFILE_TOOL", "none") != "none":
        raise ValueError(
            "Clear COURSE_PROFILE_TOOL before supplying native worker argv"
        )
    if server and args.capture != "systems":
        raise ValueError("Native server prefix requires --capture systems")
    native_systems_prefix(prefix, Path("report"), server=server, ucx=ucx and not server)
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
    # Native redirection isolates the vendor's machine-readable stdout from
    # profiler diagnostics. There is no intervening Python worker process.
    native = ["bash", "-c", 'set -euo pipefail; umask 077; set -o noclobber; output=$1; shift; exec "$@" >"$output"',
              "vendor-stdout", str(output.resolve()), *command]
    if prefix is not None:
        return [
            "timeout", "--signal=TERM", "--kill-after=15s", "900s",
            *native_systems_prefix(prefix, output.resolve().with_suffix(""), ucx=ucx),
            *native,
        ]
    if tool == "none":
        return native
    raise ValueError("Supply the native --worker-prefix from the lab's Systems job")
