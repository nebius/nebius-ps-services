"""Terminal progress, deliberately outside installation fingerprint inputs."""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time


class Messages:
    """Route installer print calls through the renderer's permanent-message lock."""

    def __init__(self, progress):
        self.progress = progress
        self.pending = ""

    def write(self, value):
        self.pending += value
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self.progress.message(line)
        return len(value)

    def flush(self):
        if self.pending:
            self.progress.message(self.pending)
            self.pending = ""


class Progress:
    def __init__(self, *, stream=None, interval=0.12, plain_interval=15):
        self.stream = stream if stream is not None else sys.stderr
        self.output = sys.stdout
        self.interactive = self.stream.isatty() and os.environ.get("TERM", "") not in {
            "",
            "dumb",
        }
        self.interval, self.plain_interval = interval, plain_interval
        self.started = time.monotonic()
        self.phase, self.component, self.log = "starting", "", ""
        self.completed, self.total = 0, 0
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.thread = None
        self.frame = 0
        self.active = False
        self.last_plain = 0.0

    def update(self, phase, *, component="", log="", completed=None, total=None):
        with self.lock:
            self.phase, self.component, self.log = phase, component, str(log)
            if completed is not None:
                self.completed = completed
            if total is not None:
                self.total = total
            self._render(force=True)

    def _clear(self):
        if self.active:
            self.stream.write("\r\033[2K")
            self.stream.flush()
            self.active = False

    def _render(self, *, force=False):
        now = time.monotonic()
        if (
            not self.interactive
            and not force
            and now - self.last_plain < self.plain_interval
        ):
            return
        seconds = int(now - self.started)
        label = f"{self.phase} {self.component}".strip()
        line = f"{label} | {self.completed}/{self.total} components | {seconds // 60}m{seconds % 60:02}s"
        if self.log:
            line += f" | log: {self.log}"
        if self.interactive:
            width = max(20, shutil.get_terminal_size((100, 24)).columns - 1)
            line = "|/-\\"[self.frame % 4] + " " + line
            self._clear()
            self.stream.write(line[:width])
            self.active = True
            self.frame += 1
        else:
            self.stream.write(line + "\n")
            self.last_plain = now
        self.stream.flush()

    def message(self, text):
        with self.lock:
            self._clear()
            self.output.write(text + "\n")
            self.output.flush()

    def _animate(self):
        while not self.event.wait(self.interval):
            with self.lock:
                self._render()

    def start(self):
        if self.thread is None:
            self.event.clear()
            self.thread = threading.Thread(
                target=self._animate, name="setup-progress", daemon=True
            )
            self.thread.start()

    def stop(self):
        if self.thread is not None:
            self.event.set()
            self.thread.join()
            self.thread = None
        with self.lock:
            self._clear()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_exc):
        self.stop()
