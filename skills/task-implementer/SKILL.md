---
name: task-implementer
description: "Requires explicit invocation for a complex sequential brownfield workflow: initialize one private prompt workspace, then repeatedly run and steer the same private Markdown prompt by path or unique filename. The workflow incrementally maintains managed requirements/design records, claims exactly one task in a durable execution plane, locks planning before implementation, validates/reviews/commits/checkpoints it, and requires a fresh session for the next task. Do not use for ordinary one-shot implementation, Agentic SDLC, chat-only brainstorming, standalone review/commit/PR work, or parallel write agents."
---

# Task Implementer

## Purpose

Coordinate complex brownfield implementation from durable private Markdown
prompts. A user initializes the current project folder once, edits prompts in
the generated `CODE` + `PROMPTS` workspace, and repeatedly invokes the same
prompt. Edits are immutable steering revisions. The Skill derives stable
requirements, maintains lightweight managed regions in project requirements
and design documents, keeps a stable `task-1..task-n` queue, and records
reviewed per-task checkpoints outside Git.

This is a lightweight sequential implementation loop, not Agentic SDLC.

## Invocation Policy

This Skill must be explicitly invoked. Keep
`policy.allow_implicit_invocation: false` in `agents/openai.yaml`.

## Public Interface

Expose exactly these two actions:

```text
$task-implementer workspace init [project-folder]
$task-implementer run <prompt-path-or-unique-filename>
```

- `workspace init` defaults to the exact current directory. A relative or
  absolute project folder resolves to the same canonical workspace.
- `run` accepts one managed absolute prompt path or one filename that is unique
  in the current project's prompt directory.
- Never require the user to supply a prompt ID, run ID, revision, handoff path,
  steering ID, requirement ID, design ID, or internal transition name.
- Do not expose a separate `steer` action. Edit the same prompt and repeat
  `run`.
- Do not expose compatibility aliases for retired actions such as workspace
  creation/listing, preparation, continuation, or reconciliation.

The generated VS Code `Task Implementer: New Prompt` task remains an optional
editor convenience. It creates another managed prompt but never starts Codex or
submits prompt content.

## Inputs

- One explicit public action and its optional project folder or prompt path or
  unique filename.
- The current project, worktree, branch, repository instructions, and exact
  immutable prompt snapshot selected by private state.
- Relevant repository paths, tickets, logs, screenshots, sketches, or design
  notes referenced by the prompt when safe and available.

## When To Use

- The user explicitly invokes one of the two public actions.
- The user explicitly requests this existing sequential brownfield workflow.
- The work benefits from dependency ordering, per-task review and commits, and
  fresh context between tasks.
- A serial multi-layer change should be delivered as ordered vertical slices.

## When Not To Use

- Do not invoke this Skill implicitly because a task is complex or a prompt is
  opened or edited.
- Do not use it for ordinary one-shot implementation, prompt organization,
  Agentic SDLC, chat-only ideation, design-only work, standalone review,
  standalone commit, PR, merge, release, or publication.
- Do not make `global-context-management` automatically invoke it.
- Do not run parallel write-capable agents or overlapping implementation
  sessions in the same scope.

## Required Reads

- Read `references/prompt-workspace.md` before initialization, prompt routing,
  or private-state recovery.
- Read `references/implementation-loop.md` before queue construction,
  reconciliation, or an implementation session.
- Before implementation, read the internal run manifest, exact bound snapshot,
  steering ledger, and complete handoff selected by the helper.
- Inspect project-managed requirements and design documents before planning.
  Fail closed when Agentic SDLC owns either exact path.
- Read relevant `AGENTS.md`, source, tests, README/design docs, changelog, and
  current Git state before queue creation or product edits.
- Use `brainstorm` for source-ranked context and assumption checks when useful;
  keep that pass read-only.
- Use `design` for non-trivial architecture, contract, missing-code, migration,
  security, reliability, or ambiguous-boundary decisions.
- Use `code-review` before a task checkpoint and `$commit` only after validation
  and review gates pass.
- Verify version-sensitive guidance against current official vendor docs.

## Writes

Private state lives outside Git under:

```text
${CODEX_HOME:-$HOME/.codex}/task-implementer/projects/
<project-id>/scopes/<scope-id>/
├── workspace.json
├── activity.json
├── <scope>-prompts.code-workspace
├── prompts/<created-at>--<ask-slug>.md
└── runs/<run-id>/
    ├── manifest.json
    ├── steering.json
    ├── inputs/<revision>/prompt.md
    ├── execution/task-n.json
    └── handoff.md
```

Initialization writes only private workspace metadata and, when needed, one
starter prompt. Running may write immutable revisions and handoff state and may
make only the focused product, test, docs, changelog, config, or generated
artifact changes required by the selected task.

After plan authorization, a task may create or update only Task
Implementer-managed regions in `<project>/docs/requirements.md` and
`<project>/docs/design.md`. Preserve every byte outside those regions and
commit the managed updates with the affected task, never as an extra workflow
commit.

Never commit prompt-workspace state. Never write prompts into the repository.
Never persist secrets, private endpoints, customer data, raw logs, or broad
copied internal documentation.

## Process

### `workspace init [project-folder]`

1. Resolve the installed Skill directory. Resolve the project folder from the
   optional argument or the exact current directory.
2. Invoke internal helper `init` with the project folder. It must canonicalize
   the Git root and exact project scope, create or verify private `CODE` and
   `PROMPTS` roots, repair generated workspace metadata, and preserve prompts,
   revisions, runs, and handoffs.
3. Under the scope lock, create exactly one starter prompt only when the prompt
   directory has no Markdown prompt. Repeated initialization must not duplicate
   it.
4. Open the generated VS Code workspace when VS Code is available. Failure to
   open the editor must not invalidate successful initialization.
5. Return workspace paths and a prompt table sorted newest-first by private
   activity. Include only last invocation, status, title, and path.
6. Stop. Initialization never queues or implements work.

### `run <prompt-path-or-unique-filename>`

1. Resolve the current project workspace, then invoke internal helper `intake`
   with the current project and supplied prompt reference.
2. Before any state change, require an initialized workspace and a regular,
   non-symlink Markdown prompt inside its managed prompt root. Reject traversal,
   foreign paths, ambiguous filenames, invalid content, unsafe permissions, and
   malformed state.
3. Acquire the scope lock. If another prompt owns unfinished work, fail closed
   with its prompt path, never an internal ID. Rejected or lock-busy calls must
   not change activity ordering.
4. Follow the helper's private route:
   - `new`: snapshot the validated prompt, inspect the project, and build the
     full dependency-ready queue before claiming `task-1` for planning.
   - `continue`: verify current evidence and claim exactly the next
     dependency-ready task. If interrupted, recover the same task or reconstruct
     a missing checkpoint only from verified commit evidence.
   - `reconcile`: compare the new immutable revision with the bound revision,
     normalize requirement changes first, preserve completed work and stable
     IDs, supersede changed pending tasks, append replacements, then claim
     exactly the next safe task.
   - `reconcile_planning`: when the same session owns a clean planning plane,
     rebind that unfinished task, resolve the steering revision, and replan it
     before authorization.
   - `steering_queued`: keep the active planning or implementation plane bound
     and locked, return `STEERING_QUEUED_AFTER_TASK`, and apply the revision
     after the task stops in the next fresh session.
   - `done`: return `ALREADY_COMPLETE` without product changes.
5. Record each accepted revision once with `pending`, `applied`, `blocked`, or
   `no_effect` disposition. Formatting-only revisions may be bound as
   `no_effect` but may not change tasks, documents, or commits.
6. For ambiguous, destructive, or irreversible steering, stop with
   `HUMAN_INPUT_REQUIRED`. Clear corrections append requirements and corrective
   tasks instead of rewriting completed evidence.
7. A completed edited prompt starts a new internal run and implements its first
   task. A completed unchanged prompt remains a no-op.
8. Update the private handoff's `Last invoked at` for every validated,
   lock-acquired invocation, including blocked, reconciliation, continuation,
   and completed-no-op outcomes. Never rename, rewrite, or deliberately touch
   the editable prompt for ordering.
9. Return the prompt table newest-first plus the user-visible outcome. Do not
   print prompt bodies or internal IDs.

## Queue And Per-Task Gate

For a new internal run, inspect the target code before ordering work. Normalize
the snapshot into stable `TI-REQ-nnn` requirements, then create stable
`task-1..task-n` IDs mapped to those requirements. Allocate `TI-DES-nnn` only
when each task is designed. Recover counters from committed managed regions,
never renumber IDs, and prefer vertical end-to-end slices.

Each implementation invocation completes exactly one task through a private
execution plane:

1. Verify canonical scope, manifest, bound digest, handoff, dependencies, Git
   state, and current execution-plane evidence.
2. Invoke private `plane-claim`. The helper selects exactly one
   dependency-ready task, rejects a dirty worktree, records the runtime session
   fingerprint and participant history, captures Git `HEAD` plus the clean
   worktree baseline, marks that task `in_progress`, and locks its phase to
   `planning`. If another session owns a plane, stop with `WORKSPACE_BUSY`.
3. While the plane is `planning`, gather only task-specific context. Use
   `brainstorm` and `design` as routed. Populate Goal, Plan, Likely files,
   Implementation steps, Validation, End-to-end validation, Done criteria,
   Rollback notes, Stop conditions, requirement mappings, the just-in-time
   design record, managed-region proposal, and document-envelope digests in the
   handoff. `Likely files` is the locked allowlist of exact repo-relative paths,
   including both specification documents for spec-aware tasks. Do not edit
   repository files.
4. Invoke private `plane-authorize`. It requires every plan field, verifies the
   worktree still matches the claim-time baseline, hashes the plan and complete
   queue contract, and changes the plane to `implementation`. Product edits are
   forbidden until this transition succeeds.
5. Deterministically create or update the managed requirements/design regions,
   then implement only the selected task. Run private `spec-inspect`, focused
   validation, and relevant end-to-end validation; inspect the scoped diff.
6. Invoke `code-review`, fix safe scoped findings, and revalidate.
7. Invoke `$commit` exactly once only when the repository-wide staged
   checkpoint is safe. More than one post-claim commit fails checkpointing.
8. Populate the task checkpoint, managed-region digests, exact next task,
   repeated `run <prompt>`
   command, and explicit current-session stop instruction. Invoke private
   `plane-checkpoint`; it verifies the locked plan/queue, exact commit message
   and changed-path evidence, current descendant task commit, clean worktree,
   handoff, and next task, then marks exactly that task done and the plane
   `stopped`.
9. Stop the session. Never claim, plan, or implement a second task in it.

After the final task, run changed-surface `$align` or the equivalent local
alignment checklist, record it, set the internal status to `done`, and stop.

## Fresh Session Contract

The Skill cannot reset its own context. The execution plane uses a SHA-256
fingerprint of runtime-provided `CODEX_THREAD_ID` as a cooperative correlation
guard, not as a cryptographic identity. After each checkpoint, save the handoff
and stop. Every fingerprint that participated in a completed task receives
`FRESH_SESSION_REQUIRED` for every other task in the scope. In a fresh
interactive session or new `codex exec` process, use:

```text
$task-implementer run <same-prompt-path-or-unique-filename>
```

Do not use `codex exec resume` for normal next-task handoff. Do not launch a new
Codex process automatically. If sandbox access to private state is missing,
preserve the current policy and report:

```bash
codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"
```

## Idempotency

- Prompt filenames, bytes, and workflow-managed mtimes stay unchanged.
- A filename date is creation metadata only. Submission ordering comes from
  private `last_invoked_at` in mutable handoff/activity state.
- Sort by `last_invoked_at`, then prompt creation time and path for deterministic
  ties. Draft prompts without runs use creation time.
- Manifests keep append-only immutable revisions; handoffs own mutable state.
- Steering ledgers keep ordered dispositions. A repeated latest digest creates
  no revision; A-B-A edits remain three historical states when each adjacent
  digest differs.
- Managed requirements/design IDs and change-log entries are stable. Removed
  requirements become `superseded`; completed design history is never deleted.
- Preserve stopped checkpoint next-task advice. When steering changes the
  selection, append one revision-and-digest-bound reconciliation override.
- One task plane moves `planning -> implementation -> stopped`; authorization
  binds the exact plan digest and checkpointing completes at most that task.
- Post-implementation review/commit/evidence fields remain writable while the
  planned task and queue contract stay locked. Stopping binds an immutable
  completed-task/checkpoint digest. Before any later claim or checkpoint,
  revalidate every stopped plane and select the predecessor from
  `Run.Last completed task`, not timestamp ordering.
- Enforce a one-to-one index between every `done` task, populated checkpoint,
  and stopped plane. Only the currently active implementation task may be the
  temporary non-stopped exception.
- A same-session claim is idempotent for the active task. A session fingerprint
  used by any completed task can never claim a different task, even after an
  intervening fresh session.
- Verified recovery transfers the existing task plane to the new trusted
  session without selecting another task or changing the locked plan. Require
  explicit confirmation that the prior session has stopped before using the
  private recovery transition; otherwise return `HUMAN_INPUT_REQUIRED`.
- Never duplicate a revision, task edit, or commit on retry.
- Never renumber task IDs or rewrite completed task records.
- Verify completed-task and recorded-commit evidence before advancing.
- Keep one scope-wide writer and exactly one active task.

## Failure Handling

Preserve stable helper tokens from `references/prompt-workspace.md`. Also use:

- `WORKSPACE_BUSY` when another transition holds the scope lock.
- `RUN_STATE_INVALID` when private run, manifest, revision, digest, or handoff
  state is unsafe.
- `EXECUTION_STATE_INVALID` for malformed task queue or execution-plane state.
- `SESSION_ID_UNAVAILABLE` when no runtime session identifier can enforce the
  cooperative fresh-session boundary.
- `PLAN_REQUIRED` when planning evidence is incomplete.
- `PLAN_LOCKED` when a plan or queue changes after implementation authorization.
- `CHECKPOINT_REQUIRED` when validation, review, commit, next-task, or stop
  evidence is incomplete.
- `FRESH_SESSION_REQUIRED` when the completed task's session attempts to claim
  the next task.
- `DESIGN_GAP` or `CONTEXT_GAP` for unresolved decisions or evidence.
- `IMPLEMENTATION_DEFECT`, `TEST_DEFECT`, or `VALIDATION_DEFECT` for incomplete
  active-task work.
- `REVIEW_BLOCKER` for unresolved review findings.
- `COMMIT_BLOCKER` or `WORKTREE_CONFLICT` when the checkpoint is unsafe.
- `ENVIRONMENT_BLOCKER` for unavailable tools, credentials, services, network,
  sandbox access, or permissions.
- `HUMAN_INPUT_REQUIRED` when a consequential reconciliation or implementation
  decision is unsafe to infer.
- `STEERING_QUEUED_AFTER_TASK` when accepted steering must wait for the active
  plane to stop.
- `SPEC_OWNER_CONFLICT` when Agentic SDLC owns a specification document.
- `SPEC_CONFLICT` for unsafe paths, markers, IDs, mappings, private-state
  exposure, managed-region drift, or changed user-owned envelopes.

Retry only when the next transition is clear and scoped. Never silently repair
malformed private state.

## Must Not

- Do not expose or require internal prompt IDs, run IDs, revisions, transition
  names, snapshot paths, or handoff paths in user commands.
- Do not restore retired public commands, flags, aliases, or compatibility
  shims.
- Do not add a public `steer` command or require users to annotate steering
  with IDs or timestamps.
- Do not edit editable source prompts, print prompt bodies, or treat SHA-256 as
  encryption.
- Do not edit product files while the execution plane is `planning`, bypass
  `plane-authorize`, change a locked plan, or use one session for two tasks.
- Do not create task files, prompt bundles, database state, or repository-local
  prompt storage.
- Do not run overlapping writers or parallel implementation sessions.
- Do not bypass review, hand-commit, push, open a PR, merge, publish, or perform
  live external writes without a separately authorized workflow.

## Completion Criteria

- Initialization reports verified workspace paths and submission-ordered prompt
  metadata without changing Git state.
- Each accepted run reports its prompt, last invocation, status, title, and
  path; it claims, plans, authorizes, and advances exactly one task unless
  blocked or already done.
- Every completed task has a stopped execution plane, verified checkpoint, and
  explicit current-session stop; unfinished work names the next fresh-session
  command.
- Every new task maps to stable requirements and a just-in-time design record;
  committed managed-region digests and user-owned envelopes verify at its
  checkpoint.
- The final handoff records all task evidence and changed-surface alignment.

## Output Contract

- Return the action and exact project scope handled.
- For initialization, return the private workspace paths and prompt metadata.
- For running, return the prompt activity/status metadata and one task
  checkpoint, `ALREADY_COMPLETE`, or a blocker.
- On failure, return a stable classification and the minimum user action, never
  prompt bodies or internal IDs.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings in the
narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
