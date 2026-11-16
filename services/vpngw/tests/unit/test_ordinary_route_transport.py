import builtins
import io
import json
import shlex
import sys
import types
from contextlib import contextmanager

import pytest

import nebius_vpngw
from nebius_vpngw import ordinary_operations
from nebius_vpngw.agent import ordinary_route_authority as installed
from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import ordinary_runtime_handoff as transport
from nebius_vpngw.deploy.ordinary_runtime_handoff import owner_entry


@pytest.mark.parametrize(
    "corrupt,failure,reason",
    [
        (None, None, None),
        ("OWNER_DIGEST", None, "route-owner-integrity-failed"),
        ("OPERATIONS_DIGEST", None, "route-owner-integrity-failed"),
        (None, "lock", "route-lock-deadline"),
        (None, "pending", "route-operation-pending"),
        (None, "authority", "route-authority-conflict"),
        (None, "import", "route-owner-import-failed"),
        (None, "unknown", "route-handoff-unknown"),
    ],
)
def test_selected_owner_protocol_runs_before_guest_package_upgrade(
    tmp_path, monkeypatch, capsys, corrupt, failure, reason
):
    """An old installed protocol cannot implement the new transfer action."""
    owner = installed.RouteAuthority(tmp_path / "routes")
    owner.reserve("a" * 64, digest(owner.snapshot()))
    request = {
        "action": "supersede-reservation",
        "previous": "a" * 64,
        "holder": "b" * 64,
        "expected": digest(owner.snapshot()),
    }
    before = owner.path.read_bytes()

    def old_protocol(_):
        raise ValueError("The installed predecessor has no transfer action")

    monkeypatch.setattr(installed, "action", old_protocol)
    with pytest.raises(ValueError, match="predecessor"):
        installed.action(request)
    predecessor = types.ModuleType("nebius_vpngw.ordinary_operations")
    predecessor.__dict__.update(vars(ordinary_operations))

    def old_lock():
        pytest.fail("The predecessor lock must not be used before package upgrade")

    predecessor.mutation_lock = old_lock
    monkeypatch.setitem(sys.modules, predecessor.__name__, predecessor)
    monkeypatch.setattr(nebius_vpngw, "ordinary_operations", predecessor)
    source = owner_entry()
    if corrupt:
        source = source.replace(corrupt + "=", corrupt + "='" + "0" * 64 + "' #", 1)

    def load_guest_owner(code, namespace):
        builtins.exec(code, namespace)
        # Only the native filesystem location is supplied by this fixture;
        # the exact source and digest are the real selected transport payload.
        if namespace["__name__"] == predecessor.__name__:
            namespace["JOURNAL"] = tmp_path / "operation.json"
            namespace["ROUTING_LOCK"] = tmp_path / "routing.lock"
        else:
            assert namespace["RouteJournal"] is installed.RouteJournal
            namespace["RouteAuthority"].__init__.__defaults__ = (owner.root,)
            if failure:

                def failed_action(_request):
                    selected_ops = sys.modules[predecessor.__name__]
                    kind = {
                        "lock": selected_ops.RoutingLockDeadline,
                        "pending": selected_ops.OperationBlocked,
                        "authority": namespace["RouteAuthorityConflict"],
                        "import": ImportError,
                        "unknown": RuntimeError,
                    }[failure]
                    raise kind("private-token-and-provider-body")

                namespace["action"] = failed_action

    namespace = {"__builtins__": {**vars(builtins), "exec": load_guest_owner}}
    module_name = "nebius_vpngw.agent._route_owner_command"
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    try:
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
        if reason:
            with pytest.raises(SystemExit) as stopped:
                exec(compile(source, "<selected-route-entry>", "exec"), namespace)
            assert stopped.value.code == 1
            response = capsys.readouterr()
            assert json.loads(response.out)["error"]["reason"] == reason
            assert "private-token" not in response.out and not response.err
            assert owner.path.read_bytes() == before
        else:
            exec(compile(source, "<selected-route-entry>", "exec"), namespace)
            response = json.loads(capsys.readouterr().out)
            assert response["authority"]["holder"] == "b" * 64
            assert response == owner.snapshot()
            after = owner.path.read_bytes()
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
            exec(compile(source, "<selected-route-entry>", "exec"), namespace)
            assert json.loads(capsys.readouterr().out) == response
            assert owner.path.read_bytes() == after
        assert installed.action is old_protocol
    finally:
        sys.modules.pop(module_name, None)


def test_transport_streams_source_before_request_in_bounded_command(monkeypatch, capsys):
    sent = io.StringIO()
    sent.channel = types.SimpleNamespace(shutdown_write=lambda: None)
    response = io.BytesIO(b'{"authority":null,"journal":null}')
    response.channel = types.SimpleNamespace(recv_exit_status=lambda: 0)
    commands = []

    class Client:
        def exec_command(self, command, timeout):
            assert timeout == 15
            commands.append(command)
            return sent, response, io.BytesIO()

    @contextmanager
    def connection(*args):
        yield Client()

    monkeypatch.setattr(transport, "_connection", connection)
    assert transport.Handoff(None, "gateway", None, {}).inspect() == {
        "authority": None,
        "journal": None,
    }
    assert len(commands) == 1 and len(commands[0]) < 1024
    argv = shlex.split(commands[0])
    assert argv[:-1] == ["sudo", "/usr/bin/python3", "-B", "-c"]
    assert len(sent.getvalue().splitlines()) == 2
    # An inspect on an unstarted guest takes the real absent-state branch.
    # No product or lock file is written by executing this transported payload.
    monkeypatch.setattr(sys, "stdin", io.StringIO(sent.getvalue()))
    exec(compile(argv[-1], "<transport-command>", "exec"), {})
    assert json.loads(capsys.readouterr().out) == {"authority": None, "journal": None}


@pytest.mark.parametrize(
    "reason",
    [
        "route-lock-deadline",
        "route-operation-pending",
        "route-authority-conflict",
        "route-owner-import-failed",
        "route-owner-integrity-failed",
        "route-handoff-unknown",
        "private-token-and-provider-body",
    ],
)
def test_transport_propagates_only_closed_diagnostics_with_repair_guidance(monkeypatch, reason):
    from nebius_vpngw.deploy.apply_diagnostics import RouteHandoffFailure
    from nebius_vpngw.deploy.recovery import Invocation, advice

    sent = io.StringIO()
    sent.channel = types.SimpleNamespace(shutdown_write=lambda: None)
    response = io.BytesIO(
        json.dumps(
            {
                "error": {
                    "schema": "nebius-vpngw.route-handoff-error.v1",
                    "reason": reason,
                }
            }
        ).encode()
    )
    response.channel = types.SimpleNamespace(recv_exit_status=lambda: 1)

    class PrivateStderr:
        def read(self, *_args):
            pytest.fail("untrusted stderr must not be consumed as a diagnostic")

    @contextmanager
    def connection(*args):
        yield types.SimpleNamespace(
            exec_command=lambda *args, **kwargs: (sent, response, PrivateStderr())
        )

    monkeypatch.setattr(transport, "_connection", connection)
    with pytest.raises(RouteHandoffFailure) as error:
        transport.Handoff(None, "gateway", None, {}).inspect()
    recovery = advice(Invocation("apply"), error.value)
    expected = reason if reason in RouteHandoffFailure.CODES else "route-handoff-unknown"
    assert recovery.reason_code == expected
    assert "--repair" in recovery.repair_command
    assert recovery.next_action and recovery.repair_prerequisite
    assert "private-token" not in recovery.text()


def test_oversized_reply_is_rejected_before_waiting_for_remote_exit(monkeypatch):
    from nebius_vpngw.deploy.apply_diagnostics import RouteHandoffFailure

    sent = io.StringIO()
    sent.channel = types.SimpleNamespace(shutdown_write=lambda: None)
    response = io.BytesIO(b"x" * (1024 * 1024 + 1))
    response.channel = types.SimpleNamespace(
        recv_exit_status=lambda: pytest.fail("unread remote output can block exit forever")
    )

    @contextmanager
    def connection(*args):
        yield types.SimpleNamespace(
            exec_command=lambda *args, **kwargs: (sent, response, io.BytesIO())
        )

    monkeypatch.setattr(transport, "_connection", connection)
    with pytest.raises(RouteHandoffFailure, match="route-handoff-unknown"):
        transport.Handoff(None, "gateway", None, {}).inspect()
