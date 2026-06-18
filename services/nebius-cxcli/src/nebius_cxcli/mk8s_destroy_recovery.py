"""SDK-backed destroy recovery helpers for stuck MK8s node-group creates."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Any

from nebius.api.nebius.common.v1 import GetByNameRequest, GetOperationRequest, ListOperationsRequest
from nebius.api.nebius.mk8s.v1 import (
    ClusterServiceClient,
    DeleteNodeGroupRequest,
    GetNodeGroupRequest,
    ListNodeGroupsRequest,
    NodeGroupServiceClient,
    NodeGroupStatus,
)

from .sdk_auth import init_nebius_sdk


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _enum_value_name(value: Any) -> str:
    if isinstance(value, Enum):
        name = _as_text(getattr(value, "name", None)).upper()
    else:
        name = _as_text(value).upper()
    return name or "UNKNOWN"


def _response_collection(response: Any, *field_names: str) -> list[Any]:
    for field_name in field_names:
        values = getattr(response, field_name, None)
        if values is not None:
            return list(values or [])
    return []


def _event_error_text(error: Any) -> str:
    if isinstance(error, str):
        return error.strip()
    if error is None:
        return ""
    for field_name in ("message", "detail", "details", "description"):
        value = getattr(error, field_name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _node_group_state_name(raw_state: Any) -> str:
    try:
        return NodeGroupStatus.State(int(raw_state)).name
    except Exception:
        text = _as_text(raw_state)
        return text or "UNKNOWN"


def _latest_operation(operation_service: Any, resource_id: str) -> Any | None:
    response = operation_service.list(ListOperationsRequest(resource_id=resource_id)).wait()
    operations = list(getattr(response, "operations", []) or [])
    if not operations:
        return None

    latest = operations[0]
    latest_created = getattr(latest, "created_at", None)
    for item in operations[1:]:
        created_at = getattr(item, "created_at", None)
        if created_at and (not latest_created or created_at > latest_created):
            latest = item
            latest_created = created_at
    return latest


def _terminal_event_reason(item: Any) -> str | None:
    status = getattr(item, "status", None)
    events = list(getattr(status, "events", []) or [])
    for event in events:
        last_occurrence = getattr(event, "last_occurrence", None)
        level = _enum_value_name(getattr(last_occurrence, "level", None))
        if level != "ERROR":
            continue
        message = _as_text(getattr(last_occurrence, "message", None))
        error_text = _event_error_text(getattr(last_occurrence, "error", None))
        if error_text and error_text not in message:
            message = f"{message}; {error_text}" if message else error_text
        if not message:
            continue
        details: list[str] = []
        event_code = _as_text(getattr(last_occurrence, "code", None))
        if event_code:
            details.append(event_code)
        error = getattr(last_occurrence, "error", None)
        status_code = _enum_value_name(getattr(error, "code", None))
        if status_code and status_code != "UNKNOWN":
            details.append(status_code)
        detail_summary = f" [{' / '.join(details)}]" if details else ""
        return f"{message}{detail_summary}"
    return None


@dataclass(frozen=True)
class Mk8sNodeGroupDestroyCandidate:
    project_id: str
    cluster_name: str
    cluster_id: str
    node_group_name: str
    node_group_id: str
    create_operation_id: str
    reason: str


def _operation_failed(operation: Any) -> bool:
    done = getattr(operation, "done", None)
    successful = getattr(operation, "successful", None)
    if not callable(done) or not callable(successful):
        return False
    return bool(done()) and not bool(successful())


def _destroy_recovery_failure(
    item: Any,
    operation_service: Any,
    *,
    expected_operation_id: str = "",
) -> tuple[str, str] | None:
    status = getattr(item, "status", None)
    state_name = _node_group_state_name(getattr(status, "state", None)).upper()
    if state_name not in {"PROVISIONING", "CREATING"}:
        return None
    ready = getattr(status, "ready_node_count", None)
    target = getattr(status, "target_node_count", None)
    if isinstance(ready, int) and isinstance(target, int) and target > 0 and ready >= target:
        return None
    reason = _terminal_event_reason(item)
    if not reason:
        return None
    metadata = getattr(item, "metadata", None)
    node_group_id = _as_text(getattr(metadata, "id", None) or getattr(item, "id", None))
    if not node_group_id:
        return None
    latest_operation = _latest_operation(operation_service, node_group_id)
    if latest_operation is None:
        return None
    description = _as_text(getattr(latest_operation, "description", None)).lower()
    if "create node group" not in description:
        return None
    operation_id = _as_text(getattr(latest_operation, "id", None))
    if not operation_id:
        return None
    if expected_operation_id and operation_id != expected_operation_id:
        return None
    full = operation_service.get(GetOperationRequest(id=operation_id)).wait()
    if not _operation_failed(full):
        return None
    return operation_id, reason


def find_stuck_node_groups(
    *,
    project_id: str,
    cluster_name: str,
) -> tuple[Mk8sNodeGroupDestroyCandidate, ...]:
    sdk = init_nebius_sdk(parent_id=project_id, context="mk8s destroy recovery")
    try:
        cluster_client = ClusterServiceClient(sdk)
        node_group_client = NodeGroupServiceClient(sdk)
        cluster = cluster_client.get_by_name(
            GetByNameRequest(parent_id=project_id, name=cluster_name)
        ).wait()
        cluster_id = _as_text(getattr(getattr(cluster, "metadata", None), "id", None))
        if not cluster_id:
            return ()

        response = node_group_client.list(ListNodeGroupsRequest(parent_id=cluster_id)).wait()
        items = _response_collection(response, "items", "node_groups")
        operation_service = node_group_client.operation_service()
        candidates: list[Mk8sNodeGroupDestroyCandidate] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            node_group_id = _as_text(getattr(metadata, "id", None) or getattr(item, "id", None))
            node_group_name = _as_text(getattr(metadata, "name", None)) or node_group_id
            failure = _destroy_recovery_failure(item, operation_service)
            if failure is None:
                continue
            operation_id, reason = failure
            candidates.append(
                Mk8sNodeGroupDestroyCandidate(
                    project_id=project_id,
                    cluster_name=cluster_name,
                    cluster_id=cluster_id,
                    node_group_name=node_group_name,
                    node_group_id=node_group_id,
                    create_operation_id=operation_id,
                    reason=reason,
                )
            )
        return tuple(candidates)
    finally:
        with suppress(Exception):
            sdk.sync_close()


def delete_node_group(
    candidate: Mk8sNodeGroupDestroyCandidate,
    *,
    timeout_seconds: float = 600.0,
) -> str:
    sdk = init_nebius_sdk(parent_id=candidate.project_id, context="mk8s destroy recovery")
    try:
        client = NodeGroupServiceClient(sdk)
        operation_service = client.operation_service()
        node_group = client.get(GetNodeGroupRequest(id=candidate.node_group_id)).wait()
        if (
            _destroy_recovery_failure(
                node_group,
                operation_service,
                expected_operation_id=candidate.create_operation_id,
            )
            is None
        ):
            raise RuntimeError(
                "Refusing to delete MK8s node group because it no longer matches "
                "the destroy-recovery stuck-create condition: "
                f"{candidate.node_group_name} ({candidate.node_group_id})"
            )
        operation = client.delete(DeleteNodeGroupRequest(id=candidate.node_group_id)).wait()
        operation_id = _as_text(getattr(operation, "id", None))
        sync_wait = getattr(operation, "sync_wait", None)
        if not callable(sync_wait):
            raise RuntimeError(
                "Nebius MK8s node-group delete operation cannot be confirmed because "
                "`sync_wait` is unavailable: "
                f"{candidate.node_group_name} ({candidate.node_group_id}), "
                f"operation={operation_id or '(unknown)'}"
            )
        wait_result = sync_wait(timeout=timeout_seconds)
        if wait_result is not None:
            operation = wait_result
            operation_id = _as_text(getattr(operation, "id", None)) or operation_id
        successful = getattr(operation, "successful", None)
        if not callable(successful):
            raise RuntimeError(
                "Nebius MK8s node-group delete operation cannot be confirmed because "
                "`successful` is unavailable after waiting: "
                f"{candidate.node_group_name} ({candidate.node_group_id}), "
                f"operation={operation_id or '(unknown)'}"
            )
        if not successful():
            status = getattr(operation, "status", None)
            rendered_status = status() if callable(status) else status
            raise RuntimeError(
                "Nebius MK8s node-group delete did not complete successfully: "
                f"{candidate.node_group_name} ({candidate.node_group_id}), "
                f"operation={operation_id or '(unknown)'} status={rendered_status}"
            )
        return operation_id or "(unknown)"
    finally:
        with suppress(Exception):
            sdk.sync_close()


__all__ = [
    "Mk8sNodeGroupDestroyCandidate",
    "delete_node_group",
    "find_stuck_node_groups",
]
