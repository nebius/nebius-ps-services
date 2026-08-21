#!/usr/bin/env python3
"""Plan and converge an isolated two-path GCP Classic static VPN fixture."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

RESOURCE_NAME = re.compile(r"^[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
GCLOUD_TIMEOUT_SECONDS = 600


class HelperError(RuntimeError):
    """One bounded, secret-free helper failure."""


@dataclass(frozen=True)
class RoutePlan:
    name: str
    prefix: str
    priority: int


@dataclass(frozen=True)
class ClassicPath:
    label: str
    vm_index: int
    peer_public_ip: str
    address: str
    gateway: str
    forwarding_esp: str
    forwarding_udp500: str
    forwarding_udp4500: str
    tunnel: str
    inner_cidr: str
    inner_local_ip: str
    inner_remote_ip: str
    psk_env_name: str
    routes: tuple[RoutePlan, ...]


@dataclass(frozen=True)
class Plan:
    project: str
    region: str
    network: str
    connection: str
    gcp_prefixes: tuple[str, ...]
    nebius_prefixes: tuple[str, ...]
    paths: tuple[ClassicPath, ClassicPath]


def _basename(value: object) -> str:
    return str(value or "").rstrip("/").rsplit("/", 1)[-1]


def _resource_name(value: str, label: str) -> str:
    if not RESOURCE_NAME.fullmatch(value):
        raise HelperError(f"{label} is not a valid GCP resource name: {value}")
    return value


def _derived_name(base: str, suffix: str) -> str:
    available = 63 - len(suffix) - 1
    prefix = base[:available].rstrip("-") or "vpngw"
    return _resource_name(f"{prefix}-{suffix}", "generated resource name")


def _public_ip(value: str, label: str) -> str:
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError as error:
        raise HelperError(f"{label} must be a valid IPv4 address") from error


def _prefixes(values: Sequence[str], label: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            network = ipaddress.IPv4Network(value, strict=True)
        except ValueError as error:
            raise HelperError(f"{label} contains an invalid IPv4 prefix: {value}") from error
        rendered = str(network)
        if rendered not in seen:
            seen.add(rendered)
            normalized.append(rendered)
    if not normalized:
        raise HelperError(f"at least one {label} is required")
    return tuple(normalized)


def _inner_link(value: str) -> tuple[str, str, str]:
    try:
        network = ipaddress.IPv4Network(value, strict=True)
    except ValueError as error:
        raise HelperError(f"invalid static XFRM inner range: {value}") from error
    if network.prefixlen != 30 or not network.subnet_of(ipaddress.IPv4Network("169.254.0.0/16")):
        raise HelperError(f"static XFRM inner range must be an APIPA /30: {value}")
    hosts = tuple(network.hosts())
    return str(network), str(hosts[0]), str(hosts[1])


def _integer(value: str | int, label: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise HelperError(f"{label} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise HelperError(f"{label} must be from {minimum} through {maximum}")
    return parsed


def _psk_env(connection: str, label: str) -> str:
    key = f"PSK_{label.upper()}_ENV_NAME"
    token = connection.replace("-", "_").upper()
    value = os.environ.get(key, f"GCP_{token}_CLASSIC_{label.upper()}_PSK")
    if not ENV_NAME.fullmatch(value):
        raise HelperError(f"{key} is not a valid environment variable name")
    return value


def _psk(value: str, label: str) -> str:
    if not 1 <= len(value) <= 256 or any(not 0x20 <= ord(character) <= 0x7E for character in value):
        raise HelperError(f"{label} must contain 1 through 256 printable ASCII characters")
    return value


def build_plan(args: argparse.Namespace) -> Plan:
    connection = _resource_name(args.connection_name, "connection name")
    network = _resource_name(args.network, "network")
    gcp_prefixes = _prefixes(args.gcp_prefix, "GCP remote prefix")
    nebius_prefixes = _prefixes(args.nebius_prefix, "Nebius local prefix")
    active_priority = _integer(args.active_priority, "active route priority", 0, 65535)
    passive_priority = _integer(args.passive_priority, "passive route priority", 0, 65535)
    if active_priority >= passive_priority:
        raise HelperError("active route priority must be numerically lower than passive priority")
    public_ips = (
        _public_ip(args.nebius_active_public_ip, "configured-active Nebius public IP"),
        _public_ip(args.nebius_passive_public_ip, "configured-passive Nebius public IP"),
    )
    inner_links = (_inner_link(args.inner_cidr_a), _inner_link(args.inner_cidr_b))
    if inner_links[0][0] == inner_links[1][0]:
        raise HelperError("the two static XFRM inner ranges must be distinct")

    paths: list[ClassicPath] = []
    for index, label in enumerate(("a", "b")):
        priority = active_priority if index == 0 else passive_priority
        routes = tuple(
            RoutePlan(
                name=_derived_name(connection, f"route-{label}-{route_index}"),
                prefix=prefix,
                priority=priority,
            )
            for route_index, prefix in enumerate(nebius_prefixes, start=1)
        )
        inner_cidr, inner_local, inner_remote = inner_links[index]
        paths.append(
            ClassicPath(
                label=label,
                vm_index=index,
                peer_public_ip=public_ips[index],
                address=_derived_name(connection, f"ip-{label}"),
                gateway=_derived_name(connection, f"gw-{label}"),
                forwarding_esp=_derived_name(connection, f"fr-{label}-esp"),
                forwarding_udp500=_derived_name(connection, f"fr-{label}-udp500"),
                forwarding_udp4500=_derived_name(connection, f"fr-{label}-udp4500"),
                tunnel=_derived_name(connection, f"tunnel-{label}"),
                inner_cidr=inner_cidr,
                inner_local_ip=inner_local,
                inner_remote_ip=inner_remote,
                psk_env_name=_psk_env(connection, label),
                routes=routes,
            )
        )
    return Plan(
        project=str(args.gcp_project_id),
        region=str(args.region),
        network=network,
        connection=connection,
        gcp_prefixes=gcp_prefixes,
        nebius_prefixes=nebius_prefixes,
        paths=(paths[0], paths[1]),
    )


class GCloud:
    def __init__(self, plan: Plan, *, secret_values: Sequence[str] = ()) -> None:
        self.plan = plan
        self.secret_values = frozenset(value for value in secret_values if value)

    def run(
        self,
        arguments: Sequence[str],
        *,
        label: str,
        check: bool = True,
        secret_flags: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        read_descriptor: int | None = None
        command = ["gcloud", *arguments]
        child_environment = os.environ.copy()
        for path in self.plan.paths:
            child_environment.pop(path.psk_env_name, None)
        if self.secret_values:
            child_environment = {
                name: value
                for name, value in child_environment.items()
                if value not in self.secret_values
            }
        options: dict[str, Any] = {"env": child_environment}
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
            options["pass_fds"] = (read_descriptor,)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=GCLOUD_TIMEOUT_SECONDS,
                **options,
            )
        except subprocess.TimeoutExpired as error:
            raise HelperError(f"{label} timed out") from error
        finally:
            if read_descriptor is not None:
                os.close(read_descriptor)
        if check and result.returncode != 0:
            detail = result.stderr or result.stdout or "gcloud failed"
            for secret in self.secret_values:
                detail = detail.replace(secret, "<redacted>")
            raise HelperError(f"{label} failed: {detail.strip()}")
        return result

    def require_auth(self) -> None:
        result = self.run(
            ["auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
            label="gcloud authentication check",
        )
        if not result.stdout.strip():
            raise HelperError("no active gcloud account; run 'gcloud auth login' explicitly")

    def describe(self, kind: str, name: str, *, regional: bool) -> dict[str, Any] | None:
        arguments = ["compute", kind, "describe", name]
        if regional:
            arguments.append(f"--region={self.plan.region}")
        arguments.extend((f"--project={self.plan.project}", "--format=json"))
        result = self.run(arguments, label=f"describe {kind} {name}", check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
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
        secret_flags: dict[str, str] | None = None,
    ) -> None:
        self.run(arguments, label=label, secret_flags=secret_flags)


ResourceKey = tuple[str, str]


def _ports(value: object, port_range: object = None) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value}
    if isinstance(port_range, str):
        lower, separator, upper = port_range.partition("-")
        if separator and lower == upper and lower.isdecimal():
            return {lower}
    return set()


def _inspect(plan: Plan, cloud: GCloud) -> dict[ResourceKey, dict[str, Any] | None]:
    resources: dict[ResourceKey, dict[str, Any] | None] = {}
    for path in plan.paths:
        address = cloud.describe("addresses", path.address, regional=True)
        resources[("addresses", path.address)] = address
        if address is not None:
            if not (
                address.get("addressType") == "EXTERNAL"
                and address.get("networkTier") == "PREMIUM"
                and _basename(address.get("region")) in {"", plan.region}
            ):
                raise HelperError(f"address {path.address} has a foreign shape")
            _public_ip(str(address.get("address") or ""), f"address {path.address}")

        gateway = cloud.describe("target-vpn-gateways", path.gateway, regional=True)
        resources[("target-vpn-gateways", path.gateway)] = gateway
        if gateway is not None and _basename(gateway.get("network")) != plan.network:
            raise HelperError(f"Classic VPN gateway {path.gateway} belongs to another network")

        forwarding_specs: tuple[tuple[str, str, set[str]], ...] = (
            (path.forwarding_esp, "ESP", set()),
            (path.forwarding_udp500, "UDP", {"500"}),
            (path.forwarding_udp4500, "UDP", {"4500"}),
        )
        for name, protocol, ports in forwarding_specs:
            rule = cloud.describe("forwarding-rules", name, regional=True)
            resources[("forwarding-rules", name)] = rule
            if rule is None:
                continue
            observed_ip = str(rule.get("IPAddress") or rule.get("ipAddress") or "")
            expected_ip = str((address or {}).get("address") or "")
            if not (
                _basename(rule.get("target")) == path.gateway
                and str(rule.get("IPProtocol") or rule.get("ipProtocol") or "").upper() == protocol
                and _ports(rule.get("ports"), rule.get("portRange")) == ports
                and rule.get("networkTier") == "PREMIUM"
                and rule.get("loadBalancingScheme") == "EXTERNAL"
                and expected_ip
                and observed_ip == expected_ip
            ):
                raise HelperError(f"forwarding rule {name} has a foreign binding")

        tunnel = cloud.describe("vpn-tunnels", path.tunnel, regional=True)
        resources[("vpn-tunnels", path.tunnel)] = tunnel
        if tunnel is not None:
            local_selectors = set(tunnel.get("localTrafficSelector") or ())
            remote_selectors = set(tunnel.get("remoteTrafficSelector") or ())
            if not (
                _basename(tunnel.get("targetVpnGateway")) == path.gateway
                and str(tunnel.get("peerIp") or "") == path.peer_public_ip
                and int(tunnel.get("ikeVersion") or 0) == 2
                and local_selectors == {"0.0.0.0/0"}
                and remote_selectors == {"0.0.0.0/0"}
            ):
                raise HelperError(f"Classic VPN tunnel {path.tunnel} has a foreign binding")

        for route in path.routes:
            observed = cloud.describe("routes", route.name, regional=False)
            resources[("routes", route.name)] = observed
            if observed is not None and not (
                _basename(observed.get("network")) == plan.network
                and str(observed.get("destRange") or "") == route.prefix
                and _basename(observed.get("nextHopVpnTunnel")) == path.tunnel
                and _basename(observed.get("nextHopVpnTunnelRegion")) in {"", plan.region}
                and int(observed.get("priority") or 0) == route.priority
            ):
                raise HelperError(f"static route {route.name} has a foreign binding")
    return resources


def _missing(resources: dict[ResourceKey, dict[str, Any] | None], kind: str, name: str) -> bool:
    return resources[(kind, name)] is None


def _resolve_psks(
    plan: Plan, resources: dict[ResourceKey, dict[str, Any] | None]
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for path in plan.paths:
        if not _missing(resources, "vpn-tunnels", path.tunnel):
            continue
        value = os.environ.get(path.psk_env_name, "")
        if not value:
            raise HelperError(f"missing secret input {path.psk_env_name} for {path.tunnel}")
        resolved[path.psk_env_name] = _psk(value, f"secret input {path.psk_env_name}")
    return resolved


def _create(
    plan: Plan,
    cloud: GCloud,
    resources: dict[ResourceKey, dict[str, Any] | None],
    psks: dict[str, str],
) -> None:
    for path in plan.paths:
        if _missing(resources, "addresses", path.address):
            cloud.mutate(
                [
                    "compute",
                    "addresses",
                    "create",
                    path.address,
                    f"--region={plan.region}",
                    f"--project={plan.project}",
                    "--network-tier=PREMIUM",
                ],
                label=f"create address {path.address}",
            )
        if _missing(resources, "target-vpn-gateways", path.gateway):
            cloud.mutate(
                [
                    "compute",
                    "target-vpn-gateways",
                    "create",
                    path.gateway,
                    f"--region={plan.region}",
                    f"--project={plan.project}",
                    f"--network={plan.network}",
                ],
                label=f"create Classic VPN gateway {path.gateway}",
            )
        forwarding_specs = (
            (path.forwarding_esp, "ESP", None),
            (path.forwarding_udp500, "UDP", "500"),
            (path.forwarding_udp4500, "UDP", "4500"),
        )
        for name, protocol, ports in forwarding_specs:
            if not _missing(resources, "forwarding-rules", name):
                continue
            arguments = [
                "compute",
                "forwarding-rules",
                "create",
                name,
                f"--region={plan.region}",
                f"--project={plan.project}",
                f"--address={path.address}",
                f"--target-vpn-gateway={path.gateway}",
                f"--ip-protocol={protocol}",
                "--network-tier=PREMIUM",
                "--load-balancing-scheme=EXTERNAL",
            ]
            if ports is not None:
                arguments.append(f"--ports={ports}")
            cloud.mutate(arguments, label=f"create forwarding rule {name}")
        if _missing(resources, "vpn-tunnels", path.tunnel):
            secret = psks[path.psk_env_name]
            cloud.mutate(
                [
                    "compute",
                    "vpn-tunnels",
                    "create",
                    path.tunnel,
                    f"--region={plan.region}",
                    f"--project={plan.project}",
                    f"--peer-address={path.peer_public_ip}",
                    f"--target-vpn-gateway={path.gateway}",
                    "--ike-version=2",
                    "--local-traffic-selector=0.0.0.0/0",
                    "--remote-traffic-selector=0.0.0.0/0",
                ],
                label=f"create Classic VPN tunnel {path.tunnel}",
                secret_flags={"--shared-secret": secret},
            )

    for path in plan.paths:
        for route in path.routes:
            if _missing(resources, "routes", route.name):
                cloud.mutate(
                    [
                        "compute",
                        "routes",
                        "create",
                        route.name,
                        f"--project={plan.project}",
                        f"--network={plan.network}",
                        f"--destination-range={route.prefix}",
                        f"--next-hop-vpn-tunnel={path.tunnel}",
                        f"--next-hop-vpn-tunnel-region={plan.region}",
                        f"--priority={route.priority}",
                    ],
                    label=f"create static route {route.name}",
                )


def print_plan(plan: Plan, resources: dict[ResourceKey, dict[str, Any] | None]) -> None:
    print("Resolved isolated Classic static VM-HA plan:")
    print(f"  Project/region: {plan.project} / {plan.region}")
    print(f"  Network:        {plan.network}")
    print(f"  Connection:     {plan.connection}")
    for path in plan.paths:
        state = "present" if resources.get(("vpn-tunnels", path.tunnel)) else "missing"
        print(
            f"  - VM{path.vm_index}: {path.gateway}, {path.tunnel}, "
            f"{len(path.routes)} static route(s), tunnel {state}"
        )


def print_connection(plan: Plan, resources: dict[ResourceKey, dict[str, Any] | None]) -> None:
    print("\nNebius static-only connection block (secret references only):")
    print(f'  - name: "{plan.connection}"')
    print('    vendor: "gcp"')
    print('    routing_mode: "static"')
    print("    remote_prefixes:")
    for prefix in plan.gcp_prefixes:
        print(f'      - "{prefix}"')
    print("    tunnels:")
    for path in plan.paths:
        address = resources.get(("addresses", path.address)) or {}
        remote_ip = str(address.get("address") or f"<pending-{path.address}>")
        print(f'      - name: "{path.tunnel}"')
        print(f"        gateway_instance_index: {path.vm_index}")
        print("        local_public_ip_index: 0")
        print('        ha_role: "active"')
        print(f'        remote_public_ip: "{remote_ip}"')
        print(f'        psk: "${{{path.psk_env_name}}}"')
        print(f'        inner_cidr: "{path.inner_cidr}"')
        print(f'        inner_local_ip: "{path.inner_local_ip}"')
        print(f'        inner_remote_ip: "{path.inner_remote_ip}"')


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Converge two isolated one-to-one GCP Classic VPN paths for a static-only "
            "Nebius two-member VM-HA gateway."
        )
    )
    result.add_argument("--classic-vm-ha-peer", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--connection-name", required=True)
    result.add_argument("--gcp-project-id", required=True)
    result.add_argument("--region", required=True)
    result.add_argument("--network", required=True)
    result.add_argument("--nebius-active-public-ip", required=True)
    result.add_argument("--nebius-passive-public-ip", required=True)
    result.add_argument("--gcp-prefix", action="append", required=True)
    result.add_argument("--nebius-prefix", action="append", required=True)
    result.add_argument("--inner-cidr-a", default="169.254.240.0/30")
    result.add_argument("--inner-cidr-b", default="169.254.240.4/30")
    result.add_argument("--active-priority", default="1000")
    result.add_argument("--passive-priority", default="2000")
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--status", action="store_true")
    result.add_argument("--yes", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.status and args.dry_run:
            raise HelperError("--status and --dry-run are mutually exclusive")
        plan = build_plan(args)
        cloud = GCloud(plan)
        cloud.require_auth()
        resources = _inspect(plan, cloud)
        print_plan(plan, resources)
        missing = [key for key, value in resources.items() if value is None]
        if args.status:
            print_connection(plan, resources)
            print(
                f"\nStatus inspection complete: {len(missing)} resource(s) missing; no changes made."
            )
            return 0
        if args.dry_run:
            for kind, name in missing:
                print(f"DRY-RUN create {kind}: {name}")
            print_connection(plan, resources)
            print("\nDry-run complete; no GCP resources changed.")
            return 0
        psks = _resolve_psks(plan, resources)
        cloud = GCloud(plan, secret_values=tuple(psks.values()))
        if not args.yes:
            reply = input(
                f"\nApply this isolated Classic plan in GCP project {plan.project}? [y/N] "
            )
            if reply.strip().lower() not in {"y", "yes"}:
                print("Cancelled. No GCP resources were changed.")
                return 1
        _create(plan, cloud, resources, psks)
        final_resources = _inspect(plan, cloud)
        if any(value is None for value in final_resources.values()):
            raise HelperError("Classic VPN fixture did not converge to the complete planned graph")
        print_connection(plan, final_resources)
        print("\nClassic static VM-HA fixture converged; resources were retained for review.")
        return 0
    except HelperError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
