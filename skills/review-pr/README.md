# Review PR

`review-pr` reviews GitHub pull requests and safely moves them closer to
merge-readiness when permissions allow.

## What It Does

- Reviews PRs by number, URL, or current branch.
- Inspects base branch, checks, review state, conflicts, and open concerns.
- Fixes safe issues on writable branches.
- Resolves straightforward conflicts when safe.
- For Agentic SDLC PRs, checks requirements, design, validation, tests,
  evaluation, UAT, and commit evidence when available.
- For active Agentic SDLC PRs, uses findings-and-readiness-only mode and never
  mutates the exact promoted PR head.
- When Agentic SDLC local state is available, records readiness and blocker
  summaries in run evidence.
- Reports whether the PR is ready to merge and what blockers remain.

## Architecture

```text
PR target
  |
  v
Fetch GitHub and git state
  |
  v
Review checks, diffs, conflicts, and comments
  |
  +--> apply safe fixes when writable
  `--> report blockers when not writable or unsafe
```

## Workflow

1. Resolve the PR target and base branch.
2. Inspect working tree state and branch ownership.
3. Review checks, comments, diff, and conflicts.
4. For Agentic SDLC PRs, inspect local SDLC specs and evidence summaries.
5. Apply relevant sibling skills for concrete file types.
6. Fix safe issues and rerun focused validation in generic mode. In active
   Agentic SDLC mode, route every required branch change through the
   coordinator instead.
7. Report readiness, remaining blockers, and merge guidance.

## Core Concepts

- Findings lead; summaries come after issues.
- Do not rewrite externally owned branches without permission.
- Preserve reviewer concerns as explicit blockers until resolved.
- Prefer non-destructive updates for shared branches.
- Preserve the exact promoted SHA in active Agentic SDLC review mode; branch
  fixes, conflict resolution, and check repairs must re-enter the SDLC gates.

## Files

- `SKILL.md`: PR review and repair workflow.
- `agents/openai.yaml`: UI metadata.
- `references/command-reference.md`: exact GitHub CLI and Git command cookbook
  loaded when inspecting, syncing, or updating a PR branch.
