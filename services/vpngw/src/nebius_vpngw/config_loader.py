from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import re
import stat
import typing as t
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml
from pydantic import ValidationError

from . import schema
from .peer_parsers.common import detect_vendor as _detect_vendor_common
from .peer_parsers.importer import (
    build_connection_config,
    merge_connection_specs,
    parse_peer_source,
)


@dataclass
class GatewayGroupSpec:
    name: str
    instance_count: int
    region: str
    external_ips: list[list[str]]
    vm_spec: dict
    network_id: str | None = None
    subnet: dict = field(default_factory=dict)
    vm_ha: VMHAProvisioningSpec | None = None


@dataclass(frozen=True)
class VMHAProvisioningSpec:
    """Minimum immutable intent required to provision VM-HA cloud identities."""

    cluster_id: str
    members: tuple[VMHANodeRecord, VMHANodeRecord]
    generation: VMHAGenerationRecord

    @property
    def active_instance_index(self) -> int:
        return next(
            member.instance_index
            for member in self.members
            if member.role is schema.VMHARole.ACTIVE
        )


@dataclass(frozen=True)
class VMHANodeRecord:
    """Stable identity and configured role for one VM-HA member."""

    node_id: str
    instance_index: int
    role: schema.VMHARole
    credential_sources: schema.VMHACredentialSourceReferences


@dataclass(frozen=True)
class VMHADigestRecord:
    """SHA-256 identities for canonical and logical configuration."""

    configuration: str
    static_routes: str
    bgp_policy: str


@dataclass(frozen=True)
class VMHALogicalManifests:
    """Canonical logical intent shared by both VM-HA nodes."""

    static_routes_json: str
    bgp_policy_json: str


@dataclass(frozen=True)
class VMHAGenerationRecord:
    """One immutable configuration generation for a VM-HA cluster."""

    generation_id: str
    digests: VMHADigestRecord
    logical_manifests: VMHALogicalManifests


@dataclass(frozen=True)
class VMHAReadinessRecord:
    """Exact parity required before a node is promotion-ready."""

    required_node_ids: tuple[str, str]
    generation_id: str
    digests: VMHADigestRecord


@dataclass(frozen=True)
class VMHAClusterRecord:
    """Resolved immutable VM-HA contract shared by both node plans."""

    cluster_id: str
    members: tuple[VMHANodeRecord, VMHANodeRecord]
    generation: VMHAGenerationRecord
    readiness: VMHAReadinessRecord


@dataclass
class InstanceResolvedConfig:
    instance_index: int
    hostname: str
    external_ip: str
    config_yaml: str  # serialized per-VM resolved config
    vm_ha_node: VMHANodeRecord | None = None
    vm_ha_generation: VMHAGenerationRecord | None = None
    vm_ha_readiness: VMHAReadinessRecord | None = None


@dataclass
class ResolvedDeploymentPlan:
    gateway_group: GatewayGroupSpec
    gateway: dict = field(default_factory=dict)
    per_instance: list[InstanceResolvedConfig] = field(default_factory=list)
    manage_routes: bool = False
    vm_ha: VMHAClusterRecord | None = None

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
            lines.append(
                f"  - idx={inst.instance_index} host={inst.hostname} ip={inst.external_ip} cfg={h}"
            )
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


def _to_int(val: t.Any) -> int | None:
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


def _canonical_json(value: t.Any) -> str:
    return json.dumps(
        _enum_to_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _build_vm_ha_cluster_record(local_cfg: dict) -> VMHAClusterRecord | None:
    vm_ha = (local_cfg.get("gateway_group") or {}).get("vm_ha")
    if not vm_ha or not vm_ha.get("enabled", False):
        return None

    members = tuple(
        VMHANodeRecord(
            node_id=str(member["node_id"]),
            instance_index=int(member["instance_index"]),
            role=schema.VMHARole(member["role"]),
            credential_sources=schema.VMHACredentialSourceReferences.model_validate(
                member["credential_sources"]
            ),
        )
        for member in sorted(vm_ha["members"], key=lambda item: int(item["instance_index"]))
    )
    if len(members) != 2:
        raise ValueError("VM-HA resolved plans require exactly two members")

    defaults_mode = ((local_cfg.get("defaults") or {}).get("routing") or {}).get("mode", "bgp")
    static_routes: list[dict[str, t.Any]] = []
    bgp_policies: list[dict[str, t.Any]] = []
    gateway = local_cfg.get("gateway") or {}
    for connection in local_cfg.get("connections") or []:
        connection_mode = connection.get("routing_mode") or defaults_mode
        if connection_mode == "static":
            remote_prefixes = set(connection.get("remote_prefixes") or [])
            for tunnel in connection.get("tunnels") or []:
                remote_prefixes.update(
                    (tunnel.get("static_routes") or {}).get("remote_prefixes") or []
                )
            static_routes.append(
                {
                    "connection": connection.get("name"),
                    "remote_prefixes": sorted(remote_prefixes),
                }
            )
        elif connection_mode == "bgp":
            bgp = connection.get("bgp") or {}
            advertise_local_prefixes = bool(bgp.get("advertise_local_prefixes", True))
            bgp_policies.append(
                {
                    "advertise_local_prefixes": advertise_local_prefixes,
                    "connection": connection.get("name"),
                    "local_asn": gateway.get("local_asn"),
                    "local_prefixes": (
                        sorted(gateway.get("local_prefixes") or [])
                        if advertise_local_prefixes
                        else []
                    ),
                    "remote_asn": bgp.get("remote_asn"),
                    "remote_prefixes": sorted(
                        connection.get("remote_prefixes") or bgp.get("remote_prefixes") or []
                    ),
                }
            )

    canonical_config = copy.deepcopy(local_cfg)
    canonical_config["gateway_group"]["vm_ha"]["members"] = sorted(
        canonical_config["gateway_group"]["vm_ha"]["members"],
        key=lambda item: int(item["instance_index"]),
    )
    for member in canonical_config["gateway_group"]["vm_ha"]["members"]:
        member.pop("credential_sources", None)
    canonical_configuration = _canonical_json(canonical_config)
    logical_manifests = VMHALogicalManifests(
        static_routes_json=_canonical_json(static_routes),
        bgp_policy_json=_canonical_json(bgp_policies),
    )
    digests = VMHADigestRecord(
        configuration=_sha256_text(canonical_configuration),
        static_routes=_sha256_text(logical_manifests.static_routes_json),
        bgp_policy=_sha256_text(logical_manifests.bgp_policy_json),
    )
    generation = VMHAGenerationRecord(
        generation_id=digests.configuration,
        digests=digests,
        logical_manifests=logical_manifests,
    )
    readiness = VMHAReadinessRecord(
        required_node_ids=t.cast(tuple[str, str], tuple(member.node_id for member in members)),
        generation_id=generation.generation_id,
        digests=digests,
    )
    return VMHAClusterRecord(
        cluster_id=str(vm_ha["cluster_id"]),
        members=t.cast(tuple[VMHANodeRecord, VMHANodeRecord], members),
        generation=generation,
        readiness=readiness,
    )


def load_local_config(
    path: Path,
    *,
    allow_missing_placeholders: bool = False,
    validate_schema: bool = True,
) -> dict:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    missing: set[str] = set()
    expanded = _expand_env(raw, missing)
    vm_ha_was_provided = "vm_ha" in (expanded.get("gateway_group") or {})
    # Allow optional placeholders: if NETWORK_ID is missing and the value
    # is an unresolved placeholder, drop the field to fall back to default network.
    try:
        if "NETWORK_ID" in missing:
            gg0 = expanded.get("gateway_group") or {}
            nid = gg0.get("network_id")
            if isinstance(nid, str) and nid.strip() == "${NETWORK_ID}":
                gg0.pop("network_id", None)
                expanded["gateway_group"] = gg0
                missing.discard("NETWORK_ID")
        # Treat unresolved placeholders in external_ips as "not provided":
        # drop any entries that remain as ${VAR} and clear those vars from missing.
        gg1 = expanded.get("gateway_group") or {}
        ext1 = gg1.get("external_ips")
        if isinstance(ext1, list):
            new_ext: list[t.Any] = []
            for entry in ext1:
                if isinstance(entry, list):
                    cleaned: list[str] = []
                    for ip in entry:
                        if isinstance(ip, str) and _ENV_PATTERN.fullmatch(ip or ""):
                            m = _ENV_PATTERN.match(ip)
                            if m:
                                missing.discard(m.group(1))
                            continue
                        if ip:
                            cleaned.append(ip)
                    new_ext.append(cleaned)
                    continue
                new_ext.append(entry)
            if new_ext != ext1:
                gg1["external_ips"] = new_ext
            expanded["gateway_group"] = gg1
    except Exception:
        # Ignore and let normal missing handling report variables
        pass
    if missing and not allow_missing_placeholders:
        # Surface all missing vars at once to help the user export them.
        raise ValueError(
            "Missing environment variables for placeholders: " + ", ".join(sorted(missing))
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
        raise ValueError(str(e)) from e

    # ============================================================================
    # SCHEMA VALIDATION: Validate against strict Pydantic schema
    # This catches typos, unknown fields, type errors, and constraint violations
    # ============================================================================
    if validate_schema:
        try:
            validated_config = schema.validate_config(expanded)
            # Convert back to dict for downstream processing
            # (preserves existing code paths while ensuring schema compliance)
            expanded = validated_config.model_dump(mode="python", exclude_none=False)
            if not vm_ha_was_provided:
                expanded["gateway_group"].pop("vm_ha", None)
            else:
                _validate_vm_ha_credential_sources(expanded)
        except ValidationError as e:
            # Format Pydantic errors into user-friendly messages
            errors = []
            for err in e.errors():
                loc = " -> ".join(str(x) for x in err["loc"])
                msg = err["msg"]
                errors.append(f"  • {loc}: {msg}")

            raise ValueError(
                "Configuration validation failed:\n"
                + "\n".join(errors)
                + "\n\nPlease fix these errors and try again. "
                "Run 'nebius-vpngw validate-config <file>' to validate without deploying."
            ) from e

    return expanded


def _validate_vm_ha_credential_sources(config: dict) -> None:
    """Fail closed on unsafe operator files without disclosing their paths."""

    vm_ha = (config.get("gateway_group") or {}).get("vm_ha") or {}
    if not vm_ha.get("enabled", False):
        return
    for member in vm_ha.get("members") or []:
        node_id = str(member.get("node_id") or "unknown")
        sources = member.get("credential_sources") or {}
        for name in (
            "certificate_authority",
            "certificate",
            "private_key",
            "nebius_credentials",
        ):
            path = Path(str(sources.get(name) or ""))
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise ValueError(
                    f"VM-HA credential source {name} for {node_id} is unavailable"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    f"VM-HA credential source {name} for {node_id} must be a non-symlink regular file"
                )
            if not os.access(path, os.R_OK):
                raise ValueError(f"VM-HA credential source {name} for {node_id} is not readable")


def _detect_vendor(text: str) -> str:
    return _detect_vendor_common(text)


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
        ) from e


def _parse_peer_file(path: Path) -> dict:
    parsed_specs = merge_connection_specs(parse_peer_source(path))
    if not parsed_specs:
        return {"vendor": "generic", "tunnels": []}
    if len(parsed_specs) == 1:
        parsed = parsed_specs[0]
        parsed.setdefault("tunnels", [])
        parsed.setdefault("vendor", "generic")
        return parsed

    combined: dict[str, t.Any] = {
        "vendor": parsed_specs[0].get("vendor", "generic"),
        "routing_mode": next(
            (spec.get("routing_mode") for spec in parsed_specs if spec.get("routing_mode")),
            None,
        ),
        "remote_asn": next(
            (spec.get("remote_asn") for spec in parsed_specs if spec.get("remote_asn") is not None),
            None,
        ),
        "remote_prefixes": [],
        "tunnels": [],
    }
    vendors = {str(spec.get("vendor") or "generic") for spec in parsed_specs}
    if len(vendors) > 1:
        combined["vendor"] = "generic"

    for spec in parsed_specs:
        combined["tunnels"].extend(spec.get("tunnels") or [])
        for prefix in spec.get("remote_prefixes") or []:
            if prefix not in combined["remote_prefixes"]:
                combined["remote_prefixes"].append(prefix)

    if not combined["remote_prefixes"]:
        combined.pop("remote_prefixes", None)
    return combined


def build_config_from_peer_files(base_cfg: dict, peer_files: list[Path]) -> dict:
    cfg = copy.deepcopy(base_cfg)
    parsed_specs: list[dict[str, t.Any]] = []
    for peer_file in peer_files:
        parsed_specs.extend(parse_peer_source(peer_file))

    merged_specs = merge_connection_specs(parsed_specs)
    if not merged_specs:
        return cfg

    cfg["connections"] = [
        build_connection_config(spec, connection_index=index)
        for index, spec in enumerate(merged_specs)
    ]

    routing_modes = {
        str(conn.get("routing_mode")) for conn in cfg["connections"] if conn.get("routing_mode")
    }
    if len(routing_modes) == 1:
        defaults = cfg.get("defaults") or {}
        routing = defaults.get("routing") or {}
        routing["mode"] = next(iter(routing_modes))
        defaults["routing"] = routing
        cfg["defaults"] = defaults

    return cfg


def _merge_fields(yaml_val, peer_val, default_val=None):
    # Priority: YAML explicit -> peer config -> default
    if yaml_val not in (None, [], ""):
        return yaml_val
    if peer_val not in (None, [], ""):
        return peer_val
    return default_val


def _merge_fields_peer_first(peer_val, yaml_val, default_val=None):
    # Priority: peer config -> YAML explicit -> default
    if peer_val not in (None, [], ""):
        return peer_val
    if yaml_val not in (None, [], ""):
        return yaml_val
    return default_val


def _merge_with_preference(yaml_val, peer_val, prefer_peer: bool, default_val=None):
    if prefer_peer:
        return _merge_fields_peer_first(peer_val, yaml_val, default_val=default_val)
    return _merge_fields(yaml_val, peer_val, default_val=default_val)


def _crypto_is_complete(crypto: dict) -> bool:
    required = (
        "ike_proposals",
        "ike_lifetime_seconds",
        "esp_proposals",
        "esp_lifetime_seconds",
    )
    return all(crypto.get(key) not in (None, [], "") for key in required)


def _merge_crypto_overrides(
    yaml_crypto: dict | None, peer_crypto: dict | None, prefer_peer: bool
) -> dict | None:
    yc = yaml_crypto or {}
    pc = peer_crypto or {}
    if not yc and not pc:
        return None
    merged = {
        "ike_proposals": _merge_with_preference(
            yc.get("ike_proposals"), pc.get("ike_proposals"), prefer_peer
        ),
        "ike_lifetime_seconds": _merge_with_preference(
            yc.get("ike_lifetime_seconds"),
            pc.get("ike_lifetime_seconds"),
            prefer_peer,
        ),
        "esp_proposals": _merge_with_preference(
            yc.get("esp_proposals"), pc.get("esp_proposals"), prefer_peer
        ),
        "esp_lifetime_seconds": _merge_with_preference(
            yc.get("esp_lifetime_seconds"),
            pc.get("esp_lifetime_seconds"),
            prefer_peer,
        ),
        "dh_groups": _merge_with_preference(yc.get("dh_groups"), pc.get("dh_groups"), prefer_peer),
    }
    if not _crypto_is_complete(merged):
        return None
    if merged.get("dh_groups") in (None, [], ""):
        merged.pop("dh_groups", None)
    return merged


def _resolved_local_public_ip(local_cfg: dict, tunnel: dict) -> str | None:
    gg = local_cfg.get("gateway_group", {}) or {}
    ips = gg.get("external_ips") or []
    inst_idx = tunnel.get("gateway_instance_index", 0)
    nic_idx = tunnel.get("local_public_ip_index")
    if nic_idx is None:
        nic_idx = 0
    try:
        if isinstance(inst_idx, int) and 0 <= inst_idx < len(ips):
            inst_ips = ips[inst_idx]
            if isinstance(inst_ips, list) and isinstance(nic_idx, int):
                if 0 <= nic_idx < len(inst_ips):
                    val = inst_ips[nic_idx]
                    return val or None
    except Exception:
        return None
    return None


def _score_peer_tunnel(
    conn_vendor: str,
    conn_remote_asn: int | None,
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


def merge_peer_configs_into_local_config(
    local_cfg: dict, peer_files: list[Path], *, prefer_peer: bool = False
) -> dict:
    """Merge keyword-imported peer data into an existing local config dict.

    prefer_peer=True will overwrite existing values with peer values when present.
    prefer_peer=False only fills missing fields (local config wins).
    """
    cfg = copy.deepcopy(local_cfg)
    peer_specs = [_parse_peer_file(p) for p in peer_files]
    if not peer_specs:
        return cfg
    flat_peer_tunnels = _normalize_peer_specs(peer_specs)
    if not flat_peer_tunnels:
        return cfg

    peer_vendor = next((spec.get("vendor") for spec in peer_specs if spec.get("vendor")), None)
    peer_remote_asn: int | None = None
    peer_routing_mode = next(
        (spec.get("routing_mode") for spec in peer_specs if spec.get("routing_mode")),
        None,
    )
    peer_remote_prefixes: list[str] = []
    for spec in peer_specs:
        if spec.get("remote_asn") is not None:
            peer_remote_asn = _to_int(spec.get("remote_asn"))
            if peer_remote_asn is not None:
                break
    for spec in peer_specs:
        for prefix in spec.get("remote_prefixes") or []:
            if prefix not in peer_remote_prefixes:
                peer_remote_prefixes.append(prefix)

    def merge_value(yaml_val, peer_val, default_val=None):
        return _merge_with_preference(yaml_val, peer_val, prefer_peer, default_val)

    connections = cfg.get("connections") or []
    for conn in connections:
        conn_vendor = (conn.get("vendor") or "").lower()
        if prefer_peer and peer_vendor:
            conn["vendor"] = peer_vendor
            conn_vendor = peer_vendor
        if peer_routing_mode and (prefer_peer or not conn.get("routing_mode")):
            conn["routing_mode"] = peer_routing_mode
        if peer_remote_prefixes and (prefer_peer or not conn.get("remote_prefixes")):
            conn["remote_prefixes"] = peer_remote_prefixes

        conn_bgp = conn.get("bgp") or {}
        conn_remote_asn = _to_int(conn_bgp.get("remote_asn"))
        if peer_remote_asn is not None and (prefer_peer or conn_remote_asn is None):
            conn_bgp["remote_asn"] = peer_remote_asn
            conn_remote_asn = peer_remote_asn
        if conn.get("routing_mode") == "bgp":
            conn_bgp["enabled"] = True
        elif conn.get("routing_mode") == "static":
            conn_bgp["enabled"] = False
        conn["bgp"] = conn_bgp

        conn_tunnels = conn.get("tunnels") or []
        used_indices: set[int] = set()
        merged_tunnels = []
        for i, tun in enumerate(conn_tunnels):
            best_idx = None
            best_score = 0
            for j, pt in enumerate(flat_peer_tunnels):
                if j in used_indices:
                    continue
                score = _score_peer_tunnel(conn_vendor, conn_remote_asn, tun, pt, cfg)
                if score > best_score:
                    best_score = score
                    best_idx = j
            if best_idx is None and flat_peer_tunnels:
                if i < len(flat_peer_tunnels) and i not in used_indices:
                    best_idx = i
                else:
                    for j in range(len(flat_peer_tunnels)):
                        if j not in used_indices:
                            best_idx = j
                            break
            peer_tun = flat_peer_tunnels[best_idx] if best_idx is not None else {}
            if best_idx is not None:
                used_indices.add(best_idx)
                if conn_remote_asn is None:
                    inferred_asn = _to_int(peer_tun.get("remote_asn"))
                    if inferred_asn is not None:
                        conn_remote_asn = inferred_asn
                        conn_bgp["remote_asn"] = inferred_asn
                        conn["bgp"] = conn_bgp

            tun = dict(tun)
            tun["psk"] = merge_value(tun.get("psk"), peer_tun.get("psk"))
            tun["inner_cidr"] = merge_value(tun.get("inner_cidr"), peer_tun.get("inner_cidr"))
            tun["inner_local_ip"] = merge_value(
                tun.get("inner_local_ip"), peer_tun.get("inner_local_ip")
            )
            tun["inner_remote_ip"] = merge_value(
                tun.get("inner_remote_ip"), peer_tun.get("inner_remote_ip")
            )
            tun["remote_public_ip"] = merge_value(
                tun.get("remote_public_ip"), peer_tun.get("remote_public_ip")
            )

            merged_crypto = _merge_crypto_overrides(
                tun.get("crypto"), peer_tun.get("crypto"), prefer_peer
            )
            if merged_crypto is not None:
                tun["crypto"] = merged_crypto
            else:
                tun.pop("crypto", None)

            tunnel_name = tun.get("name", f"tunnel-{i}")
            _validate_tunnel_inner_ips(tun, tunnel_name)

            merged_tunnels.append(tun)

        conn["tunnels"] = merged_tunnels

    cfg["connections"] = connections
    return cfg


def merge_with_peer_configs(local_cfg: dict, peer_files: list[Path]) -> ResolvedDeploymentPlan:
    # Build normalized peer specs
    peer_specs = [_parse_peer_file(p) for p in peer_files]
    gg = local_cfg.get("gateway_group", {})
    vm_ha_cluster = _build_vm_ha_cluster_record(local_cfg)
    instance_count = int(gg.get("instance_count", 1))
    name = gg.get("name", "nebius-vpn-gw")
    # Prefer gateway_group.region, else top-level region_id, else a sane default
    region = gg.get("region") or (local_cfg.get("region_id") or "eu-north1-a")
    external_ips = gg.get("external_ips", []) or []
    network_id = str(gg.get("network_id") or "").strip() or None
    subnet = gg.get("subnet", {}) or {}
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
        subnet=subnet,
        vm_spec=vm_spec,
        network_id=network_id,
        vm_ha=(
            VMHAProvisioningSpec(
                cluster_id=vm_ha_cluster.cluster_id,
                members=vm_ha_cluster.members,
                generation=vm_ha_cluster.generation,
            )
            if vm_ha_cluster is not None
            else None
        ),
    )

    # Build per-instance configs by filtering tunnels for each instance
    per_instance: list[InstanceResolvedConfig] = []
    flat_peer_tunnels = _normalize_peer_specs(peer_specs)
    # Ensure external_ips is a list to avoid NoneType errors when computing length
    ext_ips = external_ips
    for idx in range(instance_count):
        hostname = f"{name}-{idx}"
        inst_ips = ext_ips[idx] if idx < len(ext_ips) else []
        ip = inst_ips[0] if inst_ips else ""
        connections = local_cfg.get("connections", [])

        # Merge peer-derived values into tunnels that have null/empty fields
        merged_connections = []
        for conn in connections:
            conn_vendor = (conn.get("vendor") or "").lower()
            conn_tunnels = conn.get("tunnels", [])
            # Connection-level hints
            conn_bgp = conn.get("bgp") or {}
            conn_remote_asn = _to_int(conn_bgp.get("remote_asn"))
            inferred_remote_asn: int | None = conn_remote_asn
            conn_remote_prefixes = (
                conn.get("remote_prefixes") or conn_bgp.get("remote_prefixes") or []
            )
            routing_mode = conn.get("routing_mode") or (
                local_cfg.get("defaults", {}).get("routing", {}).get("mode") or "bgp"
            )

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
                tun["inner_local_ip"] = _merge_fields(
                    tun.get("inner_local_ip"), peer_tun.get("inner_local_ip")
                )
                tun["inner_remote_ip"] = _merge_fields(
                    tun.get("inner_remote_ip"), peer_tun.get("inner_remote_ip")
                )
                tun["remote_public_ip"] = _merge_fields(
                    tun.get("remote_public_ip"), peer_tun.get("remote_public_ip")
                )

                # VALIDATION: Ensure inner IPs fall within inner_cidr
                tunnel_name = tun.get("name", f"tunnel-{i}")
                _validate_tunnel_inner_ips(tun, tunnel_name)
                # local_public_ip is derived from YAML indices; preserve if present in peer
                tun["local_public_ip"] = _merge_fields(
                    _resolved_local_public_ip(local_cfg, tun),
                    peer_tun.get("local_public_ip"),
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
                    crypto.get("ike_proposals"),
                    pcrypto.get("ike_proposals"),
                    default_val=[],
                )
                crypto["esp_proposals"] = _merge_fields(
                    crypto.get("esp_proposals"),
                    pcrypto.get("esp_proposals"),
                    default_val=[],
                )
                crypto["ike_lifetime_seconds"] = _merge_fields(
                    crypto.get("ike_lifetime_seconds"),
                    pcrypto.get("ike_lifetime_seconds"),
                )
                crypto["esp_lifetime_seconds"] = _merge_fields(
                    crypto.get("esp_lifetime_seconds"),
                    pcrypto.get("esp_lifetime_seconds"),
                )
                tun["crypto"] = crypto

                merged_tunnels.append(tun)

            # Filter tunnels assigned to this instance
            inst_tunnels_raw = [
                t for t in merged_tunnels if int(t.get("gateway_instance_index", 0)) == idx
            ]
            if inst_tunnels_raw:
                # Inject the actual external IP into each tunnel's local_public_ip if not already set
                inst_tunnels = []
                for t in inst_tunnels_raw:
                    t_copy = dict(t)  # Make a copy to avoid modifying shared tunnel dict
                    lip = t_copy.get("local_public_ip")
                    if lip in (None, ""):
                        local_idx = t_copy.get("local_public_ip_index")
                        if local_idx is None:
                            local_idx = 0
                        if isinstance(local_idx, int) and inst_ips:
                            if 0 <= local_idx < len(inst_ips):
                                t_copy["local_public_ip"] = inst_ips[local_idx]
                            elif ip:
                                t_copy["local_public_ip"] = ip
                        elif ip:
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
        vm_ha_node: VMHANodeRecord | None = None
        if vm_ha_cluster is not None:
            vm_ha_node = next(
                member for member in vm_ha_cluster.members if member.instance_index == idx
            )
            digests = vm_ha_cluster.generation.digests
            per_vm_cfg["vm_ha"] = {
                "cluster_id": vm_ha_cluster.cluster_id,
                "node": {
                    "node_id": vm_ha_node.node_id,
                    "instance_index": vm_ha_node.instance_index,
                    "role": vm_ha_node.role.value,
                },
                "generation": {
                    "generation_id": vm_ha_cluster.generation.generation_id,
                    "digests": {
                        "configuration": digests.configuration,
                        "static_routes": digests.static_routes,
                        "bgp_policy": digests.bgp_policy,
                    },
                    "logical_manifests": {
                        "static_routes_json": (
                            vm_ha_cluster.generation.logical_manifests.static_routes_json
                        ),
                        "bgp_policy_json": (
                            vm_ha_cluster.generation.logical_manifests.bgp_policy_json
                        ),
                    },
                },
                "readiness": {
                    "required_node_ids": list(vm_ha_cluster.readiness.required_node_ids),
                    "generation_id": vm_ha_cluster.readiness.generation_id,
                    "digests": {
                        "configuration": digests.configuration,
                        "static_routes": digests.static_routes,
                        "bgp_policy": digests.bgp_policy,
                    },
                },
            }
        # Convert Enum objects to their values before YAML serialization
        per_vm_cfg_serializable = _enum_to_value(per_vm_cfg)
        serialized = yaml.safe_dump(per_vm_cfg_serializable, sort_keys=False)
        per_instance.append(
            InstanceResolvedConfig(
                instance_index=idx,
                hostname=hostname,
                external_ip=ip,
                config_yaml=serialized,
                vm_ha_node=vm_ha_node,
                vm_ha_generation=(vm_ha_cluster.generation if vm_ha_cluster is not None else None),
                vm_ha_readiness=(vm_ha_cluster.readiness if vm_ha_cluster is not None else None),
            )
        )

    # Determine if we should manage routes: enable if any connection/tunnel uses static routing
    manage_routes = False
    try:
        defaults_mode = (local_cfg.get("defaults", {}).get("routing", {}) or {}).get(
            "mode"
        ) or "bgp"
        for conn in local_cfg.get("connections") or []:
            conn_mode = (conn.get("routing_mode") or defaults_mode) or "bgp"
            if conn_mode == "static":
                manage_routes = True
                break
            for tun in conn.get("tunnels") or []:
                tun_mode = (tun.get("routing_mode") or conn_mode) or defaults_mode
                if tun_mode == "static":
                    manage_routes = True
                    break
            if manage_routes:
                break
    except Exception:
        manage_routes = False

    return ResolvedDeploymentPlan(
        gateway_group=gateway_group,
        gateway=local_cfg.get("gateway", {}),
        per_instance=per_instance,
        manage_routes=manage_routes,
        vm_ha=vm_ha_cluster,
    )
