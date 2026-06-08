# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Changed the generated report artifact contract from `generated/inventory/` to
  `generated/reports/`. Code paths now use `reports_dir` for deploy,
  validation, notification, and external Soperator migration reports, and new
  generated bundles scaffold `generated/reports/` without a compatibility
  alias.
- Fixed preserved-worker external Soperator cutover for heterogeneous worker
  shapes. `ext-soperator migrate --execute` now samples one live worker pod per
  preserved NodeSet for Slurm CPU/socket/core/thread topology, strips
  source-era chart-owned worker mounts, normalizes target operator affinity to
  Slurm role labels, and resumes Slurm nodes left drained after cutover.
- Fixed external Soperator GPU-stack remediation so direct
  `ext-soperator migrate --execute` Helm upgrades also apply catalog-owned
  post-render patches such as the Network Operator `NicClusterPolicy`
  `rdma/shared_device` overlay. Reruns now verify those live post-rendered
  fields before considering `target-gpu-stack-remediation` complete.
- Improved required Soperator/Slurm smoke validation: the one-task `srun`
  smoke job now prefers an idle non-GPU partition when available, while Slurm
  node status treats `inval` as unhealthy so invalid GPU workers remain a
  visible pending validation gate.
- Tightened external Soperator rerun idempotency. No-op `ext-soperator
  onboard` reruns now keep stable source discovery reports instead of churning
  timestamps that invalidate migration checkpoints, and `ext-soperator
  migrate --execute` rechecks completed selected actions against live state so
  missing GPU-stack releases, node-template drift, aligned-SFS gaps, or target
  cutover drift are retried instead of skipped solely because the local
  checkpoint said the phase had completed.
- Changed `ext-soperator migrate --execute` to own onboarded external MK8s
  control-plane and node-template upgrades through direct Nebius updates:
  control plane first, node groups one at a time with temporary zero-surge
  strategy, original strategy restore, and no extra preserved-worker quota.
- Fixed external node-template upgrade execution for legacy layouts by clearing
  stale GPU driver presets from CPU node groups before Kubernetes/OS rollout
  and by checkpointing temporary quiesce/restore of one-node
  controller/login/accounting workloads during zero-surge service-role updates.
- Hardened external Soperator chart takeover by suspending legacy Flux
  HelmReleases before applying the cxcli target chart, forcing server-side CRD
  conflict resolution, retrying transient target webhook startup failures, and
  resuming partial cutovers when the source login pod has already been removed.
- Fixed `keep-existing-storage` external chart takeover so live chart-owned
  SFS/local PersistentVolume nodeAffinity selectors are preserved in target
  Helm values instead of attempting an immutable PV selector update.
- Fixed `keep-existing-compute` external chart takeover so source worker
  NodeSet names and partition references such as `worker-gpu` and `worker-cpu`
  are preserved instead of collapsing them into a new synthetic `worker`
  NodeSet, and stale source-era camelCase `ephemeralStorage` resource keys are
  removed from adopted worker NodeSet CRs so target worker Pods can be created.
- Tightened external Soperator migration completion so completed-checkpoint
  reconciliation waits for target worker NodeSets to report desired-ready
  replicas before returning `Pending phase: none`.
- Changed `ext-soperator migrate --execute` validation hold to run the
  target-scoped `deploy.targets[].validations.mk8s_gpu.*` checks for the
  onboarded external target, including operator readiness, GPU Visibility, and
  NCCL when enabled, and refresh `generated/reports/deploy-report.md`.
- Added required Soperator/Slurm smoke validation for enabled Soperator
  targets. `deploy` now records a `soperator_cluster_smoke` JSON report and
  includes it in `deploy-report.md`; `ext-soperator migrate --execute` runs
  the same smoke validation during validation hold and writes
  `generated/reports/migrate-report.md` with migration phase, remediation,
  upgrade, layout, validation, and event summaries.
- Clarified the successful `ext-soperator onboard` config-only note so
  migration-required targets point to the Soperator-specific next steps instead
  of the generic deploy/destroy follow-up wording.
- Fixed Soperator onboarding source-version detection for legacy controller
  installs where the source operator Helm chart is released as
  `soperator-controller` in `soperator-system` with chart identity
  `helm-soperator`.
- Expanded the successful `ext-soperator onboard` footer to print the selected
  target's next-step command sequence: deploy for install/adopt-only targets,
  or migration dry-run and approved migration execute for migration-required
  targets.
- Aligned `ext-soperator --help`, `ext-soperator onboard --help`, and
  `ext-soperator migrate --help` examples with the complete external-cluster
  sequence, including the no-deploy-before-migration warning and zero-surge
  preserved-worker quota contract.
- Changed Soperator onboarding GPU/RDMA findings from an operator-owned placeholder
  action into target remediation: GPU-enabled external targets now record
  `remediate-target-gpu-stack`, add the target-scoped GPU Operator and
  Network Operator when GPU-cluster/RDMA inventory is present, persist MK8s GPU
  deploy-time validation defaults, show the target GPU remediation in
  `ext-soperator migrate --dry-run`, and execute it as a checkpointed
  `target-gpu-stack-remediation` phase before Soperator compute/cutover work.
- Renamed Soperator migration execution stop points to pending phases and
  changed incomplete onboarding analysis wording to concrete
  action-required/source-version-required statuses.
- Split Soperator existing-cluster onboarding out of the `create` wizard:
  selecting Soperator in `create` now stays on the production MK8s+SFS path,
  while the new `nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>`
  command registers an external Nebius MK8s target, can scaffold a new
  tenant/project `config.yaml` from a deployments root, lists existing MK8s
  clusters in the resolved Nebius project for interactive onboarding,
  registers one selected cluster per run by storing its `cluster_id`, repairs
  target-scoped Soperator dependencies, and refreshes the accepted onboarding
  fingerprint.
- Tightened Soperator onboarding acceptance so day-2 Soperator Helm chart
  version edits do not invalidate an already accepted target, exact pinned
  Soperator releases classify as `existing-soperator-target`, lower `-ps.N`
  package variants plan an upgrade to the cxcli target, and old-layout
  migration plans persist `create-aligned-sfs` whenever aligned SFS data
  migration is required.
- Aligned Soperator external-cluster onboarding around two explicit layer
  choices: storage mode (`keep-existing-storage` or `create-aligned-sfs`) and
  compute mode (`keep-existing-compute` or `create-aligned-node-groups`).
  Discovery still recommends aligned SFS for missing or incompatible storage,
  and old profiled releases still default to full storage+compute migration,
  but explicit keep-existing choices now narrow the saved migration plan.
- Matched Soperator onboarding deployments-root behavior to the `create`
  identity flow: after resolving tenant/project, interactive runs warn and ask
  before updating an existing resolved `config.yaml`, while non-interactive
  `--tenant-id`/`--project-id` runs print the warning and continue.
- Added Soperator onboarding source-version recovery: when discovery finds
  Soperator CRDs but no compatible Helm release version, interactive onboarding
  asks for a committed migration-profile version or manual version entry, and
  non-interactive runs can pass `--source-version`.
- Added `nebius-cxcli ext-soperator migrate <config.yaml>` as the explicit
  Soperator migration command surface. It validates the accepted onboarding
  analysis, reads `source-soperator-cluster-discovery-report.json`, prints the
  target remediation and compute/storage migration plan in dry-run mode, and
  runs checkpointed live phases in `--execute` mode. The executor rechecks the
  live source release and discovery fingerprint before the first mutation,
  records customer approval when `--approve` is passed, auto-detects source
  worker node groups from live Nebius node-group names and Slurm worker labels,
  creates or reuses aligned SFS filesystems, attaches them to
  discovered Nebius node groups, runs data-copy Jobs when PVC pairs exist, and
  executes the guarded compute path by creating or reusing aligned service-role
  MK8s node groups, verifying an empty Slurm queue from a login pod, applying
  the pinned target Soperator chart values to preserved worker node groups,
  normalizing target Slurm runtime plugin settings, recreating target worker
  Kruise StatefulSets when immutable source-era specs cannot be updated in
  place, validating cutover resources, and preserving in-place worker node
  groups while holding old storage retirement for explicit confirmation.
- Added phase-aware live status output for approved `ext-soperator migrate
  --execute` runs. Storage phases now report aligned SFS/PVC copy progress
  alongside MK8s and Slurm continuity, while compute/cutover phases report
  MK8s node readiness, Slurm login/queue/node-state health, and Soperator
  SlurmCluster reconciliation as best-effort degradation signals.
- Added a strict Soperator migration quota preflight for approved `--execute`
  runs. The executor now checks net-new aligned SFS storage and net-new
  service-role node groups before any SFS, node-group, or Helm mutation, while
  preserved worker node groups do not require parallel worker quota, and fails
  fast on confirmed shortages, unresolved limits, coverage gaps, or quota lookup
  errors. External node-group template mutations now use a temporary zero-surge
  strategy (`max_surge=0`, `max_unavailable=1`, `drain_timeout=30m`) and restore
  the original strategy afterward, so preserved workers require neither
  parallel nor surge worker quota.
- Consolidated README Soperator command guidance into a visible
  `Soperator Commands` section covering managed create/deploy, external
  onboarding, migration flags, storage/compute migration modes, safety rules,
  and the difference between `upgrade helm-chart` for cxcli-managed Soperator
  rows and onboard+migrate workflows for external clusters.
- Hardened the Soperator migration profile generator with paginated GitHub
  release fetching, release tarball extraction, official chart identity
  detection, per-component chart archive, CRD, template, values, image, and
  Slurm contract fingerprints, and tests that lock the expanded generator
  scope.
- Added node-role label compatibility axes to committed Soperator migration
  profiles so legacy source labels such as `slurm.nebius.ai/nodeset` are
  explicitly normalized to the target `slurm.nebius.ai/nodeset-name` contract.
- Updated Soperator migration compute remediation to reuse existing
  service-role node groups discovered by role name or
  `slurm.nebius.ai/nodeset` / `slurm.nebius.ai/nodeset-name` labels, while
  preserving worker node groups in place and using external zero-surge
  node-group updates for migration-owned template remediation.
- Added a final `Next step: nebius-cxcli deploy <config.yaml>` helper line to
  successful `render` command output, while suppressing that hint for internal
  rerenders used by upgrade flows.
- Clarified `component add` help and examples so `--config` is shown as the
  required config path option, target-bound app examples use the plural
  `apps:<chart>@<target>` selector form, and singular `app:` selector errors
  point operators at `apps:`.
- Clarified `upgrade node-template` help so the command list and subcommand
  help call out that Kubernetes version, OS image, and GPU stack move together
  in one non-interactive command with examples below.
- Updated the development lockfile to `aiohttp` 3.14.0 to resolve the open
  Dependabot alerts on `services/nebius-cxcli/uv.lock`.
- Fixed high-priority teardown, recovery, notification, and observability
  guardrails: `destroy` now stops before Terraform when rendered app teardown
  fails, MK8s destroy recovery refuses unconfirmable node-group delete
  operations, non-force Terraform unlock refuses ownerless locks, deploy-report
  email setup/sync/send require STARTTLS and redact tenant/project identifiers,
  and OTLP EndpointSlice readiness now requires `ready=true`.
- Added `usage.lifecycle: transient` and `usage.config.ref` metadata for
  deploy-time Helm chart sources such as `nccl-test`, with catalog validation
  and selector guidance driven from that metadata instead of a hard-coded chart
  id.
- Fixed NCCL deploy validation on one-GPU Ethernet-only MK8s targets so a
  successful single-rank smoke run passes without requiring collective bus
  bandwidth, while multi-rank checks still require observed bandwidth and RDMA
  checks still enforce the configured threshold.
- Fixed MK8s Nebius-image GPU stack selection so live compatibility matrix
  choices are constrained by the selected/defaulted OS, stale profile defaults
  are replaced during create/component add when live choices exclude them, and
  `validate-generated`, deploy, and direct generated-bundle `terraform apply`
  fail before Terraform if an existing config combines an unsupported GPU
  platform, OS, and `gpu_stack_preset` or omits OS while setting an
  OS-specific GPU stack preset. The live compatibility lookup now shares the
  provider timeout policy and accepts both top-level and version-nested matrix
  response shapes.
- Added the top-level `upgrade` command group with `upgrade k8s-version
  <config.yaml> [infra:mk8s@<target>] --to-version <major.minor>` implemented for
  Terraform-managed MK8s targets. The new flow plans with live Nebius SDK data,
  prompts for target/version/core options from a config-only interactive
  wizard, supports `--no-interactive` fail-fast automation, `--dry-run`,
  disruption policies, and drain-timeout defaults, syncs `config.yaml` plus
  `generated/`, runs Terraform plan and apply in staged control-plane then
  per-node-group order, writes explicit node-group versions during the upgrade,
  uses the SDK for live discovery/status/error watching rather than MK8s
  mutation, keeps provider drain timeout separate from cxcli's rollout wait
  budget, sizes SDK node-group rollout watches by node-group size, defaults
  `allow-unavailable` drain timeout to `30m`, uses the explicit disruption
  policy instead of a separate upgrade `--yes` confirmation, matches live node
  groups by Terraform-default names, blocks mutation when Kubernetes preflight
  inspection cannot prove safety, restores temporary node-group disruption
  strategies in source/generated files after failed stages,
  reconciles stale source config even when live resources are already at the
  target version, resumes already-started rollouts, and reserves explicit
  command/flag shapes for GPU stack, platform, hardware preset, and Helm chart
  layers.
- Implemented `upgrade os-image <config.yaml> infra:mk8s@<target> --to-os <os>`
  for Terraform-managed MK8s node groups. The command validates the requested
  node-template OS against the live Nebius MK8s compatibility matrix for the
  current Kubernetes version, platform, and GPU stack preset, updates
  `inputs.node_groups.*.os`, rerenders and validates `generated/`, runs quiet
  Terraform plan/apply stages one node group at a time in CPU/system-before-GPU
  order, supports `--node-group`, `--dry-run`, disruption policies, and
  drain-timeout defaults, and waits for Managed Kubernetes rolling replacement
  to finish without SSHing to nodes or running apt-based OS upgrades.
- Implemented non-interactive
  `upgrade node-template <config.yaml> infra:mk8s@<target> --to-version <major.minor> --to-os <os> [--to-gpu-stack-preset <preset>]`
  for combined MK8s control-plane, node-group Kubernetes version, node OS, and
  Nebius-image GPU stack upgrades. The command validates the requested tuple
  through the SDK compatibility matrix, stages control plane first, then writes
  version, OS, and Nebius-image `gpu_stack_preset` together for each selected
  node group in CPU/system-before-GPU order so the group rolls once. The GPU
  stack flag is required for selected Nebius-image GPU groups and rejected for
  CPU-only or operator-managed GPU selections. Generated-bundle GPU stack
  compatibility validation now honors explicit node-group `version` values, so
  a staged control-plane hop can validate old node templates against their
  pinned node-group version until the node-group stage writes the new template.
- Added a guided `upgrade os-image <config.yaml>` wizard that lists
  Terraform-managed `infra:mk8s@<target>` targets and generic
  `infra:vm@<target>` components, prompts for missing OS-image values, defaults
  guided runs to dry-run, supports `--no-interactive` fail-fast automation, and
  implements generic `infra:vm@<target>` `source_image_family` upgrades through
  the same config/render/validate/Terraform plan/apply path with the selected
  VM status watcher. VM upgrades are limited to module-managed boot disks that
  use `inputs.source_image_family`; `source_image_id`, `boot_disk_existing_id`,
  node-group, and MK8s disruption-policy semantics remain outside the VM path.
- Simplified the guided MK8s `upgrade os-image` `node_group` prompt so it maps
  directly to the optional `--node-group` flag: blank omits the flag and updates
  all managed node groups, while a typed source key or live name narrows the
  upgrade to one group.
- Wrapped upgrade dry-run repeat commands across shell-safe continuation lines
  so long config paths and selected flags remain readable and copy-pasteable.
- Implemented `upgrade gpu-stack-preset`, `upgrade platform`,
  `upgrade cpu-preset`, `upgrade gpu-preset`, and `upgrade helm-chart` with the
  same wizard/non-interactive flag contract as `k8s-version` and `os-image`.
  The node-layer commands update selected MK8s node-group desired-state fields,
  support `--node-group`, `--dry-run`, disruption policies, drain-timeout
  defaults, rendered-bundle validation, quiet Terraform plan/apply, and rollout
  waits; CPU preset changes target only CPU/system groups, GPU preset and GPU
  stack changes target only GPU groups, and GPU stack/platform changes use the
  live MK8s compatibility matrix where applicable. The Helm chart command
  updates the selected target-scoped `apps.charts[]` version, rerenders,
  validates, and applies that target's Flux bundle. The GPU-stack command uses
  the explicit `--to-gpu-stack-preset` flag for Nebius `drivers_preset`; hardware
  CPU/GPU preset commands keep `--to-preset`.
- Refactored guided upgrade value prompts through a reusable upgrade wizard
  choice builder and provider lookup path. MK8s OS image, GPU stack preset,
  platform, CPU preset, and GPU preset prompts now show live SDK/provider-driven
  choices when available instead of falling back immediately to raw required
  scalar input; non-interactive flags continue to use the same shared execution
  path.
- Aligned component wizard provider-option parsing so interactive choice
  rendering, strict provider-value validation, and auto-selected defaults use
  the same wizard metadata resolver while preserving planned VPC choices and
  legacy static-choice prompts.
- Reorganized README upgrade guidance into a dedicated top-level `Upgrade`
  section with a visible table-of-contents entry, disruption-policy
  drain-timeout defaults, copy-paste Kubernetes upgrade examples, node-layer
  upgrade examples, Helm chart upgrade examples, and manual desired-state
  fallback guidance.
- Aligned `upgrade --help` and upgrade subcommand help output with the README
  upgrade examples, including implemented Kubernetes dry-run/disruption-policy
  examples, node-layer examples, and Helm chart examples.
- Removed public/private endpoint access from the guided `upgrade k8s-version`
  target picker labels so managed MK8s targets are shown by selector only,
  avoiding confusion with external-cluster ownership.
- Clarified guided and explicit `upgrade k8s-version` multi-minor handling with
  upstream Kubernetes guidance that skipped minor upgrades are unsupported.
- Improved guided `upgrade k8s-version --dry-run` output by aggregating
  `emptyDir` pod findings into one PVC-aware advisory, printing a repeatable
  dry-run command with the selected arguments, and styling the warnings section
  with the shared amber warning color.
- Suppressed raw Terraform plan dumps during live `upgrade k8s-version` staged
  applies while still running each staged plan as a safety gate before apply.
- Fixed live `upgrade k8s-version` staged Terraform plans when a temporary
  node-group disruption strategy is applied to only the node group currently
  being upgraded.
- Hardened `upgrade k8s-version` ordering by rejecting live node groups that
  are already above the requested control-plane minor, and documented the
  post-upgrade GPU canary, add-on, and rollback boundaries.
- Clarified live `upgrade k8s-version` output so execution stages are labeled
  as per control-plane hop and per node group rather than per node, and
  de-duplicated repeated deploy-validation advisories across nested render and
  validation calls within one upgrade run.
- Clarified `upgrade k8s-version` OS/platform/GPU compatibility blockers so
  implemented OS-image, platform, and GPU-stack node-layer commands are printed
  as runnable follow-up commands where available, while manual
  config/render/deploy follow-up remains documented. Also tightened the
  `force-delete` warning around graceful shutdown and in-flight application
  state.
- Documented that manual desired-state upgrades through `config.yaml`, render,
  plan review, and `deploy`/`terraform apply` remain supported outside the
  structured `upgrade` command, with `deploy` running full generated-bundle
  preflight and `terraform apply` preserving the infra-only MK8s preflight and
  Terraform/provider validation path.
- Removed the reserved `upgrade firmware` command surface and documented node
  firmware as owned by the Nebius hardware team rather than a customer upgrade
  responsibility.
- Clarified the MK8s node-group service-account wizard prompt so the default
  no-service-account path is the first semantic choice, without an extra
  generic skip row, and the existing/create choices explain what they do.
- Changed `create <deployments-root>` to create the deployments root directory
  when it is missing, while keeping `discover` strict about existing
  deployment-scope directories and preserving the nested managed-root guard.
- Changed interactive `create --force` for an existing resolved project folder
  to treat `--force` as the overwrite confirmation. The CLI still prints the
  existing-project warning, but it no longer asks the follow-up overwrite
  question when the operator already passed `--force`.
- Changed live project VPC network choices to recommend the Nebius
  `default-network` when present, so any wizard profile backed by
  `project_networks` opens with that existing network selected instead of the
  create-new or first-ID fallback. The explicit `Create a new VPC network` row
  remains available for `infra:vpc` when a new network is needed.
- Switched rendered Terraform roots and bundled module validation to the
  official public Nebius Terraform provider source `nebius/nebius` with the
  shared constraint `>= 0.6.8, < 0.7.0`, and updated the cxcli-managed
  Terraform client version to `1.15.5`.
- Updated the bundled Soperator app catalog pin to chart version
  `3.0.5-ps.1`, and made local-profile Helm chart sources derive missing
  chart name/version metadata from their checked-out `Chart.yaml` so generated
  `config.yaml` rows show the active local chart version. The bundled
  Soperator profile defaults now use the matching `3.0.5-slurm25.11.3`
  worker and Munge image tags.
- Added live VPC network/subnet selection for subnet-attached infra in `create`
  and `component add`. `mk8s`, `vm`, `nfs`, `wireguard-gw`, and
  `ssh-jumphost` now list project VPC networks first, list only subnets in the
  selected network, auto-select only singleton choices, and support explicit
  `--network-id` / `--subnet-id` values with scoped selectors when several
  applicable infra rows are selected.
- Added the Terraform-owned `infra:vpc` component and row-level
  `infra.components[].bindings` so configs can bind MK8s/VM-style workloads to
  a planned VPC network or subnet created by the same config. Planned selectors
  use `--network-ref` / `--subnet-ref`; literal `--network-id` /
  `--subnet-id` remain live-ID-only.
- Fixed interactive same-run planned VPC wiring so selected `infra:vpc` rows
  are configured before MK8s and VM-style consumers, making newly declared VPC
  subnets available as planned subnet choices in the same `create` or
  `component add` field wizard pass.
- Fixed row-level planned VPC bindings during render so config targets such as
  `inputs.network_id` and `inputs.subnet_id` materialize as direct Terraform
  module arguments instead of an unsupported nested `inputs` argument.
- Changed the interactive `create` wizard to show app chart selection only
  after an MK8s target is selected, and changed the `infra:vpc` wizard to
  collect planned subnets through guided name/private-CIDR prompts instead of a
  raw YAML/JSON map prompt. Existing-network VPC rows now skip the new-network
  name prompt and collect only planned subnets for that network; new VPC rows
  can also create a network with no subnets. New-network VPC rows now label
  the skip row as `Create a new VPC network` and prompt for
  `inputs.network.ipv4_private_cidrs` before subnet creation. Network CIDR
  prompts now suggest custom private non-default `10.x` `/13` ranges such as
  `10.8.0.0/13`, `10.16.0.0/13`, `10.32.0.0/13`, `10.40.0.0/13`, and
  `10.56.0.0/13`, plus `172.16.0.0/12` and `192.168.0.0/16`, outside
  Nebius' documented regional default private-pool ranges;
  direct config can instead set
  `inputs.network.ipv4_private_pool_ids`, and the wizard now lists live
  unassigned `project_private_pools` so new VPC networks can attach an
  available existing private pool before falling back to creating a managed
  pool from CIDR. Direct config can set
  `inputs.network.ipv4_private_source_pool_id` when the managed pool must be
  carved from an existing Nebius source pool. Declared subnets now always use
  explicit private CIDRs: cxcli records `use_network_private_pools=false`, and
  subnet CIDRs must fit inside the selected network range, including
  default-network private ranges already attached to the selected parent, and
  must not overlap another subnet or live private allocation in that network.
  For Terraform-owned new networks, the wizard adds any out-of-parent custom
  subnet CIDR to
  `inputs.network.ipv4_private_cidrs` first so Terraform extends the parent
  network IP space before creating the explicit subnet child range; for live
  `inputs.network.existing_id` networks, it now adds a selected or manually
  entered out-of-parent subnet CIDR to an attached private pool on the selected
  live network before recording the subnet with explicit private pools
  (`use_network_private_pools=false`). Terraform ownership of that existing
  network remains external to the generated config. The
  VPC module now validates explicit public pool
  IDs, documents that Nebius attaches the default public pool and default route
  table when public pools or route tables are omitted, and exposes the
  Nebius-reported default route-table and effective network-pool metadata in
  outputs.
- Changed `infra:vpc` subnet CIDR prompts to suggest deterministic child CIDRs
  from the selected parent VPC private-pool ranges while avoiding known
  explicit subnet CIDRs and live private allocations, so an existing live
  `default-network` with attached private CIDR metadata offers explicit subnet
  CIDR choices instead of falling back directly to free-form input. For
  Terraform-owned new networks, the same prompt also includes suggested new
  parent blocks that cxcli can add to `inputs.network.ipv4_private_cidrs`
  before subnet creation. Existing live networks now combine those child CIDR
  suggestions with already attached RFC1918 extension blocks such as
  `172.16.0.0/12` and `192.168.0.0/16` when no explicit subnet CIDR or live
  private allocation overlaps them; selected or manually entered out-of-parent
  CIDRs extend an attached private pool on the selected live network before
  the subnet is recorded with `use_network_private_pools=false`.
- Changed the guided `infra:vpc` subnet custom-CIDR prompt to accept one or
  more comma-separated explicit private CIDRs, matching the Terraform module's
  `list(string)` shape while keeping the same parent-fit, overlap, and live
  allocation checks across the full list.
- Changed interactive `component add` so answering `n` at a newly added infra
  component's field phase cancels that pending infra row instead of writing an
  unconfigured component. App chart phases keep the existing behavior where
  `n` preserves the selected chart with catalog/default values.
- Added live `project_filesystems` lookup and VM `inputs.sfs_attachments`
  rendering so VM components can attach either existing SFS filesystems or
  planned `infra:sfs` filesystem outputs without passing cxcli helper fields to
  Terraform modules.
- Broadened the former MK8s-only preflight into a shared VPC networking
  preflight. Validation, render/deploy preflight, post-create validation, and
  post-component-add validation now verify that selected networks and subnets
  belong to the project and that each selected subnet belongs to the selected
  network, including MK8s node-group subnet overrides.
- Fixed VPC pool CIDR parsing so `project_private_pools` and VPC networking
  preflight handle Nebius SDK responses that expose CIDRs as either strings or
  objects.
- Fixed the `project_private_pools` wizard source so new VPC network prompts
  list only unassigned private IPv4 pools that already have at least one CIDR,
  and recognize assignment IDs exposed through either `networks`/`subnets` or
  `network_ids`/`subnet_ids` SDK fields.
- Fixed existing-network VPC parent-pool extension to update the selected
  network's attached private pool CIDR list directly, which matches the Nebius
  custom-private-address workflow and avoids creating or attaching detached
  root pools.
- Fixed VPC runtime validation to reject malformed subnet entries that are not
  mappings, so direct config cannot bypass the explicit subnet private-CIDR
  contract.
- Hardened cxcli diagnostics around dynamic provider lookups, Grafana runtime
  status, deployment status pollers, VPC/MK8s preflight, quota preset lookup
  retries, emitted kubectl helper commands, and malformed JSON responses so
  transient or malformed inputs no longer degrade into silent empty results.
- Fixed local Helm chart dependency staging so clean runners can render charts
  with locked remote dependencies without preconfigured global Helm repo
  entries, and stale staged copies no longer cascade into follow-on failures.
- Made NCCL launcher placement resource-aware: cxcli now pins the launcher to
  non-GPU nodes only when they have enough scheduler-visible CPU/memory
  headroom, otherwise it falls back to GPU-node headroom accounting.
- Added strict cxcli validation for `wireguard-gw` and `ssh-jumphost`
  `inputs.public_ip_allocation_name` values so Terraform resource-name regex
  failures surface before deploy.
- Highlighted the NCCL average bus bandwidth value in the generated
  `deploy-report.md` Markdown summary while keeping terminal diagnostics and
  deploy footers plain.
- Removed deprecated VM preemptible priority handling from cxcli's VM
  wizard/render contract; preemptible VM flows now only materialize
  `recovery_policy=FAIL` and pass `preemptible_enabled` to the VM module,
  which requires Nebius Terraform provider `>= 0.6.8`; generated Terraform
  roots now use that same provider floor.
- Made local Helm chart renders explicit about cert-manager
  `Certificate.spec.privateKey.rotationPolicy=Always`, covering Soperator
  post-Flux manifests and dependency-rendered webhook certificates. Portable
  Soperator Flux `HelmRelease` output now also carries matching post-render
  patches for the Soperator and MariaDB Operator webhook certificates, so
  cert-manager 1.18+ no longer emits default-change warnings in generated
  cxcli deployment paths.
- Retried the initial rendered Flux `kubectl apply -k` step for known transient
  Kubernetes API transport failures such as connection resets, while preserving
  immediate failures for validation, RBAC, and admission errors.
- Completed the SFS wizard/render contract across standalone, MK8s-attached,
  and Soperator-managed layouts: scalar SFS now exposes `mount_tag`, Soperator
  production profiles share one complete jail/controller-spool/accounting
  filesystem default map with explicit block size and deletion guard values,
  and focused tests cover scalar and multi-filesystem Terraform rendering.
- Bound Soperator production-profile chart-managed MariaDB storage to the
  accounting SFS-backed `slurm-local-pv` storage class, so the generated
  accounting filesystem is consumed by the accounting database path instead of
  only rendering the accounting mount/PV surface.
- Collapsed the Soperator SSSD wizard surface to one curated
  `values.sssd.enabled` identity gate. When enabled, cxcli now materializes
  both `values.slurmNodes.sssd.enabled=true` and generated
  `values.nodesets[].sssd.enabled=true`; when explicitly disabled, it clears
  those generated identity surfaces. The raw chart values remain available for
  advanced direct `config.yaml` edits when the guided helper is absent.
- Added Soperator production wizard helpers for CPU service-role node counts:
  `inputs.soperator.system_node_count`, `controller_node_count`,
  `login_node_count`, and `accounting_node_count`. The wizard still hides raw
  profile-owned `inputs.node_groups.*` fields, but those curated helpers now
  materialize the `system`, `controller`, `login`, and `accounting` MK8s
  `node_count` values alongside the existing worker sizing helpers.
- Added disabled-by-default Soperator production autoscaling helpers for each
  generated MK8s role. `inputs.soperator.<role>_autoscaling.*` now materializes
  concrete `inputs.node_groups.*.autoscaling` blocks and removes the conflicting
  fixed `node_count` for `system`, `controller`, `login`, `accounting`, and
  worker shards. Repeated materialization also clears stale concrete
  autoscaling blocks when a helper is disabled and preserves explicit worker
  `0..0` autoscaling instead of falling back to the profile's default worker
  count; service-role autoscaling rejects `max_node_count=0`.
- Hid the raw Soperator `rebooter.enabled` gate from the normal guided wizard
  while keeping explicit `config.yaml` overrides supported. The docs and
  warnings now describe it as a cluster-level NodeConfigurator maintenance
  helper and RBAC, not a per-NodeSet switch, install-time reboot, or chart-owned
  reboot schedule. They also describe the upstream condition-driven,
  `NoExecute` taint-based drain path, with examples of the maintenance and
  degraded-node condition chains that set `SlurmNodeDrain` and
  `SlurmNodeReboot`, and clarify advanced production-maintenance mode:
  `NebiusMaintenanceScheduled=True` is graceful drain/node handoff while
  `SlurmNodeReboot=True` is the actual host reboot path after drain.
- Made Soperator production profiles explicitly keep Slurm accounting, SlurmDBD,
  and chart-managed MariaDB enabled, and clarified partition-profile labels/docs
  so baseline/debug queue choices are not confused with disabling accounting.
- Added a redacted guided `create` example to command help and README for
  preseeding client, tenant, project, infra, and app selections while skipping
  source and post-write config validation.
- Hid Soperator ActiveChecks readiness partitions from guided partition-profile
  choices and source config. cxcli now keeps only the ActiveChecks intent toggle
  in the guided surface and derives the readiness/check partition from the
  selected profile as render-time Helm values when ActiveChecks are enabled.
  The internal `hidden` partition is also stripped from source config and
  injected only for ActiveChecks-enabled renders that need it.
- Fixed create-time target identity alignment so an entered MK8s
  `cluster.cluster_name` is applied before app wizard default previews and
  internal target refs are materialized. Target-scoped apps such as Soperator
  now keep `instance_id`, derived `target_ref`, and `values.clusterName`
  aligned to the cluster target name before render, preventing client-name or
  placeholder target drift in config and generated artifacts.
- Added concise app chart default previews before interactive app field prompts,
  capped to four lines, so answering the default `n` shows the Helm
  defaults that will be kept. The Soperator preview now surfaces
  SFS-derived jail/controller-spool/accounting sizes while SFS remains the
  capacity source of truth and the app row mirrors those sizes into chart
  storage values.
- Fixed the Soperator production MK8s wizard so GPU Visibility and NCCL
  deploy-time validation toggles are prompted alongside GPU stack readiness
  instead of being suppressed by the Soperator app policy. Soperator
  ActiveChecks and Soperator DCGM child charts remain disabled by default;
  validation continues to use cxcli-owned deploy-time checks such as the
  transient `nccl-test` chart.
- Fixed the interactive Soperator production-cluster `create` and
  `component add` wizards so the worker layout profile is selected immediately
  after `install_mode`, before MK8s shape/fabric helpers and target GPU
  validation prompts. CPU-only Soperator profiles now also skip and prune the
  inactive `inputs.node_group_defaults.gpu.*` helper scope instead of offering
  GPU fabric fields, including during direct `config.yaml` normalization.
  Soperator onboarding mode now also skips same-transaction `mk8s`/`sfs` infra
  selections so external MK8s onboarding does not create Terraform-managed
  cluster rows. Soperator worker profile materialization now honors
  `worker_nodes_per_group` as the generated MK8s node-group shard size even
  when the profile also uses `worker_total_nodes`. Non-interactive
  `component add soperator@<external-target>` now infers onboarding for
  existing external MK8s targets and repairs missing target-scoped
  Soperator-required app rows without adding Terraform MK8s/SFS rows. External
  onboarding now writes a source-cluster discovery report next to the project
  config, records stable `no-soperator-detected` or existing-Soperator
  migration states, matches installed releases against committed migration
  profile history, and plans `keep-existing-storage` or `create-aligned-sfs`
  remediation without embedding the full discovery snapshot in `config.yaml`.
  The
  local-storage onboarding path also defaults `populateJail.overwrite: true` so
  failed partial installs do not leave stale jail sentinel files that skip
  required jail population on the next deploy.
- Moved the bundled Soperator `with-qos-preemption` profiles from raw
  `customSlurmConfig` accounting enforcement lines to typed
  `schedulingConfig.accountingStorageEnforce` and
  `schedulingConfig.enforcePartLimits` values, matching the parent chart's
  typed scheduling contract.
- Fixed top-level `destroy <config.yaml>` for generated managed MK8s bundles so
  it attempts rendered app teardown before Terraform cluster destroy. The
  teardown now also removes locally applied post-Flux manifests and rendered
  admission webhooks before namespace deletion, selects all generated deploy
  targets for project-wide teardown, attempts every selected target before
  reporting target-specific teardown failures, and gives Kubernetes finalizers
  and CSI cleanup a chance to remove app-owned PVC-backed disks while still
  falling back to Terraform cluster destroy if the managed cluster is
  unreachable during teardown.
- Added README guidance for Soperator Slurm scheduling, concept ownership,
  preemption, partition, config, fairshare, niceness, and QOS inspection
  commands, including the smoke-test command patterns used for baseline,
  debug/long, and QOS partition profiles.
- Refined the Soperator Slurm inspection examples to use the login
  LoadBalancer service and SSH path first, then run Slurm commands directly
  from the login node.
- Aligned the Soperator optional-service gate contract with the parent Helm
  chart: direct Helm installs now keep the NodeConfigurator rebooter disabled
  by default, and docs distinguish child chart gates from in-chart SSSD and
  rebooter gates. The chart keeps a no-op NodeConfigurator custom container so
  host-setup initContainers still render a valid DaemonSet while rebooter is
  off.
- Renamed wizard metadata `materialize_default: true` to
  `write_default_to_config: true` and reject the old key in component source
  catalogs, keeping the prompt-default persistence contract explicit.
- Fixed non-interactive `create --infra mk8s --app soperator` so the bundled
  MK8s profile writes the provider-ranked default network and subnet before
  render/deploy, avoiding Terraform failures from missing required
  `cluster.network_id` / `cluster.subnet_id`.
- Fixed non-interactive Soperator GPU production defaults so cluster-capable
  GPU shapes auto-select the provider-ranked InfiniBand fabric when live fabric
  choices are available, keeping generated H100/H200/B200-style profiles on the
  reserved/fabric-aware GPU-cluster path instead of accidentally trying
  unclustered on-demand capacity.
- Fixed Soperator GPU and mixed production worker profiles to render
  `reservation.policy: AUTO` on GPU worker node groups, so reserved-capacity
  fabric recommendations can actually use matching reservations while still
  falling back to suitable capacity.
- Normalized the bundled Soperator catalog/settings authoring by moving the app
  wizard prompt map into built-in `wizard_profile: soperator`, while keeping
  the large `soperator_nodesets_profile` policy table in
  `component_cli_settings.yaml` and preserving the resolved catalog contract.
- Fixed the wheel packaging path so bundled app chart sources get the same
  `source.local` stripping and release-ref rewrite as Terraform module sources;
  the branch wheel-bundle verifier now fails if local source entries leak into
  the packaged catalog without requiring every app to have a release-grade
  portable source.
- Fixed Soperator profile/policy rematerialization so wizard or direct
  `config.yaml` switches from the generated GPU baseline to CPU or mixed
  profiles recompute profile-owned node groups, `nodeGroupMapping`, NodeSets,
  partitions, and topology settings. Runtime config loading now materializes
  Soperator before MK8s GPU app normalization, so CPU-only Soperator configs no
  longer re-add or retain GPU Operator rows from stale GPU node-group defaults.
- Fixed config normalization around explicit app rows and Soperator onboarding:
  CPU-only MK8s configs now preserve enabled GPU/platform app rows that carry
  explicit chart source metadata while still pruning stale Soperator-owned GPU
  Operator rows and other auto target-scoped GPU policy rows, external
  Soperator onboarding storage selectors stay scoped to discovered node groups
  while generated Soperator profiles keep their profile jail aliases, and
  accepted onboarding fingerprints remain valid across deterministic Soperator
  default materialization plus unrelated `component add` changes.
- Added catalog-owned GPU shape defaults to the Soperator GPU and mixed
  production profiles so default non-interactive or skipped-field Soperator
  bundles still render Terraform-valid GPU worker node groups.
- Aligned create/component wizard defaults for Soperator-led MK8s projects:
  selected observability apps now default the matching MK8s target
  observability switch to enabled, SFS prompts show the Terraform-backed
  `sfs` / 1024 GiB / `NETWORK_SSD` / 4 KiB /
  deletion-protection-off defaults, the production Soperator layout defaults to
  one node per generated role group including system roles and mixed worker
  NodeSet replicas, and the QoS reconciliation prompt is shown only for
  QoS-capable partition profiles.
- Added explicit `create` and `component add` adjusted-selection notices for
  Soperator-owned dependencies, so auto-added `sfs` and `cert-manager` rows are
  explained alongside generic app `release.install_after` dependencies.
- Removed the Soperator profile/chart default `PluginDir` override after live
  H100 deployment showed Slurm 25.11 fails when a static multi-arch path
  includes a directory absent from the selected image. Image-specific plugin
  paths now stay image-owned unless an operator explicitly overrides
  `customSlurmConfig`.
- Aligned omitted MK8s GPU stack-source behavior so cxcli and the MK8s
  Terraform module both default GPU node groups to the Nebius GPU image path.
- Reworked the Soperator create wizard to stay on a concise guided surface:
  raw parent chart values are hidden by default, skipping the app field phase now
  prints the production layout that will be kept, and the prompted fields focus
  on profile, partition, topology, and top-level service gates. ActiveChecks,
  the checks controller, Soperator DCGM job mapping, notifier, backup, QoS
  reconciliation, SSSD, and NodeConfigurator rebooter now default off, with
  deploy-validation warnings when production-impacting Soperator check or DCGM
  child charts are explicitly enabled.
- Fixed MK8s GPU validation prompts so enabled GPU visibility and NCCL checks
  materialize their default `max_nodes` caps. Soperator ActiveChecks remain
  opt-in diagnostics rather than production-training defaults, while cxcli
  deploy-time GPU visibility/NCCL validations stay available on Soperator
  targets.
- Clarified the SFS wizard's Weka/VAST choices as advanced quota-gated
  filesystem types after live validation showed Weka is not currently
  provisionable in the tested project because its Weka filesystem quota is zero.
- Optimized the local test suite by avoiding repeated Soperator Helm dependency
  rebuilds across `tests/test_render.py` and removing real wait loops from
  stubbed MK8s GPU, strict-validation, and Terraform streaming unit tests.
- Fixed pure CPU Soperator profile materialization so `nebius-cpu-v1` maps the
  Slurm worker role only to the generated `worker-cpu` node group, keeps service
  groups out of the CPU partition, and disables the Soperator DCGM exporter when
  no GPU node groups exist.
- Added a deploy-validation warning when Soperator NCCL ActiveChecks and cxcli
  deploy-time NCCL validation are both runnable on the same MK8s target,
  explaining that the Slurm NCCL checks and transient Kubernetes `MPIJob` can
  compete for GPUs/RDMA and skew, delay, or skip results.
- Migrated all bundled Soperator partition profiles (CPU / GPU / Mixed
  base partitions and the `with-debug-long`, `with-qos-preemption`, and
  `with-h100-infiniband-debug-long` overlays) from raw Slurm.conf strings
  to the chart's typed `policy` blocks under
  `partitionConfiguration.partitions[].policy`. The `with-qos-preemption`
  overlay now emits preemption controls through the chart's typed
  `schedulingConfig` instead of `customSlurmConfig`. The chart's render
  hard-fails on typed-vs-raw overlap, so this is also a strict
  correctness improvement.
- Added a `nebius-nvl-rack-v1` topology profile to both the GPU and
  Mixed Soperator profile entries. The profile sets
  `slurmConfig.topologyPlugin: topology/block` and points the operator's
  `topologyLabelPrefix` at `topology.nvidia.com` so NVL rack membership
  on GB300 clusters becomes a Slurm topology source. The existing
  `disabled` and `nebius-tiered-tree-v1` topology profiles remain unchanged.
- Documented cxcli alignment with the Soperator chart's new typed Slurm
  scheduling surfaces. Profile materialization should populate the chart's
  typed `schedulingConfig` block and per-partition `policy` block directly
  instead of concatenating `customSlurmConfig` / partition `config` strings;
  the typed-vs-raw conflict guard hard-fails the helm render on overlap. The
  free-form escape hatches remain available for Slurm.conf tokens the typed
  surface does not model.
- Added an opt-in Soperator `with-qos-preemption` partition profile for CPU,
  GPU, and mixed worker layouts. The catalog overlay writes Slurm
  `PreemptType=preempt/qos` config plus `debug`, `eval`, `train`, and `data`
  policy partitions plus standard QOS object definitions, non-zero QOS /
  fairshare priority weights, and a root account/association for smoke tests.
  cxcli now fails fast when this profile is selected without
  `qosConfiguration.enabled=true` or without QOS objects matching the partition
  `AllowQos` lists, preventing a live Slurm controller CrashLoop on missing
  SlurmDBD QOS rows.
- Updated the Soperator `qosConfiguration` hook to reconcile through the
  accounting pod instead of the controller pod, so QOS objects can be
  bootstrapped before slurmctld successfully starts with `AllowQos` partitions.
  The hook now uses the live-verified `alpine/k8s:1.33.5` image for Bash plus
  kubectl, grants pod watch for `kubectl wait`, and streams the reconcile script
  with `kubectl exec -i` instead of relying on `kubectl cp`. It now applies QOS
  preemption relationships in a second `sacctmgr` pass after all referenced QOS
  names exist. cxcli local static Helm renders now keep this explicitly opted-in
  hook manifest instead of stripping it with generic Helm lifecycle hooks.
- Fixed local Helm chart rendering to rebuild `file://` child-chart
  dependencies inside the temporary staging directory, so cxcli local-source
  Soperator renders do not use stale packaged child chart archives.
- Extended the catalog-owned NCCL `-mca coll ^hcoll` MPI overlay to the Nebius
  B300/GB300 shape alongside B200/B200A, keeping Blackwell-specific MPI policy in
  `component_cli_settings.yaml` instead of the shared `nccl-test` chart.
- Clarified the NCCL validation chart contract: `nccl-test` is a transient
  deploy-time chart source rather than a selectable `--app` / `component add`
  target, and selector guidance now comes from the catalog's
  `usage.config.ref`.
- Fixed Soperator production profile materialization so catalog-owned CPU shape
  defaults are applied to the `system`, `controller`, `login`, `accounting`,
  and CPU worker MK8s node groups before Terraform render.
- Raised the built-in Soperator production CPU role baseline to
  `cpu-d3/8vcpu-32gb` and added the catalog-owned login role taint so fresh
  production clusters have schedulable controller/login capacity while cxcli
  still derives the matching Soperator tolerations from node-group taints.
- Fixed Soperator MK8s node-group boot-disk materialization so profile-owned
  `boot_disk.type` defaults no longer erase computed `size_gibibytes` values
  before Terraform render or deploy.
- Fixed Soperator Helm value materialization so generated `null` booleans under
  the cert-manager and MariaDB webhook paths are treated as unset before render,
  preserving chart defaults while keeping explicit `false` and intentional
  `null` overrides on other Helm values intact.
- Fixed NCCL deploy validation handling for Soperator-style GPU workloads that
  claim all worker GPUs while the transient `MPIJob` is starting: cxcli now
  observes the `MPIJob` terminal condition when the launcher pod has already
  been cleaned up and records a skipped NCCL report when every Ready GPU node is
  reserved by higher-priority workload pods instead of spinning until timeout.
- Added `nebius-cxcli grafana --export-dashboard` and `--dashboard-json` to
  export dashboards from a Grafana API or normalize local dashboard JSON files,
  with opt-in `--attach` support that updates `component_sources.yaml`, creates
  JSON dashboard providers when needed, rewrites datasource refs to cxcli
  Grafana datasource UID/type values, rolls back catalog edits if validation
  fails, sorts interactive folder/dashboard selections, adds first-character
  jump keys for long Grafana lists, and documents the common export/attach
  scenarios directly in `grafana --help`.
- Cleaned up `deploy` / `terraform apply` / `terraform destroy` status output
  so transient Nebius SDK request retries no longer print tracebacks, stale
  completed MK8s operations from previous runs are omitted from the live API
  snapshot, and the Ethernet-only NCCL warning is shorter.
- Fixed `deploy <config.yaml>` multi-target selection so a plain deploy now
  reconciles every generated cluster target by default instead of failing with
  a `--target` / `--all-targets` prompt; `--target` remains available to narrow
  the run, and MK8s GPU validation guidance is printed once instead of twice.
- Fixed the interactive `component add infra:mk8s` wizard so target-scoped
  observability/GPU auto-enabled app rows are selected as exact
  `<chart>@<target>` rows, preserving existing target app rows and avoiding a
  stale prompt-index `list index out of range` crash after enabling
  observability on a newly added MK8s target. Interactive adds also stop
  repeating the redundant final `Added infra/apps components` summary after
  the wizard has already shown target-aware component selections, and
  non-interactive adds no longer print no-op `(none)` summary categories.
- Updated the Kubernetes GPU Grafana dashboard XID stat to follow NVIDIA DCGM
  semantics: `DCGM_FI_DEV_XID_ERRORS` is shown as the current XID code for the
  selected GPU scope, with zero mapped to `No XID` instead of treating the
  field as an error counter. The panel no longer falls back through GPU
  utilization, so a missing XID read point shows as no data instead of a false
  zero.
- Security: updated the locked transitive `idna` dependency to `3.15` to pick
  up the IDNA denial-of-service hardening for oversized crafted inputs.
- Fixed the MK8s GPU reservation CBG lookup to use the Capacity Block Group
  API's 200-item page-size limit, avoiding a live `INVALID_ARGUMENT` fallback
  during the wizard's `reservation.reservation_ids` prompt.
- Scoped `create` and `component add` source validation so they validate infra
  sources first and only resolve selected app chart sources plus auto-enabled
  app dependencies, including a final app-source pass for rows auto-enabled
  after the wizard before config write; clarified MK8s destroy messaging and
  replaced the raw MK8s `inputs.cluster` and `inputs.node_groups` wizard
  prompts with guided typed fields; documented the existing `create --validate-config` /
  `--no-validate-config` flag pair in the common command flag list.
- Replaced the plain MK8s create wizard's fixed `node_groups.system.*` walk
  with a concrete node-group creation loop that can add CPU or GPU groups,
  GPU reservations, GPU-cluster fabric, SFS attachments, SSH keys, and service
  account settings while keeping inactive `node_group_defaults.*` out of the
  saved MK8s-only config. The loop now uses the shared compute boot-disk policy
  for shape-specific boot-disk defaults, materializes singleton compatible OS
  choices without a redundant prompt, defaults the SSH toggle to enabled, and
  keeps `q` within the current draft node group. GPU-cluster fabric is now
  offered and accepted only after live metadata confirms the selected GPU shape
  supports clustering, and the plain MK8s wizard defaults that toggle to enabled
  for live-confirmed cluster-capable shapes. Reservation policy now defaults to
  `AUTO` when the selected live GPU shape/fabric exposes reserved capacity and
  otherwise keeps `FORBID`.
- Cleaned up wizard ordering and profile coverage: component selection now
  prints one target-aware summary after infra/app dependency resolution,
  component Terraform inputs finish before deploy-target observability/GPU
  customization prompts, MK8s hides raw `inputs.gpu_clusters`, and SFS uses a
  guided profile instead of raw `inputs.filesystems` prompts.
- Kept plain MK8s-only config output on concrete `inputs.node_groups.*`
  fields by suppressing and pruning inactive `inputs.node_group_defaults.*`
  helper values during wizard writes and runtime normalization unless a
  Soperator production profile needs them, and made optional provider-backed
  choice and scalar prompts offer an explicit skip/unset action.
- Fixed MK8s GPU and boot-disk evaluation to treat concrete
  `inputs.node_groups.*` entries as canonical, so mixed Ethernet/RDMA GPU pools
  can trigger the required app policy for the same target, CPU-only configs do
  not inherit stale GPU helper defaults, and direct MK8s boot-disk edits are not
  overwritten during refresh.
- Added ordered `status.name_inputs` watcher metadata so multi-filesystem SFS
  rows watch the configured `inputs.filesystems` resources before falling back
  to scalar `inputs.name`, avoiding stale status checks for unused default names.
- Disabled Rich auto-highlighting for deploy/destroy status blocks so Nebius
  API resource names, IDs, counts, and states stay plain text while the fixed
  TF/API labels and explicit warning/error colors remain consistent.
- Updated the Soperator production path so fresh MK8s+Soperator selections
  materialize the five-role MK8s/SFS bundle, expose worker total/shard sizing,
  and keep production-impacting child-chart gates disabled by default:
  ActiveChecks, ActiveChecks install wait, the checks controller, and Soperator
  DCGM job mapping.
- For Soperator targets, default generic MK8s GPU workload validations off so
  deploy-time CUDA/NCCL test pods do not compete with Slurm worker pods; keep
  the non-workload GPU stack readiness validation enabled. The generated
  profiles also avoid topology or node-health initial runs unless the matching
  profile enables them.
- Moved K8up under the Soperator Helm chart as an optional dependency gated by
  `values.soperator-backup-config.enabled`, removing the standalone
  `apps:k8up` selection path from cxcli.
- Aligned the Soperator render and source-validation paths with the folded
  child-chart model: `render_project()` now materializes the same Soperator
  profile defaults as CLI render, the portable Soperator catalog entry carries
  the chart version, and stale `apps:k8up` rows now fail fast with guidance to
  enable `values.soperator-backup-config.enabled` under `apps:soperator`.
- Reworked MK8s generation around typed `cluster` and `node_groups` inputs and
  aligned Soperator profile materialization with that inventory. The default
  Nebius GPU Soperator profile now produces the five logical node groups
  `system`, `controller`, `login`, `accounting`, and `worker`, while CPU/mixed
  variants remain catalog data.
- Added Soperator `values.nodeGroupMapping` materialization for existing typed
  MK8s node groups. The wizard now lists target node groups per Soperator role,
  defaults workers to GPU groups and service roles to CPU groups, and renders
  the selected mapping into chart-native filters, NodeSets, storage selectors,
  partitions, SFS attachments, and NodeConfigurator rebooter tolerations
  without creating extra role-named node groups.
- Added an explicit Soperator `install_mode` prompt. `production-cluster`
  creates the complete MK8s+SFS+Soperator five-role bundle, while
  `onboard-existing-cluster` registers an external Nebius MK8s target, records
  a read-only Soperator onboarding analysis and accepted action plan, and opens
  the role-mapping wizard for discovered node groups without Terraform-managing
  the existing cluster.
- Documented the Soperator onboarding workflow and ownership boundary:
  external MK8s clusters are made visible to cxcli for selected app/remediation
  management and future Soperator upgrades, but are not imported into Terraform,
  and `destroy` does not remove their clusters or node groups. The docs now
  also call out that remediation actions which update existing node-group
  templates, such as SFS attachment, are disruptive rolling updates that can
  evict pods and interrupt Slurm jobs.
- Fixed Soperator onboarding for live-discovered external MK8s groups so
  Soperator worker resources use Nebius resource-preset labels and GPU
  allocatable data when Terraform-style `preset` fields are not present, and
  live inventory-derived replicas, selectors, tolerations, and GPU resources
  override catalog NodeSet template defaults for generated onboarding NodeSets.
  The generated external-target Terraform skeleton is also emitted in
  `terraform fmt` style.
- Extended Soperator role mapping to chart-owned system helpers so the
  operator manager, checks controller, and MariaDB operator pods follow the
  selected `system` CPU node groups instead of landing on GPU workers.
- For Nebius GPU-image Soperator targets, cxcli now disables the Soperator
  DCGM job-mapping exporter's GPU Operator toolkit init wait because those
  nodes already include the host NVIDIA runtime stack.
- Added profile-owned Soperator onboarding service sizing so
  `onboard-existing-cluster` can reduce login pod requests for small external
  CPU pools without changing production-cluster defaults.
- Local Helm chart rendering now reuses packaged dependency archives when they
  already satisfy `Chart.lock`, avoiding unnecessary network downloads during
  `render` and `deploy`.
- Local Helm chart rendering now copies symlink targets into its staging tree
  and strips generic Helm hook-only renders from the static manifest while
  keeping explicitly annotated hooks for cxcli's ordered post-Flux apply path.
- Aligned `create` / `component add` command help and docs with Soperator's
  target-scoped node-group role mapping, and added a command-help guard against
  reintroducing old MK8s shortcut input names.
- Added the `nebius.com/node-group` Kubernetes node label to Soperator-created
  MK8s node groups so generated role filters and worker NodeSets can schedule
  on the five-role production profile without hand-authored labels.
- Preserved explicit per-node-group SFS key selections during Soperator role
  mapping so custom target-specific SFS filesystems are not mixed with default
  profile keys.
- Carried MK8s node-group taints into Soperator role filters, worker NodeSets,
  and storage selectors when role mapping is used, so tainted controller,
  accounting, and GPU worker groups can schedule their intended Soperator pods.
- Fixed Soperator onboarding and MK8s nested-schema edge cases: MIG validation
  now reads component-row `inputs`, including profile helper and node-group
  MIG fields, deploy reports flatten preferred `inputs.cluster.*` fields, MK8s
  preflight falls back to the resolved project id and checks every referenced
  GPU cluster name even before fabric is selected, Soperator onboarding no
  longer mistakes sibling `soperator-*` charts or unrelated `slurm`/`nebius`
  CRDs for installed Soperator, shellout failures block analysis instead of
  implying a vanilla cluster, partial/incompatible analyses are not persisted
  as accepted, and multi-target onboarding preserves each row's matching
  external target while rejecting multiple unbound onboarding rows. The
  bundled MK8s catalog still intentionally defaults `inputs.cluster.public_endpoint: true`;
  set it to `false` for private-only control planes.
- Preserved operator edits during repeated Soperator partition/topology profile
  materialization while still allowing profiles to replace catalog-owned base
  defaults on first materialization.
- Tightened Soperator backup and notifier runtime secret lookup for target-scoped
  rows: target-specific environment variables are required when `target_ref` is
  set, the notifier runtime now honors `NEBIUS_CXCLI_TARGET_KUBE_CONTEXT` the
  same way as the backup runtime, and `webhookSource=mysterybox` now requires
  the matching target `external-secrets` dependency.
- Aligned MK8s preflight messages, wizard examples, and focused tests with the
  typed `inputs.cluster.*` and `inputs.node_groups` contract so shortcut-era
  paths no longer appear in bundled MK8s-facing guidance.
- Added catalog-backed Soperator `values.topologyProfile` choices so topology
  stays disabled by default for generic clusters, while production tiered
  topology can be explicitly enabled with the `nebius-tiered-tree-v1` profile.
- Documented the Soperator topology policy: the five-role Nebius production
  node-group shape is role separation, while Slurm topology is an optional
  worker-locality optimization for prepared production clusters with accurate
  `topology.nebius.com/tier-*` labels.
- Fixed app chart default pruning so it no longer deletes scalar fields inside
  structured list values such as Soperator `k8sNodeFilters` and `nodesets`.
- Collapsed the Soperator upstream-family chart catalog surface to the single
  `soperator` app row. Optional notifier, active checks, jail backup, and DCGM
  job-mapping features now use nested parent chart values instead of standalone
  Soperator-family app ids.
- Removed the in-cluster `soperator-nfs-server` child chart surface from cxcli;
  production Soperator shared storage should use Nebius SFS, while the existing
  VM-backed `infra:nfs` path remains separate for explicit non-HA NFS cases.
- Gated the Soperator wizard so optional child chart details are prompted only
  after the matching nested child chart is enabled.
- Set the CPU Soperator profile ActiveChecks `srun` readiness probe to the
  rendered `cpu` partition so CPU-only installs do not wait on the upstream
  `hidden` partition.
- Added an explicit Soperator notifier webhook-source flow. Operators can
  choose deploy-time hidden input for a Slack App incoming webhook URL, or
  provide an existing Nebius MysteryBox Secret ID so cxcli auto-enables ESO,
  renders the notifier ExternalSecret, and follows the MysteryBox primary
  version without storing the webhook URL in Git.
- Fixed the Soperator notifier MysteryBox path so target-scoped source
  configs using the Soperator row `instance_id` auto-select the matching
  `external-secrets` app and persist the target MysteryBox sync defaults
  during `create` and `component add`.
- Changed `component add` infra identity to be name-driven. Interactive adds
  for scalar named infra modules now prompt for the resource name first,
  defaulting to the next unique value such as `vm-2`, then derive and persist
  `instance_id` from that normalized name. Non-interactive selectors such as
  `infra:vm@worker-vm` now seed both `instance_id: worker-vm` and the matching
  scalar resource-name input.
- Fixed `component add` live UX so interactive infra adds ask for the selected
  resource name before provider-backed Nebius scope checks, and bounded
  provider-backed Nebius SDK requests with
  `NEBIUS_CXCLI_PROVIDER_REQUEST_TIMEOUT_SECONDS` or a 15-second default.
- Fixed infra-only `component add` so it no longer resolves Helm chart
  dependencies for already-enabled app rows before adding the infra component.
- Fixed no-op duplicate `component add` selectors so skipped exact rows do not
  trigger provider-backed Nebius scope validation.
- Changed `component list/add/remove` to use explicit `--config <config.yaml>`
  targeting, with selectors first for `component add` and `component remove`.
  This prevents selectors such as `infra:vm` from being interpreted as config
  paths, and the command help/docs now include copy-paste examples.
- Aligned command help/docs around name-driven infra identity: `component add`
  presents suffixes as resource names or target ids, `component remove`
  presents removal selectors as row ids/resource names/target ids, and
  `--target` help explains that MK8s target ids are normalized cluster names
  persisted as `instance_id`.
- Fixed CI-facing command-contract regressions so incomplete interactive
  `create` reruns preserve an existing project when required resource-name
  prompts are abandoned, `component list/add/remove` emit an explicit missing
  `--config` error before treating selectors as paths, and
  `validate-dashboards --target` help names the target cluster `instance_id`.
- Clarified in docs and catalog wording that the VM-backed `nfs` component is
  a non-HA RWX bridge intended for tests, demos, short-lived environments, or
  explicit NFS compatibility cases, and that production or long-lived MK8s RWX
  storage should use direct Nebius SFS instead.
- Refactored the bundled `nfs` component contract to use the shared
  VM-module-backed path: the catalog now uses `wizard_profile: nfs`, the NFS
  Terraform module delegates Compute instance/boot/data disk ownership to
  `modules/vm`, and the old nested `data_disk` object is replaced by
  first-class `data_disk_*` inputs with no compatibility shim.
- Added guided single secondary-data-disk prompts for VM-style modules that
  expose first-class `data_disk_*` inputs. The wizard now asks enabled/type/size
  directly, uses the shared Compute disk-type choices, and only offers explicit
  data-disk encryption when the selected disk type supports it. High-performance
  data-disk sizes are aligned to the disk type's declared allocation unit.
- Added a general VM-backed NFS-to-MK8s path: enabling `infra:nfs` for an MK8s
  target now auto-enables the upstream `csi-driver-nfs` Helm app and deploy
  refreshes Flux after Terraform outputs exist so the generated StorageClass is
  sourced from the NFS VM endpoint, independent of Soperator. Multiple NFS
  exports can bind explicitly with `inputs.kubernetes_target_ref`; a single
  unscoped NFS export can serve every enabled MK8s target. Direct `config.yaml`
  edits persist the auto-enabled `csi-driver-nfs` app row during config
  normalization, and `create` / `component add` report the auto-selection when
  they add the CSI app row.
- Fixed `component add` wizard required-field discovery to use the CLI's
  mockable module-metadata binding for prompt-time and post-wizard no-write
  checks, while strict validation still reads the real runtime module contract,
  keeping tests deterministic across local and CI environments.
- `deploy` now ends with a compact `Deployment summary` footer that separates
  target-grouped validation PASS/FAIL results, copy-paste commands such as
  WireGuard `wg-quick`, SSH `ProxyJump`, and GitOps bootstrap follow-ups, and
  important generated paths limited to the generated bundle and `deploy-report.md`.
  The footer highlights section headers plus PASS/FAIL/completion status with
  terminal color and keeps machine-readable validation JSON paths inside
  `generated/reports/` instead of printing them in the footer.
- WireGuard deploy footer/report generation now omits `--component` for the
  common single-gateway case, keeps day-2 subnet add/remove examples in
  README/help instead of the generated handoff report, and shows enabled-only
  app handoff sections after `App Component Status`.
- Deploy reports now use distinct `Infra Component Status` and
  `App Component Status` headings so customer-repo Markdown linting does not
  trip MD024 duplicate-heading checks.
- Renamed the bundled WireGuard component/module contract to `wireguard-gw`
  because it is a point-to-site VPN gateway, not a jump host. The catalog,
  wizard profile, validation profile, Terraform module source path, render
  output names, help text, docs, and deploy report wording now use the gateway
  name with no legacy component-id compatibility shim.
- `create` and `render` no longer create `generated/reports/deploy-report.md`;
  the Markdown handoff report is now created/refreshed only by deployment/apply
  paths after live state can be read, while render keeps quota and runtime
  metadata in `generated/nebius-cxcli-manifest.json`.
- Moved Compute boot-disk recommendation policy into shared
  `compute.boot_disk_defaults`, and now materialize explicit recommended disk
  size/type values for MK8s, VM, SSH jump host, and WireGuard VPN gateway
  components from the selected live platform/preset.
- Simplified the WireGuard VPN gateway wizard by hiding advanced
  `endpoint_host`, first-boot `clients`, and raw `labels` prompts while keeping
  them available for direct config/module users; new WireGuard clients now pick
  up practical default DNS values from the module contract.
- Materialized `wireguard-gw` `inputs.wireguard_tunnel_cidr` in
  wizard-created configs so operators can see and edit the WireGuard server
  tunnel address and client allocation pool before render/deploy.
- Added `nebius-cxcli wireguard --gen-client-conf <config.yaml>` for deployed
  `wireguard-gw` components. The command asks the VPN gateway to allocate
  the next free WireGuard tunnel address, generate a unique client config, save
  server-side allocation metadata, and download the client `.conf` file into the
  project-local ignored `wireguard-clients/` directory.
- The WireGuard client generation command now prints the complete local
  `wg-quick up <client.conf>` and `wg-quick down <client.conf>` commands after
  writing the client config.
- The WireGuard client generation command no longer prints the internal
  `.gitignore` path after writing a client config; cxcli still keeps generated
  client config files ignored.
- The WireGuard client generation command now checks for the local `wg-quick`
  tool and prints an OS-specific install hint when it is missing.
- The WireGuard client generation command now uses short wg-quick-safe client
  names by default and rejects explicit `--client-name` values longer than the
  15-character interface-name limit.
- VM observability now uses the built-in Nebius VM Monitoring agent path only:
  cxcli materializes Compute journald labels for VM logs, does not install a
  standalone VM collector, and does not create VM collector service accounts or
  public write-endpoint configuration.
- The built-in VM journald prompt now states that answering yes applies the
  supported Nebius Compute labels to the VM; regression coverage now verifies
  both explicit systemd-unit allowlists and the default all-units label shape.
- Updated the cxcli-owned `Nebius VM Metrics` dashboard to query the
  `Nebius Services` datasource with built-in VM agent labels, and kept the
  `Nebius VM Logs` dashboard on the project Loki read path for journald logs.
- Expanded the cxcli-owned Kubernetes Grafana dashboards with production
  cluster signals: cAdvisor/API-server CPU, memory, throttling, filesystem,
  network, and API panels; Loki log-volume and warning/error panels; generic
  recent/slow/error TraceQL panels; and additional DCGM GPU health panels.
- The cxcli-owned `Nebius VM Logs` dashboard now defaults to the `sp_serial`
  Loki bucket used by Compute VM serial/journald logs, and `validate-dashboards`
  now reuses dashboard variable defaults for live Loki query checks.
- The SSH jump-host wizard now defaults `inputs.allowed_cidrs` from the
  detected operator public IPv4 address as a `/32` CIDR when that lookup is
  available, and documents that the field is the internet source allowlist for
  first-boot SSH reachability.
- Compute instance deploy status now reports private IP readiness for
  private-only VMs instead of leaving them as `network pending` after they are
  running without a public IP.
- Fixed `create` wizard `q` backtracking so revisiting a field and pressing
  `q` again goes to the previous distinct prompt instead of repeating the
  current prompt.
- Interactive `create` and `component add` now abort without writing when the
  wizard is stopped while selected components still have unresolved required
  fields, preserving existing project folders and config files.
- Aligned the root `nebius-cxcli` CI and release workflows so they prepare the
  cxcli-configured Terraform binary before running `make all`, matching the
  platform module template tests that invoke `terraform console`.
- Fixed the VM-style boot-disk wizard refresh so `inputs.boot_disk_size_gib`
  shows the shared Compute recommendation after platform/preset selection
  instead of the raw nullable Terraform default.
- Hid the low-level VM-style `inputs.boot_disk_block_size_bytes` field from the
  guided wizard while keeping it available for direct config/module users.
- Clarified guided Compute boot-disk type labels so Network SSD shows encryption
  always on, while SSD NRD and SSD IO M3 show encryption as opt-in.
- Added VM-style boot-disk deletion-protection prompts and opt-in managed
  encryption prompts for SSD NRD / SSD IO M3 disks, with strict validation and
  Terraform module wiring for VM, SSH jump host, and WireGuard VPN gateway.
- Added `nebius-cxcli wireguard --add-local-subnets <config.yaml>` and
  `--remove-local-subnets <config.yaml>` so operators can update VM-local
  default private destination CIDRs for future generated WireGuard clients.
- Aligned `nebius-cxcli --help` and `nebius-cxcli wireguard --help` with the
  WireGuard generation/add/remove mode contract and mode-specific flags.
- Added `nebius-cxcli ssh-jumphost --add-allowed-cidrs <config.yaml>`,
  `--remove-allowed-cidrs <config.yaml>`, and
  `--list-allowed-cidrs <config.yaml>` so operators can update deployed SSH
  jump-host source CIDRs through the VM-local helper instead of replacing the
  VM for day-2 firewall changes.
- Deploy reports and successful deploy terminal output now include concrete
  SSH ProxyJump commands for enabled `ssh-jumphost` + private `vm` pairs when
  Terraform outputs expose both addresses.
- Deploy reports now include a WireGuard VPN gateway handoff section with the
  deployed endpoint, tunnel CIDR, routed local subnets, default client DNS,
  client-generation command, and `wg-quick up/down` commands for existing local
  client config files.
- Documented the tightened WireGuard VPN gateway security posture in cxcli docs:
  SSH remains an admin-only key-based path with forwarding disabled, while
  ProxyJump use cases stay on the dedicated `ssh-jumphost` component.
- Deploy reports now include catalog-driven `Infra Component Reports` and
  `App Component Reports` sections so every enabled component from
  `component_sources.yaml` has a concise handoff entry without component-specific
  Python report code.
- Tightened `wireguard` and `ssh-jumphost` day-2 commands so the current
  `config.yaml` must still enable the same component row present in the
  rendered/deployed generated bundle before cxcli reads Terraform outputs or
  SSHes to the VM.
- Tightened app target handling: enabled Helm app rows now require an enabled
  MK8s target in the same project, and `create` / `component add` reject
  app-only selections before writing `config.yaml`. The docs now distinguish
  the Kubernetes `nebius-observability-agent` Helm chart from the VM Monitoring
  agent path.
- Renamed the WireGuard VPN gateway default private destination input from
  `client_default_local_subnets` to `local_subnets` without a compatibility
  shim.
- Clarified the WireGuard macOS connection docs around the Homebrew
  `wireguard-tools` CLI workflow.
- Kept the bundled source catalog free of `shared.admin_ssh.public_key` entries
  while documenting the still-supported private/customer-local bootstrap seed,
  and added regression coverage that `validate` fails when an enabled
  SSH-bearing module is missing `inputs.ssh_public_key`.
- Added guided SSH public-key selection in the create/component-add wizard:
  required `inputs.ssh_public_key` prompts now list supported `~/.ssh/*.pub`
  files, accept manual paths or inline keys, and persist normalized inline key
  content. SSH key validation now also accepts ECDSA public keys.
- Fixed live provider option lookups for VM image families and other Nebius
  list-backed wizard fields by using a Nebius-valid page size, so
  `inputs.source_image_family` can be selected from the live public image
  inventory instead of falling back to manual entry.
- Fixed live provider option lookups for `create` and `component add` so wizard
  discovery prefers operator SDK auth before Terraform runtime service-account
  env vars, avoiding stale runtime credentials causing `UNAUTHENTICATED`
  subnet/platform/image lookups.
- Removed bundled VM image-family preference lists from
  `component_cli_settings.yaml`; VM `source_image_family` selection now uses
  Nebius live public-image compatibility metadata only, ranking
  `recommended_platforms` matches ahead of other compatible image families.
- Aligned the bundled `wireguard-gw` and `ssh-jumphost` wizard profiles
  with the generic VM flow: both now source `inputs.source_image_family` from
  the live Nebius public image inventory, and the platform-infra public-access
  VM wrappers now wrap the shared `modules/vm` Terraform module for VM
  resources.
- Aligned strict validation for `wireguard-gw` and `ssh-jumphost` public
  IP allocation inputs with their Terraform module contract: cxcli now requires
  `inputs.public_ip_allocation_id` when `create_public_ip_allocation=false` and
  rejects setting an allocation ID while also creating a new allocation.
- Made the bundled `mk8s` baseline CPU node count explicit in
  `component_sources.yaml` and generated `config.yaml` files via
  `inputs.cpu_nodes_count: 2`, instead of relying on a hidden Terraform module
  default.
- Removed internal `enabled` gates from the bundled `managed-postgresql` and
  `sfs` Terraform modules so `config.yaml` plus the generated Terraform root
  remain the single source of truth for whether each component is deployed.
- Refactored the `object-storage` integration for the one-bucket-per-module
  contract, aligned prompting and strict validation with the required
  `inputs.name` field, and added catalog-driven Nebius Storage bucket status
  polling during deploy/apply.
- Added optional Soperator child chart controls for active checks, K8up-backed jail
  backups, and Soperator DCGM job-mapping telemetry. Backup bucket values bind
  to Terraform Object Storage outputs, while access keys and repository
  passwords are created or reused as deploy-time Kubernetes Secrets.
- Soperator ActiveChecks now derive `slurmClusterRefName` and
  `NUM_OF_LOGIN_NODES` from the matching Soperator app row instead of carrying
  fixed child chart defaults.
- Added Soperator notifier child-chart support under `apps:soperator` and
  deploy-time runtime-secret bootstrap. The child chart references an existing
  Slack webhook Secret, supports `existing-webhook` and advanced Slack OAuth
  `incoming-webhook` setup, rejects webhook URLs in generated values, and fails
  fast when VictoriaMetrics Operator CRDs are missing.
- Made MK8s GPU workload deploy validations aware of live GPU allocations:
  GPU Visibility and NCCL now skip with an explicit report when existing
  workloads already reserve every GPU on every Ready GPU node, and NCCL caps
  worker GPU requests to scheduler-free GPUs when only part of a node is free.
- Updated the bundled GPU Visibility CUDA sample image to NVIDIA's CUDA 12.5
  vectoradd sample tag for better fit with current Nebius GPU stacks.
- Hardened local post-Flux apply for Soperator upgrades by replacing only
  rendered PriorityClasses whose immutable numeric `value` differs from the
  live object before reapplying the normal generated manifest, and by pruning
  stale same-release NodeConfigurator CRs left behind by the Soperator
  cluster-scoped rename.
- Added `nfs` and target-scoped `soperator` catalog components. Soperator uses
  the repo-local umbrella Helm chart, keeps DCGM on the existing NVIDIA GPU
  Operator path, and orders after GPU/Network Operator releases when those GPU
  platform apps are enabled for the target. Selecting Soperator now also seeds
  the required sibling MK8s/SFS infra intent, and render binds matching NFS
  Terraform outputs into Soperator `externalNfs` values.
- Added the catalog-owned `soperator_nodesets_profile` for Soperator. Built-in
  `nebius-cpu-v1`, `nebius-gpu-v1`, and `nebius-mixed-v1` profiles seed generic
  MK8s node groups, SFS filesystems, and matching chart values. The mixed
  profile creates separate `worker-cpu` and `worker-gpu` Slurm NodeSets plus
  CPU/GPU partitions, while NFS remains an optional VM-based sibling infra
  component.
- Added the Soperator `values.partitionProfile` wizard option. `shape-default`
  keeps the selected worker-shape partitions, while `with-debug-long` overlays
  `debug` and `long` policy partitions into the rendered `SlurmCluster`; Slurm
  hardware features remain NodeSet `nodeConfig.features`.
- Soperator wizard profile choices now resolve from the Soperator profile
  catalog instead of a duplicate static list, with labels for CPU-only,
  GPU-only, and mixed worker scenarios. The mixed profile also exposes
  `with-h100-infiniband-debug-long`, which adds `h100` / `infiniband`
  partitions and matching worker-gpu `nodeConfig.features`.
- Rendered Soperator deployments now default to structured Slurm partitions,
  chart-managed MariaDB accounting, and Slurm REST so worker NodeSets register
  cleanly through the Soperator SConfig reconciliation path.
- The Soperator chart values mounted generated Slurm scripts into workers and
  set the pinned-image Slurm plugin directory so live `srun` smoke tests can
  load SPANK plugins and run prolog/epilog scripts.
- Soperator GPU NodeSets now render Slurm `Gres=gpu:<count>` from
  `slurmd.resources.gpu`, keeping cxcli profile values free of duplicated GPU
  counts while allowing `srun --gres=gpu:*` jobs on GPU partitions.
- The bundled Soperator GPU profile now sets
  `NVIDIA_DRIVER_CAPABILITIES=compute,graphics,utility,video` on GPU worker
  NodeSets, matching the chart default while keeping the value overrideable in
  app values.
- Suppressed retryable Nebius SDK token-refresh `DEADLINE_EXCEEDED` tracebacks
  during runtime-auth readiness checks and Terraform state-bucket bootstrap,
  while preserving cxcli's normal retry/error handling.
- Clarified and covered the local MK8s handoff behavior that non-CI
  `deploy`, `flux apply`, and `flux bootstrap` create `~/.kube/config` when it
  does not already exist before merging the generated Nebius exec context.
- Clarified MK8s boot-disk type labels in the guided wizard so
  `NETWORK_SSD` is described as erasure-coded with two-hardware-failure
  tolerance, while `NETWORK_SSD_IO_M3` is explicitly described as replicated
  with three-drive mirroring.
- Changed constrained TTY wizard prompts to render selectable values without a
  `<manual input>` row, and routed the MysteryBox ESO version policy plus
  payload type prompts through the same selector instead of typed bracket
  prompts. The non-TTY fallback now accepts only a listed index or exact choice
  value for constrained fields.
- Kept CPU-only MK8s configs clean by no longer seeding
  `inputs.gpu_stack_source` from the source catalog and by pruning stale
  GPU-only inputs such as `inputs.gpu_nodes_boot_disk_type` whenever
  `inputs.gpu_enabled` is not true. The active GPU default remains
  settings-owned as `components.infra.mk8s.cli.gpu.default_stack_source`.
- Fixed the MysteryBox guided wizard so pressing Enter at the Kubernetes Secret
  name prompt accepts a Kubernetes-safe derived default such as `db-credentials`
  for a MysteryBox Secret named `db_credentials`.
- Improved `create --validate-sources` failure UX for existing projects: full
  source validation now runs before overwrite confirmation, and source
  validation failures include retry, `NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS`, and
  `--no-validate-sources` guidance for transient Helm/network timeouts.
- Reorganized the README opening sections so core render/deploy concepts live in
  a dedicated `Core Concepts` section and `Features` is a concise capability
  summary instead of a long command-contract reference.
- Clarified the post-`deploy`/`flux apply` GitOps bootstrap warning so the
  printed `flux bootstrap <generated-dir>` command explains that the path is the
  local generated bundle and the GitHub repository is inferred from
  `GITHUB_REPOSITORY` or the local git `origin`.
- Added HA-oriented bundled Helm defaults for platform charts with documented
  safe replica knobs. Grafana's Envoy data plane, Envoy Gateway, cert-manager
  controller/webhook/cainjector, and External Secrets
  controller/webhook/cert-controller now default to two replicas, with External
  Secrets leader election enabled. Grafana itself stays at one replica unless
  the chart values configure a shared MySQL or Postgres database, and runtime
  validation now rejects unsafe multi-replica Grafana values on the bundled
  SQLite/emptyDir path.
- Added `periodicUpdateInterval: 0` to the cxcli-owned Network Operator
  `NicClusterPolicy` RDMA shared-device patch so static KVM passthrough GPU
  nodes avoid noisy periodic full PCI rescans while keeping startup discovery
  and `rdma/shared_device` advertisement intact.
- Replaced the imported Nebius GPU Grafana.com dashboard with a cxcli-owned
  Kubernetes GPU dashboard that reads DCGM metrics from `Nebius Services`,
  filters by `mk8s_cluster_id`, and uses `query_result(...)` variables so stale
  project-wide label metadata cannot list deleted GPU nodes. The bundled
  Kubernetes metrics dashboard now focuses on current CPU, memory, pod,
  container, and network metrics from `Nebius User Metrics`, while the catalog
  keeps only `nebius-disk` as a service-dashboard import example.
- Extended `validate-dashboards` Prometheus scoping so target cluster IDs narrow
  both `k8s.cluster.id` user-metrics selectors and `mk8s_cluster_id` Nebius
  service-metrics selectors.
- Fixed `validate-dashboards` kube-context resolution so a current local
  kubeconfig context such as `nebius-cluster1-mk8scluster-...-external` is used
  for its matching target even when older contexts for the same target also
  exist in kubeconfig.
- Changed the cxcli-owned Kubernetes GPU Grafana dashboard time-series legends
  to start with GPU UUID before `instance_id`, so per-GPU series are easier to
  distinguish while node context remains visible.
- Made cxcli-owned Nebius Observability Agent scrape jobs render-only. Source
  `config.yaml` now keeps the target observability intent and any custom
  operator scrape jobs, while generated Flux HelmReleases still receive the
  managed API server, kubelet, cAdvisor, and Hubble `additionalTargets`.
- Added bundled Grafana dashboard links to `deploy-report.md` for every active
  catalog dashboard whose JSON is shipped under
  `src/nebius_cxcli/grafana_dashboards`, while leaving operator-owned external
  dashboard JSON imported into Grafana but out of the report shortcut list.
- Removed the separate dashboard-index, Metrics, Logs, and Traces shortcut rows
  from the Grafana section of `deploy-report.md`; the bundled dashboard list is
  now the single dashboard handoff surface.
- Fixed ESO MysteryBox IAM bootstrap during `flux apply` after Terraform state
  handoff. The service-account and authorized-key step now ignores Terraform
  runtime service-account env vars and allows the operator Nebius CLI token
  fallback, so local federation profiles do not accidentally use the Terraform
  automation identity to manage `mysterybox-sa`.
- Suppressed expected Nebius API root HTTP status lines from the ESO MysteryBox
  TLS validation output while still requiring an HTTP response internally, so
  successful checks no longer show confusing `404` lines.
- Changed generated ESO MysteryBox sync to one key-mapped `ExternalSecret`
  per declared MysteryBox Secret, defaulting `refreshInterval` to `15m` and
  omitting `remoteRef.version` unless `inputs.secrets[].eso_version_policy` is
  explicitly `manual-version-pinning`. The default
  `auto-primary-version-pinning` mode now lets ESO/MysteryBox resolve the
  current primary version automatically.
- Added MysteryBox Kubernetes sync settings to the generated deploy report so
  target namespaces, store name, and custom refresh intervals such as `1m`
  remain visible before Terraform-created MysteryBox IDs are available for
  generated `ExternalSecret` resources.
- Added cxcli preflight validation for first-deploy MysteryBox payload values.
  Interactive local deploy/plan/apply runs now prompt with hidden input for
  missing runtime-only values before Terraform starts, while non-interactive
  runs report the exact missing `TF_VAR_*_payload_values` Secret/key names
  before Terraform apply reaches the module precondition.
- Moved the interactive MysteryBox payload-value prompts before the Rich
  preflight progress bar starts, so hidden-input prompts remain visible instead
  of appearing to hang inside the progress spinner.
- Made deploy persist first-deploy MysteryBox `version_id` values into both
  `config.yaml` and the generated manifest/tfvars, including after Terraform
  exits because the Nebius provider lost an already-accepted operation poll.
  Retried deploys can now continue from the refreshed generated bundle without
  asking again for runtime-only payload values.
- Added explanatory labels to the MK8s `gpu_stack_source` wizard choices so
  `nebius_image` is shown as the Nebius GPU image with host NVIDIA
  driver/toolkit already present, while `operator_managed` is shown as the path
  where GPU Operator installs and manages those host components.
- Fixed `q` handling inside the guided MysteryBox `inputs.secrets` wizard loop
  so it backs up to the previous Secret/policy/key/type prompt before returning
  to the outer component field wizard.
- Aligned built-in wizard-profile documentation and regression coverage with
  the current profile registry, including the `mysterybox` profile and static
  `wizard.<field>.sources` choice labels.
- Split the `create` command's final next-step commands onto separate lines and
  moved optional `bootstrap-ci` after the normal validate/render/deploy path.
- Stopped rendering cxcli-managed `Namespace` extraObjects for built-in
  Kubernetes namespaces such as `default` in the MysteryBox ESO sync path while
  still allowing `ExternalSecret` resources to target those namespaces.
- Kept cxcli-managed MysteryBox ESO `Namespace`, `ClusterSecretStore`, and
  `ExternalSecret` objects out of source `config.yaml`; they now render into a
  generated post-Flux manifest that local deploy/Flux apply applies after the
  external-secrets HelmRelease is Ready, and normalization strips stale managed
  ESO objects while preserving operator-authored chart objects.
- Expanded the generated `deploy-report.md` component summary so selected
  MysteryBox, External Secrets Operator, NVIDIA GPU Operator, and NVIDIA Network
  Operator components are visible in the human handoff artifact.
- Made the generated `deploy-report.md` infra component summary catalog-driven,
  so every Terraform component declared in `component_sources.yaml` appears as
  enabled or disabled, including `vm`. MK8s cluster rows now report CPU and GPU
  nodes with the same total-node wording, and validation sections expand JSON
  `checks[]` arrays into numbered Markdown check lists instead of only showing
  `N/N check(s) passed` prose.
- Replaced the custom MysteryBox ESO webhook path with External Secrets
  Operator's native `nebiusmysterybox` provider.
  `deploy.targets[].secrets.mysterybox.*` now auto-enables `external-secrets`
  for the target, renders
  `ClusterSecretStore`/`ExternalSecret` objects into a post-Flux manifest,
  requires `mbsec-...` MysteryBox secret IDs, validates optional
  `mbsecver-...` version IDs, creates a dedicated `mysterybox-sa` Nebius
  service account with only `mysterybox.payload-viewer`, creates the runtime
  Subject Credentials Secret used by ESO to exchange for Nebius IAM tokens,
  disables Nebius CLI token fallback for that service-account bootstrap path,
  removes the old shared runtime-auth branch for ESO MysteryBox, and keeps the
  Nebius credential Secret runtime-only for `deploy`/Flux commands.
  The cxcli config contract is snake_case only; Kubernetes camelCase is emitted
  only in rendered ESO manifests.
- Bumped the bundled `external-secrets` chart source from `2.0.1` to `2.4.1`
  for the native MysteryBox provider path.
- Added explicit coverage and documentation for ESO MysteryBox sync version
  handling. `version_id` remains the current primary MysteryBox version
  metadata, while generated ExternalSecrets render `remoteRef.version` only
  when `eso_version_policy` is `manual-version-pinning`.
- Added optional `inputs.secrets[].kubernetes_secret_name` metadata for
  MysteryBox ESO sync. The guided MysteryBox wizard now asks for the target
  Kubernetes Secret name with the MysteryBox Secret name as the default, cxcli
  render uses that value for generated `ExternalSecret.spec.target.name`, and
  Terraform rendering strips the cxcli-only metadata before calling the
  MysteryBox module.
- Clarified and regression-guarded the native MysteryBox ESO trust path:
  cxcli renders `api.nebius.cloud:443` without `caProvider`, stores ESO
  Subject Credentials only as a runtime Kubernetes Secret, documents the
  in-cluster TLS/egress validation command, and now runs that configured
  endpoint probe before local deploy/Flux apply paths use the ESO store.
- Enabled the bundled `external-secrets` app by default when the Terraform
  `mysterybox` component and an MK8s target are selected together. In that same
  selected-backend wizard context, native MysteryBox-to-Kubernetes sync now
  defaults to `deploy.targets[].secrets.mysterybox.enabled=true` and persists the
  accepted default instead of treating it as a virtual prompt value. Configured
  native sync targets now also get a required `mysterybox_eso_connectivity`
  deploy validation in
  `deploy-report.md`; it checks in-cluster API TLS, `ClusterSecretStore`
  readiness, configured `ExternalSecret` readiness, and ESO controller
  TLS/auth/permission log errors since the current validation started, and it
  is not skipped by optional validation skip flags. cxcli applies managed ESO
  `ClusterSecretStore` and `ExternalSecret` resources only after the ESO
  HelmRelease is Ready so CRDs are discoverable before those CRs are submitted.
- Moved the MysteryBox `external-secrets` auto-selection into the early
  `create` / `component add` dependency flow, so the resolved component summary
  and field wizard show the required ESO controller app alongside other
  dependency-driven app selections.
- Added a required `sync_namespaces` list for native MysteryBox ESO sync. The
  default store access mode remains `allow_all_namespaces: true`, which omits
  `ClusterSecretStore.conditions`; set `allow_all_namespaces: false` to render
  `conditions.namespaces` from the same `sync_namespaces` list.
- Removed the local ESO MysteryBox auth cache model. The Kubernetes Subject
  Credentials Secret is now the persisted ESO auth source of truth; deploy/Flux
  commands reuse a valid Secret and create a fresh Nebius authorized key only
  when the Secret is missing, invalid, or stale.
- Switched runtime-auth metadata writes to atomic same-directory replacement so
  Terraform runtime auth cache updates do not leave a partially written
  `runtime-auth.json`.
- Changed `prefer_operator_auth=True` Nebius SDK auth ordering so CLI token auth
  is tried after SDK config and before service-account credentials.
- Made app `release.install_after` prerequisites participate in component
  auto-selection before Flux `dependsOn` ordering is rendered.
- Aligned bundled MysteryBox runtime validation with the Terraform module's
  initial-primary-version contract. `inputs.secrets` is now a required list of
  secret objects keyed by each secret `name`; each secret carries one non-empty
  `payload` mapping with `text`/`file` payload entries and an optional
  `version_id` metadata field for the current primary MysteryBox version. Use
  `version_id: n/a` before first deploy; cxcli now writes created
  `mbsecver-...` primary version IDs back to `config.yaml` after Terraform
  apply. Old mapping, singular `version`, and multi-version `versions` shapes
  are rejected instead of translated.
- Rendered MysteryBox Terraform roots now expose `payload_values` as a
  sensitive runtime root variable, pass it into the child module, omit it from
  generated tfvars/manifests, require the runtime `TF_VAR_*` value to use the
  two-level `{secret_name={payload_key=value}}` shape, and reject
  `inputs.payload_values` in `config.yaml` so payload cleartext stays out of
  source and generated artifacts. After cxcli records the created `version_id`,
  later plan/apply/destroy runs no longer require the original payload values.
- Changed destroy status polling for live API misses to report watched
  resources as already absent instead of "not visible yet", while still leaving
  Terraform state/provider reconciliation as the authority for actual deletes.
- Improved create wizard selection visibility: interactive component selectors
  now print the resolved infra/apps choices once after dependency resolution,
  while field prompts show only a one-line Rich-colored
  `Wizard context: Current: <scope> / <component-or-target-feature>` marker
  instead of repeating the full component list before every input. Deploy-target
  fields such as native MysteryBox ESO sync are labeled as deploy-target
  context, not as MK8s Terraform inputs.
- Hid the MK8s native MysteryBox ESO sync wizard prompts unless the Terraform
  `mysterybox` component is also selected and enabled, aligning that
  dependency-backed prompt with the rest of the wizard gating model.
- Stopped redacting boolean wizard selections just because their field path
  contains `secret`, so toggles such as
  `deploy.targets[].secrets.mysterybox.enabled` are echoed as `true`/`false`
  while actual sensitive string values remain redacted.
- Added MysteryBox `inputs.secrets` wizard guidance that explicitly tells
  operators to enter only metadata plus payload key/type schema during `create`
  and to provide actual payload values later through runtime
  `TF_VAR_*_payload_values` input.
- Replaced the raw YAML/JSON prompt for MysteryBox `inputs.secrets` with a
  concise guided loop for Secret names plus payload keys/types, while keeping the
  same Terraform-native list/map contract and runtime-only payload values.
- Clarified the guided MysteryBox prompt so the first Secret name is shown as
  required; blank only finishes the loop after at least one Secret has been
  added.
- Clarified the guided MysteryBox payload-key prompt so the first key in each
  Secret is shown as required; blank only finishes a Secret after at least one
  key has been added.
- Normalized guided MysteryBox payload keys to uppercase and echoed the stored
  key after entry, so `username` is persisted as `USERNAME`.
- Made MK8s `inputs.gpu_stack_source` a guided wizard choice between
  `nebius_image` and `operator_managed` instead of a free string prompt that
  only displayed the default value.
- Simplified native MysteryBox ESO source config to one generated sync
  model: cxcli now derives one `ExternalSecret` per declared MysteryBox Secret
  per `sync_namespaces` entry, rejects source-authored `external_secrets` and
  old `allowed_namespaces`, resolves MysteryBox IDs from Terraform `secret_ids`
  output after apply, and refreshes Flux manifests before applying ESO
  resources.
- Persisted the native MysteryBox ESO `allow_all_namespaces: true` wizard default
  alongside the sync toggle, `refresh_interval: 15m`, and
  `sync_namespaces: [default]`, so accepted create defaults show both the
  cluster-wide store policy and sync target explicitly in `config.yaml`.
- Changed interactive `list(string)` wizard prompts, including MysteryBox
  `sync_namespaces`, to accept comma-separated input such as `ns1,ns2`
  instead of requiring a YAML/JSON list literal.
- Improved interactive wizard navigation in TTY list and checkbox prompts.
  Back/Quit are no longer rendered as selectable rows, so component
  multi-select prompts show checkboxes only for real components; `q` backs up
  and `qq` quits directly from the prompt.
- Rejected nested cxcli-managed deployments roots. `create`, `render`, and
  `bootstrap-ci` now fail fast when the requested or inferred deployments root
  sits below an ancestor that already owns the cxcli managed `.gitignore` block,
  keeping one root-level ignore contract for all tenant/project folders and no
  nested-root compatibility path.
- Kept MK8s NCCL RDMA validation on the DMA-BUF GPUDirect path by making the
  RDMA-only MPI environment export `NCCL_DMABUF_ENABLE=1` settings-owned in
  `component_cli_settings.yaml`. The value is appended to any platform-specific
  NCCL MPI overlay, such as the B200 `-mca coll ^hcoll` rule, instead of
  replacing it. NCCL validation detail JSON and `deploy-report.md` now surface
  the rendered `NCCL_DMABUF_ENABLE` value, its source, and the derived
  GPUDirect mode beside the bandwidth result. Removed the stale unused NCCL
  context selector helper so the per-target render path is the only NCCL
  context path, and aligned the first-party NCCL chart/runtime README wording
  with that cxcli-owned RDMA overlay.
- Hardened `validate-dashboards` for multi-target MK8s configs. Target-scoped
  Grafana validation now requires an explicit kube context for each Grafana
  target, only uses name-based local kubeconfig lookup when it is unambiguous,
  and passes that context to Grafana-runtime `kubectl` calls, so the command no
  longer falls back to the ambient `kubectl` current-context when multiple
  clusters exist.
- Split cxcli-owned settings out of `component_sources.yaml` into the paired
  `component_cli_settings.yaml` file. `component_sources.yaml` now owns reusable
  infra/app source metadata, while `component_cli_settings.yaml` owns managed
  tool versions, observability endpoints, Grafana datasource and dashboard signal bindings,
  MK8s GPU policy, boot-disk policy, and observability guardrails linked by the
  same component ids. The loader rejects top-level `cli`, top-level
  `observability`, and component-local `cli` fields in `component_sources.yaml`;
  build/release verification now requires both files in wheel bundles.
- Aligned bundled-catalog diagnostics and release-helper help with the split
  catalog contract so missing packaged `component_cli_settings.yaml` errors and
  `verify-wheel` help both name the paired settings file explicitly.
- Hardened the MK8s GPU Visibility deploy-time validation against transient
  Kubernetes API slowness. A single `kubectl get pod` timeout while polling
  validation pods is now retried within the configured validation timeout
  instead of failing an otherwise healthy new cluster immediately.
- Added a default-enabled deploy-time MK8s Observability Agent ingestion guardrail.
  `render` now writes `mk8s_observability_ingestion` validations into the
  generated manifest for observability-enabled targets, and `deploy` verifies
  the live agent HelmRelease, rendered signal config, DaemonSet readiness, and
  trace OTLP service endpoints before rolling the result into
  `generated/reports/deploy-report.md`. The settings catalog now exposes only
  `components.infra.mk8s.cli.observability.primary_agent.validation` as a
  boolean enabled/disabled switch that defaults to enabled; the Nebius-agent
  object names, signal value paths, selectors, trace service binding, and
  bounded check limits are internal cxcli defaults. The pass path uses direct
  or limited Kubernetes API reads instead of listing all agent pods/endpoints
  on large clusters.
- Changed the Nebius-image GPU Operator defaults so non-GPU-cluster targets run
  the GPU Operator NFD worker on Nebius GPU nodes, letting NFD own
  `nvidia.com/gpu.present=true` and GPU Operator create DCGM Exporter endpoints
  for Grafana metrics dashboards. GPU-cluster / InfiniBand targets still keep
  Network Operator as the single NFD owner, and cxcli now explicitly enables
  Network Operator NFD/NodeFeatureRules for those targets because the chart
  defaults them off. The catalog-owned DCGM node-label policy remains scoped to
  the Nebius-specific GPU Operator operand labels. Clarified that operator-managed
  targets keep GPU Operator's driver/toolkit lifecycle enabled and do not pre-seed
  manual `nvidia.com/gpu.deploy.*` operand labels.
- Removed a duplicated Network Operator release value from the bundled RDMA
  shared-device post-render patch. The patch now uses `{chart_version}`, which
  cxcli resolves from the chart's `source.portable.version`, so the plugin
  image tag stays aligned with the Network Operator chart version.
- Removed redundant Grafana image registry/repository/tag overrides from the
  bundled catalog. The pinned Grafana chart version now owns the chart
  `appVersion` and default image tag instead of repeating that derived value in
  `component_sources.yaml`.
- Replaced the bundled Metrics, Logs, and Traces dashboard shortcuts with
  cxcli-owned Grafana dashboard JSON that matches Nebius Observability read
  labels: the metrics cluster selector uses `up` with `k8s.cluster.id`,
  cAdvisor/container panels use `kubernetes_io_hostname`, and DCGM exporter
  reachability uses `node`; logs query the `default` Loki bucket with `k8s_cluster_id`,
  `k8s_namespace_name`, and `k8s_pod_name`; and traces now use a generic Nebius
  Tempo dashboard instead of the workload-specific Guardrails starter
  dashboard. Bundled dashboard JSON moved to package `json_file` assets so
  `component_sources.yaml` keeps only stable dashboard source bindings, while
  `component_cli_settings.yaml` keeps datasource and dashboard signal bindings plus custom
  active component-sources files can reference operator-owned dashboard
  JSON with relative or absolute `json_file` paths.
- Changed Grafana render output so project `config.yaml` no longer carries
  cxcli-owned dashboard JSON blobs. `render` now writes readable dashboard JSON
  copies under `generated/grafana_dashboards/<target-id>/<folder>/`, renders a
  dashboard ConfigMap into the generated Flux target, and points the generated
  Grafana HelmRelease at that ConfigMap with `dashboardsConfigMaps`. Generated
  Grafana report links now pass `var-Cluster=<cluster-id>` when the target MK8s
  handoff exposes a cluster ID, so target Metrics and Logs links open the
  bundled Kubernetes dashboards with the matching cluster selected. The bundled
  catalog keeps Grafana.com service imports under the `nebius` provider and
  cxcli-owned Kubernetes JSON dashboards under `nebius-kubernetes`, avoiding the
  Grafana Helm chart's invalid same-provider mix of `values.dashboards` and
  `dashboardsConfigMaps`.
- Added `validate-dashboards <config.yaml>` to validate enabled bundled Grafana
  dashboard sources against the live Grafana datasources/read endpoints.
  Report dashboards remain the Metrics/Logs/Traces link subset. The command
  checks the concrete read endpoint -> datasource -> dashboard JSON chain for
  Prometheus metric names/labels/queries, Loki labels/queries, and Tempo TraceQL
  reachability without dynamically generating or rewriting dashboard JSON, and
  uses a timed dashboard-level spinner while querying live Grafana. The spinner
  total is every target-bound Grafana.com and cxcli-owned dashboard binding, and
  the active item is labeled as `<target-id>: <folder>/<dashboard>`. Output now
  separates dashboard source provenance, validation checks, grouped warnings,
  and errors so informational Grafana.com-import provenance is not shown as a
  warning. It supports `--target <target-id>` for target-scoped Grafana rows
  in multi-target configs, resolves the target MK8s cluster ID from generated
  Grafana status, generated reports, or the persisted kube context, and
  scopes Metrics/Logs dashboard checks to that cluster so another
  cluster's data cannot mask a broken target dashboard.
- Simplified bundled Grafana catalog metadata by removing the redundant nested
  `components.apps.grafana.cli.grafana` namespace. Grafana app settings now live
  directly under `components.apps.grafana.cli`, for example `cli.datasources`
  and `cli.dashboard_signals`.
- Aligned the bundled MK8s observability defaults with the Nebius
  Observability Agent service-discovery contract: Kubernetes metrics now
  exclude ordinary `kube-system` service/pod annotation scrapes by default,
  the DCGM exporter target uses `prometheus.io/scrape=true` annotation
  discovery instead of a duplicate `additionalTargets` scrape job, and
  materialization removes stale catalog-owned scrape jobs when discovery moves
  to annotations.
- Rendered cxcli-owned safe kubelet, cAdvisor, API server, and Hubble scrape
  jobs for `collect_k8s_cluster_metrics=true` instead of using the Nebius
  chart's broad built-in cluster-metrics jobs, avoiding NFD/high-volume node
  labels on container metrics while preserving user-defined `additionalTargets`.
- Added a deploy completion footer that prints the complete
  `generated/reports/deploy-report.md` path after a successful local
  `deploy`.
- Split generated deploy reports into a `Client` section and an `Infra`
  section. MK8s rows and Grafana target metadata now include the Nebius cluster
  ID and derived kube context when Terraform state or live Grafana status has
  that target metadata, so the Grafana admin-password command is copy-pasteable
  with `kubectl --context=...` for each cluster.
- Reorganized `generated/reports/deploy-report.md` into smaller subsections:
  `Infra Component Status` and MK8s cluster details are separated, app handoff
  details are grouped by platform/observability/workloads, and Grafana links plus
  credentials are grouped under one subsection per target with shared notes
  separated from target-specific links.
- Scoped deploy validation summaries and deploy-report validation sections to
  the selected target for `deploy --target <target-id>`, so multi-target runs
  no longer show unrelated target validations as `NOT RUN` when they were
  intentionally outside the run. `--all-targets` still reports every selected
  target.
- Removed a CodeQL `py/incomplete-url-substring-sanitization` warning from the
  deploy-report tests by checking rendered Observability endpoint lines exactly
  instead of searching for URL substrings.
- Improved multi-target Grafana reporting: deploy reports now list each
  configured Grafana target with pending links until `deploy` or `flux apply`
  captures the target Gateway/LoadBalancer status, wait briefly for newly
  created Gateway/LoadBalancer addresses, import datasource-matched
  Grafana dashboards for Metrics, Logs, and Traces from the source/settings
  catalog pair,
  point report links at each catalog-bound dashboard when Grafana has imported
  it, write short public Grafana `/goto/...` links by setting the live Grafana
  root URL to the discovered public address before using Grafana's short URL
  API over the selected dashboard or current Explore `panes` URL schema,
  move Grafana datasource names, UIDs, types, default marker, and read endpoint
  bindings into `component_cli_settings.yaml`, keep the service-provider
  Prometheus datasource as `Nebius Services`, keep the separate
  `Nebius User Metrics` datasource for user-ingested Kubernetes metrics,
  move the Grafana admin Secret contract, read-token Secret contract, org ID,
  and fallback Explore queries into `component_cli_settings.yaml`,
  make Observability read/write endpoint records catalog-defined under the
  settings `observability.endpoints` section with settings-owned labels, templates,
  inclusion conditions, and bucket expansion,
  move service-provider metric bucket and service log bucket selection for
  VM, MK8s, Object Storage, shared storage, and Managed PostgreSQL into
  `component_cli_settings.yaml`,
  validate Grafana datasource `read_endpoint` bindings against those catalog
  endpoint keys, refresh a runtime Grafana read-token Secret when a
  catalog-bound Prometheus read endpoint clearly rejects the existing token,
  validate every Grafana dashboard source as a declared dashboard with
  datasource metadata plus either `gnetId` with pinned `revision` and imported
  `uid` or dashboard JSON with a top-level `uid`, validate that dashboard signal
  bindings are single `<folder>/<dashboard>` references to declared
  dashboard sources, include
  target-specific `kubectl --context=...` password commands, and avoid
  collapsing target-scoped Grafana installs into one generic fallback sentence.
- Removed the raw read-endpoint API probe URL section from the generated deploy
  report. The report still shows public read endpoint bases and bundled Grafana
  links, but omits diagnostic Prometheus/Loki/Tempo probe URLs to keep the
  customer handoff lighter.
- Documented the bundled Grafana Prometheus datasource split: `Nebius Services`
  reads Nebius/provider metrics from `/service-provider/prometheus`, while
  `Nebius User Metrics` reads customer/user-ingested metrics from `/prometheus`.
- Added settings-owned Grafana datasource descriptions to the generated
  `deploy-report.md` so the Grafana section explains the difference between the
  `Nebius Services` and `Nebius User Metrics` Prometheus datasources.
- Clarified the quota workflow across `create`, `quota-check`, and
  `quota-request`: create-time quota/capacity assessment is warning-only and
  does not reserve capacity, `quota-check` reruns against current live Nebius
  state, and `quota-request` is a no-op unless the current assessment confirms a
  requestable shortage. Capacity Dashboard-only GPU shortages now point
  operators toward another platform/preset/fabric or region instead of a quota
  request that cannot be derived.
- Made explicit `quota-check` and `quota-request` better aligned with day-2
  MK8s config edits by best-effort discounting capacity already managed in the
  sibling generated Terraform state. Scaling a configured node count from 4 to 6
  now plans against the net-new shortfall when state is available instead of
  treating the full desired count as additional quota.
- Aligned `validate` with that same state-aware day-2 MK8s quota path. Source
  config validation now passes the resolved project paths into quota assessment,
  so unchanged existing clusters with readable sibling generated Terraform state
  are not charged again as fresh capacity requests.
- Aligned `render` with the same state-aware day-2 MK8s quota path, so rerenders
  of unchanged existing clusters no longer warn as if the full configured node
  count were a new quota request when the current generated Terraform state is
  readable.
- Improved the interactive component field wizard. It now prints visible Infra
  and Apps section separators, echoes each answered field as a persistent
  terminal `Selected <path> = <value>` line with secret-like paths redacted, and
  keeps the VM preemptible flow aligned with Nebius Compute requirements by
  showing preemptible follow-ups only for GPU platforms, materializing
  `recovery_policy=FAIL` when `preemptible_enabled=true`, and omitting the
  deprecated preemptible priority field.
- Simplified optional wizard navigation for `create` and `component add`: `q`
  now backs up through component selection, component phase prompts, and field
  prompts so operators can revise earlier answers, while `qq` stops the wizard
  immediately and preserves the current config payload.
- Added fail-fast Git tooling checks for Git tree Helm chart sources, including
  `create --validate-sources` preflight coverage before identity prompts.
- Moved target-scoped deploy settings to a single `deploy.targets[]` contract
  keyed by `instance_id`. MK8s Kubernetes observability now lives under
  `deploy.targets[].observability.*`, MK8s GPU validation settings remain under
  `deploy.targets[].validations.mk8s_gpu.*`, and root `deploy.observability.*`
  is kept for VM observability settings that are not Kubernetes target installs.
- Simplified target-bound app chart config by removing
  `apps.charts[].target_ref` from user-authored `config.yaml`. App rows now
  bind to built-in cluster targets with `apps.charts[].instance_id`, using the
  same target id as infra rows, `deploy.targets[]`, and `--target`; cxcli still
  derives internal `target_ref` metadata for generated Flux directories and
  deploy status. Generated manifests now fail fast unless each
  `deploy.targets[].target_ref` is present and equals that target row's
  `instance_id`, so there is no old manifest fallback to chart ids or component
  ids.
- Aligned the README and design-doc command references with the current CLI help
  surface: Quick Start now names `create <deployments-root>`, supporting-command
  maps include `quota-request <config.yaml>`, and the common flag summary lists
  deploy/Flux multi-target plus deploy validation-skip flags.
- Clarified the generated Terraform inputs handoff. The README, design doc, and
  managed deployments `.gitignore` wording now state that
  `generated/infra/terraform.auto.tfvars.json` is an ignored duplicate recreated
  from `generated/nebius-cxcli-manifest.json` by `nebius-cxcli` generated-bundle
  commands before Terraform runs, and that `config.yaml` changes reach Terraform
  only after `render` refreshes the generated manifest. Fresh checkouts should
  use the cxcli wrapper commands rather than raw `terraform apply`.
- Clarified and normalized multi-target component identity: new MK8s rows created
  by the wizard use `inputs.cluster.cluster_name` as the cluster target
  `instance_id` when the row still has a generated placeholder id, while
  target-bound app rows use that same target id as their `instance_id`, so
  generated identities read clearly as `nvidia-gpu-operator@cluster2`.
- Removed the old compatibility path for implicit or chart-named app instances:
  config validation now requires explicit `instance_id` on every infra/app row,
  rejects `apps.charts[].target_ref`, rejects target-bound app rows whose
  `instance_id` does not reference an enabled cluster target, and rejects root
  Kubernetes deploy settings instead of pruning or migrating them.
- Tightened `component add` idempotence. Non-interactive mode skips
  already-enabled exact selectors, including duplicate `<chart-id>@<target-id>`
  target-bound app adds; adding another infra instance interactively can now
  reuse the bare selector, while non-interactive duplicate infra/app-only rows
  still require an explicit named `<component-id>@<resource-name>` selector.
- Tightened day-2 component target-binding edits. `component add` now
  target-binds existing app-only chart rows when the first built-in cluster
  target is added and the mapping is unambiguous, and `component remove` now
  cascades cluster-target removal to app chart rows and `deploy.targets[]`
  settings bound to that removed target.
- Clarified the `component add` / `component remove` selector contract in
  CLI help, README, and design docs. The positional argument is now described
  as a component selector, matching the supported `infra:<id>`, `apps:<id>`,
  `all`, `none`, bare row id for remove, and
  `<component-id>@<resource-name-or-target-id>` forms that edit `config.yaml`
  rows from the active `component_sources.yaml` catalog.
- Clarified day-2 component edit output and docs so `component add` and
  `component remove` state that they update only `config.yaml`; existing
  `generated/` artifacts and live resources remain unchanged until `render` and
  a later deploy/destroy command run.
- Made day-2 component command output copy-pasteable and repeat-safe:
  `component add`/`component remove` next steps now include the resolved
  `config.yaml` path from the invocation, `component remove` continues to skip
  already-absent selectors, and `component list` uses a read-only context load
  so inspection does not rewrite normalized config.
- Clarified the validation command contract. `validate` is the source
  `config.yaml` readiness gate, `validate-sources` is the active
  `component_sources.yaml` catalog/source gate, and `validate-generated` is the
  rendered-bundle gate; docs now list generated-bundle backend auth before the
  state-aware live quota/capacity phase, matching the implementation.
- Removed the standalone `report` command. Deploy reports are generated as part
  of the lifecycle commands that actually apply state (`deploy`,
  `terraform apply`, `flux apply`, and `flux bootstrap`), while
  `email` now only sends the existing `generated/reports/deploy-report.md`
  artifact instead of pointing operators at a separate manual rewrite command.
  Report refresh no longer carries cleanup logic for removed inventory sidecar
  formats.
- Tightened generated-bundle target validation for the lower-level runtime
  commands. `terraform *` now accepts only the project `generated/` root or
  paths under `generated/infra/`, while `flux *` accepts only `generated/` or
  paths under `generated/flux/`, so infra-only commands cannot silently accept
  app manifest paths and apps-only commands cannot silently accept Terraform
  artifact paths.
- Aligned the `discover` and `bootstrap-ci` CI contract. `discover` is now
  documented as local git/filesystem discovery that does not require Nebius API
  credentials, and generated customer workflows now render clean repo-root
  deployment path filters with `*/*/generated/**` instead of `**/./...`.
- Tightened the `auth` target contract. `--project-config` now owns resolving
  both `project_id` and `client_name`, `--project-id` remains the manual target
  mode, and ambiguous mixes such as `--project-config` with `--project-id` or
  `--client-name` now fail before touching runtime auth state.
- Clarified the top-level `destroy` contract across CLI help, confirmations,
  README, and design docs. `destroy <config.yaml>` is now described as the
  project-wide destructive teardown path for all rendered resources represented
  by the sibling generated bundle and generated manifest.
- Moved deploy-time MK8s GPU validation settings to target-scoped
  `deploy.targets[].validations.mk8s_gpu.*` rows. Multi-cluster configs can now
  enable validations on one MK8s target and disable them on another without
  carrying a project-global validation block.
- Tightened Kubernetes observability collector validation so an enabled
  `nebius-observability-agent` app row must be backed by observability enabled
  on that same target `instance_id`, instead of passing because another MK8s target has
  observability enabled.
- Confirmed the bundled MK8s observability collector uses the current Nebius
  Observability Agent for Kubernetes OCI chart,
  `oci://cr.nebius.cloud/observability/public/nebius-observability-agent-helm`,
  and aligned the catalog, README, design notes, and Nebius skill reference to
  that source.
- Switched the bundled GPU DCGM metric target to the Nebius agent's
  Prometheus annotation discovery path, while keeping customer-defined
  `additionalTargets` preserved for non-catalog custom scrape jobs.
- Added the bundled MK8s Grafana observability console. When MK8s observability
  is enabled, cxcli now auto-enables target-scoped `gateway-helm` and `grafana`
  Helm releases, uses the maintained Grafana community chart with its default
  image/appVersion, exposes Grafana through Envoy Gateway/Gateway API,
  forces Envoy's generated LoadBalancer service to `externalTrafficPolicy:
  Cluster` for Nebius compatibility,
  provisions Prometheus/Loki/Tempo datasources from the Nebius public read
  endpoints, and seeds Nebius service dashboard imports plus cxcli-owned
  Kubernetes dashboard JSON from `component_sources.yaml`. Grafana datasource definitions, read-endpoint
  bindings, dashboard signal bindings, and the default `20m` idle session
  timeout are now catalog-owned. Local deploy/Flux paths create the runtime-only
  Kubernetes Secrets for Grafana admin credentials and the Observability read
  static token, issuing a `viewer` service-account static key only when the
  token Secret is missing. The deploy report now separates public write
  endpoints, public read endpoints, live Grafana links, and read endpoint probes.
- Added CPU-node scheduling defaults for non-GPU bundled Helm charts. Grafana,
  Envoy Gateway, cert-manager, ExternalDNS, External Secrets, and n8n now use
  chart-native hard node affinity with `nebius.com/gpu NotIn ["true"]` so these
  pods avoid Nebius GPU workers when CPU nodes are present; the Grafana-managed
  EnvoyProxy applies the same affinity to the generated Envoy data-plane pods.
  README and design docs now also explain that the catalog stores this policy
  once with YAML anchor `&nebius_cpu_only_node_affinity` and renders ordinary
  Kubernetes affinity into HelmRelease values.
- Aligned the bundled Nebius Grafana dashboards with their upstream datasource
  variable by provisioning the service-provider metrics datasource from the
  source catalog.
  The deploy report now opens the catalog-bound Metrics, Logs, and Traces
  dashboards through public Grafana `/goto/...` links after live Gateway status
  is available.
- Fixed multi-target MK8s GPU app materialization and reporting. GPU Operator,
  Network Operator, and their post-render patches are now resolved against the
  chart row's target `instance_id`, so one deployment can mix an
  InfiniBand/RDMA MK8s target with an Ethernet-only 1-GPU H100 target without
  conflicting chart defaults. Runtime validation now reports missing required
  GPU app rows per target, and the generated deploy report lists each MK8s
  cluster plus target-scoped validation headings.
- Fixed config normalization for direct multi-target edits. When a config adds
  another GPU-enabled MK8s target or enables Kubernetes observability after app
  rows already exist, cxcli now seeds the missing target-bound GPU Operator,
  Network Operator, and observability-agent rows before render/deploy, then
  materializes their managed chart values against the mutable runtime payload.
- Fixed GPU Capacity Dashboard preflight math for MK8s quota checks. cxcli now
  treats `resource-advice` on-demand/reserved/preemptible availability as VM
  slots for the selected preset and converts those slots to GPU units before
  comparing them with `compute.instance.gpu.*` quota requirements. For example,
  three reserved `8gpu-*` H100 VM slots now count as 24 available GPUs for a
  two-node request that needs 16 GPUs. Generated-bundle quota failures from
  `deploy` and `validate-generated` now also print the exact `quota-request`
  and `quota-check --all-regions` follow-up commands.
- Fixed GPU preset wizard capacity summaries. `compute_platform_presets` now
  aggregates live Capacity Dashboard rows per exact selected
  platform/region/preset instead of keeping only one fabric row, so matching
  H100 and H200 preset names stay separated and reserved VM availability is not
  hidden when the best reserved fabric differs from the best on-demand fabric.
- Fixed MK8s InfiniBand fabric recommendations for reserved GPU capacity. When
  live Capacity Dashboard rows show reservation slots on a different fabric
  than the strongest on-demand lane, the wizard now recommends the reserved
  fabric first and labels it `recommended for reservations`.
- Closed Nebius SDK instances used by runtime-auth IAM bootstrap and stale-profile
  validation and added a token-exchange readiness wait after new runtime auth
  keys are created. Fresh auth keys can be visible in IAM before the token
  service accepts them; cxcli now waits for propagation and filters the expected
  first-attempt deleted-key refresh traceback instead of letting that SDK stack
  trace appear while Terraform continues.
- Moved the MK8s observability-agent auto-selection notice in the interactive
  wizard so it appears immediately after target observability answers make the
  chart required, instead of after later MK8s infra prompts. The notice now
  also clarifies that the later app field prompt only controls chart-value
  customization: answering `n` keeps the auto-selected
  `nebius-observability-agent` app with defaults. The canonical customer
  observability contract now lives under `deploy:`; top-level `observability:`
  is no longer accepted.
- Expanded `generated/reports/deploy-report.md` with Grafana read data-source
  hints for enabled observability signals, including Prometheus, Loki, and Tempo
  data-source types, real Nebius read URLs, server/proxy access mode, and the
  required `Authorization: Bearer <observability static token or IAM token>`
  header guidance. The report now also clarifies that `service-provider` is
  literal in the Grafana service-metrics URL, expands federation bucket URLs
  for deployment-applicable service buckets. The Observability design doc now records
  the implemented workflow from catalog metadata through deploy observability
  normalization, render/deploy materialization, deploy-time GPU label
  reconciliation, and generated report/Grafana handoff.
- Renamed the MK8s GPU stack-source enum from `manual` to
  `operator_managed` across `nebius-cxcli` and the bundled `platform-infra`
  MK8s module. The old value is no longer accepted; the new name matches the
  actual contract, where GPU Operator still manages the host driver and
  toolkit path on that stack.
- Fixed the bundled MK8s operator-managed GPU Operator policy so it now
  also forces `values.driver.nvidiaDriverCRD.enabled=false`. Live testing on
  the operator-managed path showed the marketplace GPU Operator chart's
  Nebius `NVIDIADriver` CRD template fails during Flux install when that CRD
  path is left enabled, so cxcli now keeps the driver/toolkit enabled on the
  operator-managed stack while disabling only the broken CRD branch on both stack modes.
- Refactored the source-owned observability catalog structure for clarity. The
  external `component_sources.yaml` contract now keeps built-in observability
  signals under `primary_agent.{logs,metrics,traces}`, nested
  `endpoints.{write,read}.*`, and nested DCGM metric-target discovery/GPU-policy
  metadata.
  Parser/runtime wiring now maps that clearer external structure into the same
  runtime behavior, while README and design docs now also make the
  project-switch-versus-service-endpoint boundary explicit.
- Corrected the VM observability contract to match the built-in Nebius
  Monitoring agent behavior. VM service metrics are now treated as always-on
  for enabled `vm` components even when `deploy.observability.enabled=false`, and the
  generated observability endpoint/report summary now describes the VM
  agent's platform-managed metrics/logging ingest path instead of implying that
  the VM path has no write side at all. README, design docs, and the Nebius
  skill reference asset now make the same split explicit: public customer write
  endpoints are the MK8s/external-collector path, while the built-in VM agent
  uses Nebius-managed internal regional ingest.
- Added the canonical VM observability contract. The bundled `vm` catalog now
  uses `cli.observability.primary_agent.kind: monitoring_agent`, the project
  contract exposes `deploy.observability.vm.logs.*`, and config normalization
  materializes the supported Compute journald labels into VM `inputs.labels`
  instead of documenting the older `platform_monitoring_agent` marker. README
  and design docs now describe VM observability as the built-in Nebius
  Monitoring agent path with journald collection for systemd services,
  service-metric read endpoints, the `default` Logging bucket for
  user-ingested VM logs, and stop/start as the supported day-2 activation
  boundary for changed VM labels.
  They now also make the public-doc split explicit: Managed Kubernetes node
  VMs already get that Monitoring agent automatically, while cxcli keeps the
  MK8s project contract focused on the separate Helm-managed Kubernetes agent.
- Consolidated observability documentation into one design-doc section with the
  Nebius service/agent architecture, customer `config.yaml` contract,
  `component_sources.yaml` ownership model, public-safe endpoint map, auth
  boundaries, and onboarding workflow, and added a matching public-safe
  observability reference asset under the Nebius skill.
  The VM wizard now surfaces `deploy.observability.vm.logs.systemd_units` directly so
  operators can choose explicit unit allowlists at create time. `create` and
  runtime normalization also prune irrelevant project-scope branches, so
  VM-only configs no longer carry MK8s-only deploy validation defaults.
- Fixed two project-creation/runtime-auth contract gaps. The bundled `mk8s` wizard now treats
  target observability as deploy-scoped fields, so `create` and interactive `component add` can
  actually prompt the target observability switch and main signal toggles at wizard time and then
  auto-enable the collector app in the same run. Commands that use `--auto-auth-bootstrap` now
  also self-heal a cached runtime-auth profile when its Nebius auth public key has been deleted
  or the cached private-key metadata is broken; when auto bootstrap is disabled, the CLI now
  fails fast with explicit `auth --recreate` guidance instead of surfacing a later opaque auth
  failure.
- Tightened the local MK8s handoff and observability defaults. Local `deploy`, `flux apply`, and
  `flux bootstrap` now merge every selected target cluster into `~/.kube/config`; single-target
  runs still switch `current-context`, while multi-target runs preserve the operator's current
  context and add switchable contexts for each selected cluster. Multi-target infra-only `deploy`
  now refreshes all built-in cluster handoffs automatically after Terraform apply. The bundled
  MK8s observability contract also now names the Helm-based Kubernetes agent explicitly and treats
  `collect_k8s_cluster_metrics=true` as the enabled baseline once project observability is turned
  on, while keeping those customer-facing toggles on the project contract instead of duplicating
  them under the chart's static defaults. Multi-target MK8s observability now materializes that
  managed collector config into every target-bound `nebius-observability-agent` row instead of
  only the first matching app id, and the docs now clarify the live k8s-agent signal split:
  traces/logs use OTLP or file-log collection, while Prometheus-style metrics still flow through
  the scrape pipeline rather than an in-cluster OTLP metrics receiver.
- Fixed coworker-reported wizard/deploy rough edges: `create --validate-sources`
  now checks for missing source-validation tools such as `helm` before identity
  prompts, client names are validated and re-prompted immediately in the
  interactive wizard, field-level `q` consistently revisits the previous
  answered field, and interactive `component add` can complete an infra-only add
  without selecting an app component. Repeated infra component adds, including
  `mk8s@<resource-name>`, are documented as the canonical way to provision
  multiple modules of the same type in one project; infra-only deploys now skip
  the optional kubeconfig refresh instead of failing when multiple
  handoff-capable MK8s instances are enabled. Remote Helm chart packages that
  omit `README.md` no longer produce customer-facing source-validation warnings,
  while local chart paths still warn on missing README files. MK8s GPU validation
  command timeouts now become structured validation failures with JSON detail, so
  deploy summaries show `FAIL` and the underlying `kubectl` timeout instead of
  `NOT RUN`.
- Added canonical multi-target cluster binding for repeated infra types. When a
  bundle declares built-in cluster targets such as multiple `mk8s` instances,
  enabled app charts now bind to one target through `apps.charts[].instance_id`,
  render writes one flat Flux subtree per target under
  `generated/flux/targets/<target-id>/`, the generated manifest records
  `deploy.targets[]`, and `deploy`, `flux apply`, `flux destroy`, and
  `flux bootstrap` accept `--target <target-id>` / `--all-targets` instead of
  relying on implicit cluster order or a single global kubeconfig context.
- Added a source-driven observability stack contract. Deploy observability is
  a first-class setting that stays disabled by default; when enabled for an
  MK8s target, cxcli auto-enables the bundled `nebius-observability-agent` Helm
  chart, materializes the customer-facing logs/metrics/traces toggles into
  `values.config.*`, and keeps auth on the public-safe Nebius metadata/IAM
  token-file path instead of requiring secrets in repo config. The bundled
  catalog also now carries app-side observability metadata and records the GPU
  Operator's DCGM Exporter endpoint as an annotation-discovered metrics source
  with catalog-owned GPU node labels that run only DCGM Exporter plus the GPU
  Operator validator when Kubernetes metrics are enabled on the driverful
  `nebius_image` stack. `deploy` also reconciles those labels onto existing live
  GPU Nodes using the catalog-owned selector, while VM observability stays on
  the Nebius platform monitoring agent that is already present on
  Nebius-managed VMs and MK8s worker nodes. Direct `config.yaml` edits that set
  target observability enabled now seed the required collector app row during
  config normalization, matching the wizard/create behavior. Generated deploy
  reports now also include signal-aware public read endpoints for Grafana/external
  tools and regional collector write endpoints for metrics, logs, and traces from
  catalog-owned templates without storing static tokens or secrets in config.
- Fixed the MK8s GPU operator baseline to fail fast when a GPU-enabled project
  explicitly disables `nvidia-gpu-operator.values.dcgmExporter.enabled`. The
  docs now also clarify that long-running GPU telemetry belongs to DCGM
  Exporter / Prometheus / Grafana and that cxcli materializes the required
  GPU Operator DCGM node-label policy when observability metrics are enabled,
  while Prometheus scrape wiring remains the
  chart-native `values.dcgmExporter.serviceMonitor.*` surface rather than a
  `deploy` validation toggle.
- Fixed the new NCCL transport-selection path end to end: the shared
  `nccl-test` chart now renders its Socket/TCPIP and RDMA `mpirun` env wiring
  correctly, the source chart now ships conservative 1-GPU smoke-test worker
  defaults for direct Helm use, and cxcli derives NCCL worker GPU count from
  the resolved MK8s shape while sizing worker CPU/memory from live scheduler
  headroom and pinning the launcher onto non-GPU nodes when available, so
  Ethernet-only 1-GPU clusters stay schedulable instead of inheriting an
  8-GPU worker profile or spending GPU-node headroom on the launcher. The
  transport contract stays covered by a Helm-backed render regression when
  Helm is available, and GitHub Actions now triggers on
  `helm-charts/nccl-test` / `services/nccl-test` changes and runs explicit
  socket/RDMA chart smoke renders so transport-specific template bugs fail in
  CI instead of surfacing only during live `deploy`.
- NCCL deploy validation now runs for GPU-enabled MK8s clusters on both
  Ethernet-only and GPU-cluster / InfiniBand shapes. `deploy` auto-selects the
  NCCL transport from the resolved MK8s context, using Socket/TCPIP on
  Ethernet-only shapes and RDMA on GPU-cluster shapes, while enforcing the
  configured bus-bandwidth threshold only on the RDMA path. The MK8s wizard now
  exposes NCCL enable/max-nodes controls for all GPU-enabled shapes and hides
  only the RDMA-specific threshold field until the current shape is actually on
  the GPU-cluster / fabric path.
- Removed the hardcoded MK8s InfiniBand fabric table from the wizard/provider
  path. For cluster-capable GPU presets, `inputs.infiniband_fabric` choices now
  come from live Nebius Capacity Dashboard fabric rows, while live preset
  `allow_gpu_clustering` metadata remains the gate that decides whether a shape
  is actually GPU-cluster / RDMA-capable. Runtime validation now also rejects a
  configured fabric that does not match the live Capacity Dashboard rows for
  the selected shape when those rows are available.
- Fixed the first-party NCCL validation race and clarified the surrounding MK8s
  GPU contract: the `nccl-test` launcher now waits for each worker pod's main
  `nccl` container to become Ready before starting `mpirun`, docs now explain
  that `GPU stack readiness` already scans all Ready GPU nodes while
  `average bus bandwidth` is NCCL's normalized collective metric rather than a
  raw switch-port speed, and the driverful `nebius_image` path is documented as
  keeping Network Operator optional outside the shapes where Nebius requires it.
- Clarified MK8s GPU validation semantics and cleanup: the first deploy-time
  gate is now labeled `GPU stack readiness` in operator-facing output because
  it covers GPU Operator plus Network Operator / `NicClusterPolicy` when the
  selected shape requires the network stack, and the docs now say explicitly
  that cxcli keeps dedicated validation namespaces while deleting transient
  validation pods, transient NCCL `MPIJob` resources, and any transient
  Training Operator install after each run.
- Quota assessment now prefers operator auth such as `NEBIUS_IAM_TOKEN` or a
  Nebius CLI profile before falling back to the auto-bootstrapped runtime
  project service account. That keeps tenant-scope quota and Capacity
  Dashboard reads working during `deploy` / `quota-check` / `render` reruns
  when the operator has tenant-visible credentials, instead of needlessly
  warning on `PERMISSION_DENIED` from the project-scoped runtime identity.
- Fixed generated-bundle MK8s quota preflight for reruns: `deploy` and
  `validate-generated` now initialize the rendered backend, read the current
  Terraform state, and subtract MK8s quota already managed by that bundle
  before comparing the desired bundle against live Nebius quota/capacity. That
  keeps unchanged reruns of an existing cluster idempotent instead of failing
  like fresh creates, while still failing fast when the rerun would add real
  net-new capacity such as more nodes or a larger GPU shape.
- Added a new early design-doc section, `How Flux Works`, to explain the shared
  Flux controller model, the difference between `HelmRepository` vs
  `HelmRelease` status, what `image-automation-controller` is, how local
  `deploy` / `flux apply` differ from `flux bootstrap`, and which `kubectl`
  commands operators can use to check workload-release health vs GitOps
  bootstrap state.
- Tightened the local Flux success note for the "only source objects still pending"
  edge case: the CLI now says plainly that rendered `HelmRelease` workloads are
  already `Ready`, skips the remaining source-object wait, and prints
  `kubectl get helmreleases.helm.toolkit.fluxcd.io -A` as the direct follow-up
  command for operators who want to verify installed release health.
- Fixed generated-bundle Terraform output lookup for day-2 app commands:
  `terraform output -raw/-json` now initializes the rendered backend before
  reading outputs, so `flux apply`, `flux bootstrap`, built-in MK8s cluster
  handoff, and other Terraform-output-driven generated-bundle paths work on a
  freshly rendered `generated/infra` directory instead of failing with
  Terraform's "Backend initialization required" error. Flux API discovery now
  checks resource types cluster-wide instead of requiring app target
  namespaces to exist before `flux apply` creates them.
- Fixed MK8s GPU app-value materialization for persisted operator config:
  MK8s GPU policy-managed chart-value paths are now authoritative instead of
  preserve-existing. On `create`, `component add`, and `render`, cxcli rewrites
  the currently applicable policy paths from the catalog and clears stale
  no-longer-applicable policy paths. That prevents `render` / `deploy` from
  carrying forward malformed or outdated Helm values that make Flux fail the
  `nvidia-network-operator` install with Kubernetes validation errors.
- Fixed generated-bundle MK8s resource-name preflight to treat Nebius
  `Request error NOT_FOUND: ...` responses as the expected "resource absent"
  case. That keeps `deploy`, `validate-generated`, `terraform plan`, and `terraform apply`
  from falsely failing after operators delete a stale live MK8s or GPU cluster,
  while still failing fast on real live-name collisions that would make
  Terraform hit `AlreadyExists`.
- Deploy-time generated-bundle validation now fails fast on live MK8s name
  collisions before Terraform apply: after backend init, cxcli checks whether a
  bundled MK8s cluster name or its derived GPU-cluster name already exists live
  in the target project while not being tracked in the current Terraform state.
  That turns late Terraform `AlreadyExists` failures into targeted preflight
  guidance telling operators to delete the stale live resource, import it into
  state, or rename the cluster and rerender.
- Changed `create` to generate name-derived project folders instead of ID-derived folders: operators still enter `tenant_id` / `project_id`, but after those IDs are validated the starter config now lands under `<deployments-root>/<tenant-name>/<project-name>/config.yaml` using filesystem-safe slugs from the resolved Nebius tenant/project names. `config.yaml`, generated manifests, GitHub environment names, deploy-report email identity, and other runtime surfaces still use `tenant_id` / `project_id` as the authority rather than inferring identity back from the folder names. The CLI now fails fast on name-based folder collisions so one project cannot overwrite another just because their normalized names would map to the same path, and the docs/tests/examples were updated to treat `<tenant-folder>/<project-folder>` as the canonical path shape for config-based commands.
- Reworked `quota-request` around the correct quota object model: live
  insufficiency detection still reads `QuotaAllowance`, but request creation is
  now treated as a separate `QuotaRequest` path. Internal Nebius operator
  environments on the Nebius internal network can auto-submit through the
  internal request surface, while external/public environments fall back
  cleanly to exact manual quota-request guidance instead of mutating quota
  allowances directly.
- Clarified the top-level README install contract: prerequisites and install
  steps now live near the top of the document, the command-specific local-tool
  requirements are called out explicitly, and the Helm wording now makes clear
  that Helm is needed for source/chart validation paths rather than as a
  blanket prerequisite for normal render/deploy use.
- Fixed bundled MK8s NCCL default hydration when `helm` is unavailable:
  local/unit-test resolution now falls back to the checked-in
  `helm-charts/nccl-test/values.yaml`, so the validation spec still keeps the
  first-party `chart_values.image.*` and benchmark defaults instead of
  degrading to an incomplete override-only payload.
- Removed the public `validate --strict` split: `validate` now always runs the
  deployment-readiness stack, `validate-generated` now reuses the same strict
  generated-bundle preflight as `deploy`, and the warning-only non-strict path
  remains internal to `create` so bootstrap/edit flows still continue through
  quota shortages until operators explicitly request quota.
- Aligned `validate-generated --help` and the command reference text with the
  actual generated-bundle contract: the help surface now calls out readiness,
  manifest validation, and optional portability explicitly instead of sounding
  like a generic artifact-only check.
- Improved `quota-request` manual fallback output: when automatic request
  submission is unavailable for the current identity or environment, the
  console-follow-up block now prints the minimum target limit and minimum
  increase to request for each confirmed shortage instead of listing only the
  quota names.
- Centralized GPU quota checks on the live Nebius Capacity Dashboard
  `resource-advice` surface: GPU quota sufficiency now resolves against the
  exact platform/region/preset/fabric shape instead of mixing regular quota
  allowances with a separate Capacity Block Group overlay, and `quota-check`
  / `validate` / `create` / `render` / deploy-time preflight now all share
  that same GPU path.
- Improved live GPU wizard guidance across bundled infra flows: GPU preset
  prompts now annotate/rank supported GPU shapes with live Nebius Capacity
  Dashboard `resource-advice` availability when tenant/region context is
  available, optional InfiniBand fabric prompts now annotate the exact
  platform+preset fabrics with live on-demand/reserved availability and
  highlight the recommended default without forcing the field to be set, and
  `create` quota warnings now print the exact `quota-request <config.yaml>`
  follow-up command instead of stopping the config workflow.
- Aligned shared GPU interconnect guidance across MK8s and VM wizard flows:
  single-GPU shapes are labeled as Ethernet-only testing/dev options, while
  clusterable multi-GPU shapes are labeled as the InfiniBand /
  GPUDirect-RDMA path. Fabric-scoped Capacity Dashboard rows for single-GPU
  shapes are now treated as availability-ranking input only, and stale VM
  fabric values are cleared during interactive edits when the selected GPU
  shape no longer supports clustering.
- Tightened GPU-cluster contract alignment with the public Nebius Compute VM
  types guidance: VM GPU-cluster validation no longer hardcodes an `8gpu-*`
  name check when live preset metadata is available, and MK8s deploy
  validation now warns when operators force NCCL onto Ethernet-only /
  non-cluster GPU shapes instead of silently pretending that configuration is
  representative of an InfiniBand training environment.
- Fixed MK8s deploy status fail-fast handling so `deploy` no longer aborts
  immediately on stale old node-group error events from a previous failed run
  when Terraform is about to replace that failed group. Fresh terminal API
  errors from the current run still abort early.
- Fixed `generated/reports/deploy-report.md` formatting so report output no
  longer ends with duplicate blank lines when deploy validations are
  present, keeping the generated Markdown clean for linting in customer repos.
- Changed interactive `create` so `tenant_id` / `project_id` no longer
  default from an existing project under the deployments root. `create`
  now assumes a new target unless you explicitly pass or type an existing
  tenant/project, and only then warns before overwriting that resolved folder.
- Merged the human-readable inventory and deploy-validation markdown outputs
  into one canonical `generated/reports/deploy-report.md`. It now combines
  `Infra`, `Apps`, and `Validations`, `email` sends that same file,
  deploy-time validations still keep their
  per-validation JSON detail reports, and stale markdown/report artifacts are
  cleared before each deploy run so skipped or failed runs do not leave
  misleading old summaries behind.
- Tightened the project-level runtime entrypoints to one canonical target:
  `deploy`, `destroy`, and `email` now accept only
  `config.yaml`, resolve sibling `generated/` automatically, and reject direct
  `generated/` targets instead of keeping a backward-compatibility dual path.
  The generated manifest and rendered report artifacts remain the
  authoritative runtime contract after render, so post-render source edits do
  not silently change what gets applied, destroyed, written, or emailed.
- Clarified `validate` and `quota-check` help/docs wording so the command
  surface now matches the actual live quota-plus-capacity checks already used
  by runtime validation.
- Tightened `create` overwrite semantics so "from scratch" now includes
  `client_info`: once an existing resolved project folder is confirmed
  for overwrite, the client name / region / notification prompts restart from
  the normal create defaults instead of reusing the old config values.
- Fixed the MK8s GPU validation wizard to hide the
  `deploy.targets[].validations.mk8s_gpu.health_checker.enabled` toggle unless the active
  catalog actually exposes an apps component with
  `cli.mk8s_gpu_policy.role: health_checker`, so bundled catalogs no longer
  present an impossible health-checker prompt during `create` / `component add`.
- Fixed component-level wizard phase control flow so answering `n` to
  `Configure '<component>' component fields now?` skips that component phase
  and continues with the remaining selected components, while `q` still stops the
  wizard. This fixes the MK8s GPU app case where skipping
  `nvidia-network-operator` previously prevented the later
  `nvidia-gpu-operator` prompt from appearing at all.
- Tightened the MK8s GPU health-checker contract so the bundled NVIDIA path
  treats it strictly as a custom app-policy hook instead of a built-in deploy
  validation: bundled target defaults now omit `health_checker` unless the
  active catalog actually supplies a compatible app, and `deploy
  --skip-validation` no longer advertises a nonexistent `health-checker`
  built-in validation kind.
- Fixed the Nebius `gpu_stack_source: nebius_image` MK8s path so the bundled
  catalog now renders the missing driverful-node policy: GPU Operator keeps
  host GPU-driver and NVIDIA Container Toolkit management disabled, and the
  bundled operator path now suppresses GPU Operator's NFD whenever Network
  Operator owns the networking stack so only one NFD instance is deployed.
  Network Operator enables NFD plus Mellanox NodeFeatureRules and adds a Helm
  post-render patch that exposes `rdma/shared_device` on driverful InfiniBand
  nodes without deploying the OFED driver container.
- Fixed the manual MK8s GPU-cluster / InfiniBand path so the bundled Network
  Operator render also patches `NicClusterPolicy` with `rdma/shared_device`
  instead of relying on the chart default CR, which only handled OFED. Manual
  operator-managed InfiniBand nodes now line up with the same scheduler-visible
  RDMA contract that deploy-time readiness validation already expects.
- Refactored the bundled MK8s GPU app-policy catalog so reusable driverful NFD
  overlays and `NicClusterPolicy` RDMA patch bodies can be named once under
  `cli.mk8s_gpu_policy.default_sets` / `post_render_patch_sets` and referenced
  from multiple rules. This keeps the Network Operator RDMA plugin tag and
  selector details catalog-owned without repeating the same patch inline across
  multiple `component_sources.yaml` rules.
- Fixed the MK8s GPU allocatable-resource filter to parse Kubernetes extended
  resource prefixes explicitly instead of matching the literal
  `nvidia.com/` prefix with a raw string prefix check, avoiding a false-positive
  CodeQL URL-sanitization warning without changing the GPU/RDMA readiness
  behavior.
- Clarified and locked in the layered MK8s GPU validation contract: source
  comments, README/design docs, and regression tests now explicitly treat
  `operator_readiness`, `gpu_visibility`, and `nccl` as a cheapest-to-most-
  expensive chain with distinct responsibilities rather than overlapping
  duplicate checks.
- Ignored local coverage data files and packaged chart archives in the service
  repo `.gitignore`, and clarified that the managed customer deployments
  `.gitignore` stays intentionally narrow to generated Terraform runtime files
  and tfvars instead of acting like a generic developer ignore file.
- Exposed bundled MK8s GPU validation controls as a target-facing deploy
  contract under `deploy.targets[].validations.mk8s_gpu.*`, so these CLI deploy checks
  no longer masquerade as Terraform inputs. The wizard still surfaces the same
  toggles from catalog defaults, but the resulting values now persist in
  `config.yaml` as deploy settings, and local `deploy` also supports one-run
  `--skip-validations` / `--skip-validation <kind>` overrides.
- Removed the temporary backward-compatibility shims from that MK8s GPU
  validation contract: `infra.components[].inputs.gpu_validation_overrides`
  now fails fast instead of being migrated, and local `deploy` now requires
  generated-manifest `deploy.validations` metadata instead of recomputing GPU
  validation specs from older bundles at runtime.
- Tightened the interactive MK8s GPU app flow: when the infra prompts turn on
  a GPU shape that requires `nvidia-gpu-operator` or
  `nvidia-network-operator`, the wizard now auto-enables those app rows before
  the app phase starts so the same `create` / `component add` pass can still
  show their prompts instead of only materializing them later in `config.yaml`.
- Simplified the bundled `mk8s` source catalog by removing the one-off raw
  `wizard:` block for GPU validation helper defaults. cxcli now derives those
  virtual prompt defaults directly from `components.infra.mk8s.cli.gpu.validations`
  during source parsing, so the catalog keeps one source of truth while the
  interactive wizard behavior stays unchanged.
- Removed the now-unused YAML anchors from the bundled MK8s
  `cli.gpu.validations` defaults after the wizard-helper refactor, so the
  catalog no longer carries dead alias syntax.
- Clarified the MK8s boot-disk wizard wording for
  `NETWORK_SSD_NON_REPLICATED`: it now describes the disk as the lowest-cost
  high-performance SSD-backed option, not the cheapest disk overall.
- Tightened MK8s GPU operator readiness around live cluster behavior: the
  readiness report now requires allocatable GPUs on Ready nodes instead of
  assuming manual `nvidia.com/gpu.deploy.*` labels. If the upstream GPU Operator
  `ClusterPolicy` condition reason is stale or conservative, for example
  `NoGPUNodes`, allocatable GPUs on Ready nodes remain the data-plane signal
  cxcli uses.
- Tightened MK8s GPU-cluster / InfiniBand readiness further so `deploy` no
  longer treats a fabric-enabled cluster as ready just because `ClusterPolicy`
  and `NicClusterPolicy` report `ready`: the saved operator-readiness report
  now also records `NicClusterPolicy.status.appliedStates`, checks that Ready
  GPU nodes advertise scheduler-visible RDMA-style allocatable resources (for
  example `rdma/shared_device`), and fails fast when the control-plane objects
  are green but pod-facing RDMA exposure is still missing.
- Simplified the live MK8s operator-readiness polling loop: `ClusterPolicy`
  and `NicClusterPolicy` remain the primary control-plane signals, allocatable
  GPUs on Ready nodes remain the GPU data-plane gate, daemonset rollout
  summaries are now collected once for the saved report instead of being
  polled on every pass, and local `deploy` now treats manifest
  `deploy.validations` as a required part of the generated-bundle contract
  instead of recomputing runtime-derived GPU validation specs from older
  bundles.
- Refined the bundled GPU Visibility reporting contract: the validation still
  uses a sampled CUDA workload as the authoritative pass/fail gate, but its
  saved report now also captures the Ready GPU nodes' allocatable
  device-plugin resources so operators can inspect `nvidia.com/gpu` and any
  RDMA-style resource keys without mistaking raw `allocatable` output for a
  full runtime proof.
- Fixed bundled MK8s GPU Operator deploys on Nebius-managed GPU images by
  also disabling the chart's Nebius `NVIDIADriver` CRD path in the rendered
  Helm values, avoiding the live GPU Operator Flux install failure
  on `templates/nvidiadriver_nebius_patch.yaml`.

- Clarified the source-config validation contract: `validate` help now
  explicitly calls out strict readiness, VPC networking preflight, and fail-fast live
  quota/capacity checks, and `component add` / `component remove` now point
  operators at the same `validate`, then `render` day-2 loop already used
  after `create`.
- Hardened `deploy <generated-dir>` with an explicit generated-bundle
  preflight before Terraform apply: strict readiness checks against the
  manifest runtime config, VPC networking preflight, live Nebius
  quota/capacity validation, Terraform validation for `generated/infra`, and
  rendered Flux manifest validation when apps are enabled now all fail fast
  inside `deploy` itself instead of relying on operators to run separate
  commands first.
- Changed `validate <config.yaml>` to run the live Nebius quota/capacity
  assessment as part of the default readiness gate, so operators see confirmed
  shortages before `deploy` and the command fails on confirmed insufficiency.
- Added `quota-request <config.yaml>`, which reuses the existing live quota
  assessment and plans direct tenant/project quota allowance requests for the
  confirmed insufficient quota dimensions through the published Nebius quota
  API instead of requiring manual web-console entry; the CLI prints the target
  limits it plans to request, falls back cleanly to a manual Administration →
  Limits → Quotas follow-up when Nebius denies the direct API write, now also
  prints coverage-gap detail when nothing can be submitted, and points
  operators to the web console for submission or status tracking.
- Refactored bundled Compute boot-disk defaulting so the catalog now owns
  ordered shared cxcli boot-disk rules under `compute.boot_disk_defaults`,
  keyed by resolved preset resources such as vCPU, RAM, and GPU count.
  `create`, `component add`, and runtime config loading now materialize
  explicit MK8s and VM-style boot-disk values from the first matching rule for
  the selected shape, while unmodeled shapes fail fast so maintainers update
  the shared policy. Guided disk-type prompts now show consistent settings-owned
  Nebius price/performance labels for all recommended SSD-backed choices and
  clarify that MK8s boot-disk encryption is not configurable from cxcli.
  High-performance SSD types still round to required 93 GiB multiples, regular
  `NETWORK_SSD` values stay exact GiB sizes, explicit first-class inputs or
  `template.boot_disk` overrides remain authoritative, and the quota
  estimator/request planner can now cover the common `compute.disk.size.*`
  MK8s shortages without waiting for a deploy-time failure. Public MK8s
  node-group `boot_disk` still exposes size/type only, so cxcli documents but
  does not attempt to toggle optional SSD NRD / SSD IO M3 encryption.
- Replaced the earlier Capacity Block Group / `compute.gpucluster.count`
  overlay with the live Capacity Dashboard `resource-advice` path for
  fabric-bound GPU requests, keeping `validate`, `create`, `quota-check`,
  `render`, and deploy-time guard rails on one GPU availability model.
- Refined generated-bundle destroy behavior so top-level `destroy` now skips
  separate Flux app deletion when the generated infra bundle destroys the
  handed-off MK8s cluster directly, while external-cluster app bundles still
  delete rendered Flux resources first. `destroy`, `terraform destroy`, and
  `flux destroy` now print path-specific confirmation warnings for the actual
  target they remove, and `flux destroy` / pre-destroy app teardown now skip
  cleanly with a note when the target cluster is reachable but Flux CRDs are
  already absent instead of surfacing raw `kubectl` resource-mapping errors.
- Tightened interactive wizard UX: flat Terraform module-input prompts now use
  `q` to revisit the previous prompt instead of only skipping ahead, while
  nested value/object prompts keep the existing branch-level backout behavior.
  The MK8s boot-disk wizard still documents the NRD / IO M3 encryption
  limitation in the README/design docs, but that note is no longer repeated in
  the live prompt banner or disk-type option labels.
- Removed hardcoded dollar figures from the MK8s boot-disk wizard labels so
  live CLI guidance does not drift as Nebius pricing changes. The README and
  design doc now point operators to the official Nebius disk-type and pricing
  pages for current values instead of restating specific amounts in the prompt
  text.
- Improved `validate` terminal output with one concise
  validated-scope list that separates `infra` and `apps` and shows their
  catalog groups such as `Compute`, `Storage`, `Platform`, or `Workloads`,
  so successful validation is more informative without adding another heavy
  inspection pass.
- Fixed bundled app runtime Helm resolution when an app id differs from the
  chart basename: dependency lookup, post-create source validation, live chart
  default pruning, and Flux rendering now keep using the catalog chart name
  (for example `network-operator` / `gpu-operator`) instead of incorrectly
  reconstructing refs from app ids such as `nvidia-network-operator`.
- Improved the bundled MK8s wizard default for `inputs.k8s_version`: the
  first live Nebius control-plane version is now auto-selected into the
  interactive flow instead of defaulting to an unset value.
- Refined interactive wizard exit behavior for field prompts: `q` now backs
  through previous wizard steps instead of aborting the whole wizard immediately,
  while `qq` preserves the full wizard stop path.
- Adjusted per-component field-phase defaults in the interactive wizard so
  infra components still default to `y`, while app chart field prompts now
  default to `n` because chart overrides are normally optional.
- Clarified the remaining catalog-owned NCCL MPI overlay contract in
  `component_sources.yaml`, README, and the design doc: the bundled
  `-mca coll ^hcoll` override stays catalog-owned for platform-specific
  Blackwell cases instead of becoming a shared chart default.
- Tightened MK8s in-cluster deploy validation behavior so `deploy`, `flux apply`, and `flux bootstrap` no longer block on a generic all-nodes-ready pre-wait, MK8s GPU validations now emit live Kubernetes status instead of silently polling, local `deploy` keeps a continuous spinner alive across those validation phase transitions with non-TTY log fallback, and the bundled GPU Visibility/NCCL checks now bound their default node fan-out with catalog-owned `max_nodes` caps plus shorter default timeouts to keep deploy-time validation fast on large clusters.
- Simplified the bundled app-side MK8s GPU catalog contract: `components.apps.<id>.cli.mk8s_gpu_policy` now uses one conditional `rules` list where each rule can auto-enable the app and/or contribute conditional chart defaults, replacing the earlier split between `auto_enable` and `value_overrides` while keeping top-level app `defaults` as the unconditional chart-default layer.
- Added the published portable OCI source for the bundled `nccl-test` Helm chart in `component_sources.yaml`, so the NCCL validation chart now resolves through the same dual `source.local` / `source.portable` contract as the other bundled charts.
- Aligned the bundled NCCL validation image overrides with the first-party `services/nccl-test` release path, so `component_sources.yaml` now points at `cr.<region>.nebius.cloud/<registry-short-id>/images/nccl-test` SemVer tags instead of the legacy `nebius-benchmarks/nccl-tests` repository.
- Pinned the bundled NCCL chart/image contract to the current first-party release set: `component_sources.yaml` now keeps the portable chart source on `oci://cr.<region>.nebius.cloud/<registry-short-id>/charts/nccl-test --version 0.2.8`, the bundled MK8s GPU validation path consumes the runtime image `cr.<region>.nebius.cloud/<registry-short-id>/images/nccl-test:0.2.0` from the chart's own defaults, and release-catalog coverage now guards OCI chart refs from being rewritten back to legacy GitHub tree paths.
- Simplified the bundled MK8s GPU app catalog around live chart defaults and customer-facing reports: the shared NCCL image/tag plus deploy-time benchmark defaults are now sourced directly from `helm-charts/nccl-test/values.yaml`, only the platform-specific Blackwell MPI overlay remains in `mk8s_gpu_policy.rules`, redundant operator values that already match the live NVIDIA chart defaults were dropped from `component_sources.yaml`, and the generated GPU validation reports now preserve readable field order while keeping only compact summaries plus failure-focused log excerpts.
- Fixed the remaining `nebius-cxcli-ci` wheel gate for local-only charts: branch CI now verifies that the built wheel bundles `component_sources.yaml` without forcing release-grade portable chart sources, while the tag/release workflow still runs the stricter portable `verify-wheel` / `verify-catalog` checks.
- Fixed `nebius-cxcli-ci` catalog validation for branch work: the normal CI workflow now runs `validate-sources component_sources.yaml` with source profile `local` so new in-repo Terraform modules and local-only Helm charts are validated against the checked-out branch, while the release workflow keeps the portable-profile validation for published wheel/catalog verification.
- Aligned the remaining strict-validation and docs surfaces with the current Helm/source contract: the MK8s GPU strict-validation coverage now enables `nvidia-gpu-operator` before asserting missing GPU shape fields, and the README/design examples now consistently show app charts under `source.portable` instead of the removed top-level `source.repo/chart/version` layout.
- Added a bundled `vm` infra component backed by `platform-infra/modules/vm`: the catalog now exposes guided project-subnet and live compute platform/preset selection, resolves `source_image_family` from the live Nebius public image inventory without a bundled hardcoded family default, preserves static public-IP mode choices plus optional GPU-cluster fabric guidance, and includes runtime validation/quota estimation for standalone Nebius VMs so the new module behaves like a first-class `nebius-cxcli` component instead of a raw custom Terraform source.
- Refactored the bundled MK8s GPU contract around the actual Nebius node-group model: `inputs.gpu_stack_source` and `inputs.gpu_stack_preset` now replace the earlier driver-centric terminology in the customer- and catalog-facing contracts, the MK8s module/docs now describe Nebius-managed `gpu_settings.drivers_preset` vs operator-managed GPU stacks explicitly, and the NCCL path now renders a first-party `helm-charts/nccl-test` chart selected through the same Helm `source.portable` / `source.local` contract used by other bundled charts instead of assembling the raw `MPIJob` manifest in Python.
- Replaced the old MK8s GPU hardcoded profile split with component-local settings policy: `component_cli_settings.yaml` now keeps MK8s GPU image preferences and validations under `components.infra.mk8s.cli.gpu`, keeps GPU operator/network operator auto-enable rules and Helm value overrides on the operator app entries under `components.apps.<id>.cli.mk8s_gpu_policy`, while `component_sources.yaml` keeps the reusable Terraform/Helm source and release metadata. The catalog pair removes the unused standalone `nvidia-device-plugin` entry, still materializes Nebius-image vs operator-managed MK8s defaults from the live Nebius compatibility matrix, keeps the GPU Operator B300 driver pin out of Python, and still persists deploy-time GPU readiness/visibility/NCCL reports under `generated/reports/`.
- Changed interactive `create` overwrite UX so it now resolves `tenant_id` / `project_id` before showing any overwrite warning: existing deployments roots no longer emit a root-wide pre-warning, and confirmation appears only when the chosen resolved project folder already exists.
- Changed the canonical project layout to match the two-level project hierarchy under the deployments root: project configs now live at `<deployments-root>/<tenant-folder>/<project-folder>/config.yaml`, and `create <deployments-root>` is a bootstrap/overwrite command instead of an existing-config reconcile path. Once that resolved project folder already exists, interactive reruns now require explicit overwrite confirmation unless `--force` is provided, non-interactive reruns require `--force`, overwrite recreates only that one resolved project folder from scratch, client-info prompts restart from the normal create defaults, and infra/apps selections plus component values are rebuilt from the current create inputs instead of being merged from the old config; docs/help/tests were realigned to make `component list/add/remove` the default day-2 editing surface.
- Tightened the remaining help/docs wording around the project-folder layout so `create --help`, README, and the design doc consistently describe the canonical overwrite target and the generated customer workflow's canonical `<tenant-folder>/<project-folder>/generated/**` watch scope.
- Tightened the generated customer GitHub workflow trigger to the canonical two-level deployment layout under the deployments root: it now watches only `.../<tenant-folder>/<project-folder>/generated/**` paths instead of a broader recursive `generated/**` glob that could still match stale pre-refactor layouts.
- Extended catalog-driven Nebius fail-fast status monitoring beyond MK8s: bundled SSH jump-host and WireGuard gateway modules now declare live `nebius.compute.instance` watchers, bundled `mysterybox` now declares `nebius.mysterybox.secret` watchers that expand one component row into one watcher per configured secret name, supported watcher kinds now include compute instances and MysteryBox secrets, and the MSP PostgreSQL/SFS/object-storage/compute-instance/MysteryBox pollers now abort long-running apply/destroy waits from terminal Nebius SDK operation failures instead of only printing progress summaries.
- Changed explicit `quota-check` output to also print both confirmed checked quota names and coverage-gap reasons as vertical lists under each component, including partial-coverage components such as MK8s when the checked dimensions are sufficient but other dimensions still remain coverage gaps.
- Added guarded built-in destroy recovery for generated Terraform bundles: `destroy` / `terraform destroy` now auto-clear a stale backend lock when the existing local-owner safety checks already pass, retry Terraform destroy once, and if destroy is still blocked by a live MK8s node-group create stuck in terminal-error provisioning, they can delete that stuck node group through the Nebius SDK and retry destroy again inside the same confirmed teardown flow.
- Changed `render <config.yaml>` to always run pre-render runtime validation before writing artifacts, so active-source drift, unresolved component dependencies, and Terraform module schema/input mismatches fail before any generated bundle side effects.
- Changed long-running `deploy` / `terraform apply` / `terraform destroy` MK8s monitoring from passive alerting to active fail-fast behavior: node-group API event levels are now read correctly from the live SDK enum fields, terminal node-group failures surface their Nebius error detail directly in status/recovery output without leaking raw SDK object reprs, and apply/destroy abort their Terraform wait loop instead of idling until a generic timeout when the live MK8s API already shows the operation has failed.
- Added live MK8s GPU stack-preset selection to the bundled `mk8s` wizard profile: `inputs.gpu_stack_preset` now comes from the MK8s compatibility matrix, the wizard can auto-select and materialize a singleton compatible preset into `config.yaml`, and new provider option source `mk8s_gpu_stack_presets` is available for other catalog wiring.
- Tightened bundled MK8s GPU-cluster guidance around live preset capability instead of guesswork: the wizard now selects `inputs.gpu_nodes_preset` before `inputs.infiniband_fabric`, the later fabric prompt is shown only when the chosen preset's live SDK metadata allows GPU clustering, stale `infiniband_fabric` values are cleared during interactive edits when the selected GPU shape no longer supports clustering, and runtime validation now fails early on invalid fabric+preset combinations instead of deferring them to Terraform/MK8s admission errors.
- Fixed `component_sources.yaml` wizard-option normalization so explicit `options.args` entries and `skip_prompt_if_no_choices` survive catalog loading; bundled MK8s profile expansions now keep extra provider args such as `preset_path` instead of silently dropping them.
- Standardized explicit CLI severity colors so warnings now render in amber and errors continue to render in red, and aligned the shared shell-scripting skill/template to the same warning/error color contract.
- Refined quota coverage-gap terminal output so repeated internal gap reasons for one component collapse to one concise per-component summary entry in explicit `quota-check`, while routine `create`/`render`/`deploy` output keeps those non-blocking coverage-gap details in the manifest instead of printing them every time.
- Added `quota-check --all-regions`, which replays the current config's quota requirements across all discovered tenant/project regions and prints per-region availability for the same shape while keeping the command's normal pass/fail semantics tied to the config's selected region; plain `quota-check` now suggests that exact rerun command only for confirmed insufficiency, while coverage-gap-only warnings stay informational.
- Changed local `deploy` so built-in MK8s handoff and local kubeconfig refresh still run even when no app charts are enabled, while the no-app path now skips node-readiness and Flux apply/bootstrap checks instead of requiring a Flux phase just to hand off cluster access.
- Added live Nebius quota guard rails for bundled infra components: `create` now warns when the selected project shape already exceeds current tenant/project quota, new read-only `quota-check <config.yaml>` runs the same live assessment on demand, `render` reruns the quota check and stores the report in `generated/nebius-cxcli-manifest.json` while still completing with warnings, and `deploy` now fails fast before Terraform apply when live quota is still insufficient.
- Changed shared-derived component defaults to materialize into `config.yaml` instead of remaining catalog-managed at render time: jump-host `ssh_user_name` and any other `defaults: shared.<path>` targets are now seeded into selected component/chart rows during `create` and `component add`, runtime `render`/`validate` no longer backfill missing shared-derived values from the catalog, and explicit config values no longer conflict with those original catalog seeds.
- Improved bundled MK8s onboarding UX and fail-fast behavior: the wizard profile now guides `inputs.k8s_version`, GPU follow-up fields expand immediately after `gpu_enabled=true` and after GPU platform selection, strict validation treats effective CPU/GPU node-group prerequisites as conditionally required before Terraform apply, and empty Flux renders with no enabled app charts now skip local Flux apply instead of emitting a comment-only `helm-repositories.yaml`.
- Extended jump-host SSH public key handling so private `component_sources.yaml` `shared.admin_ssh.public_key` and per-project `infra.components[].inputs.ssh_public_key` accept inline `ssh-rsa` / `ssh-ed25519` values or readable local `.pub` paths such as `~/.ssh/id_ed25519.pub`; `create`, `component add`, and config-driven commands now resolve those local files, validate supported key formats at runtime, and persist normalized inline key text into `config.yaml` and generated manifests.
- Hardened local Flux deploy/apply waiting so terminal `HelmRelease`/`Kustomization` failures are surfaced from the actual failing workload resource, remaining workload resources are allowed to settle before the command exits, and the default outer wait window now honors rendered workload `spec.timeout` hints plus a short grace period instead of assuming one fixed chart timeout.
- Extended the source-catalog Flux timeout contract so `cli.flux.release_timeout` defines the global default rendered `HelmRelease.spec.timeout`, while per-app `release.timeout` remains optional and only overrides that default when a specific chart needs a different install/upgrade budget; the bundled default is now `5m`, aligned with the upstream Helm/Flux action timeout.
- Fixed the bundled `cert-manager` app catalog defaults to enable chart CRD installation (`values.crds.enabled: true`), preventing fresh-cluster installs from hanging on the startup API check job while cert-manager CRDs are still absent.
- Fixed `render` overwrite prompting so the first render after `create` no longer warns just because the project already has the empty generated scaffold and placeholder report artifact; the warning now targets meaningful existing rendered artifacts.
- Improved config-path error handling for config-driven commands such as `render`: passing a directory like `generated/` now fails with a targeted “expected project config.yaml file path” message instead of leaking a raw `Is a directory` exception.
- Improved complex wizard prompt wording to ask for single-line YAML/JSON values for maps, objects, and object lists, and stopped app components with an empty top-level `values: {}` block from showing a confusing whole-map prompt when no concrete Helm value leaves are known yet.
- Added `wizard.<field>.prompt: false` support so bundled profiles can suppress optional advanced fields from the interactive wizard; the MK8s profile now hides the raw `mk8s_*_overrides` passthrough maps while keeping them available for manual `config.yaml` edits.
- Hardened `create --force` guard rails for existing projects: the CLI emits a force-specific overwrite warning before overwriting an existing resolved project folder and documents that `create --force` does not delete the deployments root or unrelated projects.
- Wired MK8s `inputs.infiniband_fabric` into the built-in wizard profile with a guided, optional fabric selector keyed by the chosen GPU platform and `client_info.nebius.region_id`, using the Nebius GPU-cluster fabric matrix instead of a raw free-text prompt.
- Fixed `create` wizard prompt helper late-binding closures in `cli.py` so Ruff no longer flags `B023` on the deferred module-prompt builders, and tightened the runtime-shape unit coverage to skip post-write validation in the test that only asserts generated config structure.
- Added a central Codex skill at `../../skills/onboard-nebius-cxcli/` for onboarding Nebius Terraform modules into `nebius-cxcli`; it documents the catalog-first onboarding flow, the code-owned layers (`wizard_profiles.py`, `provider_options.py`, `validation_profiles.py`, `runtime_component_validation.py`, `cluster_handoffs.py`, `deployment_status.py`), and the focused test/doc updates expected for each change shape.
- Refined MK8s wizard platform discovery to use live Nebius platform inventory at runtime: CPU/GPU platform prompts now intersect the MK8s compatibility matrix with the selected project's compute-platform list, so the wizard only shows currently available supported platforms while preset choices remain live per selected platform.
- Extended the built-in `ssh-jumphost` and `wireguard-gw` wizard profiles to use the live compute platform inventory plus preset chaining, so those VM modules no longer rely on manual `platform` / `preset` entry when project-scoped Nebius choices are available.
- Moved bundled infra runtime validation-profile selection out of the public `component_sources.yaml` catalog and into code-owned defaults in `src/nebius_cxcli/validation_profiles.py`; bundled components now omit repeated internal `validation` markers, and the catalog loader rejects that field instead of carrying a compatibility path.
- Removed the public infra `runtime` block from `component_sources.yaml` and moved the bundled MK8s kubeconfig/bootstrap handoff into code-owned built-ins in `src/nebius_cxcli/cluster_handoffs.py`; auto-discovered Terraform outputs remain the only catalog-facing producer contract, docs/tests were realigned, and inventory/deployment-status helpers now key off `status.kind` instead of old handoff/kind shortcuts.
- Fixed create/component-add wizard handling for declared `component_sources.yaml` `wizard` paths: provider-backed or catalog-declared `inputs.*` / `values.*` fields that are not yet materialized in the payload are now prompted normally instead of emitting a misleading “path not found in config payload” warning, and nested missing containers are created when those prompts are answered.
- Added built-in infra `wizard_profile` support so common Nebius component types can expand to tested wizard wiring from a short profile name, while explicit `wizard` entries remain available as overrides.
- Clarified the docs for `wizard_profile` versus `wizard`: built-in profiles are centralized today in `src/nebius_cxcli/wizard_profiles.py`, and ordinary inputs with no guided choices should omit both fields.
- Removed the generic `vpc` wizard profile and replaced it with component-scoped public-access VM profiles so built-in `wizard_profile` names stay aligned with actual TF modules/components rather than a shared service-domain label.
- Tightened the `wizard_profile` contract to a one-to-one component mapping: built-in profile names now match infra component ids exactly, the loader rejects mismatched profile names, and the bundled catalog dropped no-op `shared_file_system` / `mysterybox` profiles instead of carrying empty shorthands.
- Applied the repo Python-project workflow baseline more explicitly: Make now exposes `test-unit`, `test-integration`, `coverage`, and `clean`, `pytest-cov` is available in the dev extras, and the default unit lane blocks live network access unless a test is explicitly marked `integration`.
- Fixed `provider_options.py` type-checker issues in the plugin loader and MK8s version option builder so static analysis no longer reports a callable-signature narrowing error or `OptionChoice` construction from `str | None`.
- Tightened the MK8s control-plane version option builder further to use a direct typed `OptionChoice` append loop, which avoids stale Pyright/Pylance inference complaints around the tuple-construction expression.
- Aligned provider-backed wizard resolution end to end: prompt-time choice loading now normalizes relative provider arg paths the same way strict validation does, `filter_regex` now constrains both displayed choices and manual-entry validation, and fallback warnings preserve resolver/plugin exception text when a provider lookup fails internally.
- Added a dedicated README reference section for `component_sources.yaml` covering the file structure, supported fields, reference syntax, strict-key behavior, and the only regex-capable catalog field (`wizard.<field>.options.filter_regex`).
- Fixed chained wizard/provider prompting for optional infra fields: provider-backed downstream prompts such as MK8s `gpu_nodes_preset` now wait until their `depends_on` selector has a real value, instead of falling back to a misleading manual-entry warning when the upstream platform field was skipped.
- Tightened the README/design docs so the current bundled component catalog is spelled out explicitly: which infra components use matching `wizard_profile` names, which ones rely on plain introspection, and why app components stay on explicit `wizard` only.
- Refreshed `docs/design.md` `Source Code Structure` and test-ownership sections so they now describe the current file layout more concretely, including `wizard_profiles.py`, `cluster_handoffs.py`, source-default/wiring helpers, provider-option ownership, generated-manifest/email-settings helpers, and the focused wizard/provider test modules.
- Clarified in the docs that `component_sources.yaml` `wizard.<field>.options` is the wiring layer between existing Terraform/Helm field paths and Nebius-backed dynamic option lookups, including the chained `depends_on` flow used for platform-to-preset selection.
- Removed the separate `resource_kind` catalog field and made `status.kind` the single canonical Nebius status-watcher contract for infra components; bundled catalog entries, parser validation, tests, and docs now all require the explicit `status.kind` path instead of supporting a shorthand fallback.
- Wired the bundled `mk8s` catalog `inputs.subnet_id` field to the live `project_subnets` provider so `create` now offers Nebius subnet choices for the selected project instead of falling back to a plain manual string prompt.
- Documented explicit developer prerequisites in the README for macOS/Homebrew and Linux/apt, including the core toolchain for `make venv` / `make all` and the optional external CLIs used by specific command paths.
- Reduced `make all` wall-clock time and local/CI timeout risk by reusing the repo `.venv` for the wheel build (`python -m build --wheel --no-isolation`) and running the wheel build in parallel with the lint/test gate after env setup; `make venv` now also upgrades `setuptools` explicitly so the shared environment keeps the required backend version.
- Removed the last name-inference and provider-resource compatibility paths from the source catalog flow: wizard-backed Nebius option lookups now come only from explicit `component_sources.yaml` metadata, infra render emits only source-backed Terraform modules, app source entries no longer accept `runtime`, and docs/tests/help were realigned to that single contract.
- Updated the generated customer GitHub workflow contract to support manual `workflow_dispatch`; manual runs now use `discover --all` for the configured deployments scope so customer repos can rerun plan/apply without relying on a fresh git diff.
- Removed the unused internal `ComponentEntry.origin` field and aligned the test suite with the current source-driven component model so tests no longer carry dead registry/provider-origin scaffolding.
- Refactored `component_sources.yaml` to a keyed `components.infra` / `components.apps` schema with `source.portable` / `source.local`, `wizard`, and infra `runtime.values` / `runtime.contracts`, removed the old `outputs` / `handoff` catalog contract, and aligned create/render/release-catalog/build helpers plus tests and docs to the new source model.
- Fixed component input binding resolution so it now follows the actual enabled source instance instead of assuming the component type id equals the runtime `instance_id`. Unqualified refs such as `mk8s.cluster_id` keep working when exactly one matching source instance is enabled, and catalog bindings can now disambiguate with `<component-id>@<instance-id>.<output-alias>` when multiple instances of the same type are enabled.
- Made Helm source-validation timeouts configurable with `NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS` and improved timeout diagnostics so `validate-sources` can be tuned for slow OCI registries instead of failing on a fixed opaque `helm` timeout.
- Fixed the repo Ruff gate so `make lint` and the `nebius-cxcli-ci` workflow now pass again: `cli.py` binds deferred module prompt expansion to the current component loop state, and runtime alias validation uses the simplified single-guard jump-host check expected by Ruff.
- Added regression coverage for the explicit wizard/provider wiring contract: undeclared fields do not trigger Nebius-backed option lookups, while declared `component_sources.yaml` `wizard` fields resolve provider-backed choices only through their configured metadata.
- Clarified the architecture docs to explain why `config.yaml` stays the operator contract while Terraform modules and Helm charts are the provisioning contracts, why the Nebius SDK is used as the dynamic integration layer instead of the primary infra reconciler, and why Terraform output aliases plus `handoff` aliases must be treated as a versioned interface once the CLI/runtime consume them.
- Fixed MK8s wizard field prompting so source-defined literal defaults such as `inputs.cpu_nodes_count: 2` remain editable, GPU-prefixed fields stay hidden until `gpu_enabled=true`, and optional provider-backed fields can now be left blank without falling into an invalid-value re-prompt loop.
- Made MK8s cluster handoff access dynamic instead of hardcoded: the bundled `mk8s` source now resolves `handoff.access` from `inputs.mk8s_cluster_public_endpoint`, so local `deploy` / `flux apply` / `flux bootstrap` / `destroy` / `flux destroy` select the public or private control-plane endpoint automatically. Private-endpoint runs now fail early with explicit network-reachability guidance instead of a generic later `kubectl` dead end.
- Added generated-bundle destroy paths: new top-level `destroy <generated-dir>` now deletes rendered app resources first and then runs Terraform destroy, continuing with infra teardown even when the rendered app delete step fails, and new `terraform destroy` / `flux destroy` commands expose the same destructive workflow in infra-only and apps-only form with explicit confirmation or `--yes`.
- Stopped `destroy` and `flux destroy` from updating `~/.kube/config`; they now use only a temporary kubeconfig for cluster handoff during rendered app teardown, while `deploy`, `flux apply`, and `flux bootstrap` keep the persistent local kubeconfig update behavior.
- Added regression coverage proving `publish-release.sh --prep` remains
  idempotent for unreleased versions: reruns for the same version now stay
  no-op once `Unreleased` is empty and the tag has not been created.
- Changed `publish-release.sh --prep` to fail before editing `CHANGELOG.md` if
  the target tag already exists locally or on `origin`, so duplicate release
  preparation for an already-published version stops immediately.
- Fixed source-checkout runtime version fallback for local release tagging when
  `setuptools-scm` is not installed: `nebius-cxcli.__version__` now derives
  from `git describe` before consulting a generated `_version.py`, so
  `publish-release.sh --publish` no longer rejects a fresh exact tag because of
  a stale local dev-version cache.
- Updated the repo CI and release workflows so they now run
  `validate-sources component_sources.yaml` after `make all`, ensuring the real
  portable component catalog, Terraform modules, and Helm chart sources are
  validated in automation instead of relying only on unit tests.
- Hardened `publish-release.sh` so `--prep` now requires a strictly clean worktree, including untracked files, and first-time pushes from a new local release branch automatically set `origin/<branch>` as upstream instead of failing with Git's "no upstream branch" error; `--publish` now fails before tagging if the target changelog section is missing or empty.
- Made `render` transactional: rerenders now build the replacement bundle under a hidden sibling staging directory and swap it into `generated/` only after the new Terraform/Flux/report bundle plus generated manifest are complete, so failed rerenders leave the current bundle intact.
- Clarified docs/help that rerender is now a transactional replace action rather than an eager reset, and documented the Flux-safe workflow: rerender locally, then commit/push one final watched-path snapshot instead of unbootstrapping Flux or publishing intermediate manifest-deletion commits.
- Clarified the `deploy` command contract so help/docs now explicitly say it is the local direct-apply path and does not run `flux bootstrap`; added workflow coverage that generated customer apply jobs use `flux bootstrap` rather than `deploy`.
- Removed the last render-time `generated/flux/flux-system` preservation path. `render` now fully resets `generated/` and deletes any stale legacy Flux bootstrap subtree instead of carrying it forward.
- Reworked email delivery to be disabled by default and operator-local: `nebius-cxcli email --setup` now manages `~/.config/nebius-cxcli/email.yaml`, `bootstrap-ci` syncs non-secret SMTP fields into GitHub Environment variables plus credentials into GitHub Environment secrets, and per-client send/no-send is now controlled by `client_info.notifications.email_enabled` in `config.yaml`.
- Tightened `email <generated-dir>` so it sends only the rendered
  `deploy-report.md`, fails fast when that file is missing, and masks
  tenant/project identifiers in the email subject/body down to their last 4
  characters.
- Changed the email contract so generated workflows always run the email step after apply and use `client_info.notifications.email_enabled` as the single send/no-send switch; when email is enabled but SMTP is not configured, the command now warns and continues instead of failing the deploy.
- Changed `bootstrap-ci` to reconcile GitHub SMTP settings from local `email --setup` on every run, including removal of stale `SMTP_*` environment variables/secrets when local SMTP is disabled; `--no-auth-bootstrap` now skips only Nebius CI auth bootstrap.
- Fixed `validate-sources` to accept an optional positional catalog path such as `nebius-cxcli validate-sources component_sources.yaml`, instead of requiring only the global `--component-sources-file` override.
- Split runtime and generated validation into explicit visible phases so long-running `validate` and `validate-generated` calls no longer go silent, and optimized portable validation to reuse resolvable local module metadata when available instead of probing every remote module source during catalog load.
- Clarified root CLI help/docs that `--source-profile` defaults to `portable`; local mode remains the explicit workstation override rather than the implicit test/CI path.
- Clarified `--help` target contracts so the first help screen now tells operators whether each command expects a deployments root directory, `config.yaml`, `generated/`, or an optional `component_sources.yaml` path.
- Clarified `discover` help/docs so they match the implementation: the command accepts the deployments root or any narrower directory under it, including one instance directory or `generated/`, and added CLI coverage for that scoped invocation.
- Fixed scoped `discover` resolution so `--all` and changed-only mode both work from narrower instance directories such as `generated/`, instead of only behaving correctly from the deployments root.
- Clarified top-level and `auth --help` command contracts so `validate-generated` is listed with the generated-bundle commands, `auth` is called out as a no-positional-path command, and `auth --validate-profile` now explicitly documents its all-cached-profiles mode when no project/config target is provided.
- Tightened repo-level Dependabot policy so `.github/dependabot.yml` remains responsible for creating GitHub Actions update PRs, while `.github/workflows/dependabot-auto-merge.yml` is the separate gate for auto-approval and auto-merge of eligible workflow-only GitHub Actions updates, including majors, using the dedicated `dependabot-automerge` environment credential.
- Replaced `azure/setup-kubectl` in generated customer workflows with a direct upstream `kubectl` install step, avoiding the GitHub Actions Node 20 deprecation path.
- Switched render-time Terraform lockfile generation to backend-disabled `terraform init -backend=false` and now remove transient `.terraform/` workdir state afterward, so canonical generated bundles no longer retain local Terraform runtime residue from render.
- Simplified generated customer workflows to rely on the generated-bundle CLI commands for `terraform.auto.tfvars.json` recreation instead of carrying a duplicate inline restore script, and now reconcile the deployments-root `.gitignore` during `bootstrap-ci` as well.
- Removed the unused generated deploy-report JSON sidecars (`infra.json`,
  `apps.json`, `mk8s.json`, `postgresql.json`, `sfs.json`); the generated
  deploy-report contract is now `deploy-report.md` only, and refreshes delete
  any stale legacy inventory JSON files.
- Fixed generated `deploy-report.md` spacing so section headers and lists remain
  markdownlint-safe, and clarified in docs that email recipients still come
  from `client_info.notifications.email` in the generated manifest/runtime
  config.
- Replaced the split `component_sources.yaml` and `component_sources.release.yaml` model with a single dual-source `component_sources.yaml` schema using required `portable_source` plus optional `local_source` per Terraform module.
- Replaced command-local `--render-profile` with the global `--source-profile {portable|local}` override and added `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE` for workstation-vs-portable source selection across config-based commands.
- Aligned wheel/release packaging and repo workflows with the single-catalog contract, and hardened release-catalog verification so published portable catalogs reject local filesystem `portable_source` entries.
- Removed recently redundant compatibility branches: generated manifests now require `render.module_sources`, the duplicate manifest `render.portable` flag is gone, app release-name aliases are no longer accepted, and seeded infra project defaults now only honor canonical `parent_id` / `project_id` input keys.

## [nebius-cxcli-v0.1.8] - 2026-03-23

- Fixed the `nebius-cxcli` CI and release workflows to run `nebius_cxcli.release_catalog` checks with the repo `.venv/bin/python` created by `make all`, avoiding bare-runner Python import failures under GitHub Actions.
- Hardened `tests/test_setup_build.py` against ambient GitHub Actions build env leakage so setup/build source-selection and release-ref rewrite tests stay deterministic in CI.

## [nebius-cxcli-v0.1.7] - 2026-03-23

- Removed the standalone `nebius` CLI dependency from MK8s kubeconfig handoff and token retrieval; `deploy`, `flux apply`, `flux bootstrap`, and generated customer workflows now use Nebius SDK-backed exec kubeconfig entries through `nebius-cxcli` itself.
- Generated customer workflows no longer install the standalone `nebius` CLI before Flux bootstrap.
- Aligned the main `nebius-cxcli` CI and release workflows to run the same local `make all` verification contract before wheel verification and release publication.
- Aligned CLI help/doc wording for auth profile/config flags and MK8s handoff behavior with the SDK-based contract.
- Tightened `bootstrap-ci` help/docs so the command and flag contract explicitly matches runtime behavior: target `config.yaml` must already be inside the customer git repo, `--github-repo` is only an auth-bootstrap override, and `--github-token-env` only affects GitHub bootstrap/secrets sync.
- Clarified in help/docs that `--cli-ref` selects the `nebius-cxcli` source ref used by the generated customer workflow, not the branch of the customer target repo; kept the option display aligned with Typer's default `TEXT` metavar.
- Fixed runtime version resolution for source/editable checkouts so `nebius-cxcli` now prefers live `setuptools-scm` git state over a generated `_version.py` cache, and `publish-release.sh --publish` now verifies local runtime version/tag alignment before pushing the release tag.
- Clarified MK8s node-readiness behavior before Flux work: `deploy`, `flux apply`, and `flux bootstrap` now probe first and only announce a wait when nodes are actually not `Ready` yet.
- Kept the local Flux phase under one continuous spinner after MK8s handoff so `deploy`/`flux apply` no longer stop and restart the spinner between cluster reachability, Flux API discovery, manifest apply, and rendered-resource readiness checks.
- Added a non-interactive fallback for those Flux phase updates so GitHub Actions and other non-TTY logs get stable printed phase lines instead of relying on transient spinner frames.

## [nebius-cxcli-v0.1.6] - 2026-03-23

- Simplified `bootstrap-ci` so reruns automatically reconcile the CLI-managed customer workflow to the latest generated contract; `--auth-bootstrap` remains enabled by default and workflow-only runs are now the explicit opt-out via `--no-auth-bootstrap`.
- Added regression coverage that `bootstrap-ci --help` and the command surface keep `--auth-bootstrap` enabled by default.
- Fixed customer-side Terraform plan/apply flows for private repos by persisting rendered tfvars in the generated manifest and recreating ignored `generated/infra/terraform.auto.tfvars.json` from that manifest before Terraform runs, both in CLI-generated bundle commands and generated customer workflows.
- Clarified and tested that `deploy <generated-dir>` remains a local/customer-side bundle operation only and does not auto-run `bootstrap-ci` or mutate GitHub CI workflow/environment state.

## [nebius-cxcli-v0.1.5] - 2026-03-22

- Added PR-side coverage for `bootstrap-ci` workflow generation across both development (`main`) and stable tagged (`nebius-cxcli-v<version>`) default CLI refs.
- Hardened `bootstrap-ci` to fail before writing the customer workflow when GitHub auth-bootstrap prerequisites are missing, and documented `--github-repo` as an override over target-repo auto-detection.
- Added explicit render profiles: generator-side `validate` and `render` now default to portable output, while `--render-profile local-dev` keeps checked-out Terraform module paths for workstation testing.
- Hardened generated-bundle validation and customer workflows with `validate-generated --portable`, so PR/apply pipelines reject non-portable local Terraform module sources before plan/apply.
- Simplified wheel/release packaging to bundle the portable catalog via the build override path instead of rewriting the working-tree root catalog during GitHub Actions builds.
- Aligned the generated customer workflow with the example repo by using a shared Python-version env and compact JSON discovery output for deterministic GitHub Actions matrix handoff.
- Added repo-side coverage that the checked-in local and portable catalogs stay semantically aligned except for Terraform module source addresses.
- Added direct tests for the `validate-sources` CLI command surface and GitHub environment-secret bootstrap helpers so those paths no longer rely only on indirect coverage.

## [nebius-cxcli-v0.1.4] - 2026-03-22

- Fixed packaged/bundled `component_sources.yaml` to always use the portable Git-backed catalog so source installs and customer CI no longer fall back to repo-local Terraform module paths.
- Added `bootstrap-ci --cli-ref` so generated customer workflows can be pinned explicitly to a branch, tag, or commit when validating nebius-cxcli changes end to end.
- Stabilized Flux bootstrap fallback coverage so tests no longer depend on live local `kubectl` state when asserting the bootstrap path.

## [nebius-cxcli-v0.1.3] - 2026-03-21

- Hardened release publishing so tagged wheels use the exact tag version and verify bundled portable component sources through shared release-catalog helpers.
- Limited release catalog ref rewriting to this monorepo's module sources and now fail release validation when external module sources are left on floating refs or local paths.
- Added PR-side coverage for release catalog rendering and wheel verification so release packaging errors are caught before tagging.
- Fixed `publish-release.sh --prep` changelog rewriting so moved release notes preserve Markdownlint-safe blank lines around lists and headings.

## [nebius-cxcli-v0.1.2] - 2026-03-20

- Prepare release `v0.1.2`.

## [nebius-cxcli-v0.1.1] - 2026-03-20

- Split the workflow model into generator-side commands for `config.yaml` and customer-side commands for deploying the rendered `generated/` artifacts.
- Added generated bundle manifests, stricter render reset guardrails, and customer-side validation for portable deployment bundles.
- Hardened local deploy and Flux apply/bootstrap flows with better readiness checks, clearer status output, and safer Flux recovery behavior.
- Aligned release packaging and GitHub workflows so published wheels bundle the rewritten portable release catalog instead of local development sources.

## [nebius-cxcli-v0.1.0] - 2026-02-22

- Initial scaffold for `nebius-cxcli`.
- Added `config.yaml` schema validation and deterministic renderers.
- Added Terraform, Flux, discover, inventory, and email commands.
