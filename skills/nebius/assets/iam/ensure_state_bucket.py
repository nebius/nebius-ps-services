"""Snippet: ensure Nebius Object Storage bucket exists and is ACTIVE."""

from __future__ import annotations

import os
import time
from contextlib import suppress
from dataclasses import dataclass

from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.storage.v1 import (
    BucketServiceClient,
    BucketSpec,
    BucketStatus,
    CreateBucketRequest,
    GetBucketByNameRequest,
    VersioningPolicy,
)

DEFAULT_BUCKET_READY_TIMEOUT_SECONDS = 90.0
DEFAULT_BUCKET_READY_POLL_SECONDS = 2.0


@dataclass(frozen=True)
class StateBucketSettings:
    project_id: str
    bucket_name: str


def _wait(op_or_message):  # type: ignore[no-untyped-def]
    return op_or_message.wait() if hasattr(op_or_message, "wait") else op_or_message


def _is_not_found_error(exc: Exception) -> bool:
    message = str(exc).lower()
    tokens = (
        "not found",
        "not_found",
        "statuscode.not_found",
        "resource not found",
        "nosuchbucket",
        "bucket doesn't exist",
    )
    return any(token in message for token in tokens)


def _is_already_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "statuscode.already_exists" in message


def _bucket_is_active(bucket) -> bool:  # type: ignore[no-untyped-def]
    status = getattr(bucket, "status", None)
    state = getattr(status, "state", None)
    if state is None:
        return False
    state_name = str(getattr(state, "name", state)).upper()
    if state_name in {"ACTIVE", "STATE.ACTIVE"}:
        return True
    return state == BucketStatus.State.ACTIVE


def _wait_for_bucket_ready(*, buckets, lookup, bucket_name: str) -> None:  # type: ignore[no-untyped-def]
    timeout_seconds = float(
        str(os.environ.get("NEBIUS_CXCLI_TFSTATE_READY_TIMEOUT_SECONDS", "") or "").strip()
        or DEFAULT_BUCKET_READY_TIMEOUT_SECONDS
    )
    poll_seconds = float(
        str(os.environ.get("NEBIUS_CXCLI_TFSTATE_READY_POLL_SECONDS", "") or "").strip()
        or DEFAULT_BUCKET_READY_POLL_SECONDS
    )
    timeout_seconds = max(1.0, timeout_seconds)
    poll_seconds = max(0.25, poll_seconds)

    deadline = time.monotonic() + timeout_seconds
    last_state = "unknown"
    while True:
        bucket = _wait(buckets.get_by_name(lookup))
        status = getattr(bucket, "status", None)
        state = getattr(status, "state", None)
        last_state = str(getattr(state, "name", state) or "unknown")
        if _bucket_is_active(bucket):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Bucket '{bucket_name}' not ready yet (state={last_state}) after {timeout_seconds:.0f}s"
            )
        time.sleep(poll_seconds)


def ensure_state_bucket(sdk, settings: StateBucketSettings) -> bool:  # type: ignore[no-untyped-def]
    """
    Ensure bucket exists and is ACTIVE.

    Returns True when bucket is newly created in this call.
    """
    buckets = BucketServiceClient(sdk)
    lookup = GetBucketByNameRequest(parent_id=settings.project_id, name=settings.bucket_name)

    try:
        _wait(buckets.get_by_name(lookup))
        _wait_for_bucket_ready(buckets=buckets, lookup=lookup, bucket_name=settings.bucket_name)
        return False
    except Exception as exc:
        if not _is_not_found_error(exc):
            raise RuntimeError(f"Failed to verify bucket '{settings.bucket_name}': {exc}") from exc

    try:
        _wait(
            buckets.create(
                CreateBucketRequest(
                    metadata=ResourceMetadata(
                        parent_id=settings.project_id,
                        name=settings.bucket_name,
                    ),
                    spec=BucketSpec(versioning_policy=VersioningPolicy.ENABLED),
                )
            )
        )
        _wait_for_bucket_ready(buckets=buckets, lookup=lookup, bucket_name=settings.bucket_name)
        return True
    except Exception as exc:
        if _is_already_exists_error(exc):
            _wait_for_bucket_ready(buckets=buckets, lookup=lookup, bucket_name=settings.bucket_name)
            return False
        raise RuntimeError(f"Failed to create bucket '{settings.bucket_name}': {exc}") from exc


def close_sdk(sdk) -> None:  # type: ignore[no-untyped-def]
    with suppress(Exception):
        sdk.sync_close()
