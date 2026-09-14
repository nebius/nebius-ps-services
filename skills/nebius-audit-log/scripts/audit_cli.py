"""Bounded Nebius CLI transport; never surface provider output as an error."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from typing import Any

CALL_SECONDS = 30.0
CALL_BYTES = 8 * 1024 * 1024
TOTAL_BYTES = 32 * 1024 * 1024


class AuditError(Exception):
    """An allowlisted, safe diagnostic, independent of provider stderr."""

    def __init__(self, code: str, stage: str, message: str):
        super().__init__(message)
        self.code = code
        self.stage = stage

    def record(self) -> dict[str, str]:
        return {"code": self.code, "stage": self.stage, "message": str(self)}


class InputError(AuditError):
    def __init__(self, message: str):
        super().__init__("invalid_arguments", "arguments", message)


EXIT_ERRORS = {
    4: ("configuration_error", "The selected Nebius CLI configuration is unavailable."),
    7: (
        "authentication_failed",
        "Nebius authentication failed; use the authentication owner to diagnose it.",
    ),
    12: ("deadline_exceeded", "The Nebius request exceeded its deadline."),
    52: ("deadline_exceeded", "An additional Nebius request exceeded its deadline."),
    13: ("not_found", "The requested Nebius resource was not found."),
    53: ("not_found", "An additional Nebius resource was not found."),
    15: (
        "permission_denied",
        "Nebius denied this operation for the selected caller and scope.",
    ),
    55: (
        "permission_denied",
        "Nebius denied an additional operation for the selected caller and scope.",
    ),
    16: ("resource_exhausted", "Nebius reported an exhausted service limit."),
    56: ("resource_exhausted", "Nebius reported an exhausted service limit."),
    20: ("unavailable", "The Nebius service is unavailable."),
    60: ("unavailable", "An additional Nebius service is unavailable."),
}


class Cli:
    """One environment snapshot, selected profile, and cumulative budget per query."""

    def __init__(self, timeout: float):
        self.env = dict(os.environ)
        self.binary = self.env.get("NEBIUS_BIN", "nebius")
        self.profile: str | None = None
        self.deadline = time.monotonic() + timeout
        self.bytes_read = 0

    def run(self, command: list[str], stage: str) -> str:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AuditError(
                "deadline_exceeded", stage, "The overall query deadline was reached."
            )
        seconds = min(CALL_SECONDS, remaining)
        argv = [
            self.binary,
            *command,
            "--no-progress",
            "--no-check-update",
            "--no-browser",
            "--retries",
            "1",
            "--timeout",
            f"{seconds:.3f}s",
            "--auth-timeout",
            f"{seconds:.3f}s",
        ]
        if self.profile is not None:
            argv.extend(["--profile", self.profile])
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                start_new_session=True,
            )
        except OSError as exc:
            raise AuditError(
                "cli_unavailable", stage, "The Nebius CLI could not be started."
            ) from exc
        stdout = bytearray()
        count = 0
        end = time.monotonic() + seconds
        try:
            with selectors.DefaultSelector() as selector:
                assert proc.stdout is not None and proc.stderr is not None
                for stream in (proc.stdout, proc.stderr):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ)
                while selector.get_map():
                    wait = min(end, self.deadline) - time.monotonic()
                    if wait <= 0:
                        raise AuditError(
                            "deadline_exceeded",
                            stage,
                            "The CLI execution deadline was reached.",
                        )
                    for key, _ in selector.select(wait):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        count += len(chunk)
                        self.bytes_read += len(chunk)
                        if count > CALL_BYTES or self.bytes_read > TOTAL_BYTES:
                            raise AuditError(
                                "output_limit",
                                stage,
                                "The CLI output byte limit was reached.",
                            )
                        if key.fileobj is proc.stdout:
                            stdout.extend(chunk)
                wait = min(end, self.deadline) - time.monotonic()
                try:
                    status = proc.wait(timeout=max(0, wait))
                except subprocess.TimeoutExpired as exc:
                    raise AuditError(
                        "deadline_exceeded",
                        stage,
                        "The CLI execution deadline was reached.",
                    ) from exc
            if status:
                code, message = EXIT_ERRORS.get(
                    status, ("cli_failed", "The Nebius CLI command failed.")
                )
                raise AuditError(code, stage, message)
            try:
                return stdout.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AuditError(
                    "invalid_response", stage, "The CLI response is not valid UTF-8."
                ) from exc
        finally:
            # Also stop descendants holding inherited pipes, even if the parent exited.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()


def invalid_response(stage: str) -> AuditError:
    return AuditError(
        "invalid_response", stage, "Nebius returned an invalid or unsupported response."
    )


def object_value(value: Any, stage: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid_response(stage)
    return value
