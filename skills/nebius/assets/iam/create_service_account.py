"""Create a named service account or reuse an explicitly identified account."""

from sdk.runtime import CloudError, rpc, submit, verify_identity


def ensure_service_account(
    *,
    sdk,
    project_id: str,
    service_account_name: str,
    existing_service_account_id: str | None = None,
    description: str = "Service account managed by automation",
) -> tuple[str, bool]:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import (
        CreateServiceAccountRequest,
        GetServiceAccountByNameRequest,
        GetServiceAccountRequest,
        ServiceAccountServiceClient,
        ServiceAccountSpec,
    )

    client = ServiceAccountServiceClient(sdk)
    try:
        account = rpc(
            client.get_by_name,
            GetServiceAccountByNameRequest(
                parent_id=project_id, name=service_account_name
            ),
        )
    except CloudError as exc:
        if exc.code != "NOT_FOUND" or existing_service_account_id is not None:
            raise
    else:
        if existing_service_account_id is None:
            raise CloudError("EXPLICIT_ACCOUNT_ADOPTION_REQUIRED")
        return verify_identity(
            account,
            parent_id=project_id,
            name=service_account_name,
            resource_id=existing_service_account_id,
        ), False
    result = submit(
        client.create,
        CreateServiceAccountRequest(
            metadata=ResourceMetadata(parent_id=project_id, name=service_account_name),
            spec=ServiceAccountSpec(description=description),
        ),
    )
    try:
        account = rpc(client.get, GetServiceAccountRequest(id=result.resource_id))
        return verify_identity(
            account,
            parent_id=project_id,
            name=service_account_name,
            resource_id=result.resource_id,
        ), True
    except Exception:  # noqa: BLE001 - Redact provider details at this boundary.
        raise CloudError(
            "ACCOUNT_READBACK_FAILED",
            resource_id=result.resource_id,
            operation_id=result.operation_id,
            outcome="reconcile-required",
        ) from None
