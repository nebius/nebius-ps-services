---
name: implement
description: "Use only when explicitly invoked to implement the current plan or an inline request with isolated task workers, verified local delivery and durable continuation. Use continue for a new session; not SDLC or remote publication."
disable-model-invocation: true
---

# Implement

## Help

For `$implement --help` or `$implement -h` (including native Claude forms), return concise help
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

## Agent Compatibility

Read `../global-context-management/references/agent-hosts.md` before workflow
state. Use `$implement` in Codex, `/implement` in Claude Code, or
`/skills:implement` in the Claude plugin. Preserve native identity, permissions
and hooks. Missing host capabilities require an explicit limitation.

## Purpose

Accept a conversation objective, freeze a reviewed execution basis, dispatch
isolated task workers, validate their results, and deliver verified local work.
Global Context Management owns context, delegation and continuity. Maintain
Project Specs owns canonical interpretation/publication. This coordinator owns
objectives, planning, scheduling, execution evidence and completion.

## Invocation Policy

Explicit user invocation is required. Keep `allow_implicit_invocation: false`.
Neither hooks nor another coordinator implicitly starts this workflow.

## When To Use

- The user explicitly requests implementation of the current unambiguous plan
  or an inline objective with isolated workers and verified local delivery.
- The user explicitly requests continuation of an existing Implement run.

## When Not To Use

- Do not start implicitly, nest inside Agentic SDLC, or use for remote publication.
- Use `implement-test` when the user requests verification of this workflow.

## Inputs

- The current unambiguous plan, an inline implementation request, or a validated
  active run binding; cross-session resumption requires `continue`.
- The current project by default, or the project folder supplied to `continue`.
- Accepted scope, constraints, acceptance criteria, and clarifications.

## Public Interface

```text
$implement
$implement <implementation request>
$implement continue [project-folder]
$implement -h
$implement --help
```

- Bare invocation consumes the current unambiguous plan or this session's bound
  active run. If neither exists, ask for the intended outcome before execution.
- An inline request supplies the objective; the exact current project is default.
- `continue` resumes an existing run in this or the specified project folder.
  A new session must use it. Resolve ambiguous choices by project and outcome.
- `-h, --help` returns report-only help. No additional public flags.
- Workspace, prompt-file run, integrate and remove actions are retired. Reject
  them; do not interpret retired syntax as an implementation objective.

## Required Reads

- Applicable instructions, the current plan/request and relevant source/tests.
- `references/implementation-loop.md` and `references/objective-contract.md`.
- The exact validated run binding, objective revision, checkpoint, immutable
  basis, plan, assignments, results and recovery journal before continuation.
- Canonical specs when they apply. Do not bootstrap documents for a justified
  execution-only or no-effect objective.

## Writes

Only normalized objectives, immutable basis/plan/evidence and run-owned execution
state under `<agent-home>/implement/`, plus registered integration/task worktrees.
Preserve unrelated dirty content and old private histories/worktrees. Never store
raw turns, prompt archives, secrets, credentials or private endpoints in artifacts.

## Process

1. Resolve the exact project, Git common directory, committed HEAD and named
   source branch. Reject detached/unborn or unsupported state before execution.
   Confirm native permission for the workflow's Git metadata and private-state
   writes before editing specs. If unavailable, report `ENVIRONMENT_BLOCKER`;
   never bypass native controls or imply that workspace file writes allow Git.
   Inspect source dirt: execute committed history and block if the task depends
   on uncommitted content. Never copy source dirt into a worker.
2. Normalize accepted outcome, scope, constraints, acceptance criteria,
   clarifications and provenance. Use the shared private `workflow_intake.py`
   owner `implement` to accept, observe or explicitly continue. Do not invent
   session identity or recover ownership from the sole active run.
   For a new run, call shared `workflow_transition.py prepare-specs` with the
   run, selected project and internal `FEAT-001` identity. This allocates the
   single integration checkout before plan/basis creation. Keep the accepted
   source HEAD and its files unchanged; canonical specs are implicit committed
   inputs and cannot be excluded as unrelated dirt.
3. Classify specification impact through Maintain Project Specs. Publish initial
   changes only in that registered integration checkout, through its paired
   transaction with the accepted objective digest and no basis digest yet.
   If the pair introduces new files, stage from the integration Git root with
   `git add -A` after verifying that only the canonical pair changed, so impact
   validation can inspect tracked specs. Never force-add ignored canonical files.
   Record complete statement dispositions, including execution-only/no-effect.
4. Lock a complete dependency plan with ownership and validation. Before locking
   IDs or dispatching, audit every task against the accepted objective and
   applicable specs. Copy its concrete interfaces, data shapes, fixed values,
   constraints, required inputs and acceptance checks into its Goal, Validation
   and Done criteria. Requirement IDs or references to the full objective are
   not sufficient assignment context. Resolve missing inputs before dispatch;
   a worker must be able to implement its slice from its assignment and the
   permitted repository context. Use the
   shared Worktree execution helper directly, with internal execution identity
   `FEAT-001` for this run. Never invoke Agentic SDLC as a nested coordinator.
   Shared `prepare` now commits only the owner-published initial spec pair,
   validates its journaled direct-child tree, and freezes execution in the same
   integration checkout. Do not commit initial specs in the source checkout or
   use `seal-tdd` to reopen an already sealed Implement base.
5. Freeze objective, applicable spec receipt, plan, committed base, effective
   instructions/helper identities, assignments, ownership and validation. All
   effect boundaries reject stale bases. Record observed native capacity;
   default at most four, capped by native slots, host/configuration limits and
   independent tasks. Missing capacity evidence uses one isolated worker.
6. Prepare a deterministic ready queue within the current dependency wave.
   Each worker uses the same frozen wave base and a fresh worktree and native
   identity. Give assignment-only context, including paths, validation and
   instructions. Arm only available slots. Task-start is the first transition
   after immediate Git identity inspection.
7. Workers implement only their claims, validate and use code-review. After
   native termination, the coordinator finishes through the shared owner
   transaction for one direct-child commit. They do
   not publish specs or touch coordinator/other worktrees. Return typed spec
   gaps for root reconciliation. Observe liveness and interrupt failed/stalled
   workers; never infer progress from background heartbeat loops.
8. Independently validate identity, assignment/basis, ancestry, changed paths,
   native termination and result evidence. Admit a result only when its writer
   is quiescent. Refill the free slot immediately from the ready queue. Shared
   files, lockfiles, outputs, ports, databases and live targets are conflict
   resources; shared runtime access requires an exclusive assignment lease.
9. After every task in the wave is admitted, integrate in stable task order,
   run combined validation and code-review, and clean exact worker resources.
   Product defects become correction tasks. Keep the run integration tree for
   the next dependency wave.
10. Reconcile coordinator-owned specs/docs/changelog, record separate
    implementation and verification evidence, and complete `$align` before
    sealing. Deliver automatically to the clean unchanged original non-default
    branch. Source drift retains the verified result and reports the blocker.
    Default/unknown-default starts deliver the verified feature branch and
    checkout while leaving the source branch unchanged; this is local completion.
11. Preserve a managed outer Worktree lease. Release it only after evidence and
    cleanup; return its normal explicit integration handoff. Never appropriate
    the outer owner's branch or cleanup authority.
12. Record completion and checkpoint. Repeated unchanged bound invocation is
    idempotent. No commit/push/PR/release action on an unrelated checkout is
    authorized by this workflow; remote actions require their own authorization.

## Steering And Cancellation

Root acceptance of material steering persists an adjacent objective revision and
fences dispatch/promotion first. Quiesce affected writers, retain evidence and
replan affected work with a distinct immutable basis. Do not rebind old plan
bytes to a material revision. Cancellation similarly fences progress and
reconciles outstanding effects without reversing delivered commits. Unrelated
objectives are not queued.

## Idempotency

Every transition checks durable identity and journaled effects. An unchanged
accepted objective reuses its revision. Recovery re-observes exact Git effects,
never trusts a PID/timestamp alone, and retains ambiguous resources. SessionStart
may observe an existing binding after compaction; it never starts execution.

## Failure Handling

Return a precise blocker such as REPLAN_REQUIRED, WRITERS_ACTIVE,
WORKER_SCOPE_VIOLATION, SOURCE_DRIFT, PROMOTION_BLOCKED or CLEANUP_BLOCKED.
Unsupported old schemas fail with WORKFLOW_UPGRADE_REQUIRED and remain untouched.
Worker failures stop admission; missing runtime evidence remains NOT_RUN/PARTIAL.
Do not reset, stash, force-remove, force-delete or bypass ownership guards.

## Must Not

- Capture raw conversation or maintain managed prompt files, lanes or FIFO queues.
- Expose private IDs as required public arguments, or add retired command aliases.
- Change assignments while affected writers remain active.
- Infer parallel execution from declared tasks or capacity alone.
- Nest coordinators, duplicate Git lifecycle primitives, or bypass native controls.
- Publish canonical specs from workers or mutate old state to make it readable.

## Completion Criteria

The accepted objective is verified against its immutable basis; all task results
are admitted and combined checks/alignment pass. Delivery is the clean original
non-default checkout or retained verified feature checkout. Exact temporary
worker resources are absent; retained deliverables/blockers are explicit. No
old workflow conversion or unrelated mutation occurred.

## Output Contract

Report the outcome, local delivery checkout, actual observed concurrency,
validation, cleanup/retention and any precise blocker. Keep native, deterministic
and application evidence separate; do not expose raw state, secrets or turns.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings in the
narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
