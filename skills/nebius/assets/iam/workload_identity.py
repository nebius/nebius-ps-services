"""Exact Kubernetes OIDC trust; callers own authority and mutation reconciliation."""

from urllib.parse import urlsplit

from sdk.runtime import CloudError, rpc, submit, verify_identity


def federation_request(
    *, project_id, name, issuer, namespace, service_account, iam_service_account_id
):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.iam.v1 import (
        CreateFederatedCredentialsRequest,
        FederatedCredentialsSpec,
        OidcCredentialsProvider,
    )

    url = urlsplit(issuer)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Use the exact HTTPS issuer from the cluster discovery document")
    if any(not v or ":" in v or "/" in v for v in (namespace, service_account)):
        raise ValueError("Exact Kubernetes namespace and service account are required")
    return CreateFederatedCredentialsRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=name),
        spec=FederatedCredentialsSpec(
            subject_id=iam_service_account_id,
            federated_subject_id=f"system:serviceaccount:{namespace}:{service_account}",
            oidc_provider=OidcCredentialsProvider(issuer_url=issuer),
        ),
    )


def ensure_federation(*, sdk, existing_id=None, **settings):
    """Create once or adopt an exact ID. Never replace a differing trust resource."""
    from nebius.api.nebius.iam.v1 import (
        FederatedCredentialsServiceClient,
        GetFederatedCredentialsRequest,
        GetServiceAccountRequest,
        ServiceAccountServiceClient,
    )

    expected = federation_request(**settings)
    account = rpc(
        ServiceAccountServiceClient(sdk).get,
        GetServiceAccountRequest(id=settings["iam_service_account_id"]),
    )
    verify_identity(
        account, parent_id=settings["project_id"], resource_id=settings["iam_service_account_id"]
    )
    client = FederatedCredentialsServiceClient(sdk)
    result = None if existing_id else submit(client.create, expected)
    identity = existing_id or result.resource_id
    try:
        found = rpc(client.get, GetFederatedCredentialsRequest(id=identity))
        verify_identity(
            found, parent_id=settings["project_id"], name=settings["name"], resource_id=identity
        )
        if (
            found.spec.subject_id != expected.spec.subject_id
            or found.spec.federated_subject_id != expected.spec.federated_subject_id
            or found.spec.oidc_provider.issuer_url != expected.spec.oidc_provider.issuer_url
            or found.spec.oidc_provider.jwk_set_json
        ):
            raise CloudError("FEDERATION_TRUST_MISMATCH")
        return identity
    # SDK/readback errors must not expose provider details; retain recovery identity.
    except Exception:  # noqa: BLE001
        raise CloudError(
            "FEDERATION_READBACK_FAILED",
            resource_id=identity,
            operation_id=result.operation_id if result else "",
            outcome="reconcile-required",
        ) from None
