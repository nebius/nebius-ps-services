"""Process-level checks for the credential command used by each kubectl call."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from nebius_cxcli import __main__ as entrypoint
from nebius_cxcli import cli, mk8s_exec


@pytest.mark.parametrize("args", [["--help"], ["--unknown"], ["--project-id"]])
def test_fast_command_keeps_full_cli_option_contract(args):
    runner = CliRunner()
    fast = runner.invoke(
        mk8s_exec.create_app(entrypoint._acquire_mk8s_credential),
        ["mk8s-token", *args],
        prog_name="nebius-cxcli",
    )
    full = runner.invoke(cli.app, ["mk8s-token", *args], prog_name="nebius-cxcli")
    assert fast.exit_code == full.exit_code
    assert fast.output == full.output


@pytest.mark.parametrize(
    "error", [TimeoutError("sensitive fixture"), RuntimeError("sensitive fixture")]
)
def test_fast_and_full_credential_errors_are_sanitized(monkeypatch, error):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(mk8s_exec, "_acquire_status", fail)
    runner = CliRunner()
    for app in (mk8s_exec.create_app(entrypoint._acquire_mk8s_credential), cli.app):
        result = runner.invoke(app, ["mk8s-token"])
        assert result.exit_code == 1
        assert result.stdout == ""
        assert "sensitive fixture" not in result.stderr
        assert "Unable to create an MK8s exec credential" in result.stderr


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["mk8s-token", "--help"], "fast"),
        (["--source-profile", "portable", "mk8s-token"], "full"),
        (["grafana", "import", "mk8s-token"], "full"),
        (["--version"], "full"),
        ([], "full"),
    ],
)
def test_entrypoint_routes_only_leading_credential_command(monkeypatch, args, expected):
    calls = []
    monkeypatch.setattr(sys, "argv", ["nebius-cxcli", *args])
    monkeypatch.setattr(mk8s_exec, "create_app", lambda _provider: lambda: calls.append("fast"))
    monkeypatch.setattr(cli, "main", lambda: calls.append("full"))
    entrypoint.main()
    assert calls == [expected]


def test_cold_entrypoint_uses_injected_provider(monkeypatch, capsys):
    calls = []

    def acquire(**kwargs):
        calls.append(kwargs)
        return {"token": "fixture-token"}

    monkeypatch.setattr(cli, "_acquire_mk8s_exec_credential_status", acquire)
    monkeypatch.setattr(
        sys,
        "argv",
        ["nebius-cxcli", "mk8s-token", "--project-id", "fixture", "--require-renewable-auth"],
    )
    with pytest.raises(SystemExit) as result:
        entrypoint.main()
    assert result.value.code == 0
    assert calls == [
        {
            "project_id": "fixture",
            "client_name": "",
            "endpoint": None,
            "require_renewable_auth": True,
        }
    ]
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == {"token": "fixture-token"}
    assert not captured.err


@pytest.mark.parametrize("unsafe", ["file-mode", "file-link", "directory-link", "lock-link"])
def test_credential_cache_rejects_unsafe_files_before_acquisition(tmp_path, monkeypatch, unsafe):
    folder = tmp_path / "private"
    folder.mkdir(mode=0o700)
    cache = folder / "credential.json"
    cache.write_text("{}")
    cache.chmod(0o600)
    if unsafe == "file-mode":
        cache.chmod(0o644)
    elif unsafe == "file-link":
        link = folder / "link.json"
        link.symlink_to(cache)
        cache = link
    elif unsafe == "directory-link":
        link = tmp_path / "linked"
        link.symlink_to(folder, target_is_directory=True)
        cache = link / cache.name
    else:
        (folder / ".credential.json.lock").symlink_to(cache)

    def unexpected_acquisition(**kwargs):
        pytest.fail("unsafe cache must fail before acquiring credentials")

    monkeypatch.setattr(mk8s_exec, "_acquire_status", unexpected_acquisition)
    with pytest.raises((RuntimeError, OSError)):
        mk8s_exec._mk8s_exec_credential_status(
            provider=unexpected_acquisition,
            project_id="fixture",
            client_name="fixture",
            endpoint=None,
            cache_file=cache,
        )


def test_warm_cache_entrypoint_does_not_import_full_cli(tmp_path):
    binding = hashlib.sha256(
        json.dumps(
            {
                "project_id": "fixture-project",
                "client_name": "fixture-client",
                "endpoint": "",
                "require_renewable_auth": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    status = {"token": "fixture-token", "expirationTimestamp": "2099-01-01T00:00:00Z"}
    cache = tmp_path / "credential.json"
    cache.write_text(
        json.dumps(
            {
                "schema": "nebius-cxcli/mk8s-exec-credential-cache-v1",
                "binding_sha256": binding,
                "status": status,
            }
        )
    )
    cache.chmod(0o600)
    bootstrap = """
import importlib.abc
import sys
class NoFullCLI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'nebius_cxcli.cli':
            raise RuntimeError('full CLI loaded on credential cache hit')
sys.meta_path.insert(0, NoFullCLI())
from nebius_cxcli.__main__ import main
main()
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            bootstrap,
            "mk8s-token",
            "--project-id",
            "fixture-project",
            "--client-name",
            "fixture-client",
            "--require-renewable-auth",
            "--cache-file",
            str(cache),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "apiVersion": "client.authentication.k8s.io/v1",
        "kind": "ExecCredential",
        "status": status,
    }
    assert not result.stderr
