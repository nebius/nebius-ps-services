# Pre-effect input correction

Input correction is separate from `failure-event-v2` and `repair-control-v3`.
Only a canonical owner can issue structured feedback before publication or
requested helper effects. Never infer eligibility from `SPEC_CONFLICT`, a
nonzero exit, prose, or a role name.

The spec owner recognizes heading depth/spacing, an unambiguous missing record
closing marker, and inline-code formatting of literal HTTP routes with angle
parameters only when those known transformations produce a complete valid
canonical pair. A missing closing marker must end before the next record, outer
section or managed-region end; mismatched markers stay rejected. Code examples,
generic HTML and ambiguous markup are not rewritten. It never adds acceptance
content or changes IDs, mappings, intent or semantics. Human-owned text outside
the managed region remains byte-bound, including its whitespace. The result
owner recognizes a redundant envelope only when the enclosed complete object
already passes its canonical validator. Other errors use normal classification.
Permission, stale authority, executed checks and uncertain effects never qualify.
Without an initialized coordinator checkpoint, canonical publication has no
correction exemption. The active authoring phase or the recorded next owner of
a completed phase binds the operation; both current state and checkpoint must
agree. Startup's initialized requirements handoff follows the same contract.
An existing repair pointer excludes this lane: classified failures retain their
original accounting. Existing immutable result receipts retain their original
coordinator and termination evidence on exact replay; only new result ingestion
can enter input correction.

An initial eligible rejection opens one operation: run, accepted objective,
phase and execution for specs; run, execution, wave and logical task for results.
Candidate hashes, replacement workers, caller operation IDs and process restarts
do not identify a new operation. Two subsequent submissions may correct it within
300 active seconds. A valid second correction succeeds. Exhaustion or semantic
change returns `classification_required`; the existing classifier and four
feature-dispatch ceiling remain unchanged. Historical trials are not relabeled.

Keep bounded rejected candidates in separate private artifacts and feedback in a
compact serialized ledger. Preserve each submission's own outcome. Replays never
consume another submission or report an earlier invalid candidate as valid.
A live phase remains active across restarts. When the coordinator records a user
pause, it calls private `workflow_transition.py corrections-pause --run-dir PATH`;
authorized `continue_run` resumes recorded clocks automatically; the explicit
`corrections-resume` adapter is idempotent. These adapters exclude
only the recorded paused interval and never renew counts or the active window.

Use supported checks before submission:

```text
project_specs.py check-candidates --requirements-candidate PATH --design-candidate PATH
workflow_transition.py check-result --assignment PATH --result PATH
```

Both are read-only, reuse canonical validators and grant no publication, result
ingestion or independent acceptance. Use the verified absolute helper paths and
working directories supplied by the owner. Worker launch envelopes include the
result check argv and schema path/digest. Never import internal modules via
`runpy` to discover a supported operation.

For rejected spec feedback with a correction kind, submit the unchanged candidate
pair through the ordinary paired publisher with current authority, HEAD and
predecessor hashes. Its `correction_pending` result reserves and archives the
initial rejection before any correction. Submit at most the returned allowance;
read-only checks alone never authorize repeated edits. Run remaining configured
Markdown checks after the candidate owner accepts the pair. Other lint failures
and unavailable tools still use normal classification.
