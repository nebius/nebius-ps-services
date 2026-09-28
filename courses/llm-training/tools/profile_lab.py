#!/usr/bin/env python3
"""Run a bounded diagnostic capture inside a Slurm worker/container."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def capture_command(
    tool: str,
    command: list[str],
    destination: Path,
    region: str,
    kernel: str,
    *,
    server: bool = False,
    distributed: bool = False,
    local_companion: bool = False,
) -> list[str]:
    if tool == "none":
        return command
    if tool == "nsys":
        options = (
            [
                "--trace-fork-before-exec=true",
                "--cuda-graph-trace=node",
                "--capture-range=cudaProfilerApi",
                "--capture-range-end=stop",
            ]
            if server
            else []
        )
        return [
            "nsys",
            "profile",
            "--trace=cuda,nvtx,osrt,nccl" if distributed else "--trace=cuda,nvtx,osrt",
            "--cuda-trace-scope=process-tree",
            "--sample=none",
            "--cpuctxsw=none",
            "--discard-environment=true",
            "--force-overwrite=false",
            "--duration=300",
            "--kill=none",
            "--wait=all",
            "--output",
            str(destination),
            *options,
            *command,
        ]
    companion_options = (
        [
            "--config-file",
            "off",
            "--nvtx-push-pop-scope",
            "process",
            "--replay-mode",
            "kernel",
            "--kill",
            "no",
        ]
        if local_companion
        else []
    )
    return [
        "ncu",
        *companion_options,
        "--target-processes",
        "all",
        "--nvtx",
        "--nvtx-include",
        region + "/",
        "--kernel-name-base",
        "demangled",
        "--rename-kernels",
        "off",
        "--kernel-name",
        "regex:" + kernel,
        "--launch-count",
        "1",
        "--set",
        "basic",
        "--section",
        "SpeedOfLight",
        "--section",
        "MemoryWorkloadAnalysis",
        "--section",
        "Occupancy",
        "--clock-control",
        "none",
        "--export",
        str(destination),
        *command,
    ]


def admit_compute_companion(
    recipe: dict, command: list[str], region: str | None, *, server: bool
) -> str:
    """Admit only a declared local Python diagnostic, never a distributed command."""
    companion = recipe["compute_companion"]
    if (
        not isinstance(companion, dict)
        or set(companion) != {"path", "nvtx_range"}
        or recipe["kind"] != "distributed"
        or not isinstance(companion.get("path"), str)
        or not re.fullmatch(r"labs/[a-z][a-z0-9_]*\.py", companion["path"])
        or not isinstance(companion.get("nvtx_range"), str)
        or not re.fullmatch(r"[A-Za-z0-9_:.-]{1,80}", companion["nvtx_range"])
    ):
        raise ValueError("invalid local Compute companion metadata")
    script = ROOT / companion["path"]
    executable = shutil.which(command[0]) if command else None
    if (
        server
        or len(command) < 2
        or executable is None
        or Path(executable).resolve() != Path(sys.executable).resolve()
        or not script.is_file()
        or script.is_symlink()
        or script.resolve().parent != (ROOT / "labs").resolve()
        or Path(command[1]).resolve() != script.resolve()
    ):
        raise ValueError("Compute requires the exact Python/local companion command")
    expected_region = companion["nvtx_range"]
    if region is not None and region != expected_region:
        raise ValueError("Compute companion NVTX range cannot be overridden")
    for name, expected in (
        ("WORLD_SIZE", 1),
        ("LOCAL_WORLD_SIZE", 1),
        ("SLURM_NTASKS", 1),
        ("SLURM_JOB_NUM_NODES", 1),
        ("SLURM_NNODES", 1),
        ("RANK", 0),
        ("LOCAL_RANK", 0),
        ("SLURM_PROCID", 0),
        ("SLURM_LOCALID", 0),
    ):
        value = os.environ.get(name, str(expected))
        if not value.isdecimal() or int(value) != expected:
            raise ValueError(
                f"Compute companion requires one local process: invalid {name}"
            )
    return expected_region


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", required=True, help="Lab source path or lab identity.")
    parser.add_argument(
        "--tool",
        choices=("none", "nsys", "ncu"),
        default=os.environ.get("COURSE_PROFILE_TOOL", "none"),
    )
    parser.add_argument(
        "--range", dest="region", default=os.environ.get("COURSE_PROFILE_RANGE")
    )
    parser.add_argument(
        "--kernel", default=os.environ.get("COURSE_PROFILE_KERNEL", ".*")
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="This command is the GPU server, not a client.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide the executable after --")
    lab = Path(args.lab).stem
    recipes = json.loads((ROOT / "reference/observability.json").read_text())
    if lab not in recipes["labs"]:
        parser.error("lab is not in this course's profiling inventory")
    recipe = recipes["labs"][lab]
    if args.tool == "none":
        os.execvpe(command[0], command, os.environ.copy())
    if not os.environ.get("SLURM_JOB_ID"):
        parser.error("GPU capture must run inside a Slurm allocation")
    if not recipe["systems"]["applicable"]:
        parser.error(
            "this recipe uses model/readiness evidence; GPU capture is not applicable"
        )
    if recipe["systems"]["target"] == "optional-cuda":
        selected_cuda = "--device=cuda" in command or any(
            command[index : index + 2] == ["--device", "cuda"]
            for index in range(len(command) - 1)
        )
        if not selected_cuda:
            parser.error(
                "This introductory recipe supports GPU capture only with --device cuda"
            )
    if recipe["kind"] == "server-client" and not args.server:
        parser.error(
            "profile the GPU server; wrapping the HTTP client cannot capture server kernels"
        )
    if args.tool == "ncu" and not recipe.get("compute_command"):
        parser.error(
            "This lab has no supported Compute replay recipe; use its Systems capture"
        )
    local_companion = args.tool == "ncu" and "compute_companion" in recipe
    if local_companion:
        try:
            args.region = admit_compute_companion(
                recipe, command, args.region, server=args.server
            )
        except ValueError as exc:
            parser.error(str(exc))
    elif args.tool == "ncu" and recipe["kind"] in ("distributed", "server-client"):
        parser.error(
            "use Systems for this multi-process recipe; isolate a local kernel before using Compute"
        )
    args.region = args.region or recipe["nvtx_range"]
    if not shutil.which(args.tool):
        parser.error(
            f"{args.tool} is missing inside the execution environment; complete the shared README setup"
        )
    if not re.fullmatch(r"[A-Za-z0-9_:.-]{1,80}", args.region):
        parser.error("invalid NVTX range name")
    os.umask(0o077)
    rank = os.environ.get("RANK", os.environ.get("SLURM_PROCID", "0"))
    if not rank.isdecimal():
        parser.error("invalid rank identity")
    destination = (
        ROOT
        / "results"
        / lab
        / "profiles"
        / f"job-{os.environ['SLURM_JOB_ID']}-{secrets.token_hex(6)}-rank-{rank}"
    )
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["COURSE_CAPTURE"] = "1"
    if args.tool == "nsys":
        # Remote debug-symbol downloads can outlive an otherwise complete GPU
        # capture. Keep local symbol resolution without this network dependency.
        environment.pop("DEBUGINFOD_URLS", None)
    if args.server:
        if command[:2] != ["vllm", "serve"]:
            parser.error("This server capture contract requires vllm serve")
        command += ["--profiler-config.profiler", "cuda"]
        environment["VLLM_WORKER_MULTIPROC_METHOD"] = "spawn"
    started = time.time()
    process = subprocess.Popen(
        capture_command(
            args.tool,
            command,
            destination,
            args.region,
            args.kernel,
            server=args.server,
            distributed=recipe["systems"]["target"] == "rank",
            local_companion=local_companion,
        ),
        env=environment,
        start_new_session=True,
    )

    def stop(signum, _frame):
        if process.poll() is None:
            os.killpg(process.pid, signum)

    previous = {
        sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        status = process.wait(timeout=900)
    except subprocess.TimeoutExpired:
        stop(signal.SIGTERM, None)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        status = 124
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if status == 0:
        extension = ".ncu-rep" if args.tool == "ncu" else ".nsys-rep"
        report = destination.with_suffix(extension)
        if not report.is_file() or report.stat().st_size == 0:
            print(
                f"{args.tool} produced no nonempty {extension} report; capture failed. "
                "Check the profiler diagnostics, selected process, NVTX range and kernel filter.",
                file=sys.stderr,
            )
            status = 2
    receipt = {
        "lab": lab,
        "tool": args.tool,
        "rank": int(rank),
        "started_unix_seconds": started,
        "ended_unix_seconds": time.time(),
        "exit_code": status,
        "acceptance_timing": False,
        "nvtx_range": args.region,
        "kernel_filter": args.kernel,
    }
    with destination.with_suffix(".json").open("x") as stream:
        json.dump(receipt, stream, indent=2)
    print(
        f"Diagnostic report prefix: {destination}. Re-run without COURSE_PROFILE_TOOL for acceptance.",
        file=sys.stderr,
    )
    raise SystemExit(status)


if __name__ == "__main__":
    main()
