# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Fix the in-place Jail login handoff so one protected SSH Pod always stages a
  true extra target login replica instead of reusing the configured replica
  count. The temporary surge now has checkpointed Helm intent/proof, exact
  SlurmCluster and OpenKruise desired-replica validation, Ready target-rootfs
  Pod and Service-endpoint gating, and a separately proven restoration to the
  configured count after voluntary handoff. Existing v1 surge checkpoints are
  upgraded only from their exact target binding and accept live replica counts
  only at the locked configured or surge boundaries. Once the peer is Ready,
  the Jail phase remains explicitly pending while the original socket and hold
  are live; both fresh and resume completion paths require the hold released
  and the temporary surge restored. Surge and restore Helm values retain the
  canonical bridge-owned manager/controller fence and require exact semantic
  apply proof. Archived protected-Pod rotations are accepted only from a
  complete zero-session, drained, timestamped release record. Older exact
  handoffs that restored the manager before the current phase-owned pause
  journal existed now rearm that pause only from the immutable manager UID,
  non-replica spec, exact target SlurmCluster namespace/name/UID, and monotonic
  capture/pause/restore generations. The rearm checkpoint permits only its
  original replica count and one generation transition, including exact
  crash-after-dispatch recovery. Every bridge-gated Helm replay now renders
  `controllerManager.replicas: 0`, so Helm cannot wake the paused manager
  between the pre/post replay fences. The post-Jail OpenMetrics restoration
  pulse now carries the full campaign checkpoint through that same gate at
  source-HA and target-HA bridge authority. The temporary login Helm pulse also
  recognizes the exact `target-enabled` accounting predecessor when import,
  source retirement, target writer enable, restored command fence, and
  target-owned Deployment evidence are complete but final Slurm registration
  verification is deliberately waiting for that same SConfig pulse; ordinary
  target Helm replay still requires fully verified accounting. Each temporary
  surge or restore intent now also revalidates live source absence and exact
  target-writer identity/readiness immediately before the Helm mutation. Its
  post-Helm proof validates the exact restored-writer manifest instead of
  incorrectly requiring the earlier command-fenced lifecycle state; any
  unexpected live command fence or other SlurmDBD drift still fails closed.
  While the exact Soperator manager remains paused, a checkpointed
  campaign-bound webhook-only Deployment with a unique selector now serves
  fail-closed chart admission through an exact Deployment/ReplicaSet/Pod
  lineage. It enables only the SlurmCluster and NodeSet setup paths required to
  register those webhooks, restricts their controller cache to a repeatedly
  verified-empty `kube-system` namespace, and fails before mutation if any
  Soperator resource appears there. The chart Service's sole TCP/443 target
  must bind one manager port; that port replaces the generic readiness probe
  and must appear on the exact owned EndpointSlice before admission dry runs.
  Pod-template proof normalizes only the API-default `default` service account
  and standard NotReady/Unreachable eviction tolerations; any non-default
  scheduling drift remains blocking.
  The bridge derives the bounded NodeSet set from the target SlurmCluster's
  exact partition references and requires each live NodeSet to be an ownerless,
  target-parented Helm resource before proving SlurmCluster and NodeSet dry
  runs. It is UID-precondition deleted after either Helm success or failure;
  resume reconciles owned-lineage cleanup before every surge/restore early
  return and re-proves absence from successful inventory reads even for a
  previously `deleted` checkpoint. Existing v1 bridge records are accepted
  only for that UID-bound cleanup and archival path; all new bridge intent uses
  the namespace-isolated v2 schema.
  A voluntary zero-session hold release can no longer bypass a prepared surge:
  cxcli proves the exact bounded failed-webhook revision chain and identical
  intent values, reconstructs the temporary replica count from the immutable
  surge contract instead of the steady-state input values, establishes the next
  deployed revision through the owned webhook bridge, reconciles the released
  peer gate, and restores configured replicas before downstream GPU repair
  selects Helm release state.
  If a later active session creates a new protected Pod after that full
  release, the archived complete release journal authorizes rotation away from
  the old Pod UID without requiring a synthetic shrink-history entry.
- Recover a missing in-place ownership-handoff marker only from the exact
  checkpointed target-child gap proof: current target Helm evidence, immutable
  child bindings, manager generation, controller authority, and owned Slurm
  partition pauses must all agree. Resume then reconciles the independently
  proven temporary login surge before reaching the canonical protected-session
  gate instead of waiting for a held OpenKruise rollout and reporting a generic
  accounting-readiness failure; strict rollout and source-retirement checks
  remain unchanged.
- Render external-upgrade provider table headings in bold black on
  color-capable terminals. When a phase signal contains the multiline provider
  table, the following `Slurm Workers` and `Soperator` summaries now start on a
  new line at column one instead of being appended to the final provider row;
  plain and persisted status text remains free of terminal markup.
- Admit both the source and target Soperator instance labels through the
  controller-bridge NetworkPolicy before target Helm adoption can relabel
  clients and before the cold source-to-target bridge transition. Resume
  performs the same idempotent reconciliation before reusing a checkpointed
  target-HA proof, preventing target-adopted login and worker Pods from losing
  Slurm controller connectivity after the new bridge becomes Ready.
- Share one owner-only, command-lifetime ExecCredential cache across kubectl
  subprocesses created from a temporary MK8s kubeconfig. File locking
  single-flights concurrent refreshes, refresh starts before expiry, a still
  valid cached token remains available during a transient refresh failure,
  and a sanitized cooldown prevents lock waiters from serially repeating the
  same failed exchange. The temporary kubeconfig itself is now written
  atomically with mode `0600`. Redacted failures include a stable timeout or
  credential-exchange reason without exposing SDK details.
- Preserve the exact source-retirement checkpoint when an interrupted
  accounting import revalidates its sealed dump. Post-retirement resumes now
  prove the retired source UID, retained PVC, and target-only command fence
  instead of incorrectly requiring the deliberately deleted source
  SlurmCluster to still exist.
- Rebind a verified accounting schema-bootstrap proof across an exact
  checkpointed post-retirement writer Pod rollout. The original binary and
  network-isolation proof stays immutable; only a successor with the same
  Deployment UID, owner, image/runtime image, stable labels, Helm proof, and
  target-only command fence is journaled, while unproved replacements remain
  blocked.
- Drive an approved external Soperator v6 campaign to completion in one
  `ext-soperator upgrade --execute --approve` invocation. Each segment remains
  checkpointed and limited to one Kubernetes minor hop; after terminal segment
  success cxcli refreshes discovery, reacquires the campaign lease, revalidates
  config and journal authority, and continues. Pending phases, errors, explicit
  stop points, and interrupts still stop immediately for exact-command resume.
  Upgrade plans now distinguish current-segment support policy and phases from
  the campaign final target, show the committed support rule for every locked
  segment, include the executor's final MK8s/Helm postchecks, and safely classify
  source-fence enumeration failures without persisting raw kubectl output.
- Bound OCI registry control-response reads before parsing image indexes,
  manifests, configs, token responses, or error bodies. cxcli now rejects a
  declared or streamed response larger than 16 MiB instead of reading an
  unbounded body into memory.
- Declare the `watchfiles` development dependency used by the committed VS Code
  `pytest-watch` task so a clean `make env` installs the task's executable.
- Adopt a same-generation manager-pause fingerprint reserialization. A
  control-plane (API server) upgrade can re-default the identical paused
  soperator-manager Deployment spec, so the checkpointed non-replica spec
  fingerprint drifts while the Deployment generation still equals the pause
  expectation — and the pause integrity gate aborted recovery-required
  ("non-replica spec changed during its pause") on a spec that provably
  never mutated. The gate now adopts the reserialized fingerprint under an
  explicit journal record only at the exact checkpointed generation; drift
  with any generation movement still fails closed. The bridge/phase
  manager-pause consistency gate accepts two pause fingerprints connected by
  either journal's recorded reserialization chain — the reserialization is
  journaled on whichever pause re-verified the live Deployment first, so the
  other journal legitimately carries the pre-drift fingerprint of the
  identical spec; unlinked fingerprints still fail closed.
- Retry a torn controller-state Pod copy stream. The bridge cold/state
  artifact copy (`kubectl cp` from the reader Pod to the local checkpoint
  directory) is a cluster read whose stream can break mid-transfer
  ("unexpected EOF"), and a single failed attempt aborted the whole execute
  invocation during the fenced cold-sync window. The copy now retries
  bounded transient stream failures; every attempt writes a fresh owner-only
  partial file that is atomically promoted only after the stream completes,
  and deterministic copy errors still fail immediately.
- Resume a checkpointed bridge fence through the post-provider compute tail.
  When authority has transferred to the target singleton and the bridge
  writers are stopped but the zero-to-one singleton start has not completed,
  the rolling-compute resume replayed bridge-client staging, whose direct
  ping RPC can never succeed against fenced zero-replica writers — a
  deterministic pend. The compute-tail bypass now admits the fenced stage
  when the journal proves the bridge stop, the post-stop API-absence census,
  the verified pre-takeover client propagation proof, no active recovery
  bridge, and terminal replacement evidence for every planned node group;
  incomplete fence evidence remains recovery-required. The fenced-takeover
  client-configuration reconcile likewise accepts an interrupt recorded
  between singleton-start dispatch and acceptance — the takeover driver and
  bridge fence already resumed both recorded intents, but the reconcile
  admission demanded `accepted` and turned a mid-dispatch interrupt into
  recovery-required; an unrecorded start intent still fails closed. The
  bridge journal validator's interrupted-recovery lattice also gains the
  gated-controller dispatch shape — an interrupted singleton start that was
  ungating the one exact recorded inert-gated Pod (replicas already 1,
  `gated_pod_uid` bound) rather than scaling zero-to-one; a replicas-1
  dispatch without the gated Pod identity still fails closed.
- Absorb the MK8s exec-credential token-expiry boundary. The exec plugin
  serves IAM tokens from a cache; a kubectl call landing exactly at token
  expiry is rejected once with Unauthorized and the next exec invocation
  re-exchanges a fresh token, but the executor treated that first rejection
  as terminal — a long execute invocation died mid-phase ("You must be
  logged in to the server") and the campaign lease was released. Read-only
  kubectl commands and the lease renew/patch path now retry the exact
  Unauthorized marker within their existing bounded transient budgets;
  mutations still fail closed and a lease holder-test failure still aborts
  immediately. Lease acquisition likewise retries a timed-out kubectl call
  inside its bounded loop — the exec-credential exchange can transiently
  consume most of the kubectl budget — and the acquisition re-read
  reconciles a replace that landed server-side despite the client timeout;
  a campaign locked by another workstation still fails immediately.
- Mirror `--acknowledge-job-ended JOB_ID` onto the managed `soperator jobs`
  command. The managed upgrade journals the same preserved running-job
  baselines through the shared controller-bridge phases, so a preserved job
  ended out of band left a managed operator without the attestation path the
  external command already had. The option records the identical journaled
  attestation (Queued → Dispatching → Applied, no live RPC) against the
  managed checkpoint.
- Absorb transient Ready-Pod coverage gaps in the managed populate-jail
  rootfs-handoff `/home` mount verification. The managed path raised a
  pending failure on the first probe pass, so a login or worker Pod observed
  mid-recreation pended the whole command; it now uses the same bounded
  coverage wait as the external in-place worker health verification (180s,
  real mount failures still fail immediately).
- Share one fast-verification convergence loop between the external campaign
  executor and the managed upgrade command. The two paths carried
  behavior-identical retry loops that could drift; the managed delegation
  keeps provider errors suppressed while the external caller propagates
  compute failures, both under the same budget knobs.
- Recover a protected-login absence epoch after an exact terminal in-place
  provider rollout replaces the source Pod. Resume now recognizes the
  same-name, same-workload successor only when the released zero-session hold,
  provider operation identity and lifecycle, node-group binding, target
  Kubernetes version, and Ready postcondition all match. Unresolved session
  fingerprints remain indeterminate until the operator records voluntary exit
  or explicitly authorizes continuation after an involuntary timeout; Pod
  replacement never supplies that disposition.
- Drive the deferred accounting handoff verification from the post-cutover
  orphan cleanup. The rolling-compute phase can complete while registration
  and history verification is still deferred to the SConfig compatibility
  pulse, and every completion caller lived in that phase, so a
  `target-enabled` handoff could never reach `verified` once the phase
  completed and the validation-and-rollback-hold cleanup pended
  deterministically. The cleanup now invokes the idempotent completion when
  the handoff is `target-enabled` and the deferral no longer applies.
- Add `ext-soperator jobs --acknowledge-job-ended JOB_ID` to record an
  explicit operator attestation that a preserved running-job baseline was
  ended out of band. The running-job preservation proof already accepts
  journaled operator actions ("operator-modified" evidence), but only the
  executor could write them; a job whose accounting record became
  unobservable — for example the bridge-era controller reused its JobID and
  the accounting handoff dump predated its completion — pended
  deterministically ("did not retain its exact allocation/start/restart
  lineage"). The attestation is journaled against the immutable job binding
  through the durable Slurm action journal (Queued → Dispatching → Applied,
  no live RPC) and only preserved baseline jobs can be attested.
- Mirror the post-Jail smoke writer's temporary-partition condition in the
  inert-controller gap proof. The writer creates the root-only temporary
  partition only while checkpointed partition pause records exist; the gap
  proof demanded the deleted-partition lattice unconditionally, so a completed
  smoke that legitimately ran through a restored production partition could
  never satisfy phase validation. An empty partition record with no pause
  records is now accepted; any partial partition record or live pause still
  requires the full lattice.
- Accept the typed-GRES Helm successor for a post-OpenMetrics ConfigMap proof
  recorded before any topology-replay record existed. The first binding can
  legitimately be journaled in the pre-promotion era (prior Helm revision, no
  replay record); the successor acceptance previously demanded a checkpointed
  replay to anchor the era and pended deterministically with "drifted from its
  exact checkpointed proof". The first-instance era is now anchored to the
  verified first-bind gate-rearm ledger carried by the proposed replay, while
  recorded-era successors keep their exact prior-replay anchoring; identical
  worker topologies are accepted alongside the typed-GRES-only change.
- Admit the first post-OpenMetrics Slurm ConfigMap bind after an exact fenced
  controller-gate rearm. The manager-pause, gate-rearm, writer-release
  ordering makes the first bind observe the rearmed gate before any
  topology-replay record exists, so the prior-anchored rearm replay could
  never be constructed and the bind pended deterministically with "lacks the
  exact checkpointed Helm and target-generation proof". The first-bind
  admission counts both journaled generation bumps exactly — the verified
  typed-GRES topology replay one beyond the release scale expectation, plus
  the fenced gate rearm — and requires the canonical inert gate live on the
  target, its restored Fail admission window, and the verified manager pause.
  Any other generation delta or gate shape still fails closed.
- Defer the exact pending typed-GRES nodeset values delta from the GPU driver
  jail init repair Helm proof to the topology values replay. After the
  OpenMetrics restore, target workers register their typed GRES topology and
  the journaled `worker_topology_by_nodeset` legitimately evolves the desired
  `nodesets` values; the repair seal previously demanded stored==desired
  before the replay could ever run — an ordering livelock. The deferral
  requires the entire stored-versus-desired delta to be the `nodesets`
  document, a journaled topology with complete static tokens for every GPU
  nodeset, and no already-verified replay; any other values drift still fails
  closed. The repair drift message now also names the failed comparison and
  the exact differing value paths instead of a blanket "drifted after
  verification".
- Admit the exact no-revision post-Jail boundary rebind in the GPU driver
  jail init repair Helm proof. When the manager restore produces no config
  change, no config successor is ever journaled and the boundary rebind binds
  to the repair's own release revision; the repair check previously demanded a
  compatibility-predecessor lineage in that state and pended deterministically
  with "compatibility predecessor drifted from its exact Helm, target, or
  ConfigMap successor proof." The no-revision rebind is admitted only with the
  exact rebind schema, verified status, target UID, chart/app version, fenced
  values fingerprint, and the repair's release revision; the OpenMetrics
  restore then remains the sole next Helm revision after the repair proof.
  Direct and inherited successor lineages are still checked strictly.
- Keep accounting registration/history verification deferred through the
  `target-children-created` immutable-child stage. The deferral predicate
  previously lifted after `manager-restored`, so a resume landing between
  target-children creation and the SConfig compatibility pulse probed cluster
  registrations live and failed closed ("could not verify cluster
  registrations through the target-version sacctmgr"), even though no
  version-matched sacctmgr route can exist in that window — active-slot Jail
  clients are already target-version while the retiring accounting daemon
  still speaks the source protocol. Verification still runs unchanged once the
  pulse reaches `target-compatibility-active` or `released`.
- Adopt new canonical target partitions into the continuous Slurm scheduling
  pause instead of stopping with "changed partition set after the
  all-partitions job-free boundary." When the live partition set is a strict
  superset of the exact pause journal, each new partition is live `UP`, the
  controller-bridge journal proves exclusive bridge-target write authority,
  and the name is absent from the bridge's source-era partition inventory,
  the addition is attributable to the bridge-staged target chart: cxcli
  persists the full pre-mutation record to both the Jail and rolling journals
  through the same intent-first pause, applies `State=DOWN`, and verifies the
  exact post-mutation fingerprint. Removed names, non-`UP` additions, and
  source-era inventory names missing from the pause journal still fail closed.
- Keep the checkpoint-owned Slurm scheduling pause authoritative through the
  earliest immutable-child controller-gap window. The child-handoff partition
  pause gap contract now also matches the `manager-paused` stage while the
  legacy-rootfs dual Slurm config bridge is inside its pre-write zero-writer
  window, because the journaled version-transition shared-Jail write that
  changes the live partition set happens exactly there; a resume landing in
  that window no longer stops with "changed partition set after the
  all-partitions job-free boundary."
- Re-fence the exact checkpointed Soperator manager when an out-of-band
  reconciler resumes it after the durable immutable-child pause. The re-pause
  is accepted only for the same Deployment UID with an unchanged
  selector/template contract, replicas back at the exact original count, a
  later generation, and a verified durable source Flux reconciliation-fence
  suspension, so the re-pause cannot fight a live reconciler; it journals every
  fenced repause and re-drives the canonical generation-fenced pause. Foreign
  spec drift or an unfenced resume still fails closed as before.
- Thread the live controller-bridge journal into the pre-retirement immutable
  child handoff so the legacy-rootfs zero-writer Jail classification can admit
  the journaled bridge-staged `slurm.conf` (source-legacy-safe files plus the
  exact `version_transition` digest) instead of deterministically stopping with
  "mixed or outside every complete allowed payload" during first adoption under
  an active bridge. A canonical intent checkpointed by the pre-retirement
  client-authority bug is now rebound in place to the bridge-aware canonical
  when the dual-writer fence is still mid-zero-window with no written payload;
  the rebind runs under the same exact target-HA journal guards, records a
  `rebound-pre-write` recovery journal, and the zero-writer payload and health
  gates still verify the rebound intent before any mutation consumes it. A
  completed zero-window still requires the original ready-fence adoption proof.
- Retry a failed external-upgrade fast stage verification in-process for a
  bounded read-only convergence budget (default 300 seconds, 20-second poll)
  before recording the failure and stopping as pending. Snapshot-consuming
  stages refresh the live snapshot between attempts, a converging rollout no
  longer forces a full execute re-invocation with its discovery and preflight
  overhead, and an unconverged verification still fails closed with its
  attempt count recorded. The managed `soperator upgrade` fast stage gate now
  mirrors the same in-process retry for its live-read verifications (the Jail
  Upgrade result and the shared protected-state/fast safety verifier),
  sharing the external path's single convergence budget; evidence-only gates
  stay single-attempt, and an unconverged managed failure still writes the
  report and raises as before.
- Restore the exact Slurm partition records immediately after a completed
  target-values reconciliation when the campaign has already crossed its final
  partition-release and controller-bridge-cleanup boundary. A resume may still
  pause scheduling around that Helm reconciliation, but it no longer reuses an
  older restore receipt while leaving the newly paused partition DOWN for
  protected-state validation. The common resume path also reconciles this
  restore after the values revision is already current, rejects conflicting
  phase/global pause journals, and safely completes a phase journal left stale
  after the global restore decision was checkpointed.
- Keep a retired legacy Jail PVC fail-closed until the verified rootfs storage
  successor handoff also consumes its exact command-owned protected-state
  proof. The final external validation no longer rejects that one proven
  transition merely because missing PVCs are generically blocked, while
  arbitrary or unproven missing PVCs remain blocked. At the validated
  target-singleton handoff, exact Helm release-secret and target SlurmCluster
  deltas may now enter the same immutable chart-operation proof path; an
  incomplete handoff or unrestored manager still leaves them protected.
  After bridge cleanup, the immutable terminal transition proof retains that
  same narrow target SlurmCluster spec-hash eligibility while arbitrary
  resource or field drift remains approval-required.
- Bind final Helm manager restoration to the exact live deployed manager
  manifest before applying final values. A bridge-adopted pause journal may now
  checkpoint its previously missing Helm-spec fingerprint only after the
  Deployment UID, paused spec/generation, rendered final manager, target, and
  values all match; recorded Helm drift still fails before mutation.
- Advance an in-place post-provider controller bridge from its checkpointed
  source-version HA state to target-version HA before singleton takeover. The
  tail now uses the canonical gated cold-transition and source-bridge recovery
  path instead of deterministically asking the singleton guard to consume an
  unproduced target-HA proof.
- Build later-segment GPU topology Helm recovery from the exact deployed values
  and replace only the proven worker static topology plus canonical controller
  gate. Target-CR API defaults and lifecycle-derived jail, partition, or load
  balancer fields can no longer leak into this topology-only replay. After the
  Helm successor, re-pause the Soperator manager and reassert the inert
  controller gate before worker readiness or bridge-exclusivity checks.
- Reconcile the exact active-login PodUnavailableBudget before serial bridge
  configuration restarts. A drained hold is released, a mixed hold is shrunk
  to Pods with active SSH sessions, and a selected Pod that remains protected
  is never deleted.
- Adopt a sequential-hop controller bridge's exact verified manager-pause
  authority into the current rolling-compute phase before service replay. The
  durable adoption binds bridge stage and authority epoch plus manager
  Deployment UID, replicas, immutable spec, and pause generation; incomplete
  or unverified bridge pauses still fail closed.
- Reconcile the immutable-child worker Pod handoff through the canonical final
  target-worker runtime reload. Resume now records the exact completed
  provider-rollout successor followed by the same-node, job-free,
  final-config-loaded Pod recreation, while continuing to reject unproven UID,
  owner, node, readiness, restart, container, or authority changes.
- Build the external terminal protected-state proof from the canonical
  manager-pause journal across sequential upgrade phases. A manager pause that
  has completed its exact verified restore remains valid historical transition
  evidence, while missing identity, replica, or immutable-spec bindings still
  fail closed. The historical source configuration is bound to its immutable
  JailedConfig-discovered ConfigMap reference instead of a target-derived name.
  Segment completion now also requires both the verified terminal
  operation intent and its completed-segment record, so an all-phases-complete
  checkpoint awaiting terminal safety continues to reuse its immutable
  pre-mutation backup instead of creating an unbindable successor archive.
- Retry an exact transient `etcdserver: request timed out` response while
  renewing or fencing the cluster-visible Soperator upgrade Lease. Holder
  identity conflicts and other non-transient patch failures still stop
  immediately.
- On a `target-singleton-active` resume, repair and prove the final one-host
  Slurm configuration before revalidating target JWT material. Reconfigure now
  waits for the exact jailed projection and explicitly binds `SLURM_CONF` to
  that file; the same strict JWT proof remains mandatory after repair, while
  already-finalized handoffs retain their normal resume-time revalidation.
- Classify a completed first-segment bridge-to-target-singleton handoff as a
  post-provider compute-tail resume boundary. Resume now bypasses obsolete
  manager, bridge-client, and provider replay, while direct bridge-client
  staging fails closed after target-singleton authority instead of repointing
  clients toward already-fenced bridge controllers.
- During the final target-worker runtime reload, adopt only exact
  target-chart-owned partition field successors before strict scheduling-pause
  reassertion. The reload now also consolidates topology-owned partitions into
  the active Slurm recovery journal and reasserts the complete checkpoint-owned
  pause before any worker deletion. On resume, final client propagation now
  consumes only the exact verified v2 worker-recreation successors that it
  produced. The rebase supersedes only exact prior evidence and continues to
  reject customer-owned or unknown partition drift.
- Complete the initial in-place controller bridge handoff at the post-provider
  compute-tail boundary before final worker runtime reconciliation and source
  cleanup. This preserves the deliberate bridge-owned worker rollout while
  ensuring cleanup sees the existing exact target-singleton, command-ungate,
  and primary-authority proofs instead of deterministically running ahead of
  their producer.
- Bind active-jail GPU release evidence to each live Kubernetes node UID and
  provider node-group ID. Post-activation replay now accepts a complete
  provider-terminal worker generation that exactly covers the checkpointed
  replacement node UIDs, while continuing to reject mixed typed-GRES/provider
  Pod generations and foreign or partial node-group lineage. When an exact
  zero-drain topology replay predates that strengthened release evidence, it
  now rebuilds and runtime-verifies the inert-controller bridge authority
  against the full provider-successor generation before returning. The
  provider-successor history is checkpointed before that authority is sealed,
  so post-activation cannot invalidate the new release-gate fingerprint with a
  later telemetry write.
- Classify GPU topology restoration from completed campaign segments before
  enforcing its pre-provider boundary, so a first-segment replay can reuse its
  own checkpointed topology after the worker provider operation. True
  later-segment restores still require a verified inherited-topology checkpoint
  before any worker provider mutation.
- Wait through a bounded, coverage-only convergence window when target worker
  NodeSet status reaches Ready before the corresponding target-owned Ready Pod
  list becomes coherent during an in-place provider rollout. Actual `/home`
  mount failures still fail immediately, and coverage timeout still retains
  the cxcli-owned Slurm drains.
- Preserve the predecessor live-RPC hashes across an already-validated login Pod
  successor during controller-gap config-only replay, avoiding a raw UID lookup
  failure after an in-place login node-group rollout while retaining fail-closed
  stable client identity checks.
- Restore the checkpointed target SlurmDBD writer before requiring target
  controller health during an in-place external Soperator Jail handoff. All
  equivalent execution and replay paths now complete the existing fenced,
  source-retired accounting import before the legacy-rootfs bridge performs
  its controller and partition checks, avoiding a deterministic dependency
  inversion while preserving the existing identity and mutation guards. The
  continuous scheduling-pause guard also reuses the validated bridge partition
  journal through the exact UID-bound `target-children-created` controller gap
  instead of treating an intentionally unavailable partition RPC as inventory
  drift, and cross-checks the complete handoff workload set against the sealed
  pre-orphan inventory. Resume now also recognizes only the exact
  OpenMetrics-disabled ConfigMap successor produced by the checkpointed manager
  restore and current Helm manifest, then promotes it under the rebound
  size-zero writer fence instead of misclassifying that known transition as
  arbitrary digest drift. Manager-restore verification now follows that exact
  ungated Helm successor through every execution and replay path instead of
  reconstructing the earlier temporary controller-gate revision, while still
  requiring full stored-values equality. Target-worker recovery also runs the
  existing UID/resourceVersion-preconditioned orphan Pod rollover and
  checkpointed GPU repairs before aggregate SlurmCluster availability, any
  successor readiness, or Jail alias consumer rollout proof, preventing
  preserved `worker-N` names from deadlocking replacement creation. That
  rollover engages only when the checkpointed handoff journal actually covered
  worker NodeSets; a login-only or ownership-only handoff proceeds without
  fabricating a worker-binding requirement, while any journaled worker handoff
  still demands one exact target-bound binding per worker NodeSet. When that
  same immutable-child handoff activates target-only SConfig compatibility,
  the post-handoff mode refresh now immediately runs the exact slot-bound
  writer rebind and pulse before consumer verification can release the writer
  fence, instead of depending on the stale pre-handoff bridge mode. After an
  exact typed-GRES worker Pod rollover, GPU post-activation now re-runs the
  full target-lineage and runtime release gate against the verified successor
  UIDs before selecting a smoke-test worker, while rejecting partial or
  foreign Pod generations. GPU jail-init replay now also accepts the exact
  no-change ConfigMap lineage in which the post-Jail rebind inherits the
  previously verified manager-restore successor without consuming a new Helm
  revision, but only when both rebind epochs, target and ConfigMap identities,
  full and compatible digests, directive transition, manager generation, and
  lifecycle timestamps form one continuous proof. Direct post-Jail successors
  still require the immediately following Helm revision. GPU topology pause
  recovery now reasserts only the exact checkpointed `UP`/`DOWN` pair before
  drift validation when a later controller reconfiguration cleanly restores
  the pre-pause partition record. Its inert-controller replay also accepts the
  producer's fully verified terminal zero-drain journal when a prior recovery
  intent remains recorded, while continuing to reject incomplete worker or
  partition evidence. Active bridge service replay now uses that same sealed
  controller-gap contract as its canonical target-manager pause authority,
  instead of requiring an optional bridge-level source-era pause journal that
  legacy-to-modern transitions do not produce. Rolling compute recovery also
  checkpoints the existing bridge-client propagation proof immediately after
  that fence and before any worker RPC or provider replay, so the strict
  controller-gap runtime consumer cannot outrun its proof producer when an
  already-advanced bridge stage skips source-era reconciliation. A cold target
  MUNGE handoff now atomically stages the byte-exact target-only `clustername`
  marker without a trailing newline while both bridge controllers are stopped,
  preserving hash-bound preimage evidence but removing the source SlurmDBD
  numeric ID so the restarted primary must adopt and verify the target
  SlurmDBD-assigned ID. Repeated exact authentication retries now retain an
  immutable, census-bound origin Pod generation separately from the latest
  pre-stop generation, allowing controller-gap recovery to verify the full
  same-StatefulSet successor chain without weakening Pod ownership, placement,
  or runtime-exclusivity checks.
- Stabilize shared managed/external protected-state approvals by excluding only
  the volatile leading `scontrol show config` observation timestamp from the
  protected digest while retaining its raw command-audit hash. External
  terminal verification now also recognizes the exact checkpoint-owned login
  LoadBalancer allocation annotation transition only when the live target UID,
  persisted decision, expected annotation set, current captured hash, and
  baseline hash with only those annotation keys removed all match; other
  SlurmCluster drift still requires remediation approval.
- Preserve exact external transition authorization through terminal bridge
  cleanup. The final protected-state verifier now derives a hash-sealed
  historical proof from the validated source bridge, manager pause, client
  propagation, target singleton, cleanup, and Helm journals, so only the exact
  generated Slurm ConfigMap and canonical Helm history Secret data/label
  deltas remain command-owned after the live bridge is gone. Final worker
  health now treats `DRAIN` as non-serving and may clear only an
  identity-bound, zero-allocation, empty-queue stale topology drain carrying
  Slurm's post-controller-gap `Not responding` annotation. The recovery writes
  durable intent before `RESUME`, verifies the exact current Pod/workload and
  registration postcondition, and rejects customer drain reasons, new Pod or
  runtime identities, active jobs, allocations, and replay drift.
- Keep a completed external login handoff terminal during replay after bridge
  cleanup. Target Pod and Service-route refresh no longer checkpoints an
  invalid `complete -> target-ready` downgrade. Checkpoint loading can recover
  only that exact interrupted state when the bridge is already cleaned, no SSH
  sessions ever existed, the full journal validates after changing only the
  derived state, and the revalidation timestamp is later than cleanup.
- Checkpoint the exact UID/spec/generation-bound Soperator manager restore
  before final slot-aware Helm reconciliation in the shared managed/external
  singleton handoff. Crash recovery now accepts an already-restored manager
  only through the matching immutable Helm intent, deployed proof, target UID,
  freshly rendered manager contract, manager manifest from the proof's exact
  Helm revision, and single expected generation successor. Final Helm dispatch
  now requires the canonical manager pause, and a terminal verified restore is
  replay-safe; live replica equality, missing proof, revision drift, and
  generation jumps still fail closed.
- Replace broad protected-state mode waivers in managed and external Soperator
  upgrades with one exact proof contract. Command-owned deltas now bind command,
  cluster, campaign/checkpoint, phase, operation, baseline, resource identity,
  and full before/after digests one-to-one under ordinary
  `--execute --approve`. Unexpected drift requires a separately checkpointed
  whole-comparison fingerprint before `--approve-remediation` can consume it;
  fresh or changed drift fails with a new plan, and blocked deltas remain
  non-overrideable. Both flows now recapture protected state after their last
  cluster mutation and record a terminal verification marker.
- Retry target Soperator Helm dependency preparation only for bounded transient
  download failures. Gateway errors, timeouts, connection resets, and
  equivalent repository transport failures now receive two backoff retries
  before the checkpointed phase stops; deterministic chart or lock errors
  still fail immediately.
- Reassert bridge-owned controller exclusivity before every in-place service or
  provider replay. While source- or target-bridge HA is authoritative, cxcli
  now revalidates the exact phase-owned Soperator manager pause and inert
  target-controller command gate before any Slurm RPC, accounting sample, or
  provider-operation reconciliation. An out-of-band manager scale-up is
  compare-and-swap paused and checkpointed before accounting continuity can be
  accepted, including when an existing worker provider operation would
  otherwise enter its reuse path. Once target-native controller cleanup has a
  durable post-provider checkpoint, resumes validate every terminal node-group
  replacement and continue only the manager, runtime, and source-cleanup tail.
  Cross-segment replay binds the current phase pause to the bridge by immutable
  manager UID and original replicas while validating the current non-replica
  Deployment spec live, rather than rejecting a completed segment's legitimate
  manager-spec successor.
- Preserve verified GPU worker topology across Kubernetes-only campaign
  segments. Before any later-segment worker provider operation, cxcli selects
  one unique completed, target-UID-bound topology replay, compares only its
  effective CPU/socket/core/thread/parameter/typed-GRES fields rather than Pod
  provenance, and checkpoints that inheritance in the current segment. If
  stored Helm values lost those fields, cxcli accepts only the exact static
  NodeSet topology delta, keeps the target controller command-gated and every
  checkpoint-owned partition paused, and applies one journaled Helm successor.
  A partition-pause reset after controller reconciliation is now written
  through the exact Slurm route that produced its preceding compare-and-swap
  observation, so a login client cannot update the inert target while
  verification reads the authoritative bridge. If Helm's bounded
  admission-webhook startup retry consumes a failed revision, resume accepts
  the later deployed revision only from the exact contiguous, timestamp-bound,
  same-values retry history.
  The restore now also advances the authoritative source bridge: it derives a
  topology-only `slurm.conf` successor from the checkpointed bridge preimage,
  preserves non-worker directives, pins checkpoint-owned partitions `DOWN`,
  and journals the exact ConfigMap/material/workload/Pod binding before atomic
  shared-Jail staging. Bridge reconfigure is bracketed by partition-pause
  reassertion, downstream handoff digest gates accept only the recorded
  successor, and a worker that does not naturally re-register may receive one
  checkpointed in-place slurmd `SIGHUP` bound to its unchanged Pod and container
  identity. No worker Pod recreation is used.
  Worker migration remains blocked until the target NodeSet/Pod lineage is
  Ready and every Slurm node has the exact topology, typed GRES, zero
  allocation, no `INVALID_REG`, and no unowned drain reason.
- Recover the exact target SlurmCluster binding before final Helm
  reconciliation in later campaign segments. When the current segment has a
  fresh rolling-compute journal, cxcli now validates the completed
  campaign-level source-to-target transition against the live target and
  durably copies that namespace/name/UID authority into the segment journal
  before recording any Helm mutation intent. The matching-values and
  mutation-required paths share this accessor; conflicting singleton,
  checkpoint, transition, or live identities still fail closed.
- Close the final singleton partition-reset gap before serial worker runtime
  reload. Singleton activation can reload the canonical `State=UP` definition
  after the earlier runtime pause; cxcli now checkpoints a reload-specific
  reassertion intent, accepts only the exact saved `UP` or owned `DOWN` record,
  restores and proves `DOWN`, and binds the proof to the target authority epoch
  and final config digest before any UID-bound worker deletion. The existing
  fresh per-worker zero-allocation and second `DOWN` read remain mandatory.
- Make controller continuity evidence match the transition that actually ran.
  A target MUNGE handoff whose effective Secret bytes and accounting marker
  already match now updates only UID provenance under CAS and keeps both HA
  controllers serving. If a real Secret-byte change requires the cold path,
  cxcli preserves an already valid same-target accounting marker, including
  its numeric accounting ID, byte-for-byte instead of rewriting logical
  identity. The final bridge fence performs its full all-node process census
  before scale-to-zero, then reuses the bound Node identities with exact
  bridge-node runtime fences and fresh API absence checks. The singleton starts
  with its final one-host config and original timeout values, avoiding a second
  shared-Jail rewrite after service restoration. Safety reports record
  executor-owned controller outage boundaries and do not label an otherwise
  healthy maintenance transition zero-downtime.
- Keep the cluster-visible execution lease through a transient Kubernetes
  `etcdserver: leader changed` response. Lease renewal now retries that exact
  holder-preserving API failover with the existing bounded backoff, while a
  failed holder compare-and-swap or resource conflict still aborts
  immediately.
- Fence every live source-manager authority before a controller bridge
  cutover. The bridge now pauses the exact current
  `deployment/soperator-manager` in addition to deployments selected by the
  locked legacy profile, so a same-version manager cannot recreate
  `controller-0` after its StatefulSet is scaled to zero. An interruption
  before the first bridge write continues through the existing UID-bound
  source-Pod successor recovery, which invalidates and rebuilds the pre-copy
  and fence proofs.
- Close the in-place dual-SlurmCluster ownership handoff before rolling
  migration can be marked complete. The executor now removes only the exact
  captured old Deployment dependents after independent target capacity is
  Ready, repeats source-child cleanup, proves a target-only live
  SlurmCluster inventory, and checkpoints one structured completion proof
  bound to retirement, immutable-child cleanup, target UID/generation, and
  target availability. Final health, source-fence removal, report refresh,
  and campaign-segment advancement consume that proof instead of trusting a
  standalone `source_cleanup_completed_at` timestamp. A completed in-place
  phase missing the proof re-enters this narrow cleanup boundary rather than
  replaying provider rollout or weakening the final-health guard. Later
  Kubernetes-only segments reuse the campaign-level transition only after its
  completed origin, cleanup-proof fingerprint, exact source/target identities,
  and live target UID are revalidated; they do not recreate forbidden
  segment-local cleanup state.
- Replace resume-time Helm timestamp inference with an immutable
  intent/proof/provenance chain. Exact unchanged values may reuse only one
  target-UID-bound historical apply proof; the known no-op window is
  classified and retired without manufacturing an apply. A real final-values
  change checkpoints its complete chart, values, rendered manifest, and target
  intent before Helm runs. Source-retirement fence rebinds now repeat the live
  source-absence and Slurm-health proof. Once exact final Helm provenance is
  selected, cxcli also proves the completed gate/ungate journal and fills only
  missing controller command-gate-owned fields with target UID/resourceVersion
  preconditions before proving the full live spec. A resumed alternate
  singleton adoption may establish that gate through its exact rearm, Pod
  lineage, workload, target-start, ungate, and restored-admission proof instead
  of requiring a synthetic historical timestamp. Present values that differ
  from the manifest block instead of being overwritten, and a validated no-op
  recovery no longer strands removed probes behind a missing synthetic apply
  status. Target workers rotate one at a time
  only while every affected partition remains at its exact checkpoint-owned
  `DOWN` record and a fresh node-scoped query proves zero active allocations;
  replacement is UID/resourceVersion-preconditioned. Manager restoration uses
  one canonical pause journal with UID, resourceVersion, generation, Helm-spec,
  rollout, and availability checks, and the current controller authority must
  equal its latest durable history entry. Native controller HA also binds both
  distinct Node UIDs and the locked OCI index/platform runtime digest set.
- Make final bridge-to-singleton client propagation an operational runtime
  boundary, not only a jailed-file hash check. After the final one-host config
  is proven on every exact login and worker Pod, cxcli durably binds the target
  authority epoch, config digest, worker Pod/container identities, current Pod
  IPs, and Kubernetes instance IDs before recreating one UID-bound worker Pod
  at a time. Each successor must retain its exact workload and node ownership,
  load the final config, and return to a serving runtime identity before the
  next worker can rotate; Pod drift or non-serving postconditions remain
  pending.
- Harden resumable in-place service rollouts across provider-owned replacement
  boundaries. The controller command gate now accepts a cross-node Pod
  successor only when the completed controller node-group operation proves the
  exact replacement UID, Ready node, role labels, and target Kubernetes
  version. Accounting continuity similarly accepts only the target cluster's
  empty-to-active registration endpoint transition after that completed
  controller rollout and the verified dedicated bridge rebind; RPC identity,
  legacy registrations, catalogs, history, writer ownership, and PVC identity
  remain immutable.
- Preserve the exact jailed controller command environment and restage target
  MUNGE/config material before controller-gap bridge readiness is trusted.
  Resume also recognizes checkpointed source or target ConfigMap digests,
  recovers exact GPU topology zero/subset-drain states, and completes
  service-provider drain Pod deletion only through UID-bound durable intent.
- Keep completed worker groups on the post-provider resume path. Their live
  provider target, replacement Node UIDs, restored strategy, GPU release, and
  Slurm runtime identities are revalidated without replaying source-retirement
  alias or epilog gates against replacement nodes. When the target controller
  remains deliberately inert, refresh the exact bridge authorization after
  service replay and immediately before the final worker-runtime RPC.
- Recover target-compatibility resume when the restored Soperator manager has
  already rewritten the Slurm ConfigMap from the compatible variant to the
  checkpointed full-target variant before the later Jail-boundary recovery
  runs. Generic revalidation now promotes that state only with the exact
  ConfigMap UID and data digests plus the complete checkpointed manager
  pause/restore generation and spec-fingerprint cycle; unknown drift remains
  blocked.
- Recover the exact one-generation target SlurmCluster successor produced when
  the checkpointed accounting command fence is restored before a later active
  SConfig compatibility revalidation. Resume requires the same target UID,
  restored/enabled accounting resource version, original writer command
  identity, ready accounting owner, source-retirement proof, size-zero state,
  and a live spec that normalizes exactly to the prior fence; unrelated spec
  drift remains blocking even when status updates have advanced resourceVersion.
- Classify an already-propagated full-target login Jail config before the
  slot-bound writer-refresh checkpoint only when active resume has just sealed
  the exact accounting restore rebind and every captured worker Pod has a
  durable deletion plus target-owned replacement proof. The login digest must
  equal the checkpointed full-target digest; unknown content remains blocked.
- Recover an in-place service-role provider drain when the exact managed Pod
  named by the provider's latest `Draining` event has already been recreated on the
  same machine. Recovery is limited to login, accounting, controller, and
  system groups, requires an alternate Ready node plus exact Pod, owner, Node,
  machine, and node-group identity, journals intent before mutation, applies a
  campaign-bound `NoSchedule` fence, and deletes only the bound Pod UID and
  resource version without force. Retries do not repeat a delete for the same
  Pod UID, and the temporary fence is removed after provider completion or
  treated as clean when the replaced Node is already gone. Historical drain
  events are ignored after the provider advances to deletion or creation, and
  an unscheduled replacement Pod does not authorize a recovery mutation.
- Rebind the post-OpenMetrics topology proof across the exact one-generation
  controller command-gate rearm performed while the target manager is
  durably paused. The successor requires the checkpointed pre-patch resource
  version, target UID, canonical gate fingerprint, restored admission window,
  prior Helm/spec proof, and exact live inert gate; unrelated spec drift stays
  blocked. Retries bind that historical CAS version to the durable rearm proof
  instead of a later SConfig health check's refreshed live resource version.
- Reuse the complete exact `DOWN` partition journal while the paused-manager
  target controller command gate is intentionally inert. This retry-only gap
  now requires the target UID, canonical gate fingerprint, restored admission
  window, manager pause proof, target-HA bridge authority, and complete bridge
  partition proof. Continuous worker, rolling handoff, Jail refresh, and the
  controller-bridge replay all share that exact gap proof. The already-verified
  GPU topology drain/registration proof also reuses its exact worker and target
  partition fingerprints in this window. The pre-activation GPU-smoke worker
  drain release now requires the matching rolling/bridge pause inventories and
  verified target-partition fingerprint instead of issuing another impossible
  live RPC. Partition snapshots carry an operation label and fail immediately
  if a controller-gated caller misses checkpoint reuse, including when the
  exact reusable proof is incomplete or drifted. GPU post-activation replay
  also refuses direct node/temporary-partition RPCs in that state. Explicit
  post-activation reads require a fully validated bridge journal at singleton
  handoff or later. Ordinary states still require live Slurm RPC revalidation.
  The pre-controller-roll client handoff also binds every current login and
  worker Pod to the exact bridge-host config under a fingerprint of the full
  gate, manager-pause, bridge-authority, and partition-pause proof instead of
  issuing impossible ping/config RPCs. Final singleton client propagation still
  requires fresh live RPC verification after activation.
  The controller-gap identity is now a sealed semantic v2 binding over stable
  pause, manager, target-workload, admission, and bridge-authority fields;
  replay-only timestamps and resource versions cannot invalidate it. Gated Pod
  replacement uses an explicit exact successor lineage rather than entering the
  digest. Runtime recovery rejects provisional proofs, and checkpoints using the
  former mutable v1 fingerprint fail fast with instructions to start a fresh
  campaign; no compatibility migration is provided.
  If the active-slot handoff leaves the temporary bridge mounted on its
  pre-slot Jail root, cxcli proves the two immutable bridge Pods are not
  running, observes the same exact jailed-config digest from both bridge
  nodes, and accepts only a checkpointed target-config preimage. It then
  journals the ConfigMap successor against the immutable UID, source UID,
  predecessor material digest, and exact config key before atomically restaging
  the already verified bridge-host config. Resume reuses only that accepted
  successor and exact Jail staging record, then requires the same StatefulSet
  plus fresh live bridge/client proof before any service-role provider update.
  If target clients already use a different MUNGE identity, the existing
  stopped-HA authentication handoff now runs at this boundary before that live
  proof; its exact Pod replacement lineage remains reusable on resume.
  The pre-rollout worker runtime-identity gate now follows the same invariant:
  during the inert window it requires the exact current worker Pod/instance
  bindings, complete target partition pauses, verified all-worker GPU drain
  observations, and the completed post-activation GPU proof instead of issuing
  an impossible `scontrol show nodes`. Phase 7 must then reconcile and freshly
  verify every worker runtime identity immediately after native controller
  activation before it can complete.
  Discovery and source-backup fingerprint refresh now remain deferred through
  the same active target-controller gate, preventing post-mutation source
  observations from invalidating the campaign's immutable pre-mutation backup.
  If an earlier refresh already replaced that report, only a fully verified
  identity recovery from the immutable backup may reconstruct source evidence,
  and the operation journal retains its original report fingerprint. Segment
  projection also restores the immutable onboarding migration-profile id beside
  its accepted source/target versions, so an intermediate target generation
  cannot replace the still-running legacy-to-target execution contract.
- Fence canonical target Slurm partitions created by target-chart replay even
  when every source-era partition name is retired. GPU topology recovery now
  checkpoints the full target `UP -> DOWN` record before mutation, mirrors it
  into the Jail and rolling journals, and verifies the exact live `DOWN`
  fingerprint before releasing worker drains or running smoke checks.
- Recognize the exact stale typed-GRES registration drain left behind after the
  live worker topology already matches the replayed target values. cxcli now
  resumes that verified `IDLE+CLOUD+DRAIN` worker under the target partition
  fence instead of treating the old count/topology reason as unknown drift.
- Treat the exact generic-GRES `IDLE+CLOUD+DRAIN+INVALID_REG` sibling as a stale
  worker process when its typed target config and immutable workload identity
  are independently proven. The bounded rollover can now restart that Pod and
  verify a distinct Ready successor instead of requiring the stale state to
  include `DOWN`. Its exact verified UID transition is mirrored into the
  rolling journal before immutable-child handoff rebinds the successor, and
  downstream driver-loader validation follows that same proof chain.
- Run the generic post-Jail live `sbatch` smoke through a checkpointed,
  root-only temporary partition bound to one verified dispatchable worker while
  production partitions remain `DOWN`. Pre-fix pending attempts submitted to a
  paused production partition are cancelled, reconciled by exact job identity,
  archived, and retried once through the temporary partition.
- Refresh the canonical SConfig bridge mode after same-call immutable-child
  ownership handoff. Slot-B consumer verification now records the required
  non-writer fenced proof when that handoff activates target compatibility,
  instead of persisting a premature generic `verified` state that cannot
  authorize writer release on resume.
- Defer final accounting registration/history verification when the same
  executor call advances `imported-paused` through target-writer restoration
  to `target-enabled` while the active-slot SConfig pulse is still pending.
  cxcli now returns to immutable-child handoff before attempting controller-backed
  `sacctmgr`, preventing a circular wait on an empty Jail `/etc/slurm`.
- Rebind a prepared SConfig zero fence across the exact one-generation target
  SlurmDBD command-fence restoration. The new proof requires matching enabled
  writer/restored command resourceVersions, the exact ready target Deployment,
  unchanged Jail/login contracts, and reconstruction of the prior full spec;
  unrelated drift remains blocking.
- Keep the continuous Slurm scheduling pause resume-safe during the exact
  in-place immutable-child boundary. When the compatible shared-Jail client
  config has replaced the bridge-only config before target children exist,
  cxcli now reuses the complete job-free, exact `DOWN` partition journal
  instead of querying the intentionally unavailable source login/controller
  route. This includes a fully checkpointed one-generation manager restore;
  every other target-HA state still requires live RPC revalidation.
- Admit the one-way target worker StatefulSet successor that can be created
  after immutable source children are orphaned but before its target binding is
  checkpointed. Resume now requires the new UID, post-preparation creation
  timestamp, exact target selector, and exact NodeSet owner. Chart 4.x NodeSets
  that retain the Helm release label are bound through the exact target
  SlurmCluster reference, shared non-empty Helm provenance, bounded creation
  time, and active Jail PVC; unrelated same-name workloads still fail closed.
- Recover an exact newer Helm revision that reasserts the Soperator manager
  during the post-retirement SConfig writer-fence window. cxcli now proves the
  sole Helm-owned replica generation, deployed manifest, target successor, and
  unchanged manager UID/spec before checkpointing and applying an exact
  re-pause. A companion target-spec refresh is rebound only when its UID,
  zero-size generation, release revision, manifest hash, and
  replica-independent defaulted spec reproduce that same Helm proof. The only
  accepted manifest override is an already-verified, identity-bound target
  SlurmDBD command fence from the same accounting handoff; manual or ambiguous
  drift remains blocking.
- Make the post-manager accounting selector handoff establish its exact
  disabled target-writer gate when the earlier manager-paused path recorded
  only the command fence. The ownerless source Deployment UID and selector are
  now durably bound before immutable selector replacement instead of failing on
  a missing gate.
- Retire the exact ownerless source accounting ReplicaSet lineage before final
  smoke validation. Cleanup now requires verified source retirement and an
  exact Ready target writer, journals ReplicaSet and non-Ready Pod UIDs before
  UID/resourceVersion-preconditioned deletion, and refuses Ready, re-owned, or
  identity-drifted source workloads.
- Suspend legacy parent Flux Kustomizations with an atomic UID-bound spec patch
  that tolerates unrelated Flux status/resource-version churn. External
  Soperator upgrades no longer stop before controller fencing when the Flux
  controller updates status between cxcli's read and suspend patch.
- Rebind a controller-bridge source `JailedConfig` when only its resource
  version advances at the pre-authority boundary while its immutable UID,
  ConfigMap UID, name, key, and path remain unchanged. Status projection no
  longer invalidates a recorded configuration intent; ConfigMap writes retain
  their separate UID/resource-version compare-and-set fence.
- Validate create and deployments-root external-Soperator tenant/project scope
  from the readable project's immutable parent id. Project-scoped credentials
  no longer need otherwise-unused tenant-level `get` permission before
  onboarding an existing cluster.
- Keep standalone external-Soperator discovery's temporary MK8s kubeconfig
  alive through Helm, Slurm, and accounting collection. Generated-context
  discovery bundles no longer record false `context does not exist` failures
  after the Kubernetes snapshot succeeds.
- Replace the external Soperator upgrade runtime with the v6-only campaign,
  operation-journal, and report contracts. Discovery and execution now share
  one command-start observation, discovery and campaign compilation use one
  canonical schedule, one immutable campaign-owned backup protects every locked
  segment, and an accepted provider operation remains attached to its exact
  operation ID until terminal success or an authentication, lease, or operator
  interruption. The backup is source-observation-bound, mutation begins only
  after fresh narrow SlurmCluster/PVC identity reads, delayed provider
  postconditions remain attached, and the accepted migration-profile execution
  contract is embedded in the campaign so resumes never reinterpret mutable
  installed policy. A missing or corrupt campaign archive now requires the
  dedicated `--approve-backup-recovery` approval: cxcli may finish only the
  independently proven current segment, blocks every later segment until a
  fully verified replacement is atomically activated, and supports
  replacement-only repair after a clean final degraded completion without
  rerunning upgrade phases. Recovery no longer relabels an invalid reused
  binding as protected; binding/activation writes are lease-held, exact-checkpoint
  compare-and-set operations, activation conflicts remain fail-closed, and
  restore verification rejects unsafe ownership/permissions or archives that
  exceed member, expansion, or temporary-disk bounds before extraction.
- Record cumulative active external Soperator upgrade time in the canonical
  Markdown and JSON reports. The resume-safe campaign timing includes every
  approved execute invocation, formats the total as `hh:mm:ss`, and excludes
  offline gaps between resumptions so latency analysis reflects command work.
- Keep the controller-bridge source configuration inside one execute attempt
  while Kubernetes projects the accepted `slurm.conf`. The executor now polls
  the mounted file read-only for a bounded interval before `scontrol
  reconfigure`, avoiding a redundant full discovery/resume cycle without
  weakening the exact-content gate. Resume also accepts the resulting
  same-identity `JailedConfig` resource-version advance at the pre-authority
  boundary while the source ConfigMap write keeps its exact compare-and-set.
- Bind the live target SlurmCluster name and UID at the authorized Jail-refresh
  creation boundary before staging the in-place login surge. This removes a
  redundant pending/resume cycle while retaining the immutable source/target
  identity fence used by rolling compute migration.
- Resample positive login SSH socket counts before blocking a guarded mutation,
  while returning immediately on a zero sample. Short Kubernetes TCP readiness
  connections no longer masquerade as persistent user sessions; real sessions
  that remain present across the bounded samples continue to block.
- Resume the exact in-place Jail pre-ownership boundary after a checkpointed
  login hold was released before immutable-child inventory capture. Recovery
  now accepts only an exact zero-session release journal whose original login
  Pods remain Ready, restart-free, UID-stable, and source-StatefulSet-owned.
- Recover an interrupted target Helm `pending-*` revision only when its chart,
  application version, and stored-values fingerprint match the immutable
  pre-apply intent. Recovery reads Helm history because an ordinary Helm list
  can continue to expose the preceding deployed revision while the newer
  revision is pending. cxcli checkpoints the exact revision before clearing
  its Helm Secret. If resume-derived values changed after that never-deployed
  intent, cxcli archives the retired intent and checkpoints one new immutable
  intent before returning through the normal login-guarded replay path.
- Retry an accounting SlurmDBD command-fence compare-and-set up to three times
  without delay when only the SlurmCluster resource version changed. Every
  retry re-reads the exact UID-bound resource and revalidates the checkpointed
  enabled/command/args identity, so controller status churn no longer forces a
  full command resume and any real writer drift still fails closed. Resume
  validation now also accepts the source-first v2 `fence-intent` crash
  boundaries emitted by that same durable state machine.
- Raise the default in-place Soperator worker dispatch width from eight groups
  to 32 and the explicit maximum to 64. Full-group `max_unavailable: all`
  remains the per-group provider bound; the wider client dispatch removes the
  previous eight-group wave for 1,000-node layouts that require at least ten
  provider node groups, while provider-side replacement concurrency remains
  provider-controlled.
- Replace the crowded combined Soperator Jail handoff diagram with separate
  in-place and blue-green overall upgrade workflows. Each diagram now shows the
  controller bridge, login continuity, mode-specific compute ordering, Jail
  activation, validation, rollback retention, and final cleanup boundaries.
- Split controller-bridge HA and login-node continuity into separate diagrams
  with editable SVG sources and matching PNGs. The controller view owns
  singleton/bridge authority and partition-restore ordering; the login view
  owns stable Service routing, exact source holds, target readiness, and
  explicit protected-SSH exit disposition.
- Refresh the Jail rootfs storage diagram and add an editable SVG source. The
  updated view distinguishes managed and external slot paths; automatic,
  discovered, and explicitly declared persistent overlays; passive-populate
  isolation; rolling consumer rebinding; and the data-current limit of
  legacy-rootfs rollback.
- Document all five current architecture diagrams once in both the README and
  design guide, with a concise explanation, one rendered PNG, and one editable
  SVG source link per diagram.
- Fix completed external Soperator replay so it reconciles exact current Slurm
  worker runtime identities before fresh discovery and evaluates final health
  against the checkpoint-proven target SlurmCluster UID after source-to-target
  handoff. A healthy completed campaign no longer fails against the retired
  source UID.
- Fix external first-adoption SConfig writer fencing while the target Soperator
  manager and its validating webhook are intentionally paused. cxcli now opens
  a checkpointed, exact-identity `failurePolicy: Ignore` window only for the
  UID/resourceVersion-fenced target size patch and restores `Fail` before the
  manager resumes.
- Fix first-adoption resume when immutable inventory captured a Ready login Pod
  after the SSH hold was already released. A pod name that never participated
  in any hold is now accepted by its exact post-release captured UID, while
  held or replaced pod names still require their release lineage.
- Retire the separately installed legacy Flux-owned MariaDB operator and its
  exact empty auxiliary namespace during
  completed external Soperator Helm reconciliation. Deletion requires the exact
  legacy namespace, release/chart family, and Flux ownership labels; resource
  pruning is scoped by release namespace/name, preserves shared CRDs and
  unrelated same-name releases, verifies the target chart afterward, and
  refreshes the final report.
- Preserve accepted Jail/rootfs product versions in the external upgrade JSON
  report while continuing to reject arbitrary checkpoint strings from version
  fields.
- Clarify the external Soperator discovery spinner and command documentation
  that bounded provider, Kubernetes, Slurm, accounting, GPU, and Helm probes
  are serial and can take several minutes.
- Fix fresh-install external onboarding so a no-Soperator discovery locks a
  schema-valid v6 campaign against the live provider Kubernetes version and
  current catalog Jail identity. Campaignless or stale accepted targets now
  fail with the canonical `recovery-required` and no-conversion guidance.

- Preserve existing bound Jail PersistentVolume immutable fields during external Soperator final-values reconciliation by using Helm's client-side three-way merge path instead of Helm 4 server-side apply.

- Fix final external controller takeover for a retained target configured as
  Slurm `backup2`. Dispatch the explicit indexed takeover, prove runtime
  authority with the only-UP-host census plus bounded active-controller config
  and job RPCs instead of the static `scontrol ping` role label, and retry the
  locked image status only while the exact Pod UID is unchanged. A replacement
  retry Pod can supersede prior image and takeover bindings only after the old
  Pod and node have an exact verified runtime fence; prior proofs are archived.
  Accept the kubelet's runtime-resolved image ID only when it matches either the
  checkpointed OCI index digest or that lock's exact linux/amd64 platform digest,
  since conforming runtimes may report either immutable representation.
- Prevent finite in-place worker drains from being prolonged by Deployment
  controllers that recreate an evicted Pod on the same cordoned provider group.
  Before dispatch, checkpoint each affected Deployment UID and exact original
  affinity, require a redundant Ready replica outside the rolling group, add a
  resource-version-guarded temporary node-affinity exclusion, and restore the
  exact original affinity after terminal provider replacement. Unsafe
  single-replica placement and identity or affinity drift fail closed.
- Add fail-closed SDK authentication through `CXCLI_NEBIUS_DELEGATE_ID`: cxcli obtains the impersonated IAM
  token internally through the selected Nebius CLI profile and never falls back
  to the base identity when explicit impersonation cannot be established.
- Validate cleaned controller state against the checkpointed target
  `StateSaveLocation`, not the temporary bridge mount path, and make resumed
  bridge retirement faster by using exact Namespace absence for its namespaced
  children while continuing to prove every cluster-scoped resource and provider
  node group individually.
- Give every campaign segment its own checkpoint-bound controller authority
  epoch and token-bound local state-transfer artifacts, preventing later
  Kubernetes segments from reusing or colliding with an earlier segment's
  immutable pre-copy, cold-delta, manifest, backup, or promoted-state paths.
  Replace a retained `/shared/current` link only through a checkpointed exact
  campaign-epoch preimage so the next segment can promote its isolated state
  without accepting unrelated shared-filesystem drift.
  Reuse the already validated live substrate objects for the bridge security
  snapshot instead of issuing a second serial GET for every resource.
- Pause and bind a live `deployment/soperator-manager` before fencing the source
  controller in later Kubernetes-only campaign segments, even when the locked
  source profile has no legacy manager selector, and reuse that exact bridge-owned
  pause until the durable target-singleton handoff restores the manager.
- Stage the exact source bridge host configuration into the shared Jail through
  the existing digest-bound CAS stager before the first bridge writer scale. If
  an older executor already accepted an unstaged scale, stop its exact StatefulSet,
  prove runtime absence, stage the file, and restart the same workload. Reuse the
  first full all-node runtime census across the intervening authority-Lease-only
  transition, with fresh API absence plus node-UID and security revalidation,
  instead of repeating every inspector Pod before writer scale. Defer the outer
  discovery refresh during source-fenced, state-promoted, and target-transition
  cold-stop gaps so six serial login-side Slurm commands do not each consume their
  timeout while the checkpoint already proves that no controller should answer.
  Reuse the phase handler's fresh all-node process census in the immediately
  following fast verification and revalidate only the live Lease, workload, Pod,
  image, role, and API-exclusivity contracts there. During controller-gap resume,
  accept only the exact checkpointed source-writer recovery shape, including its
  workload UID, scale transition, timestamps, and source fence. Observe an
  unstaged shared-Jail configuration through two ready canaries on distinct
  bridge nodes, bind that exact preimage before repair, and reconcile a previously
  failed stager only against that proof. Treat terminal stager Pods as immediate
  failures instead of consuming the full readiness timeout.
- Reuse the immutable completed-segment Jail slot handoff evidence during later
  Kubernetes-only in-place segments instead of requiring a nonexistent current-
  segment Jail phase. Before the first provider rollout, bind every Ready
  worker Pod UID, Pod IP, and Kubernetes instance identity, reconcile Slurm
  `NodeAddr` to the current Ready Pod IP that dynamic `slurmd` registers through
  the serving bridge, resume only a corrected node that still carries
  `NOT_RESPONDING`, and verify the exact live identities before Slurm-clear
  gates. At a later segment boundary, bind the
  fresh provider resource version only after the immutable reservation and
  failure-domain identity plus the prior segment's exact node-template target
  are revalidated. The topology-only Helm successor may also materialize the
  canonical inert-controller command, args, and probes into release storage
  when that same gate is already proven live; missing or identical gate fields
  are accepted, while custom commands, probes, replicas, and unrelated values
  remain blocking drift.
- Collapse controller process-census preexistence, terminal-state polling, and
  post-delete absence checks into bulk inspector-Pod observations. Exact
  per-node UID, node placement, manifest, log, CRI process binding, and final
  Kubernetes-node identity proofs remain unchanged, while avoiding three
  linear sets of authenticated `kubectl` reads on larger clusters.
- Fixed controller-bridge retirement validation so checkpoint slot `0` remains the
  canonical `"0"` provider label instead of being misread as missing.
- Allow the exact bridge-owned PriorityClass through the UID-preconditioned cleanup
  transport while continuing to reject unrelated PriorityClass names.
- Preserve the immutable source SlurmCluster/SSH binding when discovery has advanced
  to the target identity after completed source retirement, while rejecting any
  target UID drift.

- Reconcile a checkpointed external controller bridge from target-version HA
  through the validated target-singleton handoff at old-resource retirement,
  after the phase-9 safety and action-journal gates pass, so a phase-bounded
  resume cannot deadlock before phases 10-12, and revalidate target-version HA
  roles without replaying the superseded source-version takeover proof. Bind the
  pre-fence client propagation proof to the same service-qualified target
  controller host list written into the handoff configuration, and preserve
  completed predecessor proofs while that checkpointed handoff mutation is
  in progress, including suppressing pre-phase compute-cutover replay until
  phase 10 resolves the boundary. Before JWT preflight or bridge fencing,
  pause and bind the exact target Soperator manager, reassert the
  checkpoint-owned canonical inert controller command gate across the target
  SlurmCluster, owned controller workload, and live Pod when target
  reconciliation has restored the ordinary command, and restore the manager
  only after the target-singleton handoff is durable. Bind the live gate proof
  to exact `controller-0` workload ownership so controller placeholder Pods do
  not enter the singleton gate census, and keep the two serving bridge hosts
  before the inert future target in the pre-fence host order. Apply and verify
  that same ordered three-host contract on both target-version bridge daemons
  before proving client RPC or fencing the bridge. Because those daemons read
  the shared Jail PVC rather than a projected ConfigMap, atomically stage the
  exact ConfigMap payload into the Jail and accept its live preimage only when
  the digest is bound by the checkpointed bridge or phase-7 compatibility proof.
  Recognize Slurm's numbered `backup1` role for the serving bridge standby while
  the configured future target is still reported as the DOWN `backup2`, and
  preserve the original jailed-file preimage across resume after staging succeeds.
  Seal the final slot-B Helm reconciliation as an exact release, values,
  manifest, live-spec, and target-UID proof, and let the post-OpenMetrics
  topology journal consume it only as a fingerprint-bound one-way successor of
  the earlier replay so phase-10 resume remains fail-closed without deadlocking
  on cxcli's own later Helm generation.
  Before target client RPC, bind the new target MUNGE Secret by UID and content
  fingerprint, cold-stop both exact HA writers, prove runtime absence, apply the
  Secret under CAS without checkpointing its bytes, and restart/revalidate HA.
  During that checkpointed controller gap, defer the ordinary discovery Slurm
  RPC refresh and reuse the bound discovery bundle so recovery cannot deadlock
  on the controller outage it is responsible for repairing. Before bridge
  fencing, bind the target-named JWT Secret by UID, copy only the checkpointed
  source HS256 data key under resource-version CAS without recording its bytes,
  and prove the target Secret mount, configured key path, and decoded hash while
  the exact target controller command gate remains active. Start the retained
  target singleton only through an exact workload-UID and resource-version CAS
  from zero to one replica, and make resume accept only that journaled scale.
  If takeover recovery encounters a partially Ready two-replica bridge, fence
  its exact Pods through a journaled two-to-zero CAS before atomically restoring
  the checkpoint-bound bridge configuration into the shared Jail and restarting
  either writer. Keep the configured controller image in the JWT continuity
  binding while recording each Pod's runtime `imageID` as observation evidence,
  because a recreated Pod can report the locked platform-manifest digest instead
  of the gated Pod's runtime-local digest; target startup still enforces the
  exact campaign-locked platform digest independently. Finalize a validated
  bridge in the segment that owns it, restoring the exact scheduling pause and
  deleting its temporary resources before that segment completes even when the
  locked campaign has later Kubernetes-only segments. Bind that cleanup health
  gate to the current segment's Kubernetes and node-group targets, treat an
  omitted provider outdated count as zero only for an otherwise terminal group,
  and match the journaled target SlurmCluster UID after completed source cleanup.
  Cleanup also retains the durable completed Jail fast-verification event when
  its current replay failure describes the intentional singleton successor
  topology; unrelated stale or failed phase verifications remain blocking.
  Recognize a NodeSet-owned passive worker successor during resumed source
  cleanup only when its live NodeSet owner UID and workload UID both match the
  active/passive release gate and immutable-child handoff.
  Treat a checkpoint-completed, expected `SKIP` as terminal for cleanup gates,
  run the final MK8s and Helm proofs once after any resume-time phase mutation
  instead of duplicating them during completed-action reconciliation, and bind
  cleanup's live target `StateSaveLocation` to its exact non-bridge PVC/PV even
  when the target and temporary bridge containers use different mount paths.
  Parse canonical indexed and service-addressed `SlurmctldHost` directives while
  still requiring exactly one final `controller-0` identity.
- Treat post-handoff reconciliation of the retired external accounting ConfigMap as intentional only when the target accounting StatefulSet is live and its successor ConfigMap exactly preserves the protected pre-upgrade contract.
- Revalidate completed external controller-bridge groups against their immutable checkpointed Kubernetes version and provider ID after the main control plane has advanced.

- Accept target worker Pod successors created by a completed external in-place node-group
  rollout only when the exact target StatefulSet owner, replacement provider-node UID,
  Ready zero-restart runtime, active Jail/rootfs and persistent mounts, and passed GPU
  workload gate all reproduce the durable completion proof.
- Accept the coherent GPU driver/library contract installed into the active Jail by a
  completed external in-place worker rollout only when every exact replacement Pod,
  workload UID, provider-node UID, zero-restart init guard, rootfs and persistent mount,
  and prior passed fleet-wide GPU gate reproduce the same promoted contract.
- Accept the external Jail login `/home` mount after cutover when `findmnt` reports the
  exact `jail[/home]` subpath at `/mnt/jail/home`, while continuing to reject unrelated
  mount-source changes.
- Classify the legacy external accounting default ConfigMap update as intentional only
  when its complete per-key hash contract exactly matches the target MariaDB successor;
  mismatched or missing successors remain remediation-approval failures.
- Revalidate completed temporary controller bridge node groups against their immutable
  checkpointed Kubernetes version and provider ID after the source control plane advances,
  while retaining source-derived validation for new bridge slots.
- Accept a completed GPU Jail post-population checkpoint after its local script evolves only
  when the post-switch ConfigMap, Job, Pod, logs, legacy marker, UIDs, and last-applied
  manifests still reproduce the exact durable historical proof, including a zero-Pod
  garbage-collected result state with no replacement Pod and exact checkpointed evidence.

- Allow external Soperator cutover validation to accept the target chart's canonical renamed headless login Service successor while preserving exact identity checks for client-facing login Services.
- Allow source cleanup to follow a recreated child only when its exact unchanged checkpointed non-Slurm controller remains captured in the source ownership chain.
- Revalidate completed controller-bridge scheduling pause against the durable target partition handoff, preserving exact target `DOWN` records while accepting only source-era partitions already proven retired by the GPU scheduling gate.
- Journal exact source child-controller deletion intents and perform a bounded post-controller inventory sweep so controller-recreated ownerless descendants are removed without weakening target ownership checks.
- Recover a missing controller-bridge target NetworkPolicy peer journal entry and its interrupted hash transition only when the durable target cluster-name transition, canonical pre-patch hash, and exact checkpointed live policy UID and target-peer semantics prove the already-applied handoff.
- Restore historically completed predecessor phases from their durable pre-boundary completion evidence while a downstream writer boundary is active, and revalidate the protected controller bridge security contract without replaying obsolete intermediate topology checks.

- Fixed source-child retirement when controller status updates continuously race
  the UID/resourceVersion-preconditioned delete. Cleanup repeatedly re-enumerates
  and reclassifies the complete child graph, accepts only status-only changes on
  the exact source UID and ownership contract, and after bounded conflicts uses
  the immutable UID precondition alone; material or ambiguous drift remains
  rejected.
- Fixed source-retirement replay when a target controller creates an exact
  target-owned successor at a checkpointed source-child API path. Cleanup now
  preserves the replacement only after exact target SlurmCluster ownership is
  proven; source-owned or ambiguous UID replacements remain fail-closed.
- Fixed completed in-place login handoff validation after its provider node
  group reaches the target Kubernetes version. The target-ready binding now
  advances from the locked source minor only with the exact completed provider
  operation, unchanged StatefulSet/node-group/image identity, and replacement
  node proof. The handoff and nested target-Pod binding advance atomically;
  arbitrary binding drift remains rejected.
- Fixed protected-login revalidation after a completed handoff when the target
  controller rolls a login Pod. Resume accepts and journals the new Pod UID
  only under the same target StatefulSet, after every original session is
  durably complete, with preserved SSH host keys, an exact released hold, and
  a zero-restart replacement; all other successor drift remains fail-closed.
- Fixed phase-9 external in-place ConfigMap replay after the final controller
  gate removal. Resume now accepts only the journaled one-generation
  SlurmCluster successor with the exact prior Helm proof, unchanged generated
  configuration, verified native-controller cleanup, and a restored fail-closed
  admission webhook; repeat resumes reuse that exact successor proof.
- Fixed completed external in-place cutover reconciliation to recognize the
  promoted target handoff Helm-values revision. A phase-9 resume no longer
  replays the superseded command-gated pre-cutover intent after the final
  controller cutover has intentionally removed that gate.
- Fixed verified accounting-handoff revalidation after a controller-managed
  MariaDB Pod rollover. Resume now accepts a new Pod UID only when the exact
  SlurmCluster, MariaDB, StatefulSet, and PVC identities remain unchanged and
  the writer, history, registration, and accounting checks still pass; the
  verified UID transition is retained in the checkpoint.
- Fixed external in-place GPU post-activation validation to align DCGM with the
  live driver generation before diagnostics. Driver 580 and newer require the
  exact CUDA 13 DCGM package/plugin pair; older supported drivers retain the
  CUDA 12 path. The selected package version and plugin directory are
  checkpointed with the health result.
- Fixed root-only H100 smoke ordering during external in-place worker resume.
  cxcli now proves canonical target partitions remain exactly `DOWN`, records
  source-era partitions omitted by the target configuration as retired, and
  only then compare-and-set releases its own worker drain. Final restoration
  reopens surviving target partitions without recreating retired source ones.
- Fixed the same retirement boundary when the final Helm replay removes a
  source-era partition after the GPU gate observed it exactly `DOWN`. Resume
  now derives retirement from the canonical target configuration and repairs
  only an unfinished restore plan whose surviving partitions still match the
  saved `DOWN` or pre-pause fingerprints; unknown drift remains fail-closed.
- Fixed cross-midnight recovery of an `Indeterminate` cxcli H100 smoke. The
  exact-name `sacct` reconciliation is now bounded by the checkpointed smoke
  preparation time, so one uniquely terminal failed/cancelled job with no
  active queue entry can be journaled and retried once; uncertain dispatches
  remain fail-closed.
- Fixed terminal external worker-provider resumes to continue at the worker
  health boundary instead of replaying stale pre-dispatch workload admission.
  Accounting continuity now treats job-history counters as monotonic while
  keeping cluster, catalog, registration, database, StatefulSet, and PVC
  identities exact.
- Fixed external in-place Slurm drain ownership checks to normalize Slurm's
  display-only `[user@timestamp]` reason suffix before exact comparison. The
  worker provider update can now dispatch while any other reason drift remains
  fail-closed.
- Fixed external in-place GPU worker drain classification so completed proof
  Jobs, Node-owned NVIDIA runtime Pods, PDB-safe ReplicaSet Pods, and the
  expected worker StatefulSet GPU runtime mounts no longer make every finite
  worker drain impossible. Unknown workloads and unrecognized live hostPath
  state still block before provider mutation.
- Fixed external in-place node-group size validation for Nebius fixed-capacity
  autoscaling groups whose minimum and maximum counts are equal. These groups
  now retain their accepted capacity during provider drift checks instead of
  being misread as one-node fixed groups before mutation.
- Added a distinct protected-login continuation authorization for confirmed
  involuntary SSH timeouts. Managed and external `soperator jobs` now accept
  `--authorize-login-timeout-continuation <fingerprint>` only for the exact
  absent-socket epoch, record a timeout-specific disposition, and still reject
  live or reappeared sockets. Voluntary exits continue to use
  `--acknowledge-login-exit`; neither path forces a disconnect.
- Fixed worker Pod resume after the checkpointed GPU sysfs Helm repair. The
  rollover binding advances only through the verified old/new Pod UID pair and
  exact target StatefulSet identity, preserves the prior UID in audit history,
  and continues to fail closed on unjournaled Pod replacement.
- Fixed protected-login revalidation after the immutable child handoff replaces
  a retired source Pod with a same-name target Pod. Resume now recognizes only
  a checkpoint-bound source UID, target StatefulSet UID, target Pod UID, and
  zero-session hold release; it still requires the original socket's explicit
  fingerprint-bound exit acknowledgement before declaring continuity complete.
- Fixed GPU worker activation after Jail slot handoff. The chart-owned worker
  init guard and cxcli release probe now run jailed `nvidia-smi -L` with the
  Jail dynamic loader and libraries while preserving the GPU-requesting
  container's live device namespace; the shared rootfs intentionally contains
  no persistent `/dev/nvidia*` nodes, so chrooting into it crash-looped valid
  replacement workers. A resumed handoff now recognizes only that exact failed
  old init contract on checkpoint-bound target Pods, journals a same-version
  Helm repair with unchanged stored values, and proves the single new revision,
  target StatefulSet identity, loader contract, and replacement Pod UIDs before
  worker readiness continues. Resume reuses that verified proof after the same
  replacement UIDs are bound into the worker rollover checkpoint. Live Pod
  validation also accepts Kubernetes' canonical string serialization of the
  exact `nvidia.com/gpu: 1` extended-resource limit and the standard read-only
  `kube-api-access-*` projected mount that Kubernetes injects beside the two
  chart-owned mounts. Any other extra or non-canonical mount remains rejected.
  Full post-activation health-checker execution derives its platform tag from
  the pinned worker's live GPU product/count, matching the chart check-runner
  convention instead of assuming its job-only environment is inherited by an
  arbitrary container exec. The post-activation gate now invokes the exact
  Jail-installed `/usr/local/bin/health-checker` through the activated Jail
  login environment, where live worker devices and the Jail CUDA, DCGM, NCCL,
  and package metadata are visible, instead of depending on the outer `slurmd`
  container `PATH`. The gate now also requires the chart-owned read-only host
  sysfs projection at `/sys-host/bus/pci`, preserving mandatory PCI and
  InfiniBand checks instead of accepting an otherwise healthy GPU-only result.
  Existing in-progress target releases may add that mount through one
  checkpointed same-version Helm repair that binds the chart content, manifest,
  stored values, release revision, target StatefulSet UIDs, and before/after Pod
  UIDs. Resume requires exactly the next revision and unchanged values, then
  reuses the verified proof without replaying Helm; drift remains pending.
  The post-activation job gate now derives the candidate Slurm node from the
  exact checkpoint-bound worker Pod name and requires `scontrol show node` to
  return that same single `NodeName`; it no longer assumes the job-only
  `SLURMD_NODENAME` environment variable exists in an arbitrary container exec.
  If phase 6 subsequently
  applies its checkpointed OpenMetrics restore, resume seals and reuses only
  that exact next Helm revision after validating its time window, final values,
  rendered target spec, and installed loader contract. The released SConfig
  writer may then expose the manager-generated full config from that revision;
  cxcli binds its exact ConfigMap UID, target UID/generation, full-data and
  compatibility fingerprints, directive delta, and Helm proof once, then
  requires those semantic fingerprints on every retry instead of comparing it
  to the superseded pre-restore ConfigMap digest. Resume also promotes the
  exact `consumers-verified-with-sconfig-released` crash state back to verified
  when all consumer/mount checks and the released writer proof still match,
  avoiding a false OpenMetrics ordering failure.
- Fixed target Soperator reconciliation when the retired source leaves the
  globally named inert `controller-placeholder` DaemonSet with its immutable
  source selector. After exact source retirement, cxcli now requires the
  checkpointed DaemonSet UID, ownerless source selector, fully Ready inert Pod
  set, and allowlisted sleep-only legacy or target placeholder spec; it records
  a durable UID/resourceVersion/spec delete intent, deletes with `Foreground`
  propagation, and accepts only the target-owned inert replacement with the
  exact target selector. Arbitrary controller workloads remain fail-closed.
- Fixed legacy-rootfs Slurm health verification after immutable login child
  replacement. Once the handoff journal binds the new target StatefulSet UID,
  owner, selector, and creation boundary, cxcli uses the exact Ready
  target-owned login backend instead of continuing to require the retired
  source workload UID; an unjournaled third UID still fails closed.
- Fixed target login startup on a freshly populated GPU Jail slot when the
  target image still waits for the legacy empty `gpu_libs_installed.flag`.
  After the stronger driver-version, library-hash, linker-cache, and jailed
  `nvidia-smi` evidence passes, cxcli now runs a checkpoint-bound exact-PVC Job
  that creates only that zero-length compatibility marker. Existing nonempty,
  symlinked, or otherwise unsafe markers fail closed; GPU libraries are not
  copied or rewritten by this compatibility step.
- Fixed target-compatibility resume after its manager pause/config pulse/writer
  zero/manager restore cycle had completed but the final login digest had not
  yet checkpointed the stage active. cxcli now reuses only an exact bounded
  pause-to-restore generation chain with matching manager spec fingerprints and
  completed config/writer proofs instead of trying to pause the restored
  manager a second time.
- Fixed the legacy-rootfs handoff after that restore cycle. Manager
  reconciliation may restore the full target ConfigMap while the zero-replica
  SConfig writer intentionally leaves the shared Jail on its compatible digest;
  cxcli now validates those two exact states independently. Before waiting for
  target worker readiness, cxcli also checkpoints and UID/resourceVersion-deletes
  only the captured ownerless legacy worker Pods that block the target
  OpenKruise StatefulSet names, then accepts only replacements owned by the
  checkpointed target StatefulSet UID. Once every old worker Pod is proven
  absent, resume may checkpoint the exact full-target login-Jail digest produced
  by target login startup; it continues to reject that transition while any
  legacy worker can still consume the shared rootfs.
- Reduced external in-place upgrade pre-fence latency without weakening mutation
  proofs. One execute attempt now reuses only checkpoint-proven read-only Slurm
  routes, the `preserve` policy reuses its first post-pause all-job snapshot,
  status-only Slurm RPCs defer during the immutable Jail mutation boundary, and
  rootfs consumers share one Kubernetes snapshot. Independent source-fence API
  inventory lists now use eight bounded parallel readers while preserving
  deterministic validation order. Mutating Slurm commands, lease checks, and
  final writer/readiness proofs remain fresh.
- Reduced source-retirement latency when the legacy Soperator `orphan` finalizer
  stalls. UID-preconditioned source deletion now gets a 30-second grace before
  entering the existing complete-inventory, immutable-identity, admission-window
  recovery instead of waiting five minutes before the same proof. Resume also
  accepts the exact post-retirement `children-prepared` zero-writer boundary only
  with the complete child, login-selector, controller-spool, source-retirement,
  and positive restore-intent journal.
- Fixed retired-source accounting takeover when the globally named Deployment
  still has the immutable source selector. After both writers are command-fenced
  and source retirement is durable, cxcli checkpoints an exact
  UID/resourceVersion delete intent, orphan-preserves the old ReplicaSet/Pod/PVC,
  waits for the target-owned fenced replacement, and completes this selector
  handoff before target schema bootstrap. Resume accepts only the exact
  checkpointed target UID/selector after the writer and retirement gates have
  advanced from the deleted source Deployment.
- Fixed accounting schema bootstrap after later cxcli-owned Helm reconciliation.
  Bootstrap now reads and fingerprint-verifies the checkpointed historical Helm
  revision that proved the target image/spec, while requiring the deployed
  release revision not to regress and separately revalidating the live target
  CR, image, command fence, Deployment, Pod, and runtime image identity.
- Fixed fresh empty accounting database initialization. The SlurmDBD update
  check must return `1` only for a freshly observed zero-table database and `0`
  for every nonempty accepted schema; the isolated bootstrap rechecks that exact
  expected code before starting target SlurmDBD. Any nonempty schema requiring
  conversion remains fail-closed.
- Fixed isolated accounting schema bootstrap startup by preserving the
  target-rendered `DbdHost` identity. The temporary daemon now changes only its
  loopback bind address, private port, PID file, and log file; substituting
  `localhost` for the container's valid SlurmDBD host identity made the daemon
  exit before local readiness.
- Fixed isolated SlurmDBD JWT initialization while the production accounting
  command is fenced. The bootstrap creates an ephemeral owner-only HS256 key in
  its private temporary directory and rewrites only `AuthAltParameters` to that
  key; cleanup removes it with the other transient bootstrap files.
- Fixed login-session protection reinstallation after immutable-child manager
  restoration. Exact ownerless login Pods remain admissible after the journal's
  monotonic `children-prepared` to `manager-restored` transition only when their
  captured UID, target selector, handoff token, readiness, and sshd identity
  still match. The protected socket/host-key revalidation path now carries that
  same checkpoint authority instead of independently requiring a live workload
  ownerReference.
- Fixed the deletion fence for those exact ownerless login Pods. OpenKruise
  `PodUnavailableBudget` protects controller-owned Pods only, so cxcli now adds
  a campaign-scoped `ValidatingAdmissionPolicy` bound to the checkpointed
  namespace, Pod name, UID, and hold label. Upgrade proceeds only after CEL
  type-checking is clean and server dry-run attributes the denial to that exact
  policy; release removes the Pod label before precondition-deleting the binding
  and policy. Source-retirement gates now forward the durable checkpoint writer,
  and a missing writer fails before any PUB, Pod-metadata, or policy cleanup.
  Jail resume now accepts the exact zero-session `release-intent` crash boundary
  while retaining held-Pod identity and zero-restart checks; intermediate Helm
  and pre-ownership gates no longer require already-removed hold metadata.
- Fixed in-place login-surge resume when the active SSH session moves to a
  different original login Pod after an earlier hold was released. Protected
  UID rotation now accepts only exact release identities from the canonical
  hold's current or archived release journal instead of treating archived proof
  as an unproven Pod loss.
- Added `ext-soperator upgrade --stop-after-phase <phase-id>` for a deliberate,
  checkpointed maintenance pause after a planned execution phase without
  changing the accepted campaign or entering the next phase.
- Fixed accounting/Jail ordering after target writer enable. Interrupted
  `target-enabling` resumes finish the idempotent writer restore, while
  registration/history verification at `target-enabled` remains deferred until
  the target SConfig compatibility pulse has materialized and verified the
  active rootfs slot. The completed selector handoff now revalidates the exact
  target Deployment UID, owner, selector, `Recreate` strategy, and enabled
  writer instead of requiring the earlier disabled-writer state. The
  already-retired source cleanup boundary now reuses its
  exact UID/resourceVersion proof instead of requiring premature registration
  verification.
- Fixed the prepared SConfig zero-writer fence after target-manager restore.
  The one expected cxcli Helm reconciliation may advance the target
  `SlurmCluster` generation and normalize its spec; cxcli now rebinds only after
  a fresh deployed-release, live-CRD-default, chart/app, desired values plus
  only the exact legacy-client OpenMetrics and zero-size writer-fence gates,
  source retirement,
  zero-writer, target UID, and login-mount proof. Arbitrary spec
  drift remains blocked.
- Fixed false accounting network-probe cleanup failures when a terminated
  listener remains briefly as an unreaped zombie under the container init
  process. Cleanup still binds a live process to the exact operation marker,
  but treats zombie/dead states as stopped and separately proves the bootstrap
  port is no longer listening.
- Fixed deny-ingress NetworkPolicy resume when the Kubernetes API omits the
  serialized empty `ingress` list. Missing and empty ingress are normalized
  only for the exact `policyTypes: [Ingress]` policy and exact Pod selector;
  match expressions, additional policy types, and ingress allow rules still
  fail closed.
- Fixed external first-adoption Jail Upgrade image selection. Passive-slot
  population now uses the immutable active campaign segment's exact target
  rootfs image and never silently reuses the discovered legacy populate-jail
  image.
- Fixed external target-Helm source-fence resume after the target chart creates
  its worker `NodeSet`. The fence now checkpoints the source `NodeSet` UID set
  at initial capture and reuses that immutable set on every resume, so mixed
  source/target discovery cannot misclassify new target children as source
  closure drift.

- Strip the admission-computed Pod priority and preemption policy from mirrored
  controller specs before assigning the temporary bridge PriorityClass,
  preventing live bridge writer Pods from being rejected when the source and
  bridge class values differ.

- Exclude Kubernetes-managed service-account projection material such as
  `kube-root-ca.crt` from controller-bridge mirroring, so a newly created bridge
  namespace can retain its system-managed trust bundle without failing campaign
  ownership checks or changing the accepted source fingerprint.

- Made `ext-soperator onboard` validate an existing `config.yaml` against the
  current runtime schema before live discovery or campaign acceptance. This
  prevents onboarding from writing a new campaign beside an invalid stale
  target that `ext-soperator upgrade` cannot reload under the shared lease.
- Fixed external controller-bridge job capture so each partial, durable
  preservation record remains a valid resumable journal while the remaining
  active-job lineages are still being captured. The aggregate `captured_at`
  proof remains mandatory when capture becomes complete.
- Applied Kubernetes Namespace objects before their dependent namespaced
  resources in multi-object upgrade manifests. Fresh controller-bridge
  substrate creation no longer fails partway with `Namespace NotFound`.
- Unified managed and external Soperator upgrade execution around the canonical
  controller-authority and Slurm action journals. Managed execution now uses
  the fixed controller/system placement domains, stages the target singleton at
  zero, crosses source bridge, target bridge, singleton handoff, partition
  restore, and bridge cleanup before completion, and preserves provider-owned
  domains and mount substrate during cleanup. Both modes retain scheduling and
  ActiveChecks fencing after the first bridge write until roll-forward recovery
  reaches the final gates.
- Made both in-place adapters execute service domains in the exact order
  `login -> accounting -> controller -> system` before workers. Accounting now
  binds writer, PVC, catalog, registration, and history continuity; controller
  and system rolls require healthy authority in the opposite fixed domain and
  rebind only the replaced domain's Node UIDs. Managed workers are dispatched
  in clear batches of at most 64, resolve zero-surge `all` to each group's
  exact fixed size, acquire an exact cxcli-owned per-group Slurm drain before
  provider dispatch, and leave job-bearing groups pending and untouched. Drain
  acquisition and restoration persist the exact preimage before mutation;
  interrupted intents adopt only a proven live postcondition and never resend
  an uncertain Slurm RPC.
  Exact held-job compensation now completes before scheduling, ActiveChecks,
  or bridge cleanup can be restored.
- Changed managed `soperator upgrade` to require exactly one of `--dry-run` or
  `--execute`; mutation requires `--execute --approve`. Removed the managed
  node-group scope, timeout-based login-session policies, and unsupported-path
  override. The end-to-end worker policy now uses
  `--node-group-strategy`, `--zero-surge-max-unavailable`,
  `--strategy-max-surge-count`, `--worker-drain-timeout`, and
  `--max-parallel-worker-groups` (default 32, maximum 64). Standalone
  `upgrade node-template` keeps its existing interface.
- Added `soperator jobs` alongside `ext-soperator jobs` for the shared durable,
  authority-aware Slurm action journal and exact
  `--acknowledge-login-exit` workflow. Managed and external upgrades now protect
  established SSH sessions indefinitely until voluntary exit is observed and
  acknowledged; neither exposes a force/timeout compatibility path.
- Made managed `soperator upgrade --dry-run` refresh live discovery into a
  temporary bundle and validate exact cluster identity plus the fixed bridge
  substrate without creating a campaign checkpoint or mutation. Managed
  controller/system provider rolls now require a post-terminal live Node UID
  rebind for only the journaled domain while preserving its group and storage
  identities.
- Added the canonical managed Soperator continuity substrate. Production
  profiles now keep controller at two fixed nodes and system at three, disable
  autoscaling for both, attach Jail and controller-spool storage to both, and
  label them as distinct node-group scheduling domains. Managed upgrade
  discovery records stable group IDs and Ready Node UIDs and fails before
  mutation when the live cluster has not been reconciled; it never falls back
  to creating provider bridge capacity. The shared bridge model now has
  explicit managed-existing and external-temporary placement-domain adapters,
  managed Kubernetes-only cleanup policy, and one authority configuration
  composer that preserves customer config outside cxcli's documented overlay.
- Added managed upgrade checkpoint schema v2 with a durable controller-authority
  journal, first-write roll-forward boundary, placement/storage/configuration
  fingerprints, continuity evidence, provider-roll domain, Jail population
  state, and explicit lifecycle stages. Incompatible unfinished checkpoints
  fail with source-restore versus roll-forward remediation instead of using a
  compatibility shim.
- Added a fail-closed active/passive populate-jail monitor. The upgrade wait now
  binds the exact Job and controller-owned Pod UIDs, checkpoints Pod/container
  state, warning Events, bounded log digests, and structured file/byte counters,
  and anchors its aggregate timeout to the immutable Job timestamp across CLI
  restarts. The TUI exposes that checkpointed substage. Transient observation
  failures retry the same Job, while identity drift, terminal failure, and
  timeout or a bounded non-progressing `stalled` state leave the Job and passive
  PVC untouched instead of restarting a potentially partial rootfs writer. The
  Job also receives the contract's Kubernetes `activeDeadlineSeconds`.
- Fixed controller command-gate resume after a checkpointed Jail Helm values
  transition. The bridge now seals only the canonical `slurmctld` command,
  args, and disabled probes that it owns instead of the entire values document.
  Existing full-values seals migrate only when they still match the current
  values or the exact verified target Helm apply intent and proof; unproven
  drift and any command-gate contract change remain recovery-required.
- Fixed interrupted first-adoption Jail resume across sequential released
  login holds. A captured login Pod may now be corroborated by the latest exact
  zero-session release, a complete archived zero-session release for the exact
  Pod UID, or by an earlier verified replacement transition ending at its
  current Pod UID under the unchanged StatefulSet UID. Unjournaled or further
  UID changes still fail before Jail write or source retirement.
- Allowed the legacy-rootfs zero-writer bridge to recognize the exact
  checkpointed controller-recovery payload produced while restoring late held
  jobs. All non-`slurm.conf` files must remain source-legacy-safe, while the
  replacement config digest, source preimage, job/partition semantics, and two
  Ready recovery controllers must agree across the jailed-config repair and
  job-state recovery journals. Old-client pre-health is deferred only until
  the canonical rewrite, and full post-write Slurm health remains mandatory.
- Prevented late queued or held Slurm jobs from disappearing during an external
  controller major-version handoff. Immediately before the cold stop, cxcli now
  captures every live job and later verifies its identity, partition, and
  restart lineage. Target bridge configuration retains source-only partitions
  as non-default `DOWN` compatibility partitions, so target `slurmctld` can
  restore queued state even when the target chart renamed its active partition.
  Resume revalidates an exact recovered epoch and the recovered job semantics
  without incorrectly requiring the live, controller-written `job_state` file
  to remain byte-identical to its immutable recovery seed.
- Aligned external-upgrade progress with the durable handoff journal. Jail
  status now names the active immutable-child substage, and the interactive
  spinner refresh is limited to one frame per second to avoid excessive PTY
  redraw output while preserving live status updates.
- Made external-upgrade Kubernetes Lease renewal resilient to bounded transient
  MK8s exec-credential, API timeout, TLS, connection, and rate-limit failures.
  Renewal retries remain inside the 120-second lease window, while holder-test
  failures and conflicts still fail immediately so lost ownership is never
  concealed.
- Minimized and made explicit the required Slurm controller RPC gap during an
  external bridge major-version transition. The expensive all-node controller
  process census now completes while the source bridge remains 2/2 Ready; the
  cold interval retains only exact bridge-node runtime fences, API absence, and
  Node-identity revalidation. Resume reuses the complete checkpointed partition
  pause before any live inventory, worker, or job-control call while that cold
  gap is active, including the Jail ownership handoff's continuous scheduling
  guard. Target authority transfer and writer scale reuse the pre-stop host
  census with fresh Node-identity, API-workload-absence, and exact two-node
  runtime fences instead of launching two more all-node host censuses after
  controllers stop. Status no longer issues doomed Slurm probes or
  treats stale cold-stop timestamps as a current outage after an exact source
  bridge recovery. The TUI explains that running jobs continue while direct
  submit/cancel/requeue/hold RPCs are briefly unavailable and durable actions
  remain queued. Cold-backup creation now also rejects a symlinked backup path
  and creates the missing directory before writing its preimage manifest.
- Fixed first-adoption target controller validation when the coexisting target
  SlurmCluster intentionally has a different `ClusterName` from the immutable
  source. The bridge now validates the generated name against the exact target
  Helm values, checkpoints both source and target names before target write,
  and still rejects an unrecognized third name or resume drift.
- Fixed target bridge material staging, singleton takeover, failed-takeover
  recovery, final verification, and cleanup to use only the exact
  checkpoint-bound `slurm.conf` ConfigMap key. A legitimate sibling such as
  `custom_slurm.conf` is preserved instead of being misclassified as a second
  complete Slurm configuration, while a missing or changed bound key still
  fails closed.
- Fixed repeated pre-target controller recovery after a safely restored source
  bridge has written new state. Each consumed backup restore now derives a
  fresh immutable epoch, while an unaccepted in-progress copy reuses its exact
  operation token. A failure before target material/write automatically
  restores the source-image bridge 2/2 and proves `bridge-0` primary instead of
  leaving every controller stopped.
- Renamed external-upgrade MK8s TUI generation counters from the ambiguous
  `upgraded/upgrading/remaining` labels to
  `provider-current/provider-updating/provider-outdated`. The table now makes
  clear that a ready provider generation can still run an older Kubernetes
  version than the locked campaign target.
- Made the Jail GPU post-population gate non-preempting and self-contained. It
  now runs from the immutable target controller image through the NVIDIA
  RuntimeClass without a GPU request or worker PriorityClass, atomically
  installs and hashes `nvidia-smi` plus the matching driver libraries in the
  passive rootfs, and supports UID-bound replacement of an exact failed
  pre-switch contract without accepting unrelated drift.
- Split GPU pre-activation fleet evidence from post-activation workload
  lineage. Every accepted Ready GPU Node is now checked by a node-pinned,
  non-preempting NVIDIA RuntimeClass probe without a GPU request; target
  NodeSet, AdvancedStatefulSet, and Pod UID lineage is enforced only after the
  active-slot handoff can legitimately establish it.
- Fixed active/passive Jail maintenance Jobs to use a PriorityClass only when
  accepted Soperator values explicitly configure one. cxcli no longer invents
  a target chart-owned PriorityClass name before that chart has created it, so
  first-adoption capacity probes and passive-slot population can run on old
  Soperator clusters without weakening their node-affinity contract.
- Kept the legacy-rootfs Slurm client compatibility override active during the
  passive-slot preparation Helm apply. Target `MetricsType` and plugin-path
  directives can no longer reach old login, worker, or temporary bridge clients
  before slot-B consumers have been populated and verified.
- Install and reverify the UID-bound source reconciliation admission fence
  around the earlier in-place passive-slot Helm apply. This prevents the new
  manager from recreating a fenced source controller or adding the target
  JailedConfig aggregation boundary to legacy source writers before handoff.
- Allow discovery resume from the passive-slot target-creation crash window
  only when the operation journal has a completed login gate, the immutable
  source binding matches, and the second SlurmCluster has exact target Helm
  ownership. Unbound, third, or UID-drifted identities still fail closed.

- Fix external Soperator controller recovery census probes to use the dedicated
  network-denied privileged inspector boundary required for complete host PID
  visibility, recognize only the exact same-cgroup Slurm `slurmscriptd` child
  contract without treating it as a second controller, and stop waiting
  immediately when a census Pod fails.

- Fixed controller bridge client propagation for source configurations whose
  primary `SlurmctldHost` includes an address alias. The before-fence proof now
  uses the exact accepted ordered host contract instead of reconstructing a
  lossy synthetic host list, so execution still fails closed before authority
  transfer without rejecting a valid aliased primary.
- Made controller bridge client propagation non-mutating. The proof now uses
  exact jailed-config reads plus direct `scontrol ping` and `show config` RPCs;
  it no longer sends `scontrol reconfigure` from every client, which could
  reopen paused partitions. Immediately before source fencing, cxcli also
  compare-and-set revalidates and reasserts every checkpoint-owned DOWN record.
- Added narrow recovery for an interrupted source fence that an operator rolls
  back before any bridge process or first bridge write. When only the source
  Pod UID changes on the same immutable workload, storage, configuration, and
  credentials, cxcli proves the replacement is the sole live controller. It
  then removes only the checkpoint-owned stale pre-copy or promoted snapshot,
  rebinds the replacement source Pod, and regenerates every dependent fence
  proof. Promoted-snapshot cleanup uses a durable dispatch intent. The manifest
  census recognizes only the exact inert campaign stager, bridge-mount helper,
  and legacy controller placeholder contracts, while the host process census
  still rejects any additional `slurmctld` authority.
- Fixed that exact legacy placeholder census for the two operator-emitted
  layouts encountered during supported upgrades: the munge/wait-helper layout
  and the older two-container layout. Both require the exact restartable inert
  munge sidecar and `sleep infinity` regular containers. Unknown init
  containers, extra regular containers, changed commands, and any host
  `slurmctld` process still fail closed before bridge authority starts.
- Fixed runtime controller-fence evidence collection on clusters that rapidly
  garbage-collect successful bare Pods. Inspectors now expose a file-backed
  readiness marker and remain `Running/Ready` long enough for cxcli to bind the
  exact Pod UID, Node identity, and logs before checkpointing the fence proof.
- Made controller-bridge discovery resumable after the source scale-to-zero is
  durably `dispatching`. Only that exact pre-bridge-write journal window may use
  the validated checkpointed source binding; execution still proves the same
  live workload UID at replicas zero before accepting the source fence.
- Fixed controller-spool pre-copy resume after interruption. When the immutable
  destination is still absent, cxcli now rejects a symlinked staging path, removes
  only the transfer-token-owned incomplete incoming tree and transient manifest
  sidecars, and extracts the already hash-verified archive from scratch. It no
  longer compares a newly completed archive against a stale partial extraction,
  and it does not use GNU tar's timestamp-precision-sensitive compare on the shared
  filesystem. Archive/source hashes, successful metadata-preserving extraction,
  deterministic tree manifests, and atomic destination promotion remain mandatory.
- Fixed controller-bridge source configuration to resolve exactly one active
  `JailedConfig` `/etc/slurm/slurm.conf` mapping and preserve the source
  `SlurmctldHost` token, including hostname/address aliases. Source reconfigure now
  waits for the controller to read the exact intended jailed file, requires the
  primary controller to remain UP, and revalidates the checkpointed
  partition pause, immediately reasserting an exact UP reset to DOWN while rejecting
  any unowned drift. An exact two-copy rollback before authority transfer is journaled
  and regenerated from corrected intent; partial and post-authority rollback fail closed.
- Replaced the unsupported controller-bridge availability-zone gate with the
  provider surface's enforceable isolation contract: two distinct immutable Nebius
  node-group IDs, exact live Kubernetes Node UIDs, and revalidated group membership.
  Reports and documentation now describe this as scheduling/process isolation rather
  than claiming separate physical availability zones.
- Fixed external Soperator in-place quota preflight so zero-surge no longer requests
  blue/green replacement capacity for the existing worker and service-role groups;
  safe-surge continues to check only its configured temporary worker surge.

- Added external Soperator upgrade campaign and execution-journal schema v5
  with explicit `compute_migration.mode: in-place|blue-green` and no v4
  conversion path. Interactive onboarding now confirms every applicable
  migration setting even when CLI values preselect defaults; non-interactive
  onboarding requires `--compute-migration-mode`. In-place defaults to paused
  Slurm scheduling, zero surge, full-group `max_unavailable: all`, a `10m`
  worker drain timeout, and 32 parallel clear groups (maximum 64). It uses exact
  per-group Slurm drains and a final job/epilog/identity proof before CAS
  provider updates, displays the aggregate prepared-batch unavailable-node
  impact before dispatch, keeps those drains held until replacement Nodes, `slurmd`,
  slot-B consumers, persistent mounts, and GPU runtime checks pass, restores
  only cxcli-owned drains and provider strategies, and records operator
  cancellations separately from provider disruption.
  Pause-false prompt-capable execution defaults to the durable job-action TUI
  and reclassifies groups after each accepted action; explicit preserve and
  non-interactive execution leave jobs untouched.
  Blue-green remains available with per-worker bootstrap counts. Both modes
  retain the controller bridge, login gateway, protected SSH sessions, and
  active/passive Jail slot-switch gates; serial service-role replacement now
  revalidates the accepted active-slot consumers, controller aliases, persistent
  mounts, bridge authority, and login gateway continuity after every group, with
  controllers last. The compute gate proves the checkpointed passive slot became
  active and the prior slot remains the rollback target instead of hard-coding slot B.
- Fixed pre-selection external onboarding findings and compute-layout choices to
  describe the separately accepted in-place or blue-green migration mode instead
  of incorrectly presenting blue-green as mandatory.
- Fixed post-acceptance live reobservation to preserve the complete accepted compute
  migration settings instead of incorrectly failing the interactive v5 campaign as
  though `--compute-migration-mode` had been omitted.
- Fixed the during-upgrade Slurm TUI refresh key so `r` performs a fresh live
  all-job poll and displays jobs submitted after the screen opened. Refresh no
  longer records a durable pseudo-action against only the stale displayed IDs;
  mutation actions still require exact checkpointed job lineage. A pre-bridge
  checkpoint now also opens the journal in dispatch-enabled mode while the
  connected source singleton remains authoritative; accept-only remains limited
  to actual controller handoff gaps. Deliberately closing the full-screen TUI
  with its visible `Esc` binding now returns without changing jobs, falling
  through to a stale Rich snapshot, or accepting a second action; exit is
  refused while a job action is in flight. Concurrent writers cannot persist
  two nonterminal mutations
  for one immutable job binding, and a reused JobID no longer displays an action
  result from the previous job lineage.
- Fixed controller-bridge quota preflight for current Nebius node-group payloads
  that expose the actual Kubernetes version only through `status.version`. The
  bridge now clones and revalidates that provider-reported version instead of
  treating an omitted desired `spec.version` as an incomplete source template,
  and preflight stops now bind the pending screen/report to the exact storage,
  bridge, or compute phase that needs attention. Aggregate shortages, unknown
  checks, coverage gaps, and lookup errors now produce a fresh checkpointed
  pre-mutation pending result instead of escaping as a generic error and leaving
  stale phase/report state; shared dimensions use cumulative phase attribution.
  Cloned GPU-worker quota also preserves the provider-effective reservation
  policy (`AUTO` when omitted) and resolves an attached GPU cluster to its exact
  InfiniBand fabric instead of silently checking another capacity row. It now
  fails closed when inventory and provider-platform GPU classifications differ,
  when a GPU replacement lacks an exact GPU-cluster ID, or when `STRICT` uses
  explicit reservation IDs that tenant-wide reserved capacity cannot prove.
  Early phase-input failures replace prior quota evidence with a fresh blocked
  manifest for the exact pending phase.
- Made GPU quota/capacity checks honor Capacity Advisor freshness per selected
  reservation-policy lane. `AUTO` now requires fresh reserved and on-demand
  data, `STRICT` requires fresh reserved data, and `FORBID` requires fresh
  on-demand data; stale, unknown, or unspecified selected lanes fail closed as
  unresolved instead of using their numeric availability.
- Fixed `ext-soperator jobs` rejecting an onboarded Nebius target that was
  registered by `cluster_id` without a durable `kube_context`. The TUI now uses
  the same non-persisted temporary kubeconfig handoff as the upgrade executor.
- Fixed long-running MK8s handoff commands failing on a single transient
  kubeconfig exec-credential timeout. The hidden `mk8s-token` path now retries
  one timeout with a fresh SDK client inside an explicit 28-second total budget,
  caps each exchange at eight seconds, bounds cleanup, suppresses SDK logs,
  fails permanent or empty responses immediately, and keeps failed credential
  requests off stdout so Kubernetes never receives a partial JSON document.
- Made the public Slurm job-test helper pass `sbatch --no-requeue` by default
  so preservation jobs do not inherit a cluster-level `JobRequeue=1` setting.
  `--requeue` remains the explicit opt-in for disposable requeue probes.
- Fixed the public Slurm job watcher treating a valid unallocated pending job as
  an interrupted lineage. Pending jobs now retain exact JobID, submit-time, and
  restart identity until their start time and node allocation become available.
- Fixed protected-login SSH identity capture for adopted Soperator clusters
  whose running sshd uses a non-default configuration path. The fail-closed
  probe now resolves the single listener's exact `-f` configuration, reads its
  effective `HostKey` paths through `sshd -T`, and fingerprints only the
  corresponding readable public-key files. It rejects command-line host-key
  overrides, recognizes both legacy rewritten `[listener]` titles and the
  target image's exact `sshd -D` daemon argv without that title, verifies the
  configured fingerprint set against two canonical
  loopback wire scans around session enumeration, and binds the result to the
  listener PID, start time, and command-line digest; private host-key material,
  raw offered keys, paths, and failing command output remain omitted.
- Fixed external Soperator onboarding so explicit `create-aligned-sfs` and
  `create-aligned-node-groups` choices remain authoritative instead of being
  silently rewritten when discovery reports compatible names or labels. The
  campaign preview now identifies discovered node-group IDs as immutable source
  template locks for separate blue/green replacements, and the TUI/help/README
  no longer imply that source groups are updated or reused in place.
  Kubernetes-only segments now also retain the mandatory target-version
  blue/green compute phase when no Soperator chart hop is required, and dry-run
  output reports that effective replacement requirement separately from the
  selected compute-layout mode. Compatible-placement discovery now describes
  preserved mappings on replacements instead of implying that replacement
  compute can be skipped during an upgrade. The accepted config actions are now
  the single authority for both printed and executable phase plans, so a fresh
  discovery recommendation cannot omit explicitly accepted aligned-storage or
  replacement work. Mutating v4 checkpoints that lack any accepted required
  phase fail closed instead of silently resuming an incomplete plan.
- Added an explicit post-takeover JWT continuity gate to the temporary
  controller bridge. cxcli now captures only one-way hashes plus exact source
  Secret names, UIDs, and selected data-key names, requires the canonical
  pre-fence controller command gate, and proves the exact upstream Secret
  volume, `items.path`, container mount, and configured `jwt_key` destination.
  Sidecar-only, ConfigMap-copy, other-Secret, and `subPath` references are
  rejected. The takeover now explicitly ungates a staged replicas=1 controller,
  waits for the gated Pod UID to disappear, and on first takeover and every
  resume hashes both the source mount and configured live key, validates the
  upstream bind mount, binds immutable workload/Pod/image identities, and
  obtains a short-lived token with `scontrol token` without logging it. Failure
  remains inside the rollback-protected takeover and restores the target-version
  bridge only after target runtime fencing.
- Closed the durable TUI action race at finalization. The target-singleton
  generation remains dispatch-enabled throughout the upgrade, then cxcli
  checkpoints accept-only mode, irreversibly closes new admission, drains every
  accepted action to `Applied` or `Rejected`, and freezes a separate target
  binding before partition restore. Final health is revalidated and rebound to
  that frozen target generation in the same command invocation. Any `Queued`,
  `Dispatching`, or `Indeterminate` action now blocks both restore and
  destructive bridge cleanup.
- Hardened temporary controller-bridge substrate ordering. cxcli now re-fetches
  both fixed node groups to verify their exact controller-spool SFS attachment,
  proves distinct immutable Nodes and node-group scheduling domains with a bidirectional
  PV/PVC-bound mount canary, and checkpoints that proof before advancing to
  `substrate-ready`. Canary failure leaves the source singleton authoritative
  and unfenced; source configuration, reconciliation suspension, fencing, and
  cold promotion fail closed without the durable proof. After online pre-copy,
  cxcli reruns the exact canary and provider attachment, Node UID, and scheduling-domain
  proof while the source still has one replica, then binds that fresh result to
  the source authority epoch and fence intent before accept-only mode or
  scale-to-zero. Stale proof, Node recreation, attachment loss, or scheduling-domain drift
  leaves the source singleton running and unfenced.
- Made the old-controller bridge scheduling contract deterministic. Temporary
  node templates no longer inherit source-role taints; they carry one
  bridge-only `NoSchedule` taint, and the controller StatefulSet, mount canaries,
  and spool stager all carry its exact toleration.
- Hardened final bridge storage cleanup for both the controller-state and Jail
  Retain PV/PVC pairs. cxcli binds and revalidates both exact names, UIDs,
  claimRefs, local paths, and reclaim policies, deletes both PVCs before the
  namespace and both PVs after it, and checkpoints each UID-preconditioned
  operation. A lost delete response is reconciled only from same-UID
  termination evidence; unexplained resource-version drift fails closed.
- Fixed external-upgrade partition restoration across the Slurm controller and
  blue/green worker transition. cxcli still checkpoints the complete raw
  partition records and hashes, but ownership CAS now guards an explicit set of
  customer-configurable fields plus `State`, retains unknown fields as
  fail-closed, and ignores only Slurm-derived `TotalNodes`, `TotalCPUs`, `TRES`,
  and `NodeIndices` summaries that legitimately change with target topology.
- Fixed external-upgrade finalization ordering so exact Slurm partition records
  are compare-and-set restored at a durable `partitions-restored` stage while
  the fenced controller bridge substrate remains stopped and retained. cxcli
  now permits namespace, Retain PV/PVC mapping, and temporary node-group cleanup
  only after that restore binding is durable, preventing cleanup from removing
  roll-forward recovery resources before scheduling is restored.
- Made completed external-upgrade v4 segments immutable during live
  reconciliation. If a completed segment's live postconditions drift, cxcli
  now fails closed with `recovery-required` instead of demoting phases or
  rewriting completed history; recovery requires fresh onboarding discovery
  and an explicitly accepted new campaign.
- Made uncertain upgrade-time Slurm action dispatches durably reconcilable.
  `Indeterminate` records are never resent, but later exact job/accounting
  postconditions can resolve them to `Applied` or `Rejected`; actions without a
  definitive outcome continue to block partition restoration and bridge cleanup.
- Hardened the passive-rootfs GPU post-population Job to resolve the exact
  upstream tagged PopulateJail image to one immutable `linux/amd64` OCI
  platform manifest before mutation. The v4 checkpoint binds both the index
  and platform digests, the Job runs only the platform-digest reference, and
  completion requires the result Pod `imageID` to equal that locked digest.
  OCI blob redirects drop registry credentials and the source `Host` header
  before following cross-origin signed storage URLs.
- Superseded the earlier blue-green-only external compute contract with schema
  v5's explicit in-place or blue-green choice. Existing v4 campaigns and
  journals fail closed and must be freshly onboarded; there is no compatibility
  alias or conversion shim.
- Replaced the fresh target-Helm hard stop with a fail-closed cxcli-owned source
  reconciliation fence. After bridge authority and source-manager shutdown,
  cxcli journals exact source object UIDs and allowlisted labels, installs a
  Kubernetes v1 `ValidatingAdmissionPolicy` and binding scoped only to the
  target manager service account, proves CEL type-check success and an
  attributed server-dry-run denial through temporary narrow canary RBAC, and
  then permits the normal upstream-compatible target manager. The UID-bound
  policy, binding, and canary RBAC remain through singleton handoff and are
  removed only during final validated cleanup; no raw Secret manifest is
  journaled.
- Made external protected-login ownership campaign-canonical across target and
  Jail Helm gates. Retries retain every exact source Pod, owning node, `sshd`
  container, and live socket fingerprint without a timeout. A disappeared
  socket remains `Indeterminate`; only an exact fingerprint-and-absence-epoch
  acknowledgement after confirmed voluntary exit releases the source hold.
  Interrupted release resumes from durable intent instead of silently dropping
  or recreating the guard.
- Fixed external-upgrade preflight crash-window and inventory handling before
  target Helm reconciliation. A prepared protected-session gate is no longer
  mistaken for a dispatched target release, and the second live Jail identity
  collection now retries failed PV/PVC list reads once while keeping an
  authoritative empty list fail-closed. Exhausted reads report collection
  failure instead of falsely claiming that the bound Jail PVC disappeared.
- Fixed external worker scheduling reopening between the MK8s control-plane-only
  upgrade and blue/green compute handoff. cxcli now carries exact Slurm
  partition ownership records across the GPU compatibility boundary and
  restores scheduling only after every target GPU worker passes a direct
  shared-jail runtime gate for the chart-owned init guard, all four
  `libcuda`/`libnvidia-ml` linker symlinks, non-empty resolved libraries,
  `ldconfig`, and `nvidia-smi`. The same fail-closed gate runs after a Jail
  rootfs slot switch before post-Jail scheduling resumes.
- Replaced external Soperator singleton-controller downtime approval with a
  checkpointed cxcli-owned two-controller HA bridge. The v4 journal now records
  shared-spool authority epochs, exact source/target image locks, fencing and
  takeover proofs, durable TUI actions, and fingerprint-bound login handoff.
  `ext-soperator jobs --acknowledge-login-exit <fingerprint>` records an exact
  acknowledgement only for a currently pending absence epoch; a live or
  reappeared session remains protected. The final
  chart surface remains the upstream-compatible singleton; missing bridge
  capacity or an unprovable handoff fails closed. Preflight counts two fixed
  one-node bridge groups cloned from the exact source controller template and
  attaches both the aligned controller spool and the exact source Jail SFS to
  each group from birth. A dedicated static Retain PV/PVC pair preserves the
  old controller's Jail runtime mount contract, and the pre-mutation canary
  proves bidirectional access to both shared paths. Preflight then
  proves scheduling separation with distinct immutable Nebius node-group IDs;
  writer activation additionally requires distinct provider-reported zones.
  Source, bridge, and failed-target stops now use digest-pinned host-PID runtime
  inspectors to prove zero `slurmctld` processes and writable state mounts, then
  move a campaign-bound Kubernetes Lease by UID/resourceVersion compare-and-swap
  before the next writer starts. A fresh all-node census now identifies actual
  host PIDs independently of Pod manifest names, binds permitted writers to
  exact CRI container and Pod identities, rejects mountinfo-escaped state
  markers, and revalidates Node UID/resourceVersion/provider identity
  immediately before Lease mutation and every writer start, including exact
  full-Node-set membership. Fence and census Pods now run tokenless in a
  dedicated privileged, default-deny inspector namespace; cxcli server-side
  dry-runs one Pod per exact Node before source mutation, removes partially
  admitted census Pods in `try/finally`, and deletes the exact inspector
  namespace during final cleanup. The target-version transition creates a
  metadata-aware cold backup with resumable backup/restore intents and restores
  only into a new authority epoch before any target write. Bridge resources now
  enforce the least source-compatible Pod Security level, default-deny network
  policy with exact Soperator-workload, Slurm, DNS, and single-address API
  allowances, token-off-by-default service accounts, and an allowlisted NodeSet
  power RBAC contract. Security-policy and workload fingerprints are
  revalidated before writer activation. Shareable latest and
  per-segment reports are sanitized mode-`0600` projections instead of copies of
  the private Slurm action journal. Report schema v4 now uses an allowlist-built
  projection and drops unknown/raw phase, identity, path, command, validation,
  and recovery fields by default. A completed campaign segment now requires
  complete planned-phase evidence and an archived `CLEANED` bridge proof owned
  by the target singleton; a non-mutating live-observation completion remains a
  separate, explicitly evidenced path.
- Added exact running-job preservation and full-partition inventory proofs to
  the controller bridge. cxcli now revalidates JobID lineage, allocation, start
  time, and restart count at every authority boundary, while the public smoke
  watcher tolerates brief controller visibility gaps and accepts completion
  only with the original allocation, `Restarts=0`, and `ExitCode=0:0`. External
  upgrade now defaults to the non-mutating `preserve` policy in TTY and
  automation; wait, cancel, requeue, hold, and release remain explicit actions.
  Final restoration now seals the drained pre-target TUI generation and opens a
  dispatch-enabled target-singleton generation, so job actions remain available
  throughout the upgrade without changing the restore proof. At the final
  partition boundary, admission closes and every accepted target action must be
  terminal before restore or bridge/provider cleanup. Fixed the final-health
  ordering so this target generation is created before health is bound to it
  instead of leaving a campaign permanently pending.
- Strengthened the Jail GPU activation boundary to inventory every Ready Node in
  the exact target GPU node groups, including podless spares. Occupied Nodes use
  direct worker-Pod evidence; spares use node-pinned immutable-image Jobs with
  before/after UID checks. The exact fleet and runtime proof are repeated
  immediately before the active-slot Helm switch and remain immutable on crash
  resume.
- Aligned Jail Upgrade plan, execute, and report output with the checkpointed
  rootfs handoff. First external adoption now renders `legacy-rootfs -> slot-b`
  instead of a misleading `slot-a -> slot-b`, while later refreshes report their
  actual slot-to-slot transition and handoff-verification status.
- Fixed external Soperator onboarding and upgrade leaving their cluster-visible
  Kubernetes Lease falsely active for 120 seconds after a normal command exit.
  Lease release now writes the API-required six-fractional-digit `MicroTime`,
  while the existing natural expiry remains the crash fallback.
- Replaced the Jail Upgrade workflow diagram with the current six-panel rootfs
  contract, including two controller pods, two illustrative worker pods, the
  first-adoption `legacy-rootfs` to `slot-b` handoff, persistent remounts, Jail
  alias consumers, and accounting outside the Jail slot switch.
- Aligned the active/passive Jail consumer contract with upstream Soperator
  4.0.2. Controller, login, REST, SConfigController, and every worker NodeSet
  switch through their supported rootfs bindings; cxcli no longer writes the
  unsupported dead `slurmNodes.rest.volumes.jail` value. Accounting remains
  outside rootfs convergence because SlurmDBD and MUNGE do not mount the
  operator-generated Jail volume, while its MariaDB data and external takeover
  continue through the dedicated accounting PVC and guarded SQL handoff.
- Fixed external accounting handoff without using
  `spec.slurmNodes.accounting.enabled=false`, which also removes that
  `SlurmCluster`'s MariaDB resource. cxcli now compare-and-set fences both exact
  source and target SlurmDBD containers with an inert command while leaving
  both MariaDB instances enabled. It proves the `Recreate` Deployment template,
  current and pre-fence Pod lineage, closed SlurmDBD listener, quiet database
  connections, each exact Pod -> StatefulSet -> MariaDB -> `SlurmCluster` UID
  chain, and exact bound PVC identities before sealing a private final source
  dump. A source database removed by an earlier interrupted attempt is recreated
  only from the exact retained PVC under the same atomic command fence. Import
  remains one-generation/one-SHA crash-replay safe and advances to
  `imported-paused` without restoring either writer. Fence and restore CAS
  mutations refuse to overwrite an externally changed accounting enabled value,
  command, or args. A canonical inert fence found without its preceding durable
  intent is treated as ambiguous external state and is not adopted. Sensitive
  command/args patches travel through stdin instead of process argv. The
  checkpoint stores only field-presence flags, item counts, and SHA-256
  identities; target restore re-derives the original values from the exact
  fingerprint-bound deployed Helm revision, so neither checkpoints nor reports
  contain the raw writer material. Exact verified v1 fence pairs are migrated
  together to hash-only v2 state only after the sealed dump, PVCs, live fence
  resource versions, quiet writers, and deployed target manifest are
  revalidated; mixed, malformed, or drifted checkpoint pairs fail before any
  rolling-compute mutation.
- Fixed fresh-target accounting schema initialization and SQL stdin delivery.
  cxcli now binds the Helm/CR/Deployment/Pod image and runtime image ID, verifies
  `slurmdbd -V/-u`, and uses an inert positive control, complete applicable
  NetworkPolicy-union check, plus cross-Pod/local enforcement probes before a
  bounded target-version SlurmDBD bootstrap on a non-Service port. Target-version
  `sacctmgr` creates both source and target cluster table sets before cxcli
  re-proves zero processes, listeners, connections, production writer fences,
  and immutable database identities. Partial non-transactional bootstrap state
  fails closed. `cluster_table` policy defaults and the old internal source ID
  are sealed; new IDs must be unique/in-range and the final target ID cannot
  reuse the historical source ID. Imports now require `kubectl exec -i` with
  reconnect/force disabled, an exact database completion marker, complete
  source-table inventory, and exact history before a durable applied checkpoint.
  Private dump/schema artifacts use one no-follow fd-backed byte read and exact
  size/SHA validation, and SQL stdin is frozen before dispatch intent. Missing or
  empty markers permit at most three identical reset attempts, exact remote
  completion is adopted after transport failure, mismatched markers never
  replay, and `import-reconciled` plus marker absence precede
  `imported-paused`. An older no-stdin `importing` checkpoint is adopted only
  when the target database still proves the exact untouched baseline.
- External node-template accounting-role pause no longer sets
  `spec.slurmNodes.accounting.enabled=false`, which can remove the chart-managed
  MariaDB resource. It leaves accounting enabled while the existing
  Deployment/MariaDB/StatefulSet pause and compare-and-set restore sequence
  protects the accounting node-group replacement.
- Fixed external-upgrade pending reports to replace stale phase
  `fast_verification` output with the current handler-pending reason while
  preserving detailed verification evidence produced by a verifier in the
  current attempt.
- Source retirement now requires controller, login workload, and login Services
  to have the exact target `SlurmCluster` API/name/UID controller owner, their
  configured target replica counts, exact endpoint Pods, plus target-parented Helm
  NodeSets and worker workloads owned by those exact NodeSet UIDs at the
  configured replica counts, Slurm RPC, and job-list access while both writers
  remain fenced. After the potentially long worker gate, every active
  cxcli-owned `held-observed` job is re-read through an exact target login Pod
  and must match its immutable held journal. cxcli records cleanup intent, the
  final source resourceVersion, and an exact source-child inventory, deletes
  only the exact source CR with UID plus resourceVersion preconditions and
  `Orphan` propagation, then proves source
  absence, retained source PVC/dump, and the target-only fence before restoring
  the target's original command/args exactly. Target `sacctmgr`/`sacct`, history,
  and registrations must pass before broader source workload cleanup. That
  cleanup accepts a now-ownerless child only when its API path and UID match the
  pre-retirement inventory, including every source NodeSet regardless of its
  name, and uses current per-object UID/resourceVersion
  deletion preconditions; target-owned children and the source accounting PVC
  survive stale source labels, while ambiguous ownership fails closed. After an
  accepted delete, the real Kubernetes transport waits until that exact UID is
  absent (or a different-UID replacement occupies the name) before cleanup can
  be marked complete. Every
  fence, retirement, and restore boundary is checkpointed; resume suppresses
  Helm replay and never replays SQL after `imported-paused`.
- Fixed target-applied rolling-compute resumes when the durable Slurm quiet
  result contains zero partition mutations. An explicit empty
  `slurm_paused_partitions` list now proves the completed pre-handoff check;
  a missing result still re-runs the source-side check and fails closed. When
  all Slurm CLI handoff fallbacks fail, the error now preserves the default
  login, legacy-config, and controller failure details.
- Fixed external Soperator resume when a partially applied rolling-compute
  handoff leaves both the immutable source and Helm-owned target
  `SlurmCluster` objects live. Ordinary discovery still requires one
  unambiguous `SlurmCluster`; an active v4 checkpoint may instead bind only the
  exact source/target namespace, name, and UID pair. Target UID bootstrap is
  allowed only after checkpointed Helm-apply intent and exact target chart
  ownership, is revalidated under the execution lock, and is persisted before
  backup or later mutation. Cleanup resumes accept only the bound pair or the
  bound target after source deletion, reject replacement/extra objects, and use
  Kubernetes UID preconditions for source `SlurmCluster` and `NodeSet`
  deletion. Completed handoffs are retained as campaign-level identity
  transition evidence so later segments discover the target without replaying
  source cleanup or stale rolling-compute state.
- Added an unconditional exact-source-session hold for non-destructive external
  target and Jail Helm reconciliation. cxcli keeps every protected source login
  Pod and owning node untouched while it warms an independent target endpoint.
  Missing or drifted identity evidence fails closed, socket disappearance alone
  remains `Indeterminate`, and source release waits indefinitely for an exact
  fingerprint-bound voluntary-exit acknowledgement.
- Fixed Slurm CLI continuity across external first-adoption chart/rootfs
  handoff. Target Helm values disable controller OpenMetrics while legacy-rootfs
  clients remain, preventing Slurm 24.x clients from rejecting the Slurm
  25.11-only `MetricsType` key and avoiding the observed follow-on compiled-in
  `PluginDir` failure. The pre-Jail dual-JailedConfig bridge now pauses the
  exact source SConfig writer at zero, classifies and health-checks one complete
  checkpointed Jail payload, exact-CAS reconciles target then source ConfigMaps,
  restores the source writer, and verifies every mapped file; mixed or unknown
  payloads fail closed. Before the manager resumes after source retirement,
  cxcli checkpoints and
  compare-and-set fences the target SConfig writer at size zero and proves that
  captured source writer Pods are gone, so regenerated target config cannot
  enter the shared Jail early. At the slot switch, cxcli pauses the exact
  manager, exact-CAS restores the checkpointed compatible all-file ConfigMap,
  and performs a generation-bounded target-service-account `0 -> 1 -> 0` pulse
  on the exact target-slot PVC before any consumer readiness wait. It verifies
  the target-slot digest and zero writer state, then restores the exact manager
  while the target SConfig CR desired size remains zero, before controller,
  login, REST, and workers finish moving to the target slot. cxcli then restores
  the target SConfig replica contract with
  its target service account, verifies its target-slot Pod and checkpointed full
  target config plus exact digest,
  and only then completes rootfs verification and restores the configured
  OpenMetrics value. Post-Jail `scontrol`, `sbatch --test-only`, and
  accounting/QOS checks run against that final configuration before user
  partitions reopen.
- Changed external first-adoption Jail Upgrade defaults to adopt `/home`,
  `/data`, `/scripts`, and `/models` at their existing `/mnt/jail` directories.
  Exact source/target equality is now a no-copy in-place adoption, so the
  ordinary legacy-rootfs-to-slot-b handoff does not apply the all-login and
  all-worker `maintenance=downscale` writer hold. Non-exact overlaps still fail
  closed, while explicitly relocated non-overlapping paths retain the guarded
  copy workflow and its explicit login-session policy requirement.
- Added a checkpointed in-place adoption gate before rootfs consumer switch.
  cxcli now uses a read-only host/PVC probe to reject jail-store or persistent
  path symlinks and unsafe root permissions, waits for the jail-mount DaemonSet,
  and binds the immutable UIDs of the legacy, slot, and persistent PVCs only
  while they are `Bound`. Retries re-probe and fail closed on contract, UID, or
  path drift, including live-corroborated post-switch resumes.
- Hardened persistent jail path inputs in cxcli and the Soperator Helm chart.
  Shell metacharacters and whitespace are rejected before privileged mount
  command rendering, and overlapping persistent local paths fail closed.
- Aligned the full-screen Slurm job TUI with its Rich fallback: both now show
  `User` before `State`, use `Partition`, and include `Elapsed` and `Limit` in
  the same column order. One action entered during an in-flight refresh is
  queued against the displayed job-ID snapshot: newly appearing jobs are never
  added, and a vanished snapshot never calls a stale action handler. The
  persistent TUI stays open with a no-longer-applies status; fast scheduler
  mode returns `jobs-changed`. Empty Slurm node and reason sentinels such as
  `(null)` and `N/A` now render uniformly as `-`.
- Fixed the Slurm smoke-job watcher so a temporary gap between a job leaving
  `squeue` and terminal `sacct` visibility is retried and reported as recovered
  when the same job later reaches `COMPLETED`. Interrupted terminal states,
  unresolved one-shot snapshots, and unresolved bounded watches still fail.
- Fixed zero-surge `system` node-group drains that could repeatedly evict and
  recreate the SlurmCluster-owned `sconfigcontroller` Pods on the node being
  replaced. cxcli now temporarily relocates the Deployment to the already-
  upgraded `controller` node filter without reducing replicas, gates provider
  dispatch on exact rollout convergence, and compare-and-set restores the
  original filter from its durable ownership journal.
- Fixed external Soperator accounting continuity during chart takeover. After
  affected jobs reach the quiet gate, rolling compute now pauses source
  accounting, captures a fresh `slurm_acct_db`-only dump under the
  cluster-scoped checkpoint directory with mode `0600`, imports it into the
  paused target MariaDB, preserves target schema/registration metadata,
  reconciles older source and global tables additively against a checksum-bound
  target schema snapshot, and verifies exact source job/step history plus both
  target-version `sacct` and `sacctmgr` registrations before source Slurm
  resources can be retired. Dump replay excludes Slurm's internal schema tables
  that can collide for long cluster names. The earlier restore-capable
  backup remains the DR restore point; it is not reused as the later live
  accounting transfer image. Failed MariaDB query/history probes redact SQL
  stdout and stderr from terminal, checkpoint, and report surfaces. Target
  MariaDB startup now polls for the exact deterministic Pod for a bounded
  creation window before waiting for Ready and binding its Pod/PVC identity.
  Only the exact Pod-specific Kubernetes server NotFound is retryable; other
  errors or ambiguous identity payloads fail immediately. A `source-dumped`
  resume reuses the checkpointed dump and cannot enter target import before
  that readiness gate, so waiting does not duplicate the dump or SQL import.
- Fixed explicit `create` and `ext-soperator onboard --region-id` handling so
  unsupported or empty values fail fast instead of being persisted or silently
  replaced by the default. Supported canonical ids are `eu-north1`,
  `eu-west1`, `me-west1`, `us-central1`, `eu-north2`, and `uk-south1`.
- Aligned external Soperator first-adoption guidance with the mandatory session
  preservation contract. Onboard, render, command-help, dry-run, and next-step
  output expose no managed login-policy or drain-timeout flags. When a legacy
  persistent-path relocation needs a writer hold, the campaign remains pending
  without a timeout until every protected session has a fingerprint-bound
  voluntary-exit acknowledgement.
- Fixed the external node-template phase-start screen so provider node groups
  are reported as not started before mutation instead of already upgraded, and
  use the provider status version when the list response omits the spec version.
  Serial service and worker groups without durable provider-dispatch evidence
  now remain counted as not started while earlier groups roll out, including
  when checkpoint keys differ from provider node-group names. Planned groups
  absent from a provider response are called out and keep the screen unknown,
  including the first control-plane frame before rollout metadata is persisted.
  Provider rollout events are now selected by their occurrence timestamps
  instead of their unstable API list order in both managed and external MK8s
  status tables. Serial service node groups are checkpointed as `updating`
  before provider-request state is persisted, so a hard interruption cannot
  leave a durable provider attempt misclassified as a retryable `planned` step.
- Fixed protected-state pending output to direct operators to review the JSON
  deltas and add `--approve-remediation` only when every approval-required delta
  is expected and no blocked deltas exist. Blocked deltas now direct the
  operator to repair or recover protected state instead of suggesting an
  ineffective approval flag.
- Fixed external-upgrade Slurm status counts for nodes that belong to multiple
  partitions. `sinfo -N` rows are now deduplicated by node name, with the least
  healthy duplicate state retained, instead of reporting one worker per
  node-partition pair. During a planned rolling-compute/Jail handoff, a known
  target-era config mismatch now reports status as deferred/upgrading even when
  that phase did not create a new partition-transition record because admission
  was already paused. Maintenance, performance-counter, reboot, power-up, and
  other non-serving/transitional node states now render as degraded or upgrading
  with named worker details instead of healthy/serving, and Ctrl-C during the
  best-effort queue probe is no longer swallowed before a mutating phase.
- Fixed the Jail Upgrade live signal before passive-slot population. A running
  or completed persistent migration now reports `upgrading`, while failed or
  writer-drift-stale migration evidence reports `degraded`, instead of every
  pre-population state being labeled `not-started`. Slurm status is also
  deferred/upgrading while the checkpointed Jail writer hold intentionally
  downscales Slurm workloads.
- Fixed protected-session discovery on minimal Soperator login images. The
  SSH-session probe now falls back to Linux procfs when neither `ss` nor
  `netstat` is installed and still fails closed if any source Pod cannot be
  checked. Every live socket is fingerprinted and binds the exact source Pod
  and owning node. A missing Pod or socket is not treated as drained: it remains
  `Indeterminate` until the user confirms voluntary exit through the exact
  acknowledgement. No timeout or external policy can release the hold.
- Fixed external upgrade reports so node-template summaries count both completed
  and already-current groups against the full planned service/worker inventory,
  and cleared active partition-pause fields after successful restoration.
  External node-template and rolling-compute crash resumes now merge durable
  partition restore records with the live quiet probe, so an already-`DOWN`
  partition cannot lose its checkpointed original `UP` state.
- Fixed Soperator protected-state comparison so a Slurm runtime field whose
  pre-upgrade probe was unavailable but whose post-upgrade probe succeeds is
  recorded as non-comparable audit evidence instead of false policy drift.
  Comparable partition, QOS, and association changes remain approval-gated,
  and failed post-upgrade probes remain blocking.
- Fixed external Soperator first-adoption Jail Upgrade and login continuity.
  The one-time persistent-data writer hold now checkpoints the immutable target
  SlurmCluster identity and uses Soperator's declarative
  `spec.maintenance=downscale` mode instead of directly scaling the rendered
  login workload or changing worker NodeSet replica intent. The hold remains in
  place through passive-slot population, the slot switch restores the recorded
  maintenance value, and resume fails closed on cluster replacement or foreign
  maintenance drift. Pre-copy hold drift restores steady-state writers for a
  fresh retry; drift after a completed copy marks that copy stale and blocks
  automatic completion-marker reuse until the source, shared target, markers,
  and checkpoint are reconciled. Controller-spool cleanup also defers the controller
  restart to the controlled Jail Upgrade handoff instead of deleting the
  controller Pod immediately after the cleanup Job.
  Persistent-path source probes now use unique operation-scoped Jobs without
  deleting same-name workloads. The one-time copy Job is checkpointed by exact
  contract plus PVC and Job UIDs. Schema-v2 copies now fail closed without GNU
  tar, a digest-pinned image, and delayed Job-Pod replacement; refuse sockets;
  and use a neutral same-PVC `cp -a` stage so shared-parent
  default ACLs cannot alter descendants, verify deterministic tree and root
  digests including ACLs and all xattrs, promote by same-filesystem rename, and
  atomically publish an operation-bound versioned marker. A retained schema-v1
  Job is recoverable only for its exact UID-bound, controller-owned Pod exit 19:
  a separately tokenized and UID-bound recovery Job binds one successful Pod,
  freezes probed source presence, verifies and quarantines only a log-proven
  first-entry failure without deleting the failed Job or target evidence, lets later
  untouched mounts use fresh neutral staging, then requires the full schema-v2
  comparison and post-log identity revalidation before completion. Missing,
  stale, unreadable, duplicate, foreign, or identity-drifted copy evidence
  blocks the slot switch instead of being treated as completed.
  Copy and recovery Pods now run explicitly as UID/GID 0 and retain only
  `CHOWN`, `DAC_OVERRIDE`, `DAC_READ_SEARCH`, `FOWNER`, `FSETID`, and `SETFCAP`
  after dropping all other Linux capabilities. `DAC_OVERRIDE` permits the
  same-PVC cross-parent rename when `cp -a` preserves a user-owned mount root;
  the Pods still keep RuntimeDefault seccomp, no privilege escalation, no
  service-account token, and a read-only image root. External resume also treats
  every checkpointed Jail Upgrade mutation window—from writer-hold intent
  through copy/recovery, passive population, switch, handoff, and Slurm
  smoke—as a cross-phase fence. Policy-bearing quota/job preflight and all
  predecessor reconciliation or rerun are deferred. A historically demoted
  rolling/final phase is restored only from paired completion and passed
  fast-verification evidence recorded before the fence began; missing evidence
  blocks before Jail Upgrade rather than mutating a predecessor. The fence is
  released only after maintenance restoration, handoff, Slurm smoke, and
  durable Jail Upgrade completion.
  Execute preflight now leaves a one-time writer hold pending while any
  protected source SSH session remains and tells the operator to acknowledge
  only exact fingerprint-bound voluntary exits. There is no policy override or
  drain timeout. The
  upgrade keeps all consumers on the discovered legacy jail PVC until the first
  successful slot switch, copies persistent paths from that source PVC, delays
  shared submounts until the switch, preserves discovered login public keys and
  fingerprint-verified SSH server host keys in a distinct target Secret, and
  reports/verifies the checkpointed passive-slot Job, rootfs handoff, exact
  every desired Ready target-owned login and target-NodeSet `/home` Pod volume, identical
  `/mnt/jail.upper/home` to `/mnt/jail/home` live filesystem identity, and
  post-Jail Slurm smoke. SSH continuity now copies only the six host-key fields, rejects
  conflicting Secret owners, and binds source-unavailable verification to the
  independent checkpoint digest. Verified-backup recovery now derives that
  canonical six-field digest from the hash-checked archive and refuses a
  same-name live Secret whose contents differ. Approved mutation requires the complete
  immutable source SlurmCluster/SSH-Secret binding. Resume now
  binds source SlurmCluster, SSH Secret, jail PVC, cluster, transition, and
  locked-segment identity to the verified pre-mutation backup, corroborates an
  already-switched live slot instead of repopulating it, and accepts a garbage-
  collected historical populate Job only after current consumer-slot, `/home`,
  `scontrol`, and `sbatch --test-only` probes revalidate durable evidence. The
  live smoke submits one checkpointed job,
  polls it to completion, and cancels it on a bounded failure. Handoff now runs
  `scontrol`, `sbatch --test-only`, and accounting/QOS checks while user
  partitions remain controlled/DOWN; `MetricsType`, `PluginDir`, or other Slurm
  configuration failures keep scheduling paused. Partitions reopen only after
  those pre-release checks pass, before the bounded live submission check.
  Passive-slot populate Jobs now use a checkpointed per-attempt token and bind
  the exact Job contract, Job UID, and passive PVC UID before reuse.
  Completed-compute
  values reconciliation also drains login sessions as requested and
  checkpoint-pauses Slurm before recreating worker StatefulSets. Controller-
  spool cleanup now checkpoints the exact target-values PVC before applying its
  Job and fails closed on claim drift; Jail Upgrade status reports a missing
  historical passive Job as unknown until execute-time live revalidation.
- Added internal Nebius API resume reconciliation for managed `soperator
  upgrade` MK8s node-template phases. Reruns now compare the checkpointed
  target, current command target, and live MK8s control-plane/node-group state
  before trusting a completed phase, waiting on provider rollout, retrying the
  Terraform-managed workflow, or failing fast on drift.
- Added internal Nebius API resume reconciliation for external Soperator
  node-template upgrades. Interrupted control-plane hops and node-group updates
  now compare checkpoint state, the accepted upgrade plan, and live Nebius
  state before cxcli decides to complete, wait, retry, or fail fast on drift.
- Added the API-reported Kubernetes version to the external Soperator upgrade
  provider node-group status table so cxcli's live screen stays aligned with
  Nebius console node-group state.
- Fixed `ext-soperator upgrade` checkpoint resume after an interrupted external
  node-template update. Checkpointed `updating` node groups are now treated as
  in-progress rollout state on rerun, reports show the accepted mutation as
  `Upgrade performed: yes`, and cxcli avoids submitting a duplicate Nebius
  node-group update while provider readback is still settling.
- Fixed Nebius SDK-backed operations, including `ext-soperator upgrade`, so
  synchronous SDK waits use a cxcli-owned background event loop instead of
  colliding with an active CLI event loop.
- Fixed external Soperator upgrade status so node groups show `not-started`
  with `upgraded=0`, `upgrading=0`, and `remaining=<total>` while the MK8s
  control-plane hop is running before node-template rollout starts, and fully
  ready `RUNNING` groups no longer show `upgraded=unknown` when Nebius omits
  `outdated_node_count` after rollout has started.
- Fixed external Soperator upgrade provider node-group status so active
  `PROVISIONING`, `Draining`, or `NodeProvisioning` rows with positive
  `outdated_node_count` report nonzero `upgrading` even when `ready/current`
  is already full.
- Changed `examples/slurm-jobs/submit-job-test.sh --watch-jobs` so the default
  watch continues until observed smoke jobs finish and leave Slurm's live queue;
  `--watch-duration` remains available as an explicit maximum watch window.
- Clarified the external Soperator access boundary: core onboarding, backup,
  and upgrade operations use Nebius API plus Kubernetes API/kubeconfig access,
  run Slurm commands through `kubectl exec`, and do not SSH from the operator
  workstation into login or worker nodes.
- Fixed `ext-soperator upgrade` execution-mode selection so omitting both
  `--dry-run` and `--execute` fails fast instead of silently running the
  read-only dry-run plan.
- Hardened Soperator upgrade handoff and packaging gates. Existing
  restore-capable external-upgrade backup metadata is reused from the active
  cluster-scoped checkpoint instead of creating a fresh backup on resume. Jail
  Upgrade now checkpoints rootfs handoff evidence for the active slot, rollback
  slot, target worker NodeSets, and preserved mounts before source retirement.
  Local Soperator render now skips Helm dependency builds when all pinned chart
  archives are packaged, including `file://` child charts, and reports missing
  packaged archives with an actionable packaging hint.
- Changed Soperator and ext-Soperator backup, discovery, onboarding, upgrade,
  segment, and checkpoint artifacts to use cluster-scoped paths under
  `backups/soperator-clusters/<cluster-key>/`,
  `generated/reports/soperator-clusters/<cluster-key>/...`, and
  `.nebius-cxcli/soperator-clusters/<cluster-key>/...`. `cluster_id` is
  preferred over display name, kube context, and cxcli target id, and old
  target-scoped checkpoints are rejected instead of resumed.
- Aligned managed `soperator upgrade` with the Jail Upgrade pattern. Only
  managed upgrades now expose `--jail-persistent-mount`,
  `--login-session-policy`, and `--login-session-drain-timeout`, automatically
  preserve `/home`, `/data`, `/scripts`, and `/models` during first adoption
  under `/mnt/jail-store/shared/...`, probe and migrate legacy rootfs data
  before passive-slot population, run managed migration Jobs and writer holds in
  the selected Soperator namespace, and keep login/worker writers held for
  resume if a later refresh step fails after the persistent copy completes.
- Fixed Soperator discovery guidance so app version, chart package version, and
  Jail rootfs image-tag version are reported separately. Discovery now records
  current and target populate-jail image evidence and only reports a Soperator
  chart upgrade or Jail refresh when selected actions or image comparisons
  require it.
- Promoted Soperator jail rootfs refresh to a visible `Jail Upgrade` phase
  across managed discovery guidance, external discovery/onboarding plans, and
  managed/external upgrade reports while keeping the durable
  `populate-jail-refresh` checkpoint id.
- Hardened Jail Upgrade interruption recovery by checkpointing phase execution
  at `populate-jail-refresh` entry, persisting the API-confirmed passive Job UID
  immediately after binding, and recording a `job-bound` monitor state before
  Pod/log polling begins. Resume also backfills monitor evidence for older
  checkpoints that already prove the exact Job complete, and validates exact
  namespace-scoped SlurmCluster identities independently of broad CRD discovery.
- Added single-SFS active/passive Soperator jail rootfs refresh. Managed
  installs now use `slot-a`/`slot-b` rootfs PVCs plus generic
  `jailPersistentMounts` from day one, with two login replicas by default.
  External upgrade keeps the existing physical jail SFS, creates logical slots
  under `/mnt/jail/.cxcli/rootfs`, treats legacy `/mnt/jail` as the rollback
  source during first adoption, and models `/home`, `/data`, `/scripts`,
  `/models`, plus explicitly declared additional customer paths as persistent
  jail mounts on the same physical jail SFS. First adoption now migrates those
  legacy in-rootfs paths into `/mnt/jail/shared/...` with ownership,
  permissions, symlinks, ACLs, and xattrs preserved where supported.
- Hardened external Soperator login continuity. `ext-soperator upgrade` now
  preserves the canonical login Service and Nebius LoadBalancer public/internal
  address, automatically converts an existing dynamic login LoadBalancer
  allocation to reusable Nebius allocation state, persists
  `nebius.com/load-balancer-allocation-id` under
  `slurmNodes.login.sshdServiceAnnotations`, and restores that checkpointed
  value into every later target Helm and Jail Upgrade values reapply so
  server-side apply cannot remove the retained allocation. It fails before
  chart handoff if that allocation cannot be uniquely resolved or updated,
  warms target login pods before source login retirement, and retains every
  exact source SSH session indefinitely. External upgrade exposes no login
  policy or drain-timeout flag. Socket disappearance remains `Indeterminate`;
  source retirement and any first-adoption writer hold advance only after the
  exact fingerprint-bound voluntary-exit acknowledgement.
- Fixed external Soperator chart/Jail handoff during rolling compute migration.
  Target values now preserve the chart-default `controller-spool`
  `volumeSources` entry, fail before Helm mutation when a rendered
  `volumeSourceName` has no matching source, and bump the rolling-compute
  values revision so reruns reapply corrected values. Login Slurm smoke can
  defer only across a planned Jail Upgrade chart/rootfs boundary when the
  failure matches known old-rootfs/target-config markers; post-Jail `scontrol`,
  `sbatch` dry-run and live-job completion, and accounting/QOS smoke are
  fail-closed. Execute output now de-duplicates repeated phase/preflight lines
  and prints a next action to rerun the exact original command in pending
  footers. First-adoption switch-over also keeps the legacy jail PV/PVC only as
  rollback storage while moving the active `jail` volume-source alias to the
  populated slot, so SConfigController and REST cannot remain
  blocked on the old rootfs. Fresh switch and post-switch resume now verify the
  exact live alias plus Ready rollouts for the enabled controller,
  SConfigController, and REST workloads before reopening Slurm
  partitions. The controller check follows Soperator's OpenKruise StatefulSet,
  and the convergence wait tolerates the operator's asynchronous generation of
  updated workload templates while requiring a regular workload container to
  mount the active PVC at `/mnt/jail` within one shared timeout.
  Resume reapplies only switched Helm values for alias or
  maintenance drift and proves both values converged without repopulating the
  slot. Completed-phase reruns revalidate the live alias, all consumers,
  persistent `/home` mounts, and Slurm admission even while the historical
  populate Job still exists. Persistent-copy paths also reject symlinks in any
  `/store` path component before copy, staging, quarantine, or promotion.
- Fixed approved `ext-soperator upgrade --execute` terminal output so the live
  phase spinner is closed before final checkpoint, report, and post-upgrade
  config-refresh lines are printed.
- Fixed external Soperator rolling-compute resume after target chart handoff.
  Reused backup metadata is now reported as a backup guard instead of a repeated
  phase, compatible reruns reuse checkpointed Slurm partition pause after
  target values have started applying, and login Slurm smoke can be deferred on
  that resume during the temporary old-client/target-config handoff while later
  cutover validation remains responsible for Slurm, partition, and accounting
  evidence. Generic login-continuity checkpoint state no longer proves target
  handoff by itself; older markerless checkpoints must first observe the Slurm
  config-source mismatch before reusing pause records. Live status now also
  reports Slurm worker status as deferred during that handoff instead of running
  old-client `sinfo` probes that can fail DNS SRV config discovery, and Slurm
  partition restore/resume falls back to the controller container when login
  pods are temporarily unavailable during handoff.
- Fixed approved external Soperator pre-mutation retries to validate and reuse
  the incomplete campaign checkpoint's existing restore-capable archive instead
  of creating a duplicate archive and replacing the checkpoint backup binding.
- Fixed external controller-HA bridge planning for onboarding-generated configs
  whose Soperator values contain only overrides. The bridge now resolves the
  exact controller image from an explicit `images.slurmctld` override or the
  committed target release profile before locking its OCI digest.
- Documented the Soperator jail upgrade process, active/passive rootfs
  switch-over semantics, same-SFS shared persistent mounts, the one-time
  rootfs-to-shared migration flow, absent-source persistent mount behavior for
  future writes such as `/models`, and checked-in workflow infographic in the
  README and design guide.
- Added a pre-populate active/passive jail capacity gate and expansion workflow.
  Managed production Soperator defaults now size the cxcli-owned jail SFS
  backing store at `2048` GiB total capacity, `soperator upgrade` expands that
  store through config render and Terraform apply, and `ext-soperator upgrade`
  expands only one identified existing Nebius jail SFS through the Nebius SDK/API.
  Both commands expose `--jail-sfs-resize-policy fail|prompt|apply` plus
  `--jail-sfs-resize-to-gib` before passive-slot population.
- Fixed `nebius-cxcli validate` coverage for external Soperator onboarding
  configs so malformed `deploy.targets[].soperator_onboarding` sections fail
  fast even when no enabled Soperator app row reaches the accepted-onboarding
  semantic gate.
- Fixed `ext-soperator onboard` Kubernetes target validation so interactive
  wizard entries and non-interactive `--to-k8s-version` values can lock a full
  supported sequential final target such as `1.32 -> 1.33 -> 1.34`, while
  `ext-soperator upgrade` still executes one locked Kubernetes minor hop per
  run.
- Aligned Soperator upgrade README, design, and docs-alignment assertions so
  external onboarding describes the full locked upgrade path with one segment
  executed per `ext-soperator upgrade` run, and managed upgrade docs name the
  actual postflight/shared-safety verification boundaries.
- Focused Soperator README and design navigation so managed setup/upgrade,
  external onboarding/upgrade, shared Jail Upgrade, Slurm examples, and safety
  checks are grouped in the same operator-facing order, with matching TOC
  entries and docs-alignment assertions.
- Fixed `ext-soperator upgrade --populate-jail-refresh force|manual` so
  node-template-only locked segments still schedule and display the
  `populate-jail-refresh` phase.
- Fixed external Soperator upgrade Markdown/JSON reports, including segment
  snapshots under
  `generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/segments/<segment-id>/`,
  so checkpoint-planned `populate-jail-refresh` remains visible even when a
  resume/report-refresh invocation uses a shorter active phase list.
- Fixed external Soperator upgrade checkpoint refresh so compatible reruns
  preserve checkpoint-planned `populate-jail-refresh` in both top-level and
  segment planned phases, allowing interrupted or failed Jail Upgrade runs to
  restart from the same pending phase.
- Fixed external Soperator backup recreation-material detection for
  SlurmCluster-prefixed Secrets and ConfigMaps such as
  `<slurmcluster>-sshd-keys`, `<slurmcluster>-slurmdbd-configs`, and
  `<slurmcluster>-slurm-configs`.
- Changed external Soperator multi-hop execution to persist the accepted locked
  path in v2 checkpoints as `locked_upgrade_path` plus explicit
  `upgrade_path_fingerprint`, `current_segment_id`, `completed_segment_ids`,
  `pending_phase`, and `segment_state`. Repeated identical
  `ext-soperator upgrade <config.yaml> --target <target> --execute --approve`
  invocations now advance one locked segment at a time, can resume from the
  checkpoint snapshot if `config.yaml` loses the onboarding `upgrade_path`, and
  fail fast on old progress-only checkpoints. The latest external upgrade
  Markdown/JSON reports now include locked-path progress and each segment also
  writes a snapshot under
  `generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/segments/<segment-id>/`.
- Clarified `ext-soperator upgrade` help and README wording so operators repeat
  the same `--execute --approve` command until all accepted locked-path segments
  are complete; `ext-soperator onboard` is only for a new later path or an
  intentional repair/replan.
- Added phase-bounded resume checkpoints for managed `soperator upgrade` and
  hardened the existing external `ext-soperator upgrade --execute` phase
  runner. Both commands now record `planned_phases`, `completed_phases`,
  `pending_phase`, and per-phase `phase_state`; reruns restart the interrupted
  phase from the beginning, verify completed phases before skipping them, and
  persist the current pending phase/report on ordinary errors, Ctrl+C, and Typer
  aborts when Python can run cleanup.
- Added phase-scoped Soperator upgrade validation summaries. Managed
  `soperator upgrade` now records `phase_state[<phase>].fast_verification` for
  every planned phase, including evidence-only setup phases, and both managed
  and external upgrade execution print a phase validation summary before
  advancing. Failed phase verification keeps the same phase pending, writes the
  Markdown/JSON upgrade reports, and stops; full Soperator/Slurm validation
  remains at managed postflight and external validation-hold boundaries.
- Changed `ext-soperator upgrade --execute` runs with no explicit
  `--job-policy` to match managed `soperator upgrade`: real TTY runs default
  to `interactive` and carry that resolved policy through plan output, backup
  metadata, and checkpointed execution, while non-TTY and `--no-interactive`
  runs default to `fail`.
- Renamed the blocking Soperator Slurm job policy from `wait` to
  `wait-to-finish` across managed and external Soperator upgrade, scale-down,
  deploy, and Flux apply surfaces. The old `wait` value is no longer accepted.
- Fixed Soperator Slurm job-policy checks after in-place node-template
  replacement so checkpoint resume skips deleted stale `computeinstance-*`
  worker nodes instead of failing node-alias mapping or querying all Slurm jobs
  for an empty affected-node scope.
- Changed external Soperator upgrade MK8s control-plane, node-group, and
  aligned-SFS reads/mutations to use the Nebius SDK/API directly instead of
  shelling out to the `nebius` CLI.
- Fixed the interactive Soperator Slurm job-control screen so selected rows use
  a high-contrast marker, remaining times tick while the selector is open, and
  pressing `w` waits in the same screen instead of switching to a separate wait
  dashboard.
- Improved the interactive Soperator Slurm job-control screen so cancel,
  requeue, and requeue-hold actions refresh the same table in place, idle
  polling exits automatically when affected jobs clear, `?` opens a scrollable
  help overlay, the compact key strip includes cancel/requeue/requeue-hold
  shortcuts, and `b` hides the full-screen table while cxcli keeps polling
  silently at the same Slurm gate.
- Added default-enabled Soperator worker-upgrade Slurm scheduling pause.
  Managed `soperator upgrade` and external `ext-soperator upgrade` now expose
  `--slurm-scheduling-pause / --no-slurm-scheduling-pause`, onboarding
  persists `node_template_upgrade.slurm_scheduling_pause: true`, cxcli sets
  worker-overlap `UP` Slurm partitions to `DOWN` during worker gates, treats
  pending jobs as queued information while pause is active, dispatches
  Slurm-clear external node-template worker provider units without old
  wave-budget pacing, verifies live partition state after restore-command
  failures, restores cxcli-owned partition state on success, failure, abort, or
  rerun cleanup, and releases only cxcli-recorded requeue-held job IDs after
  successful upgrade completion.
- Fixed explicit Soperator Slurm cancel actions so managed and external upgrade
  paths wait for selected `scancel`ed jobs to leave the affected node scope
  before continuing. The job-control screen now highlights `COMPLETING` jobs
  and explains that Slurm may keep cancelled jobs in cleanup while nodes return
  to service.
- Changed external Soperator upgrade MK8s live status to a Nebius API-backed
  provider node-group rollout table. The table now shows per-node-group
  provider state, total, upgraded, upgrading, remaining, ready/current, and
  latest event columns from one node-group snapshot per refresh, and no longer
  mixes Kubernetes `Registered nodes` counts into the MK8s section.
- Improved external Soperator upgrade live-status readability by highlighting
  provider table labels, rollout counters, and provider states in terminal
  output while preserving the same plain-text table in non-interactive logs.
- Fixed external Soperator upgrade live-status spinners so stray Enter/key
  presses while no prompt is active do not echo new lines that duplicate the
  current status row. cxcli restores normal terminal input before prompts and
  full-screen Slurm job controls.
- Fixed external Soperator upgrade operator output so backup phase comments and
  live status lines no longer repeat the top-level stage label, backup
  checkpoint reuse validates the recorded archive size before mutation resumes
  and falls back to SHA256 for older metadata, Slurm job preflight is announced
  before the job-control screen opens, pending runs include an explicit upgrade
  status summary, and the Slurm job-control screen keeps one concise legend
  while polling Slurm in a background worker. Read-only Slurm probes now return
  bounded timeout errors instead of freezing long-running upgrade status checks.
  MK8s node-template status now also surfaces active Nebius node-group rollout
  state, event code, upgraded, upgrading, remaining, and ready/current counts.
  Jail Upgrade status now includes live Kubernetes,
  Slurm, Soperator, and populate-jail Job signals instead of an unknown
  no-checks message.
- Added `examples/slurm-jobs/submit-job-test.sh --watch-jobs` to watch smoke
  jobs during an upgrade with timestamped `squeue` snapshots and optional
  `sacct` accounting evidence for observed job IDs. Watch sample headers are
  highlighted in color on terminals for easier scanning.
- Changed `ext-soperator upgrade` output to keep locked-path plans more compact:
  support policy now avoids repeating the explanatory rule text when the locked
  path is printed, MK8s node-upgrade phase wording is shorter, execution
  contracts are summarized, and external-upgrade backup archives now use the
  accepted source chart/Kubernetes versions in transition names.
- Added Soperator Jail Upgrade rootfs-refresh handling to managed `soperator upgrade`
  and external `ext-soperator upgrade`. Chart upgrades now report a
  `populate-jail-refresh` stage, defaults non-TTY upgrade job handling to
  `fail` unless a disruptive policy is selected explicitly, and extends
  Soperator backup archives with recreation coverage evidence for
  new/replacement-cluster runbooks.
- Changed managed and external Soperator backup/restore archives to fail fast
  when required controller/accounting recreation material is missing, record
  sanitized retained PV/PVC restore manifests that preserve PV `claimRef` and
  PVC `volumeName`, and restore those bindings before the remaining namespaced
  resources on new empty target clusters after validating archive checksums,
  recreation coverage, and required CRD/API availability. Slurm accounting dumps
  now derive the cluster name from live `slurm.conf`; generated MariaDB metrics
  Secrets are backed up when present but no longer block backups when absent;
  VM/NFS retention and final Terraform convergence remain operator runbook steps.
- Changed Nebius SDK operator-auth precedence so the matching Codex agent
  `NEBIUS_AUTH_CREDENTIALS_FILE`/`NEBIUS_PROFILE` pair with an existing
  credential file wins over a static `NEBIUS_IAM_TOKEN`, keeping long-running
  agent clients on renewable service-account credentials while preserving
  IAM-token fallback for short commands, stale credential paths, and generic
  runtime credential files.
- Changed Soperator `create` and `component add` scaffolding to seed
  `slurmNodes.login.sshRootPublicKeys` from the configured MK8s node-group SSH
  public key for newly created Soperator targets so rendered login nodes
  authorize `root` SSH with the matching private key instead of relying on
  chart defaults.
- Added Soperator worker scale commands. Managed `soperator scale-up` /
  `scale-down` keep cxcli config/render state aligned, use `NodeSetPowerState`
  for ephemeral workers, and gate scale-down with the existing Slurm job-policy
  choices. Ad hoc `ext-soperator scale-up` / `scale-down` operate on external
  clusters with explicit `--project-id`, `--cluster-id`, and `--kube-context`,
  including scale-to-zero maintenance workflows; explicit non-ephemeral ordinal
  removal is tail-only until a tested controller-safe `reserveOrdinals` path is
  added.
- Renamed the public Soperator Slurm job-test examples to
  `submit-job-test.sh`, `cpu-job-test.sbatch`, and `gpu-job-test.sbatch`,
  replaced the wrapper's `--kind` option with `--part-type`, and documented
  copying the examples to the Slurm login node before submitting jobs with
  `sbatch`.
- Expanded `soperator --help` and `ext-soperator --help` examples so each
  managed and external Soperator subcommand shows copy-pasteable dry-run,
  execute, config-backed, or standalone command forms where applicable.
- Hardened external Soperator target Helm cutover so `ext-soperator upgrade`
  forces target `kube-rbac-proxy` image values to
  `registry.k8s.io/kubebuilder/kube-rbac-proxy:v0.15.0` for both the Soperator
  manager and Soperator checks before applying the target chart.
- Added a managed `soperator upgrade` order guard for old Soperator chart
  upgrades across the Kubernetes `1.33+` boundary. Managed upgrades still use
  per-run `config.yaml` plus live MK8s state instead of a locked path, but now
  block a combined `1.32 -> 1.33` Kubernetes hop plus Soperator chart upgrade
  when the chart must be upgraded first while Kubernetes stays at the staging
  minor.
- Added locked external Soperator upgrade paths. `ext-soperator onboard` now
  stores the accepted discovery-guided path under
  `deploy.targets[].soperator_onboarding.upgrade_path` and includes it in the
  accepted onboarding fingerprint. Repeated `ext-soperator upgrade --execute
  --approve` runs now advance one locked segment at a time from checkpoint
  progress, print the next same-command invocation, keep onboarding in place
  while segments remain, refresh pre-mutation source discovery fingerprints
  when source/target versions and phase plans still match, and hand back to
  deploy-owned reconciliation only after the final locked segment completes.
- Clarified `ext-soperator upgrade` plan phase rows so the approval, MK8s
  node-template, target GPU stack, Soperator chart, and final cutover phase
  descriptions show the current locked segment's Kubernetes and Soperator hops
  instead of only generic lifecycle titles.
- Clarified `ext-soperator upgrade --dry-run` output for preserved storage and
  compute layouts, restore target scope, and external Kubernetes hop scope. The
  plan now labels keep-existing modes as layout preservation, uses migration
  work labels for storage/compute remediation, states that restore-capable
  backups restore only to a new/replacement cluster, and makes clear that
  external node-template work is one accepted Kubernetes minor hop per upgrade
  run.
- Organized `ext-soperator upgrade --dry-run` output into concise sections for
  target discovery, the locked upgrade path, accepted onboarding actions,
  node-template rollout, phases, execution controls, and execution contracts.
- Added spinner/status feedback while `ext-soperator upgrade --dry-run`
  refreshes external Soperator discovery and Nebius provider inventory before
  printing the plan.
- Fixed `ext-soperator onboard --cluster-id` interactive runs so `--cluster-id`
  only preselects the cluster; storage, compute, and external node-template
  rollout prompts still run unless `--no-interactive` or explicit rollout flags
  are supplied.
- Simplified external Soperator render/onboard guidance so accepted upgrade work
  is listed under `Accepted onboarding actions:` without extra route/deploy
  rationale before the next-step commands.
- Fixed `render` overwrite confirmation so first render does not warn or require
  `--force` when the only existing generated files are command-owned lifecycle
  reports such as
  `generated/reports/soperator-clusters/<cluster-key>/discovery/manifest.json`;
  rerenders over render-owned artifacts still prompt or require `--force`.
- Added public Soperator Slurm upgrade smoke job examples with a configurable
  submitter for repeated CPU/GPU `sbatch` submissions, array mode, optional
  exclusive placement, interactive job-policy demonstrations, and a `--login`
  flag that stages the examples under `/root/testjobs` before opening a
  login-node SSH session there. The submitter now defaults to the GPU job
  template for the standard Nebius `main*` partition without requiring
  `--part-type`.
- Extended Soperator Slurm job-policy coverage to chart-driven worker
  reconciliation. Managed `soperator upgrade`, external `ext-soperator
  upgrade`, local `deploy`, and `flux apply` now gate disruptive Soperator
  worker-pod reconciliation on all live worker NodeSets when a live
  SlurmCluster exists, while first installs with no live SlurmCluster skip the
  interactive job gate.
- Extended managed `soperator upgrade` and external `ext-soperator upgrade`
  Slurm job gates from running allocations to affected Slurm jobs, including
  pending jobs in affected partitions or requested/scheduled on affected nodes.
  Interactive job control now uses an aligned Textual table in prompt-capable
  terminals, reserves `a` for select/clear all and `i` for invert selection,
  uses uppercase `C` for all displayed cancellation, and uses uppercase `Q`/`H`
  for all displayed active requeue actions. External upgrade status spinners
  pause while prompts are active, and wait output shows a per-second countdown
  dashboard between `squeue` polls. Requeue policies reject pending jobs with
  guidance to cancel, wait, choose another displayed job, or abort.
- Changed default Soperator Slurm job-policy handling so managed and external
  upgrade runs default to `interactive` only in a real TTY and to `fail` in
  non-TTY or `--no-interactive` automation, while local `deploy` and `flux
  apply` still default to `wait-then-cancel` outside a TTY. The default
  `--job-wait-timeout` is now `1h`; after that timeout cxcli cancels only the
  still-displayed affected jobs and proceeds only after they clear.
- Changed existing config-backed commands so `client_info.nebius.tenant_id` is
  optional when the command can operate from `project_id` and `region_id`.
  `create` and deployments-root `ext-soperator onboard` still require tenant
  identity to validate scope, write `config.yaml`, and resolve the canonical
  tenant/project folder path. Quota checks continue with project-scope quota
  data when tenant id is absent and report a partial-coverage warning for
  skipped tenant quota and Capacity Dashboard checks.
- Hardened Soperator upgrade and deploy guardrails. External Soperator
  affected-node Slurm job handling now fails closed when Kubernetes nodes cannot
  be mapped to Slurm node names or Slurm rejects the scoped node filter, so
  `--job-policy cancel-all` and requeue-all policies cannot act on an
  unfiltered cluster-wide job list. External upgrade worker NodeSet readiness
  now prefers `readyReplicas` over total replicas, validation-hold fast
  verification records absent Soperator smoke validations as skipped, and
  deploy smoke detects failed one-shot `populate-jail` Jobs with
  `backoffLimit: 0`.
- Hardened external Soperator Kubernetes-hop upgrades. Completed prior-hop
  checkpoints no longer reuse stale backup metadata for the next hop,
  same-version Soperator onboarding now plans external node-template work for
  Kubernetes minor upgrades, zero-surge service-node rollouts temporarily reduce
  the security-profiles webhook by one replica and restore it after rollout, and
  target completion now re-runs shared protected-state safety verification
  before reporting success.
- Hardened Soperator deploy smoke around jail startup. When the
  `populate-jail` Job is still active after the same-node `jail-mount` pod can
  see the jail `.populated` sentinel, cxcli now deletes only the stuck Job pod
  and waits for the Job to complete. Deploy smoke also fails on failed or
  crash-looping Soperator pods, so GPU driver-jail and local jail failures no
  longer pass as a clean deployment snapshot.
- Added Soperator/Kubernetes upgrade support-policy enforcement for managed and
  external Soperator upgrade paths. cxcli now records matched support rule
  status in onboarding, dry-run/execute reports, and managed checkpoints; fails
  `unsupported` and `not_validated` paths before mutation; supports an explicit
  `--allow-unsupported-soperator-upgrade-path` advanced/testing override for
  Soperator policy rejections only; and continues to reject Kubernetes skipped
  minor upgrades such as `1.31 -> 1.34`.
- Added read-only Soperator discovery commands: `soperator discover` for
  cxcli-managed clusters and `ext-soperator discover` for external clusters.
  Both write the canonical support-safe
  `generated/reports/soperator-clusters/<cluster-key>/discovery/` bundle with manifest,
  identity, Kubernetes, Slurm, accounting, customizations, fingerprints,
  findings, and summary files; discovery is not a backup and omits raw Secret
  values, SQL, DB dumps, tokens, and cert material. External discovery can now
  run before onboarding with `--project-id <project-id> --cluster-id
  <mk8scluster-id>` and no config/deployments path; `--tenant-id` is optional
  metadata for that standalone cluster-id mode, and `--client-name` can select a
  runtime-auth cache profile when project-scoped cache selection is needed. The
  discovery footer and summary now include a Soperator/Kubernetes upgrade path
  evaluation, including the canonical staged order of Kubernetes `1.31 -> 1.32`,
  Soperator `1.22.3 -> 4.0.2-ps.3`, then Kubernetes `1.32 -> 1.33 -> 1.34`
  for older external sources targeting the cxcli-pinned chart. `--output-dir`
  now selects the bundle root while preserving
  `generated/reports/soperator-clusters/<cluster-key>/discovery/` below it, and discovery
  output now prints Soperator install status plus the detected Soperator version
  when Helm release metadata or live Soperator resource labels provide one.
- Added standalone restore-capable Soperator backup/restore commands:
  `soperator backup`, `soperator restore`, `ext-soperator backup`, and
  `ext-soperator restore`. The backup archive now includes raw and
  restore-ready Kubernetes in-cluster resources plus chart-managed MariaDB
  accounting DB material when live chart-managed accounting is present, while
  restore validates checksums and stays dry-run until `--execute --approve`.
  Restore help and docs now explicitly state that restore is DR/new-empty-target
  only, not same-cluster rollback, and must not target the original/source
  cluster or an existing Soperator namespace.
  External backup can now also run before onboarding with
  `ext-soperator backup --project-id <project-id> --cluster-id
  <mk8scluster-id>` or a direct `--kube-context`; standalone `--cluster-id`
  backup now documents that `--access external` selects the public endpoint and
  `--access internal` selects the private endpoint, and rejects `--access` with
  standalone `--kube-context` because the kubeconfig context already selects
  its endpoint.
- Fixed standalone external Soperator backup planning to reuse live Helm
  release evidence from the source cluster, report source kind
  `external-soperator-backup`, and write plain backup archive names without
  fake chart/Kubernetes upgrade transitions.
- Changed external Soperator backup archive names to start with
  `external-soperator-backup-` so they are easy to distinguish from
  cxcli-managed `soperator backup` archives.
- Added terminal spinners around Soperator backup and discovery collection so
  managed and external `soperator` / `ext-soperator` backup and discover
  commands show progress during quiet live-cluster work.
- Fixed Soperator backup and discovery `wckey` snapshots to use Slurm-portable
  `Cluster,User,WCKey` fields, and made accounting DB dump/restore resolve the
  target-specific `*-acct-db-0` pod instead of assuming the old static pod name.
  This avoids backup failures on clusters where `sacctmgr show wckey` does not
  expose an `Account` field or the MariaDB pod is named after the Slurm cluster.
- Fixed Soperator backup/restore for clusters without chart-managed Slurm
  accounting. Backup now records accounting snapshots and restore metadata as
  not collected instead of failing when `sacctmgr` reports that slurmdbd
  accounting is unavailable.
- Fixed Soperator accounting DB backup dumps and restore imports to authenticate
  with the chart-provided `MARIADB_ROOT_PASSWORD` environment instead of
  assuming local root access without a password.
- Added Soperator upgrade Slurm requeue policies: `--job-policy requeue-selected`
  with repeated `--requeue-job`, `--job-policy requeue-all`,
  `--job-policy requeue-hold-selected` with repeated `--requeue-job`, and
  `--job-policy requeue-hold-all`. The policies call `scontrol requeue` or
  `scontrol requeuehold`, wait for selected jobs to leave nodes selected for
  the MK8s rollout, and still stop if those jobs keep running there. The
  managed Soperator upgrade path drains cxcli-owned Slurm nodes before requeue
  or requeue-hold.
- Fixed managed Soperator upgrade protected-config comparison so cxcli-owned
  Slurm drains and Nebius node replacement state no longer look like customer
  config drift after an MK8s node-template phase.
- Hardened MK8s node-template waits to require two consecutive node-group ready
  observations before advancing to the next staged node group.
- Changed managed `soperator upgrade` into the canonical cxcli-managed
  Soperator cluster upgrade command. It now accepts `--to-chart-version`,
  optional MK8s node-template target flags, Slurm affected-job policy flags, and
  `--backup-dir`; creates a restore-capable local backup with raw Kubernetes
  Secret material plus optional chart-managed MariaDB accounting DB dump before mutation;
  and writes the combined checkpoint/report without requiring a separate
  render/deploy step.
- Changed external Soperator execution to the canonical
  `ext-soperator upgrade` command. The old `ext-soperator migrate` command is
  no longer registered; external upgrades now require prior accepted
  `ext-soperator onboard` data, refresh discovery before planning or mutation,
  create a restore-capable backup before mutation, use
  `.nebius-cxcli/soperator-clusters/<cluster-key>/ext-soperator-upgrade/checkpoint.json`,
  write `generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md`
  and `report.json`, and handle affected Slurm jobs with `--job-policy` before
  MK8s rollout work.
- Added fast stage-scoped verification after every executed
  `ext-soperator upgrade --execute` stage, including the final post-upgrade
  MK8s and Helm readiness checks. Failed stage verification keeps the same
  phase pending instead of advancing, records
  `phase_state[<stage>].fast_verification` in the checkpoint, and writes the
  Markdown `Stage Fast Verification` rollup plus JSON `stage_verification`
  details into
  `generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md`
  and `report.json`.
- Added the same fast stage-scoped verification gate to managed
  `soperator upgrade` runs. Managed upgrades now record per-stage
  `fast_verification` results in the checkpoint, including the MK8s
  post-validation boundary, stop before the next stage when a stage gate fails,
  and write the Markdown `Stage Fast Verification` rollup plus JSON
  `stage_verification` details into
  `generated/reports/soperator-clusters/<cluster-key>/soperator-upgrade/report.md`
  and `report.json`. Shared
  stage-verification payload/report helpers are reused by both managed and
  external Soperator upgrade paths.
- Improved managed and external Soperator upgrade visibility so runtime phase
  output and reports include the operator-facing top-level stage:
  `MK8s Node Upgrades` or `Soperator Upgrade`.
- Added external Soperator onboarding target chart selection. The
  `ext-soperator onboard` command now accepts `--to-chart-version`, prompts
  with the `component_sources.yaml` Soperator chart pin by default, validates
  non-default values against the configured Soperator chart source when source
  validation is enabled, and keeps
  `deploy.targets[].soperator_onboarding.target_version` aligned with the
  Soperator app chart row.
- Fixed external Soperator onboarding Kubernetes target selection. Interactive
  onboarding now prints the discovered live Kubernetes minor, defaults external
  node-template work to the next supported minor hop instead of the global latest
  minor, prints and stores current/target Kubernetes versions in discovery
  output, fails fast when the discovered live minor is newer than cxcli's
  supported external onboarding target, and keeps
  `ext-soperator upgrade --dry-run` plan output aligned with those values.
- Improved Soperator discovery and external onboarding guidance. Discovery
  summaries now include an `Upgrade Guidance` section with the matched
  Soperator/Kubernetes upgrade-path rule without blocking read-only collection,
  while onboarding decision summaries and external upgrade dry runs reuse the
  same upgrade-path wording before execute-time gates.
- Fixed external Soperator rolling compute migration to stamp both
  `slurm.nebius.ai/nodeset` and `slurm.nebius.ai/nodeset-name` on created
  service-role node groups, preventing cloned controller labels from blocking
  accounting pod scheduling after chart takeover.
- Fixed external Soperator worker NodeSet rendering for legacy source clusters
  whose NodeSet name and MK8s worker-group role label differ. cxcli now falls
  back to the single discovered worker group, samples Slurm's
  `slurmd -C --parameters=l3cache_as_socket` topology for GPU workers, and adds
  `SlurmdParameters=l3cache_as_socket` to generated target values when no
  explicit Slurm daemon parameters are configured, avoiding GPU GRES affinity
  `INVAL` workers after upgrade.
- Fixed external Soperator target cutover to reconcile Slurm worker runtime
  `NodeAddr` and `InstanceId` values from the current worker pods after chart
  takeover, and made zero-downtime safety fail when post-upgrade Slurm workers
  are `DOWN`, drained, failed, or `NOT_RESPONDING`.
- Fixed external Soperator safety verification to treat baseline-suspended
  ActiveChecks as restored when they remain suspended, and to classify
  migration-owned chart takeover and node replacement deltas as intentional
  external-upgrade drift instead of remediation-required customer drift.
- Clarified external Soperator onboarding output. The read-only discovery
  summary no longer prints future external upgrade phases or selected actions
  as if onboarding will mutate the cluster, and onboarding now prints explicit
  accepted storage and compute layout decisions after mode resolution.
- Reworded compatible non-standard Soperator Helm release discovery as
  `helm-release-detected`, including the detected release name, namespace,
  chart, source version, and matched migration profile in the onboarding
  finding message. Soperator discovery now enumerates Helm releases across all
  namespaces and stores only Soperator-like releases, so non-standard Soperator
  namespaces are reachable by onboard/discover without adding unrelated Helm
  release inventory to the discovery bundle.
- Added a shared Soperator upgrade safety layer used by both managed
  `soperator upgrade` and external `ext-soperator upgrade`. Both flows now
  capture protected customer state after backup and before mutation, run the
  same bounded read-only post-upgrade fast verification, report before/after
  protected-state hashes and deltas, expose `--approve-remediation` for
  remediation-required deltas while keeping blocked data-loss/downtime deltas
  non-overrideable, keep protected-state report payloads to redacted keys and
  fingerprints instead of raw custom-resource specs or annotations, fail closed
  when approved external mutation lacks restore-capable backup metadata, and
  print/checkpoint the current upgrade phase with component-aware operator
  comments and terminal spinners around quiet long-running steps from preflight
  through final report writing, while keeping heavy follow-ups such as backend
  metrics/log queries, Terraform drift
  review, and full Slurm acceptance audits out of the required fast gate.
- Fixed `ext-soperator onboard` for external MK8s clusters with heterogeneous
  GPU worker groups. Target-scoped GPU app value materialization now accepts
  mixed GPU presets and RDMA/non-RDMA groups when the resolved chart defaults do
  not conflict, so onboarding no longer fails with the single GPU
  platform/preset error before writing `config.yaml`.
- Improved CLI `--help` example readability. Example sections now render each
  command behind a visible accent-colored `|` separator so wrapped help output
  makes the beginning of every example easier to scan, and trailing explanatory
  prose is split into a separate `Comments:` block.
- Aligned `soperator` and `ext-soperator` help examples so managed and external
  upgrade dry-run/execute flows render as copy-pasteable command lines, and
  expanded the README common flag index to include `ext-soperator discover`,
  `ext-soperator onboard`, and `ext-soperator upgrade`.
- Changed `acceptance-test smoke` and `acceptance-test benchmark` to require an
  explicit `--suite`. Omitted suites now fail fast with a warning instead of
  selecting a default K8s acceptance path.
- Tightened the create/component-add wizard labels for MK8s, SFS, and
  Soperator production-cluster prompts. Active cluster shape, SFS filesystem,
  GPU default, service sizing, and worker-shard controls now render as required
  while true opt-in alternatives such as SFS `existing_id` and optional
  Soperator child charts stay optional.
- Documented the Soperator cluster upgrade split in the README. Full
  cxcli-managed cluster upgrades run the Terraform-managed MK8s layer first and
  the Soperator chart upgrade second only when both layers change, while
  external clusters use `ext-soperator onboard` and guarded `ext-soperator
  upgrade` for onboarding-selected MK8s/Soperator upgrade work. The docs now
  spell out the staged upgrade process, validation gates, Slurm job-policy
  decisions, report/checkpoint outputs, and completion handoff for both paths.
- Added Soperator GPU driver-jail guardrails for Nebius-image GPU workers.
  cxcli now materializes the chart-owned `gpuDriverJail` contract for managed
  and migrated GPU NodeSets, fails fast on conflicting external mounts, and
  validates both the static NodeSet mount/init contract and Slurm job-root
  visibility of non-empty `libcuda.so.1`, `libnvidia-ml.so.1`, and
  `nvidia-smi` in acceptance smoke and Slurm NCCL benchmark reports. The
  deploy/upgrade snapshot skips the static NodeSet contract for known
  pre-contract charts such as `4.0.1-ps.2`, then enforces it for
  `4.0.2-ps.3` and newer.
- Fixed Soperator deploy smoke to wait through bounded first-run storage and
  pod startup before evaluating the Pending-pod snapshot. It still does not wait
  for full Slurm availability or run Slurm jobs during deploy.
- Fixed K8s CUDA acceptance smoke so a Soperator-owned target with every Ready
  GPU already allocated reports `SKIPPED` with the allocation reason instead of
  writing a failed result.
- Changed `acceptance-test benchmark` NCCL threshold handling for 1-GPU K8s and
  Slurm runs. When NCCL completes and reports average bus bandwidth, a
  below-threshold result is recorded in the JSON report as a comment instead of
  failing the benchmark.
- Shortened `acceptance-test benchmark` terminal result lines for informational
  1-GPU NCCL below-threshold runs by keeping the threshold value visible while
  leaving the longer bandwidth-threshold comment in the JSON report only.
- Added concise terminal result lines for `acceptance-test smoke` and
  `acceptance-test benchmark` reports, including `PASSED`, `FAILED`, or
  `SKIPPED`, suite scope, target, and the most relevant report summary or skip
  reason. Result statuses are colorized on color-capable terminals: green for
  `PASSED`, red for `FAILED`, yellow for `SKIPPED`, and cyan for unknown report
  parsing status. Report paths, suite names, and targets use bold accent colors,
  while default-color labels, summaries, skip reasons, and elapsed times stay
  unbolded for readability. Acceptance reports
  now record `elapsed_seconds` and `elapsed_time`, and terminal result lines
  print elapsed time in `hh:mm:ss`. Failed acceptance runs now still print any
  report they wrote before exiting nonzero.
- Fixed Slurm NCCL acceptance benchmark eligibility so Soperator clusters with
  idle one-GPU Slurm worker nodes run `all_reduce_perf_mpi` instead of being
  skipped by the previous hardcoded 8-GPU-per-node floor. The benchmark still
  prefers 8-GPU Slurm nodes when available, but multiple one-GPU nodes now run
  as a multi-node NCCL benchmark capped at a 2G message size, and one total GPU
  runs as a launch/smoke check without a collective-bandwidth threshold. README
  and `acceptance-test benchmark --help` now show a two-node Slurm NCCL learning
  command with `--average-bus-bandwidth-threshold-gbps 300` for Ethernet-only
  one-GPU workers.
- Fixed `acceptance-test smoke` and `acceptance-test benchmark` so their
  target handoff stays ad-hoc and no longer falls back to Terraform output or
  backend initialization. Acceptance runs now use deploy-report/local
  kubeconfig handoff metadata when available and fail fast with deploy/flux
  remediation when the target context is missing.
- Changed `acceptance-test benchmark` to be suite-driven instead of NCCL-only
  in its command contract. Operators must choose `--suite k8s-nccl` or
  `--suite slurm-nccl`; after a suite is selected, omitting `--target` still
  runs across all generated targets, omitting `--max-nodes` uses all
  schedulable GPU nodes, omitting `--timeout` leaves no cxcli timeout, and the
  RDMA bandwidth threshold defaults to 300 Gbps. `slurm-nccl` is the canonical
  Slurm NCCL suite. Slurm NCCL now honors the same run-only `--max-nodes`, `--timeout`, and
  `--average-bus-bandwidth-threshold-gbps` benchmark flags, and smoke suite
  help now uses canonical `slurm` and `k8s-cuda` suite names.
- Changed `acceptance-test smoke` to run all generated targets when `--target`
  is omitted after a suite is selected, matching benchmark target selection.
  Removed the ambiguous `--k8s` and `--soperator` acceptance-test selectors;
  operators now select runtime behavior through `--suite`: `k8s-cuda` or
  `slurm` for smoke, and `k8s-nccl` or `slurm-nccl` for benchmark.
- Updated the bundled Soperator portable chart pin to `4.0.2-ps.4`, matching
  the current parent chart package release while keeping local-source
  resolution tied to `helm-charts/soperator/Chart.yaml`.
- Changed `component add apps:soperator` so production-cluster adds fail fast on
  existing managed MK8s targets whose non-empty `inputs.node_groups` do not
  include the required Soperator service-role groups (`system`, `controller`,
  `login`, and `accounting`) and do not provide a complete explicit
  `apps.charts[].placements` map. The guard runs before raw config
  normalization can auto-enable GPU app rows, so non-standard existing clusters
  should use `ext-soperator onboard` or an explicit placement-bearing config
  path instead of generated production placement inference.
- Aligned `nebius-cxcli component` help examples so the group and
  `list`/`add`/`remove` subcommands consistently show `--config` as the
  required config path option, matching the enforced day-2 component editing
  contract.
- Added a required MK8s node inventory smoke validation for every MK8s deploy
  target. It performs one read-only all-node Kubernetes inventory query,
  reports Ready/CPU/GPU/node-group totals, checks scheduler-visible
  `nvidia.com/gpu` inventory before workload validation, enforces configured
  or inventoried GPU node-group presence and minimum expected Ready GPU node
  counts when available, keeps validation reports target-scoped for multi-target and
  multi-GPU-node-group deployments, is generated outside the configurable
  `deploy.targets[].deployment_testing.mk8s_gpu.*` block so it cannot be
  disabled, and keeps the bounded GPU visibility node cap separate from explicit
  `acceptance-test benchmark` NCCL options.
- Fixed rendered MK8s node groups so they carry the canonical
  `nebius.com/node-group` label. The required node inventory smoke can now match
  live Kubernetes nodes back to configured node-group names on plain MK8s GPU
  deployments and reports nodes grouped by node group instead of failing minimum
  expected Ready GPU node checks when Nebius exposes only
  `nebius.com/node-group-id` on the nodes.
- Added the `generated/reports/` validation-detail directory to the final
  `deploy` Deployment summary footer so the per-validation JSON smoke reports
  are visible beside the customer-facing `deploy-report.md`.
- Replaced the persistent deploy-time MK8s GPU test contract with
  `deploy.targets[].deployment_testing.*`. Deploy config now carries only fast,
  declarative checks such as operator readiness and bounded GPU visibility;
  NCCL is no longer persisted in `config.yaml` and is selected only through
  explicit `acceptance-test benchmark` flags.
- Fixed the Soperator create wizard so 1-GPU Soperator production targets no
  longer materialize deploy-time NCCL settings while profile-backed worker
  defaults still carry transient GPU-cluster keys before shape cleanup runs.
- Fixed wizard backtracking for numeric fields such as
  `infra.components[0].inputs.soperator.worker_gpu_nodes_per_group`, so entering
  `q` goes back instead of being rejected by Typer as an invalid integer.
- Removed deploy-time NCCL validation support from generated manifests; bundles
  that still contain `mk8s_nccl` must be rerendered and fail fast instead of
  running benchmark work during deploy.
- Fixed the MK8s create wizard so the auto-selected Kubernetes version is
  written before `mk8s_compatible_platforms` resolves CPU/GPU platform choices.
  This prevents required platform prompts from falling back to manual input
  after `inputs.cluster.public_endpoint` when the provider depends on
  `inputs.cluster.k8s_version`.
- Added `nebius-cxcli acceptance-test smoke` and
  `nebius-cxcli acceptance-test benchmark` as explicit post-deploy validation
  surfaces, with deterministic target-scoped reports:
  `cluster-inventory-report-<target>.json`,
  `deploy-gpu-stack-readiness-report-<target>.json`,
  `deploy-gpu-visibility-report-<target>.json`,
  `deploy-smoke-report-<target>.json`,
  `acceptance-smoke-report-<target>.json`,
  and `acceptance-benchmark-report-<target>.json`. JSON detail reports now
  include `test_purpose`, `mode`, `scope`, `kind`, and `target_ref` metadata
  so copied reports remain self-describing. Required Soperator deploy smoke is
  now a fast Kubernetes deployment snapshot that checks the
  `soperator-manager` Deployment, jail storage objects, Pending Soperator
  pods/events, target `SlurmCluster`, and `NodeSet` resources; it waits only
  through bounded first-run storage/pod startup and does not wait for full
  Slurm availability or start Slurm jobs. Exhaustive
  all-node Slurm smoke moves to
  `acceptance-test smoke --suite slurm`, and K8s/Slurm NCCL performance work
  moves to explicit `acceptance-test benchmark` runs. After a suite is selected,
  smoke and benchmark both run all generated targets when `--target` is omitted. The
  Soperator validation JSON detail report schema remains
  `nebius-cxcli-soperator-cluster-validation/v2`; it stores command output as
  line arrays and keeps structured per-partition `partition_hostnames` and
  `gpu_allocations` evidence so thousand-node clusters remain inspectable
  without parsing escaped multiline strings. Acceptance GPU allocation entries
  now record whether each Slurm job proved GPU visibility through `nvidia-smi`
  or through NVIDIA proc-driver plus `/dev/nvidia*` device evidence, avoiding a
  false failure when the Slurm jail exposes an unusable `nvidia-smi` stub.
- Soperator validation now enforces mode-specific report filenames, so stale
  specs cannot write acceptance smoke or benchmark results into
  `deploy-smoke-report-<target>.json`.
- Clarified `acceptance-test --help`, `acceptance-test smoke --help`, and
  `acceptance-test benchmark --help` so the examples show JSON-only reports,
  run-only benchmark overrides, and explicit suite selection for K8s NCCL on
  Soperator-owned GPU targets during maintenance, and aligned README command
  examples/flag inventory with the no-Terraform-backend acceptance-test
  handoff contract.
- Added a README `Acceptance Testing` section that explains post-deploy smoke
  versus benchmark runs, target handoff, report outputs, and the runtime
  difference between `k8s-nccl` and `slurm-nccl`.
- Fixed deploy-time GPU visibility validation so fresh `gpu-validation`
  namespaces no longer race Kubernetes creation of the implicit `default`
  ServiceAccount. cxcli now applies the namespace before the sampled CUDA pods,
  creates a dedicated `cuda-smoke-validation` ServiceAccount, and has the
  pods explicitly use it with token automount disabled.
- Fixed the Soperator `create` wizard so generated worker shards use
  `worker_node_groups.<worker>.autoscaling.enabled` as the per-shard Infra/MK8s
  worker autoscaling toggle. Answering `true` automatically writes the matching
  `worker_node_groups.<worker>.ephemeral_nodes.enabled=true` and asks min/max;
  the max prompt defaults to the generated shard capacity, such as `4` for a
  single CPU worker shard created from `worker_cpu_total_nodes=4`. Answering
  `false` clears the same shard's autoscaling bounds and writes
  `ephemeral_nodes.enabled=false`. The wizard now asks
  `worker_ephemeral_nodes.suspend_time_seconds` only after at least one shard has
  autoscaling-backed ephemeral nodes enabled. Multi-shard worker layouts now
  get a synthetic bulk apply-to-all wizard choice for all CPU worker shards,
  all GPU worker shards, or all worker shards in mixed CPU+GPU layouts. The
  mixed-layout helper is shown as `all_worker_shards_apply_to_all`, defaults to
  `true`, writes only canonical per-shard controls when accepted, and saves no
  bulk key.
  `worker_*_nodes_per_group` values now fail fast when they exceed the selected
  profile's per-group limit instead of silently clamping the generated shard
  size.
- Fixed Soperator deploy smoke validation so first-run storage convergence no
  longer fails immediately on transient `jail-pv` `FailedMount` Pending pods.
  The validation now waits for `jail-pvc`/`jail-pv` binding, `jail-mount`
  DaemonSet readiness, and storage-related Pending pods to clear before final
  sign-off, while persistent Pending pods report Kubernetes event causes such
  as `FailedMount`.
- Fixed SFS/Soperator wizard output so the component-level filesystem `type`
  prompt is ordered before generated `jail`, `controller-spool`, and
  `accounting` fields, and mapped SFS configs prune stale single-filesystem
  `name`, `size_gib`, and `mount_tag` inputs.
- Clarified Soperator `shape-default` partition documentation so CPU-only,
  GPU-only, and mixed profiles match the rendered default partition contract,
  while the internal `hidden` partition is documented only as render-time
  ActiveChecks plumbing.
- Fixed `create <deployments-root>` so the customer-repository privacy warning
  prints only when the target deployments root is inside a git repository, not
  for operator-local non-git folders.
- Fixed Soperator CPU worker NodeSet materialization so profile-managed
  non-GPU workers request a host-sized CPU/memory slice from the selected MK8s
  worker preset and advertise matching Slurm CPU topology. This preserves the
  one Slurm worker pod to one Kubernetes worker VM contract and gives MK8s
  autoscaling real scheduler pressure for CPU-only worker pools.
- Fixed sharded Soperator worker NodeSet names so generated NodeSets use the
  matching MK8s worker shard key (`worker-0`, `worker-cpu-0`) instead of
  duplicating the template prefix (`worker-worker-0`,
  `worker-cpu-worker-cpu-0`).
- Fixed Soperator smoke validation for ephemeral worker clusters so the
  one-task `srun` check allows a longer Slurm resume window when the selected
  partition is backed by cloud/powered-down workers.
- Fixed Soperator GPU worker autoscaling from zero so cxcli applies Soperator,
  runs the required Soperator smoke validation first to create Slurm/GPU
  allocation pressure, and only then runs MK8s GPU validations. This keeps GPU
  stack readiness from failing on an intentionally empty scale-to-zero worker
  pool while still surfacing Soperator/Slurm resume failures from the smoke
  report. Also downsized generated GPU worker `nodeConfig.static` CPU topology
  when a selected preset, such as `1gpu-16vcpu-200gb`, has fewer vCPUs than the
  profile template.
- Changed Soperator production worker sizing to shape-specific fixed capacity
  plus per-generated-shard `worker_node_groups` controls. cxcli now writes
  disabled `autoscaling` and `ephemeral_nodes` controls for each generated
  worker shard, such as `worker-cpu-0` and `worker-gpu-2`; enabled shard
  autoscaling renders K8s autoscaling min/max instead of fixed `node_count`.
  Enabled shard `ephemeral_nodes` requires that same shard's autoscaling,
  renders upstream Soperator ephemeral NodeSet fields, derives
  `initialNumberEphemeralNodes` from the shard's autoscaling `min_node_count`
  for CPU workers, raises GPU worker shards to at least one initial active
  worker when max capacity is positive so Soperator can seed GPU libraries into
  the jail, and writes finite non-negative global `slurmConfig.suspendTime` from
  `worker_ephemeral_nodes.suspend_time_seconds`. Legacy worker autoscaling and
  `worker_ephemeral_nodes.enabled` helpers now fail fast.
- Refactored lifecycle report naming under the single `generated/reports/`
  folder. `upgrade node-template` now writes
  `upgrade-node-template-report.md` / `.json` after readiness verification,
  `upgrade node-group --execute --approve` writes
  `upgrade-node-group-report.md` / `.json` at the approved pre-mutation gate,
  external Soperator source discovery is now
  `soperator-clusters/<cluster-key>/discovery/manifest.json`, external upgrade
  uses `soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md` /
  `report.json`, and cxcli-managed Soperator chart upgrades use
  `soperator-clusters/<cluster-key>/soperator-upgrade/report.md` / `report.json`;
  the old generic report names are not kept as aliases.
- Clarified `soperator upgrade` dry-run output: the wizard shows the current
  Soperator chart version while prompting for `--to-version`, defaults that
  prompt to the active `component_sources.yaml` Soperator chart pin, keeps the
  repeat command copy/paste-ready with selected non-interactive flags, and
  postflight now writes command-owned Soperator upgrade reports instead of
  refreshing `deploy-report.md`.
- Fixed live quota/capacity auth selection so operator IAM tokens, SDK config,
  and Nebius CLI tokens win over runtime service-account env vars when
  `prefer_operator_auth=True`, preventing project-scoped runtime auth from
  masking tenant-scope quota and Capacity Dashboard reads. Raised the Nebius
  Python SDK floor to a version that includes the `capacity.v1` resource-advice
  API used by those checks.
- Hardened live-operation failure handling across Soperator migration, MK8s
  destroy recovery, Terraform streaming, runtime secret checks, Grafana token
  probing, Helm readiness, and SDK auth fallback so unsafe or ambiguous states
  fail fast instead of silently proceeding.
- Made MK8s GPU fabric single-source: Soperator GPU profiles now write
  `inputs.gpu_clusters.<key>.infiniband_fabric` directly, validation rejects
  stale `inputs.node_group_defaults.gpu.infiniband_fabric`, and raw fabric drift
  is blocked during render/deploy/direct Terraform apply with guidance to run
  the new `upgrade node-group ... --dry-run` planner.
- Aligned MK8s/Soperator GPU wizard ordering and live fabric selection: CPU
  defaults are prompted first and required, GPU defaults stay optional so blank
  means no GPU worker shape, cluster-capable GPU presets derive the canonical
  `inputs.gpu_clusters.<key>.infiniband_fabric` from live Capacity Dashboard
  rows without a raw fabric prompt, and validation/quota checks now reject or
  report keyed GPU clusters missing that fabric.
- Changed MK8s GPU preset prompts to use live Capacity Dashboard rows as the
  selectable choices for the selected platform and region. Selecting a
  cluster-capable multi-GPU row now materializes both the Terraform preset and
  `inputs.gpu_clusters.<key>.infiniband_fabric`, while selecting a 1-GPU
  Ethernet-only row materializes only the preset and omits the GPU-cluster
  fabric. Soperator GPU profile-backed creates now also keep regular 1-GPU
  presets selectable when a previous derived fabric exists, ask
  `inputs.node_group_defaults.gpu.reservation.policy`, default it to `AUTO`,
  materialize it into generated GPU worker node groups, and validate edited GPU
  `reservation.policy` values.
- Fixed Soperator profile-backed 1-GPU materialization to clear
  profile-managed `inputs.gpu_clusters` entries and worker `gpu_cluster_key`
  references together. Strict validation now runs the bundled component runtime
  rules before live quota/capacity checks, so stale GPU-cluster references fail
  deterministically instead of being masked by unrelated quota shortages.
- Made MK8s GPU capacity row selection fail closed when live Capacity Dashboard
  rows exist but matching Compute preset metadata is unavailable, preventing
  cluster-capable rows from being silently treated as Ethernet-only.
- Moved MK8s GPU reservation policy before GPU preset selection for both
  profile-backed and plain node-group flows. The selected policy now filters
  live Capacity Dashboard preset/fabric choices (`AUTO`, `STRICT`, `FORBID`),
  and GPU preset menu labels omit redundant vCPU/RAM/GPU parentheticals.
- Fixed MK8s GPU capacity display for selected reservation policies: `FORBID`
  live rows and menu labels now show only regular-vm slots, while `STRICT`
  labels show only reserved slots.
- Improved GPU Capacity Dashboard choice labels and recommendations: live
  advice rows now display VM slots with the selected preset's GPU totals, and
  GPU preset/fabric recommendations prefer matching reserved-capacity rows
  before falling back to regular-vm capacity.
- Folded MK8s Kubernetes-version and OS image rolling updates into
  `upgrade node-template`, making it the single public command for
  Terraform-managed MK8s node-template version, OS, and Nebius-image GPU stack
  updates. The former narrow public upgrade surfaces were removed without
  aliases or compatibility shims.
- Removed the Soperator compatibility redirect from
  `upgrade helm-chart apps:soperator@<target>`; Soperator chart upgrades now
  fail fast there and point to the single canonical
  `soperator upgrade <config.yaml> --target <target> --to-chart-version <version>`
  command.
- Exposed the live Nebius MK8s compatibility-matrix summary in infra upgrade
  plans: `upgrade node-template` now prints the returned OS and
  `drivers_preset` choices for each selected platform before non-dry-run
  mutation.
- Made `upgrade node-template` wizard-capable: running it with only
  `config.yaml` now prompts for the managed MK8s target, target Kubernetes
  version, optional node group, compatible OS, required Nebius-image GPU stack,
  dry-run/apply choice, strategy, drain timeout, and validation choice, while
  `--no-interactive` keeps missing required values as fail-fast automation
  errors.
- Clarified guided upgrade prompts so blank `node_group` input says it selects
  all managed node groups, `drain_timeout=auto` shows all strategy defaults,
  and `force-delete` states that the 10m auto drain can end with remaining Pod
  and old-node deletion.
- Fixed guided `upgrade node-template` wizard backtracking so pressing `q` at
  target selector, Kubernetes version, node-group, OS, GPU-stack, strategy,
  safe-surge count, drain-timeout, validation, or apply-confirmation prompts
  returns to the previous prompted step instead of cancelling the wizard.
- Added a post-wizard `upgrade node-template` status spinner while cxcli
  discovers live MK8s state, checks preflight blockers, and builds the upgrade
  plan before printing it.
- Made printed upgrade repeat dry-run commands preserve explicit dry-run
  choices, including `--no-interactive`, `--drain-timeout auto`, default
  safe-surge count, and selected validation/auth skip flags, so removing only
  `--dry-run` keeps the apply command aligned with the reviewed plan.
- Exposed MK8s upgrade safe-surge count in CLI and guided wizard flows:
  `--strategy-max-surge-count` now controls the temporary extra nodes per
  active node group for `--strategy safe-surge`, defaults to `1`, and is
  rejected for non-safe-surge strategies.
- Added a strict `upgrade node-template --strategy safe-surge` quota/capacity
  preflight before any staged source write or Terraform mutation. The command
  now estimates the temporary surge nodes for the selected node-group stages
  and blocks on confirmed shortages, unknown limits, coverage gaps, or lookup
  errors instead of discovering missing quota after the control plane has
  already been updated. GPU node groups are checked against the same
  InfiniBand fabric and `reservation.policy` as the selected node group, and
  Capacity Dashboard shortages now print capacity/fabric details instead of
  looking like generic quota allowance failures.
- Fixed quota preflight failure messages raised through the CLI error path so
  Rich color markup such as `[#ffbf00]Quota warning:[/]` is not printed
  literally, while normal terminal quota reports remain colorized.
- Removed confusing version-label wording from `upgrade` help and docs while
  preserving the documented supported upgrade scope.
- Added create/component-add Helm chart version overrides for app charts:
  `component_sources.yaml` remains the default version source, interactive
  `create` and `component add` prompt for `apps.charts[].version` before the
  longer app config phase, and non-interactive `create` plus `component add`
  accept `--app-version <app-id>=<chart-version>`. `component add` also accepts
  `--app-namespace` and `--app-releasename` for app rows added by that
  operation. With source validation enabled, cxcli resolves requested
  non-catalog chart versions before writing `config.yaml`, so a published
  rollback/test package such as `soperator=4.0.1-ps.2` can be selected
  explicitly while unknown versions fail fast.
- Improved create/component-add and Soperator onboarding performance by
  skipping Helm chart default lookups when app rows have no explicit scalar
  values to prune, and by caching parsed Soperator migration-profile data
  during source-version checks.
- Fixed source validation against Terraform 1.15 remote module probes: cxcli now
  treats Terraform's post-download missing-required-argument diagnostic as a
  successful module download for introspection, and resolves Git module
  subdirectories such as `//platform-infra/modules/mk8s` before checking for
  `.tf` files.
- Fixed static Soperator Helm rendering from OCI chart sources so Helm status
  lines such as `Pulled:` and `Digest:` are not carried into post-Flux
  Kubernetes manifests or handed to `kubectl apply`.
- Fixed first-install Soperator smoke validation timing by waiting for the
  rendered SlurmCluster to become `Available` before checking Slurm pod
  scheduling and login-pod commands.
- Fixed cxcli-managed Soperator Slurm smoke checks on Nebius Slurm 25 images by
  pinning `PluginDir=/usr/lib/x86_64-linux-gnu/slurm` in the managed Soperator
  profiles while keeping the standalone Helm chart default unset for direct
  Helm installs with different images.
- Fixed cxcli-managed Soperator GPU NodeSet defaults so 8-GPU worker profiles
  expose/request 32 Slurm CPUs, matching the chart's `DefCpuPerGPU=4` default
  and avoiding impossible CPU under-reporting for GPU jobs.
- Made the Soperator Slurm NCCL smoke validation probe allocatable GPU count on
  the selected 8-GPU Slurm nodes before running `all_reduce_perf_mpi`, so the
  report reflects live Slurm policy and skips the full benchmark when fewer than
  8 GPUs per selected node are allocatable.
- Fixed Soperator mixed-profile rematerialization so configs edited from the
  generated GPU baseline to `nebius-mixed-v1` prune stale managed `worker`
  state, materialize `worker-cpu` and `worker-gpu` MK8s groups, and render only
  the matching `worker-cpu` / `worker-gpu` chart NodeSets instead of compound
  names such as `worker-gpu-worker-cpu`.
- Aligned Soperator-created SFS defaults in the `create` wizard: after the
  operator enters the MK8s cluster name, generated jail/controller/accounting
  SFS filesystem `name` and `mount_tag` defaults use `<cluster-name>-<role>`
  instead of the initial `mk8s` placeholder or role-only mount tags.
- Tightened destructive/create-path guardrails and local credential writes:
  MK8s live resource-name preflight now trusts only typed Nebius `NOT_FOUND`
  status, Soperator node-group cleanup no longer swallows arbitrary errors
  containing "not found", MK8s upgrade PDB preflight flags any zero-disruption
  PDB with expected pods, WireGuard and runtime-auth private-key/cache files are
  written atomically with `0600` permissions before content is persisted, Helm
  repo search aliases are stable across processes, quota limit/usage values are
  coerced before arithmetic, and Soperator child-chart value writes preserve
  existing `-`/`_` key variants and explicit MysteryBox sync disables. The auth
  docs now call out the intentional plaintext local runtime-auth cache and
  rotation boundary. MysteryBox ESO connectivity reports now persist only an
  allow-listed pass/fail summary, without raw credential Secret specs,
  ExternalSecret specs, resource names, or controller log lines.
- Hardened cxcli safety paths: managed Terraform/Flux downloads now verify
  published SHA256 manifests, use bounded reads, atomically install cached
  binaries, and reject corrupted cache entries; local SMTP settings and
  external Soperator upgrade checkpoints and cluster-scoped upgrade reports are
  written atomically; Nebius SDK pagination loops fail fast on repeated page tokens;
  and CLI-sourced IAM tokens are no longer written into process-global
  environment variables.
- Added `nebius-cxcli soperator upgrade <config.yaml>` as the canonical
  cxcli-managed Soperator chart upgrade path. It validates the current bundle,
  runs live Soperator/Slurm preflight, updates and rerenders the chart version,
  applies the selected target Flux bundle, verifies the static Soperator chart
  version on live Kubernetes objects, reruns required Soperator/Slurm
  validation, and writes command-owned Soperator upgrade reports.
- Changed the `ext-soperator upgrade` no-upgrade-actions route from a red
  generic error paragraph to a note-style handoff with copy-paste
  `validate`, `render`, and `deploy` commands on separate first-column lines.
- Standardized CLI follow-up command output so suggested commands from create,
  component edit, render, Soperator onboarding, deploy, quota, WireGuard, and
  upgrade guidance are printed as separate first-column copy-paste lines
  instead of inline or indented prose.
- Styled CLI copy-paste command helper lines in a distinct bold cyan terminal
  color across create, component edit, render, Soperator onboarding, deploy,
  quota, WireGuard, and upgrade guidance while keeping the command text
  directly copyable.
- Renamed the external Soperator upgrade report and execute summary field
  from `Mutation performed` to `Upgrade performed` so completed upgrades are
  easier to recognize.
- Fixed `render` so the transactional `generated/` replacement preserves
  command-owned runtime reports such as `deploy-report.md`, external Soperator
  `soperator-clusters/<cluster-key>/discovery/manifest.json`,
  `soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md`,
  `upgrade-node-template-report.md` / `.json`, `upgrade-node-group-report.md` /
  `.json`, `soperator-clusters/<cluster-key>/soperator-upgrade/report.md`, and
  JSON detail reports referenced by those Markdown reports, while still removing
  unrelated stale report files from the generated bundle.
- Reorganized the README navigation to group common user tasks near the table
  of contents, keep command examples under `Commands`, and move Soperator
  Slurm scheduling guidance under `Soperator Commands` instead of the catalog
  schema reference.
- Clarified that external Soperator ActiveChecks and `wait-for-active-checks`
  should be handled as maintenance-window diagnostics rather than silently
  disabled by migration; cxcli removes stale source-family check workloads
  during takeover but does not automatically mutate operator-owned external
  ActiveChecks without an explicit checkpointed restore contract.
- Added a checkpointed cxcli-managed Soperator ActiveChecks lifecycle for non-dry-run
  `soperator upgrade`: when `values.soperator-activechecks.enabled` or
  `values.soperator-activechecks.waitForChecks.enabled` is true in cxcli-owned
  config, cxcli snapshots the original values, suspends them for the upgrade
  window, patches matching live ActiveCheck CRs, deletes matching
  already-launched check workloads, fails closed when live ActiveChecks cannot
  be inspected, restores the original values after postflight validation, and
  writes `soperator-clusters/<cluster-key>/soperator-upgrade/report.md` /
  `report.json` restore evidence. Reruns now reuse an unfinished upgrade
  checkpoint to restore the original ActiveChecks values even if a previous
  interruption left `config.yaml` temporarily suspended, and write the upgrade
  checkpoint/report atomically.
- Documented when operators should use the structured `upgrade` command instead
  of direct `config.yaml` edits, including covered MK8s, VM OS image, and
  target-scoped Helm chart upgrade layers.
- Added downgrade guardrails for day-2 upgrades. MK8s version downgrade targets
  remain refused, external Soperator node-template rechecks now fail if the
  accepted target is below the live control-plane version, and Helm chart
  upgrade plans warn when the requested chart version appears lower than the
  current configured version while still allowing operator-controlled rollback
  or recovery.
- Changed the bundled Soperator portable app source to the published Nebius OCI
  chart at version `4.0.2-ps.1`.
- Changed bundled Soperator production profiles to leave worker `slurmd` and
  `munge` image selection to the selected chart defaults instead of duplicating
  image tags in `component_cli_settings.yaml`. The same profiles now default
  `system` to autoscaling from 3 to 5 nodes, and default `controller`, `login`,
  and `accounting` to two fixed nodes each. Their catalog-owned CPU role shape
  now uses `cpu-d3/32vcpu-128gb` as the production minimum.
- Clarified external Soperator upgrade status output so external-upgrade-owned
  replacing or cordoned rollout nodes are reported in a separate colored
  `Nodes:` section as transition activity instead of `problem nodes`, while
  node-group readiness stays under `Node groups:` and unrelated NotReady nodes
  still use the problem-node signal.
- Hardened external Soperator node-template resume after near-boundary Nebius
  node-group update timeouts: service-role updates now use node-count-aware
  wait budgets, and reruns reconcile failed checkpoints from live-current node
  groups without issuing duplicate template updates.
- Hardened external Soperator target GPU-stack reconciliation after long Helm
  client timeouts: if a target GPU Operator or Network Operator
  `helm upgrade --install --wait` subprocess times out but the live release and
  rendered workloads are already ready, cxcli accepts the live-ready state and
  checkpoints the phase instead of forcing a redundant rerun.
- Documented the Soperator day-2 chart source rule: `repo: ''` keeps static
  local rendering, while an explicit parent OCI repo plus version uses the
  published package before running `render` and `deploy`; Soperator OCI sources
  still render into the static post-Flux manifest path to avoid Helm release
  Secret size limits.
- Retried transient Kubernetes API server `etcdserver: request timed out`
  failures while applying post-Flux Soperator custom resources, so large static
  Soperator upgrades can continue after a short API leader-change timeout.
- Clarified the post-apply GitOps handoff: missing Flux bootstrap is now an
  informational optional follow-up, because local direct apply is a supported
  operating mode for customers that do not want continuous GitOps sync.
- Added MK8s VPC subnet capacity guidance and validation. The wizard now warns
  when a selected explicit subnet CIDR cannot fit the entered node count or
  autoscaling maximum, and `validate` fails live or planned explicit subnets
  that do not provide enough `/24` Pod allocation blocks plus rolling-update
  headroom, including planned VPC subnet bindings on explicit node-group subnet
  overrides. The message also clarifies that
  `inputs.cluster.kube_network.service_cidrs` is Service ClusterIP space, not
  Pod IP space.
- Fixed VPC networking preflight and provider-option metadata for existing
  explicit subnets created from prefix allocation requests such as `cidr: /16`;
  cxcli now uses the resolved live subnet CIDR instead of treating the prefix
  request as a malformed pool CIDR.
- Fixed generated deploy reports so the standalone `infra:vpc` component appears
  in the infra component status list and consuming infra rows show their
  row-level planned VPC network/subnet bindings.
- Improved Soperator-owned Slurm NCCL validation. The Soperator validation now
  replaces the old one-rank smoke with one Slurm-owned
  `mpirun /usr/bin/all_reduce_perf_mpi` benchmark that uses selected 8-GPU Slurm
  nodes, parses the 2G/4G/8G large-message `busbw` rows, records
  `avg_large_message_bus_bandwidth_gbps` plus per-size bandwidths in the JSON
  check, and includes that value in deploy/migration summaries when Kubernetes
  NCCL is skipped because Soperator workers already own the Ready GPUs.
  One-GPU Slurm clusters report the Slurm NCCL benchmark as skipped.
- Fixed external Soperator upgrade validation resume. The Slurm NCCL
  validation now holds the selected multi-node allocation with `salloc` and
  launches the MPI benchmark once from a nested one-task `srun`, so Slurm does
  not collapse the allocation to one node or start duplicate launchers. When a
  previously pending migration phase succeeds on rerun, the checkpoint now
  clears the stale pending phase and reason immediately before later phases
  continue.
- Improved external Soperator upgrade completion handoff. After a fully
  completed `ext-soperator upgrade --execute`, cxcli now performs a live
  post-upgrade discovery refresh and rewrites `config.yaml` plus
  `generated/reports/soperator-clusters/<cluster-key>/discovery/manifest.json`
  into the same deploy-owned onboarding shape that a rerun of
  `ext-soperator onboard` would produce, while leaving pending or
  still-external-upgrade-owned plans blocked from normal deploy. The README and
  design guide now call out
  `generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md`
  `Pending phase: none` as the upgrade resume-complete marker before normal
  render/deploy reconciliation.
- Fixed external Soperator onboarding refresh for already-upgraded targets when
  the live canonical Soperator release matches the cxcli-pinned chart version
  but that exact target release has no committed migration profile; cxcli now
  classifies the target as `existing-soperator-target` and keeps same-name
  stale source-family Helm records informational.
- Fixed external Soperator onboarding for source Soperator patch releases that
  are newer than the exact committed profile history but still belong to a
  known major migration family; cxcli now uses the major-generation profile
  such as `v3-to-target` with a warning instead of blocking on source-version
  selection.
- Refreshed generated Soperator migration profiles from the current upstream
  release set, adding committed profile rows for `3.0.6`, `3.0.7`, and
  `4.0.2`.
- Improved external Soperator deploy validation and live cleanup. Local
  post-Flux apply now prunes stale target-instance `SlurmCluster`, `NodeSet`,
  and `NodeConfigurator` custom resources instead of only stale
  `NodeConfigurator` records, removes legacy source-family ActiveChecks
  CronJobs/jobs/pods that can keep recreating pending v1/v2 check pods after
  adoption, retires profile-derived old source-family Helm release records such
  as `flux-system-soperator-fluxcd-*` and `soperator-fluxcd-values`, and
  required Soperator smoke validation now reports active old source Flux
  HelmReleases and pending Soperator pods as explicit evidence before the
  downstream Slurm checks fail. Source Flux cleanup now treats already-deleted
  HelmRelease and Kustomization records as idempotent cleanup success during
  reruns.
- Fixed external Soperator adoption accounting database defaults. Adopted
  chart-managed MariaDB now defaults to `compute-csi-default-sc` with
  `ReadWriteOnce` storage, and onboarding/migration reuse a discovered live
  MariaDB PVC shape when one exists instead of rendering the Slurm shared
  filesystem storage class for the database PVC.
- Fixed external Soperator adoption for live clusters whose existing
  `SlurmCluster` is named `mk8s` while the cxcli target id is different.
  Soperator onboarding now preserves the discovered live cluster name in adopt
  mode instead of materializing a duplicate target-named `SlurmCluster`, which
  could leave new target NodeSets pending and fail deploy-time Soperator smoke
  validation. Adopted worker NodeSet GPU resources are now normalized from
  Kubernetes `nvidia.com/gpu` quantities into integer Soperator `gpu` chart
  values.
- Fixed `ext-soperator onboard` source-version prompting when discovery finds
  both a compatible Soperator release version and another Soperator-like Helm
  release with non-standard identity; interactive onboarding now asks the
  operator to confirm the source version instead of leaving a
  `source-version-required` finding in the final summary. If the compatible
  release is already the pinned target and the other same-name record is an
  older source-family Helm chart, onboarding now records it as informational
  stale evidence in the saved report instead of prompting for a source version
  or printing it as selected onboarding work.
- Fixed external Soperator upgrade stale Helm cleanup for same-name source
  records left behind after target takeover. The post-upgrade Helm check now
  retires stale source-family records before validation hold and target
  readiness lookup, and deletes only the stale Helm storage revision,
  preserving the current target release revision.
- Improved `ext-soperator onboard` rerun idempotency for failed or partially
  completed external Soperator upgrades. Nebius `--cluster-id` onboarding now
  enriches source discovery with control-plane and node-group template
  inventory by node group, including Kubernetes version, node OS image, and GPU
  driver preset, and omits `upgrade-external-node-template` only when that
  provider evidence proves every discovered group is already aligned.
- Tightened external Soperator onboarding guidance and deploy safety. Onboarding
  and render hints now explain whether the next path is `render` then `deploy`
  or `render` then `ext-soperator upgrade`, including cases where storage and
  compute are kept but Soperator or external node-template upgrade work
  remains, and the interactive storage/compute mode prompts now tell operators
  to choose the aligned SFS/node-group options when they are unsure because
  cxcli can keep compatible existing layouts automatically. `deploy` now
  refuses selected external-upgrade-required external Soperator targets before
  Terraform/Flux preflight, checks the source `config.yaml` as a fail-closed
  guard for older rendered bundles, and prints the required upgrade
  dry-run/execute commands.
- Improved `ext-soperator upgrade --dry-run` output by color-highlighting
  topic labels, required-action statuses, migration phases, executor contracts,
  and execution mode in interactive terminals.
- Changed `ext-soperator upgrade` to fail fast when the accepted onboarding
  action set has no external-upgrade-owned work; deploy-owned remediation such as
  target GPU stack alignment now reports the render/deploy route instead of a
  no-op migration plan.
- Improved approved `ext-soperator upgrade --execute` progress reporting with
  an interactive spinner, richer MK8s node-group status, bounded down/upgrading
  node details, named Slurm worker state summaries, and color-highlighted
  degraded/down states; every status line now includes the phase id,
  human-readable phase label, and overall phase health before component
  details.
- Hardened upgrade resume behavior for long MK8s rollouts. Accepted
  `ext-soperator upgrade --execute` node-group update timeouts now reconcile
  live state, checkpoint still-rolling external node-template updates as
  pending even when the requested template fields are not visible yet, and
  resume without duplicate Nebius update calls; Terraform-managed
  MK8s `upgrade` reruns that only wait for an already-requested rollout now
  still perform the final rendered apply needed to restore temporary
  `zero-surge`, `safe-surge`, or `force-delete` node-group strategies.
- Added final MK8s readiness checks for `upgrade node-template` and
  `ext-soperator upgrade --execute`; commands now re-read live control-plane
  and node-group state before reporting success, including Nebius OS image and
  GPU `drivers_preset` / CUDA stack where applicable. Final checks now require
  provider node-group status with ready, target, and total node counts instead
  of treating matching spec fields alone as ready, and completed external
  migrations emit a baseline MK8s cluster/node group readiness check even when
  no node-template action was selected.
- Added post-action Helm readiness checks for `upgrade helm-chart` and
  `ext-soperator upgrade --execute`; External Soperator upgrade now verifies the
  target chart workloads, suspends old source-family Flux Kustomization desired
  state, deletes suspended old source-family Flux HelmRelease records, prunes
  old operational Soperator resources, preserves shared/storage resources, and
  removes stale source-family Helm release records before reporting completion.
  Managed Helm chart upgrades now require the generated target handoff before
  running live readiness verification.
- Fixed external Soperator upgrade takeover for profiled legacy v1 and v2
  releases. Migration profiles now declare source admission webhooks to delete
  and source controller deployments to scale down before target compute
  reconciliation, preventing old source controllers or stale source webhooks
  from racing or blocking the pinned target chart over `NodeSet`, `SlurmCluster`,
  and worker `StatefulSet` objects while leaving storage/shared resources for
  the normal retirement phase.
- Fixed `ext-soperator upgrade --help` so the rendered epilog includes the
  completed remediation/upgrade/cutover rerun recheck contract.
- Changed the generated report artifact contract from `generated/inventory/` to
  `generated/reports/`. Code paths now use `reports_dir` for deploy,
  validation, notification, and external Soperator upgrade reports, and new
  generated bundles scaffold `generated/reports/` without a compatibility
  alias.
- Fixed preserved-worker external Soperator cutover for heterogeneous worker
  shapes. `ext-soperator upgrade --execute` now samples one live worker pod per
  preserved NodeSet for Slurm CPU/socket/core/thread topology, strips
  source-era chart-owned worker mounts, normalizes target operator affinity to
  Slurm role labels, and resumes Slurm nodes left drained after cutover.
- Fixed external Soperator GPU-stack reconciliation so direct
  `ext-soperator upgrade --execute` Helm upgrades also apply catalog-owned
  post-render patches such as the Network Operator `NicClusterPolicy`
  `rdma/shared_device` overlay. Reruns now verify those live post-rendered
  fields before considering `target-gpu-stack-remediation` complete.
- Improved `ext-soperator onboard` GPU-stack discovery on reruns. Onboarding
  now inspects live GPU/Network Operator Helm releases, NVIDIA ClusterPolicy
  readiness, scheduler-visible GPU/RDMA resources, and Nebius driver labels so
  healthy clusters report `gpu-stack: verified` instead of implying that every
  GPU target needs active remediation.
- Improved Soperator testing split: deploy-time Soperator testing now stays on
  fast Kubernetes resource snapshots, while `acceptance-test smoke --suite slurm`
  owns Slurm CLI, `srun`, all-node hostname, and all-node GPU allocation checks.
  Slurm node status still treats `inval` as unhealthy in the explicit
  acceptance smoke path, Slurm GPU allocation reports include the per-node
  evidence source, and Slurm NCCL remains reserved for explicit
  `acceptance-test benchmark` runs.
- Fixed `deploy-report.md` Soperator GPU validation summaries so Kubernetes
  GPU visibility scheduler skips caused by Soperator worker pod GPU reservations
  remain visible as GPU visibility skips instead of being overwritten by Slurm
  allocation evidence from a different test purpose.
- Changed local `deploy` for cxcli-managed Soperator targets to stage app
  reconciliation: cxcli now applies platform/GPU operator Flux resources and
  runs MK8s inventory, GPU stack, and GPU visibility validations before applying
  the full Soperator bundle that starts Slurm worker pods; NCCL/performance work is
  left to explicit `acceptance-test benchmark` runs.
- Changed `ext-soperator onboard` non-interactive identity flags: onboarding now
  selects the Nebius MK8s cluster with `--cluster-id`, derives temporary
  kubeconfig access through the Nebius API by default, and uses optional
  `--target-id` only for the cxcli logical target alias. Install/adopt-only
  next-step hints now prefer plain `deploy <config.yaml>` and document
  `deploy --target <target-id>` only as a narrowing selector.
- Tightened external Soperator rerun idempotency. No-op `ext-soperator
  onboard` reruns now keep stable source discovery bundles instead of churning
  timestamps that invalidate upgrade checkpoints, and `ext-soperator
  upgrade --execute` rechecks completed selected actions against live state so
  missing GPU-stack releases, node-template drift, aligned-SFS gaps, or target
  cutover drift are retried instead of skipped solely because the local
  checkpoint said the phase had completed.
- Changed `ext-soperator upgrade --execute` to own onboarded external MK8s
  control-plane and node-template upgrades through direct Nebius updates:
  control plane first, service-role and worker node groups with zero-surge by
  default or safe-surge when selected, original strategy restore, and surge
  quota preflight only for active safe-surge service groups or worker waves.
- Hardened external Soperator chart takeover by suspending legacy Flux
  HelmReleases before applying the cxcli target chart, forcing server-side CRD
  conflict resolution, retrying transient target webhook startup failures, and
  resuming partial cutovers when the source login pod has already been removed.
- Fixed `keep-existing-storage` external chart takeover so live chart-owned
  SFS/local PersistentVolume nodeAffinity selectors are preserved in target
  Helm values instead of attempting an immutable PV selector update.
- Fixed `keep-existing-storage` external chart takeover so discovered live PVC
  request/capacity and PV capacity are treated as lower bounds, preventing
  onboarded Soperator storage from rendering a smaller PVC/PV size.
- Fixed external Soperator chart takeover so live Soperator placement labels
  produce explicit `apps.charts[].placements.*`, preventing service-role
  operators from being rescheduled onto an unrelated CPU worker group during
  adoption.
- Fixed external Soperator onboarding to infer the mixed Soperator profile from
  live `worker-cpu`/`worker-gpu` labels, replace stale generic worker mappings,
  and remove stale generated `worker-<node-group-id>` NodeSets on rerender.
- Fixed install/adopt-only external Soperator onboarding so reruns sample live
  worker NodeSet CPU/socket/core/thread topology from a running `slurmd` pod
  and preserve the normalized `values.nodesets[].nodeConfig.static` through
  render/deploy instead of falling back to compact profile worker topology.
- Fixed adopted worker NodeSet rendering so source/profile `slurmd`, `munge`,
  and `sssd` image overrides are not reintroduced; target Soperator chart
  defaults now own worker image tags during external chart takeover.
- Fixed adopted Soperator chart values to make Pyxis optional and clear the
  importer path so incompatible legacy Pyxis importer options do not stop
  `slurmd` during chart takeover.
- Fixed `keep-existing-compute` external chart takeover so source worker
  NodeSet names and partition references such as `worker-gpu` and `worker-cpu`
  are preserved instead of collapsing them into a new synthetic `worker`
  NodeSet, and stale source-era camelCase `ephemeralStorage` resource keys are
  removed from adopted worker NodeSet CRs so target worker Pods can be created.
- Tightened external Soperator upgrade completion so completed-checkpoint
  reconciliation waits for target worker NodeSets to report desired-ready
  replicas before returning `Pending phase: none`.
- Changed `ext-soperator upgrade --execute` validation hold to run the
  target-scoped `deploy.targets[].deployment_testing.mk8s_gpu.*` checks for the
  onboarded external target, including operator readiness and bounded GPU
  visibility when enabled. NCCL/performance work remains an explicit
  `acceptance-test benchmark` run. The MK8s GPU rollup is written to
  `generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md`;
  `generated/reports/deploy-report.md` is refreshed as a secondary
  deploy-compatible summary.
- Added required Soperator/Slurm smoke validation for enabled Soperator
  targets. `deploy` now records a `soperator_cluster_smoke` JSON report and
  includes it in `deploy-report.md`; `ext-soperator upgrade --execute` runs
  the same smoke validation during validation hold and writes
  `generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md`
  with migration phase, remediation, upgrade, layout, validation, and event
  summaries.
- Clarified the successful `ext-soperator onboard` config-only note so
  external-upgrade-required targets point to the Soperator-specific next steps instead
  of the generic deploy/destroy follow-up wording.
- Fixed Soperator onboarding source-version detection for legacy controller
  installs where the source operator Helm chart is released as
  `soperator-controller` in `soperator-system` with chart identity
  `helm-soperator`.
- Expanded the successful `ext-soperator onboard` footer to print the selected
  target's next-step command sequence: deploy for install/adopt-only targets,
  or upgrade dry-run and approved upgrade execute for external-upgrade-required
  targets.
- Aligned `ext-soperator --help`, `ext-soperator onboard --help`, and
  `ext-soperator upgrade --help` examples with the complete external-cluster
  sequence, including the no-deploy-before-upgrade warning and external
  node-template update contract.
- Clarified `ext-soperator` help text so `--target` is explicitly the cxcli
  target id from `deploy.targets[].instance_id`, install/adopt-only targets
  point to target-scoped render/deploy and `deploy-report.md`, and onboarding
  flags describe current-context fallback, live storage preservation, and
  source-validation intent.
- Changed Soperator onboarding GPU/RDMA findings from an operator-owned placeholder
  action into target remediation: GPU-enabled external targets now record
  `reconcile-target-gpu-stack`, add the target-scoped GPU Operator and
  Network Operator when GPU-cluster/RDMA inventory is present, persist MK8s GPU
  deploy-time validation defaults, show the target GPU reconciliation in
  `ext-soperator upgrade --dry-run`, and execute it as a checkpointed
  `target-gpu-stack-remediation` phase before Soperator compute/cutover work.
- Renamed External Soperator upgrade execution stop points to pending phases and
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
- Added `nebius-cxcli ext-soperator upgrade <config.yaml>` as the explicit
  external Soperator upgrade command surface. It validates the accepted onboarding
  analysis, reads
  `generated/reports/soperator-clusters/<cluster-key>/discovery/manifest.json`,
  prints
  the target remediation and compute/storage migration plan in dry-run mode, and
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
- Added phase-aware live status output for approved `ext-soperator upgrade
  --execute` runs. Storage phases now report aligned SFS/PVC copy progress
  alongside MK8s and Slurm continuity, while compute/cutover phases report
  MK8s node readiness, Slurm login/queue/node-state health, and Soperator
  SlurmCluster reconciliation as best-effort degradation signals.
- Added a strict external Soperator upgrade quota preflight for approved `--execute`
  runs. The executor now checks net-new aligned SFS storage and net-new
  service-role node groups before any SFS, node-group, or Helm mutation, counts
  safe-surge spare capacity for active service groups and worker waves, and
  fails fast on confirmed shortages, unresolved limits, coverage gaps, or quota
  lookup errors. External service-role and worker template mutations now default
  to zero-surge and check safe-surge spare capacity only when selected, restoring
  each node group's original strategy afterward.
- Consolidated README Soperator command guidance into a visible
  `Soperator Commands` section covering managed create/deploy, external
  onboarding, migration flags, storage/compute migration modes, safety rules,
  and the difference between `upgrade helm-chart` for cxcli-managed Soperator
  rows and onboard+upgrade workflows for external clusters.
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
  preserving worker node groups in place and using external-upgrade-owned external
  node-group updates for template remediation.
- Added a final render helper to successful `render` command output: `Next step: deploy the rendered bundle:`
  followed by a copy-paste `nebius-cxcli deploy <config.yaml>` command line.
  Internal rerenders used by upgrade flows suppress this hint.
- Clarified `component add` help and examples so `--config` is shown as the
  required config path option, target-bound app examples use the plural
  `apps:<chart>@<target>` selector form, and singular `app:` selector errors
  point operators at `apps:`.
- Clarified `upgrade node-template` help so the command list and subcommand
  help call out that Kubernetes version, OS image, and GPU stack move together
  in one combined command with examples below.
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
- Implemented
  `upgrade node-template <config.yaml> [infra:mk8s@<target>] --to-version <major.minor> --to-os <os> [--to-gpu-stack-preset <preset>]`
  for MK8s control-plane, node-group Kubernetes version, node OS, and
  Nebius-image GPU stack rolling updates. The command validates the requested
  tuple through the SDK compatibility matrix, stages control plane first, then
  writes version, OS, and Nebius-image `gpu_stack_preset` together for each
  selected node group in CPU/system-before-GPU order so the group rolls once.
  Automation can request any subset of version, OS, and GPU stack fields;
  omitted values keep the selected live value when unambiguous and compatible.
  The GPU stack flag is required for selected Nebius-image GPU groups and
  rejected for CPU-only or operator-managed GPU selections. Generated-bundle
  GPU stack compatibility validation now honors explicit node-group `version`
  values, so a staged control-plane hop can validate old node templates against
  their pinned node-group version until the node-group stage writes the new
  template.
- Wrapped upgrade dry-run repeat commands across shell-safe continuation lines
  so long config paths and selected flags remain readable and copy-pasteable.
- Added `upgrade node-group` as the single MK8s node-group migration surface for
  platform, hardware preset, CPU/GPU kind, GPU cluster, and InfiniBand fabric
  changes. The command plans one node group at a time, resolves optional
  `--to-fabric` from the canonical current fabric when omitted, checks target
  shape/fabric/reservation quota and capacity before mutation, and prints
  repeatable dry-run and approved execute commands. Current execute writes only
  an approved pre-mutation checkpoint and stops before live replacement/cutover.
- Implemented `upgrade helm-chart` with the same wizard/non-interactive flag
  contract as the other upgrade surfaces. The Helm chart command updates the
  selected target-scoped `apps.charts[]` version, rerenders, validates, and
  applies that target's Flux bundle.
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
  section with a visible table-of-contents entry, strategy
  drain-timeout defaults, copy-paste Kubernetes upgrade examples,
  node-group migration examples, Helm chart upgrade examples, and manual desired-state
  fallback guidance.
- Aligned `upgrade --help` and upgrade subcommand help output with the README
  upgrade examples, including implemented Kubernetes dry-run/strategy
  examples, node-group migration examples, and Helm chart examples.
- Removed public/private endpoint access from the guided MK8s upgrade
  target picker labels so managed MK8s targets are shown by selector only,
  avoiding confusion with external-cluster ownership.
- Clarified guided and explicit MK8s upgrade multi-minor handling with
  upstream Kubernetes guidance that skipped minor upgrades are unsupported.
- Improved guided MK8s upgrade dry-run output by aggregating
  `emptyDir` pod findings into one PVC-aware advisory, printing a repeatable
  dry-run command with the selected arguments, and styling the warnings section
  with the shared amber warning color.
- Suppressed raw Terraform plan dumps during live MK8s upgrade staged
  applies while still running each staged plan as a safety gate before apply.
- Fixed live MK8s upgrade staged Terraform plans when a temporary
  node-group strategy is applied to only the node group currently
  being upgraded.
- Hardened MK8s upgrade ordering by rejecting live node groups that
  are already above the requested control-plane minor, and documented the
  post-upgrade GPU canary, add-on, and rollback boundaries.
- Clarified live MK8s upgrade output so execution stages are labeled
  as per control-plane hop and per node group rather than per node, and
  de-duplicated repeated deploy-validation advisories across nested render and
  validation calls within one upgrade run.
- Clarified MK8s upgrade OS/platform/GPU compatibility blockers so
  node-template and node-group commands are printed as
  runnable follow-up commands where available, while manual
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
  `config.yaml` rows show the active local chart version.
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
- Fixed the Soperator production MK8s wizard so bounded GPU visibility is
  prompted alongside GPU stack readiness instead of being suppressed by the
  Soperator app policy. Soperator ActiveChecks and Soperator DCGM child charts
  remain disabled by default; NCCL/performance work stays under explicit
  `acceptance-test benchmark` commands.
- Fixed the interactive Soperator production-cluster `create` and
  `component add` wizards so the worker layout profile is selected immediately
  after `install_mode`, before MK8s shape/fabric helpers and target GPU
  deployment-testing prompts. CPU-only Soperator profiles now also skip and prune
  the inactive `inputs.node_group_defaults.gpu.*` helper scope instead of
  offering GPU fabric fields, including during direct `config.yaml`
  normalization.
  Soperator onboarding mode now also skips same-transaction `mk8s`/`sfs` infra
  selections so external MK8s onboarding does not create Terraform-managed
  cluster rows. Soperator worker profile materialization now honors
  `worker_nodes_per_group` as the generated MK8s node-group shard size even
  when the profile also uses `worker_total_nodes`. Non-interactive
  `component add soperator@<external-target>` now infers onboarding for
  existing external MK8s targets and repairs missing target-scoped
  Soperator-required app rows without adding Terraform MK8s/SFS rows. External
  onboarding now writes a source-cluster discovery bundle next to the project
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
  unclustered regular-vm capacity.
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
  profiles recompute profile-owned node groups, placements, NodeSets,
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
- Removed the standalone Soperator chart default `PluginDir` override after
  live H100 deployment showed Slurm 25.11 fails when a static path includes a
  directory absent from the selected image. Direct Helm installs keep
  image-specific plugin paths image-owned unless an operator explicitly
  overrides `customSlurmConfig`; cxcli-managed Nebius profiles pin the known
  Nebius image plugin directory.
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
- Fixed MK8s GPU deployment-testing prompts so enabled GPU visibility settings
  materialize their default `max_nodes` cap. Soperator ActiveChecks remain
  opt-in diagnostics rather than production-training defaults, while cxcli keeps
  NCCL as an explicit `acceptance-test benchmark` setting.
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
- Added a benchmark warning when Soperator NCCL ActiveChecks and the cxcli K8s
  NCCL benchmark are both runnable on the same MK8s target, explaining that the
  Slurm NCCL checks and transient Kubernetes `MPIJob` can compete for GPUs/RDMA
  and skew, delay, or skip results.
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
  `cpu-d3/32vcpu-128gb` and added the catalog-owned login role taint so fresh
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
  derived only after live metadata confirms the selected GPU shape supports
  clustering, and the plain MK8s wizard writes the provider-ranked fabric for
  live-confirmed cluster-capable shapes without a raw toggle/fabric prompt.
  Reservation policy now defaults to `AUTO` when the selected live GPU
  shape/fabric exposes reserved capacity and otherwise keeps `FORBID`.
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
- For Soperator targets, default generic MK8s NCCL benchmark workloads off so
  transient K8s benchmark pods do not compete with Slurm worker pods; keep the
  non-workload GPU stack readiness validation enabled. The generated profiles
  also avoid topology or node-health initial runs unless the matching profile
  enables them.
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
- Added Soperator `apps.charts[].placements` materialization for existing typed
  MK8s node groups. The wizard now lists target node groups per Soperator
  placement, defaults workers to GPU groups and service placements to CPU
  groups, and renders the selected mapping into chart-native filters, NodeSets,
  storage selectors, partitions, SFS attachments, and NodeConfigurator rebooter
  tolerations without creating extra role-named node groups.
- Added an explicit Soperator `install_mode` prompt. `production-cluster`
  creates the complete MK8s+SFS+Soperator five-role bundle, while
  `onboard-existing-cluster` registers an external Nebius MK8s target, records
  a read-only Soperator onboarding analysis and accepted action plan, and opens
  the placement wizard for discovered node groups without Terraform-managing
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
- Extended Soperator placements to chart-owned system helpers so the operator
  manager, checks controller, and MariaDB operator pods follow the selected
  `system` CPU node groups instead of landing on GPU workers.
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
  target-scoped placements, and added a command-help guard against
  reintroducing old MK8s shortcut input names.
- Added the `nebius.com/node-group` Kubernetes node label to Soperator-created
  MK8s node groups so generated role filters and worker NodeSets can schedule
  on the five-role production profile without hand-authored labels.
- Preserved explicit per-node-group SFS key selections during Soperator
  placement mapping so custom target-specific SFS filesystems are not mixed
  with default profile keys.
- Carried MK8s node-group taints into Soperator placement filters, worker NodeSets,
  and storage selectors when placement mapping is used, so tainted controller,
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
  GPU visibility now skips with an explicit report when existing workloads
  already reserve every GPU on every Ready GPU node. NCCL caps and scheduler
  allocation behavior now belong to explicit `acceptance-test benchmark` runs.
- Updated the bundled CUDA smoke sample image to NVIDIA's CUDA 12.5
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
- Hardened the MK8s GPU visibility deploy-time validation against transient
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
  `deploy.targets[].observability.*`, MK8s GPU deployment-testing settings live
  under `deploy.targets[].deployment_testing.mk8s_gpu.*`, and root `deploy.observability.*`
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
  `deploy.targets[].deployment_testing.mk8s_gpu.*` rows. Multi-cluster configs
  can now enable deployment testing on one MK8s target and disable it on another
  without carrying a project-global validation block.
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
  treats `resource-advice` regular-vm/reserved/preemptible availability as VM
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
  hidden when the best reserved fabric differs from the best regular-vm fabric.
- Fixed MK8s InfiniBand fabric recommendations for reserved GPU capacity. When
  live Capacity Dashboard rows show reservation slots on a different fabric
  than the strongest regular-vm lane, the wizard now recommends the reserved
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
  path. For cluster-capable GPU presets,
  `inputs.gpu_clusters.<key>.infiniband_fabric` values now come from live
  Nebius Capacity Dashboard fabric rows, while live preset
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
  available, derived InfiniBand fabric selections now use exact
  platform+preset fabrics with live regular-vm/reserved availability and
  persist the recommended default without showing a raw fabric prompt, and
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
  `deploy.targets[].deployment_testing.mk8s_gpu.health_checker.enabled` toggle unless the active
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
  deploy `operator_readiness`, bounded `gpu_visibility`, and explicit NCCL
  acceptance benchmarks as distinct responsibilities rather than overlapping
  duplicate checks.
- Ignored local coverage data files and packaged chart archives in the service
  repo `.gitignore`, and clarified that the managed customer deployments
  `.gitignore` stays intentionally narrow to generated Terraform runtime files
  and tfvars instead of acting like a generic developer ignore file.
- Exposed bundled MK8s GPU deployment-testing controls as a target-facing deploy
  contract under `deploy.targets[].deployment_testing.mk8s_gpu.*`, so these CLI deploy checks
  no longer masquerade as Terraform inputs. The wizard still surfaces the same
  toggles from catalog defaults, but the resulting values now persist in
  `config.yaml` as deploy settings, and local `deploy` also supports one-run
  `--skip-validations` / `--skip-validation <kind>` overrides.
- Removed the temporary backward-compatibility shims from that MK8s GPU
  deployment-testing contract: `infra.components[].inputs.gpu_validation_overrides`
  and the old `deploy.targets[].validations.*` path now fail fast instead of
  being migrated, and local `deploy` requires generated-manifest
  `deploy.validations` metadata instead of recomputing GPU validation specs from
  older bundles at runtime.
- Tightened the interactive MK8s GPU app flow: when the infra prompts turn on
  a GPU shape that requires `nvidia-gpu-operator` or
  `nvidia-network-operator`, the wizard now auto-enables those app rows before
  the app phase starts so the same `create` / `component add` pass can still
  show their prompts instead of only materializing them later in `config.yaml`.
- Simplified the bundled `mk8s` source catalog by removing the one-off raw
  `wizard:` block for GPU validation helper defaults. cxcli now derives those
  virtual prompt defaults directly from `components.infra.mk8s.cli.gpu.deployment_testing`
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
- Refined the bundled GPU visibility reporting contract: the validation still
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
- Tightened MK8s in-cluster deploy validation behavior so `deploy`, `flux apply`, and `flux bootstrap` no longer block on a generic all-nodes-ready pre-wait, MK8s GPU validations now emit live Kubernetes status instead of silently polling, local `deploy` keeps a continuous spinner alive across those validation phase transitions with non-TTY log fallback, and the bundled GPU visibility probe bounds its default node fan-out with catalog-owned `max_nodes` caps plus shorter default timeouts to keep deploy-time validation fast on large clusters. NCCL fan-out now belongs to explicit `acceptance-test benchmark` runs.
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
- Replaced the old MK8s GPU hardcoded profile split with component-local settings policy: `component_cli_settings.yaml` now keeps MK8s GPU image preferences and validation defaults under `components.infra.mk8s.cli.gpu`, keeps GPU operator/network operator auto-enable rules and Helm value overrides on the operator app entries under `components.apps.<id>.cli.mk8s_gpu_policy`, while `component_sources.yaml` keeps the reusable Terraform/Helm source and release metadata. The catalog pair removes the unused standalone `nvidia-device-plugin` entry, materializes Nebius-image vs operator-managed MK8s defaults from the live Nebius compatibility matrix, and keeps generated GPU validation reports under `generated/reports/`.
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
- Tightened bundled MK8s GPU-cluster guidance around live preset capability instead of guesswork: the wizard now resolves the GPU preset before deriving canonical `inputs.gpu_clusters.<key>.infiniband_fabric`, fabric is written only when the chosen preset's live SDK metadata allows GPU clustering, stale fabric values fail during validation when the selected GPU shape no longer supports clustering, and runtime validation now fails early on invalid fabric+preset combinations instead of deferring them to Terraform/MK8s admission errors.
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
- Wired canonical MK8s `inputs.gpu_clusters.<key>.infiniband_fabric` materialization into the built-in wizard profile with a provider-ranked derived fabric selection keyed by the chosen GPU platform/preset and `client_info.nebius.region_id`, using live Nebius GPU-cluster capacity rows instead of a raw free-text prompt.
- Fixed `create` wizard prompt helper late-binding closures in `cli.py` so Ruff no longer flags `B023` on the deferred module-prompt builders, and tightened the runtime-shape unit coverage to skip post-write validation in the test that only asserts generated config structure.
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
