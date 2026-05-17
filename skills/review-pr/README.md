# Review PR

`review-pr` reviews GitHub pull requests and safely moves them closer to
merge-readiness when permissions allow.

## What It Does

- Reviews PRs by number, URL, or current branch.
- Inspects base branch, checks, review state, conflicts, and open concerns.
- Fixes safe issues on writable branches.
- Resolves straightforward conflicts when safe.
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
4. Apply relevant sibling skills for concrete file types.
5. Fix safe issues and rerun focused validation.
6. Report readiness, remaining blockers, and merge guidance.

## Core Concepts

- Findings lead; summaries come after issues.
- Do not rewrite externally owned branches without permission.
- Preserve reviewer concerns as explicit blockers until resolved.
- Prefer non-destructive updates for shared branches.

## Files

- `SKILL.md`: PR review and repair workflow.
- `agents/openai.yaml`: UI metadata.
