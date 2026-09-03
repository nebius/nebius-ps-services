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
- AC-004: Artifacts are fetched directly from their official upstream authorities and verified against the release source.
- AC-005: Before canonical config or generated-render mutation, execution persists a cluster-bound active release intent; interrupted-operation recovery uses its frozen snapshot and never re-resolves `latest`.
- AC-006: A future release matching reviewed source-structural capability predicates is admitted without a cxcli version pin; chart names alone never grant mutation capability and an unknown contract fails before mutation.
- AC-007: Catalog source validation resolves the bundled official Soperator `latest` selector once through the same official resolver and validates the exact resulting chart version. Every non-Soperator Helm chart requires an exact immutable version; `latest` is rejected.
- AC-008: A successful official release resolution may publish an owner-only, short-lived sealed snapshot for the same normalized selector. A matching dry-run-to-execute handoff may reuse it only while fresh and only after revalidating the snapshot digest, pinned repository/tag/commit/tree identity, and content-addressed source cache; stale, missing, mismatched, unsafe, or custom-opener evidence returns to the official resolver and never becomes an alternate release authority.
- AC-009: Official GitHub API reads may consume `GH_TOKEN` or `GITHUB_TOKEN` from the process environment to raise the provider rate limit. The token is used only in the request header and is never written to configuration, caches, receipts, errors, or logs; custom resolver openers remain isolated from ambient credentials.
- AC-010: Each official chart package pull receives at most three isolated attempts with bounded exponential backoff and jitter only for transport timeouts or connection resets. Authentication, certificate verification, not-found, digest, archive-validation, and source-identity failures fail immediately. Failed attempt directories are removed and never become release authority. Exhausted transient failures expose only the attempt count and a sanitized failure category; public error cause chains do not retain raw transport details.

#### Negative Criteria

- NC-001: Do not use a bundled target-version lock as runtime release authority.
- NC-002: Do not add a mirror, proxy, fallback registry, offline artifact authority, or release-unpacking installer.

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

- AC-001: The verified upstream release owns Soperator controllers, CRDs, workloads, active checks, images, and release topology.
- AC-002: The cxcli adapter supplies only Nebius infrastructure identities, storage, networking, observability, and supported upstream values. The frozen official release owns the jail image; cxcli does not accept a second rootfs-image override.
- AC-003: Adapter inputs are validated against the selected release contract before mutation.
- AC-004: Onboarding persists only an explicit release-neutral NodeSet, partition, and optional accounting/exporter/REST service topology projection. Adopted NodeSets remain bound to their discovered node-group identities when target-profile defaults are materialized; arbitrary live container, environment, image, annotation, init-container, secret, and volume payloads are not copied into cxcli configuration.

#### Negative Criteria

- NC-001: Do not copy, fork, template, or publish upstream product charts in this repository.
- NC-002: Do not allow adapter resources to overlap resources owned by upstream.

#### Validation Method

Compare rendered ownership with verified upstream source and the adapter
allowlist.

#### Test Method

Render every supported capability contract and reject unknown values or
resource ownership overlaps.

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

- AC-001: `soperator install --release latest|X.Y.Z` creates the purpose-built MK8s infrastructure and installs Soperator.
- AC-002: `soperator onboard` records an existing Nebius MK8s cluster with an installed official Soperator release and does not choose an upgrade target.
- AC-003: `soperator upgrade` upgrades either a cxcli-installed or explicitly authorized onboarded cluster as one full-stack campaign: the official Soperator release, every required sequential Kubernetes minor hop, node OS, the Nebius-image GPU driver stack, and the release-defined Jail CUDA version.
- AC-004: `soperator discover OUTPUT_ROOT --tenant-id TENANT --project-id PROJECT --cluster-id CLUSTER` inspects one pre-existing MK8s cluster without reading or creating `config.yaml`. Tenant/project/cluster scope is required and verified before report binding; optional `--region-id` must match the derived live region, optional `--kube-context` must match the provider-generated kube-system UID, and `--access` defaults to `external`. Discovery writes a support-safe schema-v2 pair under `soperator-discovery/<cluster-key>/`: complete normalized cluster inventory in `report.json` and the exact bounded customer summary rendered on screen in `report.md`. The JSON inventory includes every provider node group and Kubernetes node plus the detected Soperator, GPU, Slurm, storage, topology, health, and collection evidence relevant to that cluster, while dedicated allowlist projectors exclude arbitrary labels, status messages, object specs, Secret or ConfigMap values, storage handles, raw command output, and unrelated namespaces. The summary is derived from that inventory, contains fixed cluster and component rows plus one row per provider or unmatched node group, reports `Ready/Actual/Target` counts and bounded configured/observed version, OS, and GPU-preset values, and never emits per-node rows; a cluster with 4,000 nodes and five groups remains below 50 logical Markdown lines and 16 KiB. Provider-group correlation uses exact provider ID first and unique exact name only when the node has no provider ID; contradictions and unmatched nodes produce `partial` instead of guessed attribution. Collection lanes distinguish `succeeded`, `failed`, and `not-applicable`, successful empty arrays remain distinct from failed reads, and `not-detected` is reported only after every Soperator-detection lane succeeds and proves absence. Candidate resolution, inventory order, summary order, null handling, and mixed-value compaction are deterministic. Pair publication is serialized per report directory, stages both artifacts under one report identity and digest, and publishes JSON last so its presence commits the matching Markdown. Discovery creates no workload, registration, render, or lifecycle receipt and performs no cloud/Kubernetes mutation. Shared Markdown contains only the relative artifact path, never the operator's absolute output-root path, and visibly escapes terminal controls, bidi/format controls, line separators, and Markdown structure in live-derived values before persistence or display. Evidence remains classified as `provider-configured`, `kubernetes-observed`, `runtime-observed`, or `unknown`; presets, images, chart metadata, and labels never prove exact runtime driver/CUDA versions. `complete` exits zero; `partial` and `not-detected` print and write the report before exiting nonzero. Onboarding never consumes this public report and re-collects its registration evidence independently. `soperator status` remains the registered-target configured/live view; explicit `--verify-observability` may write only its separate owner-only observation receipt.
- AC-005: `soperator upgrade --execute --approve` performs fresh authoritative discovery, freezes one parent campaign intent and receipt before mutation, and starts without a second planning command. The receipt binds cluster identity, the exact provider node-group ID set, requested selectors, exact release and Jail CUDA target, dynamic provider version inventory, sequential Kubernetes hops, per-hop node compatibility rows, rollout and Slurm policy, each child irreversible frontier, the exact ownership/backend authority, the initial config-plus-generated project snapshot, and a content-addressed chain of complete config-plus-generated project generations; dry-run discovery remains advisory only. Recovery accepts only the last durable operation preimage or postimage, rejects generated-project or provider-inventory drift, and never reselects or falls back to another infrastructure backend. While parent-campaign Slurm maintenance is active, protected controller-spool migration checkpoints are durably stored in that parent receipt and supplied to the release reconciler; the release child must not create a second maintenance owner or require its standalone Slurm journal. After every main-release opening, including one preceded by a clean completed-state check, completion re-proves the target SlurmCluster's exact shared spool PVC projection, removes only a legacy same-name claim template reintroduced by that opening, and requires the exact owned controller Pod to adopt that target claim and pass its mount-receipt gate. If the template-only change leaves the Pod on the legacy claim, the migration verifies its exact AdvancedStatefulSet owner, UID, resource version, and source claim before requesting lifecycle-aware controller recreation through Kruise's `apps.kruise.io/specified-delete=true` label. Replay of an already-completed outer declarative-release transition runs the same spool convergence before Flux readiness rather than bypassing the inner stage callbacks; a completed receipt that regresses this postcondition reopens at the same boundary.
  A completed or no-op declarative-release postcondition refreshes all frozen digest-bound sources before its Flux graph wait, so recovery does not trust stale-positive artifact status after source-controller storage replacement.
- AC-006: A target equal to the source is a validated no-op and a lower target is rejected. A no-op over a cxcli-owned Flux graph uses the frozen graph as its single readiness authority and requires the unique canonical main workload, the unique retained namespace owner as the only suspended release, current Ready generations, the frozen release version on every graph member, and only the product gates declared by that graph; a direct-upstream install without that graph retains native HelmRelease and product validation. When ActiveChecks are declared as required, product readiness evaluates only checks whose `runAfterCreation` value is true or omitted under the API default, requires at least one such check, and accepts only the official status for its declared type: `k8sJobsStatus.lastJobStatus=Complete` for the default `k8sJob` type or `slurmJobsStatus.lastRunStatus=Complete` for `slurmJob`.
- AC-007: Install, onboard, and upgrade use one operation model. Discover is the config-independent pre-onboarding information view; status is the read-only registered-target view.
- AC-008: No later than the first cluster or Slurm mutation, one forward supervisor takes ownership of the upgrade and keeps the same invocation reconciling until every completion gate passes. Slurm scheduling-gate, dependency, source, API, readiness, and login-access failures have no terminal attempt budget. Every supervisor backoff remains visibly active as one retry row with elapsed time, and retrying the release child never reprints the command's static direct-upstream plan. A staged non-main HelmRelease `Stalled` condition is an immediately retryable transition outcome and must not consume the full stage-readiness window before the same supervisor reapplies the exact declared graph. The Kruise child may delegate its controller-managed webhook lists and `metadata.annotations.template` certificate snapshots to that controller, but must keep both webhook configuration objects under Flux drift correction so deletion is recovered from the frozen release. After webhook recovery and before Kruise dependency readiness, the command restores only missing rolling-update partition defaults on resource-version-bound, Soperator-owned AdvancedStatefulSets; existing values and foreign owners remain untouched. Only an explicit terminal failure reported for the one non-source main Soperator workload whose exact GVK, namespace, name, source identity, UID, generation, and observed generation are frozen in the rendered operation graph may terminate the running command after that boundary; authority, identity, or protected-state ambiguity remains a self-reproving safety pause rather than a terminal exit. An explicit process interruption leaves the exact operation recoverable by rerunning the same approved upgrade command, without a separate upgrade resume command or flag. A missing local cxcli credential-plugin executable or module is invocation-environment invalidation, not a cluster retry: the supervisor exits without recording a terminal workload failure, preserves the campaign, and directs the operator to restore the same runtime and rerun that exact approved command.
- AC-009: `soperator destroy CONFIG --target TARGET [--dry-run]` is the only Soperator teardown path. It inventories the exact cluster and protected backing storage, prints explicit DESTROY and PRESERVE sets, and requires a TTY plus the exact phrase `destroy {cluster-id}` before execution.
- AC-010: The only Soperator-related root command is `soperator`, and its public command set and help order are exactly `install`, `discover`, `onboard`, `upgrade`, `status`, and `destroy`. Named option invocation order is arbitrary, while declaration/help/example order is stable and contract-tested. Discover accepts only its output root plus tenant/project/cluster/region/context/access inputs; release and platform preview selectors belong exclusively to upgrade.
- AC-011: Status reports the configured and live release plus any active Soperator lifecycle operation type and phase, safety-pause or failure classification, receipt path, and exact rerun command without changing cluster or receipt state. For full-stack upgrade it projects the parent campaign and supervisor before child release receipts. A running, retrying, safety-paused, or terminal final-only revalidation of a completed campaign remains visible while retaining its last-known-good evidence. Completed upgrade history separately prints its frozen backend/compatibility tuples and per-group GPU runtime report references without becoming an active operation or substituting for a fresh live Helm or Flux read. `migrate node-group` is not a Soperator lifecycle operation and is visible only through its own command and report.
- AC-012: Install, onboard, upgrade, and status expose paired `--interactive/--no-interactive` flags; discover is explicit/non-interactive and destroy retains its TTY phrase gate. When a new interactive install or upgrade omits its release selector, cxcli resolves the current official latest release and prompts with a runtime-resolved default such as `latest(4.1.7)`. A fresh non-interactive install requires its explicit release selector. A fresh non-interactive full-stack upgrade requires `--to-release`, `--to-k8s-version`, `--to-os`, and `--to-gpu-stack-preset`; missing selectors fail before configuration or cloud access. Install resume rejects a release selector and all fresh-install identity, profile, network, subnet, and overwrite options, then reuses the exact authority frozen in its saved plan. `install --resume --dry-run --replan` may replace only a readable, never-executed receipt whose status is exactly `planned` and which has no execution checkpoint; it preserves the saved project, target, configuration, generated manifest, and frozen release, then creates a replacement Terraform plan and approval fingerprint. Explicit or accepted `latest` values are frozen before mutation. Interrupted upgrade recovery may omit every selector in both interactive and non-interactive mode; if a selector or policy is repeated it must exactly match the parent campaign receipt, and recovery never prompts or dynamically re-resolves a frozen value. Direct existing-config onboarding rejects project-creation flags and keeps its configured region authoritative; an explicit region is an additional assertion and cannot replace it. Fresh deployments-root onboarding derives an omitted region from the live cluster without prompting and preserves that derivation across an interrupted scaffold through an owner-only marker published first and bound to the exact scaffold config hash. Marker and initial config publication share the config lock, and config creation is atomic and create-only so a competing writer is never overwritten. The marker and hash are re-proved after discovery and under the config lock before the observed region can be written.
- AC-013: The developer CLI contract and installed-wheel verifier bind the exact six-command order, command and argument descriptions, option names, declaration/help order, parser-required options, conditional requirements, defaults, help text, structural flag properties, paired forms, repeatable forms, selected epilog clauses, and cross-option constraints.
- AC-014: The full-stack upgrade wizard dynamically calls the Nebius control-plane versions API, defaults the Kubernetes target to the highest same-major endpoint reachable through a contiguous provider-supported minor path, and freezes every hop. It selects the latest provider OS first and then the latest compatible Nebius drivers preset independently per node group and hop; exact and keep selectors must remain valid at every hop. A node group one minor behind the live control plane is caught up at the current control-plane minor before later hops; a larger or cross-major lag fails before mutation. The control plane upgrades before node groups on later hops; its gate requires matching desired and actual status versions, `RUNNING`, non-reconciling state, and two stable observations. Readiness re-reads desired capacity and resets stability on capacity changes. Every stable zero-capacity group receives two provider desired-template observations and records live GPU/CUDA evidence as not applicable; every desired-positive group requires full rollout and applicable live validation. Final completion requires provider rollout readiness plus generated Soperator, Kubernetes, and applicable GPU validation, fresh reconciliation of every frozen digest-bound Flux source, re-frozen HelmChart artifact identities, and current-generation Ready state for the complete rendered release graph. Replaying the exact command for an already-completed campaign reruns only that final postcondition, updates its final evidence, and does not reopen Slurm maintenance or repeat provider mutations. Kubernetes inventory prefers the rendered `nebius.com/node-group` name and falls back to the provider-native `nebius.com/node-group-id` identity for onboarded nodes. Soperator validation requires the canonical `sconfigcontroller` deployment and adapter-owned read-only GPU driver root mount, while separate Jail-runtime validation proves CUDA/NVML library and device access. Runtime inventory, deploy-smoke, smoke, benchmark, GPU-stack, and CUDA-visibility reports are lifecycle evidence excluded from render-owned project snapshot authority.
  Global exact GPU selectors ignore driverless groups, exact per-group driverless overrides fail, ambiguous group aliases fail, provider inventory order is canonical, and every tuple is revalidated immediately before its individual group mutation. Each desired-positive GPU group requires exactly one non-skipped successful scoped CUDA report. Final completion refreshes and proves Flux sources/graph before runtime validation, then rechecks the graph and exact all-group count/resource-version snapshot afterward; any capacity or identity change retries final readiness. Provider compatibility reports a Nebius drivers preset, not an exact NVIDIA driver build; the bounded CUDA canary proves execution and retains an attempt-unique SHA-256-bound report but does not claim exact host-driver or runtime-CUDA versions. A failed replay cannot overwrite last-known-good report artifacts, and a completed campaign replaces last-known-good final evidence only after successful revalidation.
  When all target GPUs are already allocated to Slurm workers, the bounded CUDA canary must map each selected node to exactly one Ready `slurmd` pod owned by the selected Soperator instance and requesting every advertised node GPU, then produce a non-skipped report only after `nvidia-smi` returns one inventory row per advertised GPU and CUDA Driver API initialization passes there.
  Final source and graph proof resolves the selected target's Flux directory rather than the project-wide Flux root.
- AC-015: `migrate node-group` is the only guarded platform, hardware preset, CPU/GPU kind, GPU-cluster, reservation, or fabric migration path. It has no `upgrade node-group` alias. For one Terraform-managed source group it creates a permanently named replacement, proves readiness, enters source-worker-scoped Slurm maintenance before dual placement, applies placement through a migration-owned Flux path without invoking Soperator upgrade receipts or supervision, cuts workloads over, retires the source, restores autoscaling and its own maintenance, proves replacement-only provider state and a final Terraform no-op, and records a durable forward-only receipt once cutover starts. Its receipt, report, recovery, config-generation chain, and maintenance journal are independent of `soperator upgrade`.
- AC-016: `upgrade node-template` remains the in-place rolling path for Kubernetes version, node OS, and Nebius-image GPU driver preset on existing node-group identity. `soperator upgrade` composes that ownership boundary for a whole managed Soperator cluster; it never changes hardware or fabric. Admission must classify exactly one infrastructure authority: cxcli-managed targets use Terraform for every MK8s/control-plane/node-template/OS/provider-driver mutation, while onboarded targets use Nebius provider APIs under the same newly approved `--execute --approve` campaign. The exact ownership-selected backend and internal provider authority are frozen in the campaign digest. There is no backend override, cross-backend fallback, or backend switch during recovery; both authorities share only the Soperator release/Flux path.
- AC-017: Long-running upgrade discovery, release verification, compatibility, admission, operation-authority acquisition, maintenance entry, provider, Flux, runtime, and restoration phases emit progress on stderr without changing stdout plans or results. The static plan renders every adjacent Kubernetes transition beginning at the observed source minor, while the provider table labels destination-minor rows as target compatibility. A terminal uses one left status slot whose spinner becomes a green success check, red failure mark, or dim skip mark in place; each completed row and its elapsed time is committed to terminal scrollback, while the live surface contains only the active phase and supervisor retry notices use the same stderr console. Non-TTY logs use stable ANSI-free phase records plus at most sixteen deduplicated `INFO` milestones per phase. Expired cluster-Lease takeover reports its bounded prior-writer quiescence proof, and maintenance entry reports partition pause, active-job handling, reservation creation, and barrier convergence without changing their safety gates; repeated barrier passes share one non-TTY milestone key while continuing to update the terminal row. The outer maintenance renderer pauses reentrantly while a Slurm table, live dashboard, or prompt owns the terminal, resumes afterward, and always resets its pause binding. Flux controller manifest, rollout, and migration subprocesses never write successful resource chatter through the live renderer: cxcli captures it and reports bounded apply counts, controller totals, and migration counts grouped by kind. Parsing and progress callbacks are presentation-only; command return codes, authority checks, timeouts, migration order, and API postconditions remain authoritative. Normal campaign output is bounded by node-group count, with exactly one row per group containing the complete frozen per-target compatibility path; exact node evidence remains owner-only. Failures show bounded sanitized diagnostics and interruptions leave no false success state or orphaned live display.

#### Negative Criteria

- NC-001: Generic `create` must not install Soperator.
- NC-003: Generic `destroy`, `component remove`, and generic Flux or Terraform mutation paths must reject registered Soperator targets and direct the operator to `soperator destroy`.
- NC-004: Do not provide a non-interactive destroy approval flag or a discover-based upgrade preview path.
- NC-005: Do not silently select release, Kubernetes, OS, or GPU driver targets for a fresh non-interactive upgrade. Do not accept release or fresh-install configuration options for install resume, or let any supplied upgrade-recovery selector override or re-resolve the interrupted operation's frozen authority.
- NC-006: Do not replan a missing, corrupt, started, failed, partially applied, or completed install receipt, and do not let an earlier approval fingerprint authorize a replacement plan.
- NC-007: Do not translate legacy node-group checkpoints into migration receipts, mutate hardware through `soperator upgrade`, restore the source after a migration cutover frontier, or declare a full-stack campaign complete while its operation-owned Slurm maintenance is still active.

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
- AC-004: Protected storage is represented by one storage-neutral identity contract with canonical physical SFS and an explicit optional VM-NFS variant. Install, admission, operation, recovery, upgrade, and destroy bind the same receipt digest.
- AC-005: A managed install defaults every Soperator-created physical SFS filesystem's optional Nebius `forbid_deletion` provider control to `false`, preserves an explicit user selection of either value, and a managed destroy uses a saved target-scoped Terraform plan that contains only the selected MK8s module closure.
- AC-006: An onboarded destroy deletes the exact registered cluster through the Nebius API only after in-cluster cleanup and protected-storage identity proof; both ownership kinds verify all preserved backing storage after cluster absence.

#### Negative Criteria

- NC-001: Do not add Terraform resources for in-cluster Soperator installation.
- NC-002: Do not require cxcli to own the existing cluster infrastructure before onboarding it.
- NC-003: Do not make VM/NFS-specific Helm values a prerequisite for a physical-SFS install or upgrade.
- NC-004: Do not allow a managed destroy plan to delete, replace, or mutate SFS, another target, or unrelated infrastructure.

#### Validation Method

Audit generated Terraform and Kubernetes mutation plans by ownership boundary.

#### Test Method

Run architecture guards that reject Soperator Helm or Kubernetes resources in
Terraform output.

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
- AC-007: Destruction uses `nebius-cxcli.soperator-destroy.v2`, binding the target, project, cluster ID, Kubernetes UID, destroy and preserve inventories, approval fingerprint, verification-only storage checkpoints, and terminal status. Once cluster deletion is accepted, rerun resumes polling and verification without repeating earlier mutations.
- AC-008: One receipt-driven forward supervisor starts no later than the first Slurm or cluster mutation and owns scheduling-gate recovery, release reconciliation, protected-state restoration, and terminal sealing. It distinguishes typed main-workload terminal failure from retryable failure and self-reproving safety pause; operation recovery does not depend on a second outer retry loop or a deprecated mutation-intent ConfigMap.

#### Negative Criteria

- NC-001: Do not translate or accept superseded operation formats.
- NC-002: Do not silently replace a frozen snapshot after interruption.
- NC-003: Do not accept version equality, a release name, or an unverified chart label as sufficient onboarding provenance.
- NC-004: Do not persist raw rendered values, Secret payloads, kubeconfig material, or credentials in registration, operation, discovery, or destroy evidence.

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
- AC-002: `/home`, `/data`, `/scripts`, and `/models` are mandatory retained path-specific PVC mounts outside the versioned active/passive rootfs slots. During first rootfs adoption, the interactive upgrade wizard may add validated existing data-directory mounts without copying their content; later slot-backed upgrades may retain or remove only already-backed optional mounts. The approved normalized set is persisted only with successful canonical upgrade promotion.
- AC-003: cxcli freezes the digest-pinned official target image, the admitted live rootfs identity, and the selected persistent-path boundaries without materializing source or target images in a reference scratch PVC. Selected persistent paths are customer-data ownership boundaries whose retained PVCs intentionally shadow target-image content at the same paths; outside those paths, the target image replaces the live rootfs in full. Unselected drift produces only content-free informational evidence and never blocks admission. After the forward-only commit begins, cxcli populates the exact empty passive slot once and inventories that materialization once as the canonical target-rootfs receipt.
- AC-004: The sole target jail authority is the digest-pinned image frozen from the selected official Soperator release. cxcli does not preserve, merge, export, publish, or accept a replacement image for unselected packages, system files, or rootfs customizations.
- AC-005: The effective target image is identical across rendered upstream values, passive population, operation identity, release intent, recovery journal, interrupted-operation recovery, and receipt. Preflight verifies the exact adapter-owned passive PVC, its provisioner and capacity, and has no `emptyDir`, jail-store, reference-copy, or implicit-StorageClass fallback. Immediately before the first passive-slot write, cxcli reasserts its lease and freshly proves that PVC empty, UID-matched, and unreferenced. Every Job is bound to the exact Kubernetes context, image, PVC, fencing epoch, and admitted workload identity; completed mutation stages require their checkpointed Job UID and admitted identity instead of being recreated. Pre-activation recovery repeats the passive consumer proof, while recovery after exact target-release adoption verifies the sealed materialization and completed Job evidence without requiring the now-active PVC to be unconsumed. Recovery reuses that sealed receipt rather than reclassifying under a new fence, and the original rootfs remains retained as recovery evidence.
- AC-006: Scheduling is held during the single-writer controller/accounting handoff. cxcli preserves the login Service, allocation, and reachability when the target permits, samples Service identity, ready EndpointSlices, and TCP/22 reachability at admission and supervisor retry or safety-pause boundaries, and treats any availability loss as advisory; SSH host-key material remains protected customer state.
- AC-007: Source-release reconcilers and Helm ownership are retired from the live cluster only after exact target ownership and product readiness are proved. Completed-transition replay treats an exact captured legacy HelmRelease as retired only when Kubernetes' optional-object lookup succeeds with no object; every other lookup failure remains fail-closed.
- AC-008: After mutation begins, login loss, Slurm scheduling or command/control interruption, Kubernetes or Flux source and readiness delays, and dependency failures are retried forward and do not terminate or roll back the operation. The sole terminal post-start failure is a typed terminal state of the one non-source main Soperator workload whose exact GVK, namespace, name, source identity, UID, and observed generation are frozen in the rendered operation graph. Observability verification is a separate explicit status action and is not part of this operation graph.
- AC-009: Lost or ambiguous fencing authority, protected PVC or Secret identity drift, conflicting writers, journal corruption, or an unclassifiable mutate-once outcome enters a non-mutating safety pause. The running command periodically re-proves safety and resumes automatically when the conflict is resolved.
- AC-010: Read-only discovery and staged render validation precede approval. After approval, the Lease and non-customer operation evidence remain an operation-owned preflight with no canonical config, Slurm, Service, release, or customer-PVC mutation. Only a sealed target-wins rootfs decision and exact admission receipt permit the active intent and recoverable canonical promotion transaction to establish the forward-only upgrade commit; passive-slot population is part of that committed transaction, never admission.
- AC-011: The protected-state strategy consumes the storage-neutral protected-storage receipt. Physical SFS is the canonical managed topology, and an explicitly identified VM-NFS topology is supported without requiring `externalNfs.server` for SFS-backed clusters.
- AC-012: Optional persistent paths must be normalized absolute real data directories in the admitted live rootfs and on the same physical SFS, and must not traverse symlinks or overlap protected system, slot, generated, runtime, external-NFS, or other persistent mounts. A selected data subtree may descend from `/usr`, `/opt`, or `/etc`; its retained PVC deliberately wins over official-image content at that subtree. cxcli must fail before maintenance rather than invent, copy, or silently redirect a newly selected path on an already slot-backed installation.
- AC-013: The inactive rootfs slot is a disposable versioned release surface rather than permanent customer storage. First adoption requires it logically empty; a later approved upgrade may recycle only the exact unconsumed inactive slot through a write-ahead, identity-bound cleanup stage while retaining the former active slot until that next upgrade.
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
- AC-003: Before destroy approval, cxcli freshly inventories all namespaces and workloads that disappear with the selected cluster and every physical SFS, PVC/PV binding, VM, disk, address, allocation, or export that must remain. Before the first cleanup mutation it acquires the common local and cluster-visible writer fences and re-proves that the approved cluster-wide workload and protected-storage inventories are unchanged.
- AC-004: In-cluster finalizer and storage-detach cleanup completes before cluster deletion. Cleanup, identity ambiguity, or failure to prove that every preserved backing-storage identity exists blocks the delete frontier. Nebius `forbid_deletion` is an independent optional provider control and is not an admission requirement or cxcli storage-preservation mechanism.
- AC-005: After the exact cluster is absent and every protected backing-storage identity still exists, cxcli transactionally removes only the selected Soperator app, deploy target, and managed MK8s rows; managed SFS rows preserve the user's `forbid_deletion` selection and the remaining project renders successfully before the destroy receipt completes. The receipt binds both the approved config digest and the exact post-cleanup config digest so interruption after the config write resumes rendering and sealing without overwriting later operator edits.
- AC-006: Soperator command flags, mutation APIs, retry supervision, and target-wins rootfs admission are owned exclusively by the six-command lifecycle and common operation engine. No reachable legacy rootfs classifier, historical source-image authority, or alternate source-owner adoption path remains.
- AC-007: Unit and architecture tests cover the sole root group, all six commands, both ownership kinds, both storage variants, install and interrupted-install recovery, onboarding provenance, canonical discovery reruns, upgrade recovery, destroy interruption at every frontier, status recovery output, and package boundaries.

#### Negative Criteria

- NC-001: Destroy must never delete physical SFS, PVC backing filesystems, VM/NFS instances, disks, addresses, allocations, exports, or unrelated project resources.
- NC-002: Do not remove protected storage or local registration state when cluster deletion is unproved, an install or upgrade operation is active, writer fencing is unavailable, the approved inventory changed, post-delete storage verification fails, the config changed after approval, or the remaining configuration cannot render.
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
retirement, with the selected cluster gone, all protected storage present, and
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
- AC-003: CI checks every supported Python minor, Ruff lint and formatting, diff hygiene, static typing, branch coverage, the full offline suite, and the same exact installed-wheel CLI contract under Python 3.12, 3.13, and 3.14.
- AC-004: The measured global branch-coverage floor is nondecreasing, and safety-critical supervisor, ownership, protected-storage, transaction, and compensation modules meet a separately enforced focused branch-coverage floor. Proposed coverage floors may only rise, the package mypy error ceiling may only fall, and the Ruff-format offender allowlist may only shrink relative to the merge base.
- AC-005: One canonical machine-readable contract covers the root, every public command group and leaf command, global options, arguments, option spellings, requiredness, defaults, choices, repeatability, visibility, ordering, and selected help clauses. Hidden internal commands are recorded separately and never appear as public commands.
- AC-006: Installed-wheel verification imports only the isolated artifact, renders every public help surface, exercises the version path, and reaches every public callback through a deterministic fail-before-external-effect case.
- AC-007: Every Python-backed Make target enters one shared uv environment boundary. The committed lock must be current, a validated whitespace-free custom `VENV` maps to `UV_PROJECT_ENVIRONMENT`, lock inspection and exact locked synchronization use the selected supported Python without automatic downloads, synchronization is serialized per resolved environment, and isolated wheel-build dependencies are hash-constrained by the same lock without a second installer authority.

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
supported Python matrix.

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

<!-- maintain-project-specs:requirements:end -->
