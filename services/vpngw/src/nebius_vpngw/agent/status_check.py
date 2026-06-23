"""Status checker for VPN gateway health and routing invariants.

This module provides functions to check the health of the VPN gateway,
including tunnel status, BGP sessions, and routing table invariants.
"""

import json
import re
import shutil
import subprocess
from typing import Any


def check_routing_health() -> dict[str, Any]:
    """Check routing table health and invariants.

    Returns:
        Dictionary with routing health status:
        - table_220_exists: bool
        - broad_apipa_exists: bool
        - orphaned_apipa_routes: List[str]
        - bgp_peer_routes: Dict[str, str]  # {peer_ip: iface_name}
        - overall_status: "healthy" | "warning" | "error"
    """
    health: dict[str, Any] = {
        "table_220_exists": False,
        "broad_apipa_exists": False,
        "orphaned_apipa_routes": [],
        "bgp_peer_routes": {},
        "overall_status": "healthy",
    }

    # Check if table 220 exists
    result = subprocess.run(["ip", "rule", "show"], capture_output=True, text=True)
    if "220" in result.stdout:
        health["table_220_exists"] = True
        health["overall_status"] = "error"

    # Check for broad APIPA route
    result = subprocess.run(
        ["ip", "route", "show", "169.254.0.0/16"], capture_output=True, text=True
    )
    if result.stdout.strip():
        health["broad_apipa_exists"] = True
        health["overall_status"] = "error"

    # Get all APIPA routes
    result = subprocess.run(["ip", "route", "show"], capture_output=True, text=True)

    if result.returncode == 0:
        for line in result.stdout.strip().split("\n"):
            if not line or "169.254." not in line:
                continue

            # Skip default routes and multipath routes
            if "default" in line or "nexthop" in line:
                continue

            parts = line.split()
            if len(parts) < 3:
                continue

            prefix = parts[0]

            # Track potential orphaned routes (this is a heuristic)
            # Real validation requires config context
            if "169.254." in prefix and "proto kernel" not in line:
                # Find device
                dev_interface = None
                if "dev" in parts:
                    dev_idx = parts.index("dev")
                    if dev_idx + 1 < len(parts):
                        dev_interface = parts[dev_idx + 1]

                if dev_interface and dev_interface.startswith("xfrm"):
                    health["bgp_peer_routes"][prefix] = dev_interface

    return health


def check_strongswan_tunnels() -> dict[str, Any]:
    """Check strongSwan tunnel status.

    Returns:
        Dictionary with tunnel status:
        - tunnels: List[Dict] with name, status, peer_ip, uptime
        - overall_status: "healthy" | "degraded" | "error"
    """
    if shutil.which("swanctl"):
        result = subprocess.run(["swanctl", "--list-sas"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout:
            tunnels = []
            tunnel_pattern = re.compile(
                r"^(\S+?)(?:\[\d+\])?:\s+.*\b(ESTABLISHED|CONNECTING)\b",
                re.IGNORECASE,
            )

            for line in result.stdout.splitlines():
                match = tunnel_pattern.search(line)
                if not match:
                    continue
                tunnels.append(
                    {
                        "name": match.group(1),
                        "status": match.group(2).upper(),
                        "uptime": "n/a",
                        "peer_ip": "",
                    }
                )

            if tunnels:
                overall = (
                    "healthy" if all(t["status"] == "ESTABLISHED" for t in tunnels) else "degraded"
                )
                return {"tunnels": tunnels, "overall_status": overall}

    result = subprocess.run(["ipsec", "statusall"], capture_output=True, text=True)

    if result.returncode != 0:
        return {
            "tunnels": [],
            "overall_status": "error",
            "error": result.stderr.strip(),
        }

    def parse_strongswan_uptime(uptime_str: str) -> str:
        """Parse strongSwan uptime format (e.g., '5 hours ago', '32 minutes ago') and convert to 'Xh Ym Zs' format."""
        # Parse the uptime string
        match = re.match(r"(\d+)\s+(second|minute|hour|day)s?\s+ago", uptime_str)
        if not match:
            # If we can't parse it, return as-is
            return uptime_str

        value = int(match.group(1))
        unit = match.group(2)

        # Convert to seconds
        if unit == "second":
            total_seconds = value
        elif unit == "minute":
            total_seconds = value * 60
        elif unit == "hour":
            total_seconds = value * 3600
        elif unit == "day":
            total_seconds = value * 86400
        else:
            return uptime_str

        # Format as "Xh Ym Zs"
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        return f"{hours}h {minutes}m {seconds}s"

    tunnels = []
    tunnel_pattern = re.compile(
        r"(\S+)\[\d+\]:\s+(\w+)\s+(.+?),\s+[\d.]+\[[\d.]+\]\.\.\.(\d+\.\d+\.\d+\.\d+)\["
    )

    for match in tunnel_pattern.finditer(result.stdout):
        raw_uptime = match.group(3)
        formatted_uptime = parse_strongswan_uptime(raw_uptime)

        tunnels.append(
            {
                "name": match.group(1),
                "status": match.group(2),
                "uptime": formatted_uptime,
                "peer_ip": match.group(4),
            }
        )

    # Determine overall status
    if not tunnels:
        overall = "error"
    elif all(t["status"] == "ESTABLISHED" for t in tunnels):
        overall = "healthy"
    else:
        overall = "degraded"

    return {"tunnels": tunnels, "overall_status": overall}


def check_bgp_sessions() -> dict[str, Any]:
    """Check FRR BGP session status.

    Returns:
        Dictionary with BGP session status:
        - peers: Dict[str, str] mapping peer_ip to state
        - overall_status: "healthy" | "degraded" | "error"
    """
    try:
        # Try JSON output first
        result = subprocess.run(
            ["vtysh", "-c", "show bgp ipv4 unicast summary json"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        peers = {}
        if result.returncode == 0 and result.stdout:
            try:
                data = json.loads(result.stdout)
                peers_data = (data.get("ipv4Unicast") or {}).get("peers") or data.get("peers") or {}

                for ip, info in peers_data.items():
                    state = (
                        info.get("state")
                        or info.get("state_name")
                        or info.get("stateName")
                        or info.get("peerState")
                        or info.get("bgpState")
                    )
                    if state:
                        peers[ip] = state
            except json.JSONDecodeError:
                pass

        # Fallback to text parsing if JSON didn't work
        if not peers:
            result = subprocess.run(
                ["vtysh", "-c", "show bgp summary"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] and "." in parts[0]:
                        try:
                            octets = parts[0].split(".")
                            if len(octets) == 4 and all(
                                o.isdigit() and 0 <= int(o) <= 255 for o in octets
                            ):
                                peers[parts[0]] = parts[-1]
                        except (ValueError, IndexError):
                            continue

        # Determine overall status
        if not peers:
            overall = "error"
        elif all(state.lower() == "established" for state in peers.values()):
            overall = "healthy"
        else:
            overall = "degraded"

        return {"peers": peers, "overall_status": overall}

    except subprocess.TimeoutExpired:
        return {"peers": {}, "overall_status": "error", "error": "vtysh timeout"}
    except Exception as e:
        return {"peers": {}, "overall_status": "error", "error": str(e)}


def get_full_status() -> dict[str, Any]:
    """Get comprehensive gateway status.

    Returns:
        Dictionary with all health checks:
        - routing: from check_routing_health()
        - tunnels: from check_strongswan_tunnels()
        - bgp: from check_bgp_sessions()
        - overall_health: "healthy" | "degraded" | "error"
    """
    routing = check_routing_health()
    tunnels = check_strongswan_tunnels()
    bgp = check_bgp_sessions()

    # Determine overall health
    statuses = [
        routing["overall_status"],
        tunnels["overall_status"],
        bgp["overall_status"],
    ]
    if any(s == "error" for s in statuses):
        overall = "error"
    elif any(s in ("degraded", "warning") for s in statuses):
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "routing": routing,
        "tunnels": tunnels,
        "bgp": bgp,
        "overall_health": overall,
    }
