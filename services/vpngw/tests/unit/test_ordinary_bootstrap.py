from __future__ import annotations

import base64
import json
import signal
import subprocess
import sys
from unittest.mock import Mock

import pytest

from nebius_vpngw import handoff_bootstrap, ordinary_bootstrap, repair_bootstrap
from nebius_vpngw.deploy import ordinary_remote


def test_complete_source_is_framed_without_consuming_argument_space(monkeypatch):
    frame = ordinary_bootstrap.source_frame()
    source = base64.b64decode(frame, validate=False)
    assert b"<ordinary-transaction>" in source
    assert len(ordinary_bootstrap.COMMAND.encode()) < 1024
    assert frame.endswith(b"\n") and b"\n" not in frame[:-1]
    monkeypatch.setattr(ordinary_bootstrap, "SOURCE_LIMIT", len(frame) - 1)
    with pytest.raises(RuntimeError, match="source exceeded"):
        ordinary_bootstrap.source_frame()


def test_source_upload_does_not_restart_the_remote_deadline(monkeypatch, capsys):
    monkeypatch.setattr(ordinary_remote, "DEADLINE", 0.0)
    monkeypatch.setattr(signal, "signal", Mock())
    alarm = Mock()
    monkeypatch.setattr(signal, "alarm", alarm)
    monkeypatch.setattr(ordinary_remote.time, "monotonic", lambda: 601.0)
    with pytest.raises(SystemExit) as result:
        ordinary_remote.main(deadline=600.0)
    assert result.value.code == 1
    assert ordinary_remote.DEADLINE == 600.0
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    alarm.assert_not_called()


@pytest.mark.parametrize("bundle", ["ordinary", "handoff", "repair"])
def test_repair_guard_verifier_is_available_without_installed_package(bundle):
    if bundle == "ordinary":
        source = base64.b64decode(ordinary_bootstrap.source_frame()).decode()
        # Stop before the transaction entry point reads or touches the guest.
        helpers = source.rsplit("exec(compile(base64.b64decode", 1)[0]
    else:
        helpers = (handoff_bootstrap.streamed_source() if bundle == "handoff"
                   else repair_bootstrap.source())
    result = subprocess.run(
        [sys.executable, "-I", "-S"],
        input=helpers
        + "import _vpngw_repair_guest as guard\n"
        + "assert guard.storage.SCHEMA == 'nebius-vpngw.repair.v1'\n"
        + "assert guard.service_guard().startswith(b'[Unit]\\nRequires=')\n"
        + "assert callable(guard.verify_guard)\n"
        + "assert callable(guard.underlay_state.decode)\n"
        + "import _vpngw_dependency_scope as scope\n"
        + "assert callable(scope.closure) and callable(scope.check_shared_conflicts)\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
