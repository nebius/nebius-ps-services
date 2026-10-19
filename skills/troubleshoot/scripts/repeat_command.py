#!/usr/bin/env python3
"""Measure repeated command behavior with bounded, redacted evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evidence_redaction import is_sensitive_name, redact_text

SCHEMA_VERSION = 2
DEFAULT_MAX_TAIL_BYTES = 4096
MAX_TAIL_BYTES = 65536
MAX_RETAINED_BYTES = 32 * 1024 * 1024
SENSITIVE_FLAGS = {
    "--access-key",
    "--api-key",
    "--certificate",
    "--client-secret",
    "--connection-string",
    "--cookie",
    "--credential",
    "--endpoint",
    "--host",
    "--hostname",
    "--password",
    "--private-key",
    "--secret",
    "--session",
    "--token",
    "--uri",
    "--url",
}
TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+(?:Z)?\b|\b\d+(?:\.\d+)?(?:ms|s)\b"
)
HEX_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one argv command repeatedly and report pass/failure rates, "
            "timeouts, timing, and redacted output-signature clusters."
        )
    )
    parser.add_argument("--runs", type=int, required=True, help="Number of runs.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-run timeout in seconds (default: 120).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write JSON atomically to this file instead of stdout.",
    )
    parser.add_argument(
        "--max-tail-bytes",
        type=int,
        default=DEFAULT_MAX_TAIL_BYTES,
        help=f"Maximum bytes retained from each stream (max: {MAX_TAIL_BYTES}).",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command argv after --; no shell interpretation is used.",
    )
    return parser.parse_args()


def redact_argv(argv: list[str]) -> list[str]:
    safe: list[str] = []
    hide_next = False
    for argument in argv:
        if hide_next:
            safe.append("[REDACTED]")
            hide_next = False
            continue
        lowered = argument.lower()
        if "=" in argument:
            name, _value = argument.split("=", 1)
            if is_sensitive_name(name):
                safe.append(f"{name}=[REDACTED]")
                continue
        is_bare_key = re.fullmatch(r"(?:--)?[A-Za-z_][A-Za-z0-9_.-]*", argument)
        if is_bare_key and (lowered in SENSITIVE_FLAGS or is_sensitive_name(argument)):
            safe.append(argument)
            hide_next = True
            continue
        safe.append(redact_text(argument))
    return safe


def make_signature(
    exit_code: int | None, timed_out: bool, stdout: str, stderr: str
) -> str:
    normalized = "\n".join((stdout, stderr))
    normalized = TIMESTAMP_RE.sub("[VOLATILE]", normalized)
    normalized = HEX_ADDRESS_RE.sub("0x[ADDR]", normalized)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    status = "timeout" if timed_out else f"exit-{exit_code}"
    return f"{status}:{digest}"


def run_once(argv: list[str], timeout: float, max_tail_bytes: int) -> dict[str, Any]:
    from bounded_process import observe

    started = time.monotonic()
    result = observe(argv, timeout=timeout, stdout_limit=max_tail_bytes, tail=True)
    duration = round(time.monotonic() - started, 6)
    if result["status"] == "unavailable":
        return {
            "outcome": "launch_error",
            "exit_code": None,
            "timed_out": False,
            "duration_seconds": None,
            "launch_duration_seconds": duration,
            "launch_error": "command launch unavailable",
            "stdout_tail": "",
            "stderr_tail": "",
            "signature": "launch_error",
        }
    timed_out = result["status"] == "timeout"
    outcome = (
        "interrupted"
        if result["status"] == "interrupted"
        else "timeout"
        if timed_out
        else "pass"
        if result["returncode"] == 0
        else "nonzero"
    )
    stdout = redact_text(result["stdout"].decode("utf-8", errors="replace"))
    stderr = redact_text(result["stderr"].decode("utf-8", errors="replace"))
    return {
        "outcome": outcome,
        "exit_code": result["returncode"],
        "timed_out": timed_out,
        "duration_seconds": duration,
        "signature": make_signature(result["returncode"], timed_out, stdout, stderr),
        "stdout_tail": stdout,
        "stderr_tail": stderr,
        "stdout_bytes_seen": result["stdout_bytes_seen"],
        "stderr_bytes_seen": result["stderr_bytes_seen"],
        "stdout_truncated": result["stdout_bytes_seen"] > max_tail_bytes,
        "stderr_truncated": result["stderr_bytes_seen"] > max_tail_bytes,
        "output_complete": outcome not in {"interrupted", "timeout"},
    }


def summarize(
    argv: list[str], requested_runs: int, runs: list[dict[str, Any]], interrupted: bool
) -> dict[str, Any]:
    def timings(items: list[dict[str, Any]]) -> dict[str, float | None]:
        values = [float(run["duration_seconds"]) for run in items]
        return {
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "mean": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None,
        }

    completed = [
        run for run in runs if run["outcome"] in {"pass", "nonzero", "timeout"}
    ]
    launched = [run for run in runs if run["outcome"] != "launch_error"]
    passes = sum(run["outcome"] == "pass" for run in completed)
    signatures = Counter((run["outcome"], str(run["signature"])) for run in runs)
    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": datetime.now(UTC).isoformat(),
        "command": redact_argv(argv),
        "requested_runs": requested_runs,
        "attempted_runs": len(runs),
        "launched_runs": len(launched),
        "completed_runs": len(completed),
        "interrupted_runs": sum(run["outcome"] == "interrupted" for run in runs),
        "interrupted": interrupted,
        "pass_count": passes,
        "failure_count": len(completed) - passes,
        "timeout_count": sum(run["outcome"] == "timeout" for run in runs),
        "launch_error_count": len(runs) - len(launched),
        "pass_rate": round(passes / len(completed), 6) if completed else None,
        "failure_rate": round((len(completed) - passes) / len(completed), 6)
        if completed
        else None,
        "timing_seconds": timings(completed),
        "timing_by_outcome": {
            outcome: timings([r for r in launched if r["outcome"] == outcome])
            for outcome in ("pass", "nonzero", "timeout", "interrupted")
        },
        "signature_clusters": [
            {"outcome": outcome, "signature": signature, "count": count}
            for (outcome, signature), count in sorted(signatures.items())
        ],
        "sampling_assumptions": "Consecutive runs may share caches, load and state; no independence or defect-absence claim.",
        "runs": runs,
    }


def write_atomic(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError("refusing symlink output path")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("output parent is not a directory")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> int:
    args = parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("repeat_command.py: a command is required after --", file=sys.stderr)
        return 2
    if args.runs < 1 or args.runs > 10000:
        print("repeat_command.py: --runs must be between 1 and 10000", file=sys.stderr)
        return 2
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print(
            "repeat_command.py: --timeout must be finite and positive", file=sys.stderr
        )
        return 2
    if not 1 <= args.max_tail_bytes <= MAX_TAIL_BYTES:
        print(
            f"repeat_command.py: --max-tail-bytes must be between 1 and {MAX_TAIL_BYTES}",
            file=sys.stderr,
        )
        return 2
    retained_bytes = args.runs * args.max_tail_bytes * 2
    if retained_bytes > MAX_RETAINED_BYTES:
        print(
            "repeat_command.py: runs multiplied by retained stream tails "
            f"must not exceed {MAX_RETAINED_BYTES} bytes",
            file=sys.stderr,
        )
        return 2

    runs: list[dict[str, Any]] = []
    interrupted = False
    exit_status = 0
    try:
        for _ in range(args.runs):
            result = run_once(command, args.timeout, args.max_tail_bytes)
            runs.append(result)
            if result["outcome"] == "interrupted":
                interrupted = True
                exit_status = 130
                break
            if "launch_error" in result:
                exit_status = 2
                break
    except KeyboardInterrupt:
        interrupted = True
        exit_status = 130
    payload = summarize(command, args.runs, runs, interrupted)
    content = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    try:
        if args.out is None:
            sys.stdout.write(content)
        else:
            write_atomic(args.out, content)
    except ValueError as error:
        print(f"repeat_command.py: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("repeat_command.py: output I/O failed", file=sys.stderr)
        return 2
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
