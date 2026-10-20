---
name: implement-test
description: "Use only when the user explicitly asks to verify Implement: run the no-flag contract/fixture suite, or use --create/--keep/--destroy for one replaceable verifier-owned local frontend/API/PostgreSQL stack."
disable-model-invocation: true
---

# Implement Test

## Help

For `$implement-test --help` or `$implement-test -h` (including native Claude forms), return concise help and stop before
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

## Agent Compatibility

Before stateful verification, read `../global-context-management/references/agent-hosts.md`.
The lifecycle binds the selected agent through its isolated native home (`codex-home/` or `claude-home/`);
set that host's native home variable for the public workflow. Resume and cleanup
require the recorded host. Never transfer a trial or infer live proof from fixtures.

Use `$implement-test` in Codex, `/implement-test` in Claude Code, or
`/skills:implement-test` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Purpose

Verify `implement` from outside a user project. Keep deterministic
checks separate from the opt-in disposable application lifecycle.

## When To Use

- The user explicitly invokes `$implement-test` to check the installed
  Implement contract.
- The user explicitly adds `--create` to exercise Implement on a real
  verifier-owned multi-tier fixture.
- The user explicitly adds `--create --keep` to retain that fixture for
  inspection, or `--destroy` to remove it later.

## When Not To Use

- Do not invoke this skill implicitly for ordinary implementation work.
- Do not use it on a user, production, customer, remote-backed, or otherwise
  non-disposable project.
- Use `$implement` itself for ordinary implementation work.
- Do not add `--resume`, aliases, or compatibility modes.

## Inputs

Accept exactly one of these mode shapes:

```text
$implement-test
$implement-test --create
$implement-test --create --keep
$implement-test --destroy
```

Reject `--keep` alone, mixed create/destroy, repeated flags, positional
arguments, and unknown flags before mutation. The single canonical private
root is `<agent-home>/implement-test/`; do not expose a
public alternate-root flag that would permit multiple instances.

## Required Reads

- Read `references/verification-checklist.md` for every mode.
- Read `implement/SKILL.md`, its two referenced workflow documents,
  metadata, and current helper/test surfaces before judging the contract.
- Read `scripts/verify_implement.py` before relying on or patching the
  lightweight verifier.
- For create or destroy modes, read `references/live-app-test.md`,
  `assets/app-objective.md.template`, `assets/live-results.schema.json`, and the
  lifecycle helper's `--help`.
- Read the active lifecycle state and exact generation ID from the helper;
  never reconstruct private state from conversation memory.

## Writes

The no-flag verifier may write only its private sanitized report and temporary
test fixtures. Create modes may additionally write one owned lifecycle under:

```text
<agent-home>/implement-test/
├── owner.json
├── active.json
├── report.md
├── archive/<generation>/
└── runs/<generation>/
    ├── project/
    ├── <agent>-home/
    ├── lifecycle.json
    ├── compose.snapshot.json
    └── evidence/
```

The isolated run `<agent>-home/` owns normalized Implement objectives, run state, and
worktrees for the disposable fixture. Never reuse or delete the user's normal
`<agent-home>/implement/` state. Reports and archived lifecycle
summaries survive destroy; the active project, isolated agent home, raw
evidence, and exact owned runtime resources do not.

## Process

1. Parse the complete mode before any mutation.
2. For no flags, run:

   ```bash
   python3 implement-test/scripts/verify_implement.py
   ```

   It validates explicit-only metadata, the direct-objective public surface,
   source-installed parity, and all current Implement contract,
   objective/basis, specification ownership, ready-slot scheduler, and temporary-Git lifecycle
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
7. Render `assets/app-objective.md.template` as a direct conversation objective
   using `scripts/render_app_objective.py`. Invoke the PUBLIC workflow in a fresh
   native session rooted at the fixture, with its isolated agent home:

   ```text
   $implement <rendered implementation request>
   ```

   The rendered fixture request is input, never a workflow-managed prompt file.
   It ends Implement at local delivery and cleanup; the verifier exclusively
   owns the later fresh-session continuation. Do not assign that replay to the
   initial coordinator, which would transfer ownership before the harness can
   observe its completed invocation.
   The harness may launch the native session and observe state. It must not call
   private intake, planning, worker, integration, or completion transitions to
   satisfy a product-owned step. Record native invocation evidence through
   `scripts/public_workflow_trial.py`; record only bounded observations and
   hashes, never raw conversation or tool output. Missing native event evidence
   remains PARTIAL. An intervention invalidates the affected trial.
   The Codex isolated config must omit legacy sandbox settings so the launcher's
   scoped native permission profile can permit the fixture's Git transactions.
   The launcher checks native effective configuration across all active layers
   before each invocation and fails closed on legacy settings or profile drift.
   Keep hooks active; never substitute unrestricted execution.
   Codex uses the owning stdio App Server for ephemeral public invocations and
   bounded metadata-only child snapshots. Reject samples crossing native status
   transitions; never infer live overlap from stale events or worker prose.
8. Require disjoint frontend, API, and database tasks in the first dependency
   wave and a dependent integration task. Repeat the container-port-80 contract
   and each canonical tier contract from `references/live-app-test.md` in its
   assignment. Reject planning before dispatch when a worker lacks its inputs.
   Observe genuine native overlap, a slot refill after termination and admission
   while a peer remains active, and the full-wave integration barrier. Capacity
   fallback is valid product behavior but cannot prove the parallel scenario.
   Require distinct native sessions, fresh worktrees, one direct-child commit
   per task, current immutable basis, stable integration, combined validation,
   final alignment, automatic local delivery and exact worker cleanup.
   Workers use embedded helper/workspace paths and pass their assignment
   digest unchanged; ad hoc JSON recomputation is forbidden. Observe liveness
   every 30 seconds. An armed worker must reach task-start within 60 seconds;
   stop the trial at a 240-second stale heartbeat, its standard 300-second or
   dependent integration 420-second read-only budget, or total timeout. Reject
   background heartbeat loops and out-of-claim progress. Native termination is
   independent of a worker's result text. Do not repair/retry an affected trial.
   Invoke `$implement continue <project-folder>` in a fresh native session after
   delivery and require ALREADY_COMPLETE with unchanged Git/objective identity.
   Record stage outcomes immediately with generation-fenced `record-stage`.
9. Implement must not start Docker or containers. After it promotes and
   cleans its worktrees, invoke lifecycle `validate-compose` and then
   `compose-up` with the same generation ID; the helper records live ownership
   before the first Docker mutation, discovers the Docker-assigned loopback
   port, and inspects the exact post-start inventory. Require no host-published
   PostgreSQL port and exact project-plus-generation labels on every owned
   container, network, volume, and locally built image.
10. Require exactly the three required services to be running and healthy when
    a healthcheck is configured, polling only startup health for up to 60 seconds
    including Docker inspections before the first HTTP request.
    Reject invalid inventories and failed services immediately. Validate
    frontend delivery, API create/list/update behavior, matching
    PostgreSQL data, and persistence after an API restart through lifecycle
    `collect-application`; repeat the health check after restart and do not call
    the collector directly. Write
    `evidence/live-results.json` using the strict schema, then invoke lifecycle
    `validate-results` with the isolated run, immutable public invocation receipt, and current
    Worktree scripts. The helper directly validates canonical orchestration,
    Git, unchanged post-completion invocation, application, and lifecycle state
    and records the manifest digest. Statuses or prose without this transition
    never prove PASS.
    Completed assignments retain their original immutable execution basis after
    final owner specification reconciliation. Verify that historical basis,
    admission binding and hash-linked reconciliation ancestry to the current
    basis; objective, plan, helpers, inputs and ownership must remain identical.
11. Build the sanitized report with a complete ordered stage matrix. It must
    show bounded evidence for every deterministic, fixture, direct intake,
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
    isolated Implement state, raw evidence, and exact owned Docker
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
- Classify unavailable Docker, Git, Implement runtime, or local service
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
- Do not touch real repositories, ordinary Implement state, installed
  skills, hooks, credentials, remotes, cloud services, or production data.
- Do not push, publish, open or merge a PR, force-remove worktrees, force-delete
  branches, prune Git/Docker globally, or reuse a user's browser session.
- Do not treat names, paths, labels, process discovery, or report prose alone
  as ownership or semantic proof.
- Do not print or persist secrets, prompt bodies, raw logs, or private Implement
  orchestration records in the sanitized report.

## Completion Criteria

- No flags: the deterministic profile and every skipped live capability are
  reported as stage results, with an exact report path; no real application
  exists.
- `--create`: the owned multi-tier application and Implement workflow
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
include the exact project path and the `$implement-test --destroy`
command.
