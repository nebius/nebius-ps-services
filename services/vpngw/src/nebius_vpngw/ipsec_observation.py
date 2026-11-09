"""One bounded, typed VICI inventory for installed and streamed operations.

Only a completed list-sas exchange is negative evidence. Each operation owns its
socket: a timed-out request is discarded and never reused for an event stream.
Closing a client does not cancel daemon-side mutations.
"""

from __future__ import annotations

import json
import math
import re
import socket
import struct
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import vici

SCHEMA = "nebius-vpngw.ipsec-inventory.v1"
VICI_VERSION = "6.0.3"
VICI_WHEEL_SHA256 = "734f52b57e329fbd58f3bffd1305d6546be174aeb35bc4a2ef45c257e95ad924"
SOCKET_PATH = "/run/charon.vici"
MAX_BYTES = 4 * 1024 * 1024


class IncompleteInventory(ValueError):
    """The observation cannot establish either presence or absence."""


class DeadlineSocket:
    """Retain an absolute deadline even when upstream resets socket timeout.

    VICI 6.0.3 temporarily sets timeout=None after the first partial read.
    Reapply the remaining absolute budget before *every* I/O, including cleanup.
    """

    def __init__(self, sock: socket.socket, deadline: float) -> None:
        self.socket = sock
        self.deadline = deadline
        self.received = 0
        self.timeout: float | None = None

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("VICI operation deadline expired")
        return min(remaining, self.timeout) if self.timeout is not None else remaining

    def gettimeout(self) -> float | None:
        return self.timeout

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def recv(self, count: int) -> bytes:
        if count > MAX_BYTES or self.received >= MAX_BYTES:
            raise IncompleteInventory("VICI inventory exceeds limit")
        self.socket.settimeout(self._remaining())
        data = self.socket.recv(min(count, MAX_BYTES - self.received))
        self.received += len(data)
        return data

    def sendall(self, data: bytes) -> None:
        self.socket.settimeout(self._remaining())
        self.socket.sendall(data)


def _text(value: object) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="strict")
    if not isinstance(value, str) or not value or "\x00" in value:
        raise IncompleteInventory("invalid VICI field")
    return value


def _normalize(value: object, depth: int = 0) -> Any:
    if depth > 12:
        raise IncompleteInventory("VICI nesting exceeds limit")
    if isinstance(value, Mapping):
        return {_text(k): _normalize(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v, depth + 1) for v in value]
    # Empty scalar values exist in native inventories (e.g. empty identities).
    if value in (b"", ""):
        return ""
    return _text(value)


def interface_id(value: object) -> int:
    """VICI encodes if-id-in/out as hexadecimal, including digit-only IDs."""
    if not isinstance(value, str) or not re.fullmatch(r"(?:0x)?[0-9a-fA-F]{1,8}", value):
        raise IncompleteInventory("invalid VICI interface identity")
    return int(value, 16)


@dataclass(frozen=True)
class ChildSA:
    name: str
    state: str
    fields: dict[str, Any]

    @classmethod
    def from_fields(cls, fields: object) -> ChildSA:
        if not isinstance(fields, dict):
            raise IncompleteInventory("invalid VICI CHILD")
        name, state = _text(fields.get("name")), _text(fields.get("state"))
        if state == "INSTALLED":
            _text(fields.get("protocol"))
            if fields["protocol"] == "ESP":
                interface_id(fields.get("if-id-in"))
                interface_id(fields.get("if-id-out"))
        return cls(name, state, fields)

    def ready(self, name: str, if_id: int) -> bool:
        return (
            self.name == name
            and self.state == "INSTALLED"
            and self.fields.get("protocol") == "ESP"
            and interface_id(self.fields.get("if-id-in")) == if_id
            and interface_id(self.fields.get("if-id-out")) == if_id
        )


@dataclass(frozen=True)
class IKESA:
    name: str
    unique_id: str
    state: str
    children: tuple[ChildSA, ...]
    fields: dict[str, Any]

    @classmethod
    def from_fields(cls, name: str, fields: object) -> IKESA:
        if not isinstance(fields, dict):
            raise IncompleteInventory("invalid VICI IKE")
        unique_id = _text(fields.get("uniqueid"))
        if not unique_id.isascii() or not unique_id.isdigit() or int(unique_id) <= 0:
            raise IncompleteInventory("invalid VICI IKE identity")
        children = fields.get("child-sas", {})
        if not isinstance(children, dict):
            raise IncompleteInventory("invalid VICI CHILD set")
        return cls(
            _text(name),
            unique_id,
            _text(fields.get("state")),
            tuple(ChildSA.from_fields(c) for c in children.values()),
            fields,
        )


@dataclass(frozen=True)
class Inventory:
    sas: tuple[IKESA, ...] = ()
    complete: bool = False

    @classmethod
    def from_events(cls, events: object) -> Inventory:
        if not isinstance(events, list):
            raise IncompleteInventory("invalid VICI event inventory")
        sas = []
        for event in events:
            event = _normalize(event)
            if not isinstance(event, dict) or len(event) != 1:
                raise IncompleteInventory("invalid VICI SA event")
            name, fields = next(iter(event.items()))
            sas.append(IKESA.from_fields(name, fields))
        if len({sa.unique_id for sa in sas}) != len(sas):
            raise IncompleteInventory("duplicate VICI IKE identity")
        return cls(tuple(sas), True)

    def require_complete(self) -> Inventory:
        if not self.complete:
            raise IncompleteInventory("incomplete VICI observation")
        return self

    def ready(self, name: str, if_id: int) -> bool:
        self.require_complete()
        return any(
            sa.name == name
            and sa.state == "ESTABLISHED"
            and any(child.ready(name, if_id) for child in sa.children)
            for sa in self.sas
        )

    def pairs(self) -> list[tuple[str, dict[str, Any]]]:
        self.require_complete()
        return [(sa.name, sa.fields) for sa in self.sas]

    def json(self) -> str:
        return json.dumps(
            {
                "schema": SCHEMA,
                "complete": self.complete,
                "sas": [{sa.name: sa.fields} for sa in self.sas],
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> Inventory:
        if len(raw) > MAX_BYTES:
            raise IncompleteInventory("VICI inventory exceeds limit")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "complete", "sas"}
            or value["schema"] != SCHEMA
            or type(value["complete"]) is not bool
        ):
            raise IncompleteInventory("invalid VICI inventory envelope")
        if not value["complete"]:
            raise IncompleteInventory("incomplete VICI observation")
        return cls.from_events(value["sas"])


def snapshot(timeout: float = 5.0, *, socket_path: str = SOCKET_PATH) -> Inventory:
    """Consume the terminal reply AND unsubscribe before publishing any evidence."""
    if not math.isfinite(timeout) or timeout <= 0:
        return Inventory()
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(socket_path)
            session = vici.Session(DeadlineSocket(sock, deadline))
            events: list[dict[str, Any]] = []
            for event in session.list_sas():
                if len(events) >= 4096:
                    raise IncompleteInventory("VICI SA count exceeds limit")
                events.append(event)
            return Inventory.from_events(events)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        vici.exception.SessionException,
        vici.exception.CommandException,
        vici.exception.EventUnknownException,
        vici.exception.DeserializationException,
        struct.error,
    ):
        return Inventory()


def unload_connection(name: str, timeout: float = 5.0, *, socket_path: str = SOCKET_PATH) -> None:
    """Remove native start/trap authority on a dedicated bounded request session.

    Failure is an indeterminate mutation outcome, never proof of cancellation.
    The caller must not reactivate until exact SA teardown is independently seen.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) or not 0 < timeout <= 30:
        raise ValueError("invalid VICI connection operation")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        deadline = time.monotonic() + timeout
        sock.settimeout(timeout)
        sock.connect(socket_path)
        vici.Session(DeadlineSocket(sock, deadline)).unload_conn({"name": name})


def _connection_names(session: vici.Session) -> frozenset[str]:
    names: set[str] = set()
    for event in session.list_conns():
        event = _normalize(event)
        if not isinstance(event, dict) or len(event) != 1 or len(names) >= 4096:
            raise IncompleteInventory("invalid VICI connection inventory")
        name, fields = next(iter(event.items()))
        if name in names or not isinstance(fields, dict):
            raise IncompleteInventory("duplicate or invalid VICI connection")
        names.add(name)
    return frozenset(names)


@dataclass(frozen=True)
class ConfigurationInventory:
    connections: frozenset[str] = frozenset()
    shared_keys: frozenset[str] = frozenset()

    def includes(self, expected: ConfigurationInventory) -> bool:
        return expected.connections.issubset(self.connections) and expected.shared_keys.issubset(
            self.shared_keys
        )

    def json(self) -> str:
        return json.dumps(
            {"connections": sorted(self.connections), "shared_keys": sorted(self.shared_keys)}
        )

    @classmethod
    def from_json(cls, raw: str) -> ConfigurationInventory:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"connections", "shared_keys"}:
            raise IncompleteInventory("invalid complete configuration inventory")
        return cls(
            *(parse_connections(json.dumps(value[key])) for key in ("connections", "shared_keys"))
        )


def loaded_configuration(
    timeout: float = 3.0, *, socket_path: str = SOCKET_PATH
) -> ConfigurationInventory:
    """Observe connection and credential identifiers on one bounded daemon session.

    get-shared returns identifiers only, never key material. A disconnect or
    incomplete reply cannot prove configuration loss or admit a loader.
    """
    if not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("invalid VICI observation timeout")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        deadline = time.monotonic() + timeout
        sock.settimeout(timeout)
        sock.connect(socket_path)
        session = vici.Session(DeadlineSocket(sock, deadline))
        connections = _connection_names(session)
        shared = _normalize(session.get_shared())
        if not isinstance(shared, dict) or set(shared) != {"keys"}:
            raise IncompleteInventory("invalid VICI shared-key inventory")
        return ConfigurationInventory(connections, parse_connections(json.dumps(shared["keys"])))


def loaded_connections(timeout: float = 3.0, *, socket_path: str = SOCKET_PATH) -> frozenset[str]:
    """Return complete loaded authority, or raise on indeterminate evidence."""
    if not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("invalid VICI observation timeout")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        deadline = time.monotonic() + timeout
        sock.settimeout(timeout)
        sock.connect(socket_path)
        return _connection_names(vici.Session(DeadlineSocket(sock, deadline)))


def parse_connections(raw: str) -> frozenset[str]:
    """Decode only a complete connection-inventory subprocess result."""
    value = json.loads(raw)
    if (
        not isinstance(value, list)
        or len(value) > 4096
        or any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name)
            for name in value
        )
        or len(set(value)) != len(value)
    ):
        raise IncompleteInventory("invalid complete connection inventory")
    return frozenset(value)


def parse_sas(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """Decode the canonical inventory envelope; no textual status fallbacks."""
    return Inventory.from_json(raw).pairs()


def child_ready(raw: str, name: str, if_id: int) -> bool:
    return Inventory.from_json(raw).ready(name, if_id)


def command() -> tuple[str, ...]:
    return ("/usr/bin/python3", "-m", "nebius_vpngw.ipsec_observation")


if __name__ == "__main__":
    if sys.argv[1:] == ["--configuration"]:
        print(loaded_configuration().json())
    elif sys.argv[1:] == ["--connections"]:
        print(json.dumps(sorted(loaded_connections())))
    elif not sys.argv[1:]:
        print(snapshot().json())
    else:
        raise SystemExit("unsupported observation")
