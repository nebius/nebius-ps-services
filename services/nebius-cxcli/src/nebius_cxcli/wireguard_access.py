"""Pure WireGuard access contract and short-lived, isolated SSH credentials."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ssh_host_identity import _pair

SCHEMA = 1
ACCESS_FIELDS = {
    "admin_group_id",
    "connection_secret_id",
    "generation",
    "secretstash_ref",
    "lifecycle",
}
PUBLIC_KEY = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}")


def validate_access(row: Mapping[str, Any]) -> None:
    access = row.get("access", {})
    if not isinstance(access, Mapping) or set(access) - ACCESS_FIELDS:
        raise ValueError(
            "WireGuard access must contain only admin_group_id and generated references"
        )
    if any(not isinstance(value, str) or not value.strip() for value in access.values()):
        raise ValueError("WireGuard access references must be nonempty strings")
    if row.get("enabled") and not access.get("admin_group_id"):
        raise ValueError("Enabled WireGuard requires access.admin_group_id from live IAM groups")
    if access.get("lifecycle", "active") not in {"active", "retained"}:
        raise ValueError("WireGuard access.lifecycle must be active or retained")
    if row.get("enabled") or access.get("lifecycle") == "retained":
        from .wireguard_dependencies import dependency_instance

        dependency_instance(row)
    if bool(access.get("connection_secret_id")) != bool(access.get("generation")):
        raise ValueError("WireGuard connection_secret_id and generation must be supplied together")
    if {"ssh_public_key", "ssh_host_keys", "access_bootstrap", "bootstrap_user_data"} & set(
        row.get("inputs", {})
    ):
        raise ValueError(
            "WireGuard SSH keys/bootstrap are lifecycle-owned; remove authored key inputs"
        )


def validate_pair(payload: Mapping[str, str]) -> dict[str, str]:
    if set(payload) != {"ed25519_private", "ed25519_public"}:
        raise RuntimeError("WireGuard credential payload has an unsupported schema")
    try:
        pair = _pair(payload["ed25519_private"].encode("ascii"))
    except (KeyError, AttributeError, UnicodeError):
        raise RuntimeError("WireGuard credential payload is invalid") from None
    if pair["ed25519_public"] != payload["ed25519_public"]:
        raise RuntimeError("WireGuard credential public/private key mismatch")
    return pair


def decode_connection(payload: Mapping[str, str]) -> dict[str, Any]:
    try:
        if set(payload) != {"connection"} or len(payload["connection"]) > 16384:
            raise ValueError
        value = json.loads(payload["connection"])
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("WireGuard connection payload is invalid") from None


def validate_connection(
    value: Mapping[str, Any], *, project: str, component: str, generation: str
) -> None:
    if (
        value.get("schema") != SCHEMA
        or value.get("state") != "READY"
        or value.get("project_id") != project
        or value.get("component_id") != component
        or value.get("generation") != generation
    ):
        raise RuntimeError(
            "WireGuard connection is unavailable or belongs to a different deployment"
        )
    for field in (
        "instance_id",
        "boot_disk_id",
        "public_allocation_id",
        "login_secret_id",
        "login_version_id",
    ):
        if not isinstance(value.get(field), str) or not value[field]:
            raise RuntimeError("WireGuard connection identity is incomplete")
    try:
        if not isinstance(value["public_ip"], str):
            raise ValueError
        ipaddress.ip_address(value["public_ip"])
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value["ssh_user"]):
            raise ValueError
        if value["ssh_user"] in {"root", "admin"} or not PUBLIC_KEY.fullmatch(
            value["host_public_key"]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("WireGuard connection endpoint or host identity is invalid") from None


@dataclass(frozen=True)
class SSHAccess:
    public_ip: str
    ssh_user: str
    private_key: Path
    known_hosts: Path


@contextmanager
def temporary_ssh_access(
    connection: Mapping[str, Any], pair: Mapping[str, str], *, known_hosts: Path | None = None
) -> Iterator[SSHAccess]:
    """No credential survives the command, including failed subprocesses/cancellation."""
    pair = validate_pair(pair)
    with tempfile.TemporaryDirectory(prefix="cxcli-wg-access-") as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        private = root / "login"
        pin = root / "known_hosts"
        for path, value in (
            (private, pair["ed25519_private"]),
            (pin, f"{connection['public_ip']} {connection['host_public_key']}\n"),
        ):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(value)
        if known_hosts is not None:
            # An extra operator constraint can only agree with the authenticated binding.
            from .ssh_host_enrollment import has_host
            from .ssh_trust import ssh_host_trust_options

            ssh_host_trust_options(known_hosts, connection["public_ip"])
            if not has_host(known_hosts, connection["public_ip"]):
                raise RuntimeError("Explicit WireGuard trust has no selected host")
            # Keep the authoritative pin; OpenSSH must authenticate this exact key.
            # Validate the operator pin independently using ssh-keygen, including hashed hosts.
            import subprocess

            result = subprocess.run(
                ["ssh-keygen", "-F", connection["public_ip"], "-f", str(known_hosts)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            keys = {
                " ".join(line.split()[1:3])
                for line in result.stdout.splitlines()
                if line and not line.startswith("#")
            }
            if keys != {connection["host_public_key"]}:
                raise RuntimeError(
                    "Explicit WireGuard trust differs from SecretStash host identity"
                )
        yield SSHAccess(connection["public_ip"], connection["ssh_user"], private, pin)


@contextmanager
def client_access(
    api: Any,
    *,
    project: str,
    component: str,
    access: Mapping[str, str],
    known_hosts: Path | None = None,
) -> Iterator[SSHAccess]:
    secret = access.get("connection_secret_id", "")
    generation = access.get("generation", "")
    if not secret or not generation:
        raise RuntimeError(
            "WireGuard has no published connection; complete its SecretStash deployment"
        )
    version, payload = api.payload(secret)
    connection = decode_connection(payload)
    validate_connection(connection, project=project, component=component, generation=generation)
    _, pair = api.payload(connection["login_secret_id"], connection["login_version_id"])
    # Recheck current primary after fetching credentials. Never use a retired/candidate binding.
    if api.payload(secret) != (version, payload):
        raise RuntimeError("WireGuard connection changed during credential acquisition; retry")
    with temporary_ssh_access(connection, pair, known_hosts=known_hosts) as ssh:
        yield ssh
