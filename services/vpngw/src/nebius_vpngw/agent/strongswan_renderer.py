from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

IPSEC_CONF = Path("/etc/ipsec.conf")
IPSEC_SECRETS = Path("/etc/ipsec.secrets")
STRONGSWAN_CONF_DIR = Path("/etc/strongswan.d/charon")


def _write_secret_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f"{path.name}.",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            # codeql[py/clear-text-storage-sensitive-data] - strongSwan requires on-disk PSKs; file perms are locked to 0600.
            tmp.write(content)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


class StrongSwanRenderer:
    def _collect_tunnel_state(self, cfg: dict[str, Any]) -> tuple[list[str], list[str], list[dict]]:
        connections: list[str] = []
        secrets_lines: list[str] = []

        defaults = cfg.get("defaults", {})
        global_ike_version = defaults.get("ike_version", 2)
        allow_ikev1 = defaults.get("allow_ikev1", True)
        crypto_defaults = defaults.get("crypto", {})
        dpd = defaults.get("dpd", {})

        # Gateway-level configuration
        gateway = cfg.get("gateway", {})
        gateway_local_prefixes: list[str] = gateway.get("local_prefixes", [])
        # Base if_id for XFRM mode (100+ for tunnel identifiers)
        base_if_id = 100

        idx = 0
        interface_endpoints: list[dict] = []
        for conn in cfg.get("connections", []):
            routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get(
                "routing", {}
            ).get("mode", "bgp")
            for tun in conn.get("tunnels", []):
                ha_role = tun.get("ha_role", "active")
                if ha_role == "disable":
                    continue  # Skip only explicitly disabled tunnels
                tun_mode = tun.get("routing_mode") or routing_mode

                name = tun.get("name") or f"tunnel{idx}"
                ike_version = tun.get("ike_version")
                if ike_version is None:
                    ike_version = global_ike_version

                # Tunnel IPs and endpoints
                local_public_ip = tun.get("local_public_ip")  # Optional; auto-detected if omitted
                remote_public_ip = tun.get("remote_public_ip")  # Required for right=
                inner_local_ip = tun.get("inner_local_ip")
                inner_remote_ip = tun.get("inner_remote_ip")
                inner_cidr = tun.get("inner_cidr")
                psk = tun.get("psk")

                if not remote_public_ip:
                    print(f"[StrongSwan] WARNING: Tunnel {name} missing remote_public_ip; skipping")
                    continue

                # Crypto proposals
                ccrypto = tun.get("crypto", {}) or {}
                ike_props = ccrypto.get("ike_proposals") or crypto_defaults.get("ike_proposals", [])
                esp_props = ccrypto.get("esp_proposals") or crypto_defaults.get("esp_proposals", [])
                ike_life = ccrypto.get("ike_lifetime_seconds") or crypto_defaults.get(
                    "ike_lifetime_seconds", 28800
                )
                esp_life = ccrypto.get("esp_lifetime_seconds") or crypto_defaults.get(
                    "esp_lifetime_seconds", 3600
                )

                # Build connection stanza
                conn_lines = [f"conn {name}"]

                # IKE version
                if ike_version == 2:
                    conn_lines.append("    keyexchange=ikev2")
                elif ike_version == 1 and allow_ikev1:
                    conn_lines.append("    keyexchange=ikev1")
                else:
                    print(
                        f"[StrongSwan] WARNING: Unsupported IKE version {ike_version} for {name}; skipping"
                    )
                    continue

                # Local/Remote endpoints
                if local_public_ip:
                    # VM has internal IP, use %any for local and set leftid to external IP
                    conn_lines.append("    left=%any")
                    conn_lines.append(f"    leftid={local_public_ip}")
                else:
                    conn_lines.append("    left=%any")  # Auto-detect local IP
                conn_lines.append(f"    right={remote_public_ip}")

                # Authentication
                conn_lines.append("    authby=psk")

                # Tunnel mode and subnets
                conn_lines.append("    type=tunnel")

                # Traffic selectors: limit local side to inner CIDR + gateway.local_prefixes
                # to avoid capturing public traffic/SSH; allow any remote (routes decide what flows)
                local_ts: list[str] = []
                if inner_cidr:
                    local_ts.append(inner_cidr)
                local_ts.extend(gateway_local_prefixes)
                if local_ts:
                    conn_lines.append(f"    leftsubnet={','.join(local_ts)}")
                else:
                    conn_lines.append("    leftsubnet=0.0.0.0/0")
                conn_lines.append("    rightsubnet=0.0.0.0/0")

                # XFRM interface binding
                if_id = base_if_id + idx
                conn_lines.append(f"    if_id_in={if_id}")
                conn_lines.append(f"    if_id_out={if_id}")
                interface_name = f"xfrm{idx}"

                # Collect remote_prefixes for static mode (used later for kernel route installation)
                static_routes = tun.get("static_routes", {}) or {}
                tunnel_remote_prefixes = static_routes.get("remote_prefixes", [])
                if not tunnel_remote_prefixes:
                    # Fall back to connection-level remote_prefixes
                    tunnel_remote_prefixes = conn.get("remote_prefixes", []) or []

                # Crypto proposals
                if ike_props:
                    conn_lines.append(f"    ike={','.join(ike_props)}")
                if esp_props:
                    conn_lines.append(f"    esp={','.join(esp_props)}")

                # Lifetimes
                conn_lines.append(f"    ikelifetime={int(ike_life)}s")
                conn_lines.append(f"    keylife={int(esp_life)}s")

                # DPD (Dead Peer Detection)
                if dpd:
                    conn_lines.append(f"    dpddelay={int(dpd.get('interval_seconds', 30))}s")
                    conn_lines.append(f"    dpdtimeout={int(dpd.get('timeout_seconds', 120))}s")
                    conn_lines.append("    dpdaction=restart")

                # Auto-start
                conn_lines.append("    auto=start")

                connections.append("\n".join(conn_lines))

                # Track interface setup for route installation and device management
                interface_info = {
                    "name": interface_name,
                    "mode": tun_mode,
                    "local_inner_ip": inner_local_ip,
                    "remote_inner_ip": inner_remote_ip,
                    "cidr": inner_cidr,
                    "local_public_ip": local_public_ip,
                    "remote_public_ip": remote_public_ip,
                    "remote_prefixes": tunnel_remote_prefixes,
                    "if_id": base_if_id + idx,
                }

                interface_endpoints.append(interface_info)

                # PSK secret
                if psk:
                    # Format: local_ip remote_ip : PSK "secret"
                    # Using %any for local allows auto-detection
                    secrets_lines.append(f'%any {remote_public_ip} : PSK "{psk}"')

                idx += 1

        return connections, secrets_lines, interface_endpoints

    def build_interface_endpoints(self, cfg: dict[str, Any]) -> list[dict]:
        _, _, interface_endpoints = self._collect_tunnel_state(cfg)
        return interface_endpoints

    def render_and_apply(self, cfg: dict[str, Any]) -> None:
        """Render strongSwan config based on resolved per-VM YAML.

        Generates ipsec.conf with one connection per active tunnel and ipsec.secrets for PSKs.
        Supports IKEv1/IKEv2, configurable crypto proposals, DPD, and both BGP and static routing
        using XFRM interfaces.
        """
        connections, secrets_lines, interface_endpoints = self._collect_tunnel_state(cfg)

        # Write strongSwan plugin configuration based on mode
        STRONGSWAN_CONF_DIR.mkdir(parents=True, exist_ok=True)

        # Always disable automatic route installation (we manage routes via agent)
        install_routes_conf = """# generated by nebius-vpngw-agent
# Disable automatic route installation - agent manages routes
charon {
  install_routes = no
}
"""
        install_routes_path = STRONGSWAN_CONF_DIR / "install-routes.conf"
        install_routes_path.write_text(install_routes_conf, encoding="utf-8")
        print(f"[StrongSwan] Wrote {install_routes_path}")

        # Enable xfrm_if plugin, disable vti plugin
        xfrm_conf = """# generated by nebius-vpngw-agent
xfrm_if {
  load = yes
}
"""
        xfrm_conf_path = STRONGSWAN_CONF_DIR / "xfrm_if.conf"
        xfrm_conf_path.write_text(xfrm_conf, encoding="utf-8")
        print(f"[StrongSwan] Wrote {xfrm_conf_path} (enabled xfrm_if plugin)")

        # Disable VTI plugin if present
        vti_conf_path = STRONGSWAN_CONF_DIR / "vti.conf"
        if vti_conf_path.exists():
            vti_conf = """# generated by nebius-vpngw-agent
vti {
  load = no
}
"""
            vti_conf_path.write_text(vti_conf, encoding="utf-8")
            print(f"[StrongSwan] Wrote {vti_conf_path} (disabled vti plugin)")

        # Write netplan override to disable IPv4 link-local addressing on eth0
        # This prevents DHCP from injecting the broad 169.254.0.0/16 route
        # SAFE: Uses link-local: [ipv6] instead of use-routes: false
        netplan_override_text = """# generated by nebius-vpngw-agent
# Disable IPv4 link-local (APIPA 169.254/16) to prevent conflicts with VPN inner IPs
# This is SAFE - it does NOT block DHCP routes (default gateway, DNS, etc.)
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      dhcp4-overrides:
        use-dns: true
        use-routes: true
      link-local: [ ipv6 ]
"""
        netplan_dir = Path("/etc/netplan")
        netplan_dir.mkdir(parents=True, exist_ok=True)
        netplan_override = netplan_dir / "99-nebius-vpngw.yaml"
        netplan_override.write_text(netplan_override_text, encoding="utf-8")
        print(f"[StrongSwan] Wrote {netplan_override} (disabled IPv4 link-local)")

        # Apply netplan configuration
        result = subprocess.run(["netplan", "apply"], capture_output=True, text=True)
        if result.returncode == 0:
            print("[StrongSwan] ✓ Applied netplan configuration")
        else:
            print(f"[StrongSwan] ⚠ netplan apply failed: {result.stderr}")

        # Write ipsec.conf
        conf_text = [
            "# generated by nebius-vpngw-agent",
            "config setup",
            '    charondebug="ike 1, knl 1, net 1, cfg 1"',
            "    uniqueids=no",
            "",
        ] + connections

        IPSEC_CONF.write_text("\n".join(conf_text) + "\n", encoding="utf-8")
        print(f"[StrongSwan] Wrote {IPSEC_CONF} with {len(connections)} tunnel(s)")

        # Write ipsec.secrets
        secrets_text = ["# generated by nebius-vpngw-agent", ""] + secrets_lines
        secrets_content = "\n".join(secrets_text) + "\n"
        _write_secret_file(IPSEC_SECRETS, secrets_content)
        print("[StrongSwan] Wrote IPsec secrets")
        # Reload strongSwan to pick up new configs
        try:
            subprocess.run(
                ["systemctl", "restart", "strongswan-starter"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
            )
        except Exception as e:
            print(f"[StrongSwan] WARNING: failed to restart strongswan-starter: {e}")

        # Return interface configuration for external management (XFRM device creation, routing)
        # XFRM interfaces must be created externally before/after strongSwan starts
        return interface_endpoints
