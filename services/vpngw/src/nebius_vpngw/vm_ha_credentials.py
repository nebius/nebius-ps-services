"""Fail-closed, secret-free identity proofs for VM-HA Nebius credentials."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VM_HA_IDENTITY_AUTH_TIMEOUT_SECONDS = 15.0
VM_HA_IDENTITY_REQUEST_TIMEOUT_SECONDS = 10.0
VM_HA_IDENTITY_PER_RETRY_TIMEOUT_SECONDS = 5.0
VM_HA_IDENTITY_RETRIES = 1
VM_HA_CREDENTIAL_MAX_BYTES = 1024 * 1024
VM_HA_CREDENTIAL_FILE_NAME = "nebius-credentials.json"
VM_HA_CREDENTIAL_DISPLAY_ROOT = "~/.config/nebius-vpngw/credentials"


def _credential_path_segment(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized or normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise VMHACredentialIdentityError(f"{label}-invalid")
    return normalized


def managed_vm_ha_credential_path(
    *, project_id: str, gateway_name: str, home: Path | None = None
) -> Path:
    """Return the absolute operator-owned credential path without creating it."""

    project = _credential_path_segment(project_id, label="project")
    gateway = _credential_path_segment(gateway_name, label="gateway")
    root = Path.home() if home is None else home
    if not root.is_absolute():
        raise VMHACredentialIdentityError("home-invalid")
    return (
        root
        / ".config"
        / "nebius-vpngw"
        / "credentials"
        / project
        / gateway
        / VM_HA_CREDENTIAL_FILE_NAME
    )


def display_vm_ha_credential_path(*, project_id: str, gateway_name: str) -> str:
    """Return the stable user-facing path with a literal home shorthand."""

    project = _credential_path_segment(project_id, label="project")
    gateway = _credential_path_segment(gateway_name, label="gateway")
    return f"{VM_HA_CREDENTIAL_DISPLAY_ROOT}/{project}/{gateway}/{VM_HA_CREDENTIAL_FILE_NAME}"


class VMHACredentialIdentityError(RuntimeError):
    """A closed, non-secret credential identity failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"VM-HA runtime credential identity is {reason}")


@dataclass(frozen=True)
class VMHACredentialIdentity:
    """One authenticated local credential source without credential bytes."""

    node_id: str
    source_path: Path
    credential_sha256: str
    service_account_id: str
    authorized_key_id: str
    project_id: str
    service_account_name: str

    def approval_record(self) -> dict[str, str]:
        return {
            "authorized_key_id": self.authorized_key_id,
            "credential_sha256": self.credential_sha256,
            "node_id": self.node_id,
            "project_id": self.project_id,
            "service_account_id": self.service_account_id,
        }


@dataclass(frozen=True)
class VMHACredentialSet:
    """The exact two-node runtime identity accepted by one apply."""

    nodes: tuple[VMHACredentialIdentity, VMHACredentialIdentity]

    def __post_init__(self) -> None:
        if len({node.node_id for node in self.nodes}) != 2:
            raise VMHACredentialIdentityError("incomplete")
        if self.nodes != tuple(sorted(self.nodes, key=lambda node: node.node_id)):
            raise VMHACredentialIdentityError("non-canonical")
        if len({node.service_account_id for node in self.nodes}) != 1:
            raise VMHACredentialIdentityError("service-account-mismatch")
        if len({node.authorized_key_id for node in self.nodes}) != 1:
            raise VMHACredentialIdentityError("authorized-key-mismatch")
        if len({node.project_id for node in self.nodes}) != 1:
            raise VMHACredentialIdentityError("project-mismatch")
        if len({node.source_path for node in self.nodes}) != 1:
            raise VMHACredentialIdentityError("source-path-mismatch")
        if len({node.credential_sha256 for node in self.nodes}) != 1:
            raise VMHACredentialIdentityError("credential-digest-mismatch")

    @property
    def service_account_id(self) -> str:
        return self.nodes[0].service_account_id

    @property
    def authorized_key_id(self) -> str:
        return self.nodes[0].authorized_key_id

    @property
    def project_id(self) -> str:
        return self.nodes[0].project_id

    def by_node(self) -> dict[str, VMHACredentialIdentity]:
        return {node.node_id: node for node in self.nodes}

    def approval_records(self) -> list[dict[str, str]]:
        return [node.approval_record() for node in self.nodes]

    def resource_bindings(self) -> dict[str, str]:
        bindings: dict[str, str] = {}
        for node in self.nodes:
            bindings[f"credential-service-account:{node.node_id}"] = node.service_account_id
            bindings[f"credential-authorized-key:{node.node_id}"] = node.authorized_key_id
            bindings[f"credential-sha256:{node.node_id}"] = node.credential_sha256
        return bindings


def installed_vm_ha_credential_path(
    *, node_id: str, generation_id: str, credential_sha256: str
) -> str:
    """Return the sole canonical installed path for one credential bundle."""

    if not node_id or not generation_id:
        raise VMHACredentialIdentityError("incomplete")
    if len(credential_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in credential_sha256
    ):
        raise VMHACredentialIdentityError("digest-invalid")
    return (
        f"/etc/nebius-vpngw/vm-ha-credentials/{generation_id}/{node_id}/"
        f"{credential_sha256}/nebius-credentials.json"
    )


def credential_ids_from_payload(payload: bytes) -> tuple[str, str]:
    """Parse only the public identifiers from one credentials-file payload."""

    try:
        value = json.loads(payload.decode("utf-8"))
        subject = value["subject-credentials"]
        service_account_id = subject["sub"]
        issuer = subject["iss"]
        authorized_key_id = subject["kid"]
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError):
        raise VMHACredentialIdentityError("malformed") from None
    if not all(
        isinstance(item, str) and item and len(item) <= 256 and not any(c.isspace() for c in item)
        for item in (service_account_id, issuer, authorized_key_id)
    ):
        raise VMHACredentialIdentityError("malformed")
    if issuer != service_account_id:
        raise VMHACredentialIdentityError("service-account-mismatch")
    return service_account_id, authorized_key_id


def _stable_private_read(path: Path, *, expected_uid: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            before = os.fstat(descriptor)
            if (
                not path.is_absolute()
                or not stat.S_ISREG(before.st_mode)
                or before.st_uid != expected_uid
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
            ):
                raise VMHACredentialIdentityError("file-unsafe")
            if before.st_size > VM_HA_CREDENTIAL_MAX_BYTES:
                raise VMHACredentialIdentityError("file-oversized")
            chunks: list[bytes] = []
            remaining = VM_HA_CREDENTIAL_MAX_BYTES + 1
            while remaining and (chunk := os.read(descriptor, min(remaining, 1024 * 1024))):
                chunks.append(chunk)
                remaining -= len(chunk)
            if remaining == 0:
                raise VMHACredentialIdentityError("file-oversized")
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = path.lstat()
    except VMHACredentialIdentityError:
        raise
    except OSError:
        raise VMHACredentialIdentityError("file-unavailable") from None

    def identity(item: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
        return (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_uid,
            item.st_nlink,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )

    if identity(before) != identity(after) or identity(after) != identity(current):
        raise VMHACredentialIdentityError("file-changed")
    payload = b"".join(chunks)
    if not payload:
        raise VMHACredentialIdentityError("malformed")
    return payload


def read_vm_ha_credential_source(
    path: str | Path,
    *,
    expected_uid: int | None = None,
) -> bytes:
    """Read one bounded, stable, current-user-owned private credential source."""

    uid = os.getuid() if expected_uid is None else expected_uid
    return _stable_private_read(Path(path), expected_uid=uid)


def _default_reader(path: Path) -> Any:
    from nebius.base.service_account.credentials_file import Reader

    return Reader(path).read()


def _default_sdk_factory(*, credentials_file_name: str) -> Any:
    from nebius.sdk import SDK

    return SDK(credentials_file_name=credentials_file_name)


def verify_sdk_service_account_profile(
    sdk: Any,
    *,
    expected_service_account_id: str,
    expected_project_id: str | None,
    expected_service_account_name: str | None = None,
) -> tuple[str, str, str]:
    """Force token renewal and return exact authenticated SA metadata."""

    try:
        from nebius.aio.token.renewable import (
            OPTION_RENEW_REQUEST_TIMEOUT,
            OPTION_RENEW_REQUIRED,
            OPTION_RENEW_SYNCHRONOUS,
        )

        response = sdk.whoami(
            auth_timeout=VM_HA_IDENTITY_AUTH_TIMEOUT_SECONDS,
            timeout=VM_HA_IDENTITY_REQUEST_TIMEOUT_SECONDS,
            retries=VM_HA_IDENTITY_RETRIES,
            per_retry_timeout=VM_HA_IDENTITY_PER_RETRY_TIMEOUT_SECONDS,
            auth_options={
                OPTION_RENEW_REQUIRED: "true",
                OPTION_RENEW_SYNCHRONOUS: "true",
                OPTION_RENEW_REQUEST_TIMEOUT: str(VM_HA_IDENTITY_REQUEST_TIMEOUT_SECONDS),
            },
        ).wait()
    except Exception:
        raise VMHACredentialIdentityError("authentication-failed") from None
    profile = getattr(response, "service_account_profile", None)
    info = getattr(profile, "info", None)
    metadata = getattr(info, "metadata", None)
    service_account_id = str(getattr(metadata, "id", "") or "")
    project_id = str(getattr(metadata, "parent_id", "") or "")
    service_account_name = str(getattr(metadata, "name", "") or "")
    if not service_account_id or not project_id or not service_account_name:
        raise VMHACredentialIdentityError("profile-not-service-account")
    if service_account_id != expected_service_account_id:
        raise VMHACredentialIdentityError("service-account-mismatch")
    if expected_project_id is not None and project_id != expected_project_id:
        raise VMHACredentialIdentityError("project-mismatch")
    if (
        expected_service_account_name is not None
        and service_account_name != expected_service_account_name
    ):
        raise VMHACredentialIdentityError("requested-service-account-mismatch")
    return service_account_id, project_id, service_account_name


def _preflight_one(
    *,
    node_id: str,
    path: Path,
    project_id: str,
    requested_service_account_name: str | None,
    expected_uid: int,
    reader_factory: Callable[[Path], Any],
    sdk_factory: Callable[..., Any],
) -> VMHACredentialIdentity:
    payload = read_vm_ha_credential_source(path, expected_uid=expected_uid)
    declared_service_account_id, declared_key_id = credential_ids_from_payload(payload)
    digest = hashlib.sha256(payload).hexdigest()
    try:
        parsed = reader_factory(path)
        if (
            str(getattr(parsed, "service_account_id", "") or "") != declared_service_account_id
            or str(getattr(parsed, "public_key_id", "") or "") != declared_key_id
        ):
            raise VMHACredentialIdentityError("credential-reader-mismatch")
    except VMHACredentialIdentityError:
        raise
    except Exception:
        raise VMHACredentialIdentityError("malformed") from None
    if (
        hashlib.sha256(read_vm_ha_credential_source(path, expected_uid=expected_uid)).hexdigest()
        != digest
    ):
        raise VMHACredentialIdentityError("file-changed")

    sdk: Any | None = None
    try:
        try:
            sdk = sdk_factory(credentials_file_name=str(path))
        except Exception:
            raise VMHACredentialIdentityError("authentication-failed") from None
        _service_account_id, authenticated_project_id, service_account_name = (
            verify_sdk_service_account_profile(
                sdk,
                expected_service_account_id=declared_service_account_id,
                expected_project_id=project_id,
                expected_service_account_name=requested_service_account_name,
            )
        )
    finally:
        if sdk is not None:
            close = getattr(sdk, "sync_close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    raise VMHACredentialIdentityError("sdk-close-failed") from None
    if (
        hashlib.sha256(read_vm_ha_credential_source(path, expected_uid=expected_uid)).hexdigest()
        != digest
    ):
        raise VMHACredentialIdentityError("file-changed")
    return VMHACredentialIdentity(
        node_id=node_id,
        source_path=path,
        credential_sha256=digest,
        service_account_id=declared_service_account_id,
        authorized_key_id=declared_key_id,
        project_id=authenticated_project_id,
        service_account_name=service_account_name,
    )


def preflight_vm_ha_credentials(
    node_ids: Iterable[str],
    *,
    source_path: str | Path,
    project_id: str,
    requested_service_account_name: str | None = None,
    expected_uid: int | None = None,
    reader_factory: Callable[[Path], Any] = _default_reader,
    sdk_factory: Callable[..., Any] = _default_sdk_factory,
) -> VMHACredentialSet:
    """Authenticate one managed source and project it onto both VM-HA nodes."""

    if not project_id:
        raise VMHACredentialIdentityError("project-unavailable")
    normalized = tuple(sorted(node_ids))
    if len(normalized) != 2 or len(set(normalized)) != 2:
        raise VMHACredentialIdentityError("incomplete")
    uid = os.getuid() if expected_uid is None else expected_uid
    path = Path(source_path)
    identity = _preflight_one(
        node_id=normalized[0],
        path=path,
        project_id=project_id,
        requested_service_account_name=requested_service_account_name,
        expected_uid=uid,
        reader_factory=reader_factory,
        sdk_factory=sdk_factory,
    )
    peer_identity = VMHACredentialIdentity(
        node_id=normalized[1],
        source_path=identity.source_path,
        credential_sha256=identity.credential_sha256,
        service_account_id=identity.service_account_id,
        authorized_key_id=identity.authorized_key_id,
        project_id=identity.project_id,
        service_account_name=identity.service_account_name,
    )
    return VMHACredentialSet(
        nodes=(identity, peer_identity),
    )


def credential_bindings_from_runtime(binding: Any) -> dict[str, str]:
    """Project one explicit runtime binding into lifecycle resource bindings."""

    service_account_id = str(getattr(binding, "nebius_service_account_id", "") or "")
    authorized_key_id = str(getattr(binding, "nebius_authorized_key_id", "") or "")
    result: dict[str, str] = {}
    for node in getattr(binding, "nodes", ()):
        digest = str(getattr(node, "nebius_credentials_sha256", "") or "")
        node_id = str(getattr(node, "node_id", "") or "")
        if not all((service_account_id, authorized_key_id, digest, node_id)):
            raise VMHACredentialIdentityError("binding-incomplete")
        result[f"credential-service-account:{node_id}"] = service_account_id
        result[f"credential-authorized-key:{node_id}"] = authorized_key_id
        result[f"credential-sha256:{node_id}"] = digest
    if len(result) != 6:
        raise VMHACredentialIdentityError("binding-incomplete")
    return result


def credential_resource_binding_subset(
    bindings: Mapping[str, str],
) -> dict[str, str]:
    return {
        key: value
        for key, value in bindings.items()
        if key.startswith(
            (
                "credential-service-account:",
                "credential-authorized-key:",
                "credential-sha256:",
            )
        )
    }
