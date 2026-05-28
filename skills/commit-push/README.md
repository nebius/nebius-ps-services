# Commit Push

`commit-push` commits all current local changes on the active non-default
feature branch and pushes that branch to `origin`. It is intentionally smaller
than `create-pr`: it does not create PRs, change branches, merge, rebase, or
repair remote divergence.

## What It Does

- Verifies the current Git state is safe for a branch-local commit and push.
- Stages the complete monorepo diff with `git add -A`.
- Creates a commit with a user-provided or generated message.
- Pushes the current branch to `origin`.
- Reports whether the final worktree is clean.

## Architecture

```text
Current git branch
  |
  v
Safety checks
  |
  v
Full monorepo staging
  |
  v
Lightweight staged validation
  |
  v
Commit when needed
  |
  v
Push current branch
  |
  v
Final status report
```

## Workflow

1. Detect the repository root, current branch, `origin`, default branch, and
   current worktree status.
2. Stop on unsafe states such as default branch, detached `HEAD`, missing
   `origin`, unknown default branch, unresolved conflicts, in-progress Git
   operations, missing or mismatched `origin/<branch>` upstreams, or remote
   divergence.
3. If the branch is clean and already pushed, report a no-op.
4. If the branch is clean but ahead, push the existing commits.
5. If the branch is clean, has no upstream, and has local work relative to the
   default branch, push it with upstream tracking.
6. If the branch is dirty, run `git add -A`, validate the staged diff, commit
   with a provided or generated message, and push.
7. Report the final branch status and whether the worktree is clean.

## Core Concepts

- The skill is current-branch only.
- `git add -A` is the default because monorepo changes often span projects.
- Divergence recovery is intentionally out of scope; it needs a separate
  explicit request.
- Commit and push hooks should run normally.
- Idempotence means safe no-op or push-only behavior, not hidden branch repair.

## Files

- `SKILL.md`: Runtime workflow, guardrails, commands, and output contract.
- `agents/openai.yaml`: UI metadata and default prompt.
