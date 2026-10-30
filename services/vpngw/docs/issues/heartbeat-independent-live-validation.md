# Independent heartbeat implementation and live validation

Status at October 30, 2026, 14:00 America/Toronto (18:00 UTC): implementation
passes local checks. UK deployment and recovery succeeded. UK gateway-a is
active and promotion-ready, gateway-b is standby-ready, and neither has an
apply lock. US remains fenced on its previous artifact, so the full rollout
and end-to-end failover validation remain incomplete.

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
| US upgrade | Blocked before package installation |
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

US gateway-a's old controller repeatedly rejects its existing transfer lineage
with `VM-HA transfer lineage is invalid or stale`. Neither that record nor its
interrupted lifecycle transaction has been modified. UK gateway-b retains an
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
- [Evidence checksums](evidence/heartbeat-independent-live/SHA256SUMS)

Next, resolve the US interrupted transaction and stale transfer lineage without
bypassing identity or digest checks. Then deploy the exact wheel there and
validate controlled API delay, client routing, and failover/failback. UK is
recovered, but these remaining checks are required for the full rollout.
