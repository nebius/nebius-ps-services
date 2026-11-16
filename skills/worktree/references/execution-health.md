# Execution Health And Timing

Implement and SDLC share one progress-aware execution policy. Healthy campaigns,
workers, builds and tests have no duration-only expiry. The configured model and
reasoning effort remain unchanged. Long campaigns use their existing durable
continuation and ownership records; elapsed time never authorizes a replacement
worker or resets a failure-repair budget.

## Worker Observations

`workflow/worker-assignment-v4` and `workflow/worker-launch-v3` bind the canonical
`health_policy` from `workflow_liveness.py`. Direct heartbeats target 30 seconds.
A 240-second heartbeat gap, 300-second startup review and 3600-second progress
review request diagnosis. Read-only reviews occur after 300 seconds for standard
tasks and 420 seconds for integration tasks. These are observations, not stop
conditions. An authentic late heartbeat remains admissible.

Keep useful progress, observed native activity, owned command state, external
waiting, unknown health and confirmed failure separate. CPU use, output bytes,
dirty paths and self-reported heartbeats alone do not establish useful progress.
`ACTIVE` is an ownership/scope observation, not a claim that work is advancing.
Unavailable probes retain ownership; do not admit a successor while outcome or
writer quiescence is unknown. Never generate heartbeats in a background loop.

SDLC's `checkpoint_interval_iterations` and repeated unchanged Stop state request
an observation checkpoint and coordinator diagnosis. They do not stop a healthy
run. Explicit cancellation, confirmed native failure, scope/authority violations,
resource safety and exhausted confirmed-failure repair budgets still apply.

## Commands And External Effects

Validation plan v2, task acceptance policy v3 and qualification plan v3 replace
`timeout_seconds` with an `execution_policy`:

```json
{"observation_interval_seconds": 30, "deadline": null}
```

The shared process owner streams both pipes fairly, hashes every byte, and
retains bounded complete structured output only when required. Silence and
output volume do not stop a process. Incomplete or oversized structured results
cannot be parsed as successful prefixes. Native Codex JSONL and Claude stream
JSON receive bounded framing and strict assignment-bound terminal validation.
Unclassified lost frames prevent acceptance; model text never controls authority.

A non-null deadline requires a separately verified timed criterion, predeclared
negative control or established backend constraint. A plan's self-declared label
or predicted duration is insufficient. Current workflow plan owners admit no
deadline without that independent authority; the shared runner's trusted
negative-control binding is tested separately.

Local process-group absence does not prove a remote effect stopped. Qualification
reserves the exact target durably while releasing coordinator locks during
external waits. Unknown cleanup retains that reservation. Recovery checks its
identity and original writer quiescence before effects. Cleanup authority
survives cancellation; successful acceptance still requires fresh authority and
candidate checks after cleanup. Private Worktree operations likewise retain a
pinned process group through Git work and observe lock waits without expiry.

Validation and final delivery checks reserve evaluator ownership before launch,
release coordinator locks during evaluation, and recheck authority and the exact
candidate before publication. Both stream EOFs and writer quiescence are required.
Incomplete output preserves an unknown operation and its timing observation;
neither a retry nor a successor can bypass that reservation.

## Verifier Performance Evidence

Both preflights check prerequisites before launching suites, collect exact fully
qualified test IDs and preserve capability coverage.
IDs remain bound to the collected test objects when fixture setup changes their
attributes; result callbacks cannot rename or lose the declared cases.
Only explicitly audited independent groups may overlap, with at most two groups. Other groups are
exclusive. One parent owns aggregation and reports. A required failure stops
queued work, interrupts owned peers with cleanup grace, and retains completed,
interrupted and not-run outcomes. Partial groups cannot qualify a gate.

Record actual collection, setup, execution, cleanup and observation-finalization
durations, byte counts and outcomes. Parallel group durations may overlap; their
sum is not campaign wall time. Timing is diagnostic and never acceptance authority.
Compare completed equivalent trials only. Synthetic five-day clock tests prove
policy behavior, not five days of native host/provider uptime.
