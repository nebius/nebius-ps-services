from __future__ import annotations

import contextlib
import io
import json
import time
from unittest.mock import Mock

import pytest

from nebius_vpngw.deploy.ordinary_handoff import Handoff

pytestmark = pytest.mark.usefixtures("runtime_migration_guest")


@pytest.fixture(autouse=True)
def route_runtime_guest(tmp_path, monkeypatch):
    from nebius_vpngw.agent import ordinary_route_authority

    owner = ordinary_route_authority.RouteAuthority(tmp_path / "runtime-routes")
    monkeypatch.setattr(ordinary_route_authority, "RouteAuthority", lambda: owner)
    return owner


def test_migration_reserves_runtime_through_disconnect_and_never_releases_it(route_runtime_guest):
    from tests.unit.test_ordinary_route_runtime import bound

    from nebius_vpngw.apply_approval import digest
    from nebius_vpngw.deploy import handoff_remote as guest

    owner = route_runtime_guest
    owner.reserve("1" * 64, digest(owner.snapshot()))
    owner.release("1" * 64, bound(), None)
    request = {"binding": "2" * 64, "observed": {"route_runtime": owner.snapshot()}}
    guest.reserve_runtime_routes(request)
    guest.verify_runtime_routes(request)
    with pytest.raises(RuntimeError, match="does not own"):
        owner.require_runtime(bound())
    # Crash immediately after reservation, before the ordinary handoff journal
    # exists: the same HA target can resume; another target cannot take over.
    request["observed"]["route_runtime"] = owner.snapshot()
    before = owner.path.read_bytes()
    guest.reserve_runtime_routes(request)
    assert owner.path.read_bytes() == before
    with pytest.raises(RuntimeError, match="Another command"):
        guest.reserve_runtime_routes({**request, "binding": "3" * 64})


def test_migration_rejects_new_writer_after_review(route_runtime_guest):
    from tests.unit.test_ordinary_route_runtime import bound

    from nebius_vpngw.apply_approval import digest
    from nebius_vpngw.deploy import handoff_remote as guest

    request = {"binding": "2" * 64, "observed": {"route_runtime": route_runtime_guest.snapshot()}}
    route_runtime_guest.reserve("1" * 64, digest(route_runtime_guest.snapshot()))
    route_runtime_guest.release("1" * 64, bound(), None)
    with pytest.raises(RuntimeError, match="appeared"):
        guest.reserve_runtime_routes(request)


def test_completed_migration_transfers_the_reservation_to_deployment(
    route_runtime_guest, tmp_path, monkeypatch
):
    from tests.unit.test_ordinary_route_runtime import bound

    from nebius_vpngw.apply_approval import digest
    from nebius_vpngw.deploy import handoff_remote as guest

    owner = route_runtime_guest
    owner.reserve("1" * 64, digest(owner.snapshot()))
    owner.release("1" * 64, bound(), None)
    request = {"binding": "2" * 64, "observed": {"route_runtime": owner.snapshot()}}
    config = tmp_path / "installed.yaml"
    config.write_bytes(b"verified HA configuration")
    monkeypatch.setattr(guest.remote, "CONFIG", config)
    guest.reserve_runtime_routes(request)
    guest.retire_runtime_routes(request)
    assert owner.read()["writer"] == "deployment"
    assert owner.read()["binding"] == bound()
    with pytest.raises(RuntimeError, match="does not own"):
        owner.require_runtime(bound())
    # A later ordinary apply can acquire a newly reviewed reservation. It must
    # still publish a verified binding before the runtime regains any authority.
    owner.reserve("4" * 64, digest(owner.snapshot()))
    assert owner.read()["holder"] == "4" * 64


def test_migration_unknown_cloud_operation_blocks_before_guest_effects(monkeypatch):
    from nebius_vpngw.deploy import handoff_remote as guest

    monkeypatch.setattr(guest.ops, "Journal", lambda: Mock(read=lambda: None))
    manager = Mock()
    monkeypatch.setattr(guest.ops, "Manager", lambda _: manager)
    monkeypatch.setattr(
        guest,
        "runtime_routes",
        lambda: {"authority": {}, "journal": {"transaction": {"phase": "pending"}}},
    )
    with pytest.raises(guest.ops.OperationBlocked, match="runtime_route_operation_unresolved"):
        guest.inspect({})
    assert not manager.mock_calls


def test_handoff_release_requires_exact_locked_passive_status():
    expected = {"cluster_id": "cluster", "node_id": "node", "generation_id": "generation"}
    status = dict(
        expected, data_plane_mode="passive", apply_locked=True, apply_operation_id="1" * 64
    )
    for field, wrong in [
        ("node_id", "foreign"),
        ("generation_id", "stale"),
        ("data_plane_mode", "active"),
        ("apply_locked", False),
        ("apply_operation_id", "2" * 64),
    ]:
        stdin = Mock()
        handoff = Handoff(stdin, ha=expected)
        with pytest.raises(RuntimeError, match="locked-passive"):
            handoff.complete(dict(status, **{field: wrong}), operation_id="1" * 64)
        stdin.submit.assert_not_called()
        assert not handoff.finished
    stdin = Mock()
    stdin.submit.return_value = {"complete": True}
    handoff = Handoff(stdin, ha=expected)
    handoff.activated_config_sha256 = "a" * 64
    handoff.fresh_verifier = Mock()
    handoff.complete(status, operation_id="1" * 64)
    stdin.submit.assert_called_once_with({"action": "complete", "operation": "1" * 64})
    assert handoff.finished


def test_lost_handoff_retirement_reply_is_not_success():
    expected = {"node_id": "node", "cluster_id": "cluster", "generation_id": "generation"}
    job = Mock()
    job.submit.side_effect = ValueError("lost receipt")
    handoff = Handoff(job, ha=expected)
    handoff.activated_config_sha256 = "a" * 64
    handoff.fresh_verifier = Mock()
    with pytest.raises(ValueError):
        handoff.complete(
            dict(
                expected, data_plane_mode="passive", apply_locked=True, apply_operation_id="1" * 64
            ),
            operation_id="1" * 64,
        )
    assert not handoff.finished


def test_streamed_bootstrap_uses_small_command_and_preserves_request_framing(monkeypatch):
    import shlex
    import subprocess
    import sys

    from nebius_vpngw.deploy import ordinary_handoff

    stdin = io.StringIO()
    client = Mock()
    client.exec_command.return_value = (stdin, io.StringIO(), io.StringIO())
    monkeypatch.setattr(
        ordinary_handoff,
        "_REMOTE",
        "import json; print(json.dumps(json.loads(sys.stdin.readline())))",
    )
    request = {"action": "framing-proof", "value": "exact request"}
    ordinary_handoff._start(client, request)
    command = shlex.split(client.exec_command.call_args.args[0])
    assert len(client.exec_command.call_args.args[0]) < 1024
    assert len(stdin.getvalue()) > 128 * 1024
    result = subprocess.run(
        [sys.executable, *command[command.index("/usr/bin/python3") + 1 :]],
        input=stdin.getvalue(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == request


def test_repair_admission_works_before_new_package_is_installed(monkeypatch, tmp_path):
    from contextlib import nullcontext

    from nebius_vpngw.deploy import handoff_remote as guest

    class AdmissionReached(Exception):
        pass

    observed = {"mode": "ha"}
    request = dict(
        action="reserve",
        observed=observed,
        ha={},
        repair={},
        binding="binding",
        artifact="artifact",
        approval="approval",
        ingress_policy={},
    )
    monkeypatch.setattr(guest, "receive", lambda: request)
    monkeypatch.setattr(guest, "inspect", lambda _, **kw: observed)
    monkeypatch.setattr(guest.signal, "signal", Mock())
    monkeypatch.setattr(guest.signal, "alarm", Mock())
    monkeypatch.setattr(guest.remote, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(guest.remote, "routing_lock", nullcontext)
    journal = Mock()
    journal.read.return_value = None
    monkeypatch.setattr(guest.ops, "Journal", lambda: journal)
    manager = Mock()
    manager.unit.return_value = {"missing": True}
    manager.stopped.return_value = True
    manager.network.return_value = {}
    monkeypatch.setattr(guest.ops, "Manager", lambda _: manager)
    monkeypatch.setattr(guest.ops, "identity", lambda: {})
    monkeypatch.setattr(guest.ops, "install_startup_guard", Mock())
    monkeypatch.setattr(guest.ops, "Operation", lambda *_: Mock())
    boot = tmp_path / "boot-id"
    boot.write_text("boot")
    monkeypatch.setattr(guest.ops, "BOOT", boot)
    monkeypatch.setattr(guest.repair, "persist_cold_guard", Mock())
    monkeypatch.setattr(guest.repair, "writer_locks", lambda **_: nullcontext())
    monkeypatch.setattr(guest.repair, "exact_lock", Mock())
    guard = Mock()
    monkeypatch.setattr(guest.repair, "cold_guard", guard)
    monkeypatch.setattr(guest.repair, "evidence", Mock())
    command = Mock(side_effect=ModuleNotFoundError("new package is not installed yet"))
    monkeypatch.setattr(guest.remote, "command", command)

    def admission(*_):
        guard.assert_called_once()
        raise AdmissionReached

    monkeypatch.setattr(guest, "Holder", admission)
    with pytest.raises(AdmissionReached):
        guest.main()
    command.assert_not_called()


@pytest.mark.parametrize("repairing", [False, True])
def test_package_install_is_journal_bound_after_admission(
    monkeypatch, ordinary_operation_guest, repairing, tmp_path
):
    from nebius_vpngw import ordinary_operations as ops
    from nebius_vpngw.deploy import handoff_remote as guest

    class AptFailed(RuntimeError):
        pass

    manifest = {
        "wheel_name": "nebius_vpngw-1-py3-none-any.whl",
        "wheel_sha256": "a" * 64,
        "dependency_wheels": {},
    }
    predecessor = {"assets": {}, "asset_modes": {}}
    plan = {"desired": manifest, "predecessor": predecessor}
    manager = ops.Manager(time.monotonic() + 60)
    operation = ops.Operation.begin(
        request="1" * 32,
        config="2" * 64,
        artifact=manifest["wheel_sha256"],
        predecessor="3" * 64,
        manager=manager,
        network=manager.network(),
        purpose="ha-handoff",
    )
    monkeypatch.setattr(guest, "STAGING", tmp_path)
    (tmp_path / (operation.value["id"] + ".json")).write_text("{}")
    (tmp_path / (operation.value["id"] + ".json")).chmod(0o600)
    holder = guest.Holder(
        {
            "package": plan,
            "observed": {"mode": "ha" if repairing else "ordinary"},
            "artifact": manifest["wheel_sha256"],
            "approval": "3" * 64,
        },
        operation,
    )
    events = []

    @contextlib.contextmanager
    def locked(**kwargs):
        events.append("locked")
        yield

    def stopped(name):
        events.append(name)
        return True

    def predecessor_read(_manifest):
        events.append("predecessor")
        return predecessor

    def package_install():
        assert guest.remote.OPERATION is operation
        assert ops.Journal().read()["phase"] == "install"
        assert "locked" in events and "predecessor" in events
        assert set(ops.MANAGEMENT).issubset(events)
        if repairing:
            assert set(ops.HA_WRITERS).issubset(events) and "repair" in events
            assert "conversion-preflight" in events
        raise AptFailed("injected package failure")

    monkeypatch.setattr(guest.remote, "OPERATION", None)
    monkeypatch.setattr(guest.repair, "read_json", lambda _: plan)
    monkeypatch.setattr(guest.remote, "routing_lock", locked)
    monkeypatch.setattr(guest.repair, "writer_locks", locked)
    monkeypatch.setattr(manager, "stopped", stopped)
    monkeypatch.setattr(holder, "require_repair", lambda _: events.append("repair"))
    monkeypatch.setattr(
        holder,
        "convert_checkpoint",
        lambda *, preflight=False: events.append(
            "conversion-preflight" if preflight else "conversion"
        ),
    )
    monkeypatch.setattr(guest, "package_observation", predecessor_read)
    monkeypatch.setattr(guest.repair, "verified_file", lambda *args: b"")
    monkeypatch.setattr(guest.remote.migration, "install", package_install)
    with pytest.raises(AptFailed):
        holder.prepare({"plan_digest": ops.digest(plan)})
    assert guest.remote.OPERATION is None


@pytest.mark.parametrize(
    "layout", ["absent", "partial", "unsafe", "symlink", "checkpoint", "status", "dangling-marker"]
)
def test_fresh_guarded_packages_own_lock_layout_before_install(
    monkeypatch, tmp_path, runtime_migration_guest, ordinary_operation_guest, layout
):
    """A brand new VM has no controller or mTLS state before package preparation."""
    import fcntl
    import os
    from pathlib import Path

    from nebius_vpngw import runtime_migration as migration
    from nebius_vpngw.deploy import handoff_remote as guest

    class InstallReached(Exception):
        pass

    state = tmp_path / "ha"
    if layout != "absent":
        state.mkdir(mode=0o755)
    if layout == "unsafe":
        state.chmod(0o777)
    elif layout == "symlink":
        target = tmp_path / "unrelated"
        target.mkdir(mode=0o700)
        (state / "mtls").symlink_to(target, target_is_directory=True)
    elif layout == "checkpoint":
        (state / "controller-checkpoint.json").write_bytes(b"retained checkpoint")
    elif layout == "status":
        (state / "status.json").write_bytes(b"retained status")
    marker = tmp_path / "marker"
    if layout == "dangling-marker":
        marker.symlink_to(tmp_path / "absent-marker-target")
    manifest = dict(
        runtime_plan=runtime_migration_guest,
        ha_parent="a" * 64,
        config_sha256="b" * 64,
        package_identity="c" * 64,
    )
    staged = migration.stage(runtime_migration_guest) / "payload"
    migration.write(staged / "manifest.json", json.dumps(manifest).encode())
    monkeypatch.setattr(guest, "MARKER", marker)
    monkeypatch.setattr(guest.repair, "STATE", state)
    monkeypatch.setattr(guest.remote, "LOCK", tmp_path / "deployment.lock")
    monkeypatch.setattr(guest, "package_observation", lambda _: {})
    monkeypatch.setattr(guest.ops, "install_startup_guard", lambda **_: None)
    monkeypatch.setattr(guest.ops, "alive", lambda _: True)
    observed_stops = []
    monkeypatch.setattr(ordinary_operation_guest, "unit", lambda *_: {"missing": True})

    def stopped(_manager, name):
        observed_stops.append(name)
        return True

    monkeypatch.setattr(ordinary_operation_guest, "stopped", stopped)
    original_locks = guest.repair.writer_locks
    monkeypatch.setattr(
        guest.repair, "writer_locks", lambda **kw: original_locks(state_dir=state, **kw)
    )
    routing = tmp_path / "routing.lock"
    monkeypatch.setattr(
        guest.repair,
        "Path",
        lambda value: routing if str(value) == "/run/nebius-vpngw/fix-routes.lock" else Path(value),
    )

    def install():
        assert set((*guest.ops.HA_WRITERS, *guest.ops.MANAGEMENT)).issubset(observed_stops)
        assert migration.state()["phase"] == "inhibited"
        assert guest.ops.Journal().read()["phase"] == "install"
        for path in (state / "rearm.lock", state / "mtls/writer.lock", routing):
            fd = os.open(path, os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
        assert not marker.exists()
        assert not (state / "controller-checkpoint.json").exists()
        assert not (state / "status.json").exists()
        assert not (state / "mtls/active.json").exists()
        assert not list((state / "mtls/identities").iterdir())
        raise InstallReached

    monkeypatch.setattr(migration, "install", install)
    if layout in {"absent", "partial"}:
        with pytest.raises(InstallReached):
            guest.prepare_guarded({"package": {"desired": manifest, "predecessor": {}}})
        assert (state / "mtls").stat().st_mode & 0o777 == 0o700
    else:
        with pytest.raises((RuntimeError, ValueError)):
            guest.prepare_guarded({"package": {"desired": manifest, "predecessor": {}}})
        if layout == "symlink":
            assert not list(target.iterdir())
        if layout == "checkpoint":
            assert (state / "controller-checkpoint.json").read_bytes() == b"retained checkpoint"
    assert guest.remote.OPERATION is None


def test_streamed_unstarted_lock_layout_without_installed_package(tmp_path):
    import subprocess
    import sys

    from nebius_vpngw.handoff_bootstrap import streamed_source

    code = (
        streamed_source()
        + "\n"
        + (
            "from pathlib import Path\n"
            "import _vpngw_handoff_remote as guest\n"
            f"guest.repair.STATE=Path({str(tmp_path / 'ha')!r})\n"
            f"guest.MARKER=Path({str(tmp_path / 'marker')!r})\n"
            "guest.initialize_unstarted_writer_layout()\n"
            "assert (guest.repair.STATE/'mtls/writer.lock').is_file()\n"
            "assert not (guest.repair.STATE/'controller-checkpoint.json').exists()\n"
            "assert not (guest.repair.STATE/'mtls/active.json').exists()\n"
        )
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-"], input=code, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
