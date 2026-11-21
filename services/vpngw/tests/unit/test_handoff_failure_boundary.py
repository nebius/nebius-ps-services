"""The actual streamed entrypoint must retain safe failure evidence."""

from __future__ import annotations

import io
import json
import shlex
import subprocess
import sys
from unittest.mock import Mock

import pytest

from nebius_vpngw.deploy import ordinary_handoff
from nebius_vpngw.deploy.apply_diagnostics import GuestResponseUnavailable
from nebius_vpngw.deploy.recovery import GuestFailure, Invocation, advice


@pytest.mark.parametrize("where", ["bundle", "request"])
@pytest.mark.parametrize(
    "exception,code",
    [
        ("ModuleNotFoundError", "handoff_import_failed"),
        ("RuntimeError", "handoff_execution_failed"),
    ],
)
def test_streamed_failure_is_classified_without_exception_text(monkeypatch, where, exception, code):
    secret = "private-detail-must-not-escape"
    failure = f"raise {exception}({secret!r})\n"
    monkeypatch.setattr(
        ordinary_handoff, "streamed_source", lambda: failure if where == "bundle" else ""
    )
    monkeypatch.setattr(ordinary_handoff, "_REMOTE", failure if where == "request" else "")
    stdin = io.StringIO()
    client = Mock()
    client.exec_command.return_value = (stdin, io.StringIO(), io.StringIO())
    ordinary_handoff._start(client, {"action": "inspect"})
    command = shlex.split(client.exec_command.call_args.args[0])
    result = subprocess.run(
        [sys.executable, *command[command.index("/usr/bin/python3") + 1 :]],
        input=stdin.getvalue(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert secret not in result.stdout + result.stderr
    assert json.loads(result.stdout)["error_code"] == code
    with pytest.raises(GuestFailure) as caught:
        ordinary_handoff._receive(io.StringIO(result.stdout))
    recovery = advice(Invocation("apply"), caught.value)
    assert recovery.reason_code == "guest-" + code.replace("_", "-")
    assert secret not in recovery.text()
    assert "cause-unclassified" not in recovery.text()


@pytest.mark.parametrize(
    "ready,status", [(False, None), (True, -1), (True, 0), (True, 1), (True, 127)]
)
def test_empty_response_is_unknown_outcome_with_nonblocking_exit_evidence(ready, status):
    stdout = io.StringIO("")
    stdout.channel = Mock()
    stdout.channel.exit_status_ready.return_value = ready
    stdout.channel.recv_exit_status.return_value = status
    with pytest.raises(GuestResponseUnavailable) as caught:
        ordinary_handoff._receive(stdout)
    expected = (f"guest-exit-code-{status}",) if ready and status >= 0 else ()
    assert caught.value.first.reasons == expected
    if not ready:
        stdout.channel.recv_exit_status.assert_not_called()
    recovery = advice(Invocation("apply"), caught.value)
    assert recovery.reason_code == "guest-response-unavailable"
    assert "unknown" in recovery.reason
    assert "interrupted" not in recovery.text()


def test_successful_response_does_not_wait_for_process_exit():
    stdout = io.StringIO('{"observed":true}\n')
    stdout.channel = Mock()
    assert ordinary_handoff._receive(stdout) == {"observed": True}
    stdout.channel.exit_status_ready.assert_not_called()
