"""Bounded private worker framing: JSON metadata followed by opaque object bytes."""

from __future__ import annotations

import json
import socket
import struct
import time
from collections.abc import Mapping
from typing import Any

MAX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024


def _budget(sock: socket.socket, deadline: float | None) -> None:
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("S3 worker deadline exceeded")
        sock.settimeout(remaining)


def send_frame(
    sock: socket.socket,
    header: Mapping[str, Any],
    body: bytes = b"",
    *,
    deadline: float | None = None,
) -> None:
    encoded = json.dumps(dict(header), allow_nan=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_HEADER_BYTES or len(body) > MAX_OBJECT_BYTES:
        raise ValueError("S3 worker frame exceeds its size limit")
    for part in (struct.pack("!IQ", len(encoded), len(body)), encoded, body):
        _budget(sock, deadline)
        sock.sendall(part)


def _receive(sock: socket.socket, size: int, deadline: float | None) -> bytes:
    result = bytearray()
    while len(result) < size:
        _budget(sock, deadline)
        block = sock.recv(min(size - len(result), 1024 * 1024))
        if not block:
            raise EOFError("S3 worker closed its channel")
        result.extend(block)
    return bytes(result)


def receive_frame(
    sock: socket.socket, *, deadline: float | None = None
) -> tuple[dict[str, Any], bytes]:
    header_size, body_size = struct.unpack("!IQ", _receive(sock, 12, deadline))
    if header_size > MAX_HEADER_BYTES or body_size > MAX_OBJECT_BYTES:
        raise ValueError("S3 worker frame exceeds its size limit")
    header = json.loads(_receive(sock, header_size, deadline))
    if not isinstance(header, dict):
        raise ValueError("S3 worker header is not a mapping")
    return header, _receive(sock, body_size, deadline)
