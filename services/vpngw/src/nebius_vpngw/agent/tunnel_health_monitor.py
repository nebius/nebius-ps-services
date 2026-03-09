"""Tunnel health monitor for detecting and recovering from stale IPsec tunnels.

This module implements automated detection and recovery for IPsec tunnel state desync
where strongSwan reports ESTABLISHED but the xfrm interface has stopped passing traffic.

Root Causes Addressed:
- NAT keepalive failures causing silent tunnel death
- Asymmetric routing dropping return packets
- Kernel/userspace XFRM state desynchronization
- DPD (Dead Peer Detection) false negatives

Detection Methods:
1. XFRM interface traffic counters (TX drops, RX zero bytes)
2. BGP session state (should be Established if tunnel is healthy)
3. Actual traffic test (ICMP/TCP probe through tunnel)

Recovery Actions:
1. Restart specific tunnel connection (least disruptive)
2. Full strongSwan restart if multiple tunnels affected
3. Alert logging for investigation

Best Practice References:
- AWS VPN: Monitors DPD and restarts tunnels on keepalive failure
- GCP HA VPN: Uses BFD for sub-second failure detection
- Azure VPN: Implements tunnel health probes with auto-recovery
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .tunnel_iterator import iter_active_tunnels

CONFIG_PATH = Path("/etc/nebius-vpngw/config-resolved.yaml")
LOCK_PATH = Path("/run/nebius-vpngw/health-monitor.lock")


@dataclass
class TunnelStats:
    """Statistics for a single xfrm interface."""

    interface: str
    rx_bytes: int
    rx_packets: int
    tx_bytes: int
    tx_packets: int
    tx_dropped: int
    tx_errors: int
    rx_errors: int


@dataclass
class TunnelHealth:
    """Health status for a tunnel."""

    name: str
    interface: str
    ipsec_status: str  # ESTABLISHED, CONNECTING, DOWN
    bgp_peer: str | None
    bgp_status: str | None  # Established, Idle, Connect, etc.
    stats: TunnelStats
    xfrm_present: bool
    is_healthy: bool
    failure_reasons: list[str]
    last_check_time: float


class TunnelHealthMonitor:
    """Monitor and recover unhealthy IPsec tunnels."""

    def __init__(
        self,
        check_interval_seconds: int = 10,
        enable_proactive_refresh: bool = False,
        proactive_refresh_hours: int = 8,
        max_failures_before_restart: int = 2,
        ping_enabled: bool = False,
    ) -> None:
        """Initialize tunnel health monitor.

        Args:
            check_interval_seconds: How often to check tunnel health (default: 10s)
            enable_proactive_refresh: Enable periodic tunnel refresh (default: False)
            proactive_refresh_hours: Hours between proactive refreshes (default: 8)
            max_failures_before_restart: Consecutive failures before restart (default: 2)
            ping_enabled: Enable ICMP ping to BGP peer (default: False)
        """
        self.check_interval = check_interval_seconds
        self.tunnel_states: dict[str, TunnelHealth] = {}
        self.consecutive_failures: dict[str, int] = {}
        self.max_failures_before_restart = max_failures_before_restart
        self.ping_enabled = ping_enabled

        # Proactive refresh settings (optional, disabled by default)
        self.enable_proactive_refresh = enable_proactive_refresh
        self.proactive_refresh_interval = proactive_refresh_hours * 3600
        self.last_proactive_refresh: dict[str, float] = {}

    def get_xfrm_interface_stats(self, interface: str) -> TunnelStats | None:
        """Get traffic statistics for an xfrm interface.

        Args:
            interface: Interface name (e.g., "xfrm0")

        Returns:
            TunnelStats object or None if interface doesn't exist
        """
        try:
            result = subprocess.run(
                ["ip", "-s", "link", "show", interface],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return None

            # Parse output
            # Format:
            # 4: xfrm0@eth0: <NOARP,UP,LOWER_UP> mtu 1386 qdisc noqueue state UNKNOWN
            #     RX:  bytes packets errors dropped  missed   mcast
            #              0       0      0       0       0       0
            #     TX:  bytes packets errors dropped carrier collsns
            #              0       0     41      41      41       0

            lines = result.stdout.strip().split("\n")
            rx_bytes = rx_packets = rx_errors = 0
            tx_bytes = tx_packets = tx_dropped = tx_errors = 0

            for i, line in enumerate(lines):
                if "RX:" in line and i + 1 < len(lines):
                    # Next line has RX stats
                    rx_parts = lines[i + 1].split()
                    if len(rx_parts) >= 3:
                        rx_bytes = int(rx_parts[0])
                        rx_packets = int(rx_parts[1])
                        rx_errors = int(rx_parts[2])

                elif "TX:" in line and i + 1 < len(lines):
                    # Next line has TX stats
                    tx_parts = lines[i + 1].split()
                    if len(tx_parts) >= 4:
                        tx_bytes = int(tx_parts[0])
                        tx_packets = int(tx_parts[1])
                        tx_errors = int(tx_parts[2])
                        tx_dropped = int(tx_parts[3])

            return TunnelStats(
                interface=interface,
                rx_bytes=rx_bytes,
                rx_packets=rx_packets,
                tx_bytes=tx_bytes,
                tx_packets=tx_packets,
                tx_dropped=tx_dropped,
                tx_errors=tx_errors,
                rx_errors=rx_errors,
            )

        except (subprocess.TimeoutExpired, ValueError, IndexError) as e:
            print(f"[TunnelMonitor] Error getting stats for {interface}: {e}")
            return None

    def get_ipsec_tunnel_status(self, tunnel_name: str) -> str:
        """Get IPsec tunnel status from strongSwan.

        Args:
            tunnel_name: Tunnel connection name

        Returns:
            "ESTABLISHED", "CONNECTING", or "DOWN"
        """
        if shutil.which("swanctl"):
            try:
                result = subprocess.run(
                    ["swanctl", "--list-sas"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and result.stdout:
                    for line in result.stdout.splitlines():
                        if tunnel_name in line and "ESTABLISHED" in line.upper():
                            return "ESTABLISHED"
                        if tunnel_name in line and "CONNECTING" in line.upper():
                            return "CONNECTING"
                return "DOWN"
            except subprocess.TimeoutExpired:
                return "UNKNOWN"

        try:
            result = subprocess.run(
                ["ipsec", "status", tunnel_name],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return "DOWN"

            output = result.stdout.lower()
            if "established" in output:
                return "ESTABLISHED"
            elif "connecting" in output or "negotiating" in output:
                return "CONNECTING"
            else:
                return "DOWN"

        except subprocess.TimeoutExpired:
            return "UNKNOWN"

    def get_bgp_peer_status(self, peer_ip: str) -> str | None:
        """Get BGP session status for a peer.

        Args:
            peer_ip: BGP peer IP address

        Returns:
            BGP state string or None if peer not found
        """
        try:
            result = subprocess.run(
                ["vtysh", "-c", f"show bgp neighbor {peer_ip} json"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0 and result.stdout:
                data = json.loads(result.stdout)
                peer_data = data.get(peer_ip, {})
                return peer_data.get("bgpState") or peer_data.get("state")

        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
            pass

        return None

    def check_tunnel_health(
        self, tunnel_name: str, interface: str, bgp_peer: str | None = None
    ) -> TunnelHealth:
        """Check health of a single tunnel.

        Args:
            tunnel_name: Tunnel connection name
            interface: XFRM interface name (e.g., "xfrm0")
            bgp_peer: BGP peer IP (optional, for BGP tunnels)

        Returns:
            TunnelHealth object with detailed status
        """
        stats = self.get_xfrm_interface_stats(interface)
        ipsec_status = self.get_ipsec_tunnel_status(tunnel_name)
        bgp_status = self.get_bgp_peer_status(bgp_peer) if bgp_peer else None

        failure_reasons = []
        is_healthy = True

        # Check 1: IPsec must be ESTABLISHED
        if ipsec_status != "ESTABLISHED":
            is_healthy = False
            failure_reasons.append(f"IPsec status: {ipsec_status}")

        # Check 1b: XFRM interface should exist when tunnel is up
        if stats is None:
            is_healthy = False
            failure_reasons.append(f"XFRM interface missing: {interface}")

        # Check 2: BGP session should be Established for BGP tunnels
        if bgp_peer and bgp_status:
            if bgp_status.lower() != "established":
                is_healthy = False
                failure_reasons.append(f"BGP state: {bgp_status}")

        # Check 3: Ping BGP peer to verify tunnel data plane is working
        # This is the most reliable check - if we can ping the BGP peer (169.254.x.x),
        # the tunnel XFRM interface is working correctly for data forwarding.
        # Note: We only test BGP peer connectivity, not remote networks, because
        # the gateway is a forwarder/router and shouldn't initiate traffic to remote networks.
        if self.ping_enabled and bgp_peer and ipsec_status == "ESTABLISHED":
            try:
                # Ping BGP peer (169.254.x.x address on the XFRM interface)
                # Use -c 2 for 2 packets, -W 1 for 1 second timeout
                result = subprocess.run(
                    ["ping", "-c", "2", "-W", "1", "-I", interface, bgp_peer],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                if result.returncode != 0:
                    is_healthy = False
                    failure_reasons.append(
                        f"BGP peer {bgp_peer} unreachable (tunnel data plane broken)"
                    )
            except subprocess.TimeoutExpired:
                is_healthy = False
                failure_reasons.append(f"Ping to BGP peer {bgp_peer} timed out")
            except Exception as e:
                # Don't fail health check on ping errors, just log
                print(f"[TunnelMonitor] ⚠ Ping check error for {bgp_peer}: {e}")

        return TunnelHealth(
            name=tunnel_name,
            interface=interface,
            ipsec_status=ipsec_status,
            bgp_peer=bgp_peer,
            bgp_status=bgp_status,
            stats=stats or TunnelStats(interface, 0, 0, 0, 0, 0, 0, 0),
            xfrm_present=stats is not None,
            is_healthy=is_healthy,
            failure_reasons=failure_reasons,
            last_check_time=time.time(),
        )

    def restart_tunnel(self, tunnel_name: str) -> bool:
        """Restart a specific IPsec tunnel connection.

        Args:
            tunnel_name: Tunnel connection name

        Returns:
            True if restart succeeded, False otherwise
        """
        print(f"[TunnelMonitor] 🔄 Restarting tunnel: {tunnel_name}")

        try:
            if shutil.which("swanctl"):
                def _command_output(result: subprocess.CompletedProcess[str]) -> str:
                    return (result.stderr or result.stdout or "").strip()

                def _wait_for_established(timeout_seconds: int = 12) -> bool:
                    deadline = time.monotonic() + timeout_seconds
                    while time.monotonic() < deadline:
                        if self.get_ipsec_tunnel_status(tunnel_name) == "ESTABLISHED":
                            return True
                        time.sleep(1)
                    return self.get_ipsec_tunnel_status(tunnel_name) == "ESTABLISHED"

                try:
                    load_result = subprocess.run(
                        ["swanctl", "--load-all"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if load_result.returncode != 0:
                        print(
                            f"[TunnelMonitor] ⚠ Failed to reload swanctl config for {tunnel_name}: "
                            f"{_command_output(load_result)}"
                        )
                except subprocess.TimeoutExpired:
                    print(
                        f"[TunnelMonitor] ⚠ Timeout reloading swanctl config for {tunnel_name}; proceeding with restart"
                    )

                terminate_attempts = [
                    (
                        ["swanctl", "--terminate", "--child", tunnel_name, "--timeout", "5"],
                        "CHILD_SA",
                    ),
                    (
                        ["swanctl", "--terminate", "--ike", tunnel_name, "--timeout", "5"],
                        "IKE_SA",
                    ),
                ]

                terminated = False
                for terminate_cmd, label in terminate_attempts:
                    result = subprocess.run(
                        terminate_cmd,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0:
                        terminated = True
                        break

                    output = _command_output(result)
                    if output:
                        print(
                            f"[TunnelMonitor] ⚠ Failed to terminate {label} for {tunnel_name}: {output}"
                        )

                if not terminated:
                    print(
                        "[TunnelMonitor] ⚠ Proceeding with tunnel initiate even though termination did not confirm"
                    )

                time.sleep(2)

                for attempt in range(1, 4):
                    result = subprocess.run(
                        ["swanctl", "--initiate", "--child", tunnel_name, "--timeout", "20"],
                        capture_output=True,
                        text=True,
                        timeout=25,
                    )
                    if result.returncode == 0 and _wait_for_established():
                        print(f"[TunnelMonitor] ✓ Successfully restarted tunnel: {tunnel_name}")
                        return True

                    if _wait_for_established(timeout_seconds=4):
                        print(
                            f"[TunnelMonitor] ✓ Tunnel {tunnel_name} recovered after initiate attempt {attempt}"
                        )
                        return True

                    output = _command_output(result)
                    if output:
                        print(
                            f"[TunnelMonitor] ⚠ Failed to initiate {tunnel_name} "
                            f"(attempt {attempt}/3): {output}"
                        )
                    if attempt < 3:
                        print(
                            f"[TunnelMonitor] ⚠ Retrying tunnel initiate for {tunnel_name} in 3s"
                        )
                        time.sleep(3)

                return False
            else:
                # Down the tunnel
                result = subprocess.run(
                    ["ipsec", "down", tunnel_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode != 0:
                    print(f"[TunnelMonitor] ⚠ Failed to bring down {tunnel_name}: {result.stderr}")
                    print("[TunnelMonitor] ⚠ Proceeding with tunnel up attempt anyway")

                # Wait briefly for cleanup
                time.sleep(2)

                # Up the tunnel
                result = subprocess.run(
                    ["ipsec", "up", tunnel_name],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )

                if result.returncode != 0:
                    print(f"[TunnelMonitor] ⚠ Failed to bring up {tunnel_name}: {result.stderr}")
                    return False

            print(f"[TunnelMonitor] ✓ Successfully restarted tunnel: {tunnel_name}")
            return True

        except subprocess.TimeoutExpired:
            print(f"[TunnelMonitor] ⚠ Timeout restarting tunnel: {tunnel_name}")
            return False

    def get_configured_tunnels(self) -> list[tuple[str, str, str | None]]:
        """Get list of configured tunnels from config file.

        Returns:
            List of tuples: (tunnel_name, interface_name, bgp_peer_ip)
        """
        if not CONFIG_PATH.exists():
            return []

        try:
            cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
            tunnels = []

            defaults_routing = cfg.get("defaults", {}).get("routing") or {}
            defaults_mode = defaults_routing.get("mode", "bgp")
            defaults_mode = getattr(defaults_mode, "value", defaults_mode)
            defaults_mode = str(defaults_mode).lower()

            def _normalize_mode(mode: Any, fallback: str) -> str:
                value = fallback if mode is None else mode
                value = getattr(value, "value", value)
                return str(value).lower()

            for idx, iface_name, conn, tun in iter_active_tunnels(cfg):
                name = tun.get("name") or f"tunnel{idx}"
                conn_mode = _normalize_mode(conn.get("routing_mode"), defaults_mode)
                tun_mode = _normalize_mode(tun.get("routing_mode"), conn_mode)
                bgp_enabled = (conn.get("bgp") or {}).get("enabled")

                bgp_peer = None
                if tun_mode == "bgp" and bgp_enabled is not False:
                    bgp_peer = tun.get("inner_remote_ip")

                tunnels.append((name, iface_name, bgp_peer))

            return tunnels

        except FileNotFoundError:
            print("[TunnelMonitor] ⚠️  Config file not found, no tunnels to monitor")
            return []
        except Exception as e:
            print(f"[TunnelMonitor] ⚠️  Error loading config: {e}")
            return []

    def run_health_check(self) -> dict[str, TunnelHealth]:
        """Run health check on all configured tunnels.

        Returns:
            Dictionary mapping tunnel names to TunnelHealth objects
        """
        tunnels = self.get_configured_tunnels()
        results = {}
        current_time = time.time()

        for tunnel_name, interface, bgp_peer in tunnels:
            previous_health = self.tunnel_states.get(tunnel_name)
            health = self.check_tunnel_health(tunnel_name, interface, bgp_peer)

            if previous_health and previous_health.xfrm_present and health.xfrm_present:
                stats = health.stats
                prev_stats = previous_health.stats
                delta_tx_dropped = stats.tx_dropped - prev_stats.tx_dropped
                delta_tx_errors = stats.tx_errors - prev_stats.tx_errors
                delta_rx_errors = stats.rx_errors - prev_stats.rx_errors

                if delta_tx_dropped > 0 or delta_tx_errors > 0 or delta_rx_errors > 0:
                    health.is_healthy = False
                    health.failure_reasons.append(
                        "XFRM errors increased "
                        f"(tx_dropped +{delta_tx_dropped}, "
                        f"tx_errors +{delta_tx_errors}, "
                        f"rx_errors +{delta_rx_errors})"
                    )
            results[tunnel_name] = health

            # Proactive refresh (optional, preventive maintenance)
            if self.enable_proactive_refresh and health.is_healthy:
                last_refresh = self.last_proactive_refresh.get(tunnel_name, 0)
                time_since_refresh = current_time - last_refresh

                if time_since_refresh >= self.proactive_refresh_interval:
                    print(
                        f"[TunnelMonitor] 🔄 Proactive refresh for {tunnel_name} "
                        f"(uptime: {time_since_refresh / 3600:.1f}h)"
                    )

                    if self.restart_tunnel(tunnel_name):
                        self.last_proactive_refresh[tunnel_name] = current_time
                        # Re-check health after proactive restart
                        time.sleep(5)
                        health = self.check_tunnel_health(tunnel_name, interface, bgp_peer)
                        results[tunnel_name] = health

                    continue  # Skip failure tracking for proactive refresh

            # Track consecutive failures (reactive detection)
            if not health.is_healthy:
                self.consecutive_failures[tunnel_name] = (
                    self.consecutive_failures.get(tunnel_name, 0) + 1
                )

                print(
                    f"[TunnelMonitor] ⚠ Tunnel {tunnel_name} unhealthy "
                    f"(failure #{self.consecutive_failures[tunnel_name]}): "
                    f"{', '.join(health.failure_reasons)}"
                )

                # Attempt restart if failures exceed threshold
                if self.consecutive_failures[tunnel_name] >= self.max_failures_before_restart:
                    print(
                        f"[TunnelMonitor] 🚨 Tunnel {tunnel_name} failed "
                        f"{self.consecutive_failures[tunnel_name]} consecutive checks"
                    )

                    if self.restart_tunnel(tunnel_name):
                        # Reset failure counter on successful restart
                        self.consecutive_failures[tunnel_name] = 0
                        # Give tunnel time to establish
                        time.sleep(5)
                        # Re-check health after restart
                        health = self.check_tunnel_health(tunnel_name, interface, bgp_peer)
                        results[tunnel_name] = health

                        if health.is_healthy:
                            print(f"[TunnelMonitor] ✓ Tunnel {tunnel_name} recovered after restart")
                        else:
                            print(
                                f"[TunnelMonitor] ⚠ Tunnel {tunnel_name} still unhealthy after restart"
                            )
                else:
                    # Immediate re-check after first failure (don't wait full interval)
                    # This reduces detection time from 20s to ~15s (10s + 5s re-check)
                    print("[TunnelMonitor] 🔄 Immediate re-check in 5 seconds...")
                    time.sleep(5)
                    health_recheck = self.check_tunnel_health(tunnel_name, interface, bgp_peer)

                    if not health_recheck.is_healthy:
                        # Second failure confirmed immediately
                        self.consecutive_failures[tunnel_name] += 1
                        print(
                            f"[TunnelMonitor] ⚠ Tunnel {tunnel_name} still unhealthy after re-check "
                            f"(failure #{self.consecutive_failures[tunnel_name]})"
                        )

                        # Check again if we hit threshold after immediate recheck
                        if (
                            self.consecutive_failures[tunnel_name]
                            >= self.max_failures_before_restart
                        ):
                            print(
                                f"[TunnelMonitor] 🚨 Tunnel {tunnel_name} failed "
                                f"{self.consecutive_failures[tunnel_name]} consecutive checks"
                            )

                            if self.restart_tunnel(tunnel_name):
                                self.consecutive_failures[tunnel_name] = 0
                                time.sleep(5)
                                health = self.check_tunnel_health(tunnel_name, interface, bgp_peer)
                                results[tunnel_name] = health

                                if health.is_healthy:
                                    print(
                                        f"[TunnelMonitor] ✓ Tunnel {tunnel_name} recovered after restart"
                                    )
                    else:
                        # False positive - tunnel recovered on its own
                        print(
                            f"[TunnelMonitor] ✓ Tunnel {tunnel_name} recovered (transient failure)"
                        )
                        del self.consecutive_failures[tunnel_name]
                        results[tunnel_name] = health_recheck
            else:
                # Reset failure counter on success
                if tunnel_name in self.consecutive_failures:
                    del self.consecutive_failures[tunnel_name]

            # Store state
            self.tunnel_states[tunnel_name] = health

        return results

    def get_health_summary(self) -> dict[str, Any]:
        """Get summary of tunnel health status.

        Returns:
            Dictionary with health summary
        """
        total_tunnels = len(self.tunnel_states)
        healthy_tunnels = sum(1 for h in self.tunnel_states.values() if h.is_healthy)
        unhealthy_tunnels = total_tunnels - healthy_tunnels

        return {
            "total_tunnels": total_tunnels,
            "healthy_tunnels": healthy_tunnels,
            "unhealthy_tunnels": unhealthy_tunnels,
            "overall_health": "healthy" if unhealthy_tunnels == 0 else "degraded",
            "tunnels": {
                name: {
                    "interface": health.interface,
                    "ipsec_status": health.ipsec_status,
                    "bgp_status": health.bgp_status,
                    "is_healthy": health.is_healthy,
                    "failure_reasons": health.failure_reasons,
                    "tx_dropped": health.stats.tx_dropped,
                    "rx_packets": health.stats.rx_packets,
                    "tx_packets": health.stats.tx_packets,
                }
                for name, health in self.tunnel_states.items()
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VPN tunnel health monitor",
        epilog=(
            "Examples:\\n"
            "  # Check health once\\n"
            "  python3 -m nebius_vpngw.agent.tunnel_health_monitor --once\\n\\n"
            "  # Continuous monitoring (reactive mode)\\n"
            "  python3 -m nebius_vpngw.agent.tunnel_health_monitor --check-interval 10\\n\\n"
            "  # Restart specific tunnel manually\\n"
            "  python3 -m nebius_vpngw.agent.tunnel_health_monitor --restart-tunnel gcp-ha-tunnel-1\\n\\n"
            "  # Restart all tunnels\\n"
            "  python3 -m nebius_vpngw.agent.tunnel_health_monitor --restart-tunnel all\\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check-interval",
        type=int,
        default=10,
        help="Health check interval in seconds (default: 10)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (default: continuous monitoring)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--proactive-refresh",
        action="store_true",
        help="Enable proactive tunnel refresh (periodic restart)",
    )
    parser.add_argument(
        "--refresh-hours",
        type=int,
        default=8,
        help="Hours between proactive refreshes (default: 8)",
    )
    parser.add_argument(
        "--restart-tunnel",
        type=str,
        metavar="TUNNEL_NAME",
        help="Manually restart a specific tunnel by name, or 'all' to restart all tunnels. "
        "Use with 'swanctl --list-sas' (or 'ipsec statusall') to find tunnel names. This is useful for quick "
        "recovery from stale tunnel state without waiting for automatic detection.",
    )
    parser.add_argument(
        "--config",
        type=str,
        metavar="CONFIG_FILE",
        help="Load settings from YAML config file (reads defaults.health_monitoring section)",
    )

    args = parser.parse_args()

    # Handle manual tunnel restart
    if args.restart_tunnel:
        monitor_temp = TunnelHealthMonitor(check_interval_seconds=10)

        if args.restart_tunnel.lower() == "all":
            print("[TunnelMonitor] 🔄 Restarting ALL tunnels...")
            tunnels = monitor_temp.get_configured_tunnels()
            success_count = 0
            for tunnel_name, _, _ in tunnels:
                if monitor_temp.restart_tunnel(tunnel_name):
                    success_count += 1

            print(f"[TunnelMonitor] ✅ Restarted {success_count}/{len(tunnels)} tunnels")
            if success_count < len(tunnels):
                exit(1)
            exit(0)
        else:
            print(f"[TunnelMonitor] 🔄 Restarting tunnel: {args.restart_tunnel}")
            if monitor_temp.restart_tunnel(args.restart_tunnel):
                print(f"[TunnelMonitor] ✅ Tunnel {args.restart_tunnel} restarted successfully")
                exit(0)
            else:
                print(f"[TunnelMonitor] ❌ Failed to restart tunnel {args.restart_tunnel}")
                exit(1)

    # Load config from YAML if provided
    config_check_interval = args.check_interval
    config_proactive = args.proactive_refresh
    config_refresh_hours = args.refresh_hours
    config_max_failures = 2
    config_enabled = True
    config_ping_enabled = False

    if args.config:
        try:
            from pathlib import Path

            import yaml

            config_path = Path(args.config)
            if not config_path.exists():
                print(f"[TunnelMonitor] ⚠️  Config file not found: {args.config}")
                print("[TunnelMonitor] Using command-line defaults")
            else:
                with open(config_path) as f:
                    config_data = yaml.safe_load(f)

                health_config = config_data.get("defaults", {}).get("health_monitoring", {})

                if health_config:
                    # Override with config file values if not explicitly set via CLI
                    if args.check_interval == 10:  # Default value
                        config_check_interval = health_config.get("check_interval_seconds", 10)
                    if not args.proactive_refresh:  # Not set via CLI
                        config_proactive = health_config.get("proactive_refresh_enabled", False)
                    if args.refresh_hours == 8:  # Default value
                        config_refresh_hours = health_config.get("proactive_refresh_hours", 8)
                    config_max_failures = int(
                        health_config.get("max_failures_before_restart", config_max_failures)
                    )
                    config_enabled = health_config.get("enabled", True)
                    config_ping_enabled = health_config.get("ping_enabled", False)

                    print(f"[TunnelMonitor] 📝 Loaded config from: {args.config}")
                    print(f"[TunnelMonitor]    check_interval: {config_check_interval}s")
                    print(f"[TunnelMonitor]    proactive_refresh: {config_proactive}")
                    print(f"[TunnelMonitor]    max_failures_before_restart: {config_max_failures}")
                    print(f"[TunnelMonitor]    ping_enabled: {config_ping_enabled}")
                    if config_proactive:
                        print(f"[TunnelMonitor]    refresh_hours: {config_refresh_hours}h")
        except Exception as e:
            print(f"[TunnelMonitor] ⚠️  Failed to load config: {e}")
            print("[TunnelMonitor] Using command-line defaults")

    if args.config and not config_enabled and not args.once:
        print("[TunnelMonitor] Health monitoring disabled in config; exiting")
        return

    lock_file = None
    if not args.once:
        try:
            LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            lock_file = LOCK_PATH.open("w")
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_file.write(str(os.getpid()))
            lock_file.flush()
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                print("[TunnelMonitor] Another health monitor instance is already running; exiting")
            elif e.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
                print(f"[TunnelMonitor] Lock path not writable: {LOCK_PATH} ({e})")
            elif e.errno == errno.ENOENT:
                print(f"[TunnelMonitor] Lock directory missing: {LOCK_PATH.parent} ({e})")
            else:
                print(f"[TunnelMonitor] Failed to initialize lock file: {e}")
            return

    monitor = TunnelHealthMonitor(
        check_interval_seconds=config_check_interval,
        enable_proactive_refresh=config_proactive,
        proactive_refresh_hours=config_refresh_hours,
        max_failures_before_restart=config_max_failures,
        ping_enabled=config_ping_enabled,
    )

    if args.once:
        # Single check
        results = monitor.run_health_check()
        summary = monitor.get_health_summary()

        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"\n{'=' * 60}")
            print("Tunnel Health Check Results")
            print(f"{'=' * 60}")
            print(f"Total Tunnels: {summary['total_tunnels']}")
            print(f"Healthy: {summary['healthy_tunnels']}")
            print(f"Unhealthy: {summary['unhealthy_tunnels']}")
            print(f"Overall Health: {summary['overall_health'].upper()}")
            print(f"{'=' * 60}\n")

            for name, health in results.items():
                status_icon = "✓" if health.is_healthy else "✗"
                print(f"{status_icon} {name} ({health.interface})")
                print(f"  IPsec: {health.ipsec_status}")
                if health.bgp_peer:
                    print(f"  BGP: {health.bgp_status or 'N/A'} (peer: {health.bgp_peer})")
                print(f"  RX: {health.stats.rx_packets} packets / {health.stats.rx_bytes} bytes")
                print(f"  TX: {health.stats.tx_packets} packets / {health.stats.tx_bytes} bytes")
                if health.stats.tx_dropped > 0:
                    print(f"  ⚠ TX DROPPED: {health.stats.tx_dropped} packets")
                if health.failure_reasons:
                    print(f"  Failures: {', '.join(health.failure_reasons)}")
                print()
    else:
        # Continuous monitoring
        print(
            f"[TunnelMonitor] Starting continuous monitoring (interval: {config_check_interval}s)"
        )
        print(
            f"[TunnelMonitor] Will restart tunnels after {monitor.max_failures_before_restart} consecutive failures"
        )

        if monitor.enable_proactive_refresh:
            print(
                f"[TunnelMonitor] Proactive refresh ENABLED: "
                f"tunnels will be restarted every {config_refresh_hours}h"
            )
        else:
            print("[TunnelMonitor] Proactive refresh DISABLED (reactive mode only)")

        try:
            while True:
                try:
                    monitor.run_health_check()
                    summary = monitor.get_health_summary()

                    print(
                        f"[TunnelMonitor] Health: {summary['healthy_tunnels']}/{summary['total_tunnels']} healthy"
                    )

                    time.sleep(config_check_interval)

                except KeyboardInterrupt:
                    print("\n[TunnelMonitor] Stopping monitor")
                    break
                except Exception as e:
                    print(f"[TunnelMonitor] Error in monitoring loop: {e}")
                    time.sleep(config_check_interval)
        finally:
            if lock_file is not None:
                lock_file.close()


if __name__ == "__main__":
    main()
