"""Pure canonical strongSwan tunnel and XFRM endpoint projection."""

from __future__ import annotations

from typing import Any


def collect_tunnel_state(
    cfg: dict[str, Any], *, log=print
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
        routing_mode = conn.get("routing_mode") or cfg.get("defaults", {}).get("routing", {}).get(
            "mode", "bgp"
        )
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
                log(f"[StrongSwan] WARNING: Tunnel {name} missing remote_public_ip; skipping")
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
                log(
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
