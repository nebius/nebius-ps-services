"""Package child permissions must not depend on the observing client's mask."""

import os
import stat
import sys
import time
from types import SimpleNamespace

import pytest

from nebius_vpngw import package_worker
from nebius_vpngw.deploy import ordinary_remote as remote


@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize("package_install", [False, True])
def test_package_child_permissions_are_stable_across_observers(
    monkeypatch, tmp_path, durable, package_install
):
    target = tmp_path / "created"
    script = (
        "from pathlib import Path; Path(" + repr(str(target)) + ").write_text('public package')"
    )
    # Run a real child through both command owners. The trailing arguments select
    # the pip-install policy without installing anything in the test interpreter.
    command = [sys.executable, "-c", script]
    if package_install:
        command.extend(["pip", "install"])
    effects = {}

    def started(index, pid):
        effects[index] = {"process": {"pid": pid}}

    operation = SimpleNamespace(
        check=lambda: {"effects": effects},
        intent=lambda _kind: 0,
        process_started=started,
        update=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(remote, "OPERATION", operation if durable else None)
    monkeypatch.setattr(remote, "DEADLINE", time.monotonic() + 30)
    monkeypatch.setattr(remote.operations, "process_group_empty", lambda _process: True)
    monkeypatch.setattr(package_worker, "children_running", lambda: False)
    before = os.umask(0o077)
    try:
        invoke = remote.durable_command if durable else remote.command
        assert invoke(command)[0] == 0
        assert stat.S_IMODE(target.stat().st_mode) == (0o644 if package_install else 0o600)
        inherited = os.umask(0o077)
        assert inherited == 0o077, "Child policy must not alter the caller's mask"
    finally:
        os.umask(before)
