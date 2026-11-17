"""Bounded credential redaction, shared with the standalone guest reader.

Text remains private operational evidence. Pattern redaction is not a guarantee
against every possible secret encoding; unknown ordinary messages are retained.
"""

from __future__ import annotations

import re
from typing import Any

REDACTION_POLICY = "nebius-vpngw/redaction-v1"
REDACTED = "[REDACTED]"
TEXT_LIMIT = 2 * 1024 * 1024
LINE_LIMIT = 65536
_CREDENTIAL = r"(?:psk|pre[-_ ]?shared[-_ ]?key|password|passwd|secret|token|access_token|refresh_token|client_secret|private_key|authorization)"
_ASSIGNMENT = re.compile(
    r"(?i)([\"']?\b" + _CREDENTIAL + r"\b[\"']?\s*[:=]\s*)"
    r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\[REDACTED\]|[^\r\n]+)"""
)
_CLI_SECRET = re.compile(r"(?i)(--" + _CREDENTIAL + r")(?:[ \t]+|=)([^\r\n]+)")
_EMPTY_SECRET = re.compile(r"(?i)[\"']?\b" + _CREDENTIAL + r"\b[\"']?[ \t]*[:=][ \t]*$")
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----")
_PEM_END = re.compile(r"-----END [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----")
_DUMP = re.compile(
    r"(?i)(?:private key|pre.shared key|secret|key material|encryption key|integrity key).*(?:[0-9]+ bytes|=>|dump)"
)
_HEX_LINE = re.compile(r"^\s*(?:[0-9a-fA-F]{1,8}:?\s+)?(?:[0-9a-fA-F]{2}[ :]+){4,}.*$")
_OPAQUE = re.compile(r"^\s*[A-Za-z0-9+/]{40,}={0,2}\s*$")
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")


def has_closing_quote(text: str, quote: str) -> bool:
    escaped = False
    for char in text:
        if not escaped and char == quote:
            return True
        escaped = not escaped if char == "\\" else False
    return False


class TextRedactor:
    """Line-bounded state across reads; incomplete sensitive tails never flush."""

    def __init__(self, literals: tuple[str, ...] = ()) -> None:
        self.pending = ""
        self.block: str | None = None
        self.limited = False
        self.redactions = 0
        self.discarded = 0
        self.literals = tuple(value for value in literals if value)

    def line(self, line: str) -> str:
        if self.block:
            self.discarded += 1
            if (self.block == "pem" and _PEM_END.search(line)) or (
                self.block in {"'", '"'} and has_closing_quote(line, self.block)
            ):
                self.block = None
            elif self.block == "indent" and line and not line[0].isspace():
                self.block = None
                return self.line(line)
            return ""
        if _PEM_BEGIN.search(line):
            if not _PEM_END.search(line):
                self.block = "pem"
            self.redactions += 1
            return REDACTED + "\n"
        if (
            _DUMP.search(line)
            or _HEX_LINE.fullmatch(line.rstrip("\r\n"))
            or _OPAQUE.fullmatch(line)
        ):
            self.redactions += 1
            return REDACTED + "\n"
        if "\ufffd" in line or "\x00" in line:
            self.discarded += 1
            return "[unreadable text omitted]\n"
        line = _ANSI.sub("", line)
        line = "".join(char for char in line if char in "\n\t" or ord(char) >= 32)
        if _EMPTY_SECRET.search(line.rstrip("\r\n")):
            self.block = "indent"
            self.redactions += 1
            return line.rstrip("\r\n") + " " + REDACTED + "\n"
        # Multiline shell/JSON strings and YAML block scalars need state, not a
        # per-chunk replacement. Do not release the first fragment of the value.
        starts = re.finditer(
            r"(?i)(?:[\"']?\b"
            + _CREDENTIAL
            + r"\b[\"']?\s*[:=]\s*|--"
            + _CREDENTIAL
            + r"[ \t]+)([\"'|>])",
            line,
        )
        for start in starts:
            quote = start[1]
            tail = line[start.end() :]
            if quote in "|>" or not has_closing_quote(tail, quote):
                self.block = "indent" if quote in "|>" else quote
                self.redactions += 1
                line = line[: start.end() - 1] + REDACTED + "\n"
                break
        line, count = _ASSIGNMENT.subn(lambda m: m[1] + '"' + REDACTED + '"', line)
        self.redactions += count
        line, count = _CLI_SECRET.subn(lambda m: m[1] + " " + REDACTED, line)
        self.redactions += count
        for pattern in (
            r"(?i)\bBearer\s+(?!\[REDACTED\])[A-Za-z0-9._~+/=-]+",
            r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
            r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
            r"(?i)(?<=://)[^\s/@:]+:[^\s/@]+@",
        ):
            line, count = re.subn(pattern, REDACTED, line)
            self.redactions += count
        for value in self.literals:
            if value in line:
                self.redactions += line.count(value)
                line = line.replace(value, REDACTED)
        return line

    def feed(self, text: str, *, final: bool = False, complete: bool = True) -> str:
        if self.limited:
            return ""
        result = []
        for part in text.splitlines(keepends=True):
            self.pending += part
            if len(self.pending) > LINE_LIMIT:
                # The discarded line may open a multiline credential. Its
                # continuation is unsafe even across later feed() calls.
                self.pending = ""
                self.limited = True
                self.discarded += 1
                break
            if part.endswith(("\n", "\r")):
                result.append(self.line(self.pending))
                self.pending = ""
        if final:
            if self.pending and complete:
                result.append(self.line(self.pending))
            elif self.pending:
                self.discarded += 1
            self.pending = ""
        return "".join(result)


def redact_text(
    text: str, *, complete: bool = True, literals: tuple[str, ...] = ()
) -> dict[str, Any]:
    redactor = TextRedactor(literals)
    bounded = text[:TEXT_LIMIT]
    clean = redactor.feed(bounded, final=True, complete=complete and len(text) <= TEXT_LIMIT)
    return {
        "content": clean,
        "policy": REDACTION_POLICY,
        "redactions": redactor.redactions,
        "discarded": redactor.discarded + int(redactor.block is not None),
        "truncated": len(text) > TEXT_LIMIT or not complete or redactor.limited,
    }


def unsafe_text(text: str, literals: tuple[str, ...] = ()) -> bool:
    """Final defense: accept our placeholders, reject remaining credential values."""
    inspected = _ASSIGNMENT.sub(lambda m: "" if m[2].strip("\"'") == REDACTED else m[0], text)
    return bool(
        _PEM_BEGIN.search(inspected)
        or any(match[2].strip("\"'") != REDACTED for match in _CLI_SECRET.finditer(inspected))
        or any(value and value in text for value in literals)
        or re.search(
            r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
            inspected,
        )
        or any(match[2].strip("\"'") for match in _ASSIGNMENT.finditer(inspected))
    )
