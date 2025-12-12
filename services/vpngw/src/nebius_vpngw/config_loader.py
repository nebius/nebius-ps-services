from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import typing as t
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml
from pydantic import ValidationError

from . import schema


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


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
_INT_PATTERN = re.compile(r"^-?\d+$")


def _expand_env_value(val: str, missing: set[str]) -> str:
    """Expand ${VAR} placeholders in a single string.

    Multiple placeholders per string are supported. If an environment variable
    is missing its name is added to ``missing`` and the placeholder is left
    unchanged. Returning the original string when no placeholders are found is
    intentional to avoid touching unrelated values.
    """
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        env_val = os.environ.get(name)
        if env_val is None or env_val == "":
            missing.add(name)
            return match.group(0)  # keep placeholder for later diagnostics
        return env_val

    return _ENV_PATTERN.sub(repl, val)


def _expand_env(obj: t.Any, missing: set[str]) -> t.Any:
    """Recursively expand ${VAR} placeholders in a loaded YAML structure."""
    if isinstance(obj, dict):
        return {k: _expand_env(v, missing) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v, missing) for v in obj]
    if isinstance(obj, str):
        return _expand_env_value(obj, missing)
    return obj


def _to_int(val: t.Any) -> t.Optional[int]:
    """Return integer if val represents an int, else None.

    Accept ints directly or strings of digits (with optional leading -).
    """
    if isinstance(val, int):
        return val
    if isinstance(val, str) and _INT_PATTERN.match(val.strip()):
        try:
            return int(val.strip())
        except Exception:
            return None
    return None


def _enum_to_value(obj: t.Any) -> t.Any:
    """Recursively convert Enum objects to their values for YAML serialization.
    
    This is needed because yaml.safe_dump() cannot serialize Enum objects directly.
    """
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _enum_to_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_enum_to_value(v) for v in obj]
    return obj


def load_local_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    missing: set[str] = set()
    expanded = _expand_env(raw, missing)
    # Allow optional placeholders: if NETWORK_ID is missing and the value
    # is an unresolved placeholder, drop the field to fall back to default network.
    try:
        if "NETWORK_ID" in missing:
            gg0 = (expanded.get("gateway_group") or {})
            vm0 = (gg0.get("vm_spec") or {})
            nid = vm0.get("network_id")
            if isinstance(nid, str) and nid.strip() == "${NETWORK_ID}":
                vm0.pop("network_id", None)
                gg0["vm_spec"] = vm0
                expanded["gateway_group"] = gg0
                missing.discard("NETWORK_ID")
        # Treat unresolved placeholders in external_ips as "not provided":
        # drop any entries that remain as ${VAR} and clear those vars from missing.
        gg1 = (expanded.get("gateway_group") or {})
        ext1 = list((gg1.get("external_ips") or []))
        new_ext: list[str] = []
        for ip in ext1:
            if isinstance(ip, str) and _ENV_PATTERN.fullmatch(ip or ""):
                # Placeholder remained; mark its name as non-mandatory
                m = _ENV_PATTERN.match(ip)
                if m:
                    missing.discard(m.group(1))
                # Skip adding to the list
                continue
            if ip:
                new_ext.append(ip)
        if new_ext != ext1:
            gg1["external_ips"] = new_ext
            expanded["gateway_group"] = gg1
    except Exception:
        # Ignore and let normal missing handling report variables
        pass
    if missing:
        # Surface all missing vars at once to help the user export them.
        raise ValueError(
            "Missing environment variables for placeholders: "
            + ", ".join(sorted(missing))
        )
    
    # Optional convenience: read SSH public key from a path if provided
    # DO THIS BEFORE SCHEMA VALIDATION so schema sees the inline key
    try:
        gg = expanded.get("gateway_group", {}) or {}
        vm_spec = gg.get("vm_spec", {}) or {}
        ssh_key_path = vm_spec.get("ssh_public_key_path")
        ssh_key_inline = vm_spec.get("ssh_public_key")
        if ssh_key_path and not ssh_key_inline:
            p = Path(str(ssh_key_path)).expanduser()
            if not p.exists():
                raise ValueError(f"SSH public key file not found: {p}")
            key_text = p.read_text(encoding="utf-8").strip()
            # Insert content into ssh_public_key (keep the path for reference)
            vm_spec["ssh_public_key"] = key_text
            gg["vm_spec"] = vm_spec
            expanded["gateway_group"] = gg
    except Exception as e:
        # Re-raise as ValueError to provide a clear message to CLI
        raise ValueError(str(e))
    
    # ============================================================================
    # SCHEMA VALIDATION: Validate against strict Pydantic schema
    # This catches typos, unknown fields, type errors, and constraint violations
    # ============================================================================
    try:
        validated_config = schema.validate_config(expanded)
        # Convert back to dict for downstream processing
        # (preserves existing code paths while ensuring schema compliance)
        expanded = validated_config.model_dump(mode="python", exclude_none=False)
    except ValidationError as e:
        # Format Pydantic errors into user-friendly messages
        errors = []
        for err in e.errors():
            loc = " -> ".join(str(x) for x in err["loc"])
            msg = err["msg"]
            errors.append(f"  • {loc}: {msg}")
        
        raise ValueError(
            "Configuration validation failed:\n" + "\n".join(errors) +
            "\n\nPlease fix these errors and try again. "
            "Run 'nebius-vpngw validate-config <file>' to validate without deploying."
        )

    return expanded


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


def _validate_tunnel_inner_ips(tunnel: dict, tunnel_name: str) -> None:
    """Validate that inner_local_ip and inner_remote_ip fall within inner_cidr.
    
    Raises ValueError if validation fails.
    """
    inner_cidr = tunnel.get("inner_cidr")
    inner_local_ip = tunnel.get("inner_local_ip")
    inner_remote_ip = tunnel.get("inner_remote_ip")
    
    # Skip validation if any required field is missing
    if not inner_cidr or not inner_local_ip or not inner_remote_ip:
        return
    
    try:
        # Parse the CIDR network
        network = ipaddress.ip_network(inner_cidr, strict=False)
        local_ip = ipaddress.ip_address(inner_local_ip)
        remote_ip = ipaddress.ip_address(inner_remote_ip)
        
        # Check if IPs are within the network
        if local_ip not in network:
            raise ValueError(
                f"Tunnel '{tunnel_name}': inner_local_ip {inner_local_ip} is NOT within inner_cidr {inner_cidr}. "
                f"Network range: {network.network_address} - {network.broadcast_address}"
            )
        
        if remote_ip not in network:
            raise ValueError(
                f"Tunnel '{tunnel_name}': inner_remote_ip {inner_remote_ip} is NOT within inner_cidr {inner_cidr}. "
                f"Network range: {network.network_address} - {network.broadcast_address}"
            )
        
        # Additional check: warn if using network or broadcast address
        if local_ip == network.network_address or local_ip == network.broadcast_address:
            raise ValueError(
                f"Tunnel '{tunnel_name}': inner_local_ip {inner_local_ip} is the network or broadcast address. "
                f"Use a host address within {inner_cidr}"
            )
        
        if remote_ip == network.network_address or remote_ip == network.broadcast_address:
            raise ValueError(
                f"Tunnel '{tunnel_name}': inner_remote_ip {inner_remote_ip} is the network or broadcast address. "
                f"Use a host address within {inner_cidr}"
            )
            
    except ValueError:
        raise  # Re-raise validation errors
    except Exception as e:
        # Invalid CIDR format or IP format
        raise ValueError(
            f"Tunnel '{tunnel_name}': Invalid IP/CIDR format - inner_cidr={inner_cidr}, "
            f"inner_local_ip={inner_local_ip}, inner_remote_ip={inner_remote_ip}. Error: {e}"
        )


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
    p_asn = _to_int(peer_tun.get("remote_asn") or peer_tun.get("asn"))
    if p_asn is not None and conn_remote_asn and p_asn == conn_remote_asn:
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
    # Prefer gateway_group.region, else top-level region_id, else a sane default
    region = gg.get("region") or (local_cfg.get("region_id") or "eu-north1-a")
    external_ips = gg.get("external_ips", [])
    vm_spec = gg.get("vm_spec", {})
    
    # Validate and normalize num_nics configuration
    # CURRENT PLATFORM LIMITATION: Only 1 NIC per instance is supported
    # Future: When platform supports multi-NIC, this validation can be relaxed
    num_nics = int(vm_spec.get("num_nics", 1))
    if num_nics < 1:
        raise ValueError("num_nics must be at least 1")
    if num_nics > 1:
        raise ValueError(
            f"num_nics={num_nics} requested, but current Nebius platform only supports 1 NIC per instance. "
            "Set num_nics=1 in your config. When multi-NIC support is available, you can increase this value."
        )
    # Ensure num_nics is in vm_spec for downstream processing
    vm_spec["num_nics"] = num_nics

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
    # Ensure external_ips is a list to avoid NoneType errors when computing length
    ext_ips = external_ips or []
    for idx in range(instance_count):
        hostname = f"{name}-{idx}"
        ip = ext_ips[idx] if idx < len(ext_ips) else ""
        connections = local_cfg.get("connections", [])

        # Merge peer-derived values into tunnels that have null/empty fields
        merged_connections = []
        for conn in connections:
            conn_vendor = (conn.get("vendor") or "").lower()
            conn_tunnels = conn.get("tunnels", [])
            # Connection-level hints
            conn_bgp = (conn.get("bgp") or {})
            conn_remote_asn = _to_int(conn_bgp.get("remote_asn"))
            inferred_remote_asn: t.Optional[int] = conn_remote_asn
            conn_remote_prefixes = conn.get("remote_prefixes") or conn_bgp.get("remote_prefixes") or []
            routing_mode = conn.get("routing_mode") or (local_cfg.get("defaults", {}).get("routing", {}).get("mode") or "bgp")

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
                    if inferred_remote_asn is None:
                        inferred_remote_asn = _to_int(peer_tun.get("remote_asn"))
                tun = dict(tun)  # copy
                # Merge essential fields
                tun["psk"] = _merge_fields(tun.get("psk"), peer_tun.get("psk"))
                tun["inner_cidr"] = _merge_fields(tun.get("inner_cidr"), peer_tun.get("inner_cidr"))
                tun["inner_local_ip"] = _merge_fields(tun.get("inner_local_ip"), peer_tun.get("inner_local_ip"))
                tun["inner_remote_ip"] = _merge_fields(tun.get("inner_remote_ip"), peer_tun.get("inner_remote_ip"))
                tun["remote_public_ip"] = _merge_fields(tun.get("remote_public_ip"), peer_tun.get("remote_public_ip"))
                
                # VALIDATION: Ensure inner IPs fall within inner_cidr
                tunnel_name = tun.get("name", f"tunnel-{i}")
                _validate_tunnel_inner_ips(tun, tunnel_name)
                # local_public_ip is derived from YAML indices; preserve if present in peer
                tun["local_public_ip"] = _merge_fields(
                    _resolved_local_public_ip(local_cfg, tun), peer_tun.get("local_public_ip")
                )
                # Propagate connection-level remote_prefixes into static_routes if not set per-tunnel
                if routing_mode == "static":
                    sr = tun.get("static_routes") or {}
                    if not sr.get("remote_prefixes"):
                        if conn_remote_prefixes:
                            sr = dict(sr)
                            sr["remote_prefixes"] = conn_remote_prefixes
                            tun["static_routes"] = sr
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
            inst_tunnels_raw = [t for t in merged_tunnels if int(t.get("gateway_instance_index", 0)) == idx]
            if inst_tunnels_raw:
                # Inject the actual external IP into each tunnel's local_public_ip if not already set
                inst_tunnels = []
                for t in inst_tunnels_raw:
                    t_copy = dict(t)  # Make a copy to avoid modifying shared tunnel dict
                    lip = t_copy.get("local_public_ip")
                    if lip in (None, ""):
                        t_copy["local_public_ip"] = ip
                    inst_tunnels.append(t_copy)
                
                new_conn = dict(conn)
                # Fill connection-level BGP remote_asn if missing
                if inferred_remote_asn is not None:
                    bgp = (new_conn.get("bgp") or {}).copy()
                    if bgp.get("remote_asn") in (None, ""):
                        bgp["remote_asn"] = inferred_remote_asn
                    new_conn["bgp"] = bgp
                if conn_remote_prefixes:
                    new_conn["remote_prefixes"] = conn_remote_prefixes
                new_conn["tunnels"] = inst_tunnels
                merged_connections.append(new_conn)

        per_vm_cfg = {
            "gateway_group": {"name": name, "instance_index": idx},
            "gateway": local_cfg.get("gateway", {}),
            "defaults": local_cfg.get("defaults", {}),
            "connections": merged_connections,
        }
        # Convert Enum objects to their values before YAML serialization
        per_vm_cfg_serializable = _enum_to_value(per_vm_cfg)
        serialized = yaml.safe_dump(per_vm_cfg_serializable, sort_keys=False)
        per_instance.append(
            InstanceResolvedConfig(
                instance_index=idx, hostname=hostname, external_ip=ip, config_yaml=serialized
            )
        )

    # Determine if we should manage routes: enable if any connection/tunnel uses static routing
    manage_routes = False
    try:
        defaults_mode = (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode") or "bgp"
        for conn in (local_cfg.get("connections") or []):
            conn_mode = (conn.get("routing_mode") or defaults_mode) or "bgp"
            if conn_mode == "static":
                manage_routes = True
                break
            for tun in (conn.get("tunnels") or []):
                tun_mode = (tun.get("routing_mode") or conn_mode) or defaults_mode
                if tun_mode == "static":
                    manage_routes = True
                    break
            if manage_routes:
                break
    except Exception:
        manage_routes = False

    return ResolvedDeploymentPlan(
        gateway_group=gateway_group, per_instance=per_instance, manage_routes=manage_routes
    )
