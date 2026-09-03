# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses
[Semantic Versioning](https://semver.org/) with Git tags as the source of truth.

## How to use this file

- Keep `## [Unreleased]` at the top and add bullets as changes land.
- Before tagging, move items from `Unreleased` into a new
  `## [nebius-vpngw-vX.Y.Z] - YYYY-MM-DD` section, then leave an empty `Unreleased` section.
- Newer releases go above older ones; do not reorder entries within a release.
- The release helper (`publish-release.sh`) automates rolling `Unreleased` into a dated `## [nebius-vpngw-vX.Y.Z] - YYYY-MM-DD` and re-adding an empty `Unreleased`.

## [Unreleased]

## [nebius-vpngw-v0.6.0] - 2026-09-03

- Restored reproducible Python 3.12 installs by bounding Click before its 8.4
  rendering changes, Typer before its 0.26 Click-vendoring change, and the
  Nebius SDK before its 0.4 generated-API replacement. CI semantic CLI
  assertions now normalize captured terminal styling and use a stable terminal
  height, and VM-HA TLS contexts expose their existing TLS 1.2 minimum directly
  at construction for static security analysis.
- Fixed post-promotion VM-HA restoration for an exact already-running,
  alias-free standby. The rearm service now adopts the committed authorization
  without issuing a Compute start, rejects foreign same-receipt checkpoint
  identity, and the controller serializes terminal restoration completion or
  blocking through the shared rearm writer lock.
- Fixed planned VM failover/failback terminal verification retaining a cloud
  reader after its authenticated SDK owner had already closed. The public
  transfer command now owns one manager lifetime across preparation, cutover,
  and restored-standby proof; terminal cloud reads remain independent and
  fail-closed within the existing phase deadlines.
- Fixed idempotent `prep-network` verification for public allocations already
  assigned to their exact intended gateway VM/NIC. Nebius reports these stable
  attachments as `ASSIGNED`, while unassigned reservations remain `ALLOCATED`;
  inconsistent state/assignment pairs and foreign attachments still fail
  closed.
- Fixed managed VM-HA credential enrollment when Nebius rereads an omitted
  authorized-key expiry as a present zero-valued protobuf timestamp. Only the
  exact zero sentinel is accepted as non-expiring; every nonzero expiry still
  fails closed, and crash-resume retains the already verified IAM identity and
  pending private key without creating a replacement.
- Stopped steady-state VM-HA apply from rereading the ordinary gateway SSH
  predecessor after the migration lifecycle is `ACTIVE`. The ordinary receipt
  remains bounded to provisioning and activation; subsequent applies use the
  already-published VM-HA receipt and retain exact Compute reproof.
- Reduced serial unit-test feedback without changing production timing or
  weakening selection, existing assertions, or isolation. Four tests covering
  asset selection, allocation release, and allocation identity now record exact
  retry calls or stub an unrelated SDK lookup instead of waiting through
  production pacing. Their five-sample
  median fell from 28.59 seconds to 0.46 seconds; one complete instrumented
  2,256-test run fell from 51.48 seconds to 23.51 seconds with identical
  passing outcomes.
- Completed fail-closed Nebius collection discovery across every public command
  path. One bounded paginator now buffers all SDK or raw-stub pages, preserves
  request scope and VM-HA retry options, and rejects malformed responses,
  non-string or cyclic tokens, page-bound exhaustion, duplicate stable IDs,
  and provider failure without returning partial data. Configured VM, disk,
  allocation, subnet, network, and route-table discovery uses exact lookups
  with parent/name/ID validation and typed `NOT_FOUND` absence. `status` reports
  unavailable route evidence instead of an empty table, while preparation,
  apply, ordinary route reconciliation, destroy, IAM, and VM-HA transfer paths
  cannot authorize later effects from an incomplete inventory. The CLI,
  configuration, and persisted-state contracts are unchanged.
- Preserved installed ordinary gateways while making managed SSH trust usable
  for immediate VM-HA conversion. Actual ordinary `apply` now serializes per
  project/gateway and can enroll one unchanged pre-branch VM exactly once via
  stable Ed25519 H1/cloud/H2 observation, pinned client authentication, guest
  identity correlation, and final Compute reproof; dry-run stays write-free
  and exits nonzero when enrollment is required. Exact configured client-key
  selection disables unrelated agent/default keys and password fallback.
  Authority-bearing v2 receipts record the bounded exception, and `vm-ha`
  requires the ordinary receipt before candidate or allocation effects, then
  imports its exact pin, predecessor digest, and current Compute binding into
  the approval-bound HA receipt. Ordinary pushes no longer stage or install
  VM-HA systemd/firewall assets.
- Isolated unit-test writer locks per test process so pytest-xdist workers do
  not contend over reused fixture identities, and ignored generated destroy
  lifecycle receipts and write locks alongside the existing VM-HA state files.
- Made VM-HA runtime credentials fully managed by `apply`. Both wizards and
  public YAML now omit credential paths and show one derived operator location
  under `~/.config/nebius-vpngw/credentials/<project>/<gateway>/`. After the
  exact plan is approved, apply creates or reuses one product-labeled
  `<gateway>-vm-ha` service account/group and one non-expiring RSA-4096/RS256
  authorized key, publishes a crash-resumable mode-`0600` credential source,
  and installs separate identity-bound copies on both members. Read-only
  planning now binds each IAM create/reuse action and reused resource identity;
  reuse cannot add a missing grant, generated SDK operations are awaited to
  terminal completion, source drift is rejected before IAM effects, and
  interrupted one-file cleanup residues resume safely. VM-HA rejects
  `--sa`, active lifecycle identity drift fails closed without rotation, and
  destroy retains the managed local and IAM state.
- Fixed managed VM-HA credential inspection under CLI federation profiles.
  IAM preflight now preserves an explicitly supplied `NEBIUS_IAM_TOKEN` and
  otherwise uses the same renewable, bounded CLI-token bearer as gateway cloud
  discovery. Explicit-token commands no longer require a local CLI config when
  no profile endpoint needs resolving, and an explicit endpoint is passed
  directly to the SDK. Selected profiles still supply endpoint context, while
  the shared renewable bearer now serializes refreshes safely across threads
  and event loops. The supported SDK range is bounded to the versions covered
  by these authentication regressions.
- Fixed read-only VM-HA `status` and `vm-ha` use of the deployment-wide managed
  SSH receipt. Managed trust is now resolved once for the complete member set
  and shared by the member probes, while an explicit known-hosts override keeps
  per-member failure isolation. No trust enrollment, cloud mutation, or guest
  mutation was added to either command.
- Clarified the interactive `create-config` subnet prompt so it states that
  `prefix_length` controls only auto-creation when CIDR is blank and the named
  subnet is absent, while an explicit CIDR's embedded prefix is authoritative.
- Fixed interactive `vm-ha` conversion appearing to hang at configuration
  resolution. The resolve spinner now stops before the first blocking wizard
  prompt, and a separately confirmed passive-IP preparation uses its own
  progress phase without changing the existing cloud or publication boundary.
- Render the normal initial `apply` message `SSH not ready yet` with the
  terminal's default foreground instead of error-red styling while retaining
  red output for an actual bootstrap timeout.
- Fixed ordinary `apply` failing after successful VM creation when a reusable
  public address carried a stale SSH pin. Fresh ordinary gateways now receive a
  product-owned, pre-pinned Ed25519 server identity through cloud-init;
  retained identity mismatches still fail immediately, while ordinary SSH
  transport/bootstrap readiness continues to use the existing bounded wait.
  Apply publishes deployment trust before Compute mutation, and later status,
  route, restart, failover, and failback consumers reuse it without modifying
  operator-owned `known_hosts` files. Explicit trust overrides remain
  highest-precedence and fail closed. Recreate preserves or recovers the exact
  original product identity, every retained trust source is rebound to the
  current Compute object before use, and bootstrap probes honor the configured
  management username.
- Fixed checkpointed ordinary-to-VM-HA conversion across SSH, passive recovery,
  and route cutover. The retained active now uses exact product provisioning
  identity instead of a potentially stale general `known_hosts` address pin,
  strict SSH evidence is refreshed after product-owned Compute changes, passive
  replacement is offered only after a recorded asymmetric bootstrap timeout,
  and an exact approved ordinary route receives HA authority labels only after
  its verified shared-allocation successor exists. The reconciliation mutation
  now consumes the migration ledger adopted in that same controller cycle, so
  a clean first attempt does not depend on a prior failed retry.
- Added `-c` as the consistent short spelling of `--local-config-file` on every
  operational command. `create-config CONFIG_FILE` and
  `validate-config CONFIG_FILE` remain positional, and no root configuration
  option was added.
- Made the guided `create-config` flow provider-neutral with `site-1`/`generic`
  defaults, routing-before-ASN prompts, and hidden hybrid PSK input that stores
  uppercase names as `${NAME}` or other valid input as a literal without
  rendering secret bytes. `prep-network` now supports TTY-default
  `--interactive/--no-interactive` reuse of eligible subnet-bound public
  allocations, completes partial IP matrices, reuses exact-name subnets with
  creation-only prefix settings, verifies or safely repairs the default-egress
  route, publishes YAML through a mode-`0600` fingerprint guard, and fails
  closed on shared/conflicting network state or incomplete postconditions.
- Fixed `status` and `list-routes-local` gateway discovery when the configured
  VM is not present on the first Compute instance-list page. Both commands now
  query the exact configured VM names, treat only a typed `NOT_FOUND` response
  as absence, and fail with a sanitized error when discovery is ambiguous.
- Fixed VM-HA `destroy` planning when a canonical product route from an
  ordinary lifecycle still targets an exact lifecycle-bound private
  allocation. Complete HA labels or the exact unlabeled product name and next
  hop now establish route ownership; partial/conflicting labels and foreign
  identities remain blocking. Destroy no longer depends on guest/controller
  readiness or a successful stop: exact non-stopped Compute deletion is proved
  as the fence before route cleanup. Exact current, replacement, and retired
  lifecycle Compute identities are included. Terminally failed predecessor
  cloud operations release their checkpoint for a fresh inventory, unsupported
  operation lookup replays the exact durable idempotency key, and a reported
  failure after an effect is accepted only when the exact postcondition is
  already true. A destroy-owned terminal failure whose resource remains is
  durably superseded with sequence-qualified attempt history instead of being
  resumed forever. Failures now render closed phase-specific destroy reason
  codes instead of the generic controller condition.
- Replaced the ordinary-only best-effort `destroy` loop with one topology-aware,
  identity-bound workflow for ordinary and VM-HA gateways. Destroy now holds the
  canonical writer lock, checkpoints accepted cloud operations, stops members,
  deletes managed routes, Compute, boot disks, and private allocations in
  dependency order, and commits success only after two agreeing absence reads.
  Public allocations, VPC/subnet/route-table containers, foreign routes, peer
  and IAM resources, local configuration, and receipts remain preserved.
  Interrupted runs resume without resubmission, same-name replacements and
  foreign allocation references or reassignment fail closed, VM HA records
  explicit `DESTROYED` state, and later apply starts a clean provisioning
  transaction. Terminal verification now also proves that every retained public
  allocation is stably detached and ready for same-config apply. Clean
  reprovision carries and reproves only those exact retained public IDs, so it
  also works when `external_ips` is omitted; regression coverage follows the
  VM-HA lifecycle from `DESTROYED` back to `ACTIVE` without mutating the config
  or inheriting deleted cloud identities.
- Fix standby-replacement readiness polling to make one final exact owner-state
  observation at the wait deadline, avoiding a false timeout when the serving
  owner completes its guarded local recovery during the last SSH probe.
- Fix missing-standby activation when the retained owner has the exact
  deterministic initial enabled auto-healing decision but no surviving peer
  acknowledgement. The fresh replacement may acknowledge only that default
  decision, then the command runs the canonical two-member enabled transaction
  before releasing replacement inhibition. Interrupted prepare or commit phases
  resume only that deterministic successor transaction; any nondefault
  unacknowledged owner decision remains blocked.
- Fixed interrupted missing-standby recovery when an older apply-owner adoption
  left a terminal restoration authorization bound to the prior promotion
  receipt. The exact guarded pending-inhibition checkpoint now forces the
  current owner artifact, retires only a structurally valid same-authority
  terminal record under the existing writer lock, and resumes before any disk
  or Compute creation. After a capability refresh, the exact authoritative
  owner can now replay only its local dataplane preparation, route
  reconciliation, and forwarding restoration behind the replacement
  inhibition; ownership-changing actions remain fenced. Its bounded controller
  acknowledgement spans one complete cloud-read deadline and retry. Active,
  malformed, or foreign restoration state still fails closed.
- Fixed interrupted VM-HA convergence so activation reuses the final service
  assets installed from the exact approval-bound wheel instead of requiring or
  consuming volatile `/tmp` copies. The peer-firewall helper now runs from its
  verified final path, and activation materializes the UFW lock from the final
  tmpfiles policy before refreshing services.
- Fixed apply-owner adoption so an exact terminal standby-restoration
  authorization is durably retired before its promotion receipt is replaced.
  Active or invalid restoration authority now fails closed with the prior
  receipt preserved instead of leaving healthy auto-healing projected blocked.
- Added idempotent `vm-ha` recovery for one authoritatively absent ACTIVE-
  lifecycle non-owner. Interactive text mode now asks one default-No creation
  confirmation and completes the replacement in the same invocation; JSON,
  noninteractive, and dry-run output retain the exact
  `active-standby-replacement` digest and `--approve` automation contract.
  VM-HA-owned convergence uses the `vm-ha-required` classification. The
  command persists approval before effects, proves an
  exact cloud-missing member identity before ignoring any stale configured IP,
  and does not SSH-probe that absent member while planning replacement. It then
  proves an owner-preserving operation-bound transfer inhibition after fresh
  authority revalidation, creates a configured-name Compute with a fresh cycle-qualified
  boot disk, binds lost-ack adoption to accepted cloud-operation resource IDs,
  preserves all old disks and serving network authority, releases inhibition
  through a durable idempotent receipt, and resumes the same transaction after
  interruption. A live-peer-capable serving owner is not reinstalled or
  restarted: only the replacement standby is installed and activated, managed
  mTLS supplies its new Compute identity live, and the owner runtime binding is
  published atomically. Older owners select a distinct combined upgrade,
  restart, and replacement approval that truthfully reports possible traffic
  interruption. Controller acknowledgement of replacement inhibition now uses
  a bounded typed poll and reports the resumable
  `standby-replacement-inhibition-not-ready` checkpoint on timeout.
  VM-HA result impact also reports the additive `resource_creation` dimension.
  When the absent member's product-managed private SSH key is also gone, the
  same approval now binds and checkpoints a retry-stable managed identity
  rotation before any cloud creation; explicit operator trust/key paths remain
  immutable, and checkpointed rotation retries now retain the typed
  `replacement-ssh-identity-unavailable` prerequisite instead of collapsing to
  a generic trust or convergence failure. Their next action now distinguishes
  operator-source drift from loss of the exact managed trust predecessor.
  Persisted replacement planning also retains the prior ACTIVE allocation and
  route runtime authority through its `PROVISIONING` checkpoint for the exact
  serving-owner capability preflight, instead of collapsing to a generic
  convergence failure before cloud creation. A pre-capability interrupted
  inhibition intent can now be rewound only when no guarded or accepted cloud
  operation and no replacement disk or Compute creation exists, allowing the
  required owner refresh to precede idempotent inhibition replay. An exact
  current owner may also replay controller-owned local dataplane preparation,
  route reconciliation, and forwarding restoration while that replacement
  inhibition is present, but only with current ownership, readiness,
  generation, and unlocked-writer proof; ownership-changing effects remain
  inhibited. The live-peer capability advances canonically to v4 and the
  owner-refresh checkpoint to v5 so an interrupted older
  inhibition can admit only its exact owner/route/controller cycle, journal the
  new approved owner refresh, and install the fixed agent before cloud creation.
  Replacement activation now preserves the retained owner's exact acknowledged
  enabled standby auto-healing transaction instead of leaving the fresh member
  on an independent default transaction. The v4 agent admits only a
  default-initialized replacement under a present exact apply lock, the exact
  pending apply-owned mTLS transaction, and separately matching rotation
  inhibition, adopts before VM-HA control services start, and re-proves durable
  agreement before releasing replacement inhibition. Exact package preparation
  now installs service assets from the approval-bound wheel, and staged activation
  reuses that descriptor without rebuilding or uploading different bytes. A completed replacement
  with this historical split is classified as apply-owned convergence so bare
  `vm-ha` can install the fixed artifact and repair it through the same fenced
  activation path; other policy conflicts remain blocked.
  Plain `apply` performs no
  unapproved replacement and directs the operator to `vm-ha` without claiming
  that an approval digest was already displayed. SSH identity prerequisites
  remain distinct from authentication failures.
- Hardened internal VM-HA restoration authority by preserving the existing v1
  promotion-receipt digest exactly while recomputing and reproving the durable
  receipt/authorization binding at commit and on every later authorization
  load. Tampered, missing, malformed, unreadable, or foreign receipt evidence
  now fails closed; apply-owner adoption remains excluded. Every production
  `VMManager` owner now closes its reused Nebius SDK deterministically without
  leaking cleanup details or masking command failures. Local, pull-request,
  and release Ruff checks now share the canonical `src tests misc` roots.
- Consolidated explicit managed-mTLS rotation under the canonical
  `vm-ha --rotate-mtls` action. The pre-adoption `set-vm-ha-mtls` command is
  removed without an alias, and candidate conversion, force, standby-policy,
  explicit-region, and JSON modes fail before rotation inspection or effects.
  Dry-run, exact digest approval, passive-first inhibition, human output,
  progress, capability admission, and resumable recovery remain unchanged.
- Fixed long-running `apply` authentication so an explicitly supplied IAM token
  is bound directly to every Nebius SDK client, while tokenless apply uses an
  SDK-native bearer backed by bounded, non-interactive current-profile CLI
  token acquisition. The bearer ignores ambient IAM tokens and performs at
  most one forced refresh after an unauthenticated response. Change analysis,
  provisioning, and strict VM-HA postcondition observations now reuse one SDK;
  typed authentication failures remain fail-closed, redacted, and distinct
  from member absence.
- Fixed `vm-ha --standby-auto-healing enabled|disabled` after completed
  standby recovery so cleanup is an internal prerequisite for either requested
  state instead of requiring an enable-first operator loop. The CLI validates
  and clears every present exact member-local completed recovery backed by
  either fresh two-member agreement or both committed records' durable
  acknowledgement, rereads the unchanged policy, and then reports the
  same-state result or completes the opposite-state transaction. Each clear is
  compare-and-clear bound to its complete recovery-record digest, and a
  combined plan binds the pre-cleanup authority, exact recovery digest set, and
  ordered effects before any mutation. Missing or conflicting proof remains
  blocked, and a stale approval cannot clear a newer recovery observation.
  After an exact terminal two-member transaction, the CLI now waits read-only
  for the public peer-heartbeat projection only while its sole divergence is
  the stale policy-invalid classification under the exact owner and frozen
  cloud observation digest; it validates that authority before accepting even
  a terminal sample and never replays the policy write.
  Human output now distinguishes already enabled, already disabled, enabled
  successfully, and disabled successfully while JSON v1 retains its bounded
  action codes. The canonical private mutation request remains v3 and the
  installed capability is v4, while persisted policy/status formats remain unchanged; older
  agents fail the capability preflight with no mixed-version fallback.
- Added the committed standby auto-healing state to the VM-HA `status` footer
  after Identity. Disabled maintenance now prints the exact shell-quoted
  config-bound `vm-ha --standby-auto-healing enabled` command on a standalone
  non-ellipsizing line, so it remains copy/pasteable at narrow terminal widths;
  enabled keeps Action at `none`, while transitional, blocked, and unknown
  evidence remains conservative. Other config-bearing actions are shell-parsed
  before replacing the complete path token with `<file>`, including paths with
  spaces or apostrophes. In disabled maintenance, the second-column
  `maintenance` and `disabled` values now render red while verified identity,
  details, and Action retain their existing presentation.
- Fixed planned VM-HA failover/failback falsely reporting a safe operation
  failure when a terminal SSH, agent-status, or cloud read was transiently
  unavailable or when the exact current-request controller entered its safe
  ownership-reproof sequence. Read-only terminal observations now follow only
  that closed lineage within fixed independent 300-second preparation,
  600-second cutover, and 300-second restoration budgets; progress cannot reset
  or extend a deadline. Persistent loss or reproof exits nonzero as an
  unverified cutover outcome or unverified standby restoration, preserves
  stable JSON request output, and directs the operator to `status` before
  retrying. Permanent, malformed, foreign, and contradictory evidence remains
  immediately fail-closed.
- Fixed interactive `vm-ha` progress so nested Rich spinner transitions no
  longer restore raw apply/provider output to the terminal. Routine analysis,
  VM-manager, SSH-push, per-member readiness, and package-preparation chatter
  stays behind the façade capture; both-member config-push readiness and exact
  package preparation each finish as one concise green row, and the approved
  transaction uses completed-state wording.
- Fixed `vm-ha` exact active-non-owner status so it observes the existing
  controller-owned forwarding fence instead of stopping at ambiguous state.
  The CLI recognizes only the exact `blocked:disable-active` operation targeting
  that reporting non-owner, gains no mutation authority, and stops with
  controller-journal guidance if bounded observation makes no progress.
- Changed `vm-ha` apply-plan review to publish one digest-bound typed impact that
  separates destructive changes from possible VPN traffic interruption. Plans
  explicitly classified as non-destructive and traffic-neutral now execute
  through the existing lock/replan/verification path without a second prompt;
  risky or unknown plans retain exact approval and default-No interaction. The
  artifact standby recovery warning now says VPN traffic may be briefly
  interrupted while the serving owner is upgraded and that no gateway VM or
  disk is deleted.
- Fixed `vm-ha` planning with a missing, ambiguous, stale, invalid, or changing
  agent wheel so the command reports a typed external prerequisite and exact
  rebuild or selection action instead of the generic convergence failure and
  misleading gateway-journal guidance. Approval now verifies wheel metadata,
  `RECORD`, the real capability entry point, file identity, and digest;
  same-byte path replacement is rejected. Artifact validation remains
  read-only, redacted, and fail-closed before any lock or remote effect. A later
  artifact change reports that convergence effects may have started and directs
  the next idempotent run to resume from durable checkpoints.
- Fixed planned VM-HA failover/failback cutovers immediately reporting standby
  restoration failure after a long transfer refreshed the latest agreement
  certificate. The latest agreement is now admission-only after arming; rearm,
  status, and retry share the durable authorization-to-policy predicate, and
  only automatic failover may use the exact prepared-disable predecessor race.
  Fixed runtimes advertise `vm-ha-standby-restoration-v2`, v1-only artifacts
  are rejected before approval, the persisted authorization v1 format remains
  readable, and public rearm status uses closed reason codes instead of raw
  exception text, including maintenance-policy recovery admission failures.
- Fixed the recurring post-failover/failback deadlock where the serving owner
  was reachable but lacked the current standby-restoration capability while
  the alias-free non-owner was safely Stopped. `vm-ha` now admits only that
  exact ACTIVE topology, binds one existing capability-bearing wheel SHA-256
  into its public approval, upgrades and reproves the owner, delegates the
  start to the existing owner-side rearm writer, installs the identical
  artifact on the restored standby, and resumes canonical non-owner-first
  apply. The exact legacy `peer-policy-unavailable` blocked projection is now
  recognized as the causal stopped-peer condition, while disabled, changing,
  invalid, or otherwise blocked policy remains fail-closed. Route, allocation,
  forwarding, identity, or writer drift and ordinary all-member SSH validation
  are unchanged.
- Hardened VM-HA agent preparation so apply no longer accepts package-version
  parity as artifact proof. Planning selects exactly one existing local wheel
  without building or deleting files, binds its SHA-256 into the v2 approval
  domain and additive `approval.artifact_sha256` field, and re-hashes the same
  file before execution. The wheel is uploaded through an isolated mode-0700
  staging directory under its valid wheel basename, verified before install,
  and accepted only when a fresh installed-agent capability probe advertises
  standby restoration. VM-HA convergence refuses installed-package fallback
  and ambiguous wheel sets.
- Fixed same-version VM-HA runtime skew so `vm-ha` cannot report healthy and
  planned failover/failback cannot publish a request unless the installed
  controllers advertise the transfer-bound standby-restoration capability.
  Missing support is now apply-owned drift, and private standby-policy recovery
  requires the same fixed capability before mutation.
- Fixed interrupted explicit standby-policy recovery after the non-owner
  Compute already reached `Running`. `vm-ha --standby-auto-healing enabled`
  now accepts only the exact current-owner `completed` recovery checkpoint,
  issues a fresh current-authority approval, and binds the target's current
  Compute state into that plan. A Running or transitional target resumes
  without another start; a currently Stopped target plans a new owner-side
  re-arm and one exact start request before readiness, peer initialization,
  agreement, and cleanup. An owner rearm writer-lock race now resumes only
  after the exact approval-bound recovery advances instead of falsely failing
  while the accepted start completes asynchronously. A bare `vm-ha` remains
  observation-only but now classifies invalid policy as maintenance policy and
  points to the explicit enabled recovery instead of generic journal
  inspection.
- Fixed post-failover and post-failback redundancy restoration so an enabled
  cluster no longer depends on the deliberately stopped peer keeping a
  30-second policy heartbeat fresh. The controller now captures a strict
  replay-bound two-member agreement, arms it before the first transfer effect,
  commits it only after the exact promotion receipt, and lets the sole rearm
  writer consume it for the same former owner. Rearm uses one idempotent start
  identity across five bounded retryable or ambiguous submissions, blocks
  permanent or exhausted work, waits independently for fresh standby evidence,
  and logs transitions without resource identities. Planned transfer timeouts
  leave durable background recovery running; `vm-ha` remains the role-neutral
  conversion and exact blocked-recovery facade under committed enabled policy.
  Planned promotion commit now fails closed and retains transfer lineage when
  the exact restoration authorization is unavailable; active restoration is
  reported as transitioning, while a safely blocked restoration is degraded
  instead of being mislabeled as an invalid standby policy.
- Fixed an owner-side rearm race during approved standby-policy recovery. If
  the rearm service consumes the exact single-use recovery under its writer
  lock before the CLI submits the explicit retry, a transient busy/ambiguous
  retry result no longer causes a false terminal failure. The CLI continues
  only with both the exact consumed/completed recovery and matching current-
  owner `starting`/`running` evidence; every missing, stale, blocked, or foreign
  state remains fail-closed.
- Fixed automatic VM-HA transfer effects repeatedly failing before the Nebius
  Compute mutation because colon-delimited controller operation IDs were sent
  unchanged as provider idempotency keys. The sole SDK metadata boundary now
  deterministically encodes provider-invalid IDs as lowercase SHA-256 while
  preserving the original checkpoint and accepted-operation identity; already
  valid keys and absent-key behavior remain unchanged. Effect-started automatic
  transfer lineage now also resumes after apply fencing retires an unsafe
  pending action, instead of being stranded by healthy-peer suppression. When
  the requested owner is already correct but its standby is safely Stopped,
  planned transfer preparation now uses the canonical owner-side rearm path and
  returns a request-free no-op only after exact redundancy reproof. Planned
  transfer preparation and remote-agent failures now cross one closed CLI
  boundary that suppresses raw tracebacks and provider output while retaining
  bounded operator guidance in both text and JSON invocations.
- Fixed repeated planned VM-HA failover/failback after the first durable effect.
  An exact same-direction invocation now reuses the unchanged initiating request
  and resumes observation with its original progress fingerprint instead of
  failing as a second request. Request publication and first-effect lineage are
  serialized by the existing writer lock, and transfer dispatch revalidates the
  typed intent under that lock before accepting an effect; missing, later,
  conflicting, automatic, stale, malformed, or foreign evidence remains
  fail-closed.
- Simplified a healthy human-readable `vm-ha` result to one terminal headline
  after progress. The stable JSON result still carries the complete passive
  verification scope and explicit `failover_tested=false` evidence.
- Fixed direct VM-HA `apply` timeout handling. Known Nebius SDK retry
  announcements no longer print tracebacks, and a final typed
  `DEADLINE_EXCEEDED` now exits with concise `vm-ha` recovery guidance without
  changing the bounded SDK retry policy or ordinary non-HA behavior.
- Fixed VM-HA activation when an upgraded agent encounters an authenticated
  advisory heartbeat written in the previous private schema. Apply now records
  an exact generation-bound cache reset while retaining the monotonic replay
  boundary; the new controller consumes it after the old process stops, so a
  late cache rewrite cannot survive activation. Initialization returns a local
  commit receipt without consulting unrelated peer or recovery projections.
  Future failures identify the closed step and cause class without exposing
  remote output.
- Fixed VM-HA `apply` so it installs and verifies the current agent package on
  both members non-owner-first before the first exact-generation lock invokes
  current private admission checks. Existing gateways running the previous
  package no longer reject a newly added lock-time check before `apply` reaches
  package deployment; a partial package failure attempts no new lock,
  preserves any pre-existing locks, and activates neither staged configuration.
- Added a durable two-member standby auto-healing maintenance policy to
  `vm-ha` through `--standby-auto-healing enabled|disabled`. New activations
  explicitly default to enabled; changes use a private v2 predecessor-bound
  transaction with a deterministic coordinator, a separately authority-bound
  approval digest, default-No confirmation, accepted-start quiescence, and
  terminal peer-agreement checks. Re-enabling can recover a maintenance-stopped
  standby from exact owner evidence: the owner persists a single-use intent and
  the existing rearm service remains the sole Compute-start writer before the
  normal two-member transaction completes. An explicit approved `enabled`
  recovery can also break the apply/rearm deadlock when the exact owner policy
  record is missing: it binds idempotent owner and restored-peer initialization
  to the same predicted generation transaction and never treats missing state
  as ordinary start authority. Apply, removal, and mTLS admission
  now mutually exclude prepared policy or active recovery state under the same
  node-local writer lock. Disabled policy leaves the rearm service enabled but
  inhibits ordinary standby starts and planned failover/failback while
  preserving automatic owner-loss failover and all promotion fencing. Apply and
  replacement preserve/rebind the committed choice, removal clears it after the
  existing barrier, and missing, stale, corrupt, transitioning, or split
  evidence fails closed. Ordinary status now
  reports peer-acknowledged disablement as yellow `MAINTENANCE`, removes the
  public `Rearm` summary row, and keeps `Redundancy`, `Identity`, and `Action`.
  The tunnel table now uses mode-neutral `Uptime`: BGP session uptime with IPsec
  fallback for BGP, and IPsec SA uptime for Static. Probe failures render `-`
  there, and unavailable or malformed BGP uptime evidence preserves the IPsec
  fallback instead of inventing zero uptime. After later exact VM-HA readiness
  evidence, status may rerun only the identical failed tunnel or service probe
  once. Tunnel errors clear only with recognizable established-SA evidence;
  empty, no-SA, connecting-only, or malformed output remains visible. A
  terminal enabled invocation also retries exact idempotent cleanup when an
  earlier completed recovery clear was interrupted.
- Fixed region precedence so `vm-ha`, `apply`, `prep-network`, `status`, and
  `destroy` carry one effective Nebius region through plan construction, SDK
  clients, service-account context, and VM-HA SSH trust. An explicit
  `--region` now replaces both retained in-memory region fields before
  placeholder expansion, while generated VM-HA candidates persist both keys
  consistently. An unresolved selected authority fails before authentication
  or cloud effects instead of falling through to a lower-priority region. All
  five command help pages now publish the same precedence contract, and
  `prep-network` uses the shared raw-override loader path.
- Fixed `restart-tunnel` on VM-HA-enabled gateways so it exits `1` with one
  topology-first stderr line before the loading banner, authentication, SSH,
  or subprocess execution, without a usage panel, generic error wrapper, or
  traceback. The guidance now states that recovery is controller-owned, points
  to `status` for health inspection, and limits `apply` to configuration
  convergence. Ordinary Static IPsec restart and ordinary BGP IPsec-plus-neighbor
  reset behavior remain unchanged. Its help now explicitly identifies regular
  gateways (non-HA) as the only supported topology.
- Fixed unsupported `failover tunnel` and `failback tunnel` invocations so
  ordinary Static routing and VM-HA-enabled gateways receive concise,
  action-specific stderr guidance and exit `1` before authentication or SSH,
  without a loading banner, generic error wrapper, usage panel, or traceback.
  Every VM-HA routing mode now gets the same topology-first message, which
  explains that the matching VM command transfers VM ownership only and does
  not select a tunnel. Command help and operator documentation now identify
  tunnel transfer as an ordinary-BGP-only path override and accurately describe
  its owning-instance FRR behavior. The help now labels both transfer commands
  as available only on regular gateways (non-HA) using BGP, not Static routing.
- Fixed `vm-ha --rotate-mtls` so its failover/rearm inhibition is no longer
  interpreted as a dataplane-fencing apply lock. A healthy owner now remains
  forwarding while the passive-first overlap-trust rotation runs; independent
  cloud-ownership safety fencing is unchanged. Transfer dispatch and rotation
  inhibition now share a node-local lock with an exact controller-quiescence
  barrier, and pre-prepare drift releases inhibition instead of stranding
  recovery. The command now starts with a concise availability note, shows a
  compact plan and exact approval digest without raw preview/result JSON, and
  uses an interactive spinner with one terminal result row. It also verifies
  both installed agents and their running controller processes advertise the
  split rotation-quiescence contract before plan approval or inhibition, so a
  stale or restart-skewed member fails safely with `apply` guidance instead of
  timing out after confirmation.
- Added one idempotent top-level `vm-ha` command for ordinary-to-HA conversion,
  journaled apply/resume, bounded controller observation, apply-owned drift
  convergence, and exact non-owner rearm. It preserves ordinary input, emits a
  stable passive-current-state JSON result, binds material effects to an exact
  approval digest, reclassifies under the existing writer lock, and requires
  two agreeing fresh terminal observations. It never drills failover, resets
  controller repair, invents trust or credentials, or recreates an absent
  active-lifecycle member by name; unsafe and external cases return sanitized
  owner-specific guidance. It is now the only public VM-HA conversion,
  convergence, verification, and standby-rearm entry point. Public region
  selection uses `--region` on `vm-ha`, `apply`, `prep-network`, `status`, and
  `destroy`, with CLI override precedence over `gateway_group.region` and
  top-level `region_id`. Alignment hardening prevents
  `--force` from reaching a conflicting candidate, binds the approved plan
  inside the executing apply engine, suppresses raw apply/provider diagnostics,
  and routes a verified `REMOVED` tombstone back through canonical provisioning.
  Interactive text runs now review an approvable exact plan and continue in the
  same invocation only after a default-No confirmation; refusal performs no
  mutation and remains exit `3`. Sanitized stderr progress now reports each
  authoritative healing, VM-HA control-service, wait, and verification phase
  while JSON stdout remains one stable result document. Each phase now leaves
  one terminal row with a green success check or a fully red failure x, and
  bounded waits use an animated interactive spinner instead of a literal
  ellipsis and rewrite that active row instead of appending poll lines. Known
  retriable Nebius SDK diagnostics no longer print raw tracebacks through the
  spinner, while final provider exceptions retain sanitized failure handling.
  Required activation failures preserve a bounded sanitized command class and
  exit result, and apply-owned activation exits now retain convergence and
  service-journal guidance instead of being misclassified as authentication
  failures.
- Fixed a VM-HA activation crash loop caused by a manual transfer request being
  accepted after a contradictory durable transfer lineage had already started.
  New requests now fail before publication, while upgraded controllers retire
  only an exact conflict proven to be later than the authoritative lineage.
  The guard, controller, and health monitor now preserve their shared systemd
  runtime directory across stop/restart cycles, preventing a controller crash
  from transiently making the guard's routing lock read-only.
- Fixed planned VM-HA failover/failback observation when the guarded
  controller transiently reports `controller-step-failed`. The CLI now stays
  attached through an exact current-request `effect-failed` transition only
  while the controller checkpoint still owns that same pending operation,
  reports that forwarding remains fenced, and retains the existing bounded
  deadline and terminal ownership/redundancy proof. Missing, foreign,
  malformed, or mismatched retry evidence still fails immediately with safe
  status and controller-journal guidance; no CLI-side effect retry, service
  restart, rearm, schema, or fencing change was added.
- Bound VM-HA credential-file authentication to one exact Nebius service
  account, authorized key, project, source, and digest. Apply authenticates the
  managed source with a bounded forced-renewal `whoami()` before lifecycle,
  cloud, SSH, or gateway mutation and rejects staging drift. The
  immutable runtime, operation, staging, and lifecycle records carry only
  non-secret IDs and digests. Each controller startup now revalidates its
  canonical installed bundle and exact current-start identity before cloud or
  forwarding effects. Only a recent preflight from the same systemd invocation
  is consumable once; a restart, direct controller launch, or rearm process
  proves identity online again. Installed payload reads are bounded and every
  startup invalidates stale verified status before parsing. Public status
  exposes only a closed safe state and reason. Active lifecycle identity drift
  blocks without compatibility adoption, account rebinding, or key rotation.
  Interrupted `ACTIVATING` retries now compare lifecycle credential bindings
  with the already authenticated apply credentials instead of a cloud-only
  observation, so exact identities resume while credential drift still blocks.
- Fixed automatic VM-HA takeover after the configured-passive member had
  become owner and was then stopped externally. Either exact non-owner can now
  detect current-owner loss and enter the same suspicion, stopped-owner,
  detach, attach, ownership-confirmation, route, forwarding, and standby-rearm
  workflow. Planned failover/failback remain role-bound, and no automatic
  transfer occurs while the current owner is healthy. The last accepted mTLS
  heartbeat is now checksum-persisted behind its anti-replay boundary and
  reloaded after controller restart as stale parity evidence only. Its peer
  boot and sequence must exactly match the replay high-water mark, so a crash
  between replay and heartbeat persistence discards the older cache and blocks
  transfer until fresh exact parity arrives rather than restart-looping or
  reusing stale evidence.
- Fixed false planned VM-HA standby-restoration timeouts by giving preparation,
  request-to-cutover observation, and post-cutover restoration independent
  bounded deadlines while preserving one total elapsed clock in user output.
  Failover and failback now show rearm/Compute restoration phases even when
  detailed cutover progress is unavailable, and partial-completion errors
  report cutover, restoration, and total time separately. Rearm publishes its
  durable `starting` checkpoint/status before the synchronous Compute start.
  The controller now owns its runtime directory through shutdown and enters
  its guarded readiness boundary before `network-online.target`, removing the
  observed FRR/network ordering cycle without weakening
  guard/controller-before-FRR safety.
- Added exact, presentation-only phase reporting for planned VM-HA failover and
  failback. The agent binds a bounded private transition history to the current
  request, transfer lineage, checkpoint, boot, generation, allocation, and
  controller postconditions; the CLI reports new stop, shared-IP, ownership,
  VPN, route, forwarding, and rearm phases immediately and repeats them about
  every five seconds. Missing or invalid progress retains the existing coarse
  fallback, including after fine evidence disappears, and cannot affect
  transfer authority. Added a standalone exact-identity strict-SSH one-way 5 Hz
  diagnostic probe under `misc/`; it rejects remote and ping runtime errors
  without partial evidence and never drives or gates the product transfer.
- Changed planned VM-HA `failover vm` and `failback vm` to default to concise
  human-readable text without raw request or no-op JSON. Automation can opt in
  to the former sorted records with `--output-format json`; invalid formats are
  rejected before authentication or effects. Progress now uses the exact
  `elapsed, cutting over...` and `elapsed, restoring standby...` forms without
  parentheses or a trailing single period.
- Fixed planned VM-HA transfer completion so `failover vm` and `failback vm`
  report safe cutover and standby restoration as separate timed phases and
  print terminal success only after both gateway Computes are `Running`, the
  former owner is alias-free and freshly standby-ready, and final cloud and
  agent observations remain stable. A blocked or timed-out rearm now reports
  safe partial completion and the supported `vm-ha` retry instead of a
  false full success. Definite Compute-start failure remains durably blocked
  without terminating the long-running owner reconciler, same-owner no-op
  requires healthy redundancy, and `status` now shows an identity-free
  `Redundancy`/`Rearm`/`Action` summary below its unchanged member table.
- Fixed planned VM-HA failback and failover when the non-owner retained an
  otherwise exact promotion receipt from the prior deployed generation. The
  controller now retires that receipt only at the first explicit, still-exact
  pre-transfer effect, then records current-generation lineage before stopping
  the former owner; foreign, malformed, already-started, or topology-drifting
  cases remain fail-closed. Both VM transfer commands now stay attached until
  a committed promotion, exact active status, route receipt, stopped former
  owner, and independently stable cloud ownership are proven. Explicit
  `--output-format json` preserves the request record on stdout, while
  role-specific start, five-second elapsed progress, failure, and terminal
  success messages are emitted on stderr.
- Fixed VM-HA apply when the retained current owner is still active as its
  exact-generation apply lock arrives. The lock now fences that owner before
  passive materialization, retires an obsolete autonomous local-repair attempt,
  and keeps both members locked until the existing owner-first release path.
- Fixed `status` so every configured tunnel remains visible when StrongSwan
  reports no active IPsec security association for all or part of a gateway's
  configuration. The primary table preserves configured tunnel name, role,
  gateway, and peer while reporting unobserved tunnels as `IPsec` `NONE`;
  unexpected runtime-only tunnels are appended, and only a VM with no
  configured or runtime tunnels receives `No configured tunnels`.
- Added static-only explicit VM-HA convergence to `add-routes-local` without
  creating a second route writer. The command now verifies the controller
  capability on both installed members, requires the exact quiescent ACTIVE
  generation and stable owner/allocation authority, and waits up to 120 seconds
  for the autonomous controller's route receipt plus an independent VPC
  postcondition. It never submits a repair request or mutates controller-owned
  routes directly. Exact-prefix foreign occupancy blocks safely, while unequal
  overlaps such as a foreign `/32` inside a managed `/24` remain untouched.
- Fixed static VM-HA route convergence when controller-owned routes retain
  unrelated customer labels. The observer now requires the complete exact
  authority-label subset without rejecting metadata the controller preserves.
- Fixed static `list-routes-remote` status for IPv4 host routes. Linux may
  render an installed `/32` destination without its prefix length; route
  observation now compares canonical IPv4 networks, so that equivalent kernel
  entry is reported as installed while genuinely absent prefixes remain
  missing.
- Added `apply --prepare-vm-ha-peer-rotation` as the successful,
  provider-neutral checkpoint for IPsec peer credential changes across static,
  BGP, and schema-valid mixed VM-HA configurations. It stages and activates the
  exact generation while leaving both members passively fenced and locked;
  peer mutation remains operator or provider-adapter owned, and ordinary apply
  remains the only path that unlocks the owner, proves mode-appropriate route
  readiness, and enables forwarding. The narrower GCP Classic adapter still
  requires a complete retained graph and exact two-member GCP/static topology,
  revalidates resource identity after confirmation, and removes every planned
  route after a post-mutation failure unless absence is proven.
- Fixed static-only VM-HA activation when FRR reports its valid no-peer BGP
  summary as an empty object. The controller now treats only that exact shape
  as an empty configured-peer set, so cold owner tunnel preparation can run
  instead of timing out behind an all-false readiness projection; other
  incomplete FRR summary shapes remain fail-closed.
- Fixed VM-HA apply so an unset `VPNGW_SSH_HOST_KEYS_DIR` uses the owner-only
  `~/.ssh/nebius-vpngw/host-keys/<gateway-group>/<scope-sha256>` hierarchy. The
  complete deployment scope isolates same-named gateways. Actual apply creates
  that default and atomically prepares missing Ed25519 server keys only for
  genuinely fresh members; dry-run stays write-free, filesystem errors stay
  sanitized, explicit directories stay operator-owned, and retained members
  still require original matching trust evidence before cloud mutation.
  Recreation against a managed receipt or explicit exact pin now fails without
  persisting a mismatching replacement key. `create-config` remains a
  secret-free YAML authoring command; topology-aware key preparation stays in
  `apply`. Existing deployments can now migrate an exact retained-member
  Ed25519 pin from a safely snapshotted, read-only `~/.ssh/known_hosts` entry;
  when public trust or original private material is still missing, apply can
  recover the exact current or lifecycle-bound legacy product identity from
  persisted Compute cloud-init before strict SSH verification and managed
  publication. Static and BGP modes share the same recovery path.
- Updated `list-routes-local` to label explicit VM-HA gateway headings from
  the same stable owner snapshot used by its advertisement audit. The current
  owner is shown as green `ACTIVE`, the other member as `STANDBY`, and
  unavailable or changing authority as `UNKNOWN`; configured tunnel roles and
  non-VM-HA output remain unchanged.
- Added explicit, secret-safe GCP Classic tunnel credential rotation for the
  isolated static VM-HA fixture. The helper can read exactly two named PSKs
  from a private mode-`0600` VPNGW YAML, deletes only the planned static routes
  and tunnels when `--rotate-existing-tunnels` is supplied, recreates both
  tunnels before restoring routes, and retains ordinary create-only behavior
  plus all addresses, target gateways, and forwarding rules. Missing retained
  infrastructure and private-config topology drift now fail before mutation.
- Fixed the standalone PyInstaller binary entry point so the frozen CLI starts
  outside normal package execution. The integration build now runs the actual
  binary and inspects its archive for the route-maintenance service and timer.
- Fixed recurring VM-HA standby routing drift by making the five-minute route
  owner current-boot and role aware. Exact active authority retains the full
  reconciler; a forwarding-fenced passive member can remove only table 220 and
  the broad APIPA route, while stale or blocked authority cannot mutate routes.
  Passive readiness, heartbeat/status evidence, and read-only routing health
  now fail closed on rule-backed or route-only table 220 state and broad APIPA
  drift without changing public CLI, configuration, or persisted schemas.
  Exact token matching prevents unrelated priority-220 or table-2200 rules
  from being reported or removed, and status directs this drift to the periodic
  owner or supported apply path rather than the non-repairing rearm workflow.
  Linux systems where a successfully flushed table 220 no longer exists are
  now recognized through a fail-closed all-table JSON read instead of rejecting
  the clean state because a direct missing-table query returns nonzero.
- Added a per-deployment VM-HA SSH trust store under
  `~/.ssh/nebius-vpngw/<scope-sha256>/`. Actual `apply` can safely create,
  repair, or migrate its public-only exact member pins from retained host-key
  material or a validated explicit override; dry-run, status, routes,
  transfers, and mTLS operations remain read-only. Stable hostname aliases
  survive management-address changes, explicit overrides remain strict, and no
  path uses network-only enrollment or disabled verification. The general user
  file is now consulted only as a
  bounded read-only migration source and never becomes canonical or writable.
  SSH protects operator management, while mTLS protects
  member-to-member HA heartbeat and readiness evidence. Alignment also restored
  stable-identity binding for route and removal SSH, rejects conflicting or
  raced retained host-key evidence, and keeps the generated projection usable
  by older address-based releases.
- Migrated setuptools-scm tag matching to its supported nested configuration
  and made source-checkout version discovery reuse that canonical project
  configuration without writing version files. Local tests and builds no longer
  emit the deprecated `tag_regex` warning, while tag and version semantics stay
  unchanged.
- Fixed command ordering and applicability failures with Typer releases that
  vendor Click. The custom order hook now type-checks across the supported Typer
  range, and unsupported topology, mode, or flag combinations retain their
  sanitized nonzero CLI errors through Typer's public exception boundary.

- Aligned every public CLI leaf and option with ordinary versus explicit VM-HA
  topology and static versus BGP routing. `add-routes-local` now preserves
  direct VPC route management only for ordinary gateways; VM-HA BGP skips
  member-primary route mutation and repairs only exact advertisement drift,
  while VM-HA static directs reconciliation to `apply`. BGP workflows verify a
  read-only installed-agent capability contract before route mutation, route
  and repair failures exit nonzero without a completion banner, and static
  route discovery now includes enabled per-tunnel prefixes. Direct tunnel
  restart/failover/failback and destroy reject VM HA before external effects,
  tunnel failover/failback reject static mode, and the exact 18-leaf flag/alias
  and four-mode applicability matrix is regression-tested. Mixed ordinary
  static/BGP deployments now inspect FRR only on gateways that own BGP policy;
  VM-HA route operations freeze the same exact per-member SSH host pins as the
  lifecycle paths before authentication, and transport or unexpected SDK
  failures cross one sanitized typed route boundary.

- Fixed VM-HA BGP export safety by applying an explicit outbound allow-list or
  deny-all route-map to every enabled peer, verifying owner/passive Adj-RIB-Out
  parity before readiness, and completing passive firewall/routing hygiene
  before its materialization receipt. `list-routes-local` is now strictly
  read-only and reports owner-aware `MATCH`/`DRIFT`/`UNKNOWN`; explicit route
  mutators never reload on incomplete evidence or changing HA authority.
  Runtime parity now omits disabled tunnels while still inspecting disabled-only
  BGP configurations for stale live peers, detects unexpected live peers, and
  verifies route-only table 220 cleanup. Failed FRR activation is not persisted
  as applied, mixed-time audit evidence becomes `UNKNOWN`, and explicit repair
  reconciles only the installed config under an exact lock-held
  owner/allocation/generation plus target-member epoch tuple. It also rejects
  concurrent apply or mTLS writer inhibition and holds both the apply/rearm and
  mTLS writer locks through authority validation and rendering instead of
  remotely overwriting the deployed config.
- Fixed fresh VM-HA passive materialization so exact deny-all safety no longer
  depends on every BGP session already being established. The transition now
  requires the exact live peer set, an empty Adj-RIB-Out for each established
  peer, and exclusive running-config bindings to the deny-all route-map; audit
  and readiness remain unknown until all expected peers establish.
- Fixed current-owner restart materialization when a prior durable route
  receipt exists. Receipt revalidation now projects as unproven while the
  guarded local data plane is unavailable, allowing passive materialization to
  run before the receipt is revalidated; promotion remains blocked meanwhile.
- Fixed repeated passive transitions in the same boot and generation so the
  controller cannot accept an earlier materialization receipt while a requested
  agent reload is still changing FRR. The prior handoff is durably invalidated
  first, and controller evidence begins only after a newly written receipt and
  routing-lock handoff.
- Separated exact BGP export convergence from the short agent-materialization
  poll. Owner activation now retains its forwarding fence while allowing up to
  one bounded minute for FRR sessions and Adj-RIB-Out to converge after reload.
  The controller's resolved VM-HA projection now also retains
  `gateway.local_prefixes`, so its expected per-peer export manifest matches
  the full configuration used by the active renderer. Terminal mismatch
  diagnostics report aggregate peer and prefix counts without exposing their
  values.
- Fixed `list-routes-local` with current Nebius Python SDK releases by using
  public package-facade VPC clients instead of removed generated protobuf
  modules. Local route output now follows every allocation, subnet, network,
  and route page before rendering, while `list-routes-remote` retains its
  owning-gateway and connection-peer scoping.
- Simplified `status` presentation without weakening its evidence checks. The
  primary VPN table now omits the redundant `Traffic State` column and folds
  complete tunnel names instead of ellipsizing them; runtime differences stay
  visible in `Traffic Override`. Explicit VM HA now renders one four-column
  `Gateway`/`Role`/`mTLS`/`Ready` table, reports the authoritative owner as
  `active`, the non-owner as `standby`, and unproven ownership as `unknown`
  without appending configured role preference. It colors good states green and
  all other states red and keeps the aggregate classification in the table title.
- Fixed misleading `apply` progress around gateway-network and existing-VM
  discovery. Optional `gateway_group.network_id` discovery is now reported
  once during provisioning while VM-HA still performs every authoritative cloud
  reread, and existing instances are described as recreation candidates only when
  `--recreate-gw` is actually active.
- Fixed `add-routes-local` progress when live BGP advertisements already match
  the current YAML. The no-op path now reports only its completed audit and
  success instead of listing skipped repair work; proven drift reports two
  conditional repair phases for reconciliation and post-repair verification.
  BGP route discovery failures now abort before partial VPC route reconciliation
  and report sanitized errors instead of copying remote diagnostics.
- Fixed interrupted VM-HA activation when an obsolete local route-ledger
  identity is already absent from cloud state. Apply now retires it only after
  target revalidation and two identical route reads, scopes status authority to
  exact current-cluster labels, and projects live writer/guard state instead of
  a stale successful snapshot. Current Nebius subnet status pools are also read
  without triggering the deprecated CIDR accessor.
- Fixed VM-HA agent package preparation so the uploaded artifact retains its
  valid wheel filename during remote installation. This lets `apply` install
  the managed-mTLS helper and reconcile its cryptography/CFFI dependencies
  before beginning identity enrollment.
- Fixed fenced VM-HA generation changes on a previously promoted owner. Apply
  now writes one exact lock-bound owner-adoption declaration, the agent combines
  it with independent cloud ownership before establishing continuity, and the
  normal route and forwarding gates replace it with generation-current terminal
  evidence. Malformed, foreign, mismatched, or orphaned declarations still
  block safely.
- Replaced operator-supplied VM-HA PKI with product-managed direct-pinned
  mutual TLS. Each VM generates and retains its own fixed-profile self-signed
  ECDSA identity; only public leaf receipts cross exact-pinned SSH. `apply`
  bootstraps identities, leaves healthy pairs unchanged, and replaces only a
  fenced member. The new digest-approved `vm-ha --rotate-mtls` action performs a
  resumable passive-first two-member rotation with controller/rearm inhibition,
  three fresh authenticated observations, exact pruning, and no scheduled
  renewal. Status reports only each member's closed mTLS health state; epochs,
  fingerprints, phase, and inhibition stay internal. VM-HA YAML contains no
  runtime credential path; apply owns internal node-local credential
  references, and the unreleased operator-PKI shape and heartbeat-v1 protocol
  have no compatibility reader.
- Fixed explicit VM-HA status SSH diagnostics and route-authority reporting.
  Every probe now uses the configured management user and key, exact host pins
  are isolated per member without permissive fallback, and missing trust is
  reported as `ssh-trust-unavailable` instead of making both controllers
  unknown. Cloud authority now distinguishes route-target, managed-record,
  prefix-set, and shared-allocation next-hop drift and emits a reason-specific
  apply repair action. `vm-ha` delegates exact standby restoration to the
  internal start-only writer and does not grant that writer trust, generation,
  local-route, cloud-route, allocation, firewall, or forwarding authority.
- Removed the unpublished `vm-ha-recover` command and its duplicate private
  agent flag. Ordinary `status` now owns the single VM-HA status surface with
  one concise, identity-safe four-column table and two configured-member rows.
  It uses cloud and lifecycle authority for ownership, validates controller
  evidence, and reports healthy, degraded, transitioning, blocked, or unknown
  state without mutation or inline recovery guidance.
  Read-only status now accepts unresolved tunnel-PSK environment references
  while retaining strict operational placeholders, agent-generation
  self-consistency, two-member parity, and non-secret policy-digest checks.
- Consolidated manual traffic-transfer commands under resource subcommands.
  Migrate `vm-ha-failover` to `failover vm`, `vm-ha-failback` to
  `failback vm`, flat `failover [TUNNEL_NAME]` to
  `failover tunnel [TUNNEL_NAME]`, and flat `failback [TUNNEL_NAME]` to
  `failback tunnel [TUNNEL_NAME]`. The four old paths are removed without
  aliases; leaf options, VM-HA fencing and ownership checks, request schemas,
  and tunnel behavior are unchanged. Bare `failover` and `failback` show group
  help and perform no operation.
- Aligned the public CLI help with the configuration and VM-HA workflows. The
  top-level help now includes a safe quick-start sequence, and every public
  command help page includes practical, command-specific invocation examples
  without changing flags, behavior, confirmations, approvals, or fencing.
- Added `vm-ha` for guided conversion of a supported ordinary
  single-VM config into a new explicit two-member VM-HA candidate. The
  two-phase wizard preserves the raw source and environment references,
  derives passive-member tunnel counterparts, can separately reserve only the
  deterministic passive public allocation, stops without a draft while peer
  setup is pending, and conditionally publishes a complete mode-`0600`
  credential-path-free candidate without clobbering a racing writer. The later
  exactly approved `apply` owns managed runtime credential enrollment before
  continuing through the migration workflow.
  Existing `create-config`, `prep-network`, `validate-config`, and `apply`
  contracts remain available and unchanged.
- Added a schema-backed `create-config` wizard for interactive terminals, with
  typed guidance, help/back/quit controls, BGP and static connection flows,
  explicit default-disabled VM-HA setup, PSK environment references, redacted
  review, and atomic publication. Non-TTY and `--no-interactive` invocations
  retain the existing commented template, while `--interactive` forces the
  wizard. The wizard can optionally run network preparation after a separate
  default-No cloud-effect confirmation; the supported `prep-network` command
  remains available and both entry points share one internal preparation path.
- Fixed Classic GCP VM-HA creation ordering so both paths finish their missing
  address, gateway, forwarding-rule, and tunnel mutations before any new
  static route is created. Compatible partial graphs remain idempotently
  resumable and no retained resource is deleted.
- Hardened VM-HA pre-mutation validation: stale persisted status from an older
  boot is projected as ownerless and blocked even after the current guard is
  installed; managed TLS identities must pass fixed-profile validity, exact
  DNS and URI identity, local key match, direct peer-pin verification, and
  bidirectional authenticated transport checks; rendered secret-bearing VM-HA
  configuration is uploaded only through a private, cleaned staging directory;
  and the Classic GCP helper rejects non-Premium addresses or forwarding rules
  and non-`EXTERNAL` forwarding schemes before resolving secrets or creating
  resources.
- Added isolated static-only VM HA for GCP Classic VPN. Static passive members
  remain Compute-warm but terminate their IKE SAs; after the former owner is
  `Stopped` and the shared allocation is confirmed on the candidate, one
  checkpointed preparation action establishes that candidate's tunnel while
  forwarding remains fenced. A dedicated `--classic-vm-ha-peer` helper creates
  two one-to-one Classic gateways, tunnels, and explicit-priority routes with
  secret-safe PSK transport and no Cloud Router or BGP resources. Existing BGP
  warm-standby behavior and mixed-connection configuration validity are
  unchanged. Apply and restart recovery also let an already-authoritative
  configured active owner prepare its static tunnel and routes without
  manufacturing automatic-transfer lineage. A promoted configured-passive
  owner may perform the same owner-only recovery only when an exact terminal
  promotion receipt matches the unchanged cloud ownership epoch.
- Fixed live static-HA recovery and status defects. VM-HA status now compares
  persisted guard evidence with the machine's actual current boot before
  reporting owner, forwarding, or standby readiness; interrupted apply no
  longer reinterprets an already-committed promoted owner as a new automatic
  transfer; and CLI status reports a tunnel-cold standby with zero IKE SAs as
  `No tunnels` instead of a parse failure.
- Completed isolated non-production Classic static-HA acceptance for initial
  steady state, planned failover/rearm, planned failback/rearm, and automatic
  failover/rearm. Each transfer observed the former Compute owner `Stopped`,
  exact candidate ownership before tunnel preparation, route reconciliation
  before forwarding, one owner-aligned Classic IKE SA, complete retained GCP
  resources, successful workload request/reply traffic, and an unchanged
  healthy four-tunnel BGP-only fixture. All review resources remain deployed.
- Fixed two VM-HA activation and planned-transfer admission defects found during
  live parity validation. Restoring forwarding on an already-authoritative
  current owner no longer fabricates transfer lineage, and a fresh passive
  standby is now validated by its fenced current-boot guard without requiring
  an active-owner controller-ready boot marker. Also documented that the
  supported role-neutral GCP warm-standby topology requires owner-following BGP
  routes; configured-role Classic VPN static-route priorities cannot safely be
  mixed into that topology.
- Fixed the remaining VM-HA warm-standby correctness gaps. Repeating a planned
  transfer to the exact healthy owner is now a request-free no-op; retained
  Compute-start journals are compare-cleared only after exact terminal success;
  explicit retry authority is consumed before one logical attempt; apply,
  removal, retry submission, and rearm share one stable-inode `fcntl` writer
  lock that survives deactivation; planned preparation bounds Compute polling,
  pinned SSH, and every fresh standby read under one deadline; and unchanged
  heartbeat v1 semantics now allow exact passive readiness to satisfy the
  owner-side redundancy panel. Planned status samples now enforce the complete
  runtime binding, and HA removal gates and drains both members before either
  deactivation, checkpoints that barrier, and resumes partial teardown without
  contacting an already-deactivated agent. Ambiguous evidence still fails
  closed.
- Refactored VM-HA promotion around typed, sticky planned-failover,
  planned-failback, and automatic-failover lineage plus a terminal promotion
  receipt. An independent start-only systemd reconciler now automatically
  restores the exact stopped former owner as an alias-free warm standby after
  commitment, while role-neutral `vm-ha` submits explicit retry intent
  through the exact owner. Planned failover and failback share the same
  stopped/starting/running preparation and fresh `standby_ready` gate; status
  adds a resource-identity-free redundancy panel and phase durations. Public
  VM and tunnel transfer behavior, role-bound transfer requests, heartbeat v1,
  lifecycle v4, checkpoint v4, strict former-owner `Stopped` fencing, and
  explicit-only failback remain unchanged. Standby evidence at least 10 seconds old now
  fails closed instead of remaining ready indefinitely on the same boot.
- Added the planned VM ownership transfer now exposed as `failover vm`.
  It submits strict configured-passive intent only after a read-only exact
  active-owner lifecycle and cloud preflight, while the existing controller
  retains all generation, readiness, former-Compute `Stopped`, allocation
  re-read, route, and forwarding gates. Tunnel-level `failover tunnel` and VM
  `failback vm` retain their established behavior.
- Added bounded repair-before-promote behavior for VM HA. A fresh unhealthy
  owner receives one persisted five-second local repair attempt with the final
  second reserved for a direct forwarding fence; redundant BGP neighbor loss
  remains forwarding only with complete required-prefix/XFRM coverage. VM-HA
  tunnel monitoring is observer-only, status exposes the secret-free attempt,
  checkpoint v4 migrates v1-v3 state, and the existing authoritative
  former-Compute `Stopped` requirement remains unchanged before allocation,
  route, or forwarding transfer.
- Fixed manual VM-HA failback after a real promotion leaves the
  configured-active request target stopped. The shared planned-transfer
  preparation now asks the independent owner-side rearm reconciler to start
  only that exact alias-free target, waits for pinned SSH and fresh passive
  readiness, and re-reads exact cloud ownership before submitting the existing
  fenced controller request. No operator CLI path starts Compute directly.
- Completed authorized non-production VM-HA acceptance with independent cloud,
  host, route, log, and real workload-VM evidence for steady state, BGP
  automatic failover, supported manual failback, Classic-VPN static failover,
  passive cold reboot, and final active/passive convergence. The retained test
  configuration uses only environment-backed PSK references.
- Fixed the VM-HA standby boot race where systemd could report StrongSwan
  active before VICI accepted requests. The blocked guard now stops and proves
  inactive only the exact active StrongSwan unit when connection unloading is
  unavailable, and VICI readiness requires a live Unix-socket connection
  rather than a stale pathname.
- Fixed fresh VM-HA member SSH enrollment by proving the selected mode-`0600`
  management private key matches the configured public key before cloud
  mutation, validating the rendered `sshd` configuration, activating either
  Ubuntu's socket-based or a service-based SSH model, and requiring a live
  port-22 listener before cloud-init completes.
- Added strict current-SDK recovery for VM-HA cloud effects: exact typed
  operation-lookup `UNIMPLEMENTED` resubmits only the same idempotent mutation
  and requires the same operation ID, while exact typed allocation
  `ALREADY_EXISTS` requires a complete exact-name resource reread. Completed
  lifecycle effects and pre-Compute allocation bindings now replay without
  reopening consumed guards or weakening resource-specific postconditions.
- Added `--psk-source-config` to the explicit GCP VM-HA fixture so one private,
  regular, mode-`0600` VPNGW config can supply exactly four existing PSKs,
  bound to the exact connection and tunnel names independent of YAML order,
  without placing them in process arguments, child environments, output, or
  temporary files.
- Replaced the nonfunctional legacy Service Account IAM scaffold with the
  current generated Nebius SDK clients and supported CLI impersonation. For
  ordinary gateways, explicit `apply --sa` enforces one dedicated same-name group, sole
  service-account membership, exactly one project `editor` permit, bounded
  secret-safe token capture, and fail-closed behavior with no ambient-credential
  fallback. VM-HA rejects this option and uses its deterministic managed
  runtime identity.
- Added an explicit, backward-compatible GCP two-member VM-HA peer mode with
  one regional HA VPN gateway, mirrored two-IP external peer resources, four
  tunnels/interfaces/BGP peers, member-grouped advertised priorities,
  secret-reference-only output and anonymous-descriptor secret delivery,
  bounded gcloud calls, collision-safe names, fail-closed read errors, exact
  IKEv2 and resource-shape validation, and read-only status/dry-run behavior.
  Legacy GCP status no longer starts browser login or changes the active
  gcloud project. Generated VM-HA lifecycle and writer-lock files are ignored
  as local control-plane state.
- Reduced local unit-test feedback time without changing production behavior,
  test selection, or assertions: the VM-HA crash-replay matrix no longer waits
  through listener-startup pacing outside that fixture's contract, while the
  dedicated retry test still verifies the exact production delay schedule.
  Five comparable 682-test runs improved from a 4.07-second to a 2.80-second
  median locally. A current 1,284-test remeasurement also removed a test-only
  two-second SDK operation polling delay while preserving the real waiter and
  request assertions; five comparable serial runs improved from an 8.98-second
  to a 7.03-second median.
- Hardened the existing Python project without changing its supported package,
  CLI, configuration, persistence, or release contracts: source-checkout
  `git describe` fallback now has a finite timeout, focused tests bind the
  established Python/entrypoint/SCM/Makefile contract, and standard local
  coverage, tox, and nox artifacts are ignored.
- Kept accepted VM-HA cloud-operation receipts durable when the SDK reports a
  terminal failure, so recovery cannot treat an unsuccessful operation as
  completed, and exposed the supported `apply --sa` option in CLI help.

- Made interrupted VM-HA activation resume from the exact v4 `ACTIVATING`
  lifecycle without re-entering provisioning, after stable cloud, member,
  allocation, route-target, and runtime-binding validation. Managed route
  replacement now uses a backward-readable v2 mutation journal containing the
  exact rollback snapshot, phase, and accepted Nebius operation; restart
  resumes delete, create, or restore by operation identity, and terminal create
  failure restores and verifies the original route before reporting failure.
- Hardened opt-in VM-HA crash and drift recovery with lifecycle v4 path-level
  cloud-effect guards, persisted accepted SDK operations and bounded HA-only
  waits, controller checkpoint-v2 transfer continuity with conservative v1
  reproof, typed stale-versus-foreign activation status, and exact final
  `ACTIVE` persistence recovery that restores and verifies the passive then
  active exact-operation apply locks when the lifecycle remains at
  `ACTIVATING`. Compute-create postconditions now reject unrelated aliases or
  substituted disk, allocation, NIC, project, and subnet identities, and all
  HA observation calls use the bounded SDK policy. V2/v3 lifecycle records and v1
  controller checkpoints remain readable, ordinary non-HA behavior is
  unchanged, and pull-request CI now runs the canonical all-source mypy gate.
- Made the first ordinary-to-VM-HA migration additive with post-provision
  activation checkpoints: `apply`
  retains the configured active VM and its disk, NIC, primary private address,
  public address, and unrelated aliases; provisions only the passive; and uses
  one movable secondary private alias as the HA address. The migration now has
  a read-only `--dry-run`, exact desired/current-state
  `--approve-vm-ha-migration DIGEST` approval, domain-separated
  `--recover-vm-ha-migration DIGEST`, a fsynced and CAS-protected v3 lifecycle
  transaction with mutation-time v2 successors, checkpointed replay-stable
  provisioning, strict allocation and member identity validation,
  passive-first staging, node/generation-bound apply locks, timeout-safe route
  reconciliation, pinned exact-status verification, and `ACTIVE` written only
  after active-first/passive-last unlock proof. Ordinary omitted/disabled HA
  apply performs no speculative HA discovery or SSH deactivation.
- Selected every `tests/integration` module in the integration test lane,
  including the composed VM-HA failover and runtime tests.
- Selected both complete composed VM-HA runtime and failover suites in the
  ordinary pull-request safety lane instead of leaving crash-replay and
  transition-ordering cases to manual integration runs.
- Fixed HA-to-ordinary apply ordering so both former HA members are discovered
  independently of the new instance count using the requested service account
  before any operator-authenticated cloud read, failing before discovery rather
  than falling back to ambient credentials when its token is unavailable,
  pinned and identity-rechecked, deactivated and verified (including
  retired-node services), and only then allowed to enter ordinary provisioning.
- Fixed the VM-HA clean-node bootstrap cycle by moving both initial owners
  through a non-forwarding passive materialization state, verifying that
  current-boot postcondition during passive-first activation, and reserving
  forwarding, firewall, cloud, and VPC route effects for exact active authority.
  VM-HA apply also rejects missing or unusable pinned SSH host trust before cloud mutation.
  Fresh members now receive prevalidated unencrypted private host keys matching
  their exact pins, while retained members are identity-verified before any
  mutation without requiring operator retention of server private keys. All
  VM-HA SSH paths consume one protected policy snapshot, and cloud-init
  enrollment fails before resource mutation when its structural anchors drift.
- Added explicit, default-disabled two-node VM-level active/passive HA with
  immutable generation and credential staging, authenticated peer state,
  authoritative stopped-owner and shared-secondary-alias fencing, owner-gated static
  and BGP route reconciliation, cold-start forwarding guards, durable recovery,
  status, and fenced manual failback while preserving omitted and disabled
  non-HA behavior. Deterministic offline two-node tests cover negative fencing
  and readiness gates plus restart after every takeover effect; live readiness
  remains a separately authorized validation boundary.
- Added concise static and BGP example configurations and clarified the subnet
  and route-table resources created by `prep-network`.

## [nebius-vpngw-v0.5.9] - 2026-07-07

- Added an ESP4 readiness preflight for new gateway VMs and a
  `misc/fix-vpngw-esp4.sh` repair helper for existing gateways affected by
  Ubuntu Dirty Frag module-block mitigations.
- Added a project mypy check and fixed current static typing errors in the
  VPN gateway source tree.
- Fixed local and release wheel builds by declaring the `vcs-versioning`
  provider required for the configured `semver-pep440` version scheme.
- Fixed release workflow artifact glob resolution so actionlint/shellcheck
  validation does not depend on unsafe word splitting.
- Fixed route command SSH handling so `add-routes-local` BGP route discovery and
  `list-routes-remote` gateway queries honor configured SSH user and private key
  settings.

## [nebius-vpngw-v0.5.8] - 2026-04-15

- Fixed explicit `gateway_group.external_ips` reuse for pre-created public IP allocations:
  - `apply` and `prep-network` now resolve existing public allocations by the requested IP in the current project before attempting to create a new one
  - explicit-IP runs now fail fast when the found allocation is still attached to another resource instead of warning and continuing
  - stale CLI-owned allocation names no longer silently override a different requested IP from YAML
  - removed cross-subnet public-allocation migration attempts; Nebius marks public allocation `subnet_id`, `cidr`, and `pool_id` immutable, so explicit-IP runs now require the allocation to already belong to the target gateway subnet
- Aligned CI/release wheel builds with the local `Makefile` build path so GitHub Actions suppresses the known transient `GlobalOverrides` warning during `python -m build --wheel --no-isolation`.

## [nebius-vpngw-v0.5.7] - 2026-04-08

- Fixed `add-routes-local` safety checks and output:
  - skip remote prefixes that overlap the target network's private pools before
    the Nebius API rejects them
  - sanitize inherited subnet status CIDRs against explicit CIDRs owned by
    other subnets before matching route-table targets, to avoid the Nebius
    inherited-pool display/API status bug
  - only treat an existing route as satisfied when the destination CIDR also
    points to the expected gateway allocation
  - when rerunning without `--summarize`, prune broader `vpngw-*` summaries
    after the exact desired routes under them are confirmed installed, so the
    command does not leave both summarized and exact managed routes behind
  - report connection-scoped BGP route counts instead of the raw FRR table size
- Added `add-routes-local --swap-route-table`:
  - builds a fresh custom route table per selected subnet
  - copies preserved non-`vpngw-*` routes from the currently attached table
  - rebuilds managed VPN routes from the current YAML before cutover
  - validates the replacement table before reattaching the subnet
  - requires explicit confirmation and writes rollback spec files plus exact
    `nebius vpc subnet update --file ...` rollback commands
  - updated live CLI `--help` text so operators see the validation-before-cutover
    and rollback-command behavior directly in `add-routes-local --help`
  - ignores local `.nebius-vpngw-rollbacks/` recovery artifacts and trims the
    confirmation warning to the traffic-impact/rollback guidance
- Clarified the `add-routes-local --summarize` documentation in `README.md`
  and `doc/design.md` with plainer wording and concrete CIDR examples so the
  exact merge behavior is easier to understand.

## [nebius-vpngw-v0.5.6] - 2026-04-07

- Fixed BGP route scoping for multi-connection gateways: `list-routes-remote`
  now shows only the selected connection's learned paths on the owning gateway
  VM instead of repeating the full FRR table for every connection, and
  `add-routes-local` now filters learned paths to that connection's tunnel
  peers before deriving Nebius VPC routes.
- Added `add-routes-local --summarize` for exact prefix collapsing per gateway
  next-hop allocation so large remote route sets can reduce Nebius route-table
  entry count without inventing broader supernets.
- Updated versioning configuration to the current `setuptools-scm`
  `semver-pep440` scheme and aligned runtime resolution/tests so `make all`
  no longer emits the renamed-scheme deprecation warning.
- Fixed multi-VM advertised-route labeling in `list-routes-local`: BGP peers are
  now matched to connections/tunnels using both peer IP and owning gateway VM,
  so reused APIPA ranges on different instances no longer cross-label output.
- Added regression coverage for representative multi-connection topologies,
  including the example 3-site single-VM and 3-site three-VM YAML layouts plus
  the explicit tunnel-selection behavior of `failover`/`failback`.
- Clarified live CLI `--help` text for multi-connection operation so
  `list-routes-remote`, `restart-tunnel`, `failover`, and `failback` now
  describe owning-VM scoping and when explicit tunnel selection is required.
- Aligned the `vpngw` GitHub Actions workflows with the service release path:
  CI now self-validates `vpngw` workflow YAML and exercises the wheel-build
  regression test before release publication, and both workflows use explicit
  Bash defaults for consistency with the monorepo pattern.

## [nebius-vpngw-v0.5.5] - 2026-03-31

- Added regression coverage proving `publish-release.sh --prep` remains
  idempotent for unreleased versions: reruns for the same version now stay
  no-op once `Unreleased` is empty and the tag has not been created.

- Fixed `publish-release.sh --prep` changelog formatting so moving
  `Unreleased` notes into a dated release section preserves a blank line before
  the next `##` heading, keeping the file markdownlint-safe in editors.

- Changed `publish-release.sh --prep` to fail before editing `CHANGELOG.md` if
  the target tag already exists locally or on `origin`, so duplicate release
  preparation for an already-published version stops immediately.
- Fixed source-checkout runtime version fallback for release tagging without
  `setuptools-scm` installed: `nebius_vpngw.__version__` now derives from
  `git describe` before consulting a generated `_version.py`, so
  `publish-release.sh --publish` no longer rejects a fresh exact tag because of
  a stale local dev-version cache.

- Fixed `add-routes-local` for pinned multi-VM topologies: remote prefixes are
  now routed through the gateway VM that owns each connection, and BGP route
  discovery is scoped to the owning VM(s) instead of querying every gateway VM.
- Fixed `restart-tunnel <name>` for multi-VM topologies: it now targets only
  the gateway VM that owns the selected tunnel, fails fast when the tunnel name
  is unknown, and has regression coverage alongside the existing manual
  `failover`/`failback` command paths.
- Simplified manual `failover` and `failback` tunnel selection: both commands
  now take the tunnel name as an optional positional argument instead of
  `--tunnel-failover` / `--tunnel-failback`, which matches `restart-tunnel` and
  relies on schema-enforced global tunnel-name uniqueness.
- Clarified manual failover semantics in both UX and docs: `failover` now
  explicitly remains an operational override that preserves configured YAML
  roles, and `status` now reports configured role separately from current
  traffic state with a `Traffic Override` panel when runtime behavior differs
  from the configured active/passive preference.
- Aligned `publish-release.sh --prep` with the shared release-template behavior:
  it now requires a named branch and auto-configures `origin/<current-branch>`
  as upstream on the first push instead of failing with Git's default upstream
  error.
- Tightened local release gating in `publish-release.sh`: the clean-worktree
  check now includes untracked files, and `--publish` now fails before tagging
  if the target release section exists but is empty.

- Pinned `Pygments>=2.20.0,<3.0.0` directly in project metadata and refreshed
  `uv.lock` so runtime installs, dev/test environments, and generated wheel
  metadata no longer permit the vulnerable transitive version.
- Fixed `apply` agent deployment for wheel-based installs: when a fresh local build is unavailable,
  SSH push now falls back to the originally installed wheel recorded in pip
  `direct_url.json` (including direct GitHub release URLs and local wheel files)
  instead of requiring `python -m build`.
- Cleaned up version packaging/runtime wiring: source checkouts now pass the
  non-deprecated nested `scm.git.describe_command` config to `setuptools-scm`,
  and wheel builds now use a package-local `version_file` so release artifacts
  no longer include a duplicate repo-relative `_version.py`.
- Changed the local developer `make build`/`make all` path to reuse the prepared
  project virtualenv (`python -m build --wheel --no-isolation`), which avoids
  noisy isolated-build `vcs_versioning` warnings while keeping local artifacts
  deterministic.
- Fixed runtime version resolution for source/editable checkouts so `nebius-vpngw` now prefers live `setuptools-scm` git state over a generated `_version.py` cache, and `publish-release.sh --publish` now verifies local runtime version/tag alignment before pushing the release tag.
- Clarified BFD documentation and comments: support is now described as vendor/platform specific, the template/README no longer imply generic cloud-VPN support, and the misleading GCP HA VPN BFD note was removed.
- Added concise Nebius Managed Kubernetes routing guidance covering `gateway.local_prefixes`, Pod-vs-ClusterIP expectations, and the common Cilium routing/masquerade defaults operators should account for over VPN.

## [nebius-vpngw-v0.5.4] - 2026-03-16

- Tightened multi-connection validation and template guidance: tunnel names must now be globally unique, APIPA tunnel ranges and BGP inner IPs must be unique per gateway instance, and the generated config/docs now clarify the supported multi-site active/passive workflow.
- Improved `status` for multi-connection gateways: `Carrying Traffic` is now computed per connection, and live FRR multipath across overlapping prefixes is surfaced as an `ECMP Warning` that names the prefix and the active tunnels carrying it.

## [nebius-vpngw-v0.5.3] - 2026-03-10

- Made the output path optional for `create-from-peer-config`; when omitted, the generated config now defaults to `./nebius-vpngw.config.yaml`.
- Added `--local-config-file` as an output-file alias for `create-from-peer-config`, with fail-fast validation if it conflicts with the positional output path.

## [nebius-vpngw-v0.5.2] - 2026-03-08

- Expanded the pytest-based test suite, split unit/integration coverage, centralized test config in `pyproject.toml`, and added `Makefile` targets plus service-scoped CI.
- Hardened operational CLI commands: `restart-tunnel` now performs a full IPsec and matching-BGP reset, and `failover`/`failback` were tightened and validated against the active/passive HA flow.
- Improved route management for Nebius workload subnets that inherit parent network pools, and added live BGP advertisement reconciliation so route commands reflect the current YAML instead of stale FRR state.
- Switched releases to the monorepo service pattern: `publish-release.sh` now handles prep/tagging, `vpngw-ci.yml` is PR/manual only, and `vpngw-release.yml` is the dedicated tag-driven GitHub Release workflow for `nebius-vpngw-v*`.

## [nebius-vpngw-v0.5.1] - 2026-02-04

- Fail fast when `--local-config-file` is provided but the config path does not exist.
- Fail fast for `list-routes-local` when gateway VMs are missing, and avoid traceback leaks on route listing errors.
- Inline `inner_cidr` `/30` guidance in the generated config template.
- Added `prep-network` command to create `vpngw-subnet`, reserve public IPs, and write them into `gateway_group.external_ips` (or allocate requested IPs when provided).
- `prep-network` now waits briefly and retries when a requested IP is still releasing.
- Status now uses BGP session uptime (from `show bgp summary`) when available.

## [nebius-vpngw-v0.4.9] - 2026-02-02

- Updated the release tagging to the prefixed format so tags include the app name (e.g., nebius-vpngw-v0.4.9), matching the multi-project release style.

## [v0.4.8] - 2026-01-20

- Adjusted SSH deploy to avoid rebuilding wheels for pipx/release installs and to prefer local release wheels when applying.
- Made SSH usage more Windows-friendly with OpenSSH presence checks and OS-aware null device handling.
- Updated install docs to emphasize downloading release wheels (Windows) and local wheel usage for pipx installs.

## [v0.4.7] - 2026-01-12

- Added `defaults.ha_mode` (active-passive default) and schema validation enforcing exactly one active tunnel per connection per gateway instance.
- Ensured passive tunnels include `gateway.local_prefixes` in traffic selectors so failover carries data, backed by swanctl/VICI `if_id` binding.
- Switched strongSwan rendering to swanctl (VICI) for deterministic XFRM interface binding and updated docs accordingly.
- Added manual `failover` and `failback` commands with BGP confirmation + elapsed time reporting; status now displays admin-down neighbors as `Down (Admin)`.
- Status output now includes tunnel role, carrying-traffic indicator, encryption, and d:h:m:s uptime, with swanctl de-duplication; list-routes-local shows role labels.
- Improved swanctl/VICI load reliability with socket readiness checks and retries.
- Updated defaults/template: IKEv1 disabled, SHA1/MODP1024 removed, BFD support kept optional (bfdd toggled when enabled) with default disabled, DPD/BGP timers set to 5/15 and 2/6, health monitor interval 10s with ping disabled, and `gateway.ipsec_mode` explicit; condensed template comments.
- Health monitor defaults/docs now reflect 10s checks and faster detection timing; ping checks remain optional.

## [v0.4.6] - 2026-01-10

- Added health monitor improvements: respect `health_monitoring.ping_enabled`, detect stale XFRM tunnels via error-counter deltas, and guard against duplicate monitor instances.
- Updated health monitor systemd unit to use a runtime directory and writable path for the lock under `/run/nebius-vpngw`.
- Fixed `restart-tunnel` ImportError by using the resolved plan merge path in the CLI.
- Avoided overlapping XFRM policies in HA by excluding local prefixes from passive tunnel `leftsubnet`.
- Stabilized FRR installation on Ubuntu 24.04 by removing the pinned package version and adding an apply-time install fallback.

## [v0.4.5] - 2026-01-07

- Added Active/Passive HA support with BGP MED (Multi-Exit Discriminator) and local-preference for bidirectional path control.
- Active tunnels use MED=0 and local-pref=200; passive tunnels use MED=100 and local-pref=100 for deterministic routing.
- Disabled `ensure_local_prefix_routes()` in frr_renderer.py and routing_guard.py to prevent routes that break packet forwarding.
- Added `no bgp network import-check` to BGP configuration to allow prefix advertisement without kernel routes.
- Comprehensive MED documentation added to design.md with verification commands for both Nebius and peer sides.
- Enhanced Project Structure documentation in design.md and README.md with all agent modules and systemd components.
- Added MTU/MSS hardening for XFRM gateways: TCP MSS clamp, TCP MTU probing, ICMP frag-needed allowances, and explicit XFRM MTU calculation.
- Ensured XFRM interfaces and local prefix routes are enforced even when config is unchanged; state tracking now uses render version.
- Routing guard now canonicalizes internal CIDR routes (dedup + onlink metric), flushes route cache after fixes, and uses a shared lock to prevent concurrent enforcement.
- Fix-routes timer now runs the Python entrypoint with systemd ordering/conditions and config path; legacy fix-routes shell script removed.
- Deployment updates: firewall setup script externalized, systemd assets staged via SSH push, and agent restart/reload logic refined.
- XFRM IP assignment made idempotent via `ip addr replace`.

## [v0.4.4] - 2025-12-21

- Added `create-from-peer-config` command to generate YAML from vendor peer files; removed `--peer-config-file` from `apply`.

## [v0.4.3] - 2025-12-20

- Enforced nested `gateway_group.external_ips` (list of lists) in schema and removed legacy flat-list handling.
- Static routing now requires `remote_prefixes` (connection-level or per-tunnel); example configs updated accordingly.
- Firewall setup aligned with XFRM BGP: TCP/179 not exposed on eth0, with tunnel-interface allowances and ICMP handling clarified.
- Embedded config template/docs refreshed; redundant template file removed and `*.config.yaml` ignored.

## [v0.4.2] - 2025-12-19

- Secrets file logging no longer prints the file path (CodeQL clear-text logging).

## [v0.4.1] - 2025-12-19

- Deployment no longer attempts Poetry builds; wheel build uses `python -m build --wheel` only.
- Hardened strongSwan secrets write: atomic file update, 0600 perms, CodeQL justification.
- SSH push install now verifies installed version via import metadata and uses a concise success log.
- Ruff lint configuration added with project-specific ignores/exclusions (including generated `_version.py`).
- Release script changelog update keeps a blank line between release headers and content.

## [v0.4.0] - 2025-12-19

- fix changelog update issue in the release.sh script

## [v0.3.0] - 2025-12-19

- Added new flags to the release.sh script (get --help please)
- Reformated README.md and doc/design.doc
- Removed the Poetry build path so nebius-vpngw apply always builds with python -m build --wheel. This avoids the likely Poetry failure with the current pyproject.toml.

## [v0.2.0] - 2025-12-18

### Added

- Git tag–driven versioning via `setuptools-scm` with a `--version` CLI flag that surfaces the tagged release.
- Clear install paths for end users (pipx + GitHub release wheel) and developers (editable install).
- Release workflow guidance for tagging, building, and publishing wheels with GitHub CLI.

## [0.1.0] - 2025-12-17

### Added

- Initial public release of the Nebius VM-based VPN Gateway orchestrator and agent.
- YAML schema validation with embedded config template generation.
- Multi-cloud peer support (GCP HA VPN, AWS Site-to-Site, Azure VPN Gateway, Cisco IOS).
- Agent-side routing guard, XFRM interface management, and UFW synchronization.

### Changed

### Fixed
