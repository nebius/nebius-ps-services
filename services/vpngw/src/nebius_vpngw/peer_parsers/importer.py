from __future__ import annotations

import csv
import io
import json
import re
import typing as t
from collections import OrderedDict, defaultdict
from pathlib import Path

import yaml

from .common import (
    coerce_bool,
    coerce_int,
    detect_vendor,
    ensure_list,
    extract_cidrs,
    extract_ip_addresses,
    infer_inner_cidr,
    is_apipa_cidr,
    normalize_key,
    normalize_routing_mode,
    normalize_vendor,
    sanitize_name,
    to_env_var_name,
)

CONNECTION_FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "name": (
        "connectionname",
        "vpnconnectionname",
        "vpnname",
        "peername",
        "gatewayname",
        "routername",
        "name",
    ),
    "vendor": ("vendor", "peervendor", "provider", "cloud", "platform"),
    "routing_mode": (
        "routingmode",
        "routingprotocol",
        "routemode",
        "protocol",
        "mode",
    ),
    "remote_asn": (
        "remoteasn",
        "remotebgpasn",
        "peerasn",
        "bgpasn",
        "neighborasn",
        "cloudrouterasn",
        "vgwasn",
        "asn",
    ),
    "remote_prefixes": (
        "remoteprefixes",
        "remotenetworks",
        "destinationprefixes",
        "destinationcidrs",
        "routeprefixes",
        "routes",
        "networks",
        "subnets",
    ),
}

TUNNEL_FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "name": ("tunnelname", "vpntunnelname", "interfacename", "interface", "name"),
    "remote_public_ip": (
        "remotepublicip",
        "peerpublicip",
        "remotegatewayip",
        "vpngatewayip",
        "peerip",
        "peeraddress",
        "endpointip",
        "remoteip",
        "outsideip",
    ),
    "local_public_ip": (
        "localpublicip",
        "customergatewaypublicip",
        "customergatewayip",
        "localgatewayip",
        "localip",
        "sourceip",
    ),
    "psk": (
        "psk",
        "presharedkey",
        "sharedsecret",
        "sharedkey",
        "ipsecsharedsecret",
        "ipsecpsk",
        "secret",
    ),
    "inner_cidr": (
        "innercidr",
        "insidecidr",
        "tunnelcidr",
        "linkcidr",
        "vticidr",
        "apipacidr",
        "insideipaddresses",
        "insideaddresses",
    ),
    "inner_local_ip": (
        "innerlocalip",
        "localinsideip",
        "customerinsideip",
        "customergatewayinsideaddress",
        "peeripaddress",
        "apipalocal",
        "localtunnelip",
    ),
    "inner_remote_ip": (
        "innerremoteip",
        "remoteinsideip",
        "cloudinsideip",
        "vpngatewayinsideaddress",
        "bgppeerip",
        "ipaddress",
        "apiparemote",
        "remotetunnelip",
    ),
    "gateway_instance_index": ("gatewayinstanceindex", "instanceindex", "vmindex"),
    "local_public_ip_index": ("localpublicipindex", "publicipindex", "nicindex", "interfaceindex"),
    "ha_role": ("harole", "role", "state", "activestandbyrole"),
}

CONNECTION_CONTAINER_KEYWORDS = {"connections"}
TUNNEL_CONTAINER_KEYWORDS = {
    "tunnels",
    "vpntunnels",
    "vpntunnel",
    "ipsectunnels",
    "interfaces",
    "links",
}


def parse_peer_source(path: Path, *, vendor_hint: str | None = None) -> list[dict[str, t.Any]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    source_name = path.stem
    source_format = path.suffix.lower()

    if source_format == ".csv":
        return _parse_csv_document(text, source_name=source_name, vendor_hint=vendor_hint)

    document = _load_structured_document(text, source_format=source_format)
    if document is not None:
        parsed = _parse_structured_document(
            document,
            source_name=source_name,
            vendor_hint=vendor_hint,
            raw_text=text,
        )
        if parsed:
            return parsed

    return [
        parse_text_document(
            text,
            vendor_hint=vendor_hint,
            source_name=source_name,
        )
    ]


def parse_text_document(
    text: str,
    *,
    vendor_hint: str | None = None,
    source_name: str = "peer-config",
) -> dict[str, t.Any]:
    vendor = normalize_vendor(vendor_hint or detect_vendor(text))
    kv_map = _extract_text_key_values(text)

    connection_name = _extract_connection_name_from_text(text)
    remote_asn = _extract_text_remote_asn(text)
    routing_mode = _extract_routing_mode_from_text(text)
    remote_prefixes = _extract_text_remote_prefixes(text)

    tunnel_names = _extract_pattern_values(
        text,
        (
            r"(?im)^\s*(?:vpn\s+tunnel|tunnel)\s+name\s*[:=]\s*['\"]?([^'\"\n#]+)",
            r"(?im)^\s*interface\s+(Tunnel[^\s#]+)\b",
        ),
    )
    psks = _extract_pattern_values(
        text,
        (
            r"(?im)^\s*(?:pre[\s_-]*shared[\s_-]*key|shared[\s_-]*secret|shared[\s_-]*key|psk)\s*[:=]\s*['\"]?([^'\"\n#]+)",
            r"(?im)^\s*shared\s+key\s+['\"]([^'\"]+)['\"]",
            r"(?i)crypto\s+isakmp\s+key\s+([^\s]+)\s+address\s+(?:\d{1,3}\.){3}\d{1,3}",
        ),
    )
    inner_cidrs = [
        cidr
        for cidr in _extract_pattern_values(text, (r"\b(169\.254\.\d+\.\d+/30)\b",))
        if is_apipa_cidr(cidr)
    ]
    local_ips = _extract_pattern_values(
        text,
        (
            r"(?im)^\s*(?:customer\s+gateway\s+inside\s+address|customer\s+apipa|peerIpAddress|local\s+inside\s+ip|local\s+ip)\s*[:=]?\s*(169\.254\.\d+\.\d+)",
            r"(?i)\bip\s+address\s+(169\.254\.\d+\.\d+)\s+255\.255\.255\.252\b",
        ),
    )
    remote_ips = _extract_pattern_values(
        text,
        (
            r"(?im)^\s*(?:virtual\s+private\s+gateway\s+inside\s+address|azure\s+apipa|ipAddress|remote\s+inside\s+ip|remote\s+ip)\s*[:=]?\s*(169\.254\.\d+\.\d+)",
        ),
    )
    local_public_ips = _extract_pattern_values(
        text,
        (
            r"(?im)^\s*(?:customer\s+gateway.*?(?:outside|public)\s+ip(?:\s+address)?|customer\s+gateway\s+ip(?:\s+address)?|customer\s+public\s+ip\s+address|local\s+gateway\s+ip|local\s+public\s+ip)\s*[:=]?\s*['\"]?((?:\d{1,3}\.){3}\d{1,3})",
        ),
    )
    remote_public_ips = _extract_pattern_values(
        text,
        (
            r"(?im)^\s*(?:virtual\s+private\s+gateway.*?(?:outside|public)\s+ip(?:\s+address)?|google\s+public\s+ip\s+address|azure\s+vpn\s+gateway\s+ip\s+address|peer\s+public\s+ip|remote\s+public\s+ip|remote\s+gateway\s+ip)\s*[:=]?\s*['\"]?((?:\d{1,3}\.){3}\d{1,3})",
            r"(?i)crypto\s+isakmp\s+key\s+[^\s]+\s+address\s+((?:\d{1,3}\.){3}\d{1,3})",
        ),
    )
    ike_props = re.findall(
        r"(?i)ike.*?encryption\s*[:=]\s*([A-Za-z0-9\-]+).*?integrity\s*[:=]\s*([A-Za-z0-9\-]+).*?(?:dh\s*group|group)\s*[:=]\s*(\d+)",
        text,
        re.S,
    )
    esp_props = re.findall(
        r"(?i)(?:esp|ipsec).*?encryption\s*[:=]\s*([A-Za-z0-9\-]+).*?integrity\s*[:=]\s*([A-Za-z0-9\-]+)",
        text,
        re.S,
    )

    # Prefer keyword-style text values when available.
    connection_name = connection_name or _coerce_text_mapping_value(
        kv_map, CONNECTION_FIELD_KEYWORDS["name"]
    )
    routing_mode = routing_mode or normalize_routing_mode(
        _coerce_text_mapping_value(kv_map, CONNECTION_FIELD_KEYWORDS["routing_mode"])
    )
    remote_asn = remote_asn or coerce_int(
        _coerce_text_mapping_value(kv_map, CONNECTION_FIELD_KEYWORDS["remote_asn"])
    )
    if not remote_prefixes:
        remote_prefixes = _coerce_cidr_list_from_mapping(
            kv_map, CONNECTION_FIELD_KEYWORDS["remote_prefixes"]
        )

    tunnel_count = max(
        len(psks),
        len(inner_cidrs),
        len(local_ips),
        len(remote_ips),
        len(remote_public_ips),
        len(tunnel_names),
        1,
    )
    tunnels: list[dict[str, t.Any]] = []
    for index in range(tunnel_count):
        inner_cidr = inner_cidrs[index] if index < len(inner_cidrs) else None
        inner_local_ip = local_ips[index] if index < len(local_ips) else None
        inner_remote_ip = remote_ips[index] if index < len(remote_ips) else None
        if inner_cidr is None:
            inner_cidr = infer_inner_cidr(inner_local_ip, inner_remote_ip)

        crypto: dict[str, t.Any] = {}
        if index < len(ike_props):
            enc, integ, dh = ike_props[index]
            crypto["ike_proposals"] = [f"{enc}-{integ}-modp{dh}"]
        if index < len(esp_props):
            enc, integ = esp_props[index]
            crypto["esp_proposals"] = [f"{enc}-{integ}"]

        tunnel = _clean_dict(
            {
                "name": tunnel_names[index] if index < len(tunnel_names) else None,
                "psk": psks[index] if index < len(psks) else None,
                "inner_cidr": inner_cidr,
                "inner_local_ip": inner_local_ip,
                "inner_remote_ip": inner_remote_ip,
                "local_public_ip": local_public_ips[index]
                if index < len(local_public_ips)
                else None,
                "remote_public_ip": remote_public_ips[index]
                if index < len(remote_public_ips)
                else None,
                "crypto": crypto or None,
            }
        )
        if tunnel:
            tunnels.append(tunnel)

    spec = _clean_dict(
        {
            "name": sanitize_name(
                connection_name or source_name,
                fallback=sanitize_name(source_name, fallback="peer-vpn"),
            ),
            "vendor": vendor,
            "routing_mode": routing_mode,
            "remote_asn": remote_asn,
            "remote_prefixes": remote_prefixes or None,
            "tunnels": tunnels,
        }
    )
    spec.setdefault("tunnels", tunnels)
    return spec


def default_psk_placeholder(connection_name: str, tunnel_index: int) -> str:
    env_name = to_env_var_name(connection_name, f"tunnel_{tunnel_index + 1}", "psk")
    return f"${{{env_name or f'PEER_TUNNEL_{tunnel_index + 1}_PSK'}}}"


def default_remote_prefixes() -> list[str]:
    return ["192.0.2.0/24"]


def default_tunnel_values(connection_index: int, tunnel_index: int) -> dict[str, t.Any]:
    base_octet = 10 + (connection_index * 16) + tunnel_index
    return {
        "gateway_instance_index": 0,
        "local_public_ip_index": 0,
        "ha_role": "active" if tunnel_index == 0 else "passive",
        "remote_public_ip": f"203.0.113.{10 + tunnel_index}",
        "inner_cidr": f"169.254.{base_octet}.0/30",
        "inner_local_ip": f"169.254.{base_octet}.1",
        "inner_remote_ip": f"169.254.{base_octet}.2",
    }


def build_connection_config(spec: dict[str, t.Any], *, connection_index: int) -> dict[str, t.Any]:
    vendor = normalize_vendor(spec.get("vendor"))
    fallback_name = f"{vendor}-vpn-{connection_index + 1}" if connection_index else f"{vendor}-vpn"
    connection_name = sanitize_name(spec.get("name"), fallback=fallback_name)

    remote_asn = coerce_int(spec.get("remote_asn"))
    remote_prefixes = _normalize_cidr_list(spec.get("remote_prefixes"))
    routing_mode = normalize_routing_mode(spec.get("routing_mode"))
    if routing_mode is None:
        if remote_prefixes:
            routing_mode = "static"
        elif remote_asn is not None:
            routing_mode = "bgp"
        elif vendor in {"cisco", "generic"}:
            routing_mode = "static"
        else:
            routing_mode = "bgp"

    if routing_mode == "bgp" and remote_asn is None:
        remote_asn = 65014

    if routing_mode == "static" and not remote_prefixes:
        remote_prefixes = default_remote_prefixes()

    parsed_tunnels = ensure_list(spec.get("tunnels"))
    if not parsed_tunnels:
        parsed_tunnels = [{}]

    tunnels: list[dict[str, t.Any]] = []
    for tunnel_index, tunnel_spec in enumerate(parsed_tunnels):
        defaults = default_tunnel_values(connection_index, tunnel_index)
        tunnel_name_fallback = f"{connection_name}-tunnel-{tunnel_index + 1}"
        tunnel_name = sanitize_name(tunnel_spec.get("name"), fallback=tunnel_name_fallback)
        built = {
            **defaults,
            "name": tunnel_name,
            "gateway_instance_index": coerce_int(tunnel_spec.get("gateway_instance_index")) or 0,
            "local_public_ip_index": coerce_int(tunnel_spec.get("local_public_ip_index")) or 0,
            "ha_role": _normalize_ha_role(tunnel_spec.get("ha_role"), tunnel_index=tunnel_index),
            "remote_public_ip": tunnel_spec.get("remote_public_ip") or defaults["remote_public_ip"],
            "psk": str(
                tunnel_spec.get("psk") or default_psk_placeholder(connection_name, tunnel_index)
            ),
            "inner_cidr": tunnel_spec.get("inner_cidr") or defaults["inner_cidr"],
            "inner_local_ip": tunnel_spec.get("inner_local_ip") or defaults["inner_local_ip"],
            "inner_remote_ip": tunnel_spec.get("inner_remote_ip") or defaults["inner_remote_ip"],
            "crypto": tunnel_spec.get("crypto"),
            "static_routes": tunnel_spec.get("static_routes"),
        }
        if not built.get("inner_cidr"):
            built["inner_cidr"] = (
                infer_inner_cidr(built.get("inner_local_ip"), built.get("inner_remote_ip"))
                or defaults["inner_cidr"]
            )
        tunnels.append(_clean_dict(built))

    connection = {
        "name": connection_name,
        "vendor": vendor,
        "routing_mode": routing_mode,
        "bgp": {
            "enabled": routing_mode == "bgp",
            "remote_asn": remote_asn if routing_mode == "bgp" else None,
            "advertise_local_prefixes": routing_mode == "bgp",
        },
        "tunnels": tunnels,
    }
    if remote_prefixes:
        connection["remote_prefixes"] = remote_prefixes
    return connection


def merge_connection_specs(specs: list[dict[str, t.Any]]) -> list[dict[str, t.Any]]:
    merged: OrderedDict[tuple[str, str], dict[str, t.Any]] = OrderedDict()
    for spec in specs:
        vendor = normalize_vendor(spec.get("vendor"))
        name = sanitize_name(spec.get("name"), fallback=f"{vendor}-vpn")
        key = (vendor, name)
        current = merged.get(key)
        if current is None:
            merged[key] = _clean_dict(
                {
                    "name": name,
                    "vendor": vendor,
                    "routing_mode": spec.get("routing_mode"),
                    "remote_asn": coerce_int(spec.get("remote_asn")),
                    "remote_prefixes": _normalize_cidr_list(spec.get("remote_prefixes")) or None,
                    "tunnels": [_clean_dict(tunnel) for tunnel in ensure_list(spec.get("tunnels"))],
                }
            )
            continue

        current["routing_mode"] = current.get("routing_mode") or spec.get("routing_mode")
        current["remote_asn"] = current.get("remote_asn") or coerce_int(spec.get("remote_asn"))
        current_prefixes = _normalize_cidr_list(current.get("remote_prefixes"))
        new_prefixes = _normalize_cidr_list(spec.get("remote_prefixes"))
        if new_prefixes:
            current["remote_prefixes"] = list(dict.fromkeys(current_prefixes + new_prefixes))
        current["tunnels"] = _merge_tunnels(
            ensure_list(current.get("tunnels")),
            ensure_list(spec.get("tunnels")),
        )

    return list(merged.values())


def _load_structured_document(text: str, *, source_format: str) -> t.Any | None:
    try:
        if source_format == ".json":
            return json.loads(text)
        if source_format in {".yaml", ".yml"}:
            return yaml.safe_load(text)
        if source_format in {".txt", ".cfg", ".conf"}:
            stripped = text.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                return json.loads(text)
            if ":" in text and any(
                line.lstrip().startswith(("-", "{")) for line in text.splitlines()
            ):
                return yaml.safe_load(text)
    except Exception:
        return None
    return None


def _parse_structured_document(
    document: t.Any,
    *,
    source_name: str,
    vendor_hint: str | None,
    raw_text: str,
) -> list[dict[str, t.Any]]:
    if document is None:
        return []

    if isinstance(document, dict):
        connection_items = _find_container_items(
            document, CONTAINER_KEYS=CONNECTION_CONTAINER_KEYWORDS
        )
        if connection_items:
            return [
                _normalize_connection_mapping(
                    item,
                    root=document,
                    source_name=source_name,
                    vendor_hint=vendor_hint,
                    raw_text=raw_text,
                )
                for item in connection_items
                if isinstance(item, dict)
            ]
        return [
            _normalize_connection_mapping(
                document,
                root=document,
                source_name=source_name,
                vendor_hint=vendor_hint,
                raw_text=raw_text,
            )
        ]

    if isinstance(document, list) and all(isinstance(item, dict) for item in document):
        if any(_looks_like_connection_mapping(item) for item in document):
            return [
                _normalize_connection_mapping(
                    item,
                    root=item,
                    source_name=source_name,
                    vendor_hint=vendor_hint,
                    raw_text=yaml.safe_dump(item, sort_keys=False),
                )
                for item in document
            ]
        return _group_row_mappings_into_connections(
            t.cast(list[dict[str, t.Any]], document),
            source_name=source_name,
            vendor_hint=vendor_hint,
            raw_text=raw_text,
        )

    return []


def _parse_csv_document(
    text: str,
    *,
    source_name: str,
    vendor_hint: str | None,
) -> list[dict[str, t.Any]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = [
        {str(key): value for key, value in row.items() if key}
        for row in reader
        if any(value not in (None, "") for value in row.values())
    ]
    return _group_row_mappings_into_connections(
        rows,
        source_name=source_name,
        vendor_hint=vendor_hint,
        raw_text=text,
    )


def _group_row_mappings_into_connections(
    rows: list[dict[str, t.Any]],
    *,
    source_name: str,
    vendor_hint: str | None,
    raw_text: str,
) -> list[dict[str, t.Any]]:
    grouped_rows: defaultdict[tuple[str, str], list[dict[str, t.Any]]] = defaultdict(list)
    group_meta: dict[tuple[str, str], dict[str, t.Any]] = {}

    for row in rows:
        vendor = normalize_vendor(
            _find_value_by_alias(row, CONNECTION_FIELD_KEYWORDS["vendor"]) or vendor_hint
        )
        name_value = _find_value_by_alias(row, CONNECTION_FIELD_KEYWORDS["name"]) or source_name
        name = sanitize_name(
            name_value, fallback=sanitize_name(source_name, fallback=f"{vendor}-vpn")
        )
        key = (vendor, name)
        grouped_rows[key].append(row)
        group_meta.setdefault(key, row)

    parsed_specs: list[dict[str, t.Any]] = []
    for (vendor, name), group in grouped_rows.items():
        base_row = group_meta[(vendor, name)]
        remote_prefixes = _normalize_cidr_list(
            _find_value_by_alias(base_row, CONNECTION_FIELD_KEYWORDS["remote_prefixes"])
        )
        remote_asn = coerce_int(
            _find_value_by_alias(base_row, CONNECTION_FIELD_KEYWORDS["remote_asn"])
        )
        routing_mode = normalize_routing_mode(
            _find_value_by_alias(base_row, CONNECTION_FIELD_KEYWORDS["routing_mode"])
        )
        tunnels = []
        for row_index, row in enumerate(group):
            tunnel = _normalize_tunnel_mapping(
                row,
                root=row,
                source_name=f"{name}-tunnel-{row_index + 1}",
                raw_text=yaml.safe_dump(row, sort_keys=False),
                connection_name=name,
                tunnel_index=row_index,
            )
            if tunnel:
                tunnels.append(tunnel)
        parsed_specs.append(
            _clean_dict(
                {
                    "name": name,
                    "vendor": vendor,
                    "routing_mode": routing_mode,
                    "remote_asn": remote_asn,
                    "remote_prefixes": remote_prefixes or None,
                    "tunnels": tunnels,
                }
            )
        )
    return parsed_specs or [
        parse_text_document(raw_text, vendor_hint=vendor_hint, source_name=source_name)
    ]


def _normalize_connection_mapping(
    mapping: dict[str, t.Any],
    *,
    root: dict[str, t.Any],
    source_name: str,
    vendor_hint: str | None,
    raw_text: str,
) -> dict[str, t.Any]:
    vendor = normalize_vendor(
        _find_value_by_alias(mapping, CONNECTION_FIELD_KEYWORDS["vendor"])
        or _find_value_by_alias(root, CONNECTION_FIELD_KEYWORDS["vendor"])
        or vendor_hint
        or detect_vendor(raw_text)
    )
    connection_name = sanitize_name(
        _find_value_by_alias(mapping, CONNECTION_FIELD_KEYWORDS["name"])
        or _find_value_by_alias(root, CONNECTION_FIELD_KEYWORDS["name"])
        or source_name,
        fallback=sanitize_name(source_name, fallback=f"{vendor}-vpn"),
    )
    routing_mode = normalize_routing_mode(
        _find_value_by_alias(mapping, CONNECTION_FIELD_KEYWORDS["routing_mode"])
        or _find_value_by_alias(root, CONNECTION_FIELD_KEYWORDS["routing_mode"])
    )
    remote_asn = coerce_int(
        _find_value_by_alias(mapping, CONNECTION_FIELD_KEYWORDS["remote_asn"])
        or _find_value_by_alias(root, CONNECTION_FIELD_KEYWORDS["remote_asn"])
    )
    remote_prefixes = _normalize_cidr_list(
        _find_value_by_alias(mapping, CONNECTION_FIELD_KEYWORDS["remote_prefixes"])
        or _find_value_by_alias(root, CONNECTION_FIELD_KEYWORDS["remote_prefixes"])
    )

    tunnel_mappings = _find_container_items(mapping, CONTAINER_KEYS=TUNNEL_CONTAINER_KEYWORDS)
    if not tunnel_mappings and _looks_like_tunnel_mapping(mapping):
        tunnel_mappings = [mapping]

    tunnels = []
    for tunnel_index, tunnel_mapping in enumerate(tunnel_mappings):
        if not isinstance(tunnel_mapping, dict):
            continue
        tunnel = _normalize_tunnel_mapping(
            tunnel_mapping,
            root=mapping,
            source_name=f"{connection_name}-tunnel-{tunnel_index + 1}",
            raw_text=yaml.safe_dump(tunnel_mapping, sort_keys=False),
            connection_name=connection_name,
            tunnel_index=tunnel_index,
        )
        if tunnel:
            tunnels.append(tunnel)

    if not tunnels and _looks_like_tunnel_mapping(mapping):
        fallback_tunnel = _normalize_tunnel_mapping(
            mapping,
            root=mapping,
            source_name=f"{connection_name}-tunnel-1",
            raw_text=raw_text,
            connection_name=connection_name,
            tunnel_index=0,
        )
        if fallback_tunnel:
            tunnels.append(fallback_tunnel)

    return _clean_dict(
        {
            "name": connection_name,
            "vendor": vendor,
            "routing_mode": routing_mode,
            "remote_asn": remote_asn,
            "remote_prefixes": remote_prefixes or None,
            "tunnels": tunnels,
        }
    )


def _normalize_tunnel_mapping(
    mapping: dict[str, t.Any],
    *,
    root: dict[str, t.Any],
    source_name: str,
    raw_text: str,
    connection_name: str,
    tunnel_index: int,
) -> dict[str, t.Any]:
    name = sanitize_name(
        _find_value_by_alias(mapping, TUNNEL_FIELD_KEYWORDS["name"]) or source_name,
        fallback=f"{connection_name}-tunnel-{tunnel_index + 1}",
    )
    tunnel = _clean_dict(
        {
            "name": name,
            "remote_public_ip": _extract_single_ip(
                _find_value_by_alias(mapping, TUNNEL_FIELD_KEYWORDS["remote_public_ip"])
            ),
            "local_public_ip": _extract_single_ip(
                _find_value_by_alias(mapping, TUNNEL_FIELD_KEYWORDS["local_public_ip"])
            ),
            "psk": _coerce_string(_find_value_by_alias(mapping, TUNNEL_FIELD_KEYWORDS["psk"])),
            "inner_cidr": _extract_apipa_cidr(
                _find_value_by_alias(mapping, TUNNEL_FIELD_KEYWORDS["inner_cidr"])
            ),
            "inner_local_ip": _extract_single_ip(
                _find_value_by_alias(mapping, TUNNEL_FIELD_KEYWORDS["inner_local_ip"])
            ),
            "inner_remote_ip": _extract_single_ip(
                _find_value_by_alias(mapping, TUNNEL_FIELD_KEYWORDS["inner_remote_ip"])
            ),
            "gateway_instance_index": coerce_int(
                _find_value_by_alias(mapping, TUNNEL_FIELD_KEYWORDS["gateway_instance_index"])
            ),
            "local_public_ip_index": coerce_int(
                _find_value_by_alias(mapping, TUNNEL_FIELD_KEYWORDS["local_public_ip_index"])
            ),
            "ha_role": _normalize_ha_role(
                _find_value_by_alias(mapping, TUNNEL_FIELD_KEYWORDS["ha_role"]),
                tunnel_index=tunnel_index,
            ),
        }
    )
    if not tunnel.get("inner_cidr"):
        tunnel["inner_cidr"] = infer_inner_cidr(
            t.cast(str | None, tunnel.get("inner_local_ip")),
            t.cast(str | None, tunnel.get("inner_remote_ip")),
        )
    if not tunnel.get("psk") and raw_text:
        parsed = parse_text_document(raw_text, source_name=source_name)
        if parsed.get("tunnels"):
            for key, value in parsed["tunnels"][0].items():
                tunnel.setdefault(key, value)
    return _clean_dict(tunnel)


def _find_container_items(
    obj: t.Any,
    *,
    CONTAINER_KEYS: set[str],
) -> list[t.Any]:
    items: list[t.Any] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if normalize_key(str(key)) in CONTAINER_KEYS and isinstance(value, list):
                items.extend(value)
            elif isinstance(value, (dict, list)):
                items.extend(_find_container_items(value, CONTAINER_KEYS=CONTAINER_KEYS))
    elif isinstance(obj, list):
        for item in obj:
            items.extend(_find_container_items(item, CONTAINER_KEYS=CONTAINER_KEYS))
    return items


def _looks_like_connection_mapping(mapping: dict[str, t.Any]) -> bool:
    if any(normalize_key(str(key)) in CONNECTION_CONTAINER_KEYWORDS for key in mapping):
        return True
    return any(
        _find_value_by_alias(mapping, CONNECTION_FIELD_KEYWORDS[field]) is not None
        for field in ("vendor", "routing_mode", "remote_asn", "remote_prefixes")
    )


def _looks_like_tunnel_mapping(mapping: dict[str, t.Any]) -> bool:
    return any(
        _find_value_by_alias(mapping, aliases) is not None
        for aliases in TUNNEL_FIELD_KEYWORDS.values()
    )


def _find_value_by_alias(mapping: dict[str, t.Any], aliases: tuple[str, ...]) -> t.Any:
    for alias in aliases:
        for key, value in mapping.items():
            normalized = normalize_key(str(key))
            if normalized == alias or normalized.endswith(alias):
                return value
            if isinstance(value, dict):
                nested = _find_value_by_alias(value, aliases)
                if nested is not None:
                    return nested

    flattened = list(_flatten_mapping(mapping))

    for alias in aliases:
        for path, value in flattened:
            if not path:
                continue
            if _path_matches_alias(path, alias):
                return value
    return None


def _flatten_mapping(
    obj: t.Any, path: tuple[str, ...] = ()
) -> t.Iterable[tuple[tuple[str, ...], t.Any]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_path = path + (normalize_key(str(key)),)
            if isinstance(value, (dict, list)):
                yield from _flatten_mapping(value, key_path)
            else:
                yield key_path, value
        return
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                yield from _flatten_mapping(item, path)
            else:
                yield path, item
        return
    yield path, obj


def _path_matches_alias(path: tuple[str, ...], alias: str) -> bool:
    if any(segment == alias for segment in path):
        return True
    joined = "".join(path)
    return joined.endswith(alias)


def _extract_text_key_values(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            if normalize_key(key):
                mapping.setdefault(normalize_key(key), value.strip().strip("'\""))
                continue
        if "=" in line:
            key, value = line.split("=", 1)
            if normalize_key(key):
                mapping.setdefault(normalize_key(key), value.strip().strip("'\""))
                continue
        shared_key_match = re.match(r"(?i)^shared\s+key\s+['\"]([^'\"]+)['\"]$", line)
        if shared_key_match:
            mapping.setdefault("sharedkey", shared_key_match.group(1).strip())
    return mapping


def _coerce_text_mapping_value(mapping: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        for key, value in mapping.items():
            if key == alias or key.endswith(alias):
                return value
    return None


def _coerce_cidr_list_from_mapping(mapping: dict[str, str], aliases: tuple[str, ...]) -> list[str]:
    value = _coerce_text_mapping_value(mapping, aliases)
    return _normalize_cidr_list(value)


def _extract_pattern_values(text: str, patterns: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = match if isinstance(match, str) else match[-1]
            normalized = str(value).strip().strip("'\"")
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            values.append(normalized)
    return values


def _extract_connection_name_from_text(text: str) -> str | None:
    names = _extract_pattern_values(
        text,
        (
            r"(?im)^\s*(?:vpn\s+connection|connection|gateway|router)\s+name\s*[:=]\s*['\"]?([^'\"\n#]+)",
            r"(?im)^\s*name\s*[:=]\s*['\"]?([^'\"\n#]+)",
        ),
    )
    return names[0] if names else None


def _extract_text_remote_asn(text: str) -> int | None:
    patterns = (
        r"(?i)\b(?:remote|peer|neighbor|cloud\s*router|vgw|azure)\s*asn\D+(\d+)",
        r"(?i)\bremote-as\s+(\d+)",
        r"(?s)\bbgp:\s*.*?\basn:\s*(\d+)",
    )
    matches = _extract_pattern_values(text, patterns)
    return coerce_int(matches[0]) if matches else None


def _extract_routing_mode_from_text(text: str) -> str | None:
    lowered = text.lower()
    if "routing_mode" in lowered or "routing mode" in lowered:
        modes = _extract_pattern_values(
            text,
            (r"(?i)routing(?:[_\s-]*)mode\s*[:=]\s*['\"]?([a-z-]+)",),
        )
        if modes:
            return normalize_routing_mode(modes[0])
    if re.search(r"(?i)\b(remote[- ]as|asn|bgp)\b", text):
        return "bgp"
    if re.search(r"(?i)\b(static\s+route|remote\s+prefix|destination\s+network)\b", text):
        return "static"
    return None


def _extract_text_remote_prefixes(text: str) -> list[str]:
    prefixes: list[str] = []
    for line in text.splitlines():
        if not re.search(r"(?i)\b(remote|destination|route|network|prefix)\b", line):
            continue
        for cidr in extract_cidrs(line):
            if not is_apipa_cidr(cidr) and cidr not in prefixes:
                prefixes.append(cidr)
    return prefixes


def _normalize_cidr_list(value: t.Any) -> list[str]:
    cidrs = [cidr for cidr in extract_cidrs(value) if not is_apipa_cidr(cidr)]
    return list(dict.fromkeys(cidrs))


def _extract_apipa_cidr(value: t.Any) -> str | None:
    for cidr in extract_cidrs(value):
        if is_apipa_cidr(cidr):
            return cidr
    return None


def _extract_single_ip(value: t.Any) -> str | None:
    ips = extract_ip_addresses(value)
    return ips[0] if ips else None


def _coerce_string(value: t.Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_ha_role(value: t.Any, *, tunnel_index: int) -> str:
    if value is None:
        return "active" if tunnel_index == 0 else "passive"
    normalized = normalize_key(str(value))
    if normalized in {"active", "primary", "up"}:
        return "active"
    if normalized in {"disable", "disabled", "down"}:
        return "disable"
    if normalized in {"passive", "secondary", "standby"}:
        return "passive"
    bool_value = coerce_bool(value)
    if bool_value is not None:
        return "active" if bool_value else "passive"
    return "active" if tunnel_index == 0 else "passive"


def _merge_tunnels(
    existing: list[dict[str, t.Any]], new: list[dict[str, t.Any]]
) -> list[dict[str, t.Any]]:
    merged: list[dict[str, t.Any]] = [dict(item) for item in existing]
    fingerprints = {_tunnel_fingerprint(item): index for index, item in enumerate(merged)}
    for tunnel in new:
        fingerprint = _tunnel_fingerprint(tunnel)
        if fingerprint not in fingerprints:
            fingerprints[fingerprint] = len(merged)
            merged.append(dict(tunnel))
            continue
        current = merged[fingerprints[fingerprint]]
        for key, value in tunnel.items():
            if current.get(key) in (None, "", []):
                current[key] = value
    return merged


def _tunnel_fingerprint(tunnel: dict[str, t.Any]) -> tuple[t.Any, ...]:
    return (
        sanitize_name(tunnel.get("name"), fallback=""),
        tunnel.get("remote_public_ip"),
        tunnel.get("inner_cidr"),
        tunnel.get("inner_local_ip"),
        tunnel.get("inner_remote_ip"),
    )


def _clean_dict(value: dict[str, t.Any]) -> dict[str, t.Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [])}
