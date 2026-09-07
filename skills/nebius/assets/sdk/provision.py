"""Explicit create -> terminal operation -> identity and readiness verification."""

from sdk.runtime import (
    CloudError,
    error_code,
    positive_seconds,
    rpc,
    submit,
    verify_identity,
    wait_ready,
)


def create_and_verify(
    client,
    request,
    get_request_type,
    *,
    ready,
    operation_timeout: float = 300.0,
    ready_timeout: float = 90.0,
):
    """Caller preflights dependencies/quotas and supplies a service-specific readiness predicate.

    `ready(resource)` must verify relevant spec and runtime postconditions, not
    merely existence. There is no automatic adoption, rollback, or write retry.
    """
    parent_id, name = request.metadata.parent_id, request.metadata.name
    if not parent_id or not name:
        raise ValueError("explicit parent and resource name required")
    positive_seconds(operation_timeout)
    positive_seconds(ready_timeout)
    if not callable(ready) or not callable(get_request_type):
        raise TypeError(
            "callable readiness predicate and readback request factory required"
        )
    result = submit(client.create, request, timeout=operation_timeout)

    def verified(resource):
        verify_identity(
            resource, parent_id=parent_id, resource_id=result.resource_id, name=name
        )
        return ready(resource)

    try:
        wait_ready(
            lambda timeout: rpc(
                client.get, get_request_type(id=result.resource_id), timeout=timeout
            ),
            verified,
            timeout=ready_timeout,
        )
    except Exception as exc:  # noqa: BLE001 - Sanitize errors and retain reconciliation IDs.
        raise CloudError(
            error_code(exc),
            resource_id=result.resource_id,
            operation_id=result.operation_id,
            outcome="reconcile-required",
        ) from None
    return result
