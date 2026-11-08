"""Owned native process groups with a pinned identity through descendant cleanup.

POSIX groups contain ordinary descendants, not processes that deliberately escape
or daemon-owned resources. Those require their resource owner's stop receipt.
"""
from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import time


class ProcessCleanupError(RuntimeError):
    pass


class OwnedProcessGroup:
    """Hold an unreaped group leader until the final signal has been delivered."""

    def __init__(self):
        if os.name != "posix":
            raise ProcessCleanupError("native process-group containment is unavailable")
        self.anchor = subprocess.Popen(
            [sys.executable, "-I", "-c",
             "import os,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
             "os.write(1,b'ready\\n'); os.read(0,1)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            process_group=0,
        )
        self.closed = False
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.anchor.stdout, selectors.EVENT_READ)
                if not selector.select(5) or self.anchor.stdout.readline() != b"ready\n":
                    raise ProcessCleanupError("native process-group anchor failed")
            self.anchor.stdout.close()
        except BaseException:
            try:
                self.close(grace=0)
                self.verify_absent()
            except BaseException as error:
                raise ProcessCleanupError("native process-group startup termination is unproven") from error
            raise

    @property
    def id(self):
        return self.anchor.pid

    def close(self, *, grace: float):
        if self.closed:
            return
        # Do not poll/reap the anchor before the last killpg: its PID pins PGID.
        try:
            try:
                os.killpg(self.id, signal.SIGTERM)
            except ProcessLookupError:
                pass
            time.sleep(max(0, grace))
        finally:
            try:
                os.killpg(self.id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                self.anchor.wait(timeout=5)
            finally:
                # Even an interrupted wait may have reaped the anchor. Never
                # signal its potentially reused group identity on a retry.
                if self.anchor.returncode is not None:
                    self.closed = True
                    if self.anchor.stdin:
                        self.anchor.stdin.close()

    def verify_absent(self, *, timeout: float = 5):
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.killpg(self.id, 0)
            except ProcessLookupError:
                return
            if time.monotonic() >= deadline:
                raise ProcessCleanupError("native process-group termination is unproven")
            time.sleep(.02)
