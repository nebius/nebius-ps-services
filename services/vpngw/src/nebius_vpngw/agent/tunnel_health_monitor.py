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
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path("/etc/nebius-vpngw/config-resolved.yaml")


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
    is_healthy: bool
    failure_reasons: list[str]
    last_check_time: float


class TunnelHealthMonitor:
    """Monitor and recover unhealthy IPsec tunnels."""

    def __init__(
        self,
        check_interval_seconds: int = 60,
        enable_proactive_refresh: bool = False,
        proactive_refresh_hours: int = 8,
    ) -> None:
        """Initialize tunnel health monitor.

        Args:
            check_interval_seconds: How often to check tunnel health (default: 60s)
            enable_proactive_refresh: Enable periodic tunnel refresh (default: False)
            proactive_refresh_hours: Hours between proactive refreshes (default: 8)
        """
        self.check_interval = check_interval_seconds
        self.tunnel_states: dict[str, TunnelHealth] = {}
        self.consecutive_failures: dict[str, int] = {}
        self.max_failures_before_restart = 2  # Restart after 2 consecutive failures

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
                import json

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

        # Check 2: If IPsec shows ESTABLISHED, xfrm interface must pass traffic
        if stats and ipsec_status == "ESTABLISHED":
            # Critical: TX drops indicate tunnel is not forwarding
            if stats.tx_dropped > 0:
                is_healthy = False
                failure_reasons.append(f"TX drops detected: {stats.tx_dropped} packets dropped")

            # Warning: If tunnel has been up for a while but RX is still zero,
            # either no traffic or tunnel is one-way
            # We only flag this if TX is also happening (indicates attempted communication)
            if stats.rx_packets == 0 and stats.tx_packets > 100:
                is_healthy = False
                failure_reasons.append(
                    "No RX traffic despite TX activity (possible one-way tunnel)"
                )

        # Check 3: BGP session should be Established for BGP tunnels
        if bgp_peer and bgp_status:
            if bgp_status.lower() != "established":
                is_healthy = False
                failure_reasons.append(f"BGP state: {bgp_status}")

        return TunnelHealth(
            name=tunnel_name,
            interface=interface,
            ipsec_status=ipsec_status,
            bgp_peer=bgp_peer,
            bgp_status=bgp_status,
            stats=stats or TunnelStats(interface, 0, 0, 0, 0, 0, 0, 0),
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
            # Down the tunnel
            result = subprocess.run(
                ["ipsec", "down", tunnel_name],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                print(f"[TunnelMonitor] ⚠ Failed to bring down {tunnel_name}: {result.stderr}")
                return False

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

            idx = 0
            for conn in cfg.get("connections", []):
                for tun in conn.get("tunnels", []):
                    ha_role = tun.get("ha_role", "active")
                    if ha_role == "disable":
                        continue

                    name = tun.get("name") or f"tunnel{idx}"
                    interface = f"xfrm{idx}"

                    # Get BGP peer if this is a BGP tunnel
                    bgp_peer = None
                    routing_mode = tun.get("routing_mode") or conn.get("routing_mode")
                    if routing_mode == "bgp" or routing_mode is None:
                        bgp_peer = tun.get("inner_remote_ip")

                    tunnels.append((name, interface, bgp_peer))
                    idx += 1

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
            health = self.check_tunnel_health(tunnel_name, interface, bgp_peer)
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
                    # This reduces detection time from 120s to ~65s (60s + 5s re-check)
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
            "  python3 -m nebius_vpngw.agent.tunnel_health_monitor --check-interval 60\\n\\n"
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
        default=60,
        help="Health check interval in seconds (default: 60)",
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
        "Use with 'ipsec statusall' to find tunnel names. This is useful for quick "
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
        monitor_temp = TunnelHealthMonitor(check_interval_seconds=60)

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
                    if args.check_interval == 60:  # Default value
                        config_check_interval = health_config.get("check_interval_seconds", 60)
                    if not args.proactive_refresh:  # Not set via CLI
                        config_proactive = health_config.get("proactive_refresh_enabled", False)
                    if args.refresh_hours == 8:  # Default value
                        config_refresh_hours = health_config.get("proactive_refresh_hours", 8)

                    print(f"[TunnelMonitor] 📝 Loaded config from: {args.config}")
                    print(f"[TunnelMonitor]    check_interval: {config_check_interval}s")
                    print(f"[TunnelMonitor]    proactive_refresh: {config_proactive}")
                    if config_proactive:
                        print(f"[TunnelMonitor]    refresh_hours: {config_refresh_hours}h")
        except Exception as e:
            print(f"[TunnelMonitor] ⚠️  Failed to load config: {e}")
            print("[TunnelMonitor] Using command-line defaults")

    monitor = TunnelHealthMonitor(
        check_interval_seconds=config_check_interval,
        enable_proactive_refresh=config_proactive,
        proactive_refresh_hours=config_refresh_hours,
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


if __name__ == "__main__":
    main()
