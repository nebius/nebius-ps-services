"""Small helpers shared by Nebius SDK/API adapters."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from typing import Any

from .runtime_config import to_plain_data

_NEBIUS_REQUEST_TIMEOUT_SECONDS = 60.0
_NEBIUS_REQUEST_PER_RETRY_TIMEOUT_SECONDS = 20.0
_NEBIUS_REQUEST_RETRIES = 0
_NEBIUS_OPERATION_POLL_RETRIES = 2


def renewable_nebius_auth_options(*, timeout_seconds: float) -> dict[str, str]:
    """Require bounded synchronous refresh when the SDK marks renewal due."""

    from nebius.aio.token.renewable import (
        OPTION_RENEW_REQUEST_TIMEOUT,
        OPTION_RENEW_SYNCHRONOUS,
    )

    renewal_timeout = min(
        max(float(timeout_seconds), 1.0),
        _NEBIUS_REQUEST_PER_RETRY_TIMEOUT_SECONDS,
    )
    return {
        OPTION_RENEW_SYNCHRONOUS: "true",
        OPTION_RENEW_REQUEST_TIMEOUT: str(renewal_timeout),
    }


def bounded_nebius_request_kwargs(
    *,
    timeout_seconds: float = _NEBIUS_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Return finite time and retry bounds for one synchronous SDK request."""

    timeout = max(float(timeout_seconds), 1.0)
    return {
        "timeout": timeout,
        "per_retry_timeout": min(timeout, _NEBIUS_REQUEST_PER_RETRY_TIMEOUT_SECONDS),
        "auth_timeout": min(timeout, _NEBIUS_REQUEST_TIMEOUT_SECONDS),
        "auth_options": renewable_nebius_auth_options(timeout_seconds=timeout),
        "retries": _NEBIUS_REQUEST_RETRIES,
    }


def sdk_message_to_mapping(value: object) -> Mapping[str, Any]:
    """Convert a supported Nebius SDK direct message to a JSON-style mapping."""

    if isinstance(value, Mapping):
        return dict(value)
    from nebius.base.protos.direct import Message
    from nebius.base.protos.json_format import message_to_dict

    if not isinstance(value, Message):
        raise TypeError("value is not a Nebius SDK direct message")
    return message_to_dict(
        value,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )


def sdk_parse_message(wrapper_type: type[Any], payload: Mapping[str, Any]) -> Any:
    """Build a supported Nebius SDK direct message from a JSON-style mapping."""

    from nebius.base.protos.direct import Message
    from nebius.base.protos.json_format import parse_dict

    wrapper = wrapper_type()
    if not isinstance(wrapper, Message):
        raise TypeError("wrapper_type does not construct a Nebius SDK direct message")
    return parse_dict(
        to_plain_data(dict(payload)),
        wrapper,
        ignore_unknown_fields=True,
    )


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
    poll_timeout = min(
        max(float(timeout_seconds), 1.0),
        _NEBIUS_REQUEST_PER_RETRY_TIMEOUT_SECONDS,
    )
    try:
        sync_wait(
            timeout=timeout_seconds,
            poll_iteration_timeout=poll_timeout,
            poll_per_retry_timeout=poll_timeout,
            poll_retries=_NEBIUS_OPERATION_POLL_RETRIES,
            auth_timeout=min(
                max(float(timeout_seconds), 1.0),
                _NEBIUS_REQUEST_TIMEOUT_SECONDS,
            ),
            auth_options=renewable_nebius_auth_options(
                timeout_seconds=max(float(timeout_seconds), 1.0),
            ),
        )
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
