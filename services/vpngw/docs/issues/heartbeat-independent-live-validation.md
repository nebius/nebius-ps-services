# Independent heartbeat implementation and live validation

Status at October 30, 2026, 15:03 America/Toronto (19:03 UTC): US and UK have
the new wheel. US recovery and apply succeeded. US gateway-b is active and
promotion-ready, gateway-a is standby-ready, and both apply locks are absent.
UK owner forwarding remains healthy. The UK standby recovered automatically
after its route cleanup timer removed a recurring DHCP route. End-to-end
client forwarding and fault-injected failover validation remain incomplete.

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
required assets, and package integrity. Both UK installations match its main,
runtime, and controller module hashes. US retains the previous artifact.

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
| End-to-end client forwarding, takeover, failback, controlled API fault | Not validated |

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

Next, address recurring UK standby readiness gaps and establish a stable
baseline. Then validate controlled API delay, client routing, and failover/failback,
including shared remote-peer failure: tunnel health currently contributes to
owner readiness without distinguishing a local failure from a remote outage.
Both deployments have converged, but these remaining checks are required for
end-to-end validation.
