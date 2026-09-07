"""Explicit dedicated-group IAM grants. Call only with authorized exclusive IAM ownership."""

from sdk.runtime import CloudError, collect_pages, rpc, submit, verify_identity


def create_dedicated_group(
    *,
    sdk,
    group_parent_id: str,
    group_name: str,
    service_account_id: str,
    project_id: str,
) -> str:
    """Create once; collisions fail. A partially created group is never auto-deleted."""
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import (
        CreateGroupMembershipRequest,
        CreateGroupRequest,
        GetServiceAccountRequest,
        GroupMembershipServiceClient,
        GroupMembershipSpec,
        GroupServiceClient,
        ServiceAccountServiceClient,
    )

    account = rpc(
        ServiceAccountServiceClient(sdk).get,
        GetServiceAccountRequest(id=service_account_id),
    )
    verify_identity(account, parent_id=project_id, resource_id=service_account_id)
    result = submit(
        GroupServiceClient(sdk).create,
        CreateGroupRequest(
            metadata=ResourceMetadata(parent_id=group_parent_id, name=group_name)
        ),
    )
    try:
        submit(
            GroupMembershipServiceClient(sdk).create,
            CreateGroupMembershipRequest(
                metadata=ResourceMetadata(parent_id=result.resource_id),
                spec=GroupMembershipSpec(member_id=service_account_id),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - Retain both completed and pending identities.
        from sdk.runtime import error_code

        error = CloudError(
            error_code(exc),
            resource_id=result.resource_id,
            operation_id=getattr(exc, "operation_id", ""),
            outcome="reconcile-required",
        )
        error.pending_resource_id = getattr(exc, "resource_id", "")
        error.completed_resource_ids = (result.resource_id,)
        error.completed_operation_ids = (result.operation_id,)
        raise error from None
    return result.resource_id


def grant_service_account_project_roles(
    *,
    sdk,
    project_id: str,
    service_account_id: str,
    group_id: str,
    group_parent_id: str,
    role_ids: list[str],
) -> list[str]:
    """Add only requested missing permits to an explicit single-member group.

    This does not lock IAM. The caller must keep other IAM writers quiescent
    throughout verification and grant; a post-check is detection, not atomicity.
    """
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import (
        AccessPermitServiceClient,
        AccessPermitSpec,
        CreateAccessPermitRequest,
        GetGroupRequest,
        GetServiceAccountRequest,
        GroupMembershipServiceClient,
        GroupServiceClient,
        ListAccessPermitRequest,
        ListGroupMembershipsRequest,
        ServiceAccountServiceClient,
    )

    if not role_ids or any(
        not role or role != role.strip() or "/" in role for role in role_ids
    ):
        raise ValueError("explicit plain role IDs required; aliases are not accepted")
    account = rpc(
        ServiceAccountServiceClient(sdk).get,
        GetServiceAccountRequest(id=service_account_id),
    )
    verify_identity(account, parent_id=project_id, resource_id=service_account_id)
    groups = GroupServiceClient(sdk)
    members = GroupMembershipServiceClient(sdk)
    permits = AccessPermitServiceClient(sdk)

    def verify_group():
        group = rpc(groups.get, GetGroupRequest(id=group_id))
        verify_identity(group, parent_id=group_parent_id, resource_id=group_id)
        rows = collect_pages(
            members.list_members,
            lambda token: ListGroupMembershipsRequest(
                parent_id=group_id, page_token=token
            ),
            field="memberships",
        )
        for row in rows:
            verify_identity(row, parent_id=group_id)
        if len(rows) != 1 or rows[0].spec.member_id != service_account_id:
            raise CloudError("UNEXPECTED_GROUP_MEMBERSHIP")

    verify_group()
    existing = collect_pages(
        permits.list,
        lambda token: ListAccessPermitRequest(parent_id=group_id, page_token=token),
    )
    for permit in existing:
        verify_identity(permit, parent_id=group_id)
    roles = {p.spec.role for p in existing if p.spec.resource_id == project_id}
    created = []
    try:
        for role in dict.fromkeys(role_ids):
            if role in roles:
                continue
            verify_group()
            result = submit(
                permits.create,
                CreateAccessPermitRequest(
                    metadata=ResourceMetadata(parent_id=group_id),
                    spec=AccessPermitSpec(resource_id=project_id, role=role),
                ),
            )
            created.append(result.resource_id)
        verify_group()
    except Exception as exc:  # noqa: BLE001 - Sanitize errors and retain reconciliation IDs.
        from sdk.runtime import error_code

        error = CloudError(
            error_code(exc), resource_id=group_id, outcome="reconcile-required"
        )
        error.completed_resource_ids = tuple(created)
        error.operation_id = getattr(exc, "operation_id", "")
        error.pending_resource_id = getattr(exc, "resource_id", "")
        raise error from None
    return created
