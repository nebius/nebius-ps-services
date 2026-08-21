#!/usr/bin/env python3
"""Plan and converge the GCP side of a two-member Nebius VM-HA peer."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

RESOURCE_NAME = re.compile(r"^[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
GCLOUD_TIMEOUT_SECONDS = 600


class HelperError(RuntimeError):
    """A bounded, secret-free helper failure."""


@dataclass(frozen=True)
class TunnelPlan:
    name: str
    router_interface: str
    bgp_peer: str
    external_gateway: str
    gcp_interface: int
    peer_interface: int
    vm_index: int
    ha_role: str
    cidr: str
    nebius_ip: str
    gcp_ip: str
    advertised_priority: int
    psk_env_name: str


@dataclass(frozen=True)
class Plan:
    project: str
    region: str
    network: str
    vpn_gateway: str
    cloud_router: str
    cloud_router_asn: int
    nebius_asn: int
    connection: str
    active_public_ip: str
    passive_public_ip: str
    active_priority: int
    passive_priority: int
    external_gateway_a: str
    external_gateway_b: str
    tunnels: tuple[TunnelPlan, ...]


def _resource_basename(value: str | None) -> str:
    return str(value or "").rstrip("/").rsplit("/", 1)[-1]


def _resource_name(value: str, label: str) -> str:
    if not RESOURCE_NAME.fullmatch(value):
        raise HelperError(f"{label} is not a valid GCP resource name: {value}")
    return value


def _env_name(connection: str, index: int) -> str:
    token = connection.replace("-", "_").upper()
    value = os.environ.get(f"PSK{index}_ENV_NAME", f"GCP_{token}_TUNNEL_{index}_PSK")
    if not ENV_NAME.fullmatch(value):
        raise HelperError(f"PSK{index}_ENV_NAME is not a valid environment variable name")
    return value


def _link(cidr: str) -> tuple[str, str, str]:
    try:
        network = ipaddress.IPv4Network(cidr, strict=True)
    except ValueError as error:
        raise HelperError(f"invalid tunnel APIPA range: {cidr}") from error
    apipa = ipaddress.IPv4Network("169.254.0.0/16")
    if network.prefixlen != 30 or not network.subnet_of(apipa):
        raise HelperError(f"tunnel link must be a 169.254.0.0/16 APIPA /30: {cidr}")
    hosts = list(network.hosts())
    return str(network), str(hosts[0]), str(hosts[1])


def _public_ip(value: str, label: str) -> str:
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise HelperError(f"{label} must be a valid IPv4 address") from error
    return str(parsed)


def _psk(value: str, label: str) -> str:
    if not 1 <= len(value) <= 256 or any(not 0x20 <= ord(character) <= 0x7E for character in value):
        raise HelperError(f"{label} must contain 1 through 256 printable ASCII characters")
    return value


def _psks_from_private_config(path_value: str, plan: Plan) -> dict[str, str]:
    path = Path(path_value).expanduser()
    try:
        metadata = path.lstat()
    except OSError as error:
        raise HelperError(f"cannot inspect PSK source config: {error}") from error
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise HelperError("PSK source config must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise HelperError("PSK source config must not be accessible by group or other users")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise HelperError(f"cannot read PSK source config: {error}") from error
    if not isinstance(document, dict):
        raise HelperError("PSK source config must contain a YAML mapping")
    gateway_group = document.get("gateway_group")
    connections = gateway_group.get("connections") if isinstance(gateway_group, dict) else None
    if not isinstance(connections, list):
        connections = document.get("connections")
    if not isinstance(connections, list):
        raise HelperError("PSK source config does not contain a connections list")

    matching_connections = [
        connection
        for connection in connections
        if isinstance(connection, dict) and connection.get("name") == plan.connection
    ]
    if len(matching_connections) != 1:
        raise HelperError(
            f"PSK source config must contain exactly one connection named {plan.connection}"
        )
    raw_tunnels = matching_connections[0].get("tunnels")
    if not isinstance(raw_tunnels, list):
        raise HelperError("PSK source config contains a malformed matching connection")

    values_by_name: dict[str, str] = {}
    for raw_tunnel in raw_tunnels:
        name = raw_tunnel.get("name") if isinstance(raw_tunnel, dict) else None
        value = raw_tunnel.get("psk") if isinstance(raw_tunnel, dict) else None
        if not isinstance(name, str) or not name or not isinstance(value, str):
            raise HelperError("PSK source config contains a malformed named tunnel secret")
        if name in values_by_name:
            raise HelperError(f"PSK source config contains duplicate tunnel name {name}")
        reference = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
        if reference is not None:
            value = os.environ.get(reference.group(1), "")
            if not value:
                raise HelperError(
                    f"missing secret input {reference.group(1)} referenced by PSK source config"
                )
        values_by_name[name] = _psk(value, "PSK source config tunnel secret")

    expected_names = {tunnel.name for tunnel in plan.tunnels}
    if set(values_by_name) != expected_names:
        raise HelperError(
            "PSK source config matching connection must contain exactly the four planned tunnel names"
        )
    return {tunnel.psk_env_name: values_by_name[tunnel.name] for tunnel in plan.tunnels}


def _resolve_psks(plan: Plan, source_config: str | None) -> dict[str, str]:
    configured = {
        tunnel.psk_env_name: os.environ.get(tunnel.psk_env_name, "") for tunnel in plan.tunnels
    }
    if source_config:
        if any(configured.values()):
            raise HelperError(
                "--psk-source-config cannot be combined with planned tunnel PSK environment values"
            )
        return _psks_from_private_config(source_config, plan)
    resolved: dict[str, str] = {}
    for tunnel in plan.tunnels:
        value = configured[tunnel.psk_env_name]
        if not value:
            raise HelperError(f"missing secret input {tunnel.psk_env_name} for {tunnel.name}")
        resolved[tunnel.psk_env_name] = _psk(value, f"secret input {tunnel.psk_env_name}")
    return resolved


def _integer(value: str | int, label: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise HelperError(f"{label} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise HelperError(f"{label} must be from {minimum} through {maximum}")
    return parsed


def _observed_integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise HelperError(f"{label} returned an invalid integer")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise HelperError(f"{label} returned an invalid integer") from error


def _numbered_ip_interfaces(
    payload: dict[str, Any],
    field: str,
    *,
    address_field: str,
    label: str,
) -> dict[int, str]:
    raw_interfaces = payload.get(field) or []
    if not isinstance(raw_interfaces, list):
        raise HelperError(f"{label} returned malformed interfaces")
    interfaces: dict[int, str] = {}
    for item in raw_interfaces:
        if not isinstance(item, dict):
            raise HelperError(f"{label} returned malformed interfaces")
        interface_id = _observed_integer(item.get("id"), f"{label} interface ID")
        address = item.get(address_field)
        if interface_id in interfaces or not isinstance(address, str) or not address:
            raise HelperError(f"{label} returned malformed interfaces")
        interfaces[interface_id] = address
    return interfaces


def _name(prefix: str, suffix: str) -> str:
    prefix_limit = 63 - len(suffix) - 1
    if prefix_limit < 1:
        raise HelperError(f"resource suffix is too long: {suffix}")
    shortened_prefix = prefix[:prefix_limit].rstrip("-")
    return _resource_name(f"{shortened_prefix}-{suffix}", suffix)


def build_plan(args: argparse.Namespace) -> Plan:
    connection = _resource_name(args.connection_name, "connection name")
    active_ip = _public_ip(args.nebius_active_public_ip, "active Nebius public IP")
    passive_ip = _public_ip(args.nebius_passive_public_ip, "passive Nebius public IP")
    if active_ip == passive_ip:
        raise HelperError("active and passive Nebius members must use distinct public IPs")
    active_priority = _integer(args.active_priority, "active priority", 0, 65535)
    passive_priority = _integer(args.passive_priority, "passive priority", 0, 65535)
    if active_priority >= passive_priority:
        raise HelperError(
            "the configured active member must use a lower numeric advertised priority"
        )
    cloud_asn = _integer(args.cloud_router_asn, "Cloud Router ASN", 1, 4294967295)
    nebius_asn = _integer(args.nebius_asn, "Nebius ASN", 1, 4294967295)

    external_a = _resource_name(
        os.environ.get("EXTERNAL_GW1_NAME", _name(connection, "peer-a")),
        "external gateway A",
    )
    external_b = _resource_name(
        os.environ.get("EXTERNAL_GW2_NAME", _name(connection, "peer-b")),
        "external gateway B",
    )
    mappings = (
        # External A maps VM0/VM1; external B mirrors VM1/VM0 as documented by GCP.
        (external_a, 0, 0, 0, "active", active_priority),
        (external_a, 1, 1, 1, "passive", passive_priority),
        (external_b, 0, 0, 1, "active", passive_priority),
        (external_b, 1, 1, 0, "passive", active_priority),
    )
    tunnels: list[TunnelPlan] = []
    for offset, mapping in enumerate(mappings, start=1):
        external, gcp_interface, peer_interface, vm_index, ha_role, priority = mapping
        cidr, nebius_ip, gcp_ip = _link(
            os.environ.get(f"TUN{offset}_CIDR", f"169.254.{19 + offset}.0/30")
        )
        tunnels.append(
            TunnelPlan(
                name=_resource_name(
                    os.environ.get(f"TUNNEL{offset}_NAME", _name(connection, f"tunnel-{offset}")),
                    f"tunnel {offset}",
                ),
                router_interface=_resource_name(
                    os.environ.get(f"IFACE{offset}_NAME", _name(connection, f"if-{offset}")),
                    f"router interface {offset}",
                ),
                bgp_peer=_resource_name(
                    os.environ.get(f"PEER{offset}_NAME", _name(connection, f"peer-{offset}")),
                    f"BGP peer {offset}",
                ),
                external_gateway=external,
                gcp_interface=gcp_interface,
                peer_interface=peer_interface,
                vm_index=vm_index,
                ha_role=ha_role,
                cidr=cidr,
                nebius_ip=nebius_ip,
                gcp_ip=gcp_ip,
                advertised_priority=priority,
                psk_env_name=_env_name(connection, offset),
            )
        )
    if len({tunnel.cidr for tunnel in tunnels}) != 4:
        raise HelperError("the four tunnel APIPA ranges must be distinct")
    return Plan(
        project=args.gcp_project_id,
        region=args.region,
        network=args.network,
        vpn_gateway=_resource_name(args.vpn_gateway_name, "HA VPN gateway"),
        cloud_router=_resource_name(args.cloud_router_name, "Cloud Router"),
        cloud_router_asn=cloud_asn,
        nebius_asn=nebius_asn,
        connection=connection,
        active_public_ip=active_ip,
        passive_public_ip=passive_ip,
        active_priority=active_priority,
        passive_priority=passive_priority,
        external_gateway_a=external_a,
        external_gateway_b=external_b,
        tunnels=tuple(tunnels),
    )


class GCloud:
    def __init__(self, plan: Plan, *, secret_values: Sequence[str] = ()) -> None:
        self.plan = plan
        self._secret_values = frozenset(value for value in secret_values if value)

    @staticmethod
    def _redact(text: str, secrets: Sequence[str] = ()) -> str:
        redacted = text
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "<redacted>")
        return redacted.strip()

    def run(
        self,
        arguments: Sequence[str],
        *,
        label: str,
        check: bool = True,
        secrets: Sequence[str] = (),
        secret_flags: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        read_descriptor: int | None = None
        command = ["gcloud", *arguments]
        child_environment = os.environ.copy()
        for tunnel in self.plan.tunnels:
            child_environment.pop(tunnel.psk_env_name, None)
        if self._secret_values:
            child_environment = {
                name: value
                for name, value in child_environment.items()
                if value not in self._secret_values
            }
        run_options: dict[str, Any] = {"env": child_environment}
        if secret_flags:
            read_descriptor, write_descriptor = os.pipe()
            try:
                os.write(
                    write_descriptor,
                    json.dumps(secret_flags, separators=(",", ":")).encode("utf-8"),
                )
            finally:
                os.close(write_descriptor)
            command.append(f"--flags-file=/dev/fd/{read_descriptor}")
            run_options["pass_fds"] = (read_descriptor,)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=GCLOUD_TIMEOUT_SECONDS,
                **run_options,
            )
        except subprocess.TimeoutExpired as error:
            raise HelperError(f"{label} timed out") from error
        finally:
            if read_descriptor is not None:
                os.close(read_descriptor)
        if check and result.returncode != 0:
            detail = self._redact(result.stderr or result.stdout or "gcloud failed", secrets)
            raise HelperError(f"{label} failed: {detail}")
        return result

    def require_auth(self) -> str:
        result = self.run(
            ["auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
            label="gcloud authentication check",
        )
        account = result.stdout.strip().splitlines()
        if not account:
            raise HelperError("no active gcloud account; run 'gcloud auth login' explicitly")
        return account[0]

    def describe_json(self, kind: str, name: str, *, regional: bool) -> dict[str, Any] | None:
        arguments = ["compute", kind, "describe", name]
        if regional:
            arguments.append(f"--region={self.plan.region}")
        arguments.extend((f"--project={self.plan.project}", "--format=json"))
        result = self.run(arguments, label=f"describe {kind} {name}", check=False)
        if result.returncode != 0:
            detail = self._redact(result.stderr or result.stdout or "gcloud failed")
            if "not found" in detail.lower():
                return None
            raise HelperError(f"describe {kind} {name} failed: {detail}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise HelperError(f"describe {kind} {name} returned malformed JSON") from error
        if not isinstance(payload, dict):
            raise HelperError(f"describe {kind} {name} returned an invalid object")
        return payload

    def mutate(
        self,
        arguments: Sequence[str],
        *,
        label: str,
        secrets: Sequence[str] = (),
        secret_flags: dict[str, str] | None = None,
    ) -> None:
        self.run(
            arguments,
            label=label,
            secrets=secrets,
            secret_flags=secret_flags,
        )


def _log(message: str) -> None:
    print(message)


def _validate_gateway(plan: Plan, cloud: GCloud, *, dry_run: bool, status: bool) -> None:
    existing = cloud.describe_json("vpn-gateways", plan.vpn_gateway, regional=True)
    if existing is not None:
        if _resource_basename(existing.get("network")) != plan.network:
            raise HelperError(f"HA VPN gateway {plan.vpn_gateway} belongs to a different network")
        interfaces = _numbered_ip_interfaces(
            existing,
            "vpnInterfaces",
            address_field="ipAddress",
            label=f"HA VPN gateway {plan.vpn_gateway}",
        )
        if set(interfaces) != {0, 1} or len(set(interfaces.values())) != 2:
            raise HelperError(f"HA VPN gateway {plan.vpn_gateway} has a foreign interface mapping")
        for address in interfaces.values():
            _public_ip(address, f"HA VPN gateway {plan.vpn_gateway} interface")
        _log(f"OK HA VPN gateway: {plan.vpn_gateway}")
        return
    if status:
        _log(f"MISSING HA VPN gateway: {plan.vpn_gateway}")
    elif dry_run:
        _log(f"DRY-RUN create HA VPN gateway: {plan.vpn_gateway}")
    else:
        cloud.mutate(
            [
                "compute",
                "vpn-gateways",
                "create",
                plan.vpn_gateway,
                f"--region={plan.region}",
                f"--project={plan.project}",
                f"--network={plan.network}",
            ],
            label=f"create HA VPN gateway {plan.vpn_gateway}",
        )


def _validate_router(plan: Plan, cloud: GCloud, *, dry_run: bool, status: bool) -> None:
    existing = cloud.describe_json("routers", plan.cloud_router, regional=True)
    if existing is not None:
        observed_asn = (existing.get("bgp") or {}).get("asn")
        if (
            _resource_basename(existing.get("network")) != plan.network
            or _observed_integer(observed_asn, f"Cloud Router {plan.cloud_router} ASN")
            != plan.cloud_router_asn
        ):
            raise HelperError(f"Cloud Router {plan.cloud_router} has a different network or ASN")
        _log(f"OK Cloud Router: {plan.cloud_router}")
        return
    if status:
        _log(f"MISSING Cloud Router: {plan.cloud_router}")
    elif dry_run:
        _log(f"DRY-RUN create Cloud Router: {plan.cloud_router}")
    else:
        cloud.mutate(
            [
                "compute",
                "routers",
                "create",
                plan.cloud_router,
                f"--region={plan.region}",
                f"--project={plan.project}",
                f"--network={plan.network}",
                f"--asn={plan.cloud_router_asn}",
            ],
            label=f"create Cloud Router {plan.cloud_router}",
        )


def _validate_external_gateway(
    plan: Plan,
    cloud: GCloud,
    name: str,
    expected_ips: tuple[str, str],
    *,
    dry_run: bool,
    status: bool,
) -> None:
    existing = cloud.describe_json("external-vpn-gateways", name, regional=False)
    if existing is not None:
        interfaces = _numbered_ip_interfaces(
            existing,
            "interfaces",
            address_field="ipAddress",
            label=f"external VPN gateway {name}",
        )
        if existing.get("redundancyType") != "TWO_IPS_REDUNDANCY" or interfaces != {
            0: expected_ips[0],
            1: expected_ips[1],
        }:
            raise HelperError(f"external VPN gateway {name} has a foreign interface mapping")
        _log(f"OK external VPN gateway: {name}")
        return
    if status:
        _log(f"MISSING external VPN gateway: {name}")
    elif dry_run:
        _log(f"DRY-RUN create mirrored external VPN gateway: {name}")
    else:
        cloud.mutate(
            [
                "compute",
                "external-vpn-gateways",
                "create",
                name,
                f"--project={plan.project}",
                f"--interfaces=0={expected_ips[0]},1={expected_ips[1]}",
            ],
            label=f"create external VPN gateway {name}",
        )


def _validate_tunnel(
    plan: Plan,
    cloud: GCloud,
    tunnel: TunnelPlan,
    *,
    dry_run: bool,
    status: bool,
    psks: dict[str, str] | None = None,
) -> None:
    existing = cloud.describe_json("vpn-tunnels", tunnel.name, regional=True)
    if existing is not None:
        expected = (
            _resource_basename(existing.get("vpnGateway")) == plan.vpn_gateway,
            _resource_basename(existing.get("peerExternalGateway")) == tunnel.external_gateway,
            _resource_basename(existing.get("router")) == plan.cloud_router,
            _observed_integer(existing.get("ikeVersion"), f"tunnel {tunnel.name} IKE version") == 2,
            _observed_integer(
                existing.get("vpnGatewayInterface"), f"tunnel {tunnel.name} GCP interface"
            )
            == tunnel.gcp_interface,
            _observed_integer(
                existing.get("peerExternalGatewayInterface"),
                f"tunnel {tunnel.name} peer interface",
            )
            == tunnel.peer_interface,
        )
        if not all(expected):
            raise HelperError(f"tunnel {tunnel.name} has a foreign binding")
        _log(f"OK tunnel: {tunnel.name} ({existing.get('status', 'UNKNOWN')})")
        return
    if status:
        _log(f"MISSING tunnel: {tunnel.name}")
    elif dry_run:
        _log(f"DRY-RUN create tunnel: {tunnel.name} for VM{tunnel.vm_index}")
    else:
        secret = (psks or {}).get(tunnel.psk_env_name, "")
        if not secret:
            raise HelperError(f"missing preflight secret input for {tunnel.name}")
        cloud.mutate(
            [
                "compute",
                "vpn-tunnels",
                "create",
                tunnel.name,
                f"--region={plan.region}",
                f"--project={plan.project}",
                f"--vpn-gateway={plan.vpn_gateway}",
                f"--interface={tunnel.gcp_interface}",
                f"--peer-external-gateway={tunnel.external_gateway}",
                f"--peer-external-gateway-interface={tunnel.peer_interface}",
                f"--router={plan.cloud_router}",
                "--ike-version=2",
            ],
            label=f"create tunnel {tunnel.name}",
            secrets=(secret,),
            secret_flags={"--shared-secret": secret},
        )


def _router_interface(existing: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    for item in (existing or {}).get("interfaces") or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def _router_peer(existing: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    for item in (existing or {}).get("bgpPeers") or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def _validate_router_interface(
    plan: Plan,
    cloud: GCloud,
    tunnel: TunnelPlan,
    *,
    dry_run: bool,
    status: bool,
) -> None:
    router = cloud.describe_json("routers", plan.cloud_router, regional=True)
    existing = _router_interface(router, tunnel.router_interface)
    if existing is not None:
        if (
            existing.get("ipRange") != f"{tunnel.gcp_ip}/30"
            or _resource_basename(existing.get("linkedVpnTunnel")) != tunnel.name
        ):
            raise HelperError(
                f"router interface {tunnel.router_interface} has a foreign address or tunnel"
            )
        _log(f"OK router interface: {tunnel.router_interface}")
        return
    if status:
        _log(f"MISSING router interface: {tunnel.router_interface}")
    elif dry_run:
        _log(f"DRY-RUN create router interface: {tunnel.router_interface}")
    else:
        cloud.mutate(
            [
                "compute",
                "routers",
                "add-interface",
                plan.cloud_router,
                f"--region={plan.region}",
                f"--project={plan.project}",
                f"--interface-name={tunnel.router_interface}",
                f"--ip-address={tunnel.gcp_ip}",
                "--mask-length=30",
                f"--vpn-tunnel={tunnel.name}",
                f"--vpn-tunnel-region={plan.region}",
            ],
            label=f"create router interface {tunnel.router_interface}",
        )


def _validate_bgp_peer(
    plan: Plan,
    cloud: GCloud,
    tunnel: TunnelPlan,
    *,
    dry_run: bool,
    status: bool,
) -> None:
    router = cloud.describe_json("routers", plan.cloud_router, regional=True)
    existing = _router_peer(router, tunnel.bgp_peer)
    if existing is not None:
        expected = (
            existing.get("interfaceName") == tunnel.router_interface,
            existing.get("peerIpAddress") == tunnel.nebius_ip,
            _observed_integer(existing.get("peerAsn"), f"BGP peer {tunnel.bgp_peer} ASN")
            == plan.nebius_asn,
            _observed_integer(
                existing.get("advertisedRoutePriority"),
                f"BGP peer {tunnel.bgp_peer} advertised priority",
            )
            == tunnel.advertised_priority,
        )
        if not all(expected):
            raise HelperError(
                f"BGP peer {tunnel.bgp_peer} has a foreign interface, peer, ASN, or priority"
            )
        _log(f"OK BGP peer: {tunnel.bgp_peer} priority={tunnel.advertised_priority}")
        return
    if status:
        _log(f"MISSING BGP peer: {tunnel.bgp_peer}")
    elif dry_run:
        _log(f"DRY-RUN create BGP peer: {tunnel.bgp_peer} priority={tunnel.advertised_priority}")
    else:
        cloud.mutate(
            [
                "compute",
                "routers",
                "add-bgp-peer",
                plan.cloud_router,
                f"--region={plan.region}",
                f"--project={plan.project}",
                f"--peer-name={tunnel.bgp_peer}",
                f"--interface={tunnel.router_interface}",
                f"--peer-ip-address={tunnel.nebius_ip}",
                f"--peer-asn={plan.nebius_asn}",
                f"--advertised-route-priority={tunnel.advertised_priority}",
            ],
            label=f"create BGP peer {tunnel.bgp_peer}",
        )


def print_plan(plan: Plan) -> None:
    print("Resolved VM-HA peer plan:")
    print(f"  Project/region: {plan.project} / {plan.region}")
    print(f"  Network:        {plan.network}")
    print(f"  HA gateway:     {plan.vpn_gateway}")
    print(f"  Cloud Router:   {plan.cloud_router} (ASN {plan.cloud_router_asn})")
    print(f"  External peers: {plan.external_gateway_a}, {plan.external_gateway_b}")
    print(f"  VM0 priority:   {plan.active_priority} (preferred)")
    print(f"  VM1 priority:   {plan.passive_priority} (standby)")
    for tunnel in plan.tunnels:
        print(
            f"  - {tunnel.name}: GCP if{tunnel.gcp_interface} -> "
            f"{tunnel.external_gateway} if{tunnel.peer_interface} -> VM{tunnel.vm_index}, "
            f"{tunnel.cidr}, {tunnel.ha_role}, priority {tunnel.advertised_priority}"
        )


def _gateway_ips(plan: Plan, cloud: GCloud) -> tuple[str, str]:
    gateway = cloud.describe_json("vpn-gateways", plan.vpn_gateway, regional=True) or {}
    interfaces = _numbered_ip_interfaces(
        gateway,
        "vpnInterfaces",
        address_field="ipAddress",
        label=f"HA VPN gateway {plan.vpn_gateway}",
    )
    return (
        interfaces.get(0, "<pending-gcp-interface-0>"),
        interfaces.get(1, "<pending-gcp-interface-1>"),
    )


def print_connection_block(plan: Plan, cloud: GCloud) -> None:
    gcp_ips = _gateway_ips(plan, cloud)
    print("\nNebius connection block (secret references only):")
    print(f'  - name: "{plan.connection}"')
    print('    vendor: "gcp"')
    print('    routing_mode: "bgp"')
    print("    bgp:")
    print("      enabled: true")
    print(f"      remote_asn: {plan.cloud_router_asn}")
    print("      advertise_local_prefixes: true")
    print("    tunnels:")
    for index in (0, 3, 2, 1):
        tunnel = plan.tunnels[index]
        print(f'      - name: "{tunnel.name}"')
        print(f"        gateway_instance_index: {tunnel.vm_index}")
        print("        local_public_ip_index: 0")
        print(f'        ha_role: "{tunnel.ha_role}"')
        print(f'        remote_public_ip: "{gcp_ips[tunnel.gcp_interface]}"')
        print(f'        psk: "${{{tunnel.psk_env_name}}}"')
        print(f'        inner_cidr: "{tunnel.cidr}"')
        print(f'        inner_local_ip: "{tunnel.nebius_ip}"')
        print(f'        inner_remote_ip: "{tunnel.gcp_ip}"')


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Converge one GCP HA VPN gateway and Cloud Router to two Nebius VM peers "
            "using four tunnels and member-grouped BGP priorities."
        )
    )
    result.add_argument("--vm-ha-peer", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--connection-name", required=True)
    result.add_argument("--gcp-project-id", required=True)
    result.add_argument("--region", required=True)
    result.add_argument("--network", default=os.environ.get("NETWORK", "default"))
    result.add_argument(
        "--vpn-gateway-name",
        default=os.environ.get("VPN_GATEWAY_NAME", "ha-gw-nebius"),
    )
    result.add_argument(
        "--cloud-router-name",
        default=os.environ.get("CLOUD_ROUTER_NAME", "cr-nebius-ha"),
    )
    result.add_argument(
        "--cloud-router-asn",
        default=os.environ.get("CLOUD_ROUTER_ASN", "64514"),
    )
    result.add_argument("--nebius-active-public-ip", required=True)
    result.add_argument("--nebius-passive-public-ip", required=True)
    result.add_argument("--nebius-asn", required=True)
    result.add_argument(
        "--active-priority",
        default=os.environ.get("ACTIVE_ADVERTISED_ROUTE_PRIORITY", "0"),
    )
    result.add_argument(
        "--passive-priority",
        default=os.environ.get("PASSIVE_ADVERTISED_ROUTE_PRIORITY", "100"),
    )
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--status", action="store_true")
    result.add_argument("--yes", action="store_true")
    result.add_argument(
        "--psk-source-config",
        help=(
            "reuse exactly four tunnel PSKs from a private mode-0600 VPNGW YAML config; "
            "cannot be combined with planned PSK environment values"
        ),
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.status and args.dry_run:
            raise HelperError("--status and --dry-run are mutually exclusive")
        plan = build_plan(args)
        psks = {} if args.status or args.dry_run else _resolve_psks(plan, args.psk_source_config)
        cloud = GCloud(plan, secret_values=tuple(psks.values()))
        account = cloud.require_auth()
        print(f"Using active gcloud account {account}")
        print_plan(plan)
        if not args.status and not args.dry_run and not args.yes:
            reply = input(f"\nApply this plan in GCP project {plan.project}? [y/N] ")
            if reply.strip().lower() not in {"y", "yes"}:
                print("Cancelled. No GCP resources were changed.")
                return 1

        _validate_gateway(plan, cloud, dry_run=args.dry_run, status=args.status)
        _validate_router(plan, cloud, dry_run=args.dry_run, status=args.status)
        _validate_external_gateway(
            plan,
            cloud,
            plan.external_gateway_a,
            (plan.active_public_ip, plan.passive_public_ip),
            dry_run=args.dry_run,
            status=args.status,
        )
        _validate_external_gateway(
            plan,
            cloud,
            plan.external_gateway_b,
            (plan.passive_public_ip, plan.active_public_ip),
            dry_run=args.dry_run,
            status=args.status,
        )
        for tunnel in plan.tunnels:
            _validate_tunnel(
                plan,
                cloud,
                tunnel,
                dry_run=args.dry_run,
                status=args.status,
                psks=psks,
            )
        for tunnel in plan.tunnels:
            _validate_router_interface(
                plan, cloud, tunnel, dry_run=args.dry_run, status=args.status
            )
        for tunnel in plan.tunnels:
            _validate_bgp_peer(plan, cloud, tunnel, dry_run=args.dry_run, status=args.status)

        if args.status:
            print("\nStatus inspection complete; no GCP resources or local gcloud config changed.")
        elif args.dry_run:
            print("\nDry-run complete; no GCP resources changed.")
        else:
            print("\nGCP four-tunnel VM-HA peer fixture converged.")
        print_connection_block(plan, cloud)
        return 0
    except (HelperError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
