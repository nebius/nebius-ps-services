from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.agent.vm_ha.runtime import (
    RUNTIME_IDENTITY_FILENAME,
    InstalledCredentialBundle,
    build_identity_bound_sdk,
    runtime_identity_public_status,
)
from nebius_vpngw.schema import (
    VMHARole,
    VMHARuntimeBinding,
    VMHARuntimeNodeBinding,
)
from nebius_vpngw.vm_ha_credentials import (
    VM_HA_CREDENTIAL_MAX_BYTES,
    VMHACredentialIdentityError,
)


class _Bundle:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.checks = 0

    def revalidate(self) -> tuple[str, str]:
        self.checks += 1
        if self.failure is not None:
            raise self.failure
        return "service-account-a", "authorized-key-a"


class _SDK:
    def __init__(self, *, service_account_id: str = "service-account-a") -> None:
        self.service_account_id = service_account_id
        self.whoami_calls = 0
        self.close_calls = 0

    def whoami(self, **_kwargs: object) -> SimpleNamespace:
        self.whoami_calls += 1
        response = SimpleNamespace(
            service_account_profile=SimpleNamespace(
                info=SimpleNamespace(
                    metadata=SimpleNamespace(
                        id=self.service_account_id,
                        parent_id="project-a",
                        name="gateway-ha",
                    )
                )
            )
        )
        return SimpleNamespace(wait=lambda: response)

    def sync_close(self) -> None:
        self.close_calls += 1


def _binding(credentials_path: Path) -> tuple[VMHARuntimeBinding, VMHARuntimeNodeBinding]:
    local = VMHARuntimeNodeBinding.model_construct(
        node_id="node-a",
        role=VMHARole.ACTIVE,
        compute_id="compute-a",
        network_interface_name="eth0",
        peer_endpoint="127.0.0.1:9443",
        nebius_credentials_path=str(credentials_path),
        nebius_credentials_sha256="d" * 64,
    )
    peer = VMHARuntimeNodeBinding.model_construct(
        node_id="node-b",
        role=VMHARole.PASSIVE,
        compute_id="compute-b",
        network_interface_name="eth0",
        peer_endpoint="127.0.0.1:9444",
        nebius_credentials_path="/unused/peer.json",
        nebius_credentials_sha256="d" * 64,
    )
    binding = VMHARuntimeBinding.model_construct(
        cluster_id="cluster-a",
        shared_allocation_id="allocation-a",
        nodes=(local, peer),
        route_targets=(),
        migration_routes=(),
        route_runtime_id="route-runtime-a",
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
        nebius_project_id="project-a",
        nebius_service_account_id="service-account-a",
        nebius_authorized_key_id="authorized-key-a",
    )
    return binding, local


def _private_credentials(path: Path) -> Path:
    path.write_text(json.dumps({"fixture": True}), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_exact_current_boot_attestation_reuses_online_identity_proof(tmp_path: Path) -> None:
    binding, local = _binding(_private_credentials(tmp_path / "credentials.json"))
    bundle = _Bundle()
    sdks: list[_SDK] = []

    def factory(**_kwargs: object) -> _SDK:
        sdk = _SDK()
        sdks.append(sdk)
        return sdk

    first = build_identity_bound_sdk(
        binding=binding,
        local=local,
        credential_bundle=bundle,
        state_dir=tmp_path / "state",
        boot_id="boot-a",
        factory=factory,
        identity_proof_mode="systemd-preflight",
        systemd_invocation_id="1" * 32,
        clock=lambda: 100.0,
    )
    first.close()
    second = build_identity_bound_sdk(
        binding=binding,
        local=local,
        credential_bundle=bundle,
        state_dir=tmp_path / "state",
        boot_id="boot-a",
        factory=factory,
        identity_proof_mode="systemd-controller",
        systemd_invocation_id="1" * 32,
        clock=lambda: 101.0,
    )
    second.close()
    repeated_controller = build_identity_bound_sdk(
        binding=binding,
        local=local,
        credential_bundle=bundle,
        state_dir=tmp_path / "state",
        boot_id="boot-a",
        factory=factory,
        identity_proof_mode="systemd-controller",
        systemd_invocation_id="1" * 32,
        clock=lambda: 102.0,
    )
    repeated_controller.close()

    assert [sdk.whoami_calls for sdk in sdks] == [1, 0, 1]
    assert bundle.checks == 3
    assert runtime_identity_public_status(
        state_dir=tmp_path / "state", current_boot_id="boot-a"
    ) == {"state": "verified", "reason": "exact-current-boot"}


def test_same_boot_restart_cannot_reuse_a_previous_systemd_preflight(tmp_path: Path) -> None:
    binding, local = _binding(_private_credentials(tmp_path / "credentials.json"))
    sdks: list[_SDK] = []

    def factory(**_kwargs: object) -> _SDK:
        sdk = _SDK()
        sdks.append(sdk)
        return sdk

    preflight = build_identity_bound_sdk(
        binding=binding,
        local=local,
        credential_bundle=_Bundle(),
        state_dir=tmp_path / "state",
        boot_id="boot-a",
        factory=factory,
        identity_proof_mode="systemd-preflight",
        systemd_invocation_id="1" * 32,
        clock=lambda: 100.0,
    )
    preflight.close()
    restarted = build_identity_bound_sdk(
        binding=binding,
        local=local,
        credential_bundle=_Bundle(),
        state_dir=tmp_path / "state",
        boot_id="boot-a",
        factory=factory,
        identity_proof_mode="systemd-controller",
        systemd_invocation_id="2" * 32,
        clock=lambda: 101.0,
    )
    restarted.close()

    assert [sdk.whoami_calls for sdk in sdks] == [1, 1]


def test_expired_systemd_preflight_requires_fresh_online_proof(tmp_path: Path) -> None:
    binding, local = _binding(_private_credentials(tmp_path / "credentials.json"))
    sdks: list[_SDK] = []

    def factory(**_kwargs: object) -> _SDK:
        sdk = _SDK()
        sdks.append(sdk)
        return sdk

    preflight = build_identity_bound_sdk(
        binding=binding,
        local=local,
        credential_bundle=_Bundle(),
        state_dir=tmp_path / "state",
        boot_id="boot-a",
        factory=factory,
        identity_proof_mode="systemd-preflight",
        systemd_invocation_id="1" * 32,
        clock=lambda: 100.0,
    )
    preflight.close()
    expired = build_identity_bound_sdk(
        binding=binding,
        local=local,
        credential_bundle=_Bundle(),
        state_dir=tmp_path / "state",
        boot_id="boot-a",
        factory=factory,
        identity_proof_mode="systemd-controller",
        systemd_invocation_id="1" * 32,
        clock=lambda: 161.0,
    )
    expired.close()

    assert [sdk.whoami_calls for sdk in sdks] == [1, 1]


def test_direct_controller_cannot_reuse_a_systemd_preflight(tmp_path: Path) -> None:
    binding, local = _binding(_private_credentials(tmp_path / "credentials.json"))
    sdks: list[_SDK] = []

    def factory(**_kwargs: object) -> _SDK:
        sdk = _SDK()
        sdks.append(sdk)
        return sdk

    preflight = build_identity_bound_sdk(
        binding=binding,
        local=local,
        credential_bundle=_Bundle(),
        state_dir=tmp_path / "state",
        boot_id="boot-a",
        factory=factory,
        identity_proof_mode="systemd-preflight",
        systemd_invocation_id="1" * 32,
        clock=lambda: 100.0,
    )
    preflight.close()
    direct = build_identity_bound_sdk(
        binding=binding,
        local=local,
        credential_bundle=_Bundle(),
        state_dir=tmp_path / "state",
        boot_id="boot-a",
        factory=factory,
        clock=lambda: 101.0,
    )
    direct.close()

    assert [sdk.whoami_calls for sdk in sdks] == [1, 1]


def test_installed_credential_bundle_rejects_oversized_payload_before_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path(
        f"/etc/nebius-vpngw/vm-ha-credentials/{'a' * 64}/node-a/{'d' * 64}/nebius-credentials.json"
    )
    bundle = InstalledCredentialBundle(
        node_id="node-a",
        generation_id="a" * 64,
        bundle_digest="d" * 64,
        path=path,
        service_account_id="service-account-a",
        authorized_key_id="authorized-key-a",
    )
    directory_metadata = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
    file_metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_uid=0,
        st_nlink=1,
        st_size=VM_HA_CREDENTIAL_MAX_BYTES + 1,
    )
    monkeypatch.setattr(Path, "lstat", lambda _path: directory_metadata)
    monkeypatch.setattr(os, "open", lambda *_args: 37)
    monkeypatch.setattr(os, "fstat", lambda _descriptor: file_metadata)
    close = SimpleNamespace(calls=0)

    def close_descriptor(_descriptor: int) -> None:
        close.calls += 1

    monkeypatch.setattr(os, "close", close_descriptor)
    monkeypatch.setattr(
        os,
        "read",
        lambda *_args: pytest.fail("oversized credential payload must not be read"),
    )

    with pytest.raises(ValueError, match="oversized"):
        bundle.revalidate()

    assert close.calls == 1


def test_stale_boot_attestation_requires_fresh_online_proof(tmp_path: Path) -> None:
    binding, local = _binding(_private_credentials(tmp_path / "credentials.json"))
    sdks: list[_SDK] = []

    def factory(**_kwargs: object) -> _SDK:
        sdk = _SDK()
        sdks.append(sdk)
        return sdk

    for boot_id in ("boot-a", "boot-b"):
        sdk = build_identity_bound_sdk(
            binding=binding,
            local=local,
            credential_bundle=_Bundle(),
            state_dir=tmp_path / "state",
            boot_id=boot_id,
            factory=factory,
        )
        sdk.close()

    assert [sdk.whoami_calls for sdk in sdks] == [1, 1]


def test_legacy_runtime_binding_blocks_production_boot_before_sdk_creation(
    tmp_path: Path,
) -> None:
    binding, local = _binding(_private_credentials(tmp_path / "credentials.json"))
    binding = binding.model_copy(
        update={
            "nebius_project_id": None,
            "nebius_service_account_id": None,
            "nebius_authorized_key_id": None,
        }
    )
    local = local.model_copy(update={"nebius_credentials_sha256": None})
    calls = 0

    def factory(**_kwargs: object) -> _SDK:
        nonlocal calls
        calls += 1
        return _SDK()

    with pytest.raises(VMHACredentialIdentityError, match="migration-required"):
        build_identity_bound_sdk(
            binding=binding,
            local=local,
            credential_bundle=_Bundle(),
            state_dir=tmp_path / "state",
            boot_id="boot-a",
            factory=factory,
        )

    assert calls == 0
    assert runtime_identity_public_status(
        state_dir=tmp_path / "state", current_boot_id="boot-a"
    ) == {"state": "migration-required", "reason": "apply-required"}


def test_identity_mismatch_closes_sdk_and_persists_safe_blocked_state(tmp_path: Path) -> None:
    binding, local = _binding(_private_credentials(tmp_path / "credentials.json"))
    sdk = _SDK(service_account_id="service-account-b")

    with pytest.raises(VMHACredentialIdentityError) as captured:
        build_identity_bound_sdk(
            binding=binding,
            local=local,
            credential_bundle=_Bundle(),
            state_dir=tmp_path / "state",
            boot_id="boot-a",
            factory=lambda **_kwargs: sdk,
        )

    assert captured.value.reason == "service-account-mismatch"
    assert sdk.close_calls == 1
    record = json.loads(
        (tmp_path / "state" / RUNTIME_IDENTITY_FILENAME).read_text(encoding="utf-8")
    )
    assert set(record).isdisjoint({"credential_path", "credential_bytes", "exception"})
    assert runtime_identity_public_status(
        state_dir=tmp_path / "state", current_boot_id="boot-a"
    ) == {"state": "blocked", "reason": "service-account-mismatch"}
