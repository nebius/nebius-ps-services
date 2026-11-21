"""Read-only proof that one DHCP APIPA route cannot divert BGP peer paths."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

APIPA = ipaddress.IPv4Network("169.254.0.0/16")


@dataclass(frozen=True)
class PeerRoute:
    peer: str
    source: str
    interface: str
    if_id: int


class Result(Protocol):
    @property
    def returncode(self) -> int: ...

    @property
    def stdout(self) -> str: ...


def _address(value: Any) -> str:
    return str(ipaddress.IPv4Address(bytes(value) if isinstance(value, list) else value))


def _configured(row: dict[str, Any]) -> bool:
    states = set(str(row.get("ConfigState", "")).split(","))
    return "configured" in states and states <= {"configured", "configuring"}


def _main_unicast(row: dict[str, Any]) -> bool:
    return bool(
        row.get("table", "main") in ("main", 254)
        and row.get("type", "unicast") == "unicast"
        and not row.get("flags")
        and not any(key in row for key in ("nexthops", "nhid", "encap", "via"))
    )


def _destination(row: dict[str, Any]) -> ipaddress.IPv4Network:
    return ipaddress.IPv4Network(str(row.get("dst", "")).replace("default", "0.0.0.0/0"))


def _dhcp_owned(data: Any, route: dict[str, Any], interface: str) -> bool:
    if not (
        isinstance(data, dict)
        and data.get("Name") == interface
        and data.get("Type") == "ether"
        and data.get("Kind") in (None, "", "veth")
        and not data.get("MasterInterfaceIndex")
        and _main_unicast(route)
        and _destination(route) == APIPA
        and route.get("dev") == interface
        and route.get("protocol") in ("dhcp", 16)
        and isinstance(route.get("gateway"), str)
        and isinstance(route.get("prefsrc"), str)
        and type(route.get("metric")) is int
    ):
        return False
    owners = [
        row
        for row in data.get("Routes", [])
        if row.get("Family") == 2
        and row.get("DestinationPrefixLength") == 16
        and _address(row.get("Destination")) == str(APIPA.network_address)
        and row.get("Table") == 254
        and row.get("Protocol") == 16
        and row.get("Type") == 1
        and row.get("TOS", 0) == 0
        and row.get("Flags", 0) == 0
        and row.get("ConfigSource") == "DHCPv4"
        and _configured(row)
        and row.get("Priority") == route["metric"]
        and _address(row.get("Gateway")) == route["gateway"]
        and _address(row.get("PreferredSource")) == route["prefsrc"]
    ]
    if len(owners) != 1:
        return False
    provider = _address(owners[0].get("ConfigProvider"))
    return any(
        row.get("Family") == 2
        and row.get("ConfigSource") == "DHCPv4"
        and _configured(row)
        and _address(row.get("Address")) == route["prefsrc"]
        and _address(row.get("ConfigProvider")) == provider
        for row in data.get("Addresses", [])
    )


def harmless_dhcp_route(
    *,
    peers: tuple[PeerRoute, ...],
    configured_peers: frozenset[str],
    interface: str,
    protected_prefixes: Iterable[str],
    routes: Any,
    links: Any,
    run: Callable[..., Result],
    ip_command: str,
    networkctl_command: str,
) -> bool:
    """No effects, grace, inferred ownership, or forced output-interface lookup."""

    def read(*argv: str) -> Any:
        result = run(*argv)
        if result.returncode:
            raise ValueError("native route observation unavailable")
        return json.loads(result.stdout)

    try:
        if (
            not peers
            or len({peer.peer for peer in peers}) != len(peers)
            or {peer.peer for peer in peers} != configured_peers
            or any(ipaddress.IPv4Address(peer.peer) not in APIPA for peer in peers)
            or any(ipaddress.IPv4Network(prefix).overlaps(APIPA) for prefix in protected_prefixes)
            or not isinstance(routes, list)
            or not all(isinstance(row, dict) for row in routes)
            or not isinstance(links, list)
            or not all(isinstance(row, dict) for row in links)
        ):
            return False
        broad = read(ip_command, "-j", "-4", "route", "show", "table", "main", "exact", str(APIPA))
        if broad == []:
            return True  # Concurrent product cleanup restored the ordinary clean condition.
        if not isinstance(broad, list) or len(broad) != 1 or not isinstance(broad[0], dict):
            return False
        network = read(networkctl_command, "status", "--json=short", "--no-pager", interface)
        if not _dhcp_owned(network, broad[0], interface):
            return False
        # A generic route-get cannot model TCP ports, FRR uid, marks, or other
        # policy selectors. Admit only the canonical RPDB for this exception.
        rules = read(ip_command, "-j", "-4", "rule", "show")
        if rules != [
            {"priority": 0, "src": "all", "table": "local"},
            {"priority": 32766, "src": "all", "table": "main"},
            {"priority": 32767, "src": "all", "table": "default"},
        ]:
            return False
        addresses = read(ip_command, "-j", "-4", "address", "show")
        if not isinstance(addresses, list) or not all(isinstance(row, dict) for row in addresses):
            return False
        for peer in peers:
            matching = [
                row
                for row in routes
                if row.get("table", "main") in ("main", 254)
                and _destination(row) == ipaddress.IPv4Network(peer.peer + "/32")
            ]
            if not (
                len(matching) == 1
                and _main_unicast(matching[0])
                and matching[0].get("dev") == peer.interface
                and "gateway" not in matching[0]
            ):
                return False
            devices = [row for row in links if row.get("ifname") == peer.interface]
            if len(devices) != 1 or "UP" not in devices[0].get("flags", []):
                return False
            info = devices[0].get("linkinfo", {})
            identity = info.get("info_data", {}).get("if_id")
            identity = int(identity, 0) if isinstance(identity, str) else identity
            if (
                info.get("info_kind") != "xfrm"
                or type(identity) is not int
                or identity != peer.if_id
            ):
                return False
            assigned = [row for row in addresses if row.get("ifname") == peer.interface]
            if len(assigned) != 1 or not any(
                addr.get("family") == "inet" and addr.get("local") == peer.source
                for addr in assigned[0].get("addr_info", [])
            ):
                return False
            selected = read(ip_command, "-j", "-4", "route", "get", peer.peer, "from", peer.source)
            if not (
                isinstance(selected, list)
                and len(selected) == 1
                and isinstance(selected[0], dict)
                and _main_unicast(selected[0])
                and _destination(selected[0]) == ipaddress.IPv4Network(peer.peer + "/32")
                and selected[0].get("from") == peer.source
                and selected[0].get("dev") == peer.interface
                and "gateway" not in selected[0]
            ):
                return False
        return True
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError):
        return False
