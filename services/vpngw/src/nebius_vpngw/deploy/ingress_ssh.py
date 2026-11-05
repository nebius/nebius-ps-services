"""Pinned boot identity and fresh post-activation SSH proof."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from typing import Any

import yaml

from .. import ingress_policy
from .ssh_policy import configure_paramiko_host_verification

PROBE = (
    "import json;from pathlib import Path;"
    "print(json.dumps({'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))"
)


class IngressVerificationDrift(RuntimeError):
    """Fresh authenticated evidence proves drift with the same config and boot."""


READBACK = """
import hashlib,json
from pathlib import Path
from nebius_vpngw import ingress_policy as p
installed = hashlib.sha256(Path('/etc/nebius-vpngw/config-resolved.yaml').read_bytes()).hexdigest()
try:
    policy = p.validate(json.loads(p.POLICY_FILE.read_text()))
except (FileNotFoundError, ValueError):
    observed = {'policy':None, 'effective':False, 'config':installed}
else:
    try:
        p.verify(policy)
    except p.IngressPolicyDrift:
        effective = False
    else:
        effective = True
    observed = {'policy':p.identity(policy), 'effective':effective, 'config':installed}
print(json.dumps(observed))
"""


def _read(client: Any, command: str) -> dict[str, Any]:
    _stdin, stdout, _stderr = client.exec_command(command, timeout=30)
    raw = stdout.read(65537)
    if len(raw) > 65536 or stdout.channel.recv_exit_status():
        raise RuntimeError("Unable to verify authenticated SSH ingress state")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise RuntimeError("Invalid authenticated SSH ingress evidence")
    return result


def preflight(client: Any, config_yaml: str) -> str:
    ingress_policy.from_config(yaml.safe_load(config_yaml))
    observed = _read(client, "/usr/bin/python3 -c " + shlex.quote(PROBE))
    boot = observed.get("boot_id")
    if not isinstance(boot, str) or not boot:
        raise RuntimeError("Cannot verify the gateway boot identity")
    return boot


def verify_fresh(
    ssh: Any,
    target: str,
    instance: Any,
    local: dict,
    *,
    boot_id: str,
    config_sha256: str | None = None,
) -> None:
    """A new client/transport/authentication is mandatory, even on a no-op apply."""
    paramiko = ssh._ensure_paramiko()
    client = paramiko.SSHClient()
    policy = ingress_policy.from_config(yaml.safe_load(instance.config_yaml))
    expected_config = config_sha256 or hashlib.sha256(instance.config_yaml.encode()).hexdigest()
    try:
        configure_paramiko_host_verification(
            client,
            paramiko,
            policy=ssh._ssh_policy,
            hostname=instance.hostname if ssh._ssh_policy else None,
            transport_host=target if ssh._ssh_policy else None,
        )
        spec = (local.get("gateway_group") or {}).get("vm_spec") or {}
        ssh._connect_client(
            client,
            hostname=target,
            username=spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu"),
            vm_spec=spec,
        )
        if preflight(client, instance.config_yaml) != boot_id:
            raise RuntimeError("Gateway rebooted during deployment verification")
        observed = _read(client, "sudo -n /usr/bin/python3 -c " + shlex.quote(READBACK))
        if (
            set(observed) != {"policy", "effective", "config"}
            or not isinstance(observed["effective"], bool)
            or (
                observed["policy"] is not None
                and (
                    not isinstance(observed["policy"], str)
                    or len(observed["policy"]) != 64
                    or any(c not in "0123456789abcdef" for c in observed["policy"])
                )
            )
            or (observed["policy"] is None and observed["effective"])
        ):
            raise RuntimeError("Invalid authenticated ingress policy evidence")
        if observed["config"] != expected_config:
            raise RuntimeError("Gateway installed config identity differs")
        if observed["policy"] != ingress_policy.identity(policy) or not observed["effective"]:
            raise IngressVerificationDrift("Gateway effective ingress identity differs")
    except Exception as error:
        # Preserve the underlying authentication/host-key diagnosis. A timeout does
        # not establish whether the host policy, external firewall or route failed.
        error_type = (
            IngressVerificationDrift
            if isinstance(error, IngressVerificationDrift)
            else RuntimeError
        )
        raise error_type(
            "Gateway activation may have completed, but fresh SSH verification failed; "
            "deployment is incomplete and no rollback was performed. Check "
            "Security Groups, customer firewall, routing and SSH credentials. "
            f"Cause: {type(error).__name__}: {error}"
        ) from error
    finally:
        client.close()


def verify_repair_checkpoint(
    ssh: Any,
    target: str,
    instance: Any,
    local: dict,
    *,
    boot_id: str,
    config_sha256: str,
) -> None:
    """Fresh transport proof for package repair BEFORE desired-policy activation.

    This proves only the exact predecessor bytes and boot, never desired ingress.
    The later activation must still call verify_fresh with full kernel proof.
    """
    paramiko = ssh._ensure_paramiko()
    client = paramiko.SSHClient()
    try:
        configure_paramiko_host_verification(
            client,
            paramiko,
            policy=ssh._ssh_policy,
            hostname=instance.hostname if ssh._ssh_policy else None,
            transport_host=target if ssh._ssh_policy else None,
        )
        spec = (local.get("gateway_group") or {}).get("vm_spec") or {}
        ssh._connect_client(
            client,
            hostname=target,
            username=spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu"),
            vm_spec=spec,
        )
        if preflight(client, instance.config_yaml) != boot_id:
            raise RuntimeError("Gateway rebooted during package-repair verification")
        code = (
            "import hashlib,json;from pathlib import Path;"
            "print(json.dumps({'config':hashlib.sha256("
            "Path('/etc/nebius-vpngw/config-resolved.yaml').read_bytes()).hexdigest()}))"
        )
        if _read(client, "sudo -n /usr/bin/python3 -c " + shlex.quote(code)) != {
            "config": config_sha256
        }:
            raise RuntimeError("Package-repair predecessor identity changed")
    finally:
        client.close()
