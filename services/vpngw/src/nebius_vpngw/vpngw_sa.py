from __future__ import annotations

import json
import re
import subprocess
import typing as t
from contextlib import suppress

"""
Service Account management for Nebius VPNGW.

This module ensures the exact dedicated Service Account, custom group,
membership, and project permission used by an explicit ``apply --sa``. It uses
the generated IAM clients from the pinned Nebius SDK and obtains only a
short-lived impersonated token from the installed Nebius CLI. It never creates
or persists runtime authorized-key material.
"""


# Current Nebius IAM does not expose service-specific Compute/VPC editor roles,
# and access permits cannot be attached directly to all VPC resource types.
# Project-level editor is therefore the narrowest current role boundary that
# covers the required Compute ownership and VPC allocation/route mutations.
VM_HA_ROLE_ALLOWLIST = frozenset({"editor"})
_SERVICE_ACCOUNT_DESCRIPTION = "Nebius VPNGW orchestrator"
_MAX_IAM_PAGES = 100
_IAM_PAGE_SIZE = 1000


def _init_client(tenant_id: str | None, project_id: str | None, region_id: str | None):
    """Initialize the current SDK with the authenticated Nebius CLI profile."""

    del tenant_id, project_id, region_id
    try:
        from nebius.aio.cli_config import Config  # type: ignore
        from nebius.sdk import SDK  # type: ignore

        return SDK(config_reader=Config(no_parent_id=True)), True
    except Exception as error:  # pragma: no cover - runtime import/config guard
        raise RuntimeError(
            "Failed to initialize the Nebius SDK from the CLI profile; run "
            "'nebius auth login' and retry."
        ) from error


def _wait(value: t.Any) -> t.Any:
    return value.wait() if hasattr(value, "wait") else value


def _error_code_name(error: Exception) -> str:
    status = getattr(error, "status", None)
    code = getattr(status, "code", None)
    name = getattr(code, "name", None)
    if isinstance(name, str):
        return name.upper()
    return ""


def _is_not_found_error(error: Exception) -> bool:
    return _error_code_name(error) == "NOT_FOUND" or "NOT_FOUND" in str(error).upper()


def _is_already_exists_error(error: Exception) -> bool:
    return _error_code_name(error) == "ALREADY_EXISTS" or "ALREADY_EXISTS" in str(
        error
    ).upper()


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
) -> str:
    resource_id, parent_id, name = _resource_identity(resource)
    if not resource_id or parent_id != project_id or name != expected_name:
        raise RuntimeError(f"{kind} identity did not reread exactly")
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


def _get_service_account_by_name(service: t.Any, project_id: str, name: str) -> t.Any:
    from nebius.api.nebius.iam.v1 import GetServiceAccountByNameRequest  # type: ignore

    return _wait(
        service.get_by_name(GetServiceAccountByNameRequest(parent_id=project_id, name=name))
    )


def _ensure_service_account(service: t.Any, project_id: str, name: str) -> str:
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
        )

    try:
        _wait(
            service.create(
                CreateServiceAccountRequest(
                    metadata=ResourceMetadata(parent_id=project_id, name=name),
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
    )


def _get_group_by_name(service: t.Any, project_id: str, name: str) -> t.Any:
    from nebius.api.nebius.iam.v1 import GetGroupByNameRequest  # type: ignore

    return _wait(service.get_by_name(GetGroupByNameRequest(parent_id=project_id, name=name)))


def _ensure_group(service: t.Any, project_id: str, name: str) -> str:
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
        )

    try:
        _wait(
            service.create(
                CreateGroupRequest(
                    metadata=ResourceMetadata(parent_id=project_id, name=name),
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
    )


def _list_group_member_ids(service: t.Any, group_id: str) -> tuple[str, ...]:
    from nebius.api.nebius.iam.v1 import ListGroupMembershipsRequest  # type: ignore

    members: list[str] = []
    page_token = ""
    seen_tokens: set[str] = set()
    for _ in range(_MAX_IAM_PAGES):
        response = _wait(
            service.list_members(
                ListGroupMembershipsRequest(
                    parent_id=group_id,
                    page_size=_IAM_PAGE_SIZE,
                    page_token=page_token,
                )
            )
        )
        for membership in list(getattr(response, "memberships", []) or []):
            member_id = str(getattr(getattr(membership, "spec", None), "member_id", "") or "")
            if not member_id:
                raise RuntimeError("Service Account group contains a malformed membership")
            members.append(member_id)
        next_page_token = str(getattr(response, "next_page_token", "") or "")
        if not next_page_token:
            return tuple(members)
        if next_page_token in seen_tokens:
            raise RuntimeError("Service Account group pagination repeated a token")
        seen_tokens.add(next_page_token)
        page_token = next_page_token
    raise RuntimeError("Service Account group pagination exceeded its bound")


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
        _wait(
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
    page_token = ""
    seen_tokens: set[str] = set()
    for _ in range(_MAX_IAM_PAGES):
        response = _wait(
            service.list(
                ListAccessPermitRequest(
                    parent_id=group_id,
                    page_size=_IAM_PAGE_SIZE,
                    page_token=page_token,
                )
            )
        )
        for permit in list(getattr(response, "items", []) or []):
            spec = getattr(permit, "spec", None)
            resource_id = str(getattr(spec, "resource_id", "") or "")
            role = str(getattr(spec, "role", "") or "")
            if not resource_id or not role:
                raise RuntimeError("Dedicated Service Account group has a malformed access permit")
            permits.append((resource_id, role))
        next_page_token = str(getattr(response, "next_page_token", "") or "")
        if not next_page_token:
            return tuple(permits)
        if next_page_token in seen_tokens:
            raise RuntimeError("Service Account permit pagination repeated a token")
        seen_tokens.add(next_page_token)
        page_token = next_page_token
    raise RuntimeError("Service Account permit pagination exceeded its bound")


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
        _wait(
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
) -> str | None:
    command = ["nebius"]
    if service_account_id is not None:
        command.extend(["--impersonate-service-account-id", service_account_id, "--no-browser"])
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
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 5,
            check=False,
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

    client, _ = _init_client(tenant_id, project_id, region_id)
    try:
        service_accounts, groups, memberships, permits = _iam_services(client)
        service_account_id = _ensure_service_account(service_accounts, project_id, sa_name)
        group_id = _ensure_group(groups, project_id, sa_name)
        _ensure_exact_group_membership(memberships, group_id, service_account_id)
        _ensure_exact_project_permit(permits, group_id, project_id)
    finally:
        _close_client(client)

    token = _cli_access_token(
        timeout_seconds=60,
        service_account_id=service_account_id,
    )
    if token is None:
        raise RuntimeError("Nebius CLI could not obtain an impersonated Service Account token")
    return token


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
