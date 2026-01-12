from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

BGPD_CONF = Path("/etc/frr/bgpd.conf")
FRR_CONF = Path("/etc/frr/frr.conf")
DAEMONS_FILE = Path("/etc/frr/daemons")


class FRRRenderer:
    def _ensure_bgpd_enabled(self, enable_bfd: bool = False) -> bool:
        """Ensure bgpd daemon is enabled and listening on all interfaces."""
        if not DAEMONS_FILE.exists():
            print("[FRR] WARNING: /etc/frr/daemons not found; creating with bgpd=yes")
            DAEMONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            bfdd_value = "yes" if enable_bfd else "no"
            DAEMONS_FILE.write_text(
                f'bgpd=yes\nbgpd_options="   -A 0.0.0.0"\nbfdd={bfdd_value}\n',
                encoding="utf-8",
            )
            return True

        text = DAEMONS_FILE.read_text(encoding="utf-8").splitlines()
        bgpd_seen = False
        bgpd_options_seen = False
        bfdd_seen = False
        changed = False
        new_lines: list[str] = []
        for line in text:
            stripped = line.strip()
            if stripped.startswith("bgpd="):
                new_lines.append("bgpd=yes")
                bgpd_seen = True
                if line != "bgpd=yes":
                    changed = True
            elif stripped.startswith("bgpd_options="):
                # Change -A 127.0.0.1 to -A 0.0.0.0 to listen on all interfaces
                new_line = 'bgpd_options="   -A 0.0.0.0"'
                new_lines.append(new_line)
                bgpd_options_seen = True
                if line != new_line:
                    changed = True
            elif stripped.startswith("bfdd="):
                bfdd_value = "yes" if enable_bfd else "no"
                new_line = f"bfdd={bfdd_value}"
                new_lines.append(new_line)
                bfdd_seen = True
                if line != new_line:
                    changed = True
            else:
                new_lines.append(line)

        if not bgpd_seen:
            new_lines.append("bgpd=yes")
            changed = True
        if not bgpd_options_seen:
            new_lines.append('bgpd_options="   -A 0.0.0.0"')
            changed = True
        if not bfdd_seen:
            bfdd_value = "yes" if enable_bfd else "no"
            new_lines.append(f"bfdd={bfdd_value}")
            changed = True

        if changed:
            DAEMONS_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            print("[FRR] Configured bgpd to listen on all interfaces in /etc/frr/daemons")
            return True
        return False

    def ensure_local_prefix_routes(self, cfg: dict[str, Any]) -> None:
        """Ensure static routes exist for local_prefixes so BGP can advertise them.

        BGP requires routes to exist in the kernel routing table before advertising them.
        This is controlled by FRR's 'import-check' feature (enabled by default).
        Without a kernel route, BGP will mark the prefix as 'inaccessible' and not advertise it.

        CRITICAL: Adding routes for local_prefixes that represent the source subnets
        (workload VMs) breaks packet forwarding! The gateway receives packets FROM these
        subnets and must forward them to the VPN tunnel, not route them back.
        
        Solution: Disable import-check in BGP configuration and skip adding these routes.
        This allows BGP to advertise local_prefixes without breaking packet forwarding.

        Args:
            cfg: Gateway configuration dictionary
        """
        # DO NOT add routes for local_prefixes as they break packet forwarding
        # from the workload subnets. BGP will be configured with 'no bgp network import-check'
        # to allow advertising these prefixes without requiring kernel routes.
        print("[FRR] Skipping local_prefix routes (would break packet forwarding from workload VMs)")
        return

    def render_and_apply(self, cfg: dict[str, Any]) -> None:
        """Render FRR bgpd.conf for BGP tunnels and prefix advertisement.

        When routing_mode is bgp, configures neighbors for each active tunnel with APIPA link IPs.
        Advertises gateway.local_prefixes to neighbors where connection.bgp.advertise_local_prefixes=true.
        Supports hold/keepalive and graceful-restart defaults.
        """
        d_bgp = cfg.get("defaults", {}).get("routing", {}).get("bgp", {})
        bfd_cfg = d_bgp.get("bfd", {}) or {}
        bfd_enabled = bool(bfd_cfg.get("enabled", False))
        bfd_tx = int(bfd_cfg.get("transmit_interval_ms", 300))
        bfd_rx = int(bfd_cfg.get("receive_interval_ms", 300))
        bfd_multiplier = int(bfd_cfg.get("detect_multiplier", 3))

        daemons_changed = self._ensure_bgpd_enabled(enable_bfd=bfd_enabled)
        BGPD_CONF.parent.mkdir(parents=True, exist_ok=True)

        gateway = cfg.get("gateway", {})
        local_asn = gateway.get("local_asn", 65010)
        # Gateway-level local_prefixes: single source of truth for Nebius-side subnets
        gateway_local_prefixes: list[str] = gateway.get("local_prefixes", [])

        # Ensure kernel routes exist for local_prefixes so BGP can advertise them
        if gateway_local_prefixes:
            self.ensure_local_prefix_routes(cfg)

        hold = d_bgp.get("hold_time_seconds", 60)
        keep = d_bgp.get("keepalive_seconds", 20)
        router_id = d_bgp.get("router_id")
        graceful = d_bgp.get("graceful_restart", True)
        max_prefixes_default = d_bgp.get("max_prefixes", 1000)

        lines = [
            "! generated by nebius-vpngw-agent",
        ]

        # Track prefix-list filters for inbound BGP routes (optional whitelist)
        prefix_list_filters: dict[str, list[str]] = {}  # neighbor_ip -> list of allowed prefixes

        # Track which local prefixes should be advertised outbound
        # This prevents re-advertising routes learned from peers back to them
        outbound_prefix_list_needed = len(gateway_local_prefixes) > 0

        # Track which tunnels use which local-pref (for route-map generation)
        tunnels_by_localpref: dict[int, list[str]] = {
            200: [],  # Active tunnels
            100: [],  # Passive tunnels
        }
        bfd_peers: set[str] = set()

        # First pass: collect tunnel info for route-maps and prefix-list filters
        for conn in cfg.get("connections", []):
            routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get(
                "routing", {}
            ).get("mode", "bgp")
            if routing_mode != "bgp":
                continue
            conn_bgp = conn.get("bgp", {}) or {}
            conn_remote_prefixes = (
                conn.get("remote_prefixes", []) or conn_bgp.get("remote_prefixes", []) or []
            )

            for tun in conn.get("tunnels", []):
                ha_role = tun.get("ha_role", "active")
                if ha_role == "disable":
                    continue

                tbgp = tun.get("bgp", {}) or {}
                remote_ip = tbgp.get("remote_ip") or tun.get("inner_remote_ip")

                if remote_ip:
                    # Track tunnel for local-pref route-map
                    local_pref = 200 if ha_role == "active" else 100
                    tunnels_by_localpref[local_pref].append(remote_ip)

                    # Track prefix-list filters
                    if conn_remote_prefixes:
                        prefix_list_filters[remote_ip] = conn_remote_prefixes
                    if bfd_enabled:
                        bfd_peers.add(remote_ip)

        if bfd_enabled and bfd_peers:
            lines.append("!")
            lines.append("! BFD peers for fast failure detection (if supported by peer)")
            lines.append("bfd")
            for peer_ip in sorted(bfd_peers):
                lines.append(f" peer {peer_ip}")
                lines.append(f"  transmit-interval {bfd_tx}")
                lines.append(f"  receive-interval {bfd_rx}")
                lines.append(f"  detect-multiplier {bfd_multiplier}")
                lines.append(" !")
            lines.append("!")

        # Define route-maps for Active/Passive HA (before prefix-lists)
        # Active tunnels:
        #   - local-preference 200 (inbound, LOCAL only) = preferred for outbound traffic
        #   - MED 0 (outbound, TRANSMITTED to peer) = peer prefers this tunnel
        # Passive tunnels:
        #   - local-preference 100 (inbound, LOCAL only) = standby for outbound
        #   - MED 100 (outbound, TRANSMITTED to peer) = peer deprioritizes this tunnel
        # This prevents ECMP on both sides and ensures symmetric routing
        if tunnels_by_localpref[200]:
            lines.append("!")
            lines.append("! Active/Passive HA: Active tunnel route-map")
            lines.append("! Inbound: Set local-pref 200 (prefer for outbound on THIS router)")
            lines.append("route-map SET-LOCAL-PREF-200 permit 10")
            lines.append(" set local-preference 200")
            lines.append("!")

        if tunnels_by_localpref[100]:
            lines.append("!")
            lines.append("! Active/Passive HA: Passive tunnel route-map")
            lines.append("! Inbound: Set local-pref 100 (deprioritize for outbound on THIS router)")
            lines.append("route-map SET-LOCAL-PREF-100 permit 10")
            lines.append(" set local-preference 100")
            lines.append("!")

        # Outbound route-maps: combine prefix-list filtering with MED setting
        # Active tunnel: only advertise local prefixes, set MED=0
        if outbound_prefix_list_needed and tunnels_by_localpref[200]:
            lines.append("! Outbound for active tunnel: filter prefixes + set MED 0")
            lines.append("route-map ADVERTISE-ACTIVE permit 10")
            lines.append(" match ip address prefix-list ADVERTISE-LOCAL")
            lines.append(" set metric 0")
            lines.append("!")

        # Passive tunnel: only advertise local prefixes, set MED=100
        if outbound_prefix_list_needed and tunnels_by_localpref[100]:
            lines.append("! Outbound for passive tunnel: filter prefixes + set MED 100")
            lines.append("route-map ADVERTISE-PASSIVE permit 10")
            lines.append(" match ip address prefix-list ADVERTISE-LOCAL")
            lines.append(" set metric 100")
            lines.append("!")

        # Define prefix-lists before router bgp section
        # Inbound filters (optional - only if remote_prefixes specified)
        for neighbor_ip, allowed_prefixes in prefix_list_filters.items():
            list_name = f"ALLOW-FROM-{neighbor_ip.replace('.', '-')}"
            seq = 10
            for prefix in allowed_prefixes:
                lines.append(f"ip prefix-list {list_name} seq {seq} permit {prefix}")
                seq += 10
            lines.append("!")

        # Outbound filter (mandatory - only advertise local prefixes, never re-advertise learned routes)
        if outbound_prefix_list_needed:
            lines.append("!")
            lines.append(
                "! Outbound filter: only advertise local prefixes (gateway.local_prefixes)"
            )
            lines.append("! This prevents re-advertising routes learned from peers")
            seq = 10
            for prefix in sorted(gateway_local_prefixes):
                lines.append(f"ip prefix-list ADVERTISE-LOCAL seq {seq} permit {prefix}")
                seq += 10
            lines.append("!")

        # Start router bgp configuration
        lines.append(f"router bgp {local_asn}")
        lines.append(f" timers bgp {keep} {hold}")
        if router_id:
            lines.append(f" bgp router-id {router_id}")
        if graceful:
            lines.append(" bgp graceful-restart")
        # Disable policy requirement for eBGP (FRR 8.4+)
        lines.append(" no bgp ebgp-requires-policy")
        # Disable network import-check to allow advertising local_prefixes without kernel routes
        # This is critical for VPN gateways where local_prefixes are source subnets
        lines.append(" no bgp network import-check")

        # Track which prefixes to advertise (can vary per connection)
        advertised_prefixes: set[str] = set()

        # Neighbors per tunnel (both active and passive for Active/Passive HA)
        tunnel_index = 0  # Track tunnel interface index for logging
        for conn in cfg.get("connections", []):
            routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get(
                "routing", {}
            ).get("mode", "bgp")
            if routing_mode != "bgp":
                continue

            # Check if this connection should advertise local prefixes
            conn_bgp = conn.get("bgp", {}) or {}
            advertise = conn_bgp.get("advertise_local_prefixes", True)  # Default: true
            remote_asn = conn_bgp.get("remote_asn")

            for tun in conn.get("tunnels", []):
                ha_role = tun.get("ha_role", "active")
                if ha_role == "disable":
                    continue  # Skip disabled tunnels entirely

                tbgp = tun.get("bgp", {}) or {}
                local_ip = tbgp.get("local_ip") or tun.get("inner_local_ip")
                remote_ip = tbgp.get("remote_ip") or tun.get("inner_remote_ip")
                rasn = tbgp.get("remote_asn") or remote_asn
                if not (remote_ip and rasn):
                    continue

                lines.append(f" neighbor {remote_ip} remote-as {rasn}")
                lines.append(f" neighbor {remote_ip} timers {keep} {hold}")
                lines.append(f" neighbor {remote_ip} maximum-prefix {max_prefixes_default}")
                if bfd_enabled:
                    lines.append(f" neighbor {remote_ip} bfd")

                # CRITICAL: Configure update-source to use tunnel interface IP
                # This ensures BGP packets use the correct source IP (APIPA inner IP)
                # instead of the primary interface IP (10.x.x.x)
                # Works with XFRM interfaces (xfrm0, xfrm1, ...)
                if local_ip:
                    lines.append(f" neighbor {remote_ip} update-source {local_ip}")
                    print(
                        f"[FRR] Configured neighbor {remote_ip} with update-source {local_ip} (ha_role={ha_role})"
                    )

                tunnel_index += 1

                # Track prefixes to advertise for this connection
                if advertise:
                    advertised_prefixes.update(gateway_local_prefixes)

        # Advertise accumulated prefixes and activate neighbors
        lines.append(" !")
        lines.append(" address-family ipv4 unicast")
        for pfx in sorted(advertised_prefixes):
            lines.append(f"  network {pfx}")

        # Apply Active/Passive HA via BGP local-preference (inbound) and MED (outbound)
        # Inbound: local-preference controls THIS router's path preference
        #   - Active: 200 (higher = preferred for outbound traffic)
        #   - Passive: 100 (lower = standby)
        # Outbound: MED tells PEER which path to prefer
        #   - Active: MED 0 (lower = preferred by peer)
        #   - Passive: MED 100 (higher = deprioritized by peer)
        # This ensures symmetric routing on BOTH sides without ECMP
        for conn in cfg.get("connections", []):
            routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get(
                "routing", {}
            ).get("mode", "bgp")
            if routing_mode != "bgp":
                continue
            for tun in conn.get("tunnels", []):
                ha_role = tun.get("ha_role", "active")
                if ha_role == "disable":
                    continue
                tbgp = tun.get("bgp", {}) or {}
                remote_ip = tbgp.get("remote_ip") or tun.get("inner_remote_ip")
                if remote_ip:
                    # Inbound: Set local-preference for THIS router's path preference
                    local_pref = 200 if ha_role == "active" else 100
                    lines.append(f"  neighbor {remote_ip} route-map SET-LOCAL-PREF-{local_pref} in")
                    
                    # Outbound: Apply combined route-map (prefix filter + MED)
                    if outbound_prefix_list_needed:
                        route_map_name = "ADVERTISE-ACTIVE" if ha_role == "active" else "ADVERTISE-PASSIVE"
                        lines.append(f"  neighbor {remote_ip} route-map {route_map_name} out")

        # Apply inbound prefix-list filters to neighbors (if configured)
        for neighbor_ip in prefix_list_filters:
            list_name = f"ALLOW-FROM-{neighbor_ip.replace('.', '-')}"
            lines.append(f"  neighbor {neighbor_ip} prefix-list {list_name} in")

        # NOTE: Outbound filtering is now combined with MED setting in ADVERTISE-ACTIVE/PASSIVE route-maps above
        # No separate prefix-list application needed here

        # Set next-hop-self for all eBGP neighbors
        # This explicitly sets the next-hop to the update-source IP (XFRM interface IP)
        # While FRR does this automatically for eBGP, making it explicit improves clarity
        for conn in cfg.get("connections", []):
            routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get(
                "routing", {}
            ).get("mode", "bgp")
            if routing_mode != "bgp":
                continue
            for tun in conn.get("tunnels", []):
                ha_role = tun.get("ha_role", "active")
                if ha_role == "disable":
                    continue
                tbgp = tun.get("bgp", {}) or {}
                remote_ip = tbgp.get("remote_ip") or tun.get("inner_remote_ip")
                if remote_ip:
                    lines.append(f"  neighbor {remote_ip} next-hop-self")

        # Activate all neighbors in address-family
        for conn in cfg.get("connections", []):
            routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get(
                "routing", {}
            ).get("mode", "bgp")
            if routing_mode != "bgp":
                continue
            for tun in conn.get("tunnels", []):
                ha_role = tun.get("ha_role", "active")
                if ha_role == "disable":
                    continue
                tbgp = tun.get("bgp", {}) or {}
                remote_ip = tbgp.get("remote_ip") or tun.get("inner_remote_ip")
                if remote_ip:
                    lines.append(f"  neighbor {remote_ip} activate")

        lines.append(" exit-address-family")

        rendered = "\n".join(lines) + "\n"
        # FRR 8+ uses integrated config in frr.conf
        FRR_CONF.write_text(rendered, encoding="utf-8")
        print(f"[FRR] Wrote bgp config with {len(advertised_prefixes)} advertised prefix(es)")
        # Reload bgpd to apply config (soft reload if only bgp changed; restart if daemons changed)
        cmd = ["systemctl", "restart" if daemons_changed else "reload", "frr"]
        try:
            subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
            )
        except Exception:
            pass
