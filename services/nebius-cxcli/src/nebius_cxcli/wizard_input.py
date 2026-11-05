"""Literal string lists and safe validation errors for shared wizard input."""

from __future__ import annotations

import yaml


def validate_text(raw: str) -> None:
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in raw):
        raise ValueError("Control characters are not accepted. Type a value; use q/qq to navigate.")


def _validate_collection_text(value: object) -> None:
    pending = [value]
    seen = set()
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            # Escaped text whitespace is valid structured data, including multiline values.
            validate_text(item.translate(str.maketrans("", "", "\n\r\t")))
        elif isinstance(item, (dict, list)) and id(item) not in seen:
            seen.add(id(item))
            pending.extend(item)
            if isinstance(item, dict):
                pending.extend(item.values())


def parse_collection(raw: str, *, kind: str | None, string_sequence: bool) -> object:
    validate_text(raw)
    if string_sequence and not raw.lstrip().startswith(("[", "{")):
        values = [item.strip() for item in raw.split(",")]
        if not values or any(not item for item in values):
            raise ValueError("Expected a comma-separated list of strings.")
        return values
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(
            "Invalid YAML/JSON value. Check brackets, quotes and collection syntax."
        ) from exc
    _validate_collection_text(parsed)
    if kind == "sequence":
        if isinstance(parsed, list):
            if string_sequence:
                if not all(isinstance(item, str) for item in parsed):
                    raise ValueError("Expected a comma-separated list of strings.")
                for item in parsed:
                    validate_text(item)
            return parsed
        if string_sequence:
            raise ValueError("Expected a comma-separated list of strings.")
        raise ValueError("Expected a YAML/JSON list value.")
    if kind == "mapping" and not isinstance(parsed, dict):
        raise ValueError("Expected a YAML/JSON mapping value.")
    return parsed
