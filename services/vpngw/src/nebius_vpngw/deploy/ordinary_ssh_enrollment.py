from __future__ import annotations

import base64
import hmac
import json
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .ssh_client_auth import SSHClientAuth
from .ssh_policy import SSHHostKeyEnrollment, SSHTrustAuthority

_INSTANCE_DATA_COMMAND = (
    "curl --fail --silent --show-error --max-time 5 --header Metadata:true "
    "http://metadata.nebius.internal/v1/instance-data"
)
_CLOUD_INIT_INSTANCE_ID_COMMAND = "cloud-init query instance_id"


@dataclass(frozen=True)
class OrdinarySSHEnrollmentTarget:
    """An exact retained ordinary Compute snapshot admitted for one enrollment."""

    hostname: str
    transport_address: str
    compute_id: str
    project_id: str
    instance_name: str
    region_id: str
    compute_binding_sha256: str
    assert_current: Callable[[], None] = field(repr=False, compare=False)


def _capture_ed25519_host_key(
    address: str,
    *,
    paramiko_module: Any,
) -> tuple[str, str]:
    transport: Any | None = None
    connection: socket.socket | None = None
    try:
        connection = socket.create_connection((address, 22), timeout=10)
        transport = paramiko_module.Transport(connection)
        transport.start_client(timeout=10)
        key = transport.get_remote_server_key()
        key_type = str(key.get_name())
        key_data = str(key.get_base64())
    except Exception as error:
        raise RuntimeError("Unable to observe the retained gateway SSH host identity") from error
    finally:
        if transport is not None:
            transport.close()
        elif connection is not None:
            connection.close()
    if key_type != "ssh-ed25519" or not key_data:
        raise ValueError("Retained ordinary gateway enrollment requires one Ed25519 host key")
    return key_type, key_data


def _read_bounded(stream: Any, limit: int, label: str) -> bytes:
    content = stream.read(limit + 1)
    if not isinstance(content, bytes):
        content = str(content).encode("utf-8", errors="replace")
    if len(content) > limit:
        raise RuntimeError(f"Gateway {label} response exceeded its safety limit")
    return content


def _run_guest_query(client: Any, command: str, *, timeout: int, limit: int) -> str | None:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = _read_bounded(stdout, limit, "identity")
    error = _read_bounded(stderr, limit, "identity error")
    status = stdout.channel.recv_exit_status()
    if status != 0:
        del error
        return None
    try:
        return output.decode("utf-8").strip()
    except UnicodeDecodeError as decode_error:
        raise RuntimeError("Gateway identity response is not valid UTF-8") from decode_error


def _guest_matches_target(payload: object, target: OrdinarySSHEnrollmentTarget) -> bool:
    if not isinstance(payload, dict):
        return False
    return all(
        str(payload.get(field) or "") == expected
        for field, expected in (
            ("id", target.compute_id),
            ("parent_id", target.project_id),
            ("name", target.instance_name),
            ("region", target.region_id),
        )
    )


def _prove_guest_identity(
    target: OrdinarySSHEnrollmentTarget,
    *,
    key_type: str,
    key_data: str,
    client_auth: SSHClientAuth,
    username: str,
    paramiko_module: Any,
) -> None:
    client = paramiko_module.SSHClient()
    try:
        raw_key = base64.b64decode(key_data, validate=True)
        key = paramiko_module.Ed25519Key(data=raw_key)
        client.get_host_keys().add(target.transport_address, key_type, key)
        client.set_missing_host_key_policy(paramiko_module.RejectPolicy())
        client.connect(
            hostname=target.transport_address,
            username=username,
            timeout=15,
            auth_timeout=15,
            banner_timeout=15,
            **client_auth.paramiko_connect_kwargs(),
        )
        instance_data = _run_guest_query(
            client,
            _INSTANCE_DATA_COMMAND,
            timeout=8,
            limit=16 * 1024,
        )
        if instance_data is not None:
            try:
                payload = json.loads(instance_data)
            except json.JSONDecodeError:
                payload = None
            if _guest_matches_target(payload, target):
                return
        instance_id = _run_guest_query(
            client,
            _CLOUD_INIT_INSTANCE_ID_COMMAND,
            timeout=5,
            limit=256,
        )
        if instance_id != target.compute_id:
            raise RuntimeError(
                "Gateway guest identity does not match the retained Compute resource"
            )
    except Exception as error:
        if isinstance(error, RuntimeError) and str(error).startswith("Gateway guest identity"):
            raise
        raise RuntimeError(
            "Strict SSH authentication or guest identity correlation failed for the retained gateway"
        ) from error
    finally:
        client.close()


def enroll_ordinary_ssh_host_key(
    target: OrdinarySSHEnrollmentTarget,
    *,
    client_auth: SSHClientAuth,
    username: str,
    paramiko_module: Any | None = None,
    capture_host_key: Callable[[str], tuple[str, str]] | None = None,
    prove_guest_identity: Callable[[str, str], None] | None = None,
) -> SSHHostKeyEnrollment:
    """Perform one fail-closed H1/cloud/H2/guest/cloud enrollment transaction."""

    if paramiko_module is None:
        try:
            import paramiko as paramiko_module  # type: ignore[import-untyped,no-redef]
        except ImportError as error:
            raise RuntimeError("Paramiko is required for ordinary gateway enrollment") from error
    capture = capture_host_key or (
        lambda address: _capture_ed25519_host_key(
            address,
            paramiko_module=paramiko_module,
        )
    )
    first_type, first_data = capture(target.transport_address)
    target.assert_current()
    second_type, second_data = capture(target.transport_address)
    if (
        first_type != "ssh-ed25519"
        or second_type != first_type
        or not hmac.compare_digest(second_data, first_data)
    ):
        raise RuntimeError("Retained gateway SSH host identity changed during enrollment")
    if prove_guest_identity is None:
        _prove_guest_identity(
            target,
            key_type=first_type,
            key_data=first_data,
            client_auth=client_auth,
            username=username,
            paramiko_module=paramiko_module,
        )
    else:
        prove_guest_identity(first_type, first_data)
    target.assert_current()
    if not re.fullmatch(r"[0-9a-f]{64}", target.compute_binding_sha256):
        raise RuntimeError("Retained gateway Compute binding is inconsistent")
    return SSHHostKeyEnrollment(
        hostname=target.hostname,
        key_type=first_type,
        key_data=first_data,
        authority=SSHTrustAuthority(
            kind="legacy-ordinary-network-enrollment-v1",
            compute_binding_sha256=target.compute_binding_sha256,
        ),
        assert_current=target.assert_current,
    )
