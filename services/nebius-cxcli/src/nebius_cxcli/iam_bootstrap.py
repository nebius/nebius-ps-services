"""Nebius IAM bootstrap helpers for CI service-account authentication."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .sdk_auth import init_nebius_sdk


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
    auth_private_key_pem: str
    s3_access_key_id: str
    s3_secret_access_key: str


@dataclass(frozen=True)
class CIIdentityEnsureResult:
    """Idempotent identity state for CI automation."""

    project_id: str
    service_account_name: str
    service_account_id: str
    service_account_created: bool
    roles_created: list[str]
    roles_already_present: list[str]


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
):
    return init_nebius_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        context="auth bootstrap",
        prefer_operator_auth=prefer_operator_auth,
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
            return existing_id, False
    except Exception as exc:
        if not _is_not_found_error(exc):
            raise RuntimeError(
                f"Failed to fetch service account '{service_account_name}': {exc}"
            ) from exc

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
) -> tuple[list[str], list[str]]:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import (
        AccessPermitSpec,
        CreateAccessPermitRequest,
        ListAccessPermitRequest,
    )

    existing_roles: set[str] = set()
    page_token: str | None = None
    while True:
        response = access_permits.list(
            ListAccessPermitRequest(parent_id=permit_parent_id, page_token=page_token)
        ).wait()
        for item in list(getattr(response, "items", [])):
            spec = getattr(item, "spec", None)
            if spec is None:
                continue
            if getattr(spec, "resource_id", "") == project_id:
                role = _normalize_role_id(getattr(spec, "role", ""))
                if role:
                    existing_roles.add(role)
        page_token = getattr(response, "next_page_token", "") or None
        if not page_token:
            break

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


def _generate_rsa_key_pair_pem() -> tuple[str, str]:
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


def _create_auth_public_key(
    *,
    auth_keys,
    project_id: str,
    service_account_id: str,
    description: str,
) -> tuple[str, str]:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import AuthPublicKeySpec, CreateAuthPublicKeyRequest

    private_pem, public_pem = _generate_rsa_key_pair_pem()
    try:
        operation = auth_keys.create(
            CreateAuthPublicKeyRequest(
                metadata=ResourceMetadata(parent_id=project_id),
                spec=AuthPublicKeySpec(
                    account=_account_ref(service_account_id),
                    description=description,
                    data=public_pem,
                ),
            )
        ).wait()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to create auth public key for service account '{service_account_id}': {exc}"
        ) from exc

    auth_public_key_id = getattr(operation, "resource_id", "")
    if not auth_public_key_id:
        raise RuntimeError("Auth public key was created but key ID could not be resolved")

    return auth_public_key_id, private_pem


def _create_object_storage_access_key(
    *,
    access_keys,
    project_id: str,
    service_account_id: str,
    description: str,
) -> tuple[str, str]:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v2 import (
        AccessKeySpec,
        CreateAccessKeyRequest,
        GetAccessKeyRequest,
        GetAccessKeySecretRequest,
    )

    try:
        operation = access_keys.create(
            CreateAccessKeyRequest(
                metadata=ResourceMetadata(parent_id=project_id),
                spec=AccessKeySpec(
                    account=_account_ref(service_account_id), description=description
                ),
            )
        ).wait()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to create access key for service account '{service_account_id}': {exc}"
        ) from exc

    access_key_id = getattr(operation, "resource_id", "")
    if not access_key_id:
        raise RuntimeError("Access key was created but key ID could not be resolved")

    try:
        secret_response = access_keys.get_secret(GetAccessKeySecretRequest(id=access_key_id)).wait()
    except Exception as exc:
        raise RuntimeError(
            "Access key was created but retrieving secret failed. "
            "Create a new access key and capture its secret immediately."
        ) from exc

    aws_access_key_id = getattr(secret_response, "aws_access_key_id", "")
    s3_secret_access_key = getattr(secret_response, "secret", "")

    if not aws_access_key_id:
        # Fallback for SDK variants where aws_access_key_id is absent on GetAccessKeySecret response.
        key = access_keys.get(GetAccessKeyRequest(id=access_key_id)).wait()
        aws_access_key_id = getattr(getattr(key, "status", None), "aws_access_key_id", "")

    if not aws_access_key_id or not s3_secret_access_key:
        raise RuntimeError(
            "Access key secret response did not include expected values (aws_access_key_id/secret)"
        )

    return aws_access_key_id, s3_secret_access_key


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
    )

    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
    )

    from nebius.api.nebius.iam.v1 import AuthPublicKeyServiceClient
    from nebius.api.nebius.iam.v2 import AccessKeyServiceClient

    auth_keys = AuthPublicKeyServiceClient(sdk)
    access_keys = AccessKeyServiceClient(sdk)

    auth_public_key_id, auth_private_key_pem = _create_auth_public_key(
        auth_keys=auth_keys,
        project_id=identity.project_id,
        service_account_id=identity.service_account_id,
        description=auth_key_description,
    )

    s3_access_key_id, s3_secret_access_key = _create_object_storage_access_key(
        access_keys=access_keys,
        project_id=identity.project_id,
        service_account_id=identity.service_account_id,
        description=access_key_description,
    )

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


def ensure_ci_service_account_identity(
    *,
    project_id: str,
    service_account_name: str,
    service_account_description: str,
    role_ids: list[str],
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
) -> CIIdentityEnsureResult:
    """Ensure service-account identity + role grants without creating new keys."""
    if not role_ids:
        raise ValueError("role_ids must not be empty")

    sdk = _init_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        prefer_operator_auth=True,
    )

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
    )

    permit_group_name = _group_name_for_service_account(service_account_name)
    permit_group_id, _ = _ensure_group(
        groups=groups,
        project_id=project_id,
        group_name=permit_group_name,
    )
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
    )

    return CIIdentityEnsureResult(
        project_id=project_id,
        service_account_name=service_account_name,
        service_account_id=service_account_id,
        service_account_created=service_account_created,
        roles_created=roles_created,
        roles_already_present=roles_already_present,
    )


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

    from nebius.api.nebius.iam.v1 import AuthPublicKeyServiceClient, GetAuthPublicKeyRequest

    auth_keys = AuthPublicKeyServiceClient(sdk)
    try:
        auth_keys.get(GetAuthPublicKeyRequest(id=auth_public_key_id)).wait()
        return True
    except Exception as exc:
        if _is_not_found_error(exc):
            return False
        raise RuntimeError(
            f"Failed to verify auth public key '{auth_public_key_id}' via Nebius API: {exc}"
        ) from exc
