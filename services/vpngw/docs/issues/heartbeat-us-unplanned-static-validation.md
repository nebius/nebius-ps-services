# VM-HA validation summary

## November 2 restarted observation-fault campaign

Seven isolated no-disruption cases passed from 13:34:57 to 14:02:08 EST on
November 2, 2026. This restart used the already-deployed `0.6.3.dev34` wheel
from integration commit `5bb3ef29`. No code or configuration changes were
deployed during the restart. All four nodes matched the package version and
five critical module hashes. Wheel SHA256:
`294e76693e81d8ec432497679118691cb883c44190ea48cd75b8d36d8ed931a1`.

Every injection expired independently. Each case included at least three
minutes of recovery monitoring before the next injection. API faults dropped
controller-cgroup TCP 443 traffic. Local faults terminated only controller-owned
read-only `vtysh show` queries. Counters, failed-query logs and unknown-health
heartbeats proved the faults occurred.

| Case | Successful US–UK probes | Lost probes |
|---|---:|---:|
| US owner local query, 4 seconds | 937 | 0 |
| US standby local query, 4 seconds | 939 | 0 |
| All four cloud APIs, 180 seconds | 1,867 | 0 |
| US owner API 60 seconds plus local query 4 seconds | 1,293 | 0 |
| US owner repeated local query, 6 seconds | 943 | 0 |
| UK owner local query, 4 seconds | 946 | 0 |
| UK cold standby local query, 4 seconds | 949 | 0 |

There were 7,874 successful probes and zero losses within the case windows.
Ownership, guards, daemon identities, IKE SAs and Home pfSense BGP sessions
remained stable. No controller repair or takeover occurred. Heartbeats continued
while cloud-dependent controller status became stale. Both regions finished
with gateway-a active and gateway-b passive and ready. No active injector or
fault chain remained, and the workload probe was stopped.

These passing repeats do not explain the earlier dev34 campaign's delayed US
fencing and UK cascade. That failure remains unresolved. Restart preflight also
found UK selecting the passive US gateway as its static return path despite
healthy controller status. Supported US failback restored traffic, although
the CLI reported failure before controller recovery completed. Transient query
recovery also requested redundant ordinary-agent reloads. Those reported no
configuration changes and did not restart strongSwan or FRR.

US–UK routing was static. Home BGP was observed only. These cases do not qualify
US–UK BGP failover, TCP survival, prolonged local-observation loss or all
unplanned takeover modes. Private receipts, traffic, effect logs and the full
report are in `/private/tmp/vpngw-observation-tests-20261102/restart-r3/`.

## Historical October 30 campaign

These are historical October 30, 2026 observations, not current gateway status.
The US-to-UK fault campaign used static routing. It did not validate US-to-UK
BGP failover or TCP connection survival. No faults were injected into UK.

## Controlled transfers

After integrating static route selection and repairable standby admission, four
US planned transfers completed in the controllers. Three CLI requests passed.
One CLI exited after 36.8 seconds while its exact controller transfer completed
after 92.6 seconds and restored standby automatically. That CLI issue remains.
The final failback repaired the static route while passive and enabled forwarding
after cloud route reconciliation. A separate deployment/recovery incident before
these transfers caused a 907.1-second probe reply gap.

## Unplanned faults

Reply gaps measure intervals between successful workload ping replies and are
approximate outage measurements, not TCP survival evidence.

| Fault | US ownership | Reply gap | Outcome |
|---|---|---:|---|
| Five-minute baseline | A | No loss | Stable |
| Standby API unavailable for 60 seconds | A | No loss | Guard inconsistency found later |
| Owner API unavailable, bounded at 60 seconds | A to B | 340.0 s | Required backed-up HA service restart on B |
| Both nodes' APIs unavailable for 60 seconds | B to A | 98.3 s | Automatic recovery |
| Owner process paused for about 3 seconds | A | No loss | No takeover |
| Owner process killed once | A to B | 69.6 s | Automatic recovery |
| Owner process frozen | B to A | 52.2 s | Automatic recovery |
| One-way heartbeat loss | A to B | 56.3 s | Automatic recovery |
| Bidirectional heartbeat loss | B to A | 58.3 s | Automatic recovery |
| Current UK endpoint unreachable for 180 seconds | A to B | 288.7 s | Automatic recovery with UK convergence |
| Active VM stopped without planned handover | B to A | 61.5 s | Automatic recovery |

Independent publication survived API stalls, but the then-deployed controller
still fenced on cloud errors. The 340-second case also involved a pre-existing
standby guard/data-plane mismatch and blocked route maintenance. It cannot be
attributed solely to API delay. No manual allocation or route changes were used.

The activation audit checked former-owner Stopped proof, attachment completion
and route reconciliation before enablement for eight US and two UK activations.
UK failed over twice during US disruption. Its fresh heartbeats showed local
forwarding readiness loss, but the exact failed readiness subcheck was not saved.
Both pairs finished healthy, the final probe returned 10/10 replies, and injected
rules, active fault timers and probe processes were removed.

## Remaining verification

- Repeat owner, standby and simultaneous API outages after deploying `1d8bd5cc`.
- Resolve guard/status consistency and premature planned-transfer CLI failures.
- Live-qualify local observation grace, partial forwarding and scoped repair.
  These are implemented on this branch but were absent from the historical campaign.
- Run a separate US-to-UK BGP campaign covering advertisements, withdrawals,
  route selection, takeover/failback and standby recovery. TCP survival is deferred.
- Preserve the approved cold-static and link-local policies while fixing these bugs.
- An earlier recovery exposed a UK SSH host private key in command output.
  Host-key rotation remains unverified.

On November 2, the cloud-read retry correction passed 2,953 local unit/integration
tests and wheel checks. Another 31 required disposable Linux/systemd fixtures.
The frozen-binary test was excluded because PyInstaller was unavailable. Lint,
formatting and source type checks passed. These checks are not live validation.

For future fault runs, verify a healthy owner and standby first, inject one scoped
fault at a time with independently expiring cleanup, and record traffic, fresh
health/ownership evidence and exact operation identity. Observe an existing
transfer after a CLI error instead of submitting it again.

Detailed reports, test plans, deployment receipts, checksums and raw evidence
were removed from the working tree. All remain recoverable under
`services/vpngw/docs/issues/` at Git commit `1d8bd5cc`. For example, from the
repository root:

```bash
git show 1d8bd5cc:services/vpngw/docs/issues/heartbeat-unplanned-fault-test-plan.md
```
