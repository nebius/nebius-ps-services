import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nebius_vpngw import dependency_scope
from nebius_vpngw.deploy import ordinary_apply
from nebius_vpngw.deploy.recovery import GUEST_FAILURES, GuestFailure, Invocation, advice


@pytest.mark.parametrize("code", sorted(GUEST_FAILURES))
def test_guest_diagnostic_survives_public_boundary(monkeypatch, code):
    response = dict(status="failed", stage="inspect", error_code=code)
    channel = Mock()
    channel.send.side_effect = len
    channel.recv_ready.side_effect = [True, False, False]
    channel.recv.return_value = json.dumps(response).encode()
    channel.recv_stderr_ready.return_value = False
    channel.exit_status_ready.return_value = True
    channel.recv_exit_status.return_value = 1
    client = Mock()
    client.exec_command.return_value = (NS(channel=channel), NS(channel=channel), Mock())
    ssh = NS(
        _ssh_policy=None,
        _ensure_paramiko=lambda: NS(SSHClient=lambda: client),
        _connect_client=Mock(),
    )
    monkeypatch.setattr(
        ordinary_apply, "configure_paramiko_host_verification", lambda *_a, **_k: None
    )
    monkeypatch.setattr(ordinary_apply.ingress_ssh, "preflight", lambda *_a: None)
    with pytest.raises(RuntimeError) as failure:
        ordinary_apply.remote(
            ssh,
            "fixture",
            NS(config_yaml="", hostname="gateway"),
            {},
            {},
            bootstrap=NS(source_frame=lambda: b"", COMMAND="fixture"),
        )
    recovery = advice(Invocation("apply", repair=True), failure.value)
    assert recovery.reason_code == "guest-" + code.replace("_", "-")
    assert recovery.repair_prerequisite and recovery.next_action
    client.close.assert_called_once()


def test_missing_resolver_has_usable_prerequisite(monkeypatch):
    monkeypatch.setattr(ordinary_apply.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError) as failure:
        ordinary_apply.require_dependency_resolver()
    recovery = advice(Invocation("apply"), failure.value)
    assert recovery.reason_code == "dependency-resolver-unavailable"
    assert "uv" in recovery.next_action


def test_conflicting_managed_metadata_has_specific_reason():
    observed = dict(
        candidates={
            "managed": [dict(prefix="same", version="1"), dict(prefix="same", version="2")]
        },
        dependencies={},
        requirements={},
    )
    with pytest.raises(RuntimeError) as failure:
        dependency_scope.project(observed, {"managed"})
    recovery = advice(Invocation("apply"), failure.value)
    assert recovery.reason_code == "dependency-metadata-conflict"


@pytest.mark.parametrize("kind", ["dependency", "guest"])
def test_member_preparation_wrapper_preserves_specific_reason_and_action(kind):
    from nebius_vpngw import cli

    cause = (
        dependency_scope.DependencyFailure("dependency-resolver-unavailable", "private detail")
        if kind == "dependency"
        else GuestFailure("repair_repository_unavailable", "inspect", "private detail")
    )
    wrapped = cli._VMHAAgentPackagePreparationFailed(2, cli._vm_ha_package_failure_reason(cause))
    wrapped.__cause__ = cause
    expected = advice(Invocation("apply"), cause)
    actual = advice(Invocation("apply"), wrapped)
    assert actual.reason_code == "package-preparation-failed-member-2"
    assert actual.cause_code == expected.reason_code
    assert expected.reason in actual.reason
    assert actual.next_action == expected.next_action
    assert actual.repair_prerequisite == expected.repair_prerequisite
    assert "private detail" not in actual.text()
    assert "unexpected package-preparation error" not in actual.text()


def test_unknown_member_preparation_cause_is_explicit_and_redacted():
    from nebius_vpngw import cli

    cause = LookupError("private unknown detail")
    wrapped = cli._VMHAAgentPackagePreparationFailed(1, cli._vm_ha_package_failure_reason(cause))
    wrapped.__cause__ = cause
    recovery = advice(Invocation("apply"), wrapped)
    assert recovery.cause_code == "unknown-failure"
    assert "cause is unknown" in recovery.reason
    assert "private unknown detail" not in recovery.text()


@pytest.mark.parametrize("code", sorted(dependency_scope.DependencyFailure.CODES))
def test_dependency_causes_are_complete_redacted_and_preserved_by_pair(code):
    from nebius_vpngw.deploy.recovery import RegionalFailure

    error = dependency_scope.DependencyFailure(code, "private dependency diagnostic")
    child = advice(Invocation("apply"), error)
    parent = advice(Invocation("pair apply"), RegionalFailure("a", {"recovery": child.mapping()}))
    assert child.reason_code == parent.reason_code == code
    assert child.next_action and child.repair_prerequisite
    assert "private dependency" not in child.text() + parent.text()


@pytest.mark.parametrize("backend", ["ordinary", "repair"])
@pytest.mark.parametrize("typed", [False, True])
def test_native_dependency_failure_serialization_is_typed_and_redacted(
    monkeypatch, capsys, backend, typed
):
    import io

    from nebius_vpngw.deploy import ordinary_remote, repair_remote

    code = "dependency-metadata-conflict"
    error = (
        dependency_scope.DependencyFailure(code, "private metadata detail")
        if typed
        else RuntimeError(code)
    )
    remote = ordinary_remote if backend == "ordinary" else repair_remote
    monkeypatch.setattr(
        remote.sys, "stdin", NS(buffer=io.BytesIO(b'{"action":"inspect","manifest":{}}'))
    )
    failed = Mock(side_effect=error)
    if backend == "ordinary":
        monkeypatch.setattr(remote, "inspect_for_plan", failed)
        monkeypatch.setattr(remote.signal, "signal", lambda *_: None)
        monkeypatch.setattr(remote.signal, "alarm", lambda *_: None)
        call = remote.main
    else:
        monkeypatch.setattr(remote, "control", failed)
        call = remote.control_main
    with pytest.raises(SystemExit) as stopped:
        call()
    assert stopped.value.code == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["error_code"] == (
        code if typed else "ordinary_transaction_failed" if backend == "ordinary" else "receipt"
    )
    assert "private metadata" not in output


@pytest.mark.parametrize("command", ["apply"])
@pytest.mark.parametrize("kind", ["dependency", "guest"])
def test_public_json_preserves_classified_failure_and_repair_context(
    monkeypatch, tmp_path, command, kind
):
    from typer.testing import CliRunner

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import pair_phases

    path = tmp_path / "gateway's config.yaml"
    path.touch()
    error = (
        dependency_scope.DependencyFailure("dependency-resolver-unavailable", "private detail")
        if kind == "dependency"
        else GuestFailure("repair_repository_unavailable", "inspect", "private detail")
    )

    def fail(*_args, **_kwargs):
        raise error

    reports = []
    if command == "apply":
        monkeypatch.setattr(cli, "load_local_config", lambda _: {})
        monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: NS())
        monkeypatch.setattr(cli, "_apply_impl", fail)
        monkeypatch.setattr(pair_phases, "report", reports.append)
    else:
        monkeypatch.setattr(
            cli, "_resolve_vm_ha_effective_config", lambda **_: cli._VMHAEffectiveConfig(path, ())
        )
        monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", fail)
    result = CliRunner().invoke(
        cli.app, [command, "-c", str(path), "--region", "region-test", "--output-format", "json"]
    )
    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    recovery = payload["recovery"]
    assert recovery["reason_code"] == advice(Invocation(command), error).reason_code
    assert "--region region-test" in recovery["repair_command"]
    assert "--repair" in recovery["repair_command"]
    assert "private detail" not in result.output
    if command == "apply":
        assert json.loads(json.dumps(reports)) == [payload]
