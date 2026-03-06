"""Snippet: grant project roles to a service account via IAM group + access permits."""

from __future__ import annotations

import hashlib
import re

from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.iam.v1 import (
    AccessPermitServiceClient,
    AccessPermitSpec,
    CreateAccessPermitRequest,
    CreateGroupMembershipRequest,
    CreateGroupRequest,
    GetGroupByNameRequest,
    GroupMembershipServiceClient,
    GroupMembershipSpec,
    GroupServiceClient,
    ListAccessPermitRequest,
    ListGroupMembershipsRequest,
)


def _wait(op_or_message):  # type: ignore[no-untyped-def]
    return op_or_message.wait() if hasattr(op_or_message, "wait") else op_or_message


def _is_not_found_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "not found" in message or "statuscode.not_found" in message


def _is_already_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "statuscode.already_exists" in message


def _normalize_role_id(role_id: str) -> str:
    token = role_id.strip()
    if token.startswith("roles/"):
        token = token.split("/", 1)[1]
    return token


def _group_name_for_service_account(service_account_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", service_account_name.lower()).strip("-")
    normalized = normalized or "nebius-sa"
    candidate = f"{normalized}-permits"
    if len(candidate) <= 63:
        return candidate
    digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:8]
    head = normalized[: max(1, 63 - len("-permits-") - len(digest))].rstrip("-") or "sa"
    return f"{head}-permits-{digest}"


def _ensure_group(*, groups, project_id: str, group_name: str) -> str:
    try:
        existing = _wait(groups.get_by_name(GetGroupByNameRequest(parent_id=project_id, name=group_name)))
        existing_id = getattr(getattr(existing, "metadata", None), "id", "")
        if existing_id:
            return existing_id
    except Exception as exc:
        if not _is_not_found_error(exc):
            raise

    try:
        operation = _wait(
            groups.create(
                CreateGroupRequest(
                    metadata=ResourceMetadata(parent_id=project_id, name=group_name),
                )
            )
        )
    except Exception as exc:
        if _is_already_exists_error(exc):
            existing = _wait(groups.get_by_name(GetGroupByNameRequest(parent_id=project_id, name=group_name)))
            existing_id = getattr(getattr(existing, "metadata", None), "id", "")
            if existing_id:
                return existing_id
        raise

    group_id = getattr(operation, "resource_id", "")
    if group_id:
        return group_id
    existing = _wait(groups.get_by_name(GetGroupByNameRequest(parent_id=project_id, name=group_name)))
    existing_id = getattr(getattr(existing, "metadata", None), "id", "")
    if existing_id:
        return existing_id
    raise RuntimeError("Group created but ID could not be resolved")


def _ensure_group_membership(*, group_memberships, group_id: str, member_id: str) -> None:
    page_token: str | None = None
    while True:
        response = _wait(
            group_memberships.list_members(
                ListGroupMembershipsRequest(parent_id=group_id, page_token=page_token)
            )
        )
        for membership in list(getattr(response, "memberships", [])):
            spec = getattr(membership, "spec", None)
            if getattr(spec, "member_id", "") == member_id:
                return
        page_token = getattr(response, "next_page_token", "") or None
        if not page_token:
            break

    try:
        _wait(
            group_memberships.create(
                CreateGroupMembershipRequest(
                    metadata=ResourceMetadata(parent_id=group_id),
                    spec=GroupMembershipSpec(member_id=member_id),
                )
            )
        )
    except Exception as exc:
        if not _is_already_exists_error(exc):
            raise


def _ensure_access_permit_roles(
    *,
    access_permits,
    group_id: str,
    project_id: str,
    role_ids: list[str],
) -> list[str]:
    existing: set[str] = set()
    page_token: str | None = None
    while True:
        response = _wait(
            access_permits.list(ListAccessPermitRequest(parent_id=group_id, page_token=page_token))
        )
        for permit in list(getattr(response, "items", [])):
            spec = getattr(permit, "spec", None)
            if spec is None:
                continue
            if getattr(spec, "resource_id", "") == project_id:
                role = _normalize_role_id(str(getattr(spec, "role", "") or ""))
                if role:
                    existing.add(role)
        page_token = getattr(response, "next_page_token", "") or None
        if not page_token:
            break

    created: list[str] = []
    for role_raw in role_ids:
        role_id = _normalize_role_id(role_raw)
        if not role_id or role_id in existing:
            continue
        _wait(
            access_permits.create(
                CreateAccessPermitRequest(
                    metadata=ResourceMetadata(parent_id=group_id),
                    spec=AccessPermitSpec(resource_id=project_id, role=role_id),
                )
            )
        )
        created.append(role_id)
    return created


def ensure_service_account_project_roles(
    *,
    sdk,
    project_id: str,
    service_account_id: str,
    service_account_name: str,
    role_ids: list[str],
) -> tuple[str, list[str]]:
    """
    Ensure IAM group-based project role grants for a service account.

    Returns (group_id, created_roles).
    """
    groups = GroupServiceClient(sdk)
    group_memberships = GroupMembershipServiceClient(sdk)
    access_permits = AccessPermitServiceClient(sdk)

    group_name = _group_name_for_service_account(service_account_name)
    group_id = _ensure_group(groups=groups, project_id=project_id, group_name=group_name)
    _ensure_group_membership(
        group_memberships=group_memberships,
        group_id=group_id,
        member_id=service_account_id,
    )
    created_roles = _ensure_access_permit_roles(
        access_permits=access_permits,
        group_id=group_id,
        project_id=project_id,
        role_ids=role_ids,
    )
    return group_id, created_roles
