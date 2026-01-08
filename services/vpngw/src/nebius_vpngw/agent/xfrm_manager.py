"""XFRM interface management for xfrm-interface IPsec mode.

This module creates and manages xfrm netdevices that bind to strongSwan CHILD_SAs
via if_id. Unlike VTI mode (which uses marks and updown scripts), XFRM interfaces
are created manually before strongSwan starts and bound via if_id_in/if_id_out.

Key advantages over VTI:
- No packet duplication with 0.0.0.0/0 traffic selectors
- Cleaner architecture (no marks, no updown scripts)
- Modern Linux kernel interface (5.4+)
- Better performance and maintainability
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path
from typing import Any

IPSEC_OVERHEAD_BYTES = 64  # NAT-T ESP overhead for IPv4 (bytes)


class XFRMManager:
    """Manages XFRM network devices for IPsec tunnels."""

    def setup_interfaces(
        self, interface_endpoints: list[dict[str, Any]], parent_dev: str = "eth0"
    ) -> None:
        """Create XFRM interfaces and configure them with IPs and routes.

        Args:
            interface_endpoints: List of interface configs from StrongSwanRenderer
            parent_dev: Parent network device for XFRM interfaces (default: eth0)
        """
        # Configure rp_filter for XFRM (relax reverse path filtering)
        self._configure_sysctl(parent_dev)

        mtu_info = self._calculate_xfrm_mtu(parent_dev)
        xfrm_mtu = None
        if mtu_info is not None:
            xfrm_mtu, parent_mtu = mtu_info
            print(
                f"[XFRM] Using xfrm MTU {xfrm_mtu} (parent {parent_dev} MTU {parent_mtu} - {IPSEC_OVERHEAD_BYTES})"
            )

        for iface in interface_endpoints:
            name = iface["name"]
            if_id = iface["if_id"]
            local_inner_ip = iface.get("local_inner_ip")
            remote_inner_ip = iface.get("remote_inner_ip")
            cidr = iface.get("cidr")
            mode = iface.get("mode", "bgp")
            remote_prefixes = iface.get("remote_prefixes", [])

            print(f"[XFRM] Creating interface {name} with if_id={if_id}")

            # Create XFRM device bound to if_id
            self._create_xfrm_device(name, parent_dev, if_id)

            # Set MTU for XFRM devices to avoid PMTU confusion on the tunnel
            if xfrm_mtu is not None:
                self._set_mtu(name, xfrm_mtu)

            # Assign IP address if provided (required for BGP)
            if local_inner_ip and cidr:
                self._assign_ip(name, local_inner_ip, cidr)

            # Bring interface up
            self._bring_up(name)

            # Configure routing based on mode
            if mode == "bgp":
                # BGP mode: Add host route to BGP peer
                # BGP daemon (FRR) will learn and install remote prefixes dynamically
                if remote_inner_ip:
                    self._add_peer_route(name, remote_inner_ip)
            else:
                # Static mode: Install kernel routes for remote prefixes
                if remote_prefixes:
                    self._add_static_routes(name, remote_prefixes)
                else:
                    print(f"[XFRM] WARNING: Static mode interface {name} has no remote_prefixes")

    def cleanup_interfaces(self, interface_names: list[str]) -> None:
        """Remove XFRM interfaces.

        Args:
            interface_names: List of interface names to delete
        """
        for name in interface_names:
            print(f"[XFRM] Deleting interface {name}")
            subprocess.run(["ip", "link", "del", name], capture_output=True, check=False)

    def _create_xfrm_device(self, name: str, parent_dev: str, if_id: int) -> None:
        """Create XFRM network device bound to if_id."""
        result = subprocess.run(
            [
                "ip",
                "link",
                "add",
                name,
                "type",
                "xfrm",
                "dev",
                parent_dev,
                "if_id",
                str(if_id),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            if "File exists" in result.stderr:
                print(f"[XFRM] Interface {name} already exists, reusing")
            else:
                print(f"[XFRM] ERROR creating {name}: {result.stderr}")
                raise RuntimeError(f"Failed to create XFRM interface {name}")
        else:
            print(f"[XFRM] ✓ Created interface {name}")

    def _assign_ip(self, name: str, local_ip: str, cidr: str) -> None:
        """Assign IP address to XFRM interface."""
        try:
            # Extract prefix length from CIDR
            net = ipaddress.ip_network(cidr, strict=False)
            prefix = net.prefixlen
            addr_with_prefix = f"{local_ip}/{prefix}"

            result = subprocess.run(
                ["ip", "addr", "replace", addr_with_prefix, "dev", name],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"[XFRM] ERROR assigning IP to {name}: {result.stderr}")
            else:
                print(f"[XFRM] ✓ Ensured {addr_with_prefix} on {name}")
        except Exception as e:
            print(f"[XFRM] ERROR: Failed to parse CIDR {cidr}: {e}")

    def _bring_up(self, name: str) -> None:
        """Bring XFRM interface up."""
        result = subprocess.run(["ip", "link", "set", name, "up"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[XFRM] ✓ Interface {name} is UP")
        else:
            print(f"[XFRM] ERROR bringing up {name}: {result.stderr}")

    def _set_mtu(self, name: str, mtu: int) -> None:
        """Set MTU on XFRM interface."""
        result = subprocess.run(
            ["ip", "link", "set", "dev", name, "mtu", str(mtu)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"[XFRM] ✓ Set {name} MTU to {mtu}")
        else:
            print(f"[XFRM] WARNING: Failed to set MTU on {name}: {result.stderr}")

    def _get_interface_mtu(self, name: str) -> int | None:
        """Read MTU for a given interface."""
        result = subprocess.run(
            ["ip", "-o", "link", "show", "dev", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"[XFRM] WARNING: Failed to read MTU for {name}: {result.stderr}")
            return None
        match = re.search(r"\bmtu (\d+)\b", result.stdout)
        if not match:
            print(f"[XFRM] WARNING: Could not parse MTU for {name}")
            return None
        return int(match.group(1))

    def _calculate_xfrm_mtu(self, parent_dev: str) -> tuple[int, int] | None:
        """Compute effective MTU for XFRM devices based on parent MTU."""
        parent_mtu = self._get_interface_mtu(parent_dev)
        if parent_mtu is None:
            return None
        effective_mtu = parent_mtu - IPSEC_OVERHEAD_BYTES
        if effective_mtu <= 0:
            print(
                f"[XFRM] WARNING: Parent MTU {parent_mtu} too small for overhead {IPSEC_OVERHEAD_BYTES}"
            )
            return None
        return effective_mtu, parent_mtu

    def _add_peer_route(self, name: str, remote_ip: str) -> None:
        """Add host route to BGP peer via XFRM interface."""
        result = subprocess.run(
            ["ip", "route", "replace", f"{remote_ip}/32", "dev", name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            print(f"[XFRM] ✓ Added peer route {remote_ip}/32 via {name}")
        else:
            print(f"[XFRM] WARNING: Failed to add peer route: {result.stderr}")

    def _add_static_routes(self, name: str, remote_prefixes: list[str]) -> None:
        """Add static routes for remote prefixes via XFRM interface."""
        for prefix in remote_prefixes:
            result = subprocess.run(
                ["ip", "route", "replace", prefix, "dev", name],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                print(f"[XFRM] ✓ Added static route {prefix} via {name}")
            else:
                print(f"[XFRM] WARNING: Failed to add route {prefix}: {result.stderr}")

    def _configure_sysctl(self, parent_dev: str) -> None:
        """Configure sysctl settings for XFRM interfaces.

        Relaxes reverse path filtering (rp_filter) to allow asymmetric routing
        that can occur with IPsec tunnels.
        """
        # Settings to apply (interface: value)
        settings = {
            parent_dev: "0",  # Relax rp_filter on parent (eth0)
            "all": "0",  # Global setting
            "default": "0",  # Default for new interfaces
        }

        for iface, value in settings.items():
            key = f"net.ipv4.conf.{iface}.rp_filter"
            result = subprocess.run(
                ["sysctl", "-w", f"{key}={value}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                print(f"[XFRM] ✓ Set {key}={value}")
            else:
                print(f"[XFRM] WARNING: Failed to set {key}: {result.stderr}")

        # Make sysctl settings persistent without clobbering other hardening
        sysctl_conf = Path("/etc/sysctl.d/99-vpn-gateway.conf")
        desired = {
            f"net.ipv4.conf.{parent_dev}.rp_filter": "0",
            "net.ipv4.conf.all.rp_filter": "0",
            "net.ipv4.conf.default.rp_filter": "0",
            "net.ipv4.ip_forward": "1",
            "net.ipv4.tcp_mtu_probing": "1",
        }
        existing_lines = []
        if sysctl_conf.exists():
            existing_lines = sysctl_conf.read_text().splitlines()
        new_lines = []
        seen_keys = set()
        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                new_lines.append(line)
                continue
            key, _, _ = stripped.partition("=")
            key = key.strip()
            if key in desired:
                new_lines.append(f"{key}={desired[key]}")
                seen_keys.add(key)
            else:
                new_lines.append(line)
        for key, value in desired.items():
            if key not in seen_keys:
                if new_lines and new_lines[-1].strip():
                    new_lines.append("")
                new_lines.append(f"{key}={value}")
        if not new_lines:
            new_lines.append("# generated by nebius-vpngw-agent")
            for key, value in desired.items():
                new_lines.append(f"{key}={value}")
        sysctl_conf.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
        print(f"[XFRM] ✓ Updated {sysctl_conf} (persistent sysctl settings)")
