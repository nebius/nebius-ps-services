---
name: nebius
description: Implement Nebius IAM and Object Storage automation in Python using Nebius SDK (SDK init, service accounts, project role grants via IAM groups, SA tokens, authorized keys, S3 access keys, and Terraform-state bucket readiness). Use when building or reviewing Nebius auth bootstrap and runtime credential workflows.
---

# Nebius

Use this skill for Nebius IAM/Object Storage credential automation.

## Workflow

1. Start with `references/iam.md` for the end-to-end flow and guardrails.
2. Reuse snippets from `assets/iam/` instead of rewriting IAM logic.
3. Keep workflows idempotent:
   - look up by name first
   - create only if missing
4. Never print or commit private keys, tokens, or access key secrets.
5. Use restrictive file permissions (`0600`) for private key files.

## Snippet Assets

- SDK IAM bootstrap:
  - `assets/iam/iam_api.py`
- Service account creation:
  - `assets/iam/create_service_account.py`
- Grant project roles to service account via IAM group:
  - `assets/iam/grant_project_roles.py`
- Service account token exchange:
  - `assets/iam/get_sa_token.py`
- Authorized key creation for SA:
  - `assets/iam/create_authorized_key.py`
- S3 access key creation for SA:
  - `assets/iam/create_access_key.py`
- Ensure Terraform-state S3 bucket exists and is ACTIVE:
  - `assets/iam/ensure_state_bucket.py`

## Guardrails

- Prefer one canonical IAM path in each command path; avoid duplicated fallback logic.
- For access permits, use an IAM group as the permit parent and add the service account as group member.
- Use Nebius role IDs like `editor` (not `roles/editor`).
- Upload auth public key data in PEM format (`-----BEGIN PUBLIC KEY-----`).
- Treat storage errors like `NoSuchBucket`/`NOT_FOUND` as create-path signals.
- Wait for bucket state `ACTIVE` before running Terraform backend init.
- Keep key creation explicit and reversible (support recreate/rotate flows).
- If access key secret is returned only once, persist it immediately in a secure location.
- Close SDK clients (`sync_close`) when long-running processes are not needed.
