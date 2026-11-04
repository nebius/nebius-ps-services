from pathlib import Path

import pytest
from typer.testing import CliRunner

from nebius_cxcli import cli
from nebius_cxcli import wireguard_network_commands as commands


@pytest.mark.parametrize("mode", ["reconcile-network", "refresh-client-conf", "verify-client"])
def test_network_modes_dispatch_with_selected_inputs(monkeypatch, mode):
    calls = []

    def handle(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "PASS"} if mode == "verify-client" else 1370

    name = {
        "reconcile-network": "reconcile",
        "refresh-client-conf": "refresh",
        "verify-client": "verify",
    }[mode]
    monkeypatch.setattr(commands, name, handle)
    args = [
        "wireguard",
        f"--{mode}",
        "config.yaml",
        "--component",
        "wireguard-gw@vpn",
        "--credentials-file",
        "credentials.json",
    ]
    expected = [Path("config.yaml"), "wireguard-gw@vpn"]
    kwargs = {}
    if mode != "reconcile-network":
        args += ["--client-config", "wg-client.conf"]
        expected.append(Path("wg-client.conf"))
    if mode == "verify-client":
        args += ["--target", "mk8s@test", "--kubeconfig", "kubeconfig", "--context", "test"]
        kwargs = {"target": "mk8s@test", "kubeconfig": Path("kubeconfig"), "context": "test"}
    expected.append("credentials.json")
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert calls == [(tuple(expected), kwargs)]


@pytest.mark.parametrize("status,exit_code", [("PASS", 0), ("PENDING", 1), ("FAIL", 1)])
def test_explicit_verification_requires_pass(monkeypatch, status, exit_code):
    monkeypatch.setattr(commands, "verify", lambda *a, **k: {"status": status})
    result = CliRunner().invoke(
        cli.app,
        ["wireguard", "--verify-client", "config.yaml", "--client-config", "wg-client.conf"],
    )
    assert result.exit_code == exit_code, result.output
    assert status in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["--reconcile-network", "config.yaml", "--verify-client", "config.yaml"],
        ["--refresh-client-conf", "config.yaml"],
        ["--verify-client", "config.yaml"],
        ["--reconcile-network", "config.yaml", "--client-config", "wg-client.conf"],
        ["--refresh-client-conf", "config.yaml", "--target", "mk8s@test"],
        ["--verify-client", "config.yaml", "--force"],
        ["--gen-client-conf", "config.yaml", "--context", "test"],
    ],
)
def test_network_mode_option_errors_precede_operations(monkeypatch, args):
    def unexpected(*a, **k):
        pytest.fail("Invalid options reached an operation")

    for name in ("reconcile", "refresh", "verify"):
        monkeypatch.setattr(commands, name, unexpected)
    result = CliRunner().invoke(cli.app, ["wireguard", *args])
    assert result.exit_code == 1, result.output
