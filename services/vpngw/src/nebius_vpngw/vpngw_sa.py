from __future__ import annotations

import json
import os
import re
import subprocess
import typing as t
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

from .deploy.vm_ha_cloud import nebius_request_error_code_is
from .nebius_pagination import collect_nebius_pages, nebius_resource_id

"""
Service Account management for Nebius VPNGW.

This module ensures the exact dedicated Service Account, custom group,
membership, and project permission used by an explicit ordinary ``apply --sa``
or the product-managed VM-HA identity. It uses the generated IAM clients from
the pinned Nebius SDK and obtains only a short-lived impersonated token from
the installed Nebius CLI. Authorized-key enrollment remains owned by the
VM-HA credential store.
"""


# Current Nebius IAM does not expose service-specific Compute/VPC editor roles,
# and access permits cannot be attached directly to all VPC resource types.
# Project-level editor is therefore the narrowest current role boundary that
# covers the required Compute ownership and VPC allocation/route mutations.
VM_HA_ROLE_ALLOWLIST = frozenset({"editor"})
_SERVICE_ACCOUNT_DESCRIPTION = "Nebius VPNGW orchestrator"
_IAM_PAGE_SIZE = 1000


@dataclass(frozen=True)
class ServiceAccountTokenIdentity:
    """One exact selected service account and its short-lived token."""

    token: str = field(repr=False, compare=False)
    service_account_id: str
    service_account_name: str


def _init_client(tenant_id: str | None, project_id: str | None, region_id: str | None):
    """Initialize the SDK from the explicit token or renewable CLI profile."""

    del tenant_id, project_id, region_id
    try:
        from .nebius_auth import build_operator_sdk_client

        explicit_token = os.environ.get("NEBIUS_IAM_TOKEN") or None
        return build_operator_sdk_client(explicit_token=explicit_token), True
    except Exception as error:  # pragma: no cover - runtime import/config guard
        raise RuntimeError(
            "Failed to initialize the Nebius SDK with operator credentials; refresh "
            "the Nebius CLI profile or explicit token and retry."
        ) from error


def _wait(value: t.Any) -> t.Any:
    return value.wait() if hasattr(value, "wait") else value


def _wait_operation(value: t.Any) -> t.Any:
    """Wait for both the create request and its terminal long-running operation."""

    operation = _wait(value)
    from .deploy.vm_ha_cloud import wait_vm_ha_operation

    wait_vm_ha_operation(operation)
    return operation


def _is_not_found_error(error: Exception) -> bool:
    return nebius_request_error_code_is(error, "NOT_FOUND")


def _is_already_exists_error(error: Exception) -> bool:
    return nebius_request_error_code_is(error, "ALREADY_EXISTS")


def _resource_identity(resource: t.Any) -> tuple[str, str, str]:
    metadata = getattr(resource, "metadata", None)
    return (
        str(getattr(metadata, "id", "") or ""),
        str(getattr(metadata, "parent_id", "") or ""),
        str(getattr(metadata, "name", "") or ""),
    )


def _require_named_identity(
    resource: t.Any,
    *,
    kind: str,
    project_id: str,
    expected_name: str,
    ownership_labels: t.Mapping[str, str] | None = None,
) -> str:
    resource_id, parent_id, name = _resource_identity(resource)
    if not resource_id or parent_id != project_id or name != expected_name:
        raise RuntimeError(f"{kind} identity did not reread exactly")
    if ownership_labels is not None:
        labels = dict(getattr(getattr(resource, "metadata", None), "labels", {}) or {})
        if labels != dict(ownership_labels):
            raise RuntimeError(f"{kind} has foreign or incomplete ownership metadata")
    return resource_id


def _iam_services(client: t.Any) -> tuple[t.Any, t.Any, t.Any, t.Any]:
    from nebius.api.nebius.iam.v1 import (  # type: ignore
        AccessPermitServiceClient,
        GroupMembershipServiceClient,
        GroupServiceClient,
        ServiceAccountServiceClient,
    )

    return (
        ServiceAccountServiceClient(client),
        GroupServiceClient(client),
        GroupMembershipServiceClient(client),
        AccessPermitServiceClient(client),
    )


@dataclass(frozen=True)
class VMHAIAMReconciliationPlan:
    """Read-only, approval-bound plan for the managed VM-HA IAM graph."""

    mode: Literal["create", "reuse", "resume"]
    project_id: str
    service_account_name: str
    ownership_labels: tuple[tuple[str, str], ...]
    service_account_action: Literal["create", "reuse"]
    service_account_id: str | None
    group_action: Literal["create", "reuse"]
    group_id: str | None
    membership_action: Literal["create", "reuse"]
    permit_action: Literal["create", "reuse"]

    def approval_record(self) -> dict[str, object]:
        return {
            "group": {"action": self.group_action, "id": self.group_id},
            "membership": {
                "action": self.membership_action,
                "member_id": self.service_account_id,
            },
            "project_editor_permit": {
                "action": self.permit_action,
                "resource_id": self.project_id,
                "role": "editor",
            },
            "service_account": {
                "action": self.service_account_action,
                "id": self.service_account_id,
            },
        }


def _read_named_resource(
    reader: t.Callable[[], t.Any],
    *,
    failure_message: str,
) -> t.Any | None:
    try:
        return reader()
    except Exception as error:
        if nebius_request_error_code_is(error, "NOT_FOUND"):
            return None
        raise RuntimeError(failure_message) from error


def inspect_vm_ha_service_account_identity(
    sa_name: str,
    tenant_id: str | None,
    project_id: str,
    region_id: str | None,
    *,
    mode: Literal["create", "reuse", "resume"],
    expected_service_account_id: str | None,
    ownership_labels: t.Mapping[str, str],
    client_factory: t.Callable[..., tuple[t.Any, bool]] = _init_client,
) -> VMHAIAMReconciliationPlan:
    """Inspect the complete managed IAM graph without mutating it."""

    if mode not in {"create", "reuse", "resume"}:
        raise ValueError("managed VM-HA IAM inspection mode is invalid")
    client, _ = client_factory(tenant_id, project_id, region_id)
    try:
        service_accounts, groups, memberships, permits = _iam_services(client)
        service_account = _read_named_resource(
            lambda: _get_service_account_by_name(service_accounts, project_id, sa_name),
            failure_message="Failed to read the managed VM-HA Service Account",
        )
        group = _read_named_resource(
            lambda: _get_group_by_name(groups, project_id, sa_name),
            failure_message="Failed to read the managed VM-HA Service Account group",
        )

        if mode == "create" and (service_account is not None or group is not None):
            raise RuntimeError(
                "managed VM-HA IAM state exists without recoverable enrollment state"
            )
        if mode == "reuse" and (service_account is None or group is None):
            raise RuntimeError("managed VM-HA IAM reuse graph is incomplete")

        service_account_id: str | None = None
        if service_account is not None:
            service_account_id = _require_named_identity(
                service_account,
                kind="Managed VM-HA Service Account",
                project_id=project_id,
                expected_name=sa_name,
                ownership_labels=ownership_labels,
            )
            if (
                expected_service_account_id is not None
                and service_account_id != expected_service_account_id
            ):
                raise RuntimeError("managed VM-HA Service Account identity changed")
        elif expected_service_account_id is not None:
            raise RuntimeError("managed VM-HA Service Account identity is missing")

        group_id: str | None = None
        if group is not None:
            group_id = _require_named_identity(
                group,
                kind="Managed VM-HA Service Account group",
                project_id=project_id,
                expected_name=sa_name,
                ownership_labels=ownership_labels,
            )

        membership_action: Literal["create", "reuse"] = "create"
        permit_action: Literal["create", "reuse"] = "create"
        if group_id is not None:
            member_ids = _list_group_member_ids(memberships, group_id)
            if member_ids:
                if service_account_id is None or member_ids != (service_account_id,):
                    raise RuntimeError(
                        "managed VM-HA Service Account group has foreign or duplicate members"
                    )
                membership_action = "reuse"
            access_permits = _list_access_permits(permits, group_id)
            if access_permits:
                if access_permits != ((project_id, "editor"),):
                    raise RuntimeError(
                        "managed VM-HA Service Account group has unexpected access permits"
                    )
                permit_action = "reuse"
        if mode == "reuse" and (membership_action != "reuse" or permit_action != "reuse"):
            raise RuntimeError("managed VM-HA IAM reuse graph is incomplete")

        return VMHAIAMReconciliationPlan(
            mode=mode,
            project_id=project_id,
            service_account_name=sa_name,
            ownership_labels=tuple(sorted(ownership_labels.items())),
            service_account_action="reuse" if service_account is not None else "create",
            service_account_id=service_account_id,
            group_action="reuse" if group is not None else "create",
            group_id=group_id,
            membership_action=membership_action,
            permit_action=permit_action,
        )
    finally:
        _close_client(client)


def _get_service_account_by_name(service: t.Any, project_id: str, name: str) -> t.Any:
    from nebius.api.nebius.iam.v1 import GetServiceAccountByNameRequest  # type: ignore

    return _wait(
        service.get_by_name(GetServiceAccountByNameRequest(parent_id=project_id, name=name))
    )


def _ensure_service_account(
    service: t.Any,
    project_id: str,
    name: str,
    *,
    ownership_labels: t.Mapping[str, str] | None = None,
) -> str:
    from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
    from nebius.api.nebius.iam.v1 import (  # type: ignore
        CreateServiceAccountRequest,
        ServiceAccountSpec,
    )

    try:
        existing = _get_service_account_by_name(service, project_id, name)
    except Exception as error:
        if not _is_not_found_error(error):
            raise RuntimeError("Failed to read the requested Service Account") from error
    else:
        return _require_named_identity(
            existing,
            kind="Service Account",
            project_id=project_id,
            expected_name=name,
            ownership_labels=ownership_labels,
        )

    try:
        _wait_operation(
            service.create(
                CreateServiceAccountRequest(
                    metadata=ResourceMetadata(
                        parent_id=project_id,
                        name=name,
                        labels=dict(ownership_labels or {}),
                    ),
                    spec=ServiceAccountSpec(description=_SERVICE_ACCOUNT_DESCRIPTION),
                )
            )
        )
    except Exception as error:
        if not _is_already_exists_error(error):
            raise RuntimeError("Failed to create the requested Service Account") from error

    try:
        current = _get_service_account_by_name(service, project_id, name)
    except Exception as error:
        raise RuntimeError("Created Service Account could not be reread") from error
    return _require_named_identity(
        current,
        kind="Service Account",
        project_id=project_id,
        expected_name=name,
        ownership_labels=ownership_labels,
    )


def _get_group_by_name(service: t.Any, project_id: str, name: str) -> t.Any:
    from nebius.api.nebius.iam.v1 import GetGroupByNameRequest  # type: ignore

    return _wait(service.get_by_name(GetGroupByNameRequest(parent_id=project_id, name=name)))


def _ensure_group(
    service: t.Any,
    project_id: str,
    name: str,
    *,
    ownership_labels: t.Mapping[str, str] | None = None,
) -> str:
    from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
    from nebius.api.nebius.iam.v1 import CreateGroupRequest, GroupSpec  # type: ignore

    try:
        existing = _get_group_by_name(service, project_id, name)
    except Exception as error:
        if not _is_not_found_error(error):
            raise RuntimeError("Failed to read the dedicated Service Account group") from error
    else:
        return _require_named_identity(
            existing,
            kind="Service Account group",
            project_id=project_id,
            expected_name=name,
            ownership_labels=ownership_labels,
        )

    try:
        _wait_operation(
            service.create(
                CreateGroupRequest(
                    metadata=ResourceMetadata(
                        parent_id=project_id,
                        name=name,
                        labels=dict(ownership_labels or {}),
                    ),
                    spec=GroupSpec(),
                )
            )
        )
    except Exception as error:
        if not _is_already_exists_error(error):
            raise RuntimeError("Failed to create the dedicated Service Account group") from error

    try:
        current = _get_group_by_name(service, project_id, name)
    except Exception as error:
        raise RuntimeError("Created Service Account group could not be reread") from error
    return _require_named_identity(
        current,
        kind="Service Account group",
        project_id=project_id,
        expected_name=name,
        ownership_labels=ownership_labels,
    )


def _list_group_member_ids(service: t.Any, group_id: str) -> tuple[str, ...]:
    from nebius.api.nebius.iam.v1 import ListGroupMembershipsRequest  # type: ignore

    members: list[str] = []
    memberships = collect_nebius_pages(
        lambda page_token: service.list_members(
            ListGroupMembershipsRequest(
                parent_id=group_id,
                page_size=_IAM_PAGE_SIZE,
                page_token=page_token,
            )
        ),
        context="Service account group membership",
        items_field="memberships",
        item_identity=nebius_resource_id,
    )
    for membership in memberships:
        member_id = str(getattr(getattr(membership, "spec", None), "member_id", "") or "")
        if not member_id:
            raise RuntimeError("Service Account group contains a malformed membership")
        members.append(member_id)
    return tuple(members)


def _ensure_exact_group_membership(service: t.Any, group_id: str, member_id: str) -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
    from nebius.api.nebius.iam.v1 import (  # type: ignore
        CreateGroupMembershipRequest,
        GroupMembershipSpec,
    )

    members = _list_group_member_ids(service, group_id)
    if members == (member_id,):
        return
    if members:
        raise RuntimeError("Dedicated Service Account group has foreign or duplicate members")
    try:
        _wait_operation(
            service.create(
                CreateGroupMembershipRequest(
                    metadata=ResourceMetadata(parent_id=group_id),
                    spec=GroupMembershipSpec(member_id=member_id),
                )
            )
        )
    except Exception as error:
        if not _is_already_exists_error(error):
            raise RuntimeError("Failed to create the Service Account group membership") from error
    if _list_group_member_ids(service, group_id) != (member_id,):
        raise RuntimeError("Service Account group membership did not reread exactly")


def _list_access_permits(service: t.Any, group_id: str) -> tuple[tuple[str, str], ...]:
    from nebius.api.nebius.iam.v1 import ListAccessPermitRequest  # type: ignore

    permits: list[tuple[str, str]] = []
    items = collect_nebius_pages(
        lambda page_token: service.list(
            ListAccessPermitRequest(
                parent_id=group_id,
                page_size=_IAM_PAGE_SIZE,
                page_token=page_token,
            )
        ),
        context="Service account access permit",
        item_identity=nebius_resource_id,
    )
    for permit in items:
        spec = getattr(permit, "spec", None)
        resource_id = str(getattr(spec, "resource_id", "") or "")
        role = str(getattr(spec, "role", "") or "")
        if not resource_id or not role:
            raise RuntimeError("Dedicated Service Account group has a malformed access permit")
        permits.append((resource_id, role))
    return tuple(permits)


def _ensure_exact_project_permit(service: t.Any, group_id: str, project_id: str) -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
    from nebius.api.nebius.iam.v1 import (  # type: ignore
        AccessPermitSpec,
        CreateAccessPermitRequest,
    )

    expected = ((project_id, "editor"),)
    permits = _list_access_permits(service, group_id)
    if permits == expected:
        return
    if permits:
        raise RuntimeError("Dedicated Service Account group has unexpected access permits")
    try:
        _wait_operation(
            service.create(
                CreateAccessPermitRequest(
                    metadata=ResourceMetadata(parent_id=group_id),
                    spec=AccessPermitSpec(resource_id=project_id, role="editor"),
                )
            )
        )
    except Exception as error:
        if not _is_already_exists_error(error):
            raise RuntimeError("Failed to create the reviewed project access permit") from error
    if _list_access_permits(service, group_id) != expected:
        raise RuntimeError("Reviewed project access permit did not reread exactly")


def _reconcile_planned_service_account(
    service: t.Any,
    plan: VMHAIAMReconciliationPlan,
) -> str:
    from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
    from nebius.api.nebius.iam.v1 import (  # type: ignore
        CreateServiceAccountRequest,
        ServiceAccountSpec,
    )

    labels = dict(plan.ownership_labels)
    current = _read_named_resource(
        lambda: _get_service_account_by_name(service, plan.project_id, plan.service_account_name),
        failure_message="Failed to reread the managed VM-HA Service Account",
    )
    if plan.service_account_action == "reuse":
        if current is None:
            raise RuntimeError("managed VM-HA Service Account disappeared after approval")
        resource_id = _require_named_identity(
            current,
            kind="Managed VM-HA Service Account",
            project_id=plan.project_id,
            expected_name=plan.service_account_name,
            ownership_labels=labels,
        )
        if resource_id != plan.service_account_id:
            raise RuntimeError("managed VM-HA Service Account identity changed after approval")
        return resource_id
    if current is not None:
        raise RuntimeError("managed VM-HA Service Account appeared after approval")
    _wait_operation(
        service.create(
            CreateServiceAccountRequest(
                metadata=ResourceMetadata(
                    parent_id=plan.project_id,
                    name=plan.service_account_name,
                    labels=labels,
                ),
                spec=ServiceAccountSpec(description=_SERVICE_ACCOUNT_DESCRIPTION),
            )
        )
    )
    created = _read_named_resource(
        lambda: _get_service_account_by_name(service, plan.project_id, plan.service_account_name),
        failure_message="Created managed VM-HA Service Account could not be reread",
    )
    if created is None:
        raise RuntimeError("Created managed VM-HA Service Account could not be reread")
    return _require_named_identity(
        created,
        kind="Managed VM-HA Service Account",
        project_id=plan.project_id,
        expected_name=plan.service_account_name,
        ownership_labels=labels,
    )


def _reconcile_planned_group(
    service: t.Any,
    plan: VMHAIAMReconciliationPlan,
) -> str:
    from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
    from nebius.api.nebius.iam.v1 import CreateGroupRequest, GroupSpec  # type: ignore

    labels = dict(plan.ownership_labels)
    current = _read_named_resource(
        lambda: _get_group_by_name(service, plan.project_id, plan.service_account_name),
        failure_message="Failed to reread the managed VM-HA Service Account group",
    )
    if plan.group_action == "reuse":
        if current is None:
            raise RuntimeError("managed VM-HA Service Account group disappeared after approval")
        resource_id = _require_named_identity(
            current,
            kind="Managed VM-HA Service Account group",
            project_id=plan.project_id,
            expected_name=plan.service_account_name,
            ownership_labels=labels,
        )
        if resource_id != plan.group_id:
            raise RuntimeError("managed VM-HA Service Account group changed after approval")
        return resource_id
    if current is not None:
        raise RuntimeError("managed VM-HA Service Account group appeared after approval")
    _wait_operation(
        service.create(
            CreateGroupRequest(
                metadata=ResourceMetadata(
                    parent_id=plan.project_id,
                    name=plan.service_account_name,
                    labels=labels,
                ),
                spec=GroupSpec(),
            )
        )
    )
    created = _read_named_resource(
        lambda: _get_group_by_name(service, plan.project_id, plan.service_account_name),
        failure_message="Created managed VM-HA Service Account group could not be reread",
    )
    if created is None:
        raise RuntimeError("Created managed VM-HA Service Account group could not be reread")
    return _require_named_identity(
        created,
        kind="Managed VM-HA Service Account group",
        project_id=plan.project_id,
        expected_name=plan.service_account_name,
        ownership_labels=labels,
    )


def _reconcile_planned_membership(
    service: t.Any,
    plan: VMHAIAMReconciliationPlan,
    *,
    group_id: str,
    service_account_id: str,
) -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
    from nebius.api.nebius.iam.v1 import (  # type: ignore
        CreateGroupMembershipRequest,
        GroupMembershipSpec,
    )

    members = _list_group_member_ids(service, group_id)
    if plan.membership_action == "reuse":
        if members != (service_account_id,):
            raise RuntimeError("managed VM-HA group membership changed after approval")
        return
    if members:
        raise RuntimeError("managed VM-HA group membership appeared after approval")
    _wait_operation(
        service.create(
            CreateGroupMembershipRequest(
                metadata=ResourceMetadata(parent_id=group_id),
                spec=GroupMembershipSpec(member_id=service_account_id),
            )
        )
    )
    if _list_group_member_ids(service, group_id) != (service_account_id,):
        raise RuntimeError("managed VM-HA group membership did not reread exactly")


def _reconcile_planned_permit(
    service: t.Any,
    plan: VMHAIAMReconciliationPlan,
    *,
    group_id: str,
) -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
    from nebius.api.nebius.iam.v1 import (  # type: ignore
        AccessPermitSpec,
        CreateAccessPermitRequest,
    )

    expected = ((plan.project_id, "editor"),)
    permits = _list_access_permits(service, group_id)
    if plan.permit_action == "reuse":
        if permits != expected:
            raise RuntimeError("managed VM-HA project access permit changed after approval")
        return
    if permits:
        raise RuntimeError("managed VM-HA project access permit appeared after approval")
    _wait_operation(
        service.create(
            CreateAccessPermitRequest(
                metadata=ResourceMetadata(parent_id=group_id),
                spec=AccessPermitSpec(resource_id=plan.project_id, role="editor"),
            )
        )
    )
    if _list_access_permits(service, group_id) != expected:
        raise RuntimeError("managed VM-HA project access permit did not reread exactly")


def _close_client(client: t.Any) -> None:
    close = getattr(client, "sync_close", None)
    if callable(close):
        with suppress(Exception):
            close()


def _parse_access_token(stdout: str) -> str | None:
    token: t.Any = None
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        token = payload.get("access_token") or payload.get("token")
    elif isinstance(payload, str):
        token = payload
    elif payload is None:
        token = stdout.strip()
    if not isinstance(token, str):
        return None
    token = token.strip()
    if not token or len(token) > 65536 or re.search(r"\s", token):
        return None
    return token


def _cli_access_token(
    *,
    timeout_seconds: int,
    service_account_id: str | None = None,
    ignore_ambient_token: bool = False,
    no_browser: bool = False,
    process_timeout_seconds: float | None = None,
) -> str | None:
    command = ["nebius"]
    if service_account_id is not None:
        command.extend(["--impersonate-service-account-id", service_account_id])
    if service_account_id is not None or no_browser:
        command.append("--no-browser")
    command.extend(
        [
            "--format",
            "json",
            "--no-progress",
            "--auth-timeout",
            f"{timeout_seconds}s",
            "--timeout",
            f"{timeout_seconds}s",
            "--retries",
            "1",
            "iam",
            "get-access-token",
        ]
    )
    try:
        cli_environment = None
        if ignore_ambient_token:
            cli_environment = os.environ.copy()
            cli_environment.pop("NEBIUS_IAM_TOKEN", None)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=(
                process_timeout_seconds
                if process_timeout_seconds is not None
                else timeout_seconds + 5
            ),
            check=False,
            env=cli_environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_access_token(result.stdout)


def ensure_service_account_and_token(
    sa_name: str,
    tenant_id: str | None,
    project_id: str | None,
    region_id: str | None,
    *,
    role_ids: tuple[str, ...] = ("editor",),
    strict_role_grants: bool = True,
) -> str | None:
    """Ensure the exact reviewed identity and return its impersonated token.

    ``strict_role_grants`` remains in the internal signature for supported
    callers, but explicit Service Account selection is always fail-closed.
    """

    del strict_role_grants
    if project_id is None or not project_id.strip():
        raise ValueError("Service Account provisioning requires project_id")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", sa_name):
        raise ValueError("Service Account name must be a canonical Nebius resource name")
    roles = tuple(dict.fromkeys(role.strip() for role in role_ids if role.strip()))
    if roles != ("editor",):
        raise ValueError("Service Account provisioning accepts only project role 'editor'")

    return ensure_service_account_identity_and_token(
        sa_name,
        tenant_id,
        project_id,
        region_id,
        role_ids=roles,
    ).token


def ensure_service_account_identity_and_token(
    sa_name: str,
    tenant_id: str | None,
    project_id: str | None,
    region_id: str | None,
    *,
    role_ids: tuple[str, ...] = ("editor",),
    ownership_labels: t.Mapping[str, str] | None = None,
    reconciliation_plan: VMHAIAMReconciliationPlan | None = None,
) -> ServiceAccountTokenIdentity:
    """Ensure and return the exact selected identity plus its ephemeral token."""

    if project_id is None or not project_id.strip():
        raise ValueError("Service Account provisioning requires project_id")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", sa_name):
        raise ValueError("Service Account name must be a canonical Nebius resource name")
    roles = tuple(dict.fromkeys(role.strip() for role in role_ids if role.strip()))
    if roles != ("editor",):
        raise ValueError("Service Account provisioning accepts only project role 'editor'")
    if reconciliation_plan is not None and (
        reconciliation_plan.project_id != project_id
        or reconciliation_plan.service_account_name != sa_name
        or reconciliation_plan.ownership_labels != tuple(sorted((ownership_labels or {}).items()))
    ):
        raise ValueError("managed VM-HA IAM plan does not match this apply")

    client, _ = _init_client(tenant_id, project_id, region_id)
    try:
        service_accounts, groups, memberships, permits = _iam_services(client)
        if reconciliation_plan is None:
            service_account_id = _ensure_service_account(
                service_accounts,
                project_id,
                sa_name,
                ownership_labels=ownership_labels,
            )
            group_id = _ensure_group(
                groups,
                project_id,
                sa_name,
                ownership_labels=ownership_labels,
            )
            _ensure_exact_group_membership(memberships, group_id, service_account_id)
            _ensure_exact_project_permit(permits, group_id, project_id)
        else:
            service_account_id = _reconcile_planned_service_account(
                service_accounts, reconciliation_plan
            )
            group_id = _reconcile_planned_group(groups, reconciliation_plan)
            _reconcile_planned_membership(
                memberships,
                reconciliation_plan,
                group_id=group_id,
                service_account_id=service_account_id,
            )
            _reconcile_planned_permit(
                permits,
                reconciliation_plan,
                group_id=group_id,
            )
    finally:
        _close_client(client)

    token = _cli_access_token(
        timeout_seconds=60,
        service_account_id=service_account_id,
    )
    if token is None:
        raise RuntimeError("Nebius CLI could not obtain an impersonated Service Account token")
    return ServiceAccountTokenIdentity(
        token=token,
        service_account_id=service_account_id,
        service_account_name=sa_name,
    )


def ensure_vm_ha_service_account_and_token(
    sa_name: str,
    tenant_id: str | None,
    project_id: str | None,
    region_id: str | None,
    *,
    verified_role_ids: tuple[str, ...],
) -> str | None:
    """Provision VM-HA identity only from the reviewed current project role."""

    roles = tuple(dict.fromkeys(role.strip() for role in verified_role_ids if role.strip()))
    if not roles:
        raise ValueError("VM HA service-account provisioning requires verified role IDs")
    unexpected = sorted(set(roles) - VM_HA_ROLE_ALLOWLIST)
    if unexpected:
        raise ValueError(f"VM HA rejects roles outside its reviewed allowlist: {unexpected}")
    if set(roles) != VM_HA_ROLE_ALLOWLIST:
        raise ValueError("VM HA requires the reviewed project role 'editor'")
    return ensure_service_account_and_token(
        sa_name,
        tenant_id,
        project_id,
        region_id,
        role_ids=roles,
        strict_role_grants=True,
    )


def ensure_vm_ha_service_account_identity_and_token(
    sa_name: str,
    tenant_id: str | None,
    project_id: str | None,
    region_id: str | None,
    *,
    verified_role_ids: tuple[str, ...],
    ownership_labels: t.Mapping[str, str] | None = None,
    reconciliation_plan: VMHAIAMReconciliationPlan | None = None,
) -> ServiceAccountTokenIdentity:
    """Provision and return the exact reviewed VM-HA service account identity."""

    roles = tuple(dict.fromkeys(role.strip() for role in verified_role_ids if role.strip()))
    if not roles:
        raise ValueError("VM HA service-account provisioning requires verified role IDs")
    unexpected = sorted(set(roles) - VM_HA_ROLE_ALLOWLIST)
    if unexpected:
        raise ValueError(f"VM HA rejects roles outside its reviewed allowlist: {unexpected}")
    if set(roles) != VM_HA_ROLE_ALLOWLIST:
        raise ValueError("VM HA requires the reviewed project role 'editor'")
    return ensure_service_account_identity_and_token(
        sa_name,
        tenant_id,
        project_id,
        region_id,
        role_ids=roles,
        ownership_labels=ownership_labels,
        reconciliation_plan=reconciliation_plan,
    )


def get_cli_token() -> str | None:
    """Attempt to read an IAM token from Nebius CLI config via SDK's Config reader.

    Returns a token string if discoverable, else None.
    """
    try:
        from nebius.aio.cli_config import Config  # type: ignore
    except Exception:
        return None

    try:
        cfg = Config(no_parent_id=True)
    except Exception:
        return None

    # Try common attribute/method names defensively
    try:
        if hasattr(cfg, "token"):
            tok = cfg.token
            if callable(tok):
                tok = tok()  # type: ignore[call-arg]
            return tok  # type: ignore[return-value]
    except Exception:
        pass
    try:
        if hasattr(cfg, "access_token"):
            at = cfg.access_token
            if callable(at):
                at = at()  # type: ignore[call-arg]
            return at  # type: ignore[return-value]
    except Exception:
        pass
    # Some configs expose a dict-like interface
    try:
        tok = getattr(cfg, "get", None)
        if callable(tok):
            val = tok("token")  # type: ignore[call-arg]
            if val:
                return val
    except Exception:
        pass
    return None


def ensure_cli_access_token(timeout_seconds: int = 60) -> str | None:
    """Obtain an IAM access token using Nebius CLI config.

    Order:
    1) Try the SDK Config reader (fast path).
    2) Invoke the supported Nebius CLI access-token command.

    Args:
        timeout_seconds: Maximum time to wait for authentication (default: 60s)

    Returns:
        Token string if successful, None if auth fails or times out
    """
    # 1) Config reader fast path
    tok = get_cli_token()
    if tok:
        return tok

    return _cli_access_token(timeout_seconds=timeout_seconds)
