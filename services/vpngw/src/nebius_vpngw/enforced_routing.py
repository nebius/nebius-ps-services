"""Pure, shared compilation of source-selected VPN routing intent.

No cloud or host effects belong here. A destination's table and owner must be
identical in renderers, observers, cloud plans and retirement records.
"""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from typing import Any

MAIN_TABLE = 254
TABLE_BASE = 10000
TABLE_COUNT = 50000


def prefixes(values: list[str]) -> tuple[str, ...]:
    if not values:
        raise ValueError("enforced routing requires a nonempty CIDR list")
    return tuple(sorted({str(ipaddress.IPv4Network(p, strict=True)) for p in values}))


@dataclass(frozen=True)
class Policy:
    connection: str
    source_subnets: tuple[str, ...]
    destinations: tuple[str, ...]
    table: int
    ordinary_shared: tuple[str, ...] = ()

    def mapping(self) -> dict[str, Any]:
        return {
            "source_subnets": list(self.source_subnets),
            "destinations": list(self.destinations),
        }


def compile_policies(cfg: dict[str, Any]) -> tuple[Policy, ...]:
    policies = []
    local = [
        ipaddress.IPv4Network(p, strict=False)
        for p in (cfg.get("gateway") or {}).get("local_prefixes", [])
        if ":" not in p
    ]
    gateway_subnet = ((cfg.get("gateway_group") or {}).get("subnet") or {}).get("cidr")
    forbidden = [
        ipaddress.IPv4Network(p)
        for p in ("127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4")
    ]
    for connection in cfg.get("connections", []):
        block = connection.get("enforced_routing")
        if block is None:
            continue
        if not isinstance(block, dict) or set(block) != {"source_subnets", "destinations"}:
            raise ValueError("enforced_routing requires source_subnets and destinations")
        sources, destinations = prefixes(block["source_subnets"]), prefixes(block["destinations"])
        for source in sources:
            net = ipaddress.IPv4Network(source)
            if not any(net.subnet_of(p) for p in local):
                raise ValueError(
                    "enforced source_subnets must be covered by gateway.local_prefixes"
                )
            if gateway_subnet and net.overlaps(ipaddress.IPv4Network(gateway_subnet)):
                raise ValueError("enforced source_subnets must exclude the gateway subnet")
        for destination in destinations:
            net = ipaddress.IPv4Network(destination)
            if any(net.subnet_of(p) for p in [*local, *forbidden]):
                raise ValueError("enforced destinations cannot select local or control networks")
        mode = connection.get("routing_mode") or cfg.get("defaults", {}).get("routing", {}).get(
            "mode", "bgp"
        )
        allowed = connection.get("remote_prefixes") or (connection.get("bgp") or {}).get(
            "remote_prefixes"
        )
        if mode == "bgp" and allowed and not set(destinations) <= set(prefixes(allowed)):
            raise ValueError("enforced BGP destinations must pass the existing exact import filter")
        name = connection["name"]
        table = (
            TABLE_BASE
            + int.from_bytes(hashlib.sha256(name.encode()).digest()[:4], "big") % TABLE_COUNT
        )
        policies.append(
            Policy(name, sources, destinations, table, ordinary_shared(connection, mode))
        )
    if len({p.table for p in policies}) != len(policies):
        raise ValueError("enforced connection names have conflicting table identities; rename one")
    for policy in policies:
        for connection in cfg.get("connections", []):
            if connection["name"] == policy.connection:
                continue
            other = set(
                connection.get("remote_prefixes")
                or (connection.get("bgp") or {}).get("remote_prefixes")
                or []
            )
            other.update((connection.get("enforced_routing") or {}).get("destinations", []))
            for tunnel in connection.get("tunnels", []):
                other.update((tunnel.get("static_routes") or {}).get("remote_prefixes", []))
            if any(
                ipaddress.IPv4Network(a).overlaps(ipaddress.ip_network(b, strict=False))
                for a in policy.destinations
                for b in other
                if ":" not in b
            ):
                raise ValueError("Overlapping destination ownership across connections")
    return tuple(sorted(policies, key=lambda p: p.connection))


def route_table(connection: dict, destination: str) -> int:
    block = connection.get("enforced_routing") or {}
    if str(ipaddress.IPv4Network(destination, strict=False)) not in block.get("destinations", []):
        return MAIN_TABLE
    return (
        TABLE_BASE
        + int.from_bytes(hashlib.sha256(connection["name"].encode()).digest()[:4], "big")
        % TABLE_COUNT
    )


def bgp_import_map_name(policy: Policy | None, preference: int) -> str:
    """One import binding contract for rendering and every readiness observer."""
    return (
        f"ENFORCED-{policy.table}-{preference}"
        if policy is not None
        else f"SET-LOCAL-PREF-{preference}"
    )


def bgp_policy_lines(policy: Policy, preference: int) -> list[str]:
    """Exact FRR match/action contract, shared with observed-policy validation."""
    prefix_list = f"ENFORCED-{policy.table}"
    name = bgp_import_map_name(policy, preference)
    return [
        *(
            f"ip prefix-list {prefix_list} seq {i * 10} permit {p}"
            for i, p in enumerate(policy.destinations, 1)
        ),
        f"route-map {name} permit 10",
        f" match ip address prefix-list {prefix_list}",
        f" set table {policy.table}",
        f" set local-preference {preference}",
        f"route-map {name} permit 20",
        f" set local-preference {preference}",
        "!",
    ]


def ordinary_prefixes(
    connection: dict, mode: str, *, instance_index: int | None = None
) -> tuple[str, ...]:
    """Explicit ordinary intent, without destinations introduced by enforcement."""
    if mode == "bgp":
        values = (
            connection.get("remote_prefixes")
            or (connection.get("bgp") or {}).get("remote_prefixes")
            or []
        )
    else:
        values = list(connection.get("remote_prefixes") or [])
        for tunnel in connection.get("tunnels", []):
            if tunnel.get("ha_role") == "disable":
                continue
            if (
                instance_index is not None
                and int(tunnel.get("gateway_instance_index", 0) or 0) != instance_index
            ):
                continue
            values.extend((tunnel.get("static_routes") or {}).get("remote_prefixes") or [])
    return tuple(sorted({str(ipaddress.IPv4Network(p, strict=False)) for p in values}))


def ordinary_shared(connection: dict, mode: str) -> tuple[str, ...]:
    """Preserve only explicitly configured finite ordinary destinations."""
    enforced = set((connection.get("enforced_routing") or {}).get("destinations", []))
    return tuple(sorted(set(ordinary_prefixes(connection, mode)) & enforced - {"0.0.0.0/0"}))


def static_destinations(connection: dict, tunnel: dict | None = None) -> list[str]:
    ordinary = ((tunnel or {}).get("static_routes") or {}).get("remote_prefixes")
    ordinary = ordinary or connection.get("remote_prefixes") or []
    return sorted(
        set(ordinary) | set((connection.get("enforced_routing") or {}).get("destinations", []))
    )


def resolve_sources(
    policies: tuple[Policy, ...], subnets: dict[str, tuple[str, ...]], *, gateway_subnet_id: str
) -> dict[str, tuple[str, ...]]:
    """Bind complete explicit CIDRs to actual subnet IDs, never overlap matching."""
    result = {}
    for policy in policies:
        wanted = set(policy.source_subnets)
        selected = []
        found: set[str] = set()
        for subnet_id, cidrs in subnets.items():
            actual = {str(ipaddress.IPv4Network(cidr, strict=True)) for cidr in cidrs}
            if not wanted & actual:
                continue
            if subnet_id == gateway_subnet_id or not actual <= wanted or found & actual:
                raise ValueError(
                    "enforced source selection must resolve unambiguously to whole workload subnets"
                )
            found.update(actual)
            selected.append(subnet_id)
        if found != wanted:
            raise ValueError(
                "enforced source_subnets must match actual whole workload subnet CIDRs"
            )
        result[policy.connection] = tuple(sorted(selected))
    return result
