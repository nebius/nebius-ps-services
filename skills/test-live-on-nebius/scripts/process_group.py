"""Gate a subprocess on durable ownership; pin its process group through cleanup."""

import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from live_state import StateError


def reconcile_owned(journal):
    """Prove absence after a crash; never signal a potentially reused identity."""
    record = journal.data["evidence"].get("owned_process", {})
    if not record.get("active"):
        return
    pgid = record.get("pgid")
    if record.get("state") == "launch_pending" and pgid is None:
        # The durable PID receipt precedes release of the child's execution gate.
        journal.evidence(
            "owned_process",
            {"active": False, "state": "quiescent", "proof": "gate_not_released"},
        )
        return
    if type(pgid) is not int or pgid <= 1 or pgid != record.get("pid"):
        raise StateError("WRITER_IDENTITY_UNRESOLVED")
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        journal.evidence(
            "owned_process",
            {**record, "active": False, "state": "quiescent", "proof": "group_absent"},
        )
        return
    raise StateError("WRITER_NOT_PROVEN_QUIESCENT")


def run_owned(journal, command, *, cwd, env, deadline):
    read_status, write_status = os.pipe()
    read_gate, write_gate = os.pipe()
    process = None
    ready = False
    result = None
    journal.evidence("owned_process", {"active": True, "state": "launch_pending"})
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                str(write_status),
                str(read_gate),
                str(deadline),
                json.dumps(command),
            ],
            pass_fds=(write_status, read_gate),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        os.close(write_status)
        write_status = None
        os.close(read_gate)
        read_gate = None
        pending = b""
        while time.monotonic() < deadline:
            if not select.select(
                [read_status], [], [], min(1, max(0, deadline - time.monotonic()))
            )[0]:
                continue
            data = os.read(read_status, 4096)
            if not data:
                raise StateError("SUPERVISOR_LOST")
            pending += data
            if len(pending) > 8192:
                raise StateError("SUPERVISOR_PROTOCOL")
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                event = json.loads(line)
                if event == {"event": "ready"} and not ready:
                    ready = True
                    journal.evidence(
                        "owned_process",
                        {
                            "active": True,
                            "state": "running",
                            "pid": process.pid,
                            "pgid": process.pid,
                        },
                    )
                    os.write(write_gate, b"G")
                elif (
                    ready
                    and event.get("event") == "exit"
                    and type(event.get("code")) is int
                ):
                    result = event
                    break
                else:
                    raise StateError("SUPERVISOR_PROTOCOL")
            if result is not None:
                break
        if result is None:
            raise StateError("PROCESS_DEADLINE")
        return result
    finally:
        # Do not poll/reap the anchor before signaling: its live/zombie PID pins
        # the process-group identity even if the command has already exited.
        quiescent = process is None
        if process is not None:
            try:
                group_created = os.getpgid(process.pid) == process.pid
            except ProcessLookupError:
                group_created = False
            sender = os.killpg if group_created else os.kill
            for sig, delay in ((signal.SIGTERM, 0.2), (signal.SIGKILL, 0)):
                try:
                    sender(process.pid, sig)
                except ProcessLookupError:
                    pass
                if delay:
                    time.sleep(delay)
            process.wait(timeout=10)
            until = time.monotonic() + 5
            while time.monotonic() < until:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    quiescent = True
                    break
                time.sleep(0.05)
        for fd in (read_status, write_status, read_gate, write_gate):
            if fd is not None:
                os.close(fd)
        journal.evidence(
            "owned_process",
            {
                "active": not quiescent,
                "state": "quiescent" if quiescent else "unknown",
                "pid": process.pid if process else None,
                "pgid": process.pid if process else None,
            },
        )
        if not quiescent:
            raise StateError("WRITER_NOT_PROVEN_QUIESCENT")


def child():
    status, gate = int(sys.argv[1]), int(sys.argv[2])
    deadline = float(sys.argv[3])
    command = json.loads(sys.argv[4])
    signal.signal(signal.SIGTERM, lambda *_: None)
    signal.signal(signal.SIGINT, lambda *_: None)
    os.write(status, b'{"event":"ready"}\n')
    if os.read(gate, 1) != b"G":
        return 127
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        digest = hashlib.sha256()
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            readable = select.select([gate, process.stdout], [], [], 0.1)[0]
            if gate in readable and not os.read(gate, 1):
                raise BrokenPipeError("owner exited")
            if process.stdout in readable:
                chunk = os.read(process.stdout.fileno(), 65536)
                digest.update(chunk)
            if process.poll() is not None:
                break
        result = {
            "event": "exit",
            "code": process.returncode,
            "captured_output_sha256": digest.hexdigest(),
        }
        os.write(status, (json.dumps(result) + "\n").encode())
        # Remain the session anchor until the parent destroys the entire group.
        while time.monotonic() < deadline + 10:
            if select.select([gate], [], [], 0.1)[0] and not os.read(gate, 1):
                break
    finally:
        os.killpg(os.getpid(), signal.SIGKILL)


if __name__ == "__main__":
    child()
