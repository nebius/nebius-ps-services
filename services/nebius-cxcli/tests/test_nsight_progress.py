"""Profiling progress at real blocking boundaries, without a live cluster."""

from io import StringIO

import pytest
from rich.console import Console

from nebius_cxcli import cli, nsight_install
from nebius_cxcli.soperator_upgrade_progress import SoperatorUpgradeProgress
from test_nsight_credentials import Kube
from test_nsight_install import installed_project as installed_project


@pytest.mark.parametrize("terminal", [False, True])
def test_shared_jail_wait_has_progress(installed_project, monkeypatch, terminal):
    local, *_ = installed_project
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=180,
    )
    monkeypatch.setattr(cli, "progress_console", console)
    waiter = cli._wait_protected_data_plane_job
    descriptions = {
        "admit": "Check shared jail prerequisites and resolve profiler packages",
        "install": "Install Nsight Systems and Compute in the shared jail",
        "verify": "Verify shared jail profiler versions and activation",
    }
    observed = []

    def wait(**kwargs):
        stage = kwargs["name"].rsplit("-", 1)[-1]
        assert descriptions[stage] in output.getvalue()
        assert len(console._live_stack) == int(terminal)
        observed.append(stage)
        return waiter(**kwargs)

    monkeypatch.setattr(cli, "_wait_protected_data_plane_job", wait)
    nsight_install.install_profiling(
        local.config_path,
        target_ref="cluster",
        secret_name="nsight-streamer-auth",
        reports_path="/data/nsight-reports",
    )
    assert observed == ["admit", "install", "verify"]
    assert not console._live_stack
    if terminal:
        assert "0:00:00" in output.getvalue()
    else:
        assert "\x1b" not in output.getvalue()
        for description in descriptions.values():
            assert output.getvalue().count(f"Nsight: START {description}") == 1
            assert output.getvalue().count(f"Nsight: OK {description}") == 1


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("mode", ["wizard", "stdin", "reuse", "cancel"])
def test_credential_io_has_progress_but_input_does_not(monkeypatch, terminal, mode):
    import json

    import typer

    from nebius_cxcli import nsight_credentials as credentials

    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=180,
    )
    progress = SoperatorUpgradeProgress(console, prefix="Nsight")
    kube = Kube()
    if mode == "reuse":
        kube.run(
            ["create"],
            input_text=json.dumps(
                {
                    "metadata": {"name": "login", "namespace": "soperator"},
                    "stringData": {"username": "admin", "password": "synthetic-test-value"},
                }
            ),
        )
        kube.writes.clear()
    run = kube.run
    calls = []

    def observed_io(*args, **kwargs):
        assert len(console._live_stack) == int(terminal)
        assert "Nsight browser login Secret" in output.getvalue()
        calls.append("io")
        return run(*args, **kwargs)

    monkeypatch.setattr(kube, "run", observed_io)
    monkeypatch.setattr(credentials, "NsightKubernetes", lambda _: kube)

    class Input(StringIO):
        def isatty(self):
            return mode != "stdin"

        def read(self, *args):
            assert not console._live_stack
            calls.append("input")
            return super().read(*args)

    monkeypatch.setattr(credentials.sys, "stdin", Input("synthetic-test-value\n"))

    def prompt(label, **kwargs):
        assert not console._live_stack
        assert mode != "reuse"
        calls.append("input")
        if mode == "cancel":
            raise typer.Abort()
        if "username" in label:
            return "admin"
        assert kwargs["hide_input"] and kwargs["confirmation_prompt"]
        return "synthetic-test-value"

    monkeypatch.setattr(credentials.typer, "prompt", prompt)

    def prepare():
        credentials.prepare_login_credentials(
            env={},
            name="login",
            username="admin",
            interactive=mode != "stdin",
            password_stdin=mode == "stdin",
            fence=lambda: None,
            progress=progress,
        )

    if mode == "cancel":
        with pytest.raises(typer.Abort):
            prepare()
    else:
        prepare()
    assert not console._live_stack
    assert "synthetic-test-value" not in output.getvalue()
    assert len(kube.writes) == int(mode in {"wizard", "stdin"})
    assert ("input" in calls) == (mode != "reuse")


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("boundary", ["handoff", "admit", "active-tools", "access"])
def test_failed_phase_stops_without_success_or_acceptance(
    installed_project, monkeypatch, terminal, failure, boundary
):
    local, _, state, *_ = installed_project
    accepted = state.read().value["accepted"]
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=180,
    )
    monkeypatch.setattr(cli, "progress_console", console)
    owner, name, description = {
        "handoff": (
            cli,
            "_prepare_cluster_handoff_kube_env",
            "Connect to the accepted cluster and verify its identity",
        ),
        "admit": (
            cli,
            "_wait_protected_data_plane_job",
            "Check shared jail prerequisites and resolve profiler packages",
        ),
        "active-tools": (
            nsight_install,
            "verify_active_tools",
            "Verify nsys and ncu through the active login node",
        ),
        "access": (
            nsight_install,
            "collect_nsight_status",
            "Verify viewer readiness and prepare private access commands",
        ),
    }[boundary]

    def fail(*args, **kwargs):
        assert description in output.getvalue()
        assert len(console._live_stack) == int(terminal)
        raise failure("operation interrupted")

    monkeypatch.setattr(owner, name, fail)
    with pytest.raises(failure, match="operation interrupted"):
        nsight_install.install_profiling(
            local.config_path,
            target_ref="cluster",
            secret_name="nsight-streamer-auth",
            reports_path="/data/nsight-reports",
        )
    assert not console._live_stack
    assert state.read().value["accepted"] == accepted
    if not terminal:
        assert f"Nsight: FAILED {description}" in output.getvalue()
        assert f"Nsight: OK {description}" not in output.getvalue()


def test_resume_after_lease_loss_reuses_completed_jobs(installed_project, monkeypatch):
    local, _, state, jobs, verified, applied = installed_project
    apply = nsight_install.apply_viewers

    def lost_lease(*args, **kwargs):
        raise RuntimeError("Lost the Soperator operation Lease")

    monkeypatch.setattr(nsight_install, "apply_viewers", lost_lease)
    options = dict(
        target_ref="cluster",
        secret_name="nsight-streamer-auth",
        reports_path="/data/nsight-reports",
    )
    with pytest.raises(RuntimeError, match="Lost the Soperator"):
        nsight_install.install_profiling(local.config_path, **options)
    assert len(jobs) == 3
    assert "profiling" in state.read().value["active"]["stages"]
    monkeypatch.setattr(nsight_install, "apply_viewers", apply)
    nsight_install.install_profiling(local.config_path, **options)
    assert len(jobs) == 3 and len(verified) == 2 and len(applied) == 1
    assert state.read().value["active"] is None


@pytest.mark.parametrize("terminal", [False, True])
def test_preparation_is_visible_and_prompts_and_deploy_have_exclusive_terminal(
    installed_project, monkeypatch, terminal
):
    from nebius_cxcli import deployment_jail_state, nsight_credentials

    local, *_ = installed_project
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=180,
    )
    monkeypatch.setattr(cli, "progress_console", console)
    observed = []

    def watch(owner, name, description):
        original = getattr(owner, name)

        def run(*args, **kwargs):
            assert description in output.getvalue()
            assert len(console._live_stack) == int(terminal)
            observed.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, run)

    watch(cli, "_load_deploy_context_readonly", "Load profiling configuration and target")
    watch(nsight_install, "prepare_generation", "Prepare and render both pinned Nsight viewers")
    watch(cli, "_read_kube_system_namespace_uid", "Connect to the accepted cluster")
    watch(deployment_jail_state, "observe_jail_storage", "Verify shared jail storage")
    watch(nsight_install, "prepare_nsight_viewers", "Check viewer credentials and report storage")
    watch(nsight_install, "publish_generation", "Publish profiling configuration")

    def credentials(**kwargs):
        assert kwargs["progress"] is not None
        assert not console._live_stack
        observed.append("credentials")

    def deploy(*args, **kwargs):
        assert not console._live_stack
        assert "Nsight: Deploy and wait for both browser viewers" in output.getvalue()
        observed.append("deploy")

    monkeypatch.setattr(nsight_credentials, "prepare_login_credentials", credentials)
    monkeypatch.setattr(nsight_install, "apply_viewers", deploy)
    nsight_install.install_profiling(
        local.config_path,
        target_ref="cluster",
        secret_name="nsight-streamer-auth",
        reports_path="/data/nsight-reports",
    )
    assert observed == [
        "_load_deploy_context_readonly",
        "prepare_generation",
        "_read_kube_system_namespace_uid",
        "observe_jail_storage",
        "credentials",
        "prepare_nsight_viewers",
        "publish_generation",
        "deploy",
        "observe_jail_storage",
    ]
    assert not console._live_stack
