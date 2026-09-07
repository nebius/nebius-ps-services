"""Issue once, or resume secret retrieval by explicit access-key ID. No secret output."""

from dataclasses import dataclass, field

from sdk.runtime import CloudError, rpc, submit, verify_identity


@dataclass(frozen=True)
class AccessKeyMaterial:
    resource_id: str
    aws_access_key_id: str = field(repr=False)
    secret: str = field(repr=False)


def create_object_storage_access_key(
    *,
    sdk,
    project_id: str,
    service_account_id: str,
    existing_access_key_id: str | None = None,
    description: str = "Object Storage key managed by automation",
):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import (
        Account,
        GetServiceAccountRequest,
        ServiceAccountServiceClient,
    )
    from nebius.api.nebius.iam.v2 import (
        AccessKeyServiceClient,
        AccessKeySpec,
        CreateAccessKeyRequest,
        GetAccessKeyRequest,
        GetAccessKeySecretRequest,
    )

    account = rpc(
        ServiceAccountServiceClient(sdk).get,
        GetServiceAccountRequest(id=service_account_id),
    )
    verify_identity(account, parent_id=project_id, resource_id=service_account_id)
    keys = AccessKeyServiceClient(sdk)
    operation_id = ""
    key_id = existing_access_key_id
    if key_id is None:
        result = submit(
            keys.create,
            CreateAccessKeyRequest(
                metadata=ResourceMetadata(parent_id=project_id),
                spec=AccessKeySpec(
                    account=Account(
                        service_account=Account.ServiceAccount(id=service_account_id)
                    ),
                    description=description,
                ),
            ),
        )
        key_id, operation_id = result.resource_id, result.operation_id
    try:
        key = rpc(keys.get, GetAccessKeyRequest(id=key_id))
        verify_identity(key, parent_id=project_id, resource_id=key_id)
        if key.spec.account.service_account.id != service_account_id:
            raise CloudError("ACCESS_KEY_ACCOUNT_MISMATCH")
        secret = rpc(keys.get_secret, GetAccessKeySecretRequest(id=key_id))
        if not secret.aws_access_key_id or not secret.secret:
            raise CloudError("ACCESS_KEY_SECRET_UNAVAILABLE")
        return AccessKeyMaterial(key_id, secret.aws_access_key_id, secret.secret)
    except Exception as exc:  # noqa: BLE001 - Sanitize errors and retain reconciliation IDs.
        from sdk.runtime import error_code

        raise CloudError(
            error_code(exc),
            resource_id=key_id,
            operation_id=operation_id,
            outcome="reconcile-required",
        ) from None
