"""Operator-only public address aliases; operational reads always use real addresses."""

from __future__ import annotations

import ipaddress
import re
from typing import Any

# Explicit non-public ranges make the export policy independent of Python patch
# releases. Special-purpose/documentation space is useful diagnostic evidence.
_V4_PRIVATE = tuple(
    map(
        ipaddress.ip_network,
        (
            "0.0.0.0/8",
            "10.0.0.0/8",
            "100.64.0.0/10",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "172.16.0.0/12",
            "192.0.0.0/24",
            "192.0.2.0/24",
            "192.168.0.0/16",
            "198.18.0.0/15",
            "198.51.100.0/24",
            "203.0.113.0/24",
            "224.0.0.0/3",
        ),
    )
)
_V6_PRIVATE = tuple(
    map(
        ipaddress.ip_network,
        (
            "::/128",
            "::1/128",
            "64:ff9b:1::/48",
            "100::/64",
            "2001::/23",
            "2001:db8::/32",
            "2002::/16",
            "3fff::/20",
            "fc00::/7",
            "fe80::/10",
            "ff00::/8",
        ),
    )
)
_V4_EXCEPTIONS = {ipaddress.ip_address(x) for x in ("192.0.0.9", "192.0.0.10")}
_V6_EXCEPTIONS = tuple(
    map(
        ipaddress.ip_network,
        (
            "2001:1::1/128",
            "2001:1::2/128",
            "2001:3::/32",
            "2001:4:112::/48",
            "2001:20::/28",
            "2001:30::/28",
        ),
    )
)
_ADDRESS = re.compile(
    r"(?:(?<![\w.])|(?<=\.\.\.))(?:[0-9]{1,3}(?:\.[0-9]{1,3}){3}|"
    r"[0-9a-fA-F]*:[0-9a-fA-F:.]*:[0-9a-fA-F:.]*)(?:/[0-9]{1,3})?(?!\w|\.[0-9])"
)


def public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv4Address):
        return address in _V4_EXCEPTIONS or not any(address in net for net in _V4_PRIVATE)
    if address.ipv4_mapped is not None:
        return public_address(address.ipv4_mapped)
    return any(address in net for net in _V6_EXCEPTIONS) or not any(
        address in net for net in _V6_PRIVATE
    )


def address_key(value: str) -> tuple[int, str, str] | None:
    try:
        if "/" in value:
            interface = ipaddress.ip_interface(value)
            if (
                interface.network.prefixlen == 0 and interface.ip.is_unspecified
            ) or not public_address(interface.ip):
                return None
            # Preserve host bits in malformed/noncanonical prefix input too.
            return interface.version, "PREFIX", str(interface)
        address = ipaddress.ip_address(value)
        if public_address(address):
            return address.version, "ADDRESS", str(address)
    except ValueError:
        pass
    return None


def address_spans(text: str) -> list[tuple[int, int, tuple[int, str, str]]]:
    result = []
    for match in _ADDRESS.finditer(text):
        value = match.group()
        # Sentences commonly place a full stop immediately after an address.
        value = value.rstrip(".")
        key = address_key(value)
        if key is None and "/" in value:
            # An invalid prefix must not conceal an otherwise valid address.
            # Keep the bad suffix so the source's mistake remains diagnosable.
            value = value.partition("/")[0]
            key = address_key(value)
        if key is not None:
            result.append((match.start(), match.start() + len(value), key))
    return result


class AddressAliases:
    """Stable within a bundle, never exported or used for network operations."""

    def __init__(self) -> None:
        self.aliases: dict[tuple[int, str, str], str] = {}
        self.replacements = 0

    def alias(self, key: tuple[int, str, str]) -> str:
        if key not in self.aliases:
            version, kind, original = key
            count = 1 + sum(k[:2] == key[:2] for k in self.aliases)
            prefix = "_PREFIX" if kind == "PREFIX" else ""
            suffix = "/" + original.rsplit("/", 1)[1] if prefix else ""
            self.aliases[key] = f"PUBLIC_IPV{version}{prefix}_{count:03d}{suffix}"
        return self.aliases[key]

    def text(self, text: str) -> str:
        spans = address_spans(text)
        self.replacements += len(spans)
        for start, end, key in reversed(spans):
            text = text[:start] + self.alias(key) + text[end:]
        return text

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                clean = self.text(key)
                if clean in result:
                    raise ValueError("Address substitution key collision.")
                result[clean] = self.value(item)
            return result
        return value


def contains_public_address(value: Any) -> bool:
    if isinstance(value, str):
        return bool(address_spans(value))
    if isinstance(value, dict):
        return any(
            contains_public_address(k) or contains_public_address(v) for k, v in value.items()
        )
    if isinstance(value, list):
        return any(contains_public_address(item) for item in value)
    return False
