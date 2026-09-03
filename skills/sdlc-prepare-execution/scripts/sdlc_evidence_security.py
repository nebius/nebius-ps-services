"""Shared sensitive-text screening for Agentic SDLC worker evidence."""

from __future__ import annotations

import re


SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAWS_ACCESS_KEY_ID\b\s*[:=]\s*[A-Z0-9]{16,}"),
    re.compile(r"\bAWS_SECRET_ACCESS_KEY\b\s*[:=]\s*[A-Za-z0-9/+=]{30,}"),
    re.compile(r"\bGITHUB_TOKEN\b\s*[:=]\s*[A-Za-z0-9_ghopsu-]{20,}"),
    re.compile(r"\bOPENAI_API_KEY\b\s*[:=]\s*sk-[A-Za-z0-9_-]{16,}"),
    re.compile(
        r"(?i)\b(password|secret|token)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+=:-]{12,}"
    ),
    re.compile(r"(?i)https?://[^\s/]+\.(?:internal|corp|local)(?::[0-9]+)?(?:/|\b)"),
)
PLACEHOLDERS = (
    "example",
    "dummy",
    "placeholder",
    "redacted",
    "<token>",
    "<secret>",
    "<password>",
    "changeme",
    "not-a-secret",
)


def contains_sensitive(value: str) -> bool:
    """Return whether non-placeholder text resembles sensitive material."""

    for line in value.splitlines() or [value]:
        lowered = line.lower()
        if any(marker in lowered for marker in PLACEHOLDERS):
            continue
        if any(pattern.search(line) for pattern in SENSITIVE_PATTERNS):
            return True
    return False
