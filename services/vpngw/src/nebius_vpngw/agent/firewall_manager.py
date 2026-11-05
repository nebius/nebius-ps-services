"""Firewall management for VPN gateway.

This module manages UFW firewall rules to ensure:
- IPsec protocols (IKE, NAT-T, ESP) are allowed from VPN peers
- SSH source filtering is owned by customer Security Groups
- Local VPC prefixes are allowed for forwarding
- XFRM interfaces are not filtered (BGP traffic flows freely)
"""

import fcntl
import shlex
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import ingress_policy
from ..tunnel_state import collect_tunnel_state
from .local_commands import run

PEER_IPS_FILE = Path("/etc/vpngw_peer_ips")
LOCAL_PREFIXES_FILE = Path("/etc/vpngw_local_prefixes")
FIREWALL_SETUP_SCRIPT = Path("/usr/local/bin/setup-vpngw-firewall.sh")
UFW_USER_RULES = Path("/etc/ufw/user.rules")
UFW_LOCK = Path("/run/ufw.lock")
UFW_LOCK_TIMEOUT = 5.0


def _tunnel_host_rules(cfg: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Plan rules without depending on whether XFRM devices already exist."""
    _, _, endpoints = collect_tunnel_state(cfg, log=lambda message: None)
    rules = []
    for endpoint in endpoints:
        interface = endpoint["name"]
        rules.append(("ufw-user-input", "-i", interface))
        rules.append(("ufw-user-output", "-o", interface))
    return rules


def _missing_tunnel_host_rules(
    rules: list[tuple[str, str, str]], *, save_text: str | None = None
) -> list[tuple[str, str, str]]:
    if not rules:
        return []
    if save_text is None:
        result = run(["iptables-save", "-t", "filter"], capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            raise RuntimeError("unable to verify tunnel host firewall rules")
        save_text = result.stdout
    installed = set()
    for line in save_text.splitlines():
        if line.startswith("-A "):
            installed.add(tuple(shlex.split(line)))
    missing = []
    for chain, direction, interface in rules:
        if ("-A", chain, direction, interface, "-j", "ACCEPT") not in installed:
            missing.append((chain, direction, interface))
    return missing


def verify_tunnel_host_rules(cfg: dict[str, Any], *, save_text: str | None = None) -> None:
    """Require host INPUT/OUTPUT access independently of transit forwarding."""
    if _missing_tunnel_host_rules(_tunnel_host_rules(cfg), save_text=save_text):
        raise RuntimeError("required tunnel host firewall rules are missing")


@contextmanager
def _ufw_lock() -> Iterator[None]:
    """Share UFW's POSIX record lock, never while running the UFW CLI itself."""
    deadline = time.monotonic() + UFW_LOCK_TIMEOUT
    with UFW_LOCK.open("a") as lock:
        while True:
            try:
                fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("timed out locking tunnel host firewall policy") from None
                time.sleep(min(0.05, remaining))
        try:
            yield
        finally:
            fcntl.lockf(lock, fcntl.LOCK_UN)


def _filter_policy(save_text: str) -> tuple[set[str], dict[str, list[str]]]:
    """Read one complete filter table without interpreting iptables matches."""
    chains: set[str] = set()
    rules: dict[str, list[str]] = {}
    in_filter = False
    seen_filter = False
    complete = False
    for raw_line in save_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "*filter":
            if seen_filter:
                raise ValueError("duplicate filter table")
            seen_filter = in_filter = True
        elif in_filter:
            if line == "COMMIT":
                in_filter = False
                complete = True
            elif line.startswith(":"):
                parts = shlex.split(line)
                if len(parts) != 3 or parts[0][1:] in chains:
                    raise ValueError("invalid chain declaration")
                chains.add(parts[0][1:])
            else:
                parts = shlex.split(line)
                # UFW emits an insert in its logging chain. Those commands are
                # parsed for completeness but never replayed as host policy.
                if len(parts) < 3 or parts[0] not in {"-A", "-I"}:
                    raise ValueError("unsupported filter rule")
                rules.setdefault(parts[1], []).append(line)
        else:
            raise ValueError("unexpected content outside filter table")
    if not complete or in_filter or not rules.keys() <= chains:
        raise ValueError("incomplete filter policy")
    return chains, rules


def _restore_saved_host_rules(rules: list[tuple[str, str, str]]) -> None:
    """Replay only affected UFW-owned host chains, retaining saved rule order."""
    with _ufw_lock():
        result = run(["iptables-save", "-t", "filter"], capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            raise RuntimeError("unable to read tunnel host firewall policy")
        missing = _missing_tunnel_host_rules(rules, save_text=result.stdout)
        if not missing:
            return
        affected = sorted({chain for chain, _, _ in missing})
        try:
            live_chains, _ = _filter_policy(result.stdout)
            saved = UFW_USER_RULES.read_text(encoding="utf-8")
            saved_chains, saved_rules = _filter_policy(saved)
            if not set(affected) <= live_chains & saved_chains:
                raise ValueError("host chain missing")
            if any(
                not line.startswith("-A ")
                for chain in affected
                for line in saved_rules.get(chain, [])
            ):
                raise ValueError("unsupported host chain command")
            saved_filter_rules = "\n".join(line for lines in saved_rules.values() for line in lines)
            if _missing_tunnel_host_rules(rules, save_text=saved_filter_rules):
                raise ValueError("saved permit missing")
        except (OSError, ValueError):
            raise RuntimeError("unable to restore saved tunnel host firewall policy") from None
        # --noflush retains other chains but rebuilds declared user chains. The
        # native parser preserves implicit matches, comments and saved ordering.
        payload = "\n".join(
            [
                "*filter",
                *(f":{chain} - [0:0]" for chain in affected),
                *(line for chain in affected for line in saved_rules.get(chain, [])),
                "COMMIT",
                "",
            ]
        )
        for test in (True, False):
            command = ["iptables-restore", "--noflush", "--wait", "5"]
            if test:
                command.append("--test")
            restored = run(command, input=payload, capture_output=True, text=True, timeout=20)
            if restored.returncode != 0:
                raise RuntimeError("unable to restore saved tunnel host firewall rules")
        if _missing_tunnel_host_rules(rules):
            raise RuntimeError("required tunnel host firewall rules are missing after repair")


def ensure_tunnel_host_rules(cfg: dict[str, Any]) -> None:
    """Repair missing rules, including on first boot before device creation.

    UFW persists interface rules even when the interface does not exist yet.
    Recheck live rules before reporting firewall reconciliation success.
    """
    rules = _tunnel_host_rules(cfg)
    missing = _missing_tunnel_host_rules(rules)
    if not missing:
        return
    for chain, _, interface in missing:
        direction = "in" if chain == "ufw-user-input" else "out"
        result = run(
            ["ufw", "allow", direction, "on", interface],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            raise RuntimeError("unable to install tunnel host firewall rules")
    if _missing_tunnel_host_rules(rules):
        _restore_saved_host_rules(rules)


def update_peer_ips(cfg: dict[str, Any]) -> bool:
    """Extract peer public IPs from config and update /etc/vpngw_peer_ips.

    Args:
        cfg: Gateway configuration containing connections and peer info

    Returns:
        True if file was updated, False if unchanged
    """
    peer_ips: set[str] = set()

    # Extract peer IPs from connections
    for conn in cfg.get("connections", []):
        for tun in conn.get("tunnels", []):
            remote_ip = (tun.get("remote_public_ip") or "").strip()
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
        "",
    ]
    lines.extend(sorted(peer_ips))
    new_content = "\n".join(lines) + "\n"

    # Update if changed
    if new_content != current_content:
        PEER_IPS_FILE.write_text(new_content)
        print(f"[FirewallMgr] Updated {PEER_IPS_FILE} with {len(peer_ips)} peer IP(s)")
        return True

    return False


def update_local_prefixes(prefixes: list[str]) -> bool:
    """Update /etc/vpngw_local_prefixes with allowed local VPC prefixes.

    Args:
        prefixes: List of CIDR strings (e.g., ["10.0.0.0/16", "10.1.0.0/16"])

    Returns:
        True if file was updated, False if unchanged
    """
    current_content = ""
    if LOCAL_PREFIXES_FILE.exists():
        current_content = LOCAL_PREFIXES_FILE.read_text()

    lines = [
        "# Local VPC prefixes allowed through firewall (auto-generated)",
        "# One CIDR per line",
        "",
    ]
    lines.extend(sorted(set(prefixes)))
    new_content = "\n".join(lines) + "\n"

    if new_content != current_content:
        LOCAL_PREFIXES_FILE.write_text(new_content)
        print(f"[FirewallMgr] Updated {LOCAL_PREFIXES_FILE} with {len(prefixes)} prefix(es)")
        return True

    return False


def reload_firewall() -> bool:
    """Reload UFW firewall rules by running the setup script.

    This re-applies firewall rules based on current VPN peer IPs.
    Safe to call multiple times - script is idempotent.

    Returns:
        True if reload succeeded, False otherwise
    """
    if not FIREWALL_SETUP_SCRIPT.exists():
        print(f"[FirewallMgr] WARNING: Firewall setup script not found at {FIREWALL_SETUP_SCRIPT}")
        return False

    print("[FirewallMgr] Reloading UFW firewall rules...")

    result = run(["bash", str(FIREWALL_SETUP_SCRIPT)], capture_output=True, text=True, timeout=30)

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


def update_firewall_from_config(
    cfg: dict[str, Any],
    *,
    require_reload: bool = False,
) -> None:
    """Update firewall configuration from gateway config.

    This is the main entry point for firewall management. It:
    1. Extracts peer IPs from config
    2. Updates peer IPs file
    3. Updates peer-only effective ingress policy
    4. Updates local prefix rules
    5. Reloads firewall rules if anything changed

    Args:
        cfg: Gateway configuration
    """
    policy = ingress_policy.from_config(cfg)
    # Validate every path before files or rules change, including passive HA.
    ingress_policy.install(policy, run=run)
    peer_ips_changed = update_peer_ips(cfg)
    local_prefixes = (cfg.get("gateway", {}) or {}).get("local_prefixes", []) or []
    local_prefixes_changed = update_local_prefixes(local_prefixes)

    # Reload firewall if anything changed
    if peer_ips_changed or local_prefixes_changed or require_reload:
        if not reload_firewall():
            raise RuntimeError("required firewall reload failed")
    else:
        print("[FirewallMgr] No firewall changes needed")

    ensure_tunnel_host_rules(cfg)
    ingress_policy.verify(policy, run=run)
