"""Transient transport for the existing HA package owner, never effect authority.

Only root can submit phases. The ordinary operation journal still admits and
records every mutation. These package-owner receipts make submission/readback
idempotent across SSH loss; they cannot authorize activation themselves.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import queue
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path("/var/lib/nebius-vpngw/runtime-migration/workers")
RUN = Path("/run/nebius-vpngw")
BOOT = Path("/proc/sys/kernel/random/boot_id")
SCHEMA = "nebius-vpngw.package-worker.v1"
LIMIT = 128 * 1024 * 1024
ACTIVE = False
TRANSPORT = None


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def identity(request: dict) -> str:
    if request.get("action") not in {"prepare-guarded", "reserve"}:
        raise ValueError("package worker action is invalid")
    # Transport payloads and fresh observations are not new transaction identity.
    # The original immutable package envelope and authority are always retained.
    return digest(
        {k: v for k, v in request.items() if k not in {"observed", "staged_package", "peer"}}
    )


def directory(path: Path) -> None:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise RuntimeError("package worker directory contains a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise RuntimeError("package worker directory is unsafe")


def read(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise RuntimeError("package worker record is unsafe")
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise RuntimeError("package worker record exceeds limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("package worker record is invalid")
    return value


def publish(path: Path, value: dict) -> None:
    raw = json.dumps(value, sort_keys=True).encode()
    if len(raw) > LIMIT:
        raise RuntimeError("package worker record exceeds limit")
    temporary = path.with_name(path.name + "." + os.urandom(12).hex())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def record(key: str) -> tuple[Path, dict]:
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("package worker identity is invalid")
    path = ROOT / key / "transport.json"
    value = read(path)
    if (
        set(value)
        != {
            "schema",
            "identity",
            "boot",
            "source",
            "generation",
            "unit",
            "requests",
            "terminal",
            "waiting",
        }
        or value["schema"] != SCHEMA
        or value["identity"] != key
        or not isinstance(value["generation"], int)
        or value["generation"] < 1
        or value["unit"] != unit(key, value["generation"])
        or not isinstance(value["requests"], dict)
        or not isinstance(value["waiting"], bool)
        or value["terminal"] not in {None, "complete", "failed"}
    ):
        raise RuntimeError("package worker record changed")
    for request_key, entry in value["requests"].items():
        if (
            set(entry) != {"request", "result", "state"}
            or request_key != digest(entry["request"])
            or entry["state"] not in {"accepted", "complete", "failed"}
            or (entry["result"] is not None and not isinstance(entry["result"], dict))
        ):
            raise RuntimeError("package phase receipt changed")
    return path, value


def unit(key: str, generation: int) -> str:
    return f"nebius-vpngw-package-{key}-{generation}.service"


def address(key: str) -> str:
    return str(RUN / ("package-" + key[:32] + ".sock"))


def frame(stream: Any) -> dict:
    raw = stream.readline(LIMIT + 1)
    if len(raw) > LIMIT or not raw.endswith(b"\n"):
        raise RuntimeError("package worker frame is incomplete")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("package worker frame is invalid")
    return value


def rpc(key: str, request: dict) -> dict:
    target = Path(address(key))
    info = target.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RuntimeError("package worker socket is unsafe")
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(10)
        client.connect(str(target))
        raw = json.dumps({"identity": key, **request}).encode() + b"\n"
        if len(raw) > LIMIT:
            raise ValueError("package request exceeds limit")
        client.sendall(raw)
        with client.makefile("rb") as stream:
            return frame(stream)


def unit_running(value: dict) -> bool:
    if value["boot"] != BOOT.read_text().strip():
        return False
    result = subprocess.run(
        [
            "systemctl",
            "show",
            "--all",
            value["unit"],
            "--property=LoadState,ActiveState,SubState,ControlGroup,MainPID,ControlPID,Job",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if set(fields) != {
        "LoadState",
        "ActiveState",
        "SubState",
        "ControlGroup",
        "MainPID",
        "ControlPID",
        "Job",
    }:
        raise RuntimeError("package unit observation is incomplete")
    if result.returncode and not (
        fields["LoadState"] == "not-found"
        and fields["ActiveState"] == "inactive"
        and fields["MainPID"] == fields["ControlPID"] == "0"
        and not fields["ControlGroup"]
        and not fields["Job"]
    ):
        raise RuntimeError("package unit observation failed")
    group = fields["ControlGroup"]
    if group:
        base = Path("/sys/fs/cgroup") / group.lstrip("/")
        if ".." in base.parts:
            raise RuntimeError("package cgroup is invalid")
        if base.exists() and any(p.read_text().strip() for p in base.rglob("cgroup.procs")):
            return True
    return bool(
        fields["Job"]
        or fields["MainPID"] != "0"
        or fields["ControlPID"] != "0"
        or fields["ActiveState"] in {"activating", "deactivating"}
    )


def status(key: str, request_key: str | None = None) -> dict:
    try:
        _path, value = record(key)
    except FileNotFoundError:
        return {"state": "NOT_SUBMITTED", "identity": key, "result": None}
    if request_key is None:
        request_key = initial(key)
    entry = value["requests"].get(request_key) if request_key else None
    if (
        value["boot"] == BOOT.read_text().strip()
        and entry
        and entry["state"] == "complete"
        and (entry["request"]["action"] != "reserve" or unit_running(value))
    ):
        return {"state": "PREPARED", "identity": key, "result": entry["result"]}
    if unit_running(value):
        if entry is None and value["requests"]:
            return {"state": "NOT_SUBMITTED", "identity": key, "result": None}
        return {
            "state": "WAIT_EXTERNAL_LOCK" if value["waiting"] else "RUNNING_OWNED",
            "identity": key,
            "result": None,
        }
    request = read(ROOT / key / "request.json")
    if (
        entry
        and entry["request"]["action"] == "complete"
        and value["boot"] == BOOT.read_text().strip()
    ):
        handoff: Any = sys.modules.get("_vpngw_handoff_remote")
        if handoff is None:
            from .deploy import handoff_remote as handoff
        completed = handoff.completed_receipt(request, entry["request"])
        if completed is not None:
            return {"state": "PREPARED", "identity": key, "result": completed}
    operations: Any = sys.modules.get("_vpngw_ordinary_operations")
    migration: Any = sys.modules.get("_vpngw_runtime_migration")
    if operations is None or migration is None:
        from . import ordinary_operations as operations
        from . import runtime_migration as migration
    journal = operations.Journal().read()
    if journal is not None and journal["state"] != "complete":
        package = migration.state()
        if (
            package is None
            or package["operation"] != journal["id"]
            or package["manifest"] != request["package"]["desired"]
            or journal["purpose"] != "ha-handoff"
        ):
            return {"state": "BLOCKED", "identity": key, "result": None}
        manager = operations.Manager(time.monotonic() + 20)
        if not operations.handoff_recoverable(journal, manager, inhibited_quiescence=True):
            return {"state": "RUNNING_OWNED", "identity": key, "result": None}
    return {
        "state": "READY_TO_CONTINUE",
        "identity": key,
        "result": {"generation": value["generation"]},
    }


def lookup(request: dict) -> dict:
    key = identity(request)
    path = ROOT / key / "transport.json"
    if not path.exists() and not path.is_symlink():
        return {"state": "NOT_SUBMITTED", "identity": key, "result": None}
    return status(key, digest(read(ROOT / key / "request.json")))


def initial(key: str) -> str:
    return digest(read(ROOT / key / "request.json"))


def inspect_reservation(expected: dict) -> dict:
    key = expected["identity"]
    path = ROOT / key / "transport.json"
    if not path.exists() and not path.is_symlink():
        return {"state": "NOT_SUBMITTED", "identity": key, "result": None}
    _, value = record(key)
    request = read(ROOT / key / "request.json")
    if (
        identity(request) != key
        or value["source"] != expected["source"]
        or {k: v for k, v in request.items() if k != "staged_package"} != expected["request"]
    ):
        raise RuntimeError("package reservation readback differs")
    return status(key, initial(key))


def receipts(key: str) -> dict:
    _, value = record(key)
    if value["boot"] != BOOT.read_text().strip():
        raise RuntimeError("package receipts belong to a previous boot")
    return {
        entry["request"]["action"]: {"request": entry["request"], "result": entry["result"]}
        for entry in value["requests"].values()
        if entry["state"] == "complete"
        and entry["request"]["action"] in {"prepare-package", "publish-activation", "complete"}
    }


def set_waiting(waiting: bool) -> None:
    if TRANSPORT is not None:
        with TRANSPORT.lock:
            TRANSPORT.value["waiting"] = waiting
            publish(TRANSPORT.path, TRANSPORT.value)


@contextlib.contextmanager
def native_lock(path: str):
    """Wait for native exclusion without deleting locks or cancelling writers."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o640)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
        ):
            raise RuntimeError("native package lock is unsafe")
        while True:
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                set_waiting(True)
                time.sleep(0.5)
        set_waiting(False)
        yield
    finally:
        set_waiting(False)
        os.close(fd)


def children_running() -> bool:
    if TRANSPORT is None:
        raise RuntimeError("package process has no durable worker")
    groups = Path("/proc/self/cgroup").read_text().splitlines()
    group = next((line.split(":", 2)[2] for line in groups if line.startswith("0::")), None)
    if (
        group is None
        or not group.endswith("/" + TRANSPORT.value["unit"])
        or ".." in Path(group).parts
    ):
        raise RuntimeError("package process cgroup is unverified")
    base = Path("/sys/fs/cgroup") / group.lstrip("/")
    if not (base / "cgroup.procs").exists():
        raise RuntimeError("package process cgroup is unavailable")
    return any(
        int(pid) != os.getpid()
        for path in base.rglob("cgroup.procs")
        for pid in path.read_text().split()
    )


def start(request: dict, source: str, *, retry: bool = False) -> dict:
    key = identity(request)
    directory(ROOT)
    directory(ROOT / key)
    directory(RUN)
    guard = os.open(ROOT / key / "submission.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    instance_guard = os.open(
        ROOT / key / "instance.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = ROOT / key / "transport.json"
        generation = 1
        source_hash = hashlib.sha256(source.encode()).hexdigest()
        if path.exists() or path.is_symlink():
            _, previous = record(key)
            if previous["source"] != source_hash:
                raise RuntimeError("package worker bootstrap differs from admitted source")
            observed = status(key, initial(key))
            if observed["state"] != "READY_TO_CONTINUE" or not retry:
                return dict(observed, phase=initial(key))
            # Receipt inspection does not grant replay. The canonical owner will
            # independently prove journal settlement and inhibition on restart.
            generation = previous["generation"] + 1
            request = read(ROOT / key / "request.json")
        # A running worker holds this lock for its entire lifetime. A delayed
        # start must verify the immutable generation after taking the same lock.
        try:
            fcntl.flock(instance_guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status(key, initial(key)), phase=initial(key))
        publish(ROOT / key / "request.json", request)
        bootstrap = ROOT / key / "bootstrap.py"
        if bootstrap.exists():
            if hashlib.sha256(bootstrap.read_bytes()).hexdigest() != source_hash:
                raise RuntimeError("package worker source changed")
        else:
            fd = os.open(bootstrap, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(source.encode())
                stream.flush()
                os.fsync(stream.fileno())
        publish(
            path,
            dict(
                schema=SCHEMA,
                identity=key,
                boot=BOOT.read_text().strip(),
                source=source_hash,
                generation=generation,
                unit=unit(key, generation),
                requests={},
                terminal=None,
                waiting=False,
            ),
        )
        fcntl.flock(instance_guard, fcntl.LOCK_UN)
        try:
            subprocess.run(
                [
                    "systemd-run",
                    "--quiet",
                    "--collect",
                    "--no-ask-password",
                    "--unit=" + unit(key, generation),
                    "--property=Type=exec",
                    "--property=ExitType=cgroup",
                    "--property=Restart=no",
                    "--property=RuntimeMaxSec=infinity",
                    "--property=StandardOutput=null",
                    "--property=StandardError=null",
                    "/usr/bin/python3",
                    "-B",
                    str(bootstrap),
                    key,
                    str(generation),
                    source_hash,
                ],
                capture_output=True,
                timeout=15,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            # A failed submission observation does not establish cancellation.
            return dict(status(key, initial(key)), phase=initial(key))
        return {"state": "RUNNING_OWNED", "identity": key, "result": None, "phase": initial(key)}
    finally:
        os.close(instance_guard)
        os.close(guard)


class Transport:
    def __init__(self, key: str):
        self.key = key
        self.path, self.value = record(key)
        self.lock = threading.Lock()
        self.pending: queue.Queue[dict] = queue.Queue()
        self.current: str | None = None
        self.stopped = threading.Event()
        self.listener = socket.socket(socket.AF_UNIX)
        target = Path(address(key))
        if target.exists() or target.is_symlink():
            info = target.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                raise RuntimeError("package socket predecessor is unsafe")
            target.unlink()  # Submission already proved the old cgroup settled.
        self.listener.bind(str(target))
        os.chmod(target, 0o600)
        self.listener.listen(8)
        self.listener.settimeout(0.2)
        initial = read(ROOT / key / "request.json")
        self.submit(initial)

    def submit(self, payload: dict) -> dict:
        key = digest(payload)
        with self.lock:
            entry = self.value["requests"].get(key)
            if entry is not None:
                return {
                    "state": "PREPARED" if entry["state"] == "complete" else "RUNNING_OWNED",
                    "identity": self.key,
                    "result": entry["result"],
                }
            if self.value["terminal"] or any(
                e["state"] == "accepted" for e in self.value["requests"].values()
            ):
                raise RuntimeError("package owner has another pending phase")
            # Fresh observations are nonce-bound and can be retired after delivery.
            for old_key, old in list(self.value["requests"].items()):
                if old["request"].get("action") == "verify-prepared" and old["state"] == "complete":
                    del self.value["requests"][old_key]
            if len(self.value["requests"]) >= 64:
                raise RuntimeError("package phase history exceeds limit")
            self.value["requests"][key] = {"request": payload, "result": None, "state": "accepted"}
            publish(self.path, self.value)
            self.pending.put(payload)
            return {"state": "RUNNING_OWNED", "identity": self.key, "result": None}

    def serve(self) -> None:
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            with client:
                client.settimeout(10)
                try:
                    if hasattr(socket, "SO_PEERCRED"):
                        _pid, uid, _gid = struct.unpack(
                            "3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                        )
                        if uid != os.geteuid():
                            raise RuntimeError("package socket peer is unauthorized")
                    with client.makefile("rb") as stream:
                        request = frame(stream)
                    if request.get("identity") != self.key:
                        raise RuntimeError("package socket identity differs")
                    if request.get("action") != "submit" or set(request) != {
                        "identity",
                        "action",
                        "payload",
                    }:
                        raise RuntimeError("package socket action is invalid")
                    result = self.submit(request["payload"])
                except Exception:
                    result = {"state": "BLOCKED", "identity": self.key, "result": None}
                with contextlib.suppress(OSError):
                    client.sendall(json.dumps(result).encode() + b"\n")

    def receive(self) -> dict:
        value = self.pending.get()
        self.current = digest(value)
        return json.loads(json.dumps(value))

    def reply(self, value: dict) -> None:
        with self.lock:
            if self.current is None:
                raise RuntimeError("package reply has no admitted phase")
            self.value["requests"][self.current].update(state="complete", result=value)
            publish(self.path, self.value)
            self.current = None

    def close(self, failed: bool) -> None:
        with self.lock:
            if self.current is not None:
                self.value["requests"][self.current]["state"] = "failed"
            self.value["terminal"] = "failed" if failed else "complete"
            publish(self.path, self.value)
        self.stopped.set()


def control(request: dict) -> dict:
    """Return typed readback even when guest authority checks reject a request."""
    action = request.get("action")
    key = request.get("identity")
    try:
        if action in {"worker-start", "worker-find"}:
            key = identity(request["request"])
        elif action == "worker-reservation":
            key = request["reservation"]["identity"]
        if not isinstance(key, str):
            raise ValueError("package control identity is missing")
        if action == "worker-start":
            return start(request["request"], request["source"], retry=request["retry"])
        if action == "worker-status":
            return status(key, request.get("phase"))
        if action == "worker-find":
            return lookup(request["request"])
        if action == "worker-reservation":
            return inspect_reservation(request["reservation"])
        if action == "worker-receipts":
            return receipts(key)
        if action == "worker-submit":
            try:
                return rpc(key, {"action": "submit", "payload": request["payload"]})
            except (OSError, EOFError):
                # A missing socket or lost local ACK is resolved from the same
                # request receipt before any resubmission is considered.
                return status(key, digest(request["payload"]))
        raise ValueError("invalid worker control action")
    except subprocess.TimeoutExpired:
        return {"state": "RUNNING_OWNED", "identity": key, "result": None}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return {"state": "BLOCKED", "identity": key, "result": None}


def main(key: str, generation: int, source: str) -> None:
    global ACTIVE, TRANSPORT
    handoff_remote: Any = sys.modules.get("_vpngw_handoff_remote")
    if handoff_remote is None:
        from .deploy import handoff_remote

    instance_guard = os.open(ROOT / key / "instance.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(instance_guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, value = record(key)
        if (
            value["boot"] != BOOT.read_text().strip()
            or value["generation"] != generation
            or value["source"] != source
            or hashlib.sha256((ROOT / key / "bootstrap.py").read_bytes()).hexdigest() != source
        ):
            raise RuntimeError("package worker execution identity differs")
        # PID1 supplies the unit's cgroup. A shell or stale unit cannot acquire
        # package authority by invoking a copied bootstrap.
        groups = Path("/proc/self/cgroup").read_text().splitlines()
        if not any(line.endswith("/" + value["unit"]) for line in groups):
            raise RuntimeError("package worker is outside its admitted unit")
        ACTIVE = True
        transport = Transport(key)
        TRANSPORT = transport
        thread = threading.Thread(target=transport.serve, daemon=True)
        thread.start()
        handoff_remote.receive, handoff_remote.reply = transport.receive, transport.reply
        failed = True
        try:
            handoff_remote.main(durable=True)
            failed = False
        finally:
            transport.close(failed)
            thread.join(timeout=11)
            transport.listener.close()
            TRANSPORT = None
    finally:
        os.close(instance_guard)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), sys.argv[3])
