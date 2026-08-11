---
name: commit
description: "Create a fast local Git commit on the current branch without pushing. Use when the user explicitly asks to commit current local changes, or when a fresh explicit `$worktree integrate` delegates one exact child/source commit: inspect the complete diff, stage with repo-root `git add -A`, validate, commit with normal hooks, and report exact evidence. Do not use for pushes, pull requests, branch repair, or Agentic SDLC feature commits."
---

# Commit

## Help

For `$commit --help` or `$commit -h`, return concise help and stop before
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

Create one local commit for the current branch as quickly as is safely
reasonable, including a complete monorepo diff that spans any number of
project folders. This skill is for ordinary Git commits, not publishing or
Agentic SDLC checkpoints.

## When To Use

- Committing all current local changes on the current branch without pushing.
- Staging complete repository changes with repo-root `git add -A`, regardless
  of the project, service, chart, app, or package directory where Codex starts.
- Using the user's exact commit message when provided.
- Generating a concise commit message from the staged diff when the user does
  not provide one.
- Reporting a clear no-op when there is nothing to commit.
- Executing one bounded delegated commit for a fresh explicit
  `$worktree integrate`: an eligible ordinary child first or its primary source
  second, at the exact preflight branch and head.

## When Not To Use

- Do not push; use `commit-push` when the user explicitly asks to push.
- Do not create, open, update, review, merge, or close pull requests.
- Do not replace `sdlc-commit`; that skill is only for Agentic SDLC feature
  checkpoints with SDLC evidence and local run state.
- Do not create branches, switch branches, pull, merge, rebase, cherry-pick,
  revert, stash, reset, amend, squash, or repair divergence.
- Do not stage a partial pathspec or project-scoped path. This skill always
  stages the complete repository diff with `git add -A` from the Git root.
- Do not run formatters, dependency updates, tests, or broad repair commands
  just to create the commit unless the user separately requested them.

## Inputs

- The exact Git worktree and current branch selected by the user's session.
- A fresh root-user authorization from the prompt hook, or exact delegated
  owner evidence from Task Implementer or Worktree.
- An optional user-provided commit message; otherwise the reviewed candidate
  must be coherent enough to summarize truthfully.
- The current selected-project lifecycle state or its bounded zero-write
  commit-only waiver.

## Required Reads

- Repository-root branch, operation, conflict, status, staged, unstaged, and
  untracked state before preparation.
- The hook- or coordinator-provided canonical authorization and claim paths;
  never discover or substitute an alternate helper or private-state path.
- The complete temporary-index candidate summary and enough focused diff to
  review every changed project, root file, risky path, and generated artifact.
- In delegated mode, the exact current assignment, preflight, branch, head,
  and owner evidence returned by the owning workflow.

## Writes

- One owner-private authorization and claim state under the canonical Codex
  home transaction root; these records contain bounded identities and digests,
  not prompt, message, diff, or repository file content.
- The real Git index and one local commit only inside the owning transaction
  helper after the reviewed candidate is revalidated.
- No push, PR, publication, external-system write, branch operation, or
  Agentic SDLC state mutation.

## Process

1. Enter the repository root.
   - Reject caller-provided repository-shaping Git environment before any Git
     discovery or mutation. The transaction's private preview is the only
     permitted `GIT_INDEX_FILE` override.
   - Use `git rev-parse --show-toplevel`, then run all Git commands from that
     root.
   - Treat the Git root as the commit scope. Never infer a narrower scope from
     the starting directory.
2. Run fast branch and repository safety checks.
   - Stop on detached `HEAD`.
   - Stop if a merge, rebase, cherry-pick, revert, or bisect is in progress.
   - Stop if unresolved conflicts exist.
   - If the repository default branch is known locally and the current branch
     matches it, stop unless the user explicitly asked to commit on the default
     branch by starting the invocation body with `on <current-branch>` or
     `on the default branch`. Do not perform network fetches only to discover
     the default branch.
3. Inspect current status.
   - Run `git status --short --branch`.
   - If the worktree is clean, report that there is nothing to commit.
   - Inspect staged and unstaged diffs plus every untracked, renamed, deleted,
     credential-like, environment, key, and generated path before staging.
   - Stop before staging obvious secrets, private endpoints, credentials,
     unclear generated artifacts, or a diff too broad or incoherent to
     summarize truthfully.
4. Prepare one reviewed repository transaction.
   - Direct mode is authorized only by this turn's explicit root-user
     invocation. Accept optional `please`, then either `$commit` directly or
     one bounded leading directive from `run`, `apply`, `execute`, `invoke`, or
     `use` immediately before `$commit`. Casual mentions,
     questions, quotations, later prose references, implicit selection,
     subagent turns, Stop continuations, contradictory explicit origin
     markers, and help do not authorize mutation. An absent `agent_type` is
     compatible with primary UserPromptSubmit events; an explicit non-root
     value is denied.
   - Before direct preparation, require the current selected-project lifecycle
     to be sealed. A fresh turn that has performed no project writes may record
     its bounded commit-only waiver instead. Do not discover or require sibling
     project lifecycle attestations. Task Implementer uses its delegated owner
     evidence at the worker boundary rather than a root direct-mode waiver.
   - A Task Implementer worker does not use root-user intent. Its exact
     one-direct-child authorization is minted by `task-start` from the immutable
     assignment, running task plane, worker session, branch, and base `HEAD`;
     the helper revalidates that evidence before preparation, execution, and
     interrupted-commit recovery.
   - Use the canonical installed `commit_transaction.py prepare` helper with
     the hook-provided authorization and claim paths, exact Git root, and
     current session. Task Implementer returns the same two paths from its
     owner transition. Never invoke a source-tree or alternate helper.
   - The helper copies the current real index into a private temporary index,
     runs repo-root `git add -A` there, validates the candidate, and returns the
     exact `git write-tree` candidate plus a one-shot private claim token.
     Preparation must leave the real index and worktree unchanged.
   - Inspect the complete candidate with read-only Git tree/diff commands.
     Stop before execution if it contains unsafe, incoherent, or unexplained
     content. The selected project lifecycle remains the only semantic
     lifecycle; sibling project attestations are not required.
5. Execute the exact reviewed transaction.
   - Use the canonical installed `commit_transaction.py execute` helper with
     the same root, session, claim and token, the exact reviewed candidate tree,
     and the final commit message. Run one uncomposed command.
   - Under the common-repository lock, the helper revalidates ref, `HEAD`, real
     index, complete porcelain status, candidate tree, and Worktree ownership.
     Any drift makes the claim stale before real staging.
   - The helper alone runs repo-root `git add -A` with no pathspec, verifies the
     staged tree, and runs `git diff --cached --check`.
   - If it reports whitespace errors, conflict markers, or another staged-diff
     problem, stop and report the blocker. Keep this skill fast; do not start a
     repair loop unless the user separately asks for fixes.
   - Run `git diff --stat HEAD <candidate-tree>` and
     `git diff --name-status HEAD <candidate-tree>` while reviewing.
   - If the candidate tree equals `HEAD^{tree}`, report that there is nothing
     to commit.
   - Inspect candidate filenames and enough focused candidate diff to avoid an
     obviously wrong or unsafe commit message, especially for new config,
     credential-like, environment, key, or generated files.
   - In delegated Worktree mode, continue to pass its reviewed tree plus the
     exact preflight head to Worktree's private integration-commit action; do
     not create a second direct claim.
6. Commit inside the owning transaction.
   - Use the user's exact commit message if provided.
   - Otherwise generate one concise imperative subject line from the staged
     diff. If the diff is too broad or unclear to summarize truthfully, stop and
     ask for a commit message.
   - For an ordinary direct `$commit`, the transaction helper runs
     `git commit -m "<message>"` with normal hooks enabled. Do not run a raw
     `git commit` or raw `git add` outside the helper.
   - For a delegated Worktree commit, let the private Worktree helper create a
     durable source-scoped preparation claim, rerun `git add -A`, compare the
     staged tree to the reviewed tree, and then run the normal-hook commit.
     The claim blocks nested leases, competing integrations, removal, and
     source publication until candidate reservation or explicit abort.
7. Verify, reconcile, and report.
   - Run `git status --short --branch`.
   - In delegated worktree mode, require the same branch, exactly one
     direct-descendant commit from the preflight head, and a completely clean
     checkout. Return that exact commit SHA to the worktree workflow.
   - Compare `HEAD^{tree}` with the reviewed staged tree. If a hook changed the
     committed tree, preserve the commit, mark the claim `REVIEW_REQUIRED`, and
     inspect the complete actual direct-child commit. After that review, use the
     helper's private `review` transition with the exact observed commit and
     tree to complete the claim. Never amend or reset automatically. If the
     actual commit cannot be fully reviewed or is not the clean exact direct
     child, stop with `REVIEW_REQUIRED` intact.
   - A failed hook that did not create a commit and any pre-commit drift make
     the claim `STALE`; preserve the real index and worktree and require a fresh
     explicit `$commit` after the blocker is resolved.
   - Exact crash recovery accepts only the same branch and either the unchanged
     base or one direct-child commit with the reviewed tree. A fresh explicit
     `$commit` may rebind an otherwise unchanged prepared claim; it never uses
     a TTL, process identity, or guessed ownership.
   - Report the branch name, commit hash and message when a commit was created,
     the validation performed, and whether the worktree is clean.

## Recommended Commands

- Repository root: `git rev-parse --show-toplevel`
- Current branch: `git symbolic-ref -q --short HEAD`
- Status: `git status --short --branch`
- In-progress operation checks: inspect Git state paths such as `MERGE_HEAD`,
  `rebase-merge`, `rebase-apply`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, and
  `BISECT_LOG` under the Git directory from `git rev-parse --git-path <name>`.
- Conflict check: `git diff --name-only --diff-filter=U`
- Default branch, local only:
  `git symbolic-ref -q --short refs/remotes/origin/HEAD | sed 's#^origin/##'`
- Candidate preparation: canonical installed commit transaction helper with
  the hook-provided explicit-turn authorization and private claim paths
- Staging inside the helper: `git add -A` from the repository root, with no
  pathspec
- Staged validation inside the helper: `git diff --cached --check`
- Candidate summary: `git diff --stat HEAD <candidate-tree>`
- Candidate filenames: `git diff --name-status HEAD <candidate-tree>`
- Reviewed staged tree: exact `candidate_tree` returned by preparation
- Commit inside the helper: `git commit -m "<message>"`
- Committed tree: `git rev-parse HEAD^{tree}`
- Commit hash: `git rev-parse --short HEAD`

## Idempotency

- One authorization is consumed by one prepared claim, and execution requires
  the claim's exact session, token, reviewed tree, repository identity, and
  owner evidence.
- Repeated execution returns the already-proven commit instead of creating a
  second one. Fresh preparation may adopt only an unchanged exact staged state
  or one exact direct child after all applicable ownership checks pass.
- Terminal claims are archived by exact content digest before a later fresh
  transaction replaces the active claim; no timeout or process identity is
  treated as completion evidence.

## Failure Handling

- Before real staging, repository, candidate, authorization, owner, or claim
  drift becomes `STALE` and requires a fresh explicit invocation.
- A normal hook failure with no commit becomes `STALE`; preserve the real
  index and worktree for diagnosis rather than resetting or unstaging them.
- A created direct child whose tree or checkout is not the exact reviewed
  clean result becomes `REVIEW_REQUIRED`; complete it only through the private
  review transition after inspecting the actual commit and tree.
- Malformed private state, conflicting workflow ownership, default-branch
  ambiguity, merge history, or unverifiable recovery fails closed without
  repository cleanup or a second commit.

## Must Not

- Do not treat anything except a fresh explicit root-user `$commit`
  invocation, including the bounded leading directive forms above, as
  permission to prepare and execute the one canonical local transaction for
  the current branch. The hook authorization is single-use and contains no
  prompt or commit-message text.
- Do not use the helper to bypass unresolved selected-project reconciliation.
  Direct mode requires a sealed lifecycle or the bounded fresh zero-write
  commit-only waiver; this never expands into sibling-project attestation.
- Treat a fresh explicit `$worktree integrate <name>` as permission for only
  the eligible child/source commits that its read-only preflight orders. Do not
  infer delegated permission from a coordinator handoff, active reservation,
  restart, candidate, or ordinary implicit skill selection.
- Treat a Task Implementer worker authorization as permission only in its exact
  assigned worktree/session while the task plane is still running at the
  assigned base. It never authorizes primary-source dirt or another worker.
- Never push, create a PR, dispatch CI, publish artifacts, or call external
  write APIs from this skill.
- Never run `git add -A` outside the repository root.
- Never broadly allow raw Git mutation to work around project lifecycle
  policy. Only the exact digest-pinned helper and current private evidence are
  admissible.
- Never pass a pathspec to `git add -A`; this skill is intentionally whole-repo
  because multi-folder repositories often have related changes outside the
  current directory.
- Never use `--no-verify`; commit hooks should run normally.
- Never use `--allow-empty`; an empty staged diff is a no-op.
- Stop before committing obvious secrets, private endpoints, credential files,
  generated files with unclear ownership, unresolved conflicts, or staged
  validation failures.
- Stop on known default branch unless the user explicitly authorized committing
  there.
- Never write Agentic SDLC evidence, permissions files, run state, or
  checkpoints. An active Agentic SDLC run rejects the direct helper; use
  `sdlc-commit` inside the SDLC workflow instead.
- In delegated worktree mode, stop if the observed branch or head differs from
  preflight. Never commit a nested/coordinated child or any dirty checkout after
  an integration reservation exists.
- When Task Implementer integration finds dirty primary-source state, require a
  separate fresh explicit `$commit`, then repeat integration. Never auto-commit
  that dirt.

## Completion Criteria

- Direct mode reports either a truthful no-op or one local exact direct-child
  commit whose tree matches the reviewed candidate and whose final checkout is
  clean.
- Delegated mode returns the exact eligible commit to its owner with unchanged
  branch identity and all owner evidence still current.
- Final output names the branch, commit hash and message when created,
  repo-root `git add -A`, validation performed, final status, and any retained
  `STALE` or `REVIEW_REQUIRED` blocker.
- No push, PR, branch rewrite, sibling lifecycle mutation, or external write
  occurred.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- Whether the run committed or no-op'd.
- The current branch.
- The commit hash and commit message when a commit was created.
- Confirmation that staging used repo-root `git add -A`.
- The lightweight validation performed.
- Final `git status --short --branch` interpretation.
- Any blocker that stopped the workflow.
