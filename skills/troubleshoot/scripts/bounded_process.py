"""Bound subprocess ingestion and lifetime, including inherited output pipes."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

STREAM_LIMIT = 64 * 1024


def stop(process: subprocess.Popen[bytes]) -> None:
    """Kill the owned session, including children after their parent exited."""
    # Reap an exited parent before signaling its remaining group (Darwin may
    # reject a signal to a group containing only the unreaped zombie).
    process.poll()
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


def observe(
    argv: tuple[str, ...] | list[str],
    cwd: Path | None = None,
    *,
    deadline: float | None = None,
    timeout: float = 5,
    stdout_limit: int = STREAM_LIMIT,
    tail: bool = False,
    on_stdout=None,
) -> dict[str, Any]:
    end = min(deadline or float("inf"), time.monotonic() + timeout)
    result: dict[str, Any] = {
        "status": "skipped",
        "stdout": b"",
        "stderr": b"",
        "returncode": None,
        "truncated": False,
        "stdout_bytes_seen": 0,
        "stderr_bytes_seen": 0,
    }
    if os.name != "posix":
        result.update(
            status="unavailable", reason="POSIX process-group support required"
        )
        return result
    if time.monotonic() >= end:
        return result
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError:
        result["status"] = "unavailable"
        return result
    result["status"] = "ok"
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        with selectors.DefaultSelector() as poller:
            for name in buffers:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                poller.register(stream, selectors.EVENT_READ, name)
            while poller.get_map():
                remaining = end - time.monotonic()
                if remaining <= 0:
                    result["status"] = "timeout"
                    break
                for key, _ in poller.select(min(remaining, 0.1)):
                    name = key.data
                    limit = stdout_limit if name == "stdout" or tail else STREAM_LIMIT
                    chunk = os.read(
                        key.fd,
                        8192 if tail else min(8192, limit - len(buffers[name]) + 1),
                    )
                    result[name + "_bytes_seen"] += len(chunk)
                    if name == "stdout" and chunk and on_stdout is not None:
                        on_stdout(chunk)
                    if not chunk:
                        poller.unregister(key.fileobj)
                    elif tail:
                        buffers[name].extend(chunk)
                        if len(buffers[name]) > limit:
                            del buffers[name][:-limit]
                            result["truncated"] = True
                    elif len(buffers[name]) + len(chunk) > limit:
                        buffers[name].extend(chunk[: limit - len(buffers[name])])
                        result.update(status="truncated", truncated=True)
                        break
                    else:
                        buffers[name].extend(chunk)
                if result["truncated"] and not tail:
                    break
            if result["status"] == "ok":
                try:
                    process.wait(timeout=max(0.001, end - time.monotonic()))
                except subprocess.TimeoutExpired:
                    result["status"] = "timeout"
    except KeyboardInterrupt:
        result["status"] = "interrupted"
    finally:
        try:
            stop(process)
        finally:
            for name, buffer in buffers.items():
                getattr(process, name).close()
                result[name] = bytes(buffer)
            result["returncode"] = process.returncode
    if result["status"] == "ok" and process.returncode:
        result["status"] = "failed"
    return result
