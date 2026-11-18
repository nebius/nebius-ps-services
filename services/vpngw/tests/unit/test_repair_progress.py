import io
import json
import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.deploy import repair_apply
from nebius_vpngw.deploy import repair_progress as progress


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("interrupted", [False, True])
def test_display_precedes_blocking_work_and_closes_without_redirecting_streams(
    monkeypatch,
    interactive,
    interrupted,
):
    output = Terminal() if interactive else io.StringIO()
    monkeypatch.setattr(sys, "stderr", output)
    original_stdout = sys.stdout
    with progress.reporting():
        reporter = progress.CURRENT.get()
        try:
            with progress.step(progress.Phase.INSTALL, 2):
                if interactive:
                    reporter.display.refresh()
                    assert reporter.display.live.is_started
                assert "installing the managed software stack — member 2" in output.getvalue()
                assert sys.stdout is original_stdout and sys.stderr is output
                with progress.step(progress.Phase.VERIFY, 2):
                    assert reporter.frames[-1].key is progress.Phase.VERIFY
                assert reporter.frames[-1].key is progress.Phase.INSTALL
                if interrupted:
                    raise KeyboardInterrupt("private diagnostic sentinel")
        except KeyboardInterrupt:
            pass
        assert reporter.frames == []
        if interactive:
            assert not reporter.display.live.is_started
    assert progress.CURRENT.get() is None
    text = output.getvalue()
    marker = "✗" if interrupted else "✓"
    assert marker + " installing the managed software stack" in text
    assert "private diagnostic sentinel" not in text
    if not interactive:
        assert "\x1b" not in text


def test_wait_updates_use_state_changes_and_bounded_cadence(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(progress.time, "monotonic", lambda: now[0])
    output = io.StringIO()
    monkeypatch.setattr(sys, "stderr", output)
    with progress.reporting(), progress.step(progress.Phase.INSTALL, 1):
        for moment in (0.0, 1.0, 2.0, 4.9, 5.0):
            now[0] = moment
            progress.waiting(progress.Wait.RUNNING_OWNED, deadline=30)
        progress.waiting(progress.Wait.WAIT_EXTERNAL_LOCK, deadline=30)
    text = output.getvalue()
    assert text.count("package worker is running") == 2
    assert "30s remaining" in text and "25s remaining" in text
    assert text.count("package-manager lock") == 1


def test_presentation_failure_does_not_mask_original_failure(monkeypatch):
    class Broken(io.StringIO):
        def write(self, _value):
            raise OSError("broken terminal")

    monkeypatch.setattr(sys, "stderr", Broken())
    with (
        pytest.raises(RuntimeError, match="original failure"),
        progress.reporting(),
        progress.step(progress.Phase.INSTALL, 1),
    ):
        raise RuntimeError("original failure")
    assert progress.CURRENT.get() is None


def test_canonical_ha_progress_uses_the_same_display(monkeypatch):
    output = Terminal()
    monkeypatch.setattr(sys, "stderr", output)
    with progress.reporting(), progress.step(progress.Phase.ACTIVATE):
        reporter = progress.CURRENT.get()
        display = reporter.display
        phase = cli._VMHAProgressPhase.PREPARE_MTLS
        progress.activation(cli._VMHAProgressEvent(phase, cli._VMHAProgressState.STARTED))
        assert reporter.display is display
        assert reporter.frames[-1].label == cli._VMHA_PROGRESS_LABELS[phase]
        progress.activation(cli._VMHAProgressEvent(phase, cli._VMHAProgressState.COMPLETED))
        assert reporter.frames[-1].key is progress.Phase.ACTIVATE
    assert not display.live.is_started


def test_activation_completion_preserves_peer_uncertainty_warning(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(sys, "stderr", output)
    phase = cli._VMHAProgressPhase.VERIFY_FENCED
    with progress.reporting():
        progress.activation(cli._VMHAProgressEvent(phase, cli._VMHAProgressState.STARTED))
        progress.activation(
            cli._VMHAProgressEvent(
                phase,
                cli._VMHAProgressState.COMPLETED,
                warnings=("member 2 peer check was unknown at gate expiry",),
            )
        )
    assert "warning: member 2 peer check was unknown at gate expiry" in output.getvalue()


def test_real_terminal_redraws_during_blocking_work_and_keeps_json_separate():
    import errno
    import os
    import pty
    import select
    import subprocess
    import time

    master, slave = pty.openpty()
    script = """
import json, time
from nebius_vpngw.deploy import repair_progress as p
with p.reporting(), p.step(p.Phase.INSTALL, 2):
    time.sleep(0.25)
    p.waiting(p.Wait.RUNNING_OWNED)
    time.sleep(0.25)
print(json.dumps({'result': 'complete'}))
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=slave,
    )
    os.close(slave)
    chunks = []
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    block = os.read(master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not block:
                    break
                chunks.append(block)
            elif child.poll() is not None:
                break
        stdout, _ = child.communicate(timeout=2)
    finally:
        os.close(master)
        if child.poll() is None:
            child.kill()
            child.wait()
    assert child.returncode == 0
    assert json.loads(stdout) == {"result": "complete"}
    terminal = b"".join(chunks).decode()
    assert terminal.count("\x1b[2K") >= 2
    assert "member 2" in terminal and "package worker is running" in terminal
    assert "✓ installing the managed software stack" in terminal
    assert "\x1b[?25h" in terminal  # Cursor is restored when the live display closes.


@pytest.mark.parametrize("ha", [False, True])
@pytest.mark.parametrize("resumed", [False, True])
def test_public_repair_reports_stages_before_work_and_preserves_json(
    monkeypatch,
    tmp_path,
    ha,
    resumed,
):
    from nebius_vpngw.deploy import pair_phases, repair_activation

    config = tmp_path / "gateway.yaml"
    config.write_text("{}")
    admission = SimpleNamespace(
        root=tmp_path / "repair", intent={}, plan=SimpleNamespace(vm_ha=object() if ha else None)
    )
    previous = {"id": "a", "phase": "installed", "intent": {}} if resumed else None
    monkeypatch.setattr(
        repair_apply.state,
        "Journal",
        lambda _: SimpleNamespace(
            load=lambda: previous,
            begin=lambda *_a, **_k: {"id": "a"},
        ),
    )
    monkeypatch.setattr(repair_apply, "confirm", lambda _: True)
    calls = []

    def observed(phase):
        assert progress.CURRENT.get().frames[-1].key is phase
        calls.append(phase)

    class Owner:
        def __init__(self, *_a, **_k):
            self.phase = "installed" if resumed else "confirmed"

        def prepare(self):
            observed(progress.Phase.PREPARE)

        def install(self):
            with progress.step(progress.Phase.INSTALL, 1):
                observed(progress.Phase.INSTALL)
            self.phase = "installed"

        def finish(self):
            observed(progress.Phase.FINISH)

    monkeypatch.setattr(repair_apply, "Coordinator", Owner)
    monkeypatch.setattr(
        repair_activation, "activate_ha", lambda *_: observed(progress.Phase.ACTIVATE)
    )
    monkeypatch.setattr(
        repair_activation, "activate_ordinary", lambda *_: observed(progress.Phase.ACTIVATE)
    )
    monkeypatch.setattr(cli, "load_local_config", lambda _: {})
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: SimpleNamespace())
    monkeypatch.setattr(
        cli, "_apply_impl", lambda **kw: repair_apply.execute(admission, report=kw["result_report"])
    )
    reports = []
    monkeypatch.setattr(pair_phases, "report", reports.append)
    result = CliRunner().invoke(
        cli.app, ["apply", "-c", str(config), "--repair", "--output-format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["outcome"] == ("resumed" if resumed else "applied")
    assert len(reports) == 1
    assert "opening the confirmed repair transaction" in result.stderr
    assert "activating and verifying repaired gateways" in result.stderr
    assert "verifying and completing the repair transaction" in result.stderr
    assert "\x1b" not in result.stderr
    assert (progress.Phase.PREPARE in calls) is not resumed


def test_package_worker_wait_is_visible_before_poll_and_completion(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(sys, "stderr", output)
    monkeypatch.setattr(repair_apply.time, "sleep", lambda _: None)
    owner = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    owner.instances = {"private-name": SimpleNamespace(instance_index=0)}
    owner.package_request = lambda _: {}
    owner.context = {"installed": {}}
    owner.save = lambda: None
    states = iter(["RUNNING_OWNED", "WAIT_EXTERNAL_LOCK", "PREPARED"])

    def remote(_name, action, **_payload):
        assert "installing the managed software stack — member 0" in output.getvalue()
        if action == "verify-installed":
            assert "verifying the installed repair packages — member 0" in output.getvalue()
            return {"verified": True}
        if action == "worker-find":
            assert "package worker" in output.getvalue()
        return {"state": next(states)}

    owner.remote = remote
    with progress.reporting():
        owner.install_member("private-name")
    assert owner.context["installed"] == {"private-name": {"verified": True}}
    assert "private-name" not in output.getvalue()


@pytest.mark.parametrize("index", [0, 1])
def test_member_index_matches_canonical_activation_observations(monkeypatch, index):
    output = io.StringIO()
    monkeypatch.setattr(sys, "stderr", output)
    owner = SimpleNamespace(instances={"private-name": SimpleNamespace(instance_index=index)})

    @progress.for_member(progress.Phase.INSTALL)
    def install(_owner, _name):
        pass

    with progress.reporting():
        install(owner, "private-name")
        observer = cli._VMHAActivationProgress(progress.activation)
        phase = cli._VMHAProgressPhase.VERIFY_FENCED
        observer(cli._VMHAProgressEvent(phase, cli._VMHAProgressState.STARTED))
        observer.observe(
            index,
            "service readiness",
            cli._VMHAWaitObservation(
                predicate="waiting-for-services",
                observed_at=1,
                elapsed_seconds=1,
                remaining_seconds=29,
            ),
        )
        observer(cli._VMHAProgressEvent(phase, cli._VMHAProgressState.COMPLETED))
    text = output.getvalue()
    assert f"software stack — member {index}" in text
    assert f"member {index}: service readiness" in text


def test_real_ordinary_activation_reports_each_blocking_member(monkeypatch, tmp_path):
    import contextlib

    from nebius_vpngw.deploy import (
        apply_command,
        ordinary_apply,
        ordinary_command,
        ordinary_route_command,
        ordinary_route_publication,
        pair_phases,
        repair_activation,
        repair_routes,
    )

    output = io.StringIO()
    monkeypatch.setattr(sys, "stderr", output)
    instances = {
        f"private-{i}": SimpleNamespace(hostname=f"private-{i}", instance_index=i) for i in (0, 1)
    }
    targets = {name: (f"vm-{i}", f"192.0.2.{i + 1}") for i, name in enumerate(instances)}
    journal = contextlib.nullcontext()
    monkeypatch.setattr(apply_command, "CommandJournal", lambda *_a, **_kw: journal)
    monkeypatch.setattr(repair_routes, "prepare", lambda _: None)
    monkeypatch.setattr(ordinary_command, "package_manifests", lambda *_a, **_kw: {})
    monkeypatch.setattr(ordinary_route_publication, "inspect", lambda *_a, **_kw: None)
    monkeypatch.setattr(ordinary_route_command, "prepare", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        ordinary_command,
        "inspect",
        lambda *_a, **_kw: SimpleNamespace(plan=SimpleNamespace(effects=())),
    )
    monkeypatch.setattr(repair_activation, "Activation", lambda _: object())
    monkeypatch.setattr(pair_phases, "reached", lambda *_a, **_kw: None)
    calls = []

    def inspect(_ssh, _target, instance, *_a, **_kw):
        assert progress.CURRENT.get().frames[-1].label.endswith(f"member {instance.instance_index}")
        return SimpleNamespace(instance=instance, noop=True, close=lambda: None)

    def stage(_ssh, prepared, *_a, **_kw):
        frame = progress.CURRENT.get().frames[-1]
        assert frame.key is progress.Phase.CONFIGURE
        assert frame.label.endswith(f"member {prepared.instance.instance_index}")
        calls.append(("stage", prepared.instance.instance_index))

    def execute(_ssh, prepared, *_a, **_kw):
        frame = progress.CURRENT.get().frames[-1]
        assert frame.key is progress.Phase.ACTIVATE
        assert frame.label.endswith(f"member {prepared.instance.instance_index}")
        calls.append(("activate", prepared.instance.instance_index))
        return {}

    monkeypatch.setattr(ordinary_apply, "inspect_plan", inspect)
    monkeypatch.setattr(ordinary_apply, "stage_plan", stage)
    monkeypatch.setattr(ordinary_apply, "execute_plan", execute)
    owner = SimpleNamespace(
        instances=instances,
        directory=tmp_path,
        operation="operation",
        context={"ordinary_route_handoff": {}},
        remote=lambda *_a, **_kw: {},
        advance=lambda _: None,
        admission=SimpleNamespace(
            intent={"project": "project", "gateway": "gateway", "artifact": "a" * 64},
            manager=SimpleNamespace(inspect_ordinary_targets=lambda _: targets),
            bindings={name: {"compute": value[0]} for name, value in targets.items()},
            targets={name: value[1] for name, value in targets.items()},
            instances=tuple(instances.values()),
            local={},
            ssh=SimpleNamespace(_ssh_policy=None),
            plan=object(),
        ),
    )
    with progress.reporting(), progress.step(progress.Phase.ACTIVATE):
        repair_activation.activate_ordinary(owner)
    assert calls == [("stage", 0), ("stage", 1), ("activate", 0), ("activate", 1)]
    assert "private-" not in output.getvalue()
