"""Register a caller-owned public key; never generate or retain a private key here."""

from sdk.runtime import rpc, submit, verify_identity


def create_authorized_key_for_service_account(
    *,
    sdk,
    project_id: str,
    service_account_id: str,
    public_key_pem: str,
    description: str = "Authorized key managed by automation",
):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import (
        Account,
        AuthPublicKeyServiceClient,
        AuthPublicKeySpec,
        CreateAuthPublicKeyRequest,
        GetServiceAccountRequest,
        ServiceAccountServiceClient,
    )

    load_pem_public_key(public_key_pem.encode())
    account = rpc(
        ServiceAccountServiceClient(sdk).get,
        GetServiceAccountRequest(id=service_account_id),
    )
    verify_identity(account, parent_id=project_id, resource_id=service_account_id)
    return submit(
        AuthPublicKeyServiceClient(sdk).create,
        CreateAuthPublicKeyRequest(
            metadata=ResourceMetadata(parent_id=project_id),
            spec=AuthPublicKeySpec(
                account=Account(
                    service_account=Account.ServiceAccount(id=service_account_id)
                ),
                description=description,
                data=public_key_pem,
            ),
        ),
    )
