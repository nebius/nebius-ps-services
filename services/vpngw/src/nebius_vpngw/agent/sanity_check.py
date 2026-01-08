#!/usr/bin/env python3
"""Sanity check script for VPN gateway routing configuration.

This script verifies that:
1. All active BGP tunnels have correct /32 routes
2. XFRM interfaces match expected configuration
3. Table 220 does not exist
4. No orphaned routes remain after tunnel removal

Usage:
    python3 -m nebius_vpngw.agent.sanity_check
"""

import subprocess
import sys
from pathlib import Path

import yaml

from .tunnel_iterator import iter_active_tunnels


def get_ip_routes():
    """Get all IP routes as a list of strings."""
    result = subprocess.run(["ip", "route", "show"], capture_output=True, text=True)
    return result.stdout.strip().split("\n") if result.returncode == 0 else []


def get_ip_rules():
    """Get all IP policy routing rules."""
    result = subprocess.run(["ip", "rule", "show"], capture_output=True, text=True)
    return result.stdout.strip().split("\n") if result.returncode == 0 else []


def check_routing_invariants():
    """Verify routing configuration matches expected state."""
    config_path = Path("/etc/nebius-vpngw/config-resolved.yaml")

    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        return False

    cfg = yaml.safe_load(config_path.read_text())
    routes = get_ip_routes()
    rules = get_ip_rules()

    all_good = True

    # Check 1: Table 220 must not exist
    print("\n🔍 Checking for table 220...")
    table_220_exists = any("220" in rule for rule in rules if "lookup 220" in rule)
    if table_220_exists:
        print("❌ FAIL: Table 220 exists (breaks BGP)")
        all_good = False
    else:
        print("✅ PASS: Table 220 not found")

    # Check 2: BGP peer /32 routes
    print("\n🔍 Checking BGP peer routes...")
    expected_routes = {}

    for _idx, iface_name, conn, tun in iter_active_tunnels(cfg):
        # Check if this is a BGP tunnel
        routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get("routing", {}).get(
            "mode", "bgp"
        )
        tun_mode = tun.get("routing_mode") or routing_mode
        bgp_enabled = conn.get("bgp", {}).get("enabled", False)

        if tun_mode == "bgp" and bgp_enabled:
            inner_remote_ip = tun.get("inner_remote_ip", "")
            peer_ip = inner_remote_ip.split("/")[0] if inner_remote_ip else None

            if peer_ip:
                expected_routes[peer_ip] = iface_name

    for peer_ip, expected_iface in expected_routes.items():
        # Look for route matching: <peer_ip> dev <iface>
        matching_routes = [r for r in routes if peer_ip in r and expected_iface in r]

        if not matching_routes:
            print(f"❌ FAIL: Missing route for {peer_ip} via {expected_iface}")
            all_good = False
        else:
            # Verify correct interface
            route = matching_routes[0]
            if f"dev {expected_iface}" in route:
                print(f"✅ PASS: {peer_ip} → {expected_iface}")
            else:
                # Route exists but uses wrong interface
                actual_iface = None
                for part in route.split():
                    if part.startswith("xfrm"):
                        actual_iface = part
                        break
                print(f"❌ FAIL: {peer_ip} uses {actual_iface}, expected {expected_iface}")
                all_good = False

    # Check 3: No orphaned routes
    print("\n🔍 Checking for orphaned routes...")
    expected_peer_ips = set(expected_routes.keys())

    # Also track expected CIDRs (connected routes from VTI IP assignments)
    expected_cidrs = set()
    for _idx, _iface_name, _conn, tun in iter_active_tunnels(cfg):
        inner_cidr = tun.get("inner_cidr")
        if inner_cidr:
            expected_cidrs.add(inner_cidr)

    for route in routes:
        # Look for APIPA routes (169.254.x.x) but exclude defaults and multipath
        if "169.254." not in route or "default" in route or "nexthop" in route:
            continue

        # Extract first part (prefix/IP)
        parts = route.split()
        if not parts or not parts[0].startswith("169.254."):
            continue

        route_prefix = parts[0]

        # Skip cloud metadata routes (169.254.169.x)
        if route_prefix.startswith("169.254.169."):
            continue

        # Check if this is an expected peer IP or CIDR
        if route_prefix in expected_peer_ips or route_prefix in expected_cidrs:
            continue

        # Check if it's a connected route from XFRM interface (has "proto kernel")
        if "proto kernel" in route and "dev xfrm" in route:
            # This is a connected route from IP assignment - expected
            continue

        # If we get here, it's truly orphaned
        print(f"⚠️  WARNING: Orphaned route found: {route.strip()}")

    # Summary
    print("\n" + "=" * 60)
    if all_good:
        print("✅ All routing invariants satisfied!")
        return True
    else:
        print("❌ Routing configuration has issues!")
        return False


if __name__ == "__main__":
    success = check_routing_invariants()
    sys.exit(0 if success else 1)
