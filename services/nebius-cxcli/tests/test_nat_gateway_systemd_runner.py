from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


@pytest.mark.parametrize("cleanup_error", [None, "timeout", "oserror"])
def test_ambiguous_launch_always_attempts_all_scoped_cleanup(monkeypatch, capsys, cleanup_error):
    path = (
        Path(__file__).resolve().parents[3]
        / "platform-infra/modules/nat-gateway/tests/systemd/run.py"
    )
    spec = importlib.util.spec_from_file_location("nat_systemd_runner", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    calls = []

    def launch(*args, **kwargs):
        if args[:2] == ("docker", "run"):
            raise subprocess.TimeoutExpired(args, 30)
        return NS(returncode=0)

    def cleanup(args, **kwargs):
        calls.append(args)
        if args[:2] == ["docker", "stop"]:
            if cleanup_error == "timeout":
                raise subprocess.TimeoutExpired(args, 30)
            if cleanup_error == "oserror":
                raise OSError("fixture injected failure")
        return NS(returncode=0)

    monkeypatch.setattr(runner, "run", launch)
    monkeypatch.setattr(runner.subprocess, "run", cleanup)
    with pytest.raises(subprocess.TimeoutExpired) as original:
        runner.main()
    assert original.value.cmd[:2] == ("docker", "run")
    assert [args[:2] for args in calls] == [
        ["docker", "stop"],
        ["docker", "rm"],
        ["docker", "image"],
    ]
    assert calls[1][2] == "--force"
    name = calls[0][-1]
    assert name.startswith("cxcli-nat-security-")
    assert calls[1][-1] == name and calls[2][-1] == name + ":fixture"
    assert ("cleanup commands failed" in capsys.readouterr().err) is bool(cleanup_error)
