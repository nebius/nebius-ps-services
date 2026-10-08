"""Public diagnostics for selected replay-window artifacts."""

from __future__ import annotations

import contextlib
import subprocess
import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from nebius_vpngw import cli, replay_window


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("compatible", [False, True])
def test_apply_reports_replay_artifact_admission(monkeypatch, tmp_path, dry_run, compatible):
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n")
    config = {
        "project_id": "project-test",
        "connections": [{"tunnels": [{"replay_window": 1024, "psk": "SYNTHETIC_CANARY"}]}],
    }
    plan = SimpleNamespace(vm_ha=object(), gateway_group=SimpleNamespace(name="gateway"))
    monkeypatch.setattr(cli, "load_local_config", lambda _path: config)
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_args: plan)
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **_kwargs: contextlib.nullcontext())
    effects = []

    def admission(**_kwargs):
        replay_window.require_replay_window_capability(
            config, (replay_window.REPLAY_WINDOW_CAPABILITY,) if compatible else ()
        )
        effects.append("admitted")

    monkeypatch.setattr(cli, "_apply_impl", admission)
    result = CliRunner().invoke(
        cli.app,
        ["apply", "-c", str(config_path), *(["--dry-run"] if dry_run else [])],
    )
    assert result.exit_code == (0 if compatible else 1)
    assert effects == (["admitted"] if compatible else [])
    if not compatible:
        assert isinstance(result.exception, SystemExit)
        assert "replay_window" in result.output
        assert "python -m build --wheel --no-isolation" in result.output
        assert "VPNGW_AGENT_WHEEL" in result.output
    assert "Traceback" not in result.output
    assert "SYNTHETIC_CANARY" not in result.output


@pytest.mark.parametrize("command_group", ["app", "failover_app", "failback_app"])
@pytest.mark.parametrize("default_show_locals", [False, True])
def test_unexpected_cli_exception_does_not_render_local_values(command_group, default_show_locals):
    script = f"""
import sys
import typer

# Model both supported Typer defaults before the application is constructed.
original_init = typer.Typer.__init__
def configured_init(self, *args, **kwargs):
    kwargs.setdefault("pretty_exceptions_show_locals", {default_show_locals!r})
    original_init(self, *args, **kwargs)
typer.Typer.__init__ = configured_init

from nebius_vpngw import cli

@cli.{command_group}.command("exception-probe")
def exception_probe():
    private_config = sys.stdin.read()
    raise RuntimeError("synthetic unexpected failure")

cli.{command_group}(["exception-probe"])
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input="SYNTHETIC_TRACEBACK_CANARY",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert "synthetic unexpected failure" in result.stderr
    assert "SYNTHETIC_TRACEBACK_CANARY" not in result.stderr
