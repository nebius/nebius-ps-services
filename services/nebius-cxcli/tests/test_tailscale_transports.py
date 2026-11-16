"""Vendor-shaped inputs through real readers and command dispatch, without network I/O."""

import io
import json
import subprocess
import time
import urllib.error
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from typer.testing import CliRunner

from nebius_cxcli import (
    generated_manifest,
    tailscale_api,
    tailscale_cli,
    tailscale_config,
    tailscale_retirement,
    tailscale_verify,
)
from nebius_cxcli.cli import app
from nebius_cxcli.tailscale_deploy import readiness
from test_deployment_campaign import paths
from test_tailscale import guest as guest_fixture
from test_tailscale import healthy, row

guest = guest_fixture


@pytest.fixture
def vendor():
    return json.loads((Path(__file__).parent / "fixtures/tailscale/observations.json").read_text())


@pytest.fixture
def api_transport(tmp_path, vendor):
    api = tailscale_api.TailscaleAPI(row()["tailscale"], base_dir=tmp_path)
    api.token, api.expiry = "synthetic-test-token", time.monotonic() + 3600
    calls = []

    def open_response(request, *, timeout):
        assert timeout == 30
        calls.append((request.method, request.full_url))
        assert request.method == "GET", "Read-only checks must not mutate tailnet state"
        path = request.full_url.removeprefix(tailscale_api.BASE)
        if path.endswith("/routes"):
            value = vendor["routes"]
        elif path.endswith("/devices?fields=default"):
            value = {"devices": [vendor["device"]]}
        else:
            value = {**vendor["device"], "nodeId": path.rsplit("/", 1)[-1]}
        stream = io.BytesIO(json.dumps(value).encode())
        stream.headers = {"ETag": '"fixture"'}
        return stream

    api.opener = NS(open=open_response)
    return api, calls


def test_guest_reads_status_and_preferences(guest, vendor, monkeypatch):
    def run(args):
        if args == ["tailscale", "status", "--json", "--peers=false"]:
            return json.dumps(vendor["guest_status"])
        assert args == ["tailscale", "debug", "prefs"]
        return json.dumps(vendor["prefs"])

    monkeypatch.setattr(guest, "run", run)
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *a, **k: (
            "1\n" if str(path) == "/proc/sys/net/ipv4/ip_forward" else original(path, *a, **k)
        ),
    )
    observed = guest.observation()
    assert observed["stateful_filtering"] is True
    assert observed["approved_routes"] == ["10.20.0.0/24"]
    assert readiness(None, observed, tailscale_config.desired(row(), "a"))
    vendor["guest_status"]["Self"]["AllowedIPs"] = ["100.64.0.1/32"]
    assert not readiness(None, guest.observation(), tailscale_config.desired(row(), "a"))


def test_api_readiness_uses_device_and_enabled_routes(api_transport, vendor):
    api, calls = api_transport
    intent = tailscale_config.desired(row(), "a")
    assert readiness(api, healthy(), intent)
    assert [url.rsplit("/", 1)[-1] for _, url in calls] == ["node-a", "routes"]
    vendor["routes"]["enabledRoutes"] = []
    assert not readiness(api, healthy(), intent)
    vendor["routes"]["enabledRoutes"] = intent["routes"]
    vendor["device"]["authorized"] = False
    assert not readiness(api, healthy(), intent)


@pytest.mark.parametrize("field", ["advertisedRoutes", "enabledRoutes"])
def test_api_approval_never_erases_unexpected_routes(api_transport, vendor, field):
    api, calls = api_transport
    vendor["routes"][field].append("10.30.0.0/24")
    with pytest.raises(RuntimeError, match="no route approval was changed"):
        api.approve("node-a", ["10.20.0.0/24"])
    assert len(calls) == 1


@pytest.fixture
def client_transport(vendor, monkeypatch):
    calls = []
    monkeypatch.setattr(tailscale_verify.shutil, "which", lambda _: "/fixture/tailscale")
    monkeypatch.setattr(tailscale_verify.platform, "system", lambda: "Linux")

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] in {10, 15}
        if args[1:] == ["status", "--json"]:
            output = json.dumps(vendor["client_status"])
        else:
            assert args[:4] == ["ip", "-json", "route", "get"]
            output = '[{"dev":"tailscale0"}]'
        return NS(returncode=0, stdout=output)

    monkeypatch.setattr(tailscale_verify.subprocess, "run", run)
    return calls


def test_client_uses_primary_routes_and_linux_interface(client_transport, vendor):
    status = tailscale_verify.client_status()
    assert tailscale_verify.selected_router(status, "10.20.0.1", {"node-a", "node-b"}) == "node-a"
    assert tailscale_verify.verify_os_route("10.20.0.1", status, "node-a") == "tailscale0"
    vendor["client_status"]["Peer"]["nodekey:fixture-a"]["PrimaryRoutes"] = None
    with pytest.raises(RuntimeError, match="no selected subnet router"):
        tailscale_verify.selected_router(tailscale_verify.client_status(), "10.20.0.1", {"node-a"})


@pytest.fixture
def cli_context(tmp_path, monkeypatch, api_transport):
    config_row = row()
    config_row["tailscale"]["verification"] = {
        "targets": [{"url": "https://private.example/readyz"}]
    }
    project = paths(tmp_path)
    records = {
        m: {"node_id": "node-" + m, "instance_id": "vm-" + m, "phase": "READY"} for m in ("a", "b")
    }
    record = {"tailnet": "example.ts.net", "members": records}
    machines = {m: NS(call=lambda _op, member=m: healthy(member)) for m in records}
    api, _calls = api_transport
    owner = NS(
        executor=NS(report_paths=project),
        api=lambda _: api,
        open=lambda: NS(value={"gateways": {"ts": record}}),
        raw_outputs=lambda: {"tailscale_gateways": {"ts": {"members": records}}},
    )
    cli = NS(_terraform_runtime_env=lambda _: {})
    monkeypatch.setattr(tailscale_cli, "load", lambda *_: (cli, {}, project, config_row))
    monkeypatch.setattr(tailscale_cli, "credential_api", lambda *_: api)
    monkeypatch.setattr(
        tailscale_cli,
        "operation",
        lambda *_a, **_k: nullcontext((owner, config_row, record, machines)),
    )
    monkeypatch.setattr(tailscale_cli, "TailscaleDeployment", lambda _: nullcontext(owner))
    monkeypatch.setattr(tailscale_cli, "outputs", lambda *_: records)
    monkeypatch.setattr(tailscale_cli, "ComputeHostIdentityClient", lambda _: nullcontext(None))
    monkeypatch.setattr(generated_manifest, "load_generated_manifest", lambda _: {})
    monkeypatch.setattr(
        tailscale_retirement,
        "guest_for",
        lambda _o, _r, binding, _s: machines[binding["node_id"][-1]],
    )
    return project, machines


@pytest.mark.parametrize("command", ["status", "doctor"])
@pytest.mark.parametrize("unavailable", [False, True])
def test_cli_observes_fresh_status_and_doctor(cli_context, command, unavailable):
    _project, machines = cli_context
    if unavailable:

        def fail(_op):
            raise RuntimeError("PRIVATE-SENTINEL")

        machines["b"].call = fail
    result = CliRunner().invoke(app, ["tailscale", command, "config.yaml", "--json"])
    assert result.exit_code == 0, result.output
    value = json.loads(result.stdout)
    assert [m["status"] for m in value["members"]] == [
        "READY",
        "PENDING" if unavailable else "READY",
    ]
    assert ("guest" in value["members"][0]) == (command == "doctor")
    assert "PRIVATE-SENTINEL" not in result.output


@pytest.mark.parametrize("denied", [False, True])
def test_cli_auth_check_reads_api_without_writes(cli_context, api_transport, denied):
    api, calls = api_transport
    if denied:

        def fail(request, **_kwargs):
            raise urllib.error.HTTPError(request.full_url, 403, "PRIVATE-SENTINEL", {}, None)

        api.opener.open = fail
    result = CliRunner().invoke(app, ["tailscale", "auth", "check", "config.yaml", "--json"])
    assert result.exit_code == (1 if denied else 0), result.output
    if denied:
        assert "HTTP 403" in result.output
    else:
        assert json.loads(result.stdout)["status"] == "PASS"
        assert calls[0][1].endswith("/devices?fields=default")
    assert "PRIVATE-SENTINEL" not in result.output


@pytest.mark.parametrize("timeout", [False, True])
def test_cli_verify_uses_real_client_boundary(cli_context, client_transport, monkeypatch, timeout):
    project, _machines = cli_context
    monkeypatch.setattr(
        tailscale_verify.socket,
        "getaddrinfo",
        lambda *_a, **_k: [(2, 1, 6, "", ("10.20.0.1", 443))],
    )
    monkeypatch.setattr(
        tailscale_verify, "probe", lambda *_a: {"tls": "verified", "bytes": 1, "http_status": 200}
    )
    if timeout:

        def run(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(["tailscale"], 15, output=b"PRIVATE-SENTINEL")

        monkeypatch.setattr(tailscale_verify.subprocess, "run", run)
    result = CliRunner().invoke(app, ["tailscale", "verify", "config.yaml", "--json"])
    assert result.exit_code == (1 if timeout else 0), result.output
    saved = json.loads((project.reports_dir / "tailscale-client-verification.json").read_text())
    if timeout:
        assert "retry verification" in " ".join(result.output.split())
        assert saved["status"] == "PENDING"
    else:
        assert json.loads(result.stdout) == saved
        assert saved["status"] == "PASS"
    assert "PRIVATE-SENTINEL" not in result.output
