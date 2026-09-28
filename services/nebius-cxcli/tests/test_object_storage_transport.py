"""Real exec workers against a loopback-only S3 fault server; no cloud credentials."""

from __future__ import annotations

import hashlib
import socket
import threading
import time
from contextlib import suppress
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nebius_cxcli import object_storage_transport as module
from nebius_cxcli.object_storage_errors import ObjectStorageError
from test_deployment_state import settings as _settings


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.objects = {}
        self.requests = []
        self.connections = 0
        self.drop_put = False
        self.stall_put = None
        self.fail_put = None
        self.stall = threading.Event()
        self.resume = threading.Event()
        self.truncate = False

    def get_request(self):
        result = super().get_request()
        self.connections += 1
        return result

    def handle_error(self, request, client_address):
        pass  # Deliberate client termination is part of these fault trials.


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def respond(self, status, body=b"", *, etag=None, metadata=None, size=None):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body) if size is None else size))
        if etag:
            self.send_header("ETag", '"' + etag + '"')
        for key, value in (metadata or {}).items():
            self.send_header("x-amz-meta-" + key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
            self.wfile.flush()

    def handle_s3(self):
        state = self.server
        state.requests.append((self.command, self.path, dict(self.headers)))
        old = state.objects.get(self.path)
        if self.command in {"HEAD", "GET"}:
            if old is None:
                self.respond(404)
                return
            body, etag, metadata = old
            if self.command == "GET" and self.path.endswith("/stall"):
                self.respond(200, b"", etag=etag, size=len(body))
                state.stall.set()
                state.resume.wait(5)
                with suppress(OSError):
                    self.wfile.write(body)
                return
            self.respond(
                200,
                body[:1] if state.truncate else body,
                etag=etag,
                metadata=metadata,
                size=len(body),
            )
            if state.truncate:
                self.close_connection = True
            return
        if self.command == "PUT":
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if state.fail_put:
                self.respond(
                    state.fail_put,
                    b"<Error><Code>AccessDenied</Code><Message>private-value</Message></Error>",
                )
                return
            if (self.headers.get("If-None-Match") == "*" and old is not None) or (
                self.headers.get("If-Match")
                and (old is None or self.headers["If-Match"].strip('"') != old[1])
            ):
                self.respond(412, b"<Error><Code>PreconditionFailed</Code></Error>")
                return
            stall, state.stall_put = state.stall_put, None
            if stall == "uncommitted":
                state.stall.set()
                state.resume.wait(5)
                self.close_connection = True
                return
            etag = hashlib.sha256(body).hexdigest()
            metadata = {
                k.lower().removeprefix("x-amz-meta-"): v
                for k, v in self.headers.items()
                if k.lower().startswith("x-amz-meta-")
            }
            state.objects[self.path] = (body, etag, metadata)
            if stall == "committed":
                state.stall.set()
                state.resume.wait(5)
                self.close_connection = True
                return
            if state.drop_put:
                state.drop_put = False
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.respond(200, etag=etag)
        elif self.command == "DELETE":
            if old is None or self.headers.get("If-Match", "").strip('"') != old[1]:
                self.respond(412, b"<Error><Code>PreconditionFailed</Code></Error>")
                return
            del state.objects[self.path]
            self.respond(204)

    do_HEAD = do_GET = do_PUT = do_DELETE = handle_s3


@pytest.fixture
def s3(monkeypatch):
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "NEBIUS_S3_ACCESS_KEY_ID",
        "NEBIUS_S3_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fixture-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fixture-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "ambient-token")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "ambient-legacy-token")
    monkeypatch.setenv("AWS_DEFAULT_OUTPUT", "text")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "10")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    server = Server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = replace(_settings(), endpoint=f"http://127.0.0.1:{server.server_port}")
    try:
        with module.object_storage_scope():
            yield server, settings
    finally:
        server.resume.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.integration
def test_worker_reuses_client_and_scope_closes_process(s3):
    server, settings = s3
    with module.object_storage_transport(settings) as first:
        created = first.request(
            "put_object", "key", body=b"{}", create_only=True, metadata={"owner": "fixture"}
        )
        process = first._process
    with module.object_storage_transport(settings) as second:
        assert second is first
        for _ in range(5):
            observed = second.request("head_object", "key")
            assert observed.etag == created.etag
            assert observed.metadata == {"owner": "fixture"}
        assert second.request("get_object", "key").body == b"{}"
        with pytest.raises(ObjectStorageError, match="PreconditionFailed"):
            second.request("put_object", "key", body=b"{}", create_only=True)
        second.request("delete_object", "key", etag=created.etag)
        assert second._process is process
        assert server.connections == 1
        assert all("X-Amz-Security-Token" not in headers for _, _, headers in server.requests)
    first.close()
    assert process.poll() is not None


@pytest.mark.integration
def test_stalled_state_body_deadline_reaps_worker(s3):
    server, settings = s3
    with module.object_storage_transport(settings) as state:
        state.request("put_object", "stall", body=b"{}", create_only=True)
        old_process = state._process
        failures = []

        def blocked():
            try:
                state.request("get_object", "stall", deadline=time.monotonic() + 1)
            except ObjectStorageError as exc:
                failures.append(exc.code)

        thread = threading.Thread(target=blocked)
        started = time.monotonic()
        thread.start()
        assert server.stall.wait(2)
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert time.monotonic() - started < 3
        assert failures == ["request deadline exceeded"]
        assert old_process.poll() is not None
        # Readback starts only after the previous local writer has stopped.
        assert state.request("head_object", "stall").etag
        assert state._process.pid != old_process.pid


@pytest.mark.integration
@pytest.mark.parametrize("fault", ["oversize", "truncated", "access"])
def test_bounded_bodies_and_safe_service_errors(s3, fault):
    server, settings = s3
    with module.object_storage_transport(settings) as transport:
        transport.request("put_object", "key", body=b'{"test":true}', create_only=True)
        if fault == "oversize":
            with pytest.raises(ObjectStorageError, match="object too large"):
                transport.request("get_object", "key", max_bytes=4)
        elif fault == "truncated":
            server.truncate = True
            with pytest.raises(ObjectStorageError, match="transport connection closed"):
                transport.request("get_object", "key")
        else:
            server.fail_put = 403
            with pytest.raises(ObjectStorageError, match="AccessDenied") as caught:
                transport.request("put_object", "another", body=b"{}", create_only=True)
            assert "private-value" not in str(caught.value)
            assert len([r for r in server.requests if r[1].endswith("/another")]) == 1


@pytest.mark.integration
def test_worker_is_reaped_after_parent_interrupt(s3, monkeypatch):
    _, settings = s3
    with module.object_storage_transport(settings) as transport:
        transport.request("put_object", "key", body=b"{}", create_only=True)
        process = transport._process

        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(module, "receive_frame", interrupted)
        with pytest.raises(KeyboardInterrupt):
            transport.request("head_object", "key")
        assert process.poll() is not None


def test_credentials_choose_a_complete_pair_without_session_tokens(monkeypatch):
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "NEBIUS_S3_ACCESS_KEY_ID",
        "NEBIUS_S3_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NEBIUS_S3_ACCESS_KEY_ID", " fixture-access ")
    monkeypatch.setenv("NEBIUS_S3_SECRET_ACCESS_KEY", " fixture-secret ")
    credentials = module.StorageCredentials.from_environment()
    assert credentials == module.StorageCredentials("fixture-access", "fixture-secret")
    assert "fixture" not in repr(credentials)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "partial")
    with pytest.raises(ObjectStorageError, match="invalid credentials"):
        module.StorageCredentials.from_environment()


def test_credentials_alias_export_clears_tokens_and_rejects_mixed_pairs(monkeypatch):
    from nebius_cxcli import cli

    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "NEBIUS_S3_ACCESS_KEY_ID",
        "NEBIUS_S3_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NEBIUS_S3_ACCESS_KEY_ID", " fixture-access ")
    monkeypatch.setenv("NEBIUS_S3_SECRET_ACCESS_KEY", " fixture-secret ")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "fixture-token")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "fixture-token")
    cli._ensure_backend_s3_env_aliases()
    assert "AWS_SESSION_TOKEN" not in module.os.environ
    assert "AWS_SECURITY_TOKEN" not in module.os.environ
    assert module.os.environ["AWS_ACCESS_KEY_ID"] == "fixture-access"
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
    with pytest.raises(ObjectStorageError):
        cli._ensure_backend_s3_env_aliases()


@pytest.mark.integration
@pytest.mark.parametrize("interrupted", [False, True])
def test_scope_reaps_worker_on_normal_and_exception_exit(s3, interrupted):
    _, settings = s3
    token = module._SCOPE.set(None)
    try:
        with suppress(KeyboardInterrupt), module.object_storage_scope():
            with module.object_storage_transport(settings) as transport:
                transport.request("put_object", "scoped", body=b"{}", create_only=True)
                process = transport._process
            if interrupted:
                raise KeyboardInterrupt
        assert process.poll() is not None
        assert transport.closed
    finally:
        module._SCOPE.reset(token)


def test_scope_cleanup_preserves_primary_error(monkeypatch):
    class Broken:
        def close(self):
            raise RuntimeError("cleanup")

    with pytest.raises(ValueError, match="primary"), module.object_storage_scope():
        module._SCOPE.get()[()] = Broken()
        raise ValueError("primary")


def test_runtime_credential_export_removes_ambient_session_tokens(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from nebius_cxcli import cli

    for name in ("AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.setenv(name, "fixture-token")
    # Protect all environment changes made by the production exporter.
    for name in (
        "NEBIUS_SA_ID",
        "NEBIUS_AUTH_PUBLIC_KEY_ID",
        "NEBIUS_AUTH_PRIVATE_KEY_PEM",
        "NEBIUS_AUTH_PRIVATE_KEY_FILE",
        cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV,
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "NEBIUS_S3_ACCESS_KEY_ID",
        "NEBIUS_S3_SECRET_ACCESS_KEY",
        "NEBIUS_AUTH_CREDENTIALS_FILE",
        "NEBIUS_IAM_TOKEN",
        "CXCLI_NEBIUS_DELEGATE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    cli._export_runtime_auth_material(
        SimpleNamespace(
            service_account_id="fixture-sa",
            auth_public_key_id="fixture-key",
            private_key_pem="fixture-key",
            private_key_file=tmp_path / "unused",
            project_id="fixture-project",
            s3_access_key_id="fixture-access",
            s3_secret_access_key="fixture-secret",
        )
    )
    assert "AWS_SESSION_TOKEN" not in module.os.environ
    assert "AWS_SECURITY_TOKEN" not in module.os.environ
    assert module.os.environ["AWS_SECRET_ACCESS_KEY"] == "fixture-secret"


def test_unreaped_worker_cannot_be_replaced_within_invocation(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fixture-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fixture-secret")
    with module.object_storage_scope():
        with module.object_storage_transport(_settings()) as first:
            first.closed = True
            first._process = object()  # Retained handle after a failed kill/join.
        try:
            with (
                pytest.raises(RuntimeError, match="shutdown is unresolved"),
                module.object_storage_transport(_settings()),
            ):
                pytest.fail("created another writer")
        finally:
            first._process = None
