# US heartbeat fault validation plan

Status: US live campaign completed October 30. See the
[execution report](heartbeat-us-unplanned-static-validation.md) for results,
failures, operator assistance, cleanup and evidence.

Execution scope was narrowed by the user: omit TCP survival and all home-to-US
traffic probes. Measure continuous workload pings from US `10.81.0.20` to UK
`10.80.1.0`. Defer F8's pfSense BGP fault. The US-to-UK connection uses static
routes. This campaign does not validate BGP failover between US and UK. A separate
campaign must convert and verify that path with BGP, then exercise the failure
matrix including neighbor establishment, withdrawal, advertisement and route
selection on both gateways.

This extends the [live validation report](heartbeat-independent-live-validation.md).
Use the deployed US dev31 artifact and record fresh installed hashes before testing.
UK receives no injected faults. Its automatic reactions to US tunnel changes may
affect recovery and must be inspected at the end, or earlier if they block US recovery.

## Traffic and safety evidence for every case

- Keep continuous US-to-UK workload pings for this campaign.
- Deferred TCP verification for a later campaign: run a persistent TCP stream
  over each available workload path. Establish it
  before injection, exchange numbered application messages in both directions,
  and disable automatic reconnect. Record socket identity, acknowledged sequence
  numbers, last successful message, first resumed message, and errors.
- Use a separate reconnecting TCP probe to measure when new connections work.
  A successful reconnect does not count as survival of the original connection.
- Record application timeout and TCP keepalive/user-timeout settings. A surviving
  socket with unacknowledged data does not count as recovered application traffic.
- Sample heartbeat sequence, receive age, sample age, health fields, data-plane
  mode, advisory ownership age, controller state and reason, boot IDs, and journals.
- Prove cloud allocation ownership, former-owner Compute state, route ownership,
  BGP/static path readiness, forwarding flags, and standby restoration afterward.
- Independently expiring cleanup must be installed before network faults. Scope
  rules to the gateway process/API endpoints or exact heartbeat peer and port.
  Never block all HTTPS, SSH, workload traffic, metadata, or IKE/ESP incidentally.
- Resolve and verify actual API destinations and transport before injection,
  including existing connections and any shared heartbeat/API destinations.
  If they cannot be isolated, use a scoped test harness rather than a broad rule.
- One case at a time. Restore and verify a healthy owner and ready standby before
  proceeding. Stop the campaign on an unbounded fault, ownership inconsistency,
  failed cleanup, or recovery requiring durable-state manipulation.
- Do not resubmit transfers after an early CLI error. Observe the exact ongoing
  operation. No manual allocation, route ledger, or pending-journal edits.

## Ordered fault matrix

| ID | Injection | Expected result and acceptance |
|---|---|---|
| F0 | No fault, five-minute traffic baseline | Stable heartbeats, ownership and traffic. Prove the TCP harness works before interpreting survival results. |
| F1 | Drop cloud API traffic on standby for 60 seconds | Owner remains active and traffic continues. Standby cannot obtain fresh cloud authority and must not promote. Publisher and local sampler continue. API recovery restores normal observations without manual state edits. |
| F2 | Drop cloud API traffic on owner for 60 seconds | Desired outcome: no takeover solely because API calls are unavailable while local forwarding and heartbeat delivery remain healthy. Verify heartbeat continuity separately from controller fencing. Report any fence, unhealthy mode advertisement, or takeover as a failure of that desired availability behavior. |
| F3 | Drop cloud API traffic on both nodes for 60 seconds | Heartbeats and local sampling continue. No node may claim new ownership without authoritative cloud proof. Desired steady-owner availability is assessed separately from the existing fail-closed ownership policy. Restore APIs and verify automatic recovery. |
| F4 | Briefly pause the owner's HA process for 3 seconds, then resume | No takeover for a disturbance below the heartbeat freshness threshold. Traffic behavior and restart-free recovery are recorded. Use an independent resume watchdog. |
| F5 | Crash the owner's HA process once, preserving normal systemd restart policy | Fast restart may recover before takeover. Otherwise the peer may take over after freshness and suspicion checks. Both outcomes are acceptable only with one forwarding owner and automatic recovery. Record actual restart timing. |
| F6 | Freeze the owner's HA process beyond heartbeat expiry and suspicion | Peer detects stale delivery, enters suspicion, then executes authoritative stop-before-activation takeover. A paused process must not be mistaken for a stopped VM. No forwarding on candidate until former Compute owner is Stopped and exact allocation is confirmed. |
| F7 | Drop only owner-to-standby heartbeat traffic for 45 seconds, then separately test bidirectional heartbeat isolation | Local services may remain healthy, but the standby loses owner liveness evidence and may initiate takeover after suspicion. Cloud fencing must prevent simultaneous forwarding. Preserve cloud API and data-plane connectivity. |
| F8 | Block BGP TCP/179 on the owner's pfSense session beyond local repair budget | Fresh heartbeats must report local health degradation. Verify repair attempt followed by existing health-driven failover if degradation persists. Clear the rule so the new owner and restored standby can converge. |
| F9 | Make one remote IPsec endpoint unavailable to both US nodes, with bounded cleanup | If an alternate usable path covers required prefixes, repair routes and keep forwarding without needless takeover. If every required path is unavailable, existing policy may fence/fail over, but failover cannot repair the remote outage. Record cascades or repeated transfers rather than call them successful recovery. |
| F10 | Stop the active US Compute instance unexpectedly | Peer detects missing heartbeat and takes ownership through the normal unplanned path. Prove former owner Stopped, exact candidate attachment, managed routes, traffic recovery, and automatic former-member restoration. |

F1 through F3 distinguish timeout behavior using packet DROP from immediate
connection failure. Add a short REJECT variant if the timeout cases pass and
error handling remains uncertain. A 60-second interval exceeds the ten-second
health freshness and five-second suspicion windows without requiring a prolonged
outage. Elapsed takeover time also includes controller and cloud operations.

For F6, arrange a timed process resume only if the same process and boot still
exist. Let the native controller stop and restore the VM normally. Do not run a
second operator recovery concurrently with its durable transfer.

## Important API expectation boundary

Independent publication removes cloud calls from the heartbeat delivery path.
It does not remove all cloud-authority enforcement from the controller. The
controller still calls `_block_or_disable` when `snapshot.cloud.authoritative`
is false, and healthy-peer evaluation includes an active data-plane mode. A
cloud failure could therefore keep heartbeats flowing while triggering fencing
and subsequent peer suspicion. Whether the actual provider error takes this
path must be established from live evidence. Passing heartbeat continuity alone
does not satisfy the requested no-failover API availability behavior.

## TCP results and delivery report

For each case report: injected scope and duration, expected and actual state
transitions, heartbeat gaps, health-sample ages, ownership changes, ping outage,
existing TCP survival, new TCP availability, cleanup, and standby restoration.
Use pass, fail, or not exercised for each independent assertion. Existing TCP
survival is measured without conntrack synchronization first. Do not claim it
from ping or successful SSH reconnection.

After the final case, inspect UK, verify the US-to-UK workload path, confirm both
pairs' owner and standby health, and publish sanitized receipts and checksums.
