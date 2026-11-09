"""Bounded recovery of pure Compute reads. This module admits no effects."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

from .apply_diagnostics import Failure, ObservationUnavailable
from .vm_ha_cloud import (
    nebius_request_error_code_is,
    vm_ha_observation_budget,
    vm_ha_observation_remaining,
)

T = TypeVar("T")


class AuthorityUnavailable(ObservationUnavailable):
    """Fresh evidence is missing; retries never imply drift or mutation failure."""

    def __init__(self, reason: str, *, attempts: int = 0) -> None:
        self.retryable = reason in {"compute-read-timeout", "compute-read-unavailable"}
        self.attempts = attempts
        self.first = Failure("verify-ssh-authority", "compute-observation-unavailable", (reason,))
        RuntimeError.__init__(
            self,
            "HA operation pending: waiting for fresh Compute authority; resume the same command"
            if self.retryable
            else "HA Compute authority is blocked; check access and exact resource identity",
        )


def compute_failure(error: Exception | None, *, attempts: int = 0) -> AuthorityUnavailable:
    reason = "compute-read-failed"
    if error is None:
        reason = "compute-response-empty"
    elif isinstance(error, TimeoutError):
        reason = "compute-read-timeout"
    elif isinstance(error, PermissionError):
        reason = "compute-read-denied"
    else:
        for code, category in (
            ("DEADLINE_EXCEEDED", "compute-read-timeout"),
            ("UNAVAILABLE", "compute-read-unavailable"),
            ("PERMISSION_DENIED", "compute-read-denied"),
            ("UNAUTHENTICATED", "compute-read-unauthenticated"),
            ("NOT_FOUND", "compute-not-found"),
        ):
            if nebius_request_error_code_is(error, code):
                reason = category
                break
    return AuthorityUnavailable(reason, attempts=attempts)


def read_compute(
    read: Callable[[dict[str, Any]], T],
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Retry only a pure read, sharing its parent's cumulative observation budget.

    The SDK still owns bounded token refresh. One SDK RPC attempt per call
    prevents application and SDK retries multiplying. No cached result is used.
    """
    attempts = 0
    try:
        with vm_ha_observation_budget(clock=clock):
            while True:
                remaining = vm_ha_observation_remaining()
                # SDK 0.3.40 adds 200ms to its synchronous auth wait for cleanup.
                # Reserve that margin and reject completion after our deadline.
                allowance = min(10.0, remaining - 0.2)
                if allowance <= 0:
                    raise TimeoutError("Compute observation budget exhausted")
                attempts += 1
                try:
                    value = read(
                        dict(
                            timeout=allowance,
                            auth_timeout=allowance,
                            per_retry_timeout=allowance,
                            retries=1,
                        )
                    )
                except Exception as error:
                    failure = compute_failure(error, attempts=attempts)
                    if not failure.retryable or attempts >= 3:
                        raise failure from error
                    remaining = vm_ha_observation_remaining()
                    delay = min(0.2 * 2 ** (attempts - 1) * (0.5 + 0.5 * jitter()), remaining)
                    sleep(delay)
                    continue
                vm_ha_observation_remaining()  # A late success grants no authority.
                if value is None:
                    raise compute_failure(None, attempts=attempts)
                return value
    except TimeoutError as error:
        raise compute_failure(error, attempts=attempts) from error
