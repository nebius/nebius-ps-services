from __future__ import annotations

import json
import ssl
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import nat_gateway_probe_guest, tailscale_verify


@pytest.fixture
def tls_context(monkeypatch):
    context = NS(minimum_version=ssl.TLSVersion.MINIMUM_SUPPORTED)
    handshakes = []

    def wrap_socket(sock, *, server_hostname):
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
        handshakes.append(server_hostname)
        return sock

    context.wrap_socket = wrap_socket
    monkeypatch.setattr(ssl, "create_default_context", lambda **kwargs: context)
    return handshakes


def test_tailscale_probe_sets_tls_floor_before_handshake(monkeypatch, tmp_path, tls_context):
    raw = NS(close=lambda: None)
    response = NS(status=200, read=lambda limit: b"ready")
    connection = NS(
        request=lambda *args, **kwargs: None,
        getresponse=lambda: response,
        close=lambda: None,
    )
    monkeypatch.setattr(tailscale_verify.socket, "create_connection", lambda *a, **k: raw)
    monkeypatch.setattr(tailscale_verify.http.client, "HTTPSConnection", lambda *a, **k: connection)

    result = tailscale_verify.probe(
        {"url": "https://target.invalid/ready"}, "192.0.2.1", tmp_path, 5
    )

    assert result["tls"] == "verified"
    assert tls_context == ["target.invalid"]


def test_nat_probe_sets_tls_floor_before_handshake(monkeypatch, tls_context):
    read_text = Path.read_text

    def guest_text(path, *args, **kwargs):
        if str(path) == "/proc/sys/kernel/random/boot_id":
            return "fixture-boot"
        if str(path) == "/proc/stat":
            return "btime 123\n"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guest_text)
    raw = NS(connect=lambda address: None, settimeout=lambda value: None, close=lambda: None)
    response = NS(status=200, read=lambda limit: json.dumps({"ip": "203.0.113.9"}).encode())
    connection = NS(
        request=lambda *args, **kwargs: None,
        getresponse=lambda: response,
        close=lambda: None,
    )
    monkeypatch.setattr(nat_gateway_probe_guest.socket, "socket", lambda *a, **k: raw)
    monkeypatch.setattr(
        nat_gateway_probe_guest.http.client, "HTTPSConnection", lambda *a, **k: connection
    )

    result = nat_gateway_probe_guest.measure(
        {
            "run_id": "fixture",
            "activation": "fixture",
            "endpoint_ip": "192.0.2.1",
            "expected_ip": "203.0.113.9",
        }
    )

    assert result["ok"] is True
    assert tls_context == ["api.ipify.org"]
