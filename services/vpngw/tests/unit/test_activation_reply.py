"""Exercise actual SSH draining, reply decoding and durable activation recovery."""

import base64
import json
import time
from types import SimpleNamespace as NS

import pytest

from nebius_vpngw.deploy import ssh_push
from nebius_vpngw.deploy.activation_recovery import Recovery, execute


class Channel:
    def __init__(self, data, rc):
        self.data, self.rc = data, rc

    def recv_ready(self):
        return bool(self.data)

    def recv(self, _size):
        data, self.data = self.data, b""
        return data

    def recv_stderr_ready(self):
        return False

    def recv_stderr(self, _size):
        return b""

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return self.rc

    def close(self):
        pass


def reply_fixture(monkeypatch, tmp_path, output, rc, marker_kind, action="mark"):
    request = {
        "action": action,
        "lock": {"operation_id": "a" * 64, "node_id": "node"},
        "configuration": "b" * 64,
    }
    receipt = tmp_path / "committed-receipt.json"
    submissions, observations = [], []

    class Client:
        def exec_command(self, command, **_):
            accepted = json.loads(base64.b64decode(command.rsplit(" ", 1)[1]))
            assert accepted == request
            submissions.append(accepted)
            if marker_kind != "absent":
                value = json.loads(json.dumps(accepted))
                if marker_kind == "foreign":
                    value["lock"]["operation_id"] = "f" * 64
                receipt.write_text(json.dumps(value))
            if isinstance(output, Exception):
                raise output
            return None, NS(channel=Channel(output, rc)), None

        def close(self):
            pass

    ssh = ssh_push.SSHPush()
    monkeypatch.setattr(ssh, "_ensure_paramiko", lambda: NS(SSHClient=Client))
    monkeypatch.setattr(ssh, "_connect_client", lambda *_a, **_k: None)
    monkeypatch.setattr(ssh_push, "configure_paramiko_host_verification", lambda *_a, **_k: None)
    owner = Recovery(tmp_path / "recovery", execution="c" * 32, authority="d" * 64)
    owner.initialize()

    def effect(_):
        return ssh.activation_control(
            "target", NS(hostname="node"), {}, request, deadline=time.monotonic() + 2
        )

    def observe(_):
        observations.append("read")
        exact = receipt.exists() and json.loads(receipt.read_text()) == request
        return {"settled": True, "complete": exact}

    def run(current):
        execute(
            current,
            "activate-node",
            effect=effect,
            observe=observe,
            repair=lambda *_: pytest.fail("No repair authority from a lost reply"),
            finish=lambda *_: pytest.fail("No corrective attempt to finish"),
        )

    return owner, submissions, observations, run, effect


@pytest.mark.parametrize(
    "output,rc",
    [
        (b"", 0),
        (b'{"complete":', 0),
        (b"", -1),
        (b"\xff", 0),
        (b"[]", 0),
        (b"null", 0),
        (b'{"complete":true}', -1),
        (EOFError("stream closed"), 0),
    ],
)
@pytest.mark.parametrize("marker_kind", ["exact", "absent", "foreign"])
def test_completed_mark_with_lost_response(monkeypatch, tmp_path, output, rc, marker_kind):
    owner, submissions, observations, run, _ = reply_fixture(
        monkeypatch, tmp_path, output, rc, marker_kind
    )
    if marker_kind == "exact":
        run(owner)
        assert observations == ["read"]
        assert owner.initial("activate-node") == "verified"
        reopened = Recovery(
            tmp_path / "recovery", execution=owner.execution, authority=owner.authority
        )
        run(reopened)
        assert observations == ["read", "read"]
        assert reopened.initial("activate-node") == "verified"
    else:
        with pytest.raises(RuntimeError, match="no proven safe local repair"):
            run(owner)
        assert observations == ["read"]
        assert owner.initial("activate-node") == "started"
    assert len(submissions) == 1 and owner.history() == []


def test_valid_reply_control(monkeypatch, tmp_path):
    owner, submissions, observations, run, _ = reply_fixture(
        monkeypatch, tmp_path, b'{"complete":true}', 0, "exact"
    )
    run(owner)
    assert len(submissions) == 1 and not observations and owner.history() == []


@pytest.mark.parametrize("action", ["inspect", "inspect-unlock"])
@pytest.mark.parametrize(
    "output,rc",
    [(b"", 0), (b"{", 0), (b"\xff", 0), (b"[]", 0), (b"{}", -1), (EOFError("stream closed"), 0)],
)
def test_observation_reply_loss_remains_typed_pending(monkeypatch, tmp_path, action, output, rc):
    from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable

    owner, submissions, observations, run, _ = reply_fixture(
        monkeypatch, tmp_path, output, rc, "absent", action
    )
    with pytest.raises(ObservationUnavailable) as error:
        run(owner)
    assert error.value.retryable and error.value.first.phase == "activation-observation"
    assert owner.initial("activate-node") == "waiting-authority"
    assert owner.history() == [] and observations == []
    assert [item["action"] for item in submissions] == [action]


@pytest.mark.parametrize(
    "output,rc", [(b'{"complete":true}', 1), (b"", 1), (b'{"error":"refused","complete":true}', 0)]
)
def test_positive_guest_refusal_is_not_a_completion_reply(monkeypatch, tmp_path, output, rc):
    from nebius_vpngw.deploy.apply_diagnostics import DiagnosticFailure

    _, _, _, _, effect = reply_fixture(monkeypatch, tmp_path, output, rc, "exact")
    with pytest.raises(RuntimeError, match="authority or observation unavailable") as error:
        effect(None)
    assert not isinstance(error.value, DiagnosticFailure)


@pytest.mark.parametrize("retryable", [True, False])
def test_lost_mutation_reply_retains_first_and_unavailable_readback(
    monkeypatch, tmp_path, retryable
):
    from nebius_vpngw.deploy.apply_diagnostics import (
        ObservationUnavailable,
        VerificationFailure,
        unavailable_observation,
    )

    owner, submissions, _, _, effect = reply_fixture(monkeypatch, tmp_path, b"", 0, "exact")

    def unavailable(_):
        raise ObservationUnavailable(
            "activation-observation", "readback-unavailable", retryable=retryable
        )

    with pytest.raises(VerificationFailure) as error:
        execute(
            owner,
            "activate-node",
            effect=effect,
            observe=unavailable,
            repair=lambda *_: pytest.fail("Unavailable readback cannot select repair"),
            finish=lambda *_: pytest.fail("No repair was admitted"),
        )
    assert error.value.first.phase == "activation-command"
    assert error.value.first.predicate == "reply-unavailable"
    assert error.value.first.reasons == ("guest-response-missing",)
    assert error.value.latest.reasons == ("readback-unavailable",)
    assert unavailable_observation(error.value).retryable is retryable
    assert len(submissions) == 1 and owner.history() == []
    assert owner.initial("activate-node") == "started"
