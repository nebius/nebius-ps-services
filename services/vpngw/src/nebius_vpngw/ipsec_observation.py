"""Complete native VICI observations shared by installed and streamed operations."""

from __future__ import annotations

import re
from typing import Any


def parse_sas(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse VICI raw events with balanced containers, preserving rekey instances."""
    tokens = re.findall(r"[^\s{}=\[\]]+|[{}=\[\]]", raw)
    position = 0

    def block() -> dict[str, Any]:
        nonlocal position
        result: dict[str, Any] = {}
        if position >= len(tokens) or tokens[position] != "{":
            raise ValueError("missing VICI container")
        position += 1
        while position < len(tokens) and tokens[position] != "}":
            key = tokens[position]
            position += 1
            if key in result or position >= len(tokens):
                raise ValueError("ambiguous VICI field")
            if tokens[position] == "{":
                result[key] = block()
            elif tokens[position] == "=":
                position += 1
                if position >= len(tokens):
                    raise ValueError("missing VICI value")
                if tokens[position] == "[":
                    position += 1
                    values = []
                    while position < len(tokens) and tokens[position] != "]":
                        values.append(tokens[position])
                        position += 1
                    if position >= len(tokens):
                        raise ValueError("unterminated VICI list")
                    position += 1
                    result[key] = values
                else:
                    result[key] = tokens[position]
                    position += 1
            else:
                raise ValueError("invalid VICI field")
        if position >= len(tokens):
            raise ValueError("unterminated VICI event")
        position += 1
        return result

    sas = []
    reply = False
    while position < len(tokens):
        header = tokens[position : position + 2]
        position += 2
        if header == ["list-sa", "event"] and not reply:
            event = block()
            if len(event) != 1:
                raise ValueError("invalid VICI SA event")
            name, value = next(iter(event.items()))
            if not isinstance(value, dict) or not str(value.get("uniqueid", "")).isdigit():
                raise ValueError("invalid VICI IKE identity")
            sas.append((name, value))
        elif header == ["list-sas", "reply"] and not reply:
            terminal = block()
            if terminal and terminal != {"success": "yes"}:
                raise ValueError("unsuccessful VICI inventory reply")
            reply = True
        else:
            raise ValueError("unexpected VICI event")
    if not reply or len({sa["uniqueid"] for _, sa in sas}) != len(sas):
        raise ValueError("incomplete VICI SA observation")
    return sas


def interface_id(value: object) -> int:
    """VICI prints if-id-in/out with %.8x, including digit-only values."""
    if not isinstance(value, str) or not re.fullmatch(r"(?:0x)?[0-9a-fA-F]{1,8}", value):
        raise ValueError("invalid VICI interface identity")
    return int(value, 16)


def child_ready(raw: str, name: str, if_id: int) -> bool:
    """Require exact established IKE and installed ESP CHILD; allow rekey overlap."""
    ready = False
    for ike_name, sa in parse_sas(raw):
        if ike_name != name:
            continue
        if not isinstance(sa.get("state"), str):
            raise ValueError("missing VICI IKE state")
        if sa["state"] != "ESTABLISHED":
            continue
        children = sa.get("child-sas", {})
        if not isinstance(children, dict):
            raise ValueError("invalid VICI CHILD set")
        for child in children.values():
            if not isinstance(child, dict) or not isinstance(child.get("name"), str):
                raise ValueError("invalid VICI CHILD identity")
            if child["name"] != name:
                continue
            if not isinstance(child.get("state"), str):
                raise ValueError("missing VICI CHILD state")
            if child["state"] != "INSTALLED":
                continue
            if "protocol" not in child:
                raise ValueError("missing VICI CHILD protocol")
            if child["protocol"] != "ESP":
                continue
            inbound, outbound = (
                interface_id(child.get("if-id-in")),
                interface_id(child.get("if-id-out")),
            )
            ready |= inbound == if_id and outbound == if_id
    return ready
