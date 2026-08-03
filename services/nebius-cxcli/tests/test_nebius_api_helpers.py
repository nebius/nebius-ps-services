from __future__ import annotations

import subprocess
from typing import Any

import pytest

from nebius_cxcli.nebius_api_helpers import (
    nebius_operation_id,
    wait_nebius_operation,
)


class _Operation:
    def __init__(
        self,
        *,
        operation_id: str = "operation-123",
        successful: bool = True,
        timeout: bool = False,
    ) -> None:
        self.id = operation_id
        self._successful = successful
        self._timeout = timeout
        self.waited_with: dict[str, float | int] | None = None

    def sync_wait(self, **kwargs: float | int) -> None:
        self.waited_with = kwargs
        if self._timeout:
            raise TimeoutError("provider timeout")

    def successful(self) -> bool:
        return self._successful

    def status(self) -> dict[str, Any]:
        return {"code": "OK" if self._successful else "INTERNAL"}


def test_wait_nebius_operation_requires_successful_terminal_operation() -> None:
    operation = _Operation()

    assert (
        wait_nebius_operation(
            operation,
            timeout_seconds=19,
            action="Nebius update",
        )
        is operation
    )
    assert operation.waited_with == {
        "timeout": 19,
        "poll_iteration_timeout": 19,
        "poll_per_retry_timeout": 19,
        "poll_retries": 2,
        "auth_timeout": 19,
    }
    assert nebius_operation_id(operation) == "operation-123"


def test_wait_nebius_operation_rejects_failed_operation() -> None:
    with pytest.raises(RuntimeError, match="Nebius update failed"):
        wait_nebius_operation(
            _Operation(successful=False),
            timeout_seconds=19,
            action="Nebius update",
        )


def test_wait_nebius_operation_rejects_missing_terminal_evidence() -> None:
    with pytest.raises(RuntimeError, match="did not return a waitable"):
        wait_nebius_operation(
            object(),
            timeout_seconds=19,
            action="Nebius update",
        )


def test_wait_nebius_operation_translates_provider_timeout() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        wait_nebius_operation(
            _Operation(timeout=True),
            timeout_seconds=19,
            action="Nebius update",
        )
