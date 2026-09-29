<!-- markdownlint-disable MD001 MD013 MD024 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
# Project Requirements

## Scope

These requirements define the supported Soperator lifecycle and the
cross-cutting persistence, IAM, and delivery controls needed by `nebius-cxcli`.
REQ-001 through REQ-012 remain reserved. The command, configuration, operation,
and delivery paths below form one canonical system.

<!-- REQUIREMENT: REQ-013 status=active priority=P0 type=product -->
### REQ-013: Resolve official upstream releases dynamically

#### User Story

As a platform operator, I need install and upgrade to use the requested official
Soperator release without requiring a matching cxcli release.

#### Acceptance Criteria

- AC-001: `latest` resolves through the official GitHub latest-release API and an exact `X.Y.Z` resolves through its official release tag.
- AC-002: Drafts, prereleases, downgrades, private forks, arbitrary tags, and downstream builds are rejected. After cxcli first verifies an official tag, an owner-only repository-plus-tag identity ledger pins its commit and tree; any later movement is rejected before artifact acquisition or mutation.
- AC-003: Before mutation, cxcli freezes the tag, commit, tree, source archive, normalized manifest, chart graph, scripts, image references, and package digests in one operation snapshot.
- AC-004: Artifacts are fetched directly from their official upstream authorities and verified against the release source. Root Markdown aliases may be represented only as inert Git-verified link-target bytes pointing to a distinct regular root document; source acquisition and recovery never create or follow filesystem links. Runtime links, link chains, hard links, submodules, and unsafe paths are rejected.
- AC-005: Before live deployment mutation, execution persists a target-bound active intent and immutable rendered generation. Configuration creation and rendering do not claim execution authority; interrupted execution reuses its exact generation without re-resolving latest. Every deployment application re-render, including ordinary reconciliation and target-scoped stages, binds all enabled Soperator releases from that generation before rendering; same-version chart tags are never resolved again. Release admission and execution pass that same snapshot identity to the common reconciler; generation-carried content remains sufficient when local discovery caches are absent or have changed.
- AC-006: A future release matching reviewed source-structural capability predicates is admitted without a cxcli version pin. Registry locations, additive upstream charts and third-party dependencies come from the verified release source, and enabled child releases come from its Helm render with effective values, not a compiled release-name inventory. Preserve stable adapter roles, immutable artifact verification, unambiguous identities and dependencies. Chart names alone never grant mutation capability; missing required structure, unsupported interfaces and enabled artifacts outside the frozen inventory fail before mutation.
- AC-007: Catalog source validation resolves the bundled official Soperator `latest` selector once through the same official resolver and validates the exact resulting chart version. Every non-Soperator Helm chart requires an exact immutable version; `latest` is rejected.
- AC-008: Reusable release admission is target- and request-scoped, binding verified source identity, compiler policy, normalized requested configuration and declared stages. The bounded recent cache never substitutes for immutable operation authority; package bytes are independently content-addressed. Different targets or selections on the same release cannot share admission evidence. Frozen replay requires its exact snapshot identity and never falls back to mutable discovery.
- AC-009: Official GitHub API reads may consume `GH_TOKEN` or `GITHUB_TOKEN` from the process environment to raise the provider rate limit. The token is used only in the request header and is never written to configuration, caches, receipts, errors, or logs; custom resolver openers remain isolated from ambient credentials.
- AC-010: Each official chart package pull receives at most three isolated attempts with bounded exponential backoff and jitter only for transport timeouts or connection resets. Authentication, certificate verification, not-found, digest, archive-validation, and source-identity failures fail immediately. Failed attempt directories are removed and never become release authority. Exhausted transient failures expose only the attempt count and a sanitized failure category; public error cause chains do not retain raw transport details.
- AC-011: Separate verified source discovery from required-package admission. Creation loads source defaults before the wizard and verifies only completed selections before saving; validation, rendering and Grafana installation use the same target-scoped pipeline. Required artifacts comprise the umbrella, effective consumers, explicit compiler/adapter consumers, declared operation stages and recursive packaging dependencies. Disabled unused standalone charts are not acquired or package-validated. Compare required official packages with source and validate final transformed consumer graphs and values before sealing v3 evidence. Missing packages or uncovered consumers fail explicitly. No NFS-specific exception, content-rewrite bypass, project release lock or compatibility path is provided. Old snapshots are rejected before mutation; unfinished operations must complete with their previous binary before cutover.

#### Negative Criteria

- NC-001: Do not use a bundled target-version lock as runtime release authority.
- NC-002: Do not add a mirror, proxy, fallback registry, offline artifact authority, or release-unpacking installer.
- NC-003: A global chart-role inventory or release version alone must not select or certify deployment packages. Frozen active attempts must not expand their artifact inventory from edited configuration or refreshed registry tags.

#### Validation Method

Compare the frozen snapshot, verified source, downloaded packages, rendered
graph, and live revisions.

#### Test Method

Run latest and exact discovery, durable moved-tag and concurrent-ledger,
hostile-archive, package-mismatch, structural-contract, downgrade, and frozen
recovery tests.

#### Evaluation Method

Confirm no cloud or Kubernetes mutation precedes complete immutable release
authority.

<!-- /REQUIREMENT: REQ-013 -->

<!-- REQUIREMENT: REQ-014 status=active priority=P0 type=architecture -->
### REQ-014: Keep Nebius integration in a thin adapter

#### User Story

As a product owner, I need Soperator behavior to remain owned by upstream while
cxcli supplies only Nebius-specific integration.

#### Acceptance Criteria

- AC-001: The verified upstream release owns Soperator controllers, CRDs, workloads, active checks, images, and release topology. An explicit one-GPU test/dev diagnostics profile may waive incompatible GPU checks through supported upstream values, with reduced coverage bound to the acceptance evidence.
- AC-002: The cxcli adapter supplies only Nebius infrastructure identities, storage, networking, observability, and supported upstream values. The frozen official release owns the jail image; cxcli does not accept a second rootfs-image override.
- AC-003: Adapter inputs are validated against the selected release contract before mutation.
- AC-004: Onboarding persists only an explicit release-neutral NodeSet, partition, and optional accounting/exporter service topology projection; REST is a required upstream SConfig dependency. Generated GPU NodeSets include a missing Slurm Gres count even when their CPU topology already fits; fitting topology and its explicit Gres settings remain unchanged. Adopted NodeSets remain bound to their discovered node-group identities when target-profile defaults are materialized; arbitrary live container, environment, image, annotation, init-container, secret, and volume payloads are not copied into cxcli configuration.

- AC-005: New managed worker CPU and memory defaults derive from the selected VM preset using the Nebius Soperator reference deployment reserve policy, with configured resident sidecar deductions. Keep all selected GPUs, preserve explicit resource values, reject insufficient capacity, and keep physical Slurm CPU topology independent of the container CPU-time quota. Offline rendering stays deterministic; registered topology and frozen recovery generations are not resized.
- AC-006: Support homogeneous H100 workers with one or eight GPUs per host across generated resources, device configuration, Slurm registration and scheduling. Eight-GPU workers retain standard upstream diagnostics. One-GPU deployment requires the explicit fast-dev-test profile described in AC-007, which also supports eight-GPU and mixed targets. It waives reviewed GPU health/performance diagnostics while retaining bootstrap, service readiness, worker registration and native Slurm smoke checks. Bind and display waived coverage in acceptance evidence; never label omitted diagnostics as passed or production-qualified.

- AC-007: For soperator create only, non-interactive creation resolves --fast-deploy/--no-fast-deploy before values.deploymentProfile in --values-file, falling back to standard. Interactive creation always asks for deployment mode: a supplied flag, then a values-file profile, seeds the selection; without either, require an explicit answer and reprompt on blank input. The final interactive answer wins. Persist values.deploymentProfile as fast-dev-test or standard. Preserve other commands and generic wizard behavior. Existing configurations without the setting remain standard; reject invalid profiles and the retired diagnosticsProfile key before overriding supplied values. Fast supports CPU, one-GPU, eight-GPU and mixed targets, warns about waived qualification, and never infers policy from hardware during render/deploy.

- AC-008: New MK8s/Soperator wizard configurations default each enabled CPU or GPU worker host total to two, including both worker shapes in mixed profiles. Prompt defaults and omitted-total profile materialization agree; explicit worker totals remain authoritative. Worker hosts are distinct from GPUs per host, nodes per group and autoscaling bounds.

#### Negative Criteria

- NC-001: Do not copy, fork, template, or publish upstream product charts in this repository.
- NC-002: Do not allow adapter resources to overlap resources owned by upstream.

#### Validation Method

Compare rendered ownership with verified upstream source and the adapter
allowlist.

#### Test Method

Render every supported capability contract and reject unknown values or
resource ownership overlaps. For creation mode, verify non-interactive precedence,
interactive seed/override and required answers, saved profiles, cancellation
before setup and unchanged generic wizard behavior. Verify two-worker defaults
for CPU, GPU and mixed profiles, matching wizard fields and omitted-total
materialization, and preservation of explicit worker counts.

#### Evaluation Method

Confirm an upstream release can change independently when its capability
contract remains compatible.

<!-- /REQUIREMENT: REQ-014 -->

<!-- REQUIREMENT: REQ-015 status=active priority=P0 type=product -->
### REQ-015: Provide one canonical Soperator command family

#### User Story

As an operator, I need one clear lifecycle for new, existing, and upgraded
Soperator clusters.

#### Acceptance Criteria

- AC-001: `soperator create --release latest|X.Y.Z` runs the purpose-built wizard and saves configuration, with the same authentication bootstrap as ordinary create. Standard validate, render, validate-generated and deploy perform the remaining workflow under REQ-031. The complete MK8s, SFS, and upstream-adjusted Soperator field wizards remain available without core component-selection phases or a second release prompt. Fresh installation exposes dedicated Infrastructure, upstream Soperator configuration, and required platform/integration sections; optional ordinary Apps are added only afterward using `component add`, and create has no `--app` option or generic observability prompts. A private typed creation workflow carries the frozen release unchanged. Infrastructure is fixed to MK8s and SFS; external helpers follow actual configuration requirements, while installation ordering never selects components. Upstream cert-manager is the sole required certificate owner. Public Nebius Grafana is the default query interface. Explicitly selected Grafana and its Gateway remain cxcli-installed features; upstream Grafana is disabled through the existing Flux post-render boundary. The separate Nebius observability agent retains general target telemetry and trace ingestion, with Soperator namespace exclusions. Slow creation discovery and subsequent deployment rendering/planning emit terminal-safe progress on stderr with elapsed completion/failure rows in terminals and bounded stable non-TTY records. Subnet discovery and MK8s preflight consume supported Nebius status pool fields without deprecation tracebacks and preserve explicit versus inherited subnet ownership. Platform choices and preset metadata share one complete project-scoped inventory. Managed node-group defaults are propagated before recommendations for a newly selected CPU/GPU shape; absent prior defaults do not trigger repeated failed API calls or replace explicit user choices.
- AC-002: `soperator onboard` records an existing Nebius MK8s cluster with an installed official Soperator release and does not choose an upgrade target.
- AC-003: `soperator upgrade` upgrades either a cxcli-installed or explicitly authorized onboarded cluster as one full-stack campaign: the official Soperator release, every required sequential Kubernetes minor hop, node OS, the Nebius-image GPU driver stack, and the release-defined Jail CUDA version.
- AC-004: `soperator discover OUTPUT_ROOT --tenant-id TENANT --project-id PROJECT --cluster-id CLUSTER` inspects one pre-existing MK8s cluster without reading or creating `config.yaml`. Tenant/project/cluster scope is required and verified before report binding; optional `--region-id` must match the derived live region, optional `--kube-context` must match the provider-generated kube-system UID, and `--access` defaults to `external`. Discovery writes a support-safe schema-v2 pair under `soperator-discovery/<cluster-key>/`: complete normalized cluster inventory in `report.json` and the exact bounded customer summary rendered on screen in `report.md`. The JSON inventory includes every provider node group and Kubernetes node plus the detected Soperator, GPU, Slurm, storage, topology, health, and collection evidence relevant to that cluster, while dedicated allowlist projectors exclude arbitrary labels, status messages, object specs, Secret or ConfigMap values, storage handles, raw command output, and unrelated namespaces. The summary is derived from that inventory, contains fixed cluster and component rows plus one row per provider or unmatched node group, reports `Ready/Actual/Target` counts and bounded configured/observed version, OS, and GPU-preset values, and never emits per-node rows; a cluster with 4,000 nodes and five groups remains below 50 logical Markdown lines and 16 KiB. Provider-group correlation uses exact provider ID first and unique exact name only when the node has no provider ID; contradictions and unmatched nodes produce `partial` instead of guessed attribution. Collection lanes distinguish `succeeded`, `failed`, and `not-applicable`, successful empty arrays remain distinct from failed reads, and `not-detected` is reported only after every Soperator-detection lane succeeds and proves absence. Candidate resolution, inventory order, summary order, null handling, and mixed-value compaction are deterministic. Pair publication is serialized per report directory, stages both artifacts under one report identity and digest, and publishes JSON last so its presence commits the matching Markdown. Discovery creates no workload, registration, render, or lifecycle receipt and performs no cloud/Kubernetes mutation. Shared Markdown contains only the relative artifact path, never the operator's absolute output-root path, and visibly escapes terminal controls, bidi/format controls, line separators, and Markdown structure in live-derived values before persistence or display. Evidence remains classified as `provider-configured`, `kubernetes-observed`, `runtime-observed`, or `unknown`; presets, images, chart metadata, and labels never prove exact runtime driver/CUDA versions. `complete` exits zero; `partial` and `not-detected` print and write the report before exiting nonzero. Onboarding never consumes this public report and re-collects its registration evidence independently. `soperator status` remains the registered-target configured/live view; explicit `--verify-observability` may write only its separate owner-only observation receipt.
- AC-005: `soperator upgrade` performs fresh authoritative discovery, freezes one parent campaign intent and receipt before mutation, and starts without a second planning command. The receipt binds cluster identity, the exact provider node-group ID set, requested selectors, exact release and Jail CUDA target, dynamic provider version inventory, sequential Kubernetes hops, per-hop node compatibility rows, rollout and Slurm policy, each child irreversible frontier, the exact ownership/backend authority, the initial config-plus-generated project snapshot, and a content-addressed chain of complete config-plus-generated project generations; dry-run discovery remains advisory only. Planning binds the initial configuration bytes in every mode and rejects concurrent edits before publishing desired configuration. Recovery accepts only the last durable operation preimage or postimage, rejects generated-project or provider-inventory drift, and never reselects or falls back to another infrastructure backend. While parent-campaign Slurm maintenance is active, protected controller-spool migration checkpoints are durably stored in that parent receipt and supplied to the release reconciler; the release child must not create a second maintenance owner or require its standalone Slurm journal. After every main-release opening, including one preceded by a clean completed-state check, completion re-proves the target SlurmCluster's exact shared spool PVC projection, removes only a legacy same-name claim template reintroduced by that opening, and requires the exact owned controller Pod to adopt that target claim and pass its mount-receipt gate. If the template-only change leaves the Pod on the legacy claim, the migration verifies its exact AdvancedStatefulSet owner, UID, resource version, and source claim before requesting lifecycle-aware controller recreation through Kruise's `apps.kruise.io/specified-delete=true` label. Replay of an already-completed outer declarative-release transition runs the same spool convergence before Flux readiness rather than bypassing the inner stage callbacks; a completed receipt that regresses this postcondition reopens at the same boundary.
  A completed or no-op declarative-release postcondition refreshes all frozen digest-bound sources before its Flux graph wait, so recovery does not trust stale-positive artifact status after source-controller storage replacement.
- AC-006: A target equal to the source is a validated no-op only when the reviewed checks policy is unchanged and no later infrastructure segment requires fresh acceptance; a lower target is rejected. A no-op over a cxcli-owned Flux graph uses the frozen graph as its single readiness authority and requires the unique canonical main workload, the unique retained namespace owner as the only suspended release, current Ready generations, the frozen release version on every graph member, and only the product gates declared by that graph; a direct-upstream install without that graph retains native HelmRelease and product validation. When ActiveChecks are declared as required, product readiness evaluates only checks whose `runAfterCreation` value is true or omitted under the API default, requires at least one such check, and accepts only the official status for its declared type: `k8sJobsStatus.lastJobStatus=Complete` for the default `k8sJob` type or `slurmJobsStatus.lastRunStatus=Complete` for `slurmJob`.
- AC-007: Deploy, onboard, and upgrade share project ownership and operation evidence. Discover is the config-independent pre-onboarding information view; status is the read-only registered-target view. Local campaign and child receipts that lack complete frozen deployment controls provide original-options recovery guidance without inventing a complete executable command; known campaign Slurm options remain visible.
- AC-008: Upgrade execution is forward-only and resumable, with bounded invocation work. Permanent authority, immutable-input, integrity, authentication, configuration and unknown failures stop promptly without abandoning the durable operation. Only explicitly classified transient failures of safe logical operations receive at most three total attempts, with one- and two-second backoff plus at most 250 ms jitter; campaign and release commands never replay the whole workflow on error. Readiness keeps its original deadline. Ambiguous mutations require receipt-based observation before replay. Stopping preserves maintenance and restoration evidence, confirms contained local writers are quiescent, and returns nonzero with sanitized cause-first recovery guidance. Resume uses `nebius-cxcli deploy CONFIG_YAML` and the original frozen generated bundle and controls; changed inputs must be restored explicitly and are never silently rebound. Cluster controllers may continue independently while the local invocation is stopped. The Kruise child may delegate its controller-managed webhook lists and `metadata.annotations.template` certificate snapshots to that controller, but must keep both webhook configuration objects under Flux drift correction so deletion is recovered from the frozen release. After webhook recovery and before Kruise dependency readiness, the command restores only missing rolling-update partition defaults on resource-version-bound, Soperator-owned AdvancedStatefulSets; existing values and foreign owners remain untouched. A non-main Stalled condition stops promptly for recovery; it does not restart a full readiness window. Exact main-workload terminal classification still requires its frozen GVK, namespace, name, source, UID and observed generation. A missing local credential-plugin runtime requires restoring the same runtime before deploy recovery.

- AC-009: Global `destroy CONFIG --target CLUSTER_ID [--dry-run] [--yes] [--delete-sfs] [--preserve-pvc-disks]` is the only MK8s teardown path for managed and onboarded clusters, with or without Soperator. An immutable cloud cluster ID is always required. It inventories exact DESTROY and PRESERVE sets; execution requires an exact TTY confirmation or explicit --yes after identical validation. REQ-034 defines the shared ownership and recovery contract.
- AC-010: The Soperator command family contains exactly create, discover, onboard, upgrade and status in that help order. Standard quota-check, quota-request, validate, render, validate-generated, deploy and bootstrap-ci accept its configuration. Public soperator install and its aliases are absent.
- AC-011: Status reports the configured and live release plus any active Soperator lifecycle operation type and phase, safety-pause or failure classification, receipt path, and exact rerun command without changing cluster or receipt state. For full-stack upgrade it projects the parent campaign and supervisor before child release receipts. A running, recovery-required, safety-paused, or terminal final-only revalidation of a completed campaign remains visible while retaining its last-known-good evidence. Completed upgrade history separately prints its frozen backend/compatibility tuples and per-group GPU runtime report references without becoming an active operation or substituting for a fresh live Helm or Flux read. `migrate node-group` is not a Soperator lifecycle operation and is visible only through its own command and report.
  Live status emits current-stage progress with a spinner and elapsed time on stderr, or bounded plain-text records without a terminal. It shows one installed chart release with build metadata, explicit idle lifecycle wording, and an attributed component table with Kubernetes readiness and bounded read-only Slurm controller/node observations. Healthy is green with an empty Details cell; degraded is yellow, unhealthy/error red, unknown magenta, disabled dim gray and intentionally zero capacity cyan. Nonhealthy details state observed issues and bounded operational impacts without inventing causes. A final overall health line aggregates every enabled applicable component, never declares empty or partial evidence healthy, excludes disabled/intentional zero capacity and historical check results, and preserves unknown counts. Independent failed current-health reads yield a partial report and nonzero; identity conflicts fail closed. Helm workload ownership uses the release target namespace, never the Helm storage namespace. An installed NodeConfigurator child requires one uniquely attributed CR; missing or ambiguous expected resources remain Unknown. Recorded-check history has a compact outcome summary with at most five failed/error/cancelled entries by default; --show-checks expands all records without additional queries. Missing results and missing timestamps do not imply failure. History collection failures are visible advisory warnings and do not change current overall health or exit status. Offline --show-checks reports history Not checked. Unhealthy/degraded/incomplete live health is nonzero. Offline status reports Not checked. Failed explicitly requested observability verification also prevents overall Healthy.

- AC-012: Create, onboard, upgrade and status retain paired interactive flags. Create resolves omitted interactive latest once and requires an explicit release in noninteractive mode; it owns identity, profile, values-file, networking and overwrite options. Deploy executes the rendered bundle directly with optional --dry-run, without approval, fingerprint, execute, resume or replan flags. Matching interrupted deployments recover automatically under REQ-031. Interrupted deployments recover through deploy with the frozen generation and matching job controls, without prompting or re-resolving selectors. Direct existing-config onboarding rejects project-creation flags and keeps its configured region authoritative; an explicit region is an additional assertion and cannot replace it. Fresh deployments-root onboarding derives an omitted region from the live cluster without prompting and preserves that derivation across an interrupted scaffold through an owner-only marker published first and bound to the exact scaffold config hash. Marker and initial config publication share the config lock, and config creation is atomic and create-only so a competing writer is never overwritten. The marker and hash are re-proved after discovery and under the config lock before the observed region can be written.
- AC-013: The developer CLI contract and installed-wheel verifier bind the exact five-command order, command and argument descriptions, option names, declaration/help order, parser-required options, conditional requirements, defaults, help text, structural flag properties, paired forms, repeatable forms, selected epilog clauses, and cross-option constraints.
- AC-014: The full-stack upgrade wizard dynamically calls the Nebius control-plane versions API, defaults the Kubernetes target to the highest same-major endpoint reachable through a contiguous provider-supported minor path, and freezes every hop. It selects the latest provider OS first and then the latest compatible Nebius drivers preset independently per node group and hop; exact and keep selectors must remain valid at every hop. A node group one minor behind the live control plane is caught up at the current control-plane minor before later hops; a larger or cross-major lag fails before mutation. The control plane upgrades before node groups on later hops; its gate requires matching desired and actual status versions, `RUNNING`, non-reconciling state, and two stable observations. Readiness re-reads desired capacity and resets stability on capacity changes. Every stable zero-capacity group receives two provider desired-template observations and records live GPU/CUDA evidence as not applicable; every desired-positive group requires full rollout and applicable live validation. Final completion requires provider rollout readiness plus generated Soperator, Kubernetes, and applicable GPU validation, fresh reconciliation of every frozen digest-bound Flux source, re-frozen HelmChart artifact identities, and current-generation Ready state for the complete rendered release graph. Replaying the exact command for an already-completed campaign reruns only that final postcondition, updates its final evidence, and does not reopen Slurm maintenance or repeat provider mutations. Kubernetes inventory prefers the rendered `nebius.com/node-group` name and falls back to the provider-native `nebius.com/node-group-id` identity for onboarded nodes. Soperator validation requires the canonical `sconfigcontroller` deployment and adapter-owned read-only GPU driver root mount, while separate Jail-runtime validation proves CUDA/NVML library and device access. Runtime inventory, deploy-smoke, smoke, benchmark, GPU-stack, and CUDA-visibility reports are lifecycle evidence excluded from render-owned project snapshot authority.
  Global exact GPU selectors ignore driverless groups, exact per-group driverless overrides fail, ambiguous group aliases fail, provider inventory order is canonical, and every tuple is revalidated immediately before its individual group mutation. Each desired-positive GPU group requires exactly one non-skipped successful scoped CUDA report. Final completion refreshes and proves Flux sources/graph before runtime validation, then rechecks the graph and exact all-group count/resource-version snapshot afterward; any capacity or identity change retries final readiness. Provider compatibility reports a Nebius drivers preset, not an exact NVIDIA driver build; the bounded CUDA canary proves execution and retains an attempt-unique SHA-256-bound report but does not claim exact host-driver or runtime-CUDA versions. A failed replay cannot overwrite last-known-good report artifacts, and a completed campaign replaces last-known-good final evidence only after successful revalidation.
  When all target GPUs are already allocated to Slurm workers, the bounded CUDA canary must map each selected node to exactly one Ready `slurmd` pod owned by the selected Soperator instance and requesting every advertised node GPU, then produce a non-skipped report only after `nvidia-smi` returns one inventory row per advertised GPU and CUDA Driver API initialization passes there.
  Final source and graph proof resolves the selected target's Flux directory rather than the project-wide Flux root.
- AC-015: `migrate node-group` is the only guarded platform, hardware preset, CPU/GPU kind, GPU-cluster, reservation, or fabric migration path. It has no `upgrade node-group` alias. For one Terraform-managed source group it creates a permanently named replacement, proves readiness, enters source-worker-scoped Slurm maintenance before dual placement, applies placement through a migration-owned Flux path without invoking Soperator upgrade receipts or supervision, cuts workloads over, retires the source, restores autoscaling and its own maintenance, proves replacement-only provider state and a final Terraform no-op, and records a durable forward-only receipt once cutover starts. Its receipt, report, recovery, config-generation chain, and maintenance journal are independent of `soperator upgrade`.
- AC-016: `upgrade node-template` remains the in-place rolling path for Kubernetes version, node OS, and Nebius-image GPU driver preset on existing node-group identity. `soperator upgrade` composes that ownership boundary for a whole managed Soperator cluster; it never changes hardware or fabric. Admission must classify exactly one infrastructure authority: cxcli-managed targets use Terraform for every MK8s/control-plane/node-template/OS/provider-driver mutation, while onboarded targets use Nebius provider APIs under the same shared admitted deployment campaign. The exact ownership-selected backend and internal provider authority are frozen in the campaign digest. There is no backend override, cross-backend fallback, or backend switch during recovery; both authorities share only the Soperator release/Flux path.
- AC-017: Long-running upgrade discovery, release verification, compatibility, admission, operation-authority acquisition, maintenance entry, provider, Flux, runtime, and restoration phases emit progress on stderr without changing stdout plans or results. The static plan renders every adjacent Kubernetes transition beginning at the observed source minor, while the provider table labels destination-minor rows as target compatibility. A terminal uses one left status slot whose spinner becomes a green success check, red failure mark, or dim skip mark in place; each completed row and its elapsed time is committed to terminal scrollback, while the live surface contains only the active phase and invocation stop notices use the same stderr console. Non-TTY logs use stable ANSI-free phase records plus at most sixteen deduplicated `INFO` milestones per phase. Expired cluster-Lease takeover reports its bounded prior-writer quiescence proof, and maintenance entry reports partition pause, active-job handling, reservation creation, and barrier convergence without changing their safety gates; repeated barrier passes share one non-TTY milestone key while continuing to update the terminal row. The outer maintenance renderer pauses reentrantly while a Slurm table, live dashboard, or prompt owns the terminal, resumes afterward, and always resets its pause binding. Flux controller manifest, rollout, and migration subprocesses never write successful resource chatter through the live renderer: cxcli captures it and reports bounded apply counts, controller totals, and migration counts grouped by kind. Parsing and progress callbacks are presentation-only; command return codes, authority checks, timeouts, migration order, and API postconditions remain authoritative. Normal campaign output is bounded by node-group count, with exactly one row per group containing the complete frozen per-target compatibility path; exact node evidence remains owner-only. Failures show bounded sanitized diagnostics and interruptions leave no false success state or orphaned live display.

- AC-018: Fresh interactive `soperator create` asks Create new or Use existing independently for accounting, controller-spool, and jail before their settings. New selections retain profile defaults and never request existing IDs or filesystem inventory. Reuse requires an explicit current-project inventory selection and prompts only for the attachment mount tag; empty or failed lookup never implies creation. Existing IDs default to reuse, switching to new removes the ID, and duplicate role IDs or mount tags are rejected. A shared filesystem-type prompt appears only when a role creates storage. A role/action summary also appears when customization is skipped; reuse summaries show ID and attachment tag, never profile defaults as observed storage metadata. Navigation follows visible fields and an incomplete fresh-install field wizard stops before further materialization, config publication, rendering, or planning. Ordinary SFS wizards and the configuration/Terraform create-versus-reuse contract remain unchanged. Headless creation consumes explicit inputs; deployment recovery never reopens the wizard.

- AC-019: Fresh interactive Soperator installation defaults upstream Soperator configuration and required platform/integration customization to no. Each prompt displays the default; Enter retains the enabled component, frozen release and previewed settings, while explicit yes opens detailed configuration. Back and quit remain available; infrastructure and generic-app wizard defaults retain their own policies. Fresh installation, storage adoption, and slot switching produce one canonical storage intent without synthesizing adapter-owned volume sources. Only the adapter generates those sources; conflicting supplied sources fail rather than being stripped or migrated. Before publishing a fresh project, both interactive and non-interactive installation compile its completed configuration with the frozen release using the same compiler as rendering. Compiler failure prevents scaffolding, config publication, and planning.

#### Negative Criteria

- NC-001: Generic `create` must not install Soperator.
- NC-003: Generic destructive and Terraform mutation paths must reject protected Soperator resources and direct the operator to the dedicated lifecycle. Ordinary app configuration and mutation follow REQ-028.
- NC-004: Do not provide a dedicated Soperator destroy command, an implicit/bulk MK8s deletion path, or a discover-based upgrade preview path.
- NC-005: Do not reinterpret interrupted creation as complete merely because its release appears. Retain recorded source and target identities until independent completion. Never let new YAML or selectors alter an active immutable operation.
- NC-006: Recovery must prove ambiguous prior effects before refreshing a Terraform stage and must not expand its action scope, replace unknown identities or discard failure evidence. Internal fingerprints are integrity records, not mandatory user approval.
- NC-007: Do not translate legacy node-group checkpoints into migration receipts, mutate hardware through `soperator upgrade`, restore the source after a migration cutover frontier, or declare a full-stack campaign complete while its operation-owned Slurm maintenance is still active.
- NC-008: A post-infrastructure dashboard delivery repair must preserve the approved release, compiled values, infrastructure, optional apps, and failed history. Admit only a pinned failed chart before the main release starts, with cluster-bound repair evidence and an exact successor operation; no general render replacement is allowed.
- NC-009: An interrupted first-install checks repair may bind checks to the already approved active jail after the main release starts, but only before acceptance. Preserve immutable source, storage and prior repair evidence; terminate only a fenced, exact upstream read-only wait hook and retain native failed-install remediation. Never manufacture successful check status or replace the cluster. A queued retry is quiescent only with exact admitted identity, current suspension acknowledgment and definitive failed-install uninstall evidence. The same fenced pre-acceptance frontier may correct an omitted REST dependency using only the enabled flag, controller OpenMetrics setting and existing JWT mount gate; authenticate every prior repair seal.
- NC-010: The upstream jail-log collector must use the approved system placement and the active jail backing path selected by the same render, including after a jail-slot switch. Preserve its upstream executable, configuration, storage access mode and readiness checks. A closed first-install repair may bind only that collector before acceptance while retaining successful bootstrap checks, immutable source and storage, native failed-install recovery, and every predecessor seal. If that exact corrected child reaches a current healthy Deployment but native upgrade remediation has no rollback target, resume may request one native retry per immutable release configuration; preserve readiness and stop on another failure. Do not add compatibility node labels or replace shared collector values.

- NC-011: A failed initial GPU-acceptance operation with missing generated Slurm `Gres` counts may receive a sealed successor that changes only those absent counts in matching compiled values and umbrella inputs. Require the exact failed acceptance frontier, unchanged source/storage, unstarted check execution and the original full-worker maintenance reservation. Preserve every predecessor and carry only that reservation's verified identity and fingerprint, never successful acceptance evidence. Authenticate both full-values policy identities through the sealed missing-count delta while proving unchanged check execution; replay from declarative apply before the invalid restoration boundary.

#### Validation Method

Inspect command help, saved configuration, operation snapshots, and mutation
ordering.

#### Test Method

Run CLI contract and command-order checks, dynamic version-path and compatibility
tests, interactive selector prompts, non-interactive fail-before-network,
pre-execution replan, exact parent-campaign recovery, maintenance restoration,
provider and Terraform backend tests, migration-frontier tests, and
fail-before-mutation tests for installed and onboarded clusters, then exercise
each command callback from an isolated built wheel.

#### Evaluation Method

Confirm every advertised lifecycle is reachable only through `soperator`.

<!-- /REQUIREMENT: REQ-015 -->

<!-- REQUIREMENT: REQ-016 status=active priority=P0 type=architecture -->
### REQ-016: Separate cloud infrastructure from in-cluster reconciliation

#### User Story

As an infrastructure owner, I need Terraform limited to Nebius cloud resources
while Kubernetes reconciliation is performed directly.

#### Acceptance Criteria

- AC-001: Terraform owns out-of-cluster Nebius infrastructure only.
- AC-002: Helm, Flux, and Kubernetes APIs own all in-cluster installation and upgrade actions.
- AC-003: Onboarded clusters are discovered through Nebius APIs and then use the same in-cluster engine as cxcli-installed clusters.
- AC-004: Protected storage is represented by one storage-neutral identity contract with canonical physical SFS and an explicit optional VM-NFS variant. Install, admission, operation, recovery and upgrade bind the same receipt digest. Destroy freezes a separate cloud-only inventory with the exact physical storage IDs.
- AC-005: A managed install defaults every Soperator-created physical SFS filesystem's optional Nebius `forbid_deletion` provider control to `false`, preserves an explicit user selection of either value, and both destroy ownership modes use one SDK cluster-delete request. Default destroy preserves SFS; --delete-sfs respects the live provider protection setting.
- AC-006: Both ownership kinds delete the exact registered cluster through the Nebius SDK without in-cluster cleanup, verify the approved storage disposition after cluster absence, and reconcile only frozen owned Terraform ancillary resources and expected state/output changes.
- AC-007: Generated Soperator node service-account names are scoped to the configured MK8s cluster name and node-group key. An incomplete wizard does not allocate identities from the temporary component name. Repeated normalization and worker resizing retain saved account names and explicit account IDs for surviving groups; separate cluster names in one project produce separate default identities. A name collision never authorizes automatic adoption or deletion.
- AC-008: Deploy remains idempotent with its retained backend state. Before applying a plan that creates a named IAM service account, check its project for an existing identity and stop before infrastructure mutation on collision or an inconclusive lookup. Report the exact Terraform address and existing account ID for ownership-verified state recovery. Recreating a deleted backend does not imply existing infrastructure is absent or authorize adoption or cleanup by name.

#### Negative Criteria

- NC-001: Do not add Terraform resources for in-cluster Soperator installation.
- NC-002: Do not require cxcli to own the existing cluster infrastructure before onboarding it.
- NC-003: Do not make VM/NFS-specific Helm values a prerequisite for a physical-SFS install or upgrade.
- NC-004: Do not apply Terraform cluster/SFS deletions during MK8s destroy, or permit ancillary reconciliation to create, replace, update or delete unrelated infrastructure.

#### Validation Method

Audit generated Terraform and Kubernetes mutation plans by ownership boundary.

#### Test Method

Run architecture guards that reject Soperator Helm or Kubernetes resources in
Terraform output. Exercise account-name isolation across clusters, incomplete
wizard materialization, saved configuration round trips, and account retention
when worker shards grow, shrink or change autoscaling.

#### Evaluation Method

Confirm infrastructure convergence and in-cluster reconciliation can be
recovered independently.

<!-- /REQUIREMENT: REQ-016 -->

<!-- REQUIREMENT: REQ-017 status=active priority=P0 type=reliability -->
### REQ-017: Persist immutable and resumable operation evidence

#### User Story

As an operator recovering an interrupted lifecycle, I need the same approved
operation to continue from its exact durable evidence.

#### Acceptance Criteria

- AC-001: The receipt records selector, resolved release, source identity, exact source and target capability fingerprints, rendered/reconcile stage-plan fingerprint, strategy, ownership, infrastructure identity, and all verified artifact digests.
- AC-002: An operation anchor and renewable lease prevent conflicting writers.
- AC-003: Recovery verifies immutable evidence and live identity before continuing from the earliest safe checkpoint.
- AC-004: Failure receipts preserve the last authoritative stage without claiming completion.
- AC-005: Registrations use `nebius-cxcli.soperator-registration.v3`; the fingerprint binds immutable target identity, observed source release/provenance, live object evidence, namespace, and release name, but not the mutable desired app version. Every other registration schema fails before discovery or mutation with a generic unsupported-schema error.
- AC-006: Onboarding proves the live Helm release and owned Kubernetes object graph equivalent to the verified official source rendered with live values. Only normalized digests, redacted evidence, and the provenance method are persisted; raw Helm values and Secret data are never written.
- AC-007: Destruction preserves its command-local receipt, exact approved resource disposition, operation IDs and safe recovery. An unrelated shared deployment record never blocks destruction.
- AC-008: One receipt-driven forward-only executor owns scheduling, release reconciliation, protected-state restoration and completion. Bounded transient retries belong to the smallest safe operation, never nested campaign loops. Invocation stop is distinct from unresolved durable intent. Keep exact transition IDs, phase order, ownership, immutable hashes and existing receipt schemas; historical failure counters cannot permanently exhaust a new invocation's allowance.

- AC-009: Actual local mutation contention reports the current owner; checkpoint existence is not process liveness. Generic render and deployment do not inspect shared backend lease/history records. Backend-only operation status and lease-wait interfaces are removed.
- AC-010: Local recovery writes remain atomic, bounded and owner-only; malformed command-owned checkpoints retain their originating command recovery contract. Backend lease renewal, takeover and conditional release are not part of generic deployment.

- AC-011: Contained subprocess supervision remains active across cancellation and parent death independently of remote leases. Local ownership lasts until contained writers stop. Dedicated command checkpoints and their command-specific locks remain unchanged. Input larger than a pipe buffer must be delivered completely despite slow child startup, while stdout/stderr drain concurrently and cancellation, deadlines and quiescence remain enforced.

#### Negative Criteria

- NC-001: Do not translate or accept superseded operation formats.
- NC-002: Do not silently replace a frozen snapshot after interruption.
- NC-003: Do not accept version equality, a release name, or an unverified chart label as sufficient onboarding provenance.
- NC-004: Do not persist raw live-discovered Helm values, Secret payloads, kubeconfig material, or credentials in registration, operation, discovery, or destroy evidence. Frozen project authoring inputs and render-owned files follow the generation contract; runtime secrets remain excluded.

#### Validation Method

Compare persisted receipts with the operation anchor, verified cache, and live
cluster identity.

#### Test Method

Run interruption, lease-loss, tamper, identity-drift, and recovery tests at
every mutating boundary.

#### Evaluation Method

Confirm replay neither repeats an unsafe mutation nor skips an unproved
postcondition.

<!-- /REQUIREMENT: REQ-017 -->

<!-- REQUIREMENT: REQ-018 status=active priority=P0 type=reliability -->
### REQ-018: Preserve protected cluster state during upgrade

#### User Story

As a cluster operator, I need upgrades to preserve accounting, controller,
storage, and shared-home state.

#### Acceptance Criteria

- AC-001: Protected PVs, PVCs, secrets, accounting state, controller state, VM-based NFS data disks, jail state, login identity, and MK8s identity are discovered and journaled before the upgrade commit. Nebius API evidence binds the exact MK8s cluster, NFS VM, attached non-boot data disks, and login allocation without persisting credentials or vendor response bodies.
- AC-002: Required PVs are set to `Retain`; exact protected PVC/PV bindings and secret identities are adopted by the target and independently verified.
- AC-003: The MK8s cluster, VM-based NFS service, NFS data disks, and protected storage are never recreated by a Soperator release upgrade. The admitted target must preserve the existing login Service and allocation; unexpected post-commit login identity or reachability drift is recorded as non-blocking degradation while the upgrade continues.
- AC-004: Missing, ambiguous, unsupported, or unclassified protected state rejects the operation before commit. Post-commit MK8s, NFS VM, data-disk, fencing, or journal ambiguity enters a read-only safety pause that revalidates until the same identity is proved.
- AC-005: Managed and onboarded targets use the same authoritative infrastructure receipt, and its digest is bound into admission, active intent, operation anchor, protected-data receipt, recovery journal, interrupted-operation recovery, and completion evidence.

#### Negative Criteria

- NC-001: Do not migrate VM-based NFS to Kubernetes as part of an upgrade.
- NC-002: Do not infer protected resources only from one fixed release layout.
- NC-003: Do not export secret values, SSH private keys, or plaintext accounting backups into generated reports.

#### Validation Method

Compare preimage journals and authoritative post-upgrade Kubernetes and Nebius
resource identities.

#### Test Method

Run protected-state, retention, adoption, NFS identity, postcondition, and
ambiguous-discovery tests for each strategy contract.

#### Evaluation Method

Confirm every protected object and data-disk identity survives the transition.

<!-- /REQUIREMENT: REQ-018 -->

<!-- REQUIREMENT: REQ-019 status=active priority=P0 type=reliability -->
### REQ-019: Gate upgrades with exact Slurm state

#### User Story

As a Slurm user, I need jobs and scheduling state preserved across upgrades.

#### Acceptance Criteria

- AC-001: Exact running-job, pending-job, hold, partition, and maintenance-reservation preimages are journaled with canonical records and fingerprints before the corresponding mutation.
- AC-002: Only operation-owned holds and reservations are added or removed.
- AC-003: The automatic default requeues and holds only authoritative eligible active batch jobs after an immediate stable-identity recheck. Completing, non-batch, unsupported, disappeared, and unproven jobs are reported and waited out; the default never cancels a job.
- AC-004: Job release occurs only after infrastructure, workload, storage, and product gates pass in the documented order. Observability verification is outside the upgrade transaction and cannot delay job release.
- AC-005: Every partition observed `UP` at the scheduling barrier is changed to `DOWN` under a full-record ownership journal, newly active partitions are incorporated until the barrier converges, partitions not initially active remain unchanged, and cluster-wide job discovery remains authoritative even when no running worker Pod is discoverable. The active operation Lease is re-proved immediately before each individual partition mutation; authority loss stops later mutations.
- AC-006: Upgrade provides two experiences over the same scheduling barrier. The wizard first displays the fixed required `pause-all-active` Partition Policy, then presents a structured Job Policy choice whose universal omitted default is guarded `requeue-hold-all`. The TUI is used only when the operator explicitly selects `interactive` and blocking jobs require a human per-job decision; its wait, temporary hold, requeue, requeue-and-hold, and explicit cancellation choices are journaled before mutation.
- AC-007: After the operation-owned maintenance reservation is present, cxcli repeats partition pause and authoritative job inventory until there are no newly active partitions or blocking jobs outside the frozen policy. Jobs that cannot be safely requeued follow wait-to-finish without another prompt. Presence of jobs, wait intervals, or TUI refresh intervals never terminates an upgrade after mutation begins.
- AC-008: Completion proves every operation-paused partition was restored from its exact preimage, every initially inactive partition and customer reservation retained its customer-owned fields, pre-existing holds remain held, and each affected job has an explicit verified outcome. Restoration releases only exact identity-bound applied holds while partitions remain `DOWN`, journals disappeared jobs as tombstones, rejects job-ID reuse or ambiguous legacy records, deletes only the operation-owned reservation, and restores partitions last.
- AC-009: Reservation admission accepts `scontrol -o` field values that contain unquoted whitespace, including login-shell time formats, while preserving complete values in a deterministic shell-safe canonical record. Malformed quoting, missing first-field identity, and duplicate field names remain fail-closed without exposing values.
- AC-010: Cross-version partition restoration reasserts `AllocNodes=ALL` when
  that unrestricted sentinel was present in the guarded preimage, even if the
  visible record already matches. Target partition projection omits null output
  sentinels, renders unlimited memory as explicit numeric zero, and preserves
  every finite partition memory value.
- AC-011: Scheduling action identities survive JSON persistence of list and
  tuple subjects. An interrupted install may correct only the proven omitted
  node-tuple hash defect when one exact frozen receipt proves installation,
  local and cluster journals match that operation, and valid gate records bind
  the same node scope. Preserve all
  original events in a bound repair record, change only affected action IDs,
  persist through the fenced journal owner, and retain accepted diagnostics.
  Unrelated identity changes and altered repair history remain failures.

#### Negative Criteria

- NC-001: Do not release all jobs, change initially inactive partitions, or delete pre-existing reservations indiscriminately.
- NC-002: Do not infer successful Slurm recovery from Kubernetes pod health alone.
- NC-003: Do not retain a job-policy mode whose post-start behavior is to fail merely because affected jobs remain.

#### Validation Method

Compare exact preimages with authoritative `scontrol`, `squeue`, and accounting
postconditions.

#### Test Method

Run mixed-job, partial-failure, ownership, interruption, and release-order tests.

#### Evaluation Method

Confirm user-owned scheduling state is preserved and jobs resume only after all
gates pass.

<!-- /REQUIREMENT: REQ-019 -->

<!-- REQUIREMENT: REQ-020 status=active priority=P0 type=observability -->
### REQ-020: Verify Soperator observability explicitly

#### User Story

As an operator, I need upgrade completion to depend only on Soperator product
readiness, with a separate command that verifies whether Nebius has ingested
the current controller's metrics and logs.

#### Acceptance Criteria

- AC-001: Upgrade completion verifies Flux or Helm revisions, SlurmCluster readiness, workloads, PVCs, NFS mounts, active checks, and expected product behavior, with no telemetry phase or observability credential lifecycle.
- AC-002: `soperator status CONFIG --target TARGET --verify-observability` reuses the exact validated live Kubernetes context; the flag implies the existing live-by-default status behavior and is incompatible with `--no-live`.
- AC-003: The verifier requires a project-scoped Prometheus sample no older than five minutes and a Loki record bound to the exact current Soperator Pod UID since that Pod started, polling for at most 60 seconds at 10-second intervals.
- AC-004: The verifier obtains one short-lived token from the operator's existing Nebius CLI profile, tries non-browser authentication first, and may retry through browser SSO only for an explicitly interactive TTY invocation.
- AC-005: Verification creates no IAM identity, role grant, static key, runtime service-account fallback, Kubernetes Secret, or cluster mutation. `NEBIUS_IAM_TOKEN`, cxcli delegated identities, and project runtime service-account credentials are not accepted as operator-verifier fallback.
- AC-006: Every completed verification attempt writes a separate atomic owner-only receipt containing only release, timestamps, safe counts, typed outcome, credential-source label, and hashed target, workload, endpoint, and query identities. It never changes or supersedes an install, upgrade, destroy, or recovery receipt.
- AC-007: Authentication, authorization, backend, and missing-evidence failures return nonzero with sanitized `authentication-unavailable`, `authorization-denied`, `backend-unavailable`, or `evidence-missing` status. They never change upgrade completion. Grafana dashboard validation remains a separate broader workflow.

- AC-008: For both deployment profiles derive vmagent remoteWrite.queues as the string form of 2 + floor(total configured worker capacity / 60), including ephemeral maximum capacity and excluding service nodes. Derive only in rendered values, preserve explicit overrides, resize on new generations and retain frozen values during recovery. Keep alerting enabled; effective arguments, current ingestion and rate-limit/backlog observations provide separate live evidence.

#### Negative Criteria

- NC-001: Healthy collector pods alone must not count as observability success.
- NC-002: Do not persist credentials, raw logs, provider responses, project IDs, cluster IDs, or customer data in verifier evidence.
- NC-003: Do not invoke the verifier automatically from install, upgrade, no-op reconciliation, or completed-operation recovery.

#### Validation Method

Query the validated live Kubernetes target and the direct Nebius Prometheus and
Loki read authorities with the existing operator identity.

#### Test Method

Run stale-sample, wrong-project, missing-series, missing-log, secret-redaction,
and successful-ingestion tests.

#### Evaluation Method

Confirm upgrade receipts are unchanged by verifier success or failure and each
explicit attempt records only its separate sanitized result.

<!-- /REQUIREMENT: REQ-020 -->

<!-- REQUIREMENT: REQ-021 status=active priority=P0 type=architecture -->
### REQ-021: Select upgrade strategy by capabilities

#### User Story

As a maintainer, I need new compatible upstream releases to work without
changing cxcli for every source and target version pairing.

#### Acceptance Criteria

- AC-001: The release catalog stores source-derived facts and capability fingerprints, not a target-support allowlist.
- AC-002: A separate strategy graph selects install, no-op, in-place, or protected-data-plane behavior from source and target capability contracts.
- AC-003: Any currently discoverable official stable source release may be onboarded when its facts can be proven.
- AC-004: Unknown source state or an unknown source-to-target strategy fails before mutation with actionable diagnostics.

#### Negative Criteria

- NC-001: Do not dispatch on exact release pairs or major-version fallback.
- NC-002: Do not mix source discovery facts with product support policy.

#### Validation Method

Inspect catalog provenance, capability fingerprints, and selected strategy
evidence.

#### Test Method

Require the canonical official-release matrix containing `latest`, `1.22.0`,
`3.0.4`, `4.0.5`, and `4.1.7`; reject incomplete opt-in matrices. Generate
capability evidence from every required selector, require each to classify
under a supported structural contract, then run strategy coverage tests for
every distinct edge.

#### Evaluation Method

Confirm a compatible future patch release succeeds without changing cxcli
source and an incompatible contract fails closed.

<!-- /REQUIREMENT: REQ-021 -->

<!-- REQUIREMENT: REQ-022 status=active priority=P0 type=cleanup -->
### REQ-022: Enforce one upstream delivery implementation

#### User Story

As a maintainer, I need one upstream-owned implementation so product behavior
cannot drift from a retained local copy or parallel lifecycle.

#### Acceptance Criteria

- AC-001: Verified official upstream artifacts plus the thin cxcli adapter are the only Soperator product delivery path.
- AC-002: Runtime, packaging, CI, and repository contents contain exactly one lifecycle engine and no duplicate product chart or bundled target lock.
- AC-003: Documentation, architecture images, CI, packaging, help, tests, and examples describe only the canonical lifecycle; every retained image is referenced and has one SVG source of truth.
- AC-004: Static gates verify the single-path tree; unavailable live gates are reported explicitly rather than represented as passed.

#### Negative Criteria

- NC-001: Do not package a second Soperator root command, product chart, lifecycle engine, release authority, or generated diagram format.
- NC-002: Do not permit more than the selected product-delivery authority.

#### Validation Method

Search the repository, inspect package contents, and verify the single-path
command and artifact surface.

#### Test Method

Run architecture guards, package tests, CLI help tests, focused unit and
integration suites, lint, documentation checks, and disposable live matrices.

#### Evaluation Method

Confirm upstream artifacts plus the thin adapter are the only Soperator product
delivery path.

<!-- /REQUIREMENT: REQ-022 -->

<!-- REQUIREMENT: REQ-023 status=active priority=P0 type=reliability -->
### REQ-023: Upgrade the jail and Soperator data plane in place

#### User Story

As a customer, I need any supported installed official Soperator release
upgraded on the same MK8s cluster while retaining my selected data, login
identity, and Slurm state without carrying packages or unselected rootfs
customizations into the new release.

#### Acceptance Criteria

- AC-001: A protected-data-plane upgrade keeps the Nebius MK8s cluster ID and Kubernetes `kube-system` namespace UID unchanged.
- AC-002: `/home`, `/data`, `/scripts`, `/models`, and `/opt/soperator-home` are mandatory retained path-specific PVC mounts. All relevant jail consumers, including bootstrap and check workloads, use the same retained bindings; image population and slot cleanup exclude their backing storage. Every fresh interactive `soperator upgrade` asks whether to keep additional existing data folders and accepts comma-separated absolute paths. Selection is additive: No, empty input, and unattended execution preserve configured protections. New paths bind their existing directories through retained PVC submounts without copying, moving, or an additional SSH pause. The authored configuration remains the immutable request. The frozen campaign carries the exact selection and publishes activated physical storage through a verified effective generation. Growth, final reconciliation, recovery, and subsequent upgrade intake consume that same state; accepted deployment evidence advances atomically only after successful promotion and final verification. Recovery never prompts again.
- AC-003: cxcli freezes the digest-pinned official target image, the admitted live rootfs identity, and the selected persistent-path boundaries without materializing source or target images in a reference scratch PVC. Selected persistent paths are customer-data ownership boundaries whose retained PVCs intentionally shadow target-image content at the same paths; outside those paths, the target image replaces the live rootfs in full. Unselected drift produces only content-free informational evidence and never blocks admission. After the forward-only commit begins, cxcli populates the exact empty passive slot once and inventories that materialization once as the canonical target-rootfs receipt.
- AC-004: The sole target jail authority is the digest-pinned image frozen from the selected official Soperator release. cxcli does not preserve, merge, export, publish, or accept a replacement image for unselected packages, system files, or rootfs customizations.
- AC-005: The effective target image is identical across rendered upstream values, passive population, operation identity, release intent, recovery journal, interrupted-operation recovery, and receipt. Preflight verifies the exact adapter-owned passive PVC, its provisioner and capacity, and has no `emptyDir`, jail-store, reference-copy, or implicit-StorageClass fallback. Immediately before the first passive-slot write, cxcli reasserts its lease and freshly proves that PVC empty, UID-matched, and unreferenced. Every Job is bound to the exact Kubernetes context, image, PVC, fencing epoch, and admitted workload identity; completed mutation stages require their checkpointed Job UID and admitted identity instead of being recreated. Pre-activation recovery repeats the passive consumer proof, while recovery after exact target-release adoption verifies the sealed materialization and completed Job evidence without requiring the now-active PVC to be unconsumed. Recovery reuses that sealed receipt rather than reclassifying under a new fence, and the original rootfs remains retained as recovery evidence.
- AC-006: Scheduling is held during the single-writer controller/accounting handoff. cxcli preserves the login Service, allocation, and reachability when the target permits, samples Service identity, ready EndpointSlices, and TCP/22 reachability at admission, including each resumed invocation, and treats any availability loss as advisory; SSH host-key material remains protected customer state.
- AC-007: Source-release reconcilers and Helm ownership are retired from the live cluster only after exact target ownership and product readiness are proved. Completed-transition replay treats an exact captured legacy HelmRelease as retired only when Kubernetes' optional-object lookup succeeds with no object; every other lookup failure remains fail-closed.
- AC-008: After mutation begins, recovery remains forward-only without rolling back the operation. Readiness polling retains its deadline; only positively identified transient safe reads receive bounded retries. Failed gates, permanent errors and exhausted read budgets stop the local invocation with unfinished intent and restoration evidence retained. A terminal product failure is recorded only for a typed terminal state of the one non-source main Soperator workload whose exact GVK, namespace, name, source identity, UID, and observed generation are frozen in the rendered operation graph. Observability verification is a separate explicit status action and is not part of this operation graph.
- AC-009: Lost or ambiguous fencing authority, protected PVC or Secret identity drift, conflicting writers, journal corruption, or an unclassifiable mutate-once outcome enters a non-mutating safety pause. The local command stops; a later invocation with the original frozen inputs and controls must re-prove safety before resuming.
- AC-010: Read-only discovery and staged render validation precede approval. After approval, the Lease and non-customer operation evidence remain an operation-owned preflight with no canonical config, Slurm, Service, release, or customer-PVC mutation. Only a sealed target-wins rootfs decision and exact admission receipt permit the active intent and recoverable canonical promotion transaction to establish the forward-only upgrade commit; passive-slot population is part of that committed transaction, never admission.
- AC-011: The protected-state strategy consumes the storage-neutral protected-storage receipt. Physical SFS is the canonical managed topology, and an explicitly identified VM-NFS topology is supported without requiring `externalNfs.server` for SFS-backed clusters.
- AC-012: Optional persistent paths must be normalized absolute real data directories in the admitted live rootfs and on the same physical SFS, without symlink traversal or overlap with generated, runtime, external-NFS, or other persistent mounts. A selected data subtree may descend from `/usr`, `/opt`, or `/etc`; its retained PVC deliberately wins over official-image content there. A backing directory inside a physical rootfs generation is allowed only under explicit retained-generation authority. Admission and recovery bind the filesystem, physical paths, generation, and PV/PVC identities; missing or changed backing fails before mutation instead of creating an empty replacement.
- AC-013: The two logical rootfs slots may reference successive physical generations. A physical generation containing a selected persistent directory remains retained independently of the current selection and is never an inventory, cleanup, or population target. Before reusing its logical slot, cxcli freezes a fresh same-filesystem directory and new PV/PVC identities, preserving the retained objects in rendered inventory. Only an exact unconsumed disposable generation may be recycled through the identity-bound cleanup stage. The preview discloses retained storage; automatic reclamation is outside this workflow.
- AC-014: Target Flux staging derives raw child HelmRelease identities from the exact rendered outer release, patches every child into the frozen cxcli graph before any child may reconcile, and keeps the namespace-owning child suspended after its first successful installation. Any foreign, already-reconciled, or terminating raw target inventory fails closed as contaminated recovery state; cxcli must not delete it or continue release staging automatically.
- AC-015: When an exact digest-pinned official subchart is structurally unrenderable, cxcli may suppress only that unusable child and deliver the same verified upstream declarative payload through lifecycle-owned post-Flux resources. The exception is bound to the exact known-broken digest and frozen source tree, requires content and path validation, preserves ordinary cleanup and verification ownership, needs no user-built image or republished chart, and fails closed for any other digest or source shape.
- AC-016: When an exact frozen official chart enables an uninstall-only helper whose resolved image is proven unavailable, cxcli may disable only that helper through the exact staged raw-child HelmRelease value path when the chart identity, version, digest, child identity, value path, and disabled behavior all match a closed known-broken contract. The compiled graph must preserve normal runtime resources and lifecycle cleanup ownership, require no user-supplied image or republished chart, and fail closed rather than applying the exception to any other artifact, child, or source shape.
- AC-017: Slurm discovery, maintenance, protected-state capture, and verification commands issued through the login workload execute inside its mounted customer jail rootfs. A controller fallback remains bounded and explicit, but a missing host-container Slurm configuration must not cause serial probe timeouts or be misclassified as cluster failure.
- AC-018: When the same exact frozen chart can create fail-closed validation webhooks and their dependent custom resources in one fresh Helm action, cxcli may set only that digest-bound staged raw-child HelmRelease install strategy to Flux `RetryOnFailure` with a bounded retry interval. The webhook remains enabled and fail-closed, successful chart resources stay in place between retries, no live object is patched out of band, and every other artifact, child identity, or source/install shape fails closed.
- AC-019: A bound upgrade may admit a new intervention generation for that install-strategy repair only at the exact running `apply-declarative-release` frontier, when the sole generated delta is the closed VM-stack post-render patch and the exact graph-owned live child is observed-generation current and `Stalled` from install retry exhaustion. Previously sealed rootfs, storage, maintenance, and ownership evidence remains immutable; any other frontier, live identity, status, or generated delta safety-pauses without patching the live release.
- AC-020: The admitted `apply-declarative-release` repair generation imports the authenticated completed predecessor transition prefix through legacy-owner quiescence and begins at the declared apply frontier. It reuses the predecessor's sealed rootfs recovery journal and must not create another passive-slot preflight, population, or materialization inventory. Recovery from an older cxcli replay that already started before this rule may cancel only the exact repair-owned, read-only duplicate inventory Job after proving its operation identity, read-only PVC mount, command, incomplete status, and absence of any population or cleanup stage; cxcli preserves that discarded replay's digest in the successor receipt and requires no user action.

#### Negative Criteria

- NC-001: Do not recreate MK8s, use the native destructive single-rootfs overwrite path, or run concurrent source and target controller/accounting writers.
- NC-002: Do not claim continuous scheduling, running-job, login-endpoint, Slurm command/control, or active-SSH-session availability; the supported contract is planned maintenance with best-effort reconnectable login access.
- NC-003: Do not copy unselected live rootfs changes into the target slot or treat them as customer data. Do not delete selected persistent data, worker-local data outside the declared jail contract, the retained source recovery rootfs, or customer-owned scheduling state.
- NC-004: Do not make login continuity capability, a transient endpoint outage, or a bounded observation timeout an admission or post-start failure condition.

#### Validation Method

Compare frozen source facts, target-owned filesystem decisions, protected-path
compatibility, protected identities, mount consumers, Slurm state, and
authoritative post-upgrade product evidence.

#### Test Method

Run capability, target-wins admission, single-pass passive materialization,
protected-path compatibility, wizard persistence, passive-slot recycle and
consumer-race, zero-copy retained-mount, slot-switch, single-writer,
direct-jail Slurm probe, exact-artifact adapter, login-continuity,
failure-injection, interrupted-operation recovery, and disposable
managed/onboarded upgrade tests.

#### Evaluation Method

Confirm the customer sees the same MK8s cluster, protected data and identities,
and Slurm history running the frozen target release without a remaining source
release owner.

<!-- /REQUIREMENT: REQ-023 -->

<!-- REQUIREMENT: REQ-024 status=active priority=P0 type=product -->
### REQ-024: Complete and safely retire a registered Soperator lifecycle

#### User Story

As an operator, I need installation, onboarding, inspection, upgrade, recovery,
and destruction to form one closed lifecycle that preserves customer storage
and exposes one coherent command and implementation path.

#### Acceptance Criteria

- AC-001: A fresh managed install can upgrade through the common reconciler with its canonical physical-SFS topology and no VM/NFS-only configuration requirement.
- AC-002: Onboarding rejects ambiguous, incomplete, non-official, or live-manifest-drifted installations before writing registration state.
- AC-003: Destroy inventories the exact cloud cluster, every node group including scaled-to-zero groups, actual worker attachments and template SFS references without Kubernetes access. Preview and execution print DESTROY/PRESERVE sets; unapproved previews refresh. Provider project, group parentage and filesystem IDs define approval, not workload or instance turnover.
- AC-004: Both ownership kinds submit one SDK cluster-delete request per approved attempt, with no Kubernetes, Helm, Flux, finalizer, or Kubernetes Lease prerequisite. Default behavior deletes verified cluster-owned PVC disks, attached or detached, and dedicated GPU clusters after worker absence; --preserve-pvc-disks retains PVC disks. Default behavior preserves SFS. --delete-sfs deletes only explicitly confirmed attached SFS after cluster, node-group and worker absence, zero remaining attachments and reference revalidation. Shared, unknown or deletion-protected SFS blocks before cluster deletion; cxcli never disables protection.
- AC-005: Before approval cxcli freezes and validates the final remaining project generation. After cloud postconditions, a fresh saved normal Terraform plan against that generation may only delete frozen owned ancillary identities and reconcile expected SDK-caused absence and outputs. No create, replacement, resource update, unrelated deletion or unrelated drift is allowed. Verify authoritative state, then transactionally publish config/generated files and clear selected accepted deployment evidence.
- AC-006: Soperator command flags, mutation APIs, retry supervision, and target-wins rootfs admission are owned exclusively by the five-command lifecycle and common operation engine. No reachable legacy rootfs classifier, historical source-image authority, or alternate source-owner adoption path remains.
- AC-007: Unit and architecture tests cover the sole root group, all five commands, both ownership kinds, both storage variants, install and interrupted-install recovery, onboarding provenance, canonical discovery reruns, upgrade recovery, destroy interruption at every frontier, status recovery output, and package boundaries.
- AC-008: Destroy resolves immutable cluster identity from explicit registration or the exact deployment handoff without kubeconfig or Terraform output resolution; conflicting identities fail closed. Cloud reads verify the selected project and node-group parentage.
- AC-009: A cloud-only inventory deduplicates all SFS IDs across arbitrary mount tags, templates and actual worker attachments. Deletion checks other cluster templates, stopped/running instance specifications, provider attachment lists and remaining config references. Empty SFS sets are valid; VM-NFS and other independent storage remain outside --delete-sfs.
- AC-010: Destroy shows its current phase with a spinner and elapsed time on stderr from startup through inventory and teardown, before any potentially slow preflight operation. Non-terminal output uses bounded plain progress records. Deletion wait progress identifies the approved cluster, GPU cluster, disk or filesystem by its resource ID; provider operation IDs remain recorded for polling and recovery. Inventory and confirmation remain readable with progress paused, and rendering failures do not affect authority, storage checks, or mutation order.
- AC-011: All cloud lists consume complete pagination; incomplete reads, unknown ownership and authentication errors cannot become empty inventories or absence. Immediately before submission revalidate approved group and SFS scope while permitting normal worker turnover within unchanged groups.
- AC-012: The command-owned local destroy-v1 receipt freezes disposition, exact IDs, config/generation digests and approval. It persists request intent before SDK submission and accepted operation IDs before polling. Matching destroy recovery retains its exact controls and never blindly replays ambiguous submissions. Generic render/deploy do not consult this command history.
- AC-013: Destroy accepts explicit --yes authorization or checks terminal interactivity with progress paused at the exact confirmation boundary and passes that authorization to the execution engine. Resuming presentation must not invalidate an accepted interactive confirmation. The engine rejects noninteractive first approval without --yes and a mismatched exact inventory phrase; already-approved recovery remains receipt-owned.
- AC-014: Destroy distinguishes provider refresh observations from planned mutations. Same-address/type/ID refresh updates are admissible only for exact resources approved for deletion; retained-resource drift, changed or unknown IDs, and unapproved planned mutations remain blocked. Reconciliation admits expected absence and same-identity refresh only for still-pending ancillary deletions. Terraform subprocess output and raw failure diagnostics are not exposed by the destroy workflow. Materialized execution caches use one canonical filesystem root so operating-system temporary-path aliases cannot invalidate saved-plan containment; descendant symlink and file-identity checks remain enforced.

- AC-015: Freeze complete, exact project/cluster/CSI/PVC disk provenance and GPU identities from node templates, workers and selected managed state. Cover every node group, including zero-node groups. Exclude boot, instance-managed, independent VM-NFS and unrelated disks. Shared GPU references, unknown ownership, outside consumers, protected disks and active disk locks block execution before cluster deletion.
- AC-016: Use SDK GPU/disk deletion with per-resource intent, operation correlation, terminal retry confirmation and typed absence. Bound disks to eight in-flight operations with a single receipt writer. Recheck ownership/detachment before submission and all cloud postconditions on later resumes. Terraform only reconciles SDK-caused GPU absence; never delete/recreate an SDK resource through Terraform. Unexpected unapproved resources require separate resolution.
- AC-017: v4 approval includes GPU/PVC disposition, provenance and count-inclusive confirmation. Only v4 receipts are supported, for local previews and backend records in every status, including completed. No older-receipt import, rebuild, archival, migration or execution recovery exists. Rebuild only unapproved v4 local previews; existing-orphan cleanup remains outside scope. PVC deletion is an explicit whole-cluster policy independent of unavailable Kubernetes reclaim policy.

#### Negative Criteria

- NC-001: Without --delete-sfs never delete physical SFS. Never delete shared, protected or unapproved SFS, independent VM/NFS resources, or unrelated project resources. Node-local ephemeral data is not retained storage.
- NC-002: Never finalize destroy when cloud postconditions, Terraform reconciliation or publication are unproved. Storage disposition cannot change during command recovery. Unsupported local receipt schemas fail before cloud work; no migration or shared backend receipt lookup is provided.
- NC-003: Do not add another local product chart, migration campaign, controller bridge, scaling engine, verifier workflow, or version-pair command family.

#### Validation Method

Inspect CLI routing, immutable receipts, provider call identity and ordering,
generated Terraform plans, redacted registration evidence, and negative source
searches.

#### Test Method

Run focused registration, provenance, storage, install, upgrade, destroy,
recovery, CLI, documentation, and architecture suites; then run repository
quality gates. Treat disposable managed and onboarded live trials as a separate
authorization and evidence boundary.

#### Evaluation Method

Confirm a registered target has one supported path from entry through safe
retirement, with the selected cluster gone, the approved storage disposition verified, and
no stale Soperator mutation surface remaining.

<!-- /REQUIREMENT: REQ-024 -->

<!-- REQUIREMENT: REQ-025 status=active priority=P0 type=reliability -->
### REQ-025: Persist project state and bootstrap credentials transactionally

#### User Story

As an operator, I need cxcli to recover interrupted project-file promotion and
credential bootstrap without mixing generations, losing my edits, duplicating
credentials, or persisting secret material.

#### Acceptance Criteria

- AC-001: A multi-file project update stages and fsyncs one immutable generation containing every render-owned write and deletion tombstone, compare-and-swaps the complete admitted target set, commits one owner-only metadata record, and recovers committed materialization before any cxcli reader consumes the affected files. Upgrade and destroy advance config plus the complete generated postimage together while preserving lifecycle reports.
- AC-002: While a committed generation is incomplete, recovery accepts only exact old or committed-new digests; a concurrent operator edit, unsafe link, ownership mismatch, or unknown file state fails without overwrite. After materialization completes, the journal is historical and does not fence later operator edits or a later generation; an active operation must separately prove that its admitted generation is still current before continuing.
- AC-003: IAM bootstrap journals only operation-owned credential resource IDs, ownership evidence, digests, phases, and compensation status. Private keys, tokens, access secrets, payload values, and provider response bodies are never persisted or logged.
- AC-004: Credential delivery records a secret-free destination and operation marker before delivery. Recovery independently classifies the destination as delivered, not delivered, or ambiguous; delivered or ambiguous credentials are retained, and only a monotonic, independently proved not-delivered outcome compensates exact operation-created credentials in reverse order. Destination absence alone is ambiguous because it cannot distinguish never-delivered from delivered-then-removed. Failed compensation or ambiguous delivery blocks another credential creation for that scope; reusable service accounts, groups, memberships, permits, and roles are retained.
- AC-005: User-facing documentation and relevant help identify the product as SecretStash while explicitly retaining `mysterybox` as the Nebius CLI, API, Terraform, configuration, and manifest service identifier.
- AC-006: Canonical runtime authentication automatically and idempotently ensures one strictly owned project service account with exactly project `admin`, sufficient for rendered IAM and node service-account attachment, without tenant-scoped permits. Ordinary project commands, including `create`, validate the cached key before IAM reconciliation and reuse a healthy account and key without operator authentication. Confirmed managed group, membership, or project-role drift is reconciled under available operator IAM authority, bound to the cached service-account ID, then verified with canonical runtime credentials before downstream writes; no separate `auth` command is required. When canonical IAM reads return typed permission denial, operator read-only inspection must prove managed drift before repair. Establish the desired role before deleting exact obsolete managed project permits. Foreign scope, membership, identity, or unclassified provider failures fail closed without automatic repair. Deployment preview and CI imports enforce the identity contract read-only. Missing operator authority produces a sanitized actionable failure without credential rotation or duplicate identity creation. SDK token-refresh timeouts, including empty-message Python timeout exceptions, have concise credential-free diagnostics during ordinary commands; bounded retry ownership and terminal failures remain with the SDK and calling operation.

#### Negative Criteria

- NC-001: Do not use sequential best-effort writes, rollback over a foreign edit, or store file contents in a transaction journal.
- NC-002: Do not delete pre-existing or ambiguously owned IAM resources, and do not trigger credential compensation from explicit observability verification or unrelated post-commit failures.
- NC-003: Do not rename or alias executable `mysterybox` identifiers.

#### Validation Method

Inspect transaction journals, filesystem promotion ordering, IAM call identity,
and user-facing terminology.

#### Test Method

Inject failure at every stage, fsync, commit, materialization, credential
creation, delivery, and compensation boundary; test concurrent edits, unsafe
links, secret redaction, and exact rerun convergence.

#### Evaluation Method

Confirm every successful operation exposes one complete generation and every
interrupted operation resumes without duplicating credentials or exposing
secret material.

<!-- /REQUIREMENT: REQ-025 -->

<!-- REQUIREMENT: REQ-026 status=active priority=P1 type=architecture -->
### REQ-026: Keep one modular and continuously verified CLI implementation

#### User Story

As a maintainer, I need command wiring, application orchestration, domain
state, and external adapters separated and guarded by repository quality gates.

#### Acceptance Criteria

- AC-001: `cli.py` is the Typer composition root and output/exit mapper. Command modules depend on application services, which depend on domain and adapter interfaces; leaf and service modules never import `cli.py`.
- AC-002: Extracted behavior has one canonical implementation with no forwarding compatibility wrappers or duplicate orchestration loops.
- AC-003: CI checks every supported Python minor, Ruff lint and formatting, diff hygiene, static typing, branch coverage, the full offline suite, and the same exact installed-wheel CLI contract under Python 3.12, 3.13, and 3.14. The explicit integration lane selects marked tests across the complete test tree, while public release downloads remain opt-in.
- AC-004: The measured global branch-coverage floor is nondecreasing, and safety-critical supervisor, ownership, protected-storage, transaction, and compensation modules meet a separately enforced focused branch-coverage floor. Proposed coverage floors may only rise, the package mypy error ceiling may only fall, and the Ruff-format offender allowlist may only shrink relative to the merge base.
- AC-005: One canonical machine-readable contract covers the root, every public command group and leaf command, global options, arguments, option spellings, requiredness, defaults, choices, repeatability, visibility, ordering, and selected help clauses. Help descriptions and example comments match the current execution path, including conditional requirements, target selection, prompts, recovery, and local versus remote effects. Displayed example commands parse with the registered interface after operators substitute placeholders; explanatory punctuation and comments stay outside command text. Hidden internal commands are recorded separately and never appear as public commands.
- AC-006: Installed-wheel verification imports only the isolated artifact, renders every public help surface, exercises the version path, and reaches every public callback through a deterministic fail-before-external-effect case. Status smoke verification independently proves the injected configuration-read boundary was reached exactly once, requires the sanitized failure diagnostic and overall Error with nonzero exit, and rejects disclosure of the injected exception text.
- AC-007: Every Python-backed Make target enters one shared uv environment boundary. The committed lock must be current, a validated whitespace-free custom `VENV` maps to `UV_PROJECT_ENVIRONMENT`, lock inspection and exact locked synchronization use the selected supported Python without automatic downloads, synchronization is serialized per resolved environment, and isolated wheel-build dependencies are hash-constrained by the same lock without a second installer authority.

- AC-008: Commands explicitly presented for copying and running use one shared terminal style: bold dark text on a light-gray background covering only command text. Apply the style to workflow handoffs, access and recovery commands, and public help examples. Keep labels, commentary and help separators outside command highlighting; preserve complete command text, quoting, runtime soft wrapping, normal redirected plain output and existing color-disable behavior. Saved command/report artifacts remain free of terminal styling.

#### Negative Criteria

- NC-001: Do not perform a big-bang rewrite or retain an alternate legacy command path.
- NC-002: Do not weaken gates with blanket type ignores, invented coverage exclusions, or network/live dependencies in required pull-request CI.
- NC-003: Do not maintain a second Soperator-only CLI contract or accept a same-change baseline edit as proof that a quality regression is allowed.
- NC-004: Do not retain a timestamp stamp, standalone pip launcher, public development extra, or compatibility install path; do not select an unrelated active environment, download Python automatically, pass a whitespace-containing or otherwise unsafe environment path to uv, clear an unrecognized environment path, resolve build backends outside the reviewed lock, or leave the uv prerequisite undocumented.

#### Validation Method

Inspect import direction, command registration, locked uv synchronization,
coverage reports, workflow matrices, and isolated wheel behavior.

#### Test Method

Run architecture/import guards, command snapshots, unsafe-path, stale-lock,
environment-drift, concurrency, and missing-tool fault-injection tests, then
Ruff, mypy, branch coverage, the full offline suite, and wheel smoke on the
supported Python matrix. Soperator CLI fixtures retain the real frozen-release
interface and the source metadata consumed by admission, with external release
resolution replaced at its boundary and network blocking kept active.

#### Evaluation Method

Confirm each command reaches one service path, every required repository gate
rejects a deliberate contract regression, and every Make/CI consumer runs from
the reviewed lock while the wheel lane installs only the exact built artifact.

<!-- /REQUIREMENT: REQ-026 -->

<!-- REQUIREMENT: REQ-027 status=active priority=P1 type=security -->
### REQ-027: Authenticate SSH hosts before privileged day-2 operations

#### User Story

As an operator, I need SSH jump-host and WireGuard day-2 commands to authenticate
the selected VM before they invoke privileged remote helpers.

#### Acceptance Criteria

- AC-001: `ssh-jumphost` and `wireguard` accept `--ssh-known-hosts-file PATH`; when omitted, the path is `<project>/generated/ssh_known_hosts`.
- AC-002: The selected file must already exist and contain an independently verified key for the target host. Missing, unknown, or mismatched identities fail before any remote helper runs.
- AC-003: SSH uses strict host-key checking and the selected cxcli trust file without falling back to the user's global known-hosts database.
- AC-004: The cxcli-managed deployments-root `.gitignore` excludes each project `generated/ssh_known_hosts` file while preserving the rest of the generated deployment contract.

#### Negative Criteria

- NC-001: Do not use `accept-new`, automatically trust `ssh-keyscan` output, or expose an insecure host-key bypass.
- NC-002: Do not store host keys, customer addresses, or trust files in public examples, package data, logs, receipts, or committed generated artifacts.

#### Validation Method

Inspect SSH argv construction, default path resolution, managed ignore rules,
CLI help, and operator documentation.

#### Test Method

Inject exact, missing, unknown, and mismatched trust files and prove failures
occur before the VM-local `sudo` helper is invoked.

#### Evaluation Method

Confirm both SSH-backed command families use one trust-policy implementation
and that no first-use trust path remains.

<!-- /REQUIREMENT: REQ-027 -->

<!-- REQUIREMENT: REQ-028 status=satisfied priority=P0 type=product -->
### REQ-028: Manage ordinary MK8s apps on Soperator clusters

#### User Story

As an operator, I need the normal MK8s app workflow on a Soperator cluster,
with optional local Grafana and additional telemetry independent of upstream
Soperator observability.

#### Acceptance Criteria

- AC-001: Fresh install keeps the fixed MK8s, SFS, and upstream Soperator core plus configuration-derived platform/integration prerequisites, and defaults to Public Nebius Grafana. Optional ordinary apps are selected only afterward through `component add`; fresh install has no optional-app questions, generic observability wizard, or `--app` option. Resume preserves the saved frozen operation and selections.
- AC-002: Later local Grafana selection retains cxcli dashboards and authorized datasources and selects its Gateway dependency without the additional agent. Additional telemetry requires an explicitly enabled collector row on the exact Soperator target and never selects Grafana. Enabling additional telemetry without selecting its collector fails with component-add guidance; collector removal clears the target telemetry switch. Ordinary Kubernetes signal summaries follow effective collector configuration, separately from upstream Soperator metrics/logs. Mixed targets never share implicit collector selection.
- AC-003: Normal component add/remove author ordinary apps before or after installation without changing unrelated configuration. Render and deploy process the complete desired configuration through REQ-031; standalone app operations preserve their declared field ownership.
- AC-004: Ordinary app-only changes preserve unrelated infrastructure, upstream resources and lifecycle artifacts. Intentional protected configuration edits are admitted through the shared deployment planner; unknown resource or Helm ownership collisions fail before mutation. Mixed targets retain exact isolation. Ownership inventory requires Helm 4 before cluster reads and includes all release statuses; unsupported client versions fail rather than silently omitting pending or uninstalled releases.
- AC-005: App-only deployment does not cause unrelated Terraform changes or Slurm maintenance. Configuration removal does not imply live app pruning or uninstall.
- AC-006: Fresh installation orders ordinary prerequisites before the protected graph; Soperator upgrades and recovery preserve unrelated apps. Generated publication is atomic and detects concurrent preimage changes.
- AC-007: Upstream metrics and logs use the selected project and region. Output distinguishes configured export from verified ingestion. Existing onboarded upstream Grafana is not automatically removed or adopted.

- AC-008: Soperator observability inherits the selected verified upstream release's exporters, collectors, metric/log storage and supported explicit native values. Remote Nebius Grafana remains the default; the bundled upstream Grafana is disabled by one documented post-render exception. The optional cxcli Grafana app remains independently selectable.
- AC-009: Soperator values use the native `observability` subtree validated against the frozen umbrella chart. Obsolete `soperator-dcgm-exporter` inputs fail before mutation, including saved defaults without explicit-value metadata. Required Nebius identity, credentials, placement and jail bindings remain protected.
- AC-010: Upstream Soperator owns GPU export. Its target does not enable the separate GPU Operator exporter or receive generic exporter node-label reconciliation; ordinary MK8s behavior remains unchanged and metric-source discovery is target-local.
- AC-011: An explicitly selected additional Soperator collector defaults to application logs, metrics and traces with infrastructure metric collection disabled. Explicit signal/custom-target settings remain honored; selection never changes upstream telemetry or selects Grafana. Configuration, readiness and verified ingestion remain distinct.

#### Negative Criteria

- NC-001: Do not ban ordinary app changes solely because a Soperator marker exists.
- NC-002: Do not import upstream dashboards, create a Public Grafana account, or introduce compatibility wrappers or new approval engines.
- NC-003: Do not treat app omission as an instruction to uninstall a live release.

#### Validation Method

Inspect target-aware selections, generated object identities, protected artifact
preimages, execution ordering, manifest paths, and cluster identity checks.

#### Test Method

Exercise default and explicit app selections, mixed targets, scoped render and
apply, collision and drift rejection, atomic publication, and lifecycle
preservation using offline fixtures and actual emitted Kubernetes resources.

#### Evaluation Method

Confirm ordinary app commands share the existing MK8s workflow while every
protected mutation remains owned by the Soperator lifecycle. Keep source,
package, and live evidence separate.

<!-- /REQUIREMENT: REQ-028 -->

<!-- REQUIREMENT: REQ-029 status=active priority=P0 type=product -->
### REQ-029: Defer disruptive checks with validated Soperator handoff

#### User Story

As an operator, I need installation and upgrade to defer competing checks while
infrastructure changes and prove health before customer handoff.

#### Acceptance Criteria

- AC-001: Enable upstream ActiveChecks/controller defaults, preserve operational prolog/epilog work and platform exclusions, and remove ineffective wait toggles. The explicit fast-dev-test deployment profile may permanently disable the reviewed diagnostics inventory while preserving setup and operational hooks; standard retains full diagnostic coverage. Fast Dev/Test admission must not depend on a release-version or exact chart-bundle allowlist: compatible official upstream releases remain usable without a cxcli update, including changed disabled diagnostic bodies. Verify effective suppression controls, retained setup, operational hooks and dependency integrity against the frozen source. Unrelated custom Slurm settings are allowed when the effective hook contract remains valid; conflicting execution controls still fail. Temporarily suppress verified passive diagnostics only on fully isolated maintenance nodes; unsupported suppression keeps passive diagnostics enabled with an explicit warning. Changes to saved disabled policy are explicit in the reviewed upgrade target.
- AC-002: Frozen source-derived policy separates bootstrap and scheduling-dependent work. Temporary overrides never replace steady configuration and survive staged Flux reconciliation and interruption. The exact umbrella inline values consumed by Helm and its matching values evidence must carry the same effective policy; changing an unused ConfigMap is insufficient. The orchestration-only umbrella delegates readiness to the complete staged child and product gates, so its own Helm install and upgrade must not wait for suspended children.
- AC-003: Source checks quiesce before scheduling disruption, without name-based cancellation. Suspension requires current controller generation acknowledgement; handoff resolves the same exact rendered umbrella identity as staged apply. Customer admission remains blocked through fresh acceptance, including initial installation. Validate the operation-owned unlimited reservation using Slurm's native 365-day duration representation while retaining active-state, full-resource, authorization and fingerprint checks. Initial restoration, interrupted recovery and completed-postcondition verification must all include the checks reservation or its exact durable release proof, together with initial partition states; generic infrastructure health cannot complete that combined transition. Include the auxiliary reservation CronJob in suspension, running-job quiescence and verified restoration, with the same active jail claim as native checks. Initial product readiness waits within the existing checks deadline for controller-created CronJobs after bootstrap dependencies settle. Missing chart-owned objects or conflicting scheduling, suspension, ownership and storage fail immediately with resource/field diagnostics; completed maintenance verification remains strict.
- AC-004: Native templates with explicit reservation/node allocation cover every required worker. Worker pinning uses supported Slurm directives while retaining the upstream diagnostic body and entrypoint; projected script contents are part of executable identity. Recovery retains exact terminal obsolete submissions and requires fresh correctly allocated jobs, without importing their results or reassigning expected workers. Validate the upstream cluster Munge Secret name separately from its Pod volume name, retaining exact key paths and file permissions. All installed and registered NodeSets mount the upstream runtime scripts and node-local job metrics directory. Use the upstream GPU example scratch allowance in GPU wizard defaults and fill missing GPU allowances while preserving explicit resources. An initial native import interrupted by a proven worker storage eviction requires an exact sealed resource delta, identity-bound orphan retirement with failed terminal accounting and boot-bound durable kubelet eviction evidence plus the pinned native passive checker's over-limit storage report, retained evidence and fresh acceptance under the original reservation. A missing-mount repair during initial acceptance preserves failed evidence, quiesces only the exact recorded probe and its attributable Slurm submissions, proves script contents and executable permissions, restores only attributable prolog drains, and replays apply and fresh acceptance under the original reservation. Acceptance proves rendered executable/script authority, Kubernetes Job identity, all allocated worker GPUs where required, every Slurm job terminal success and target generation; stale, missing, cancelled, partial or ambiguous evidence is insufficient.
- AC-005: After required readiness and the selected extended-acceptance outcome, close temporary check-user authorization and durably release the exact operation-owned reservation before restoring recurring schedules, so native catch-up jobs can select workers. Keep existing customer job holds until steady policy and product readiness are verified. Resume intent/completion interruptions without recreating a released reservation or rewinding infrastructure. Preserve and authenticate any earlier native zero-eligible-node submission failures; recovery must run fresh upstream checks under the retained reservation, never import successful results or patch check status. Retain terminal native scheduled-check history without treating it as service Pod readiness or successful check acceptance; require exact Kubernetes ownership and the matching Helm-owned ActiveCheck, while current required results remain gating. Report pending restoration and stage progress. Failures before release retain maintenance; failures after release retain durable handoff evidence and existing job holds.
- AC-006: Bind Enroot user-namespace permission to its exact executable through the existing upstream Security Profiles Operator. Defer the profile until the upstream API is ready and require owner-bound per-node installation before the Slurm workload, retaining the host-wide restriction. Recover a proven initial namespace denial only through a sealed additive adapter delta, exact native job and Slurm identity, boot-bound kernel denial evidence, preserved reservation and fresh acceptance.
- AC-007: GPU NodeSets use the upstream Docker Supervisor and read-only daemon settings, with a pod-local disposable image cache within their declared storage allowance. Reject conflicting runtime bindings. An initial native Docker missing-socket failure may receive only an exact sealed values repair bound to terminal native jobs on every GPU worker, unchanged allocations, native ownership and pinned runtime configuration. Preserve protected storage, failed evidence and the original reservation, then replay apply and fresh native acceptance. Recover only idle drains attributed to those exact handled failures after native runtime verification. Quiesce an attributable queued bootstrap probe, cancel only its pending Slurm submission, and resume the same native Job with a new Pod after drain restoration and check authorization. Unrelated drains, hardware faults, active work and unproven identities stop recovery.
  A resumed initial Docker check may replace one terminal submission per worker
  only when authenticated accounting and bounded native output prove a registry
  connection reset before diagnostic execution. Retain the failed Job, output
  digest and exact reaction; verify unchanged native policy, worker runtime,
  private storage, idle inventory and maintenance ownership before restoring only
  its attributed drain. Persist intent before effects and give the replacement a
  distinct deterministic identity. Repeated or unclassified failures remain
  failures; never import success, delete evidence, or reopen user scheduling.
  Persistent jail compilation must preserve private runtime submounts and reject
  conflicting names or paths. A separate initial storage-isolation repair may
  add only the omitted private cache mount when existing Docker bindings,
  terminal native allocations, an attributed connection failure and a shared
  metadata directory are proven. Retain each predecessor outcome, including
  peer timeouts; none satisfies successor acceptance. Require newly reconciled
  Pods with private storage and a ready Docker API before attributed drain
  restoration, preserving the original reservation and protected storage.
  Bind the application bundle transition to the cluster-sealed exact delta,
  preserve its predecessor digest, and reject unrelated renderer drift. Unused
  optional rootfs-retention metadata must not change the saved bundle identity.
  Recheck replacement runtime identities immediately before clearing an attributed
  drain.
  Preserve full sealed repair receipts across execution-cache recovery. Permit
  only a timeout's one-way termination-signal finalization while retaining exact
  job, allocation and handled-failure identity; keep both observations separate
  from fresh successful acceptance.
- AC-008: Diagnostic acceptance requires actual native PASS evidence in addition to successful Slurm accounting. Validate enabled health-checker tests and subchecks, complete extensive-check phase coverage, and the native Docker NCCL validation result on every allocated GPU. The native CUDA command-only report requires all four exact sample commands enabled and successful; its null subchecks do not permit missing, duplicate or substituted tests. Bind bounded output reads to the exact worker, job name and job ID; persist a digest and reverify it. Missing, ambiguous, skipped, changed or non-PASS reports cannot satisfy acceptance even when the wrapper exits zero.
- AC-009: The verified managed eight-H200, 128-vCPU preset exposes truthful physical CPU topology and all hardware CPU IDs while Kubernetes independently enforces the configured CPU-time quota. Do not convert that quota into core specialization that prevents upstream full-hardware diagnostics. Preserve resource and storage allowances and reject conflicting custom topology. An initial native affinity failure may receive an exact sealed topology-only values delta, retaining failed evidence and replaying fresh acceptance. Under root-only quiescent maintenance, permit only the corresponding physical core and CPU reservation totals to change during physical topology correction. A subsequent generated-mask removal preserves the complete original reservation and requires full effective hardware CPU access before fresh acceptance.

- AC-010: Maintenance entry applies the existing selected job policy and TUI actions, retaining the guarded requeue-hold-all default, eligibility checks, wait fallback, timeout supervision and exact hold ownership. Pending or held jobs may remain; all affected allocations, running steps and completion cleanup must clear before passive suppression or disruptive work. Diagnostics never substitute cancellation, requeue or universal waiting for user decisions. Deploy evaluates job policy in the Slurm workload namespace, independently of Helm release storage.
- AC-011: Admission remains independently closed through passive restoration, fresh applicable diagnostic acceptance and recurring-schedule restoration, including declarative reconciliation and restart. Release the diagnostic reservation separately from user jobs. A durable final admission authorization permits only owner-bound hold and partition restoration; pre-existing and independently changed holds remain protected.
- AC-012: Freeze source and target diagnostic execution contracts independently, record effective temporary phases and prove their effects. Restore partially applied passive suppression before claiming an enabled fallback. Verify the rendered desired scheduler, all operational hooks and mounted policy in fallback and at final or resumed completion; historical acceptance alone does not prove current restoration. Reconcile owned drift through the existing forward-only recovery path without rewinding completed infrastructure. Unknown authority, isolation or failed health blocks only the dependent transition. Fresh passive evidence distinguishes applicability, missing results and successful measurements; wrapper exit zero or skipped checks cannot satisfy required measurement coverage. Freeze measured versus supporting-only proof roles with the reviewed source: GPU health, boot disk and memory require positive native measurements when applicable; GPU-busy and optional NVMe results are supporting evidence because their native output cannot prove every underlying query succeeded. Surface supporting limitations without converting them to PASS or waiving required fresh native active acceptance.

- AC-013: Fast Dev/Test deployment installs the dependency graph once, performs required bootstrap once, restores ordinary-user scheduling through journaled transitions, and verifies service readiness plus bounded ordinary-user Slurm jobs across initially active workers. Pre-restoration readiness verifies active registration and responsiveness while maintenance still protects scheduling; final ordinary-user admission rejects drain and maintenance states. Fast day-2 acceptance preserves completed bootstrap receipts and verifies current passive policy and worker coverage without demanding evidence from waived diagnostic jobs; final ordinary-user smoke remains mandatory. Standard diagnostic hook coverage remains required. Preserve exact ownership checks before restoring scheduling. Disable only reviewed diagnostic jobs/triggers and passive diagnostic entries. Unknown or custom suppression contracts fail before cloud mutation. Waived coverage never counts as passed. Preserve leases, identity, protected storage, ownership, job safety and immutable recovery.
- AC-014: Freeze deployment profile, exact transition graph and coverage in operation and acceptance evidence. A no-op performs bounded readiness without replaying setup. Resume only the original frozen profile; unfinished older operations require their original executable. Smoke receipts bind the generation, user, partition, exact active worker inventory, job and terminal accounting success; cancellation is restricted to recorded owned probes. Ephemeral maxima are not awakened for acceptance; at least one initially active worker is required.
- AC-015: Persist structured phase and subprocess timings outside the disposable cache, with attempts, categories, outcomes and parent relationships. Fresh two-worker fast deployment retains a fifteen-minute performance target in structured timing evidence, not a failure deadline. Console output reports elapsed duration, outcome and the timing-report path without a target-overrun warning. Required work must finish, and nested phases must not be double-counted. Reject invalid target selection before creating timing reports. A timing-publication failure must not replace an existing deployment error or leak timing context; publication failure without a prior error remains visible.
- AC-016: An expired, cancellation-pending fast smoke intent with uncertain submission and no recorded job ID or proof may be retired only after explicit exact-attempt operator confirmation, successful current queue and accounting absence checks, unchanged receipt identity and valid operation authority. Append a generation- and attempt-digest-bound outcome-unknown disposition while preserving the complete original intent. Publish retirement through the existing shared checkpoint before replacement submission. Require a distinct fresh passing smoke and check retired attempts for late active work before submission, acceptance and completed verification; never infer non-submission or success from absence. Declined, noninteractive, stale, malformed, mismatched or unowned requests cannot retire an attempt.

- AC-017: Restrict timing-publication checkpoint reduction to an explicitly frozen fast-dev-test deployment. Timing report publication for a selected fast-only Soperator target must not trigger execution-recovery capture or deployment checkpoints. Standard, unspecified, disabled, other-target and mixed-target execution retain ordinary behavior. Real lifecycle receipts, atomic local checkpoints, current authority, required setup and readiness remain mandatory. Persist private atomic timing reports and preserve publication failure semantics; validate call reduction independently from any live duration claim.

- AC-018: Explain Standard versus Fast Dev/Test with compact target-scoped summaries of saved creation intent, successfully published Soperator renders, and admitted frozen deployment inputs. Standard preserves native defaults and valid configured overrides; Fast names reduced qualification and retained operational behavior. Report counts and explicit origins only from available evidence; unsupported passive inventories remain unknown. Display must not perform extra source resolution, Helm execution, network calls, or alter policy, generated schemas or recovery identity. Reject unsupported Standard one-GPU worker configurations before project publication, preserving existing files and profile choice.

#### Negative Criteria

- NC-001: Do not fork upstream charts/scripts, disable the passive framework or operational hooks, bypass bootstrap, add compatibility shims or introduce unvalidated early success. A deployment-name binding may substitute only the fixed login hostname in a verified native script through its supported command value; arbitrary script or command overrides remain rejected.
- NC-002: Do not infer live performance improvement from offline evidence or disabled diagnostics.

#### Validation Method

Inspect wizard, frozen policy, effective manifests, ordered journal, reservation
identity and complete acceptance evidence.

#### Test Method

Exercise profiles, upstream renders, quiescence, full coverage, partial submission,
ambiguous accounting, interruption/restoration, and package wiring.

#### Evaluation Method

Separate source/package evidence from declared live trials. Compare timings only
with equivalent hardware and final acceptance criteria.

<!-- /REQUIREMENT: REQ-029 -->

<!-- REQUIREMENT: REQ-030 status=satisfied priority=P1 type=product -->
### REQ-030: Configure dedicated Soperator installation completely

#### User Story

As an operator, I need supported advanced Soperator settings, backups and directory
identity configured before the immutable installation plan is created.

#### Acceptance Criteria

- AC-001: Soperator create accepts one values-only YAML file through `--values-file`; interactive questions prefill supplied settings and explicit answers win. Generic catalogs remain Soperator-free and infrastructure selection remains unchanged.
- AC-002: Persist explicit value paths outside Helm values. Explicit false, lists and default-equal choices survive normalization, pruning, rendering and upgrades. Reject malformed input, duplicate keys, unsupported helper/routing fields, inline credentials and protected conflicts before dependent work.
- AC-003: Deployment recovery uses the frozen rendered configuration without rereading external values. Upgrades preserve saved choices and reject target-incompatible settings without a new input flag.
- AC-004: Enabled backups require an existing bucket and endpoint, supported schedules, retention and credential references. Selected release defaults supply scheduling policy. Settings reach the upstream backup Schedule and credentials use its actual target namespace.
- AC-005: Enabled SSSD has shared configuration Secret and optional LDAP CA ConfigMap references, propagated to services and every worker NodeSet. Runtime files supply contents without persisting local paths or secret material in configuration, generated artifacts or diagnostics.
- AC-006: Backup and SSSD prerequisites are established before dependent workloads through the existing runtime bootstrap. Reuse complete objects, create absent objects, reject incomplete existing objects and assert target/lifecycle authority before every write. Dry-run does not prompt for sensitive inputs or write runtime objects.
- AC-007: Fresh installation selects a root administrator SSH public key through local discovery or manual public-key input. Interactive installation resolves a missing explicit key choice before the optional upstream customization prompt, so accepting its default no cannot bypass SSH setup. A newly selected key is not immediately prompted again when detailed customization is opened. With no explicit setting, both interactive and non-interactive installation prefer the local Ed25519, ECDSA, then RSA default filename, followed by other supported public-key files. Non-interactive installation fails before installation when no usable key exists. Back preserves component navigation; quitting an unresolved interactive key choice cannot save configuration or create an install plan. Explicit lists, including empty lists, remain unchanged unless deliberately replaced; an unowned default empty list is not an explicit choice. Save inline keys and explicit ownership at `slurmNodes.login.sshRootPublicKeys`; render, resume and upgrade never rediscover local keys. MK8s SSH inputs do not supply this setting.

#### Negative Criteria

- NC-001: Do not add a generic Soperator catalog entry, a whole-deployment seed interface, secret rotation, SecretStash provisioning, new backup-bucket provisioning or downstream QoS compatibility.
- NC-002: Do not treat chart rendering as proof of backup recovery or directory authentication.

#### Validation Method

Inspect frozen input-to-child rendering, configuration provenance and runtime
prerequisite ordering and mutation authority.

#### Test Method

Exercise wizard/noninteractive input, repeated normalization, resume and upgrade,
final upstream manifests, runtime object reuse/failure and secret-safe errors.

#### Evaluation Method

Report source/package checks separately from separately authorized live backup
and directory trials.

<!-- /REQUIREMENT: REQ-030 -->

<!-- REQUIREMENT: REQ-031 status=active priority=P0 type=architecture -->
### REQ-031: Deploy Soperator through the standard configuration pipeline

#### User Story

As an operator, I configure Soperator through its wizard and use the same quota,
validation, rendering, deployment and CI commands as every other project.

#### Acceptance Criteria

- AC-001: Soperator create ends after ordinary authentication bootstrap, wizard configuration publication and post-create checks. It does not render or install infrastructure/workloads. Post-create validation is advisory: a failed check preserves the published configuration, reports validation as incomplete, and retains quota assessment and next-step guidance. Explicit validation and pre-publication source checks still fail on unresolved sources. Helm metadata, values and chart-materialization reads retry only transport timeouts or connection resets, at most three attempts with bounded backoff; permanent failures fail immediately. This includes OCI package capture for render's deployment manifest, retaining verified bytes through capture without retrying consumer errors. Each pull attempt uses a fresh extraction directory; failed or interrupted attempts are cleaned before returning or retrying. Materialization exceptions are not cached as chart findings. Diagnostics never expose signed download URLs or raw timeout exception chains, and command guidance never recommends an unsupported source-validation bypass.
- AC-002: Quota-check, quota-request, validate, render, validate-generated, deploy and bootstrap-ci accept Soperator configurations. Render emits the complete desired graph, including supported edits to an existing deployment. It loads source configuration without rewriting it and publishes local artifacts without destroy-receipt admission, remote lifecycle leases, recovery writes or backend generation admission. A short local publication lock prevents mixed artifact snapshots; execution consumes a private copy. After pre-validation, noninteractive render stages show activity before work starts, with elapsed-time terminal progress or plain stderr start/outcome messages. Failure/interruption clears progress without reporting success; overwrite prompts run outside active phases, and nested rendering reuses its caller's progress owner.
- AC-003: Deploy uses the validated generated snapshot as authority and executes directly for all components. Optional deploy --dry-run previews the whole workflow; it does not acquire active execution ownership, advance checkpoints or publish accepted state. No prior preview, approval token or Soperator-specific execution flags are required.
- AC-004: Fresh Terraform and cluster observations select creation, unchanged-state verification, reconciliation, topology changes or coordinated upgrades. For unchanged release and topology, compare live Soperator settings with frozen desired values resolved from authoritative outputs, using the same selector and restored-partition semantics as final verification. Equivalent render placeholders or default syntax must not trigger maintenance; actual setting changes and unavailable required outputs remain blocking or require reconciliation. First installation and coordinated changes retain their original output-read ordering. A previous shared accepted deployment is never required; unknown observations do not imply absence or convergence.
- AC-005: Local deployment checkpoints remain input- and execution-control-bound. Matching attempts revalidate effects before recovery; changed inputs or controls select an independent local attempt without deleting earlier checkpoints or requiring their completion.
- AC-006: Worker growth, shrink, additions, removal and replacement are supported. Every potentially removed worker is quiescent and autoscaler activity is controlled before provider removal. Default job policy fails on blocking jobs; explicit existing job policies remain available.
- AC-007: Combined transitions validate all intermediate configurations, compatibility hops, quota and dependencies before retirement. Retire/downsize, verify remaining inventory, upgrade that inventory, add/grow target-version groups, reconcile final configuration and verify capacity. Every Terraform stage is restricted to its declared resource/action set. Internal stage/config publication projects frozen component rows into the strict source schema without derived runtime aliases, preserving selected values and instance identities and rejecting conflicting derived targets.
- AC-008: Terraform alone owns shared backend state and native locking. cxcli does not read or write shared deployment checkpoints, accepted generations or execution leases. Command-owned local checkpoints, receipts and recovery rules remain supported, including dedicated Soperator upgrade and deployment checkpoints. Summaries preserve primary errors and independently verified evidence.
- AC-009: Render publishes current config artifacts after overwrite confirmation, preserving local command checkpoints. Deploy atomically captures current artifacts into its private execution root; subsequent renders cannot change running inputs. Past attempts and obsolete backend objects never gate a new current-input attempt.
- AC-010: Soperator upgrade retains its existing command-local checkpoint and resume behavior. Generic deploy uses its own attempt namespace even when sharing execution logic. CI serializes mutations without cancelling a running deployment; cross-machine mutation requires operator serialization.
- AC-011: The first implementation supports one Soperator target per project with ordinary targets. Preserve target/storage identities and retained data; reject unsupported ownership changes, protected storage replacement and unsupported downgrades.
- AC-012: Remove soperator install, obsolete flags, compatibility aliases, old-format readers and migration shims. One current command, manifest and operation contract is supported.
- AC-013: Downstream admission includes exact resources recreated after prior retirement, including unchanged dependencies. Creation authority requires verified deletion of the original resource identity and cannot authorize additional survivor destruction. Fresh execution plans stay within frozen semantic admissions; diagnostic replacement plans are never applied.
- AC-014: Managed and onboarded coordinated transitions reconcile the frozen desired application generation, including changed and removed values, under one maintenance owner. Verification uses the release owner's stable transformations, declared Terraform-derived values and exact checks-phase projection. Equivalent native defaults, quantity serialization and API-added fields must not become false drift. Graph-owned Soperator node-filter In/NotIn selector values are order-independent; membership, selector keys/operators, other ordered lists, removed owned fields and immutable identities remain verified. The parent verifies phase settings before releasing scheduling and restored settings before completion. Onboarded cluster platform ownership remains with the provider API.
- AC-015: Plain deploy and --all-targets execute selected targets with Soperator scheduling restored before other applications. Local attempt recovery re-verifies completed effects; an unrelated failed attempt never supplies admission or prevents a new invocation.
- AC-016: Completion evidence records immutable identity, desired inputs and mandatory validation results per selected target in local reports. Unselected targets are not claimed complete. Applications use explicitly bound temporary kubeconfig handoffs and local execution ownership.
- AC-017: Deploy does not implicitly prune or uninstall resources omitted from current artifacts. Fresh inventory rejects omitted installed HelmReleases carrying the exact target ownership annotation before application mutation, including removal of the last App. Unmarked and unrelated resources are never adopted or pruned. Retained resources support value removals. Verification covers both root and ordinary sibling bundles and treats OCI HelmRepository objects according to their statusless contract.
- AC-018: Deploy identifies each Terraform plan's purpose and shows activity before inspecting a saved plan, with a terminal spinner and elapsed time or plain stderr start/outcome records. Inspection completion rows retain the purpose beside the per-executor sequence number; numbers do not imply retries. Soperator admission-input checks, observed-source and stage rendering, and frozen recovery-input replay show named progress before blocking work. Application output resolution, compatibility replay, Flux refresh, target handoff/authority/input preparation and final live/acceptance verification retain named progress after both changed and no-change Terraform plans. Follow-up commands use the original generated bundle path after private execution-cache cleanup. Errors and interruption clear progress without reporting success. A single unchanged execution reuses its completed preflight and in-memory runtime inputs; an unexecuted simple stage does not run a verification plan that cannot establish completion. Fresh execution plans, stage-scope checks, fencing and independent post-execution/final verification remain mandatory. Final Soperator observation invokes the canonical desired-state verifier once; that verifier owns graph readiness for both profiles, retaining receipt freshness and subsequent desired-state, dashboard, storage and settings checks. No-change messages describe observed convergence without claiming an unobserved checkpoint. Post-apply output resolution reuses the initialized Terraform root. Streaming progress coalesces bursts, suppresses elapsed-only duplicates until the heartbeat, preserves instance keys and drains event queues independently of periodic fencing checks; final authority and errors remain immediate.

- AC-019: Configuration loading normalizes only in memory and does not acquire remote ownership solely to persist normalization. Explicit configuration writers retain their existing fenced publication and preimage checks.
- AC-020: Deploy treats temporary GPU physical-capacity shortages as advisory and continues submitting the desired infrastructure. Confirmed tenant/project quota allowance shortfalls still block after discounting already managed resources and aggregating shared GPU quota across shapes. Recognized VM schedule-timeout events during node-group provisioning or updating remain visible as pending capacity and do not abort Terraform early. Unknown resource exhaustion, quota/permission failures, terminal provider errors and execution deadlines remain enforced; pending capacity does not count as successful readiness or acceptance.

- AC-021: Retain Terraform native S3 state and lockfile semantics. Remove cxcli shared lifecycle I/O and ignore obsolete lifecycle objects without migration or deletion. Existing artifact transport and command-specific cluster recovery are separate from shared deployment admission.

- AC-022: Prepare, assess changes, deploy, verify and report a result through one shared path. Reuse successful invocation-local initialization and immutable preparation only for identical inputs and concrete runtime roots; restore the exact recovery generation before preflight. One raw Terraform observation supplies initial drift classification and semantic admission. A simple stage takes a second fresh execution plan immediately before apply, retaining scope/fence checks and independent post-stage/final verification. Distinct intermediate generations retain their own plans. No-op skips mutation but retains required readiness and acceptance. Dry-run wording is reserved for actual user previews; same-version settings changes are reconciliation. Effective application publication commits selected target bytes and compatibility evidence for the resulting complete execution bundle together. Recovery may correct stale private metadata only when unchanged authored intent and an authenticated target checkpoint prove every changed resource; unexplained changes remain blocked. Authored compatibility replay cannot reuse effective proof unless artifact bytes also match.

#### Negative Criteria

- NC-001: Do not apply raw Terraform before identifying and establishing necessary Soperator maintenance.
- NC-002: Do not infer convergence from equal versions, adopt colliding names, broaden a resumed stage, or replay a consumed Terraform plan.
- NC-003: Do not introduce a mandatory Soperator confirmation, approval fingerprint or separate recovery command into deploy.
- NC-004: Do not persist runtime secret values or credential-file paths in configuration, artifacts, receipts or logs.
- NC-005: Changed rendered input selects an independent local deployment attempt, including corrections to obsolete values. Matching local attempts retain stage and command recovery evidence. Dedicated command repair receipts remain scoped to their original operation; their contents are never reset or adopted by a different generic deployment.

#### Validation Method

Exercise the complete public command chain, typed change planning, stage authority,
remote checkpoint recovery, generated-source isolation and common CI execution.
Local spawned-worker tests cover deadlines, cancellation, connection reuse and
ambiguous write recovery; live Object Storage conditional semantics remain a
separate qualification from local source/package evidence.

#### Test Method

Test fresh/unchanged/changed/partial/unknown states, worker transitions and combined
upgrades, stage scope injection, generation races, lost acknowledgements, local/CI
concurrency, command removal, credential isolation and installed-wheel contracts.

#### Evaluation Method

Review independently verified convergence, source isolation, recovery authority and
maintenance ordering. Record source, installed-package and live evidence separately;
local tests do not qualify real worker retirement, Object Storage CAS or Slurm/GPU health.

<!-- /REQUIREMENT: REQ-031 -->

<!-- REQUIREMENT: REQ-032 status=active priority=P0 type=product -->
### REQ-032: Select acceptance depth and finish Soperator testing safely

#### User Story

As an operator, I can skip or safely stop extended diagnostics on first install
and upgrade while retaining required readiness and verified restoration.

#### Acceptance Criteria

- AC-001: Deploy and Soperator upgrade accept --acceptance readiness|full. Unattended omission selects full; interactive omission prompts after required readiness with default no. This refines REQ-029's formerly mandatory extended acceptance without weakening readiness or ownership.
- AC-002: Required readiness includes rollout, capacity, storage, Slurm, GPU smoke and applicable passive policy proof. Extended checks have reviewed release-derived membership and dependency closure; unknown bootstrap behavior remains mandatory.
  Fresh CPU, GPU and mixed profile defaults keep `ensure-healthy-nodes` enabled
  with `runAfterCreation: true`, so generated configurations satisfy mandatory
  native Slurm smoke. Explicitly conflicting user settings remain rejected;
  operation-scoped maintenance overlays do not alter desired defaults.
- AC-003: Ctrl+G durably requests safe finishing and is shown at startup and during extended testing. Ctrl+C retains ordinary interruption. Stop future extended submissions, cancel only exactly owned work with proven cleanup, and let unprovably cancellable running tests finish naturally.
- AC-004: Submission identity is durable before effects; recover uncertain Kubernetes-to-Slurm submissions without broad cancellation. Preserve native scripts, real initial-run guard Jobs, failed evidence and partial results. Cleanup ambiguity, actual health failures and lost authority cannot become successful cancellation.
- AC-005: Both choices release the diagnostic reservation only after quiescence, restore and verify desired Active/Passive policy with customer admission closed, then restore owned job holds/partitions. Success and exit zero require all declared mandatory work and restoration complete. Skipped/cancelled is never PASS.
- AC-006: Decisions, cancellation, evidence and restoration survive resume. Ctrl+G skips remaining extended suites across the invocation while mandatory work still completes. Source-bound hook handling prevents hidden extended waits during install, upgrade, restoration and postflight. A registry-hostname-only change in the verified upstream waiter image does not invalidate the reviewed execution contract; image repository/version, executable body and the complete affected hook inventory remain checked.

#### Negative Criteria

- NC-001: Do not disable desired monitoring permanently, fabricate native success, erase failures, cancel unrelated jobs or reopen an unverified cluster.
- NC-002: Do not add legacy receipt readers or bypass failed full acceptance by changing a resume flag.

#### Validation Method

Trace shared install/upgrade execution, receipts, runtime readiness and admission.

#### Test Method

Exercise both profiles, submission/cancellation races, interruption, terminal
handling, natural completion, restored policy and multi-target recovery.

#### Evaluation Method

Use separate source tests and independently verified live first-install and
upgrade trials; retain intervened or failed trials separately.

<!-- /REQUIREMENT: REQ-032 -->

<!-- REQUIREMENT: REQ-033 status=active priority=P0 type=architecture -->
### REQ-033: Enforce reproducible component compatibility

#### User Story

As an operator, I receive consistent component compatibility decisions from one
versioned registry before installation and every upgrade stage.

#### Acceptance Criteria

- AC-001: One packaged compatibility-matrix.yaml owns covered version sets, relationships and source-ranked evidence. Sources remain in the component catalog; Soperator child versions remain exclusively upstream-owned.
- AC-002: Inventory all selected instances and enabled operands. Generic Helm/Terraform and MK8s/Soperator-specific checks distinguish artifact constraints, documented support and observed runtime outcomes. An omitted or empty optional Helm `kubeVersion` is undeclared, not a failed constraint or proof of support; malformed nonempty constraints remain blocking. Native rendered-chart inspection accepts bare `=` scalar enum values as strings without changing frozen chart bytes or global YAML behavior. Malformed YAML and unsupported or unsafe tags still fail closed. Reject malformed SHA-256 image references and duplicate environment variable names within each native workload container before deployment effects; preserve optional empty lists and independent container scopes, and do not interpret unknown custom resources as Pod templates.
- AC-003: Unknown or stale affirmative support records an internal warning outcome and continues without routine terminal warnings. Applicable known unsupported combinations, conflicting evidence, failed or unresolved hard constraints, required adapter failures, provider rejection and integrity failures block with actionable errors and nonzero exits. Negative evidence remains blocking until superseded.
- AC-004: Version-set selection round-trips through compatibility.targets.<target_id>.version_set. Explicit conflicts fail; custom versions gain no implicit qualification. No silent operator upgrades or cross-distribution support inheritance.
- AC-005: Validate every proposed intermediate state, using existing deterministic planners and native Helm version semantics. Existing source violations permit only an explicitly reviewed remediation transition; they never authorize a new incompatibility.
- AC-006: Freeze selected matrix/evaluator/tool identity, artifacts, effective values, dependencies and output-binding instructions in the deployment generation. Output hydration and recovery cannot reread mutable catalog defaults or reinterpret unsupported schemas. Recovery admits the immutable rendered generation before restoring execution-time transformations; restored runtime configuration and Terraform inputs are prepared before execution without replacing fresh compatibility evidence with cached reports.
- AC-007: Validation and previews produce complete per-target findings internally; existing manifests and operation evidence retain machine-readable findings. Commands do not display compatibility tables, routine assessment summaries, provider matrix inventories or recommendation rows. Progress, operational plans and selected settings, runtime health, recovery guidance and actionable blocking errors remain visible. Plain validation adds no report persistence. Skipped/cancelled extended tests remain distinct from readiness and never imply complete runtime validation or vendor support.
- AC-008: Direct application execution admits every selected target before setup or application effects, using the frozen generation. Generic chart upgrades prepare and assess the same candidate in dry-run and execution; publish configuration, artifacts and transition evidence atomically only after admission. Concurrent edits fail safely, and retries use admitted artifacts with independent live readiness verification.

- AC-009: Support both HTTP/HTTPS Helm repositories and OCI chart sources through the existing deployment, application and upgrade commands. HTTP charts require an exact chart version, matching chart identity and a fresh extracted-content comparison against the frozen render snapshot before deployment effects; unavailable or changed content blocks execution. OCI releases retain digest-bound execution. Native HTTP reconciliation continues to trust the versioned upstream repository, so its preflight content check must not be described as continuous digest pinning. Git chart execution remains unsupported without its own adapter.

#### Negative Criteria

- NC-001: No executable policy language, arbitrary evidence-URL fetching, blanket bypass or legacy reader.
- NC-002: No whole-catalog or secret capture in operation evidence.

#### Validation Method

Inspect parser, adapters, command wiring, exact-source evaluation and frozen recovery.

#### Test Method

Cover malformed data, distribution/patch boundaries, unknown/stale/conflicting
evidence, mixed versions, source remediation, package contents and catalog drift.
Pair command-output absence checks using nonempty findings with assertions that
evaluation still runs, stored evidence remains complete and blocking errors retain
their execution gates and remediation guidance.

#### Evaluation Method

Independently distinguish constraints, support evidence and live observations.

<!-- /REQUIREMENT: REQ-033 -->

<!-- REQUIREMENT: REQ-034 status=active priority=P0 type=product -->
### REQ-034: Retire one explicitly selected MK8s cluster through global destroy

#### User Story

As an operator, I need one SDK-based destroy command for managed and onboarded
MK8s clusters, with explicit cloud identity and resumable resource cleanup.

#### Acceptance Criteria

- AC-001: `destroy CONFIG --target CLUSTER_ID` requires an immutable Nebius cluster ID even for one target. Resolve exactly one project-bound managed or onboarded target; reject aliases, context-only registrations, duplicate/conflicting bindings and foreign projects. No --all-targets or implicit selection exists.
- AC-002: Both ownership kinds and ordinary/Soperator workloads use one SDK workflow without Kubernetes access or Helm/Flux teardown prerequisites. Delete the cluster, then its dedicated GPU clusters and owned PVC disks after proving cluster/node-group/worker absence. Preserve SFS unless --delete-sfs, PVC disks with --preserve-pvc-disks, VM-NFS and unrelated infrastructure. Protection, shared references and unknown storage ownership retain their existing fail-closed checks.
- AC-003: --yes authorizes initial execution and terminal failed-operation retries without a TTY after identical validation. Otherwise require the exact inventory confirmation. --dry-run writes only an owner-only local preview; --yes does not turn it into execution.
- AC-004: Command-local destroy-v1 receipts bind immutable cloud ID, internal target, project/backend, disposition, inventory and final generation. Read this command recovery before resolving removed targets, persist request intent, poll accepted operations and never blindly replay uncertain acceptance. Local execution excludes concurrent writers; CI/operators serialize across machines.
- AC-005: Normal Terraform reconciliation may remove only exact approved ancillary resources and reconcile SDK-deleted identities. Publish selected-target removal transactionally, including target-bound app config, after independent cloud/state checks. Reject unrelated drift, changed local files and surviving references. Completed replay requires the original cloud ID and matching published generation; it never targets a replacement cluster.
- AC-006: Completion describes the selected cluster and approved inventory, not arbitrary application-controller external resources. Report detected unsupported/preserved resources. Existing non-MK8s-only teardown remains available; cluster-specific options never fall through to whole-project destruction. Low-level Terraform teardown cannot bypass MK8s SDK destruction.
- AC-007: Remove the Soperator destroy command, implementation aliases and old receipt readers. Keep the existing Terraform backend location and command-local destroy receipt owner; unsupported command receipts fail untouched before mutation. Finish active predecessor operations and explicitly retire incompatible completed records before release adoption; no automatic migration/reset is provided.

#### Negative Criteria

- NC-001: Do not infer deletion authority from a name, local kubeconfig context, arbitrary cloud ID, or --yes alone.
- NC-002: Do not run Terraform cluster deletion, widen approved scope on resume, silently delete unrelated resources, or claim live correctness from offline tests.

#### Validation Method

Compare frozen IDs and final generation with independent provider absence and
preserved-resource/state observations; separate source, wheel and live evidence.

#### Test Method

Exercise ownership/workload combinations, exact selection, approval, every
storage policy, interrupted recovery and publication, receipt rejection,
non-MK8s regression behavior and source/installed command contracts.

#### Evaluation Method

One public command deletes only the explicitly selected MK8s cluster and its
approved dependent resources with durable recovery and aligned documentation.

<!-- /REQUIREMENT: REQ-034 -->

<!-- REQUIREMENT: REQ-035 status=active priority=P1 type=feature -->
### REQ-035: Guarded private course observability integration

#### User Story

Learners need an executable, evidence-led GPU optimization workflow across all five courses.

#### Acceptance Criteria

- AC-001: Soperator dashboard-only render/apply uses ordinary application scope with accepted protected baseline, target identity, pending-operation and backend deployment authority checks; it must not reconcile Terraform or enter Slurm maintenance.
- AC-002: Grafana readiness and dashboard validation support internal ClusterIP access through bounded loopback forwarding with cleanup and no persisted temporary endpoint.
- AC-003: Catalog-owned internal Prometheus datasources support an explicit no-cloud-auth mode while existing Nebius datasource authentication remains intact.
- AC-004: Custom course catalogs can install private Grafana, finite result publication resources and JSON dashboards repeatably without creating a public Gateway.
- AC-005: Ordinary application apply automatically reconciles proven stale install records through the shared accepted-deployment owner. Preserve historical outcomes, require exact ownership and fresh current-state proof under project and cluster fencing, and persist a resumable backend transaction before conditional record changes. No Terraform, upgrades, storage repair or Slurm maintenance may be invoked by this metadata reconciliation.

#### Negative Criteria

- NC-001: Do not claim GPU performance from simulations, profile overhead, stale data or unsupported attribution. Preserve unrelated changes and existing cluster ownership.

#### Validation Method

Inspect source, recipes, dashboards, setup and supported command flow against the approved design.

#### Test Method

Run focused contract, failure-path, publisher, dashboard, renderer and integration tests.

#### Evaluation Method

Separate offline source checks from installed-tool, browser and representative two-H100 live evidence. Record unavailable live checks explicitly.

<!-- /REQUIREMENT: REQ-035 -->

<!-- REQUIREMENT: REQ-036 status=active priority=P1 type=feature -->
### REQ-036: Shared Nsight profiling and private browser viewers

#### User Story

Operators need Nsight Systems and Compute collection in the shared Slurm jail
and browser analysis on a laptop without copying reports off the cluster.

#### Acceptance Criteria

- AC-001: Ordinary MK8s Apps expose independent Systems and Compute viewers using the official Nsight Streamer chart 2026.4.1, with a guided component-add wizard, existing PVC and Secret references, and default namespace soperator.
- AC-002: `soperator profiling install CONFIG --target TARGET` coordinates both pinned CLIs and both catalog viewers on an accepted Soperator target. Systems is 2026.4.1 and Compute is 2026.2.1, matching the chart images. Component add remains configuration-only.
- AC-003: Reports default to /data/nsight-reports and viewers mount only the resolved persistent report directory read-only. Storage, namespace, placement and report permissions are validated; no claim name is inferred from jail-pvc.
- AC-004: Version-selected PATH takes precedence over older bundled profilers in fresh login and worker shells. Every new jail generation replays pinned profiler setup after pristine-image inventory and before promotion, with independent customization evidence.
- AC-005: All jail mutations use Soperator target identity, locks, fencing and recoverable receipts. Ordinary application reconciliation never installs packages or changes the jail; partial viewer failure preserves successful tool setup.
- AC-006: Installation output prints complete loopback-only kubectl port-forward commands using persistent verified contexts and both HTTP/TURN ports: Systems 30080/30478, Compute 30081/30479. Credentials are referenced, never printed or persisted in generated values. After installation success and on successful reruns, print one copyable password-retrieval command for the shared Secret, bound to the verified persistent kubeconfig and context, namespace and selected Secret. The command contains no credential value and is never executed by cxcli; omit it when persistent access cannot be verified. The retrieval command adds a display newline after successful decoding without changing the password bytes or stripping literal percent characters.

- AC-007: Explicit profiling recovery covers initial installation and pre-promotion jail customization. Standalone profiling install automatically resumes recorded stages and retries only exact terminated installer failures with authenticated partial-state ownership. Retain failed attempts and at most three Jobs per stage across reruns. Explicit recovery retains exact target, stage and predecessor Job UID for corrections outside ordinary retry; upgrade resume never implicitly allocates successors.
- AC-008: Capture Job identity before waiting and package/profile preimages before mutation. Recovery admits only proven bounded partial states, requires terminated predecessor writers, and rejects missing Jobs, stale installer identities, incomplete historical preimages and started promotion.
- AC-009: Both official viewer renders and native Ubuntu 22.04/24.04 on amd64/arm64 have repeatable automated validation. Separate artifact acquisition from cache-only installation and distinguish source, native and live evidence.
- AC-011: Profiling install defaults to wizard mode without terminal-based mode selection. For a missing login Secret, prompt for username (admin default) and a masked, confirmed password; reject missing, empty and whitespace-only passwords and prompt again without altering valid password bytes. Reuse existing valid Secrets without prompts or rotation. Automation selects --no-interactive for existing credentials or --password-stdin with optional --username for explicit runtime input; stdin alone selects noninteractive entry and explicit --interactive plus stdin is rejected. Missing terminal input produces actionable guidance before Secret creation, configuration publication or package Jobs. Reject differing supplied credentials for an existing Secret without changing it. Credentials never enter configuration or output; component add remains configuration-only.
- AC-012: Explicit pre-admission runtime-mounts and runtime-image repairs admit only source-defined corrections to a failed standalone admission successor. The former adds the required AppArmor Unconfined field; the latter replaces the incompatible upstream population image in the main container with the pinned Ubuntu execution image and supplies Unconfined if missing. Require exact retained Job/Pod termination and the matching initial mount or BusyBox path-check failure proof. Preserve frozen installer bytes, original manifests, failed history, fences and the existing three-Job stage limit. Explicit profile-order repair is limited to a failed standalone install with the exact login-PATH failure. Add the mandatory late activation hook used by fresh installs, sourcing the one managed profiler profile after Soperator CUDA PATH setup. Reject foreign or unsafe hook files; verification checks the hook read-only and retains actual login-shell executable resolution.
- AC-010: Repeating a successful profiling install with unchanged target, settings and accepted configuration preserves the accepted generation, package receipts, report data and login credentials, creates no additional package Jobs, and revalidates tools and private viewers before reporting access. Existing viewer configuration may be reconciled without reinstalling the shared tools.

- AC-013: Installation observes installed tools, shared storage and viewers only; empty reports and failed user profiling workloads do not affect readiness. Reuse healthy components without new package Jobs, repair only proven owned omissions through a receipt-bound transaction, and resume safe interrupted work through the same command without new flags or commands. Missing ownership, foreign content, active competing writers and ambiguous storage remain blocked. Successful output always includes both complete loopback port forwards.

- AC-014: Profiling installation shows descriptive phase progress before and after viewer deployment, including cluster and storage checks, credential Secret I/O, shared-jail package admission, installation and verification, and final acceptance. Terminals show a spinner and elapsed time; redirected output emits bounded START/OK/FAILED records. Prompts and existing deployment displays have exclusive terminal ownership. Cluster-handoff notices share the progress output owner so completed phases leave one clean success row without stale spinner fragments. Exceptions and interruption stop progress without reporting success; progress labels never contain credential values or raw remote output.

- AC-015: `soperator profiling show CONFIG --target TARGET` reconstructs exactly two complete loopback HTTP/TURN port-forward commands and one shared-password retrieval command from the live, owned, ready viewers on the recorded accepted Soperator cluster. Require explicit verified durable kubeconfig/context and shared password references; preserve the current context and honor local persistence opt-outs. Do not use saved commands or undeployed viewer values, execute the displayed commands, retrieve password data, probe inside Pods, reconcile installations or change cluster resources. Missing, ambiguous, unready, changing or unverifiable access fails without a partial command set. Apply the shared copyable-command style and show browser URLs separately.

#### Negative Criteria

- NC-001: Do not upgrade CUDA, GPU drivers or unrelated packages; overwrite unrelated tools or configuration; expose the entire jail; or install viewers automatically on unrelated targets.
- NC-002: Do not claim live profiling, browser streaming, or upgrade acceptance from source tests or chart rendering alone.

#### Validation Method

Compare generated manifests, frozen package and storage identities, installed
executables, generation receipts and independent browser/report observations.

#### Test Method

Exercise catalog/wizard, rendering, generic MK8s, target selection, dependencies,
PATH precedence, fencing, interruption, jail replay, permissions and access output.

#### Evaluation Method

Run separate authorized disposable-target Systems/Compute captures, browser
viewing and jail-upgrade replay; report source and live evidence separately.

<!-- /REQUIREMENT: REQ-036 -->

<!-- REQUIREMENT: REQ-037 status=active priority=P1 type=feature -->
### REQ-037: Immediate and recoverable Grafana dashboard management

#### User Story

As an operator, I need one coherent Grafana command group to import, export and
validate dashboards for a configured cluster or an independent external server.

#### Acceptance Criteria

- AC-001: `grafana import PATH...` handles files and directories, saves normalized project JSON and target-local dashboard declarations, and immediately installs through the Grafana API without render/deploy. `--attach` additionally publishes reusable catalog entries; unattached imports do not modify the catalog.
- AC-002: Cluster access defaults to config.yaml and requires an explicitly selected target. Discover the configured existing Grafana admin Secret and use a verified loopback tunnel; never create or rotate credentials during dashboard operations.
- AC-003: `--url` selects external mode, which rejects config, target and catalog options and never reads or modifies project state. Explicit token environment selection supports API import/export/validation. Interactive browser-assisted SSO prepares import files and opens the UI, reporting manual completion pending rather than installed.
- AC-004: Stable dashboard UIDs, whole-batch preflight, canonical content comparison, explicit overwrite decisions, version conflicts and recoverable project publication make repeated imports converge without duplicate dashboards or a recovery command. Identical effective inputs and unchanged destination state produce no committed dashboard POST/PUT, version increment, duplicate declaration or rewrite of unchanged config, JSON, receipt or catalog files; reads, schema dry runs and coordination leases remain permitted. Lost responses use readback, partial batches retain completed work, and interrupted writes preserve their frozen versions. A completed import followed by external content drift may start a fresh explicitly authorized update; its next identical run is again a no-op.
- AC-005: API-owned imports and file-provisioned dashboards never silently transfer ownership. The importing target excludes only explicitly linked attached catalog entries from file provisioning. Render is offline; ordinary apply/deploy restores declared replayable missing dashboards after readiness without overwriting subsequent browser edits.
- AC-006: Preserve multiple datasource references and support explicit mappings. Export downloads portable JSON with paginated selection. Validation retains existing configured datasource/read-endpoint checks and supports offline local-file checks.
- AC-007: New managed Grafana installations use the shared persistent PostgreSQL backend specified by REQ-039. API imports set editable=true; ordinary deployment preserves existing API-owned dashboard content, including UI edits, and restores only missing replayable dashboards.
- AC-008: Replace the flag-driven Grafana entrypoint and top-level validate-dashboards with import/export/validate, without compatibility aliases. Keep credentials in memory, bind all effects to the selected destination and reject credential forwarding across redirects.

- AC-009: `grafana --help` includes labeled copyable examples for file and directory imports with and without attachment, external token and browser-assisted SSO import, cluster export, and offline validation. Explain the target placeholder, existing-Secret cluster authentication, immediate installation without attachment, and manual SSO completion. Keep explanatory labels outside command text.

- AC-010: Interactive API imports resolve unknown datasource references with a searchable selection menu of existing compatible datasources. Display name, type and UID; a sole choice still requires Enter. Reuse confirmed mappings across the batch and retain explicit `--datasource-map` for automation. No compatible choices or cancellation stops the new batch before project publication or committed dashboard writes. Preserve existing recovery of prior interrupted operations and revalidate selected datasource availability/type during preflight.

- AC-011: Grafana import reports each active stage and elapsed time before blocking access, validation, publication and API work. Terminal progress yields to interactive prompts and ordinary results; redirected progress is bounded plain text on stderr. Rendering failure never changes operation results, and cancellation or failure never prints overall success. Labels exclude credentials and private endpoints. Existing mutation, timeout and recovery semantics remain authoritative.

- AC-012: Local execution contention fails promptly while a real writer or its contained-process supervisor holds the kernel lock. Persisted deployment history is not liveness or admission. Terraform native state locking remains enabled.

- AC-013: All deployment callers, including standalone cluster imports, use local process ownership; nested execution reuses its parent owner. Terraform alone owns shared S3 state and native locking; obsolete cxcli S3 lifecycle records are ignored. Datasource discovery and selection precede mutation ownership, followed by fresh identity, configuration, datasource and dashboard preflight. Changed selections fail before new publication rather than prompting under ownership. Dedicated local command recovery and Kubernetes coordination remain enforced.

- AC-014: API import classifies dashboard ownership before datasource prompts or schema-conversion requests; cluster discovery rejects ineligible management before mutation leases. Selection uses one inventory snapshot and locked preflight refreshes it. Cluster import reuses its still-active renewable Kubernetes authentication environment only after exact target, configuration and accepted identity continuity checks; all locked admission checks remain fresh, including credentials and the Grafana endpoint.

- AC-015: Explicit `--overwrite` may update classic-file-provisioning dashboards only with a valid manager identity and exactly `managerAllowsEdits="true"`. Preserve the UID, management/source metadata, unrelated writable metadata and folder; reject a different explicit folder, other managers, absent/denied edit permission and managed batches with `--attach` before new writes. The flag is sufficient confirmation. Warn that provisioning remains authoritative and may replace the edit; never modify or disable its source. Ownership and permission drift fail even on an otherwise unchanged import.

- AC-016: Cluster managed updates save manual copies in `dashboard_imports` with strict `replay: false`, required `management_sha256` and no `catalog_key`. The fingerprint binds destination, namespace, UID, folder, manager and edit permission. Preserve it in declarations and receipts across completed and interrupted imports; reject missing provenance, missing managed dashboards and ownership-mode changes. Manual copies never suppress catalog provisioning, become replay assets, trigger deployment ownership checks or participate in replay acceptance. Explicit validation and re-import remain available; external API mode writes no project state.

#### Negative Criteria

- NC-001: Do not invoke Terraform, Helm deployment, Slurm maintenance or whole-deployment acceptance from dashboard-only import.
- NC-002: Do not extract browser cookies, fall back to ambient Nebius tokens, delete dashboards on omission, or claim live validation from offline tests.

#### Validation Method

Compare saved declarations, remote dashboard identity/content, optional catalog
publication and unchanged protected deployment evidence.

#### Test Method

Cover command modes, file/directory batches, authentication boundaries,
provisioning collisions, retry/CAS conflicts, datasource mappings, temporary
storage loss and browser-assisted pending outcomes.

#### Evaluation Method

Qualify the API against disposable pinned Grafana separately from source tests;
keep designated live-cluster/browser validation as a separately declared trial.

<!-- /REQUIREMENT: REQ-037 -->

<!-- REQUIREMENT: REQ-038 status=satisfied priority=P0 type=security -->
### REQ-038: Bind every Kubernetes connection to its intended target

#### User Story

As an operator with multiple clusters, I need every cluster command to use its
selected target regardless of my workstation's current kubeconfig context.

#### Acceptance Criteria

- AC-001: Live kubectl, Helm and Flux subprocesses require an explicit context or a context supplied by the selected target handoff. Missing, blank, conflicting or ambiguous target selection fails before connection. The kubeconfig current-context is never a target-selection fallback.
- AC-002: Generated handoffs retain immutable cluster identity verification and isolated kubeconfig storage. Explicit contexts resolve through their selected kubeconfig; tools reject missing context entries rather than falling back. Reused local contexts must match the selected cluster ID's provider API endpoint and CA certificate. Multiple matching local cluster histories require explicit disambiguation.
- AC-003: Targetless application observation, deploy, bootstrap, teardown, validation, runtime-secret access and port forwarding fail before cluster effects. Offline rendering and client-only version/help operations remain available without a target.
- AC-004: Follow-up cluster commands carry the selected context or state that target selection is required. Rollout failures identify the failed controller and retain bounded sanitized diagnostics from both output streams.

#### Negative Criteria

- NC-001: Do not switch or persist the workstation current-context as a repair, infer authority from that context, increase timeouts without causal evidence, or add legacy fallback paths.

#### Validation Method

Trace every cluster-capable process boundary and target resolver, including
subprocess streaming and generated guidance.

#### Test Method

Use an unrelated ambient context, explicit target handoffs, missing/conflicting
selectors, ambiguous histories and controller failures; assert zero subprocess
calls on rejected inputs and preserve offline commands.

#### Evaluation Method

Separate source regression results from live target reads and deployment replay.

<!-- /REQUIREMENT: REQ-038 -->

<!-- REQUIREMENT: REQ-039 status=active priority=P1 type=feature -->
### REQ-039: Persistent PostgreSQL-backed Grafana replicas

#### User Story

As an operator, I need automatically connected Grafana replicas with durable,
UI-editable API dashboards and no manual database authentication setup.

#### Acceptance Criteria

- AC-001: The reusable PostgreSQL catalog component is pinned, automatically selected and ordered before Grafana on each required target. Its default single CPU-only instance has a 10Gi RWO PVC on compute-csi-default-sc, configurable storage, and retained claims.
- AC-002: Grafana defaults to two CPU-only replicas using one PostgreSQL database and shared encryption key. Private connectivity, credentials, deployment ordering, disruption budget and alerting peer discovery are generated automatically. Use the pinned chart's own Pod IP environment binding without generating a duplicate. Bind the pinned image using the exact native chart digest-value format and verify the complete rendered image reference.
- AC-003: Deployment creates separate administrator, application and encryption Secrets only for a fresh installation. Validate the complete ownership inventory before writes; reuse existing credentials and fail on partial, missing or foreign state when persistent data or releases already exist. Standalone PostgreSQL receives the same bootstrap.
- AC-004: PostgreSQL uses private ClusterIP connectivity, SCRAM authentication and an ingress NetworkPolicy. The non-superuser Grafana role owns its database; no TLS or certificate management is introduced.
- AC-005: Reject existing SQLite or indeterminate Grafana backends before application mutations, even without dashboard declarations. No migration, fallback, compatibility alias or credential regeneration is allowed. A failed initial PostgreSQL Helm install with no workload may resume only after current-generation terminal failure, exact revision-one storage/configuration, owned stored workload and consumed configuration, absent residual Pods/ReplicaSets and complete retained credentials prove the same backend. Ambiguous or previously successful history remains blocked.
- AC-006: API imports force editable=true before canonical publication. Reapply preserves existing API-owned UI edits, restores missing dashboards and verifies ownership/existence. Explicit import --overwrite remains the replacement action with concurrency guards.
- AC-007: Ordinary component removal retains database claims and runtime Secrets. Verify authenticated database readiness, both Grafana replicas and persistence across pod replacement separately from source tests.

#### Negative Criteria

- NC-001: Do not share a Grafana SQLite PVC between replicas or put credentials in generated configuration, logs or Git.
- NC-002: Do not promise database HA, backup infrastructure, automatic migration, or protection against PVC/cluster deletion; the default database remains a single point of failure.

#### Validation Method

Inspect target-scoped generated releases, ownership admission, Secret references,
readiness and retained resources.

#### Test Method

Cover catalog selection, rendering, bootstrap/reuse/rejection, both apply paths,
editable import, source tamper and UI-edit-preserving replay.

#### Evaluation Method

Qualify pinned charts and disposable Grafana/PostgreSQL runtime independently;
record Kubernetes, browser and customer-cluster evidence boundaries explicitly.

<!-- /REQUIREMENT: REQ-039 -->

<!-- REQUIREMENT: REQ-040 status=active priority=P1 type=feature -->
### REQ-040: Target-bound local and remote observability installation

#### User Story

Operators need one reproducible Grafana installation and Apps wizard for managed
MK8s and Soperator targets, with independently selectable telemetry destinations.

#### Acceptance Criteria

- AC-001: `grafana install` requires explicit `--config` and `--target`, loads that managed target, runs the interactive observability wizard, atomically persists choices into the same configuration, renders, applies through existing lifecycle owners, and verifies the result. Automation resolves flags, saved choices, then defaults without prompting. Cancellation before save changes nothing; partial apply preserves desired state for retry.
- AC-002: Metrics, logs and traces default to local in every environment. Each supports local, remote or both. Optional write-URL flags override Nebius endpoint templates without implicitly enabling remote export. Custom write/read endpoints and credentials are independent; no cloud credentials are sent to custom destinations implicitly.
- AC-003: Catalog Apps expose independent VictoriaMetrics, VictoriaLogs, VictoriaTraces and optional Pushgateway installations. Collector selection reuses compatible owned pipelines per signal/scope: native Soperator collectors first, existing compatible owners next, Nebius agent for unowned Nebius-only signals, VMAgent for other metrics, OpenTelemetry for other logs/traces. Never duplicate collection or silently adopt resources.
- AC-004: Local datasource types are prometheus, victoriametrics-logs-datasource and jaeger, respectively. Remote Nebius defaults are prometheus, loki and tempo. Repeatable --datasource NAME TYPE URL configures query connections; --default-datasource selects one. Names, stable UIDs, credentials, type validation and plugin provisioning are shared by CLI and wizard.
- AC-005: Local retention defaults are metrics 90d, logs 30d and traces 7d. Storage is persistent, private and separately owned from Grafana. Pushgateway defaults disabled, is asked in the wizard, and when selected has one replica, a retained 1Gi claim and five-second label-honoring scrape. Preserve explicit existing sizing and configuration.
- AC-006: Native routing changes use qualified upstream render adapters and protected lifecycle authority. Local-only removes each Nebius export and its associated per-destination settings, with matching graph dependencies, credential needs and alerting consumers. Keep source/render/live evidence separate and never delete database claims as a routing side effect.
- AC-007: Implementation, tests and documentation remain inside nebius-cxcli. Do not modify courses, generate course artifacts, fabricate course receipts, or add compatibility wrappers. Generic endpoint reporting and existing dashboard datasource mapping support unchanged clients; incompatible authenticated or query-language bindings fail explicitly.

- AC-008: The shared installation/Apps wizard explains storage placement and remote write destinations, previews effective automatic and saved Grafana read connections with types, URLs and the default, and asks optional customization with default No. Local setup requires no datasource fields. Customization offers add, edit and default selection with supported backend choices and immediate input validation; existing names and UIDs remain stable.
- AC-009: Missing custom remote read connections and invalidated saved defaults require guided resolution before saving. A changed write destination requires review of its retained read connection unless explicitly supplied. Explicit command options remain authoritative; headless interfaces and precedence are unchanged.
- AC-010: Wizard edits preserve complete saved datasource settings. Authentication remains configuration-managed: no secret values are prompted, printed or persisted, and authenticated datasource edits or custom write Secret changes cannot silently drop or transfer credentials. Cancellation at every prompt leaves source configuration and deployment untouched.
- AC-011: Native datasource preview acquires only verified release source defaults and reports progress before network work. One Grafana installation invocation reuses those raw defaults per exact release across selected-target setup, post-confirmation project qualification, save and render configuration processing. Reuse grants no target or deployment authority: authored values are merged independently, frozen generation sources take precedence, and full required-package admission still verifies current source identity and rechecks the tag before sealing. Clear defaults after success, cancellation or failure; a new invocation revalidates. Preview must not download the full release chart graph.
- AC-012: Before saving, Grafana setup resolves backend sources and introduces observability routing and Apps only for the selected target. A plain MK8s target performs no Soperator source lookup, even when other configured targets use Soperator. Whole-project structural and routing validation and ordinary non-network canonicalization remain active.
- AC-013: Interactive setup identifies the configured target and explains native collector reuse before source acquisition without claiming live health. Before confirmation it explains that the subsequent deployment qualification remains project-wide and may verify other configured components.

- AC-014: Grafana installation idempotently updates current configuration, renders current artifacts and invokes normal deploy with normal plan approvals, including other pending changes. No shared accepted deployment or old execution controls are prerequisites. Preserve configuration compare-and-swap, failed-apply desired settings, datasource identities, secrets and dashboard edits.
- AC-015: Repeating a named datasource option preserves its position, stable UID and complete saved authentication settings. Authenticated URL/type changes require explicit configuration-managed credential review and fail before mutation through the three-field option. Repeated installation preserves owned database and encryption Secrets, persistent storage, and existing API-managed dashboard edits.

- AC-016: Shared Soperator deployment admission classifies the fresh owned predecessor and frozen desired graphs before maintenance or job holds. Retained and added releases remain staged; only qualified, identity-bound removals may retire. The native telemetry token writer retires through its owning umbrella and normal Helm uninstall, with its exact child resumed under a suspended parent before pruning. Verify HelmRelease, Deployment and Pod absence before opening desired stages. Preserve token Secrets and persistent storage; unknown ownership, keep policies, hooks or unsupported inventory fail before mutation.
- AC-017: Interrupted same-release reconciliation may acquire retirement authority only through an explicit sealed successor at the validated failed-apply frontier. Preserve immutable bundle and execution controls, source and target maintenance preimages, scheduling holds and history. Bind fresh parent and child identities and predecessor source evidence; absence of a completed apply receipt does not prove no writes. Replay publication interruptions without recapturing mutation authority or inheriting acceptance success. Preserve the saved source configuration independently of expanded runtime defaults, and keep subsequent checks-policy publications under the same native graph checkpoint. Exact suspended child publication may materialize an explicit empty values map left null by server-side apply, using a durable preimage/postimage intent bound to child identity and the parent Helm revision. No other specification drift is admitted. Preserve ordinary first-install staging and qualify unchanged deployment alongside recovery. For an exhausted SSA upgrade followed by successful rollback, recover the exact VMAgent empty-map failure through a field-only, identity-checked materialization and one journaled Flux retry reset. Authenticate desired content against the frozen chart and effective values; preserve all other fields until normal Helm reconciliation applies them. Reject changed sources, references, ownership, preimages or a second failed retry. Private admission rendering preserves an authenticated compatibility observation across date changes only after fresh compatibility checks pass and all evidence except its observation date and derived digest matches exactly. Preserve Fast readiness receipts as lifecycle-owned files outside render replacement.

- AC-018: After successful Grafana installation or ordinary deployment, terminal output and the deployment report show identical target-bound commands for loopback port forwarding and admin-password retrieval, with a browser URL and login username. Derive Service ports and Secret references from current owned live resources. Never retrieve password values for presentation, automatically open a tunnel, or let private forwarding satisfy public Gateway readiness. Include local-only observability and successful resumed or unchanged deployment.
- AC-019: `grafana show --config PATH --target TARGET` verifies the exact live managed target on every invocation and constructs fresh access commands without cached-report fallback, application reconciliation or Grafana API access. It may refresh verified local kubeconfig access while preserving the current context and honoring persistence opt-outs. Commands reference a verified durable kubeconfig and explicit context. Missing, ambiguous, unready or unverifiable access fails clearly; post-deployment handoff failure remains distinct from accepted deployment success.

#### Negative Criteria

- NC-001: No unmanaged-cluster fallback, implicit config/context, MLflow/TensorBoard integration, automatic ownership migration, or unapproved infrastructure reconciliation.
- NC-002: Do not install OpenTelemetry merely because Grafana is selected, claim all-signal support from VMAgent, or infer production high availability from local storage.

#### Validation Method

Inspect canonical configuration, exact pinned Helm renders, collector ownership,
backend destinations, Secret references, persistence and generated datasource types.

#### Test Method

Exercise CLI/wizard parity, routing combinations, custom endpoints, ownership
conflicts, partial failures, native graph parity, datasource provisioning, and
independent backend Apps. Snapshot the unchanged course tree.

#### Evaluation Method

Qualify metric, log and trace ingestion/readback on an explicitly selected test
target separately from unit and chart-render evidence.

<!-- /REQUIREMENT: REQ-040 -->

<!-- maintain-project-specs:requirements:end -->
