from __future__ import annotations

import ipaddress
import re
import typing as t

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_VENDOR_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gcp", ("google cloud", "cloud router", "ha vpn", "google")),
    ("aws", ("amazon web services", "aws", "customer gateway", "virtual private gateway")),
    ("azure", ("virtual network gateway", "azure", "microsoft")),
    ("cisco", ("crypto isakmp", "cisco", "ios", "asa")),
)

_VENDOR_ALIASES = {
    "amazon": "aws",
    "amazonwebservices": "aws",
    "aws": "aws",
    "azure": "azure",
    "cisco": "cisco",
    "gcp": "gcp",
    "generic": "generic",
    "google": "gcp",
    "googlecloud": "gcp",
    "ios": "cisco",
    "microsoftazure": "azure",
    "onprem": "generic",
}

_ROUTING_MODE_ALIASES = {
    "bgp": "bgp",
    "dynamic": "bgp",
    "dynamicrouting": "bgp",
    "routebasedbgp": "bgp",
    "static": "static",
    "staticrouting": "static",
    "policybased": "static",
}

_TRUE_VALUES = {"1", "enabled", "on", "true", "yes"}
_FALSE_VALUES = {"0", "disabled", "false", "no", "off"}


def normalize_key(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value.strip().lower())


def detect_vendor(text: str) -> str:
    lowered = text.lower()
    best_vendor = "generic"
    best_score = 0
    for vendor, hints in _VENDOR_HINTS:
        score = sum(1 for hint in hints if hint in lowered)
        if score > best_score:
            best_vendor = vendor
            best_score = score
    if best_score:
        return best_vendor
    return "generic"


def normalize_vendor(value: t.Any, *, fallback: str = "generic") -> str:
    if value is None:
        return fallback
    key = normalize_key(str(value))
    return _VENDOR_ALIASES.get(key, fallback)


def normalize_routing_mode(value: t.Any) -> str | None:
    if value is None:
        return None
    key = normalize_key(str(value))
    return _ROUTING_MODE_ALIASES.get(key)


def coerce_int(value: t.Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def coerce_bool(value: t.Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    normalized = normalize_key(str(value))
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def ensure_list(value: t.Any) -> list[t.Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def extract_cidrs(value: t.Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        cidrs: list[str] = []
        for item in value:
            cidrs.extend(extract_cidrs(item))
        return cidrs
    text = str(value)
    found: list[str] = []
    for token in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b", text):
        try:
            found.append(str(ipaddress.ip_network(token, strict=False)))
        except ValueError:
            continue
    return found


def extract_ip_addresses(value: t.Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        ips: list[str] = []
        for item in value:
            ips.extend(extract_ip_addresses(item))
        return ips
    text = str(value)
    found: list[str] = []
    for token in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        try:
            ipaddress.ip_address(token)
        except ValueError:
            continue
        found.append(token)
    return found


def is_apipa_cidr(value: str) -> bool:
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return isinstance(network, ipaddress.IPv4Network) and network.subnet_of(
        ipaddress.ip_network("169.254.0.0/16")
    )


def infer_inner_cidr(local_ip: str | None, remote_ip: str | None) -> str | None:
    if not local_ip:
        return None
    try:
        network = ipaddress.ip_network(f"{local_ip}/30", strict=False)
    except ValueError:
        return None
    if remote_ip is None:
        return str(network)
    try:
        remote = ipaddress.ip_address(remote_ip)
    except ValueError:
        return None
    if remote in network:
        return str(network)
    return None


def sanitize_name(value: t.Any, *, fallback: str) -> str:
    if value is None:
        return fallback
    raw = str(value).strip().lower()
    sanitized = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", raw)).strip("-")
    if not sanitized:
        return fallback
    return sanitized[:64].rstrip("-") or fallback


def to_env_var_name(*parts: str) -> str:
    pieces = [normalize_key(part).upper() for part in parts if normalize_key(part)]
    return "_".join(piece for piece in pieces if piece)
