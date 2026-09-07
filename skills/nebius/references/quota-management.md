# Nebius Quota Management

## Read This When

- the task needs a live Nebius quota check before create, render, or deploy
- a workload request must be mapped to granular Nebius quota names
- a user wants to understand whether tenant quota, project quota, or both are constraining a request
- MK8s, compute, storage, or managed service sizing must be checked against current quota

## Primary API Surface

Use the live Nebius quota allowance API, not a static quota table:

- `nebius.api.nebius.quotas.v1.QuotaAllowanceServiceClient`
- `ListQuotaAllowancesRequest(parent_id=<tenant-id-or-project-id>, page_size=500, page_token=...)`

Important fields on each allowance:

- `metadata.name`
  Quota name such as `compute.instance.count` or `compute.instance.gpu.h200`
- `spec.region`
  Region scope for the allowance
- `spec.limit`
  Current limit when Nebius exposes one at that scope
- `status.usage`
  Current consumption
- `status.service`, `status.description`, `status.unit`
  Useful for operator reporting
- `status.state`, `status.usage_state`, `status.usage_percentage`
  Useful for diagnostics, but do not use them as the only gate

## Effective Availability Rule

Query both tenant and project allowances for the same `(quota_name, region)` key.

- If tenant and project both expose limits:
  - `effective_available = min(tenant_limit - tenant_usage, project_limit - project_usage)`
- If only the project exposes a limit:
  - use the project value
- If only the tenant exposes a limit:
  - use the tenant value
- If neither scope exposes a limit:
  - mark the quota unresolved instead of assuming the request is safe

Scope interpretation:

- tenant scope often carries the effective hard limit
- project scope may expose usage without a limit
- missing project limit does not mean unlimited project capacity

## Reporting Semantics

Keep the quota result model split into separate states instead of collapsing everything into one pass/fail bit:

- confirmed insufficiency
  - at least one live quota dimension is known and currently too small for the requested shape
- unresolved live limit
  - the workload-to-quota mapping is known, but no live allowance limit could be resolved for one or more required quota keys
- coverage gap
  - the current config or API surface cannot translate some part of the workload into a safe quota requirement estimate
- confirmed checked dimensions
  - one or more quota dimensions were estimated and compared live successfully

Important operational rule:

- a coverage gap on one quota dimension does not erase successfully confirmed dimensions for the same component
- report both:
  - what was confirmed
  - what could not be evaluated

Example when a workload input omits storage dimensions:

- MK8s may confirm `mk8s.cluster.count`, `compute.instance.count`, `compute.disk.count`, and `compute.instance.non-gpu.vcpu`
- the same MK8s component may still carry a coverage gap because node-group boot disk size/type is not exposed by the current module inputs, so disk-size quotas cannot be checked safely
- operator output should therefore say both:
  - the checked MK8s dimensions are currently sufficient
  - boot-disk size/type quotas were not checked

## Estimating Workload Requirements

Do not compare quota until the requested workload is converted into quota requirements.

### Compute presets

Resolve instance resources live from the compute platform API:

- `nebius.api.nebius.compute.v1.PlatformServiceClient`
- `GetByNameRequest(parent_id=<project-id>, name=<platform>)`
- inspect `spec.presets[*].resources` for:
  - `vcpu_count`
  - `memory_gibibytes`
  - `gpu_count`

This is required for MK8s node-group checks because preset sizes are not stable enough to hardcode.

### Shared quotas must be aggregated

Multiple components and node groups can consume the same allowance. Sum them first, then compare once.

Examples:

- `compute.instance.count`
  Total VM count across CPU and GPU node groups
- `compute.disk.count`
  Total boot-disk count across all nodes
- `vpc.ipv4-address.public.count`
  Total public IP demand across all instances that request them
- `compute.instance.non-gpu.vcpu`
  Total non-GPU vCPU demand
- `compute.instance.gpu.h200`
  Total H200 GPU demand

## MK8s-Specific Guidance

For MK8s, check at least:

- `mk8s.cluster.count`
- `compute.instance.count`
- `compute.disk.count`
- `compute.instance.non-gpu.vcpu` for CPU node groups
- `compute.instance.gpu.<type>` for GPU node groups
- `vpc.ipv4-address.public.count` when node templates request public IPs
- `compute.instance.preemptible.count` when node templates are preemptible

Important lessons:

- CPU and GPU node groups share VM-count and boot-disk-count quotas
- GPU quota type must be derived from the platform name carefully
- unsupported GPU-type mapping should be surfaced as a coverage gap, not guessed
- preemptible GPU-type quotas may not be exposed by the current public allowance surface
- if the configuration does not expose boot-disk size/type details, state that disk-size quotas were not checked
- a caller that omits boot-disk size/type still has a coverage gap even when VM-count, cluster-count, and vCPU quotas are confirmed live

## Managed Service Examples

Quota names are service-specific. Example names to verify in the selected live quota inventory include:

- `msp.postgres.count`
- `msp.postgres.cpu`
- `msp.postgres.ram`
- `msp.postgres.disk.size.<disk-type>`
- `storage.bucket.count`
- `mysterybox.secret.count`

Do not extrapolate quota names blindly. Keep mappings explicit and emit a coverage gap when a component cannot be translated safely.

## Recommended Phase Behavior

- create or planning phase:
  - warn when confirmed insufficiency is found
  - allow the operator to continue explicitly
- render phase:
  - run the live check again
  - persist the report into generated artifacts
  - allow the render to finish with a clear warning/report
- deploy phase:
  - fail on confirmed insufficiency
  - instruct the operator to increase the relevant quota before retrying
- regional diagnostics:
  - when quota is insufficient or a user asks for alternatives, replay the same derived requirements across all discovered quota regions
  - keep this replay quota-only; it does not revalidate region-specific platform/preset support

## Inspection contract

Use `scripts/inspect_quotas.py` for bounded pagination, explicit parent checking,
unique dimensions and sanitized failures instead of copying a second list loop.
The JSON envelope declares `mode: effective` unless `--raw` is requested,
including when a selected scope is empty. The SDK uses scalar zero even for unknown usage, so accept measurements only
for `STATE_ACTIVE` with `USAGE_STATE_USED` or `USAGE_STATE_NOT_USED`. Normalize
unknown/unspecified/not-applicable usage and inactive quota states to unresolved.
A known limit with unresolved usage cannot produce effective headroom using
only the other scope; effective rows preserve both scopes' state fields. A missing limit may inherit from the other scope, but never
means unlimited capacity. `complete` means collection completed, not that every
workload dimension is resolved or that deployment is approved.

## Resource Advice Caveat

Nebius `capacity.v1.ResourceAdvice` may exist, but it can be disabled or unavailable on some tenants. Treat it as optional enrichment, not as the primary quota gate.

## Official references

- [Quotas](https://docs.nebius.com/overview/quotas)
- [Allowance API](https://docs.nebius.com/cli/reference/quotas/quota-allowance/list)

- [Quota and usage states](https://docs.nebius.com/terraform-provider/reference/resources/quotas_v1_quota_allowance)
