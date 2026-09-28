"""Kubeconfig notices must not strand a live handoff spinner in scrollback."""

from contextlib import ExitStack
from functools import partial
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from nebius_cxcli import cli, soperator_upgrade_progress
from nebius_cxcli.soperator_upgrade_progress import SoperatorUpgradeProgress


@pytest.fixture
def persist_kubeconfig(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(cli, "_should_persist_local_kubeconfig", lambda: True)
    spec = cli._Mk8sKubeconfigSpec(
        cluster_entry_name="fixture-cluster",
        user_entry_name="fixture-user",
        context_name="fixture-context",
        server="https://fixture.invalid",
        ca_pem="FIXTURE-CA",
        exec_command="/usr/bin/false",
        exec_args=(),
    )
    return partial(cli._persist_cluster_handoff_kubeconfig, spec=spec)


@pytest.mark.parametrize("malformed", [False, True])
def test_handoff_notice_clears_live_row_before_printing(
    persist_kubeconfig, tmp_path, monkeypatch, malformed
):
    output = StringIO()
    options = dict(
        file=output,
        force_terminal=True,
        color_system="standard",
        no_color=False,
        _environ={"TERM": "xterm"},
        width=200,
    )
    monkeypatch.setattr(cli, "console", Console(**options))
    monkeypatch.setattr(cli, "progress_console", Console(**options))
    # Refresh deterministically so a timer cannot clear the row before the notice.
    monkeypatch.setattr(
        soperator_upgrade_progress,
        "Progress",
        partial(soperator_upgrade_progress.Progress, auto_refresh=False),
    )
    path = tmp_path / ".kube" / "config"
    if malformed:
        path.parent.mkdir()
        path.write_text("[]\n")
    description = "Connect to the accepted cluster and verify its identity"
    progress = SoperatorUpgradeProgress(cli.progress_console, prefix="Nsight")
    with progress.phase("nsight-handoff", description) as sequence:
        sequence._progress.refresh()
        start = output.tell()
        result = persist_kubeconfig()
        notice = output.getvalue()[start:]
        assert notice.startswith("\r\x1b[2K"), "notice appended to the live spinner row"
        assert ("WARNING:" if malformed else "Updated local kubeconfig") in notice
    assert result == (None if malformed else path)
    if malformed:
        assert path.read_text() == "[]\n"
    assert output.getvalue().count("\x1b[1;32m✓\x1b[0m") == 1
    assert not cli.progress_console._live_stack


@pytest.mark.parametrize("malformed", [False, True])
def test_redirected_handoff_notices_use_stderr(
    persist_kubeconfig, tmp_path, monkeypatch, malformed
):
    stdout, stderr = StringIO(), StringIO()
    monkeypatch.setattr(cli, "console", Console(file=stdout, force_terminal=False))
    monkeypatch.setattr(cli, "progress_console", Console(file=stderr, force_terminal=False))
    path = tmp_path / ".kube" / "config"
    if malformed:
        path.parent.mkdir()
        path.write_text("[]\n")
    persist_kubeconfig()
    assert not stdout.getvalue()
    assert ("WARNING:" if malformed else "Updated local kubeconfig") in stderr.getvalue()
    assert "\x1b" not in stderr.getvalue()


def test_internal_handoff_note_uses_progress_output(persist_kubeconfig, monkeypatch):
    stdout, stderr = StringIO(), StringIO()
    monkeypatch.setattr(cli, "console", Console(file=stdout, force_terminal=False))
    monkeypatch.setattr(cli, "progress_console", Console(file=stderr, force_terminal=False))
    monkeypatch.setattr(cli, "_renewable_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(
        cli, "_mk8s_cluster_handoff_spec", lambda *a, **kw: persist_kubeconfig.keywords["spec"]
    )
    with ExitStack() as stack:
        env = cli._prepare_cluster_handoff_kube_env(
            None,
            None,
            stack=stack,
            target={"target_ref": "fixture", "cluster_id": "fixture", "access": "internal"},
            persist_local_kubeconfig=False,
            allow_terraform_output=False,
            require_renewable_auth=True,
        )
        assert env is not None
    assert not stdout.getvalue()
    assert "NOTE:" in stderr.getvalue()
