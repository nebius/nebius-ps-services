"""Routing Guard: Enforce routing table invariants for VPN gateway.

This module ensures critical routing rules are always correct, independent of
configuration changes. It runs AFTER all config rendering (strongSwan/FRR/netplan)
completes to prevent routing issues from breaking VPN connectivity.

Defense-in-Depth Strategy:
This module is part of a multi-layer defense against problematic APIPA routing:

Layer 1 (Hardening - sysctl): Kernel settings reduce attack surface
  - net.ipv4.conf.all.route_localnet=0 (disable link-local routing)
  - net.ipv4.conf.all.accept_local=0 (prevent local address acceptance)
  - net.ipv4.conf.all.arp_announce=2 (strict ARP source address)
  - net.ipv4.conf.all.arp_ignore=1 (strict ARP target matching)
  Note: These prevent kernel auto-generation but NOT DHCP client route additions
  
Layer 2 (Primary Defense - this module): Remove DHCP-added routes reactively
  - Runs AFTER strongSwan/FRR/netplan rendering completes
  - Removes table 220 policy routes
  - Removes broad APIPA routes (169.254.0.0/16) added by DHCP
  - Runs on every agent start/reload
  - This is the PRIMARY defense against DHCP-added broad APIPA routes
  
Layer 3 (Backup - timer): Periodic enforcement every 5 minutes
  - nebius-vpngw-fix-routes.timer runs fix-routes.sh
  - Catches routes added by periodic DHCP renewals
  - Independent of agent lifecycle

Design Philosophy:
- Routing invariants are GLOBAL, not tied to any specific config renderer
- Must be idempotent (safe to run multiple times)
- Must run AFTER config rendering to clean up DHCP-added routes
- Defense-in-depth: Combine prevention (sysctl) with cleanup (this module + timer)

Timing is Critical:
The module MUST run AFTER strongSwan/FRR rendering because:
1. strongSwan renderer writes netplan config and runs "netplan apply"
2. netplan apply triggers systemd-networkd reload
3. systemd-networkd loses DHCP lease and re-acquires it
4. DHCP renewal may add back 169.254.0.0/16 route (Nebius metadata service)
5. This broad APIPA route captures VTI tunnel traffic and breaks BGP

By running AFTER all rendering, we ensure routes are cleaned up even if
DHCP renewal occurs during agent startup/reload.

Common Issues Prevented:
1. Table 220 policy routing (overrides main table, breaks VTI routing)
2. Broad APIPA routes (169.254.0.0/16) that capture VTI traffic
3. Missing /32 routes for BGP peers (causes source IP issues)

Note: This follows the same pattern as AWS/Azure/Juniper/Cisco routers when
building strongSwan customer gateways in cloud environments.
"""

from __future__ import annotations

import subprocess
from typing import Dict, Any, List
import ipaddress

from .tunnel_iterator import iter_active_tunnels


# Cloud metadata service APIPA whitelist
# These routes are OWNED by the cloud platform and must NEVER be removed
# GCP/AWS/Azure: Instance metadata service at 169.254.169.254
# Nebius: Gateway at 169.254.169.1 for metadata and DHCP
METADATA_APIPA_WHITELIST = [
    "169.254.169.0/24",   # Full metadata service range
    "169.254.169.1/32",   # Specific gateway (Nebius)
    "169.254.169.254/32", # Specific metadata endpoint (AWS/GCP/Azure)
]


def enforce_routing_invariants(cfg: Dict[str, Any]) -> None:
    """Enforce routing table invariants for VPN gateway.
    
    This function MUST be called on every agent startup/reload, regardless of
    whether the configuration changed. It ensures:
    
    1. No policy routing rules exist (especially table 220)
    2. Table 220 is flushed if it exists
    3. Broad APIPA routes (169.254.0.0/16) are removed
    4. Required /32 routes for BGP peers exist
    
    Args:
        cfg: Gateway configuration (used to extract BGP peer IPs)
    
    Note:
        This function is idempotent and safe to call multiple times.
        It will only make changes if invariants are violated.
    """
    print("[RoutingGuard] Enforcing routing table invariants...")
    
    # Track metrics for structured logging
    stats = {
        "table_220_removed": False,
        "broad_apipa_removed": False,
        "orphaned_apipa_removed": 0,
        "bgp_peer_routes_ensured": 0,
    }
    
    # INVARIANT 1: Remove table 220 policy routing rule
    # Table 220 is created by some cloud platforms (e.g., GCP) and overrides
    # the main routing table. This breaks VTI routing because packets are
    # routed based on table 220 instead of the VTI routes in the main table.
    stats["table_220_removed"] = _remove_table_220()
    
    # INVARIANT 2: Remove broad APIPA route if present
    # Some systems create a 169.254.0.0/16 route that captures VTI traffic
    # (VTI uses 169.254.x.x addressing). We need specific /32 routes instead.
    stats["broad_apipa_removed"] = _remove_broad_apipa_route()
    
    # INVARIANT 3: Clean up unexpected APIPA routes (declarative management)
    # Only APIPA routes explicitly defined in config should exist.
    # This prevents leftover routes from old tunnels or cloud DHCP interference.
    stats["orphaned_apipa_removed"] = _cleanup_unexpected_apipa_routes(cfg)
    
    # INVARIANT 4: Ensure /32 routes for BGP peers
    # BGP peers must have explicit /32 routes through VTI interfaces
    # to ensure correct source IP selection and routing
    stats["bgp_peer_routes_ensured"] = _ensure_bgp_peer_routes(cfg)
    
    # Structured logging summary
    if stats["table_220_removed"] or stats["broad_apipa_removed"] or stats["orphaned_apipa_removed"] > 0:
        # Something was fixed
        print(f"[RoutingGuard] Summary: table_220_removed={stats['table_220_removed']} "
              f"broad_apipa_removed={stats['broad_apipa_removed']} "
              f"orphaned_apipa_removed={stats['orphaned_apipa_removed']} "
              f"bgp_peer_routes_ensured={stats['bgp_peer_routes_ensured']}")
        print("[RoutingGuard] ✓ Routing invariants enforced")
    else:
        # Clean state - nothing needed fixing
        print(f"[RoutingGuard] ✓ No orphan routes found. Table 220 & APIPA OK. "
              f"BGP peer routes: {stats['bgp_peer_routes_ensured']}")



def _remove_table_220() -> bool:
    """Remove table 220 policy routing rule and flush its routes.
    
    Table 220 is problematic because:
    - It's consulted before the main table (lower priority number)
    - Routes in table 220 override VTI routes in main table
    - Causes BGP SYN-ACK to go out the wrong interface
    
    This function:
    1. Flushes all routes in table 220
    2. Removes the policy routing rule (by table and by preference)
    3. Verifies removal (logs if still present)
    
    Returns:
        True if table 220 was removed, False if it didn't exist
    """
    removed = False
    
    # Check if table 220 rule exists before attempting removal
    result = subprocess.run(
        ["ip", "rule", "show"],
        capture_output=True,
        text=True
    )
    if "220" not in result.stdout:
        # Clean state - nothing to do
        return False
    
    # Flush all routes in table 220 first (important for clean removal)
    result = subprocess.run(
        ["ip", "route", "flush", "table", "220"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print("[RoutingGuard] Flushed table 220 routes")
        removed = True
    
    # Remove routing rule by table lookup
    result = subprocess.run(
        ["ip", "rule", "del", "lookup", "220"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print("[RoutingGuard] Removed policy rule: pref 220 from all lookup 220")
        removed = True
    
    # Fallback: Remove by preference number (some systems use pref instead of lookup)
    result = subprocess.run(
        ["ip", "rule", "del", "pref", "220"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print("[RoutingGuard] Removed policy rule: pref 220 (fallback method)")
        removed = True
    
    # Verify removal
    result = subprocess.run(
        ["ip", "rule", "show"],
        capture_output=True,
        text=True
    )
    if "220" in result.stdout:
        print(f"[RoutingGuard] ⚠ WARNING: Table 220 rule still present after removal attempt")
        print(f"[RoutingGuard] Current rules:\n{result.stdout}")
    elif removed:
        print("[RoutingGuard] ✓ Table 220 completely removed")
    
    return removed


def _remove_broad_apipa_route() -> bool:
    """Remove broad 169.254.0.0/16 APIPA route if present.
    
    Problem:
    - DHCP client creates 169.254.0.0/16 route on eth0 for metadata gateway
    - VTI interfaces use 169.254.x.x addresses for BGP peering
    - Broad route captures VTI traffic, routing it to eth0 instead of VTI
    
    Solution:
    - Remove the broad /16 route (this function)
    - Preserve specific metadata routes in METADATA_APIPA_WHITELIST
    - Use specific /32 routes for each BGP peer (handled separately)
    - VTI interfaces get their own /30 routes automatically
    
    Returns:
        True if broad route was removed, False if it didn't exist
    """
    # Check if broad APIPA route exists
    result = subprocess.run(
        ["ip", "route", "show", "169.254.0.0/16"],
        capture_output=True,
        text=True
    )
    
    if result.stdout.strip():
        route_info = result.stdout.strip()
        # Route exists, remove it
        result = subprocess.run(
            ["ip", "route", "del", "169.254.0.0/16"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"[RoutingGuard] Removed orphan APIPA route: 169.254.0.0/16 (was: {route_info})")
            return True
        else:
            print(f"[RoutingGuard] ⚠ Failed to remove 169.254.0.0/16: {result.stderr}")
    
    return False


def _cleanup_unexpected_apipa_routes(cfg: Dict[str, Any]) -> int:
    """Remove APIPA routes that are not explicitly defined in config.
    
    This implements declarative APIPA route management with explicit scoping:
    - We OWN tunnel APIPA (inner_cidr, inner_remote_ip)
    - Cloud OWNS metadata APIPA (169.254.169.0/24)
    - Everything else gets removed
    
    Expected APIPA routes (preserved):
    1. Tunnel CIDRs: Connected routes from VTI IP assignments (inner_cidr)
    2. Tunnel peers: BGP peer /32 routes through VTI (inner_remote_ip)
    3. Metadata: Cloud metadata routes (169.254.169.x) for platform APIs
    
    Unexpected routes (deleted):
    - APIPA routes on wrong interfaces (e.g., dev eth0 instead of vtiX)
    - Leftover routes from deleted/disabled tunnels
    - Routes to APIPA prefixes not in config
    
    Args:
        cfg: Gateway configuration containing tunnel definitions
    
    Returns:
        Number of unexpected routes removed
    """
    # Build explicit sets of expected APIPA routes
    # Scoping: We manage tunnel APIPA, cloud manages metadata APIPA
    expected_tunnel_cidrs = {}  # {cidr: vti_name}
    expected_tunnel_peers = {}  # {peer_ip: vti_name}
    
    for idx, vti_name, conn, tun in iter_active_tunnels(cfg):
        # Tunnel CIDR: Connected route from VTI IP assignment (kernel-added)
        inner_cidr = tun.get("inner_cidr")
        if inner_cidr:
            # e.g., "169.254.18.224/30"
            expected_tunnel_cidrs[inner_cidr] = vti_name
        
        # Tunnel peer: /32 route for BGP peer (routing_guard-added)
        inner_remote_ip = tun.get("inner_remote_ip")
        if inner_remote_ip:
            peer_ip = inner_remote_ip.split("/")[0]
            expected_tunnel_peers[peer_ip] = vti_name
            expected_tunnel_peers[f"{peer_ip}/32"] = vti_name
    
    # Get all current APIPA routes
    result = subprocess.run(
        ["ip", "route", "show"],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print(f"[RoutingGuard] ⚠ Failed to get routes: {result.stderr}")
        return 0
    
    routes_to_remove = []
    
    for line in result.stdout.strip().split("\n"):
        if not line or "169.254." not in line:
            continue
        
        # Skip default routes and multipath routes (contain "nexthop" or "default")
        if "default" in line or "nexthop" in line:
            continue
        
        # Parse route: "169.254.18.224/30 dev vti0 proto kernel scope link src 169.254.18.226"
        parts = line.split()
        if len(parts) < 3:
            continue
        
        prefix = parts[0]
        
        # EXPLICIT SCOPING: Cloud metadata APIPA whitelist - NEVER touch
        # Check if route destination overlaps with metadata whitelist
        is_metadata = False
        try:
            route_net = ipaddress.ip_network(prefix, strict=False)
            for metadata_prefix in METADATA_APIPA_WHITELIST:
                metadata_net = ipaddress.ip_network(metadata_prefix)
                # Check if route is within metadata range or exactly matches
                if route_net.overlaps(metadata_net) or route_net == metadata_net:
                    is_metadata = True
                    break
        except ValueError:
            # Invalid IP format - skip
            continue
        
        if is_metadata:
            # Protected metadata route - skip
            continue
        
        # Find device
        dev_interface = None
        if "dev" in parts:
            dev_idx = parts.index("dev")
            if dev_idx + 1 < len(parts):
                dev_interface = parts[dev_idx + 1]
        
        if not dev_interface:
            continue
        
        # Check if this is a tunnel APIPA route we own
        is_expected = (
            prefix in expected_tunnel_cidrs and expected_tunnel_cidrs[prefix] == dev_interface
        ) or (
            prefix in expected_tunnel_peers and expected_tunnel_peers[prefix] == dev_interface
        )
        
        if not is_expected:
            # Unexpected tunnel APIPA route - mark for removal
            # (We only manage tunnel APIPA, cloud manages metadata APIPA)
            routes_to_remove.append((prefix, dev_interface, line.strip()))
    
    # Remove unexpected routes
    removed_count = 0
    if routes_to_remove:
        print(f"[RoutingGuard] Found {len(routes_to_remove)} unexpected APIPA route(s) to remove")
        for prefix, dev, full_route in routes_to_remove:
            result = subprocess.run(
                ["ip", "route", "del", prefix, "dev", dev],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                print(f"[RoutingGuard] Removed orphan APIPA route: {prefix} dev {dev}")
                removed_count += 1
            else:
                # Log but don't fail - route might have been removed already
                if "No such process" not in result.stderr and "not found" not in result.stderr.lower():
                    print(f"[RoutingGuard] ⚠ Could not remove {prefix} dev {dev}: {result.stderr.strip()}")
    
    return removed_count


def _ensure_bgp_peer_routes(cfg: Dict[str, Any]) -> int:
    """Ensure /32 routes exist for BGP peers through VTI interfaces.
    
    Problem:
    - BGP peers use 169.254.x.x addresses (APIPA range)
    - Without explicit /32 routes, traffic may use wrong interface
    - Causes source IP mismatch and BGP session failures
    
    Solution:
    - Extract BGP peer IPs from config using centralized tunnel iterator
    - Create /32 host route for each peer through its VTI interface
    - Use 'replace' to make this idempotent (no error if route exists)
    
    Args:
        cfg: Gateway configuration containing tunnel/BGP peer information
    
    Returns:
        Number of BGP peer routes ensured
    """
    routes_ensured = 0
    # Get routing defaults
    routing_mode_default = cfg.get("defaults", {}).get("routing", {}).get("mode", "bgp")
    
    # Use centralized iterator to ensure VTI index consistency
    # This guarantees routing_guard and strongswan_renderer use identical mappings
    for idx, vti_name, conn, tun in iter_active_tunnels(cfg):
        tunnel_name = tun.get("name", f"tunnel{idx}")
        
        # Get routing mode (connection or tunnel level overrides defaults)
        routing_mode = conn.get("routing_mode") or routing_mode_default
        tun_mode = tun.get("routing_mode") or routing_mode
        
        # Check if BGP is enabled for this connection
        bgp_cfg = conn.get("bgp", {})
        bgp_enabled = bgp_cfg.get("enabled", False)
        
        # Only add routes for BGP-enabled tunnels
        if tun_mode != "bgp" or not bgp_enabled:
            print(f"[RoutingGuard] Skipping non-BGP tunnel {tunnel_name} (vti{idx})")
            continue
        
        # Extract BGP peer IP
        inner_remote_ip = tun.get("inner_remote_ip")
        if not inner_remote_ip:
            print(f"[RoutingGuard] Tunnel {tunnel_name} missing inner_remote_ip, skipping")
            continue
        
        # Remove /30 CIDR suffix if present (we want just the IP)
        remote_ip = inner_remote_ip.split("/")[0]
        
        print(f"[RoutingGuard] Processing {tunnel_name}: tunnel_idx={idx}, vti={vti_name}, peer={remote_ip}")
        
        # Add /32 route for BGP peer through VTI
        # Use 'replace' to make this idempotent
        result = subprocess.run(
            ["ip", "route", "replace", f"{remote_ip}/32", "dev", vti_name],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print(f"[RoutingGuard] Ensured route {remote_ip}/32 via {vti_name}")
            routes_ensured += 1
        else:
            # Log error but don't fail (VTI might not exist yet during initial setup)
            print(f"[RoutingGuard] Could not add route {remote_ip}/32 via {vti_name}: {result.stderr.strip()}")
    
    return routes_ensured


def get_routing_diagnostics() -> Dict[str, Any]:
    """Get current routing state for diagnostics.
    
    Returns:
        Dictionary containing:
        - table_220_rule_exists: bool
        - table_220_routes: list of routes
        - apipa_broad_route_exists: bool
        - all_rules: list of policy routing rules
    """
    diagnostics = {}
    
    # Check for table 220 rule
    result = subprocess.run(
        ["ip", "rule", "show"],
        capture_output=True,
        text=True
    )
    diagnostics["all_rules"] = result.stdout.split("\n")
    diagnostics["table_220_rule_exists"] = "220" in result.stdout
    
    # Check table 220 routes
    result = subprocess.run(
        ["ip", "route", "show", "table", "220"],
        capture_output=True,
        text=True
    )
    diagnostics["table_220_routes"] = [r for r in result.stdout.split("\n") if r.strip()]
    
    # Check for broad APIPA route
    result = subprocess.run(
        ["ip", "route", "show", "169.254.0.0/16"],
        capture_output=True,
        text=True
    )
    diagnostics["apipa_broad_route_exists"] = bool(result.stdout.strip())
    
    return diagnostics
