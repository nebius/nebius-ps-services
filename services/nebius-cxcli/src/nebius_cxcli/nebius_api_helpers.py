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


def wait_nebius_operation(
    operation: object,
    *,
    timeout_seconds: int,
    action: str,
) -> None:
    """Wait for a Nebius operation when the SDK exposes sync_wait."""

    sync_wait = getattr(operation, "sync_wait", None)
    if not callable(sync_wait):
        return
    try:
        sync_wait(timeout=timeout_seconds)
    except TimeoutError as exc:
        raise subprocess.TimeoutExpired(action, timeout_seconds) from exc
