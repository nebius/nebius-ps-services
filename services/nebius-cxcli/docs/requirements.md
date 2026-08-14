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
- AC-007: A `target-handoff` admission bootstrap may replace the rolling phase's exact completed `passive-slot-preparation` pause with the populate phase's exact completed `active-slot-switch` pause only when the active pause preserves the passive Deployment generation after a no-op manager-spec reapply or advances it by exactly one, and matches the live annotated zero-manager plus the campaign, target UID, manager UID, original replicas, non-replica spec, chart, prerequisite identities, prerequisite render, and manager render. Both predecessor bootstrap lifecycles must be complete and chronologically precede the target-handoff intent. cxcli atomically moves the active pause to the rolling owner, removes the displaced populate owner, and checkpoints both a sealed passive-to-active adoption receipt and the exact target-handoff continuous-pause reuse receipt before webhook publication or Helm dispatch. Missing, incomplete, decreased, skipped-generation, foreign, malformed, or drifted state remains recovery-required without a checkpoint write.
- AC-008: After target handoff has durably adopted the exact completed `active-slot-switch` pause into the rolling owner and completed its own admission lifecycle, `post-switch-resume` may continue the same zero-manager generation only when the sealed target-handoff adoption and continuous-reuse receipts, both completed slot lifecycles, completed target-handoff lifecycle, campaign, target UID, manager UID, original replicas, non-replica spec, chart, prerequisites, prerequisite UIDs, manager render, and lifecycle chronology all replay exactly. cxcli checkpoints a boundary-local post-switch continuous-pause reuse receipt before webhook publication or Helm dispatch; missing or drifted adoption lineage remains recovery-required without a checkpoint write.
- AC-009: After the exact target-handoff lifecycle and Helm apply proof complete, the nested accounting command-fence admission bootstrap may retain the rolling phase's same `active-slot-switch` pause through exactly one of two producer stages. Before `post-switch-resume` exists, it must replay the target-handoff intent and completion, sealed target-handoff reuse and adoption receipts, verified intent-bound target Helm proof and compatible semantic-drift evidence, campaign, source and target bindings, target UID, manager UID, original replicas, non-replica spec, generation, chart, prerequisites, prerequisite UIDs, manager render, and chronology exactly. If that accounting intent is interrupted and later re-enters after `post-switch-resume` completes, it must instead replay that exact completed bootstrap authority and its sealed continuous-pause reuse receipt; accounting apply and prerequisite-ready timestamps must follow post-switch completion while preserving the earlier handoff-bound accounting intent and manager identity. cxcli checkpoints one accounting-boundary reuse receipt before webhook publication or the accounting operation. An absent post-switch is valid only through the direct handoff route, while any incomplete, unsealed, reordered, foreign, ambiguous, or drifted post-switch state remains recovery-required without a checkpoint write.
- AC-010: Before either post-Jail in-place completion or later active-bridge service replay, a retained verified bootstrap pause may be replaced by the canonical restored-manager rearm authority only when the immutable-child handoff and post-retirement target-compatibility lineage prove the exact same campaign, target, manager UID, original replicas, non-replica spec, and predecessor pause generation. The target-compatibility authority may be reconstructed from either the exact interrupted intent with its child-handoff gap proof or the exact completed active receipt with its stable marker, zero writer, complete jail-boundary state, post-retirement bindings, and accounting-retirement proof; status alone is never authority. The early started-gate continuation, both post-Jail completion branches, and later service replay must route restored bootstrap or checkpointed rearm state through the same authority-aware fence instead of calling the low-level pause verifier directly. Before checkpointing that rearm, cxcli validates the complete active bridge journal plus its exact campaign, stage owner, epoch, and permanent source-restart fence. If that journal still contains the original bridge-owned standard pause, cxcli may transfer ownership only when its sealed verified contract, exact reserialization chain, and terminal pause generation form the canonical chronologically earlier pause for the same manager, replicas, linked spec, and non-advanced generation; it atomically removes that historical owner and records a hash-sealed adoption beside `rearm-authorized`. Every replay of a checkpointed rearm status repeats the bridge and adoption validation, binds the adoption to the same bridge writer epoch or its exact immediate source-to-target successor, and rejects a newly conflicting direct-bridge or target-admission pause before live target reads or mutation. cxcli checkpoints the rearm authority before pausing the restored manager or recreating the controller command gate. A mismatched retained pause or bridge remains recovery-required and still needs an exact direct bridge, target-admission successor, or sealed controller-gap authority.
- AC-011: After that exact replay has command-gated the future target controller while the source-HA bridge remains authoritative, legacy-rootfs Slurm health must not probe the target-only login config as though its deliberately inert controller were still the authority. cxcli instead revalidates the complete checkpoint-bound source-HA bridge journal, two-replica workload, exact Pod/image/state-volume identities, cluster-wide slurmctld exclusivity, the journaled active Pod's unique primary runtime marker, the standby process, and a live `squeue` RPC through the active bridge. The route requires the exact verified target command-gate identity; malformed, foreign, role-drifted, bridge-unavailable, ungated, or target-HA state remains pending without checkpoint mutation or fallback to the target-only login config.
- AC-012: When a post-Jail replay enters the direct active-slot SConfig rebind after the target accounting writer was restored, cxcli must adopt that SlurmCluster successor through the existing strict accounting-writer rebind before comparing the Jail-boundary fingerprint or recovering a missing boundary intent. The successor requires the same target UID, exactly one generation beyond the prepared zero-writer fence, unchanged login submounts, exact restored/enabled accounting command-fence bindings and resource version, exact source retirement, the target-owned Ready accounting Deployment, and a normalized preimage that differs only by restoring the checkpointed SlurmDBD command and args. cxcli checkpoints the accounting rebind receipt before any recovered Jail intent, alias rebind, or writer pulse. Any additional spec, identity, generation, writer, deployment, retirement, or chronology drift remains pending without adopting the live fingerprint.
- AC-013: When that direct Jail-boundary recovery runs after the exact restored-manager rearm has already paused the same manager and command-gated the target controller, the later verified rearm is the current manager authority and the immutable-child replica count is historical restore capacity only. cxcli may borrow that pause for the boundary writer pulse only after revalidating the canonical rearm contract against its immutable-child and terminal target-compatibility predecessor, the same target and manager UID, original replicas, non-replica spec, exact pause generation and live zero-manager state, the verified inert target-controller workload and Pod lineage, and the checkpointed source-HA bridge authority. If the checkpoint still labels the ConfigMap compatible while its exact live UID and data/content hashes match the full-target output of the rearm contract's immediately preceding manager restore generation, cxcli durably classifies that bounded successor before the pulse; any other ConfigMap state remains pending. It checkpoints one sealed boundary-local borrowed-pause receipt before ConfigMap or SConfig mutation, pulses only the exact SConfig writer from zero to one and back to zero, returns the active variant to compatible, and re-proves the borrowed pause without restoring the manager. Missing, incomplete, foreign, over-advanced, live-drifted, ConfigMap-drifted, or gate-drifted rearm state remains pending before the boundary pulse and must not fall back to the historical one-replica expectation.

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
between either legal rolling-authority role and a populate-owned successor, and
the completed passive-generation rolling owner beside an exact same-generation
no-op or next-generation active-slot populate owner at target handoff. Include
malformed or drifted
variants, foreign lifecycle authorities, a stale live predecessor, and same-
or split-owner placement. Include the retained verified bootstrap predecessor
beside the exact immutable-child and target-compatibility restore chain. Assert
the canonical rolling owner, sealed adoption
and reuse receipts, including the nested accounting command-fence successor,
checkpoint writer sequence, rearm checkpoint before manager or controller-gate
mutation, atomic retirement of an exact historical bridge owner, stable replay
of its sealed adoption, exact bridge-role health after target command gating,
the exact accounting-writer successor receipt before a recovered Jail-boundary
intent, a sealed borrowed-rearm receipt before the resulting SConfig-only
writer pulse, preservation of the verified zero-manager and inert controller,
and absence of mutation on rejected variants.

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
- AC-014: A target-handoff apply intent whose exact admission lifecycle remains before Helm dispatch must resume through that guarded lifecycle instead of being compared with the previously deployed release as though the new apply had completed. Before any provider, cluster, or checkpoint-capable work, this deferral requires the same target, current selected chart-content fingerprint, current effective-values fingerprint, prepared intent, pre-Helm lifecycle status, and absence of a terminal apply proof or values revision. The lifecycle must be classified even when its apply marker is missing. Any drift remains blocking without a live read, checkpoint write, or Helm replay.

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
Model a target-handoff intent beside each supported pre-Helm admission status,
including the live-shaped `prerequisites-ready` interruption. Require the early
recovery probe to perform no Helm or Kubernetes read, preserve the intent, and
return control to the normal guarded lifecycle. Drift the intent/lifecycle
values binding, current effective values, or selected chart content; remove the
apply marker; use an empty or unknown status; or add a contradictory terminal
proof and require a write-free failure before any provider or cluster call.
Retain exact historical proof only for the canonical empty no-webhook
placeholder or known `helm-applied` and `complete` post-dispatch states, and
keep an already-started accounting-writer gate blocking the ordinary
fresh-target path.

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
- AC-007: When the selected target bridge-client configuration is already the exact canonical legacy-compatible payload, cxcli must seal an exact accepted compatibility-adoption receipt before client propagation. That receipt binds the campaign target and ConfigMap identities, uses the same predecessor and target digest, records zero compatibility transforms, and is the sole authority that lets the existing serial worker rollout classify an exact unready predecessor for replacement.

#### Negative Criteria

- NC-001: The implementation must not treat `Ready=false` as proof that an exact successor digest is foreign.
- NC-002: The implementation must not weaken predecessor deletion, successor identity, zero-job, Secret handoff, or target workload gates.
- NC-003: Local validation must not be reported as completion of the live campaign.
- NC-004: The implementation must not wait indefinitely for a projected target digest on an old container whose readiness probe cannot converge without recreation.
- NC-005: The implementation must not weaken manager-pause contract validation merely to ignore an absent resource version, and it must not issue a live Slurm RPC through an intentionally inert controller.
- NC-006: The implementation must not treat a missing compatibility receipt as equivalent to canonical adoption, accept a mixed transformed/adopted receipt shape, or relax Pod readiness globally to bypass the worker rollout.

#### Validation Method

Run a fail-first checkpoint-shaped regression for an unjournaled exact
successor with `Ready=false`, the surrounding in-place worker bridge-config
rollout and CAS-ordering tests, the sealed bootstrap-pause controller-gap reuse
regression, canonical adoption-receipt validation, changed-scope lint and
documentation alignment, and then the
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
On a first call with an already canonical bridge-client payload, assert that
cxcli checkpoints one exact identity adoption receipt and that replay writes
nothing. Feed that producer receipt into the real worker rollout with a
Running but unready exact-digest predecessor, then require the guarded serial
replacement and reject every mixed, foreign, or malformed receipt before Pod
enumeration or mutation.

#### Evaluation Method

Accept the change when the live failure signature is reproduced by the
regression before the repair, the exact projected successor follows the existing
serial recreation and verification path afterward, foreign digests remain
recovery-required, exact bootstrap pause authority retains the no-RPC
controller-gap path after recreation, and the authorized campaign replay
advances beyond both boundaries. An already canonical payload must no longer
skip the receipt required to enter that same worker convergence path.

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

<!-- REQUIREMENT: REQ-007 status=active priority=P0 type=reliability -->
### REQ-007: Resume terminal in-place checkpoints from sealed authority

#### User Story

As an external Soperator upgrade operator, I need a later campaign segment to
reuse the exact durable proofs produced by earlier successful boundaries, so a
legitimate resume does not invalidate itself by refreshing observation or
acceptance timestamps and does not require synthetic provider history.

#### Acceptance Criteria

- AC-001: Replay of a terminal verified typed-GRES compatibility successor and controller-lineage rebind validates the sealed marker, successor, runtime, and receipt material without re-deriving chronology from later mutable observations.
- AC-002: When the current segment intentionally plans no provider worker groups, scheduling and job-control scope comes from the checkpoint's accepted campaign worker groups. A non-empty planned scope remains authoritative and cannot fall back after drift or failed resolution.
- AC-003: A verified target-manager pause may remain continuous across a later boundary owned by the same rolling phase only through an exact operation, manager, target, generation, spec, original-replica, and receipt match. Foreign, partial, or cross-generation reuse remains recovery-required.
- AC-004: Source cleanup for an explicitly empty provider segment may use the effective post-Jail active-worker release generation only when the rolling topology-drain authority seals its exact typed-GRES successor fingerprint and self-hash, topology replay, target partition pause, fleet gate, bridge receipt, and runtime verification. Cleanup must also re-prove the fresh final worker runtime and exact immutable target-child bindings.
- AC-005: A segment with any planned provider worker group still requires its own rolling provider-release receipt. Missing, malformed, unsealed, target-drifted, or fingerprint-drifted evidence fails before source cleanup or unrelated checkpoint mutation.
- AC-006: Source validation, a pending-gate exit, and advancement past one repaired boundary remain distinct from live campaign completion; completion requires the unchanged authorized command and authoritative final checkpoint validation.
- AC-007: Rolling-compute fast verification derives its expected node-group scope from the current locked campaign segment. An empty expected scope with an explicitly recorded empty journal is converged, while an absent or malformed journal and any missing, unexpected, duplicate, blank, or incomplete group remain failed.
- AC-008: Immutable-child cleanup scales every exact captured source ReplicaSet to zero and proves its selector has no Pods before retiring captured children and orphan-deleting the owner. A completed cleanup receipt may recover a residual Pod before validation only when the source SlurmCluster is absent, the exact target workload remains independently ready, the captured ReplicaSet is absent with a complete exact deletion receipt, and the non-Ready residual matches that captured ReplicaSet's name, selector, UID lineage, and stable workload fingerprint; each approved UID- and resourceVersion-preconditioned deletion is checkpointed and replay-safe.
- AC-009: Protected-state Node presence proofs select the exact command-owned provider operation for each delta. A node in an explicitly empty rolling segment may bind to a terminal controller-bridge node-group create only when its name and node-group label match that bridge record's immutable scheduling identity, a fresh live read revalidates the same Node UID, name, label, and non-deleting state, and the operation remains exactly mirrored in the campaign journal; rolling replacements continue to require their own terminal group operation.
- AC-010: Protected-state remediation approval uses a canonical fingerprint over the unchanged baseline and exact blocked or approval-required delta digests, distinct from the raw whole-comparison audit fingerprint. A resume must revalidate the persisted raw comparison before deriving that approval fingerprint; mutable health/status and non-approval audit churn may not poison an unchanged reviewed remediation plan, while any baseline, blocked, classification, or approval-required digest drift remains fail-closed.
- AC-011: Protected-login successor selection accepts either canonical hold-release receipt: an exact zero-session release probe or an exact target-ready release authority. The target-ready route must revalidate the durable authority and exact released source Pod, target workload, current successor Pod, image, restart, container, and host-key identities before journaling the successor; it must not require the deliberately absent zero-session probe.
- AC-012: Final Slurm restore derives target-retired source partitions from the exact mirrored canonical target-partition pause receipt when the optional GPU-smoke scheduling gate is absent. The receipt must bind the complete checkpoint pause inventory, controller-gap inventory, target partition names, and target `DOWN` records before cxcli excludes non-target records from restore. A later target-only pause record may replace its receipt predecessor only through the exact target-singleton runtime-reload reassertion bound to the current full pause inventory, authority epoch, final config, and reasserted target names; every source record remains byte-identical. An in-progress pre-repair restore may be rebound only after a full live snapshot proves every retired source partition absent and every surviving target partition still matches its owned `DOWN` or pre-pause record. After successful restore consumes the target record from the active pause journal, cleanup must validate the exact mirrored restored plan, its superseded-plan link, the consumed target against the canonical target and reassertion receipts, and the remaining exact retired-source records; it must not reconstruct the completed plan from historical phase journals.
- AC-013: Controller-bridge cleanup may accept a provider `NOT_FOUND` response from an exact node-group delete dispatch as terminal deleted postcondition only after the live node-group ID, name, resource version, bridge ownership labels, and slot match the checkpoint record and the delete intent and request are durably journaled. When that dispatch already returned and durably accepted a real provider operation ID, later exact node-group absence plus `NOT_FOUND` from that same operation lookup is also terminal only through a receipt binding the request, acceptance, operation ID, original resource identity, and deleted postcondition. The terminal receipt must preserve that identity-bound evidence and record the provider response classification before later absence is accepted. Absence observed before an exact dispatch remains recovery-required unless one of those exact routes completes.
- AC-014: Post-upgrade MK8s verification must derive its retained provider inventory from the source discovery snapshot minus only exact node-group IDs bound by the current valid controller-bridge journal as `external-temporary`, `provider-create-delete`, `delete-domain`, and excluded from provider upgrade. Every other discovered source, worker, service-role, managed-existing bridge, or merely similarly named group remains mandatory and must be live and ready.
- AC-015: While an exact accepted controller node-group operation remains non-terminal, a target controller command-gate Pod that moves from the journaled source node onto a live Ready node in that exact controller group must defer lineage adoption until the same provider operation reaches terminal success. Resume must preserve the original Pod lineage and must not require the completed replacement inventory or strategy-restore receipt before the provider can produce them. Once terminal, cxcli must re-read the node and require the existing exact completed provider successor proof before journaling the Pod successor.
- AC-016: A later-segment protected-login successor may consume its immediately completed predecessor only when bridge cleanup completed before the predecessor's final login revalidation, that revalidation completed before segment completion, and the next segment started afterward. The archived target identity, successor lineage, released source hold, current live workload, image, container, restart, SSH host keys, and current final-Helm restore proof must remain exact; pre-cleanup or post-completion revalidation remains inadmissible.
- AC-017: After the first controller-inspector admission preflight seals the exact current Node set, an automatic continuation must replay that sealed set instead of expanding it to a newly joining controller-bridge Node. Every recorded Node name, UID, provider ID, and system UUID must still match a fresh live read, the sealed set fingerprint must remain exact, and the later pre-writer all-Node census must still include every current Node before authority transfer.
- AC-018: When an incomplete controller-bridge phase has durably accepted a real provider operation for one exact temporary bridge node group, command entry must defer fresh source discovery and retain the journal's current segment without reclassifying the intermediate report as a config waypoint until the executor reconciles that operation. The checkpoint remains authoritative for the temporary provider delta; the executor must still re-read and validate the exact operation, node-group identity, and intended postcondition before further mutation.

#### Negative Criteria

- NC-001: The implementation must not add a legacy checkpoint schema, timestamp bypass, mutable chronology refresh, manual checkpoint edit, or compatibility shim.
- NC-002: An empty segment must not invent a provider rollout or copy the post-Jail release receipt into a synthetic rolling receipt.
- NC-003: Historical bridge authority must not be treated as current RPC authority after target-singleton takeover; cleanup may consume only its sealed release lineage together with fresh runtime and immutable-child proofs.
- NC-004: Fast verification must not require a synthetic group for a zero-group segment, treat a missing journal as empty proof, or accept a completed journal entry outside that segment's locked scope.
- NC-005: Validation must not filter or ignore residual Pods, delete by a broad source label, or retire a Ready Pod, target-matching Pod, foreign-owned Pod, live-ReplicaSet Pod, or stable-fingerprint-drifted Pod.
- NC-006: An empty rolling journal must not authorize arbitrary Node drift, substitute for a bridge create receipt, or allow one bridge or rolling operation to prove a Node whose name or node-group lineage does not match that operation.
- NC-007: Remediation approval must not be inferred from a fresh uncheckpointed capture, omit the exact approved delta digests, override a blocked delta, or accept a changed approval-required plan merely because its raw snapshot remains healthy.
- NC-008: A generic released hold, release timestamp, Pod name match, or target-ready handoff state must not substitute for one exact release receipt or weaken successor UID, workload, runtime, image, and host-key validation.
- NC-009: Final restore must not recreate a source-era partition, infer retirement from a missing live row alone, trust an unmirrored or malformed target-pause receipt, discard an in-progress restore plan without proving the exact supported target-retirement transition, or invalidate a completed restore by reintroducing a consumed target predecessor from a historical phase journal.
- NC-010: A generic missing node group, pre-dispatch `NOT_FOUND`, unjournaled or unaccepted delete attempt, missing or changed provider operation ID, mismatched ownership identity, transient operation lookup failure, or non-`NOT_FOUND` delete failure must not be promoted to terminal controller-bridge cleanup evidence.
- NC-011: Final MK8s verification must not query a retired exact temporary bridge group as retained infrastructure, exclude groups by name or label alone, trust a malformed bridge journal, or omit a managed-existing or non-bridge source group.
- NC-012: A pending controller rollout must not journal a provisional Pod/node successor, accept a foreign or non-Ready node, substitute a different provider operation, weaken the terminal successor proof, or turn the expected pre-terminal observation into `recovery-required`.
- NC-013: Admission replay must not add an incomplete joining Node to the sealed set, drop or rebind a recorded Node, accept a changed sealed fingerprint, or weaken the strict Node identity checks used by the initial preflight and later writer fences.
- NC-014: Discovery deferral and waypoint bypass must not activate for a blank, synthetic, unaccepted, malformed, or already completed controller-bridge operation, infer authority from a temporary-looking live node-group name, advance the completed waypoint index, or bypass the executor's exact provider-operation reconciliation.

#### Validation Method

Run fail-first terminal-successor, controller-lineage, empty-segment scope,
continuous manager-pause, source-cleanup, ReplicaSet zero-fence, and exact
residual-orphan recovery regressions, including an empty-segment protected-state
Node delta bound to its exact controller-bridge create receipt; run adjacent
topology authority and source-cleanup guards, remediation fingerprint stability
and stale-real-drift guards, target-ready and zero-session login-release
successor guards, target-partition retirement and in-progress restore-rebind
guards, exact post-dispatch node-group delete `NOT_FOUND` convergence and
accepted-operation lookup-absence guards, exact final-MK8s temporary-bridge
inventory exclusion and retained-group drift guards, changed-scope static checks, and
the full migration executor. Include a command-gate regression in which the
gated Pod reaches an exact replacement controller node while its accepted
provider operation is still pending, then require the unchanged terminal proof
after convergence. Include an admission-preflight continuation whose sealed
source Node set remains exact while a provider-pending bridge Node is visible
before its kubelet publishes complete runtime identity. Include command-entry
coverage for an incomplete planned controller bridge with one exact accepted
provider operation, prove its already-refreshed intermediate report is not
reclassified as a config waypoint, and reject incomplete or synthetic operation
records. Resume the unchanged authorized live command from the authoritative
checkpoint.

#### Test Method

Model successful receipts followed by later acceptance or observation
timestamps, an intentionally empty segment beside a non-empty drifted segment,
same-phase and cross-phase pause reuse, and a post-Jail release projected
through typed-GRES worker successors. Exercise zero-of-zero convergence and
missing, unexpected, or incomplete fast-verification journals. Tamper each receipt hash, target,
generation, topology, pause, fleet, worker scope, runtime, and immutable-child
binding independently and assert fail-closed behavior before mutation. Model a
ReplicaSet recreating a child during cleanup, then require the zero fence to
prevent it; separately model exact non-Ready orphan residuals after a completed
receipt and reject target, readiness, ownership, selector, UID, receipt, or
stable-fingerprint drift before deletion. Model a bridge Node addition beside an
explicitly empty rolling journal and reject node name, node-group label,
scheduling identity, terminal operation, or campaign-mirror drift. Model the
initial all-Node admission preflight and its sealed replay: the initial path
must reject incomplete identity, while replay must ignore only additional Nodes
and reject disappearance, deletion, UID, provider ID, system UUID, or sealed-
fingerprint drift for every recorded Node. Model discovery entry with a real
accepted temporary bridge node-group operation and prove fresh discovery is
deferred and waypoint reclassification is bypassed without advancing completed
segments; remove its operation kind, request timestamp, or real provider ID and
prove both exceptions are denied. Model both
canonical protected-login release receipts and reject a missing or malformed
target-ready authority, source Pod mismatch, target workload mismatch, runtime
identity drift, image drift, restart, or host-key drift before successor
adoption. Model an exact target-only partition receipt without the optional GPU
smoke release gate, then reject phase-mirror, inventory, controller-gap, target
name, target-record, runtime-reload reassertion, authority epoch, final config,
active pause journal, or live restore-reconciliation drift before excluding any
source record. Model post-restore target-record consumption and reject a changed
stored restore, superseded-plan link, target successor receipt, or remaining
retired-source journal before cleanup.

#### Evaluation Method

Accept the change when every original live signature is reproduced before its
repair, exact receipts resume through one canonical selector afterward, all
drift cases remain blocked, and the authoritative campaign either completes or
reports the next causally independent pending boundary without out-of-band
state changes.

<!-- /REQUIREMENT: REQ-007 -->

<!-- REQUIREMENT: REQ-008 status=active priority=P0 type=reliability -->
### REQ-008: Continue through exact remediations by default

#### User Story

As a Soperator upgrade operator, I need cxcli to perform an exactly proven
remediation without stopping for a second approval, so managed and external
upgrades continue unattended while unsafe or changed recovery state still
fails closed.

#### Acceptance Criteria

- AC-001: Both `soperator upgrade` and `ext-soperator upgrade` automatically consume an exact persisted protected-state remediation fingerprint after one fresh read-only verification proves that the baseline, classification, blocked deltas, and approval-required deltas are unchanged.
- AC-002: The first approval-required protected-state comparison is checkpointed before automatic approval. The same invocation performs at most one approval-specific recapture; a changed plan, missing proof, or blocked delta stops without mutation.
- AC-003: Both upgrade commands automatically adopt an exact journaled Slurm intent-to-held crash-window transition when live state still matches the recorded request. Identity or state drift remains recovery-required, and cxcli does not dispatch a second hold request.
- AC-004: `--stop-for-remediation-approval` opts into the review workflow. It checkpoints the exact remediation plan or Slurm recovery boundary and stops; rerunning without the flag uses the normal automatic policy after revalidation.
- AC-005: `--approve-remediation` and `--no-approve-remediation` are removed without aliases or compatibility shims. Ordinary execution approval, backup-recovery approval, checkpoint identity, writer quiescence, and terminal no-mutation guarantees remain independent and unchanged.
- AC-006: Checkpoint and report evidence record whether remediation handling used the `automatic` or `stop-for-review` policy while retaining the exact approved fingerprint when one is consumed.
- AC-007: Generated repeat and resume commands omit the stop flag for the default automatic policy and include it only when the operator explicitly selected review mode.

#### Negative Criteria

- NC-001: Automatic mode must not approve a fresh uncheckpointed comparison, retry a moving remediation plan until it happens to stabilize, override a blocked data-loss or downtime delta, weaken identity checks, or mutate after terminal verification.
- NC-002: Review mode must not imply approval, and an ordinary `--approve` or `--approve-backup-recovery` must not authorize protected-state or journaled Slurm remediation.
- NC-003: The implementation must not retain legacy remediation-approval flags, hidden aliases, dual policy paths, or checkpoint migration shims.

#### Validation Method

Run focused safety, managed and external orchestration, Slurm crash-window,
CLI parsing/help, command serialization, documentation-alignment, lint, and
full unit checks. Validate source behavior without running a live upgrade.

#### Test Method

Model fresh stable, previously persisted stable, changed, blocked, and malformed
protected-state plans at validation and terminal gates. Model exact and drifted
journaled Slurm holds for both upgrade commands, and assert no duplicate hold
RPC. Exercise the new stop flag, removal of both legacy flags, policy audit
evidence, and default repeat/resume command rendering.

#### Evaluation Method

Accept the change when both commands continue through exactly checkpointed and
revalidated remediations by default, review mode stops at the same durable
boundary, all unsafe or drifting cases remain fail-closed, and local validation
passes without claiming live campaign completion.

<!-- /REQUIREMENT: REQ-008 -->

<!-- REQUIREMENT: REQ-009 status=active priority=P1 type=usability -->
### REQ-009: Present external upgrade dry runs as optional

#### User Story

As an external Soperator upgrade operator, I need onboarding and deploy
guidance to present the dry run as an optional inspection step, so I can either
inspect the campaign first or start the approved upgrade directly without being
told that a dry run must be accepted.

#### Acceptance Criteria

- AC-001: External Soperator onboarding next steps label the `--dry-run` command as optional and label the `--execute --approve` command as the direct upgrade action.
- AC-002: Render/deploy routing, deploy-block guidance, and onboarding help use the same optional-preview and direct-execution model for one or multiple external targets.
- AC-003: Upgrade guidance does not say that a dry run or dry-run plan is accepted; accepted onboarding actions remain a separate campaign concept.
- AC-004: The command contract is unchanged: exactly one of `--dry-run` or `--execute` remains required, and mutation still requires `--execute --approve`.
- AC-005: Focused CLI tests assert the new headings, command ordering, and absence of the superseded dry-run acceptance wording.

#### Negative Criteria

- NC-001: The wording change must not make dry run implicit, remove execution approval, change campaign acceptance, or add a legacy output path.
- NC-002: Guidance must not imply that an optional preview is a prerequisite for execution.

#### Validation Method

Run the focused onboarding-next-step, render/deploy-hint, deploy-block, and CLI
help tests, plus Ruff and diff checks for the changed surfaces.

#### Test Method

Exercise single-target and multi-target guidance, assert both copy-paste
commands remain exact, and reject every former phrase that described accepting
a dry run before execution.

#### Evaluation Method

Accept the change when every external-upgrade guidance surface identifies dry
run as optional, presents execution independently, and preserves the existing
explicit-mode and approval behavior.

<!-- /REQUIREMENT: REQ-009 -->
<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
