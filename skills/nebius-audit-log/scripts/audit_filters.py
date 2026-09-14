"""Compile explicit selectors and an AND-only subset of the Audit Logs language."""

from __future__ import annotations

import re
from typing import Any

from audit_cli import InputError

ID = re.compile(r"[a-z][a-z0-9]*-[A-Za-z0-9_-]{1,180}\Z")
REGION = re.compile(r"[a-z]+-[a-z]+[0-9]+\Z")
ATOM = re.compile(r"[A-Za-z0-9._:/@+=,~-]{1,512}\Z")
FIELDS = frozenset(
    {
        "action",
        "authentication.subject.name",
        "authentication.subject.service_account_id",
        "authentication.subject.tenant_user_id",
        "project_region.name",
        "resource.hierarchy.id",
        "resource.hierarchy.name",
        "resource.metadata.id",
        "resource.metadata.name",
        "resource.metadata.type",
        "service.name",
        "type",
        "status",
    }
)
TOKEN = re.compile(
    r"""\s*(?:(?P<word>[A-Za-z_][A-Za-z0-9_.]*)|(?P<string>'(?:[^'\\\r\n]|\\[^\r\n])*'|"(?:[^"\\\r\n]|\\[^\r\n])*")|(?P<op>!=|=|:|\(|\)|,))"""
)


def require_id(value: str, field: str, prefix: str | None = None) -> str:
    if not ID.fullmatch(value) or (prefix and not value.startswith(prefix)):
        raise InputError(f"{field} must contain a valid Nebius ID.")
    return value


def subject_field(subject_id: str) -> str:
    require_id(subject_id, "subject ID")
    for prefix, field in (
        ("tenantuseraccount-", "tenant_user_id"),
        ("serviceaccount-", "service_account_id"),
    ):
        if subject_id.startswith(prefix):
            return f"authentication.subject.{field}"
    raise InputError("subject ID must identify a tenant user or service account.")


def equality(field: str, value: str) -> str:
    if not ATOM.fullmatch(value):
        raise InputError("A structured filter contains an unsupported value.")
    return f"{field}='{value}'"


def parse_extra(expression: str | None) -> tuple[str, list[dict[str, str]]]:
    """Return a normalized conjunction and metadata with no user literals."""
    if expression is None:
        return "", []
    if (
        not expression.strip()
        or len(expression) > 4096
        or any(ord(c) < 32 for c in expression)
    ):
        raise InputError(
            "raw-filter must be a nonempty, single-line expression of at most 4096 characters."
        )
    tokens: list[tuple[str, str]] = []
    position = 0
    source = expression.strip()
    while position < len(source):
        match = TOKEN.match(source, position)
        if match is None:
            raise InputError("raw-filter contains unsupported syntax.")
        tokens.append((match.lastgroup or "", match.group(match.lastgroup or 0)))
        position = match.end()
    cursor = 0

    def take(kind: str, expected: str | None = None) -> str:
        nonlocal cursor
        if cursor >= len(tokens):
            raise InputError(
                "raw-filter must contain complete AND-connected predicates."
            )
        actual_kind, value = tokens[cursor]
        if actual_kind != kind or (expected is not None and value != expected):
            raise InputError(
                "raw-filter supports only comparisons and regex predicates joined by AND."
            )
        cursor += 1
        return value

    predicates = []
    metadata = []
    while cursor < len(tokens):
        word = take("word")
        if word == "regex":
            take("op", "(")
            field = take("word")
            take("op", ",")
            value = take("string")
            take("op", ")")
            operator = "regex"
            predicate = f"regex({field}, {value})"
        else:
            field = word
            operator = take("op")
            if operator not in {"=", "!=", ":"}:
                raise InputError("raw-filter contains an unsupported comparison.")
            value = take("string")
            predicate = f"{field}{operator}{value}"
        if field not in FIELDS:
            raise InputError(
                "raw-filter references an unsupported or credential-bearing field."
            )
        predicates.append(predicate)
        metadata.append({"field": field, "operator": operator})
        if cursor < len(tokens):
            take("word", "AND")
            if cursor == len(tokens):
                raise InputError("raw-filter ends with an incomplete conjunction.")
    return " AND ".join(predicates), metadata


def compile_filter(
    selector: dict[str, Any],
    filters: dict[str, str],
    extra: str,
    caller_id: str | None = None,
) -> str:
    parts = []
    kind = selector["kind"]
    if kind == "resource":
        parts.append(equality("resource.metadata.id", selector["id"]))
    elif kind in {"subject", "current_subject"}:
        identity = selector.get("id") if kind == "subject" else caller_id
        if identity is None:
            raise InputError("The current subject is unresolved.")
        parts.append(equality(subject_field(identity), identity))
    for field, value in filters.items():
        parts.append(equality(field, value))
    if extra:
        parts.append(extra)
    return " AND ".join(parts)
