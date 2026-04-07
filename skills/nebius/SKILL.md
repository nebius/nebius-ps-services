---
name: nebius
description: Implement Nebius cloud automation in Python using the Nebius SDK, including IAM/Object Storage workflows and VPC networking inspection/design. Use when building or reviewing Nebius auth bootstrap, service accounts, access keys, Terraform state buckets, VPC pools, subnet inheritance, route tables, or safe Nebius networking automation.
---

# Nebius

Implement Nebius IAM/Object Storage and VPC networking workflows with reusable SDK patterns, references, and read-only inspection scripts.

## Use This Skill For

- Nebius IAM bootstrap:
  - service accounts
  - IAM groups and project role grants
  - authorized keys
  - S3 access keys
  - Terraform state bucket readiness
- Nebius VPC networking:
  - network private pools
  - explicit subnet CIDRs versus inherited subnet mode
  - route tables and route ownership
  - dedicated service subnet and workload subnet design
  - control-plane behavior review and cloud-pattern comparison

## Workflow

1. Choose the track:
   - IAM/Object Storage:
     - start with `references/iam.md`
     - reuse code from `assets/iam/`
   - VPC networking:
     - start with `references/vpc-networking.md`
2. For live Nebius VPC inspection, run:
   - `scripts/inspect_vpc_topology.py`
   - `scripts/inspect_vpc_routes.py`
3. Load only the references needed for the task:
   - `references/cloud-patterns.md`
   - `references/route-inspection.md`
4. Keep workflows idempotent and safe:
   - look up by name or ID first
   - create only when missing
   - prefer read-only inspection before mutating infrastructure

## Guardrails

- Never print or commit private keys, tokens, or access key secrets.
- Use Nebius role IDs like `editor`, not `roles/editor`.
- Upload Nebius auth public keys in PEM format.
- Prefer the current SDK auth order from `nebius/pysdk` README:
  - credentials file
  - service-account private key env/config
  - `NEBIUS_IAM_TOKEN`
  - Nebius CLI token via `nebius iam get-access-token`
  - CLI config/profile (`Config()`)
- Wait for stateful Nebius resources to become ready before dependent actions.
- Treat `use_network_pools=true` as inherited allocation mode, not subnet CIDR ownership.
- Do not treat `status.ipv4_private_cidrs` as the only ownership signal for subnet automation.
- Close SDK clients with `sync_close()` when long-running processes are not needed.

## Assets and Scripts

### IAM assets

- `assets/iam/iam_api.py`
- `assets/iam/create_service_account.py`
- `assets/iam/grant_project_roles.py`
- `assets/iam/get_sa_token.py`
- `assets/iam/create_authorized_key.py`
- `assets/iam/create_access_key.py`
- `assets/iam/ensure_state_bucket.py`

### VPC inspection scripts

- `scripts/inspect_vpc_topology.py`
  - reports network parent pools, derived child pools, subnet allocation mode, explicit CIDRs, and inherited status CIDRs
- `scripts/inspect_vpc_routes.py`
  - reports effective route-table attachment, route-table consumers, and routes per table

Run scripts relative to the skill directory.

## References

- `references/iam.md`
- `references/vpc-networking.md`
- `references/cloud-patterns.md`
- `references/route-inspection.md`
