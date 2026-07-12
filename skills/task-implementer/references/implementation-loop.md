# Implementation Loop

Use this reference for queue construction, per-task gates, reconciliation, and
fresh-session handoff. Use `prompt-workspace.md` for private storage, prompt
validation, snapshot, and lifecycle mechanics. Keep this workflow smaller than
the Agentic SDLC state machine.

## Source Order

1. The exact immutable prompt revision bound in the handoff, including its
   constraints, non-goals, acceptance criteria, verification, and references.
   Never use the editable prompt as execution truth.
2. Current repository instructions such as `AGENTS.md`.
3. Current code, tests, docs, changelog, generated artifacts, Git state, and
   focused local command output.
4. Related skills. Use `brainstorm` for source-ranked context and assumption
   checks, `design` for architecture or contract choices, `code-review` as the
   implementation gate, and `$commit` as the local checkpoint gate.
5. Current official vendor documentation for version-sensitive products,
   SDKs, CLIs, APIs, frameworks, package managers, cloud behavior, or standards.

If repository evidence changes the prompt's proposed ordering, follow the
evidence and record the reason. Do not silently broaden the requested outcome.

## Queue Construction

Build a dependency-first queue in the handoff:

- `task-1`: first prerequisite or highest-priority independent deliverable.
- `task-2`: next deliverable whose dependencies can be completed first.
- `task-n`: final integration or cleanup only when it is a real work item.

Each task must have one coherent observable result. It may include source,
tests, docs, changelog, config, and validation when those changes belong to the
same behavioral surface.

For serial multi-layer applications, prefer vertical tasks over horizontal
layer tasks. Carry one user-visible or system-visible behavior through the
layers it needs, such as frontend -> API -> service -> database, with its tests
and docs. Use a horizontal foundation task only when it unblocks later slices,
such as schema contracts, auth, migrations, a shared test harness, or a safety
preflight.

Avoid ceremony-only tasks such as "inspect code" or "run tests". Those are
gates inside a task. Do not split merely because multiple files are involved.
Split when independent deliverables, dependency edges, review risks, or
validation checkpoints justify separate commits. One prompt may produce many
tasks; never create separate task files.

Every queued task records:

- stable task ID and status
- source revision and relevant prompt headings
- priority, dependencies, goal, rationale, and done criteria
- likely files and vertical slice or layers covered
- brainstorm/context and design needs
- implementation plan outline and rollback notes
- focused and end-to-end validation targets

Preparation stops after this queue is reviewable. It does not activate or
implement `task-1`.

## Per-Task Context, Design, And Plan

Before editing the selected task:

1. Read the bound snapshot, handoff, and current repository evidence. Gather
   task-specific context with targeted reads. Use `brainstorm` when the task
   needs source-ranked context, tradeoff framing, or assumption challenges.
   Keep `brainstorm` read-only and summarize only recommendation-changing
   findings.
2. Route to `design` when the task is non-trivial, changes contracts, crosses
   ownership boundaries, has missing code, has ambiguous behavior, or has
   multiple plausible implementations. If `design` is unavailable but needed,
   record a compact local design note marked `design_skill_unavailable`.
3. Create a short implementation plan in the handoff: exact steps, likely
   files, vertical slice or layers, docs/changelog impact, focused and
   end-to-end validation, stop conditions, rollback notes, and review/commit
   gates.
4. Implement only after context, design, and plan fields are populated or
   explicitly marked not needed.

## Per-Task Completion Gate

Each `run` or `continue` session completes exactly one task:

1. Verify repository/scope identity, manifest, bound revision and digest,
   handoff status, Git state, dependencies, and single-writer ownership.
2. Complete the Per-Task Context, Design, And Plan gate.
3. Implement only the active task.
4. Run focused validation and the task's end-to-end checks.
5. Inspect the diff and remove unrelated cleanup.
6. Use `code-review` on the task's changes.
7. Fix safe scoped findings, re-run validation, and refresh review after
   material fixes.
8. Use `$commit` to create the local task checkpoint.
9. Update the handoff with context, design, plan, changed files, validation,
   review, fixes, commit hash/message or blocker, residual risks, checkpoint,
   and exact next-session prompt.
10. Stop so the next pending task starts in fresh context.

If `code-review` finds an unresolvable scoped blocker, mark the task `blocked`
with `REVIEW_BLOCKER`. If `$commit` cannot safely create the checkpoint, mark
the task with `COMMIT_BLOCKER` or `WORKTREE_CONFLICT`, as appropriate.

Because `$commit` stages the complete repository diff from the Git root, do not
invoke it when unrelated user changes are present and not intentionally part of
the active task or prior recorded checkpoints. Stop rather than produce a mixed
commit.

## Design Routing

Route to `design` before editing when any of these are true:

- Public API, CLI, data model, storage schema, or protocol contract changes.
- Cross-module or cross-service ownership boundaries.
- Missing codebase surface with no clear local pattern.
- Multiple viable approaches with materially different tradeoffs.
- Rollout, migration, compatibility, security, or reliability decisions.
- Underspecified behavior where guessing could create the wrong outcome.

If `design` is unavailable, record: problem, chosen approach, rejected
alternatives, open risks, and validation. Mark it
`design_skill_unavailable` so a later session can revisit it.

## Reconciliation

Reconciliation is a planning-only transition between immutable revisions of an
unfinished run. It must not edit product files or run an implementation task.

1. Verify there is no task actively being edited and acquire the scope-wide
   single-writer lock.
2. Append the edited prompt as the next immutable revision.
3. Compare old and new revisions plus recommendation-changing source drift.
4. Preserve every completed task exactly, including its ID and evidence.
5. Preserve a pending task only when its identity and completion contract are
   unchanged. Otherwise mark it `superseded`.
6. Append replacements and new work with the next unused task IDs. Never reuse
   or renumber IDs.
7. Update the handoff's bound revision, digest, source references,
   reconciliation summary, and queue. Return overall status to `prepared`.
8. Present the proposal and stop before implementation.

If the edited prompt is unchanged, return `NO_CHANGES`. If another scope run is
active, return `ACTIVE_RUN_EXISTS`. Never overwrite a prior snapshot.

## Handoff Discipline

The handoff is the durable execution truth between fresh sessions. The run
manifest owns immutable revision metadata only; do not place mutable status in
the manifest.

Read the complete handoff before any implementation action. Keep it concise
enough for a fresh agent to consume. Record summaries, not transcripts:

- prompt ID, source path, run manifest, bound revision and digest
- reconciliation state and revision history summary
- task queue, active task, completed checkpoints, and overall status
- changed files and vertical slice or layers covered
- focused and end-to-end validation results
- code-review decision and scoped fixes
- commit hash/message or exact commit blocker
- assumptions, risks, failure classification, and minimum missing input
- next task ID and exact next-session prompt

Do not store raw logs, prompt bodies, copied docs, credentials, private
endpoints, customer data, or unrelated exploration output.

## Fresh Session Patterns

Interactive CLI:

1. Finish the selected task through validation, review, fixes, and `$commit`.
2. Save the handoff checkpoint.
3. Run `/archive` or `/quit`, start a new Codex session from the repository
   root, and invoke `$task-implementer continue <run-id>`.
4. Use `/new` only if the operator accepts a fresh conversation in the same
   interactive process as the boundary.

Noninteractive automation:

1. Finish exactly one task in one `codex exec` process.
2. Save the reviewed and committed checkpoint.
3. Let the process exit.
4. Start a new `codex exec` process for the next task and pass the run ID.
5. Do not use `codex exec resume` for normal next-task handoff.

Example operator command when the private root needs write access:

```bash
codex --ask-for-approval never exec \
  --cd <repo-root> \
  --sandbox workspace-write \
  --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer" \
  'Use $task-implementer continue <run-id>. Implement exactly the next task, update the handoff, and stop.'
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

Use `$align` when available. Otherwise perform the equivalent local checklist
and record that substitution in the final checkpoint.
