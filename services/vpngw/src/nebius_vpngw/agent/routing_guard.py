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
  - nebius-vpngw-fix-routes.timer runs nebius_vpngw.agent.fix_routes
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
5. This broad APIPA route captures tunnel traffic and breaks BGP

By running AFTER all rendering, we ensure routes are cleaned up even if
DHCP renewal occurs during agent startup/reload.

Common Issues Prevented:
1. Table 220 policy routing (overrides main table, breaks tunnel routing)
2. Broad APIPA routes (169.254.0.0/16) that capture tunnel traffic
3. Missing /32 routes for BGP peers (causes source IP issues)
4. Missing or duplicate local prefix routes (breaks BGP advertisement)

Note: This follows the same pattern as AWS/Azure/Juniper/Cisco routers when
building strongSwan customer gateways in cloud environments.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from .tunnel_iterator import iter_active_tunnels

logger = logging.getLogger(__name__)


# Critical sysctl settings for XFRM routing
# These MUST be enforced for proper VPN gateway operation
REQUIRED_SYSCTLS = {
    "net.ipv4.ip_forward": "1",
    # rp_filter must be 0 for asymmetric routing on VPN gateways.
    "net.ipv4.conf.all.rp_filter": "0",
    "net.ipv4.conf.default.rp_filter": "0",
    "net.ipv4.conf.eth0.rp_filter": "0",
    "net.ipv4.conf.all.accept_redirects": "0",
    "net.ipv4.conf.all.send_redirects": "0",
    "net.ipv4.tcp_mtu_probing": "1",
}


# Cloud metadata service APIPA whitelist
# These routes are OWNED by the cloud platform and must NEVER be removed
# GCP/AWS/Azure: Instance metadata service at 169.254.169.254
# Nebius: Gateway at 169.254.169.1 for metadata and DHCP
METADATA_APIPA_WHITELIST = [
    "169.254.169.0/24",  # Full metadata service range
    "169.254.169.1/32",  # Specific gateway (Nebius)
    "169.254.169.254/32",  # Specific metadata endpoint (AWS/GCP/Azure)
]

LOCK_PATH = Path("/run/nebius-vpngw/fix-routes.lock")
VM_HA_STATUS_PATH = Path("/var/lib/nebius-vpngw/vm-ha/status.json")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")


def require_vm_ha_current_boot_ready(
    cfg: dict[str, Any],
    *,
    status_path: Path = VM_HA_STATUS_PATH,
    boot_id_path: Path = BOOT_ID_PATH,
    guard_path: Path | None = None,
) -> None:
    """Refuse every forwarding writer until this boot's controller is ready.

    Omitted VM HA remains on the established path.  An enabled node manifest,
    however, may not use process liveness or an old status file as forwarding
    authority.
    """

    vm_ha = cfg.get("vm_ha")
    if not isinstance(vm_ha, dict):
        return
    try:
        boot_id = boot_id_path.read_text(encoding="utf-8").strip()
        status = json.loads(status_path.read_text(encoding="utf-8"))
        guard = json.loads(
            (guard_path or status_path.with_name("guard.json")).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("VM-HA current-boot readiness is unavailable") from error
    if not boot_id or not isinstance(status, dict) or not isinstance(guard, dict):
        raise RuntimeError("VM-HA current-boot readiness is unavailable")
    if not (
        status.get("schema") == "nebius-vpngw/vm-ha-status-v1"
        and status.get("controller_ready_boot_id") == boot_id
        and status.get("guard_boot_id") == boot_id
        and status.get("promotion_ready") is True
        and status.get("data_plane_mode") == "active"
        and status.get("state") in {"active", "degraded"}
        and guard.get("guard_boot_id") == boot_id
        and guard.get("data_plane_mode") == "active"
    ):
        raise RuntimeError("VM-HA controller is not ready for forwarding on the current boot")


def acquire_routing_lock(blocking: bool = True) -> int | None:
    """Acquire the routing lock to prevent concurrent route mutations."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        import fcntl

        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(fd, flags)
    except BlockingIOError:
        os.close(fd)
        return None
    except Exception:
        os.close(fd)
        raise
    return fd


def _enforce_routing_sysctls() -> tuple[int, list[str]]:
    """Enforce critical sysctl settings for XFRM routing.

    This provides self-healing capability if sysctls get reset by:
    - Cloud-init rerunning
    - Other services modifying sysctl
    - Manual changes that break routing

    Returns:
        Tuple of (number_fixed, list_of_fixed_sysctls)
    """
    fixed_count = 0
    fixed_sysctls = []

    for key, expected_value in REQUIRED_SYSCTLS.items():
        # Read current value
        result = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True)

        if result.returncode != 0:
            logger.warning(f"[RoutingGuard] Could not read sysctl {key}: {result.stderr.strip()}")
            continue

        current_value = result.stdout.strip()

        if current_value != expected_value:
            # Fix it
            print(
                f"[RoutingGuard] Fixing sysctl: {key} (current={current_value}, expected={expected_value})"
            )
            result = subprocess.run(
                ["sysctl", "-w", f"{key}={expected_value}"],
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                fixed_count += 1
                fixed_sysctls.append(key)
                print(f"[RoutingGuard] ✓ Fixed {key}={expected_value}")
            else:
                logger.error(f"[RoutingGuard] Failed to set {key}: {result.stderr.strip()}")

    # Also enforce rp_filter=0 on all XFRM interfaces
    result = subprocess.run(["ip", "link", "show", "type", "xfrm"], capture_output=True, text=True)

    for line in result.stdout.split("\n"):
        if ": xfrm" in line:
            # Extract interface name (e.g., "xfrm0" from "5: xfrm0@eth0:")
            iface_name = line.split(": ")[1].split("@")[0] if ": " in line else None
            if iface_name:
                key = f"net.ipv4.conf.{iface_name}.rp_filter"
                result = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True)

                if result.returncode == 0 and result.stdout.strip() != "0":
                    subprocess.run(["sysctl", "-w", f"{key}=0"], capture_output=True)
                    fixed_count += 1
                    fixed_sysctls.append(key)
                    print(f"[RoutingGuard] ✓ Fixed {key}=0")

    return fixed_count, fixed_sysctls


def enforce_routing_invariants(cfg: dict[str, Any]) -> None:
    """Enforce routing table invariants for VPN gateway.

    This function MUST be called on every agent startup/reload, regardless of
    whether the configuration changed. It ensures:

    1. Critical sysctls are correct (ip_forward, rp_filter, redirects)
    2. No policy routing rules exist (especially table 220)
    3. Table 220 is flushed if it exists
    4. Broad APIPA routes (169.254.0.0/16) are removed
    5. Required /32 routes for BGP peers exist
    6. Local prefix routes are canonical and unique

    Args:
        cfg: Gateway configuration (used to extract BGP peer IPs)

    Note:
        This function is idempotent and safe to call multiple times.
        It will only make changes if invariants are violated.
    """
    require_vm_ha_current_boot_ready(cfg)
    lock_fd = acquire_routing_lock(blocking=False)
    if lock_fd is None:
        print("[RoutingGuard] Lock held; skipping")
        return
    try:
        _enforce_routing_invariants_locked(cfg)
    finally:
        try:
            os.close(lock_fd)
        except Exception:
            pass


def _enforce_routing_invariants_locked(cfg: dict[str, Any]) -> None:
    require_vm_ha_current_boot_ready(cfg)
    print("[RoutingGuard] Enforcing routing table invariants...")

    # Track metrics for structured logging
    stats = {
        "sysctls_fixed": 0,
        "table_220_removed": False,
        "broad_apipa_removed": False,
        "orphaned_apipa_removed": 0,
        "bgp_peer_routes_ensured": 0,
        "scope_link_routes_removed": 0,
        "local_prefix_routes_fixed": 0,
    }

    # INVARIANT 0: Enforce critical sysctl settings (self-healing)
    # This ensures routing works even if cloud-init or other services reset sysctls
    sysctls_fixed, fixed_list = _enforce_routing_sysctls()
    stats["sysctls_fixed"] = sysctls_fixed
    if sysctls_fixed > 0:
        print(f"[RoutingGuard] Fixed {sysctls_fixed} sysctls: {', '.join(fixed_list)}")

    # INVARIANT 1: Remove table 220 policy routing rule
    # Table 220 is created by some cloud platforms and overrides the main table.
    # With XFRM we want main-table routing only.
    stats["table_220_removed"] = _remove_table_220()

    # INVARIANT 2: Remove broad APIPA route if present (keep metadata-specific routes)
    stats["broad_apipa_removed"] = _remove_broad_apipa_route()

    # INVARIANT 3: Remove scope link routes for local prefixes
    # "scope link" routes mark prefixes as directly connected, breaking forwarding
    # These must be routed via gateway instead to enable VPN traffic
    stats["scope_link_routes_removed"] = _remove_scope_link_local_prefixes(cfg)

    # INVARIANT 4: Ensure local prefix routes exist and are canonical
    stats["local_prefix_routes_fixed"] = ensure_local_prefix_routes(cfg)

    # INVARIANT 5: Clean up unexpected APIPA routes (declarative management)
    # Only APIPA routes explicitly defined in config should exist.
    # This prevents leftover routes from old tunnels or cloud DHCP interference.
    stats["orphaned_apipa_removed"] = _cleanup_unexpected_apipa_routes(cfg)

    # INVARIANT 6: Ensure /32 routes for BGP peers
    # BGP peers must have explicit /32 routes through XFRM interfaces
    # to ensure correct source IP selection and routing
    stats["bgp_peer_routes_ensured"] = _ensure_bgp_peer_routes(cfg)

    route_cache_needed = (
        stats["table_220_removed"]
        or stats["broad_apipa_removed"]
        or stats["scope_link_routes_removed"] > 0
        or stats["local_prefix_routes_fixed"] > 0
        or stats["orphaned_apipa_removed"] > 0
    )

    # Structured logging summary
    if (
        stats["sysctls_fixed"] > 0
        or stats["table_220_removed"]
        or stats["broad_apipa_removed"]
        or stats["orphaned_apipa_removed"] > 0
        or stats["scope_link_routes_removed"] > 0
        or stats["local_prefix_routes_fixed"] > 0
    ):
        # Something was fixed
        print(
            f"[RoutingGuard] Summary: sysctls_fixed={stats['sysctls_fixed']} "
            f"table_220_removed={stats['table_220_removed']} "
            f"broad_apipa_removed={stats['broad_apipa_removed']} "
            f"scope_link_routes_removed={stats['scope_link_routes_removed']} "
            f"local_prefix_routes_fixed={stats['local_prefix_routes_fixed']} "
            f"orphaned_apipa_removed={stats['orphaned_apipa_removed']} "
            f"bgp_peer_routes_ensured={stats['bgp_peer_routes_ensured']}"
        )
        print("[RoutingGuard] ✓ Routing invariants enforced")
    else:
        # Clean state - nothing needed fixing
        print(
            f"[RoutingGuard] ✓ All invariants OK. BGP peer routes: {stats['bgp_peer_routes_ensured']}"
        )
    if route_cache_needed:
        _flush_route_cache()


def enforce_routing_invariants_locked(cfg: dict[str, Any]) -> None:
    """Enforce routing invariants assuming the routing lock is already held."""
    _enforce_routing_invariants_locked(cfg)


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
    result = subprocess.run(["ip", "rule", "show"], capture_output=True, text=True)
    if "lookup 220" not in result.stdout and "pref 220" not in result.stdout:
        # Clean state - nothing to do
        return False

    # Flush all routes in table 220 first (important for clean removal)
    result = subprocess.run(
        ["ip", "route", "flush", "table", "220"], capture_output=True, text=True
    )
    if result.returncode == 0:
        print("[RoutingGuard] Flushed table 220 routes")
        removed = True

    # Remove routing rule by table lookup
    result = subprocess.run(["ip", "rule", "del", "lookup", "220"], capture_output=True, text=True)
    if result.returncode == 0:
        print("[RoutingGuard] Removed policy rule: pref 220 from all lookup 220")
        removed = True

    # Fallback: Remove by preference number (some systems use pref instead of lookup)
    result = subprocess.run(["ip", "rule", "del", "pref", "220"], capture_output=True, text=True)
    if result.returncode == 0:
        print("[RoutingGuard] Removed policy rule: pref 220 (fallback method)")
        removed = True

    # Verify removal
    result = subprocess.run(["ip", "rule", "show"], capture_output=True, text=True)
    if "220" in result.stdout:
        print("[RoutingGuard] ⚠ WARNING: Table 220 rule still present after removal attempt")
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
        ["ip", "route", "show", "169.254.0.0/16"], capture_output=True, text=True
    )

    if result.stdout.strip():
        route_info = result.stdout.strip()
        # Route exists, remove it
        result = subprocess.run(
            ["ip", "route", "del", "169.254.0.0/16"], capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"[RoutingGuard] Removed orphan APIPA route: 169.254.0.0/16 (was: {route_info})")
            return True
        else:
            print(f"[RoutingGuard] ⚠ Failed to remove 169.254.0.0/16: {result.stderr}")

    return False


def _flush_route_cache() -> None:
    result = subprocess.run(["ip", "route", "flush", "cache"], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[RoutingGuard] WARNING: Failed to flush route cache: {result.stderr.strip()}")


def _remove_scope_link_local_prefixes(cfg: dict[str, Any]) -> int:
    """Remove scope link routes for local prefixes that break packet forwarding.

    Problem:
    - FRR may add "scope link" routes for local_prefixes to enable BGP advertisement
    - Example: "10.49.0.0/16 dev eth0 scope link"
    - "scope link" marks the prefix as directly connected on eth0
    - This causes the kernel to treat packets from that prefix as local delivery
    - Packets arrive at the gateway but are NOT forwarded to VPN tunnels

    Solution:
    - Remove any "scope link" routes for prefixes in gateway.local_prefixes
    - These should instead route via the VPC gateway (169.254.169.1)
    - FRR renderer has been updated to create proper routes
    - This guard removes any that slip through

    Args:
        cfg: Gateway configuration containing local_prefixes

    Returns:
        Number of scope link routes removed
    """
    local_prefixes = cfg.get("gateway", {}).get("local_prefixes", [])
    if not local_prefixes:
        return 0

    removed_count = 0

    for prefix in local_prefixes:
        # Get the current route for this prefix
        result = subprocess.run(["ip", "route", "show", prefix], capture_output=True, text=True)

        if result.returncode != 0 or not result.stdout.strip():
            # No route exists for this prefix
            continue

        route_info = result.stdout.strip()

        # Check if this is a scope link route
        if "scope link" in route_info:
            # This is a problematic scope link route - remove it
            result = subprocess.run(["ip", "route", "del", prefix], capture_output=True, text=True)
            if result.returncode == 0:
                print(
                    f"[RoutingGuard] ⚠ CRITICAL: Removed scope link route that breaks forwarding: {prefix}"
                )
                print(f"[RoutingGuard]   Was: {route_info}")
                print("[RoutingGuard]   This route prevented VPN traffic forwarding!")
                removed_count += 1
            else:
                print(
                    f"[RoutingGuard] ⚠ Failed to remove scope link route for {prefix}: {result.stderr}"
                )

    if removed_count > 0:
        print(f"[RoutingGuard] ✓ Removed {removed_count} scope link route(s) for local prefixes")

    return removed_count


def _collect_remote_prefixes(
    cfg: dict[str, Any],
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    prefixes: list[str] = []

    for conn in cfg.get("connections", []):
        prefixes.extend(conn.get("remote_prefixes", []) or [])
        conn_bgp = conn.get("bgp", {}) or {}
        prefixes.extend(conn_bgp.get("remote_prefixes", []) or [])

        for tun in conn.get("tunnels", []):
            static_routes = tun.get("static_routes", {}) or {}
            prefixes.extend(static_routes.get("remote_prefixes", []) or [])
            prefixes.extend(tun.get("remote_prefixes", []) or [])

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for prefix in prefixes:
        try:
            networks.append(ipaddress.ip_network(prefix, strict=False))
        except ValueError:
            print(f"[RoutingGuard] WARNING: Skipping invalid remote prefix: {prefix}")
    return networks


def ensure_local_prefix_routes(cfg: dict[str, Any], interface: str = "eth0") -> int:
    """CRITICAL: Adding kernel routes for local_prefixes that represent SOURCE subnets
    breaks packet forwarding from workload VMs!

    When a packet from 10.49.0.47 arrives at the gateway destined for 10.10.0.2:
    1. Gateway must FORWARD it to the VPN tunnel (xfrm0)
    2. Route "10.49.0.0/16 via 169.254.169.1 dev eth0" makes the gateway route packets
       FROM the workload subnet BACK to the VPC gateway instead of forwarding to VPN
    3. This breaks all connectivity

    Solution: Use FRR's "no bgp network import-check" instead to allow advertising without
    kernel routes. This is configured in frr_renderer.py.

    Args:
        cfg: Gateway configuration (unused)
        interface: Interface (unused)

    Returns:
        Always 0 (no routes added)
    """
    print(
        "[RoutingGuard] Skipping local_prefix routes (would break packet forwarding from workload VMs)"
    )
    return 0

    local_prefixes = cfg.get("gateway", {}).get("local_prefixes", [])
    if not local_prefixes:
        return 0

    blocked_networks = [ipaddress.ip_network("169.254.0.0/16")]
    blocked_networks.extend(_collect_remote_prefixes(cfg))

    route_metric = "50"
    changes_made = False
    prefixes_fixed = 0

    # Get the default gateway IP for proper routing
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and "via" in result.stdout:
            # Extract gateway IP (e.g., "default via 169.254.169.1 dev eth0")
            gateway_ip = result.stdout.split("via")[1].split()[0]
        else:
            print(
                "[RoutingGuard] WARNING: Could not determine default gateway, skipping local prefix routes"
            )
            return 0
    except Exception as e:
        print(f"[RoutingGuard] WARNING: Error getting default gateway: {e}")
        return 0

    def _parse_route(line: str) -> tuple[str, dict[str, str | bool]] | None:
        tokens = line.split()
        if not tokens:
            return None
        if tokens[0].endswith(":"):
            tokens = tokens[1:]
        if not tokens:
            return None
        attrs: dict[str, str | bool] = {
            "via": "",
            "dev": "",
            "metric": "",
            "proto": "",
            "scope": "",
            "src": "",
            "onlink": False,
        }
        i = 1
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("via", "dev", "metric", "proto", "scope", "src"):
                if i + 1 < len(tokens):
                    attrs[tok] = tokens[i + 1]
                    i += 2
                    continue
            if tok == "onlink":
                attrs["onlink"] = True
            i += 1
        return tokens[0], attrs

    def _route_is_canonical(line: str, prefix: str) -> bool:
        parsed = _parse_route(line)
        if not parsed:
            return False
        route_prefix, attrs = parsed
        return (
            route_prefix == prefix
            and attrs.get("via") == gateway_ip
            and attrs.get("dev") == interface
            and attrs.get("metric") == route_metric
            and bool(attrs.get("onlink"))
        )

    def _delete_route(line: str) -> bool:
        parsed = _parse_route(line)
        if not parsed:
            return False
        prefix, attrs = parsed
        cmd = ["ip", "route", "del", prefix]
        if attrs.get("via"):
            cmd.extend(["via", str(attrs["via"])])
        if attrs.get("dev"):
            cmd.extend(["dev", str(attrs["dev"])])
        if attrs.get("metric"):
            cmd.extend(["metric", str(attrs["metric"])])
        if attrs.get("proto"):
            cmd.extend(["proto", str(attrs["proto"])])
        if attrs.get("scope"):
            cmd.extend(["scope", str(attrs["scope"])])
        if attrs.get("src"):
            cmd.extend(["src", str(attrs["src"])])
        if attrs.get("onlink"):
            cmd.append("onlink")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0:
            print(f"[RoutingGuard] Removed duplicate route: {line}")
            return True
        print(f"[RoutingGuard] WARNING: Failed to remove route: {line}: {result.stderr}")
        return False

    for prefix in local_prefixes:
        try:
            local_net = ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            print(f"[RoutingGuard] WARNING: Skipping invalid local prefix: {prefix}")
            continue
        if local_net.version != 4:
            print(f"[RoutingGuard] WARNING: Skipping non-IPv4 local prefix: {prefix}")
            continue
        if any(
            local_net.version == blocked.version and local_net.overlaps(blocked)
            for blocked in blocked_networks
        ):
            print(
                f"[RoutingGuard] WARNING: Skipping local prefix {prefix} due to overlap with remote/APIPA"
            )
            continue

        existing_routes: list[str] = []
        result = subprocess.run(
            ["ip", "-o", "route", "show", prefix],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            existing_routes = [line.strip() for line in result.stdout.splitlines() if line.strip()]

        if len(existing_routes) == 1 and _route_is_canonical(existing_routes[0], prefix):
            continue

        prefix_changed = False
        for line in existing_routes:
            if _delete_route(line):
                changes_made = True
                prefix_changed = True

        # Add static route via gateway (NOT scope link)
        # This allows BGP to advertise the prefix while enabling proper forwarding
        try:
            subprocess.run(
                [
                    "ip",
                    "route",
                    "replace",
                    prefix,
                    "via",
                    gateway_ip,
                    "dev",
                    interface,
                    "onlink",
                    "metric",
                    route_metric,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            print(f"[RoutingGuard] Ensured local route: {prefix} via {gateway_ip} dev {interface}")
            changes_made = True
            prefix_changed = True
        except subprocess.CalledProcessError as e:
            print(f"[RoutingGuard] WARNING: Failed to add route for {prefix}: {e.stderr}")

        if prefix_changed:
            prefixes_fixed += 1

    if changes_made:
        subprocess.run(
            ["ip", "route", "flush", "cache"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    return prefixes_fixed


def _cleanup_unexpected_apipa_routes(cfg: dict[str, Any]) -> int:
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
    - APIPA routes on wrong interfaces (e.g., dev eth0 instead of xfrmX)
    - Leftover routes from deleted/disabled tunnels
    - Routes to APIPA prefixes not in config

    Args:
        cfg: Gateway configuration containing tunnel definitions

    Returns:
        Number of unexpected routes removed
    """
    # Build explicit sets of expected APIPA routes
    # Scoping: We manage tunnel APIPA, cloud manages metadata APIPA
    expected_tunnel_cidrs = {}  # {cidr: iface_name}
    expected_tunnel_peers = {}  # {peer_ip: iface_name}

    for _idx, iface_name, _conn, tun in iter_active_tunnels(cfg):
        # Tunnel CIDR: Connected route from VTI IP assignment (kernel-added)
        inner_cidr = tun.get("inner_cidr")
        if inner_cidr:
            # e.g., "169.254.18.224/30"
            expected_tunnel_cidrs[inner_cidr] = iface_name

        # Tunnel peer: /32 route for BGP peer (routing_guard-added)
        inner_remote_ip = tun.get("inner_remote_ip")
        if inner_remote_ip:
            peer_ip = inner_remote_ip.split("/")[0]
            expected_tunnel_peers[peer_ip] = iface_name
            expected_tunnel_peers[f"{peer_ip}/32"] = iface_name

    # Get all current APIPA routes
    result = subprocess.run(["ip", "route", "show"], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[RoutingGuard] ⚠ Failed to get routes: {result.stderr}")
        return 0

    routes_to_remove = []

    for line in result.stdout.strip().split("\n"):
        if not line:
            continue

        # Skip default routes and multipath routes (contain "nexthop" or "default")
        if "default" in line or "nexthop" in line:
            continue

        # Parse route: "169.254.18.224/30 dev xfrm0 proto kernel scope link src 169.254.18.226"
        parts = line.split()
        if len(parts) < 3:
            continue

        prefix = parts[0]

        # ONLY process routes where the PREFIX (destination) is in APIPA range
        # This prevents us from removing routes like "10.49.0.0/16 via 169.254.169.1"
        # which contain APIPA in the gateway but aren't APIPA routes themselves
        try:
            prefix_net = ipaddress.ip_network(prefix, strict=False)
            # Check if prefix is in APIPA range (169.254.0.0/16)
            apipa_range = ipaddress.ip_network("169.254.0.0/16")
            if not prefix_net.overlaps(apipa_range):
                # Not an APIPA destination - skip this route
                continue
        except ValueError:
            # Invalid IP format - skip
            continue

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
        ) or (prefix in expected_tunnel_peers and expected_tunnel_peers[prefix] == dev_interface)

        if not is_expected:
            # Unexpected tunnel APIPA route - mark for removal
            # (We only manage tunnel APIPA, cloud manages metadata APIPA)
            routes_to_remove.append((prefix, dev_interface, line.strip()))

    # Remove unexpected routes
    removed_count = 0
    if routes_to_remove:
        print(f"[RoutingGuard] Found {len(routes_to_remove)} unexpected APIPA route(s) to remove")
        for prefix, dev, _full_route in routes_to_remove:
            result = subprocess.run(
                ["ip", "route", "del", prefix, "dev", dev],
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                print(f"[RoutingGuard] Removed orphan APIPA route: {prefix} dev {dev}")
                removed_count += 1
            else:
                # Log but don't fail - route might have been removed already
                if (
                    "No such process" not in result.stderr
                    and "not found" not in result.stderr.lower()
                ):
                    print(
                        f"[RoutingGuard] ⚠ Could not remove {prefix} dev {dev}: {result.stderr.strip()}"
                    )

    return removed_count


def _ensure_bgp_peer_routes(cfg: dict[str, Any]) -> int:
    """Ensure /32 routes exist for BGP peers through XFRM interfaces.

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

    # Use centralized iterator to ensure interface index consistency
    # This guarantees routing_guard and strongswan_renderer use identical mappings
    for idx, iface_name, conn, tun in iter_active_tunnels(cfg):
        tunnel_name = tun.get("name", f"tunnel{idx}")

        # Get routing mode (connection or tunnel level overrides defaults)
        routing_mode = conn.get("routing_mode") or routing_mode_default
        tun_mode = tun.get("routing_mode") or routing_mode

        # Check if BGP is enabled for this connection
        bgp_cfg = conn.get("bgp", {})
        bgp_enabled = bgp_cfg.get("enabled", False)

        # Only add routes for BGP-enabled tunnels
        if tun_mode != "bgp" or not bgp_enabled:
            print(f"[RoutingGuard] Skipping non-BGP tunnel {tunnel_name} (xfrm{idx})")
            continue

        # Extract BGP peer IP
        inner_remote_ip = tun.get("inner_remote_ip")
        if not inner_remote_ip:
            print(f"[RoutingGuard] Tunnel {tunnel_name} missing inner_remote_ip, skipping")
            continue

        # Remove /30 CIDR suffix if present (we want just the IP)
        remote_ip = inner_remote_ip.split("/")[0]

        print(
            f"[RoutingGuard] Processing {tunnel_name}: tunnel_idx={idx}, iface={iface_name}, peer={remote_ip}"
        )

        # Add /32 route for BGP peer through VTI
        # Use 'replace' to make this idempotent
        result = subprocess.run(
            ["ip", "route", "replace", f"{remote_ip}/32", "dev", iface_name],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            print(f"[RoutingGuard] Ensured route {remote_ip}/32 via {iface_name}")
            routes_ensured += 1
        else:
            # Log error but don't fail (VTI might not exist yet during initial setup)
            print(
                f"[RoutingGuard] Could not add route {remote_ip}/32 via {iface_name}: {result.stderr.strip()}"
            )

    return routes_ensured


def get_routing_diagnostics() -> dict[str, Any]:
    """Get current routing state for diagnostics.

    Returns:
        Dictionary containing:
        - table_220_rule_exists: bool
        - table_220_routes: list of routes
        - apipa_broad_route_exists: bool
        - all_rules: list of policy routing rules
    """
    diagnostics: dict[str, Any] = {}

    # Check for table 220 rule
    result = subprocess.run(["ip", "rule", "show"], capture_output=True, text=True)
    diagnostics["all_rules"] = result.stdout.split("\n")
    diagnostics["table_220_rule_exists"] = "220" in result.stdout

    # Check table 220 routes
    result = subprocess.run(["ip", "route", "show", "table", "220"], capture_output=True, text=True)
    diagnostics["table_220_routes"] = [r for r in result.stdout.split("\n") if r.strip()]

    # Check for broad APIPA route
    result = subprocess.run(
        ["ip", "route", "show", "169.254.0.0/16"], capture_output=True, text=True
    )
    diagnostics["apipa_broad_route_exists"] = bool(result.stdout.strip())

    return diagnostics
