from __future__ import annotations

import hashlib
import typing as t
from dataclasses import dataclass, field
from pathlib import Path

import yaml
import re


@dataclass
class GatewayGroupSpec:
    name: str
    instance_count: int
    region: str
    external_ips: t.List[str]
    vm_spec: dict


@dataclass
class InstanceResolvedConfig:
    instance_index: int
    hostname: str
    external_ip: str
    config_yaml: str  # serialized per-VM resolved config


@dataclass
class ResolvedDeploymentPlan:
    gateway_group: GatewayGroupSpec
    per_instance: t.List[InstanceResolvedConfig] = field(default_factory=list)
    manage_routes: bool = False

    def validate(self) -> None:
        if self.gateway_group.instance_count != len(self.per_instance):
            raise ValueError("Instance count mismatch in resolved plan")
        # Additional quota checks could go here

    @property
    def should_manage_routes(self) -> bool:
        return self.manage_routes

    def iter_instance_configs(self) -> t.Iterable[InstanceResolvedConfig]:
        return iter(self.per_instance)

    def summary(self) -> str:
        lines = [
            f"Gateway group: {self.gateway_group.name} ({self.gateway_group.instance_count} VM(s))",
            f"Region: {self.gateway_group.region}",
            "Instances:",
        ]
        for inst in self.per_instance:
            h = hashlib.sha256(inst.config_yaml.encode()).hexdigest()[:12]
            lines.append(f"  - idx={inst.instance_index} host={inst.hostname} ip={inst.external_ip} cfg={h}")
        return "\n".join(lines)


def load_local_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _detect_vendor(text: str) -> str:
    t_lower = text.lower()
    if "google cloud" in t_lower or "cloud router" in t_lower or "ha vpn" in t_lower:
        return "gcp"
    if "aws" in t_lower or "amazon" in t_lower or "customer gateway" in t_lower:
        return "aws"
    if "azure" in t_lower or "virtual network gateway" in t_lower:
        return "azure"
    if "cisco" in t_lower or "ios" in t_lower or "asa" in t_lower:
        return "cisco"
    return "generic"


def _parse_peer_file(path: Path) -> dict:
    from .peer_parsers import gcp as gcp_parser
    from .peer_parsers import aws as aws_parser
    from .peer_parsers import azure as azure_parser
    from .peer_parsers import cisco as cisco_parser

    text = path.read_text(encoding="utf-8", errors="ignore")
    vendor = _detect_vendor(text)
    if vendor == "gcp":
        parsed = gcp_parser.parse(text)
    elif vendor == "aws":
        parsed = aws_parser.parse(text)
    elif vendor == "azure":
        parsed = azure_parser.parse(text)
    elif vendor == "cisco":
        parsed = cisco_parser.parse(text)
    else:
        parsed = {"tunnels": []}
    parsed.setdefault("vendor", vendor)
    return parsed


def _merge_fields(yaml_val, peer_val, default_val=None):
    # Priority: YAML explicit -> peer config -> default
    if yaml_val not in (None, [], ""):
        return yaml_val
    if peer_val not in (None, [], ""):
        return peer_val
    return default_val


def _resolved_local_public_ip(local_cfg: dict, tunnel: dict) -> t.Optional[str]:
    gg = local_cfg.get("gateway_group", {})
    ips = gg.get("external_ips", [])
    idx = tunnel.get("local_public_ip_index")
    try:
        if isinstance(idx, int) and 0 <= idx < len(ips):
            return ips[idx]
    except Exception:
        return None
    return None


def _score_peer_tunnel(
    conn_vendor: str,
    conn_remote_asn: t.Optional[int],
    yaml_tun: dict,
    peer_tun: dict,
    local_cfg: dict,
) -> int:
    score = 0
    pv = (peer_tun.get("vendor") or "").lower()
    if pv and conn_vendor and pv == conn_vendor:
        score += 6
    # ASN match
    p_asn = peer_tun.get("remote_asn") or peer_tun.get("asn")
    if isinstance(p_asn, int) and conn_remote_asn and p_asn == conn_remote_asn:
        score += 6
    # Public IP alignment
    y_local_pub = _resolved_local_public_ip(local_cfg, yaml_tun)
    if y_local_pub and peer_tun.get("local_public_ip") == y_local_pub:
        score += 4
    if peer_tun.get("remote_public_ip") and peer_tun.get("remote_public_ip") != y_local_pub:
        score += 2
    # Inner IP/cidr hints
    hints = 0
    for key in ("inner_cidr", "inner_local_ip", "inner_remote_ip"):
        yv = yaml_tun.get(key)
        pv2 = peer_tun.get(key)
        if yv and pv2 and str(yv) == str(pv2):
            hints += 2
    score += hints
    return score


def _normalize_peer_specs(peer_specs: list[dict]) -> list[dict]:
    """Flatten peer specs into per-tunnel items with vendor/asn at tunnel level."""
    flat: list[dict] = []
    for spec in peer_specs:
        vendor = (spec.get("vendor") or "").lower()
        remote_asn = spec.get("remote_asn")
        for tnl in spec.get("tunnels", []):
            item = {**tnl}
            item.setdefault("vendor", vendor)
            if item.get("remote_asn") is None:
                item["remote_asn"] = remote_asn
            flat.append(item)
    return flat


def merge_with_peer_configs(local_cfg: dict, peer_files: t.List[Path]) -> ResolvedDeploymentPlan:
    # Build normalized peer specs
    peer_specs = [_parse_peer_file(p) for p in peer_files]
    gg = local_cfg.get("gateway_group", {})
    instance_count = int(gg.get("instance_count", 1))
    name = gg.get("name", "nebius-vpn-gw")
    region = gg.get("region", "eu-north1-a")
    external_ips = gg.get("external_ips", [])
    vm_spec = gg.get("vm_spec", {})

    gateway_group = GatewayGroupSpec(
        name=name,
        instance_count=instance_count,
        region=region,
        external_ips=external_ips,
        vm_spec=vm_spec,
    )

    # Build per-instance configs by filtering tunnels for each instance
    per_instance: t.List[InstanceResolvedConfig] = []
    flat_peer_tunnels = _normalize_peer_specs(peer_specs)
    for idx in range(instance_count):
        hostname = f"{name}-{idx}"
        ip = external_ips[idx] if idx < len(external_ips) else ""
        connections = local_cfg.get("connections", [])

        # Merge peer-derived values into tunnels that have null/empty fields
        merged_connections = []
        for conn in connections:
            conn_vendor = (conn.get("vendor") or "").lower()
            conn_tunnels = conn.get("tunnels", [])
            # Connection-level hints
            conn_bgp = (conn.get("bgp") or {})
            conn_remote_asn = conn_bgp.get("remote_asn") if isinstance(conn_bgp.get("remote_asn"), int) else None
            inferred_remote_asn: t.Optional[int] = conn_remote_asn

            merged_tunnels = []
            used_indices: set[int] = set()
            for i, tun in enumerate(conn_tunnels):
                # Choose best matching peer tunnel
                best_idx = None
                best_score = 0
                for j, pt in enumerate(flat_peer_tunnels):
                    if j in used_indices:
                        continue
                    score = _score_peer_tunnel(conn_vendor, conn_remote_asn, tun, pt, local_cfg)
                    if score > best_score:
                        best_score = score
                        best_idx = j
                peer_tun = flat_peer_tunnels[best_idx] if best_idx is not None else {}
                if best_idx is not None:
                    used_indices.add(best_idx)
                    if inferred_remote_asn is None and isinstance(peer_tun.get("remote_asn"), int):
                        inferred_remote_asn = peer_tun.get("remote_asn")
                tun = dict(tun)  # copy
                # Merge essential fields
                tun["psk"] = _merge_fields(tun.get("psk"), peer_tun.get("psk"))
                tun["inner_cidr"] = _merge_fields(tun.get("inner_cidr"), peer_tun.get("inner_cidr"))
                tun["inner_local_ip"] = _merge_fields(tun.get("inner_local_ip"), peer_tun.get("inner_local_ip"))
                tun["inner_remote_ip"] = _merge_fields(tun.get("inner_remote_ip"), peer_tun.get("inner_remote_ip"))
                tun["remote_public_ip"] = _merge_fields(tun.get("remote_public_ip"), peer_tun.get("remote_public_ip"))
                # local_public_ip is derived from YAML indices; preserve if present in peer
                tun["local_public_ip"] = _merge_fields(
                    _resolved_local_public_ip(local_cfg, tun), peer_tun.get("local_public_ip")
                )
                # Crypto proposals
                crypto = tun.get("crypto", {}) or {}
                pcrypto = peer_tun.get("crypto", {}) or {}
                crypto["ike_proposals"] = _merge_fields(
                    crypto.get("ike_proposals"), pcrypto.get("ike_proposals"), default_val=[]
                )
                crypto["esp_proposals"] = _merge_fields(
                    crypto.get("esp_proposals"), pcrypto.get("esp_proposals"), default_val=[]
                )
                crypto["ike_lifetime_seconds"] = _merge_fields(
                    crypto.get("ike_lifetime_seconds"), pcrypto.get("ike_lifetime_seconds")
                )
                crypto["esp_lifetime_seconds"] = _merge_fields(
                    crypto.get("esp_lifetime_seconds"), pcrypto.get("esp_lifetime_seconds")
                )
                tun["crypto"] = crypto

                merged_tunnels.append(tun)

            # Filter tunnels assigned to this instance
            inst_tunnels = [t for t in merged_tunnels if int(t.get("gateway_instance_index", 0)) == idx]
            if inst_tunnels:
                new_conn = dict(conn)
                # Fill connection-level BGP remote_asn if missing
                if inferred_remote_asn is not None:
                    bgp = (new_conn.get("bgp") or {}).copy()
                    if bgp.get("remote_asn") in (None, ""):
                        bgp["remote_asn"] = inferred_remote_asn
                    new_conn["bgp"] = bgp
                new_conn["tunnels"] = inst_tunnels
                merged_connections.append(new_conn)

        per_vm_cfg = {
            "gateway_group": {"name": name, "instance_index": idx},
            "gateway": local_cfg.get("gateway", {}),
            "defaults": local_cfg.get("defaults", {}),
            "connections": merged_connections,
        }
        serialized = yaml.safe_dump(per_vm_cfg, sort_keys=False)
        per_instance.append(
            InstanceResolvedConfig(
                instance_index=idx, hostname=hostname, external_ip=ip, config_yaml=serialized
            )
        )

    # Determine if we should manage routes (future flag; default False)
    manage_routes = False

    return ResolvedDeploymentPlan(
        gateway_group=gateway_group, per_instance=per_instance, manage_routes=manage_routes
    )
