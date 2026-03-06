"""Snippet: create an authorized key for a Nebius Service Account."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.iam.v1 import (
    Account,
    AuthPublicKeyServiceClient,
    AuthPublicKeySpec,
    CreateAuthPublicKeyRequest,
)


def _wait(op_or_message):  # type: ignore[no-untyped-def]
    return op_or_message.wait() if hasattr(op_or_message, "wait") else op_or_message


def _generate_rsa_key_pair() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    # Nebius IAM expects PEM public key data in AuthPublicKeySpec.data.
    return private_pem, public_pem


def create_authorized_key_for_service_account(
    *,
    sdk,
    project_id: str,
    service_account_id: str,
    description: str = "Authorized key managed by automation",
) -> tuple[str, str]:
    """
    Returns (auth_public_key_id, private_key_pem).

    Persist `private_key_pem` immediately in a secure location (`0600`).
    """
    private_key_pem, public_pem = _generate_rsa_key_pair()
    auth_keys = AuthPublicKeyServiceClient(sdk)
    operation = _wait(
        auth_keys.create(
            CreateAuthPublicKeyRequest(
                metadata=ResourceMetadata(parent_id=project_id),
                spec=AuthPublicKeySpec(
                    account=Account(
                        service_account=Account.ServiceAccount(id=service_account_id),
                    ),
                    description=description,
                    data=public_pem,
                ),
            )
        )
    )

    auth_public_key_id = str(getattr(operation, "resource_id", "") or "").strip()
    if not auth_public_key_id:
        raise RuntimeError("Authorized key was created but key ID could not be resolved")
    return auth_public_key_id, private_key_pem
