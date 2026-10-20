# Implement Test

The public fixture request repeats the required per-tier assignment contracts.
The coordinator must preserve concrete interfaces and constraints in the locked
task fields before dispatch; global objective text alone is insufficient.
Final validation preserves each completed task's historical basis and verifies
its admission plus the owner reconciliation chain to the final specifications.
A new final spec receipt does not replace the basis under which a task ran.

Execution supports Codex and Claude through the selected native host context.
See `SKILL.md` for invocation, required setup and evidence boundaries.

`implement-test` is an explicit verifier for `implement`.

```text
$implement-test
$implement-test --create
$implement-test --create --keep
$implement-test --destroy
```

No flags run the Implement contract, objective/basis and continuation, and
temporary-fixture and sensitive-data regression suites plus the verifier helper, lifecycle, reporting, and
semantic suites. They do not create a real application, start Docker, or
dispatch implementation workers.
The deterministic harness allows each suite up to 900 seconds; this covers the
full disposable linked-worktree crash matrix without changing worker budgets.

`--create` first replaces any prior exactly owned fixture, then exercises the
real Implement public interface on a seeded local-only brownfield
frontend/API/PostgreSQL project. It verifies dependency waves, worker and Git
evidence, the running application, database correlation, restart persistence,
and a canonical post-completion cross-session continuation. A lifecycle-owned
semantic gate binds those results to the generation before PASS can finish. It
writes the immutable public invocation receipt beside the native observations
in the fixture generation's `evidence/` directory, which is the same location
required by lifecycle validation. It leaves the product's run state untouched.
Native observations retain only bounded failure counts and allowlisted blocker
codes. A completed native session without an accepted objective is a failed
intake, distinct from incomplete observation; diagnostic mentions never prove
that a workflow transition occurred.
It writes a sanitized report and cleans the fixture even when the test fails. The
report contains an ordered stage matrix with totals, per-stage evidence,
explicit failure analysis, downstream NOT_RUN stages, and the minimum next
action. Stage outcomes are recorded as they happen, so the cleanup/fallback
path cannot collapse a failed run into one overall sentence.
Successful cleanup preserves the corrective action for a failed live test.
Worker liveness is checked every 30 seconds with fresh assignment-only worker
context. Queued assignments remain unarmed until a worker slot exists; the
worker uses exact helper/workspace paths embedded in assignment v1 and passes
the embedded digest unchanged to `task-start` for authoritative validation;
it never spends the start budget guessing JSON serialization. The
verifier stops a disposable run when an armed worker's `task-start` misses 60
seconds, after a 240-second stale heartbeat, or after the immutable `standard` 300-second
or dependent `integration` 420-second read-only/no-file budget instead of
waiting or recovering it. Successful local delivery
proceeds directly to runtime evidence and report generation.
The rendered objective leaves post-completion fresh-session continuation to
the verifier; the initial Implement turn ends after delivery and cleanup.
The Codex launcher uses a scoped permission profile extending `:workspace`,
with writes to the isolated native home and the fixture's exact Git directory.
Its isolated config must omit legacy `sandbox_mode` and
`sandbox_workspace_write` settings, which override permission profiles.
Forcing `workspace-write` would prevent the required Git transactions.
Before each invocation, native `config/read` resolves all active layers for the
fixture. The launcher rejects effective legacy sandbox settings or changes to
the exact scoped profile; raw configuration remains in memory and is discarded.
The Codex verifier owns one foreground stdio App Server for each fresh public
invocation. It reads loaded ephemeral children with metadata-only `thread/read`
requests; it never resumes, subscribes, or reads their histories to observe them.
The refill evaluator retains a terminated worker until its result is admitted,
while requiring the same peer to remain active in every intervening snapshot.
Peer disappearance/restart and replacements started before admission invalidate
that candidate. Extra legitimate observations cannot erase a valid refill.
Two bounded native snapshots bracket an unchanged product-state sample. Native
status/turn transitions, changed inventories, or rounds exceeding 500 ms invalidate
the sample. All requests share the invocation deadline; raw protocol bytes are
hashed and discarded. Terminal failure stops immediately, and exact process-group
quiescence is required after graceful shutdown or bounded cleanup escalation.

The verifier escalates the matching 240- or 360-second read-only warning and
rejects autonomous or background heartbeat loops.
It also rejects replayed `task-start` and out-of-claim mutations as liveness
progress.
Generated Compose is treated as untrusted: an exact pre-Compose allowlist
rejects external file and unsafe build features, while locally built images
must carry exact generation and verifier-project labels. Service network
attachments must use long-form object-map syntax with empty option objects;
list syntax fails before Docker runs. Application mutation
and evidence collection hold one generation lease through restart verification.
Before HTTP checks and after the API restart, the collector requires exactly
one running container for each required service, healthy when a healthcheck is
configured. It waits up to 60 seconds only for startup health, including Docker
inspection time; failed services, late results or invalid inventories fail.
Imported and direct CLI execution share bounded lifecycle errors.
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
