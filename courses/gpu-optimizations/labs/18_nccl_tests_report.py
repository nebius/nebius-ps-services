"""Run or inspect a bounded MPI all_reduce_perf sweep without exporting secrets."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

from common import (
    add_common_args,
    open_private_exclusive,
    validate_common_args,
    write_result,
)
from networking import PROFILES, message_sizes, profile_settings

SOURCE_COMMIT = "b4d5beebca8a76cf01335f724d154b9b9d394d96"
MAX_LOG_BYTES = 16 * 2**20


def acceptance_timing(mode: str, diagnostic: bool, document: str) -> bool:
    # Offline logs do not prove the launch or whether hidden instrumentation ran.
    verbose = re.search(r"\bNCCL (?:INFO|TRACE)\b", document) is not None
    return mode == "run" and not diagnostic and not verbose


def parse_output(document: str, sizes: list[int], ranks: int) -> dict:
    """Accept only the pinned float/sum, uninstrumented two-mode table format."""
    lines = [" ".join(line.split()) for line in document.splitlines() if line.strip()]
    if re.search(r"\b(?:FAILED|ERROR)\b|NCCL WARN", document):
        raise ValueError(
            "Log contains a failed check or runtime warning/error; investigate first"
        )
    headers = [line for line in lines if line.startswith("# nccl-tests version ")]
    match = re.fullmatch(
        r"# nccl-tests version v?(2\.20\.0) \([^()]+\) nccl-headers=(\d+) nccl-library=(\d+)",
        headers[0] if len(headers) == 1 else "",
    )
    if not match:
        raise ValueError("Expected one nccl-tests 2.20.0 version record")
    if min(int(match[2]), int(match[3])) < 20000:
        raise ValueError("Invalid NCCL version record")
    configs = [line for line in lines if line.startswith("# nThread ")]
    config = re.fullmatch(
        r"# nThread 1 nGpus 1 minBytes (\d+) maxBytes (\d+) step: 2\(factor\) "
        r"warmup iters: (\d+) iters: (\d+) agg iters: 1 validation: (\d+) graph: 0 unalign: 0",
        configs[0] if len(configs) == 1 else "",
    )
    if not config or (int(config[1]), int(config[2])) != (sizes[0], sizes[-1]):
        raise ValueError("Missing or incompatible sweep configuration")
    if (
        not 1 <= int(config[3]) <= 100
        or not 3 <= int(config[4]) <= 1000
        or int(config[5]) < 1
    ):
        raise ValueError("Warm-up, iterations and enabled validation must be recorded")
    rank_rows = [line for line in lines if line.startswith("# Rank ")]
    seen_ranks, hosts = set(), set()
    for line in rank_rows:
        entry = re.fullmatch(
            r"# Rank (\d+) Group 0 Pid \d+ on (\S+) device 0 \[[^]]+\] (.+)", line
        )
        if not entry or "H100" not in entry[3] or "MIG" in entry[3].upper():
            raise ValueError("Expected one full H100, visible device zero, per node")
        seen_ranks.add(int(entry[1]))
        hosts.add(entry[2])
    if (
        len(rank_rows) != ranks
        or seen_ranks != set(range(ranks))
        or len(hosts) != ranks
    ):
        raise ValueError("Rank inventory must show one distinct node per rank")
    table = (
        "# size count type redop root time algbw busbw #wrong time algbw busbw #wrong"
    )
    units = "# (B) (elements) (us) (GB/s) (GB/s) (us) (GB/s) (GB/s)"
    if lines.count(table) != 1 or lines.count(units) != 1:
        raise ValueError(
            "Unsupported columns or time units; use the uninstrumented table"
        )
    starts = [
        i
        for i, line in enumerate(lines)
        if re.fullmatch(r"# Collective test starting: .*all_reduce_perf\w*", line)
    ]
    ends = [
        i
        for i, line in enumerate(lines)
        if re.fullmatch(r"# Collective test concluded: .*all_reduce_perf\w*", line)
    ]
    good = "# Out of bounds values : 0 OK"
    if len(starts) != 1 or len(ends) != 1 or lines.count(good) != 1:
        raise ValueError(
            "Incomplete or duplicate test start/completion/correctness footer"
        )
    if (
        not starts[0]
        < lines.index(table)
        < lines.index(units)
        < lines.index(good)
        < ends[0]
    ):
        raise ValueError("Invalid report ordering")
    averages = [line for line in lines if line.startswith("# Avg bus bandwidth : ")]
    if len(averages) != 1 or not re.fullmatch(
        r"# Avg bus bandwidth : [0-9.eE+\-]+(?: OK)?", averages[0]
    ):
        raise ValueError("Missing average bandwidth footer")
    rows = []
    for position, line in enumerate(lines):
        if not line[0].isdigit():
            continue
        fields = line.split()
        if len(fields) != 13 or not lines.index(units) < position < lines.index(good):
            raise ValueError("Malformed, unexpected or misplaced numeric row")
        try:
            size, count = int(fields[0]), int(fields[1])
            if fields[2:5] != ["float", "sum", "-1"] or count * 4 != size:
                raise ValueError("Expected aligned float/sum all-reduce")
            row = {"bytes": size}
            for name, offset in (("out_of_place", 5), ("in_place", 9)):
                latency, algbw, busbw = map(float, fields[offset : offset + 3])
                wrong = int(fields[offset + 3])
                if not all(math.isfinite(v) for v in (latency, algbw, busbw)):
                    raise ValueError("Non-finite metric")
                if latency <= 0 or min(algbw, busbw) < 0 or wrong != 0:
                    raise ValueError("Invalid metric or nonzero correctness count")
                row[name] = {
                    "time_us": latency,
                    "algbw_GBps": algbw,
                    "normalized_busbw_GBps": busbw,
                    "wrong": wrong,
                }
            rows.append(row)
        except (ValueError, OverflowError) as exc:
            raise ValueError(
                "Invalid result row; inspect the private source log"
            ) from exc
    if [row["bytes"] for row in rows] != sizes:
        raise ValueError("Missing, duplicate or unexpected message sizes")
    # Do not retain hostnames, PIDs, PCI addresses, arbitrary environment or raw lines.
    return {
        "benchmark_version": match[1],
        "nccl_headers": int(match[2]),
        "nccl_library": int(match[3]),
        "ranks": ranks,
        "warmup": int(config[3]),
        "iterations": int(config[4]),
        "validation_iterations": int(config[5]),
        "rows": rows,
    }


def launch_command(
    binary: Path, mpi: str, sizes: list[int], warmup: int, iterations: int
) -> list[str]:
    if not re.fullmatch(r"(?:pmix(?:_v[0-9]+)?|pmi2)", mpi):
        raise ValueError("Select a site-qualified pmix, pmix_vN or pmi2 mode")
    return [
        "srun",
        f"--mpi={mpi}",
        "--nodes=2",
        "--ntasks=2",
        "--ntasks-per-node=1",
        "--gpus-per-task=1",
        "--kill-on-bad-exit=1",
        "--export=ALL",
        str(binary),
        "-b",
        str(sizes[0]),
        "-e",
        str(sizes[-1]),
        "-f",
        "2",
        "-t",
        "1",
        "-g",
        "1",
        "-d",
        "float",
        "-o",
        "sum",
        "-w",
        str(warmup),
        "-n",
        str(iterations),
        "-c",
        "1",
        "-a",
        "3",
        "-I",
        "0",
        "-U",
        "0",
        "-C",
        "0",
        "-S",
        "0",
        "-T",
        "120",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--mode", choices=("run", "parse"), default="parse")
    parser.add_argument("--variant", choices=tuple(PROFILES), default="default")
    parser.add_argument(
        "--binary", type=Path, help="MPI-enabled all_reduce_perf, shared on both nodes"
    )
    parser.add_argument("--mpi", help="MPI integration confirmed by the cluster owner")
    parser.add_argument(
        "--input", type=Path, help="private stdout log for offline parsing"
    )
    parser.add_argument(
        "--exit-code", type=int, help="observed srun exit status for offline parsing"
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="INFO diagnostics, never acceptance timing",
    )
    parser.add_argument("--min-bytes", type=int, default=8)
    parser.add_argument("--max-bytes", type=int, default=64 * 2**20)
    args = parser.parse_args()
    validate_common_args(args)
    try:
        sizes = message_sizes(args.min_bytes, args.max_bytes)
        if not 1 <= args.warmup <= 100 or not 3 <= args.iterations <= 1000:
            raise ValueError("Use 1–100 warmups and 3–1000 iterations")
        metadata = {"variant": args.variant}
        if args.mode == "run":
            if args.input is not None or args.exit_code is not None:
                raise ValueError("--input/--exit-code belong to parse mode")
            if (
                not os.environ.get("SLURM_JOB_ID")
                or os.environ.get("SLURM_JOB_NUM_NODES") != "2"
                or os.environ.get("SLURM_NTASKS") != "2"
            ):
                raise ValueError("Use the dedicated two-node Slurm launcher")
            if args.binary is None or args.mpi is None or shutil.which("srun") is None:
                raise ValueError("Provide --binary and --mpi with srun available")
            binary = args.binary.resolve(strict=True)
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise ValueError("NCCL Tests binary must be executable")
            env = os.environ.copy()
            settings = profile_settings(args.variant, env)
            env.update(settings)
            if "NCCL_TESTS_DEVICE" in env or "NCCL_TESTS_MIN_BW" in env:
                raise ValueError(
                    "Remove inherited benchmark device/threshold overrides from the job shell"
                )
            if not args.diagnostic and env.get("NCCL_DEBUG", "WARN") not in (
                "WARN",
                "VERSION",
            ):
                raise ValueError(
                    "Use --diagnostic for inherited verbose logging, then a clean timing job"
                )
            if args.diagnostic:
                env.update(NCCL_DEBUG="INFO", NCCL_DEBUG_SUBSYS="INIT,NET,GRAPH")
                if "NCCL_DEBUG_FILE" in env:
                    raise ValueError(
                        "Inherited debug-file redirection would split the diagnostic evidence"
                    )
            raw = args.output_dir / f"18_nccl_tests_report-run-{args.run_id}.log"
            command = launch_command(
                binary, args.mpi, sizes, args.warmup, args.iterations
            )
            with open_private_exclusive(raw) as output:
                completed = subprocess.run(
                    command,
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=900,
                    check=False,
                )
            if completed.returncode != 0:
                raise ValueError(
                    "srun failed; private log retained, no successful report published"
                )
            with binary.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            metadata.update(
                job_local_overrides=settings,
                launcher_exit_code=0,
                local_binary_sha256=digest,
                reviewed_source_commit=SOURCE_COMMIT,
                remote_binary_parity="requires independent per-node qualification",
            )
        else:
            if args.binary is not None or args.mpi is not None:
                raise ValueError("--binary/--mpi belong to run mode")
            if args.input is None or args.exit_code != 0:
                raise ValueError(
                    "Provide --input and an independently observed --exit-code 0"
                )
            raw = args.input
            metadata.update(
                launcher_exit_code=0,
                exit_status_evidence="learner supplied, not observed by parser",
            )
        if raw.stat().st_size > MAX_LOG_BYTES:
            raise ValueError("Log exceeds the 16 MiB parser limit")
        document = raw.read_text(encoding="utf-8")
        report = parse_output(document, sizes, 2)
        metadata["acceptance_timing"] = acceptance_timing(
            args.mode, args.diagnostic, document
        )
        target = write_result(
            args,
            lab_id="18_nccl_tests_report",
            environment={"target": "two one-H100 nodes", "evidence_lane": args.mode},
            measurements={**metadata, **report},
            correctness={"checked_elements_match": True},
        )
        print(f"Validated NCCL Tests report: {target}")
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"ERROR: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
