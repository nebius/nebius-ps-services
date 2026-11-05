"""Pure HA connection policy; never rewrites configured roles or interface identities.

Ordinary gateways must not consult selector state. The controller supplies validated
operational selections via ``_ha_tunnel_selections`` on its effective configuration.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TunnelPath:
    name: str
    interface: str
    if_id: int
    preferred: bool
    destinations: frozenset[str]
    peer: str
    outer_pair: tuple[str, str]
    local_selectors: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ConnectionPolicy:
    name: str
    mode: str
    paths: tuple[TunnelPath, ...]
    destinations: frozenset[str]
    unavailable_reason: str | None = None

    @property
    def preferred(self) -> str:
        return next((p.name for p in self.paths if p.preferred), self.paths[0].name)

    @property
    def eligible(self) -> bool:
        return self.unavailable_reason is None


def covers(prefixes: frozenset[str], required: frozenset[str]) -> bool:
    available = tuple(ipaddress.IPv4Network(p, strict=False) for p in prefixes)
    return all(
        any(ipaddress.IPv4Network(p, strict=False).subnet_of(n) for n in available)
        for p in required
    )


def connection_policies(cfg: dict[str, Any]) -> tuple[ConnectionPolicy, ...]:
    """Project per-member topology, preserving the canonical enabled-tunnel order."""
    if not cfg.get("vm_ha") or cfg["vm_ha"].get("enabled") is False:
        return ()
    index = 0
    result: list[ConnectionPolicy] = []
    for connection in cfg.get("connections", []):
        mode = connection.get("routing_mode") or cfg.get("defaults", {}).get("routing", {}).get(
            "mode", "bgp"
        )
        paths = []
        modes = set()
        for tunnel in connection.get("tunnels", []):
            if tunnel.get("ha_role") == "disable":
                continue
            modes.add(tunnel.get("routing_mode") or mode)
            prefixes = (
                (
                    (tunnel.get("static_routes") or {}).get("remote_prefixes")
                    if mode == "static"
                    else None
                )
                or connection.get("remote_prefixes")
                or (connection.get("bgp") or {}).get("remote_prefixes")
                or []
            )
            paths.append(
                TunnelPath(
                    name=tunnel["name"],
                    interface=f"xfrm{index}",
                    if_id=100 + index,
                    preferred=tunnel.get("ha_role", "active") == "active",
                    destinations=frozenset(
                        str(ipaddress.IPv4Network(p, strict=False)) for p in prefixes
                    ),
                    peer=str(
                        (tunnel.get("bgp") or {}).get("remote_ip")
                        or tunnel.get("inner_remote_ip")
                        or ""
                    ),
                    outer_pair=(
                        str(tunnel.get("local_public_ip") or "%any"),
                        str(tunnel.get("remote_public_ip") or ""),
                    ),
                    local_selectors=frozenset(
                        str(ipaddress.ip_network(p, strict=False))
                        for p in ([tunnel["inner_cidr"]] if tunnel.get("inner_cidr") else [])
                        + (cfg.get("gateway", {}).get("local_prefixes") or [])
                        or ["0.0.0.0/0"]
                    ),
                )
            )
            index += 1
        if not paths:
            continue
        required = frozenset(
            str(ipaddress.IPv4Network(p, strict=False))
            for p in connection.get("remote_prefixes") or []
        ).union(*(p.destinations for p in paths))
        reason = None
        if modes != {mode}:
            reason = "mixed-tunnel-routing-modes"
        elif len(paths) < 2:
            reason = "no-same-owner-alternative"
        elif not required:
            reason = "destination-coverage-unspecified"
        elif not all(covers(p.destinations, required) for p in paths):
            reason = "unequal-destination-coverage"
        result.append(
            ConnectionPolicy(
                str(connection["name"]),
                mode,
                tuple(paths),
                required,
                reason,
            )
        )
    checked = []
    for policy in result:
        reason = policy.unavailable_reason
        for other in result:
            if other.name == policy.name:
                continue
            if any(
                ipaddress.IPv4Network(a).overlaps(ipaddress.IPv4Network(b))
                for a in policy.destinations
                for b in other.destinations
            ):
                reason = "conflicting-route-ownership"
            if policy.mode == "static" and any(
                a.outer_pair == b.outer_pair for a in policy.paths for b in other.paths
            ):
                reason = "potential-shared-ike-sa"
        checked.append(
            ConnectionPolicy(
                policy.name,
                policy.mode,
                policy.paths,
                policy.destinations,
                reason,
            )
        )
    return tuple(checked)


def selections(cfg: dict[str, Any]) -> dict[str, str]:
    """Return only eligible, exact selections; defaults apply only to Static."""
    supplied = cfg.get("_ha_tunnel_selections", {})
    result = {}
    for policy in connection_policies(cfg):
        if not policy.eligible:
            if policy.name in supplied:
                raise ValueError("HA tunnel selection is unavailable for connection")
            continue
        target = supplied.get(policy.name)
        if target is None and policy.mode == "static":
            target = policy.preferred
        if target is not None:
            if target not in {p.name for p in policy.paths}:
                raise ValueError("HA tunnel selection is not a current local path")
            result[policy.name] = target
    if set(supplied) - {p.name for p in connection_policies(cfg)}:
        raise ValueError("HA tunnel selection refers to an unknown connection")
    return result


def tunnel_preferred(cfg: dict[str, Any], connection: dict, tunnel: dict) -> bool:
    target = selections(cfg).get(str(connection.get("name"))) if cfg.get("vm_ha") else None
    return tunnel.get("name") == target if target else tunnel.get("ha_role", "active") == "active"


def tunnel_exports(cfg: dict[str, Any], connection: dict, tunnel: dict) -> bool:
    target = selections(cfg).get(str(connection.get("name"))) if cfg.get("vm_ha") else None
    return target is None or tunnel.get("name") == target
