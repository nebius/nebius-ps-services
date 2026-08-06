# Task Implementer Test

`task-implementer-test` is an explicit verifier for `task-implementer`.

```text
$task-implementer-test
$task-implementer-test --create
$task-implementer-test --create --keep
$task-implementer-test --destroy
```

No flags run the Task Implementer contract and temporary-fixture suites plus
the verifier helper, lifecycle, reporting, and semantic suites. They do not
create a real application, start Docker, or dispatch implementation workers.

`--create` first replaces any prior exactly owned fixture, then exercises the
real four-action Task Implementer interface on a seeded local-only brownfield
frontend/API/PostgreSQL project. It verifies dependency waves, worker and Git
evidence, the running application, database correlation, restart persistence,
and a canonical post-completion unchanged-prompt invocation. A lifecycle-owned
semantic gate binds those results to the generation before PASS can finish. It
writes a sanitized report and cleans the fixture even when the test fails. The
report contains an ordered stage matrix with totals, per-stage evidence,
explicit failure analysis, downstream NOT_RUN stages, and the minimum next
action. Stage outcomes are recorded as they happen, so the cleanup/fallback
path cannot collapse a failed run into one overall sentence.
Worker liveness is checked every 30 seconds with fresh assignment-only worker
context. Queued assignments remain unarmed until a worker slot exists; the
worker uses exact helper/workspace paths embedded in assignment v7 and passes
the embedded digest unchanged to `task-start` for authoritative validation;
it never spends the start budget guessing JSON serialization. The
verifier stops a disposable run when an armed worker's `task-start` misses 60
seconds, after a 240-second stale heartbeat, or after the immutable `standard` 300-second
or dependent `integration` 420-second read-only/no-file budget instead of
waiting or recovering it. Successful workspace validation
proceeds directly to runtime evidence and report generation.
The verifier escalates the matching 240- or 360-second read-only warning and
rejects autonomous or background heartbeat loops.
It also rejects replayed `task-start` and out-of-claim mutations as liveness
progress.
Generated Compose is treated as untrusted: an exact pre-Compose allowlist
rejects external file and unsafe build features, while locally built images
must carry exact generation and verifier-project labels. Service network
attachments must use long-form object-map syntax with empty option objects;
list syntax fails before Docker runs. Application mutation
and evidence collection hold one generation lease from the first HTTP request
through restart verification.
The canonical-model pass accepts Docker Compose's injected null `command` and
`entrypoint` defaults and empty network `ipam` default only after the raw source
pass has proved those keys were not authored; any non-null runtime or non-empty
IPAM override remains rejected.

`--create --keep` performs the same replacement and verification but retains
the one current owned application, private verifier state, and runtime for
inspection. A later `--destroy` removes only that recorded owned generation;
repeating destroy returns `ALREADY_DESTROYED`.
Filesystem cleanup atomically moves the run behind a recoverable deleting
pointer, so an interrupted or partially failed recursive deletion resumes only
that exact tombstone without trying to re-adopt a damaged project.

Create never blindly overwrites a directory. It performs an ownership-checked
destroy-and-replace under the canonical private root. A symlink, wrong marker,
remote, external linked worktree, stale generation, or ambiguous Docker
resource blocks replacement so a second instance is never created. Because
the kept app is disposable, explicit destroy or the next create removes edits
inside that exact owned run.
