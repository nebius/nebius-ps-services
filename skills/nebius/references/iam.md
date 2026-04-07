# Nebius IAM Reference

This reference defines a practical Python SDK flow for Nebius IAM automation:

1. Initialize SDK IAM context for API usage.
2. Ensure a Service Account exists in the target project.
3. Ensure project role grants by:
   - ensuring a project-scoped IAM group
   - adding the service account as a member
   - creating access permits under the group
4. Get a short-lived IAM token for that Service Account.
5. Create authorized keys for Service Accounts.
6. Create access keys for Nebius Object Storage (S3 API).
7. Ensure remote Terraform state bucket exists and is `ACTIVE` before Terraform init.

## Prerequisites

- Python package: `nebius`
- For authorized key creation: `cryptography`
- For SA token exchange snippet: `PyJWT`
- Operator is logged in with Nebius CLI (`nebius auth login`) or env-based credentials.

## SDK Authentication Order

Prefer the current Nebius Python SDK initialization order from the upstream
`nebius/pysdk` README:

1. Explicit credentials file (`NEBIUS_AUTH_CREDENTIALS_FILE` or `credentials_file_name=...`)
2. Explicit service-account private key credentials
3. `NEBIUS_IAM_TOKEN`
4. Nebius CLI token via `nebius iam get-access-token`
5. CLI config/profile via `from nebius.aio.cli_config import Config`

Notes:

- `SDK()` can use `NEBIUS_IAM_TOKEN` directly when that env var is present.
- `Config()` may also pick up `NEBIUS_IAM_TOKEN` and `NEBIUS_PROFILE` unless
  `no_env=True` is used.
- For operator tools, a pragmatic fallback is:
  - check explicit credentials first
  - then `NEBIUS_IAM_TOKEN`
  - then try `nebius iam get-access-token`
  - then fall back to `Config()`

## Operational Notes

- `project_id` is the parent for Service Account, authorized key, and access key resources.
- Access permits should be created under IAM group parent IDs, not under service account IDs.
- Nebius access-permit role IDs are plain values such as `editor`, `viewer`, `auditor`, `admin`.
- Auth public key `data` must be a PEM public key (SubjectPublicKeyInfo), not OpenSSH text.
- Access key secret is typically returned once; store it immediately and securely.
- Object Storage can return `NoSuchBucket`/`NOT_FOUND` immediately after create/list checks.
- Newly created buckets may be in `CREATING`; poll until `ACTIVE` before Terraform backend init.
- Private key material must be stored with strict permissions (`0600`) and never committed.
- Use idempotent create logic:
  - `get_by_name`
  - create only if not found

## Snippet Map

- SDK/API IAM init:
  - `../assets/iam/iam_api.py`
- Create/ensure Service Account:
  - `../assets/iam/create_service_account.py`
- Grant project roles to service account:
  - `../assets/iam/grant_project_roles.py`
- Create SA IAM token:
  - `../assets/iam/get_sa_token.py`
- Create authorized key for SA:
  - `../assets/iam/create_authorized_key.py`
- Create Object Storage access key for SA:
  - `../assets/iam/create_access_key.py`
- Ensure Terraform-state bucket readiness:
  - `../assets/iam/ensure_state_bucket.py`
