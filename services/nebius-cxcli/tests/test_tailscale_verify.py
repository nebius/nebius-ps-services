"""Real client response readers with offline command/HTTP transports."""

import json
import subprocess
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import tailscale_verify as verify


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(["tailscale"], 15, output=b"PRIVATE-SENTINEL"),
        OSError("PRIVATE-SENTINEL"),
    ],
)
def test_client_status_command_failures_are_sanitized(monkeypatch, error):
    monkeypatch.setattr(verify.shutil, "which", lambda _: "/test/tailscale")

    def run(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(verify.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Tailscale.*retry") as raised:
        verify.client_status()
    assert "PRIVATE-SENTINEL" not in str(raised.value)


@pytest.mark.parametrize(
    "output", ["PRIVATE-SENTINEL", "[]", "null", '{"BackendState":"Running","Peer":[]}']
)
def test_invalid_client_response_is_sanitized(monkeypatch, output):
    monkeypatch.setattr(verify.shutil, "which", lambda _: "/test/tailscale")
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_k: NS(returncode=0, stdout=output))
    with pytest.raises(RuntimeError, match="Tailscale.*response") as raised:
        verify.client_status()
    assert "PRIVATE-SENTINEL" not in str(raised.value)


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_os_route_timeout_is_sanitized(monkeypatch, system):
    monkeypatch.setattr(verify.platform, "system", lambda: system)

    def run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["route"], 10, output=b"PRIVATE-SENTINEL")

    monkeypatch.setattr(verify.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="route.*retry") as raised:
        verify.verify_os_route(
            "10.20.0.1", {"Peer": {"p": {"ID": "n", "TailscaleIPs": ["100.64.0.1"]}}}, "n"
        )
    assert "PRIVATE-SENTINEL" not in str(raised.value)


@pytest.mark.parametrize(
    "output", ["PRIVATE-SENTINEL", "null", "{}", '["tailscale0"]', '[{"dev":[]}]']
)
def test_malformed_linux_route_is_sanitized(monkeypatch, output):
    monkeypatch.setattr(verify.platform, "system", lambda: "Linux")
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_k: NS(returncode=0, stdout=output))
    with pytest.raises(RuntimeError, match="route.*response") as raised:
        verify.verify_os_route(
            "10.20.0.1", {"Peer": {"p": {"ID": "n", "TailscaleIPs": ["100.64.0.1"]}}}, "n"
        )
    assert "PRIVATE-SENTINEL" not in str(raised.value)


@pytest.mark.parametrize(
    "peer", [None, [], {"ID": []}, {"PrimaryRoutes": ["invalid"]}, {"TailscaleIPs": [3]}]
)
def test_malformed_peer_is_sanitized(monkeypatch, peer):
    monkeypatch.setattr(verify.shutil, "which", lambda _: "/test/tailscale")
    monkeypatch.setattr(
        verify.subprocess,
        "run",
        lambda *_a, **_k: NS(
            returncode=0, stdout=json.dumps({"BackendState": "Running", "Peer": {"p": peer}})
        ),
    )
    with pytest.raises(RuntimeError, match="Invalid Tailscale status response"):
        verify.client_status()


@pytest.mark.parametrize(
    "returncode, output", [(1, "PRIVATE-SENTINEL"), (0, "x" * (4 * 1024 * 1024 + 1))]
)
def test_failed_or_oversized_client_response_is_sanitized(monkeypatch, returncode, output):
    monkeypatch.setattr(verify.shutil, "which", lambda _: "/test/tailscale")
    monkeypatch.setattr(
        verify.subprocess, "run", lambda *_a, **_k: NS(returncode=returncode, stdout=output)
    )
    with pytest.raises(RuntimeError, match="client is unavailable") as raised:
        verify.client_status()
    assert "PRIVATE-SENTINEL" not in str(raised.value)


def test_disconnected_client_has_actionable_error(monkeypatch):
    monkeypatch.setattr(verify.shutil, "which", lambda _: "/test/tailscale")
    monkeypatch.setattr(
        verify.subprocess,
        "run",
        lambda *_a, **_k: NS(
            returncode=0, stdout='{"BackendState":"NeedsLogin","Peer":null,"CurrentTailnet":null}'
        ),
    )
    with pytest.raises(RuntimeError, match="not connected"):
        verify.client_status()


def test_null_router_addresses_are_reported_as_missing():
    with pytest.raises(RuntimeError, match="no IPv4 tailnet address"):
        verify.verify_os_route("10.20.0.1", {"Peer": {"p": {"ID": "n", "TailscaleIPs": None}}}, "n")


def test_macos_route_parser_checks_both_destinations(monkeypatch):
    calls = []
    monkeypatch.setattr(verify.platform, "system", lambda: "Darwin")

    def run(args, **_kwargs):
        calls.append(args)
        return NS(returncode=0, stdout="   route to: 10.20.0.1\n  interface: utun4\n")

    monkeypatch.setattr(verify.subprocess, "run", run)
    assert (
        verify.verify_os_route(
            "10.20.0.1", {"Peer": {"p": {"ID": "n", "TailscaleIPs": ["100.64.0.1"]}}}, "n"
        )
        == "utun4"
    )
    assert calls == [["route", "-n", "get", "10.20.0.1"], ["route", "-n", "get", "100.64.0.1"]]
