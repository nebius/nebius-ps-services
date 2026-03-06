"""Snippet: create an Object Storage access key for a Service Account."""

from __future__ import annotations

from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.iam.v1 import Account
from nebius.api.nebius.iam.v2 import (
    AccessKeyServiceClient,
    AccessKeySpec,
    CreateAccessKeyRequest,
    GetAccessKeyRequest,
    GetAccessKeySecretRequest,
)


def _wait(op_or_message):  # type: ignore[no-untyped-def]
    return op_or_message.wait() if hasattr(op_or_message, "wait") else op_or_message


def create_object_storage_access_key(
    *,
    sdk,
    project_id: str,
    service_account_id: str,
    description: str = "Object Storage key managed by automation",
) -> tuple[str, str, str]:
    """
    Returns (access_key_id, aws_access_key_id, s3_secret_access_key).

    `s3_secret_access_key` is sensitive and may only be returned once.
    """
    access_keys = AccessKeyServiceClient(sdk)
    operation = _wait(
        access_keys.create(
            CreateAccessKeyRequest(
                metadata=ResourceMetadata(parent_id=project_id),
                spec=AccessKeySpec(
                    account=Account(
                        service_account=Account.ServiceAccount(id=service_account_id),
                    ),
                    description=description,
                ),
            )
        )
    )

    access_key_id = str(getattr(operation, "resource_id", "") or "").strip()
    if not access_key_id:
        raise RuntimeError("Access key was created but ID could not be resolved")

    secret_response = _wait(
        access_keys.get_secret(GetAccessKeySecretRequest(id=access_key_id))
    )
    aws_access_key_id = str(getattr(secret_response, "aws_access_key_id", "") or "").strip()
    s3_secret_access_key = str(getattr(secret_response, "secret", "") or "").strip()

    if not aws_access_key_id:
        # Fallback for SDK variants where aws_access_key_id is only visible on GetAccessKey.
        key_obj = _wait(access_keys.get(GetAccessKeyRequest(id=access_key_id)))
        aws_access_key_id = str(
            getattr(getattr(key_obj, "status", None), "aws_access_key_id", "") or ""
        ).strip()

    if not aws_access_key_id or not s3_secret_access_key:
        raise RuntimeError("Access key secret response is missing aws_access_key_id or secret")
    return access_key_id, aws_access_key_id, s3_secret_access_key
