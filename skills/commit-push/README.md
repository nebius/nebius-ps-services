# Commit Push

`commit-push` commits all current local changes across the whole Git
repository on the active non-default feature branch and pushes that branch to
`origin`. It is intentionally smaller than `create-pr`: it does not create
PRs, change branches, merge, rebase, or repair remote divergence.

## What It Does

- Verifies the current Git state is safe for a branch-local commit and push.
- Stages the complete repository diff with repo-root `git add -A`, regardless
  of the project or subdirectory where the agent started.
- Repairs small mechanical whitespace blockers reported by
  `git diff --cached --check` when the fix is local and unambiguous.
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
Full repository staging
  |
  v
Lightweight staged validation
  |
  v
Bounded whitespace repair when safe
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
6. If the branch is dirty, run `git add -A` from the repository root with no
   pathspec, validate the staged diff, repair simple whitespace-only validation
   blockers when safe, commit with a provided or generated message, and push.
7. Report the final branch status and whether the worktree is clean.

## Core Concepts

- The skill is current-branch only.
- `git add -A` is mandatory and always runs from the Git repository root
  because monorepo changes often span projects.
- The current working directory, service folder, chart folder, or package
  folder never narrows the commit scope for this skill.
- Divergence recovery is intentionally out of scope; it needs a separate
  explicit request.
- The remote branch refresh uses a full `refs/heads/<branch>` source ref so the
  update target is explicit.
- Whitespace repair is intentionally narrow: trailing whitespace and final
  extra blank lines can be fixed, but conflict markers, unresolved conflicts,
  semantic changes, broad formatter churn, and dependency updates still stop the
  workflow.
- Commit and push hooks should run normally.
- Idempotence means safe no-op or push-only behavior, not hidden branch repair.

## Files

- `SKILL.md`: Runtime workflow, guardrails, commands, and output contract.
- `agents/openai.yaml`: UI metadata and default prompt.
