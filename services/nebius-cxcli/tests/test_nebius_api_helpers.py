from __future__ import annotations

import subprocess
from typing import Any

import pytest

from nebius_cxcli.nebius_api_helpers import (
    bounded_nebius_request_kwargs,
    nebius_operation_id,
    sdk_message_to_mapping,
    sdk_parse_message,
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
        self.waited_with: dict[str, object] | None = None

    def sync_wait(self, **kwargs: object) -> None:
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
        "auth_options": {
            "token_renew_synchronous": "true",
            "token_renew_request_timeout": "19.0",
        },
    }
    assert nebius_operation_id(operation) == "operation-123"


def test_bounded_nebius_request_waits_for_due_token_renewal() -> None:
    assert bounded_nebius_request_kwargs(timeout_seconds=45) == {
        "timeout": 45.0,
        "per_retry_timeout": 20.0,
        "auth_timeout": 45.0,
        "auth_options": {
            "token_renew_synchronous": "true",
            "token_renew_request_timeout": "20.0",
        },
        "retries": 0,
    }


def test_sdk_direct_messages_round_trip_through_protojson_mapping() -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.mk8s.v1 import NodeGroup, NodeGroupSpec

    node_group = NodeGroup(
        metadata=ResourceMetadata(
            id="nodegroup-123",
            parent_id="cluster-123",
            name="worker",
            resource_version=17,
        ),
        spec=NodeGroupSpec(version="1.32", fixed_node_count=3),
    )

    assert sdk_message_to_mapping(node_group) == {
        "metadata": {
            "id": "nodegroup-123",
            "parent_id": "cluster-123",
            "name": "worker",
            "resource_version": "17",
        },
        "spec": {"version": "1.32", "fixed_node_count": "3"},
    }
    parsed = sdk_parse_message(
        NodeGroupSpec,
        {"version": "1.33", "fixed_node_count": 4},
    )
    assert parsed.version == "1.33"
    assert parsed.fixed_node_count == 4


def test_sdk_message_to_mapping_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError, match="Nebius SDK direct message"):
        sdk_message_to_mapping(object())


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
