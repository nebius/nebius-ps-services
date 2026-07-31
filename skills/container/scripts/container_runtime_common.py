#!/usr/bin/env python3
"""Shared bounded subprocess primitives for container validation helpers."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


MAX_COMMAND_OUTPUT = 1024 * 1024


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated


def _drain_bounded(
    stream: BinaryIO,
    limit: int,
    chunks: list[bytes],
    truncated: list[bool],
) -> None:
    retained = 0
    try:
        while data := stream.read(64 * 1024):
            remaining = max(0, limit - retained)
            if remaining:
                chunks.append(data[:remaining])
                retained += min(len(data), remaining)
            if len(data) > remaining:
                truncated[0] = True
    except (OSError, ValueError):
        truncated[0] = True


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
    process.wait()


def run_command(
    argv: list[str],
    *,
    timeout: float = 60.0,
    output_limit: int = MAX_COMMAND_OUTPUT,
    cwd: Path | None = None,
) -> CommandResult:
    """Run an argument array with bounded capture and process-group timeout."""

    resolved_cwd: Path | None = None
    if cwd is not None:
        resolved_cwd = cwd.resolve()
        if not resolved_cwd.is_dir():
            raise ValueError(f"command cwd is not a directory: {resolved_cwd}")
    process = subprocess.Popen(  # noqa: S603 - argv is never passed to a shell.
        argv,
        cwd=resolved_cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    stdout_stream = process.stdout
    stderr_stream = process.stderr
    if stdout_stream is None or stderr_stream is None:
        process.kill()
        process.wait()
        raise RuntimeError("subprocess pipe creation failed")
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_truncated = [False]
    stderr_truncated = [False]
    stdout_thread = threading.Thread(
        target=_drain_bounded,
        args=(stdout_stream, output_limit, stdout_chunks, stdout_truncated),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_bounded,
        args=(stderr_stream, output_limit, stderr_chunks, stderr_truncated),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        returncode = 124
        _terminate_process_tree(process)
    stdout_thread.join(timeout=2.0)
    stderr_thread.join(timeout=2.0)
    if stdout_thread.is_alive():
        stdout_stream.close()
        stdout_thread.join(timeout=0.1)
    if stderr_thread.is_alive():
        stderr_stream.close()
        stderr_thread.join(timeout=0.1)
    stdout_stream.close()
    stderr_stream.close()
    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    return CommandResult(
        argv=argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated[0],
        stderr_truncated=stderr_truncated[0],
    )
