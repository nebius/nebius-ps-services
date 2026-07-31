#!/usr/bin/env python3
"""Conservative redaction shared by troubleshoot evidence helpers."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from typing import Any

SENSITIVE_SINGLE_PARTS = {
    "authorization",
    "certificate",
    "cookie",
    "credential",
    "endpoint",
    "host",
    "hostname",
    "password",
    "passwd",
    "privatekey",
    "pwd",
    "secret",
    "session",
    "token",
    "uri",
    "url",
}
SENSITIVE_COMPOSITES = {
    ("access", "key"),
    ("api", "key"),
    ("client", "secret"),
    ("connection", "string"),
    ("private", "key"),
}
STRONG_SUFFIXES = ("credential", "password", "passwd", "privatekey", "secret", "token")
ACRONYM_BOUNDARY_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
IDENTIFIER_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")
JSON_ASSIGNMENT_RE = re.compile(
    r"""(?P<quote>["'])(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)(?P=quote)(?P<separator>\s*:\s*)(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^,}\s]+)"""
)
ASSIGNMENT_RE = re.compile(
    r"""(?i)(?<![a-z0-9_.-])(?P<key>[a-z_][a-z0-9_.-]*)\s*[:=]\s*(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;]+)"""
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
URL_CREDENTIAL_RE = re.compile(r"(https?://)[^/@\s:]+:[^/@\s]+@")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
IPV4_CANDIDATE_RE = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])"
)
PRIVATE_HOST_RE = re.compile(
    r"(?i)\b(?:[a-z0-9-]+\.)*(?:internal|corp|private|local)(?:\.[a-z0-9-]+)*\b"
)
CLOUD_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
PEM_BLOCK_RE = re.compile(
    r"-----BEGIN [^-\r\n]+-----.*?-----END [^-\r\n]+-----", re.DOTALL
)
LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_+/=-]{32,}\b")


def endpoint_placeholder(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"[ENDPOINT:{digest}]"


def identifier_parts(name: str) -> tuple[str, ...]:
    separated = ACRONYM_BOUNDARY_RE.sub(r"\1_\2", name)
    separated = CAMEL_BOUNDARY_RE.sub(r"\1_\2", separated).lower()
    return tuple(part for part in IDENTIFIER_SEPARATOR_RE.split(separated) if part)


def is_sensitive_name(name: str) -> bool:
    parts = identifier_parts(name.removeprefix("--"))
    if not parts:
        return False
    if any(part in SENSITIVE_SINGLE_PARTS for part in parts):
        return True
    if any(pair in SENSITIVE_COMPOSITES for pair in zip(parts, parts[1:])):
        return True
    compact = "".join(parts)
    return any(compact.endswith(suffix) for suffix in STRONG_SUFFIXES)


def redact_json_assignment(match: re.Match[str]) -> str:
    if not is_sensitive_name(match.group("key")):
        return match.group(0)
    quote = match.group("quote")
    return (
        f"{quote}{match.group('key')}{quote}"
        f"{match.group('separator')}{quote}[REDACTED]{quote}"
    )


def redact_assignment(match: re.Match[str]) -> str:
    if not is_sensitive_name(match.group("key")):
        return match.group(0)
    return f"{match.group('key')}=[REDACTED]"


def redact_private_ipv6(match: re.Match[str]) -> str:
    value = match.group(0)
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value
    if address.version == 6 and (
        address.is_private or address.is_loopback or address.is_link_local
    ):
        return endpoint_placeholder(value)
    return value


def redact_private_ipv4(match: re.Match[str]) -> str:
    value = match.group(0)
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value
    if address.version == 4 and (
        address.is_private or address.is_loopback or address.is_link_local
    ):
        return endpoint_placeholder(value)
    return value


def redact_text(value: str) -> str:
    value = PEM_BLOCK_RE.sub("[PEM_REDACTED]", value)
    value = JSON_ASSIGNMENT_RE.sub(redact_json_assignment, value)
    value = ASSIGNMENT_RE.sub(redact_assignment, value)
    value = BEARER_RE.sub("Bearer [REDACTED]", value)
    value = URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", value)
    value = URL_RE.sub(lambda match: endpoint_placeholder(match.group(0)), value)
    value = IPV4_CANDIDATE_RE.sub(redact_private_ipv4, value)
    value = IPV6_CANDIDATE_RE.sub(redact_private_ipv6, value)
    value = PRIVATE_HOST_RE.sub(
        lambda match: endpoint_placeholder(match.group(0)), value
    )
    value = CLOUD_ACCESS_KEY_RE.sub("[REDACTED]", value)
    return LONG_TOKEN_RE.sub("[REDACTED]", value)


def redact_value(value: Any, sensitive: bool = False) -> Any:
    if sensitive:
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): redact_value(item, is_sensitive_name(str(key)))
            for key, item in value.items()
        }
    return value
