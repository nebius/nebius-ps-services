# Commit

`commit` creates a fast local Git commit on the current branch without pushing.
It is intentionally smaller than `commit-push`, `create-pr`, and
`sdlc-commit`: it only inspects, stages, validates, commits, and reports status.
It may also execute one exact commit delegated by a fresh explicit
`$worktree integrate` after that workflow proves the checkout is eligible.

## What It Does

- Resolves the Git repository root and runs Git commands from there.
- Inspects the complete tracked and untracked diff before staging and stops on
  obvious unsafe or incoherent content.
- Stages the complete repository diff with repo-root `git add -A`.
- Runs lightweight staged validation with `git diff --cached --check`.
- Uses a provided commit message or generates a concise imperative one.
- Creates a local commit with normal hooks enabled.
- Reports the final branch status and whether anything remains dirty.

## Architecture

```text
Current git branch
  |
  v
Fast safety checks
  |
  v
Complete diff inspection
  |
  v
Full repository staging
  |
  v
Lightweight staged validation
  |
  v
Local commit
  |
  v
Final status report
```

## Core Concepts

- The skill is current-branch only.
- `git add -A` is mandatory and always runs from the Git repository root
  because monorepo changes often span projects.
- The current working directory never narrows the commit scope.
- The skill never pushes, opens PRs, repairs branches, or writes Agentic SDLC
  run state.
- Commit hooks should run normally.
- Delegated worktree commits remain local, require the exact preflight branch
  and head, bind the reviewed staged tree to the resulting commit tree, and
  return a clean direct-descendant commit to the integration workflow. A
  durable source-scoped preparation claim blocks competing lifecycle owners
  while the commit runs. Delegation is never permitted for nested/coordinated
  children or active integration attempts.

## Files

- `SKILL.md`: Runtime workflow, guardrails, commands, and output contract.
- `agents/openai.yaml`: UI metadata and default prompt.
