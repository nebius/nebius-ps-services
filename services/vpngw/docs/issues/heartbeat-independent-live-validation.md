# Independent heartbeat implementation and live validation

Status at October 30, 2026, 13:50 America/Toronto (17:50 UTC): implementation
passes local checks. Live deployment is incomplete. All four gateways are
currently fenced by apply locks. Successful routed forwarding and failover
have not been demonstrated with the new artifact.

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
| Live forwarding, takeover, failback, controlled API fault | Not validated |

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
   deletion succeeded. No ledger repair has been performed.
5. Automatic approval review rejected manual UK ledger adoption and controller
   stop/restart because general deployment permission did not cover that exact
   durable ownership repair. Specific approval was requested and is pending.
6. The supported US `vm-ha --dry-run` still proposes transaction resumption.
   Its apply path rejects the desired-state mismatch. Digest protections have
   not been bypassed.

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
- [Evidence checksums](evidence/heartbeat-independent-live/SHA256SUMS)

After approved recovery, restore owner forwarding and standby readiness first.
Then validate the exact wheel under controlled API delay, normal routing, and
failover/failback. Until those checks pass, this report does not establish a
successful live rollout.
