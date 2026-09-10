# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Match ExternalSecret readiness by its exact Kubernetes API group and verify
  removed install options in both colored and plain CLI output.
- Reject FIFO/device inputs for Soperator values and SSSD runtime files before
  reading, and reject malformed feature enablement or disabled required checks
  during input validation.
- Add fresh Soperator install `--values-file`, wizard prefill and saved explicit
  value provenance, preserving false, default-equal and list choices through
  normalization and upgrades while retaining generic catalog separation.
- Complete conditional backup destination, schedule, retention and Secret-reference
  inputs; route them through upstream `backup.config.values` and validate frozen
  child chart renders before installation.
- Complete shared SSSD Secret/optional CA references and runtime file delivery.
  Resolve runtime namespaces from rendered consumers, require explicit target
  context and lifecycle authority for each create, reuse complete objects and
  reject incomplete objects without overwriting credentials. Runtime paths and
  contents remain outside saved configuration and receipts.

- Separate Soperator maintenance, fresh diagnostic acceptance and final user
  admission. Preserve all existing job-policy/TUI choices, pause reviewed passive
  diagnostics only after isolation, and keep unsupported passive behavior at its
  verified desired policy with a warning. Required bootstrap and operational
  hooks remain active.
- Keep ordinary scheduling closed while native acceptance and recurring schedule
  restoration complete. Journal partition/hold ownership and reservation release
  separately, preserve original closed partitions and ACLs, and resume interrupted
  handoff without releasing unrelated holds or recreating maintenance.
- Bind passive acceptance to fresh native logs, worker identity and the desired
  rendered configuration, with bounded transport and incremental private worker
  receipts. New checks receipts require the matching operation executable.
- Require positive native GPU-health, boot-disk and memory measurements for
  applicable passive acceptance. Freeze GPU-busy and optional NVMe proof as
  supporting-only, report their limitations, and prevent native completion or
  skipped discovery from becoming measured PASS evidence. Bound and recheck
  child-log reads. Align wizard and upgrade-plan guidance with phase-based
  diagnostic suppression and the selected job policy.
- Respect the desired Slurm health-check node-state selector during passive
  acceptance, recording inapplicable periodic runs without inventing PASS or
  skipping native job-hook evidence. Preserve partial baseline progress across
  busy workers and resume without requiring a cluster-wide quiet sweep.

- Keep parent-owned graph-validation selection in the campaign module, preserving
  its ownership checks while satisfying the CLI architecture gate.

- Update lifecycle validation documentation with the completed managed install
  recovery and full-stack upgrade, retaining the clean-install and scale limits.

- Bind final upgrade check-policy readiness and catch-up recovery to durable
  campaign main-workload authority, fixing a wait that could time out despite
  the exact Slurm HelmRelease already being Ready.
- Resume interrupted final check-policy stages before full-graph readiness,
  prove source adoption after target writers resume, retain campaign authority
  evidence, and keep fresh diagnostics after
  runtime validation. Accepted restoration resumes reuse verified acceptance
  without recreating maintenance reservations or diagnostic jobs.

- Verify effective diagnostic deferral before install infrastructure restoration
  and between upgrade segments, including CronJobs and in-flight Kubernetes and
  Slurm work. Preserve verified bootstrap and owned acceptance recovery; require
  exact cron expressions and time zones when restoring desired schedules.
- Retain Nebius provider node-group IDs in GPU inventory and use those exact IDs
  for campaign validation, independently of shared logical Kubernetes labels.
- Fetch cold OCI chart caches by the frozen manifest digest and verify both the
  manifest and package digests, so moved version tags cannot replace approved charts.

- Keep complete Soperator graph validation in the parent upgrade after frozen
  source refresh. Managed node-template children retain node, GPU and other
  deployment checks without deadlocking on suspended HelmChart artifacts lost
  during system-node replacement. Standalone required checks remain mandatory.

- Preserve Terraform caches and local state across renders and exclude them from
  generated-configuration authority. Upgrade validation can initialize Terraform
  without invalidating its applied generation; configuration and the provider
  dependency lock remain fingerprinted. Interrupted commits still require exact
  transaction recovery, while applied stages require their recorded postimage
  through the same check at campaign admission and per-stage recovery.

- Resolve protected local SFS through retained PV/PVC bindings and exact Nebius
  node-group attachments for both managed and onboarded upgrades. Declaring SFS
  in configuration no longer incorrectly requires CSI volume handles. Match
  configured mount tags exactly instead of assuming they equal protected role
  names; reject duplicate or missing tag assignments.

- Retain approved Soperator release snapshots by digest and pass the campaign's
  frozen identity to checks and release execution. Resume no longer re-resolves
  chart tags after discovery cache expiry; missing or altered content fails
  before maintenance without changing the approval receipt.

- Preserve native check lifecycle and full-stack source, target and catch-up receipts as lifecycle
  evidence during rendering. Their creation or progress cannot change the
  admitted generated-configuration fingerprint or block maintenance entry or
  final release readiness.

- Initialize upgrade checks after creating the durable campaign receipt and
  bind source-check compilation to the rendered Slurm cluster name. Preserve
  the original initialization failure in resumable supervisor reporting.

- Resolve a managed Soperator upgrade's immutable cluster ID from its exact
  generated Terraform output before preparing renewable Kubernetes credentials.
  Read the existing backend without initialization; external targets retain
  their registered identity and never read Terraform state.

- Capture Slurm reservation identity with standard UTC timestamps and complete
  canonical fields. Reject incomplete check-reservation times before accepting
  ownership; preserve exact preimages for upgrade recovery.

- Validate Soperator deployments through the authoritative upstream release graph.
  Replace legacy jail-object smoke lookups with the same exact storage, workload
  and required-check readiness used by reconciliation; retain canonical reports
  and show pending readiness progress without validation-side recovery mutations.

- Release the verified diagnostic reservation before restoring recurring upstream
  checks during install and upgrade. Close temporary diagnostic authorization
  first, retain the independent admission barrier and existing customer job
  holds until the final policy handoff, and journal reservation release
  intent/completion for interruption recovery. Recover proven native catch-up
  submission failures through the staged workflow and fresh checks, preserving
  failed evidence. Recognize a retained Flux retry only after the exact admitted
  checks writer has acknowledged suspension and completed native rollback, with
  independent source artifact verification. Report policy restoration stages
  during resume.

- Keep terminal native scheduled-check history from blocking Soperator service
  readiness after successful recovery. Validate the complete Kubernetes owner
  chain and matching Helm-owned ActiveCheck; retain required check-result gates
  and strict readiness for service Pods and unproven Job ownership.
  Bind the auxiliary extensive-check workload to its cluster's existing Slurm
  ConfigMap and munge Secret in staged and steady rendering. Recover only a
  proven never-started stale auxiliary Job through a journaled, UID-bound native
  deadline after writer suspension; preserve accepted checks and frozen inputs.

- Require actual native health-checker PASS reports and validated NCCL results
  before accepting Slurm diagnostics, including output revalidation before maintenance release. After release, verify
  the bound diagnostic verdict/digest plus live Job and accounting evidence so
  upstream local-log retention cannot invalidate a completed acceptance.
  Accept the native CUDA sample report with null subchecks only when all four
  exact sample commands succeeded and the overall report is PASS.
  Correct the managed eight-H200, 128-vCPU worker topology without converting
  its Kubernetes CPU-time quota into a physical CPU mask. Preserve the quota,
  retain native affinity failures and replay fresh acceptance through sealed
  values repairs. Verify reservation resource totals during physical topology
  repair; removing a generated CPU mask preserves the complete reservation.

- Bind GPU workers to the upstream Docker Supervisor configuration and daemon
  settings, with a pod-local disposable image cache under the existing storage
  allowance. Recover an initial native Docker NCCL missing-socket failure through
  an exact sealed values repair, retaining failed jobs and the original
  reservation and requiring fresh upstream checks.
  Recover the exact drains left by those failed checks after proving native
  Docker readiness. Quiesce only an attributable queued bootstrap probe and
  resume its native Job after drain restoration and check authorization;
  preserve failed history and reject unrelated drains or workloads.

- Declare an Enroot-specific AppArmor user-namespace profile through the
  upstream Security Profiles Operator. Wait for installation on all ready
  nodes before opening the Slurm workload, preserving the host-wide
  restriction. Recover a proven initial Enroot namespace denial through a
  sealed additive adapter repair and fresh acceptance under the original
  reservation, retaining the failed job and its accounting.

- Correct the GPU wizard `ephemeralStorage` default from 10Gi to 55Gi, matching the
  pinned upstream GPU examples. Fill missing GPU allowances while preserving explicit settings.
  Recover an initial native image import interrupted by a proven worker
  storage eviction through an exact sealed values repair. Retain its failed
  accounting and require fresh checks under the original reservation. Bind
  durable kubelet termination and eviction cursors to the node boot identity
  and the native passive checker's over-limit storage report instead of relying
  on short-lived Kubernetes Events.

- Pin native acceptance jobs with supported Slurm allocation directives and
  bind their projected script contents to executable identity. Preserve the
  upstream diagnostic body and entrypoint. Retain exact terminal submissions
  made with unsupported worker-selection variables, then require fresh jobs
  with independently verified worker and GPU coverage on resume.

- Materialize upstream runtime script mounts and the node-local job metrics
  directory for both installed and registered NodeSets. Verify the normalized
  Pod mount paths emitted by the upstream renderer and the target-file permissions
  of projected ConfigMap links. Recover an exact initial
  acceptance blocked by missing mounts through a sealed values repair, native
  probe quiescence and verified restoration of only its attributable prolog
  drains. Preserve failed evidence and require fresh checks after replay.

- Match the upstream `<cluster>-munge` Secret in native check verification while
  retaining the distinct `munge-key` volume name, exact key paths and permissions.

- Include check reservation binding and initial partition restoration in both
  normal execution and interrupted recovery, and verify both before accepting
  the combined restoration step. Repair the exact missing-GPU install frontier
  with a sealed successor that retains the original reservation and failed
  history, changes only absent `Gres` counts, and replays from declarative apply.
  Authenticate both full-values policy identities through that sealed delta
  before carrying reservation ownership into the successor.

- Validate unlimited check reservations using Slurm's native 365-day duration
  output, preserving active-state, resource coverage, authorization and
  fingerprint checks.

- Bind the upstream jail-log collector to the approved system placement and
  active jail storage, including slot switches. Preserve upstream configuration
  and readiness, and support an exact pre-acceptance install repair with native
  HelmChart identity and uninstall verification.
  Recover a healthy corrected collector stalled without a rollback target using
  one source-bound native retry per configuration; preserve waits and stop on
  another failure.

- Bind native bootstrap check login commands to the declared cluster Service
  using exact verified upstream script bytes. Reject arbitrary command
  changes, and admit interrupted installs through a sealed two-file repair
  that preserves source, storage and earlier repair evidence.
  Recognize the recorded failed-apply checkpoint as well as an interrupted
  apply, retaining exact pending-intent and pre-acceptance checks.

- Keep the orchestration-only Soperator umbrella from waiting on suspended
  child releases during install and upgrade; retain child waits, hooks and all
  staged acceptance gates. Resolve checks handoff through the same rendered
  umbrella identity and require acknowledged source-writer suspension.

- Populate missing Slurm GPU counts even when worker CPU topology already fits.
  Reject changes to existing IAM permit settings during failed-install recovery.

- Require the upstream Slurm REST service for configuration reconciliation,
  including its existing JWT startup gate and OpenMetrics handling. Repair an
  omitted dependency during interrupted install through a sealed successor
  that preserves all worker, storage, source and check inputs.

- Recognize completed uninstall remediation as quiescent for the exact admitted
  checks-install repair, even when Flux retains a queued retry condition.

- Bind native Soperator checks and the reservation CronJob to the active jail
  PVC; suspend and verify restoration of the auxiliary schedule across install
  and upgrade. Resume interrupted first installs through a sealed render repair
  and exact old wait-hook deadline, preserving source, storage and failed history.

- Preserve the frozen absent source on install resume after the target main
  release appears, instead of inferring a no-op from live discovery.

- Apply temporary Soperator check policy to the umbrella HelmRelease inline
  values that Helm consumes, as well as the matching values record. Reject
  missing, ambiguous, or drifted umbrella values before mutation.

- Extend the upstream dashboard source adapter to the pinned Soperator 4.1.5
  chart. Interrupted installs can resume its exact pre-main separator failure
  with cluster-bound repair evidence, preserved compiled values and failed
  history, and a fenced successor operation.

- Use the supported project `editor` role for upstream observability ingestion
  instead of assigning permission names as IAM roles. Failed install replanning
  refreshes only Terraform root wiring with frozen-input checks and may replace
  only uncreated grants for retained groups; existing resources remain protected.
  Recovery compares desired values using provider schemas so normal status
  changes after node creation do not prevent resuming a partial apply.

- Keep the Soperator strategy guard inside its release branch so ordinary Flux
  app deployment remains independent of Soperator lifecycle policy.

- Require canonical project admin permissions for Terraform-owned IAM and MK8s
  service-account attachment. Cached commands verify permissions read-only;
  explicit targeted `auth` reconciles managed project roles without rotating a
  healthy key or granting tenant access.
- Recover failed Soperator infrastructure applies through `install --resume
  --dry-run --replan`. Preserve failed evidence, infrastructure and shared-group addresses,
  known identities, and frozen inputs; reject destructive changes and require
  a new approval fingerprint before resuming.

- Include exact renderer-owned Soperator observability IAM instances in the
  install plan ownership check. Reject unrelated accounts and resource names,
  destructive actions, and fresh IAM-only plans without infrastructure changes.

- Repair the MK8s service-account output used by Soperator observability IAM:
  account keys remain known in the first Terraform plan while IDs resolve at
  apply time. No targeted bootstrap apply is needed.

- Normalize Object Storage lease metadata header casing so Soperator install
  retains valid ownership on Nebius responses. Reject ambiguous duplicate keys
  and keep exact ETag, owner, expiry, and cluster-binding checks. Suppress the
  generic deploy hint during install's internal render step.

- Fix fresh Soperator rendering before Terraform state exists and resolve the
  managed cluster output without duplicating its target prefix. Restore the
  required fabric prompt when regional capacity advice is missing, and report
  absent regional coverage as unknown instead of confirmed zero capacity.

- Keep upstream Soperator checks enabled while deferring scheduling-dependent
  diagnostics during install and the full upgrade campaign. Run fresh native
  acceptance on every required worker under retained maintenance, verify all
  required GPU allocations and executable/script identity, and restore schedules
  and authorization before customer handoff. Freeze policy changes in upgrade
  plans, including equal-release campaigns, reject unsupported execution knobs,
  and preserve exact submission/restoration evidence across interruption.

- Align the install wizard with upstream Soperator ownership: show infrastructure,
  dedicated upstream Soperator settings, and required platform/integration
  components. Remove optional-app questions, generic observability prompts, and
  `soperator install --app`; optional apps use `component add` afterward. Preserve
  placement and storage mappings, with separate autoscaling and ephemeral-worker
  choices. Require explicit
  collector selection on each Soperator target, keep Grafana independent, retain
  protected ownership during ordinary dependency filtering, and report ordinary
  telemetry signals from effective collector configuration. Saved install/resume
  state and existing upstream observability remain unchanged.

- Fix ordinary app reapplication when namespaces already exist: remove their
  matching Kustomization references from the private apply snapshot while
  preserving the saved generated bundle and live namespaces.
- Use public Nebius Grafana by default for Soperator installs. Local
  Grafana plus Gateway and the additional app telemetry agent are independent
  optional apps selected afterward through `component add`.
  Ordinary catalog apps use the existing component, render, deploy, Flux apply,
  and Helm upgrade commands on Soperator MK8s targets. Publish and apply only
  ordinary app resources, preserve protected infrastructure and the upstream
  graph, verify target identity and resource ownership, and fence concurrent
  lifecycle operations. Preserve ordinary app generations through upgrade and
  recovery; omission does not uninstall live releases. Render the upstream
  logging endpoint for the selected region and retain cxcli Grafana dashboards.

- Refactor `soperator install` to use the shared typed project-creation workflow
  directly, fixing the `Invalid --app-version value '4'` failure. Select MK8s,
  SFS, and Soperator automatically while preserving their full field wizards
  and the one frozen upstream release. Apply the worker profile before
  materializing topology, reconcile GPU helpers after field changes, and keep
  cert-manager exclusively upstream-owned. Report release, authentication,
  service-account, provider, render, and plan progress using the same terminal
  presentation as upgrade, with stable stderr records when redirected. Keep
  cached lookups quiet and report failed validation or provider requests as
  failures even when the wizard handles their errors.
- Read supported Nebius subnet status pools without SDK deprecation tracebacks,
  preserving explicit versus inherited subnet ownership. Reuse complete project
  platform inventories for preset metadata, and propagate CPU/GPU selections
  to managed node groups before refreshing disk recommendations. This avoids
  repeated failed requests for unavailable prior defaults without replacing
  explicit selections or caching failed inventory reads.
- Align workflow regression tests with the existing pinned setup-uv v10.0.1
  release and download-artifact v8 action.
- Remove the downstream Soperator Helm chart family, its verifier workflow, and
  its publication entry. Soperator is no longer declared in the generic source
  or CLI-settings catalogs; the six lifecycle commands now combine a dedicated
  packaged install-policy contract with exact official `nebius/soperator` GitHub
  and OCI release authority. Remove the three downstream-only QoS/preemption
  profiles and reject `qosConfiguration` and `schedulingConfig` without a
  compatibility path. Release and wheel verification also reject any
  reintroduced Soperator entry in either bundled generic catalog, and
  credential-bearing GitHub API requests reject redirects outside the official
  API authority before following them.
- Replace the node-shaped `soperator discover` output with one bounded customer
  summary and one complete support-safe schema-v2 JSON inventory. Screen output
  and `report.md` now show fixed component rows plus one node-group row with
  Ready/Actual/Target counts, while `report.json` retains every in-scope node,
  provider group, Soperator/GPU component, storage/topology/health record, and
  explicit collection-lane outcome. Exact ID-first group correlation,
  deterministic mixed-value summaries, dedicated public allowlists, and
  serialized Markdown-first/JSON-last pair publication prevent guessed
  attribution, data leakage, unbounded terminal output, and mixed concurrent
  report pairs.

- Normalize cross-version Slurm `AllocNodes=ALL` state during authenticated
  partition restoration so upgraded controllers continue accepting jobs from
  login nodes even when the visible partition record is unchanged.
- Normalize output-only unlimited partition-memory sentinels to Slurm's
  explicit numeric-zero representation so Slurm 25.11 does not treat them as
  unsatisfiable per-CPU requests or inherit a finite cluster-wide default.

- Make Soperator upgrade final validation compatible with fully allocated GPU
  workers and the canonical Soperator report contract. A saturated target now
  proves `nvidia-smi` and CUDA Driver API initialization inside the exact Ready
  Slurm worker pod, while canonical smoke reports are archived under a unique
  attempt identity before campaign evidence is accepted, including a fresh
  failed report written before its validator raises.
- Isolate each Soperator release reconciliation attempt from its caller-owned
  source payload. Full-stack supervisor retries now recompute admission from
  the same frozen input instead of inheriting rootfs-adoption and chart-version
  mutations from a prior attempt. Recovery also preserves the sealed legacy
  rollback PVC authority after the active rootfs has switched to a slot, so an
  exact checkpoint replay no longer falsely pauses on config-generation drift.
- Make uv the single dependency and environment authority for repository
  development and the cxcli CI/release workflows. `make env` now rejects stale
  lock state and unsafe, whitespace-containing, or unrecognized VENV paths
  before uv runs, serializes exact locked synchronization, disables automatic
  Python downloads, and runs every Python-backed target through locked uv
  execution. Wheel builds constrain isolated PEP 517 dependencies with hashes
  exported from the same lock and install the one exact artifact into a
  temporary uv environment for CLI and dependency verification; the former
  pip/stamp/checker path and public `dev` extra are retired.
- Materialize one canonical active/passive Jail rootfs source before Soperator
  release execution. An omitted `adoption.activeSource` on an already
  `activePassive` installation now remains slot-backed, so a populated inactive
  slot uses the exact write-ahead recycle journal instead of being
  misclassified as a fresh legacy-adoption PVC and retried indefinitely.
- Give `soperator upgrade` one terminal-owned progress surface across release,
  provider, compatibility, writer-authority, Slurm-maintenance, Flux, and
  readiness phases. The frozen Kubernetes plan now spells out adjacent hops
  from the observed source minor, and provider rows are labeled as target
  compatibility instead of looking like upgrade transitions. After the plan,
  execution visibly reports cluster-Lease acquisition, bounded expired-writer
  quiescence, partition/job maintenance, reservation setup, and barrier
  convergence. A spinner is replaced
  in the same status column by a green check on success, while non-TTY progress
  is stable and stderr-only with capped, deduplicated `INFO` milestones. Flux
  and campaign completion rows are committed to terminal scrollback with their
  elapsed time while the live surface retains only the active phase; supervisor
  retry waits remain active spinner rows with elapsed time instead of becoming
  static notices, and the direct-upstream release plan is printed only once per
  invocation. Flux
  maintenance progress pauses while a nested Slurm table, dashboard, or prompt
  owns the terminal, then resumes without overlapping live renderers. Flux
  apply, rollout, and migration subprocess
  chatter is captured and summarized by disposition, controller count, and
  resource kind; bounded sanitized failures retain authoritative command
  status. Campaign compatibility output now uses one row per node group with
  the complete per-hop OS/driver path and ready/target count instead of scaling
  terminal output with nodes or duplicate hop rows.
- Regenerate Soperator upgrade kubeconfig handoffs from the immutable cluster
  identity with the current Python module and renewable exec authentication
  instead of copying a context that may contain a stale checkout-local launcher.
  If an external edit removes the running cxcli environment anyway, the forward
  supervisor now exits with a resumable local-runtime error rather than retrying
  Kubernetes authentication forever; the same approved command reloads the
  existing campaign receipt and continues from its earliest unproved boundary.
- Remove the redundant `soperator upgrade --allow-provider-api-upgrade` and
  `--zero-size-gpu-validation` options. The approved campaign now derives its
  single Terraform or provider-API backend from proven target ownership, while
  retaining the internal authority and zero-policy fields in v3 receipts for
  exact recovery. Fresh campaigns update zero-capacity groups and verify two
  stable desired-template observations automatically; readiness follows live
  capacity changes, desired-positive GPU groups still require scoped CUDA
  validation, and GPU operator readiness remains required. Provider `auto`
  selection now resolves the latest OS and then the latest compatible Nebius
  drivers preset independently per node group and Kubernetes hop, prints every
  frozen API tuple, and safety-pauses if that tuple disappears immediately
  before its individual group mutation. Global GPU selectors ignore driverless
  groups, per-group driverless overrides and ambiguous aliases fail, and
  provider inventory ordering is canonical. Final readiness now refreshes and
  proves the Flux graph before runtime checks, requires one non-skipped scoped
  CUDA report per active GPU group, then re-proves the graph and unchanged all-
  group capacity/resource identity. Attempt-unique validation reports are bound
  by SHA-256. Completed campaign evidence remains visible in status, while
  failed final-only reproofs retain last-known-good evidence and surface their
  own recovery lifecycle.
- Make `soperator upgrade` show its fixed `pause-all-active` Partition Policy
  before a structured Job Policy selector and use guarded
  `requeue-hold-all` as the universal omitted default. Requeue and hold only
  identity-stable eligible batch jobs, wait for completing or unproven jobs,
  and reserve per-job human decisions for explicit `interactive` mode. Close
  the scheduling barrier after reservation creation, journal exact hold intent,
  applied state, and tombstones, then release exact operation holds while
  partitions remain paused, remove the operation reservation, and restore
  partitions last. Re-prove the operation lease immediately before every
  individual partition mutation so authority loss cannot spill into later
  partitions. Official chart pulls now retry three isolated times only for
  transient transport failures; authentication, certificate, not-found,
  digest, and identity failures remain immediate, and exhausted public errors
  omit raw transport cause chains.
- Identify every cxcli-owned Nebius SDK client with the versioned
  `nebius-cxcli/<runtime-version>` user-agent prefix, removing the SDK's
  repeated future-mandatory constructor warnings.
- Restore the Nebius SDK's native event-loop and shutdown ownership instead of
  lending it an application-owned loop. This removes the competing task drain,
  background reaper, and recovered-error log filter that could cancel SDK 0.6.4
  runtime finalization and emit repeated `CancelledError` tracebacks during
  read-only Soperator discovery. Native shutdown failures remain visible, and
  synchronous SDK helpers retain the SDK's fail-fast async-context contract.
  The project now requires SDK 0.6.4 or newer within the supported 0.x series.
- Repurpose `soperator discover` as a config-independent, information-only
  pre-onboarding command with parser-required tenant/project/cluster identity,
  optional verified region/context/access, and atomic JSON/Markdown reports.
  Align the six-command help order, paired interactive flags, existing-config
  onboard validation, region semantics, documentation, tests, installed-wheel
  smoke, and the v4 CLI contract with explicit option order and conditional
  requirements. Keep onboarding discovery evidence internal and independently
  collected; public driver presets, images, chart metadata, and labels are not
  reported as runtime GPU-driver or CUDA proof. Public discovery now converts
  ambiguous lifecycle identity, failed storage inventory, and unavailable
  Slurm health probes into a printed and written partial report while onboarding
  remains fail-closed. The exact saved Markdown projection is rendered on
  screen for complete and incomplete outcomes; terminal, bidi/format, line
  separator, and Markdown control characters from live-derived values are
  visibly escaped before persistence or display. Shared Markdown contains only
  the relative artifact path rather than the operator's absolute local path. Deployments-root
  onboarding also keeps an omitted region live-derived across interactive use
  and failed scaffold retries through an owner-only, config-hash-bound bootstrap
  marker instead of treating the temporary default as operator intent. The
  marker and initial config are published under the config lock with
  create-only semantics, then re-proved after discovery and under that lock;
  established configured and explicit region assertions must agree
  independently. Public discovery uses the schema-v2 bounded-summary and
  complete-inventory contract described above.
- Retire the ownerless downstream `soperator-upstream-verifier` workflow with
  its local chart, lock, and synchronization inputs. The surviving cxcli CI and
  release workflows now share the canonical quality and exact-wheel contract,
  explicitly provision Helm, and keep official upstream Soperator artifacts as
  the single supported delivery path.
- Make full-stack completion prove the retained Flux release graph, not only
  healthy live workloads. Completed/no-op declarative-release replay and final
  readiness now refresh every frozen digest-bound source before their graph
  waits, so controller restarts cannot leave stale-positive artifact status;
  final readiness resolves the selected target's Flux directory, re-freezes
  HelmChart artifacts, and waits for all graph members to acknowledge their
  current generation as Ready. An exact replay of a
  completed campaign reruns only this final postcondition without reopening
  maintenance or repeating provider mutations.
- Recognize the provider-native `nebius.com/node-group-id` label when final GPU
  inventory validates an onboarded cluster, while keeping the rendered
  `nebius.com/node-group` name authoritative when both labels exist.
- Preserve generated runtime inventory, deploy-smoke, acceptance, benchmark,
  GPU-stack, and CUDA-visibility reports as lifecycle evidence outside the render-owned
  project snapshot so their creation cannot safety-pause upgrade recovery.
- Align final Soperator smoke with the official 4.1.7 graph: validate the ready
  `sconfigcontroller` deployment and the adapter-owned read-only GPU driver-root
  mount, leaving CUDA/NVML library and device proof to the runtime Jail check.
- Fix interrupted protected Soperator recovery after the target release has
  adopted its prepared jail-rootfs slot. Pre-apply recovery still requires the
  passive PVC to be empty and unconsumed, while post-apply recovery verifies
  the sealed materialization receipt and exact completed Jobs instead of
  incorrectly applying the pre-write consumer gate to the now-active PVC.
- Align the Soperator README and design contract with the current code: use
  registration v3 and destroy receipt v2, keep hardware replacement exclusively
  under `migrate node-group`, document disabled and partial lifecycle-marker
  guards, state the real Helm/kubectl requirements, replace the drifting manual
  test list with the complete Soperator lane, and distinguish current onboarded
  live evidence from the pending cxcli-managed/Terraform live trial. Refresh
  both Soperator SVGs to show the full-stack parent campaign, exclusive backend
  authority, content-free target-wins rootfs transition, current protected
  storage variants, and the separate non-gating observability status action.
- Make `soperator upgrade --to-release latest|X.Y.Z` a durable full-stack
  campaign. Its wizard dynamically queries Nebius for the highest reachable
  Kubernetes endpoint, freezes sequential minor hops and per-group OS/GPU
  driver compatibility, keeps whole-campaign Slurm maintenance, upgrades the
  official release plus MK8s and Jail CUDA, and requires final provider and
  generated workload readiness. Maintenance entry is event-journaled before
  each Slurm mutation, and one-minor-lagging node groups are caught up before
  later control-plane hops. Managed targets keep Terraform authoritative;
  onboarded targets require explicit provider-API upgrade authority. Freeze the
  exact ownership/backend digest with no fallback on recovery, supervise the
  whole parent campaign instead of nesting child release retry ownership,
  journal complete config-plus-generated generations, and project the parent
  campaign through `soperator status`. Reservation recovery now recreates a
  missing reservation only from durable operation intent and rejects a live
  same-name reservation that the campaign did not record. Recovery also binds
  the initial render-owned project snapshot and exact provider node-group ID
  set, control-plane readiness requires stable actual-status convergence, and
  zero-sized groups retain stable desired-template verification even when live
  GPU/CUDA workload proof is explicitly waived. Full-stack planning now also
  accepts provider-default node groups whose empty desired Kubernetes version
  inherits the control plane by normalizing the provider-reported actual node
  version at the SDK boundary. Forward the parent campaign's proven Lease
  fencing authority into the child release reconciler instead of discarding
  the returned fencing token during campaign-state validation, reuse the
  frozen resolved max-surge count during recovery, and translate its non-safe
  zero to the command-neutral Terraform child's no-override representation
  instead of revalidating it as a fresh CLI override. Apply the same
  inherited-version normalization and resolved zero-surge contract inside
  provider node-group updates so a resumed control-plane-first hop can advance
  its node groups, and omit an empty GPU settings message for driverless groups
  so the provider does not reject the request as an incomplete driver preset.
  Persist protected controller-spool migration checkpoints in the parent
  campaign receipt and forward them to the release child without creating a
  second Slurm maintenance owner, and address the legacy Kruise
  AdvancedStatefulSet through its canonical plural API resource. After the
  target SlurmCluster opens, re-prove its shared spool projection, remove a
  claim template reintroduced by the restored old controller, and require the
  owned Pod to adopt the target PVC and pass its mount receipt. When the
  template-only update leaves the existing Pod on the legacy claim, verify its
  exact owner, UID, resource version, and source claim before requesting
  controller-managed recreation through Kruise's `specified-delete` label.
  Repeat that
  post-open proof even when a completed migration is clean before the main
  release opens, rather than short-circuiting finish and missing regression
  caused by the opening itself. Completed outer declarative-release replay also
  invokes this spool convergence before Flux readiness instead of skipping the
  inner stage callbacks. Keep Kruise
  webhook configuration objects under Flux drift correction while delegating
  only their controller-managed webhook lists and `template` certificate
  snapshots, so a missing configuration is recreated instead of ignored as an
  entire document. Recover Soperator-owned
  Kruise StatefulSets admitted during that outage by restoring only a missing
  rolling-update partition to the upstream default before dependency health.
- Replace `upgrade node-group` with the canonical `migrate node-group` command
  and no compatibility alias. The completed executor creates a permanent
  replacement group, validates it, performs Soperator placement cutover,
  retires the source, restores autoscaling, records forward-only recovery after
  cutover, rejects legacy checkpoints, and proves final provider and Terraform
  no-op state. Keep migration independent from Soperator upgrade receipts and
  status, with its own source-worker Slurm maintenance, staged placement apply,
  recovery ledger, report, and config-generation authority. A crash after
  journaling reservation intent resumes by creating the missing reservation;
  migration never records a foreign or absent reservation as applied.
- Fix live Soperator status for cxcli-owned Flux installs by projecting the
  unique current-generation Ready main HelmRelease into the release view and
  failing closed on incomplete or version-conflicting graph evidence.
- Fix equal-release Soperator replay over a staged cxcli Flux graph by
  routing observation through the frozen graph's declared release and product
  gates, including its retained suspended namespace owner, instead of applying
  direct-upstream discovery and ActiveCheck defaults to staged `chartRef`
  releases.
- Fix native Soperator ActiveCheck readiness by following the installed API's
  check-type-specific `Complete` status and creation-check filter instead of
  waiting for an `Available` condition that its controller does not publish.
- Add `soperator status --verify-observability` as the explicit read-only
  current-evidence verifier. It reuses validated live target context, accepts
  only the operator's Nebius CLI/SSO token, polls direct Prometheus and Loki,
  and writes a separate sanitized owner-only receipt. Remove telemetry phases,
  observability credentials, IAM/static-key handling, and Kubernetes Secret
  handling from every Soperator upgrade and no-op plan so upgrade completion
  ends at authoritative product and scheduling readiness.
- Fixed protected Soperator checkpoint replay after legacy HelmRelease retirement by using Kubernetes' machine-readable optional-object contract instead of parsing human NotFound text.
- Fix first Soperator rootfs-slot adoption to recognize the admitted `/home`
  persistent-PVC rebind as a mount-transport transition while still requiring
  the exact protected PV/PVC, consumer, storage, and Secret identities.
- Fix final Soperator product readiness to accept the canonical namespace
  HelmRelease only in its permanently suspended, Ready, current-generation
  state while continuing to reject suspension on every active child release.
- Preserve graph-derived AppArmor, MariaDB, and Prometheus controller
  capability flags when a partial Soperator operator override activates
  upstream's replacement semantics. Validate Soperator v1 SlurmCluster
  readiness through its native phase and component conditions, and v1alpha1
  NodeSet readiness through its Ready phase and exact desired/ready replica
  counts, instead of generic status fields those APIs do not publish.
- Treat the canonical passive-slot inventory as a sealed pre-activation image
  proof. After activation, allow target-owned Soperator runtime changes outside
  selected persistent mounts while continuing to verify exact protected
  PV/PVC and consumer identities, without creating another whole-rootfs
  inventory Job.

- Repair adopted Soperator worker and REST activation during protected
  upgrades. Render-only topology hydration now canonicalizes legacy and current
  GPU resource aliases against the discovered per-node capacity, emits matching
  GRES/static topology, and rejects conflicts or over-capacity requests before
  apply. REST-enabled controllers now wait for the protected jail's JWT
  directives before Slurm starts, breaking the SConfig REST-before-reconfigure
  bootstrap cycle without user action. An interrupted exact failed-adoption
  repair may resume from its fully proved `running` receipt while preserving
  the completed passive-rootfs Jobs; unrelated receipt or render drift remains
  fail-closed. The `disabled` topology profile now emits the explicit empty
  plugin override required by upstream 4.1.7, so GPU workers do not enter the
  topology ConfigMap wait path when no topology producer was selected.
- Repair live Soperator onboarding and upgrade discovery for older official
  releases: preserve authoritative default partition topology, keep Helm
  storage and Slurm workload namespaces distinct, preserve the process
  environment for live subprocesses, and use a bounded official GitHub release
  list fallback after transient tag-endpoint failures. Resolve onboarded
  physical SFS IDs from exact `READ_WRITE` MK8s node-group attachments, exclude
  dynamic compute-disk CSI handles, require retained local PV bindings, and
  verify each resolved filesystem's immutable live Nebius identity before
  upgrade mutation without requiring or changing the independent optional
  `forbid_deletion` provider control. Default managed Soperator SFS profiles to
  `false` while preserving an explicit user choice. Fall back from the login
  container to the controller `slurmctld` container for read-only protected
  Slurm-state capture only on the known configless/DNS failure, and resolve
  retained protected PVC/PV identities from exact physical-SFS receipt bindings
  when similarly named dynamic PVCs coexist. Advance registration to v3 so its
  fingerprint binds observed source provenance without binding the mutable
  desired app version. Reuse a matching owner-only sealed release snapshot for
  up to 15 minutes across dry-run and execution after revalidating its digest,
  first-seen tag identity, and content-addressed source, avoiding redundant
  unauthenticated GitHub resolution. Allow official GitHub API reads to use an
  out-of-band `GH_TOKEN` or `GITHUB_TOKEN` without logging or persisting it.
  Normalize the protected-upgrade handoff so current-chart storage aliases
  cannot collide with the adapter-owned aliases regenerated from admitted PVC
  identities. Rebase an exact project-root path alias such as macOS `/tmp` to
  its physical root before crash-safe generation containment checks while still
  rejecting symlinks below the project root, and validate upgrade admission at
  the selected target's staged Flux Kustomization rather than the multi-target
  parent. Verify official source/package render equivalence with the same
  adapter-compiled upstream umbrella values used by that staged render instead
  of passing cxcli's raw `nodesets` list into the upstream chart, and read the
  protected adapter/rootfs-capacity manifest from that same selected target
  subtree rather than the multi-target Flux root. Move live registration
  observation and release-neutral topology
  projection out of the Typer composition root into focused services. Treat
  Slurm's successful no-reservations status as an empty admission inventory
  instead of a malformed reservation record, and encode rootfs preflight
  operation fingerprints as Kubernetes-safe hexadecimal label tokens while
  retaining the full digest in sealed evidence. Replace source/reference
  rootfs classification with a content-free target-wins admission that binds
  the exact target image, selected persistent paths, active PVC, and staged
  passive-PVC storage contract without creating a scratch PVC or writing
  customer storage. After commit, authenticate the exact passive PVC as empty,
  populate it once from the digest-pinned official image, inventory it once,
  and seal that materialization receipt for recovery. Run rootfs inventory and cleanup Jobs
  through the official populate-jail image's POSIX `/bin/sh` contract, and poll
  their exact authenticated Job state so terminal failures surface immediately
  instead of waiting for the completion timeout. Admit a newly formatted
  rootfs as logically empty only when its authenticated inventory is empty or
  contains exactly one empty `/lost+found` directory, while rejecting every
  file, link, child, or additional path. Treat each selected persistent path as
  a customer-data boundary whose retained PVC intentionally shadows official
  image content at that subtree, while all unselected rootfs content remains
  target-owned. Execute Slurm probes issued through the login workload inside
  its mounted `/mnt/jail` rootfs, retain only the bounded controller fallback,
  and report the exact target release stage currently being installed. Disable
  only the uninstall CRD-cleanup helper in the exact digest-pinned staged
  `victoria-metrics-k8s-stack` 0.39.4 raw child, guarded by its exact identity
  and complete source-value shape, whose implicit kubectl image is unavailable;
  keep that same exact child's validation webhook fail-closed while using Flux
  `RetryOnFailure` at a 30-second interval so its initial operator endpoint can
  become Ready without an uninstall/reinstall race. Return any unexpected
  non-main staged `Stalled` condition immediately to the forward supervisor for
  exact-graph retry instead of consuming the full stage timeout; every other
  artifact, child, value shape, and install shape fails closed. Parse
  Slurm reservation records by quote-aware field boundaries so login-shell
  timestamp values containing unquoted spaces remain complete and canonically
  shell-quoted instead of blocking recovery. Prompt a
  terminal-driven upgrade for its durable Slurm job policy before admission,
  default that choice to guarded `requeue-hold-all`, and require an interactive
  terminal only when an explicitly interactive policy actually encounters
  blocking jobs. Refresh the adapter mount-gate image to
  the registry-resolved immutable digest and invalidate recent release snapshots
  whose recorded adapter image no longer matches that authority. Make an
  admitted target-apply repair a receipt-linked successor that imports and
  live-verifies the predecessor prefix, reuses its sealed passive-rootfs
  materialization, and never repeats rootfs population or inventory. Recover
  an older pre-fix replay only by write-ahead deletion of its exact incomplete
  read-only duplicate inventory Job, with both UID and resource version as API
  preconditions; completed, writable, foreign, or later-stage workloads fail
  closed. Re-prove repair storage from the authenticated predecessor receipt
  and authoritative Nebius filesystem reads instead of a globally clean
  post-apply discovery snapshot, and isolate optional Kruise workload collection
  so an absent optional API cannot erase required Kubernetes inventory.
- Generalize the canonical CLI contract from Soperator to the complete root,
  every public group and leaf, all parameters, hidden surfaces, and help text.
  Verify every public help/version/callback boundary from one exact built wheel
  across Python 3.12-3.14 CI using the same downloaded artifact.
- Make quality, coverage, and CLI-architecture baselines monotonic against the
  merge base: type ceilings may only fall, Ruff allowlists may only shrink,
  coverage floors may only rise, and new service/domain definitions may not
  accumulate in the Typer composition root.
- Require project-local, independently verified SSH host keys for WireGuard and
  SSH jump-host day-2 commands. Both commands now use strict host-key checking,
  reject missing or unsafe trust files before remote work, disable global
  known-hosts fallback, and ignore only `generated/ssh_known_hosts` in managed
  deployment repositories.
- Restrict `soperator install --resume --dry-run --replan` to stale,
  never-executed owner-only plan receipts. Reject every started, failed,
  partially applied, completed, corrupt, or authority-drifted receipt before
  replanning; validate a replacement Terraform plan separately, publish a new
  approval fingerprint only after validation, and preserve the prior saved
  plan/receipt if replacement planning or publication fails. Bind the exact
  six-command order and exercise every callback from the installed wheel.
- Make SecretStash primary-version promotion snapshot the canonical config,
  generated manifest, and tfvars before Terraform output reads, then commit the
  exact changed subset with compare-and-swap preimages so concurrent operator
  edits fail without partial writes. Rework staged Soperator reconciliation to
  establish final child specifications under a suspended outer release, remove
  child suspension fields stage by stage, and freeze exact main-workload
  authority before interpreting `Stalled` or `Ready`; only an authenticated
  main `Stalled` condition can terminate a started upgrade.
- Complete the remaining review remediations: make Soperator upgrade and
  destroy promote config plus the full render-owned generated postimage in one
  v2 transaction with deletion tombstones and reject non-canonical generation
  identifiers or digests; freeze the exact main HelmRelease
  UID/generation in the operation anchor so only that component can terminate a
  started upgrade and generic Flux waits cannot authenticate terminal evidence;
  add tri-state credential delivery recovery with durable cache/Kubernetes
  Secret markers and parent-directory fsync for cache commits, treat destination absence as ambiguous,
  sanitize credential-provider errors, and never let a completed generation
  fence later operator edits; and make
  `validate-sources` resolve official Soperator `latest` to an exact version
  while rejecting mutable versions for every other chart.
- Add crash-safe project bundle generations and credential-only IAM
  compensation, with content-free owner-only journals and foreign-edit/link
  rejection; identify SecretStash in user-facing surfaces while retaining the
  required `mysterybox` service identifier. Extract Soperator configuration
  materialization from the Typer root and add Python 3.12-3.14 offline CI,
  installed-wheel verification, Ruff, format/mypy debt ratchets, and global
  plus safety-critical branch-coverage gates.
- Harden architecture-preserving Soperator and local trust boundaries: keep a
  started upgrade retrying within the same invocation except for a typed main
  workload terminal failure; bind explicit Kubernetes contexts to the exact
  target UID; verify complete cached-source receipts; bound decompressed chart
  archives; require HTTPS release and chart authorities; classify Kubernetes
  absence only from structured `Status` objects; validate resume evidence;
  bind Terraform apply to an owner-only saved-plan digest; make normalized
  config writes atomic; and close the synchronous Nebius SDK client.
- Harden Soperator recovery and teardown safety: use persistent file locks,
  reject malformed Kubernetes and Object Storage lease expiry metadata, renew
  writer authority during takeover quiescence, revalidate Slurm journal action
  identities, bind managed destroy plans to exactly one approved cluster ID,
  restore exact config bytes after failed cleanup rendering, fail live status
  on incomplete collection, and omit raw Helm failure output from discovery.
- Close the canonical Soperator CLI proof boundary with shared target-selection,
  onboarding fail-before-write, explicit cluster-identity, invalid-redaction,
  and per-command option-rejection regressions. Verify from the installed wheel
  that a missing non-interactive install release fails before configuration or
  provider work, and align FEAT-015 with read-only discover/status behavior.
- Finalize one root group with exactly six public commands and pin every group,
  command, argument, and option description in the built-wheel contract v3;
  cover fresh-install forwarding, replan, upgrade job-control and approval
  forwarding, and both managed command routes. Make `--no-interactive` select a
  non-prompting upgrade job policy even in a TTY, keep raw collector output out
  of discovery bundles, durably publish first-seen release-tag identities, and
  bound downloaded chart metadata inspection before YAML parsing. Make live `soperator status`
  reuse registered onboarded cluster ID/access through a scoped temporary
  kubeconfig when no context is stored, retain nonpersistent managed handoff,
  and project only allowlisted stable install, upgrade, and destroy
  classifications. Reject selected Slurm job IDs and policies that do not
  match before config, cloud, or Kubernetes access. Bind the discovery rerun
  and bundle identity to one durable kube-context input while keeping temporary
  collection contexts out of both.
- Make new interactive Soperator install and upgrade operations show a
  runtime-resolved `latest(X.Y.Z)` release default, while non-interactive
  fresh installs and every non-interactive upgrade require an explicit exact
  version or `latest` before any resolver, cloud, or Kubernetes access. Preserve
  frozen recovery without re-resolving `latest`; interactive upgrade recovery
  may omit its selector while non-interactive recovery must repeat the frozen
  requested selector. Reject install-resume release, identity, profile, network,
  subnet, and overwrite overrides, enforce the complete official-release capability matrix
  through `make test-integration`, and verify the exact six-command option/help
  contract from the built wheel during `make all`.
- Reject generic render, deploy, destroy, Terraform, Flux, and component-target
  removal paths for every Soperator app row or registration marker, including
  disabled and partial state. Run the lifecycle guard before canonical auth,
  generated tfvars/report materialization, or provider work so rejected generic
  commands are side-effect free.
- Complete the six-command Soperator lifecycle with the dedicated
  `soperator destroy CONFIG --target TARGET [--dry-run]` path for managed and
  onboarded clusters. Add immutable resumable destroy receipts, exact
  destroy/preserve inventories, TTY plus cluster-ID confirmation, saved
  target-scoped Terraform teardown for managed MK8s, exact Nebius API deletion
  for onboarded MK8s, and post-delete proof that physical SFS/PVC backing or
  VM-NFS infrastructure remains. Fence against concurrent operations, refresh
  the full workload and PVC/PV/CSI inventory before cleanup, persist only safe
  failure classifications, and bind the post-cleanup config digest for
  crash-safe terminal rendering without overwriting later edits. Route generic
  destructive paths to `soperator destroy`.
- Move Soperator registration and protected-storage evidence to fail-fast v2
  contracts. Onboarding now proves verified official Helm-render equivalence
  plus live persistent object identity and rendered-field equivalence while
  persisting only digests and redacted PVC/PV/CSI identities. Upgrades use
  physical SFS as the canonical storage
  model, verify immutable live Nebius filesystem identities, and retain the explicit VM-NFS
  variant.
- Make `soperator status` report active install, upgrade, safety-pause, and
  destroy recovery state plus the canonical resume command without mutation;
  keep `soperator discover` support-bundle-only. Remove the unused mutation
  ConfigMap APIs and the duplicate outer upgrade retry supervisor.
- Restore the accepted pre-project-lifecycle `nebius-cxcli` workflow,
  catalog, Flux, observability, configuration, command, authentication,
  development, and security documentation in `README.md` and `docs/design.md`.
  Keep the lifecycle-managed upstream Soperator contract authoritative, add
  final task-oriented contents, and bind general documentation restoration to
  its current heading and body contract.
- Align Soperator documentation and CLI help with the exact six-command
  surface, distinguish the four lifecycle-changing commands from read-only discovery and
  status, and clarify that interrupted upgrades recover through the same
  approved upgrade command rather than a separate resume command or flag.
- Make protected rootfs admission resumable from one sealed content-free
  target-wins decision. Admission creates no reference/scratch PVC and performs
  no customer-storage write; after commit, cxcli authenticates the exact
  passive slot, populates it once from the official target image, inventories
  it once, and binds the resulting materialization receipt to recovery. Bind
  the complete admitted Slurm job, partition, and reservation preimage into the
  fenced cluster recovery journal.
- Preserve the official populate-jail runtime contract in custom active/passive
  Jobs by forcing overwrite only after the target precondition passes and by
  adding the upstream-required `SYS_ADMIN` and `SETFCAP` capabilities.
- Treat selected persistent paths as customer-data ownership boundaries and
  every unselected rootfs path as target-owned. Do not derive a historical
  source image or block on package/system drift; the single digest-pinned
  official target image is authoritative outside retained mounts.
- Make `soperator upgrade --execute --approve` freeze an authoritative inline
  admission receipt before Kubernetes or Slurm mutation, pause every live `UP`
  partition under exact full-record ownership, inventory jobs cluster-wide even
  when no worker Pod is discoverable, offer guarded automatic requeue-and-hold
  or interactive job handling, and supervise the admitted operation forward to
  completion without a terminal ordinary-failure budget. Login continuity is
  best-effort advisory evidence; authority, protected-state, journal,
  single-writer, and ambiguous mutate-once conditions enter a re-proving safety
  pause instead of terminating or rolling back the upgrade. Preserve the
  expired-takeover quiescence obligation across retries, recover the exact held
  Lease after transient renewal outages, reject partial Slurm partition
  inventories, and describe login evidence as sampled EndpointSlice plus TCP/22
  observations rather than continuous SSH monitoring.
- Add `soperator install --release latest|X.Y.Z` as the only fresh Soperator
  birth path. It creates the role-separated MK8s and SFS topology, freezes an
  exact infrastructure and official-upstream release plan, and requires the
  reviewed fingerprint for execution.
- Make `soperator onboard` adoption-only and target-free: it registers one
  unambiguous existing Nebius MK8s plus official Soperator installation without
  choosing an upgrade release.
- Make `soperator upgrade --to-release latest|X.Y.Z` use one capability-based
  operation planner for cxcli-installed and onboarded targets. Equal releases
  are observation-only, downgrades and unknown transitions fail before
  mutation, and execute freezes `latest` exactly once. Protected transitions
  keep the same MK8s cluster, adopt exact protected storage and SSH identity,
  classify and populate an active/passive jail rootfs, and use a single-writer
  Flux ownership handoff without recreating the control plane or NFS data disk.
- Resolve official release tags, Git identities, source archives, chart graphs,
  scripts, image references, and direct first- and third-party package digests
  into an immutable resumable snapshot. Persist the first fully verified
  repository/tag commit and tree identity and reject a moved tag. Remove the
  bundled target release lock.
- Make the digest-addressed frozen upstream populate-jail image the sole target
  rootfs authority. Reject `jailRootfs.targetImage`, mutable references, and
  competing image inputs, and bind the official image across rendering,
  operation identity, recovery journal, classification, resume, and receipts.
- Retain `/home`, `/data`, `/scripts`, and `/models` through mandatory
  path-specific PVCs, let the first-adoption upgrade wizard approval-bind
  additional existing data directories without copying them, and treat every
  unselected rootfs path as target-owned. Reject newly introduced optional paths
  after slot adoption rather than silently redirecting live data.
  Recycle a non-empty inactive slot only through an identity-bound, journaled,
  unconsumed-PVC cleanup stage on later active/passive upgrades.
- Classify releases from required source structure and values contracts rather
  than chart names, and fail closed when a required CRD, SlurmCluster template,
  Flux graph marker, or rootfs values surface is absent or incompatible.
- Reconcile verified upstream product resources plus a thin Nebius adapter;
  keep Terraform limited to out-of-cluster infrastructure.
- Use verified official upstream artifacts plus the thin Nebius adapter as the
  only Soperator product-delivery path. No OCI mirror, proxy, fallback registry,
  offline authority, or release-unpacking installer is supported.
- Keep migration planning, protected-state handling, scaling decisions, and
  recovery in the common capability-based operation engine.
- Keep only the canonical `soperator install`, `soperator onboard`,
  `soperator upgrade`, `soperator destroy`, `soperator discover`, and
  `soperator status` implementation in runtime and package artifacts.
- Harden Soperator operation, release, recovery, and destroy receipts with one
  owner-only atomic writer and symlink-rejecting reader; align generic render,
  destroy, Flux teardown, lifecycle diagrams, and job-monitoring guidance with
  the dedicated six-command Soperator surface.
- Preserve an onboarded cluster's non-secret, release-neutral live Slurm
  topology through an explicit projection and render its generated handoff
  immediately after the registration config is committed. Target profile
  defaults hydrate that projection only in render memory; arbitrary live
  images, environment values, init containers, annotations, and volume payloads
  are not copied into cxcli configuration.
- Bind reconciliation to an immutable operation specification, operation
  anchor, infrastructure identity, renewable lease, and exact protected-state
  and Slurm journals.
- Treat the first target declarative apply as the forward-only cutover boundary;
  restore source ownership only before that boundary and only while no target
  owner exists. Hold an exact operation-owned Slurm maintenance reservation
  through product readiness, remove it immediately before releasing requeued
  jobs, keep observability verification outside the operation, and leave every
  pre-existing reservation untouched.
- Refuse passive-rootfs cleanup until a context-pinned, identity-bound operation
  journal proves the exact PVC empty and unconsumed; bind cleanup and population
  Jobs to their full workload specification and selected Kubernetes context,
  and never recreate a completed stage whose checkpointed Job is missing or
  replaced.
- Persist a cluster-bound frozen release intent before upgrade config/render
  mutation, reuse it instead of re-resolving `latest` after interruption, and
  bind source/target capability plus rendered/reconcile stage-plan fingerprints
  into operation, anchor, and receipt authority.
- Disable upstream bundled Grafana, avoid duplicate collection of
  Soperator-owned namespaces, and grant configured Soperator node-account groups
  project-scoped editor access for metrics and log ingestion.
- Align offline rendering tests with frozen official-upstream release fixtures,
  add direct hostile-archive/cache, fencing, recovery-journal, rootfs race, and
  observability-verifier negative coverage, verify the single delivery path in
  CI and wheel contents, and keep two accessible SVG sources of truth for the
  protected workflow and jail storage design.
- Harden protected-upgrade recovery by accepting the canonical captured `/home`
  digest, rotating an interrupted operation anchor only to a strictly newer
  lease authority through Kubernetes CAS, rejecting stale Slurm-journal
  writers, rechecking suspended source HelmReleases, and making completed
  source retirement replay-safe.

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
