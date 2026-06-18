# Commit

`commit` creates a fast local Git commit on the current branch without pushing.
It is intentionally smaller than `commit-push`, `create-pr`, and
`sdlc-commit`: it only stages, validates, commits, and reports status.

## What It Does

- Resolves the Git repository root and runs Git commands from there.
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

## Files

- `SKILL.md`: Runtime workflow, guardrails, commands, and output contract.
- `agents/openai.yaml`: UI metadata and default prompt.
