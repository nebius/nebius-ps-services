from __future__ import annotations

import importlib
import sys
import traceback
from dataclasses import dataclass, fields
from types import ModuleType, SimpleNamespace

import pytest

from nebius_cxcli import iam_bootstrap
from nebius_cxcli.credential_compensation import CallbackCredentialDeliveryAdapter


def _delivery(callback):  # type: ignore[no-untyped-def]
    return CallbackCredentialDeliveryAdapter(
        kind="test-destination",
        target="test-target",
        deliver_callback=lambda result, _intent: callback(result),
    )


def _assert_secret_free_provider_error(call, expected_code: str) -> None:  # type: ignore[no-untyped-def]
    sentinel = "provider-response-secret=must-not-escape"
    try:
        call(sentinel)
    except iam_bootstrap.CredentialProviderError as exc:
        rendered_traceback = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert exc.code == expected_code
        assert expected_code in str(exc)
        assert sentinel not in str(exc)
        assert sentinel not in rendered_traceback
    else:
        raise AssertionError("credential provider failure was not sanitized")


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
    monkeypatch.setattr(
        importlib.import_module("nebius.api.nebius.common"),
        "v1",
        common_module,
        raising=False,
    )
    monkeypatch.setattr(
        importlib.import_module("nebius.api.nebius.iam"),
        "v1",
        iam_module,
        raising=False,
    )


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


@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [
        ("auth-public-key", "iam-auth-public-key-create-failed"),
        ("access-key", "iam-access-key-create-failed"),
    ],
)
def test_credential_provider_failures_redact_raw_exception_text(
    operation: str,
    expected_code: str,
) -> None:
    def _invoke(sentinel: str) -> None:
        class _FailingCredentialClient:
            def create(self, _request: object) -> object:
                raise RuntimeError(sentinel)

        if operation == "auth-public-key":
            iam_bootstrap._upload_auth_public_key(
                auth_keys=_FailingCredentialClient(),
                project_id="project-123",
                service_account_id="serviceaccount-123",
                description="test auth key",
                public_key_pem="PUBLIC-KEY",
            )
        else:
            iam_bootstrap._create_object_storage_access_key(
                access_keys=_FailingCredentialClient(),
                project_id="project-123",
                service_account_id="serviceaccount-123",
                description="test access key",
            )

    _assert_secret_free_provider_error(_invoke, expected_code)


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        ("auth-public-key", "iam-auth-public-key-list-failed"),
        ("access-key", "iam-access-key-list-failed"),
        ("static-key", "iam-static-key-list-failed"),
    ],
)
def test_credential_recovery_list_failures_are_secret_free(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected_code: str,
) -> None:
    monkeypatch.setattr(iam_bootstrap, "_account_ref", lambda _service_account_id: object())

    def _invoke(sentinel: str) -> None:
        class _FailingCredentialClient:
            def list_by_account(self, _request: object) -> object:
                raise RuntimeError(sentinel)

            def list(self, _request: object) -> object:
                raise RuntimeError(sentinel)

        client = _FailingCredentialClient()
        iam_bootstrap._operation_owned_credential_ids(
            kind=kind,
            service_account_id="serviceaccount-123",
            operation_id="a" * 32,
            project_id="project-123",
            auth_keys=client if kind == "auth-public-key" else None,
            access_keys=client if kind == "access-key" else None,
            static_keys=client if kind == "static-key" else None,
        )

    _assert_secret_free_provider_error(_invoke, expected_code)


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        ("auth-public-key", "iam-auth-public-key-delete-failed"),
        ("access-key", "iam-access-key-delete-failed"),
        ("static-key", "iam-static-key-delete-failed"),
    ],
)
def test_credential_recovery_delete_failures_are_secret_free(
    kind: str,
    expected_code: str,
) -> None:
    def _invoke(sentinel: str) -> None:
        class _FailingCredentialClient:
            def delete(self, _request: object) -> object:
                raise RuntimeError(sentinel)

        client = _FailingCredentialClient()
        iam_bootstrap._delete_operation_credential(
            kind=kind,
            resource_id="credential-123",
            auth_keys=client if kind == "auth-public-key" else None,
            access_keys=client if kind == "access-key" else None,
            static_keys=client if kind == "static-key" else None,
        )

    _assert_secret_free_provider_error(_invoke, expected_code)


@pytest.mark.parametrize(
    "expected_code",
    ["iam-access-key-secret-read-failed", "iam-static-key-issue-failed"],
)
def test_secret_result_provider_boundaries_are_secret_free(expected_code: str) -> None:
    def _invoke(sentinel: str) -> None:
        iam_bootstrap._credential_provider_result(
            expected_code,
            lambda: (_ for _ in ()).throw(RuntimeError(sentinel)),
        )

    _assert_secret_free_provider_error(_invoke, expected_code)


def test_bootstrap_ci_service_account_closes_key_sdk(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
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
    monkeypatch.setenv("NEBIUS_CXCLI_CREDENTIAL_JOURNAL_DIR", str(tmp_path / "journals"))
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
        lambda **_kwargs: ("accesskey-resource", "access-key", "secret-key"),
    )
    delivered: list[iam_bootstrap.CIBootstrapResult] = []

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
        compensation_scope="test-ci",
        delivery=_delivery(delivered.append),
    )

    assert result.auth_public_key_id == "publickey-123"
    assert result.s3_access_key_id == "access-key"
    assert delivered == [result]
    assert sdk.closed


def test_bootstrap_result_sensitive_fields_are_excluded_from_repr() -> None:
    ci_fields = {item.name: item.repr for item in fields(iam_bootstrap.CIBootstrapResult)}
    auth_fields = {
        item.name: item.repr for item in fields(iam_bootstrap.ServiceAccountAuthKeyResult)
    }
    object_storage_fields = {
        item.name: item.repr for item in fields(iam_bootstrap.ObjectStorageAccessKeyResult)
    }
    static_fields = {item.name: item.repr for item in fields(iam_bootstrap.StaticKeyIssueResult)}

    assert ci_fields["auth_" + "private_key_pem"] is False
    assert ci_fields["s3_" + "secret_" + "access_key"] is False
    assert auth_fields["auth_" + "private_key_pem"] is False
    assert object_storage_fields["s3_" + "secret_" + "access_key"] is False
    assert static_fields["to" + "ken"] is False


def test_delivery_failure_preserves_created_credentials_when_effect_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("NEBIUS_CXCLI_CREDENTIAL_JOURNAL_DIR", str(tmp_path / "journals"))
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        iam_bootstrap,
        "_delete_operation_credential",
        lambda *, kind, resource_id, **_kwargs: deleted.append((kind, resource_id)),
    )

    def _create(journal, _operation_id):  # type: ignore[no-untyped-def]
        journal.record_intent(
            kind="auth-public-key",
            ownership_sha256="sha256:" + "1" * 64,
            service_account_id="serviceaccount-123",
        )
        journal.record_created(kind="auth-public-key", resource_id="publickey-123")
        journal.record_intent(
            kind="access-key",
            ownership_sha256="sha256:" + "2" * 64,
            service_account_id="serviceaccount-123",
        )
        journal.record_created(kind="access-key", resource_id="accesskey-123")
        return SimpleNamespace(secret="must-not-persist")

    with pytest.raises(RuntimeError, match="delivery failed"):
        iam_bootstrap._run_compensated_credential_issue(
            project_id="project-123",
            scope="delivery-test",
            auth_keys=object(),
            access_keys=object(),
            static_keys=None,
            create=_create,
            delivery=_delivery(
                lambda _result: (_ for _ in ()).throw(RuntimeError("delivery failed"))
            ),
        )

    assert deleted == []
    raw_journal = next((tmp_path / "journals").glob("*.json")).read_text(encoding="utf-8")
    assert '"status": "delivery-uncertain"' in raw_journal
    assert "must-not-persist" not in raw_journal


def test_bootstrap_service_account_auth_key_closes_sdk_without_s3(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    class _FakeAuthPublicKeyServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

    iam_v1_module = ModuleType("nebius.api.nebius.iam.v1")
    iam_v1_module.AuthPublicKeyServiceClient = _FakeAuthPublicKeyServiceClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.iam.v1", iam_v1_module)

    sdk = _CloseTrackingSDK()
    monkeypatch.setenv("NEBIUS_CXCLI_CREDENTIAL_JOURNAL_DIR", str(tmp_path / "journals"))
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
        compensation_scope="test-auth",
        delivery=_delivery(lambda _result: None),
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


def test_access_permits_reject_repeated_page_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_access_permit_modules(monkeypatch)

    class _RepeatingAccessPermits:
        def __init__(self) -> None:
            self.list_requests: list[_FakeListAccessPermitRequest] = []

        def list(self, request: _FakeListAccessPermitRequest):  # type: ignore[no-untyped-def]
            self.list_requests.append(request)
            response = SimpleNamespace(items=[], next_page_token="same-token")
            return SimpleNamespace(wait=lambda: response)

    access_permits = _RepeatingAccessPermits()

    with pytest.raises(RuntimeError, match="repeated pagination token"):
        iam_bootstrap._ensure_project_role_permits(
            access_permits=access_permits,
            permit_parent_id="group-123",
            principal_label="IAM group 'demo'",
            project_id="project-abc",
            role_ids=["editor"],
        )

    assert [request.page_token for request in access_permits.list_requests] == [
        None,
        "same-token",
    ]


def test_strict_access_permits_reject_unexpected_project_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_access_permit_modules(monkeypatch)

    class _UnexpectedRoleAccessPermits(_FakeAccessPermits):
        def list(self, request: _FakeListAccessPermitRequest):  # type: ignore[no-untyped-def]
            self.list_requests.append(request)
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(spec=SimpleNamespace(resource_id="project-abc", role="editor")),
                    SimpleNamespace(spec=SimpleNamespace(resource_id="project-abc", role="viewer")),
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

    access_permits = _UnexpectedRoleAccessPermits()

    with pytest.raises(RuntimeError, match="unexpected project role permits: viewer"):
        iam_bootstrap._ensure_project_role_permits(
            access_permits=access_permits,
            permit_parent_id="group-123",
            principal_label="IAM group 'demo'",
            project_id="project-abc",
            role_ids=["editor"],
            reject_unexpected_role_ids=True,
        )

    assert access_permits.create_requests == []


def test_read_only_access_permit_validation_rejects_missing_role_without_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_access_permit_modules(monkeypatch)
    access_permits = _FakeAccessPermits()

    with pytest.raises(RuntimeError, match="missing required project role permits: editor"):
        iam_bootstrap._ensure_project_role_permits(
            access_permits=access_permits,
            permit_parent_id="group-123",
            principal_label="IAM group 'demo'",
            project_id="project-abc",
            role_ids=["editor"],
            reject_unexpected_role_ids=True,
            create_missing=False,
        )

    assert access_permits.create_requests == []


def test_strict_access_permits_reject_unexpected_resource_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_access_permit_modules(monkeypatch)

    class _UnexpectedScopeAccessPermits(_FakeAccessPermits):
        def list(self, request: _FakeListAccessPermitRequest):  # type: ignore[no-untyped-def]
            self.list_requests.append(request)
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(spec=SimpleNamespace(resource_id="project-abc", role="editor")),
                    SimpleNamespace(
                        spec=SimpleNamespace(resource_id="project-other", role="editor")
                    ),
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

    access_permits = _UnexpectedScopeAccessPermits()

    with pytest.raises(RuntimeError, match="unexpected resource-scoped access permits"):
        iam_bootstrap._ensure_project_role_permits(
            access_permits=access_permits,
            permit_parent_id="group-123",
            principal_label="IAM group 'demo'",
            project_id="project-abc",
            role_ids=["editor"],
            reject_unexpected_role_ids=True,
        )

    assert access_permits.create_requests == []


def test_strict_service_account_rejects_same_name_with_different_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass
    class _FakeGetServiceAccountByNameRequest:
        parent_id: str
        name: str

    @dataclass
    class _FakeServiceAccountSpec:
        description: str

    @dataclass
    class _FakeCreateServiceAccountRequest:
        metadata: object
        spec: object

    common_module = ModuleType("nebius.api.nebius.common.v1")
    common_module.ResourceMetadata = _FakeResourceMetadata  # type: ignore[attr-defined]
    iam_module = ModuleType("nebius.api.nebius.iam.v1")
    iam_module.GetServiceAccountByNameRequest = _FakeGetServiceAccountByNameRequest  # type: ignore[attr-defined]
    iam_module.ServiceAccountSpec = _FakeServiceAccountSpec  # type: ignore[attr-defined]
    iam_module.CreateServiceAccountRequest = _FakeCreateServiceAccountRequest  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.common.v1", common_module)
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.iam.v1", iam_module)

    class _ServiceAccounts:
        def get_by_name(self, request: object):  # type: ignore[no-untyped-def]
            assert request == _FakeGetServiceAccountByNameRequest(
                parent_id="project-abc",
                name="nebius-cxcli-sa",
            )
            existing = SimpleNamespace(
                metadata=SimpleNamespace(id="serviceaccount-conflict"),
                spec=SimpleNamespace(description="owned by another tool"),
            )
            return SimpleNamespace(wait=lambda: existing)

    with pytest.raises(RuntimeError, match="not the cxcli-managed identity"):
        iam_bootstrap._ensure_service_account(
            service_accounts=_ServiceAccounts(),
            project_id="project-abc",
            service_account_name="nebius-cxcli-sa",
            service_account_description="canonical cxcli runtime identity",
            strict_description=True,
        )


def test_group_member_ids_reject_repeated_page_token(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass
    class _FakeListGroupMembershipsRequest:
        parent_id: str
        page_token: str | None = None

    iam_module = ModuleType("nebius.api.nebius.iam.v1")
    iam_module.ListGroupMembershipsRequest = _FakeListGroupMembershipsRequest  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.iam.v1", iam_module)
    monkeypatch.setattr(
        importlib.import_module("nebius.api.nebius.iam"),
        "v1",
        iam_module,
        raising=False,
    )

    class _RepeatingGroupMemberships:
        def __init__(self) -> None:
            self.list_requests: list[_FakeListGroupMembershipsRequest] = []

        def list_members(self, request: _FakeListGroupMembershipsRequest):  # type: ignore[no-untyped-def]
            self.list_requests.append(request)
            response = SimpleNamespace(memberships=[], next_page_token="same-token")
            return SimpleNamespace(wait=lambda: response)

    group_memberships = _RepeatingGroupMemberships()

    with pytest.raises(RuntimeError, match="repeated pagination token"):
        iam_bootstrap._group_member_ids(
            group_memberships=group_memberships,
            group_id="group-123",
        )

    assert [request.page_token for request in group_memberships.list_requests] == [
        None,
        "same-token",
    ]


def test_group_name_for_service_account_is_stable_and_bounded() -> None:
    name = iam_bootstrap._group_name_for_service_account(
        "THIS-IS-A-VERY-LONG-SERVICE-ACCOUNT-NAME-THAT-SHOULD-BE-TRUNCATED-SAFELY"
    )
    assert name.endswith("permits") or "-permits-" in name
    assert len(name) <= 63


def test_generate_rsa_key_pair_returns_pem_material() -> None:
    private_pem, public_pem = iam_bootstrap.generate_service_account_auth_key_pair()
    assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert public_pem.startswith("-----BEGIN PUBLIC KEY-----")


def test_delete_observability_static_key_is_idempotent_when_already_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DeleteStaticKeyRequest:
        def __init__(self, *, id: str) -> None:
            self.id = id

    class _StaticKeyServiceClient:
        def __init__(self, _sdk: object) -> None:
            pass

        def delete(self, request: _DeleteStaticKeyRequest) -> object:
            assert request.id == "static-key-id"

            def _wait() -> None:
                raise RuntimeError("StatusCode.NOT_FOUND")

            return SimpleNamespace(wait=_wait)

    iam_module = ModuleType("nebius.api.nebius.iam.v1")
    iam_module.DeleteStaticKeyRequest = _DeleteStaticKeyRequest  # type: ignore[attr-defined]
    iam_module.StaticKeyServiceClient = _StaticKeyServiceClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.iam.v1", iam_module)
    sdk = _CloseTrackingSDK()
    monkeypatch.setattr(iam_bootstrap, "_init_sdk", lambda **_kwargs: sdk)

    iam_bootstrap.delete_observability_static_key(
        static_key_id="static-key-id",
        profile=None,
        endpoint=None,
        config_file=None,
    )

    assert sdk.closed
