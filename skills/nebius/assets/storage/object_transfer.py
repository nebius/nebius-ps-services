"""Bounded regular-bucket S3 examples. Caller supplies an authenticated boto3 client.

Do not use ETags as content hashes. Payloads and credentials are never logged.
Use a managed multipart transfer workflow for large datasets, not this memory example.
"""

import base64
import hashlib

from sdk.runtime import CloudError

MAX_BYTES = 64 * 1024 * 1024


def upload_bytes(s3, *, bucket: str, key: str, data: bytes) -> dict:
    """One explicit data write, without application-level retries."""
    if len(data) > MAX_BYTES:
        raise ValueError("example supports at most 64 MiB")
    digest = hashlib.sha256(data).digest()
    try:
        response = s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ChecksumSHA256=base64.b64encode(digest).decode(),
        )
    except Exception:  # noqa: BLE001 - Redact provider details at this boundary.
        raise CloudError(
            "OBJECT_WRITE_UNCERTAIN", outcome="reconcile-required"
        ) from None
    return {"sha256": digest.hex(), "version_id": response.get("VersionId")}


def download_verified(
    s3, *, bucket: str, key: str, sha256: str, version_id: str | None = None
) -> bytes:
    """Verify against a trusted checksum; optional version pins the object read."""
    params = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    try:
        response = s3.get_object(**params)
        body = response["Body"]
        try:
            data = body.read(MAX_BYTES + 1)
        finally:
            body.close()
    except Exception:  # noqa: BLE001 - Redact provider details at this boundary.
        raise CloudError("OBJECT_READ_FAILED") from None
    if len(data) > MAX_BYTES:
        raise CloudError("OBJECT_SIZE_LIMIT")
    if hashlib.sha256(data).hexdigest() != sha256:
        raise CloudError("OBJECT_CHECKSUM_MISMATCH")
    return data
