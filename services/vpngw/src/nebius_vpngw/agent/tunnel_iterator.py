"""Centralized tunnel iteration logic.

This module provides a single source of truth for iterating over enabled tunnels
and their corresponding XFRM interface indices. This ensures that strongswan_renderer,
routing_guard, and FRR all use identical tunnel-to-interface mappings.

The mapping logic:
- Both "active" and "passive" tunnels are processed (for Active/Passive HA)
- Only tunnels with ha_role="disable" are skipped
- Interface indices are assigned sequentially starting from 0
- Each enabled tunnel gets xfrm{idx} where idx increments globally across all connections
- FRR applies local-preference to differentiate active (pref=200) vs passive (pref=100)
"""

from collections.abc import Iterator
from typing import Any


def iter_active_tunnels(
    cfg: dict[str, Any],
) -> Iterator[tuple[int, str, dict[str, Any], dict[str, Any]]]:
    """Iterate over all enabled tunnels with their XFRM interface indices.

    This is the canonical source of truth for tunnel-to-interface mapping.
    All components (strongswan_renderer, routing_guard, frr_renderer) MUST use
    this iterator to ensure consistent interface index assignment.

    For Active/Passive HA:
    - Both "active" and "passive" tunnels are included (IPsec + BGP established)
    - Only tunnels with ha_role="disable" are excluded
    - FRR applies different local-preference: active=200, passive=100
    - This prevents ECMP while maintaining hot standby

    The interface index is scoped per-VM (per rendered config), not globally across
    a gateway group. Each VM starts from xfrm0 for its first enabled tunnel.

    Args:
        cfg: Gateway configuration dictionary

    Yields:
        Tuple of (iface_index, iface_name, connection, tunnel) for each enabled tunnel

    Example:
        >>> for idx, iface_name, conn, tun in iter_active_tunnels(cfg):
        ...     print(f"Tunnel {tun['name']} ({tun['ha_role']}) uses {iface_name}")
        Tunnel gcp-ha-tunnel-1 (active) uses xfrm0
        Tunnel gcp-ha-tunnel-2 (passive) uses xfrm1
    """
    idx = 0
    connections = cfg.get("connections", [])

    for conn in connections:
        tunnels = conn.get("tunnels", [])

        for tun in tunnels:
            # Skip only explicitly disabled tunnels
            # Both "active" and "passive" tunnels are processed for Active/Passive HA
            ha_role = tun.get("ha_role", "active")
            if ha_role == "disable":
                continue

            iface_name = f"xfrm{idx}"
            yield idx, iface_name, conn, tun
            idx += 1


def get_tunnel_interface_mapping(cfg: dict[str, Any]) -> dict[str, tuple[int, str]]:
    """Get a mapping of tunnel names to their interface indices and names.

    Useful for lookups when you have a tunnel name and need its interface.

    Args:
        cfg: Gateway configuration dictionary

    Returns:
        Dict mapping tunnel_name -> (iface_index, iface_name)

    Example:
        >>> mapping = get_tunnel_interface_mapping(cfg)
        >>> idx, iface = mapping["gcp-ha-tunnel-1"]
        >>> print(f"Tunnel uses {iface} (index {idx})")
        Tunnel uses xfrm0 (index 0)
    """
    mapping = {}
    for idx, iface_name, _conn, tun in iter_active_tunnels(cfg):
        tunnel_name = tun.get("name", f"tunnel{idx}")
        mapping[tunnel_name] = (idx, iface_name)
    return mapping


# Backwards compatibility alias (legacy callers)
get_tunnel_vti_mapping = get_tunnel_interface_mapping
