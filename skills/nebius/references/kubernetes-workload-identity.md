# Kubernetes workload identity for Nebius services

Use this pattern when a Kubernetes workload needs a Nebius **IAM-authenticated
API**, including direct SecretStash payload reads. It removes stored Nebius
service-account private keys. The application still owns any external service
secret, its refresh policy and authorization. Human federation/SSO is a different
flow. S3 object requests use the S3 authentication contract; an IAM bearer alone
does not make boto3 work.

## Trust and authorization

The trust resource binds the exact cluster issuer, Kubernetes subject
`system:serviceaccount:<namespace>:<service-account>` and Nebius IAM service
account ID. A dedicated IAM group contains that IAM account; access permits on
that group grant resource-scoped roles. Trust does not grant permissions.
Keep the IAM principal and target service in compatible regions. SecretStash
payload access requires `mysterybox.payload-viewer`; `mysterybox.editor` alone
does not grant it. Grant it on the exact secret, not the project by default.

The platform owner provisions the Kubernetes ServiceAccount and IAM trust;
the resource owner approves the access permit. The application owner handles
secret refresh. Terraform or another declared lifecycle owner should manage
these resources when already in use; do not add a second SDK writer.

## Provision with the Python SDK

1. Select the exact cluster context. Obtain its advertised issuer:

   ```bash
   kubectl --context "$CONTEXT" get --raw /.well-known/openid-configuration | jq -er .issuer
   ```

   Do not derive it from the Kubernetes API URL. Managed Kubernetes exposes the
   public issuer at `https://oidc.mk8s.api.nebius.cloud/<cluster_ID>`. Verify that
   its discovery document returns that same issuer and that its `jwks_uri` is
   reachable by Nebius STS. This does not require exposing the Kubernetes API.
   For another Kubernetes provider, verify its public issuer/JWKS or supported
   trust mechanism. Do not disable TLS validation.

2. Create/select an explicit namespace and Kubernetes ServiceAccount. Set
   `automountServiceAccountToken: false` and use the projected token below.
   Restrict who can create pods or request tokens as that ServiceAccount: they
   can exercise the workload's cloud authority.

3. Use the SDK assets on `PYTHONPATH=assets` with operator credentials. Select
   an explicit project and SDK profile/config; do not shell out for tokens.
   The existing `ensure_service_account` helper requires an exact ID to adopt
   an existing account. `create_dedicated_group` creates a new dedicated group
   and membership and returns its ID. Record returned resource/operation IDs;
   reconcile uncertain mutations before resubmitting them.

   ```python
   from sdk.runtime import owned_sdk, submit
   from iam.create_service_account import ensure_service_account
   from iam.grant_project_roles import create_dedicated_group
   from iam.workload_identity import ensure_federation
   from nebius.api.nebius.common.v1 import ResourceMetadata
   from nebius.api.nebius.iam.v1 import (
       AccessPermitServiceClient, AccessPermitSpec, CreateAccessPermitRequest,
   )

   with owned_sdk(parent_id=project_id, profile=operator_profile) as sdk:
       iam_sa, created = ensure_service_account(
           sdk=sdk, project_id=project_id, service_account_name=workload_name,
           existing_service_account_id=existing_iam_sa_id,
       )
       # New, exclusively owned group; reuse requires exact membership checks.
       group_id = create_dedicated_group(
           sdk=sdk, group_parent_id=project_id, project_id=project_id,
           group_name=workload_name, service_account_id=iam_sa,
       )
       permit = submit(AccessPermitServiceClient(sdk).create,
           CreateAccessPermitRequest(
               metadata=ResourceMetadata(parent_id=group_id),
               spec=AccessPermitSpec(resource_id=secret_id,
                                     role="mysterybox.payload-viewer"),
           ))
       trust_id = ensure_federation(
           sdk=sdk, project_id=project_id, name=workload_name,
           issuer=issuer, namespace=namespace, service_account=k8s_sa,
           iam_service_account_id=iam_sa,
       )
   ```

   This is a one-time provisioning example, not a blind repeatable installer.
   On rerun, inspect exact existing IDs and dedicated-group membership, list
   existing permits, add only a missing exact resource/role grant and pass
   `existing_id=trust_id` to the trust helper. Preserve unrelated permits. A
   failure after creation retains IDs for reconciliation; never recreate or
   delete based on a timeout alone. Verify permit and membership readback
   independently before testing the workload.

## Projected token and renewable SDK session

The pod uses the named Kubernetes ServiceAccount and requests a short-lived JWT
for **`sts.nebius.com`** (no URL scheme). Mount the directory, not a `subPath`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: workload-namespace
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: workload-client
  namespace: workload-namespace
automountServiceAccountToken: false
---
apiVersion: v1
kind: Pod
metadata:
  name: workload-client
  namespace: workload-namespace
spec:
  serviceAccountName: workload-client
  automountServiceAccountToken: false
  containers:
    - name: client
      image: <approved-application-image>
      volumeMounts:
        - name: nebius-identity
          mountPath: /var/run/nebius
          readOnly: true
  volumes:
    - name: nebius-identity
      projected:
        sources:
          - serviceAccountToken:
              path: token
              audience: sts.nebius.com
              expirationSeconds: 600
```

Create the ServiceAccount in that namespace before applying the pod. Run as a
non-root user with a suitable pod security context and token-file permissions.
The kubelet rotates the projection. Preserve `/var/run/nebius/token` as a path;
do not resolve its backing symlink, copy the JWT into an environment variable,
cache the JWT forever or mount with `subPath`.

```python
from sdk.runtime import owned_sdk
from sdk.secretstash import read_secret

with owned_sdk(parent_id=project_id, workload_identity={
    "service_account_id": iam_sa,
    "token_file": "/var/run/nebius/token",
}) as sdk:
    version_id, values = read_secret(sdk=sdk, secret_id=secret_id)
    # Pass values in memory to the application. Never log the payload or JWT.
```

The helper uses `FederatedCredentialsBearer`, then `SDK(credentials=bearer)`
and `bearer.set_channel(sdk)`. The SDK exchanges the projected token, renews IAM
tokens and rereads the projected path when exchanging again. Explicit workload
identity is exclusive with profile/config/credential-file selection; it never
falls back to a workstation identity. This API exists in Python SDK 0.6.4 and
the skill's validated 0.6.7 pin. Callers close the SDK they own.

For other IAM APIs, reuse the same SDK with a generated service client and
bounded requests from `sdk.runtime.rpc`. For example, a read-only resource
inspection can use `Compute InstanceServiceClient.get(GetInstanceRequest(id=...))`
after the owner grants the appropriate scope:

```python
from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient
from sdk.runtime import rpc

# Inside the same owned_sdk context, with separately approved Compute access:
instance = rpc(InstanceServiceClient(sdk).get, GetInstanceRequest(id=instance_id))
assert instance.metadata.id == instance_id
```

Do not infer that SecretStash permissions authorize Compute or any other service.

## Secret rotation and optional ESO

Read all fields of a logical credential together from one SecretStash version.
An unpinned read selects the approved primary version; an explicit version
pins it. Applications must decide when to reread and replace their in-memory
credential. SDK IAM-token renewal does **not** refresh an application secret.
Keep the original credential usable while operations issued by it remain
unfinished, and bind their recovery to its original immutable version. Never
claim a key is absent by querying through a replacement client.

Direct SDK consumers do not need ESO or a Kubernetes Secret. Use ESO only when
the application requires Kubernetes Secret synchronization. Native Nebius
workload identity requires **ESO 2.10.0 or newer**; older installations must be
upgraded through their Helm owner before using this configuration. Configure
`auth.workloadIdentity.serviceAccountRef` and `iamServiceAccountID`, namespace
scoping and TokenRequest RBAC for the controller. Confirm the installed CRD
schema. ESO refreshes its IAM token and syncs on the configured refresh
interval; an environment-variable consumer still requires a restart and a
file consumer must reload changes. Do not place the workload JWT or private
IAM key in the SecretStore.

## Qualification and troubleshooting

Verify exact issuer, namespace, ServiceAccount, IAM subject, permit resource
and role without printing token contents. Prove an allowed read and a denied
out-of-scope read, then repeat the allowed read across both a Kubernetes token
rotation and IAM-token expiry in a long-running workload. Test loss of trust
or permission with sanitized errors and no fallback. Offline tests prove
request construction and file rereads; they do not prove live STS, IAM or
service access. Account for propagation delays without widening permissions.

Sources checked 2026-10-29:

- [Managed Kubernetes public OIDC issuer](https://docs.nebius.com/kubernetes/networking/oidc-public-endpoint)
- [SecretStash payload access](https://docs.nebius.com/mysterybox/secrets/get#how-to-get-a-payload)
- [SecretStash versions](https://docs.nebius.com/mysterybox/overview#secrets-and-versions)
- [IAM roles](https://docs.nebius.com/iam/authorization/roles)
- [Nebius ESO workload identity](https://docs.nebius.com/mysterybox/kubernetes)
- [SDK federated bearer](https://github.com/nebius/pysdk/blob/v0.6.7/src/nebius/aio/token/federated_credentials.py)
- [Kubernetes ServiceAccount tokens](https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/)
