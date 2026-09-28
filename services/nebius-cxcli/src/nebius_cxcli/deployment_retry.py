"""Bounded retry for explicitly safe operations, without nested allowances."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Protocol


class TransientReadError(RuntimeError):
    """A read failed with a positively identified temporary transport condition."""


_RETRY_OWNER: ContextVar[bool] = ContextVar("deployment_retry_owner", default=False)


class ReadResult(Protocol):
    returncode: int
    stderr: str


def retry_transport_read[Result: ReadResult](read: Callable[[], Result]) -> Result:
    """Retry only explicit transport diagnostics from an already-proven read."""

    def once() -> Result:
        result = read()
        detail = result.stderr.casefold()
        permanent = (
            "unauthorized",
            "forbidden",
            "getting credentials",
            "certificate",
            "no such host",
        )
        transient = (
            "connection reset by peer",
            "tls handshake timeout",
            "i/o timeout",
            "serviceunavailable",
        )
        if (
            result.returncode
            and not any(item in detail for item in permanent)
            and any(item in detail for item in transient)
        ):
            raise TransientReadError(
                "Kubernetes read failed: " + next(item for item in transient if item in detail)
            )
        return result

    return retry_read(once)


def retry_read[Result](
    read: Callable[[], Result],
    *,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = lambda: random.uniform(0.0, 0.25),
) -> Result:
    """Three total attempts. Callers must prove this operation cannot mutate."""
    if _RETRY_OWNER.get():
        return read()
    token = _RETRY_OWNER.set(True)
    try:
        for attempt in range(3):
            try:
                return read()
            except TransientReadError:
                if attempt == 2:
                    raise
                sleep(2**attempt + min(max(jitter(), 0.0), 0.25))
        raise AssertionError("unreachable")
    finally:
        _RETRY_OWNER.reset(token)
