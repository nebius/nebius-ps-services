# US unplanned static-route fault validation

The October 30 campaign completed. Process and heartbeat faults recovered through
fenced takeover, but the requested API availability behavior failed. Independent
publication keeps heartbeats flowing during cloud timeouts. The controller still
fences forwarding on cloud observation errors, which can trigger takeover.

Final state: US A active, B standby-ready. UK B active, A standby-ready. Both pairs
pass strict current-boot identity and redundancy checks. The final workload probe
returned 10/10 replies. Test firewall chains and active fault timers are absent,
and the test ping processes have been stopped.

US A's active UK route is on `xfrm2`. US B is ready in the explicit repairable
standby state with forwarding disabled. F10 exercised fenced static-route
preparation before activation, which is the intended handling of its stale
standby route.

## Scope and artifact

This exercised **static routing between US and UK**, using workload traffic from
US `10.81.0.20` to UK `10.80.1.0`. It did not exercise BGP between those gateways.
The existing US-to-pfSense BGP sessions remained part of the gateway's readiness
requirements, including standby restoration.

At the user's direction, TCP survival and home-to-US traffic tests were omitted.
F8's pfSense BGP fault was deferred. UK received no injected faults, deployment,
manual takeover, or manual repair. UK inspection occurred after the US cases.

US used the existing `0.6.3.dev31` wheel, SHA256
`39e1d338ceb1b5bf7eb873c17fd1401b35e14bda0183bb799c156a32fe1e1628`.
Final installed module hashes match. UK retained its previous dev30 installation.
No product code, configuration, ledger, or cloud route was manually changed.

## Results

Reply gaps are the interval between the last successful ping reply and the next
successful reply. They include the normal probe interval and are approximate
outage measurements. An independent logger on the US workload preserved source
timestamps even when management SSH was unavailable.

| Case | Injected fault | US ownership | Reply gap | Recovery |
|---|---|---|---:|---|
| F0 | Five-minute baseline | A | No loss observed | Stable |
| F1 | Standby HA cloud API traffic dropped for 60 seconds | A | No loss observed | Traffic passed, guard recovery defect found later |
| F2 | Owner HA cloud API traffic dropped, bounded at 60 seconds | A → B | 340.0 s | Operator-assisted |
| F3 | Both HA services' cloud API traffic dropped for 60 seconds | B → A | 98.3 s | Automatic |
| F4 | Owner HA process paused for approximately 3.07 seconds | A | No loss observed | Automatic, no takeover |
| F5 | Owner HA process killed once, normal systemd policy retained | A → B | 69.6 s | Automatic |
| F6 | Owner HA process frozen with a 45-second resume watchdog | B → A | 52.2 s | Automatic |
| F7a | Owner-to-standby heartbeat traffic blocked | A → B | 56.3 s | Automatic |
| F7b | Heartbeat traffic blocked in both directions | B → A | 58.3 s | Automatic |
| F9 | Current UK endpoint unreachable from both US nodes for 180 seconds | A → B | 288.7 s | Automatic |
| F10 | Active Compute VM stopped without a planned handover | B → A | 61.5 s | Automatic |

Network cleanup was armed before each injection. API rules matched only the HA
service's HTTPS sockets. Heartbeat rules matched the exact peer and TCP/9443.
F9 dropped egress to the exact UK endpoint, including encapsulated packets, with
packet counters confirming the fault. A native VM shutdown could end a fault
before its watchdog expired. Timer journals preserve the actual execution times.

## API failures and the assisted recovery

F1 demonstrated publication independence: heartbeat sequences advanced while the
sender's ownership observation aged beyond 60 seconds and local health remained
fresh. F2 and F3 also continued publishing, including the changed blocked state.
This does not meet the no-failover requirement because the controller exception
path still installs the forwarding-off guard.

F1 left B's `guard.json` blocked while its durable data-plane mode remained
passive. Raw status subsequently reported standby-ready. The initial harness
trusted that raw status, so it missed the inconsistent guard before F2.

During F2, B stopped A and detached its allocation, then stalled. A broad
link-local route failed routing hygiene, while the periodic maintenance service
refused to run under the blocked guard. B still had a UK backup SA. This latent
standby condition contributed to the 340-second gap, which must not be attributed
solely to the owner's API timeout.

Recovery backed up B's state, freshly proved A Stopped and both allocation
attachments absent, and restarted only B's fenced HA service. Native transitions
then restored passive authority and completed takeover. No guard, ledger, journal,
allocation, or route was manually edited. Subsequent case gates checked guard and
boot consistency as well as raw status. F3 independently reproduced API-related
fencing and takeover from a consistent starting state, without assistance.

## Fencing and harness limitations

The [activation audit](evidence/heartbeat-us-unplanned-static-20261030/fencing-audit.json)
checks all eight US and two UK activations against former-owner Stopped proof,
completed attachment, and route reconciliation. Explicit candidate-preparation
and confirmation actions are recorded where executed. They are conditional, so
their absence alone is not an ordering failure.

F10's external Compute stop overlapped a controller stop request. The controller
logged a transient failure, then observed the former VM Stopped and continued.
The audit preserves that live observation rather than inventing a completed
controller stop receipt.

F7a's observation command timed out during takeover. Monitoring resumed without
reinjecting the fault or resubmitting a transfer. End-of-test authenticated SDK
probes briefly published identity-preflight metadata. Strict status was rerun
after those probes completed and verified both pairs. That temporary projection
was a diagnostic side effect, not a forwarding failure.

## UK reactions

| Activation time, UTC | UK transfer | US case |
|---|---|---|
| 22:15:33.991 | B → A | Prolonged F2 outage |
| 22:43:43.653 | A → B | F9 endpoint loss |

Before both transfers, UK kept sending fresh heartbeats with healthy services and
recent cloud observations. Local forwarding readiness became false, local repair
ran, and forwarding became blocked before takeover. This supports a data-plane
health trigger rather than heartbeat delivery stalled by UK cloud calls. The
precise failed readiness subcheck was not captured historically.

Several intervening US transfers caused UK local route-repair actions without a
full UK failover. F9 also exposed the detection delay: installed SAs initially
remained locally ready while real packets were being dropped. Installed strongSwan
documentation confirms that `dpd_timeout`, rendered as 15 seconds on US, does not
govern IKEv2. Its retransmission mechanism governs detection. Packet recovery then
required UK and US convergence after the three-minute injection ended.

## Follow-up verification

API timeout handling and guard/status consistency remain blocking defects. Repeat
the affected cases after correcting them. Keep the existing cold-static and
link-local policy decisions separate from those fixes.

Run a separate US–UK BGP campaign with both sides configured for BGP. Verify
neighbor establishment, advertisements, withdrawals, learned next-hop selection,
cloud route ownership, and standby recovery through this same fault matrix.
These static results do not establish BGP failover correctness. TCP survival
remains deferred.

The [evidence directory](evidence/heartbeat-us-unplanned-static-20261030/) contains
receipts, source packet logs, compact heartbeat/state samples, effect journals,
cloud proofs, final status, and checksums. Raw collection remains at
`/private/tmp/vpngw-us-faults-20261030`.

- Process and heartbeat failures recovered through fenced takeover.
- Cloud API outages still caused forwarding loss and unnecessary takeover.
- Both pairs finished healthy, with static routing verified and BGP follow-up pending.
