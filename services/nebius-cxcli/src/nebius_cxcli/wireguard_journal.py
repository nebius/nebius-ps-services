"""Cross-machine WireGuard ownership in the admitted regular backend bucket."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .object_storage_errors import ObjectStorageError

PREFIX = "cxcli/wireguard/"


def journal_keys(project: str, component: str) -> tuple[str, str]:
    identity = hashlib.sha256(f"{project}\0{component}".encode()).hexdigest()
    return f"{PREFIX}{identity}.json", f"{PREFIX}{identity}.lock"


class WireGuardJournal:
    def __init__(self, transport: Any, project: str, component: str):
        self.transport = transport
        self.key, self.lock_key = journal_keys(project, component)
        self.project, self.component = project, component
        self.owner = str(uuid.uuid4())
        self.lock_etag = ""
        self.etag = ""
        self.value: dict[str, Any] = {}
        self.uncertain = False
        self.reload()

    def read(self, key: str) -> tuple[str, dict[str, Any]] | None:
        try:
            response = self.transport.request("get_object", key, max_bytes=256 * 1024)
        except ObjectStorageError as error:
            if error.code in {"NoSuchKey", "NotFound", "404"}:
                return None
            raise
        try:
            value = json.loads(response.body)
            if not response.etag or not isinstance(value, dict):
                raise ValueError
        except (ValueError, TypeError):
            raise RuntimeError("Invalid WireGuard operation record") from None
        return response.etag, value

    def reload(self) -> None:
        found = self.read(self.key)
        if found:
            self.etag, self.value = found
            if (
                self.value.get("schema"),
                self.value.get("project_id"),
                self.value.get("component_id"),
            ) != (1, self.project, self.component):
                raise RuntimeError("WireGuard operation record belongs to a different gateway")

    def acquire(self) -> None:
        if self.lock_etag:
            self.assert_held()
            return
        body = {"owner": self.owner}
        try:
            result = self.transport.request(
                "put_object", self.lock_key, body=json.dumps(body).encode(), create_only=True
            )
            self.lock_etag = result.etag
        except ObjectStorageError:
            observed = self.read(self.lock_key)
            if observed is None or observed[1] != body:
                holder = observed[1].get("owner", "unknown") if observed else "unknown"
                raise RuntimeError(
                    f"WireGuard lifecycle is locked or uncertain (owner {holder}); prove the prior writer stopped before explicit recovery"
                ) from None
            self.lock_etag = observed[0]
        self.assert_held()
        self.reload()

    def assert_held(self) -> None:
        if not self.lock_etag or self.read(self.lock_key) != (
            self.lock_etag,
            {"owner": self.owner},
        ):
            raise RuntimeError("WireGuard remote execution ownership was lost")

    def save(self) -> None:
        self.assert_held()
        # Explicitly reject private key/token material even if a caller misuses the journal.
        body = json.dumps(self.value, sort_keys=True, separators=(",", ":")).encode()
        if any(
            marker in body for marker in (b"PRIVATE KEY", b'"access_token"', b'"ed25519_private"')
        ):
            raise RuntimeError("Secret material is forbidden in WireGuard operation records")
        try:
            result = self.transport.request(
                "put_object", self.key, body=body, etag=self.etag or None, create_only=not self.etag
            )
            self.etag = result.etag
        except ObjectStorageError:
            observed = self.read(self.key)
            if observed is None or observed[1] != self.value:
                self.uncertain = True
                raise RuntimeError(
                    "WireGuard journal write is unconfirmed; no subsequent effect is permitted"
                ) from None
            self.etag = observed[0]

    def release(self) -> None:
        if self.lock_etag:
            self.assert_held()
            if self.uncertain or self.value.get("in_flight"):
                # A timed-out provider operation can still be running after this process exits.
                # Only explicit stopped-writer/terminal-operation recovery may release it.
                return
            self.transport.request("delete_object", self.lock_key, etag=self.lock_etag)
            self.lock_etag = ""

    def begin_effect(self, *, client: str, nonce: str, collection: str, key: str) -> None:
        if self.value.get("in_flight"):
            raise RuntimeError(
                "WireGuard has an unconfirmed cloud effect; explicit recovery is required"
            )
        self.value["in_flight"] = {
            "client": client,
            "nonce": nonce,
            "collection": collection,
            "key": key,
        }
        self.save()

    def unlock(self, owner: str) -> None:
        found = self.read(self.lock_key)
        if found is None:
            return
        if found[1] != {"owner": owner}:
            raise RuntimeError("WireGuard lock owner changed; refusing recovery")
        self.transport.request("delete_object", self.lock_key, etag=found[0])
