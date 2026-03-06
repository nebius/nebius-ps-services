"""Snippet: create (or reuse) a Nebius Service Account by name."""

from __future__ import annotations

from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.iam.v1 import (
    CreateServiceAccountRequest,
    GetServiceAccountByNameRequest,
    ServiceAccountServiceClient,
    ServiceAccountSpec,
)


def _wait(op_or_message):  # type: ignore[no-untyped-def]
    return op_or_message.wait() if hasattr(op_or_message, "wait") else op_or_message


def _is_not_found_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "not found" in message or "statuscode.not_found" in message


def _is_already_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "statuscode.already_exists" in message


def ensure_service_account(
    *,
    sdk,
    project_id: str,
    service_account_name: str,
    description: str = "Service account managed by automation",
) -> tuple[str, bool]:
    """Return (service_account_id, created_now)."""
    service_accounts = ServiceAccountServiceClient(sdk)

    try:
        existing = _wait(
            service_accounts.get_by_name(
                GetServiceAccountByNameRequest(parent_id=project_id, name=service_account_name)
            )
        )
        existing_id = getattr(getattr(existing, "metadata", None), "id", "")
        if existing_id:
            return existing_id, False
    except Exception as exc:
        if not _is_not_found_error(exc):
            raise

    try:
        operation = _wait(
            service_accounts.create(
                CreateServiceAccountRequest(
                    metadata=ResourceMetadata(parent_id=project_id, name=service_account_name),
                    spec=ServiceAccountSpec(description=description),
                )
            )
        )
    except Exception as exc:
        if _is_already_exists_error(exc):
            existing = _wait(
                service_accounts.get_by_name(
                    GetServiceAccountByNameRequest(parent_id=project_id, name=service_account_name)
                )
            )
            existing_id = getattr(getattr(existing, "metadata", None), "id", "")
            if existing_id:
                return existing_id, False
        raise

    service_account_id = getattr(operation, "resource_id", "")
    if service_account_id:
        return service_account_id, True

    existing = _wait(
        service_accounts.get_by_name(
            GetServiceAccountByNameRequest(parent_id=project_id, name=service_account_name)
        )
    )
    existing_id = getattr(getattr(existing, "metadata", None), "id", "")
    if existing_id:
        return existing_id, True
    raise RuntimeError("Service account created but ID could not be resolved")
