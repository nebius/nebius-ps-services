from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List
import ipaddress
import subprocess
import re

IPSEC_CONF = Path("/etc/ipsec.conf")
IPSEC_SECRETS = Path("/etc/ipsec.secrets")
STRONGSWAN_CONF_DIR = Path("/etc/strongswan.d/charon")


class StrongSwanRenderer:
    def render_and_apply(self, cfg: Dict[str, Any]) -> None:
        """Render strongSwan config based on resolved per-VM YAML.

        Generates ipsec.conf with one connection per active tunnel and ipsec.secrets for PSKs.
        Supports IKEv1/IKEv2, configurable crypto proposals, DPD, and both BGP (VTI) and static routing.
        """
        connections: List[str] = []
        secrets_lines: List[str] = []

        defaults = cfg.get("defaults", {})
        global_ike_version = defaults.get("ike_version", 2)
        allow_ikev1 = defaults.get("allow_ikev1", True)
        crypto_defaults = defaults.get("crypto", {})
        dpd = defaults.get("dpd", {})
        
        # Gateway-level local_prefixes: single source of truth for Nebius-side subnets
        gateway = cfg.get("gateway", {})
        gateway_local_prefixes: List[str] = gateway.get("local_prefixes", [])

        idx = 0
        vti_endpoints: List[dict] = []
        for conn in cfg.get("connections", []):
            routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get("routing", {}).get("mode", "bgp")
            for tun in conn.get("tunnels", []):
                if tun.get("ha_role", "active") != "active":
                    continue
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
                ike_life = ccrypto.get("ike_lifetime_seconds") or crypto_defaults.get("ike_lifetime_seconds", 28800)
                esp_life = ccrypto.get("esp_lifetime_seconds") or crypto_defaults.get("esp_lifetime_seconds", 3600)

                # Build connection stanza
                conn_lines = [f"conn {name}"]
                
                # IKE version
                if ike_version == 2:
                    conn_lines.append("    keyexchange=ikev2")
                elif ike_version == 1 and allow_ikev1:
                    conn_lines.append("    keyexchange=ikev1")
                else:
                    print(f"[StrongSwan] WARNING: Unsupported IKE version {ike_version} for {name}; skipping")
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
                mark_val = None
                
                # For BGP mode: use VTI with mark for route-based VPN
                # For static mode: use policy-based with explicit subnets
                if tun_mode == "bgp":
                    # GCP HA VPN requires 0.0.0.0/0 selectors
                    conn_lines.append("    leftsubnet=0.0.0.0/0")
                    conn_lines.append("    rightsubnet=0.0.0.0/0")
                    # Use mark=%unique to let strongSwan assign unique marks automatically
                    # This sets PLUTO_MARK_OUT and PLUTO_MARK_IN env vars for updown script
                    conn_lines.append("    mark=%unique")
                else:
                    # Static routing: use actual network prefixes
                    static_routes = tun.get("static_routes", {}) or {}
                    remote_prefixes = static_routes.get("remote_prefixes", [])
                    
                    # Local prefixes: use tunnel-specific override if provided, else gateway defaults
                    tunnel_local_prefixes = static_routes.get("local_prefixes", [])
                    if tunnel_local_prefixes:
                        # Tunnel-specific override (for split traffic scenarios)
                        local_prefixes = tunnel_local_prefixes
                    else:
                        # Default: use gateway-level local_prefixes
                        local_prefixes = gateway_local_prefixes
                    
                    if local_prefixes:
                        conn_lines.append(f"    leftsubnet={','.join(local_prefixes)}")
                    if remote_prefixes:
                        conn_lines.append(f"    rightsubnet={','.join(remote_prefixes)}")

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

                # Custom VTI updown script (no plugin required)
                # Script creates VTI interfaces using marks from strongSwan PLUTO variables
                if tun_mode == "bgp" and inner_local_ip and inner_remote_ip and inner_cidr:
                    # Extract prefix length from CIDR
                    try:
                        import ipaddress
                        net = ipaddress.ip_network(inner_cidr, strict=False)
                        prefix = net.prefixlen
                    except Exception:
                        prefix = 30  # Default for GCP HA VPN
                    # Pass: tunnel_id, remote_ip/prefix, local_ip/prefix
                    vti_id = idx  # Use index as VTI ID
                    conn_lines.append(f'    leftupdown="/var/lib/strongswan/ipsec-vti.sh {vti_id} {inner_remote_ip}/{prefix} {inner_local_ip}/{prefix}"')

                # Auto-start
                conn_lines.append("    auto=start")
                
                connections.append("\n".join(conn_lines))
                # Track VTI interface setup for BGP inner IPs
                if tun_mode == "bgp" and inner_local_ip and inner_remote_ip and inner_cidr:
                    vti_name = f"vti{idx}"  # Match updown script naming: vti0, vti1, etc.
                    vti_endpoints.append(
                        {
                            "name": vti_name,
                            "local_inner_ip": inner_local_ip,
                            "remote_inner_ip": inner_remote_ip,
                            "cidr": inner_cidr,
                            "local_public_ip": local_public_ip,
                            "remote_public_ip": remote_public_ip,
                            "remote_prefixes": conn.get("remote_prefixes", []) or [],
                        }
                    )

                # PSK secret
                if psk:
                    # Format: local_ip remote_ip : PSK "secret"
                    # Using %any for local allows auto-detection
                    secrets_lines.append(f"%any {remote_public_ip} : PSK \"{psk}\"")

                idx += 1

        # Write strongswan.d/charon configuration
        # Disable automatic route installation (we manage routes via agent)
        strongswan_conf_text = """# generated by nebius-vpngw-agent
# Disable automatic route installation - agent manages routes
charon {
  install_routes = no
}
"""
        STRONGSWAN_CONF_DIR.mkdir(parents=True, exist_ok=True)
        vti_conf_path = STRONGSWAN_CONF_DIR / "vti.conf"
        vti_conf_path.write_text(strongswan_conf_text, encoding="utf-8")
        print(f"[StrongSwan] Wrote {vti_conf_path} (disabled automatic route installation)")

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
            "    charondebug=\"ike 1, knl 1, net 1, cfg 1\"",
            "    uniqueids=no",
            "",
        ] + connections

        IPSEC_CONF.write_text("\n".join(conf_text) + "\n", encoding="utf-8")
        print(f"[StrongSwan] Wrote {IPSEC_CONF} with {len(connections)} tunnel(s)")

        # Write ipsec.secrets
        secrets_text = ["# generated by nebius-vpngw-agent\n"] + secrets_lines
        IPSEC_SECRETS.write_text("\n".join(secrets_text) + "\n", encoding="utf-8")
        print(f"[StrongSwan] Wrote {IPSEC_SECRETS}")
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

        # VTI interfaces are created by ipsec-vti.sh updown script when tunnels establish
        # For BGP mode: Only add host route to BGP peer - BGP will learn and install remote prefixes dynamically
        # For static mode: remote_prefixes would be in rightsubnet, not here
        import time
        time.sleep(2)  # Brief wait for tunnels to establish and updown script to run
        
        for vti in vti_endpoints:
            name = vti["name"]
            remote_inner = vti["remote_inner_ip"]
            
            # Ensure a host route to the BGP peer IP exists (required for BGP session)
            subprocess.run(["ip", "route", "replace", f"{remote_inner}/32", "dev", name], check=False)
            print(f"[StrongSwan] Added route to BGP peer {remote_inner} via {name}")
            
            # NOTE: In BGP mode, remote_prefixes are NOT installed as static routes here.
            # BGP will learn and install these routes dynamically from the peer.
            # If remote_prefixes is specified, it's used as a filter in FRR bgpd.conf.