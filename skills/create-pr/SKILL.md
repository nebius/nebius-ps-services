---
name: create-pr
description: "Use only when explicitly asked to create/prepare a GitHub PR from unmanaged work or a named branch: validate, commit, merge base, push, open/reuse, and report readiness. In active Agentic SDLC, publish only the exact promoted SHA after UAT. Reject managed worktrees; use commit-push when no PR is wanted."
disable-model-invocation: true
---

# Create PR

## Help

For `$create-pr --help` or `$create-pr -h` (including native Claude forms), return concise help and stop before
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

Use `$create-pr` in Codex, `/create-pr` in Claude Code, or
`/skills:create-pr` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Purpose

Use this skill to turn local repository work or named branches into GitHub pull
requests with a safe default-branch workflow. It can prepare a single PR or one
PR per branch, resolve straightforward merge conflicts against the default
branch, and report the order the user should merge the PRs manually.

## When To Use

- Opening a PR from local changes or local commits.
- Opening or reusing PRs for one or more named branches.
- Moving in-progress work off the default branch before publishing it.
- Always staging current local work from the repository root with `git add -A`
  when committing changes for a PR.
- Running and repairing safe formatting, whitespace, lint, build, and test
  gates before committing local dirty work for a PR.
- Waiting for local test commands to finish before staging and committing
  dirty work.
- Reusing the current feature branch instead of creating extra branches.
- Treating the current non-default branch as the normal PR path: stage existing
  work with `git add -A`, commit on that branch, push it, and open or reuse the
  PR without creating another branch.
- Merging the latest `origin/<base>` into each target branch before PR creation
  so the branch includes the current base without rewriting branch history.
- Making each target branch conflict-free against the default branch, usually
  `main`, before returning the PR.
- Repairing safe branch-owned validation, build, lint, test, or GitHub check
  failures before presenting PR creation as handled.
- Waiting for GitHub PR checks to reach a terminal state when those checks are
  available before reporting the PR as ready.
- Planning an ordered multi-branch merge path when several branches may overlap.
- Honoring an explicit user-provided PR title or body instead of inventing one.
- Returning PR numbers, URLs, readiness state, and merge order so the user can
  review or merge manually.
- In an Agentic SDLC run, publishing or reusing the PR only after
  `sdlc-uat-tests` passes for the clean exact SHA promoted by `sdlc-commit`.

## When Not To Use

Use commit-push for publication without a PR and review-pr for PR review.
Managed children must finish their owning integration workflow first.

## Inputs

- A Git repository with an `origin` remote.
- GitHub CLI (`gh`) authenticated for the target repository.
- A clean worktree before switching between existing branches or updating
  remote PR branches. If local work is dirty on the active branch, move or
  commit it before updating other branches.

## Managed Worktree Guard

Before staging, committing, fetching for publication, pushing, or creating a
PR, resolve the installed `worktree` skill and invoke its Python manager's
`publication-guard --publication-action create-pr` action from the current
checkout.
If it reports any managed child, integration candidate, nested worker, or
inconsistent ownership claim, stop and route to its owning local workflow.
Only a genuinely unmanaged manual worktree may pass as `unmanaged`. This guard
also overrides active SDLC publication mode: managed children integrate
locally, while only an unmanaged final source branch may use the PR workflow
below.

## Active Agentic SDLC Publication Mode

When the current project has a matching active Agentic SDLC run, this mode
overrides the generic branch-preparation, repair, and base-merge steps below.
This is publication-only mode: `create-pr` is a handoff, not another
implementation gate.

Before any push or PR creation/reuse:

- Reload the active run, current checkpoint, feature evidence, execution
  coordinator, commit evidence, and UAT evidence.
- Require the run to route to `create-pr`, have no managed outer-integration
  state, have passed UAT, and have a clean current named non-default branch.
- If managed interop is `leased`, `pending`, or `integrated`, do not publish the
  child. Route `leased` back through the active SDLC run. For `pending`, stop
  and tell the user to switch to the recorded primary checkout and invoke the
  exact `$worktree integrate` handoff; for `integrated`, stop and publish only
  from the separately selected final source branch. Never invoke the
  explicit-only `worktree` skill from `create-pr`.
- Resolve one canonical `promoted_head` from execution and commit evidence.
  Require current `HEAD`, the recorded promoted HEAD, and any existing remote
  PR head to equal that exact SHA.
- Stop on missing, stale, or conflicting evidence. Do not infer a replacement
  SHA from branch history. Identity disagreement maps to `PR_HEAD_DRIFT`.

In this mode, do not stage, format, edit, commit, amend, merge the base, resolve
conflicts, switch or create branches, repair checks, or otherwise change the
promoted commit. Fetching refs and reading PR/check state are allowed. If the
base moved, the PR conflicts, a branch-owned check fails, or any code/docs/test
change is needed, record the blocker and route it through
`sdlc-classify-failure` and `sdlc-start`. The responsible validation,
evaluation, documentation, alignment, commit, and UAT gates must produce and
promote a new exact SHA before publication is retried.

Immediately before each authorized push or GitHub PR creation call, write a
short-lived `permissions/pr-authorization.json` containing at least
`allowed: true`, `phase: "create-pr"`, the exact branch,
`expected_head: <promoted_head>`, the dynamically resolved
`base_branch: <origin-default>`, `base_head: <recorded-default-head>`,
`uat_status: "passed"`, and `expires_at`. Re-resolve the symbolic remote
default immediately before authorization and reject either branch or HEAD
drift.
Publish with one direct `git push origin HEAD:<branch>` action, and create a PR
with one direct `gh pr create --base <origin-default> --head <branch> ...`
action or a PR-creation MCP call whose `base` and `head` are those exact
branches. Do not use a shell wrapper, prepend or append another command, or
omit either explicit branch. Reuse an existing remote branch or PR only when
its head is the exact promoted SHA and its base is the recorded remote default;
any mismatch is a blocker, not permission to update or overwrite it. Remove or
expire the authorization when publication completes or stops.

## Commit Authority

Explicit complete `publish-release` requests delegate necessary PR/commit work
to this owner; see the release-caller contract in `references/commit-continuation.md`.

An explicit PR task authorizes the necessary validated staging, commits and
pushes throughout its branch-owned repair loop. Do not request a new user turn
for each repair. Before committing, read `references/commit-continuation.md`
and call the canonical installed shared `commit` helper in `create-pr` mode.
Each commit gets a fresh exact-tree claim; only the helper runs repo-root
`git add -A` and normal-hook `git commit`. Staging commands below describe those
helper-owned effects, not permission for raw Git fallback. Ordinary `commit`
and `commit-push` permit one actual commit plus safe no-commit retries. Active SDLC remains publication-only.

## Required Reads

Read current repository instructions, the affected checks, and
`references/commit-continuation.md` before any PR commit.

## Writes

May repair authorized branch-owned files, create exact local commit claims,
push the selected branch and update its PR. Keep private grants outside Git.

## Idempotency

Reuse the same branch, PR and active continuation grant. Review each fresh tree;
repeated execution of one claim returns its existing commit. Close the grant
when the PR task completes or is abandoned; a closed receipt cannot reopen it.

## Failure Handling

Retry safe no-commit failures within the same grant after correcting their
cause. Preserve uncertain commits for explicit review. Scope or ownership drift
blocks continuation; never reset claims, fabricate receipts or bypass hooks.

## Completion Criteria

The current pushed head has terminal passing available checks, the PR body
reflects that head, and the continuation grant is closed. Otherwise report the
specific blocker and keep the PR draft; ordinary review approval stays separate.

## Local Check Order

For any dirty local work this skill will commit:

- Work from the repository root.
- Inspect `git status --short` and the dirty diff before staging.
- Run pre-test hygiene before tests: scan for conflict markers, run
  `git diff --check`, and run the relevant existing formatter or lint command
  for touched files when that command is available and safe. Apply only safe,
  mechanical fixes. Do not run broad formatters over generated, vendored, or
  exact upstream-imported files.
- Run the focused local tests after formatting, whitespace, and lint fixes.
  Wait for each test command to finish before staging or committing. If a test
  is still running, pending, or waiting on external state, do not commit yet
  unless generic non-SDLC mode applies, the user explicitly asked for an early
  draft PR, and the blocker is recorded.
- If validation fails and the failure is plausibly caused by branch work,
  repair it, rerun the failed check, and keep the loop bounded to safe,
  branch-owned fixes.
- Stage only after the working tree passes the selected checks:
  `git add -A`, then `git diff --cached --check`, then inspect
  `git diff --cached --stat` and any needed focused staged diff before
  committing.
- If staged validation finds only simple mechanical whitespace issues, repair
  the smallest safe whitespace-only issue, rerun `git add -A` and
  `git diff --cached --check`, and rerun affected local checks when the repair
  touches executable or source files. Stop on conflict markers, unresolved
  conflicts, broad formatter churn, generated-artifact uncertainty, semantic
  failures, or any staged-validation problem that is not plainly mechanical.

## Base Merge Policy

Refresh refs with `git fetch origin`, then run private `sync` on the clean,
selected target after any dirty work has been validated and committed. The
helper owns the normal-hook merge from the frozen base; do not perform raw
merges. Review/check the actual result and acknowledge its exact tree before
publication. Never merge the PR branch into the default branch, rebase or
force-push. Use `git push -u origin HEAD:<branch>` for a new remote branch and
`git push origin HEAD:<branch>` for an existing one.

## Branch Selection

- If the user provides one or more branch names, process exactly those
  branches. Preserve the user-provided order unless current Git evidence shows
  a safer dependency order.
- If the user provides no branch name and the current branch is non-default,
  treat the current branch as the only target branch. Do not create a new
  branch, do not switch to another branch before committing current work, and
  make that branch conflict-free against the default branch after the local
  work is committed.
- If the user provides no branch name and the current branch is the default
  branch, use the local-work PR flow: create a feature branch only when there
  is work to submit.
- For multiple target branches, create or reuse one PR per branch. Do not
  combine unrelated branches into a single PR.
- Use the repository default branch as the PR base unless the user explicitly
  provides another base.

## Process

1. Inspect repository state first.
   Determine:
   - current branch
   - whether `HEAD` is detached
   - repository default branch
   - whether the worktree has changes
   - whether the current branch already has an upstream
   - whether the current branch is ahead of or behind `origin/<base>`
   - whether the user named target branches or expects current-branch fallback
   - whether an Agentic SDLC UAT report exists for the current run and whether
     it passed, when this skill is invoked from the SDLC workflow
   - whether a matching active Agentic SDLC run selects the restricted
     publication mode above; if so, follow that mode and skip generic
     branch-preparation, base-merge, repair, and commit steps
2. Freeze task scope, then handle the current feature branch.
   - For an unmanaged task, read `references/commit-continuation.md`. Resolve
     all targets, base, dependencies and a new scratch validation ref if needed.
     Call the canonical helper's private `begin` with the original receipt before
     any branch switch or HEAD movement, including a clean start.
   - Keep the original task key for all attempts and every selected branch.
     User authorization persists through safe retries; do not require another
     commit invocation because a claim became stale.
   - If `HEAD` is detached, stop and explain the problem.
   - If no branch is named and the current branch is already non-default,
     reuse it as the only target branch. Do not create another branch.
   - If this current feature branch has a dirty worktree and the user invoked
     this skill to create or update the PR for current work, follow
     `Local Check Order`: run and repair safe pre-test hygiene, wait for local
     tests to finish, stage the complete repository diff from the repository
     root with `git add -A`, run `git diff --cached --check`, inspect the
     staged diff, and commit it on the current branch with a concise message
     before merging the base branch, switching branches, pushing, or
     opening the PR.
   - Do not stage only selected paths. If the dirty worktree contains changes
     that should not be part of the PR, stop before staging and tell the user
     that `create-pr` is configured for repo-wide `git add -A` commits.
3. Refresh base-branch context.
   Fetch `origin/<base>` and the target branch refs before resolving
   conflicts, validating branch diffs, or opening PRs. From the default branch,
   create the planned feature at the receipt HEAD, then synchronize that feature
   with the refreshed base through the helper. Do not move default-branch HEAD
   between receipt capture and task intake.
4. Resolve the remaining target branches.
   - If the user named branches, check whether each exists locally or on
     `origin`. Stop for unknown branches instead of guessing.
   - If no branch is named and the current branch is the default branch, create
     a new branch from it. Prefer a short user-provided slug such as
     `prep/<project-tag>` or `fix/<topic>`. Ensure the branch name does not
     collide with an unrelated existing local or remote branch.
   - If no branch is named and the current branch is already non-default, keep
     using the current branch selected in step 2.
5. Make each branch reviewable.
   A PR must come from committed changes, not only a dirty worktree.
   - If the worktree is dirty on the default branch, create the feature branch
     first so the in-progress work moves off the default branch safely.
   - If the worktree is still dirty after branch selection and the user clearly
     wants to submit the current local work, follow `Local Check Order`: run
     and repair safe formatting, whitespace, lint, build, and test issues
     first, wait for local tests to finish, then stage the complete repository
     diff from the repository root with `git add -A`, including modified,
     deleted, and untracked files across monorepo projects. Then run
     `git diff --cached --check`, inspect the staged diff, and commit it with
     a concise message.
   - Do not stage only selected paths. If the dirty worktree contains changes
     that should not be part of the PR, stop before staging and tell the user
     that `create-pr` is configured for repo-wide `git add -A` commits.
   - If the user did not clearly ask to submit the dirty work, stop and explain
     that a PR cannot be created until the branch has reviewable commits.
6. Confirm there is something to review for every target.
   Compare each target branch with `origin/<base>`. If a branch has no diff and
   no unpublished commits, do not open an empty PR for that branch.
7. Make target branches conflict-free when possible.
   - First test each target branch against `origin/<base>` without changing it,
     for example with
     `git merge-tree --write-tree origin/<base> <branch-or-origin/branch>`.
   - Update every target branch non-destructively with the `Base Merge Policy`:
     refresh refs, invoke private `sync`, review its actual result, run affected
     checks and acknowledge its exact tree before pushing. The helper owns the
     normal-hook merge and recorded no-op/fast-forward/two-parent proof.
   - Resolve only straightforward conflicts where both sides are clear and
     preserving current logic is possible.
   - Do not rebase or force-push PR branches in this skill.
   - Never use blanket `ours` or `theirs` conflict resolution. Keep both sides
     when they are additive, preserve the branch behavior when the base only
     moved nearby code, and stop when the conflict needs product or business
     judgment.
   - When multiple branches are requested, also validate the proposed merge
     order through private `validate-order` on the frozen temporary ref. Require
     a clean checkout; the helper records sources, runs the merges, restores
     the exact original checkout and performs exact-SHA cleanup. Never push
     that branch or create an unmanaged throwaway worktree.
   - If a later branch depends on an earlier branch, either merge the earlier
     branch into the later branch with `sync --dependency <earlier-branch>`
     for the dependency frozen at intake, so the ordered path is conflict-free, or
     report that the later PR should be refreshed after the earlier PR lands.
     Choose the non-destructive update only when the dependency is evident from
     current branch history or the user asked for an ordered multi-branch PR
     flow.
8. Validate after conflict resolution.
   Run focused checks based on touched files after any merge, conflict
   resolution, or validation repair. At minimum, scan for conflict markers and
   whitespace errors before pushing. Run formatting/lint checks before tests
   when they can change files, then run the relevant local tests and wait for
   them to finish. If relevant sibling skills apply to the touched surfaces,
   use them after the branch edits and keep the scope limited to the PR
   branches. If validation fails and the failures are plausibly caused by the
   branch, continue repairing the branch before treating PR creation as
   complete. Do not stop at opening a PR link when the branch has fixable local
   test, lint, build, or CI failures. Commit and push the repair, then rerun
   the focused failing checks.
9. Publish each branch.
   In an Agentic SDLC run, write
   `permissions/pr-authorization.json` in the active run directory immediately
   before pushing or opening/reusing the PR. Follow the stricter active-run
   publication contract above; missing or failed UAT cannot authorize an
   Agentic SDLC PR.
   If a branch has no upstream yet, push it with upstream tracking. If conflict
   resolution or base merge work created new commits, push those commits to the
   same branch with an explicit `HEAD:<branch>` refspec.
10. Avoid duplicate PRs.
   Check for an existing open PR for each head branch. If one already exists,
   return that PR instead of creating another.
11. Open each PR with the right readiness state.
   Treat any explicit user-provided PR title as authoritative. Use it verbatim
   unless the user explicitly asks for refinement. Do not substitute a generic
   title such as "Preparation" and do not derive the PR title from a branch
   prefix such as `prep/<topic>`.

    - If the user provides both title and body, use both.
    - If the user provides only a title, use that title and generate or fill the
      body as needed.
    - If the user provides neither, prefer a concise generated title/body or
      `gh pr create --fill` when the commit history is clean enough to support
      it.

   Prefer a draft PR when the branch is intentionally incomplete, validation
   has not run yet, or a non-fixable blocker remains. If GitHub checks fail
   after the PR is opened and the failures are available and branch-caused,
   keep working on the same branch until the failures are resolved or clearly
   blocked by external state.
   When GitHub checks are expected and available, watch or poll them until
   they reach a terminal pass, fail, skip, or cancel state before reporting the
   PR as ready. If checks remain pending because external CI is delayed or
   unavailable, report the PR as pending instead of ready and include the last
   observed check state.
   In an Agentic SDLC run, include requirements covered, feature list,
   validation, tests, evaluation, and UAT evidence summary in the PR body when
   that local evidence exists. If UAT failed or is missing, stop and route
   through the coordinator instead of creating an early Agentic SDLC PR.
   Record the PR URL and readiness summary in the active SDLC run evidence
   when local run state is available; if local state cannot be updated, report
   the missing write explicitly.
   Remove or expire `permissions/pr-authorization.json` after publishing and
   PR creation/reuse completes.
12. Return the result.
   Report:

    - head branch name for each PR
    - base branch name
    - PR number and URL for each PR
    - whether conflicts were found and how they were resolved
    - validation performed
    - recommended manual merge order
    - any blockers that remain

## Command Reference

Close the private root task with `finish completed` after publication and
checks are verified, including zero-commit tasks. Use `finish cancelled` when
abandoning it; preserve unresolved effects. Neither closure nor passing checks
bypasses required human approval.

Read `references/command-reference.md` when exact Git or GitHub CLI commands
are needed for branch detection, validation, base merges, ordered merge
simulation, PR lookup, checks, or PR creation.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Must Not

- In active Agentic SDLC mode, never mutate the clean promoted SHA or publish a
  different local or remote head.
- In active Agentic SDLC mode, route any required repair through
  `sdlc-classify-failure` and the coordinator so all invalidated gates rerun.
- Never keep new work on the default branch once the user asks to open a PR.
- Do not create a second feature branch if the current branch is already a
  feature branch. With no user-named branch and a current non-default branch,
  commit current local work on that branch first, then push it and create or
  reuse the PR for that same branch.
- Reuse an existing open PR for the same branch instead of creating duplicates.
- Do not push directly to the default branch.
- Do not merge the PRs into the default branch unless the user explicitly asks.
  This skill prepares PRs so the user can merge them manually.
- Do not open a PR from uncommitted changes alone. Commit first or stop.
- For local-work PRs, always stage from the repository root with `git add -A`
  so monorepo-wide related changes stay together. Do not path-limit staging; if
  repo-wide staging would include work that should not be in the PR, stop and
  ask the user to split or clean the worktree first.
- Do not open an empty PR with no branch diff against the base branch.
- Do not combine multiple requested branches into one PR.
- Do not rewrite published branch history in this skill. Do not rebase or
  force-push; merge the current base branch instead.
- Do not run private `sync` with uncommitted work. Validate and commit dirty
  work first, then synchronize the frozen base through the helper.
- Do not stage or commit while local tests are still running or pending. Wait
  for terminal pass/fail status, repair branch-caused failures when safe, and
  rerun the failed checks before committing or reporting readiness.
- Do not use plain ambiguous `git push`. Use explicit refspecs such as
  `git push origin HEAD:<branch>` or `git push -u origin HEAD:<branch>`.
- Do not treat a conflict-free current-base PR as enough when the user asked
  for multiple branches. Also check the proposed manual merge order.
- Do not resolve semantic conflicts by guessing. Prefer a small merge commit
  that preserves both branch and base behavior, or stop and report the blocker.
- Do not normalize or reformat generated, vendored, or exact upstream-imported
  files just because a conflict was nearby.
- When the default branch is clean, do not branch from a stale local copy if it
  can be safely fast-forwarded to `origin/<base>` first.
- If local uncommitted work is present on the default branch, create the new
  branch first so those changes move off the default branch safely.
- Do not let a suggested branch slug such as `prep/<topic>` determine the PR
  title when the user supplied a title explicitly.
- Prefer a draft PR over a misleading ready-for-review PR when the work is
  intentionally still in progress.
- Do not present the PR URL as the completed outcome while known branch-caused
  validation or GitHub check failures remain fixable. Repair the branch first,
  update the PR branch, and return the URL together with the now-current
  validation state.
- If a failure cannot be fixed safely in the current turn, leave or convert the
  PR to draft, document the blocker in the PR body, and make the blocker lead
  the final answer instead of treating PR creation as successful.

## Output Contract

When using this skill:

1. Create or reuse the correct working branch or target branches.
2. Make each target branch conflict-free against the base branch when safe.
3. Validate the ordered multi-branch merge path when more than one branch is
   requested.
4. Push each branch if needed.
5. Create or reuse the GitHub PR for each branch.
6. Return the PR numbers, URLs, merge order, validation performed, and any
   conflict-resolution commits.
7. Call out any blockers, such as detached `HEAD`, missing `gh` auth, unknown
   branch names, unresolved conflicts, failing checks, or no diff against the
   base branch.
