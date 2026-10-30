"""Single-writer OAuth import into an explicitly linked empty container via SDK."""

import fcntl
import hashlib
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .secretstash import decode_payload, sdk_context
from .tailscale_cli_io import protected_json
from .tailscale_config import secret


def import_pair(
    ref: dict[str, Any],
    *,
    client_id: str,
    secret_ref: str,
    base: Path,
    validate_pair: Callable[[str, str], None],
    operation_id: str = "",
) -> str:
    """Return an immutable version reference; never overwrite a shared primary."""
    from nebius.api.nebius.common.v1 import GetOperationRequest, ResourceMetadata
    from nebius.api.nebius.mysterybox.v1 import (
        CreateSecretVersionRequest,
        GetPayloadRequest,
        GetSecretRequest,
        GetSecretVersionRequest,
        Payload,
        PayloadServiceClient,
        SecretServiceClient,
        SecretVersionServiceClient,
        SecretVersionSpec,
    )

    from .nebius_api_helpers import wait_nebius_operation

    if not ref.get("component_instance") or not ref.get("secret_name") or not ref.get("secret_id"):
        raise ValueError(
            "Secure import requires a linked container from completed infrastructure setup"
        )
    folder = base / ".nebius-cxcli" / "tailscale-import"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if folder.is_symlink() or folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077:
        raise RuntimeError("OAuth import directory must be owner-only")
    name = hashlib.sha256(ref["secret_id"].encode()).hexdigest()
    target = folder / (name + ".json")
    fd = os.open(folder / (name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if target.is_symlink():
            raise RuntimeError("Refusing a linked OAuth import journal")
        if target.exists() and (
            target.stat().st_uid != os.getuid()
            or target.stat().st_mode & 0o077
            or target.stat().st_nlink != 1
            or target.stat().st_size > 8192
        ):
            raise RuntimeError("OAuth import journal must be a bounded owner-only file")
        record = (
            json.loads(target.read_text())
            if target.exists()
            else {
                "secret_id": ref["secret_id"],
                "project_id": ref["project_id"],
                "client_id": client_id,
                "nonce": uuid.uuid4().hex,
                "submitted": False,
                "operation_id": "",
                "version_id": "",
            }
        )
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "secret_id",
                "project_id",
                "client_id",
                "nonce",
                "submitted",
                "operation_id",
                "version_id",
            }
            or type(record["submitted"]) is not bool
            or any(not isinstance(record[k], str) for k in record if k != "submitted")
        ):
            raise RuntimeError("Unsupported OAuth import journal")
        if any(
            record.get(k) != v
            for k, v in {
                "secret_id": ref["secret_id"],
                "project_id": ref["project_id"],
                "client_id": client_id,
            }.items()
        ):
            raise RuntimeError("OAuth import binding changed; reconcile the original import")
        with sdk_context(ref["project_id"], ref.get("nebius_auth")) as sdk:

            def call(method: Any, value: Any, **kwargs: Any) -> Any:
                try:
                    return method(
                        value,
                        timeout=30,
                        auth_timeout=30,
                        per_retry_timeout=10,
                        retries=1,
                        **kwargs,
                    ).wait()
                except Exception:
                    raise RuntimeError(
                        "SecretStash import outcome unavailable; retain its journal and reconcile"
                    ) from None

            versions = SecretVersionServiceClient(sdk)
            metadata = call(SecretServiceClient(sdk).get, GetSecretRequest(id=ref["secret_id"]))
            if (
                metadata.metadata.parent_id != ref["project_id"]
                or metadata.metadata.name != ref["secret_name"]
            ):
                raise RuntimeError("OAuth container identity differs from the linked dependency")
            if not record["submitted"]:
                if metadata.spec.primary_version_id:
                    raise RuntimeError(
                        "Container already has a primary credential; link its approved version instead"
                    )
                value = secret(secret_ref, base=base)
                validate_pair(client_id, value)
                record["submitted"] = True
                protected_json(target, record)
                operation = call(
                    versions.create,
                    CreateSecretVersionRequest(
                        metadata=ResourceMetadata(parent_id=ref["secret_id"]),
                        spec=SecretVersionSpec(
                            set_primary=False,
                            payload=[
                                Payload(key="client_id", string_value=client_id),
                                Payload(key="client_secret", string_value=value),
                            ],
                        ),
                    ),
                    metadata=[("x-idempotency-key", record["nonce"])],
                )
                record["operation_id"] = operation.id
                protected_json(target, record)
            elif not record["version_id"]:
                known = record["operation_id"]
                if known and operation_id and known != operation_id:
                    raise RuntimeError("Import recovery operation differs from its journal")
                selected = known or operation_id
                if not selected:
                    raise RuntimeError(
                        "Import acknowledgement was lost; supply the exact --import-operation-id after investigation"
                    )
                operation = call(versions.operation_service().get, GetOperationRequest(id=selected))
                headers = operation.raw().request_headers
                if operation.id != selected or (
                    not known
                    and (
                        "x-idempotency-key" not in headers
                        or list(headers["x-idempotency-key"].values) != [record["nonce"]]
                    )
                ):
                    raise RuntimeError(
                        "Provider operation does not match the original OAuth import"
                    )
                record["operation_id"] = selected
                protected_json(target, record)
            if not record["version_id"]:
                try:
                    wait_nebius_operation(
                        operation, timeout_seconds=180, action="OAuth secret import"
                    )
                except Exception:
                    raise RuntimeError(
                        "OAuth import operation is unconfirmed; retry with its retained journal"
                    ) from None
                if not operation.resource_id:
                    raise RuntimeError("OAuth import has no confirmed resource ID")
                record["version_id"] = operation.resource_id
                protected_json(target, record)
            version = call(versions.get, GetSecretVersionRequest(id=record["version_id"]))
            if (
                version.metadata.id != record["version_id"]
                or version.metadata.parent_id != ref["secret_id"]
            ):
                raise RuntimeError("Imported credential version differs from its exact operation")
            _, stored = decode_payload(
                call(
                    PayloadServiceClient(sdk).get,
                    GetPayloadRequest(secret_id=ref["secret_id"], version_id=record["version_id"]),
                ),
                record["version_id"],
            )
            if set(stored) != {"client_id", "client_secret"} or stored["client_id"] != client_id:
                raise RuntimeError("Imported OAuth payload differs from its journal")
            validate_pair(stored["client_id"], stored["client_secret"])
            return record["version_id"]
    finally:
        os.close(fd)
