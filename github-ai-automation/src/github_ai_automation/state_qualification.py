"""Finite real S3 trials in an isolated bucket; never mutate production state."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from botocore.exceptions import ClientError, ReadTimeoutError

from .contracts import Blocked, digest
from .state import S3Store, acquire

SENTINEL = "qualification-sentinel"


def observe_sentinel(store, repository_id):
    expected = {"schema": "state-isolation-sentinel/v1", "repository_id": repository_id}
    value, _ = store.read(SENTINEL)
    if value != expected:
        raise Blocked("STATE_SENTINEL_UNPROVEN")
    return {
        "schema": "state-sentinel-proof/v1",
        "repository_id": repository_id,
        "key": store.key(SENTINEL),
        "body_sha256": digest(expected),
        "bucket_sha256": hashlib.sha256(store.bucket.encode()).hexdigest(),
    }


def _denied(client, bucket, key):
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        response["Body"].close()
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"AccessDenied", "403"}:
            return True
    raise Blocked("STATE_ISOLATION_NOT_PROVEN")


def _versions(client, bucket, prefix):
    continuation = {}
    for _ in range(10):
        page = client.list_object_versions(
            Bucket=bucket, Prefix=prefix, MaxKeys=100, **continuation
        )
        for item in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]:
            if (
                not item["Key"].startswith(prefix)
                or not item.get("VersionId")
                or item["VersionId"] == "null"
            ):
                raise Blocked("STATE_VERSIONING_UNPROVEN")
            yield item
        if not page.get("IsTruncated"):
            return
        continuation = {
            "KeyMarker": page["NextKeyMarker"],
            "VersionIdMarker": page["NextVersionIdMarker"],
        }
    raise Blocked("STATE_QUALIFICATION_INVENTORY_LIMIT")


def qualify(client, anonymous, bucket, production_bucket, trial_id, sentinel):
    """Clients must use bounded timeouts and no automatic write retry."""
    if (
        not production_bucket
        or bucket == production_bucket
        or not re.fullmatch(r"[a-f0-9]{32}", trial_id)
    ):
        raise Blocked("STATE_QUALIFICATION_TARGET_INVALID")
    # Caller retains this identity before any write so interrupted cleanup is attributable.
    prefix = "qualification/" + trial_id + "/"
    store = S3Store(client, bucket, prefix=prefix.rstrip("/"))
    expected_sentinel = {
        "schema": "state-isolation-sentinel/v1",
        "repository_id": sentinel.get("repository_id"),
    }
    if (
        sentinel.get("schema") != "state-sentinel-proof/v1"
        or sentinel.get("key") != "github-ai-automation/" + SENTINEL + ".json"
        or sentinel.get("body_sha256") != digest(expected_sentinel)
        or sentinel.get("bucket_sha256") != hashlib.sha256(production_bucket.encode()).hexdigest()
    ):
        raise Blocked("STATE_SENTINEL_UNPROVEN")
    if list(_versions(client, bucket, prefix)):
        raise Blocked("STATE_QUALIFICATION_TRIAL_ALREADY_STARTED")
    checks = {}
    complete = False
    owned = False
    owner = {"invocation": uuid.uuid4().hex}
    try:
        # A denied read is required. A missing key (404) is not isolation proof.
        checks["production_sentinel_read_denied"] = _denied(
            client, production_bucket, sentinel["key"]
        )
        try:
            store.put("owner", owner, None)
            owned = True
        except Exception:
            # Reconcile an uncertain claim without adopting another invocation.
            owned = store.read("owner")[0] == owner
            raise
        first = store.put("probe", {"revision": 1}, None)
        if store.read("probe") != ({"revision": 1}, first):
            raise Blocked("STATE_READ_AFTER_CREATE_FAILED")
        checks["conditional_create_read"] = True
        try:
            store.put("probe", {"revision": 99}, None)
        except Blocked as exc:
            if exc.code != "STATE_CONFLICT":
                raise
        else:
            raise Blocked("STATE_CREATE_CONDITION_IGNORED")
        checks["duplicate_create_rejected"] = True
        second = store.put("probe", {"revision": 2}, first)
        try:
            store.put("probe", {"revision": 99}, first)
        except Blocked as exc:
            if exc.code != "STATE_CONFLICT":
                raise
        else:
            raise Blocked("STATE_UPDATE_CONDITION_IGNORED")
        checks["stale_update_rejected"] = True

        barrier = Barrier(2, timeout=15)

        def contend(number):
            barrier.wait()
            try:
                return ("accepted", number, store.put("probe", {"winner": number}, second))
            except Blocked as exc:
                if exc.code != "STATE_CONFLICT":
                    raise
                return ("conflict", number, None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(contend, (1, 2)))
        winners = [row for row in outcomes if row[0] == "accepted"]
        if len(winners) != 1 or store.read("probe") != ({"winner": winners[0][1]}, winners[0][2]):
            raise Blocked("STATE_CAS_CONTENTION_FAILED")
        checks["one_concurrent_cas_winner"] = True

        now = int(time.time())
        old = acquire(store, "lease", clock=lambda: now)
        successor = acquire(store, "lease", clock=lambda: now + 121)
        if successor.fence != old.fence + 1:
            raise Blocked("STATE_FENCE_FAILED")
        try:
            old.renew()
        except Blocked as exc:
            if exc.code != "LEASE_LOST":
                raise
        else:
            raise Blocked("STATE_STALE_LEASE_ACCEPTED")
        checks["expired_owner_fenced"] = True

        class LostResponse:
            def put_object(self, **kwargs):
                client.put_object(**kwargs)
                raise ReadTimeoutError(endpoint_url="redacted")

        uncertain = S3Store(LostResponse(), bucket, prefix=prefix.rstrip("/"))
        try:
            uncertain.put("response-loss", {"accepted": True}, None)
        except ReadTimeoutError:
            pass
        else:
            raise Blocked("STATE_RESPONSE_LOSS_NOT_INJECTED")
        if store.read("response-loss")[0] != {"accepted": True}:
            raise Blocked("STATE_RESPONSE_LOSS_UNRECONCILED")
        checks["accepted_write_read_after_lost_response"] = True
        checks["anonymous_access_denied"] = _denied(anonymous, bucket, store.key("probe"))
        versions = list(_versions(client, bucket, prefix))
        if len([v for v in versions if v["Key"] == store.key("probe")]) != 3:
            raise Blocked("STATE_VERSION_HISTORY_UNPROVEN")
        checks["independent_version_history"] = True
        complete = True
    finally:
        # Delete only this invocation's exact versions, then independently list.
        if owned:
            if store.read("owner")[0] != owner:
                raise Blocked("STATE_QUALIFICATION_OWNER_CHANGED")
            versions = sorted(
                _versions(client, bucket, prefix), key=lambda v: v["Key"] == store.key("owner")
            )
            for version in versions:
                client.delete_object(
                    Bucket=bucket, Key=version["Key"], VersionId=version["VersionId"]
                )
            if list(_versions(client, bucket, prefix)):
                raise Blocked("STATE_QUALIFICATION_CLEANUP_PENDING")
            checks["owned_versions_absent"] = True
    if not complete:
        raise Blocked("STATE_QUALIFICATION_FAILED")
    return {
        "schema": "state-qualification/v1",
        "checks": checks,
        "completed_at": int(time.time()),
        "limits": [
            "Provider credential expiry was not tested",
            "Cross-bucket write and delete denial were not tested",
            "The response-loss boundary was injected after an actual accepted write",
            "This is storage evidence, not GitHub or native product qualification",
        ],
    }
