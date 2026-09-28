"""Private subprocess supervisor; control/parent lifetime is separate from output."""

from __future__ import annotations

import ctypes
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress

from .lease_clock import elapsed


def _signal_group(pid: int, sig: int) -> None:
    # Darwin can transiently deny a group signal while its last members exit.
    # This is not evidence of quiescence: the subsequent probe must prove absence.
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def _group_alive(process: subprocess.Popen) -> bool:
    if process.poll() is not None and sys.platform == "linux":
        # This worker is a subreaper; reap adopted grandchildren after the leader.
        with suppress(ChildProcessError):
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop(process: subprocess.Popen) -> bool:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        _signal_group(process.pid, sig)
        until = elapsed() + 5
        while _group_alive(process) and elapsed() < until:
            time.sleep(0.025)
        if not _group_alive(process):
            process.wait()
            return True
    return False


def _start_permission(
    channel: socket.socket, identity: str, deadline: float
) -> tuple[float, int] | None:
    """Do not launch for a parent that died/cancelled during interpreter startup."""
    pending = b""
    granted = False
    generation = -1
    while elapsed() < deadline:
        ready, _, _ = select.select([channel], [], [], 0 if granted and not pending else 0.1)
        if not ready:
            if granted and not pending:
                return deadline, generation
            continue
        data = channel.recv(4096)
        if not data:
            return None
        pending += data
        if len(pending) > 65536:
            return None
        while b"\n" in pending:
            frame, pending = pending.split(b"\n", 1)
            message = json.loads(frame)
            if message.get("owner") != identity or message.get("cancel"):
                return None
            if not granted:
                if not message.get("start") or message.get("deadline") != deadline:
                    return None
                granted = True
            elif (
                message.get("start")
                or message["generation"] <= generation
                or message["deadline"] <= deadline
                or elapsed() >= deadline
            ):
                return None
            generation, deadline = message["generation"], message["deadline"]
    return None


def main() -> None:
    channel = socket.socket(fileno=int(sys.argv[1]))
    identity = sys.argv[2]
    deadline = float(sys.argv[3])
    ownership_fd = int(sys.argv[4])
    generation = 0
    process = None
    clean = True
    code = 125
    # The supervisor survives terminal signals; only its private control channel
    # and authority deadline govern contained processes.
    signal.signal(signal.SIGINT, lambda *_: None)
    signal.signal(signal.SIGTERM, lambda *_: None)
    if sys.platform == "linux":
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise RuntimeError("Cannot supervise descendant processes")
    try:
        permission = _start_permission(channel, identity, deadline)
        if permission is None:
            return
        deadline, generation = permission
        process = subprocess.Popen(
            sys.argv[5:],
            start_new_session=True,
            pass_fds=(ownership_fd,) if ownership_fd >= 0 else (),
        )
        clean = False
        pending = b""
        closing = False
        while not closing:
            if elapsed() >= deadline:
                break
            if process.poll() is not None:
                break
            ready, _, _ = select.select([channel], [], [], max(0, min(0.1, deadline - elapsed())))
            if not ready:
                continue
            data = channel.recv(4096)
            if not data:
                break
            pending += data
            if len(pending) > 65536:
                break
            while b"\n" in pending:
                frame, pending = pending.split(b"\n", 1)
                message = json.loads(frame)
                if message.get("owner") != identity or message.get("cancel"):
                    closing = True
                    break
                new_generation = message["generation"]
                new_deadline = message["deadline"]
                # Never let a queued grant revive expired authority.
                if (
                    elapsed() >= deadline
                    or new_generation <= generation
                    or new_deadline <= deadline
                ):
                    closing = True
                    break
                generation, deadline = new_generation, new_deadline
        clean = _stop(process)
        code = process.returncode if process.returncode is not None else 125
    except BaseException:
        if process is not None:
            clean = _stop(process)
    finally:
        # A local owner transfers a kernel-lock reference to this supervisor.
        # Keep it until the contained group is gone even if the parent exits or
        # its bounded wait expires. No persisted deployment history is involved.
        while ownership_fd >= 0 and process is not None and not clean:
            try:
                clean = _stop(process)
            except OSError:
                time.sleep(0.1)
        with suppress(OSError):
            channel.sendall(json.dumps({"quiescent": clean, "returncode": code}).encode())
        channel.close()
        if ownership_fd >= 0:
            os.close(ownership_fd)


if __name__ == "__main__":
    main()
