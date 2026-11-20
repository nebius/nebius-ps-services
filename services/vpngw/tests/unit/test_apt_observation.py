"""APT preparation diagnostics and bounded, non-disruptive observation."""

import base64
import io
import json
import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from nebius_vpngw import package_worker, repair_bootstrap
from nebius_vpngw import runtime_migration as migration
from nebius_vpngw.deploy import (
    handoff_remote,
    ordinary_handoff,
    ordinary_remote,
    repair_apply,
    repair_remote,
)
from nebius_vpngw.deploy.recovery import GUEST_FAILURES, GuestFailure, Invocation, advice


@pytest.mark.parametrize(
    "message,category",
    [
        (
            "W: Failed to fetch https://private.invalid/x Temporary failure resolving 'private.invalid'",
            "dns",
        ),
        ("E: Could not connect to proxy.invalid: Connection refused", "transport"),
        ("E: Certificate verification failed: The certificate issuer is unknown", "tls"),
        (
            "W: GPG error: signatures couldn't be verified: NO_PUBKEY 1234\nE: The repository 'private' is not signed.",
            "signature",
        ),
        ("E: Release file for private is not valid yet", "metadata_time"),
        ("E: Release file for private is expired", "metadata_time"),
        ("E: The repository 'private' does not have a Release file.", "configuration"),
        ("E: Malformed entry 1 in list file", "configuration"),
        ("E: Unclassified failure with secret-value", "unknown"),
        ("E: Temporary failure resolving private\nE: NO_PUBKEY 123", "unknown"),
        ("E: Temporary failure resolving private\nE: Disk unavailable", "unknown"),
    ],
)
def test_classification_is_conservative_and_never_returns_diagnostic_values(message, category):
    assert migration.classify_apt_stderr(message.encode()) == category
    assert migration.classify_apt_stderr(message.encode(), truncated=True) == "unknown"


def test_stderr_is_drained_and_bounded_without_hiding_success():
    script = "import sys;sys.stderr.write('x'*200000);sys.stdout.write('ok')"
    assert migration.run([sys.executable, "-c", script], apt_diagnostics=True, timeout=5) == "ok"
    script += ";sys.stderr.write('E: Temporary failure resolving private');sys.exit(100)"
    with pytest.raises(migration.CommandFailure) as raised:
        migration.run([sys.executable, "-c", script], apt_diagnostics=True, timeout=5)
    assert raised.value.category == "unknown"
    assert "private" not in str(raised.value) + repr(raised.value)


def test_refresh_allows_slow_success_inside_new_cap(monkeypatch):
    calls = []

    def command(_args, *, timeout, **_kwargs):
        calls.append(timeout)
        # The fake dependency completes after the former 120-second limit.
        if timeout < 150:
            raise TimeoutError()
        return ""

    monkeypatch.setattr(migration, "run", command)
    with migration.apt_indexes():
        pass
    assert calls == [180]


@pytest.mark.parametrize(
    "error,expected",
    [
        (TimeoutError("private-value"), "apt_repository_timeout"),
        (migration.CommandFailure("dns"), "apt_repository_dns"),
        (migration.ObservationDeadline(), "package_observation_deadline"),
        (migration.ObservationInterrupted(), "package_observation_interrupted"),
        (RuntimeError("private-value"), None),
    ],
)
def test_refresh_preserves_typed_cause_and_removes_temporary_state(monkeypatch, error, expected):
    directories = []

    def command(_args, *, cwd, **_kwargs):
        directories.append(cwd)
        raise error

    monkeypatch.setattr(migration, "run", command)
    with pytest.raises((RuntimeError, TimeoutError)) as raised, migration.apt_indexes():
        pytest.fail("failed metadata must not be admitted")
    assert migration.observation_code(raised.value) == expected
    assert all(not directory.exists() for directory in directories)
    assert "private-value" not in str(raised.value)


def test_scoped_deadline_reserves_cleanup_and_does_not_reset(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(migration.time, "monotonic", lambda: now[0])
    with pytest.raises(migration.ObservationDeadline), migration.observation_budget(200):
        assert migration.observation_remaining() == 90
        now[0] = 150
        with migration.observation_budget(500):
            assert migration.observation_remaining() == 40
        now[0] = 190
        migration.run(["never-launch"])
    assert migration.OBSERVATION_DEADLINE.get() is None


def test_completed_observation_cannot_report_success_after_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(migration.time, "monotonic", lambda: now[0])
    with pytest.raises(migration.ObservationDeadline), migration.observation_budget(120):
        now[0] = 111.0
    assert migration.OBSERVATION_DEADLINE.get() is None


@pytest.mark.parametrize(
    "operation,args",
    [
        (migration.plan, ()),
        (migration.plan_current, ()),
        (migration.stage, ({},)),
        (migration.stage_payload, ({}, {})),
    ],
)
def test_all_package_entrypoints_reject_expired_budget_before_work(monkeypatch, operation, args):
    monkeypatch.setattr(migration, "run", lambda *_a, **_k: pytest.fail("launched after deadline"))
    with pytest.raises(migration.ObservationDeadline):
        operation(*args, deadline=0)


def test_request_budget_clamps_command_without_changing_unscoped_worker(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(migration.time, "monotonic", lambda: now[0])
    limits = []
    monkeypatch.setattr(migration, "COMMAND", lambda args, limit: (limits.append(limit) or 0, "ok"))
    with migration.observation_budget(140):
        migration.run(["observe"], timeout=180)
        now[0] = 115
        migration.run(["observe"], timeout=180)
    migration.run(["owned-worker"], timeout=60)
    assert limits == [30, 15, 60]


def test_process_startup_consumes_the_same_absolute_budget(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(migration.time, "monotonic", lambda: now[0])
    original = migration.subprocess.Popen
    children = []

    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        now[0] = 105.0
        return child

    monkeypatch.setattr(migration.subprocess, "Popen", launch)
    with pytest.raises(migration.ObservationDeadline), migration.observation_budget(112):
        migration.run([sys.executable, "-c", "import time;time.sleep(60)"], timeout=180)
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize("owner", ["migration", "ordinary", "ordinary-operation"])
def test_signal_during_spawn_cannot_orphan_the_observation_child(monkeypatch, owner):
    monkeypatch.setattr(ordinary_remote, "DEADLINE", migration.time.monotonic() + 600)
    monkeypatch.setattr(ordinary_remote, "OPERATION", None)
    invoke = migration.run if owner == "migration" else ordinary_remote.command
    effects = {0: {}}
    if owner == "ordinary-operation":
        operation = SimpleNamespace(
            value={"effects": effects},
            intent=lambda _: 0,
            process_started=lambda index, pid: effects[index].update(process={"pid": pid}),
            update=lambda index, **values: effects[index].update(values),
        )

        def quiescent(process):
            assert process.get("pid"), "cancellation escaped before process ownership was recorded"
            return True

        monkeypatch.setattr(ordinary_remote, "OPERATION", operation)
        monkeypatch.setattr(ordinary_remote.operations, "process_group_empty", quiescent)
    original = migration.subprocess.Popen
    children = []

    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        os.kill(os.getpid(), signal.SIGTERM)
        return child

    monkeypatch.setattr(migration.subprocess, "Popen", launch)
    try:
        with (
            repair_remote.control_deadline(migration.time.monotonic() + 10),
            pytest.raises(migration.ObservationInterrupted),
        ):
            invoke([sys.executable, "-c", "import time;time.sleep(60)"])
        assert children[0].poll() is not None, "child escaped cancellation during spawn"
        if owner == "ordinary-operation":
            assert effects[0] == {"process": {"pid": children[0].pid}, "state": "settled"}
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def test_python_preparation_uses_remaining_observation_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(ordinary_remote, "DEADLINE", migration.time.monotonic() + 600)
    monkeypatch.setattr(ordinary_remote, "OPERATION", None)
    monkeypatch.setattr(
        ordinary_remote.python_packages, "require_shared_dependencies", lambda _: None
    )

    def prepare(_manifest, _path, command):
        command([sys.executable, "-B", "-c", "import time;time.sleep(0.3)"], limit=120)

    monkeypatch.setattr(ordinary_remote.python_packages, "prepare_payload", prepare)
    with (
        pytest.raises(migration.ObservationDeadline),
        migration.observation_budget(migration.time.monotonic() + 10.05),
    ):
        ordinary_remote.prepare_python_payload({}, tmp_path)


def test_observation_command_can_run_from_non_main_thread():
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(migration.run, [sys.executable, "-c", "print('ok')"])
        assert result.result(timeout=5).strip() == "ok"


def test_scoped_projection_is_bounded_without_changing_unscoped_installation(monkeypatch):
    effects = {}
    operation = SimpleNamespace(
        value={"effects": effects},
        intent=lambda _: 0,
        process_started=lambda index, pid: effects.update({index: {"process": {"pid": pid}}}),
        update=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(ordinary_remote, "DEADLINE", migration.time.monotonic() + 600)
    monkeypatch.setattr(ordinary_remote, "DURABLE_PACKAGES", True)
    monkeypatch.setattr(ordinary_remote, "OPERATION", operation)
    monkeypatch.setattr(ordinary_remote.operations, "process_group_empty", lambda _: True)
    monkeypatch.setattr(ordinary_remote, "durable_command", lambda *_, **kw: (0, "durable"))
    # Trailing arguments select the pip policy without installing any package.
    args = [sys.executable, "-c", "print('projection')", "pip", "install"]
    assert ordinary_remote.command(args) == (0, "durable")
    with migration.observation_budget(migration.time.monotonic() + 11):
        assert ordinary_remote.command(args) == (0, "projection")
    assert ordinary_remote.command(args) == (0, "durable")


@pytest.mark.parametrize("boundary", ["before_plan", "after_plan"])
def test_corrective_planning_budget_covers_surrounding_observations(monkeypatch, boundary):
    now = [100.0]
    monkeypatch.setattr(migration.time, "monotonic", lambda: now[0])
    calls = []
    monkeypatch.setattr(migration, "COMMAND", lambda args, **kw: (calls.append(args) or 0, ""))
    children = {
        "runtime": {"phase": "installed", "operation": "owner", "digest": "digest"},
        "ordinary": {"state": "complete", "id": "owner"},
        "conversion": None,
    }
    current = {"reservation": None, "children": children}
    monkeypatch.setattr(handoff_remote, "corrective_inspect", lambda _: current)
    monkeypatch.setattr(migration, "state", lambda: {"plan": {}})
    monkeypatch.setattr(migration, "plan_current", lambda **_: {})
    monkeypatch.setattr(migration, "corrective_policy", lambda *_: None)
    monkeypatch.setattr(migration, "asset_retirement", lambda *_, **kw: {})

    def observe(phase):
        if phase == boundary:
            now[0] = 121.0
            migration.run(["must-not-launch"])
        return {}

    monkeypatch.setattr(migration, "verify_packages", lambda _: observe("before_plan"))
    monkeypatch.setattr(
        handoff_remote.remote, "inspect_for_plan", lambda *_, **kw: observe("after_plan")
    )
    request = {"children": children, "operation": "owner", "manifest": {"ha_parent": "owner"}}
    with pytest.raises(migration.ObservationDeadline):
        handoff_remote.corrective_plan(request, deadline=130)
    assert calls == []


def test_delegated_check_and_command_share_observation_cutoff(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(migration.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(migration, "CHECK", lambda: now.__setitem__(0, 105.0))
    limits = []

    def command(_args, *, limit):
        limits.append(limit)
        now[0] = 110.0
        raise TimeoutError("transaction_deadline")

    monkeypatch.setattr(migration, "COMMAND", command)
    with pytest.raises(migration.ObservationDeadline), migration.observation_budget(120):
        migration.run(["observe"], timeout=180)
    assert limits == [5]


@pytest.mark.parametrize("category", sorted(migration.RepositoryFailure.CATEGORIES))
def test_repair_projection_uses_existing_envelope_and_safe_recovery(
    monkeypatch, capsys, tmp_path, category
):
    monkeypatch.setattr(
        repair_remote.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"action":"inspect"}'))
    )

    def fail(_request):
        raise migration.RepositoryFailure(category)

    monkeypatch.setattr(repair_remote, "control", fail)
    with pytest.raises(SystemExit):
        repair_remote.control_main()
    result = json.loads(capsys.readouterr().out)
    code = (
        "repair_repository_unavailable" if category == "unknown" else "apt_repository_" + category
    )
    assert result == {"status": "failed", "stage": "inspect", "error_code": code}
    failure = GuestFailure(code, "inspect", GUEST_FAILURES[code][0])
    report = advice(Invocation("apply", tmp_path / "vpn.yaml"), failure)
    assert report.reason_code == "guest-" + code.replace("_", "-")
    assert report.next_action and report.repair_prerequisite


def test_ordinary_projection_preserves_new_code(monkeypatch, capsys):
    monkeypatch.setattr(
        ordinary_remote.sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(b'{"action":"release-resume","manifest":{}}')),
    )
    monkeypatch.setattr(ordinary_remote.signal, "signal", lambda *_: None)

    def fail(*_args, **_kwargs):
        raise migration.RepositoryFailure("signature")

    monkeypatch.setattr(ordinary_remote, "release_resume", fail)
    with pytest.raises(SystemExit):
        ordinary_remote.main(deadline=migration.time.monotonic() + 600)
    assert json.loads(capsys.readouterr().out)["error_code"] == "apt_repository_signature"


def test_repair_source_deadline_is_not_reset_after_upload(monkeypatch, capsys):
    frame = base64.b64decode(repair_bootstrap.source_frame()).decode()
    assert "control_main(deadline=_repair_bootstrap_deadline)" in frame
    monkeypatch.setattr(repair_remote, "control", lambda _: pytest.fail("expired request ran"))
    with pytest.raises(SystemExit):
        repair_remote.control_main(deadline=0)
    assert json.loads(capsys.readouterr().out)["error_code"] == "package_observation_deadline"


@pytest.mark.parametrize("failed_member", ["node-0", "node-1"])
def test_preparation_failure_never_stops_running_members(monkeypatch, tmp_path, failed_member):
    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.instances = {"node-0": object(), "node-1": object()}
    coordinator.context = {"staged": [], "guards": {}}
    coordinator.cloud = SimpleNamespace(
        require=lambda _: object(), stop_all=lambda: pytest.fail("stopped before staging")
    )
    coordinator.wait_access = lambda _: None
    coordinator.guard = lambda _: pytest.fail("guarded before staging")
    monkeypatch.setattr(
        repair_apply, "instance_cloud_state", lambda _: repair_apply.InstanceCloudState.RUNNING
    )
    monkeypatch.setattr(
        repair_apply, "Quarantine", lambda *_: SimpleNamespace(path=tmp_path / "absent")
    )
    calls = []

    def stage(name):
        calls.append(name)
        if name == failed_member:
            raise migration.RepositoryFailure("dns")

    coordinator.stage = stage
    with pytest.raises(migration.RepositoryFailure):
        coordinator.prepare()
    assert calls[-1] == failed_member


def test_handoff_and_durable_receipts_preserve_only_allowlisted_codes():
    failure = migration.RepositoryFailure("tls")
    code = package_worker.failure_code(failure)
    assert code == "guest-apt-repository-tls" and code in package_worker.FAILURE_CODES
    assert package_worker.failure_code(RuntimeError("private-secret")) == "package-worker-failed"
    with pytest.raises(GuestFailure):
        ordinary_handoff._receive(
            io.StringIO(json.dumps({"status": "failed", "error_code": "apt_repository_tls"}))
        )
    with pytest.raises(ValueError):
        ordinary_handoff._receive(
            io.StringIO(json.dumps({"status": "failed", "error_code": "private-secret"}))
        )


def test_handoff_stage_respects_shorter_phase_budget(monkeypatch):
    observed = []

    def stage(*_args, deadline):
        observed.append(deadline)
        raise migration.ObservationDeadline()

    monkeypatch.setattr(handoff_remote.remote.migration, "stage_payload", stage)
    with pytest.raises(migration.ObservationDeadline):
        handoff_remote.stage_package({"desired": {}}, {}, deadline=42)
    assert observed == [42]
