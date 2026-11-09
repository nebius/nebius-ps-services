import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nebius_vpngw.deploy.ssh_commands import run


class Channel:
    def __init__(self, *, exit_ready=True, noisy=False):
        self.output = [b"a" * 70000]
        self.errors = [b"e" * 70000]
        self.ready = exit_ready
        self.noisy = noisy
        self.closed = False
        self.exit_called = False

    def recv_ready(self):
        return bool(self.output) or self.noisy

    def recv_stderr_ready(self):
        return bool(self.errors) or self.noisy

    def recv(self, _):
        return self.output.pop() if self.output else b"n" * 4096

    def recv_stderr(self, _):
        return self.errors.pop() if self.errors else b"e" * 4096

    def exit_status_ready(self):
        return self.ready

    def recv_exit_status(self):
        assert self.ready
        self.exit_called = True
        return 0

    def close(self):
        self.closed = True


def client(channel):
    return SimpleNamespace(
        exec_command=lambda *_args, **_kwargs: (None, SimpleNamespace(channel=channel), None),
        close=Mock(),
    )


def test_drains_both_streams_retains_only_bounded_output():
    channel = Channel()
    code, stdout, stderr = run(client(channel), "probe", deadline=time.monotonic() + 1, limit=16)
    assert (code, stdout, stderr) == (0, b"a" * 16, b"e" * 16)
    assert channel.closed and channel.exit_called


@pytest.mark.parametrize("noisy", [False, True])
def test_missing_exit_status_or_continuous_output_cannot_defeat_deadline(noisy):
    channel = Channel(exit_ready=False, noisy=noisy)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        run(client(channel), "probe", deadline=started + 0.05)
    assert time.monotonic() - started < 1
    assert channel.closed and not channel.exit_called


def test_expired_budget_never_submits():
    transport = Mock()
    with pytest.raises(TimeoutError):
        run(transport, "restart", deadline=time.monotonic() - 1)
    transport.exec_command.assert_not_called()


def test_materialization_bounds_command_acknowledgement_wait():
    from nebius_vpngw.deploy.ssh_push import SSHPush

    closed = threading.Event()

    def execute(*args, **kwargs):
        # Bounded negative control: even broken code cannot strand the test.
        closed.wait(0.5)
        raise EOFError("transport closed")

    transport = SimpleNamespace(exec_command=execute, close=closed.set)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="bounded wait"):
        SSHPush._wait_for_vm_ha_materialization(transport, timeout_seconds=0.05)
    assert closed.is_set()
    assert time.monotonic() - started < 0.4
