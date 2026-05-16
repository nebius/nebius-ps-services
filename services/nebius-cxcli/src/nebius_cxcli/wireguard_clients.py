"""WireGuard VPN gateway day-2 operation helpers."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .component_instances import component_instance_id, component_instance_label, component_type_id
from .component_sources import component_output_root_name
from .paths import ProjectPaths
from .runtime_config import to_plain_data

WIREGUARD_COMPONENT_ID = "wireguard-gw"
WIREGUARD_CLIENT_OUTPUT_DIR = "wireguard-clients"
WIREGUARD_QUICK_INTERFACE_NAME_MAX_LENGTH = 15
_CLIENT_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,13}[a-z0-9])?$")


@dataclass(frozen=True)
class WireGuardComponent:
    component_id: str
    instance_id: str
    inputs: Mapping[str, Any]

    @property
    def label(self) -> str:
        return component_instance_label(self.component_id, self.instance_id)


@dataclass(frozen=True)
class WireGuardClientGenerationRequest:
    component: WireGuardComponent
    public_ip: str
    ssh_user: str
    ssh_private_key: Path | None
    client_name: str | None
    local_subnets: tuple[str, ...]
    dns: tuple[str, ...]
    persistent_keepalive: int | None
    output_dir: Path
    force: bool = False


@dataclass(frozen=True)
class WireGuardClientGenerationResult:
    client_name: str
    client_wg_tunnel_address: str
    local_subnets: tuple[str, ...]
    output_path: Path
    remote_config_path: str
    clients_created: int
    remaining_client_slots: int


@dataclass(frozen=True)
class WireGuardLocalSubnetUpdateRequest:
    component: WireGuardComponent
    public_ip: str
    ssh_user: str
    ssh_private_key: Path | None
    operation: str
    local_subnets: tuple[str, ...]


@dataclass(frozen=True)
class WireGuardLocalSubnetUpdateResult:
    local_subnets: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _enabled_wireguard_components(payload_or_config: Any) -> tuple[WireGuardComponent, ...]:
    payload = to_plain_data(payload_or_config)
    if not isinstance(payload, Mapping):
        return ()
    infra = _as_mapping(payload.get("infra"))
    rows = infra.get("components")
    if not isinstance(rows, list):
        return ()
    components: list[WireGuardComponent] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if component_type_id(row) != WIREGUARD_COMPONENT_ID:
            continue
        if row.get("enabled") is False:
            continue
        inputs = _as_mapping(row.get("inputs"))
        components.append(
            WireGuardComponent(
                component_id=WIREGUARD_COMPONENT_ID,
                instance_id=component_instance_id(row),
                inputs=inputs,
            )
        )
    return tuple(components)


def select_wireguard_component(
    payload_or_config: Any,
    *,
    component_selector: str | None = None,
) -> WireGuardComponent:
    components = _enabled_wireguard_components(payload_or_config)
    if not components:
        raise RuntimeError("config.yaml does not enable a wireguard-gw infra component")
    selector = _as_text(component_selector).lower().replace("_", "-")
    if selector:
        for component in components:
            if selector in {component.instance_id, component.label, component.component_id}:
                return component
        labels = ", ".join(component.label for component in components)
        raise RuntimeError(
            f"wireguard-gw component '{component_selector}' was not found. Available: {labels}"
        )
    if len(components) > 1:
        labels = ", ".join(component.label for component in components)
        raise RuntimeError(
            "config.yaml enables multiple wireguard-gw components. "
            f"Select one with --component. Available: {labels}"
        )
    return components[0]


def default_wireguard_client_output_dir(paths: ProjectPaths) -> Path:
    return paths.project_dir / WIREGUARD_CLIENT_OUTPUT_DIR


def wireguard_public_ip_from_outputs(
    terraform_outputs: Mapping[str, Any],
    component: WireGuardComponent,
) -> str:
    output_name = component_output_root_name(component.instance_id, "public_ip")
    output_payload = terraform_outputs.get(output_name)
    value = output_payload.get("value") if isinstance(output_payload, Mapping) else None
    public_ip = _as_text(value)
    if not public_ip:
        raise RuntimeError(
            f"Terraform output `{output_name}` is missing or empty. "
            "Run render/deploy for this config before running WireGuard day-2 operations."
        )
    return public_ip


def normalize_local_subnets(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(str(value), strict=False)
        except ValueError as exc:
            raise ValueError(f"--local-subnet must be a valid CIDR: {value}") from exc
        if network.version != 4:
            raise ValueError("--local-subnet currently supports IPv4 CIDRs only")
        normalized.append(network.with_prefixlen)
    return tuple(normalized)


def normalize_local_subnet_csv(value: str) -> tuple[str, ...]:
    parts = [item.strip() for item in str(value or "").split(",")]
    if not parts or any(not item for item in parts):
        raise ValueError("--local-subnet must be a comma-separated list of IPv4 CIDRs")
    normalized = normalize_local_subnets(parts)
    deduped: list[str] = []
    seen: set[str] = set()
    for cidr in normalized:
        if cidr not in seen:
            deduped.append(cidr)
            seen.add(cidr)
    return tuple(deduped)


def normalize_dns(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        try:
            address = ipaddress.ip_address(str(value))
        except ValueError as exc:
            raise ValueError(f"--dns must be an IPv4 address: {value}") from exc
        if address.version != 4:
            raise ValueError("--dns currently supports IPv4 addresses only")
        normalized.append(str(address))
    return tuple(normalized)


def _generated_client_name() -> str:
    return f"wg-{secrets.token_hex(6)}"


def _safe_client_config_filename(client_name: str) -> str:
    if not _CLIENT_NAME_RE.fullmatch(client_name):
        raise RuntimeError(
            "WireGuard server returned a client name that cannot be used as a "
            f"wg-quick interface config filename: {client_name}. Expected lowercase "
            "letters, digits, and hyphens, up to 15 characters."
        )
    return f"{client_name}.conf"


def _extract_json_payload(stdout: str) -> Mapping[str, Any]:
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            return payload
    raise RuntimeError("WireGuard gateway-local helper did not return a JSON object")


def _ssh_command_parts(
    *,
    ssh_user: str,
    public_ip: str,
    ssh_private_key: Path | None,
    remote_parts: Sequence[str],
) -> list[str]:
    remote_command = " ".join(shlex.quote(part) for part in remote_parts)
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if ssh_private_key is not None:
        command.extend(["-i", str(ssh_private_key.expanduser())])
    command.extend([f"{ssh_user}@{public_ip}", remote_command])
    return command


def _ssh_command(request: WireGuardClientGenerationRequest) -> list[str]:
    remote_parts = [
        "sudo",
        "/usr/local/sbin/nebius-wireguard-client",
        "add",
        "--output-json",
    ]
    if request.client_name:
        remote_parts.extend(["--name", request.client_name])
    for local_subnet in request.local_subnets:
        remote_parts.extend(["--local-subnet", local_subnet])
    for dns in request.dns:
        remote_parts.extend(["--dns", dns])
    if request.persistent_keepalive is not None:
        remote_parts.extend(["--persistent-keepalive", str(request.persistent_keepalive)])
    return _ssh_command_parts(
        ssh_user=request.ssh_user,
        public_ip=request.public_ip,
        ssh_private_key=request.ssh_private_key,
        remote_parts=remote_parts,
    )


def _ssh_local_subnet_update_command(request: WireGuardLocalSubnetUpdateRequest) -> list[str]:
    if request.operation not in {"add", "remove"}:
        raise ValueError("WireGuard local subnet operation must be add or remove")
    remote_action = f"{request.operation}-local-subnets"
    remote_parts = [
        "sudo",
        "/usr/local/sbin/nebius-wireguard-client",
        remote_action,
        "--local-subnet",
        ",".join(request.local_subnets),
    ]
    return _ssh_command_parts(
        ssh_user=request.ssh_user,
        public_ip=request.public_ip,
        ssh_private_key=request.ssh_private_key,
        remote_parts=remote_parts,
    )


def generate_wireguard_client_config(
    request: WireGuardClientGenerationRequest,
    *,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> WireGuardClientGenerationResult:
    resolved_client_name = request.client_name or _generated_client_name()
    if not _CLIENT_NAME_RE.fullmatch(resolved_client_name):
        raise ValueError(
            "--client-name must use lowercase letters, digits, and hyphens, "
            "up to 15 characters so wg-quick can use it as the interface name"
        )
    if request.persistent_keepalive is not None and not (
        0 <= request.persistent_keepalive <= 65535
    ):
        raise ValueError("--persistent-keepalive must be between 0 and 65535")

    request = replace(request, client_name=resolved_client_name)
    request.output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(request.output_dir, 0o700)
    try:
        completed = run_command(
            _ssh_command(request),
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = (
            f"SSH WireGuard client generation failed for {request.component.label} "
            f"at {request.public_ip}"
        )
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"SSH WireGuard client generation timed out for {request.component.label} "
            f"at {request.public_ip}"
        ) from exc
    payload = _extract_json_payload(completed.stdout or "")
    client_name = _as_text(payload.get("name"))
    config_text = str(payload.get("config") or "")
    if not client_name or not config_text:
        raise RuntimeError("WireGuard client generator returned incomplete client data")

    output_path = request.output_dir / _safe_client_config_filename(client_name)
    if output_path.exists() and not request.force:
        raise RuntimeError(f"Refusing to overwrite existing WireGuard client config: {output_path}")
    output_path.write_text(config_text, encoding="utf-8")
    output_path.chmod(0o600)

    return WireGuardClientGenerationResult(
        client_name=client_name,
        client_wg_tunnel_address=_as_text(payload.get("client_wg_tunnel_address")),
        local_subnets=tuple(str(item) for item in payload.get("local_subnets") or []),
        output_path=output_path,
        remote_config_path=_as_text(payload.get("config_path")),
        clients_created=int(payload.get("clients_created") or 0),
        remaining_client_slots=int(payload.get("remaining_client_slots") or 0),
    )


def update_wireguard_local_subnets(
    request: WireGuardLocalSubnetUpdateRequest,
    *,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> WireGuardLocalSubnetUpdateResult:
    if request.operation not in {"add", "remove"}:
        raise ValueError("WireGuard local subnet operation must be add or remove")
    if not request.local_subnets:
        raise ValueError("--local-subnet is required for WireGuard local subnet updates")

    try:
        completed = run_command(
            _ssh_local_subnet_update_command(request),
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = (
            f"SSH WireGuard local subnet update failed for {request.component.label} "
            f"at {request.public_ip}"
        )
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"SSH WireGuard local subnet update timed out for {request.component.label} "
            f"at {request.public_ip}"
        ) from exc
    payload = _extract_json_payload(completed.stdout or "")
    return WireGuardLocalSubnetUpdateResult(
        local_subnets=tuple(str(item) for item in payload.get("local_subnets") or []),
        added=tuple(str(item) for item in payload.get("added") or []),
        removed=tuple(str(item) for item in payload.get("removed") or []),
        unchanged=tuple(str(item) for item in payload.get("unchanged") or []),
    )
