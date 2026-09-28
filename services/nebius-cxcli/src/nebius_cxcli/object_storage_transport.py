"""Synchronous S3 operations with pooled clients and killable request deadlines."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .object_storage_errors import ObjectStorageError
from .object_storage_protocol import MAX_OBJECT_BYTES, receive_frame, send_frame

if TYPE_CHECKING:
    from .terraform_backend import TerraformBackendSettings


@dataclass(frozen=True)
class ObjectStorageResponse:
    etag: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)
    body: bytes = field(default=b"", repr=False)


@dataclass(frozen=True)
class StorageCredentials:
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    @classmethod
    def from_environment(cls, extra_env: Mapping[str, str] | None = None) -> StorageCredentials:
        env = {**os.environ, **(extra_env or {})}
        # Select a complete pair from one namespace, never mix two identities.
        for prefix in ("AWS", "NEBIUS_S3"):
            access, secret = (
                env.get(prefix + "_ACCESS_KEY_ID", "").strip(),
                env.get(prefix + "_SECRET_ACCESS_KEY", "").strip(),
            )
            if access or secret:
                if not access or not secret:
                    raise ObjectStorageError("invalid credentials")
                return cls(access, secret)
        raise ObjectStorageError("invalid credentials")


class ObjectStorageTransport:
    """One sequential execution lane. Its caller owns lifetime and recovery policy."""

    def __init__(self, settings: TerraformBackendSettings, credentials: StorageCredentials) -> None:
        self.settings = settings
        self.credentials = credentials
        self._process: subprocess.Popen[bytes] | None = None
        self._channel: socket.socket | None = None
        self._lock = threading.Lock()
        self.closed = False

    def _start(self, deadline: float) -> None:
        if self._process is not None:
            return
        parent, child = socket.socketpair()
        self._channel = parent
        # Preserve networking/CA configuration, exclude AWS discovery and token settings.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "NEBIUS_"))}
        if os.environ.get("AWS_CA_BUNDLE"):
            env["AWS_CA_BUNDLE"] = os.environ["AWS_CA_BUNDLE"]
        env.update(
            AWS_CONFIG_FILE=os.devnull,
            AWS_SHARED_CREDENTIALS_FILE=os.devnull,
            AWS_EC2_METADATA_DISABLED="true",
        )
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-m", "nebius_cxcli.object_storage_worker", str(child.fileno())],
                pass_fds=(child.fileno(),),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        finally:
            child.close()
        send_frame(
            parent,
            {
                "endpoint": self.settings.endpoint,
                "region": self.settings.region_id,
                "access_key": self.credentials.access_key,
                "secret_key": self.credentials.secret_key,
            },
            deadline=deadline,
        )
        ready, body = receive_frame(parent, deadline=deadline)
        if ready != {"ready": True} or body:
            raise ValueError("Invalid S3 worker startup response")

    def _stop(self, *, graceful: bool = False) -> None:
        """Never return recovery control while this lane's previous writer is alive."""
        if self._channel is not None:
            self._channel.close()
            self._channel = None
        process = self._process
        if process is not None:
            if graceful:
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.25)
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Retain the process handle and fail closed; another request cannot start.
                self.closed = True
                raise RuntimeError("Object Storage worker could not be stopped") from None
            self._process = None

    def close(self) -> None:
        with self._lock:
            self.closed = True
            self._stop(graceful=True)

    def request(
        self,
        operation: str,
        key: str,
        *,
        body: bytes = b"",
        metadata: Mapping[str, str] | None = None,
        etag: str | None = None,
        create_only: bool = False,
        deadline: float | None = None,
        max_bytes: int = MAX_OBJECT_BYTES,
    ) -> ObjectStorageResponse:
        deadline = min(deadline if deadline is not None else float("inf"), time.monotonic() + 60)
        if operation not in {"get_object", "head_object", "put_object", "delete_object"}:
            raise ValueError("Unsupported Object Storage operation")
        if not key or len(body) > MAX_OBJECT_BYTES or not 0 <= max_bytes <= MAX_OBJECT_BYTES:
            raise ValueError("Invalid Object Storage request size or key")
        if operation in {"put_object", "delete_object"} and (
            bool(etag) == create_only or operation == "delete_object" and create_only
        ):
            raise ValueError("Object Storage mutation requires exactly one supported condition")
        if not self._lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise ObjectStorageError("request deadline exceeded")
        try:
            if self.closed:
                raise ObjectStorageError("worker unavailable")
            if time.monotonic() >= deadline:
                raise ObjectStorageError("request deadline exceeded")
            try:
                self._start(deadline)
                assert self._channel is not None
                send_frame(
                    self._channel,
                    {
                        "operation": operation,
                        "bucket": self.settings.bucket,
                        "key": key,
                        "metadata": dict(metadata or {}),
                        "etag": etag,
                        "create_only": create_only,
                        "max_bytes": max_bytes,
                    },
                    body,
                    deadline=deadline,
                )
                response, data = receive_frame(self._channel, deadline=deadline)
                if time.monotonic() >= deadline:
                    raise TimeoutError
            except TimeoutError:
                self._stop()
                raise ObjectStorageError("request deadline exceeded") from None
            except (OSError, EOFError, ValueError, subprocess.SubprocessError):
                self._stop()
                raise ObjectStorageError("worker unavailable") from None
            except BaseException:
                self._stop()
                raise
            if "error" in response:
                raise ObjectStorageError(response["error"], status=response.get("status"))
            if (
                set(response) != {"etag", "metadata"}
                or not isinstance(response["etag"], str)
                or not isinstance(response["metadata"], dict)
                or any(
                    not isinstance(k, str) or not isinstance(v, str)
                    for k, v in response["metadata"].items()
                )
                or len(data) > max_bytes
            ):
                self._stop()
                raise ObjectStorageError("invalid response")
            return ObjectStorageResponse(response["etag"].strip('"'), response["metadata"], data)
        finally:
            self._lock.release()


_SCOPE: ContextVar[dict[tuple[Any, ...], ObjectStorageTransport] | None] = ContextVar(
    "s3_transport_scope", default=None
)


@contextmanager
def object_storage_scope() -> Iterator[None]:
    """Lazy command ownership, reused by nested lifecycle/library operations."""
    if _SCOPE.get() is not None:
        yield
        return
    transports: dict[tuple[Any, ...], ObjectStorageTransport] = {}
    token = _SCOPE.set(transports)
    try:
        yield
    finally:
        primary_error = sys.exc_info()[0] is not None
        _SCOPE.reset(token)
        # Attempt every close even if one worker cannot be reaped.
        failure = None
        for transport in transports.values():
            try:
                transport.close()
            except Exception as exc:
                failure = exc
        if failure is not None:
            if not primary_error:
                raise failure
            logging.getLogger(__name__).warning("Object Storage worker shutdown is unresolved")


def _transport(
    settings: TerraformBackendSettings, extra_env: Mapping[str, str] | None = None
) -> ObjectStorageTransport:
    credentials = StorageCredentials.from_environment(extra_env)
    scope = _SCOPE.get()
    key = (settings, credentials)
    if scope is None:
        return ObjectStorageTransport(settings, credentials)
    current = scope.get(key)
    if current is not None and current.closed and current._process is not None:
        raise RuntimeError("Object Storage worker shutdown is unresolved")
    if current is None or current.closed:
        current = ObjectStorageTransport(settings, credentials)
        scope[key] = current
    return current


@contextmanager
def object_storage_transport(
    settings: TerraformBackendSettings, *, extra_env: Mapping[str, str] | None = None
) -> Iterator[ObjectStorageTransport]:
    transport = _transport(settings, extra_env)
    try:
        yield transport
    finally:
        if _SCOPE.get() is None:
            transport.close()
