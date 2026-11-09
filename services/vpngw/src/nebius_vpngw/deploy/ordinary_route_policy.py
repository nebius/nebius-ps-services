"""Frozen destination intent for ordinary VPC routes.

BGP observations select additions inside the approved policy. Runtime withdrawal
requires three complete absence observations. This module has no cloud effects.
"""

from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass


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

    @classmethod
    def parse(cls, value: dict) -> Policy:
        """Read generated policy without accepting extra ownership or peer fields."""
        try:
            if (
                set(value) != {"connections", "excluded_prefixes", "summarize"}
                or type(value["summarize"]) is not bool
            ):
                raise ValueError
            connections = []
            for item in value["connections"]:
                if (
                    set(item)
                    != {"name", "mode", "instance_index", "peers", "prefixes", "max_prefixes"}
                    or not isinstance(item["name"], str)
                    or not item["name"]
                    or item["mode"] not in {"static", "bgp"}
                    or type(item["instance_index"]) is not int
                    or item["instance_index"] < 0
                    or type(item["max_prefixes"]) is not int
                    or not 1 <= item["max_prefixes"] <= 10000
                ):
                    raise ValueError
                peers = tuple(item["peers"])
                prefixes = tuple(item["prefixes"])
                if (
                    peers != tuple(sorted(set(peers)))
                    or any(str(ipaddress.IPv4Address(peer)) != peer for peer in peers)
                    or item["mode"] == "bgp"
                    and not peers
                    or prefixes != tuple(sorted({prefix(p) for p in prefixes}))
                ):
                    raise ValueError
                connections.append(
                    Connection(
                        item["name"],
                        item["mode"],
                        item["instance_index"],
                        peers,
                        prefixes,
                        item["max_prefixes"],
                    )
                )
            excluded = tuple(value["excluded_prefixes"])
            if (
                not excluded
                or excluded != tuple(sorted({prefix(p) for p in excluded}))
                or len({c.name for c in connections}) != len(connections)
                or connections != sorted(connections, key=lambda c: c.name)
            ):
                raise ValueError
            return cls(tuple(connections), excluded, value["summarize"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError("Invalid generated ordinary route policy") from None

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
    from ..config_loader import connection_static_remote_prefixes
    from .route_manager import RouteManager

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


@dataclass(frozen=True)
class AbsenceEvidence:
    """Three consecutive complete observations under one configuration and boot."""

    binding: str
    policy: str
    selected: str
    absent: frozenset[str]
    observations: int

    def admits(self, policy: Policy, learned: dict[str, tuple[str, ...]], destination: str) -> bool:
        from ..apply_approval import digest

        network = ipaddress.IPv4Network(destination)
        return (
            self.observations >= 3
            and self.policy == digest(policy.mapping())
            and self.selected == digest(policy.select(learned))
            and destination in self.absent
            and destination not in policy.select(learned)
            and any(
                c.mode == "bgp"
                and policy.retains(destination, c.instance_index)
                and (
                    not c.prefixes
                    or any(network.overlaps(ipaddress.IPv4Network(p)) for p in c.prefixes)
                )
                for c in policy.connections
            )
            and not any(c.mode == "static" and c.accepts(destination) for c in policy.connections)
        )


class AbsenceObservations:
    """Unknown evidence or a changed snapshot resets the withdrawal window.

    Deliberately process-owned: reboot, daemon restart and generation change all
    require three fresh complete samples; persisted counts confer no authority.
    """

    def __init__(self) -> None:
        self.signature: str | None = None
        self.count = 0

    def unknown(self) -> None:
        self.signature, self.count = None, 0

    def observe(
        self,
        binding: str,
        policy: Policy,
        learned: dict[str, tuple[str, ...]],
        admitted: frozenset[str],
    ) -> AbsenceEvidence:
        from ..apply_approval import digest

        try:
            selected = policy.select(learned)
            missing = admitted - selected.keys()
            signature = digest([binding, policy.mapping(), selected, sorted(admitted)])
        except (ValueError, RuntimeError, TypeError, KeyError):
            self.unknown()
            raise
        self.count = min(3, self.count + 1) if signature == self.signature else 1
        self.signature = signature
        return AbsenceEvidence(
            binding,
            digest(policy.mapping()),
            digest(selected),
            frozenset(missing),
            self.count,
        )
