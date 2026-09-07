# IAM and security services

Reviewed 2026-09-07. APIs include `iam.v1` principals/groups/permits,
`iam.v2` access keys/projects, `kms.v1` and `mysterybox.v1`.
KMS is documented as preview. SecretStash is the public product name; its
current API/CLI identifier remains `mysterybox`. Verify service/region availability.

## Resource and authentication model

Resolve the tenant/project hierarchy and intended principal before provisioning.
A group membership makes a principal part of a group; access permits grant that
group a role on a resource. They are separate effects. Follow least privilege,
plain Nebius role identifiers such as `viewer`, and exact grant scope; do not
accept `roles/` aliases. Inspect required service roles rather than assuming an
administrator role is necessary. Project creation and invitations are IAM writes.

Authentication follows `api-sdk.md`; renewable SDK credentials are preferred for
long workflows. Authorized keys, S3 access keys, service-specific static tokens,
user access tokens and federation are different mechanisms. Do not exchange one
for another by trial-and-error fallback. Federation/SSO and outbound workload
identity require the documented trust direction, audience and region; do not
assume an AWS-style role or general inbound OIDC integration.

## IAM asset workflow

1. Use `assets/iam/create_service_account.py` to create once, or reuse an
   explicitly supplied account ID. A name collision is not adoption authority.
2. `create_dedicated_group` in `assets/iam/grant_project_roles.py` creates a
   group and its intended service-account membership. If membership fails,
   retain the failure's `resource_id` (group), `operation_id` (pending
   membership operation), `pending_resource_id` (membership), and
   `completed_resource_ids`/`completed_operation_ids` (successful group create).
   The original sanitized failure code is preserved. Reconcile both steps;
   do not create another group or delete the first one automatically.
3. `grant_service_account_project_roles` requires exact group/parent/account
   IDs, a single expected member, and explicit roles. Keep concurrent IAM
   writers quiescent: separate membership and permit APIs are not an atomic
   authorization transaction. Recheck membership before each grant and after
   the sequence. Fail on unexpected membership; retain completed permit IDs
   and pending operation identity on partial failure.
4. `create_authorized_key.py` registers a caller-owned PEM public key. Generate
   and protect the private key in the caller's authorized credential workflow;
   the helper never creates or stores it. A timed-out registration is reconciled
   by operation identity/public-key evidence before any new registration.
5. `create_access_key.py` creates once or resumes by explicit existing key ID,
   verifies project/account binding and returns secret material only in memory.
   Its representation hides credentials. Never print/serialize the result.
   Retrieval failure retains the key ID; no secret-loss retry creates another key.
6. `get_sa_token.py` illustrates bounded explicit JWT exchange when needed;
   prefer SDK-native renewal for ordinary automation. Treat its return as secret.

Key creation, rotation, grants, revocation and deletion each require exact
operation authority. Rotation means prove the new credential works for its
intended consumer before separately authorizing retirement of the old credential.
Never grant roles or repair auth implicitly to make a test pass.

## KMS and SecretStash

Choose symmetric encryption versus asymmetric signing/encryption from current
KMS key capabilities. Track key versions and deletion/restore windows before
rotation or deletion; key deletion can make application data inaccessible.
Secrets have versioned payloads and a primary version. Pin a version when
reproducibility is required, and change the primary only under explicit authority.
Do not place a secret in cloud-init, Terraform values or examples as a shortcut.

`assets/iam/security_metadata.py` lists only allowlisted metadata for secrets
or symmetric/asymmetric keys. It never requests secret payloads. Even a generated
Secret resource schema can contain payload fields, so unrestricted serialization
is forbidden. Audit Logs queries are tenant security access and require the
explicit audit-log workflow; ordinary application logs are a separate service.

## Official references

- [IAM hierarchy](https://docs.nebius.com/iam/overview)
- [Roles](https://docs.nebius.com/iam/authorization/roles)
- [Service-account authentication](https://docs.nebius.com/iam/service-accounts/authentication)
- [Authorized keys](https://docs.nebius.com/iam/service-accounts/authorized-keys)
- [Access keys](https://docs.nebius.com/iam/service-accounts/access-keys)
- [Federations](https://docs.nebius.com/iam/federations/manage)
- [Workload identity](https://docs.nebius.com/iam/wif)
- [KMS](https://docs.nebius.com/kms)
- [SecretStash](https://docs.nebius.com/mysterybox/overview)
