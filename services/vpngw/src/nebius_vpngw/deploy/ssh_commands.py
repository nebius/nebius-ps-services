"""Deadline-bound SSH command observation; a timeout never proves cancellation."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

DEADLINE: ContextVar[float | None] = ContextVar("ha_activation_ssh_deadline", default=None)
SOFT_OPERATION: ContextVar[bool] = ContextVar("ha_soft_operation", default=False)


@contextmanager
def soft_operation():
    token = SOFT_OPERATION.set(True)
    previous = DEADLINE.set(None)
    try:
        yield
    finally:
        DEADLINE.reset(previous)
        SOFT_OPERATION.reset(token)


@contextmanager
def budget(deadline: float | None):
    from .vm_ha_cloud import vm_ha_observation_budget, vm_ha_observation_remaining

    parent = DEADLINE.get()
    if deadline is not None and parent is not None:
        deadline = min(deadline, parent)
    if deadline is not None:
        deadline = min(
            deadline, time.monotonic() + vm_ha_observation_remaining(default=float("inf"))
        )
    token = DEADLINE.set(deadline)
    try:
        if deadline is None:
            yield None
        else:
            with vm_ha_observation_budget(seconds=deadline - time.monotonic()):
                yield deadline
    finally:
        DEADLINE.reset(token)


@contextmanager
def connection_deadline(client: Any, deadline: float):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("activation SSH connection deadline expired")
    watchdog = threading.Timer(remaining, client.close)
    watchdog.daemon = True
    watchdog.start()
    try:
        yield remaining
        if time.monotonic() >= deadline:
            raise TimeoutError("activation SSH connection exceeded deadline")
    finally:
        watchdog.cancel()
        watchdog.join()


def run(
    client: Any,
    command: str,
    *,
    deadline: float,
    limit: int = 4096,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[int, bytes, bytes]:
    remaining = deadline - clock()
    if remaining <= 0:
        raise TimeoutError("remote command deadline expired; outcome unverified")
    expired = threading.Event()

    def expire() -> None:
        expired.set()
        client.close()

    watchdog = threading.Timer(remaining, expire)
    watchdog.daemon = True
    channel = None
    watchdog.start()
    try:
        _, stdout, _ = client.exec_command(command, get_pty=True, timeout=remaining)
        channel = stdout.channel
        output, errors = bytearray(), bytearray()
        while True:
            if expired.is_set() or clock() >= deadline:
                raise TimeoutError("remote command deadline expired; outcome unverified")
            for ready, receive, retained in (
                (channel.recv_ready, channel.recv, output),
                (channel.recv_stderr_ready, channel.recv_stderr, errors),
            ):
                if ready():
                    chunk = receive(65536)
                    retained.extend(chunk[: max(0, limit - len(retained))])
            if (
                channel.exit_status_ready()
                and not channel.recv_ready()
                and not channel.recv_stderr_ready()
            ):
                code = channel.recv_exit_status()
                if expired.is_set() or clock() >= deadline:
                    raise TimeoutError("remote command completion exceeded deadline")
                return code, bytes(output), bytes(errors)
            sleeper(min(0.02, max(0.0, deadline - clock())))
    finally:
        watchdog.cancel()
        watchdog.join()
        if channel is not None:
            channel.close()
