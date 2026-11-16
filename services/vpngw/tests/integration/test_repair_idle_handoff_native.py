"""Real systemd/cgroup/process retirement in an explicitly disposable container.

The fixture supplies a reserved worker with a declared receipt and held locks.
Production retirement must prove or refuse native process settlement. This does
not substitute for live conversion or full repair qualification.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from nebius_vpngw import package_worker as worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires the explicit disposable systemd fixture",
    ),
]


def eventually(predicate):
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, "native retirement fixture did not settle"
        time.sleep(0.05)


@pytest.mark.parametrize("mode", ["idle", "accepted", "child", "foreign-frozen", "late-request", "lost-reply"])
def test_native_repair_retires_only_exact_idle_process(monkeypatch, tmp_path, mode):
    assert Path("/.dockerenv").exists() and os.geteuid() == 0
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    monkeypatch.setattr(worker, "ROOT", tmp_path / "workers")
    monkeypatch.setattr(worker, "BACKEND", "handoff")
    request = {"action": "reserve", "package": {"host": "fixture-" + uuid.uuid4().hex}}
    key = worker.identity(request)
    root = worker.ROOT / key
    worker.directory(worker.ROOT)
    worker.directory(root)
    source = (
        "import fcntl,pathlib,subprocess,time\n"
        "root=pathlib.Path(__file__).parent\n"
        "locks=[(root/name).open('r+b') for name in ('instance.lock','deployment.lock')]\n"
        "[fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB) for lock in locks]\n"
        + ("child=subprocess.Popen(['/bin/sleep','300'])\n" if mode == "child" else "")
        + "(root/'ready').touch()\n"
        "while True: time.sleep(1)\n"
    )
    bootstrap = root / "bootstrap.py"
    bootstrap.write_text(source)
    bootstrap.chmod(0o600)
    for name in ("submission.lock", "instance.lock", "deployment.lock"):
        (root / name).touch(mode=0o600)
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    value = {
        "schema": worker.SCHEMA, "identity": key,
        "boot": worker.BOOT.read_text().strip(), "source": source_hash,
        "generation": 1, "unit": worker.unit(key, 1), "terminal": None, "waiting": False,
        "requests": {worker.digest(request): {"request": request,
            "state": "accepted" if mode == "accepted" else "complete",
            "result": None if mode == "accepted" else {"reserved": True}}},
    }
    worker.publish(root / "request.json", request)
    worker.publish(root / "transport.json", value)
    expected = {"identity": key, "request": request, "source": source_hash}
    operation = uuid.uuid4().hex
    control = worker._retirement_control
    publication = worker.publish
    submitted = False
    try:
        submitted = True
        subprocess.run([
            "systemd-run", "--quiet", "--collect", "--no-ask-password", "--unit=" + value["unit"],
            "--property=Type=exec", "--property=ExitType=cgroup", "--property=Restart=no",
            "--property=RuntimeMaxSec=300", "/usr/bin/python3", "-B", str(bootstrap),
            key, "1", source_hash,
        ], check=True, capture_output=True, timeout=15)
        eventually(lambda: (root / "ready").exists() and worker.unit_running(value))
        with (root / "deployment.lock").open("rb") as guard, pytest.raises(BlockingIOError):
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        initial_bytes = (root / "transport.json").read_bytes()
        initial_owner = worker._retirement_owner(value, require_solitary=False)
        if mode == "foreign-frozen":
            control(value["unit"], "freeze")
            eventually(lambda: worker._retirement_frozen(initial_owner))
        if mode == "late-request":
            def late_request(unit, action):
                control(unit, action)
                if action == "freeze":
                    following = {"action": "prepare-package"}
                    value["requests"][worker.digest(following)] = {
                        "request": following, "state": "accepted", "result": None,
                    }
                    publication(root / "transport.json", value)
            monkeypatch.setattr(worker, "_retirement_control", late_request)
        if mode == "lost-reply":
            def lose_reply(path, content):
                if content.get("phase") == "stopped":
                    raise ConnectionResetError("fixture loses the completion response")
                publication(path, content)
            monkeypatch.setattr(worker, "publish", lose_reply)
            with pytest.raises(ConnectionResetError):
                worker.retire_idle_for_repair(expected, operation)
            monkeypatch.setattr(worker, "publish", publication)
            assert not worker.unit_running(value)
        if mode in {"child", "foreign-frozen"}:
            reason = "executing children" if mode == "child" else "frozen by another"
            with pytest.raises(RuntimeError, match=reason):
                worker.retire_idle_for_repair(expected, operation)
        else:
            result = worker.retire_idle_for_repair(expected, operation)
            assert result["state"] == ("PENDING" if mode in {"accepted", "late-request"} else "RETIRED")
        if mode in {"idle", "lost-reply"}:
            assert not worker.unit_running(value)
            assert (root / "transport.json").read_bytes() == initial_bytes
            with (root / "deployment.lock").open("rb") as guard:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert worker.read(root / "retirements" / (operation + ".json"))["phase"] == "stopped"
        else:
            assert worker.unit_running(value)
            assert worker._retirement_owner(value, require_solitary=False) == initial_owner
            assert worker._retirement_frozen(initial_owner) == (mode == "foreign-frozen")
            with (root / "deployment.lock").open("rb") as guard, pytest.raises(BlockingIOError):
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if submitted and worker.unit_running(value):
            # Fixture cleanup, after assertions; never evidence of product success.
            subprocess.run(["systemctl", "stop", value["unit"]], check=True,
                           capture_output=True, timeout=15)
        if submitted:
            eventually(lambda: not worker.unit_running(value))
