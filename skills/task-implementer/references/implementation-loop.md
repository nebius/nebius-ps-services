# Implementation Loop

Use this reference for queue construction, automatic reconciliation, per-task
gates, interruption recovery, and fresh-session handoff. Use
`prompt-workspace.md` for private storage, validation, snapshot, activity, and
routing mechanics. Keep this workflow smaller than Agentic SDLC.

## Source Order

1. The exact immutable prompt revision bound in the private handoff, including
   constraints, non-goals, acceptance criteria, verification, references, and
   steering. Never use the editable prompt as execution truth after
   snapshotting.
2. Current repository instructions such as `AGENTS.md`.
3. Validated managed regions in project `docs/requirements.md` and
   `docs/design.md`, followed by current code, tests, other docs, changelog,
   generated artifacts, Git state, and focused local command output.
4. Related skills. Use `brainstorm` for source-ranked context and assumption
   checks, `design` for architecture or contract choices, `code-review` as the
   implementation gate, and `$commit` as the local checkpoint gate.
5. Current official vendor documentation for version-sensitive behavior.

If repository evidence changes the prompt's proposed ordering, follow the
evidence and record the reason. Do not silently broaden the requested outcome.

## First Invocation And Queue Construction

The first accepted `run <prompt>` invocation normalizes requirements, performs
queue construction,
claims `task-1` for planning, authorizes its locked plan, and implements that
task; it does not stop for a separate user approval command.

Build a dependency-first queue in the handoff:

- `task-1`: first prerequisite or highest-priority independent deliverable.
- `task-2`: next deliverable whose dependencies can be satisfied.
- `task-n`: final integration or cleanup only when it is a real work item.

Each task has one coherent observable result. It may include source, tests,
docs, changelog, config, and validation when they belong to the same behavior.
For serial multi-layer applications, prefer vertical tasks over horizontal
layers. Use a foundation task only when it unblocks later slices, such as a
schema contract, authentication, migration, shared test harness, or safety
preflight.

Avoid ceremony-only tasks such as "inspect code" or "run tests". Those are
gates inside a task. Do not split merely because multiple files are involved.
One prompt may produce many tasks; never create separate task files.

Every queued task records:

- stable internal task ID and status
- source revision and relevant prompt headings
- stable `TI-REQ-nnn` mappings; every active requirement maps to a task or an
  explicit open question
- priority, dependencies, goal, rationale, and done criteria
- likely files and vertical slice or layers covered
- brainstorm/context and design needs
- a `TI-DES-nnn` record when that task becomes active
- implementation plan outline and rollback notes
- focused and end-to-end validation targets

After writing and verifying the queue, claim exactly `task-1` in the private
execution plane. Planning and implementation remain separate enforced phases.

## Incremental Requirements And Design

Keep the committed artifacts lightweight and deterministic:

- Normalize the complete desired-state prompt and each accepted revision delta
  into requirements before creating or reconciling tasks. The helper validates
  structured IDs and mappings; the agent owns semantic classification.
- Allocate IDs from the maximum committed Task Implementer-managed ID. Never reuse
  or renumber IDs, and never ask the user to supply them.
- Update compatible requirements in place, append new IDs, and mark removals
  `superseded`. Preserve a short semantic change log without prompt bodies,
  paths, internal revisions, digests, secrets, or raw logs.
- Design just in time for the selected task. Every task gets a compact
  `TI-DES-nnn` record; invoke the full `design` skill only for non-trivial
  choices. An implemented design is historical evidence, so later reversals
  append corrective records.
- Keep proposed requirements/design text and the pre-authorization user-owned
  envelope digests in the locked task plan. Do not write repository documents
  before `plane-authorize`.
- After authorization, create missing documents from
  `assets/requirements-managed-region.md` and
  `assets/design-managed-region.md`, append to generic documents, or replace
  only an existing managed body. Run private `spec-inspect` before commit.
- Commit specification changes with the affected task. When only committed
  specification truth changes, create a real docs-only task and checkpoint.

## Execution Plane State Machine

Every task owns one private `execution/task-n.json` record guarded by the scope
lock. Store only SHA-256 fingerprints of runtime-provided `CODEX_THREAD_ID`,
never the raw identifier. This is a cooperative correlation guard, not an
authenticated security identity.

```text
unclaimed -> planning -> implementation -> stopped
```

- `plane-claim` selects the first dependency-ready pending task, requires a
  clean Git worktree, records `HEAD`, the worktree baseline, bound revision,
  and session-participant history, marks the task `in_progress`, and locks
  ownership to the current runtime session. The same session may retry that
  task idempotently. Another session receives
  `WORKSPACE_BUSY` unless the Skill explicitly performs verified recovery of
  the same task.
- `plane-authorize` is the only transition into `implementation`. It requires
  complete plan/specification fields, resolved steering, and an unchanged Git
  status fingerprint, then binds SHA-256 digests of the plan and queue
  contract. Post-implementation review, commit, changed-file, evidence, and
  blocker fields are normalized out of that lock so they can be populated
  after implementation. No product edit is allowed before authorization.
- `plane-replan` may rebind only the same session's clean planning plane to the
  latest pending steering revision. It keeps the same task and claim baseline,
  clears required plan fields, and cannot authorize until reconciliation marks
  every processed steering event `applied`, `blocked`, or `no_effect`.
- `plane-checkpoint` verifies the plan/queue digests, exact commit message and
  changed-path evidence, managed specification IDs/digests/envelopes, task
  checkpoint, validation, review, current commit descended from claim-time
  `HEAD`, clean worktree, next task, repeated run command, and explicit stop
  instruction. It completes at most the claimed task and marks its plane
  `stopped`.
- Stopping binds a completed-task/checkpoint digest. A later reconciled prompt
  may change only pending work; every later claim and checkpoint revalidates
  every historical completed digest and commit before it advances. Resolve the
  immediate predecessor from `Run.Last completed task`, never timestamp
  ordering.
- The execution index is bijective: every `done` task has exactly one populated
  checkpoint and one stopped plane. Only the currently active implementation
  task may temporarily have a checkpoint before its plane stops.
- A stopped plane prevents every session fingerprint that participated in that
  task from claiming any other task in the scope. A distinct runtime session
  must read the handoff and claim again in `planning`.
- Steering submitted during `implementation` never changes the active plane,
  handoff binding, plan, or queue. The old revision checkpoints normally; a
  fresh session reconciles the ordered pending revisions before the next claim.

The scope filesystem lock serializes each state transition. Ordered atomic
writes plus retry validation converge partial transitions. The durable plane
extends exclusive ownership across the product-edit interval after the lock is
released.

## Per-Task Context, Design, And Plan

After `plane-claim` and before any product edit:

1. Read the bound snapshot, complete handoff, and current repository evidence.
   Gather task-specific context with targeted reads. Use `brainstorm` when the
   task needs source-ranked context, tradeoff framing, or assumption challenges.
   Keep it read-only and summarize only recommendation-changing findings.
2. Route to `design` when the task is non-trivial, changes contracts, crosses
   ownership boundaries, has missing code, has ambiguous behavior, or has
   multiple plausible implementations. If needed but unavailable, record a
   compact local design note marked `design_skill_unavailable`.
3. Create a short implementation plan in the handoff: exact steps, an allowlist
   of exact repo-relative files (one per line), vertical slice or layers,
   docs/changelog impact, focused and
   end-to-end validation, stop conditions, rollback notes, and review/commit
   gates.
4. Populate Goal, Plan, Likely files, Implementation steps, Validation,
   End-to-end validation, Done criteria, Rollback notes, Stop conditions,
   Requirement IDs, Design ID, Requirements proposal, Design record, and both
   document-envelope digests. Include both managed document paths in Likely
   files. These fields may not be empty or deferred for new tasks.
5. Invoke `plane-authorize`. If the worktree changed after the planning claim,
   stop with `WORKTREE_CONFLICT`; do not absorb those edits into the plan by
   inference.
6. Implement only after the helper returns phase `implementation`. Render the
   managed documents first so interruption recovery converges on the same IDs
   and content.

## One-Task Completion Gate

Every accepted implementation invocation completes exactly one task:

1. Verify project/scope identity, manifest, bound revision and digest, handoff,
   dependencies, runtime session, and execution-plane ownership.
2. Claim the dependency-ready task in `planning`.
3. Complete the Per-Task Context, Design, And Plan gate and authorize its plan.
4. Render and inspect the authorized managed requirements/design regions, then
   implement only the active task in phase `implementation`.
5. Run `spec-inspect`, focused validation, and the task's relevant end-to-end
   checks.
6. Inspect the diff and remove unrelated cleanup.
7. Use `code-review` on the task changes.
8. Fix safe scoped findings, re-run validation, and refresh review after
   material fixes.
9. Use `$commit` exactly once to create the local task checkpoint. The helper
   rejects multiple post-claim commits.
10. Update the handoff with context, design, plan, changed files,
   validation, review, fixes, commit hash/message or blocker, residual risks,
   managed-region digests, checkpoint, last invocation, and exact next-session
   command.
11. Invoke `plane-checkpoint`. It verifies and atomically records task
    completion, the next task, and the mandatory stop boundary.
12. Stop this session. The helper rejects a next-task claim from the same
    runtime session fingerprint with `FRESH_SESSION_REQUIRED`.

If review finds an unresolvable scoped blocker, mark the task `blocked` with
`REVIEW_BLOCKER`. If `$commit` cannot safely create the checkpoint, use
`COMMIT_BLOCKER` or `WORKTREE_CONFLICT`.

Because `$commit` stages the complete repository diff from the Git root, do not
invoke it when unrelated user changes are present and are not intentionally
part of the active task or prior recorded checkpoints. Stop rather than create
a mixed commit.

## Automatic Reconciliation

When an unfinished prompt's bytes differ from its bound immutable revision,
the same `run <prompt>` invocation performs an internal reconciliation before
selecting a task. This is not a separate public action.

1. Acquire the scope lock and inspect the authoritative active execution plane.
2. Append the edited prompt exactly once when its bytes differ from the latest
   revision. Record a pending steering event. Reuse an existing latest revision
   on unchanged retry; allow later different revisions while earlier events are
   pending.
3. If the same session owns a clean planning plane, rebind only that same task
   and clear its unfinished plan. If another session owns planning or any plane
   is implementing, return `STEERING_QUEUED_AFTER_TASK` without changing its
   execution contract.
4. At a safe boundary, process pending revisions in order. Compare the complete
   desired state and each delta, classifying requirements, constraints,
   acceptance criteria, design constraints, priorities, removals,
   clarifications, and corrections.
5. Reconcile stable requirements before tasks. Preserve completed tasks and
   designs exactly. Preserve an unchanged pending task; otherwise mark it
   `superseded` and append replacements with the next unused IDs.
6. Clear corrections to completed work append corrective requirements/tasks.
   Ambiguous, destructive, or irreversible reversals become `blocked` and
   return `HUMAN_INPUT_REQUIRED` before repository edits.
7. Bind the latest revision and record each processed disposition. A
   formatting-only revision becomes `no_effect` and changes no requirement,
   design, task, repository file, or commit.
8. When reconciliation changes a stopped checkpoint's recommended next task,
   preserve the checkpoint and append exactly one override in this form:

   ```text
   <predecessor> | <old-next> -> <new-next> | <revision> | <bound-sha256>
   ```

9. Select the dependency-ready task and complete the one-task gate.

Never overwrite a snapshot, discard a steering event, renumber an ID, or
change completed task/checkpoint text.

## Completed Prompts

- An unchanged completed prompt records current invocation activity and returns
  `ALREADY_COMPLETE` without product edits or a new internal run.
- An edited completed prompt creates a new internal run linked to the same
  prompt identity, builds a new queue, implements its first task, and preserves
  all historical state.
- Steering accepted while the final task was still implementing remains part
  of that run. After its old-revision checkpoint stops, a fresh session
  reconciles the pending delta and appends corrective or new tasks when needed.

No public new-run flag is needed.

## Interruption Recovery

On retry, verify state before writing:

- Snapshot created, no handoff: reuse the same internal run and revision, build
  the handoff, then claim the first task in planning.
- Handoff created, no scoped edits: recover or resume the same execution plane.
- Scoped edits exist, no commit: inspect them against the active task and
  continue only when ownership and intent are unambiguous.
- Commit exists, checkpoint missing: verify the commit exists, belongs to the
  active task, and matches recorded/scoped evidence; reconstruct the checkpoint
  without creating another commit.
- Reconciliation revision appended, handoff not rebound: reuse the same
  revision and finish reconciliation.
- Steering revision appended before ledger persistence: recover the manifest
  revision once and create its missing pending disposition.
- One managed document rendered before the other: validate both preimages,
  reuse the same stable IDs and locked proposals, and converge without a second
  managed region or change-log entry.
- Specification commit exists, checkpoint missing: read the exact managed
  regions from that commit, verify IDs, mappings, user-owned envelopes, and
  digests, then reconstruct the checkpoint without another commit.

Recovery never selects another task. A different session must call internal
`plane-claim --recover --confirmed-recovery-worktree-sha256 <digest>` only
after explicit confirmation that the prior writer is no longer active and
after reviewing the exact worktree digest and changed paths against the locked
file allowlist. Without digest-bound confirmation, stop with
`HUMAN_INPUT_REQUIRED`.
Planning recovery requires the original Git baseline to remain unchanged.
Implementation recovery verifies and preserves the already locked plan and
records a recovery worktree digest.

Every runtime session fingerprint that participated in a completed task is
permanently ineligible for every other task in that scope. This remains true
after recovery or intervening fresh sessions; an A-B-A task-session sequence
fails with `FRESH_SESSION_REQUIRED`.

Never create duplicate revisions, task IDs, product edits, or commits. If
concurrent ownership cannot be ruled out, stop with `WORKSPACE_BUSY` or
`HUMAN_INPUT_REQUIRED`.

## Handoff Discipline

The private handoff is the durable execution truth between fresh sessions. The
run manifest owns immutable revision metadata only; do not place mutable status
there.

Read the complete handoff before implementation. Keep it concise enough for a
fresh agent. Record summaries, not transcripts:

- internal identity, source path, manifest, bound revision and digest
- `Last invoked at`, revision history, and reconciliation summary
- steering dispositions, requirement/design mappings, open requirements, and
  revision-bound next-task overrides
- queue, active task, completed checkpoints, and overall status
- execution-plane task, phase, plan/queue digests, baseline, participant
  history, recovery count, and stop boundary
- changed files and vertical slice or layers covered
- focused and end-to-end validation results
- code-review decision and scoped fixes
- commit hash/message or exact checkpoint blocker
- committed managed-region digests and `spec-inspect` result
- assumptions, risks, failure classification, and minimum missing input
- next task and exact repeated `run <prompt>` command

Do not store raw logs, prompt bodies, copied docs, credentials, private
endpoints, customer data, or unrelated exploration output.

## Fresh Session Patterns

Interactive:

1. Finish the selected task through validation, review, fixes, and `$commit`.
2. Save the handoff checkpoint, pass `plane-checkpoint`, and stop.
3. Start a fresh Codex session from the same project folder.
4. Invoke `$task-implementer run <same-prompt-path-or-unique-filename>`. The
   new runtime session claims the next task back in `planning`.

Noninteractive:

1. Claim, plan-authorize, and finish exactly one task in one `codex exec`
   process.
2. Save the reviewed and committed checkpoint, pass `plane-checkpoint`, then
   let the process exit.
3. Start a new process for the next task with the same project directory and
   prompt reference. Do not pass an internal ID.
4. Do not use `codex exec resume` for normal handoff.

Example when the private root needs write access:

```bash
codex --ask-for-approval never exec \
  --cd <project-folder> \
  --sandbox workspace-write \
  --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer" \
  'Use $task-implementer run <same-prompt-file>. Implement exactly one task, update the handoff, and stop.'
```

Do not let the helper or VS Code launch this command. Use one implementation
process at a time.

## Final Alignment

After the last task, align the changed surface:

- code and module wiring
- tests and fixtures
- CLI/help or API behavior
- docs, README, design docs, and changelog
- config, generated artifacts, and CI/workflow surfaces
- prompt-workspace and handoff contracts affected by implementation

Use `$align` when available. Otherwise perform the equivalent local checklist,
record that substitution in the final checkpoint, set status `done`, and stop.
