"""Real disposable process qualification, without Terraform or cluster access."""

import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from nebius_cxcli import owned_process as owner
from nebius_cxcli.lease_clock import elapsed


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    pytest.fail("Disposable process did not reach its expected state")


def running(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    return (
        result.returncode == 0
        and bool(result.stdout.strip())
        and not result.stdout.lstrip().startswith("Z")
    )


@pytest.fixture
def scope():
    value = owner.ExecutionScope("test-invocation", elapsed() + 30)
    value.activate()
    try:
        yield value
    finally:
        try:
            assert value.close(elapsed() + 15)
        finally:
            value.deactivate()


def tree_command(path, *, ignore_term=False, flood=False):
    descendant = "import time; time.sleep(60)"
    return [
        sys.executable,
        "-c",
        f"""
import os, signal, subprocess, sys, time
from pathlib import Path
if {ignore_term!r}:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, "-c", {descendant!r}])
Path({str(path)!r}).write_text(str(os.getpid()) + " " + str(child.pid))
if {flood!r}:
    while True:
        os.write(1, b"x" * 65536)
time.sleep(60)
""",
    ]


def test_success_preserves_io_and_exit_status(scope):
    result = owner.run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read()); sys.exit(7)"],
        input="payload",
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.stdout == "payload\n" and result.returncode == 7
    assert scope._processes[0].quiescent


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("text_mode", [False, True])
def test_slow_reader_receives_all_input_while_both_outputs_drain(scope, managed, text_mode):
    if not managed:
        scope.deactivate()
    data = "ö" * 200000 if text_mode else b"x" * 200000
    expected_size = len(data.encode("utf-8") if text_mode else data)
    result = owner.run(
        [
            sys.executable,
            "-c",
            """
import os, sys, time
time.sleep(0.6)
os.write(1, b'o' * 150000)
os.write(2, b'e' * 150000)
data = sys.stdin.buffer.read()
os.write(1, str(len(data)).encode() + b'\\r\\n')
sys.exit(7)
""",
        ],
        input=data,
        capture_output=True,
        **({"encoding": "utf-8"} if text_mode else {}),
        abort_check=lambda: None,
        timeout=4,
    )
    assert result.returncode == 7
    expected = b"o" * 150000 + str(expected_size).encode() + b"\r\n"
    assert result.stdout == (expected.decode().replace("\r\n", "\n") if text_mode else expected)
    assert result.stderr == ("e" * 150000 if text_mode else b"e" * 150000)
    if managed:
        assert scope._processes[0].quiescent


def test_abort_during_input_backpressure_keeps_callbacks_on_caller_thread(scope, tmp_path):
    import threading

    ready = tmp_path / "reader-ready"
    caller = threading.get_ident()

    def abort():
        assert threading.get_ident() == caller
        return "fixture cancellation" if ready.exists() else None

    with pytest.raises(RuntimeError, match="fixture cancellation"):
        owner.run(
            [
                sys.executable,
                "-c",
                f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)",
            ],
            input=b"x" * 200000,
            capture_output=True,
            abort_check=abort,
            timeout=5,
        )
    assert scope._processes[0].quiescent


def test_timeout_during_input_backpressure_stops_owned_process(scope):
    with pytest.raises(subprocess.TimeoutExpired):
        owner.run(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            input=b"x" * 200000,
            capture_output=True,
            timeout=0.5,
        )
    assert scope._processes[0].quiescent


def test_cleanup_preserves_completion_ack_for_unpolled_process(scope):
    process = owner.popen([sys.executable, "-c", "pass"])
    process._process.wait(timeout=5)  # Completion exists but caller has not consumed it.
    assert scope.close(elapsed() + 15)
    assert process.quiescent


@pytest.fixture
def completed_worker():
    channels = []

    def create(*, quiescent):
        channel, peer = socket.socketpair()
        channels.append(channel)
        with peer:
            peer.sendall(json.dumps({"returncode": 0, "quiescent": quiescent}).encode())
        process = object.__new__(owner.ManagedProcess)
        process._channel = channel
        process._finished = False
        process.quiescent = False
        process.returncode = None
        process._process = SimpleNamespace(
            poll=lambda: 0,
            wait=lambda timeout=None: 0,
            communicate=lambda: ("", ""),
            stdout=io.StringIO(""),
            stderr=io.StringIO(""),
        )
        return process

    yield create
    for channel in channels:
        channel.close()


@pytest.mark.parametrize("method", ["poll", "wait", "communicate"])
@pytest.mark.parametrize("quiescent", [False, True])
def test_success_requires_confirmed_group_shutdown(completed_worker, method, quiescent):
    process = completed_worker(quiescent=quiescent)
    getattr(process, method)()
    assert (process.returncode == 0) is quiescent
    assert process.quiescent is quiescent


@pytest.mark.parametrize("quiescent", [False, True])
def test_streaming_terraform_requires_confirmed_group_shutdown(
    completed_worker, monkeypatch, tmp_path, quiescent
):
    from nebius_cxcli.terraform_ops import _stream_json_events

    process = completed_worker(quiescent=quiescent)
    monkeypatch.setattr(owner, "popen", lambda *args, **kwargs: process)
    if quiescent:
        _stream_json_events(["terraform", "apply", "-json"], cwd=tmp_path, timeout=5)
    else:
        with pytest.raises(RuntimeError, match="Terraform command .* failed"):
            _stream_json_events(["terraform", "apply", "-json"], cwd=tmp_path, timeout=5)


@pytest.mark.parametrize("ignore_term,flood", [(False, False), (True, False), (False, True)])
def test_cancel_stops_child_and_grandchild_independent_of_output(
    scope, tmp_path, ignore_term, flood
):
    ready = tmp_path / "ready"
    process = owner.popen(
        tree_command(ready, ignore_term=ignore_term, flood=flood), stdout=subprocess.PIPE
    )
    try:
        wait_for(ready.exists)
        pids = [int(p) for p in ready.read_text().split()]
        scope.cancel()
        process.wait(timeout=12)
        assert process.quiescent
        assert all(not running(pid) for pid in pids)
        with pytest.raises(RuntimeError, match="authority ended"):
            scope.grant(elapsed() + 60)
    finally:
        process.stdout.close()


def test_expiry_stops_quiet_process_without_parent_polling(scope, tmp_path):
    ready = tmp_path / "ready"
    scope.deadline = elapsed() + 1
    process = owner.popen(tree_command(ready))
    wait_for(ready.exists)
    pids = [int(p) for p in ready.read_text().split()]
    process.wait(timeout=12)
    assert process.quiescent and all(not running(pid) for pid in pids)


def test_confirmed_grants_extend_existing_supervisor(scope, tmp_path):
    ready = tmp_path / "ready"
    scope.deadline = elapsed() + 1
    process = owner.popen(tree_command(ready))
    wait_for(ready.exists)
    scope.grant(elapsed() + 10)
    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=1.2)
    scope.cancel()
    process.wait(timeout=12)
    assert process.quiescent


def test_parent_sigkill_closes_lifetime_channel_and_stops_tree(tmp_path):
    ready = tmp_path / "ready"
    command = tree_command(ready)
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"""
import time
from nebius_cxcli.owned_process import ExecutionScope, popen
from nebius_cxcli.lease_clock import elapsed
scope = ExecutionScope("parent-death-test", elapsed()+30)
scope.activate()
popen({command!r})
time.sleep(60)
""",
        ]
    )
    try:
        wait_for(ready.exists)
        pids = [int(p) for p in ready.read_text().split()]
        parent.kill()
        parent.wait(timeout=5)
        wait_for(lambda: all(not running(pid) for pid in pids))
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=15)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_signals_cancel_once_and_restore_handlers(scope, tmp_path, sig):
    ready = tmp_path / "ready"
    process = owner.popen(tree_command(ready))
    wait_for(ready.exists)
    original = scope._handlers[sig]
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), sig)
    os.kill(os.getpid(), sig)  # Repeated signals must not interrupt cleanup.
    assert scope.close(elapsed() + 15)
    scope.deactivate()
    assert signal.getsignal(sig) == original
    assert process.quiescent


@pytest.mark.parametrize("message", [None, {"owner": "startup-test", "cancel": True}])
def test_dead_or_cancelled_parent_never_launches_command(tmp_path, message):
    ready = tmp_path / "must-not-launch"
    parent, child = socket.socketpair()
    if message is not None:
        parent.sendall(json.dumps(message).encode() + b"\n")
    parent.close()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "nebius_cxcli.owned_process_worker",
            str(child.fileno()),
            "startup-test",
            str(elapsed() + 10),
            "-1",
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(ready)!r}).touch()",
        ],
        pass_fds=(child.fileno(),),
    )
    child.close()
    process.wait(timeout=5)
    assert not ready.exists()


def test_supervision_does_not_bypass_unit_test_cloud_guard(scope):
    with pytest.raises(AssertionError, match="Network access is disabled"):
        owner.popen(["kubectl", "get", "nodes"])
