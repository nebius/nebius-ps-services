"""Deterministic, per-destination route selection for explicit VM HA only."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..tunnel_iterator import iter_active_tunnels


@dataclass(frozen=True)
class StaticRouteCandidate:
    prefix: str
    device: str
    preferred: bool
    order: int


def static_route_candidates(config: Mapping[str, Any]) -> tuple[StaticRouteCandidate, ...]:
    candidates = []
    default_mode = (config.get("defaults", {}).get("routing") or {}).get("mode", "bgp")
    for order, device, connection, tunnel in iter_active_tunnels(dict(config)):
        mode = tunnel.get("routing_mode") or connection.get("routing_mode") or default_mode
        if mode != "static":
            continue
        prefixes = (tunnel.get("static_routes") or {}).get("remote_prefixes")
        if not prefixes:
            prefixes = connection.get("remote_prefixes") or []
        for prefix in prefixes:
            network = ipaddress.ip_network(prefix, strict=False)
            if network.version != 4:
                raise ValueError("VM-HA static routes require IPv4 destinations")
            candidates.append(
                StaticRouteCandidate(
                    str(network), device, tunnel.get("ha_role", "active") == "active", order
                )
            )
    return tuple(candidates)


def select_static_routes(
    candidates: Iterable[StaticRouteCandidate], usable_devices: set[str]
) -> dict[str, str]:
    selected: dict[str, StaticRouteCandidate] = {}
    for candidate in candidates:
        previous = selected.get(candidate.prefix)
        if previous is None or _priority(candidate, usable_devices) < _priority(
            previous, usable_devices
        ):
            selected[candidate.prefix] = candidate
    return {prefix: candidate.device for prefix, candidate in selected.items()}


def _priority(candidate: StaticRouteCandidate, usable_devices: set[str]) -> tuple[bool, bool, int]:
    # A usable backup beats an unavailable preferred path. Without SAs, install
    # the preferred path for bootstrap, while readiness remains false.
    return candidate.device not in usable_devices, not candidate.preferred, candidate.order
