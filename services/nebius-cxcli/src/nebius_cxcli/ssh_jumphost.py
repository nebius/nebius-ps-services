"""SSH jump-host day-2 operation helpers."""

from __future__ import annotations

import ipaddress
import json
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .component_instances import component_instance_id, component_instance_label, component_type_id
from .component_sources import component_output_root_name
from .runtime_config import to_plain_data
from .ssh_trust import ssh_trust_options

SSH_JUMPHOST_COMPONENT_ID = "ssh-jumphost"


@dataclass(frozen=True)
class SshJumphostComponent:
    component_id: str
    instance_id: str
    inputs: Mapping[str, Any]

    @property
    def label(self) -> str:
        return component_instance_label(self.component_id, self.instance_id)


@dataclass(frozen=True)
class SshJumphostAllowedCidrRequest:
    component: SshJumphostComponent
    public_ip: str
    ssh_user: str
    ssh_private_key: Path | None
    ssh_known_hosts_file: Path
    operation: str
    allowed_cidrs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SshJumphostAllowedCidrResult:
    allowed_cidrs: tuple[str, ...]
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _enabled_ssh_jumphost_components(payload_or_config: Any) -> tuple[SshJumphostComponent, ...]:
    payload = to_plain_data(payload_or_config)
    if not isinstance(payload, Mapping):
        return ()
    infra = _as_mapping(payload.get("infra"))
    rows = infra.get("components")
    if not isinstance(rows, list):
        return ()
    components: list[SshJumphostComponent] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if component_type_id(row) != SSH_JUMPHOST_COMPONENT_ID:
            continue
        if row.get("enabled") is False:
            continue
        components.append(
            SshJumphostComponent(
                component_id=SSH_JUMPHOST_COMPONENT_ID,
                instance_id=component_instance_id(row),
                inputs=_as_mapping(row.get("inputs")),
            )
        )
    return tuple(components)


def select_ssh_jumphost_component(
    payload_or_config: Any,
    *,
    component_selector: str | None = None,
) -> SshJumphostComponent:
    components = _enabled_ssh_jumphost_components(payload_or_config)
    if not components:
        raise RuntimeError("config.yaml does not enable an ssh-jumphost infra component")
    selector = _as_text(component_selector).lower().replace("_", "-")
    if selector:
        for component in components:
            if selector in {component.instance_id, component.label, component.component_id}:
                return component
        labels = ", ".join(component.label for component in components)
        raise RuntimeError(
            f"ssh-jumphost component '{component_selector}' was not found. Available: {labels}"
        )
    if len(components) > 1:
        labels = ", ".join(component.label for component in components)
        raise RuntimeError(
            "config.yaml enables multiple ssh-jumphost components. "
            f"Select one with --component. Available: {labels}"
        )
    return components[0]


def ssh_jumphost_public_ip_from_outputs(
    terraform_outputs: Mapping[str, Any],
    component: SshJumphostComponent,
) -> str:
    output_name = component_output_root_name(component.instance_id, "public_ip")
    output_payload = terraform_outputs.get(output_name)
    value = output_payload.get("value") if isinstance(output_payload, Mapping) else None
    public_ip = _as_text(value)
    if not public_ip:
        raise RuntimeError(
            f"Terraform output `{output_name}` is missing or empty. "
            "Run render/deploy for this config before running SSH jump-host day-2 operations."
        )
    return public_ip


def normalize_allowed_cidrs(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(str(value), strict=False)
        except ValueError as exc:
            raise ValueError(f"--allowed-cidr must be a valid CIDR: {value}") from exc
        if network.version != 4:
            raise ValueError("--allowed-cidr currently supports IPv4 CIDRs only")
        normalized.append(network.with_prefixlen)
    return tuple(normalized)


def normalize_allowed_cidr_csv(value: str) -> tuple[str, ...]:
    parts = [item.strip() for item in str(value or "").split(",")]
    if not parts or any(not item for item in parts):
        raise ValueError("--allowed-cidr must be a comma-separated list of IPv4 CIDRs")
    normalized = normalize_allowed_cidrs(parts)
    deduped: list[str] = []
    seen: set[str] = set()
    for cidr in normalized:
        if cidr not in seen:
            deduped.append(cidr)
            seen.add(cidr)
    return tuple(deduped)


def _extract_json_payload(stdout: str) -> Mapping[str, Any]:
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            return payload
    raise RuntimeError("SSH jump-host VM-local helper did not return a JSON object")


def _ssh_command_parts(
    *,
    ssh_user: str,
    public_ip: str,
    ssh_private_key: Path | None,
    ssh_known_hosts_file: Path,
    remote_parts: Sequence[str],
) -> list[str]:
    remote_command = " ".join(shlex.quote(part) for part in remote_parts)
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
    ]
    command.extend(ssh_trust_options(ssh_known_hosts_file))
    if ssh_private_key is not None:
        command.extend(["-i", str(ssh_private_key.expanduser())])
    command.extend([f"{ssh_user}@{public_ip}", remote_command])
    return command


def _ssh_allowed_cidr_command(request: SshJumphostAllowedCidrRequest) -> list[str]:
    if request.operation not in {"add", "remove", "list"}:
        raise ValueError("SSH jump-host allowed CIDR operation must be add, remove, or list")
    remote_action = "list" if request.operation == "list" else f"{request.operation}-allowed-cidrs"
    remote_parts = [
        "sudo",
        "/usr/local/sbin/nebius-ssh-jumphost",
        remote_action,
        "--output-json",
    ]
    if request.operation in {"add", "remove"}:
        remote_parts.extend(["--allowed-cidr", ",".join(request.allowed_cidrs)])
    return _ssh_command_parts(
        ssh_user=request.ssh_user,
        public_ip=request.public_ip,
        ssh_private_key=request.ssh_private_key,
        ssh_known_hosts_file=request.ssh_known_hosts_file,
        remote_parts=remote_parts,
    )


def update_ssh_jumphost_allowed_cidrs(
    request: SshJumphostAllowedCidrRequest,
    *,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> SshJumphostAllowedCidrResult:
    if request.operation not in {"add", "remove", "list"}:
        raise ValueError("SSH jump-host allowed CIDR operation must be add, remove, or list")
    if request.operation in {"add", "remove"} and not request.allowed_cidrs:
        raise ValueError("--allowed-cidr is required for SSH jump-host CIDR updates")
    if request.operation == "list" and request.allowed_cidrs:
        raise ValueError("--allowed-cidr does not apply to SSH jump-host list mode")

    try:
        completed = run_command(
            _ssh_allowed_cidr_command(request),
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = (
            f"SSH jump-host allowed CIDR operation failed for {request.component.label} "
            f"at {request.public_ip}"
        )
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"SSH jump-host allowed CIDR operation timed out for {request.component.label} "
            f"at {request.public_ip}"
        ) from exc
    payload = _extract_json_payload(completed.stdout or "")
    return SshJumphostAllowedCidrResult(
        allowed_cidrs=tuple(str(item) for item in payload.get("allowed_cidrs") or []),
        added=tuple(str(item) for item in payload.get("added") or []),
        removed=tuple(str(item) for item in payload.get("removed") or []),
        unchanged=tuple(str(item) for item in payload.get("unchanged") or []),
    )
