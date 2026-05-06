from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

import pytest

from nebius_cxcli import iam_bootstrap


@dataclass
class _FakeResourceMetadata:
    parent_id: str


@dataclass
class _FakeAccessPermitSpec:
    resource_id: str
    role: str


@dataclass
class _FakeCreateAccessPermitRequest:
    metadata: _FakeResourceMetadata
    spec: _FakeAccessPermitSpec


@dataclass
class _FakeListAccessPermitRequest:
    parent_id: str
    page_token: str | None = None


class _FakeAccessPermits:
    def __init__(self) -> None:
        self.list_requests: list[_FakeListAccessPermitRequest] = []
        self.create_requests: list[_FakeCreateAccessPermitRequest] = []

    def list(self, request: _FakeListAccessPermitRequest):  # type: ignore[no-untyped-def]
        self.list_requests.append(request)
        response = SimpleNamespace(items=[], next_page_token="")
        return SimpleNamespace(wait=lambda: response)

    def create(self, request: _FakeCreateAccessPermitRequest):  # type: ignore[no-untyped-def]
        self.create_requests.append(request)
        return SimpleNamespace(wait=lambda: SimpleNamespace())


class _CloseTrackingSDK:
    def __init__(self) -> None:
        self.closed = False

    def sync_close(self) -> None:
        self.closed = True


def _install_fake_access_permit_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    common_module = ModuleType("nebius.api.nebius.common.v1")
    common_module.ResourceMetadata = _FakeResourceMetadata  # type: ignore[attr-defined]
    iam_module = ModuleType("nebius.api.nebius.iam.v1")
    iam_module.AccessPermitSpec = _FakeAccessPermitSpec  # type: ignore[attr-defined]
    iam_module.CreateAccessPermitRequest = _FakeCreateAccessPermitRequest  # type: ignore[attr-defined]
    iam_module.ListAccessPermitRequest = _FakeListAccessPermitRequest  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.common.v1", common_module)
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.iam.v1", iam_module)


def test_auth_public_key_exists_closes_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass
    class _FakeGetAuthPublicKeyRequest:
        id: str

    class _FakeAuthPublicKeyServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: _FakeGetAuthPublicKeyRequest):  # type: ignore[no-untyped-def]
            assert request.id == "publickey-123"
            return SimpleNamespace(wait=lambda: SimpleNamespace())

    iam_module = ModuleType("nebius.api.nebius.iam.v1")
    iam_module.AuthPublicKeyServiceClient = _FakeAuthPublicKeyServiceClient  # type: ignore[attr-defined]
    iam_module.GetAuthPublicKeyRequest = _FakeGetAuthPublicKeyRequest  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.iam.v1", iam_module)

    sdk = _CloseTrackingSDK()
    monkeypatch.setattr(iam_bootstrap, "_init_sdk", lambda **_kwargs: sdk)

    assert iam_bootstrap.auth_public_key_exists(
        auth_public_key_id="publickey-123",
        profile=None,
        endpoint=None,
        config_file=None,
    )
    assert sdk.closed


def test_bootstrap_ci_service_account_closes_key_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeAuthPublicKeyServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

    class _FakeAccessKeyServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

    iam_v1_module = ModuleType("nebius.api.nebius.iam.v1")
    iam_v1_module.AuthPublicKeyServiceClient = _FakeAuthPublicKeyServiceClient  # type: ignore[attr-defined]
    iam_v2_module = ModuleType("nebius.api.nebius.iam.v2")
    iam_v2_module.AccessKeyServiceClient = _FakeAccessKeyServiceClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.iam.v1", iam_v1_module)
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.iam.v2", iam_v2_module)

    sdk = _CloseTrackingSDK()
    monkeypatch.setattr(iam_bootstrap, "_init_sdk", lambda **_kwargs: sdk)
    monkeypatch.setattr(
        iam_bootstrap,
        "ensure_ci_service_account_identity",
        lambda **_kwargs: iam_bootstrap.CIIdentityEnsureResult(
            project_id="project-123",
            service_account_name="nebius-cxcli-tf-sa",
            service_account_id="serviceaccount-123",
            service_account_created=False,
            roles_created=[],
            roles_already_present=["editor"],
        ),
    )
    monkeypatch.setattr(
        iam_bootstrap,
        "_create_auth_public_key",
        lambda **_kwargs: ("publickey-123", "PRIVATE-KEY"),
    )
    monkeypatch.setattr(
        iam_bootstrap,
        "_create_object_storage_access_key",
        lambda **_kwargs: ("access-key", "secret-key"),
    )

    result = iam_bootstrap.bootstrap_ci_service_account(
        project_id="project-123",
        service_account_name="nebius-cxcli-tf-sa",
        service_account_description="runtime",
        role_ids=["editor"],
        auth_key_description="auth",
        access_key_description="s3",
        profile=None,
        endpoint=None,
        config_file=None,
    )

    assert result.auth_public_key_id == "publickey-123"
    assert result.s3_access_key_id == "access-key"
    assert sdk.closed


def test_bootstrap_service_account_auth_key_closes_sdk_without_s3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAuthPublicKeyServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

    iam_v1_module = ModuleType("nebius.api.nebius.iam.v1")
    iam_v1_module.AuthPublicKeyServiceClient = _FakeAuthPublicKeyServiceClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.iam.v1", iam_v1_module)

    sdk = _CloseTrackingSDK()
    monkeypatch.setattr(iam_bootstrap, "_init_sdk", lambda **_kwargs: sdk)
    monkeypatch.setattr(
        iam_bootstrap,
        "ensure_ci_service_account_identity",
        lambda **_kwargs: iam_bootstrap.CIIdentityEnsureResult(
            project_id="project-123",
            service_account_name="mysterybox-sa",
            service_account_id="serviceaccount-mysterybox",
            service_account_created=False,
            roles_created=[],
            roles_already_present=["mysterybox.payload-viewer"],
        ),
    )
    monkeypatch.setattr(
        iam_bootstrap,
        "_create_auth_public_key",
        lambda **_kwargs: ("publickey-mysterybox", "PRIVATE-KEY"),
    )
    monkeypatch.setattr(
        iam_bootstrap,
        "_create_object_storage_access_key",
        lambda **_kwargs: pytest.fail("MysteryBox ESO auth must not create S3 access keys"),
    )

    result = iam_bootstrap.bootstrap_service_account_auth_key(
        project_id="project-123",
        service_account_name="mysterybox-sa",
        service_account_description="runtime",
        role_ids=["mysterybox.payload-viewer"],
        auth_key_description="auth",
        profile=None,
        endpoint=None,
        config_file=None,
    )

    assert result.service_account_name == "mysterybox-sa"
    assert result.auth_public_key_id == "publickey-mysterybox"
    assert result.roles_already_present == ["mysterybox.payload-viewer"]
    assert sdk.closed


def test_access_permits_use_group_as_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_access_permit_modules(monkeypatch)
    access_permits = _FakeAccessPermits()

    created, already = iam_bootstrap._ensure_project_role_permits(
        access_permits=access_permits,
        permit_parent_id="group-123",
        principal_label="IAM group 'demo'",
        project_id="project-abc",
        role_ids=["editor"],
    )

    assert created == ["editor"]
    assert already == []
    assert access_permits.list_requests[0].parent_id == "group-123"
    assert access_permits.create_requests[0].metadata.parent_id == "group-123"
    assert access_permits.create_requests[0].spec.resource_id == "project-abc"
    assert access_permits.create_requests[0].spec.role == "editor"


def test_access_permits_normalize_prefixed_role_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_access_permit_modules(monkeypatch)
    access_permits = _FakeAccessPermits()

    created, already = iam_bootstrap._ensure_project_role_permits(
        access_permits=access_permits,
        permit_parent_id="group-123",
        principal_label="IAM group 'demo'",
        project_id="project-abc",
        role_ids=["roles/editor"],
    )

    assert created == ["editor"]
    assert already == []
    assert access_permits.create_requests[0].spec.role == "editor"


def test_group_name_for_service_account_is_stable_and_bounded() -> None:
    name = iam_bootstrap._group_name_for_service_account(
        "THIS-IS-A-VERY-LONG-SERVICE-ACCOUNT-NAME-THAT-SHOULD-BE-TRUNCATED-SAFELY"
    )
    assert name.endswith("permits") or "-permits-" in name
    assert len(name) <= 63


def test_generate_rsa_key_pair_returns_pem_material() -> None:
    private_pem, public_pem = iam_bootstrap._generate_rsa_key_pair_pem()
    assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert public_pem.startswith("-----BEGIN PUBLIC KEY-----")
