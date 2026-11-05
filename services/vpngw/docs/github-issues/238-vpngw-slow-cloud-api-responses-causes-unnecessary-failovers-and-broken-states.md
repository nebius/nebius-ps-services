# Issue 238: vpngw: slow cloud API responses causes unnecessary failovers and broken states

- Repository: `github.com/nebius/nebius-ps-services` (`R_kgDOPRGzqg`).
- Issue: [#238](https://github.com/nebius/nebius-ps-services/issues/238), API ID
  `6119890527`, node ID `I_kwDOPRGzqs8AAAABbMYeXw`.
- Owning project: `services/vpngw`.
- Affected projects: `services/vpngw` only; no shared skill changes.
- Reviewed revision: `d4d4c5852d05a055c67cfac380e489d19749d5f8` on
  `nebius-23985`, clean at intake. Observed upstream `main`:
  `4e176eb519175b5f70dac0746c2c100a5762580d`.
- Review date: 2026-11-02.
- Planning status: Complete; independently reviewed and verified on 2026-11-02.
- Implementation status: Scheduling and replacement-lifecycle repairs are implemented.
  The latest candidate passes local/native checks and supported recovery checks;
  complete two-pair live qualification remains incomplete, with a strict traffic failure.
- Priority: P1 / High, for an HA availability defect; no delivery date implied.

## Problem and evidence

The supported VM-HA controller must detect genuine owner loss while retaining
strict cloud fencing. Slow dependency observations must not themselves masquerade
as missing peer transport. The report describes cloud API retries overlapping
fencing activity and a subsequent Static routing failure.

The core scheduling defect is supported by current source and a credential-free
experiment. The precise incident sequence and the Static consequence are not
fully established by the supplied excerpts. A `disable-active` event does not
prove takeover; `stop-former-owner` records a fencing action starting, not its
completion. The three supplied module hashes match neither reviewed revision,
so the deployed version and full incident outcome remain unknown. Those limits
do not block repair of the independently demonstrated scheduling defect.

### Source evidence

Paths below are relative to this project; line references describe the reviewed
revision and must be refreshed if implementation starts on another revision.

| Boundary | Verified observation |
| --- | --- |
| `src/nebius_vpngw/agent/main.py:934-1059` | `VMHASnapshotAdapter.observe()` captures the peer before synchronous cloud, readiness and route observations, samples decision time afterward, then publishes the heartbeat. Incoming updates during the wait can be absent from that decision. |
| `src/nebius_vpngw/agent/main.py:2322-2408` | The current sender runs from the completed-snapshot callback. Receive already runs in a separate listener thread. |
| `src/nebius_vpngw/agent/vm_ha/runtime.py:621-635` and `src/nebius_vpngw/deploy/vm_ha_cloud.py:824-840` | One cloud observation uses sequential allocation, former-VM, candidate-VM and allocation reads. |
| `src/nebius_vpngw/deploy/vm_ha_cloud.py:21-75` | HA requests allow 30 seconds overall/authentication, 10 seconds per retry and three attempts; journalled operation waits have a separate 300-second bound. |
| `src/nebius_vpngw/agent/vm_ha_controller.py:1171-1212,1258-1300` | Peer freshness combines transport recency with health/readiness booleans. Ten-second peer timeout and five-second suspicion can lead to `STOP_FORMER_OWNER`. Merely sending unknown readiness as false would retain false suspicion. |
| `src/nebius_vpngw/agent/vm_ha/runtime.py:2521-2540,2768-2779` | Route mutation already requires exact current ownership. Non-owners still need cloud reads for safe readiness and takeover. |
| `src/nebius_vpngw/agent/vm_ha/models.py:217-358` | The strict heartbeat-v4 format has readiness booleans but no independently aged health evidence. |
| `src/nebius_vpngw/agent/vm_ha/runtime.py:2947-2990` | Accepted peer payload and receipt time are separate mutable fields; restored payloads intentionally have no fresh receive time. |

Upstream main sends a heartbeat before the controller step and performs additional
cloud/readiness observations to construct it. The local branch's completed-snapshot
publication removes duplicate reads, but still waits for each observation. This
branch-only improvement is not proof of a released fix. Both revisions capture
peer evidence before cloud waits. No GitHub closing PR or timeline entry existed
at review intake. Targeted duplicate search returned #233 (canonical observation
path ordering) and #235 (initial XFRM host firewall materialization); neither has
the same underlying defect. The report's proposed separate Static issue was not
identified by that bounded search.

### Investigation checks already performed

- Existing test
  `test_default_runtime_keeps_heartbeats_fresh_during_multiple_cloud_observations`
  in `tests/unit/test_vm_ha_agent_runtime.py`: **1 passed in 0.27 seconds**.
  It models separate three-second observations, not one observation exceeding
  the peer timeout.
- An in-memory variation of that test changed only the simulated observation
  duration from three to twelve seconds. Maximum heartbeat gap was **13 seconds**,
  and the less-than-ten-second assertion failed as expected. Temporary state was
  isolated and removed; product and test files were unchanged.
- This experiment proves a scheduling failure, not two-node cloud takeover,
  incident reproduction, SDK latency, or workload packet loss.
- No cloud, gateway, credential, route, service or deployment operation was run.

## Decision and scope

Accept the demonstrated heartbeat/observation coupling defect for implementation
at P1 / High. A false HA failure signal can interrupt forwarding and initiate
Compute fencing. Reach and frequency are unknown; the report describes an
infrequent event, which does not make an availability failure low priority.
There is no established safe workaround: increasing peer timeouts delays real
failure detection, and active-only cloud access removes standby safety evidence.
Effort and regression risk are substantial because transport, evidence freshness,
checkpoint replay and update ordering interact. Reuse the existing controller,
transport and SDK to limit ongoing support cost.

Scope includes outgoing scheduling, independently aged observations, coherent
incoming snapshots, automatic suspicion, unstarted-action cancellation, lifecycle
shutdown, capability/update gating, and Static/BGP regression coverage.

Non-goals: implement or claim the separate Static-route repair without evidence;
change SingleVM behavior, public CLI/YAML, configured roles or ownership rules;
introduce a consensus service, new credentials, another cloud writer, active-only
cloud reads, a dependency upgrade, legacy readers or mixed-wire fallback;
extend the peer timeout, suspicion or repair/recovery windows; or guarantee
availability during loss of authoritative cloud evidence.

## Design

### Runtime ownership and scheduling

Keep the existing controller thread as the sole owner of cloud SDK use, mutable
cloud/route adapters, decisions, checkpoints and effects. Moving those objects
into an unrestricted thread pool is unnecessary. Add a dedicated periodic sender
and one read-only local-health sampler, retaining the existing receive listener.
This isolates heartbeat delivery from cloud work while preserving one effect path.
No sampler or sender may enable forwarding, repair services, change routes,
move allocations, start/stop VMs or write controller authority.

Use an immutable, atomically published evidence record and short locks solely for
record exchange. Never hold those locks during SDK calls, subprocesses, socket
I/O, receipt persistence or worker joins. Publish the received heartbeat and its
receive time as one tuple. Refresh that tuple after slow observations, immediately
before policy evaluation, and at automatic-stop admission.

The sender has one sequence-reservation owner, a one-second monotonic cadence,
and a two-second total transport-attempt budget including retries. Skip missed
send slots rather than queueing bursts. Preserve exact mTLS leaf/epoch identity,
boot binding, durable anti-replay high-water marks and sequence reservation before
sending. Payload timestamps describe transmission; they do not renew evidence.

The local sampler runs at most one job, aiming for one-second cadence with a
five-second cumulative budget across all subprocesses, including per-neighbor
checks. Use isolated read-only collectors rather than sharing mutable controller
readiness objects. Capture effective configuration, owner incarnation, guard/mode,
tunnel-selection and recovery identity before collecting; recheck them before
publishing and discard path readiness spanning a transition. Track daemon service
liveness separately against boot/configuration identity and the same five-second
age bound. Path churn may retain that service evidence only for the original
bounded Static recovery claim. Slow or partial path checks produce unknown path
evidence, not healthy forwarding or an invented confirmed failure.

### Evidence contract and decision semantics

Replace the HA-only heartbeat with one strict v5 contract under REQ-008's existing
pre-adoption allowance. Retain the existing identity, parity, policy and bounded
recovery fields. Add typed evidence for local dataplane health, cloud/route
observation status and controller operation/progress. Each sample carries a
process-UUID plus monotonically increasing sample counter, sender-measured age and
collection duration. Health states distinguish healthy, confirmed unhealthy and
unknown. Cloud status distinguishes complete, in progress and unavailable;
reported past ownership remains advisory and never grants authority.

Local positive samples are valid for less than five seconds from collection start
at the sender. Each process sends random 128-bit challenges, keeps at most sixteen
with immutable first-send times, and expires them after ten seconds. It echoes the
last authenticated peer challenge. Receiver health proof requires the expected
peer and generation, an echo of a challenge issued by the current local process,
and a round trip below ten seconds. Initial age upper bound is sender sample age
plus local elapsed time since that challenge was first sent. Add receiver monotonic
time thereafter; positive health expires at twenty seconds of that upper bound.
Retries never reset the challenge clock. Repeated sample IDs cannot extend expiry;
new unknown supersedes positive, and restart loses proof. Existing wall timestamp
filters remain admission checks, never a cross-host clock-offset guarantee.

Serving-path evidence additionally requires observed active forwarding and full
local path readiness. Prepared or cold standby readiness cannot protect a fenced
owner indefinitely. A pending Static recovery retains sampled service liveness
for its original bounded recovery claim but cannot assert a healthy path. Optional
probe health requires a fresh bounded probe; the sampler does not update the
controller's hysteresis counters or persistent records.

A fast authenticated sender is not a health authority. Apply these separate rules:

| Evidence | Automatic decision |
| --- | --- |
| Exact peer is alive, independently fresh local path is healthy, and the candidate's own current cloud observation identifies that same owner with matching generation/policy | Cloud-observation or cloud-route-verification delay alone does not start or continue unhealthy-peer suspicion. It does not certify overall readiness. |
| Missing or stale authenticated transport | Retain the existing ten-second peer timeout and five-second suspicion, with no additional grace. |
| Confirmed local path failure | Preserve the existing five-second owner repair or exact bounded Static recovery claim and all candidate-readiness/fencing gates. |
| Local health unknown or expired | Do not suppress suspicion indefinitely or claim readiness. Use the existing bounded unready-peer path; no extra repair budget is granted for unknown data. |
| Candidate cloud evidence unavailable, ambiguous or incomplete | Block transfer and effects; never use a peer claim or cached record as replacement authority. |
| Planned transfer or a cloud effect already submitted or possibly accepted | Heartbeat refresh does not revoke the operation; reconcile its existing durable authority and cloud-operation journal. |

Implement the predicates once and reuse them in controller and status/redundancy
projection, restoration/policy agreement consumers and operation admission.
Fresh liveness with unknown authority must display degraded/unknown, never ready.
Tunnel-recovery claims keep their exact owner/incarnation and absolute deadline;
repeated heartbeats cannot renew a repair episode or its allowed grace.

Cloud reads remain on both members. Regular observation gets one cumulative
30-second deadline covering authentication, all constituent reads and retries;
pass the remaining budget through the existing request-timeout mechanism. Reject
partial or overdue observations. Do not treat completion of one subrequest as a
fresh complete snapshot. Sender projections retain original observation ages and
identity, and cannot authorize new effects. Preserve the existing guarded failure
path on an observation error. This does not promise uninterrupted forwarding
through a prolonged control-plane outage.

Do not apply this observation deadline to a journalled mutation's separate
300-second operation wait. Do not install a short process watchdog that kills
legitimate cloud effects. Every mutation retains fresh operation-specific cloud
and writer/inhibition checks; regular cached observation never replaces them.
The sender remains responsive during bounded long effects but reports their
actual progress and unknown evidence rather than synthesizing ready status.

### Automatic stop and concurrency boundaries

Before the first automatic `STOP_FORMER_OWNER` submission, revalidate current peer
evidence, parity, candidate readiness, cloud ownership and inhibition under the
existing writer lock. If fresh healthy evidence invalidates an automatic
suspicion and no Stop request has been submitted, atomically retire the pending
action, suspicion and associated automatic lineage. A saved pending action must
not replay after cancellation. Keep planned transfers distinct.

Once submission may have happened, including a lost response before the operation
ID is known, do not erase authorization or pretend cancellation succeeded.
Reconcile through existing replay-stable idempotency and authoritative cloud
readback, retaining the journal and fail-closed state. Former Compute owner must
still be `Stopped`, its alias absent and the allocation exact on the candidate
before promotion, route reconciliation or forwarding.

Bound all workers and queues; there is one outstanding health sample and no
per-tick SDK task creation. A timeout is not proof a thread stopped: Python
running futures cannot generally be cancelled. Sampler work uses cumulative
subprocess deadlines and late results are discarded by sample/lifecycle identity.
Use a publication generation for start, transition and shutdown. Once closing
begins, no completing worker can publish positive evidence again. Stop sender
admission, invalidate readiness, install the existing forwarding guard, stop and
join sender/sampler/listener within their I/O bounds, then close owned resources.
A worker failure becomes a controller-visible guarded failure, not silent thread
loss. On join timeout, keep the guard and fail the service; never close resources
under a surviving user or allow a late result to restore authority.

### Alternatives and research basis

| Option | Assessment |
| --- | --- |
| Current completed-snapshot sender | Small and already improves duplicate reads, but the demonstrated single-read delay remains. |
| Only shorten API waits or enlarge peer timeouts | Less code, but does not isolate slow local checks/effects and trades false suspicion against delayed real detection. Explicit request bounds are necessary but insufficient. |
| Add a sender that repeats old readiness booleans | Reject: refreshes stale health and cannot distinguish unavailable authority from proven path failure. |
| Move all controller/cloud work into concurrent workers | Reject: creates SDK/adaptor and effect-ordering races without benefit to this scoped fix. |
| Independent evidence-aware sender with bounded local sampler | Selected: timely liveness, explicit health uncertainty and existing serialized cloud authority; costs coordinated protocol and lifecycle work. |

Research used local source, canonical requirements/design, the complete selected
issue discussion (no comments at intake), related issue checks and primary vendor
sources. Organizational Slack/Confluence was unnecessary for the code-established
question and was not consulted. Python 3.10-3.12 and the existing dependencies
remain fixed; all behavior is deterministic. Application/AI stack selection and
AI-agent design are inapplicable.

The lockfile and installed environment use Nebius SDK 0.3.40. Installed
`nebius/aio/channel.py` and `request.py` matched the official release wheel bytes;
the downloaded wheel SHA-256 matched release metadata. Its synchronous calls
operate through an SDK-owned/provided loop. Keep use and closure in the existing
controller owner instead of assuming arbitrary concurrent reuse. No SDK upgrade
is proposed. Current SDK documentation describes newer runtime behavior and must
not be treated as a guarantee for this older pin. Nebius documentation was read
through nebiusDocs; direct version-source retrieval through Tavily was insufficient,
so official release-distribution verification supplied the pinned evidence.

Primary sources, retrieved 2026-11-02:

- [Nebius Python SDK request guide](https://docs.nebius.com/sdk/python/send-request):
  native asynchronous request and operation-wait patterns; it does not establish
  a provider latency SLA or deployed resource state.
- [Nebius 0.3.40 release metadata](https://pypi.org/pypi/nebius/0.3.40/json):
  versioned distribution identity used for the local source comparison.
- [Current Nebius SDK runtime documentation](https://github.com/nebius/pysdk/blob/main/README.md):
  documents later 0.5 runtime changes, reinforcing the version boundary.
- [Python 3.10 asyncio tasks](https://docs.python.org/3.10/library/asyncio-task.html):
  blocking work must be isolated from responsive scheduling.
- [Python 3.10 futures](https://docs.python.org/3.10/library/concurrent.futures.html):
  running calls are not stopped by future cancellation or a caller wait timeout.

The `nebius` consultation preserves cloud/provider ownership and bounded SDK
operations without credentials or live inspection. The `system-design-rules`
review focused on single effect ownership, partial failures, temporal evidence,
shutdown and deployability. Its findings are incorporated above: atomic peer
records, cancellable unstarted decisions, sample invalidation, monotonic aging,
SDK confinement and a two-member update barrier.

## Implementation work

Execute in dependency order. Each phase must meet its exit evidence before the
next starts; this plan authorizes none of these implementation effects itself.

1. **Freeze the implementation basis and reconcile canonical specs.** Recheck
   source, issue discussion, pending branch changes and exact installed candidate.
   Use `maintain-project-specs` for the paired requirements/design amendment:
   TI-REQ-003/004/005/008, TI-DES-002/003/004/005, REQ-008, REQ-025 and the current
   completed-observation heartbeat rule in FEAT-023. Preserve all fencing and
   repair invariants. Replace the synchronous-publication design explicitly;
   do not silently reinterpret old health booleans. Exit: one accepted paired
   contract and regression oracles; no claim of implemented behavior.
2. **Establish the failing composed regressions.** Extend
   `tests/unit/test_vm_ha_agent_runtime.py`, `test_vm_ha_controller.py` and
   `tests/integration/test_vm_ha_runtime_composed.py` with barriers and injected
   clocks. Prove slow-cloud outbound starvation and the fresh-receive-during-wait
   hazard independently. Exit: failures occur for those reasons before repair,
   without cloud credentials or sleeps used as timing luck.
3. **Define and integrate the canonical evidence contract.** Update
   `agent/vm_ha/models.py`, `transport.py`, `store.py`, `runtime.py`,
   `auto_healing.py`, restoration/policy consumers and their tests. Add a focused
   private evidence/scheduling module under `agent/vm_ha/` instead of growing
   `main.py` with another general framework. Bind new capability to both installed
   and running controller checks; update `agent/main.py`, `build.py` and the
   deployment capability/activation consumers found through the existing handshake.
   Exit: strict serialization, anti-replay, age/identity and mixed-version-negative
   tests pass; no alternate reader or old/new fallback remains.
4. **Deliver independent publication end to end.** Wire the single sender,
   immutable records, bounded read-only local sampler and controller-owned cloud
   projection in `agent/main.py` and `agent/vm_ha/runtime.py`. Propagate the
   cumulative observation deadline through `deploy/vm_ha_cloud.py` without changing
   journalled mutation bounds. Separate mutable controller readiness state from
   sampler inputs. Exit: blocked SDK/local checks/effects cannot block transport,
   stale samples remain unknown, jobs are bounded and no observer mutates state.
5. **Integrate policy and safe cancellation.** Update `agent/vm_ha_controller.py`,
   `_vm_ha_transfer_effect_guard`, checkpoint/lineage handling and status/restoration
   projections. Refresh peer evidence after waits and before automatic Stop;
   retire only proven-unstarted decisions and preserve uncertain accepted effects.
   Exit: the fault matrix below passes with exact stop/detach/attach ordering,
   true failure detection and unchanged planned-transfer/recovery authority.
6. **Finish lifecycle and deployability.** Update close/error handling, capability
   gates and the existing apply activation workflow to establish durable
   two-member inhibition before either wire changes. Keep the current systemd
   forwarding guard and resource restrictions; change the unit only if a verified
   lifecycle need requires it. Exit: race/restart tests prove no late healthy
   publication, no SDK-after-close and no release of mixed-version inhibition.
7. **Qualify, document and hand back.** Update project README's HA architecture,
   degraded-cloud/status guidance and upgrade instructions, `[Unreleased]`, and
   canonical implementation/independent verification evidence through
   `maintain-project-specs`. Run project quality gates and `$align`. Exit: all
   claimed local/native/live results identify the exact candidate and their
   proof boundaries; remaining external acceptance is explicitly not run.

## Tests and acceptance

All checks below are future implementation gates, except the investigation
checks explicitly recorded earlier. Test both configured ownership directions,
Static, BGP and mixed HA. Preserve SingleVM Static/BGP regressions.

| Scenario | Required observable outcome |
| --- | --- |
| Delay one cloud read by 12 or 20 seconds, then return a valid observation; delay each read stage independently | With healthy local paths and peer transport, heartbeats continue within the sender schedule/budget; no automatic Stop, alias transfer or route mutation is caused solely by the delay. |
| Receive fresh peer state during blocked observation or after checkpoint save | Final decision uses one coherent newest tuple. A proven-unstarted automatic Stop is durably retired; restart cannot replay it. |
| Heartbeat arrives after ambiguous Stop submission | Journal/authorization remains; authoritative reconciliation precedes any further effect. |
| Both members see retries, throttling, permission errors, unavailable or partial cloud state | No promotion or cached authority; bounded guarded outcome with honest degraded status and no reciprocal speculative fencing. |
| Slow local health or route checks, changing tunnel selection, large BGP peer set | Sender remains responsive; late or mixed-identity path evidence is discarded; independently fresh service liveness retains only the original bounded recovery grace. Expired health cannot grant readiness or suppress suspicion indefinitely. |
| Long legitimate journalled cloud effect | Heartbeats remain responsive without a short watchdog terminating the effect; original operation ID, deadline and writer serialization remain intact. |
| Repeated sample IDs, delayed frames, clock skew/jumps, restart/replay | Test challenge expiry/eviction, reordered echoes, asymmetric loss, process restart, unknown supersession and repeated samples; wall-clock admission is not freshness proof. Sending never extends sample life; stale/unknown evidence never authorizes readiness. |
| Genuine owner death, peer network loss, confirmed local forwarding failure | Existing bounded detection/recovery and candidate readiness still lead only through former-owner Stopped, detach, exact attach, fresh ownership, route receipt and forwarding. |
| Sampler/sender exception, shutdown with blocked work, publication after guard installation | Guard remains installed, positive publication stays disabled, workers terminate within bounds, no resource closes under active users. |
| Apply, mTLS rotation, planned failover/failback, rearm and interrupted protocol upgrade | Durable inhibition and exact operation authority hold; mixed wire cannot transfer or resume forwarding; both controllers must prove the new contract before release. |
| Legitimate Static promotion | Verify the new owner's native Static path and existing VPC next hops independently. Any separate route defect remains visible and separately scoped; do not mark it fixed by heartbeat success. |

From the project root, use the existing environment for focused checks:

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q   tests/unit/test_vm_ha_agent_runtime.py   tests/unit/test_vm_ha_controller.py   tests/unit/test_vm_ha_runtime_ports.py   tests/unit/test_vm_ha_cloud.py   tests/unit/test_vm_ha_transport.py   tests/unit/test_vm_ha_auto_healing.py
.venv/bin/python -m pytest -p no:cacheprovider -q -m integration   tests/integration/test_vm_ha_runtime_composed.py   tests/integration/test_vm_ha_failover.py
.venv/bin/python -m ruff check --no-cache src tests misc
.venv/bin/python -m mypy --no-incremental
```

Then run the project-native complete unit/integration gates and build checks after
implementation, with the existing dependency environment. Use the documented
`python3 tests/systemd/run.py` only in the disposable Linux fixture with its
required kernel capabilities. Native frozen-cloud tests do not prove Compute
fencing or real allocation transfer. Validate modified Markdown and diff integrity.

A separately authorized non-production live trial must freeze the exact candidate,
expected product workflow and delay-injection boundary. Inject delays only in
cloud API traffic; keep peer heartbeat traffic independent. Observe both directions
of workload traffic, heartbeat arrival, effect journals and independent Compute,
allocation and route truth. Also remove real owner/peer availability as a negative
control. Fixture/recovery actions remain distinct; any intervention that performs
a product-owned step invalidates the affected proof and requires a clean replay.
No live acceptance has been executed by this review.

## Rollout and risks

Use the supported `apply` lifecycle. Stage both artifacts, inhibit the non-owner then owner, prove both writers
quiescent, and stop both under independently verified forwarding guards before
installing either package. Bind the artifact and barrier to the exact lifecycle
transaction; interrupted updates retain inhibition.
Do not release it until both running controllers advertise the new capability,
prove fresh bidirectional v5 traffic while fenced. Release and verify the exact
owner before releasing the standby and proving authoritative readiness. An
installed-wheel check alone is insufficient. This clean-break upgrade can interrupt
service; do not promise a rolling zero-downtime update or permit mixed-version
fallback. No additional SingleVM migration is introduced.

Before activation, a failed candidate may be replaced while both members remain
inhibited and guarded. After new wire/state is active, use a coordinated supported
pair update or roll forward; do not downgrade one member, hand-edit state, erase
replay/checkpoint history or bypass fencing. Preserve pending cloud-operation
receipts across recovery and verify authoritative postconditions before release.

Main risks are hiding a stuck controller behind a live sender, renewing old
health evidence, invalidating an accepted Stop, sharing mutable collectors,
unbounded subprocess work and mixed-version update races. The mandatory tests
above address each. Expose secret-free heartbeat gap, sample age/status, cloud
read duration/result, controller phase/deadline and discarded-sample reasons in
internal diagnostics without expanding the public status identity surface.

No tenant-specific identifiers, journal request IDs, hostnames, credentials or
incident configuration are included. The issue's Static consequence still needs
independent incident/version evidence and a failing reproduction. It is not a
condition for accepting the proven heartbeat defect and is not claimed resolved.

## Maintainer handoff

The original review and plan did not implement or publish the change. The
subsequently authorized implementation and its evidence are recorded below.
Commit, push and PR publication remain separate repository workflow actions.

## Implementation checkpoint (2026-11-02)

REQ-026 / FEAT-025 own the accepted contract. The implementation adds isolated
publication/sampling, authenticated causal freshness, serving-path distinction,
atomic receive records, cumulative observation deadlines and exact pre-dispatch
Stop cancellation. Upgrade regressions cover failure and retry at both members'
lock, quiescence, guarded stop and package boundaries. Public CLI/YAML and SingleVM
interfaces remain unchanged.

An intermediate service-evidence candidate passed 3,303 unit tests, 137 host
integration/build tests and the 86-test native Linux lane. Type and Python lint
checks also passed. The later stop-barrier candidate and its final qualification
are recorded below; these intermediate results do not verify the final artifact.

Live testing exposed six implementation defects and supplied reproductions:
apply checked inhibition before controller acknowledgement; the service loop
retried a guarded controller after its publisher had permanently stopped; and
path-identity changes erased independently sampled daemon liveness during bounded
Static tunnel recovery. Apply now waits for the exact acknowledgement. A failed
controller exits so systemd recreates its complete runtime behind the guard.
Path churn now invalidates only path evidence; fresh daemon liveness retains its
five-second expiry and can support only the original recovery deadline. A cloud
timeout before package work also exposed late persistence of upgrade intent,
which blocked resume. The first durable successor now pins artifact intent; an
unmarked retained pair must establish the complete upgrade barrier before packages.
Counterfactual and regression tests reproduce the old failures and pass after
these corrections, including expiry of the original recovery grace.

A successful cutover and restoration also exposed owner redundancy status following
the standby's cloud-progress promotion flag. A 65-second observation found healthy
owner and standby status throughout while redundancy toggled with ordinary cloud
reads. The status projection now combines causally fresh standby path evidence
with exact owner-local cloud truth and the current promotion-bound rearm receipt.
A composed producer-to-receiver-to-status-to-CLI test covers this boundary and
rejects stale, unknown, serving, stopped or incorrectly attached peer evidence.
The exact failing terminal predicate in that CLI invocation was not captured;
its attribution is inferred from the independently reproduced mismatch.

The research/design/troubleshoot consumer audit found a further bounded defect:
Static recovery consumed service liveness without receiver-side causal freshness
when path evidence was unknown. Service and path proofs now expire independently;
repeated samples cannot renew either proof, and Static recovery requires fresh
service proof while retaining its original deadline and cloud ownership checks.
The source-backed rationale is recorded in FEAT-025; none of these changes extends
failure timers or grants cloud authority from a heartbeat.

The preceding lifecycle candidate passed all six Static/BGP 12/20/35-second API
fault trials. Its later Static tunnel failback caused a premature takeover and
remains failed evidence. Recovery required a bounded Start of the stopped,
alias-free former member; that trial and dependent state are marked intervened.
Both selected gateways are confirmed non-production. Qualification was restarted
for the service-evidence candidate. This failed trial supplies no live fixed claim
or separate Static-route consequence claim.

The next candidate passed all six API-fault trials and the Static tunnel cycles,
but its first BGP tunnel failover timed out and led to VM takeover. The sampler
was invalidating every pending tunnel operation, including BGP native samples
that already proved healthy forwarding. A counterfactual against that frozen
runtime reproduces the defect with native command fixtures. The correction retains
only freshly verified readiness for an eligible, bound BGP connection; unknown
BGP, missing RIB/XFRM and forwarding failure remain non-serving. Static recovery
semantics and all deadlines are unchanged. The original live native-path health
was not captured, so this defect is not claimed as its proven sole cause. Full
qualification was restarted for the corrected candidate; the failed BGP trial
and supported recovery remain separate evidence.

The following Static apply failed closed before package replacement when a
dependency start restarted the controller after shutdown. An isolated systemd
255.4 experiment reproduced the accepted late start with the original stop and
proved its rejection with an irreversible aggregate stop. The deployment barrier
now drains both writers and their installed dependent services together, then
requires inactive states, zero main/control processes, no pending jobs, successful
guard execution and current-boot forwarding fencing. This matches systemd's
[documented job replacement semantics](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html).
The precise caller of the original live late start was not captured. Supported
resume is recorded as recovery. The revised candidate passed 3,308 unit, 137 host
integration and 86 isolated native tests, followed by a fresh complete supported
apply on both pairs. The final live assessment remains separate from recovery.

### Cross-cloud timeout and retry review

AWS recommends selecting request timeouts from downstream latency percentiles
and an acceptable false-timeout rate, including network overhead. Google Cloud
recommends bounded, idempotency-aware retries with exponential backoff and jitter,
and warns against layering retry loops. AWS's static-stability guidance keeps
established data-plane operation independent of control-plane impairment.
Sources: [AWS timeouts and retries](https://builder.aws.com/content/3EumjoZascWd1oZiEgL8ORlv3qE/timeouts-retries-and-backoff-with-jitter),
[Google Cloud retries](https://cloud.google.com/storage/docs/retry-strategy), and
[AWS static stability](https://docs.aws.amazon.com/whitepapers/latest/aws-fault-isolation-boundaries/static-stability.html).

These principles support the selected independent heartbeat/health workers,
cumulative observation deadline and durable mutation reconciliation. They do not
establish a Nebius backend latency defect or justify a larger timeout without
representative measurements. Current tests deliberately impair API connectivity;
they measure gateway resilience, not provider-side request latency. The 30-second
observation budget remains an application policy, not a claimed provider latency SLA.
Indefinite forwarding under lost ownership authority is outside this design.

Installed Nebius SDK 0.3.40 performs three total attempts with the configured
request bounds, but its immediate-error retry path has no backoff or jitter.
That is a separate resilience opportunity; no incident evidence currently ties
it to this scheduling defect. A future retry owner must wrap individual typed
read RPCs and pagination, re-evaluate the same remaining deadline and disable
nested SDK retries only for those calls. Retrying the entire snapshot is unsafe
because it also retires adoption state and durable transfer lineage. Mutation
submission and accepted-operation reconciliation retain their existing ownership.

## Frozen-candidate qualification (2026-11-03)

The final immutable candidate passed 3,308 unit/composed tests, 137 host
integration/build tests and 86 isolated native Linux/systemd/FRR/strongSwan tests.
Ruff, mypy over 86 source files and wheel construction passed. All 86 installed
source hashes match the frozen source and wheel on both authorized non-production
pairs. Both pairs completed fresh supported coordinated apply. The independent
systemd late-start counterfactual and its cleanup passed; the original live caller
remains unknown. No separate historical pre-package process snapshot was captured:
that boundary is supported by the product-enforced stop/postcondition gates,
source and emitted-shell regressions, and native counterfactual.

All six API-impairment trials and two complete VM and tunnel failover/failback
cycles per routing mode passed their non-loss gates. The 12/20-second trials
impaired the owner; the 35-second exhausted-observation trials impaired the
standby and verified guarded restart. The latter do not qualify owner availability
under a 35-second outage. Independent observations proved exact ownership,
former-owner Stop and detach before candidate attachment, one forwarder, restored
managed and peer routes, reciprocal fresh v5 traffic and released apply locks.
Both gateways ended on their preferred owner.

Twenty-five complete bidirectional steady windows provided 50 directional
measurements, each transmitting 1,200 packets at the declared 20 packets/second.
The normal strict loss target remains below 0.2%; the existing REQ-025 temporary exception was used after three declared
bounded path/owner variants, with every direction strictly below 1% and every
non-loss gate passing. This accepts the measured run; it does not fix packet loss.

| Mode | API trials | Transition steps | Steady directions | Maximum loss | Normal-target failures |
| --- | --- | --- | --- | --- | --- |
| STATIC | 3 | 8 | 24 | 0.333% | 5 |
| BGP | 3 | 8 | 26 | 0.250% | 1 |

The accepted evidence combines the original final-candidate API trials, all
Static cycles, and the BGP preferred-owner tunnel and manual VM cycles with a
fresh BGP replay of the automatic VM cycle and alternate-owner tunnel cycle.
The replay began after supported recovery, with a new readiness check and clean
baseline. Recovery itself is excluded from qualification. Prior candidate failures,
intervened recoveries and invalid observer measurements retain their original
verdicts.

The private continuous traffic collector initially stopped before a transition
ended. Its replacement proves readiness before the action, complete coverage
through saved cloud history, matching stop intent and process quiescence. Two
later BGP steady windows exceeded the frozen 65-second duration limit despite
sending all 1,200 packets. A serial diagnostic repeated that timing failure.
A separate fixed-slot collector then passed real-host controls and a negative
stall test before the targeted replay. It records every send time and rejects
missed slots, incomplete counts and send failures. No timing or completeness
criterion was relaxed, and no collector repair is claimed as a product fix.

The GCP route evaluator also distinguished a newly present output-only MED field
from a changed route. The earlier baseline omitted the field, so its value remains
unknown. Acceptance required unchanged route identity, next hop, priority and
AS path, complete router-route equivalence, healthy peers and independently read
native export metrics. Six negative mutations still fail the corrected evaluator.
See the official [router status fields](https://docs.cloud.google.com/compute/docs/reference/rest/v1/routers/getRouterStatus).

All task-owned fault rules, traffic collectors, temporary workload resources and
fixture credentials were removed; independent VM/disk absence and watchdog
quiescence were checked. Native fixtures were also cleaned. The source-level
scheduling defect is verified fixed within this declared scope. The original
Nebius backend/network/authentication cause and the separately reported Static
route consequence remain unknown. Broader REQ-025 release-upgrade qualification
is not claimed by this issue-specific result.

## Alignment follow-up (2026-11-03)

The subsequent alignment pass found HA-DEPLOY-001: a new missing-standby replacement
inherited the completed predecessor's heartbeat artifact intent. A different
reviewed wheel was rejected as an interrupted-apply mismatch; the same wheel after
an upgrade could later produce conflicting initial and upgrade intents. The
existing tests had omitted predecessor artifact intent.

The lifecycle owner now moves that completed intent into exact member/cycle audit
history. The CLI supplies the reviewed artifact when constructing the first durable
replacement successor. Read-only preview remains provisional; it grants no effect
authority. Interrupted replacement retains its new artifact and rejects drift.
Tests reproduce the former failure, exercise same/different artifacts and both
predecessor intent kinds, reject altered audit history, and execute the real CLI
through the first verified write before stopping ahead of provisioning or SSH.

The current correction passes 3,325 unit/composed tests, 137 host integration/build
tests, Ruff and mypy across 89 configured files. CLI help checks passed, and the
independent deployment/security review found no remaining blocker. The host lane
skipped 86 native tests; those and the live trials have not been repeated for this
updated candidate.

This correction changes only CLI/lifecycle source; the heartbeat runtime is
unchanged. It has not been deployed or replayed on the live gateways. The native
and live results above remain attached to the earlier immutable candidate with
wheel SHA-256 `3dd3ae64f9cb6b0c62b3b89c09d403c89bc1b698be8692497763abbac4efd93d`.
REQ-026 is active and FEAT-025 delivery is implemented until the updated candidate
meets its complete qualification gates. This preserves the earlier evidence while
avoiding a live-qualified claim for changed source.

## Replacement counter repair and final qualification

HA-LIFECYCLE-002 was a pre-existing decimal parser defect: `[2-9][0-9]*`
omitted cycles 10–19, 100–199 and similar ranges. These are successive replacement
attempts for the same member, not the count of automatic traffic failovers.
One shared grammar now covers lifecycle discovery, effective Compute/disk bindings,
cloud effect identity/observation guards, and historical cleanup inventory. Cycle
one retains its unsuffixed spelling; later cycles use canonical ASCII integers
at least two, without leading zeroes or alternate encodings.

Regression coverage includes boundary values through 1,000, malformed keys,
retained owner isolation, verified persisted missing and failed-passive replacement
histories through cycles 9→10→11, real accepted-operation lookup after journal
reload, and CLI first-intent/resume cases with exact artifact history and drift
rejection. New tests failed against the old grammar before the repair.

The final candidate still requires its full native and live qualification. The
approved live scope includes one exact non-owner replacement on each test pair,
preserving predecessor disks and reserved allocations, with a controlled client
interruption after a durable accepted operation and supported resume. Qualification
uses one frozen artifact, fresh evidence and the complete Static/BGP matrix.
Historical results above remain attached to their original candidate.

### Missing-peer identity preflight admission

The final counter candidate passed 3,450 unit tests, 137 host tests and 86 isolated
native Linux/systemd/XFRM tests. Its supported Static pair installation also passed.
The subsequent real missing-standby trial stopped safely twice before accepting a
replacement cloud operation: once in planning and once in approval revalidation.
The predecessor disk and lifecycle record remained intact.

Bounded observations localized an additional client admission race. A missing peer
causes the guarded controller to restart; systemd startup temporarily publishes
`blocked / identity-proof-pending` before establishing a fresh verified runtime
identity. Otherwise identical owner and route evidence alternated between pending
and verified identity, so read-only previews could pass while revalidation failed.

The CLI now waits at most 30 seconds only for that explicit pending state with an
exact guarded owner, unchanged route receipt and no concurrent writer or effect.
It requires verified identity before admission, caps each SSH read by the remaining
budget, and rejects responses arriving at or after the deadline. Actual identity
failures, unavailable or malformed proof, ownership drift and transport errors
still fail closed. Regression tests exercise real runtime binding and SSH status
validation; the original pending-state reproducer failed before the correction.

This client-only correction requires a new frozen candidate and qualification.
The earlier native, installed and failed live trials remain attached to their
original artifact; no final replacement/resume qualification is claimed yet.

The accepted disk-create interruption was subsequently captured and its client
writers proved quiescent. Supported resume reconciled that operation, then the
Compute API rejected generated cloud-init exceeding its 32,768-character limit.
The client now compresses four bundled scripts using cloud-init's native `gz+b64`
encoding and validates the final enrolled UTF-8 payload against a conservative
32 KiB limit before provisioning. Host-key entries and identity anchors remain
unchanged. Local regressions cover decoded script contents, strict host-key
recovery, exact byte boundaries and rejection before replacement disk creation.
This additional repair requires a new candidate; recovery of the existing
transaction does not itself qualify that candidate on both members.

The compressed payload was accepted by Compute and replacement reached activation.
Recovery then exposed a separate policy-lineage defect: the canonical policy
initializer preserves a previous decision when rebinding an enabled policy to a
new generation, but replacement adoption and interrupted reproof accept only a
zero-predecessor initial decision. A local reproducer confirms rejection at the
CLI, agent and reproof boundaries. mTLS staging itself completed.

The interrupted transaction remains bound to its reviewed artifact. The previously available
public artifact-recovery path did not admit this activating checkpoint, and
generic policy changes correctly rejected its apply lock and active mTLS operation.
No journal edits, artifact-pin changes or manual runtime patches were used.
The approved repair centralizes canonical root/rebound policy admission and
persists the full original policy before adoption. A typed host recovery preserves
the existing transaction and resources, requires both writer-stop and forwarding
fences before package replacement, verifies exact installed source, and records an
audited artifact successor. Local tests exercise generation A/B/C and interrupted
prepare/commit and host recovery stages. Native and live recovery proof remain
separate from final qualification; no final-build stability claim follows from
these local checks.

### Historical V5 recovery and replacement checkpoint

The approved recovery extension restored the interrupted Static pair without
another VM deletion. Both members received the reviewed artifact only after
writer quiescence and forwarding fences. Independent verification confirmed the
original resource identities and audit history, retained predecessor disk,
one forwarding owner, fresh reciprocal heartbeats, terminal mTLS/policy agreement
and restored standby readiness. The first canonical resume returned an interrupted
result; a separately declared same-artifact public resume succeeded. The first
failure remains unexplained and is retained.

On BGP, the originally authorized standby deletion was performed with the final
candidate installed on both members. The first replacement attempt timed out
waiting for owner inhibition acknowledgement while automatic rearm was in
progress. Later public readiness and quiescence checks passed. A separately
declared retry captured an accepted disk-create checkpoint, terminated the exact
client process group, and resumed the same transaction. Independent cloud and
host checks verified reuse of that operation, one fresh VM and boot disk,
retained predecessor disk, unchanged allocations and route authority, preserved
history, and restored policy, mTLS and redundancy. GCP learned routes and peer
state returned to the declared baseline.

All 3,504 unit tests, 137 host tests and 87 isolated native Linux/systemd/XFRM
tests pass for this final source. Both pairs' initial steady traffic windows
passed the strict loss threshold. A later BGP planned failback passed its fencing
and ownership checks, but its following reverse traffic window lost 5 of 1,200
packets (0.417%), exceeding the below-0.2% criterion. That failed window is retained;
later healthy checks cannot turn it into a pass. The separate post-replacement
BGP steady window also failed in the reverse direction, losing 3 of 1,200 packets
(0.25%) with complete fixed-slot measurements and unchanged ownership/routes.
The cause of these losses remains unresolved.

Separate final-candidate trials dropped only owner API traffic for 12 seconds on
each pair. Both retained fresh healthy serving heartbeat evidence, started no
ownership action, preserved cloud attachments/routes and resumed valid cloud
observations within 30 seconds. The fault rules were removed. Static also passed
the strict bidirectional traffic criterion; BGP lost 5 of 1,200 packets in one
direction and failed it. These controlled faults establish bounded scheduling
resilience, not the cause or location of a real provider API delay.

These results do not establish full qualification. The original Static deletion
preceded the final candidate and its trial was intervened. The complete final
API-delay and transition matrix remains unfinished. The owner acknowledgement
delay does not establish a Nebius API backend cause, and the boot forwarding gap
below remains proposed follow-up work. No merge-readiness claim is made.

The task-created measurement VM and disk, temporary firewall rule, observers,
native fixtures and fresh fixture credentials were removed. Gateway predecessor
disks and allocations were retained; no publication was performed.

## Persistent boot-policy and diagnostic implementation

The approved follow-up is implemented. HA provisioning now persists forwarding
disabled. Guarded existing-host installation reconciles the product-owned sysctl
files and rejects unmanaged conflicting assignments before controller activation.
Ordinary gateways retain forwarding enabled. The same standalone module serves
bootstrap and package installation; recovery invokes it after installing the
package, while the host is fenced. Reapplying unchanged contents also repairs
ownership and permissions.

Negative readiness results now identify the failed predicates without exposing
identities or raw output. Convergence and direct apply failures preserve safe
phase/cause categories. Readiness success, ownership gates, heartbeat timers and
cloud-operation deadlines remain unchanged.

The first live apply exposed conflicting installed package metadata: older
Pydantic metadata described newer import files, leading dependency planning to
select an incompatible core. Admission now rejects different versions within
one installation prefix, while preserving separate-prefix shadowing and valid
same-version distro metadata. The failed trial and its intervened environment
recovery are preserved separately from final qualification.

Recovery also exposed a historical systemd failure result surviving a successful
current stop and guard. The barrier now checks the current stop, explicit guard,
current-boot fence and process/job postconditions. Current stop or guard failures
still reject progression; the historical failure record is not cleared.

Local checks cover 3,568 unit tests, 137 host integration tests, Ruff and mypy
over 89 source files. The later HA caller and shutdown repairs supersede the
preceding frozen artifact; exact-final-build native and live qualification remain
incomplete. The approved temporary steady-loss criterion is strictly below 1%
in each independently initiated direction; original below-0.2% verdicts and raw
measurements remain preserved. This exception changes no ownership, fencing,
route, resource-identity, recovery or measurement-completeness criterion.

Fresh replacement/resume on both test pairs, actual boot recovery, all six API
trials and sixteen transition steps remain required before final qualification.
Historical interruption causes and the location of a real provider delay remain
unknown. No merge-readiness or early-kernel continuous-observation claim is made.

### Interrupted existing-pair recovery extension — approved

The next activation attempt exposed three HA agent reload/materialization paths
that still requested ordinary persistent sysctl writes. The new boot policy
correctly rejected those writes. All HA paths now request runtime-only settings;
three reproducing cases failed before the repair, and 62 focused/composed tests
pass afterward. The preceding artifact's live trial remains failed.

The route-maintenance timer also remained enabled after the original stop
barrier. Its dependencies could restart services before its readiness condition
rejected the route action. The barrier now stops the timer and route/health
maintenance with the other writers. Both Static members were independently
verified fenced and quiescent after containment. BGP admission separately rejected
conflicting SDK metadata before any deployment mutation; its runtime is unchanged.
Both newly authorized replacement allowances remain unused.

Validation at that checkpoint passed 3,568 unit and 137 host integration tests,
type checking, linting and spec validation. The preceding artifact also passed
88 isolated native tests, but it predates the final caller and maintenance-stop
repairs and does not qualify them. Final checks found BGP forwarding with fresh
reciprocal heartbeats and the Static pair locked with forwarding disabled.
The temporary workload VM/disk, firewall rule, observer/native fixtures,
watchdogs and fresh fixture credentials were removed. All five recorded gateway
disks remain present. No final qualification or publication was performed.

The interrupted existing-pair transaction remains pinned to that preceding
artifact, with a host-only activation pending and no accepted cloud operation.
The approved replacement recovery does not admit this different transaction.
The user approved the following extension, conditioned on preserving existing
customer environments and normal `apply` upgrades with at most two minutes of
independently measured traffic interruption. Recovery from an already failed
upgrade is measured separately:

1. Admit only a retained two-member interrupted upgrade, with exact existing
   Compute, disk, allocation, route, generation, credential and SSH identities,
   a host-only pending effect, and no accepted or uncertain cloud operation.
2. Preview an exact recovery digest binding the predecessor lifecycle record,
   previous and repaired artifact hashes, both host observations and cloud
   ownership. Reject drift or competing writers before mutation.
3. Preserve the existing locks; independently fence and stop both members and
   dependent jobs. Require current-boot blocked guards, forwarding zero, no
   processes/jobs and no pending controller, rearm, tunnel or mTLS transaction.
4. Append durable recovery authority and predecessor artifact/operation history
   through the lifecycle owner. Install one reviewed repaired artifact on both
   hosts, preserving every VM, disk, allocation, credential and route identity.
5. Resume the canonical passive-first activation and reciprocal heartbeat proof;
   release only the independently verified cloud owner, then the standby.
   Repeated interruption must resume the same recovery without duplicate effects.
6. Test rejected identity/artifact drift, active writers, cloud uncertainty,
   failure between each durable phase, exact resume and no-resource-mutation
   behavior. Treat this restoration as intervened evidence; begin final
   qualification only from the independently verified healthy checkpoint.

The retained-pair recovery trial exposed a stale lifecycle snapshot in SSH trust
validation after the recovery authority was written. Recovery and its caller now
share one lifecycle journal; trust callbacks follow each authorized write while
rejecting independent drift. The real-store interruption regressions exercise
these checks after every durable boundary. The failed attempt remains recorded;
resume uses the same approved agent artifact. A new final build still requires
its own qualification before release.

The next handoff reached both installed packages but waited for a fresh lock
acknowledgement from a deliberately stopped controller. Prepared recovery now
requires fresh exact SSH trust, locks, stopped services/jobs, current-boot fences,
source and full package postconditions before canonical activation. The ordinary
running-controller acknowledgement remains required outside this approved
recovery state. The focused suite passes 698 tests, with 3,622 unit and 137 host
integration tests passing. The same-artifact public recovery completed. Independent
checks verified exact installed source, fresh reciprocal heartbeats, current-boot
guards, one forwarding owner and an unlocked standby. Each traffic direction
received 1,199 of 1,200 packets. This is recovery evidence; the final build still
requires clean normal-upgrade, replacement and failure-injection qualification.

A further regression reproduced a resume failure after a controller legitimately
advanced its sequence during activation. Recovery now fingerprints stable host
and credential identity, validates controller intent separately, and repeats the
stopped-writer barrier after interruption. Completed mTLS transaction progress is
allowed without changing certificate pins. Competing transfer/repair work, missing
controller evidence and stale fences still reject. After owner release starts,
normal apply acknowledgement remains the gate. The focused regression set passes
678 tests; the final candidate is rebuilt and requalified after this repair.

## Assessment of additional HA recommendations

The assessment below preceded the boot-policy implementation above. Its remaining
data-plane, watchdog and event-monitoring suggestions are separate follow-ups.

Keep the three-layer architecture and the existing 10-second freshness and
5-second suspicion defaults until measured restoration results justify a change.
The strongest next work is restart-safe fencing and scoped traffic evidence.

| Recommendation | Current implementation and proposed action |
| --- | --- |
| Startup fencing | Guard and current-boot ownership checks exist, but bootstrap still writes forwarding enabled. A newly created, inactive standby was observed with forwarding enabled and was contained using the public guard. This is a proven configuration gap, without observed dual ownership. Audit every forwarding writer; default HA boot to disabled and test real reboot/late bootstrap writes. |
| Data-plane probes | Static already supports source/XFRM-bound probes with debounce; the supplied test configurations do not enable them. Establish stable remote targets and negative controls, then design equivalent BGP probes. One endpoint failure must not authorize VM takeover. |
| Failure scope | Existing checks distinguish local tunnel, route and readiness failures. Add explicit remote/common-outage evidence and require a viable alternate before escalation. Test one tunnel, missing required routes, endpoint failure and protected-traffic blackholes independently. |
| Heartbeat semantics | Wire v5 already includes incarnation/sample identity, persisted sequence, challenge response and receiver freshness/replay checks. Keep healthy/unhealthy/unknown path evidence separate from degraded controller state. |
| Recovery hysteresis | Sticky tunnel selection, manual failback and sustained-health reset already exist. Avoid a broad cooldown that could prevent necessary fencing. |
| Cloud operations | Durable intents, accepted-operation reconciliation and idempotent effects already exist. Qualify timeout/crash boundaries rather than adding a second retry framework. |
| Watchdog | Add progress-based supervision for independent publisher, sampler and controller loops. Account for bounded cloud waits; a remote outage is not a stuck local thread. Test hangs and guard-before-restart. |
| Events | FRR JSON is already used. VICI subscriptions can later wake or invalidate the existing sampler, with bounded queues and periodic full resync. Events alone cannot establish traffic availability. |

Proposed delivery order, after completing the current recovery and qualification:

1. **P0: close the boot forwarding gap.** Set the persistent HA forwarding default
   to disabled, retain ordinary gateway behavior, and audit bootstrap, systemd,
   apply and recovery writers. Verify cold boot, late cloud-init writes and former
   owner restart. No candidate may forward until current ownership is verified;
   takeover must still prove the former owner Stopped and the shared allocation
   attached to the candidate.
2. **P0: add scoped traffic evidence.** Define stable remote endpoints and failure
   ownership before enabling existing Static probes or adding BGP probes. Start
   with observation and controlled negative tests; prove that a failed endpoint,
   one tunnel or a common remote outage cannot alone authorize VM takeover.
   Validate protected-path binding, required prefixes and independent workload
   traffic, including a blackhole while SAs and BGP remain established.
3. **P1: supervise progress.** Add stage-aware watchdog progress for the publisher,
   sampler and controller. Test each loop hanging independently, legitimate slow
   cloud operations, remote outages, and fencing before restart. A periodic timer
   that continues sending watchdog notifications while a critical loop is stuck
   does not meet acceptance.
4. **P1: optimize only after measurement.** Consider bounded VICI event wakeups
   with periodic resynchronization, and tune recovery hysteresis from measured
   traffic restoration. Keep the existing heartbeat timers and ownership rules
   unless a separate experiment demonstrates a better safe bound.

These are proposed implementation slices. Their acceptance criteria must be
frozen separately before live mutations; this assessment does not claim that
those features have been delivered.

Continuing to serve during API loss needs an explicit, bounded ownership grace
and a safety argument. The current authority contract fails closed when cloud
ownership cannot be verified. Bounded slow-call resilience is supported; any
longer ownership grace needs a separate safety design. Only the shared private next-hop allocation moves in this topology;
member public IPs remain fixed.

Google Cloud Router explicitly does not support BFD for HA VPN BGP sessions
([Google documentation](https://docs.cloud.google.com/network-connectivity/docs/router/concepts/bfd)).
strongSwan 5.9.13 provides IKE/CHILD events through
[VICI](https://github.com/strongswan/strongswan/blob/5.9.13/src/libcharon/plugins/vici/README.md);
these are useful triggers for reconciliation. A
[systemd watchdog](https://www.freedesktop.org/software/systemd/man/255/systemd.service.html)
requires recurring progress notifications, beyond the current initial readiness
notification.

Qualification should separately measure detection, former-owner stop/detach,
allocation attach, VPN/BGP convergence and sustained workload recovery. Add
one-way/two-way heartbeat partitions, dead or stuck agents, former-owner reboot,
and blackholes with SAs/BGP still established. Independent workload traffic stays
mandatory. These recommendations are proposed follow-up work, not implemented
features or completed qualification.

### Normal upgrade after a completed replacement

The first final-build Static upgrade passed the approved 120-second traffic gate:
its independently measured maximum reply gaps were 90.6503 and 90.5523 seconds.
The corresponding BGP upgrade failed after package preparation and before
activation. Its predecessor was a completed replacement, and an obsolete
predecessor flag attempted replacement-only policy persistence inside the new
normal-upgrade transaction, which correctly had no replacement cycle. Both
members remained fenced with apply locks; truncated traffic observations from
that failed command are not a passing interruption measurement.

Public-apply regressions using the real lifecycle store reproduced the exact
missing-cycle exception in both Static and BGP cases. The repair scopes policy
adoption and persistence to the current replacement. The original replacement
still reconciles policy before its terminal checkpoint. Both reproducers and
757 affected tests pass; the complete local suite has 3,636 passing unit tests
and 137 passing host integration tests, with 88 native-only tests skipped there.
The failed live trial is preserved. Guarded restoration and a fresh final-build
qualification remain separate requirements; these local results do not establish
release readiness or the two-minute customer-upgrade gate for both gateways.

### Later final-build qualification and repeated replacement defects

A subsequent frozen build passed normal `apply` on both test pairs with complete
independent traffic observations: Static maximum reply gaps were 90.350592 and
90.251787 seconds; BGP gaps were 119.350157 and 119.303642 seconds. Both meet the
approved two-minute gate, with little BGP margin. These are build-specific results.

The following Static missing-standby trial failed. A typed Compute not-found
response escaped the owner's cloud observer before inhibition could be installed,
causing controller fencing/restarts. FRR's service start limit then prolonged local
recovery. The source repair preserves an existing owner only with two stable
allocation reads and exact running local NIC ownership. Missing peer state is
UNKNOWN and cannot supply the mandatory STOPPED takeover proof. Real reader,
adapter, runtime and controller tests reproduce the old failure for either role.

Supported restoration created the replacement but failed before peer verification.
A second replacement retained two trusted peer leaves at epoch two while the
survivor retained epoch one. Inferring current Compute membership by highest
credential epoch became ambiguous. A real managed-store/CLI repeated-replacement
regression reproduces it. The repaired snapshot selects the trusted pending peer
from the exact active transaction, preserving certificates and persisted formats;
missing, foreign or untrusted transaction authority still fails closed.

The guarded recovery admission now covers this exact activated-but-unverified
checkpoint and an independently fenced owner, retaining all completed effects,
resource identities and matching committed policy. Fresh source, native and live
recovery verification are required. This failed qualification and its intervened
restoration remain distinct: replacement/resume, API faults, the complete cycle
matrix and final cleanup must not be reported as passed from normal-upgrade proof.

### Recovery journal and stopped-service validation

The repaired gateway artifact passed all 88 native Linux checks and ordinary
Static/BGP upgrade-and-no-op canaries. Recovery then exposed two client-side
validation gaps before package installation. Its preview admitted an acknowledged
policy, but its durable codec still required an unacknowledged initializer. The
actual interrupted checkpoint and a real store compare-and-swap regression
reproduced that rejection. The codec now accepts the admitted policy, while
authorization requires exact standby activation and the identical policy already
bound in the replacement journal. No history is reconstructed or overwritten.

The next stop reached a fresh current-boot blocked guard and forwarding zero, but
FRR retained a historical failed/start-limit state with zero PIDs and no job.
The emitted shell reproduced the rejection. The repaired barrier accepts that
label only after the current stop and guard succeed and every process, job and
fence check passes; neither a live process nor a failed current guard is accepted.
Both recovery evidence consumers use the same quiescence rule. This follows
systemd's distinction between a retained failed state and current process/job
properties ([systemctl documentation](https://www.freedesktop.org/software/systemd/man/255/systemctl.html)).

The updated client passes 3,687 unit tests, 137 host integration tests and type
checks for all 90 source files. It is tracked separately from the unchanged
Linux-tested gateway artifact during restoration. Guarded Static restoration
subsequently completed through the public command. Independent checks proved exact
installed source, preserved VM/disk/IP and transaction identities, a single cloud
owner and forwarder, current-boot guards, fresh reciprocal v5 heartbeats, committed
mTLS and policy agreement, and cleared locks/inhibition. Both workload directions
delivered 1,200 of 1,200 packets in the closing window.

The unchanged BGP pair's closing window delivered 1,192 and 1,189 of 1,200 packets:
0.667% and 0.917% loss. Both pass the approved strict-under-1% exception and fail
the original strict-under-0.2% criterion. Keep both verdicts. Temporary workload,
firewall, observer and credential fixtures were removed; gateway disks were
retained.

A new candidate includes the client fixes, but still needs its complete native,
ordinary-upgrade and two-gateway qualification. The failed trials are preserved;
restoration cannot qualify replacement/resume or the two-minute normal-upgrade
gate. The initial pre-effect approval revalidation interruption remains
unexplained. Another full trial requires a renewed live window and an additional
Static non-owner deletion allowance, retaining all gateway disks.

### Recovery package metadata correction

The next read-only normal-upgrade preview found two gateway version records in
the recovered Static standby, although its installed source matched the recovery
wheel. The fresh/recovery installer used `--ignore-installed` for the product and
then reinstalled it; the older distribution record could remain. A real offline
pip regression reproduced this after successive versions. Fresh/recovery hosts
now use the same selected dependency plan and record-aware installer as normal
upgrades, retaining narrow apt dependency shadowing. The planner takes the exact
approved wheel and closes its temporary dependencies on success and failure.
Conflicting installed metadata still rejects before a normal upgrade. This
restores the existing package contract without changing public commands.

The repair passes 3,687 unit tests, 139 host integration tests and type checking
for 90 source files. The real pip repeated-version and apt-shadowing regressions
pass; the earlier native artifact does not qualify this source change. Exact
final-build native and live qualification remain required. Target metadata
restoration is separate recovery evidence, never a normal-upgrade pass.

Pip documents that `--ignore-installed` overwrites installed packages; its 24.0
installer removes predecessors only through the reinstall/uninstall path. See
[pip install](https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-I) and
[the matching pip 24.0 installer](https://github.com/pypa/pip/blob/24.0/src/pip/_internal/req/__init__.py).

### Replacement reboot firewall ordering

The next candidate passed its native lane and both normal HA upgrades, with
observed traffic gaps of about 91.30 seconds for Static and 119.85 seconds for
BGP. The BGP result has only about 0.15 seconds of margin against the approved
120-second bound. These passes belong to that artifact alone.

Its Static replacement/resume failed first during package preparation with an
unexplained I/O error. A separately recorded recovery installed the package but
failed during peer-firewall activation. Current-boot evidence showed systemd
deleted UFW's start job to break an ordering cycle: the product override waited
for `network-online.target`, while the distribution UFW unit precedes
`network-pre.target`. Saved UFW policy was enabled but its live chains were
absent. The first rule addition wrote saved policy and then failed to reload
missing chains; repeating the addition merely skipped the saved rule.

The repair delivers one canonical early-ordering asset through fresh bootstrap,
ordinary apply and HA apply. Only the known product predecessor, the exact new
asset or an absent override is admitted; foreign contents reject before gateway
mutation. Saved rules and forwarding policy remain unchanged. Real systemd
verification reproduces the cycle with the old override and removes it with the
proposed asset; its exit code alone is insufficient because cycle diagnostics
can accompany exit zero. See [systemd unit search and ordering](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html)
and [Ubuntu systemd-analyze verification](https://manpages.ubuntu.com/manpages/noble/man1/systemd-analyze.1.html).

The failed replacement trial remains failed. Recovery/component interventions
cannot qualify it, and the original package I/O interruption remains unexplained.
Restoring the existing fenced replacement is separate recovery evidence. The
repaired artifact has since passed 3,700 unit tests, 139 host tests and 89 native
tests. Six authentic ordinary guest upgrades from releases 0.6.0, 0.6.1 and
0.6.2, in Static and BGP modes, also passed saved-state, unchanged-reapply,
old-peer interoperability and strict steady-traffic checks. Those isolated
guest tests do not establish cloud discovery or real kernel-reboot behavior.

Its clean normal HA upgrades passed with maximum observed traffic gaps of
91.45 seconds for Static and 110.90 seconds for BGP against the approved
120-second limit. Both pairs had the exact candidate sources afterward, with
one forwarding owner each, fresh heartbeats and preserved infrastructure
identities. The Static standby's already-recovered firewall ordering asset is
explicitly excluded from that upgrade's publication proof.
The remaining live qualification is still in progress; this is not a release
readiness claim.

### Interrupted ordinary-upgrade test diagnosis

A later isolated interrupted-upgrade test exposed a fault in the test itself.
It sent `SIGSTOP` before killing the operation owner to freeze a journal
snapshot. The matching sudo 1.9.15p5 implementation also suspends its supervisor
when the child stops. Killing that stopped child left its supervisor suspended
and holding the SSH channel open. Captured process states and an independent
paired control reproduced this behavior; directly killing the child allowed
the supervisor to exit immediately. See the matching
[sudo process supervisor implementation](https://github.com/sudo-project/sudo/blob/SUDO_1_9_15p5/src/exec_nopty.c).

The diagnostic caller still exited within the product's existing 630-second
channel bound. This evidence does not justify increasing that timeout. The
test's broad `install`-phase predicate had also selected a pre-install
`systemctl` process, so it did not prove interruption during package installation.
The corrected fixture requires an accepted live pip process and directly kills
the identity-checked owner without pausing it. A fresh 0.6.1 BGP resume replay
passed with saved state, tunnel identities, unchanged reapply and old-peer
interoperability preserved. Both steady-traffic directions received all 1,200
packets, and temporary fixtures were cleaned. The remaining version/mode
combinations still require the corrected test. Earlier failed trials stay
failed, and this fixture diagnosis does not explain the separate cloud
replacement I/O error.

### BGP tunnel transition export mismatch

The firewall-ordering candidate completed both clean missing-standby replacement
and interrupted same-artifact resume tests. Accepted disk-create operations were
reused, predecessor disks and allocations were retained, and redundancy returned.
Static also completed all eight declared VM and tunnel transition steps. Those
passes remain scoped to that candidate.

The first BGP tunnel-only switch unexpectedly triggered VM takeover, so the
combined HA qualification failed and later API-impairment phases did not run.
Two subsequent diagnostic switches succeeded; a third captured the native path
through a reverse switch. It showed about 4.8 seconds of export drift while both
BGP sessions, imports, routes, XFRM coverage and forwarding remained healthy.
A diagnostic launcher initially selected an older sampler; its incomplete capture
was retained separately and the corrected collector was verified before replay.

The selector persists requested tunnel policy before repeated cloud-authority
checks and FRR activation. Comparing still-serving predecessor advertisements
with that new intent can therefore clear heartbeat path readiness. A regression
reproduced this for normal and previously selected exports. This establishes the
policy-transition defect; the earlier takeover lacked the same native capture,
so its complete causal attribution remains less certain. It does not establish
a Nebius API service incident. FRR documents that `advertised-routes` reports
routes actually permitted and advertised to the peer, separately from configured
intent: [FRR BGP inspection](https://docs.frrouting.org/en/stable-10.7/bgp.html).

The repair limits alternate export proof to the isolated active health sampler
and a current-authority, eligible, outstanding BGP operation. It compares the
same fresh native evidence against exact normal or previous-path policies for
that connection only. Other connections, passive readiness, desired-policy
completion, route/XFRM checks, heartbeat timers and cloud fencing retain their
existing checks. This is not a timeout increase or a grace interval. The new
artifact requires fresh affected live validation; earlier artifact passes do
not qualify it. Full interrupted-upgrade coverage and real ordinary VM reboot
qualification remain incomplete, and the historical replacement I/O error is
still unexplained. Release qualification remains incomplete.

### Timeout-release coverage

The first transition repair subsequently passed all eight BGP VM/tunnel steps
and the 12/20-second owner and 35-second standby API-impairment trials. A separate
20-second delay after selection intent retained healthy serving evidence for
over 20 seconds without Compute stop or ownership transfer. The public request
timed out and the controller autonomously restored normal policy; this is
recovery proof, not successful completion of the requested tunnel switch.

A new controller regression then exposed an omitted intermediate policy during
release: the requested target can already be installed when timeout or failed
verification persists normal-policy restoration. The isolated sampler now also
admits that exact requested policy while the current operation is releasing it.
Both previous and requested paths must belong to the affected connection.
Deadline and failed-verification reproducers failed before this repair; the
focused suite and full 3,723-test unit suite pass afterward. Fresh live
qualification of this candidate remains required; earlier passes do not qualify
the new artifact.

The GCP peer cannot use BFD to replace these checks: Google documents that
Cloud Router BFD is unsupported for HA VPN BGP sessions.
See [Cloud Router BFD limitations](https://docs.cloud.google.com/network-connectivity/docs/router/concepts/bfd).

### Final-build progress and ordinary management admission

The timeout-release candidate subsequently passed all sixteen VM/tunnel
transitions and all six API impairment cases across both HA pairs. Its normal
retained-pair upgrades measured about 116.85 seconds of interruption for BGP and
92.30 seconds for Static, inside the accepted 120-second bound. Short and long
heartbeat partitions and the single failed BGP tunnel passed their declared
checks. Blackholing workload traffic while native control-plane checks stayed
healthy demonstrated the configured end-to-end detection gap; it did not add or
qualify a data-plane probe feature. A Static observer initially compared a bare
kernel host route with its `/32` configuration; the failed trial was retained and
a fresh replay passed after normalizing only that observer.

The real ordinary-VM lane then established authentic 0.6.0 runtimes and delivered
1,200/1,200 baseline packets in both directions. Its first candidate apply exited
before installation because deployment admission required an external ping
response despite successful authenticated SSH. The gateway's source, saved
configuration, service processes, boot and operation-journal absence were
unchanged; the observed traffic gap stayed below 56 milliseconds. This is an
upgrade-admission defect, not evidence of a Nebius API delay.

The repair uses the existing strict SSH policy, selected management user and
credentials for deployment reachability. Host-key mismatch fails immediately;
transient failures remain inside the original monotonic deadline. Bootstrap
checks remain mandatory. No customer firewall exception, timeout increase or
new public interface is required. The focused readiness and CLI suite passes
495 tests. The successor artifact still needs full exact-build qualification;
these earlier live passes do not qualify the source change.

The old release also exposed its historical bootstrap ordering issue: it writes
an FRR-owned file before package installation creates that user. That aborts the
file-writing loop and omits four later service overrides on the real fixture.
The authentic old runtime is recorded with those exact absences, without manual
repair; all old apply-owned assets remain required. Later releases already
install a root-owned bootstrap file after the FRR package. Cloud-init documents
that deferred file writes occur after user and package creation:
[cloud-init file writing](https://cloudinit.readthedocs.io/en/latest/reference/modules.html#write-files).

### SSH-readiness candidate qualification (2026-11-04, acceptance unresolved)

The successor artifact is `0.6.3.dev37`, wheel SHA-256
`b54cb075d6e22e2a3c9996d48d19535cbf100c04c8b0791228dab04193c58f76`.
Its 90 Python files and 21 packaged assets remained unchanged through the final
live trials and restoration checks.
Local verification accounts for 3,870 passing unit/host tests, including an
isolated successful rerun of the frozen-binary check after a test-environment
PATH failure. All 89 native systemd tests also passed. The prior-release upgrade
and declared live fault/reboot/replacement matrix are complete, and task fixtures
are cleaned up. The earlier 1.25% Static steady-loss failure remains unresolved;
these scoped passes do not establish merge readiness.

| Completed final-artifact check | Observed result |
| --- | --- |
| Normal BGP HA `apply` | Maximum traffic interruption 114.253 seconds; CLI elapsed time 262.29 seconds, measured separately |
| Normal Static HA `apply` | Maximum traffic interruption 94.801 seconds |
| BGP VM/tunnel cycles and API impairment | Eight transitions and all three 12/20/35-second impairment cases passed |
| Separate Static repetition | Eight transitions and all three impairment cases passed; highest steady loss 0.25% |
| Authentic 0.6.0 ordinary VMs | All four Static/BGP normal/interrupted upgrade cases passed, including unchanged reapply, actual kernel reboot, independent traffic, identity preservation and case VM/disk cleanup |
| Authentic 0.6.1 ordinary Static VMs | Normal and interrupted upgrades passed with at most 0.351 seconds of traffic interruption; real reboots restored readiness within 84 seconds, with zero loss in subsequent steady windows; both case VM/disk sets were removed |
| Authentic 0.6.1 ordinary BGP VMs | Normal and interrupted upgrades passed with at most 0.351 seconds of traffic interruption; real reboots restored readiness within 79 seconds; subsequent steady windows had zero loss and both case VM/disk sets were removed |
| Authentic 0.6.2 ordinary Static VMs | Normal and interrupted upgrades passed with at most 0.350 seconds of traffic interruption; real reboots restored readiness within 84 seconds; subsequent steady windows had zero loss and both case VM/disk sets were removed |
| Authentic 0.6.2 ordinary BGP VMs | Normal and interrupted upgrades passed with at most 0.351 seconds of traffic interruption; real reboots restored readiness within 83 seconds; subsequent steady windows had zero loss and both case VM/disk sets were removed |
| Actual HA standby reboots, both routing modes | Normal and API-unavailable cases passed; normal passive recovery took 44–45 seconds and maximum serving-traffic gaps were at most 0.151 seconds; temporary API fault groups were removed and original memberships restored |
| Final Static replacement and interrupted resume | Accepted disk-create operation reused; one new disk/VM, predecessor disk and all allocations retained; independent policy/mTLS/redundancy checks passed; maximum traffic gap 0.150 seconds and both steady directions had zero loss |
| Final BGP replacement and interrupted resume | Same operation-reuse, uniqueness, preservation and terminal-health checks passed; maximum traffic gap 0.146 seconds; steady loss was 0.75% in one direction and zero in the other, passing the exception while retaining the original-limit failure |
| Post-replacement Static sequence | All eight VM/tunnel transitions and three API impairment cases passed; highest steady loss was 0.583%, passing the exception |
| Post-replacement BGP sequence | All eight VM/tunnel transitions and three API impairment cases passed; highest steady loss was 0.417%, passing the exception |
| Short heartbeat partitions, both routing modes | Eight-second partitions caused no VM transfer; maximum traffic gaps were below 0.11 seconds |
| Long heartbeat partitions, both routing modes | Former-owner Compute stop preceded candidate attachment; maximum traffic gaps were 72.552 seconds for BGP and 74.749 seconds for Static |
| One failed BGP tunnel | Healthy backup and required routes remained available without a VM transfer; maximum traffic gap was 6.350 seconds; steady loss passed the exception, with one 0.25% direction failing the original limit |

All twelve ordinary-VM cases are complete: six normal upgrades and six
interrupted upgrades with public `apply` resume, each followed by unchanged
reapply checks and an actual kernel reboot. The largest upgrade traffic gap was
0.351 seconds; the longest reboot-to-readiness interval was 83.536 seconds.
Every post-upgrade and post-reboot steady window delivered all 1,200 packets in
each direction. Each case preserved gateway identities until its separately
authorized fixture cleanup. Shared ordinary-test infrastructure cleanup is also
complete: all 110 tracked resources and ten provider-created child resources
were independently absent, and temporary credentials and request files were
removed. Existing HA gateway resources were outside that cleanup scope.

Both final HA replacement/resume cases are also complete. Before each exact
non-owner Compute deletion, task diagnostics were removed from its retained
boot disk. The product's accepted disk-create checkpoint was captured before
terminating the exact client; its process group was proven quiescent before
supported resume. Independent checks verified reuse of the accepted operation,
resource uniqueness, preserved allocation and audit identities, healthy mTLS,
released inhibition and restored redundancy. The complete workflows took
609.019 seconds for Static and 628.858 seconds for BGP; these durations are
separate from their sub-0.15-second traffic gaps. The earlier Static package I/O
error did not recur, but its historical cause remains unknown. Fresh transition
and API-impairment sequences on both replacement pairs subsequently passed. Each
sequence restored the original active owner and healthy standby. Original-limit
failures remain recorded separately from passes under the temporary exception.

The native normal-upgrade matrix passed for all three prior releases in both
routing modes. One interrupted-matrix collection was invalid: its writer and
collector disagreed on the evidence directory, and detailed proofs were lost
during cleanup. Successful caller exits cannot qualify those six cases. The
original failure is retained. The repaired private collector checks writer paths
before launch and captures and verifies all declared evidence before deletion.
All six fresh interrupted cases passed against the unchanged candidate, with
111 evidence files captured and hash-verified before fixture cleanup. This native
matrix supplements the real-VM public-CLI and reboot cases; it does not replace
them.

The missing-route fault is now qualified on the final artifact. Its initial kernel delete selector
did not match routes backed by nexthop objects. A same-kernel isolated control
proved that matching the exact nexthop identity repairs that selector. A fresh
trial then recorded 91 deletions, but all six independent samples still observed
the route present and the maximum traffic gap was 0.117 seconds. Sustained route
absence was not established; no product detection failure is inferred. A scoped,
unsaved Zebra kernel-install filter subsequently produced 17 independent samples
of established BGP sessions with missing required routes. The product fenced
forwarding and stopped the former owner before transferring the allocation;
the maximum traffic gap was 78.453 seconds. That trial still failed before its
steady window: the verifier required a cleanup receipt from the fault process,
which the real former-owner reboot had terminated.

Independent checks confirmed the changed kernel boot, quiescent old fault unit,
and absence of the task filter from both running and persistent FRR configuration.
The failed trial remains unqualified. Twelve local controls validate the repaired
private cleanup check: a missing receipt is accepted only with a proven new boot,
quiescent old unit and independently absent task filter. A fresh trial passed
after supported public failback restored the original owner: 13 independent
samples confirmed established BGP with missing required routes; the product
responded within its existing repair/fencing bound, stopped the former owner
before attachment, and recovered autonomously. The maximum traffic gap was
81.903 seconds and both subsequent steady windows delivered all 1,200 packets.
Independent cleanup checks passed. The fixture never restores routes or
overwrites product-owned export policy, and the existing five-second local-repair
bound remains unchanged.

The first HA standby reboot evaluator incorrectly required an active-owner
readiness field. The canonical passive contract instead requires current-boot
fencing and a fresh standby-ready receipt, while the active-owner field remains
empty. The first evaluator repair also used the wrong clock domain for receipt
age. Both failed trials are retained. The corrected private checker uses the
VM's monotonic clock, validates exact identity and guard evidence, and passes
21 local controls plus a read-only live counterfactual. All four fresh actual
reboot cases subsequently passed. API-blocked post-SSH observations verified
current-boot fencing; this does not claim continuous observation from the
earliest kernel instructions. Recovery remained autonomous after restoring API
access. Temporary API fixture setup also required handling omitted empty
protobuf lists and referencing the canonical Static workload ledger; those
failures occurred before their respective reboot actions and are retained.
Neither evaluator nor fixture repair changes product code.

The seven additional fault scenarios also include one 20-second traffic
blackhole per routing mode. Both confirmed that native BGP/IPsec health can remain
green while workload traffic fails. These configurations have no end-to-end
probe enabled. The experiments establish that detection limitation; they do not
qualify a repair for it or turn native readiness into application-availability
proof. Temporary fault rules were removed after each scenario.

The first Static sequence remains **failed**: after a successful public VM
failback, one steady direction delivered 1,185 of 1,200 packets, or **1.25% loss**.
The opposite direction lost 0.667%. Both measurements completed every fixed send
slot without scheduling or send errors. Former-owner fencing, exact allocation
attachment, single forwarding ownership and restored redundancy passed. A later
separately declared repetition passed without changing the product or acceptance
limits. That demonstrates intermittency; it does not repair or erase the failed
window, which exceeds the approved strictly-below-1% criterion.

Bounded three-point packet-metadata diagnostics localized most missing packets in
one window between GCP guest egress and Nebius gateway decrypted ingress. A later
window lost two requests in the opposite direction, with consecutive gateway ESP
sequences and unchanged zero kernel XFRM error counters. All collectors were
removed. Google VPN packet/drop counters reported no drops in buckets overlapping
the failed steady window or either completed capture. Those counters use
60-second buckets and cannot identify individual test packets; they do not
exclude underlay loss or unreported provider behavior. The underlying cause and
provider attribution remain unknown. No speculative MTU, replay-window or timer
change was made. See the [Google VPN metric definitions](https://cloud.google.com/network-connectivity/docs/vpn/how-to/viewing-logs-metrics).

A subsequent four-point diagnostic admission failed before launching collectors
or sending packets. The private verifier reported an ambiguous package-metadata
error but had discarded the failing command's transport and error details.
Fresh read-only observations found the expected package on both members without
installation or repair. The original command's cause is unknown; that failed
admission supplies no package-corruption or historical I/O-recurrence proof.
The private verifier now preserves safe, classified command/error evidence before
asserting the unchanged baseline predicates, with six local controls.

A separately declared fresh diagnostic then captured the active gateway,
passive gateway and both workload endpoints. It lost two of 1,200 packets in
one direction and three in the other. All five first-missing boundaries were
between active Nebius gateway egress and the GCP guest. No corresponding test
traffic arrived at the passive gateway, and passive XFRM counters remained
unchanged. Active ESP sequences were consecutive: 2,400 outbound and 2,398
inbound, without observed sequence gaps or duplicates. Capture drops, parse
errors, bracketed queue-drop increments and XFRM error increments were zero.
Subsequent same-boot NIC error/drop counters were also zero. Thirteen local
analysis controls passed, and all four captures were preserved and hash-verified
before collector/root removal. These observations exclude passive misdelivery
for this window; they do not prove physical transmission, provider receipt or
the cause of the earlier 1.25% failure.

An initial provider-log query was truncated before the relevant windows and
cannot support absence claims. Complete, exact-window queries then correlated
proposal-rejection events by tunnel identity for the original failed window
and two diagnostics. All returned rejections belonged to the three sampled
inactive Static paths; the serving path remained established in the bracketing
metric samples. Thus those events do not establish serving-tunnel failure or
explain the packet loss. Sampled establishment and aggregate zero-drop counters
are not continuous packet-delivery evidence. Provider-side correlation of exact
ESP sequence/time boundaries remains the highest-value missing observation;
neither Nebius API attribution nor a causal product module has been established.

The ordinary-VM lane also exposed separate test-boundary issues. Its verifier now
includes the exact source-generated startup admission units. An authentic 0.6.0
BGP baseline required a declared, narrow inner-peer TCP/179 permit because that
release's interface parser retained the displayed underlay suffix; later releases
already remove it. The candidate independently had to install its own correct
XFRM-interface rule. Neither adjustment is a new product fix or proof of old
release provisioning.

Fixture cloud reads returned CLI exit 20 (`UNAVAILABLE`) and, in a later
pre-candidate baseline, exit 12 (`DEADLINE_EXCEEDED`). Fresh exact reads succeeded
without environment repair. The private harness now retries only these two
read-only failures, with at most three single CLI attempts inside its original
overall deadline. Negative controls reject write retries and unrelated errors.
The deadline case was replayed from an independently verified unchanged old
runtime checkpoint, then completed the interrupted upgrade and real reboot. Writes retain single submission and accepted-operation reconciliation.
The underlying network, endpoint or service cause remains unknown, and original
failed runs are retained. See the [Nebius exit-code reference](https://docs.nebius.com/cli/exit-codes)
and [CLI retry controls](https://docs.nebius.com/cli/reference/index).

Authentic 0.6.1 baseline setup separately reproduced the old CLI's external-ping
requirement. A declared fixture prerequisite temporarily allowed ICMP from the
existing operator SSH address while the old public `apply` installed the runtime.
Both old installations passed. The temporary rules were then explicitly absent,
the original rule identities and settings were restored, and repeated checks
confirmed blocked ping with working authenticated SSH before candidate admission.
This is test setup, not a customer firewall requirement or an old-provisioning
qualification claim.

The old installation also enabled two services after its final manager reload.
Systemd 255.4 sets a global `NeedDaemonReload` flag when enablement creates links;
that flag alone does not establish that a particular loaded service definition is
stale. The private verifier now accepts this historical state only for authentic
old releases when exact loaded unit hashes match, every observed definition
predates the recorded reload, only the expected enablement links are newer, and
the old operation and its effects are settled. Seven local controls reject stale
definitions, foreign links, unsettled operations and candidate-version exceptions.
Both old gateways passed the corrected checks with their boot, service processes
and journal unchanged. No manual manager reload repaired the test baseline; the
candidate still requires a clear reload state. See the matching systemd
[unit reload predicate](https://github.com/systemd/systemd-stable/blob/v255.4/src/core/unit.c)
and [unit-file enablement implementation](https://github.com/systemd/systemd-stable/blob/v255.4/src/core/dbus-manager.c).

Final cleanup and restoration are complete. All task workload fixtures, temporary
access/fault rules, collectors, test units and temporary credentials were removed;
the ordinary fixture's provider-created child resources were also independently
absent. Final checks confirmed both HA pairs healthy on the exact tested artifact,
both members running, the original active-owner selection, fenced standby,
unchanged desired configuration, member addressing, security memberships and
managed routes. All ten retained gateway disks and all five allocations per pair
were preserved. The approved replacement standby VMs and tested build remain.
No commit, push or merge was performed. Release acceptance remains unresolved
because the original 1.25% Static failure exceeds the approved exception.

### Post-qualification alignment (2026-11-04)

A subsequent source review found `ALIGN-LIFECYCLE-001`: the public apply
continuation verified recovered packages before binding the member used by its
error handler. A verification or I/O failure therefore raised `UnboundLocalError`,
masking the original safe reason and resume guidance. The handler now obtains
the member from the exact package plan before verification. This changes only
failure reporting; installation, lifecycle authority and forwarding gates remain
unchanged. It does not explain the historical package I/O incident.

Four new public-CLI regressions, covering both members and both failure classes,
failed with the original unbound-local error before the repair and passed after
it. They verify safe member/reason output, retained lifecycle identities,
lock/quiescence/stop calls, and no package install, activation or unlock after the
failure. Recovery admission/execution and remote fencing are mocked in these
regressions; they do not establish live guard behavior. The source repair
postdates the frozen artifact qualified above and has not been deployed or
qualified in native/live trials. Historical failed trials and the unresolved
1.25% loss retain their original verdicts.
