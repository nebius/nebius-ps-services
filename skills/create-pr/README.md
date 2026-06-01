# Create PR

`create-pr` turns local work or named branches into reviewable GitHub pull
requests. It is designed for branch-safe PR creation without hand-driving Git
and GitHub steps.

## What It Does

- Creates or reuses feature branches.
- Keeps PR branches conflict-free against the base branch when possible.
- Repairs safe branch-owned validation, build, lint, test, or GitHub check
  failures before presenting PR creation as handled.
- Opens or reuses GitHub pull requests.
- Preserves explicit user-supplied PR titles and bodies.
- Reports PR URLs, readiness, and merge order for multi-branch work.

## Architecture

```text
Local git state
  |
  v
Branch selection and base refresh
  |
  v
Commit or reuse requested work
  |
  v
Run focused validation and repair safe branch-owned failures
  |
  v
Push branch
  |
  v
Open or reuse GitHub PR
  |
  v
Report PR number, URL, and blockers
```

## Workflow

1. Inspect repository status, remotes, current branch, and base branch.
2. Select or create the target branch.
3. Commit requested local work when appropriate.
4. Refresh against the base branch and handle safe conflicts.
5. Run focused validation and repair safe branch-owned failures.
6. Push the branch.
7. Open or reuse the PR with the requested title and body.
8. Keep repairing available branch-caused check failures when safe, or mark a
   real blocker.
9. Return PR details, validation state, and remaining blockers.

## Core Concepts

- Do not leave new work on the default branch.
- Do not guess when GitHub authentication, remotes, or branch ownership are
  unclear.
- Always stage local work from the repository root with `git add -A`; do not
  narrow PR commits to selected paths.
- Use draft PRs for incomplete work when that better matches readiness.
- Preserve one PR per branch.
- Treat a known fixable branch-owned failure as unfinished PR creation, not as
  a successful handoff with a link.

## Files

- `SKILL.md`: PR creation workflow, guardrails, and command guidance.
- `agents/openai.yaml`: UI metadata and default prompt.
