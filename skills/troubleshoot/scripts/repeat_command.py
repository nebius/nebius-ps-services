#!/usr/bin/env python3
"""Measure repeated command behavior with bounded, redacted evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from evidence_redaction import is_sensitive_name, redact_text

SCHEMA_VERSION = 1
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


def drain_stream(stream: BinaryIO, state: dict[str, Any], limit: int) -> None:
    try:
        while chunk := stream.read(8192):
            state["bytes_seen"] += len(chunk)
            tail = state["tail"]
            tail.extend(chunk)
            if len(tail) > limit:
                del tail[: len(tail) - limit]
    except (OSError, ValueError):
        return


def process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_group_members(process_group_id: int) -> list[int]:
    try:
        result = subprocess.run(
            ("ps", "-axo", "pid=,pgid="),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    members: list[int] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, pgid = (int(field) for field in fields)
        except ValueError:
            continue
        if pgid == process_group_id:
            members.append(pid)
    return members


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        process_group_id = process.pid
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            if process.poll() is None:
                process.kill()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and process_group_exists(process_group_id):
            time.sleep(0.05)
        if process_group_exists(process_group_id):
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                for pid in process_group_members(process_group_id):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (PermissionError, ProcessLookupError):
                        continue
    else:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
    if process.poll() is None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


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
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    timed_out = False
    stdout_state: dict[str, Any] = {"tail": bytearray(), "bytes_seen": 0}
    stderr_state: dict[str, Any] = {"tail": bytearray(), "bytes_seen": 0}
    threads: list[threading.Thread] = []
    try:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            process = subprocess.Popen(argv, **kwargs)
        except OSError as error:
            duration = time.monotonic() - started
            error_name = type(error).__name__
            message = f"command launch failed: {error_name} errno={error.errno}"
            return {
                "exit_code": None,
                "timed_out": False,
                "duration_seconds": round(duration, 6),
                "signature": make_signature(None, False, "", message),
                "stdout_tail": "",
                "stderr_tail": "",
                "launch_error": message,
            }

        assert process.stdout is not None
        assert process.stderr is not None
        for stream, state in (
            (process.stdout, stdout_state),
            (process.stderr, stderr_state),
        ):
            thread = threading.Thread(
                target=drain_stream,
                args=(stream, state, max_tail_bytes),
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_process(process)
        for thread in threads:
            thread.join(timeout=2)
    finally:
        if process is not None and process.poll() is None:
            terminate_process(process)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        for thread in threads:
            thread.join(timeout=1)

    duration = time.monotonic() - started
    stdout_tail = redact_text(stdout_state["tail"].decode("utf-8", errors="replace"))
    stderr_tail = redact_text(stderr_state["tail"].decode("utf-8", errors="replace"))
    exit_code = process.returncode if process is not None else None
    signature = make_signature(exit_code, timed_out, stdout_tail, stderr_tail)
    return {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": round(duration, 6),
        "signature": signature,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "stdout_bytes_seen": stdout_state["bytes_seen"],
        "stderr_bytes_seen": stderr_state["bytes_seen"],
        "stdout_truncated": stdout_state["bytes_seen"] > max_tail_bytes,
        "stderr_truncated": stderr_state["bytes_seen"] > max_tail_bytes,
    }


def summarize(
    argv: list[str], requested_runs: int, runs: list[dict[str, Any]], interrupted: bool
) -> dict[str, Any]:
    durations = [float(run["duration_seconds"]) for run in runs]
    passes = sum(run["exit_code"] == 0 and not run["timed_out"] for run in runs)
    timeouts = sum(bool(run["timed_out"]) for run in runs)
    launch_errors = sum("launch_error" in run for run in runs)
    signatures = Counter(str(run["signature"]) for run in runs)
    completed = len(runs)
    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": datetime.now(UTC).isoformat(),
        "command": redact_argv(argv),
        "requested_runs": requested_runs,
        "completed_runs": completed,
        "interrupted": interrupted,
        "pass_count": passes,
        "failure_count": completed - passes,
        "timeout_count": timeouts,
        "launch_error_count": launch_errors,
        "pass_rate": round(passes / completed, 6) if completed else 0.0,
        "timing_seconds": {
            "min": min(durations) if durations else None,
            "max": max(durations) if durations else None,
            "mean": statistics.fmean(durations) if durations else None,
            "median": statistics.median(durations) if durations else None,
        },
        "signature_clusters": [
            {"signature": signature, "count": count}
            for signature, count in sorted(signatures.items())
        ],
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
