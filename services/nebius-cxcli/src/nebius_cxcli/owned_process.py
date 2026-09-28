"""Execution-owned subprocesses with independent parent-death/lease supervision."""

from __future__ import annotations

import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from .lease_clock import elapsed

_active: ExecutionScope | None = None


class ManagedProcess:
    """Popen-shaped handle whose termination stops the entire contained group."""

    def __init__(self, scope: ExecutionScope, args: Any, kwargs: dict[str, Any]) -> None:
        self.args = args
        self._scope = scope
        self._send_lock = threading.Lock()
        self._finished = False
        self.quiescent = False
        self.returncode: int | None = None
        self._channel, child = socket.socketpair()
        self._channel.setblocking(False)
        if any(
            kwargs.get(key)
            for key in ("shell", "preexec_fn", "pass_fds", "executable", "process_group")
        ):
            self._channel.close()
            child.close()
            raise ValueError(
                "Owned subprocesses require direct execution without process overrides"
            )
        try:
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "nebius_cxcli.owned_process_worker",
                    str(child.fileno()),
                    scope.identity,
                    str(scope.deadline),
                    str(scope.ownership_fd if scope.ownership_fd is not None else -1),
                    *args,
                ],
                **(
                    kwargs
                    | {
                        "pass_fds": (child.fileno(),)
                        + ((scope.ownership_fd,) if scope.ownership_fd is not None else ()),
                        "start_new_session": True,
                    }
                ),
            )
        except BaseException:
            self._channel.close()
            raise
        finally:
            child.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._process, name)

    def _send(self, message: dict[str, Any]) -> None:
        if not self._send_lock.acquire(blocking=False):
            self._channel.close()
            return
        try:
            if self._finished:
                return
            data = json.dumps(message | {"owner": self._scope.identity}).encode() + b"\n"
            try:
                if self._channel.send(data) != len(data):
                    self._channel.close()
            except BrokenPipeError:
                # A completed worker may already have queued its quiescence ACK.
                # Keep the receive half until wait/poll consumes that evidence.
                pass
            except OSError:
                self._channel.close()  # EOF is an irreversible cancellation.
        finally:
            self._send_lock.release()

    def __enter__(self) -> ManagedProcess:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.terminate()
        self.wait(timeout=11)
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is not None:
                stream.close()

    def terminate(self) -> None:
        self._send({"cancel": True})

    def kill(self) -> None:
        self.terminate()  # Never kill the supervisor before its descendants.

    def _finish(self) -> None:
        if self._finished:
            return
        try:
            result = json.loads(self._channel.recv(4096))
            self.quiescent = result.get("quiescent") is True
            self.returncode = int(result["returncode"])
            if not self.quiescent and self.returncode == 0:
                self.returncode = 125  # A successful leader cannot vouch for its group.
        except (OSError, ValueError, KeyError, TypeError):
            self.returncode = 125
        finally:
            self._finished = True
            self._channel.close()

    def poll(self) -> int | None:
        if self._process.poll() is not None:
            self._finish()
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self._process.wait(timeout=timeout)
        self._finish()
        assert self.returncode is not None
        return self.returncode

    def communicate(self, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        result = self._process.communicate(*args, **kwargs)
        self._finish()
        return result


class ExecutionScope:
    """One outer backend owner, shared by nested calls and their worker threads."""

    def __init__(self, identity: str, deadline: float, *, ownership_fd: int | None = None) -> None:
        self.identity, self.deadline = identity, deadline
        self.ownership_fd = ownership_fd
        self.stopping = False
        self._processes: list[ManagedProcess] = []
        self._lock = threading.RLock()
        self._generation = 0
        self._handlers: dict[int, Any] = {}

    def activate(self) -> None:
        global _active
        if _active is not None:
            raise RuntimeError("Another backend execution scope is already active")
        _active = self
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                self._handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._signal)

    def _signal(self, signum: int, frame: Any) -> None:
        already_stopping = self.stopping
        self.cancel()
        if not already_stopping:
            raise KeyboardInterrupt(f"Operation interrupted by signal {signum}")

    def check(self) -> None:
        if self.stopping or elapsed() >= self.deadline:
            self.cancel()
            raise RuntimeError("Operation authority ended; owned subprocesses are stopping")

    def grant(self, deadline: float) -> None:
        with self._lock:
            self.check()
            if deadline <= self.deadline:
                return
            self.deadline = deadline
            self._generation += 1
            for process in self._processes:
                process._send({"generation": self._generation, "deadline": deadline})

    def launch(self, args: Any, kwargs: dict[str, Any]) -> ManagedProcess:
        with self._lock:
            self.check()
            process = ManagedProcess(self, args, kwargs)
            self._processes.append(process)
            # A signal may arrive during Popen. Closing cannot admit this child.
            if self.stopping:
                process.terminate()
                raise RuntimeError("Operation cancelled during subprocess startup")
            process._send(
                {"start": True, "generation": self._generation, "deadline": self.deadline}
            )
            return process

    def cancel(self) -> None:
        # Signal handling must not wait for another thread's lock or network I/O.
        self.stopping = True
        for process in tuple(self._processes):
            process.terminate()

    def close(self, deadline: float) -> bool:
        self.cancel()
        clean = True
        for process in self._processes:
            try:
                process.wait(timeout=max(0, min(11, deadline - elapsed())))
                clean = process.quiescent and clean
            except (subprocess.TimeoutExpired, OSError):
                clean = False
        return clean

    def deactivate(self) -> None:
        global _active
        for sig, handler in self._handlers.items():
            signal.signal(sig, handler)
        if _active is self:
            _active = None


def popen(args: Any, **kwargs: Any) -> Any:
    scope = _active
    return subprocess.Popen(args, **kwargs) if scope is None else scope.launch(args, kwargs)


def _exchange_io(process: Any, data: Any, poll_timeout: Callable[[], float]) -> tuple[Any, Any]:
    """Drain all pipes without losing unfinished input between authority polls."""
    outputs = {"stdout": process.stdout, "stderr": process.stderr}
    buffers: dict[str, bytearray] = {name: bytearray() for name in outputs}
    if data is None:
        data = b""
    elif process.text_mode:
        data = data.encode(process.stdin.encoding, process.stdin.errors)
    pending = memoryview(data)
    offset = 0
    with selectors.DefaultSelector() as selector:
        for name, stream in outputs.items():
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
        if process.stdin is not None:
            if pending:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
        while selector.get_map():
            for key, _events in selector.select(poll_timeout()):
                stream = process.stdin if key.data == "stdin" else outputs[key.data]
                try:
                    if key.data == "stdin":
                        try:
                            offset += os.write(key.fd, pending[offset : offset + 65536])
                        except BrokenPipeError:
                            offset = len(pending)
                        if offset < len(pending):
                            continue
                    else:
                        chunk = os.read(key.fd, 65536)
                        if chunk:
                            buffers[key.data].extend(chunk)
                            continue
                except BlockingIOError:
                    continue
                selector.unregister(stream)
                stream.close()
        while True:
            try:
                process.wait(timeout=poll_timeout())
                break
            except subprocess.TimeoutExpired:
                continue
    result = []
    for name, stream in outputs.items():
        value: Any = bytes(buffers[name]) if stream is not None else None
        if value is not None and process.text_mode:
            value = (
                value.decode(stream.encoding, stream.errors)
                .replace("\r\n", "\n")
                .replace("\r", "\n")
            )
        result.append(value)
    return result[0], result[1]


def run(args: Any, *, abort_check: Any = None, **kwargs: Any) -> subprocess.CompletedProcess:
    if _active is None and abort_check is None:
        return subprocess.run(args, **kwargs)
    check = kwargs.pop("check", False)
    timeout = kwargs.pop("timeout", None)
    capture = kwargs.pop("capture_output", False)
    data = kwargs.pop("input", None)
    if capture:
        if "stdout" in kwargs or "stderr" in kwargs:
            raise ValueError("capture_output conflicts with stdout/stderr")
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if data is not None:
        if "stdin" in kwargs:
            raise ValueError("input conflicts with stdin")
        kwargs["stdin"] = subprocess.PIPE
    if abort_check is not None:
        reason = abort_check()
        if reason:
            raise RuntimeError(f"Command aborted before launch: {reason}")
    process = popen(args, **kwargs)
    deadline = time.monotonic() + timeout if timeout is not None else float("inf")

    def poll_timeout() -> float:
        if _active is not None:
            _active.check()
        if abort_check is not None:
            reason = abort_check()
            if reason:
                raise RuntimeError(f"Command aborted: {reason}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, timeout)
        return min(0.25, remaining)

    try:
        stdout, stderr = _exchange_io(process, data, poll_timeout)
        if _active is not None:
            _active.check()
        if isinstance(process, ManagedProcess) and not process.quiescent:
            raise RuntimeError("Owned subprocess shutdown is unresolved")
        if check and process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args, stdout, stderr)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=11)
        except subprocess.TimeoutExpired:
            if not isinstance(process, ManagedProcess):
                process.kill()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
        raise
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
