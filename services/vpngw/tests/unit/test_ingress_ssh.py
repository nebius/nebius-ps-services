"""Transport-level admission and fresh proof, independent of deployment mocks."""

from __future__ import annotations

import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from nebius_vpngw import ingress_policy
from nebius_vpngw.deploy import ingress_ssh

CONFIG = "gateway:\nconnections: []\n"
POLICY = {"peer_ips": []}
PROBE = {"connection": "203.0.113.4 1234 10.0.0.1 22", "boot_id": "boot"}
EVIDENCE = {
    "effective": True,
    "policy": ingress_policy.identity(POLICY),
    "config": hashlib.sha256(CONFIG.encode()).hexdigest(),
}


class Client:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.commands = []
        self.closed = False

    def exec_command(self, command, **kwargs):
        self.commands.append(command)
        response = io.BytesIO(json.dumps(next(self.replies)).encode())
        response.channel = SimpleNamespace(recv_exit_status=lambda: 0)
        return None, response, None

    def close(self):
        self.closed = True


def boundary(monkeypatch, replies, *, connection_error=None):
    clients = []
    connections = []
    trust = []

    def new_client():
        client = Client(replies)
        clients.append(client)
        return client

    def connect(client, **kwargs):
        connections.append((client, kwargs))
        if connection_error:
            raise connection_error

    monkeypatch.setattr(
        ingress_ssh,
        "configure_paramiko_host_verification",
        lambda client, paramiko, **kwargs: trust.append((client, kwargs)),
    )
    ssh = SimpleNamespace(
        _ensure_paramiko=lambda: SimpleNamespace(SSHClient=new_client),
        _ssh_policy=object(),
        _connect_client=connect,
    )
    return ssh, clients, connections, trust


def test_fresh_proof_uses_new_authenticated_pinned_transport_each_time(monkeypatch):
    ssh, clients, connections, trust = boundary(monkeypatch, [PROBE, EVIDENCE])
    for _ in range(2):
        ingress_ssh.verify_fresh(
            ssh,
            "10.0.0.1",
            SimpleNamespace(hostname="gateway-0", config_yaml=CONFIG),
            {"gateway_group": {"vm_spec": {"ssh_username": "operator"}}},
            boot_id="boot",
        )
    assert clients[0] is not clients[1]
    assert all(client.closed and len(client.commands) == 2 for client in clients)
    assert all(args["username"] == "operator" for _, args in connections)
    assert all(args["hostname"] == "gateway-0" for _, args in trust)
    assert all(args["transport_host"] == "10.0.0.1" for _, args in trust)


@pytest.mark.parametrize(
    "probe,evidence,diagnosis",
    [
        ({**PROBE, "boot_id": "another-boot"}, EVIDENCE, "rebooted"),
        (PROBE, {**EVIDENCE, "config": "wrong"}, "identity differs"),
        (PROBE, {**EVIDENCE, "policy": "a" * 64}, "identity differs"),
    ],
)
def test_fresh_proof_rejects_identity_drift(monkeypatch, probe, evidence, diagnosis):
    ssh, clients, _, _ = boundary(monkeypatch, [probe, evidence])
    with pytest.raises(RuntimeError, match=f"no rollback.*{diagnosis}"):
        ingress_ssh.verify_fresh(
            ssh,
            "10.0.0.1",
            SimpleNamespace(hostname="gateway-0", config_yaml=CONFIG),
            {},
            boot_id="boot",
        )
    assert clients[0].closed


@pytest.mark.parametrize("error", [TimeoutError("timed out"), PermissionError("authentication")])
def test_fresh_connection_failure_preserves_cause_without_claiming_policy_block(monkeypatch, error):
    ssh, clients, _, _ = boundary(monkeypatch, [], connection_error=error)
    with pytest.raises(RuntimeError) as raised:
        ingress_ssh.verify_fresh(
            ssh,
            "10.0.0.1",
            SimpleNamespace(hostname="gateway-0", config_yaml=CONFIG),
            {},
            boot_id="boot",
        )
    assert "deployment is incomplete" in str(raised.value)
    assert f"Cause: {type(error).__name__}: {error}" in str(raised.value)
    assert clients[0].closed


def test_package_repair_proves_predecessor_without_claiming_desired_policy(monkeypatch):
    ssh, clients, _, _ = boundary(monkeypatch, [PROBE, {"config": "predecessor"}])
    ingress_ssh.verify_repair_checkpoint(
        ssh,
        "10.0.0.1",
        SimpleNamespace(hostname="gateway-0", config_yaml=CONFIG),
        {},
        boot_id="boot",
        config_sha256="predecessor",
    )
    assert clients[0].closed
    assert "p.verify" not in clients[0].commands[-1]
    assert "config-resolved.yaml" in clients[0].commands[-1]


def test_preflight_checks_boot_without_ssh_source_restrictions():
    client = Client([{**PROBE, "connection": "203.0.113.9 1234 10.0.0.1 22"}])
    assert ingress_ssh.preflight(client, CONFIG) == "boot"
    assert len(client.commands) == 1
    assert "sudo" not in client.commands[0]


@pytest.mark.parametrize("case", ["healthy", "missing", "drift", "unreadable"])
def test_remote_readback_distinguishes_policy_drift_from_unknown_state(
    monkeypatch, tmp_path, capsys, case
):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG)
    persisted = tmp_path / "policy.json"
    monkeypatch.setattr(ingress_policy, "POLICY_FILE", persisted)
    if case != "missing":
        persisted.write_text(ingress_policy.serialized(POLICY))

    def verify(policy):
        if case == "drift":
            raise ingress_policy.IngressPolicyDrift("different rules")
        if case == "unreadable":
            raise RuntimeError("native observation failed")

    monkeypatch.setattr(ingress_policy, "verify", verify)
    code = ingress_ssh.READBACK.replace("/etc/nebius-vpngw/config-resolved.yaml", str(config))
    if case == "unreadable":
        with pytest.raises(RuntimeError, match="native observation failed"):
            exec(compile(code, "<ingress-readback>", "exec"), {})
        assert capsys.readouterr().out == ""
    else:
        exec(compile(code, "<ingress-readback>", "exec"), {})
        observed = json.loads(capsys.readouterr().out)
        assert observed["config"] == EVIDENCE["config"]
        assert observed["effective"] is (case == "healthy")
        assert observed["policy"] == (None if case == "missing" else EVIDENCE["policy"])
