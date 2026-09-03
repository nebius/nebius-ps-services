---
name: task-implementer
description: "Requires explicit invocation for durable brownfield work in persistent project worktree lanes: initialize or reopen a workspace, run dependency waves, integrate completed generations, or remove an idle lane. Not for one-shot work, Agentic SDLC, standalone Git, or generic parallel agents."
---

# Task Implementer

## Help

For `$task-implementer --help` or `$task-implementer -h`, return concise help
and stop before any workflow step. State the purpose and invocation policy.
Show exact usage for every public action. Describe each public action,
positional argument, and flag in one concise line, including `-h, --help`; say
"No additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external
systems. Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Coordinate a complex brownfield request from one durable private Markdown
prompt. A run compiles accepted intent, plans dependency waves, dispatches
isolated workers, integrates verified commits, promotes each wave to a
persistent lane, and resumes from durable state until complete or blocked.

## Invocation Policy

Public entry requires explicit invocation. Keep
`policy.allow_implicit_invocation: false` in `agents/openai.yaml`.
`prompt-session-intake` may capture a safe project-intent projection for an
already-bound session, but it never selects or invokes Task Implementer.
That durable project-intent projection excludes shell/tool control text.

## Public Interface

Expose exactly these actions:

```text
$task-implementer workspace init [project-folder]
$task-implementer workspace reuse [project-folder]
$task-implementer run <prompt-ref-or-file>
$task-implementer integrate [project-folder]
$task-implementer workspace remove [project-folder]
```

- `workspace init` initializes or verifies the exact project workspace.
- `workspace reuse` reopens an existing verified workspace without repair or
  lane mutation.
- `run` accepts one exact managed prompt reference, ID, filename, or path.
- `integrate` consumes completed lane generations into the recorded source
  branch after exact validation.
- `workspace remove` removes only an idle, clean, fully integrated lane and
  preserves private prompt/run history.
- Never require a user to provide run, wave, task, branch, worktree, lifecycle,
  or private-state identifiers.
- Do not add compatibility aliases or public internal transitions.

## When To Use

- The user explicitly invokes one of the five actions.
- Durable steering, dependency ordering, isolated worker commits, combined
  validation, and resumable integration materially help the request.
- Parallel tasks have complete and disjoint write ownership.

## When Not To Use

- Do not invoke it implicitly for a complex task or generic parallel request.
- Do not use it for one-shot implementation, Agentic SDLC, design-only work,
  standalone review/commit/PR/merge, release, or publication.
- Do not dispatch parallel workers when dependencies or ownership are unclear.

## Inputs

- One exact explicit public action and selected project folder.
- The accepted immutable prompt reference plus current steering disposition.
- Verified workspace, lane, coordinator, task, assignment, result, journal,
  Git, Worktree, and applicable repository-instruction evidence for the active
  transition.
- Focused code, tests, docs, and validation commands needed by the locked plan.

## Required Reads

- Read `references/prompt-workspace.md` before workspace, prompt, steering,
  queue, state-recovery, or sandbox decisions.
- Read `references/prompt-requirements-refinement.md` before compiling or
  revising accepted intent.
- Read `references/implementation-loop.md` before wave planning, dispatch,
  integration, promotion, cleanup, or finalization.
- Read the bound prompt snapshot, handoff, coordinator, active wave,
  assignments, results, journals, applicable repository instructions, relevant
  code/tests/docs, and current Git state.
- Every worker uses `code-review` and exactly one `$commit`. The coordinator
  uses combined validation, integration `code-review`, and final `$align`.

## Writes

- Owner-only prompt, plan, queue, coordinator, task-plane, assignment, result,
  evidence, lease, and recovery records under the verified private workspace.
- Exact registered lane, worker, and integration worktrees plus journaled Git
  refs and commits owned by the active run.
- Coordinator-owned requirements, design, documentation, and changelog deltas
  only through the exact reviewed integration path.
- Never lifecycle state, project-instruction state, unrelated source dirt, or
  unregistered external systems.

## Canonical Project-Spec Contract

- The root coordinator classifies every direct root-user statement and uses
  `maintain-project-specs` as the single parser, paired publisher, and receipt
  owner. Durable intent is reconciled into requirements and a ready design
  before the implementation plan locks.
- Missing or invalid canonical specs block only the spec-dependent plan lock or
  publication that cannot be performed safely. They never deny tools, Stop,
  cleanup, session completion, or independent recovery work.
- Spec status and receipts never authorize or prevent a Task Implementer
  transition.
- Task Implementer owns prompt-impact claims, receipts, plan settlement, and
  resource gates under its private run state. It binds the exact root-intent
  digest and project-spec receipt as plan evidence, not workflow authority.
- Workers inherit the root-intent digest and exact project-spec receipt. They
  never reclassify direct user intent or edit canonical specs; discoveries are
  returned as typed `spec_gaps` for coordinator reconciliation and replanning.
  Enumerate dirty and committed worker paths with rename folding disabled, and
  reject sensitive text in every gap summary/evidence item before persistence.
- Do not expose or call a lifecycle authorization bridge.
- Do not require project-instruction inspection, rendering, mutation,
  verification, reload, or sealing as part of a run. Read every already-
  effective `AGENTS.md` in the applicable instruction chain.
- A separately and explicitly invoked project-instruction workflow owns its
  mutations and safety checks. Its result may inform a later run but cannot
  become a prerequisite or terminal mutation for the current run.
- The project-instruction workflow remains separately responsible for every
  requested repository mutation and reload decision.
- Historical lifecycle evidence may remain readable for diagnosis. Ignore it
  for active decisions; do not reconstruct, migrate, or refresh it
  automatically.

## Private State

Keep prompts, immutable revisions, queues, coordinator state, assignments,
results, journals, validation evidence, and summaries under the verified
private Task Implementer root outside Git. Files are owner-only, bounded,
regular, non-symlinked, and atomically written. Never persist prompt bodies,
diff bodies, secrets, private endpoints, raw logs, or credentials in public
status or repository artifacts.

## Process

### Workspace initialization and reuse

1. Resolve the exact Git common directory, primary checkout, named non-default
   source branch, selected project scope, and applicable instructions.
2. Initialize or verify the Worktree-owned persistent full-repository lane and
   generated prompt workspace. Preserve unrelated source-checkout dirt; never
   copy it into the lane.
3. Treat generated workspace mismatch as tampering unless the existing narrow
   executable-relocation repair contract applies to `workspace init` or `run`.
   `workspace reuse` is strictly non-mutating.
4. Return only public-safe paths and observed status.

### Run intake and planning

1. Bind one accepted prompt revision and one active lane generation under the
   scope and execution locks. Queue a different explicit prompt FIFO when
   another run is active.
2. Compile the prompt into a complete Task Implementer prompt-impact claim and
   stable clarification IDs. Classify statements, then publish the canonical
   requirements/design pair through `maintain-project-specs`. Material
   ambiguity blocks planning; historical lifecycle state does not.
3. Bind the immutable plan to the latest accepted revision, root-intent digest,
   current canonical v2 spec receipt and bytes, task IDs, dependencies,
   exact/prefix write claims, conflict domains, validation commands, and done
   criteria.
4. Review a resource-free dirty lane completely before the built-in checkpoint
   transaction. Preserve unrelated, sensitive, unsupported, or ambiguous
   changes as a blocker. Never reset, stash, clean, amend, unstage, or manually
   commit the managed lane.
5. Reconcile steering only at a proven resource-safe boundary. Material change
   requires a distinct plan identity; never rebind unchanged plan bytes.

### Dependency waves

Wave states are:

```text
planned -> preparing -> running -> integrating -> promotion_pending -> promoted -> cleanup -> done|blocked
```

Task states are:

```text
planned -> assigned -> running -> committed -> merged|failed
```

For each wave:

1. Require the persistent lane to be clean at its recorded branch and `HEAD`.
2. Journal resource intent; create one locked full-repository integration
   worktree from that exact commit; re-observe Git identity.
3. Create immutable assignments only for the active capacity batch. Each
   assignment binds absolute scope cwd, base commit, helper digest, task
   digest, root-intent digest, canonical project-spec receipt, claims, domains,
   validation, criteria, guardrails, and predecessor evidence.
4. Dispatch one fresh worker per assignment. A worker may mutate only its own
   registered worktree and claims, validates its task, performs `code-review`,
   invokes `$commit` exactly once, and publishes immutable result evidence.
   Its result always contains `spec_gaps`; a non-empty list requires
   `REPLAN_REQUIRED`.
5. The coordinator independently verifies assignment, worker session, commit
   ancestry/tree/paths, result digest, and validation evidence. It never
   implements a worker task.
6. Merge verified worker commits into the integration branch in stable task-ID
   order with `git merge --no-ff --no-edit`. Never cherry-pick, rebase, squash,
   or push internal branches.
7. Apply coordinator-owned requirements/design/docs/changelog reconciliation
   through the exact journaled coordinator commit path. Record implementation
   and verification evidence in the canonical pair after combined proof.
   Product fixes become correction tasks.
8. Run combined validation and integration `code-review`, bind evidence to the
   unchanged integration tip, then remove only verified clean worker resources.
9. Fast-forward the persistent lane under the shared promotion lock after
   exact preconditions and postconditions. Tasks become done only after
   promotion.
10. Reconcile workflow-owned prompt impact, remove the exact integration
    worktree/ref, record retained resources if any, and continue. No project
    lifecycle receipt or project-instruction mutation is part of cleanup.

### Recovery and resume

- Resume from authoritative coordinator-v7, task-plane, journal, Git,
  Worktree, interop, and lease evidence. Handoff Markdown is a projection, not
  authority after coordinator creation.
- Every mutation is intent-journaled and re-observed. Replays reuse only exact
  validated effects and immutable arguments.
- `resume-control-v1` binds controlled transitions and returns `RESUME_STALE`
  before effect when its token or observed basis changed.
- A fresh heartbeat waits. Expired workers or ambiguous prior ownership require
  confirmation. Dirty, divergent, malformed, or unknown-writer state remains
  blocked with resources retained.
- Coordinator v1 through v6 remain unsupported. Do not add a compatibility
  reader, state shim, or inferred migration.
- If promotion reports failure, classify the actual lane `HEAD` as unchanged,
  promoted, or unexpectedly moved before retry.

### Finalization and source integration

1. After the last wave cleanup, run changed-surface `$align` and pass its
   bounded evidence to the private finalizer.
2. Finalization requires every wave done, no retained internal resource, a
   clean lane at the final promoted `HEAD`, current workflow-owned prompt impact,
   and a stable summary projection. It never requires lifecycle seal evidence.
3. Release the immutable generation and activate the unchanged FIFO queue head.
   Repeating an unchanged completed prompt returns the same summary.
4. Public `integrate` requires no active generation, contiguous released
   receipts, and clean exact source/lane identities. Build and validate one
   exact two-parent candidate when histories differ, then advance the source
   ref only by expected-old compare-and-set.
5. Never push, open a PR, publish, or merge remotely without a separate
   authorized workflow.

## Idempotency

- Reopening an unchanged workspace verifies and reuses the same lane without
  repairing or duplicating resources.
- Replayed transitions reuse only exact journaled effects, immutable arguments,
  and current compare-and-set preconditions.
- Repeating a completed prompt returns its existing released summary; it does
  not create another generation, plan, commit, or integration candidate.
- Stale tokens, changed prompt revisions, head drift, partial effects, or
  ambiguous ownership stop before effect and route through owned recovery.

## Failure Handling

- Worker, merge, validation, review, steering, promotion, cleanup, or source
  integration failures leave the last proven branch head unchanged and retain
  exact recovery resources.
- Use precise codes such as `WORKSPACE_BUSY`, `REPLAN_REQUIRED`,
  `WORKTREE_CONFLICT`, `WORKER_SCOPE_VIOLATION`, `VALIDATION_FAILED`,
  `PROMOTION_BLOCKED`, and `CLEANUP_BLOCKED`.
- Lifecycle advisory failure is never converted into one of these workflow
  blockers.
- Do not force-remove worktrees, force-delete refs, initialize submodules,
  widen permissions, or perform external writes to make recovery pass.

## Must Not

- Expose internal IDs or transitions in user commands or status.
- Let workers touch the primary checkout, other worktrees/refs, shared
  coordinator files, undeclared paths, Git maintenance, or external systems.
- Run overlapping tasks in parallel.
- Trust PID, timestamp, Markdown, or lifecycle state as proof that a writer is
  quiescent.
- Persist or print secrets, private identities, raw diffs, or private paths.

## Completion Criteria

- The public interface remains exactly five explicit-only actions.
- Every task has deterministic dependencies and locked ownership.
- Every promoted task has one verified worker commit plus combined validation
  and review evidence.
- Cleanup leaves no unreported temporary resource.
- Final `$align` passes; the generation is released from a clean exact lane
  head without lifecycle gating.
- Public integration, when requested, advances only the recorded source ref
  through an exact validated candidate.

## Output Contract

Return the versioned public run summary or one precise blocker and next public
action. Status is strictly read-only, double-observed, and identity-safe. Do
not expose prompt content, lifecycle/private state, raw logs, branches,
commits, or internal paths.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings in the
narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## References

- `references/prompt-workspace.md`
- `references/prompt-requirements-refinement.md`
- `references/implementation-loop.md`
- `assets/handoff-template.md`
