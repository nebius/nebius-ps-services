---
name: task-implementer-test
description: "Use only when the user explicitly asks to verify Task Implementer: run the no-flag lightweight contract and temporary-fixture suite without a real application, or use --create, --create --keep, and --destroy for one replaceable verifier-owned local frontend/API/PostgreSQL stack."
---

# Task Implementer Test

## Help

For `$task-implementer-test --help` or `$task-implementer-test -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Verify `task-implementer` from outside a user project. Keep deterministic
checks separate from the opt-in disposable application lifecycle.

## When To Use

- The user explicitly invokes `$task-implementer-test` to check the installed
  Task Implementer contract.
- The user explicitly adds `--create` to exercise Task Implementer on a real
  verifier-owned multi-tier fixture.
- The user explicitly adds `--create --keep` to retain that fixture for
  inspection, or `--destroy` to remove it later.

## When Not To Use

- Do not invoke this skill implicitly for ordinary implementation work.
- Do not use it on a user, production, customer, remote-backed, or otherwise
  non-disposable project.
- Do not use it instead of `$task-implementer workspace init`, `workspace
  reuse`, `run`, `integrate`, or `workspace remove` for real brownfield work.
- Do not add `--resume`, aliases, or compatibility modes.

## Inputs

Accept exactly one of these mode shapes:

```text
$task-implementer-test
$task-implementer-test --create
$task-implementer-test --create --keep
$task-implementer-test --destroy
```

Reject `--keep` alone, mixed create/destroy, repeated flags, positional
arguments, and unknown flags before mutation. The single canonical private
root is `${CODEX_HOME:-$HOME/.codex}/task-implementer-test/`; do not expose a
public alternate-root flag that would permit multiple instances.

## Required Reads

- Read `references/verification-checklist.md` for every mode.
- Read `task-implementer/SKILL.md`, its two referenced workflow documents,
  metadata, and current helper/test surfaces before judging the contract.
- Read `scripts/verify_task_implementer.py` before relying on or patching the
  lightweight verifier.
- For create or destroy modes, read `references/live-app-test.md`,
  `assets/app-prompt.md.template`, `assets/live-results.schema.json`, and the
  lifecycle helper's `--help`.
- Read the active lifecycle state and exact generation ID from the helper;
  never reconstruct private state from conversation memory.

## Writes

The no-flag verifier may write only its private sanitized report and temporary
test fixtures. Create modes may additionally write one owned lifecycle under:

```text
${CODEX_HOME:-$HOME/.codex}/task-implementer-test/
├── owner.json
├── active.json
├── report.md
├── archive/<generation>/
└── runs/<generation>/
    ├── project/
    ├── codex-home/
    ├── lifecycle.json
    ├── compose.snapshot.json
    └── evidence/
```

The isolated run `codex-home/` owns Task Implementer prompts, run state, and
worktrees for the disposable fixture. Never reuse or delete the user's normal
`${CODEX_HOME}/task-implementer/` state. Reports and archived lifecycle
summaries survive destroy; the active project, isolated Codex home, raw
evidence, and exact owned runtime resources do not.

## Process

1. Parse the complete mode before any mutation.
2. For no flags, run:

   ```bash
   python3 task-implementer-test/scripts/verify_task_implementer.py
   ```

   It validates explicit-only metadata, the exact five-action public surface,
   source-installed parity, and all current Task Implementer contract,
   workspace, specification, scheduler, temporary-Git wave, and persistent-lane
   suites, plus the verifier's own helper/lifecycle/semantic suites. It must not
   call Docker, create workers, or touch a real project.
   The bounded default is 900 seconds per suite so the complete disposable
   linked-worktree crash matrix is not cut off by the worker's shorter
   per-assignment runtime budget.
   Report the deterministic profile independently; absence of live evidence is
   `NOT_RUN`, not synthetic live PASS.
3. For `--destroy`, invoke the lifecycle helper's `destroy` action. A missing
   active lifecycle returns `ALREADY_DESTROYED`. Preserve the latest sanitized
   report and archive.
4. For either create mode, run the no-flag verifier first. Any deterministic
   FAIL blocks mutation and preserves a retained instance.
5. Invoke lifecycle `prepare`. While holding its private lock, it validates
   ownership, destroys and archives the previous exact active generation, and
   only then creates a fresh seeded local-only brownfield Git fixture with an
   owned bare `origin`, configured `origin/HEAD`, and non-default source branch. If
   cleanup or ownership is ambiguous, stop; never create a second instance.
6. Retain the immutable generation ID returned by `prepare`. Pass it to every
   later lifecycle mutation so a superseded invocation cannot modify its
   replacement.
7. Run `workspace init`, then render `assets/app-prompt.md.template` into the
   generated managed prompt while preserving its schema, prompt ID, and
   creation fields. Use only the real public Task Implementer interface against
   the owned fixture:

   ```text
   $task-implementer workspace init <project-folder>
   $task-implementer run <managed-prompt-path>
   $task-implementer integrate <project-folder>
   ```

   Run it with the lifecycle's isolated Codex home. Do not bypass the public
   contract with private `wave-*`, `task-*`, or orchestration commands.
8. Require the first dependency wave to contain disjoint frontend, API, and
   database work; require a later integration/runtime task. The frontend
   assignment must repeat the container-port-80 contract before wave planning
   so the worker remains self-contained. The API/database/integration
   assignments must likewise repeat the canonical task contracts in
   `references/live-app-test.md`; reject an under-specified plan before worker
   dispatch. In particular, require every service network attachment to use
   the verifier's long-form object map with an empty options object; a labelled
   top-level network does not make service list syntax valid. Verify distinct
   worker sessions/worktrees/branches, one reviewed direct-child commit per
   task, digest-bound handoffs, stable-order integration, verified ff-only
   promotion, combined validation, final alignment, successful cleanup, and an
   unchanged-prompt `ALREADY_COMPLETE` rerun. While a worker runs, observe its
   Task Implementer liveness state every 30 seconds. Give workers fresh
   assignment-only context, arm an assignment only when a worker slot is
   available, and require the worker to make `task-start` its first private
   transition after immediate Git identity verification. The worker must use
   the assignment's exact embedded helper/workspace paths and pass its embedded
   digest unchanged; `task-start` owns canonical digest validation, and ad hoc
   JSON recomputation is forbidden. Interrupt
   immediately if `task-start` is not reached within 60 seconds, when a
   heartbeat becomes hard-stale at 240 seconds, after the assignment's `standard`
   300-second or dependent `integration` 420-second read-only budget, or on
   total-budget expiry; do not recover or
   retry this disposable verification run. Once the
   run generation is finalized and cleaned, invoke public `integrate` and bind
   its exact validated candidate, then invoke public `workspace remove` for the
   now-idle lane. Only after both source integration and lane removal succeed
   continue to runtime evidence and report generation.
   At the assignment's 240-second `standard` or 360-second `integration`
   warning, demand an immediate edit or blocker.
   Reject autonomous/background heartbeat loops as no-progress behavior.
   Require single-use `task-start`; stop on `WORKER_SCOPE_VIOLATION` because
   only claimed-file mutations count as progress.
   After every deterministic, workspace, planning, worker, wave, finalization,
   and rerun stage reaches a terminal outcome, invoke lifecycle `record-stage`
   with the immutable generation, canonical stage name, status, and one bounded
   evidence or failure summary. Do not postpone stage recording until the end.
9. Task Implementer must not start Docker or containers. After it promotes and
   cleans its worktrees, invoke lifecycle `validate-compose` and then
   `compose-up` with the same generation ID; the helper records live ownership
   before the first Docker mutation, discovers the Docker-assigned loopback
   port, and inspects the exact post-start inventory. Require no host-published
   PostgreSQL port and exact project-plus-generation labels on every owned
   container, network, volume, and locally built image.
10. Validate frontend delivery, API create/list/update behavior, matching
    PostgreSQL data, and persistence after an API restart through lifecycle
    `collect-application`; do not call the collector directly. Write
    `evidence/live-results.json` using the strict schema, then invoke lifecycle
    `validate-results` with the isolated run, managed prompt, and current Task
    Implementer scripts. The helper directly validates canonical orchestration,
    Git, unchanged post-completion invocation, application, and lifecycle state
    and records the manifest digest. Statuses or prose without this transition
    never prove PASS.
11. Build the sanitized report with a complete ordered stage matrix. It must
    show bounded evidence for every deterministic, fixture, workspace,
    planning, tier-worker, wave-integration, finalization, rerun, Compose,
    runtime, application, semantic-validation, reporting, and cleanup stage.
    Include explicit failure analysis, downstream NOT_RUN stages, the minimum
    next action, project and promoted Git identity when available, and the
    exact report path. Do not include prompt bodies, raw logs, credentials,
    private internal IDs, or environment-specific secrets.
12. Call lifecycle `finish` in a finally-style path. A PASS finish is rejected
    unless the generation-fenced results transition succeeded. If report
    construction fails, finish with FAIL/PARTIAL, the exact failed stage, and a
    bounded reason. The helper renders the same complete stage matrix from its
    lifecycle-owned ledger before cleanup; it must not fall back to an
    overall-only failure sentence. Plain `--create` removes the active project,
    isolated Task Implementer state, raw evidence, and exact owned Docker
    resources after either success or failure.
    `--create --keep` retains the current generation and runtime for the user.
    The lifecycle helper finalizes the preserved report with `CLEANED`,
    `RETAINED`, `DESTROYED`, or `CLEANUP_FAILED`; cleanup failure overrides the
    test outcome and must be reported.

## Idempotency

- No-flag reruns replace only the sanitized lightweight report.
- Each live stage is generation-fenced and terminal once recorded. An exact
  repeat is idempotent; conflicting status or detail fails closed.
- There is at most one active generation. Every create is replace-on-create,
  including `--create --keep`.
- Replacement uses the exact standalone destroy path before allocating the
  next generation. Failed cleanup leaves either the old active generation or
  its exact deletion tombstone and blocks creation until destroy retries it.
- Mutations are serialized and generation-fenced. A stale invocation fails
  before changing the replacement.
- Repeated destroy returns `ALREADY_DESTROYED` after the first exact cleanup.
- Explicit destroy and replace-on-create may remove a user-inspected dirty or
  advanced checkout inside the exact owned run; `--keep` is disposable by
  contract. A linked worktree outside that run or a remote still blocks.
- Preserve cumulative cleanup evidence across retries and distinguish a
  confirmed absent resource from a Docker-daemon or inspection failure.

## Failure Handling

- Classify deterministic verifier failure as `FAIL` and do not mutate a live
  fixture.
- Classify unavailable Docker, Git, Task Implementer runtime, or local service
  capability before an attempted action as `PARTIAL`.
- Classify a failed attempted worker, Git, application, database, or runtime
  action as `FAIL`.
- Classify wrong owner, symlink, path escape, remote Git repository, marker
  mismatch, generation mismatch, an external linked worktree, or Docker
  ownership ambiguity as `OWNERSHIP_BLOCKED` and stop without cleanup or
  replacement. Strict clean/head identity remains a PASS gate, not a blocker
  after explicit destroy or replacement of an owned fixture.
- Preserve the active state and exact recovery inventory before deletion; once
  deletion begins, retain an exact generation tombstone that resumes without
  re-adopting or revalidating a partially removed project. Never use broad
  prune, force removal, process-name matching, or guessed IDs.

## Must Not

- Do not create more than one active instance or silently adopt old/unowned
  state.
- Do not touch real repositories, ordinary Task Implementer state, installed
  skills, hooks, credentials, remotes, cloud services, or production data.
- Do not push, publish, open or merge a PR, force-remove worktrees, force-delete
  branches, prune Git/Docker globally, or reuse a user's browser session.
- Do not treat names, paths, labels, process discovery, or report prose alone
  as ownership or semantic proof.
- Do not print or persist secrets, prompt bodies, raw logs, or private Task
  Implementer orchestration records in the sanitized report.

## Completion Criteria

- No flags: the deterministic profile and every skipped live capability are
  reported as stage results, with an exact report path; no real application
  exists.
- `--create`: the owned multi-tier application and Task Implementer workflow
  are semantically verified, the report is complete, and exact cleanup leaves
  no active lifecycle or owned runtime resource.
- `--create --keep`: the same evidence exists and exactly one current owned
  generation remains, with its inspection and destroy commands reported.
- `--destroy`: the retained owned generation is absent or the helper returns
  `ALREADY_DESTROYED`; reports and lifecycle history remain.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return the selected mode, deterministic status, live status, current lifecycle
status, report path, stage totals, failed-stage analysis, not-run stages,
cleanup or retention result, and the minimum next action. For kept mode,
include the exact project path and the `$task-implementer-test --destroy`
command.
