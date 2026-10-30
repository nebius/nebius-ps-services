"""Content-free Tailscale ownership journal, fenced by the shared project lease."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from .object_storage_errors import ObjectStorageError


class Journal:
    def __init__(self, transport: Any, key: str, assert_held: Callable[[], None]) -> None:
        self.transport, self.key, self._assert_project = (
            transport,
            key + ".tailscale.json",
            assert_held,
        )
        self.lock_key = key + ".tailscale.lock"
        self.owner, self.lock_etag = uuid.uuid4().hex, ""
        self.etag = ""
        self.value: dict[str, Any] = {"schema": 3, "gateways": {}}
        found = self.read()
        if found:
            self.etag, self.value = found

    def acquire(self) -> None:
        self._assert_project()
        body = json.dumps({"owner": self.owner}).encode()
        try:
            self.lock_etag = self.transport.request(
                "put_object", self.lock_key, body=body, create_only=True
            ).etag
        except ObjectStorageError:
            observed = self.transport.request("get_object", self.lock_key, max_bytes=4096)
            if json.loads(observed.body) != {"owner": self.owner}:
                raise RuntimeError(
                    "Tailscale gateway operation is locked by "
                    + str(json.loads(observed.body).get("owner", "unknown"))
                    + "; use tailscale recover only after stopping the prior writer"
                ) from None
            self.lock_etag = observed.etag
        self.assert_held()
        found = self.read()
        if found:
            self.etag, self.value = found

    def assert_held(self) -> None:
        self._assert_project()
        if not self.lock_etag:
            raise RuntimeError("Tailscale operation requires its shared remote lock")
        observed = self.transport.request("get_object", self.lock_key, max_bytes=4096)
        if observed.etag != self.lock_etag or json.loads(observed.body) != {"owner": self.owner}:
            raise RuntimeError("Tailscale execution authority was lost")

    def release(self) -> None:
        if self.lock_etag:
            self.assert_held()
            self.transport.request("delete_object", self.lock_key, etag=self.lock_etag)
            self.lock_etag = ""

    def read(self) -> tuple[str, dict[str, Any]] | None:
        try:
            response = self.transport.request("get_object", self.key, max_bytes=2 * 1024 * 1024)
        except ObjectStorageError as error:
            if error.code in {"NoSuchKey", "NotFound", "404"}:
                return None
            raise
        value = json.loads(response.body)
        if (
            not response.etag
            or set(value) != {"schema", "gateways"}
            or value["schema"] != 3
            or not isinstance(value["gateways"], dict)
        ):
            raise RuntimeError("Unsupported Tailscale ownership journal")
        from .tailscale_enrollment import validate_operation

        for record in value["gateways"].values():
            if (
                not isinstance(record, dict)
                or type(record.get("activation_started")) is not bool
                or record.get("lifecycle")
                not in {
                    "provisioning",
                    "infrastructure-ready",
                    "activating",
                    "active",
                    "retiring",
                    "retired",
                }
                or not isinstance(record.get("members"), dict)
            ):
                raise RuntimeError("Unsupported Tailscale lifecycle checkpoint")
            for member in record.get("members", {}).values():
                if "enrollment_pending" in member or "enrollment_key_id" in member:
                    raise RuntimeError("Unsupported Tailscale enrollment journal")
                if member.get("enrollment"):
                    validate_operation(member["enrollment"])
                for operation in member.get("prior_enrollments", []):
                    validate_operation(operation)
        return response.etag, value

    def save(self) -> None:
        self.assert_held()
        body = json.dumps(self.value, sort_keys=True, separators=(",", ":")).encode()
        try:
            result = self.transport.request(
                "put_object", self.key, body=body, etag=self.etag or None, create_only=not self.etag
            )
            self.etag = result.etag
        except ObjectStorageError:
            found = self.read()
            if not found or found[1] != self.value:
                raise RuntimeError(
                    "Tailscale journal write is unconfirmed; no further mutation is permitted"
                ) from None
            self.etag = found[0]
        self.assert_held()
