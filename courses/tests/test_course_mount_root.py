"""Confinement and fail-closed contracts for privileged jail setup."""

from types import SimpleNamespace

import pytest
from course_bootstrap import mount_root


class Namespace:
    def __init__(self, jailed=True, failure=None, drift=False):
        self.jailed, self.failure, self.drift = jailed, failure, drift
        self.events = []
        self.root = "jail"
        self.cwd = "cwd"
        self.namespace = 1
        self.pivoted = False
        self.handles = {}

    def event(self, name):
        self.events.append(name)
        if self.failure == name:
            raise OSError("injected syscall failure")

    def identity(self, path):
        if path == "/proc/self/ns/mnt":
            return self.namespace
        if path == "/":
            return self.root
        if path == ".":
            return self.cwd
        if self.pivoted and self.drift and path == "/dev":
            return "wrong-device-tree"
        return path

    def unshare(self, _):
        self.event("unshare")
        self.namespace = 2

    def open(self, path, _):
        self.event("open:" + path)
        assert self.namespace == 2, "pinned descriptor came from parent mounts"
        handle = len(self.handles) + 10
        self.handles[handle] = path
        return handle

    def setns(self, fd, _):
        self.event("setns")
        assert self.handles[fd] == "/proc/self/ns/mnt"
        self.root = "host" if self.jailed and not self.pivoted else "jail"
        self.cwd = self.root

    def fchdir(self, fd):
        self.event("fchdir:" + self.handles[fd])
        self.cwd = "jail" if self.handles[fd] == "/" else "cwd"

    def private(self):
        self.event("private")
        assert self.namespace == 2

    def pivot(self):
        self.event("pivot")
        assert self.cwd == "jail"
        assert "private" in self.events
        self.pivoted = True
        self.root = "jail"

    def chdir(self, path):
        self.event("chdir")
        assert path == "/"
        self.cwd = "jail"

    def close(self, fd):
        self.event("close")
        del self.handles[fd]

    def abort(self):
        self.event("abort")
        raise SystemExit(1)

    def install(self, monkeypatch):
        monkeypatch.setattr(mount_root, "identity", self.identity)
        monkeypatch.setattr(mount_root, "abort_transition", self.abort)
        monkeypatch.setattr(
            mount_root,
            "os",
            SimpleNamespace(
                CLONE_NEWNS=1,
                O_RDONLY=0,
                O_CLOEXEC=1,
                **{
                    name: getattr(self, name)
                    for name in ("unshare", "open", "setns", "fchdir", "chdir", "close")
                },
            ),
        )
        monkeypatch.setattr(
            mount_root.signal,
            "pthread_sigmask",
            lambda *args: self.event("signal-mask") or set(),
            raising=False,
        )


@pytest.mark.parametrize("jailed", [False, True])
def test_normalization_preserves_jail_and_cwd_and_closes_handles(monkeypatch, jailed):
    fixture = Namespace(jailed=jailed)
    fixture.install(monkeypatch)
    assert mount_root.normalize(fixture) is jailed
    assert fixture.root == "jail" and fixture.cwd == "cwd"
    assert not fixture.handles
    assert ("pivot" in fixture.events) is jailed
    assert fixture.events.count("setns") == 2
    assert fixture.events[-1] == "signal-mask"


@pytest.mark.parametrize(
    "failure",
    [
        "unshare",
        "open:/",
        "open:.",
        "open:/proc/self/ns/mnt",
        "setns",
        "private",
        "fchdir:/",
        "pivot",
        "chdir",
        "fchdir:.",
        "close",
    ],
)
def test_any_incomplete_transition_terminates_before_continuation(monkeypatch, failure):
    fixture = Namespace(failure=failure)
    fixture.install(monkeypatch)
    with pytest.raises(SystemExit, match="1"):
        mount_root.normalize(fixture)
    assert fixture.events[-1] == "abort"
    assert fixture.events.count("signal-mask") == 1


def test_identity_drift_terminates_before_installation(monkeypatch):
    fixture = Namespace(drift=True)
    fixture.install(monkeypatch)
    with pytest.raises(SystemExit, match="1"):
        mount_root.normalize(fixture)
    assert fixture.events[-1] == "abort"


def test_missing_capabilities_reject_known_jail_without_mutation(monkeypatch):
    monkeypatch.setattr(mount_root.sys, "platform", "linux")
    monkeypatch.setattr(mount_root.Path, "read_text", lambda _: "CapEff:\t00000000\n")
    monkeypatch.setattr(mount_root.os, "geteuid", lambda: 0)
    monkeypatch.setattr(mount_root, "identity", lambda path: path)
    monkeypatch.setattr(
        mount_root, "normalize", lambda _: pytest.fail("privileges changed")
    )
    with pytest.raises(RuntimeError, match="CAP_SYS_ADMIN"):
        mount_root.prepare()


def test_transition_failure_exits_even_when_stderr_is_closed(monkeypatch):
    def closed(*_args):
        raise BrokenPipeError("closed stderr")

    def terminate(code):
        raise SystemExit(code)

    monkeypatch.setattr(mount_root.os, "write", closed)
    monkeypatch.setattr(mount_root.os, "_exit", terminate)
    with pytest.raises(SystemExit, match="1"):
        mount_root.abort_transition()
