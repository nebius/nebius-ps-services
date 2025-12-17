"""Centralized tunnel iteration logic.

This module provides a single source of truth for iterating over active tunnels
and their corresponding XFRM interface indices. This ensures that strongswan_renderer,
routing_guard, and FRR all use identical tunnel-to-interface mappings.

The mapping logic is:
- Only active tunnels (ha_role != "standby") are processed
- Interface indices are assigned sequentially starting from 0
- Each active tunnel gets xfrm{idx} where idx increments globally across all connections
"""

from typing import Iterator, Tuple, Dict, Any


def iter_active_tunnels(
    cfg: Dict[str, Any],
) -> Iterator[Tuple[int, str, Dict[str, Any], Dict[str, Any]]]:
    """Iterate over all active tunnels with their XFRM interface indices.

    This is the canonical source of truth for tunnel-to-interface mapping.
    All components (strongswan_renderer, routing_guard, frr_renderer) MUST use
    this iterator to ensure consistent interface index assignment.

    The interface index is scoped per-VM (per rendered config), not globally across
    a gateway group. Each VM starts from xfrm0 for its first active tunnel.

    Args:
        cfg: Gateway configuration dictionary

    Yields:
        Tuple of (iface_index, iface_name, connection, tunnel) for each active tunnel

    Example:
        >>> for idx, iface_name, conn, tun in iter_active_tunnels(cfg):
        ...     print(f"Tunnel {tun['name']} uses {iface_name}")
        Tunnel gcp-ha-tunnel-1 uses xfrm0
        Tunnel gcp-ha-tunnel-2 uses xfrm1
    """
    idx = 0
    connections = cfg.get("connections", [])

    for conn in connections:
        tunnels = conn.get("tunnels", [])

        for tun in tunnels:
            # Skip standby/disabled tunnels (only process active)
            # This must match the logic in strongswan_renderer to keep indices aligned
            if tun.get("ha_role", "active") != "active":
                continue

            iface_name = f"xfrm{idx}"
            yield idx, iface_name, conn, tun
            idx += 1


def get_tunnel_interface_mapping(cfg: Dict[str, Any]) -> Dict[str, Tuple[int, str]]:
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
    for idx, iface_name, conn, tun in iter_active_tunnels(cfg):
        tunnel_name = tun.get("name", f"tunnel{idx}")
        mapping[tunnel_name] = (idx, iface_name)
    return mapping


# Backwards compatibility alias (legacy callers)
get_tunnel_vti_mapping = get_tunnel_interface_mapping
