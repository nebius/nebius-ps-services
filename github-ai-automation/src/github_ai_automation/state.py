"""Conditional durable state. Ambiguous effects are reconciled, never replayed."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .contracts import Blocked, canonical


class Store(Protocol):
    def read(self, key: str) -> tuple[dict | None, str | None]: ...
    def put(self, key: str, value: dict, expected: str | None) -> str: ...


class NotSubmitted(Exception):
    """Trusted effect owner proved its process exited before remote submission."""


@dataclass(frozen=True)
class Unconfirmed:
    """Submission was accepted, but its authoritative postcondition is pending."""

    result: dict


class S3Store:
    """Regular Nebius Object Storage buckets only; no local production fallback."""

    def __init__(self, client, bucket: str, prefix: str = "github-ai-automation"):
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise Blocked("INVALID_STATE_BUCKET")
        self.client, self.bucket, self.prefix = client, bucket, prefix

    def key(self, key: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9/_-]+", key) or ".." in key:
            raise Blocked("INVALID_STATE_KEY")
        return f"{self.prefix}/{key}.json"

    def read(self, key: str):
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.key(key))
            body = response["Body"].read(1_048_577)
            if len(body) > 1_048_576:
                raise Blocked("STATE_TOO_LARGE")
            return json.loads(body), response["ETag"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                return None, None
            raise Blocked("STATE_READ_UNAVAILABLE") from None

    def put(self, key: str, value: dict, expected: str | None):
        from botocore.exceptions import ClientError

        body = canonical(value)
        if len(body) > 1_048_576:
            raise Blocked("STATE_TOO_LARGE")
        condition = {"IfMatch": expected} if expected else {"IfNoneMatch": "*"}
        try:
            return self.client.put_object(
                Bucket=self.bucket,
                Key=self.key(key),
                Body=body,
                ContentType="application/json",
                **condition,
            )["ETag"]
        except ClientError as exc:
            # S3 providers may name the same HTTP precondition differently
            # (Nebius uses KeyAlreadyExists for an If-None-Match rejection).
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in {
                409,
                412,
            } or exc.response["Error"]["Code"] in {
                "PreconditionFailed",
                "412",
                "ConditionalRequestConflict",
                "409",
            }:
                raise Blocked("STATE_CONFLICT") from None
            # A transport error also leaves the operation uncertain. Caller must read back.
            raise Blocked("STATE_WRITE_UNCERTAIN") from None

    def scan(self, prefix: str):
        """Bounded recovery inventory; keys only, never credential/config values."""
        path = self.key(prefix).removesuffix(".json") + "/"
        token = None
        for _ in range(100):
            args = {"Bucket": self.bucket, "Prefix": path, "MaxKeys": 100}
            if token:
                args["ContinuationToken"] = token
            page = self.client.list_objects_v2(**args)
            for item in page.get("Contents", []):
                key = item["Key"]
                if key.endswith(".json"):
                    yield key[len(self.prefix) + 1 : -5]
            if not page.get("IsTruncated"):
                return
            token = page["NextContinuationToken"]
        raise Blocked("RECOVERY_INVENTORY_LIMIT")


@dataclass
class Lease:
    store: Store
    key: str
    owner: str
    fence: int
    clock: Callable[[], float]

    def assert_owned(self) -> tuple[dict, str]:
        data, etag = self.store.read(self.key)
        if (
            not data
            or data["owner"] != self.owner
            or data["fence"] != self.fence
            or data["until"] <= self.clock()
        ):
            raise Blocked("LEASE_LOST")
        return data, etag

    def renew(self, seconds: int = 120):
        data, etag = self.assert_owned()
        data["until"] = int(self.clock()) + seconds
        self.store.put(self.key, data, etag)

    def release(self):
        data, etag = self.assert_owned()
        data["until"] = 0
        self.store.put(self.key, data, etag)


def acquire(store: Store, key: str, *, seconds: int = 120, clock=time.time) -> Lease:
    data, etag = store.read(key)
    if data and data["until"] > clock():
        raise Blocked("OPERATION_BUSY")
    owner = uuid.uuid4().hex
    fence = data["fence"] + 1 if data else 1
    store.put(key, {"owner": owner, "fence": fence, "until": int(clock()) + seconds}, etag)
    return Lease(store, key, owner, fence, clock)


def effect(
    store: Store, lease: Lease, key: str, intent: dict, observe: Callable, execute: Callable
):
    """Reconcile an intent after *every* uncertain outcome, including process death.

    execute must use provider-native identity/preconditions. A lease is not a
    replacement for a remote compare-and-set, and must remain live across I/O.
    """
    lease.assert_owned()
    record, etag = store.read(key)
    previous = []
    if record and record.get("status") == "not_submitted":
        previous = record.get("previous_attempts", []) + [
            {k: v for k, v in record.items() if k != "previous_attempts"}
        ]
        if len(previous) >= 3:
            raise Blocked("EFFECT_RETRY_EXHAUSTED")
        record = None
    if record and record["intent"] != intent:
        raise Blocked("EFFECT_IDENTITY_CONFLICT")
    if record:
        found = observe(intent)
        if found is not None:
            lease.assert_owned()
            if record.get("status") != "observed" or record.get("result") != found:
                store.put(key, {**record, "status": "observed", "result": found}, etag)
            return found
        # Absence after a timeout does not establish rejection of an accepted operation.
        raise Blocked("EFFECT_RECONCILIATION_REQUIRED")
    store.put(
        key,
        {
            "intent": intent,
            "fence": lease.fence,
            "status": "pending",
            "previous_attempts": previous,
        },
        etag,
    )
    lease.assert_owned()
    try:
        result = execute(intent)
    except NotSubmitted:
        lease.assert_owned()
        current, revision = store.read(key)
        if not current or current["intent"] != intent:
            raise Blocked("EFFECT_IDENTITY_CONFLICT") from None
        store.put(key, {**current, "status": "not_submitted"}, revision)
        raise Blocked("EFFECT_NOT_SUBMITTED") from None
    lease.assert_owned()
    current, revision = store.read(key)
    if not current or current["intent"] != intent:
        raise Blocked("EFFECT_IDENTITY_CONFLICT")
    status = "pending" if isinstance(result, Unconfirmed) else "observed"
    result = result.result if isinstance(result, Unconfirmed) else result
    store.put(key, {**current, "status": status, "result": result}, revision)
    return result
