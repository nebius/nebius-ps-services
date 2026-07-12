---
name: task-implementer
description: "Requires explicit invocation for a complex sequential brownfield workflow: create a private file-backed prompt workspace, prepare an immutable prompt revision into a reviewable task queue, or run, continue, or reconcile that queue one task per fresh Codex session with brainstorm/design/plan, validation, code-review, fixes, $commit, and a private markdown handoff. Do not use for ordinary one-shot implementation, prompt-file organization advice, Agentic SDLC, chat-only brainstorming, standalone review/commit/PR work, or parallel write agents."
---

# Task Implementer

## Purpose

Coordinate complex brownfield implementation from durable private Markdown
prompts. Turn one immutable submitted revision into an ordered
`task-1..task-n` handoff queue, then implement exactly one queued task per fresh
Codex session with reviewed, committed checkpoints.

The editable prompt records user intent. The immutable run revision fixes the
approved input. The handoff is the execution truth. This is a lightweight
sequential implementation loop, not the Agentic SDLC state machine.

## Invocation Policy

This Skill must be explicitly invoked. Do not implicitly invoke it because a
request is complex or because the user opens or edits a prompt file.

## When To Use

- The user explicitly invokes `$task-implementer workspace init`, `workspace
  new`, `prepare`, `run`, `continue`, or `reconcile`.
- The user explicitly requests the existing complex sequential brownfield
  implementation loop from a prompt or handoff.
- The work is bigger than one coherent task and benefits from dependency
  ordering, per-task review and commits, and fresh context between tasks.
- A serial multi-layer change should be delivered as ordered vertical slices.

## When Not To Use

- Do not invoke this Skill implicitly. Keep
  `policy.allow_implicit_invocation: false` in `agents/openai.yaml`.
- Do not use it for a normal one-shot implementation. Use the applicable
  project skills directly.
- Do not use it merely because a user opens, edits, or asks how to organize
  prompt files.
- Do not make `global-context-management` always call it. That skill owns
  context hygiene; this Skill owns an explicitly requested per-task commit and
  fresh-session workflow.
- Do not use it for Agentic SDLC; use `$sdlc-start` and the `sdlc-*` skills.
- Do not use it for chat-only ideation, design-only work, review-only work,
  standalone commits, PRs, merges, releases, or publication.
- Do not run parallel write-capable agents or overlapping implementation
  sessions in the same workspace.

## Interface

- `$task-implementer workspace init <scope>`: create or verify the private
  prompt workspace for a repository scope and optionally open it.
- `$task-implementer workspace new "<short ask>"`: create one private prompt
  file for one independent ask and optionally open it.
- `$task-implementer workspace list [filters]`: list prompt metadata without
  prompt bodies.
- `$task-implementer prepare [--new-run] <prompt-path>`: validate and snapshot
  the prompt, inspect the repository, create a reviewable queue and handoff,
  then stop without product edits.
- `$task-implementer run <run-id>`: approve the prepared bound revision and
  implement exactly the first pending task.
- `$task-implementer continue <run-id>`: in a fresh session, implement exactly
  the next pending task.
- `$task-implementer reconcile <run-id> <prompt-path>`: snapshot an edited
  active prompt, propose additive or superseding queue changes, and stop
  without product edits.

The VS Code task is an optional way to create a prompt. It never starts Codex.
After editing in VS Code, `prepare <prompt-path>` is the stable chat action that
submits the saved ask.

## Inputs

- An explicit interface action and its scope, prompt path, or run ID.
- The current repository, worktree and branch state, and instructions such as
  `AGENTS.md`.
- The exact immutable prompt snapshot and current handoff for `run`,
  `continue`, and `reconcile`.
- Relevant repository paths, tickets, issue text, logs, screenshots, sketches,
  or design notes referenced by the prompt, when safe and available.

## Required Reads

- `references/prompt-workspace.md` before any `workspace`, `prepare`, or
  `reconcile` action and whenever prompt identity or run state is unclear.
- `references/implementation-loop.md` before queue construction or an
  implementation session.
- The current run's `manifest.json`, bound snapshot, and complete `handoff.md`
  before `run`, `continue`, or `reconcile`.
- The relevant `AGENTS.md`, README, design docs, changelog, tests, source, and
  Git state before creating a queue or editing product files.
- The `brainstorm` skill when source-ranked context, tradeoffs, or assumption
  checks are relevant; keep that pass read-only.
- The `design` skill before each non-trivial task with architecture, contract,
  missing-code, ambiguous-boundary, or multiple-path decisions. Let `design`
  decide when `research` is needed.
- The `code-review` skill before reviewing an implemented task.
- The `commit` skill before a per-task local commit.
- Current official vendor documentation for version-sensitive behavior.

## Writes

Private state lives under:

```text
${CODEX_HOME:-$HOME/.codex}/task-implementer/projects/
<project-id>/scopes/<scope-id>/
├── workspace.json
├── <scope>-prompts.code-workspace
├── prompts/<created-at>--<ask-slug>.md
└── runs/<run-id>/
    ├── manifest.json
    ├── inputs/<revision>/prompt.md
    └── handoff.md
```

- `workspace init` and `workspace new` write only private workspace state.
- `prepare` and `reconcile` write only immutable revisions, manifests, and the
  private handoff. They must not edit product files.
- `run` and `continue` may make only the focused product, test, docs,
  changelog, config, or generated-artifact edits required by the active task.
- Per-task evidence belongs in the handoff as concise summaries, not raw logs.

Never commit prompt-workspace state. Never write prompts into the repository.
Never persist secrets, private endpoints, customer data, or broad copied
internal documentation.

## Process

### Workspace Actions

1. Resolve the installed Skill directory and invoke
   `scripts/prompt_workspace.py` with an argument array.
2. For `workspace init`, pass the explicit Git root and repo-relative scope to
   helper `init`. Create or verify the saved `CODE` + `PROMPTS` workspace. Open
   it only when explicitly requested.
3. For `workspace new`, resolve the owning `workspace.json`, pass the short ask
   to helper `new`, and return the generated path. Open it only when explicitly
   requested.
4. For `workspace list`, use helper `list` with optional query or creation-date
   filters. Never print a prompt body.
5. Stop after the requested workspace operation. Do not infer a preparation or
   implementation request.

### Prepare

1. Read `references/prompt-workspace.md`. Resolve the prompt's owning
   `workspace.json`; do not accept repository-local or unowned prompt files.
2. Use helper `snapshot` to validate and copy the exact prompt bytes. Forward
   `--new-run` only when the user explicitly requested an exact new run.
   If an interrupted earlier attempt left exactly one same-prompt,
   same-digest run in `snapshot_only` state with no handoff, resume that
   revision instead of creating another. Do not apply this recovery to another
   prompt, a drifted source, or any run that already has a handoff.
3. Create `handoff.md` from `assets/handoff-template.md` in the returned run
   directory using a private atomic write. Bind it to `r0001`, or the helper's
   returned revision, digest, manifest, source path, prompt ID, project, and
   scope.
4. Inspect the target code before ordering tasks. Use targeted `rg`, small file
   reads, local instructions, tests, docs, and current Git evidence.
5. Extract concrete work items from the immutable snapshot and repo evidence.
   Merge duplicates and split only for independent results, dependency edges,
   review risks, or validation gates.
6. Order tasks as `task-1..task-n`. Prefer vertical end-to-end slices for serial
   multi-layer features; create foundation tasks only for real prerequisites.
7. For every task record the goal, source prompt sections and bound revision,
   rationale, dependencies, likely files, context/design needs, vertical slice
   or layers, plan outline, validation, done criteria, and rollback notes.
8. Set overall status to `prepared`, reconciliation state to `none`, and the
   active task to `none`. Summarize current worktree risks.
9. Verify the run and handoff, report the queue for review, and stop. Do not
   edit product files, approve the queue implicitly, invoke `$commit`, or begin
   `task-1`.

### Run Or Continue

1. Resolve the run unambiguously within the current canonical repository and
   scope. Read its manifest and handoff completely.
2. Use helper `verify` and verify the exact bound revision and SHA-256 recorded
   in the handoff. Read that snapshot, never the editable source, as execution
   input. Treat `PROMPT_DRIFT` from an invalid, missing, or edited source as
   non-binding for `run` and `continue`: report it and continue from the valid
   bound snapshot unless the user asks to reconcile. Do not silently rebind.
3. For `run`, require overall status `prepared`, no completed task, and select
   exactly the first pending task. For `continue`, require a prior checkpoint
   and select exactly the next dependency-ready pending task.
4. Verify current Git status and task evidence. Enforce one active task and one
   writer for the scope.
5. Gather only the active task's context. Use `brainstorm` when relevant and
   summarize recommendation-changing findings in the handoff.
6. Route non-trivial architecture, contract, missing-code, ambiguous-boundary,
   rollout, migration, security, or reliability choices through `design`. If
   needed but unavailable, record a compact local design note marked
   `design_skill_unavailable`.
7. Write the per-task implementation plan: exact steps, likely files, vertical
   slice or layers, docs/changelog impact, validation including end-to-end
   checks, rollback notes, stop conditions, and review/commit gates.
8. Implement only the selected task. Run focused validation and broaden only
   when the changed surface requires it. Inspect the diff and remove unrelated
   cleanup.
9. Invoke `code-review`. Fix safe scoped findings, re-run validation, and
   refresh review when fixes materially changed code.
10. Invoke `$commit` only after validation and review gates pass. Let the
    `commit` skill own repo-root `git add -A`, staged checks, hooks, message,
    and no-push behavior.
11. Update the handoff with context, design, plan, changed files, validation,
    review, fixes, commit hash/message or blocker, risks, checkpoint, and exact
    next-session prompt.
12. Stop. The next task must start in a fresh session with `continue <run-id>`.
13. After the last task, run changed-surface `$align` or the equivalent local
    alignment checklist, record the result, set status `done`, and stop.

### Reconcile

1. Read the manifest, current bound snapshot, edited prompt, and handoff. Use
   helper `snapshot --run-id <run-id>` to append the next revision.
2. Require an unfinished run and no implementation currently in progress.
   Reconciliation is a planning-only transition with no product edits.
   Reject a handoff whose overall status is `running`.
3. Compare prompt revisions and inspect only source drift that affects the
   queue.
4. Never change completed task text or IDs. Never renumber any existing task.
   Preserve unchanged pending tasks, mark changed or removed pending tasks
   `superseded`, and append replacements or new tasks with the next unused IDs.
5. Update the bound revision, digest, manifest path, reconciliation summary,
   and task source references. Set status `prepared` and active task `none`.
6. Verify and present the proposal, then stop without implementing it.

If an interrupted earlier reconciliation already appended a same-digest latest
revision that the handoff has not bound, resume that revision and finish the
queue proposal. Do not create another revision. If the source changed again,
stop until the pending reconciliation is resolved.

## Fresh Session Contract

The Skill cannot reset its own context. After each task checkpoint, save the
handoff and stop. Use one of these mechanisms:

- Close/archive and start a new interactive Codex session from the repository
  root, then invoke `$task-implementer continue <run-id>`.
- Use `/new` only when the operator accepts a fresh conversation in the same
  interactive process as the session boundary.
- Start one new `codex exec` process per task. Let the previous process exit;
  do not use `codex exec resume` for normal handoff.

When sandbox access is missing, preserve the current sandbox and approval
policy and report:

```bash
codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"
```

Do not launch a new Codex process automatically from the prompt helper or VS
Code workspace.

## Ordering Rules

- Preserve explicit user priority unless dependencies make it impossible.
- Put prerequisite schema, API, data-model, config, auth, or migration work
  before consumers.
- Keep tests and documentation with the behavior they verify or explain.
- Prefer vertical deliverables over broad layer-by-layer tasks.
- Prefer independently validated checkpoints; do not create ceremony-only
  tasks.
- Do not split a request merely because it touches several files.
- One prompt can create many tasks. Never create one task file per task.

## Idempotency

- On every action, verify canonical repository, scope, private paths, schema,
  permissions, manifest, digest, handoff, and current Git state before writes.
- Revisions are created only by `prepare` or `reconcile`, never on save.
- The run manifest is append-only revision metadata. The handoff owns mutable
  status and the queue.
- Never renumber task IDs after preparation. Append new IDs and explicitly mark
  superseded tasks.
- Never rewrite a completed task during reconciliation.
- If a task is done, verify its evidence instead of repeating it.
- If a recorded commit exists, verify it still exists and matches the evidence.
- Keep one scope-wide writer and exactly one active task.
- `run` and `continue` always read the immutable bound snapshot. The editable
  source remains read-only to the Skill.

## Failure Handling

Preserve helper error tokens from `references/prompt-workspace.md`. Also
classify implementation-loop failures before retrying:

- `ACTIVE_RUN_EXISTS`: ordinary preparation cannot bypass unfinished scope
  work; reconcile the owning run.
- `NO_CHANGES`: no revision or run was created; stop unless the user explicitly
  requests `prepare --new-run` for a completed prompt.
- `PROMPT_DRIFT`: editable source differs from the bound snapshot; continue
  from the snapshot or reconcile, never rebind silently.
- `WORKSPACE_BUSY`: another process owns the scope-wide write lock.
- `RUN_STATE_INVALID`: run, manifest, revision, digest, or handoff state is
  unsafe; fail closed instead of repairing it by inference.
- `DESIGN_GAP`: architecture, contract, missing-code, or boundary decisions
  are unresolved.
- `CONTEXT_GAP`: required repository, ticket, internal, or vendor context is
  missing.
- `IMPLEMENTATION_DEFECT`, `TEST_DEFECT`, or `VALIDATION_DEFECT`: the active
  task or its checks are incomplete or incorrect.
- `REVIEW_BLOCKER`: `code-review` found a blocker that cannot be fixed safely
  inside the task.
- `COMMIT_BLOCKER`: staged checks, hooks, branch state, or commit scope is
  unsafe.
- `WORKTREE_CONFLICT`: unrelated or concurrent changes make whole-repository
  `$commit` unsafe.
- `ENVIRONMENT_BLOCKER`: tools, credentials, services, network, sandbox access,
  or permissions are unavailable.
- `HUMAN_INPUT_REQUIRED`: a consequential decision is unsafe to guess.

Retry only when the next change is clear and scoped. Never silently repair
malformed prompt-workspace state. Record blockers in the handoff and request
the minimum safe user action.

## Must Not

- Do not write prompts into a Git worktree, even as an ignored directory.
- Do not edit an editable source prompt, print prompt bodies in status output,
  or treat SHA-256 as encryption.
- Do not read the editable prompt as execution truth after a run is prepared.
- Do not create task files, includes, prompt bundles, database state, deprecated
  custom prompts, or legacy state migration.
- Do not prepare or reconcile and then continue into product implementation.
- Do not automatically launch Codex or submit content from VS Code.
- Do not run overlapping writers or parallel implementation sessions.
- Do not bypass `code-review`, hand-commit, push, open a PR, merge, publish, or
  perform live external writes without a separately authorized workflow.
- Do not use compatibility shims or dual old/new paths unless explicitly
  requested.

## Completion Criteria

- Workspace actions leave a private, valid, discoverable prompt workspace and
  no Git changes.
- `prepare` leaves an immutable revision and a reviewable `prepared` handoff,
  with no product edits.
- `run` and each `continue` complete, validate, review, fix, and locally commit
  exactly one queued task, save a checkpoint, and stop for a fresh session.
- `reconcile` preserves completed work and stable IDs, appends revision and
  queue changes, and stops without product edits.
- The final handoff records all task evidence and changed-surface alignment,
  with no unresolved blocker hidden.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- Action and repository scope handled.
- Private workspace, prompt, run, revision, manifest, and handoff paths created
  or verified, without quoting prompt bodies.
- Prepared queue or exactly one completed task checkpoint, as applicable.
- Validation, review, commit, and final alignment evidence actually obtained.
- Stable failure classification and the minimum next action when blocked.
