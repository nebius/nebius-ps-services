"""Run or inspect a bounded MPI all_reduce_perf sweep without exporting secrets."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from common import (
    add_common_args,
    open_private_exclusive,
    validate_common_args,
    write_result,
)
from course_evidence import annotated_operation, gpu_family
from networking import PROFILES, message_sizes, profile_settings

SOURCE_COMMIT = "b4d5beebca8a76cf01335f724d154b9b9d394d96"
MAX_LOG_BYTES = 16 * 2**20


def acceptance_timing(mode: str, diagnostic: bool, document: str) -> bool:
    # Offline logs do not prove the launch or whether hidden instrumentation ran.
    verbose = re.search(r"\bNCCL (?:INFO|TRACE)\b", document) is not None
    return (
        mode == "run"
        and not diagnostic
        and not verbose
        and os.environ.get("COURSE_PROFILE_TOOL", "none") == "none"
    )


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
    seen_ranks, hosts, placements = set(), set(), set()
    families = set()
    per_node = 8 if ranks in (8, 16) else 1
    for line in rank_rows:
        entry = re.fullmatch(
            r"# Rank (\d+) Group 0 Pid \d+ on (\S+) device 0 \[([^]]+)\] (.+)", line
        )
        if not entry:
            raise ValueError("Expected one full GPU, visible device zero, per rank")
        families.add(gpu_family(entry[4]))
        seen_ranks.add(int(entry[1]))
        hosts.add(entry[2])
        placements.add((entry[2], entry[3]))
    if (
        len(rank_rows) != ranks
        or seen_ranks != set(range(ranks))
        or len(hosts) != ranks // per_node
        or len(placements) != ranks
        or any(sum(host == h for host, _ in placements) != per_node for h in hosts)
    ):
        raise ValueError(
            "Rank inventory must show distinct GPUs and the requested ranks per node"
        )
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
        "gpu_family": one_family(families),
        "benchmark_version": match[1],
        "nccl_headers": int(match[2]),
        "nccl_library": int(match[3]),
        "ranks": ranks,
        "warmup": int(config[3]),
        "iterations": int(config[4]),
        "validation_iterations": int(config[5]),
        "rows": rows,
    }


def one_family(families):
    if len(families) != 1:
        raise ValueError("Require one observed GPU family across all ranks")
    return next(iter(families))


def launch_command(
    binary: Path,
    mpi: str,
    sizes: list[int],
    warmup: int,
    iterations: int,
    nodes: int = 2,
) -> list[str]:
    if not re.fullmatch(r"(?:pmix(?:_v[0-9]+)?|pmi2)", mpi):
        raise ValueError("Select a site-qualified pmix, pmix_vN or pmi2 mode")
    native = [str(binary)]
    if os.environ.get("COURSE_PROFILE_TOOL", "none") != "none":
        native = [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "tools/profile_lab.py"),
            "--lab",
            "10_nccl_tests_report",
            "--",
            *native,
        ]
    return [
        "srun",
        f"--mpi={mpi}",
        f"--nodes={nodes}",
        f"--ntasks={nodes * 8}",
        "--ntasks-per-node=8",
        "--gpus-per-task=1",
        "--gres-flags=allow-task-sharing",
        "--kill-on-bad-exit=1",
        "--export=ALL",
        *native,
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
    parser.add_argument("--nodes", type=int, choices=(1, 2), default=2)
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
            if not os.environ.get("SLURM_JOB_ID") or os.environ.get(
                "SLURM_JOB_NUM_NODES"
            ) != str(args.nodes):
                raise ValueError(
                    "Use the matching one-/two-node eight-GPU Slurm launcher"
                )
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
            # Slurm exposes one assigned GPU per task. The benchmark otherwise
            # uses the MPI local rank, which is outside that one-device view.
            env["NCCL_TESTS_DEVICE"] = "0"
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
            raw = args.output_dir / f"10_nccl_tests_report-run-{args.run_id}.log"
            command = launch_command(
                binary, args.mpi, sizes, args.warmup, args.iterations, args.nodes
            )
            with open_private_exclusive(raw) as output:
                rank_logs = []
                if env.get("COURSE_PROFILE_TOOL", "none") != "none":
                    # Concurrent profiler progress can split lines in srun's
                    # merged stream. Retain each rank/stream intact and combine
                    # only after every task has exited; never relax the parser.
                    folder = raw.with_suffix(".rank-logs").resolve()
                    folder.mkdir(mode=0o700, exist_ok=False)
                    command[1:1] = [
                        f"--output={folder}/rank-%t.out",
                        f"--error={folder}/rank-%t.err",
                    ]
                    rank_logs = [
                        folder / f"rank-{rank}.{stream}"
                        for rank in range(args.nodes * 8)
                        for stream in ("out", "err")
                    ]
                completed = subprocess.run(
                    command,
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=900,
                    check=False,
                )
                if completed.returncode == 0 and rank_logs:
                    if (
                        output.tell() + sum(p.stat().st_size + 1 for p in rank_logs)
                        > MAX_LOG_BYTES
                    ):
                        raise ValueError("Rank logs exceed the 16 MiB parser limit")
                    for path in rank_logs:
                        output.write("\n" + path.read_text(encoding="utf-8"))
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
        report = parse_output(document, sizes, args.nodes * 8)
        os.environ["WORLD_SIZE"] = str(args.nodes * 8)
        metadata["acceptance_timing"] = acceptance_timing(
            args.mode, args.diagnostic, document
        )
        target = write_result(
            args,
            lab_id="10_nccl_tests_report",
            environment={
                "gpu_family": report.pop("gpu_family"),
                "target": "eight-GPU NVLink and InfiniBand workers",
                "evidence_lane": args.mode,
                "rank_count": args.nodes * 8,
            },
            measurements={**metadata, **report},
            correctness={"checked_elements_match": True},
        )
        print(f"Validated NCCL Tests report: {target}")
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"ERROR: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    annotated_operation(main, "lab_workload")()
