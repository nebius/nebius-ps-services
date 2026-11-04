"""Dependency-free, bounded workload executed in the validation Pod.

The tiny unary gRPC sender only emits an OTLP Export request. It deliberately
does not grade an ingestion acknowledgement: backend and Grafana readback own
success. Wire fields follow opentelemetry-proto trace/v1 and gRPC HTTP/2.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, HTTPServer


def varint(value: int) -> bytes:
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    data.append(value)
    return bytes(data)


def field(number: int, value: bytes | str) -> bytes:
    raw = value.encode() if isinstance(value, str) else value
    return varint(number * 8 + 2) + varint(len(raw)) + raw


def trace_request(identity: dict[str, str], trace_id: str, started_ns: int) -> bytes:
    attributes = b"".join(
        field(1, field(1, key) + field(2, field(1, value)))
        for key, value in {"service.name": "cxcli-validation", **identity}.items()
    )
    span = (
        field(1, bytes.fromhex(trace_id))
        + field(2, bytes.fromhex(trace_id[:16]))
        + field(5, "cxcli-observability-probe")
        + b"\x30\x01"
        + b"\x39"
        + struct.pack("<Q", started_ns)
        + b"\x41"
        + struct.pack("<Q", started_ns + 1_000_000)
    )
    return field(1, field(1, attributes) + field(2, field(2, span)))


def _hpack_string(value: str) -> bytes:
    raw = value.encode()
    length = bytes([len(raw)]) if len(raw) < 127 else b"\x7f" + varint(len(raw) - 127)
    return length + raw


def _frame(kind: int, flags: int, stream: int, data: bytes = b"") -> bytes:
    return len(data).to_bytes(3, "big") + bytes([kind, flags]) + stream.to_bytes(4, "big") + data


def send_trace(endpoint: str, payload: bytes) -> None:
    host, port = endpoint.rsplit(":", 1)
    headers = b"".join(
        b"\x00" + _hpack_string(k) + _hpack_string(v)
        for k, v in [
            (":method", "POST"),
            (":scheme", "http"),
            (":path", "/opentelemetry.proto.collector.trace.v1.TraceService/Export"),
            (":authority", endpoint),
            ("grpc-timeout", "5S"),
            ("content-type", "application/grpc"),
            ("te", "trailers"),
        ]
    )
    with socket.create_connection((host, int(port)), timeout=5) as connection:
        connection.sendall(
            b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
            + _frame(4, 0, 0)
            + _frame(1, 4, 1, headers)
            + _frame(0, 1, 1, b"\0" + struct.pack(">I", len(payload)) + payload)
        )
        buffer = b""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            data = connection.recv(65536)
            if not data:
                return
            buffer += data
            while len(buffer) >= 9:
                length = int.from_bytes(buffer[:3], "big")
                if len(buffer) < 9 + length:
                    break
                kind, flags = buffer[3:5]
                stream = int.from_bytes(buffer[5:9], "big") & 0x7FFFFFFF
                body, buffer = buffer[9 : 9 + length], buffer[9 + length :]
                if kind == 4 and not flags & 1:
                    connection.sendall(_frame(4, 1, 0))
                elif kind == 6 and not flags & 1:
                    connection.sendall(_frame(6, 1, 0, body))
                elif kind in {3, 7} or (stream == 1 and kind in {0, 1} and flags & 1):
                    return


def run(settings: dict) -> None:
    identity = settings["identity"]
    labels = ",".join(f"{key}={json.dumps(value)}" for key, value in identity.items())
    sample = f"cxcli_observability_probe{{{labels}}} 1\n".encode()

    class Metrics(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/metrics" else 404)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            if self.path == "/metrics":
                self.wfile.write(sample)

        def log_message(self, *args):
            pass

    server = HTTPServer(("0.0.0.0", 8080), Metrics)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    started_ns = time.time_ns()
    trace = trace_request(identity, settings["trace_id"], started_ns)
    deadline = time.monotonic() + 540
    while time.monotonic() < deadline:
        if "logs" in settings["signals"]:
            print(
                json.dumps(
                    {**identity, "timestamp": time.time(), "message": "cxcli-observability-probe"}
                ),
                flush=True,
            )
        if "traces" in settings["signals"]:
            # The external validator owns success; missing evidence times out.
            with suppress(OSError):
                send_trace(settings["trace_endpoint"], trace)
        time.sleep(10)
    server.shutdown()


if __name__ == "__main__":
    run(json.loads(sys.argv[1]))
