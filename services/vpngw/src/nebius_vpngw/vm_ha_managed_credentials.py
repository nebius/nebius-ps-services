"""Crash-resumable operator credential enrollment for one VM-HA gateway."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import secrets
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .nebius_pagination import collect_nebius_pages, nebius_resource_id
from .vm_ha_credentials import (
    VMHACredentialIdentityError,
    VMHACredentialSet,
    display_vm_ha_credential_path,
    managed_vm_ha_credential_path,
    preflight_vm_ha_credentials,
    read_vm_ha_credential_source,
)
from .vpngw_sa import (
    ServiceAccountTokenIdentity,
    VMHAIAMReconciliationPlan,
    _close_client,
    _init_client,
    _wait_operation,
    ensure_vm_ha_service_account_identity_and_token,
    inspect_vm_ha_service_account_identity,
)

_KEY_BITS = 4096
_KEY_ALGORITHM = "RS256"
_KEY_DESCRIPTION = "Nebius VPNGW VM-HA runtime key"
_MANAGED_BY = "nebius-vpngw"
_PENDING_KEY_NAME = ".private-key.pending.pem"
_JOURNAL_NAME = ".enrollment.json"
_JOURNAL_VERSION = 1
_IAM_PAGE_SIZE = 1000
_AT_FDCWD = -100
_LINUX_RENAME_NOREPLACE = 1
_DARWIN_RENAME_EXCL = 0x00000004


class VMHAManagedCredentialError(RuntimeError):
    """A secret-free managed credential reconciliation failure."""


def _bounded_iam_name(gateway_name: str, suffix: str) -> str:
    candidate = f"{gateway_name}{suffix}"
    if len(candidate) <= 63:
        return candidate
    digest = hashlib.sha256(gateway_name.encode("utf-8")).hexdigest()[:10]
    prefix_length = 63 - len(suffix) - len(digest) - 1
    prefix = gateway_name[:prefix_length].rstrip("-")
    if not prefix:
        raise VMHAManagedCredentialError("gateway name cannot form a managed IAM name")
    return f"{prefix}-{digest}{suffix}"


def vm_ha_service_account_name(gateway_name: str) -> str:
    return _bounded_iam_name(gateway_name, "-vm-ha")


def vm_ha_authorized_key_name(gateway_name: str) -> str:
    return _bounded_iam_name(gateway_name, "-vm-ha-runtime-key")


def vm_ha_ownership_labels(*, project_id: str, gateway_name: str) -> dict[str, str]:
    owner = hashlib.sha256(f"{project_id}\0{gateway_name}".encode()).hexdigest()[:32]
    return {"managed-by": _MANAGED_BY, "vm-ha-owner": owner}


@dataclass(frozen=True)
class VMHAManagedCredentialPlan:
    """Secret-free read-only result included in the exact apply approval."""

    action: Literal["create", "reuse", "resume"]
    project_id: str
    gateway_name: str
    service_account_name: str
    authorized_key_name: str
    display_path: str
    home_root: Path
    source_path: Path
    iam_plan: VMHAIAMReconciliationPlan
    authorized_key_action: Literal["create", "reuse"]
    authorized_key_id: str | None
    source_present: bool
    pending_present: bool
    journal_present: bool
    public_key_sha256: str | None
    credentials: VMHACredentialSet | None = None

    def approval_record(self) -> dict[str, object]:
        result: dict[str, object] = {
            "action": self.action,
            "algorithm": _KEY_ALGORITHM,
            "authorized_key_name": self.authorized_key_name,
            "destination": self.display_path,
            "expires_at": None,
            "key_size_bits": _KEY_BITS,
            "local_source_expected_absent": not self.source_present,
            "project_id": self.project_id,
            "service_account_name": self.service_account_name,
            "iam_resources": {
                **self.iam_plan.approval_record(),
                "authorized_key": {
                    "action": self.authorized_key_action,
                    "id": self.authorized_key_id,
                },
            },
        }
        if self.credentials is not None:
            result["runtime_credentials"] = self.credentials.approval_records()
        return result


@dataclass(frozen=True)
class VMHAManagedCredentialResult:
    credentials: VMHACredentialSet
    token_identity: ServiceAccountTokenIdentity


@dataclass(frozen=True)
class _LocalCredentialState:
    mode: Literal["create", "reuse", "resume"]
    source_present: bool
    pending_present: bool
    journal_present: bool
    public_key: str | None
    public_key_sha256: str | None
    credentials: VMHACredentialSet | None
    journal: dict[str, object] | None


def inspect_managed_vm_ha_credentials(
    *,
    project_id: str,
    gateway_name: str,
    node_ids: Sequence[str],
    tenant_id: str | None = None,
    region_id: str | None = None,
    home: Path | None = None,
    preflight: Callable[..., VMHACredentialSet] = preflight_vm_ha_credentials,
    iam_inspector: Callable[..., VMHAIAMReconciliationPlan] = (
        inspect_vm_ha_service_account_identity
    ),
    client_factory: Callable[..., tuple[Any, bool]] = _init_client,
) -> VMHAManagedCredentialPlan:
    """Inspect exact local and IAM state without creating either."""

    home_root = Path.home() if home is None else home
    source_path = managed_vm_ha_credential_path(
        project_id=project_id,
        gateway_name=gateway_name,
        home=home_root,
    )
    sa_name = vm_ha_service_account_name(gateway_name)
    key_name = vm_ha_authorized_key_name(gateway_name)
    try:
        local_state = _inspect_local_credential_state(
            source_path=source_path,
            home=home_root,
            project_id=project_id,
            gateway_name=gateway_name,
            service_account_name=sa_name,
            authorized_key_name=key_name,
            node_ids=node_ids,
            preflight=preflight,
        )
        expected_service_account_id = (
            local_state.credentials.service_account_id
            if local_state.credentials is not None
            else _journal_identifier(local_state.journal, "service_account_id")
        )
        labels = vm_ha_ownership_labels(
            project_id=project_id,
            gateway_name=gateway_name,
        )
        cloud_mode: Literal["create", "reuse", "resume"] = (
            "reuse" if local_state.source_present else local_state.mode
        )
        iam_plan = iam_inspector(
            sa_name,
            tenant_id,
            project_id,
            region_id,
            mode=cloud_mode,
            expected_service_account_id=expected_service_account_id,
            ownership_labels=labels,
            client_factory=client_factory,
        )
        expected_key_id = (
            local_state.credentials.authorized_key_id
            if local_state.credentials is not None
            else _journal_identifier(local_state.journal, "authorized_key_id")
        )
        key_action, key_id = _inspect_authorized_key(
            mode=cloud_mode,
            project_id=project_id,
            service_account_id=iam_plan.service_account_id,
            key_name=key_name,
            labels=labels,
            public_key=local_state.public_key,
            expected_key_id=expected_key_id,
            tenant_id=tenant_id,
            region_id=region_id,
            client_factory=client_factory,
        )
    except (VMHACredentialIdentityError, VMHAManagedCredentialError):
        raise
    except Exception as error:
        raise VMHAManagedCredentialError("managed credential read-only inventory failed") from error
    return VMHAManagedCredentialPlan(
        action=local_state.mode,
        project_id=project_id,
        gateway_name=gateway_name,
        service_account_name=sa_name,
        authorized_key_name=key_name,
        display_path=display_vm_ha_credential_path(
            project_id=project_id,
            gateway_name=gateway_name,
        ),
        home_root=home_root,
        source_path=source_path,
        iam_plan=iam_plan,
        authorized_key_action=key_action,
        authorized_key_id=key_id,
        source_present=local_state.source_present,
        pending_present=local_state.pending_present,
        journal_present=local_state.journal_present,
        public_key_sha256=local_state.public_key_sha256,
        credentials=local_state.credentials,
    )


def _require_private_directory(path: Path, *, expected_uid: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise VMHAManagedCredentialError("managed credential directory is unavailable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_nlink < 1
    ):
        raise VMHAManagedCredentialError(
            "managed credential directories must be owner-only real directories"
        )


def _validate_private_parent(path: Path, *, home: Path, expected_uid: int) -> None:
    try:
        path.relative_to(home)
        relative_parent = path.parent.relative_to(home / ".config")
    except ValueError as error:
        raise VMHAManagedCredentialError(
            "managed credential path is outside the operator configuration directory"
        ) from error
    config = home / ".config"
    try:
        config_metadata = config.lstat()
    except OSError as error:
        raise VMHAManagedCredentialError(
            "operator configuration directory is unavailable"
        ) from error
    if not stat.S_ISDIR(config_metadata.st_mode) or config_metadata.st_uid != expected_uid:
        raise VMHAManagedCredentialError("operator configuration directory is unsafe")
    current = config
    for part in relative_parent.parts:
        current = current / part
        _require_private_directory(current, expected_uid=expected_uid)


def _ensure_private_parent(path: Path, *, home: Path, expected_uid: int) -> None:
    try:
        path.relative_to(home)
    except ValueError as error:
        raise VMHAManagedCredentialError(
            "managed credential path is outside the operator home"
        ) from error
    config = home / ".config"
    try:
        config_created = False
        if not config.exists():
            config.mkdir(mode=0o700)
            config_created = True
        if config_created:
            os.chmod(config, 0o700)
        config_metadata = config.lstat()
    except OSError as error:
        raise VMHAManagedCredentialError(
            "operator configuration directory is unavailable"
        ) from error
    if not stat.S_ISDIR(config_metadata.st_mode) or config_metadata.st_uid != expected_uid:
        raise VMHAManagedCredentialError("operator configuration directory is unsafe")
    current = config
    for part in path.parent.relative_to(config).parts:
        current = current / part
        current_created = False
        try:
            current.mkdir(mode=0o700)
            current_created = True
        except FileExistsError:
            pass
        except OSError as error:
            raise VMHAManagedCredentialError(
                "managed credential directory could not be created"
            ) from error
        if current_created:
            try:
                os.chmod(current, 0o700)
            except OSError as error:
                raise VMHAManagedCredentialError(
                    "managed credential directory could not be protected"
                ) from error
        _require_private_directory(current, expected_uid=expected_uid)
    _validate_private_parent(path, home=home, expected_uid=expected_uid)


def _atomic_rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename source only when destination does not exist."""

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "exclusive rename is unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(source_bytes, destination_bytes, _DARWIN_RENAME_EXCL)
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "exclusive rename is unavailable")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(
            _AT_FDCWD,
            source_bytes,
            _AT_FDCWD,
            destination_bytes,
            _LINUX_RENAME_NOREPLACE,
        )
    else:
        raise OSError(errno.ENOSYS, "exclusive rename is unavailable")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, "destination already exists")
    raise OSError(error_number, "exclusive rename failed")


def _atomic_private_write(path: Path, payload: bytes, *, replace: bool) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if replace:
            os.replace(temporary, path)
        else:
            try:
                _atomic_rename_noreplace(temporary, path)
            except FileExistsError:
                raise VMHAManagedCredentialError(
                    "managed credential publication would overwrite state"
                ) from None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except VMHAManagedCredentialError:
        raise
    except OSError as error:
        raise VMHAManagedCredentialError(
            "managed credential private state update failed"
        ) from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(FileNotFoundError):
            try:
                temporary.unlink()
            except OSError:
                pass


def _private_key_material(path: Path) -> tuple[bytes, str, str]:
    try:
        payload = read_vm_ha_credential_source(path)
        private_key = serialization.load_pem_private_key(payload, password=None)
    except (TypeError, ValueError, VMHACredentialIdentityError) as error:
        raise VMHAManagedCredentialError("pending private key is invalid") from error
    if not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size != _KEY_BITS:
        raise VMHAManagedCredentialError("pending private key is not the required RSA-4096 key")
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fingerprint = hashlib.sha256(public_bytes).hexdigest()
    return payload, public_bytes.decode("ascii"), fingerprint


def _load_or_create_pending_key(path: Path) -> tuple[bytes, str, str]:
    try:
        path.lstat()
    except FileNotFoundError:
        try:
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=_KEY_BITS)
            payload = private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            _atomic_private_write(path, payload, replace=False)
        except VMHAManagedCredentialError:
            raise
        except Exception as error:
            raise VMHAManagedCredentialError("pending private key generation failed") from error
    except OSError as error:
        raise VMHAManagedCredentialError("pending private key is unavailable") from error
    return _private_key_material(path)


def _read_journal(path: Path) -> dict[str, object] | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise VMHAManagedCredentialError(
            "managed credential enrollment journal is unavailable"
        ) from error
    try:
        value = json.loads(read_vm_ha_credential_source(path).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, VMHACredentialIdentityError) as error:
        raise VMHAManagedCredentialError(
            "managed credential enrollment journal is invalid"
        ) from error
    if not isinstance(value, dict) or value.get("version") != _JOURNAL_VERSION:
        raise VMHAManagedCredentialError("managed credential enrollment journal is invalid")
    return value


def _write_journal(path: Path, value: Mapping[str, object]) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _atomic_private_write(path, payload, replace=True)


def _protobuf_timestamp_is_configured(value: Any, field: str) -> bool:
    """Treat the provider's present zero Timestamp as an omitted optional value."""

    raw = getattr(value, "__pb2_message__", value)
    has_field = getattr(raw, "HasField", None)
    if callable(has_field):
        try:
            if not has_field(field):
                return False
            timestamp = getattr(raw, field, None)
            seconds = getattr(timestamp, "seconds", None)
            nanos = getattr(timestamp, "nanos", None)
            if isinstance(seconds, int) and isinstance(nanos, int):
                return bool(seconds or nanos)
            return True
        except (TypeError, ValueError):
            pass
    return getattr(value, field, None) is not None


def _key_identity(item: Any) -> tuple[str, str, str, dict[str, str], str, str, str, bool]:
    metadata = getattr(item, "metadata", None)
    spec = getattr(item, "spec", None)
    account = getattr(getattr(spec, "account", None), "service_account", None)
    return (
        str(getattr(metadata, "id", "") or ""),
        str(getattr(metadata, "parent_id", "") or ""),
        str(getattr(metadata, "name", "") or ""),
        dict(getattr(metadata, "labels", {}) or {}),
        str(getattr(account, "id", "") or ""),
        str(getattr(spec, "data", "") or "").strip(),
        str(getattr(spec, "description", "") or ""),
        _protobuf_timestamp_is_configured(spec, "expires_at"),
    )


def _list_service_account_keys(service: Any, service_account_id: str) -> tuple[Any, ...]:
    from nebius.api.nebius.iam.v1 import Account, ListAuthPublicKeyByAccountRequest

    account = Account(service_account=Account.ServiceAccount(id=service_account_id))
    return collect_nebius_pages(
        lambda page_token: service.list_by_account(
            ListAuthPublicKeyByAccountRequest(
                account=account,
                page_size=_IAM_PAGE_SIZE,
                page_token=page_token,
            )
        ),
        context="Authorized key",
        item_identity=nebius_resource_id,
    )


def _require_exact_key(
    item: Any,
    *,
    project_id: str,
    service_account_id: str,
    key_name: str,
    labels: Mapping[str, str],
    public_key: str,
    expected_key_id: str | None = None,
) -> str:
    key_id, parent_id, name, actual_labels, account_id, data, description, has_expiry = (
        _key_identity(item)
    )
    if (
        not key_id
        or (expected_key_id is not None and key_id != expected_key_id)
        or parent_id != project_id
        or name != key_name
        or actual_labels != dict(labels)
        or account_id != service_account_id
        or data != public_key.strip()
        or description != _KEY_DESCRIPTION
        or has_expiry
    ):
        raise VMHAManagedCredentialError("managed authorized key has foreign identity or data")
    return key_id


def _reconcile_authorized_key(
    service: Any,
    *,
    action: Literal["create", "reuse"],
    project_id: str,
    service_account_id: str,
    key_name: str,
    labels: Mapping[str, str],
    public_key: str,
    expected_key_id: str | None,
) -> str:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import (
        Account,
        AuthPublicKeySpec,
        CreateAuthPublicKeyRequest,
    )

    items = _list_service_account_keys(service, service_account_id)
    if len(items) > 1:
        raise VMHAManagedCredentialError("managed service account has unexpected authorized keys")
    if action == "reuse":
        if len(items) != 1:
            raise VMHAManagedCredentialError("managed authorized key disappeared after approval")
        return _require_exact_key(
            items[0],
            project_id=project_id,
            service_account_id=service_account_id,
            key_name=key_name,
            labels=labels,
            public_key=public_key,
            expected_key_id=expected_key_id,
        )
    if items:
        raise VMHAManagedCredentialError("managed authorized key appeared after approval")
    account = Account(service_account=Account.ServiceAccount(id=service_account_id))
    try:
        _wait_operation(
            service.create(
                CreateAuthPublicKeyRequest(
                    metadata=ResourceMetadata(
                        parent_id=project_id,
                        name=key_name,
                        labels=dict(labels),
                    ),
                    spec=AuthPublicKeySpec(
                        account=account,
                        description=_KEY_DESCRIPTION,
                        data=public_key,
                    ),
                )
            )
        )
    except Exception as error:
        raise VMHAManagedCredentialError("managed authorized-key creation failed") from error
    reread = _list_service_account_keys(service, service_account_id)
    if len(reread) != 1:
        raise VMHAManagedCredentialError("managed authorized key did not reread exactly")
    return _require_exact_key(
        reread[0],
        project_id=project_id,
        service_account_id=service_account_id,
        key_name=key_name,
        labels=labels,
        public_key=public_key,
        expected_key_id=None,
    )


def _public_key_from_credentials(path: Path) -> str:
    try:
        return _public_key_from_credential_payload(read_vm_ha_credential_source(path))
    except VMHAManagedCredentialError:
        raise
    except Exception as error:
        raise VMHAManagedCredentialError("managed credential private key is invalid") from error


def _public_key_from_credential_payload(payload: bytes) -> str:
    try:
        value = json.loads(payload.decode("utf-8"))
        private_pem = value["subject-credentials"]["private-key"].encode("utf-8")
        private_key = serialization.load_pem_private_key(private_pem, password=None)
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise VMHAManagedCredentialError("managed credential private key is invalid") from error
    if not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size != _KEY_BITS:
        raise VMHAManagedCredentialError("managed credential private key is not RSA-4096")
    return (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


def _journal_identifier(journal: Mapping[str, object] | None, key: str) -> str | None:
    if journal is None or key not in journal:
        return None
    value = journal.get(key)
    if not isinstance(value, str) or not value or len(value) > 256:
        raise VMHAManagedCredentialError("managed credential enrollment journal is invalid")
    return value


def _validate_journal(
    journal: Mapping[str, object],
    *,
    project_id: str,
    gateway_name: str,
    service_account_name: str,
    authorized_key_name: str,
    public_key_sha256: str,
    credentials: VMHACredentialSet | None,
) -> None:
    base = {
        "authorized_key_name",
        "gateway_name",
        "project_id",
        "public_key_sha256",
        "service_account_name",
        "version",
    }
    identities = {"authorized_key_id", "service_account_id"}
    if set(journal) not in {frozenset(base), frozenset(base | identities)}:
        raise VMHAManagedCredentialError("managed credential enrollment journal is invalid")
    expected = {
        "authorized_key_name": authorized_key_name,
        "gateway_name": gateway_name,
        "project_id": project_id,
        "public_key_sha256": public_key_sha256,
        "service_account_name": service_account_name,
        "version": _JOURNAL_VERSION,
    }
    if any(journal.get(key) != value for key, value in expected.items()):
        raise VMHAManagedCredentialError("managed credential enrollment journal changed")
    service_account_id = _journal_identifier(journal, "service_account_id")
    authorized_key_id = _journal_identifier(journal, "authorized_key_id")
    if (service_account_id is None) != (authorized_key_id is None):
        raise VMHAManagedCredentialError("managed credential enrollment journal is invalid")
    if credentials is not None and service_account_id is not None:
        if (
            service_account_id != credentials.service_account_id
            or authorized_key_id != credentials.authorized_key_id
        ):
            raise VMHAManagedCredentialError("managed credential enrollment identities changed")


def _path_present(path: Path, *, label: str) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise VMHAManagedCredentialError(f"{label} is unavailable") from error
    return True


def _inspect_local_credential_state(
    *,
    source_path: Path,
    home: Path,
    project_id: str,
    gateway_name: str,
    service_account_name: str,
    authorized_key_name: str,
    node_ids: Sequence[str],
    preflight: Callable[..., VMHACredentialSet],
) -> _LocalCredentialState:
    source_present = _path_present(source_path, label="managed credential source")
    parent_present = _path_present(source_path.parent, label="managed credential directory")
    if not source_present and not parent_present:
        return _LocalCredentialState(
            mode="create",
            source_present=False,
            pending_present=False,
            journal_present=False,
            public_key=None,
            public_key_sha256=None,
            credentials=None,
            journal=None,
        )
    if not parent_present:
        raise VMHAManagedCredentialError("managed credential directory is unavailable")
    _validate_private_parent(source_path, home=home, expected_uid=os.getuid())

    pending_path = source_path.parent / _PENDING_KEY_NAME
    journal_path = source_path.parent / _JOURNAL_NAME
    pending_present = _path_present(pending_path, label="pending private key")
    journal_present = _path_present(journal_path, label="enrollment journal")
    journal = _read_journal(journal_path) if journal_present else None

    credentials: VMHACredentialSet | None = None
    public_key: str | None = None
    if source_present:
        credentials = preflight(
            node_ids,
            source_path=source_path,
            project_id=project_id,
            requested_service_account_name=service_account_name,
        )
        public_key = _public_key_from_credentials(source_path)
    if pending_present:
        _pending_payload, pending_public_key, _pending_fingerprint = _private_key_material(
            pending_path
        )
        if public_key is not None and pending_public_key.strip() != public_key.strip():
            raise VMHAManagedCredentialError(
                "pending private key does not match the published credential"
            )
        public_key = pending_public_key

    if not source_present and pending_present != journal_present:
        raise VMHAManagedCredentialError("managed credential enrollment state is incomplete")
    if not source_present and not pending_present:
        return _LocalCredentialState(
            mode="create",
            source_present=False,
            pending_present=False,
            journal_present=False,
            public_key=None,
            public_key_sha256=None,
            credentials=None,
            journal=None,
        )
    if public_key is None:
        raise VMHAManagedCredentialError("managed credential key material is unavailable")
    public_key_sha256 = hashlib.sha256(public_key.encode("ascii")).hexdigest()
    if journal is not None:
        _validate_journal(
            journal,
            project_id=project_id,
            gateway_name=gateway_name,
            service_account_name=service_account_name,
            authorized_key_name=authorized_key_name,
            public_key_sha256=public_key_sha256,
            credentials=credentials,
        )
    return _LocalCredentialState(
        mode="resume" if pending_present or journal_present else "reuse",
        source_present=source_present,
        pending_present=pending_present,
        journal_present=journal_present,
        public_key=public_key,
        public_key_sha256=public_key_sha256,
        credentials=credentials,
        journal=journal,
    )


def _inspect_authorized_key(
    *,
    mode: Literal["create", "reuse", "resume"],
    project_id: str,
    service_account_id: str | None,
    key_name: str,
    labels: Mapping[str, str],
    public_key: str | None,
    expected_key_id: str | None,
    tenant_id: str | None,
    region_id: str | None,
    client_factory: Callable[..., tuple[Any, bool]],
) -> tuple[Literal["create", "reuse"], str | None]:
    if service_account_id is None:
        if expected_key_id is not None or mode == "reuse":
            raise VMHAManagedCredentialError("managed authorized-key identity is missing")
        return "create", None
    if public_key is None:
        raise VMHAManagedCredentialError("managed authorized-key material is unavailable")
    client: Any | None = None
    try:
        client, _ = client_factory(tenant_id, project_id, region_id)
        from nebius.api.nebius.iam.v1 import AuthPublicKeyServiceClient

        items = _list_service_account_keys(AuthPublicKeyServiceClient(client), service_account_id)
    except VMHAManagedCredentialError:
        raise
    except Exception as error:
        raise VMHAManagedCredentialError("managed authorized-key inventory failed") from error
    finally:
        if client is not None:
            _close_client(client)
    if len(items) > 1:
        raise VMHAManagedCredentialError("managed service account has unexpected authorized keys")
    if not items:
        if expected_key_id is not None or mode == "reuse":
            raise VMHAManagedCredentialError("managed authorized key is missing")
        return "create", None
    if mode == "create":
        raise VMHAManagedCredentialError(
            "managed authorized key exists without recoverable enrollment state"
        )
    key_id = _require_exact_key(
        items[0],
        project_id=project_id,
        service_account_id=service_account_id,
        key_name=key_name,
        labels=labels,
        public_key=public_key,
        expected_key_id=expected_key_id,
    )
    return "reuse", key_id


def _require_approved_local_state(
    plan: VMHAManagedCredentialPlan,
    current: _LocalCredentialState,
) -> None:
    approved = (
        plan.action,
        plan.source_present,
        plan.pending_present,
        plan.journal_present,
        plan.public_key_sha256,
        plan.credentials,
    )
    observed = (
        current.mode,
        current.source_present,
        current.pending_present,
        current.journal_present,
        current.public_key_sha256,
        current.credentials,
    )
    if observed != approved:
        raise VMHAManagedCredentialError("managed credential local state changed after approval")


def _cleanup_enrollment_state(*, pending_path: Path, journal_path: Path) -> None:
    try:
        for path in (pending_path, journal_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        directory = os.open(pending_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as error:
        raise VMHAManagedCredentialError("managed credential enrollment cleanup failed") from error


def ensure_managed_vm_ha_credentials(
    plan: VMHAManagedCredentialPlan,
    *,
    node_ids: Sequence[str],
    tenant_id: str | None,
    region_id: str | None,
    identity_ensurer: Callable[..., ServiceAccountTokenIdentity] = (
        ensure_vm_ha_service_account_identity_and_token
    ),
    client_factory: Callable[..., tuple[Any, bool]] = _init_client,
    preflight: Callable[..., VMHACredentialSet] = preflight_vm_ha_credentials,
) -> VMHAManagedCredentialResult:
    """Reconcile the approved identity and return one authenticated shared source."""

    labels = vm_ha_ownership_labels(
        project_id=plan.project_id,
        gateway_name=plan.gateway_name,
    )
    if tuple(sorted(labels.items())) != plan.iam_plan.ownership_labels:
        raise VMHAManagedCredentialError("managed credential IAM ownership plan changed")
    uid = os.getuid()
    current = _inspect_local_credential_state(
        source_path=plan.source_path,
        home=plan.home_root,
        project_id=plan.project_id,
        gateway_name=plan.gateway_name,
        service_account_name=plan.service_account_name,
        authorized_key_name=plan.authorized_key_name,
        node_ids=node_ids,
        preflight=preflight,
    )
    _require_approved_local_state(plan, current)

    pending_path = plan.source_path.parent / _PENDING_KEY_NAME
    journal_path = plan.source_path.parent / _JOURNAL_NAME
    journal = current.journal
    private_pem = b""
    if current.source_present:
        assert current.credentials is not None
        credential_payload = read_vm_ha_credential_source(plan.source_path)
        if (
            hashlib.sha256(credential_payload).hexdigest()
            != current.credentials.nodes[0].credential_sha256
        ):
            raise VMHAManagedCredentialError(
                "managed credential source changed after authentication"
            )
        public_key = _public_key_from_credential_payload(credential_payload)
    else:
        _ensure_private_parent(plan.source_path, home=plan.home_root, expected_uid=uid)
        if _path_present(plan.source_path, label="managed credential source"):
            raise VMHAManagedCredentialError("managed credential source appeared after approval")
        private_pem, public_key, fingerprint = _load_or_create_pending_key(pending_path)
        expected_journal: dict[str, object] = {
            "authorized_key_name": plan.authorized_key_name,
            "gateway_name": plan.gateway_name,
            "project_id": plan.project_id,
            "public_key_sha256": fingerprint,
            "service_account_name": plan.service_account_name,
            "version": _JOURNAL_VERSION,
        }
        journal = _read_journal(journal_path)
        if journal is None:
            journal = dict(expected_journal)
            _write_journal(journal_path, journal)
        else:
            _validate_journal(
                journal,
                project_id=plan.project_id,
                gateway_name=plan.gateway_name,
                service_account_name=plan.service_account_name,
                authorized_key_name=plan.authorized_key_name,
                public_key_sha256=fingerprint,
                credentials=None,
            )

    try:
        token_identity = identity_ensurer(
            plan.service_account_name,
            tenant_id,
            plan.project_id,
            region_id,
            verified_role_ids=("editor",),
            ownership_labels=labels,
            reconciliation_plan=plan.iam_plan,
        )
    except VMHAManagedCredentialError:
        raise
    except Exception as error:
        raise VMHAManagedCredentialError("managed service-account reconciliation failed") from error
    if (
        plan.credentials is not None
        and token_identity.service_account_id != plan.credentials.service_account_id
    ):
        raise VMHAManagedCredentialError("managed service-account identity changed after approval")

    client: Any | None = None
    try:
        client, _ = client_factory(tenant_id, plan.project_id, region_id)
        from nebius.api.nebius.iam.v1 import AuthPublicKeyServiceClient

        key_id = _reconcile_authorized_key(
            AuthPublicKeyServiceClient(client),
            action=plan.authorized_key_action,
            project_id=plan.project_id,
            service_account_id=token_identity.service_account_id,
            key_name=plan.authorized_key_name,
            labels=labels,
            public_key=public_key,
            expected_key_id=plan.authorized_key_id,
        )
    except VMHAManagedCredentialError:
        raise
    except Exception as error:
        raise VMHAManagedCredentialError("managed authorized-key reconciliation failed") from error
    finally:
        if client is not None:
            _close_client(client)

    if plan.credentials is not None and key_id != plan.credentials.authorized_key_id:
        raise VMHAManagedCredentialError("managed authorized-key identity changed after approval")

    if not current.source_present:
        assert journal is not None
        journal = {
            **journal,
            "authorized_key_id": key_id,
            "service_account_id": token_identity.service_account_id,
        }
        _write_journal(journal_path, journal)
        credential_payload = json.dumps(
            {
                "subject-credentials": {
                    "alg": _KEY_ALGORITHM,
                    "iss": token_identity.service_account_id,
                    "kid": key_id,
                    "private-key": private_pem.decode("ascii"),
                    "sub": token_identity.service_account_id,
                    "type": "JWT",
                }
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _atomic_private_write(plan.source_path, credential_payload, replace=False)

    credentials = preflight(
        node_ids,
        source_path=plan.source_path,
        project_id=plan.project_id,
        requested_service_account_name=plan.service_account_name,
    )
    if credentials.service_account_id != token_identity.service_account_id:
        raise VMHAManagedCredentialError("published credential service account did not verify")
    if credentials.authorized_key_id != key_id:
        raise VMHAManagedCredentialError("published credential authorized key did not verify")
    if current.pending_present or current.journal_present or not current.source_present:
        _cleanup_enrollment_state(
            pending_path=pending_path,
            journal_path=journal_path,
        )
    return VMHAManagedCredentialResult(
        credentials=credentials,
        token_identity=token_identity,
    )
