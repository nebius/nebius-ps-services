"""Frozen destination intent for ordinary VPC routes.

BGP observations select additions inside the approved policy. They never retire
previously admitted destinations. This module has no cloud or guest effects.
"""

from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass

from ..config_loader import connection_static_remote_prefixes
from .route_manager import RouteManager


def prefix(value: str) -> str:
    network = ipaddress.ip_network(value, strict=True)
    if network.version != 4:
        raise ValueError("Ordinary VPC routes require canonical IPv4 prefixes")
    return str(network)


@dataclass(frozen=True)
class Connection:
    name: str
    mode: str
    instance_index: int
    peers: tuple[str, ...]
    prefixes: tuple[str, ...]
    max_prefixes: int

    def accepts(self, destination: str) -> bool:
        destination = prefix(destination)
        if self.mode == "static":
            return destination in self.prefixes
        network = ipaddress.IPv4Network(destination)
        return not self.prefixes or any(
            network.subnet_of(ipaddress.IPv4Network(allowed)) for allowed in self.prefixes
        )


@dataclass(frozen=True)
class Policy:
    connections: tuple[Connection, ...]
    excluded_prefixes: tuple[str, ...]
    summarize: bool = False

    def mapping(self) -> dict:
        return asdict(self)

    @property
    def dynamic(self) -> bool:
        return any(connection.mode == "bgp" for connection in self.connections)

    def excluded(self, destination: str) -> bool:
        network = ipaddress.IPv4Network(prefix(destination))
        return any(
            network.overlaps(ipaddress.IPv4Network(excluded)) for excluded in self.excluded_prefixes
        )

    def retains(self, destination: str, instance_index: int) -> bool:
        """Only authoritative configuration retirement can return false."""
        if self.excluded(destination):
            return False
        if self.summarize:
            permitted = [
                ipaddress.IPv4Network(p)
                for connection in self.connections
                if connection.instance_index == instance_index
                for p in connection.prefixes
            ]
            if ipaddress.IPv4Network(destination) in ipaddress.collapse_addresses(permitted):
                return True
        return any(
            connection.instance_index == instance_index and connection.accepts(destination)
            for connection in self.connections
        )

    def select(self, learned: dict[str, tuple[str, ...]]) -> dict[str, int]:
        expected = {connection.name for connection in self.connections if connection.mode == "bgp"}
        if set(learned) != expected:
            raise RuntimeError("Route selection requires complete configured-peer observations")
        result: dict[str, int] = {}
        for connection in self.connections:
            observed = learned[connection.name] if connection.mode == "bgp" else connection.prefixes
            if not isinstance(observed, tuple) or any(
                not isinstance(item, str) for item in observed
            ):
                raise ValueError("Incomplete route selection evidence")
            destinations = {prefix(item) for item in observed}
            if len(destinations) > connection.max_prefixes:
                raise RuntimeError("Observed route count exceeds the approved connection limit")
            for destination in sorted(destinations):
                if not connection.accepts(destination) or self.excluded(destination):
                    continue
                previous = result.get(destination)
                if previous is not None and previous != connection.instance_index:
                    raise RuntimeError("Remote destination has multiple ordinary gateway owners")
                result[destination] = connection.instance_index
        if self.summarize:
            result = {
                str(prefix): index
                for index in sorted(set(result.values()))
                for prefix in ipaddress.collapse_addresses(
                    ipaddress.IPv4Network(p) for p, owner in result.items() if owner == index
                )
            }
        return dict(sorted(result.items()))


def freeze(local: dict, *, network_prefixes: tuple[str, ...], summarize: bool = False) -> Policy:
    """Reuse configured connection ownership and Static selector semantics."""
    manager = RouteManager(None)
    defaults = local.get("defaults", {})
    default_mode = manager._normalize_value(defaults.get("routing", {}).get("mode", "bgp"))
    limit = defaults.get("routing", {}).get("bgp", {}).get("max_prefixes", 1000)
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("Invalid configured BGP prefix limit")
    connections = []
    for item in local.get("connections", []):
        indices = manager._connection_instance_indices(item)
        if not indices:
            continue
        if len(indices) != 1:
            raise ValueError("Ordinary VPC routing requires one gateway owner per connection")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Route policy requires a named connection")
        index = indices[0]
        mode = manager._normalize_value(item.get("routing_mode") or default_mode)
        peers: tuple[str, ...] = ()
        if mode == "bgp":
            peers = tuple(sorted(manager._connection_peer_ips(item, instance_index=index)))
            if not peers or any(ipaddress.ip_address(peer).version != 4 for peer in peers):
                raise ValueError("BGP route policy requires exact configured IPv4 peers")
            configured = (
                item.get("remote_prefixes") or item.get("bgp", {}).get("remote_prefixes") or []
            )
            maximum = limit
        elif mode == "static":
            configured = connection_static_remote_prefixes(item, instance_index=index)
            maximum = max(1, len(configured))
        else:
            raise ValueError("Unsupported ordinary route mode")
        connections.append(
            Connection(
                name, mode, index, peers, tuple(sorted({prefix(p) for p in configured})), maximum
            )
        )
    if len({connection.name for connection in connections}) != len(connections):
        raise ValueError("Ambiguous connection identity in route policy")
    excluded = tuple(
        sorted(
            {
                prefix(p)
                for p in [*local.get("gateway", {}).get("local_prefixes", []), *network_prefixes]
            }
        )
    )
    if not excluded:
        raise ValueError("Route policy requires canonical local and VPC exclusions")
    return Policy(tuple(sorted(connections, key=lambda item: item.name)), excluded, summarize)
