"""Concurrent package transports never fork to discover their SSH identity."""

import hashlib
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import paramiko
import pytest

from nebius_vpngw import cli
from nebius_vpngw.deploy import checkpoint_conversion, ssh_client_auth
from nebius_vpngw.deploy.ssh_policy import SSHTrustPolicy
from nebius_vpngw.deploy.ssh_push import SSHPush


def key_spec(tmp_path):
    path = tmp_path / "operator"
    key = paramiko.RSAKey.generate(1024)
    key.write_private_key_file(str(path))
    path.chmod(0o600)
    return {
        "ssh_public_key": f"{key.get_name()} {key.get_base64()}",
        "ssh_private_key_path": str(path),
    }


def package_inputs(tmp_path, evidence=lambda: None):
    pins = tmp_path / "known_hosts"
    pins.write_text("# immutable test pins\n")
    policy = SSHTrustPolicy(
        pins, hashlib.sha256(pins.read_bytes()).hexdigest(), _evidence_checks=(evidence,)
    )
    staged = [
        (SimpleNamespace(hostname=name, vm_ha_node=SimpleNamespace(node_id=name)), name, object())
        for name in ("owner", "standby")
    ]
    artifact = SimpleNamespace(
        sha256="a" * 64,
        dependency_plans=tuple(
            (
                instance.hostname,
                SimpleNamespace(
                    instance=instance, target=target, manifest={"wheel_sha256": "a" * 64}
                ),
            )
            for instance, target, _ in staged
        ),
    )
    return dict(
        staged=staged,
        ssh_policy=policy,
        artifact=artifact,
        operation_id="original-operation",
        cluster_id="cluster",
    )


@pytest.mark.parametrize("invocations", [1, 2])
def test_coordinator_prepares_real_keys_before_workers_and_resume(
    tmp_path, monkeypatch, invocations
):
    spec = key_spec(tmp_path)
    owner = threading.get_ident()
    events = []
    subprocess_run = ssh_client_auth.subprocess.run

    def key_validation(*args, **kwargs):
        assert threading.get_ident() == owner, "worker fork overlaps command-owned gRPC"
        events.append("key-validation")
        return subprocess_run(*args, **kwargs)

    def evidence():
        assert threading.get_ident() == owner
        events.append("cloud-evidence")

    options = package_inputs(tmp_path, evidence)
    arrived = threading.Barrier(2)
    clients = []

    def package(worker, target, *_args, **_kwargs):
        events.append("worker-start")
        arrived.wait(timeout=2)
        worker._ssh_policy.assert_current()
        client = Mock()
        worker._connect_client(client, hostname=target, username="operator", vm_spec=spec)
        clients.append(client)
        assert client.connect.call_args.kwargs["key_filename"] == spec["ssh_private_key_path"]
        assert client.connect.call_args.kwargs["allow_agent"] is False
        assert client.connect.call_args.kwargs["look_for_keys"] is False
        worker._ssh_policy.assert_current()

    monkeypatch.setattr(ssh_client_auth.subprocess, "run", key_validation)
    monkeypatch.setattr(checkpoint_conversion, "context", lambda *args, **kwargs: {})
    monkeypatch.setattr(cli, "_prepare_vm_ha_agent_package", package)
    for _ in range(invocations):
        events.clear()
        cli._prepare_stopped_vm_ha_packages(**options, local={"gateway_group": {"vm_spec": spec}})
        assert events[:2] == ["key-validation", "key-validation"]
        assert events.count("key-validation") == 2
        assert events.count("worker-start") == 2
        assert events.count("cloud-evidence") == 4
    assert len({id(client) for client in clients}) == 2 * invocations


@pytest.mark.parametrize("changed", ["public", "private", "operator"])
def test_prepared_identity_change_never_resolves_again(tmp_path, monkeypatch, changed):
    spec = key_spec(tmp_path)
    original = ssh_client_auth.resolve_ssh_client_auth(
        spec["ssh_public_key"], explicit_private_key=tmp_path / "operator"
    )
    token = ssh_client_auth.OPERATOR_IDENTITY.set(original)
    try:
        transport = SSHPush()
        transport.prepare_client_authentication(spec)
        if changed == "public":
            spec = dict(spec, ssh_public_key=spec["ssh_public_key"] + " changed")
        elif changed == "private":
            spec = dict(spec, ssh_private_key_path=str(tmp_path / "different"))
        else:
            different = replace(original, _key_path=tmp_path / "different")
            assert different == original  # Dataclass equality is insufficient authority.
            ssh_client_auth.OPERATOR_IDENTITY.set(different)
        resolver = Mock(side_effect=AssertionError("prepared worker tried identity discovery"))
        monkeypatch.setattr("nebius_vpngw.deploy.ssh_push.resolve_ssh_client_auth", resolver)
        client = Mock()
        with pytest.raises(ValueError, match="identity changed"):
            transport._connect_client_unlocked(
                client, hostname="member", username="operator", vm_spec=spec
            )
        resolver.assert_not_called()
        client.connect.assert_not_called()
    finally:
        ssh_client_auth.OPERATOR_IDENTITY.reset(token)


def test_prepared_transport_refuses_implicit_identity_discovery():
    token = ssh_client_auth.OPERATOR_IDENTITY.set(None)
    try:
        with pytest.raises(ValueError, match="exact selected identity"):
            SSHPush().prepare_client_authentication({})
    finally:
        ssh_client_auth.OPERATOR_IDENTITY.reset(token)


def test_preparation_uses_current_operator_instead_of_old_cached_identity(tmp_path):
    spec = key_spec(tmp_path)
    original = ssh_client_auth.resolve_ssh_client_auth(
        spec["ssh_public_key"], explicit_private_key=tmp_path / "operator"
    )
    token = ssh_client_auth.OPERATOR_IDENTITY.set(original)
    try:
        transport = SSHPush()
        assert transport._client_authentication(spec) is original
        selected = replace(original, _key_path=tmp_path / "selected")
        ssh_client_auth.OPERATOR_IDENTITY.set(selected)
        transport.prepare_client_authentication(spec)
        assert transport._client_authentication(spec) is selected
    finally:
        ssh_client_auth.OPERATOR_IDENTITY.reset(token)


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "member", "target", "artifact"])
def test_incomplete_package_plans_never_create_workers(tmp_path, monkeypatch, invalid):
    options = package_inputs(tmp_path)
    artifact = options["artifact"]
    plans = artifact.dependency_plans
    if invalid == "missing":
        artifact.dependency_plans = plans[:1]
    elif invalid == "duplicate":
        artifact.dependency_plans = plans + plans[:1]
    elif invalid == "member":
        plans[0][1].instance = SimpleNamespace(hostname="foreign")
    elif invalid == "target":
        plans[0][1].target = "foreign"
    else:
        plans[0][1].manifest["wheel_sha256"] = "b" * 64
    transports = Mock()
    monkeypatch.setattr(cli, "SSHPush", transports)
    with pytest.raises(RuntimeError, match="exact approved package plans"):
        cli._prepare_stopped_vm_ha_packages(**options, local={})
    transports.assert_not_called()
