# Cloud API delays can trigger false VM-HA peer failure detection

Status: reproduced in an isolated simulation. No production fix is included.

Branch: `codex/fix-vpngw-heartbeat-api-coupling`, based directly on local `main`
commit `5b73a95bec179242e4850e01d12f7d970397bf51`. This issue is independent of
transit forwarding and static route reselection.

## Problem

Heartbeat construction synchronously reads cloud ownership and route readiness.
A slow API request can delay publication beyond the peer freshness threshold.
Separately, `VMHASnapshotAdapter.observe()` samples peer state before reading
cloud state, then obtains the evaluation clock afterward. Heartbeats received
by the listener during that cloud read are excluded from the decision snapshot.
A responsive peer can therefore be classified as stale and selected for fencing.

The same observation ordering exists on the original main branch. The preserved
reproduction invokes the production snapshot adapter and policy, with simulated
provider responses. It never invokes a cloud or controller effect.

## Observed UK incident

Times below are October 29, 2026 in America/Toronto (EDT, UTC-04:00). The corresponding
UTC date is October 30. Logs were collected during the October 30 investigation.

| Local time | Observation |
|---|---|
| 23:49:10 | Both UK controllers report cloud API failures. Gateway-a reports AllocationService `UNAVAILABLE`. Gateway-b reports InstanceService `DEADLINE_EXCEEDED`. |
| 23:49:20 | Gateway-b reports RouteTableService `DEADLINE_EXCEEDED`. |
| 23:49:21 | Gateway-a reports AllocationService `DEADLINE_EXCEEDED`. |
| 23:49:22 | Gateway-a starts `stop-former-owner` targeting gateway-b. |
| 23:49:25 | Gateway-b disables active forwarding and deletes its IKE sessions. The US gateway receives the tunnel DELETE. |

Gateway-a's terminal promotion receipt identifies `automatic-failover`. No
preceding controller restart or OOM appeared in the inspected pre-takeover
journal window. The ordinary-admission drop-in predates this incident.

The API stalls are the strongest evidenced trigger. Historical logs do not
retain each decision's peer age and health flags, so they cannot establish
whether delayed publication, stale snapshot sampling, or both caused this
specific takeover. The offline reproduction proves the sampling defect, not
an exact replay of the live packet history. The initiating reason for the
provider API failures has not been established.

## Timing and retry policy

The deployed Nebius SDK was `0.3.101`. Native request settings were:

| Setting | Value |
|---|---|
| Overall API request timeout | 30 seconds |
| Per-attempt timeout | 10 seconds |
| SDK `retries=3` | Three total attempts, including the first |
| Peer freshness threshold | 10 seconds |
| Suspicion interval | 5 seconds |

The inspected SDK retry loop immediately retries eligible failures. Multiple
cloud reads occur serially during an ownership observation. The 30-second
request budget is not an overall controller-cycle budget. Failover policy does
not require three failed API requests before reacting to an unhealthy/stale peer.

## Evidence and reproduction

Evidence is preserved in [the evidence directory](evidence/heartbeat-api-coupling/):

- `uk-a-api-and-fencing.jsonl` and `uk-b-api-and-fencing.jsonl` are unchanged,
  selected journal extracts with deployed module hashes and provider request IDs.
- `stale-peer-result.jsonl` is the original investigation output.
- `reproduce_stale_peer.py` is a formatted baseline reproducer and control.
- `main-reproduction-result.jsonl` records the reproduction on this branch.
- `SHA256SUMS` records integrity hashes for these evidence files.

Run from `services/vpngw` with the project's development dependencies installed:

```bash
PYTHONPATH=src:. uv run python docs/issues/evidence/heartbeat-api-coupling/reproduce_stale_peer.py
```

The baseline advances a fake clock by 12 seconds during each cloud read while
simulating a listener receiving a healthy heartbeat. The snapshot uses a
13-second-old sample while a one-second-old heartbeat is available. Two policy
evaluations produce `suspect`, then `stop-former-owner`. The control refreshes
peer state after the read and remains `normal` in both evaluations.

The script expects the current defect. It is diagnostic evidence, not the final
regression-test acceptance condition.

## Fix acceptance criteria

- Sample the latest peer state and evaluation time after blocking observations.
- Prevent cloud API latency from silently starving advisory heartbeat publication.
  Keep local service/path health distinct from uncertain cloud ownership evidence.
- Preserve authoritative cloud checks, former-owner Compute fencing, allocation
  ownership checks, and fail-closed promotion. Do not weaken takeover safety.
- Test slow/retrying APIs with a healthy peer and actual peer failures separately.
- Record decision peer age, health flags, suspicion timing, and observation latency.

The downstream US repair/fencing behavior and its stale accepted-operation
journal are separate issues. This branch only preserves this incident evidence.
