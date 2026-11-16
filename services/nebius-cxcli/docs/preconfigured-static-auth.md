# Preconfigured static authentication

`NEBIUS_CXCLI_AUTH_MODE=preconfigured-static` is an explicit restricted path for
trusted CPU VM test configurations. Normal managed authentication remains the
default. This path consumes an already issued bearer and existing Object Storage
credentials; it cannot bootstrap, rotate, export or renew a managed identity.
It is locally tested but has not yet been qualified against a live provider target.

Set `NEBIUS_CXCLI_STATIC_AUTH_FILE` to an absolute protected runtime JSON file.
The file must be a regular file owned by the invoking user, accessible only to
that user, not a symlink or hard link, and no larger than 32 KiB. Its closed schema
has these fields:

| Field | Meaning |
| --- | --- |
| `schema` | Exactly `preconfigured-static/v1` |
| `token` | Already issued bearer; never log it or put it in project configuration |
| `service_account_id` | Exact service account authenticated by the bearer |
| `project_id` | Exact authorized project |
| `issued_at`, `expires_at` | Provider issuance and expiry as Unix seconds |
| `deadline` | Earlier task deadline, at most three hours after issuance |

Expiry must be at most 12 hours after issuance, and the task deadline must fall
within the bearer lifetime. These values are supplied by the trusted issuer; the
CLI's deadline is a local admission check, not provider revocation. Verification
uses the official API endpoint and `whoami` with the static token. A mismatched
project/account, expired deadline, alternative endpoint, mixed credential source
or request for renewable authentication fails before falling back to another
identity. The bearer is not included in object representations or generated files.

Enabled infrastructure components must be CPU VMs on `cpu-d3` or `cpu-e2`, with
no GPU cluster/fabric, runtime service account, dependencies, input bindings or
access enrollment. App charts are not supported. The backend bucket must already
exist; supply its scoped access-key pair through the existing storage environment
interface. Terraform receives the bearer only in its process environment, and its
generated provider block omits signing credentials. The test supervisor owns
actual resource/cost bounds, module/source selection and external IAM restrictions.
The configuration check alone is not a sandbox for hostile Terraform inputs.

Input bindings use the native component row's `bindings` field and are rejected
before rendering. Non-regular credential sources, including named pipes, fail
without waiting for a writer; they cannot supply authentication material.

`bootstrap-ci`, WireGuard operations, managed credential export and storage-key
issuance, and operator CLI token acquisition reject this mode. Do not install a
human CLI profile or renewable deployment credential in the candidate executor.
Missing authority must stay unavailable rather than trigger bootstrap.

This path is for qualification of native VM apply/destroy behavior. It does not
qualify GPU, Kubernetes, Helm, factory provisioning, WireGuard, VPNGW BGP or HA.
Live evidence must bind the tested source and fixture and independently establish
owned cleanup before any automation capability is marked qualified.
