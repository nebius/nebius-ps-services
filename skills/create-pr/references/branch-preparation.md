# Branch preparation

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

An explicitly selected non-default base is preparation-only. Do not silently
retarget it; default completion returns a merge blocker for that base.
