# Merge PR

`merge-pr` verifies and merges a GitHub pull request outside the Agentic SDLC
workflow. It is the general-purpose merge primitive used by release publishing
skills after `create-pr` opens or reuses a release-prep PR.

## What It Does

- Resolves a PR from a number, URL, or current branch.
- Waits for PR checks when they are pending.
- Verifies review state, draft state, mergeability, and head SHA.
- Merges with `gh pr merge --match-head-commit`, using an explicit method
  unless the base branch requires a merge queue.
- Refuses admin bypasses and branch-protection overrides.

## Architecture

```text
Pull request
  |
  v
Readiness checks
  |
  v
Head SHA guarded merge
  |
  v
Post-merge verification
```

## Core Concepts

- The user or calling skill must have explicit merge intent.
- `squash` is the default merge method for ordinary protected branches.
- Merge queues use the no-strategy `gh pr merge <pr> --match-head-commit <sha>`
  path after checks and reviews are ready.
- Required reviews, branch protection, and environment rules are blockers, not
  conditions to bypass.
- This skill does not write Agentic SDLC state; `sdlc-merge-pr` owns that path.

## Files

- `SKILL.md`: Merge workflow, guardrails, and output contract.
- `agents/openai.yaml`: UI metadata and default prompt.
