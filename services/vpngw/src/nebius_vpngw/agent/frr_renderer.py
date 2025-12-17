from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, Any, List

BGPD_CONF = Path("/etc/frr/bgpd.conf")
FRR_CONF = Path("/etc/frr/frr.conf")
DAEMONS_FILE = Path("/etc/frr/daemons")


class FRRRenderer:
    def _ensure_bgpd_enabled(self) -> bool:
        """Ensure bgpd daemon is enabled and listening on all interfaces."""
        if not DAEMONS_FILE.exists():
            print("[FRR] WARNING: /etc/frr/daemons not found; creating with bgpd=yes")
            DAEMONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            DAEMONS_FILE.write_text(
                'bgpd=yes\nbgpd_options="   -A 0.0.0.0"\n', encoding="utf-8"
            )
            return True

        text = DAEMONS_FILE.read_text(encoding="utf-8").splitlines()
        bgpd_seen = False
        bgpd_options_seen = False
        changed = False
        new_lines: List[str] = []
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
            else:
                new_lines.append(line)

        if not bgpd_seen:
            new_lines.append("bgpd=yes")
            changed = True
        if not bgpd_options_seen:
            new_lines.append('bgpd_options="   -A 0.0.0.0"')
            changed = True

        if changed:
            DAEMONS_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            print(
                "[FRR] Configured bgpd to listen on all interfaces in /etc/frr/daemons"
            )
            return True
        return False

    def _ensure_local_prefix_routes(
        self, local_prefixes: List[str], interface: str = "eth0"
    ) -> None:
        """Ensure static routes exist for local_prefixes so BGP can advertise them.

        BGP requires routes to exist in the kernel routing table before advertising them.
        This is controlled by FRR's 'import-check' feature (enabled by default).
        Without a kernel route, BGP will mark the prefix as 'inaccessible' and not advertise it.

        CRITICAL: We do NOT use 'scope link' as that marks the prefix as directly connected,
        which prevents proper forwarding. Instead, we route via the VPC default gateway.

        Args:
            local_prefixes: List of CIDR prefixes to add routes for
            interface: Interface to use for the static route (default: eth0)
        """
        # Get the default gateway IP for proper routing
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and "via" in result.stdout:
                # Extract gateway IP (e.g., "default via 169.254.169.1 dev eth0" -> "169.254.169.1")
                gateway_ip = result.stdout.split("via")[1].split()[0]
            else:
                print(
                    "[FRR] WARNING: Could not determine default gateway, skipping local prefix routes"
                )
                return
        except Exception as e:
            print(f"[FRR] WARNING: Error getting default gateway: {e}")
            return

        for prefix in local_prefixes:
            # Check if route already exists
            result = subprocess.run(
                ["ip", "route", "show", prefix],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                print(
                    f"[FRR] Route for {prefix} already exists: {result.stdout.strip()}"
                )
                continue

            # Add static route via gateway (NOT scope link)
            # This allows BGP to advertise the prefix while enabling proper packet forwarding
            try:
                subprocess.run(
                    ["ip", "route", "add", prefix, "via", gateway_ip, "dev", interface],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                print(
                    f"[FRR] Added static route: {prefix} via {gateway_ip} dev {interface}"
                )
            except subprocess.CalledProcessError as e:
                print(f"[FRR] WARNING: Failed to add route for {prefix}: {e.stderr}")

    def render_and_apply(self, cfg: Dict[str, Any]) -> None:
        """Render FRR bgpd.conf for BGP tunnels and prefix advertisement.

        When routing_mode is bgp, configures neighbors for each active tunnel with APIPA link IPs.
        Advertises gateway.local_prefixes to neighbors where connection.bgp.advertise_local_prefixes=true.
        Supports hold/keepalive and graceful-restart defaults.
        """
        daemons_changed = self._ensure_bgpd_enabled()
        BGPD_CONF.parent.mkdir(parents=True, exist_ok=True)

        gateway = cfg.get("gateway", {})
        local_asn = gateway.get("local_asn", 65010)
        # Gateway-level local_prefixes: single source of truth for Nebius-side subnets
        gateway_local_prefixes: List[str] = gateway.get("local_prefixes", [])

        # Ensure kernel routes exist for local_prefixes so BGP can advertise them
        if gateway_local_prefixes:
            self._ensure_local_prefix_routes(gateway_local_prefixes)

        d_bgp = cfg.get("defaults", {}).get("routing", {}).get("bgp", {})
        hold = d_bgp.get("hold_time_seconds", 60)
        keep = d_bgp.get("keepalive_seconds", 20)
        router_id = d_bgp.get("router_id")
        graceful = d_bgp.get("graceful_restart", True)
        max_prefixes_default = d_bgp.get("max_prefixes", 1000)

        lines = [
            "! generated by nebius-vpngw-agent",
        ]

        # Track prefix-list filters for inbound BGP routes (optional whitelist)
        prefix_list_filters: Dict[
            str, List[str]
        ] = {}  # neighbor_ip -> list of allowed prefixes

        # Track which local prefixes should be advertised outbound
        # This prevents re-advertising routes learned from peers back to them
        outbound_prefix_list_needed = len(gateway_local_prefixes) > 0

        # First pass: collect prefix-list filters
        for conn in cfg.get("connections", []):
            routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get(
                "routing", {}
            ).get("mode", "bgp")
            if routing_mode != "bgp":
                continue
            conn_bgp = conn.get("bgp", {}) or {}
            conn_remote_prefixes = (
                conn.get("remote_prefixes", [])
                or conn_bgp.get("remote_prefixes", [])
                or []
            )

            for tun in conn.get("tunnels", []):
                if tun.get("ha_role", "active") != "active":
                    continue
                tbgp = tun.get("bgp", {}) or {}
                remote_ip = tbgp.get("remote_ip") or tun.get("inner_remote_ip")
                if remote_ip and conn_remote_prefixes:
                    prefix_list_filters[remote_ip] = conn_remote_prefixes

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
                lines.append(
                    f"ip prefix-list ADVERTISE-LOCAL seq {seq} permit {prefix}"
                )
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

        # Track which prefixes to advertise (can vary per connection)
        advertised_prefixes: set[str] = set()

        # Neighbors per active tunnel
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
                if tun.get("ha_role", "active") != "active":
                    continue
                tbgp = tun.get("bgp", {}) or {}
                local_ip = tbgp.get("local_ip") or tun.get("inner_local_ip")
                remote_ip = tbgp.get("remote_ip") or tun.get("inner_remote_ip")
                rasn = tbgp.get("remote_asn") or remote_asn
                if not (remote_ip and rasn):
                    continue

                lines.append(f" neighbor {remote_ip} remote-as {rasn}")
                lines.append(f" neighbor {remote_ip} timers {keep} {hold}")
                lines.append(
                    f" neighbor {remote_ip} maximum-prefix {max_prefixes_default}"
                )

                # CRITICAL: Configure update-source to use tunnel interface IP
                # This ensures BGP packets use the correct source IP (APIPA inner IP)
                # instead of the primary interface IP (10.x.x.x)
                # Works with XFRM interfaces (xfrm0, xfrm1, ...)
                if local_ip:
                    lines.append(f" neighbor {remote_ip} update-source {local_ip}")
                    print(
                        f"[FRR] Configured neighbor {remote_ip} with update-source {local_ip}"
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

        # Apply inbound prefix-list filters to neighbors (if configured)
        for neighbor_ip in prefix_list_filters.keys():
            list_name = f"ALLOW-FROM-{neighbor_ip.replace('.', '-')}"
            lines.append(f"  neighbor {neighbor_ip} prefix-list {list_name} in")

        # Apply outbound prefix-list filter to ALL neighbors
        # This is CRITICAL to prevent route reflection (advertising learned routes back to peers)
        if outbound_prefix_list_needed:
            for conn in cfg.get("connections", []):
                routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get(
                    "routing", {}
                ).get("mode", "bgp")
                if routing_mode != "bgp":
                    continue
                for tun in conn.get("tunnels", []):
                    if tun.get("ha_role", "active") != "active":
                        continue
                    tbgp = tun.get("bgp", {}) or {}
                    remote_ip = tbgp.get("remote_ip") or tun.get("inner_remote_ip")
                    if remote_ip:
                        lines.append(
                            f"  neighbor {remote_ip} prefix-list ADVERTISE-LOCAL out"
                        )

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
                if tun.get("ha_role", "active") != "active":
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
                if tun.get("ha_role", "active") != "active":
                    continue
                tbgp = tun.get("bgp", {}) or {}
                remote_ip = tbgp.get("remote_ip") or tun.get("inner_remote_ip")
                if remote_ip:
                    lines.append(f"  neighbor {remote_ip} activate")

        lines.append(" exit-address-family")

        rendered = "\n".join(lines) + "\n"
        # FRR 8+ uses integrated config in frr.conf
        FRR_CONF.write_text(rendered, encoding="utf-8")
        print(
            f"[FRR] Wrote bgp config with {len(advertised_prefixes)} advertised prefix(es)"
        )
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
