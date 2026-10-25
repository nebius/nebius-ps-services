"""Conditional shared NAT records. Locks never expire or transfer automatically."""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Mapping
from typing import Any

from .object_storage_errors import ObjectStorageError


def state_keys(state_key: str) -> tuple[str, str]:
    return state_key + ".nat.json", state_key + ".nat.lock"


class NatState:
    def __init__(self, transport: Any, state_key: str, *, readonly: bool = False) -> None:
        self.transport = transport
        self.key, self.lock_key = state_keys(state_key)
        self.readonly = readonly
        self.owner = str(uuid.uuid4())
        self.lock_etag = ""
        self.etag = ""
        self.value: dict[str, Any] = {"version": 1, "gateways": {}}
        self.reload()

    def read(self, key: str) -> tuple[str, dict[str, Any]] | None:
        try:
            response = self.transport.request("get_object", key, max_bytes=2 * 1024 * 1024)
        except ObjectStorageError as exc:
            if exc.code in {"NoSuchKey", "NotFound", "404"}:
                return None
            raise
        value = json.loads(response.body)
        if not response.etag or not isinstance(value, dict):
            raise RuntimeError("Invalid shared NAT record")
        return response.etag, value

    def reload(self) -> None:
        record = self.read(self.key)
        if record is not None:
            self.etag, self.value = record
            if (
                set(self.value) != {"version", "gateways"}
                or self.value["version"] != 1
                or not isinstance(self.value["gateways"], dict)
            ):
                raise RuntimeError("Unsupported shared NAT record")

    def acquire(self) -> None:
        if self.readonly or self.lock_etag:
            return
        body = json.dumps({"owner": self.owner}).encode()
        try:
            result = self.transport.request(
                "put_object", self.lock_key, body=body, create_only=True
            )
        except ObjectStorageError:
            # Reconcile a lost acknowledgement, never overwrite an existing lock.
            record = self.read(self.lock_key)
            if record is None or record[1] != {"owner": self.owner}:
                holder = record[1].get("owner", "unknown") if record else "unknown"
                raise RuntimeError(
                    f"NAT shared lock held or acquisition uncertain (owner {holder}); stop prior writers before explicit nat unlock"
                ) from None
            self.lock_etag = record[0]
        else:
            self.lock_etag = result.etag
        self.assert_held()
        # A different host may have completed work between initial read and acquisition.
        self.reload()

    def assert_held(self) -> None:
        if self.readonly or not self.lock_etag:
            raise RuntimeError("NAT mutation requires shared execution authority")
        observed = self.read(self.lock_key)
        if observed != (self.lock_etag, {"owner": self.owner}):
            raise RuntimeError("NAT shared execution authority was lost")

    def save(self) -> None:
        if self.readonly:
            return
        self.assert_held()
        desired = copy.deepcopy(self.value)
        body = json.dumps(desired, sort_keys=True, separators=(",", ":")).encode()
        try:
            result = self.transport.request(
                "put_object", self.key, body=body, etag=self.etag or None, create_only=not self.etag
            )
        except ObjectStorageError:
            observed = self.read(self.key)
            if observed is None or observed[1] != desired:
                raise RuntimeError(
                    "NAT journal update was not confirmed; no subsequent effect is permitted"
                ) from None
            self.etag = observed[0]
        else:
            self.etag = result.etag

    def release(self) -> None:
        if not self.lock_etag:
            return
        self.assert_held()
        self.transport.request("delete_object", self.lock_key, etag=self.lock_etag)
        self.lock_etag = ""

    def unlock(self, expected_owner: str) -> None:
        record = self.read(self.lock_key)
        if record is None:
            return
        if record[1] != {"owner": expected_owner}:
            raise RuntimeError("NAT lock owner changed; refusing release")
        self.transport.request("delete_object", self.lock_key, etag=record[0])


def assert_quota_resolved(report: Any) -> None:
    if (
        report.has_confirmed_insufficiency
        or report.errors
        or report.coverage_gaps
        or report.unknown_checks
    ):
        raise RuntimeError(
            "NAT requires confirmed live quota headroom for every required resource; run quota-check and resolve unknown or insufficient allowances"
        )


def assert_activation_plan(plan: Mapping[str, Any], desired: Mapping[str, str]) -> None:
    """The second Terraform stage may change only admitted subnet route fields."""
    for resource in plan.get("resource_changes", []):
        change = resource.get("change", {})
        if change.get("actions") in (["no-op"], ["read"]):
            continue
        before, after = change.get("before") or {}, change.get("after") or {}
        if (
            resource.get("type") != "nebius_vpc_v1_subnet"
            or change.get("actions") != ["update"]
            or before.get("id") not in desired
            or after.get("route_table_id") != desired[before["id"]]
        ):
            raise RuntimeError("NAT activation plan contains an unapproved resource action")
        # Computed provider status may be unknown, but every configurable field must stay fixed.
        keys = (set(before) | set(after)) - {"route_table_id", "status", "updated_at"}
        if any(before.get(key) != after.get(key) for key in keys):
            raise RuntimeError(
                "NAT activation plan changes more than the delegated route-table field"
            )
