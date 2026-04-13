---
name: nebius
description: Implement Nebius cloud automation in Python using the Nebius SDK, including IAM/Object Storage workflows, VPC networking inspection/design, live quota management, and MK8s compatibility/readiness checks. Use when building or reviewing Nebius auth bootstrap, service accounts, access keys, Terraform state buckets, VPC pools, subnet inheritance, route tables, quota-aware Nebius provisioning checks, or MK8s GPU/image compatibility.
---

# Nebius

Implement Nebius IAM/Object Storage, VPC networking, quota-management, and MK8s compatibility/readiness workflows with reusable SDK patterns, references, and read-only inspection scripts.

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
- Nebius quota management:
  - tenant/project quota allowance inspection
  - region-scoped quota availability checks
  - granular compute, disk, public-IP, and managed-service quota mapping
  - quota-aware create/render/deploy guardrails
  - MK8s node-group quota estimation and aggregation
  - component-level confirmed-versus-partial quota reporting for explicit operator checks
- Nebius MK8s compatibility and readiness:
  - control-plane version and node-group compatibility-matrix checks
  - GPU platform, OS, and `drivers_preset` selection
  - GPU preset eligibility checks for InfiniBand / GPU clustering
  - image-family selection for CPU/GPU platforms
  - rolling-update quota and public-IP headroom review
  - node infra-version drift checks when provisioning or autoscaling behaves unexpectedly
  - maintenance-behavior review for MK8s nodes

## Workflow

1. Choose the track:
   - IAM/Object Storage:
     - start with `references/iam.md`
     - reuse code from `assets/iam/`
   - VPC networking:
     - start with `references/vpc-networking.md`
   - Quota management:
     - start with `references/quota-management.md`
     - use `scripts/inspect_quotas.py` for live allowance inspection
     - resolve compute platform presets live before estimating CPU/GPU requirements
     - distinguish between confirmed insufficiency, unresolved live limits, and coverage gaps
     - when reporting results, surface components whose checked quota dimensions are sufficient even if the same component still has separate coverage gaps
     - use `quota-check --all-regions` style regional replay only as a quota diagnostic; it does not prove platform/preset support in those other regions
   - MK8s compatibility/readiness:
     - start with `references/mk8s-compatibility.md`
     - query the live MK8s compatibility matrix before choosing GPU platform, OS, or `drivers_preset`
     - query the live compute platform preset inventory before deciding whether `infiniband_fabric` is valid for the chosen GPU shape
     - prefer the Nebius SDK API for automation; use the CLI only for ad hoc manual inspection
     - prefer exact `drivers_preset` strings returned by the matrix, not shorthand guesses
     - treat GPU clustering as a property of the selected preset's live metadata, not a platform-only assumption
     - use image-family guidance as a secondary recommendation layer after the matrix confirms compatibility
2. For live Nebius VPC inspection, run:
   - `scripts/inspect_vpc_topology.py`
   - `scripts/inspect_vpc_routes.py`
   For live quota inspection, run:
   - `scripts/inspect_quotas.py`
3. Load only the references needed for the task:
   - `references/cloud-patterns.md`
   - `references/route-inspection.md`
   - `references/quota-management.md`
   - `references/mk8s-compatibility.md`
4. Keep workflows idempotent and safe:
   - look up by name or ID first
   - create only when missing
   - prefer read-only inspection before mutating infrastructure
   - query live quota allowances at both tenant and project scope before planning or provisioning
   - aggregate shared quota consumers before comparing against available capacity
   - treat unresolved limits as warnings or coverage gaps, not as proof that quota is sufficient

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
- Query quota live with `quotas.v1.QuotaAllowanceService.list`; do not rely on stale hand-maintained quota tables.
- Quota availability is granular by quota name and region. Do not assume a global quota covers a regional request.
- Query both tenant and project allowances. When both expose limits, the effective available quota is the more restrictive value. When only the tenant exposes a limit, treat tenant quota as the effective bound instead of assuming project quota is unlimited.
- If a quota limit cannot be resolved live, surface it as unresolved coverage instead of marking the request safe.
- Aggregate quotas across all requested resources that share the same backing allowance, for example VM count, boot-disk count, and public-IP count across multiple MK8s node groups.
- Resolve GPU quota names from the live platform naming when possible and emit an explicit coverage gap when the mapping is unknown.
- Coverage gaps do not automatically negate already confirmed checked dimensions for the same component. Report both truths: what was confirmed and what still could not be evaluated.
- `capacity.v1.ResourceAdvice` may be unavailable on some tenants. Keep quota workflows centered on allowance APIs plus live platform/preset inspection.
- Earlier phases may warn and report on confirmed insufficiency, but deployment should fail when quota is still insufficient.
- Treat the live MK8s compatibility matrix as the source of truth for control-plane version, platform, OS, and GPU `drivers_preset` combinations.
- For automation, prefer `nebius.api.nebius.mk8s.v1.NodeGroupServiceClient.get_compatibility_matrix(...)` over shelling out to the `nebius` CLI.
- For InfiniBand / GPU-cluster decisions, prefer `nebius.api.nebius.compute.v1.PlatformServiceClient.get_by_name(...)` and inspect the selected preset's live `allow_gpu_clustering` metadata.
- Do not assume a GPU platform implies GPU clustering support. Different presets on the same platform may differ.
- If the selected preset does not allow GPU clustering, leave `infiniband_fabric` unset or clear any stale value before render/apply.
- Prefer exact preset/image tags such as `cuda13.0`; do not use shorthand aliases such as `cuda13` or floating tags such as `latest`.
- If a compatibility lookup returns exactly one valid `drivers_preset` for the selected Kubernetes version and GPU platform, use that as the default while still allowing an explicit override.
- Do not trust stale static platform-to-driver maps without revalidating them against the live compatibility matrix.
- When a workload needs pinned public IPs for MK8s node groups, plan the dedicated subnet/public-pool capacity for the steady-state node count plus rolling-update headroom.
- For rolling updates, quota and public-IP planning must account for surge behavior; if full utilization leaves no headroom, either free capacity first or use a staged size-reduction/update/restore sequence.
- When MK8s autoscaling or provisioning looks wrong, check node infra versions and compare them with the latest rollout for that region before assuming a workload-side issue.
- MK8s maintenance handling may cordon/drain and stop-start nodes before the maintenance deadline once GPU workloads have completed; do not assume “before deadline” means “unexpected”.
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
- `scripts/inspect_quotas.py`
  - reports raw tenant/project quota allowances and effective available quota by `(name, region)`

Run scripts relative to the skill directory.

## References

- `references/iam.md`
- `references/vpc-networking.md`
- `references/cloud-patterns.md`
- `references/route-inspection.md`
- `references/quota-management.md`
- `references/mk8s-compatibility.md`
