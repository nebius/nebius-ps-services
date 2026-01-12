from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

IPSEC_CONF = Path("/etc/ipsec.conf")
STRONGSWAN_CONF_DIR = Path("/etc/strongswan.d/charon")
SWANCTL_CONF = Path("/etc/swanctl/swanctl.conf")
VICI_SOCKET = Path("/var/run/charon.vici")


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


def _wait_for_vici_socket(timeout_seconds: float = 15.0, interval_seconds: float = 0.5) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if VICI_SOCKET.exists():
            return True
        time.sleep(interval_seconds)
    return False


class StrongSwanRenderer:
    def _collect_tunnel_state(
        self, cfg: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        tunnels: list[dict[str, Any]] = []
        secrets: list[dict[str, Any]] = []

        defaults = cfg.get("defaults", {})
        global_ike_version = defaults.get("ike_version", 2)
        allow_ikev1 = defaults.get("allow_ikev1", False)
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

                # IKE version
                if ike_version == 2:
                    ike_version = 2
                elif ike_version == 1 and allow_ikev1:
                    ike_version = 1
                else:
                    print(
                        f"[StrongSwan] WARNING: Unsupported IKE version {ike_version} for {name}; skipping"
                    )
                    continue

                # Traffic selectors: limit local side to inner CIDR + gateway.local_prefixes
                # to avoid capturing public traffic/SSH; allow any remote (routes decide what flows).
                # Include local_prefixes on all tunnels so passive can carry traffic on failover.
                local_ts: list[str] = []
                if inner_cidr:
                    local_ts.append(inner_cidr)
                if gateway_local_prefixes:
                    local_ts.extend(gateway_local_prefixes)
                if not local_ts:
                    local_ts = ["0.0.0.0/0"]

                # XFRM interface binding
                if_id = base_if_id + idx
                interface_name = f"xfrm{idx}"

                # Collect remote_prefixes for static mode (used later for kernel route installation)
                static_routes = tun.get("static_routes", {}) or {}
                tunnel_remote_prefixes = static_routes.get("remote_prefixes", [])
                if not tunnel_remote_prefixes:
                    # Fall back to connection-level remote_prefixes
                    tunnel_remote_prefixes = conn.get("remote_prefixes", []) or []

                tunnels.append(
                    {
                        "name": name,
                        "ike_version": ike_version,
                        "local_public_ip": local_public_ip,
                        "remote_public_ip": remote_public_ip,
                        "local_ts": local_ts,
                        "remote_ts": "0.0.0.0/0",
                        "if_id": if_id,
                        "ike_props": ike_props,
                        "esp_props": esp_props,
                        "ike_life": ike_life,
                        "esp_life": esp_life,
                        "dpd": dpd,
                    }
                )

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
                    secrets.append(
                        {
                            "local_id": local_public_ip or "%any",
                            "remote_id": remote_public_ip,
                            "secret": psk,
                        }
                    )

                idx += 1

        return tunnels, secrets, interface_endpoints

    def build_interface_endpoints(self, cfg: dict[str, Any]) -> list[dict]:
        _, _, interface_endpoints = self._collect_tunnel_state(cfg)
        return interface_endpoints

    def render_and_apply(self, cfg: dict[str, Any]) -> None:
        """Render strongSwan config based on resolved per-VM YAML.

        Uses swanctl (VICI) to load per-tunnel connections with if_id_in/out for deterministic
        XFRM binding. Writes a minimal ipsec.conf to start charon via strongswan-starter.
        Supports IKEv1/IKEv2, configurable crypto proposals, DPD, and both BGP and static routing
        using XFRM interfaces.
        """
        tunnels, secrets, interface_endpoints = self._collect_tunnel_state(cfg)

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

        # Enable NAT keepalives to prevent NAT mapping timeout
        # Critical for UDP-encapsulated ESP (NAT-T) - prevents tunnel state desync
        keepalive_conf = """# generated by nebius-vpngw-agent
# Enable NAT keepalives to maintain NAT bindings for UDP-encap ESP
# Prevents tunnel state desync where tunnel shows ESTABLISHED but stops forwarding
charon {
  keep_alive = 20s
}
"""
        keepalive_conf_path = STRONGSWAN_CONF_DIR / "keepalive.conf"
        keepalive_conf_path.write_text(keepalive_conf, encoding="utf-8")
        print(f"[StrongSwan] Wrote {keepalive_conf_path} (enabled NAT keepalives: 20s)")

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

        # Write minimal ipsec.conf so strongswan-starter can launch charon without parsing tunnels.
        ipsec_text = [
            "# generated by nebius-vpngw-agent",
            "config setup",
            '    charondebug="ike 1, knl 1, net 1, cfg 1"',
            "    uniqueids=no",
            "",
        ]
        IPSEC_CONF.write_text("\n".join(ipsec_text) + "\n", encoding="utf-8")
        print(f"[StrongSwan] Wrote {IPSEC_CONF} (starter-only config)")

        # Write swanctl.conf (includes secrets)
        SWANCTL_CONF.parent.mkdir(parents=True, exist_ok=True)
        swanctl_lines = ["# generated by nebius-vpngw-agent", "connections {"]
        for tun in tunnels:
            name = tun["name"]
            swanctl_lines.append(f"  {name} {{")
            swanctl_lines.append(f"    version = {tun['ike_version']}")
            swanctl_lines.append("    local_addrs = %any")
            swanctl_lines.append(f"    remote_addrs = {tun['remote_public_ip']}")
            if tun["ike_props"]:
                swanctl_lines.append(f"    proposals = {','.join(tun['ike_props'])}")
            swanctl_lines.append(f"    rekey_time = {int(tun['ike_life'])}s")
            swanctl_lines.append("    local {")
            swanctl_lines.append("      auth = psk")
            if tun["local_public_ip"]:
                swanctl_lines.append(f"      id = {tun['local_public_ip']}")
            swanctl_lines.append("    }")
            swanctl_lines.append("    remote {")
            swanctl_lines.append("      auth = psk")
            swanctl_lines.append(f"      id = {tun['remote_public_ip']}")
            swanctl_lines.append("    }")
            dpd = tun.get("dpd") or {}
            if dpd:
                swanctl_lines.append(f"    dpd_delay = {int(dpd.get('interval_seconds', 30))}s")
                swanctl_lines.append(f"    dpd_timeout = {int(dpd.get('timeout_seconds', 120))}s")
            swanctl_lines.append("    children {")
            swanctl_lines.append(f"      {name} {{")
            swanctl_lines.append(f"        local_ts = {','.join(tun['local_ts'])}")
            swanctl_lines.append(f"        remote_ts = {tun['remote_ts']}")
            if tun["esp_props"]:
                swanctl_lines.append(f"        esp_proposals = {','.join(tun['esp_props'])}")
            swanctl_lines.append(f"        rekey_time = {int(tun['esp_life'])}s")
            swanctl_lines.append("        mode = tunnel")
            swanctl_lines.append("        start_action = start")
            swanctl_lines.append("        close_action = restart")
            swanctl_lines.append(f"        if_id_in = {tun['if_id']}")
            swanctl_lines.append(f"        if_id_out = {tun['if_id']}")
            swanctl_lines.append("      }")
            swanctl_lines.append("    }")
            swanctl_lines.append("  }")
        swanctl_lines.append("}")

        if secrets:
            swanctl_lines.append("secrets {")
            for idx, secret in enumerate(secrets, start=1):
                swanctl_lines.append(f"  ike-psk-{idx} {{")
                swanctl_lines.append(f"    id-1 = {secret['local_id']}")
                swanctl_lines.append(f"    id-2 = {secret['remote_id']}")
                swanctl_lines.append(f"    secret = \"{secret['secret']}\"")
                swanctl_lines.append("  }")
            swanctl_lines.append("}")

        swanctl_content = "\n".join(swanctl_lines) + "\n"
        _write_secret_file(SWANCTL_CONF, swanctl_content)
        print(f"[StrongSwan] Wrote {SWANCTL_CONF} (permissions: 0600)")

        # Enable VICI plugin so swanctl can talk to charon
        vici_conf = """# generated by nebius-vpngw-agent
vici {
  load = yes
}
"""
        vici_conf_path = STRONGSWAN_CONF_DIR / "vici.conf"
        vici_conf_path.write_text(vici_conf, encoding="utf-8")
        print(f"[StrongSwan] Wrote {vici_conf_path} (enabled vici plugin)")

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

        # Load swanctl connections and secrets via VICI
        try:
            load_result = None
            for attempt in range(1, 4):
                if not _wait_for_vici_socket():
                    print("[StrongSwan] WARNING: VICI socket not ready; retrying swanctl load")
                    time.sleep(1)
                    continue
                load_result = subprocess.run(
                    ["swanctl", "--load-all"],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=20,
                )
                if load_result.returncode == 0:
                    break
                print(
                    f"[StrongSwan] WARNING: swanctl load failed (attempt {attempt}): "
                    f"{(load_result.stderr or '').strip()}"
                )
                time.sleep(1)

            if load_result and load_result.returncode == 0:
                for tun in tunnels:
                    subprocess.run(
                        ["swanctl", "--initiate", "--child", tun["name"]],
                        check=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=20,
                    )
            else:
                print("[StrongSwan] WARNING: swanctl load did not succeed; skipping initiate")
        except Exception as e:
            print(f"[StrongSwan] WARNING: failed to load swanctl config: {e}")

        # Return interface configuration for external management (XFRM device creation, routing)
        # XFRM interfaces must be created externally before/after strongSwan starts
        return interface_endpoints
