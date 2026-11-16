# Verification Checklist

## Lightweight profile

- The source skill exists and its folder/frontmatter name is
  `implement`.
- `agents/openai.yaml` keeps `allow_implicit_invocation: false`.
- The public interface accepts a bare current plan, an inline request,
  `continue [project-folder]`, `status [project-folder]`, and help. Retired commands fail without aliases.
- Objective/basis, explicit binding, specification ownership, scheduler,
  temporary-Git delivery, steering, cancellation and recovery suites pass.
- Dispatch rejects changed, missing, or symlinked mandatory execution inputs
  before creating workers. The sensitive-data regression suite is a required
  deterministic gate; a failure cannot be omitted from the profile.
- The verifier's own prompt, lifecycle, collector, reporting, semantic, and
  deterministic-gate suites pass before any live lifecycle mutation.
- The readiness integration suite gates live preparation, including bounded
  traversal where empty directories do not consume the real-file allowance.
- No Docker command, worker dispatch, persistent application, or real project
  is created.
- The report records live capabilities as `NOT_RUN` rather than PASS.

## Live profile

- Preflight passes against unchanged source/install payloads before replacement.
- The frozen physical catalog is revalidated at launch, continuation, admission
  and cleanup. A changed receipt, file, imported helper, or symlink fails closed.
- Root marker version 1 and active generation version 2 are checked separately;
  unsupported state is never adopted or rewritten.
- The private root, active state, run directory, fixture marker, project Git
  identity, and immutable generation all agree and contain no symlink.
- A previous active generation is exactly cleaned and archived before the new
  generation exists.
- Implement is invoked only through its documented public actions with the
  isolated verifier Codex home.
- The requested two-worker policy is configured before `prepare-specs` allocates
  the integration checkout. Frozen policy replacement is never a repair path.
- The plan contains disjoint frontend, API, and database tasks followed by a
  dependent integration/runtime task whose direct dependencies include all
  three tier tasks.
- Stable fixture task order is database, API, frontend, then integration. The
  database and API start together; frontend refills after database admission
  while the same API peer remains active. No artificial delay supplies overlap.
  All tier assignments declare initially absent paths and conditional discovery.
- Native departure does not release a slot before independent admission. Refill
  proof requires the same active peer immediately before and at admission and
  continuously until a replacement appears in a later snapshot. Authorized
  pre-admission recovery remains supported; missing peers, later restarts,
  pre-admission starts and ambiguous same-snapshot ordering cannot certify refill.
- The frontend worker's self-contained assignment requires container port 80;
  it does not rely on rereading the accepted objective to discover that contract.
- The API assignment binds service `db`, `task_test` credentials, and
  idempotent schema creation without a bind mount. The integration assignment
  repeats the exact service/image/label/build/port/network/volume allowlist.
- Frontend, API and integration assignments require image-local runtime read
  and directory traversal access even with host modes `0600`/`0700`. Offline
  checks cover the chosen permission or ownership setup. Preserve the native
  private umask and host modes; live startup and HTTP acceptance remain required.
- Every task has distinct native sessions, assignment, worktree, branch, digest-bound
  incoming handoff, reviewed direct-child commit, and validation result.
- Authorized recoveries preserve exact termination and authorization receipts,
  plus blocked-result receipts when present. Every attempt uses a fresh session;
  histories cannot overlap across tasks. Interruption recovery remains supported.
- Final owner reconciliation may fulfill unchanged objective statements and
  clear their effects. Each immutable basis binds its own plan action; historical
  hashes, reconciliation links and all execution contract identities still pass.
- Workers receive fresh assignment-only context. Assignments remain unarmed
  until a worker slot exists. Each worker makes `task-start` its first private
  transition after immediate Git identity verification, using the assignment's
  exact helper/workspace paths and passing its embedded digest unchanged for
  authoritative helper validation. Ad hoc JSON digest recomputation is
  forbidden. Incoming-handoff reading and deeper preflight follow. Observe liveness
  every 30 seconds with the installed owner's read-only `task-watch`. `QUEUED`
  and `PENDING_START` remain pending. Startup, stale heartbeat and read-only
  review thresholds request diagnosis without duration-only termination. Keep
  unknown health reserved; confirmed failure and scope violations still stop.
- Direct heartbeats are activity observations, not proof of useful progress;
  autonomous/background heartbeat loops are rejected.
- `task-start` is single-use, and out-of-claim mutations stop with
  `WORKER_SCOPE_VIOLATION` instead of counting as progress.
- Internal integration uses stable order and automatic local delivery; final
  validation, review, alignment, generation finalization, public source
  integration and exact worker cleanup pass.
- Explicit continuation after delivery returns `ALREADY_COMPLETE` through the
  canonical intake owner and binds each fresh native session. Completed status
  without this binding is insufficient for either project.
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
- Every deterministic, fixture, Implement, Compose, runtime,
  application, reporting, and cleanup stage has one explicit
  PASS/PARTIAL/FAIL/NOT_RUN result. Failure analysis names the failed stage and
  bounded reason; blocked downstream stages remain NOT_RUN rather than FAIL.
- Stage results are generation-fenced and recorded as each stage finishes, so
  failure-path report generation does not depend on conversation memory.
- Lifecycle `validate-results` directly accepts the canonical Implement,
  Git, post-completion unchanged invocation, application, and helper state;
  PASS finish is impossible without its digest-bound transition.
- Only `validate-results` records workflow-statistics PASS. Generic stage
  recording rejects that success before mutation; earlier report inspection
  never pre-satisfies or conflicts with canonical semantic admission.
- Plain create leaves no active lifecycle or owned runtime resource. Keep mode
  leaves exactly one current generation. Destroy preserves reports/history.

## Reporting and overhead

- Include shared and Implement-specific statistics regressions in deterministic
  preflight: initial architecture without plans, late-capture gaps, revisions,
  actual running versus reserved slots, aggregation/correction outcomes, clocks,
  contention, immutable history and reporting failure isolation.
- Semantic admission replays initial/current/terminal reports against owner
  history and requires recorded execution before completed delivery.
- Archive sanitized Markdown/JSON before removing isolated homes. Missing or
  partial history remains explicit; unsafe retention preserves the owned home.
- Report repeated unprofiled transition/status measurements separately from
  profiling and instrumentation. Fewer checks alone do not prove faster delivery.
- Fresh native qualification requires lifecycle-owned Playwright assertions,
  traces and screenshots for UI create/update/reload and the same task after API
  restart, correlated with API/PostgreSQL and exact browser cleanup.
- Native FAIL outranks PARTIAL, including when another project is incomplete.

Before native launch, bind the isolated home’s exact reviewed hook payload and
canonical host registrations. Reject missing, changed, symlinked or unexpected
importable payloads and disabled hooks. Recheck before continuation, admission
and cleanup; PASS requires this binding. Authentication remains outside reports.
