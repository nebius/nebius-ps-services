"""Resumable enrollment: public operation records and private, original key custody."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .ssh_host_identity import _directory, _private_bytes
from .tailscale_api import ApiError
from .tailscale_config import secret

BINDING = (
    "project_id",
    "instance_id",
    "boot_disk_id",
    "public_allocation_id",
    "network_id",
    "subnet_id",
    "host_public_key",
    "bootstrap_sha256",
)
PHASES = {
    "rejected",
    "issuing",
    "key-ready",
    "delivering",
    "enrolled",
    "fencing",
    "revoked",
    "retired",
}
NO_OUTSTANDING_KEY_PHASES = {"rejected", "revoked", "retired"}


def validate_operation(op: Any) -> None:
    if (
        not isinstance(op, dict)
        or set(op)
        != {
            "schema",
            "id",
            "binding",
            "tailnet",
            "tags",
            "key_id",
            "phase",
            "mode",
            "credential",
            "key_retired",
        }
        or op["schema"] != 2
        or not isinstance(op["credential"], dict)
        or type(op["key_retired"]) is not bool
        or not isinstance(op["id"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", op["id"])
        or not isinstance(op["binding"], dict)
        or set(op["binding"]) != set(BINDING)
        or any(not isinstance(v, str) or not v for v in op["binding"].values())
        or op["phase"] not in PHASES
        or op["mode"] not in {"oauth", "auth-key"}
        or not isinstance(op["key_id"], str)
        or not isinstance(op["tailnet"], str)
        or not isinstance(op["tags"], list)
        or not op["tags"]
        or any(not isinstance(tag, str) for tag in op["tags"])
    ):
        raise RuntimeError("Unsupported Tailscale enrollment operation")
    pin = op["credential"]
    expected = (
        {"source", "client_id", "client_secret_ref"}
        if pin.get("source") == "reference"
        else {"source", "client_id", "project_id", "secret_id", "version_id"}
    )
    if pin and (
        pin.get("source") not in {"reference", "secretstash"}
        or set(pin) != expected
        or any(not isinstance(v, str) or not v for v in pin.values())
    ):
        raise RuntimeError("Unsupported OAuth credential binding")
    if op["mode"] == "oauth" and not pin:
        raise RuntimeError("OAuth enrollment requires the original credential binding")


def contract(op: dict[str, Any]) -> dict[str, Any]:
    validate_operation(op)
    return {"schema": 1, **{k: op[k] for k in ("id", "binding", "tailnet", "tags", "key_id")}}


class KeyStore:
    """No-follow owner-only files, outside Terraform, reports and the shared journal."""

    def __init__(self, base: Path) -> None:
        self.base = base

    @contextmanager
    def directory(self):
        descriptors = []
        try:
            root = _directory(self.base / ".nebius-cxcli")
            descriptors.append(root)
            folder = _directory("tailscale-enrollment", parent=root)
            descriptors.append(folder)
            yield folder
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    @staticmethod
    def name(op: dict[str, Any]) -> str:
        validate_operation(op)
        return op["id"] + ".json"

    @staticmethod
    def digest(op: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(op["binding"], sort_keys=True).encode()).hexdigest()

    def read(self, op: dict[str, Any]) -> dict[str, str] | None:
        with self.directory() as folder:
            try:
                raw = _private_bytes(self.name(op), directory=folder)
            except FileNotFoundError:
                try:
                    raw = _private_bytes(".pending-" + self.name(op), directory=folder)
                except FileNotFoundError:
                    return None
        try:
            value = json.loads(raw)
            if (
                set(value) != {"schema", "id", "binding_sha256", "key_id", "key"}
                or value["schema"] != 1
                or value["id"] != op["id"]
                or value["binding_sha256"] != self.digest(op)
                or not isinstance(value["key_id"], str)
                or not isinstance(value["key"], str)
                or not value["key"].startswith("tskey-auth-")
                or len(value["key"]) > 4096
                or (op["key_id"] and value["key_id"] != op["key_id"])
            ):
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError):
            raise RuntimeError(
                "Enrollment runtime key receipt is invalid; recovery required"
            ) from None

    def put(self, op: dict[str, Any], key_id: str, key: str) -> None:
        if not isinstance(key, str) or not key.startswith("tskey-auth-") or len(key) > 4096:
            raise RuntimeError("Enrollment API returned an invalid key; issuance is uncertain")
        value = {
            "schema": 1,
            "id": op["id"],
            "binding_sha256": self.digest(op),
            "key_id": key_id,
            "key": key,
        }
        with self.directory() as folder:
            name = self.name(op)
            temporary = ".pending-" + name
            if self.read(op) is not None:
                raise RuntimeError("Original enrollment key is already persisted")
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=folder,
            )
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(value, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.rename(temporary, name, src_dir_fd=folder, dst_dir_fd=folder)
                os.fsync(folder)
            except BaseException:
                # A complete pending receipt can recover a lost local publication.
                # An incomplete one remains fenced until its exact key is revoked.
                raise

    def delete(self, op: dict[str, Any]) -> None:
        # Revocation/enrollment is confirmed by the caller. Remove even a torn
        # receipt, but validate the inode and permissions without following links.
        with self.directory() as folder:
            for name in (self.name(op), ".pending-" + self.name(op)):
                try:
                    _private_bytes(name, directory=folder)
                except FileNotFoundError:
                    continue
                os.unlink(name, dir_fd=folder)
            os.fsync(folder)


def receipt(
    guest: Any, op: dict[str, Any], *, fence: bool = False, unused: bool = False
) -> dict[str, Any]:
    result = guest.call(
        "enrollment-fence-unused"
        if unused
        else "enrollment-fence"
        if fence
        else "enrollment-status",
        enrollment=contract(op),
    )
    found = result.get("receipt")
    if found is not None and (
        not isinstance(found, dict)
        or found.get("contract") != contract(op)
        or found.get("phase") not in {"prepared", "started", "enrolled"}
        or type(found.get("fenced")) is not bool
        or not isinstance(found.get("node_id"), str)
    ):
        raise RuntimeError("Authenticated enrollment receipt differs from the owned operation")
    if not isinstance(result.get("guest"), dict):
        raise RuntimeError("Authenticated enrollment observation is missing")
    if (
        found
        and found["phase"] == "enrolled"
        and (
            not found["node_id"]
            or (result["guest"].get("node_id") and found["node_id"] != result["guest"]["node_id"])
        )
    ):
        raise RuntimeError("Enrolled guest receipt node identity changed")
    return result


def verify_node(observed: dict[str, Any], op: dict[str, Any], api: Any) -> str:
    node = observed.get("node_id")
    if (
        not node
        or observed.get("tailnet") != op["tailnet"]
        or set(observed.get("tags", [])) != set(op["tags"])
    ):
        raise RuntimeError("Enrollment node differs from the expected tailnet or tags")
    if api is not None:
        device = api.device(node)
        if node not in {device.get("nodeId"), device.get("id")} or set(
            device.get("tags", [])
        ) != set(op["tags"]):
            raise RuntimeError("Enrollment API identity differs from the authenticated guest")
    return str(node)


def resume(
    journal: Any,
    own: dict[str, Any],
    guest: Any,
    cfg: dict[str, Any],
    intent: dict[str, Any],
    api: Any,
    store: KeyStore,
) -> dict[str, Any]:
    """Inspect guest before every delivery; persist uncertainty before each external write."""
    journal.assert_held()
    op = own.get("enrollment")
    if op is None:
        current = guest.call("status")
        if current.get("node_id"):
            raise RuntimeError("Unowned Tailscale node found; automatic adoption is forbidden")
        op = {
            "schema": 2,
            "id": uuid.uuid4().hex,
            "binding": {k: own[k] for k in BINDING},
            "tailnet": cfg["tailnet"],
            "tags": sorted(cfg["tags"]),
            "key_id": "",
            "phase": "rejected",
            "mode": cfg.get("auth", {}).get("mode", "oauth"),
            "credential": api.credential_binding() if api is not None else {},
            "key_retired": False,
        }
        validate_operation(op)
        own["enrollment"] = op
        own["key_retired"] = False
        journal.save()
    validate_operation(op)
    if not op["key_retired"] and op["credential"]:
        if api is None:
            raise RuntimeError("Original OAuth credentials are required to resume enrollment")
        api.bind_credential(op["credential"])
    if (
        op["binding"] != {k: own[k] for k in BINDING}
        or op["tailnet"] != cfg["tailnet"]
        or op["tags"] != sorted(cfg["tags"])
        or op["mode"] != cfg.get("auth", {}).get("mode", "oauth")
    ):
        raise RuntimeError(
            "Enrollment operation binding changed; automatic re-enrollment is forbidden"
        )
    if op["phase"] == "rejected":
        if own.get("node_id") or guest.call("status").get("node_id"):
            raise RuntimeError("Unexpected node before key issuance")
        if op["mode"] == "auth-key":
            key_id = cfg.get("auth", {}).get("member_key_ids", {}).get(own["member"])
            if not key_id or op["key_id"] not in {"", key_id}:
                raise RuntimeError(
                    "Configure the exact administrator member_key_id before enrollment"
                )
            reference = cfg.get("auth", {}).get("member_key_refs", {}).get(own["member"])
            key = secret(reference or "", base=store.base)
            if api is not None:
                info = api.key_info(key_id)
                create = (info or {}).get("capabilities", {}).get("devices", {}).get("create", {})
                if (
                    not info
                    or create.get("reusable") is not False
                    or create.get("ephemeral") is not False
                    or set(create.get("tags", [])) != set(op["tags"])
                ):
                    raise RuntimeError(
                        "Administrator key is absent or its capabilities differ from the enrollment contract"
                    )
            op["key_id"] = key_id
        op["phase"] = "issuing"
        journal.save()  # A lost API response must never result in a second issuance.
        try:
            if op["mode"] == "oauth":
                if api is None:
                    op["phase"] = "rejected"
                    journal.save()
                    raise RuntimeError("Scoped OAuth credentials required for enrollment")
                journal.assert_held()
                key_id, key = api.key(op["id"])
        except ApiError as error:
            if error.status in {400, 401, 403, 404, 422}:
                op["phase"] = "rejected"
                journal.save()
            raise
        store.put(op, key_id, key)
        op.update(key_id=key_id, phase="key-ready")
        journal.save()
    if op["phase"] in {"fencing", "revoked", "retired"}:
        raise RuntimeError("Enrollment operation is fenced; use exact operation recovery")
    saved = store.read(op)
    if op["phase"] == "issuing":
        if saved is None:
            raise RuntimeError(
                "Enrollment key creation outcome is uncertain; no issuance retry is permitted. Use tailscale recover with the exact operation and a verified key ID"
            )
        op.update(key_id=saved["key_id"], phase="key-ready")
        journal.save()
    found = receipt(guest, op)
    remote, current = found["receipt"], found["guest"]
    if remote and remote["fenced"]:
        raise RuntimeError("Guest enrollment operation is fenced; recovery required")
    if current.get("node_id"):
        if not remote or remote["phase"] not in {"started", "enrolled"}:
            raise RuntimeError("Node has no matching enrollment receipt; adoption is forbidden")
        node = verify_node(current, op, api)
        if own.get("node_id") not in {None, node}:
            raise RuntimeError("Enrollment node identity differs from its recorded owner")
        # Guest finalizes its receipt/owner after a lost response, without reauthentication.
    elif op["phase"] == "enrolled" or (remote and remote["phase"] == "enrolled"):
        raise RuntimeError("Enrolled node disappeared; automatic re-enrollment is forbidden")
    elif saved is None and not remote:
        raise RuntimeError("Original enrollment key is unavailable; recover the unused operation")
    op["phase"] = "delivering"
    journal.save()
    journal.assert_held()
    result = guest.call(
        "reconcile",
        desired=intent,
        enrollment=contract(op),
        auth_key=saved["key"] if saved and not current.get("node_id") else "",
    )
    confirmed = receipt(guest, op)
    if result.get("node_id"):
        if not confirmed["receipt"] or confirmed["receipt"]["phase"] != "enrolled":
            raise RuntimeError("Guest enrollment receipt is not confirmed")
        node = verify_node(confirmed["guest"], op, api)
        if node != result["node_id"]:
            raise RuntimeError("Enrollment acknowledgement identity changed")
        if own.get("node_id") not in {None, node}:
            raise RuntimeError("Enrollment acknowledgement differs from its recorded owner")
        own["node_id"] = node
        op["phase"] = "enrolled"
        journal.save()
        if api is not None and not op["key_retired"]:
            api.revoke_key(op["key_id"])
            op["key_retired"] = True
            own["key_retired"] = True
            journal.save()
        store.delete(op)
    return result


def resolve_key_id(
    journal: Any, op: dict[str, Any], api: Any, store: KeyStore, *, key_id: str = ""
) -> None:
    if api is not None and op["credential"] and not op["key_retired"]:
        api.bind_credential(op["credential"])
    if key_id and op["key_id"] and key_id != op["key_id"]:
        raise RuntimeError("Recovery key ID differs from the recorded key")
    if op["key_id"] or op["phase"] in NO_OUTSTANDING_KEY_PHASES:
        return
    if key_id and api is not None:
        info = api.key_info(key_id)
        create = (info or {}).get("capabilities", {}).get("devices", {}).get("create", {})
        if (
            not info
            or info.get("description") != "cxcli enrollment " + op["id"]
            or create.get("reusable") is not False
            or create.get("ephemeral") is not False
            or set(create.get("tags", [])) != set(op["tags"])
        ):
            raise RuntimeError("Recovery key ID is not proven to belong to this operation")
        op["key_id"] = key_id
    else:
        saved = store.read(op)
        if saved:
            op["key_id"] = saved["key_id"]
        if not op["key_id"]:
            raise RuntimeError(
                "Owned key identity is unresolved; exact key-ID recovery or administrator assistance required"
            )
    journal.save()


def revoke(
    journal: Any, own: dict[str, Any], api: Any, store: KeyStore, *, key_id: str = ""
) -> None:
    op = own["enrollment"]
    if not op["key_retired"]:
        if op["phase"] not in NO_OUTSTANDING_KEY_PHASES:
            resolve_key_id(journal, op, api, store, key_id=key_id)
            if api is None:
                raise RuntimeError(
                    "Confirming owned key revocation requires scoped API credentials"
                )
            journal.assert_held()
            api.revoke_key(op["key_id"])
        own["key_retired"] = True
        op["key_retired"] = True
        journal.save()
    store.delete(op)


def recover_unused(
    journal: Any, own: dict[str, Any], guest: Any, api: Any, store: KeyStore, *, key_id: str = ""
) -> None:
    """The guest atomically rejects started operations before changing its fence."""
    op = own["enrollment"]
    journal.assert_held()
    # Reject a known started/enrolled operation without poisoning its resumability.
    found = receipt(guest, op)
    if found["guest"].get("node_id") or (
        found["receipt"] and found["receipt"]["phase"] != "prepared"
    ):
        raise RuntimeError(
            "Enrollment already started or produced a device; replacement is blocked. Resume the original operation or reconcile its device before retirement"
        )
    resolve_key_id(journal, op, api, store, key_id=key_id)
    journal.assert_held()
    found = receipt(guest, op, unused=True)
    if not found["receipt"] or not found["receipt"]["fenced"]:
        raise RuntimeError("Unused enrollment fence is unconfirmed")
    # Preserve durable absence/revocation proof across a retry before archiving.
    if op["phase"] not in NO_OUTSTANDING_KEY_PHASES:
        op["phase"] = "fencing"
    journal.save()
    revoke(journal, own, api, store, key_id=key_id)
    guest.call("enrollment-forget-key", enrollment=contract(op))
    op["phase"] = "revoked"
    journal.save()
    own.setdefault("prior_enrollments", []).append(op)
    own.pop("enrollment")
    own.pop("key_retired", None)
    journal.save()
