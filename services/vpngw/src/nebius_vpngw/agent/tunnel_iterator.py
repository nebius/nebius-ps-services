"""Centralized tunnel iteration logic.

This module provides a single source of truth for iterating over active tunnels
and their corresponding VTI interface indices. This ensures that strongswan_renderer,
routing_guard, and FRR all use identical tunnel-to-VTI mappings.

The mapping logic is:
- Only active tunnels (ha_role != "standby") are processed
- VTI indices are assigned sequentially starting from 0
- Each active tunnel gets vti{idx} where idx increments globally across all connections
"""

from typing import Iterator, Tuple, Dict, Any


def iter_active_tunnels(cfg: Dict[str, Any]) -> Iterator[Tuple[int, str, Dict[str, Any], Dict[str, Any]]]:
    """Iterate over all active tunnels with their VTI indices.
    
    This is the canonical source of truth for tunnel-to-VTI mapping.
    All components (strongswan_renderer, routing_guard, frr_renderer) MUST use
    this iterator to ensure consistent VTI index assignment.
    
    The VTI index is scoped per-VM (per rendered config), not globally across
    a gateway group. Each VM starts from vti0 for its first active tunnel.
    
    Args:
        cfg: Gateway configuration dictionary
        
    Yields:
        Tuple of (vti_index, vti_name, connection, tunnel) for each active tunnel
        
    Example:
        >>> for idx, vti_name, conn, tun in iter_active_tunnels(cfg):
        ...     print(f"Tunnel {tun['name']} uses {vti_name}")
        Tunnel gcp-ha-tunnel-1 uses vti0
        Tunnel gcp-ha-tunnel-2 uses vti1
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
            
            vti_name = f"vti{idx}"
            yield idx, vti_name, conn, tun
            idx += 1


def get_tunnel_vti_mapping(cfg: Dict[str, Any]) -> Dict[str, Tuple[int, str]]:
    """Get a mapping of tunnel names to their VTI indices and names.
    
    Useful for lookups when you have a tunnel name and need its VTI.
    
    Args:
        cfg: Gateway configuration dictionary
        
    Returns:
        Dict mapping tunnel_name -> (vti_index, vti_name)
        
    Example:
        >>> mapping = get_tunnel_vti_mapping(cfg)
        >>> idx, vti = mapping["gcp-ha-tunnel-1"]
        >>> print(f"Tunnel uses {vti} (index {idx})")
        Tunnel uses vti0 (index 0)
    """
    mapping = {}
    for idx, vti_name, conn, tun in iter_active_tunnels(cfg):
        tunnel_name = tun.get("name", f"tunnel{idx}")
        mapping[tunnel_name] = (idx, vti_name)
    return mapping
