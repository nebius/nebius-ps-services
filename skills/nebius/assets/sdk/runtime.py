"""Small synchronous Nebius SDK patterns. Importing this module performs no I/O.

Callers own authorization and the lifecycle of clients passed into operations.
Use the native async SDK directly inside an active asyncio event loop.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CloudError(RuntimeError):
    """Sanitized failure; safe identifiers survive uncertain mutations."""

    def __init__(
        self,
        code: str,
        *,
        operation_id: str = "",
        resource_id: str = "",
        outcome: str = "failed",
    ):
        super().__init__(code)
        self.code = code
        self.operation_id = operation_id
        self.resource_id = resource_id
        self.outcome = outcome


def error_code(exc: Exception) -> str:
    if isinstance(exc, CloudError):
        return exc.code
    try:
        from nebius.aio.service_error import RequestError

        if isinstance(exc, RequestError):
            return exc.status.code.name
    except ImportError:
        pass
    if isinstance(exc, TimeoutError):
        return "DEADLINE_EXCEEDED"
    if isinstance(exc, ImportError):
        return "DEPENDENCY_UNAVAILABLE"
    return "INSPECTION_FAILED"


def positive_seconds(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout and interval must be finite and positive")
    return value


def request_options(timeout: float = 30.0, *, retries: int = 3) -> dict[str, Any]:
    positive_seconds(timeout)
    return {
        "timeout": timeout,
        "per_retry_timeout": min(10.0, timeout),
        "auth_timeout": timeout,
        "retries": retries,
    }


def rpc(
    method: Callable, request: Any, *, timeout: float = 30.0, retries: int = 3
) -> Any:
    """Bound one SDK request, including auth; never expose provider error text."""
    options = request_options(timeout, retries=retries)
    try:
        return method(request, **options).wait()
    except Exception as exc:  # noqa: BLE001 - Sanitize errors and retain reconciliation IDs.
        raise CloudError(error_code(exc)) from None


def init_nebius_sdk(
    *,
    parent_id: str,
    profile: str | None = None,
    config_file: Path | None = None,
    endpoint: str | None = None,
    credentials_file: Path | None = None,
):
    """Explicit credentials file OR SDK CLI Config, without shell/token mutation.

    Selecting a profile/config disables ambient token/profile/endpoint overrides.
    With no explicit selection, Config's documented environment semantics apply.
    The resource parent is always explicit, never inherited from auth config.
    """
    if not parent_id or not parent_id.strip():
        raise ValueError("an explicit parent_id is required")
    if credentials_file is not None and (
        profile is not None or config_file is not None
    ):
        raise ValueError("choose credentials_file or profile/config_file")
    from nebius.aio.cli_config import Config
    from nebius.sdk import SDK

    kwargs: dict[str, Any] = {
        "parent_id": parent_id,
        "user_agent_prefix": "nebius-skill/1.0",
        "federation_invitation_no_browser_open": True,
    }
    if credentials_file is not None:
        if endpoint is not None:
            raise ValueError("endpoint overrides require profile/config authentication")
        kwargs["credentials_file_name"] = credentials_file
    else:
        config: dict[str, Any] = {
            "no_parent_id": True,
            "no_env": profile is not None or config_file is not None,
        }
        if profile is not None:
            config["profile"] = profile
        if config_file is not None:
            config["config_file"] = config_file
        if endpoint is not None:
            config["endpoint"] = endpoint
        kwargs["config_reader"] = Config(**config)
    return SDK(**kwargs)


@contextmanager
def owned_sdk(**kwargs):
    sdk = init_nebius_sdk(**kwargs)
    try:
        yield sdk
    except BaseException as primary:
        try:
            sdk.sync_close(timeout=10.0)
        except Exception as cleanup:  # noqa: BLE001 - Never mask the primary failure.
            primary.cleanup_error_code = error_code(cleanup)
        raise
    else:
        try:
            sdk.sync_close(timeout=10.0)
        except Exception:  # noqa: BLE001 - Redact provider details at this boundary.
            raise CloudError("SDK_CLOSE_FAILED") from None


def identity(resource: Any) -> str:
    value = getattr(getattr(resource, "metadata", None), "id", None)
    if not isinstance(value, str) or not value:
        raise CloudError("RESOURCE_ID_MISSING")
    return value


def verify_identity(
    resource: Any,
    *,
    parent_id: str,
    resource_id: str | None = None,
    name: str | None = None,
) -> str:
    rid = identity(resource)
    meta = resource.metadata
    if meta.parent_id != parent_id or (resource_id is not None and rid != resource_id):
        raise CloudError("RESOURCE_IDENTITY_MISMATCH")
    if name is not None and meta.name != name:
        raise CloudError("RESOURCE_NAME_MISMATCH")
    return rid


def collect_pages(
    method: Callable,
    request_factory: Callable[[str], Any],
    *,
    field: str = "items",
    key: Callable = identity,
    timeout: float = 120.0,
    max_pages: int = 1000,
    max_items: int = 100000,
) -> list[Any]:
    """Return a complete inventory or fail; partial inventories are never returned."""
    deadline = time.monotonic() + positive_seconds(timeout)
    if max_pages < 1 or max_items < 1:
        raise ValueError("collection bounds must be positive")
    token = ""
    tokens: set[str] = set()
    keys: set[Any] = set()
    result: list[Any] = []
    for _ in range(max_pages):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CloudError("DEADLINE_EXCEEDED")
        page = rpc(method, request_factory(token), timeout=min(30.0, remaining))
        values = getattr(page, field, None)
        next_token = getattr(page, "next_page_token", None)
        if values is None or not isinstance(next_token, str):
            raise CloudError("MALFORMED_PAGE")
        for item in values:
            item_key = key(item)
            if not item_key or item_key in keys:
                raise CloudError("DUPLICATE_OR_MISSING_IDENTITY")
            keys.add(item_key)
            result.append(item)
            if len(result) > max_items:
                raise CloudError("INVENTORY_LIMIT_EXCEEDED")
        if not next_token:
            return result
        if next_token in tokens:
            raise CloudError("PAGINATION_CYCLE")
        tokens.add(next_token)
        token = next_token
    raise CloudError("PAGE_LIMIT_EXCEEDED")


@dataclass(frozen=True)
class MutationResult:
    operation_id: str
    resource_id: str


def submit(method: Callable, request: Any, *, timeout: float = 300.0) -> MutationResult:
    """Submit once, wait for terminal success, retain IDs on failure. Never retry writes."""
    deadline = time.monotonic() + positive_seconds(timeout)
    operation_id = resource_id = ""
    op = None
    try:
        op = rpc(method, request, timeout=min(30.0, timeout), retries=1)
        operation_id, resource_id = op.id, op.resource_id
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CloudError("DEADLINE_EXCEEDED")
        op.sync_wait(
            timeout=remaining,
            interval=1.0,
            poll_iteration_timeout=min(30.0, remaining),
            poll_per_retry_timeout=min(10.0, remaining),
            poll_retries=3,
            auth_timeout=min(30.0, remaining),
        )
        resource_id = op.resource_id
        if not op.done() or not op.successful():
            raise CloudError("OPERATION_FAILED")
        if not operation_id or not resource_id:
            raise CloudError("OPERATION_IDENTITY_MISSING")
        return MutationResult(operation_id, resource_id)
    except Exception as exc:  # noqa: BLE001 - Sanitize errors and retain reconciliation IDs.
        if op is not None:
            operation_id = op.id or operation_id
            resource_id = op.resource_id or resource_id
        raise CloudError(
            error_code(exc),
            operation_id=operation_id,
            resource_id=resource_id,
            outcome="reconcile-required",
        ) from None


def wait_ready(
    read: Callable[[float], Any],
    predicate: Callable[[Any], bool],
    *,
    timeout: float = 90.0,
    interval: float = 2.0,
) -> Any:
    """Read with remaining budget; predicate may fail on terminal or mismatched state."""
    deadline = time.monotonic() + positive_seconds(timeout)
    positive_seconds(interval)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CloudError("READINESS_TIMEOUT")
        resource = read(min(30.0, remaining))
        if predicate(resource):
            return resource
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
