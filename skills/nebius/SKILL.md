---
name: nebius
description: "Use for Nebius SDK/cloud automation in Python: IAM/Object Storage, service accounts/access keys, VPC pools/subnets/routes, quota checks, MK8s compatibility/readiness, GPU platform/preset/fabric/operator decisions, and observability endpoint/auth wiring. Do not use for Terraform scaffolding, Helm charts, or nebius-cxcli module onboarding."
---

# Nebius

## Help

For `$nebius --help` or `$nebius -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

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
  - live project-supported GPU platform, region, preset, and fabric selection
  - GPU platform, OS, and `drivers_preset` selection
  - GPU preset eligibility checks for InfiniBand / GPU clustering
  - single-GPU Ethernet-only versus multi-GPU InfiniBand-capable shape review
  - driverful Nebius-image versus operator-managed GPU stack decisions
  - GPU Operator and Network Operator ordering, ownership, and NFD scoping
  - scheduler-visible RDMA exposure checks such as `rdma/shared_device`
  - NCCL suitability review for MK8s GPU node groups
  - image-family selection for CPU/GPU platforms
  - rolling-update quota and public-IP headroom review
  - node infra-version drift checks when provisioning or autoscaling behaves unexpectedly
  - maintenance-behavior review for MK8s nodes
- Nebius observability:
  - Monitoring, Logging, and Tracing architecture
  - Monitoring agent versus Nebius Observability Agent for Kubernetes
  - public write/read endpoint mapping
  - IAM/static-token auth boundaries for agents, Grafana, and CLI tools
  - VM journald label contract and MK8s cluster-agent contract
  - public-safe `nebius-cxcli` observability onboarding and config design

## Workflow

1. Establish project context, then choose the track:
   - when an implementation needs a project ID, start with
     `references/project-selection.md`
   - prefer an explicit current-task project
   - when no explicit project exists and the workflow permits the user's CLI
     default as authority, use the documented config-owned profile fallback
   - IAM/Object Storage:
     - start with `references/iam.md`
     - reuse code from `assets/iam/`
   - VPC networking:
     - start with `references/vpc-networking.md`
     - distinguish network parent pools, explicit subnet pools, inherited
       subnet allocation mode, live allocations, and route-table ownership
     - when designing subnet CIDR automation, verify parent CIDR containment,
       explicit peer-subnet overlap, live allocation overlap, and pool-tree
       compatibility before proposing or applying changes
   - Quota management:
     - start with `references/quota-management.md`
     - use `scripts/inspect_quotas.py` for live allowance inspection
     - resolve compute platform presets live before estimating CPU/GPU requirements
     - distinguish between confirmed insufficiency, unresolved live limits, and coverage gaps
     - when reporting results, surface components whose checked quota dimensions are sufficient even if the same component still has separate coverage gaps
     - use `quota-check --all-regions` style regional replay only as a quota diagnostic; it does not prove platform/preset support in those other regions
   - MK8s compatibility/readiness:
     - start with `references/mk8s-compatibility.md`
     - load `references/mk8s-gpu-setup.md` when the task is about GPU-enabled cluster setup, operator selection, or GPUDirect RDMA readiness
     - query the live MK8s compatibility matrix before choosing GPU platform, OS, or `drivers_preset`
     - query the live compute platform preset inventory for the current project before deciding whether `infiniband_fabric` is valid for the chosen GPU shape
     - if Capacity Dashboard `capacity.v1.ResourceAdvice` is available, use it only to rank supported region / preset / fabric options and recommend a default fabric for clusterable shapes
     - prefer the Nebius SDK API for automation; use the CLI only for ad hoc manual inspection
     - prefer exact `drivers_preset` strings returned by the matrix, not shorthand guesses
     - treat GPU clustering as a property of the selected preset's live metadata, not a platform-only assumption
     - treat single-GPU presets as Ethernet-only testing/dev shapes; do not present them as InfiniBand or GPUDirect-RDMA-capable production training shapes
     - use image-family guidance as a secondary recommendation layer after the matrix confirms compatibility
     - when the task is specific to `services/nebius-cxcli`, treat `component_sources.yaml` as the authoritative contract for operator auto-enable rules and rendered Helm values
     - when the task is specific to `services/nebius-cxcli`, keep `create` quota and capacity checks warning-only before config creation and point operators to `quota-request` for the follow-up request path
     - when the task is specific to `services/nebius-cxcli`, warn if NCCL is forced onto a 1-GPU or other non-cluster MK8s shape because it would run over Ethernet/TCPIP rather than InfiniBand / GPUDirect-RDMA
     - when the host uses a Nebius driverful GPU image, treat host GPU driver, NVIDIA Container Toolkit, and Nebius-provided OFED as separate concerns from Kubernetes-side device plugins and RDMA exposure
     - when both NVIDIA operators are needed, install or reconcile Network Operator before GPU Operator and keep exactly one NFD owner
   - Observability:
     - start with `references/observability.md`
     - reuse `assets/observability/public-endpoints.yaml`
     - verify the Kubernetes agent chart source against the current Nebius
       Observability Agent docs; the cxcli catalog pins
       `oci://cr.nebius.cloud/observability/public/nebius-observability-agent-helm`
     - when the task is specific to `services/nebius-cxcli`, treat `component_sources.yaml` and `src/nebius_cxcli/observability.py` as the contract owners for observability defaults, runtime materialization, and report endpoint summaries
2. For live Nebius inspection, use the bundled scripts only when script
   execution is permitted by the current user and repository policy and the
   requested operation is read-only:
   - `scripts/inspect_vpc_topology.py`
   - `scripts/inspect_vpc_routes.py`
   - `scripts/inspect_quotas.py`
   If script execution is not permitted, read the scripts as references and
   report that live script execution was skipped.
3. Load only the references needed for the task:
   - `references/project-selection.md`
   - `references/cloud-patterns.md`
   - `references/observability.md`
   - `references/route-inspection.md`
   - `references/quota-management.md`
   - `references/mk8s-compatibility.md`
   - `references/mk8s-gpu-setup.md`
4. Keep workflows idempotent and safe:
   - look up by name or ID first
   - create only when missing
   - prefer read-only inspection before mutating infrastructure
   - query live quota allowances at both tenant and project scope before planning or provisioning
   - aggregate shared quota consumers before comparing against available capacity
   - treat unresolved limits as warnings or coverage gaps, not as proof that quota is sufficient

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Guardrails

- Never print or commit private keys, tokens, or access key secrets.
- Do not parse `~/.nebius/config.yaml` to infer the current project. Follow
  `references/project-selection.md`, keep explicit task project selection
  authoritative, and fail closed when fallback discovery is invalid.
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
- For explicit subnet CIDRs, set `use_network_pools=false` and ensure the
  subnet CIDR list is a child of the network private pool. Do not create
  explicit subnet pools that overlap another explicit subnet range or a live
  private allocation in the same network.
- If a subnet needs a private CIDR outside the network's current private pool,
  extend an attached network private pool first, then create the subnet with an
  explicit child CIDR. Do not attach an arbitrary detached or wrong-tree pool
  to an existing network.
- When reusing pools, list all candidates and skip assigned, empty, public,
  wrong-tree, or incompatible pools; do not stop at the first name or CIDR
  collision.
- For new networks, unassigned private IPv4 pools with existing CIDRs can be
  offered as attach candidates. Hide pools already assigned to another network
  or subnet and hide empty pools.
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
- The public Nebius Compute docs currently restrict GPU-cluster compatibility to specific 8-GPU presets. Use live project inventory and `allow_gpu_clustering` metadata as the gate instead of hardcoding a stale preset list.
- Do not assume a GPU platform implies GPU clustering support. Different presets on the same platform may differ.
- Single-GPU presets are Ethernet-only. Treat them as testing/dev shapes and not as representative InfiniBand / GPUDirect-RDMA training environments.
- If Capacity Dashboard returns fabric-scoped rows for a 1-GPU or other non-clusterable preset, use that only as availability ranking metadata. It does not make `infiniband_fabric` valid for that shape.
- If the selected preset does not allow GPU clustering, leave `infiniband_fabric` unset or clear any stale value before render/apply.
- Prefer exact preset/image tags such as `cuda13.0`; do not use shorthand aliases such as `cuda13` or floating tags such as `latest`.
- If a compatibility lookup returns exactly one valid `drivers_preset` for the selected Kubernetes version and GPU platform, use that as the default while still allowing an explicit override.
- Do not trust stale static platform-to-driver maps without revalidating them against the live compatibility matrix.
- Nebius driverful GPU images and operator-managed GPU stacks are different operating modes. Do not install host GPU drivers or the NVIDIA Container Toolkit from GPU Operator on top of a Nebius driverful image unless the task explicitly requires changing that ownership model.
- `toolkit.enabled` in GPU Operator controls the NVIDIA Container Toolkit runtime, not the CUDA Toolkit.
- NVIDIA Network Operator is required for Nebius MK8s GPU node groups that do not use the Nebius GPU image and either join a GPU cluster for InfiniBand or use B200/B200A GPUs. In all other cases, follow the current Nebius docs and active automation contract instead of assuming Network Operator is always needed.
- If both NVIDIA operators are installed, keep exactly one NFD instance. Disable GPU Operator's NFD when Network Operator is the intended NFD owner.
- Do not treat `nvidia.com/gpu.deploy.operands=true` as a setup requirement. Prefer standard NFD labels, operator policy objects, and allocatable resources as the readiness signals.
- For GPU-cluster / InfiniBand readiness, a ready `NicClusterPolicy` alone is insufficient. Verify scheduler-visible RDMA resources such as `rdma/shared_device` on Ready GPU nodes.
- NCCL is appropriate for InfiniBand-capable GPU-cluster shapes. If it is configured on a single-GPU or other Ethernet-only shape, warn that the result is degraded and not representative of production distributed training.
- Do not blanket-enable `driver.rdma.enabled=true` in GPU Operator. Use it only when the task explicitly requires the legacy `nvidia-peermem` path instead of the default DMA-BUF path.
- Use `driver.rdma.useHostMofed=true` only when Mellanox OFED is installed directly on the host outside Network Operator's managed lifecycle.
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

### GPU assets

- `assets/gpu/gpu-operator-driverful-values.yaml`
  - reference values for the Nebius driverful-image path where GPU Operator should leave the host GPU driver and NVIDIA Container Toolkit untouched
- `assets/gpu/network-operator-driverful-values.yaml`
  - reference values for the Nebius driverful InfiniBand path where Network Operator owns NFD and host OFED installation stays disabled
- `assets/gpu/nicclusterpolicy-driverful-rdma-shared.yaml`
  - reference `NicClusterPolicy` for exposing `rdma/shared_device` on driverful InfiniBand-capable nodes
- `assets/gpu/gpu-operator-manual-values.yaml`
  - reference values for an operator-managed host path where GPU Operator owns the host GPU driver and NVIDIA Container Toolkit runtime
- `assets/gpu/network-operator-manual-values.yaml`
  - reference values for the operator-managed B200/B200A or InfiniBand path where Network Operator owns host OFED installation
- `assets/gpu/nicclusterpolicy-manual-rdma-shared.yaml`
  - reference `NicClusterPolicy` patch for exposing `rdma/shared_device` on operator-managed InfiniBand-capable nodes
- `assets/gpu/check-cluster-readiness.sh`
  - quick cluster-wide check for operator policy state, operator pods, daemonsets, labels, and allocatable GPU/RDMA resources
- `assets/gpu/inspect-driverful-host.sh`
  - privileged host inspection helper for checking installed GPU packages, NVIDIA Container Toolkit runtime config, and loaded kernel modules on a Nebius driverful GPU node
- `assets/gpu/proof-rdma-gpu-pod.yaml`
  - reference proof pod that requests both `nvidia.com/gpu` and `rdma/shared_device`

### Observability assets

- `assets/observability/public-endpoints.yaml`
  - public-safe endpoint/auth map plus cxcli config-branch summary for Monitoring, Logging, Tracing, the VM Monitoring agent, and the Kubernetes agent

### VPC inspection scripts

- `scripts/inspect_vpc_topology.py`
  - reports network parent pools, derived child pools, subnet allocation mode, explicit CIDRs, and inherited status CIDRs
- `scripts/inspect_vpc_routes.py`
  - reports effective route-table attachment, route-table consumers, and routes per table
- `scripts/inspect_quotas.py`
  - reports raw tenant/project quota allowances and effective available quota by `(name, region)`

When execution is permitted, run scripts relative to the skill directory.

## References

- `references/project-selection.md`
- `references/iam.md`
- `references/vpc-networking.md`
- `references/cloud-patterns.md`
- `references/observability.md`
- `references/route-inspection.md`
- `references/quota-management.md`
- `references/mk8s-compatibility.md`
- `references/mk8s-gpu-setup.md`
