"""Read-only admission for IAM identities missing from Terraform state."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from typing import Any

from .nebius_api_helpers import bounded_nebius_request_kwargs
from .sdk_auth import init_nebius_sdk


def assert_service_account_names_available(plan: Mapping[str, Any]) -> None:
    """Reject fresh creates colliding with untracked accounts; never infer ownership."""
    candidates: list[tuple[str, str, str]] = []
    for resource in plan.get("resource_changes", []):
        change = resource.get("change", {})
        if (
            resource.get("mode", "managed") != "managed"
            or resource.get("type") != "nebius_iam_v1_service_account"
            or change.get("actions") != ["create"]
        ):
            continue
        desired = change.get("after") or {}
        project, name = desired.get("parent_id"), desired.get("name")
        address = resource.get("address")
        if (
            not isinstance(project, str)
            or not project.strip()
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(address, str)
            or not address.strip()
        ):
            raise RuntimeError(
                "Cannot check a planned service account without project, name and address"
            )
        candidates.append((project, name, address))
    if not candidates:
        return

    from nebius.aio.service_error import RequestError
    from nebius.api.nebius.iam.v1 import (
        GetServiceAccountByNameRequest,
        ServiceAccountServiceClient,
    )

    collisions = []
    with ExitStack() as stack:
        clients: dict[str, Any] = {}
        for project, name, address in candidates:
            account = None
            try:
                if project not in clients:
                    sdk = init_nebius_sdk(parent_id=project, context="Deploy IAM admission")
                    stack.callback(sdk.sync_close)
                    clients[project] = ServiceAccountServiceClient(sdk)
                account = (
                    clients[project]
                    .get_by_name(
                        GetServiceAccountByNameRequest(parent_id=project, name=name),
                        **bounded_nebius_request_kwargs(timeout_seconds=20),
                    )
                    .wait()
                )
            except Exception as exc:
                if isinstance(exc, RequestError) and exc.status.code.name == "NOT_FOUND":
                    continue
            if account is None:
                # Raise outside the handler: CLI wrappers traverse exception contexts,
                # including contexts suppressed with `from None`.
                raise RuntimeError(
                    f"Cannot verify IAM name availability for {address}; "
                    "check project access and retry deploy. This plan was not applied."
                )
            metadata = getattr(account, "metadata", None)
            identifier = getattr(metadata, "id", None)
            if (
                not identifier
                or getattr(metadata, "parent_id", None) != project
                or getattr(metadata, "name", None) != name
            ):
                raise RuntimeError(
                    f"IAM lookup returned an incomplete or conflicting identity for {address}"
                )
            collisions.append(f"  {address}: {name} -> {identifier}")
    if collisions:
        raise RuntimeError(
            "Deploy found existing service accounts that Terraform plans to create:\n"
            + "\n".join(collisions)
            + "\nA missing or deleted state backend does not delete cloud resources. "
            "Restore the original Terraform state, or verify ownership and import the exact "
            "account IDs at these addresses before retrying. Names alone do not authorize "
            "reuse or deletion. This plan was not applied."
        )
