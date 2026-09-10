from __future__ import annotations

import io
import json
from unittest.mock import Mock

import pytest

from nebius_vpngw.deploy.ordinary_handoff import Handoff


def test_handoff_release_requires_exact_locked_passive_status():
    expected = {"cluster_id": "cluster", "node_id": "node", "generation_id": "generation"}
    status = dict(
        expected, data_plane_mode="passive", apply_locked=True, apply_operation_id="1" * 64
    )
    for field, wrong in [
        ("node_id", "foreign"),
        ("generation_id", "stale"),
        ("data_plane_mode", "active"),
        ("apply_locked", False),
        ("apply_operation_id", "2" * 64),
    ]:
        stdin = Mock()
        handoff = Handoff(stdin, io.StringIO('{"complete": true}\n'), ha=expected)
        with pytest.raises(RuntimeError, match="locked-passive"):
            handoff.complete(dict(status, **{field: wrong}), operation_id="1" * 64)
        stdin.write.assert_not_called()
        assert not handoff.finished
    stdin = io.StringIO()
    handoff = Handoff(stdin, io.StringIO('{"complete": true}\n'), ha=expected)
    handoff.complete(status, operation_id="1" * 64)
    assert json.loads(stdin.getvalue()) == {"action": "complete", "operation": "1" * 64}
    assert handoff.finished


def test_lost_handoff_retirement_reply_is_not_success():
    expected = {"node_id": "node", "cluster_id": "cluster", "generation_id": "generation"}
    handoff = Handoff(io.StringIO(), io.StringIO(""), ha=expected)
    with pytest.raises(ValueError):
        handoff.complete(
            dict(
                expected, data_plane_mode="passive", apply_locked=True, apply_operation_id="1" * 64
            ),
            operation_id="1" * 64,
        )
    assert not handoff.finished


def test_streamed_bootstrap_uses_small_command_and_preserves_request_framing(monkeypatch):
    import shlex
    import subprocess
    import sys

    from nebius_vpngw.deploy import ordinary_handoff

    stdin = io.StringIO()
    client = Mock()
    client.exec_command.return_value = (stdin, io.StringIO(), io.StringIO())
    monkeypatch.setattr(
        ordinary_handoff,
        "_REMOTE",
        "import json; print(json.dumps(json.loads(sys.stdin.readline())))",
    )
    request = {"action": "framing-proof", "value": "exact request"}
    ordinary_handoff._start(client, request)
    command = shlex.split(client.exec_command.call_args.args[0])
    assert len(client.exec_command.call_args.args[0]) < 1024
    assert len(stdin.getvalue()) > 128 * 1024
    result = subprocess.run(
        [sys.executable, *command[3:]],
        input=stdin.getvalue(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == request
