"""Small helpers shared by Nebius SDK/API adapters."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from typing import Any

from .runtime_config import to_plain_data


def sdk_message_to_mapping(value: object) -> Mapping[str, Any]:
    """Convert a Nebius SDK wrapper object to a JSON-style mapping."""

    if isinstance(value, Mapping):
        return dict(value)
    message = getattr(value, "__pb2_message__", None)
    if message is None:
        return {}
    from google.protobuf.json_format import MessageToDict

    parsed = MessageToDict(
        message,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )
    return parsed if isinstance(parsed, Mapping) else {}


def sdk_parse_message(wrapper_type: type[Any], payload: Mapping[str, Any]) -> Any:
    """Build a Nebius SDK wrapper from a JSON-style mapping."""

    from google.protobuf.json_format import ParseDict

    wrapper = wrapper_type()
    message = wrapper.__pb2_message__
    parsed = ParseDict(
        to_plain_data(dict(payload)),
        message,
        ignore_unknown_fields=True,
    )
    return wrapper_type(parsed)


def nebius_operation_id(operation: object) -> str:
    """Return the stable ID exposed by a Nebius SDK operation wrapper."""

    direct_id = str(getattr(operation, "id", "") or "").strip()
    if direct_id:
        return direct_id
    payload = sdk_message_to_mapping(operation)
    payload_id = str(
        payload.get("id", "")
        or sdk_message_to_mapping(payload.get("metadata", {})).get("id", "")
        or ""
    ).strip()
    if payload_id:
        return payload_id
    raw = getattr(operation, "raw", None)
    if callable(raw):
        raw_operation = raw()
        if raw_operation is not operation:
            return nebius_operation_id(raw_operation)
    return ""


def wait_nebius_operation(
    operation: object,
    *,
    timeout_seconds: int,
    action: str,
) -> object:
    """Wait for a Nebius operation and require a successful terminal result."""

    sync_wait = getattr(operation, "sync_wait", None)
    if not callable(sync_wait):
        raise RuntimeError(f"{action} did not return a waitable Nebius provider operation.")
    try:
        sync_wait(timeout=timeout_seconds)
    except TimeoutError as exc:
        raise subprocess.TimeoutExpired(action, timeout_seconds) from exc
    successful = getattr(operation, "successful", None)
    if not callable(successful):
        raise RuntimeError(
            f"{action} returned a provider operation without terminal success evidence."
        )
    if not successful():
        status_getter = getattr(operation, "status", None)
        status = status_getter() if callable(status_getter) else None
        status_suffix = f" status={status}" if status is not None else ""
        raise RuntimeError(f"{action} failed.{status_suffix}")
    return operation
