"""Bounded stdio client; retain hashes and finite runtime metadata only."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import threading
import time


class NativeServer:
    def __init__(self, command, project, environment, notify, deadline=None):
        self.deadline = deadline
        self.process = subprocess.Popen(
            command, cwd=project, env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            start_new_session=True,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.pending = b""
        self.sequence = 0
        self.notify = notify
        self.stdout_hash = hashlib.sha256()
        self.stderr_hash = hashlib.sha256()
        self.stderr_done = threading.Event()
        self.cleanup_verified = self.forced_cleanup = False
        self.stderr_thread = threading.Thread(target=self._errors, daemon=True)
        self.stderr_thread.start()

    def _errors(self):
        try:
            while block := self.process.stderr.read(65536):
                self.stderr_hash.update(block)
        finally:
            self.stderr_done.set()

    def send(self, message, timeout=5):
        payload = (json.dumps(message) + "\n").encode()
        if len(payload) > 1024 * 1024:
            raise ValueError("native request exceeds its bound")
        descriptor = self.process.stdin.fileno()
        os.set_blocking(descriptor, False)
        deadline = min(time.monotonic() + timeout, self.deadline or float("inf"))
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_WRITE)
            while payload:
                if time.monotonic() >= deadline or not selector.select(max(0, deadline - time.monotonic())):
                    raise ValueError("native input exceeded its time bound")
                try:
                    size = os.write(descriptor, payload)
                except BlockingIOError:
                    continue
                payload = payload[size:]

    def receive(self, timeout):
        if b"\n" not in self.pending:
            if not self.selector.select(timeout):
                return None
            chunk = os.read(self.process.stdout.fileno(), 65536)
            self.stdout_hash.update(chunk)
            if not chunk:
                raise ValueError("native server closed before completion")
            self.pending += chunk
            if len(self.pending) > 1024 * 1024:
                raise ValueError("native event exceeds its bound")
        if b"\n" not in self.pending:
            return None
        line, self.pending = self.pending.split(b"\n", 1)
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            raise ValueError("native event is invalid") from None
        if "method" in event:
            if "id" in event:
                # Never grant approvals or answer user questions on their behalf.
                self.send({"id": event["id"], "error": {
                    "code": -32601, "message": "Unattended verifier cannot handle this request"}})
                raise ValueError("native invocation requires an unsupported interactive request")
            self.notify(event)
        return event

    def request(self, method, params, timeout=15, deadline=None):
        self.sequence += 1
        request_id = self.sequence
        deadline = min(time.monotonic() + timeout, deadline or float("inf"),
                       self.deadline or float("inf"))
        self.send({"id": request_id, "method": method, "params": params},
                  timeout=max(0, deadline - time.monotonic()))
        while time.monotonic() < deadline:
            event = self.receive(min(.2, max(0, deadline - time.monotonic())))
            if event and event.get("id") == request_id:
                if not isinstance(event.get("result"), dict):
                    raise ValueError("native request rejected: " + method)
                return event["result"]
        raise ValueError("native request timed out: " + method)

    def close(self):
        # EOF is the normal app-server shutdown contract. Escalation is retained
        # as failed invocation evidence, even if it eventually proves quiescence.
        self.process.stdin.close()
        deadline = time.monotonic() + 5
        while self.process.poll() is None and time.monotonic() < deadline:
            if self.selector.select(.05):
                block = os.read(self.process.stdout.fileno(), 65536)
                self.stdout_hash.update(block)
        for sig in (None, signal.SIGTERM, signal.SIGKILL):
            if sig is not None:
                try:
                    os.killpg(self.process.pid, sig)
                    self.forced_cleanup = True
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                self.process.poll()
                try:
                    os.killpg(self.process.pid, 0)
                except ProcessLookupError:
                    self.cleanup_verified = self.process.returncode is not None
                    break
                except PermissionError:
                    pass
                time.sleep(.05)
            if self.cleanup_verified:
                break
        self.stderr_thread.join(timeout=2)
        self.cleanup_verified &= self.stderr_done.is_set()
        self.process.stdout.close()
        if self.stderr_done.is_set():
            self.process.stderr.close()
        self.selector.close()


def child_snapshot(server, root_session, deadline):
    """Read current loaded children; never load, resume, subscribe or scan logs."""
    result = server.request("thread/loaded/list", {"limit": 64}, deadline=deadline)
    if result.get("nextCursor") is not None or len(result.get("data", [])) > 64:
        raise ValueError("native loaded-thread inventory exceeds its bound")
    children = {}
    for native in result["data"]:
        if native == root_session:
            continue
        thread = server.request("thread/read", {"threadId": native, "includeTurns": False},
                                deadline=deadline)["thread"]
        if thread.get("id") != native:
            raise ValueError("native thread identity changed")
        if thread.get("parentThreadId") == root_session:
            status = thread.get("status", {}).get("type")
            if status not in {"active", "idle", "notLoaded", "systemError"}:
                raise ValueError("native thread status is unsupported")
            children[native] = status
    return children
