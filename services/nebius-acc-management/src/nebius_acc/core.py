from __future__ import annotations

import logging
from dataclasses import dataclass

from grpc import StatusCode
from nebius.aio.service_error import RequestError as NebiusRequestError
from nebius.api.nebius.common.v1 import GetByNameRequest, ResourceMetadata
from nebius.api.nebius.iam.v1 import (
    AccessPermitSpec,
    ContainerSpec,
    CreateAccessPermitRequest,
    CreateFederationCertificateRequest,
    CreateFederationRequest,
    CreateGroupMembershipRequest,
    CreateGroupRequest,
    CreateInvitationRequest,
    CreateProjectRequest,
    Federation,
    FederationCertificateSpec,
    FederationSpec,
    GetGroupByNameRequest,
    GetInvitationRequest,
    GetProjectByNameRequest,
    GroupMembership,
    GroupMembershipSpec,
    GroupSpec,
    Invitation,
    InvitationSpec,
    ListAccessPermitRequest,
    ListFederationCertificateByFederationRequest,
    ListGroupMembershipsRequest,
    ListInvitationsRequest,
    ListTenantUserAccountsWithAttributesRequest,
    SamlSettings,
    UpdateFederationRequest,
)
from nebius.api.nebius.quotas.v1 import (
    CreateQuotaAllowanceRequest,
    ListQuotaAllowancesRequest,
    QuotaAllowanceSpec,
    UpdateQuotaAllowanceRequest,
)

from .errors import ConfigError, NebiusSdkError
from .nebius_sdk import NebiusSdk
from .quota import QuotaEntry


@dataclass(frozen=True)
class ProjectResult:
    name: str
    project_id: str
    created: bool
    group_id: str
    access_permit_created: bool


def ensure_project(
    sdk: NebiusSdk,
    tenant_id: str,
    name: str,
    region_id: str,
) -> tuple[str, bool, str | None]:
    existing = get_project_by_name(sdk, tenant_id, name)
    if existing:
        return existing["id"], False, existing.get("region")
    try:
        request = CreateProjectRequest(
            metadata=ResourceMetadata(parent_id=tenant_id, name=name),
            spec=ContainerSpec(region=region_id),
        )
        operation = sdk.projects.create(request).wait()
    except NebiusRequestError as exc:
        if _is_already_exists_error(exc):
            existing = get_project_by_name(sdk, tenant_id, name)
            if existing:
                return existing["id"], False, existing.get("region")
        _raise_request_error("Creating project", exc)
    project_id = getattr(operation, "resource_id", "")
    if not project_id:
        existing = get_project_by_name(sdk, tenant_id, name)
        if existing:
            return existing["id"], True, existing.get("region") or region_id
        raise ConfigError(f"Failed to resolve project ID for '{name}'")
    return project_id, True, region_id


def get_project_by_name(sdk: NebiusSdk, tenant_id: str, name: str) -> dict[str, str] | None:
    try:
        project = sdk.projects.get_by_name(
            GetProjectByNameRequest(parent_id=tenant_id, name=name)
        ).wait()
    except NebiusRequestError as exc:
        if _is_not_found_error(exc):
            return None
        _raise_request_error("Fetching project", exc)
    project_id = project.metadata.id if project and project.metadata else ""
    if not project_id:
        return None
    region = project.spec.region if project and project.spec else ""
    return {"id": project_id, "region": region}


def ensure_group(sdk: NebiusSdk, tenant_id: str, name: str) -> tuple[str, bool]:
    existing = get_group_by_name(sdk, tenant_id, name)
    if existing:
        return existing, False
    try:
        request = CreateGroupRequest(
            metadata=ResourceMetadata(parent_id=tenant_id, name=name),
            spec=GroupSpec(),
        )
        operation = sdk.groups.create(request).wait()
    except NebiusRequestError as exc:
        if _is_already_exists_error(exc):
            existing = get_group_by_name(sdk, tenant_id, name)
            if existing:
                return existing, False
        _raise_request_error("Creating group", exc)
    group_id = getattr(operation, "resource_id", "")
    if not group_id:
        existing = get_group_by_name(sdk, tenant_id, name)
        if existing:
            return existing, True
        raise ConfigError(f"Failed to resolve group ID for '{name}'")
    return group_id, True


def get_group_by_name(sdk: NebiusSdk, tenant_id: str, name: str) -> str | None:
    try:
        group = sdk.groups.get_by_name(GetGroupByNameRequest(parent_id=tenant_id, name=name)).wait()
    except NebiusRequestError as exc:
        if _is_not_found_error(exc):
            return None
        _raise_request_error("Fetching group", exc)
    group_id = group.metadata.id if group and group.metadata else ""
    return group_id or None


def ensure_access_permit(
    sdk: NebiusSdk,
    group_id: str,
    resource_id: str,
    role: str,
) -> bool:
    permits = list_access_permits(sdk, group_id)
    for permit in permits:
        spec = permit.spec
        if not spec:
            continue
        if spec.resource_id == resource_id and spec.role == role:
            return False
    try:
        request = CreateAccessPermitRequest(
            metadata=ResourceMetadata(parent_id=group_id),
            spec=AccessPermitSpec(resource_id=resource_id, role=role),
        )
        sdk.access_permits.create(request).wait()
    except NebiusRequestError as exc:
        if _is_already_exists_error(exc):
            return False
        _raise_request_error("Creating access permit", exc)
    return True


def list_access_permits(sdk: NebiusSdk, group_id: str) -> list:
    permits = []
    page_token: str | None = None
    while True:
        try:
            response = sdk.access_permits.list(
                ListAccessPermitRequest(parent_id=group_id, page_token=page_token)
            ).wait()
        except NebiusRequestError as exc:
            _raise_request_error("Listing access permits", exc)
        permits.extend(list(response.items))
        page_token = response.next_page_token or None
        if not page_token:
            break
    return permits


def list_group_memberships(sdk: NebiusSdk, group_id: str) -> list[GroupMembership]:
    memberships: list[GroupMembership] = []
    page_token: str | None = None
    while True:
        try:
            response = sdk.group_memberships.list_members(
                ListGroupMembershipsRequest(parent_id=group_id, page_token=page_token)
            ).wait()
        except NebiusRequestError as exc:
            _raise_request_error("Listing group memberships", exc)
        memberships.extend(list(response.memberships))
        page_token = response.next_page_token or None
        if not page_token:
            break
    return memberships


def ensure_group_membership(
    sdk: NebiusSdk,
    group_id: str,
    member_id: str,
    existing_member_ids: set[str] | None = None,
) -> bool:
    if existing_member_ids is not None:
        if member_id in existing_member_ids:
            return False
    else:
        memberships = list_group_memberships(sdk, group_id)
        for membership in memberships:
            spec = membership.spec
            if spec and spec.member_id == member_id:
                return False
    try:
        request = CreateGroupMembershipRequest(
            metadata=ResourceMetadata(parent_id=group_id),
            spec=GroupMembershipSpec(member_id=member_id),
        )
        sdk.group_memberships.create(request).wait()
    except NebiusRequestError as exc:
        if _is_already_exists_error(exc):
            return False
        _raise_request_error("Creating group membership", exc)
    if existing_member_ids is not None:
        existing_member_ids.add(member_id)
    return True


def list_invitations(sdk: NebiusSdk, tenant_id: str) -> list[Invitation]:
    invites: list[Invitation] = []
    page_token: str | None = None
    while True:
        try:
            response = sdk.invitations.list(
                ListInvitationsRequest(parent_id=tenant_id, page_token=page_token)
            ).wait()
        except NebiusRequestError as exc:
            _raise_request_error("Listing invitations", exc)
        invites.extend(list(response.items))
        page_token = response.next_page_token or None
        if not page_token:
            break
    return invites


def ensure_invitation(
    sdk: NebiusSdk,
    tenant_id: str,
    email: str,
    description: str | None = None,
) -> tuple[Invitation | None, bool]:
    existing = _find_invitation_by_email(list_invitations(sdk, tenant_id), email)
    if existing and not _invitation_expired(existing):
        return existing, False

    try:
        request = CreateInvitationRequest(
            metadata=ResourceMetadata(parent_id=tenant_id),
            spec=InvitationSpec(email=email, description=description or ""),
        )
        result = sdk.invitations.create(request).wait()
    except NebiusRequestError as exc:
        if _is_already_exists_error(exc):
            existing = _find_invitation_by_email(list_invitations(sdk, tenant_id), email)
            if existing:
                return existing, False
        _raise_request_error("Creating invitation", exc)

    invitation_id = getattr(result, "resource_id", "") or getattr(
        getattr(result, "metadata", None), "id", ""
    )
    if invitation_id:
        invite = sdk.invitations.get(GetInvitationRequest(id=invitation_id)).wait()
        return invite, True

    existing = _find_invitation_by_email(list_invitations(sdk, tenant_id), email)
    return existing, True


def list_tenant_users_with_attributes(sdk: NebiusSdk, tenant_id: str) -> list:
    users: list = []
    page_token: str | None = None
    while True:
        try:
            response = sdk.tenant_users.list(
                ListTenantUserAccountsWithAttributesRequest(
                    parent_id=tenant_id,
                    page_token=page_token,
                )
            ).wait()
        except NebiusRequestError as exc:
            _raise_request_error("Listing tenant users", exc)
        users.extend(list(response.items))
        page_token = response.next_page_token or None
        if not page_token:
            break
    return users


def _find_invitation_by_email(invitations: list[Invitation], email: str) -> Invitation | None:
    target = email.strip().lower()
    for invite in invitations:
        spec = invite.spec
        if spec and (spec.email or "").strip().lower() == target:
            return invite
    return None


def _invitation_expired(invite: Invitation) -> bool:
    status = invite.status
    if not status:
        return False
    state = getattr(status, "state", None)
    return str(state) == "EXPIRED" or getattr(state, "name", "") == "EXPIRED"


def apply_quotas(
    sdk: NebiusSdk,
    project_id: str,
    quotas: list[QuotaEntry],
    logger: logging.Logger,
) -> None:
    existing = _list_quota_allowances(sdk, project_id)
    for quota in quotas:
        existing_item = _find_quota_allowance(existing, quota)
        if existing_item and _quota_matches(existing_item, quota):
            logger.info(
                "Quota %s=%s for %s already set; skipping",
                quota.name,
                quota.limit,
                project_id,
            )
            continue
        if existing_item:
            logger.info("Updating quota %s=%s for %s", quota.name, quota.limit, project_id)
            _update_quota_allowance(sdk, project_id, existing_item, quota)
        else:
            logger.info("Creating quota %s=%s for %s", quota.name, quota.limit, project_id)
            _create_quota_allowance(sdk, project_id, quota)


def _create_quota_allowance(sdk: NebiusSdk, project_id: str, quota: QuotaEntry) -> None:
    request = CreateQuotaAllowanceRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=quota.name),
        spec=QuotaAllowanceSpec(limit=quota.limit, region=quota.region),
    )
    try:
        sdk.quotas.create(request).wait()
    except NebiusRequestError as exc:
        if _is_already_exists_error(exc):
            return
        _raise_request_error("Creating quota allowance", exc)


def _update_quota_allowance(
    sdk: NebiusSdk,
    project_id: str,
    existing,
    quota: QuotaEntry,
) -> None:
    existing_id = existing.metadata.id if existing and existing.metadata else ""
    if not existing_id:
        existing_id = _find_quota_allowance_id(sdk, project_id, quota.name, quota.region)
    if not existing_id:
        # Fallback: try create to keep the run moving; if it already exists we'll skip.
        _create_quota_allowance(sdk, project_id, quota)
        return
    update_request = UpdateQuotaAllowanceRequest(
        metadata=ResourceMetadata(id=existing_id, parent_id=project_id, name=quota.name),
        spec=QuotaAllowanceSpec(limit=quota.limit, region=quota.region),
    )
    try:
        sdk.quotas.update(update_request).wait()
    except NebiusRequestError as exc:
        _raise_request_error("Updating quota allowance", exc)


def _list_quota_allowances(sdk: NebiusSdk, project_id: str) -> list:
    items: list = []
    page_token: str | None = None
    while True:
        try:
            response = sdk.quotas.list(
                ListQuotaAllowancesRequest(parent_id=project_id, page_token=page_token)
            ).wait()
        except NebiusRequestError as exc:
            _raise_request_error("Listing quota allowances", exc)
        items.extend(list(response.items))
        page_token = response.next_page_token or None
        if not page_token:
            break
    return items


def _find_quota_allowance(items: list, quota: QuotaEntry):
    matches = [item for item in items if item.metadata and item.metadata.name == quota.name]
    if not matches:
        return None
    for item in matches:
        spec = item.spec
        if spec and getattr(spec, "region", None) == quota.region:
            return item
    if len(matches) == 1:
        return matches[0]
    return None


def _find_quota_allowance_id(
    sdk: NebiusSdk,
    project_id: str,
    name: str,
    region: str,
) -> str | None:
    for item in _list_quota_allowances(sdk, project_id):
        if not item.metadata or item.metadata.name != name:
            continue
        spec = item.spec
        if spec and getattr(spec, "region", None) == region:
            return item.metadata.id
    return None


def _quota_matches(existing, quota: QuotaEntry) -> bool:
    spec = existing.spec if existing else None
    if not spec:
        return False
    return (
        getattr(spec, "region", None) == quota.region
        and getattr(spec, "limit", None) == quota.limit
    )


def create_federation(
    sdk: NebiusSdk,
    tenant_id: str,
    name: str,
    sso_url: str,
    idp_issuer: str,
    auto_create: bool,
    active: bool,
    force_authn: bool,
) -> str:
    request = CreateFederationRequest(
        metadata=ResourceMetadata(parent_id=tenant_id, name=name),
        spec=_build_federation_spec(
            sso_url=sso_url,
            idp_issuer=idp_issuer,
            auto_create=auto_create,
            active=active,
            force_authn=force_authn,
        ),
    )
    try:
        operation = sdk.federations.create(request).wait()
    except NebiusRequestError as exc:
        _raise_request_error("Creating federation", exc)
    federation_id = getattr(operation, "resource_id", "")
    if not federation_id:
        raise ConfigError("Failed to resolve federation ID")
    return federation_id


def create_federation_certificate(
    sdk: NebiusSdk,
    federation_id: str,
    cert_data: str,
    description: str | None,
) -> None:
    request = CreateFederationCertificateRequest(
        metadata=ResourceMetadata(parent_id=federation_id),
        spec=FederationCertificateSpec(
            description=description or "",
            data=cert_data,
        ),
    )
    try:
        sdk.federation_certs.create(request).wait()
    except NebiusRequestError as exc:
        _raise_request_error("Creating federation certificate", exc)


def ensure_federation(
    sdk: NebiusSdk,
    tenant_id: str,
    name: str,
    sso_url: str,
    idp_issuer: str,
    auto_create: bool,
    active: bool,
    force_authn: bool,
) -> tuple[str, bool, bool]:
    existing = get_federation_by_name(sdk, tenant_id, name)
    desired = _build_federation_spec(
        sso_url=sso_url,
        idp_issuer=idp_issuer,
        auto_create=auto_create,
        active=active,
        force_authn=force_authn,
    )
    if existing:
        federation_id = existing.metadata.id if existing.metadata else ""
        if not federation_id:
            raise ConfigError(f"Failed to resolve federation ID for '{name}'")
        if _federation_needs_update(existing, desired):
            _update_federation(sdk, tenant_id, federation_id, name, desired)
            return federation_id, False, True
        return federation_id, False, False
    federation_id = create_federation(
        sdk,
        tenant_id=tenant_id,
        name=name,
        sso_url=sso_url,
        idp_issuer=idp_issuer,
        auto_create=auto_create,
        active=active,
        force_authn=force_authn,
    )
    return federation_id, True, False


def ensure_federation_certificate(
    sdk: NebiusSdk,
    federation_id: str,
    cert_data: str,
    description: str | None,
) -> bool:
    existing = _list_federation_certificates(sdk, federation_id)
    for cert in existing:
        spec = cert.spec
        if spec and getattr(spec, "data", None) == cert_data:
            return False
    create_federation_certificate(sdk, federation_id, cert_data, description)
    return True


def get_federation_by_name(sdk: NebiusSdk, tenant_id: str, name: str) -> Federation | None:
    try:
        federation = sdk.federations.get_by_name(
            GetByNameRequest(parent_id=tenant_id, name=name)
        ).wait()
    except NebiusRequestError as exc:
        if _is_not_found_error(exc):
            return None
        _raise_request_error("Fetching federation", exc)
    return federation


def _update_federation(
    sdk: NebiusSdk,
    tenant_id: str,
    federation_id: str,
    name: str,
    spec: FederationSpec,
) -> None:
    request = UpdateFederationRequest(
        metadata=ResourceMetadata(id=federation_id, parent_id=tenant_id, name=name),
        spec=spec,
    )
    try:
        sdk.federations.update(request).wait()
    except NebiusRequestError as exc:
        _raise_request_error("Updating federation", exc)


def _build_federation_spec(
    *,
    sso_url: str,
    idp_issuer: str,
    auto_create: bool,
    active: bool,
    force_authn: bool,
) -> FederationSpec:
    return FederationSpec(
        user_account_auto_creation=auto_create,
        active=active,
        saml_settings=SamlSettings(
            idp_issuer=idp_issuer,
            sso_url=sso_url,
            force_authn=force_authn,
        ),
    )


def _federation_needs_update(existing: Federation, desired: FederationSpec) -> bool:
    spec = existing.spec if existing else None
    if not spec:
        return True
    if getattr(spec, "user_account_auto_creation", None) != desired.user_account_auto_creation:
        return True
    if getattr(spec, "active", None) != desired.active:
        return True
    saml = getattr(spec, "saml_settings", None)
    desired_saml = desired.saml_settings
    if not saml or not desired_saml:
        return True
    if getattr(saml, "idp_issuer", None) != desired_saml.idp_issuer:
        return True
    if getattr(saml, "sso_url", None) != desired_saml.sso_url:
        return True
    return getattr(saml, "force_authn", None) != desired_saml.force_authn


def _list_federation_certificates(sdk: NebiusSdk, federation_id: str) -> list:
    items: list = []
    page_token: str | None = None
    while True:
        try:
            response = sdk.federation_certs.list_by_federation(
                ListFederationCertificateByFederationRequest(
                    federation_id=federation_id,
                    page_token=page_token,
                )
            ).wait()
        except NebiusRequestError as exc:
            _raise_request_error("Listing federation certificates", exc)
        items.extend(list(response.items))
        page_token = response.next_page_token or None
        if not page_token:
            break
    return items


def _is_not_found_error(exc: NebiusRequestError) -> bool:
    if exc.status.code == StatusCode.NOT_FOUND:
        return True
    return any(
        getattr(err, "resource_not_found", None) is not None for err in exc.status.service_errors
    )


def _is_already_exists_error(exc: NebiusRequestError) -> bool:
    if exc.status.code == StatusCode.ALREADY_EXISTS:
        return True
    return any(
        getattr(err, "resource_already_exists", None) is not None
        for err in exc.status.service_errors
    )


def _raise_request_error(action: str, exc: NebiusRequestError) -> None:
    detail = _format_request_error(exc)
    raise NebiusSdkError(f"{action} failed: {detail}") from exc


def _format_request_error(exc: NebiusRequestError) -> str:
    status = exc.status
    code = status.code.name if hasattr(status.code, "name") else str(status.code)
    message = status.message or "request failed"
    return f"{code}: {message}"
