from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.vm_ha_credentials import (
    VM_HA_CREDENTIAL_MAX_BYTES,
    VMHACredentialIdentity,
    VMHACredentialIdentityError,
    VMHACredentialSet,
    preflight_vm_ha_credentials,
)


def _credential_payload(
    *,
    service_account_id: str = "service-account-a",
    authorized_key_id: str = "authorized-key-a",
    private_value: str = "never-report-this-private-value",
) -> bytes:
    return json.dumps(
        {
            "subject-credentials": {
                "iss": service_account_id,
                "sub": service_account_id,
                "kid": authorized_key_id,
            },
            "private-key": private_value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _private_file(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


class _SDK:
    def __init__(
        self,
        *,
        service_account_id: str = "service-account-a",
        project_id: str = "project-a",
        service_account_name: str = "gateway-ha",
        failure: Exception | None = None,
    ) -> None:
        self.service_account_id = service_account_id
        self.project_id = project_id
        self.service_account_name = service_account_name
        self.failure = failure
        self.calls: list[dict[str, object]] = []
        self.closed = False

    def whoami(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)

        def wait() -> SimpleNamespace:
            if self.failure is not None:
                raise self.failure
            metadata = SimpleNamespace(
                id=self.service_account_id,
                parent_id=self.project_id,
                name=self.service_account_name,
            )
            return SimpleNamespace(
                service_account_profile=SimpleNamespace(info=SimpleNamespace(metadata=metadata))
            )

        return SimpleNamespace(wait=wait)

    def sync_close(self) -> None:
        self.closed = True


def _reader(path: Path) -> SimpleNamespace:
    subject = json.loads(path.read_text(encoding="utf-8"))["subject-credentials"]
    return SimpleNamespace(
        service_account_id=subject["sub"],
        public_key_id=subject["kid"],
    )


def test_preflight_authenticates_one_source_and_binds_both_nodes(tmp_path: Path) -> None:
    node_a = _private_file(tmp_path / "node-a.json", _credential_payload(private_value="a"))
    sdks: list[_SDK] = []

    def sdk_factory(*, credentials_file_name: str) -> _SDK:
        assert Path(credentials_file_name) == node_a
        sdk = _SDK()
        sdks.append(sdk)
        return sdk

    verified = preflight_vm_ha_credentials(
        ("node-b", "node-a"),
        source_path=node_a,
        project_id="project-a",
        requested_service_account_name="gateway-ha",
        expected_uid=os.getuid(),
        reader_factory=_reader,
        sdk_factory=sdk_factory,
    )

    assert [node.node_id for node in verified.nodes] == ["node-a", "node-b"]
    assert verified.service_account_id == "service-account-a"
    assert verified.authorized_key_id == "authorized-key-a"
    assert verified.project_id == "project-a"
    assert len(verified.resource_bindings()) == 6
    assert len(sdks) == 1
    assert all(sdk.closed and len(sdk.calls) == 1 for sdk in sdks)
    assert all(sdk.calls[0]["retries"] == 1 for sdk in sdks)
    assert all(sdk.calls[0]["auth_options"] for sdk in sdks)


@pytest.mark.parametrize(
    ("peer_path", "peer_digest", "reason"),
    [
        (Path("/operator/other.json"), "d" * 64, "source-path-mismatch"),
        (Path("/operator/credentials.json"), "e" * 64, "credential-digest-mismatch"),
    ],
)
def test_credential_set_requires_one_source_and_digest(
    peer_path: Path,
    peer_digest: str,
    reason: str,
) -> None:
    def identity(node_id: str, path: Path, digest: str) -> VMHACredentialIdentity:
        return VMHACredentialIdentity(
            node_id=node_id,
            source_path=path,
            credential_sha256=digest,
            service_account_id="service-account-a",
            authorized_key_id="authorized-key-a",
            project_id="project-a",
            service_account_name="gateway-ha",
        )

    with pytest.raises(VMHACredentialIdentityError, match=reason):
        VMHACredentialSet(
            nodes=(
                identity("node-a", Path("/operator/credentials.json"), "d" * 64),
                identity("node-b", peer_path, peer_digest),
            )
        )


def test_preflight_rejects_unsafe_file_before_sdk_creation(tmp_path: Path) -> None:
    node_a = _private_file(tmp_path / "node-a.json", _credential_payload())
    node_a.chmod(0o644)
    calls = 0

    def sdk_factory(**_kwargs: object) -> _SDK:
        nonlocal calls
        calls += 1
        return _SDK()

    with pytest.raises(VMHACredentialIdentityError, match="file-unsafe"):
        preflight_vm_ha_credentials(
            ("node-a", "node-b"),
            source_path=node_a,
            project_id="project-a",
            expected_uid=os.getuid(),
            reader_factory=_reader,
            sdk_factory=sdk_factory,
        )

    assert calls == 0


def test_preflight_rejects_oversized_file_before_sdk_creation(tmp_path: Path) -> None:
    node_a = _private_file(
        tmp_path / "node-a.json",
        b"x" * (VM_HA_CREDENTIAL_MAX_BYTES + 1),
    )
    calls = 0

    def sdk_factory(**_kwargs: object) -> _SDK:
        nonlocal calls
        calls += 1
        return _SDK()

    with pytest.raises(VMHACredentialIdentityError, match="file-oversized"):
        preflight_vm_ha_credentials(
            ("node-a", "node-b"),
            source_path=node_a,
            project_id="project-a",
            expected_uid=os.getuid(),
            reader_factory=_reader,
            sdk_factory=sdk_factory,
        )

    assert calls == 0


def test_preflight_sanitizes_sdk_failure_and_closes_client(tmp_path: Path) -> None:
    secret = "never-report-this-private-value"
    node_a = _private_file(tmp_path / "node-a.json", _credential_payload(private_value=secret))
    sdk = _SDK(failure=RuntimeError(f"remote failure included {secret}"))

    with pytest.raises(VMHACredentialIdentityError) as captured:
        preflight_vm_ha_credentials(
            ("node-a", "node-b"),
            source_path=node_a,
            project_id="project-a",
            expected_uid=os.getuid(),
            reader_factory=_reader,
            sdk_factory=lambda **_kwargs: sdk,
        )

    assert captured.value.reason == "authentication-failed"
    assert secret not in str(captured.value)
    assert sdk.closed


def test_preflight_rejects_authenticated_project_mismatch(tmp_path: Path) -> None:
    node_a = _private_file(tmp_path / "node-a.json", _credential_payload())
    sdk = _SDK(project_id="project-b")

    with pytest.raises(VMHACredentialIdentityError, match="project-mismatch"):
        preflight_vm_ha_credentials(
            ("node-a", "node-b"),
            source_path=node_a,
            project_id="project-a",
            expected_uid=os.getuid(),
            reader_factory=_reader,
            sdk_factory=lambda **_kwargs: sdk,
        )

    assert sdk.closed
