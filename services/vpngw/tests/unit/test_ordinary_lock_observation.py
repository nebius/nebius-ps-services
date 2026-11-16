from __future__ import annotations

import fcntl
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nebius_vpngw import ordinary_operations as operations
from nebius_vpngw.agent import ordinary_route_authority as authority
from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import ordinary_remote


@pytest.fixture
def held_routing_lock(tmp_path):
    path = tmp_path / "routing.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    held = [fd]
    try:
        yield path, held
    finally:
        for current in held:
            os.close(current)


@pytest.mark.parametrize("release", [True, False])
def test_initial_inspection_waits_within_original_deadline_and_refreshes_boot(
    monkeypatch,
    tmp_path,
    runtime_migration_guest,
    ordinary_operation_guest,
    held_routing_lock,
    release,
):
    path, held = held_routing_lock
    boot = tmp_path / "observed-boot"
    boot.write_text("old-boot")
    clock = SimpleNamespace(now=100.0, sleeps=0)

    def sleep(seconds):
        clock.now += seconds
        clock.sleeps += 1
        if release and held:
            boot.write_text("new-boot")
            ordinary_remote.CONFIG.write_text("gateway: {}\nconnections: []\n")
            os.close(held.pop())

    monkeypatch.setattr(
        ordinary_remote,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.now,
            sleep=sleep,
        ),
    )
    monkeypatch.setattr(ordinary_remote, "DEADLINE", 100.2)
    monkeypatch.setattr(ordinary_remote, "BOOT", boot)
    monkeypatch.setattr(ordinary_remote, "CONFIG", tmp_path / "absent-config")
    monkeypatch.setattr(ordinary_remote, "ROUTING_LOCK", path)
    monkeypatch.setattr(ordinary_remote, "command", lambda *args, **kwargs: (0, "active"))
    receipt = Mock(return_value={"package_identity": "candidate"})
    monkeypatch.setattr(ordinary_remote, "agent_receipt", receipt)
    snapshot = Mock(return_value={"exact": True})
    monkeypatch.setattr(ordinary_remote.routes.RouteStore, "snapshot", snapshot)
    manifest = {
        "files": {},
        "assets": {},
        "route_projection": None,
        "package_identity": "candidate",
    }
    if release:
        observed = ordinary_remote.inspect(manifest)
        assert observed["boot_id"] == "new-boot"
        assert observed["route_ownership"] == {"exact": True}
        assert observed["config_sha256"] == ordinary_remote.sha(ordinary_remote.CONFIG.read_bytes())
        receipt.assert_called_once_with(observed["config_sha256"], "new-boot", verify_only=True)
        assert snapshot.call_args.args[3] == "new-boot"
        assert snapshot.call_count == 1
    else:
        with pytest.raises(TimeoutError, match="routing_lock_deadline"):
            ordinary_remote.inspect(manifest)
        snapshot.assert_not_called()
    assert clock.sleeps > 0 and clock.now <= 100.2


def test_runtime_handoff_waits_for_lock_before_one_reservation(
    monkeypatch,
    tmp_path,
    ordinary_operation_guest,
    held_routing_lock,
):
    path, held = held_routing_lock
    clock = SimpleNamespace(now=100.0, sleeps=0)

    def sleep(seconds):
        clock.now += seconds
        clock.sleeps += 1
        if held:
            os.close(held.pop())

    fake_time = SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
    monkeypatch.setattr(operations, "time", fake_time)
    monkeypatch.setattr(authority, "time", fake_time, raising=False)
    monkeypatch.setattr(operations, "ROUTING_LOCK", path)
    store = authority.RouteAuthority(tmp_path / "authority")
    monkeypatch.setattr(authority, "RouteAuthority", lambda: store)
    reserve = Mock(wraps=store.reserve)
    monkeypatch.setattr(store, "reserve", reserve)
    request = {"action": "reserve", "holder": "a" * 64, "expected": digest(store.snapshot())}
    observed = authority.action(request)
    assert observed["authority"]["holder"] == request["holder"]
    assert reserve.call_count == 1 and clock.sleeps == 1


def test_periodic_mutation_lock_stays_nonblocking(
    monkeypatch,
    ordinary_operation_guest,
    held_routing_lock,
):
    path, _held = held_routing_lock
    monkeypatch.setattr(operations, "ROUTING_LOCK", path)
    with pytest.raises(BlockingIOError), operations.mutation_lock():
        pytest.fail("The periodic writer must not enter while another writer owns the lock")


@pytest.mark.parametrize("failure", ["deadline", "foreign-operation"])
def test_handoff_wait_cannot_override_deadline_or_foreign_operation(
    monkeypatch,
    ordinary_operation_guest,
    held_routing_lock,
    failure,
):
    path, held = held_routing_lock
    clock = SimpleNamespace(now=100.0, sleeps=0)

    def sleep(seconds):
        clock.now += seconds
        clock.sleeps += 1
        if failure == "foreign-operation" and held:
            os.close(held.pop())

    def idle():
        if failure == "foreign-operation" and not held:
            raise operations.OperationBlocked("ordinary_operation_in_progress")

    fake_time = SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
    monkeypatch.setattr(operations, "time", fake_time)
    monkeypatch.setattr(authority, "time", fake_time)
    monkeypatch.setattr(operations, "ROUTING_LOCK", path)
    monkeypatch.setattr(operations, "require_idle", idle)
    owner = Mock()
    monkeypatch.setattr(authority, "RouteAuthority", owner)
    error = TimeoutError if failure == "deadline" else operations.OperationBlocked
    with pytest.raises(error):
        authority.action({"action": "inspect"})
    owner.assert_not_called()
    assert clock.sleeps > 0 and clock.now <= 110


@pytest.mark.parametrize("boundary", ["inspection", "handoff"])
def test_lock_acquired_after_deadline_grants_no_admission(monkeypatch, tmp_path, boundary):
    clock = SimpleNamespace(now=100.0)
    native_flock = fcntl.flock

    def acquire(fd, flags):
        native_flock(fd, flags)
        clock.now = 101.1

    fake_time = SimpleNamespace(monotonic=lambda: clock.now, sleep=Mock())
    owner = ordinary_remote if boundary == "inspection" else operations
    monkeypatch.setattr(owner, "time", fake_time)
    monkeypatch.setattr(owner, "ROUTING_LOCK", tmp_path / "lock")
    monkeypatch.setattr(ordinary_remote, "DEADLINE", 101)
    monkeypatch.setattr(operations, "require_idle", lambda: None)
    monkeypatch.setattr(fcntl, "flock", acquire)
    context = (
        ordinary_remote.routing_lock()
        if boundary == "inspection"
        else operations.mutation_lock(deadline=101)
    )
    with pytest.raises(TimeoutError), context:
        pytest.fail("An expired observation cannot admit work")
    fake_time.sleep.assert_not_called()


@pytest.mark.parametrize("boundary", ["inspection", "handoff"])
def test_lock_permission_error_is_not_retried(monkeypatch, tmp_path, boundary):
    fake_time = SimpleNamespace(monotonic=lambda: 100.0, sleep=Mock())
    owner = ordinary_remote if boundary == "inspection" else operations
    monkeypatch.setattr(owner, "time", fake_time)
    monkeypatch.setattr(owner, "ROUTING_LOCK", tmp_path / "lock")
    monkeypatch.setattr(ordinary_remote, "DEADLINE", 101)
    monkeypatch.setattr(operations, "require_idle", lambda: None)
    acquire = Mock(side_effect=PermissionError("not allowed"))
    monkeypatch.setattr(fcntl, "flock", acquire)
    context = (
        ordinary_remote.routing_lock()
        if boundary == "inspection"
        else operations.mutation_lock(deadline=101)
    )
    with pytest.raises(PermissionError), context:
        pytest.fail("A permission error cannot admit work")
    acquire.assert_called_once()
    fake_time.sleep.assert_not_called()


def test_handoff_effect_error_is_never_replayed(monkeypatch, tmp_path, ordinary_operation_guest):
    store = authority.RouteAuthority(tmp_path / "authority")
    monkeypatch.setattr(authority, "RouteAuthority", lambda: store)
    persist = store.reserve

    def lost_reply(*args):
        persist(*args)
        raise RuntimeError("effect outcome unknown")

    reserve = Mock(side_effect=lost_reply)
    monkeypatch.setattr(store, "reserve", reserve)
    request = {"action": "reserve", "holder": "a" * 64, "expected": digest(store.snapshot())}
    with pytest.raises(RuntimeError, match="effect outcome unknown"):
        authority.action(request)
    reserve.assert_called_once_with(request["holder"], request["expected"])
    assert store.snapshot()["authority"]["holder"] == request["holder"]
