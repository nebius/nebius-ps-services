"""Observe typed tunnel health and supervise bounded ordinary component repair.

Native strongSwan owns peer retries. The ordinary monitor repairs positively
failed components or lost authorized daemon configuration; HA monitoring is
observation-only. Explicit proactive refresh runs only after complete health.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from ..ipsec_observation import ConfigurationInventory, snapshot
from ..ipsec_observation import command as observation_command
from .strongswan_renderer import authorized_configuration
from .tunnel_iterator import iter_active_tunnels

CONFIG_PATH = Path("/etc/nebius-vpngw/config-resolved.yaml")
LOCK_PATH = Path("/run/nebius-vpngw/health-monitor.lock")
COMPONENT_RECOVERY_PATH = Path("/var/lib/nebius-vpngw/component-recovery.json")


def _vm_ha_observer_only(config: object) -> bool:
    """Recognize both deployed runtime and operator-facing VM-HA config shapes."""

    if not isinstance(config, dict):
        return False
    runtime_vm_ha = config.get("vm_ha")
    if isinstance(runtime_vm_ha, dict):
        return runtime_vm_ha.get("enabled") is not False
    gateway_group = config.get("gateway_group")
    if not isinstance(gateway_group, dict):
        return False
    operator_vm_ha = gateway_group.get("vm_ha")
    return isinstance(operator_vm_ha, dict) and operator_vm_ha.get("enabled") is True


def _installed_observer_only() -> bool:
    """Fail closed when installed topology cannot establish ordinary ownership."""
    try:
        config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError(
            "Cannot read installed gateway topology; tunnel repair is blocked"
        ) from error
    if not isinstance(config, dict):
        raise ValueError("Invalid installed gateway topology; tunnel repair is blocked")
    return _vm_ha_observer_only(config)


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
    assessment: Literal["healthy", "unhealthy", "unknown"]
    failure_reasons: list[str]
    last_check_time: float

    @property
    def is_healthy(self) -> bool:
        return self.assessment == "healthy"


class TunnelHealthMonitor:
    """Monitor and recover unhealthy IPsec tunnels."""

    def __init__(
        self,
        check_interval_seconds: int = 10,
        enable_proactive_refresh: bool = False,
        proactive_refresh_hours: int = 8,
        max_failures_before_restart: int = 2,
        ping_enabled: bool = False,
        observer_only: bool = False,
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
        self.observer_only = observer_only

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

            received = transmitted = False
            for i, line in enumerate(lines):
                if "RX:" in line and i + 1 < len(lines):
                    # Next line has RX stats
                    rx_parts = lines[i + 1].split()
                    if len(rx_parts) >= 3:
                        received = True
                        rx_bytes = int(rx_parts[0])
                        rx_packets = int(rx_parts[1])
                        rx_errors = int(rx_parts[2])

                elif "TX:" in line and i + 1 < len(lines):
                    # Next line has TX stats
                    tx_parts = lines[i + 1].split()
                    if len(tx_parts) >= 4:
                        transmitted = True
                        tx_bytes = int(tx_parts[0])
                        tx_packets = int(tx_parts[1])
                        tx_errors = int(tx_parts[2])
                        tx_dropped = int(tx_parts[3])

            if not received or not transmitted:
                return None
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

        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as e:
            print(f"[TunnelMonitor] Error getting stats for {interface}: {e}")
            return None

    def get_ipsec_tunnel_status(self, tunnel_name: str, if_id: int | None = None) -> str:
        """Use complete raw IKE/CHILD evidence; unavailable observations are unknown."""
        if if_id is None:
            # Direct recovery callers resolve the same installed per-VM ordering.
            try:
                cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
                matches = [
                    idx + 100
                    for idx, _iface, _conn, tun in iter_active_tunnels(cfg)
                    if tun.get("name", f"tunnel{idx}") == tunnel_name
                ]
                if len(matches) != 1:
                    return "UNKNOWN"
                if_id = matches[0]
            except (OSError, ValueError, TypeError, yaml.YAMLError):
                return "UNKNOWN"
        try:
            return "ESTABLISHED" if snapshot(5).ready(tunnel_name, if_id) else "DOWN"
        except ValueError:
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
                if not isinstance(data, dict):
                    return None
                if peer_ip not in data:
                    return "Missing"
                peer_data = data[peer_ip]
                if not isinstance(peer_data, dict):
                    return None
                state = peer_data.get("bgpState") or peer_data.get("state")
                return state if isinstance(state, str) and state else None

        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
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
        if_id = int(interface[4:]) + 100 if re.fullmatch(r"xfrm[0-9]+", interface) else None
        ipsec_status = self.get_ipsec_tunnel_status(tunnel_name, if_id)
        bgp_status = self.get_bgp_peer_status(bgp_peer) if bgp_peer else None

        failure_reasons = []
        down = False
        unknown = False

        # Check 1: IPsec must be ESTABLISHED
        if ipsec_status != "ESTABLISHED":
            unknown |= ipsec_status == "UNKNOWN"
            down |= ipsec_status != "UNKNOWN"
            failure_reasons.append(f"IPsec status: {ipsec_status}")

        # Check 1b: XFRM interface should exist when tunnel is up
        if stats is None:
            unknown = True
            failure_reasons.append(f"XFRM statistics unavailable: {interface}")

        # Check 2: BGP session should be Established for BGP tunnels
        if bgp_peer:
            if bgp_status is None:
                unknown = True
                failure_reasons.append("BGP observation unavailable")
            elif bgp_status.lower() != "established":
                down = True
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

                if result.returncode == 1:
                    down = True
                    failure_reasons.append(
                        f"BGP peer {bgp_peer} unreachable (tunnel data plane broken)"
                    )
                elif result.returncode != 0:
                    unknown = True
                    failure_reasons.append("Ping observation unavailable")
            except subprocess.TimeoutExpired:
                unknown = True
                failure_reasons.append("Ping observation timed out")
            except Exception:
                unknown = True
                failure_reasons.append("Ping observation unavailable")

        return TunnelHealth(
            name=tunnel_name,
            interface=interface,
            ipsec_status=ipsec_status,
            bgp_peer=bgp_peer,
            bgp_status=bgp_status,
            stats=stats or TunnelStats(interface, 0, 0, 0, 0, 0, 0, 0),
            xfrm_present=stats is not None,
            assessment="unhealthy" if down else "unknown" if unknown else "healthy",
            failure_reasons=failure_reasons,
            last_check_time=time.time(),
        )

    def restart_tunnel(self, tunnel_name: str) -> bool:
        from ..ordinary_operations import OperationBlocked, mutation_lock

        if self.observer_only:
            return False
        try:
            with mutation_lock():
                if _installed_observer_only():
                    print("[TunnelMonitor] VM-HA controller owns tunnel repair; skipping")
                    return False
                return self._restart_tunnel_locked(tunnel_name)
        except ValueError as error:
            print(f"[TunnelMonitor] {error}")
            return False
        except (OperationBlocked, BlockingIOError):
            print("[TunnelMonitor] Ordinary operation pending; skipping tunnel repair")
            return False

    def _restart_tunnel_locked(self, tunnel_name: str) -> bool:
        """Use the same single-submit IPsec owner while retaining the guest lock."""
        from ..tunnel_restart import Restart, targets

        try:
            config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
            selected = [target for target in targets(config) if target["name"] == tunnel_name]
            if len(selected) != 1 or not shutil.which("swanctl"):
                return False
            operation = Restart(time.monotonic() + 132)
            operation.validate_targets(selected)
            return operation.restart_ipsec(selected[0]) and operation.wait(
                selected[0], operation.end(5)
            )
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
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

    def _repair_failed_components(self, healthy: bool) -> None:
        """One bounded attempt per positively failed component until 60s health.

        Missing SAs, BGP withdrawals and unknown observations never enter this
        owner. The durable record is written before a mutation can be submitted.
        """
        if self.observer_only:
            return
        from .. import ordinary_operations as ops
        from .vm_ha.store import atomic_write_json

        path = COMPONENT_RECOVERY_PATH
        try:
            with ops.mutation_lock():
                if _installed_observer_only():
                    return
                value = (
                    json.loads(path.read_text())
                    if path.exists()
                    else {"attempted": [], "healthy_since": None, "failed_observations": {}}
                )
                if (
                    not isinstance(value, dict)
                    or set(value) != {"attempted", "healthy_since", "failed_observations"}
                    or not isinstance(value["attempted"], list)
                    or any(
                        unit not in {"strongswan.service", "frr.service"}
                        for unit in value["attempted"]
                    )
                    or (
                        value["healthy_since"] is not None
                        and type(value["healthy_since"]) not in {int, float}
                    )
                    or not isinstance(value["failed_observations"], dict)
                    or any(
                        type(count) is not int or count < 0
                        for count in value["failed_observations"].values()
                    )
                ):
                    return
                now = time.time()
                if healthy:
                    since = value["healthy_since"]
                    if since is None or now < since:
                        value["healthy_since"] = now
                    elif now - since >= 60:
                        value["attempted"] = []
                else:
                    value["healthy_since"] = None
                for unit in ("strongswan.service", "frr.service"):
                    observed = ops.bounded(
                        ["systemctl", "show", unit, "--property=ActiveState", "--value"], timeout=5
                    )
                    if observed.returncode:
                        continue
                    state = observed.stdout.strip()
                    missing = False
                    if unit == "strongswan.service" and state in {"active", "failed"}:
                        try:
                            cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
                            if not isinstance(cfg, dict) or _vm_ha_observer_only(cfg):
                                continue
                            expected = authorized_configuration(cfg)
                            if state == "active" and expected.connections:
                                listed = ops.bounded(
                                    [*observation_command(), "--configuration"], timeout=3
                                )
                                if listed.returncode:
                                    continue
                                missing = not ConfigurationInventory.from_json(
                                    listed.stdout
                                ).includes(expected)
                        except (OSError, ValueError, RuntimeError, yaml.YAMLError):
                            continue
                    if state in {"active", "inactive"} and not missing:
                        value["failed_observations"].pop(unit, None)
                    if state != "failed" and not missing:
                        continue
                    value["healthy_since"] = None
                    if unit in value["attempted"]:
                        continue
                    count = value["failed_observations"].get(unit, 0) + 1
                    value["failed_observations"][unit] = count
                    if count < self.max_failures_before_restart:
                        continue
                    # Recheck under the same writer lock immediately before consuming
                    # the durable attempt. Neither a timeout nor changed policy is failure.
                    current = ops.bounded(
                        ["systemctl", "show", unit, "--property=ActiveState", "--value"], timeout=5
                    )
                    if current.returncode or current.stdout.strip() != state:
                        continue
                    if unit == "strongswan.service":
                        if authorized_configuration(cfg) != expected:
                            continue
                        if missing:
                            listed = ops.bounded(
                                [*observation_command(), "--configuration"], timeout=3
                            )
                            if listed.returncode or ConfigurationInventory.from_json(
                                listed.stdout
                            ).includes(expected):
                                continue
                    value["attempted"].append(unit)
                    atomic_write_json(path, value)
                    if missing:
                        ops.bounded(["swanctl", "--load-all", "--noprompt"], timeout=15)
                    else:
                        result = ops.bounded(["systemctl", "restart", unit], timeout=20)
                        if result.returncode == 0 and unit == "strongswan.service":
                            ops.bounded(["swanctl", "--load-all", "--noprompt"], timeout=15)
                atomic_write_json(path, value)
        except (OSError, ValueError, TypeError, RuntimeError, subprocess.TimeoutExpired):
            # Unknown process completion is never permission to resubmit.
            return

    def run_health_check(self) -> dict[str, TunnelHealth]:
        """Observe all paths before admitting repair or scheduled maintenance."""
        tunnels = self.get_configured_tunnels()
        results = {}
        for name, interface, peer in tunnels:
            health = self.check_tunnel_health(name, interface, peer)
            previous = self.tunnel_states.get(name)
            if previous and previous.xfrm_present and health.xfrm_present:
                if any(
                    getattr(health.stats, field) > getattr(previous.stats, field)
                    for field in ("tx_dropped", "tx_errors", "rx_errors")
                ):
                    health.assessment = "unhealthy"
                    health.failure_reasons.append("XFRM errors increased")
            results[name] = health
            if health.is_healthy:
                self.consecutive_failures.pop(name, None)
            elif health.assessment == "unhealthy":
                self.consecutive_failures[name] = self.consecutive_failures.get(name, 0) + 1
        self.tunnel_states = results
        complete_health = bool(results) and all(item.is_healthy for item in results.values())
        self._repair_failed_components(complete_health)
        # A healthy individual path is insufficient while another path is in
        # native recovery. Serialize one scheduled refresh after complete health.
        if complete_health and self.enable_proactive_refresh and not self.observer_only:
            now = time.time()
            for name, _interface, _peer in tunnels:
                last = self.last_proactive_refresh.setdefault(name, now)
                if now - last >= self.proactive_refresh_interval:
                    # A timeout cannot prove a native mutation was cancelled.
                    # Reserve the maintenance interval before submitting once.
                    self.last_proactive_refresh[name] = now
                    self.restart_tunnel(name)
                    results[name] = self.check_tunnel_health(name, _interface, _peer)
                    break
        return results

    def get_health_summary(self) -> dict[str, Any]:
        """Get summary of tunnel health status.

        Returns:
            Dictionary with health summary
        """
        total_tunnels = len(self.tunnel_states)
        healthy_tunnels = sum(1 for h in self.tunnel_states.values() if h.is_healthy)
        unhealthy_tunnels = sum(h.assessment == "unhealthy" for h in self.tunnel_states.values())
        unknown_tunnels = total_tunnels - healthy_tunnels - unhealthy_tunnels

        return {
            "total_tunnels": total_tunnels,
            "healthy_tunnels": healthy_tunnels,
            "unhealthy_tunnels": unhealthy_tunnels,
            "unknown_tunnels": unknown_tunnels,
            "overall_health": "degraded"
            if unhealthy_tunnels
            else "unknown"
            if unknown_tunnels
            else "healthy",
            "tunnels": {
                name: {
                    "interface": health.interface,
                    "ipsec_status": health.ipsec_status,
                    "bgp_status": health.bgp_status,
                    "is_healthy": health.is_healthy,
                    "assessment": health.assessment,
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
        "Use gateway status to find configured tunnel names. This is useful for quick "
        "recovery from stale tunnel state without waiting for automatic detection.",
    )
    parser.add_argument(
        "--config",
        type=str,
        metavar="CONFIG_FILE",
        help="Load settings from YAML config file (reads defaults.health_monitoring section)",
    )

    args = parser.parse_args()

    try:
        installed_observer_only = _installed_observer_only()
    except ValueError as error:
        parser.exit(1, f"{error}\n")

    # Handle manual tunnel restart
    if args.restart_tunnel:
        monitor_temp = TunnelHealthMonitor(
            check_interval_seconds=10, observer_only=installed_observer_only
        )

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
    config_observer_only = installed_observer_only

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
                config_observer_only = installed_observer_only or _vm_ha_observer_only(config_data)

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
                    if config_observer_only:
                        print("[TunnelMonitor]    recovery_owner: vm-ha-controller")
                    if config_proactive:
                        print(f"[TunnelMonitor]    refresh_hours: {config_refresh_hours}h")
        except Exception as e:
            print(f"[TunnelMonitor] ⚠️  Failed to load config: {e}")
            parser.exit(1, "[TunnelMonitor] Invalid configuration; tunnel repair is blocked\n")

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
        observer_only=config_observer_only,
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
            print(f"Unknown: {summary['unknown_tunnels']}")
            print(f"Overall Health: {summary['overall_health'].upper()}")
            print(f"{'=' * 60}\n")

            for name, health in results.items():
                status_icon = {"healthy": "✓", "unhealthy": "✗", "unknown": "?"}[health.assessment]
                print(f"{status_icon} {name} ({health.interface}) [{health.assessment}]")
                print(f"  IPsec: {health.ipsec_status}")
                if health.bgp_peer:
                    print(f"  BGP: {health.bgp_status or 'UNKNOWN'} (peer: {health.bgp_peer})")
                if health.xfrm_present:
                    print(
                        f"  RX: {health.stats.rx_packets} packets / {health.stats.rx_bytes} bytes"
                    )
                    print(
                        f"  TX: {health.stats.tx_packets} packets / {health.stats.tx_bytes} bytes"
                    )
                    if health.stats.tx_dropped > 0:
                        print(f"  ⚠ TX DROPPED: {health.stats.tx_dropped} packets")
                else:
                    print("  RX/TX: unavailable")
                if health.failure_reasons:
                    print(f"  Details: {', '.join(health.failure_reasons)}")
                print()
    else:
        # Continuous monitoring
        print(
            f"[TunnelMonitor] Starting continuous monitoring (interval: {config_check_interval}s)"
        )
        print(
            "[TunnelMonitor] Native strongSwan recovery owns tunnel retries; confirmed component failures receive bounded repair"
        )

        if monitor.enable_proactive_refresh:
            print(
                f"[TunnelMonitor] Proactive refresh ENABLED: "
                f"tunnels will be restarted every {config_refresh_hours}h"
            )
        else:
            print("[TunnelMonitor] Proactive refresh DISABLED (native recovery enabled)")

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
