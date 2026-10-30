#!/usr/bin/env python3
"""Exercise the pinned proxy against a loopback-only OAuth/MCP server and fake browser.

No laptop registration or real credential is read or changed. Processes are owned
and terminated, synthetic tokens live only in a temporary private fixture.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import hashlib
import json
import os
import queue
import signal
import shutil
import tempfile
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.parse


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        if process.poll() is None:
            raise
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, value, code=200):
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if code == 401:
            self.send_header(
                "WWW-Authenticate",
                f'Bearer resource_metadata="{self.server.base}/resource"',
            )
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        b = self.server.base
        if self.path == "/resource":
            self.respond({"resource": b + "/mcp", "authorization_servers": [b]})
        elif self.path.startswith("/.well-known/"):
            self.respond(
                {
                    "issuer": b,
                    "authorization_endpoint": b + "/authorize",
                    "token_endpoint": b + "/token",
                    "registration_endpoint": b + "/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                }
            )
        elif self.path.startswith("/authorize"):
            self.respond({})
        elif self.path == "/mcp":
            self.respond(
                {},
                405
                if self.headers.get("Authorization") == "Bearer " + self.server.token
                else 401,
            )
        else:
            self.respond({}, 404)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/register":
            self.respond(
                {
                    "client_id": "fixture-client",
                    "token_endpoint_auth_method": "none",
                    "redirect_uris": json.loads(raw).get("redirect_uris", []),
                },
                201,
            )
        elif self.path == "/token":
            params = urllib.parse.parse_qs(raw.decode())
            if params.get("grant_type") == ["refresh_token"]:
                self.server.refreshes += 1
                self.respond({"error": "invalid_grant"}, 400)
            else:
                self.server.exchanges += 1
                self.server.token = "fixture-valid-" + str(self.server.exchanges)
                self.respond(
                    {
                        "access_token": self.server.token,
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "refresh_token": "fixture-refresh",
                    }
                )
        elif self.path == "/mcp":
            if self.headers.get("Authorization") != "Bearer " + self.server.token:
                self.respond({}, 401)
                return
            d = json.loads(raw)
            if "id" not in d:
                self.respond({}, 202)
                return
            if d["method"] == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            elif d["method"] == "tools/call":
                result = {"content": [{"type": "text", "text": "fixture-success"}]}
            elif d["method"] == "tools/list":
                result = {
                    "tools": [{"name": "fixture", "inputSchema": {"type": "object"}}]
                }
            else:
                result = {}
            self.respond({"jsonrpc": "2.0", "id": d["id"], "result": result})
        else:
            self.respond({}, 404)


def rpc(p, q, ident, method, params):
    p.stdin.write(
        (
            json.dumps(
                {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}
            )
            + "\n"
        ).encode()
    )
    p.stdin.flush()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            line = q.get(timeout=max(0.01, deadline - time.monotonic()))
        except queue.Empty:
            raise AssertionError("native proxy RPC deadline exceeded") from None
        if not line:
            raise AssertionError("native proxy exited")
        data = json.loads(line)
        if data.get("id") == ident:
            assert "result" in data and not data["result"].get("isError"), (
                "native proxy rejected RPC"
            )
            return data["result"]
    raise AssertionError("native proxy RPC deadline exceeded")


def browser_events(root, expected, *, successful=True):
    """Wait for complete fake-opener receipts, then assert count and callbacks."""
    deadline = time.monotonic() + 3
    while True:
        events = [json.loads(path.read_text()) for path in root.glob("browser-*.json")]
        assert len(events) <= expected, "unexpected extra browser authorization"
        if len(events) == expected and all(
            event["status"] != "pending" for event in events
        ):
            if successful:
                assert all(event["status"] == 200 for event in events), (
                    "unsuccessful browser callback"
                )
            return events
        assert time.monotonic() < deadline, "missing or unsuccessful browser callback"
        time.sleep(0.01)


def short_probe_control(base, entry):
    """Copy executable code only; change one internal deadline in a local control."""
    directory = base / "short-probe-control"
    directory.mkdir(mode=0o700)
    (directory / "package.json").write_text('{"type":"module"}\n')
    dependencies = entry.parent.parent.parent
    assert dependencies.name == "node_modules" and dependencies.is_dir()
    # Reuse reviewed dependency code read-only; never link credential state.
    (directory / "node_modules").symlink_to(
        dependencies.resolve(), target_is_directory=True
    )
    for source in entry.parent.glob("*.js"):
        assert source.is_file() and not source.is_symlink()
        shutil.copyfile(source, directory / source.name)
    chunk = directory / "chunk-UZ6Y4QJH.js"
    text = chunk.read_text()
    before = "await testClient.connect(testTransport, { timeout: 360000 });"
    assert text.count(before) == 1, "review the pinned probe timeout fixture"
    chunk.write_text(
        text.replace(
            before, "await testClient.connect(testTransport, { timeout: 1000 });"
        )
    )
    return directory / entry.name


def run(base, node, entry):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    control = short_probe_control(base, entry)
    try:
        for kind in [
            "cold",
            "expired-single",
            "expired-concurrent",
            "rejected-concurrent",
            "warm-occupied",
            "mid-session",
            "delayed-control",
            "delayed-consent",
        ]:
            concurrent = kind.endswith("concurrent") or kind.startswith("delayed")
            server.refreshes = server.exchanges = 0
            server.token = "fixture-initial"
            root = base / kind
            root.mkdir(mode=0o700)
            auth = root / "auth"
            auth.mkdir(mode=0o700)
            store = auth / "mcp-remote-v1"
            store.mkdir(mode=0o700)
            bindir = root / "bin"
            bindir.mkdir(mode=0o700)
            opener = bindir / "open"
            opener.write_text(
                "#!"
                + sys.executable
                + """
import json,os,sys,time,urllib.parse,urllib.request
from pathlib import Path
p=urllib.parse.parse_qs(urllib.parse.urlsplit(sys.argv[-1]).query)
u=p['redirect_uri'][0]+'?'+urllib.parse.urlencode({'code':'fixture-code','state':p['state'][0]})
receipt=Path("""
                + repr(str(root))
                + """,'browser-'+str(os.getpid())+'.json')
node_pid=os.getppid()
def record(status):
    pending=receipt.with_suffix('.pending')
    pending.write_text(json.dumps({'node_pid':node_pid,'status':status}))
    pending.replace(receipt)
record('pending')
time.sleep("""
                + repr(2 if kind.startswith("delayed") else 0.5 if concurrent else 0)
                + """)
try:
    with urllib.request.urlopen(u,timeout=4) as r: result=r.status
except Exception as e:result=type(e).__name__
record(result)
    """
            )
            opener.chmod(0o700)
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            if kind == "warm-occupied":
                sock.listen()
            else:
                sock.close()
            url = server.base + "/mcp"
            path = store / (hashlib.md5(url.encode()).hexdigest() + "_tokens.json")
            expired = kind.startswith(("expired", "delayed"))
            path.write_text(
                json.dumps(
                    {
                        "access_token": "fixture-expired"
                        if expired or kind == "rejected-concurrent"
                        else server.token,
                        "token_type": "Bearer",
                        "refresh_token": "fixture-rejected-refresh",
                        "expires_in": 3600,
                        "expires_at": int(time.time() * 1000)
                        + (-600000 if expired else 3600000),
                    }
                )
            )
            path.chmod(0o600)
            if kind == "cold":
                path.unlink()
            ps = []
            start = time.monotonic()
            try:
                for _ in range(2 if concurrent else 1):
                    p = subprocess.Popen(
                        [
                            str(node),
                            str(control if kind == "delayed-control" else entry),
                            url,
                            str(port),
                            "--transport",
                            "http-only",
                            "--auth-timeout",
                            "5",
                            "--silent",
                        ],
                        env=dict(
                            os.environ,
                            PATH=str(bindir) + ":/usr/bin:/bin",
                            MCP_REMOTE_CONFIG_DIR=str(auth),
                        ),
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    q = queue.Queue()

                    def readlines(proc=p, out=q):
                        for line in proc.stdout:
                            out.put(line)
                        out.put(b"")

                    threading.Thread(target=readlines, daemon=True).start()
                    ps.append((p, q))
                if kind == "delayed-control":
                    # The only changed variable is the internal probe deadline.
                    # At least one proxy must exit before the two-second consent.
                    deadline = time.monotonic() + 8
                    while not list(root.glob("browser-*.json")):
                        assert all(p.poll() is None for p, _ in ps), (
                            "probe exited before browser consent"
                        )
                        assert time.monotonic() < deadline, (
                            "probe never requested consent"
                        )
                        time.sleep(0.01)
                    deadline = time.monotonic() + 1.5
                    while (
                        all(p.poll() is None for p, _ in ps)
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.01)
                    assert any(
                        p.poll() is not None and p.returncode != 0 for p, _ in ps
                    ), "short probe control did not fail before consent"
                    browser_events(root, 1, successful=False)
                    print(
                        json.dumps(
                            {
                                "trial": kind,
                                "status": "EXPECTED_FAILURE",
                                "local_fixture": True,
                            }
                        ),
                        flush=True,
                    )
                    continue
                for p, q in ps:
                    rpc(
                        p,
                        q,
                        1,
                        "initialize",
                        {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "fixture", "version": "1"},
                        },
                    )
                    p.stdin.write(
                        b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                    )
                    p.stdin.flush()
                    result = rpc(
                        p, q, 2, "tools/call", {"name": "fixture", "arguments": {}}
                    )
                    assert result.get("content") == [
                        {"type": "text", "text": "fixture-success"}
                    ]
                initial = 0 if kind in ("warm-occupied", "mid-session") else 1
                events = browser_events(root, initial)
                assert server.exchanges == initial, "unexpected startup token exchanges"
                if kind == "mid-session" or concurrent:
                    if concurrent:
                        owner = events[0]["node_pid"]
                        assert owner in {p.pid for p, _ in ps}, (
                            "fake browser owner was not a proxy"
                        )
                        # The follower must recover BEFORE the previous owner can
                        # replace shared tokens and conceal its stale PKCE state.
                        ps.sort(key=lambda pair: pair[0].pid == owner)
                        assert ps[0][0].pid != owner
                    server.token = "fixture-revoked"
                    for p, q in ps:
                        result = rpc(
                            p, q, 3, "tools/call", {"name": "fixture", "arguments": {}}
                        )
                        assert result.get("content") == [
                            {"type": "text", "text": "fixture-success"}
                        ]
                    events = browser_events(root, initial + 1)
                    assert server.exchanges == initial + 1, (
                        "unexpected recovery token exchanges"
                    )
                    if concurrent:
                        assert {event["node_pid"] for event in events} == {
                            p.pid for p, _ in ps
                        }, "startup follower did not own recovery"
                print(
                    json.dumps(
                        {
                            "trial": kind,
                            "status": "PASS",
                            "processes": len(ps),
                            "code_exchanges": server.exchanges,
                            "browser_attempts": len(list(root.glob("browser-*.json"))),
                            "seconds": round(time.monotonic() - start, 2),
                        }
                    ),
                    flush=True,
                )
            finally:
                for p, q in ps:
                    stop(p)
                sock.close()
    finally:
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(
        description="Local OAuth regression fixture; no real browser or external account."
    )
    parser.add_argument("--node", required=True, type=Path)
    parser.add_argument("--proxy", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("the fake macOS browser opener requires macOS")
    if not args.node.is_absolute() or not args.proxy.is_absolute():
        parser.error("absolute paths required")
    with tempfile.TemporaryDirectory(prefix="tavily-oauth-fixture-") as directory:
        run(Path(directory).resolve(), args.node, args.proxy)


if __name__ == "__main__":
    main()
