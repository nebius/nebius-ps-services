# Independent heartbeat implementation and live validation

Status after the October 30, 2026 US live transfer campaign: gateway-a is
active, gateway-b is verified standby-ready, and both pfSense BGP sessions are
Established. Planned takeover and failback completed with local standby route
repairs. This is not a clean unattended pass. The first transfer recovered after
the CLI reported failure. The final UK check found gateway-b forwarding and
gateway-a stopped with automatic restoration inhibited. See the US live results
below for timings, interventions, and evidence.

## Code and artifact

The independent feature branch is `codex/fix-vpngw-heartbeat-api-coupling`, based
directly on local main `5b73a95b`. Implementation commit: `db18fea6`.

Deployment branch `codex/vpngw-heartbeat-live-validation` combines the feature
with the earlier transit, firewall, static-route, and bootstrap fixes. Its code
commits are `fb7efe04` and `a1b57a8b`, on integration base `fcfffbe8`. The latter
preserves static-route candidate information in the read-only sampler.

Exact wheel: `nebius_vpngw-0.6.3.dev30-py3-none-any.whl`.

SHA256: `57c73c076b83994ddaa8ca4905437f92829b9b3832cbf649a18710f1a2a5a973`.

The artifact validator accepted the wheel, including its heartbeat capability,
required assets, and package integrity. All four installations matched its main,
runtime, and controller module hashes after the earlier recovery. Final US
checks reconfirmed the same artifact.

## Implemented behavior

- A dedicated publisher targets one heartbeat per second without cloud calls.
- A separate read-only local sampler targets one second, with a five-second
  aggregate command deadline. Controller mutations invalidate cached health.
- The controller retains cloud observation, repair, ownership changes, and
  fencing. Cloud ownership is advisory heartbeat metadata with a separate age.
- Strict heartbeat v4 separates service health, transfer readiness, forwarding
  readiness, and forwarding mode. Missing or stale health enters the existing
  suspicion path. The five-second suspicion window remains unchanged.
- Peer sampling occurs after blocking providers. Delivery age and sample age
  prevent cached health from appearing fresh. Activation requires the new
  capability and a fresh compatible peer heartbeat.

There is no additional daemon. Application threads increase from two
(controller and receiver) to four (adding sampler and publisher). The SDK may
also create transport worker threads.

## Validation results

| Check | Result |
|---|---|
| Feature branch full suite | 2,898 passed, 31 skipped |
| Deployment integration full suite | 2,987 passed, 35 skipped |
| Focused integration HA and forwarding checks | 515 passed |
| New heartbeat regressions | 28 passed |
| Ruff and mypy | Passed on both branches |
| Wheel inspection | Passed |
| UK bidirectional v4 heartbeat exchange | Observed after coordinated upgrade |
| UK cold static standby | Transfer ready true, forwarding ready false |
| US upgrade and routed-forwarding verification | Supported apply passed after recovery |
| US BGP | Both pfSense neighbors Established, five prefixes received each |
| UK owner routed-forwarding verification | Supported apply passed |
| UK active tunnel inside-address probes | 3/3 replies from US gateway-b |
| US workload forwarding and planned takeover/failback | Exercised live with interventions, detailed below |
| Controlled API fault injection | Not validated |

Skipped tests include disposable systemd and privileged Linux packet fixtures
unavailable on this macOS host. Unix socket and frozen-build tests passed when
run outside the sandbox with absolute source paths.

UK sequence numbers advance by ten over approximately ten seconds. At the
17:50 UTC observation, sampled health was less than one second old on both UK
nodes. During activation, gateway-a continued publishing while its cached
ownership observation aged beyond seven seconds. This is evidence of independent
publication, not a controlled cloud timeout or successful failover test.

## Deployment attempts and blockers

1. Initial inspection found US already apply-fenced, with gateway-a restarting
   and gateway-b owning the allocation. The user authorized recovery of this
   existing condition. UK was initially operational.
2. Both dry runs validated the selected wheel and required an exact disruption
   approval. US apply then refused to resume because its interrupted transaction
   recorded a different desired-state digest. No US wheel installation occurred.
3. UK apply installed the wheel on both nodes, coordinated the accepted-heartbeat
   cache reset, and observed fresh compatible heartbeats while fenced. It then
   failed during owner route reconciliation and retained restrictive locks.
4. UK reported an absent durable management entry for cloud route
   `vpcroute-e03c07qagrnzkqmcbz`, prefix `192.168.0.0/16`. Its pending delete intent
   has no accepted operation. A fresh cloud read proved the route still exists
   and the local ledger entry is missing. The journal must not be cleared as if
   deletion succeeded.
5. Automatic approval review rejected manual UK ledger adoption and controller
   stop/restart because general deployment permission did not cover that exact
   durable ownership repair. The user subsequently explicitly approved that
   exact backed-up repair.
6. The supported US `vm-ha --dry-run` still proposes transaction resumption.
   Its apply path rejects the desired-state mismatch. Digest protections have
   not been bypassed.
7. At 17:54 UTC, the approved operator repair backed up UK gateway-a state in
   `/var/lib/nebius-vpngw/vm-ha/operator-backups/heartbeat-ledger-repair-20261030T175404Z`.
   With the fenced controller stopped, two matching cloud listings and fresh
   allocation ownership checks proved one missing ledger entry. It was added
   atomically, increasing the ledger from 12 to 13 entries. The pending journal
   remained byte-for-byte unchanged. The controller restarted still fenced.
8. A fresh dry run produced disruption digest
   `cd9eb6c1af6dad120583bd0d9f1a8e928feffb1a6e562868248e62de1ebf52ec`.
   Supported apply then completed successfully, including owner routed
   forwarding verification and standby verification. The owner ledger returned
   to 12 entries and its pending journal is absent. Both UK apply locks are gone.
9. Final UK inspection found one established IPsec SA toward US gateway-b.
   Three ICMP probes from `169.254.11.5` to `169.254.11.6` succeeded, with
   approximately 98 ms round-trip time. The configured path toward US gateway-a
   returned host-unreachable, consistent with the absent SA and that node's
   existing restart failure. These probes originate on the gateway and do not
   prove client transit forwarding.

Before US recovery, gateway-a's old controller repeatedly rejected its existing
transfer lineage with `VM-HA transfer lineage is invalid or stale`. Its
interrupted lifecycle transaction was preserved throughout recovery. UK gateway-b retains an
old ledger record for the now-obsolete route, but has no pending mutation and
is standby-ready. Only the authoritative owner ledger was repaired.

The UK route failure is in unchanged route-management code. Inspection suggests
the planner recognizes management labels for an obsolete route while the writer
requires its missing durable ledger entry. This cause remains a diagnosis to
verify during recovery, not a proven regression from heartbeat threading.

Current local configuration also omits the transit switch that was present in
the previously installed resolved configuration. UK apply used the current
local file. Recovery must account for the intended configuration before declaring
the original forwarding behavior restored.

## US recovery and separation from transit

Read-only inspection established that the transaction's recorded desired digest
`3e24a03f76063a29d5435305c22fbcef192c29ca40909644981b5e4959f2948b`
matches gateway-a's staged generation
`9877f8d5b2b131bcd4dd1b0df1237a314ea333e2da51703663b7dddb705f03a5`.
Gateway-b was still using generation `f37271254e3f...`. The independent heartbeat
branch and original main loader reproduce the recorded digest from the current
US YAML. The integrated transit loader instead inserts default policy fields,
changing that digest. A loader normalization fix was tested and committed as
`78e15fc4`, then explicitly backed out at the user's request by `3205e6ee`.
Neither fix remains active. No US transit key or connection override was present
in the raw YAML, so no configuration rewrite was necessary.

Recovery used the independent heartbeat client branch and the same exact tested
wheel described above. It did not rewrite the transaction digest. The generic
cloud-operation lookup returned `UNIMPLEMENTED`. A read through the SDK's
Compute-scoped operation service proved that recorded operation
`computeoperation-u00af4bbke5q7gawfz` was already successfully complete.

After explicit approval of the exact cleanup, protected on-VM backups were
created on both nodes at 18:58 UTC. Gateway-a's already-fenced controller was
stopped. Fresh cloud reads proved gateway-b's exact allocation ownership and
gateway-a's absent attachment. The old operation was rechecked as successful
for gateway-a's exact Compute identity. Its journal was compare-and-cleared,
and older-generation transfer lineage was archived after confirming no pending
controller, route, or rearm operation. The controller restarted still fenced.
The cleanup performed no cloud mutation.

Supported apply then resumed the original transaction and completed owner
routed-forwarding and standby verification. Final inspection confirmed:

- Both nodes use generation `9877f8d5b2b...`, with the independent-heartbeat capability.
- Gateway-b is active and promotion-ready. Gateway-a is standby-ready.
- Neither node has an apply lock or pending route mutation.
- Both nodes' main, runtime, and controller module hashes match the exact wheel.
- Both pfSense BGP sessions are Established and receive five prefixes. The owner
  advertises 12 prefixes, while the standby advertises zero.
- Both nodes have IPsec SAs toward pfSense and UK gateway-a.

The deployment's ICMP checks to the pfSense inside addresses failed even though
BGP established. This is preserved as a distinct probe result and does not
establish a BGP failure. Client transit traffic and failover were not exercised.
US gateway-b's inside-address probe to UK gateway-a passed three out of three
packets. Its configured path to cold UK gateway-b returned host-unreachable.

## UK normal-operation standby readiness investigation

The user requested a read-only subagent investigation. It found a recurring DHCP
route hygiene failure rather than lost XFRM interfaces. UK gateway-b's lease
was acquired at 17:57:55 UTC, with a one-hour renewal time. Transfer readiness
became false at 18:57:56. At 19:01:24, the five-minute route-cleanup timer removed
`169.254.0.0/16 via 169.254.169.1 dev eth0 proto dhcp`. Readiness recovered at
19:01:26, after approximately 210 seconds. Historical cleanup logs show the same
DHCP route recurring approximately hourly. The renewal is strongly supported
as the trigger, although networkd's normal journal does not log that renewal.

The cold static standby intentionally has no SAs. Its missing static/XFRM
forwarding readiness is normally covered by the cold-standby transfer exception.
That exception also requires clean routing hygiene. The DHCP route disables
the exception, exposing all three diagnostic reasons and blocking takeover
until cleanup. Interfaces remained present and UP. The configured tunnel `/30`
routes are more specific than the broad `/16`. Post-cleanup route lookups selected
the expected XFRM interfaces. There is no evidence this event disrupted peer
routing or UK owner forwarding.

Recommended follow-up is prompt bounded passive hygiene repair through the
existing routing lock and passive authority, followed by resampling. Keep the
sampler read-only and preserve the transfer gate if cleanup fails. Separately
investigate preventing only the unwanted DHCP route without disabling default
or metadata routes. No UK changes were made during this investigation.

## Preserved evidence and next step

Sanitized evidence is committed under `evidence/heartbeat-independent-live/`:

- [Four-node status, module hashes, threads, and recent heartbeat events](evidence/heartbeat-independent-live/four-node-status-20261030T175013Z.json)
- [UK activation heartbeat and route failure events](evidence/heartbeat-independent-live/uk-activation-events.json)
- [US supported recovery plan](evidence/heartbeat-independent-live/us-recovery-plan.json)
- [Artifact metadata](evidence/heartbeat-independent-live/artifact.json)
- [Approved UK ledger repair receipt](evidence/heartbeat-independent-live/uk-ledger-repair.json)
- [Recovered UK state and tunnel status](evidence/heartbeat-independent-live/uk-recovered-state.json)
- [UK tunnel inside-address probes](evidence/heartbeat-independent-live/uk-tunnel-probes.json)
- [US pre-existing stale transfer-lineage errors](evidence/heartbeat-independent-live/us-stale-transfer-lineage.json)
- [US approved stale-state recovery receipt](evidence/heartbeat-independent-live/us-recovery-receipt.json)
- [Recovered US state, tunnels, routes, and BGP](evidence/heartbeat-independent-live/us-recovered-state.json)
- [US installed artifact verification](evidence/heartbeat-independent-live/us-artifact-verification.json)
- [US inside-tunnel probes](evidence/heartbeat-independent-live/us-tunnel-probes.json)
- [UK DHCP and cleanup evidence](evidence/heartbeat-independent-live/uk-standby-dhcp.json)
- [UK recurring cleanup evidence](evidence/heartbeat-independent-live/uk-standby-recurrence.json)
- [UK route lookups](evidence/heartbeat-independent-live/uk-standby-routing.json)
- [Evidence checksums](evidence/heartbeat-independent-live/SHA256SUMS)

The user chose to retain the existing link-local cleanup and cold-static
standby behavior. No draft changes to those policies were deployed. The later
US campaign below covers planned transfers and client ICMP forwarding.
Controlled API faults and shared remote-peer outage injection remain untested.

## US live planned takeover and failback, October 30

The campaign ran from 19:54 to approximately 20:28 UTC. UK was first accessed
only after all US transfer commands finished. No UK configuration or controller
changes were made. The installed wheel remained unchanged throughout the tests.

Baseline: gateway-b owned US, gateway-a was standby-ready, both members ran the
exact wheel above, and neither had an apply lock or pending cloud operation.
The test sequence was B to A normalization, A to B takeover, then B to A failback.
Each transfer used the supported CLI from the independent heartbeat branch.

A one-second ICMP probe ran from the operator's home client to the existing US
workload `10.81.0.20`. A bounded capture on US gateway-b confirmed the request
and reply traversed `xfrm0`, from client `192.168.0.11`. This tests home/pfSense
to US workload forwarding without depending on UK. The gaps below measure time
between successful replies, rather than exact outage duration.

| Test | Result | Traffic interruption | Completion |
|---|---|---|---|
| Initial B to A | CLI failed after 41.7 seconds, controller subsequently completed cutover and restarted B | 110 consecutive probes lost, 111.4-second reply gap | Controller cutover receipt: 112.4 seconds |
| A to B preflight | Refused to submit a transfer after 301.5 seconds because standby evidence was invalid | No transfer-induced interruption | Failed safely before ownership changes |
| A to B retry | Cutover succeeded, standby verified after local route repair | 54 consecutive probes lost, 55.2-second reply gap | Cutover 71.4 seconds, command completion 194.6 seconds |
| Final B to A | Cutover succeeded, standby verified after local route repair | 57 consecutive probes lost, 58.2-second reply gap | Cutover 71.2 seconds, command completion 157.6 seconds |

### Required interventions and reproduced failures

1. Before the first actual transfer, two requests were rejected by a blocked
   restoration authorization from obsolete generation `f37271254e3f...`.
   Gateway-a's currently healthy status did not reveal that request blocker.
   A protected on-VM backup was made. With only the passive controller stopped,
   fresh cloud reads proved gateway-b's exact attachment and gateway-a's absent
   attachment. Only authorization `a4366ed0e6c1...` was archived. No allocation or
   route mutation occurred. The initial cleanup attempt stopped at a readiness
   assertion because the DHCP link-local route had recurred. The existing timer
   cleared it naturally before cleanup proceeded.
2. During the initial transfer, A lost static/XFRM readiness after B was stopped.
   A remained fenced until readiness recovered. The CLI reported generic failure,
   but the controller later resumed the same transfer, acquired the allocation,
   reconciled routes, and enabled forwarding. US observations showed A's UK SA
   later established toward UK gateway-b. UK was not inspected during this event,
   so its contemporaneous behavior is not independently established by this test.
3. Each standby reboot installed `10.80.1.0/24 dev xfrm1` while the usable UK SA
   was on `xfrm2`. The controller considered this repairable and published
   `standby_ready=true`, but `standby_tunnel_state=not-standby`. The operator
   status validator returned `standby-ready-evidence-invalid` because it accepts
   only fully warm or cold readiness. Consequently, CLI preflight/restoration
   could not validate the standby without intervention.
4. Three backed-up local repairs invoked the installed static-route selector
   under its existing routing lock, after proving passive mode, forwarding off,
   exact peer ownership, and no pending operation. The selector moved the
   configured UK route from `xfrm1` to `xfrm2`. Repairs were on B before takeover,
   A after takeover, and B after final failback. They changed no cloud routes or
   allocation ownership and did not enable standby forwarding. No application
   code, configuration, or wheel was changed during this campaign.

These results validate the transfer chain with intervention. They do not
validate unattended standby restoration. The stale route and inconsistent
readiness evidence need a separate fix. The early generic CLI failure also
needs investigation because the transfer continued afterward.

### Safety ordering and final state

US effect journals show Stop completed before detach, detach completed before
attachment, and cloud route reconciliation completed before active forwarding.
The final fresh cloud read proved gateway-a's exact allocation attachment and
gateway-b's absent attachment. The installed read-only route-context check
revalidated the current cloud plan against the durable reconciliation receipt.
No successful activation of both nodes at once was observed. Sampling cannot
exclude every subsecond overlap by itself.

During blocking Stop operations, the publisher continued attempting heartbeats
and local health samples remained less than one second old while ownership
observations aged beyond 20 seconds. Delivery failed while the peer VM was
stopped, as expected. This demonstrates thread independence during real blocking
operations, but is not a controlled API-timeout fault test.

Final US operator status confirmed:

- A active, promotion-ready, forwarding `1`, and the exact shared allocation owner.
- B normal, standby-ready, forwarding `0`, with no standby readiness reasons.
- Both pfSense BGP sessions Established, five prefixes received each. A advertises
  12 prefixes and B advertises zero.
- No apply locks, manual transfer requests, accepted cloud operation, or pending
  route mutation. Installed module hashes still match the tested wheel.
- Home-to-US workload replies resumed after each cutover. The final US-to-UK
  active tunnel probe returned three of three replies at approximately 98 ms.

The deferred UK check found gateway-b active and forwarding, with no pending
operation. Gateway-a was unreachable over SSH and reported Stopped by the
owner's cloud observation. Automatic standby restart was inhibited with
`standby-auto-healing-peer-policy-unavailable`. UK redundancy therefore remains
unrestored. No UK recovery was attempted in this US-only campaign.

Long-lived TCP preservation, controlled API delay/failure, heartbeat isolation,
process crash, and shared remote-peer failure injection were not exercised.
Workload SSH rejected the available keys, so traffic validation used ICMP.

### US campaign evidence

Evidence is preserved in
[evidence/heartbeat-us-transfers-20261030](evidence/heartbeat-us-transfers-20261030/):

- [Command results and timings](evidence/heartbeat-us-transfers-20261030/06-b-to-a.log)
- [Initial blocked restoration record](evidence/heartbeat-us-transfers-20261030/restoration-blocker.json)
- [Backed-up obsolete-record archival](evidence/heartbeat-us-transfers-20261030/obsolete-restoration-cleanup.json)
- [Invalid operator standby evidence](evidence/heartbeat-us-transfers-20261030/b-operator-status.json)
- [Example bounded standby route repair](evidence/heartbeat-us-transfers-20261030/a-standby-route-repair.json)
- [Traffic interruption measurements](evidence/heartbeat-us-transfers-20261030/traffic-summary.json)
- [Effect/heartbeat events and fresh cloud proof](evidence/heartbeat-us-transfers-20261030/us-events-and-cloud-proof.json)
- [Final US state](evidence/heartbeat-us-transfers-20261030/final-us.json)
- [Deferred UK state](evidence/heartbeat-us-transfers-20261030/final-uk.json)
- [Evidence checksums](evidence/heartbeat-us-transfers-20261030/SHA256SUMS)
