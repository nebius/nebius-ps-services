"""Firewall management for VPN gateway.

This module manages UFW firewall rules to ensure:
- IPsec protocols (IKE, NAT-T, ESP) are allowed from VPN peers
- SSH is restricted to management CIDRs
- VTI interfaces are not filtered (BGP traffic flows freely)
"""

import subprocess
from pathlib import Path
from typing import Dict, Any, List, Set
import logging

PEER_IPS_FILE = Path("/etc/vpngw_peer_ips")
MGMT_CIDRS_FILE = Path("/etc/vpngw_mgmt_cidrs")
FIREWALL_SETUP_SCRIPT = Path("/usr/local/bin/setup-vpngw-firewall.sh")


def update_peer_ips(cfg: Dict[str, Any]) -> bool:
    """Extract peer public IPs from config and update /etc/vpngw_peer_ips.
    
    Args:
        cfg: Gateway configuration containing connections and peer info
    
    Returns:
        True if file was updated, False if unchanged
    """
    peer_ips: Set[str] = set()
    
    # Extract peer IPs from connections
    for conn in cfg.get("connections", []):
        for tun in conn.get("tunnels", []):
            remote_ip = tun.get("remote_ip", "").strip()
            if remote_ip:
                peer_ips.add(remote_ip)
    
    # Read current content
    current_content = ""
    if PEER_IPS_FILE.exists():
        current_content = PEER_IPS_FILE.read_text()
    
    # Build new content
    lines = [
        "# VPN peer public IPs (auto-generated from config)",
        "# One IP per line - used by UFW to allow IPsec protocols",
        ""
    ]
    lines.extend(sorted(peer_ips))
    new_content = "\n".join(lines) + "\n"
    
    # Update if changed
    if new_content != current_content:
        PEER_IPS_FILE.write_text(new_content)
        print(f"[FirewallMgr] Updated {PEER_IPS_FILE} with {len(peer_ips)} peer IP(s)")
        return True
    
    return False


def update_management_cidrs(cidrs: List[str]) -> bool:
    """Update /etc/vpngw_mgmt_cidrs with allowed SSH source CIDRs.
    
    Args:
        cidrs: List of CIDR strings (e.g., ["203.0.113.4/32", "198.51.100.0/24"])
    
    Returns:
        True if file was updated, False if unchanged
    """
    # Read current content
    current_content = ""
    if MGMT_CIDRS_FILE.exists():
        current_content = MGMT_CIDRS_FILE.read_text()
    
    # Build new content
    lines = [
        "# Management CIDRs allowed for SSH (auto-generated)",
        "# One CIDR per line",
        ""
    ]
    lines.extend(sorted(set(cidrs)))
    new_content = "\n".join(lines) + "\n"
    
    # Update if changed
    if new_content != current_content:
        MGMT_CIDRS_FILE.write_text(new_content)
        print(f"[FirewallMgr] Updated {MGMT_CIDRS_FILE} with {len(cidrs)} management CIDR(s)")
        return True
    
    return False


def reload_firewall() -> bool:
    """Reload UFW firewall rules by running the setup script.
    
    This re-applies firewall rules based on current peer IPs and management CIDRs.
    Safe to call multiple times - script is idempotent.
    
    Returns:
        True if reload succeeded, False otherwise
    """
    if not FIREWALL_SETUP_SCRIPT.exists():
        print(f"[FirewallMgr] WARNING: Firewall setup script not found at {FIREWALL_SETUP_SCRIPT}")
        return False
    
    print("[FirewallMgr] Reloading UFW firewall rules...")
    
    result = subprocess.run(
        ["bash", str(FIREWALL_SETUP_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30
    )
    
    if result.returncode == 0:
        print("[FirewallMgr] ✓ Firewall rules reloaded successfully")
        # Log output for debugging
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                print(f"[FirewallMgr]   {line}")
        return True
    else:
        print(f"[FirewallMgr] ✗ Firewall reload failed: {result.stderr}")
        return False


def update_firewall_from_config(cfg: Dict[str, Any], mgmt_cidrs: List[str] = None) -> None:
    """Update firewall configuration from gateway config.
    
    This is the main entry point for firewall management. It:
    1. Extracts peer IPs from config
    2. Updates peer IPs file
    3. Updates management CIDRs file (if provided)
    4. Reloads firewall rules if anything changed
    
    Args:
        cfg: Gateway configuration
        mgmt_cidrs: Optional list of management CIDRs for SSH access
    """
    peer_ips_changed = update_peer_ips(cfg)
    
    mgmt_cidrs_changed = False
    if mgmt_cidrs is not None:
        mgmt_cidrs_changed = update_management_cidrs(mgmt_cidrs)
    
    # Reload firewall if anything changed
    if peer_ips_changed or mgmt_cidrs_changed:
        reload_firewall()
    else:
        print("[FirewallMgr] No firewall changes needed")
