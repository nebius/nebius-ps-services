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


def _install_fake_access_permit_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    common_module = ModuleType("nebius.api.nebius.common.v1")
    common_module.ResourceMetadata = _FakeResourceMetadata  # type: ignore[attr-defined]
    iam_module = ModuleType("nebius.api.nebius.iam.v1")
    iam_module.AccessPermitSpec = _FakeAccessPermitSpec  # type: ignore[attr-defined]
    iam_module.CreateAccessPermitRequest = _FakeCreateAccessPermitRequest  # type: ignore[attr-defined]
    iam_module.ListAccessPermitRequest = _FakeListAccessPermitRequest  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.common.v1", common_module)
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.iam.v1", iam_module)


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
