"""Nebius IAM bootstrap helpers for CI service-account authentication."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .credential_compensation import (
    CredentialCompensationError,
    CredentialCompensationJournal,
    CredentialDeliveryAdapter,
    CredentialDeliveryDisposition,
)
from .sdk_auth import init_nebius_sdk, suppress_deleted_key_refresh_logs


class CredentialProviderError(RuntimeError):
    """A stable, secret-free IAM credential provider failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"{code}: Nebius IAM credential operation failed")


def _credential_provider_result[Result](
    code: str,
    invoke: Callable[[], Result],
) -> Result:
    """Run one secret-bearing provider call behind a sanitized error boundary."""

    try:
        return invoke()
    except Exception:
        raise CredentialProviderError(code) from None


@dataclass(frozen=True)
class CIBootstrapResult:
    """Result values needed to configure CI secrets."""

    project_id: str
    service_account_name: str
    service_account_id: str
    service_account_created: bool
    roles_created: list[str]
    roles_already_present: list[str]
    auth_public_key_id: str
    auth_private_key_pem: str = field(repr=False)
    s3_access_key_id: str
    s3_secret_access_key: str = field(repr=False)


@dataclass(frozen=True)
class ServiceAccountAuthKeyResult:
    """Result values for a service-account auth key without Object Storage keys."""

    project_id: str
    service_account_name: str
    service_account_id: str
    service_account_created: bool
    roles_created: list[str]
    roles_already_present: list[str]
    auth_public_key_id: str
    auth_private_key_pem: str = field(repr=False)


@dataclass(frozen=True)
class ObjectStorageAccessKeyResult:
    """One-time Object Storage access-key material for an existing service account."""

    project_id: str
    service_account_id: str
    s3_access_key_id: str
    s3_secret_access_key: str = field(repr=False)


@dataclass(frozen=True)
class CIIdentityEnsureResult:
    """Idempotent identity state for CI automation."""

    project_id: str
    service_account_name: str
    service_account_id: str
    service_account_created: bool
    roles_created: list[str]
    roles_already_present: list[str]


@dataclass(frozen=True)
class StaticKeyIssueResult:
    """One-time static key material for an IAM service account."""

    project_id: str
    service_account_name: str
    service_account_id: str
    service_account_created: bool
    roles_created: list[str]
    roles_already_present: list[str]
    static_key_id: str
    token: str = field(repr=False)


def _close_sdk(sdk: object) -> None:
    close = getattr(sdk, "sync_close", None)
    if not callable(close):
        return
    with suppress(Exception):
        close()


def _is_not_found_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "not found" in message or "statuscode.not_found" in message


def _is_already_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "statuscode.already_exists" in message


def _normalize_role_id(role_id: str) -> str:
    """Normalize common IAM role syntaxes to Nebius access-permit role IDs."""
    token = role_id.strip()
    if not token:
        return ""
    if token.startswith("roles/"):
        token = token.split("/", 1)[1]
    return token


def _init_sdk(
    *,
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
    prefer_operator_auth: bool = False,
    allow_cli_token: bool = True,
):
    return init_nebius_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        context="auth bootstrap",
        prefer_operator_auth=prefer_operator_auth,
        allow_cli_token=allow_cli_token,
    )


def _account_ref(service_account_id: str):
    from nebius.api.nebius.iam.v1 import Account

    return Account(service_account=Account.ServiceAccount(id=service_account_id))


def _ensure_service_account(
    *,
    service_accounts,
    project_id: str,
    service_account_name: str,
    service_account_description: str,
    strict_description: bool = False,
    create_missing: bool = True,
) -> tuple[str, bool]:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import (
        CreateServiceAccountRequest,
        GetServiceAccountByNameRequest,
        ServiceAccountSpec,
    )

    try:
        existing = service_accounts.get_by_name(
            GetServiceAccountByNameRequest(parent_id=project_id, name=service_account_name)
        ).wait()
        existing_id = getattr(getattr(existing, "metadata", None), "id", "")
        if existing_id:
            existing_description = str(
                getattr(getattr(existing, "spec", None), "description", "") or ""
            )
            if strict_description and existing_description != service_account_description:
                raise RuntimeError(
                    f"Service account '{service_account_name}' already exists but is not "
                    "the cxcli-managed identity (description mismatch)."
                )
            return existing_id, False
    except Exception as exc:
        if not _is_not_found_error(exc):
            raise RuntimeError(
                f"Failed to fetch service account '{service_account_name}': {exc}"
            ) from exc

    if not create_missing:
        raise RuntimeError(
            f"Service account '{service_account_name}' is missing; read-only identity "
            "validation cannot create it."
        )

    try:
        operation = service_accounts.create(
            CreateServiceAccountRequest(
                metadata=ResourceMetadata(parent_id=project_id, name=service_account_name),
                spec=ServiceAccountSpec(description=service_account_description),
            )
        ).wait()
    except Exception as exc:
        if _is_already_exists_error(exc):
            existing = service_accounts.get_by_name(
                GetServiceAccountByNameRequest(parent_id=project_id, name=service_account_name)
            ).wait()
            existing_id = getattr(getattr(existing, "metadata", None), "id", "")
            if existing_id:
                existing_description = str(
                    getattr(getattr(existing, "spec", None), "description", "") or ""
                )
                if strict_description and existing_description != service_account_description:
                    raise RuntimeError(
                        f"Service account '{service_account_name}' already exists but is not "
                        "the cxcli-managed identity (description mismatch)."
                    ) from exc
                return existing_id, False
        raise RuntimeError(
            f"Failed to create service account '{service_account_name}': {exc}"
        ) from exc

    service_account_id = getattr(operation, "resource_id", "")
    if service_account_id:
        return service_account_id, True

    existing = service_accounts.get_by_name(
        GetServiceAccountByNameRequest(parent_id=project_id, name=service_account_name)
    ).wait()
    existing_id = getattr(getattr(existing, "metadata", None), "id", "")
    if existing_id:
        existing_description = str(
            getattr(getattr(existing, "spec", None), "description", "") or ""
        )
        if strict_description and existing_description != service_account_description:
            raise RuntimeError(
                f"Service account '{service_account_name}' was created concurrently but is not "
                "the cxcli-managed identity (description mismatch)."
            )
        return existing_id, True

    raise RuntimeError(
        f"Service account '{service_account_name}' was created but its ID could not be resolved"
    )


def _ensure_project_role_permits(
    *,
    access_permits,
    permit_parent_id: str,
    principal_label: str,
    project_id: str,
    role_ids: list[str],
    reject_unexpected_role_ids: bool = False,
    create_missing: bool = True,
) -> tuple[list[str], list[str]]:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import (
        AccessPermitSpec,
        CreateAccessPermitRequest,
        ListAccessPermitRequest,
    )

    existing_roles: set[str] = set()
    unexpected_resource_permits: set[tuple[str, str]] = set()
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        response = access_permits.list(
            ListAccessPermitRequest(parent_id=permit_parent_id, page_token=page_token)
        ).wait()
        for item in list(getattr(response, "items", [])):
            spec = getattr(item, "spec", None)
            if spec is None:
                continue
            resource_id = str(getattr(spec, "resource_id", "") or "").strip()
            role = _normalize_role_id(getattr(spec, "role", ""))
            if role:
                if resource_id == project_id:
                    existing_roles.add(role)
                else:
                    unexpected_resource_permits.add((resource_id, role))
        page_token = getattr(response, "next_page_token", "") or None
        if not page_token:
            break
        if page_token in seen_tokens:
            raise RuntimeError(
                "IAM access permit listing received a repeated pagination token from "
                "the Nebius API; aborting to avoid an infinite list loop."
            )
        seen_tokens.add(page_token)

    expected_roles = {_normalize_role_id(role_id) for role_id in role_ids}
    expected_roles.discard("")
    if reject_unexpected_role_ids and unexpected_resource_permits:
        raise RuntimeError(
            f"{principal_label} has unexpected resource-scoped access permits outside "
            f"the canonical project ({len(unexpected_resource_permits)} found)"
        )
    unexpected_roles = sorted(existing_roles - expected_roles)
    if reject_unexpected_role_ids and unexpected_roles:
        raise RuntimeError(
            f"{principal_label} has unexpected project role permits: " + ", ".join(unexpected_roles)
        )

    missing_roles = sorted(expected_roles - existing_roles)
    if missing_roles and not create_missing:
        raise RuntimeError(
            f"{principal_label} is missing required project role permits: "
            + ", ".join(missing_roles)
        )

    created: list[str] = []
    already_present: list[str] = []
    for role_id_raw in role_ids:
        role_id = _normalize_role_id(role_id_raw)
        if not role_id:
            continue
        if role_id in existing_roles:
            already_present.append(role_id)
            continue
        try:
            access_permits.create(
                CreateAccessPermitRequest(
                    metadata=ResourceMetadata(parent_id=permit_parent_id),
                    spec=AccessPermitSpec(resource_id=project_id, role=role_id),
                )
            ).wait()
            created.append(role_id)
        except Exception as exc:
            if _is_already_exists_error(exc):
                already_present.append(role_id)
                continue
            raise RuntimeError(
                f"Failed to grant role '{role_id}' to {principal_label}: {exc}"
            ) from exc

    return created, already_present


def _group_name_for_service_account(service_account_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", service_account_name.lower()).strip("-")
    if not normalized:
        normalized = "nebius-cxcli-sa"
    suffix = "permits"
    candidate = f"{normalized}-{suffix}"
    if len(candidate) <= 63:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:8]
    max_prefix = max(1, 63 - len(suffix) - len(digest) - 2)
    prefix = normalized[:max_prefix].rstrip("-") or "sa"
    return f"{prefix}-{suffix}-{digest}"


def _ensure_group(
    *,
    groups,
    project_id: str,
    group_name: str,
    create_missing: bool = True,
) -> tuple[str, bool]:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import CreateGroupRequest, GetGroupByNameRequest, GroupSpec

    try:
        existing = groups.get_by_name(
            GetGroupByNameRequest(parent_id=project_id, name=group_name)
        ).wait()
        existing_id = getattr(getattr(existing, "metadata", None), "id", "")
        if existing_id:
            return existing_id, False
    except Exception as exc:
        if not _is_not_found_error(exc):
            raise RuntimeError(f"Failed to fetch IAM group '{group_name}': {exc}") from exc

    if not create_missing:
        raise RuntimeError(
            f"IAM group '{group_name}' is missing; read-only identity validation cannot create it."
        )

    try:
        operation = groups.create(
            CreateGroupRequest(
                metadata=ResourceMetadata(parent_id=project_id, name=group_name),
                spec=GroupSpec(),
            )
        ).wait()
    except Exception as exc:
        if _is_already_exists_error(exc):
            existing = groups.get_by_name(
                GetGroupByNameRequest(parent_id=project_id, name=group_name)
            ).wait()
            existing_id = getattr(getattr(existing, "metadata", None), "id", "")
            if existing_id:
                return existing_id, False
        raise RuntimeError(f"Failed to create IAM group '{group_name}': {exc}") from exc

    group_id = getattr(operation, "resource_id", "")
    if group_id:
        return group_id, True

    existing = groups.get_by_name(
        GetGroupByNameRequest(parent_id=project_id, name=group_name)
    ).wait()
    existing_id = getattr(getattr(existing, "metadata", None), "id", "")
    if existing_id:
        return existing_id, True
    raise RuntimeError(f"IAM group '{group_name}' was created but its ID could not be resolved")


def _group_member_ids(
    *,
    group_memberships,
    group_id: str,
) -> set[str]:
    from nebius.api.nebius.iam.v1 import ListGroupMembershipsRequest

    members: set[str] = set()
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        response = group_memberships.list_members(
            ListGroupMembershipsRequest(parent_id=group_id, page_token=page_token)
        ).wait()
        for membership in list(getattr(response, "memberships", [])):
            spec = getattr(membership, "spec", None)
            if spec is None:
                continue
            member_id = getattr(spec, "member_id", "")
            if member_id:
                members.add(member_id)
        page_token = getattr(response, "next_page_token", "") or None
        if not page_token:
            break
        if page_token in seen_tokens:
            raise RuntimeError(
                "IAM group membership listing received a repeated pagination token from "
                "the Nebius API; aborting to avoid an infinite list loop."
            )
        seen_tokens.add(page_token)
    return members


def _ensure_group_membership(
    *,
    group_memberships,
    group_id: str,
    member_id: str,
) -> bool:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import CreateGroupMembershipRequest, GroupMembershipSpec

    members = _group_member_ids(group_memberships=group_memberships, group_id=group_id)
    if member_id in members:
        return False

    try:
        group_memberships.create(
            CreateGroupMembershipRequest(
                metadata=ResourceMetadata(parent_id=group_id),
                spec=GroupMembershipSpec(member_id=member_id),
            )
        ).wait()
        return True
    except Exception as exc:
        if _is_already_exists_error(exc):
            return False
        raise RuntimeError(
            f"Failed to add member '{member_id}' to IAM group '{group_id}': {exc}"
        ) from exc


def generate_service_account_auth_key_pair() -> tuple[str, str]:
    """Generate private/public PEM values for a recoverable authorized-key intent."""

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except Exception as exc:  # pragma: no cover - runtime integration
        raise RuntimeError(
            "cryptography package is required to generate authorized key pairs for IAM bootstrap"
        ) from exc

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


def _upload_auth_public_key(
    *,
    auth_keys,
    project_id: str,
    service_account_id: str,
    description: str,
    public_key_pem: str,
) -> str:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import AuthPublicKeySpec, CreateAuthPublicKeyRequest

    operation = _credential_provider_result(
        "iam-auth-public-key-create-failed",
        lambda: auth_keys.create(
            CreateAuthPublicKeyRequest(
                metadata=ResourceMetadata(parent_id=project_id),
                spec=AuthPublicKeySpec(
                    account=_account_ref(service_account_id),
                    description=description,
                    data=public_key_pem,
                ),
            )
        ).wait(),
    )

    auth_public_key_id = getattr(operation, "resource_id", "")
    if not auth_public_key_id:
        raise RuntimeError("Auth public key was created but key ID could not be resolved")

    return auth_public_key_id


def _create_auth_public_key(
    *,
    auth_keys,
    project_id: str,
    service_account_id: str,
    description: str,
) -> tuple[str, str]:
    private_pem, public_pem = generate_service_account_auth_key_pair()
    auth_public_key_id = _upload_auth_public_key(
        auth_keys=auth_keys,
        project_id=project_id,
        service_account_id=service_account_id,
        description=description,
        public_key_pem=public_pem,
    )
    return auth_public_key_id, private_pem


def _create_object_storage_access_key(
    *,
    access_keys,
    project_id: str,
    service_account_id: str,
    description: str,
) -> tuple[str, str, str]:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v2 import (
        AccessKeySpec,
        CreateAccessKeyRequest,
        GetAccessKeyRequest,
        GetAccessKeySecretRequest,
    )

    operation = _credential_provider_result(
        "iam-access-key-create-failed",
        lambda: access_keys.create(
            CreateAccessKeyRequest(
                metadata=ResourceMetadata(parent_id=project_id),
                spec=AccessKeySpec(
                    account=_account_ref(service_account_id), description=description
                ),
            )
        ).wait(),
    )

    access_key_id = getattr(operation, "resource_id", "")
    if not access_key_id:
        raise RuntimeError("Access key was created but key ID could not be resolved")

    secret_response = _credential_provider_result(
        "iam-access-key-secret-read-failed",
        lambda: access_keys.get_secret(GetAccessKeySecretRequest(id=access_key_id)).wait(),
    )

    aws_access_key_id = getattr(secret_response, "aws_access_key_id", "")
    s3_secret_access_key = getattr(secret_response, "secret", "")

    if not aws_access_key_id:
        # Fallback for SDK variants where aws_access_key_id is absent on GetAccessKeySecret response.
        key = _credential_provider_result(
            "iam-access-key-read-failed",
            lambda: access_keys.get(GetAccessKeyRequest(id=access_key_id)).wait(),
        )
        aws_access_key_id = getattr(getattr(key, "status", None), "aws_access_key_id", "")

    if not aws_access_key_id or not s3_secret_access_key:
        raise RuntimeError(
            "Access key secret response did not include expected values (aws_access_key_id/secret)"
        )

    return access_key_id, aws_access_key_id, s3_secret_access_key


def _credential_description(base: str, operation_id: str) -> str:
    return f"{base.strip()} [cxcli-op:{operation_id}]"


def _credential_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _credential_name(base: str, operation_id: str) -> str:
    suffix = f"-cxcli-{operation_id[:12]}"
    normalized = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-") or "credential"
    return normalized[: 63 - len(suffix)].rstrip("-") + suffix


def _credential_metadata_id(item: object) -> str:
    return str(getattr(getattr(item, "metadata", None), "id", "") or "").strip()


def _operation_owned_credential_ids(
    *,
    kind: str,
    service_account_id: str,
    operation_id: str,
    project_id: str,
    auth_keys: object | None,
    access_keys: object | None,
    static_keys: object | None,
) -> tuple[str, ...]:
    suffix = f"[cxcli-op:{operation_id}]"
    matches: list[str] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        if kind == "auth-public-key":
            if auth_keys is None:
                raise RuntimeError("authorized-key compensation client is unavailable")
            from nebius.api.nebius.iam.v1 import ListAuthPublicKeyByAccountRequest

            try:
                response = auth_keys.list_by_account(  # type: ignore[attr-defined]
                    ListAuthPublicKeyByAccountRequest(
                        account=_account_ref(service_account_id),
                        page_token=page_token,
                    )
                ).wait()
            except Exception:
                raise CredentialProviderError("iam-auth-public-key-list-failed") from None
        elif kind == "access-key":
            if access_keys is None:
                raise RuntimeError("access-key compensation client is unavailable")
            from nebius.api.nebius.iam.v2 import ListAccessKeysByAccountRequest

            try:
                response = access_keys.list_by_account(  # type: ignore[attr-defined]
                    ListAccessKeysByAccountRequest(
                        account=_account_ref(service_account_id),
                        page_token=page_token,
                    )
                ).wait()
            except Exception:
                raise CredentialProviderError("iam-access-key-list-failed") from None
        elif kind == "static-key":
            if static_keys is None:
                raise RuntimeError("static-key compensation client is unavailable")
            from nebius.api.nebius.iam.v1 import ListStaticKeysRequest

            try:
                response = static_keys.list(  # type: ignore[attr-defined]
                    ListStaticKeysRequest(parent_id=project_id, page_token=page_token)
                ).wait()
            except Exception:
                raise CredentialProviderError("iam-static-key-list-failed") from None
        else:
            raise RuntimeError(f"unsupported credential compensation kind: {kind}")

        for item in list(getattr(response, "items", [])):
            metadata = getattr(item, "metadata", None)
            spec = getattr(item, "spec", None)
            description = str(getattr(spec, "description", "") or "").strip()
            name = str(getattr(metadata, "name", "") or "").strip()
            account = getattr(spec, "account", None)
            item_service_account_id = str(
                getattr(getattr(account, "service_account", None), "id", "") or ""
            ).strip()
            owned = (
                name.endswith(f"-cxcli-{operation_id[:12]}")
                if kind == "static-key"
                else description.endswith(suffix)
            )
            resource_id = _credential_metadata_id(item)
            if owned and item_service_account_id == service_account_id and resource_id:
                matches.append(resource_id)
        page_token = str(getattr(response, "next_page_token", "") or "").strip() or None
        if not page_token:
            break
        if page_token in seen_tokens:
            raise RuntimeError("credential compensation listing repeated a pagination token")
        seen_tokens.add(page_token)
    return tuple(sorted(set(matches)))


def _delete_operation_credential(
    *,
    kind: str,
    resource_id: str,
    auth_keys: object | None,
    access_keys: object | None,
    static_keys: object | None,
) -> None:
    if kind == "auth-public-key":
        if auth_keys is None:
            raise RuntimeError("authorized-key compensation client is unavailable")
        from nebius.api.nebius.iam.v1 import DeleteAuthPublicKeyRequest

        try:
            auth_keys.delete(DeleteAuthPublicKeyRequest(id=resource_id)).wait()  # type: ignore[attr-defined]
        except Exception as exc:
            if _is_not_found_error(exc):
                return
            raise CredentialProviderError("iam-auth-public-key-delete-failed") from None
    elif kind == "access-key":
        if access_keys is None:
            raise RuntimeError("access-key compensation client is unavailable")
        from nebius.api.nebius.iam.v2 import DeleteAccessKeyRequest

        try:
            access_keys.delete(DeleteAccessKeyRequest(id=resource_id)).wait()  # type: ignore[attr-defined]
        except Exception as exc:
            if _is_not_found_error(exc):
                return
            raise CredentialProviderError("iam-access-key-delete-failed") from None
    elif kind == "static-key":
        if static_keys is None:
            raise RuntimeError("static-key compensation client is unavailable")
        from nebius.api.nebius.iam.v1 import DeleteStaticKeyRequest

        try:
            static_keys.delete(DeleteStaticKeyRequest(id=resource_id)).wait()  # type: ignore[attr-defined]
        except Exception as exc:
            if _is_not_found_error(exc):
                return
            raise CredentialProviderError("iam-static-key-delete-failed") from None
    else:
        raise RuntimeError(f"unsupported credential compensation kind: {kind}")


def _run_compensated_credential_issue[CredentialResult](
    *,
    project_id: str,
    scope: str,
    auth_keys: object | None,
    access_keys: object | None,
    static_keys: object | None,
    create: Callable[[CredentialCompensationJournal, str], CredentialResult],
    delivery: CredentialDeliveryAdapter[CredentialResult],
) -> CredentialResult:
    journal = CredentialCompensationJournal(project_id=project_id, scope=scope)

    def _delete(kind: str, resource_id: str) -> None:
        _delete_operation_credential(
            kind=kind,
            resource_id=resource_id,
            auth_keys=auth_keys,
            access_keys=access_keys,
            static_keys=static_keys,
        )

    def _resolve(item: Mapping[str, Any], operation_id: str) -> tuple[str, ...]:
        return _operation_owned_credential_ids(
            kind=str(item["kind"]),
            service_account_id=str(item["serviceAccountId"]),
            operation_id=operation_id,
            project_id=project_id,
            auth_keys=auth_keys,
            access_keys=access_keys,
            static_keys=static_keys,
        )

    with journal.locked():
        journal.recover(delete=_delete, resolve=_resolve, delivery=delivery)
        operation_id = journal.begin()
        delivery_started = False
        try:
            result = create(journal, operation_id)
            intent = delivery.prepare(
                result,
                operation_id=operation_id,
                credentials=journal.created_credentials(),
            )
            journal.record_delivery_intent(intent)
            delivery_started = True
            disposition = delivery.deliver(result, intent)
        except BaseException:
            if journal.payload is not None:
                if delivery_started:
                    journal.mark_delivery_uncertain()
                else:
                    journal.recover(delete=_delete, resolve=_resolve, delivery=delivery)
            raise
        if disposition is CredentialDeliveryDisposition.DELIVERED:
            journal.mark_delivered()
            return result
        if disposition is CredentialDeliveryDisposition.NOT_DELIVERED:
            journal.compensate(delete=_delete)
            raise CredentialCompensationError(
                "credential destination proved that delivery did not complete"
            )
        if disposition is CredentialDeliveryDisposition.AMBIGUOUS:
            journal.mark_delivery_uncertain()
            raise CredentialCompensationError(
                "credential delivery remains ambiguous; created credentials were preserved"
            )
        journal.mark_delivery_uncertain()
        raise CredentialCompensationError(
            "credential delivery returned an invalid disposition; created credentials were preserved"
        )


def create_service_account_auth_public_key(
    *,
    project_id: str,
    service_account_id: str,
    public_key_pem: str,
    description: str,
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
    allow_cli_token: bool = True,
) -> str:
    """Upload a caller-owned public key and return its Nebius resource ID."""

    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
        allow_cli_token=allow_cli_token,
    )
    try:
        from nebius.api.nebius.iam.v1 import AuthPublicKeyServiceClient

        return _upload_auth_public_key(
            auth_keys=AuthPublicKeyServiceClient(sdk),
            project_id=project_id,
            service_account_id=service_account_id,
            description=description,
            public_key_pem=public_key_pem,
        )
    finally:
        _close_sdk(sdk)


def service_account_auth_public_key_ids_by_description(
    *,
    project_id: str,
    service_account_id: str,
    description: str,
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
    allow_cli_token: bool = True,
) -> tuple[str, ...]:
    """Return exact authorized-key IDs for one account/description pair."""

    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
        allow_cli_token=allow_cli_token,
    )
    try:
        from nebius.api.nebius.iam.v1 import (
            AuthPublicKeyServiceClient,
            ListAuthPublicKeyRequest,
        )

        auth_keys = AuthPublicKeyServiceClient(sdk)
        matches: list[str] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            response = auth_keys.list(
                ListAuthPublicKeyRequest(parent_id=project_id, page_token=page_token)
            ).wait()
            for item in list(getattr(response, "items", [])):
                spec = getattr(item, "spec", None)
                metadata = getattr(item, "metadata", None)
                account = getattr(spec, "account", None)
                account_id = getattr(getattr(account, "service_account", None), "id", "")
                key_description = str(getattr(spec, "description", "") or "")
                key_id = str(getattr(metadata, "id", "") or "")
                if account_id == service_account_id and key_description == description and key_id:
                    matches.append(key_id)
            page_token = getattr(response, "next_page_token", "") or None
            if not page_token:
                break
            if page_token in seen_tokens:
                raise RuntimeError(
                    "IAM auth public key listing received a repeated pagination token from "
                    "the Nebius API; aborting to avoid an infinite list loop."
                )
            seen_tokens.add(page_token)
        return tuple(sorted(set(matches)))
    finally:
        _close_sdk(sdk)


def issue_service_account_object_storage_access_key(
    *,
    project_id: str,
    service_account_id: str,
    description: str,
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
    compensation_scope: str,
    delivery: CredentialDeliveryAdapter[ObjectStorageAccessKeyResult],
    allow_cli_token: bool = False,
) -> ObjectStorageAccessKeyResult:
    """Issue one Object Storage key for an existing service account."""

    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=False,
        allow_cli_token=allow_cli_token,
    )
    try:
        from nebius.api.nebius.iam.v2 import AccessKeyServiceClient

        access_keys = AccessKeyServiceClient(sdk)

        def _create(
            journal: CredentialCompensationJournal, operation_id: str
        ) -> ObjectStorageAccessKeyResult:
            owned_description = _credential_description(description, operation_id)
            journal.record_intent(
                kind="access-key",
                ownership_sha256=_credential_digest(owned_description),
                service_account_id=service_account_id,
            )
            resource_id, access_key_id, secret_access_key = _create_object_storage_access_key(
                access_keys=access_keys,
                project_id=project_id,
                service_account_id=service_account_id,
                description=owned_description,
            )
            journal.record_created(kind="access-key", resource_id=resource_id)
            return ObjectStorageAccessKeyResult(
                project_id=project_id,
                service_account_id=service_account_id,
                s3_access_key_id=access_key_id,
                s3_secret_access_key=secret_access_key,
            )

        return _run_compensated_credential_issue(
            project_id=project_id,
            scope=compensation_scope,
            auth_keys=None,
            access_keys=access_keys,
            static_keys=None,
            create=_create,
            delivery=delivery,
        )
    finally:
        _close_sdk(sdk)


def bootstrap_ci_service_account(
    *,
    project_id: str,
    service_account_name: str,
    service_account_description: str,
    role_ids: list[str],
    auth_key_description: str,
    access_key_description: str,
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
    compensation_scope: str,
    delivery: CredentialDeliveryAdapter[CIBootstrapResult],
) -> CIBootstrapResult:
    """Ensure SA + role grants and create auth/access keys for CI usage."""

    identity = ensure_ci_service_account_identity(
        project_id=project_id,
        service_account_name=service_account_name,
        service_account_description=service_account_description,
        role_ids=role_ids,
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
        allow_mutation=True,
    )

    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
    )
    try:
        from nebius.api.nebius.iam.v1 import AuthPublicKeyServiceClient
        from nebius.api.nebius.iam.v2 import AccessKeyServiceClient

        auth_keys = AuthPublicKeyServiceClient(sdk)
        access_keys = AccessKeyServiceClient(sdk)

        def _create(journal: CredentialCompensationJournal, operation_id: str) -> CIBootstrapResult:
            owned_auth_description = _credential_description(auth_key_description, operation_id)
            journal.record_intent(
                kind="auth-public-key",
                ownership_sha256=_credential_digest(owned_auth_description),
                service_account_id=identity.service_account_id,
            )
            auth_public_key_id, auth_private_key_pem = _create_auth_public_key(
                auth_keys=auth_keys,
                project_id=identity.project_id,
                service_account_id=identity.service_account_id,
                description=owned_auth_description,
            )
            journal.record_created(kind="auth-public-key", resource_id=auth_public_key_id)

            owned_access_description = _credential_description(access_key_description, operation_id)
            journal.record_intent(
                kind="access-key",
                ownership_sha256=_credential_digest(owned_access_description),
                service_account_id=identity.service_account_id,
            )
            access_key_resource_id, s3_access_key_id, s3_secret_access_key = (
                _create_object_storage_access_key(
                    access_keys=access_keys,
                    project_id=identity.project_id,
                    service_account_id=identity.service_account_id,
                    description=owned_access_description,
                )
            )
            journal.record_created(kind="access-key", resource_id=access_key_resource_id)
            return CIBootstrapResult(
                project_id=identity.project_id,
                service_account_name=identity.service_account_name,
                service_account_id=identity.service_account_id,
                service_account_created=identity.service_account_created,
                roles_created=identity.roles_created,
                roles_already_present=identity.roles_already_present,
                auth_public_key_id=auth_public_key_id,
                auth_private_key_pem=auth_private_key_pem,
                s3_access_key_id=s3_access_key_id,
                s3_secret_access_key=s3_secret_access_key,
            )

        return _run_compensated_credential_issue(
            project_id=identity.project_id,
            scope=compensation_scope,
            auth_keys=auth_keys,
            access_keys=access_keys,
            static_keys=None,
            create=_create,
            delivery=delivery,
        )
    finally:
        _close_sdk(sdk)


def bootstrap_service_account_auth_key(
    *,
    project_id: str,
    service_account_name: str,
    service_account_description: str,
    role_ids: list[str],
    auth_key_description: str,
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
    compensation_scope: str,
    delivery: CredentialDeliveryAdapter[ServiceAccountAuthKeyResult],
    allow_cli_token: bool = False,
) -> ServiceAccountAuthKeyResult:
    """Ensure SA + role grants and create an auth key for non-S3 runtime usage."""

    identity = ensure_ci_service_account_identity(
        project_id=project_id,
        service_account_name=service_account_name,
        service_account_description=service_account_description,
        role_ids=role_ids,
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        allow_cli_token=allow_cli_token,
        prefer_operator_auth=True,
        allow_mutation=True,
    )

    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
        allow_cli_token=allow_cli_token,
    )
    try:
        from nebius.api.nebius.iam.v1 import AuthPublicKeyServiceClient

        auth_keys = AuthPublicKeyServiceClient(sdk)

        def _create(
            journal: CredentialCompensationJournal, operation_id: str
        ) -> ServiceAccountAuthKeyResult:
            owned_description = _credential_description(auth_key_description, operation_id)
            journal.record_intent(
                kind="auth-public-key",
                ownership_sha256=_credential_digest(owned_description),
                service_account_id=identity.service_account_id,
            )
            auth_public_key_id, auth_private_key_pem = _create_auth_public_key(
                auth_keys=auth_keys,
                project_id=identity.project_id,
                service_account_id=identity.service_account_id,
                description=owned_description,
            )
            journal.record_created(kind="auth-public-key", resource_id=auth_public_key_id)
            return ServiceAccountAuthKeyResult(
                project_id=identity.project_id,
                service_account_name=identity.service_account_name,
                service_account_id=identity.service_account_id,
                service_account_created=identity.service_account_created,
                roles_created=identity.roles_created,
                roles_already_present=identity.roles_already_present,
                auth_public_key_id=auth_public_key_id,
                auth_private_key_pem=auth_private_key_pem,
            )

        return _run_compensated_credential_issue(
            project_id=identity.project_id,
            scope=compensation_scope,
            auth_keys=auth_keys,
            access_keys=None,
            static_keys=None,
            create=_create,
            delivery=delivery,
        )
    finally:
        _close_sdk(sdk)


def issue_observability_static_key(
    *,
    project_id: str,
    service_account_name: str,
    service_account_description: str,
    key_name: str,
    role_ids: list[str],
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
    compensation_scope: str,
    delivery: CredentialDeliveryAdapter[StaticKeyIssueResult],
) -> StaticKeyIssueResult:
    """Ensure a viewer identity and issue a one-time Observability static key."""
    identity = ensure_ci_service_account_identity(
        project_id=project_id,
        service_account_name=service_account_name,
        service_account_description=service_account_description,
        role_ids=role_ids,
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
        allow_mutation=True,
    )
    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
    )
    try:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.iam.v1 import (
            IssueStaticKeyRequest,
            StaticKeyServiceClient,
            StaticKeySpec,
        )

        static_keys = StaticKeyServiceClient(sdk)

        def _create(
            journal: CredentialCompensationJournal, operation_id: str
        ) -> StaticKeyIssueResult:
            owned_name = _credential_name(key_name, operation_id)
            journal.record_intent(
                kind="static-key",
                ownership_sha256=_credential_digest(owned_name),
                service_account_id=identity.service_account_id,
            )
            response = _credential_provider_result(
                "iam-static-key-issue-failed",
                lambda: static_keys.issue(
                    IssueStaticKeyRequest(
                        metadata=ResourceMetadata(parent_id=project_id, name=owned_name),
                        spec=StaticKeySpec(
                            account=_account_ref(identity.service_account_id),
                            service=StaticKeySpec.ClientService.OBSERVABILITY,
                        ),
                    )
                ).wait(),
            )
            operation = getattr(response, "operation", None)
            static_key_id = getattr(operation, "resource_id", "")
            token = getattr(response, "token", "")
            if not static_key_id:
                raise RuntimeError(
                    "Observability static key was issued but key ID was not returned"
                )
            journal.record_created(kind="static-key", resource_id=static_key_id)
            if not token:
                raise RuntimeError("Observability static key was issued but token was not returned")
            return StaticKeyIssueResult(
                project_id=identity.project_id,
                service_account_name=identity.service_account_name,
                service_account_id=identity.service_account_id,
                service_account_created=identity.service_account_created,
                roles_created=identity.roles_created,
                roles_already_present=identity.roles_already_present,
                static_key_id=static_key_id,
                token=token,
            )

        return _run_compensated_credential_issue(
            project_id=identity.project_id,
            scope=compensation_scope,
            auth_keys=None,
            access_keys=None,
            static_keys=static_keys,
            create=_create,
            delivery=delivery,
        )
    finally:
        _close_sdk(sdk)


def delete_observability_static_key(
    *,
    static_key_id: str,
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
) -> None:
    """Delete one operation-scoped Observability static key by immutable ID."""

    normalized_id = str(static_key_id or "").strip()
    if not normalized_id:
        raise ValueError("static_key_id is required")
    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
    )
    try:
        from nebius.api.nebius.iam.v1 import DeleteStaticKeyRequest, StaticKeyServiceClient

        StaticKeyServiceClient(sdk).delete(DeleteStaticKeyRequest(id=normalized_id)).wait()
    except Exception as exc:
        if _is_not_found_error(exc):
            return
        raise CredentialProviderError("iam-static-key-delete-failed") from None
    finally:
        _close_sdk(sdk)


def ensure_ci_service_account_identity(
    *,
    project_id: str,
    service_account_name: str,
    service_account_description: str,
    role_ids: list[str],
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
    prefer_operator_auth: bool,
    allow_mutation: bool,
    allow_cli_token: bool = True,
    strict_managed_identity: bool = False,
) -> CIIdentityEnsureResult:
    """Validate or ensure service-account identity and roles without creating keys."""
    if not role_ids:
        raise ValueError("role_ids must not be empty")

    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=prefer_operator_auth,
        allow_cli_token=allow_cli_token,
    )
    try:
        from nebius.api.nebius.iam.v1 import (
            AccessPermitServiceClient,
            GroupMembershipServiceClient,
            GroupServiceClient,
            ServiceAccountServiceClient,
        )

        service_accounts = ServiceAccountServiceClient(sdk)
        groups = GroupServiceClient(sdk)
        group_memberships = GroupMembershipServiceClient(sdk)
        access_permits = AccessPermitServiceClient(sdk)

        service_account_id, service_account_created = _ensure_service_account(
            service_accounts=service_accounts,
            project_id=project_id,
            service_account_name=service_account_name,
            service_account_description=service_account_description,
            strict_description=strict_managed_identity,
            create_missing=allow_mutation,
        )

        permit_group_name = _group_name_for_service_account(service_account_name)
        permit_group_id, _ = _ensure_group(
            groups=groups,
            project_id=project_id,
            group_name=permit_group_name,
            create_missing=allow_mutation,
        )
        existing_members = _group_member_ids(
            group_memberships=group_memberships,
            group_id=permit_group_id,
        )
        unexpected_members = sorted(existing_members - {service_account_id})
        if strict_managed_identity and unexpected_members:
            raise RuntimeError(
                f"IAM group '{permit_group_name}' contains unexpected members; "
                "refusing to reuse it for the canonical cxcli identity."
            )
        if service_account_id not in existing_members and not allow_mutation:
            raise RuntimeError(
                f"IAM group '{permit_group_name}' is missing the canonical service-account "
                "member; read-only identity validation cannot add it."
            )
        if allow_mutation:
            _ensure_group_membership(
                group_memberships=group_memberships,
                group_id=permit_group_id,
                member_id=service_account_id,
            )

        roles_created, roles_already_present = _ensure_project_role_permits(
            access_permits=access_permits,
            permit_parent_id=permit_group_id,
            principal_label=f"IAM group '{permit_group_name}'",
            project_id=project_id,
            role_ids=role_ids,
            reject_unexpected_role_ids=strict_managed_identity,
            create_missing=allow_mutation,
        )

        return CIIdentityEnsureResult(
            project_id=project_id,
            service_account_name=service_account_name,
            service_account_id=service_account_id,
            service_account_created=service_account_created,
            roles_created=roles_created,
            roles_already_present=roles_already_present,
        )
    finally:
        _close_sdk(sdk)


def auth_public_key_exists(
    *,
    auth_public_key_id: str,
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
) -> bool:
    """Return true when IAM auth public key exists and is readable."""
    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
    )
    try:
        from nebius.api.nebius.iam.v1 import AuthPublicKeyServiceClient, GetAuthPublicKeyRequest

        auth_keys = AuthPublicKeyServiceClient(sdk)
        with suppress_deleted_key_refresh_logs():
            auth_keys.get(GetAuthPublicKeyRequest(id=auth_public_key_id)).wait()
        return True
    except Exception as exc:
        if _is_not_found_error(exc):
            return False
        raise RuntimeError(
            f"Failed to verify auth public key '{auth_public_key_id}' via Nebius API: {exc}"
        ) from exc
    finally:
        _close_sdk(sdk)
