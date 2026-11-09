import json
import os
import tempfile
import threading
from pathlib import Path

import pytest

from nebius_vpngw import package_worker as worker


@pytest.fixture
def transport(monkeypatch, tmp_path):
    root = tmp_path.resolve() / "workers"
    run = tmp_path.resolve() / "run"
    boot = tmp_path / "boot"
    boot.write_text("boot-a")
    monkeypatch.setattr(worker, "ROOT", root)
    monkeypatch.setattr(worker, "RUN", run)
    monkeypatch.setattr(worker, "BOOT", boot)
    # macOS has a shorter Unix socket path limit than the guest path.
    sockets = tempfile.TemporaryDirectory(prefix="vpngw-rpc-", dir="/tmp")
    monkeypatch.setattr(worker, "address", lambda key: str(Path(sockets.name) / "rpc.sock"))
    request = {"action": "prepare-guarded", "package": {"desired": {}}, "expected": None}
    key = worker.identity(request)
    worker.directory(root / key)
    worker.directory(run)
    worker.publish(root / key / "request.json", request)
    worker.publish(
        root / key / "transport.json",
        {
            "schema": worker.SCHEMA,
            "identity": key,
            "boot": "boot-a",
            "source": "a" * 64,
            "generation": 1,
            "unit": worker.unit(key, 1),
            "requests": {},
            "terminal": None,
            "waiting": False,
        },
    )
    value = worker.Transport(key)
    yield value, request
    value.close(False)
    value.listener.close()
    sockets.cleanup()


def test_receipt_survives_observer_loss_and_duplicate_submission(transport):
    value, request = transport
    assert value.receive() == request
    assert value.submit(request)["state"] == "RUNNING_OWNED"
    assert value.pending.empty()
    value.reply({"artifact": "exact"})
    result = value.submit(request)
    assert result == {"state": "PREPARED", "identity": value.key, "result": {"artifact": "exact"}}
    assert value.pending.empty()
    _, saved = worker.record(value.key)
    assert saved["requests"][worker.digest(request)]["state"] == "complete"


def test_no_next_phase_while_current_phase_is_running(transport):
    value, _ = transport
    with pytest.raises(RuntimeError, match="pending phase"):
        value.submit({"action": "publish-activation"})


def test_real_socket_requires_exact_identity_and_returns_existing_receipt(transport):
    value, request = transport
    thread = threading.Thread(target=value.serve)
    thread.start()
    try:
        result = worker.rpc(value.key, {"action": "submit", "payload": request})
        assert result["state"] == "RUNNING_OWNED"
        assert value.receive() == request
        value.reply({"prepared": True})
        assert worker.rpc(value.key, {"action": "submit", "payload": request})["result"] == {
            "prepared": True
        }
    finally:
        value.stopped.set()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_foreign_and_oversized_frames_rejected(transport):
    import io

    with pytest.raises(RuntimeError, match="incomplete"):
        worker.frame(io.BytesIO(b"{}"))
    with pytest.raises(RuntimeError, match="invalid"):
        worker.frame(io.BytesIO(b"[]\n"))


def test_protected_receipt_rejects_symlink_and_permissions(transport):
    value, _ = transport
    original = value.path.read_bytes()
    value.path.chmod(0o644)
    with pytest.raises(RuntimeError, match="unsafe"):
        worker.record(value.key)
    value.path.chmod(0o600)
    other = value.path.with_name("other.json")
    other.write_bytes(original)
    value.path.unlink()
    value.path.symlink_to(other)
    with pytest.raises(OSError):
        worker.record(value.key)
    value.path.unlink()
    worker.publish(value.path, json.loads(original))


def test_changed_source_cannot_restart_existing_worker(transport):
    value, request = transport
    with pytest.raises(RuntimeError, match="bootstrap differs"):
        worker.start(request, "foreign", retry=True)


def test_submission_identity_excludes_only_transport_observations():
    original = {"action": "reserve", "approval": "a", "package": {"desired": "exact"}}
    assert worker.identity(original) == worker.identity({**original, "observed": {"fresh": True}})
    assert worker.identity(original) != worker.identity({**original, "approval": "foreign"})


def test_socket_permissions_are_private(transport):
    value, _ = transport
    assert os.stat(worker.address(value.key)).st_mode & 0o777 == 0o600


def test_native_lock_waits_without_replacing_external_lock(monkeypatch, tmp_path):
    path = tmp_path / "native.lock"
    path.touch(mode=0o640)
    inode = path.stat().st_ino
    calls, waiting = [], []
    original = worker.fcntl.lockf

    def lock(fd, flags):
        calls.append(flags)
        if len(calls) == 1:
            raise BlockingIOError()
        return original(fd, flags)

    monkeypatch.setattr(worker.fcntl, "lockf", lock)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(worker, "set_waiting", waiting.append)
    with worker.native_lock(str(path)):
        assert path.stat().st_ino == inode
    assert calls == [worker.fcntl.LOCK_EX | worker.fcntl.LOCK_NB] * 2
    assert waiting == [True, False, False]
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError), worker.native_lock(str(link)):
        pytest.fail("symlink lock admitted")


def test_control_authority_error_is_blocked_not_an_endless_lost_reply(monkeypatch):
    def rejected(*_args):
        raise RuntimeError("untrusted private diagnostic")

    monkeypatch.setattr(worker, "status", rejected)
    assert worker.control({"action": "worker-status", "identity": "a" * 64}) == {
        "state": "BLOCKED",
        "identity": "a" * 64,
        "result": None,
    }


def test_control_lost_socket_reply_reads_exact_submitted_phase(monkeypatch):
    payload = {"action": "prepare-package", "plan_digest": "approved"}
    monkeypatch.setattr(worker, "rpc", lambda *_a: (_ for _ in ()).throw(EOFError()))

    def readback(key, phase):
        assert key == "a" * 64 and phase == worker.digest(payload)
        return {"state": "PREPARED", "identity": key, "result": {"exact": True}}

    monkeypatch.setattr(worker, "status", readback)
    assert worker.control({"action": "worker-submit", "identity": "a" * 64, "payload": payload})[
        "result"
    ] == {"exact": True}


@pytest.mark.parametrize("code", [0, 1])
def test_collected_unit_requires_complete_native_absence_evidence(monkeypatch, tmp_path, code):
    from types import SimpleNamespace

    boot = tmp_path / "boot"
    boot.write_text("current")
    monkeypatch.setattr(worker, "BOOT", boot)
    fields = "LoadState=not-found\nActiveState=inactive\nSubState=dead\nControlGroup=\nMainPID=0\nControlPID=0\nJob=\n"
    monkeypatch.setattr(
        worker.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=code, stdout=fields)
    )
    assert worker.unit_running({"boot": "current", "unit": "exact.service"}) is False
    fields = ""
    with pytest.raises(RuntimeError, match="incomplete"):
        worker.unit_running({"boot": "current", "unit": "exact.service"})
