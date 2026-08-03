# Create PR

`create-pr` turns local work or named branches into reviewable GitHub pull
requests. It is designed for branch-safe PR creation without hand-driving Git
and GitHub steps.

## What It Does

- Creates or reuses feature branches.
- Reuses the current non-default branch as the normal path, staging existing
  work only after formatting, whitespace, lint, and test gates complete,
  validating the staged diff, committing it, pushing it, and opening or reusing
  a PR without creating another branch.
- Runs safe pre-test hygiene before local tests, waits for local tests to
  finish, and commits only after selected checks pass or a real blocker is
  reported.
- Merges the latest `origin/<base>` into the PR branch before PR creation so
  the branch is current without rewriting history.
- Keeps PR branches conflict-free against the base branch when possible.
- Repairs safe branch-owned validation, build, lint, test, or GitHub check
  failures before presenting PR creation as handled.
- Waits for available GitHub PR checks to reach a terminal state before
  reporting a PR as ready.
- Opens or reuses GitHub pull requests.
- Preserves explicit user-supplied PR titles and bodies.
- When invoked from Agentic SDLC, checks local UAT evidence and summarizes
  requirements, features, validation, tests, evaluation, and UAT in the PR body.
- In an active Agentic SDLC run, switches to publication-only mode: it requires
  a clean exact promoted SHA with passing UAT and does not stage, commit, merge,
  repair, or otherwise change that SHA. Push and CLI PR creation use one direct
  action with explicit ref and head arguments.
- When Agentic SDLC local state is available, records the PR URL and readiness
  summary in run evidence.
- Reports PR URLs, readiness, and merge order for multi-branch work.

## Architecture

```text
Local git state
  |
  v
Branch selection or current-branch reuse
  |
  v
Commit current feature-branch work or reuse requested commits
  |
  v
Base refresh, merge update, or conflict handling
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
2. If already on a non-default branch and no branch was named, reuse that
   branch. For dirty work, run safe formatting, whitespace, lint, build, and
   test checks first, wait for local tests to finish, then stage with repo-root
   `git add -A`, validate the staged diff, and commit.
3. Select or create the target branch only when needed.
4. Refresh against the base branch, then merge `origin/<base>` into the target
   branch before PR creation. For a `main` base branch, merge `origin/main`.
5. Rerun focused validation after formatting fixes, merge, or conflict
   repair, and repair safe branch-owned failures.
6. Push the branch with an explicit refspec.
7. Open or reuse the PR with the requested title and body.
8. For active Agentic SDLC runs, require passing UAT, reverify the recorded
   remote-default branch and HEAD, and publish only the exact promoted SHA;
   route any branch or default drift back through the coordinator.
9. Keep repairing available branch-caused check failures when safe, or mark a
   real blocker. If GitHub checks are still pending, report the PR as pending
   instead of ready.
10. Return PR details, validation state, and remaining blockers.

## Core Concepts

- Do not leave new work on the default branch.
- Do not create another branch when the current branch is already non-default;
  commit, push, and open or reuse the PR from that branch.
- Resolve the actual `origin` default branch and bind both authorization and PR
  creation to that exact base; do not guess a default branch name.
- Do not guess when GitHub authentication, remotes, or branch ownership are
  unclear.
- Always stage local work from the repository root with `git add -A`; do not
  narrow PR commits to selected paths.
- Run format and whitespace checks before tests when those checks may change
  files, wait for tests to finish, then stage and commit.
- Use `git fetch origin` plus `git merge --no-edit origin/<base>` before PR
  creation so the branch has the latest base updates without rewriting
  history.
- Use explicit push refspecs such as `git push origin HEAD:<branch>`; do not
  use a plain ambiguous `git push`.
- Do not rebase or force-push inside this skill.
- Use draft PRs for incomplete work when that better matches readiness.
- Preserve one PR per branch.
- Treat a known fixable branch-owned failure as unfinished PR creation, not as
  a successful handoff with a link.
- Do not call a PR ready while local tests or GitHub checks are still pending.
- Treat active Agentic SDLC PR creation as publication-only. A different remote
  head, conflict, failed check, or requested repair returns to the coordinator.

## Files

- `SKILL.md`: PR creation workflow, guardrails, and command guidance.
- `agents/openai.yaml`: UI metadata and default prompt.
- `references/command-reference.md`: exact Git and GitHub CLI command cookbook
  loaded when executing or validating PR operations.
