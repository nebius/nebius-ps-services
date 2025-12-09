from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List
import ipaddress
import subprocess
import re

IPSEC_CONF = Path("/etc/ipsec.conf")
IPSEC_SECRETS = Path("/etc/ipsec.secrets")


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
                
                # For BGP mode: use VTI with /30 or /31 CIDR for the tunnel interface
                # For static mode: use policy-based with explicit subnets
                if tun_mode == "bgp":
                    # VTI mode: leftsubnet/rightsubnet are the tunnel IPs (CIDR)
                    if inner_cidr:
                        conn_lines.append(f"    leftsubnet={inner_cidr}")
                        conn_lines.append(f"    rightsubnet={inner_cidr}")
                    # Mark/key for the VTI interface creation; avoid policy mark to prevent EHOSTUNREACH on local sockets
                    mark_val = 100 + idx
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

                # Auto-start
                conn_lines.append("    auto=start")
                
                connections.append("\n".join(conn_lines))
                # Track VTI interface setup for BGP inner IPs
                if tun_mode == "bgp" and inner_local_ip and inner_remote_ip and inner_cidr:
                    vti_endpoints.append(
                        {
                            "name": f"vti{idx}",
                            "local_inner_ip": inner_local_ip,
                            "remote_inner_ip": inner_remote_ip,
                            "cidr": inner_cidr,
                            "mark": mark_val or (100 + idx),
                            "local_public_ip": local_public_ip,
                            "remote_public_ip": remote_public_ip,
                        }
                    )

                # PSK secret
                if psk:
                    # Format: local_ip remote_ip : PSK "secret"
                    # Using %any for local allows auto-detection
                    secrets_lines.append(f"%any {remote_public_ip} : PSK \"{psk}\"")

                idx += 1

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

        def _src_for_remote(ip: str) -> str | None:
            try:
                out = subprocess.run(
                    ["ip", "route", "get", ip],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout
                m = re.search(r"\bsrc\s+([0-9.]+)", out)
                if m:
                    return m.group(1).strip()
            except Exception:
                return None
            return None

        # Ensure local tunnel IPs are bound via VTIs so BGP can form over inner addresses.
        for vti in vti_endpoints:
            name = vti["name"]
            local_inner = vti["local_inner_ip"]
            remote_inner = vti["remote_inner_ip"]
            cidr = vti["cidr"]
            mark = vti["mark"]
            remote_pub = vti.get("remote_public_ip") or ""
            local_pub_cfg = vti.get("local_public_ip") or ""
            local_pub = local_pub_cfg
            if not remote_pub:
                print(f"[StrongSwan] WARNING: Missing remote_public_ip for {name}; skipping VTI setup")
                continue
            actual_src = _src_for_remote(remote_pub)
            # Prefer the actual source IP of the route to the peer (handles NAT/1-NIC cases)
            if actual_src:
                local_pub = actual_src
            elif not local_pub:
                local_pub = local_pub_cfg
            if not local_pub:
                print(f"[StrongSwan] WARNING: Could not determine local public IP for {name}; skipping VTI setup")
                continue
            try:
                net = ipaddress.ip_network(cidr, strict=False)
                prefix = net.prefixlen
            except Exception:
                prefix = 30
            # Recreate VTI to ensure correct endpoints/mark
            subprocess.run(["ip", "link", "del", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(
                ["ip", "link", "add", name, "type", "vti", "local", local_pub, "remote", remote_pub, "key", str(mark)],
                check=False,
            )
            subprocess.run(["ip", "addr", "flush", "dev", name], check=False)
            subprocess.run(["ip", "addr", "add", f"{local_inner}/{prefix}", "dev", name], check=False)
            subprocess.run(["ip", "link", "set", name, "up"], check=False)
            # Disable policy to avoid double-encryption
            subprocess.run(["sysctl", f"net.ipv4.conf.{name}.disable_policy=1"], check=False)
            subprocess.run(["sysctl", f"net.ipv4.conf.{name}.disable_xfrm=1"], check=False)
            # Allow asymmetric routing on VTIs so BGP keepalives are not dropped
            subprocess.run(["sysctl", f"net.ipv4.conf.{name}.rp_filter=0"], check=False)
            subprocess.run(["sysctl", "net.ipv4.conf.all.rp_filter=0"], check=False)
            subprocess.run(["sysctl", "net.ipv4.conf.default.rp_filter=0"], check=False)
            # Ensure a host route to the peer inner IP exists
            subprocess.run(["ip", "route", "replace", f"{remote_inner}/32", "dev", name], check=False)
            # Ensure packets on the VTI get the same mark as the xfrm policy, so BGP/TCP flows are encrypted
            for chain in (("OUTPUT", "-o"), ("PREROUTING", "-i")):
                table, direction_flag = chain
                check_rule = [
                    "iptables",
                    "-t",
                    "mangle",
                    "-C",
                    table,
                    direction_flag,
                    name,
                    "-j",
                    "MARK",
                    "--set-mark",
                    str(mark),
                ]
                add_rule = check_rule.copy()
                add_rule[3] = "-A"
                try:
                    res = subprocess.run(check_rule, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if res.returncode != 0:
                        subprocess.run(add_rule, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    # Ignore iptables errors; tunnel traffic may still work without marking rules
                    pass
