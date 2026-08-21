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
  survive management-address changes, explicit overrides remain strict, and
  no path uses the general known-hosts file, network-only enrollment, or
  disabled verification. SSH protects operator management, while mTLS protects
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
  fenced member. The new digest-approved `set-vm-ha-mtls` command performs a
  resumable passive-first two-member rotation with controller/rearm inhibition,
  three fresh authenticated observations, exact pruning, and no scheduled
  renewal. Status reports only each member's closed mTLS health state; epochs,
  fingerprints, phase, and inhibition stay internal. VM-HA YAML now accepts
  only a node-scoped
  `nebius_credentials_path`; the unreleased operator-PKI shape and heartbeat-v1
  protocol have no compatibility reader.
- Fixed explicit VM-HA status SSH diagnostics and route-authority reporting.
  Every probe now uses the configured management user and key, exact host pins
  are isolated per member without permissive fallback, and missing trust is
  reported as `ssh-trust-unavailable` instead of making both controllers
  unknown. Cloud authority now distinguishes route-target, managed-record,
  prefix-set, and shared-allocation next-hop drift and emits a reason-specific
  apply repair action. `vm-ha-rearm` remains the start-only retry path and does
  not gain trust, generation, local-route, cloud-route, allocation, firewall,
  or forwarding mutation authority.
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
- Added `configure-vm-ha` for guided conversion of a supported ordinary
  single-VM config into a new explicit two-member VM-HA candidate. The
  two-phase wizard preserves the raw source and environment references,
  derives passive-member tunnel counterparts, can separately reserve only the
  deterministic passive public allocation, stops without a draft while peer
  setup is pending, preflights both mode-`0600` Nebius credential JSON files before cloud
  access, and conditionally publishes a complete mode-`0600` candidate without
  clobbering a racing writer before handing off to the existing migration
  dry-run and approval workflow.
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
  commitment, while role-neutral `vm-ha-rearm` submits explicit retry intent
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
  current generated Nebius SDK clients and supported CLI impersonation. An
  explicit `apply --sa` now enforces one dedicated same-name group, sole
  service-account membership, exactly one project `editor` permit, bounded
  secret-safe token capture, and fail-closed behavior with no ambient-credential
  fallback; runtime authorized-key enrollment remains separate.
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
