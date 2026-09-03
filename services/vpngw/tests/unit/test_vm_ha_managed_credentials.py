from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from nebius_vpngw import vm_ha_managed_credentials as managed
from nebius_vpngw.vm_ha_credentials import (
    VMHACredentialIdentity,
    VMHACredentialSet,
    display_vm_ha_credential_path,
    managed_vm_ha_credential_path,
)
from nebius_vpngw.vm_ha_managed_credentials import (
    VMHAManagedCredentialError,
    ensure_managed_vm_ha_credentials,
    inspect_managed_vm_ha_credentials,
    vm_ha_authorized_key_name,
    vm_ha_ownership_labels,
    vm_ha_service_account_name,
)
from nebius_vpngw.vpngw_sa import (
    ServiceAccountTokenIdentity,
    VMHAIAMReconciliationPlan,
)


class _Operation:
    def __init__(self, callback, *, error: Exception | None = None) -> None:
        self.callback = callback
        self.error = error
        self.sync_wait_calls: list[dict[str, object]] = []

    def sync_wait(self, **kwargs):  # type: ignore[no-untyped-def]
        self.sync_wait_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        self.callback()


class _Request:
    def __init__(self, operation: _Operation) -> None:
        self.operation = operation

    def wait(self) -> _Operation:
        return self.operation


def _write_credential(
    path: Path,
    *,
    service_account_id: str = "service-account-a",
    key_id: str = "auth-public-key-a",
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    path.write_text(
        json.dumps(
            {
                "subject-credentials": {
                    "alg": "RS256",
                    "iss": service_account_id,
                    "kid": key_id,
                    "private-key": private_pem,
                    "sub": service_account_id,
                    "type": "JWT",
                }
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _prepare_private_parent(path: Path) -> None:
    path.parent.mkdir(parents=True)
    for parent in (
        path.parent,
        path.parent.parent,
        path.parent.parent.parent,
        path.parent.parent.parent.parent,
    ):
        parent.chmod(0o700)


def _iam_inspector(
    name: str,
    _tenant_id: str | None,
    project_id: str,
    _region_id: str | None,
    *,
    mode: str,
    expected_service_account_id: str | None,
    ownership_labels: dict[str, str],
    **_kwargs,
) -> VMHAIAMReconciliationPlan:
    reuse = mode == "reuse" or expected_service_account_id is not None
    return VMHAIAMReconciliationPlan(
        mode=mode,  # type: ignore[arg-type]
        project_id=project_id,
        service_account_name=name,
        ownership_labels=tuple(sorted(ownership_labels.items())),
        service_account_action="reuse" if reuse else "create",
        service_account_id="service-account-a" if reuse else None,
        group_action="reuse" if reuse else "create",
        group_id="group-a" if reuse else None,
        membership_action="reuse" if reuse else "create",
        permit_action="reuse" if reuse else "create",
    )


def _credential_set(path: Path, *, node_ids: tuple[str, str]) -> VMHACredentialSet:
    payload = path.read_bytes()
    subject = json.loads(payload)["subject-credentials"]
    digest = hashlib.sha256(payload).hexdigest()
    return VMHACredentialSet(
        nodes=tuple(
            VMHACredentialIdentity(
                node_id=node_id,
                source_path=path,
                credential_sha256=digest,
                service_account_id=subject["sub"],
                authorized_key_id=subject["kid"],
                project_id="project-a",
                service_account_name="gateway-a-vm-ha",
            )
            for node_id in node_ids
        )
    )


def _key_item_from_credential(path: Path) -> object:
    payload = json.loads(path.read_text(encoding="utf-8"))["subject-credentials"]
    private_key = serialization.load_pem_private_key(
        payload["private-key"].encode("utf-8"), password=None
    )
    public_key = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(
            id=payload["kid"],
            parent_id="project-a",
            name="gateway-a-vm-ha-runtime-key",
            labels=vm_ha_ownership_labels(project_id="project-a", gateway_name="gateway-a"),
        ),
        spec=SimpleNamespace(
            account=SimpleNamespace(service_account=SimpleNamespace(id=payload["sub"])),
            data=public_key,
            description="Nebius VPNGW VM-HA runtime key",
            expires_at=None,
        ),
    )


class _KeyService:
    def __init__(self) -> None:
        self.items: list[object] = []
        self.create_requests: list[object] = []
        self.fail_create = False
        self.operations: list[_Operation] = []

    def list_by_account(self, _request: object) -> SimpleNamespace:
        return SimpleNamespace(
            wait=lambda: SimpleNamespace(items=list(self.items), next_page_token="")
        )

    def create(self, request: object) -> _Request:
        self.create_requests.append(request)

        def complete() -> None:
            metadata = SimpleNamespace(
                id="auth-public-key-a",
                parent_id=request.metadata.parent_id,
                name=request.metadata.name,
                labels=dict(request.metadata.labels),
            )
            self.items.append(SimpleNamespace(metadata=metadata, spec=request.spec))

        operation = _Operation(
            complete,
            error=(RuntimeError("provider detail must stay private") if self.fail_create else None),
        )
        self.operations.append(operation)
        return _Request(operation)


def test_authorized_key_inventory_reads_every_page() -> None:
    tokens: list[str] = []

    def list_by_account(request: object) -> SimpleNamespace:
        token = str(request.page_token)  # type: ignore[attr-defined]
        tokens.append(token)
        suffix = "one" if not token else "two"
        return SimpleNamespace(
            items=[SimpleNamespace(metadata=SimpleNamespace(id=f"authorized-key-{suffix}"))],
            next_page_token="next" if not token else "",
        )

    items = managed._list_service_account_keys(
        SimpleNamespace(list_by_account=list_by_account),
        "service-account-a",
    )

    assert [item.metadata.id for item in items] == [
        "authorized-key-one",
        "authorized-key-two",
    ]
    assert tokens == ["", "next"]


def _identity_ensurer(
    name: str,
    _tenant_id: str | None,
    project_id: str,
    _region_id: str | None,
    *,
    verified_role_ids: tuple[str, ...],
    ownership_labels: dict[str, str],
    reconciliation_plan: VMHAIAMReconciliationPlan,
) -> ServiceAccountTokenIdentity:
    assert name == "gateway-a-vm-ha"
    assert project_id == "project-a"
    assert verified_role_ids == ("editor",)
    assert ownership_labels == vm_ha_ownership_labels(
        project_id="project-a", gateway_name="gateway-a"
    )
    assert reconciliation_plan.project_id == "project-a"
    return ServiceAccountTokenIdentity(
        token="bounded-token",
        service_account_id="service-account-a",
        service_account_name=name,
    )


def _create_managed_credential(
    tmp_path: Path,
) -> tuple[object, _KeyService, object, object]:
    plan = inspect_managed_vm_ha_credentials(
        project_id="project-a",
        gateway_name="gateway-a",
        node_ids=("node-a", "node-b"),
        home=tmp_path,
        iam_inspector=_iam_inspector,
    )
    key_service = _KeyService()
    client = SimpleNamespace(key_service=key_service, sync_close=lambda: None)

    def preflight(node_ids, *, source_path, **_kwargs):
        return _credential_set(source_path, node_ids=tuple(node_ids))

    with patch(
        "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
        side_effect=lambda _client: _client.key_service,
    ):
        ensure_managed_vm_ha_credentials(
            plan,
            node_ids=("node-a", "node-b"),
            tenant_id=None,
            region_id="eu-north1",
            identity_ensurer=_identity_ensurer,
            client_factory=lambda *_args: (client, True),
            preflight=preflight,
        )
    return plan, key_service, client, preflight


def test_managed_path_is_absolute_but_display_uses_home_shorthand(tmp_path: Path) -> None:
    path = managed_vm_ha_credential_path(
        project_id="project-a",
        gateway_name="gateway-a",
        home=tmp_path,
    )

    assert path == (
        tmp_path
        / ".config"
        / "nebius-vpngw"
        / "credentials"
        / "project-a"
        / "gateway-a"
        / "nebius-credentials.json"
    )
    assert path.is_absolute()
    assert (
        display_vm_ha_credential_path(project_id="project-a", gateway_name="gateway-a")
        == "~/.config/nebius-vpngw/credentials/project-a/gateway-a/nebius-credentials.json"
    )


def test_managed_names_are_deterministic_bounded_and_distinct() -> None:
    first = "gateway-" + "a" * 55
    second = "gateway-" + "b" * 55

    assert len(vm_ha_service_account_name(first)) <= 63
    assert len(vm_ha_authorized_key_name(first)) <= 63
    assert vm_ha_service_account_name(first) != vm_ha_service_account_name(second)
    assert vm_ha_authorized_key_name(first) != vm_ha_authorized_key_name(second)


def test_inspection_of_missing_source_has_no_filesystem_effects(tmp_path: Path) -> None:
    plan = inspect_managed_vm_ha_credentials(
        project_id="project-a",
        gateway_name="gateway-a",
        node_ids=("node-a", "node-b"),
        home=tmp_path,
        iam_inspector=_iam_inspector,
    )

    assert plan.action == "create"
    assert plan.credentials is None
    assert plan.approval_record()["local_source_expected_absent"] is True
    assert not (tmp_path / ".config").exists()


def test_inspection_reuses_one_authenticated_source_for_both_nodes(tmp_path: Path) -> None:
    source = managed_vm_ha_credential_path(
        project_id="project-a", gateway_name="gateway-a", home=tmp_path
    )
    source.parent.mkdir(parents=True)
    for parent in (
        source.parent,
        source.parent.parent,
        source.parent.parent.parent,
        source.parent.parent.parent.parent,
    ):
        parent.chmod(0o700)
    _write_credential(source)
    calls: list[tuple[tuple[str, str], Path]] = []
    credentials = _credential_set(source, node_ids=("node-a", "node-b"))

    def preflight(node_ids, *, source_path, **_kwargs):
        calls.append((tuple(node_ids), source_path))
        return credentials

    key_service = _KeyService()
    key_service.items.append(_key_item_from_credential(source))
    client = SimpleNamespace(key_service=key_service, sync_close=lambda: None)
    with patch(
        "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
        side_effect=lambda _client: _client.key_service,
    ):
        plan = inspect_managed_vm_ha_credentials(
            project_id="project-a",
            gateway_name="gateway-a",
            node_ids=("node-b", "node-a"),
            home=tmp_path,
            preflight=preflight,
            iam_inspector=_iam_inspector,
            client_factory=lambda *_args: (client, True),
        )

    assert plan.action == "reuse"
    assert plan.credentials is credentials
    assert calls == [(("node-b", "node-a"), source)]
    assert plan.approval_record()["action"] == "reuse"


def test_inspection_rejects_shared_managed_directory(tmp_path: Path) -> None:
    source = managed_vm_ha_credential_path(
        project_id="project-a", gateway_name="gateway-a", home=tmp_path
    )
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    source.chmod(0o600)

    with pytest.raises(VMHAManagedCredentialError, match="owner-only"):
        inspect_managed_vm_ha_credentials(
            project_id="project-a",
            gateway_name="gateway-a",
            node_ids=("node-a", "node-b"),
            home=tmp_path,
            preflight=lambda *_args, **_kwargs: SimpleNamespace(),
        )


def test_approved_creation_publishes_one_private_source_and_reuses_cloud_key(
    tmp_path: Path,
) -> None:
    plan = inspect_managed_vm_ha_credentials(
        project_id="project-a",
        gateway_name="gateway-a",
        node_ids=("node-a", "node-b"),
        home=tmp_path,
        iam_inspector=_iam_inspector,
    )
    key_service = _KeyService()
    client = SimpleNamespace(key_service=key_service, sync_close=lambda: None)

    def preflight(node_ids, *, source_path, **_kwargs):
        return _credential_set(source_path, node_ids=tuple(node_ids))

    with patch(
        "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
        side_effect=lambda _client: _client.key_service,
    ):
        result = ensure_managed_vm_ha_credentials(
            plan,
            node_ids=("node-a", "node-b"),
            tenant_id=None,
            region_id="eu-north1",
            identity_ensurer=_identity_ensurer,
            client_factory=lambda *_args: (client, True),
            preflight=preflight,
        )

    assert result.token_identity.token == "bounded-token"
    assert result.credentials.service_account_id == "service-account-a"
    assert len(key_service.create_requests) == 1
    assert key_service.operations[0].sync_wait_calls
    assert stat.S_IMODE(plan.source_path.stat().st_mode) == 0o600
    assert plan.source_path.stat().st_nlink == 1
    assert all(
        stat.S_IMODE(parent.stat().st_mode) == 0o700
        for parent in (
            plan.source_path.parent,
            plan.source_path.parent.parent,
            plan.source_path.parent.parent.parent,
            plan.source_path.parent.parent.parent.parent,
        )
    )
    assert not (plan.source_path.parent / ".enrollment.json").exists()
    assert not (plan.source_path.parent / ".private-key.pending.pem").exists()
    subject = json.loads(plan.source_path.read_text(encoding="utf-8"))["subject-credentials"]
    assert subject["alg"] == "RS256"
    assert subject["kid"] == "auth-public-key-a"
    assert subject["iss"] == subject["sub"] == "service-account-a"
    assert "PRIVATE KEY" in subject["private-key"]
    from nebius.base.service_account.credentials_file import Reader

    parsed = Reader(plan.source_path).read()
    assert parsed.service_account_id == "service-account-a"
    assert parsed.public_key_id == "auth-public-key-a"

    with patch(
        "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
        side_effect=lambda _client: _client.key_service,
    ):
        reuse = inspect_managed_vm_ha_credentials(
            project_id="project-a",
            gateway_name="gateway-a",
            node_ids=("node-a", "node-b"),
            home=tmp_path,
            preflight=preflight,
            iam_inspector=_iam_inspector,
            client_factory=lambda *_args: (client, True),
        )
    with patch(
        "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
        side_effect=lambda _client: _client.key_service,
    ):
        ensure_managed_vm_ha_credentials(
            reuse,
            node_ids=("node-a", "node-b"),
            tenant_id=None,
            region_id="eu-north1",
            identity_ensurer=_identity_ensurer,
            client_factory=lambda *_args: (client, True),
            preflight=preflight,
        )
    assert len(key_service.create_requests) == 1


def test_approved_creation_enforces_private_modes_with_restrictive_umask(
    tmp_path: Path,
) -> None:
    previous_umask = os.umask(0o777)
    try:
        plan, _key_service, _client, _preflight = _create_managed_credential(tmp_path)
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(plan.source_path.stat().st_mode) == 0o600
    assert all(
        stat.S_IMODE(parent.stat().st_mode) == 0o700
        for parent in (
            plan.source_path.parent,
            plan.source_path.parent.parent,
            plan.source_path.parent.parent.parent,
            plan.source_path.parent.parent.parent.parent,
        )
    )
    assert not (plan.source_path.parent / ".enrollment.json").exists()
    assert not (plan.source_path.parent / ".private-key.pending.pem").exists()


def test_failed_key_create_retains_protected_resume_material(tmp_path: Path) -> None:
    plan = inspect_managed_vm_ha_credentials(
        project_id="project-a",
        gateway_name="gateway-a",
        node_ids=("node-a", "node-b"),
        home=tmp_path,
        iam_inspector=_iam_inspector,
    )
    key_service = _KeyService()
    key_service.fail_create = True
    client = SimpleNamespace(key_service=key_service, sync_close=lambda: None)

    with (
        patch(
            "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
            side_effect=lambda _client: _client.key_service,
        ),
        pytest.raises(VMHAManagedCredentialError, match="authorized-key creation failed"),
    ):
        ensure_managed_vm_ha_credentials(
            plan,
            node_ids=("node-a", "node-b"),
            tenant_id=None,
            region_id="eu-north1",
            identity_ensurer=_identity_ensurer,
            client_factory=lambda *_args: (client, True),
        )

    journal = plan.source_path.parent / ".enrollment.json"
    pending = plan.source_path.parent / ".private-key.pending.pem"
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
    combined = journal.read_text(encoding="utf-8")
    assert "PRIVATE KEY" not in combined
    assert not plan.source_path.exists()
    assert os.getuid() == journal.stat().st_uid == pending.stat().st_uid


def test_reuse_rejects_expiring_managed_authorized_key(tmp_path: Path) -> None:
    source = managed_vm_ha_credential_path(
        project_id="project-a", gateway_name="gateway-a", home=tmp_path
    )
    _prepare_private_parent(source)
    _write_credential(source)
    key_service = _KeyService()
    key = _key_item_from_credential(source)
    key.spec.expires_at = object()
    key_service.items.append(key)
    client = SimpleNamespace(key_service=key_service, sync_close=lambda: None)

    with (
        patch(
            "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
            side_effect=lambda _client: _client.key_service,
        ),
        pytest.raises(VMHAManagedCredentialError, match="foreign identity or data"),
    ):
        inspect_managed_vm_ha_credentials(
            project_id="project-a",
            gateway_name="gateway-a",
            node_ids=("node-a", "node-b"),
            home=tmp_path,
            preflight=lambda node_ids, **_kwargs: _credential_set(source, node_ids=tuple(node_ids)),
            iam_inspector=_iam_inspector,
            client_factory=lambda *_args: (client, True),
        )


def test_zero_protobuf_expiry_timestamp_is_treated_as_omitted() -> None:
    from google.protobuf.timestamp_pb2 import Timestamp
    from nebius.api.nebius.iam.v1 import AuthPublicKeySpec

    spec = AuthPublicKeySpec()
    spec.__pb2_message__.expires_at.CopyFrom(Timestamp())
    assert not managed._protobuf_timestamp_is_configured(spec, "expires_at")

    spec.__pb2_message__.expires_at.CopyFrom(Timestamp(seconds=1))
    assert managed._protobuf_timestamp_is_configured(spec, "expires_at")


def test_source_drift_after_plan_is_rejected_before_iam_reconciliation(
    tmp_path: Path,
) -> None:
    source = managed_vm_ha_credential_path(
        project_id="project-a", gateway_name="gateway-a", home=tmp_path
    )
    _prepare_private_parent(source)
    _write_credential(source)
    key_service = _KeyService()
    key_service.items.append(_key_item_from_credential(source))
    client = SimpleNamespace(key_service=key_service, sync_close=lambda: None)

    def preflight(node_ids, *, source_path, **_kwargs):
        return _credential_set(source_path, node_ids=tuple(node_ids))

    with patch(
        "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
        side_effect=lambda _client: _client.key_service,
    ):
        plan = inspect_managed_vm_ha_credentials(
            project_id="project-a",
            gateway_name="gateway-a",
            node_ids=("node-a", "node-b"),
            home=tmp_path,
            preflight=preflight,
            iam_inspector=_iam_inspector,
            client_factory=lambda *_args: (client, True),
        )

    _write_credential(source)
    identity_ensurer = Mock()
    with pytest.raises(VMHAManagedCredentialError, match="changed after approval"):
        ensure_managed_vm_ha_credentials(
            plan,
            node_ids=("node-a", "node-b"),
            tenant_id=None,
            region_id="eu-north1",
            identity_ensurer=identity_ensurer,
            client_factory=lambda *_args: (client, True),
            preflight=preflight,
        )
    identity_ensurer.assert_not_called()


def test_cleanup_failure_resumes_from_one_valid_journal_survivor(
    tmp_path: Path,
) -> None:
    created_plan, key_service, client, preflight = _create_managed_credential(tmp_path)
    source = created_plan.source_path
    key_item = _key_item_from_credential(source)
    private_pem = json.loads(source.read_text(encoding="utf-8"))["subject-credentials"][
        "private-key"
    ].encode("ascii")
    pending = source.parent / ".private-key.pending.pem"
    journal = source.parent / ".enrollment.json"
    pending.write_bytes(private_pem)
    pending.chmod(0o600)
    journal.write_text(
        json.dumps(
            {
                "authorized_key_id": "auth-public-key-a",
                "authorized_key_name": "gateway-a-vm-ha-runtime-key",
                "gateway_name": "gateway-a",
                "project_id": "project-a",
                "public_key_sha256": hashlib.sha256(key_item.spec.data.encode("ascii")).hexdigest(),
                "service_account_id": "service-account-a",
                "service_account_name": "gateway-a-vm-ha",
                "version": 1,
            }
        ),
        encoding="utf-8",
    )
    journal.chmod(0o600)

    with patch(
        "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
        side_effect=lambda _client: _client.key_service,
    ):
        resume = inspect_managed_vm_ha_credentials(
            project_id="project-a",
            gateway_name="gateway-a",
            node_ids=("node-a", "node-b"),
            home=tmp_path,
            preflight=preflight,
            iam_inspector=_iam_inspector,
            client_factory=lambda *_args: (client, True),
        )
    assert resume.action == "resume"

    original_unlink = Path.unlink

    def fail_journal_cleanup(path: Path, *args, **kwargs):
        if path == journal:
            raise OSError("private provider path must not escape")
        return original_unlink(path, *args, **kwargs)

    with (
        patch(
            "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
            side_effect=lambda _client: _client.key_service,
        ),
        patch.object(Path, "unlink", fail_journal_cleanup),
        pytest.raises(VMHAManagedCredentialError, match="cleanup failed"),
    ):
        ensure_managed_vm_ha_credentials(
            resume,
            node_ids=("node-a", "node-b"),
            tenant_id=None,
            region_id="eu-north1",
            identity_ensurer=_identity_ensurer,
            client_factory=lambda *_args: (client, True),
            preflight=preflight,
        )
    assert source.exists()
    assert journal.exists()
    assert not pending.exists()

    with patch(
        "nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient",
        side_effect=lambda _client: _client.key_service,
    ):
        survivor = inspect_managed_vm_ha_credentials(
            project_id="project-a",
            gateway_name="gateway-a",
            node_ids=("node-a", "node-b"),
            home=tmp_path,
            preflight=preflight,
            iam_inspector=_iam_inspector,
            client_factory=lambda *_args: (client, True),
        )
        ensure_managed_vm_ha_credentials(
            survivor,
            node_ids=("node-a", "node-b"),
            tenant_id=None,
            region_id="eu-north1",
            identity_ensurer=_identity_ensurer,
            client_factory=lambda *_args: (client, True),
            preflight=preflight,
        )
    assert not journal.exists()
    assert len(key_service.create_requests) == 1


def test_atomic_no_clobber_preserves_racing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "credential.json"

    def collide(_source, target, **_kwargs):
        Path(target).write_bytes(b"winner")
        raise FileExistsError

    with (
        patch.object(managed, "_atomic_rename_noreplace", side_effect=collide),
        pytest.raises(VMHAManagedCredentialError, match="overwrite state"),
    ):
        managed._atomic_private_write(destination, b"loser", replace=False)

    assert destination.read_bytes() == b"winner"


def test_filesystem_failure_is_normalized_without_operator_path(tmp_path: Path) -> None:
    destination = tmp_path / "private" / "credential.json"
    destination.parent.mkdir()

    with (
        patch.object(managed.os, "open", side_effect=OSError("disk full")),
        pytest.raises(VMHAManagedCredentialError) as captured,
    ):
        managed._atomic_private_write(destination, b"payload", replace=False)

    assert str(tmp_path) not in str(captured.value)
