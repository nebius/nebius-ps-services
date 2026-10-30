#!/usr/bin/env python3
"""Qualify an installed mcp-grafana against loopback and dummy tokens only.

This test records the v2.1.1 stdio retention limitation. A future upstream fix
should fail the retention assertion and trigger a lifecycle design review.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
from typing import Iterator


class Client:
    def __init__(self, binary: str, token: Path, url: str) -> None:
        # Isolate the fixture from all configured Grafana credentials/endpoints.
        env = {
            key: val
            for key, val in os.environ.items()
            if not key.startswith("GRAFANA_")
        }
        env.update(GRAFANA_URL=url, GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE=str(token))
        self.process = subprocess.Popen(
            [binary, "--disable-write", "--max-loki-log-limit", "20"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )
        self.messages: queue.Queue[str | None] = queue.Queue()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        self.request_id = 0

    def read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.messages.put(line)
        self.messages.put(None)

    def send(self, message: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        self.request_id += 1
        self.send({"id": self.request_id, "method": method, "params": params})
        # Each fixture call has a bounded wait, including unsolicited messages.
        import time

        deadline = time.monotonic() + 20
        while True:
            line = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            if line is None:
                raise AssertionError("MCP closed before the fixture response")
            message = json.loads(line)
            if message.get("id") == self.request_id:
                if "error" in message:
                    raise AssertionError("MCP returned a protocol error")
                return message["result"]

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "token-lifecycle-fixture", "version": "1"},
            },
        )
        assert "serverInfo" in result
        self.send({"method": "notifications/initialized"})

    def list_datasources(self) -> None:
        result = self.request(
            "tools/call", {"name": "list_datasources", "arguments": {"limit": 1}}
        )
        assert not result.get("isError"), "datasource fixture call failed"

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()


@contextmanager
def connected(binary: str, token: Path, url: str) -> Iterator[Client]:
    client = Client(binary, token, url)
    try:
        client.initialize()
        yield client
    finally:
        client.close()


def qualify(binary: str) -> None:
    observed: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] != "/api/datasources":
                self.send_error(404)
                return
            observed.append(self.headers.get("Authorization"))
            payload = b"[]"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="grafana-stdio-fixture-") as temp:
            token = Path(temp) / "token"
            token.write_text("fixture-token-a\n")
            token.chmod(0o600)
            url = f"http://127.0.0.1:{server.server_port}"
            with connected(binary, token, url) as client:
                client.list_datasources()
                assert observed[-1:] == ["Bearer fixture-token-a"]
                replacement = Path(temp) / "replacement"
                replacement.write_text("fixture-token-b\n")
                replacement.chmod(0o600)
                replacement.replace(token)
                client.list_datasources()
                assert observed[-1:] == ["Bearer fixture-token-a"], (
                    "upstream stdio lifecycle changed; review the restart design"
                )
            with connected(binary, token, url) as client:
                client.list_datasources()
                assert observed[-1:] == ["Bearer fixture-token-b"]
            assert len(observed) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print(
        "PASS: stdio retains the startup token; a new process loads replacement bytes"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", default="mcp-grafana", help="installed binary to qualify"
    )
    args = parser.parse_args()
    binary = shutil.which(args.binary)
    if binary is None:
        parser.error("mcp-grafana binary is unavailable; install separately")
    qualify(binary)


if __name__ == "__main__":
    main()
