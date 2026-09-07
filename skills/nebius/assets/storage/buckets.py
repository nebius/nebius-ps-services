"""Versioned bucket example: explicit adoption, readiness and configuration verification."""

from dataclasses import dataclass

from sdk.runtime import (
    CloudError,
    positive_seconds,
    rpc,
    submit,
    verify_identity,
    wait_ready,
)


@dataclass(frozen=True)
class BucketResult:
    resource_id: str
    created: bool
    operation_id: str = ""


def ensure_versioned_bucket(
    *,
    sdk,
    project_id: str,
    bucket_name: str,
    existing_bucket_id: str | None = None,
    ready_timeout: float = 90.0,
) -> BucketResult:
    positive_seconds(ready_timeout)
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.storage.v1 import (
        BucketServiceClient,
        BucketSpec,
        BucketStatus,
        CreateBucketRequest,
        GetBucketByNameRequest,
        GetBucketRequest,
        VersioningPolicy,
    )

    client = BucketServiceClient(sdk)
    try:
        bucket = rpc(
            client.get_by_name,
            GetBucketByNameRequest(parent_id=project_id, name=bucket_name),
        )
    except CloudError as exc:
        if exc.code != "NOT_FOUND" or existing_bucket_id is not None:
            raise
        result = submit(
            client.create,
            CreateBucketRequest(
                metadata=ResourceMetadata(parent_id=project_id, name=bucket_name),
                spec=BucketSpec(versioning_policy=VersioningPolicy.ENABLED),
            ),
        )
        outcome = BucketResult(result.resource_id, True, result.operation_id)
    else:
        if existing_bucket_id is None:
            raise CloudError("EXPLICIT_BUCKET_ADOPTION_REQUIRED")
        verify_identity(
            bucket,
            parent_id=project_id,
            resource_id=existing_bucket_id,
            name=bucket_name,
        )
        outcome = BucketResult(existing_bucket_id, False)

    def acceptable(bucket):
        verify_identity(
            bucket,
            parent_id=project_id,
            resource_id=outcome.resource_id,
            name=bucket_name,
        )
        if bucket.spec.versioning_policy != VersioningPolicy.ENABLED:
            raise CloudError("BUCKET_VERSIONING_MISMATCH")
        return bucket.status.state == BucketStatus.State.ACTIVE

    try:
        wait_ready(
            lambda timeout: rpc(
                client.get, GetBucketRequest(id=outcome.resource_id), timeout=timeout
            ),
            acceptable,
            timeout=ready_timeout,
        )
    except Exception as exc:  # noqa: BLE001 - Sanitize errors and retain reconciliation IDs.
        from sdk.runtime import error_code

        raise CloudError(
            error_code(exc),
            resource_id=outcome.resource_id,
            operation_id=outcome.operation_id,
            outcome="reconcile-required",
        ) from None
    return outcome
