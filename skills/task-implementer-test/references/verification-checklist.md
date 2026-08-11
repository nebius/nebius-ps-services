# Verification Checklist

## Lightweight profile

- The source skill exists and its folder/frontmatter name is
  `task-implementer`.
- `agents/openai.yaml` keeps `allow_implicit_invocation: false`.
- The public interface contains only `workspace init [project-folder]`,
  `run <prompt-ref-or-file>`, `integrate [project-folder]`, and
  `workspace remove [project-folder]`.
- Source and installed copies match when an installed copy is present.
- The current contract, prompt workspace, managed specs, scheduler, real
  temporary-Git wave lifecycle, and persistent-lane suites pass.
- The verifier's own prompt, lifecycle, collector, reporting, semantic, and
  deterministic-gate suites pass before any live lifecycle mutation.
- No Docker command, worker dispatch, persistent application, or real project
  is created.
- The report records live capabilities as `NOT_RUN` rather than PASS.

## Live profile

- Preflight passes before replacement.
- The private root, active state, run directory, fixture marker, project Git
  identity, and immutable generation all agree and contain no symlink.
- A previous active generation is exactly cleaned and archived before the new
  generation exists.
- Task Implementer is invoked only through its four public actions with the
  isolated verifier Codex home.
- The plan contains disjoint frontend, API, and database tasks followed by a
  dependent integration/runtime task whose direct dependencies include all
  three tier tasks.
- The frontend worker's self-contained assignment requires container port 80;
  it does not rely on rereading the managed prompt to discover that contract.
- The API assignment binds service `db`, `task_test` credentials, and
  idempotent schema creation without a bind mount. The integration assignment
  repeats the exact service/image/label/build/port/network/volume allowlist.
- Every task has a unique session, assignment, worktree, branch, digest-bound
  incoming handoff, reviewed direct-child commit, and validation result.
- Workers receive fresh assignment-only context. Assignments remain unarmed
  until a worker slot exists. Each worker makes `task-start` its first private
  transition after immediate Git identity verification, using the assignment's
  exact helper/workspace paths and passing its embedded digest unchanged for
  authoritative helper validation. Ad hoc JSON digest recomputation is
  forbidden. Incoming-handoff reading and deeper preflight follow. Liveness is observed every 30
  seconds; an armed worker's 60-second dispatch-to-start miss, 240-second stale heartbeat, immutable
  `standard` 300-second or dependent `integration` 420-second read-only
  interval, or total timeout stops the disposable run without recovery or
  blind retry.
- The matching 240- or 360-second read-only warning demands an immediate edit or blocker;
  autonomous/background heartbeat loops are rejected as no progress.
- `task-start` is single-use, and out-of-claim mutations stop with
  `WORKER_SCOPE_VIOLATION` instead of counting as progress.
- Internal integration uses stable order and ff-only lane promotion; final
  validation, review, alignment, generation finalization, public source
  integration, explicit lane removal, and cleanup pass.
- Repeating `run` with the unchanged prompt returns `ALREADY_COMPLETE`.
- Only the web entrypoint is host-published and it binds to loopback.
  PostgreSQL remains internal to the Compose network.
- Before Compose parses generated input, exact top-level, service, build,
  port, mount, network, and volume allowlists reject external files, unsafe
  build options, and custom ownership names.
- Containers, networks, and volumes have exact Compose-project plus generation
  labels; built images have exact verifier-project plus generation labels.
- Application HTTP, database, inventory, and restart evidence is collected
  while one generation lifecycle lock remains held.
- The frontend loads, API create/list/update passes, the database contains the
  same record, and that record survives an API restart.
- The strict live results manifest and sanitized report are complete.
- Every deterministic, fixture, Task Implementer, Compose, runtime,
  application, reporting, and cleanup stage has one explicit
  PASS/PARTIAL/FAIL/NOT_RUN result. Failure analysis names the failed stage and
  bounded reason; blocked downstream stages remain NOT_RUN rather than FAIL.
- Stage results are generation-fenced and recorded as each stage finishes, so
  failure-path report generation does not depend on conversation memory.
- Lifecycle `validate-results` directly accepts the canonical Task Implementer,
  Git, post-completion unchanged invocation, application, and helper state;
  PASS finish is impossible without its digest-bound transition.
- Plain create leaves no active lifecycle or owned runtime resource. Keep mode
  leaves exactly one current generation. Destroy preserves reports/history.
