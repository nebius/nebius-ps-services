from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

import nebius_vpngw.deploy.ordinary_ssh_enrollment as enrollment_module
from nebius_vpngw.deploy.ordinary_ssh_enrollment import (
    OrdinarySSHEnrollmentTarget,
    _capture_ed25519_host_key,
    _prove_guest_identity,
    _run_guest_query,
    enroll_ordinary_ssh_host_key,
)


def _target(assert_current) -> OrdinarySSHEnrollmentTarget:
    values = (
        "compute-a",
        "project-a",
        "gateway-0",
        "eu-west1",
        "203.0.113.10",
    )
    return OrdinarySSHEnrollmentTarget(
        hostname="gateway-0",
        transport_address=values[4],
        compute_id=values[0],
        project_id=values[1],
        instance_name=values[2],
        region_id=values[3],
        compute_binding_sha256=hashlib.sha256("\0".join(values).encode()).hexdigest(),
        assert_current=assert_current,
    )


def test_enrollment_reproves_cloud_and_guest_before_returning_authority() -> None:
    current_checks: list[str] = []
    captures: list[str] = []
    guest_proofs: list[tuple[str, str]] = []
    key_data = "AAAAC3NzaC1lZDI1NTE5AAAAIFixtureHostIdentity"

    enrollment = enroll_ordinary_ssh_host_key(
        _target(lambda: current_checks.append("current")),
        client_auth=SimpleNamespace(),
        username="operator",
        paramiko_module=SimpleNamespace(),
        capture_host_key=lambda address: captures.append(address) or ("ssh-ed25519", key_data),
        prove_guest_identity=lambda key_type, data: guest_proofs.append((key_type, data)),
    )

    assert captures == ["203.0.113.10", "203.0.113.10"]
    assert current_checks == ["current", "current"]
    assert guest_proofs == [("ssh-ed25519", key_data)]
    assert enrollment.authority.kind == "legacy-ordinary-network-enrollment-v1"
    assert enrollment.authority.compute_binding_sha256 is not None


def test_enrollment_rejects_host_key_change_before_guest_authentication() -> None:
    keys = iter(
        (
            ("ssh-ed25519", "first"),
            ("ssh-ed25519", "second"),
        )
    )
    guest_proofs: list[tuple[str, str]] = []

    with pytest.raises(RuntimeError, match="changed during enrollment"):
        enroll_ordinary_ssh_host_key(
            _target(lambda: None),
            client_auth=SimpleNamespace(),
            username="operator",
            paramiko_module=SimpleNamespace(),
            capture_host_key=lambda _address: next(keys),
            prove_guest_identity=lambda key_type, data: guest_proofs.append((key_type, data)),
        )

    assert guest_proofs == []


def test_enrollment_rejects_compute_change_after_guest_correlation() -> None:
    checks = 0

    def assert_current() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("Compute changed")

    with pytest.raises(RuntimeError, match="Compute changed"):
        enroll_ordinary_ssh_host_key(
            _target(assert_current),
            client_auth=SimpleNamespace(),
            username="operator",
            paramiko_module=SimpleNamespace(),
            capture_host_key=lambda _address: ("ssh-ed25519", "same"),
            prove_guest_identity=lambda _key_type, _data: None,
        )


class _Stream:
    def __init__(self, payload: bytes, *, status: int = 0) -> None:
        self.payload = payload
        self.channel = SimpleNamespace(recv_exit_status=lambda: status)

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


def test_concrete_host_key_capture_closes_transport_and_requires_ed25519(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(close=lambda: None)
    closed: list[str] = []
    key_type = ["ssh-ed25519"]

    class Transport:
        def __init__(self, observed_connection: object) -> None:
            assert observed_connection is connection

        def start_client(self, *, timeout: int) -> None:
            assert timeout == 10

        def get_remote_server_key(self) -> object:
            return SimpleNamespace(
                get_name=lambda: key_type[0],
                get_base64=lambda: "host-key-data",
            )

        def close(self) -> None:
            closed.append("closed")

    def connect(address: tuple[str, int], *, timeout: int) -> object:
        assert address == ("203.0.113.10", 22)
        assert timeout == 10
        return connection

    monkeypatch.setattr(enrollment_module.socket, "create_connection", connect)
    paramiko_api = SimpleNamespace(Transport=Transport)

    assert _capture_ed25519_host_key(
        "203.0.113.10",
        paramiko_module=paramiko_api,
    ) == ("ssh-ed25519", "host-key-data")
    assert closed == ["closed"]

    key_type[0] = "ssh-rsa"
    with pytest.raises(ValueError, match="requires one Ed25519"):
        _capture_ed25519_host_key(
            "203.0.113.10",
            paramiko_module=paramiko_api,
        )
    assert closed == ["closed", "closed"]


def test_guest_query_is_bounded_and_uses_the_remote_exit_status() -> None:
    responses = iter(
        (
            (None, _Stream(b" identity \n"), _Stream(b"")),
            (None, _Stream(b"ignored", status=1), _Stream(b"diagnostic")),
            (None, _Stream(b"x" * 6), _Stream(b"")),
        )
    )
    client = SimpleNamespace(exec_command=lambda _command, timeout: next(responses))

    assert _run_guest_query(client, "identity", timeout=5, limit=16) == "identity"
    assert _run_guest_query(client, "identity", timeout=5, limit=16) is None
    with pytest.raises(RuntimeError, match="exceeded its safety limit"):
        _run_guest_query(client, "identity", timeout=5, limit=5)


@pytest.mark.parametrize("metadata_matches", [True, False])
def test_concrete_guest_proof_pins_the_observed_key_and_closes_the_client(
    metadata_matches: bool,
) -> None:
    target = _target(lambda: None)
    host_key_data = base64.b64encode(b"fixture-ed25519-key").decode("ascii")
    added_keys: list[tuple[str, str, object]] = []
    queries: list[str] = []
    closed: list[str] = []

    class Client:
        def get_host_keys(self) -> object:
            return SimpleNamespace(
                add=lambda host, key_type, key: added_keys.append((host, key_type, key))
            )

        def set_missing_host_key_policy(self, policy: object) -> None:
            assert policy == "reject"

        def connect(self, **kwargs: object) -> None:
            assert kwargs["hostname"] == target.transport_address
            assert kwargs["username"] == "operator"
            assert kwargs["allow_agent"] is False

        def exec_command(self, command: str, *, timeout: int):
            queries.append(command)
            if "instance-data" in command:
                payload = (
                    {
                        "id": target.compute_id,
                        "parent_id": target.project_id,
                        "name": target.instance_name,
                        "region": target.region_id,
                    }
                    if metadata_matches
                    else {"id": "different"}
                )
                return None, _Stream(json.dumps(payload).encode()), _Stream(b"")
            assert command == "cloud-init query instance_id"
            return None, _Stream(target.compute_id.encode()), _Stream(b"")

        def close(self) -> None:
            closed.append("closed")

    client = Client()
    paramiko_api = SimpleNamespace(
        SSHClient=lambda: client,
        Ed25519Key=lambda *, data: ("ed25519", data),
        RejectPolicy=lambda: "reject",
    )
    client_auth = SimpleNamespace(
        paramiko_connect_kwargs=lambda: {
            "allow_agent": False,
            "look_for_keys": False,
            "password": None,
            "key_filename": "/private/key",
        }
    )

    _prove_guest_identity(
        target,
        key_type="ssh-ed25519",
        key_data=host_key_data,
        client_auth=client_auth,
        username="operator",
        paramiko_module=paramiko_api,
    )

    assert added_keys == [
        (
            target.transport_address,
            "ssh-ed25519",
            ("ed25519", base64.b64decode(host_key_data)),
        )
    ]
    assert len(queries) == (1 if metadata_matches else 2)
    assert closed == ["closed"]
