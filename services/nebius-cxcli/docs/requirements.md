<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v1 -->
# Project Requirements

<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=reliability -->
### REQ-001: Resume external upgrades from exact interrupted manager-pause checkpoints

#### User Story

As an external Soperator upgrade operator, I need cxcli to resume a checkpointed
target-admission bootstrap after interruption when the saved manager-pause state
is provably from the same campaign and live Deployment, so the upgrade can
continue without manual checkpoint editing or unsafe authority adoption.

#### Acceptance Criteria

- AC-001: An exact checkpoint state produced by an interrupted supported manager-pause transition is reconciled into the canonical target-admission bootstrap pause authority and durably checkpointed before the upgrade continues.
- AC-002: Recovery verifies the campaign, target, manager UID, original replica count, paused live spec fingerprint, generation, rendered Helm restore contract, and zero live manager Pods before adopting the state.
- AC-003: Conflicting, foreign, malformed, over-advanced, or unverifiable manager-pause state fails before any new target mutation.
- AC-004: Restore-capable backup metadata already bound to the external upgrade checkpoint remains reusable and does not trigger a replacement backup solely because admission recovery resumes.
- AC-005: The `post-switch-resume` admission bootstrap may replace an exact completed `active-slot-switch` manager-pause predecessor only when the checkpoint proves the intervening `target-compatibility` manager restore authority and the live annotated zero-manager is its single-generation bootstrap successor with the same UID, original replicas, non-replica spec, and target binding. The successor receipt and new bootstrap pause authority are checkpointed together before webhook publication or Helm dispatch.
- AC-006: Before any active-bridge service replay, the exact post-switch manager-pause successor is owned by the rolling service-replay phase. A checkpoint produced before that ownership handoff may be reconciled only when the sealed admission successor receipt, manager identity and spec, campaign and target binding, live zero-manager proof, and bridge authority lineage all match. The displaced rolling authority must be either the receipt-generation predecessor or an exact same-generation passive/active successor sibling with the same chart, manager render, prerequisites, prerequisite UIDs, and manager UID; boundary-specific values may differ. The ownership move and its role-bound bridge adoption receipt are checkpointed atomically before any manager, Helm, Slurm, or provider mutation. A later target-HA replay may retain that adoption-time source-HA binding only when the current target authority is its immediately following unique durable history successor, both canonical writer-scale transition reasons match, and the target transition timestamp is at or after the adoption; any foreign, reordered, duplicated, skipped, or time-inconsistent lineage fails closed.

#### Negative Criteria

- NC-001: The implementation must not add a legacy compatibility branch that accepts unbound or ambiguous checkpoint shapes.
- NC-002: Recovery must not infer success from a healthy final cluster state, a retry, or terminal output without matching checkpoint and live authority evidence.
- NC-003: Source validation alone must not be reported as completion of the live external upgrade campaign.

#### Validation Method

Run the exact checkpoint-shape regression and the surrounding target-admission,
manager-pause, external-upgrade resume, and backup-reuse checks. A live success
claim additionally requires a clean replay from the authoritative checkpoint.

#### Test Method

Use deterministic checkpoint fixtures that represent the producer-sealed
`accepted` and `terminating` interruption states before and after a canonical
same-generation spec reserialization, an unchanged completed canonical
authority, an exact completed predecessor followed by its journaled restore and
single-generation post-switch bootstrap successor, the exact historical split
between either legal rolling-authority role and a populate-owned successor, malformed or
drifted variants, foreign lifecycle authorities, a stale live predecessor, and
same- or split-owner placement. Assert the canonical rolling owner, bridge-
epoch-bound adoption receipt, checkpoint writer sequence, and absence of
mutation on rejected variants.

#### Evaluation Method

Accept the change when the original recovery-required signature is absent for
the exact supported interrupted state, negative cases remain fail-closed, the
focused tests pass, and any live replay is reported separately with its evidence
lineage.

<!-- /REQUIREMENT: REQ-001 -->

<!-- REQUIREMENT: REQ-002 status=active priority=P0 type=reliability -->
### REQ-002: Fence post-switch Helm resume against release replacement

#### User Story

As an external Soperator upgrade operator, I need a post-switch admission resume
to remain bound to the exact Helm release and SlurmCluster it selected until the
first Helm command is dispatched, so a concurrent release cannot make cxcli
reapply stale predecessor values.

#### Acceptance Criteria

- AC-001: Selection binds the deployed Helm revision, chart, app version, stored-values fingerprint, and target namespace, name, and UID as one immutable precondition.
- AC-002: cxcli compares the complete precondition before CRD mutation and again immediately before the first Helm command after admission and login-session preparation have passed.
- AC-003: Any release, values, or target-identity drift fails closed before Helm dispatch, while an unchanged precondition permits the canonical operation.
- AC-004: A protected Helm command error fails the attempt without an internal webhook, pending-operation, or ownership-adoption retry; immediately before dispatch cxcli checkpoints the selected release head and exact expected successor revision.
- AC-005: The next command may recover only an unchanged head, an exact intent-bound pending revision, a bounded contiguous transient-webhook failed chain, or the exact deployed successor. The lifecycle keeps its original deployed release as immutable authority while the authenticated current head advances for retry or adoption. It clears a proven pending revision only through Kubernetes Secret UID and resourceVersion deletion preconditions, then re-reads and accepts only the exact prior head before authorizing a retry. A failed head requires durable authorization, and an exact deployed successor is adopted as `helm-applied` without duplicate Helm dispatch.
- AC-006: A successful protected post-switch Helm command durably advances its dispatch journal to the exact deployed successor without downgrading a completed admission lifecycle. If that successor already applied the temporary login-surge replica count before a boundary-local surge intent was recorded, cxcli may adopt it only from the completed `post-switch-resume` lifecycle, exact protected-dispatch root and expected successor, causal dispatch/history/completion timestamps, exact target and chart identities, desired values fingerprint, rendered live target spec, and compatible semantic-drift proof. The resulting surge proof and adoption receipt are checkpointed before service replay continues; replay requires the same normalized proof and immutable receipt and performs no Helm mutation.
- AC-007: Login-surge restore binds its Helm pulse to the exact deployed surge release proven by the surge intent/proof or protected post-switch adoption material. If an interrupted `admission-ready` restore lacks that release precondition after an ambiguous create timeout, cxcli may recover it only when Helm history still exposes the unchanged exact surge release and no successor revision; the recovered precondition is durably checkpointed before a protected dispatch. A later failed revision is retryable only when its exact protected history, values, and description prove a supported webhook-startup failure or Kubernetes ambiguous create timeout.
- AC-008: The target Soperator Helm `upgrade --install` process disables Go HTTP/2 client negotiation after repeated release-Secret connection loss, preserving TLS over HTTP/1.1 while leaving other Helm commands, chart values, protected-dispatch authority, and error/retry classification unchanged. Any pre-existing unrelated `GODEBUG` settings are preserved.
- AC-009: If a successful protected login-restore Helm successor synchronously changes the live SlurmCluster from surge to configured replicas before cxcli creates the owner-transition intent, cxcli may adopt that owner successor without another patch only after re-proving the exact protected root/head history, immutable surge and restore proofs, both revision manifests, a login-size-only spec delta, current target identity/spec, and the existing manager, controller-gate, and bridge fences. The verified adoption is checkpointed before workload scaling.
- AC-010: A later controller-gap bridge recovery may supersede a durable surged bridge-client proof with the configured login set only when the exact verified login-workload restore receipt proves the same workload UID, target UID, configured and surge counts, expected generation, and terminal configured observation; every retained consumer identity and config must remain exact and only the ordinal surge Pod may be absent. The accepted removal is appended to the bridge consumer-successor journal before the live RPC proof is replaced.
- AC-011: OpenMetrics restore must bind target-admission bridge and manager-pause ownership to the canonical rolling phase. A checkpoint written by the earlier operation-local owner may be normalized only after the completed OpenMetrics lifecycle, sealed operation pause, canonical post-switch pause, campaign, target UID, manager UID/spec/generation, chart, prerequisites, and restore fingerprints all agree; cxcli then checkpoints the exact continuous-pause reuse receipt and removes only the redundant operation-local pause.
- AC-012: After a protected post-switch login surge has been adopted and restored, the later SConfig bridge refresh may recognize the prepared zero-writer fence's exact restored successor without a synthetic SlurmCluster-regression receipt. That successor requires the complete campaign-bound post-switch adoption receipt, proof, semantic evidence, reconciliation hash and desired-values fingerprint, protected restore-owner successor, verified login-client removal, target identity, manager UID/generation, controller-gate fingerprint, propagated client digest, and restored accounting-writer gate and command fence. The ordinary regression route still requires its exact accounting rebind; the protected route may lack that older nested receipt only because its protected restore successor independently binds the configured owner to the release root/head and restored accounting live boundary. Missing or drifted authority remains blocking before writer mutation.
- AC-013: A controller-gap config-only client proof may advance across one observation that combines an exact session-free replacement of a retained target login with the already verified removal of the temporary surge ordinal. cxcli must first prove the replacement over the retained client intersection with zero active SSH sessions and exact target, workload, Pod, container, node, and config authority; normalize only those proven replacement identities to their durable predecessors; then prove the full terminal surge removal against the normalized set. The replacement and removal must bind the same workload and target authority and are journaled as one combined successor before the durable proof changes. A missing or extra client, foreign proof material, active session, or any config, identity, owner, workload, target, or removal drift remains blocking without a checkpoint write.

#### Negative Criteria

- NC-001: The implementation must not retain a values-only resume path, legacy checkpoint fallback, or compatibility shim that bypasses the complete precondition.
- NC-002: The precondition must not weaken controller-bridge gating, manager pause, inert controller command, jail alias, maintenance, or OpenMetrics lifecycle checks.
- NC-003: Source validation alone must not be reported as completion of the live external upgrade campaign.
- NC-004: A generic timeout or `another operation` error must not delete whichever Helm revision is currently pending; cleanup requires an exact intent- or journal-bound revision.
- NC-005: A live surge replica count, matching values alone, or a release-history wall-clock window must not substitute for the protected post-switch dispatch authority, and recovery must not synthesize a retroactive surge apply intent.

#### Validation Method

Run deterministic release-replacement regressions at the final Helm dispatch
boundary, the post-switch admission and OpenMetrics neighborhood, and the full
Soperator migration executor suite. A live success claim additionally requires
an authorized replay of the exact checkpointed command.

#### Test Method

Select revision N with exact chart, app, values, and target identity; expose
revision N+1 before dispatch; and assert that the session gate may complete but
no Helm mutation occurs. Reject entry drift before CRD apply and prove a
protected command error does not retry in-process. Exercise a failed first
attempt followed by a successful second invocation, exact pending-revision
cleanup, a pending-to-deployed or replaced Secret race that cannot delete or
authorize, a newer pending head that cannot be deleted through an older
checkpoint, failed-head retry through the outer admission lifecycle,
deployed-successor adoption, and rejection of a foreign failed revision. Cover
unchanged state plus chart, app, values, and target-UID drift independently.
Exercise the completed post-switch successor that already contains the login
surge without a dedicated surge intent; require exact dispatch lineage and
causal timestamps, persist one stable adoption receipt, reject receipt or
history drift without a write, and prove no duplicate Helm apply occurs.
Exercise login-restore recovery from an unchanged no-successor release after an
ambiguous create timeout, then reject a changed deployed head and an arbitrary
failed revision. Require every restore Helm call to carry and revalidate the
checkpointed release precondition. Assert that only the target Soperator Helm
upgrade process receives `http2client=0`, that unrelated `GODEBUG` settings are
preserved, and that protected dispatch failures still return to the outer
checkpointed lifecycle. Exercise a deployed protected restore successor whose
live SlurmCluster is already configured while the workload remains surged;
require exact root/head history and manifest lineage, checkpoint one verified
no-patch owner adoption, and reject manifest, history, proof, target, or
non-login spec drift before workload mutation. Re-enter controller-gap bridge
recovery from the earlier surged client proof after the exact workload restore;
require the caller to pass the verified removal receipt, accept only the absent
surge ordinal with unchanged retained identities and configuration, and reject
a missing receipt, workload, target, or generation drift, a present removed
Pod, or any other client-set change.
Model the interrupted post-OpenMetrics checkpoint with both the already adopted
canonical rolling pause and the completed operation-local pause. Require exact
completed lifecycle and pause material before normalizing it into the existing
continuous-reuse receipt; drift any bound campaign, target, manager, lifecycle,
or boundary field and assert no checkpoint write.
Model the later restored SConfig bridge boundary without the ordinary
SlurmCluster-regression receipt. Admit it only through the full protected
post-switch adoption validator and exact downstream manager, controller,
client, protected restore-owner, accounting-writer, and prepared-fence chain.
Prove the ordinary route still requires its accounting rebind, while the
protected route accepts the exact older no-rebind checkpoint only through the
full protected restore successor; reject campaign, desired-values,
adoption-hash, proof, semantic, restore-owner, or downstream fence drift before
the refresh can restart or scale a writer.
Model one config-only controller-gap observation with an unchanged retained
login, one exact session-free retained-login replacement, and removal of only
the terminal surge ordinal. Require the retained-subset replacement proof and
the normalized full-set removal proof to bind the same target and workload,
append one combined successor, and write once. Reject foreign replacement,
removal, or authority material without changing the prior proof or checkpoint.

#### Evaluation Method

Accept the change when every immutable field is carried to the once-only
pre-dispatch check, drift fails before Helm, stable state dispatches normally,
the checkpointed next-command recovery accepts only the exact successor chain,
and bridge-fencing regressions remain green.

<!-- /REQUIREMENT: REQ-002 -->

<!-- REQUIREMENT: REQ-003 status=active priority=P0 type=reliability -->
### REQ-003: Recover an exact projected worker bridge-config successor

#### User Story

As an external Soperator upgrade operator, I need cxcli to distinguish an exact
bridge-config successor projected into an old unready worker from a completed
runtime successor or a foreign config digest, so resume performs the required
serial recreation instead of waiting forever or weakening identity fences.

#### Acceptance Criteria

- AC-001: When an unjournaled worker Pod reports the exact target bridge-config digest but is not Ready, cxcli binds that exact live Pod as the source requiring replacement and enters the existing serial recreation state machine; it must not mark the worker verified from the projected digest.
- AC-002: Before deleting that source, cxcli must preserve the existing partition pause, MUNGE continuity, zero-job proof, exact workload identity, and UID/resourceVersion CAS gates; only a distinct Ready successor with the exact target digest may be checkpointed as verified.
- AC-003: A digest outside the exact checkpointed predecessor/successor pair remains recovery-required before any Pod mutation.
- AC-004: Resume uses one canonical digest-first classification and the existing replacement journal; it does not add a legacy checkpoint shape, inferred readiness, a second deletion mechanism, or a compatibility bypass.
- AC-005: After exact worker successors are verified while the target controller command gate remains deliberately inert, partition-pause verification reuses the complete checkpoint-owned `DOWN` records only when the manager pause is one exact sealed standard, bootstrap, or rearm authority and the target UID, controller gate, restored admission window, target-HA bridge authority, and partition records all match. A canonical bootstrap pause is bound by its UID, generation, spec fingerprint, original replicas, and immutable bootstrap contract; it does not require a stale mutation-time Kubernetes resource version.
- AC-006: The exact inert-controller pause proof seals or validates the controller-gap semantic binding before worker partition reassertion and must prevent a live Slurm partition RPC. Missing, malformed, unsealed, or drifted pause authority remains mutation-free pending or recovery-required.

#### Negative Criteria

- NC-001: The implementation must not treat `Ready=false` as proof that an exact successor digest is foreign.
- NC-002: The implementation must not weaken predecessor deletion, successor identity, zero-job, Secret handoff, or target workload gates.
- NC-003: Local validation must not be reported as completion of the live campaign.
- NC-004: The implementation must not wait indefinitely for a projected target digest on an old container whose readiness probe cannot converge without recreation.
- NC-005: The implementation must not weaken manager-pause contract validation merely to ignore an absent resource version, and it must not issue a live Slurm RPC through an intentionally inert controller.

#### Validation Method

Run a fail-first checkpoint-shaped regression for an unjournaled exact
successor with `Ready=false`, the surrounding in-place worker bridge-config
rollout and CAS-ordering tests, the sealed bootstrap-pause controller-gap reuse
regression, changed-scope lint and documentation alignment, and then the
unchanged live upgrade command from its authoritative checkpoint.

#### Test Method

Seed the worker rollout with the exact predecessor and target digests, expose
the exact target digest with `Ready=false`, then assert source binding, the
job-free proof before one UID/resourceVersion-CAS delete, and a distinct Ready
exact successor before verification. Retain positive coverage for an initially
Ready exact successor and negative no-write coverage for any digest outside the
predecessor and successor pair. Model the post-recreation live checkpoint with
a sealed `post-switch-resume` bootstrap manager pause that intentionally has no
stored resource version, then assert that the exact partition records are
reused and no Slurm partition snapshot is attempted. Drift the sealed pause
contract and assert fail-closed rejection before reuse.

#### Evaluation Method

Accept the change when the live failure signature is reproduced by the
regression before the repair, the exact projected successor follows the existing
serial recreation and verification path afterward, foreign digests remain
recovery-required, exact bootstrap pause authority retains the no-RPC
controller-gap path after recreation, and the authorized campaign replay
advances beyond both boundaries.

<!-- /REQUIREMENT: REQ-003 -->

<!-- REQUIREMENT: REQ-004 status=active priority=P0 type=reliability -->
### REQ-004: Resume an exact accepted bridge-client successor before first propagation

#### User Story

As an external Soperator upgrade operator, I need cxcli to resume after it has
durably accepted and deployed the canonical bridge-client compatibility
successor but before it writes the first cluster-wide propagation proof, so the
same command can finish the existing login, worker, and live-RPC gates.

#### Acceptance Criteria

- AC-001: An `accepted` handoff whose recorded and observed digests equal the sealed compatibility target may resume without a predecessor propagation record only when the successor binds the exact target, ConfigMap, predecessor digest, target digest, transform counts, and canonical compatibility reason.
- AC-002: The pre-propagation replay remains `accepted`; it does not synthesize, rewrite, or treat the missing propagation receipt as verified and must continue through the existing login, worker, and cluster-wide propagation proofs.
- AC-003: A partial or drifted propagation record, foreign handoff or successor ConfigMap, conflicting login-rollout digest, missing verified-login timestamp, or digest outside the sealed successor remains recovery-required without a checkpoint write.
- AC-004: Resume uses one canonical accepted-successor path and adds no legacy checkpoint schema, backward-compatibility shim, or inferred predecessor proof.

#### Negative Criteria

- NC-001: The implementation must not accept an arbitrary `accepted` digest merely because no propagation proof exists.
- NC-002: The implementation must not weaken the verified predecessor recovery path or the later worker, Slurm, controller, manager, and live-RPC gates.
- NC-003: Local validation must not be reported as completion of the live campaign.

#### Validation Method

Run the fail-first checkpoint-shaped accepted-successor regression, drift and
no-write negatives, the bridge-client observation and propagation neighborhood,
documentation alignment, changed-scope lint, and then the unchanged live
upgrade command from the authoritative checkpoint.

#### Test Method

Seed an accepted handoff from the live checkpoint shape: a sealed predecessor
to compatibility-target receipt, the target digest in both the handoff and
observed ConfigMap, a verified target-digest login rollout, and no propagation
record. Assert mutation-free replay, then independently drift the two ConfigMap
bindings, login digest, and a partial propagation record and require fail-closed
behavior without a write.

#### Evaluation Method

Accept the change when the live error is reproduced before the repair, the
exact pre-propagation successor resumes afterward, every drift case remains
blocked, and the authorized campaign replay advances beyond this boundary.

<!-- /REQUIREMENT: REQ-004 -->

<!-- REQUIREMENT: REQ-005 status=active priority=P0 type=reliability -->
### REQ-005: Restart an exact pre-replay GPU worker process

#### User Story

As an external Soperator upgrade operator, I need cxcli to distinguish an idle
GPU worker daemon that predates the verified static-topology replay from a
healthy current registration or arbitrary topology drift, so the interrupted
upgrade can restart only that stale process generation and continue safely.

#### Acceptance Criteria

- AC-001: A worker with the exact configured typed GRES may enter the stale-process path only when its internally consistent live CPU topology is smaller than the verified replay, `Parameters` is absent, state is exactly `IDLE+DYNAMIC_NORM`, reason is empty, and CPU and memory allocations are zero.
- AC-002: Before any Pod deletion, cxcli proves the release-gate Pod UID and zero-restart `slurmd` process were created and started before the checkpointed topology replay was prepared and verified.
- AC-003: cxcli re-reads the target Helm-owned NodeSet and requires its UID, generation, replicas, target ownership, complete spec fingerprint, and canonical static CPU/socket/core/thread/parameter/typed-GRES topology to match the replay. The old Pod's jailed `slurmd -C` output must describe the same topology.
- AC-004: The existing checkpoint-first UID/resourceVersion-preconditioned rollover deletes all exact stale Pods before waiting, then requires distinct Ready zero-restart successors on the same nodes and workload lineage whose `slurmd` processes started after replay and whose runtime config is exact.
- AC-005: After rollover, cxcli freshly re-observes the replacement registration. An idle stale controller registration does not issue `scontrol update State=RESUME`; when the live replacement Pod and exact `slurmd -C` runtime are durably rebound to the verified rollover, that same exact stale-static controller observation may enter the existing target-HA topology/config successor. Replay preserves the historical full-output fingerprint but compares only the canonical CPU/socket/core/thread/typed-GRES contract, so non-contract `slurmd -C` fields such as uptime, memory, or temporary-disk capacity cannot invalidate an unchanged topology. The already supported generic-GRES `INVALID_REG` observation remains the other exact recovery entry, and only its separately proven owned-drain path may clear a drain.
- AC-006: Any allocation, drain/down flag, reason, GRES drift, noncanonical CPU shape, NodeSet/spec/owner drift, post-replay predecessor process, replacement drift, or incomplete checkpoint proof fails before an unauthorized Pod or Slurm mutation.
- AC-007: Before topology recovery issues any target-HA partition, queue, node, or reconfigure RPC, cxcli runs the existing exact target cluster-ID marker transition when the accepted cluster-name and collision-free accounting registration proofs require it. At this pre-smoke boundary, the repair must bind the full checkpoint-paused zero-allocation queue chain plus the current enabled target accounting Deployment, authenticated zero-restart writer successor, MariaDB/PVC identity, and registered IDs. It must then bind the exact two-role bridge workload and marker preimage, stop both writers, remove only that preimage, restart the same workload, prove the target SlurmDBD ID plus one active and one standby controller, and re-prove bidirectional Munge authentication between those successor controllers and the accounting writer. If both journaled controller Pods have already entered the exact non-deleting, StatefulSet-owned `CrashLoopBackOff` envelope with a positive restart count and last `slurmctld` exit `1`, cxcli may read the marker only through the already-existing Ready campaign stager after validating that its image is the same immutable source image recorded by both the bridge source binding and version transition. The current bridge image is bound separately as the target image, and the stager's inert command, security context, UID/resourceVersion, and shared state-PVC mount must also remain exact. cxcli must not reapply that reader before intent. After the verified stop/restart, the accepted typed-GRES config successor keeps its intent-time Pod identities; resume may bind them to the current generation only when the marker repair names those exact predecessors, retains the same StatefulSet UID and pre-Slurm successor fingerprint, fingerprints the current active/standby role records, orders their observations after restart and before verification, and the live config plus both jailed digests remain unchanged. cxcli checkpoints that child-lineage receipt once without rewriting the historical successor. Replay of a verified marker repair must revalidate its sealed pre-Slurm queue/accounting authority directly and must not reconstruct that historical chronology from a later semantically identical queue observation; an unverified or absent repair still requires fresh derivation. After all staging and probes, every Slurm-capable pause/reconfigure boundary must be preceded by a fresh guard-fence-guard sequence that re-reads the same StatefulSet, exact successor Pod UIDs, Ready container IDs/images/restart counts/start times, live active/standby mapping, ConfigMap material, and both jailed digests. A fourth guard-fence-guard sequence after the successful post-reconfigure ping must bind that result to the same generation before `reconfigure.status=verified` is checkpointed. Missing or drifted authority fails before a Slurm RPC or unrelated checkpoint mutation; replay of a verified repair is otherwise mutation-free.

#### Negative Criteria

- NC-001: The implementation must not classify the smaller pre-replay topology as ready or accept a generic mismatched worker topology.
- NC-002: The implementation must not bypass the partition pause, queue, release-gate, workload-identity, mutation-guard, or Pod deletion preconditions.
- NC-003: The implementation must not add a legacy checkpoint fallback, manual checkpoint edit, direct live workaround, or source-only completion claim.

#### Validation Method

Run the live-shaped classifier and chronology regressions, the NodeSet static
binding and pre-delete mutation guards, the topology-drain/typed-GRES rollover
neighborhood, documentation alignment, changed-scope lint, and then the exact
authorized upgrade command from its authoritative checkpoint.

#### Test Method

Model the observed 32-CPU idle registration against the verified 128-CPU
replay, exact typed GRES, zero allocations, and no reason. Independently drift
allocation, state, reason, GRES, parameters, CPU shape, process chronology,
Pod UID, NodeSet UID/spec/owner, and replacement runtime. Assert only the exact
pre-replay generation reaches the UID-bound delete boundary, and prove the
caller rolls the complete stale set without a Slurm resume. Under target-HA
authority, prove an exact replacement registration skips config recovery,
while either a freshly observed generic-GRES `INVALID_REG` successor or the
exact stale-static controller record backed by a live replacement/runtime
resume proof routes through that existing recovery. Model both exact controller
Pods in the stale-marker fatal loop and prove the existing inert stager reads
the marker without reapply while carrying the immutable source image distinct
from the target bridge image; independently drift the source-binding/transition
image agreement or immutability, controller owner, wait and termination
envelope, stager image, command, security, readiness, and PVC and require
rejection before intent or marker mutation.

#### Evaluation Method

Accept the change when the deterministic live blocker is reproduced before the
repair, the exact stale processes are replaced through the existing guarded
rollover afterward, all drift cases remain fail-closed, and the authorized
campaign replay advances beyond GPU topology drain recovery.

<!-- /REQUIREMENT: REQ-005 -->

<!-- REQUIREMENT: REQ-006 status=active priority=P0 type=reliability -->
### REQ-006: Reload an exact pre-CAS accounting Munge runtime

#### User Story

As an external Soperator upgrade operator, I need cxcli to reload the target
accounting runtime when its long-running Munge daemon predates the verified
bridge-to-target Secret handoff, so the retained target-HA controllers can use
Slurm accounting without an uncheckpointed Pod restart.

#### Acceptance Criteria

- AC-001: cxcli may classify accounting as stale only when one exact target-selected Deployment/ReplicaSet/Pod lineage is Running and Ready, mounts the handoff-bound target Munge Secret, has zero container restarts, and its current container start predates the accepted Secret CAS.
- AC-002: Local Munge encode/decode must succeed in the retained bridge and accounting runtimes, while ephemeral cross-decode must prove their current daemons disagree; cxcli never logs or checkpoints the generated credential.
- AC-003: Before restart, cxcli requires the verified Munge handoff, retained target-HA bridge authority and Pod identities, exact active accounting writer/deployment/service binding, paused target partitions, and an empty Slurm queue.
- AC-004: cxcli records an immutable restart intent before one UID/resourceVersion-preconditioned accounting Pod deletion, then accepts only a distinct same-Deployment successor that is Running/Ready, zero-restart, mounts the same Secret, started after CAS, and cross-authenticates with both retained bridge controllers.
- AC-005: Exact verified replay performs no second deletion; foreign owners, multiple selected Pods, allocation or queue activity, Secret/material drift, bridge drift, accounting writer drift, failed local Munge, unexpected cross-auth behavior, or successor drift fails before mutation.

#### Negative Criteria

- NC-001: The implementation must not restart accounting merely because a log contains an authentication error or because a Secret object changed.
- NC-002: The implementation must not print, persist, hash into reports, or otherwise expose an ephemeral Munge credential or Secret payload.
- NC-003: The implementation must not add a manual Kubernetes workaround, legacy fallback, or non-checkpointed deletion path.

#### Validation Method

Run exact stale/current accounting runtime classifiers, credential-crossing and
lineage drift tests, caller-level checkpoint/delete/successor regressions,
adjacent Munge handoff and accounting-writer tests, changed-scope lint/docs
checks, then the unchanged authorized upgrade command.

#### Test Method

Model the live verified Secret CAS with one pre-CAS active accounting Pod whose
local Munge works but whose credentials fail against both bridge controllers.
Assert one durable UID-bound deletion and a post-CAS authenticated successor.
Independently vary queue state, timestamps, owner UIDs, selectors, Secret name,
restart counts, bridge identities, writer authority, and every credential probe
outcome; assert no mutation for every non-exact case.

#### Evaluation Method

Accept the change when the live cross-component authentication failure is
reproduced before repair, the accounting successor authenticates both retained
controllers after the product-owned restart, the controller no longer logs
protocol-authentication failures, and the campaign continues without manual
cluster mutation.

<!-- /REQUIREMENT: REQ-006 -->
<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
